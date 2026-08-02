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
    PROVISIONING = "provisioning"
    INTAKE = "intake"
    PROVISIONING_ROLES = "provisioning_roles"
    PLANNING = "planning"
    AUTHORIZATION = "authorization"
    EXECUTING_STAGE = "executing_stage"
    REVIEWING_STAGE = "reviewing_stage"
    REPAIRING_STAGE = "repairing_stage"
    COMPLETING = "completing"
    PAUSING = "pausing"
    RESUMING = "resuming"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"
    NOT_APPLICABLE = "not_applicable"


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


class GuidanceScope(str, Enum):
    PROJECT = "project"
    ROLE = "role"


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
class GuidanceRecord:
    guidance_id: str
    revision: int
    text: str
    scope: GuidanceScope = GuidanceScope.PROJECT
    target_role: Role | None = None
    requirement_delta: Mapping[str, Any] | None = None
    force_replan: bool = False

    def __post_init__(self) -> None:
        if not self.guidance_id or not self.text.strip() or self.revision < 1:
            raise ValueError("guidance requires an ID, text, and positive revision")
        if self.scope is GuidanceScope.ROLE and self.target_role is None:
            raise ValueError("role guidance requires target_role")
        if self.scope is GuidanceScope.PROJECT and self.target_role is not None:
            raise ValueError("project guidance cannot have target_role")
        if self.requirement_delta is not None:
            object.__setattr__(
                self,
                "requirement_delta",
                MappingProxyType(dict(self.requirement_delta)),
            )
        if not isinstance(self.force_replan, bool):
            raise ValueError("guidance force_replan must be boolean")


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
    latest_screenshot_hash: str | None = None
    latest_coverage_hash: str | None = None


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
    compaction_count: int = 0
    compaction_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.compaction_count < 0:
            raise ValueError("role compaction_count cannot be negative")
        if self.compaction_count != len(self.compaction_event_ids):
            raise ValueError("role compaction count and event identities disagree")
        if len(set(self.compaction_event_ids)) != len(self.compaction_event_ids):
            raise ValueError("role compaction event identities must be unique")


@dataclass(frozen=True)
class PendingEffect:
    effect_id: str
    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.effect_id or not self.kind:
            raise ValueError("pending effect requires effect_id and kind")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class ProjectState:
    project_id: str
    goal_thread_id: str
    authoritative_task_id: str
    authoritative_task_sha256: str
    status: ProjectStatus = ProjectStatus.PROVISIONING
    roles: Mapping[Role, RoleThread] = field(default_factory=dict)
    requirements: tuple[Requirement, ...] = ()
    guidance: tuple[GuidanceRecord, ...] = ()
    guidance_consumed: Mapping[Role, int] = field(default_factory=dict)
    stage: StageState = field(default_factory=StageState)
    turns: Mapping[Role, TurnState] = field(default_factory=dict)
    budget: RuntimeBudget = field(default_factory=RuntimeBudget)
    elapsed_seconds: int = 0
    total_evidence_bytes: int = 0
    resume_status: ProjectStatus | None = None
    pause_target: ProjectStatus | None = None
    attention_reason: str | None = None
    last_error: str | None = None
    pending_effects: tuple[PendingEffect, ...] = ()
    recovery_required: bool = False
    recovery_return_status: ProjectStatus | None = None
    recovery_pending_effects: tuple[PendingEffect, ...] = ()
    plan_stale: bool = False
    blueprint_revision: int = 0
    authorized_blueprint_revision: int = 0
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
        revision_values = (
            self.blueprint_revision,
            self.authorized_blueprint_revision,
        )
        if any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in revision_values
        ):
            raise ValueError("blueprint revisions must be non-negative integers")
        if self.authorized_blueprint_revision > self.blueprint_revision:
            raise ValueError("authorized blueprint revision cannot exceed current revision")
        if not isinstance(self.plan_stale, bool):
            raise ValueError("plan_stale must be boolean")
        if not isinstance(self.recovery_required, bool):
            raise ValueError("recovery_required must be boolean")
        if self.recovery_required:
            recovery_target = self.recovery_return_status or self.resume_status
            if recovery_target is None:
                raise ValueError("recovery_required needs an exact return status")
        elif self.recovery_return_status is not None or self.recovery_pending_effects:
            raise ValueError("recovery fields require recovery_required")
        role_values = dict(self.roles)
        thread_ids = [binding.thread_id for binding in role_values.values()]
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("project role Thread IDs must be unique")
        supervisor = role_values.get(Role.SUPERVISOR)
        if supervisor is not None and supervisor.thread_id != self.goal_thread_id:
            raise ValueError("Supervisor Thread must own the native Goal")
        object.__setattr__(self, "roles", MappingProxyType(role_values))
        object.__setattr__(self, "turns", MappingProxyType(dict(self.turns)))
        object.__setattr__(
            self,
            "guidance_consumed",
            MappingProxyType(dict(self.guidance_consumed)),
        )


def authoritative_task_identity(task_text: str) -> tuple[str, str]:
    """Return stable references without asking a model to restate the task."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    return f"task-{digest[:24]}", digest


def native_goal_objective(task_text: str, task_id: str, *, limit: int = 180) -> str:
    """Build a readable, bounded Goal title without copying the whole request."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be non-empty")
    title = " ".join(task_text.strip().splitlines()[0].split())
    suffix = f" · {task_id}"
    available = limit - len(suffix)
    if available < 2:
        raise ValueError("goal objective limit is too small")
    if len(title) > available:
        title = title[: available - 1].rstrip() + "…"
    return title + suffix


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
                "compaction_count": binding.compaction_count,
                "compaction_event_ids": list(binding.compaction_event_ids),
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
        "guidance": [
            {
                "guidance_id": item.guidance_id,
                "revision": item.revision,
                "text": item.text,
                "scope": item.scope.value,
                "target_role": item.target_role.value if item.target_role else None,
                "requirement_delta": (
                    dict(item.requirement_delta)
                    if item.requirement_delta is not None
                    else None
                ),
                "force_replan": item.force_replan,
            }
            for item in state.guidance
        ],
        "guidance_consumed": {
            role.value: revision
            for role, revision in state.guidance_consumed.items()
        },
        "stage": asdict(state.stage),
        "turns": {
            role.value: {
                **asdict(turn),
                "role": turn.role.value if turn.role else None,
            }
            for role, turn in state.turns.items()
        },
        "budget": asdict(state.budget),
        "elapsed_seconds": state.elapsed_seconds,
        "total_evidence_bytes": state.total_evidence_bytes,
        "resume_status": state.resume_status.value if state.resume_status else None,
        "pause_target": state.pause_target.value if state.pause_target else None,
        "attention_reason": state.attention_reason,
        "last_error": state.last_error,
        "pending_effects": [
            {
                "effect_id": effect.effect_id,
                "kind": effect.kind,
                "data": dict(effect.data),
            }
            for effect in state.pending_effects
        ],
        "recovery_required": state.recovery_required,
        "recovery_return_status": (
            state.recovery_return_status.value
            if state.recovery_return_status
            else None
        ),
        "recovery_pending_effects": [
            {
                "effect_id": effect.effect_id,
                "kind": effect.kind,
                "data": dict(effect.data),
            }
            for effect in state.recovery_pending_effects
        ],
        "plan_stale": state.plan_stale,
        "blueprint_revision": state.blueprint_revision,
        "authorized_blueprint_revision": state.authorized_blueprint_revision,
        "revision": state.revision,
    }


def project_state_from_dict(value: Mapping[str, Any]) -> ProjectState:
    """Parse a persisted state without guessing identities from display text."""

    def required_text(name: str) -> str:
        item = value.get(name)
        if not isinstance(item, str) or not item:
            raise ValueError(f"{name} must be a non-empty string")
        return item

    raw_roles = value.get("roles", {})
    if not isinstance(raw_roles, Mapping):
        raise ValueError("roles must be an object")
    roles: dict[Role, RoleThread] = {}
    for key, raw_binding in raw_roles.items():
        if not isinstance(key, str) or not isinstance(raw_binding, Mapping):
            raise ValueError("role bindings are malformed")
        role = Role(key)
        declared = Role(raw_binding.get("role"))
        if role is not declared:
            raise ValueError("role binding key does not match its role")
        thread_id = raw_binding.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("role thread_id must be a non-empty string")
        raw_compaction_ids = raw_binding.get("compaction_event_ids", [])
        if not isinstance(raw_compaction_ids, list) or any(
            not isinstance(item, str) or not item for item in raw_compaction_ids
        ):
            raise ValueError("role compaction_event_ids are malformed")
        roles[role] = RoleThread(
            role=role,
            thread_id=thread_id,
            model=_optional_text(raw_binding.get("model"), "model"),
            effort=_optional_text(raw_binding.get("effort"), "effort"),
            service_tier=_optional_text(
                raw_binding.get("service_tier"), "service_tier"
            ),
            compaction_count=_non_negative_int(
                raw_binding.get("compaction_count", 0), "role compaction_count"
            ),
            compaction_event_ids=tuple(raw_compaction_ids),
        )

    raw_requirements = value.get("requirements", [])
    if not isinstance(raw_requirements, list):
        raise ValueError("requirements must be a list")
    requirements: list[Requirement] = []
    for raw in raw_requirements:
        if not isinstance(raw, Mapping):
            raise ValueError("requirement is malformed")
        requirement_id = raw.get("requirement_id")
        kind = raw.get("kind")
        if not isinstance(requirement_id, str) or not requirement_id:
            raise ValueError("requirement_id must be a non-empty string")
        if not isinstance(kind, str) or not kind:
            raise ValueError("requirement kind must be a non-empty string")
        requirements.append(
            Requirement(
                requirement_id=requirement_id,
                kind=kind,
                status=RequirementStatus(raw.get("status")),
                source_ref=str(raw.get("source_ref") or ""),
                superseded_by=_optional_text(
                    raw.get("superseded_by"), "superseded_by"
                ),
            )
        )

    raw_guidance = value.get("guidance", [])
    raw_consumed = value.get("guidance_consumed", {})
    if not isinstance(raw_guidance, list) or not isinstance(raw_consumed, Mapping):
        raise ValueError("guidance state is malformed")
    guidance: list[GuidanceRecord] = []
    for raw in raw_guidance:
        if not isinstance(raw, Mapping):
            raise ValueError("guidance record is malformed")
        target = raw.get("target_role")
        raw_delta = raw.get("requirement_delta")
        if raw_delta is not None and not isinstance(raw_delta, Mapping):
            raise ValueError("guidance requirement_delta is malformed")
        guidance.append(
            GuidanceRecord(
                guidance_id=str(raw.get("guidance_id") or ""),
                revision=_non_negative_int(raw.get("revision"), "guidance revision"),
                text=str(raw.get("text") or ""),
                scope=GuidanceScope(raw.get("scope")),
                target_role=Role(target) if target is not None else None,
                requirement_delta=raw_delta,
                force_replan=_boolean(
                    raw.get("force_replan", False), "guidance force_replan"
                ),
            )
        )
    consumed: dict[Role, int] = {}
    for key, raw_revision in raw_consumed.items():
        consumed[Role(key)] = _non_negative_int(raw_revision, "consumed revision")

    raw_stage = value.get("stage", {})
    raw_turns = value.get("turns", {})
    raw_budget = value.get("budget", {})
    if not all(isinstance(item, Mapping) for item in (raw_stage, raw_turns, raw_budget)):
        raise ValueError("stage, turns, and budget must be objects")
    raw_evidence = raw_stage.get("latest_evidence_ids", [])
    if not isinstance(raw_evidence, (list, tuple)) or not all(
        isinstance(item, str) and item for item in raw_evidence
    ):
        raise ValueError("latest_evidence_ids must contain non-empty strings")
    turns: dict[Role, TurnState] = {}
    for key, raw_turn in raw_turns.items():
        if not isinstance(raw_turn, Mapping):
            raise ValueError("turn entry is malformed")
        role = Role(key)
        raw_turn_role = raw_turn.get("role")
        if raw_turn_role is not None and Role(raw_turn_role) is not role:
            raise ValueError("turn key does not match role")
        turns[role] = TurnState(
            role=role,
            thread_id=_optional_text(raw_turn.get("thread_id"), "thread_id"),
            turn_id=_optional_text(raw_turn.get("turn_id"), "turn_id"),
            active=bool(raw_turn.get("active", False)),
            consumed_turns=_non_negative_int(
                raw_turn.get("consumed_turns", 0), "consumed_turns"
            ),
        )
    resume = value.get("resume_status")
    pause_target = value.get("pause_target")
    raw_effects = value.get("pending_effects", [])
    raw_recovery_effects = value.get("recovery_pending_effects", [])
    if not isinstance(raw_effects, list) or not isinstance(raw_recovery_effects, list):
        raise ValueError("pending effect collections must be lists")

    def parse_effects(raw_values: list[Any]) -> tuple[PendingEffect, ...]:
        effects: list[PendingEffect] = []
        for raw_effect in raw_values:
            if not isinstance(raw_effect, Mapping):
                raise ValueError("pending effect is malformed")
            raw_data = raw_effect.get("data", {})
            if not isinstance(raw_data, Mapping):
                raise ValueError("pending effect data must be an object")
            effects.append(
                PendingEffect(
                    effect_id=str(raw_effect.get("effect_id") or ""),
                    kind=str(raw_effect.get("kind") or ""),
                    data=raw_data,
                )
            )
        return tuple(effects)

    effects = parse_effects(raw_effects)
    recovery_effects = parse_effects(raw_recovery_effects)
    recovery_return = value.get("recovery_return_status")
    return ProjectState(
        project_id=required_text("project_id"),
        goal_thread_id=required_text("goal_thread_id"),
        authoritative_task_id=required_text("authoritative_task_id"),
        authoritative_task_sha256=required_text("authoritative_task_sha256"),
        status=ProjectStatus(value.get("status")),
        roles=roles,
        requirements=tuple(requirements),
        guidance=tuple(guidance),
        guidance_consumed=consumed,
        stage=StageState(
            stage_id=_optional_text(raw_stage.get("stage_id"), "stage_id"),
            ordinal=_non_negative_int(raw_stage.get("ordinal", 0), "ordinal"),
            repair_count=_non_negative_int(
                raw_stage.get("repair_count", 0), "repair_count"
            ),
            schema_correction_count=_non_negative_int(
                raw_stage.get("schema_correction_count", 0),
                "schema_correction_count",
            ),
            no_progress_rounds=_non_negative_int(
                raw_stage.get("no_progress_rounds", 0), "no_progress_rounds"
            ),
            latest_evidence_ids=tuple(raw_evidence),
            latest_defect_hash=_optional_text(
                raw_stage.get("latest_defect_hash"), "latest_defect_hash"
            ),
            latest_repair_hash=_optional_text(
                raw_stage.get("latest_repair_hash"), "latest_repair_hash"
            ),
            latest_screenshot_hash=_optional_text(
                raw_stage.get("latest_screenshot_hash"), "latest_screenshot_hash"
            ),
            latest_coverage_hash=_optional_text(
                raw_stage.get("latest_coverage_hash"), "latest_coverage_hash"
            ),
        ),
        turns=turns,
        budget=RuntimeBudget(**dict(raw_budget)),
        elapsed_seconds=_non_negative_int(
            value.get("elapsed_seconds", 0), "elapsed_seconds"
        ),
        total_evidence_bytes=_non_negative_int(
            value.get("total_evidence_bytes", 0), "total_evidence_bytes"
        ),
        resume_status=ProjectStatus(resume) if resume is not None else None,
        pause_target=(
            ProjectStatus(pause_target) if pause_target is not None else None
        ),
        attention_reason=_optional_text(
            value.get("attention_reason"), "attention_reason"
        ),
        last_error=_optional_text(value.get("last_error"), "last_error"),
        pending_effects=effects,
        recovery_required=_boolean(
            value.get("recovery_required", False), "recovery_required"
        ),
        recovery_return_status=(
            ProjectStatus(recovery_return) if recovery_return is not None else None
        ),
        recovery_pending_effects=recovery_effects,
        plan_stale=_boolean(value.get("plan_stale", False), "plan_stale"),
        blueprint_revision=_non_negative_int(
            value.get("blueprint_revision", 0), "blueprint revision"
        ),
        authorized_blueprint_revision=_non_negative_int(
            value.get("authorized_blueprint_revision", 0),
            "authorized blueprint revision",
        ),
        revision=_non_negative_int(value.get("revision", 0), "revision"),
    )


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value
