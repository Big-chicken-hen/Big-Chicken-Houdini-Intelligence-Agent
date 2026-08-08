from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import zlib
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402


_UNSET = object()


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def _png_bytes(
    width: int,
    height: int,
    color: tuple[int, int, int] = (72, 104, 136),
    alternate_color: tuple[int, int, int] | None = None,
) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = []
    for row_index in range(height):
        row_color = (
            alternate_color
            if alternate_color is not None and row_index % 2
            else color
        )
        rows.append(b"\x00" + bytes(row_color) * width)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _png_chunk(b"IEND", b"")
    )


@dataclass(frozen=True)
class FakeCamera:
    path: str


class FakeCookNode:
    def __init__(self, path: str, hou_module: "FakeHou") -> None:
        self.path = path
        self.hou = hou_module
        self._cook_count = 0
        self.cook_frames: list[float] = []
        self.cook_forces: list[bool] = []

    def needsToCook(self) -> bool:  # noqa: N802
        return True

    def isTimeDependent(self, *, for_last_cook: bool = False) -> bool:  # noqa: N802
        del for_last_cook
        return True

    def cookCount(self) -> int:  # noqa: N802
        return self._cook_count

    def lastCookTime(self) -> float:  # noqa: N802
        return float(self._cook_count)

    def cook(self, *, force: bool) -> None:
        self.cook_frames.append(self.hou.frame())
        self.cook_forces.append(force)
        self._cook_count += 1


@dataclass
class FakeViewportCamera:
    state: str

    def stash(self) -> "FakeViewportCamera":
        return FakeViewportCamera(self.state)


class FakeDisplaySet:
    def shadedMode(self) -> str:  # noqa: N802
        return "Smooth"


class FakeViewportSettings:
    def displaySet(self, _display_set_type: object) -> FakeDisplaySet:  # noqa: N802
        return FakeDisplaySet()

    def viewportType(self) -> str:  # noqa: N802
        return "Perspective"

    def lighting(self) -> str:
        return "HighQualityWithShadows"

    def showingMaterials(self) -> bool:  # noqa: N802
        return True

    def showingDiffuse(self) -> bool:  # noqa: N802
        return True

    def showingSpecular(self) -> bool:  # noqa: N802
        return True

    def showingAmbient(self) -> bool:  # noqa: N802
        return True

    def showingEmission(self) -> bool:  # noqa: N802
        return True

    def usingTransparency(self) -> bool:  # noqa: N802
        return True

    def showingGeometryColor(self) -> bool:  # noqa: N802
        return True

    def usingAspectRatio(self) -> bool:  # noqa: N802
        return False

    def aspectRatio(self) -> float:  # noqa: N802
        return 16 / 9

    def viewAspectRatio(self, _masked: bool) -> float:  # noqa: N802
        return 16 / 9


class FakeViewport:
    def __init__(
        self,
        *,
        original_camera: FakeCamera | None,
        default_camera_state: str,
        camera_locked: bool,
        image_size: tuple[int, int],
        image_color: tuple[int, int, int] = (72, 104, 136),
        alternate_image_color: tuple[int, int, int] | None = None,
        fail_save: bool = False,
        fail_restore_camera_path: str | None = None,
    ) -> None:
        self._camera = original_camera
        self._default_camera = FakeViewportCamera(default_camera_state)
        self._camera_locked = camera_locked
        self._image_size = image_size
        self._image_color = image_color
        self._alternate_image_color = alternate_image_color
        self._fail_save = fail_save
        self._fail_restore_camera_path = fail_restore_camera_path
        self._settings = FakeViewportSettings()
        self.camera_at_save: FakeCamera | None = None
        self.camera_lock_at_save: bool | None = None
        self.camera_restore_calls: list[str] = []

    def camera(self) -> FakeCamera | None:
        return self._camera

    def name(self) -> str:
        return "persp1"

    def isVisible(self) -> bool:  # noqa: N802
        return True

    def size(self) -> tuple[int, int, int, int]:
        return (0, 0, self._image_size[0], self._image_size[1])

    def type(self) -> str:
        return "Perspective"

    def settings(self) -> FakeViewportSettings:
        return self._settings

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
        Path(path).write_bytes(
            _png_bytes(
                *self._image_size,
                color=self._image_color,
                alternate_color=self._alternate_image_color,
            )
        )


class FakeFlipbookSettings:
    def __init__(self) -> None:
        self.frame_range = (1.0, 24.0)
        self.output_path = "original.$F4.png"
        self.image_resolution = (320, 200)
        self.use_resolution = False
        self.output_zoom = 50
        self.use_sheet_size = True
        self.output_to_mplay = True
        self.crop_mask = False
        self.override_gamma = False
        self.gamma_value = 2.2
        self.override_lut = False
        self.lut_path = ""

    def stash(self) -> "FakeFlipbookSettings":
        copy = FakeFlipbookSettings()
        copy.frame_range = self.frame_range
        copy.output_path = self.output_path
        copy.image_resolution = self.image_resolution
        copy.use_resolution = self.use_resolution
        copy.output_zoom = self.output_zoom
        copy.use_sheet_size = self.use_sheet_size
        copy.output_to_mplay = self.output_to_mplay
        copy.crop_mask = self.crop_mask
        copy.override_gamma = self.override_gamma
        copy.gamma_value = self.gamma_value
        copy.override_lut = self.override_lut
        copy.lut_path = self.lut_path
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

    def cropOutMaskOverlay(self, value: object = _UNSET) -> bool | None:  # noqa: N802
        if value is _UNSET:
            return self.crop_mask
        self.crop_mask = bool(value)
        return None

    def overrideGamma(self, value: object = _UNSET) -> bool | None:  # noqa: N802
        if value is _UNSET:
            return self.override_gamma
        self.override_gamma = bool(value)
        return None

    def gamma(self, value: object = _UNSET) -> float | None:
        if value is _UNSET:
            return self.gamma_value
        self.gamma_value = float(value)
        return None

    def overrideLUT(self, value: object = _UNSET) -> bool | None:  # noqa: N802
        if value is _UNSET:
            return self.override_lut
        self.override_lut = bool(value)
        return None

    def LUT(self, value: object = _UNSET) -> str | None:  # noqa: N802
        if value is _UNSET:
            return self.lut_path
        self.lut_path = str(value)
        return None


class FakeHipFile:
    def __init__(self) -> None:
        self.current_path = "untitled.hip"
        self.new_file = True

    def path(self) -> str:
        return self.current_path

    def isNewFile(self) -> bool:  # noqa: N802
        return self.new_file

    def hasUnsavedChanges(self) -> bool:  # noqa: N802
        return False


class FakePaneTabType:
    SceneViewer = object()


class FakeDisplaySetType:
    DisplayModel = object()


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
        self.flipbook_output_size: tuple[int, int] | None = None
        self.frame_colors: dict[int, tuple[int, int, int]] = {}
        self.missing_frames: set[int] = set()
        self.color_state_available = True

    def name(self) -> str:
        return "sceneviewer1"

    def viewportLayout(self) -> str:  # noqa: N802
        return "Single"

    def usingOCIO(self) -> bool:  # noqa: N802
        if not self.color_state_available:
            raise RuntimeError("OCIO state unavailable")
        return True

    def getOCIODisplay(self) -> str:  # noqa: N802
        if not self.color_state_available:
            raise RuntimeError("OCIO display unavailable")
        return "sRGB"

    def getOCIOView(self) -> str:  # noqa: N802
        if not self.color_state_available:
            raise RuntimeError("OCIO view unavailable")
        return "ACES 1.0 - SDR Video"

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
        if self.viewport._fail_save:
            raise RuntimeError("simulated viewport capture failure")
        end = float(settings.frame_range[1])
        self.hou.setFrame(end)
        first = int(round(float(settings.frame_range[0])))
        if first in self.missing_frames:
            return
        output_path = settings.output_path.replace("$F4", f"{first:04d}")
        output_size = self.flipbook_output_size or settings.image_resolution
        Path(output_path).write_bytes(
            _png_bytes(
                *output_size,
                color=self.frame_colors.get(first, self.viewport._image_color),
                alternate_color=self.viewport._alternate_image_color,
            )
        )


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
        self.displaySetType = FakeDisplaySetType()
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
        self._temporary = tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / "tests"
        )
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

    def test_capture_always_uses_project_cache_even_for_saved_hip(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(320, 200),
        )
        hou_module, executor = self.make_executor(viewport)
        hip_directory = self.project_root / "saved-scene"
        hip_directory.mkdir()
        hou_module.hipFile.current_path = str(hip_directory / "asset.hip")
        hou_module.hipFile.new_file = False

        response = executor.dispatch(
            "hia_capture_viewport",
            {"return_image": False},
        )

        expected_root = (
            self.project_root / ".runtime" / "cache" / "screenshots"
        ).resolve()
        self.assertEqual("project_cache", response["result"]["storage_scope"])
        self.assertEqual(
            expected_root,
            Path(response["result"]["absolute_path"]).parent,
        )
        self.assertFalse((hip_directory / ".hia").exists())

    def test_reparse_runtime_screenshot_fallback_is_rejected_before_write(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(320, 200),
        )
        _hou_module, executor = self.make_executor(viewport)
        screenshot_root = (
            self.project_root / ".runtime" / "cache" / "screenshots"
        )
        screenshot_root.mkdir(parents=True)

        with (
            mock.patch(
                "hia_mcp_runtime.executor._is_reparse_point",
                side_effect=lambda path: Path(path) == screenshot_root,
            ),
            self.assertRaises(HiaRuntimeError),
        ):
            executor.dispatch(
                "hia_capture_viewport",
                {"return_image": False},
            )

        self.assertEqual([], list(screenshot_root.glob("*.png")))

    def test_viewport_uses_display_flipbook_and_restores_camera(self) -> None:
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

        self.assertEqual((1280, 720), (response["result"]["width"], response["result"]["height"]))
        self.assertEqual("scene_viewer.flipbook", response["result"]["capture_api"])
        self.assertTrue(response["result"]["capture_ok"])
        self.assertEqual("project_cache", response["result"]["storage_scope"])
        self.assertEqual("persp1", response["result"]["source_state"]["viewport"]["name"])
        self.assertEqual(
            capture_camera.path,
            response["result"]["source_state"]["camera"]["path"],
        )
        self.assertEqual(
            "HighQualityWithShadows",
            response["result"]["source_state"]["display_options"]["lighting"],
        )
        self.assertEqual(
            "Smooth",
            response["result"]["source_state"]["display_options"]["shading"],
        )
        self.assertTrue(
            response["result"]["source_state"]["display_options"]["materials"]
        )
        self.assertEqual(
            "Perspective",
            response["result"]["source_state"]["viewport"]["projection"],
        )
        self.assertEqual(
            "sRGB",
            response["result"]["source_state"]["color_management"]["ocio_display"],
        )
        self.assertIs(capture_camera, hou_module.scene_viewer.camera_at_flipbook)
        self.assertFalse(hou_module.scene_viewer.camera_lock_at_flipbook)
        self.assertIs(original_camera, viewport.camera())
        self.assertEqual("original-camera-view", viewport.defaultCamera().state)
        self.assertNotIn("set_default", viewport.camera_restore_calls)
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)

    def test_capture_rejects_resolution_mismatch(self) -> None:
        for output_size in ((640, 480), (800, 450)):
            with self.subTest(output_size=output_size):
                viewport = FakeViewport(
                    original_camera=None,
                    default_camera_state="view",
                    camera_locked=False,
                    image_size=(640, 360),
                )
                hou_module, executor = self.make_executor(viewport)
                hou_module.scene_viewer.flipbook_output_size = output_size

                with self.assertRaises(HiaRuntimeError) as raised:
                    executor.dispatch(
                        "hia_capture_viewport",
                        {"width": 640, "height": 360, "return_image": False},
                    )
                self.assertEqual(
                    "VIEWPORT_CAPTURE_UNAVAILABLE",
                    raised.exception.code,
                )

    def test_capture_rejects_requested_camera_mismatch(self) -> None:
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(640, 360),
        )
        _hou_module, executor = self.make_executor(viewport, capture_camera)

        with mock.patch.object(viewport, "setCamera", return_value=None):
            with self.assertRaises(HiaRuntimeError) as raised:
                executor.dispatch(
                    "hia_capture_viewport",
                    {
                        "camera_path": capture_camera.path,
                        "return_image": False,
                    },
                )
        self.assertEqual("VIEWPORT_CAPTURE_UNAVAILABLE", raised.exception.code)

    def test_capture_requires_scene_viewer_flipbook(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(640, 360),
        )
        hou_module, executor = self.make_executor(viewport)
        hou_module.scene_viewer.flipbook = None  # type: ignore[method-assign]

        with self.assertRaises(HiaRuntimeError) as raised:
            executor.dispatch(
                "hia_capture_viewport",
                {"return_image": False},
            )
        self.assertEqual("VIEWPORT_CAPTURE_UNAVAILABLE", raised.exception.code)

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

        self.assertEqual("VIEWPORT_CAPTURE_UNAVAILABLE", raised.exception.code)
        self.assertIs(capture_camera, hou_module.scene_viewer.camera_at_flipbook)
        self.assertFalse(hou_module.scene_viewer.camera_lock_at_flipbook)
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
        original_settings.override_gamma = True
        original_settings.gamma_value = 1.8
        original_settings.override_lut = True
        original_settings.lut_path = str(self.project_root / "display.cube")
        hou_module.scene_viewer.frame_colors = {
            3: (72, 104, 136),
            4: (88, 118, 148),
            5: (104, 132, 160),
        }

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
        self.assertTrue(used.override_gamma)
        self.assertEqual(1.8, used.gamma_value)
        self.assertTrue(used.override_lut)
        self.assertEqual(
            str(self.project_root / "display.cube"),
            used.lut_path,
        )
        self.assertFalse(hou_module.scene_viewer.open_dialog)
        self.assertEqual((640, 360), (response["result"]["width"], response["result"]["height"]))
        self.assertTrue(response["ok"])
        self.assertEqual("passed", response["result"]["sequence"]["status"])
        self.assertEqual([3.0, 4.0, 5.0], response["result"]["requested_frames"])
        self.assertEqual([3.0, 4.0, 5.0], response["result"]["actual_frames"])
        self.assertEqual(3, response["result"]["sequence"]["captured_count"])
        self.assertEqual(12.0, hou_module.frame())
        self.assertEqual(
            [3.0, 3.0, 12.0, 4.0, 4.0, 12.0, 5.0, 5.0, 12.0, 12.0],
            hou_module.frame_history,
        )
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)
        self.assertEqual(0, hou_module.scene_viewer.mplay_launches)
        self.assertTrue(original_settings.output_to_mplay)
        self.assertFalse(original_settings.use_resolution)
        self.assertEqual(
            1.8,
            response["result"]["source_state"]["color_management"][
                "flipbook_gamma"
            ],
        )
        self.assertEqual(
            str(self.project_root / "display.cube"),
            response["result"]["source_state"]["color_management"][
                "flipbook_lut"
            ],
        )
        self.assertEqual("original-free-view", viewport.defaultCamera().state)
        self.assertIsNone(viewport.camera())
        self.assertEqual([], viewport.camera_restore_calls)

    def test_default_stage_flipbook_derives_viewport_resolution_and_restores_viewer_state(self) -> None:
        original_camera = FakeCamera("/obj/original_camera")
        capture_camera = FakeCamera("/obj/capture_camera")
        viewport = FakeViewport(
            original_camera=original_camera,
            default_camera_state="original-camera-view",
            camera_locked=True,
            image_size=(960, 540),
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
        self.assertEqual((960, 540), used.image_resolution)
        self.assertEqual((960, 540), (response["result"]["width"], response["result"]["height"]))
        self.assertEqual("viewport", response["result"]["resolution_source"])
        self.assertAlmostEqual(16 / 9, response["result"]["aspect_ratio"])
        self.assertEqual(12.0, response["result"]["requested_frame"])
        self.assertEqual(12.0, response["result"]["actual_frame"])
        self.assertIs(capture_camera, hou_module.scene_viewer.camera_at_flipbook)
        self.assertFalse(hou_module.scene_viewer.camera_lock_at_flipbook)
        self.assertIs(original_camera, viewport.camera())
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual("original-camera-view", viewport.defaultCamera().state)
        self.assertEqual([12.0, 12.0, 12.0], hou_module.frame_history)
        self.assertEqual(0, hou_module.scene_viewer.focus_calls)
        self.assertEqual(0, hou_module.scene_viewer.mplay_launches)

    def test_flipbook_rejects_invalid_or_excessive_ranges_before_capture(self) -> None:
        cases = (
            ([5, 3], "end must not precede"),
            ([1, float("nan")], "finite frame numbers"),
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
                    "frame_range": [3, 3],
                    "return_image": False,
                },
            )

        self.assertEqual("VIEWPORT_CAPTURE_UNAVAILABLE", raised.exception.code)
        self.assertEqual("restore_camera", raised.exception.details["errors"][0]["operation"])
        self.assertTrue(viewport.isCameraLockedToView())
        self.assertEqual(12.0, hou_module.frame())
        self.assertEqual([3.0, 3.0, 12.0], hou_module.frame_history)

    def test_single_dimension_preserves_viewport_aspect(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(1280, 720),
        )
        hou_module, executor = self.make_executor(viewport)

        response = executor.dispatch(
            "hia_capture_viewport",
            {"width": 800, "return_image": False},
        )

        self.assertTrue(response["ok"])
        self.assertEqual((800, 450), (
            response["result"]["width"],
            response["result"]["height"],
        ))
        self.assertEqual(
            "requested_width_viewport_aspect",
            response["result"]["resolution_source"],
        )
        self.assertEqual(12.0, hou_module.frame())

    def test_sequence_reports_no_change_and_missing_frames_but_restores_frame(
        self,
    ) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(320, 180),
        )
        hou_module, executor = self.make_executor(viewport)

        unchanged = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "flipbook",
                "frames": [1, 2, 3],
                "return_image": False,
            },
        )
        self.assertFalse(unchanged["ok"])
        self.assertTrue(
            unchanged["result"]["sequence"]["no_change_detected"]
        )
        self.assertEqual(
            "not_proven",
            unchanged["result"]["sequence"]["simulation_advancement"],
        )
        self.assertEqual(12.0, hou_module.frame())

    def test_sequence_force_cooks_each_validation_target_at_the_locked_frame(
        self,
    ) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(320, 180),
        )
        hou_module, executor = self.make_executor(viewport)
        target = FakeCookNode("/obj/OUT_SIM", hou_module)
        hou_module._cameras[target.path] = target  # type: ignore[assignment]
        hou_module.scene_viewer.frame_colors = {
            1: (72, 104, 136),
            2: (88, 118, 148),
            3: (104, 132, 160),
        }

        response = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "flipbook",
                "frames": [1, 2, 3],
                "validation_paths": [target.path],
                "return_image": False,
            },
        )

        self.assertTrue(response["ok"])
        self.assertEqual([1.0, 2.0, 3.0], target.cook_frames)
        self.assertEqual([True, True, True], target.cook_forces)
        self.assertEqual(
            [1.0, 2.0, 3.0],
            [
                item["cook_frame"]
                for item in response["result"]["sequence"]["frames"]
            ],
        )
        self.assertEqual(12.0, hou_module.frame())

    def test_sequence_reports_missing_frames_and_restores_frame(self) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="view",
            camera_locked=False,
            image_size=(320, 180),
        )
        hou_module, executor = self.make_executor(viewport)
        hou_module.scene_viewer.frame_colors = {
            1: (72, 104, 136),
            2: (88, 118, 148),
            3: (104, 132, 160),
        }
        hou_module.scene_viewer.missing_frames = {2}
        missing = executor.dispatch(
            "hia_capture_viewport",
            {
                "mode": "flipbook",
                "frames": [1, 2, 3],
                "return_image": False,
            },
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(
            [2.0],
            missing["result"]["sequence"]["missing_or_failed_frames"],
        )
        self.assertEqual(12.0, hou_module.frame())


if __name__ == "__main__":
    unittest.main()
