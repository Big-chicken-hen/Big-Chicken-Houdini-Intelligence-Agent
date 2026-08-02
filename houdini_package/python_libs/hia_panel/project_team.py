"""Bounded view-model for native project teams shown by the Houdini Panel.

The Bridge owns the authoritative snapshot.  This module only validates and
shapes that snapshot for presentation; it does not infer project membership or
maintain a second conversation store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .runtime_diagnostics import RuntimeDiagnosticWriter


PROJECT_TEAM_SCHEMA = "hia-project-team/1"
PROJECT_TEAM_LEGACY_SCHEMA = "hia-project-team-settings/1"
PROJECT_TEAM_SCHEMAS = frozenset(
    {PROJECT_TEAM_SCHEMA, PROJECT_TEAM_LEGACY_SCHEMA}
)
PROJECT_TEAM_MODES = ("single", "team")
PROJECT_TEAM_MODE_LABELS = {
    "single": "单个 AI",
    "team": "项目团队",
}
PROJECT_TEAM_TURN_OVERRIDE_OPTIONS = (
    (PROJECT_TEAM_MODE_LABELS["single"], "single"),
    (PROJECT_TEAM_MODE_LABELS["team"], "team"),
)
PROJECT_TEAM_TURN_OVERRIDES = frozenset(
    value for _label, value in PROJECT_TEAM_TURN_OVERRIDE_OPTIONS
)
PROJECT_TEAM_ROLE_ORDER = (
    "supervisor",
    "planning",
    "execution",
    "visual_review",
    "technical_review",
)
PROJECT_TEAM_ROLE_TITLES = {
    "supervisor": "监督 AI",
    "planning": "方案 AI",
    "execution": "执行 AI",
    "visual_review": "视觉审查 AI",
    "technical_review": "技术审查 AI",
}
PROJECT_TEAM_ROLE_RESPONSIBILITIES = {
    "supervisor": "统筹目标、分工、证据和交付。",
    "planning": "整理需求并给出可执行方案。",
    "execution": "按方案完成当前项目的实际工作。",
    "visual_review": "核对视觉结果与目标是否一致。",
    "technical_review": "核对结构、可编辑性与技术证据。",
}

_ROLE_ALIASES = {
    "supervisor": "supervisor",
    "planning": "planning",
    "plan": "planning",
    "blueprint": "planning",
    "build_blueprint": "planning",
    "execution": "execution",
    "execute": "execution",
    "builder": "execution",
    "visual_review": "visual_review",
    "visual": "visual_review",
    "technical_review": "technical_review",
    "technical": "technical_review",
}

_STATUS_LABELS = {
    "pending": "等待开始",
    "queued": "等待开始",
    "planning": "制定方案",
    "ready": "可以开始",
    "in_progress": "进行中",
    "running": "进行中",
    "waiting": "等待协作",
    "needs_attention": "需要关注",
    "blocked": "受阻",
    "completed": "已完成",
    "complete": "已完成",
    "failed": "未完成",
    "cancelled": "已停止",
    "canceled": "已停止",
}

_MAX_PROJECTS = 256
_MAX_THREADS_PER_PROJECT = 12

_LEGACY_MODE_MIGRATION = {
    "off": "single",
    "suggest": "single",
    "auto": "team",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = RuntimeDiagnosticWriter._sanitize_text(value, limit).strip()
    return text or None


def _bounded_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _status_label(value: str | None) -> str:
    if not value:
        return "状态待同步"
    return _STATUS_LABELS.get(value.lower(), value)


def _normalize_progress(value: Any) -> dict[str, Any]:
    progress = _mapping(value)
    completed = _bounded_number(progress.get("completed"))
    total = _bounded_number(progress.get("total"))
    percent = _bounded_number(progress.get("percent"))
    if percent is None and completed is not None and total not in (None, 0):
        percent = (float(completed) / float(total)) * 100.0
    if percent is not None:
        percent = max(0, min(100, int(round(float(percent)))))
    label = _bounded_text(progress.get("label"), 240)
    if label is None and completed is not None and total is not None:
        label = f"{completed:g} / {total:g}"
    return {
        "completed": completed,
        "total": total,
        "percent": percent,
        "label": label or "进度待同步",
    }


def _action_enabled(
    actions: Mapping[str, Any],
    *names: str,
) -> bool:
    return any(actions.get(name) is True for name in names)


def _resolved_action(
    actions: Mapping[str, Any],
    project_actions: Mapping[str, Any],
    *names: str,
    default: bool,
) -> bool:
    if any(name in actions for name in names):
        return _action_enabled(actions, *names)
    if any(name in project_actions for name in names):
        return _action_enabled(project_actions, *names)
    return default


def _normalize_thread(
    value: Any,
    *,
    project_actions: Mapping[str, Any],
) -> dict[str, Any] | None:
    raw = _mapping(value)
    raw_role = _bounded_text(
        raw.get("role") or raw.get("role_id") or raw.get("id"),
        80,
    )
    if raw_role is None:
        return None
    role_key = _ROLE_ALIASES.get(raw_role.lower(), raw_role.lower())
    thread = _mapping(raw.get("thread"))
    thread_id = _bounded_text(
        raw.get("thread_id") or thread.get("id") or thread.get("thread_id"),
        160,
    )
    title = _bounded_text(
        raw.get("title")
        or raw.get("thread_title")
        or thread.get("title")
        or thread.get("name"),
        240,
    )
    role_title = _bounded_text(raw.get("role_title"), 120)
    role_title = (
        role_title
        or PROJECT_TEAM_ROLE_TITLES.get(role_key, raw_role)
    )
    status = _bounded_text(raw.get("status") or thread.get("status"), 80)
    model = _bounded_text(raw.get("model") or thread.get("model"), 160)
    actions = _mapping(raw.get("actions"))
    can_open = bool(
        thread_id
        and _resolved_action(
            actions,
            project_actions,
            "open",
            "open_thread",
            default=True,
        )
    )
    can_guide = bool(
        thread_id
        and _resolved_action(
            actions,
            project_actions,
            "guide",
            "append_guidance",
            default=False,
        )
    )
    return {
        "role": raw_role,
        "role_key": role_key,
        "role_title": role_title,
        "responsibility": _bounded_text(raw.get("responsibility"), 320)
        or PROJECT_TEAM_ROLE_RESPONSIBILITIES.get(role_key, ""),
        "thread_id": thread_id,
        "title": title or f"{role_title} 任务",
        "status": status,
        "status_label": _status_label(status),
        "model": model,
        "model_label": model or "沿用项目模型",
        "progress": _normalize_progress(raw.get("progress")),
        "can_open": can_open,
        "can_choose_model": can_open,
        "can_guide": can_guide,
    }


def _role_sort_key(role: Mapping[str, Any]) -> tuple[int, str]:
    role_key = str(role.get("role_key") or "")
    try:
        index = PROJECT_TEAM_ROLE_ORDER.index(role_key)
    except ValueError:
        index = len(PROJECT_TEAM_ROLE_ORDER)
    return index, str(role.get("role_title") or role_key)


def _normalize_project(value: Any) -> dict[str, Any] | None:
    raw = _mapping(value)
    project_id = _bounded_text(
        raw.get("project_id") or raw.get("id"),
        160,
    )
    if project_id is None:
        return None
    title = _bounded_text(raw.get("title") or raw.get("name"), 240)
    status = _bounded_text(raw.get("status"), 80)
    actions = _mapping(raw.get("actions"))
    raw_threads = raw.get("threads", raw.get("roles"))
    threads: list[dict[str, Any]] = []
    seen_roles: set[tuple[str, str | None]] = set()
    for raw_thread in (
        raw_threads if isinstance(raw_threads, list) else []
    )[:_MAX_THREADS_PER_PROJECT]:
        thread = _normalize_thread(raw_thread, project_actions=actions)
        if thread is None:
            continue
        identity = (thread["role"], thread["thread_id"])
        if identity in seen_roles:
            continue
        seen_roles.add(identity)
        threads.append(thread)
    threads.sort(key=_role_sort_key)
    member_thread_ids = tuple(
        dict.fromkeys(
            thread["thread_id"]
            for thread in threads
            if isinstance(thread.get("thread_id"), str)
        )
    )
    root_thread_id = _bounded_text(raw.get("root_thread_id"), 160)
    if root_thread_id is None:
        root_thread_id = next(
            (
                thread["thread_id"]
                for thread in threads
                if thread.get("role_key") == "supervisor"
                and isinstance(thread.get("thread_id"), str)
            ),
            None,
        )
    updated_at = _bounded_number(raw.get("updated_at"))
    return {
        "project_id": project_id,
        "title": title or "未命名项目",
        "status": status,
        "status_label": _status_label(status),
        "stage": _bounded_text(raw.get("stage"), 160),
        "progress": _normalize_progress(raw.get("progress")),
        "updated_at": updated_at,
        "root_thread_id": root_thread_id,
        "member_thread_ids": member_thread_ids,
        "threads": threads,
        "can_append_guidance": _action_enabled(
            actions, "append_guidance"
        ),
    }


def normalize_project_team(raw: Any) -> dict[str, Any]:
    """Normalize one authoritative project snapshot without local inference."""

    root = _mapping(raw)
    schema = _bounded_text(root.get("schema"), 128)
    available = schema in PROJECT_TEAM_SCHEMAS
    settings = _mapping(root.get("settings"))
    root_mode = _bounded_text(root.get("mode"), 32)
    settings_mode = _bounded_text(settings.get("mode"), 32)
    settings_mode = _LEGACY_MODE_MIGRATION.get(settings_mode, settings_mode)
    root_mode = _LEGACY_MODE_MIGRATION.get(root_mode, root_mode)
    mode = settings_mode if settings_mode in PROJECT_TEAM_MODES else root_mode
    if mode not in PROJECT_TEAM_MODES:
        mode = "single"
    state_status = _bounded_text(root.get("state_status"), 64)
    projects: list[dict[str, Any]] = []
    raw_projects = root.get("projects")
    for raw_project in (
        raw_projects if isinstance(raw_projects, list) else []
    )[:_MAX_PROJECTS]:
        project = _normalize_project(raw_project)
        if project is not None:
            projects.append(project)
    worker_thread_ids = tuple(
        dict.fromkeys(
            thread_id
            for project in projects
            for thread_id in project["member_thread_ids"]
        )
    )
    return {
        "available": available,
        "schema": schema,
        "revision": _bounded_text(root.get("revision"), 160)
        or _bounded_number(root.get("revision")),
        "state_status": state_status,
        "mode": mode,
        "mode_label": PROJECT_TEAM_MODE_LABELS[mode],
        "settings_writable": available and settings.get("writable") is True,
        "projects": projects,
        "worker_thread_ids": worker_thread_ids,
    }


def project_for_thread(
    snapshot: Mapping[str, Any],
    thread_id: str | None,
) -> dict[str, Any] | None:
    """Return the project containing an authoritative member Thread id."""

    if not isinstance(thread_id, str):
        return None
    projects = snapshot.get("projects")
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict):
            continue
        if thread_id in project.get("member_thread_ids", ()):
            return project
    return None


def project_thread_id_transfers(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[dict[str, str], ...]:
    """Return explicit same-project, same-role Thread identity changes."""

    def identities(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], str]:
        values: dict[tuple[str, str], str] = {}
        projects = snapshot.get("projects")
        for project in projects if isinstance(projects, list) else []:
            if not isinstance(project, Mapping):
                continue
            project_id = project.get("project_id")
            threads = project.get("threads")
            if not isinstance(project_id, str) or not isinstance(threads, list):
                continue
            for thread in threads:
                if not isinstance(thread, Mapping):
                    continue
                role = thread.get("role_key") or thread.get("role")
                thread_id = thread.get("thread_id")
                if isinstance(role, str) and isinstance(thread_id, str):
                    values[(project_id, role)] = thread_id
        return values

    before = identities(previous)
    after = identities(current)
    transfers: list[dict[str, str]] = []
    for identity, old_thread_id in before.items():
        new_thread_id = after.get(identity)
        if not isinstance(new_thread_id, str) or new_thread_id == old_thread_id:
            continue
        transfers.append(
            {
                "project_id": identity[0],
                "role": identity[1],
                "old_thread_id": old_thread_id,
                "new_thread_id": new_thread_id,
            }
        )
    return tuple(transfers)


def replace_project_thread_id(
    snapshot: Mapping[str, Any],
    old_thread_id: str,
    new_thread_id: str,
    *,
    project_id: str | None = None,
    role: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Apply one authoritative transfer event to a normalized snapshot."""

    root = dict(snapshot)
    if (
        not old_thread_id
        or not new_thread_id
        or old_thread_id == new_thread_id
    ):
        return root, False
    existing_ids = {
        value
        for value in snapshot.get("worker_thread_ids", ())
        if isinstance(value, str)
    }
    if new_thread_id in existing_ids and new_thread_id != old_thread_id:
        return root, False

    projects = snapshot.get("projects")
    project_values = list(projects) if isinstance(projects, list) else []
    matches: list[tuple[int, int]] = []
    for project_index, project in enumerate(project_values):
        if not isinstance(project, Mapping):
            continue
        if project_id is not None and project.get("project_id") != project_id:
            continue
        threads = project.get("threads")
        for thread_index, thread in enumerate(
            threads if isinstance(threads, list) else []
        ):
            if not isinstance(thread, Mapping):
                continue
            thread_role = thread.get("role_key") or thread.get("role")
            if (
                thread.get("thread_id") == old_thread_id
                and (role is None or thread_role == role)
            ):
                matches.append((project_index, thread_index))
    if len(matches) != 1:
        return root, False

    project_index, thread_index = matches[0]
    project = dict(project_values[project_index])
    threads = list(project.get("threads", []))
    thread = dict(threads[thread_index])
    thread["thread_id"] = new_thread_id
    threads[thread_index] = thread
    project["threads"] = threads
    project["member_thread_ids"] = tuple(
        dict.fromkeys(
            value.get("thread_id")
            for value in threads
            if isinstance(value, Mapping)
            and isinstance(value.get("thread_id"), str)
        )
    )
    if project.get("root_thread_id") == old_thread_id:
        project["root_thread_id"] = new_thread_id
    project_values[project_index] = project
    root["projects"] = project_values
    root["worker_thread_ids"] = tuple(
        dict.fromkeys(
            thread_id
            for project_value in project_values
            if isinstance(project_value, Mapping)
            for thread_id in project_value.get("member_thread_ids", ())
            if isinstance(thread_id, str)
        )
    )
    return root, True


def top_level_history_records(
    snapshot: Mapping[str, Any],
    threads: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fold member Threads into project containers for the top-level history."""

    worker_ids = {
        value
        for value in snapshot.get("worker_thread_ids", ())
        if isinstance(value, str)
    }
    project_records: list[dict[str, Any]] = []
    ordinary_records: list[dict[str, Any]] = []
    projects = snapshot.get("projects")
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict):
            continue
        project_records.append(
            {
                "kind": "project",
                "project_id": project["project_id"],
                "name": project["title"],
                "status": project.get("status"),
                "status_label": project.get("status_label"),
                "progress": project.get("progress"),
                "updated_at": project.get("updated_at"),
                "root_thread_id": project.get("root_thread_id"),
                "member_thread_ids": project.get("member_thread_ids", ()),
                "threads": tuple(
                    dict(thread)
                    for thread in project.get("threads", [])
                    if isinstance(thread, Mapping)
                ),
            }
        )
    for thread in threads:
        thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or thread_id in worker_ids:
            continue
        record = dict(thread)
        record["kind"] = "thread"
        ordinary_records.append(record)

    def sort_value(record: Mapping[str, Any]) -> float:
        updated_at = _bounded_number(record.get("updated_at"))
        return float(updated_at) if updated_at is not None else 0.0

    project_records.sort(key=sort_value, reverse=True)
    ordinary_records.sort(key=sort_value, reverse=True)
    ordinary_limit = max(0, int(limit))
    return project_records + ordinary_records[:ordinary_limit]
