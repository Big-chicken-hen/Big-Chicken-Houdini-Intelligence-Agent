"""Authenticated, loopback-only HTTP transport for the Gate B2C MCP sidecar.

The transport deliberately knows only the two Gate B2 read tools and the
Bridge's bounded scene-request endpoints.  It never accepts a caller-supplied
URL, credential, deadline envelope, HIP identity, or scene revision as
authoritative.  Those values are derived from the current process environment
and the Bridge's authenticated first-read status endpoint.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from hia_core.houdini_contract import B2_READ_ONLY_TOOLS, strict_json_loads

from .adapter import (
    BridgeTransportError,
    CancellationHandoff,
    RequestId,
)


BRIDGE_URL_ENV = "HIA_BRIDGE_URL"
BRIDGE_TOKEN_ENV = "HIA_BRIDGE_TOKEN"
_B2_PROFILE = "b2_read_only"
_B2_SCHEMA_VERSION = "0.2.0"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SAFE_BUILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_RESOLVED_NODE_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,255}$")
_STATUS_FIELDS = frozenset(
    {
        "available",
        "profile",
        "schema_version",
        "schema_digest",
        "launch_id",
        "generation",
        "attestation_digest",
        "houdini_build",
        "hip_session_id",
        "hip_fingerprint",
        "scene_revision",
        "catalog_digest",
        "enabled_tools",
        "allowed_node_types",
    }
)
_ALLOWED_NODE_TYPES = (
    ("Object", "geo", "geo"),
    ("Sop", "box", "box"),
    ("Sop", "transform", "xform"),
    ("Sop", "merge", "merge"),
    ("Sop", "null", "null"),
)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_DEFAULT_CONNECT_TIMEOUT = 0.5
_DEFAULT_READ_TIMEOUT = 1.5
_DEFAULT_TOTAL_TIMEOUT = 60.0
_DEFAULT_STATUS_TIMEOUT = 2.0
_DEFAULT_CANCEL_TIMEOUT = 1.0
_DEFAULT_REQUEST_DEADLINE_MS = 10_000
_DEFAULT_POLL_WAIT_MS = 250
_DEFAULT_MAX_IO_WORKERS = 4


@dataclass(frozen=True)
class _BridgeResponse:
    status: int
    payload: dict[str, Any]


class _IOResources:
    """One bounded request's connection, response stream, and abort latch."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connection: http.client.HTTPConnection | None = None
        self.response: http.client.HTTPResponse | None = None
        self.abort_reason: tuple[str, str] | None = None
        self.connection_closed = False
        self.response_closed = False


class LoopbackBridgeTransport:
    """Submit two read-only MCP tools to one authenticated loopback Bridge."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        manifest_digest: str,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        total_timeout: float = _DEFAULT_TOTAL_TIMEOUT,
        status_timeout: float = _DEFAULT_STATUS_TIMEOUT,
        cancel_timeout: float = _DEFAULT_CANCEL_TIMEOUT,
        request_deadline_ms: int = _DEFAULT_REQUEST_DEADLINE_MS,
        poll_wait_ms: int = _DEFAULT_POLL_WAIT_MS,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_io_workers: int = _DEFAULT_MAX_IO_WORKERS,
        clock: Callable[[], float] = time.monotonic,
        connection_factory: Callable[..., http.client.HTTPConnection] = (
            http.client.HTTPConnection
        ),
    ) -> None:
        host, port = self._validate_origin(base_url)
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_INVALID",
                "The Bridge credential is missing or malformed",
            )
        if not isinstance(manifest_digest, str) or _SHA256.fullmatch(
            manifest_digest
        ) is None:
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_INVALID",
                "The local frozen schema digest is missing or malformed",
            )
        self._connect_timeout = self._positive_timeout(
            connect_timeout, "connect_timeout"
        )
        self._read_timeout = self._positive_timeout(read_timeout, "read_timeout")
        self._total_timeout = self._positive_timeout(total_timeout, "total_timeout")
        self._status_timeout = min(
            self._positive_timeout(status_timeout, "status_timeout"),
            self._total_timeout,
        )
        self._cancel_timeout = min(
            self._positive_timeout(cancel_timeout, "cancel_timeout"),
            self._total_timeout,
        )
        if (
            isinstance(request_deadline_ms, bool)
            or not isinstance(request_deadline_ms, int)
            or not 100 <= request_deadline_ms <= 60_000
        ):
            raise ValueError("request_deadline_ms must be between 100 and 60000")
        if (
            isinstance(poll_wait_ms, bool)
            or not isinstance(poll_wait_ms, int)
            or not 0 <= poll_wait_ms <= 1_000
        ):
            raise ValueError("poll_wait_ms must be between 0 and 1000")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES
        ):
            raise ValueError("max_response_bytes is outside the bounded limit")
        if (
            isinstance(max_io_workers, bool)
            or not isinstance(max_io_workers, int)
            or not 1 <= max_io_workers <= 8
        ):
            raise ValueError("max_io_workers must be between 1 and 8")
        if not callable(clock) or not callable(connection_factory):
            raise TypeError("clock and connection_factory must be callable")

        self._base_url = f"http://{host}:{port}"
        self._host = host
        self._port = port
        self._token = token
        self._manifest_digest = manifest_digest
        self._request_deadline_ms = request_deadline_ms
        self._poll_wait_ms = poll_wait_ms
        self._max_response_bytes = max_response_bytes
        self._clock = clock
        self._connection_factory = connection_factory
        self._io_slots = threading.BoundedSemaphore(max_io_workers)
        self._closer_slots = threading.BoundedSemaphore(max_io_workers * 2)
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._active_io: set[_IOResources] = set()
        self._session_identifier = f"mcp-{secrets.token_hex(16)}"
        self._active_lock = threading.Lock()
        self._active_requests: dict[RequestId, str] = {}

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        manifest_digest: str,
        **kwargs: Any,
    ) -> "LoopbackBridgeTransport":
        """Create the transport from two inherited, non-persisted values."""

        source = os.environ if environment is None else environment
        base_url = source.get(BRIDGE_URL_ENV)
        token = source.get(BRIDGE_TOKEN_ENV)
        if not isinstance(base_url, str) or not isinstance(token, str):
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_MISSING",
                "The Bridge connection environment is incomplete",
            )
        return cls(base_url, token, manifest_digest=manifest_digest, **kwargs)

    @property
    def base_url(self) -> str:
        """Return the validated non-secret loopback origin."""

        return self._base_url

    def close(self) -> None:
        """Idempotently reject new work and interrupt all active HTTP I/O."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            active = tuple(self._active_io)
        with self._active_lock:
            self._active_requests.clear()
        for resources in active:
            self._abort_io(
                resources,
                "TRANSPORT_CLOSED",
                "The Bridge transport is closed",
            )

    def prepare_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: RequestId,
        cancellation_handoff: CancellationHandoff | None = None,
    ) -> Mapping[str, Any]:
        """Build the full 0.2.0 envelope from semantic input and live status."""

        if tool_name not in B2_READ_ONLY_TOOLS:
            raise BridgeTransportError(
                "TOOL_NOT_ALLOWED",
                "The requested tool is outside the Gate B2 read-only profile",
            )
        if not isinstance(arguments, Mapping):
            raise BridgeTransportError(
                "INVALID_PARAMS", "Tool arguments must be a JSON object"
            )
        semantic = self._semantic_arguments(tool_name, arguments)
        status_deadline = self._clock() + self._status_timeout
        response = self._request_json(
            "GET",
            "/v1/scene/status",
            body=None,
            absolute_deadline=status_deadline,
            cancellation_handoff=cancellation_handoff,
        )
        if not 200 <= response.status < 300:
            self._raise_bridge_rejection(response)
        scene = self._validated_scene_status(response.payload)

        correlation = self._correlation_digest(
            tool_name,
            semantic,
            rpc_request_id,
            scene["hip_session_id"],
            scene["scene_revision"],
        )
        prepared: dict[str, Any] = {
            "request_id": f"mcp-{correlation[:32]}",
            "thread_id": self._session_identifier,
            "turn_id": f"rpc-{correlation[32:64]}",
            "hip_session_id": scene["hip_session_id"],
            "base_scene_revision": scene["scene_revision"],
            "idempotency_key": correlation,
            "deadline_ms": self._request_deadline_ms,
            "permission_level": "scene_read",
        }
        prepared.update(copy.deepcopy(semantic))
        return prepared

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: RequestId,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        """Submit, then bounded-poll, one already prepared read request."""

        if tool_name not in B2_READ_ONLY_TOOLS:
            raise BridgeTransportError(
                "TOOL_NOT_ALLOWED",
                "The requested tool is outside the Gate B2 read-only profile",
            )
        request_id = arguments.get("request_id")
        if not isinstance(request_id, str) or _IDENTIFIER.fullmatch(request_id) is None:
            raise BridgeTransportError(
                "INVALID_PARAMS", "The prepared scene request ID is invalid"
            )
        deadline_ms = arguments.get("deadline_ms")
        if (
            isinstance(deadline_ms, bool)
            or not isinstance(deadline_ms, int)
            or not 100 <= deadline_ms <= 60_000
        ):
            raise BridgeTransportError(
                "INVALID_PARAMS", "The prepared scene deadline is invalid"
            )
        absolute_deadline = self._clock() + min(
            self._total_timeout, deadline_ms / 1000.0
        )
        registration: dict[str, _BridgeResponse] = {}

        def register() -> None:
            with self._active_lock:
                if rpc_request_id in self._active_requests:
                    raise BridgeTransportError(
                        "DUPLICATE_REQUEST_ID",
                        "The MCP request ID is already registered",
                    )
                self._active_requests[rpc_request_id] = request_id
            try:
                registration["response"] = self._request_json(
                    "POST",
                    "/v1/scene/requests",
                    body={"tool_name": tool_name, "arguments": dict(arguments)},
                    absolute_deadline=absolute_deadline,
                    cancellation_handoff=cancellation_handoff,
                )
            except Exception as exc:
                # A cancellation after request bytes were sent still needs the
                # active correlation so cancel() can best-effort notify Bridge.
                if not (
                    isinstance(exc, BridgeTransportError)
                    and exc.code == "CANCELLED"
                ):
                    with self._active_lock:
                        self._active_requests.pop(rpc_request_id, None)
                raise

        claimed = cancellation_handoff.claim_submission(register)
        if not claimed:
            raise BridgeTransportError(
                "CANCELLED",
                "The MCP tool call was cancelled before Bridge submission",
            )

        try:
            response = registration.get("response")
            if response is None:
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE",
                    "The Bridge did not acknowledge the scene request",
                )
            result = self._tool_result(response, request_id)
            while result is None:
                self._remaining(absolute_deadline)
                if cancellation_handoff.cancelled:
                    raise BridgeTransportError(
                        "CANCELLED", "The MCP tool call was cancelled"
                    )
                wait_ms = min(
                    self._poll_wait_ms,
                    max(0, int(self._remaining(absolute_deadline) * 1000)),
                )
                response = self._request_json(
                    "GET",
                    f"/v1/scene/requests/{request_id}/result?wait_ms={wait_ms}",
                    body=None,
                    absolute_deadline=absolute_deadline,
                    cancellation_handoff=cancellation_handoff,
                )
                result = self._tool_result(response, request_id)
            return result
        finally:
            with self._active_lock:
                if not cancellation_handoff.cancel_requested:
                    self._active_requests.pop(rpc_request_id, None)

    def cancel(self, rpc_request_id: RequestId) -> None:
        """Best-effort bounded cancellation of one registered Bridge request."""

        with self._active_lock:
            request_id = self._active_requests.get(rpc_request_id)
        if request_id is None:
            return
        try:
            response = self._request_json(
                "POST",
                f"/v1/scene/requests/{request_id}/cancel",
                body={},
                absolute_deadline=self._clock() + self._cancel_timeout,
            )
            if not 200 <= response.status < 300:
                self._raise_bridge_rejection(response)
        finally:
            with self._active_lock:
                if self._active_requests.get(rpc_request_id) == request_id:
                    self._active_requests.pop(rpc_request_id, None)

    @staticmethod
    def _validate_origin(base_url: str) -> tuple[str, int]:
        if not isinstance(base_url, str) or not base_url:
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_INVALID",
                "The Bridge URL is missing or malformed",
            )
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError as exc:
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_INVALID",
                "The Bridge URL is missing or malformed",
            ) from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or not 1 <= port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.netloc != f"127.0.0.1:{port}"
        ):
            raise BridgeTransportError(
                "BRIDGE_CONFIGURATION_INVALID",
                "The Bridge URL must be an ordinary 127.0.0.1 HTTP origin",
            )
        return "127.0.0.1", port

    @staticmethod
    def _positive_timeout(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite positive number")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0 or numeric > 120:
            raise ValueError(f"{name} must be a finite positive number")
        return numeric

    @staticmethod
    def _semantic_arguments(
        tool_name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        if tool_name == "houdini_scene_info":
            field = "include_graph_summaries"
            if field not in arguments:
                raise BridgeTransportError(
                    "INVALID_PARAMS", "The semantic scene-info argument is missing"
                )
            return {field: copy.deepcopy(arguments[field])}
        if tool_name == "houdini_node_type_info":
            field = "node_types"
            if field not in arguments:
                raise BridgeTransportError(
                    "INVALID_PARAMS", "The semantic node-type argument is missing"
                )
            return {field: copy.deepcopy(arguments[field])}
        raise BridgeTransportError(
            "TOOL_NOT_ALLOWED",
            "The requested tool is outside the Gate B2 read-only profile",
        )

    def _correlation_digest(
        self,
        tool_name: str,
        semantic: Mapping[str, Any],
        rpc_request_id: RequestId,
        hip_session_id: str,
        scene_revision: int,
    ) -> str:
        payload = {
            "transport_session": self._session_identifier,
            "rpc_request_id": rpc_request_id,
            "tool_name": tool_name,
            "semantic_arguments": semantic,
            "hip_session_id": hip_session_id,
            "scene_revision": scene_revision,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validated_scene_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if set(payload) != {"ok", "scene"} or payload.get("ok") is not True:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE", "The Bridge status response is invalid"
            )
        scene = payload.get("scene")
        if not isinstance(scene, Mapping):
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE", "The Bridge status response is invalid"
            )
        if set(scene) != _STATUS_FIELDS:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge status response has an unexpected shape",
            )
        hip_session_id = scene.get("hip_session_id")
        revision = scene.get("scene_revision")
        enabled_tools = scene.get("enabled_tools")
        if (
            scene.get("available") is not True
            or scene.get("profile") != _B2_PROFILE
            or scene.get("schema_version") != _B2_SCHEMA_VERSION
            or scene.get("schema_digest") != self._manifest_digest
            or enabled_tools != list(B2_READ_ONLY_TOOLS)
        ):
            raise BridgeTransportError(
                "CAPABILITY_MISMATCH",
                "The Bridge does not expose the exact Gate B2 read-only capability",
            )

        generation = scene.get("generation")
        launch_id = scene.get("launch_id")
        houdini_build = scene.get("houdini_build")
        allowed_node_types = scene.get("allowed_node_types")
        if (
            not isinstance(launch_id, str)
            or _IDENTIFIER.fullmatch(launch_id) is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or not 0 <= generation <= 9_007_199_254_740_991
            or not isinstance(houdini_build, str)
            or _SAFE_BUILD.fullmatch(houdini_build) is None
            or not isinstance(hip_session_id, str)
            or _IDENTIFIER.fullmatch(hip_session_id) is None
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or not 0 <= revision <= 9_007_199_254_740_991
            or any(
                not isinstance(scene.get(field), str)
                or _SHA256.fullmatch(scene[field]) is None
                for field in (
                    "attestation_digest",
                    "hip_fingerprint",
                    "catalog_digest",
                )
            )
            or not self._valid_allowed_node_types(allowed_node_types)
        ):
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge status capability evidence is malformed",
            )
        return {
            "hip_session_id": hip_session_id,
            "scene_revision": revision,
        }

    @staticmethod
    def _valid_allowed_node_types(value: Any) -> bool:
        if not isinstance(value, list) or len(value) != len(_ALLOWED_NODE_TYPES):
            return False
        for item, expected in zip(value, _ALLOWED_NODE_TYPES):
            if not isinstance(item, Mapping) or set(item) != {
                "context",
                "requested_name",
                "resolved_name",
                "available",
            }:
                return False
            if (item.get("context"), item.get("requested_name")) != expected[:2]:
                return False
            resolved = item.get("resolved_name")
            available = item.get("available")
            if available is not True:
                return False
            if (
                resolved != expected[2]
                or not isinstance(resolved, str)
                or _RESOLVED_NODE_TYPE.fullmatch(resolved) is None
            ):
                return False
        return True

    def _tool_result(
        self, response: _BridgeResponse, request_id: str
    ) -> Mapping[str, Any] | None:
        payload = response.payload
        if self._contains_secret(payload):
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge response contained forbidden credential material",
            )
        response_request_id = payload.get("request_id")
        if response_request_id is not None and response_request_id != request_id:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge response request ID did not match",
            )
        if payload.get("terminal") is True:
            result = payload.get("result")
            if isinstance(result, Mapping):
                return copy.deepcopy(dict(result))
            self._raise_bridge_rejection(response)
        if not 200 <= response.status < 300:
            self._raise_bridge_rejection(response)
        if response.status != 202 or payload.get("ok") is not True:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge returned an invalid non-terminal response",
            )
        return None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None,
        absolute_deadline: float,
        cancellation_handoff: CancellationHandoff | None = None,
    ) -> _BridgeResponse:
        self._remaining(absolute_deadline)
        if (
            cancellation_handoff is not None
            and cancellation_handoff.cancel_requested
        ):
            raise BridgeTransportError(
                "CANCELLED", "The MCP tool call was cancelled"
            )
        with self._lifecycle_lock:
            if self._closed:
                raise BridgeTransportError(
                    "TRANSPORT_CLOSED", "The Bridge transport is closed"
                )
        if not self._io_slots.acquire(blocking=False):
            raise BridgeTransportError(
                "BRIDGE_IO_SATURATED",
                "The bounded Bridge I/O worker limit is exhausted",
            )
        resources = _IOResources()
        with self._lifecycle_lock:
            if self._closed:
                self._io_slots.release()
                raise BridgeTransportError(
                    "TRANSPORT_CLOSED", "The Bridge transport is closed"
                )
            self._active_io.add(resources)
        completed = threading.Event()
        state: dict[str, Any] = {}

        def run_io() -> None:
            try:
                state["response"] = self._request_json_io(
                    method,
                    path,
                    body=body,
                    absolute_deadline=absolute_deadline,
                    resources=resources,
                )
            except BaseException as exc:
                state["error"] = exc
            finally:
                with self._lifecycle_lock:
                    self._active_io.discard(resources)
                self._io_slots.release()
                completed.set()

        worker = threading.Thread(
            target=run_io,
            name="hia-mcp-bridge-io",
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            with self._lifecycle_lock:
                self._active_io.discard(resources)
            self._io_slots.release()
            raise BridgeTransportError(
                "BRIDGE_IO_UNAVAILABLE",
                "The bounded Bridge I/O worker could not start",
            ) from exc
        while not completed.is_set():
            aborted_error = self._io_abort_error(resources)
            if aborted_error is not None:
                raise aborted_error
            if (
                cancellation_handoff is not None
                and cancellation_handoff.cancel_requested
            ):
                self._abort_io(
                    resources,
                    "CANCELLED",
                    "The MCP tool call was cancelled",
                )
                raise BridgeTransportError(
                    "CANCELLED", "The MCP tool call was cancelled"
                )
            try:
                wait_seconds = min(0.05, self._remaining(absolute_deadline))
            except BridgeTransportError:
                self._abort_io(
                    resources,
                    "DEADLINE_EXCEEDED",
                    "The Bridge request deadline expired",
                )
                raise
            completed.wait(wait_seconds)
        if self._clock() >= absolute_deadline:
            raise BridgeTransportError(
                "DEADLINE_EXCEEDED", "The Bridge request deadline expired"
            )
        error = state.get("error")
        if error is not None:
            if isinstance(error, Exception):
                raise error
            raise BridgeTransportError(
                "BRIDGE_IO_UNAVAILABLE",
                "The bounded Bridge I/O worker terminated unexpectedly",
            )
        response = state.get("response")
        if not isinstance(response, _BridgeResponse):
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge I/O worker returned no response",
            )
        return response

    def _request_json_io(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None,
        absolute_deadline: float,
        resources: _IOResources,
    ) -> _BridgeResponse:
        remaining = self._remaining(absolute_deadline)
        encoded_body: bytes | None = None
        if body is not None:
            try:
                encoded_body = json.dumps(
                    body,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
                raise BridgeTransportError(
                    "INVALID_PARAMS", "The Bridge request body is not strict JSON"
                ) from exc
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Connection": "close",
        }
        if encoded_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        phase = "connect"
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection = self._connection_factory(
                self._host,
                self._port,
                timeout=min(self._connect_timeout, remaining),
            )
            self._publish_connection(resources, connection)
            self._raise_if_io_aborted(resources)
            connection.connect()
            # A request owns one connection and must never reconnect after a
            # concurrent deadline/cancel/close has closed that connection.
            connection.auto_open = False
            self._raise_if_io_aborted(resources)
            phase = "read"
            self._set_socket_timeout(connection, absolute_deadline)
            connection.request(method, path, body=encoded_body, headers=headers)
            self._raise_if_io_aborted(resources)
            self._set_socket_timeout(connection, absolute_deadline)
            response = connection.getresponse()
            # HTTP/1.0 + Connection: close legitimately detaches the socket
            # from HTTPConnection here.  Publish the still-readable response
            # immediately and never consult connection.sock for body reads.
            self._publish_response(resources, response)
            self._raise_if_io_aborted(resources)
            content_type = response.getheader("Content-Type", "")
            if not content_type.casefold().startswith("application/json"):
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE",
                    "The Bridge response content type is not JSON",
                )
            content_length = self._content_length(response)
            raw = self._read_bounded_body(
                response,
                absolute_deadline,
                content_length,
                resources,
            )
            self._raise_if_io_aborted(resources)
            try:
                payload = strict_json_loads(
                    raw,
                    "Bridge response",
                    max_bytes=self._max_response_bytes,
                )
            except (TypeError, ValueError, RecursionError) as exc:
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE",
                    "The Bridge response is not bounded strict JSON",
                ) from exc
            if not isinstance(payload, dict):
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE",
                    "The Bridge response root is not a JSON object",
                )
            if self._contains_secret(payload):
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE",
                    "The Bridge response contained forbidden credential material",
                )
            return _BridgeResponse(int(response.status), payload)
        except BridgeTransportError as exc:
            aborted_error = self._io_abort_error(resources)
            if aborted_error is not None:
                raise aborted_error from exc
            raise
        except (socket.timeout, TimeoutError) as exc:
            aborted_error = self._io_abort_error(resources)
            if aborted_error is not None:
                raise aborted_error from exc
            if self._clock() >= absolute_deadline:
                raise BridgeTransportError(
                    "DEADLINE_EXCEEDED", "The Bridge request deadline expired"
                ) from exc
            raise BridgeTransportError(
                "BRIDGE_DISCONNECTED",
                "The Bridge did not respond within the bounded timeout",
                {"phase": phase},
            ) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            aborted_error = self._io_abort_error(resources)
            if aborted_error is not None:
                raise aborted_error from exc
            raise BridgeTransportError(
                "BRIDGE_DISCONNECTED",
                "The loopback Bridge connection is unavailable",
                {"phase": phase},
            ) from exc
        finally:
            self._close_io_resources(resources)

    def _publish_connection(
        self,
        resources: _IOResources,
        connection: http.client.HTTPConnection,
    ) -> None:
        with resources.lock:
            resources.connection = connection
            aborted = resources.abort_reason is not None
        if aborted:
            self._schedule_io_close(resources)
            self._raise_if_io_aborted(resources)

    def _publish_response(
        self,
        resources: _IOResources,
        response: http.client.HTTPResponse,
    ) -> None:
        with resources.lock:
            resources.response = response
            aborted = resources.abort_reason is not None
        if aborted:
            self._schedule_io_close(resources)
            self._raise_if_io_aborted(resources)

    def _abort_io(
        self,
        resources: _IOResources,
        code: str,
        message: str,
    ) -> None:
        with resources.lock:
            if resources.abort_reason is None:
                resources.abort_reason = (code, message)
        self._schedule_io_close(resources)

    @staticmethod
    def _io_abort_error(resources: _IOResources) -> BridgeTransportError | None:
        with resources.lock:
            reason = resources.abort_reason
        if reason is None:
            return None
        return BridgeTransportError(reason[0], reason[1])

    def _raise_if_io_aborted(self, resources: _IOResources) -> None:
        error = self._io_abort_error(resources)
        if error is not None:
            raise error

    @staticmethod
    def _take_io_resources(
        resources: _IOResources,
    ) -> tuple[http.client.HTTPResponse | None, http.client.HTTPConnection | None]:
        with resources.lock:
            response = None
            connection = None
            if resources.response is not None and not resources.response_closed:
                resources.response_closed = True
                response = resources.response
            if (
                resources.connection is not None
                and not resources.connection_closed
            ):
                resources.connection_closed = True
                connection = resources.connection
        return response, connection

    @staticmethod
    def _close_claimed_io(
        response: http.client.HTTPResponse | None,
        connection: http.client.HTTPConnection | None,
    ) -> None:
        # HTTPResponse owns the readable stream after an HTTP/1.0 close.
        # Close it first, outside the resource lock, then close the connection.
        if response is not None:
            LoopbackBridgeTransport._close_response(response)
        if connection is not None:
            LoopbackBridgeTransport._close_connection(connection)

    def _close_io_resources(self, resources: _IOResources) -> None:
        response, connection = self._take_io_resources(resources)
        self._close_claimed_io(response, connection)

    def _schedule_io_close(self, resources: _IOResources) -> None:
        """Close an aborted stream without blocking its deadline caller."""

        if not self._closer_slots.acquire(blocking=False):
            return
        response, connection = self._take_io_resources(resources)
        if response is None and connection is None:
            self._closer_slots.release()
            return

        def close_claimed() -> None:
            try:
                self._close_claimed_io(response, connection)
            finally:
                self._closer_slots.release()

        closer = threading.Thread(
            target=close_claimed,
            name="hia-mcp-bridge-close",
            daemon=True,
        )
        try:
            closer.start()
        except Exception:
            self._closer_slots.release()

    @staticmethod
    def _close_response(response: http.client.HTTPResponse) -> None:
        try:
            response.close()
        except Exception:
            pass

    @staticmethod
    def _close_connection(connection: http.client.HTTPConnection) -> None:
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        try:
            connection.close()
        except Exception:
            pass

    def _set_socket_timeout(
        self,
        connection: http.client.HTTPConnection,
        absolute_deadline: float,
    ) -> None:
        remaining = self._remaining(absolute_deadline)
        sock = connection.sock
        if sock is None:
            raise BridgeTransportError(
                "BRIDGE_DISCONNECTED", "The loopback Bridge socket is unavailable"
            )
        sock.settimeout(min(self._read_timeout, remaining))

    def _read_bounded_body(
        self,
        response: http.client.HTTPResponse,
        absolute_deadline: float,
        content_length: int | None,
        resources: _IOResources,
    ) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while content_length is None or received < content_length:
            self._remaining(absolute_deadline)
            self._raise_if_io_aborted(resources)
            remaining_capacity = self._max_response_bytes - received
            if remaining_capacity <= 0:
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE", "The Bridge response is too large"
                )
            requested = min(_READ_CHUNK_BYTES, remaining_capacity + 1)
            if content_length is not None:
                requested = min(requested, content_length - received)
            chunk = response.read(requested)
            self._raise_if_io_aborted(resources)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > self._max_response_bytes:
                raise BridgeTransportError(
                    "INVALID_BRIDGE_RESPONSE", "The Bridge response is too large"
                )
        if content_length is not None and received != content_length:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE", "The Bridge response body was truncated"
            )
        return b"".join(chunks)

    def _content_length(self, response: http.client.HTTPResponse) -> int | None:
        raw = response.getheader("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE",
                "The Bridge response Content-Length is invalid",
            ) from exc
        if value < 0 or value > self._max_response_bytes:
            raise BridgeTransportError(
                "INVALID_BRIDGE_RESPONSE", "The Bridge response is too large"
            )
        return value

    def _raise_bridge_rejection(self, response: _BridgeResponse) -> None:
        error = response.payload.get("structured_error")
        remote_code = error.get("code") if isinstance(error, Mapping) else None
        code = (
            remote_code
            if isinstance(remote_code, str)
            and _SAFE_ERROR_CODE.fullmatch(remote_code) is not None
            else "BRIDGE_REQUEST_REJECTED"
        )
        raise BridgeTransportError(
            code,
            "The Bridge rejected the bounded scene request",
            {"http_status": response.status},
        )

    def _remaining(self, absolute_deadline: float) -> float:
        remaining = absolute_deadline - self._clock()
        if remaining <= 0:
            raise BridgeTransportError(
                "DEADLINE_EXCEEDED", "The Bridge request deadline expired"
            )
        return remaining

    def _contains_secret(self, value: Any) -> bool:
        if isinstance(value, str):
            return self._token in value
        if isinstance(value, Mapping):
            return any(
                self._contains_secret(key) or self._contains_secret(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(self._contains_secret(item) for item in value)
        return False


__all__ = [
    "BRIDGE_TOKEN_ENV",
    "BRIDGE_URL_ENV",
    "LoopbackBridgeTransport",
]
