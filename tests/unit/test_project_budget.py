from __future__ import annotations

from dataclasses import replace
import unittest

from services.bridge.hia_bridge.project_budget import ProgressObservation, record_progress
from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Role,
    RuntimeBudget,
    StageState,
    authoritative_task_identity,
)


def _state(**budget: int) -> ProjectState:
    task_id, digest = authoritative_task_identity("scene task")
    return ProjectState(
        project_id="p1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        budget=RuntimeBudget(**budget),
    )


class ProjectBudgetTests(unittest.TestCase):
    def test_turn_elapsed_evidence_and_repair_budgets_are_finite(self) -> None:
        cases = (
            (
                "max_project_turns",
                _state(max_project_turns=1),
                ProgressObservation(Role.EXECUTION, "turn"),
                ProgressObservation(Role.EXECUTION, "turn"),
            ),
            (
                "max_stage_repairs",
                _state(max_stage_repairs=1),
                ProgressObservation(Role.EXECUTION, "repair"),
                ProgressObservation(Role.EXECUTION, "repair"),
            ),
            (
                "max_elapsed_seconds",
                _state(max_elapsed_seconds=1),
                ProgressObservation(Role.EXECUTION, "turn", elapsed_seconds=1),
                ProgressObservation(Role.EXECUTION, "turn", elapsed_seconds=1),
            ),
            (
                "max_total_evidence_bytes",
                _state(max_total_evidence_bytes=1),
                ProgressObservation(Role.EXECUTION, "turn", evidence_bytes=1),
                ProgressObservation(Role.EXECUTION, "turn", evidence_bytes=1),
            ),
        )
        for expected, state, first, second in cases:
            with self.subTest(expected=expected):
                first_result = record_progress(state, first)
                self.assertIsNone(first_result.violation)
                second_result = record_progress(first_result.state, second)
                self.assertEqual(expected, second_result.violation)

    def test_schema_corrections_are_counted_but_do_not_auto_pass(self) -> None:
        result = record_progress(
            _state(), ProgressObservation(Role.PLANNING, "schema_correction")
        )
        self.assertEqual(1, result.state.stage.schema_correction_count)
        self.assertIsNone(result.violation)

    def test_evidence_ids_are_deduplicated_without_interpreting_quality(self) -> None:
        initial = replace(_state(), stage=StageState(latest_evidence_ids=("item-a",)))
        result = record_progress(
            initial,
            ProgressObservation(
                Role.EXECUTION,
                "turn",
                evidence_ids=("item-a", "item-b", "item-b"),
            ),
        )
        self.assertEqual(("item-a", "item-b"), result.state.stage.latest_evidence_ids)

    def test_negative_observation_values_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "elapsed_seconds"):
            record_progress(
                _state(), ProgressObservation(Role.EXECUTION, "turn", elapsed_seconds=-1)
            )


if __name__ == "__main__":
    unittest.main()
