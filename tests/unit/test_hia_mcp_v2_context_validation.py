from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(
    0,
    str(REPOSITORY_ROOT / "houdini_package" / "python_libs"),
)
sys.path.insert(
    0,
    str(REPOSITORY_ROOT / "services" / "hia_mcp_v2"),
)

from hia_mcp_runtime.executor import HiaRuntimeError, HoudiniExecutor  # noqa: E402
from tests.unit.test_hia_mcp_v2_runtime import FakeHou, FakeNamed  # noqa: E402


class FakeGeometry:
    def __init__(self, points: int, vertices: int, primitives: int) -> None:
        self._counts = {
            "pointcount": points,
            "vertexcount": vertices,
            "primitivecount": primitives,
        }

    def intrinsicValue(self, name: str) -> int:  # noqa: N802
        return self._counts[name]

    def boundingBox(self) -> None:  # noqa: N802
        return None

    def prims(self) -> tuple[Any, ...]:
        return ()

    def pointGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()

    def primGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()

    def edgeGroups(self) -> tuple[Any, ...]:  # noqa: N802
        return ()


class FakeValidationType:
    def __init__(
        self,
        *,
        name: str = "test_sop",
        category: str = "Sop",
        min_inputs: int = 0,
    ) -> None:
        self._name = name
        self._category = category
        self._min_inputs = min_inputs

    def name(self) -> str:
        return self._name

    def category(self) -> FakeNamed:
        return FakeNamed(self._category)

    def description(self) -> str:
        return "Test SOP"

    def nameComponents(self) -> tuple[str, ...]:
        return ("", self._name, "", "")

    def minNumInputs(self) -> int:  # noqa: N802
        return self._min_inputs


class FakeControlTemplate:
    def __init__(self, label: str) -> None:
        self._label = label

    def label(self) -> str:
        return self._label

    def isHidden(self) -> bool:  # noqa: N802
        return False


class FakeControlParm:
    def __init__(
        self,
        node_path: str,
        name: str,
        value: Any,
        *,
        label: str = "",
        at_default: bool = False,
    ) -> None:
        self._path = f"{node_path}/{name}"
        self._name = name
        self._value = value
        self._template = FakeControlTemplate(label or name)
        self._at_default = at_default

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._name

    def parmTemplate(self) -> FakeControlTemplate:  # noqa: N802
        return self._template

    def eval(self) -> Any:
        return self._value

    def unexpandedString(self) -> str:  # noqa: N802
        return str(self._value)

    def isAtDefault(self) -> bool:  # noqa: N802
        return self._at_default

    def isTimeDependent(self) -> bool:  # noqa: N802
        return False

    def getReferencedParm(self) -> "FakeControlParm":  # noqa: N802
        return self


class FakeDigestKeyframe:
    def __init__(self, expression: str) -> None:
        self._expression = expression

    def frame(self) -> float:
        return 1.0

    def time(self) -> float:
        return 0.0

    def value(self) -> float:
        return 1.0

    def expression(self) -> str:
        return self._expression


class FakeDigestParm:
    def __init__(
        self,
        node_path: str,
        name: str,
        value: Any,
        *,
        hou_module: Any = None,
        expression: str = "",
    ) -> None:
        self._path = f"{node_path}/{name}"
        self._name = name
        self._value = value
        self._hou = hou_module
        self._expression = expression

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._name

    def eval(self) -> Any:
        return self._hou.frame() if self._expression else self._value

    def set(self, value: Any) -> None:
        self._value = value

    def unexpandedString(self) -> str:  # noqa: N802
        raise RuntimeError("numeric parameter has no unexpanded string")

    def keyframes(self) -> tuple[FakeDigestKeyframe, ...]:
        return (
            (FakeDigestKeyframe(self._expression),)
            if self._expression
            else ()
        )

    def expression(self) -> str:
        if not self._expression:
            raise RuntimeError("parameter has no expression")
        return self._expression

    def isTimeDependent(self) -> bool:  # noqa: N802
        return bool(self._expression)

    def isAtDefault(self) -> bool:  # noqa: N802
        return False

    def getReferencedParm(self) -> "FakeDigestParm":  # noqa: N802
        return self


class FakeValidationNode:
    def __init__(
        self,
        path: str,
        *,
        counts: tuple[int, int, int] = (1, 1, 1),
        errors: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        category: str = "Sop",
        type_name: str = "test_sop",
        display: bool = True,
        render: bool = True,
        needs_to_cook: bool = False,
        children: tuple["FakeValidationNode", ...] = (),
        geometry_raises: bool = False,
        on_cook: Any = None,
        min_inputs: int = 0,
        inputs: tuple["FakeValidationNode | None", ...] = (),
        outputs: tuple["FakeValidationNode", ...] = (),
        parms: tuple[Any, ...] = (),
        inside_locked_hda: bool = False,
    ) -> None:
        self._path = path
        self._geometry = FakeGeometry(*counts)
        self._errors = errors
        self._warnings = warnings
        self._type = FakeValidationType(
            name=type_name,
            category=category,
            min_inputs=min_inputs,
        )
        self._display = display
        self._render = render
        self._needs_to_cook = needs_to_cook
        self._children = children
        self._geometry_raises = geometry_raises
        self._on_cook = on_cook
        self._inputs = inputs
        self._outputs = outputs
        self._parms = parms
        self._inside_locked_hda = inside_locked_hda
        self.geometry_calls = 0
        self.cook_calls = 0
        self.cook_forces: list[bool] = []
        self._cook_count = 0
        self._last_cook_time = 0.0

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    def type(self) -> FakeValidationType:
        return self._type

    def inputs(self) -> tuple[Any, ...]:
        return self._inputs

    def outputs(self) -> tuple[Any, ...]:
        return self._outputs

    def parms(self) -> tuple[Any, ...]:
        return self._parms

    def errors(self) -> tuple[str, ...]:
        return self._errors

    def warnings(self) -> tuple[str, ...]:
        return self._warnings

    def geometry(self) -> FakeGeometry:
        self.geometry_calls += 1
        if self._geometry_raises:
            raise AssertionError("geometry() must not be called")
        return self._geometry

    def isDisplayFlagSet(self) -> bool:  # noqa: N802
        return self._display

    def isRenderFlagSet(self) -> bool:  # noqa: N802
        return self._render

    def isBypassed(self) -> bool:  # noqa: N802
        return False

    def isInsideLockedHDA(self) -> bool:  # noqa: N802
        return self._inside_locked_hda

    def cook(self, *, force: bool) -> None:
        self.cook_calls += 1
        self.cook_forces.append(force)
        if force or self._needs_to_cook:
            self._cook_count += 1
            self._last_cook_time += 1.0
            self._needs_to_cook = False
        if callable(self._on_cook):
            self._on_cook()

    def needsToCook(self) -> bool:  # noqa: N802
        return self._needs_to_cook

    def cookCount(self) -> int:  # noqa: N802
        return self._cook_count

    def lastCookTime(self) -> float:  # noqa: N802
        return self._last_cook_time

    def children(self) -> tuple["FakeValidationNode", ...]:
        return self._children

    def allSubChildren(self) -> tuple["FakeValidationNode", ...]:  # noqa: N802
        raise AssertionError("bounded validation must not expand all descendants")


class FakeKnowledgeIndex:
    relative_database_path = ".runtime/knowledge/knowledge.sqlite3"


class FakeKnowledgeStore:
    def __init__(self, runner_state: dict[str, bool]) -> None:
        self.index = FakeKnowledgeIndex()
        self.runner_state = runner_state
        self.calls: list[dict[str, Any]] = []

    def search_many(
        self,
        queries: list[str],
        source_groups: set[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "queries": list(queries),
                "source_groups": set(source_groups),
                "in_ui_runner": self.runner_state["active"],
                **kwargs,
            }
        )
        results = []
        for query in queries:
            results.append(
                {
                    "matches": [
                        {
                            "source": "houdini_help",
                            "title": f"{query}-{_index}.txt",
                            "snippet": ("high signal help " + query + " ") * 80,
                            "metadata": {
                                "source_key": f"help:{query}:{_index}",
                                "url": f"houdini://help/{query}",
                                "houdini_version": "21.0.440",
                                "verification": "verified",
                                "evidence": "installed help",
                            },
                        }
                        for _index in range(8)
                    ],
                    "total": 8,
                    "retrieval": {"mode_used": "lexical"},
                }
            )
        return results


class HiaMcpV2ContextValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = REPOSITORY_ROOT / ".runtime" / "test-runs"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="hia-mcp-v2-",
            dir=temporary_root,
        )
        temporary_directory = Path(self._temporary.name)
        self.project_root = temporary_directory / "project"
        self.project_root.mkdir()
        self.outside_root = temporary_directory / "outside"
        self.outside_root.mkdir()
        self.hou = FakeHou()
        self.runner_state = {"active": False}

        def runner(callback: Any) -> Any:
            self.runner_state["active"] = True
            try:
                return callback()
            finally:
                self.runner_state["active"] = False

        with mock.patch.dict(
            os.environ,
            {
                "HIA_CACHE_DIR": str(
                    self.project_root / ".runtime" / "cache"
                )
            },
            clear=False,
        ):
            self.executor = HoudiniExecutor(
                hou_module=self.hou,
                main_thread_runner=runner,
                project_root=self.project_root,
            )

    def tearDown(self) -> None:
        self.executor.close()
        self._temporary.cleanup()

    def install_nodes(self, *nodes: FakeValidationNode) -> dict[str, Any]:
        mapping: dict[str, Any] = {"/": self.hou.root}
        mapping.update({node.path(): node for node in nodes})
        self.hou.node = lambda path: mapping.get(path)  # type: ignore[method-assign]
        return mapping

    def test_context_pack_is_bounded_provenanced_and_searches_once_off_ui(
        self,
    ) -> None:
        selected = FakeValidationNode("/obj/selected")
        self.install_nodes(selected)
        self.hou.selectedNodes = lambda: (selected,)  # type: ignore[method-assign]
        store = FakeKnowledgeStore(self.runner_state)
        self.executor._hybrid_knowledge_store = mock.Mock(  # type: ignore[method-assign]
            return_value=store
        )

        response = self.executor.dispatch(
            "hia_context",
            {
                "include_context_pack": True,
                "task": "build a stable vellum cloth setup",
                "change_scope": ["/obj/selected", "/obj/not-created-yet"],
                "knowledge_queries": ["vellum constraints", "cloth collision"],
                "context_pack_max_bytes": 4096,
            },
        )

        pack = response["result"]["context_pack"]
        encoded = json.dumps(
            pack,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        self.assertTrue(pack["limits"]["truncated"])
        self.assertEqual(1, len(store.calls))
        self.assertEqual(
            ["vellum constraints", "cloth collision"],
            store.calls[0]["queries"],
        )
        self.assertFalse(store.calls[0]["in_ui_runner"])
        by_path = {item["path"]: item for item in pack["entities"]}
        self.assertTrue(by_path["/obj/selected"]["exists"])
        self.assertFalse(by_path["/obj/not-created-yet"]["exists"])
        self.assertEqual("cached_sqlite_fts5", pack["sources"][-1]["kind"])
        self.assertTrue(pack["knowledge"]["hits"])
        for hit in pack["knowledge"]["hits"]:
            self.assertIn("verification", hit["provenance"])

        long_paths = [
            f"/obj/{'x' * 3000}{index}"
            for index in range(32)
        ]
        response = self.executor.dispatch(
            "hia_context",
            {
                "include_context_pack": True,
                "change_scope": long_paths,
                "context_pack_max_bytes": 4096,
            },
        )
        pack = response["result"]["context_pack"]
        encoded = json.dumps(
            pack,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        self.assertTrue(pack["limits"]["truncated"])

    def test_context_pack_explicit_false_suppresses_task_enrichment(
        self,
    ) -> None:
        self.executor._context_pack_live_snapshot = mock.Mock(  # type: ignore[method-assign]
            side_effect=AssertionError("context pack must remain disabled")
        )
        self.executor._hybrid_knowledge_store = mock.Mock(  # type: ignore[method-assign]
            side_effect=AssertionError("knowledge search must remain disabled")
        )

        response = self.executor.dispatch(
            "hia_context",
            {
                "include_context_pack": False,
                "task": "inspect the selected pyro network",
                "knowledge_queries": ["pyro turbulence workflow"],
            },
        )

        self.assertNotIn("context_pack", response["result"])
        self.executor._context_pack_live_snapshot.assert_not_called()
        self.executor._hybrid_knowledge_store.assert_not_called()

    def test_context_pack_omitted_flag_keeps_task_compatibility(
        self,
    ) -> None:
        store = FakeKnowledgeStore(self.runner_state)
        self.executor._hybrid_knowledge_store = mock.Mock(  # type: ignore[method-assign]
            return_value=store
        )

        response = self.executor.dispatch(
            "hia_context",
            {"task": "inspect the selected pyro network"},
        )

        self.assertIn("context_pack", response["result"])
        self.assertEqual(1, len(store.calls))
        self.assertEqual(
            ["inspect the selected pyro network"],
            store.calls[0]["queries"],
        )

    def test_domain_checks_have_one_unified_bounded_shape(self) -> None:
        good = FakeValidationNode("/obj/good", counts=(3, 3, 1))
        empty = FakeValidationNode(
            "/obj/empty",
            counts=(0, 0, 0),
            errors=("broken output",),
        )
        self.install_nodes(good, empty)

        response = self.executor.dispatch(
            "hia_validate",
            {
                "paths": ["/obj/good", "/obj/empty"],
                "expected_paths": ["/obj/good", "/obj/missing"],
                "changed_paths": [
                    "/obj/good",
                    "/obj/protected/child",
                    "/stage/outside",
                ],
                "mutable_root": "/obj",
                "checks": [
                    "node_errors",
                    "empty_output",
                    "critical_paths",
                    "geometry_summary",
                    "changed_scope",
                ],
                "limit": 50,
            },
        )

        result = response["result"]
        by_check = {
            item["check"]: item for item in result["check_results"]
        }
        self.assertEqual(
            {
                "node_errors",
                "empty_output",
                "critical_paths",
                "geometry_summary",
                "changed_scope",
            },
            set(by_check),
        )
        self.assertEqual("fail", by_check["node_errors"]["status"])
        self.assertEqual("fail", by_check["empty_output"]["status"])
        self.assertEqual("fail", by_check["critical_paths"]["status"])
        self.assertEqual("pass", by_check["geometry_summary"]["status"])
        self.assertEqual("fail", by_check["changed_scope"]["status"])
        self.assertFalse(result["valid"])
        self.assertNotIn("domain_valid", result)
        self.assertIn("/obj/missing", result["missing_expected_paths"])
        self.assertEqual(1, good.geometry_calls)
        self.assertEqual(1, empty.geometry_calls)
        for check in by_check.values():
            self.assertEqual(
                {
                    "check",
                    "status",
                    "finding_count",
                    "findings",
                    "evidence",
                    "truncated",
                },
                set(check),
            )
        codes = {
            finding["code"]
            for check in result["check_results"]
            for finding in check["findings"]
        }
        self.assertIn("EMPTY_OUTPUT", codes)
        self.assertIn("OUTSIDE_MUTABLE_ROOT", codes)
        self.assertEqual(
            {
                "kind",
                "timestamp",
                "paths",
                "path_count",
                "status",
                "complete",
                "checks",
                "error_codes",
            },
            set(self.executor._recent_evidence_snapshot([])[0]),  # noqa: SLF001
        )

        empty_only = FakeValidationNode("/obj/empty-only", counts=(0, 0, 0))
        self.install_nodes(empty_only)
        empty_result = self.executor.dispatch(
            "hia_validate",
            {
                "paths": ["/obj/empty-only"],
                "checks": ["empty_output"],
            },
        )["result"]
        self.assertFalse(empty_result["valid"])
        self.assertEqual({"errors": 0, "warnings": 0}, empty_result["counts"])

        noisy = FakeValidationNode(
            "/obj/noisy",
            warnings=tuple(f"warning-{index}" for index in range(32)),
        )
        broken = FakeValidationNode(
            "/obj/broken",
            errors=("late error",),
        )
        self.install_nodes(noisy, broken)
        response = self.executor.dispatch(
            "hia_validate",
            {
                "paths": ["/obj/noisy", "/obj/broken"],
                "checks": ["node_errors"],
                "limit": 2,
            },
        )
        result = response["result"]
        check = result["check_results"][0]
        self.assertEqual("fail", check["status"])
        self.assertEqual(33, check["finding_count"])
        self.assertTrue(check["truncated"])
        self.assertFalse(result["valid"])
        self.assertEqual({"errors": 1, "warnings": 32}, result["counts"])

    def test_validate_without_cook_does_not_cook_geometry_or_dirty_scene(
        self,
    ) -> None:
        node = FakeValidationNode(
            "/obj/dirty_sop",
            needs_to_cook=True,
            on_cook=lambda: setattr(self.hou.hipFile, "dirty", True),
        )
        self.install_nodes(node)
        self.hou.hipFile.dirty = False

        result = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [node.path()],
                "checks": ["node_errors", "geometry_summary"],
                "cook": False,
            },
        )["result"]

        self.assertFalse(result["cooked"])
        self.assertEqual(0, node.cook_calls)
        self.assertEqual(0, node.geometry_calls)
        self.assertFalse(self.hou.hipFile.dirty)
        by_check = {
            item["check"]: item for item in result["check_results"]
        }
        self.assertFalse(
            by_check["node_errors"]["evidence"]["cook_requested"]
        )
        self.assertEqual(
            "cook_not_requested",
            by_check["geometry_summary"]["evidence"]["geometry"][0]["reason"],
        )

    def test_inspect_obj_does_not_call_geometry(self) -> None:
        obj = FakeValidationNode(
            "/obj/container",
            category="Object",
            type_name="geo",
            geometry_raises=True,
        )
        self.install_nodes(obj)

        result = self.executor.dispatch(
            "hia_inspect",
            {"paths": [obj.path()], "views": ["geometry"]},
        )["result"]

        geometry = result["nodes"][0]["geometry"]
        self.assertFalse(geometry["available"])
        self.assertEqual("Object", geometry["category"])
        self.assertEqual("unsupported_node_category", geometry["reason"])
        self.assertEqual(0, obj.geometry_calls)

    def test_inspect_evidence_is_bounded_and_reports_local_network_facts(
        self,
    ) -> None:
        source = FakeValidationNode("/obj/asset/source", type_name="sphere")
        target_path = "/obj/asset/OUT_MODEL"
        target = FakeValidationNode(
            target_path,
            type_name="null",
            min_inputs=1,
            inputs=(source,),
            parms=(
                FakeControlParm(
                    target_path,
                    "shop_materialpath",
                    "/mat/lookdev",
                    label="Material",
                ),
            ),
        )
        boxes = tuple(
            FakeValidationNode(
                f"/obj/asset/box_{index}",
                type_name="box",
            )
            for index in range(8)
        )
        python_sop = FakeValidationNode(
            "/obj/asset/python_geometry",
            type_name="python",
        )
        broken = FakeValidationNode(
            "/obj/asset/broken_merge",
            type_name="merge",
            min_inputs=1,
        )
        root = FakeValidationNode(
            "/obj/asset",
            category="Object",
            type_name="geo",
            children=(*boxes, python_sop, broken, target),
        )
        self.install_nodes(root, source, target, *boxes, python_sop, broken)

        result = self.executor.dispatch(
            "hia_inspect",
            {
                "paths": [target.path(), root.path()],
                "use_selection": False,
                "views": ["evidence"],
            },
        )["result"]
        by_path = {
            item["path"]: item["network_evidence"]
            for item in result["nodes"]
        }
        target_evidence = by_path[target.path()]
        self.assertEqual(
            [{"index": 0, "path": source.path()}],
            target_evidence["inputs"],
        )
        self.assertEqual(
            source.path(),
            target_evidence["upstream_chain"][0]["path"],
        )
        self.assertEqual(
            ["shop_materialpath"],
            [item["name"] for item in target_evidence["controls"]],
        )
        self.assertEqual(1, len(target_evidence["material_entries"]))
        self.assertEqual("binding_consumer", target_evidence["material_role"])
        self.assertIn("needs_to_cook", target_evidence["cook_state"])

        self.assertNotIn("quality_evidence", by_path[root.path()])
        self.assertEqual(0, target.geometry_calls)

    def test_empty_output_only_checks_explicit_or_final_output_roles(
        self,
    ) -> None:
        repeat_metadata = FakeValidationNode(
            "/obj/asset/repeat_metadata",
            counts=(0, 0, 0),
            type_name="repeat_metadata",
            display=False,
            render=False,
        )
        blast = FakeValidationNode(
            "/obj/asset/blast_intermediate",
            counts=(0, 0, 0),
            type_name="blast",
            display=False,
            render=False,
        )
        final = FakeValidationNode(
            "/obj/asset/OUT_FINAL",
            counts=(0, 0, 0),
            display=True,
            render=True,
        )
        root = FakeValidationNode(
            "/obj/asset",
            category="Object",
            type_name="geo",
            display=False,
            render=False,
            children=(repeat_metadata, blast, final),
        )
        self.install_nodes(root, repeat_metadata, blast, final)

        check = self.executor.dispatch(
            "hia_validate",
            {
                "root_path": root.path(),
                "checks": ["empty_output"],
                "limit": 10,
            },
        )["result"]["check_results"][0]

        self.assertEqual("fail", check["status"])
        self.assertEqual([final.path()], check["evidence"]["output_paths"])
        self.assertEqual(3, check["evidence"]["ignored_non_output_paths"])
        self.assertEqual(
            [final.path()],
            [finding["path"] for finding in check["findings"]],
        )
        self.assertEqual(0, repeat_metadata.geometry_calls)
        self.assertEqual(0, blast.geometry_calls)
        self.assertEqual(1, final.geometry_calls)

    def test_geometry_summary_ignores_known_non_geometry_category(
        self,
    ) -> None:
        sop = FakeValidationNode("/obj/good", counts=(4, 4, 1))
        obj = FakeValidationNode(
            "/obj/container",
            category="Object",
            type_name="geo",
            geometry_raises=True,
        )
        self.install_nodes(sop, obj)

        check = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [sop.path(), obj.path()],
                "checks": ["geometry_summary"],
            },
        )["result"]["check_results"][0]

        self.assertEqual("pass", check["status"])
        self.assertEqual(1, check["evidence"]["available_paths"])
        self.assertEqual(0, check["evidence"]["unavailable_paths"])
        self.assertEqual(1, check["evidence"]["ignored_unsupported_paths"])
        self.assertEqual(0, obj.geometry_calls)
        self.assertEqual(
            "passed",
            self.executor._recent_evidence_snapshot([])[0]["status"],  # noqa: SLF001
        )

    def test_validation_scope_is_bounded_for_paths_and_root(self) -> None:
        target = FakeValidationNode("/obj/target")
        children = tuple(
            FakeValidationNode(f"/obj/root/child_{index}")
            for index in range(100)
        )
        root = FakeValidationNode(
            "/obj/root",
            category="Object",
            type_name="geo",
            children=children,
        )
        self.install_nodes(root, target, *children)

        explicit = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [target.path()],
                "root_path": root.path(),
                "checks": ["node_errors"],
                "limit": 3,
            },
        )["result"]["check_results"][0]
        self.assertEqual(1, explicit["evidence"]["paths_checked"])

        bounded = self.executor.dispatch(
            "hia_validate",
            {
                "root_path": root.path(),
                "checks": ["node_errors"],
                "limit": 3,
            },
        )["result"]["check_results"][0]
        self.assertEqual(3, bounded["evidence"]["paths_checked"])

    def test_interrupted_cook_errors_are_folded_once(self) -> None:
        root_error = FakeValidationNode(
            "/obj/source",
            errors=("Upstream source failed",),
        )
        interrupted = (
            FakeValidationNode(
                "/obj/downstream_a",
                errors=("Cooking was interrupted",),
            ),
            FakeValidationNode(
                "/obj/downstream_b",
                errors=("  COOKING   WAS INTERRUPTED.  ",),
            ),
            FakeValidationNode(
                "/obj/downstream_c",
                errors=("cooking was interrupted.",),
            ),
        )
        self.install_nodes(root_error, *interrupted)

        check = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [
                    root_error.path(),
                    *(node.path() for node in interrupted),
                ],
                "checks": ["node_errors"],
            },
        )["result"]["check_results"][0]

        folded = [
            finding
            for finding in check["findings"]
            if finding["code"] == "COOK_INTERRUPTED"
        ]
        self.assertEqual(1, len(folded))
        self.assertIn("Upstream source failed", folded[0]["message"])
        self.assertEqual(
            {
                "root_cause": "Upstream source failed",
                "representative_path": "/obj/downstream_a",
                "affected_count": 3,
            },
            check["evidence"]["interrupted"],
        )
        self.assertEqual(2, check["finding_count"])

    def test_recent_evidence_uses_representative_paths_and_exact_count(
        self,
    ) -> None:
        paths = [
            f"/obj/evidence_{index}_{'x' * 120}"
            for index in range(64)
        ]
        self.executor._remember_evidence(  # noqa: SLF001
            {
                "kind": "validation",
                "timestamp": "2026-07-27T00:00:00Z",
                "paths": paths,
                "path_count": len(paths),
                "status": "failed",
                "complete": False,
                "checks": [
                    {
                        "check": "node_errors",
                        "status": "fail",
                        "finding_count": 64,
                    }
                ],
                "error_codes": ["COOK_INTERRUPTED"],
            }
        )

        pack = self.executor.dispatch(
            "hia_context",
            {
                "include_context_pack": True,
                "change_scope": [paths[-1]],
                "context_pack_max_bytes": 4096,
            },
        )["result"]["context_pack"]

        encoded = json.dumps(
            pack,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        self.assertFalse(pack["limits"]["truncated"])
        evidence = pack["recent_evidence"][0]
        self.assertEqual(64, evidence["path_count"])
        self.assertLessEqual(len(evidence["paths"]), 4)
        self.assertIn(paths[-1], evidence["paths"])
        self.assertEqual(
            "node_errors",
            evidence["checks"][0]["check"],
        )

    def test_changed_scope_uses_path_segments_not_similar_prefixes(self) -> None:
        asset = FakeValidationNode("/obj/asset")
        self.install_nodes(asset)
        response = self.executor.dispatch(
            "hia_validate",
            {
                "paths": ["/obj/asset"],
                "changed_paths": ["/obj/asset"],
                "mutable_root": "/obj",
                "checks": ["changed_scope"],
            },
        )
        check = response["result"]["check_results"][0]
        self.assertEqual("unknown", check["status"])
        self.assertFalse(check["evidence"]["scope_complete"])
        self.assertEqual(
            "scope_not_observable",
            check["evidence"]["scope_state"],
        )
        self.assertEqual(
            "caller_declared",
            check["evidence"]["change_provenance"],
        )
        self.assertEqual(
            "notice",
            response["result"]["messages"][0]["level"],
        )
        self.assertTrue(response["result"]["valid"])
        self.assertFalse(response["result"]["complete"])

        failed = self.executor.dispatch(
            "hia_validate",
            {
                "paths": ["/stage/outside"],
                "changed_paths": ["/stage/outside"],
                "mutable_root": "/obj",
                "checks": ["changed_scope"],
            },
        )
        self.assertFalse(failed["result"]["valid"])
        self.assertEqual(
            "scope_violation",
            failed["result"]["check_results"][0]["evidence"]["scope_state"],
        )
    def test_validation_requires_a_real_target_and_output_checks_cook_fresh(
        self,
    ) -> None:
        with self.assertRaises(HiaRuntimeError) as raised:
            self.executor.dispatch("hia_validate", {"checks": ["node_errors"]})
        self.assertEqual("VALIDATION_TARGET_REQUIRED", raised.exception.code)

        output = FakeValidationNode("/obj/OUT_RESULT", counts=(3, 3, 1))
        self.install_nodes(output)
        result = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [output.path()],
                "checks": ["empty_output", "geometry_summary"],
            },
        )["result"]

        self.assertTrue(result["valid"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["cooked"])
        self.assertEqual("recompute_verified", result["freshness"])
        self.assertEqual([True], output.cook_forces)


if __name__ == "__main__":
    unittest.main()
