"""Authenticated loopback-only HTTP facade for the Houdini Panel."""

from __future__ import annotations

import hmac
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import BridgeError
from .events import EventBuffer
from .session import BridgeSession


MAX_REQUEST_BYTES = 1024 * 1024


class BridgeApplication:
    def __init__(
        self,
        session: BridgeSession,
        events: EventBuffer,
        token: str,
    ) -> None:
        if len(token) < 32:
            raise ValueError("Bearer token must contain at least 32 characters")
        self.session = session
        self.events = events
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
                body = self._read_json_body()
                payload, status = self._handle_post(parsed.path, body)
            self._write_json(status, payload)
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
        if path == "/v1/shutdown":
            threading.Thread(
                target=self.server.shutdown,
                name="hia-bridge-shutdown",
                daemon=True,
            ).start()
            return {"ok": True, "status": "shutting_down"}, HTTPStatus.OK
        raise BridgeError("NOT_FOUND", "Unknown Bridge endpoint", HTTPStatus.NOT_FOUND)

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BridgeError("INVALID_CONTENT_LENGTH", "Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise BridgeError(
                "REQUEST_TOO_LARGE",
                f"Request body exceeds {MAX_REQUEST_BYTES} bytes",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
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
