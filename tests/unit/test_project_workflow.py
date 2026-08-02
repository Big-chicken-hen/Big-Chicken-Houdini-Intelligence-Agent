from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest

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

    def test_bridge_restart_does_not_auto_resume_or_recreate_queue(self) -> None:
        self.registry.put(_record("p1", ProjectStatus.EXECUTING, stage="stage-1"))
        restarted_registry = ProjectRegistry(self.registry.path)
        restarted_runner = ProjectRunner(restarted_registry)
        host = ProjectWorkflowHost(
            registry=restarted_registry,
            runner=restarted_runner,
            executor_factory=lambda _: _Executor(),
        )
        try:
            self.assertEqual(ProjectStatus.STOPPED, restarted_registry.require("p1").state.status)
            self.assertFalse(restarted_runner.has_pending("p1"))
            self.assertFalse(host.start("p1"))
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
