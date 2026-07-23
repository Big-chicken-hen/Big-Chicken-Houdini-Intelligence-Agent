from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hia_core.houdini_contract import (  # noqa: E402
    B2_READ_ONLY_TOOLS,
    B2_SCHEMA_VERSION,
    ContractError,
    EXPECTED_TOOLS,
    MAX_JSON_BYTES,
    SchemaRegistry,
    approval_binding_digest,
    approval_binding_payload,
    canonical_json_bytes,
    canonical_json_sha256,
    graph_digest,
    graph_side_effect_summary,
    normalize_graph,
    strict_json_loads,
    validate_graph_relations,
)


REQUEST_ID = "request-1"
THREAD_ID = "thread-1"
TURN_ID = "turn-1"
HIP_SESSION_ID = "hip-session-1"
IDEMPOTENCY_KEY = "idempotency-key-0001"
BASE_REVISION = 7
FINGERPRINT = "b" * 64
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v"


def fixture_graph(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def common_input(permission: str, *, suffix: str = "1") -> dict:
    return {
        "request_id": f"request-{suffix}",
        "thread_id": THREAD_ID,
        "turn_id": f"turn-{suffix}",
        "hip_session_id": HIP_SESSION_ID,
        "base_scene_revision": BASE_REVISION,
        "idempotency_key": f"idempotency-key-{suffix.zfill(4)}",
        "deadline_ms": 1000,
        "permission_level": permission,
    }


def common_output(request: dict, *, ok: bool = True, revision: int | None = None) -> dict:
    return {
        "ok": ok,
        "request_id": request["request_id"],
        "thread_id": request["thread_id"],
        "turn_id": request["turn_id"],
        "hip_session_id": request["hip_session_id"],
        "base_scene_revision": request["base_scene_revision"],
        "idempotency_key": request["idempotency_key"],
        "scene_revision": request["base_scene_revision"] if revision is None else revision,
        "result": None,
        "warnings": [],
        "structured_error": None if ok else {
            "code": "INTERNAL_ERROR",
            "message": "Bounded offline failure.",
            "details": [],
        },
    }


def scene_info_pair() -> tuple[dict, dict]:
    request = common_input("scene_read")
    request["include_graph_summaries"] = True
    result = common_output(request)
    result["result"] = {
        "hip_fingerprint": FINGERPRINT,
        "current_frame": 1.0,
        "fps": 24.0,
        "dirty": False,
        "enabled_contexts": ["Object", "Sop"],
        "hia_graphs": [],
        "graph_summaries_truncated": False,
    }
    return request, result


def node_type_pair() -> tuple[dict, dict]:
    request = common_input("scene_read")
    request["node_types"] = [{"context": "Object", "name": "geo"}]
    result = common_output(request)
    result["result"] = {
        "node_types": [
            {
                "context": "Object",
                "requested_name": "geo",
                "resolved_name": "geo",
                "available": True,
                "creatable": True,
                "schema_source": "live_houdini_instance",
                "parameters": [],
                "input_count": 0,
                "output_count": 1,
            }
        ]
    }
    return request, result


def b2_scene_info_pair() -> tuple[dict, dict]:
    request = common_input("scene_read", suffix="b2scene")
    request["include_graph_summaries"] = True
    result = common_output(request)
    result["result"] = {
        "houdini_build": "21.0.440",
        "hip_fingerprint": FINGERPRINT,
        "current_frame": 1.0,
        "fps": 24.0,
        "dirty": False,
        "enabled_contexts": ["Object", "Sop"],
        "hia_graphs": [],
        "graph_summaries_truncated": False,
    }
    return request, result


def b2_node_type_pair() -> tuple[dict, dict]:
    request = common_input("scene_read", suffix="b2types")
    request["node_types"] = [
        {"context": "Sop", "name": "box"},
        {"context": "Sop", "name": "merge"},
    ]
    result = common_output(request)
    result["result"] = {
        "node_types": [
            {
                "context": "Sop",
                "requested_name": "box",
                "resolved_name": "box",
                "available": True,
                "creatable": False,
                "schema_source": "live_houdini_instance",
                "parameters": [
                    {
                        "name": "size",
                        "label": "Size",
                        "value_type": "tuple",
                        "tuple_size": 3,
                        "writable": False,
                        "allows_expression": False,
                        "default_value": {
                            "type": "tuple",
                            "items_type": "float",
                            "value": [1.0, 1.0, 1.0],
                        },
                        "numeric_range": {
                            "min_value": 0.0,
                            "max_value": 1000000.0,
                            "min_is_strict": False,
                            "max_is_strict": False,
                        },
                    }
                ],
                "input_count": 0,
                "output_count": 1,
            },
            {
                "context": "Sop",
                "requested_name": "merge",
                "resolved_name": "merge",
                "available": True,
                "creatable": False,
                "schema_source": "live_houdini_instance",
                "parameters": [],
                "input_count": 9999,
                "output_count": 1,
            },
        ]
    }
    return request, result


def graph_summary(graph: dict) -> dict:
    counts: dict[tuple[str, str], int] = {}
    for node in graph["nodes"]:
        key = (node["type"]["context"], node["type"]["name"])
        counts[key] = counts.get(key, 0) + 1
    display = [node["id"] for node in graph["nodes"] if node["flags"]["display"]]
    render = [node["id"] for node in graph["nodes"] if node["flags"]["render"]]
    return {
        "node_count": len(graph["nodes"]),
        "connection_count": len(graph["connections"]),
        "type_counts": [
            {"context": context, "name": name, "count": count}
            for (context, name), count in sorted(counts.items())
        ],
        "display_node_id": display[0],
        "render_node_id": render[0],
    }


def validate_pair(graph: dict | None = None) -> tuple[dict, dict]:
    graph = fixture_graph("stairs_graph.json") if graph is None else copy.deepcopy(graph)
    request = common_input("scene_read", suffix="validate")
    request.update({"expected_hip_fingerprint": FINGERPRINT, "graph": graph})
    normalized = normalize_graph(graph)
    digest = graph_digest(normalized)
    result = common_output(request)
    result["result"] = {
        "valid": True,
        "scene_mutated": False,
        "normalized_graph": normalized,
        "canonical_graph_digest": digest,
        "approval_binding_digest": approval_binding_digest(
            request, normalized, digest, graph_side_effect_summary(normalized)
        ),
        "summary": graph_summary(normalized),
        "issues": [],
    }
    return request, result


def apply_pair(graph: dict | None = None) -> tuple[dict, dict]:
    graph = fixture_graph("stairs_graph.json") if graph is None else copy.deepcopy(graph)
    normalized = normalize_graph(graph)
    digest = graph_digest(normalized)
    request = common_input("scene_write", suffix="apply")
    request.update(
        {
            "expected_hip_fingerprint": FINGERPRINT,
            "graph": normalized,
            "canonical_graph_digest": digest,
        }
    )
    root_path = f"/obj/{normalized['target']['name_hint']}"
    created = [
        {
            "request_local_id": "root",
            "path": root_path,
            "context": "Object",
            "resolved_type": "geo",
        }
    ] + [
        {
            "request_local_id": node["id"],
            "path": f"{root_path}/{node['name_hint']}",
            "context": node["type"]["context"],
            "resolved_type": node["type"]["name"],
        }
        for node in normalized["nodes"]
    ]
    result = common_output(request, revision=BASE_REVISION + 1)
    result["result"] = {
        "root_path": root_path,
        "canonical_graph_digest": digest,
        "approval_binding_digest": approval_binding_digest(
            request, normalized, digest, graph_side_effect_summary(normalized)
        ),
        "replay": False,
        "revision_before": BASE_REVISION,
        "revision_after": BASE_REVISION + 1,
        "created_nodes": created,
        "changed_nodes": [node["path"] for node in created],
        "undo_transaction": {"label": "HIA: Apply Graph", "opened": True, "committed": True},
        "rollback": {"attempted": False, "complete": True, "retained_paths": []},
        "artifacts": [],
        "job_id": None,
    }
    return request, result


def verify_pair(graph: dict | None = None) -> tuple[dict, dict]:
    graph = normalize_graph(
        fixture_graph("stairs_graph.json") if graph is None else copy.deepcopy(graph)
    )
    digest = graph_digest(graph)
    root_path = f"/obj/{graph['target']['name_hint']}"
    request = common_input("scene_read", suffix="verify")
    request.update(
        {
            "expected_hip_fingerprint": FINGERPRINT,
            "root_path": root_path,
            "expected_graph_digest": digest,
        }
    )
    result = common_output(request)
    result["result"] = {
        "valid": True,
        "root_path": root_path,
        "ownership": "hia_owned",
        "context": copy.deepcopy(graph["context"]),
        "expected_graph_digest": digest,
        "observed_graph_digest": digest,
        "digest_matches": True,
        "nodes": [
            {
                "request_local_id": node["id"],
                "path": f"{root_path}/{node['name_hint']}",
                "context": node["type"]["context"],
                "resolved_type": node["type"]["name"],
                "parameters": [
                    {
                        "name": parameter["name"],
                        "value": copy.deepcopy(parameter["value"]),
                        "expression_present": False,
                    }
                    for parameter in node["parameters"]
                ],
                "flags": copy.deepcopy(node["flags"]),
                "cook_state": "clean",
            }
            for node in graph["nodes"]
        ],
        "connections": [
            {
                "source": {"node": edge["source"]["node"], "index": edge["source"]["output"]},
                "destination": {"node": edge["destination"]["node"], "index": edge["destination"]["input"]},
            }
            for edge in graph["connections"]
        ],
        "checks": [
            {"name": name, "passed": True, "message": f"{name} passed offline."}
            for name in (
                "session", "revision", "target", "ownership", "nodes",
                "parameters", "connections", "flags", "cook", "graph_digest",
            )
        ],
    }
    return request, result


class StrictJsonTests(unittest.TestCase):
    def test_rejects_duplicate_keys_non_finite_and_float_overflow(self) -> None:
        invalid = (
            b'{"a":1,"a":2}',
            b'{"a":NaN}',
            b'{"a":Infinity}',
            b'{"a":-Infinity}',
            b'{"a":1e9999}',
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ContractError) as raised:
                strict_json_loads(raw)
            self.assertEqual("INVALID_JSON", raised.exception.code)

    def test_rejects_size_and_depth_over_limits(self) -> None:
        with self.assertRaises(ContractError) as raised:
            strict_json_loads('"' + ("x" * MAX_JSON_BYTES) + '"')
        self.assertEqual("JSON_TOO_LARGE", raised.exception.code)

        nested = ("[" * 34) + "0" + ("]" * 34)
        with self.assertRaises(ContractError) as raised:
            strict_json_loads(nested)
        self.assertEqual("JSON_DEPTH_EXCEEDED", raised.exception.code)

        parser_overflow = ("[" * 5000) + "0" + ("]" * 5000)
        with self.assertRaises(ContractError) as raised:
            strict_json_loads(parser_overflow)
        self.assertEqual("INVALID_JSON", raised.exception.code)

    def test_canonical_utf8_json_and_hash_are_order_stable(self) -> None:
        first = {"z": "机", "a": [1, True, None]}
        second = {"a": [1, True, None], "z": "机"}
        self.assertEqual(b'{"a":[1,true,null],"z":"\xe6\x9c\xba"}', canonical_json_bytes(first))
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(canonical_json_sha256(first), canonical_json_sha256(second))
        with self.assertRaises(ContractError):
            canonical_json_bytes({"bad": math.nan})


class SchemaRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = SchemaRegistry()

    def test_manifest_hashes_inventory_permissions_and_descriptors_are_frozen(self) -> None:
        self.assertEqual(EXPECTED_TOOLS, self.registry.tool_names)
        self.assertRegex(self.registry.manifest_digest, r"^[a-f0-9]{64}$")
        descriptors = self.registry.tool_descriptors()
        self.assertEqual(EXPECTED_TOOLS, tuple(item["name"] for item in descriptors))
        self.assertTrue(all(isinstance(item["inputSchema"], dict) for item in descriptors))
        self.assertEqual("scene_write", self.registry.permission_level("houdini_graph_apply"))
        self.assertEqual("scene_read", self.registry.permission_level("houdini_graph_validate"))
        self.assertEqual("scene_read", self.registry.permission_level("houdini_scene_info"))

        descriptors[0]["inputSchema"]["title"] = "mutated copy"
        self.assertNotEqual(
            "mutated copy",
            self.registry.tool_descriptors()[0]["inputSchema"]["title"],
        )

    def test_unknown_tool_is_denied(self) -> None:
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_python_exec", {})
        self.assertEqual("TOOL_NOT_ALLOWED", raised.exception.code)

    def test_all_five_valid_input_and_output_pairs_pass(self) -> None:
        pairs = {
            "houdini_scene_info": scene_info_pair(),
            "houdini_node_type_info": node_type_pair(),
            "houdini_graph_validate": validate_pair(),
            "houdini_graph_apply": apply_pair(),
            "houdini_graph_verify": verify_pair(),
        }
        for tool, (request, result) in pairs.items():
            with self.subTest(tool=tool):
                self.assertIs(request, self.registry.validate_input(tool, request))
                self.assertIs(result, self.registry.validate_output(tool, request, result))

    def test_schema_subset_rejects_unknown_fields_bounds_duplicates_and_conditions(self) -> None:
        request, _ = scene_info_pair()
        request["unexpected"] = True
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_scene_info", request)
        self.assertEqual("SCHEMA_INVALID", raised.exception.code)
        self.assertEqual("additionalProperties", raised.exception.details["keyword"])

        request, _ = scene_info_pair()
        request["base_scene_revision"] = 10**1000
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_scene_info", request)
        self.assertEqual("maximum", raised.exception.details["keyword"])

        request, _ = node_type_pair()
        request["node_types"].append(copy.deepcopy(request["node_types"][0]))
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_node_type_info", request)
        self.assertEqual("uniqueItems", raised.exception.details["keyword"])

        request, result = apply_pair()
        result["result"]["unexpected"] = True
        with self.assertRaises(ContractError):
            self.registry.validate_output("houdini_graph_apply", request, result)

    def test_error_output_and_replay_preserve_closed_output_branches(self) -> None:
        request, _ = scene_info_pair()
        failure = self.registry.make_error_output(
            "houdini_scene_info", request, "INTERNAL_ERROR", "Bounded failure."
        )
        self.assertFalse(failure["ok"])
        self.assertIsNone(failure["result"])
        self.assertEqual("INTERNAL_ERROR", failure["structured_error"]["code"])
        self.assertNotIn("retryable", failure["structured_error"])

        request, result = apply_pair()
        replay = self.registry.make_replay_output("houdini_graph_apply", request, result)
        self.assertTrue(replay["result"]["replay"])
        self.assertEqual(result["result"]["created_nodes"], replay["result"]["created_nodes"])

    def test_contract_error_is_structured_and_does_not_embed_values(self) -> None:
        request, _ = scene_info_pair()
        request["request_id"] = "contains a private value"
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_scene_info", request)
        payload = raised.exception.to_dict()
        self.assertEqual({"code", "message", "details"}, set(payload))
        self.assertNotIn("contains a private value", str(payload))


class B2ReadOnlySchemaRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = SchemaRegistry.b2_read_only()

    def test_profile_is_explicit_versioned_and_exactly_two_read_only_tools(self) -> None:
        self.assertEqual(B2_SCHEMA_VERSION, self.registry.schema_version)
        self.assertEqual(B2_READ_ONLY_TOOLS, self.registry.tool_names)
        self.assertRegex(self.registry.manifest_digest, r"^[a-f0-9]{64}$")
        descriptors = self.registry.tool_descriptors()
        self.assertEqual(
            B2_READ_ONLY_TOOLS,
            tuple(item["name"] for item in descriptors),
        )
        for name, descriptor in zip(B2_READ_ONLY_TOOLS, descriptors):
            with self.subTest(tool=name):
                self.assertEqual("scene_read", self.registry.permission_level(name))
                self.assertTrue(descriptor["annotations"]["readOnlyHint"])
                self.assertFalse(descriptor["annotations"]["destructiveHint"])
                self.assertFalse(descriptor["annotations"]["openWorldHint"])

    def test_default_registry_remains_the_frozen_b1_five_tool_profile(self) -> None:
        default = SchemaRegistry()
        self.assertEqual("0.1.0", default.schema_version)
        self.assertEqual(EXPECTED_TOOLS, default.tool_names)

    def test_scene_info_requires_bounded_houdini_build(self) -> None:
        request, result = b2_scene_info_pair()
        self.registry.validate_input("houdini_scene_info", request)
        self.registry.validate_output("houdini_scene_info", request, result)

        missing = copy.deepcopy(result)
        del missing["result"]["houdini_build"]
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_output("houdini_scene_info", request, missing)
        self.assertEqual("SCHEMA_INVALID", raised.exception.code)

        unsafe = copy.deepcopy(result)
        unsafe["result"]["houdini_build"] = "21.0.440/C:/Users"
        with self.assertRaises(ContractError):
            self.registry.validate_output("houdini_scene_info", request, unsafe)

    def test_node_types_are_read_only_and_merge_input_limit_is_faithful(self) -> None:
        request, result = b2_node_type_pair()
        self.registry.validate_input("houdini_node_type_info", request)
        self.registry.validate_output("houdini_node_type_info", request, result)

        writable = copy.deepcopy(result)
        writable["result"]["node_types"][0]["parameters"][0]["writable"] = True
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_output("houdini_node_type_info", request, writable)
        self.assertEqual("SCHEMA_INVALID", raised.exception.code)

        creatable = copy.deepcopy(result)
        creatable["result"]["node_types"][0]["creatable"] = True
        with self.assertRaises(ContractError):
            self.registry.validate_output("houdini_node_type_info", request, creatable)

        unavailable = copy.deepcopy(result)
        unavailable["result"]["node_types"][0]["available"] = False
        unavailable["result"]["node_types"][0]["resolved_name"] = None
        with self.assertRaises(ContractError):
            self.registry.validate_output(
                "houdini_node_type_info", request, unavailable
            )

        too_many_inputs = copy.deepcopy(result)
        too_many_inputs["result"]["node_types"][1]["input_count"] = 65536
        with self.assertRaises(ContractError):
            self.registry.validate_output(
                "houdini_node_type_info", request, too_many_inputs
            )

    def test_numeric_range_is_closed_bounded_and_relationally_validated(self) -> None:
        request, result = b2_node_type_pair()
        parameter = result["result"]["node_types"][0]["parameters"][0]

        missing = copy.deepcopy(result)
        del missing["result"]["node_types"][0]["parameters"][0]["numeric_range"]
        with self.assertRaises(ContractError):
            self.registry.validate_output("houdini_node_type_info", request, missing)

        extra = copy.deepcopy(result)
        extra["result"]["node_types"][0]["parameters"][0]["numeric_range"][
            "help"
        ] = "not admitted"
        with self.assertRaises(ContractError):
            self.registry.validate_output("houdini_node_type_info", request, extra)

        inverted = copy.deepcopy(result)
        inverted_range = inverted["result"]["node_types"][0]["parameters"][0][
            "numeric_range"
        ]
        inverted_range["min_value"] = parameter["numeric_range"]["max_value"]
        inverted_range["max_value"] = parameter["numeric_range"]["min_value"]
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_output("houdini_node_type_info", request, inverted)
        self.assertEqual("CONTRACT_MISMATCH", raised.exception.code)

    def test_graph_tools_and_attestation_fields_are_rejected_before_dispatch(self) -> None:
        for name in (
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        ):
            with self.subTest(tool=name), self.assertRaises(ContractError) as raised:
                self.registry.validate_input(name, {})
            self.assertEqual("TOOL_NOT_ALLOWED", raised.exception.code)

        request, _ = b2_scene_info_pair()
        for field in (
            "attestation_digest",
            "launch_id",
            "generation",
            "process_nonce",
            "catalog_digest",
            "schema_digest",
        ):
            forged = copy.deepcopy(request)
            forged[field] = "a" * 64
            with self.subTest(field=field), self.assertRaises(ContractError) as raised:
                self.registry.validate_input("houdini_scene_info", forged)
            self.assertEqual("SCHEMA_INVALID", raised.exception.code)


class CrossFieldContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = SchemaRegistry()

    def assert_mismatch(self, tool: str, request: dict, result: dict) -> ContractError:
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_output(tool, request, result)
        self.assertEqual("CONTRACT_MISMATCH", raised.exception.code)
        return raised.exception

    def test_correlation_and_revision_cannot_diverge(self) -> None:
        request, result = scene_info_pair()
        result["request_id"] = "another-request"
        self.assert_mismatch("houdini_scene_info", request, result)

        request, result = scene_info_pair()
        result["scene_revision"] = BASE_REVISION - 1
        self.assert_mismatch("houdini_scene_info", request, result)

        request, result = scene_info_pair()
        result["scene_revision"] = BASE_REVISION + 1
        self.assert_mismatch("houdini_scene_info", request, result)

        request, result = apply_pair()
        result["scene_revision"] = BASE_REVISION
        self.assert_mismatch("houdini_graph_apply", request, result)

    def test_apply_digest_approval_revision_and_created_scope_are_bound(self) -> None:
        request, _ = apply_pair()
        request["canonical_graph_digest"] = "c" * 64
        with self.assertRaises(ContractError) as raised:
            self.registry.validate_input("houdini_graph_apply", request)
        self.assertEqual("DIGEST_MISMATCH", raised.exception.code)

        request, result = apply_pair()
        result["result"]["approval_binding_digest"] = "c" * 64
        self.assert_mismatch("houdini_graph_apply", request, result)

        request, result = apply_pair()
        result["result"]["created_nodes"][-1]["path"] = (
            result["result"]["root_path"] + "/other"
        )
        self.assert_mismatch("houdini_graph_apply", request, result)

        request, result = apply_pair()
        result["result"]["changed_nodes"].append("/obj/HIA_Graph_escape/other")
        self.assert_mismatch("houdini_graph_apply", request, result)

    def test_table_and_stairs_use_identical_general_normalization(self) -> None:
        table = fixture_graph("table_graph.json")
        stairs = fixture_graph("stairs_graph.json")
        for graph in (table, stairs):
            validate_graph_relations(graph)
            normalized = normalize_graph(graph)
            shuffled = copy.deepcopy(graph)
            shuffled["nodes"].reverse()
            shuffled["connections"].reverse()
            for node in shuffled["nodes"]:
                node["parameters"].reverse()
            self.assertEqual(normalized, normalize_graph(shuffled))
            self.assertEqual(graph_digest(graph), graph_digest(shuffled))
            request, result = validate_pair(graph)
            self.registry.validate_output("houdini_graph_validate", request, result)
        self.assertNotEqual(graph_digest(table), graph_digest(stairs))

    def test_malicious_graph_relations_fail_closed(self) -> None:
        base = fixture_graph("stairs_graph.json")

        def duplicate_id(graph: dict) -> None:
            graph["nodes"][1]["id"] = graph["nodes"][0]["id"]

        def duplicate_name(graph: dict) -> None:
            graph["nodes"][1]["name_hint"] = graph["nodes"][0]["name_hint"]

        def wrong_parent(graph: dict) -> None:
            graph["nodes"][0]["parent"] = "outside"

        def duplicate_parameter(graph: dict) -> None:
            graph["nodes"][0]["parameters"].append(
                copy.deepcopy(graph["nodes"][0]["parameters"][0])
            )

        def dangling(graph: dict) -> None:
            graph["connections"][0]["source"]["node"] = "missing"

        def duplicate_destination(graph: dict) -> None:
            graph["connections"][1]["destination"] = copy.deepcopy(
                graph["connections"][0]["destination"]
            )

        def cycle(graph: dict) -> None:
            graph["connections"].append(
                {
                    "source": {"node": "output", "output": 0},
                    "destination": {"node": "step_source", "input": 1},
                }
            )

        def split_flags(graph: dict) -> None:
            graph["nodes"][-1]["flags"] = {"display": True, "render": False}
            graph["nodes"][0]["flags"] = {"display": False, "render": True}

        def unknown_type(graph: dict) -> None:
            graph["nodes"][0]["type"]["name"] = "unreviewed"

        def missing_flags(graph: dict) -> None:
            del graph["nodes"][0]["flags"]

        for mutate in (
            duplicate_id,
            duplicate_name,
            wrong_parent,
            duplicate_parameter,
            dangling,
            duplicate_destination,
            cycle,
            split_flags,
            unknown_type,
            missing_flags,
        ):
            graph = copy.deepcopy(base)
            mutate(graph)
            with self.subTest(mutation=mutate.__name__), self.assertRaises(ContractError):
                validate_graph_relations(graph)

    def test_validate_result_normalized_graph_digest_summary_and_binding_agree(self) -> None:
        request, result = validate_pair()
        result["result"]["canonical_graph_digest"] = "c" * 64
        self.assert_mismatch("houdini_graph_validate", request, result)

        request, result = validate_pair()
        result["result"]["summary"]["node_count"] += 1
        self.assert_mismatch("houdini_graph_validate", request, result)

        request, result = validate_pair()
        result["result"]["normalized_graph"]["nodes"].reverse()
        self.assert_mismatch("houdini_graph_validate", request, result)

    def test_approval_payload_binds_exact_graph_request_and_closed_effects(self) -> None:
        request, result = validate_pair()
        normalized = result["result"]["normalized_graph"]
        digest = result["result"]["canonical_graph_digest"]
        effects = graph_side_effect_summary(normalized)
        payload = approval_binding_payload(request, normalized, digest, effects)
        self.assertEqual(
            {
                "request_id", "thread_id", "turn_id", "hip_session_id",
                "expected_hip_fingerprint", "base_scene_revision", "idempotency_key",
                "deadline_ms", "permission_level", "canonical_graph_digest",
                "schema_version", "context", "target", "nodes", "connections",
                "layout", "side_effect_summary",
            },
            set(payload),
        )
        baseline = approval_binding_digest(request, normalized, digest, effects)
        changed = copy.deepcopy(normalized)
        changed["layout"]["direction"] = (
            "left_to_right"
            if changed["layout"]["direction"] == "top_to_bottom"
            else "top_to_bottom"
        )
        self.assertNotEqual(
            baseline,
            approval_binding_digest(
                request,
                changed,
                graph_digest(changed),
                graph_side_effect_summary(changed),
            ),
        )
        widened_effects = copy.deepcopy(effects)
        widened_effects["file_write_count"] = 1
        with self.assertRaises(ContractError) as raised:
            approval_binding_payload(request, normalized, digest, widened_effects)
        self.assertEqual("APPROVAL_MISMATCH", raised.exception.code)

    def test_node_type_results_exactly_match_unique_queries(self) -> None:
        request, result = node_type_pair()
        result["result"]["node_types"][0]["context"] = "Sop"
        result["result"]["node_types"][0]["requested_name"] = "merge"
        self.assert_mismatch("houdini_node_type_info", request, result)

        request, result = node_type_pair()
        result["result"]["node_types"][0]["available"] = False
        self.assert_mismatch("houdini_node_type_info", request, result)

    def test_verification_checks_issues_validity_and_digest_are_consistent(self) -> None:
        request, result = verify_pair()
        result["result"]["checks"][0]["name"] = "graph_digest"
        self.assert_mismatch("houdini_graph_verify", request, result)

        request, result = verify_pair()
        result["result"]["observed_graph_digest"] = "c" * 64
        self.assert_mismatch("houdini_graph_verify", request, result)

        request, result = verify_pair()
        result["result"]["valid"] = False
        self.assert_mismatch("houdini_graph_verify", request, result)

        request, result = verify_pair()
        result["result"]["nodes"][0]["cook_state"] = "error"
        self.assert_mismatch("houdini_graph_verify", request, result)

        request, result = verify_pair()
        for check in result["result"]["checks"]:
            if check["name"] == "graph_digest":
                check["passed"] = False
        result["result"]["valid"] = False
        self.assert_mismatch("houdini_graph_verify", request, result)

    def test_scene_summary_request_and_inventory_are_consistent(self) -> None:
        request, result = scene_info_pair()
        request["include_graph_summaries"] = False
        result["result"]["hia_graphs"] = [
            {
                "root_path": "/obj/HIA_Graph_demo",
                "context": "Object",
                "ownership": "hia_owned",
                "graph_digest": "a" * 64,
                "node_count": 1,
                "connection_count": 0,
                "cook_state": "clean",
            }
        ]
        self.assert_mismatch("houdini_scene_info", request, result)

    def test_contract_source_contains_no_object_specific_recipe(self) -> None:
        source = (REPOSITORY_ROOT / "src" / "hia_core" / "houdini_contract.py").read_text(
            encoding="utf-8"
        ).casefold()
        for marker in (
            "hia_table_", "tabletop_box", "leg_front_left", "leg_front_right",
            "leg_back_left", "leg_back_right", "merge_table", "out_table",
            "create_table", "p2_v_table",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
