from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest

from services.bridge.hia_bridge.project_contracts import (
    PendingEffect,
    ProjectState,
    ProjectStatus,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_effects import EffectResult
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import ProjectRunner
from services.bridge.hia_bridge.project_workflow import ProjectWorkflowHost


def _record(project_id: str, effect_count: int = 1) -> ProjectRecord:
    text = f"task for {project_id}"
    task_id, digest = authoritative_task_identity(text)
    effects = tuple(
        PendingEffect(f"{project_id}-effect-{index}", f"effect-{index}")
        for index in range(effect_count)
    )
    return ProjectRecord(
        ProjectState(
            project_id=project_id,
            goal_thread_id=f"{project_id}-supervisor",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=ProjectStatus.EXECUTING_STAGE,
            pending_effects=effects,
        ),
        text,
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.effects: list[tuple[str, str]] = []

    def execute(self, state, effect):
        with self.lock:
            self.effects.append((state.project_id, effect.effect_id))
        if effect.kind == "pause_goal":
            return EffectResult(state, LifecycleEvent(ProjectEvent.GOAL_PAUSED))
        if effect.kind == "resume_goal":
            return EffectResult(state, LifecycleEvent(ProjectEvent.GOAL_RESUMED))
        return EffectResult(state, None)


class BlockingExecutor(RecordingExecutor):
    def __init__(self, expected: int = 1) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.all_entered = threading.Event()
        self.release = threading.Event()
        self.expected = expected

    def execute(self, state, effect):
        with self.lock:
            self.effects.append((state.project_id, effect.effect_id))
            if len(self.effects) >= self.expected:
                self.all_entered.set()
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("test release was not signalled")
        if effect.kind == "pause_goal":
            return EffectResult(state, LifecycleEvent(ProjectEvent.GOAL_PAUSED))
        if effect.kind == "resume_goal":
            return EffectResult(state, LifecycleEvent(ProjectEvent.GOAL_RESUMED))
        return EffectResult(state, None)


class FailingExecutor:
    def execute(self, state, effect):
        raise RuntimeError("external rpc failed")


class ProjectWorkflowHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")
        self.runner = ProjectRunner(self.registry)
        self.hosts: list[ProjectWorkflowHost] = []

    def tearDown(self) -> None:
        for host in self.hosts:
            host.close(0.1)
        self.temp.cleanup()

    def _host(self, executor, **kwargs) -> ProjectWorkflowHost:
        host = ProjectWorkflowHost(
            registry=self.registry,
            runner=self.runner,
            executor_factory=lambda _project_id: executor,
            **kwargs,
        )
        self.hosts.append(host)
        return host

    def _wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition was not reached")

    def test_chains_every_persisted_effect_in_order(self) -> None:
        self.registry.put(_record("p1", 4))
        executor = RecordingExecutor()
        snapshots = []
        host = self._host(executor, on_snapshot=snapshots.append)

        self.assertTrue(host.start("p1"))
        self._wait_until(lambda: not self.registry.require("p1").state.pending_effects)

        self.assertEqual(
            [("p1", f"p1-effect-{index}") for index in range(4)],
            executor.effects,
        )
        self.assertEqual(4, len(snapshots))

    def test_same_project_start_is_deduplicated(self) -> None:
        self.registry.put(_record("p1", 2))
        executor = BlockingExecutor()
        host = self._host(executor)

        self.assertTrue(host.start("p1"))
        self.assertTrue(executor.entered.wait(1))
        self.assertFalse(host.start("p1"))
        self.assertEqual(1, len(executor.effects))
        executor.release.set()
        self._wait_until(lambda: not self.registry.require("p1").state.pending_effects)

    def test_different_projects_can_execute_in_parallel(self) -> None:
        self.registry.put(_record("p1"))
        self.registry.put(_record("p2"))
        executor = BlockingExecutor(expected=2)
        host = self._host(executor, max_workers=2)

        self.assertTrue(host.start("p1"))
        self.assertTrue(host.start("p2"))
        self.assertTrue(executor.all_entered.wait(1))
        self.assertEqual({"p1", "p2"}, {project for project, _ in executor.effects})
        executor.release.set()
        self._wait_until(
            lambda: not host.is_inflight("p1") and not host.is_inflight("p2")
        )

    def test_stop_interrupts_current_rpc_and_does_not_start_next_effect(self) -> None:
        self.registry.put(_record("p1", 2))
        executor = BlockingExecutor()
        interrupted: list[str] = []
        host = self._host(executor, interrupt_hook=interrupted.append)

        host.start("p1")
        self.assertTrue(executor.entered.wait(1))
        self.assertTrue(host.stop("p1"))
        self.assertEqual(["p1"], interrupted)
        executor.release.set()
        self._wait_until(lambda: not host.is_inflight("p1"))

        self._wait_until(
            lambda: self.registry.require("p1").state.status
            is ProjectStatus.INTERRUPTED
        )
        record = self.registry.require("p1")
        self.assertEqual((), record.state.pending_effects)
        self.assertEqual(2, len(executor.effects))
        self.assertNotIn(("p1", "p1-effect-1"), executor.effects)

    def test_idle_stop_replaces_unstarted_work_with_confirmed_goal_pause(self) -> None:
        self.registry.put(_record("p1", 2))
        executor = RecordingExecutor()
        host = self._host(executor)

        self.assertFalse(host.stop("p1"))
        self._wait_until(
            lambda: self.registry.require("p1").state.status
            is ProjectStatus.INTERRUPTED
        )

        self.assertNotIn(("p1", "p1-effect-0"), executor.effects)
        self.assertNotIn(("p1", "p1-effect-1"), executor.effects)
        self.assertEqual(1, len(executor.effects))

    def test_executor_exception_is_persisted_as_project_failure(self) -> None:
        self.registry.put(_record("p1"))
        snapshots = []
        host = self._host(FailingExecutor(), on_snapshot=snapshots.append)

        host.start("p1")
        self._wait_until(lambda: self.registry.require("p1").state.status is ProjectStatus.FAILED)

        record = self.registry.require("p1")
        self.assertIn("RuntimeError: external rpc failed", record.state.last_error)
        self.assertEqual("record_failure", record.state.pending_effects[0].kind)
        self.assertIsNone(host.host_error("p1"))
        self.assertEqual(ProjectStatus.FAILED, snapshots[-1].state.status)

    def test_recover_only_schedules_nonterminal_projects_with_pending_work(self) -> None:
        self.registry.put(_record("active"))
        completed = _record("completed")
        completed = ProjectRecord(
            replace(completed.state, status=ProjectStatus.COMPLETED),
            completed.authoritative_task_text,
        )
        self.registry.put(completed)
        empty = _record("empty")
        empty = ProjectRecord(
            replace(empty.state, pending_effects=()), empty.authoritative_task_text
        )
        self.registry.put(empty)
        executor = RecordingExecutor()
        host = self._host(executor)

        self.assertEqual(("active",), host.recover())
        self._wait_until(lambda: not self.registry.require("active").state.pending_effects)
        self.assertEqual([("active", "active-effect-0")], executor.effects)

    def test_resuming_project_becomes_active_only_after_resume_effect_ack(self) -> None:
        record = _record("p1")
        record = ProjectRecord(
            replace(
                record.state,
                status=ProjectStatus.RESUMING,
                resume_status=ProjectStatus.EXECUTING_STAGE,
                pending_effects=(PendingEffect("resume-effect", "resume_goal"),),
            ),
            record.authoritative_task_text,
        )
        self.registry.put(record)
        executor = BlockingExecutor()
        host = self._host(executor)

        self.assertTrue(host.start("p1"))
        self.assertTrue(executor.entered.wait(1))
        self.assertEqual(
            ProjectStatus.RESUMING,
            self.registry.require("p1").state.status,
        )
        executor.release.set()
        self._wait_until(
            lambda: self.registry.require("p1").state.status
            is ProjectStatus.EXECUTING_STAGE
        )
        self.assertEqual([("p1", "resume-effect")], executor.effects)

    def test_close_is_bounded_and_does_not_claim_running_rpc_was_cancelled(self) -> None:
        self.registry.put(_record("p1"))
        executor = BlockingExecutor()
        interrupted: list[str] = []
        host = self._host(executor, interrupt_hook=interrupted.append)
        host.start("p1")
        self.assertTrue(executor.entered.wait(1))

        started = time.monotonic()
        self.assertFalse(host.close(0.02))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(["p1"], interrupted)
        self.assertTrue(host.is_inflight("p1"))
        executor.release.set()
        self._wait_until(lambda: not host.is_inflight("p1"))

    def test_close_records_interrupt_failure_and_still_bounds_pool_shutdown(self) -> None:
        self.registry.put(_record("p1"))
        executor = BlockingExecutor()

        def fail_interrupt(_project_id: str) -> None:
            raise RuntimeError("interrupt rpc unavailable")

        host = self._host(executor, interrupt_hook=fail_interrupt)
        host.start("p1")
        self.assertTrue(executor.entered.wait(1))

        self.assertFalse(host.close(0.02))
        self.assertRegex(str(host.host_error("p1")), "interrupt rpc unavailable")
        executor.release.set()
        self._wait_until(lambda: not host.is_inflight("p1"))


if __name__ == "__main__":
    unittest.main()
