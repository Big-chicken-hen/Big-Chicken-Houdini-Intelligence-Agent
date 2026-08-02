from __future__ import annotations

import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.errors import BridgeError  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402
from hia_bridge.thread_transfer import (  # noqa: E402
    TRANSFER_COMPACTION_LIMIT,
    ThreadTransferManager,
)


class _Events:
    def __init__(self, *, fail_publish: bool = False) -> None:
        self.values: list[dict[str, Any]] = []
        self.fail_publish = fail_publish

    def publish(self, event_type: str, **fields: Any) -> dict[str, Any]:
        if self.fail_publish and event_type == "thread_transferred":
            raise RuntimeError("event transport failed")
        value = {"type": event_type, **copy.deepcopy(fields)}
        self.values.append(value)
        return value


class _Client:
    def __init__(self, old_id: str = "thread-old") -> None:
        self.old_id = old_id
        self.new_id = "thread-new"
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.fail_method: str | None = None
        self.invalid_fork = False
        self.invalid_cwd = False
        self.drop_context_prefix = False
        self.resolve_workspace_read_only = False
        self.fork_sandbox_mismatch = False
        self.delete_unknown = False
        self.read_old_unknown = False
        self.threads: dict[str, dict[str, Any]] = {
            old_id: {
                "id": old_id,
                "name": "完整木屋项目｜执行 AI",
                "threadSource": "hia-project/project-one/execution",
                "turns": [
                    {"id": "turn-finished", "status": "completed", "items": []}
                ],
            }
        }
        self.goals: dict[str, dict[str, Any]] = {
            old_id: {
                "threadId": old_id,
                "objective": "保留用户原 Goal",
                "status": "active",
                "tokenBudget": 98765,
                "tokensUsed": 4321,
                "timeUsedSeconds": 87,
            }
        }

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        copied = copy.deepcopy(dict(params))
        self.requests.append((method, copied))
        if self.fail_method == method:
            if method == "thread/delete" and self.delete_unknown:
                self.read_old_unknown = True
            raise RuntimeError(f"forced {method} failure")
        if method == "thread/read":
            thread_id = copied["threadId"]
            if thread_id == self.old_id and self.read_old_unknown:
                raise RuntimeError("transport outcome unknown")
            thread = self.threads.get(thread_id)
            if thread is None:
                raise BridgeError("THREAD_NOT_FOUND", "Thread not found", 404)
            return {"thread": copy.deepcopy(thread)}
        if method == "thread/goal/get":
            return {"goal": copy.deepcopy(self.goals.get(copied["threadId"]))}
        if method == "thread/goal/set":
            previous = self.goals.get(copied["threadId"], {})
            goal = {
                "threadId": copied["threadId"],
                "objective": copied["objective"],
                "status": copied["status"],
                "tokenBudget": copied["tokenBudget"],
                "tokensUsed": previous.get("tokensUsed", 0),
                "timeUsedSeconds": previous.get("timeUsedSeconds", 0),
            }
            self.goals[copied["threadId"]] = goal
            return {"goal": copy.deepcopy(goal)}
        if method == "thread/fork":
            new = copy.deepcopy(self.threads[copied["threadId"]])
            if self.drop_context_prefix and len(new.get("turns", [])) > 1:
                new["turns"] = new["turns"][-1:]
            new.update(
                id=self.new_id,
                forkedFromId=("wrong-old" if self.invalid_fork else copied["threadId"]),
                threadSource=copied.get("threadSource", new.get("threadSource")),
            )
            self.threads[self.new_id] = new
            old_goal = self.goals.get(copied["threadId"])
            if old_goal is not None:
                new_goal = copy.deepcopy(old_goal)
                new_goal["threadId"] = self.new_id
                self.goals[self.new_id] = new_goal
            return {
                "thread": copy.deepcopy(new),
                **self._profile(copied, method=method),
            }
        if method == "thread/name/set":
            self.threads[copied["threadId"]]["name"] = copied["name"]
            return {}
        if method == "thread/resume":
            return {
                "thread": copy.deepcopy(self.threads[copied["threadId"]]),
                **self._profile(copied, method=method),
            }
        if method == "thread/delete":
            thread_id = copied["threadId"]
            if thread_id not in self.threads:
                raise BridgeError("THREAD_NOT_FOUND", "Thread not found", 404)
            self.threads.pop(thread_id)
            self.goals.pop(thread_id, None)
            return {}
        raise AssertionError(f"unexpected method {method}")

    def _profile(
        self, params: Mapping[str, Any], *, method: str
    ) -> dict[str, Any]:
        sandbox = params.get("sandbox")
        sandbox_type = "readOnly" if sandbox == "read-only" else "workspaceWrite"
        if self.resolve_workspace_read_only and sandbox_type == "workspaceWrite":
            sandbox_type = "readOnly"
        if self.fork_sandbox_mismatch and method == "thread/fork":
            sandbox_type = "workspaceWrite"
        return {
            "model": params.get("model"),
            "reasoningEffort": params.get("reasoningEffort"),
            "serviceTier": params.get("serviceTier"),
            "approvalPolicy": params.get("approvalPolicy"),
            "approvalsReviewer": params.get("approvalsReviewer"),
            "sandbox": {"type": sandbox_type},
            "cwd": (
                str(REPOSITORY_ROOT.parent)
                if self.invalid_cwd
                else params.get("cwd")
            ),
        }


class _Harness:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        role: str | None = None,
        fail_publish: bool = False,
    ) -> None:
        self.client = _Client()
        self.events = _Events(fail_publish=fail_publish)
        self.active = False
        self.fail_commit = False
        self.role = role
        self.ownership = {self.client.old_id}
        self.manager = ThreadTransferManager(
            self.client,
            self.events,
            descriptor=self.descriptor,
            rehydrate_descriptor=self.rehydrate_descriptor,
            begin=self.begin,
            commit=self.commit,
            rollback=self.rollback,
            finish=lambda *_ids: None,
            state_path=state_path,
        )

    def descriptor(self, thread_id: str) -> dict[str, Any] | None:
        if thread_id not in self.ownership:
            return None
        profile = self.role or "single"
        read_only = self.role is not None and self.role != "execution"
        result = {
            "thread_id": thread_id,
            "title": f"完整木屋项目｜{profile}",
            "model": "gpt-5.6-sol",
            "effort": "ultra",
            "service_tier": "priority",
            "cwd": str(REPOSITORY_ROOT),
            "approval_policy": "never" if read_only else "on-request",
            "approvals_reviewer": "user",
            "sandbox": "read-only" if read_only else "workspace-write",
            "developer_instructions": profile + "-role-boundary",
            "config": {
                "mcp_servers.hia_mcp_v2.enabled": self.role in {None, "execution"},
                "mcp_servers.houdini_intelligence.enabled": self.role in {None, "execution"},
                **(
                    {
                        "multi_agent_mode": (
                            "explicitRequestOnly"
                            if self.role == "execution"
                            else "proactive"
                        )
                    }
                    if self.role is not None
                    else {}
                ),
            },
            "thread_source": f"hia-project/project-one/{profile}",
            "profile": profile,
            "selected": True,
            "focus_enabled": True,
            "focus_binding": "a" * 64,
        }
        if self.role is not None:
            result.update(project_id="project-one", role=self.role)
        return result

    @staticmethod
    def rehydrate_descriptor(value: Mapping[str, Any]) -> Mapping[str, Any]:
        profile = value.get("profile", "single")
        return {
            **copy.deepcopy(dict(value)),
            "developer_instructions": str(profile) + "-role-boundary",
        }

    def begin(self, thread_id: str) -> bool:
        return thread_id in self.ownership and not self.active

    def commit(
        self, old_id: str, new_id: str, descriptor: Mapping[str, Any]
    ) -> dict[str, Any]:
        del descriptor
        if self.fail_commit:
            raise RuntimeError("forced registry write failure")
        if old_id in self.ownership:
            self.ownership.remove(old_id)
        self.ownership.add(new_id)
        return (
            {"project_id": "project-one", "role": self.role}
            if self.role is not None
            else {}
        )

    def rollback(
        self, new_id: str, old_id: str, descriptor: Mapping[str, Any]
    ) -> None:
        del descriptor
        self.ownership.discard(new_id)
        self.ownership.add(old_id)

    def compact(self, number: int, *, paired: bool = False) -> None:
        turn_id = f"turn-compact-{number}"
        legacy = {
                "type": "codex_notification",
                "method": "thread/compacted",
                "params": {"threadId": self.client.old_id, "turnId": turn_id},
        }
        self.manager.observe(legacy)
        self.manager.after_event(legacy)
        if paired:
            item = {
                    "type": "codex_notification",
                    "method": "item/completed",
                    "params": {
                        "threadId": self.client.old_id,
                        "turnId": turn_id,
                        "item": {"id": f"compact-item-{number}", "type": "contextCompaction"},
                    },
            }
            self.manager.observe(item)
            self.manager.after_event(item)

    def wait_event(self, timeout: float = 2.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.events.values:
                return self.events.values[-1]
            time.sleep(0.01)
        raise AssertionError("thread transfer did not publish an event")


class ThreadTransferTests(unittest.TestCase):
    def _state_path(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        root = REPOSITORY_ROOT / ".runtime" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(directory.cleanup)
        return directory, Path(directory.name) / "thread-transfer.json"

    def test_compaction_events_deduplicate_and_third_waits_for_idle_turn(self) -> None:
        harness = _Harness()
        harness.compact(1, paired=True)
        self.assertEqual(1, harness.manager.snapshot("thread-old")["compactions"])
        harness.compact(2, paired=True)
        self.assertEqual(2, harness.manager.snapshot("thread-old")["compactions"])
        self.assertFalse(harness.events.values)
        harness.active = True
        harness.compact(3, paired=True)
        self.assertTrue(harness.manager.snapshot("thread-old")["pending"])
        self.assertFalse(any(method == "thread/fork" for method, _ in harness.client.requests))
        harness.active = False
        harness.manager.after_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {},
            }
        )
        self.assertEqual("thread-new", harness.wait_event()["new_thread_id"])

    def test_success_preserves_native_profile_goal_title_and_resets_generation(self) -> None:
        harness = _Harness()
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertEqual(
            {"type", "old_thread_id", "new_thread_id"}, set(event)
        )
        self.assertNotIn("thread-old", harness.client.threads)
        self.assertIn("thread-new", harness.ownership)
        self.assertEqual(
            {
                "threadId": "thread-new",
                "objective": "保留用户原 Goal",
                "status": "active",
                "tokenBudget": 98765,
                "tokensUsed": 4321,
                "timeUsedSeconds": 87,
            },
            harness.client.goals["thread-new"],
        )
        fork = next(params for method, params in harness.client.requests if method == "thread/fork")
        self.assertEqual("turn-finished", fork["lastTurnId"])
        self.assertEqual("gpt-5.6-sol", fork["model"])
        self.assertEqual("priority", fork["serviceTier"])
        self.assertNotIn("reasoningEffort", fork)
        self.assertEqual("workspace-write", fork["sandbox"])
        self.assertEqual("on-request", fork["approvalPolicy"])
        self.assertEqual("single-role-boundary", fork["developerInstructions"])
        self.assertTrue(fork["config"]["mcp_servers.hia_mcp_v2.enabled"])
        self.assertEqual(
            {"generation": 1, "compactions": 0, "pending": False},
            harness.manager.snapshot("thread-new"),
        )
        harness.client.old_id = "thread-new"
        harness.client.new_id = "thread-next"
        harness.compact(4)
        harness.compact(5)
        self.assertFalse(any(
            params.get("threadId") == "thread-new"
            for method, params in harness.client.requests
            if method == "thread/fork"
        ))

    def test_fork_validation_commit_and_delete_failures_never_misdelete_old(self) -> None:
        cases = ("thread/fork", "invalid-fork", "commit", "thread/delete")
        for case in cases:
            with self.subTest(case=case):
                harness = _Harness()
                if case == "invalid-fork":
                    harness.client.invalid_fork = True
                elif case == "commit":
                    harness.fail_commit = True
                else:
                    harness.client.fail_method = case
                for number in range(1, 4):
                    harness.compact(number)
                event = harness.wait_event()
                self.assertIsNone(event["new_thread_id"])
                self.assertIn("thread-old", harness.client.threads)
                self.assertIn("thread-old", harness.ownership)
                self.assertFalse(
                    any(
                        method == "thread/delete" and case != "thread/delete"
                        for method, _params in harness.client.requests
                    )
                )

    def test_state_write_failure_restores_in_memory_state_before_commit(self) -> None:
        harness = _Harness()
        harness.manager._threads["thread-old"] = {
            **harness.manager._new_state(),
            "count": TRANSFER_COMPACTION_LIMIT,
            "pending": True,
        }
        before = copy.deepcopy(harness.manager._threads)
        with mock.patch.object(harness.manager, "_write", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                harness.manager._prepare_delete(
                    "thread-old",
                    "thread-new",
                    "turn-finished",
                    {},
                    harness.descriptor("thread-old"),
                    harness.client._profile(
                        harness.manager._profile_params(
                            harness.descriptor("thread-old")
                        ),
                        method="thread/resume",
                    ),
                )
        self.assertEqual(before, harness.manager._threads)
        self.assertEqual([], harness.manager._pending_deletes)

    def test_latest_terminal_turn_and_complete_context_are_preserved(self) -> None:
        harness = _Harness()
        harness.client.threads["thread-old"]["turns"] = [
            {
                "id": "turn-finished",
                "status": "completed",
                "items": [
                    {"id": "item-user", "type": "userMessage", "text": "A"}
                ],
            },
            {
                "id": "turn-latest-failed",
                "status": "failed",
                "items": [
                    {"id": "item-error", "type": "error", "message": "B"}
                ],
            },
        ]
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertEqual("thread-new", event["new_thread_id"])
        fork = next(
            params
            for method, params in harness.client.requests
            if method == "thread/fork"
        )
        self.assertEqual("turn-latest-failed", fork["lastTurnId"])
        self.assertEqual(
            ["turn-finished", "turn-latest-failed"],
            [turn["id"] for turn in harness.client.threads["thread-new"]["turns"]],
        )

    def test_context_mismatch_preserves_old_thread(self) -> None:
        harness = _Harness()
        harness.client.threads["thread-old"]["turns"].insert(
            0,
            {
                "id": "turn-earlier",
                "status": "completed",
                "items": [
                    {
                        "id": "item-context",
                        "type": "agentMessage",
                        "text": "keep",
                    }
                ],
            },
        )
        harness.client.drop_context_prefix = True
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertIsNone(event["new_thread_id"])
        self.assertIn("thread-old", harness.client.threads)
        self.assertEqual({"thread-old"}, harness.ownership)
        self.assertFalse(
            any(
                method == "thread/delete"
                for method, _params in harness.client.requests
            )
        )

    def test_delete_success_then_publish_failure_never_rolls_back_deleted_owner(self) -> None:
        harness = _Harness(fail_publish=True)
        for number in range(1, 4):
            harness.compact(number)
        deadline = time.monotonic() + 2
        while "thread-old" in harness.client.threads and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn("thread-old", harness.client.threads)
        self.assertEqual({"thread-new"}, harness.ownership)
        self.assertEqual([], harness.events.values)

    def test_restart_recovers_only_reverified_pending_delete(self) -> None:
        _directory, state_path = self._state_path()
        first = _Harness(state_path=state_path)
        first.client.fail_method = "thread/delete"
        first.client.delete_unknown = True
        for number in range(1, 4):
            first.compact(number)
        first.wait_event()
        self.assertTrue(first.manager._pending_deletes)
        self.assertEqual({"thread-new"}, first.ownership)

        second_events = _Events()
        second = ThreadTransferManager(
            first.client,
            second_events,
            descriptor=first.descriptor,
            rehydrate_descriptor=first.rehydrate_descriptor,
            begin=first.begin,
            commit=first.commit,
            rollback=first.rollback,
            finish=lambda *_ids: None,
            state_path=state_path,
        )
        first.client.fail_method = None
        first.client.read_old_unknown = False
        second.recover_pending_deletes()
        self.assertNotIn("thread-old", first.client.threads)
        self.assertEqual("thread-new", second_events.values[-1]["new_thread_id"])
        self.assertFalse(second._pending_deletes)

    def test_recovery_refuses_unverified_fork_lineage(self) -> None:
        _directory, state_path = self._state_path()
        first = _Harness(state_path=state_path)
        first.client.fail_method = "thread/delete"
        first.client.delete_unknown = True
        for number in range(1, 4):
            first.compact(number)
        first.wait_event()
        first.client.threads["thread-new"]["forkedFromId"] = "other"
        first.client.fail_method = None
        second_events = _Events()
        second = ThreadTransferManager(
            first.client,
            second_events,
            descriptor=first.descriptor,
            rehydrate_descriptor=first.rehydrate_descriptor,
            begin=first.begin,
            commit=first.commit,
            rollback=first.rollback,
            finish=lambda *_ids: None,
            state_path=state_path,
        )
        second.recover_pending_deletes()
        self.assertIn("thread-old", first.client.threads)
        self.assertIsNone(second_events.values[-1]["new_thread_id"])

    def test_recovery_after_irreversible_delete_accepts_failed_terminal_turn(self) -> None:
        _directory, state_path = self._state_path()
        first = _Harness(state_path=state_path)
        first.client.threads["thread-old"]["turns"] = [
            {"id": "turn-failed", "status": "failed", "items": []}
        ]
        first.manager._finalize_delete = mock.Mock(  # type: ignore[method-assign]
            side_effect=RuntimeError("forced marker cleanup failure")
        )
        for number in range(1, 4):
            first.compact(number)
        self.assertEqual("thread-new", first.wait_event()["new_thread_id"])
        self.assertNotIn("thread-old", first.client.threads)
        self.assertTrue(first.manager._pending_deletes)

        second_events = _Events()
        second = ThreadTransferManager(
            first.client,
            second_events,
            descriptor=first.descriptor,
            rehydrate_descriptor=first.rehydrate_descriptor,
            begin=first.begin,
            commit=first.commit,
            rollback=first.rollback,
            finish=lambda *_ids: None,
            state_path=state_path,
        )
        second.recover_pending_deletes()
        self.assertEqual("thread-new", second_events.values[-1]["new_thread_id"])
        self.assertFalse(second._pending_deletes)

    def test_recovery_after_old_delete_rejects_effective_sandbox_widening(self) -> None:
        _directory, state_path = self._state_path()
        first = _Harness(state_path=state_path)
        first.client.resolve_workspace_read_only = True
        first.manager._finalize_delete = mock.Mock(  # type: ignore[method-assign]
            side_effect=RuntimeError("forced marker cleanup failure")
        )
        for number in range(1, 4):
            first.compact(number)
        self.assertEqual("thread-new", first.wait_event()["new_thread_id"])
        self.assertNotIn("thread-old", first.client.threads)

        first.client.resolve_workspace_read_only = False
        second_events = _Events()
        second = ThreadTransferManager(
            first.client,
            second_events,
            descriptor=first.descriptor,
            rehydrate_descriptor=first.rehydrate_descriptor,
            begin=first.begin,
            commit=first.commit,
            rollback=first.rollback,
            finish=lambda *_ids: None,
            state_path=state_path,
        )
        second.recover_pending_deletes()
        self.assertIsNone(second_events.values[-1]["new_thread_id"])
        self.assertTrue(second._pending_deletes)
        self.assertIn("thread-new", first.client.threads)

    def test_cwd_mismatch_is_rejected_before_ownership_switch_or_delete(self) -> None:
        harness = _Harness()
        harness.client.invalid_cwd = True
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertIsNone(event["new_thread_id"])
        self.assertIn("thread-old", harness.client.threads)
        self.assertEqual({"thread-old"}, harness.ownership)
        self.assertFalse(
            any(method == "thread/delete" for method, _params in harness.client.requests)
        )

    def test_effective_read_only_windows_sandbox_is_preserved_across_transfer(self) -> None:
        harness = _Harness()
        harness.client.resolve_workspace_read_only = True
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertEqual("thread-new", event["new_thread_id"])
        self.assertNotIn("thread-old", harness.client.threads)

    def test_effective_sandbox_mismatch_preserves_old_thread(self) -> None:
        harness = _Harness()
        harness.client.resolve_workspace_read_only = True
        harness.client.fork_sandbox_mismatch = True
        for number in range(1, 4):
            harness.compact(number)
        event = harness.wait_event()
        self.assertIsNone(event["new_thread_id"])
        self.assertIn("thread-old", harness.client.threads)
        self.assertEqual({"thread-old"}, harness.ownership)
        self.assertFalse(
            any(method == "thread/delete" for method, _params in harness.client.requests)
        )

    def test_fresh_restart_rehydrates_normal_and_project_runtime_from_marker(self) -> None:
        for role in (None, "visual_review"):
            with self.subTest(role=role):
                _directory, state_path = self._state_path()
                first = _Harness(state_path=state_path, role=role)
                first.client.fail_method = "thread/delete"
                first.client.delete_unknown = True
                for number in range(1, 4):
                    first.compact(number)
                first.wait_event()
                marker_descriptor = first.manager._pending_deletes[0]["descriptor"]
                self.assertNotIn("developer_instructions", marker_descriptor)
                self.assertEqual(str(REPOSITORY_ROOT), marker_descriptor["cwd"])
                self.assertEqual("ultra", marker_descriptor["effort"])
                self.assertIn("config", marker_descriptor)

                ownership: set[str] = set()
                runtimes: dict[str, dict[str, Any]] = {}
                selected: list[str | None] = [None]

                def descriptor(thread_id: str) -> Mapping[str, Any] | None:
                    value = runtimes.get(thread_id)
                    if value is None:
                        return None
                    return {
                        **copy.deepcopy(value),
                        "thread_id": thread_id,
                        "developer_instructions": str(value["profile"])
                        + "-role-boundary",
                    }

                def rehydrate(value: Mapping[str, Any]) -> Mapping[str, Any]:
                    return {
                        **copy.deepcopy(dict(value)),
                        "developer_instructions": str(value["profile"])
                        + "-role-boundary",
                    }

                def commit(
                    old_id: str,
                    new_id: str,
                    runtime: Mapping[str, Any],
                ) -> Mapping[str, Any]:
                    ownership.discard(old_id)
                    ownership.add(new_id)
                    runtimes.pop(old_id, None)
                    runtimes[new_id] = {
                        key: copy.deepcopy(runtime[key])
                        for key in runtime
                        if key != "developer_instructions"
                    }
                    if runtime.get("selected") is True:
                        selected[0] = new_id
                    return {
                        key: runtime[key]
                        for key in ("project_id", "role")
                        if isinstance(runtime.get(key), str)
                    }

                def rollback(
                    new_id: str,
                    old_id: str,
                    runtime: Mapping[str, Any],
                ) -> None:
                    commit(new_id, old_id, runtime)

                events = _Events()
                recovered = ThreadTransferManager(
                    first.client,
                    events,
                    descriptor=descriptor,
                    rehydrate_descriptor=rehydrate,
                    begin=lambda _thread_id: False,
                    commit=commit,
                    rollback=rollback,
                    finish=lambda *_ids: None,
                    state_path=state_path,
                )
                first.client.fail_method = None
                first.client.read_old_unknown = False
                recovered.recover_pending_deletes()

                self.assertEqual({"thread-new"}, ownership)
                self.assertEqual("thread-new", selected[0])
                self.assertEqual(role or "single", runtimes["thread-new"]["profile"])
                self.assertEqual(str(REPOSITORY_ROOT), runtimes["thread-new"]["cwd"])
                self.assertEqual("ultra", runtimes["thread-new"]["effort"])
                self.assertNotIn("developer_instructions", runtimes["thread-new"])
                self.assertNotIn("thread-old", first.client.threads)
                self.assertEqual("thread-new", events.values[-1]["new_thread_id"])

    def test_all_five_project_roles_emit_project_association(self) -> None:
        for role in (
            "supervisor",
            "planning",
            "execution",
            "visual_review",
            "technical_review",
        ):
            with self.subTest(role=role):
                harness = _Harness(role=role)
                for number in range(1, 4):
                    harness.compact(number)
                event = harness.wait_event()
                self.assertEqual("project-one", event["project_id"])
                self.assertEqual(role, event["role"])
                fork = next(
                    params
                    for method, params in harness.client.requests
                    if method == "thread/fork"
                )
                resume = next(
                    params
                    for method, params in harness.client.requests
                    if method == "thread/resume"
                )
                expected_mode = (
                    "explicitRequestOnly" if role == "execution" else "proactive"
                )
                expected_hia = role == "execution"
                for params in (fork, resume):
                    self.assertEqual(expected_mode, params["config"]["multi_agent_mode"])
                    self.assertEqual(
                        expected_hia,
                        params["config"]["mcp_servers.hia_mcp_v2.enabled"],
                    )
                    self.assertEqual(
                        expected_hia,
                        params["config"][
                            "mcp_servers.houdini_intelligence.enabled"
                        ],
                    )


class TransferEventVisibilityTests(unittest.TestCase):
    class _BareClient:
        is_running = True
        process_id = 1

        def set_event_sink(self, sink: Any) -> None:
            self.sink = sink

        def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
            raise AssertionError((method, params))

    def test_native_deleted_is_consumed_before_successful_transfer_event(self) -> None:
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, self._BareClient(), events)
        session._normal_transfers.add("thread-old")
        cursor = events.poll(0, timeout=0)["latest"]
        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "thread/deleted",
                "params": {"threadId": "thread-old"},
            }
        )
        events.publish(
            "thread_transferred",
            old_thread_id="thread-old",
            new_thread_id="thread-new",
        )
        visible = events.poll(cursor, timeout=0)["events"]
        self.assertEqual(["thread_transferred"], [event["type"] for event in visible])


if __name__ == "__main__":
    unittest.main()
