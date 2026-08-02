"""Process-local role action queue for the explicit project lifecycle.

Actions are intentionally not persisted.  If Bridge exits, the registry loads
the project as ``stopped`` and the user explicitly continues from the current
stage. There are no action receipts, replay transactions, or host-error shadow
records.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import threading
from typing import Any, Mapping, Protocol

from .project_contracts import ProjectState, Role
from .project_guidance import RequirementDelta, publish_guidance
from .project_lifecycle import LifecycleCommand, LifecycleEvent, ProjectEvent, reduce_project
from .project_registry import ProjectRecord, ProjectRegistry


@dataclass(frozen=True)
class ProjectAction:
    kind: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class ProjectActionResult:
    state: ProjectState
    event: LifecycleEvent | None


class ActionExecutor(Protocol):
    def execute(self, state: ProjectState, action: ProjectAction) -> ProjectActionResult: ...


class ProjectRunner:
    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry
        self._queues: dict[str, deque[ProjectAction]] = {}
        self._lock = threading.RLock()

    def has_pending(self, project_id: str) -> bool:
        with self._lock:
            return bool(self._queues.get(project_id))

    def next_action(self, project_id: str) -> ProjectAction | None:
        with self._lock:
            queue = self._queues.get(project_id)
            return queue[0] if queue else None

    def dispatch(self, project_id: str, event: LifecycleEvent) -> ProjectRecord:
        with self._lock:
            if self._queues.get(project_id):
                raise ValueError("project already has a role action in progress")
            record = self._registry.require(project_id)
            next_state, commands = reduce_project(record.state, event)
            updated = ProjectRecord(next_state, record.authoritative_task_text)
            self._registry.put(updated, expected_revision=record.state.revision)
            self._enqueue(project_id, commands)
            return updated

    def execute_next(self, project_id: str, executor: ActionExecutor) -> ProjectRecord:
        with self._lock:
            record = self._registry.require(project_id)
            queue = self._queues.get(project_id)
            if not queue:
                raise ValueError("project has no pending role action")
            action = queue[0]
        outcome = executor.execute(record.state, action)
        with self._lock:
            current = self._registry.require(project_id)
            queue = self._queues.get(project_id)
            if not queue or queue[0] != action:
                raise ValueError("project action ownership changed while running")
            base = _merge_concurrent_user_state(current.state, outcome.state)
            queue.popleft()
            if outcome.event is None:
                next_state = replace(base, revision=current.state.revision + 1)
                commands: tuple[LifecycleCommand, ...] = ()
            else:
                next_state, commands = reduce_project(base, outcome.event)
            updated = ProjectRecord(next_state, current.authoritative_task_text)
            self._registry.put(updated, expected_revision=current.state.revision)
            self._enqueue(project_id, commands)
            if not queue:
                self._queues.pop(project_id, None)
            return updated

    def merge_guidance(
        self,
        project_id: str,
        text: str,
        *,
        target_role: Role | None = None,
        requirement_delta: RequirementDelta | None = None,
    ) -> ProjectRecord:
        """Apply confirmed native guidance to the latest lifecycle state."""

        with self._lock:
            current = self._registry.require(project_id)
            state = publish_guidance(
                current.state,
                text,
                target_role=target_role,
                requirement_delta=requirement_delta,
            )
            updated = ProjectRecord(state, current.authoritative_task_text)
            self._registry.put(updated, expected_revision=current.state.revision)
            return updated

    def cancel_and_dispatch(
        self, project_id: str, event: LifecycleEvent
    ) -> ProjectRecord:
        with self._lock:
            self._queues.pop(project_id, None)
        return self.dispatch(project_id, event)

    def fail(self, project_id: str, error: BaseException) -> ProjectRecord:
        with self._lock:
            self._queues.pop(project_id, None)
        return self.dispatch(
            project_id,
            LifecycleEvent(
                ProjectEvent.PROJECT_FAILED,
                {"error": f"{type(error).__name__}: {error}"},
            ),
        )

    def _enqueue(
        self, project_id: str, commands: tuple[LifecycleCommand, ...]
    ) -> None:
        if not commands:
            return
        queue = self._queues.setdefault(project_id, deque())
        queue.extend(
            ProjectAction(command.kind.value, dict(command.data or {}))
            for command in commands
        )


def _merge_concurrent_user_state(
    current: ProjectState, outcome: ProjectState
) -> ProjectState:
    if (
        outcome.project_id != current.project_id
        or outcome.authoritative_task_id != current.authoritative_task_id
        or outcome.authoritative_task_sha256 != current.authoritative_task_sha256
        or outcome.status is not current.status
    ):
        raise ValueError("role action changed authoritative project identity or phase")
    for role, binding in current.roles.items():
        produced = outcome.roles.get(role)
        if produced is None or produced.thread_id != binding.thread_id:
            raise ValueError("role action changed a project Thread identity")
    return replace(
        outcome,
        roles=current.roles,
        requirements=(
            current.requirements
            if current.guidance_revision > outcome.guidance_revision
            else outcome.requirements
        ),
        guidance_revision=max(
            current.guidance_revision, outcome.guidance_revision
        ),
        revision=current.revision,
    )
