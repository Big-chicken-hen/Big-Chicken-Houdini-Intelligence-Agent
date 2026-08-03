"""Authenticated loopback-only HTTP facade for the Houdini Panel."""

from __future__ import annotations

import hmac
import json
import re
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlsplit

from hia_core.houdini_contract import ContractError, SchemaRegistry, strict_json_loads

from .errors import BridgeError
from .events import EventBuffer
from .knowledge_cli import KnowledgeCliError, KnowledgeCliRunner
from .project_contracts import Requirement
from .project_guidance import RequirementDelta
from .scene_queue import (
    B2_READ_ONLY_PROFILE,
    RequestSnapshot,
    SceneQueue,
    SceneQueueError,
)
from .session import BridgeSession
from .project_service import (
    ProjectGuidanceUnavailable,
    ProjectGuidanceRecordError,
    ProjectRuntimeSelectionError,
    ProjectTeamService,
)


def _parse_requirement_delta(value: Any) -> RequirementDelta | None:
    """Parse explicit user scope edits without interpreting guidance prose."""

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"add", "supersede", "remove"}:
        raise BridgeError("INVALID_REQUEST", "Invalid requirement_delta fields")
    raw_add = value.get("add", [])
    raw_supersede = value.get("supersede", {})
    raw_remove = value.get("remove", [])
    if not isinstance(raw_add, list) or not isinstance(raw_supersede, dict):
        raise BridgeError("INVALID_REQUEST", "Invalid requirement_delta collection")
    if not isinstance(raw_remove, list) or not all(
        isinstance(item, str) and item for item in raw_remove
    ):
        raise BridgeError("INVALID_REQUEST", "Invalid removed requirement IDs")
    additions = []
    for item in raw_add:
        if not isinstance(item, dict) or set(item) - {
            "requirement_id",
            "kind",
            "source_ref",
        }:
            raise BridgeError("INVALID_REQUEST", "Invalid added requirement")
        requirement_id = item.get("requirement_id")
        kind = item.get("kind")
        source_ref = item.get("source_ref", "")
        if not all(isinstance(field, str) for field in (requirement_id, kind, source_ref)):
            raise BridgeError("INVALID_REQUEST", "Invalid added requirement values")
        if not requirement_id or not kind:
            raise BridgeError("INVALID_REQUEST", "Added requirement needs ID and kind")
        additions.append(Requirement(requirement_id, kind, source_ref=source_ref))
    if not all(
        isinstance(old_id, str)
        and old_id
        and isinstance(new_id, str)
        and new_id
        for old_id, new_id in raw_supersede.items()
    ):
        raise BridgeError("INVALID_REQUEST", "Invalid supersession IDs")
    return RequirementDelta(
        add=tuple(additions),
        supersede=dict(raw_supersede),
        remove=tuple(raw_remove),
    )


MAX_REQUEST_BYTES = 1024 * 1024
MAX_SCENE_REQUEST_BYTES = 262_144
MAX_SCENE_POLL_MS = 1_000
SCENE_EXECUTOR_HEADER = "X-HIA-Executor-Token"
HIA_MCP_V2_BACKEND = "hia_v2"
FXHOUDINI_MCP_BACKEND = "fxhoudini"
_PROJECT_MEMORY_TOOL = "hia_project_memory"
_PROJECT_MEMORY_ACTIONS = frozenset(
    {"record", "search", "list", "delete", "supersede"}
)
_PROJECT_MEMORY_ID = re.compile(r"^mem_[0-9a-f]{32}$")
_PROJECT_MEMORY_TIMEOUT_SECONDS = 60.0
_RUNTIME_IDENTITY_ERRORS = frozenset(
    {
        "HOUDINI_SESSION_CHANGED",
        "HOUDINI_SESSION_MISMATCH",
        "HOUDINI_RUNTIME_SOURCE_CHANGED",
        "STALE_HOUDINI_RUNTIME",
    }
)
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


def _bounded_project_memory_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\x00", " ").split())
    return text[:limit]


def _project_memory_item(
    value: Any,
    *,
    search_result: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeError(
            "INVALID_PROJECT_MEMORY_RESPONSE",
            "A project-memory item was malformed",
            HTTPStatus.BAD_GATEWAY,
        )
    metadata = value.get("metadata") if search_result else value
    if not isinstance(metadata, dict):
        raise BridgeError(
            "INVALID_PROJECT_MEMORY_RESPONSE",
            "Project-memory metadata was malformed",
            HTTPStatus.BAD_GATEWAY,
        )
    memory_id = metadata.get("memory_id") if search_result else value.get("id")
    if not isinstance(memory_id, str) or _PROJECT_MEMORY_ID.fullmatch(memory_id) is None:
        raise BridgeError(
            "INVALID_PROJECT_MEMORY_RESPONSE",
            "A project-memory item had an invalid stable ID",
            HTTPStatus.BAD_GATEWAY,
        )
    raw_tags = metadata.get("tags") if search_result else value.get("tags")
    tags = (
        [
            text
            for text in (
                _bounded_project_memory_text(tag, 128)
                for tag in raw_tags[:32]
            )
            if text
        ]
        if isinstance(raw_tags, list)
        else []
    )
    status = _bounded_project_memory_text(
        metadata.get("status") if search_result else value.get("status"),
        32,
    )
    superseded_by = _bounded_project_memory_text(
        (
            metadata.get("superseded_by")
            if search_result
            else value.get("superseded_by")
        ),
        36,
    )
    if (
        superseded_by
        and _PROJECT_MEMORY_ID.fullmatch(superseded_by) is None
    ):
        superseded_by = ""
    projected = {
        "id": memory_id,
        "memory_type": _bounded_project_memory_text(
            (
                metadata.get("memory_type")
                if search_result
                else value.get("memory_type")
            ),
            32,
        ),
        "title": _bounded_project_memory_text(value.get("title"), 512),
        "summary": _bounded_project_memory_text(
            value.get("snippet") if search_result else value.get("body"),
            800,
        ),
        "tags": tags,
        "scope": _bounded_project_memory_text(
            metadata.get("scope") if search_result else value.get("scope"),
            256,
        ),
        "status": status or "active",
        "superseded_by": superseded_by,
        "created_at": _bounded_project_memory_text(
            (
                metadata.get("created_at")
                if search_result
                else value.get("created_at")
            ),
            64,
        ),
        "updated_at": _bounded_project_memory_text(
            (
                metadata.get("updated_at")
                if search_result
                else value.get("updated_at")
            ),
            64,
        ),
    }
    for field in ("source_thread_id", "source_turn_id"):
        if field in metadata:
            projected[field] = _bounded_project_memory_text(
                metadata.get(field),
                256,
            )
    return projected


def _project_memory_projection(
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if action in {"list", "search"}:
        field = "items" if action == "list" else "matches"
        values = payload.get(field)
        total = payload.get("total")
        if (
            not isinstance(values, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
        ):
            raise BridgeError(
                "INVALID_PROJECT_MEMORY_RESPONSE",
                "The project-memory collection result was malformed",
                HTTPStatus.BAD_GATEWAY,
            )
        return {
            "action": action,
            "memories": [
                _project_memory_item(
                    value,
                    search_result=action == "search",
                )
                for value in values
            ],
            "total": total,
        }
    if action == "record":
        return {
            "action": action,
            "memory": _project_memory_item(
                payload.get("memory"),
                search_result=False,
            ),
        }
    if action == "supersede":
        return {
            "action": action,
            "superseded": _project_memory_item(
                payload.get("superseded"),
                search_result=False,
            ),
            "replacement": _project_memory_item(
                payload.get("replacement"),
                search_result=False,
            ),
        }
    memory_id = payload.get("memory_id")
    if (
        action != "delete"
        or not isinstance(memory_id, str)
        or _PROJECT_MEMORY_ID.fullmatch(memory_id) is None
        or payload.get("deleted") is not True
    ):
        raise BridgeError(
            "INVALID_PROJECT_MEMORY_RESPONSE",
            "The project-memory delete result was malformed",
            HTTPStatus.BAD_GATEWAY,
        )
    return {"action": action, "memory_id": memory_id, "deleted": True}


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
        houdini_launcher_session_id: str | None = None,
        houdini_executor_path: Path | None = None,
        knowledge_cli: KnowledgeCliRunner | None = None,
        project_team: ProjectTeamService | None = None,
        project_team_factory: Callable[[], ProjectTeamService] | None = None,
    ) -> None:
        if len(token) < 32:
            raise ValueError("Bearer token must contain at least 32 characters")
        self.session = session
        self.events = events
        self.project_team = project_team
        self._project_team_factory = project_team_factory
        self._project_team_lock = threading.Lock()
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
        self._hia_transport: Any | None = None
        self._hia_transport_error: type[Exception] = Exception
        self._hia_cancellation_type: Any | None = None
        if (
            houdini_mcp_backend == HIA_MCP_V2_BACKEND
            and houdini_mcp_port is not None
            and houdini_mcp_token is not None
        ):
            if (
                not isinstance(houdini_launcher_session_id, str)
                or re.fullmatch(
                    r"[0-9A-Fa-f]{32}",
                    houdini_launcher_session_id,
                )
                is None
                or not isinstance(houdini_executor_path, Path)
                or not houdini_executor_path.is_absolute()
            ):
                raise ValueError(
                    "HIA MCP V2 requires a launcher session and executor path"
                )
            try:
                from hia_mcp_v2.errors import TransportError
                from hia_mcp_v2.transport import (
                    CancellationToken,
                    LoopbackTransport,
                    TransportConfig,
                )
            except ImportError as exc:
                raise ValueError(
                    "The HIA MCP V2 transport contract is unavailable"
                ) from exc
            self._hia_transport = LoopbackTransport(
                TransportConfig(
                    host="127.0.0.1",
                    port=houdini_mcp_port,
                    token=houdini_mcp_token,
                    launcher_session_id=houdini_launcher_session_id,
                    executor_module_path=str(houdini_executor_path),
                    timeout_seconds=_PROJECT_MEMORY_TIMEOUT_SECONDS,
                )
            )
            self._hia_transport_error = TransportError
            self._hia_cancellation_type = CancellationToken
        self._knowledge_cli = knowledge_cli or KnowledgeCliRunner()

    def require_project_team(self) -> ProjectTeamService:
        with self._project_team_lock:
            if self.project_team is not None:
                return self.project_team
            if self._project_team_factory is None:
                raise BridgeError(
                    "PROJECT_TEAM_UNAVAILABLE",
                    "Project mode is unavailable; ordinary chat remains available",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                service = self._project_team_factory()
            except BridgeError:
                raise
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise BridgeError(
                    "PROJECT_REGISTRY_CORRUPTED",
                    "Project registry exists but cannot be read",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc
            self.project_team = service
            return service

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
            status["runtime_identity"] = None
            status["identity_status"] = "unavailable"
            status["restart_required"] = False
        port = self._houdini_mcp_port
        token = self._houdini_mcp_token
        if port is None or token is None:
            return status
        if backend == HIA_MCP_V2_BACKEND:
            transport = self._hia_transport
            if transport is None:
                return status
            try:
                identity = dict(transport.health(timeout_seconds=0.75))
            except self._hia_transport_error as exc:
                code = str(getattr(exc, "code", "HOUDINI_UNAVAILABLE"))
                raw_details = getattr(exc, "details", None)
                details = (
                    dict(raw_details)
                    if isinstance(raw_details, dict)
                    else {}
                )
                status["identity_status"] = (
                    "stale_or_changed"
                    if code in _RUNTIME_IDENTITY_ERRORS
                    else "unavailable"
                )
                status["identity_error_code"] = code
                status["restart_required"] = code in _RUNTIME_IDENTITY_ERRORS
                runtime_identity = details.get("runtime_identity")
                if isinstance(runtime_identity, dict):
                    status["runtime_identity"] = runtime_identity
                    status["scene_revision"] = runtime_identity.get(
                        "scene_revision"
                    )
                return status
            status["available"] = True
            status["scene_revision"] = identity["scene_revision"]
            status["runtime_identity"] = identity
            status["identity_status"] = "verified"
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

    def project_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward one explicit memory action to the existing HIA V2 tool."""

        action = arguments.get("action")
        if not isinstance(action, str) or action not in _PROJECT_MEMORY_ACTIONS:
            raise BridgeError(
                "INVALID_PROJECT_MEMORY_ACTION",
                "Project memory action must be record, search, list, delete, or supersede",
                HTTPStatus.BAD_REQUEST,
            )
        if (
            self._houdini_mcp_backend != HIA_MCP_V2_BACKEND
            or self._houdini_mcp_port is None
            or self._houdini_mcp_token is None
        ):
            raise BridgeError(
                "PROJECT_MEMORY_UNAVAILABLE",
                "Project memory requires the live HIA MCP V2 runtime",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        try:
            from hia_mcp_v2.errors import InputError
            from hia_mcp_v2.tools import validate_input
        except ImportError as exc:
            raise BridgeError(
                "PROJECT_MEMORY_UNAVAILABLE",
                "The HIA MCP V2 project-memory contract is unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc
        try:
            validate_input(_PROJECT_MEMORY_TOOL, arguments)
        except InputError as exc:
            raise BridgeError(
                exc.code,
                exc.message,
                HTTPStatus.BAD_REQUEST,
                dict(exc.details) if exc.details is not None else None,
            ) from exc

        transport = self._hia_transport
        cancellation_type = self._hia_cancellation_type
        if transport is None or cancellation_type is None:
            raise BridgeError(
                "PROJECT_MEMORY_UNAVAILABLE",
                "Project memory requires the verified live HIA MCP V2 runtime",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        try:
            payload = transport.call(
                _PROJECT_MEMORY_TOOL,
                arguments,
                request_id=f"bridge-memory-{uuid.uuid4().hex}",
                cancellation=cancellation_type(),
            )
        except self._hia_transport_error as exc:
            code = str(getattr(exc, "code", "PROJECT_MEMORY_UNAVAILABLE"))
            message = str(
                getattr(
                    exc,
                    "message",
                    "The live HIA MCP V2 project-memory tool is unavailable",
                )
            )
            raw_details = getattr(exc, "details", None)
            details = dict(raw_details) if isinstance(raw_details, dict) else {}
            status = (
                HTTPStatus.BAD_REQUEST
                if details.get("http_status") == HTTPStatus.BAD_REQUEST
                else HTTPStatus.CONFLICT
                if code in _RUNTIME_IDENTITY_ERRORS
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            raise BridgeError(code, message, status, details) from exc
        if not isinstance(payload, dict):
            raise BridgeError(
                "INVALID_PROJECT_MEMORY_RESPONSE",
                "The project-memory runtime response was malformed",
                HTTPStatus.BAD_GATEWAY,
            )
        runtime_result = payload.get("result")
        if not isinstance(runtime_result, dict):
            raise BridgeError(
                "INVALID_PROJECT_MEMORY_RESPONSE",
                "The project-memory tool result was malformed",
                HTTPStatus.BAD_GATEWAY,
            )
        return _project_memory_projection(action, runtime_result)

    def project_knowledge(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one fixed project-local knowledge CLI action."""

        try:
            return self._knowledge_cli.handle(
                arguments,
                thread_reader=self.session.read_thread,
            )
        except KnowledgeCliError as exc:
            raise BridgeError(
                exc.code,
                exc.message,
                HTTPStatus(exc.http_status),
                exc.details,
            ) from exc

    def close(self) -> None:
        if self._hia_transport is not None:
            self._hia_transport.close()
        self._knowledge_cli.close()


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

    def server_close(self) -> None:
        try:
            self.application.close()
        finally:
            super().server_close()


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
        if path == "/v1/project-team":
            project_team = application.require_project_team()
            return {
                "ok": True,
                "project_team": project_team.snapshot(),
            }, HTTPStatus.OK
        project_thread_prefix = "/v1/project-team/threads/"
        if path.startswith(project_thread_prefix):
            thread_id = urllib_parse.unquote(path[len(project_thread_prefix) :])
            if not thread_id or "/" in thread_id or "\\" in thread_id:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "Project role read requires one encoded thread id",
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                result = application.require_project_team().read_role_thread(thread_id)
            except KeyError as exc:
                raise BridgeError(
                    "PROJECT_ROLE_NOT_FOUND",
                    "Project role Thread was not found",
                    HTTPStatus.NOT_FOUND,
                ) from exc
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
            allowed = {
                "text",
                "model",
                "effort",
                "local_image_paths",
                "service_tier",
            }
            if set(body) - allowed:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "Ordinary Turn contains unsupported fields",
                    HTTPStatus.BAD_REQUEST,
                )
            result = application.session.start_turn(
                text=body.get("text", ""),
                model=body.get("model"),
                effort=body.get("effort"),
                local_image_paths=body.get("local_image_paths"),
                service_tier=body.get("service_tier"),
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/project-team/start":
            allowed = {
                "text",
                "model",
                "effort",
                "service_tier",
                "local_image_paths",
                "attachment_draft_id",
            }
            if set(body) - allowed:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "Project start contains unsupported fields",
                    HTTPStatus.BAD_REQUEST,
                )
            result = application.require_project_team().start_team_project(
                task_text=body.get("text", ""),
                model=body.get("model"),
                effort=body.get("effort"),
                service_tier=body.get("service_tier"),
                local_image_paths=body.get("local_image_paths"),
                attachment_draft_id=body.get("attachment_draft_id"),
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/project-team":
            self._require_exact_fields(body, {"mode"})
            return {
                "ok": True,
                "project_team": application.require_project_team().set_mode(
                    body.get("mode")
                ),
            }, HTTPStatus.OK
        if path == "/v1/project-team/actions":
            project_team = application.require_project_team()
            action = body.get("action")
            if action == "append_guidance":
                allowed = {
                    "action",
                    "project_id",
                    "thread_id",
                    "text",
                    "requirement_delta",
                }
                if set(body) - allowed:
                    raise BridgeError("INVALID_REQUEST", "Unexpected guidance fields")
                try:
                    snapshot = project_team.append_guidance(
                        project_id=body.get("project_id"),
                        thread_id=body.get("thread_id"),
                        text=body.get("text"),
                        requirement_delta=_parse_requirement_delta(
                            body.get("requirement_delta")
                        ),
                    )
                except ProjectGuidanceUnavailable as exc:
                    raise BridgeError(
                        exc.code,
                        str(exc),
                        HTTPStatus.CONFLICT,
                        {
                            "project_id": exc.project_id,
                            "status": exc.status,
                            "recoverable": exc.recoverable,
                            "next_action": (
                                "continue" if exc.recoverable else "new_project"
                            ),
                        },
                    ) from exc
                except ProjectGuidanceRecordError as exc:
                    raise BridgeError(
                        exc.code,
                        str(exc),
                        HTTPStatus.CONFLICT,
                        {"recoverable": True, "next_action": "retry_guidance"},
                    ) from exc
            elif action == "set_role_runtime":
                expected = {
                    "action",
                    "project_id",
                    "thread_id",
                    "model",
                    "effort",
                    "service_tier",
                }
                self._require_exact_fields(body, expected)
                try:
                    snapshot = project_team.set_role_runtime(
                        project_id=body.get("project_id"),
                        thread_id=body.get("thread_id"),
                        model=body.get("model"),
                        effort=body.get("effort"),
                        service_tier=body.get("service_tier"),
                    )
                except ProjectRuntimeSelectionError as exc:
                    raise BridgeError(
                        exc.code,
                        str(exc),
                        HTTPStatus.BAD_REQUEST,
                        {
                            "field": exc.field,
                            "model": exc.model,
                            "allowed": exc.allowed,
                            "next_action": "refresh_models",
                        },
                    ) from exc
            elif action in {"continue", "stop"}:
                self._require_exact_fields(body, {"action", "project_id"})
                if action == "continue":
                    snapshot = project_team.continue_project(
                        project_id=body.get("project_id")
                    )
                elif action == "stop":
                    snapshot = project_team.stop_project(
                        project_id=body.get("project_id")
                    )
            else:
                raise BridgeError(
                    "INVALID_PROJECT_ACTION",
                    "Project action must be append_guidance, set_role_runtime, continue, or stop",
                )
            return {"ok": True, "project_team": snapshot}, HTTPStatus.OK
        if path == "/v1/project-memory":
            result = application.project_memory(body)
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/knowledge":
            result = application.project_knowledge(body)
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/threads/name":
            self._require_exact_fields(body, {"thread_id", "name"})
            result = application.session.rename_thread(
                body.get("thread_id"), body.get("name")
            )
            return {"ok": True, **result}, HTTPStatus.OK
        if path == "/v1/threads/delete":
            self._require_exact_fields(body, {"thread_id"})
            result = application.session.delete_thread(body.get("thread_id"))
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
