from __future__ import annotations

import unittest

from tests.unit.test_panel_wiring import _CloseEvent, _make_panel


class _TeamClient:
    def __init__(self) -> None:
        self.calls = []
        self.result = "project-intake-request"

    def start_turn(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.result


class _Controller:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.close_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class PanelProjectTeamWiringTests(unittest.TestCase):
    def _team_panel(self):
        panel = _make_panel(selected_thread_id=None)
        panel._new_task_route = "team"
        panel._pending_team_drafts = {}
        panel._project_team_controller = _Controller()
        panel._client = _TeamClient()
        return panel

    def test_team_submit_requires_no_selected_ordinary_thread(self) -> None:
        panel = self._team_panel()
        panel.input_edit.setPlainText("根据参考图建造完整住宅")

        panel._send()

        self.assertIsNone(panel._selected_thread_id)
        self.assertEqual(1, len(panel._client.calls))
        text, options = panel._client.calls[0]
        self.assertEqual("根据参考图建造完整住宅", text)
        self.assertEqual("team", options["team_override"])
        self.assertTrue(options["context"].startswith("project_team_create:"))

    def test_single_creation_keeps_existing_native_thread_start(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        before = len(panel._client.thread_requests)

        panel._on_project_team_new_task("single")

        self.assertEqual(before + 1, len(panel._client.thread_requests))
        self.assertIsNone(panel._new_task_route)

    def test_project_ack_clears_exact_draft_and_refreshes_tree(self) -> None:
        panel = self._team_panel()
        panel.input_edit.setPlainText("建造木屋")
        panel._send()
        context = panel._client.calls[0][1]["context"]

        panel._on_action_completed(
            context,
            {"ok": True, "project_id": "project-house", "routing": "team"},
        )

        self.assertEqual("", panel.input_edit.toPlainText())
        self.assertIsNone(panel._new_task_route)
        self.assertFalse(panel._turn_start_request_pending)
        self.assertEqual(1, panel._project_team_controller.refresh_calls)

    def test_project_failure_preserves_draft_for_explicit_retry(self) -> None:
        panel = self._team_panel()
        panel.input_edit.setPlainText("建造木屋")
        panel._send()
        context = panel._client.calls[0][1]["context"]

        panel._on_request_failed(
            context,
            {
                "structured_error": {
                    "code": "NETWORK_TIMEOUT",
                    "message": "Bridge request timed out",
                }
            },
        )

        self.assertEqual("建造木屋", panel.input_edit.toPlainText())
        self.assertEqual("team", panel._new_task_route)
        self.assertFalse(panel._turn_start_request_pending)

    def test_open_role_reuses_existing_resume_path(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        panel._new_task_route = "team"

        panel._open_project_role_thread("thread-visual-review")

        self.assertEqual(
            [("thread-visual-review", None, "session_resume")],
            panel._client.resume_requests,
        )
        self.assertIsNone(panel._new_task_route)

    def test_close_event_closes_controller_before_client_disposal(self) -> None:
        panel = _make_panel()
        controller = _Controller()
        panel._project_team_controller = controller

        panel.closeEvent(_CloseEvent())

        self.assertEqual(1, controller.close_calls)
        self.assertIsNone(panel._project_team_controller)


if __name__ == "__main__":
    unittest.main()
