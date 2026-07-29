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

from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
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
    ) -> None:
        self._name = name
        self._category = category

    def name(self) -> str:
        return self._name

    def category(self) -> FakeNamed:
        return FakeNamed(self._category)

    def description(self) -> str:
        return "Test SOP"

    def nameComponents(self) -> tuple[str, ...]:
        return ("", self._name, "", "")

    def minNumInputs(self) -> int:  # noqa: N802
        return 0


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
    ) -> None:
        self._path = path
        self._geometry = FakeGeometry(*counts)
        self._errors = errors
        self._warnings = warnings
        self._type = FakeValidationType(
            name=type_name,
            category=category,
        )
        self._display = display
        self._render = render
        self._needs_to_cook = needs_to_cook
        self._children = children
        self._geometry_raises = geometry_raises
        self._on_cook = on_cook
        self.geometry_calls = 0
        self.cook_calls = 0

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    def type(self) -> FakeValidationType:
        return self._type

    def inputs(self) -> tuple[Any, ...]:
        return ()

    def outputs(self) -> tuple[Any, ...]:
        return ()

    def parms(self) -> tuple[Any, ...]:
        return ()

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

    def cook(self, *, force: bool) -> None:
        del force
        self.cook_calls += 1
        if callable(self._on_cook):
            self._on_cook()

    def needsToCook(self) -> bool:  # noqa: N802
        return self._needs_to_cook

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
                "protected_paths": ["/obj/protected"],
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
        self.assertIn("PROTECTED_PATH_CHANGED", codes)
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

    def test_geometry_summary_with_some_valid_statistics_is_partial(
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

        self.assertEqual("partial", check["status"])
        self.assertEqual(1, check["evidence"]["available_paths"])
        self.assertEqual(1, check["evidence"]["unavailable_paths"])
        self.assertEqual(0, obj.geometry_calls)
        self.assertEqual(
            "partial",
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

    def test_execute_envelope_reports_postconditions_and_trace_machine_facts(
        self,
    ) -> None:
        asset = FakeValidationNode("/obj/asset")
        protected = FakeValidationNode("/obj/protected")
        self.install_nodes(asset, protected)
        digests = iter(
            [
                "asset-before",
                "protected-before",
                "asset-after",
                "protected-after",
                "protected-after",
                "asset-after",
            ]
        )
        self.executor._node_digest = mock.Mock(  # type: ignore[method-assign]
            side_effect=lambda _path: next(digests)
        )
        script = "hia_result = 'SECRET_SCRIPT_BODY'"
        task = "SECRET_TASK_BODY"
        original_trace_writer = self.executor._record_execution_trace  # noqa: SLF001
        trace_runner_states: list[bool] = []

        def record_trace(result: dict[str, Any]) -> dict[str, Any]:
            trace_runner_states.append(self.runner_state["active"])
            return original_trace_writer(result)

        with mock.patch.object(
            self.executor,
            "_record_execution_trace",
            side_effect=record_trace,
        ):
            response = self.executor.dispatch(
                "hia_execute_hom",
                {
                    "script": script,
                    "task": task,
                    "mutable_root": "/obj",
                    "protected_paths": ["/obj/protected"],
                    "expected_outputs": ["/obj/asset"],
                    "checks": ["node_errors", "changed_scope"],
                    "diff_paths": ["/obj/asset"],
                },
            )

        self.assertTrue(response["ok"])
        self.assertEqual([False], trace_runner_states)
        evidence = response["execution_evidence"]
        self.assertEqual("changed", response["scene_change_status"])
        self.assertFalse(evidence["validation"]["valid"])
        scope = next(
            item
            for item in evidence["validation"]["check_results"]
            if item["check"] == "changed_scope"
        )
        self.assertEqual("fail", scope["status"])
        self.assertFalse(scope["evidence"]["scope_complete"])
        execution_evidence = self.executor._recent_evidence_snapshot([])[0]  # noqa: SLF001
        self.assertEqual(
            len(response["created_or_changed_paths"]),
            execution_evidence["path_count"],
        )

        trace = response["execution_trace"]
        self.assertTrue(trace["recorded"])
        self.assertEqual(
            hashlib.sha256(script.encode("utf-8")).hexdigest(),
            trace["script_sha256"],
        )
        trace_path = self.project_root / trace["relative_path"]
        record_text = trace_path.read_text(encoding="utf-8")
        record = json.loads(record_text.splitlines()[-1])
        self.assertEqual("hia-execution-trace/1", record["schema"])
        self.assertEqual(trace["trace_id"], record["trace_id"])
        self.assertNotIn(script, record_text)
        self.assertNotIn(task, record_text)
        self.assertNotIn("SECRET_SCRIPT_BODY", record_text)
        self.assertLessEqual(
            len(record_text.splitlines()[-1].encode("utf-8")),
            65_536,
        )
        self.assertEqual(
            {
                "queue_seconds",
                "hom_seconds",
                "validation_seconds",
                "total_seconds",
            },
            set(record["phase_timings"]),
        )

        with mock.patch.object(
            Path,
            "open",
            side_effect=OSError("trace disk unavailable"),
        ):
            degraded = self.executor.dispatch(
                "hia_execute_hom",
                {"script": "hia_result = 'already done'", "capture_diff": False},
            )
        self.assertTrue(degraded["ok"])
        self.assertFalse(degraded["execution_trace"]["recorded"])
        self.assertEqual(
            "EXECUTION_TRACE_WRITE_FAILED",
            degraded["execution_trace"]["error"]["code"],
        )
        self.assertTrue(any("do not retry" in item for item in degraded["warnings"]))

        self.executor._runtime_root = self.outside_root / "escaped-runtime"  # noqa: SLF001
        escaped = self.executor.dispatch(
            "hia_execute_hom",
            {"script": "hia_result = 'already done'", "capture_diff": False},
        )
        self.assertTrue(escaped["ok"])
        self.assertFalse(escaped["execution_trace"]["recorded"])
        self.assertEqual(
            "EXECUTION_TRACE_WRITE_FAILED",
            escaped["execution_trace"]["error"]["code"],
        )
        self.assertFalse(
            (self.outside_root / "escaped-runtime" / "execution-traces").exists()
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
                "protected_paths": ["/obj/a"],
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
                "paths": ["/obj/asset"],
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

        incomplete = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "pass",
                "mutable_root": "/obj",
                "checks": ["changed_scope"],
            },
        )
        incomplete_validation = incomplete["execution_evidence"]["validation"]
        self.assertTrue(incomplete_validation["valid"])
        self.assertFalse(incomplete_validation["complete"])
        self.assertFalse(incomplete["warnings"])
        self.assertEqual(
            "notice",
            incomplete_validation["messages"][0]["level"],
        )

        self.executor._snapshot_map = mock.Mock(  # type: ignore[method-assign]
            side_effect=[({}, False), ({}, False)]
        )
        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "pass",
                "diff_root_path": "/",
                "mutable_root": "/obj",
                "checks": ["changed_scope"],
            },
        )
        validation = response["execution_evidence"]["validation"]
        scope = validation["check_results"][0]
        self.assertEqual("pass", scope["status"])
        self.assertTrue(scope["evidence"]["scope_complete"])
        self.assertEqual(
            "observed_no_out_of_scope_change",
            scope["evidence"]["scope_state"],
        )
        self.assertTrue(validation["complete"])
        self.assertFalse(
            any("incomplete" in warning for warning in response["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
