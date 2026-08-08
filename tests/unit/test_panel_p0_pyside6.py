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

if PYSIDE_AVAILABLE:  # pragma: no branch - exercised by Houdini's Python.
    from hia_panel.panel import HoudiniIntelligencePanel  # noqa: E402


def _snapshot(
    *,
    status: str = "planning",
    stop: bool = True,
    continue_: bool = False,
) -> dict:
    return {
        "schema": "hia-project-team/2",
        "projects": [
            {
                "project_id": "project-a",
                "title": "测试项目",
                "status": status,
                "stage": "stage-a",
                "attention_reason": "需要用户处理",
                "actions": {
                    "stop": stop,
                    "continue": continue_,
                    "append_guidance": status == "waiting_user",
                },
                "threads": [
                    {
                        "role": "planning",
                        "role_title": "方案",
                        "thread_id": "thread-planning",
                        "status": "waiting",
                        "actions": {"open_thread": True},
                    }
                ],
            }
        ],
    }


class _PanelClient:
    def __init__(self) -> None:
        self.role_reads: list[tuple[str, str]] = []
        self.resumes: list[tuple[str, str]] = []
        self.goal_reads: list[str] = []
        self.resume_result: str | None = "resume"

    def read_project_role_thread(self, thread_id: str, *, context: str) -> str:
        self.role_reads.append((thread_id, context))
        return "role-read"

    def resume_thread(
        self,
        thread_id: str,
        *,
        service_tier: str | None = None,
        context: str = "session_resume",
    ) -> str | None:
        del service_tier
        self.resumes.append((thread_id, context))
        return self.resume_result

    def get_goal(self, thread_id: str) -> str:
        self.goal_reads.append(thread_id)
        return "goal-read"

    def dispose(self) -> None:
        pass


class _NavigationController:
    def __init__(self) -> None:
        self.selected: list[str] = []
        self.snapshots: list[list[dict]] = []

    def select_ordinary_thread_when_available(self, thread_id: str) -> None:
        self.selected.append(thread_id)

    def consume_ordinary_threads(self, records: list[dict]) -> None:
        self.snapshots.append(list(records))

    def close(self) -> None:
        pass


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is available in Houdini/runtime Qt")
class PanelP0PySide6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _panel(self):
        with mock.patch.dict(
            os.environ,
            {"HIA_BRIDGE_URL": "", "HIA_BRIDGE_TOKEN": ""},
        ):
            panel = HoudiniIntelligencePanel(hou_module=None)
        panel.resize(1200, 800)
        panel.show()
        self.app.processEvents()
        return panel

    def _close(self, widget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def test_rotation_event_updates_real_panel_without_render_failure(self) -> None:
        panel = self._panel()
        try:
            controller = _NavigationController()
            panel._project_team_controller = controller
            panel._selected_thread_id = "thread-old"
            panel._thread_history = [
                {
                    "thread_id": "thread-old",
                    "name": "普通任务",
                    "updated_at": 1,
                }
            ]
            panel._current_goal = {
                "threadId": "thread-old",
                "objective": "目标",
                "status": "active",
            }
            failures: list[str] = []
            panel._request_session_reconciliation = failures.append

            panel._on_events(
                {
                    "events": [
                        {
                            "seq": 41,
                            "type": "thread_rotated",
                            "oldThreadId": "thread-old",
                            "newThreadId": "thread-new",
                        }
                    ]
                }
            )

            self.assertEqual("thread-new", panel._selected_thread_id)
            self.assertEqual(
                ["thread-new"],
                [record["thread_id"] for record in panel._thread_history],
            )
            self.assertEqual("thread-new", panel._current_goal["threadId"])
            self.assertIn("thread-new", panel.thread_status_label.toolTip())
            self.assertEqual(["thread-new"], controller.selected)
            self.assertEqual(41, panel._event_sequence)
            self.assertFalse(
                any(item.startswith("event_render_failure_") for item in failures)
            )
        finally:
            self._close(panel)

    def test_project_actions_follow_only_backend_actions(self) -> None:
        state = ProjectPanelState()
        view = ProjectTeamView(state)
        view.resize(520, 760)
        view.show()
        try:
            cases = (
                ("planning", True, False),
                ("executing", True, False),
                ("reviewing", True, False),
                ("stopped", False, True),
                ("waiting_user", False, True),
                ("completed", False, False),
                ("failed", False, False),
            )
            for status, can_stop, can_continue in cases:
                with self.subTest(status=status):
                    state.apply_snapshot(
                        _snapshot(
                            status=status,
                            stop=can_stop,
                            continue_=can_continue,
                        )
                    )
                    state.select("project:project-a")
                    view.refresh_view()
                    self.app.processEvents()
                    self.assertTrue(view.project_action_surface.isVisible())
                    self.assertEqual(can_stop, view.stop_button.isVisible())
                    self.assertEqual(can_stop, view.stop_button.isEnabled())
                    self.assertEqual(
                        can_continue,
                        view.continue_button.isVisible(),
                    )
                    self.assertEqual(
                        can_continue,
                        view.continue_button.isEnabled(),
                    )
        finally:
            self._close(view)

    def test_left_collapse_keeps_reparented_project_surfaces_visible(self) -> None:
        panel = self._panel()
        try:
            view = panel.project_team_view
            view.state.apply_snapshot(
                _snapshot(status="waiting_user", stop=False, continue_=True)
            )
            view.state.select("project:project-a")
            view.refresh_view()
            team_page = view.project_action_surface.parentWidget()
            panel.task_tabs.setCurrentWidget(team_page)
            self.app.processEvents()
            self.assertIs(team_page, view.detail_surface.parentWidget())
            self.assertIs(team_page, view.attention_surface.parentWidget())
            self.assertTrue(view.project_action_surface.isVisible())
            self.assertTrue(view.detail_surface.isVisible())
            self.assertTrue(view.attention_surface.isVisible())

            view.collapse_button.setChecked(True)
            self.app.processEvents()

            self.assertFalse(view.navigation_body.isVisible())
            self.assertFalse(view.title_label.isVisible())
            self.assertTrue(view.project_action_surface.isVisible())
            self.assertTrue(view.detail_surface.isVisible())
            self.assertTrue(view.attention_surface.isVisible())
        finally:
            self._close(panel)

    def test_active_ordinary_turn_rejects_project_role_read(self) -> None:
        panel = self._panel()
        try:
            client = _PanelClient()
            panel._client = client
            panel._selected_thread_id = "thread-ordinary"
            panel.project_team_view.state.apply_snapshot(
                _snapshot(),
                [{"thread_id": "thread-ordinary", "name": "普通任务"}],
            )
            panel.project_team_view.state.select("role:project-a:planning")
            panel.project_team_view.refresh_view()
            panel.conversation.add_user_message("保留普通聊天", ())
            self.assertTrue(panel._turn_state.begin_start("thread-ordinary"))
            token = panel._turn_state.capture_token()
            self.assertTrue(
                panel._turn_state.acknowledge_start(
                    token,
                    "thread-ordinary",
                    "turn-active",
                )
            )

            panel._open_project_role_thread("thread-planning")

            self.assertEqual([], client.role_reads)
            text = panel.conversation.toPlainText()
            self.assertIn("保留普通聊天", text)
            self.assertIn(
                "当前普通 Turn 正在运行，结束或停止后再查看项目角色。",
                text,
            )
            self.assertIsNone(panel._visible_project_role_thread_id)
        finally:
            self._close(panel)

    def test_role_context_is_read_only_and_return_restores_ordinary_chat(self) -> None:
        panel = self._panel()
        try:
            client = _PanelClient()
            controller = _NavigationController()
            panel._client = client
            panel._project_team_controller = controller
            panel._connected = True
            panel._authenticated = True
            panel._selected_thread_id = "thread-ordinary"
            panel._thread_history = [
                {"thread_id": "thread-ordinary", "name": "普通任务"}
            ]
            panel.project_team_view.state.apply_snapshot(
                _snapshot(),
                panel._thread_history,
            )
            panel.project_team_view.state.select("role:project-a:planning")
            panel.project_team_view.refresh_view()

            panel._open_project_role_thread("thread-planning")
            self.assertEqual(
                [("thread-planning", "project_role_read:thread-planning")],
                client.role_reads,
            )
            panel._on_action_completed(
                "project_role_read:thread-planning",
                {
                    "read": {
                        "thread": {
                            "id": "thread-planning",
                            "turns": [
                                {
                                    "items": [
                                        {
                                            "type": "agentMessage",
                                            "text": "项目角色历史",
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                },
            )

            self.assertEqual(
                "thread-planning",
                panel._visible_project_role_thread_id,
            )
            self.assertTrue(panel.project_role_banner.isVisible())
            self.assertEqual(
                "只读查看：测试项目 / 方案",
                panel.project_role_banner_label.text(),
            )
            self.assertTrue(panel.return_to_ordinary_button.isVisible())
            self.assertFalse(panel.input_edit.isEnabled())
            self.assertFalse(panel.send_button.isEnabled())
            self.assertFalse(panel.add_image_button.isEnabled())
            self.assertFalse(panel.include_selection_checkbox.isEnabled())
            self.assertFalse(panel.attachment_strip.isEnabled())
            self.assertFalse(panel.goal_save_button.isEnabled())
            self.assertFalse(panel.goal_focus_checkbox.isEnabled())
            self.assertFalse(panel.knowledge_import_thread_button.isEnabled())

            panel.project_memory_title_edit.setText("记录")
            panel.project_memory_body_edit.setPlainText("正文")
            memory_values = panel._project_memory_form_values()
            self.assertIsNotNone(memory_values)
            self.assertNotIn("source_thread_id", memory_values)

            before = panel.conversation.toPlainText()
            self.assertTrue(panel._turn_state.begin_start("thread-ordinary"))
            token = panel._turn_state.capture_token()
            self.assertTrue(
                panel._turn_state.acknowledge_start(
                    token,
                    "thread-ordinary",
                    "turn-hidden",
                )
            )
            panel._stream_thread_id = "thread-ordinary"
            panel._stream_turn_id = "turn-hidden"
            panel._render_event(
                {
                    "type": "codex_notification",
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-ordinary",
                        "turnId": "turn-hidden",
                        "itemId": "message-hidden",
                        "delta": "不应进入角色聊天",
                    },
                }
            )
            self.assertEqual(before, panel.conversation.toPlainText())

            panel._turn_state = type(panel._turn_state)()
            panel._stream_thread_id = None
            panel._stream_turn_id = None
            client.resume_result = None
            panel._return_to_ordinary_task()
            self.assertFalse(panel._session_action_pending)
            self.assertEqual(
                "thread-planning",
                panel._visible_project_role_thread_id,
            )
            self.assertIn(
                "返回普通任务失败：当前请求尚未结束或 Bridge 不可用。",
                panel.conversation.toPlainText(),
            )

            client.resume_result = "resume"
            panel._return_to_ordinary_task()
            self.assertEqual(
                [
                    ("thread-ordinary", "session_resume"),
                    ("thread-ordinary", "session_resume"),
                ],
                client.resumes,
            )
            self.assertEqual(
                "thread-planning",
                panel._visible_project_role_thread_id,
            )
            self.assertFalse(panel.send_button.isEnabled())

            panel._on_action_completed(
                "session_resume",
                {
                    "thread_id": "thread-ordinary",
                    "focus_mode": False,
                    "read": {
                        "thread": {
                            "id": "thread-ordinary",
                            "turns": [
                                {
                                    "items": [
                                        {
                                            "type": "agentMessage",
                                            "text": "普通聊天已恢复",
                                        }
                                    ]
                                }
                            ],
                        }
                    },
                },
            )

            self.assertIsNone(panel._visible_project_role_thread_id)
            self.assertFalse(panel.project_role_banner.isVisible())
            self.assertIn("普通聊天已恢复", panel.conversation.toPlainText())
            self.assertNotIn("项目角色历史", panel.conversation.toPlainText())
            self.assertTrue(panel.input_edit.isEnabled())
            self.assertIn("thread-ordinary", controller.selected)
        finally:
            self._close(panel)


if __name__ == "__main__":
    unittest.main()
