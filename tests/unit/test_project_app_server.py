from __future__ import annotations

import json
import threading
import unittest

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.project_app_server import (
    ProjectAppServerError,
    ProjectRoleClient,
    ProjectTurnTimeout,
)
from services.bridge.hia_bridge.scene_writer import SceneWriterOwnership


class _Client:
    def __init__(self, events: EventBuffer) -> None:
        self.events = events
        self.is_running = True
        self.before_ack = None
        self.next_turn = "turn-1"
        self.requests: list[tuple[str, dict]] = []
        self.notification_observers = []

    def add_notification_observer(self, observer) -> None:
        self.notification_observers.append(observer)

    def emit_notification(self, method: str, params: dict) -> None:
        for observer in tuple(self.notification_observers):
            observer(method, params)
        _publish(self.events, method, params)

    def request(self, method, params):
        self.requests.append((method, dict(params)))
        if method == "turn/start" and self.before_ack is not None:
            self.before_ack()
        if method == "turn/start":
            return {"turn": {"id": self.next_turn}}
        return {"ok": True}


class _WaitAwareEventBuffer(EventBuffer):
    def __init__(self) -> None:
        super().__init__()
        self.poll_entered = threading.Event()

    def poll(self, *args, **kwargs):
        self.poll_entered.set()
        return super().poll(*args, **kwargs)


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


def _start(adapter: ProjectRoleClient, client: _Client, thread: str, turn: str) -> None:
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
        adapter = ProjectRoleClient(client, events)
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
        adapter = ProjectRoleClient(client, events)
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

    def test_same_thread_start_fails_fast_without_queueing(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events, poll_interval_seconds=0.002)
        _start(adapter, client, "thread-supervisor", "turn-first")

        with self.assertRaises(ProjectAppServerError) as busy:
            adapter.request("turn/start", {"threadId": "thread-supervisor", "input": []})
        self.assertEqual("PROJECT_THREAD_BUSY", busy.exception.code)
        self.assertIs(False, busy.exception.turn_created)

        _agent(events, "thread-supervisor", "turn-first", '{"schema":"first/1"}')
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-supervisor",
                "turn": {"id": "turn-first", "status": "completed"},
            },
        )
        adapter.wait_for_turn("thread-supervisor", "turn-first", 0.2)

        client.next_turn = "turn-second"
        ack = adapter.request(
            "turn/start", {"threadId": "thread-supervisor", "input": []}
        )
        self.assertEqual("turn-second", ack["turn"]["id"])

    def test_keeps_only_current_turn_completed_hia_items(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events)
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
        adapter = ProjectRoleClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        events.publish("noise", value=1)
        events.publish("noise", value=2)
        events.publish("noise", value=3)

        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("PROJECT_EVENT_GAP", raised.exception.code)
        self.assertTrue(adapter.has_active_thread("thread-a"))
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-a",
                "turn": {"id": "turn-a", "status": "failed"},
            },
        )
        self.assertFalse(adapter.has_active_thread("thread-a"))

    def test_timeout_retains_exact_turn_until_late_terminal_and_hia_completion(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        writer = SceneWriterOwnership()
        adapter = ProjectRoleClient(
            client,
            events,
            scene_writer=writer,
            poll_interval_seconds=0.002,
        )

        def before_ack() -> None:
            client.emit_notification(
                "item/started",
                {
                    "threadId": "thread-a",
                    "turnId": "turn-a",
                    "item": {
                        "id": "hia-item-a",
                        "type": "mcpToolCall",
                        "server": "hia_mcp_v2",
                        "tool": "hia_execute_hom",
                    },
                },
            )

        client.before_ack = before_ack
        _start(adapter, client, "thread-a", "turn-a")
        reservation = writer.reserve("project", "project-a")
        owner = writer.bind(reservation, "thread-a", "turn-a")
        adapter.bind_scene_writer("thread-a", "turn-a", owner)

        with self.assertRaises(ProjectTurnTimeout):
            adapter.wait_for_turn("thread-a", "turn-a", 0.01)
        self.assertTrue(adapter.has_active_thread("thread-a"))
        self.assertEqual(
            (("thread-a", "turn-a"),),
            adapter.interrupt_threads(("thread-a",)),
        )
        self.assertEqual((), adapter.interrupt_threads(("thread-a",)))

        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-a",
                "turn": {"id": "turn-a", "status": "interrupted"},
            },
        )
        self.assertTrue(writer.retained_after_terminal(owner))
        client.emit_notification(
            "item/completed",
            {
                "threadId": "thread-a",
                "turnId": "turn-a",
                "item": {
                    "id": "hia-item-a",
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_execute_hom",
                },
            },
        )
        self.assertIsNone(writer.snapshot()["owner"])
        self.assertFalse(adapter.has_active_thread("thread-a"))

    def test_missing_hia_item_id_releases_after_matching_anonymous_completion(self) -> None:
        events = _WaitAwareEventBuffer()
        client = _Client(events)
        writer = SceneWriterOwnership()
        adapter = ProjectRoleClient(client, events, scene_writer=writer)
        _start(adapter, client, "thread-execution", "turn-execution")
        reservation = writer.reserve("project", "project-a")
        owner = writer.bind(
            reservation, "thread-execution", "turn-execution"
        )
        adapter.bind_scene_writer(
            "thread-execution", "turn-execution", owner
        )
        outcome = {}

        def wait_for_turn() -> None:
            try:
                adapter.wait_for_turn(
                    "thread-execution", "turn-execution", 0.5
                )
            except Exception as exc:
                outcome["error"] = exc

        waiter = threading.Thread(target=wait_for_turn)
        waiter.start()
        self.assertTrue(events.poll_entered.wait(0.2))
        client.emit_notification(
            "item/started",
            {
                "threadId": "thread-execution",
                "turnId": "turn-execution",
                "item": {
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_execute_hom",
                },
            },
        )
        waiter.join(1.0)
        self.assertFalse(waiter.is_alive())
        self.assertIsInstance(outcome.get("error"), ProjectAppServerError)
        self.assertEqual("INVALID_HIA_ITEM_ID", outcome["error"].code)
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-execution",
                "turn": {"id": "turn-execution", "status": "completed"},
            },
        )
        snapshot = writer.snapshot()
        self.assertEqual(owner, snapshot["owner"])
        self.assertTrue(snapshot["turn_terminal"])
        self.assertEqual(1, snapshot["anonymous_hia_items"])
        client.emit_notification(
            "item/completed",
            {
                "threadId": "thread-execution",
                "turnId": "turn-execution",
                "item": {
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_execute_hom",
                },
            },
        )
        self.assertIsNone(writer.snapshot()["owner"])
        self.assertFalse(adapter.has_active_thread("thread-execution"))

    def test_returns_terminal_receipt_for_invalid_or_non_object_agent_json(self) -> None:
        for body, code in (("not json", "INVALID_AGENT_JSON"), ("[]", "INVALID_AGENT_PAYLOAD")):
            with self.subTest(body=body):
                events = EventBuffer()
                client = _Client(events)
                adapter = ProjectRoleClient(client, events)
                _start(adapter, client, "thread-a", "turn-a")
                _agent(events, "thread-a", "turn-a", body)
                _terminal(events, "thread-a", "turn-a")
                completed = adapter.wait_for_turn("thread-a", "turn-a", 0.2)
                self.assertEqual("completed", completed.status)
                self.assertEqual("thread-a", completed.thread_id)
                self.assertEqual("turn-a", completed.turn_id)
                self.assertEqual({}, completed.payload)
                self.assertEqual(code, completed.payload_error_code)

    def test_rejects_conflicting_or_oversized_agent_messages(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        _agent(events, "thread-a", "turn-a", '{"value":1}')
        _agent(events, "thread-a", "turn-a", '{"value":2}')
        _terminal(events, "thread-a", "turn-a")
        completed = adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("CONFLICTING_AGENT_MESSAGES", completed.payload_error_code)

        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events, max_agent_message_bytes=8)
        _start(adapter, client, "thread-b", "turn-b")
        _delta(events, "thread-b", "turn-b", '{"long":')
        _delta(events, "thread-b", "turn-b", '"value"}')
        _terminal(events, "thread-b", "turn-b")
        completed = adapter.wait_for_turn("thread-b", "turn-b", 0.2)
        self.assertEqual("AGENT_MESSAGE_TOO_LARGE", completed.payload_error_code)

    def test_process_exit_and_failed_terminal_are_explicit_failures(self) -> None:
        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events)
        _start(adapter, client, "thread-a", "turn-a")
        events.publish("process_exit", returncode=7)
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-a", "turn-a", 0.2)
        self.assertEqual("CODEX_PROCESS_EXITED", raised.exception.code)

        events = EventBuffer()
        client = _Client(events)
        adapter = ProjectRoleClient(client, events)
        _start(adapter, client, "thread-b", "turn-b")
        _terminal(events, "thread-b", "turn-b", "failed")
        with self.assertRaises(ProjectAppServerError) as raised:
            adapter.wait_for_turn("thread-b", "turn-b", 0.2)
        self.assertEqual("PROJECT_TURN_NOT_COMPLETED", raised.exception.code)

if __name__ == "__main__":
    unittest.main()
