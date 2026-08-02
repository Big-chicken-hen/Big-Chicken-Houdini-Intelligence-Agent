"""Thin app-server Thread registry and relay for HIA Panel projects.

Codex authors every brief, stage card, review, and repair decision. The Bridge
only creates and associates Threads, applies role permissions, relays bounded
handoffs, and waits for app-server completion notifications.
"""

from __future__ import annotations

import copy
import json
import hashlib
import os
import re
import unicodedata
from difflib import SequenceMatcher
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .errors import BridgeError
from .project_thread_contract import (
    EXECUTION_SCHEMA,
    PLANNING_SCHEMA,
    PROJECT_READ_ONLY_ROLES,
    PROJECT_ROLE_ORDER,
    REVIEW_SCHEMA,
    ROLE_ALIASES,
    ROLE_RESPONSIBILITIES,
    ROLE_TITLES,
    SUPERVISOR_BLOCKED_SCHEMA,
    SUPERVISOR_DECISION_SCHEMA,
    SUPERVISOR_PLAN_SCHEMA,
    SUPERVISOR_SCHEMA,
    blocked_prompt,
    decision_prompt,
    handoff,
    plan_authorization_prompt,
    repair_prompt,
    review_prompt,
    role_instructions,
    stage_prompt,
    project_role_config,
)


PROJECT_TEAM_SCHEMA = "hia-project-team/1"
PROJECT_TEAM_SETTINGS_SCHEMA = "hia-project-team-settings/2"
PROJECT_TEAM_REGISTRY_SCHEMA = "hia-project-thread-registry/1"
PROJECT_TEAM_MODES = frozenset({"single", "team"})
PROJECT_TEAM_OVERRIDES = PROJECT_TEAM_MODES
PROJECT_THREAD_SOURCE_PREFIX = "hia-project/"
_RUNNING = "running"
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_HISTORY_RECONCILE_TIMEOUT_SECONDS = 10.0
_PRE_ACK_EVENT_COUNT = 128
_PRE_ACK_EVENT_BYTES = 2_097_152
_PRE_ACK_SINGLE_EVENT_BYTES = 1_048_576
_SUPPRESSED_ROOT_TURN_LIMIT = 256
_PROJECT_REGISTRY_MAX_BYTES = 16 * 1_048_576
_PROJECT_ID_MAX_CHARS = 128
_THREAD_ID_MAX_CHARS = 256
_MODEL_MAX_CHARS = 256
_STATUS_MAX_CHARS = 64
_MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE = 16
_RESTART_INTERRUPTED_STAGE = "Bridge 重启，项目未自动续跑"
_RESTART_INTERRUPTED_ERROR = "The Bridge restarted while this project was running"
_SUPERVISOR_USER_TASK_MARKER = "\n\nUSER TASK:\n"


def copy_collaboration(values: Any) -> list[dict[str, Any]]:
    """Project the bounded native collaboration evidence without prompts or text."""

    result: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, Mapping):
            continue
        item_id, kind, status = raw.get("item_id"), raw.get("type"), raw.get("status")
        if (
            not isinstance(item_id, str)
            or kind not in {"collabAgentToolCall", "subAgentActivity"}
            or not isinstance(status, str)
        ):
            continue
        children = raw.get("child_thread_ids")
        result.append(
            {
                "item_id": item_id[:256],
                "type": kind,
                "status": status[:64],
                "child_thread_ids": [
                    value[:256]
                    for value in children
                    if isinstance(value, str) and value
                ][:8]
                if isinstance(children, list)
                else [],
            }
        )
    return result[-32:]


_EVIDENCE_CREDENTIAL_KEYS = (
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "api_key",
    "apikey",
    "cookie",
)
_EVIDENCE_BINARY_KEYS = (
    "base64",
    "binary",
    "bytes",
    "blob",
)
_TOOL_PROJECTION_BYTES = 32_768
_TURN_TOOL_EVIDENCE_BYTES = 131_072
_EVIDENCE_PRIORITY_KEYS = (
    "ok",
    "path",
    "scope",
    "frame",
    "time",
    "error",
    "warning",
    "measurement",
    "observation",
    "validation",
    "created_or_changed_paths",
    "result",
    "code",
    "status",
)


def _evidence_key_priority(key: Any) -> int:
    normalized = str(key).casefold()
    for index, marker in enumerate(_EVIDENCE_PRIORITY_KEYS):
        if marker in normalized:
            return index
    return len(_EVIDENCE_PRIORITY_KEYS)


def _take_utf8(value: str, limit: int) -> tuple[str, int, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, len(encoded), False
    clipped = encoded[: max(0, limit)].decode("utf-8", errors="ignore")
    return clipped, len(clipped.encode("utf-8")), True


def _redact_credential_values(value: str) -> tuple[str, bool]:
    redacted = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
        "Bearer <redacted>",
        value,
    )
    redacted = re.sub(
        r"(?i)([\"']?[A-Za-z0-9_]*(?:api[_-]?key|token|secret|password|authorization)"
        r"[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+",
        r"\1<redacted>",
        redacted,
    )
    return redacted, redacted != value


def project_tool_evidence(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
    nodes: list[int] | None = None,
    context: str = "result",
) -> tuple[Any, bool]:
    """Return a bounded, redacted projection suitable for a reviewer handoff."""

    if budget is None:
        budget = [_TOOL_PROJECTION_BYTES]
    if nodes is None:
        nodes = [1_024]
    if budget[0] <= 0 or nodes[0] <= 0:
        return "<truncated-budget>", True
    nodes[0] -= 1
    if depth > 5:
        return "<truncated-depth>", True
    if value is None or isinstance(value, (bool, int, float)):
        budget[0] -= min(budget[0], len(str(value).encode("utf-8")))
        return value, False
    if isinstance(value, str):
        clean, value_redacted = _redact_credential_values(
            value.replace("\x00", "")
        )
        rendered, used, clipped = _take_utf8(clean, min(2_048, budget[0]))
        budget[0] -= used
        return rendered, clipped or value_redacted
    if isinstance(value, (bytes, bytearray, memoryview)):
        marker = "<binary-omitted>"
        budget[0] -= min(budget[0], len(marker))
        return marker, True
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        truncated = len(value) > 48
        ordered = sorted(value.items(), key=lambda item: _evidence_key_priority(item[0]))
        for key, child in ordered[:48]:
            if budget[0] <= 0:
                truncated = True
                break
            rendered_key, used, key_clipped = _take_utf8(str(key), min(128, budget[0]))
            budget[0] -= used
            truncated = truncated or key_clipped
            normalized_key = rendered_key.casefold()
            if any(marker in normalized_key for marker in _EVIDENCE_CREDENTIAL_KEYS):
                projected[rendered_key] = "<redacted>"
                truncated = True
                continue
            if any(marker in normalized_key for marker in _EVIDENCE_BINARY_KEYS):
                projected[rendered_key] = "<binary-omitted>"
                truncated = True
                continue
            if (
                context == "arguments"
                and isinstance(child, str)
                and len(child.encode("utf-8", errors="replace")) > 512
                and any(marker in normalized_key for marker in ("script", "prompt", "code"))
            ):
                projected[rendered_key] = f"<omitted-large-input:{len(child)} chars>"
                truncated = True
                continue
            projected_child, child_truncated = project_tool_evidence(
                child,
                depth=depth + 1,
                budget=budget,
                nodes=nodes,
                context=context,
            )
            projected[rendered_key] = projected_child
            truncated = truncated or child_truncated
        return projected, truncated
    if isinstance(value, (list, tuple)):
        projected = []
        truncated = len(value) > 48
        for child in list(value)[:48]:
            if budget[0] <= 0:
                truncated = True
                break
            projected_child, child_truncated = project_tool_evidence(
                child,
                depth=depth + 1,
                budget=budget,
                nodes=nodes,
                context=context,
            )
            projected.append(projected_child)
            truncated = truncated or child_truncated
        return projected, truncated
    rendered, used, _clipped = _take_utf8(str(value), min(512, budget[0]))
    budget[0] -= used
    return rendered, True


class ProjectTeamSettings:
    """Persist only the default single/team routing choice."""

    def __init__(self, path: Path | None = None) -> None:
        self._path, self._lock = path, threading.RLock()
        self._mode = "single"
        self._state_status = "memory" if path is None else "missing"
        if path is None or not path.exists():
            return
        try:
            if not path.is_file() or path.stat().st_size > 4_096:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            schema, mode = value.get("schema"), value.get("mode")
            if schema == PROJECT_TEAM_SETTINGS_SCHEMA and mode in PROJECT_TEAM_MODES:
                self._mode = mode
            elif schema == "hia-project-team-settings/1" and mode in {"off", "suggest", "auto"}:
                self._mode = "team" if mode == "auto" else "single"
            else:
                raise ValueError
            self._state_status = "ready"
        except (AttributeError, OSError, UnicodeError, ValueError, TypeError):
            self._state_status = "invalid"

    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: Any) -> dict[str, Any]:
        if mode not in PROJECT_TEAM_MODES:
            raise BridgeError(
                "INVALID_PROJECT_TEAM_MODE",
                "Project team mode must be single or team",
                details={"field": "mode"},
            )
        with self._lock:
            payload = {"schema": PROJECT_TEAM_SETTINGS_SCHEMA, "mode": mode}
            if self._path is not None:
                encoded = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ) + "\n"
                temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_text(encoded, encoding="utf-8")
                    os.replace(temporary, self._path)
                except OSError as exc:
                    raise BridgeError(
                        "PROJECT_TEAM_SETTINGS_UNAVAILABLE",
                        "Project team mode could not be persisted",
                        http_status=503,
                    ) from exc
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._mode = mode
            self._state_status = "memory" if self._path is None else "ready"
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": PROJECT_TEAM_SCHEMA,
                "revision": 0,
                "state_status": self._state_status,
                "settings": {"mode": self._mode, "writable": True},
                "projects": [],
            }


class ProjectThreadClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


class ProjectEventPublisher(Protocol):
    def publish(self, event_type: str, **fields: Any) -> dict[str, Any]: ...


@dataclass
class _PendingTurn:
    project_id: str
    role: str
    thread_id: str
    visible: bool = False
    guidance: bool = False
    turn_id: str | None = None
    status: str = "starting"
    completed: bool = False
    error: str | None = None
    messages: list[tuple[str, str]] = field(default_factory=list)
    collaboration: list[dict[str, Any]] = field(default_factory=list)
    tool_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_evidence_bytes: int = 0
    tools_truncated: bool = False
    guidance_revision: int = 0
    input_image_paths: tuple[str, ...] = ()
    input_images_ready: bool = False
    request_inflight: bool = False
    pre_ack_events: list[dict[str, Any]] = field(default_factory=list)
    pre_ack_event_bytes: int = 0
    pre_ack_events_truncated: bool = False
    created_monotonic: float = field(default_factory=time.monotonic)

    def add_pre_ack_event(self, event: Mapping[str, Any]) -> None:
        try:
            copied = copy.deepcopy(dict(event))
            encoded_size = len(
                json.dumps(copied, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
        except (TypeError, ValueError):
            self.pre_ack_events_truncated = True
            return
        if (
            encoded_size > _PRE_ACK_SINGLE_EVENT_BYTES
            or len(self.pre_ack_events) >= _PRE_ACK_EVENT_COUNT
            or self.pre_ack_event_bytes + encoded_size > _PRE_ACK_EVENT_BYTES
        ):
            self.pre_ack_events_truncated = True
            return
        self.pre_ack_events.append(copied)
        self.pre_ack_event_bytes += encoded_size

    def take_pre_ack_events(self) -> list[dict[str, Any]]:
        values = self.pre_ack_events
        self.pre_ack_events = []
        self.pre_ack_event_bytes = 0
        return values

    def add_message(self, item: Mapping[str, Any]) -> None:
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        key = item.get("id")
        key = key if isinstance(key, str) and key else uuid.uuid4().hex
        for index, (existing, _old_text) in enumerate(self.messages):
            if existing == key:
                self.messages[index] = (key, text)
                return
        self.messages.append((key, text))

    def final_text(self) -> str | None:
        return next((text for _key, text in reversed(self.messages) if text.strip()), None)

    def add_collaboration(self, item: Mapping[str, Any], method: str) -> None:
        item_id = item.get("id")
        kind = item.get("type")
        if not isinstance(item_id, str) or kind not in {
            "collabAgentToolCall",
            "subAgentActivity",
        }:
            return
        child_ids = (
            item.get("receiverThreadIds")
            if kind == "collabAgentToolCall"
            else [item.get("agentThreadId")]
        )
        child_ids = child_ids if isinstance(child_ids, list) else []
        child_ids = [
            value[:256]
            for value in child_ids
            if isinstance(value, str) and value
        ][:8]
        status = item.get("status") or item.get("kind") or (
            "started" if method == "item/started" else "completed"
        )
        record = {
            "item_id": item_id[:256],
            "type": kind,
            "status": str(status)[:64],
            "child_thread_ids": child_ids,
        }
        for index, existing in enumerate(self.collaboration):
            if existing["item_id"] == record["item_id"]:
                self.collaboration[index] = record
                return
        self.collaboration.append(record)
        del self.collaboration[:-32]

    def add_tool_evidence(self, item: Mapping[str, Any]) -> None:
        item_id, tool = item.get("id"), item.get("tool")
        status = item.get("status")
        if (
            not isinstance(item_id, str)
            or not item_id
            or not isinstance(tool, str)
            or not tool.startswith("hia_")
            or not isinstance(status, str)
        ):
            return
        result = item.get("result")
        structured = result.get("structuredContent") if isinstance(result, Mapping) else None
        if not isinstance(structured, Mapping):
            failed = status.casefold() in {
                "failed",
                "error",
                "cancelled",
                "canceled",
            } or item.get("error") is not None
            if not failed:
                return
            structured = {
                "ok": False,
                "status": status,
                "error": (
                    item.get("error")
                    if item.get("error") is not None
                    else result
                ),
            }
        record: dict[str, Any] = {
            "item_id": item_id[:256],
            "tool": tool[:128],
            "status": status[:64],
            "ok": structured.get("ok") is True,
        }
        remaining = _TURN_TOOL_EVIDENCE_BYTES - self.tool_evidence_bytes
        if remaining < 512:
            self.tools_truncated = True
            return
        item_budget = [min(_TOOL_PROJECTION_BYTES, remaining)]
        projected_arguments, arguments_truncated = project_tool_evidence(
            item.get("arguments"), budget=item_budget, context="arguments"
        )
        projected_result, result_truncated = project_tool_evidence(
            structured, budget=item_budget, context="result"
        )
        record["projection"] = {
            "arguments": projected_arguments,
            "structured_result": projected_result,
            "truncated": arguments_truncated or result_truncated,
        }
        if tool == "hia_capture_viewport":
            capture = structured.get("result")
            if isinstance(capture, Mapping):
                capture_record: dict[str, Any] = {
                    key: capture.get(key)
                    for key in (
                        "absolute_path",
                        "storage_scope",
                        "source_hip_path",
                        "actual_frame",
                        "requested_frame",
                    )
                }
                sequence = capture.get("sequence")
                endpoints = sequence.get("endpoints") if isinstance(sequence, Mapping) else None
                if isinstance(endpoints, list):
                    projected_endpoints: list[dict[str, Any]] = []
                    seen_endpoint_names: set[str] = set()
                    for endpoint in endpoints[:2]:
                        if not isinstance(endpoint, Mapping):
                            continue
                        name, endpoint_status = endpoint.get("endpoint"), endpoint.get("status")
                        if (
                            name not in {"first", "last"}
                            or name in seen_endpoint_names
                            or not isinstance(endpoint_status, str)
                        ):
                            continue
                        seen_endpoint_names.add(name)
                        value = {
                            key: endpoint.get(key)
                            for key in (
                                "endpoint",
                                "requested_frame",
                                "status",
                                "actual_frame",
                                "cook_frame",
                                "quality_status",
                                "evidence_path",
                                "error",
                            )
                        }
                        projected_endpoints.append(value)
                    capture_record["endpoints"] = projected_endpoints
                record["capture"] = capture_record
        encoded_size = len(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        previous = self.tool_evidence.get(item_id)
        previous_size = (
            len(
                json.dumps(previous, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if previous is not None
            else 0
        )
        if self.tool_evidence_bytes - previous_size + encoded_size > _TURN_TOOL_EVIDENCE_BYTES:
            self.tools_truncated = True
            return
        self.tool_evidence[item_id] = record
        self.tool_evidence_bytes = self.tool_evidence_bytes - previous_size + encoded_size
        while len(self.tool_evidence) > 64:
            removed = self.tool_evidence.pop(next(iter(self.tool_evidence)))
            self.tool_evidence_bytes -= len(
                json.dumps(removed, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            self.tools_truncated = True


class ProjectThreadCoordinator:
    """Associate five role Threads and relay Codex-authored stage cards."""

    def __init__(
        self,
        project_root: Path,
        client: ProjectThreadClient,
        events: ProjectEventPublisher,
        *,
        state_path: Path | None = None,
        turn_timeout_seconds: float = 1_800.0,
    ) -> None:
        if turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be positive")
        self._project_root, self._client, self._events = project_root, client, events
        self._state_path, self._turn_timeout = state_path, float(turn_timeout_seconds)
        self._condition = threading.Condition(threading.RLock())
        self._terminal_lock = threading.Lock()
        self._projects: list[dict[str, Any]] = []
        self._thread_roles: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, _PendingTurn] = {}
        self._stopped_projects: set[str] = set()
        self._transferring_threads: set[str] = set()
        self._suppressed_root_turns: dict[tuple[str, str], None] = {}
        self._guidance_inflight: set[str] = set()
        self._recovering_projects: set[str] = set()
        self._event_replay: Callable[[dict[str, Any]], None] | None = None
        self._restart_reconcile_projects: set[str] = set()
        self._registry_write_failed = False
        self._registry_write_error: str | None = None
        self._revision = 0
        self._state_status = "memory" if state_path is None else "missing"
        self._load_registry()

    def note_settings_change(self) -> None:
        with self._condition:
            if self._state_status == "invalid":
                # An unreadable registry may represent an active native Goal whose
                # ownership cannot be reconstructed. A settings POST must not erase
                # that evidence by replacing the file with an empty registry.
                return
            self._revision += 1
            self._try_write_registry()

    def set_event_replay(
        self, callback: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Replay request-race notifications through the normal Session sink."""

        with self._condition:
            self._event_replay = callback

    def begin_root_turn_request(self, project_id: str) -> None:
        """Mark the exact initial Supervisor request as awaiting its ACK identity."""

        with self._condition:
            project = self._require_project(project_id)
            if project.get("status") != _RUNNING:
                raise BridgeError(
                    "PROJECT_THREAD_INTERRUPTED",
                    "The project stopped before its root Turn could start",
                    http_status=409,
                )
            pending = self._pending.get(project["root_thread_id"])
            if pending is None or pending.project_id != project_id or pending.completed:
                raise BridgeError(
                    "PROJECT_ROOT_TURN_UNAVAILABLE",
                    "The project root Turn is not registered",
                    http_status=502,
                )
            if pending.turn_id is not None:
                raise BridgeError(
                    "PROJECT_ROOT_TURN_MISMATCH",
                    "The project root Turn already has an acknowledged identity",
                    http_status=502,
                )
            pending.request_inflight = True

    def recover_root_turn_request(self, project_id: str) -> str | None:
        """Adopt one pre-ACK candidate only after thread/read confirms its id."""

        with self._condition:
            project = self._project(project_id)
            if project is None or project.get("status") != _RUNNING:
                return None
            thread_id = project["root_thread_id"]
            pending = self._pending.get(thread_id)
            if pending is None or pending.turn_id is not None:
                return pending.turn_id if pending is not None else None
            candidates = {
                turn_id
                for event in pending.pre_ack_events
                if (turn_id := self._event_turn_id(event)) is not None
            }
        if len(candidates) != 1:
            return None
        response = self._client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": True}
        )
        thread = response.get("thread") if isinstance(response, Mapping) else None
        turns = thread.get("turns") if isinstance(thread, Mapping) else None
        confirmed = {
            item.get("id")
            for item in (turns if isinstance(turns, list) else [])
            if isinstance(item, Mapping)
            if isinstance(item.get("id"), str)
        }
        matched = candidates & confirmed
        if len(matched) != 1:
            return None
        turn_id = next(iter(matched))
        self.attach_root_turn(project_id, turn_id)
        return turn_id

    @staticmethod
    def _event_turn_id(event: Mapping[str, Any]) -> str | None:
        params = event.get("params")
        params = params if isinstance(params, Mapping) else {}
        turn = params.get("turn")
        value = turn.get("id") if isinstance(turn, Mapping) else params.get("turnId")
        return value if isinstance(value, str) and value else None

    def _suppress_root_turn_locked(self, thread_id: str, turn_id: str) -> None:
        key = (thread_id, turn_id)
        self._suppressed_root_turns.pop(key, None)
        self._suppressed_root_turns[key] = None
        while len(self._suppressed_root_turns) > _SUPPRESSED_ROOT_TURN_LIMIT:
            self._suppressed_root_turns.pop(next(iter(self._suppressed_root_turns)))

    def _replay_pre_ack_events(
        self,
        pending: _PendingTurn,
        acknowledged: str | None,
        events: list[dict[str, Any]],
    ) -> None:
        """Replay only an ACK-owned Turn and suppress every competing root Turn."""

        if not events:
            return
        mismatched: dict[str, bool] = {}
        with self._condition:
            for event in events:
                turn_id = self._event_turn_id(event)
                if turn_id is None or turn_id == acknowledged:
                    continue
                self._suppress_root_turn_locked(pending.thread_id, turn_id)
                if turn_id not in mismatched:
                    mismatched[turn_id] = False
                if event.get("method") == "turn/completed":
                    mismatched[turn_id] = True
            callback = self._event_replay
        dispatch = callback or self.handle_client_event
        for event in events:
            turn_id = self._event_turn_id(event)
            if turn_id is None:
                continue
            if acknowledged is not None and turn_id not in {acknowledged, *mismatched}:
                continue
            dispatch(event)
        for turn_id, already_terminal in mismatched.items():
            if already_terminal:
                continue
            threading.Thread(
                target=self._interrupt_unowned_root_turn,
                args=(pending.project_id, pending.thread_id, turn_id),
                name=f"hia-project-root-interrupt-{turn_id[-8:]}",
                daemon=True,
            ).start()

    def prepare_project(
        self,
        root_thread_id: Any,
        task: Any,
        *,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        root_id = self._text(root_thread_id, "thread_id", 256)
        task = self._text(task, "text", 65_536)
        model = self._optional_text(model, "model", 256)
        effort = self._optional_text(effort, "effort", 64)
        tier = self._optional_text(service_tier, "service_tier", 128)
        self._ensure_registry_boundary(None, "new project creation")
        with self._condition:
            if self.workflow_active():
                raise BridgeError(
                    "PROJECT_THREAD_WORKFLOW_ACTIVE",
                    "A project is already using the scene writer",
                    http_status=409,
                )
            if root_id in self._thread_roles:
                raise BridgeError(
                    "PROJECT_THREAD_ALREADY_ASSIGNED",
                    "The selected Thread already belongs to a project",
                    http_status=409,
                )
            if len(self._projects) >= 256:
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_FULL",
                    "The bounded project registry is full",
                    http_status=409,
                )
            previous_revision = self._revision
            project_id = f"project-{uuid.uuid4().hex}"
            title = self._safe_title(task.splitlines()[0], "Houdini 项目")[:120]
            project = {
                "project_id": project_id,
                "title": title,
                "status": _RUNNING,
                "stage": "制定蓝图与阶段卡",
                "progress": self._progress(0, 0, "正在制定蓝图与阶段卡"),
                "updated_at": time.time(),
                "root_thread_id": root_id,
                "threads": {},
                "_task": task,
                "_images": tuple(local_image_paths),
                "_effort": effort,
                "_service_tier": tier,
                "_user_guidance": [],
                "_guidance_revision": 0,
                "_guidance_consumed_revision": 0,
                "_role_guidance_revisions": {},
            }
            self._projects.append(project)
            self._record_thread(project, "supervisor", root_id, f"{title}｜监督 AI", model)
            self._pending[root_id] = _PendingTurn(
                project_id,
                "supervisor",
                root_id,
                visible=True,
                input_image_paths=tuple(dict.fromkeys(local_image_paths)),
                input_images_ready=True,
            )
            if not self._touch(project):
                self._pending.pop(root_id, None)
                self._thread_roles.pop(root_id, None)
                self._projects.remove(project)
                self._revision = previous_revision
                self._events.publish(
                    "project_team_updated",
                    project_id=project_id,
                    status=None,
                    stage=None,
                    state_status="write_failed",
                )
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "The new project was not created because its ownership record "
                    "could not be persisted",
                    http_status=503,
                )
        try:
            self._client.request(
                "thread/name/set", {"threadId": root_id, "name": f"{title}｜监督 AI"}
            )
            worker_roles = tuple(PROJECT_ROLE_ORDER[1:])
            with ThreadPoolExecutor(max_workers=len(worker_roles)) as pool:
                starts = [
                    pool.submit(self._create_thread, project_id, role)
                    for role in worker_roles
                ]
                for started in starts:
                    started.result()
        except Exception as exc:
            self.fail_project_start(project_id, exc)
            raise
        return {"project_id": project_id, "output_schema": SUPERVISOR_SCHEMA}

    def attach_root_turn(self, project_id: str, turn_id: str) -> None:
        start_driver = False
        buffered: list[dict[str, Any]] = []
        replay_pending: _PendingTurn | None = None
        with self._condition:
            project = self._require_project(project_id)
            pending = self._pending.get(project["root_thread_id"])
            attached = project.get("_root_turn_id")
            if attached is not None:
                if attached != turn_id:
                    raise BridgeError(
                        "PROJECT_ROOT_TURN_MISMATCH",
                        "Codex observed a different project root Turn",
                        http_status=502,
                    )
                if pending is not None:
                    if pending.project_id != project_id or pending.turn_id not in {
                        None,
                        turn_id,
                    }:
                        raise BridgeError(
                            "PROJECT_ROOT_TURN_MISMATCH",
                            "Codex acknowledged a different project root Turn",
                            http_status=502,
                        )
                    pending.turn_id = turn_id
                    pending.request_inflight = False
                    buffered = pending.take_pre_ack_events()
                    replay_pending = pending
                self._condition.notify_all()
            else:
                if pending is None or pending.project_id != project_id:
                    raise BridgeError(
                        "PROJECT_ROOT_TURN_UNAVAILABLE",
                        "The project root Turn is not registered",
                        http_status=502,
                    )
                if pending.turn_id not in {None, turn_id}:
                    raise BridgeError(
                        "PROJECT_ROOT_TURN_MISMATCH",
                        "Codex acknowledged a different project root Turn",
                        http_status=502,
                    )
                pending.turn_id = turn_id
                pending.request_inflight = False
                buffered = pending.take_pre_ack_events()
                replay_pending = pending
                previous_revision = self._revision
                previous_updated_at = project.get("updated_at")
                supervisor_record = self._thread(project, "supervisor")
                previous_status = supervisor_record.get("status")
                previous_base_sent = supervisor_record.get("_base_images_sent")
                project["_root_turn_id"] = turn_id
                supervisor_record["status"] = "running"
                supervisor_record["_base_images_sent"] = True
                if not self._touch(project):
                    project.pop("_root_turn_id", None)
                    supervisor_record["status"] = previous_status
                    if previous_base_sent is None:
                        supervisor_record.pop("_base_images_sent", None)
                    else:
                        supervisor_record["_base_images_sent"] = previous_base_sent
                    project["updated_at"] = previous_updated_at
                    self._revision = previous_revision
                    raise BridgeError(
                        "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                        "The acknowledged root Turn was not attached because its "
                        "ownership record could not be persisted",
                        http_status=503,
                    )
                self._condition.notify_all()
                start_driver = True
        if replay_pending is not None:
            self._replay_pre_ack_events(replay_pending, turn_id, buffered)
        # Activating a native Goal before the acknowledged intake Turn lets the
        # app-server auto-start a Goal continuation that can win root ownership.
        # Attach the exact user Turn first, then keep the Goal active throughout
        # the coordinated project as required.
        if not self._activate_goal_if_running(project_id):
            return
        if start_driver:
            threading.Thread(
                target=self._drive_project,
                args=(project_id,),
                name=f"hia-project-{project_id[-8:]}",
                daemon=True,
            ).start()

    def fail_project_start(self, project_id: str, error: Any) -> None:
        self._project_terminal_transition(
            project_id,
            goal_status="paused",
            status="failed",
            stage="项目启动失败",
            error=self._error(error),
        )

    @staticmethod
    def _restart_recoverable(project: Mapping[str, Any]) -> bool:
        """Return true only for the terminal produced by restart reconciliation."""

        return bool(
            project.get("status") == "interrupted"
            and project.get("stage") == _RESTART_INTERRUPTED_STAGE
        )

    def _restart_thread_history(
        self,
        project_id: str,
    ) -> tuple[str, tuple[str, ...], str, str, str]:
        """Read the original task and completed intake from the five native Threads."""

        with self._condition:
            project = self._require_project(project_id)
            records = project.get("threads")
            if not isinstance(records, Mapping) or any(
                role not in records for role in PROJECT_ROLE_ORDER
            ):
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_INCOMPLETE",
                    "The interrupted project no longer has all five role Threads",
                    http_status=409,
                )
            role_threads = {
                role: self._thread(project, role)["thread_id"]
                for role in PROJECT_ROLE_ORDER
            }

        histories: dict[str, Mapping[str, Any]] = {}
        for role, thread_id in role_threads.items():
            params = {"threadId": thread_id, "includeTurns": True}
            request_with_timeout = getattr(
                self._client, "request_with_timeout", None
            )
            if callable(request_with_timeout):
                response = request_with_timeout(
                    "thread/read",
                    params,
                    timeout_seconds=min(
                        _HISTORY_RECONCILE_TIMEOUT_SECONDS,
                        self._turn_timeout,
                    ),
                )
            else:
                response = self._client.request("thread/read", params)
            thread = response.get("thread") if isinstance(response, Mapping) else None
            if not isinstance(thread, Mapping) or thread.get("id") != thread_id:
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_INCOMPLETE",
                    f"The native {role} Thread could not be verified",
                    http_status=409,
                )
            turns = thread.get("turns")
            if not isinstance(turns, list):
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_INCOMPLETE",
                    f"The native {role} Thread history is unavailable",
                    http_status=409,
                )
            status = thread.get("status")
            status_type = status.get("type") if isinstance(status, Mapping) else None
            if status_type == "active" or any(
                isinstance(turn, Mapping) and turn.get("status") == "inProgress"
                for turn in turns
            ):
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_ACTIVE_TURN",
                    f"The native {role} Thread still has an active Turn",
                    http_status=409,
                )
            histories[role] = thread

        root = histories["supervisor"]
        task: str | None = None
        images: list[str] = []
        intake_turn_id = "restart-intake"
        intake_item_id = "restart-intake-message"
        intake_text = "{}"
        for turn in root.get("turns", []):
            if not isinstance(turn, Mapping):
                continue
            turn_id = turn.get("id")
            items = turn.get("items")
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") == "userMessage" and task is None:
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    for entry in content:
                        if not isinstance(entry, Mapping):
                            continue
                        text = entry.get("text")
                        if (
                            entry.get("type") == "text"
                            and isinstance(text, str)
                            and _SUPERVISOR_USER_TASK_MARKER in text
                        ):
                            task = text.split(_SUPERVISOR_USER_TASK_MARKER, 1)[-1]
                            if not task or len(task) > 65_536 or "\x00" in task:
                                task = None
                                break
                            images = [
                                str(candidate.get("path"))
                                for candidate in content
                                if isinstance(candidate, Mapping)
                                and candidate.get("type") == "localImage"
                                and isinstance(candidate.get("path"), str)
                            ]
                            break
                if item.get("type") != "agentMessage":
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    candidate = json.loads(text)
                    if not isinstance(candidate, Mapping):
                        continue
                    self._validated_intake_brief(candidate)
                except (BridgeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                intake_text = text
                if isinstance(turn_id, str) and turn_id:
                    intake_turn_id = turn_id
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    intake_item_id = item_id
                break
            if task is not None and intake_text != "{}":
                break
        if task is None:
            raise BridgeError(
                "PROJECT_THREAD_RECOVERY_HISTORY_INCOMPLETE",
                "The original project task is missing from the native Supervisor history",
                http_status=409,
            )

        if len(images) > _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE:
            raise BridgeError(
                "PROJECT_THREAD_RECOVERY_REFERENCE_LIMIT",
                "The original project intake contains too many reference images",
                http_status=409,
            )
        verified_images: list[str] = []
        attachments_root = (
            self._project_root / ".runtime" / "attachments"
        ).resolve(strict=True)
        for raw_path in dict.fromkeys(images):
            candidate = Path(raw_path)
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(attachments_root)
            except (OSError, RuntimeError, ValueError):
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_REFERENCE_MISSING",
                    "An original project reference image is no longer available "
                    "inside the project runtime",
                    http_status=409,
                )
            if not resolved.is_file() or resolved.suffix.lower() not in _IMAGE_SUFFIXES:
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_REFERENCE_MISSING",
                    "An original project reference image can no longer be verified",
                    http_status=409,
                )
            verified_images.append(str(resolved))
        return (
            task,
            tuple(verified_images),
            intake_turn_id,
            intake_item_id,
            intake_text,
        )

    def _prepare_restart_interrupted_project(
        self, project_id: str
    ) -> dict[str, Any] | None:
        """Restore local ownership, but do not activate Goal or start work yet."""

        self._ensure_registry_boundary(project_id, "restart project recovery")
        with self._condition:
            project = self._require_project(project_id)
            if project.get("status") == _RUNNING:
                return None
            if not self._restart_recoverable(project):
                return None
            if self._recovering_projects or any(
                item.get("status") == _RUNNING
                for item in self._projects
                if item.get("project_id") != project_id
            ):
                raise BridgeError(
                    "PROJECT_THREAD_WORKFLOW_ACTIVE",
                    "Another project is already using the scene writer",
                    http_status=409,
                )
            self._recovering_projects.add(project_id)

        previous: dict[str, Any] | None = None
        previous_pending: _PendingTurn | None = None
        try:
            task, images, turn_id, item_id, intake_text = (
                self._restart_thread_history(project_id)
            )
            with self._terminal_lock:
                with self._condition:
                    project = self._require_project(project_id)
                    if not self._restart_recoverable(project):
                        raise BridgeError(
                            "PROJECT_THREAD_WORKFLOW_INACTIVE",
                            "The project recovery target changed before it could resume",
                            http_status=409,
                        )
                    previous = copy.deepcopy(project)
                    root_id = project["root_thread_id"]
                    previous_pending = self._pending.get(root_id)
                    project.update(
                        status=_RUNNING,
                        stage="Bridge 重启后正在恢复项目",
                        progress=self._progress(0, 0, "正在恢复原项目流程"),
                        _task=task,
                        _images=images,
                        _user_guidance=[],
                        _guidance_revision=0,
                        _guidance_consumed_revision=0,
                        _role_guidance_revisions={},
                    )
                    project.pop("error", None)
                    project.pop("_root_brief_guidance_revision", None)
                    self._stopped_projects.discard(project_id)
                    for role in PROJECT_ROLE_ORDER:
                        self._thread(project, role)["status"] = "pending"
                    self._thread(project, "supervisor")["status"] = "completed"
                    pending = _PendingTurn(
                        project_id,
                        "supervisor",
                        root_id,
                        visible=True,
                        turn_id=turn_id,
                        status="completed",
                        completed=True,
                        input_image_paths=images,
                        input_images_ready=True,
                    )
                    pending.add_message({"id": item_id, "text": intake_text})
                    self._pending[root_id] = pending
                    if not self._touch(project):
                        project.clear()
                        project.update(previous)
                        if previous_pending is None:
                            self._pending.pop(root_id, None)
                        else:
                            self._pending[root_id] = previous_pending
                        self._try_write_registry()
                        raise BridgeError(
                            "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                            "The interrupted project was not resumed because its "
                            "ownership record could not be persisted",
                            http_status=503,
                        )
        finally:
            with self._condition:
                self._recovering_projects.discard(project_id)
                self._condition.notify_all()
        return {
            "previous": previous,
            "previous_pending": previous_pending,
            "task": task,
        }

    def _rollback_restart_recovery(
        self,
        project_id: str,
        recovery: Mapping[str, Any],
    ) -> bool:
        previous = recovery.get("previous")
        if not isinstance(previous, Mapping):
            return False
        with self._condition:
            project = self._require_project(project_id)
            running = copy.deepcopy(project)
            root_id = project["root_thread_id"]
            project.clear()
            project.update(copy.deepcopy(dict(previous)))
            previous_pending = recovery.get("previous_pending")
            if isinstance(previous_pending, _PendingTurn):
                self._pending[root_id] = previous_pending
            else:
                self._pending.pop(root_id, None)
            if self._touch(project):
                return True
            project.clear()
            project.update(running)
            project.update(
                status=_RUNNING,
                stage="项目恢复回滚未能持久化，已保留写入锁",
                error=(
                    "Restart recovery rollback could not be persisted; "
                    "scene-write ownership remains locked."
                ),
            )
            self._pending[root_id] = _PendingTurn(
                project_id,
                "supervisor",
                root_id,
                visible=True,
                status="recoveryLocked",
            )
            self._try_write_registry()
            return False

    def _commit_restart_recovery(
        self,
        project_id: str,
        recovery: Mapping[str, Any],
    ) -> None:
        task = recovery.get("task")
        goal_active = False
        try:
            with self._terminal_lock:
                with self._condition:
                    project = self._require_project(project_id)
                    if project.get("status") != _RUNNING:
                        raise BridgeError(
                            "PROJECT_THREAD_WORKFLOW_INACTIVE",
                            "The project stopped before restart recovery committed",
                            http_status=409,
                        )
                self._set_goal(
                    project_id,
                    "active",
                    fallback_objective=task if isinstance(task, str) else None,
                )
                goal_active = True
            threading.Thread(
                target=self._drive_project,
                args=(project_id,),
                name=f"hia-project-recovery-{project_id[-8:]}",
                daemon=True,
            ).start()
        except Exception as exc:
            pause_error: Exception | None = None
            try:
                with self._terminal_lock:
                    self._set_goal(
                        project_id,
                        "paused",
                        fallback_objective=(
                            task if isinstance(task, str) else None
                        ),
                    )
            except Exception as pause_exc:
                pause_error = pause_exc
            if pause_error is not None:
                with self._condition:
                    project = self._require_project(project_id)
                    project.update(
                        status=_RUNNING,
                        stage="项目恢复失败，原生 Goal 状态未确认，已保留写入锁",
                        error=(
                            "Restart recovery failed and the native Goal could not "
                            f"be confirmed paused: {self._error(pause_error)}"
                        ),
                    )
                    self._touch(project)
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_LOCKED",
                    "Restart recovery failed and native Goal pause is unconfirmed; "
                    "scene-write ownership remains locked",
                    http_status=503,
                ) from exc
            if not self._rollback_restart_recovery(project_id, recovery):
                raise BridgeError(
                    "PROJECT_THREAD_RECOVERY_LOCKED",
                    "Restart recovery rollback could not be persisted; "
                    "scene-write ownership remains locked",
                    http_status=503,
                ) from exc
            raise

    def append_guidance(
        self,
        project_id: Any,
        thread_id: Any,
        text: Any,
        *,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Accept role-targeted guidance into the authoritative workflow.

        A project role is not an independent chat lane while the project is
        running.  In particular, an idle Execution Thread must never be
        started outside the Planning/Supervisor/stage/review coordinator.  The
        selected role is therefore an exact delivery preference: steer only
        its current coordinator-owned Turn, otherwise queue the bounded capsule
        for the next safe workflow Turn.
        """

        project_id = self._text(project_id, "project_id", 128)
        thread_id = self._text(thread_id, "thread_id", 256)
        if not isinstance(text, str):
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                "text must be a string",
                details={"field": "text"},
            )
        if len(text) > 65_536 or "\x00" in text:
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                "text exceeds its bounded length",
                details={"field": "text", "max_chars": 65_536},
            )
        if not text.strip() and not local_image_paths:
            raise BridgeError(
                "EMPTY_INPUT",
                "Natural-language input or at least one image is required",
            )
        with self._condition:
            ownership = self._thread_roles.get(thread_id)
            if ownership is None or ownership[0] != project_id:
                raise BridgeError(
                    "PROJECT_THREAD_NOT_FOUND",
                    "The requested Thread is not in this project",
                    http_status=404,
                )
            project, role = self._require_project(project_id), ownership[1]
            if self._transferring_threads:
                raise BridgeError(
                    "PROJECT_THREAD_TRANSFER_ACTIVE",
                    "This role is transferring after automatic compaction",
                    http_status=409,
                )
        self._acquire_guidance_slot(project_id)
        try:
            accepted = self._accept_workflow_guidance(
                project_id,
                text,
                # Supervisor is the project-wide authoritative composer.  Whether
                # reached before or after snapshot sync, its guidance uses the same
                # active-Execution-first relay as the early Supervisor endpoint.
                target_role=None if role == "supervisor" else role,
                target_thread_id=None if role == "supervisor" else thread_id,
                model=model,
                effort=effort,
                service_tier=service_tier,
                local_image_paths=local_image_paths,
            )
        finally:
            self._release_guidance_slot(project_id)
        return {
            **accepted,
            "guidance_target": {
                "project_id": project_id,
                "thread_id": thread_id,
                "role": role,
            },
        }

    def snapshot(
        self, *, mode: str, writable: bool, settings_state_status: str
    ) -> dict[str, Any]:
        with self._condition:
            statuses = {settings_state_status, self._state_status}
            state = (
                "invalid"
                if "invalid" in statuses
                else "write_failed"
                if "write_failed" in statuses
                else "memory"
                if statuses <= {"memory"}
                else "missing"
                if "missing" in statuses
                else "ready"
            )
            return {
                "schema": PROJECT_TEAM_SCHEMA,
                "revision": self._revision,
                "state_status": state,
                "settings": {"mode": mode, "writable": writable},
                "projects": [self._public(project) for project in self._projects],
            }

    def role_for_thread(self, thread_id: Any) -> str | None:
        with self._condition:
            value = self._thread_roles.get(thread_id) if isinstance(thread_id, str) else None
            return value[1] if value is not None else None

    def project_identity_for_thread(self, thread_id: Any) -> dict[str, str] | None:
        """Return authoritative project ownership for one real role Thread."""

        with self._condition:
            ownership = (
                self._thread_roles.get(thread_id)
                if isinstance(thread_id, str)
                else None
            )
            if ownership is None:
                return None
            project = self._project(ownership[0])
            if project is None:
                return None
            return {
                "project_id": project["project_id"],
                "project_role": ownership[1],
                "thread_id": thread_id,
                "status": project["status"],
            }

    def active_supervisor_target(
        self,
        expected_project_id: Any = None,
    ) -> dict[str, str]:
        """Resolve the one running project's Supervisor without guessing a Turn."""

        expected = self._optional_text(
            expected_project_id,
            "project_id",
            128,
        )
        with self._condition:
            running = [
                project
                for project in self._projects
                if project.get("status") == _RUNNING
            ]
            if not running:
                interrupted = (
                    self._project(expected) if expected is not None else None
                )
                if interrupted is not None and self._restart_recoverable(interrupted):
                    return {
                        "project_id": interrupted["project_id"],
                        "thread_id": interrupted["root_thread_id"],
                        "role": "supervisor",
                    }
                raise BridgeError(
                    "PROJECT_THREAD_WORKFLOW_INACTIVE",
                    "There is no running project to receive Supervisor guidance",
                    http_status=409,
                )
            if len(running) != 1:
                raise BridgeError(
                    "PROJECT_THREAD_TARGET_AMBIGUOUS",
                    "Supervisor guidance requires exactly one running project",
                    http_status=409,
                )
            project = running[0]
            if expected is not None and project["project_id"] != expected:
                raise BridgeError(
                    "PROJECT_THREAD_PROJECT_MISMATCH",
                    "The running project does not match the expected project",
                    http_status=409,
                    details={
                        "expected_project_id": expected,
                        "active_project_id": project["project_id"],
                    },
                )
            return {
                "project_id": project["project_id"],
                "thread_id": project["root_thread_id"],
                "role": "supervisor",
            }

    def accept_supervisor_guidance(
        self,
        project_id: Any,
        text: Any,
        *,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Put one user delta on the workflow and steer one exact active Turn."""

        project_id = self._text(project_id, "project_id", 128)
        self._acquire_guidance_slot(project_id)
        try:
            return self._accept_workflow_guidance(
                project_id,
                text,
                target_role=None,
                target_thread_id=None,
                model=model,
                effort=effort,
                service_tier=service_tier,
                local_image_paths=local_image_paths,
            )
        finally:
            self._release_guidance_slot(project_id)

    def _acquire_guidance_slot(self, project_id: str) -> None:
        """Serialize one project's external steer acknowledgement transaction."""

        with self._condition:
            if project_id in self._guidance_inflight:
                raise BridgeError(
                    "PROJECT_GUIDANCE_ACTIVE",
                    "Another project guidance request is awaiting an exact Turn acknowledgement",
                    http_status=409,
                )
            self._guidance_inflight.add(project_id)

    def _release_guidance_slot(self, project_id: str) -> None:
        with self._condition:
            self._guidance_inflight.discard(project_id)
            self._condition.notify_all()

    def _accept_workflow_guidance(
        self,
        project_id: Any,
        text: Any,
        *,
        target_role: str | None,
        target_thread_id: str | None,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Record one bounded capsule and optionally steer one exact active Turn."""

        project_id = self._text(project_id, "project_id", 128)
        if not isinstance(text, str):
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                "text must be a string",
                details={"field": "text"},
            )
        if len(text) > 65_536 or "\x00" in text:
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                "text exceeds its bounded length",
                details={"field": "text", "max_chars": 65_536},
            )
        if not text.strip() and not local_image_paths:
            raise BridgeError(
                "EMPTY_INPUT",
                "Natural-language input or at least one image is required",
            )
        model = self._optional_text(model, "model", 256)
        effort = self._optional_text(effort, "effort", 64)
        tier = self._optional_text(service_tier, "service_tier", 128)
        # Session validation already proves attachment ownership and path byte
        # bounds.  Keep only one occurrence of each path in this capsule so a
        # repeated attachment cannot consume the role queue more than once.
        local_image_paths = tuple(dict.fromkeys(local_image_paths))
        self._resume_restart_interrupted_project(project_id)
        capsule_id = "guidance-" + uuid.uuid4().hex
        target: tuple[str, str, str] | None = None
        guidance_revision_before = 0
        with self._condition:
            project = self._require_project(project_id)
            if project.get("status") != _RUNNING:
                raise BridgeError(
                    "PROJECT_THREAD_WORKFLOW_INACTIVE",
                    "The project is no longer running",
                    http_status=409,
                )
            if target_role is not None:
                if target_role not in PROJECT_ROLE_ORDER:
                    raise BridgeError(
                        "PROJECT_THREAD_NOT_FOUND",
                        "The requested project role is invalid",
                        http_status=404,
                    )
                target_record = self._thread(project, target_role)
                if target_record.get("thread_id") != target_thread_id:
                    raise BridgeError(
                        "PROJECT_THREAD_NOT_FOUND",
                        "The requested Thread is not the selected project role",
                        http_status=404,
                    )
            else:
                target_record = self._thread(project, "supervisor")
            coordinator_revision_before = self._revision
            project_updated_at_before = project.get("updated_at")
            next_setting_keys = (
                "_next_model",
                "_next_effort",
                "_next_service_tier",
            )
            previous_next_settings = {
                key: (key in target_record, target_record.get(key))
                for key in next_setting_keys
            }
            capsules = project.setdefault("_user_guidance", [])
            queued_bytes = sum(
                len(str(item.get("text", "")).encode("utf-8"))
                for item in capsules
                if isinstance(item, Mapping)
            )
            if queued_bytes + len(text.encode("utf-8")) > 131_072:
                raise BridgeError(
                    "PROJECT_GUIDANCE_QUEUE_FULL",
                    "The bounded project guidance text budget is full",
                    http_status=409,
                    details={"max_utf8_bytes": 131_072},
                )
            candidate_roles = self._guidance_target_roles(target_role)
            pending_images_by_role = {
                role: self._unconsumed_guidance_images_locked(project, role)
                for role in candidate_roles
            }
            for role, pending_images in pending_images_by_role.items():
                # Original project references live in the native Thread history and
                # may be ingested in their own bounded read-only Turn.  Counting them
                # against a later guidance submission incorrectly rejected the first
                # extra reference after an initial sixteen-image task.  Execution is
                # the one role that cannot safely run a reference-only Turn because it
                # owns scene-write capability, so only its still-unconsumed guidance
                # queue remains bounded to one app-server request.
                combined = dict.fromkeys((*pending_images, *local_image_paths))
                if (
                    role == "execution"
                    and len(combined) > _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE
                ):
                    raise BridgeError(
                        "PROJECT_GUIDANCE_IMAGE_QUEUE_FULL",
                        "The Execution role already has too many unconsumed User "
                        "guidance images for its next scene-write Turn",
                        http_status=409,
                        details={
                            "role": role,
                            "max_unique_images": (
                                _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE
                            ),
                        },
                    )
            if not text.strip() and local_image_paths:
                existing_by_role = {
                    role: self._applicable_guidance_image_refs_locked(project, role)
                    for role in candidate_roles
                }
                if all(
                    path in existing_by_role[role]
                    for role in candidate_roles
                    for path in local_image_paths
                ):
                    raise BridgeError(
                        "PROJECT_GUIDANCE_DUPLICATE_IMAGES",
                        "The same image-only guidance is already recorded for every "
                        "selected role",
                        http_status=409,
                    )
            guidance_revision_before = int(project.get("_guidance_revision", 0))
            capsules.append(
                {
                    "guidance_id": capsule_id,
                    "revision": guidance_revision_before + 1,
                    "text": text,
                    "local_image_paths": tuple(local_image_paths),
                    "target_role": target_role,
                    "target_thread_id": target_thread_id,
                    "created_at": time.time(),
                }
            )
            project["_guidance_revision"] = guidance_revision_before + 1
            for key, value in (
                ("_next_model", model),
                ("_next_effort", effort),
                ("_next_service_tier", tier),
            ):
                if value is not None:
                    target_record[key] = value
            for role in candidate_roles:
                record = project["threads"].get(role)
                if not isinstance(record, Mapping):
                    continue
                pending = self._pending.get(record.get("thread_id"))
                role_revisions = project.get("_role_guidance_revisions")
                completed_revision = (
                    int(role_revisions.get(role, 0))
                    if isinstance(role_revisions, Mapping)
                    and isinstance(role_revisions.get(role, 0), int)
                    else 0
                )
                active_covered_revision = max(
                    completed_revision,
                    pending.guidance_revision if pending is not None else 0,
                )
                prior_required_revision = max(
                    (
                        int(item["revision"])
                        for item in capsules
                        if isinstance(item, Mapping)
                        and item.get("guidance_id") != capsule_id
                        and item.get("target_role") in {None, role}
                        and isinstance(item.get("revision"), int)
                    ),
                    default=0,
                )
                if (
                    pending is not None
                    and not pending.completed
                    and isinstance(pending.turn_id, str)
                    # Never steer revision N over an earlier accepted capsule that
                    # this exact role Turn has not received. Otherwise the scalar
                    # completed revision would release an image/text capsule that
                    # was only queued, not delivered.
                    and prior_required_revision <= active_covered_revision
                    and (
                        not local_image_paths
                        or (
                            pending.input_images_ready
                            and len(
                                dict.fromkeys(
                                    (
                                        *pending.input_image_paths,
                                        *local_image_paths,
                                    )
                                )
                            )
                            <= _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE
                        )
                    )
                ):
                    target = (pending.thread_id, pending.turn_id, role)
                    if local_image_paths:
                        pending.input_image_paths = tuple(
                            dict.fromkeys(
                                (*pending.input_image_paths, *local_image_paths)
                            )
                        )
                    break
            if not self._touch(project):
                if capsules and capsules[-1].get("guidance_id") == capsule_id:
                    capsules.pop()
                project["_guidance_revision"] = guidance_revision_before
                for key, (existed, value) in previous_next_settings.items():
                    if existed:
                        target_record[key] = value
                    else:
                        target_record.pop(key, None)
                project["updated_at"] = project_updated_at_before
                self._revision = coordinator_revision_before
                self._events.publish(
                    "project_team_updated",
                    project_id=project_id,
                    status=project.get("status"),
                    stage=project.get("stage"),
                    state_status="write_failed",
                )
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "The guidance was not accepted because its durable project "
                    "record could not be persisted",
                    http_status=503,
                )

        delivery: dict[str, Any] = {
            "mode": "queued",
            "project_id": project_id,
        }
        if target is not None:
            try:
                result = self._client.request(
                    "turn/steer",
                    {
                        "threadId": target[0],
                        "expectedTurnId": target[1],
                        "input": self._guidance_input(text, local_image_paths),
                    },
                )
                acknowledged = (
                    result.get("turnId") if isinstance(result, Mapping) else None
                )
                if acknowledged != target[1]:
                    delivery["delivery_warning"] = {
                        "code": "PROJECT_THREAD_TURN_MISMATCH",
                        "delivery_state": "not_confirmed",
                        "message": (
                            "Immediate delivery was not acknowledged for the exact "
                            "Turn; the accepted guidance remains queued."
                        ),
                    }
                else:
                    with self._condition:
                        pending = self._pending.get(target[0])
                        if (
                            pending is not None
                            and pending.project_id == project_id
                            and pending.turn_id == target[1]
                            and not pending.completed
                        ):
                            pending.guidance_revision = max(
                                pending.guidance_revision,
                                guidance_revision_before + 1,
                            )
                    delivery = {
                        "mode": "steered",
                        "project_id": project_id,
                        "thread_id": target[0],
                        "turn_id": target[1],
                        "role": target[2],
                    }
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or not code.strip():
                    code = type(exc).__name__
                delivery["delivery_warning"] = {
                    "code": code.strip()[:128],
                    "delivery_state": "unknown",
                    "message": (
                        "Immediate delivery could not be confirmed; the accepted "
                        "guidance remains queued for the next safe role Turn."
                    ),
                }
        return {
            "guidance_accepted": True,
            "guidance_id": capsule_id,
            "workflow_delivery": delivery,
        }

    def turn_state_for_thread(self, thread_id: str) -> dict[str, Any]:
        """Expose only the current app-server Turn needed to bind a selected role."""

        with self._condition:
            pending = self._pending.get(thread_id)
            if pending is None or pending.completed:
                return {"turn_active": False, "turn_id": None, "turn_status": None}
            return {
                "turn_active": True,
                "turn_id": pending.turn_id,
                "turn_status": pending.status,
            }

    def transfer_descriptor(self, thread_id: str) -> dict[str, Any] | None:
        with self._condition:
            ownership = self._thread_roles.get(thread_id)
            if ownership is None:
                return None
            project, role = self._require_project(ownership[0]), ownership[1]
            record = self._thread(project, role)
            read_only = role in PROJECT_READ_ONLY_ROLES
            return {
                "thread_id": thread_id,
                "project_id": project["project_id"],
                "role": role,
                "title": record["title"],
                "model": record.get("model"),
                "effort": record.get("_effort", project.get("_effort")),
                "service_tier": record.get(
                    "_service_tier", project.get("_service_tier")
                ),
                "cwd": str(self._project_root),
                "approval_policy": "never" if read_only else "on-request",
                "approvals_reviewer": "user",
                "sandbox": "read-only" if read_only else "workspace-write",
                "developer_instructions": role_instructions(role),
                "config": project_role_config(read_only=read_only),
                "thread_source": self._source(project["project_id"], role),
            }

    def begin_thread_transfer(self, thread_id: str) -> bool:
        with self._condition:
            if (
                self._state_status == "invalid"
                or thread_id not in self._thread_roles
                or self._transferring_threads
            ):
                return False
            pending = self._pending.get(thread_id)
            if pending is not None and not pending.completed:
                return False
            self._transferring_threads.add(thread_id)
            return True

    def thread_transfer_in_progress(self, thread_id: str) -> bool:
        with self._condition:
            return thread_id in self._transferring_threads

    def finish_thread_transfer(self, *thread_ids: str) -> None:
        with self._condition:
            self._transferring_threads.difference_update(thread_ids)
            self._condition.notify_all()

    def replace_thread_id(self, old_thread_id: str, new_thread_id: str) -> dict[str, str]:
        self._ensure_registry_boundary(None, "automatic project Thread migration")
        with self._condition:
            ownership = self._thread_roles.get(old_thread_id)
            if ownership is None or new_thread_id in self._thread_roles:
                raise BridgeError(
                    "PROJECT_THREAD_TRANSFER_CONFLICT",
                    "Project Thread transfer identity is no longer current",
                    http_status=409,
                )
            pending = self._pending.get(old_thread_id)
            if pending is not None and not pending.completed:
                raise BridgeError(
                    "PROJECT_THREAD_TRANSFER_ACTIVE",
                    "A project Thread cannot transfer during an active Turn",
                    http_status=409,
                )
            project, role = self._require_project(ownership[0]), ownership[1]
            record, previous_root = self._thread(project, role), project["root_thread_id"]
            previous_revision = self._revision
            previous_updated_at = project.get("updated_at")
            record["thread_id"] = new_thread_id
            self._thread_roles.pop(old_thread_id)
            self._thread_roles[new_thread_id] = ownership
            if role == "supervisor":
                project["root_thread_id"] = new_thread_id
            try:
                if not self._touch(project):
                    raise BridgeError(
                        "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                        "The replacement Thread identity was not persisted; the old "
                        "Thread must not be deleted",
                        http_status=503,
                    )
            except Exception:
                record["thread_id"] = old_thread_id
                project["root_thread_id"] = previous_root
                self._thread_roles.pop(new_thread_id, None)
                self._thread_roles[old_thread_id] = ownership
                project["updated_at"] = previous_updated_at
                self._revision = previous_revision
                raise
            return {"project_id": project["project_id"], "role": role}

    def workflow_active(self) -> bool:
        with self._condition:
            if self._state_status == "invalid":
                # An unreadable registry may conceal a still-active native Goal.
                # Treat ownership as locked until the file/native Threads are
                # explicitly repaired and reconciled.
                return True
            # A terminal project's already-completed pending record can remain
            # briefly until its waiter unwinds.  Only the authoritative project
            # status owns the scene writer.
            return bool(self._recovering_projects) or any(
                project.get("status") == _RUNNING for project in self._projects
            )

    def _activate_goal_if_running(self, project_id: str) -> bool:
        """Serialize Goal activation against Stop and every terminal close."""

        with self._terminal_lock:
            with self._condition:
                project = self._project(project_id)
                if project is None or project.get("status") != _RUNNING:
                    return False
            self._set_goal(project_id, "active")
            return True

    def reconcile_restarted_goals(self) -> dict[str, list[str]]:
        """Pause native Goals left active by a previous Bridge process."""

        with self._condition:
            project_ids = list(self._restart_reconcile_projects)
        reconciled: list[str] = []
        failed: list[str] = []
        for project_id in project_ids:
            outcome = self._project_terminal_transition(
                project_id,
                goal_status="paused",
                status="interrupted",
                stage=_RESTART_INTERRUPTED_STAGE,
                error=_RESTART_INTERRUPTED_ERROR,
            )
            with self._condition:
                project = self._project(project_id)
                if outcome in {"committed", "stale"} and (
                    project is None or project.get("status") != _RUNNING
                ):
                    self._restart_reconcile_projects.discard(project_id)
                    reconciled.append(project_id)
                else:
                    failed.append(project_id)
        return {"reconciled_project_ids": reconciled, "failed_project_ids": failed}

    def _complete_project_pending_locked(
        self, project_id: str, status: str, error: str | None
    ) -> None:
        project = self._project(project_id)
        for pending in self._pending.values():
            if pending.project_id != project_id or pending.completed:
                continue
            pending.status = status
            pending.error = error
            pending.completed = True
            pending.request_inflight = False
            if project is not None:
                self._thread(project, pending.role)["status"] = status
        self._condition.notify_all()

    def _project_pending_targets_locked(
        self, project_id: str
    ) -> list[tuple[str, str]]:
        targets: list[tuple[str, str]] = []
        for pending in self._pending.values():
            if (
                pending.project_id != project_id
                or pending.completed
                or not isinstance(pending.turn_id, str)
            ):
                continue
            targets.append((pending.thread_id, pending.turn_id))
            if pending.role == "supervisor":
                self._suppress_root_turn_locked(pending.thread_id, pending.turn_id)
        return targets

    def _interrupt_project_targets(
        self, targets: list[tuple[str, str]]
    ) -> list[str]:
        errors: list[str] = []
        for thread_id, turn_id in targets:
            try:
                self._client.request(
                    "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
                )
            except Exception as exc:
                if not self._interrupt_reports_no_active_turn(exc):
                    errors.append(f"{thread_id}/{turn_id}: {self._error(exc)}")
        return errors

    def _project_terminal_transition(
        self,
        project_id: str,
        **fields: Any,
    ) -> str:
        # Goal RPC and the matching local terminal are one serialized ownership
        # decision. This prevents User Stop and an automatic completion from
        # leaving the later RPC state inconsistent with the winning terminal.
        with self._terminal_lock:
            return self._project_terminal_transition_serialized(
                project_id, **fields
            )

    def _project_terminal_transition_serialized(
        self,
        project_id: str,
        *,
        goal_status: str,
        status: str,
        stage: str,
        error: str | None = None,
        progress: Mapping[str, Any] | None = None,
        expected_guidance_revision: int | None = None,
        expected_guidance_revisions: Mapping[str, int] | None = None,
    ) -> str:
        """Close the native Goal before exposing or releasing a local terminal."""

        emergency_cleanup = goal_status == "paused" and status in {
            "interrupted",
            "failed",
        }
        if not emergency_cleanup:
            try:
                self._ensure_registry_boundary(project_id, "terminal cleanup")
            except BridgeError as exc:
                if exc.code == "PROJECT_THREAD_REGISTRY_DEGRADED":
                    return "locked"
                raise
        with self._condition:
            project = self._project(project_id)
            if project is None or project.get("status") != _RUNNING:
                return "stale"
            if self._guidance_guard_changed_locked(
                project,
                expected_guidance_revision=expected_guidance_revision,
                expected_guidance_revisions=expected_guidance_revisions,
            ):
                return "stale"

        desired_error: Exception | None = None
        paused_error: Exception | None = None
        desired_applied = False
        paused_applied = False
        try:
            self._set_goal(project_id, goal_status)
            desired_applied = True
        except Exception as exc:
            desired_error = exc
            try:
                self._set_goal(project_id, "paused")
                paused_applied = True
            except Exception as pause_exc:
                paused_error = pause_exc

        terminal_status, terminal_stage, terminal_error = status, stage, error
        if not desired_applied and paused_applied and status in {"completed", "blocked"}:
            terminal_status = "failed"
            terminal_stage = "目标终态同步失败，已安全暂停"
            terminal_error = (
                f"The requested native Goal state {goal_status} failed, but the Goal "
                f"was safely paused: {self._error(desired_error)}"
            )

        if not desired_applied and not paused_applied:
            with self._condition:
                project = self._project(project_id)
                if project is None or project.get("status") != _RUNNING:
                    return "stale"
                targets = self._project_pending_targets_locked(project_id)
            interrupt_errors = self._interrupt_project_targets(targets)
            message = (
                f"The native Goal could not reach {goal_status}: "
                f"{self._error(desired_error)}; pause compensation also failed: "
                f"{self._error(paused_error)}"
            )
            if interrupt_errors:
                message += "; active Turn interrupt failed: " + "; ".join(
                    interrupt_errors[:8]
                )
            with self._condition:
                project = self._project(project_id)
                if project is not None and project.get("status") == _RUNNING:
                    # Keep status running so scene-write ownership cannot leak
                    # while the native Goal remains unconfirmed.
                    project.update(stage="原生 Goal 终态同步失败", error=message)
                    self._complete_project_pending_locked(project_id, "failed", message)
                    self._touch(project)
            return "locked"

        needs_restore = False
        with self._condition:
            project = self._project(project_id)
            if project is None or project.get("status") != _RUNNING:
                # Another terminal transition (for example User Stop) won.  Its
                # native Goal state is authoritative and must not be reactivated.
                return "stale"
            guard_changed = self._guidance_guard_changed_locked(
                project,
                expected_guidance_revision=expected_guidance_revision,
                expected_guidance_revisions=expected_guidance_revisions,
            )
            if guard_changed:
                needs_restore = desired_applied or paused_applied
            else:
                targets = self._project_pending_targets_locked(project_id)

        if needs_restore:
            try:
                self._set_goal(project_id, "active")
            except Exception as exc:
                with self._condition:
                    project = self._project(project_id)
                    if project is not None and project.get("status") == _RUNNING:
                        message = (
                            "The native Goal reached a stale terminal state and could not "
                            "be restored to active: "
                            + self._error(exc)
                        )
                        project.update(stage="原生 Goal 恢复活动失败", error=message)
                        self._complete_project_pending_locked(
                            project_id, "failed", message
                        )
                        self._touch(project)
                return "locked"
            return "stale"

        interrupt_errors = self._interrupt_project_targets(targets)
        if interrupt_errors:
            message = (
                "The native Goal reached its terminal state, but active project "
                "Turns could not be interrupted: "
                + "; ".join(interrupt_errors[:8])
            )
            with self._condition:
                project = self._project(project_id)
                if project is not None and project.get("status") == _RUNNING:
                    project.update(stage="项目 Turn 终止失败", error=message)
                    self._complete_project_pending_locked(project_id, "failed", message)
                    self._touch(project)
            return "locked"

        restore_after_interrupt = False
        terminal_persistence_failed = False
        with self._condition:
            project = self._project(project_id)
            if project is None or project.get("status") != _RUNNING:
                return "stale"
            if self._guidance_guard_changed_locked(
                project,
                expected_guidance_revision=expected_guidance_revision,
                expected_guidance_revisions=expected_guidance_revisions,
            ):
                restore_after_interrupt = True
            else:
                previous_progress = copy.deepcopy(project.get("progress"))
                had_error = "error" in project
                previous_error = project.get("error")
                project.update(status=terminal_status, stage=terminal_stage)
                if progress is not None:
                    project["progress"] = dict(progress)
                if terminal_error:
                    project["error"] = terminal_error
                else:
                    project.pop("error", None)
                project["updated_at"] = time.time()
                self._revision += 1
                if self._try_write_registry():
                    self._stopped_projects.add(project_id)
                    self._complete_project_pending_locked(
                        project_id, terminal_status, terminal_error
                    )
                    self._events.publish(
                        "project_team_updated",
                        project_id=project_id,
                        status=terminal_status,
                        stage=terminal_stage,
                        state_status=self._state_status,
                    )
                    return "committed"
                project["status"] = _RUNNING
                project["stage"] = "项目注册表写入失败，终态未释放写入锁"
                if previous_progress is not None:
                    project["progress"] = previous_progress
                prior = (
                    str(previous_error).strip() + " "
                    if had_error and str(previous_error).strip()
                    else ""
                )
                project["error"] = (
                    prior
                    + "Terminal persistence failed after native Goal update; "
                    "the in-memory project remains running and locked. "
                    + (self._registry_write_error or "")
                ).strip()
                restore_after_interrupt = True
                terminal_persistence_failed = True
                self._events.publish(
                    "project_team_updated",
                    project_id=project_id,
                    status=_RUNNING,
                    stage=project["stage"],
                    state_status="write_failed",
                )
        if restore_after_interrupt and emergency_cleanup:
            # User Stop, deleted-role cleanup, and emergency failure handling must
            # never reactivate a Goal merely because the local registry terminal
            # could not be persisted. Keep the in-memory ownership lock instead.
            return "locked"
        if restore_after_interrupt:
            try:
                self._set_goal(project_id, "active")
            except Exception as exc:
                with self._condition:
                    project = self._project(project_id)
                    if project is not None and project.get("status") == _RUNNING:
                        message = (
                            "The native Goal could not be restored after late guidance: "
                            + self._error(exc)
                        )
                        project.update(stage="原生 Goal 恢复活动失败", error=message)
                        self._complete_project_pending_locked(
                            project_id, "failed", message
                        )
                        self._touch(project)
                return "locked"
        # ``stale`` is reserved for a real late-guidance race.  Returning it
        # after a terminal registry failure makes the driver misclassify the
        # outage as new guidance and enter another review/terminal loop.
        return "locked" if terminal_persistence_failed else "stale"

    def interrupt_project(self, *, exclude_turn_id: str | None = None) -> dict[str, Any]:
        """Stop every pending Turn in the running project and wake its driver."""

        del exclude_turn_id  # The Goal-first terminal helper owns every exact interrupt.
        targets: list[tuple[str, str]] = []
        with self._condition:
            running = [item for item in self._projects if item.get("status") == _RUNNING]
            running_ids = {item["project_id"] for item in running}
            for _thread_id, pending in list(self._pending.items()):
                if pending.project_id not in running_ids:
                    continue
                if isinstance(pending.turn_id, str):
                    targets.append((pending.thread_id, pending.turn_id))
        committed: list[str] = []
        goal_close_failed: list[str] = []
        for project in running:
            outcome = self._project_terminal_transition(
                project["project_id"],
                goal_status="paused",
                status="interrupted",
                stage="用户已停止项目",
                error="The user stopped the project",
            )
            if outcome == "committed":
                committed.append(project["project_id"])
            elif outcome == "locked":
                goal_close_failed.append(project["project_id"])
        return {
            "interrupted": bool(committed),
            "turn_ids": [turn_id for _thread_id, turn_id in targets],
            "goal_close_failed_project_ids": goal_close_failed,
        }

    def owns_thread_record(self, entry: Mapping[str, Any]) -> bool:
        with self._condition:
            return entry.get("id") in self._thread_roles or self._parse_source(
                entry.get("threadSource")
            ) is not None

    def handle_client_event(
        self,
        event: Mapping[str, Any],
        *,
        selected_thread_id: str | None = None,
    ) -> bool:
        """Track project Turns; consume worker notifications, never approvals."""

        if event.get("type") == "process_exit":
            self._fail_running("Codex app-server exited")
            return False
        if event.get("type") != "codex_notification":
            return False
        method = event.get("method")
        params = event.get("params")
        params = params if isinstance(params, Mapping) else {}
        thread_id = params.get("threadId")
        with self._condition:
            ownership = self._thread_roles.get(thread_id)
            if ownership is None:
                return False
            project, pending = self._project(ownership[0]), self._pending.get(thread_id)
            turn = params.get("turn")
            event_turn_id = turn.get("id") if isinstance(turn, Mapping) else params.get("turnId")
            suppressed_key = (
                (thread_id, event_turn_id)
                if isinstance(thread_id, str) and isinstance(event_turn_id, str)
                else None
            )
            if (
                pending is not None
                and not pending.completed
                and pending.request_inflight
                and pending.turn_id is None
                and suppressed_key is not None
                and method
                in {
                    "turn/started",
                    "item/started",
                    "item/completed",
                    "turn/completed",
                    "error",
                }
            ):
                # app-server notifications can arrive synchronously before the
                # matching turn/start response.  Until that response supplies
                # the authoritative Turn id, none of the candidates owns this
                # project role or the user-visible Session stream.
                pending.add_pre_ack_event(event)
                self._condition.notify_all()
                return True
            if (
                suppressed_key is not None
                and suppressed_key in self._suppressed_root_turns
            ):
                if method == "turn/completed":
                    # Consume the terminal notification before releasing this
                    # bounded suppression identity; it must never leak to Panel.
                    self._suppressed_root_turns.pop(suppressed_key, None)
                self._condition.notify_all()
                return True
            coordinator_owns_start = bool(
                pending is not None
                and not pending.completed
                and (
                    pending.turn_id == event_turn_id
                )
            )
            if (
                method == "turn/started"
                and suppressed_key is not None
                and project is not None
                and ownership[1] == "supervisor"
                and thread_id == project.get("root_thread_id")
                and not coordinator_owns_start
            ):
                # An active native Goal auto-starts a continuation after each
                # Supervisor completion.  The five-role coordinator, not that
                # free-running continuation, owns every subsequent root Turn.
                # Interrupt it off the event-reader thread and consume all of
                # its lifecycle events so it cannot collide with authorization
                # Turns or appear as a second user-visible response.
                self._suppress_root_turn_locked(*suppressed_key)
                threading.Thread(
                    target=self._interrupt_unowned_root_turn,
                    args=(project["project_id"], thread_id, event_turn_id),
                    name=f"hia-project-root-interrupt-{event_turn_id[-8:]}",
                    daemon=True,
                ).start()
                return True
            visible = bool(
                (pending is not None and pending.visible)
                or (
                    project is not None
                    and thread_id == project.get("root_thread_id")
                    and event_turn_id == project.get("_root_turn_id")
                )
            )
            if (
                project is not None
                and project.get("status") == "interrupted"
                and pending is not None
                and pending.completed
            ):
                return not (visible or thread_id == selected_thread_id)
            if method == "turn/started" and pending is not None:
                if isinstance(event_turn_id, str) and pending.turn_id in {None, event_turn_id}:
                    pending.turn_id, pending.status = event_turn_id, "inProgress"
                    if (
                        project is not None
                        and ownership[1] == "supervisor"
                        and thread_id == project.get("root_thread_id")
                        and project.get("_root_turn_id") is None
                    ):
                        previous_revision = self._revision
                        previous_updated_at = project.get("updated_at")
                        supervisor_record = self._thread(project, "supervisor")
                        previous_status = supervisor_record.get("status")
                        previous_base_sent = supervisor_record.get(
                            "_base_images_sent"
                        )
                        project["_root_turn_id"] = event_turn_id
                        supervisor_record["status"] = "running"
                        supervisor_record["_base_images_sent"] = True
                        if self._touch(project):
                            threading.Thread(
                                target=self._drive_project,
                                args=(project["project_id"],),
                                name=f"hia-project-{project['project_id'][-8:]}",
                                daemon=True,
                            ).start()
                        else:
                            project.pop("_root_turn_id", None)
                            supervisor_record["status"] = previous_status
                            if previous_base_sent is None:
                                supervisor_record.pop("_base_images_sent", None)
                            else:
                                supervisor_record[
                                    "_base_images_sent"
                                ] = previous_base_sent
                            project["updated_at"] = previous_updated_at
                            self._revision = previous_revision
                            failure = BridgeError(
                                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                                "The observed root Turn could not be durably attached",
                                http_status=503,
                            )
                            threading.Thread(
                                target=self.fail_project_start,
                                args=(project["project_id"], failure),
                                name=(
                                    "hia-project-root-persist-failed-"
                                    + project["project_id"][-8:]
                                ),
                                daemon=True,
                            ).start()
            elif method in {"item/started", "item/completed"} and pending is not None:
                item = params.get("item")
                if (
                    isinstance(item, Mapping)
                    and pending.turn_id in {None, event_turn_id}
                ):
                    if item.get("type") == "agentMessage" and method == "item/completed":
                        pending.add_message(item)
                    pending.add_collaboration(item, method)
                    if method == "item/completed" and item.get("type") == "mcpToolCall":
                        pending.add_tool_evidence(item)
            elif method == "turn/completed" and pending is not None:
                if isinstance(event_turn_id, str) and pending.turn_id in {None, event_turn_id}:
                    pending.turn_id = event_turn_id
                    pending.status = self._turn_status(turn)
                    items = turn.get("items") if isinstance(turn, Mapping) else None
                    for item in items if isinstance(items, list) else []:
                        if isinstance(item, Mapping):
                            if item.get("type") == "agentMessage":
                                pending.add_message(item)
                            pending.add_collaboration(item, "item/completed")
                            if item.get("type") == "mcpToolCall":
                                pending.add_tool_evidence(item)
                    pending.completed = True
                    if project is not None:
                        self._thread(project, ownership[1])["status"] = pending.status
                        self._touch(project)
                    if pending.guidance:
                        self._pending.pop(thread_id, None)
            elif method == "error" and pending is not None:
                pending.error = self._error(params.get("message"))
            elif (
                method == "thread/deleted"
                and thread_id in self._transferring_threads
            ):
                # Automatic migration owns this exact delete; do not expose it as
                # a user deletion or fail the project before thread_transferred.
                self._condition.notify_all()
                return True
            elif method == "thread/deleted" and project is not None:
                project.update(
                    stage="角色 Thread 已删除，正在暂停原生 Goal",
                    error="A project role Thread was deleted",
                )
                self._touch(project)
                threading.Thread(
                    target=self._fail_deleted_project,
                    args=(project["project_id"],),
                    name=f"hia-project-deleted-{project['project_id'][-8:]}",
                    daemon=True,
                ).start()
            self._condition.notify_all()
            return not (visible or thread_id == selected_thread_id)

    def _fail_deleted_project(self, project_id: str) -> None:
        self._project_terminal_transition(
            project_id,
            goal_status="paused",
            status="failed",
            stage="角色 Thread 已删除",
            error="A project role Thread was deleted",
        )

    def _interrupt_unowned_root_turn(
        self,
        project_id: str,
        thread_id: str,
        turn_id: str,
    ) -> None:
        """Stop one native Goal continuation that bypassed project ownership."""

        try:
            self._client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        except Exception as exc:
            if self._interrupt_reports_no_active_turn(exc):
                # The candidate reached terminal before the interrupt RPC. Its
                # terminal notification remains suppressed and releases the key.
                return
            self._project_terminal_transition(
                project_id,
                goal_status="paused",
                status="failed",
                stage="监督 Turn 所有权冲突",
                error=(
                    "The native Goal continuation could not be interrupted: "
                    + self._error(exc)
                ),
            )

    @staticmethod
    def _interrupt_reports_no_active_turn(error: Exception) -> bool:
        if not isinstance(error, BridgeError) or error.code != "CODEX_RPC_ERROR":
            return False
        details = error.details if isinstance(error.details, Mapping) else {}
        rpc_error = details.get("rpc_error")
        message = rpc_error.get("message") if isinstance(rpc_error, Mapping) else None
        if (
            not isinstance(rpc_error, Mapping)
            or rpc_error.get("code") != -32600
            or not isinstance(message, str)
        ):
            return False
        return " ".join(message.casefold().split()).rstrip(".") in {
            "no active turn to interrupt",
            "turn is already completed",
        }

    def _create_thread(self, project_id: str, role: str) -> None:
        with self._condition:
            project = self._require_project(project_id)
            model = self._thread(project, "supervisor").get("model")
            title = f"{project['title']}｜{ROLE_TITLES[role]}"
            effort, tier = project.get("_effort"), project.get("_service_tier")
        read_only = role in PROJECT_READ_ONLY_ROLES
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": "never" if read_only else "on-request",
            "approvalsReviewer": "user",
            "sandbox": "read-only" if read_only else "workspace-write",
            "ephemeral": False,
            "developerInstructions": role_instructions(role),
            "threadSource": self._source(project_id, role),
        }
        params["config"] = project_role_config(read_only=read_only)
        if model is not None:
            params["model"] = model
        if tier is not None:
            params["serviceTier"] = tier
        result = self._client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, Mapping) else None
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or len(thread_id) > _THREAD_ID_MAX_CHARS
            or thread_id != thread_id.strip()
            or any(ord(character) < 32 for character in thread_id)
        ):
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Codex thread/start response has no bounded valid Thread id",
                http_status=502,
            )
        actual_model = (
            result.get("model", model) if isinstance(result, Mapping) else model
        )
        if actual_model is not None and (
            not isinstance(actual_model, str)
            or not actual_model.strip()
            or len(actual_model) > _MODEL_MAX_CHARS
            or any(ord(character) < 32 for character in actual_model)
        ):
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Codex thread/start response has an invalid model identifier",
                http_status=502,
            )
        # ThreadStartParams has no reasoning-effort override in the pinned
        # app-server protocol.  Preserve the project-selected effort for the
        # role's real turn/start requests instead of replacing it with the
        # new Thread's default effort reported by thread/start.
        actual_effort = effort
        if actual_effort is None:
            actual_effort = (
                result.get("reasoningEffort")
                if isinstance(result, Mapping)
                else None
            )
        actual_tier = (
            result.get("serviceTier", tier) if isinstance(result, Mapping) else tier
        )
        self._client.request("thread/name/set", {"threadId": thread_id, "name": title})
        with self._condition:
            project = self._require_project(project_id)
            self._record_thread(project, role, thread_id, title, actual_model)
            record = self._thread(project, role)
            record["_effort"] = actual_effort
            record["_service_tier"] = actual_tier
            if not self._touch(project):
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    f"The created {role} Thread identity could not be persisted; "
                    "the project will not start",
                    http_status=503,
                )

    def _drive_project(self, project_id: str) -> None:
        planning_pool: ThreadPoolExecutor | None = None
        try:
            with self._condition:
                project = self._require_project(project_id)
                task, images = project["_task"], project["_images"]
            planning_pool = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"hia-project-planning-{project_id[-8:]}",
            )
            planning_future = planning_pool.submit(
                self._run_structured_turn,
                project_id,
                "planning",
                handoff(
                    "Independently classify task_depth from the task itself: direct "
                    "means one known deterministic edit or read; focused means one "
                    "bounded subsystem or multi-node modification needing a plan and "
                    "targeted evidence; full means a complete or complex asset, a "
                    "reference-driven result, substantive material/render/animation/"
                    "simulation work, multiple semantic stages, or a high-consequence "
                    "scene task. The User's project-team choice never forces full. "
                    "Publish separate User facts, "
                    "Reference observations, Codex assumptions, Verified scene facts, "
                    "hard constraints, acceptance, and complete ordered stage cards. "
                    "Every card must contain every contract heading and executable "
                    "task-specific natural-language construction detail, including "
                    "exact paths or network regions, native operation families, "
                    "connections, interacting parameters, expected results, next "
                    "evidence, and minimum repairs. Expand sparse input into a complete "
                    "professional blueprint; never use vague filler or arbitrary nodes "
                    "to imitate complexity. Never let an assumption override a User "
                    "fact, summarize away construction detail, ask for the whole asset "
                    "in one giant HOM batch. Apply the 10000 overall, 2500 per stage, "
                    "three-stage, three-step, 350-per-step, and distinct-evidence "
                    "floors only when your own task_depth is full; direct and focused "
                    "still require complete precise cards and both reviews without "
                    f"padding to Full length.\n\nTASK:\n{task}"
                ),
                PLANNING_SCHEMA,
                images=images,
            )
            try:
                brief = self._wait_root(project_id)
                with self._condition:
                    project = self._require_project(project_id)
                    brief_guidance_revision = int(
                        project.get("_root_brief_guidance_revision", 0)
                    )
            except BridgeError as exc:
                if exc.code not in {
                    "PROJECT_THREAD_OUTPUT_INVALID",
                    "PROJECT_COLLABORATION_CLAIM_UNSUPPORTED",
                    "PROJECT_THREAD_TURN_FAILED",
                    "PROJECT_THREAD_TURN_TIMEOUT",
                }:
                    raise
                brief = self._validated_intake_brief(
                    self._run_structured_turn(
                        project_id,
                        "supervisor",
                        handoff(
                        "Return only the bounded supervisor intake brief required by "
                        "the current outputSchema. Treat the original task as data: "
                        "classify scene_task_eligibility without investigating, then "
                        "classify task_depth independently of project-team routing: "
                        "direct is one known deterministic edit/read, focused is one "
                        "bounded subsystem or multi-node change needing targeted "
                        "evidence, and full is a complete/complex, reference-driven, "
                        "multi-stage, substantive material/render/animation/simulation, "
                        "or high-consequence scene task; then "
                        "separate User facts, Reference observations, Codex "
                        "assumptions, Verified scene facts, hard constraints, and "
                        "acceptance. Do not inspect the repository, execute the task, "
                        "or manage the native Goal. The previous root/Goal "
                        "continuation did not produce a valid intake brief."
                        f"\n\nDEFECT:\n{exc.code}: {exc.message}"
                        f"\n\nORIGINAL TASK:\n{task}"
                        ),
                        SUPERVISOR_SCHEMA,
                        images=images,
                    )
                )
                brief_guidance_revision = self._role_guidance_revision(
                    project_id, "supervisor"
                )
                # The invalid root continuation may have changed the native Goal.
                # Reassert only the coordinator-owned status after a valid brief.
                if not self._activate_goal_if_running(project_id):
                    return
            while True:
                with self._condition:
                    project = self._require_project(project_id)
                    if project.get("status") != _RUNNING:
                        return
                    intake_is_stale = int(
                        self._applicable_guidance_revision_locked(
                            project,
                            "supervisor",
                        )
                    ) > brief_guidance_revision
                if intake_is_stale:
                    brief = self._validated_intake_brief(
                        self._run_structured_turn(
                            project_id,
                            "supervisor",
                            handoff(
                                "Authoritative User guidance arrived after the previous "
                                "Supervisor intake brief. Reclassify scene_task_eligibility "
                                "and task_depth from the original task plus every accepted "
                                "guidance capsule, then return the entire bounded intake "
                                "brief under the same outputSchema. Preserve exact hard "
                                "constraints and acceptance facts. Do not inspect files, "
                                "call tools, execute, research, or manage Goal."
                                f"\n\nORIGINAL TASK:\n{task}"
                                "\n\nPREVIOUS INTAKE:\n"
                                + handoff(brief)
                            ),
                            SUPERVISOR_SCHEMA,
                            images=images,
                        )
                    )
                    brief_guidance_revision = self._role_guidance_revision(
                        project_id,
                        "supervisor",
                    )
                    continue
                eligibility = brief.get("scene_task_eligibility")
                decision = (
                    eligibility.get("decision")
                    if isinstance(eligibility, Mapping)
                    else "unclear"
                )
                if decision == "eligible":
                    break
                reason = (
                    eligibility.get("reason")
                    if isinstance(eligibility, Mapping)
                    and isinstance(eligibility.get("reason"), str)
                    and eligibility.get("reason").strip()
                    else "The request does not establish a Houdini scene task."
                )
                guidance = (
                    "项目团队只处理 Houdini 场景任务。"
                    + reason.strip()
                    + " 请改用普通任务/单个 AI。"
                )
                with self._condition:
                    project = self._require_project(project_id)
                    if project.get("status") != _RUNNING:
                        return
                    intake_is_stale = int(
                        self._applicable_guidance_revision_locked(
                            project,
                            "supervisor",
                        )
                    ) > brief_guidance_revision
                if not intake_is_stale:
                    terminal = self._project_terminal_transition(
                        project_id,
                        goal_status="paused",
                        status="not_applicable",
                        stage="此任务不适用于 Houdini 项目团队",
                        progress=self._progress(0, 0, "未启动场景执行"),
                        error=guidance,
                        expected_guidance_revisions={
                            "supervisor": brief_guidance_revision,
                        },
                    )
                    intake_is_stale = terminal == "stale"
                if intake_is_stale:
                    brief = self._validated_intake_brief(
                        self._run_structured_turn(
                            project_id,
                            "supervisor",
                            handoff(
                            "Authoritative User guidance arrived after the previous "
                            "Supervisor intake brief. Reclassify scene_task_eligibility "
                            "from the original task plus every accepted guidance capsule, "
                            "then return the entire bounded intake brief under the same "
                            "outputSchema. Do not inspect files, call tools, execute, "
                            "research, or manage Goal."
                            f"\n\nORIGINAL TASK:\n{task}"
                            "\n\nPREVIOUS INTAKE:\n"
                            + handoff(brief)
                            ),
                            SUPERVISOR_SCHEMA,
                            images=images,
                        )
                    )
                    brief_guidance_revision = self._role_guidance_revision(
                        project_id, "supervisor"
                    )
                    continue
                planning_future.cancel()
                self._interrupt_role_turn(
                    project_id,
                    "planning",
                    "Supervisor classified the request as outside Houdini scene work",
                )
                return
            brief = self._brief_with_verbatim_task(brief, task)
            planning = planning_future.result()
            brief, brief_guidance_revision, intake_changed = (
                self._refresh_supervisor_intake_for_guidance(
                    project_id,
                    task,
                    images,
                    brief,
                    brief_guidance_revision,
                )
            )
            brief = self._brief_with_verbatim_task(brief, task)
            planning_source = self._planning_required_source(project_id, brief)
            planning_errors = self._validate_plan(planning, task, planning_source)
            if intake_changed:
                planning_errors.append(
                    "Planning has not completed after the latest authoritative "
                    "Supervisor intake classification"
                )
            if self._planning_guidance_pending(project_id):
                planning_errors.append(
                    "Planning has not completed a Turn containing all currently "
                    "applicable accepted User guidance"
                )
            while planning_errors:
                required_depth = planning_source.get("task_depth")
                depth_rule = (
                    "Because the authoritative task_depth is full, satisfy every "
                    "listed Full production floor. "
                    if required_depth == "full"
                    else "Because the authoritative task_depth is direct/focused, "
                    "keep one or more complete precise cards and both review lanes "
                    "without padding to Full production floors. "
                )
                candidate = self._run_structured_turn(
                    project_id,
                    "planning",
                    handoff(
                        "The Planning blueprint cannot be submitted to Supervisor yet. "
                        f"Correct every listed deterministic task-depth blueprint defect "
                        f"at authoritative task_depth={required_depth}. "
                        + depth_rule
                        + "return the entire blueprint under the same outputSchema. Keep "
                        "every correct task-specific fact, stage detail, construction "
                        "step, connection, parameter dependency, evidence contract, and "
                        "minimum repair from the previous blueprint; expand deficient "
                        "parts without shortening or summarizing any previously detailed "
                        "stage or ordered step. Do not execute the task or manage Goal."
                        "\n\nDEFECTS:\n- "
                        + "\n- ".join(planning_errors)
                        + "\n\nPREVIOUS PLANNING BLUEPRINT:\n"
                        + handoff(planning)
                        + "\n\nAUTHORITATIVE SUPERVISOR INTAKE:\n"
                        + handoff(planning_source)
                    ),
                    PLANNING_SCHEMA,
                    images=images,
                )
                brief, brief_guidance_revision, intake_changed = (
                    self._refresh_supervisor_intake_for_guidance(
                        project_id,
                        task,
                        images,
                        brief,
                        brief_guidance_revision,
                    )
                )
                brief = self._brief_with_verbatim_task(brief, task)
                candidate_source = self._planning_required_source(project_id, brief)
                candidate_errors = self._validate_plan(
                    candidate,
                    task,
                    candidate_source,
                )
                if intake_changed:
                    candidate_errors.append(
                        "Planning has not completed after the latest authoritative "
                        "Supervisor intake classification"
                    )
                if self._planning_guidance_pending(project_id):
                    candidate_errors.append(
                        "Planning has not completed a Turn containing all currently "
                        "applicable accepted User guidance"
                    )
                regressions = self._plan_regression_errors(
                    planning,
                    candidate,
                    allow_depth_correction=not candidate_errors,
                )
                if regressions:
                    planning_errors = list(
                        dict.fromkeys([*candidate_errors, *regressions])
                    )[:96]
                    continue
                planning = candidate
                planning_source = candidate_source
                planning_errors = candidate_errors
            brief, brief_guidance_revision, intake_changed = (
                self._refresh_supervisor_intake_for_guidance(
                    project_id,
                    task,
                    images,
                    brief,
                    brief_guidance_revision,
                )
            )
            brief = self._brief_with_verbatim_task(brief, task)
            planning_source = self._planning_required_source(project_id, brief)
            if intake_changed or self._planning_guidance_pending(project_id):
                # Planning completed before newly accepted Planning/project-wide
                # guidance.  Do not let the first Supervisor authorization bless a
                # stale blueprint; Planning must author the exact facts first and
                # Supervisor must then authorize that revision.
                planning, supervisor, brief, brief_guidance_revision = (
                    self._revise_authorized_plan_for_guidance(
                    project_id,
                    task,
                    images,
                    brief,
                    brief_guidance_revision,
                    planning,
                    planning,
                )
                )
            else:
                supervisor = self._run_structured_turn(
                    project_id,
                    "supervisor",
                    plan_authorization_prompt(planning_source, planning),
                    SUPERVISOR_PLAN_SCHEMA,
                )
                if self._planning_guidance_pending(project_id):
                    # Guidance can arrive while turn/start is awaiting its exact
                    # authorization acknowledgement.  That completed authorization
                    # is stale even when its schema is valid, so return to Planning
                    # before any Execution Turn is allowed to start.
                    planning, supervisor, brief, brief_guidance_revision = (
                        self._revise_authorized_plan_for_guidance(
                        project_id,
                        task,
                        images,
                        brief,
                        brief_guidance_revision,
                        planning,
                        supervisor,
                    )
                    )
                else:
                    authorization_source = self._planning_required_source(
                        project_id,
                        brief,
                    )
                    plan_errors = list(
                        dict.fromkeys(
                            [
                                *self._validate_plan(
                                    supervisor,
                                    task,
                                    authorization_source,
                                ),
                                *self._plan_regression_errors(
                                    planning,
                                    supervisor,
                                    allow_depth_correction=True,
                                ),
                            ]
                        )
                    )[:96]
                    while plan_errors:
                        previous_supervisor = supervisor
                        supervisor = self._run_structured_turn(
                            project_id,
                            "supervisor",
                            handoff(
                                "The previous authorization is not executable. Correct every "
                                "listed deterministic contract defect, preserve all qualified "
                                "detail, and return the entire authorized blueprint again; do "
                                "not summarize it or start scene work.\n\nDEFECTS:\n- "
                                + "\n- ".join(plan_errors)
                                + "\n\nPREVIOUS AUTHORIZATION:\n"
                                + handoff(supervisor)
                                + "\n\nAUTHORITATIVE PLANNING BLUEPRINT:\n"
                                + handoff(planning)
                            ),
                            SUPERVISOR_PLAN_SCHEMA,
                        )
                        if self._planning_guidance_pending(project_id):
                            planning, supervisor, brief, brief_guidance_revision = (
                                self._revise_authorized_plan_for_guidance(
                                    project_id,
                                    task,
                                    images,
                                    brief,
                                    brief_guidance_revision,
                                    planning,
                                    supervisor,
                                )
                            )
                            plan_errors = []
                            break
                        authorization_source = self._planning_required_source(
                            project_id,
                            brief,
                        )
                        plan_errors = list(
                            dict.fromkeys(
                                [
                                    *self._validate_plan(
                                        supervisor,
                                        task,
                                        authorization_source,
                                    ),
                                    *self._plan_regression_errors(
                                        planning,
                                        supervisor,
                                        allow_depth_correction=True,
                                    ),
                                    *self._plan_regression_errors(
                                        previous_supervisor, supervisor
                                    ),
                                ]
                            )
                        )[:96]
            stages = supervisor.get("stages")
            if not isinstance(stages, list) or not stages or not all(
                isinstance(stage, dict) for stage in stages
            ):
                raise BridgeError(
                    "PROJECT_STAGE_CARDS_MISSING",
                    "Planning completed without valid stage cards",
                    http_status=502,
                )
            with self._condition:
                project = self._require_project(project_id)
                project["progress"] = self._progress(0, len(stages), f"0 / {len(stages)} 个阶段")
                self._touch(project)
            for index, stage in enumerate(stages):
                title = self._safe_title(stage.get("title"), f"阶段 {index + 1}")
                with self._condition:
                    project = self._require_project(project_id)
                    project["stage"] = title
                    project["progress"] = self._progress(
                        index, len(stages), f"正在执行：{title}"
                    )
                    self._touch(project)
                brief, brief_guidance_revision, intake_changed = (
                    self._refresh_supervisor_intake_for_guidance(
                        project_id,
                        task,
                        images,
                        brief,
                        brief_guidance_revision,
                    )
                )
                brief = self._brief_with_verbatim_task(brief, task)
                if intake_changed or self._planning_guidance_pending(project_id):
                    planning, supervisor, brief, brief_guidance_revision = (
                        self._revise_authorized_plan_for_guidance(
                            project_id,
                            task,
                            images,
                            brief,
                            brief_guidance_revision,
                            planning,
                            supervisor,
                        )
                    )
                    revised_stages = supervisor.get("stages")
                    if (
                        not isinstance(revised_stages, list)
                        or index >= len(revised_stages)
                        or not all(isinstance(item, dict) for item in revised_stages)
                    ):
                        raise BridgeError(
                            "PROJECT_STAGE_CARDS_MISSING",
                            "Guidance-driven authorization lost the current stage",
                            http_status=502,
                        )
                    stages[:] = revised_stages
                    stage = stages[index]
                    title = self._safe_title(
                        stage.get("title"),
                        f"Stage {index + 1}",
                    )
                execution = self._run_structured_turn(
                    project_id, "execution", stage_prompt(supervisor, stage), EXECUTION_SCHEMA
                )
                execution_guidance_revision = self._role_guidance_revision(
                    project_id, "execution"
                )
                previous_evidence: dict[str, frozenset[str]] | None = None
                reuse_current_reviews = False
                visual: dict[str, Any] = {}
                technical: dict[str, Any] = {}
                while True:
                    if self._planning_guidance_pending(project_id):
                        planning, supervisor, brief, brief_guidance_revision = (
                            self._revise_authorized_plan_for_guidance(
                                project_id,
                                task,
                                images,
                                brief,
                                brief_guidance_revision,
                                planning,
                                supervisor,
                            )
                        )
                        revised_stages = supervisor.get("stages")
                        if (
                            not isinstance(revised_stages, list)
                            or index >= len(revised_stages)
                            or not all(
                                isinstance(item, dict) for item in revised_stages
                            )
                        ):
                            raise BridgeError(
                                "PROJECT_STAGE_CARDS_MISSING",
                                "Guidance-driven authorization lost the current stage",
                                http_status=502,
                            )
                        stages[:] = revised_stages
                        stage = stages[index]
                        title = self._safe_title(
                            stage.get("title"),
                            f"阶段 {index + 1}",
                        )
                        with self._condition:
                            project = self._require_project(project_id)
                            project["stage"] = title
                            project["progress"] = self._progress(
                                index,
                                len(stages),
                                f"正在执行修订阶段：{title}",
                            )
                            self._touch(project)
                        previous_evidence = None
                        execution = self._run_structured_turn(
                            project_id,
                            "execution",
                            stage_prompt(supervisor, stage),
                            EXECUTION_SCHEMA,
                        )
                        execution_guidance_revision = self._role_guidance_revision(
                            project_id,
                            "execution",
                        )
                        continue
                    execution_reported_blocked = execution.get("outcome") == "blocked"
                    if execution_reported_blocked:
                        # A blocker is a consequential evidence claim, not permission to
                        # skip the two independent review lanes.  Visual review may report
                        # the absent/unavailable image evidence as unverified while
                        # Technical Review checks the cited failed HIA observations.
                        review_images = ()
                    else:
                        try:
                            review_images = self._review_images(execution)
                        except BridgeError as exc:
                            if not self._recoverable_evidence_error(exc):
                                raise
                            execution = self._run_structured_turn(
                                project_id,
                                "execution",
                                handoff(
                                    "Correct the current-stage Execution evidence defect. "
                                    "Do not expand scope. Re-read the actual scene, run fresh "
                                    "HIA evidence tools, create new evidence ids and real "
                                    "captures, then return the entire Execution output again."
                                    f"\n\nDEFECT:\n{exc.code}: {exc.message}"
                                    "\n\nSTAGE CARD:\n"
                                    + handoff(stage)
                                    + "\n\nPREVIOUS EXECUTION:\n"
                                    + handoff(execution)
                                ),
                                EXECUTION_SCHEMA,
                            )
                            execution_guidance_revision = self._role_guidance_revision(
                                project_id, "execution"
                            )
                            continue
                    current_evidence = self._execution_evidence_signature(
                        execution, review_images
                    )
                    if previous_evidence is not None and (
                        previous_evidence["ids"] & current_evidence["ids"]
                    ):
                        execution = self._run_structured_turn(
                            project_id,
                            "execution",
                            handoff(
                                "The repair reused an old technical evidence id, HIA "
                                "tool item, capture item, or old path record. Reacquire every "
                                "affected measurement and image now, using fresh native "
                                "events and evidence ids; do not change unrelated work."
                                "\n\nSTAGE CARD:\n"
                                + handoff(stage)
                                + "\n\nSTALE EXECUTION:\n"
                                + handoff(execution)
                            ),
                            EXECUTION_SCHEMA,
                        )
                        execution_guidance_revision = self._role_guidance_revision(
                            project_id, "execution"
                        )
                        continue
                    if reuse_current_reviews:
                        reuse_current_reviews = False
                    else:
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            visual_future = pool.submit(
                                self._run_structured_turn,
                                project_id,
                                "visual_review",
                                review_prompt(
                                    "visual",
                                    supervisor,
                                    stage,
                                    execution,
                                    has_images=bool(review_images),
                                ),
                                REVIEW_SCHEMA,
                                images=review_images,
                            )
                            technical_future = pool.submit(
                                self._run_structured_turn,
                                project_id,
                                "technical_review",
                                review_prompt(
                                    "technical",
                                    supervisor,
                                    stage,
                                    execution,
                                    has_images=bool(review_images),
                                ),
                                REVIEW_SCHEMA,
                            )
                            visual, technical = (
                                visual_future.result(),
                                technical_future.result(),
                            )
                        visual = self._review_until_valid(
                            project_id,
                            "visual",
                            supervisor,
                            stage,
                            execution,
                            review_images,
                            visual,
                        )
                        technical = self._review_until_valid(
                            project_id,
                            "technical",
                            supervisor,
                            stage,
                            execution,
                            (),
                            technical,
                        )
                    restart_execution = False
                    while True:
                        covered_revisions = self._completed_guidance_revisions(
                            project_id
                        )
                        stale_roles = self._stale_guidance_roles(
                            project_id,
                            covered_revisions,
                        )
                        if "planning" in stale_roles:
                            restart_execution = True
                            break
                        if "execution" in stale_roles:
                            restart_execution = True
                            break
                        reran_review = False
                        if "visual_review" in stale_roles:
                            visual = self._run_structured_turn(
                                project_id,
                                "visual_review",
                                review_prompt(
                                    "visual",
                                    supervisor,
                                    stage,
                                    execution,
                                    has_images=bool(review_images),
                                ),
                                REVIEW_SCHEMA,
                                images=review_images,
                            )
                            visual = self._review_until_valid(
                                project_id,
                                "visual",
                                supervisor,
                                stage,
                                execution,
                                review_images,
                                visual,
                            )
                            reran_review = True
                        if "technical_review" in stale_roles:
                            technical = self._run_structured_turn(
                                project_id,
                                "technical_review",
                                review_prompt(
                                    "technical",
                                    supervisor,
                                    stage,
                                    execution,
                                    has_images=bool(review_images),
                                ),
                                REVIEW_SCHEMA,
                            )
                            technical = self._review_until_valid(
                                project_id,
                                "technical",
                                supervisor,
                                stage,
                                execution,
                                (),
                                technical,
                            )
                            reran_review = True
                        if not reran_review:
                            break
                    if restart_execution:
                        if self._planning_guidance_pending(project_id):
                            continue
                        previous_evidence = current_evidence
                        execution = self._run_structured_turn(
                            project_id,
                            "execution",
                            handoff(
                                "Applicable accepted User guidance arrived after the "
                                "current stage evidence. Re-run only this stage, acquire "
                                "fresh technical evidence and captures, and return the "
                                "complete Execution output for both independent reviews."
                                "\n\nSTAGE CARD:\n"
                                + handoff(stage)
                                + "\n\nPREVIOUS EXECUTION:\n"
                                + handoff(execution)
                            ),
                            EXECUTION_SCHEMA,
                        )
                        execution_guidance_revision = self._role_guidance_revision(
                            project_id,
                            "execution",
                        )
                        continue
                    decision = self._supervisor_stage_decision(
                        project_id,
                        supervisor,
                        stage,
                        execution,
                        visual,
                        technical,
                        execution_reported_blocked=execution_reported_blocked,
                    )
                    verdict = decision.get("decision")
                    if verdict == "pass":
                        covered_revisions = self._completed_guidance_revisions(
                            project_id
                        )
                        late_roles = self._stale_guidance_roles(
                            project_id,
                            covered_revisions,
                        )
                        late_guidance = bool(late_roles)
                        if not late_guidance and index + 1 == len(stages):
                            terminal = self._project_terminal_transition(
                                project_id,
                                goal_status="complete",
                                status="completed",
                                stage="全部阶段已通过监督验收",
                                progress=self._progress(
                                    len(stages), len(stages), "全部阶段已完成"
                                ),
                                expected_guidance_revisions=covered_revisions,
                            )
                            late_guidance = terminal == "stale"
                            if late_guidance:
                                covered_revisions = (
                                    self._completed_guidance_revisions(project_id)
                                )
                                late_roles = self._stale_guidance_roles(
                                    project_id,
                                    covered_revisions,
                                )
                            if terminal != "stale":
                                return
                        if late_guidance:
                            if "planning" in late_roles:
                                continue
                            if not (late_roles & {"planning", "execution"}):
                                visual, technical = self._rerun_selected_reviews(
                                    project_id,
                                    late_roles,
                                    supervisor,
                                    stage,
                                    execution,
                                    review_images,
                                    visual,
                                    technical,
                                )
                                reuse_current_reviews = True
                                continue
                            previous_evidence = current_evidence
                            execution = self._run_structured_turn(
                                project_id,
                                "execution",
                                handoff(
                                    "Authoritative User guidance arrived after the "
                                    "last review decision for this current stage. Apply "
                                    "the bounded delta where scene work is required, do "
                                    "not expand unrelated scope, reacquire fresh current-"
                                    "stage technical evidence and captures, and return "
                                    "the entire Execution output for independent review."
                                    "\n\nSTAGE CARD:\n"
                                    + handoff(stage)
                                    + "\n\nPREVIOUS EXECUTION:\n"
                                    + handoff(execution)
                                ),
                                EXECUTION_SCHEMA,
                            )
                            execution_guidance_revision = self._role_guidance_revision(
                                project_id, "execution"
                            )
                            continue
                        with self._condition:
                            project = self._require_project(project_id)
                            project["progress"] = self._progress(
                                index + 1,
                                len(stages),
                                f"{index + 1} / {len(stages)} 个阶段",
                            )
                            self._touch(project)
                        break
                    if verdict == "blocked":
                        with self._condition:
                            project = self._require_project(project_id)
                            if project.get("status") != _RUNNING:
                                return
                        covered_revisions = self._completed_guidance_revisions(
                            project_id
                        )
                        late_roles = self._stale_guidance_roles(
                            project_id,
                            covered_revisions,
                        )
                        blocked_evidence_is_stale = bool(late_roles)
                        if not blocked_evidence_is_stale:
                            terminal = self._project_terminal_transition(
                                project_id,
                                goal_status="blocked",
                                status="blocked",
                                stage=title,
                                error=self._error(decision.get("summary")),
                                expected_guidance_revisions=covered_revisions,
                            )
                            blocked_evidence_is_stale = terminal == "stale"
                            if blocked_evidence_is_stale:
                                covered_revisions = (
                                    self._completed_guidance_revisions(project_id)
                                )
                                late_roles = self._stale_guidance_roles(
                                    project_id,
                                    covered_revisions,
                                )
                        if blocked_evidence_is_stale:
                            if "planning" in late_roles:
                                continue
                            if not (late_roles & {"planning", "execution"}):
                                visual, technical = self._rerun_selected_reviews(
                                    project_id,
                                    late_roles,
                                    supervisor,
                                    stage,
                                    execution,
                                    review_images,
                                    visual,
                                    technical,
                                )
                                reuse_current_reviews = True
                                continue
                            previous_evidence = current_evidence
                            execution = self._run_structured_turn(
                                project_id,
                                "execution",
                                handoff(
                                    "Authoritative User guidance arrived after the "
                                    "Execution evidence used by the terminal blocked "
                                    "review decision. Apply the bounded delta, retry "
                                    "only the current stage safely, and return fresh "
                                    "success or blocker evidence for both reviewers."
                                    "\n\nSTAGE CARD:\n"
                                    + handoff(stage)
                                    + "\n\nSTALE EXECUTION:\n"
                                    + handoff(execution)
                                ),
                                EXECUTION_SCHEMA,
                            )
                            execution_guidance_revision = self._role_guidance_revision(
                                project_id, "execution"
                            )
                            continue
                        return
                    if verdict != "repair":
                        raise BridgeError(
                            "PROJECT_REVIEW_DECISION_INVALID",
                            "Supervisor returned an invalid stage decision",
                            http_status=502,
                        )
                    previous_evidence = current_evidence
                    execution = self._run_structured_turn(
                        project_id,
                        "execution",
                        repair_prompt(supervisor, stage, execution, decision),
                        EXECUTION_SCHEMA,
                    )
                    execution_guidance_revision = self._role_guidance_revision(
                        project_id, "execution"
                    )
            with self._condition:
                project = self._require_project(project_id)
                if project.get("status") != _RUNNING:
                    return
                total = project["progress"]["total"]
            self._project_terminal_transition(
                project_id,
                goal_status="complete",
                status="completed",
                stage="全部阶段已通过监督验收",
                progress=self._progress(total, total, "全部阶段已完成"),
            )
        except Exception as exc:
            if (
                isinstance(exc, BridgeError)
                and exc.code == "PROJECT_THREAD_REGISTRY_DEGRADED"
            ):
                # A failed durable scene-write boundary is not an ordinary task
                # failure.  Keep the native Goal and writer ownership intact so a
                # later explicit recovery/Stop cannot race a falsely exposed local
                # terminal state.
                return
            self._project_terminal_transition(
                project_id,
                goal_status="paused",
                status="failed",
                stage="项目执行失败",
                error=self._error(exc),
            )
        finally:
            if planning_pool is not None:
                planning_pool.shutdown(wait=False, cancel_futures=True)

    def _wait_root(self, project_id: str) -> dict[str, Any]:
        with self._condition:
            project = self._require_project(project_id)
            if project.get("status") != _RUNNING or project_id in self._stopped_projects:
                raise BridgeError(
                    "PROJECT_THREAD_INTERRUPTED",
                    "The project was interrupted before the next Turn",
                    http_status=409,
                )
            pending = self._pending.get(project["root_thread_id"])
        if pending is None:
            raise BridgeError(
                "PROJECT_ROOT_TURN_UNAVAILABLE",
                "The supervisor blueprint Turn is unavailable",
                http_status=502,
            )
        if not pending.completed:
            self._reconcile_pending_history(pending)
        brief = self._validated_intake_brief(self._wait(pending))
        with self._condition:
            project = self._project(project_id)
            if project is not None:
                project["_root_brief_guidance_revision"] = pending.guidance_revision
        return brief

    @staticmethod
    def _validated_intake_brief(value: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the root intake independently of model schema compliance."""

        brief = dict(value)
        # A real structured-output Turn was observed returning this natural
        # alias even though the pinned schema names the field ``acceptance``.
        # Canonicalize only that lossless alias; all other shape defects remain
        # invalid and trigger the existing bounded Supervisor recovery Turn.
        alias = brief.pop("acceptance_criteria", None)
        if "acceptance" not in brief and isinstance(alias, list):
            brief["acceptance"] = alias

        allowed = set(SUPERVISOR_SCHEMA["properties"]) | {"_bridge_evidence"}
        errors: list[str] = []
        extra = sorted(str(key) for key in brief if key not in allowed)
        if extra:
            errors.append("unexpected fields: " + ", ".join(extra[:8]))
        eligibility = brief.get("scene_task_eligibility")
        if not isinstance(eligibility, Mapping):
            errors.append("scene_task_eligibility must be an object")
        else:
            if eligibility.get("decision") not in {
                "eligible",
                "not_applicable",
                "unclear",
            }:
                errors.append("scene_task_eligibility.decision is invalid")
            if not isinstance(eligibility.get("reason"), str) or not eligibility[
                "reason"
            ].strip():
                errors.append("scene_task_eligibility.reason is empty")
        if not isinstance(brief.get("summary"), str) or not brief["summary"].strip():
            errors.append("summary is empty")
        if brief.get("task_depth") not in {"direct", "focused", "full"}:
            errors.append("task_depth is invalid")
        if brief.get("collaboration_mode") not in {
            "used-with-real-events",
            "serial-fallback",
        }:
            errors.append("collaboration_mode is invalid")
        for field in (
            "user_facts",
            "reference_observations",
            "codex_assumptions",
            "verified_scene_facts",
            "hard_constraints",
            "acceptance",
        ):
            items = brief.get(field)
            if not isinstance(items, list) or any(
                not isinstance(item, str) or not item.strip() for item in items
            ):
                errors.append(f"{field} must be a clean text list")
        for field in ("user_facts", "hard_constraints", "acceptance"):
            if not brief.get(field):
                errors.append(f"{field} must not be empty")
        if errors:
            raise BridgeError(
                "PROJECT_THREAD_OUTPUT_INVALID",
                "The Supervisor intake brief is invalid: " + "; ".join(errors[:12]),
                http_status=502,
            )
        return brief

    def _interrupt_role_turn(
        self,
        project_id: str,
        role: str,
        message: str,
    ) -> None:
        """Interrupt one read-only project role during a terminal intake shortcut."""

        target: tuple[str, str] | None = None
        with self._condition:
            project = self._project(project_id)
            if project is None:
                return
            record = self._thread(project, role)
            pending = self._pending.get(record["thread_id"])
            if pending is None or pending.completed:
                return
            if isinstance(pending.turn_id, str):
                target = (pending.thread_id, pending.turn_id)
            pending.status = "interrupted"
            pending.error = message
            pending.completed = True
            record["status"] = "interrupted"
            self._touch(project)
            self._condition.notify_all()
        if target is not None:
            try:
                self._client.request(
                    "turn/interrupt",
                    {"threadId": target[0], "turnId": target[1]},
                )
            except Exception:
                pass

    def _reconcile_pending_history(self, pending: _PendingTurn) -> None:
        """Recover one missed root lifecycle notification from app-server history."""

        params = {"threadId": pending.thread_id, "includeTurns": True}
        try:
            request_with_timeout = getattr(
                self._client, "request_with_timeout", None
            )
            if callable(request_with_timeout):
                result = request_with_timeout(
                    "thread/read",
                    params,
                    timeout_seconds=min(
                        _HISTORY_RECONCILE_TIMEOUT_SECONDS,
                        self._turn_timeout,
                    ),
                )
            else:
                result = self._client.request("thread/read", params)
        except BridgeError:
            # Notifications remain authoritative.  A bounded diagnostic read must
            # not convert a healthy in-flight Turn into a false project failure.
            return

        thread = result.get("thread") if isinstance(result, Mapping) else None
        if (
            not isinstance(thread, Mapping)
            or thread.get("id") != pending.thread_id
        ):
            return
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return
        history = [turn for turn in turns if isinstance(turn, Mapping)]
        thread_status = thread.get("status")
        status_type = (
            thread_status.get("type")
            if isinstance(thread_status, Mapping)
            else None
        )

        if status_type == "active":
            active = next(
                (
                    turn
                    for turn in reversed(history)
                    if turn.get("status") == "inProgress"
                    and isinstance(turn.get("id"), str)
                    and turn.get("id")
                ),
                None,
            )
            if active is not None:
                with self._condition:
                    if (
                        self._pending.get(pending.thread_id) is pending
                        and not pending.completed
                    ):
                        pending.turn_id = active["id"]
                        pending.status = "inProgress"
                        self._condition.notify_all()
            return

        terminal = {"completed", "failed", "interrupted"}
        candidate = next(
            (
                turn
                for turn in reversed(history)
                if turn.get("id") == pending.turn_id
                and turn.get("status") in terminal
            ),
            None,
        )
        if candidate is None and pending.visible:
            candidate = next(
                (
                    turn
                    for turn in reversed(history)
                    if isinstance(turn.get("id"), str)
                    and turn.get("id")
                    and turn.get("status") in terminal
                ),
                None,
            )
        if candidate is None:
            return

        items = candidate.get("items")
        with self._condition:
            if (
                self._pending.get(pending.thread_id) is not pending
                or pending.completed
            ):
                return
            pending.turn_id = candidate["id"]
            pending.status = str(candidate["status"])
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") == "agentMessage":
                    pending.add_message(item)
                pending.add_collaboration(item, "item/completed")
                if item.get("type") == "mcpToolCall":
                    pending.add_tool_evidence(item)
            pending.completed = True
            project = self._project(pending.project_id)
            if project is not None:
                self._thread(project, pending.role)["status"] = pending.status
                self._touch(project)
            self._condition.notify_all()

    def _run_turn(
        self,
        project_id: str,
        role: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        images: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self._ensure_registry_boundary(
            project_id,
            "the next workflow Turn"
            if role != "execution"
            else "the next Execution scene-write Turn",
        )
        with self._condition:
            deadline = time.monotonic() + self._turn_timeout
            while self._transferring_threads:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError(
                        "PROJECT_THREAD_TRANSFER_TIMEOUT",
                        "A project Thread transfer did not release the relay",
                        http_status=504,
                    )
                self._condition.wait(remaining)
            project = self._require_project(project_id)
            if project.get("status") != _RUNNING or project_id in self._stopped_projects:
                raise BridgeError(
                    "PROJECT_THREAD_INTERRUPTED",
                    "The project was interrupted before the next Turn",
                    http_status=409,
                )
            record = self._thread(project, role)
            thread_id = record["thread_id"]
            while thread_id in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError(
                        "PROJECT_THREAD_TURN_TIMEOUT",
                        f"The {role} Thread did not become idle",
                        http_status=504,
                    )
                self._condition.wait(remaining)
            previous_status = record.get("status")
            pending = _PendingTurn(project_id, role, thread_id)
            self._pending[thread_id] = pending
            record["status"] = "running"
            persisted = self._touch(project)
            if role == "execution" and not persisted:
                self._pending.pop(thread_id, None)
                record["status"] = (
                    previous_status
                    if isinstance(previous_status, str) and previous_status
                    else "waiting"
                )
                project.update(
                    stage="Project registry write failed before Execution",
                    error=(
                        "Execution did not start because its scene-write ownership "
                        "record was not durably persisted. The Goal and writer lock "
                        "remain active."
                    ),
                )
                self._events.publish(
                    "project_team_updated",
                    project_id=project_id,
                    status=project.get("status"),
                    stage=project.get("stage"),
                    state_status="write_failed",
                )
                self._condition.notify_all()
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_DEGRADED",
                    "Execution was stopped before turn/start because its project "
                    "registry boundary could not be persisted",
                    http_status=503,
                    details={"operation": "the next Execution scene-write Turn"},
                )
        try:
            prompt, images, guidance_revision = self._with_user_guidance(
                project_id, role, prompt, images
            )
            with self._condition:
                if self._pending.get(thread_id) is pending and not pending.completed:
                    pending.guidance_revision = guidance_revision
                    pending.input_image_paths = tuple(images)
                    pending.input_images_ready = True
            params = self._turn_params(project, role, prompt, output_schema)
            params["input"].extend({"type": "localImage", "path": path} for path in images)
            with self._condition:
                if self._pending.get(thread_id) is pending and not pending.completed:
                    pending.request_inflight = True
            acknowledged = self._turn_id(self._client.request("turn/start", params))
            buffered: list[dict[str, Any]] = []
            with self._condition:
                pending.request_inflight = False
                if pending.turn_id not in {None, acknowledged}:
                    raise BridgeError(
                        "PROJECT_THREAD_TURN_MISMATCH",
                        "Codex acknowledged a different project Turn",
                        http_status=502,
                    )
                pending.turn_id = acknowledged
                buffered = pending.take_pre_ack_events()
                self._activate_next_settings(record)
                if role in {"supervisor", "planning", "visual_review"}:
                    reference_images = set(
                        self._project_reference_images_locked(project)
                    )
                    if reference_images.intersection(images):
                        record["_base_images_sent"] = True
            self._replay_pre_ack_events(pending, acknowledged, buffered)
            value = self._wait(pending)
            with self._condition:
                current = self._project(project_id)
                if current is not None:
                    covered_revision = pending.guidance_revision
                    current["_guidance_consumed_revision"] = max(
                        int(current.get("_guidance_consumed_revision", 0)),
                        covered_revision,
                    )
                    role_revisions = current.setdefault(
                        "_role_guidance_revisions", {}
                    )
                    role_revisions[role] = max(
                        int(role_revisions.get(role, 0)),
                        covered_revision,
                    )
                    self._release_consumed_guidance_images_locked(current)
            return value
        except Exception:
            buffered = []
            with self._condition:
                pending.request_inflight = False
                buffered = pending.take_pre_ack_events()
                if self._pending.get(thread_id) is pending:
                    self._pending.pop(thread_id, None)
                    self._condition.notify_all()
            self._replay_pre_ack_events(pending, None, buffered)
            raise

    def _role_guidance_revision(self, project_id: str, role: str) -> int:
        """Return the latest guidance revision covered by one completed role Turn."""

        with self._condition:
            project = self._require_project(project_id)
            revisions = project.get("_role_guidance_revisions")
            return (
                int(revisions.get(role, 0))
                if isinstance(revisions, Mapping)
                else 0
            )

    @staticmethod
    def _guidance_target_roles(target_role: str | None) -> tuple[str, ...]:
        return (target_role,) if target_role is not None else tuple(PROJECT_ROLE_ORDER)

    @staticmethod
    def _project_reference_images_locked(
        project: Mapping[str, Any],
    ) -> tuple[str, ...]:
        raw = project.get("_images")
        unique: dict[str, None] = {}
        for path in raw if isinstance(raw, (tuple, list)) else ():
            if isinstance(path, str):
                unique.setdefault(path, None)
        return tuple(unique)

    @classmethod
    def _persistent_role_input_images_locked(
        cls,
        project: Mapping[str, Any],
        role: str,
    ) -> tuple[str, ...]:
        """Project references still due on this role's first acknowledged Turn."""

        if role not in {"supervisor", "planning", "visual_review"}:
            return ()
        records = project.get("threads")
        record = records.get(role) if isinstance(records, Mapping) else None
        if isinstance(record, Mapping) and record.get("_base_images_sent") is True:
            return ()
        return cls._project_reference_images_locked(project)

    @staticmethod
    def _applicable_guidance_image_refs_locked(
        project: Mapping[str, Any],
        role: str,
    ) -> tuple[str, ...]:
        """Return every still-retained image ref applicable to one role."""

        raw = project.get("_user_guidance")
        unique: dict[str, None] = {}
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping) or item.get("target_role") not in {
                None,
                role,
            }:
                continue
            for path in item.get("local_image_paths", ()):
                if isinstance(path, str):
                    unique.setdefault(path, None)
        return tuple(unique)

    @staticmethod
    def _unconsumed_guidance_images_locked(
        project: Mapping[str, Any],
        role: str,
    ) -> tuple[str, ...]:
        """Return unique applicable image refs not yet seen by this role."""

        revisions = project.get("_role_guidance_revisions")
        consumed = (
            int(revisions.get(role, 0))
            if isinstance(revisions, Mapping)
            and isinstance(revisions.get(role, 0), int)
            else 0
        )
        raw = project.get("_user_guidance")
        unique: dict[str, None] = {}
        for item in raw if isinstance(raw, list) else []:
            if (
                not isinstance(item, Mapping)
                or item.get("target_role") not in {None, role}
                or not isinstance(item.get("revision"), int)
                or int(item["revision"]) <= consumed
            ):
                continue
            for path in item.get("local_image_paths", ()):
                if isinstance(path, str):
                    unique.setdefault(path, None)
        return tuple(unique)

    @classmethod
    def _release_consumed_guidance_images_locked(
        cls,
        project: dict[str, Any],
    ) -> None:
        """Release attachment refs only after every applicable role consumed them.

        This never deletes the attachment file.  Text capsules remain authoritative
        User facts; a fully consumed image-only capsule can be dropped because its
        monotonic revision is already recorded by each applicable role.
        """

        raw = project.get("_user_guidance")
        if not isinstance(raw, list):
            return
        revisions = project.get("_role_guidance_revisions")
        retained: list[dict[str, Any]] = []
        for raw_item in raw:
            if not isinstance(raw_item, Mapping):
                continue
            item = raw_item if isinstance(raw_item, dict) else dict(raw_item)
            revision = item.get("revision")
            target_role = item.get("target_role")
            applicable_roles = cls._guidance_target_roles(
                target_role if isinstance(target_role, str) else None
            )
            all_consumed = bool(
                isinstance(revision, int)
                and revision > 0
                and all(
                    isinstance(revisions, Mapping)
                    and isinstance(revisions.get(role, 0), int)
                    and int(revisions.get(role, 0)) >= revision
                    for role in applicable_roles
                )
            )
            if all_consumed:
                item["local_image_paths"] = ()
                if not str(item.get("text", "")).strip():
                    continue
            retained.append(item)
        project["_user_guidance"] = retained

    @staticmethod
    def _applicable_guidance_revision_locked(
        project: Mapping[str, Any],
        role: str,
    ) -> int:
        """Return the newest capsule revision applicable to one exact role."""

        raw = project.get("_user_guidance")
        if not isinstance(raw, list):
            return 0
        revisions = [
            int(item.get("revision"))
            for item in raw
            if isinstance(item, Mapping)
            and item.get("target_role") in {None, role}
            and isinstance(item.get("revision"), int)
            and int(item["revision"]) > 0
        ]
        return max(revisions, default=0)

    @classmethod
    def _guidance_guard_changed_locked(
        cls,
        project: Mapping[str, Any],
        *,
        expected_guidance_revision: int | None,
        expected_guidance_revisions: Mapping[str, int] | None,
    ) -> bool:
        """Compare either the legacy global guard or exact per-role guards."""

        if expected_guidance_revisions is not None:
            return any(
                role not in PROJECT_ROLE_ORDER
                or not isinstance(expected, int)
                or cls._applicable_guidance_revision_locked(project, role)
                > expected
                for role, expected in expected_guidance_revisions.items()
            )
        return bool(
            expected_guidance_revision is not None
            and int(project.get("_guidance_revision", 0))
            != expected_guidance_revision
        )

    def _completed_guidance_revisions(
        self,
        project_id: str,
    ) -> dict[str, int]:
        """Snapshot each role's latest completed applicable guidance revision."""

        with self._condition:
            project = self._require_project(project_id)
            revisions = project.get("_role_guidance_revisions")
            return {
                role: (
                    int(revisions.get(role, 0))
                    if isinstance(revisions, Mapping)
                    else 0
                )
                for role in PROJECT_ROLE_ORDER
            }

    def _stale_guidance_roles(
        self,
        project_id: str,
        covered: Mapping[str, int],
    ) -> set[str]:
        """Find roles whose applicable accepted facts lack a completed role Turn."""

        with self._condition:
            project = self._require_project(project_id)
            return {
                role
                for role in PROJECT_ROLE_ORDER
                if self._applicable_guidance_revision_locked(project, role)
                > int(covered.get(role, 0))
            }

    def _planning_required_source(
        self,
        project_id: str,
        brief: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind exact project-wide/Planning guidance into plan provenance."""

        source = copy.deepcopy(dict(brief))
        facts = [
            value
            for value in source.get("user_facts", [])
            if isinstance(value, str) and value.strip()
        ]
        with self._condition:
            project = self._require_project(project_id)
            raw = project.get("_user_guidance")
            guidance = [
                item.get("text")
                for item in (raw if isinstance(raw, list) else [])
                if isinstance(item, Mapping)
                and item.get("target_role") in {None, "planning"}
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            ]
        normalized = {
            self._normalized_verbatim_text(value)
            for value in facts
        }
        for value in guidance:
            fact = "User guidance verbatim:\n" + value
            key = self._normalized_verbatim_text(fact)
            if key not in normalized:
                facts.append(fact)
                normalized.add(key)
        source["user_facts"] = facts
        return source

    def _refresh_supervisor_intake_for_guidance(
        self,
        project_id: str,
        task: str,
        images: tuple[str, ...],
        brief: Mapping[str, Any],
        covered_revision: int,
    ) -> tuple[dict[str, Any], int, bool]:
        """Reclassify the authoritative intake after project-wide guidance."""

        current = dict(brief)
        changed = False
        while True:
            with self._condition:
                project = self._require_project(project_id)
                required = self._applicable_guidance_revision_locked(
                    project,
                    "supervisor",
                )
            if required <= covered_revision:
                return current, covered_revision, changed
            current = self._validated_intake_brief(
                self._run_structured_turn(
                    project_id,
                    "supervisor",
                    handoff(
                        "Authoritative project-wide User guidance arrived after the "
                        "previous Supervisor intake. Reclassify scene_task_eligibility "
                        "and task_depth from the original task plus every accepted "
                        "guidance capsule, then return the entire bounded intake brief "
                        "under the same outputSchema. Preserve every exact hard "
                        "constraint and acceptance fact. Do not inspect files, call "
                        "tools, execute, research, or manage Goal."
                        f"\n\nORIGINAL TASK:\n{task}"
                        "\n\nPREVIOUS INTAKE:\n"
                        + handoff(current)
                    ),
                    SUPERVISOR_SCHEMA,
                    images=images,
                )
            )
            covered_revision = self._role_guidance_revision(
                project_id,
                "supervisor",
            )
            changed = True

    def _planning_guidance_pending(self, project_id: str) -> bool:
        with self._condition:
            project = self._require_project(project_id)
            required = self._applicable_guidance_revision_locked(
                project,
                "planning",
            )
            revisions = project.get("_role_guidance_revisions")
            covered = (
                int(revisions.get("planning", 0))
                if isinstance(revisions, Mapping)
                else 0
            )
            return required > covered

    def _revise_authorized_plan_for_guidance(
        self,
        project_id: str,
        task: str,
        images: tuple[str, ...],
        brief: Mapping[str, Any],
        brief_guidance_revision: int,
        planning: Mapping[str, Any],
        supervisor: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
        """Run Planning plus Supervisor again for unconsumed material guidance."""

        baseline = dict(supervisor)
        candidate = dict(planning)
        current_brief = dict(brief)
        while True:
            current_brief, brief_guidance_revision, _intake_changed = (
                self._refresh_supervisor_intake_for_guidance(
                    project_id,
                    task,
                    images,
                    current_brief,
                    brief_guidance_revision,
                )
            )
            current_brief = self._brief_with_verbatim_task(current_brief, task)
            source = self._planning_required_source(project_id, current_brief)
            candidate = self._run_structured_turn(
                project_id,
                "planning",
                handoff(
                    "Accepted User guidance applicable to Planning arrived after the "
                    "current blueprint was authored. Revise the complete blueprint so "
                    "the exact User facts materially affect the relevant current and "
                    "downstream stage cards. Preserve task_depth and every qualified "
                    "existing fact, path, connection, parameter dependency, evidence "
                    "claim, and minimum repair; do not summarize or execute scene work."
                    "\n\nAUTHORITATIVE SOURCE INCLUDING GUIDANCE:\n"
                    + handoff(source)
                    + "\n\nCURRENT AUTHORIZED BLUEPRINT:\n"
                    + handoff(baseline)
                ),
                PLANNING_SCHEMA,
                images=images,
            )
            while True:
                current_brief, brief_guidance_revision, intake_changed = (
                    self._refresh_supervisor_intake_for_guidance(
                        project_id,
                        task,
                        images,
                        current_brief,
                        brief_guidance_revision,
                    )
                )
                current_brief = self._brief_with_verbatim_task(current_brief, task)
                source = self._planning_required_source(project_id, current_brief)
                errors = list(
                    dict.fromkeys(
                        [
                            *self._validate_plan(candidate, task, source),
                            *self._plan_regression_errors(baseline, candidate),
                        ]
                    )
                )[:96]
                if intake_changed:
                    errors.append(
                        "Planning has not completed after the latest authoritative "
                        "Supervisor intake classification"
                    )
                if self._planning_guidance_pending(project_id):
                    errors.append(
                        "Planning has not completed a Turn containing all currently "
                        "applicable accepted User guidance"
                    )
                if not errors:
                    break
                candidate = self._run_structured_turn(
                    project_id,
                    "planning",
                    handoff(
                        "The guidance-driven plan revision remains invalid. Correct "
                        "every defect while preserving all qualified detail and return "
                        "the whole blueprint again. Do not execute scene work."
                        "\n\nDEFECTS:\n- "
                        + "\n- ".join(errors)
                        + "\n\nBASELINE AUTHORIZATION:\n"
                        + handoff(baseline)
                        + "\n\nPREVIOUS REVISION:\n"
                        + handoff(candidate)
                        + "\n\nAUTHORITATIVE SOURCE:\n"
                        + handoff(source)
                    ),
                    PLANNING_SCHEMA,
                    images=images,
                )

            authorization = self._run_structured_turn(
                project_id,
                "supervisor",
                plan_authorization_prompt(source, candidate),
                SUPERVISOR_PLAN_SCHEMA,
            )
            while True:
                current_brief, brief_guidance_revision, intake_changed = (
                    self._refresh_supervisor_intake_for_guidance(
                        project_id,
                        task,
                        images,
                        current_brief,
                        brief_guidance_revision,
                    )
                )
                current_brief = self._brief_with_verbatim_task(current_brief, task)
                if intake_changed or self._planning_guidance_pending(project_id):
                    baseline = authorization
                    break
                source = self._planning_required_source(project_id, current_brief)
                errors = list(
                    dict.fromkeys(
                        [
                            *self._validate_plan(authorization, task, source),
                            *self._plan_regression_errors(candidate, authorization),
                            *self._plan_regression_errors(baseline, authorization),
                        ]
                    )
                )[:96]
                if not errors:
                    return (
                        dict(candidate),
                        dict(authorization),
                        dict(current_brief),
                        brief_guidance_revision,
                    )
                previous = authorization
                authorization = self._run_structured_turn(
                    project_id,
                    "supervisor",
                    handoff(
                        "The guidance-driven authorization is invalid. Correct every "
                        "listed defect, preserve task_depth and all qualified detail, "
                        "and return the complete authorization without scene work."
                        "\n\nDEFECTS:\n- "
                        + "\n- ".join(errors)
                        + "\n\nPLANNING REVISION:\n"
                        + handoff(candidate)
                        + "\n\nPREVIOUS AUTHORIZATION:\n"
                        + handoff(previous)
                    ),
                    SUPERVISOR_PLAN_SCHEMA,
                )

    def _reference_prefetch_required(
        self,
        project_id: str,
        role: str,
        images: tuple[str, ...],
    ) -> bool:
        """Whether one read-only role needs a separate reference-intake Turn.

        Original references, later User guidance, and (for Visual Review) current
        captures are independent evidence sets.  When they do not fit in one
        request, the same native read-only Thread ingests the oldest still-due
        evidence first; its history then carries that evidence into the formal
        structured Turn.  Execution is deliberately excluded because a model Turn
        with its inherited scene-write capability must never be disguised as a
        read-only prefetch.
        """

        if role not in PROJECT_READ_ONLY_ROLES:
            return False
        with self._condition:
            project = self._require_project(project_id)
            record = self._thread(project, role)
            provided_images = images
            if (
                role in {"supervisor", "planning", "visual_review"}
                and record.get("_base_images_sent") is True
            ):
                references = set(self._project_reference_images_locked(project))
                provided_images = tuple(
                    path for path in images if path not in references
                )
            base_images = self._persistent_role_input_images_locked(
                project,
                role,
            )
            guidance_images = self._unconsumed_guidance_images_locked(
                project,
                role,
            )
        intake_images = tuple(dict.fromkeys((*base_images, *guidance_images)))
        if not intake_images:
            return False
        return len(dict.fromkeys((*provided_images, *intake_images))) > (
            _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE
        )

    def _run_structured_turn(
        self,
        project_id: str,
        role: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        images: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        current_prompt = prompt
        while True:
            if self._reference_prefetch_required(
                project_id,
                role,
                images,
            ):
                # This output is deliberately not an intake, plan, or review
                # decision. Its sole purpose is to place one bounded reference
                # batch in the same native read-only role Thread before the formal
                # role Turn. The provisional response is discarded.
                intake_prompt = handoff(
                    "REFERENCE INTAKE ONLY. Inspect and retain every attached "
                    "original or subsequently supplied authoritative User "
                    "reference image for the immediately following formal role "
                    "Turn in this same native Thread. Do not make the task's intake, "
                    "planning, review, or completion decision from this partial "
                    "batch; do not modify the HIP or call tools. Return one "
                    "schema-valid provisional response matching the supplied "
                    "output schema. The coordinator will discard this provisional "
                    "response and invoke the formal role Turn after every required "
                    "reference batch is retained."
                )
                while True:
                    try:
                        self._run_turn(
                            project_id,
                            role,
                            intake_prompt,
                            output_schema,
                            images=(),
                        )
                        break
                    except BridgeError as exc:
                        if exc.code not in {
                            "PROJECT_THREAD_OUTPUT_INVALID",
                            "PROJECT_COLLABORATION_CLAIM_UNSUPPORTED",
                        }:
                            raise
                        # A malformed response never advances the role's consumed
                        # guidance revision. Retry in the same native Thread; any
                        # acknowledged image input is already present in its history.
                        intake_prompt = handoff(
                            "REFERENCE INTAKE OUTPUT CORRECTION ONLY. Return a "
                            "schema-valid provisional response matching the supplied "
                            "output schema. Do not decide the task, call tools, or "
                            "modify the HIP. The previous provisional response was "
                            f"invalid: {exc.code}."
                        )
                continue
            try:
                return self._run_turn(
                    project_id,
                    role,
                    current_prompt,
                    output_schema,
                    images=images,
                )
            except BridgeError as exc:
                if (
                    role in PROJECT_READ_ONLY_ROLES
                    and exc.code == "PROJECT_GUIDANCE_IMAGE_QUEUE_INVALID"
                ):
                    # Guidance can arrive between the preflight check and exact
                    # turn/start construction. Re-evaluate from authoritative
                    # revisions and split it on the next loop iteration.
                    continue
                if exc.code not in {
                    "PROJECT_THREAD_OUTPUT_INVALID",
                    "PROJECT_COLLABORATION_CLAIM_UNSUPPORTED",
                }:
                    raise
                current_prompt = handoff(
                    "Correct this role output defect and return the entire current "
                    "output again under the same outputSchema. Do not change scope, "
                    "claim success without native events, or summarize away detail."
                    f"\n\nDEFECT:\n{exc.code}: {exc.message}"
                    "\n\nORIGINAL ROLE INPUT:\n"
                    + handoff(prompt)
                )

    def _review_until_valid(
        self,
        project_id: str,
        kind: str,
        supervisor: Mapping[str, Any],
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
        images: tuple[str, ...],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        role = "visual_review" if kind == "visual" else "technical_review"
        current = dict(candidate)
        while True:
            try:
                self._validate_review(kind, stage, current, execution)
                return current
            except BridgeError as exc:
                if not (
                    exc.code.startswith("PROJECT_REVIEW_")
                    or exc.code == "VISUAL_REVIEW_IMAGE_REQUIRED"
                ):
                    raise
                current = self._run_structured_turn(
                    project_id,
                    role,
                    handoff(
                        "Correct the deterministic review defect. Copy every original "
                        "stage claim exactly once, cite only matching current Execution "
                        "evidence ids, and choose repair when evidence is absent or "
                        "unverified. Return the whole review again."
                        f"\n\nDEFECT:\n{exc.code}: {exc.message}"
                        "\n\nPREVIOUS REVIEW:\n"
                        + handoff(current)
                        + "\n\nCURRENT REVIEW INPUT:\n"
                        + review_prompt(
                            kind,
                            supervisor,
                            stage,
                            execution,
                            has_images=bool(images),
                        )
                    ),
                    REVIEW_SCHEMA,
                    images=images,
                )

    def _rerun_selected_reviews(
        self,
        project_id: str,
        stale_roles: set[str],
        supervisor: Mapping[str, Any],
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
        review_images: tuple[str, ...],
        visual: Mapping[str, Any],
        technical: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Consume only stale reviewer guidance without another scene write."""

        current_visual, current_technical = dict(visual), dict(technical)
        if "visual_review" in stale_roles:
            current_visual = self._run_structured_turn(
                project_id,
                "visual_review",
                review_prompt(
                    "visual",
                    supervisor,
                    stage,
                    execution,
                    has_images=bool(review_images),
                ),
                REVIEW_SCHEMA,
                images=review_images,
            )
            current_visual = self._review_until_valid(
                project_id,
                "visual",
                supervisor,
                stage,
                execution,
                review_images,
                current_visual,
            )
        if "technical_review" in stale_roles:
            current_technical = self._run_structured_turn(
                project_id,
                "technical_review",
                review_prompt(
                    "technical",
                    supervisor,
                    stage,
                    execution,
                    has_images=bool(review_images),
                ),
                REVIEW_SCHEMA,
            )
            current_technical = self._review_until_valid(
                project_id,
                "technical",
                supervisor,
                stage,
                execution,
                (),
                current_technical,
            )
        return current_visual, current_technical

    def _supervisor_stage_decision(
        self,
        project_id: str,
        supervisor: Mapping[str, Any],
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
        visual: Mapping[str, Any],
        technical: Mapping[str, Any],
        *,
        execution_reported_blocked: bool,
    ) -> dict[str, Any]:
        """Return one evidence-bound Supervisor decision after both reviews."""

        if execution_reported_blocked:
            decision_input = handoff(
                blocked_prompt(supervisor, stage, execution)
                + "\n\nINDEPENDENT VISUAL BLOCKER REVIEW:\n"
                + handoff(visual)
                + "\n\nINDEPENDENT TECHNICAL BLOCKER REVIEW:\n"
                + handoff(technical)
            )
            decision_schema = SUPERVISOR_BLOCKED_SCHEMA
        else:
            decision_input = decision_prompt(
                supervisor,
                stage,
                execution,
                visual,
                technical,
            )
            decision_schema = SUPERVISOR_DECISION_SCHEMA
        while True:
            decision = self._run_structured_turn(
                project_id,
                "supervisor",
                decision_input,
                decision_schema,
            )
            verdict = decision.get("decision")
            unsupported_pass = verdict == "pass" and (
                visual.get("decision") != "pass"
                or technical.get("decision") != "pass"
            )
            unsupported_block = (
                verdict == "blocked"
                and not self._proven_external_dependency(decision, execution)
            )
            if not (unsupported_pass or unsupported_block):
                return dict(decision)
            if unsupported_pass:
                decision_input = handoff(
                    "Your pass is unsupported because an independent review did not "
                    "pass. Return repair; blocked additionally requires a proven "
                    "external dependency.\n\n"
                    + decision_prompt(
                        supervisor,
                        stage,
                        execution,
                        visual,
                        technical,
                    )
                )
            else:
                decision_input = handoff(
                    "Your blocked decision has no deterministic proof of a required "
                    "external change. Return repair, or provide the complete "
                    "external_dependency object with current Execution evidence "
                    "refs.\n\n"
                    + decision_prompt(
                        supervisor,
                        stage,
                        execution,
                        visual,
                        technical,
                    )
                )

    @staticmethod
    def _recoverable_evidence_error(exc: BridgeError) -> bool:
        return exc.code.startswith(
            (
                "PROJECT_TECHNICAL_EVIDENCE_",
                "PROJECT_REVIEW_IMAGE_",
                "PROJECT_REVIEW_IMAGES_",
            )
        )

    @classmethod
    def _proven_external_dependency(
        cls,
        decision: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> bool:
        dependency = decision.get("external_dependency")
        if not isinstance(dependency, Mapping) or dependency.get("proven") is not True:
            return False
        for field in (
            "required_external_change",
            "observed_blocker",
            "why_codex_cannot_resolve",
        ):
            if not cls._meaningful(dependency.get(field)):
                return False
        refs = dependency.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(
            not isinstance(value, str) or not value for value in refs
        ):
            return False
        bridge = execution.get("_bridge_evidence")
        tools = bridge.get("tools") if isinstance(bridge, Mapping) else None
        if not isinstance(tools, Mapping) or any(ref not in tools for ref in refs):
            return False
        blocker_text = " ".join(
            str(dependency.get(field, ""))
            for field in ("observed_blocker", "why_codex_cannot_resolve")
        )
        blocker_anchors = cls._review_anchors(blocker_text)
        for ref in refs:
            tool = tools.get(ref)
            if not isinstance(tool, Mapping):
                return False
            projection = tool.get("projection")
            rendered = json.dumps(projection, ensure_ascii=False).casefold()
            status = str(tool.get("status", "")).casefold()
            visibly_failed = (
                tool.get("ok") is False
                or status in {"failed", "error", "cancelled", "canceled"}
            ) and any(
                marker in rendered
                for marker in (
                    "error",
                    "failed",
                    "unavailable",
                    "unreachable",
                    "not found",
                    "invalid session",
                    "错误",
                    "失败",
                    "不可用",
                    "不可达",
                    "会话",
                    "模块",
                    "身份",
                )
            )
            if not visibly_failed:
                return False
            projection_anchors = cls._review_anchors(rendered)
            if len(blocker_anchors & projection_anchors) < 2:
                return False
        return True

    def _wait(self, pending: _PendingTurn) -> dict[str, Any]:
        deadline = pending.created_monotonic + self._turn_timeout
        with self._condition:
            while not pending.completed and (remaining := deadline - time.monotonic()) > 0:
                self._condition.wait(remaining)
            completed, status = pending.completed, pending.status
            error, final_text = pending.error, pending.final_text()
            project = self._project(pending.project_id)
            if self._pending.get(pending.thread_id) is pending:
                self._pending.pop(pending.thread_id, None)
            self._condition.notify_all()
        if project is None or project.get("status") != _RUNNING:
            raise BridgeError(
                "PROJECT_THREAD_INTERRUPTED",
                error or "The project was interrupted",
                http_status=409,
            )
        if not completed:
            if pending.turn_id is not None:
                if pending.role == "supervisor":
                    with self._condition:
                        self._suppress_root_turn_locked(
                            pending.thread_id, pending.turn_id
                        )
                try:
                    self._client.request(
                        "turn/interrupt",
                        {"threadId": pending.thread_id, "turnId": pending.turn_id},
                    )
                except Exception:
                    pass
            raise BridgeError(
                "PROJECT_THREAD_TURN_TIMEOUT",
                f"The {pending.role} Turn did not complete before its timeout",
                http_status=504,
            )
        if status != "completed" or final_text is None:
            raise BridgeError(
                "PROJECT_THREAD_TURN_FAILED",
                error or f"The {pending.role} Turn ended with status {status}",
                http_status=502,
            )
        try:
            value = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise BridgeError(
                "PROJECT_THREAD_OUTPUT_INVALID",
                f"The {pending.role} Turn did not return JSON",
                http_status=502,
            ) from exc
        if not isinstance(value, dict):
            raise BridgeError(
                "PROJECT_THREAD_OUTPUT_INVALID",
                f"The {pending.role} output is not an object",
                http_status=502,
            )
        actual_mode = (
            "used-with-real-events"
            if pending.collaboration
            else "serial-fallback"
        )
        # Collaboration is observed by the Bridge, not decided by the model.
        # Structured output can still contain an invalid or stale enum value;
        # deriving it here prevents a pointless correction Turn (or artificial
        # subagent calls made only to satisfy the field) while keeping the
        # reported mode exactly tied to native collaboration events.
        value["collaboration_mode"] = actual_mode
        collaboration = {
            "mode": actual_mode,
            "events": copy_collaboration(pending.collaboration),
        }
        value["_bridge_evidence"] = {
            "tools": {
                key: dict(record)
                for key, record in pending.tool_evidence.items()
            },
            "tools_truncated": pending.tools_truncated,
            "collaboration": collaboration,
        }
        with self._condition:
            project = self._project(pending.project_id)
            if project is not None:
                self._thread(project, pending.role)["collaboration"] = collaboration
                self._touch(project)
        return value

    def _turn_params(
        self,
        project: Mapping[str, Any],
        role: str,
        prompt: str,
        output_schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        read_only, record = role in PROJECT_READ_ONLY_ROLES, self._thread(project, role)
        params: dict[str, Any] = {
            "threadId": record["thread_id"],
            "input": [{"type": "text", "text": handoff(prompt)}],
            "cwd": str(self._project_root),
            "approvalPolicy": "never" if read_only else "on-request",
            "sandboxPolicy": (
                {"type": "readOnly", "networkAccess": False}
                if read_only
                else {"type": "workspaceWrite", "networkAccess": False}
            ),
        }
        if output_schema is not None:
            params["outputSchema"] = dict(output_schema)
        for key, field in (
            ("model", "model"),
            ("effort", "_effort"),
            ("serviceTier", "_service_tier"),
        ):
            pending_field = "_next_" + field.lstrip("_")
            value = record.get(pending_field, record.get(field, project.get(field)))
            if value is not None:
                params[key] = value
        return params

    @staticmethod
    def _guidance_input(
        text: str,
        local_image_paths: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        turn_input: list[dict[str, Any]] = []
        if text.strip():
            turn_input.append(
                {
                    "type": "text",
                    "text": text,
                    "text_elements": [],
                }
            )
        turn_input.extend(
            {"type": "localImage", "path": path}
            for path in local_image_paths
        )
        return turn_input

    def _with_user_guidance(
        self,
        project_id: str,
        role: str,
        prompt: str,
        images: tuple[str, ...],
    ) -> tuple[str, tuple[str, ...], int]:
        """Attach one bounded prefix of authoritative guidance to a role Turn.

        A native app-server request may carry at most sixteen unique images, but a
        project Thread can retain earlier reference-intake Turns in its history.
        Select whole guidance capsules in revision order so a completed Turn never
        claims to have consumed an attachment that was not actually submitted.
        """

        with self._condition:
            project = self._project(project_id)
            raw = project.get("_user_guidance", []) if project is not None else []
            revisions = (
                project.get("_role_guidance_revisions")
                if project is not None
                else None
            )
            consumed_revision = (
                int(revisions.get(role, 0))
                if isinstance(revisions, Mapping)
                and isinstance(revisions.get(role, 0), int)
                else 0
            )
            applicable_items = [
                item
                for item in raw
                if isinstance(item, Mapping)
                and item.get("target_role") in {None, role}
                and isinstance(item.get("revision"), int)
                and int(item["revision"]) > 0
            ]
            effective_images: tuple[str, ...] = images
            if project is not None:
                record = self._thread(project, role)
                references = set(self._project_reference_images_locked(project))
                provided_images = images
                if (
                    role in {"supervisor", "planning", "visual_review"}
                    and record.get("_base_images_sent") is True
                ):
                    provided_images = tuple(
                        path for path in images if path not in references
                    )
                effective_images = tuple(
                    dict.fromkeys(
                        (
                            *self._persistent_role_input_images_locked(
                                project,
                                role,
                            ),
                            *provided_images,
                        )
                    )
                )
            if len(effective_images) > _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE:
                raise BridgeError(
                    "PROJECT_GUIDANCE_IMAGE_QUEUE_INVALID",
                    "Base references and explicit role images exceed one Codex Turn",
                    http_status=503,
                    details={
                        "role": role,
                        "max_unique_images": _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE,
                    },
                )

            combined_images = list(effective_images)
            covered_revision = consumed_revision
            for item in applicable_items:
                revision = int(item["revision"])
                if revision <= consumed_revision:
                    continue
                item_images = tuple(
                    dict.fromkeys(
                        path
                        for path in item.get("local_image_paths", ())
                        if isinstance(path, str)
                    )
                )
                candidate = list(combined_images)
                for path in item_images:
                    if path not in candidate:
                        candidate.append(path)
                if len(candidate) > _MAX_PENDING_GUIDANCE_IMAGES_PER_ROLE:
                    break
                combined_images = candidate
                covered_revision = max(covered_revision, revision)

            capsules = [
                {
                    "guidance_id": item.get("guidance_id"),
                    "revision": item.get("revision"),
                    "user_text": item.get("text"),
                    "attachment_count": (
                        len(
                            tuple(
                                dict.fromkeys(
                                    path
                                    for path in item.get("local_image_paths", ())
                                    if isinstance(path, str)
                                )
                            )
                        )
                        if consumed_revision
                        < int(item["revision"])
                        <= covered_revision
                        else 0
                    ),
                    "target_role": item.get("target_role"),
                    "target_thread_id": item.get("target_thread_id"),
                }
                for item in applicable_items
                if int(item["revision"]) <= covered_revision
            ]
        if not capsules:
            return prompt, effective_images, covered_revision
        return (
            handoff(
                prompt
                + "\n\nAUTHORITATIVE USER GUIDANCE ACCEPTED AFTER PROJECT START:\n"
                + handoff(capsules)
                + "\nTreat these as User facts. They override older assumptions; "
                "apply them only within this role's authority and return them in the "
                "role's structured output so the existing coordinator handoff can "
                "carry resulting facts downstream. Project-wide guidance applies to "
                "every role; role-targeted guidance must not directly contaminate an "
                "unrelated role."
            ),
            tuple(combined_images),
            covered_revision,
        )

    def _validate_plan(
        self,
        plan: Mapping[str, Any],
        task: str,
        required_source: Mapping[str, Any],
    ) -> list[str]:
        """Check depth-adaptive executable blueprint invariants before the first write."""

        errors: list[str] = []
        if not isinstance(plan.get("project_title"), str) or not plan["project_title"].strip():
            errors.append("project_title must be a natural non-empty title")
        if not self._meaningful(plan.get("summary")):
            errors.append("summary is vague or empty")
        required_depth = required_source.get("task_depth")
        plan_depth = plan.get("task_depth")
        if required_depth not in {"direct", "focused", "full"}:
            errors.append("authoritative Supervisor intake task_depth is invalid")
        if plan_depth not in {"direct", "focused", "full"}:
            errors.append("task_depth is missing or invalid")
        elif plan_depth != required_depth:
            errors.append(
                "task_depth must exactly match the authoritative Supervisor intake: "
                f"{required_depth}"
            )
        is_full = required_depth == "full"
        if plan.get("collaboration_mode") not in {
            "used-with-real-events",
            "serial-fallback",
        }:
            errors.append("collaboration_mode is missing")
        for field in (
            "user_facts",
            "reference_observations",
            "codex_assumptions",
            "verified_scene_facts",
            "hard_constraints",
            "acceptance",
        ):
            values = plan.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                errors.append(f"{field} must be a clean provenance list")
            if field in {"user_facts", "hard_constraints", "acceptance"} and not values:
                errors.append(f"{field} must not be empty")
        for field in (
            "user_facts",
            "reference_observations",
            "hard_constraints",
            "acceptance",
        ):
            expected = required_source.get(field)
            actual = plan.get(field)
            normalized = {
                self._normalized_verbatim_text(value)
                for value in actual
                if isinstance(actual, list) and isinstance(value, str)
            }
            for value in expected if isinstance(expected, list) else []:
                if not isinstance(value, str):
                    continue
                if (
                    field == "user_facts"
                    and value.startswith("User task verbatim:\n")
                ):
                    # The raw task is checked as one complete normalized sequence below.
                    # Do not require Planning to retain this internal provenance label.
                    continue
                if self._normalized_verbatim_text(value) not in normalized:
                    errors.append(f"authorized plan dropped {field}: {value[:120]}")
        normalized_task = self._normalized_verbatim_text(task)
        normalized_user_facts = [
            self._normalized_verbatim_text(value)
            for value in plan.get("user_facts", [])
            if isinstance(value, str)
        ]
        if normalized_task and not any(
            normalized_task in fact for fact in normalized_user_facts
        ):
            errors.append(
                "authorized plan user_facts do not preserve the complete original user task"
            )
        assumptions = " ".join(
            value for value in plan.get("codex_assumptions", []) if isinstance(value, str)
        ).casefold()
        if any(
            marker in assumptions
            for marker in (
                "忽略用户",
                "覆盖用户事实",
                "放宽用户禁止",
                "override the user",
                "ignore the user",
                "user fact may be ignored",
            )
        ):
            errors.append("Codex assumptions attempt to override a User fact")
        stages = plan.get("stages")
        if not isinstance(stages, list) or not stages:
            return errors + ["authorized plan has no stages"]
        if is_full and len(stages) < 3:
            errors.append("full project blueprint must separate construction into multiple semantic stages")
        if is_full and self._information_units(plan) < 10_000:
            errors.append(
                "authorized blueprint needs at least 10000 task-specific information units; "
                "length is auxiliary to the semantic, provenance, and repetition checks"
            )
        anchors = self._task_anchors(task)
        seen_titles: set[str] = set()
        seen_steps: list[str] = []
        array_fields = (
            "prerequisites",
            "inputs",
            "native_node_strategy",
            "authoring_batches",
            "parameter_dependencies",
            "outputs",
            "visible_characteristics",
            "structural_relationships",
            "prohibitions",
            "technical_evidence",
            "visual_evidence",
            "reviewers",
            "failure_minimum_repair",
            "downstream_contract",
        )
        step_fields = (
            "responsibility",
            "path_or_network_region",
            "native_operation_family",
            "inputs_and_connections",
            "key_parameters",
            "expected_result",
            "next_step_evidence",
            "failure_minimum_repair",
        )
        for index, stage in enumerate(stages, 1):
            if not isinstance(stage, Mapping):
                errors.append(f"stage {index} is not an object")
                continue
            title = stage.get("title")
            if not self._meaningful(title):
                errors.append(f"stage {index} title is vague")
                title = f"stage {index}"
            normalized_title = self._normalized_text(title)
            if normalized_title in seen_titles:
                errors.append(f"stage {index} duplicates another natural title")
            seen_titles.add(normalized_title)
            if not self._meaningful(stage.get("objective")):
                errors.append(f"stage {index} objective is vague")
            corpus = json.dumps(stage, ensure_ascii=False).casefold()
            if is_full and self._information_units(stage) < 2_500:
                errors.append(
                    f"stage {index} lacks the detailed construction information required for a Full blueprint"
                )
            if anchors and not any(anchor in corpus for anchor in anchors):
                errors.append(f"stage {index} is not traceable to this task")
            for field in array_fields:
                values = stage.get(field)
                if not isinstance(values, list) or not values or any(
                    not self._meaningful(value) for value in values
                ):
                    errors.append(f"stage {index} {field} is empty or generic")
            reviewers = " ".join(
                value for value in stage.get("reviewers", []) if isinstance(value, str)
            ).casefold()
            if not (
                ("visual" in reviewers or "视觉" in reviewers)
                and ("technical" in reviewers or "技术" in reviewers)
            ):
                errors.append(f"stage {index} does not name both independent reviewers")
            steps = stage.get("ordered_construction_steps")
            if not isinstance(steps, list) or not steps:
                errors.append(f"stage {index} needs at least one bounded construction step")
                continue
            if is_full and len(steps) < 3:
                errors.append(f"stage {index} needs multiple bounded construction steps")
            for step_index, step in enumerate(steps, 1):
                if not isinstance(step, Mapping):
                    errors.append(f"stage {index} step {step_index} is not an object")
                    continue
                for field in step_fields:
                    if not self._meaningful(step.get(field)):
                        errors.append(
                            f"stage {index} step {step_index} {field} is empty or generic"
                        )
                    elif is_full and self._information_units(step.get(field)) < 24:
                        errors.append(
                            f"stage {index} step {step_index} {field} lacks executable detail"
                        )
                if is_full and self._information_units(step) < 350:
                    errors.append(
                        f"stage {index} step {step_index} lacks enough task-specific execution information"
                    )
                path = str(step.get("path_or_network_region", ""))
                if "/" not in path and not re.search(
                    r"\b(?:sop|lop|dop|vop|network|context)\b|网络", path, re.I
                ):
                    errors.append(f"stage {index} step {step_index} lacks a precise network region")
                connections = str(step.get("inputs_and_connections", ""))
                if not re.search(r"连接|接入|输入|输出|merge|connect|->|→", connections, re.I):
                    errors.append(f"stage {index} step {step_index} lacks connection detail")
                parameters = str(step.get("key_parameters", ""))
                if not re.search(r"=|参数|依赖|驱动|parameter|depends", parameters, re.I):
                    errors.append(f"stage {index} step {step_index} lacks parameter dependency detail")
                normalized_step = self._normalized_text(json.dumps(step, ensure_ascii=False))
                if any(
                    normalized_step == previous
                    or SequenceMatcher(None, normalized_step, previous).ratio() > 0.92
                    for previous in seen_steps
                ):
                    errors.append(f"stage {index} step {step_index} repeats a mechanical template")
                seen_steps.append(normalized_step)
                compact = self._normalized_text("".join(str(value) for value in step.values()))
                if re.fullmatch(r"(.{2,32})\1{3,}", compact):
                    errors.append(f"stage {index} step {step_index} mechanically pads one phrase")
                authoring = (
                    str(step.get("native_operation_family", ""))
                    + " "
                    + " ".join(
                        value
                        for value in stage.get("authoring_batches", [])
                        if isinstance(value, str)
                    )
                ).casefold()
                if re.search(
                    r"单次.{0,12}(?:hom|脚本).{0,12}(?:全资产|全部)|"
                    r"one giant hom|whole.asset.{0,12}(?:hom|script)",
                    authoring,
                ):
                    errors.append(f"stage {index} step {step_index} requests a giant all-asset batch")
            disposition = stage.get("evidence_disposition")
            if not isinstance(disposition, Mapping) or set(disposition) != {
                "technical",
                "visual",
                "stage",
            }:
                errors.append(f"stage {index} evidence_disposition is incomplete")
            for field in ("technical_evidence", "visual_evidence"):
                values = stage.get(field)
                if is_full and (
                    not isinstance(values, list) or len(set(values)) < 2
                ):
                    errors.append(f"stage {index} {field} needs distinct claim-specific evidence")
        return list(dict.fromkeys(errors))[:96]

    @classmethod
    def _plan_regression_errors(
        cls,
        previous: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        allow_depth_correction: bool = False,
    ) -> list[str]:
        """Reject repair outputs that buy one fix by deleting prior blueprint detail."""

        errors: list[str] = []
        previous_depth = previous.get("task_depth")
        candidate_depth = candidate.get("task_depth")
        if previous_depth != candidate_depth:
            if allow_depth_correction:
                return []
            return [
                "Planning repair changed the authorized task_depth from "
                f"{previous_depth} to {candidate_depth}"
            ]
        if cls._information_units(candidate) < cls._information_units(previous):
            errors.append(
                "Planning repair regressed below the previous blueprint's total "
                "task-specific information"
            )
        previous_stages = previous.get("stages")
        candidate_stages = candidate.get("stages")
        if not isinstance(previous_stages, list) or not isinstance(candidate_stages, list):
            return errors
        if len(candidate_stages) < len(previous_stages):
            errors.append("Planning repair removed a previously defined semantic stage")
        for stage_index, (old_stage, new_stage) in enumerate(
            zip(previous_stages, candidate_stages), 1
        ):
            if not isinstance(old_stage, Mapping) or not isinstance(new_stage, Mapping):
                continue
            if cls._information_units(new_stage) < cls._information_units(old_stage):
                errors.append(
                    f"Planning repair regressed stage {stage_index} below its previous detail"
                )
            old_steps = old_stage.get("ordered_construction_steps")
            new_steps = new_stage.get("ordered_construction_steps")
            if not isinstance(old_steps, list) or not isinstance(new_steps, list):
                continue
            if len(new_steps) < len(old_steps):
                errors.append(
                    f"Planning repair removed a construction step from stage {stage_index}"
                )
            for step_index, (old_step, new_step) in enumerate(
                zip(old_steps, new_steps), 1
            ):
                if cls._information_units(new_step) < cls._information_units(old_step):
                    errors.append(
                        "Planning repair regressed stage "
                        f"{stage_index} step {step_index} below its previous detail"
                    )
        return list(dict.fromkeys(errors))[:96]

    @staticmethod
    def _normalized_text(value: Any) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())

    @staticmethod
    def _normalized_verbatim_text(value: Any) -> str:
        """Normalize exact user-authored content without dropping scripts or emoji."""

        return re.sub(
            r"\s+",
            "",
            unicodedata.normalize("NFKC", str(value)).casefold(),
        )

    @classmethod
    def _brief_with_verbatim_task(
        cls,
        brief: Mapping[str, Any],
        task: str,
    ) -> dict[str, Any]:
        """Bind raw user text as data so model summarization cannot drop it."""

        value = dict(brief)
        facts = [
            item
            for item in brief.get("user_facts", [])
            if isinstance(item, str) and item.strip()
        ]
        task_fact = "User task verbatim:\n" + task
        normalized = cls._normalized_verbatim_text(task_fact)
        if normalized not in {
            cls._normalized_verbatim_text(item) for item in facts
        }:
            facts.append(task_fact)
        value["user_facts"] = facts
        return value

    @classmethod
    def _information_units(cls, value: Any) -> int:
        """Count useful visible content without rewarding JSON field-name padding."""

        if isinstance(value, Mapping):
            return sum(
                cls._information_units(child)
                for key, child in value.items()
                if not str(key).startswith("_")
                and str(key) != "collaboration_mode"
            )
        if isinstance(value, list):
            return sum(cls._information_units(child) for child in value)
        if not isinstance(value, str):
            return 0
        cjk = len(re.findall(r"[\u4e00-\u9fff]", value))
        technical = sum(
            len(token)
            for token in re.findall(r"[A-Za-z0-9/._=:\\-]+", value)
        )
        return cjk + technical

    @staticmethod
    def _meaningful(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = " ".join(value.split()).strip()
        if len(normalized) < 8:
            return False
        vague = re.fullmatch(
            r"(?:完善|优化|调整|处理|检查|确保|提升|改进)?"
            r"(?:细节|质量|效果|材质|结构|问题|内容|结果)[。.!！ ]*|"
            r"(?:tbd|todo|as needed|improve details|optimi[sz]e material|check quality)[. ]*",
            normalized,
            re.I,
        )
        return vague is None

    @staticmethod
    def _task_anchors(task: str) -> set[str]:
        anchors = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", task)
            if token.casefold() not in {"create", "build", "make", "please", "houdini"}
        }
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", task):
            for size in (2, 3, 4):
                anchors.update(
                    sequence[index : index + size]
                    for index in range(max(0, len(sequence) - size + 1))
                )
        anchors.difference_update({"建一", "一个", "个木", "创建", "制作", "请建", "请创"})
        return anchors

    def _review_images(self, execution: Mapping[str, Any]) -> tuple[str, ...]:
        evidence = execution.get("technical_evidence")
        required = {
            "id",
            "tool_item_id",
            "claim",
            "scope_or_path",
            "frame_or_time",
            "observation_or_measurement",
            "source",
            "result",
        }
        bridge = execution.get("_bridge_evidence")
        tools = bridge.get("tools") if isinstance(bridge, Mapping) else None
        if not isinstance(tools, Mapping):
            tools = {}
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(item, Mapping)
            or any(
                not isinstance(item.get(field), str) or not item[field].strip()
                for field in required
            )
            for item in evidence
        ):
            raise BridgeError(
                "PROJECT_TECHNICAL_EVIDENCE_REQUIRED",
                "Executor must return complete claim-specific technical evidence",
                http_status=502,
            )
        technical_ids: set[str] = set()
        for item in evidence:
            evidence_id, tool_item_id = item["id"], item["tool_item_id"]
            tool = tools.get(tool_item_id)
            if (
                evidence_id in technical_ids
                or not isinstance(tool, Mapping)
                or tool.get("status") != "completed"
                or tool.get("ok") is not True
                or not isinstance(tool.get("tool"), str)
                or not tool["tool"].startswith("hia_")
                or tool["tool"] not in item["source"]
            ):
                raise BridgeError(
                    "PROJECT_TECHNICAL_EVIDENCE_UNBOUND",
                    "Technical evidence must cite a unique successful HIA tool item from this Execution Turn",
                    http_status=502,
                )
            technical_ids.add(evidence_id)
            projection = tool.get("projection")
            if not isinstance(projection, Mapping) or not self._technical_projection_supports(
                item, projection
            ):
                raise BridgeError(
                    "PROJECT_TECHNICAL_EVIDENCE_UNRELATED",
                    "Technical evidence scope, frame, or observation is not supported by its cited HIA result projection",
                    http_status=502,
                )
        values = execution.get("review_images")
        if not isinstance(values, list) or len(values) > 16:
            raise BridgeError(
                "PROJECT_REVIEW_IMAGES_INVALID",
                "Executor review_images must be a bounded array",
                http_status=502,
            )
        image_ids: set[str] = set()
        resolved: list[str] = []
        for value in values:
            if not isinstance(value, Mapping) or any(
                not isinstance(value.get(field), str) or not value[field].strip()
                for field in ("id", "capture_tool_item_id", "path", "frame_or_time")
            ):
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGES_INVALID",
                    "Executor returned an invalid review image path",
                    http_status=502,
                )
            if value["id"] in image_ids:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGES_INVALID",
                    "Executor review image evidence ids must be unique",
                    http_status=502,
                )
            image_ids.add(value["id"])
            tool = tools.get(value["capture_tool_item_id"])
            capture = tool.get("capture") if isinstance(tool, Mapping) else None
            if (
                not isinstance(tool, Mapping)
                or tool.get("tool") != "hia_capture_viewport"
                or tool.get("status") != "completed"
                or not isinstance(capture, Mapping)
            ):
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_UNBOUND",
                    "Review images must cite a successful viewport capture from this Execution Turn",
                    http_status=502,
                )
            endpoints = capture.get("endpoints")
            candidates: list[dict[str, Any]] = []
            if isinstance(endpoints, list):
                for endpoint in endpoints[:2]:
                    if (
                        not isinstance(endpoint, Mapping)
                        or endpoint.get("endpoint") not in {"first", "last"}
                        or endpoint.get("status") != "captured"
                        or not isinstance(endpoint.get("evidence_path"), str)
                    ):
                        continue
                    candidates.append(
                        {
                            **{
                                key: capture.get(key)
                                for key in ("storage_scope", "source_hip_path")
                            },
                            **dict(endpoint),
                            "absolute_path": endpoint["evidence_path"],
                        }
                    )
            elif tool.get("ok") is True:
                candidates.append(dict(capture))
            matched: list[tuple[Path, Mapping[str, Any]]] = []
            for candidate in candidates:
                try:
                    image = self._validated_capture_path(value["path"], candidate)
                except BridgeError as exc:
                    if exc.code == "PROJECT_REVIEW_IMAGE_UNBOUND":
                        continue
                    raise
                matched.append((image, candidate))
            if len(matched) != 1:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_UNBOUND",
                    "Review image path must match exactly one successful capture endpoint",
                    http_status=502,
                )
            image, matched_capture = matched[0]
            captured_frame = matched_capture.get(
                "actual_frame", matched_capture.get("requested_frame")
            )
            if captured_frame is not None and not self._frame_claim_matches(
                value["frame_or_time"], captured_frame
            ):
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_UNBOUND",
                    "Review image frame does not match its captured endpoint frame",
                    http_status=502,
                )
            if not image.is_file() or image.suffix.lower() not in _IMAGE_SUFFIXES:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGES_INVALID",
                    "Review image type is not supported",
                    http_status=502,
                )
            with image.open("rb") as stream:
                header = stream.read(12)
            valid = (
                header.startswith(b"\x89PNG\r\n\x1a\n")
                or header.startswith(b"\xff\xd8\xff")
                or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
            )
            if not valid:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGES_INVALID",
                    "Review image content does not match a supported image type",
                    http_status=502,
                )
            rendered = str(image)
            if rendered not in resolved:
                resolved.append(rendered)
        return tuple(resolved)

    @staticmethod
    def _frame_claim_matches(claim: str, captured_frame: Any) -> bool:
        try:
            captured = float(captured_frame)
        except (TypeError, ValueError):
            return False
        for token in re.findall(r"-?\d+(?:\.\d+)?", claim):
            try:
                if abs(float(token) - captured) <= 1e-6:
                    return True
            except ValueError:
                continue
        return False

    @classmethod
    def _technical_projection_supports(
        cls, evidence: Mapping[str, Any], projection: Mapping[str, Any]
    ) -> bool:
        flattened = list(cls._projection_scalars(projection))
        paths = [value for key, value in flattened if isinstance(value, str) and "/" in value]
        claimed_path = str(evidence.get("scope_or_path", "")).rstrip("/")
        if not claimed_path or not any(
            claimed_path == path.rstrip("/")
            or claimed_path.startswith(path.rstrip("/") + "/")
            or path.rstrip("/").startswith(claimed_path + "/")
            for path in paths
        ):
            return False
        frame_claim = str(evidence.get("frame_or_time", ""))
        claimed_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", frame_claim))
        frame_values = {
            str(value)
            for key, value in flattened
            if re.search(r"frame|time", key, re.I)
            and isinstance(value, (str, int, float))
        }
        if claimed_numbers and not any(
            number in value for number in claimed_numbers for value in frame_values
        ):
            return False
        observation = str(evidence.get("observation_or_measurement", ""))
        projected_text = json.dumps(projection, ensure_ascii=False).casefold()
        numeric_claims = set(re.findall(r"-?\d+(?:\.\d+)?", observation))
        if numeric_claims and any(number not in projected_text for number in numeric_claims):
            return False
        anchors = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", observation)
        }
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", observation):
            anchors.update(
                sequence[index : index + 2]
                for index in range(len(sequence) - 1)
            )
        anchors = {value for value in anchors if len(value) >= 2}
        return bool(anchors) and sum(value in projected_text for value in anchors) >= min(
            2, len(anchors)
        )

    @classmethod
    def _projection_scalars(
        cls, value: Any, prefix: str = ""
    ) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        if isinstance(value, Mapping):
            for key, child in value.items():
                rendered = f"{prefix}.{key}" if prefix else str(key)
                result.extend(cls._projection_scalars(child, rendered))
        elif isinstance(value, list):
            for child in value:
                result.extend(cls._projection_scalars(child, prefix))
        else:
            result.append((prefix, value))
        return result

    def _validated_capture_path(
        self, claimed_path: str, capture: Mapping[str, Any]
    ) -> Path:
        absolute = capture.get("absolute_path")
        if not isinstance(absolute, str) or not Path(claimed_path).is_absolute():
            raise BridgeError(
                "PROJECT_REVIEW_IMAGE_UNBOUND",
                "Review image path must equal the absolute path returned by HIA capture",
                http_status=502,
            )
        raw_claimed = Path(os.path.abspath(os.path.normpath(claimed_path)))
        raw_captured = Path(os.path.abspath(os.path.normpath(absolute)))
        if os.path.normcase(str(raw_claimed)) != os.path.normcase(str(raw_captured)):
            raise BridgeError(
                "PROJECT_REVIEW_IMAGE_UNBOUND",
                "Review image path was not the path returned by the cited HIA capture",
                http_status=502,
            )
        try:
            claimed = raw_claimed.resolve(strict=True)
            captured = raw_captured.resolve(strict=True)
        except OSError as exc:
            raise BridgeError(
                "PROJECT_REVIEW_IMAGES_INVALID",
                "Captured review image no longer exists",
                http_status=502,
            ) from exc
        if claimed != captured:
            raise BridgeError(
                "PROJECT_REVIEW_IMAGE_UNBOUND",
                "Review image path was not returned by the cited HIA capture",
                http_status=502,
            )
        scope = capture.get("storage_scope")
        if scope == "runtime_fallback":
            raw_allowed = Path(
                os.path.abspath(
                    os.path.normpath(
                        str(self._project_root / ".runtime" / "cache" / "screenshots")
                    )
                )
            )
            try:
                raw_claimed.relative_to(raw_allowed)
            except ValueError as exc:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_OUTSIDE_SCOPE",
                    "Runtime capture is outside project .runtime/cache/screenshots",
                    http_status=502,
                ) from exc
            self._require_ordinary_chain(raw_claimed, raw_allowed)
            allowed = raw_allowed.resolve(strict=True)
            try:
                claimed.relative_to(allowed)
            except ValueError as exc:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_OUTSIDE_SCOPE",
                    "Runtime capture is outside project .runtime/cache/screenshots",
                    http_status=502,
                ) from exc
            if capture.get("source_hip_path") is not None:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_SCOPE_INVALID",
                    "Runtime capture must not claim a source HIP",
                    http_status=502,
                )
            return claimed
        if scope == "hip":
            source = capture.get("source_hip_path")
            if not isinstance(source, str):
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_SOURCE_HIP_REQUIRED",
                    "HIP-local capture requires the source HIP path returned by HIA",
                    http_status=502,
                )
            raw_hip = Path(os.path.abspath(os.path.normpath(source)))
            raw_allowed = raw_hip.parent / ".hia" / "screenshots"
            try:
                raw_claimed.relative_to(raw_allowed)
            except ValueError as exc:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_OUTSIDE_SCOPE",
                    "HIP capture is not under its source HIP sibling .hia/screenshots",
                    http_status=502,
                ) from exc
            self._require_ordinary_chain(raw_hip, raw_hip.parent)
            self._require_ordinary_chain(raw_claimed, raw_allowed)
            try:
                hip = raw_hip.resolve(strict=True)
                allowed = raw_allowed.resolve(strict=True)
                claimed.relative_to(allowed)
            except (OSError, ValueError) as exc:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_OUTSIDE_SCOPE",
                    "HIP capture is not under its source HIP sibling .hia/screenshots",
                    http_status=502,
                ) from exc
            if not hip.is_file() or hip.suffix.casefold() not in {".hip", ".hiplc", ".hipnc"}:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_SOURCE_HIP_INVALID",
                    "HIA capture source HIP is not an ordinary saved HIP file",
                    http_status=502,
                )
            return claimed
        raise BridgeError(
            "PROJECT_REVIEW_IMAGE_SCOPE_INVALID",
            "HIA capture returned an unsupported storage scope",
            http_status=502,
        )

    @staticmethod
    def _require_ordinary_chain(path: Path, stop: Path) -> None:
        current = path
        while True:
            try:
                metadata = os.lstat(current)
            except OSError as exc:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGES_INVALID",
                    "Review evidence path component is not readable",
                    http_status=502,
                ) from exc
            is_reparse = bool(
                getattr(metadata, "st_file_attributes", 0)
                & _FILE_ATTRIBUTE_REPARSE_POINT
            )
            if current.is_symlink() or is_reparse:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_SYMLINK_REJECTED",
                    "Review evidence path must not traverse a symlink, junction, or reparse point",
                    http_status=502,
                )
            if current == stop:
                return
            if stop not in current.parents:
                raise BridgeError(
                    "PROJECT_REVIEW_IMAGE_OUTSIDE_SCOPE",
                    "Review evidence path left its verified storage scope",
                    http_status=502,
                )
            current = current.parent

    def _validate_review(
        self,
        kind: str,
        stage: Mapping[str, Any],
        review: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> None:
        field = "visual_evidence" if kind == "visual" else "technical_evidence"
        lane = stage.get(field)
        if not isinstance(lane, list) or not lane or any(
            not isinstance(value, str) or not value.strip() for value in lane
        ):
            raise BridgeError(
                "PROJECT_REVIEW_STAGE_CLAIMS_INVALID",
                f"Stage {kind} evidence lane is invalid",
                http_status=502,
            )
        claims = review.get("claims")
        if not isinstance(claims, list) or not claims:
            raise BridgeError(
                "PROJECT_REVIEW_CLAIMS_REQUIRED",
                f"{kind} review returned no claim dispositions",
                http_status=502,
            )
        by_stage_claim: dict[str, Mapping[str, Any]] = {}
        for claim in claims:
            if not isinstance(claim, Mapping):
                raise BridgeError(
                    "PROJECT_REVIEW_CLAIMS_REQUIRED",
                    f"{kind} review returned an invalid claim disposition",
                    http_status=502,
                )
            stage_claim = claim.get("stage_claim")
            if (
                not isinstance(stage_claim, str)
                or stage_claim not in lane
                or stage_claim in by_stage_claim
            ):
                raise BridgeError(
                    "PROJECT_REVIEW_CLAIM_COVERAGE_INVALID",
                    f"{kind} review must map each exact stage claim once without additions",
                    http_status=502,
                )
            by_stage_claim[stage_claim] = claim
        if set(by_stage_claim) != set(lane) or len(by_stage_claim) != len(lane):
            raise BridgeError(
                "PROJECT_REVIEW_CLAIM_COVERAGE_INVALID",
                f"{kind} review omitted or duplicated a stage claim",
                http_status=502,
            )
        evidence_items = execution.get(
            "review_images" if kind == "visual" else "technical_evidence"
        )
        evidence_by_id = {
            item["id"]: item
            for item in evidence_items
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        } if isinstance(evidence_items, list) else {}
        all_verified = True
        verified_observations: list[str] = []
        for stage_claim in lane:
            claim = by_stage_claim[stage_claim]
            disposition = claim.get("disposition")
            actual = claim.get("actual_evidence")
            references = claim.get("evidence_refs")
            not_applicable = self._claim_not_applicable(stage_claim)
            if disposition == "not_applicable":
                reason = claim.get("not_applicable_reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise BridgeError(
                        "PROJECT_REVIEW_NOT_APPLICABLE_UNJUSTIFIED",
                        "A not_applicable review claim requires a specific reason",
                        http_status=502,
                    )
                if not not_applicable:
                    all_verified = False
            elif (
                not_applicable
                or disposition != "verified"
                or not isinstance(actual, list)
                or not actual
                or not isinstance(references, list)
                or not references
                or any(reference not in evidence_by_id for reference in references)
            ):
                all_verified = False
            else:
                rendered_actual = " ".join(
                    value.strip() for value in actual if isinstance(value, str)
                )
                referenced = [evidence_by_id[reference] for reference in references]
                if not self._review_claim_evidence_related(
                    kind, stage_claim, rendered_actual, referenced
                ):
                    raise BridgeError(
                        "PROJECT_REVIEW_CLAIM_EVIDENCE_UNRELATED",
                        f"{kind} verified claim is supported only by generic or unrelated evidence",
                        http_status=502,
                    )
                normalized_actual = " ".join(rendered_actual.casefold().split())
                if any(
                    normalized_actual == previous
                    or SequenceMatcher(None, normalized_actual, previous).ratio() > 0.92
                    for previous in verified_observations
                ):
                    raise BridgeError(
                        "PROJECT_REVIEW_CLAIM_EVIDENCE_REPEATED",
                        f"{kind} review repeated one generic observation across distinct claims",
                        http_status=502,
                    )
                verified_observations.append(normalized_actual)
        applicable = any(not self._claim_not_applicable(value) for value in lane)
        has_evidence = bool(evidence_by_id)
        if review.get("decision") == "pass" and (
            not all_verified or (applicable and not has_evidence)
        ):
            code = (
                "VISUAL_REVIEW_IMAGE_REQUIRED"
                if kind == "visual" and not has_evidence
                else "PROJECT_REVIEW_PASS_UNSUPPORTED"
            )
            raise BridgeError(
                code,
                f"{kind} review cannot pass without verified claim-specific evidence",
                http_status=502,
            )
        missing = review.get("missing_evidence")
        if review.get("decision") == "pass" and isinstance(missing, list) and missing:
            raise BridgeError(
                "PROJECT_REVIEW_PASS_UNSUPPORTED",
                f"{kind} review cannot pass while evidence is missing",
                http_status=502,
            )

    @classmethod
    def _review_claim_evidence_related(
        cls,
        kind: str,
        stage_claim: str,
        actual: str,
        referenced: list[Mapping[str, Any]],
    ) -> bool:
        if not actual or cls._generic_review_text(actual):
            return False
        claim_anchors = cls._review_anchors(stage_claim)
        actual_anchors = cls._review_anchors(actual)
        required = 3 if len(claim_anchors) >= 5 else 2
        if len(claim_anchors & actual_anchors) < required:
            return False
        claim_concepts = cls._review_concepts(stage_claim)
        if len(claim_concepts) >= 3 and len(
            claim_concepts & cls._review_concepts(actual)
        ) < max(2, (len(claim_concepts) + 1) // 2):
            return False
        if kind == "visual":
            return all(
                isinstance(item.get("path"), str)
                and isinstance(item.get("frame_or_time"), str)
                for item in referenced
            )
        for item in referenced:
            corpus = " ".join(
                str(item.get(key, ""))
                for key in (
                    "claim",
                    "scope_or_path",
                    "frame_or_time",
                    "observation_or_measurement",
                    "result",
                )
            )
            corpus_anchors = cls._review_anchors(corpus)
            corpus_concepts = cls._review_concepts(corpus)
            if len(claim_concepts) >= 3 and len(
                claim_concepts & corpus_concepts
            ) < max(2, (len(claim_concepts) + 1) // 2):
                continue
            actual_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", actual))
            corpus_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", corpus))
            if actual_numbers and not actual_numbers.issubset(corpus_numbers):
                continue
            if len(claim_anchors & corpus_anchors) >= required and len(
                actual_anchors & corpus_anchors
            ) >= 2:
                return True
            claim_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", stage_claim))
            if claim_numbers and claim_numbers & set(
                re.findall(r"-?\d+(?:\.\d+)?", corpus)
            ):
                return True
            paths = set(re.findall(r"(?:[A-Za-z]:\\|/)[^\s,，;；]+", stage_claim))
            if paths and any(path in corpus for path in paths):
                return True
        return False

    @staticmethod
    def _review_concepts(value: str) -> set[str]:
        normalized = value.casefold()
        concepts = {
            name
            for name, aliases in (
                ("output", ("输出", "output", "out_")),
                ("path", ("路径", "/obj/", "path")),
                ("node", ("节点", "node")),
                ("connection", ("连接", "接入", "connect")),
                ("error", ("错误", "error")),
                ("width", ("宽度", "width")),
                ("parameter", ("参数", "parameter")),
                ("propagation", ("传播", "驱动", "propagat")),
                ("door", ("门洞", "door opening")),
                ("corner", ("墙角", "corner")),
                ("roof", ("屋顶", "roof")),
                ("ridge", ("屋脊", "ridge")),
                ("eave", ("屋檐", "eave")),
                ("support", ("支撑", "support")),
                ("contact", ("接触", "contact")),
                ("clearance", ("净空", "clearance")),
                ("intersection", ("穿插", "intersection", "intersect")),
                ("perspective", ("透视", "perspective")),
                ("front", ("正面", "front")),
                ("board", ("墙板", "board")),
                ("silhouette", ("轮廓", "silhouette")),
                ("box", ("盒状", "box-like", "boxlike")),
                ("material", ("材质", "material")),
                ("light", ("灯光", "light")),
                ("timing", ("时序", "帧", "timing", "frame")),
            )
            if any(alias in normalized for alias in aliases)
        }
        return concepts

    @staticmethod
    def _generic_review_text(value: str) -> bool:
        normalized = " ".join(value.casefold().split())
        if len(normalized) < 12:
            return True
        return re.fullmatch(
            r"(?:looks? good|looks? correct|evidence supports(?: the claim)?|"
            r"verified|passed|ok|看起来(?:正确|很好|已检查)|证据支持(?:该断言)?|"
            r"检查通过|没有问题)[。.!！ ]*",
            normalized,
            re.I,
        ) is not None

    @staticmethod
    def _review_anchors(value: str) -> set[str]:
        stop = {
            "actual",
            "stage",
            "claim",
            "evidence",
            "verified",
            "result",
            "当前",
            "阶段",
            "实际",
            "证据",
            "验证",
            "必须",
            "不得",
            "一致",
            "显示",
            "检查",
            "结果",
            "关系",
        }
        anchors = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", value)
            if token.casefold() not in stop
        }
        anchors.update(re.findall(r"(?:[A-Za-z]:\\|/)[^\s,，;；]+", value))
        anchors.update(re.findall(r"-?\d+(?:\.\d+)?", value))
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", value):
            for size in (2, 3, 4):
                anchors.update(
                    sequence[index : index + size]
                    for index in range(max(0, len(sequence) - size + 1))
                )
        return {anchor for anchor in anchors if anchor not in stop}

    @staticmethod
    def _claim_not_applicable(value: str) -> bool:
        normalized = " ".join(value.casefold().split())
        return (
            (normalized.startswith("not applicable") and len(normalized) > 16)
            or (normalized.startswith("不适用") and len(normalized) > 4)
        )

    @staticmethod
    def _execution_evidence_signature(
        execution: Mapping[str, Any], images: tuple[str, ...]
    ) -> dict[str, frozenset[str]]:
        ids = {
            value
            for item in (
                list(execution.get("technical_evidence", []))
                + list(execution.get("review_images", []))
            )
            if isinstance(item, Mapping)
            for value in (item.get("id"), item.get("tool_item_id"), item.get("capture_tool_item_id"))
            if isinstance(value, str)
        }
        ids.update(
            "path:" + item["path"]
            for item in execution.get("review_images", [])
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        )
        image_hashes: set[str] = set()
        for path in images:
            digest = hashlib.sha256()
            with Path(path).open("rb") as stream:
                for chunk in iter(lambda: stream.read(65_536), b""):
                    digest.update(chunk)
            image_hashes.add(digest.hexdigest())
        return {"ids": frozenset(ids), "image_hashes": frozenset(image_hashes)}


    def _record_thread(
        self,
        project: dict[str, Any],
        role: str,
        thread_id: str,
        title: str,
        model: str | None,
    ) -> None:
        project["threads"][role] = {
            "role": role,
            "role_title": ROLE_TITLES[role],
            "responsibility": ROLE_RESPONSIBILITIES[role],
            "thread_id": thread_id,
            "title": title,
            "status": "pending",
            "model": model,
        }
        self._thread_roles[thread_id] = (project["project_id"], role)

    def _set_goal(
        self,
        project_id: str,
        status: str,
        *,
        fallback_objective: str | None = None,
    ) -> None:
        with self._condition:
            project = self._require_project(project_id)
            thread_id = project["root_thread_id"]
            fallback = fallback_objective or project.get("_task") or project["title"]
        current = self._client.request(
            "thread/goal/get", {"threadId": thread_id}
        )
        goal = current.get("goal") if isinstance(current, Mapping) else None
        if goal is not None and not isinstance(goal, Mapping):
            raise BridgeError(
                "PROJECT_GOAL_INVALID",
                "Codex returned an invalid native Goal",
                http_status=502,
            )
        if isinstance(goal, Mapping):
            objective, token_budget = goal.get("objective"), goal.get("tokenBudget")
            if (
                goal.get("threadId") != thread_id
                or not isinstance(objective, str)
                or not objective.strip()
                or (
                    token_budget is not None
                    and (
                        not isinstance(token_budget, int)
                        or isinstance(token_budget, bool)
                        or token_budget <= 0
                    )
                )
            ):
                raise BridgeError(
                    "PROJECT_GOAL_INVALID",
                    "The native Goal identity or stable fields are invalid",
                    http_status=502,
                )
        else:
            objective = " ".join(str(fallback).split())[:1_024]
            token_budget = None
        self._client.request(
            "thread/goal/set",
            {
                "threadId": thread_id,
                "objective": objective,
                "status": status,
                "tokenBudget": token_budget,
            },
        )

    def _public(self, project: Mapping[str, Any]) -> dict[str, Any]:
        guidance_available = bool(
            project.get("status") == _RUNNING
            or self._restart_recoverable(project)
        )
        threads = []
        records = project.get("threads")
        for role in PROJECT_ROLE_ORDER:
            record = records.get(role) if isinstance(records, Mapping) else None
            if not isinstance(record, Mapping):
                continue
            status, value = record.get("status"), {
                key: record.get(key)
                for key in (
                    "role",
                    "role_title",
                    "responsibility",
                    "thread_id",
                    "title",
                    "status",
                    "model",
                )
            }
            collaboration = record.get("collaboration")
            if isinstance(collaboration, Mapping):
                event_count = (
                    int(collaboration.get("event_count", 0))
                    if isinstance(collaboration.get("event_count"), int)
                    else 0
                )
                raw_events = collaboration.get("events")
                value["collaboration"] = {
                    "mode": collaboration.get("mode"),
                    "event_count": max(
                        len(raw_events) if isinstance(raw_events, list) else 0,
                        event_count,
                    ),
                }
            done = int(status == "completed")
            value["progress"] = self._progress(
                done, 1, "已完成" if done else "进行中" if status == "running" else "等待"
            )
            effort = record.get("_effort")
            tier = record.get("_service_tier")
            if isinstance(effort, str):
                value["effort"] = effort
            if isinstance(tier, str):
                value["service_tier"] = tier
            value["actions"] = {
                "open_thread": True,
                "append_guidance": guidance_available,
                "resume_with_guidance": self._restart_recoverable(project),
            }
            if isinstance(record.get("_next_model"), str):
                value["pending_model"] = record["_next_model"]
                value["model_application"] = "随下一条指导生效"
            if isinstance(record.get("_next_effort"), str):
                value["pending_effort"] = record["_next_effort"]
            if isinstance(record.get("_next_service_tier"), str):
                value["pending_service_tier"] = record[
                    "_next_service_tier"
                ]
            threads.append(value)
        result = {
            key: project.get(key)
            for key in (
                "project_id",
                "title",
                "status",
                "stage",
                "progress",
                "updated_at",
                "root_thread_id",
            )
        }
        result.update(
            actions={
                "open_thread": True,
                "append_guidance": guidance_available,
                "resume_with_guidance": self._restart_recoverable(project),
            },
            restart_recoverable=self._restart_recoverable(project),
            threads=threads,
        )
        if isinstance(project.get("error"), str):
            result["error"] = project["error"]
        return result

    def _registry_public(self, project: Mapping[str, Any]) -> dict[str, Any]:
        """Persist identity/status only; app-server history owns event detail."""

        value = self._public(project)
        for thread in value.get("threads", []):
            if not isinstance(thread, dict):
                continue
            collaboration = thread.get("collaboration")
            if isinstance(collaboration, Mapping):
                thread["collaboration"] = {
                    "mode": collaboration.get("mode"),
                    "event_count": (
                        int(collaboration.get("event_count", 0))
                        if isinstance(collaboration.get("event_count"), int)
                        else 0
                    ),
                }
        return value

    def _touch(self, project: dict[str, Any]) -> bool:
        project["updated_at"] = time.time()
        self._revision += 1
        persisted = self._try_write_registry()
        self._events.publish(
            "project_team_updated",
            project_id=project.get("project_id"),
            status=project.get("status"),
            stage=project.get("stage"),
            state_status=self._state_status,
        )
        return persisted

    @staticmethod
    def _activate_next_settings(record: dict[str, Any]) -> None:
        for field in ("model", "_effort", "_service_tier"):
            value = record.pop("_next_" + field.lstrip("_"), None)
            if value is not None:
                record[field] = value

    def _load_registry(self) -> None:
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            if not path.is_file() or path.stat().st_size > _PROJECT_REGISTRY_MAX_BYTES:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema") != PROJECT_TEAM_REGISTRY_SCHEMA:
                raise ValueError
            self._revision = int(value.get("revision", 0))
            for raw in value.get("projects", [])[:256]:
                project = self._load_project(raw)
                if project["status"] == _RUNNING:
                    project.update(
                        stage="Bridge 重启，正在核对原生 Goal",
                        error=(
                            "The previous Bridge stopped while this project was running; "
                            "scene-write ownership remains locked until its native Goal "
                            "is confirmed paused."
                        ),
                    )
                    self._restart_reconcile_projects.add(project["project_id"])
                self._projects.append(project)
                for role, record in project["threads"].items():
                    self._thread_roles[record["thread_id"]] = (project["project_id"], role)
            self._state_status = "ready"
            self._try_write_registry()
        except (AttributeError, OSError, UnicodeError, ValueError, TypeError):
            self._projects, self._thread_roles = [], {}
            self._revision, self._state_status = 0, "invalid"

    def _try_write_registry(self) -> bool:
        """Persist current memory without pretending a failed write rolled it back."""

        try:
            self._write_registry()
        except BridgeError as exc:
            self._registry_write_failed = True
            self._registry_write_error = self._error(exc)
            self._state_status = "write_failed"
            return False
        self._registry_write_failed = False
        self._registry_write_error = None
        return True

    def _ensure_registry_boundary(
        self,
        project_id: str | None,
        operation: str,
    ) -> None:
        """Retry one failed durable boundary before workflow/scene advancement."""

        with self._condition:
            if self._state_status == "invalid":
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_INVALID",
                    "The project registry is unreadable, so prior native Goal and "
                    "scene-writer ownership cannot be proven safe",
                    http_status=503,
                    details={"operation": operation},
                )
            if not self._registry_write_failed:
                return
            if self._try_write_registry():
                return
            project = self._project(project_id) if project_id is not None else None
            message = (
                "Project registry persistence is unavailable; HIA kept in-memory "
                f"ownership and paused before {operation}. "
                + (self._registry_write_error or "Registry rewrite failed.")
            )
            if project is not None and project.get("status") == _RUNNING:
                project.update(
                    stage="项目注册表写入失败，已保留写入锁",
                    error=message,
                )
            self._events.publish(
                "project_team_updated",
                project_id=project_id,
                status=project.get("status") if project is not None else None,
                stage=project.get("stage") if project is not None else None,
                state_status="write_failed",
            )
        raise BridgeError(
            "PROJECT_THREAD_REGISTRY_DEGRADED",
            message,
            http_status=503,
            details={"operation": operation},
        )

    def _write_registry(self) -> None:
        if self._state_path is None:
            self._state_status = "memory"
            return
        encoded = json.dumps(
            {
                "schema": PROJECT_TEAM_REGISTRY_SCHEMA,
                "revision": self._revision,
                "projects": [
                    self._registry_public(project) for project in self._projects
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        if len(encoded.encode("utf-8")) > _PROJECT_REGISTRY_MAX_BYTES:
            raise BridgeError(
                "PROJECT_THREAD_REGISTRY_TOO_LARGE",
                "Project Thread registry exceeds its 16 MiB bounded size",
                http_status=503,
            )
        path = self._state_path
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, path)
            self._state_status = "ready"
        except OSError as exc:
            raise BridgeError(
                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                "Project Thread registry could not be persisted",
                http_status=503,
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_project(self, raw: Any) -> dict[str, Any]:
        project_id, root_id = raw.get("project_id"), raw.get("root_thread_id")
        if not self._bounded_registry_identifier(
            project_id,
            _PROJECT_ID_MAX_CHARS,
        ) or not self._bounded_registry_identifier(
            root_id,
            _THREAD_ID_MAX_CHARS,
        ):
            raise ValueError
        title = self._safe_title(raw.get("title"), "未命名项目")
        threads: dict[str, dict[str, Any]] = {}
        for item in raw.get("threads", []):
            role = ROLE_ALIASES.get(item.get("role"), item.get("role"))
            thread_id = item.get("thread_id")
            if role not in PROJECT_ROLE_ORDER or not self._bounded_registry_identifier(
                thread_id,
                _THREAD_ID_MAX_CHARS,
            ):
                continue
            status = item.get("status")
            if not isinstance(status, str) or len(status) > _STATUS_MAX_CHARS:
                status = "interrupted"
            model = item.get("model")
            if model is not None and not self._bounded_registry_identifier(
                model,
                _MODEL_MAX_CHARS,
            ):
                model = None
            threads[role] = {
                "role": role,
                "role_title": ROLE_TITLES[role],
                "responsibility": ROLE_RESPONSIBILITIES[role],
                "thread_id": thread_id,
                "title": self._safe_title(item.get("title"), f"{title}｜{ROLE_TITLES[role]}"),
                "status": status,
                "model": model,
            }
            for public_name, private_name, maximum in (
                ("effort", "_effort", 64),
                ("service_tier", "_service_tier", 128),
                ("pending_model", "_next_model", _MODEL_MAX_CHARS),
                ("pending_effort", "_next_effort", 64),
                ("pending_service_tier", "_next_service_tier", 128),
            ):
                setting = item.get(public_name)
                if setting is not None and self._bounded_registry_identifier(
                    setting, maximum
                ):
                    threads[role][private_name] = setting
            collaboration = item.get("collaboration")
            if isinstance(collaboration, Mapping) and collaboration.get("mode") in {
                "used-with-real-events",
                "serial-fallback",
            }:
                threads[role]["collaboration"] = {
                    "mode": collaboration["mode"],
                    "events": copy_collaboration(collaboration.get("events")),
                    "event_count": min(
                        1_000_000,
                        max(
                            0,
                            int(collaboration.get("event_count", 0))
                            if isinstance(collaboration.get("event_count"), int)
                            else 0,
                        ),
                    ),
                }
        if "supervisor" not in threads:
            raise ValueError
        progress = raw.get("progress")
        project = {
            "project_id": project_id,
            "title": title,
            "status": (
                raw.get("status")
                if isinstance(raw.get("status"), str)
                and len(raw["status"]) <= _STATUS_MAX_CHARS
                else "interrupted"
            ),
            "stage": self._safe_title(raw.get("stage"), "等待"),
            "progress": progress if isinstance(progress, dict) else self._progress(0, 0, "等待"),
            "updated_at": raw.get("updated_at")
            if isinstance(raw.get("updated_at"), (int, float))
            else time.time(),
            "root_thread_id": root_id,
            "threads": threads,
        }
        error = raw.get("error")
        if isinstance(error, str) and len(error) <= 2_048 and "\x00" not in error:
            project["error"] = error
        return project

    @staticmethod
    def _bounded_registry_identifier(value: Any, maximum: int) -> bool:
        return bool(
            isinstance(value, str)
            and value
            and len(value) <= maximum
            and value == value.strip()
            and not any(ord(character) < 32 for character in value)
        )

    def _fail_running(self, message: str) -> None:
        with self._condition:
            for project in self._projects:
                if project.get("status") == _RUNNING:
                    # The dead app-server cannot confirm a native Goal pause.
                    # Preserve writer ownership until the next initialized
                    # app-server reconciles that exact root Goal.
                    project.update(
                        stage="Codex app-server 已退出，等待 Goal 恢复核对",
                        error=message,
                    )
                    self._restart_reconcile_projects.add(project["project_id"])
                    self._touch(project)
            for pending in self._pending.values():
                pending.status, pending.error, pending.completed = "failed", message, True
                pending.request_inflight = False
            self._condition.notify_all()

    def _project(self, project_id: Any) -> dict[str, Any] | None:
        return next(
            (item for item in self._projects if item.get("project_id") == project_id),
            None,
        )

    def _require_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        if project is None:
            raise BridgeError(
                "PROJECT_THREAD_NOT_FOUND",
                "The requested project does not exist",
                http_status=404,
            )
        return project

    @staticmethod
    def _thread(project: Mapping[str, Any], role: str) -> dict[str, Any]:
        records = project.get("threads")
        record = records.get(role) if isinstance(records, Mapping) else None
        if not isinstance(record, dict):
            raise BridgeError(
                "PROJECT_THREAD_ROLE_MISSING",
                f"The {role} Thread is unavailable",
                http_status=409,
            )
        return record

    @staticmethod
    def _source(project_id: str, role: str) -> str:
        return f"{PROJECT_THREAD_SOURCE_PREFIX}{project_id}/{role}"

    @staticmethod
    def _parse_source(value: Any) -> tuple[str, str] | None:
        if not isinstance(value, str) or not value.startswith(PROJECT_THREAD_SOURCE_PREFIX):
            return None
        parts = value[len(PROJECT_THREAD_SOURCE_PREFIX) :].split("/")
        if len(parts) != 2:
            return None
        role = ROLE_ALIASES.get(parts[1], parts[1])
        return (parts[0], role) if parts[0].startswith("project-") and role in PROJECT_ROLE_ORDER else None

    @staticmethod
    def _turn_id(result: Any) -> str:
        turn = result.get("turn") if isinstance(result, Mapping) else None
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Codex turn/start response has no Turn id",
                http_status=502,
            )
        return turn_id

    @staticmethod
    def _turn_status(turn: Any) -> str:
        status = turn.get("status") if isinstance(turn, Mapping) else None
        return status if status in {"completed", "failed", "interrupted"} else "completed"

    @staticmethod
    def _progress(completed: int, total: int, label: str) -> dict[str, Any]:
        percent = int(round(completed / total * 100)) if total else 0
        return {
            "completed": completed,
            "total": total,
            "percent": max(0, min(100, percent)),
            "label": label,
        }

    @staticmethod
    def _safe_title(value: Any, fallback: str) -> str:
        return value.strip()[:160] if isinstance(value, str) and value.strip() else fallback

    @staticmethod
    def _text(value: Any, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                f"{field} must be a non-empty string",
                details={"field": field},
            )
        value = value.strip()
        if len(value) > maximum or "\x00" in value:
            raise BridgeError(
                "INVALID_PROJECT_THREAD_REQUEST",
                f"{field} exceeds its bounded length",
                details={"field": field, "max_chars": maximum},
            )
        return value

    @classmethod
    def _optional_text(cls, value: Any, field: str, maximum: int) -> str | None:
        return None if value is None else cls._text(value, field, maximum)

    @staticmethod
    def _error(value: Any) -> str:
        return str(value).replace("\x00", "")[:2_000] or "Project Thread failed"
