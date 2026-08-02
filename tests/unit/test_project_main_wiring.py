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
from tests.unit.project_test_support import server_transports  # noqa: E402


class _RawClient:
    is_running = True

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.interrupted = threading.Event()
        self.native_threads: dict[str, dict] = {}
        self.native_goals: dict[str, dict | None] = {}

    def request(self, method, params):
        self.requests.append((method, dict(params)))
        if method == "turn/start":
            return {"threadId": params["threadId"], "turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            self.interrupted.set()
            return {"ok": True}
        if method == "thread/read":
            return {"thread": self.native_threads.get(params["threadId"])}
        if method == "thread/goal/get":
            return {"goal": self.native_goals.get(params["threadId"])}
        if method == "thread/goal/set":
            goal = {
                "threadId": params["threadId"],
                "status": params["status"],
                "objective": params["objective"],
                "tokenBudget": params["tokenBudget"],
            }
            self.native_goals[params["threadId"]] = goal
            return {"goal": goal}
        return {"ok": True}


def _record(
    project_id: str,
    *,
    role_thread: bool = False,
    supervisor_thread_id: str | None = None,
) -> ProjectRecord:
    text = f"task for {project_id}"
    task_id, digest = authoritative_task_identity(text)
    roles = {}
    if supervisor_thread_id is not None:
        roles[Role.SUPERVISOR] = RoleThread(
            Role.SUPERVISOR, supervisor_thread_id
        )
    if role_thread:
        roles[Role.EXECUTION] = RoleThread(Role.EXECUTION, "thread-execution")
    return ProjectRecord(
        ProjectState(
            project_id=project_id,
            goal_thread_id=supervisor_thread_id or f"{project_id}-supervisor",
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
                server_transports=server_transports(),
                allowed_evidence_roots=roots,
            )
            supervisor_id = "project-1-supervisor-migrated"
            runtime.registry.put(
                _record("project-1", supervisor_thread_id=supervisor_id)
            )
            # Recovery verifies the exact migrated native identity and Goal but
            # deliberately requests no Turn history.  The authoritative task
            # remains available only from the durable Bridge registry.
            self.client.native_threads[supervisor_id] = {
                "id": supervisor_id,
                "threadSource": "hia-project/project-1/supervisor",
                "status": {"type": "idle"},
            }
            self.client.native_goals[supervisor_id] = {
                "threadId": supervisor_id,
                    "status": "paused",
            }
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
        reads = [
            params
            for method, params in self.client.requests
            if method == "thread/read"
        ]
        self.assertEqual(
            [{"threadId": supervisor_id, "includeTurns": False}], reads
        )

    def test_runtime_recovery_isolates_native_identity_failure(self) -> None:
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )
        runtime.registry.put(
            _record("project-1", supervisor_thread_id="missing-supervisor")
        )

        self.assertEqual((), runtime.recover())
        failed = runtime.registry.require("project-1")
        self.assertTrue(failed.authoritative_task_text)
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, failed.state.status)
        self.assertTrue(failed.state.recovery_required)
        self.assertEqual(
            ProjectStatus.EXECUTING_STAGE, failed.state.recovery_return_status
        )
        self.assertEqual("effect-1", failed.state.recovery_pending_effects[0].effect_id)
        self.assertEqual((), failed.state.pending_effects)
        self.assertIn("Thread identity is missing", failed.state.attention_reason)
        update = next(
            event
            for event in self.events.poll(0, timeout=0)["events"]
            if event["type"] == "project_team_updated"
        )
        self.assertEqual([], update["recovered_project_ids"])
        self.assertEqual("project-1", update["recovery_failures"][0]["project_id"])
        project = update["project_team"]["projects"][0]
        self.assertEqual("needs_attention", project["status"])
        self.assertIn("Thread identity is missing", project["attention_reason"])
        runtime.close(1)

    def test_active_recovery_attention_revalidates_before_goal_and_restores_work(self) -> None:
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )
        supervisor_id = "recoverable-supervisor"
        record = _record("project-retry", supervisor_thread_id=supervisor_id)
        original = PendingEffect("original-attention", "show_attention", {"why": "queued"})
        runtime.registry.put(
            ProjectRecord(
                replace(
                    record.state,
                    resume_status=ProjectStatus.AUTHORIZATION,
                    pending_effects=(original,),
                ),
                record.authoritative_task_text,
            )
        )

        self.assertEqual((), runtime.recover())
        failed = runtime.registry.require("project-retry").state
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, failed.status)
        self.assertEqual(ProjectStatus.AUTHORIZATION, failed.resume_status)
        self.assertEqual((original,), failed.recovery_pending_effects)

        runtime.service.continue_project(project_id="project-retry")
        self._wait_until(
            lambda: runtime.registry.require("project-retry").state.status
            is ProjectStatus.NEEDS_ATTENTION
            and "Thread identity is missing"
            in (runtime.registry.require("project-retry").state.last_error or "")
        )
        still_failed = runtime.registry.require("project-retry").state
        self.assertTrue(still_failed.recovery_required)
        self.assertEqual((original,), still_failed.recovery_pending_effects)
        self.assertFalse(
            any(method == "thread/goal/set" for method, _ in self.client.requests)
        )

        self.client.native_threads[supervisor_id] = {
            "id": supervisor_id,
            "threadSource": "hia-project/project-retry/supervisor",
            "status": {"type": "idle"},
        }
        self.client.native_goals[supervisor_id] = {
            "threadId": supervisor_id,
            "status": "paused",
        }
        runtime.service.continue_project(project_id="project-retry")
        self._wait_until(
            lambda: runtime.registry.require("project-retry").state.status
            is ProjectStatus.EXECUTING_STAGE
            and not runtime.registry.require("project-retry").state.pending_effects
        )
        restored = runtime.registry.require("project-retry").state
        self.assertFalse(restored.recovery_required)
        self.assertEqual(ProjectStatus.AUTHORIZATION, restored.resume_status)
        self.assertEqual((), restored.recovery_pending_effects)
        goal_sets = [
            params
            for method, params in self.client.requests
            if method == "thread/goal/set"
        ]
        self.assertEqual(1, len(goal_sets))
        self.assertEqual("paused", goal_sets[0]["status"])
        self.assertTrue(runtime.close(1))

    def test_runtime_recovery_rejects_registry_without_authoritative_task(self) -> None:
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )
        runtime.registry.path.parent.mkdir(parents=True, exist_ok=True)
        runtime.registry.path.write_text(
            '{"schema":"hia-project-registry/2","projects":[{"state":{}}]}',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "entry is incomplete"):
            runtime.recover()
        self.assertFalse(
            any(method == "thread/read" for method, _ in self.client.requests)
        )
        runtime.close(1)

    def test_interrupted_restart_validates_registry_identity_and_resumes_goal(self) -> None:
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )
        supervisor_id = "supervisor-after-transfer"
        record = _record("project-restart", supervisor_thread_id=supervisor_id)
        interrupted = replace(
            record.state,
            status=ProjectStatus.INTERRUPTED,
            resume_status=ProjectStatus.EXECUTING_STAGE,
            pending_effects=(),
            revision=record.state.revision + 1,
        )
        runtime.registry.put(
            ProjectRecord(interrupted, record.authoritative_task_text)
        )
        self.client.native_threads[supervisor_id] = {
            "id": supervisor_id,
            "threadSource": "hia-project/project-restart/supervisor",
            "status": {"type": "idle"},
        }
        self.client.native_goals[supervisor_id] = {
            "threadId": supervisor_id,
            "status": "paused",
        }

        pending = runtime.runner.dispatch(
            "project-restart",
            LifecycleEvent(ProjectEvent.RESTART_REQUESTED),
        )
        self.assertEqual("verify_recovery", pending.state.pending_effects[0].kind)
        self.assertTrue(runtime.close(1))
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )

        self.assertEqual(("project-restart",), runtime.recover())
        self._wait_until(
            lambda: runtime.registry.require("project-restart").state.status
            is ProjectStatus.EXECUTING_STAGE
        )
        recovered = runtime.registry.require("project-restart")
        self.assertEqual((), recovered.state.pending_effects)
        self.assertIsNone(recovered.state.resume_status)
        self.assertEqual(
            "paused", self.client.native_goals[supervisor_id]["status"]
        )
        self.assertEqual(
            [{"threadId": supervisor_id, "includeTurns": False}],
            [
                params
                for method, params in self.client.requests
                if method == "thread/read"
            ],
        )
        self.assertTrue(runtime.close(1))

    def test_interrupted_restart_identity_failure_persists_needs_attention(self) -> None:
        runtime = bridge_main._build_project_runtime(
            client=self.client,
            events=self.events,
            project_root=self.root,
            selected_backend="hia_mcp_v2",
            server_transports=server_transports(),
            allowed_evidence_roots=(self.root / ".runtime",),
        )
        record = _record(
            "project-restart-fails",
            supervisor_thread_id="missing-after-transfer",
        )
        interrupted = replace(
            record.state,
            status=ProjectStatus.INTERRUPTED,
            resume_status=ProjectStatus.EXECUTING_STAGE,
            pending_effects=(),
            revision=record.state.revision + 1,
        )
        runtime.registry.put(
            ProjectRecord(interrupted, record.authoritative_task_text)
        )

        self.assertEqual(("project-restart-fails",), runtime.recover())
        self._wait_until(
            lambda: runtime.registry.require("project-restart-fails").state.status
            is ProjectStatus.NEEDS_ATTENTION
        )
        failed = runtime.registry.require("project-restart-fails")
        self.assertEqual((), failed.state.pending_effects)
        self.assertIn("Thread identity is missing", failed.state.last_error)
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, failed.state.resume_status)
        self.assertFalse(
            any(method == "thread/goal/set" for method, _ in self.client.requests)
        )
        self.assertTrue(runtime.close(1))

    def test_runtime_stop_interrupts_only_the_tracked_project_role_turn(self) -> None:
        with mock.patch.object(
            bridge_main, "ProjectEffectExecutor", _InterruptibleEffectExecutor
        ):
            runtime = bridge_main._build_project_runtime(
                client=self.client,
                events=self.events,
                project_root=self.root,
                selected_backend="hia_mcp_v2",
                server_transports=server_transports(),
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
