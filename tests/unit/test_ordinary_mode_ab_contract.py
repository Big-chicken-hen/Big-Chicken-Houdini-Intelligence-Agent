from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from importlib import import_module
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "bridge"))

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.http_server import BridgeApplication, LoopbackHTTPServer
from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry


class _OrdinaryOnlySession:
    def __init__(self) -> None:
        self.turns: list[dict] = []

    def start_turn(self, **kwargs):
        self.turns.append(dict(kwargs))
        return {"thread_id": "ordinary-thread", "turn_id": f"turn-{len(self.turns)}"}


def _project_record(status: ProjectStatus) -> ProjectRecord:
    task = "existing Houdini project"
    task_id, digest = authoritative_task_identity(task)
    return ProjectRecord(
        ProjectState(
            project_id=f"project-{status.value}",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
            roles={role: RoleThread(role, f"thread-{role.value}") for role in Role},
        ),
        task,
    )

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

    def exercise_ordinary_http(
        self,
        project_factory,
        *,
        probe_project_error: str | None = None,
    ) -> _OrdinaryOnlySession:
        token = "ordinary-ab-token-with-at-least-thirty-two-chars"
        session = _OrdinaryOnlySession()
        application = BridgeApplication(
            session,
            EventBuffer(),
            token,
            project_team_factory=project_factory,
        )
        server = LoopbackHTTPServer(("127.0.0.1", 0), application)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"

        def request(method: str, path: str, body: dict | None = None):
            data = None if body is None else json.dumps(body).encode("utf-8")
            headers = {"Authorization": f"Bearer {token}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            return urlopen(
                Request(base_url + path, data=data, headers=headers, method=method),
                timeout=2,
            )

        try:
            if probe_project_error is not None:
                with self.assertRaises(HTTPError) as raised:
                    request("GET", "/v1/project-team")
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(
                    probe_project_error,
                    payload["structured_error"]["code"],
                )
            with request("POST", "/v1/turn", {"text": "ordinary survives"}) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual("ordinary-thread", payload["thread_id"])
            self.assertEqual("ordinary survives", session.turns[0]["text"])
            self.assertEqual(
                {"text", "model", "effort", "local_image_paths", "service_tier"},
                set(session.turns[0]),
            )
            return session
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_01_new_ordinary_thread(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionNativeToolPolicyTests", "test_thread_start_enables_workspace_write_with_native_hython_instructions")

    def test_02_open_ordinary_thread(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionNativeToolPolicyTests", "test_thread_resume_enables_workspace_write_with_on_request_approval")

    def test_03_text_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_http", "BridgeHTTPTests", "test_unicode_model_effort_and_service_tier_are_forwarded_without_loss")

    def test_04_image_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionImageInputTests", "test_text_and_images_share_turn_start_input_in_order")

    def test_05_image_only_turn(self) -> None:
        self.run_case("tests.unit.test_bridge_http", "BridgeHTTPTests", "test_turn_endpoint_accepts_image_only_without_invented_text")

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
        self.run_case("tests.unit.test_panel_project_team_v2", "ProjectTeamControllerTests", "test_copy_signal_forwards_ordinary_thread_id")

    def test_12_delete_and_attachment_cleanup(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionThreadHistoryTests", "test_delete_removes_only_the_exact_thread_attachment_cache")

    def test_13_history_refresh(self) -> None:
        self.run_case("tests.unit.test_bridge_session", "BridgeSessionThreadHistoryTests", "test_list_threads_reads_every_native_page")

    def test_14_model_effort_and_tier(self) -> None:
        self.run_case("tests.unit.test_bridge_http", "BridgeHTTPTests", "test_unicode_model_effort_and_service_tier_are_forwarded_without_loss")

    def test_15_bridge_reconnect(self) -> None:
        self.run_case("tests.unit.test_panel_wiring", "PanelWiringTests", "test_bridge_reconnect_is_bounded_and_never_replays_turn")

    def test_16_unused_project_mode_creates_no_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".runtime" / "bridge"
            calls = []

            def factory():
                calls.append(True)
                raise AssertionError("ordinary HTTP constructed project runtime")

            self.exercise_ordinary_http(factory)
            self.assertEqual([], calls)
            self.assertFalse(runtime.exists())

    def test_17_missing_registry_does_not_block_ordinary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing-project-registry.json"
            calls = []

            def factory():
                calls.append(True)
                return ProjectRegistry(path)

            self.exercise_ordinary_http(factory)
            self.assertEqual([], calls)
            self.assertFalse(path.exists())

    def test_18_corrupt_registry_does_not_block_ordinary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project-registry.json"
            path.write_text("{not-json", encoding="utf-8")
            calls = []

            def factory():
                calls.append(True)
                return ProjectRegistry(path)

            self.exercise_ordinary_http(
                factory,
                probe_project_error="PROJECT_REGISTRY_CORRUPTED",
            )
            self.assertEqual([True], calls)

    def test_19_project_snapshot_failure_does_not_retarget_composer(self) -> None:
        self.exercise_ordinary_http(
            lambda: (_ for _ in ()).throw(ValueError("broken project snapshot")),
            probe_project_error="PROJECT_REGISTRY_CORRUPTED",
        )
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_central_composer_never_turns_into_project_guidance")

    def test_20_stopped_project_does_not_retarget_ordinary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project-registry.json"
            ProjectRegistry(path).put(_project_record(ProjectStatus.STOPPED))
            calls = []

            def factory():
                calls.append(True)
                return ProjectRegistry(path)

            self.exercise_ordinary_http(factory)
            self.assertEqual([], calls)

    def test_21_completed_project_does_not_retarget_ordinary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project-registry.json"
            ProjectRegistry(path).put(_project_record(ProjectStatus.COMPLETED))
            calls = []

            def factory():
                calls.append(True)
                return ProjectRegistry(path)

            self.exercise_ordinary_http(factory)
            self.assertEqual([], calls)

    def test_22_role_view_preserves_ordinary_selection(self) -> None:
        self.run_case("tests.unit.test_panel_project_team_wiring", "PanelProjectTeamWiringTests", "test_role_view_uses_readonly_endpoint_and_preserves_ordinary_selection")


if __name__ == "__main__":
    unittest.main()
