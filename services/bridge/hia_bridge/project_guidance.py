"""Requirement deltas and monotonic guidance revision.

Guidance prose belongs to native Codex Thread history.  The registry records
only the revision number needed to show that newer guidance exists; it never
copies message bodies into a second local transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from .project_contracts import ProjectState, Requirement, RequirementStatus, Role


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
) -> ProjectState:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("guidance text must be non-empty")
    if target_role is not None and not isinstance(target_role, Role):
        raise ValueError("target_role must be a project role")
    delta = requirement_delta or RequirementDelta()
    return replace(
        state,
        guidance_revision=state.guidance_revision + 1,
        requirements=_apply_requirement_delta(state.requirements, delta),
        revision=state.revision + 1,
    )


def validate_requirement_coverage(
    requirements: tuple[Requirement, ...] | list[Requirement],
    covered_requirement_ids: tuple[str, ...] | list[str],
) -> None:
    active = {
        item.requirement_id
        for item in requirements
        if item.status in {RequirementStatus.ACTIVE, RequirementStatus.PLANNED}
    }
    covered = set(covered_requirement_ids)
    missing = sorted(active - covered)
    unknown = sorted(covered - active)
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
