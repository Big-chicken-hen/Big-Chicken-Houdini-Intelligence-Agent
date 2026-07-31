from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor, HiaRuntimeError  # noqa: E402
from tests.unit.test_hia_mcp_v2_viewport_state import (  # noqa: E402
    FakeCamera,
    FakeHou as ViewportFakeHou,
    FakeViewport,
)


class FakeNamed:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class FakeNodeType:
    def __init__(self, name: str, category: str) -> None:
        self._name = name
        self._category = category

    def name(self) -> str:
        return self._name

    def category(self) -> FakeNamed:
        return FakeNamed(self._category)

    def description(self) -> str:
        return self._name

    def nameComponents(self) -> tuple[str, ...]:  # noqa: N802
        return ("", self._name, "", "")


class FakeParm:
    def __init__(
        self,
        path: str,
        value: Any,
        actions: list[tuple[Any, ...]],
        *,
        button: bool = False,
        fail_value: Any = object(),
    ) -> None:
        self._path = path
        self._value = value
        self._actions = actions
        self._button = button
        self._fail_value = fail_value

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    def eval(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._actions.append(("set", self._path, value))
        if value == self._fail_value:
            raise RuntimeError(f"simulated set failure for {self._path}")
        self._value = value

    def pressButton(self) -> None:  # noqa: N802
        if not self._button:
            raise RuntimeError("not a button")
        self._actions.append(("reset", self._path))

    def keyframes(self) -> tuple[Any, ...]:
        return ()

    def isTimeDependent(self) -> bool:  # noqa: N802
        return False

    def isLocked(self) -> bool:  # noqa: N802
        return False

    def isDisabled(self) -> bool:  # noqa: N802
        return False

    def unexpandedString(self) -> str:  # noqa: N802
        return str(self._value)


class FakeBoundingBox:
    def __init__(self, center: tuple[float, float, float]) -> None:
        self._center = center

    def minvec(self) -> tuple[float, float, float]:
        return tuple(value - 1.0 for value in self._center)

    def maxvec(self) -> tuple[float, float, float]:
        return tuple(value + 1.0 for value in self._center)

    def sizevec(self) -> tuple[float, float, float]:
        return (2.0, 2.0, 2.0)

    def center(self) -> tuple[float, float, float]:
        return self._center


class FakePrimitive:
    def type(self) -> FakeNamed:
        return FakeNamed("Volume")

    def vertices(self) -> tuple[Any, ...]:
        return ()


class FakeGeometry:
    def __init__(self, hou_module: "FakeHou") -> None:
        self._hou = hou_module

    def boundingBox(self) -> FakeBoundingBox:  # noqa: N802
        return FakeBoundingBox((self._hou.frame(), 0.0, 0.0))

    def intrinsicValue(self, name: str) -> int:  # noqa: N802
        return {
            "pointcount": 8,
            "vertexcount": 8,
            "primitivecount": 1,
        }[name]

    def prims(self) -> tuple[FakePrimitive, ...]:
        return (FakePrimitive(),)

    def pointGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()

    def primGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()

    def edgeGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()


class FakeNode:
    def __init__(
        self,
        path: str,
        hou_module: "FakeHou",
        *,
        category: str = "Obj",
        parms: tuple[FakeParm, ...] = (),
        inside_locked_hda: bool = False,
    ) -> None:
        self._path = path
        self._hou = hou_module
        self._category = category
        self._parms = parms
        self._inside_locked_hda = inside_locked_hda
        self._children: tuple[FakeNode, ...] = ()
        self._cook_count = 0
        self._needs_cook = True
        self.cook_frames: list[float] = []

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    def type(self) -> FakeNodeType:
        return FakeNodeType(self.name(), self._category)

    def inputs(self) -> tuple[Any, ...]:
        return ()

    def outputs(self) -> tuple[Any, ...]:
        return ()

    def parms(self) -> tuple[FakeParm, ...]:
        return self._parms

    def allSubChildren(self) -> tuple["FakeNode", ...]:  # noqa: N802
        return self._children

    def children(self) -> tuple["FakeNode", ...]:
        return self._children

    def errors(self) -> tuple[str, ...]:
        return ()

    def warnings(self) -> tuple[str, ...]:
        return ()

    def isDisplayFlagSet(self) -> bool:  # noqa: N802
        return self.name() == "OUT"

    def isRenderFlagSet(self) -> bool:  # noqa: N802
        return self.name() == "OUT"

    def isBypassed(self) -> bool:  # noqa: N802
        return False

    def isInsideLockedHDA(self) -> bool:  # noqa: N802
        return self._inside_locked_hda

    def needsToCook(self) -> bool:  # noqa: N802
        return self._needs_cook

    def isTimeDependent(self, *, for_last_cook: bool = False) -> bool:  # noqa: N802
        del for_last_cook
        return True

    def cookCount(self) -> int:  # noqa: N802
        return self._cook_count

    def lastCookTime(self) -> float:  # noqa: N802
        return float(self._cook_count)

    def cook(self, *, force: bool) -> None:
        self._hou.actions.append(("cook", self._path, self._hou.frame(), force))
        self.cook_frames.append(self._hou.frame())
        self._cook_count += 1
        self._needs_cook = False

    def geometry(self) -> FakeGeometry:
        return FakeGeometry(self._hou)


class FakeUndos:
    def __init__(self, actions: list[tuple[Any, ...]]) -> None:
        self._actions = actions
        self.labels: list[str] = []
        self.undo_calls = 0
        self.foreign_after_group = False

    def areEnabled(self) -> bool:  # noqa: N802
        return True

    @contextmanager
    def group(self, label: str) -> Any:
        self._actions.append(("undo_group_begin", label))
        try:
            yield
        finally:
            self.labels.insert(0, label)
            if self.foreign_after_group:
                self.labels.insert(0, "User Edit")
            self._actions.append(("undo_group_end", label))

    def undoLabels(self) -> tuple[str, ...]:  # noqa: N802
        return tuple(self.labels)

    def performUndo(self) -> None:  # noqa: N802
        self.undo_calls += 1
        self._actions.append(("undo", self.labels[0]))
        self.labels.pop(0)


class FakeHou(ViewportFakeHou):
    def __init__(self, *, failing_value: Any = object()) -> None:
        viewport = FakeViewport(
            original_camera=None,
            default_camera_state="experiment-view",
            camera_locked=False,
            image_size=(800, 400),
            alternate_image_color=(96, 128, 160),
        )
        camera = FakeCamera("/obj/cam1")
        super().__init__(viewport, (camera,))
        self.actions: list[tuple[Any, ...]] = []
        self.undos = FakeUndos(self.actions)
        self.density = FakeParm(
            "/obj/fx/control/density",
            1.0,
            self.actions,
            fail_value=failing_value,
        )
        self.disturbance = FakeParm(
            "/obj/fx/control/disturbance",
            0.25,
            self.actions,
        )
        self.reset = FakeParm(
            "/obj/fx/control/reset",
            False,
            self.actions,
            button=True,
        )
        self.control = FakeNode(
            "/obj/fx/control",
            self,
            parms=(self.density, self.disturbance, self.reset),
        )
        self.output = FakeNode("/obj/fx/OUT", self, category="Sop")
        self.network = FakeNode("/obj/fx", self)
        self.network._children = (self.control, self.output)
        self._nodes = {
            value.path(): value
            for value in (self.network, self.control, self.output)
        }
        self._parms = {
            value.path(): value
            for value in (self.density, self.disturbance, self.reset)
        }
        original_flipbook = self.scene_viewer.flipbook

        def recorded_flipbook(*args: Any, **kwargs: Any) -> None:
            self.actions.append(("capture", self.frame()))
            original_flipbook(*args, **kwargs)

        self.scene_viewer.flipbook = recorded_flipbook  # type: ignore[method-assign]
        self.scene_viewer.frame_colors.update(
            {
                1: (80, 112, 144),
                24: (112, 144, 176),
                44: (144, 176, 208),
            }
        )

    def node(self, path: str) -> Any:
        return self._nodes.get(path) or super().node(path)

    def parm(self, path: str) -> FakeParm | None:
        return self._parms.get(path)

    def setFrame(self, frame: float) -> None:  # noqa: N802
        changed = float(frame) != self.frame()
        self.actions.append(("frame", float(frame)))
        super().setFrame(frame)
        if changed:
            self.output._needs_cook = True


class HiaMcpV2EffectExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / "tests"
        )
        self.project_root = Path(self._temporary.name) / "effect-project"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def make_executor(
        self,
        *,
        failing_value: Any = object(),
    ) -> tuple[FakeHou, HoudiniExecutor]:
        hou_module = FakeHou(failing_value=failing_value)
        cache_root = self.project_root / ".runtime" / "cache"
        with mock.patch.dict(
            os.environ,
            {"HIA_CACHE_DIR": str(cache_root)},
            clear=False,
        ):
            executor = HoudiniExecutor(
                hou_module=hou_module,
                main_thread_runner=lambda callback: callback(),
                project_root=self.project_root,
            )
        return hou_module, executor

    @staticmethod
    def arguments() -> dict[str, Any]:
        return {
            "target_network": "/obj/fx",
            "baseline": {"parameters": {}},
            "candidates": [
                {
                    "name": "dense",
                    "parameters": {"/obj/fx/control/density": 2.0},
                },
                {
                    "name": "disturbed",
                    "parameters": {"/obj/fx/control/disturbance": 0.75},
                },
            ],
            "frame_range": [1, 48],
            "sample_frames": [1, 24, 44],
            "preview": {"width": 400, "quality_scale": 0.5},
            "camera_path": "/obj/cam1",
            "framing": "camera",
            "display_mode": "Smooth",
            "capture_mode": "contact_sheet",
            "cache_reset_parms": ["/obj/fx/control/reset"],
            "cook_targets": ["/obj/fx/OUT"],
            "metric_targets": ["/obj/fx/OUT"],
            "timeout_seconds": 300,
        }

    def test_success_is_multiframe_locked_and_fully_restored(self) -> None:
        hou_module, executor = self.make_executor()

        response = executor.dispatch(
            "hia_run_effect_experiment",
            self.arguments(),
        )

        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual("completed", result["status"])
        self.assertEqual(
            {
                "requested_timeout_seconds": 300.0,
                "timeout_kind": "client_wait_budget",
                "interruptible_after_main_thread_entry": False,
                "hom_may_continue_after_client_timeout": True,
                "automatic_retry_after_timeout": False,
            },
            response["execution_limit"],
        )
        self.assertEqual([200, 100], [
            result["preview"]["width"],
            result["preview"]["height"],
        ])
        self.assertEqual("verified", result["view_lock"]["status"])
        self.assertEqual("observed", result["cache_reset"])
        self.assertEqual(3, len(result["variants"]))
        self.assertEqual(
            list(range(1, 49)) * 3,
            [int(value) for value in hou_module.output.cook_frames],
        )
        self.assertEqual(
            3,
            sum(action[0] == "reset" for action in hou_module.actions),
        )
        for variant in result["variants"]:
            self.assertEqual("completed", variant["status"])
            self.assertEqual("recompute_verified", variant["freshness"])
            self.assertEqual([1, 24, 44], [
                value["requested_frame"]
                for value in variant["sample_frames"]
            ])
            self.assertEqual(
                {
                    "sample_count": 3,
                    "unique_hash_count": 3,
                    "no_change_detected": False,
                    "simulation_advancement": "observed_visual_change",
                },
                variant["temporal_evidence"],
            )
            for frame in variant["sample_frames"]:
                self.assertEqual(
                    frame["requested_frame"],
                    frame["actual_frame"],
                )
                self.assertEqual(
                    frame["requested_frame"],
                    frame["cook_frame"],
                )
                geometry = frame["geometry"][0]
                self.assertEqual(1, geometry["primitive_count"])
                self.assertEqual(1, geometry["volume_primitive_count"])
                self.assertIn("center", geometry["bbox"])
                self.assertIn("size", geometry["bbox"])
        dense = result["variants"][1]
        actual = {
            value["path"]: value
            for value in dense["actual_parameters"]
        }
        self.assertEqual(2.0, actual["/obj/fx/control/density"]["actual"])
        self.assertEqual(
            "candidate",
            actual["/obj/fx/control/density"]["source"],
        )
        self.assertEqual(
            "scene_baseline",
            actual["/obj/fx/control/disturbance"]["source"],
        )

        sheet = result["contact_sheet"]
        self.assertEqual("created", sheet["status"])
        self.assertEqual(3, sheet["rows"])
        self.assertEqual(3, sheet["columns"])
        self.assertEqual(9, len(sheet["source_frames"]))
        payload = Path(sheet["absolute_path"]).read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", payload)
        self.assertIn("dense", payload)
        self.assertIn("frame 44", payload)
        self.assertEqual("verified", result["restoration"]["status"])
        self.assertEqual(1.0, hou_module.density.eval())
        self.assertEqual(0.25, hou_module.disturbance.eval())
        self.assertEqual(12.0, hou_module.frame())
        self.assertEqual(1, hou_module.undos.undo_calls)

    def test_candidate_failure_continues_and_still_restores(self) -> None:
        hou_module, executor = self.make_executor(failing_value=99.0)
        arguments = self.arguments()
        arguments["candidates"][0]["parameters"] = {
            "/obj/fx/control/density": 99.0
        }
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertFalse(response["ok"])
        self.assertEqual(
            ["baseline", "dense", "disturbed"],
            [value["name"] for value in response["result"]["variants"]],
        )
        self.assertEqual(
            ["completed", "failed", "completed"],
            [value["status"] for value in response["result"]["variants"]],
        )
        self.assertEqual(
            "verified",
            response["result"]["restoration"]["status"],
        )
        self.assertEqual(1.0, hou_module.density.eval())
        self.assertEqual(0.25, hou_module.disturbance.eval())
        self.assertEqual(12.0, hou_module.frame())

    def test_preflight_failure_performs_zero_scene_writes(self) -> None:
        hou_module, executor = self.make_executor()
        arguments = self.arguments()
        arguments["candidates"][0]["parameters"] = {
            "/obj/fx/control/missing": 2.0
        }

        with self.assertRaises(HiaRuntimeError) as captured:
            executor.dispatch("hia_run_effect_experiment", arguments)

        self.assertEqual(
            "INVALID_EXPERIMENT_PARAMETER",
            captured.exception.code,
        )
        self.assertEqual([], hou_module.actions)

        arguments = self.arguments()
        arguments["candidates"][0]["parameters"] = {}
        with self.assertRaises(HiaRuntimeError) as empty:
            executor.dispatch("hia_run_effect_experiment", arguments)
        self.assertEqual("INVALID_ARGUMENTS", empty.exception.code)
        self.assertEqual([], hou_module.actions)

        arguments = self.arguments()
        arguments["timeout_seconds"] = 301
        with self.assertRaises(HiaRuntimeError) as timeout:
            executor.dispatch("hia_run_effect_experiment", arguments)
        self.assertEqual("INVALID_ARGUMENTS", timeout.exception.code)
        self.assertEqual([], hou_module.actions)

    def test_parameter_values_reject_non_round_trip_shapes(self) -> None:
        hou_module, executor = self.make_executor()
        for value in (None, [1.0], {"value": 1.0}, float("nan")):
            arguments = self.arguments()
            arguments["candidates"][0]["parameters"] = {
                "/obj/fx/control/density": value
            }
            with self.subTest(value=value), self.assertRaises(
                HiaRuntimeError
            ) as captured:
                executor.dispatch("hia_run_effect_experiment", arguments)
            self.assertEqual("INVALID_ARGUMENTS", captured.exception.code)
        self.assertEqual([], hou_module.actions)

    def test_force_cook_without_reset_is_reported_not_proven(self) -> None:
        _hou_module, executor = self.make_executor()
        arguments = self.arguments()
        arguments.pop("cache_reset_parms")
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual("not_proven", response["result"]["cache_reset"])
        self.assertTrue(
            all(
                value["freshness"] == "recompute_not_proven"
                for value in response["result"]["variants"]
            )
        )
        self.assertTrue(
            any("force" in value.casefold() for value in response["warnings"])
        )

    def test_static_samples_are_factual_and_do_not_score_the_candidate(self) -> None:
        hou_module, executor = self.make_executor()
        hou_module.scene_viewer.frame_colors = {}
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertTrue(response["ok"], response)
        for variant in response["result"]["variants"]:
            self.assertEqual(
                {
                    "sample_count": 2,
                    "unique_hash_count": 1,
                    "no_change_detected": True,
                    "simulation_advancement": "not_proven",
                },
                variant["temporal_evidence"],
            )

    def test_foreign_undo_label_is_never_undone(self) -> None:
        hou_module, executor = self.make_executor()
        hou_module.undos.foreign_after_group = True
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        undo = response["result"]["restoration"]["undo"]
        self.assertFalse(response["ok"])
        self.assertEqual("not_proven", undo["status"])
        self.assertTrue(undo["user_history_untouched"])
        self.assertIn("not current", undo["error"])
        self.assertEqual(0, hou_module.undos.undo_calls)
        self.assertEqual("User Edit", hou_module.undos.labels[0])
        self.assertEqual(1.0, hou_module.density.eval())
        self.assertEqual(0.25, hou_module.disturbance.eval())
        self.assertEqual(12.0, hou_module.frame())

    def test_locked_asset_lazy_materialization_is_classified_not_aborted(
        self,
    ) -> None:
        hou_module, executor = self.make_executor()
        lazy = FakeNode(
            "/obj/fx/pyro/sopguide1/META/attribvop1",
            hou_module,
            category="Vop",
            inside_locked_hda=True,
        )
        original_cook = hou_module.output.cook
        original_children = hou_module.network._children

        def cook_with_lazy_materialization(*, force: bool) -> None:
            original_cook(force=force)
            if lazy.path() not in hou_module._nodes:
                hou_module._nodes[lazy.path()] = lazy
                hou_module.network._children = (*original_children, lazy)

        hou_module.output.cook = cook_with_lazy_materialization  # type: ignore[method-assign]
        original_undo = hou_module.undos.performUndo

        def undo_materialization() -> None:
            original_undo()
            hou_module._nodes.pop(lazy.path(), None)
            hou_module.network._children = original_children

        hou_module.undos.performUndo = undo_materialization  # type: ignore[method-assign]
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            ["completed", "completed", "completed"],
            [value["status"] for value in response["result"]["variants"]],
        )
        for variant in response["result"]["variants"]:
            structural = variant["structural_diff"]
            self.assertEqual([lazy.path()], structural["created"])
            self.assertEqual(
                [lazy.path()],
                structural["ignored_locked_asset_internal"],
            )
            self.assertEqual([], structural["unexpected_creations"])
        self.assertNotIn(lazy.path(), hou_module._nodes)

    def test_editable_target_creation_still_aborts_experiment(self) -> None:
        hou_module, executor = self.make_executor()
        created = FakeNode(
            "/obj/fx/user_created_node",
            hou_module,
            category="Sop",
        )
        original_cook = hou_module.output.cook
        original_children = hou_module.network._children

        def cook_with_editable_creation(*, force: bool) -> None:
            original_cook(force=force)
            if created.path() not in hou_module._nodes:
                hou_module._nodes[created.path()] = created
                hou_module.network._children = (*original_children, created)

        hou_module.output.cook = cook_with_editable_creation  # type: ignore[method-assign]
        original_undo = hou_module.undos.performUndo

        def undo_creation() -> None:
            original_undo()
            hou_module._nodes.pop(created.path(), None)
            hou_module.network._children = original_children

        hou_module.undos.performUndo = undo_creation  # type: ignore[method-assign]
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertFalse(response["ok"])
        baseline = response["result"]["variants"][0]
        self.assertEqual("failed", baseline["status"])
        self.assertEqual(
            [created.path()],
            baseline["structural_diff"]["unexpected_creations"],
        )
        self.assertEqual(
            "STRUCTURAL_CHANGE_ABORT",
            response["result"]["variants"][1]["errors"][0]["code"],
        )
        self.assertNotIn(created.path(), hou_module._nodes)

    def test_restoration_ignores_locked_asset_internal_materialized_by_undo(
        self,
    ) -> None:
        hou_module, executor = self.make_executor()
        lazy = FakeNode(
            "/obj/fx/SOLVE/pyrobakevolume1/assign_material/attribvop1",
            hou_module,
            category="Vop",
            inside_locked_hda=True,
        )
        original_children = hou_module.network._children
        original_undo = hou_module.undos.performUndo

        def undo_with_lazy_materialization() -> None:
            original_undo()
            hou_module._nodes[lazy.path()] = lazy
            hou_module.network._children = (*original_children, lazy)

        hou_module.undos.performUndo = undo_with_lazy_materialization  # type: ignore[method-assign]
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            ["completed", "completed", "completed"],
            [value["status"] for value in response["result"]["variants"]],
        )
        self.assertTrue(
            all(
                not value["structural_diff"]["created"]
                for value in response["result"]["variants"]
            )
        )
        restoration = response["result"]["restoration"]
        self.assertEqual("verified", restoration["status"])
        self.assertEqual([lazy.path()], restoration["network"]["created"])
        self.assertEqual(
            [lazy.path()],
            restoration["network"]["ignored_locked_asset_internal"],
        )
        self.assertEqual(
            [],
            restoration["network"]["unexpected_creations"],
        )
        self.assertEqual(
            "rolled_back_own_item",
            restoration["undo"]["status"],
        )
        self.assertTrue(restoration["undo"]["user_history_untouched"])

    def test_restoration_still_fails_for_editable_creation_after_undo(
        self,
    ) -> None:
        hou_module, executor = self.make_executor()
        created = FakeNode(
            "/obj/fx/user_editable_creation",
            hou_module,
            category="Sop",
        )
        original_children = hou_module.network._children
        original_undo = hou_module.undos.performUndo

        def undo_with_editable_creation() -> None:
            original_undo()
            hou_module._nodes[created.path()] = created
            hou_module.network._children = (*original_children, created)

        hou_module.undos.performUndo = undo_with_editable_creation  # type: ignore[method-assign]
        arguments = self.arguments()
        arguments["frame_range"] = [1, 2]
        arguments["sample_frames"] = [1, 2]

        response = executor.dispatch(
            "hia_run_effect_experiment",
            arguments,
        )

        self.assertFalse(response["ok"])
        self.assertEqual(
            ["completed", "completed", "completed"],
            [value["status"] for value in response["result"]["variants"]],
        )
        restoration = response["result"]["restoration"]
        self.assertEqual("failed", restoration["status"])
        self.assertEqual([created.path()], restoration["network"]["created"])
        self.assertEqual(
            [created.path()],
            restoration["network"]["unexpected_creations"],
        )
        self.assertEqual(
            [],
            restoration["network"]["ignored_locked_asset_internal"],
        )


if __name__ == "__main__":
    unittest.main()
