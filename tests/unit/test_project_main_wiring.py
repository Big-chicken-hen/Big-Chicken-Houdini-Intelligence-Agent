from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge import main as bridge_main  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.project_app_server import ProjectEffectClient  # noqa: E402
from hia_bridge.project_contracts import (  # noqa: E402
    PendingEffect,
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from hia_bridge.project_effects import EffectResult  # noqa: E402
from hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent  # noqa: E402
from hia_bridge.project_registry import ProjectRecord  # noqa: E402


class _RawClient:
    is_running = True

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.interrupted = threading.Event()

    def request(self, method, params):
        self.requests.append((method, dict(params)))
        if method == "turn/start":
            return {"threadId": params["threadId"], "turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            self.interrupted.set()
            return {"ok": True}
        return {"ok": True}


def _record(project_id: str, *, role_thread: bool = False) -> ProjectRecord:
    text = f"task for {project_id}"
    task_id, digest = authoritative_task_identity(text)
    roles = (
        {Role.EXECUTION: RoleThread(Role.EXECUTION, "thread-execution")}
        if role_thread
        else {}
    )
    return ProjectRecord(
        ProjectState(
            project_id=project_id,
            goal_thread_id=f"{project_id}-supervisor",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=ProjectStatus.EXECUTING_STAGE,
            roles=roles,
            pending_effects=(PendingEffect("effect-1", "test_effect"),),
        ),
        text,
    )


class _ImmediateEffectExecutor:
    constructed: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.constructed.append(dict(kwargs))

    def execute(self, state, effect):
        return EffectResult(state, None)


class _InterruptibleEffectExecutor:
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, **kwargs) -> None:
        self.client = kwargs["client"]

    def execute(self, state, effect):
        if effect.kind == "pause_goal":
            return EffectResult(state, LifecycleEvent(ProjectEvent.GOAL_PAUSED))
        self.client.request("turn/start", {"threadId": "thread-execution"})
        self.entered.set()
        if not self.release.wait(2):
            raise TimeoutError("test effect was not released")
        return EffectResult(state, None)


class ProjectMainWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        test_runtime = REPOSITORY_ROOT / ".runtime" / "tmp"
        test_runtime.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_runtime)
        self.root = Path(self.temp.name).resolve()
        self.render_root = (self.root.parent / f"{self.root.name}-renders").resolve()
        self.events = EventBuffer()
        self.client = _RawClient()
        _ImmediateEffectExecutor.constructed.clear()
        _InterruptibleEffectExecutor.entered.clear()
        _InterruptibleEffectExecutor.release.clear()

    def tearDown(self) -> None:
        _InterruptibleEffectExecutor.release.set()
        self.temp.cleanup()

    def _wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition was not reached")

    def test_runtime_recovery_uses_one_composed_production_graph_and_publishes(self) -> None:
        roots = bridge_main._project_evidence_roots(
            self.root, str(self.render_root)
        )
        with mock.patch.object(
            bridge_main, "ProjectEffectExecutor", _ImmediateEffectExecutor
        ):
            runtime = bridge_main._build_project_runtime(
                client=self.client,
                events=self.events,
                project_root=self.root,
                selected_backend="hia_mcp_v2",
                allowed_evidence_roots=roots,
            )
            runtime.registry.put(_record("project-1"))
            self.assertEqual(("project-1",), runtime.recover())
            self._wait_until(
                lambda: not runtime.registry.require("project-1").state.pending_effects
            )
            self.assertTrue(runtime.close(1))

        self.assertIs(runtime.service._registry, runtime.registry)
        self.assertIs(runtime.service._workflow, runtime.workflow)
        self.assertEqual(1, len(_ImmediateEffectExecutor.constructed))
        wiring = _ImmediateEffectExecutor.constructed[0]
        self.assertIs(wiring["client"], runtime.effect_client)
        self.assertIs(wiring["registry"], runtime.registry)
        self.assertIs(wiring["thread_factory"], runtime.thread_factory)
        self.assertIs(wiring["artifacts"], runtime.artifacts)
        self.assertEqual(roots, wiring["allowed_evidence_roots"])
        batch = self.events.poll(0, timeout=0)
        update_events = [
            event
            for event in batch["events"]
            if event["type"] == "project_team_updated"
        ]
        self.assertGreaterEqual(len(update_events), 2)
        self.assertEqual(
            ["project-1"], update_events[0]["recovered_project_ids"]
        )
        self.assertEqual(
            "hia-project-team/2", update_events[-1]["project_team"]["schema"]
        )
        self.assertEqual("project_workflow_closed", batch["events"][-1]["type"])

    def test_runtime_stop_interrupts_only_the_tracked_project_role_turn(self) -> None:
        with mock.patch.object(
            bridge_main, "ProjectEffectExecutor", _InterruptibleEffectExecutor
        ):
            runtime = bridge_main._build_project_runtime(
                client=self.client,
                events=self.events,
                project_root=self.root,
                selected_backend="hia_mcp_v2",
                allowed_evidence_roots=(self.root / ".runtime",),
            )
            runtime.registry.put(_record("project-1", role_thread=True))
            runtime.workflow.start("project-1")
            self.assertTrue(_InterruptibleEffectExecutor.entered.wait(1))

            self.assertTrue(runtime.workflow.stop("project-1"))
            self.assertTrue(self.client.interrupted.wait(1))
            _InterruptibleEffectExecutor.release.set()
            self._wait_until(
                lambda: runtime.registry.require("project-1").state.status
                is ProjectStatus.INTERRUPTED
            )
            runtime.close(1)

        interrupts = [
            params
            for method, params in self.client.requests
            if method == "turn/interrupt"
        ]
        self.assertEqual(
            [{"threadId": "thread-execution", "turnId": "turn-1"}], interrupts
        )
        event_types = [
            event["type"] for event in self.events.poll(0, timeout=0)["events"]
        ]
        self.assertIn("project_interrupt_requested", event_types)

    def test_project_effect_client_interrupts_exact_tracked_threads(self) -> None:
        adapter = ProjectEffectClient(self.client, self.events)
        adapter.request("turn/start", {"threadId": "thread-allowed"})

        interrupted = adapter.interrupt_threads(
            ["thread-other", "thread-allowed"]
        )

        self.assertEqual((("thread-allowed", "turn-1"),), interrupted)
        self.assertEqual(
            (
                "turn/interrupt",
                {"threadId": "thread-allowed", "turnId": "turn-1"},
            ),
            self.client.requests[-1],
        )

    def test_evidence_roots_reject_relative_and_drive_root_configuration(self) -> None:
        roots = bridge_main._project_evidence_roots(
            self.root, str(self.render_root)
        )
        self.assertEqual((self.root / ".runtime", self.render_root), roots)
        with self.assertRaisesRegex(
            bridge_main.BridgeError, "must be absolute"
        ):
            bridge_main._project_evidence_roots(self.root, "relative/renders")
        drive_root = Path(self.root.anchor)
        if drive_root.anchor:
            with self.assertRaisesRegex(
                bridge_main.BridgeError, "drive root"
            ):
                bridge_main._project_evidence_roots(
                    self.root, str(drive_root)
                )


if __name__ == "__main__":
    unittest.main()
