from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
sys.path.insert(0, str(PANEL_LIB_ROOT))

_DLL_HANDLES: list[object] = []
_hfs = os.environ.get("HFS")
if sys.platform == "win32" and isinstance(_hfs, str) and _hfs:
    try:
        _DLL_HANDLES.append(os.add_dll_directory(str(Path(_hfs) / "bin")))
    except (FileNotFoundError, OSError):
        pass

try:
    from PySide6 import QtCore, QtTest, QtWidgets

    from hia_panel.panel import HoudiniIntelligencePanel
    from hia_panel.project_team import PROJECT_TEAM_ROLE_ORDER
    from hia_panel.project_team_view import ProjectTeamPage
except (ImportError, OSError) as exc:  # pragma: no cover - standard CI has no Qt
    QtCore = QtTest = QtWidgets = None  # type: ignore[assignment]
    HoudiniIntelligencePanel = ProjectTeamPage = None  # type: ignore[assignment]
    _QT_IMPORT_ERROR = str(exc)
else:
    _QT_IMPORT_ERROR = ""


class _PanelClient:
    def __init__(self) -> None:
        self.mode_requests: list[str] = []
        self.resume_requests: list[tuple[str, str | None, str]] = []
        self.thread_routes: list[str | None] = []
        self.guidance_requests: list[dict[str, object]] = []
        self.supervisor_guidance_requests: list[dict[str, object]] = []
        self.goal_requests: list[str] = []

    def set_project_team_mode(self, mode: str) -> str:
        self.mode_requests.append(mode)
        return "project-team-set-request"

    def resume_thread(
        self,
        thread_id: str,
        *,
        service_tier: str | None,
        context: str,
    ) -> None:
        self.resume_requests.append((thread_id, service_tier, context))

    def start_thread(
        self,
        *,
        model: str | None,
        service_tier: str | None,
        team_override: str | None,
    ) -> str:
        del model, service_tier
        self.thread_routes.append(team_override)
        return "qt-session-start"

    def get_goal(self, thread_id: str) -> None:
        self.goal_requests.append(thread_id)

    def guide_project_thread(
        self,
        project_id: str,
        thread_id: str,
        text: str,
        *,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
        local_image_paths: list[str],
        context: str,
    ) -> str:
        self.guidance_requests.append({
            "project_id": project_id,
            "thread_id": thread_id,
            "text": text,
            "model": model,
            "effort": effort,
            "service_tier": service_tier,
            "local_image_paths": list(local_image_paths),
            "context": context,
        })
        return context

    def guide_project_supervisor(
        self,
        text: str,
        *,
        expected_project_id: str | None,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
        local_image_paths: list[str],
        context: str,
    ) -> str:
        self.supervisor_guidance_requests.append({
            "text": text,
            "expected_project_id": expected_project_id,
            "model": model,
            "effort": effort,
            "service_tier": service_tier,
            "local_image_paths": list(local_image_paths),
            "context": context,
        })
        return context


@unittest.skipIf(QtWidgets is None, f"real PySide6 unavailable: {_QT_IMPORT_ERROR}")
class ProjectTeamRealQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def _snapshot(*, mode: str = "single") -> dict[str, object]:
        return {
            "schema": "hia-project-team/1",
            "revision": f"qt-{mode}",
            "mode": mode,
            "settings": {"mode": mode, "writable": True},
            "state_status": "ready",
            "projects": [
                {
                    "project_id": "project-qt",
                    "title": "程序化小鸡",
                    "status": "in_progress",
                    "stage": "执行",
                    "updated_at": 1_752_825_700,
                    "progress": {"completed": 2, "total": 5},
                    "actions": {
                        "open_thread": True,
                        "append_guidance": True,
                    },
                    "threads": [
                        {
                            "role": role,
                            "thread_id": f"worker-{role}",
                            "title": f"小鸡 {role}",
                            "status": "in_progress",
                            "model": "gpt-5.6-sol",
                        }
                        for role in PROJECT_TEAM_ROLE_ORDER
                    ],
                }
            ],
        }

    def _make_panel(self) -> HoudiniIntelligencePanel:
        with mock.patch.dict(
            os.environ,
            {"HIA_BRIDGE_URL": "", "HIA_BRIDGE_TOKEN": ""},
        ), mock.patch.object(
            HoudiniIntelligencePanel,
            "_initialize_houdini_read_adapter",
            return_value=None,
        ), mock.patch.object(
            HoudiniIntelligencePanel,
            "_refresh_selection_status",
            return_value=None,
        ), mock.patch.object(
            HoudiniIntelligencePanel,
            "_update_houdini_status",
            return_value=None,
        ), mock.patch.object(
            HoudiniIntelligencePanel,
            "_start_local_houdini_loop",
            return_value=None,
        ):
            panel = HoudiniIntelligencePanel()
        panel.resize(1_000, 760)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.deleteLater)
        return panel

    def assert_text_button_is_complete(
        self,
        button: QtWidgets.QToolButton,
        text: str,
    ) -> None:
        self.assertEqual(text, button.text())
        self.assertNotIn("…", button.text())
        self.assertNotIn("...", button.text())
        self.assertEqual(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly,
            button.toolButtonStyle(),
        )
        self.assertTrue(
            button.focusPolicy() & QtCore.Qt.FocusPolicy.ClickFocus,
            f"{text} must retain mouse-click focus in its exclusive group",
        )
        self.assertGreaterEqual(
            button.minimumWidth(),
            button.fontMetrics().horizontalAdvance(text) + 24,
        )
        self.assertGreaterEqual(button.minimumHeight(), 28)
        self.assertGreaterEqual(button.width(), button.minimumWidth())
        self.assertGreaterEqual(button.height(), button.minimumHeight())

    def test_project_page_contains_only_project_role_status_and_actions(self) -> None:
        panel = self._make_panel()
        panel._apply_project_team_snapshot(self._snapshot(mode="single"))
        page = panel.project_team_page
        page.resize(420, 700)
        self.app.processEvents()

        self.assertFalse(hasattr(page, "mode_buttons"))
        self.assertFalse(hasattr(page, "mode_single_button"))
        self.assertFalse(hasattr(page, "mode_team_button"))
        self.assertFalse(hasattr(page, "save_button"))
        self.assertFalse(hasattr(page, "guidance_edit"))
        self.assertFalse(hasattr(page, "guidance_button"))
        self.assertEqual(
            [],
            page.findChildren(QtWidgets.QPlainTextEdit),
        )
        self.assertEqual("刷新项目状态", page.refresh_button.text())
        self.assertEqual(1, page.project_tree.topLevelItemCount())
        self.assertEqual(
            len(PROJECT_TEAM_ROLE_ORDER),
            page.project_tree.topLevelItem(0).childCount(),
        )
        visible_text = " ".join(
            label.text() for label in page.findChildren(QtWidgets.QLabel)
        )
        self.assertNotIn("新任务如何推进", visible_text)
        self.assertNotIn("默认处理方式", visible_text)
        self.assertNotIn("单个 AI", visible_text)

    def test_default_new_task_buttons_live_only_in_collapsed_runtime_settings(
        self,
    ) -> None:
        panel = self._make_panel()
        panel._connected = False
        panel._authenticated = False
        panel._selected_thread_id = None
        panel._refresh_controls()

        self.assertFalse(panel.runtime_settings_group.isChecked())
        self.assertFalse(panel.turn_mode_buttons.isVisible())
        panel.runtime_settings_group.setChecked(True)
        self.app.processEvents()
        self.assertTrue(panel.turn_mode_buttons.isVisible())
        self.assertIs(
            panel.runtime_settings_group,
            panel.turn_mode_buttons.parentWidget(),
        )
        self.assert_text_button_is_complete(panel.turn_single_button, "普通任务")
        self.assert_text_button_is_complete(panel.turn_team_button, "项目")
        self.assertTrue(panel.turn_team_override_group.exclusive())
        self.assertTrue(panel.turn_single_button.isEnabled())
        self.assertTrue(panel.turn_team_button.isEnabled())

        clicked: list[str] = []
        panel.turn_team_button.clicked.connect(lambda: clicked.append("team"))
        panel.turn_single_button.clicked.connect(lambda: clicked.append("single"))
        QtTest.QTest.mouseClick(
            panel.turn_team_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(["team"], clicked)
        self.assertTrue(panel.turn_team_button.isChecked())
        self.assertFalse(panel.turn_single_button.isChecked())
        QtTest.QTest.mouseClick(
            panel.turn_single_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(["team", "single"], clicked)
        self.assertTrue(panel.turn_single_button.isChecked())
        self.assertFalse(panel.turn_team_button.isChecked())

        panel.turn_mode_buttons.setFixedWidth(150)
        self.app.processEvents()
        self.assertTrue(panel.turn_mode_buttons.isWrapped())
        for button in (panel.turn_single_button, panel.turn_team_button):
            self.assertEqual(
                QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly,
                button.toolButtonStyle(),
            )
            self.assertGreaterEqual(button.width(), button.minimumWidth())
            self.assertGreaterEqual(button.height(), button.minimumHeight())

        panel._turn_start_request_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())
        panel._turn_start_request_pending = False
        panel._turn_steer_request_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())
        panel._turn_steer_request_pending = False
        panel._interrupt_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())

    def test_project_draft_and_container_have_distinct_real_qt_composer_states(
        self,
    ) -> None:
        panel = self._make_panel()
        client = _PanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel._refresh_controls()

        self.assertEqual("新建普通任务", panel.new_thread_button.text())
        self.assertEqual("新建项目", panel.new_project_button.text())
        QtTest.QTest.mouseClick(
            panel.new_project_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()

        self.assertEqual([], client.thread_routes)
        self.assertEqual("project_draft", panel._task_view_mode)
        self.assertEqual("项目目标", panel.composer_context_label.text())
        self.assertEqual("创建项目并发送", panel.send_button.text())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertTrue(panel.composer_panel.isVisible())
        self.assertTrue(panel.add_image_button.isEnabled())
        self.assertTrue(panel.attachment_strip.isEnabled())
        self.assertFalse(panel.conversation.isVisible())
        self.assertTrue(panel.project_context_group.isVisible())
        self.assertTrue(panel.non_conversation_spacer.isVisible())
        self.assertIn("模式：新建项目", panel.goal_mode_label.text())
        self.assertLessEqual(
            panel.project_context_group.height(),
            panel.project_context_group.sizeHint().height() + 4,
        )
        self.assertLessEqual(
            panel.composer_context_label.height(),
            panel.composer_context_label.sizeHint().height() + 4,
        )
        self.assertIn(
            "background-color: palette(alternate-base)",
            panel.project_context_group.styleSheet(),
        )
        self.assertIn(
            "color: palette(text)",
            panel.project_context_title.styleSheet(),
        )
        self.assertIn(
            "color: palette(text)",
            panel.project_context_detail.styleSheet(),
        )

        panel._apply_project_team_snapshot(self._snapshot(mode="team"))
        panel._apply_threads(
            [
                {
                    "thread_id": f"worker-{role}",
                    "name": role,
                    "updated_at": 1_752_825_600,
                }
                for role in PROJECT_TEAM_ROLE_ORDER
            ]
        )
        project_item = panel.history_combo.topLevelItem(0).child(0)
        panel.history_combo.setCurrentItem(project_item)
        self.app.processEvents()

        self.assertEqual("project_container", panel._task_view_mode)
        self.assertIsNone(panel._selected_thread_id)
        self.assertFalse(panel.conversation.isVisible())
        self.assertFalse(panel.input_edit.isEnabled())
        self.assertFalse(panel.composer_panel.isVisible())
        self.assertFalse(panel.add_image_button.isEnabled())
        self.assertFalse(panel.attachment_strip.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertIn("项目本身不对话", panel.project_context_detail.text())

    def test_history_tree_sections_fold_workers_and_open_only_exact_role(self) -> None:
        panel = self._make_panel()
        client = _PanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel._apply_project_team_snapshot(self._snapshot(mode="team"))
        raw_threads = [
            {
                "thread_id": f"worker-{role}",
                "name": f"重复 worker {role}",
                "updated_at": 1_752_825_600,
            }
            for role in PROJECT_TEAM_ROLE_ORDER
        ] + [
            {
                "thread_id": "ordinary-one",
                "name": "普通材质任务",
                "updated_at": 1_752_825_650,
            }
        ]
        panel._apply_threads(raw_threads)
        tree = panel.history_combo
        self.app.processEvents()

        self.assertEqual(2, tree.topLevelItemCount())
        projects_group = tree.topLevelItem(0)
        tasks_group = tree.topLevelItem(1)
        self.assertEqual("项目", projects_group.text(0))
        self.assertEqual("普通任务", tasks_group.text(0))
        self.assertEqual(1, projects_group.childCount())
        self.assertEqual(1, tasks_group.childCount())
        project_item = projects_group.child(0)
        self.assertEqual(len(PROJECT_TEAM_ROLE_ORDER), project_item.childCount())
        self.assertEqual(
            [
                panel._project_team_snapshot["projects"][0]["threads"][index][
                    "role_title"
                ]
                for index in range(len(PROJECT_TEAM_ROLE_ORDER))
            ],
            [
                project_item.child(index).text(0).split(" · ", 1)[0]
                for index in range(project_item.childCount())
            ],
        )
        self.assertEqual("普通材质任务", tasks_group.child(0).text(0))
        self.assertFalse(any(
            "重复 worker" in tasks_group.child(index).text(0)
            for index in range(tasks_group.childCount())
        ))
        self.assertFalse(any(
            "worker-" in tree.itemText(index)
            for index in range(tree.count())
        ))

        project_item.setExpanded(False)
        panel._apply_threads(raw_threads)
        project_item = tree.topLevelItem(0).child(0)
        self.assertFalse(project_item.isExpanded())

        tree.scrollToItem(project_item)
        self.app.processEvents()
        QtTest.QTest.mouseClick(
            tree.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(project_item).center(),
        )
        self.assertEqual([], client.resume_requests)

        project_item.setExpanded(True)
        role_item = project_item.child(2)
        tree.scrollToItem(role_item)
        self.app.processEvents()
        QtTest.QTest.mouseClick(
            tree.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(role_item).center(),
        )
        expected_thread_id = f"worker-{PROJECT_TEAM_ROLE_ORDER[2]}"
        self.assertEqual([], client.resume_requests)
        self.assertEqual(expected_thread_id, tree.currentData()["thread_id"])

        QtTest.QTest.mouseDClick(
            tree.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(role_item).center(),
        )
        self.assertEqual(
            [(expected_thread_id, None, "session_resume")],
            client.resume_requests,
        )
        self.assertEqual(expected_thread_id, tree.currentData()["thread_id"])
        self.assertTrue(tree.currentData()["managed_project_thread"])

        panel._session_action_pending = False
        tree.setCurrentItem(project_item)
        other_thread_id = f"worker-{PROJECT_TEAM_ROLE_ORDER[4]}"
        panel._open_project_thread(other_thread_id)
        self.assertEqual(other_thread_id, tree.currentData()["thread_id"])
        self.assertEqual(other_thread_id, client.resume_requests[-1][0])

    def test_left_task_sidebar_collapse_and_expand_restore_width(self) -> None:
        panel = self._make_panel()
        panel.main_splitter.setSizes([245, 520, 235])
        self.app.processEvents()
        original_width = panel.main_splitter.sizes()[0]
        self.assertGreater(original_width, 0)

        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertFalse(panel.left_column.isVisible())
        self.assertEqual("展开任务栏", panel.history_sidebar_button.text())

        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertTrue(panel.left_column.isVisible())
        self.assertEqual("收起任务栏", panel.history_sidebar_button.text())
        self.assertLessEqual(
            abs(panel.main_splitter.sizes()[0] - original_width),
            12,
        )

        preferred_width = panel._last_left_sidebar_width
        panel.resize(780, 760)
        self.app.processEvents()
        constrained_width = panel.main_splitter.sizes()[0]
        self.assertLess(constrained_width, original_width)
        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertEqual(preferred_width, panel._last_left_sidebar_width)
        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertEqual(preferred_width, panel._last_left_sidebar_width)

        panel.resize(1_100, 760)
        self.app.processEvents()
        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertLessEqual(
            abs(panel.main_splitter.sizes()[0] - original_width),
            12,
        )

        panel.collapse_history_button.click()
        self.app.processEvents()
        self.assertFalse(panel.history_combo.topLevelItem(0).isExpanded())
        panel.expand_history_button.click()
        self.app.processEvents()
        self.assertTrue(panel.history_combo.topLevelItem(0).isExpanded())

    def test_early_supervisor_guidance_uses_project_action_and_exact_ack(
        self,
    ) -> None:
        panel = self._make_panel()
        client = _PanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel._selected_thread_id = "worker-supervisor"
        panel._selected_project_id = "project-qt"
        panel._current_task_route = "team"
        panel._remember_project_thread(
            "worker-supervisor",
            project_id="project-qt",
            role_key="supervisor",
            project_status="running",
        )
        panel._set_task_view("conversation")
        panel._refresh_controls()
        panel.input_edit.setPlainText("执行期间补充门洞宽度")
        panel.attachment_strip.add_path("E:/references/early-supervisor.png")

        panel._send()

        self.assertEqual(1, len(client.supervisor_guidance_requests))
        request = client.supervisor_guidance_requests[0]
        self.assertEqual("project-qt", request["expected_project_id"])
        self.assertEqual(
            ["E:/references/early-supervisor.png"],
            request["local_image_paths"],
        )
        self.assertEqual("执行期间补充门洞宽度", panel.input_edit.toPlainText())
        self.assertEqual(
            ["E:/references/early-supervisor.png"],
            panel.attachment_strip.paths(),
        )
        self.assertFalse(panel.send_button.isEnabled())
        self.assertEqual("发送指导中…", panel.send_button.text())

        panel._on_action_completed(
            request["context"],
            {
                "guidance_accepted": True,
                "guidance_id": "guidance-qt-1",
                "workflow_delivery": {
                    "mode": "steered",
                    "project_id": "project-qt",
                    "thread_id": "worker-execution",
                    "turn_id": "execution-live",
                    "role": "execution",
                },
                "guidance_target": {
                    "project_id": "project-qt",
                    "thread_id": "worker-supervisor",
                    "role": "supervisor",
                },
                "project_team": self._snapshot(mode="team"),
            },
        )

        self.assertEqual("", panel.input_edit.toPlainText())
        self.assertEqual([], panel.attachment_strip.paths())

    def test_running_project_role_switches_and_keeps_guidance_controls_live(
        self,
    ) -> None:
        panel = self._make_panel()
        client = _PanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel._apply_project_team_snapshot(self._snapshot(mode="team"))
        panel._apply_threads([
            {
                "thread_id": f"worker-{role}",
                "name": role,
                "updated_at": 1_752_825_600,
            }
            for role in PROJECT_TEAM_ROLE_ORDER
        ])
        panel._selected_thread_id = "worker-supervisor"
        panel._current_task_route = "team"
        panel._set_task_view("conversation")
        self.assertTrue(panel._turn_state.begin_start("worker-supervisor"))
        token = panel._turn_state.capture_token()
        self.assertTrue(panel._turn_state.acknowledge_start(
            token,
            "worker-supervisor",
            "supervisor-live",
        ))
        panel._stream_thread_id = "worker-supervisor"
        panel._stream_turn_id = "supervisor-live"
        panel._refresh_controls()
        self.app.processEvents()

        self.assertTrue(panel.history_combo.isEnabled())
        self.assertTrue(panel.model_combo.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertTrue(panel.add_image_button.isEnabled())

        tree = panel.history_combo
        projects_group = tree.topLevelItem(0)
        project_item = projects_group.child(0)
        projects_group.setExpanded(True)
        project_item.setExpanded(True)
        execution_item = project_item.child(2)
        tree.scrollToItem(execution_item)
        self.app.processEvents()
        QtTest.QTest.mouseClick(
            tree.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(execution_item).center(),
        )
        QtTest.QTest.mouseDClick(
            tree.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(execution_item).center(),
        )
        self.assertEqual(
            [("worker-execution", None, "session_resume")],
            client.resume_requests,
        )
        self.assertEqual("worker-supervisor", panel._turn_state.thread_id)
        self.assertTrue(panel._turn_state.busy)

        panel._on_action_completed(
            "session_resume",
            {
                "thread_id": "worker-execution",
                "turn_active": True,
                "turn_id": "execution-live",
                "turn_status": "inProgress",
                "read": {
                    "thread": {"id": "worker-execution", "turns": []}
                },
            },
        )
        self.assertIn("模式：项目角色 · 执行 AI", panel.goal_mode_label.text())
        panel.input_edit.setPlainText("运行中追加图片约束")
        panel.attachment_strip.add_path("E:/references/running.png")
        panel._send()

        self.assertEqual("worker-execution", panel._selected_thread_id)
        self.assertEqual("execution-live", panel._turn_state.turn_id)
        self.assertEqual(1, len(client.guidance_requests))
        self.assertEqual(
            ["E:/references/running.png"],
            client.guidance_requests[0]["local_image_paths"],
        )
        self.assertEqual("发送指导中…", panel.send_button.text())

        first_request = client.guidance_requests[0]
        panel._on_action_completed(
            first_request["context"],
            {"project_team": self._snapshot(mode="team")},
        )
        self.assertEqual("运行中追加图片约束", panel.input_edit.toPlainText())
        self.assertEqual(
            ["E:/references/running.png"],
            panel.attachment_strip.paths(),
        )

        panel._send()
        self.assertEqual(2, len(client.guidance_requests))
        second_request = client.guidance_requests[1]
        panel._on_action_completed(
            second_request["context"],
            {
                "guidance_accepted": True,
                "guidance_id": (
                    "guidance-0123456789abcdef0123456789abcdef"
                ),
                "workflow_delivery": {
                    "mode": "steered",
                    "project_id": "project-qt",
                    "thread_id": "worker-execution",
                    "turn_id": "execution-live",
                    "role": "execution",
                },
                "guidance_target": {
                    "project_id": "project-qt",
                    "thread_id": "worker-execution",
                    "role": "execution",
                },
                "project_team": self._snapshot(mode="team"),
            },
        )
        self.assertEqual("", panel.input_edit.toPlainText())
        self.assertEqual([], panel.attachment_strip.paths())
        self.assertIn(
            "下一次安全处理时生效",
            panel.conversation.toPlainText(),
        )

    def test_codex_status_card_keeps_readable_foreground_in_light_host(self) -> None:
        panel = self._make_panel()
        panel.conversation.append_codex_delta("项目状态应清晰可读")
        self.app.processEvents()

        card = panel.conversation._active_codex_card
        self.assertIsNotNone(card)
        self.assertIn("color: #e9edf2", card.body.styleSheet())

    def test_narrow_panel_keeps_manual_navigation_and_project_composer_usable(
        self,
    ) -> None:
        panel = self._make_panel()
        client = _PanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel._refresh_controls()

        self.assertLessEqual(panel.minimumSizeHint().width(), 360)
        host = QtWidgets.QWidget()
        host_layout = QtWidgets.QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel.setParent(host)
        host_layout.addWidget(panel)
        host.resize(470, 760)
        host.show()
        self.addCleanup(host.deleteLater)
        self.app.processEvents()

        self.assertLessEqual(host.width(), 470)
        self.assertLessEqual(panel.width(), 470)
        for button in (
            panel.history_sidebar_button,
            panel.task_sidebar_button,
            panel.project_team_sidebar_button,
        ):
            self.assertTrue(button.isVisible())
            right_edge = button.mapTo(panel, QtCore.QPoint(0, 0)).x()
            right_edge += button.width()
            self.assertLessEqual(right_edge, panel.width())

        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        QtTest.QTest.mouseClick(
            panel.task_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertFalse(panel.left_column.isVisible())
        self.assertFalse(panel.right_column.isVisible())
        self.assertEqual("展开任务栏", panel.history_sidebar_button.text())
        self.assertGreaterEqual(panel.center_column.width(), 360)

        QtTest.QTest.mouseClick(
            panel.history_sidebar_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertTrue(panel.left_column.isVisible())
        self.assertTrue(panel.new_project_button.isVisible())
        QtTest.QTest.mouseClick(
            panel.new_project_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertEqual("project_draft", panel._task_view_mode)
        self.assertTrue(panel.composer_panel.isVisible())
        self.assertEqual("创建项目并发送", panel.send_button.text())


if __name__ == "__main__":
    unittest.main()
