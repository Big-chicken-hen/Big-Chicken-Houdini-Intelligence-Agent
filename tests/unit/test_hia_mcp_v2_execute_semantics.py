from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402
from tests.unit.test_hia_mcp_v2_runtime import FakeHou  # noqa: E402

GOAL_BINDING = "a" * 64


class HiaMcpV2ExecuteSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = REPOSITORY_ROOT / ".runtime" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.project_root = Path(self._temporary.name) / "execute-project"
        self.project_root.mkdir()
        self.hou = FakeHou()
        with mock.patch.dict(
            os.environ,
            {"HIA_CACHE_DIR": str(self.project_root / ".runtime" / "cache")},
            clear=False,
        ):
            self.executor = HoudiniExecutor(
                hou_module=self.hou,
                main_thread_runner=lambda callback: callback(),
                project_root=self.project_root,
            )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def checkpoint_directory(self) -> Path:
        directory = (
            self.project_root
            / ".runtime"
            / "launcher-sessions"
            / ("a" * 32)
            / "checkpoints"
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def enabled_focus_environment(self) -> dict[str, str]:
        path = self.project_root / ".runtime" / "bridge" / "focus-mode.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active_thread_id": "thread-test",
                    "enabled_thread_ids": ["thread-test"],
                    "goal_bindings": {"thread-test": GOAL_BINDING},
                }
            ),
            encoding="utf-8",
        )
        return {"HIA_FOCUS_STATE_PATH": str(path)}

    def test_default_diff_is_targeted_and_never_walks_the_scene(self) -> None:
        self.executor._snapshot_map = mock.Mock(  # type: ignore[method-assign]
            side_effect=AssertionError("default execution must not walk the scene")
        )
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=[None, "created"]
        )

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "hia_mark_changed('/obj/touched')\nhia_result = 'done'",
            },
        )

        self.assertTrue(response["ok"])
        self.assertEqual("done", response["result"])
        self.assertEqual("targeted", response["diff"]["mode"])
        self.assertIsNone(response["diff"]["root_path"])
        self.assertEqual(["/obj/touched"], response["diff"]["declared_or_touched_paths"])
        self.assertEqual(["/obj/touched"], response["diff"]["created"])
        self.assertEqual([], response["diff"]["unverified_paths"])
        self.executor._snapshot_map.assert_not_called()

        self.executor._node_digest = mock.Mock(return_value=None)  # type: ignore[method-assign]
        bounded = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "\n".join(f"hia_mark_changed('/obj/n{index}')" for index in range(129))},
        )
        self.assertTrue(bounded["diff"]["truncated"])
        self.assertEqual(128, len(bounded["diff"]["declared_or_touched_paths"]))

    def test_predeclared_path_produces_a_verified_bounded_diff(self) -> None:
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=["before", "after"]
        )

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "hia_result = {'edited': True}",
                "diff_paths": ["/obj/existing"],
            },
        )

        self.assertEqual(["/obj/existing"], response["diff"]["changed"])
        self.assertEqual([], response["diff"]["unverified_paths"])
        self.assertEqual(["/obj/existing"], response["created_or_changed_paths"])
        self.assertEqual("changed", response["scene_change_status"])

    def test_late_marker_is_declared_but_does_not_claim_a_verified_change(self) -> None:
        self.hou.hipFile.dirty = True
        self.executor._node_digest = mock.Mock(return_value="already-edited")  # type: ignore[method-assign]

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "hou.after_edit = True\nhia_mark_changed('/obj/late-marker')",
            },
        )

        self.assertEqual([], response["created_or_changed_paths"])
        self.assertEqual(["/obj/late-marker"], response["diff"]["unverified_paths"])
        self.assertEqual("unknown", response["scene_change_status"])
        self.assertEqual(0, response["revision"])

    def test_no_change_marker_remains_unverified_without_revision_increment(self) -> None:
        self.executor._node_digest = mock.Mock(return_value="same")  # type: ignore[method-assign]

        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "hia_mark_changed('/obj/no-change')"},
        )

        self.assertEqual([], response["created_or_changed_paths"])
        self.assertEqual(["/obj/no-change"], response["diff"]["unverified_paths"])
        self.assertEqual("unknown", response["scene_change_status"])
        self.assertEqual(0, response["revision"])

    def test_digest_inspection_failure_is_unverified_not_a_scene_change(self) -> None:
        self.hou.node = mock.Mock(side_effect=RuntimeError("inspection failed"))  # type: ignore[method-assign]

        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "pass", "diff_paths": ["/obj/unreadable"]},
        )

        self.assertEqual([], response["created_or_changed_paths"])
        self.assertEqual(["/obj/unreadable"], response["diff"]["unverified_paths"])
        self.assertEqual("unknown", response["scene_change_status"])
        self.assertEqual(0, response["revision"])

    def test_only_an_explicit_diff_root_requests_a_full_snapshot(self) -> None:
        self.executor._snapshot_map = mock.Mock(  # type: ignore[method-assign]
            side_effect=[
                ({"/obj": "before"}, False),
                ({"/obj": "before", "/obj/new": "created"}, False),
            ]
        )

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "\n".join(
                    f"hia_mark_changed('/obj/n{index}')" for index in range(129)
                ),
                "diff_root_path": "/obj",
            },
        )

        self.assertEqual("full", response["diff"]["mode"])
        self.assertEqual("/obj", response["diff"]["root_path"])
        self.assertEqual(["/obj/new"], response["diff"]["created"])
        self.assertTrue(response["diff"]["truncated"])
        self.assertEqual(2, self.executor._snapshot_map.call_count)

    def test_deleted_full_diff_root_is_reported_as_an_observed_change(self) -> None:
        self.executor._snapshot_map = mock.Mock(  # type: ignore[method-assign]
            side_effect=[
                ({"/obj/branch": "root", "/obj/branch/child": "child"}, False),
                HiaRuntimeError(
                    "NODE_NOT_FOUND",
                    "Snapshot root does not exist",
                    {"path": "/obj/branch"},
                ),
            ]
        )

        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "pass", "diff_root_path": "/obj/branch"},
        )

        self.assertTrue(response["ok"])
        self.assertEqual(
            ["/obj/branch", "/obj/branch/child"],
            response["diff"]["deleted"],
        )
        self.assertEqual(
            ["/obj/branch", "/obj/branch/child"],
            response["created_or_changed_paths"],
        )
        self.assertEqual("changed", response["scene_change_status"])
        self.assertEqual(1, response["revision"])

    def test_checkpoint_is_created_once_only_after_a_verified_change(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        backup_path = checkpoint_directory / "test_bak1.hip"
        original_hip = self.hou.hipFile.path()

        def save_backup() -> str:
            backup_path.write_bytes(b"hip")
            return str(backup_path)

        self.hou.hipFile.saveAsBackup = mock.Mock(side_effect=save_backup)  # type: ignore[attr-defined]
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=["before", "after"]
        )

        with mock.patch.dict(
            os.environ,
            {
                "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                **self.enabled_focus_environment(),
            },
            clear=False,
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "hia_result = 'stage complete'",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "modeling-stage-1",
                },
            )

        self.assertTrue(response["ok"])
        self.hou.hipFile.saveAsBackup.assert_called_once_with()
        self.assertEqual(original_hip, self.hou.hipFile.path())
        self.assertEqual(
            {
                "requested": True,
                "label": "modeling-stage-1",
                "created": True,
                "path": str(backup_path.resolve()),
                "error": None,
            },
            response["checkpoint"],
        )
        marker = json.loads(
            (checkpoint_directory / ".hia-stage-checkpoint.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("thread-test", marker["thread_id"])
        self.assertEqual(GOAL_BINDING, marker["goal_binding"])
        self.assertEqual(backup_path.name, marker["checkpoint_file"])
        self.assertEqual(
            {"version", "thread_id", "goal_binding", "checkpoint_file"},
            set(marker),
        )

    def test_focus_mode_off_never_creates_a_recovery_checkpoint(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        legacy_focus_path = (
            self.project_root / ".runtime" / "bridge" / "focus-mode.json"
        )
        legacy_focus_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_focus_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active_thread_id": "thread-test",
                    "enabled_thread_ids": ["thread-test"],
                }
            ),
            encoding="utf-8",
        )
        self.hou.hipFile.saveAsBackup = mock.Mock(  # type: ignore[attr-defined]
            side_effect=AssertionError("focus mode off must not save a backup")
        )
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=["before", "after"]
        )

        with mock.patch.dict(
            os.environ,
            {
                "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                "HIA_FOCUS_STATE_PATH": str(legacy_focus_path),
            },
            clear=False,
        ):
            context = self.executor.dispatch("hia_context", {})
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "hia_result = 'stage complete'",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "stage-complete",
                },
            )

        self.assertFalse(context["result"]["goal_focus_mode"])
        self.assertEqual(
            "FOCUS_MODE_DISABLED",
            response["checkpoint"]["skipped_reason"],
        )
        self.hou.hipFile.saveAsBackup.assert_not_called()
        self.assertFalse(
            (checkpoint_directory / ".hia-stage-checkpoint.json").exists()
        )

    def test_valid_checkpoint_path_bypasses_user_profile_text_redaction(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        backup_path = checkpoint_directory / "user-profile-checkpoint.hip"

        def save_backup() -> str:
            backup_path.write_bytes(b"hip")
            return str(backup_path)

        self.hou.hipFile.saveAsBackup = mock.Mock(side_effect=save_backup)  # type: ignore[attr-defined]
        self.executor._node_digest = mock.Mock(side_effect=["before", "after"])  # type: ignore[method-assign]
        with (
            mock.patch.dict(
                os.environ,
                {
                    "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                    **self.enabled_focus_environment(),
                },
                clear=False,
            ),
            mock.patch(
                "hia_mcp_runtime.executor._redact_text",
                side_effect=lambda value: "%USERPROFILE%\\checkpoint.hip"
                if str(backup_path.resolve()) in str(value)
                else value,
            ),
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "pass",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "user-profile-path",
                },
            )

        returned_path = Path(response["checkpoint"]["path"])
        self.assertTrue(response["checkpoint"]["created"])
        self.assertEqual(backup_path.resolve(), returned_path)
        self.assertTrue(returned_path.is_absolute())
        self.assertTrue(returned_path.is_file())
        self.assertTrue(returned_path.is_relative_to(checkpoint_directory.resolve()))

    def test_checkpoint_is_skipped_without_a_confirmed_successful_change(self) -> None:
        self.hou.hipFile.saveAsBackup = mock.Mock(  # type: ignore[attr-defined]
            side_effect=AssertionError("backup must not run")
        )
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=["same", "same"]
        )

        unchanged = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "hia_result = 'unchanged'",
                "diff_paths": ["/obj/asset"],
                "checkpoint_label": "stage-unchanged",
            },
        )
        failed = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "raise RuntimeError('failed edit')",
                "capture_diff": False,
                "checkpoint_label": "stage-failed",
            },
        )

        self.assertEqual("NO_CONFIRMED_SCENE_CHANGE", unchanged["checkpoint"]["skipped_reason"])
        self.assertEqual("HOM_EXECUTION_FAILED", failed["checkpoint"]["skipped_reason"])
        self.hou.hipFile.saveAsBackup.assert_not_called()

    def test_checkpoint_failure_is_nonfatal_and_must_not_trigger_write_retry(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        self.hou.hipFile.saveAsBackup = mock.Mock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("backup disk unavailable")
        )
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=["before", "after"]
        )

        with mock.patch.dict(
            os.environ,
            {
                "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                **self.enabled_focus_environment(),
            },
            clear=False,
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "hia_result = 'write succeeded'",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "stage-with-backup-error",
                },
            )

        self.assertTrue(response["ok"])
        self.assertEqual("CHECKPOINT_FAILED", response["checkpoint"]["error"]["code"])
        self.assertTrue(any("do not retry" in item for item in response["warnings"]))

    def test_checkpoint_marker_failure_does_not_retry_the_completed_scene_write(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        backup_path = checkpoint_directory / "completed-stage.hip"

        def save_backup() -> str:
            backup_path.write_bytes(b"hip")
            return str(backup_path)

        self.hou.hipFile.saveAsBackup = mock.Mock(side_effect=save_backup)  # type: ignore[attr-defined]
        self.executor._node_digest = mock.Mock(side_effect=["before", "after"])  # type: ignore[method-assign]
        with (
            mock.patch.dict(
                os.environ,
                {
                    "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                    **self.enabled_focus_environment(),
                },
                clear=False,
            ),
            mock.patch.object(
                self.executor,
                "_write_stage_checkpoint_marker",
                side_effect=OSError("marker unavailable"),
            ),
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "hia_result = 'write completed'",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "completed-stage",
                },
            )

        self.assertTrue(response["ok"])
        self.assertTrue(backup_path.is_file())
        self.hou.hipFile.saveAsBackup.assert_called_once_with()
        self.assertEqual("CHECKPOINT_FAILED", response["checkpoint"]["error"]["code"])
        self.assertTrue(any("do not retry" in item for item in response["warnings"]))

    def test_checkpoint_is_skipped_when_backup_directory_is_unconfigured(self) -> None:
        self.hou.hipFile.saveAsBackup = mock.Mock()  # type: ignore[attr-defined]
        self.executor._node_digest = mock.Mock(side_effect=["before", "after"])  # type: ignore[method-assign]

        with mock.patch.dict(
            os.environ,
            {
                "HOUDINI_BACKUP_DIR": "",
                **self.enabled_focus_environment(),
            },
            clear=False,
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": "pass",
                    "diff_paths": ["/obj/asset"],
                    "checkpoint_label": "missing-backup-dir",
                },
            )

        self.hou.hipFile.saveAsBackup.assert_not_called()
        self.assertEqual(
            "CHECKPOINT_CONFIGURATION_INVALID",
            response["checkpoint"]["skipped_reason"],
        )
        self.assertEqual(
            "CHECKPOINT_CONFIGURATION_INVALID",
            response["checkpoint"]["error"]["code"],
        )

    def test_checkpoint_rejects_outside_or_missing_returned_paths(self) -> None:
        checkpoint_directory = self.checkpoint_directory()
        outside = self.project_root / "outside.hip"
        outside.write_bytes(b"must remain")

        for returned_path in (outside, checkpoint_directory / "missing.hip"):
            with self.subTest(returned_path=returned_path):
                self.hou.hipFile.saveAsBackup = mock.Mock(return_value=str(returned_path))  # type: ignore[attr-defined]
                self.executor._node_digest = mock.Mock(side_effect=["before", "after"])  # type: ignore[method-assign]
                with mock.patch.dict(
                    os.environ,
                    {
                        "HOUDINI_BACKUP_DIR": str(checkpoint_directory),
                        **self.enabled_focus_environment(),
                    },
                    clear=False,
                ):
                    response = self.executor.dispatch(
                        "hia_execute_hom",
                        {
                            "script": "pass",
                            "diff_paths": ["/obj/asset"],
                            "checkpoint_label": "bad-return-path",
                        },
                    )

                self.assertFalse(response["checkpoint"]["created"])
                self.assertEqual(
                    "CHECKPOINT_FAILED",
                    response["checkpoint"]["error"]["code"],
                )
        self.assertEqual(b"must remain", outside.read_bytes())

    def test_result_reports_runtime_phases_and_the_non_interruptible_boundary(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "hia_result = 1", "capture_diff": False},
        )

        timings = response["phase_timings"]
        for name in (
            "runtime_ui_queue_seconds",
            "runtime_ui_main_thread_seconds",
            "runtime_ui_return_seconds",
            "runtime_pre_diff_seconds",
            "runtime_hom_seconds",
            "runtime_post_diff_seconds",
            "runtime_checkpoint_seconds",
            "runtime_result_normalization_seconds",
            "runtime_execute_total_seconds",
        ):
            self.assertIn(name, timings)
            self.assertGreaterEqual(timings[name], 0.0)
        self.assertFalse(response["execution_limit"]["interruptible_after_main_thread_entry"])
        self.assertTrue(response["execution_limit"]["hom_may_continue_after_client_timeout"])
        self.assertFalse(response["execution_limit"]["automatic_retry_after_timeout"])


if __name__ == "__main__":
    unittest.main()
