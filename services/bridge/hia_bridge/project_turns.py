"""Per-role native Turn ownership and pre-ACK event reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Mapping

from .project_contracts import ProjectState, Role, TurnState


@dataclass(frozen=True)
class PendingTurnRequest:
    request_id: str
    role: Role
    thread_id: str


class TurnOwnershipLedger:
    def __init__(self, *, max_pre_ack_events: int = 128, max_pre_ack_bytes: int = 2_097_152):
        self._pending: dict[Role, PendingTurnRequest] = {}
        self._pre_ack: dict[str, list[Mapping[str, Any]]] = {}
        self._pre_ack_bytes = 0
        self._max_pre_ack_events = max_pre_ack_events
        self._max_pre_ack_bytes = max_pre_ack_bytes

    def begin(self, state: ProjectState, role: Role, request_id: str) -> dict[str, Any]:
        if role not in state.roles:
            raise ValueError(f"project has no {role.value} Thread")
        if role in self._pending or state.turns.get(role, TurnState()).active:
            raise ValueError(f"{role.value} already has an active Turn")
        if not request_id:
            raise ValueError("request_id is required")
        binding = state.roles[role]
        self._pending[role] = PendingTurnRequest(request_id, role, binding.thread_id)
        params: dict[str, Any] = {"threadId": binding.thread_id}
        if binding.model is not None:
            params["model"] = binding.model
        if binding.effort is not None:
            params["effort"] = binding.effort
        if binding.service_tier is not None:
            params["serviceTier"] = binding.service_tier
        return params

    def observe_pre_ack(self, event: Mapping[str, Any]) -> bool:
        """Buffer only events for a Thread whose turn/start request is pending."""

        thread_id, turn_id = _event_identity(event)
        if thread_id is None or turn_id is None:
            return False
        if thread_id not in {item.thread_id for item in self._pending.values()}:
            return False
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        count = sum(len(items) for items in self._pre_ack.values())
        if count >= self._max_pre_ack_events or self._pre_ack_bytes + len(encoded) > self._max_pre_ack_bytes:
            raise ValueError("pre-ACK Turn event budget exceeded")
        self._pre_ack.setdefault(thread_id, []).append(dict(event))
        self._pre_ack_bytes += len(encoded)
        return True

    def acknowledge(
        self,
        state: ProjectState,
        role: Role,
        request_id: str,
        result: Mapping[str, Any],
    ) -> tuple[ProjectState, tuple[Mapping[str, Any], ...]]:
        pending = self._pending.get(role)
        if pending is None or pending.request_id != request_id:
            raise ValueError("turn/start ACK does not match its pending request")
        turn = result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("turn/start ACK has no valid Turn id")
        buffered = self._pre_ack.pop(pending.thread_id, [])
        accepted: list[Mapping[str, Any]] = []
        retained: list[Mapping[str, Any]] = []
        for event in buffered:
            _, event_turn_id = _event_identity(event)
            (accepted if event_turn_id == turn_id else retained).append(event)
        self._pre_ack_bytes -= sum(
            len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for event in accepted
        )
        if retained:
            self._pre_ack[pending.thread_id] = retained
        else:
            self._pre_ack_bytes -= sum(
                len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                for event in retained
            )
        self._pending.pop(role, None)
        turns = dict(state.turns)
        previous = turns.get(role, TurnState(role=role))
        turns[role] = replace(
            previous,
            role=role,
            thread_id=pending.thread_id,
            turn_id=turn_id,
            active=True,
        )
        return replace(state, turns=turns, revision=state.revision + 1), tuple(accepted)

    def fail_request(self, role: Role, request_id: str) -> None:
        pending = self._pending.get(role)
        if pending is None or pending.request_id != request_id:
            return
        events = self._pre_ack.pop(pending.thread_id, [])
        self._pre_ack_bytes -= sum(
            len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for event in events
        )
        self._pending.pop(role, None)

    @staticmethod
    def complete(state: ProjectState, role: Role, event: Mapping[str, Any]) -> ProjectState:
        thread_id, turn_id = _event_identity(event)
        current = state.turns.get(role)
        if current is None or not current.active:
            raise ValueError(f"{role.value} has no active Turn")
        if thread_id != current.thread_id or turn_id != current.turn_id:
            raise ValueError("Turn completion ownership mismatch")
        turns = dict(state.turns)
        turns[role] = replace(current, active=False)
        return replace(state, turns=turns, revision=state.revision + 1)


def _event_identity(event: Mapping[str, Any]) -> tuple[str | None, str | None]:
    params = event.get("params")
    if not isinstance(params, Mapping):
        return None, None
    thread_id = params.get("threadId")
    turn = params.get("turn")
    turn_id = turn.get("id") if isinstance(turn, Mapping) else params.get("turnId")
    return (
        thread_id if isinstance(thread_id, str) and thread_id else None,
        turn_id if isinstance(turn_id, str) and turn_id else None,
    )
