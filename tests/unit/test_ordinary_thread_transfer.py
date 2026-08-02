from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.bridge.hia_bridge.ordinary_transfer import OrdinaryThreadTransfer
from services.bridge.hia_bridge.errors import BridgeError
from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.session import BridgeSession


class _SessionClient:
    def set_event_sink(self, sink):
        self.sink = sink


class _Client:
    def __init__(
        self,
        root: Path,
        *,
        corrupt_fork: bool = False,
        project: bool = False,
        fail_old_delete: bool = False,
    ):
        source = "hia-project/p/execution" if project else None
        self.root = root
        self.corrupt_fork = corrupt_fork
        self.fail_old_delete = fail_old_delete
        self.calls: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.goals = {"old": {"objective": "goal", "status": "active"}}
        self.threads = {
            "old": {
                "id": "old",
                "cwd": str(root),
                "source": "appServer",
                "threadSource": source,
                "status": {"type": "idle"},
                "turns": [{"id": "turn-context"}],
            }
        }

    def request(self, method, params):
        params = dict(params)
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": dict(self.threads[params["threadId"]])}
        if method == "thread/goal/get":
            return {"goal": self.goals.get(params["threadId"])}
        if method == "thread/fork":
            old = self.threads[params["threadId"]]
            turns = [] if self.corrupt_fork else list(old["turns"])
            self.threads["new"] = {
                **old,
                "id": "new",
                "forkedFromId": params["threadId"],
                "turns": turns,
            }
            self.goals["new"] = self.goals.get(params["threadId"])
            return {
                "thread": dict(self.threads["new"]),
                "approvalPolicy": "on-request",
                "sandbox": {"type": "workspaceWrite", "networkAccess": False},
                "model": params.get("model", "model-default"),
                "reasoningEffort": "high",
                "serviceTier": params.get("serviceTier"),
            }
        if method == "thread/delete":
            thread_id = params["threadId"]
            if thread_id == "old" and self.fail_old_delete:
                raise RuntimeError("delete refused")
            self.deleted.append(thread_id)
            self.threads.pop(thread_id, None)
            self.goals.pop(thread_id, None)
            return {}
        raise AssertionError(method)


class OrdinaryThreadTransferTests(unittest.TestCase):
    def _exercise(self, transfer: OrdinaryThreadTransfer, thread_id: str = "old") -> None:
        for number in range(1, 4):
            transfer.observe_codex_event(
                {
                    "type": "codex_notification",
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": f"turn-{number}",
                        "item": {"id": f"compact-{number}", "type": "contextCompaction"},
                    },
                }
            )

    def test_third_real_compaction_forks_verifies_rebinds_and_deletes_old(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root)
            events: list[tuple[str, dict]] = []
            rebound: list[tuple[str, str]] = []
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / ".runtime/bridge/ordinary.json",
                publish=lambda kind, **fields: events.append((kind, fields)),
                rebind=lambda old, new: rebound.append((old, new)) or True,
                idle_timeout_seconds=0.2,
            )
            transfer.record_profile(
                "old", model="gpt-test", effort="high", service_tier="priority"
            )
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertEqual([("old", "new")], rebound)
            self.assertEqual(["old"], client.deleted)
            self.assertTrue(any(kind == "thread_transferred" for kind, _ in events))
            ledger = json.loads(
                (root / ".runtime/bridge/ordinary.json").read_text(encoding="utf-8")
            )
            self.assertEqual("new", ledger["threads"][0]["thread_id"])
            self.assertEqual([], ledger["threads"][0]["compaction_event_ids"])
            fork = next(params for method, params in client.calls if method == "thread/fork")
            self.assertEqual("gpt-test", fork["model"])
            self.assertEqual("priority", fork["serviceTier"])

    def test_duplicate_notification_pairs_count_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root)
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: True,
            )
            item = {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "old",
                    "turnId": "turn-1",
                    "item": {"id": "compact-1", "type": "contextCompaction"},
                },
            }
            legacy = {
                "type": "codex_notification",
                "method": "thread/compacted",
                "params": {"threadId": "old", "turnId": "turn-1"},
            }
            transfer.observe_codex_event(legacy)
            transfer.observe_codex_event(item)
            transfer.observe_codex_event(item)
            ledger = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(["item:turn-1:compact-1"], ledger["threads"][0]["compaction_event_ids"])

    def test_context_failure_preserves_old_and_cleans_only_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root, corrupt_fork=True)
            events: list[tuple[str, dict]] = []
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda kind, **fields: events.append((kind, fields)),
                rebind=lambda old, new: self.fail("invalid fork must not rebind"),
                idle_timeout_seconds=0.2,
            )
            transfer.record_profile("old", model="gpt-test", effort=None, service_tier=None)
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertIn("old", client.threads)
            self.assertEqual(["new"], client.deleted)
            failure = [fields for kind, fields in events if kind == "thread_transferred"][-1]
            self.assertIsNone(failure["new_thread_id"])

    def test_project_role_is_not_migrated_by_ordinary_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root, project=True)
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: self.fail("project role must not rebind"),
                idle_timeout_seconds=0.2,
            )
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertFalse(any(method == "thread/fork" for method, _ in client.calls))
            self.assertIn("old", client.threads)

    def test_confirmed_old_delete_failure_rolls_identity_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root, fail_old_delete=True)
            rebound: list[tuple[str, str]] = []
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: rebound.append((old, new)) or True,
                idle_timeout_seconds=0.2,
            )
            transfer.record_profile("old", model="gpt-test", effort=None, service_tier=None)
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertEqual([("old", "new"), ("new", "old")], rebound)
            self.assertIn("old", client.threads)
            self.assertNotIn("new", client.threads)
            ledger = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("old", ledger["threads"][0]["thread_id"])

    def test_restart_recovery_schedules_persisted_threshold_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger_path = root / "ledger.json"
            ledger_path.write_text(
                json.dumps(
                    {
                        "schema": "hia-ordinary-thread-transfer/1",
                        "threads": [
                            {
                                "thread_id": "old",
                                "model": "gpt-test",
                                "effort": "high",
                                "service_tier": None,
                                "compaction_event_ids": ["a", "b", "c"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            client = _Client(root)
            rebound: list[tuple[str, str]] = []
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=ledger_path,
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: rebound.append((old, new)) or False,
                idle_timeout_seconds=0.2,
            )
            self.assertEqual(("old",), transfer.recover())
            self.assertEqual((), transfer.recover())
            self.assertTrue(transfer.close(2.0))
            self.assertEqual([("old", "new")], rebound)

    def test_rebind_failure_restores_old_ledger_and_deletes_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root)
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: (
                    False
                    if (old, new) == ("new", "old")
                    else (_ for _ in ()).throw(RuntimeError("active Turn"))
                ),
                idle_timeout_seconds=0.2,
            )
            transfer.record_profile("old", model="gpt-test", effort=None, service_tier=None)
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertIn("old", client.threads)
            self.assertNotIn("new", client.threads)
            ledger = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("old", ledger["threads"][0]["thread_id"])

    @unittest.skipUnless(sys.platform == "win32", "Windows extended path contract")
    def test_windows_extended_cwd_is_the_same_project_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            client = _Client(root)
            client.threads["old"]["cwd"] = "\\\\?\\" + str(root)
            transfer = OrdinaryThreadTransfer(
                client,
                project_root=root,
                ledger_path=root / "ledger.json",
                publish=lambda *args, **kwargs: None,
                rebind=lambda old, new: True,
                idle_timeout_seconds=0.2,
            )
            transfer.record_profile("old", model="gpt-test", effort=None, service_tier=None)
            self._exercise(transfer)
            self.assertTrue(transfer.close(2.0))
            self.assertEqual(["old"], client.deleted)

    def test_session_rebind_is_one_locked_identity_cas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            session = BridgeSession(
                root,
                _SessionClient(),  # type: ignore[arg-type]
                EventBuffer(),
                focus_state_path=root / ".runtime/bridge/focus.json",
            )
            with session._lock:
                session._thread_id = "old"
                session._focus_enabled_threads.add("old")
                session._focus_goal_bindings["old"] = "a" * 64
            self.assertTrue(session.rebind_transferred_thread("old", "new"))
            with session._lock:
                self.assertEqual("new", session._thread_id)
                self.assertNotIn("old", session._focus_enabled_threads)
                self.assertIn("new", session._focus_enabled_threads)
                self.assertEqual("a" * 64, session._focus_goal_bindings["new"])
                session._thread_id = "new"
                session._turn_active = True
            with self.assertRaises(BridgeError):
                session.rebind_transferred_thread("new", "later")
            with session._lock:
                self.assertEqual("new", session._thread_id)


if __name__ == "__main__":
    unittest.main()
