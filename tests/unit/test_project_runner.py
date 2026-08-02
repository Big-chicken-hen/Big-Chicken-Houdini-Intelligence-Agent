from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import ProjectRunner


def _record(status: ProjectStatus) -> ProjectRecord:
    text = "build a Houdini scene"
    task_id, digest = authoritative_task_identity(text)
    return ProjectRecord(
        ProjectState(
            project_id="p1",
            goal_thread_id="supervisor",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
        ),
        text,
    )


class FakeExecutor:
    def __init__(self, event: ProjectEvent) -> None:
        self.event = event
        self.effects = []

    def execute(self, state, effect):
        self.effects.append(effect)
        return LifecycleEvent(self.event)


class ProjectRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")
        self.runner = ProjectRunner(self.registry)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_role_provisioning_is_persisted_before_rpc_and_plan_waits_for_ack(self) -> None:
        self.registry.put(_record(ProjectStatus.INTAKE))
        pending = self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.SCENE_ELIGIBLE))
        self.assertEqual(ProjectStatus.PROVISIONING_ROLES, pending.state.status)
        self.assertEqual("provision_workers", pending.state.pending_effects[0].kind)
        executor = FakeExecutor(ProjectEvent.ROLES_PROVISIONED)
        updated = self.runner.execute_next("p1", executor)
        self.assertEqual(ProjectStatus.PLANNING, updated.state.status)
        self.assertEqual("request_plan", updated.state.pending_effects[0].kind)
        self.assertEqual(1, len(executor.effects))

    def test_final_review_never_completes_before_goal_ack(self) -> None:
        self.registry.put(_record(ProjectStatus.REVIEWING_STAGE))
        pending = self.runner.dispatch(
            "p1", LifecycleEvent(ProjectEvent.REVIEWS_PASSED, {"final_stage": True})
        )
        self.assertEqual(ProjectStatus.COMPLETING, pending.state.status)
        self.assertEqual("complete_goal", pending.state.pending_effects[0].kind)
        updated = self.runner.execute_next("p1", FakeExecutor(ProjectEvent.GOAL_COMPLETED))
        self.assertEqual(ProjectStatus.COMPLETED, updated.state.status)
        self.assertEqual((), updated.state.pending_effects)

    def test_goal_completion_failure_needs_attention(self) -> None:
        self.registry.put(_record(ProjectStatus.REVIEWING_STAGE))
        pending = self.runner.dispatch(
            "p1", LifecycleEvent(ProjectEvent.REVIEWS_PASSED, {"final_stage": True})
        )
        effect = pending.state.pending_effects[0]
        updated = self.runner.acknowledge(
            "p1",
            effect.effect_id,
            LifecycleEvent(ProjectEvent.GOAL_COMPLETION_FAILED, {"error": "rpc"}),
        )
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, updated.state.status)

    def test_wrong_or_duplicate_effect_ack_is_rejected(self) -> None:
        self.registry.put(_record(ProjectStatus.INTAKE))
        pending = self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.SCENE_ELIGIBLE))
        effect = pending.state.pending_effects[0]
        with self.assertRaisesRegex(ValueError, "oldest"):
            self.runner.acknowledge(
                "p1", "wrong", LifecycleEvent(ProjectEvent.ROLES_PROVISIONED)
            )
        self.runner.acknowledge(
            "p1", effect.effect_id, LifecycleEvent(ProjectEvent.ROLES_PROVISIONED)
        )
        with self.assertRaisesRegex(ValueError, "oldest"):
            self.runner.acknowledge(
                "p1", effect.effect_id, LifecycleEvent(ProjectEvent.ROLES_PROVISIONED)
            )

    def test_new_event_cannot_bypass_pending_effect(self) -> None:
        self.registry.put(_record(ProjectStatus.INTAKE))
        self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.SCENE_ELIGIBLE))
        with self.assertRaisesRegex(ValueError, "pending"):
            self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.PROJECT_FAILED))


if __name__ == "__main__":
    unittest.main()
