from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.unit.test_panel_wiring import _CloseEvent, _make_panel
from hia_panel.project_team import ProjectPanelState


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
        self.events = []
        self.ordinary_snapshots = []
        self.pending_ordinary_selections = []
        self.guidance_calls = []
        self.guidance_result = True

    def refresh(self) -> None:
        self.refresh_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def consume_project_team_update(self, event) -> bool:
        self.events.append(event)
        return True

    def consume_ordinary_threads(self, threads) -> None:
        self.ordinary_snapshots.append(threads)

    def select_ordinary_thread_when_available(self, thread_id) -> None:
        self.pending_ordinary_selections.append(thread_id)

    def submit_guidance(self, **kwargs) -> bool:
        self.guidance_calls.append(kwargs)
        return self.guidance_result


class PanelProjectTeamWiringTests(unittest.TestCase):
    @staticmethod
    def _project_snapshot(*, can_guide=True):
        return {
            "schema": "hia-project-team/2",
            "settings": {"mode": "team", "writable": True},
            "projects": [
                {
                    "project_id": "project-house",
                    "title": "House",
                    "status": "planning" if can_guide else "completed",
                    "stage": "planning",
                    "actions": {"append_guidance": can_guide},
                    "threads": [
                        {
                            "role": "supervisor",
                            "role_title": "Supervisor",
                            "thread_id": "thread-supervisor",
                            "status": "waiting",
                            "actions": {
                                "open_thread": True,
                                "append_guidance": can_guide,
                                "set_role_runtime": can_guide,
                            },
                        }
                    ],
                }
            ],
        }

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

    def test_single_creation_ack_selects_new_ordinary_and_keeps_composer(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        controller = _Controller()
        panel._project_team_controller = controller
        panel.input_edit.setPlainText("下一条普通任务消息")

        panel._on_action_completed(
            "session_start",
            {
                "thread_id": "ordinary-new",
                "focus_mode": False,
            },
        )

        self.assertEqual("ordinary-new", panel._selected_thread_id)
        self.assertEqual(["ordinary-new"], controller.pending_ordinary_selections)
        self.assertTrue(panel.input_edit.isVisible())
        self.assertTrue(panel.input_edit.isEnabled())

    def test_thread_history_is_forwarded_to_visible_workspace_tree(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        controller = _Controller()
        panel._project_team_controller = controller
        threads = [
            {"thread_id": "ordinary-new", "name": "普通任务", "updated_at": 2}
        ]

        panel._apply_threads(threads)

        self.assertEqual([threads], controller.ordinary_snapshots)

    def test_thread_history_does_not_truncate_after_twenty_items(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        controller = _Controller()
        panel._project_team_controller = controller
        threads = [
            {
                "thread_id": f"ordinary-{index}",
                "name": f"普通任务 {index}",
                "updated_at": index,
            }
            for index in range(44)
        ]

        panel._apply_threads(threads)

        self.assertEqual(44, len(panel._thread_history))
        self.assertEqual(44, len(controller.ordinary_snapshots[-1]))

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

    def test_live_project_update_is_forwarded_to_project_controller(self) -> None:
        panel = _make_panel()
        controller = _Controller()
        panel._project_team_controller = controller
        event = {
            "type": "project_team_updated",
            "project_team": {"schema": "hia-project-team/2", "projects": []},
        }

        panel._render_event(event)

        self.assertEqual([event], controller.events)

    def test_second_project_message_routes_to_guidance_not_ordinary_turn(self) -> None:
        panel = _make_panel(selected_thread_id="thread-supervisor")
        controller = _Controller()
        panel._project_team_controller = controller
        panel.project_team_view = SimpleNamespace(state=ProjectPanelState())
        panel.project_team_view.state.apply_snapshot(self._project_snapshot())
        panel.project_team_view.state.select("role:project-house:supervisor")
        panel.input_edit.setPlainText("change the roof")

        panel._send()

        self.assertEqual(
            [
                {
                    "project_id": "project-house",
                    "thread_id": "thread-supervisor",
                    "text": "change the roof",
                }
            ],
            controller.guidance_calls,
        )
        self.assertEqual([], panel._client.turn_requests)
        self.assertEqual("", panel.input_edit.toPlainText())

    def test_finished_project_message_is_preserved_without_raw_turn_error(self) -> None:
        panel = _make_panel(selected_thread_id="thread-supervisor")
        controller = _Controller()
        panel._project_team_controller = controller
        panel.project_team_view = SimpleNamespace(state=ProjectPanelState())
        panel.project_team_view.state.apply_snapshot(
            self._project_snapshot(can_guide=False)
        )
        panel.project_team_view.state.select("role:project-house:supervisor")
        panel.input_edit.setPlainText("follow up")

        panel._send()

        self.assertEqual([], controller.guidance_calls)
        self.assertEqual([], panel._client.turn_requests)
        self.assertEqual("follow up", panel.input_edit.toPlainText())

    def test_close_event_closes_controller_before_client_disposal(self) -> None:
        panel = _make_panel()
        controller = _Controller()
        panel._project_team_controller = controller

        panel.closeEvent(_CloseEvent())

        self.assertEqual(1, controller.close_calls)
        self.assertIsNone(panel._project_team_controller)


if __name__ == "__main__":
    unittest.main()
