"""Thin project-team facade for Session, HTTP, and Panel integration."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping
from typing import Protocol
import uuid

from .project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from .project_guidance import RequirementDelta, publish_guidance
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectAttachment, ProjectRecord, ProjectRegistry
from .project_runner import ProjectRunner
from .project_thread_factory import AppServerClient, ProjectThreadFactory, ROLE_TITLES


PROJECT_TEAM_SCHEMA = "hia-project-team/2"
SETTINGS_SCHEMA = "hia-project-team-settings/3"
PROJECT_MODES = frozenset({"single", "team"})
_TERMINAL = frozenset(
    {ProjectStatus.COMPLETED, ProjectStatus.FAILED, ProjectStatus.NOT_APPLICABLE}
)


class ProjectWorkflowControl(Protocol):
    def start(self, project_id: str) -> bool: ...

    def stop(self, project_id: str) -> bool: ...

    def resume(self, project_id: str) -> bool: ...


class ProjectTeamSettings:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.RLock()

    def get(self) -> str:
        with self._lock:
            if not self._path.exists():
                return "single"
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                return "single"
            mode = raw.get("mode") if isinstance(raw, Mapping) else None
            return mode if raw.get("schema") == SETTINGS_SCHEMA and mode in PROJECT_MODES else "single"

    def set(self, mode: str) -> None:
        if mode not in PROJECT_MODES:
            raise ValueError("mode must be single or team")
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps({"schema": SETTINGS_SCHEMA, "mode": mode}) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._path)


class ProjectTeamService:
    def __init__(
        self,
        *,
        client: AppServerClient,
        project_root: Path,
        registry: ProjectRegistry,
        settings: ProjectTeamSettings,
        selected_backend: str = "hia_mcp_v2",
        workflow: ProjectWorkflowControl | None = None,
    ) -> None:
        self._client = client
        self._project_root = project_root.resolve()
        self._registry = registry
        self._settings = settings
        self._factory = ProjectThreadFactory(client, project_root, selected_backend)
        self._runner = ProjectRunner(registry)
        self._workflow = workflow
        self._lock = threading.RLock()

    def set_mode(self, mode: str) -> dict[str, Any]:
        self._settings.set(mode)
        return self.snapshot()

    def route(self, override: str | None) -> str:
        if override is not None and override not in PROJECT_MODES:
            raise ValueError("team_override must be single or team")
        return override or self._settings.get()

    def start_team_project(
        self,
        *,
        task_text: str,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
        local_image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        task_id, digest = authoritative_task_identity(task_text)
        project_id = f"project-{uuid.uuid4()}"
        provisional = f"pending-{project_id}"
        state = ProjectState(
            project_id=project_id,
            goal_thread_id=provisional,
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
        )
        state = self._factory.start_supervisor(
            state,
            model=model,
            effort=effort,
            service_tier=service_tier,
        )
        supervisor_id = state.roles[Role.SUPERVISOR].thread_id
        try:
            goal_result = self._client.request(
                "thread/goal/set",
                {
                    "threadId": supervisor_id,
                    "objective": f"Houdini project {task_id}",
                    "status": "active",
                    "tokenBudget": None,
                },
            )
            _validate_goal_result(goal_result, supervisor_id)
        except Exception as original_error:
            try:
                self._client.request("thread/delete", {"threadId": supervisor_id})
            except Exception as cleanup_error:
                raise RuntimeError(
                    "native Goal creation failed and precise Supervisor cleanup "
                    f"also failed: goal={original_error}; cleanup={cleanup_error}"
                ) from original_error
            raise
        record = ProjectRecord(
            state,
            task_text,
            self._attachment_refs(local_image_paths or []),
        )
        self._registry.put(record)
        record = self._runner.dispatch(
            project_id, LifecycleEvent(ProjectEvent.INTAKE_STARTED)
        )
        if self._workflow is not None:
            self._workflow.start(project_id)
        return {
            "project_id": project_id,
            "root_thread_id": supervisor_id,
            "routing": "team",
            "project_team": self.snapshot(),
            "pending_effect": record.state.pending_effects[0].kind,
        }

    def append_guidance(
        self,
        *,
        project_id: str,
        text: str,
        thread_id: str | None = None,
        requirement_delta: RequirementDelta | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status in _TERMINAL:
                raise ValueError("project no longer accepts guidance")
            target_role = None
            if thread_id is not None:
                matches = [
                    role
                    for role, binding in record.state.roles.items()
                    if binding.thread_id == thread_id
                ]
                if len(matches) != 1:
                    raise ValueError("thread_id is not an explicit member of this project")
                target_role = matches[0]
            state = publish_guidance(
                record.state,
                text,
                target_role=target_role,
                requirement_delta=requirement_delta,
            )
            self._registry.put(
                ProjectRecord(
                    state, record.authoritative_task_text, record.attachments
                ),
                expected_revision=record.state.revision,
            )
        return self.snapshot()

    def continue_project(self, *, project_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status is not ProjectStatus.NEEDS_ATTENTION:
                raise ValueError("only a needs_attention project can continue")
            if record.state.pending_effects:
                raise ValueError("project attention transition is not fully acknowledged")
            self._runner.dispatch(
                project_id,
                LifecycleEvent(ProjectEvent.USER_CONTINUE),
            )
        if self._workflow is not None:
            self._workflow.resume(project_id)
        return self.snapshot()

    def stop_project(self, *, project_id: str) -> dict[str, Any]:
        record = self._registry.require(project_id)
        if record.state.status in _TERMINAL:
            raise ValueError("terminal project cannot be stopped")
        if self._workflow is None:
            raise ValueError("project workflow is unavailable")
        self._workflow.stop(project_id)
        return self.snapshot()

    def set_role_runtime(
        self,
        *,
        project_id: str,
        thread_id: str,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status in _TERMINAL:
                raise ValueError("terminal project role runtime cannot be changed")
            matches = [
                role
                for role, binding in record.state.roles.items()
                if binding.thread_id == thread_id
            ]
            if len(matches) != 1:
                raise ValueError("thread_id is not an explicit member of this project")
            role = matches[0]
            roles = dict(record.state.roles)
            roles[role] = replace(
                roles[role],
                model=model,
                effort=effort,
                service_tier=service_tier,
            )
            state = replace(record.state, roles=roles, revision=record.state.revision + 1)
            self._registry.put(
                ProjectRecord(
                    state, record.authoritative_task_text, record.attachments
                ),
                expected_revision=record.state.revision,
            )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        records = self._registry.list()
        projects = [self._public_project(record) for record in records]
        return {
            "schema": PROJECT_TEAM_SCHEMA,
            "revision": max((record.state.revision for record in records), default=0),
            "state_status": "ready",
            "settings": {"mode": self._settings.get(), "writable": True},
            "projects": projects,
        }

    @staticmethod
    def _public_project(record: ProjectRecord) -> dict[str, Any]:
        state = record.state
        runtime_allowed = state.status not in _TERMINAL
        guidance_allowed = state.status not in _TERMINAL
        threads = []
        for role in Role:
            binding = state.roles.get(role)
            if binding is None:
                continue
            threads.append(
                {
                    "role": role.value,
                    "role_title": ROLE_TITLES[role],
                    "thread_id": binding.thread_id,
                    "model": binding.model,
                    "effort": binding.effort,
                    "service_tier": binding.service_tier,
                    "status": (
                        "running"
                        if state.turns.get(role) and state.turns[role].active
                        else "waiting"
                    ),
                    "actions": {
                        "open_thread": True,
                        "append_guidance": guidance_allowed,
                        "set_role_runtime": runtime_allowed,
                    },
                }
            )
        return {
            "project_id": state.project_id,
            "title": record.authoritative_task_text.strip().splitlines()[0][:160],
            "status": state.status.value,
            "stage": state.stage.stage_id,
            "root_thread_id": state.goal_thread_id,
            "attention_reason": state.attention_reason,
            "consumed_turns": sum(item.consumed_turns for item in state.turns.values()),
            "last_error": state.last_error,
            "latest_evidence_ids": list(state.stage.latest_evidence_ids),
            "attachment_count": len(record.attachments),
            "actions": {
                "append_guidance": guidance_allowed,
                "continue": state.status is ProjectStatus.NEEDS_ATTENTION,
                "stop": state.status not in _TERMINAL,
            },
            "threads": threads,
        }

    def _attachment_refs(self, values: list[str]) -> tuple[ProjectAttachment, ...]:
        if len(values) > 16:
            raise ValueError("a project task accepts at most 16 attachments")
        attachments: list[ProjectAttachment] = []
        total = 0
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError("attachment path must be a non-empty string")
            path = Path(value).resolve(strict=True)
            try:
                path.relative_to(self._project_root)
            except ValueError as exc:
                raise ValueError("attachment path must stay inside the project") from exc
            if not path.is_file():
                raise ValueError("attachment path must reference a file")
            size = path.stat().st_size
            total += size
            if total > 128 * 1024 * 1024:
                raise ValueError("project task attachments exceed 128 MiB")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            attachments.append(ProjectAttachment(str(path), digest, size))
        return tuple(attachments)


def _validate_goal_result(value: Any, expected_thread_id: str) -> None:
    goal = value.get("goal") if isinstance(value, Mapping) else None
    if not isinstance(goal, Mapping):
        raise ValueError("thread/goal/set did not return a Goal")
    thread_id = goal.get("threadId", expected_thread_id)
    if thread_id != expected_thread_id or goal.get("status") != "active":
        raise ValueError("native Goal identity or status is invalid")
