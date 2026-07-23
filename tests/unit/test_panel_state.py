from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_panel.turn_state import PanelTurnState, TurnPhase  # noqa: E402


class PanelTurnStateTests(unittest.TestCase):
    def test_start_ack_keeps_start_controls_locked_and_enables_valid_stop(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        starting = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id="thread-1",
        )
        self.assertEqual(TurnPhase.STARTING, state.phase)
        self.assertFalse(starting.new_thread)
        self.assertFalse(starting.resume_thread)
        self.assertFalse(starting.send)
        self.assertFalse(starting.stop)

        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        active = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id="thread-1",
        )
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)
        self.assertFalse(active.new_thread)
        self.assertFalse(active.resume_thread)
        self.assertFalse(active.send)
        self.assertTrue(active.stop)

    def test_four_consecutive_turns_unlock_only_after_matching_completion(self) -> None:
        state = PanelTurnState()
        for index in range(1, 5):
            turn_id = f"turn-{index}"
            self.assertTrue(state.begin_start("thread-1"))
            self.assertTrue(
                state.acknowledge_start(
                    state.capture_token(), "thread-1", turn_id
                )
            )
            active = state.derive_controls(
                connected=True,
                authenticated=True,
                selected_thread_id="thread-1",
            )
            self.assertFalse(active.new_thread)
            self.assertFalse(active.resume_thread)
            self.assertFalse(active.send)
            self.assertTrue(active.stop)

            self.assertTrue(state.observe_completed("thread-1", turn_id))
            idle = state.derive_controls(
                connected=True,
                authenticated=True,
                selected_thread_id="thread-1",
            )
            self.assertEqual(TurnPhase.IDLE, state.phase)
            self.assertTrue(idle.new_thread)
            self.assertTrue(idle.resume_thread)
            self.assertTrue(idle.send)
            self.assertFalse(idle.stop)

    def test_unfinished_turn_rejects_another_start(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        self.assertFalse(state.begin_start("thread-1"))
        self.assertEqual("turn-1", state.turn_id)
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)

    def test_uncertain_start_requires_explicit_no_active_confirmation(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(state.mark_start_uncertain("thread-1"))
        self.assertEqual(TurnPhase.RECONCILING, state.phase)
        self.assertFalse(
            state.confirm_start_not_created("thread-1", no_active_turn=False)
        )
        self.assertEqual(TurnPhase.RECONCILING, state.phase)
        self.assertTrue(
            state.confirm_start_not_created("thread-1", no_active_turn=True)
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)

    def test_late_completion_cannot_unlock_a_new_turn(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))

        self.assertTrue(state.begin_start("thread-1"))
        self.assertFalse(state.observe_completed("thread-1", "turn-1"))
        self.assertEqual(TurnPhase.STARTING, state.phase)
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-2"
            )
        )
        self.assertFalse(state.observe_completed("thread-1", "turn-1"))
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)
        self.assertEqual("turn-2", state.turn_id)

    def test_completion_before_start_ack_unlocks_without_ack_regression(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        start_token = state.capture_token()
        self.assertTrue(state.observe_started("thread-1", "turn-fast"))
        self.assertTrue(state.observe_completed("thread-1", "turn-fast"))
        self.assertEqual(TurnPhase.IDLE, state.phase)
        self.assertFalse(
            state.acknowledge_start(start_token, "thread-1", "turn-fast")
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)

    def test_stale_snapshot_cannot_attach_old_turn_to_new_start(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        stale_token = state.capture_token()
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))
        self.assertTrue(state.begin_start("thread-1"))

        self.assertFalse(
            state.reconcile_snapshot(
                stale_token,
                "thread-1",
                "turn-1",
                "completed",
                turn_active=False,
            )
        )
        self.assertEqual(TurnPhase.STARTING, state.phase)
        self.assertIsNone(state.turn_id)

    def test_active_snapshot_captured_before_completion_cannot_relock_idle(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        stale_active_token = state.capture_token()
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))

        self.assertFalse(
            state.reconcile_snapshot(
                stale_active_token,
                "thread-1",
                "turn-1",
                "inProgress",
                turn_active=True,
            )
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)
        controls = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id="thread-1",
        )
        self.assertTrue(controls.send)
        self.assertFalse(controls.stop)

    def test_current_token_rejects_previous_turn_terminal_snapshot(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))

        self.assertTrue(state.begin_start("thread-1"))
        current_start_token = state.capture_token()
        self.assertFalse(
            state.reconcile_snapshot(
                current_start_token,
                "thread-1",
                "turn-1",
                "completed",
                turn_active=False,
            )
        )
        self.assertEqual(TurnPhase.STARTING, state.phase)
        self.assertIsNone(state.turn_id)

    def test_uncertain_start_reconciles_from_authoritative_snapshot(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(state.mark_start_uncertain("thread-1"))
        token = state.claim_reconciliation()
        self.assertIsNotNone(token)
        self.assertTrue(
            state.reconcile_snapshot(
                token,
                "thread-1",
                "turn-1",
                "inProgress",
                turn_active=True,
            )
        )
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)
        self.assertEqual("turn-1", state.turn_id)

    def test_no_active_turn_error_applies_authoritative_terminal_state(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        token = state.capture_token()

        self.assertTrue(
            state.reconcile_no_active_error(
                token,
                {
                    "turn_active": False,
                    "thread_id": "thread-1",
                    "turn_id": "turn-1",
                    "turn_status": "completed",
                },
            )
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)
        controls = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id="thread-1",
        )
        self.assertTrue(controls.send)
        self.assertFalse(controls.stop)

    def test_missed_completion_reconciles_from_matching_session_snapshot(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        token = state.claim_reconciliation()
        self.assertIsNotNone(token)

        self.assertTrue(
            state.reconcile_snapshot(
                token,
                "thread-1",
                "turn-1",
                "completed",
                turn_active=False,
            )
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)
        self.assertIsNone(state.turn_id)

    def test_reconciliation_is_bounded_once_per_reason_and_generation(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        first_generation = state.claim_reconciliation()
        self.assertIsNotNone(first_generation)
        self.assertIsNone(state.claim_reconciliation())
        self.assertIsNotNone(state.claim_reconciliation("event_gap"))
        self.assertIsNone(state.claim_reconciliation("event_gap"))
        self.assertIsNotNone(
            state.claim_reconciliation("unmatched_turn_completed")
        )
        self.assertIsNone(
            state.claim_reconciliation("unmatched_turn_completed")
        )

        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-1"
            )
        )
        self.assertIsNone(state.claim_reconciliation())
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))
        self.assertIsNone(state.claim_reconciliation())

        self.assertTrue(state.begin_start("thread-1"))
        second_generation = state.claim_reconciliation()
        self.assertIsNotNone(second_generation)
        self.assertNotEqual(
            first_generation.generation,
            second_generation.generation,
        )
        self.assertIsNone(state.claim_reconciliation())

    def test_late_ack_old_completion_and_expired_snapshot_are_all_rejected(self) -> None:
        state = PanelTurnState()
        self.assertTrue(state.begin_start("thread-1"))
        expired_ack_token = state.capture_token()
        self.assertTrue(
            state.acknowledge_start(
                expired_ack_token, "thread-1", "turn-1"
            )
        )
        expired_token = state.capture_token()
        self.assertTrue(state.observe_completed("thread-1", "turn-1"))

        self.assertTrue(state.begin_start("thread-1"))
        self.assertFalse(state.token_is_current(expired_ack_token))
        self.assertFalse(
            state.acknowledge_start(expired_ack_token, "thread-1", "turn-1")
        )
        self.assertTrue(
            state.acknowledge_start(
                state.capture_token(), "thread-1", "turn-2"
            )
        )
        self.assertFalse(state.observe_completed("thread-1", "turn-1"))
        self.assertFalse(
            state.reconcile_snapshot(
                expired_token,
                "thread-1",
                "turn-1",
                "completed",
                turn_active=False,
            )
        )
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)
        self.assertEqual("turn-2", state.turn_id)

    def test_stop_is_disabled_without_a_valid_active_thread_and_turn(self) -> None:
        state = PanelTurnState()
        idle = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id=None,
        )
        self.assertFalse(idle.send)
        self.assertFalse(idle.stop)
        self.assertTrue(state.begin_start("thread-1"))
        starting = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id="thread-1",
        )
        self.assertFalse(starting.stop)


if __name__ == "__main__":
    unittest.main()
