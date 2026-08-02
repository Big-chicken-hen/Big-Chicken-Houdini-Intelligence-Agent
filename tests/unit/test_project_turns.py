from __future__ import annotations

import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_turns import TurnOwnershipLedger


def _state() -> ProjectState:
    task_id, digest = authoritative_task_identity("scene task")
    roles = {
        role: RoleThread(role, f"thread-{role.value}")
        for role in Role
    }
    return ProjectState(
        project_id="p1",
        goal_thread_id="thread-supervisor",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        roles=roles,
    )


def _event(thread: str, turn: str, method: str = "turn/started") -> dict:
    return {
        "method": method,
        "params": {"threadId": thread, "turn": {"id": turn}},
    }


class ProjectTurnTests(unittest.TestCase):
    def test_pre_ack_event_is_reconciled_to_exact_turn(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger()
        params = ledger.begin(state, Role.EXECUTION, "request-1")
        self.assertEqual("thread-execution", params["threadId"])
        self.assertTrue(ledger.observe_pre_ack(_event("thread-execution", "turn-1")))
        state, events = ledger.acknowledge(
            state, Role.EXECUTION, "request-1", {"turn": {"id": "turn-1"}}
        )
        self.assertEqual(1, len(events))
        self.assertTrue(state.turns[Role.EXECUTION].active)

    def test_wrong_thread_is_not_claimed_and_wrong_turn_is_not_replayed(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger()
        ledger.begin(state, Role.EXECUTION, "request-1")
        self.assertFalse(ledger.observe_pre_ack(_event("other", "turn-1")))
        ledger.observe_pre_ack(_event("thread-execution", "stale-turn"))
        state, events = ledger.acknowledge(
            state, Role.EXECUTION, "request-1", {"turn": {"id": "turn-current"}}
        )
        self.assertEqual((), events)

    def test_two_review_turns_can_be_active_in_parallel(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger()
        for role in (Role.VISUAL_REVIEW, Role.TECHNICAL_REVIEW):
            request_id = f"request-{role.value}"
            ledger.begin(state, role, request_id)
            state, _ = ledger.acknowledge(
                state, role, request_id, {"turn": {"id": f"turn-{role.value}"}}
            )
        self.assertTrue(state.turns[Role.VISUAL_REVIEW].active)
        self.assertTrue(state.turns[Role.TECHNICAL_REVIEW].active)

    def test_same_role_cannot_start_twice(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger()
        ledger.begin(state, Role.EXECUTION, "request-1")
        with self.assertRaisesRegex(ValueError, "already"):
            ledger.begin(state, Role.EXECUTION, "request-2")

    def test_completion_requires_exact_thread_and_turn(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger()
        ledger.begin(state, Role.EXECUTION, "request-1")
        state, _ = ledger.acknowledge(
            state, Role.EXECUTION, "request-1", {"turn": {"id": "turn-1"}}
        )
        with self.assertRaisesRegex(ValueError, "ownership"):
            ledger.complete(state, Role.EXECUTION, _event("thread-execution", "wrong"))
        state = ledger.complete(
            state, Role.EXECUTION, _event("thread-execution", "turn-1", "turn/completed")
        )
        self.assertFalse(state.turns[Role.EXECUTION].active)

    def test_pre_ack_budget_fails_closed(self) -> None:
        state = _state()
        ledger = TurnOwnershipLedger(max_pre_ack_events=1)
        ledger.begin(state, Role.EXECUTION, "request-1")
        ledger.observe_pre_ack(_event("thread-execution", "turn-1"))
        with self.assertRaisesRegex(ValueError, "budget"):
            ledger.observe_pre_ack(_event("thread-execution", "turn-1", "item/started"))


if __name__ == "__main__":
    unittest.main()
