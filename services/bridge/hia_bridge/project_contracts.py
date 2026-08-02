"""Typed, deterministic contracts for the project-team runtime.

This module deliberately contains no RPC, prompt, persistence, or natural-language
quality judgement.  It is the shared data boundary used by the lifecycle reducer
and the runtime adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from typing import Any, Mapping


class ProjectStatus(str, Enum):
    PROVISIONING = "provisioning"
    INTAKE = "intake"
    PLANNING = "planning"
    AUTHORIZATION = "authorization"
    EXECUTING_STAGE = "executing_stage"
    REVIEWING_STAGE = "reviewing_stage"
    REPAIRING_STAGE = "repairing_stage"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"


class Role(str, Enum):
    SUPERVISOR = "supervisor"
    PLANNING = "planning"
    EXECUTION = "execution"
    VISUAL_REVIEW = "visual_review"
    TECHNICAL_REVIEW = "technical_review"


READ_ONLY_ROLES = frozenset(
    {Role.SUPERVISOR, Role.PLANNING, Role.VISUAL_REVIEW, Role.TECHNICAL_REVIEW}
)


class RequirementStatus(str, Enum):
    ACTIVE = "active"
    PLANNED = "planned"
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"
    SUPERSEDED_BY_USER = "superseded_by_user"
    REMOVED_BY_USER = "removed_by_user"


@dataclass(frozen=True)
class RuntimeBudget:
    """Finite defaults prevent an unattended project from consuming forever."""

    max_project_turns: int = 120
    max_stage_repairs: int = 12
    max_schema_corrections: int = 6
    max_elapsed_seconds: int = 14_400
    max_no_progress_rounds: int = 3
    max_native_subagents_per_turn: int = 4
    max_total_evidence_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    kind: str
    status: RequirementStatus = RequirementStatus.ACTIVE
    source_ref: str = ""
    superseded_by: str | None = None


@dataclass(frozen=True)
class StageState:
    stage_id: str | None = None
    ordinal: int = 0
    repair_count: int = 0
    schema_correction_count: int = 0
    no_progress_rounds: int = 0
    latest_evidence_ids: tuple[str, ...] = ()
    latest_defect_hash: str | None = None
    latest_repair_hash: str | None = None


@dataclass(frozen=True)
class TurnState:
    role: Role | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    active: bool = False
    consumed_turns: int = 0


@dataclass(frozen=True)
class RoleThread:
    role: Role
    thread_id: str
    model: str | None = None
    effort: str | None = None
    service_tier: str | None = None


@dataclass(frozen=True)
class ProjectState:
    project_id: str
    goal_thread_id: str
    authoritative_task_id: str
    authoritative_task_sha256: str
    status: ProjectStatus = ProjectStatus.PROVISIONING
    roles: Mapping[Role, RoleThread] = field(default_factory=dict)
    requirements: tuple[Requirement, ...] = ()
    stage: StageState = field(default_factory=StageState)
    turn: TurnState = field(default_factory=TurnState)
    budget: RuntimeBudget = field(default_factory=RuntimeBudget)
    elapsed_seconds: int = 0
    total_evidence_bytes: int = 0
    resume_status: ProjectStatus | None = None
    attention_reason: str | None = None
    last_error: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.project_id or not self.goal_thread_id:
            raise ValueError("project_id and goal_thread_id are required")
        if not self.authoritative_task_id:
            raise ValueError("authoritative_task_id is required")
        digest = self.authoritative_task_sha256.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("authoritative_task_sha256 must be a SHA-256 hex digest")
        if self.elapsed_seconds < 0 or self.total_evidence_bytes < 0:
            raise ValueError("project counters cannot be negative")


def authoritative_task_identity(task_text: str) -> tuple[str, str]:
    """Return stable references without asking a model to restate the task."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    return f"task-{digest[:24]}", digest


def project_state_to_dict(state: ProjectState) -> dict[str, Any]:
    """Serialize enums and role-keyed mappings into a stable JSON object."""

    return {
        "project_id": state.project_id,
        "goal_thread_id": state.goal_thread_id,
        "authoritative_task_id": state.authoritative_task_id,
        "authoritative_task_sha256": state.authoritative_task_sha256,
        "status": state.status.value,
        "roles": {
            role.value: {
                "role": binding.role.value,
                "thread_id": binding.thread_id,
                "model": binding.model,
                "effort": binding.effort,
                "service_tier": binding.service_tier,
            }
            for role, binding in state.roles.items()
        },
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "kind": item.kind,
                "status": item.status.value,
                "source_ref": item.source_ref,
                "superseded_by": item.superseded_by,
            }
            for item in state.requirements
        ],
        "stage": asdict(state.stage),
        "turn": {
            **asdict(state.turn),
            "role": state.turn.role.value if state.turn.role else None,
        },
        "budget": asdict(state.budget),
        "elapsed_seconds": state.elapsed_seconds,
        "total_evidence_bytes": state.total_evidence_bytes,
        "resume_status": state.resume_status.value if state.resume_status else None,
        "attention_reason": state.attention_reason,
        "last_error": state.last_error,
        "revision": state.revision,
    }
