"""Simple finite runtime counters for project Turns and evidence bytes."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .project_contracts import ProjectState, Role, TurnState


@dataclass(frozen=True)
class ProgressObservation:
    role: Role
    kind: str
    elapsed_seconds: int = 0
    evidence_bytes: int = 0
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetResult:
    state: ProjectState
    violation: str | None = None


def record_progress(state: ProjectState, observation: ProgressObservation) -> BudgetResult:
    for name, value in (
        ("elapsed_seconds", observation.elapsed_seconds),
        ("evidence_bytes", observation.evidence_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    turns = dict(state.turns)
    previous = turns.get(observation.role, TurnState(role=observation.role))
    turns[observation.role] = replace(
        previous, active=False, consumed_turns=previous.consumed_turns + 1
    )
    stage = state.stage
    if observation.kind == "repair":
        stage = replace(stage, repair_count=stage.repair_count + 1)
    if observation.kind == "schema_correction":
        stage = replace(
            stage, schema_correction_count=stage.schema_correction_count + 1
        )
    stage = replace(
        stage,
        latest_evidence_ids=tuple(
            sorted(set(stage.latest_evidence_ids) | set(observation.evidence_ids))
        ),
    )
    updated = replace(
        state,
        turns=turns,
        stage=stage,
        elapsed_seconds=state.elapsed_seconds + observation.elapsed_seconds,
        total_evidence_bytes=state.total_evidence_bytes + observation.evidence_bytes,
        revision=state.revision + 1,
    )
    budget = state.budget
    if sum(item.consumed_turns for item in turns.values()) > budget.max_project_turns:
        return BudgetResult(updated, "max_project_turns")
    if stage.repair_count > budget.max_stage_repairs:
        return BudgetResult(updated, "max_stage_repairs")
    if updated.elapsed_seconds > budget.max_elapsed_seconds:
        return BudgetResult(updated, "max_elapsed_seconds")
    if updated.total_evidence_bytes > budget.max_total_evidence_bytes:
        return BudgetResult(updated, "max_total_evidence_bytes")
    return BudgetResult(updated)
