from __future__ import annotations

import json
import math
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

from hia_mcp_runtime.executor import HiaRuntimeError, HoudiniExecutor  # noqa: E402
from tests.unit.test_hia_mcp_v2_runtime import (  # noqa: E402
    FakeHou,
    FakeNamed,
)


class FakeSemanticType:
    def name(self) -> str:
        return "semantic_sop"

    def category(self) -> FakeNamed:
        return FakeNamed("Sop")

    def description(self) -> str:
        return "Semantic test SOP"

    def nameComponents(self) -> tuple[str, ...]:
        return ("", "semantic_sop", "", "")

    def minNumInputs(self) -> int:  # noqa: N802
        return 0


class FakeAttribute:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeElement:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def attribValue(self, attribute: FakeAttribute) -> Any:  # noqa: N802
        return self.values[attribute.name]


class FakeBoundingBox:
    def minvec(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)

    def maxvec(self) -> tuple[float, float, float]:
        return (1.0, 1.0, 1.0)

    def center(self) -> tuple[float, float, float]:
        return (0.5, 0.5, 0.5)


class FakeVolume:
    def __init__(self, values: list[Any], *, vector: bool = False) -> None:
        self.values = values
        self.vector = vector
        self.calls = 0

    def boundingBox(self) -> FakeBoundingBox:  # noqa: N802
        return FakeBoundingBox()

    def sample(self, _position: Any) -> Any:
        value = self.values[self.calls % len(self.values)]
        self.calls += 1
        return value

    def samplev(self, position: Any) -> Any:
        if not self.vector:
            raise RuntimeError("scalar volume")
        return self.sample(position)


class FakeSemanticGeometry:
    def __init__(
        self,
        *,
        point_attributes: dict[str, list[Any]] | None = None,
        primitive_attributes: dict[str, list[Any]] | None = None,
        detail_attributes: dict[str, Any] | None = None,
        volumes: dict[str, FakeVolume] | None = None,
    ) -> None:
        self.point_attributes = point_attributes or {}
        self.primitive_attributes = primitive_attributes or {}
        self.detail_attributes = detail_attributes or {}
        self.volumes = volumes or {}

    def findPointAttrib(self, name: str) -> FakeAttribute | None:  # noqa: N802
        return FakeAttribute(name) if name in self.point_attributes else None

    def findPrimAttrib(self, name: str) -> FakeAttribute | None:  # noqa: N802
        return FakeAttribute(name) if name in self.primitive_attributes else None

    def findVertexAttrib(self, _name: str) -> None:  # noqa: N802
        return None

    def findGlobalAttrib(self, name: str) -> FakeAttribute | None:  # noqa: N802
        return FakeAttribute(name) if name in self.detail_attributes else None

    def iterPoints(self) -> Any:  # noqa: N802
        count = max((len(values) for values in self.point_attributes.values()), default=0)
        for index in range(count):
            yield FakeElement(
                {
                    name: values[index]
                    for name, values in self.point_attributes.items()
                    if index < len(values)
                }
            )

    def iterPrims(self) -> Any:  # noqa: N802
        count = max(
            (len(values) for values in self.primitive_attributes.values()),
            default=0,
        )
        for index in range(count):
            yield FakeElement(
                {
                    name: values[index]
                    for name, values in self.primitive_attributes.items()
                    if index < len(values)
                }
            )

    def iterVertices(self) -> tuple[Any, ...]:  # noqa: N802
        return ()

    def attribValue(self, attribute: FakeAttribute) -> Any:  # noqa: N802
        return self.detail_attributes[attribute.name]

    def primByName(self, name: str) -> FakeVolume | None:  # noqa: N802
        return self.volumes.get(name)


class FakeSemanticNode:
    def __init__(
        self,
        path: str,
        geometry: FakeSemanticGeometry,
        *,
        needs_to_cook: bool = False,
        time_dependent: bool = False,
    ) -> None:
        self._path = path
        self._geometry = geometry
        self._needs_to_cook = needs_to_cook
        self._time_dependent = time_dependent
        self._cook_count = 3
        self._last_cook_time = 2.5
        self.cook_calls = 0
        self.geometry_calls = 0

    def path(self) -> str:
        return self._path

    def name(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    def type(self) -> FakeSemanticType:
        return FakeSemanticType()

    def inputs(self) -> tuple[Any, ...]:
        return ()

    def errors(self) -> tuple[str, ...]:
        return ()

    def warnings(self) -> tuple[str, ...]:
        return ()

    def needsToCook(self) -> bool:  # noqa: N802
        return self._needs_to_cook

    def isTimeDependent(self, *, for_last_cook: bool = False) -> bool:  # noqa: N802
        self.last_cook_query = for_last_cook
        return self._time_dependent

    def cookCount(self) -> int:  # noqa: N802
        return self._cook_count

    def lastCookTime(self) -> float:  # noqa: N802
        return self._last_cook_time

    def cook(self, *, force: bool) -> None:
        self.cook_calls += 1
        if self._needs_to_cook or force:
            self._cook_count += 1
            self._last_cook_time = 7.25
            self._needs_to_cook = False

    def geometry(self) -> FakeSemanticGeometry:
        self.geometry_calls += 1
        return self._geometry


class FakeUnsupportedSemanticNode(FakeSemanticNode):
    def type(self) -> FakeSemanticType:
        node_type = FakeSemanticType()
        node_type.category = lambda: FakeNamed("Object")  # type: ignore[method-assign]
        return node_type


class FakeMissingNeedsToCookNode(FakeSemanticNode):
    needsToCook = None  # type: ignore[assignment]


class FakeBrokenSamplingGeometry(FakeSemanticGeometry):
    def iterPoints(self) -> Any:  # noqa: N802
        raise RuntimeError("sampling failed")


class FakeSemanticHou(FakeHou):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[str, Any] = {"/": self.root}
        self.selection: tuple[Any, ...] = ()

    def node(self, path: str) -> Any | None:
        return self.nodes.get(path)

    def selectedNodes(self) -> tuple[Any, ...]:
        return self.selection


class HiaMcpV2SemanticEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="hia-semantic-",
            dir=REPOSITORY_ROOT,
        )
        self.addCleanup(self.temp.cleanup)
        self.project_root = Path(self.temp.name)
        self.hou = FakeSemanticHou()
        cache = self.project_root / ".runtime" / "cache"
        with mock.patch.dict(os.environ, {"HIA_CACHE_DIR": str(cache)}):
            self.executor = HoudiniExecutor(
                hou_module=self.hou,
                main_thread_runner=lambda callback: callback(),
                project_root=self.project_root,
            )

    def install(self, *nodes: FakeSemanticNode) -> None:
        for node in nodes:
            self.hou.nodes[node.path()] = node
        self.hou.selection = tuple(nodes)

    def test_presence_and_all_zero_samples_are_distinct(self) -> None:
        node = FakeSemanticNode(
            "/obj/fields",
            FakeSemanticGeometry(
                point_attributes={"density": [0.0, 0.0, 0.0]},
                volumes={"smoke": FakeVolume([0.0])},
            ),
        )
        self.install(node)

        result = self.executor.dispatch(
            "hia_validate",
            {
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "id": "volume-present",
                        "type": "presence",
                        "path": node.path(),
                        "data_kind": "volume",
                        "name": "smoke",
                    },
                    {
                        "id": "density-active",
                        "type": "sample",
                        "path": node.path(),
                        "data_kind": "attribute",
                        "owner": "point",
                        "name": "density",
                        "nonzero": True,
                    },
                ],
                "checks": ["semantic_expectations"],
            },
        )["result"]

        semantic = result["check_results"][0]
        self.assertEqual("fail", semantic["status"])
        by_id = {
            item["id"]: item
            for item in semantic["evidence"]["expectations"]
        }
        self.assertEqual("pass", by_id["volume-present"]["status"])
        self.assertEqual("fail", by_id["density-active"]["status"])
        self.assertIn(
            "ALL_ZERO_SAMPLES",
            {finding["code"] for finding in semantic["findings"]},
        )

    def test_mapping_rejects_missing_target_and_forbidden_field(self) -> None:
        node = FakeSemanticNode(
            "/obj/mapping",
            FakeSemanticGeometry(
                volumes={
                    "source_velocity": FakeVolume(
                        [(1.0, 0.0, 0.0)],
                        vector=True,
                    ),
                    "density": FakeVolume([1.0]),
                }
            ),
        )
        self.install(node)
        ref = {
            "path": node.path(),
            "data_kind": "field",
        }

        result = self.executor.dispatch(
            "hia_validate",
            {
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "type": "mapping",
                        "source": {**ref, "name": "source_velocity"},
                        "target": {**ref, "name": "velocity"},
                        "forbidden_targets": [{**ref, "name": "density"}],
                    }
                ],
            },
        )["result"]
        codes = {
            finding["code"]
            for finding in result["check_results"][0]["findings"]
        }
        self.assertEqual(
            {"SEMANTIC_TARGET_MISSING", "FORBIDDEN_MAPPING_PRESENT"},
            codes,
        )

    def test_nonfinite_and_excessive_vector_samples_fail(self) -> None:
        node = FakeSemanticNode(
            "/obj/velocity",
            FakeSemanticGeometry(
                point_attributes={
                    "v": [(math.nan, 0.0, 0.0), (100.0, 0.0, 0.0)]
                }
            ),
        )
        self.install(node)

        result = self.executor.dispatch(
            "hia_validate",
            {
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "type": "sample",
                        "path": node.path(),
                        "data_kind": "attribute",
                        "owner": "point",
                        "name": "v",
                        "finite": True,
                        "max_magnitude": 10.0,
                    }
                ],
            },
        )["result"]
        semantic = result["check_results"][0]
        codes = {finding["code"] for finding in semantic["findings"]}
        self.assertEqual(
            {"NONFINITE_SAMPLES", "MAGNITUDE_ABOVE_MAXIMUM"},
            codes,
        )
        observation = semantic["evidence"]["expectations"][0]["observation"]
        self.assertNotIn("values", json.dumps(observation))

    def test_semantic_status_distinguishes_unsupported_unknown_and_fail(self) -> None:
        unsupported = FakeUnsupportedSemanticNode(
            "/obj/unsupported",
            FakeSemanticGeometry(),
        )
        unknown = FakeSemanticNode(
            "/obj/unknown",
            FakeSemanticGeometry(point_attributes={"density": [1.0]}),
            needs_to_cook=True,
        )
        method_unsupported = FakeMissingNeedsToCookNode(
            "/obj/method-unsupported",
            FakeSemanticGeometry(point_attributes={"density": [1.0]}),
        )
        sampling_failed = FakeSemanticNode(
            "/obj/sampling-failed",
            FakeBrokenSamplingGeometry(point_attributes={"density": [1.0]}),
        )
        failed = FakeSemanticNode("/obj/failed", FakeSemanticGeometry())
        self.install(
            unsupported,
            unknown,
            method_unsupported,
            sampling_failed,
            failed,
        )

        result = self.executor.dispatch(
            "hia_validate",
            {
                "cook": False,
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "id": "unsupported",
                        "type": "presence",
                        "path": unsupported.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    },
                    {
                        "id": "unknown",
                        "type": "presence",
                        "path": unknown.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    },
                    {
                        "id": "failed",
                        "type": "presence",
                        "path": failed.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    },
                    {
                        "id": "method-unsupported",
                        "type": "presence",
                        "path": method_unsupported.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    },
                    {
                        "id": "sampling-failed",
                        "type": "sample",
                        "path": sampling_failed.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    },
                ],
            },
        )["result"]

        semantic = result["check_results"][0]
        by_id = {
            item["id"]: item["status"]
            for item in semantic["evidence"]["expectations"]
        }
        self.assertEqual(
            {
                "unsupported": "unsupported",
                "unknown": "unknown",
                "failed": "fail",
                "method-unsupported": "unsupported",
                "sampling-failed": "unknown",
            },
            by_id,
        )
        self.assertEqual(
            {"pass": 0, "fail": 1, "unsupported": 2, "unknown": 2},
            semantic["evidence"]["summary"],
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["complete"])

        unsupported_result = self.executor.dispatch(
            "hia_validate",
            {
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "type": "presence",
                        "path": method_unsupported.path(),
                        "data_kind": "attribute",
                        "name": "density",
                    }
                ],
            },
        )["result"]
        self.assertEqual(
            "unsupported",
            unsupported_result["check_results"][0]["status"],
        )
        self.assertEqual(1, unsupported_result["check_summary"]["unsupported"])
        self.assertTrue(unsupported_result["valid"])
        self.assertFalse(unsupported_result["complete"])
        self.assertEqual(
            [("notice", "SEMANTIC_EXPECTATIONS_UNSUPPORTED")],
            [
                (message["level"], message["code"])
                for message in unsupported_result["messages"]
            ],
        )

        context_pack = self.executor.dispatch(
            "hia_context",
            {
                "include_context_pack": True,
                "change_scope": [method_unsupported.path()],
                "context_pack_max_bytes": 4096,
            },
        )["result"]["context_pack"]
        recent = context_pack["recent_evidence"][0]
        self.assertEqual("partial", recent["status"])
        self.assertEqual(
            "unsupported",
            recent["checks"][0]["status"],
        )
        encoded_pack = json.dumps(context_pack, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded_pack), 4096)
        self.assertNotIn('"observation"', encoded_pack.decode("utf-8"))
        self.assertNotIn('"criteria"', encoded_pack.decode("utf-8"))

    def test_cook_false_never_reads_dirty_geometry_and_reports_stale_risk(self) -> None:
        node = FakeSemanticNode(
            "/obj/dirty",
            FakeSemanticGeometry(point_attributes={"density": [1.0]}),
            needs_to_cook=True,
        )
        self.install(node)

        result = self.executor.dispatch(
            "hia_validate",
            {
                "cook": False,
                "checks": ["semantic_expectations"],
                "semantic_checks": [
                    {
                        "type": "presence",
                        "path": node.path(),
                        "data_kind": "attribute",
                        "owner": "point",
                        "name": "density",
                    }
                ],
            },
        )["result"]

        self.assertEqual(0, node.cook_calls)
        self.assertEqual(0, node.geometry_calls)
        self.assertFalse(self.hou.hipFile.dirty)
        self.assertEqual(
            "stale_cache_risk",
            result["cook_cache_evidence"]["assessment"],
        )
        self.assertEqual("warning", result["messages"][0]["level"])
        expectation = result["check_results"][0]["evidence"]["expectations"][0]
        self.assertEqual("unknown", expectation["status"])
        self.assertEqual(
            "cook_not_requested",
            expectation["observation"]["reason"],
        )

    def test_cook_evidence_separates_recompute_from_cache_hit(self) -> None:
        dirty = FakeSemanticNode(
            "/obj/recompute",
            FakeSemanticGeometry(),
            needs_to_cook=True,
        )
        cached = FakeSemanticNode(
            "/obj/cached",
            FakeSemanticGeometry(),
            needs_to_cook=False,
        )
        self.install(dirty, cached)

        recomputed = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [dirty.path()],
                "cook": True,
                "checks": ["node_errors"],
            },
        )["result"]["cook_cache_evidence"]
        self.assertEqual("recompute_verified", recomputed["assessment"])
        recompute_target = recomputed["targets"][0]
        self.assertEqual(dirty.path(), recompute_target["path"])
        self.assertEqual(12.0, recompute_target["frame"])
        self.assertTrue(recompute_target["before"]["needs_to_cook"])
        self.assertFalse(
            recompute_target["before"]["time_dependent_last_cook"]
        )
        self.assertEqual(2.5, recompute_target["before"]["last_cook_time_ms"])
        self.assertEqual("observed", recompute_target["cook_started"])
        self.assertEqual("not_observed", recompute_target["evidence"]["reset"])
        self.assertEqual(1, recompute_target["cook_count_delta"])
        self.assertEqual(
            "observed",
            recompute_target["evidence"]["out_of_date"],
        )
        self.assertEqual(
            "not_proven",
            recompute_target["evidence"]["dependency_invalidation"],
        )

        cache_hit = self.executor.dispatch(
            "hia_validate",
            {
                "paths": [cached.path()],
                "cook": True,
                "checks": ["node_errors"],
            },
        )["result"]["cook_cache_evidence"]
        self.assertEqual("recompute_verified", cache_hit["assessment"])
        self.assertEqual(
            1,
            cache_hit["targets"][0]["cook_count_delta"],
        )
        self.assertEqual(
            "not_proven",
            cache_hit["targets"][0]["evidence"]["cache_hit"],
        )

    def test_context_capability_probe_is_versioned_and_non_mutating(self) -> None:
        node = FakeSemanticNode(
            "/obj/probe",
            FakeSemanticGeometry(),
        )
        self.install(node)

        result = self.executor.dispatch(
            "hia_context",
            {"include_runtime_capabilities": True},
        )["result"]["runtime_capabilities"]
        by_name = {item["name"]: item for item in result["capabilities"]}
        self.assertEqual("hia-runtime-capabilities/1", result["schema"])
        self.assertEqual("21.0.440", result["houdini_build"])
        self.assertEqual(
            "observed",
            by_name["hou.OpNode.needsToCook"]["probe_status"],
        )
        self.assertTrue(by_name["hou.OpNode.needsToCook"]["documented"])
        self.assertEqual(
            "callable_not_invoked",
            by_name["hou.OpNode.cook"]["probe_status"],
        )
        self.assertEqual(
            "unavailable",
            by_name["hou.Geometry.findPointAttrib"]["probe_status"],
        )
        self.assertEqual(0, node.cook_calls)
        self.assertEqual(0, node.geometry_calls)

    def test_execute_envelope_reuses_semantic_contract_and_stays_bounded(self) -> None:
        node = FakeSemanticNode(
            "/obj/output",
            FakeSemanticGeometry(point_attributes={"id": [1.0]}),
        )
        self.install(node)
        checks = [
            {
                "id": f"id-{index}",
                "type": "presence",
                "path": node.path(),
                "data_kind": "attribute",
                "owner": "point",
                "name": "id",
            }
            for index in range(32)
        ]

        response = self.executor.dispatch(
            "hia_execute_hom",
            {
                "script": "hia_result = 'validated'",
                "capture_diff": False,
                "semantic_checks": checks,
            },
        )
        validation = response["execution_evidence"]["validation"]
        self.assertTrue(validation["valid"])
        self.assertEqual(
            32,
            response["execution_evidence"]["envelope"]["semantic_check_count"],
        )
        self.assertLess(
            len(json.dumps(validation, ensure_ascii=False).encode("utf-8")),
            65_536,
        )

    def test_runtime_rejects_ambiguous_semantic_union_fields(self) -> None:
        with self.assertRaises(HiaRuntimeError) as raised:
            self.executor.dispatch(
                "hia_validate",
                {
                    "semantic_checks": [
                        {
                            "type": "mapping",
                            "path": "/obj/output",
                            "source": {
                                "path": "/obj/source",
                                "data_kind": "field",
                                "name": "source",
                            },
                            "target": {
                                "path": "/obj/output",
                                "data_kind": "field",
                                "name": "target",
                            },
                        }
                    ],
                },
            )
        self.assertEqual("INVALID_ARGUMENTS", raised.exception.code)
        self.assertEqual(["path"], raised.exception.details["fields"])


if __name__ == "__main__":
    unittest.main()
