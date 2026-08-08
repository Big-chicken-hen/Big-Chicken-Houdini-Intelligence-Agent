from __future__ import annotations

import threading
import time
import tempfile
import unittest
import json
from pathlib import Path
import sys
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402
from hia_bridge.thread_rotation import (  # noqa: E402
    ThreadRotationAdapters,
    ThreadRotationProfile,
    ThreadRotationService,
)


OLD_THREAD = "thread-old"
NEW_THREAD = "thread-new"


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.order: list[str] = []
        self.fail_fork = False
        self.fail_delete_thread_ids: set[str] = set()
        self.corrupt_new_history = False
        self.source = "hia-project/project-a/planning"
        self.goals: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _turn(turn_id: str) -> dict[str, Any]:
        return {
            "id": turn_id,
            "status": "completed",
            "items": [{"id": f"item-{turn_id}", "type": "contextCompaction"}],
        }

    def _thread(self, thread_id: str) -> dict[str, Any]:
        source = self.source
        turns = [self._turn(f"turn-{index}") for index in range(1, 4)]
        if thread_id == NEW_THREAD and self.corrupt_new_history:
            turns = turns[:2]
        thread: dict[str, Any] = {
            "id": thread_id,
            "threadSource": source,
            "turns": turns,
        }
        if thread_id == NEW_THREAD:
            thread["forkedFromId"] = OLD_THREAD
        return thread

    @staticmethod
    def _goal(thread_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "threadId": thread_id,
            "objective": fields["objective"],
            "status": fields["status"],
            "tokenBudget": fields.get("tokenBudget"),
            "tokensUsed": 1,
            "timeUsedSeconds": 1,
            "createdAt": 1,
            "updatedAt": 2,
        }

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        self.calls.append((method, dict(params)))
        if method == "thread/read":
            thread_id = str(params["threadId"])
            self.order.append(f"read:{thread_id}")
            return {"thread": self._thread(thread_id)}
        if method == "thread/goal/get":
            thread_id = str(params["threadId"])
            self.order.append(f"goal-get:{thread_id}")
            return {"goal": self.goals.get(thread_id)}
        if method == "thread/goal/set":
            thread_id = str(params["threadId"])
            self.order.append(f"goal-set:{thread_id}")
            goal = self._goal(thread_id, params)
            self.goals[thread_id] = goal
            return {"goal": goal}
        if method == "thread/fork":
            self.order.append("fork")
            if self.fail_fork:
                raise RuntimeError("fork failed")
            return {
                "thread": {
                    "id": NEW_THREAD,
                    "forkedFromId": params["threadId"],
                    "threadSource": params["threadSource"],
                },
                "model": params["model"],
                "reasoningEffort": "high",
                "serviceTier": params["serviceTier"],
                "sandbox": {"type": "workspaceWrite", "networkAccess": True},
                "approvalPolicy": params["approvalPolicy"],
                "roleTools": {"hia": False, "houdini": False},
            }
        if method == "thread/delete":
            thread_id = str(params["threadId"])
            self.order.append(f"delete:{thread_id}")
            if thread_id in self.fail_delete_thread_ids:
                raise RuntimeError("delete failed")
            self.goals.pop(thread_id, None)
        return {}


class ThreadRotationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _Client()
        self.idle = True
        self.events: list[dict[str, Any]] = []
        self.event = threading.Event()
        self.client.order = []
        self.goal_supported = False
        self.authoritative_thread_id = OLD_THREAD

        def publish(payload: Mapping[str, Any]) -> None:
            event = dict(payload)
            self.events.append(event)
            self.client.order.append(f"publish:{event['type']}")
            self.event.set()

        def rebind(old_thread_id: str, new_thread_id: str) -> None:
            self.assertEqual((OLD_THREAD, NEW_THREAD), (old_thread_id, new_thread_id))
            self.client.order.append("rebind")
            self.assertEqual(OLD_THREAD, self.authoritative_thread_id)
            self.authoritative_thread_id = new_thread_id

        def validate_role_tools(
            thread_id: str, result: Mapping[str, Any]
        ) -> bool:
            self.client.order.append("validate-role-tools")
            return thread_id == OLD_THREAD and result.get("roleTools") == {
                "hia": False,
                "houdini": False,
            }

        self.service = ThreadRotationService(
            self.client,
            ThreadRotationAdapters(
                expected_profile=lambda thread_id, _turn_id: (
                    self._profile() if thread_id == OLD_THREAD else None
                ),
                is_idle=lambda _thread_id: self.idle,
                rebind=rebind,
                publish=publish,
                validate_role_tools=validate_role_tools,
                supports_goal=lambda _thread_id: self.goal_supported,
            ),
        )
        self.addCleanup(self.service.close)

    @staticmethod
    def _profile() -> ThreadRotationProfile:
        return ThreadRotationProfile(
            cwd="E:/project",
            developer_instructions="keep the role contract",
            ephemeral=False,
            thread_source="hia-project/project-a/planning",
            model="gpt-test",
            reasoning_effort="high",
            service_tier="priority",
            fork_sandbox="workspace-write",
            response_sandbox={"type": "workspaceWrite", "networkAccess": True},
            config={
                "mcp_servers.hia_mcp_v2.enabled": False,
                "mcp_servers.houdini_intelligence.enabled": False,
            },
        )

    def _compaction(self, index: int, *, duplicates: int = 0) -> None:
        turn_id = f"turn-{index}"
        item_id = f"compact-{index}"
        for _ in range(duplicates + 1):
            self.service.observe(
                "thread/compacted",
                {"threadId": OLD_THREAD, "turnId": turn_id},
            )
            self.service.observe(
                "item/completed",
                {
                    "threadId": OLD_THREAD,
                    "turnId": turn_id,
                    "item": {"id": item_id, "type": "contextCompaction"},
                },
            )

    def _legacy_compaction(self, index: int) -> None:
        self.service.observe(
            "thread/compacted",
            {"threadId": OLD_THREAD, "turnId": f"turn-{index}"},
        )

    def _item_compaction(self, index: int) -> None:
        self.service.observe(
            "item/completed",
            {
                "threadId": OLD_THREAD,
                "turnId": f"turn-{index}",
                "item": {
                    "id": f"compact-{index}",
                    "type": "contextCompaction",
                },
            },
        )

    def _idle_notification(self) -> None:
        self.service.observe(
            "thread/status/changed",
            {"threadId": OLD_THREAD, "status": {"type": "idle"}},
        )

    def _wait_for(self, predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail("timed out waiting for asynchronous rotation")
            self.event.wait(min(remaining, 0.02))
            self.event.clear()

    def test_legacy_compaction_alone_counts_once(self) -> None:
        self._legacy_compaction(1)
        self._legacy_compaction(1)
        self.assertEqual(1, self.service.snapshot(OLD_THREAD)["count"])

    def test_context_compaction_item_alone_counts_once(self) -> None:
        self._item_compaction(1)
        self._item_compaction(1)
        self.assertEqual(1, self.service.snapshot(OLD_THREAD)["count"])

    def test_legacy_and_item_notifications_for_one_turn_count_once(self) -> None:
        self._compaction(1, duplicates=2)
        self.assertEqual(1, self.service.snapshot(OLD_THREAD)["count"])
        self._compaction(2, duplicates=2)
        self.assertEqual(2, self.service.snapshot(OLD_THREAD)["count"])
        self.assertEqual([], self.client.calls)

    def test_third_busy_then_native_idle_rotates_exactly_once(self) -> None:
        self.idle = False
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        state = self.service.snapshot(OLD_THREAD)
        self.assertTrue(state["pending"])
        self.assertEqual([], self.client.calls)

        self.idle = True
        self._idle_notification()
        self._idle_notification()
        self._wait_for(
            lambda: any(
                method == "thread/delete" and params["threadId"] == OLD_THREAD
                for method, params in self.client.calls
            )
        )
        self._wait_for(
            lambda: any(
                event.get("type") == "thread_rotated" for event in self.events
            )
        )
        self._idle_notification()

        self.assertEqual(
            ["thread/read", "thread/fork", "thread/read", "thread/delete"],
            [method for method, _ in self.client.calls],
        )
        fork_params = next(
            params for method, params in self.client.calls if method == "thread/fork"
        )
        self.assertEqual("turn-3", fork_params["lastTurnId"])
        self.assertEqual("never", fork_params["approvalPolicy"])
        self.assertNotIn("reasoningEffort", fork_params)
        self.assertEqual("E:/project", fork_params["cwd"])
        self.assertEqual(
            "keep the role contract", fork_params["developerInstructions"]
        )
        self.assertIs(fork_params["ephemeral"], False)
        self.assertEqual(
            [
                "read:thread-old",
                "fork",
                "read:thread-new",
                "validate-role-tools",
                "rebind",
                "delete:thread-old",
                "publish:thread_rotated",
            ],
            self.client.order,
        )
        self.assertFalse(self.events[-1]["old_thread_orphaned"])

    def test_fork_failure_latches_and_never_deletes_old_thread(self) -> None:
        self.client.fail_fork = True
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        self._wait_for(
            lambda: any(event["type"] == "thread_rotation_failed" for event in self.events)
        )
        state = self.service.snapshot(OLD_THREAD)
        self.assertTrue(state["failed"])
        self._idle_notification()
        self.assertEqual(
            ["thread/read", "thread/fork"],
            [method for method, _ in self.client.calls],
        )
        self.assertFalse(any(method == "thread/delete" for method, _ in self.client.calls))

    def test_old_delete_failure_reports_orphan_without_retry(self) -> None:
        self.client.fail_delete_thread_ids.add(OLD_THREAD)
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        self._wait_for(
            lambda: any(event["type"] == "thread_rotated" for event in self.events)
        )
        self._idle_notification()
        self.assertEqual(
            ["thread/read", "thread/fork", "thread/read", "thread/delete"],
            [method for method, _ in self.client.calls],
        )
        self.assertEqual(["thread_rotated"], [event["type"] for event in self.events])
        self.assertTrue(self.events[0]["old_thread_orphaned"])

    def test_native_validation_failure_deletes_new_once_and_keeps_old(self) -> None:
        self.client.corrupt_new_history = True
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        self._wait_for(
            lambda: any(event["type"] == "thread_rotation_failed" for event in self.events)
        )

        deleted = [
            params["threadId"]
            for method, params in self.client.calls
            if method == "thread/delete"
        ]
        self.assertEqual([NEW_THREAD], deleted)
        self.assertEqual(OLD_THREAD, self.authoritative_thread_id)
        self.assertNotIn("orphan_new_thread_id", self.events[-1])

    def test_failed_new_cleanup_reports_exact_orphan_without_retry(self) -> None:
        self.client.corrupt_new_history = True
        self.client.fail_delete_thread_ids.add(NEW_THREAD)
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        self._wait_for(
            lambda: any(event["type"] == "thread_rotation_failed" for event in self.events)
        )
        self._idle_notification()

        self.assertEqual(
            [NEW_THREAD],
            [
                params["threadId"]
                for method, params in self.client.calls
                if method == "thread/delete"
            ],
        )
        self.assertEqual(NEW_THREAD, self.events[-1]["orphan_new_thread_id"])

    def test_native_goal_is_set_and_read_back_before_rebind(self) -> None:
        self.goal_supported = True
        self.client.goals[OLD_THREAD] = self.client._goal(
            OLD_THREAD,
            {
                "objective": "Finish the native Goal",
                "status": "active",
                "tokenBudget": 25_000,
            },
        )
        for index in range(1, 4):
            self._compaction(index)
        self._idle_notification()
        self._wait_for(
            lambda: any(event["type"] == "thread_rotated" for event in self.events)
        )

        self.assertEqual(
            ("Finish the native Goal", "active", 25_000),
            (
                self.client.goals[NEW_THREAD]["objective"],
                self.client.goals[NEW_THREAD]["status"],
                self.client.goals[NEW_THREAD]["tokenBudget"],
            ),
        )
        self.assertLess(
            self.client.order.index("goal-get:thread-old"),
            self.client.order.index("fork"),
        )
        self.assertLess(
            self.client.order.index("goal-get:thread-new"),
            self.client.order.index("rebind"),
        )


class OrdinaryRotationAdapterTests(unittest.TestCase):
    def test_rebind_keeps_focus_and_historical_attachment_path(self) -> None:
        class _SessionClient:
            def __init__(self) -> None:
                self.sink = None

            def set_event_sink(self, sink) -> None:
                self.sink = sink

            @property
            def is_running(self) -> bool:
                return True

            @property
            def process_id(self) -> int:
                return 1

            def request(self, method: str, params: Mapping[str, Any]) -> Any:
                if method == "thread/start":
                    return {
                        "thread": {
                            "id": OLD_THREAD,
                            "threadSource": "appServer",
                        },
                        "cwd": str(root),
                        "model": "gpt-test",
                        "reasoningEffort": "high",
                        "serviceTier": "priority",
                        "sandbox": {
                            "type": "workspaceWrite",
                            "networkAccess": True,
                        },
                        "approvalPolicy": "never",
                    }
                if method == "turn/start":
                    return {"turn": {"id": "turn-runtime"}}
                raise AssertionError(method)

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            focus_path = root / ".runtime" / "bridge" / "focus.json"
            attachment = root / ".runtime" / "attachments" / OLD_THREAD / "keep.png"
            attachment.parent.mkdir(parents=True)
            attachment.write_bytes(b"keep")
            client = _SessionClient()
            session = BridgeSession(
                root,
                client,
                EventBuffer(),
                focus_state_path=focus_path,
            )
            session.start_thread(service_tier="priority")
            with session._lock:
                session._focus_enabled_threads.add(OLD_THREAD)
                session._focus_goal_bindings[OLD_THREAD] = "a" * 64

            profile = session.rotation_profile(OLD_THREAD, "turn-3")
            self.assertIsNotNone(profile)
            self.assertEqual(str(root), profile.cwd)
            self.assertIs(profile.ephemeral, False)
            self.assertTrue(session.rotation_is_idle(OLD_THREAD))

            session.start_turn(
                "update runtime",
                model="gpt-runtime",
                effort="low",
                service_tier="flex",
            )
            profile = session.rotation_profile(OLD_THREAD, "turn-3")
            self.assertEqual("gpt-runtime", profile.model)
            self.assertEqual("low", profile.reasoning_effort)
            self.assertEqual("flex", profile.service_tier)
            client.sink(
                {
                    "type": "codex_notification",
                    "method": "turn/completed",
                    "params": {
                        "threadId": OLD_THREAD,
                        "turn": {
                            "id": "turn-runtime",
                            "status": "completed",
                        },
                    },
                }
            )
            self.assertTrue(session.rotation_is_idle(OLD_THREAD))

            session.rotation_rebind(OLD_THREAD, NEW_THREAD)

            self.assertFalse(session.owns_ordinary_thread(OLD_THREAD))
            self.assertTrue(session.owns_ordinary_thread(NEW_THREAD))
            self.assertEqual(NEW_THREAD, session.snapshot()["thread_id"])
            focus = json.loads(focus_path.read_text(encoding="utf-8"))
            self.assertEqual(NEW_THREAD, focus["active_thread_id"])
            self.assertEqual([NEW_THREAD], focus["enabled_thread_ids"])
            self.assertTrue(attachment.is_file())
            new_attachment = attachment.parent.parent / NEW_THREAD / "future.png"
            new_attachment.parent.mkdir()
            new_attachment.write_bytes(b"future")
            self.assertEqual(
                [str(new_attachment.resolve())],
                session._validated_local_image_paths(
                    [str(new_attachment)],
                    NEW_THREAD,
                ),
            )
            session.close()


if __name__ == "__main__":
    unittest.main()
