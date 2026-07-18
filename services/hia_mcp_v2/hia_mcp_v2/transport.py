"""Independent authenticated loopback transport for HIA MCP V2."""

from __future__ import annotations

import json
import os
import socket
import threading
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


class CancellationToken:
    """Cooperative cancellation that can stop a call before HTTP submission."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._submitted = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def mark_submitted(self) -> None:
        self._submitted.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def submitted(self) -> bool:
        return self._submitted.is_set()


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
        if cancellation.cancelled:
            raise TransportError(
                "CANCELLED_BEFORE_EXECUTION",
                "The call was cancelled before Houdini execution began",
                {"interruptible_after_submission": False},
            )
        with self._active_lock:
            if self._closed:
                raise TransportError("TRANSPORT_CLOSED", "The HIA MCP V2 transport is closed")
            self._active[request_id] = cancellation
        try:
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
                    {"interruptible_after_submission": False},
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
            cancellation.mark_submitted()
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                raw_error = exc.read(MAX_RESPONSE_BYTES + 1)
                parsed = _decode_error(raw_error)
                code = parsed.get("code", "HTTP_ERROR")
                message = parsed.get("message", f"Houdini runtime returned HTTP {exc.code}")
                raise TransportError(str(code), str(message), {"http_status": exc.code}) from exc
            except (TimeoutError, socket.timeout) as exc:
                raise TransportError(
                    "TIMEOUT",
                    "The Houdini call exceeded the transport wait timeout; an already-entered HOM call may still finish",
                    {
                        "timeout_seconds": self.config.timeout_seconds,
                        "interruptible_after_submission": False,
                    },
                ) from exc
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    raise TransportError(
                        "TIMEOUT",
                        "The Houdini call exceeded the transport wait timeout; an already-entered HOM call may still finish",
                        {
                            "timeout_seconds": self.config.timeout_seconds,
                            "interruptible_after_submission": False,
                        },
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
            return result
        finally:
            with self._active_lock:
                self._active.pop(request_id, None)

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
