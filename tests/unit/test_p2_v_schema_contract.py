from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unittest
from pathlib import Path
from typing import Any, Callable, Iterator


REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "houdini-mcp" / "0.1.0"
MANIFEST_PATH = SCHEMA_ROOT / "manifest.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v"
P2_PROTOCOL_DOCS = (
    REPOSITORY_ROOT / "docs" / "P2-V-ARCHITECTURE.md",
    REPOSITORY_ROOT / "docs" / "P2-V-THREAT-MODEL.md",
    REPOSITORY_ROOT / "docs" / "P2-V-TEST-PLAN.md",
)
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_VERSION = "0.1.0"
EXPECTED_TOOLS = (
    "houdini_scene_info",
    "houdini_node_type_info",
    "houdini_graph_validate",
    "houdini_graph_apply",
    "houdini_graph_verify",
)
TOOL_RESULT_ERROR_CODES = (
    "INVALID_ARGUMENT",
    "SCHEMA_INVALID",
    "NODE_TYPE_NOT_ALLOWED",
    "NODE_TYPE_UNAVAILABLE",
    "PARAMETER_NOT_ALLOWED",
    "PARAMETER_TYPE_MISMATCH",
    "PATH_SCOPE_VIOLATION",
    "GRAPH_INVALID",
    "TOPOLOGY_NOT_ALLOWED",
    "DIGEST_MISMATCH",
    "APPROVAL_REQUIRED",
    "APPROVAL_DENIED",
    "APPROVAL_MISMATCH",
    "APPROVAL_EXPIRED",
    "DEADLINE_EXCEEDED",
    "HIP_SESSION_MISMATCH",
    "SCENE_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "NAME_CONFLICT",
    "MAIN_THREAD_REQUIRED",
    "CAPABILITY_MISMATCH",
    "HOUDINI_UNAVAILABLE",
    "WRITE_IN_PROGRESS",
    "GRAPH_NOT_FOUND",
    "OWNERSHIP_MISMATCH",
    "COOK_FAILED",
    "VERIFY_FAILED",
    "POSTCONDITION_FAILED",
    "ROLLBACK_FAILED",
    "SCENE_STATE_INDETERMINATE",
    "BRIDGE_DISCONNECTED",
    "INTERNAL_ERROR",
)
FORBIDDEN_INPUT_TOKENS = {
    "python", "hscript", "shell", "eval", "exec", "execute",
    "script", "expression", "callback", "code",
}
TABLE_PROTOCOL_MARKERS = {
    "hia_table_", "tabletop_box", "leg_front_left", "leg_front_right",
    "leg_back_left", "leg_back_right", "merge_table", "out_table",
    "create_table", "p2_v_table",
}


class SchemaValidationError(AssertionError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def load_strict_json_bytes(raw: bytes, source: Path | str) -> Any:
    """Load strict UTF-8 JSON without claiming full JSON Schema support."""

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid strict JSON in {source}: {exc}") from exc


def load_strict_json(path: Path) -> Any:
    return load_strict_json_bytes(path.read_bytes(), path)


def walk_json(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_json(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json(child, path + (str(index),))


def resolve_local_json_pointer(document: Any, reference: str) -> Any:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise ValueError(f"reference is not document-local: {reference}")
    current = document
    for raw_token in reference[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def dereference(schema: Any, document: dict[str, Any]) -> Any:
    current = schema
    seen: set[str] = set()
    while isinstance(current, dict) and set(current) == {"$ref"}:
        reference = current["$ref"]
        if reference in seen:
            raise ValueError(f"cyclic schema reference: {reference}")
        seen.add(reference)
        current = resolve_local_json_pointer(document, reference)
    return current


def property_names(document: Any) -> Iterator[tuple[tuple[str, ...], str, Any]]:
    for path, value in walk_json(document):
        if isinstance(value, dict) and isinstance(value.get("properties"), dict):
            for name, schema in value["properties"].items():
                yield path + ("properties", name), name, schema


def identifier_tokens(name: str) -> set[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.replace("-", "_"))
    return {token.casefold() for token in snake.split("_") if token}


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SemanticGraphError(ValueError):
    """Raised only by the offline test oracle below."""


def validate_fixture_graph_semantics(graph: dict[str, Any]) -> None:
    """Pure test oracle; this is not a production handler and never imports ``hou``.

    JSON Schema closes and bounds the wire representation.  These relational
    checks demonstrate the fail-closed semantics that a later, separately
    approved B1 validator must implement without giving this test helper any
    product authority.
    """

    nodes = graph["nodes"]
    node_ids = [node["id"] for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise SemanticGraphError("duplicate node id")

    name_hints = [node["name_hint"] for node in nodes]
    if len(name_hints) != len(set(name_hints)):
        raise SemanticGraphError("duplicate node name_hint")

    for node in nodes:
        if node["parent"] != "root":
            raise SemanticGraphError("node parent is not the new graph root")
        parameter_names = [parameter["name"] for parameter in node["parameters"]]
        if len(parameter_names) != len(set(parameter_names)):
            raise SemanticGraphError("duplicate parameter name")

    node_id_set = set(node_ids)
    occupied_inputs: set[tuple[str, int]] = set()
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for connection in graph["connections"]:
        source_id = connection["source"]["node"]
        destination_id = connection["destination"]["node"]
        if source_id not in node_id_set or destination_id not in node_id_set:
            raise SemanticGraphError("dangling connection endpoint")
        if source_id == destination_id:
            raise SemanticGraphError("self-link")
        destination = (destination_id, connection["destination"]["input"])
        if destination in occupied_inputs:
            raise SemanticGraphError("duplicate destination input")
        occupied_inputs.add(destination)
        if destination_id not in adjacency[source_id]:
            adjacency[source_id].add(destination_id)
            indegree[destination_id] += 1

    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        source_id = ready.pop()
        visited += 1
        for destination_id in adjacency[source_id]:
            indegree[destination_id] -= 1
            if indegree[destination_id] == 0:
                ready.append(destination_id)
    if visited != len(node_ids):
        raise SemanticGraphError("cycle")

    display_nodes = [node["id"] for node in nodes if node["flags"]["display"]]
    render_nodes = [node["id"] for node in nodes if node["flags"]["render"]]
    if len(display_nodes) != 1 or len(render_nodes) != 1:
        raise SemanticGraphError("graph needs exactly one display and one render node")
    if display_nodes != render_nodes:
        raise SemanticGraphError("display and render flags must select the same node")


def approval_binding_payload(
    request: dict[str, Any], side_effect_summary: dict[str, Any]
) -> dict[str, Any]:
    """Return the complete approval-bound material for an offline digest test."""

    graph = request["graph"]
    return {
        "request_id": request["request_id"],
        "thread_id": request["thread_id"],
        "turn_id": request["turn_id"],
        "hip_session_id": request["hip_session_id"],
        "expected_hip_fingerprint": request["expected_hip_fingerprint"],
        "base_scene_revision": request["base_scene_revision"],
        "idempotency_key": request["idempotency_key"],
        "deadline_ms": request["deadline_ms"],
        "permission_level": request["permission_level"],
        "canonical_graph_digest": request["canonical_graph_digest"],
        "schema_version": graph["schema_version"],
        "context": copy.deepcopy(graph["context"]),
        "target": copy.deepcopy(graph["target"]),
        "nodes": copy.deepcopy(graph["nodes"]),
        "connections": copy.deepcopy(graph["connections"]),
        "layout": copy.deepcopy(graph["layout"]),
        "side_effect_summary": copy.deepcopy(side_effect_summary),
    }


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and math.isfinite(value) and value.is_integer()
        )
    return False


def _matches(instance: Any, schema: Any, document: dict[str, Any], path: str) -> bool:
    try:
        validate_instance(instance, schema, document, path)
    except SchemaValidationError:
        return False
    return True


def validate_instance(
    instance: Any,
    schema: Any,
    document: dict[str, Any],
    path: str = "$",
) -> None:
    """Validate the Draft 2020-12 subset used by the frozen graph inputs."""

    if schema is True:
        return
    if schema is False:
        raise SchemaValidationError(f"{path}: false schema")
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: malformed schema")

    if "$ref" in schema:
        validate_instance(
            instance, resolve_local_json_pointer(document, schema["$ref"]), document, path
        )
    for child in schema.get("allOf", []):
        validate_instance(instance, child, document, path)
    if "oneOf" in schema:
        count = sum(_matches(instance, child, document, path) for child in schema["oneOf"])
        if count != 1:
            raise SchemaValidationError(f"{path}: oneOf matched {count} branches")
    if "if" in schema:
        branch = schema.get("then") if _matches(instance, schema["if"], document, path) else schema.get("else")
        if branch is not None:
            validate_instance(instance, branch, document, path)

    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: const mismatch")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: outside enum")
    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_json_type_matches(instance, candidate) for candidate in accepted):
            raise SchemaValidationError(f"{path}: wrong type")

    if isinstance(instance, dict):
        missing = [name for name in schema.get("required", []) if name not in instance]
        if missing:
            raise SchemaValidationError(f"{path}: missing {missing}")
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in instance:
                validate_instance(instance[name], child, document, f"{path}.{name}")
        extras = [name for name in instance if name not in properties]
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            raise SchemaValidationError(f"{path}: extra properties {extras}")
        if isinstance(additional, dict):
            for name in extras:
                validate_instance(instance[name], additional, document, f"{path}.{name}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise SchemaValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaValidationError(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            rendered = [
                json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
                for item in instance
            ]
            if len(rendered) != len(set(rendered)):
                raise SchemaValidationError(f"{path}: duplicate items")
        prefix = schema.get("prefixItems", [])
        for index, child in enumerate(prefix[: len(instance)]):
            validate_instance(instance[index], child, document, f"{path}[{index}]")
        if "items" in schema:
            items = schema["items"]
            start = len(prefix)
            if items is False and len(instance) > start:
                raise SchemaValidationError(f"{path}: fixed tuple has extra items")
            if items is not False:
                for index in range(start, len(instance)):
                    validate_instance(instance[index], items, document, f"{path}[{index}]")
        if "contains" in schema:
            count = sum(
                _matches(item, schema["contains"], document, f"{path}[{index}]")
                for index, item in enumerate(instance)
            )
            if count < schema.get("minContains", 1):
                raise SchemaValidationError(f"{path}: too few contains matches")
            if "maxContains" in schema and count > schema["maxContains"]:
                raise SchemaValidationError(f"{path}: too many contains matches")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: string too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaValidationError(f"{path}: pattern mismatch")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if isinstance(instance, float) and not math.isfinite(instance):
            raise SchemaValidationError(f"{path}: non-finite")
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaValidationError(f"{path}: below exclusive minimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise SchemaValidationError(f"{path}: above exclusive maximum")


def build_graph_request(
    tool_name: str,
    schema: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    permission = "scene_write" if tool_name == "houdini_graph_apply" else "scene_read"
    digest: str | None = canonical_digest(graph)
    if tool_name == "houdini_graph_validate":
        digest = None
    values: dict[str, Any] = {
        "request_id": f"{tool_name}-fixture",
        "thread_id": "thread-fixture",
        "turn_id": "turn-fixture",
        "hip_session_id": "hip-fixture",
        "expected_hip_fingerprint": "ab" * 32,
        "base_scene_revision": 0,
        "idempotency_key": "fixture-idempotency-0001",
        "deadline_ms": 1000,
        "permission_level": permission,
        "graph": copy.deepcopy(graph),
        "canonical_graph_digest": digest,
    }
    unknown = set(schema["required"]) - set(values)
    if unknown:
        raise AssertionError(f"fixture builder lacks fields: {sorted(unknown)}")
    return {name: values[name] for name in schema["required"]}


class P2VGeneralGraphSchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_strict_json(MANIFEST_PATH)
        cls.tools = {tool["name"]: tool for tool in cls.manifest["tools"]}
        cls.schemas: dict[str, dict[str, Any]] = {}
        for tool in cls.manifest["tools"]:
            for direction in ("input", "output"):
                file_name = tool[f"{direction}Schema"]
                cls.schemas[file_name] = load_strict_json(SCHEMA_ROOT / file_name)
        cls.table_graph = load_strict_json(FIXTURE_ROOT / "table_graph.json")
        cls.stairs_graph = load_strict_json(FIXTURE_ROOT / "stairs_graph.json")

    def test_strict_loader_rejects_duplicates_non_finite_and_overflow(self) -> None:
        for raw in (
            b'{"key":1,"key":2}', b'{"value":NaN}', b'{"value":Infinity}',
            b'{"value":-Infinity}', b'{"value":1e9999}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_strict_json_bytes(raw, "invalid")

    def test_manifest_and_ten_schema_inventory_are_frozen(self) -> None:
        self.assertEqual("1.0", self.manifest["manifestVersion"])
        self.assertEqual(SCHEMA_VERSION, self.manifest["schemaVersion"])
        self.assertEqual(SCHEMA_DIALECT, self.manifest["schemaDialect"])
        self.assertEqual(
            "canonical-json-utf8-v1", self.manifest["schemaDigestEncoding"]
        )
        self.assertEqual("frozen_pre_release", self.manifest["contractStatus"])
        self.assertEqual(EXPECTED_TOOLS, tuple(tool["name"] for tool in self.manifest["tools"]))
        self.assertEqual(5, len(self.tools))
        self.assertEqual(10, len(self.schemas))
        self.assertEqual(
            set(self.schemas),
            {path.name for path in SCHEMA_ROOT.glob("*.schema.json")},
        )

    def test_manifest_sha256_matches_canonical_strict_json(self) -> None:
        for tool_name, tool in self.tools.items():
            for direction in ("input", "output"):
                file_name = tool[f"{direction}Schema"]
                expected = tool[f"{direction}Sha256"]
                with self.subTest(tool=tool_name, direction=direction):
                    self.assertRegex(expected, r"^[a-f0-9]{64}$")
                    parsed = load_strict_json(SCHEMA_ROOT / file_name)
                    actual = canonical_digest(parsed)
                    self.assertEqual(expected, actual)

    def test_schema_digest_is_independent_of_crlf_and_indentation(self) -> None:
        tool = self.tools["houdini_graph_validate"]
        path = SCHEMA_ROOT / tool["inputSchema"]
        parsed = load_strict_json(path)
        reformatted = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            indent=4,
            sort_keys=False,
        ).replace("\n", "\r\n").encode("utf-8")
        reparsed = load_strict_json_bytes(reformatted, "crlf-reformatted-schema")
        self.assertEqual(tool["inputSha256"], canonical_digest(reparsed))

    def test_draft_ids_and_document_local_references_are_consistent(self) -> None:
        for tool_name, tool in self.tools.items():
            for direction in ("input", "output"):
                file_name = tool[f"{direction}Schema"]
                schema = self.schemas[file_name]
                with self.subTest(file=file_name):
                    self.assertEqual(SCHEMA_DIALECT, schema["$schema"])
                    self.assertEqual(
                        f"urn:hia:houdini-mcp:{SCHEMA_VERSION}:{tool_name}:{direction}",
                        schema["$id"],
                    )
                    self.assertNotIn("$dynamicRef", schema)
                    for path, value in walk_json(schema):
                        if not isinstance(value, dict) or "$ref" not in value:
                            continue
                        self.assertTrue(
                            value["$ref"].startswith("#/"),
                            f"external ref at {file_name}:{'/'.join(path)}",
                        )
                        try:
                            resolve_local_json_pointer(schema, value["$ref"])
                        except (KeyError, IndexError, TypeError, ValueError) as exc:
                            self.fail(f"broken ref in {file_name}: {value['$ref']}: {exc}")

    def test_roots_and_accepted_object_boundaries_are_deny_by_default(self) -> None:
        for file_name, schema in self.schemas.items():
            with self.subTest(file=file_name, path="$"):
                self.assertEqual("object", schema.get("type"))
                self.assertIs(False, schema.get("additionalProperties"))
                self.assertEqual(set(schema["properties"]), set(schema["required"]))
            for path, value in walk_json(schema):
                if not isinstance(value, dict) or value.get("type") != "object":
                    continue
                if not ({"properties", "required"} & set(value)):
                    continue
                if {"if", "then", "else"} & set(path):
                    continue
                with self.subTest(file=file_name, path="/".join(path)):
                    self.assertIs(False, value.get("additionalProperties"))
                    self.assertEqual(
                        set(value.get("properties", {})),
                        set(value.get("required", [])),
                    )

    def test_strings_arrays_numbers_and_enums_have_finite_bounds(self) -> None:
        fixed_pattern = re.compile(r"\{\d+\}")
        for file_name, schema in self.schemas.items():
            for path, value in walk_json(schema):
                if not isinstance(value, dict):
                    continue
                location = f"{file_name}:{'/'.join(path)}"
                kind = value.get("type")
                if kind == "string" and "const" not in value and "enum" not in value:
                    bounded_pattern = isinstance(value.get("pattern"), str) and bool(
                        fixed_pattern.search(value["pattern"])
                    )
                    self.assertTrue("maxLength" in value or bounded_pattern, location)
                elif kind == "array":
                    self.assertIn("maxItems", value, location)
                    if "minItems" in value:
                        self.assertLessEqual(value["minItems"], value["maxItems"], location)
                elif isinstance(kind, str) and kind in {"integer", "number"}:
                    self.assertTrue("minimum" in value or "exclusiveMinimum" in value, location)
                    self.assertTrue("maximum" in value or "exclusiveMaximum" in value, location)
                if "enum" in value:
                    rendered = [
                        json.dumps(item, sort_keys=True, separators=(",", ":"))
                        for item in value["enum"]
                    ]
                    self.assertGreater(len(rendered), 0, location)
                    self.assertLessEqual(len(rendered), 128, location)
                    self.assertEqual(len(rendered), len(set(rendered)), location)

    def test_only_graph_apply_is_scene_write_and_every_tool_is_closed_world(self) -> None:
        for tool_name, tool in self.tools.items():
            expected = "scene_write" if tool_name == "houdini_graph_apply" else "scene_read"
            with self.subTest(tool=tool_name):
                self.assertEqual(expected, tool["permissionLevel"])
                input_schema = self.schemas[tool["inputSchema"]]
                self.assertEqual({"const": expected}, input_schema["properties"]["permission_level"])
                self.assertIs(tool["annotations"]["readOnlyHint"], expected == "scene_read")
                self.assertFalse(tool["annotations"]["destructiveHint"])
                self.assertFalse(tool["annotations"]["openWorldHint"])

    def test_graph_contract_is_generic_bounded_and_small_allowlist(self) -> None:
        schema = self.schemas[self.tools["houdini_graph_validate"]["inputSchema"]]
        graph = dereference(schema["properties"]["graph"], schema)
        self.assertEqual(
            {"schema_version", "context", "target", "nodes", "connections", "layout"},
            set(graph["properties"]),
        )
        self.assertEqual(set(graph["properties"]), set(graph["required"]))
        self.assertIs(False, graph["additionalProperties"])
        nodes = graph["properties"]["nodes"]
        connections = graph["properties"]["connections"]
        self.assertEqual((1, 128), (nodes["minItems"], nodes["maxItems"]))
        self.assertEqual(256, connections["maxItems"])
        self.assertNotIn("contains", nodes)
        self.assertNotIn("allOf", nodes)

        graph_text = json.dumps(schema, sort_keys=True)
        for required in ("box", "transform", "merge", "null"):
            self.assertIn(f'"{required}"', graph_text)
        for value_type in ("float", "int", "bool", "string", "tuple"):
            self.assertIn(f'"{value_type}"', graph_text)
        parameter_arrays = [
            value for _, name, value in property_names(schema) if name == "parameters"
        ]
        self.assertEqual(1, len(parameter_arrays))
        self.assertEqual(64, parameter_arrays[0]["maxItems"])

    def test_table_and_non_table_fixtures_validate_for_validate_and_apply(self) -> None:
        for fixture_name, graph in (("table", self.table_graph), ("stairs", self.stairs_graph)):
            for tool_name in ("houdini_graph_validate", "houdini_graph_apply"):
                schema = self.schemas[self.tools[tool_name]["inputSchema"]]
                request = build_graph_request(tool_name, schema, graph)
                with self.subTest(fixture=fixture_name, tool=tool_name):
                    validate_instance(request, schema, schema)
                    validate_fixture_graph_semantics(graph)

    def test_non_table_fixture_is_structurally_different(self) -> None:
        table_types = [node["type"]["name"] for node in self.table_graph["nodes"]]
        stairs_types = [node["type"]["name"] for node in self.stairs_graph["nodes"]]
        self.assertNotEqual(len(table_types), len(stairs_types))
        self.assertIn("transform", stairs_types)
        self.assertNotIn("transform", table_types)
        self.assertNotEqual(
            len(self.table_graph["connections"]), len(self.stairs_graph["connections"])
        )

    def test_fixture_parameter_names_are_provisional_until_b2_live_schema(self) -> None:
        """Fixtures use plausible names only; B2 live node type schema is authoritative."""

        parameter_names = {
            parameter["name"]
            for graph in (self.table_graph, self.stairs_graph)
            for node in graph["nodes"]
            for parameter in node["parameters"]
        }
        self.assertEqual({"size", "t"}, parameter_names)

    def test_typed_value_five_kinds_and_homogeneous_tuples_directly(self) -> None:
        schema = self.schemas[self.tools["houdini_graph_validate"]["inputSchema"]]
        typed_value = schema["$defs"]["typedValue"]
        valid_values = (
            {"type": "float", "value": 1.25},
            {"type": "int", "value": 2},
            {"type": "bool", "value": True},
            {"type": "string", "value": "bounded metadata"},
            {"type": "tuple", "items_type": "float", "value": [1.0, 2.5]},
            {"type": "tuple", "items_type": "int", "value": [1, 2]},
            {"type": "tuple", "items_type": "bool", "value": [True, False]},
            {"type": "tuple", "items_type": "string", "value": ["a", "b"]},
        )
        self.assertEqual(
            {"float", "int", "bool", "string", "tuple"},
            {value["type"] for value in valid_values},
        )
        for value in valid_values:
            with self.subTest(value=value):
                validate_instance(value, typed_value, schema)

        heterogeneous = (
            {"type": "tuple", "items_type": "float", "value": [1.0, "2"]},
            {"type": "tuple", "items_type": "int", "value": [1, True]},
            {"type": "tuple", "items_type": "bool", "value": [True, 0]},
            {"type": "tuple", "items_type": "string", "value": ["a", 2]},
        )
        for value in heterogeneous:
            with self.subTest(value=value), self.assertRaises(SchemaValidationError):
                validate_instance(value, typed_value, schema)

    def test_semantic_oracle_rejects_malicious_relational_graphs(self) -> None:
        def duplicate_node_id(graph: dict[str, Any]) -> None:
            graph["nodes"][1]["id"] = graph["nodes"][0]["id"]

        def duplicate_name_hint(graph: dict[str, Any]) -> None:
            graph["nodes"][1]["name_hint"] = graph["nodes"][0]["name_hint"]

        def duplicate_parameter(graph: dict[str, Any]) -> None:
            graph["nodes"][0]["parameters"].append(
                copy.deepcopy(graph["nodes"][0]["parameters"][0])
            )

        def dangling_endpoint(graph: dict[str, Any]) -> None:
            graph["connections"][0]["source"]["node"] = "missing"

        def self_link(graph: dict[str, Any]) -> None:
            graph["connections"].append(
                {
                    "source": {"node": "step_source", "output": 0},
                    "destination": {"node": "step_source", "input": 63},
                }
            )

        def duplicate_destination(graph: dict[str, Any]) -> None:
            duplicate = copy.deepcopy(graph["connections"][0])
            duplicate["source"]["node"] = "landing"
            graph["connections"].append(duplicate)

        def cycle(graph: dict[str, Any]) -> None:
            graph["connections"].append(
                {
                    "source": {"node": "output", "output": 0},
                    "destination": {"node": "combine", "input": 63},
                }
            )

        def parent_outside_root(graph: dict[str, Any]) -> None:
            graph["nodes"][0]["parent"] = "other"

        def no_display(graph: dict[str, Any]) -> None:
            graph["nodes"][-1]["flags"]["display"] = False

        def multiple_render(graph: dict[str, Any]) -> None:
            graph["nodes"][0]["flags"]["render"] = True

        def split_display_render(graph: dict[str, Any]) -> None:
            graph["nodes"][-1]["flags"]["render"] = False
            graph["nodes"][0]["flags"]["render"] = True

        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("duplicate node id", duplicate_node_id),
            ("duplicate name_hint", duplicate_name_hint),
            ("duplicate parameter", duplicate_parameter),
            ("dangling endpoint", dangling_endpoint),
            ("self-link", self_link),
            ("duplicate destination input", duplicate_destination),
            ("cycle", cycle),
            ("parent outside root", parent_outside_root),
            ("no display", no_display),
            ("multiple render", multiple_render),
            ("different display and render", split_display_render),
        )
        for name, mutate in mutations:
            graph = copy.deepcopy(self.stairs_graph)
            mutate(graph)
            with self.subTest(case=name), self.assertRaises(SemanticGraphError):
                validate_fixture_graph_semantics(graph)

    def test_protocol_has_no_table_roles_or_fixed_five_box_contract(self) -> None:
        paths = [MANIFEST_PATH] + [
            SCHEMA_ROOT / file_name for file_name in sorted(self.schemas)
        ] + [path for path in P2_PROTOCOL_DOCS if path.is_file()]
        protocol_text = "\n".join(path.read_text(encoding="utf-8") for path in paths).casefold()
        for marker in sorted(TABLE_PROTOCOL_MARKERS):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, protocol_text)
        schema = self.schemas[self.tools["houdini_graph_validate"]["inputSchema"]]
        graph = dereference(schema["properties"]["graph"], schema)
        nodes = graph["properties"]["nodes"]
        self.assertEqual((1, 128), (nodes["minItems"], nodes["maxItems"]))
        self.assertNotIn("contains", nodes)
        self.assertIn('"transform"', json.dumps(schema, sort_keys=True))

    @unittest.skipUnless(
        all(path.is_file() for path in P2_PROTOCOL_DOCS),
        "internal historical P2 documents are not part of the public repository",
    )
    def test_documented_error_inventory_matches_closed_output_schemas(self) -> None:
        documents = [path.read_text(encoding="utf-8") for path in P2_PROTOCOL_DOCS]
        for error_code in TOOL_RESULT_ERROR_CODES:
            with self.subTest(error_code=error_code):
                self.assertIn(error_code, documents[1])
                self.assertIn(error_code, documents[2])

        control_only = {
            "AUTH_REQUIRED",
            "TOOL_NOT_ALLOWED",
            "MALFORMED_REQUEST",
            "REQUEST_TOO_LARGE",
            "CANCELLED",
            "QUEUE_FULL",
            "SHUTTING_DOWN",
        }
        self.assertTrue(control_only.isdisjoint(TOOL_RESULT_ERROR_CODES))
        for error_code in control_only:
            with self.subTest(control_error=error_code):
                self.assertIn(error_code, documents[1])
                self.assertIn(error_code, documents[2])

    def test_input_contracts_expose_no_code_expression_or_callback_surface(self) -> None:
        for tool_name, tool in self.tools.items():
            self.assertTrue(FORBIDDEN_INPUT_TOKENS.isdisjoint(identifier_tokens(tool_name)))
            file_name = tool["inputSchema"]
            schema = self.schemas[file_name]
            for definition_name in schema.get("$defs", {}):
                self.assertTrue(
                    FORBIDDEN_INPUT_TOKENS.isdisjoint(identifier_tokens(definition_name)),
                    f"{file_name}:$defs/{definition_name}",
                )
            for path, name, _ in property_names(schema):
                self.assertTrue(
                    FORBIDDEN_INPUT_TOKENS.isdisjoint(identifier_tokens(name)),
                    f"{file_name}:{'/'.join(path)}",
                )

    def test_only_hia_graph_root_grammar_is_accepted(self) -> None:
        schema = self.schemas[self.tools["houdini_graph_validate"]["inputSchema"]]
        graph = dereference(schema["properties"]["graph"], schema)
        target = dereference(graph["properties"]["target"], schema)
        self.assertEqual("/obj", target["properties"]["parent_path"]["const"])
        self.assertTrue(target["properties"]["name_hint"]["pattern"].startswith("^HIA_Graph_"))
        self.assertTrue(self.table_graph["target"]["name_hint"].startswith("HIA_Graph_"))
        self.assertTrue(self.stairs_graph["target"]["name_hint"].startswith("HIA_Graph_"))
        for tool_name in ("houdini_graph_validate", "houdini_graph_apply"):
            tool_schema = self.schemas[self.tools[tool_name]["inputSchema"]]
            request = build_graph_request(tool_name, tool_schema, self.stairs_graph)
            request["graph"]["target"]["name_hint"] = "HIA_Table_forbidden"
            with self.subTest(tool=tool_name), self.assertRaises(SchemaValidationError):
                validate_instance(request, tool_schema, tool_schema)

    def test_unknown_types_code_fields_and_graph_fields_fail_closed(self) -> None:
        schema = self.schemas[self.tools["houdini_graph_validate"]["inputSchema"]]
        unknown = build_graph_request("houdini_graph_validate", schema, self.stairs_graph)
        unknown["graph"]["nodes"][0]["type"]["name"] = "blast"
        with self.assertRaises(SchemaValidationError):
            validate_instance(unknown, schema, schema)

        code_field = build_graph_request("houdini_graph_validate", schema, self.stairs_graph)
        code_field["graph"]["nodes"][0]["parameters"][0]["code"] = "forbidden"
        with self.assertRaises(SchemaValidationError):
            validate_instance(code_field, schema, schema)

        extra = build_graph_request("houdini_graph_validate", schema, self.stairs_graph)
        extra["graph"]["unreviewed"] = True
        with self.assertRaises(SchemaValidationError):
            validate_instance(extra, schema, schema)

    def test_validate_has_no_claimed_digest_and_apply_requires_one(self) -> None:
        validate_schema = self.schemas[
            self.tools["houdini_graph_validate"]["inputSchema"]
        ]
        apply_schema = self.schemas[self.tools["houdini_graph_apply"]["inputSchema"]]
        self.assertNotIn("canonical_graph_digest", validate_schema["properties"])
        self.assertNotIn("canonical_graph_digest", validate_schema["required"])
        self.assertIn("canonical_graph_digest", apply_schema["properties"])
        self.assertIn("canonical_graph_digest", apply_schema["required"])

    def test_five_tools_keep_their_general_graph_responsibilities(self) -> None:
        scene_output = self.schemas[self.tools["houdini_scene_info"]["outputSchema"]]
        scene_result = scene_output["$defs"]["sceneResult"]
        self.assertTrue(
            {"hip_fingerprint", "enabled_contexts", "hia_graphs"}.issubset(
                scene_result["properties"]
            )
        )

        type_output = self.schemas[
            self.tools["houdini_node_type_info"]["outputSchema"]
        ]
        type_info = type_output["$defs"]["nodeTypeInfo"]
        parameter_info = type_output["$defs"]["parameterInfo"]
        self.assertEqual(
            "live_houdini_instance",
            type_info["properties"]["schema_source"]["const"],
        )
        self.assertIs(
            False,
            parameter_info["properties"]["allows_expression"]["const"],
        )

        validate_output = self.schemas[
            self.tools["houdini_graph_validate"]["outputSchema"]
        ]
        validate_result = validate_output["$defs"]["validateResult"]
        self.assertEqual(
            {"const": False}, validate_result["properties"]["scene_mutated"]
        )
        self.assertTrue(
            {"normalized_graph", "canonical_graph_digest", "approval_binding_digest"}
            .issubset(validate_result["properties"])
        )

        apply_output = self.schemas[self.tools["houdini_graph_apply"]["outputSchema"]]
        apply_result = apply_output["$defs"]["applyResult"]
        self.assertEqual(
            "HIA: Apply Graph",
            apply_output["$defs"]["undoTransaction"]["properties"]["label"]["const"],
        )
        self.assertEqual(0, apply_result["properties"]["artifacts"]["maxItems"])
        self.assertEqual("null", apply_result["properties"]["job_id"]["type"])

        verify_output = self.schemas[
            self.tools["houdini_graph_verify"]["outputSchema"]
        ]
        verify_result = verify_output["$defs"]["verifyResult"]
        self.assertTrue(
            {
                "nodes",
                "connections",
                "expected_graph_digest",
                "observed_graph_digest",
                "checks",
            }.issubset(verify_result["properties"])
        )

    def test_approval_binding_digest_covers_full_normalized_request_and_effects(self) -> None:
        apply_schema = self.schemas[self.tools["houdini_graph_apply"]["inputSchema"]]
        request = build_graph_request(
            "houdini_graph_apply", apply_schema, self.stairs_graph
        )
        side_effect_summary = {
            "operation": "create_hia_owned_graph",
            "target_parent": "/obj",
            "new_container_count": 1,
            "node_count": len(self.stairs_graph["nodes"]),
            "connection_count": len(self.stairs_graph["connections"]),
            "undo_transaction_count": 1,
            "expected_revision_delta": 1,
            "file_write_count": 0,
        }
        baseline_payload = approval_binding_payload(request, side_effect_summary)
        baseline_digest = canonical_digest(baseline_payload)

        def set_field(name: str, value: Any) -> Callable[[dict[str, Any]], None]:
            def mutate(payload: dict[str, Any]) -> None:
                payload[name] = value

            return mutate

        def change_context(payload: dict[str, Any]) -> None:
            payload["context"]["children"] = "FutureContext"

        def change_target(payload: dict[str, Any]) -> None:
            payload["target"]["name_hint"] = "HIA_Graph_other"

        def change_node_id(payload: dict[str, Any]) -> None:
            payload["nodes"][0]["id"] = "changed_id"

        def change_node_type(payload: dict[str, Any]) -> None:
            payload["nodes"][0]["type"]["name"] = "null"

        def change_node_name(payload: dict[str, Any]) -> None:
            payload["nodes"][0]["name_hint"] = "changed_name"

        def change_node_parent(payload: dict[str, Any]) -> None:
            payload["nodes"][0]["parent"] = "changed_parent"

        def change_parameter(payload: dict[str, Any]) -> None:
            payload["nodes"][0]["parameters"][0]["value"]["value"][0] = 9.0

        def change_connection(payload: dict[str, Any]) -> None:
            payload["connections"][0]["source"]["output"] = 1

        def change_flags(payload: dict[str, Any]) -> None:
            payload["nodes"][-1]["flags"]["display"] = False

        def change_layout(payload: dict[str, Any]) -> None:
            payload["layout"]["direction"] = "left_to_right"

        def change_side_effect_summary(payload: dict[str, Any]) -> None:
            payload["side_effect_summary"]["node_count"] += 1

        mutators: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("request_id", set_field("request_id", "request-other")),
            ("thread_id", set_field("thread_id", "thread-other")),
            ("turn_id", set_field("turn_id", "turn-other")),
            ("hip_session_id", set_field("hip_session_id", "hip-other")),
            (
                "fingerprint",
                set_field("expected_hip_fingerprint", "cd" * 32),
            ),
            ("base revision", set_field("base_scene_revision", 1)),
            (
                "idempotency key",
                set_field("idempotency_key", "fixture-idempotency-0002"),
            ),
            ("deadline", set_field("deadline_ms", 1001)),
            ("permission", set_field("permission_level", "scene_read")),
            ("claimed graph digest", set_field("canonical_graph_digest", "ef" * 32)),
            ("schema version", set_field("schema_version", "0.1.1")),
            ("context", change_context),
            ("target", change_target),
            ("node id", change_node_id),
            ("node type", change_node_type),
            ("node name", change_node_name),
            ("node parent", change_node_parent),
            ("typed parameter", change_parameter),
            ("connection", change_connection),
            ("flags", change_flags),
            ("layout", change_layout),
            ("side-effect summary", change_side_effect_summary),
        )
        for name, mutate in mutators:
            changed = copy.deepcopy(baseline_payload)
            mutate(changed)
            with self.subTest(field=name):
                self.assertNotEqual(baseline_digest, canonical_digest(changed))

    def test_outputs_keep_structured_failures_and_manifest_denies_by_default(self) -> None:
        self.assertEqual("deny_by_default", self.manifest["protocolPolicy"])
        self.assertEqual(["Object", "Sop"], self.manifest["activeContexts"])
        shared_error_codes: tuple[str, ...] | None = None
        for tool_name in EXPECTED_TOOLS:
            output = self.schemas[self.tools[tool_name]["outputSchema"]]
            with self.subTest(tool=tool_name):
                self.assertTrue(
                    {
                        "ok",
                        "request_id",
                        "thread_id",
                        "turn_id",
                        "hip_session_id",
                        "base_scene_revision",
                        "idempotency_key",
                        "scene_revision",
                        "result",
                        "warnings",
                        "structured_error",
                    }.issubset(output["required"])
                )
                self.assertIn("structured_error", output["properties"])
                error = output["$defs"]["error"]
                code = error["properties"]["code"]
                self.assertEqual("string", code["type"])
                codes = tuple(code["enum"])
                self.assertEqual(TOOL_RESULT_ERROR_CODES, codes)
                self.assertEqual(len(codes), len(set(codes)))
                for error_code in codes:
                    self.assertRegex(error_code, r"^[A-Z][A-Z0-9_]{0,63}$")
                    self.assertLessEqual(len(error_code), 64)
                if shared_error_codes is None:
                    shared_error_codes = codes
                else:
                    self.assertEqual(shared_error_codes, codes)

                detail_schema = output["$defs"]["detailEntry"]["properties"]["value"]
                self.assertEqual(4, len(detail_schema["oneOf"]))
                for branch in detail_schema["oneOf"]:
                    if branch.get("type") == "string":
                        self.assertLessEqual(branch["maxLength"], 1024)
                    if branch.get("type") == "number":
                        self.assertEqual(-9007199254740991, branch["minimum"])
                        self.assertEqual(9007199254740991, branch["maximum"])

                conditionals = [
                    condition
                    for condition in output.get("allOf", [])
                    if condition.get("if", {})
                    .get("properties", {})
                    .get("ok", {})
                    .get("const")
                    is True
                ]
                self.assertEqual(1, len(conditionals))
                success = conditionals[0]["then"]["properties"]
                failure = conditionals[0]["else"]["properties"]
                self.assertFalse(
                    _matches(None, success["result"], output, "$.result")
                )
                self.assertTrue(
                    _matches(None, success["structured_error"], output, "$.structured_error")
                )
                self.assertTrue(
                    _matches(None, failure["result"], output, "$.result")
                )
                self.assertFalse(
                    _matches(None, failure["structured_error"], output, "$.structured_error")
                )

        apply_output = self.schemas[
            self.tools["houdini_graph_apply"]["outputSchema"]
        ]
        apply_result = apply_output["$defs"]["applyResult"]
        owned_path_pattern = (
            apply_result["properties"]["changed_nodes"]["items"]["pattern"]
        )
        retained_path_pattern = (
            apply_output["$defs"]["rollback"]
            ["properties"]["retained_paths"]["items"]["pattern"]
        )
        self.assertEqual(owned_path_pattern, retained_path_pattern)
        self.assertTrue(owned_path_pattern.startswith("^/obj/HIA_Graph_"))


if __name__ == "__main__":
    unittest.main()
