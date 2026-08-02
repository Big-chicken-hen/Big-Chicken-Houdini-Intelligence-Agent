from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hia_panel.project_team import ProjectPanelState  # noqa: E402
from hia_panel.project_team_view import (  # noqa: E402
    PYSIDE_AVAILABLE,
    ProjectTeamView,
)


def snapshot():
    return {
        "schema": "hia-project-team/2",
        "settings": {"mode": "team", "writable": True},
        "projects": [
            {
                "project_id": "project-a",
                "title": "极长但仍必须在窄面板中安全省略而不是撑出横向滚动条的项目名称",
                "status": "needs_attention",
                "stage": "review",
                "attention_reason": "视觉证据显示栏杆穿插",
                "consumed_turns": 8,
                "requirements": [
                    {"requirement_id": "REQ-structure", "kind": "structure", "status": "active"},
                    {"requirement_id": "REQ-animation", "kind": "animation", "status": "active"},
                ],
                "actions": {"append_guidance": True, "continue": True, "stop": True},
                "threads": [
                    {
                        "role": "supervisor",
                        "role_title": "监督 AI",
                        "thread_id": "thread-supervisor",
                        "status": "waiting",
                        "actions": {
                            "open_thread": True,
                            "append_guidance": True,
                            "set_role_runtime": True,
                        },
                    }
                ],
            }
        ],
    }


def add_execution_role(value, *, status="waiting"):
    value["projects"][0]["threads"].append(
        {
            "role": "execution",
            "role_title": "执行 AI",
            "thread_id": "thread-execution",
            "status": status,
            "actions": {
                "open_thread": True,
                "append_guidance": True,
                "set_role_runtime": True,
            },
        }
    )
    return value


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is available only in Houdini/runtime Qt")
class ProjectTeamQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.state = ProjectPanelState()
        self.state.apply_snapshot(snapshot())
        self.view = ProjectTeamView(self.state)
        self.view.resize(470, 760)
        self.view.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def test_buttons_are_complete_and_project_entry_exists_once(self) -> None:
        from PySide6 import QtWidgets

        self.assertEqual("新建普通任务（单个 AI）", self.view.new_single_button.text())
        self.assertEqual("新建项目（项目团队）", self.view.new_project_button.text())
        project_buttons = [
            button
            for button in self.view.findChildren(QtWidgets.QPushButton)
            if button.text() == "新建项目（项目团队）"
        ]
        self.assertEqual(1, len(project_buttons))
        self.assertNotIn("...", self.view.new_single_button.text())
        self.assertNotIn("…", self.view.new_project_button.text())

    def test_470px_view_has_no_tree_horizontal_scroll_and_dark_background(self) -> None:
        from PySide6 import QtCore

        self.assertEqual(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            self.view.tree.horizontalScrollBarPolicy(),
        )
        color = self.view.palette().window().color()
        self.assertLess(color.red(), 64)
        self.assertLess(color.green(), 64)
        self.assertLess(color.blue(), 64)

    def test_project_container_does_not_open_chat_but_role_does(self) -> None:
        opened = []
        self.view.openThreadRequested.connect(opened.append)
        self.state.select("project:project-a")
        self.view._open_selected()
        self.assertEqual([], opened)
        self.state.select("role:project-a:supervisor")
        self.view._open_selected()
        self.assertEqual(["thread-supervisor"], opened)

    def test_ordinary_selection_clears_project_only_detail(self) -> None:
        data = snapshot()
        self.state.apply_snapshot(
            data,
            [{"thread_id": "ordinary-a", "name": "普通任务 A", "updated_at": 2}],
        )
        self.state.select("project:project-a")
        self.view.refresh_view()
        self.assertTrue(self.view.guidance_edit.isVisible())
        self.assertTrue(self.view.attention_surface.isVisible())

        self.state.select("thread:ordinary-a")
        self.view.refresh_view()
        self.app.processEvents()
        self.assertEqual("普通任务 A", self.view.selection_title.text())
        self.assertFalse(self.view.runtime_widget.isVisible())
        self.assertFalse(self.view.guidance_edit.isVisible())
        self.assertFalse(self.view.guidance_button.isVisible())
        self.assertFalse(self.view.requirement_change_widget.isVisible())
        self.assertFalse(self.view.attention_surface.isVisible())

    def test_collapse_and_expand_restore_navigation(self) -> None:
        changes = []
        self.view.collapsedChanged.connect(changes.append)
        self.view.collapse_button.setChecked(True)
        self.app.processEvents()
        self.assertFalse(self.view.navigation_body.isVisible())
        self.assertFalse(self.view.title_label.isVisible())
        self.assertEqual(42, self.view.minimumWidth())
        self.view.collapse_button.setChecked(False)
        self.app.processEvents()
        self.assertTrue(self.view.navigation_body.isVisible())
        self.assertTrue(self.view.title_label.isVisible())
        self.assertEqual(300, self.view.minimumWidth())
        self.assertEqual([True, False], changes)

    def test_project_and_ordinary_groups_keep_user_collapse_after_refresh(self) -> None:
        self.state.apply_snapshot(
            snapshot(),
            [{"thread_id": "ordinary-a", "name": "普通任务 A", "updated_at": 2}],
        )
        self.view.refresh_view()
        projects = self.view.tree.topLevelItem(0)
        ordinary = self.view.tree.topLevelItem(1)
        projects.setExpanded(False)
        ordinary.setExpanded(False)
        self.app.processEvents()

        self.view.refresh_view()

        self.assertFalse(self.view.tree.topLevelItem(0).isExpanded())
        self.assertFalse(self.view.tree.topLevelItem(1).isExpanded())
        self.assertEqual("项目（1）", self.view.tree.topLevelItem(0).text(0))
        self.assertEqual("普通任务（1）", self.view.tree.topLevelItem(1).text(0))

    def test_empty_and_ordinary_selection_do_not_show_project_detail_card(self) -> None:
        self.state.apply_snapshot(
            snapshot(),
            [{"thread_id": "ordinary-a", "name": "普通任务 A", "updated_at": 2}],
        )
        self.state.select(None)
        self.view.refresh_view()
        self.assertFalse(self.view.detail_surface.isVisible())
        self.state.select("thread:ordinary-a")
        self.view.refresh_view()
        self.assertFalse(self.view.detail_surface.isVisible())

    def test_role_runtime_uses_live_model_capabilities_without_free_text(self) -> None:
        models = [
            {
                "model": "model-a",
                "displayName": "Model A",
                "isDefault": True,
                "inputModalities": ["text", "image"],
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "fast"},
                    {"reasoningEffort": "high", "description": "deep"},
                ],
                "defaultReasoningEffort": "high",
                "serviceTiers": [
                    {"id": "priority", "name": "Priority", "description": "fast"}
                ],
                "defaultServiceTier": "priority",
            },
            {
                "model": "model-b",
                "displayName": "Model B",
                "isDefault": False,
                "inputModalities": ["text"],
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "ultra", "description": "deepest"}
                ],
                "defaultReasoningEffort": "ultra",
                "serviceTiers": [],
                "defaultServiceTier": None,
            },
        ]
        self.view.set_model_catalog(models)
        self.state.select("role:project-a:supervisor")
        self.view.refresh_view()
        self.assertFalse(self.view.model_combo.isEditable())
        self.assertEqual(2, self.view.model_combo.count())
        self.assertEqual(
            [None, "low", "high"],
            [
                self.view.effort_combo.itemData(index)
                for index in range(self.view.effort_combo.count())
            ],
        )
        self.view.model_combo.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(
            [None, "ultra"],
            [
                self.view.effort_combo.itemData(index)
                for index in range(self.view.effort_combo.count())
            ],
        )
        self.assertEqual(
            "model-b",
            self.state.runtime_draft_for(self.state.tree.projects[0].roles[0]).model,
        )

    def test_role_runtime_drafts_are_independent_and_interrupted_roles_editable(self) -> None:
        data = add_execution_role(snapshot(), status="interrupted")
        self.state.apply_snapshot(data)
        self.view.set_model_catalog(
            [
                {
                    "model": "model-a",
                    "isDefault": True,
                    "inputModalities": ["text"],
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "fast"},
                        {"reasoningEffort": "high", "description": "deep"},
                    ],
                    "defaultReasoningEffort": "low",
                    "serviceTiers": [],
                    "defaultServiceTier": None,
                },
                {
                    "model": "model-b",
                    "inputModalities": ["text"],
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "max", "description": "deepest"}
                    ],
                    "defaultReasoningEffort": "max",
                    "serviceTiers": [],
                    "defaultServiceTier": None,
                },
            ]
        )
        self.state.select("role:project-a:supervisor")
        self.view.refresh_view()
        self.view.model_combo.setCurrentIndex(1)
        self.app.processEvents()
        supervisor = self.state.tree.projects[0].roles[0]
        self.assertEqual("model-b", self.state.runtime_draft_for(supervisor).model)

        self.state.select("role:project-a:execution")
        self.view.refresh_view()
        self.app.processEvents()
        execution = self.state.tree.projects[0].roles[1]
        self.assertTrue(self.view.model_combo.isEnabled())
        self.assertTrue(self.view.effort_combo.isEnabled())
        self.assertTrue(self.view.save_runtime_button.isEnabled())
        self.view.effort_combo.setCurrentIndex(2)
        self.app.processEvents()
        self.assertEqual("model-a", self.state.runtime_draft_for(execution).model)
        self.assertEqual("high", self.state.runtime_draft_for(execution).effort)
        self.assertEqual("model-b", self.state.runtime_draft_for(supervisor).model)

    def test_empty_catalog_exposes_refresh_instead_of_permanent_lock(self) -> None:
        requested = []
        self.view.modelCatalogRefreshRequested.connect(lambda: requested.append(True))
        self.state.select("role:project-a:supervisor")
        self.view.refresh_view()
        self.assertFalse(self.view.model_combo.isEnabled())
        self.assertTrue(self.view.refresh_models_button.isEnabled())
        self.assertIn("刷新", self.view.runtime_status_label.text())
        self.view.refresh_models_button.click()
        self.assertEqual([True], requested)

    def test_guidance_scope_is_mutually_exclusive_and_ack_matches_mode(self) -> None:
        emitted = []
        self.view.appendGuidanceRequested.connect(
            lambda project_id, thread_id, text, requirement_change, force_replan: emitted.append(
                (project_id, thread_id, text, requirement_change, force_replan)
            )
        )
        self.state.select("role:project-a:supervisor")
        self.view.refresh_view()
        self.assertTrue(self.view.current_step_guidance_button.isChecked())
        self.assertFalse(self.view.replan_guidance_button.isChecked())
        self.view.replan_guidance_button.click()
        self.assertFalse(self.view.current_step_guidance_button.isChecked())
        self.assertTrue(self.view.replan_guidance_button.isChecked())
        self.view.guidance_edit.setPlainText("改成三层钢结构并重新安排所有阶段")
        self.view._send_guidance()
        self.assertEqual(
            [
                (
                    "project-a",
                    None,
                    "改成三层钢结构并重新安排所有阶段",
                    {"operation": "add"},
                    True,
                )
            ],
            emitted,
        )
        self.assertFalse(
            self.view.acknowledge_guidance(
                "改成三层钢结构并重新安排所有阶段", {"operation": "add"}, False
            )
        )
        self.assertEqual(
            "改成三层钢结构并重新安排所有阶段",
            self.view.guidance_edit.toPlainText(),
        )
        self.assertTrue(
            self.view.acknowledge_guidance(
                "改成三层钢结构并重新安排所有阶段", {"operation": "add"}, True
            )
        )
        self.assertTrue(self.view.current_step_guidance_button.isChecked())

    def test_complete_panel_goal_card_renders_dark_instead_of_white(self) -> None:
        from hia_panel.panel import HoudiniIntelligencePanel

        with mock.patch.dict(
            os.environ,
            {"HIA_BRIDGE_URL": "", "HIA_BRIDGE_TOKEN": ""},
        ):
            panel = HoudiniIntelligencePanel(hou_module=None)
        try:
            panel.resize(1280, 800)
            panel.show()
            self.app.processEvents()
            image = panel.goal_stage_summary_group.grab().toImage()
            self.assertFalse(image.isNull())
            samples = [
                image.pixelColor(x, y)
                for y in range(2, image.height(), 8)
                for x in range(2, image.width(), 8)
            ]
            near_white = sum(
                color.red() > 240 and color.green() > 240 and color.blue() > 240
                for color in samples
            )
            self.assertLess(near_white, max(1, len(samples) // 10))
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
