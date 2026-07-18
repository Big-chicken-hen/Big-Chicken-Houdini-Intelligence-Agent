from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402


class FakeHipFile:
    def __init__(self) -> None:
        self.dirty = False

    def path(self) -> str:
        return "E:/houdini-intelligence-agent/test.hip"

    def hasUnsavedChanges(self) -> bool:
        return self.dirty


class FakeNodeType:
    def name(self) -> str:
        return "root"

    def category(self) -> Any:
        return FakeNamed("Manager")

    def description(self) -> str:
        return "Root"

    def nameComponents(self) -> tuple[str, ...]:
        return ("", "root", "", "")


class FakeNamed:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class FakeRoot:
    def path(self) -> str:
        return "/"

    def name(self) -> str:
        return ""

    def type(self) -> FakeNodeType:
        return FakeNodeType()

    def inputs(self) -> tuple[Any, ...]:
        return ()

    def parms(self) -> tuple[Any, ...]:
        return ()

    def allSubChildren(self) -> tuple[Any, ...]:
        return ()

    def children(self) -> tuple[Any, ...]:
        return ()

    def errors(self) -> tuple[str, ...]:
        return ()

    def warnings(self) -> tuple[str, ...]:
        return ()


class FakeViewport:
    def saveViewToImage(self, path: str) -> None:  # noqa: N802
        Path(path).write_bytes(b"fake-png")


class FakeSceneViewer:
    def __init__(self) -> None:
        self.viewport = FakeViewport()

    def curViewport(self) -> FakeViewport:  # noqa: N802
        return self.viewport


class FakeDesktop:
    def __init__(self) -> None:
        self.scene_viewer = FakeSceneViewer()

    def paneTabOfType(self, _pane_type: object) -> FakeSceneViewer:  # noqa: N802
        return self.scene_viewer


class FakeUi:
    def __init__(self) -> None:
        self.desktop = FakeDesktop()

    def curDesktop(self) -> FakeDesktop:  # noqa: N802
        return self.desktop


class FakePaneTabType:
    SceneViewer = object()


class FakeHou:
    def __init__(self) -> None:
        self.hipFile = FakeHipFile()
        self.root = FakeRoot()
        self.ui = FakeUi()
        self.paneTabType = FakePaneTabType()

    def node(self, path: str) -> FakeRoot | None:
        return self.root if path == "/" else None

    def selectedNodes(self) -> tuple[Any, ...]:
        return ()

    def applicationVersionString(self) -> str:
        return "21.0.440"

    def frame(self) -> float:
        return 12.0

    def fps(self) -> float:
        return 24.0

    def frameRange(self) -> tuple[float, float]:
        return (1.0, 240.0)

    def playbarRange(self) -> tuple[float, float]:
        return (1.0, 120.0)

    def nodeTypeCategories(self) -> dict[str, Any]:
        return {}

    def isUIAvailable(self) -> bool:
        return True


class HiaMcpV2RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hou = FakeHou()
        self.runner_calls = 0

        def runner(callback: Any) -> Any:
            self.runner_calls += 1
            return callback()

        self.executor = HoudiniExecutor(
            hou_module=self.hou,
            main_thread_runner=runner,
            project_root=REPOSITORY_ROOT,
        )

    def test_every_dispatch_crosses_the_main_thread_runner(self) -> None:
        response = self.executor.dispatch("hia_context", {})
        self.assertEqual(1, self.runner_calls)
        self.assertTrue(response["ok"])
        self.assertEqual("21.0.440", response["result"]["houdini_build"])
        self.assertEqual(12.0, response["result"]["frame"])

    def test_execute_hom_returns_the_full_structured_contract(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "print('hello')\nhia_mark_changed('/obj/test')\nhia_result = {'frame': hou.frame()}",
                "capture_diff": True,
            },
        )
        self.assertTrue(response["ok"])
        self.assertEqual({"frame": 12.0}, response["result"])
        self.assertEqual("hello\n", response["stdout"])
        self.assertEqual(["/obj/test"], response["created_or_changed_paths"])
        self.assertEqual(1, response["revision"])
        self.assertIn("interruptible_after_main_thread_entry", response["execution_limit"])
        self.assertFalse(response["execution_limit"]["interruptible_after_main_thread_entry"])

    def test_execute_hom_failure_preserves_traceback_and_redacts_credentials(self) -> None:
        response = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "raise RuntimeError('Bearer SUPERSECRETVALUE')", "capture_diff": False},
        )
        self.assertFalse(response["ok"])
        self.assertEqual("HOM_EXECUTION_FAILED", response["errors"][0]["code"])
        self.assertIn("Traceback", response["errors"][0]["traceback"])
        self.assertNotIn("SUPERSECRETVALUE", str(response))
        self.assertIn("[REDACTED]", str(response))

    def test_unknown_tool_has_a_stable_error(self) -> None:
        with self.assertRaises(HiaRuntimeError) as raised:
            self.executor.dispatch("hia_create_node", {})
        self.assertEqual("TOOL_NOT_FOUND", raised.exception.code)

    def test_viewport_defaults_to_portable_timestamped_screenshot_cache(self) -> None:
        temp_root = REPOSITORY_ROOT / ".runtime" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
            project_root = Path(temporary) / "portable-project"
            project_root.mkdir()
            cache_root = project_root / ".runtime" / "cache"
            with mock.patch.dict(
                os.environ,
                {"HIA_CACHE_DIR": str(cache_root)},
                clear=False,
            ):
                executor = HoudiniExecutor(
                    hou_module=FakeHou(),
                    main_thread_runner=lambda callback: callback(),
                    project_root=project_root,
                )
                first = executor.dispatch("hia_capture_viewport", {})
                second = executor.dispatch("hia_capture_viewport", {})

            first_relative = first["result"]["path"]
            second_relative = second["result"]["path"]
            self.assertRegex(
                first_relative,
                re.compile(
                    r"^\.runtime/cache/screenshots/"
                    r"viewport-\d{8}T\d{12}Z-[0-9a-f]{8}-0012\.png$"
                ),
            )
            self.assertNotEqual(first_relative, second_relative)
            self.assertTrue((project_root / first_relative).is_file())
            self.assertTrue((project_root / second_relative).is_file())

    def test_runtime_rejects_cache_root_outside_the_project_cache(self) -> None:
        project_root = REPOSITORY_ROOT / ".runtime" / "portable-runtime-root"
        outside = REPOSITORY_ROOT / ".runtime" / "outside-cache"
        with mock.patch.dict(
            os.environ,
            {"HIA_CACHE_DIR": str(outside)},
            clear=False,
        ):
            with self.assertRaises(HiaRuntimeError) as captured:
                HoudiniExecutor(
                    hou_module=FakeHou(),
                    main_thread_runner=lambda callback: callback(),
                    project_root=project_root,
                )
        self.assertEqual("INVALID_CACHE_DIR", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
