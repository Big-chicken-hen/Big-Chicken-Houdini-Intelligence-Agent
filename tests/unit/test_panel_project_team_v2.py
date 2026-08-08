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


def project_snapshot(*, role_thread_id: str = "thread-supervisor", status: str = "waiting_user"):
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
                "requirements": [
                    {"requirement_id": "REQ-structure", "kind": "structure", "status": "active"},
                    {"requirement_id": "REQ-animation", "kind": "animation", "status": "active"},
                ],
                "actions": {
                    "append_guidance": True,
                    "continue": status in {"waiting_user", "stopped"},
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

    def test_selection_and_role_draft_survive_an_ordinary_snapshot_refresh(self) -> None:
        state = ProjectPanelState()
        state.apply_snapshot(project_snapshot())
        stable_key = "role:project-house:supervisor"
        state.select(stable_key)
        draft = RoleRuntimeDraft("gpt-next", "ultra", "priority")
        state.set_runtime_draft(stable_key, draft)

        state.apply_snapshot(project_snapshot())
        self.assertEqual(stable_key, state.selected_key)
        self.assertEqual("thread-supervisor", state.selected_chat_thread_id())
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

    def test_waiting_user_exposes_reason_evidence_repair_and_actions(self) -> None:
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
        tree = normalize_workspace_tree(
            snapshot,
            [
                {
                    "thread_id": "ordinary-safe",
                    "name": "普通任务仍可用",
                    "updated_at": 7,
                }
            ],
        )
        self.assertEqual("unsupported", tree.state_status)
        self.assertEqual((), tree.projects)
        self.assertEqual(
            ("ordinary-safe",),
            tuple(item.thread_id for item in tree.ordinary_threads),
        )


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
        self.deleteThreadRequested = FakeSignal()
        self.renameThreadRequested = FakeSignal()
        self.copyThreadIdRequested = FakeSignal()
        self.appendGuidanceRequested = FakeSignal()
        self.roleRuntimeRequested = FakeSignal()
        self.modelCatalogRefreshRequested = FakeSignal()
        self.continueProjectRequested = FakeSignal()
        self.stopProjectRequested = FakeSignal()
        self.state = ProjectPanelState()
        self.render_count = 0
        self.guidance_ack_count = 0
        self.guidance_text = ""
        self.guidance_requirement_change = None
        self.model_catalogs = []

    def refresh_view(self) -> None:
        self.render_count += 1

    def acknowledge_guidance(
        self,
        submitted_text: str,
        requirement_change=None,
    ) -> bool:
        if (
            self.guidance_text != submitted_text
            or self.guidance_requirement_change != requirement_change
        ):
            return False
        self.guidance_ack_count += 1
        self.guidance_text = ""
        self.guidance_requirement_change = None
        return True

    def set_model_catalog(self, models) -> None:
        self.model_catalogs.append(models)


class FakeGateway:
    def __init__(self) -> None:
        self.actionCompleted = FakeSignal()
        self.requestFailed = FakeSignal()
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return f"request-{len(self.calls)}"

        return call


class ProjectTeamControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = FakeView()
        self.gateway = FakeGateway()
        self.new_routes = []
        self.opened = []
        self.deleted = []
        self.renamed = []
        self.copied = []
        self.errors = []
        self.controller = ProjectTeamController(
            self.view,
            self.gateway,
            on_new_task=self.new_routes.append,
            on_open_thread=self.opened.append,
            on_delete_thread=self.deleted.append,
            on_rename_thread=lambda thread_id, name: self.renamed.append(
                (thread_id, name)
            ),
            on_copy_thread_id=self.copied.append,
            on_error=self.errors.append,
        )

    def test_view_signals_connect_once_and_show_is_idempotent(self) -> None:
        signals = (
            self.view.refreshRequested,
            self.view.newTaskRequested,
            self.view.openThreadRequested,
            self.view.deleteThreadRequested,
            self.view.renameThreadRequested,
            self.view.copyThreadIdRequested,
            self.view.appendGuidanceRequested,
            self.view.roleRuntimeRequested,
            self.view.modelCatalogRefreshRequested,
            self.view.continueProjectRequested,
            self.view.stopProjectRequested,
        )
        self.assertTrue(all(signal.connect_count == 1 for signal in signals))
        self.controller.show()
        self.controller.show()
        self.assertEqual(1, self.gateway.actionCompleted.connect_count)
        self.assertEqual(1, self.gateway.requestFailed.connect_count)
        self.assertEqual(
            ["get_project_team", "get_models"],
            [call[0] for call in self.gateway.calls],
        )

    def test_delete_signal_only_forwards_while_controller_is_active(self) -> None:
        self.view.deleteThreadRequested.emit("thread-hidden")
        self.assertEqual([], self.deleted)

        self.controller.show()
        self.view.deleteThreadRequested.emit("thread-ordinary")
        self.assertEqual(["thread-ordinary"], self.deleted)

    def test_copy_signal_forwards_ordinary_thread_id(self) -> None:
        self.controller.show()
        self.view.copyThreadIdRequested.emit("thread-ordinary")
        self.assertEqual(["thread-ordinary"], self.copied)


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

    def test_empty_catalog_has_a_direct_retry_path(self) -> None:
        self.controller.show()
        initial_model_calls = len(
            [call for call in self.gateway.calls if call[0] == "get_models"]
        )
        self.view.modelCatalogRefreshRequested.emit()
        model_calls = [
            call for call in self.gateway.calls if call[0] == "get_models"
        ]
        self.assertEqual(initial_model_calls + 1, len(model_calls))
        self.assertEqual("project_model_catalog", model_calls[-1][2]["context"])

    def test_snapshot_and_history_render_as_one_tree_when_both_arrive(self) -> None:
        self.controller.show()
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.controller.consume_ordinary_threads(
            [{"thread_id": "ordinary", "name": "普通", "updated_at": 1}]
        )
        self.assertEqual(1, len(self.view.state.tree.projects))
        self.assertEqual(1, len(self.view.state.tree.ordinary_threads))

    def test_ordinary_history_survives_missing_and_failed_project_snapshot(self) -> None:
        self.controller.show()
        self.controller.consume_ordinary_threads(
            [{"thread_id": "ordinary", "name": "普通", "updated_at": 1}]
        )
        self.assertEqual(
            ["ordinary"],
            [item.thread_id for item in self.view.state.tree.ordinary_threads],
        )

        self.gateway.requestFailed.emit(
            "project_team_refresh",
            {
                "structured_error": {
                    "code": "PROJECT_REGISTRY_CORRUPTED",
                    "message": "registry unavailable",
                }
            },
        )
        self.assertEqual(
            ["ordinary"],
            [item.thread_id for item in self.view.state.tree.ordinary_threads],
        )

        self.view.openThreadRequested.emit("ordinary")
        self.view.renameThreadRequested.emit("ordinary", "已重命名")
        self.view.copyThreadIdRequested.emit("ordinary")
        self.view.deleteThreadRequested.emit("ordinary")
        self.assertEqual(["ordinary"], self.opened)
        self.assertEqual([("ordinary", "已重命名")], self.renamed)
        self.assertEqual(["ordinary"], self.copied)
        self.assertEqual(["ordinary"], self.deleted)

    def test_new_ordinary_selection_survives_project_then_history_order(self) -> None:
        self.controller.show()
        self.controller.select_ordinary_thread_when_available("ordinary-new")
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.assertIsNone(self.view.state.selected_key)
        self.controller.consume_ordinary_threads(
            [{"thread_id": "ordinary-new", "name": "新普通任务", "updated_at": 2}]
        )
        self.assertEqual("thread:ordinary-new", self.view.state.selected_key)
        self.assertEqual("ordinary-new", self.view.state.selected_chat_thread_id())

    def test_new_ordinary_selection_survives_history_then_project_order(self) -> None:
        self.controller.show()
        self.controller.select_ordinary_thread_when_available("ordinary-new")
        self.controller.consume_ordinary_threads(
            [{"thread_id": "ordinary-new", "name": "新普通任务", "updated_at": 2}]
        )
        self.assertEqual("thread:ordinary-new", self.view.state.selected_key)
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.assertEqual("thread:ordinary-new", self.view.state.selected_key)
        self.assertEqual("ordinary-new", self.view.state.selected_chat_thread_id())

    def test_live_project_event_updates_project_without_losing_ordinary_history(self) -> None:
        self.controller.show()
        self.gateway.actionCompleted.emit(
            "project_team_refresh", {"project_team": project_snapshot()}
        )
        self.controller.consume_ordinary_threads(
            [{"thread_id": "ordinary", "name": "普通", "updated_at": 1}]
        )
        updated = project_snapshot(status="completed")
        self.assertTrue(
            self.controller.consume_project_team_update(
                {"type": "project_team_updated", "project_team": updated}
            )
        )
        self.assertEqual("completed", self.view.state.tree.projects[0].status)
        self.assertEqual(
            ["ordinary"],
            [item.thread_id for item in self.view.state.tree.ordinary_threads],
        )

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
        self.assertRegex(guidance[2]["context"], r"^project_guidance:[0-9a-f]{32}$")

    def test_project_model_catalog_is_live_and_runtime_error_refreshes_it(self) -> None:
        self.controller.show()
        models = [
            {
                "model": "gpt-live",
                "inputModalities": ["text", "image"],
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "high", "description": "deep"}
                ],
                "serviceTiers": [
                    {"id": "priority", "name": "Priority", "description": "fast"}
                ],
            }
        ]
        self.gateway.actionCompleted.emit(
            "project_model_catalog", {"models": models}
        )
        self.assertEqual([models], self.view.model_catalogs)
        self.gateway.requestFailed.emit(
            "project_runtime:project-house:thread-execution",
            {
                "structured_error": {
                    "code": "PROJECT_RUNTIME_SELECTION_INVALID",
                    "message": "unsupported",
                    "details": {"field": "effort", "next_action": "refresh_models"},
                }
            },
        )
        self.assertTrue(any(call[0] == "get_models" for call in self.gateway.calls))
        self.assertIn("未保存", self.errors[-1])

    def test_guidance_text_is_acknowledged_only_after_success(self) -> None:
        self.controller.show()
        self.view.guidance_text = "保留屋顶"
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", self.view.guidance_text
        )
        context = next(
            call[2]["context"]
            for call in self.gateway.calls
            if call[0] == "append_project_guidance"
        )
        self.gateway.requestFailed.emit(
            context,
            {"error": {"message": "project needs attention"}},
        )
        self.assertEqual(0, self.view.guidance_ack_count)
        self.assertEqual("保留屋顶", self.view.guidance_text)
        self.assertTrue(self.errors)
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", self.view.guidance_text
        )
        context = [
            call[2]["context"]
            for call in self.gateway.calls
            if call[0] == "append_project_guidance"
        ][-1]
        self.gateway.actionCompleted.emit(
            context,
            {"ok": True},
        )
        self.assertEqual(0, self.view.guidance_ack_count)
        self.gateway.actionCompleted.emit(
            context,
            {"project_team": project_snapshot()},
        )
        self.assertEqual(1, self.view.guidance_ack_count)

    def test_requirement_removal_is_explicit_and_acknowledges_exact_delta(self) -> None:
        self.controller.show()
        self.view.guidance_text = "改成三层钢结构并重新安排所有阶段"
        self.view.guidance_requirement_change = {
            "operation": "remove",
            "target_requirement_id": "REQ-animation",
        }
        self.view.appendGuidanceRequested.emit(
            "project-house",
            None,
            self.view.guidance_text,
            self.view.guidance_requirement_change,
        )
        guidance = [
            call
            for call in self.gateway.calls
            if call[0] == "append_project_guidance"
        ][-1]
        self.assertNotIn("force_replan", guidance[2])
        self.assertEqual(
            {"remove": ["REQ-animation"]}, guidance[2]["requirement_delta"]
        )
        context = guidance[2]["context"]
        self.gateway.actionCompleted.emit(
            context, {"project_team": project_snapshot()}
        )
        self.assertEqual("", self.view.guidance_text)
        self.assertEqual(1, self.view.guidance_ack_count)

    def test_replacing_requirement_sends_explicit_add_and_supersede_delta(self) -> None:
        self.controller.show()
        self.view.guidance_text = "缩小材质范围，只保留基础木材"
        self.view.guidance_requirement_change = {
            "operation": "replace",
            "target_requirement_id": "REQ-structure",
        }
        self.view.appendGuidanceRequested.emit(
            "project-house",
            None,
            self.view.guidance_text,
            self.view.guidance_requirement_change,
        )
        guidance = [
            call for call in self.gateway.calls if call[0] == "append_project_guidance"
        ][-1]
        delta = guidance[2]["requirement_delta"]
        new_id = delta["add"][0]["requirement_id"]
        self.assertEqual({"REQ-structure": new_id}, delta["supersede"])
        self.assertEqual("user_scope", delta["add"][0]["kind"])

    def test_plain_current_step_guidance_has_no_requirement_delta(self) -> None:
        self.controller.show()
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", "只调整当前扶手间距"
        )
        guidance = [
            call for call in self.gateway.calls if call[0] == "append_project_guidance"
        ][-1]
        self.assertIsNone(guidance[2]["requirement_delta"])
        self.assertNotIn("force_replan", guidance[2])

    def test_old_guidance_ack_never_clears_new_draft(self) -> None:
        self.controller.show()
        self.view.guidance_text = "旧指导"
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", self.view.guidance_text
        )
        context = next(
            call[2]["context"]
            for call in self.gateway.calls
            if call[0] == "append_project_guidance"
        )
        self.view.guidance_text = "新的详细指导"
        self.gateway.actionCompleted.emit(
            context, {"project_team": project_snapshot()}
        )
        self.assertEqual("新的详细指导", self.view.guidance_text)
        self.assertEqual(0, self.view.guidance_ack_count)
        self.assertIn("当前正在编辑的内容已保留", self.errors[-1])

    def test_inactive_guidance_failure_is_actionable_and_preserves_text(self) -> None:
        self.controller.show()
        self.view.guidance_text = "继续细化栏杆"
        self.view.appendGuidanceRequested.emit(
            "project-house", "thread-execution", self.view.guidance_text
        )
        context = next(
            call[2]["context"]
            for call in self.gateway.calls
            if call[0] == "append_project_guidance"
        )
        self.gateway.requestFailed.emit(
            context,
            {
                "structured_error": {
                    "code": "PROJECT_GUIDANCE_INACTIVE",
                    "message": "project is not running",
                    "details": {"recoverable": False},
                }
            },
        )
        self.assertEqual("继续细化栏杆", self.view.guidance_text)
        self.assertIn("请新建项目继续", self.errors[-1])
        self.assertEqual("get_project_team", self.gateway.calls[-1][0])


if __name__ == "__main__":
    unittest.main()
