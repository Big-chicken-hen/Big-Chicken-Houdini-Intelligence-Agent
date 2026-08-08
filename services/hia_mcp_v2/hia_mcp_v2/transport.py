"""Independent authenticated loopback transport for HIA MCP V2."""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import TransportError


SERVER_ID = "hia_mcp_v2"
WIRE_PROTOCOL = "hia-mcp-v2/1"
LOOPBACK_HOST = "127.0.0.1"
EXECUTE_ROUTE = "/hia-mcp-v2/v1/execute"
HEALTH_ROUTE = "/hia-mcp-v2/v1/health"
RUNTIME_IDENTITY_VERSION = 1
RUNTIME_DIRECTORY = ".runtime/hia-mcp-v2"
ENV_PREFIX = "HIA_MCP_V2_"
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
DEFAULT_TIMEOUT_SECONDS = 60.0
_SCENE_WRITE_TOOLS = frozenset({"hia_execute_hom"})


class CancellationToken:
    """Cooperative cancellation plus proven runtime-response acceptance."""

    def __init__(self, *, stdio_queue_seconds: float = 0.0) -> None:
        self._event = threading.Event()
        self._accepted = threading.Event()
        try:
            queue_seconds = float(stdio_queue_seconds)
        except (TypeError, ValueError):
            queue_seconds = 0.0
        self._stdio_queue_seconds = (
            queue_seconds if math.isfinite(queue_seconds) and queue_seconds >= 0 else 0.0
        )

    def cancel(self) -> None:
        self._event.set()

    def mark_accepted(self) -> None:
        self._accepted.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def accepted(self) -> bool:
        return self._accepted.is_set()

    @property
    def stdio_queue_seconds(self) -> float:
        return self._stdio_queue_seconds


@runtime_checkable
class HoudiniTransport(Protocol):
    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: int | str,
        cancellation: CancellationToken,
    ) -> Mapping[str, Any]: ...

    def cancel(self, request_id: int | str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class TransportConfig:
    host: str
    port: int
    token: str
    launcher_session_id: str
    executor_module_path: str
    route: str = EXECUTE_ROUTE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ValueError("HIA MCP V2 transport may connect only to 127.0.0.1")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65_535:
            raise ValueError("HIA MCP V2 port must be between 1 and 65535")
        if not _valid_token(self.token):
            raise ValueError("HIA MCP V2 token is missing or invalid")
        if not _valid_launcher_session_id(self.launcher_session_id):
            raise ValueError("HIA launcher session ID is missing or invalid")
        executor_path = Path(self.executor_module_path)
        if not executor_path.is_absolute() or "\x00" in self.executor_module_path:
            raise ValueError("Expected HIA executor module path must be absolute")
        object.__setattr__(
            self,
            "executor_module_path",
            str(executor_path.resolve()),
        )
        if self.route != EXECUTE_ROUTE:
            raise ValueError("HIA MCP V2 route must use its independent fixed namespace")
        if not 0.1 <= float(self.timeout_seconds) <= 300:
            raise ValueError("HIA MCP V2 timeout must be between 0.1 and 300 seconds")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "TransportConfig":
        env = environment if environment is not None else os.environ
        host = env.get("HIA_MCP_V2_HOST", LOOPBACK_HOST)
        port_text = env.get("HIA_MCP_V2_PORT", "")
        token = env.get("HIA_MCP_V2_TOKEN", "")
        launcher_session_id = env.get("HIA_LAUNCHER_SESSION_ID", "")
        executor_module_path = env.get("HIA_MCP_V2_EXECUTOR_PATH", "")
        route = env.get("HIA_MCP_V2_ROUTE", EXECUTE_ROUTE)
        timeout_text = env.get("HIA_MCP_V2_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            port = int(port_text)
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ValueError("HIA MCP V2 environment contains an invalid number") from exc
        return cls(
            host=host,
            port=port,
            token=token,
            launcher_session_id=launcher_session_id,
            executor_module_path=executor_module_path,
            route=route,
            timeout_seconds=timeout,
        )


class LoopbackTransport:
    """POST one bounded batch request to the live Houdini runtime."""

    def __init__(self, config: TransportConfig) -> None:
        self.config = config
        self._active_lock = threading.Lock()
        self._active: dict[int | str, CancellationToken] = {}
        self._identity_lock = threading.Lock()
        self._latched_identity: dict[str, Any] | None = None
        self._closed = False

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "LoopbackTransport":
        return cls(TransportConfig.from_environment(environment))

    def health(self, *, timeout_seconds: float = 2.0) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"http://{LOOPBACK_HOST}:{self.config.port}{HEALTH_ROUTE}",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(0.1, min(float(timeout_seconds), 10.0)),
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            error = _decode_error(exc.read(MAX_RESPONSE_BYTES + 1))
            raise TransportError(
                str(error.get("code", "HOUDINI_UNAVAILABLE")),
                str(
                    error.get(
                        "message",
                        "The live HIA MCP V2 Houdini runtime rejected identity verification",
                    )
                ),
                {
                    **(
                        error.get("details")
                        if isinstance(error.get("details"), dict)
                        else {}
                    ),
                    "http_status": exc.code,
                },
            ) from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            raise TransportError(
                "HOUDINI_UNAVAILABLE",
                "The live HIA MCP V2 Houdini runtime identity is unavailable",
                {"stage": "identity_preflight", "request_submitted": False},
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError(
                "STALE_HOUDINI_RUNTIME",
                "The Houdini runtime identity response is too large; restart the launcher",
                {"restart_required": True, "request_submitted": False},
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError(
                "STALE_HOUDINI_RUNTIME",
                "The Houdini runtime did not return the current identity contract; restart the launcher",
                {"restart_required": True, "request_submitted": False},
            ) from exc
        identity = _health_identity(payload)
        return self._accept_identity(identity)

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: int | str,
        cancellation: CancellationToken,
    ) -> Mapping[str, Any]:
        call_started = time.monotonic()
        queue_seconds = cancellation.stdio_queue_seconds
        wait_budget = self._wait_budget(tool_name, arguments)
        if cancellation.cancelled:
            raise TransportError(
                "CANCELLED_BEFORE_EXECUTION",
                "The call was cancelled before Houdini execution began",
                _before_submission_details(
                    stage="before_runtime_submission",
                    queue_seconds=queue_seconds,
                ),
            )
        with self._active_lock:
            if self._closed:
                raise TransportError("TRANSPORT_CLOSED", "The HIA MCP V2 transport is closed")
            self._active[request_id] = cancellation
        try:
            if tool_name == "hia_execute_hom" and queue_seconds >= wait_budget:
                raise TransportError(
                    "TIMEOUT_BEFORE_EXECUTION",
                    "The hia_execute_hom wait budget expired in the stdio queue before Houdini execution began",
                    _before_submission_details(
                        stage="stdio_queue",
                        queue_seconds=queue_seconds,
                        timeout_seconds=wait_budget,
                    ),
                )
            serialized_arguments = json.dumps(
                dict(arguments),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(serialized_arguments) > MAX_REQUEST_BYTES:
                raise TransportError(
                    "REQUEST_TOO_LARGE",
                    "The HIA MCP V2 request exceeds the byte limit",
                    {"limit_bytes": MAX_REQUEST_BYTES},
                )
            identity_started = time.monotonic()
            scene_write = tool_name in _SCENE_WRITE_TOOLS
            try:
                identity = self._runtime_identity()
            except TransportError as exc:
                raw_identity = exc.details.get("runtime_identity")
                if (
                    scene_write
                    or exc.code
                    not in {
                        "HOUDINI_SESSION_CHANGED",
                        "HOUDINI_RUNTIME_SOURCE_CHANGED",
                        "STALE_HOUDINI_RUNTIME",
                    }
                    or not isinstance(raw_identity, Mapping)
                ):
                    raise
                identity = _validated_identity(
                    raw_identity,
                    self.config,
                    enforce_expected=False,
                )
            identity_seconds = max(0.0, time.monotonic() - identity_started)
            serialization_started = time.monotonic()
            body = json.dumps(
                {
                    "protocol": WIRE_PROTOCOL,
                    "id": request_id,
                    "tool": tool_name,
                    "arguments": dict(arguments),
                    "expected_runtime": _runtime_binding(identity),
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request_serialization_seconds = max(
                0.0,
                time.monotonic() - serialization_started,
            )
            if len(body) > MAX_REQUEST_BYTES:
                raise TransportError(
                    "REQUEST_TOO_LARGE",
                    "The HIA MCP V2 request exceeds the byte limit",
                    {"limit_bytes": MAX_REQUEST_BYTES},
                )
            if cancellation.cancelled:
                raise TransportError(
                    "CANCELLED_BEFORE_EXECUTION",
                    "The call was cancelled before Houdini execution began",
                    _before_submission_details(
                        stage="request_serialization",
                        queue_seconds=queue_seconds,
                    ),
                )
            remaining_timeout = wait_budget
            if tool_name == "hia_execute_hom":
                remaining_timeout -= (
                    queue_seconds
                    + identity_seconds
                    + request_serialization_seconds
                )
                if remaining_timeout <= 0:
                    raise TransportError(
                        "TIMEOUT_BEFORE_EXECUTION",
                        "The hia_execute_hom wait budget expired before the request was submitted to Houdini",
                        _before_submission_details(
                            stage="request_serialization",
                            queue_seconds=queue_seconds,
                            timeout_seconds=wait_budget,
                            request_serialization_seconds=request_serialization_seconds,
                        ),
                    )
            request = urllib.request.Request(
                f"http://{LOOPBACK_HOST}:{self.config.port}{self.config.route}",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.config.token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=remaining_timeout) as response:
                    cancellation.mark_accepted()
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                cancellation.mark_accepted()
                raw_error = exc.read(MAX_RESPONSE_BYTES + 1)
                parsed = _decode_error(raw_error)
                code = parsed.get("code", "HTTP_ERROR")
                message = parsed.get("message", f"Houdini runtime returned HTTP {exc.code}")
                details = (
                    dict(parsed["details"])
                    if isinstance(parsed.get("details"), Mapping)
                    else {}
                )
                details["http_status"] = exc.code
                raise TransportError(str(code), str(message), details) from exc
            except (TimeoutError, socket.timeout) as exc:
                raise _runtime_timeout(
                    wait_budget,
                    queue_seconds,
                    request_serialization_seconds,
                    accepted=cancellation.accepted,
                ) from exc
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    raise _runtime_timeout(
                        wait_budget,
                        queue_seconds,
                        request_serialization_seconds,
                        accepted=cancellation.accepted,
                    ) from exc
                raise TransportError(
                    "HOUDINI_UNAVAILABLE",
                    "The live HIA MCP V2 Houdini runtime is unavailable",
                ) from exc
            if len(raw) > MAX_RESPONSE_BYTES:
                raise TransportError(
                    "RESPONSE_TOO_LARGE",
                    "The Houdini runtime response exceeds the byte limit",
                    {"limit_bytes": MAX_RESPONSE_BYTES},
                )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError("INVALID_RESPONSE", "The Houdini runtime returned invalid JSON") from exc
            if not isinstance(payload, dict) or payload.get("protocol") != WIRE_PROTOCOL:
                raise TransportError("INVALID_RESPONSE", "The Houdini runtime response has the wrong protocol")
            if payload.get("ok") is not True:
                error = payload.get("error")
                if not isinstance(error, dict):
                    raise TransportError("INVALID_RESPONSE", "The Houdini runtime error is malformed")
                raise TransportError(
                    str(error.get("code", "HOUDINI_ERROR")),
                    str(error.get("message", "Houdini execution failed")),
                    error.get("details") if isinstance(error.get("details"), dict) else None,
                )
            result = payload.get("result")
            if not isinstance(result, dict):
                raise TransportError("INVALID_RESPONSE", "The Houdini runtime result must be an object")
            response_identity = result.get("runtime_identity")
            if not isinstance(response_identity, Mapping):
                raise TransportError(
                    "STALE_HOUDINI_RUNTIME",
                    "The Houdini runtime response lacks the current identity contract; restart the launcher",
                    {
                        "restart_required": True,
                        "request_submitted": True,
                    },
                )
            if scene_write:
                self._accept_identity(response_identity, request_submitted=True)
            else:
                observed_identity = _validated_identity(
                    response_identity,
                    self.config,
                    request_submitted=True,
                    enforce_expected=False,
                )
                warning = self._identity_warning(observed_identity)
                if warning is None:
                    self._accept_identity(
                        observed_identity,
                        request_submitted=True,
                    )
                else:
                    result = dict(result)
                    result["runtime_identity"] = observed_identity
                    result["restart_required"] = True
                    result["identity_warning"] = warning
            if tool_name == "hia_execute_hom":
                allowed = (
                    "ok",
                    "result",
                    "stdout",
                    "warnings",
                    "errors",
                    "revision",
                    "dirty",
                    "elapsed_seconds",
                    "script_sha256",
                    "scene_change_status",
                )
                result = {name: result.get(name) for name in allowed}
            return result
        finally:
            with self._active_lock:
                self._active.pop(request_id, None)

    def _wait_budget(self, tool_name: str, arguments: Mapping[str, Any]) -> float:
        if tool_name != "hia_execute_hom" or "timeout_seconds" not in arguments:
            return float(self.config.timeout_seconds)
        value = arguments.get("timeout_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return float(self.config.timeout_seconds)
        timeout = float(value)
        return timeout if math.isfinite(timeout) and timeout > 0 else float(self.config.timeout_seconds)

    def cancel(self, request_id: int | str) -> None:
        with self._active_lock:
            token = self._active.get(request_id)
        if token is not None:
            token.cancel()

    def close(self) -> None:
        with self._active_lock:
            self._closed = True
            active = list(self._active.values())
        for token in active:
            token.cancel()

    def _runtime_identity(self) -> dict[str, Any]:
        with self._identity_lock:
            identity = (
                dict(self._latched_identity)
                if self._latched_identity is not None
                else None
            )
        if identity is not None:
            return identity
        return dict(self.health())

    def _accept_identity(
        self,
        value: Mapping[str, Any],
        *,
        request_submitted: bool = False,
    ) -> dict[str, Any]:
        identity = _validated_identity(
            value,
            self.config,
            request_submitted=request_submitted,
        )
        with self._identity_lock:
            previous = self._latched_identity
            if previous is not None and _runtime_binding(previous) != _runtime_binding(
                identity
            ):
                raise TransportError(
                    "HOUDINI_SESSION_CHANGED",
                    "The live Houdini process or executor module changed; reconnect before continuing",
                    {
                        "expected": _runtime_binding(previous),
                        "actual": _runtime_binding(identity),
                        "runtime_identity": identity,
                        "restart_required": True,
                        "request_submitted": request_submitted,
                    },
                )
            self._latched_identity = dict(identity)
        return dict(identity)

    def _identity_warning(
        self,
        identity: Mapping[str, Any],
    ) -> dict[str, str] | None:
        if identity.get("executor_source_status") != "current":
            code = "STALE_HOUDINI_RUNTIME"
        elif identity.get("launcher_session_id") != self.config.launcher_session_id:
            code = "HOUDINI_SESSION_CHANGED"
        elif os.path.normcase(str(identity.get("executor_module_path"))) != os.path.normcase(
            self.config.executor_module_path
        ):
            code = "HOUDINI_RUNTIME_SOURCE_CHANGED"
        else:
            with self._identity_lock:
                previous = self._latched_identity
                changed = (
                    previous is not None
                    and _runtime_binding(previous)
                    != _runtime_binding(identity)
                )
            code = "HOUDINI_SESSION_CHANGED" if changed else ""
        if not code:
            return None
        return {
            "code": code,
            "message": (
                "The read completed against the observed Houdini runtime; "
                "reconnect before any scene write"
            ),
        }


def _valid_token(value: str) -> bool:
    return (
        isinstance(value, str)
        and 32 <= len(value) <= 512
        and "\r" not in value
        and "\n" not in value
        and all(32 < ord(character) < 127 for character in value)
    )


def _valid_launcher_session_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _health_identity(
    payload: Any,
) -> Mapping[str, Any]:
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("protocol") != WIRE_PROTOCOL
        or payload.get("ok") is not True
        or not isinstance(result, Mapping)
        or result.get("server_id") != SERVER_ID
        or not isinstance(result.get("runtime_identity"), Mapping)
    ):
        raise TransportError(
            "STALE_HOUDINI_RUNTIME",
            "The running Houdini module predates the current identity contract; restart the launcher",
            {"restart_required": True, "request_submitted": False},
        )
    return result["runtime_identity"]


def _validated_identity(
    value: Mapping[str, Any],
    config: TransportConfig,
    *,
    request_submitted: bool = False,
    enforce_expected: bool = True,
) -> dict[str, Any]:
    expected_fields = {
        "identity_version",
        "launcher_session_id",
        "houdini_pid",
        "hip_path",
        "hip_state",
        "scene_revision",
        "executor_module_path",
        "executor_loaded_mtime_ns",
        "executor_disk_mtime_ns",
        "executor_source_status",
    }
    if set(value) != expected_fields:
        raise TransportError(
            "STALE_HOUDINI_RUNTIME",
            "The running Houdini module has an incompatible identity contract; restart the launcher",
            {
                "restart_required": True,
                "request_submitted": request_submitted,
            },
        )
    identity_version = value.get("identity_version")
    launcher_session_id = value.get("launcher_session_id")
    houdini_pid = value.get("houdini_pid")
    hip_path = value.get("hip_path")
    hip_state = value.get("hip_state")
    scene_revision = value.get("scene_revision")
    executor_module_path = value.get("executor_module_path")
    loaded_mtime_ns = value.get("executor_loaded_mtime_ns")
    disk_mtime_ns = value.get("executor_disk_mtime_ns")
    source_status = value.get("executor_source_status")
    if identity_version != RUNTIME_IDENTITY_VERSION:
        raise TransportError(
            "STALE_HOUDINI_RUNTIME",
            "The running Houdini module uses an older identity contract; restart the launcher",
            {
                "expected_identity_version": RUNTIME_IDENTITY_VERSION,
                "actual_identity_version": identity_version,
                "restart_required": True,
                "request_submitted": request_submitted,
            },
        )
    if not _valid_launcher_session_id(launcher_session_id):
        raise TransportError(
            "INVALID_RESPONSE",
            "The Houdini runtime launcher session ID is invalid",
        )
    if (
        isinstance(houdini_pid, bool)
        or not isinstance(houdini_pid, int)
        or houdini_pid <= 0
    ):
        raise TransportError(
            "INVALID_RESPONSE",
            "The Houdini runtime process ID is invalid",
        )
    if (
        hip_state not in {"saved", "unsaved", "unavailable"}
        or hip_path is not None
        and (
            not isinstance(hip_path, str)
            or not hip_path
            or "\x00" in hip_path
            or len(hip_path) > 32_767
        )
        or hip_state != "saved"
        and hip_path is not None
        or hip_state == "saved"
        and hip_path is None
    ):
        raise TransportError(
            "INVALID_RESPONSE",
            "The Houdini runtime HIP identity is invalid",
        )
    if (
        isinstance(scene_revision, bool)
        or not isinstance(scene_revision, int)
        or scene_revision < 0
    ):
        raise TransportError(
            "INVALID_RESPONSE",
            "The Houdini runtime scene revision is invalid",
        )
    if (
        not isinstance(executor_module_path, str)
        or not Path(executor_module_path).is_absolute()
        or "\x00" in executor_module_path
    ):
        raise TransportError(
            "INVALID_RESPONSE",
            "The loaded HIA executor module path is invalid",
        )
    if (
        isinstance(loaded_mtime_ns, bool)
        or not isinstance(loaded_mtime_ns, int)
        or loaded_mtime_ns < 0
        or (
            disk_mtime_ns is not None
            and (
                isinstance(disk_mtime_ns, bool)
                or not isinstance(disk_mtime_ns, int)
                or disk_mtime_ns < 0
            )
        )
        or source_status not in {"current", "stale"}
        or (source_status == "current" and disk_mtime_ns != loaded_mtime_ns)
        or (source_status == "stale" and disk_mtime_ns == loaded_mtime_ns)
    ):
        raise TransportError(
            "INVALID_RESPONSE",
            "The loaded HIA executor source state is invalid",
        )
    actual_executor_path = str(Path(executor_module_path).resolve())
    identity = {
        "identity_version": identity_version,
        "launcher_session_id": launcher_session_id,
        "houdini_pid": houdini_pid,
        "hip_path": hip_path,
        "hip_state": hip_state,
        "scene_revision": scene_revision,
        "executor_module_path": actual_executor_path,
        "executor_loaded_mtime_ns": loaded_mtime_ns,
        "executor_disk_mtime_ns": disk_mtime_ns,
        "executor_source_status": source_status,
    }
    if (
        enforce_expected
        and launcher_session_id != config.launcher_session_id
    ):
        raise TransportError(
            "HOUDINI_SESSION_CHANGED",
            "The endpoint belongs to a different launcher session; reconnect before continuing",
            {
                "expected_launcher_session_id": config.launcher_session_id,
                "actual_launcher_session_id": launcher_session_id,
                "runtime_identity": identity,
                "restart_required": True,
                "request_submitted": request_submitted,
            },
        )
    if (
        enforce_expected
        and os.path.normcase(actual_executor_path)
        != os.path.normcase(config.executor_module_path)
    ):
        raise TransportError(
            "HOUDINI_RUNTIME_SOURCE_CHANGED",
            "Houdini loaded a different HIA executor module; restart the launcher",
            {
                "expected_executor_module_path": config.executor_module_path,
                "actual_executor_module_path": actual_executor_path,
                "runtime_identity": identity,
                "restart_required": True,
                "request_submitted": request_submitted,
            },
        )
    if enforce_expected and source_status != "current":
        raise TransportError(
            "STALE_HOUDINI_RUNTIME",
            "The executor source changed after Houdini loaded it; restart the launcher",
            {
                "runtime_identity": identity,
                "restart_required": True,
                "request_submitted": request_submitted,
            },
        )
    return identity


def _runtime_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity_version": value["identity_version"],
        "launcher_session_id": value["launcher_session_id"],
        "houdini_pid": value["houdini_pid"],
        "executor_module_path": value["executor_module_path"],
    }


def _rounded_seconds(value: float) -> float:
    return round(max(0.0, float(value)), 6)


def _before_submission_details(
    *,
    stage: str,
    queue_seconds: float,
    timeout_seconds: float | None = None,
    request_serialization_seconds: float | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "stage": stage,
        "stdio_queue_seconds": _rounded_seconds(queue_seconds),
        "submission_state": "not_submitted",
        "request_submitted": False,
        "hom_may_still_execute": False,
        "interruptible_after_submission": False,
    }
    if timeout_seconds is not None:
        details["timeout_seconds"] = float(timeout_seconds)
    if request_serialization_seconds is not None:
        details["request_serialization_seconds"] = _rounded_seconds(
            request_serialization_seconds
        )
    return details


def _runtime_timeout(
    timeout_seconds: float,
    queue_seconds: float,
    request_serialization_seconds: float,
    *,
    accepted: bool,
) -> TransportError:
    if accepted:
        stage = "runtime_response_read"
        submission_state = "accepted"
        request_submitted: bool | None = True
        hom_may_still_execute = False
        message = (
            "The Houdini response timed out while being read after the runtime accepted the request. "
            "The result is unknown; do not automatically retry."
        )
    else:
        stage = "runtime_request_outcome_unknown"
        submission_state = "unknown"
        request_submitted = None
        hom_may_still_execute = True
        message = (
            "The Houdini wait timed out before an HTTP response was observed. The runtime may have "
            "accepted or started the request; do not automatically retry."
        )
    return TransportError(
        "TIMEOUT",
        message,
        {
            "stage": stage,
            "timeout_seconds": float(timeout_seconds),
            "stdio_queue_seconds": _rounded_seconds(queue_seconds),
            "request_serialization_seconds": _rounded_seconds(
                request_serialization_seconds
            ),
            "submission_state": submission_state,
            "request_submitted": request_submitted,
            "hom_may_still_execute": hom_may_still_execute,
            "interruptible_after_submission": False,
        },
    )


def _decode_error(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"code": "RESPONSE_TOO_LARGE", "message": "The error response exceeds the byte limit"}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict):
            return error
        return value
    return {}
