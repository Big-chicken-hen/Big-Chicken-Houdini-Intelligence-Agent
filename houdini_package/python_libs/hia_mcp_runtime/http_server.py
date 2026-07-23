"""Authenticated loopback HTTP facade for the HIA MCP V2 Houdini runtime."""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .executor import HiaRuntimeError, HoudiniExecutor, _bounded_text, _redact_text


WIRE_PROTOCOL = "hia-mcp-v2/1"
LOOPBACK_HOST = "127.0.0.1"
EXECUTE_ROUTE = "/hia-mcp-v2/v1/execute"
HEALTH_ROUTE = "/hia-mcp-v2/v1/health"
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
SERIALIZATION_TIMING_HEADER = "X-HIA-MCP-V2-Serialize-Seconds"


class _RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        *,
        executor: HoudiniExecutor,
        token: str,
    ) -> None:
        host, port = address
        if host != LOOPBACK_HOST:
            raise ValueError("HIA MCP V2 runtime may bind only to 127.0.0.1")
        if not _valid_token(token):
            raise ValueError("HIA MCP V2 runtime token is missing or invalid")
        self.executor = executor
        self.expected_authorization = f"Bearer {token}"
        super().__init__((host, port), _RuntimeRequestHandler)


class _RuntimeRequestHandler(BaseHTTPRequestHandler):
    server: _RuntimeHTTPServer
    server_version = "HIA-MCP-V2-Runtime/0.1"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("hia-mcp-v2-runtime: " + (format % args) + "\n")

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Keep successful runtime traffic out of Houdini's Python Shell."""

        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if 200 <= status < 300:
            return
        super().log_request(code, size)

    def _handle(self, method: str) -> None:
        authorization = self.headers.get("Authorization")
        if authorization is None:
            self._send_error(HTTPStatus.UNAUTHORIZED, "UNAUTHORIZED", "Bearer token required")
            return
        if not hmac.compare_digest(authorization, self.server.expected_authorization):
            self._send_error(HTTPStatus.FORBIDDEN, "FORBIDDEN", "Bearer token rejected")
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._send_error(HTTPStatus.NOT_FOUND, "ROUTE_NOT_FOUND", "Unknown HIA MCP V2 runtime route")
            return
        if method == "GET" and parsed.path == HEALTH_ROUTE:
            self._send_json(
                HTTPStatus.OK,
                {
                    "protocol": WIRE_PROTOCOL,
                    "ok": True,
                    "result": {"server_id": "hia_mcp_v2", "scene_revision": self.server.executor.scene_revision},
                },
            )
            return
        if method != "POST" or parsed.path != EXECUTE_ROUTE:
            self._send_error(HTTPStatus.NOT_FOUND, "ROUTE_NOT_FOUND", "Unknown HIA MCP V2 runtime route")
            return
        try:
            payload = self._read_request()
            tool_name = payload["tool"]
            arguments = payload["arguments"]
            result = self.server.executor.dispatch(tool_name, arguments)
            self._send_json(
                HTTPStatus.OK,
                {"protocol": WIRE_PROTOCOL, "ok": True, "id": payload["id"], "result": result},
            )
        except HiaRuntimeError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, exc.code, exc.message, exc.details)
        except Exception as exc:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "The HIA MCP V2 runtime encountered an internal error",
                {"reason": _bounded_text(_redact_text(str(exc)), 1024)},
            )

    def _read_request(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise HiaRuntimeError("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            raise HiaRuntimeError("LENGTH_REQUIRED", "Content-Length is required")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise HiaRuntimeError("INVALID_REQUEST", "Content-Length is invalid") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise HiaRuntimeError("REQUEST_TOO_LARGE", "The request exceeds the HIA MCP V2 byte limit", {"limit_bytes": MAX_REQUEST_BYTES})
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HiaRuntimeError("INVALID_REQUEST", "The request is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict) or set(value) != {"protocol", "id", "tool", "arguments"}:
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime request envelope is invalid")
        if value.get("protocol") != WIRE_PROTOCOL:
            raise HiaRuntimeError("INVALID_PROTOCOL", "The runtime request protocol is unsupported")
        if not isinstance(value.get("tool"), str) or not isinstance(value.get("arguments"), dict):
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime tool or arguments are invalid")
        request_id = value.get("id")
        if not ((isinstance(request_id, int) and not isinstance(request_id, bool)) or (isinstance(request_id, str) and request_id)):
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime request id is invalid")
        return value

    def _send_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._send_json(
            status,
            {
                "protocol": WIRE_PROTOCOL,
                "ok": False,
                "error": {
                    "code": str(code),
                    "message": _bounded_text(_redact_text(str(message)), 2048),
                    "details": _redacted_details(details),
                },
            },
        )

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        serialization_started = time.monotonic()
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_RESPONSE_BYTES:
            status = HTTPStatus.INSUFFICIENT_STORAGE
            raw = json.dumps(
                {
                    "protocol": WIRE_PROTOCOL,
                    "ok": False,
                    "error": {
                        "code": "RESPONSE_TOO_LARGE",
                        "message": "The runtime response exceeds the HIA MCP V2 byte limit",
                        "details": {"limit_bytes": MAX_RESPONSE_BYTES},
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
        serialization_seconds = max(0.0, time.monotonic() - serialization_started)
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                SERIALIZATION_TIMING_HEADER,
                f"{serialization_seconds:.9f}",
            )
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # The stdio-side transport may have timed out.  The already-entered
            # HOM call is not killed; there is simply no client left to receive
            # its eventual result.
            return


@dataclass
class RuntimeSession:
    """One independently authenticated runtime listener and its lifecycle."""

    server: _RuntimeHTTPServer
    thread: threading.Thread
    _token: str
    runtime_directory: Path

    @property
    def host(self) -> str:
        return LOOPBACK_HOST

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def route(self) -> str:
        return EXECUTE_ROUTE

    def environment(self) -> dict[str, str]:
        """Return launcher-only environment; callers must not log this mapping."""

        return {
            "HIA_MCP_V2_HOST": LOOPBACK_HOST,
            "HIA_MCP_V2_PORT": str(self.port),
            "HIA_MCP_V2_TOKEN": self._token,
            "HIA_MCP_V2_ROUTE": EXECUTE_ROUTE,
            "HIA_MCP_V2_RUNTIME_DIR": str(self.runtime_directory),
        }

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def start_runtime_server(
    *,
    executor: HoudiniExecutor | None = None,
    project_root: str | Path | None = None,
    token: str | None = None,
    port: int = 0,
) -> RuntimeSession:
    """Start one random-port, random-token loopback runtime for this session."""

    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("HIA MCP V2 runtime port must be zero or between 1 and 65535")
    resolved_root = Path(project_root or Path.cwd()).resolve()
    runtime_directory = (resolved_root / ".runtime" / "hia-mcp-v2").resolve()
    try:
        runtime_directory.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("HIA MCP V2 runtime directory must stay under the project root") from exc
    runtime_directory.mkdir(parents=True, exist_ok=True)
    resolved_token = token or secrets.token_urlsafe(48)
    if not _valid_token(resolved_token):
        raise ValueError("HIA MCP V2 runtime token is missing or invalid")
    selected_executor = executor or HoudiniExecutor(project_root=resolved_root)
    server = _RuntimeHTTPServer(
        (LOOPBACK_HOST, port),
        executor=selected_executor,
        token=resolved_token,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="hia-mcp-v2-runtime",
        daemon=True,
    )
    thread.start()
    return RuntimeSession(server=server, thread=thread, _token=resolved_token, runtime_directory=runtime_directory)


def _valid_token(value: str) -> bool:
    return (
        isinstance(value, str)
        and 32 <= len(value) <= 512
        and "\r" not in value
        and "\n" not in value
        and all(32 < ord(character) < 127 for character in value)
    )


def _redacted_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, Mapping):
        return {}
    raw = json.dumps(dict(details), ensure_ascii=False, default=str)
    redacted = _redact_text(raw)
    try:
        value = json.loads(redacted)
    except json.JSONDecodeError:
        return {"summary": _bounded_text(redacted, 4096)}
    return value if isinstance(value, dict) else {}
