"""Strict JSON-RPC/MCP adapter for internally selected Houdini tools.

The adapter has no Houdini dependency.  A caller injects either an offline
test transport or the separately bounded, authenticated loopback transport.
"""

from __future__ import annotations

import copy
import json
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, TypeAlias, runtime_checkable

from hia_core.houdini_contract import (
    B2_READ_ONLY_TOOLS,
    ContractError,
    SchemaRegistry,
    validate_schema_instance,
)


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "houdini-intelligence-agent"
SERVER_VERSION = "0.1.0"
RequestId: TypeAlias = int | str
DiagnosticSink: TypeAlias = Callable[[str], None]

_REQUEST_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})
_NOTIFICATION_METHODS = frozenset(
    {"notifications/initialized", "notifications/cancelled"}
)
_MESSAGE_KEYS = frozenset({"jsonrpc", "id", "method", "params"})
FROZEN_TOOL_NAMES = (
    "houdini_scene_info",
    "houdini_node_type_info",
    "houdini_graph_validate",
    "houdini_graph_apply",
    "houdini_graph_verify",
)
FROZEN_TOOL_PERMISSIONS: Mapping[str, str] = MappingProxyType({
    "houdini_scene_info": "scene_read",
    "houdini_node_type_info": "scene_read",
    "houdini_graph_validate": "scene_read",
    "houdini_graph_apply": "scene_write",
    "houdini_graph_verify": "scene_read",
})
B2_READ_ONLY_TOOL_NAMES = B2_READ_ONLY_TOOLS
B2_READ_ONLY_TOOL_PERMISSIONS: Mapping[str, str] = MappingProxyType({
    "houdini_scene_info": "scene_read",
    "houdini_node_type_info": "scene_read",
})
_FROZEN_ANNOTATION_KEYS = frozenset(
    {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
)
_SEMANTIC_ARGUMENT_FIELDS = MappingProxyType({
    "houdini_scene_info": "include_graph_summaries",
    "houdini_node_type_info": "node_types",
})
_NODE_TYPE_INFO_SEMANTIC_DESCRIPTION = (
    "Query one to five allowlisted live Houdini node types. Each node_types item "
    "must be an object containing exactly context and name: Object permits geo, "
    "and Sop permits box, transform, merge, or null."
)
_NODE_TYPE_INFO_SEMANTIC_EXAMPLE = {
    "node_types": [
        {"context": "Object", "name": "geo"},
        {"context": "Sop", "name": "box"},
        {"context": "Sop", "name": "transform"},
        {"context": "Sop", "name": "merge"},
        {"context": "Sop", "name": "null"},
    ]
}
_MAX_IGNORED_METADATA_BYTES = 16_384
_MAX_IGNORED_METADATA_DEPTH = 8
_MAX_CLIENT_INFO_TEXT = 256


def _schema_contains_keyword(value: Any, keyword: str) -> bool:
    """Return whether a projected schema contains ``keyword`` at any depth."""

    if isinstance(value, Mapping):
        return keyword in value or any(
            _schema_contains_keyword(child, keyword) for child in value.values()
        )
    if isinstance(value, list):
        return any(_schema_contains_keyword(child, keyword) for child in value)
    return False


@dataclass(frozen=True)
class BridgeTransportError(Exception):
    """A bounded, explicitly safe error supplied by a Bridge transport."""

    code: str
    message: str
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class CancellationHandoff:
    """Atomically order cancellation against transport-side registration.

    A transport must place its bounded request-registration operation inside
    :meth:`claim_submission`.  Cancellation can then either prevent that
    registration or run after it has completed; there is no check/submit gap.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._cancelled = False
        self._submitted = False

    def claim_submission(self, register: Callable[[], None]) -> bool:
        """Run ``register`` exactly once unless cancellation won the handoff."""

        if not callable(register):
            raise TypeError("register must be callable")
        with self._lock:
            if self._cancelled:
                return False
            if self._submitted:
                raise RuntimeError("submission was already claimed")
            register()
            self._submitted = True
            return True

    def cancel(self) -> bool:
        """Latch cancellation and report whether registration already won."""

        # Publish the request before waiting for an in-progress registration's
        # handoff lock.  A transport can then close a blocked response stream
        # without weakening the atomic submit-versus-cancel ordering below.
        self._cancel_requested.set()
        with self._lock:
            self._cancelled = True
            return self._submitted

    @property
    def cancel_requested(self) -> bool:
        """Return the lock-free cancellation signal for bounded transport I/O."""

        return self._cancel_requested.is_set()

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def submitted(self) -> bool:
        with self._lock:
            return self._submitted


@runtime_checkable
class BridgeTransport(Protocol):
    """Minimal injected boundary between MCP and the authenticated Bridge."""

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: RequestId,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]: ...

    def cancel(self, rpc_request_id: RequestId) -> None: ...


class HoudiniMCPAdapter:
    """Handle the frozen MCP 2025-06-18 request and notification surface."""

    def __init__(
        self,
        transport: BridgeTransport,
        *,
        registry: SchemaRegistry | None = None,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> None:
        self._transport = transport
        self._registry = registry or SchemaRegistry()
        self._diagnostic_sink = diagnostic_sink
        self._state_lock = threading.Lock()
        self._initialize_seen = False
        self._initialized = False
        self._shutdown = False
        self._active_calls: dict[RequestId, CancellationHandoff] = {}
        self._cancelled_calls: OrderedDict[RequestId, None] = OrderedDict()
        tool_names = tuple(self._registry.tool_names)
        if tool_names == FROZEN_TOOL_NAMES:
            expected_permissions = FROZEN_TOOL_PERMISSIONS
            self._server_version = SERVER_VERSION
        elif tool_names == B2_READ_ONLY_TOOL_NAMES:
            expected_permissions = B2_READ_ONLY_TOOL_PERMISSIONS
            self._server_version = "0.2.0"
        else:
            raise ValueError(
                "Schema registry must expose one exact internally supported tool profile"
            )
        permission_level = getattr(self._registry, "permission_level", None)
        if not callable(permission_level):
            raise ValueError("Schema registry must expose frozen tool permissions")
        for name, expected in expected_permissions.items():
            if permission_level(name) != expected:
                raise ValueError("Schema registry tool permissions violate policy")

        descriptors = tuple(self._registry.tool_descriptors())
        if any(not isinstance(item, Mapping) for item in descriptors):
            raise ValueError("Schema descriptors must be JSON objects")
        if tuple(item.get("name") for item in descriptors) != tool_names:
            raise ValueError("Schema descriptors differ from the selected allowlist")
        for descriptor in descriptors:
            name = descriptor["name"]
            annotations = descriptor.get("annotations")
            if not isinstance(annotations, Mapping):
                raise ValueError("Schema descriptor annotations are required")
            if set(annotations) != _FROZEN_ANNOTATION_KEYS:
                raise ValueError("Schema descriptor annotations must use exact keys")
            expected_read_only = expected_permissions[name] == "scene_read"
            if annotations.get("readOnlyHint") is not expected_read_only:
                raise ValueError("Schema descriptor read-only policy is invalid")
            if annotations.get("destructiveHint") is not False:
                raise ValueError("Frozen graph tools must be non-destructive")
            if annotations.get("idempotentHint") is not True:
                raise ValueError("Frozen graph tools must be idempotent")
            if annotations.get("openWorldHint") is not False:
                raise ValueError("Frozen graph tools must remain deny-by-default")

        prepare_arguments = getattr(self._transport, "prepare_arguments", None)
        self._prepare_arguments = prepare_arguments if callable(prepare_arguments) else None
        self._semantic_input_schemas: dict[str, dict[str, Any]] = {}
        if self._prepare_arguments is not None:
            if tool_names != B2_READ_ONLY_TOOL_NAMES:
                raise ValueError(
                    "Argument-preparing transports require the exact Gate B2 profile"
                )
            projected_descriptors = []
            for descriptor in descriptors:
                name = descriptor["name"]
                projected_schema = self._project_semantic_input_schema(name)
                projected = copy.deepcopy(dict(descriptor))
                projected["inputSchema"] = copy.deepcopy(projected_schema)
                get_output_schema = getattr(self._registry, "get_output_schema", None)
                if not callable(get_output_schema):
                    raise ValueError(
                        "Argument-preparing transports require retrievable frozen schemas"
                    )
                projected["outputSchema"] = get_output_schema(name)
                projected_descriptors.append(projected)
                self._semantic_input_schemas[name] = projected_schema
            descriptors = tuple(projected_descriptors)

        self._tool_descriptors = copy.deepcopy(descriptors)
        self._tool_names = frozenset(tool_names)

    def _project_semantic_input_schema(self, name: str) -> dict[str, Any]:
        """Project one full B2 envelope to its model-supplied semantic field."""

        field = _SEMANTIC_ARGUMENT_FIELDS.get(name)
        if field is None:
            raise ValueError("No semantic projection exists for the selected tool")
        get_input_schema = getattr(self._registry, "get_input_schema", None)
        if not callable(get_input_schema):
            raise ValueError(
                "Argument-preparing transports require retrievable frozen schemas"
            )
        full_schema = get_input_schema(name)
        properties = full_schema.get("properties")
        if not isinstance(properties, Mapping) or not isinstance(
            properties.get(field), Mapping
        ):
            raise ValueError("Frozen input schema lacks its semantic field")
        property_schema = copy.deepcopy(dict(properties[field]))
        projected: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": [field],
            "properties": {field: property_schema},
        }
        if name == "houdini_node_type_info":
            items_schema = property_schema.get("items")
            reference = (
                items_schema.get("$ref")
                if isinstance(items_schema, Mapping)
                else None
            )
            if (
                not isinstance(items_schema, Mapping)
                or reference != "#/$defs/nodeType"
                or set(items_schema) != {"$ref"}
            ):
                raise ValueError(
                    "Frozen node-type semantic schema has an unexpected item shape"
                )
            definitions = full_schema.get("$defs")
            if not isinstance(definitions, Mapping) or not isinstance(
                definitions.get("nodeType"), Mapping
            ):
                raise ValueError("Frozen semantic schema reference is unresolved")
            property_schema["items"] = copy.deepcopy(dict(definitions["nodeType"]))
            property_schema["description"] = (
                "One to five selectors; every item contains exactly context and name."
            )
            projected["description"] = _NODE_TYPE_INFO_SEMANTIC_DESCRIPTION
            projected["examples"] = [copy.deepcopy(_NODE_TYPE_INFO_SEMANTIC_EXAMPLE)]
        if _schema_contains_keyword(projected, "$ref") or _schema_contains_keyword(
            projected, "$defs"
        ):
            raise ValueError(
                "Projected semantic schema must not depend on local definitions"
            )
        return projected

    @classmethod
    def b2_read_only(
        cls,
        transport: BridgeTransport,
        *,
        registry: SchemaRegistry | None = None,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> "HoudiniMCPAdapter":
        """Construct the explicit two-tool Gate B2 read-only MCP profile."""

        selected_registry = registry or SchemaRegistry.b2_read_only()
        if tuple(selected_registry.tool_names) != B2_READ_ONLY_TOOL_NAMES:
            raise ValueError("Gate B2 requires the exact read-only schema profile")
        return cls(
            transport,
            registry=selected_registry,
            diagnostic_sink=diagnostic_sink,
        )

    @property
    def tool_names(self) -> frozenset[str]:
        return self._tool_names

    @property
    def initialized(self) -> bool:
        with self._state_lock:
            return self._initialized

    def cancel_request(self, request_id: RequestId) -> None:
        """Record and forward cancellation without exposing transport details."""

        if not self._valid_request_id(request_id):
            return
        with self._state_lock:
            active_handoff = self._active_calls.get(request_id)
            if active_handoff is not None:
                active_handoff.cancel()
            self._cancelled_calls[request_id] = None
            self._cancelled_calls.move_to_end(request_id)
            while len(self._cancelled_calls) > 256:
                self._cancelled_calls.popitem(last=False)
        self._transport.cancel(request_id)

    def shutdown(self) -> None:
        """Idempotently close transport I/O, then cancel active handoffs."""

        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            active = tuple(self._active_calls.items())
        close_transport = getattr(self._transport, "close", None)
        if callable(close_transport):
            try:
                close_transport()
            except Exception:
                self._diagnose("TRANSPORT_CLOSE_FAILED")
        for request_id, handoff in active:
            handoff.cancel()
            try:
                self._transport.cancel(request_id)
            except Exception:
                self._diagnose("SHUTDOWN_CANCELLATION_FAILED")

    def handle_message(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """Handle one already decoded JSON-RPC object.

        Unknown requests receive method-not-found.  Unknown notifications are
        recorded by code only and ignored, because JSON-RPC notifications have
        no response.
        """

        if not isinstance(message, Mapping):
            return self._error(None, -32600, "Invalid Request", "INVALID_REQUEST")
        request_id = message.get("id") if "id" in message else None
        has_id = "id" in message
        response_id = (
            request_id
            if has_id and self._valid_request_id(request_id)
            else None
        )
        if not self._valid_envelope(message, has_id=has_id):
            return (
                self._error(response_id, -32600, "Invalid Request", "INVALID_REQUEST")
                if has_id
                else None
            )

        method = message["method"]
        params = message.get("params")
        if not has_id:
            try:
                return self._handle_notification(method, params)
            except (TypeError, ValueError):
                self._diagnose("INVALID_NOTIFICATION_IGNORED")
                return None
            except Exception:
                self._diagnose("NOTIFICATION_HANDLER_FAILED")
                return None
        if method not in _REQUEST_METHODS:
            self._diagnose("UNKNOWN_REQUEST_REJECTED")
            return self._error(
                request_id,
                -32601,
                "Method not found",
                "METHOD_NOT_ALLOWED",
            )

        try:
            result = self._dispatch_request(request_id, method, params)
        except ContractError as exc:
            rpc_code = -32602 if exc.code in {
                "SCHEMA_INVALID",
                "TOOL_NOT_ALLOWED",
            } else -32603
            return self._error(
                request_id,
                rpc_code,
                exc.message,
                exc.code,
                exc.details,
            )
        except BridgeTransportError as exc:
            return self._error(
                request_id,
                -32000,
                exc.message,
                exc.code,
                exc.details,
            )
        except (TypeError, ValueError):
            self._diagnose("INVALID_PARAMS_REJECTED")
            return self._error(
                request_id,
                -32602,
                "Invalid params",
                "INVALID_PARAMS",
            )
        except Exception:
            self._diagnose("INTERNAL_ERROR")
            return self._error(
                request_id,
                -32603,
                "Internal error",
                "INTERNAL_ERROR",
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _valid_envelope(self, message: Mapping[str, Any], *, has_id: bool) -> bool:
        if set(message) - _MESSAGE_KEYS:
            return False
        if message.get("jsonrpc") != "2.0":
            return False
        if not isinstance(message.get("method"), str) or not message["method"]:
            return False
        if has_id and not self._valid_request_id(message.get("id")):
            return False
        if "params" in message and not isinstance(message["params"], Mapping):
            return False
        return True

    @staticmethod
    def _valid_request_id(value: Any) -> bool:
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, str) and bool(value)
        )

    def _dispatch_request(
        self,
        request_id: RequestId,
        method: str,
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            self._require_empty_params(params)
            return {}
        self._require_initialized()
        if method == "tools/list":
            self._require_tools_list_params(params)
            return {"tools": copy.deepcopy(list(self._tool_descriptors))}
        if method == "tools/call":
            return self._call_tool(request_id, params)
        raise AssertionError("request allowlist and dispatch diverged")

    def _initialize(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise ValueError("initialize params must be an object")
        if (
            not {"protocolVersion", "capabilities", "clientInfo"} <= set(params)
            or set(params) - {"protocolVersion", "capabilities", "clientInfo", "_meta"}
        ):
            raise ValueError("initialize params contain unsupported fields")
        if params.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise BridgeTransportError(
                "UNSUPPORTED_PROTOCOL_VERSION",
                "Only MCP protocol 2025-06-18 is supported",
            )
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        if not isinstance(capabilities, Mapping) or not isinstance(client_info, Mapping):
            raise ValueError("capabilities and clientInfo must be objects")
        self._validate_bounded_ignored_object(capabilities, "capabilities")
        if "_meta" in params:
            self._validate_bounded_ignored_object(params.get("_meta"), "initialize._meta")
        if not {"name", "version"} <= set(client_info) or set(client_info) - {
            "name",
            "version",
            "title",
        }:
            raise ValueError("clientInfo must contain name, version, and optional title")
        for field in ("name", "version"):
            self._validate_client_info_text(client_info.get(field), f"clientInfo.{field}")
        if "title" in client_info:
            self._validate_client_info_text(client_info.get("title"), "clientInfo.title")
        with self._state_lock:
            if self._initialize_seen:
                raise BridgeTransportError(
                    "ALREADY_INITIALIZED",
                    "This MCP session was already initialized",
                )
            self._initialize_seen = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": self._server_version},
        }

    def _handle_notification(
        self,
        method: str,
        params: Mapping[str, Any] | None,
    ) -> None:
        if method not in _NOTIFICATION_METHODS:
            self._diagnose("UNKNOWN_NOTIFICATION_IGNORED")
            return None
        if method == "notifications/initialized":
            self._require_initialized_notification_params(params)
            with self._state_lock:
                if not self._initialize_seen:
                    self._diagnose("INITIALIZED_BEFORE_INITIALIZE_IGNORED")
                    return None
                self._initialized = True
            return None

        if not isinstance(params, Mapping):
            self._diagnose("INVALID_CANCELLATION_IGNORED")
            return None
        if set(params) - {"requestId", "reason", "_meta"}:
            self._diagnose("INVALID_CANCELLATION_IGNORED")
            return None
        if "_meta" in params:
            try:
                self._validate_bounded_ignored_object(
                    params.get("_meta"), "notifications/cancelled._meta"
                )
            except (TypeError, ValueError):
                self._diagnose("INVALID_CANCELLATION_IGNORED")
                return None
        request_id = params.get("requestId")
        if not self._valid_request_id(request_id):
            self._diagnose("INVALID_CANCELLATION_IGNORED")
            return None
        try:
            self.cancel_request(request_id)
        except BridgeTransportError:
            self._diagnose("CANCELLATION_REJECTED")
        except Exception:
            self._diagnose("CANCELLATION_FAILED")
        return None

    def _call_tool(
        self,
        request_id: RequestId,
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if (
            not isinstance(params, Mapping)
            or not {"name", "arguments"} <= set(params)
            or set(params) - {"name", "arguments", "_meta"}
        ):
            raise ValueError(
                "tools/call requires name, arguments, and optional _meta"
            )
        if "_meta" in params:
            self._validate_bounded_ignored_object(params.get("_meta"), "tools/call._meta")
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or name not in self._tool_names:
            raise ContractError(
                "TOOL_NOT_ALLOWED",
                "The requested tool is not in the active frozen allowlist",
                {"allowed_tools": sorted(self._tool_names)},
            )
        if not isinstance(arguments, Mapping):
            raise ValueError("tools/call arguments must be an object")
        if self._prepare_arguments is None:
            supplied_arguments = self._registry.validate_input(name, dict(arguments))
        else:
            semantic_schema = self._semantic_input_schemas[name]
            supplied_arguments = copy.deepcopy(dict(arguments))
            validate_schema_instance(supplied_arguments, semantic_schema)
        cancellation_handoff = CancellationHandoff()
        with self._state_lock:
            if self._shutdown:
                raise BridgeTransportError(
                    "SERVER_SHUTTING_DOWN",
                    "The MCP adapter is shutting down",
                )
            if request_id in self._cancelled_calls:
                self._cancelled_calls.pop(request_id, None)
                raise BridgeTransportError(
                    "CANCELLED",
                    "The MCP tool call was cancelled before Bridge submission",
                )
            if request_id in self._active_calls:
                raise BridgeTransportError(
                    "DUPLICATE_REQUEST_ID",
                    "The JSON-RPC request id is already active",
                )
            self._active_calls[request_id] = cancellation_handoff
        try:
            if cancellation_handoff.cancelled:
                raise BridgeTransportError(
                    "CANCELLED",
                    "The MCP tool call was cancelled before Bridge submission",
                )
            if self._prepare_arguments is None:
                validated = supplied_arguments
            else:
                prepared = self._prepare_arguments(
                    name,
                    supplied_arguments,
                    rpc_request_id=request_id,
                    cancellation_handoff=cancellation_handoff,
                )
                if not isinstance(prepared, Mapping):
                    raise ContractError(
                        "CONTRACT_MISMATCH",
                        "Bridge transport argument preparation must return an object",
                    )
                if cancellation_handoff.cancelled:
                    raise BridgeTransportError(
                        "CANCELLED",
                        "The MCP tool call was cancelled before Bridge submission",
                    )
                validated = self._registry.validate_input(name, dict(prepared))
            raw_result = self._transport.call_tool(
                name,
                validated,
                rpc_request_id=request_id,
                cancellation_handoff=cancellation_handoff,
            )
            if not cancellation_handoff.submitted:
                raise ContractError(
                    "CONTRACT_MISMATCH",
                    "Bridge transport returned without claiming submission",
                )
        finally:
            with self._state_lock:
                self._active_calls.pop(request_id, None)
                self._cancelled_calls.pop(request_id, None)
        if not isinstance(raw_result, Mapping):
            raise ContractError(
                "CONTRACT_MISMATCH",
                "Bridge tool result must be a JSON object",
            )
        result = self._registry.validate_output(name, validated, dict(raw_result))
        text = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": copy.deepcopy(result),
            "isError": result.get("ok") is not True,
        }

    @classmethod
    def _validate_bounded_ignored_object(cls, value: Any, label: str) -> None:
        """Accept but ignore one bounded strict-JSON metadata object."""

        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be an object")
        nodes = 0

        def visit(item: Any, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > 1024 or depth > _MAX_IGNORED_METADATA_DEPTH:
                raise ValueError(f"{label} exceeds its bounded complexity")
            if item is None or isinstance(item, (str, bool, int)):
                return
            if isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError(f"{label} must contain strict JSON values")
                return
            if isinstance(item, Mapping):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError(f"{label} keys must be strings")
                    visit(child, depth + 1)
                return
            if isinstance(item, list):
                for child in item:
                    visit(child, depth + 1)
                return
            raise ValueError(f"{label} must contain strict JSON values")

        visit(value, 0)
        try:
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError(f"{label} must contain strict JSON values") from exc
        if len(encoded) > _MAX_IGNORED_METADATA_BYTES:
            raise ValueError(f"{label} exceeds its byte limit")

    @staticmethod
    def _validate_client_info_text(value: Any, label: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_CLIENT_INFO_TEXT
            or any(ord(character) < 0x20 for character in value)
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        ):
            raise ValueError(f"{label} must be bounded non-control text")

    def _require_initialized(self) -> None:
        with self._state_lock:
            initialized = self._initialized
        if not initialized:
            raise BridgeTransportError(
                "NOT_INITIALIZED",
                "The MCP initialized notification has not been received",
            )

    @staticmethod
    def _require_empty_params(params: Mapping[str, Any] | None) -> None:
        if params is not None and dict(params):
            raise ValueError("params must be absent or an empty object")

    def _require_tools_list_params(
        self, params: Mapping[str, Any] | None
    ) -> None:
        if params is None or not dict(params):
            return
        if set(params) - {"cursor", "_meta"}:
            raise ValueError("tools/list contains unsupported fields")
        if "cursor" in params and params.get("cursor") is not None:
            raise ValueError("tools/list supports only an initial null cursor")
        if "_meta" in params:
            self._validate_bounded_ignored_object(
                params.get("_meta"), "tools/list._meta"
            )

    def _require_initialized_notification_params(
        self, params: Mapping[str, Any] | None
    ) -> None:
        if params is None or not dict(params):
            return
        if set(params) != {"_meta"}:
            raise ValueError(
                "notifications/initialized contains unsupported fields"
            )
        self._validate_bounded_ignored_object(
            params.get("_meta"), "notifications/initialized._meta"
        )

    def _diagnose(self, code: str) -> None:
        sink = self._diagnostic_sink
        if sink is None:
            return
        try:
            sink(code)
        except Exception:
            pass

    @staticmethod
    def _error(
        request_id: Any,
        rpc_code: int,
        message: str,
        stable_code: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"code": stable_code}
        if details:
            data["details"] = dict(details)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": rpc_code, "message": message, "data": data},
        }
