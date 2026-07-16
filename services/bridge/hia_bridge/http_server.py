"""Authenticated loopback-only HTTP facade for the Houdini Panel."""

from __future__ import annotations

import hmac
import json
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from hia_core.houdini_contract import ContractError, SchemaRegistry, strict_json_loads

from .errors import BridgeError
from .events import EventBuffer
from .scene_queue import RequestSnapshot, SceneQueue, SceneQueueError
from .session import BridgeSession


MAX_REQUEST_BYTES = 1024 * 1024
MAX_SCENE_REQUEST_BYTES = 262_144
MAX_SCENE_POLL_MS = 1_000
_SCENE_RESULT_PATH = re.compile(
    r"^/v1/scene/requests/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/result$"
)
_SCENE_APPROVAL_PATH = re.compile(
    r"^/v1/scene/requests/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/approval$"
)
_SCENE_CANCEL_PATH = re.compile(
    r"^/v1/scene/requests/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/cancel$"
)


class BridgeApplication:
    def __init__(
        self,
        session: BridgeSession,
        events: EventBuffer,
        token: str,
        *,
        scene_queue: SceneQueue | None = None,
        scene_registry: SchemaRegistry | None = None,
    ) -> None:
        if len(token) < 32:
            raise ValueError("Bearer token must contain at least 32 characters")
        self.session = session
        self.events = events
        if scene_queue is None and scene_registry is not None:
            raise ValueError("A scene registry cannot be enabled without a scene queue")
        self.scene_queue = scene_queue
        self.scene_registry = (
            scene_registry or SchemaRegistry() if scene_queue is not None else None
        )
        if (
            self.scene_queue is not None
            and self.scene_registry is not None
            and self.scene_queue.expected_schema_digest
            != self.scene_registry.manifest_digest
        ):
            raise ValueError(
                "Scene queue schema digest does not match the frozen registry"
            )
        self._expected_authorization = f"Bearer {token}"

    def authorized(self, value: str | None) -> bool:
        return value is not None and hmac.compare_digest(
            value,
            self._expected_authorization,
        )


class LoopbackHTTPServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that refuses every non-loopback bind address."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        application: BridgeApplication,
    ) -> None:
        host, port = server_address
        if host != "127.0.0.1":
            raise ValueError("Bridge may bind only to 127.0.0.1")
        self.application = application
        super().__init__((host, port), BridgeRequestHandler)


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server: LoopbackHTTPServer
    server_version = "HIA-Bridge/0.1"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("bridge-http: " + (format % args) + "\n")

    def _handle(self, http_method: str) -> None:
        try:
            if not self.server.application.authorized(
                self.headers.get("Authorization")
            ):
                raise BridgeError(
                    "UNAUTHORIZED",
                    "A valid Bridge Bearer token is required",
                    http_status=HTTPStatus.UNAUTHORIZED,
                )
            parsed = urlsplit(self.path)
            if http_method == "GET":
                payload, status = self._handle_get(parsed.path, parsed.query)
            else:
                scene_request = parsed.path.startswith("/v1/scene/")
                body = self._read_json_body(
                    max_bytes=(MAX_SCENE_REQUEST_BYTES if scene_request else MAX_REQUEST_BYTES),
                    strict=scene_request,
                )
                payload, status = self._handle_post(parsed.path, body)
            self._write_json(status, payload)
        except SceneQueueError as exc:
            self._write_json(
                exc.status,
                {
                    "ok": False,
                    "structured_error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                },
            )
        except ContractError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "structured_error": exc.to_dict()},
            )
        except BridgeError as exc:
            self._write_json(exc.http_status, exc.to_dict())
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            error = BridgeError("INVALID_REQUEST", str(exc), HTTPStatus.BAD_REQUEST)
            self._write_json(error.http_status, error.to_dict())
        except BrokenPipeError:
            pass
        except Exception as exc:
            sys.stderr.write(f"bridge-http internal error: {type(exc).__name__}: {exc}\n")
            error = BridgeError(
                "INTERNAL_ERROR",
                "The Bridge could not complete the request",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            self._write_json(error.http_status, error.to_dict())

    def _handle_get(self, path: str, query: str) -> tuple[dict[str, Any], int]:
        application = self.server.application
        if path == "/v1/health":
            return {
                "ok": True,
                "status": "ok",
                "session": application.session.snapshot(),
            }, HTTPStatus.OK
        if path == "/v1/session":
            return {"ok": True, "session": application.session.snapshot()}, HTTPStatus.OK
        if path == "/v1/models":
            result = application.session.list_models()
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/events":
            values = parse_qs(query, keep_blank_values=False)
            after = int(values.get("after", ["0"])[0])
            timeout = float(values.get("timeout", ["15"])[0])
            polled = application.events.poll(after, timeout=timeout)
            return {"ok": True, **polled}, HTTPStatus.OK
        if path == "/v1/scene/requests/next":
            queue, _ = self._scene_components()
            wait_ms = self._scene_wait_ms(query)
            work = queue.poll_next(wait_ms / 1000.0)
            return {
                "ok": True,
                "work": None if work is None else work.to_dict(),
            }, HTTPStatus.OK
        matched = _SCENE_RESULT_PATH.fullmatch(path)
        if matched is not None:
            queue, _ = self._scene_components()
            wait_ms = self._scene_wait_ms(query)
            snapshot = queue.get_result(matched.group(1), wait_ms / 1000.0)
            if snapshot.terminal:
                return self._terminal_scene_payload(snapshot), HTTPStatus.OK
            return {"ok": True, **snapshot.to_dict()}, HTTPStatus.ACCEPTED
        raise BridgeError("NOT_FOUND", "Unknown Bridge endpoint", HTTPStatus.NOT_FOUND)

    def _handle_post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        application = self.server.application
        if path == "/v1/session":
            action = body.get("action")
            if action == "start":
                result = application.session.start_thread(body.get("model"))
            elif action == "resume":
                result = application.session.resume_thread(body.get("thread_id"))
            elif action == "read":
                result = application.session.read_thread(body.get("thread_id"))
            else:
                raise BridgeError(
                    "INVALID_SESSION_ACTION",
                    "Session action must be start, resume, or read",
                )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/turn":
            result = application.session.start_turn(
                body.get("text"),
                body.get("model"),
                body.get("effort"),
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/interrupt":
            result = application.session.interrupt_turn()
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/approval":
            if "request_id" not in body:
                raise BridgeError("MISSING_REQUEST_ID", "request_id is required")
            result = application.session.resolve_approval(
                body["request_id"],
                body.get("decision"),
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/scene/requests":
            return self._submit_scene_request(body)
        matched = _SCENE_APPROVAL_PATH.fullmatch(path)
        if matched is not None:
            self._require_exact_fields(
                body,
                {"decision", "request_digest", "launch_id", "generation"},
            )
            queue, _ = self._scene_components()
            snapshot = queue.decide_approval(
                matched.group(1),
                body["decision"],
                body["request_digest"],
                body["launch_id"],
                body["generation"],
            )
            return {"ok": True, **snapshot.to_dict()}, HTTPStatus.OK
        matched = _SCENE_RESULT_PATH.fullmatch(path)
        if matched is not None:
            self._require_exact_fields(body, {"executor_token", "result"})
            queue, registry = self._scene_components()
            tool_name, arguments = queue.request_context(matched.group(1))
            result = registry.validate_output(tool_name, arguments, body["result"])
            snapshot = queue.complete(
                matched.group(1),
                body["executor_token"],
                result,
            )
            return {"ok": True, **snapshot.to_dict()}, HTTPStatus.OK
        matched = _SCENE_CANCEL_PATH.fullmatch(path)
        if matched is not None:
            self._require_exact_fields(body, set())
            queue, _ = self._scene_components()
            snapshot = queue.cancel(matched.group(1))
            return {"ok": True, **snapshot.to_dict()}, HTTPStatus.OK
        if path == "/v1/shutdown":
            if application.scene_queue is not None:
                application.scene_queue.shutdown()
            threading.Thread(
                target=self.server.shutdown,
                name="hia-bridge-shutdown",
                daemon=True,
            ).start()
            return {"ok": True, "status": "shutting_down"}, HTTPStatus.OK
        raise BridgeError("NOT_FOUND", "Unknown Bridge endpoint", HTTPStatus.NOT_FOUND)

    def _scene_components(self) -> tuple[SceneQueue, SchemaRegistry]:
        application = self.server.application
        if application.scene_queue is None or application.scene_registry is None:
            raise BridgeError(
                "SCENE_GATEWAY_DISABLED",
                "The offline scene gateway is not enabled for this Bridge instance",
                HTTPStatus.NOT_FOUND,
            )
        return application.scene_queue, application.scene_registry

    @staticmethod
    def _require_exact_fields(body: dict[str, Any], expected: set[str]) -> None:
        if set(body) != expected:
            raise BridgeError(
                "INVALID_REQUEST",
                "Scene request body does not match the frozen field set",
                HTTPStatus.BAD_REQUEST,
                {"expected_fields": sorted(expected)},
            )

    @staticmethod
    def _scene_wait_ms(query: str) -> int:
        values = parse_qs(query, keep_blank_values=False)
        if set(values) - {"wait_ms"}:
            raise BridgeError("INVALID_REQUEST", "Unknown scene poll query field")
        raw = values.get("wait_ms", ["0"])
        if len(raw) != 1:
            raise BridgeError("INVALID_REQUEST", "wait_ms must occur once")
        try:
            wait_ms = int(raw[0])
        except (TypeError, ValueError) as exc:
            raise BridgeError("INVALID_REQUEST", "wait_ms must be an integer") from exc
        if not 0 <= wait_ms <= MAX_SCENE_POLL_MS:
            raise BridgeError(
                "INVALID_REQUEST",
                f"wait_ms must be between 0 and {MAX_SCENE_POLL_MS}",
            )
        return wait_ms

    def _submit_scene_request(
        self,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        self._require_exact_fields(body, {"tool_name", "arguments"})
        tool_name = body["tool_name"]
        arguments = body["arguments"]
        queue, registry = self._scene_components()
        try:
            validated = registry.validate_input(tool_name, arguments)
            absolute_deadline = time.monotonic() + validated["deadline_ms"] / 1000.0
            request = queue.build_request(tool_name, validated, absolute_deadline)
            snapshot = queue.submit(request)
        except SceneQueueError as exc:
            try:
                result = registry.make_error_output(
                    tool_name,
                    arguments,
                    exc.code,
                    exc.message,
                    retryable=exc.status in {408, 429, 503},
                )
            except ContractError:
                raise exc
            return {
                "ok": False,
                "request_id": arguments["request_id"],
                "state": "completed",
                "terminal": True,
                "result": result,
            }, exc.status
        if snapshot.terminal:
            return self._terminal_scene_payload(snapshot), HTTPStatus.OK
        return {"ok": True, **snapshot.to_dict()}, HTTPStatus.ACCEPTED

    def _terminal_scene_payload(self, snapshot: RequestSnapshot) -> dict[str, Any]:
        queue, registry = self._scene_components()
        tool_name, arguments = queue.request_context(snapshot.request_id)
        if snapshot.result is not None:
            result = snapshot.result
            if snapshot.replayed:
                result = registry.make_replay_output(tool_name, arguments, result)
            else:
                result = registry.validate_output(tool_name, arguments, result)
            return {"ok": True, **snapshot.to_dict(), "result": result}
        error = snapshot.structured_error or {}
        code = error.get("code")
        message = error.get("message")
        status = error.get("status", HTTPStatus.CONFLICT)
        if isinstance(code, str) and isinstance(message, str):
            try:
                result = registry.make_error_output(
                    tool_name,
                    arguments,
                    code,
                    message,
                    retryable=status in {408, 429, 503},
                )
            except ContractError:
                raise BridgeError(
                    code,
                    message,
                    int(status),
                    error.get("details") if isinstance(error.get("details"), dict) else None,
                )
            return {"ok": True, **snapshot.to_dict(), "result": result}
        raise BridgeError(
            "INVALID_SCENE_RESULT",
            "Terminal scene request has no valid result",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _read_json_body(
        self,
        *,
        max_bytes: int = MAX_REQUEST_BYTES,
        strict: bool = False,
    ) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BridgeError("INVALID_CONTENT_LENGTH", "Invalid Content-Length") from exc
        if length < 0 or length > max_bytes:
            raise BridgeError(
                "REQUEST_TOO_LARGE",
                f"Request body exceeds {max_bytes} bytes",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        value = strict_json_loads(raw, "Bridge scene request", max_bytes=max_bytes) if strict else json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise BridgeError("INVALID_JSON_ROOT", "Request JSON must be an object")
        return value

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
