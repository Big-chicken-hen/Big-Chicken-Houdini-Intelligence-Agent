"""Deterministic runtime budgets and no-progress detection."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Iterable

from .project_contracts import ProjectState, Role, StageState, TurnState


@dataclass(frozen=True)
class ProgressObservation:
    role: Role
    kind: str
    elapsed_seconds: int = 0
    evidence_bytes: int = 0
    evidence_ids: tuple[str, ...] = ()
    defect_hash: str | None = None
    repair_hash: str | None = None
    screenshot_hash: str | None = None
    claims_visual_change: bool = False
    coverage_ids: tuple[str, ...] = ()
    native_subagents: int = 0


@dataclass(frozen=True)
class BudgetResult:
    state: ProjectState
    violation: str | None = None


def record_progress(state: ProjectState, observation: ProgressObservation) -> BudgetResult:
    """Record one completed role Turn and report, never auto-pass, a limit breach."""

    for name, value in (
        ("elapsed_seconds", observation.elapsed_seconds),
        ("evidence_bytes", observation.evidence_bytes),
        ("native_subagents", observation.native_subagents),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    turns = dict(state.turns)
    previous_turn = turns.get(observation.role, TurnState(role=observation.role))
    turns[observation.role] = replace(
        previous_turn,
        active=False,
        consumed_turns=previous_turn.consumed_turns + 1,
    )
    elapsed = state.elapsed_seconds + observation.elapsed_seconds
    evidence_bytes = state.total_evidence_bytes + observation.evidence_bytes
    stage = state.stage
    new_ids = tuple(dict.fromkeys(observation.evidence_ids))
    prior_ids = set(stage.latest_evidence_ids)
    repeated = False
    reasons: list[str] = []

    if observation.kind == "repair":
        stage = replace(stage, repair_count=stage.repair_count + 1)
        if not set(new_ids) - prior_ids:
            repeated = True
            reasons.append("repair_added_no_new_evidence")
        if observation.repair_hash and observation.repair_hash == stage.latest_repair_hash:
            repeated = True
            reasons.append("repair_card_repeated")
    if observation.kind == "schema_correction":
        stage = replace(
            stage, schema_correction_count=stage.schema_correction_count + 1
        )
    if observation.defect_hash and observation.defect_hash == stage.latest_defect_hash:
        repeated = True
        reasons.append("defect_repeated")
    if (
        observation.claims_visual_change
        and observation.screenshot_hash
        and observation.screenshot_hash == stage.latest_screenshot_hash
    ):
        repeated = True
        reasons.append("visual_change_has_identical_screenshot")
    coverage_hash = _coverage_hash(observation.coverage_ids)
    if (
        observation.kind == "plan_revision"
        and coverage_hash
        and coverage_hash == stage.latest_coverage_hash
    ):
        repeated = True
        reasons.append("plan_revision_changed_no_requirement_coverage")

    stage = replace(
        stage,
        no_progress_rounds=stage.no_progress_rounds + 1 if repeated else 0,
        latest_evidence_ids=tuple(sorted(prior_ids | set(new_ids))),
        latest_defect_hash=observation.defect_hash or stage.latest_defect_hash,
        latest_repair_hash=observation.repair_hash or stage.latest_repair_hash,
        latest_screenshot_hash=(
            observation.screenshot_hash or stage.latest_screenshot_hash
        ),
        latest_coverage_hash=coverage_hash or stage.latest_coverage_hash,
    )
    updated = replace(
        state,
        turns=turns,
        stage=stage,
        elapsed_seconds=elapsed,
        total_evidence_bytes=evidence_bytes,
        revision=state.revision + 1,
    )
    total_turns = sum(item.consumed_turns for item in turns.values())
    budget = state.budget
    if total_turns > budget.max_project_turns:
        return BudgetResult(updated, "max_project_turns")
    if stage.repair_count > budget.max_stage_repairs:
        return BudgetResult(updated, "max_stage_repairs")
    if stage.schema_correction_count > budget.max_schema_corrections:
        return BudgetResult(updated, "max_schema_corrections")
    if elapsed > budget.max_elapsed_seconds:
        return BudgetResult(updated, "max_elapsed_seconds")
    if evidence_bytes > budget.max_total_evidence_bytes:
        return BudgetResult(updated, "max_total_evidence_bytes")
    if observation.native_subagents > budget.max_native_subagents_per_turn:
        return BudgetResult(updated, "max_native_subagents_per_turn")
    if stage.no_progress_rounds >= budget.max_no_progress_rounds:
        return BudgetResult(updated, ",".join(reasons) or "max_no_progress_rounds")
    return BudgetResult(updated)


def _coverage_hash(values: Iterable[str]) -> str | None:
    normalized = sorted(set(values))
    if not normalized:
        return None
    return hashlib.sha256("\0".join(normalized).encode("utf-8")).hexdigest()
