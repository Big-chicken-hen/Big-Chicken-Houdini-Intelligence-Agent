"""Offline JSON-RPC/MCP adapter for the five frozen Houdini graph tools.

This module contains no network client, no Houdini integration, and no live
dispatcher.  A caller must inject a deterministic :class:`BridgeTransport`.
"""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, TypeAlias, runtime_checkable

from hia_core.houdini_contract import ContractError, SchemaRegistry


MCP_PROTOCOL_VERSION = "2024-11-05"
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
_FROZEN_ANNOTATION_KEYS = frozenset(
    {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
)


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

        with self._lock:
            self._cancelled = True
            return self._submitted

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
    """Handle the frozen MCP 2024-11-05 request and notification surface."""

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
        self._active_calls: dict[RequestId, CancellationHandoff] = {}
        self._cancelled_calls: OrderedDict[RequestId, None] = OrderedDict()
        tool_names = tuple(self._registry.tool_names)
        if tool_names != FROZEN_TOOL_NAMES:
            raise ValueError(
                "Schema registry must expose the exact ordered five-tool allowlist"
            )
        permission_level = getattr(self._registry, "permission_level", None)
        if not callable(permission_level):
            raise ValueError("Schema registry must expose frozen tool permissions")
        for name, expected in FROZEN_TOOL_PERMISSIONS.items():
            if permission_level(name) != expected:
                raise ValueError("Schema registry tool permissions violate policy")

        descriptors = tuple(self._registry.tool_descriptors())
        if any(not isinstance(item, Mapping) for item in descriptors):
            raise ValueError("Schema descriptors must be JSON objects")
        if tuple(item.get("name") for item in descriptors) != FROZEN_TOOL_NAMES:
            raise ValueError("Schema descriptors differ from the frozen allowlist")
        for descriptor in descriptors:
            name = descriptor["name"]
            annotations = descriptor.get("annotations")
            if not isinstance(annotations, Mapping):
                raise ValueError("Schema descriptor annotations are required")
            if set(annotations) != _FROZEN_ANNOTATION_KEYS:
                raise ValueError("Schema descriptor annotations must use exact keys")
            expected_read_only = name != "houdini_graph_apply"
            if annotations.get("readOnlyHint") is not expected_read_only:
                raise ValueError("Schema descriptor read-only policy is invalid")
            if annotations.get("destructiveHint") is not False:
                raise ValueError("Frozen graph tools must be non-destructive")
            if annotations.get("idempotentHint") is not True:
                raise ValueError("Frozen graph tools must be idempotent")
            if annotations.get("openWorldHint") is not False:
                raise ValueError("Frozen graph tools must remain deny-by-default")

        self._tool_descriptors = copy.deepcopy(descriptors)
        self._tool_names = frozenset(tool_names)

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
        if not self._valid_envelope(message, has_id=has_id):
            return (
                self._error(request_id, -32600, "Invalid Request", "INVALID_REQUEST")
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
            self._require_empty_params(params)
            return {"tools": copy.deepcopy(list(self._tool_descriptors))}
        if method == "tools/call":
            return self._call_tool(request_id, params)
        raise AssertionError("request allowlist and dispatch diverged")

    def _initialize(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise ValueError("initialize params must be an object")
        if set(params) != {"protocolVersion", "capabilities", "clientInfo"}:
            raise ValueError("initialize params must contain only frozen fields")
        if params.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise BridgeTransportError(
                "UNSUPPORTED_PROTOCOL_VERSION",
                "Only MCP protocol 2024-11-05 is supported",
            )
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        if not isinstance(capabilities, Mapping) or not isinstance(client_info, Mapping):
            raise ValueError("capabilities and clientInfo must be objects")
        if dict(capabilities):
            raise ValueError("B1 initialize capabilities must be an empty object")
        if set(client_info) != {"name", "version"}:
            raise ValueError("clientInfo must contain exactly name and version")
        if not isinstance(client_info.get("name"), str) or not client_info["name"]:
            raise ValueError("clientInfo.name must be a non-empty string")
        if not isinstance(client_info.get("version"), str) or not client_info["version"]:
            raise ValueError("clientInfo.version must be a non-empty string")
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
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
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
            self._require_empty_params(params)
            with self._state_lock:
                if not self._initialize_seen:
                    self._diagnose("INITIALIZED_BEFORE_INITIALIZE_IGNORED")
                    return None
                self._initialized = True
            return None

        if not isinstance(params, Mapping):
            self._diagnose("INVALID_CANCELLATION_IGNORED")
            return None
        if set(params) - {"requestId", "reason"}:
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
        if not isinstance(params, Mapping) or set(params) != {"name", "arguments"}:
            raise ValueError("tools/call requires exactly name and arguments")
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or name not in self._tool_names:
            raise ContractError(
                "TOOL_NOT_ALLOWED",
                "The requested tool is not in the frozen five-tool allowlist",
                {"allowed_tools": sorted(self._tool_names)},
            )
        if not isinstance(arguments, Mapping):
            raise ValueError("tools/call arguments must be an object")
        validated = self._registry.validate_input(name, dict(arguments))
        cancellation_handoff = CancellationHandoff()
        with self._state_lock:
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
            "isError": result.get("ok") is not True,
        }

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
