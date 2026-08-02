from __future__ import annotations

from dataclasses import replace
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    RuntimeBudget,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_lifecycle import (
    InvalidTransition,
    LifecycleEvent,
    ProjectCommand,
    ProjectEvent,
    reduce_project,
)


def _state(status: ProjectStatus = ProjectStatus.PROVISIONING) -> ProjectState:
    task_id, digest = authoritative_task_identity("build a Houdini cabin")
    return ProjectState(
        project_id="project-1",
        goal_thread_id="thread-supervisor",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        status=status,
    )


class ProjectContractTests(unittest.TestCase):
    def test_authoritative_task_identity_is_stable(self) -> None:
        self.assertEqual(
            authoritative_task_identity("same task"),
            authoritative_task_identity("same task"),
        )

    def test_budget_rejects_non_positive_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_project_turns"):
            RuntimeBudget(max_project_turns=0)


class ProjectLifecycleTests(unittest.TestCase):
    def apply(self, state: ProjectState, kind: ProjectEvent, **data: object):
        return reduce_project(state, LifecycleEvent(kind, data))

    def test_happy_path_and_final_completion(self) -> None:
        state = _state()
        expected = (
            (ProjectEvent.INTAKE_STARTED, ProjectStatus.INTAKE),
            (ProjectEvent.SCENE_ELIGIBLE, ProjectStatus.PROVISIONING_ROLES),
            (ProjectEvent.ROLES_PROVISIONED, ProjectStatus.PLANNING),
            (ProjectEvent.PLAN_READY, ProjectStatus.AUTHORIZATION),
            (ProjectEvent.PLAN_AUTHORIZED, ProjectStatus.EXECUTING_STAGE),
            (ProjectEvent.STAGE_EXECUTED, ProjectStatus.REVIEWING_STAGE),
        )
        for event, status in expected:
            state, _ = self.apply(state, event)
            self.assertEqual(status, state.status)
        state, commands = self.apply(state, ProjectEvent.REVIEWS_PASSED, final_stage=True)
        self.assertEqual(ProjectStatus.COMPLETING, state.status)
        self.assertEqual([ProjectCommand.COMPLETE_GOAL], [item.kind for item in commands])
        state, commands = self.apply(state, ProjectEvent.GOAL_COMPLETED)
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        self.assertEqual((), commands)

    def test_review_repair_cycle(self) -> None:
        state = _state(ProjectStatus.REVIEWING_STAGE)
        state, commands = self.apply(state, ProjectEvent.REVIEWS_FAILED)
        self.assertEqual(ProjectStatus.REPAIRING_STAGE, state.status)
        self.assertEqual(ProjectCommand.REQUEST_REPAIR, commands[0].kind)
        state, commands = self.apply(state, ProjectEvent.REPAIR_READY)
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, state.status)
        self.assertTrue(commands[0].data["repair"])

    def test_interruption_and_exact_restart(self) -> None:
        state = _state(ProjectStatus.EXECUTING_STAGE)
        state, commands = self.apply(
            state, ProjectEvent.PROJECT_INTERRUPTED, reason="bridge_restart"
        )
        self.assertEqual(ProjectStatus.PAUSING, state.status)
        self.assertEqual(ProjectCommand.PAUSE_GOAL, commands[0].kind)
        state, _ = self.apply(state, ProjectEvent.GOAL_PAUSED)
        self.assertEqual(ProjectStatus.INTERRUPTED, state.status)
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, state.resume_status)
        state, commands = self.apply(state, ProjectEvent.RESTART_REQUESTED)
        self.assertEqual(ProjectStatus.INTERRUPTED, state.status)
        self.assertEqual(ProjectCommand.VERIFY_RECOVERY, commands[0].kind)
        state, commands = self.apply(state, ProjectEvent.RECOVERY_VALIDATED)
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, state.status)
        self.assertEqual(ProjectCommand.RESUME_GOAL, commands[0].kind)

    def test_budget_and_no_progress_need_attention_without_auto_pass(self) -> None:
        for event in (ProjectEvent.BUDGET_EXHAUSTED, ProjectEvent.NO_PROGRESS):
            with self.subTest(event=event):
                state, commands = self.apply(
                    _state(ProjectStatus.REPAIRING_STAGE), event, reason="limit"
                )
                self.assertEqual(ProjectStatus.PAUSING, state.status)
                self.assertEqual(ProjectCommand.PAUSE_GOAL, commands[0].kind)
                state, commands = self.apply(state, ProjectEvent.GOAL_PAUSED)
                self.assertEqual(ProjectStatus.NEEDS_ATTENTION, state.status)
                self.assertNotIn(
                    ProjectCommand.COMPLETE_GOAL, [item.kind for item in commands]
                )
                state, _ = self.apply(state, ProjectEvent.USER_CONTINUE)
                self.assertEqual(ProjectStatus.REPAIRING_STAGE, state.status)

    def test_ineligible_and_unclear_do_not_provision_workers(self) -> None:
        for event, expected in (
            (ProjectEvent.SCENE_INELIGIBLE, ProjectStatus.NOT_APPLICABLE),
            (ProjectEvent.INTAKE_UNCLEAR, ProjectStatus.NEEDS_ATTENTION),
        ):
            with self.subTest(event=event):
                state, commands = self.apply(_state(ProjectStatus.INTAKE), event)
                self.assertEqual(ProjectStatus.PAUSING, state.status)
                state, pause_commands = self.apply(state, ProjectEvent.GOAL_PAUSED)
                self.assertEqual(expected, state.status)
                self.assertNotIn(
                    ProjectCommand.PROVISION_WORKERS,
                    [item.kind for item in (*commands, *pause_commands)],
                )

    def test_worker_provisioning_failure_needs_attention(self) -> None:
        state, _ = self.apply(_state(ProjectStatus.INTAKE), ProjectEvent.SCENE_ELIGIBLE)
        state, commands = self.apply(
            state, ProjectEvent.ROLE_PROVISIONING_FAILED, error="thread/start failed"
        )
        self.assertEqual(ProjectStatus.PAUSING, state.status)
        state, followup = self.apply(state, ProjectEvent.GOAL_PAUSED)
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, state.status)
        self.assertNotIn(
            ProjectCommand.REQUEST_PLAN,
            [item.kind for item in (*commands, *followup)],
        )

    def test_goal_completion_failure_never_claims_completed(self) -> None:
        state, _ = self.apply(
            _state(ProjectStatus.REVIEWING_STAGE),
            ProjectEvent.REVIEWS_PASSED,
            final_stage=True,
        )
        state, _ = self.apply(
            state, ProjectEvent.GOAL_COMPLETION_FAILED, error="goal/update failed"
        )
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, state.status)

    def test_failure_is_terminal(self) -> None:
        state, _ = self.apply(
            _state(ProjectStatus.PLANNING), ProjectEvent.PROJECT_FAILED, error="rpc"
        )
        self.assertEqual(ProjectStatus.FAILED, state.status)
        with self.assertRaises(InvalidTransition):
            self.apply(state, ProjectEvent.RESTART_REQUESTED)

    def test_every_event_rejects_an_unrelated_status(self) -> None:
        completed = _state(ProjectStatus.COMPLETED)
        for event in ProjectEvent:
            with self.subTest(event=event):
                with self.assertRaises(InvalidTransition):
                    self.apply(completed, event)

    def test_restart_without_resume_status_is_illegal(self) -> None:
        state = replace(_state(ProjectStatus.INTERRUPTED), resume_status=None)
        state, _ = self.apply(state, ProjectEvent.RESTART_REQUESTED)
        with self.assertRaises(InvalidTransition):
            self.apply(state, ProjectEvent.RECOVERY_VALIDATED)

    def test_pause_failure_never_claims_interrupted_or_stopped(self) -> None:
        state, _ = self.apply(
            _state(ProjectStatus.EXECUTING_STAGE),
            ProjectEvent.PROJECT_INTERRUPTED,
            reason="user_stop",
        )
        state, commands = self.apply(
            state,
            ProjectEvent.GOAL_PAUSE_FAILED,
            error="goal/update failed",
        )
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, state.status)
        self.assertEqual("goal/update failed", state.last_error)
        self.assertEqual(ProjectCommand.SHOW_ATTENTION, commands[0].kind)


if __name__ == "__main__":
    unittest.main()
