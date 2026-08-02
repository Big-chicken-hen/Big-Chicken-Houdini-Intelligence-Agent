from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    StageState,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_guidance import publish_guidance
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import (
    ProjectAction,
    ProjectActionResult,
    ProjectRunner,
)


def _record(status: ProjectStatus = ProjectStatus.PLANNING) -> ProjectRecord:
    text = "build a Houdini scene"
    task_id, digest = authoritative_task_identity(text)
    return ProjectRecord(
        ProjectState(
            project_id="p1",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
            roles={role: RoleThread(role, f"thread-{role.value}") for role in Role},
        ),
        text,
    )


class _Executor:
    def __init__(self, event: LifecycleEvent | None, mutate=None) -> None:
        self.event = event
        self.mutate = mutate
        self.actions: list[ProjectAction] = []

    def execute(self, state: ProjectState, action: ProjectAction) -> ProjectActionResult:
        self.actions.append(action)
        if self.mutate is not None:
            state = self.mutate(state)
        return ProjectActionResult(state, self.event)


class ProjectRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")
        self.registry.put(_record())
        self.runner = ProjectRunner(self.registry)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_actions_are_process_local_and_restart_does_not_replay(self) -> None:
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        self.assertTrue(self.runner.has_pending("p1"))
        restarted = ProjectRunner(ProjectRegistry(self.registry.path))
        self.assertFalse(restarted.has_pending("p1"))
        self.assertEqual(ProjectStatus.STOPPED, restarted._registry.require("p1").state.status)

    def test_one_action_must_finish_before_the_next_lifecycle_step(self) -> None:
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        with self.assertRaisesRegex(ValueError, "in progress"):
            self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_ACCEPTED))
        executor = _Executor(LifecycleEvent(ProjectEvent.PROJECT_ACCEPTED))
        updated = self.runner.execute_next("p1", executor)
        self.assertEqual(["start_supervisor"], [item.kind for item in executor.actions])
        self.assertEqual("request_plan", self.runner.next_action("p1").kind)
        self.assertEqual(ProjectStatus.PLANNING, updated.state.status)

    def test_repair_event_queues_execution_directly(self) -> None:
        reviewing = replace(_record(ProjectStatus.REVIEWING).state, revision=1)
        self.registry.put(ProjectRecord(reviewing, "build a Houdini scene"), expected_revision=0)
        updated = self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.REVIEWS_FAILED))
        action = self.runner.next_action("p1")
        self.assertEqual(ProjectStatus.EXECUTING, updated.state.status)
        self.assertEqual("start_execution", action.kind)
        self.assertEqual({"repair": True}, dict(action.data))

    def test_concurrent_guidance_revision_is_merged_after_role_turn(self) -> None:
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))

        def mutate(state: ProjectState) -> ProjectState:
            current = self.registry.require("p1")
            guided = publish_guidance(current.state, "new native guidance")
            self.registry.put(ProjectRecord(guided, current.authoritative_task_text), expected_revision=current.state.revision)
            return replace(state, stage=StageState(stage_id="stage-from-role"))

        updated = self.runner.execute_next(
            "p1", _Executor(LifecycleEvent(ProjectEvent.PROJECT_ACCEPTED), mutate)
        )
        self.assertEqual(1, updated.state.guidance_revision)
        self.assertEqual("stage-from-role", updated.state.stage.stage_id)

    def test_role_action_cannot_change_identity_phase_or_thread_binding(self) -> None:
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        bad = _Executor(
            None,
            lambda state: replace(state, status=ProjectStatus.EXECUTING),
        )
        with self.assertRaisesRegex(ValueError, "identity or phase"):
            self.runner.execute_next("p1", bad)

    def test_fail_clears_queue_and_marks_failed(self) -> None:
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_STARTED))
        failed = self.runner.fail("p1", RuntimeError("rpc failed"))
        self.assertEqual(ProjectStatus.FAILED, failed.state.status)
        self.assertFalse(self.runner.has_pending("p1"))
        self.assertIn("rpc failed", failed.state.last_error)


if __name__ == "__main__":
    unittest.main()
