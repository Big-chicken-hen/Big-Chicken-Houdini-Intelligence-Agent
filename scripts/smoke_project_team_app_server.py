"""Finite smoke for the real Codex app-server project-team route.

The default ``pre-activation`` case starts the pinned real app-server and
creates the five project Threads, but deliberately starts no model Turn.  It
therefore verifies provisioning and permission isolation only: it must observe
no native Goal before an acknowledged root Turn.  Goal lifecycle claims belong
to a separate explicitly opted-in model-Turn smoke.

Bridge registries and outputs are isolated under ``.runtime/smoke``.  The
app-server safely reuses the existing project-local login store without copying
or printing credentials.  Every Thread id returned by this process is tracked
from the real protocol response and, even after an exception, is individually
deleted in ``finally`` after any established Goal is confirmed paused.  The
smoke never connects to the running Houdini process and never records chat
content.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
import uuid
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.main import (  # noqa: E402
    HIA_MCP_V2_BACKEND,
    _codex_app_server_command,
)
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.project_thread_contract import REVIEW_SCHEMA  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402


TERMINAL_PROJECT_STATUSES = {
    "blocked",
    "completed",
    "failed",
    "interrupted",
    "not_applicable",
}
ROLE_ORDER = (
    "supervisor",
    "planning",
    "execution",
    "visual_review",
    "technical_review",
)


def _audited_source_hashes() -> dict[str, str]:
    paths = (
        Path(__file__).resolve(),
        PROJECT_ROOT / "services" / "bridge" / "hia_bridge" / "project_threads.py",
        PROJECT_ROOT
        / "services"
        / "bridge"
        / "hia_bridge"
        / "project_thread_contract.py",
    )
    return {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }


class _RecordingClient:
    """Record redaction-safe protocol ownership while delegating requests."""

    def __init__(self, delegate: CodexStdioClient) -> None:
        self._delegate = delegate
        self._lock = threading.Lock()
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._created_thread_ids: list[str] = []
        self._started_turns: list[tuple[str, str]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def request(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            self.requests.append((method, copy.deepcopy(params)))
        result = self._delegate.request(method, params)
        self._record_protocol_result(method, params, result)
        return result

    def request_with_timeout(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        with self._lock:
            self.requests.append((method, copy.deepcopy(params)))
        result = self._delegate.request_with_timeout(
            method,
            params,
            timeout_seconds=timeout_seconds,
        )
        self._record_protocol_result(method, params, result)
        return result

    def snapshot_requests(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return copy.deepcopy(self.requests)

    def snapshot_created_thread_ids(self) -> list[str]:
        with self._lock:
            return list(self._created_thread_ids)

    def snapshot_started_turns(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._started_turns)

    def _record_protocol_result(
        self,
        method: str,
        params: Mapping[str, Any],
        result: Any,
    ) -> None:
        if not isinstance(result, Mapping):
            return
        if method in {"thread/start", "thread/fork"}:
            thread = result.get("thread")
            thread_id = thread.get("id") if isinstance(thread, Mapping) else None
            if isinstance(thread_id, str) and thread_id:
                with self._lock:
                    if thread_id not in self._created_thread_ids:
                        self._created_thread_ids.append(thread_id)
        elif method == "turn/start":
            turn = result.get("turn")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            thread_id = params.get("threadId")
            if (
                isinstance(thread_id, str)
                and thread_id
                and isinstance(turn_id, str)
                and turn_id
            ):
                with self._lock:
                    value = (thread_id, turn_id)
                    if value not in self._started_turns:
                        self._started_turns.append(value)


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _child_environment(run_directory: Path) -> dict[str, str]:
    environment = os.environ.copy()
    python_executable = str(Path(sys.executable).resolve())
    service_root = PROJECT_ROOT / "services" / "hia_mcp_v2"
    python_path = [str(service_root), str(PROJECT_ROOT / "src")]
    existing_python_path = environment.get("PYTHONPATH")
    if existing_python_path:
        python_path.append(existing_python_path)
    path_entries = [str(Path(python_executable).parent)]
    existing_path = environment.get("PATH")
    if existing_path:
        path_entries.append(existing_path)
    temporary = run_directory / "tmp"
    cache = run_directory / "cache"
    runtime = run_directory / "hia-mcp-v2"
    for directory in (temporary, cache, runtime):
        directory.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            # Reuse authenticated project-local Codex state, while every Bridge
            # registry and temporary artifact remains isolated in run_directory.
            "CODEX_HOME": str(PROJECT_ROOT / ".runtime" / "codex-home"),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "PATH": os.pathsep.join(path_entries),
            "PYTHONPATH": os.pathsep.join(python_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HIA_PROJECT_ROOT": str(PROJECT_ROOT),
            "HIA_CACHE_DIR": str(cache),
            "HIA_RENDER_OUTPUT_DIR": str(cache),
            "HIA_EXPECTED_PYTHON_EXE": python_executable,
            "HIA_MCP_V2_HOST": "127.0.0.1",
            "HIA_MCP_V2_PORT": str(_unused_loopback_port()),
            "HIA_MCP_V2_TOKEN": secrets.token_urlsafe(32),
            "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
            "HIA_MCP_V2_RUNTIME_DIR": str(runtime),
            "HIA_MCP_V2_EXECUTOR_PATH": str(
                PROJECT_ROOT
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "executor.py"
            ),
            "HIA_LAUNCHER_SESSION_ID": uuid.uuid4().hex,
        }
    )
    return environment


def _project(session: BridgeSession, project_id: str) -> dict[str, Any]:
    projects = session.project_team_snapshot().get("projects", [])
    return next(
        item
        for item in projects
        if isinstance(item, dict) and item.get("project_id") == project_id
    )


def _wait_terminal(
    session: BridgeSession,
    project_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    current = _project(session, project_id)
    while (
        current.get("status") not in TERMINAL_PROJECT_STATUSES
        and time.monotonic() < deadline
    ):
        time.sleep(0.1)
        current = _project(session, project_id)
    if current.get("status") not in TERMINAL_PROJECT_STATUSES:
        raise TimeoutError(
            f"project {project_id} remained {current.get('status')} at "
            f"stage {current.get('stage')}"
        )
    return current


def _redacted_event_summary(events: EventBuffer) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = events.poll(cursor, timeout=0, limit=512)
        values = page.get("events", [])
        if not values:
            break
        for event in values:
            method = event.get("method")
            if isinstance(method, str) and method.endswith("/delta"):
                continue
            params = event.get("params")
            params = params if isinstance(params, dict) else {}
            turn = params.get("turn")
            turn = turn if isinstance(turn, dict) else {}
            output.append(
                {
                    key: value
                    for key, value in {
                        "seq": event.get("seq"),
                        "type": event.get("type"),
                        "method": event.get("method"),
                        "thread_id": params.get("threadId"),
                        "turn_id": params.get("turnId") or turn.get("id"),
                        "turn_status": turn.get("status"),
                        "project_id": event.get("project_id"),
                        "project_status": event.get("status"),
                        "stage": event.get("stage"),
                    }.items()
                    if value is not None
                }
            )
        cursor = int(page.get("latest", cursor))
        if len(values) < 512:
            break
    return output


def _redacted_error(error: BaseException) -> dict[str, str]:
    """Return diagnostic identity without messages, prompts, or credentials."""

    value = {"type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        value["code"] = code
    method = getattr(error, "method", None)
    if isinstance(method, str) and method:
        value["method"] = method
    return value


def _pause_goal_before_delete(
    client: _RecordingClient,
    thread_id: str,
) -> dict[str, Any]:
    """Confirm that one created Thread has no Goal or a paused Goal."""

    try:
        response = client.request("thread/goal/get", {"threadId": thread_id})
    except Exception as exc:
        return {
            "thread_id": thread_id,
            "safe_to_delete": False,
            "state": "goal_read_failed",
            "error": _redacted_error(exc),
        }
    goal = response.get("goal") if isinstance(response, Mapping) else None
    if goal is None:
        return {
            "thread_id": thread_id,
            "safe_to_delete": True,
            "state": "not_established",
        }
    if not isinstance(goal, Mapping):
        return {
            "thread_id": thread_id,
            "safe_to_delete": False,
            "state": "invalid_goal_response",
        }
    objective = goal.get("objective")
    token_budget = goal.get("tokenBudget")
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
        return {
            "thread_id": thread_id,
            "safe_to_delete": False,
            "state": "invalid_goal_identity",
        }
    if goal.get("status") != "paused":
        try:
            client.request(
                "thread/goal/set",
                {
                    "threadId": thread_id,
                    "objective": objective,
                    "status": "paused",
                    "tokenBudget": token_budget,
                },
            )
        except Exception as exc:
            return {
                "thread_id": thread_id,
                "safe_to_delete": False,
                "state": "goal_pause_failed",
                "error": _redacted_error(exc),
            }
    try:
        verified = client.request("thread/goal/get", {"threadId": thread_id})
    except Exception as exc:
        return {
            "thread_id": thread_id,
            "safe_to_delete": False,
            "state": "goal_pause_unverified",
            "error": _redacted_error(exc),
        }
    verified_goal = verified.get("goal") if isinstance(verified, Mapping) else None
    if (
        not isinstance(verified_goal, Mapping)
        or verified_goal.get("threadId") != thread_id
        or verified_goal.get("status") != "paused"
    ):
        return {
            "thread_id": thread_id,
            "safe_to_delete": False,
            "state": "goal_pause_unverified",
        }
    return {
        "thread_id": thread_id,
        "safe_to_delete": True,
        "state": "paused",
    }


def _cleanup_created_threads(
    session: BridgeSession,
    client: _RecordingClient,
) -> dict[str, Any]:
    """Pause Goals, then delete only Thread ids returned to this process."""

    created = client.snapshot_created_thread_ids()
    summary: dict[str, Any] = {
        "created_thread_ids": created,
        "created_count": len(created),
        "goal_checks": [],
        "deleted_thread_ids": [],
        "retained_thread_ids": [],
        "project_interrupt": None,
        "turn_interrupt_attempts": 0,
        "complete": not created,
    }
    if not client.is_running:
        summary["retained_thread_ids"] = created
        summary["complete"] = not created
        if created:
            summary["cleanup_error"] = {"type": "AppServerNotRunning"}
        return summary

    try:
        summary["project_interrupt"] = session._project_threads.interrupt_project()
    except Exception as exc:
        summary["project_interrupt"] = {"error": _redacted_error(exc)}

    # Explicit model-Turn cases may still have protocol-acknowledged Turns.
    # Interrupt only exact pairs returned to this process; completed Turns are
    # harmless and their expected rejection is deliberately not fatal here.
    for thread_id, turn_id in reversed(client.snapshot_started_turns()):
        summary["turn_interrupt_attempts"] += 1
        try:
            client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        except Exception:
            pass

    # Root is created first, so reverse creation order deletes workers first
    # and the project root last.  Never broaden this into thread/list cleanup.
    for thread_id in reversed(created):
        goal_check = _pause_goal_before_delete(client, thread_id)
        summary["goal_checks"].append(goal_check)
        if not goal_check["safe_to_delete"]:
            summary["retained_thread_ids"].append(thread_id)
            continue
        try:
            client.request("thread/delete", {"threadId": thread_id})
        except Exception as exc:
            summary["retained_thread_ids"].append(thread_id)
            goal_check["delete_error"] = _redacted_error(exc)
        else:
            summary["deleted_thread_ids"].append(thread_id)
    summary["complete"] = (
        len(summary["deleted_thread_ids"]) == len(created)
        and not summary["retained_thread_ids"]
    )
    return summary


def _turn_start_counts(
    requests: list[tuple[str, dict[str, Any]]], project: dict[str, Any]
) -> dict[str, int]:
    roles = {
        item.get("thread_id"): item.get("role")
        for item in project.get("threads", [])
        if isinstance(item, dict)
    }
    counts = {role: 0 for role in ROLE_ORDER}
    for method, params in requests:
        if method != "turn/start":
            continue
        role = roles.get(params.get("threadId"))
        if role in counts:
            counts[role] += 1
    return counts


def _tool_calls_for_thread(events: EventBuffer, thread_id: str) -> list[str]:
    """Return bounded tool names observed for one exact real app-server Thread."""

    names: list[str] = []
    cursor = 0
    while True:
        page = events.poll(cursor, timeout=0, limit=512)
        values = page.get("events", [])
        if not values:
            break
        for event in values:
            params = event.get("params")
            params = params if isinstance(params, Mapping) else {}
            item = params.get("item")
            item = item if isinstance(item, Mapping) else {}
            if (
                params.get("threadId") == thread_id
                and item.get("type") == "mcpToolCall"
            ):
                name = item.get("tool")
                if isinstance(name, str) and name and name not in names:
                    names.append(name[:256])
        cursor = int(page.get("latest", cursor))
        if len(values) < 512:
            break
    return names


def _write_smoke_png(path: Path, rgb: tuple[int, int, int]) -> None:
    """Write one deterministic 32x32 RGB PNG without external dependencies."""

    width = height = 32
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk("IHDR".encode("ascii"), struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk("IDAT".encode("ascii"), zlib.compress(raw, level=9))
        + chunk("IEND".encode("ascii"), b"")
    )


def _run_reference_intake_case(
    session: BridgeSession,
    events: EventBuffer,
    client: _RecordingClient,
    run_directory: Path,
) -> dict[str, Any]:
    """Exercise two real Visual Review Turns without starting Execution or HIA."""

    request_offset = len(client.snapshot_requests())
    started_thread = session.start_thread(team_override="team")
    specification = session._project_threads.prepare_project(
        started_thread["thread_id"],
        "Reference-intake smoke only; do not execute or modify a Houdini scene.",
        effort="ultra",
    )
    project_id = str(specification["project_id"])
    created = _project(session, project_id)
    visual = next(
        item
        for item in created.get("threads", [])
        if isinstance(item, dict) and item.get("role") == "visual_review"
    )
    image_directory = run_directory / "reference-intake-images"
    image_directory.mkdir(parents=True, exist_ok=False)
    reference = image_directory / "authoritative-reference.png"
    _write_smoke_png(reference, (24, 120, 210))
    captures: list[Path] = []
    for index in range(16):
        capture = image_directory / f"current-capture-{index:02d}.png"
        _write_smoke_png(
            capture,
            ((index * 29) % 256, (80 + index * 11) % 256, (210 - index * 7) % 256),
        )
        captures.append(capture)
    session._project_threads.append_guidance(
        project_id,
        str(visual["thread_id"]),
        "Treat the attached blue square as the authoritative visual reference for "
        "this bounded transport smoke. Do not infer Houdini scene state.",
        local_image_paths=(str(reference.resolve()),),
    )
    review = session._project_threads._run_structured_turn(
        project_id,
        "visual_review",
        "Review only the attached transport-smoke images. This is not a Houdini "
        "asset verdict: report the absence of scene evidence as blocked or repair, "
        "and return the complete required output schema without tools.",
        REVIEW_SCHEMA,
        images=tuple(str(path.resolve()) for path in captures),
    )
    requests = client.snapshot_requests()[request_offset:]
    visual_start = next(
        (
            params
            for method, params in requests
            if method == "thread/start"
            and str(params.get("threadSource", "")).endswith("/visual_review")
        ),
        {},
    )
    visual_config = visual_start.get("config")
    visual_config = visual_config if isinstance(visual_config, Mapping) else {}
    starts = [
        params
        for method, params in requests
        if method == "turn/start" and params.get("threadId") == visual["thread_id"]
    ]
    image_counts = [
        sum(1 for item in params.get("input", []) if item.get("type") == "localImage")
        for params in starts
    ]
    intake_markers = [
        any(
            item.get("type") == "text"
            and "REFERENCE INTAKE ONLY" in str(item.get("text", ""))
            for item in params.get("input", [])
        )
        for params in starts
    ]
    counts = _turn_start_counts(requests, created)
    goal_before_response = session.client.request(
        "thread/goal/get", {"threadId": created["root_thread_id"]}
    )
    goal_before = (
        goal_before_response.get("goal")
        if isinstance(goal_before_response, Mapping)
        else None
    )
    tool_names = _tool_calls_for_thread(events, str(visual["thread_id"]))
    interrupted = session._project_threads.interrupt_project()
    goal_after_response = session.client.request(
        "thread/goal/get", {"threadId": created["root_thread_id"]}
    )
    goal_after = (
        goal_after_response.get("goal")
        if isinstance(goal_after_response, Mapping)
        else None
    )
    return {
        "case": "reference-intake",
        "claim_scope": "real_app_server_visual_reference_intake_transport_only",
        "root_thread_id": started_thread["thread_id"],
        "project_id": project_id,
        "exact_five_roles": [
            item.get("role")
            for item in created.get("threads", [])
            if isinstance(item, dict)
        ]
        == list(ROLE_ORDER),
        "visual_thread_id": visual["thread_id"],
        "visual_turn_count": len(starts),
        "visual_turn_image_counts": image_counts,
        "reference_intake_then_formal_review": intake_markers == [True, False],
        "structured_review_returned": isinstance(review, Mapping)
        and set(REVIEW_SCHEMA["required"]).issubset(review),
        "turn_start_counts": counts,
        "execution_turns": counts["execution"],
        "visual_read_only": visual_start.get("sandbox") == "read-only",
        "visual_hia_disabled": (
            visual_config.get("mcp_servers.hia_mcp_v2.enabled") is False
            and visual_config.get("mcp_servers.houdini_intelligence.enabled") is False
        ),
        "model_mcp_tool_calls": tool_names,
        "hia_or_houdini_tools_invoked": any(
            method.startswith("hia_") or "houdini" in method.casefold()
            for method, _params in requests
        )
        or any(
            name.startswith("hia_") or "houdini" in name.casefold()
            for name in tool_names
        ),
        "goal_status_before_interrupt": (
            goal_before.get("status") if isinstance(goal_before, Mapping) else None
        ),
        "goal_status_after_interrupt": (
            goal_after.get("status") if isinstance(goal_after, Mapping) else None
        ),
        "project_interrupted_after_evidence": bool(interrupted.get("interrupted")),
        "writer_released": not session._project_threads.workflow_active(),
    }


def _run_case(
    session: BridgeSession,
    events: EventBuffer,
    client: _RecordingClient,
    *,
    name: str,
    task: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request_offset = len(client.snapshot_requests())
    started_thread = session.start_thread(team_override="team")
    started_turn = session.start_turn(
        task,
        effort="ultra",
        team_override="team",
    )
    project_id = str(started_turn["project_id"])
    terminal = _wait_terminal(session, project_id, timeout_seconds)
    goal_response = session.client.request(
        "thread/goal/get", {"threadId": terminal["root_thread_id"]}
    )
    goal = goal_response.get("goal") if isinstance(goal_response, dict) else None
    requests = client.snapshot_requests()[request_offset:]
    roles = [
        item.get("role")
        for item in terminal.get("threads", [])
        if isinstance(item, dict)
    ]
    return {
        "case": name,
        "root_thread_id": started_thread["thread_id"],
        "project_id": project_id,
        "project_status": terminal.get("status"),
        "project_stage": terminal.get("stage"),
        "project_error_present": bool(terminal.get("error")),
        "roles": roles,
        "exact_five_roles": roles == list(ROLE_ORDER),
        "turn_start_counts": _turn_start_counts(requests, terminal),
        "goal_status": goal.get("status") if isinstance(goal, dict) else None,
        "writer_released": not session._project_threads.workflow_active(),
    }


def _run_pre_activation_case(
    session: BridgeSession,
    client: _RecordingClient,
) -> dict[str, Any]:
    """Verify real five-Thread provisioning without making a Goal claim."""

    request_offset = len(client.snapshot_requests())
    started_thread = session.start_thread(team_override="team")
    specification = session._project_threads.prepare_project(
        started_thread["thread_id"],
        "隔离烟测：只验证项目制五个真实 Thread 的创建、权限和释放。",
        effort="ultra",
    )
    project_id = str(specification["project_id"])
    created = _project(session, project_id)
    roles = [
        item.get("role")
        for item in created.get("threads", [])
        if isinstance(item, dict)
    ]
    requests = client.snapshot_requests()[request_offset:]
    worker_starts = {
        str(params.get("threadSource", "")).rsplit("/", 1)[-1]: params
        for method, params in requests
        if method == "thread/start"
        and isinstance(params.get("threadSource"), str)
        and params["threadSource"].startswith("hia-project/")
    }
    root_start = next(
        (
            params
            for method, params in requests
            if method == "thread/start" and "threadSource" not in params
        ),
        {},
    )
    starts = {"supervisor": root_start, **worker_starts}
    permissions: dict[str, dict[str, Any]] = {}
    for role in ROLE_ORDER:
        params = starts.get(role, {})
        config = params.get("config")
        config = config if isinstance(config, dict) else {}
        hia_enabled = (
            config.get("mcp_servers.hia_mcp_v2.enabled") is True
            and config.get("mcp_servers.houdini_intelligence.enabled") is True
        )
        hia_disabled = (
            config.get("mcp_servers.hia_mcp_v2.enabled") is False
            and config.get("mcp_servers.houdini_intelligence.enabled") is False
        )
        permissions[role] = {
            "write_access": params.get("sandbox") == "workspace-write",
            "read_only": params.get("sandbox") == "read-only",
            "hia_enabled": hia_enabled,
            "hia_disabled": hia_disabled,
            "source": "captured real thread/start",
        }
    goal_response = session.client.request(
        "thread/goal/get", {"threadId": created["root_thread_id"]}
    )
    goal_before = (
        goal_response.get("goal") if isinstance(goal_response, Mapping) else None
    )
    turn_counts = _turn_start_counts(requests, created)
    return {
        "case": "pre-activation",
        "claim_scope": "thread_provisioning_and_permission_isolation_only",
        "root_thread_id": started_thread["thread_id"],
        "project_id": project_id,
        "project_status_before_cleanup": created.get("status"),
        "roles": roles,
        "exact_five_roles": roles == list(ROLE_ORDER),
        "permissions": permissions,
        "only_execution_writes": (
            permissions.get("execution", {}).get("write_access") is True
            and permissions.get("execution", {}).get("hia_enabled") is True
            and all(
                value.get("read_only") is True
                and value.get("hia_disabled") is True
                for role, value in permissions.items()
                if role != "execution"
            )
        ),
        "turn_start_counts": turn_counts,
        "no_model_turn_started": all(value == 0 for value in turn_counts.values()),
        "goal_established_before_root_turn": goal_before is not None,
        "goal_status_before_root_turn": (
            goal_before.get("status") if isinstance(goal_before, Mapping) else None
        ),
        "writer_reserved_before_cleanup": session._project_threads.workflow_active(),
    }


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Real Codex app-server project-team smoke. The default case creates "
            "Threads only and sends no model Turn."
        )
    )
    parser.add_argument(
        "--case",
        choices=(
            "pre-activation",
            "reference-intake",
            "non-scene",
            "missing-hia",
            "all",
        ),
        default="pre-activation",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--allow-model-turns",
        action="store_true",
        help=(
            "Required for non-scene, missing-hia, or all; these cases spend "
            "model inference and may take several minutes."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected redaction-safe plan without starting app-server.",
    )
    arguments = parser.parse_args(argv)
    if (
        not arguments.dry_run
        and arguments.case in {"reference-intake", "non-scene", "missing-hia", "all"}
        and not arguments.allow_model_turns
    ):
        parser.error(
            "this case starts real model Turns; pass --allow-model-turns explicitly"
        )
    return arguments


def main() -> int:
    arguments = _arguments()
    if arguments.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "case": arguments.case,
                    "starts_app_server": False,
                    "starts_model_turns": arguments.case
                    in {"reference-intake", "non-scene", "missing-hia", "all"},
                    "default_claim_scope": (
                        "thread_provisioning_and_permission_isolation_only"
                    ),
                    "cleanup_policy": (
                        "pause established Goals, then delete only Thread ids "
                        "returned to this process"
                    ),
                    "houdini_connection_attempted": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_directory = (
        PROJECT_ROOT / ".runtime" / "smoke" / "project-team-app-server" / run_id
    )
    run_directory.mkdir(parents=True, exist_ok=False)
    events = EventBuffer(max_events=4096)
    codex_executable = (
        PROJECT_ROOT / ".runtime" / "toolchains" / "codex" / "0.144.3" / "codex.exe"
    )
    environment = _child_environment(run_directory)
    command = _codex_app_server_command(
        codex_executable,
        str(Path(sys.executable).resolve()),
        backend=HIA_MCP_V2_BACKEND,
        project_root=PROJECT_ROOT,
    )
    raw_client = CodexStdioClient(
        command,
        cwd=PROJECT_ROOT,
        environment=environment,
        policy=ProtocolPolicy.from_project_root(PROJECT_ROOT),
        event_sink=events.publish,
        request_timeout=45.0,
    )
    client = _RecordingClient(raw_client)
    session = BridgeSession(
        PROJECT_ROOT,
        client,
        events,
        mcp_backend=HIA_MCP_V2_BACKEND,
        focus_state_path=run_directory / "focus-mode.json",
        project_team_state_path=run_directory / "project-team.json",
    )
    # Keep every explicitly opted-in model case bounded by the CLI timeout,
    # including coordinator-internal correction Turns.
    session._project_threads._turn_timeout = float(arguments.timeout)
    result: dict[str, Any] = {
        "ok": False,
        "run_id": run_id,
        "run_directory": str(run_directory),
        "app_server_version": "0.144.3",
        "audited_source_sha256": _audited_source_hashes(),
        "houdini_connection_attempted": False,
        "cases": [],
    }
    functional_ok = False
    try:
        session.start()
        cases: list[tuple[str, str]] = []
        if arguments.case in {"non-scene", "all"}:
            cases.append(("non-scene", "验证插件项目模式是否能正常启动"))
        if arguments.case in {"missing-hia", "all"}:
            cases.append(
                (
                    "missing-hia",
                    "在当前 Houdini 场景中创建一个简单、可编辑并经过技术与视觉审查的木制长凳。",
                )
            )
        for name, task in cases:
            result["cases"].append(
                _run_case(
                    session,
                    events,
                    client,
                    name=name,
                    task=task,
                    timeout_seconds=arguments.timeout,
                )
            )
        if arguments.case in {"reference-intake", "all"}:
            result["cases"].append(
                _run_reference_intake_case(session, events, client, run_directory)
            )
        # In ``all``, run this no-Turn case last because it intentionally keeps
        # the writer reserved until the unconditional cleanup below.
        if arguments.case in {"pre-activation", "all"}:
            result["cases"].append(_run_pre_activation_case(session, client))
        non_scene = next(
            (case for case in result["cases"] if case["case"] == "non-scene"),
            None,
        )
        non_scene_ok = bool(
            non_scene is None
            or (
                non_scene["project_status"] == "not_applicable"
                and non_scene["exact_five_roles"]
                and non_scene["turn_start_counts"]["execution"] == 0
                and non_scene["turn_start_counts"]["visual_review"] == 0
                and non_scene["turn_start_counts"]["technical_review"] == 0
                and non_scene["goal_status"] == "paused"
                and non_scene["writer_released"]
            )
        )
        pre_activation = next(
            (
                case
                for case in result["cases"]
                if case["case"] == "pre-activation"
            ),
            None,
        )
        pre_activation_ok = bool(
            pre_activation is None
            or (
                pre_activation["project_status_before_cleanup"] == "running"
                and pre_activation["exact_five_roles"]
                and pre_activation["only_execution_writes"]
                and pre_activation["no_model_turn_started"]
                and not pre_activation["goal_established_before_root_turn"]
                and pre_activation["goal_status_before_root_turn"] is None
                and pre_activation["writer_reserved_before_cleanup"]
            )
        )
        model_cases_ok = all(
            case["exact_five_roles"] and case["writer_released"]
            for case in result["cases"]
            if case["case"] != "pre-activation"
        )
        reference_intake = next(
            (
                case
                for case in result["cases"]
                if case["case"] == "reference-intake"
            ),
            None,
        )
        reference_intake_ok = bool(
            reference_intake is None
            or (
                reference_intake["exact_five_roles"]
                and reference_intake["visual_turn_count"] == 2
                and reference_intake["visual_turn_image_counts"] == [1, 16]
                and reference_intake["reference_intake_then_formal_review"]
                and reference_intake["structured_review_returned"]
                and reference_intake["execution_turns"] == 0
                and reference_intake["visual_read_only"]
                and reference_intake["visual_hia_disabled"]
                and not reference_intake["hia_or_houdini_tools_invoked"]
                and reference_intake["goal_status_before_interrupt"] is None
                and reference_intake["goal_status_after_interrupt"] == "paused"
                and reference_intake["project_interrupted_after_evidence"]
                and reference_intake["writer_released"]
            )
        )
        functional_ok = (
            non_scene_ok
            and pre_activation_ok
            and model_cases_ok
            and reference_intake_ok
        )
        result["functional_checks_passed"] = functional_ok
    except Exception as exc:  # finite smoke must preserve an inspectable artifact
        result["error"] = _redacted_error(exc)
    finally:
        cleanup = _cleanup_created_threads(session, client)
        result["cleanup"] = cleanup
        event_summary = _redacted_event_summary(events)
        result["event_summary"] = event_summary
        result["registry_paths"] = {
            "settings": str(run_directory / "project-team.json"),
            "threads": str(run_directory / "project-threads.json"),
            "transfer": str(run_directory / "thread-transfer.json"),
        }
        session.close()
        result["process_reaped"] = not client.is_running
        result["ok"] = bool(
            functional_ok and cleanup["complete"] and result["process_reaped"]
        )
        artifact = run_directory / "result.json"
        result["result_path"] = str(artifact)
        artifact.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
