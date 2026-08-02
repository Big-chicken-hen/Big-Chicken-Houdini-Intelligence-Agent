from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402
from tests.unit.test_hia_mcp_v2_runtime import FakeHou  # noqa: E402


OUTPUT_FIELDS = {
    "ok",
    "result",
    "stdout",
    "warnings",
    "errors",
    "revision",
    "dirty",
    "elapsed_seconds",
    "script_sha256",
    "scene_change_status",
}


class HiaMcpV2ExecuteSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "tests")
        self.project_root = Path(self._temporary.name) / "execute-project"
        self.project_root.mkdir()
        self.hou = FakeHou()
        self.executor = HoudiniExecutor(
            hou_module=self.hou,
            main_thread_runner=lambda callback: callback(),
            project_root=self.project_root,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_success_returns_only_direct_execution_facts_and_one_manual_undo_item(self) -> None:
        self.executor._validate = mock.Mock(  # type: ignore[method-assign]
            side_effect=AssertionError("execute must not run validation")
        )
        self.executor._snapshot_map = mock.Mock(  # type: ignore[method-assign]
            side_effect=AssertionError("execute must not capture a scene diff")
        )

        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "print('hello')\nhia_result = {'frame': hou.frame()}"},
        )

        self.assertEqual(OUTPUT_FIELDS, set(response))
        self.assertTrue(response["ok"])
        self.assertEqual({"frame": 12.0}, response["result"])
        self.assertEqual("hello\n", response["stdout"])
        self.assertEqual([], response["errors"])
        self.assertEqual("unchanged", response["scene_change_status"])
        self.assertEqual(["HIA MCP V2 execute"], self.hou.undos.labels)
        self.assertEqual(0, self.hou.undos.undo_calls)
        self.executor._validate.assert_not_called()
        self.executor._snapshot_map.assert_not_called()

    def test_only_script_and_timeout_are_accepted(self) -> None:
        with self.assertRaises(HiaRuntimeError) as captured:
            self.executor.dispatch(
                "hia_execute_hom",
                {"script": "hia_result = 1", "task": "legacy field"},
            )

        self.assertEqual("INVALID_ARGUMENTS", captured.exception.code)
        self.assertEqual(["task"], captured.exception.details["fields"])
        self.assertEqual([], self.hou.undos.labels)

    def test_syntax_failure_never_enters_undo_or_advances_revision(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "if:"},
        )

        self.assertEqual(OUTPUT_FIELDS, set(response))
        self.assertFalse(response["ok"])
        self.assertEqual("INVALID_HOM_SCRIPT", response["errors"][0]["code"])
        self.assertFalse(response["errors"][0]["partial_scene_changes_possible"])
        self.assertEqual("unchanged", response["scene_change_status"])
        self.assertEqual(0, response["revision"])
        self.assertEqual([], self.hou.undos.labels)

    def test_missing_undo_group_fails_before_exec_without_fallback(self) -> None:
        self.hou.undos.group = None  # type: ignore[method-assign]

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": (
                    "hou.hipFile.current_path = 'executed'\n"
                    "hia_result = 'must not run'"
                )
            },
        )

        self.assertEqual("untitled.hip", self.hou.hipFile.current_path)
        self.assertFalse(response["ok"])
        self.assertEqual("UNDO_GROUP_UNAVAILABLE", response["errors"][0]["code"])
        self.assertFalse(response["errors"][0]["partial_scene_changes_possible"])
        self.assertEqual("unchanged", response["scene_change_status"])
        self.assertEqual(0, response["revision"])

    def test_execution_error_reports_possible_partial_change_and_never_calls_undo(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "hou.hipFile.dirty = True\nraise RuntimeError('failed')"},
        )

        self.assertFalse(response["ok"])
        self.assertEqual("HOM_EXECUTION_FAILED", response["errors"][0]["code"])
        self.assertTrue(response["errors"][0]["partial_scene_changes_possible"])
        self.assertEqual("changed", response["scene_change_status"])
        self.assertTrue(response["dirty"])
        self.assertEqual(1, response["revision"])
        self.assertEqual(0, self.hou.undos.undo_calls)
        self.assertEqual(["HIA MCP V2 execute"], self.hou.undos.labels)

    def test_successful_dirty_transition_is_reported_as_changed(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "hou.hipFile.dirty = True\nhia_result = 'changed'"},
        )

        self.assertTrue(response["ok"])
        self.assertEqual("changed", response["scene_change_status"])
        self.assertTrue(response["dirty"])
        self.assertEqual(1, response["revision"])

    def test_timeout_bounds_are_validated_before_undo(self) -> None:
        for value in (True, 0, 301, float("nan"), "60"):
            with self.subTest(value=value), self.assertRaises(HiaRuntimeError) as captured:
                self.executor.dispatch(
                    "hia_execute_hom",
                    {"script": "hia_result = 1", "timeout_seconds": value},
                )
            self.assertEqual("INVALID_ARGUMENTS", captured.exception.code)
        self.assertEqual([], self.hou.undos.labels)


if __name__ == "__main__":
    unittest.main()
