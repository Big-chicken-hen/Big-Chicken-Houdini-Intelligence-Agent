from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import (
    PendingEffect,
    ProjectState,
    ProjectStatus,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_effects import EffectResult
from services.bridge.hia_bridge.project_guidance import publish_guidance
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
        return EffectResult(state, LifecycleEvent(self.event))


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

    def test_continue_persists_resuming_until_goal_resume_effect_ack(self) -> None:
        record = _record(ProjectStatus.NEEDS_ATTENTION)
        record = ProjectRecord(
            replace(record.state, resume_status=ProjectStatus.PLANNING),
            record.authoritative_task_text,
        )
        self.registry.put(record)

        pending = self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.USER_CONTINUE))
        self.assertEqual(ProjectStatus.RESUMING, pending.state.status)
        self.assertEqual("resume_goal", pending.state.pending_effects[0].kind)

        updated = self.runner.execute_next("p1", FakeExecutor(ProjectEvent.GOAL_RESUMED))
        self.assertEqual(ProjectStatus.PLANNING, updated.state.status)
        self.assertIsNone(updated.state.resume_status)
        self.assertEqual(1, len(updated.state.pending_effects))
        self.assertEqual("request_plan", updated.state.pending_effects[0].kind)
        self.assertEqual(
            {"recovery": True}, dict(updated.state.pending_effects[0].data)
        )

    def test_persist_recovery_failure_holds_original_effect_for_revalidation(self) -> None:
        original = PendingEffect("original", "start_execution", {"repair": True})
        record = _record(ProjectStatus.EXECUTING_STAGE)
        record = ProjectRecord(
            replace(
                record.state,
                resume_status=ProjectStatus.AUTHORIZATION,
                pending_effects=(original,),
            ),
            record.authoritative_task_text,
        )
        self.registry.put(record)

        updated = self.runner.persist_recovery_failure("p1", "identity missing")
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, updated.state.status)
        self.assertEqual("identity missing", updated.state.attention_reason)
        self.assertEqual(ProjectStatus.AUTHORIZATION, updated.state.resume_status)
        self.assertEqual((original,), updated.state.recovery_pending_effects)
        self.assertEqual((), updated.state.pending_effects)

        reloaded = self.registry.require("p1")
        self.assertTrue(reloaded.state.recovery_required)
        self.assertEqual((original,), reloaded.state.recovery_pending_effects)

    def test_resume_effect_failure_is_persisted_as_needs_attention(self) -> None:
        record = _record(ProjectStatus.NEEDS_ATTENTION)
        record = ProjectRecord(
            replace(record.state, resume_status=ProjectStatus.AUTHORIZATION),
            record.authoritative_task_text,
        )
        self.registry.put(record)
        pending = self.runner.dispatch("p1", LifecycleEvent(ProjectEvent.USER_CONTINUE))
        effect = pending.state.pending_effects[0]

        updated = self.runner.acknowledge(
            "p1",
            effect.effect_id,
            LifecycleEvent(
                ProjectEvent.GOAL_RESUME_FAILED,
                {"error": "native goal stayed paused"},
            ),
        )
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, updated.state.status)
        self.assertEqual(ProjectStatus.AUTHORIZATION, updated.state.resume_status)
        self.assertEqual("native goal stayed paused", updated.state.last_error)

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

    def test_explicit_idle_stop_cancels_pending_work_and_requires_pause_ack(self) -> None:
        record = _record(ProjectStatus.EXECUTING_STAGE)
        record = ProjectRecord(
            replace(
                record.state,
                pending_effects=(PendingEffect("write-next", "start_execution"),),
            ),
            record.authoritative_task_text,
        )
        self.registry.put(record)
        stopped = self.runner.cancel_pending_and_dispatch(
            "p1",
            LifecycleEvent(ProjectEvent.PROJECT_INTERRUPTED, {"reason": "user_stop"}),
        )
        self.assertEqual(ProjectStatus.PAUSING, stopped.state.status)
        self.assertEqual("pause_goal", stopped.state.pending_effects[0].kind)

    def test_state_only_effect_ack_persists_stage_advance_without_auto_pass(self) -> None:
        record = _record(ProjectStatus.EXECUTING_STAGE)
        effect = PendingEffect("effect-advance", "advance_stage", {})
        record = ProjectRecord(
            replace(record.state, pending_effects=(effect,)),
            record.authoritative_task_text,
        )
        self.registry.put(record)
        advanced = replace(
            record.state,
            stage=replace(record.state.stage, stage_id="stage-2", ordinal=2),
            revision=record.state.revision + 1,
        )
        updated = self.runner.acknowledge_effect(
            "p1", effect.effect_id, EffectResult(advanced, None)
        )
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, updated.state.status)
        self.assertEqual("stage-2", updated.state.stage.stage_id)
        self.assertEqual((), updated.state.pending_effects)

    def test_effect_ack_preserves_guidance_added_while_rpc_was_running(self) -> None:
        record = _record(ProjectStatus.EXECUTING_STAGE)
        effect = PendingEffect("effect-long-rpc", "advance_stage", {})
        started = replace(record.state, pending_effects=(effect,))
        self.registry.put(ProjectRecord(started, record.authoritative_task_text))
        outcome = replace(
            started,
            stage=replace(started.stage, stage_id="stage-2", ordinal=2),
            revision=started.revision + 1,
        )

        latest = publish_guidance(started, "remove the optional antenna")
        self.registry.put(
            ProjectRecord(latest, record.authoritative_task_text),
            expected_revision=started.revision,
        )
        updated = self.runner.acknowledge_effect(
            "p1", effect.effect_id, EffectResult(outcome, None)
        )

        self.assertEqual("stage-2", updated.state.stage.stage_id)
        self.assertEqual(
            ["remove the optional antenna"],
            [item.text for item in updated.state.guidance],
        )

    def test_material_delta_at_effect_boundary_discards_old_execution_and_replans(self) -> None:
        record = _record(ProjectStatus.EXECUTING_STAGE)
        effect = PendingEffect("old-execution", "start_execution", {})
        state = replace(
            record.state,
            pending_effects=(effect, PendingEffect("old-review", "start_reviews", {})),
            plan_stale=True,
            blueprint_revision=1,
            authorized_blueprint_revision=1,
        )
        self.registry.put(ProjectRecord(state, record.authoritative_task_text))
        updated = self.runner.acknowledge_effect(
            "p1",
            effect.effect_id,
            EffectResult(state, LifecycleEvent(ProjectEvent.STAGE_EXECUTED)),
        )
        self.assertEqual(ProjectStatus.PLANNING, updated.state.status)
        self.assertEqual(["request_plan"], [item.kind for item in updated.state.pending_effects])
        self.assertTrue(updated.state.plan_stale)


if __name__ == "__main__":
    unittest.main()
