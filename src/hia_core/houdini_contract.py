"""Frozen, offline contract validation for the P2-V Houdini tool slice.

This module uses only the Python standard library.  It reads the reviewed
project-local schemas, validates JSON values, and enforces relations that JSON
Schema cannot express.  It does not import Houdini, open a network connection,
or execute a tool.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "0.1.0"
B2_SCHEMA_VERSION = "0.2.0"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_JSON_BYTES = 262_144
MAX_JSON_DEPTH = 32
EXPECTED_TOOLS = (
    "houdini_scene_info",
    "houdini_node_type_info",
    "houdini_graph_validate",
    "houdini_graph_apply",
    "houdini_graph_verify",
)
_EXPECTED_PERMISSIONS = {
    "houdini_scene_info": "scene_read",
    "houdini_node_type_info": "scene_read",
    "houdini_graph_validate": "scene_read",
    "houdini_graph_apply": "scene_write",
    "houdini_graph_verify": "scene_read",
}
B2_READ_ONLY_TOOLS = (
    "houdini_scene_info",
    "houdini_node_type_info",
)
_B2_READ_ONLY_PERMISSIONS = {
    "houdini_scene_info": "scene_read",
    "houdini_node_type_info": "scene_read",
}
_B1_FROZEN_PROFILE = "b1_frozen"
_B2_READ_ONLY_PROFILE = "b2_read_only"
_ALLOWED_GRAPH_NODE_TYPES = frozenset(
    {
        ("Sop", "box"),
        ("Sop", "transform"),
        ("Sop", "merge"),
        ("Sop", "null"),
    }
)
_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "minProperties",
        "maxProperties",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "oneOf",
        "allOf",
        "if",
        "then",
        "else",
    }
)


class ContractError(ValueError):
    """A bounded, JSON-serializable contract failure."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": copy.deepcopy(self.details),
        }


def _error(code: str, message: str, **details: Any) -> None:
    raise ContractError(code, message, details)


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant: {token}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _check_json_value(
    value: Any,
    *,
    path: str = "$",
    depth: int = 0,
    max_depth: int = MAX_JSON_DEPTH,
    active: set[int] | None = None,
) -> None:
    if depth > max_depth:
        _error(
            "JSON_DEPTH_EXCEEDED",
            "JSON nesting exceeds the configured limit",
            path=path,
            limit=max_depth,
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _error("INVALID_JSON", "JSON numbers must be finite", path=path)
        return
    if active is None:
        active = set()
    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            _error("INVALID_JSON", "Cyclic objects are not JSON values", path=path)
        active.add(marker)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    _error("INVALID_JSON", "JSON object keys must be strings", path=path)
                _check_json_value(
                    child,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                    max_depth=max_depth,
                    active=active,
                )
        finally:
            active.remove(marker)
        return
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            _error("INVALID_JSON", "Cyclic arrays are not JSON values", path=path)
        active.add(marker)
        try:
            for index, child in enumerate(value):
                _check_json_value(
                    child,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    max_depth=max_depth,
                    active=active,
                )
        finally:
            active.remove(marker)
        return
    _error("INVALID_JSON", "Value contains a non-JSON type", path=path)


def strict_json_loads(
    raw: bytes | str,
    source: str = "<json>",
    *,
    max_bytes: int = MAX_JSON_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
) -> Any:
    """Decode bounded RFC JSON, rejecting duplicates and non-finite numbers."""

    if not isinstance(raw, (bytes, str)):
        _error("INVALID_JSON", "JSON input must be bytes or text", source=source)
    try:
        encoded = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        _error("INVALID_JSON", "JSON text is not valid UTF-8", source=source, reason=str(exc))
    if len(encoded) > max_bytes:
        _error(
            "JSON_TOO_LARGE",
            "JSON input exceeds the configured byte limit",
            source=source,
            limit=max_bytes,
            actual=len(encoded),
        )
    try:
        text = encoded.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        _error("INVALID_JSON", "Strict JSON decoding failed", source=source, reason=str(exc))
    _check_json_value(value, max_depth=max_depth)
    return value


def canonical_json_bytes(
    value: Any,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
) -> bytes:
    """Return deterministic, compact, sorted-key UTF-8 JSON bytes."""

    _check_json_value(value, max_depth=max_depth)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        _error("INVALID_JSON", "Value cannot be encoded as canonical JSON", reason=str(exc))
    if len(encoded) > max_bytes:
        _error(
            "JSON_TOO_LARGE",
            "Canonical JSON exceeds the configured byte limit",
            limit=max_bytes,
            actual=len(encoded),
        )
    return encoded


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def normalize_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic 0.1.0 representation of a declarative graph.

    Array order has no execution authority for nodes, parameter assignments, or
    connections.  Sorting those collections makes equivalent declarations
    produce identical canonical bytes without changing typed values or layout.
    The caller still has to run :func:`validate_graph_relations` before treating
    the result as executable.
    """

    if not isinstance(graph, Mapping):
        _error("GRAPH_INVALID", "Graph must be a JSON object", path="$.graph")
    normalized = copy.deepcopy(dict(graph))
    _check_json_value(normalized, path="$.graph")
    nodes = normalized.get("nodes")
    connections = normalized.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, list):
        _error(
            "GRAPH_INVALID",
            "Graph nodes and connections must be arrays",
            path="$.graph",
        )
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("parameters"), list):
            _error(
                "GRAPH_INVALID",
                "Every graph node must contain a parameter array",
                path=f"$.graph.nodes[{index}]",
            )
        node["parameters"] = sorted(
            node["parameters"],
            key=lambda item: (
                item.get("name", "")
                if isinstance(item, dict) and isinstance(item.get("name", ""), str)
                else ""
            ),
        )
    normalized["nodes"] = sorted(
        nodes,
        key=lambda item: (
            item.get("id", "")
            if isinstance(item, dict) and isinstance(item.get("id", ""), str)
            else ""
        ),
    )

    def connection_key(item: Any) -> tuple[Any, ...]:
        if not isinstance(item, dict):
            return ("", -1, "", -1)
        source = item.get("source", {})
        destination = item.get("destination", {})
        if not isinstance(source, dict) or not isinstance(destination, dict):
            return ("", -1, "", -1)
        return (
            source.get("node", "") if isinstance(source.get("node", ""), str) else "",
            source.get("output", -1) if isinstance(source.get("output", -1), int) else -1,
            destination.get("node", "") if isinstance(destination.get("node", ""), str) else "",
            destination.get("input", -1) if isinstance(destination.get("input", -1), int) else -1,
        )

    normalized["connections"] = sorted(connections, key=connection_key)
    return normalized


def validate_graph_relations(graph: Mapping[str, Any]) -> None:
    """Fail closed on graph relations that JSON Schema cannot express."""

    normalized = normalize_graph(graph)
    if normalized.get("schema_version") != SCHEMA_VERSION or normalized.get("context") != {
        "root": "Object",
        "children": "Sop",
    }:
        _error(
            "GRAPH_INVALID",
            "Graph version or active context is outside the frozen contract",
            path="$.graph",
        )
    target = normalized.get("target")
    if not isinstance(target, dict) or target.get("root_type") != {
        "context": "Object",
        "name": "geo",
    }:
        _error(
            "GRAPH_INVALID",
            "Graph target must be the approved Object/geo root",
            path="$.graph.target.root_type",
        )
    if (
        target.get("parent_path") != "/obj"
        or target.get("root_local_id") != "root"
        or target.get("ownership") != "hia_owned_new"
    ):
        _error(
            "GRAPH_INVALID",
            "Graph target is outside the new HIA-owned root policy",
            path="$.graph.target",
        )

    nodes = normalized["nodes"]
    node_ids: list[str] = []
    name_hints: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            _error("GRAPH_INVALID", "Graph node must be an object", path=f"$.graph.nodes[{index}]")
        node_id = node.get("id")
        name_hint = node.get("name_hint")
        if not isinstance(node_id, str) or not isinstance(name_hint, str):
            _error(
                "GRAPH_INVALID",
                "Graph node id and name_hint must be strings",
                path=f"$.graph.nodes[{index}]",
            )
        node_type = node.get("type")
        if not isinstance(node_type, dict) or (
            node_type.get("context"), node_type.get("name")
        ) not in _ALLOWED_GRAPH_NODE_TYPES:
            _error(
                "NODE_TYPE_NOT_ALLOWED",
                "Graph node type is outside the frozen allowlist",
                path=f"$.graph.nodes[{index}].type",
            )
        if node.get("parent") != "root":
            _error(
                "GRAPH_INVALID",
                "Every current graph node must be parented to the request-local root",
                path=f"$.graph.nodes[{index}].parent",
            )
        node_ids.append(node_id)
        name_hints.append(name_hint)
        parameters = node.get("parameters")
        if not isinstance(parameters, list):
            _error(
                "GRAPH_INVALID",
                "Graph node parameters must be an array",
                path=f"$.graph.nodes[{index}].parameters",
            )
        parameter_names = [
            parameter.get("name") if isinstance(parameter, dict) else None
            for parameter in parameters
        ]
        if not all(isinstance(name, str) for name in parameter_names):
            _error(
                "GRAPH_INVALID",
                "Parameter assignments must have string names",
                path=f"$.graph.nodes[{index}].parameters",
            )
        if len(parameter_names) != len(set(parameter_names)):
            _error(
                "GRAPH_INVALID",
                "Parameter names must be unique within each node",
                path=f"$.graph.nodes[{index}].parameters",
            )
        flags = node.get("flags")
        if not isinstance(flags, dict) or not isinstance(flags.get("display"), bool) or not isinstance(
            flags.get("render"), bool
        ):
            _error(
                "GRAPH_INVALID",
                "Every graph node requires boolean display and render flags",
                path=f"$.graph.nodes[{index}].flags",
            )
    if len(node_ids) != len(set(node_ids)):
        _error("GRAPH_INVALID", "Request-local node ids must be unique", path="$.graph.nodes")
    if len(name_hints) != len(set(name_hints)):
        _error("GRAPH_INVALID", "Node name hints must be unique", path="$.graph.nodes")

    node_id_set = set(node_ids)
    occupied_inputs: set[tuple[str, int]] = set()
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for index, connection in enumerate(normalized["connections"]):
        if not isinstance(connection, dict):
            _error(
                "GRAPH_INVALID",
                "Graph connection must be an object",
                path=f"$.graph.connections[{index}]",
            )
        source = connection.get("source")
        destination = connection.get("destination")
        if not isinstance(source, dict) or not isinstance(destination, dict):
            _error(
                "GRAPH_INVALID",
                "Graph connection endpoints must be objects",
                path=f"$.graph.connections[{index}]",
            )
        source_id = source.get("node")
        destination_id = destination.get("node")
        if (
            not isinstance(source_id, str)
            or not isinstance(destination_id, str)
            or not isinstance(source.get("output"), int)
            or isinstance(source.get("output"), bool)
            or not isinstance(destination.get("input"), int)
            or isinstance(destination.get("input"), bool)
        ):
            _error(
                "GRAPH_INVALID",
                "Graph endpoints require string node ids and integer socket indices",
                path=f"$.graph.connections[{index}]",
            )
        if source_id not in node_id_set or destination_id not in node_id_set:
            _error(
                "GRAPH_INVALID",
                "Graph connection contains a dangling endpoint",
                path=f"$.graph.connections[{index}]",
            )
        if source_id == destination_id:
            _error(
                "GRAPH_INVALID",
                "Graph connection cannot link a node to itself",
                path=f"$.graph.connections[{index}]",
            )
        destination_key = (destination_id, destination.get("input"))
        if destination_key in occupied_inputs:
            _error(
                "GRAPH_INVALID",
                "A destination input may have only one source",
                path=f"$.graph.connections[{index}].destination",
            )
        occupied_inputs.add(destination_key)
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
        _error("GRAPH_INVALID", "Graph connections must be acyclic", path="$.graph.connections")

    display_nodes = [node["id"] for node in nodes if node["flags"]["display"]]
    render_nodes = [node["id"] for node in nodes if node["flags"]["render"]]
    if len(display_nodes) != 1 or len(render_nodes) != 1:
        _error(
            "GRAPH_INVALID",
            "Graph must select exactly one display node and one render node",
            path="$.graph.nodes",
        )
    if display_nodes != render_nodes:
        _error(
            "GRAPH_INVALID",
            "Display and render flags must select the same node",
            path="$.graph.nodes",
        )


def graph_digest(graph: Mapping[str, Any]) -> str:
    """Validate, normalize, and hash a graph with canonical JSON UTF-8 v1."""

    validate_graph_relations(graph)
    return canonical_json_sha256(normalize_graph(graph))


def graph_side_effect_summary(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Return the closed B1 side-effect declaration for one new owned graph."""

    normalized = normalize_graph(graph)
    validate_graph_relations(normalized)
    return {
        "operation": "create_hia_owned_graph",
        "target_parent": "/obj",
        "new_container_count": 1,
        "node_count": len(normalized["nodes"]),
        "connection_count": len(normalized["connections"]),
        "undo_transaction_count": 1,
        "expected_revision_delta": 1,
        "file_write_count": 0,
    }


def approval_binding_payload(
    request: Mapping[str, Any],
    normalized_graph: Mapping[str, Any],
    canonical_graph_digest: str,
    side_effect_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the single reviewed approval payload shared by queue and executor."""

    if not isinstance(request, Mapping) or not isinstance(side_effect_summary, Mapping):
        _error("APPROVAL_MISMATCH", "Approval binding inputs must be objects")
    graph = normalize_graph(normalized_graph)
    validate_graph_relations(graph)
    actual_digest = graph_digest(graph)
    if not isinstance(canonical_graph_digest, str) or canonical_graph_digest.casefold() != actual_digest:
        _error(
            "DIGEST_MISMATCH",
            "Approval binding digest does not match the normalized graph",
            path="$.canonical_graph_digest",
        )
    expected_effects = graph_side_effect_summary(graph)
    if not _json_equal(dict(side_effect_summary), expected_effects):
        _error(
            "APPROVAL_MISMATCH",
            "Approval side-effect summary is not the closed graph summary",
            path="$.side_effect_summary",
        )
    correlation_fields = (
        "request_id",
        "thread_id",
        "turn_id",
        "hip_session_id",
        "expected_hip_fingerprint",
        "base_scene_revision",
        "idempotency_key",
        "deadline_ms",
        "permission_level",
    )
    missing = [field for field in correlation_fields if field not in request]
    if missing:
        _error(
            "APPROVAL_MISMATCH",
            "Approval request is missing correlation fields",
            fields=missing,
        )
    return {
        **{field: request[field] for field in correlation_fields},
        "canonical_graph_digest": actual_digest,
        "schema_version": graph["schema_version"],
        "context": copy.deepcopy(graph["context"]),
        "target": copy.deepcopy(graph["target"]),
        "nodes": copy.deepcopy(graph["nodes"]),
        "connections": copy.deepcopy(graph["connections"]),
        "layout": copy.deepcopy(graph["layout"]),
        "side_effect_summary": copy.deepcopy(expected_effects),
    }


def approval_binding_digest(
    request: Mapping[str, Any],
    normalized_graph: Mapping[str, Any],
    canonical_graph_digest: str,
    side_effect_summary: Mapping[str, Any],
) -> str:
    return canonical_json_sha256(
        approval_binding_payload(
            request,
            normalized_graph,
            canonical_graph_digest,
            side_effect_summary,
        )
    )


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _resolve_pointer(document: Mapping[str, Any], reference: str) -> Any:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        _error(
            "CONTRACT_INVALID",
            "Only document-local JSON Schema references are supported",
            reference=reference,
        )
    current: Any = document
    try:
        for raw_token in reference[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
    except (KeyError, IndexError, ValueError, TypeError):
        _error("CONTRACT_INVALID", "JSON Schema reference cannot be resolved", reference=reference)
    return current


def _schema_failure(path: str, keyword: str, message: str, **extra: Any) -> None:
    _error("SCHEMA_INVALID", message, path=path, keyword=keyword, **extra)


def _type_matches(value: Any, expected: str) -> bool:
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


def _matches(value: Any, schema: Any, document: Mapping[str, Any], path: str) -> bool:
    try:
        _validate(value, schema, document, path, 0)
    except ContractError as exc:
        if exc.code == "SCHEMA_INVALID":
            return False
        raise
    return True


def _validate(
    value: Any,
    schema: Any,
    document: Mapping[str, Any],
    path: str,
    reference_depth: int,
) -> None:
    if schema is True:
        return
    if schema is False:
        _schema_failure(path, "falseSchema", "Value is forbidden by the schema")
    if not isinstance(schema, dict):
        _error("CONTRACT_INVALID", "Schema node must be an object or boolean", path=path)
    if reference_depth > 64:
        _error("CONTRACT_INVALID", "Schema reference depth is excessive", path=path)
    if "$ref" in schema:
        _validate(
            value,
            _resolve_pointer(document, schema["$ref"]),
            document,
            path,
            reference_depth + 1,
        )
    if "allOf" in schema:
        for child in schema["allOf"]:
            _validate(value, child, document, path, reference_depth)
    if "oneOf" in schema:
        matches = sum(_matches(value, child, document, path) for child in schema["oneOf"])
        if matches != 1:
            _schema_failure(
                path,
                "oneOf",
                "Value must match exactly one schema branch",
                matching_branches=matches,
            )
    if "if" in schema:
        branch = schema.get("then") if _matches(value, schema["if"], document, path) else schema.get("else")
        if branch is not None:
            _validate(value, branch, document, path, reference_depth)

    if "const" in schema and not _json_equal(value, schema["const"]):
        _schema_failure(path, "const", "Value does not match the required constant")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        _schema_failure(path, "enum", "Value is outside the approved enumeration")

    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(accepted, list) or not all(isinstance(item, str) for item in accepted):
            _error("CONTRACT_INVALID", "Schema type must be a string or string array", path=path)
        if not any(_type_matches(value, item) for item in accepted):
            _schema_failure(path, "type", "Value has the wrong JSON type", expected=accepted)

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            _schema_failure(path, "required", "Required object properties are missing", properties=missing)
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in value:
                _validate(value[name], child, document, f"{path}.{name}", reference_depth)
        extras = [name for name in value if name not in properties]
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            _schema_failure(
                path,
                "additionalProperties",
                "Object contains unapproved properties",
                properties=sorted(extras),
            )
        if isinstance(additional, dict):
            for name in extras:
                _validate(value[name], additional, document, f"{path}.{name}", reference_depth)
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            _schema_failure(path, "minProperties", "Object has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            _schema_failure(path, "maxProperties", "Object has too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _schema_failure(path, "minItems", "Array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _schema_failure(path, "maxItems", "Array has too many items")
        if schema.get("uniqueItems"):
            for index, item in enumerate(value):
                if any(_json_equal(item, earlier) for earlier in value[:index]):
                    _schema_failure(path, "uniqueItems", "Array contains duplicate items")
        prefix = schema.get("prefixItems", [])
        for index, child in enumerate(prefix[: len(value)]):
            _validate(value[index], child, document, f"{path}[{index}]", reference_depth)
        if "items" in schema:
            items = schema["items"]
            start = len(prefix)
            if items is False and len(value) > start:
                _schema_failure(path, "items", "Array contains items beyond its fixed prefix")
            if items is not False:
                for index in range(start, len(value)):
                    _validate(value[index], items, document, f"{path}[{index}]", reference_depth)
        if "contains" in schema:
            count = sum(_matches(item, schema["contains"], document, f"{path}[{i}]") for i, item in enumerate(value))
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if count < minimum or (maximum is not None and count > maximum):
                _schema_failure(path, "contains", "Array contains the wrong number of matching items", matches=count)

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            _schema_failure(path, "minLength", "String is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            _schema_failure(path, "maxLength", "String is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _schema_failure(path, "pattern", "String does not match the approved grammar")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            _schema_failure(path, "type", "Number must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            _schema_failure(path, "minimum", "Number is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            _schema_failure(path, "maximum", "Number is above the maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            _schema_failure(path, "exclusiveMinimum", "Number is not above the exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            _schema_failure(path, "exclusiveMaximum", "Number is not below the exclusive maximum")


def validate_schema_instance(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    document: Mapping[str, Any] | None = None,
) -> Any:
    """Validate an instance against the frozen Draft 2020-12 keyword subset."""

    _check_json_value(instance)
    canonical_json_bytes(instance)
    if not isinstance(schema, Mapping):
        _error("CONTRACT_INVALID", "Schema document must be an object")
    root = schema if document is None else document
    _validate(instance, schema, root, "$", 0)
    return instance


def _audit_schema(schema: Any, document: Mapping[str, Any], path: str = "$") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        _error("CONTRACT_INVALID", "Schema node must be an object or boolean", path=path)
    unknown = sorted(set(schema) - _SCHEMA_KEYWORDS)
    if unknown:
        _error("CONTRACT_INVALID", "Schema uses unsupported keywords", path=path, keywords=unknown)
    if "$ref" in schema:
        _resolve_pointer(document, schema["$ref"])
    for map_name in ("$defs", "properties"):
        children = schema.get(map_name, {})
        if not isinstance(children, dict):
            _error("CONTRACT_INVALID", f"{map_name} must be an object", path=path)
        for name, child in children.items():
            _audit_schema(child, document, f"{path}.{map_name}.{name}")
    for list_name in ("prefixItems", "oneOf", "allOf"):
        children = schema.get(list_name, [])
        if not isinstance(children, list):
            _error("CONTRACT_INVALID", f"{list_name} must be an array", path=path)
        for index, child in enumerate(children):
            _audit_schema(child, document, f"{path}.{list_name}[{index}]")
    for child_name in ("additionalProperties", "items", "contains", "if", "then", "else"):
        if child_name in schema:
            _audit_schema(schema[child_name], document, f"{path}.{child_name}")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            _error("CONTRACT_INVALID", "Schema contains an invalid pattern", path=path, reason=str(exc))


class SchemaRegistry:
    """Load one internally selected, frozen P2-V contract profile.

    The default remains the Gate B1 five-tool ``0.1.0`` contract.  Callers may
    explicitly select :meth:`b2_read_only` for the Gate B2 two-tool ``0.2.0``
    capability slice.  A network request can never choose the profile.
    """

    def __init__(
        self,
        schema_root: Path | None = None,
        *,
        profile: str = _B1_FROZEN_PROFILE,
    ):
        if profile == _B1_FROZEN_PROFILE:
            schema_version = SCHEMA_VERSION
            expected_tools = EXPECTED_TOOLS
            expected_permissions = _EXPECTED_PERMISSIONS
        elif profile == _B2_READ_ONLY_PROFILE:
            schema_version = B2_SCHEMA_VERSION
            expected_tools = B2_READ_ONLY_TOOLS
            expected_permissions = _B2_READ_ONLY_PERMISSIONS
        else:
            raise ValueError("Unknown internal Houdini contract profile")

        repository_root = Path(__file__).resolve().parents[2]
        self.profile = profile
        self.schema_version = schema_version
        self._expected_tools = expected_tools
        self._expected_permissions = expected_permissions
        self.schema_root = Path(schema_root) if schema_root is not None else (
            repository_root / "schemas" / "houdini-mcp" / schema_version
        )
        manifest_path = self.schema_root / "manifest.json"
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError as exc:
            _error("CONTRACT_INVALID", "Cannot read Houdini tool manifest", path=str(manifest_path), reason=str(exc))
        manifest = strict_json_loads(manifest_bytes, str(manifest_path))
        if not isinstance(manifest, dict):
            _error("CONTRACT_INVALID", "Houdini tool manifest root must be an object")
        self._manifest = manifest
        self._tools: dict[str, dict[str, Any]] = {}
        self._input_schemas: dict[str, dict[str, Any]] = {}
        self._output_schemas: dict[str, dict[str, Any]] = {}
        self._load_manifest()
        self.tool_names = expected_tools
        self.manifest_digest = canonical_json_sha256(self._manifest)

    @classmethod
    def b2_read_only(cls, schema_root: Path | None = None) -> "SchemaRegistry":
        """Return the explicit, deny-by-default Gate B2 read-only profile."""

        return cls(schema_root, profile=_B2_READ_ONLY_PROFILE)

    def _load_manifest(self) -> None:
        if self._manifest.get("manifestVersion") != "1.0":
            _error("CONTRACT_INVALID", "Unexpected manifest version")
        if self._manifest.get("schemaVersion") != self.schema_version:
            _error("CONTRACT_INVALID", "Unexpected Houdini schema version")
        if self._manifest.get("schemaDialect") != SCHEMA_DIALECT:
            _error("CONTRACT_INVALID", "Unexpected Houdini schema dialect")
        if self._manifest.get("schemaDigestEncoding") != "canonical-json-utf8-v1":
            _error("CONTRACT_INVALID", "Unexpected Houdini schema digest encoding")
        if self._manifest.get("contractStatus") != "frozen_pre_release":
            _error("CONTRACT_INVALID", "Houdini contract is not frozen pre-release")
        if self._manifest.get("protocolPolicy") != "deny_by_default":
            _error("CONTRACT_INVALID", "Houdini protocol policy must deny by default")
        if self._manifest.get("activeContexts") != ["Object", "Sop"]:
            _error("CONTRACT_INVALID", "Unexpected active Houdini contexts")
        tools = self._manifest.get("tools")
        if not isinstance(tools, list) or tuple(
            tool.get("name") for tool in tools if isinstance(tool, dict)
        ) != self._expected_tools:
            _error(
                "CONTRACT_INVALID",
                "Manifest must contain the exact ordered profile tool allowlist",
                profile=self.profile,
            )
        referenced: set[str] = set()
        for tool in tools:
            name = tool["name"]
            if tool.get("permissionLevel") != self._expected_permissions[name]:
                _error("CONTRACT_INVALID", "Tool permission does not match the frozen policy", tool=name)
            for direction in ("input", "output"):
                file_name = tool.get(f"{direction}Schema")
                digest = tool.get(f"{direction}Sha256")
                if not isinstance(file_name, str) or PurePosixPath(file_name).name != file_name or "\\" in file_name or ":" in file_name:
                    _error("CONTRACT_INVALID", "Schema filename is not a safe leaf name", tool=name, direction=direction)
                if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                    _error("CONTRACT_INVALID", "Schema digest is malformed", tool=name, direction=direction)
                path = self.schema_root / file_name
                try:
                    raw = path.read_bytes()
                except OSError as exc:
                    _error("CONTRACT_INVALID", "Cannot read a frozen tool schema", tool=name, direction=direction, reason=str(exc))
                schema = strict_json_loads(raw, str(path))
                if not isinstance(schema, dict):
                    _error("CONTRACT_INVALID", "Tool schema root must be an object", tool=name, direction=direction)
                actual = canonical_json_sha256(schema)
                if actual != digest:
                    _error("SCHEMA_HASH_MISMATCH", "Frozen tool schema hash does not match the manifest", tool=name, direction=direction)
                if schema.get("$schema") != SCHEMA_DIALECT:
                    _error("CONTRACT_INVALID", "Tool schema dialect does not match", tool=name, direction=direction)
                expected_id = (
                    f"urn:hia:houdini-mcp:{self.schema_version}:{name}:{direction}"
                )
                if schema.get("$id") != expected_id:
                    _error("CONTRACT_INVALID", "Tool schema identifier does not match", tool=name, direction=direction)
                _audit_schema(schema, schema)
                referenced.add(file_name)
                target = self._input_schemas if direction == "input" else self._output_schemas
                target[name] = schema
            self._tools[name] = copy.deepcopy(tool)
        try:
            present = {path.name for path in self.schema_root.glob("*.schema.json") if path.is_file()}
        except OSError as exc:
            _error("CONTRACT_INVALID", "Cannot inventory frozen tool schemas", reason=str(exc))
        if present != referenced:
            _error("CONTRACT_INVALID", "Schema directory differs from the frozen manifest inventory")

    def _tool(self, name: str) -> dict[str, Any]:
        if name not in self._tools:
            _error("TOOL_NOT_ALLOWED", "Tool is outside the exact P2-V allowlist", tool=name if isinstance(name, str) else "<invalid>")
        return self._tools[name]

    def permission_level(self, name: str) -> str:
        return self._tool(name)["permissionLevel"]

    def get_input_schema(self, name: str) -> dict[str, Any]:
        self._tool(name)
        return copy.deepcopy(self._input_schemas[name])

    def get_output_schema(self, name: str) -> dict[str, Any]:
        self._tool(name)
        return copy.deepcopy(self._output_schemas[name])

    def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
        descriptors = []
        for name in self.tool_names:
            tool = self._tools[name]
            descriptors.append(
                {
                    "name": name,
                    "description": tool["description"],
                    "inputSchema": copy.deepcopy(self._input_schemas[name]),
                    "annotations": copy.deepcopy(tool["annotations"]),
                }
            )
        return tuple(descriptors)

    def validate_input(self, name: str, arguments: Any) -> Any:
        self._tool(name)
        validate_schema_instance(arguments, self._input_schemas[name])
        if name in ("houdini_graph_validate", "houdini_graph_apply"):
            validate_graph_relations(arguments["graph"])
        if name == "houdini_graph_apply":
            actual_digest = graph_digest(arguments["graph"])
            if arguments["canonical_graph_digest"].casefold() != actual_digest:
                _error(
                    "DIGEST_MISMATCH",
                    "Claimed graph digest does not match the normalized graph",
                    path="$.canonical_graph_digest",
                )
        return arguments

    def validate_output(self, name: str, request: Any, result: Any) -> Any:
        self._tool(name)
        validate_schema_instance(request, self._input_schemas[name])
        validate_schema_instance(result, self._output_schemas[name])
        self._validate_common_output(request, result)
        if name != "houdini_graph_apply" and result["scene_revision"] != request["base_scene_revision"]:
            self._mismatch(
                "A read-only tool must describe the exact requested scene revision",
                "$.scene_revision",
            )
        if name == "houdini_scene_info":
            self._validate_scene_info(request, result)
        elif name == "houdini_node_type_info":
            self._validate_node_types(request, result)
        elif name == "houdini_graph_validate":
            self._validate_graph_validate(request, result)
        elif name == "houdini_graph_apply":
            self._validate_apply(request, result)
        elif name == "houdini_graph_verify":
            self._validate_verify(request, result)
        return result

    def make_error_output(
        self,
        name: str,
        request: Mapping[str, Any],
        code: str,
        message: str,
        *,
        retryable: bool = False,
        scene_revision: int | None = None,
    ) -> dict[str, Any]:
        """Create a schema-valid failure when the tool contract admits ``code``."""

        self.validate_input(name, request)
        output_schema = self._output_schemas[name]
        admitted = output_schema["$defs"]["error"]["properties"]["code"]["enum"]
        if code not in admitted:
            _error(
                "CONTRACT_MISMATCH",
                "Failure code is not admitted by this tool output",
                tool=name,
                code=code,
            )
        revision = request["base_scene_revision"] if scene_revision is None else scene_revision
        result: dict[str, Any] = {
            "ok": False,
            "request_id": request["request_id"],
            "thread_id": request["thread_id"],
            "turn_id": request["turn_id"],
            "hip_session_id": request["hip_session_id"],
            "base_scene_revision": request["base_scene_revision"],
            "scene_revision": revision,
            "idempotency_key": request["idempotency_key"],
            "result": None,
            "warnings": [],
            "structured_error": {
                "code": code,
                "message": message,
                "details": [{"key": "retryable", "value": retryable}],
            },
        }
        return self.validate_output(name, request, result)

    def make_replay_output(
        self,
        name: str,
        request: Mapping[str, Any],
        original_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return the reviewed replay shape without invoking an executor again."""

        original = copy.deepcopy(dict(original_result))
        self.validate_output(name, request, original)
        if name != "houdini_graph_apply" or not original["ok"]:
            return original
        original["result"]["replay"] = True
        return self.validate_output(name, request, original)

    @staticmethod
    def _mismatch(message: str, path: str, **details: Any) -> None:
        _error("CONTRACT_MISMATCH", message, path=path, **details)

    def _validate_common_output(self, request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        for field in (
            "request_id",
            "thread_id",
            "turn_id",
            "hip_session_id",
            "base_scene_revision",
            "idempotency_key",
        ):
            if result[field] != request[field]:
                self._mismatch("Result correlation does not echo its request", f"$.{field}", field=field)
        if result["scene_revision"] < result["base_scene_revision"]:
            self._mismatch("Scene revision regressed", "$.scene_revision")

    def _validate_scene_info(
        self, request: Mapping[str, Any], result: Mapping[str, Any] | None = None
    ) -> None:
        # Keep compatibility with the pre-correction private call shape while
        # validating the public request whenever invoked by validate_output.
        if result is None:
            result = request
            request = {"include_graph_summaries": True}
        scene = result["result"]
        if scene is None:
            return
        graphs = scene["hia_graphs"]
        roots = [graph["root_path"] for graph in graphs]
        if len(roots) != len(set(roots)):
            self._mismatch(
                "Scene graph summaries must have unique HIA-owned roots",
                "$.result.hia_graphs",
            )
        if not request["include_graph_summaries"] and graphs:
            self._mismatch(
                "Graph summaries were returned when the caller disabled them",
                "$.result.hia_graphs",
            )

    def _validate_node_types(self, request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        if not result["ok"]:
            return
        requested = {(item["context"], item["name"]) for item in request["node_types"]}
        actual = [
            (item["context"], item["requested_name"])
            for item in result["result"]["node_types"]
        ]
        if len(actual) != len(set(actual)) or set(actual) != requested:
            self._mismatch(
                "Node-type results must uniquely and exactly match the submitted queries",
                "$.result.node_types",
            )
        for node_index, node_type in enumerate(result["result"]["node_types"]):
            path = f"$.result.node_types[{node_index}]"
            if node_type["available"] != (node_type["resolved_name"] is not None):
                self._mismatch("Node availability contradicts its resolved name", path)
            if not node_type["available"] and (
                node_type["creatable"] or node_type["parameters"]
            ):
                self._mismatch("Unavailable node types cannot be creatable or expose parameters", path)
            parameter_names = [item["name"] for item in node_type["parameters"]]
            if len(parameter_names) != len(set(parameter_names)):
                self._mismatch("Live parameter names must be unique", f"{path}.parameters")
            for parameter_index, parameter in enumerate(node_type["parameters"]):
                default = parameter["default_value"]
                parameter_path = f"{path}.parameters[{parameter_index}]"
                value_type = parameter["value_type"]
                if default is not None:
                    if value_type == "tuple":
                        if (
                            default["type"] != "tuple"
                            or len(default["value"]) != parameter["tuple_size"]
                        ):
                            self._mismatch(
                                "Tuple default contradicts live tuple metadata",
                                parameter_path,
                            )
                    elif default["type"] != value_type or parameter["tuple_size"] != 1:
                        self._mismatch(
                            "Scalar default contradicts live parameter metadata",
                            parameter_path,
                        )
                if self.profile == _B2_READ_ONLY_PROFILE:
                    if node_type["creatable"] is not False or parameter["writable"] is not False:
                        self._mismatch(
                            "Gate B2 node metadata cannot advertise write capability",
                            parameter_path,
                        )
                    numeric_range = parameter["numeric_range"]
                    numeric_type = value_type in {"float", "int"}
                    if value_type == "tuple" and default is not None:
                        numeric_type = default.get("items_type") in {"float", "int"}
                    if numeric_type and numeric_range is None:
                        self._mismatch(
                            "Numeric parameter metadata requires a bounded live range",
                            f"{parameter_path}.numeric_range",
                        )
                    if not numeric_type and numeric_range is not None:
                        self._mismatch(
                            "Non-numeric parameter metadata cannot advertise a numeric range",
                            f"{parameter_path}.numeric_range",
                        )
                    if numeric_range is not None:
                        minimum = numeric_range["min_value"]
                        maximum = numeric_range["max_value"]
                        if minimum > maximum:
                            self._mismatch(
                                "Numeric parameter range is inverted",
                                f"{parameter_path}.numeric_range",
                            )
                        integer_items = value_type == "int" or (
                            value_type == "tuple"
                            and default is not None
                            and default.get("items_type") == "int"
                        )
                        if integer_items and (
                            isinstance(minimum, bool)
                            or isinstance(maximum, bool)
                            or not isinstance(minimum, int)
                            or not isinstance(maximum, int)
                        ):
                            self._mismatch(
                                "Integer parameter ranges must use integer bounds",
                                f"{parameter_path}.numeric_range",
                            )

    @staticmethod
    def _graph_summary(graph: Mapping[str, Any]) -> dict[str, Any]:
        counts: dict[tuple[str, str], int] = {}
        display_node_id = None
        render_node_id = None
        for node in graph["nodes"]:
            key = (node["type"]["context"], node["type"]["name"])
            counts[key] = counts.get(key, 0) + 1
            if node["flags"]["display"]:
                display_node_id = node["id"]
            if node["flags"]["render"]:
                render_node_id = node["id"]
        return {
            "node_count": len(graph["nodes"]),
            "connection_count": len(graph["connections"]),
            "type_counts": [
                {"context": context, "name": name, "count": count}
                for (context, name), count in sorted(counts.items())
            ],
            "display_node_id": display_node_id,
            "render_node_id": render_node_id,
        }

    def _validate_graph_validate(
        self, request: Mapping[str, Any], result: Mapping[str, Any]
    ) -> None:
        if not result["ok"]:
            return
        validation = result["result"]
        normalized = normalize_graph(request["graph"])
        validate_graph_relations(normalized)
        digest = graph_digest(normalized)
        if not validation["valid"]:
            self._mismatch("A successfully admitted graph must validate", "$.result.valid")
        if any(issue["severity"] == "error" for issue in validation["issues"]):
            self._mismatch("A valid graph cannot contain error issues", "$.result.issues")
        if not _json_equal(validation["normalized_graph"], normalized):
            self._mismatch(
                "Validate result does not return the deterministic normalized graph",
                "$.result.normalized_graph",
            )
        if validation["canonical_graph_digest"].casefold() != digest:
            self._mismatch(
                "Validate digest does not match the normalized graph",
                "$.result.canonical_graph_digest",
            )
        side_effects = graph_side_effect_summary(normalized)
        expected_approval = approval_binding_digest(
            request,
            normalized,
            digest,
            side_effects,
        )
        if validation["approval_binding_digest"].casefold() != expected_approval:
            self._mismatch(
                "Validate approval digest does not bind the complete request",
                "$.result.approval_binding_digest",
            )
        if not _json_equal(validation["summary"], self._graph_summary(normalized)):
            self._mismatch("Validate summary contradicts the graph", "$.result.summary")

    def _validate_apply(self, request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        if not result["ok"]:
            return
        applied = result["result"]
        normalized = normalize_graph(request["graph"])
        digest = graph_digest(normalized)
        root_path = f"/obj/{normalized['target']['name_hint']}"
        if applied["root_path"] != root_path:
            self._mismatch("Apply root does not match the requested target", "$.result.root_path")
        if applied["canonical_graph_digest"].casefold() != digest:
            self._mismatch("Apply digest contradicts the normalized graph", "$.result.canonical_graph_digest")
        expected_approval = approval_binding_digest(
            request,
            normalized,
            digest,
            graph_side_effect_summary(normalized),
        )
        if applied["approval_binding_digest"].casefold() != expected_approval:
            self._mismatch(
                "Apply approval digest does not bind the complete request",
                "$.result.approval_binding_digest",
            )
        if applied["revision_before"] != request["base_scene_revision"]:
            self._mismatch("Apply revision_before does not match the request", "$.result.revision_before")
        if applied["revision_after"] != request["base_scene_revision"] + 1:
            self._mismatch("Apply must advance the revision exactly once", "$.result.revision_after")
        if result["scene_revision"] != applied["revision_after"]:
            self._mismatch("Top-level revision contradicts apply result", "$.scene_revision")

        expected_nodes = {
            "root": (root_path, "Object", "geo"),
            **{
                node["id"]: (
                    f"{root_path}/{node['name_hint']}",
                    node["type"]["context"],
                    node["type"]["name"],
                )
                for node in normalized["nodes"]
            },
        }
        actual_nodes: dict[str, tuple[str, str, str]] = {}
        for index, created in enumerate(applied["created_nodes"]):
            local_id = created["request_local_id"]
            if local_id in actual_nodes:
                self._mismatch("Created node ids must be unique", f"$.result.created_nodes[{index}]")
            actual_nodes[local_id] = (
                created["path"],
                created["context"],
                created["resolved_type"],
            )
        if actual_nodes != expected_nodes:
            self._mismatch(
                "Apply result must report the exact resolved graph",
                "$.result.created_nodes",
            )
        created_paths = {value[0] for value in expected_nodes.values()}
        changed_paths = applied["changed_nodes"]
        if len(changed_paths) != len(set(changed_paths)) or not set(changed_paths).issubset(created_paths):
            self._mismatch(
                "Apply changed_nodes must be unique and remain inside created scope",
                "$.result.changed_nodes",
            )
        undo = applied["undo_transaction"]
        if not undo["opened"] or not undo["committed"]:
            self._mismatch("Successful apply must report one committed Undo transaction", "$.result.undo_transaction")
        rollback = applied["rollback"]
        if rollback["attempted"] or not rollback["complete"] or rollback["retained_paths"]:
            self._mismatch("Successful apply cannot retain rollback residue", "$.result.rollback")

    def _validate_verify(self, request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        if not result["ok"]:
            return
        verification = result["result"]
        if verification["root_path"] != request["root_path"]:
            self._mismatch("Verification root does not match the request", "$.result.root_path")
        if verification["expected_graph_digest"].casefold() != request["expected_graph_digest"].casefold():
            self._mismatch("Verification expected digest does not echo the request", "$.result.expected_graph_digest")
        expected_match = (
            verification["observed_graph_digest"].casefold()
            == request["expected_graph_digest"].casefold()
        )
        if verification["digest_matches"] != expected_match:
            self._mismatch("Verification digest status contradicts its digests", "$.result.digest_matches")

        expected_check_names = {
            "session",
            "revision",
            "target",
            "ownership",
            "nodes",
            "parameters",
            "connections",
            "flags",
            "cook",
            "graph_digest",
        }
        checks = verification["checks"]
        check_names = [check["name"] for check in checks]
        if len(check_names) != len(set(check_names)) or set(check_names) != expected_check_names:
            self._mismatch("Verification must report each frozen check exactly once", "$.result.checks")
        checks_by_name = {check["name"]: check for check in checks}
        if checks_by_name["graph_digest"]["passed"] != expected_match:
            self._mismatch(
                "Graph-digest check contradicts the observed and expected digests",
                "$.result.checks",
            )
        expected_valid = all(check["passed"] for check in checks) and expected_match
        if verification["valid"] != expected_valid:
            self._mismatch("Overall verification status contradicts its checks", "$.result.valid")

        node_ids: set[str] = set()
        node_paths: set[str] = set()
        display_nodes: list[str] = []
        render_nodes: list[str] = []
        for index, node in enumerate(verification["nodes"]):
            node_id = node["request_local_id"]
            path = node["path"]
            if node_id in node_ids or path in node_paths:
                self._mismatch("Observed nodes must have unique ids and paths", f"$.result.nodes[{index}]")
            if not path.startswith(request["root_path"] + "/"):
                self._mismatch("Observed node escapes the verified root", f"$.result.nodes[{index}].path")
            node_ids.add(node_id)
            node_paths.add(path)
            parameter_names = [parameter["name"] for parameter in node["parameters"]]
            if len(parameter_names) != len(set(parameter_names)):
                self._mismatch("Observed parameter names must be unique", f"$.result.nodes[{index}].parameters")
            if node["flags"]["display"]:
                display_nodes.append(node_id)
            if node["flags"]["render"]:
                render_nodes.append(node_id)
        if len(display_nodes) != 1 or display_nodes != render_nodes:
            self._mismatch("Observed display/render flags must select one shared node", "$.result.nodes")
        all_nodes_cooked_cleanly = all(
            node["cook_state"] == "clean" for node in verification["nodes"]
        )
        if checks_by_name["cook"]["passed"] != all_nodes_cooked_cleanly:
            self._mismatch(
                "Cook check contradicts the observed node cook states",
                "$.result.checks",
            )

        destinations: set[tuple[str, int]] = set()
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        indegree = {node_id: 0 for node_id in node_ids}
        for index, connection in enumerate(verification["connections"]):
            source = connection["source"]
            destination = connection["destination"]
            source_id = source["node"]
            destination_id = destination["node"]
            if source_id not in node_ids or destination_id not in node_ids or source_id == destination_id:
                self._mismatch("Observed connection endpoint is invalid", f"$.result.connections[{index}]")
            destination_key = (destination_id, destination["index"])
            if destination_key in destinations:
                self._mismatch("Observed destination input has multiple sources", f"$.result.connections[{index}]")
            destinations.add(destination_key)
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
            self._mismatch("Observed graph must remain acyclic", "$.result.connections")


__all__ = [
    "B2_READ_ONLY_TOOLS",
    "B2_SCHEMA_VERSION",
    "ContractError",
    "EXPECTED_TOOLS",
    "MAX_JSON_BYTES",
    "MAX_JSON_DEPTH",
    "SCHEMA_DIALECT",
    "SCHEMA_VERSION",
    "SchemaRegistry",
    "approval_binding_digest",
    "approval_binding_payload",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "graph_digest",
    "graph_side_effect_summary",
    "normalize_graph",
    "strict_json_loads",
    "validate_graph_relations",
    "validate_schema_instance",
]
