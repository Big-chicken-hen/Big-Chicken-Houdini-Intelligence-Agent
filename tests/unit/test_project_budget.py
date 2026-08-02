from __future__ import annotations

from dataclasses import replace
import unittest

from services.bridge.hia_bridge.project_budget import ProgressObservation, record_progress
from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Role,
    RuntimeBudget,
    authoritative_task_identity,
)


def _state(**budget: int) -> ProjectState:
    task_id, digest = authoritative_task_identity("build scene")
    return ProjectState(
        project_id="p1",
        goal_thread_id="t1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        budget=RuntimeBudget(**budget) if budget else RuntimeBudget(),
    )


class ProjectBudgetTests(unittest.TestCase):
    def test_repeated_repairs_without_new_evidence_trigger_attention_signal(self) -> None:
        state = _state(max_no_progress_rounds=2)
        first = ProgressObservation(
            Role.EXECUTION,
            "repair",
            evidence_ids=("e1",),
            repair_hash="same",
            defect_hash="d1",
        )
        state = record_progress(state, first).state
        result = record_progress(state, first)
        self.assertIsNone(result.violation)
        result = record_progress(result.state, first)
        self.assertIn("repair", result.violation)

    def test_identical_screenshot_cannot_prove_visual_change(self) -> None:
        state = _state(max_no_progress_rounds=1)
        state = record_progress(
            state,
            ProgressObservation(
                Role.EXECUTION, "execution", screenshot_hash="image-a"
            ),
        ).state
        result = record_progress(
            state,
            ProgressObservation(
                Role.EXECUTION,
                "repair",
                evidence_ids=("e2",),
                screenshot_hash="image-a",
                claims_visual_change=True,
            ),
        )
        self.assertEqual("visual_change_has_identical_screenshot", result.violation)

    def test_schema_and_repair_budgets_do_not_auto_pass(self) -> None:
        state = _state(max_schema_corrections=1, max_stage_repairs=1)
        for kind, expected in (
            ("schema_correction", "max_schema_corrections"),
            ("repair", "max_stage_repairs"),
        ):
            current = state
            current = record_progress(
                current, ProgressObservation(Role.PLANNING, kind, evidence_ids=("a",))
            ).state
            result = record_progress(
                current, ProgressObservation(Role.PLANNING, kind, evidence_ids=("b",))
            )
            self.assertEqual(expected, result.violation)

    def test_elapsed_turn_evidence_and_subagent_budgets(self) -> None:
        cases = (
            ("max_project_turns", {"max_project_turns": 1}, [{}, {}]),
            ("max_elapsed_seconds", {"max_elapsed_seconds": 1}, [{"elapsed_seconds": 2}]),
            ("max_total_evidence_bytes", {"max_total_evidence_bytes": 1}, [{"evidence_bytes": 2}]),
            ("max_native_subagents_per_turn", {"max_native_subagents_per_turn": 1}, [{"native_subagents": 2}]),
        )
        for expected, budget, observations in cases:
            with self.subTest(expected=expected):
                state = _state(**budget)
                result = None
                for values in observations:
                    result = record_progress(
                        state, ProgressObservation(Role.SUPERVISOR, "turn", **values)
                    )
                    state = result.state
                self.assertEqual(expected, result.violation)

    def test_plan_revision_uses_coverage_not_prose_length(self) -> None:
        state = _state(max_no_progress_rounds=1)
        state = record_progress(
            state,
            ProgressObservation(
                Role.PLANNING, "plan_revision", coverage_ids=("REQ-a", "REQ-b")
            ),
        ).state
        result = record_progress(
            state,
            ProgressObservation(
                Role.PLANNING, "plan_revision", coverage_ids=("REQ-b", "REQ-a")
            ),
        )
        self.assertEqual(
            "plan_revision_changed_no_requirement_coverage", result.violation
        )


if __name__ == "__main__":
    unittest.main()
