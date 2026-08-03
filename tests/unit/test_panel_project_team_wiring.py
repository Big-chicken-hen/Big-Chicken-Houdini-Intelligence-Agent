from __future__ import annotations

from tests.unit.test_panel_wiring import _CloseEvent, _make_panel
import unittest
from types import SimpleNamespace

from hia_panel.project_team import ProjectPanelState


class _TeamClient:
    def __init__(self) -> None:
        self.project_calls: list[tuple[str, dict]] = []
        self.role_reads: list[tuple[str, str]] = []
        self.turn_requests: list[tuple[str, dict]] = []
        self.result: str | None = "project-intake-request"

    def start_project(self, text: str, **kwargs):
        self.project_calls.append((text, kwargs))
        return self.result

    def read_project_role_thread(self, thread_id: str, *, context: str):
        self.role_reads.append((thread_id, context))
        return "project-role-read"

    def start_turn(self, text: str, **kwargs):
        self.turn_requests.append((text, kwargs))
        return "ordinary-turn"

    def dispose(self) -> None:
        pass


class _Controller:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.close_calls = 0
        self.events = []
        self.ordinary_snapshots = []
        self.pending_ordinary_selections = []

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


def _project_snapshot():
    return {
        "schema": "hia-project-team/2",
        "settings": {"mode": "team", "writable": True},
        "projects": [
            {
                "project_id": "project-house",
                "title": "House",
                "status": "planning",
                "stage": "planning",
                "actions": {"append_guidance": True},
                "threads": [
                    {
                        "role": "planning",
                        "role_title": "Planning",
                        "thread_id": "thread-planning",
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


class PanelProjectTeamWiringTests(unittest.TestCase):
    def _team_panel(self, *, selected_thread_id="ordinary-thread"):
        panel = _make_panel(selected_thread_id=selected_thread_id)
        panel._project_team_controller = _Controller()
        panel._client = _TeamClient()
        return panel

    def test_team_submit_uses_dedicated_project_start_without_ordinary_thread(self) -> None:
        panel = self._team_panel(selected_thread_id=None)
        panel._on_project_team_new_task("team")
        panel.input_edit.setPlainText("根据参考图建造完整住宅")

        panel._send()

        self.assertIsNone(panel._selected_thread_id)
        self.assertEqual(1, len(panel._client.project_calls))
        text, options = panel._client.project_calls[0]
        self.assertEqual("根据参考图建造完整住宅", text)
        self.assertEqual("draft-1", options["attachment_draft_id"])
        self.assertNotIn("team_override", options)
        self.assertEqual([], panel._client.turn_requests)

    def test_project_success_restores_unrelated_ordinary_composer_draft(self) -> None:
        panel = self._team_panel()
        panel.input_edit.setPlainText("ordinary draft")
        panel.attachment_strip.add_path("ordinary.png")
        panel._on_project_team_new_task("team")
        panel.input_edit.setPlainText("project task")
        panel._send()
        context = panel._client.project_calls[0][1]["context"]

        panel._on_action_completed(
            context,
            {"ok": True, "project_id": "project-house"},
        )

        self.assertEqual("ordinary draft", panel.input_edit.toPlainText())
        self.assertEqual(("ordinary.png",), panel._attachment_paths())
        self.assertFalse(panel._project_draft_active)
        self.assertEqual(1, panel._project_team_controller.refresh_calls)

    def test_project_failure_preserves_exact_project_draft(self) -> None:
        panel = self._team_panel(selected_thread_id=None)
        panel._on_project_team_new_task("team")
        panel.input_edit.setPlainText("project task")
        panel._send()
        context = panel._client.project_calls[0][1]["context"]

        panel._on_request_failed(
            context,
            {"structured_error": {"code": "NETWORK_TIMEOUT", "message": "timeout"}},
        )

        self.assertEqual("project task", panel.input_edit.toPlainText())
        self.assertTrue(panel._project_draft_active)
        self.assertEqual("draft-1", panel._project_draft_id)

    def test_role_view_uses_readonly_endpoint_and_preserves_ordinary_selection(self) -> None:
        panel = self._team_panel()
        panel.project_team_view = SimpleNamespace(state=ProjectPanelState())
        panel.project_team_view.state.apply_snapshot(_project_snapshot())
        panel.project_team_view.state.select("role:project-house:planning")

        panel._open_project_role_thread("thread-planning")

        self.assertEqual("ordinary-thread", panel._selected_thread_id)
        self.assertEqual(
            [("thread-planning", "project_role_read:thread-planning")],
            panel._client.role_reads,
        )
        self.assertEqual([], panel._client.turn_requests)

    def test_role_read_response_preserves_ordinary_goal_focus_attachments_and_turn(self) -> None:
        panel = self._team_panel()
        panel.project_team_view = SimpleNamespace(state=ProjectPanelState())
        panel.project_team_view.state.apply_snapshot(_project_snapshot())
        panel.project_team_view.state.select("role:project-house:planning")
        panel._current_goal = {"objective": "ordinary goal", "status": "active"}
        panel._focus_mode = True
        panel.attachment_strip.add_path("ordinary.png")
        self.assertTrue(panel._turn_state.begin_start("ordinary-thread"))
        token = panel._turn_state.capture_token()
        self.assertTrue(
            panel._turn_state.acknowledge_start(
                token,
                "ordinary-thread",
                "ordinary-turn",
            )
        )
        panel._stream_thread_id = "ordinary-thread"
        panel._stream_turn_id = "ordinary-turn"
        before_turn = panel._turn_state.capture_token()

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
                                        "text": '{"schema":"hia-project-plan/1","requirements":[],"stages":[]}',
                                    }
                                ]
                            }
                        ],
                    }
                }
            },
        )

        self.assertEqual("ordinary-thread", panel._selected_thread_id)
        self.assertEqual(
            {"objective": "ordinary goal", "status": "active"},
            panel._current_goal,
        )
        self.assertTrue(panel._focus_mode)
        self.assertEqual(["ordinary.png"], panel.attachment_strip.paths())
        self.assertEqual(before_turn, panel._turn_state.capture_token())
        self.assertEqual("ordinary-thread", panel._stream_thread_id)
        self.assertEqual("ordinary-turn", panel._stream_turn_id)

    def test_central_composer_never_turns_into_project_guidance(self) -> None:
        panel = self._team_panel()
        panel.project_team_view = SimpleNamespace(state=ProjectPanelState())
        panel.project_team_view.state.apply_snapshot(_project_snapshot())
        panel.project_team_view.state.select("role:project-house:planning")
        panel.input_edit.setPlainText("ordinary follow-up")

        panel._send()

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertEqual("ordinary follow-up", panel._client.turn_requests[0][0])

    def test_thread_history_has_one_owner_and_is_forwarded_to_project_tree(self) -> None:
        panel = self._team_panel(selected_thread_id=None)
        threads = [
            {"thread_id": f"ordinary-{index}", "name": f"普通任务 {index}"}
            for index in range(44)
        ]

        panel._apply_threads(threads)

        self.assertEqual(44, len(panel._thread_history))
        self.assertEqual([threads], panel._project_team_controller.ordinary_snapshots)

    def test_close_event_closes_controller_before_client_disposal(self) -> None:
        panel = self._team_panel()
        controller = panel._project_team_controller

        panel.closeEvent(_CloseEvent())

        self.assertEqual(1, controller.close_calls)
        self.assertIsNone(panel._project_team_controller)


if __name__ == "__main__":
    unittest.main()
