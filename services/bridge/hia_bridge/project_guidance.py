"""Versioned guidance routing and requirement supersession.

The Bridge records explicit deltas.  It never tries to infer whether user prose
is a better plan, similar claim, or sufficiently detailed blueprint.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Iterable, Mapping

from .project_contracts import (
    GuidanceRecord,
    GuidanceScope,
    ProjectState,
    Requirement,
    RequirementStatus,
    Role,
)


@dataclass(frozen=True)
class RequirementDelta:
    add: tuple[Requirement, ...] = ()
    supersede: Mapping[str, str] | None = None
    remove: tuple[str, ...] = ()

    @property
    def is_material(self) -> bool:
        return bool(self.add or self.supersede or self.remove)


def publish_guidance(
    state: ProjectState,
    text: str,
    *,
    target_role: Role | None = None,
    requirement_delta: RequirementDelta | None = None,
    force_replan: bool = False,
) -> ProjectState:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("guidance text must be non-empty")
    if not isinstance(force_replan, bool):
        raise ValueError("force_replan must be boolean")
    revision = max((item.revision for item in state.guidance), default=0) + 1
    digest = hashlib.sha256(
        f"{state.project_id}\0{revision}\0{text}".encode("utf-8")
    ).hexdigest()
    delta = requirement_delta or RequirementDelta()
    record = GuidanceRecord(
        guidance_id=f"guidance-{digest[:24]}",
        revision=revision,
        text=text,
        scope=GuidanceScope.ROLE if target_role is not None else GuidanceScope.PROJECT,
        target_role=target_role,
        requirement_delta=_delta_payload(delta) if delta.is_material else None,
        force_replan=force_replan,
    )
    requirements = _apply_requirement_delta(
        state.requirements, delta
    )
    return replace(
        state,
        guidance=(*state.guidance, record),
        requirements=requirements,
        plan_stale=state.plan_stale
        or ((delta.is_material or force_replan) and state.blueprint_revision > 0),
        revision=state.revision + 1,
    )


def pending_guidance(state: ProjectState, role: Role) -> tuple[GuidanceRecord, ...]:
    consumed = state.guidance_consumed.get(role, 0)
    return tuple(
        item
        for item in state.guidance
        if item.revision > consumed
        and (item.scope is GuidanceScope.PROJECT or item.target_role is role)
    )


def mark_guidance_consumed(
    state: ProjectState, role: Role, guidance_ids: Iterable[str]
) -> ProjectState:
    requested = set(guidance_ids)
    applicable = pending_guidance(state, role)
    expected = {item.guidance_id for item in applicable}
    if requested != expected:
        missing = sorted(expected - requested)
        unknown = sorted(requested - expected)
        raise ValueError(
            f"role must consume every applicable guidance revision; "
            f"missing={missing}, unknown={unknown}"
        )
    consumed = dict(state.guidance_consumed)
    if applicable:
        consumed[role] = max(item.revision for item in applicable)
    return replace(state, guidance_consumed=consumed, revision=state.revision + 1)


def validate_requirement_coverage(
    requirements: Iterable[Requirement], covered_requirement_ids: Iterable[str]
) -> None:
    """Check stable IDs only; blueprint length and prose style are irrelevant."""

    active = {
        item.requirement_id
        for item in requirements
        if item.status in {RequirementStatus.ACTIVE, RequirementStatus.PLANNED}
    }
    covered = set(covered_requirement_ids)
    missing = sorted(active - covered)
    unknown = sorted(covered - {item.requirement_id for item in requirements})
    if missing or unknown:
        raise ValueError(
            f"requirement coverage mismatch: missing={missing}, unknown={unknown}"
        )


def _apply_requirement_delta(
    current: tuple[Requirement, ...], delta: RequirementDelta
) -> tuple[Requirement, ...]:
    records = {item.requirement_id: item for item in current}
    if len(records) != len(current):
        raise ValueError("requirements contain duplicate IDs")
    for item in delta.add:
        if item.requirement_id in records:
            raise ValueError(f"requirement already exists: {item.requirement_id}")
        records[item.requirement_id] = item
    for old_id, replacement_id in dict(delta.supersede or {}).items():
        if old_id not in records or replacement_id not in records:
            raise ValueError("supersession must reference existing requirement IDs")
        records[old_id] = replace(
            records[old_id],
            status=RequirementStatus.SUPERSEDED_BY_USER,
            superseded_by=replacement_id,
        )
    for requirement_id in delta.remove:
        if requirement_id not in records:
            raise ValueError(f"removed requirement does not exist: {requirement_id}")
        records[requirement_id] = replace(
            records[requirement_id],
            status=RequirementStatus.REMOVED_BY_USER,
            superseded_by=None,
        )
    return tuple(records[key] for key in sorted(records))


def _delta_payload(delta: RequirementDelta) -> dict[str, object]:
    return {
        "add": [
            {
                "requirement_id": item.requirement_id,
                "kind": item.kind,
                "status": item.status.value,
                "source_ref": item.source_ref,
                "superseded_by": item.superseded_by,
            }
            for item in delta.add
        ],
        "supersede": dict(delta.supersede or {}),
        "remove": list(delta.remove),
    }
