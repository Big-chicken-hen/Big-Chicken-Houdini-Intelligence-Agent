"""Authenticated loopback HTTP facade for the HIA MCP V2 Houdini runtime."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
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
RUNTIME_IDENTITY_VERSION = 1
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
_RUNTIME_BINDING_FIELDS = (
    "identity_version",
    "launcher_session_id",
    "houdini_pid",
    "executor_module_path",
)
_SCENE_WRITE_TOOLS = frozenset(
    {"hia_execute_hom", "hia_run_effect_experiment"}
)


class _RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        *,
        executor: HoudiniExecutor,
        token: str,
        launcher_session_id: str,
        executor_module_path: str,
    ) -> None:
        host, port = address
        if host != LOOPBACK_HOST:
            raise ValueError("HIA MCP V2 runtime may bind only to 127.0.0.1")
        if not _valid_token(token):
            raise ValueError("HIA MCP V2 runtime token is missing or invalid")
        self.executor = executor
        self.expected_authorization = f"Bearer {token}"
        self.launcher_session_id = launcher_session_id
        self.executor_module_path = executor_module_path
        self.executor_loaded_mtime_ns = _file_mtime_ns(executor_module_path)
        if self.executor_loaded_mtime_ns is None:
            raise ValueError("The loaded HIA executor source is unavailable")
        super().__init__((host, port), _RuntimeRequestHandler)

    def runtime_identity(self) -> dict[str, Any]:
        hip_path, hip_state = _current_hip(self.executor)
        disk_mtime_ns = _file_mtime_ns(self.executor_module_path)
        return {
            "identity_version": RUNTIME_IDENTITY_VERSION,
            "launcher_session_id": self.launcher_session_id,
            "houdini_pid": os.getpid(),
            "hip_path": hip_path,
            "hip_state": hip_state,
            "scene_revision": self.executor.scene_revision,
            "executor_module_path": self.executor_module_path,
            "executor_loaded_mtime_ns": self.executor_loaded_mtime_ns,
            "executor_disk_mtime_ns": disk_mtime_ns,
            "executor_source_status": (
                "current"
                if disk_mtime_ns == self.executor_loaded_mtime_ns
                else "stale"
            ),
        }


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
                    "result": {
                        "server_id": "hia_mcp_v2",
                        "scene_revision": self.server.executor.scene_revision,
                        "runtime_identity": self.server.runtime_identity(),
                    },
                },
            )
            return
        if method != "POST" or parsed.path != EXECUTE_ROUTE:
            self._send_error(HTTPStatus.NOT_FOUND, "ROUTE_NOT_FOUND", "Unknown HIA MCP V2 runtime route")
            return
        try:
            payload = self._read_request()
            tool_name = payload["tool"]
            scene_write = tool_name in _SCENE_WRITE_TOOLS
            current_identity = self.server.runtime_identity()
            source_current = (
                current_identity["executor_source_status"] == "current"
            )
            binding_matches = _same_runtime_binding(
                payload["expected_runtime"],
                current_identity,
            )
            if scene_write and not source_current:
                raise HiaRuntimeError(
                    "STALE_HOUDINI_RUNTIME",
                    "The executor source changed after Houdini loaded it; restart the launcher",
                    {
                        "runtime_identity": current_identity,
                        "restart_required": True,
                        "request_submitted": False,
                    },
                )
            if scene_write and not binding_matches:
                raise HiaRuntimeError(
                    "HOUDINI_SESSION_CHANGED",
                    "The live Houdini process or executor module changed; reconnect before executing",
                    {
                        "expected": _runtime_binding(payload["expected_runtime"]),
                        "actual": _runtime_binding(current_identity),
                        "runtime_identity": current_identity,
                        "restart_required": True,
                        "request_submitted": False,
                    },
                )
            arguments = payload["arguments"]
            result = dict(self.server.executor.dispatch(tool_name, arguments))
            result_identity = self.server.runtime_identity()
            result["runtime_identity"] = result_identity
            warning_code = (
                "STALE_HOUDINI_RUNTIME"
                if result_identity["executor_source_status"] != "current"
                else "HOUDINI_SESSION_CHANGED"
                if not _same_runtime_binding(
                    payload["expected_runtime"],
                    result_identity,
                )
                else None
            )
            if warning_code is not None:
                result["restart_required"] = True
                result["identity_warning"] = {
                    "code": warning_code,
                    "message": (
                        "The read completed against the observed Houdini runtime; "
                        "reconnect before any scene write"
                    ),
                }
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
        if not isinstance(value, dict) or set(value) != {
            "protocol",
            "id",
            "tool",
            "arguments",
            "expected_runtime",
        }:
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime request envelope is invalid")
        if value.get("protocol") != WIRE_PROTOCOL:
            raise HiaRuntimeError("INVALID_PROTOCOL", "The runtime request protocol is unsupported")
        if not isinstance(value.get("tool"), str) or not isinstance(value.get("arguments"), dict):
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime tool or arguments are invalid")
        request_id = value.get("id")
        if not ((isinstance(request_id, int) and not isinstance(request_id, bool)) or (isinstance(request_id, str) and request_id)):
            raise HiaRuntimeError("INVALID_REQUEST", "The runtime request id is invalid")
        expected_runtime = value.get("expected_runtime")
        if (
            not isinstance(expected_runtime, Mapping)
            or set(expected_runtime) != set(_RUNTIME_BINDING_FIELDS)
            or _runtime_binding(expected_runtime) is None
        ):
            raise HiaRuntimeError(
                "INVALID_REQUEST",
                "The expected Houdini runtime identity is invalid",
            )
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
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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
    launcher_session_id: str
    executor_module_path: str

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
            "HIA_MCP_V2_EXECUTOR_PATH": self.executor_module_path,
            "HIA_LAUNCHER_SESSION_ID": self.launcher_session_id,
        }

    def identity(self) -> dict[str, Any]:
        return self.server.runtime_identity()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        close = getattr(self.server.executor, "close", None)
        if callable(close):
            close()


def start_runtime_server(
    *,
    executor: HoudiniExecutor | None = None,
    project_root: str | Path | None = None,
    token: str | None = None,
    port: int = 0,
    launcher_session_id: str | None = None,
    expected_executor_path: str | Path | None = None,
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
    resolved_launcher_session_id = (
        launcher_session_id or os.environ.get("HIA_LAUNCHER_SESSION_ID", "")
    )
    if not _valid_launcher_session_id(resolved_launcher_session_id):
        raise ValueError("HIA launcher session ID is missing or invalid")
    selected_executor = executor or HoudiniExecutor(project_root=resolved_root)
    executor_module_path = _executor_module_path(selected_executor)
    configured_executor_path = expected_executor_path or os.environ.get(
        "HIA_MCP_V2_EXECUTOR_PATH",
        "",
    )
    if not configured_executor_path:
        raise ValueError("Expected HIA executor module path is missing")
    resolved_executor_path = str(Path(configured_executor_path).resolve())
    if os.path.normcase(resolved_executor_path) != os.path.normcase(
        executor_module_path
    ):
        raise ValueError(
            "The loaded HIA executor module does not match the launcher source"
        )
    server = _RuntimeHTTPServer(
        (LOOPBACK_HOST, port),
        executor=selected_executor,
        token=resolved_token,
        launcher_session_id=resolved_launcher_session_id,
        executor_module_path=executor_module_path,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="hia-mcp-v2-runtime",
        daemon=True,
    )
    thread.start()
    return RuntimeSession(
        server=server,
        thread=thread,
        _token=resolved_token,
        runtime_directory=runtime_directory,
        launcher_session_id=resolved_launcher_session_id,
        executor_module_path=executor_module_path,
    )


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


def _executor_module_path(executor: Any) -> str:
    module = sys.modules.get(type(executor).__module__)
    raw_path = getattr(module, "__file__", None)
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("The loaded HIA executor module path is unavailable")
    return str(Path(raw_path).resolve())


def _file_mtime_ns(path: str) -> int | None:
    try:
        value = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _current_hip(executor: Any) -> tuple[str | None, str]:
    def read() -> tuple[str | None, str]:
        hou_module = getattr(executor, "_hou", None)
        hip_file = getattr(hou_module, "hipFile", None)
        if hip_file is None:
            return None, "unavailable"
        is_new = bool(hip_file.isNewFile())
        raw_path = str(hip_file.path() or "")
        if is_new or not raw_path:
            return None, "unsaved"
        return _bounded_text(_redact_text(raw_path), 32_767), "saved"

    runner = getattr(executor, "_run_on_main_thread", None)
    try:
        value = runner(read) if callable(runner) else read()
    except Exception:
        return None, "unavailable"
    return value if isinstance(value, tuple) and len(value) == 2 else (None, "unavailable")


def _runtime_binding(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identity_version = value.get("identity_version")
    launcher_session_id = value.get("launcher_session_id")
    houdini_pid = value.get("houdini_pid")
    executor_module_path = value.get("executor_module_path")
    if (
        identity_version != RUNTIME_IDENTITY_VERSION
        or not _valid_launcher_session_id(launcher_session_id)
        or isinstance(houdini_pid, bool)
        or not isinstance(houdini_pid, int)
        or houdini_pid <= 0
        or not isinstance(executor_module_path, str)
        or not executor_module_path
        or "\x00" in executor_module_path
    ):
        return None
    return {
        "identity_version": identity_version,
        "launcher_session_id": launcher_session_id,
        "houdini_pid": houdini_pid,
        "executor_module_path": executor_module_path,
    }


def _same_runtime_binding(expected: Any, actual: Any) -> bool:
    expected_binding = _runtime_binding(expected)
    actual_binding = _runtime_binding(actual)
    return expected_binding is not None and expected_binding == actual_binding


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
