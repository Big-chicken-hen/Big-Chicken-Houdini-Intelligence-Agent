"""Typed, deterministic contracts for the project-team runtime.

This module deliberately contains no RPC, prompt, persistence, or natural-language
quality judgement.  It is the shared data boundary used by the lifecycle reducer
and the runtime adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from types import MappingProxyType
from typing import Any, Mapping


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


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
    max_elapsed_seconds: int = 14_400
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
    latest_evidence_ids: tuple[str, ...] = ()


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
    authoritative_task_id: str
    authoritative_task_sha256: str
    status: ProjectStatus = ProjectStatus.PLANNING
    roles: Mapping[Role, RoleThread] = field(default_factory=dict)
    requirements: tuple[Requirement, ...] = ()
    guidance_revision: int = 0
    plan_rejection_count: int = 0
    stage: StageState = field(default_factory=StageState)
    turns: Mapping[Role, TurnState] = field(default_factory=dict)
    budget: RuntimeBudget = field(default_factory=RuntimeBudget)
    elapsed_seconds: int = 0
    total_evidence_bytes: int = 0
    attention_reason: str | None = None
    last_error: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.authoritative_task_id:
            raise ValueError("authoritative_task_id is required")
        digest = self.authoritative_task_sha256.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("authoritative_task_sha256 must be a SHA-256 hex digest")
        if self.elapsed_seconds < 0 or self.total_evidence_bytes < 0:
            raise ValueError("project counters cannot be negative")
        revision_values = (self.guidance_revision, self.plan_rejection_count)
        if any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in revision_values
        ):
            raise ValueError("project revisions must be non-negative integers")
        role_values = dict(self.roles)
        thread_ids = [binding.thread_id for binding in role_values.values()]
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("project role Thread IDs must be unique")
        object.__setattr__(self, "roles", MappingProxyType(role_values))
        object.__setattr__(self, "turns", MappingProxyType(dict(self.turns)))

    @property
    def supervisor_thread_id(self) -> str:
        supervisor = self.roles.get(Role.SUPERVISOR)
        if supervisor is None:
            raise ValueError("project has no Supervisor Thread")
        return supervisor.thread_id


def authoritative_task_identity(task_text: str) -> tuple[str, str]:
    """Return stable references without asking a model to restate the task."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    return f"task-{digest[:24]}", digest
