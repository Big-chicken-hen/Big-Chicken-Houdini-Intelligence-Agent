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
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import TransportError


SERVER_ID = "hia_mcp_v2"
WIRE_PROTOCOL = "hia-mcp-v2/1"
LOOPBACK_HOST = "127.0.0.1"
EXECUTE_ROUTE = "/hia-mcp-v2/v1/execute"
HEALTH_ROUTE = "/hia-mcp-v2/v1/health"
RUNTIME_DIRECTORY = ".runtime/hia-mcp-v2"
ENV_PREFIX = "HIA_MCP_V2_"
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
DEFAULT_TIMEOUT_SECONDS = 60.0
SERIALIZATION_TIMING_HEADER = "X-HIA-MCP-V2-Serialize-Seconds"


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
    route: str = EXECUTE_ROUTE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ValueError("HIA MCP V2 transport may connect only to 127.0.0.1")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65_535:
            raise ValueError("HIA MCP V2 port must be between 1 and 65535")
        if not _valid_token(self.token):
            raise ValueError("HIA MCP V2 token is missing or invalid")
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
        route = env.get("HIA_MCP_V2_ROUTE", EXECUTE_ROUTE)
        timeout_text = env.get("HIA_MCP_V2_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            port = int(port_text)
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ValueError("HIA MCP V2 environment contains an invalid number") from exc
        return cls(host=host, port=port, token=token, route=route, timeout_seconds=timeout)


class LoopbackTransport:
    """POST one bounded batch request to the live Houdini runtime."""

    def __init__(self, config: TransportConfig) -> None:
        self.config = config
        self._active_lock = threading.Lock()
        self._active: dict[int | str, CancellationToken] = {}
        self._closed = False

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "LoopbackTransport":
        return cls(TransportConfig.from_environment(environment))

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
            serialization_started = time.monotonic()
            body = json.dumps(
                {
                    "protocol": WIRE_PROTOCOL,
                    "id": request_id,
                    "tool": tool_name,
                    "arguments": dict(arguments),
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
                remaining_timeout -= queue_seconds + request_serialization_seconds
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
            request_attempted_at = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=remaining_timeout) as response:
                    headers_received_at = time.monotonic()
                    cancellation.mark_accepted()
                    runtime_serialization_seconds = _header_seconds(
                        response.headers.get(SERIALIZATION_TIMING_HEADER)
                    )
                    response_read_started = time.monotonic()
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    response_read_seconds = max(
                        0.0,
                        time.monotonic() - response_read_started,
                    )
            except urllib.error.HTTPError as exc:
                cancellation.mark_accepted()
                raw_error = exc.read(MAX_RESPONSE_BYTES + 1)
                parsed = _decode_error(raw_error)
                code = parsed.get("code", "HTTP_ERROR")
                message = parsed.get("message", f"Houdini runtime returned HTTP {exc.code}")
                raise TransportError(str(code), str(message), {"http_status": exc.code}) from exc
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
            decode_started = time.monotonic()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError("INVALID_RESPONSE", "The Houdini runtime returned invalid JSON") from exc
            response_decode_seconds = max(0.0, time.monotonic() - decode_started)
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
            if tool_name == "hia_execute_hom":
                result = dict(result)
                phase_timings = result.get("phase_timings")
                merged_timings = dict(phase_timings) if isinstance(phase_timings, Mapping) else {}
                merged_timings.update(
                    {
                        "stdio_queue_seconds": _rounded_seconds(queue_seconds),
                        "request_serialization_seconds": _rounded_seconds(
                            request_serialization_seconds
                        ),
                        "runtime_wait_seconds": _rounded_seconds(
                            headers_received_at - request_attempted_at
                        ),
                        "response_read_seconds": _rounded_seconds(response_read_seconds),
                        "response_decode_seconds": _rounded_seconds(response_decode_seconds),
                        "total_seconds": _rounded_seconds(
                            queue_seconds + (time.monotonic() - call_started)
                        ),
                    }
                )
                if runtime_serialization_seconds is not None:
                    merged_timings["runtime_serialization_seconds"] = _rounded_seconds(
                        runtime_serialization_seconds
                    )
                result["phase_timings"] = merged_timings
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


def _valid_token(value: str) -> bool:
    return (
        isinstance(value, str)
        and 32 <= len(value) <= 512
        and "\r" not in value
        and "\n" not in value
        and all(32 < ord(character) < 127 for character in value)
    )


def _rounded_seconds(value: float) -> float:
    return round(max(0.0, float(value)), 6)


def _header_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


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
        "automatic_retry_safe": True,
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
            "automatic_retry_safe": False,
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
