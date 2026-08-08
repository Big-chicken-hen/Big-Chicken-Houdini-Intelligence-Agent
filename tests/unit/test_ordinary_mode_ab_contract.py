from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "bridge"))

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.http_server import BridgeApplication, LoopbackHTTPServer


class _OrdinaryOnlySession:
    def __init__(self) -> None:
        self.turns: list[dict] = []

    def start_turn(self, **kwargs):
        self.turns.append(dict(kwargs))
        return {"thread_id": "ordinary-thread", "turn_id": "ordinary-turn"}


class OrdinaryModeContractTests(unittest.TestCase):
    """Keep the ordinary product path executable after removing team mode."""

    def run_case(self, module_name: str, class_name: str, method: str) -> None:
        case = getattr(import_module(module_name), class_name)
        result = unittest.TestResult()
        case(method).run(result)
        details = [str(error) for _, error in result.failures + result.errors]
        self.assertEqual([], details)
        self.assertEqual(1, result.testsRun)

    def exercise_ordinary_http(self) -> _OrdinaryOnlySession:
        token = "ordinary-contract-token-with-at-least-thirty-two-chars"
        session = _OrdinaryOnlySession()
        application = BridgeApplication(session, EventBuffer(), token)
        server = LoopbackHTTPServer(("127.0.0.1", 0), application)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
        try:
            request = Request(
                base_url + "/v1/turn",
                data=json.dumps({"text": "ordinary survives"}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual("ordinary-thread", payload["thread_id"])
            self.assertEqual("ordinary survives", session.turns[0]["text"])
            return session
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_01_new_ordinary_thread(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionNativeToolPolicyTests",
            "test_thread_start_enables_workspace_write_with_native_hython_instructions",
        )

    def test_02_open_ordinary_thread(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionNativeToolPolicyTests",
            "test_thread_resume_enables_workspace_write_without_runtime_approval",
        )

    def test_03_text_turn(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_http",
            "BridgeHTTPTests",
            "test_unicode_model_effort_and_service_tier_are_forwarded_without_loss",
        )

    def test_04_image_turn(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionImageInputTests",
            "test_text_and_images_share_turn_start_input_in_order",
        )

    def test_05_image_only_turn(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_http",
            "BridgeHTTPTests",
            "test_turn_endpoint_accepts_image_only_without_invented_text",
        )

    def test_06_active_turn_steer(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionSteerTests",
            "test_steer_uses_expected_turn_and_does_not_change_lifecycle",
        )

    def test_07_stop(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionTurnStateTests",
            "test_interrupt_completion_within_grace_is_terminal",
        )

    def test_08_goal(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionGoalTests",
            "test_goal_round_trip_uses_selected_native_thread",
        )

    def test_09_focus(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionGoalTests",
            "test_focus_mode_requires_active_goal_and_persists_per_thread",
        )

    def test_10_rename(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionThreadHistoryTests",
            "test_rename_thread_forwards_the_original_name",
        )

    def test_11_copy_thread_id_ui(self) -> None:
        self.run_case(
            "tests.unit.test_panel_p0_pyside6",
            "OrdinaryPanelP0Tests",
            "test_ordinary_history_actions_are_visible_and_wired",
        )

    def test_12_delete_and_attachment_cleanup(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionThreadHistoryTests",
            "test_delete_removes_only_the_exact_thread_attachment_cache",
        )

    def test_13_history_refresh(self) -> None:
        self.run_case(
            "tests.unit.test_bridge_session",
            "BridgeSessionThreadHistoryTests",
            "test_list_threads_reads_every_native_page",
        )

    def test_14_bridge_reconnect(self) -> None:
        self.run_case(
            "tests.unit.test_panel_wiring",
            "PanelWiringTests",
            "test_bridge_reconnect_is_bounded_and_never_replays_turn",
        )

    def test_15_no_project_files_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".runtime" / "bridge"
            self.exercise_ordinary_http()
            self.assertFalse(runtime.exists())

    def test_16_legacy_project_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy = Path(temporary) / "project-team-registry.json"
            legacy.write_text("{not-json", encoding="utf-8")
            before = legacy.read_bytes()
            self.exercise_ordinary_http()
            self.assertEqual(before, legacy.read_bytes())

    def test_17_ordinary_rotation(self) -> None:
        self.run_case(
            "tests.unit.test_thread_rotation",
            "ThreadRotationServiceTests",
            "test_third_busy_then_native_idle_rotates_exactly_once",
        )

    def test_18_panel_has_no_project_team_entry(self) -> None:
        self.run_case(
            "tests.unit.test_panel_p0_pyside6",
            "OrdinaryPanelP0Tests",
            "test_panel_exposes_only_ordinary_task_navigation",
        )


if __name__ == "__main__":
    unittest.main()
