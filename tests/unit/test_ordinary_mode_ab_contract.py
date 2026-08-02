from __future__ import annotations

import unittest
from importlib import import_module

class OrdinaryModeABContractTests(unittest.TestCase):
    """Trace the 22 ordinary-mode parity requirements to executable tests.

    Each case runs the referenced behavior test directly.  The mapping is
    intentionally explicit so project work cannot silently remove one parity
    requirement while leaving the general suite green.
    """

    def run_case(self, module_name: str, class_name: str, method: str) -> None:
        case = getattr(import_module(module_name), class_name)
        result = unittest.TestResult()
        case(method).run(result)
        details = [str(error) for _, error in result.failures + result.errors]
        self.assertEqual([], details)
        self.assertEqual(1, result.testsRun)

    def test_01_new_ordinary_thread(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionNativeToolPolicyTests", "test_thread_start_enables_workspace_write_with_native_hython_instructions")

    def test_02_open_ordinary_thread(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionNativeToolPolicyTests", "test_thread_resume_enables_workspace_write_with_on_request_approval")

    def test_03_text_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_http", "BridgeHTTPTests", "test_unicode_model_effort_and_service_tier_are_forwarded_without_loss")

    def test_04_image_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionImageInputTests", "test_text_and_images_share_turn_start_input_in_order")

    def test_05_image_only_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionImageInputTests", "test_images_without_text_are_valid_turn_input")

    def test_06_active_turn_steer(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionSteerTests", "test_steer_uses_expected_turn_and_does_not_change_lifecycle")

    def test_07_stop(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionTurnStateTests", "test_interrupt_completion_within_grace_does_not_restart_codex")

    def test_08_goal(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionGoalTests", "test_goal_round_trip_uses_selected_native_thread")

    def test_09_focus(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionGoalTests", "test_focus_mode_requires_active_goal_and_persists_per_thread")

    def test_10_rename(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionThreadHistoryTests", "test_rename_thread_forwards_the_original_name")

    def test_11_copy_thread_id_ui(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_thread_history_has_one_owner_and_is_forwarded_to_project_tree")

    def test_12_delete_and_attachment_cleanup(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionThreadHistoryTests", "test_delete_removes_only_the_exact_thread_attachment_cache")

    def test_13_history_refresh(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionThreadHistoryTests", "test_list_threads_reads_every_native_page")

    def test_14_model_effort_and_tier(self) -> None:
        self.run_case("tests.unit.test_bridge_http", "BridgeHTTPTests", "test_unicode_model_effort_and_service_tier_are_forwarded_without_loss")

    def test_15_bridge_reconnect(self) -> None:
        self.run_case("tests.unit.test_panel_wiring", "PanelWiringTests", "test_bridge_reconnect_is_bounded_and_never_replays_turn")

    def test_16_unused_project_mode_creates_no_runtime(self) -> None:
        self.run_case("tests.unit.test_bridge_main_lifecycle", "BridgeMainLifecycleTests", "test_ordinary_server_lifecycle_does_not_construct_project_runtime")

    def test_17_missing_registry_does_not_block_ordinary_mode(self) -> None:
        self.run_case("tests.unit.test_bridge_main_lifecycle", "BridgeMainLifecycleTests", "test_ordinary_server_lifecycle_does_not_construct_project_runtime")

    def test_18_corrupt_registry_does_not_block_ordinary_mode(self) -> None:
        self.run_case("tests.unit.test_bridge_main_lifecycle", "BridgeMainLifecycleTests", "test_ordinary_server_lifecycle_does_not_construct_project_runtime")

    def test_19_project_snapshot_failure_does_not_retarget_composer(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_central_composer_never_turns_into_project_guidance")

    def test_20_stopped_project_does_not_retarget_ordinary_mode(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_central_composer_never_turns_into_project_guidance")

    def test_21_completed_project_does_not_retarget_ordinary_mode(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_central_composer_never_turns_into_project_guidance")

    def test_22_role_view_preserves_ordinary_selection(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_role_view_uses_readonly_endpoint_and_preserves_ordinary_selection")


if __name__ == "__main__":
    unittest.main()
