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
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlsplit

from hia_core.houdini_contract import ContractError, SchemaRegistry, strict_json_loads

from .errors import BridgeError
from .events import EventBuffer
from .scene_queue import (
    B2_READ_ONLY_PROFILE,
    RequestSnapshot,
    SceneQueue,
    SceneQueueError,
)
from .session import BridgeSession


MAX_REQUEST_BYTES = 1024 * 1024
MAX_SCENE_REQUEST_BYTES = 262_144
MAX_SCENE_POLL_MS = 1_000
MAX_MCP_HEALTH_RESPONSE_BYTES = 65_536
SCENE_EXECUTOR_HEADER = "X-HIA-Executor-Token"
HIA_MCP_V2_BACKEND = "hia_v2"
FXHOUDINI_MCP_BACKEND = "fxhoudini"
HIA_MCP_V2_HEALTH_ROUTE = "/hia-mcp-v2/v1/health"
HIA_MCP_V2_WIRE_PROTOCOL = "hia-mcp-v2/1"
_SCENE_CAPABILITY_PATH = "/v1/scene/capabilities"
_SCENE_STATUS_PATH = "/v1/scene/status"
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
        scene_executor_token: str | None = None,
        houdini_mcp_port: int | None = None,
        houdini_mcp_token: str | None = None,
        houdini_mcp_backend: str = FXHOUDINI_MCP_BACKEND,
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
        if scene_executor_token is not None and (
            not isinstance(scene_executor_token, str)
            or len(scene_executor_token) < 32
            or "\r" in scene_executor_token
            or "\n" in scene_executor_token
        ):
            raise ValueError("Scene executor token must contain at least 32 safe characters")
        if (
            scene_executor_token is not None
            and hmac.compare_digest(scene_executor_token, token)
        ):
            raise ValueError("Scene executor token must be independent from the Bridge token")
        if (
            self.scene_queue is not None
            and self.scene_queue.profile == B2_READ_ONLY_PROFILE
            and scene_executor_token is None
        ):
            raise ValueError("B2 read-only scene queue requires an independent executor token")
        self._expected_scene_executor_token = scene_executor_token
        if (houdini_mcp_port is None) != (houdini_mcp_token is None):
            raise ValueError("Houdini MCP port and token must be configured together")
        if houdini_mcp_port is not None and (
            isinstance(houdini_mcp_port, bool)
            or not isinstance(houdini_mcp_port, int)
            or not 1 <= houdini_mcp_port <= 65_535
        ):
            raise ValueError("Houdini MCP port is invalid")
        if houdini_mcp_token is not None and (
            len(houdini_mcp_token) < 32
            or "\r" in houdini_mcp_token
            or "\n" in houdini_mcp_token
        ):
            raise ValueError("Houdini MCP token is invalid")
        if houdini_mcp_backend not in {
            HIA_MCP_V2_BACKEND,
            FXHOUDINI_MCP_BACKEND,
        }:
            raise ValueError("Houdini MCP backend is invalid")
        self._houdini_mcp_port = houdini_mcp_port
        self._houdini_mcp_token = houdini_mcp_token
        self._houdini_mcp_backend = houdini_mcp_backend

    def authorized(self, value: str | None) -> bool:
        return value is not None and hmac.compare_digest(
            value,
            self._expected_authorization,
        )

    def scene_executor_authorized(self, value: str | None) -> bool:
        expected = self._expected_scene_executor_token
        if expected is None:
            return self.scene_queue is None or self.scene_queue.profile != B2_READ_ONLY_PROFILE
        return value is not None and hmac.compare_digest(value, expected)

    def requires_scene_executor_authorization(
        self,
        http_method: str,
        path: str,
    ) -> bool:
        if self.scene_queue is None or self.scene_queue.profile != B2_READ_ONLY_PROFILE:
            return False
        if http_method == "POST" and path == _SCENE_CAPABILITY_PATH:
            return True
        if http_method == "GET" and path == "/v1/scene/requests/next":
            return True
        return http_method == "POST" and _SCENE_RESULT_PATH.fullmatch(path) is not None

    def houdini_mcp_status(self) -> dict[str, Any]:
        backend = self._houdini_mcp_backend
        if backend == HIA_MCP_V2_BACKEND:
            server_id = "hia_mcp_v2"
            display_name = "HIA MCP V2"
        else:
            server_id = "houdini_intelligence"
            display_name = "FXHoudiniMCP 1.3.0"
        status: dict[str, Any] = {
            "backend": backend,
            "server_id": server_id,
            "display_name": display_name,
            "available": False,
        }
        if backend == HIA_MCP_V2_BACKEND:
            status["scene_revision"] = None
        port = self._houdini_mcp_port
        token = self._houdini_mcp_token
        if port is None or token is None:
            return status
        if backend == HIA_MCP_V2_BACKEND:
            request = urllib_request.Request(
                f"http://127.0.0.1:{port}{HIA_MCP_V2_HEALTH_ROUTE}",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            try:
                with urllib_request.urlopen(request, timeout=0.75) as response:
                    raw = response.read(MAX_MCP_HEALTH_RESPONSE_BYTES + 1)
                if len(raw) > MAX_MCP_HEALTH_RESPONSE_BYTES:
                    return status
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return status
            result = payload.get("result") if isinstance(payload, dict) else None
            status["available"] = (
                isinstance(payload, dict)
                and set(payload) == {"protocol", "ok", "result"}
                and payload.get("protocol") == HIA_MCP_V2_WIRE_PROTOCOL
                and payload.get("ok") is True
                and isinstance(result, dict)
                and set(result) == {"server_id", "scene_revision"}
                and result.get("server_id") == server_id
                and isinstance(result.get("scene_revision"), int)
                and not isinstance(result.get("scene_revision"), bool)
                and result["scene_revision"] >= 0
            )
            if status["available"]:
                status["scene_revision"] = result["scene_revision"]
            return status

        body = urllib_parse.urlencode(
            {"json": json.dumps(["mcp.health", [], {}])}
        ).encode("utf-8")
        request = urllib_request.Request(
            f"http://127.0.0.1:{port}/api",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=0.75) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return status
        status["available"] = (
            isinstance(payload, dict) and payload.get("status") == "ok"
        )
        return status


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
            if self.server.application.requires_scene_executor_authorization(
                http_method,
                parsed.path,
            ) and not self.server.application.scene_executor_authorized(
                self.headers.get(SCENE_EXECUTOR_HEADER)
            ):
                raise BridgeError(
                    "SCENE_EXECUTOR_UNAUTHORIZED",
                    "A valid independent scene executor credential is required",
                    http_status=HTTPStatus.FORBIDDEN,
                )
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
                "houdini_mcp": application.houdini_mcp_status(),
            }, HTTPStatus.OK
        if path == "/v1/session":
            return {"ok": True, "session": application.session.snapshot()}, HTTPStatus.OK
        if path == "/v1/models":
            result = application.session.list_models()
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/threads":
            result = application.session.list_threads()
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/goal":
            values = parse_qs(query, keep_blank_values=True)
            thread_ids = values.get("thread_id", [])
            if set(values) != {"thread_id"} or len(thread_ids) != 1:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "Goal get requires exactly one thread_id query field",
                    HTTPStatus.BAD_REQUEST,
                )
            result = application.session.get_goal(thread_ids[0])
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/events":
            values = parse_qs(query, keep_blank_values=False)
            after = int(values.get("after", ["0"])[0])
            timeout = float(values.get("timeout", ["15"])[0])
            polled = application.events.poll(after, timeout=timeout)
            return {"ok": True, **polled}, HTTPStatus.OK
        if path == _SCENE_STATUS_PATH:
            queue, _ = self._scene_components()
            return {
                "ok": True,
                "scene": queue.live_capability_status(),
            }, HTTPStatus.OK
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
        if path == _SCENE_CAPABILITY_PATH:
            self._require_exact_fields(body, {"report"})
            report = body.get("report")
            if not isinstance(report, dict):
                raise BridgeError(
                    "INVALID_REQUEST",
                    "Capability report must be a JSON object",
                    HTTPStatus.BAD_REQUEST,
                )
            queue, _ = self._scene_components()
            attestation = queue.publish_live_capability(report)
            return {
                "ok": True,
                "available": attestation is not None,
                "attestation_digest": (
                    None if attestation is None else attestation.digest
                ),
                "catalog_digest": (
                    None if attestation is None else attestation.catalog_digest
                ),
                "observer_sequence": report["observer_sequence"],
                "lease_duration_ms": int(
                    queue.live_capability_lease_seconds * 1000
                ),
            }, HTTPStatus.OK
        if path == "/v1/session":
            action = body.get("action")
            if action == "start":
                result = application.session.start_thread(
                    model=body.get("model"),
                    service_tier=body.get("service_tier"),
                )
            elif action == "resume":
                result = application.session.resume_thread(
                    thread_id=body.get("thread_id"),
                    service_tier=body.get("service_tier"),
                )
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
                text=body.get("text"),
                model=body.get("model"),
                effort=body.get("effort"),
                local_image_paths=body.get("local_image_paths"),
                service_tier=body.get("service_tier"),
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/threads/name":
            self._require_exact_fields(body, {"thread_id", "name"})
            result = application.session.rename_thread(
                body.get("thread_id"), body.get("name")
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/goal":
            action = body.get("action")
            if action == "clear":
                if set(body) != {"action", "thread_id"}:
                    raise BridgeError(
                        "INVALID_REQUEST",
                        "Goal clear requires action and thread_id",
                        HTTPStatus.BAD_REQUEST,
                    )
                result = application.session.clear_goal(body["thread_id"])
            elif action == "set":
                expected = {
                    "action",
                    "thread_id",
                    "objective",
                    "status",
                    "token_budget",
                }
                if set(body) != expected:
                    raise BridgeError(
                        "INVALID_REQUEST",
                        "Goal set requires objective, status, and token_budget",
                        HTTPStatus.BAD_REQUEST,
                    )
                result = application.session.set_goal(
                    expected_thread_id=body["thread_id"],
                    objective=body["objective"],
                    status=body["status"],
                    token_budget=body["token_budget"],
                )
            else:
                raise BridgeError(
                    "INVALID_GOAL_ACTION",
                    "Goal action must be set or clear",
                    HTTPStatus.BAD_REQUEST,
                )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/focus":
            self._require_exact_fields(body, {"thread_id", "enabled"})
            result = application.session.set_focus_mode(
                body["thread_id"],
                body["enabled"],
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/steer":
            result = application.session.steer_turn(
                body.get("text"),
                body.get("local_image_paths"),
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
            if (
                application.scene_queue is not None
                and application.scene_queue.profile == B2_READ_ONLY_PROFILE
            ):
                raise SceneQueueError(
                    "TOOL_NOT_ALLOWED",
                    403,
                    "Scene approvals are disabled in the B2 read-only profile",
                )
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
                "The scene gateway is not enabled for this Bridge instance",
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
        if (
            queue.profile == B2_READ_ONLY_PROFILE
            and not queue.tool_enabled(tool_name)
        ):
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Tool is outside the active P2-V capability profile",
                {"tool_name": tool_name, "profile": queue.profile},
            )
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
