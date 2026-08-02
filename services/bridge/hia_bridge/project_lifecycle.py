"""Pure lifecycle reducer for project-team orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from .project_contracts import ProjectState, ProjectStatus, Role


class ProjectEvent(str, Enum):
    INTAKE_STARTED = "intake_started"
    SCENE_ELIGIBLE = "scene_eligible"
    ROLES_PROVISIONED = "roles_provisioned"
    ROLE_PROVISIONING_FAILED = "role_provisioning_failed"
    SCENE_INELIGIBLE = "scene_ineligible"
    INTAKE_UNCLEAR = "intake_unclear"
    PLAN_READY = "plan_ready"
    PLAN_AUTHORIZED = "plan_authorized"
    STAGE_EXECUTED = "stage_executed"
    REVIEWS_PASSED = "reviews_passed"
    REVIEWS_FAILED = "reviews_failed"
    GOAL_COMPLETED = "goal_completed"
    GOAL_COMPLETION_FAILED = "goal_completion_failed"
    REPAIR_READY = "repair_ready"
    PROJECT_INTERRUPTED = "project_interrupted"
    RESTART_REQUESTED = "restart_requested"
    RECOVERY_VALIDATED = "recovery_validated"
    RECOVERY_FAILED = "recovery_failed"
    PROJECT_BLOCKED = "project_blocked"
    PROJECT_FAILED = "project_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    USER_CONTINUE = "user_continue"


class ProjectCommand(str, Enum):
    START_INTAKE = "start_intake"
    PROVISION_WORKERS = "provision_workers"
    REQUEST_PLAN = "request_plan"
    REQUEST_AUTHORIZATION = "request_authorization"
    START_EXECUTION = "start_execution"
    START_REVIEWS = "start_reviews"
    REQUEST_REPAIR = "request_repair"
    ADVANCE_STAGE = "advance_stage"
    COMPLETE_GOAL = "complete_goal"
    VERIFY_RECOVERY = "verify_recovery"
    PAUSE_GOAL = "pause_goal"
    RESUME_GOAL = "resume_goal"
    SHOW_ATTENTION = "show_attention"
    RECORD_FAILURE = "record_failure"


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
    {
        ProjectStatus.PROVISIONING,
        ProjectStatus.INTAKE,
        ProjectStatus.PROVISIONING_ROLES,
        ProjectStatus.PLANNING,
        ProjectStatus.AUTHORIZATION,
        ProjectStatus.EXECUTING_STAGE,
        ProjectStatus.REVIEWING_STAGE,
        ProjectStatus.REPAIRING_STAGE,
        ProjectStatus.COMPLETING,
    }
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
    """Apply one legal event and emit deterministic side-effect commands."""

    kind = event.kind
    data = dict(event.data or {})
    status = state.status

    if kind is ProjectEvent.INTAKE_STARTED and status is ProjectStatus.PROVISIONING:
        return _next(state, ProjectStatus.INTAKE, LifecycleCommand(ProjectCommand.START_INTAKE))
    if kind is ProjectEvent.SCENE_ELIGIBLE and status is ProjectStatus.INTAKE:
        return _next(
            state,
            ProjectStatus.PROVISIONING_ROLES,
            LifecycleCommand(ProjectCommand.PROVISION_WORKERS),
        )
    if kind is ProjectEvent.ROLES_PROVISIONED and status is ProjectStatus.PROVISIONING_ROLES:
        return _next(
            state,
            ProjectStatus.PLANNING,
            LifecycleCommand(ProjectCommand.REQUEST_PLAN),
        )
    if (
        kind is ProjectEvent.ROLE_PROVISIONING_FAILED
        and status is ProjectStatus.PROVISIONING_ROLES
    ):
        error = str(data.get("error") or "role_provisioning_failed")
        return _next(
            state,
            ProjectStatus.NEEDS_ATTENTION,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": error}),
            LifecycleCommand(ProjectCommand.SHOW_ATTENTION, {"reason": error}),
            resume_status=ProjectStatus.PROVISIONING_ROLES,
            attention_reason=error,
            last_error=error,
        )
    if kind is ProjectEvent.SCENE_INELIGIBLE and status is ProjectStatus.INTAKE:
        reason = str(data.get("reason") or "not_a_houdini_scene_task")
        return _next(
            state,
            ProjectStatus.BLOCKED,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": reason}),
            attention_reason=reason,
        )
    if kind is ProjectEvent.INTAKE_UNCLEAR and status is ProjectStatus.INTAKE:
        reason = str(data.get("reason") or "scene_task_eligibility_unclear")
        return _next(
            state,
            ProjectStatus.NEEDS_ATTENTION,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": reason}),
            LifecycleCommand(ProjectCommand.SHOW_ATTENTION, {"reason": reason}),
            resume_status=ProjectStatus.INTAKE,
            attention_reason=reason,
        )
    if kind is ProjectEvent.PLAN_READY and status is ProjectStatus.PLANNING:
        return _next(
            state,
            ProjectStatus.AUTHORIZATION,
            LifecycleCommand(ProjectCommand.REQUEST_AUTHORIZATION),
        )
    if kind is ProjectEvent.PLAN_AUTHORIZED and status is ProjectStatus.AUTHORIZATION:
        return _next(
            state,
            ProjectStatus.EXECUTING_STAGE,
            LifecycleCommand(ProjectCommand.START_EXECUTION),
        )
    if kind is ProjectEvent.STAGE_EXECUTED and status is ProjectStatus.EXECUTING_STAGE:
        return _next(
            state,
            ProjectStatus.REVIEWING_STAGE,
            LifecycleCommand(ProjectCommand.START_REVIEWS),
        )
    if kind is ProjectEvent.REVIEWS_FAILED and status is ProjectStatus.REVIEWING_STAGE:
        return _next(
            state,
            ProjectStatus.REPAIRING_STAGE,
            LifecycleCommand(ProjectCommand.REQUEST_REPAIR),
        )
    if kind is ProjectEvent.REPAIR_READY and status is ProjectStatus.REPAIRING_STAGE:
        return _next(
            state,
            ProjectStatus.EXECUTING_STAGE,
            LifecycleCommand(ProjectCommand.START_EXECUTION, {"repair": True}),
        )
    if kind is ProjectEvent.REVIEWS_PASSED and status is ProjectStatus.REVIEWING_STAGE:
        if bool(data.get("final_stage")):
            return _next(
                state,
                ProjectStatus.COMPLETING,
                LifecycleCommand(ProjectCommand.COMPLETE_GOAL),
            )
        return _next(
            state,
            ProjectStatus.EXECUTING_STAGE,
            LifecycleCommand(ProjectCommand.ADVANCE_STAGE),
            LifecycleCommand(ProjectCommand.START_EXECUTION),
        )
    if kind is ProjectEvent.PROJECT_INTERRUPTED and status in _ACTIVE:
        reason = str(data.get("reason") or "interrupted")
        return _next(
            state,
            ProjectStatus.INTERRUPTED,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": reason}),
            resume_status=status,
            attention_reason=reason,
        )
    if kind is ProjectEvent.GOAL_COMPLETED and status is ProjectStatus.COMPLETING:
        return _next(
            state,
            ProjectStatus.COMPLETED,
            resume_status=None,
            attention_reason=None,
        )
    if kind is ProjectEvent.GOAL_COMPLETION_FAILED and status is ProjectStatus.COMPLETING:
        error = str(data.get("error") or "goal_completion_failed")
        return _next(
            state,
            ProjectStatus.NEEDS_ATTENTION,
            LifecycleCommand(ProjectCommand.SHOW_ATTENTION, {"reason": error}),
            resume_status=ProjectStatus.COMPLETING,
            attention_reason=error,
            last_error=error,
        )
    if kind is ProjectEvent.RESTART_REQUESTED and status is ProjectStatus.INTERRUPTED:
        return _next(
            state,
            ProjectStatus.INTERRUPTED,
            LifecycleCommand(ProjectCommand.VERIFY_RECOVERY),
            attention_reason="recovery_validation_pending",
        )
    if kind is ProjectEvent.RECOVERY_VALIDATED and status is ProjectStatus.INTERRUPTED:
        target = state.resume_status
        if target not in _ACTIVE:
            raise InvalidTransition(status, kind)
        return _next(
            state,
            target,
            LifecycleCommand(ProjectCommand.RESUME_GOAL),
            attention_reason=None,
            last_error=None,
        )
    if kind is ProjectEvent.RECOVERY_FAILED and status is ProjectStatus.INTERRUPTED:
        error = str(data.get("error") or "recovery_validation_failed")
        return _next(
            state,
            ProjectStatus.NEEDS_ATTENTION,
            LifecycleCommand(ProjectCommand.SHOW_ATTENTION, {"reason": error}),
            attention_reason=error,
            last_error=error,
        )
    if kind is ProjectEvent.USER_CONTINUE and status is ProjectStatus.NEEDS_ATTENTION:
        target = state.resume_status
        if target not in _ACTIVE:
            raise InvalidTransition(status, kind)
        return _next(
            state,
            target,
            LifecycleCommand(ProjectCommand.RESUME_GOAL),
            attention_reason=None,
        )
    if kind in {ProjectEvent.BUDGET_EXHAUSTED, ProjectEvent.NO_PROGRESS} and status in _ACTIVE:
        reason = str(data.get("reason") or kind.value)
        return _next(
            state,
            ProjectStatus.NEEDS_ATTENTION,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": reason}),
            LifecycleCommand(ProjectCommand.SHOW_ATTENTION, {"reason": reason}),
            resume_status=status,
            attention_reason=reason,
        )
    if kind is ProjectEvent.PROJECT_BLOCKED and status in _ACTIVE:
        reason = str(data.get("reason") or "blocked")
        return _next(
            state,
            ProjectStatus.BLOCKED,
            LifecycleCommand(ProjectCommand.PAUSE_GOAL, {"reason": reason}),
            attention_reason=reason,
        )
    if kind is ProjectEvent.PROJECT_FAILED and status not in {
        ProjectStatus.COMPLETED,
        ProjectStatus.FAILED,
    }:
        error = str(data.get("error") or "project_failed")
        return _next(
            state,
            ProjectStatus.FAILED,
            LifecycleCommand(ProjectCommand.RECORD_FAILURE, {"error": error}),
            last_error=error,
        )
    raise InvalidTransition(status, kind)
