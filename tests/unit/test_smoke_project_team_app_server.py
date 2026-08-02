from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "smoke_project_team_app_server.py"
SPEC = importlib.util.spec_from_file_location("smoke_project_team_app_server", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class _FakeDelegate:
    def __init__(self) -> None:
        self.is_running = True
        self.next_thread = 0
        self.threads = {"existing-thread"}
        self.goals: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.pause_failures: set[str] = set()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, dict(params)))
        if method in {"thread/start", "thread/fork"}:
            self.next_thread += 1
            thread_id = f"smoke-thread-{self.next_thread}"
            self.threads.add(thread_id)
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            return {"turn": {"id": "smoke-turn-1"}}
        if method == "turn/interrupt":
            return {"interrupted": True}
        if method == "thread/goal/get":
            goal = self.goals.get(params["threadId"])
            return {"goal": dict(goal) if goal is not None else None}
        if method == "thread/goal/set":
            thread_id = params["threadId"]
            if thread_id in self.pause_failures:
                raise RuntimeError("injected pause failure")
            goal = {
                "threadId": thread_id,
                "objective": params["objective"],
                "status": params["status"],
                "tokenBudget": params["tokenBudget"],
            }
            self.goals[thread_id] = goal
            return {"goal": dict(goal)}
        if method == "thread/delete":
            self.threads.remove(params["threadId"])
            return {"deleted": True}
        raise AssertionError(method)

    def request_with_timeout(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        del timeout_seconds
        return self.request(method, params)


class _Coordinator:
    def __init__(self) -> None:
        self.interrupts = 0

    def interrupt_project(self) -> dict[str, Any]:
        self.interrupts += 1
        return {"interrupted": True, "turn_ids": []}


class _Session:
    def __init__(self) -> None:
        self._project_threads = _Coordinator()


class ProjectTeamAppServerSmokeTests(unittest.TestCase):
    def test_recording_client_tracks_only_returned_thread_and_turn_ids(self) -> None:
        delegate = _FakeDelegate()
        client = smoke._RecordingClient(delegate)

        root = client.request("thread/start", {"sandbox": "read-only"})
        client.request(
            "turn/start",
            {"threadId": root["thread"]["id"], "input": []},
        )

        self.assertEqual(["smoke-thread-1"], client.snapshot_created_thread_ids())
        self.assertEqual(
            [("smoke-thread-1", "smoke-turn-1")],
            client.snapshot_started_turns(),
        )

    def test_cleanup_pauses_goal_then_deletes_exact_created_ids_only(self) -> None:
        delegate = _FakeDelegate()
        client = smoke._RecordingClient(delegate)
        root = client.request("thread/start", {})["thread"]["id"]
        worker = client.request("thread/start", {})["thread"]["id"]
        delegate.goals[root] = {
            "threadId": root,
            "objective": "smoke objective",
            "status": "active",
            "tokenBudget": None,
        }

        result = smoke._cleanup_created_threads(_Session(), client)

        self.assertTrue(result["complete"])
        self.assertEqual({root, worker}, set(result["deleted_thread_ids"]))
        self.assertEqual({"existing-thread"}, delegate.threads)
        pause_index = delegate.calls.index(
            (
                "thread/goal/set",
                {
                    "threadId": root,
                    "objective": "smoke objective",
                    "status": "paused",
                    "tokenBudget": None,
                },
            )
        )
        delete_index = delegate.calls.index(("thread/delete", {"threadId": root}))
        self.assertLess(pause_index, delete_index)

    def test_cleanup_retains_goal_thread_when_pause_cannot_be_verified(self) -> None:
        delegate = _FakeDelegate()
        client = smoke._RecordingClient(delegate)
        root = client.request("thread/start", {})["thread"]["id"]
        worker = client.request("thread/start", {})["thread"]["id"]
        delegate.goals[root] = {
            "threadId": root,
            "objective": "smoke objective",
            "status": "active",
            "tokenBudget": 1000,
        }
        delegate.pause_failures.add(root)

        result = smoke._cleanup_created_threads(_Session(), client)

        self.assertFalse(result["complete"])
        self.assertEqual([root], result["retained_thread_ids"])
        self.assertIn(root, delegate.threads)
        self.assertNotIn(worker, delegate.threads)
        self.assertNotIn(("thread/delete", {"threadId": root}), delegate.calls)

    def test_model_turn_cases_require_explicit_opt_in_but_dry_run_does_not(self) -> None:
        with self.assertRaises(SystemExit):
            smoke._arguments(["--case", "non-scene"])
        with self.assertRaises(SystemExit):
            smoke._arguments(["--case", "reference-intake"])
        parsed = smoke._arguments(["--case", "all", "--dry-run"])
        self.assertTrue(parsed.dry_run)
        self.assertFalse(parsed.allow_model_turns)
        self.assertEqual("pre-activation", smoke._arguments([]).case)

    def test_smoke_png_is_real_bounded_rgb_png(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / ".runtime") as raw:
            path = Path(raw) / "smoke.png"
            smoke._write_smoke_png(path, (12, 34, 56))
            payload = path.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLess(len(payload), 4096)
        width, height = struct.unpack(">II", payload[16:24])
        self.assertEqual((32, 32), (width, height))

    def test_tool_call_scan_is_exact_to_visual_thread(self) -> None:
        events = smoke.EventBuffer()
        events.publish(
            "codex_notification",
            method="item/completed",
            params={
                "threadId": "visual-thread",
                "item": {"type": "mcpToolCall", "tool": "hia_inspect"},
            },
        )
        events.publish(
            "codex_notification",
            method="item/completed",
            params={
                "threadId": "other-thread",
                "item": {"type": "mcpToolCall", "tool": "other_tool"},
            },
        )
        self.assertEqual(
            ["hia_inspect"],
            smoke._tool_calls_for_thread(events, "visual-thread"),
        )


if __name__ == "__main__":
    unittest.main()
