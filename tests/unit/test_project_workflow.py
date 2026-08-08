from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest

from services.bridge.hia_bridge.errors import BridgeError
from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    StageState,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import ProjectActionResult, ProjectRunner
from services.bridge.hia_bridge.project_workflow import ProjectWorkflowHost


def _record(project_id: str, status: ProjectStatus = ProjectStatus.PLANNING, *, stage: str | None = None):
    text = f"scene task {project_id}"
    task_id, digest = authoritative_task_identity(text)
    return ProjectRecord(
        ProjectState(
            project_id=project_id,
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
            roles={role: RoleThread(role, f"{project_id}-{role.value}") for role in Role},
            stage=StageState(stage_id=stage),
        ),
        text,
    )


class _Executor:
    def __init__(self, *, entered=None, release=None, event=None):
        self.entered = entered
        self.release = release
        self.event = event
        self.actions = []

    def execute(self, state, action):
        self.actions.append(action)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(1.0)
        return ProjectActionResult(state, self.event)


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_start_runs_only_an_explicit_process_local_action(self) -> None:
        self.registry.put(_record("p1"))
        runner = ProjectRunner(self.registry)
        executor = _Executor(event=LifecycleEvent(ProjectEvent.PROJECT_ANSWERED))
        host = ProjectWorkflowHost(
            registry=self.registry, runner=runner, executor_factory=lambda _: executor
        )
        try:
            self.assertFalse(host.start("p1"))
            runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
            self.assertTrue(host.start("p1"))
            self.assertTrue(_wait(lambda: self.registry.require("p1").state.status is ProjectStatus.COMPLETED))
            self.assertEqual(["start_supervisor"], [item.kind for item in executor.actions])
        finally:
            host.close()

    def test_terminal_workflow_publishes_one_exact_idle_callback(self) -> None:
        self.registry.put(_record("p1"))
        runner = ProjectRunner(self.registry)
        runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        idle: list[str] = []
        host = ProjectWorkflowHost(
            registry=self.registry,
            runner=runner,
            executor_factory=lambda _: _Executor(
                event=LifecycleEvent(ProjectEvent.PROJECT_ANSWERED)
            ),
            on_idle=idle.append,
        )
        try:
            self.assertTrue(host.start("p1"))
            self.assertTrue(_wait(lambda: idle == ["p1"]))
            self.assertFalse(host.is_inflight("p1"))
        finally:
            host.close()

    def test_stop_interrupts_once_and_marks_project_stopped_after_turn_returns(self) -> None:
        self.registry.put(_record("p1"))
        runner = ProjectRunner(self.registry)
        runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        entered = threading.Event()
        release = threading.Event()
        interrupted: list[str] = []
        host = ProjectWorkflowHost(
            registry=self.registry,
            runner=runner,
            executor_factory=lambda _: _Executor(entered=entered, release=release),
            interrupt_hook=interrupted.append,
        )
        try:
            self.assertTrue(host.start("p1"))
            self.assertTrue(entered.wait(0.5))
            self.assertTrue(host.stop("p1"))
            self.assertEqual(["p1"], interrupted)
            release.set()
            self.assertTrue(_wait(lambda: self.registry.require("p1").state.status is ProjectStatus.STOPPED))
        finally:
            release.set()
            host.close()

    def test_explicit_resume_continues_current_stage_without_recovery_replay(self) -> None:
        self.registry.put(_record("p1", ProjectStatus.STOPPED, stage="stage-current"))
        runner = ProjectRunner(self.registry)
        executor = _Executor()
        host = ProjectWorkflowHost(
            registry=self.registry, runner=runner, executor_factory=lambda _: executor
        )
        try:
            self.assertTrue(host.resume("p1", {"command": "start_execution"}))
            self.assertTrue(_wait(lambda: bool(executor.actions)))
            self.assertEqual("start_execution", executor.actions[0].kind)
            self.assertEqual({}, dict(executor.actions[0].data))
            self.assertEqual("stage-current", self.registry.require("p1").state.stage.stage_id)
        finally:
            host.close()

    def test_resume_while_stopped_turn_is_finishing_is_409_without_mutation(self) -> None:
        self.registry.put(_record("p1"))
        runner = ProjectRunner(self.registry)
        runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        entered = threading.Event()
        release = threading.Event()
        executor = _Executor(entered=entered, release=release)
        snapshots = []
        host = ProjectWorkflowHost(
            registry=self.registry,
            runner=runner,
            executor_factory=lambda _: executor,
            on_snapshot=snapshots.append,
        )
        try:
            self.assertTrue(host.start("p1"))
            self.assertTrue(entered.wait(0.5))
            self.assertTrue(host.stop("p1"))
            stopped = self.registry.require("p1")
            self.assertEqual(ProjectStatus.STOPPED, stopped.state.status)
            self.assertFalse(runner.has_pending("p1"))

            with self.assertRaises(BridgeError) as raised:
                host.resume("p1", {"command": "request_plan"})
            self.assertEqual("PROJECT_TURN_STILL_STOPPING", raised.exception.code)
            self.assertEqual(409, raised.exception.http_status)
            self.assertEqual(stopped, self.registry.require("p1"))
            self.assertFalse(runner.has_pending("p1"))

            release.set()
            self.assertTrue(_wait(lambda: not host.is_inflight("p1")))
            self.assertEqual(
                ProjectStatus.STOPPED,
                self.registry.require("p1").state.status,
            )
            self.assertEqual(ProjectStatus.STOPPED, snapshots[-1].state.status)
            self.assertTrue(
                host.resume("p1", {"command": "request_plan"})
            )
            self.assertTrue(_wait(lambda: len(executor.actions) == 2))
        finally:
            release.set()
            host.close()

    def test_bridge_restart_does_not_auto_resume_or_recreate_queue(self) -> None:
        self.registry.put(_record("p1", ProjectStatus.EXECUTING, stage="stage-1"))
        hia_writes = ["stage-1 completed before Bridge exit"]

        class _ReplayDetector:
            def execute(self, state, action):
                hia_writes.append(f"unexpected replay: {action.kind}")
                return ProjectActionResult(state, None)

        restarted_registry = ProjectRegistry(self.registry.path)
        restarted_runner = ProjectRunner(restarted_registry)
        host = ProjectWorkflowHost(
            registry=restarted_registry,
            runner=restarted_runner,
            executor_factory=lambda _: _ReplayDetector(),
        )
        try:
            self.assertEqual(ProjectStatus.STOPPED, restarted_registry.require("p1").state.status)
            self.assertFalse(restarted_runner.has_pending("p1"))
            self.assertFalse(host.start("p1"))
            self.assertEqual(["stage-1 completed before Bridge exit"], hia_writes)
        finally:
            host.close()


def _wait(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


if __name__ == "__main__":
    unittest.main()
