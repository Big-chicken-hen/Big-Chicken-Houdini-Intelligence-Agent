from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - the standard test interpreter has no Qt.
    PYSIDE_AVAILABLE = False
else:
    PYSIDE_AVAILABLE = True
    from hia_panel.panel import HoudiniIntelligencePanel


class _OrdinaryPanelClient:
    def __init__(self) -> None:
        self.thread_starts: list[dict] = []
        self.thread_resumes: list[tuple[str, str]] = []
        self.thread_renames: list[tuple[str, str, str]] = []
        self.thread_deletes: list[tuple[str, str]] = []
        self.goal_reads: list[str] = []
        self.thread_list_requests = 0
        self.disposed = False

    def start_thread(self, **kwargs) -> str:
        self.thread_starts.append(dict(kwargs))
        return "request-start"

    def resume_thread(
        self,
        thread_id: str,
        *,
        service_tier: str | None = None,
        context: str = "session_resume",
    ) -> str:
        del service_tier
        self.thread_resumes.append((thread_id, context))
        return "request-resume"

    def rename_thread(self, thread_id: str, name: str, *, context: str) -> str:
        self.thread_renames.append((thread_id, name, context))
        return "request-rename"

    def delete_thread(self, thread_id: str, *, context: str) -> str:
        self.thread_deletes.append((thread_id, context))
        return "request-delete"

    def get_threads(self) -> str:
        self.thread_list_requests += 1
        return "request-threads"

    def get_goal(self, thread_id: str) -> str:
        self.goal_reads.append(thread_id)
        return "request-goal"

    def dispose(self) -> None:
        self.disposed = True


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is available in Houdini/runtime Qt")
class OrdinaryPanelP0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _panel(self):
        with mock.patch.dict(
            os.environ,
            {"HIA_BRIDGE_URL": "", "HIA_BRIDGE_TOKEN": ""},
        ):
            panel = HoudiniIntelligencePanel(hou_module=None)
        client = _OrdinaryPanelClient()
        panel._client = client
        panel._connected = True
        panel._authenticated = True
        panel.resize(1200, 800)
        panel.show()
        panel._refresh_controls()
        self.app.processEvents()
        return panel, client

    def _close(self, panel) -> None:
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def _unlock_session_action(self, panel) -> None:
        panel._session_action_pending = False
        panel._threads_requested = False
        panel._refresh_controls()
        self.app.processEvents()

    def test_rotation_event_updates_real_panel_without_render_failure(self) -> None:
        panel, client = self._panel()
        try:
            panel._selected_thread_id = "thread-old"
            panel._apply_threads(
                [
                    {
                        "thread_id": "thread-old",
                        "name": "Ordinary task",
                        "updated_at": 1,
                    }
                ],
                preferred_thread_id="thread-old",
            )
            panel._current_goal = {
                "threadId": "thread-old",
                "objective": "Finish the ordinary task",
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

            self.assertEqual(41, panel._event_sequence)
            self.assertEqual("thread-new", panel._selected_thread_id)
            self.assertEqual(
                ["thread-new"],
                [record["thread_id"] for record in panel._thread_history],
            )
            self.assertEqual("thread-new", panel._current_goal["threadId"])
            self.assertEqual(1, panel.history_tree.topLevelItemCount())
            self.assertEqual(
                "thread-new",
                panel.history_tree.topLevelItem(0).data(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                ),
            )
            self.assertIn("thread-new", panel.thread_status_label.toolTip())
            self.assertIn("thread-new", client.goal_reads)
            self.assertFalse(
                any(item.startswith("event_render_failure_") for item in failures)
            )
        finally:
            self._close(panel)

    def test_ordinary_history_actions_are_visible_and_wired(self) -> None:
        panel, client = self._panel()
        try:
            panel._selected_thread_id = "thread-a"
            panel._apply_threads(
                [
                    {
                        "thread_id": "thread-a",
                        "name": "Task A",
                        "preview": "ordinary history",
                        "updated_at": 1,
                    }
                ],
                preferred_thread_id="thread-a",
            )
            panel._refresh_controls()
            self.app.processEvents()

            self.assertTrue(panel.history_tree.isVisible())
            fixed_controls = (
                "new_thread_button",
                "task_creation_feedback_label",
                "thread_name_edit",
                "open_thread_button",
                "rename_thread_button",
                "copy_thread_id_button",
                "refresh_threads_button",
                "delete_thread_button",
            )
            for name in fixed_controls:
                with self.subTest(control=name):
                    self.assertTrue(hasattr(panel, name))
                    self.assertTrue(getattr(panel, name).isVisible())

            panel.new_thread_button.click()
            self.assertEqual(1, len(client.thread_starts))
            self._unlock_session_action(panel)

            panel.open_thread_button.click()
            self.assertEqual([("thread-a", "session_resume")], client.thread_resumes)
            self._unlock_session_action(panel)

            panel.thread_name_edit.setText("Renamed ordinary task")
            panel.rename_thread_button.click()
            self.assertEqual(1, len(client.thread_renames))
            self.assertEqual(
                ("thread-a", "Renamed ordinary task"),
                client.thread_renames[0][:2],
            )
            self._unlock_session_action(panel)

            panel.copy_thread_id_button.click()
            self.assertEqual("thread-a", QtWidgets.QApplication.clipboard().text())

            panel.refresh_threads_button.click()
            self.assertEqual(1, client.thread_list_requests)
            self._unlock_session_action(panel)

            panel.delete_thread_button.click()
            self.assertEqual([], client.thread_deletes)
            panel.delete_thread_button.click()
            self.assertEqual(1, len(client.thread_deletes))
            self.assertEqual("thread-a", client.thread_deletes[0][0])
        finally:
            self._close(panel)

    def test_panel_exposes_only_ordinary_task_navigation(self) -> None:
        panel, _client = self._panel()
        try:
            forbidden_tokens = ("project_team", "project_role", "project_draft")
            forbidden = [
                name
                for name in dir(panel)
                if any(token in name for token in forbidden_tokens)
            ]
            self.assertEqual([], forbidden)

            tab_titles = [
                panel.task_tabs.tabText(index)
                for index in range(panel.task_tabs.count())
            ]
            self.assertNotIn("项目团队", tab_titles)
            self.assertFalse(any("项目" in title for title in tab_titles))

            self.assertEqual(0, panel.history_tree.topLevelItemCount())
            panel._apply_threads(
                [
                    {
                        "thread_id": "ordinary-a",
                        "name": "Ordinary A",
                        "updated_at": 1,
                    },
                    {
                        "thread_id": "ordinary-b",
                        "name": "Ordinary B",
                        "updated_at": 2,
                    },
                ],
                preferred_thread_id="ordinary-b",
            )

            self.assertEqual(
                ["ordinary-a", "ordinary-b"],
                [record["thread_id"] for record in panel._thread_history],
            )
            self.assertEqual(2, panel.history_tree.topLevelItemCount())
            self.assertEqual(
                ["ordinary-a", "ordinary-b"],
                [
                    panel.history_tree.topLevelItem(index).data(
                        0,
                        QtCore.Qt.ItemDataRole.UserRole,
                    )
                    for index in range(panel.history_tree.topLevelItemCount())
                ],
            )
            self.assertEqual("ordinary-b", panel._history_selected_thread_id())
        finally:
            self._close(panel)


if __name__ == "__main__":
    unittest.main()
