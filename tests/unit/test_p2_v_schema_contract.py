from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterator


REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "houdini-mcp" / "0.1.0"
MANIFEST_PATH = SCHEMA_ROOT / "manifest.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_VERSION = "0.1.0"
EXPECTED_TOOLS = {
    "houdini_scene_info",
    "houdini_node_type_info",
    "houdini_graph_apply",
    "houdini_graph_verify",
}
REQUIRED_FAIL_CLOSED_CODES = {
    "HOUDINI_UNAVAILABLE",
    "CAPABILITY_MISMATCH",
    "MAIN_THREAD_REQUIRED",
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def load_strict_json_bytes(raw: bytes, source: Path | str) -> Any:
    """Load strict JSON without claiming to be a JSON Schema validator."""

    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
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
    if not reference.startswith("#/"):
        raise ValueError(f"reference is not document-local: {reference}")
    current = document
    for raw_token in reference[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    return current


def property_names(document: Any) -> Iterator[tuple[tuple[str, ...], str, Any]]:
    """Yield accepted object-property names, excluding constraint-only fragments."""

    for path, value in walk_json(document):
        if not isinstance(value, dict):
            continue
        properties = value.get("properties")
        if not isinstance(properties, dict):
            continue
        for name, schema in properties.items():
            yield path + ("properties", name), name, schema


def identifier_tokens(name: str) -> set[str]:
    snake = name.replace("-", "_")
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", snake)
    return {token.casefold() for token in snake.split("_") if token}


class P2VSchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_strict_json(MANIFEST_PATH)
        cls.tools = {tool["name"]: tool for tool in cls.manifest["tools"]}
        cls.schemas: dict[str, dict[str, Any]] = {}
        for tool in cls.manifest["tools"]:
            for direction in ("input", "output"):
                file_name = tool[f"{direction}Schema"]
                cls.schemas[file_name] = load_strict_json(SCHEMA_ROOT / file_name)

    def test_strict_loader_rejects_duplicate_keys_and_non_finite_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            load_strict_json_bytes(b'{"key": 1, "key": 2}', "duplicate")
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                    load_strict_json_bytes(b'{"value": ' + token + b"}", "number")

    def test_manifest_and_tool_inventory_are_frozen(self) -> None:
        self.assertEqual("1.0", self.manifest["manifestVersion"])
        self.assertEqual(SCHEMA_VERSION, self.manifest["schemaVersion"])
        self.assertEqual(SCHEMA_DIALECT, self.manifest["schemaDialect"])
        self.assertEqual(EXPECTED_TOOLS, set(self.tools))
        self.assertEqual(4, len(self.manifest["tools"]))
        self.assertEqual(8, len(self.schemas))

        referenced_files = set(self.schemas)
        disk_files = {path.name for path in SCHEMA_ROOT.glob("*.schema.json")}
        self.assertEqual(referenced_files, disk_files)
        for tool_name, tool in self.tools.items():
            self.assertEqual(tool_name, tool["name"])
            self.assertIn(tool["inputSchema"], disk_files)
            self.assertIn(tool["outputSchema"], disk_files)

    def test_manifest_sha256_values_match_exact_schema_bytes(self) -> None:
        for tool_name, tool in self.tools.items():
            for direction in ("input", "output"):
                with self.subTest(tool=tool_name, direction=direction):
                    file_name = tool[f"{direction}Schema"]
                    expected = tool[f"{direction}Sha256"]
                    self.assertRegex(expected, r"^[a-f0-9]{64}$")
                    actual = hashlib.sha256((SCHEMA_ROOT / file_name).read_bytes()).hexdigest()
                    self.assertEqual(expected, actual)

    def test_schema_dialect_ids_and_local_references_are_consistent(self) -> None:
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
                        reference = value["$ref"]
                        self.assertTrue(
                            reference.startswith("#/"),
                            f"non-local reference at {file_name}:{'/'.join(path)}",
                        )
                        try:
                            resolve_local_json_pointer(schema, reference)
                        except (KeyError, IndexError, ValueError) as exc:
                            self.fail(f"unresolved reference in {file_name}: {reference}: {exc}")

    def test_all_roots_are_closed_and_require_every_declared_property(self) -> None:
        for file_name, schema in self.schemas.items():
            with self.subTest(file=file_name):
                self.assertEqual("object", schema.get("type"))
                self.assertIs(False, schema.get("additionalProperties"))
                self.assertEqual(set(schema["properties"]), set(schema["required"]))
                self.assertEqual(len(schema["required"]), len(set(schema["required"])))

    def test_typed_quantities_strings_numbers_and_enums_are_bounded(self) -> None:
        for file_name, schema in self.schemas.items():
            for path, value in walk_json(schema):
                if not isinstance(value, dict):
                    continue
                location = f"{file_name}:{'/'.join(path)}"
                kind = value.get("type")
                if kind == "string" and "const" not in value and "enum" not in value:
                    self.assertIn("minLength", value, location)
                    self.assertIn("maxLength", value, location)
                    self.assertLessEqual(value["minLength"], value["maxLength"], location)
                elif kind == "array":
                    self.assertIn("maxItems", value, location)
                    if "minItems" in value:
                        self.assertLessEqual(value["minItems"], value["maxItems"], location)
                elif isinstance(kind, str) and kind in {"integer", "number"}:
                    self.assertTrue(
                        "minimum" in value or "exclusiveMinimum" in value,
                        location,
                    )
                    self.assertTrue(
                        "maximum" in value or "exclusiveMaximum" in value,
                        location,
                    )
                if "enum" in value:
                    enum = value["enum"]
                    self.assertIsInstance(enum, list, location)
                    self.assertGreater(len(enum), 0, location)
                    self.assertLessEqual(len(enum), 128, location)
                    serialized = [json.dumps(item, sort_keys=True) for item in enum]
                    self.assertEqual(len(serialized), len(set(serialized)), location)

    def test_only_graph_apply_has_scene_write_permission(self) -> None:
        for tool_name, tool in self.tools.items():
            expected = "scene_write" if tool_name == "houdini_graph_apply" else "scene_read"
            with self.subTest(tool=tool_name):
                self.assertEqual(expected, tool["permissionLevel"])
                input_schema = self.schemas[tool["inputSchema"]]
                permission = input_schema["properties"]["permission_level"]
                self.assertEqual({"const": expected}, permission)
                self.assertIs(
                    tool["annotations"]["readOnlyHint"],
                    expected == "scene_read",
                )
                self.assertFalse(tool["annotations"]["destructiveHint"])
                self.assertFalse(tool["annotations"]["openWorldHint"])

    def test_graph_apply_is_exactly_the_canonical_seven_node_six_edge_graph(self) -> None:
        schema = self.schemas[self.tools["houdini_graph_apply"]["inputSchema"]]
        properties = schema["properties"]
        defs = schema["$defs"]

        nodes = properties["nodes"]
        self.assertEqual((7, 7, True), (nodes["minItems"], nodes["maxItems"], nodes["uniqueItems"]))
        node_names = set(defs["boxNode"]["properties"]["name"]["enum"])
        node_names.add(defs["mergeNode"]["properties"]["name"]["const"])
        node_names.add(defs["outputNode"]["properties"]["name"]["const"])
        self.assertEqual(
            {
                "tabletop_box",
                "leg_front_left",
                "leg_front_right",
                "leg_back_left",
                "leg_back_right",
                "merge_table",
                "OUT_TABLE",
            },
            node_names,
        )
        self.assertEqual(7, len(nodes["allOf"]))
        for constraint in nodes["allOf"]:
            contains = resolve_local_json_pointer(schema, constraint["$ref"])
            self.assertEqual(1, contains["minContains"])
            self.assertEqual(1, contains["maxContains"])

        connections = properties["connections"]
        self.assertEqual(
            (6, 6, True),
            (connections["minItems"], connections["maxItems"], connections["uniqueItems"]),
        )
        connection_defs = [
            reference["$ref"].rsplit("/", 1)[-1]
            for reference in defs["approvedConnection"]["oneOf"]
        ]
        expected_connections = {
            ("tabletop_box", "merge_table", 0),
            ("leg_front_left", "merge_table", 1),
            ("leg_front_right", "merge_table", 2),
            ("leg_back_left", "merge_table", 3),
            ("leg_back_right", "merge_table", 4),
            ("merge_table", "OUT_TABLE", 0),
        }
        actual_connections = set()
        for definition_name in connection_defs:
            definition = defs[definition_name]
            self.assertIs(False, definition["additionalProperties"])
            self.assertEqual(set(definition["properties"]), set(definition["required"]))
            edge = definition["properties"]
            actual_connections.add(
                (edge["source"]["const"], edge["target"]["const"], edge["input"]["const"])
            )
        self.assertEqual(expected_connections, actual_connections)
        self.assertEqual(6, len(connections["allOf"]))
        for constraint in connections["allOf"]:
            contains = resolve_local_json_pointer(schema, constraint["$ref"])
            self.assertEqual(1, contains["minContains"])
            self.assertEqual(1, contains["maxContains"])

        self.assertEqual({"display", "render"}, set(properties["output_flags"]["properties"]))
        self.assertEqual({"const": True}, properties["output_flags"]["properties"]["display"])
        self.assertEqual({"const": True}, properties["output_flags"]["properties"]["render"])
        self.assertEqual("OUT_TABLE", properties["output_node"]["const"])

    def test_input_interfaces_do_not_expose_general_execution_or_filesystem_tools(self) -> None:
        forbidden = {
            "python",
            "hscript",
            "shell",
            "eval",
            "exec",
            "execute",
            "command",
            "process",
            "script",
            "expression",
            "callback",
            "filesystem",
            "file",
            "filepath",
            "delete",
            "save",
            "hda",
        }
        render_locations: list[tuple[str, tuple[str, ...], Any]] = []
        for tool_name, tool in self.tools.items():
            self.assertTrue(forbidden.isdisjoint(identifier_tokens(tool_name)))
            file_name = tool["inputSchema"]
            schema = self.schemas[file_name]
            for definition_name in schema.get("$defs", {}):
                self.assertTrue(
                    forbidden.isdisjoint(identifier_tokens(definition_name)),
                    f"forbidden definition interface at {file_name}:{definition_name}",
                )
            for path, name, property_schema in property_names(schema):
                tokens = identifier_tokens(name)
                self.assertTrue(
                    forbidden.isdisjoint(tokens - {"render"}),
                    f"forbidden input at {file_name}:{path}",
                )
                if "render" in tokens:
                    render_locations.append((file_name, path, property_schema))

        self.assertEqual(
            [
                (
                    "houdini_graph_apply.input.schema.json",
                    ("properties", "output_flags", "properties", "render"),
                    {"const": True},
                )
            ],
            render_locations,
        )

    def test_every_output_supports_fail_closed_capability_and_thread_errors(self) -> None:
        for tool_name, tool in self.tools.items():
            output = self.schemas[tool["outputSchema"]]
            codes = set(output["$defs"]["structuredError"]["properties"]["code"]["enum"])
            issue_codes = set(output["$defs"]["errorIssue"]["properties"]["code"]["enum"])
            with self.subTest(tool=tool_name):
                self.assertTrue(REQUIRED_FAIL_CLOSED_CODES.issubset(codes))
                self.assertTrue(REQUIRED_FAIL_CLOSED_CODES.issubset(issue_codes))

    def test_b1_and_b2_documents_keep_live_dispatch_fail_closed(self) -> None:
        documents = "\n".join(
            (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "docs/P2-V-ARCHITECTURE.md",
                "docs/P2-V-TEST-PLAN.md",
                "docs/P2-V-THREAT-MODEL.md",
            )
        ).casefold()
        for required_text in (
            "b1",
            "fake-only",
            "fail closed",
            "houdini_unavailable",
            "capability_mismatch",
            "current houdini process",
            "schema hash",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, documents)


if __name__ == "__main__":
    unittest.main()
