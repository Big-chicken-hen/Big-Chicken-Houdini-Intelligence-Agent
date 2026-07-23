"""Threaded stdio JSONL client for the pinned Codex app-server."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import BridgeError, CodexRPCError
from .protocol import ProtocolPolicy


EventSink = Callable[[dict[str, Any]], None]
RequestId = int | str


_LATE_RESPONSE_TOMBSTONE_METHODS = frozenset(
    {"initialize", "thread/resume", "turn/interrupt"}
)
_MAX_LATE_RESPONSE_TOMBSTONES = 16


_SENSITIVE_ENVIRONMENT_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "AUTHORIZATION",
    "PROXY",
)
_SENSITIVE_ENVIRONMENT_NAMES = frozenset({"HIA_BRIDGE_URL"})

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "password",
        "secret",
        "setcookie",
        "token",
    }
)
_CURL_SENSITIVE_HEADER_PATTERN = re.compile(
    r"(?i)((?<!\S)(?:-H|--header)\s+)([\"'])(\s*(?:authorization|"
    r"proxy-authorization|cookie|set-cookie|x-api-key|api-key)\s*:)[^\"']*\2"
)
_CURL_COOKIE_PATTERN = re.compile(
    r"(?i)((?<!\S)(?:--cookie|-b)\s+)(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)?\s*)"
    r"([^\s\"'`;,&}\[\]]+)"
)
_BEARER_PATTERN = re.compile(
    r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{6,})"
)
_NAMED_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(\b(?:x[-_]?api[-_]?key|api[-_]?key|access[-_]?token|auth[-_]?token|"
    r"bearer[-_]?token|id[-_]?token|refresh[-_]?token|password|secret|credential|"
    r"cookie|set-cookie)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&)}\[\]]+)"
)
_QUERY_CREDENTIAL_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|id[_-]?token|"
    r"refresh[_-]?token|token|password|secret|credential)=)"
    r"([^&#\s\"'\[\]]+)"
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)(https?://)[^/@\s:\"']+:[^/@\s\"']+@"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")


@dataclass
class _PendingResponse:
    method: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Any = None


class CodexStdioClient:
    """A single-process Codex client with separated stdout and stderr readers."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        policy: ProtocolPolicy,
        event_sink: EventSink | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("command must be a non-empty string sequence")
        self._command = tuple(command)
        self._cwd = cwd
        self._environment = dict(environment)
        self._sensitive_values = self._collect_sensitive_values(self._environment)
        self._policy = policy
        self._event_sink = event_sink
        self._request_timeout = request_timeout

        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[RequestId, _PendingResponse] = {}
        self._late_response_tombstones: deque[RequestId] = deque(
            maxlen=_MAX_LATE_RESPONSE_TOMBSTONES
        )
        self._server_requests: dict[RequestId, dict[str, Any]] = {}
        self._next_request_id = 1
        self._closing = False

    def set_event_sink(self, event_sink: EventSink) -> None:
        with self._state_lock:
            self._event_sink = event_sink

    def set_environment_overlay(self, values: Mapping[str, str]) -> None:
        """Apply child-only environment values before process creation.

        The Bridge binds its random loopback port after this client is
        constructed.  This one-way pre-start gate lets it add only that
        launch's URL and ordinary Bearer credential without placing either in
        command-line arguments or persistent configuration.
        """

        if not isinstance(values, Mapping):
            raise TypeError("values must be a mapping")
        overlay: dict[str, str] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key or "=" in key or "\x00" in key:
                raise ValueError("environment names must be non-empty safe strings")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("environment values must be strings without NUL")
            overlay[key] = value
        with self._state_lock:
            if self._process is not None:
                raise BridgeError(
                    "CODEX_ENVIRONMENT_LOCKED",
                    "Codex child environment cannot change after process start",
                    http_status=409,
                )
            self._environment.update(overlay)
            self._sensitive_values = self._collect_sensitive_values(
                self._environment
            )

    @property
    def process_id(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def process(self) -> subprocess.Popen[str] | None:
        """Expose the owned process for lifecycle verification only."""

        return self._process

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._process is not None:
                    raise BridgeError("ALREADY_STARTED", "Codex app-server is already started")
                self._closing = False
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    process = subprocess.Popen(
                        list(self._command),
                        cwd=str(self._cwd),
                        env=self._environment,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        shell=False,
                        creationflags=creationflags,
                    )
                except OSError as exc:
                    raise BridgeError(
                        "CODEX_START_FAILED",
                        "Unable to start Codex app-server: "
                        f"{self._redact_text(str(exc))}",
                        http_status=502,
                    ) from exc
                self._process = process

            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                name="hia-codex-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name="hia-codex-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            self._emit("process_started", pid=process.pid)

    def initialize(self) -> Any:
        return self.initialize_with_timeout(self._request_timeout)

    def initialize_with_timeout(self, timeout_seconds: float) -> Any:
        result = self.request_with_timeout(
            "initialize",
            {
                "clientInfo": {
                    "name": "houdini-intelligence",
                    "title": "Big-Chicken Houdini Intelligence Agent",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": False},
            },
            timeout_seconds=timeout_seconds,
        )
        self.notify("initialized")
        return result

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        return self.request_with_timeout(
            method,
            params,
            timeout_seconds=self._request_timeout,
        )

    def request_with_timeout(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        """Send one request with a caller-bounded wait without changing defaults."""

        self._policy.require_client_request(method)
        if not isinstance(params, Mapping):
            raise BridgeError("INVALID_PARAMS", "Request params must be an object")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")
        if not self.is_running:
            raise BridgeError(
                "CODEX_NOT_RUNNING",
                "Codex app-server is not running",
                http_status=503,
            )

        with self._pending_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _PendingResponse(method=method)
            self._pending[request_id] = pending

        try:
            self._send_json(
                {
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                }
            )
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise

        if not pending.event.wait(float(timeout_seconds)):
            with self._pending_lock:
                self._pending.pop(request_id, None)
                if method in _LATE_RESPONSE_TOMBSTONE_METHODS:
                    self._late_response_tombstones.append(request_id)
            raise BridgeError(
                "CODEX_REQUEST_TIMEOUT",
                f"Codex request timed out: {method}",
                http_status=504,
                details={"method": method},
            )
        with self._pending_lock:
            self._pending.pop(request_id, None)
        if pending.error is not None:
            if isinstance(pending.error, BridgeError):
                raise pending.error
            raise CodexRPCError(method, self._redact_value(pending.error))
        return pending.result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._policy.require_client_notification(method)
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send_json(message)

    def pending_server_request(self, request_id: RequestId) -> dict[str, Any] | None:
        with self._pending_lock:
            request = self._server_requests.get(request_id)
            return dict(request) if request is not None else None

    def respond_to_server_request(
        self,
        request_id: RequestId,
        result: Mapping[str, Any],
    ) -> str:
        with self._pending_lock:
            request = self._server_requests.get(request_id)
        if request is None:
            raise BridgeError(
                "APPROVAL_NOT_FOUND",
                "The approval request is no longer pending",
                http_status=404,
                details={"request_id": request_id},
            )
        self._send_json({"id": request_id, "result": dict(result)})
        with self._pending_lock:
            self._server_requests.pop(request_id, None)
        return request["method"]

    def close(
        self,
        grace_seconds: float = 5.0,
        *,
        deadline: float | None = None,
    ) -> None:
        def remaining(maximum: float) -> float:
            if deadline is None:
                return maximum
            return max(0.0, min(maximum, deadline - time.monotonic()))

        with self._lifecycle_lock:
            with self._state_lock:
                if self._closing:
                    return
                process = self._process
                if process is None:
                    return
                self._closing = True

            with self._write_lock:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            try:
                process.wait(timeout=remaining(grace_seconds))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=remaining(2.0))
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=remaining(2.0))
                    except subprocess.TimeoutExpired:
                        pass

            self._fail_pending(
                BridgeError(
                    "CODEX_PROCESS_CLOSED",
                    "Codex app-server closed before the request completed",
                    http_status=503,
                )
            )
            with self._pending_lock:
                self._server_requests.clear()
            for thread in (self._stdout_thread, self._stderr_thread):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=remaining(1.0))
            stopped = process.poll() is not None
            if stopped:
                for stream in (process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
                self._emit("process_stopped", returncode=process.returncode)
            with self._state_lock:
                if stopped and self._process is process:
                    self._process = None
                    self._stdout_thread = None
                    self._stderr_thread = None
                self._closing = False
            if not stopped:
                raise BridgeError(
                    "CODEX_PROCESS_CLOSE_TIMEOUT",
                    "Codex app-server did not stop before the recovery deadline",
                    http_status=504,
                )

    def restart(
        self,
        grace_seconds: float = 1.0,
        *,
        deadline: float | None = None,
    ) -> None:
        """Replace only the owned app-server process; Houdini is untouched."""

        with self._lifecycle_lock:
            self.close(grace_seconds=grace_seconds, deadline=deadline)
            if deadline is not None and time.monotonic() >= deadline:
                raise BridgeError(
                    "CODEX_STOP_RECOVERY_TIMEOUT",
                    "Codex app-server restart exceeded the recovery deadline",
                    http_status=504,
                )
            self.start()

    def _send_json(self, message: Mapping[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise BridgeError(
                    "CODEX_NOT_RUNNING",
                    "Codex app-server is not running",
                    http_status=503,
                )
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise BridgeError(
                    "CODEX_STDIN_FAILED",
                    "Unable to write to Codex app-server: "
                    f"{self._redact_text(str(exc))}",
                    http_status=502,
                ) from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._emit(
                        "protocol_warning",
                        code="INVALID_JSONL",
                        message=f"Ignored invalid app-server stdout JSON: {exc}",
                    )
                    continue
                if not isinstance(message, dict):
                    self._emit(
                        "protocol_warning",
                        code="INVALID_MESSAGE",
                        message="Ignored non-object app-server message",
                    )
                    continue
                self._handle_message(message)
        finally:
            with self._state_lock:
                report_exit = self._process is process and not self._closing
            if report_exit:
                returncode = process.poll()
                self._emit("process_exit", returncode=returncode)
                self._fail_pending(
                    BridgeError(
                        "CODEX_PROCESS_EXITED",
                        "Codex app-server stdout closed unexpectedly",
                        http_status=502,
                        details={"returncode": returncode},
                    )
                )

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.rstrip("\r\n")
            if line:
                self._emit("codex_stderr", line=self._redact_text(line))

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            if "id" in message:
                self._handle_server_request(message)
            else:
                self._handle_notification(message)
            return
        if "id" in message and ("result" in message or "error" in message):
            self._handle_response(message)
            return
        self._emit(
            "protocol_warning",
            code="UNRECOGNIZED_MESSAGE",
            message="Ignored an unrecognized app-server message",
        )

    def _handle_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        with self._pending_lock:
            pending = self._pending.get(request_id)
            is_late_response = request_id in self._late_response_tombstones
        if pending is None:
            if is_late_response:
                return
            self._emit(
                "protocol_warning",
                code="UNKNOWN_RESPONSE_ID",
                message="Ignored a response for an unknown request id",
                request_id=request_id,
            )
            return
        if "error" in message:
            pending.error = self._redact_value(message["error"])
        else:
            pending.result = self._redact_value(message.get("result"))
        pending.event.set()

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        request_id = message.get("id")
        if not self._policy.allows_server_request(method):
            try:
                self._send_json(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Server request is not allowlisted: {method}",
                        },
                    }
                )
            finally:
                self._emit(
                    "protocol_warning",
                    code="SERVER_REQUEST_REJECTED",
                    message="Rejected a server request outside the allowlist",
                    method=method,
                    request_id=request_id,
                )
            return
        params = message.get("params")
        request = {
            "method": method,
            "params": self._redact_value(
                params if isinstance(params, dict) else {}
            ),
        }
        with self._pending_lock:
            if request_id in self._server_requests:
                self._emit(
                    "protocol_warning",
                    code="DUPLICATE_SERVER_REQUEST_ID",
                    message="Ignored duplicate server request id",
                    request_id=request_id,
                )
                return
            self._server_requests[request_id] = request
        self._emit(
            "server_request",
            request_id=request_id,
            method=method,
            params=request["params"],
        )

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message["method"]
        if not self._policy.allows_server_notification(method):
            self._emit(
                "protocol_warning",
                code="UNKNOWN_NOTIFICATION_IGNORED",
                message="Recorded and ignored a notification outside the allowlist",
                method=method,
            )
            return
        params = message.get("params")
        self._emit(
            "codex_notification",
            method=method,
            params=params if isinstance(params, dict) else {},
        )

    def _fail_pending(self, error: BridgeError) -> None:
        with self._pending_lock:
            pending_requests = list(self._pending.values())
        for pending in pending_requests:
            if not pending.event.is_set():
                pending.error = error
                pending.event.set()

    def _emit(self, event_type: str, **fields: Any) -> None:
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink(self._redact_value({"type": event_type, **fields}))
        except Exception:
            # Event consumers must never terminate the stdout/stderr reader.
            pass

    @staticmethod
    def _collect_sensitive_values(environment: Mapping[str, str]) -> tuple[str, ...]:
        values = {
            value
            for name, value in environment.items()
            if isinstance(name, str)
            and isinstance(value, str)
            and len(value) >= 8
            and (
                name.upper() in _SENSITIVE_ENVIRONMENT_NAMES
                or any(
                    marker in name.upper()
                    for marker in _SENSITIVE_ENVIRONMENT_MARKERS
                )
            )
        }
        return tuple(sorted(values, key=len, reverse=True))

    def _redact_text(self, value: str) -> str:
        redacted = value
        for secret in self._sensitive_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = _CURL_SENSITIVE_HEADER_PATTERN.sub(
            lambda match: (
                f"{match.group(1)}{match.group(2)}{match.group(3)} "
                f"[REDACTED]{match.group(2)}"
            ),
            redacted,
        )
        redacted = _CURL_COOKIE_PATTERN.sub(
            lambda match: f"{match.group(1)}[REDACTED]",
            redacted,
        )
        redacted = _URL_USERINFO_PATTERN.sub(r"\1[REDACTED]@", redacted)
        redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", redacted)
        redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
        redacted = _NAMED_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]", redacted)
        redacted = _QUERY_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]", redacted)
        redacted = _OPENAI_KEY_PATTERN.sub("[REDACTED]", redacted)
        return redacted

    @staticmethod
    def _is_sensitive_key(value: str) -> bool:
        normalized = "".join(
            character for character in value.lower() if character.isalnum()
        )
        if normalized in _SENSITIVE_FIELD_NAMES:
            return True
        if normalized.endswith(
            (
                "apikey",
                "authorization",
                "cookie",
                "credential",
                "password",
                "secret",
                "accesstoken",
                "authtoken",
                "bearertoken",
                "idtoken",
                "refreshtoken",
            )
        ):
            return True
        words = re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
            value.replace("-", " ").replace("_", " "),
        )
        lowered_words = [word.lower() for word in words]
        if any(
            word in {"authorization", "cookie", "credential", "password", "secret"}
            for word in lowered_words
        ):
            return True
        return any(
            first == "api" and second == "key"
            for first, second in zip(lowered_words, lowered_words[1:])
        )

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                redacted_key = self._redact_text(key) if isinstance(key, str) else key
                redacted[redacted_key] = (
                    "[REDACTED]"
                    if isinstance(key, str) and self._is_sensitive_key(key)
                    else self._redact_value(item)
                )
            return redacted
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(item) for item in value)
        return value
