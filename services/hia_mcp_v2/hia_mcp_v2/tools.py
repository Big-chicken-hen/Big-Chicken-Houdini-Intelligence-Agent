"""Capability-led tool registry for HIA MCP V2.

The registry intentionally exposes broad, batch-oriented operations.  Houdini
node types are discovered from the live installation; none are allowlisted
here.  Codex remains responsible for understanding the user's intent and for
generating HOM Python.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import InputError


def _object(
    properties: Mapping[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties or {}),
    }
    if required:
        schema["required"] = list(required)
    return schema


STRING = {"type": "string", "maxLength": 4096}
PATH = {"type": "string", "maxLength": 4096}
PATHS = {"type": "array", "items": PATH, "maxItems": 64}
QUERY = {"type": "string", "maxLength": 512}
QUERIES = {"type": "array", "items": QUERY, "minItems": 1, "maxItems": 16}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 1_000_000, "default": 0}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
RETRIEVAL_MODE = {
    "type": "string",
    "enum": ["lexical", "vector", "hybrid"],
    "default": "hybrid",
}

NODE_HELP_PROPERTIES = {
    "node_path": PATH,
    "category": STRING,
    "node_type": STRING,
    "include_parameters": {"type": "boolean", "default": True},
    "parameter_query": QUERY,
    "offset": OFFSET,
    "limit": LIMIT,
}

PROJECT_MEMORY_PROPERTIES = {
    "action": {
        "type": "string",
        "enum": ["record", "search", "list", "delete", "supersede"],
    },
    "memory_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "memory_type": {
        "type": "string",
        "enum": ["decision", "preference", "asset", "lesson", "workflow"],
    },
    "title": {"type": "string", "minLength": 1, "maxLength": 512},
    "body": {"type": "string", "minLength": 1, "maxLength": 65_536},
    "tags": {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 128},
        "maxItems": 32,
    },
    "scope": {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
        "default": "project",
    },
    "source_thread_id": {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
    },
    "source_turn_id": {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
    },
    "query": {"type": "string", "minLength": 2, "maxLength": 256},
    "mode": RETRIEVAL_MODE,
    "include_superseded": {"type": "boolean", "default": False},
    "offset": OFFSET,
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
}

_PROJECT_MEMORY_ACTION_FIELDS = {
    "record": frozenset(
        {
            "action",
            "memory_type",
            "title",
            "body",
            "tags",
            "scope",
            "source_thread_id",
            "source_turn_id",
        }
    ),
    "search": frozenset(
        {
            "action",
            "query",
            "memory_type",
            "tags",
            "scope",
            "mode",
            "include_superseded",
            "offset",
            "limit",
        }
    ),
    "list": frozenset(
        {
            "action",
            "memory_type",
            "tags",
            "scope",
            "include_superseded",
            "offset",
            "limit",
        }
    ),
    "delete": frozenset({"action", "memory_id"}),
    "supersede": frozenset(
        {
            "action",
            "memory_id",
            "memory_type",
            "title",
            "body",
            "tags",
            "scope",
            "source_thread_id",
            "source_turn_id",
        }
    ),
}

_PROJECT_MEMORY_REQUIRED_FIELDS = {
    "record": frozenset({"action", "memory_type", "title", "body"}),
    "search": frozenset({"action", "query"}),
    "list": frozenset({"action"}),
    "delete": frozenset({"action", "memory_id"}),
    "supersede": frozenset(
        {"action", "memory_id", "memory_type", "title", "body"}
    ),
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    description: str
    input_schema: Mapping[str, Any]
    read_only: bool = True

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": copy.deepcopy(dict(self.input_schema)),
            "outputSchema": {
                "type": "object",
                "additionalProperties": True,
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            },
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": not self.read_only,
                "idempotentHint": self.read_only,
                "openWorldHint": False,
            },
        }


TOOL_SPECS = (
    ToolSpec(
        "hia_search_capabilities",
        "discovery",
        "Discover the HIA Houdini capability matrix by task or domain. Use this when unsure which high-signal tool to call; prefer one batch hia_execute_hom call for complex edits.",
        _object({"query": QUERY, "domain": QUERY, "offset": OFFSET, "limit": LIMIT}),
    ),
    ToolSpec(
        "hia_context",
        "scene_perception",
        "Read the live Houdini build, HIP, frame/FPS, take, dirty state, current network/node, selection, scene revision, Goal focus recovery mode, installed contexts, and an optional bounded graph overview.",
        _object(
            {
                "include_graph": {"type": "boolean", "default": False},
                "graph_depth": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_inspect",
        "scene_perception",
        "Inspect paths or the current selection in one bounded call: types, parameters, inputs/outputs, flags, errors, geometry hints, and a finite child graph. Use filters instead of dumping the scene.",
        _object(
            {
                "paths": PATHS,
                "use_selection": {"type": "boolean", "default": True},
                "depth": {"type": "integer", "minimum": 0, "maximum": 3, "default": 0},
                "views": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["parameters", "connections", "flags", "errors", "geometry", "children"],
                    },
                    "maxItems": 6,
                },
                "query": QUERY,
                "offset": OFFSET,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_scene_graph",
        "scene_perception",
        "Read a filtered, paginated node graph and dependency edges below one network. Supports OBJ, SOP, DOP, LOP, VOP/MaterialX, ROP, CHOP, COP, and TOP networks without a node allowlist.",
        _object(
            {
                "root_path": PATH,
                "query": QUERY,
                "depth": {"type": "integer", "minimum": 0, "maximum": 6, "default": 2},
                "include_dependencies": {"type": "boolean", "default": True},
                "offset": OFFSET,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_search_node_types",
        "dynamic_node_knowledge",
        "Search node types actually installed in the current Houdini build across any context, including versioned names. Prefer one queries batch for several keywords, then reuse its merged results; query remains the compatible single-query form. Wait for the result instead of fanning out parallel searches or blindly retrying. Results are filtered and paginated; there is no static catalog or node-type allowlist.",
        _object(
            {
                "query": QUERY,
                "queries": QUERIES,
                "contexts": {"type": "array", "items": STRING, "maxItems": 32},
                "include_deprecated": {"type": "boolean", "default": False},
                "offset": OFFSET,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_node_help",
        "dynamic_node_knowledge",
        "Resolve installed Houdini help. Use requests to batch several targets, or the compatible single-target form with node_path, category plus a bare node_type, or node_type=\"Category/name\". Returns the real versioned name, context, input rules, parameter templates, definition/source hints, and installed help metadata.",
        _object(
            {
                **NODE_HELP_PROPERTIES,
                "requests": {
                    "type": "array",
                    "items": _object(NODE_HELP_PROPERTIES),
                    "minItems": 1,
                    "maxItems": 16,
                },
            }
        ),
    ),
    ToolSpec(
        "hia_geometry_summary",
        "geometry_understanding",
        "Summarize geometry for several nodes: bounds, point/vertex/primitive counts, groups, attributes, primitive kinds, packed data, volumes, instances, topology hints, and cook errors.",
        _object(
            {
                "paths": PATHS,
                "use_selection": {"type": "boolean", "default": True},
                "include_attributes": {"type": "boolean", "default": True},
                "sample_limit": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_material_render_summary",
        "material_render_understanding",
        "Inspect material/VOP/MaterialX networks, material bindings, texture/file references, ROP/Karma settings, render dependencies, and errors in one filtered summary.",
        _object({"root_paths": PATHS, "query": QUERY, "offset": OFFSET, "limit": LIMIT}),
    ),
    ToolSpec(
        "hia_solaris_summary",
        "solaris_usd_understanding",
        "Inspect a LOP node's composed USD stage with filtered prims, types, activity, variants, material bindings, layers, and stage/cook errors. Results are paginated.",
        _object(
            {
                "lop_path": PATH,
                "prim_path": PATH,
                "query": QUERY,
                "offset": OFFSET,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_animation_summary",
        "animation_understanding",
        "Summarize animated parameters, keyframes, channels, expressions, time dependencies, frame ranges, and takes for paths or the current selection.",
        _object(
            {
                "paths": PATHS,
                "use_selection": {"type": "boolean", "default": True},
                "include_static": {"type": "boolean", "default": False},
                "offset": OFFSET,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_simulation_summary",
        "simulation_understanding",
        "Summarize DOP/Vellum/Pyro/FLIP/RBD and related cache networks, time dependence, cache paths/status, cook state, memory hints, and errors without assuming fixed node types.",
        _object({"root_paths": PATHS, "query": QUERY, "offset": OFFSET, "limit": LIMIT}),
    ),
    ToolSpec(
        "hia_validate",
        "debug_validation",
        "Validate target paths or a bounded network: missing inputs, node/cook errors, warnings, invalid references, geometry availability, and whether expected paths exist. Cooking is opt-in.",
        _object(
            {
                "paths": PATHS,
                "root_path": PATH,
                "cook": {"type": "boolean", "default": False},
                "expected_paths": PATHS,
                "query": QUERY,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_execute_hom",
        "hom_execution",
        "Execute one Codex-generated Python/HOM batch in the current Houdini UI main thread. Default diffing is targeted: predeclare exact diff_paths or call hia_mark_changed(path) before the first edit; only an explicit diff_root_path expands to a bounded network scan. timeout_seconds is a client wait budget, not a HOM kill deadline; a timeout after network I/O begins may have unknown execution state and must not be retried automatically. An optional checkpoint_label saves one Houdini backup only after a confirmed successful change.",
        _object(
            {
                "script": {"type": "string", "minLength": 1, "maxLength": 524_288},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 60},
                "capture_diff": {"type": "boolean", "default": True},
                "diff_paths": PATHS,
                "diff_root_path": PATH,
                "checkpoint_label": {"type": "string", "maxLength": 128},
            },
            required=("script",),
        ),
        read_only=False,
    ),
    ToolSpec(
        "hia_scene_diff",
        "debug_validation",
        "Capture, compare, list, or forget bounded scene snapshots to verify execution effects. Snapshots contain structural fingerprints, not HIP copies.",
        _object(
            {
                "action": {"type": "string", "enum": ["capture", "compare", "list", "forget"]},
                "snapshot_id": {"type": "string", "maxLength": 128},
                "root_path": PATH,
                "limit": LIMIT,
            },
            required=("action",),
        ),
    ),
    ToolSpec(
        "hia_capture_viewport",
        "visual_feedback",
        "Capture the current viewport or a bounded flipbook only when visual verification is needed. Low-resolution flipbooks default to 640 x 360. Use a same-frame flipbook for stage previews and selected key frames for animation or simulation; a flipbook range may span at most 240 frames. Restores the original camera/view, camera lock, and frame state, does not open MPlay or take focus, and returns dimensions read from the produced PNG. Images stay under HIA_CACHE_DIR/screenshots.",
        _object(
            {
                "mode": {"type": "string", "enum": ["viewport", "flipbook"], "default": "viewport"},
                "camera_path": PATH,
                "frame_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Start and end frames for flipbook capture. The runtime rejects reversed ranges and spans over 240 frames; prefer same-frame milestone previews or selected key frames.",
                },
                "width": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 640},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 360},
                "return_image": {"type": "boolean", "default": True},
            }
        ),
    ),
    ToolSpec(
        "hia_local_help_search",
        "local_documentation",
        "Search project-local Houdini help, published project references, user-authorized documents, and explicit project memories with SQLite FTS5 plus optional local Qwen embeddings. Qwen only encodes text; Codex remains the sole reasoning system. Hybrid is the default and degrades completely to lexical search when embeddings are unavailable. While the vector index is partial, semantic scoring only reranks each query's own lexical candidates; global semantic recall starts only after the index reports complete. Prefer one queries batch so refreshable sources are scanned once. Results preserve provenance and verification metadata and report requested/active embedding profiles plus any degradation reason. Index work runs outside the Houdini UI thread; web research remains Codex's responsibility.",
        _object(
            {
                "query": {"type": "string", "minLength": 2, "maxLength": 256},
                "queries": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 2, "maxLength": 256},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["houdini", "project", "user", "memory"],
                    },
                    "maxItems": 4,
                },
                "mode": RETRIEVAL_MODE,
                "offset": OFFSET,
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force an immediate incremental source refresh before searching.",
                },
            },
        ),
    ),
    ToolSpec(
        "hia_project_memory",
        "project_memory",
        "Explicitly record, search, list, delete, or supersede durable project decisions, preferences, assets, lessons, and workflows. This is not chat history: nothing is saved automatically, and Codex supplies the final memory text. Search defaults to hybrid local retrieval with lexical fallback; Qwen only encodes text. Results report requested/active embedding profiles and any degradation reason. The runtime generates stable IDs and keeps bodies and vectors under project .runtime/knowledge.",
        _object(PROJECT_MEMORY_PROPERTIES, required=("action",)),
        read_only=False,
    ),
)

TOOL_NAMES = tuple(spec.name for spec in TOOL_SPECS)
TOOL_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}

CAPABILITY_MATRIX = (
    {"domain": "discovery", "tools": ["hia_search_capabilities"], "status": "implemented"},
    {"domain": "scene_perception", "tools": ["hia_context", "hia_inspect", "hia_scene_graph"], "status": "implemented"},
    {"domain": "dynamic_node_knowledge", "tools": ["hia_search_node_types", "hia_node_help"], "status": "implemented"},
    {"domain": "geometry_understanding", "tools": ["hia_geometry_summary"], "status": "implemented"},
    {"domain": "material_render_understanding", "tools": ["hia_material_render_summary"], "status": "implemented"},
    {"domain": "solaris_usd_understanding", "tools": ["hia_solaris_summary"], "status": "implemented"},
    {"domain": "animation_understanding", "tools": ["hia_animation_summary"], "status": "implemented"},
    {"domain": "simulation_understanding", "tools": ["hia_simulation_summary"], "status": "implemented"},
    {"domain": "hom_execution", "tools": ["hia_execute_hom"], "status": "implemented"},
    {"domain": "visual_feedback", "tools": ["hia_capture_viewport"], "status": "implemented"},
    {"domain": "debug_validation", "tools": ["hia_validate", "hia_scene_diff"], "status": "implemented"},
    {"domain": "local_documentation", "tools": ["hia_local_help_search"], "status": "implemented"},
    {"domain": "project_memory", "tools": ["hia_project_memory"], "status": "implemented"},
    {
        "domain": "long_jobs",
        "tools": [],
        "status": "deferred_until_real_render_or_cache_need",
        "note": "Synchronous HOM reports its cancellation limit honestly; no scheduler is created in V2 core.",
    },
)


def descriptors() -> list[dict[str, Any]]:
    return [spec.descriptor() for spec in TOOL_SPECS]


def validate_input(tool_name: str, arguments: Mapping[str, Any]) -> None:
    spec = TOOL_BY_NAME.get(tool_name)
    if spec is None:
        raise InputError("TOOL_NOT_FOUND", "Unknown HIA MCP V2 tool", {"tool": tool_name})
    if not isinstance(arguments, Mapping):
        raise InputError("INVALID_ARGUMENTS", "Tool arguments must be an object")
    _validate_schema(dict(arguments), spec.input_schema, path="arguments")
    if tool_name in {"hia_search_node_types", "hia_local_help_search"}:
        if "query" in arguments and "queries" in arguments:
            raise InputError(
                "INVALID_ARGUMENTS",
                "Provide query or queries, not both",
            )
        if tool_name == "hia_local_help_search" and not (
            "query" in arguments or "queries" in arguments
        ):
            raise InputError(
                "INVALID_ARGUMENTS",
                "Provide query or queries",
            )
    if tool_name == "hia_node_help" and "requests" in arguments:
        if set(arguments) != {"requests"}:
            raise InputError(
                "INVALID_ARGUMENTS",
                "Batch node help options belong inside each requests item",
            )
    if tool_name == "hia_project_memory":
        _validate_project_memory(arguments)


def _validate_project_memory(arguments: Mapping[str, Any]) -> None:
    action = str(arguments["action"])
    required = _PROJECT_MEMORY_REQUIRED_FIELDS[action]
    missing = sorted(required.difference(arguments))
    if missing:
        raise InputError(
            "INVALID_ARGUMENTS",
            f"hia_project_memory action {action} is missing required fields",
            {"action": action, "missing": missing},
        )
    unrelated = sorted(set(arguments).difference(_PROJECT_MEMORY_ACTION_FIELDS[action]))
    if unrelated:
        raise InputError(
            "INVALID_ARGUMENTS",
            f"hia_project_memory action {action} contains unrelated fields",
            {"action": action, "fields": unrelated},
        )
    for field in required:
        value = arguments[field]
        if isinstance(value, str) and not value.strip():
            raise InputError(
                "INVALID_ARGUMENTS",
                f"arguments.{field} must not be blank",
            )


def _validate_schema(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise InputError("INVALID_ARGUMENTS", f"{path} must be an object")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise InputError("INVALID_ARGUMENTS", f"{path} is missing required fields", {"missing": missing})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise InputError("INVALID_ARGUMENTS", f"{path} contains unknown fields", {"fields": extras})
        for name, item in value.items():
            child = properties.get(name)
            if child is not None:
                _validate_schema(item, child, path=f"{path}.{name}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise InputError("INVALID_ARGUMENTS", f"{path} must be an array")
        _validate_length(value, schema, path)
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, path=f"{path}[{index}]")
        return
    if expected == "string":
        if not isinstance(value, str):
            raise InputError("INVALID_ARGUMENTS", f"{path} must be a string")
        _validate_length(value, schema, path)
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InputError("INVALID_ARGUMENTS", f"{path} must be an integer")
        _validate_number(value, schema, path)
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError("INVALID_ARGUMENTS", f"{path} must be a number")
        _validate_number(value, schema, path)
    elif expected == "boolean" and not isinstance(value, bool):
        raise InputError("INVALID_ARGUMENTS", f"{path} must be a boolean")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise InputError("INVALID_ARGUMENTS", f"{path} is not an allowed option", {"allowed": list(enum)})


def _validate_length(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if "minLength" in schema and len(value) < int(schema["minLength"]):
        raise InputError("INVALID_ARGUMENTS", f"{path} is too short")
    if "maxLength" in schema and len(value) > int(schema["maxLength"]):
        raise InputError("REQUEST_TOO_LARGE", f"{path} is too long")
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        raise InputError("INVALID_ARGUMENTS", f"{path} has too few items")
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        raise InputError("INVALID_ARGUMENTS", f"{path} has too many items")


def _validate_number(value: int | float, schema: Mapping[str, Any], path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise InputError("INVALID_ARGUMENTS", f"{path} is below its minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise InputError("INVALID_ARGUMENTS", f"{path} exceeds its maximum")
