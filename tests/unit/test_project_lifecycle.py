from __future__ import annotations

from dataclasses import replace
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    RuntimeBudget,
    StageState,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_lifecycle import (
    InvalidTransition,
    LifecycleEvent,
    ProjectCommand,
    ProjectEvent,
    reduce_project,
)


def _state(status: ProjectStatus = ProjectStatus.PLANNING) -> ProjectState:
    task_id, digest = authoritative_task_identity("build a Houdini cabin")
    return ProjectState(
        project_id="project-1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        status=status,
    )


class ProjectContractTests(unittest.TestCase):
    def test_project_status_is_exactly_the_seven_public_states(self) -> None:
        self.assertEqual(
            {
                "planning",
                "executing",
                "reviewing",
                "waiting_user",
                "completed",
                "failed",
                "stopped",
            },
            {status.value for status in ProjectStatus},
        )

    def test_authoritative_task_identity_is_stable(self) -> None:
        self.assertEqual(authoritative_task_identity("same task"), authoritative_task_identity("same task"))

    def test_budget_rejects_non_positive_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_project_turns"):
            RuntimeBudget(max_project_turns=0)


class ProjectLifecycleTests(unittest.TestCase):
    def apply(self, state: ProjectState, kind: ProjectEvent, **data: object):
        return reduce_project(state, LifecycleEvent(kind, data))

    def test_scene_flow_has_one_authorization_then_execution_and_reviews(self) -> None:
        state, commands = self.apply(_state(), ProjectEvent.PROJECT_STARTED)
        self.assertEqual([ProjectCommand.START_SUPERVISOR], [item.kind for item in commands])

        state, commands = self.apply(state, ProjectEvent.PROJECT_ACCEPTED)
        self.assertEqual([ProjectCommand.REQUEST_PLAN], [item.kind for item in commands])

        state, commands = self.apply(state, ProjectEvent.PLAN_READY)
        self.assertEqual([ProjectCommand.REQUEST_AUTHORIZATION], [item.kind for item in commands])

        state, commands = self.apply(state, ProjectEvent.PLAN_AUTHORIZED)
        self.assertEqual(ProjectStatus.EXECUTING, state.status)
        self.assertEqual([ProjectCommand.START_EXECUTION], [item.kind for item in commands])

        state, commands = self.apply(state, ProjectEvent.STAGE_EXECUTED)
        self.assertEqual(ProjectStatus.REVIEWING, state.status)
        self.assertEqual([ProjectCommand.START_REVIEWS], [item.kind for item in commands])

    def test_non_scene_supervisor_answer_completes_without_workers_or_goal(self) -> None:
        state, _ = self.apply(_state(), ProjectEvent.PROJECT_STARTED)
        state, commands = self.apply(state, ProjectEvent.PROJECT_ANSWERED)
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        self.assertEqual((), commands)

    def test_failed_review_sends_repair_directly_to_execution(self) -> None:
        state, commands = self.apply(_state(ProjectStatus.REVIEWING), ProjectEvent.REVIEWS_FAILED)
        self.assertEqual(ProjectStatus.EXECUTING, state.status)
        self.assertEqual([ProjectCommand.START_EXECUTION], [item.kind for item in commands])
        self.assertEqual({"repair": True}, dict(commands[0].data or {}))
        self.assertFalse(any("author" in item.kind.value for item in commands))

    def test_passing_nonfinal_stage_advances_directly(self) -> None:
        initial = replace(
            _state(ProjectStatus.REVIEWING),
            stage=StageState(stage_id="stage-1", ordinal=1, repair_count=4),
        )
        state, commands = self.apply(
            initial,
            ProjectEvent.REVIEWS_PASSED,
            final_stage=False,
            next_stage_id="stage-2",
        )
        self.assertEqual(ProjectStatus.EXECUTING, state.status)
        self.assertEqual("stage-2", state.stage.stage_id)
        self.assertEqual(2, state.stage.ordinal)
        self.assertEqual(0, state.stage.repair_count)
        self.assertEqual([ProjectCommand.START_EXECUTION], [item.kind for item in commands])

    def test_passing_final_stage_completes_without_goal_command(self) -> None:
        state, commands = self.apply(
            _state(ProjectStatus.REVIEWING), ProjectEvent.REVIEWS_PASSED, final_stage=True
        )
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        self.assertEqual((), commands)
        self.assertFalse(any("goal" in item.kind.value for item in commands))

    def test_stop_requires_explicit_continue_and_resumes_current_stage(self) -> None:
        active = replace(
            _state(ProjectStatus.EXECUTING),
            stage=StageState(stage_id="stage-current", ordinal=3),
        )
        stopped, commands = self.apply(active, ProjectEvent.PROJECT_INTERRUPTED)
        self.assertEqual(ProjectStatus.STOPPED, stopped.status)
        self.assertEqual((), commands)
        with self.assertRaises(InvalidTransition):
            self.apply(stopped, ProjectEvent.PROJECT_STARTED)

        resumed, commands = self.apply(
            stopped,
            ProjectEvent.USER_CONTINUE,
            command="start_execution",
        )
        self.assertEqual(ProjectStatus.EXECUTING, resumed.status)
        self.assertEqual("stage-current", resumed.stage.stage_id)
        self.assertEqual([ProjectCommand.START_EXECUTION], [item.kind for item in commands])

    def test_waiting_user_continue_is_explicit_and_does_not_replay_history(self) -> None:
        state, _ = self.apply(
            _state(ProjectStatus.EXECUTING), ProjectEvent.PROJECT_BLOCKED, reason="need input"
        )
        self.assertEqual(ProjectStatus.WAITING_USER, state.status)
        state, commands = self.apply(
            state,
            ProjectEvent.USER_CONTINUE,
            command="request_plan",
        )
        self.assertEqual(ProjectStatus.PLANNING, state.status)
        self.assertEqual([ProjectCommand.REQUEST_PLAN], [item.kind for item in commands])

    def test_terminal_states_reject_unrelated_events(self) -> None:
        for status in (ProjectStatus.COMPLETED, ProjectStatus.FAILED):
            for event in ProjectEvent:
                with self.subTest(status=status, event=event):
                    with self.assertRaises(InvalidTransition):
                        self.apply(_state(status), event)


if __name__ == "__main__":
    unittest.main()
