"""Small deterministic lifecycle for the five native project Threads.

The reducer describes only the visible project phase. It does not model role
provisioning, a second goal authority, persisted actions, restart replay, or a
separate repair authorization phase.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from .project_contracts import ProjectState, ProjectStatus


class ProjectEvent(str, Enum):
    PROJECT_STARTED = "project_started"
    PROJECT_ACCEPTED = "project_accepted"
    PROJECT_ANSWERED = "project_answered"
    PLAN_READY = "plan_ready"
    PLAN_AUTHORIZED = "plan_authorized"
    STAGE_EXECUTED = "stage_executed"
    REVIEWS_PASSED = "reviews_passed"
    REVIEWS_FAILED = "reviews_failed"
    PROJECT_INTERRUPTED = "project_interrupted"
    PROJECT_BLOCKED = "project_blocked"
    PROJECT_FAILED = "project_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    USER_CONTINUE = "user_continue"


class ProjectCommand(str, Enum):
    START_SUPERVISOR = "start_supervisor"
    REQUEST_PLAN = "request_plan"
    REQUEST_AUTHORIZATION = "request_authorization"
    START_EXECUTION = "start_execution"
    START_REVIEWS = "start_reviews"


@dataclass(frozen=True)
class LifecycleEvent:
    kind: ProjectEvent
    data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LifecycleCommand:
    kind: ProjectCommand
    data: Mapping[str, Any] | None = None


class InvalidTransition(ValueError):
    def __init__(self, status: ProjectStatus, event: ProjectEvent) -> None:
        self.status = status
        self.event = event
        super().__init__(f"event {event.value} is not allowed from {status.value}")


_ACTIVE = frozenset(
    {ProjectStatus.PLANNING, ProjectStatus.EXECUTING, ProjectStatus.REVIEWING}
)


def _next(
    state: ProjectState,
    status: ProjectStatus,
    *commands: LifecycleCommand,
    **changes: Any,
) -> tuple[ProjectState, tuple[LifecycleCommand, ...]]:
    return (
        replace(state, status=status, revision=state.revision + 1, **changes),
        tuple(commands),
    )


def reduce_project(
    state: ProjectState, event: LifecycleEvent
) -> tuple[ProjectState, tuple[LifecycleCommand, ...]]:
    """Apply one explicit phase event and return the next role action."""

    kind = event.kind
    data = dict(event.data or {})
    status = state.status

    if kind is ProjectEvent.PROJECT_STARTED and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.PLANNING,
            LifecycleCommand(ProjectCommand.START_SUPERVISOR, data),
        )
    if kind is ProjectEvent.PROJECT_ACCEPTED and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.PLANNING,
            LifecycleCommand(ProjectCommand.REQUEST_PLAN),
        )
    if kind is ProjectEvent.PROJECT_ANSWERED and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.COMPLETED,
            attention_reason=None,
            last_error=None,
        )
    if kind is ProjectEvent.PLAN_READY and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.PLANNING,
            LifecycleCommand(ProjectCommand.REQUEST_AUTHORIZATION),
        )
    if kind is ProjectEvent.PLAN_AUTHORIZED and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.EXECUTING,
            LifecycleCommand(ProjectCommand.START_EXECUTION),
        )
    if kind is ProjectEvent.STAGE_EXECUTED and status is ProjectStatus.EXECUTING:
        return _next(
            state,
            ProjectStatus.REVIEWING,
            LifecycleCommand(ProjectCommand.START_REVIEWS),
        )
    if kind is ProjectEvent.REVIEWS_FAILED and status is ProjectStatus.REVIEWING:
        return _next(
            state,
            ProjectStatus.EXECUTING,
            LifecycleCommand(
                ProjectCommand.START_EXECUTION,
                {"repair": True},
            ),
        )
    if kind is ProjectEvent.REVIEWS_PASSED and status is ProjectStatus.REVIEWING:
        if bool(data.get("final_stage")):
            return _next(
                state,
                ProjectStatus.COMPLETED,
                attention_reason=None,
                last_error=None,
            )
        next_stage_id = data.get("next_stage_id")
        if not isinstance(next_stage_id, str) or not next_stage_id:
            raise InvalidTransition(status, kind)
        return _next(
            state,
            ProjectStatus.EXECUTING,
            LifecycleCommand(ProjectCommand.START_EXECUTION),
            stage=replace(
                state.stage,
                stage_id=next_stage_id,
                ordinal=state.stage.ordinal + 1,
                repair_count=0,
                schema_correction_count=0,
                latest_evidence_ids=(),
            ),
        )
    if kind is ProjectEvent.PROJECT_INTERRUPTED and status in _ACTIVE:
        return _next(state, ProjectStatus.STOPPED)
    if kind is ProjectEvent.USER_CONTINUE and status is ProjectStatus.STOPPED:
        target = (
            ProjectStatus.EXECUTING
            if state.stage.stage_id is not None
            else ProjectStatus.PLANNING
        )
        command = (
            ProjectCommand.START_EXECUTION
            if target is ProjectStatus.EXECUTING
            else ProjectCommand.REQUEST_PLAN
        )
        return _next(
            state,
            target,
            LifecycleCommand(command, {"restart_stage": True}),
            attention_reason=None,
            last_error=None,
        )
    if kind in {
        ProjectEvent.PROJECT_BLOCKED,
        ProjectEvent.BUDGET_EXHAUSTED,
    } and status in _ACTIVE:
        reason = str(data.get("reason") or kind.value)
        return _next(
            state,
            ProjectStatus.WAITING_USER,
            attention_reason=reason,
        )
    if kind is ProjectEvent.USER_CONTINUE and status is ProjectStatus.WAITING_USER:
        target = (
            ProjectStatus.EXECUTING
            if state.stage.stage_id is not None
            else ProjectStatus.PLANNING
        )
        command = (
            ProjectCommand.START_EXECUTION
            if target is ProjectStatus.EXECUTING
            else ProjectCommand.REQUEST_PLAN
        )
        return _next(
            state,
            target,
            LifecycleCommand(command, {"continue": True}),
            attention_reason=None,
            last_error=None,
        )
    if kind is ProjectEvent.PROJECT_FAILED and status not in {
        ProjectStatus.COMPLETED,
        ProjectStatus.FAILED,
    }:
        error = str(data.get("error") or "project_failed")
        return _next(state, ProjectStatus.FAILED, last_error=error)
    raise InvalidTransition(status, kind)
