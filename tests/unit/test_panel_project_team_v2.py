from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_panel.project_team import (  # noqa: E402
    ProjectPanelState,
    ProjectViewModel,
    RoleRuntimeDraft,
    RoleViewModel,
    find_tree_item,
    normalize_workspace_tree,
)
from hia_panel.project_team_controller import ProjectTeamController  # noqa: E402


def project_snapshot(*, role_thread_id: str = "thread-supervisor", status: str = "needs_attention"):
    return {
        "schema": "hia-project-team/2",
        "settings": {"mode": "team", "writable": True},
        "projects": [
            {
                "project_id": "project-house",
                "title": "写实木屋",
                "status": status,
                "stage": "structure",
                "attention_reason": "连续三轮没有新增证据",
                "consumed_turns": 17,
                "last_error": "栏杆穿入扶手",
                "latest_evidence_ids": ["evidence-17", "image-8"],
                "latest_repair_card": "重新约束栏杆端点和扶手净空",
                "actions": {
                    "append_guidance": True,
                    "continue": status == "needs_attention",
                    "stop": True,
                },
                "threads": [
                    {
                        "role": "supervisor",
                        "role_title": "监督 AI",
                        "thread_id": role_thread_id,
                        "status": "waiting",
                        "model": "gpt-supervisor",
                        "effort": "high",
                        "service_tier": "priority",
                        "actions": {
                            "open_thread": True,
                            "append_guidance": True,
                            "set_role_runtime": True,
                        },
                    },
                    {
                        "role": "execution",
                        "role_title": "执行 AI",
                        "thread_id": "thread-execution",
                        "status": "running",
                        "model": "gpt-execution",
                        "effort": "max",
                        "service_tier": "standard",
                        "actions": {
                            "open_thread": True,
                            "append_guidance": True,
                            "set_role_runtime": True,
                        },
                    },
                ],
            }
        ],
    }


class ProjectTeamViewModelTests(unittest.TestCase):
    def test_project_and_ordinary_threads_are_separate_and_members_do_not_leak(self) -> None:
        tree = normalize_workspace_tree(
            project_snapshot(),
            {
                "threads": [
                    {
                        "thread_id": "thread-supervisor",
                        "name": "must be folded into project",
                        "preview": "worker",
                        "updated_at": 3,
                    },
                    {
                        "thread_id": "thread-ordinary",
                        "name": "独立对话",
                        "preview": "ordinary",
                        "updated_at": 2,
                    },
                ]
            },
        )
        self.assertEqual(["写实木屋"], [item.title for item in tree.projects])
        self.assertEqual(
            ["thread-ordinary"],
            [item.thread_id for item in tree.ordinary_threads],
        )
        self.assertEqual(
            ["supervisor", "execution"],
            [role.role for role in tree.projects[0].roles],
        )

    def test_project_container_never_opens_chat_but_role_and_ordinary_do(self) -> None:
        state = ProjectPanelState()
        state.apply_snapshot(
            project_snapshot(),
            [{"thread_id": "ordinary", "name": "普通", "updated_at": 1}],
        )
        state.select("project:project-house")
        self.assertIsNone(state.selected_chat_thread_id())
        state.select("role:project-house:supervisor")
        self.assertEqual("thread-supervisor", state.selected_chat_thread_id())
        state.select("thread:ordinary")
        self.assertEqual("ordinary", state.selected_chat_thread_id())

    def test_selection_and_role_draft_survive_verified_role_thread_transfer(self) -> None:
        state = ProjectPanelState()
        state.apply_snapshot(project_snapshot())
        stable_key = "role:project-house:supervisor"
        state.select(stable_key)
        draft = RoleRuntimeDraft("gpt-next", "ultra", "priority")
        state.set_runtime_draft(stable_key, draft)

        state.apply_snapshot(project_snapshot(role_thread_id="thread-supervisor-new"))
        self.assertEqual(stable_key, state.selected_key)
        self.assertEqual("thread-supervisor-new", state.selected_chat_thread_id())
        role = find_tree_item(state.tree, stable_key)
        self.assertIsInstance(role, RoleViewModel)
        self.assertEqual(draft, state.runtime_draft_for(role))

    def test_each_role_owns_an_independent_next_turn_draft(self) -> None:
        snapshot = project_snapshot()
        existing_roles = {
            item["role"] for item in snapshot["projects"][0]["threads"]
        }
        for role in (
            "supervisor",
            "planning",
            "execution",
            "visual_review",
            "technical_review",
        ):
            if role in existing_roles:
                continue
            snapshot["projects"][0]["threads"].append(
                {
                    "role": role,
                    "thread_id": f"thread-{role}",
                    "status": "waiting",
                    "actions": {
                        "open_thread": True,
                        "append_guidance": True,
                        "set_role_runtime": True,
                    },
                }
            )
        state = ProjectPanelState()
        state.apply_snapshot(snapshot)
        self.assertEqual(5, len(state.tree.projects[0].roles))
        expected = {}
        for index, role in enumerate(state.tree.projects[0].roles):
            draft = RoleRuntimeDraft(
                f"gpt-role-{index}",
                f"effort-{index}",
                f"tier-{index}",
            )
            state.set_runtime_draft(role.stable_key, draft)
            expected[role.stable_key] = draft
        self.assertEqual(
            expected,
            {
                role.stable_key: state.runtime_draft_for(role)
                for role in state.tree.projects[0].roles
            },
        )

    def test_needs_attention_exposes_reason_evidence_repair_and_actions(self) -> None:
        project = normalize_workspace_tree(project_snapshot()).projects[0]
        self.assertTrue(project.attention.visible)
        self.assertEqual("structure", project.attention.stage)
        self.assertEqual(17, project.attention.consumed_turns)
        self.assertEqual(("evidence-17", "image-8"), project.attention.latest_evidence_ids)
        self.assertIn("端点", project.attention.latest_repair_card)
        self.assertTrue(project.can_continue)
        self.assertTrue(project.can_guide)
        self.assertTrue(project.can_stop)

    def test_project_action_permissions_fail_closed(self) -> None:
        snapshot = project_snapshot()
        snapshot["projects"][0]["actions"] = {}
        snapshot["projects"][0]["threads"][0]["actions"] = {}
        project = normalize_workspace_tree(snapshot).projects[0]
        role = project.roles[0]
        self.assertFalse(project.can_continue)
        self.assertFalse(project.can_guide)
        self.assertFalse(project.can_stop)
        self.assertFalse(role.can_open)
        self.assertFalse(role.can_guide)
        self.assertFalse(role.can_set_runtime)

    def test_unknown_schema_does_not_render_untrusted_membership(self) -> None:
        snapshot = project_snapshot()
        snapshot["schema"] = "hia-project-team/999"
        tree = normalize_workspace_tree(snapshot)
        self.assertEqual("unsupported", tree.state_status)
        self.assertEqual((), tree.projects)


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []
        self.connect_count = 0
        self.disconnect_count = 0

    def connect(self, callback) -> None:
        self.connect_count += 1
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.disconnect_count += 1
        self.callbacks.remove(callback)

    def emit(self, *args) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class FakeView:
    def __init__(self) -> None:
        self.refreshRequested = FakeSignal()
        self.newTaskRequested = FakeSignal()
        self.openThreadRequested = FakeSignal()
        self.appendGuidanceRequested = FakeSignal()
        self.roleRuntimeRequested = FakeSignal()
        self.continueProjectRequested = FakeSignal()
        self.stopProjectRequested = FakeSignal()
        self.state = ProjectPanelState()
        self.render_count = 0
        self.guidance_ack_count = 0

    def render(self) -> None:
        self.render_count += 1

    def acknowledge_guidance(self) -> None:
        self.guidance_ack_count += 1


class FakeGateway:
    def __init__(self) -> None:
        self.actionCompleted = FakeSignal()
        self.requestFailed = FakeSignal()
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return call


class ProjectTeamControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = FakeView()
        self.gateway = FakeGateway()
        self.new_routes = []
        self.opened = []
        self.errors = []
        self.controller = ProjectTeamController(
            self.view,
            self.gateway,
            on_new_task=self.new_routes.append,
            on_open_thread=self.opened.append,
            on_error=self.errors.append,
        )

    def test_view_signals_connect_once_and_show_is_idempotent(self) -> None:
        signals = (
            self.view.refreshRequested,
            self.view.newTaskRequested,
            self.view.openThreadRequested,
            self.view.appendGuidanceRequested,
            self.view.roleRuntimeRequested,
            self.view.continueProjectRequested,
            self.view.stopProjectRequested,
        )
        self.assertTrue(all(signal.connect_count == 1 for signal in signals))
        self.controller.show()
        self.controller.show()
        self.assertEqual(1, self.gateway.actionCompleted.connect_count)
        self.assertEqual(1, self.gateway.requestFailed.connect_count)
        self.assertEqual(
            ["get_project_team", "get_threads"],
            [call[0] for call in self.gateway.calls],
        )

    def test_close_reopen_reconnects_gateway_once_without_reconnecting_view(self) -> None:
        self.controller.show()
        self.controller.close()
        self.assertEqual(1, self.gateway.actionCompleted.disconnect_count)
        self.assertEqual(1, self.gateway.requestFailed.disconnect_count)
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.assertEqual(0, self.view.render_count)
        self.view.newTaskRequested.emit("team")
        self.view.openThreadRequested.emit("thread-hidden")
        self.assertEqual([], self.new_routes)
        self.assertEqual([], self.opened)

        self.controller.show()
        self.assertEqual(2, self.gateway.actionCompleted.connect_count)
        self.assertEqual(1, self.view.refreshRequested.connect_count)

    def test_snapshot_and_history_render_as_one_tree_when_both_arrive(self) -> None:
        self.controller.show()
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.gateway.actionCompleted.emit(
            "project_history_refresh",
            {
                "threads": [
                    {"thread_id": "ordinary", "name": "普通", "updated_at": 1}
                ]
            },
        )
        self.assertEqual(1, len(self.view.state.tree.projects))
        self.assertEqual(1, len(self.view.state.tree.ordinary_threads))

    def test_role_runtime_and_guidance_use_explicit_ids(self) -> None:
        self.controller.show()
        self.view.roleRuntimeRequested.emit(
            "project-house",
            "thread-execution",
            "gpt-next",
            "ultra",
            "priority",
        )
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", "减少屋顶装饰"
        )
        runtime = next(call for call in self.gateway.calls if call[0] == "set_project_role_runtime")
        self.assertEqual("thread-execution", runtime[2]["thread_id"])
        self.assertEqual("gpt-next", runtime[2]["model"])
        guidance = next(call for call in self.gateway.calls if call[0] == "append_project_guidance")
        self.assertEqual("减少屋顶装饰", guidance[2]["text"])

    def test_guidance_text_is_acknowledged_only_after_success(self) -> None:
        self.controller.show()
        self.gateway.requestFailed.emit(
            "project_guidance:project-house",
            {"error": {"message": "project needs attention"}},
        )
        self.assertEqual(0, self.view.guidance_ack_count)
        self.assertTrue(self.errors)
        self.gateway.actionCompleted.emit(
            "project_guidance:project-house",
            {"ok": True},
        )
        self.assertEqual(0, self.view.guidance_ack_count)
        self.gateway.actionCompleted.emit(
            "project_guidance:project-house",
            {"project_team": project_snapshot()},
        )
        self.assertEqual(1, self.view.guidance_ack_count)


if __name__ == "__main__":
    unittest.main()
