from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402


def _png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


@dataclass(frozen=True)
class FakeCamera:
    path: str


@dataclass
class FakeViewportCamera:
    state: str

    def stash(self) -> "FakeViewportCamera":
        return FakeViewportCamera(self.state)


class FakeViewport:
    def __init__(
        self,
        *,
        original_camera: FakeCamera | None,
        default_camera_state: str,
        camera_locked: bool,
        image_size: tuple[int, int],
        fail_save: bool = False,
        fail_restore_camera_path: str | None = None,
    ) -> None:
        self._camera = original_camera
        self._default_camera = FakeViewportCamera(default_camera_state)
        self._camera_locked = camera_locked
        self._image_size = image_size
        self._fail_save = fail_save
        self._fail_restore_camera_path = fail_restore_camera_path
        self.camera_at_save: FakeCamera | None = None
        self.camera_lock_at_save: bool | None = None
        self.camera_restore_calls: list[str] = []

    def camera(self) -> FakeCamera | None:
        return self._camera

    def defaultCamera(self) -> FakeViewportCamera:  # noqa: N802
        return self._default_camera

    def setDefaultCamera(self, camera: FakeViewportCamera) -> None:  # noqa: N802
        self._camera = None
        self._default_camera = camera.stash()
        self.camera_restore_calls.append("set_default")

    def useDefaultCamera(self) -> None:  # noqa: N802
        self._camera = None
        self.camera_restore_calls.append("use_default")

    def isCameraLockedToView(self) -> bool:  # noqa: N802
        return self._camera_locked

    def lockCameraToView(self, locked: bool) -> None:  # noqa: N802
        self._camera_locked = locked

    def setCamera(self, camera: FakeCamera) -> None:  # noqa: N802
        self.camera_restore_calls.append(f"set_camera:{camera.path}")
        if camera.path == self._fail_restore_camera_path:
            raise RuntimeError("simulated camera restore failure")
        self._camera = camera

    def saveViewToImage(self, path: str) -> None:  # noqa: N802
        self.camera_at_save = self._camera
        self.camera_lock_at_save = self._camera_locked
        if self._fail_save:
            raise RuntimeError("simulated viewport capture failure")
        Path(path).write_bytes(_png_bytes(*self._image_size))


class FakeFlipbookSettings:
    def __init__(self) -> None:
        self.frame_range = (1.0, 24.0)
        self.output_path = "original.$F4.png"
        self.image_resolution = (320, 200)
        self.use_resolution = False
        self.output_zoom = 50
        self.use_sheet_size = True
        self.output_to_mplay = True

    def stash(self) -> "FakeFlipbookSettings":
        copy = FakeFlipbookSettings()
        copy.frame_range = self.frame_range
        copy.output_path = self.output_path
        copy.image_resolution = self.image_resolution
        copy.use_resolution = self.use_resolution
        copy.output_zoom = self.output_zoom
        copy.use_sheet_size = self.use_sheet_size
        copy.output_to_mplay = self.output_to_mplay
        return copy

    def frameRange(self, value: tuple[float, float]) -> None:  # noqa: N802
        self.frame_range = value

    def output(self, value: str) -> None:
        self.output_path = value

    def resolution(self, value: tuple[int, int]) -> None:
        self.image_resolution = value

    def useResolution(self, value: bool) -> None:  # noqa: N802
        self.use_resolution = value

    def outputZoom(self, value: int) -> None:  # noqa: N802
        self.output_zoom = value

    def useSheetSize(self, value: bool) -> None:  # noqa: N802
        self.use_sheet_size = value

    def outputToMPlay(self, value: bool) -> None:  # noqa: N802
        self.output_to_mplay = value


class FakeHipFile:
    def hasUnsavedChanges(self) -> bool:  # noqa: N802
        return False


class FakePaneTabType:
    SceneViewer = object()


class FakeSceneViewer:
    def __init__(self, viewport: FakeViewport, hou_module: "FakeHou") -> None:
        self.viewport = viewport
        self.hou = hou_module
        self.original_flipbook_settings = FakeFlipbookSettings()
        self.used_flipbook_settings: FakeFlipbookSettings | None = None
        self.open_dialog: bool | None = None
        self.focus_calls = 0
        self.mplay_launches = 0
        self.camera_at_flipbook: FakeCamera | None = None
        self.camera_lock_at_flipbook: bool | None = None

    def curViewport(self) -> FakeViewport:  # noqa: N802
        return self.viewport

    def setIsCurrentTab(self) -> None:  # noqa: N802
        self.focus_calls += 1

    def flipbookSettings(self) -> FakeFlipbookSettings:  # noqa: N802
        return self.original_flipbook_settings

    def flipbook(
        self,
        _viewport: FakeViewport,
        settings: FakeFlipbookSettings,
        *,
        open_dialog: bool,
    ) -> None:
        self.used_flipbook_settings = settings
        self.camera_at_flipbook = self.viewport.camera()
        self.camera_lock_at_flipbook = self.viewport.isCameraLockedToView()
        self.open_dialog = open_dialog
        if open_dialog:
            self.focus_calls += 1
        if settings.output_to_mplay:
            self.mplay_launches += 1
        end = float(settings.frame_range[1])
        self.hou.setFrame(end)
        first = int(round(float(settings.frame_range[0])))
        output_path = settings.output_path.replace("$F4", f"{first:04d}")
        Path(output_path).write_bytes(_png_bytes(*settings.image_resolution))


class FakeDesktop:
    def __init__(self, scene_viewer: FakeSceneViewer) -> None:
        self.scene_viewer = scene_viewer

    def paneTabOfType(self, _pane_type: object) -> FakeSceneViewer:  # noqa: N802
        return self.scene_viewer


class FakeUi:
    def __init__(self, desktop: FakeDesktop) -> None:
        self.desktop = desktop

    def curDesktop(self) -> FakeDesktop:  # noqa: N802
        return self.desktop


class FakeHou:
    def __init__(self, viewport: FakeViewport, cameras: tuple[FakeCamera, ...]) -> None:
        self._frame = 12.0
        self.frame_history: list[float] = []
        self.hipFile = FakeHipFile()
        self.paneTabType = FakePaneTabType()
        self._cameras = {camera.path: camera for camera in cameras}
        self.scene_viewer = FakeSceneViewer(viewport, self)
        self.ui = FakeUi(FakeDesktop(self.scene_viewer))

    def isUIAvailable(self) -> bool:  # noqa: N802
        return True

    def node(self, path: str) -> FakeCamera | None:
        return self._cameras.get(path)

    def frame(self) -> float:
        return self._frame

    def setFrame(self, frame: float) -> None:  # noqa: N802
        self._frame = float(frame)
        self.frame_history.append(self._frame)


class HiaMcpV2ViewportStateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = REPOSITORY_ROOT / ".runtime" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.project_root = Path(self._temporary.name) / "viewport-state-project"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def make_executor(
        self,
        viewport: FakeViewport,
        *cameras: FakeCamera,
    ) -> tuple[FakeHou, HoudiniExecutor]:
        hou_module = FakeHou(viewport, cameras)
        cache_root = self.project_root / ".runtime" / "cache"
        with mock.patch.dict(os.environ, {"HIA_CACHE_DIR": str(cache_root)}, clear=False):
            executor = HoudiniExecutor(
                hou_module=hou_module,
                main_thread_runner=lambda callback: callback(),
                project_root=self.project_root,
            )
        return hou_module, executor

    def test_viewport_reports_png_ihdr_dimensions_and_restores_camera(self) -> None:
        original_camera = FakeCamera("/obj/original_camera")
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=original_camera,
            default_camera_state="original-camera-view",
            camera_locked=True,
            image_size=(913, 517),
        )
        hou_module, executor = self.make_executor(viewport, original_camera, capture_camera)

        response = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "viewport",
                "camera_path": capture_camera.path,
                "width": 1280,
                "height": 720,
                "return_image": False,
            },
        )

        self.assertEqual((913, 517), (response["result"]["width"], response["result"]["height"]))
        self.assertIs(capture_camera, viewport.camera_at_save)
        self.assertFalse(viewport.camera_lock_at_save)
        self.assertIs(original_camera, viewport.camera())
        self.assertEqual("original-camera-view", viewport.defaultCamera().state)
        self.assertNotIn("set_default", viewport.camera_restore_calls)
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)

    def test_failed_viewport_capture_restores_default_camera_and_lock(self) -> None:
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="original-free-view",
            camera_locked=True,
            image_size=(640, 360),
            fail_save=True,
        )
        hou_module, executor = self.make_executor(viewport, capture_camera)

        with self.assertRaises(HiaRuntimeError) as raised:
            executor.dispatch(
                "hia_capture_viewport",
                {"mode": "viewport", "camera_path": capture_camera.path},
            )

        self.assertEqual("VIEWPORT_CAPTURE_FAILED", raised.exception.code)
        self.assertIs(capture_camera, viewport.camera_at_save)
        self.assertFalse(viewport.camera_lock_at_save)
        self.assertIsNone(viewport.camera())
        self.assertEqual("original-free-view", viewport.defaultCamera().state)
        self.assertIn("set_default", viewport.camera_restore_calls)
        self.assertTrue(viewport.isCameraLockedToView())

    def test_flipbook_disables_mplay_uses_resolution_and_restores_frame(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="original-free-view",
            camera_locked=False,
            image_size=(1, 1),
        )
        hou_module, executor = self.make_executor(viewport)
        original_settings = hou_module.scene_viewer.original_flipbook_settings

        response = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "flipbook",
                "frame_range": [3, 5],
                "width": 640,
                "height": 360,
                "return_image": False,
            },
        )

        used = hou_module.scene_viewer.used_flipbook_settings
        self.assertIsNotNone(used)
        assert used is not None
        self.assertEqual((640, 360), used.image_resolution)
        self.assertTrue(used.use_resolution)
        self.assertEqual(100, used.output_zoom)
        self.assertFalse(used.use_sheet_size)
        self.assertFalse(used.output_to_mplay)
        self.assertFalse(hou_module.scene_viewer.open_dialog)
        self.assertEqual((640, 360), (response["result"]["width"], response["result"]["height"]))
        self.assertEqual(12.0, hou_module.frame())
        self.assertEqual([5.0, 12.0], hou_module.frame_history)
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)
        self.assertEqual(0, hou_module.scene_viewer.mplay_launches)
        self.assertTrue(original_settings.output_to_mplay)
        self.assertFalse(original_settings.use_resolution)
        self.assertEqual("original-free-view", viewport.defaultCamera().state)
        self.assertIsNone(viewport.camera())
        self.assertEqual([], viewport.camera_restore_calls)

    def test_default_stage_flipbook_is_low_resolution_and_restores_viewer_state(self) -> None:
        original_camera = FakeCamera("/obj/original_camera")
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=original_camera,
            default_camera_state="original-camera-view",
            camera_locked=True,
            image_size=(1, 1),
        )
        hou_module, executor = self.make_executor(
            viewport,
            original_camera,
            capture_camera,
        )

        response = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "flipbook",
                "camera_path": capture_camera.path,
                "return_image": False,
            },
        )

        used = hou_module.scene_viewer.used_flipbook_settings
        self.assertIsNotNone(used)
        assert used is not None
        self.assertEqual((12.0, 12.0), used.frame_range)
        self.assertEqual((640, 360), used.image_resolution)
        self.assertEqual((640, 360), (response["result"]["width"], response["result"]["height"]))
        self.assertIs(capture_camera, hou_module.scene_viewer.camera_at_flipbook)
        self.assertFalse(hou_module.scene_viewer.camera_lock_at_flipbook)
        self.assertIs(original_camera, viewport.camera())
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual("original-camera-view", viewport.defaultCamera().state)
        self.assertEqual([12.0, 12.0], hou_module.frame_history)
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)
        self.assertEqual(0, hou_module.scene_viewer.mplay_launches)

    def test_flipbook_rejects_invalid_or_excessive_ranges_before_capture(self) -> None:
        cases = (
            ([5, 3], "end must not precede"),
            ([1, float("nan")], "finite numbers"),
            ([1, 241.01], "at most 240 frames"),
        )
        for frame_range, expected_message in cases:
            with self.subTest(frame_range=frame_range):
                viewport = FakeViewport(
                    original_camera=None,
                    default_camera_state="original-free-view",
                    camera_locked=False,
                    image_size=(1, 1),
                )
                hou_module, executor = self.make_executor(viewport)

                with self.assertRaises(HiaRuntimeError) as raised:
                    executor.dispatch(
                        "hia_capture_viewport",
                        {
                            "mode": "flipbook",
                            "frame_range": frame_range,
                            "return_image": False,
                        },
                    )

                self.assertEqual("INVALID_ARGUMENTS", raised.exception.code)
                self.assertIn(expected_message, str(raised.exception))
                self.assertIsNone(hou_module.scene_viewer.used_flipbook_settings)
                self.assertEqual([], hou_module.frame_history)
                self.assertEqual(0, hou_module.scene_viewer.focus_calls)
                screenshot_root = self.project_root / ".runtime" / "cache" / "screenshots"
                self.assertEqual([], list(screenshot_root.glob("*.png")))

    def test_camera_restore_failure_does_not_skip_lock_or_frame_restore(self) -> None:
        original_camera = FakeCamera("/obj/original_camera")
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=original_camera,
            default_camera_state="original-camera-view",
            camera_locked=True,
            image_size=(640, 360),
            fail_restore_camera_path=original_camera.path,
        )
        hou_module, executor = self.make_executor(
            viewport,
            original_camera,
            capture_camera,
        )

        with self.assertRaises(HiaRuntimeError) as raised:
            executor.dispatch(
                "hia_capture_viewport",
                {
                    "mode": "flipbook",
                    "camera_path": capture_camera.path,
                    "frame_range": [3, 5],
                    "return_image": False,
                },
            )

        self.assertEqual("VIEWPORT_STATE_RESTORE_FAILED", raised.exception.code)
        self.assertEqual("restore_camera", raised.exception.details["errors"][0]["operation"])
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual(12.0, hou_module.frame())
        self.assertEqual([5.0, 12.0], hou_module.frame_history)


if __name__ == "__main__":
    unittest.main()
