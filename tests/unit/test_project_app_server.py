from __future__ import annotations

import json
import unittest

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.project_app_server import (
    ProjectAppServerError,
    ProjectEffectClient,
    ProjectTurnTimeout,
)


class _Client:
    def __init__(self, events: EventBuffer) -> None:
        self.events = events
        self.is_running = True
        self.before_ack = None
        self.next_turn = "turn-1"
        self.requests: list[tuple[str, dict]] = []

    def request(self, method, params):
        self.requests.append((method, dict(params)))
        if self.before_ack is not None:
            self.before_ack()
        if method == "turn/start":
            return {"turn": {"id": self.next_turn}}
        return {"ok": True}


def _publish(events: EventBuffer, method: str, params: dict) -> None:
    events.publish("codex_notification", method=method, params=params)


def _delta(events: EventBuffer, thread_id: str, turn_id: str, text: str) -> None:
    _publish(
        events,
        "item/agentMessage/delta",
        {"threadId": thread_id, "turnId": turn_id, "delta": text},
    )


def _agent(events: EventBuffer, thread_id: str, turn_id: str, text: str) -> None:
    _publish(
        events,
        "item/completed",
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {"id": "agent", "type": "agentMessage", "text": text},
        },
    )


def _terminal(
    events: EventBuffer, thread_id: str, turn_id: str, status: str = "completed"
) -> None:
    _publish(
        events,
        "turn/completed",
        {"threadId": thread_id, "turn": {"id": turn_id, "status": status}},
    )


def _start(adapter: ProjectEffectClient, client: _Client, thread: str, turn: str) -> None:
    client.next_turn = turn
    result = adapter.request("turn/start", {"threadId": thread, "input": []})
    assert result["turn"]["id"] == turn


class ProjectAppServerTests(unittest.TestCase):
    def test_event_cursor_is_public_and_monotonic(self) -> None:
        events = EventBuffer()
        self.assertEqual(0, events.cursor())
        events.publish("noise")
        self.assertEqual(1, events.cursor())

    def test_captures_agent_events_that_arrive_before_turn_start_ack(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        body = json.dumps({"schema": "example/1", "ok": True})

        def pre_ack() -> None:
            _delta(events, "thread-a", "turn-a", body)
            _terminal(events, "thread-a", "turn-a")

        client.before_ack = pre_ack
        _start(adapter, client, "thread-a", "turn-a")
        completed = adapter.wait_for_turn("thread-a", "turn-a", 0.2)

        self.assertEqual({"schema": "example/1", "ok": True}, completed.payload)
        self.assertEqual("completed", completed.status)

    def test_concurrent_turns_are_isolated_without_consuming_each_other(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-visual", "turn-visual")
        _start(adapter, client, "thread-technical", "turn-technical")
        _agent(events, "thread-technical", "turn-technical", '{"role":"technical"}')
        _terminal(events, "thread-technical", "turn-technical")
        _agent(events, "thread-visual", "turn-visual", '{"role":"visual"}')
        _terminal(events, "thread-visual", "turn-visual")

        visual = adapter.wait_for_turn("thread-visual", "turn-visual", 0.2)
        technical = adapter.wait_for_turn("thread-technical", "turn-technical", 0.2)

        self.assertEqual("visual", visual.payload["role"])
        self.assertEqual("technical", technical.payload["role"])

    def test_keeps_only_current_turn_completed_hia_items(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _publish(
            events,
            "item/completed",
            {
                "threadId": "thread-execution",
                "turnId": "turn-old",
                "item": {
                    "id": "old-tool",
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_validate",
                },
            },
        )
        _start(adapter, client, "thread-execution", "turn-new")
        _publish(
            events,
            "item/completed",
            {
                "threadId": "thread-execution",
                "turnId": "turn-new",
                "item": {
                    "id": "new-tool",
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_capture_viewport",
                },
            },
        )
        _agent(events, "thread-execution", "turn-new", '{"schema":"execution/1"}')
        _terminal(events, "thread-execution", "turn-new")

        completed = adapter.wait_for_turn("thread-execution", "turn-new", 0.2)

        self.assertEqual(1, len(completed.events))
        self.assertEqual(
            "new-tool", completed.events[0]["params"]["item"]["id"]
        )

    def test_event_ring_gap_fails_closed(self) -> None:
        events = EventBuffer(max_events=2)
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        events.publish("noise", value=1)
        events.publish("noise", value=2)
        events.publish("noise", value=3)

        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("PROJECT_EVENT_GAP", raised.exception.code)

    def test_timeout_is_bounded_and_removes_turn_registration(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events, poll_interval_seconds=0.002)
        _start(adapter, client, "thread-a", "turn-a")

        with self.assertRaises(ProjectTurnTimeout):
            adapter.wait_for_turn("thread-a", "turn-a", 0.01)
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.01)
        self.assertEqual("UNACKNOWLEDGED_TURN", raised.exception.code)

    def test_rejects_invalid_or_non_object_agent_json(self) -> None:
        for body, code in (("not json", "INVALID_AGENT_JSON"), ("[]", "INVALID_AGENT_PAYLOAD")):
            with self.subTest(body=body):
                events = EventBuffer()
                client = _Client(events)
                adapter = ProjectEffectClient(client, events)
                _start(adapter, client, "thread-a", "turn-a")
                _agent(events, "thread-a", "turn-a", body)
                _terminal(events, "thread-a", "turn-a")
                with self.assertRaises(ProjectAppServerError) as raised:
                    adapter.wait_for_turn("thread-a", "turn-a", 0.2)
                self.assertEqual(code, raised.exception.code)

    def test_rejects_conflicting_or_oversized_agent_messages(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        _agent(events, "thread-a", "turn-a", '{"value":1}')
        _agent(events, "thread-a", "turn-a", '{"value":2}')
        _terminal(events, "thread-a", "turn-a")
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("CONFLICTING_AGENT_MESSAGES", raised.exception.code)

        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events, max_agent_message_bytes=8)
        _start(adapter, client, "thread-b", "turn-b")
        _delta(events, "thread-b", "turn-b", '{"long":')
        _delta(events, "thread-b", "turn-b", '"value"}')
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-b", "turn-b", 0.2)
        self.assertEqual("AGENT_MESSAGE_TOO_LARGE", raised.exception.code)

    def test_process_exit_and_failed_terminal_are_explicit_failures(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        events.publish("process_exit", returncode=7)
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("CODEX_PROCESS_EXITED", raised.exception.code)

        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-b", "turn-b")
        _terminal(events, "thread-b", "turn-b", "failed")
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-b", "turn-b", 0.2)
        self.assertEqual("PROJECT_TURN_NOT_COMPLETED", raised.exception.code)

    def test_counts_only_unique_current_turn_native_subagents(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectEffectClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        collab = {
            "id": "collab-call",
            "type": "collabAgentToolCall",
            "receiverThreadIds": ["child-1", "child-2"],
            "agentsStates": {"child-1": {"status": "running"}},
        }
        for method in ("item/started", "item/completed"):
            _publish(
                events,
                method,
                {"threadId": "thread-a", "turnId": "turn-a", "item": collab},
            )
        _publish(
            events,
            "item/completed",
            {
                "threadId": "thread-a",
                "turnId": "turn-a",
                "item": {
                    "id": "activity-1",
                    "type": "subAgentActivity",
                    "agentThreadId": "child-1",
                },
            },
        )
        _publish(
            events,
            "item/completed",
            {
                "threadId": "other",
                "turnId": "other-turn",
                "item": {
                    "id": "activity-other",
                    "type": "subAgentActivity",
                    "agentThreadId": "child-other",
                },
            },
        )
        _agent(events, "thread-a", "turn-a", '{"schema":"review/1"}')
        _terminal(events, "thread-a", "turn-a")

        completed = adapter.wait_for_turn("thread-a", "turn-a", 0.2)

        self.assertEqual(2, completed.native_subagents)


if __name__ == "__main__":
    unittest.main()
