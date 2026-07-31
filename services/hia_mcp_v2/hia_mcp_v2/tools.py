"""Capability-led tool registry for HIA MCP V2.

The registry intentionally exposes broad, batch-oriented operations.  Houdini
node types are discovered from the live installation; none are allowlisted
here.  Codex remains responsible for understanding the user's intent and for
generating HOM Python.
"""

from __future__ import annotations

import copy
import math
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
CAPTURE_VALIDATION_PATHS = {
    "type": "array",
    "items": PATH,
    "maxItems": 16,
}
EXPECTED_DELETION_PATHS = {
    "type": "array",
    "items": PATH,
    "maxItems": 1_024,
}
QUERY = {"type": "string", "maxLength": 512}
QUERIES = {"type": "array", "items": QUERY, "minItems": 1, "maxItems": 16}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 1_000_000, "default": 0}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
VALIDATION_CHECK_NAMES = (
    "node_errors",
    "empty_output",
    "critical_paths",
    "geometry_summary",
    "changed_scope",
    "semantic_expectations",
)
VALIDATION_CHECKS = {
    "type": "array",
    "items": {"type": "string", "enum": list(VALIDATION_CHECK_NAMES)},
    "maxItems": len(VALIDATION_CHECK_NAMES),
}
RETRIEVAL_MODE = {
    "type": "string",
    "enum": ["lexical", "vector", "hybrid"],
    "default": "hybrid",
}

SEMANTIC_DATA_REF = _object(
    {
        "path": PATH,
        "data_kind": {
            "type": "string",
            "enum": ["attribute", "volume", "field"],
        },
        "name": {"type": "string", "minLength": 1, "maxLength": 256},
        "owner": {
            "type": "string",
            "enum": ["point", "primitive", "vertex", "detail"],
            "default": "point",
        },
    },
    required=("path", "data_kind", "name"),
)
SEMANTIC_CHECKS = {
    "type": "array",
    "maxItems": 32,
    "items": _object(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 128},
            "type": {
                "type": "string",
                "enum": ["presence", "sample", "mapping"],
            },
            "path": PATH,
            "data_kind": {
                "type": "string",
                "enum": ["attribute", "volume", "field"],
            },
            "name": {"type": "string", "minLength": 1, "maxLength": 256},
            "owner": {
                "type": "string",
                "enum": ["point", "primitive", "vertex", "detail"],
                "default": "point",
            },
            "finite": {"type": "boolean", "default": True},
            "nonzero": {"type": "boolean", "default": False},
            "min_magnitude": {"type": "number", "minimum": 0},
            "max_magnitude": {"type": "number", "minimum": 0},
            "sample_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 256,
                "default": 64,
            },
            "source": SEMANTIC_DATA_REF,
            "target": SEMANTIC_DATA_REF,
            "forbidden_targets": {
                "type": "array",
                "items": SEMANTIC_DATA_REF,
                "maxItems": 16,
            },
        },
        required=("type",),
    ),
}

EXPERIMENT_PARAMETERS = {
    "type": "object",
    "additionalProperties": {
        "anyOf": [
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string", "maxLength": 4096},
        ],
    },
    "description": (
        "Expanded absolute hou.Parm paths mapped to temporary scalar values. "
        "baseline.parameters may be empty to use the current scene; candidate "
        "maps are deltas and may introduce paths not repeated in baseline."
    ),
}
EXPERIMENT_BASELINE = _object(
    {
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "parameters": EXPERIMENT_PARAMETERS,
    },
    required=("parameters",),
)
EXPERIMENT_CANDIDATE = _object(
    {
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "parameters": EXPERIMENT_PARAMETERS,
    },
    required=("name", "parameters"),
)

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
    aliases: tuple[str, ...] = ()

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
        "Read the live Houdini build, HIP, frame/FPS, take, dirty state, current network/node, selection, scene revision, Goal focus recovery mode, installed contexts, and an optional bounded graph overview. include_runtime_capabilities adds a versioned, non-mutating probe of the small HOM surface HIA actually depends on, distinguishing documented, callable, safely observed, and unavailable capabilities. For a concrete task, request a compact Context Pack: it combines only selected/change-scope entities, batched cached local-knowledge summaries, and recent execution/validation evidence under a strict byte budget with provenance. Explicit include_context_pack=false suppresses Context Pack construction and knowledge retrieval even when task, change_scope, or knowledge_queries are supplied.",
        _object(
            {
                "include_graph": {"type": "boolean", "default": False},
                "include_runtime_capabilities": {
                    "type": "boolean",
                    "default": False,
                },
                "graph_depth": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
                "include_context_pack": {"type": "boolean"},
                "task": {"type": "string", "maxLength": 1024},
                "change_scope": {
                    "type": "array",
                    "items": PATH,
                    "maxItems": 32,
                },
                "knowledge_queries": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 2, "maxLength": 256},
                    "maxItems": 4,
                },
                "context_pack_max_bytes": {
                    "type": "integer",
                    "minimum": 4096,
                    "maximum": 32768,
                    "default": 16384,
                },
                "limit": LIMIT,
            }
        ),
        aliases=("runtime", "recovery", "recover", "运行时", "恢复", "崩溃恢复"),
    ),
    ToolSpec(
        "hia_inspect",
        "scene_perception",
        "Inspect one path, multiple paths, or the current selection in one bounded call: types, parameters, inputs/outputs, flags, errors, geometry hints, and a finite child graph. Use path for one target or paths for a batch, never both. Missing batch paths are returned as bounded evidence without discarding records for paths that still exist. The evidence view adds compact upstream, public-control/reference, material-entry, cook/message, and high-confidence network-quality facts; its notices are evidence for Codex review, not proof of subjective quality. Use filters instead of dumping the scene.",
        _object(
            {
                "path": PATH,
                "paths": PATHS,
                "use_selection": {"type": "boolean", "default": True},
                "depth": {"type": "integer", "minimum": 0, "maximum": 3, "default": 0},
                "views": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["parameters", "connections", "flags", "errors", "geometry", "children", "evidence"],
                    },
                    "maxItems": 7,
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
        "Resolve installed Houdini help. Use requests to batch several targets, or the compatible single-target form with node_path, category plus a bare node_type, or node_type=\"Category/name\". A node_path returns expanded runtime parameter names, including instantiated multiparms; a type-only query returns template patterns such as names containing # because no live instance exists. Also returns the real versioned type, context, input rules, definition/source hints, and installed help metadata.",
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
        "Summarize geometry for several nodes: bounds, point/vertex/primitive counts, groups, attributes, primitive kinds, packed data, volumes, instances, topology hints, and cook errors. Bounds and point samples are factual hints, not proof of intersection, endpoint clearance, or subjective shape; measure an explicit spatial constraint with one short read-only HOM batch over named outputs.",
        _object(
            {
                "paths": PATHS,
                "use_selection": {"type": "boolean", "default": True},
                "include_attributes": {"type": "boolean", "default": True},
                "sample_limit": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
                "limit": LIMIT,
            }
        ),
        aliases=("modeling", "model", "建模", "几何建模"),
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
        "Run bounded, structured domain checks over explicit target paths, a network, or the current selection. Checks cover node/cook errors, empty outputs, critical paths, geometry summaries, changed-scope evidence, and optional explicit semantic expectations for attribute/field presence, finite or nonzero samples, magnitude ranges, and source-to-target mappings. Results separate observed failures, unsupported node categories, unknown/unobservable states, and caller declarations. Output-data checks request a forced fresh cook by default; pass cook=false for a strictly read-only stale-risk inspection. Cook/cache evidence never treats a clean error state as proof of recomputation. Spatial intersection is an extension boundary, not a hidden heavy scan.",
        _object(
            {
                "paths": PATHS,
                "root_path": PATH,
                "cook": {
                    "type": "boolean",
                    "description": "Force a fresh cook before validation. When omitted, output-data and semantic checks cook; structural checks do not.",
                },
                "expected_paths": PATHS,
                "checks": VALIDATION_CHECKS,
                "changed_paths": PATHS,
                "mutable_root": PATH,
                "protected_paths": PATHS,
                "semantic_checks": SEMANTIC_CHECKS,
                "query": QUERY,
                "limit": LIMIT,
            }
        ),
    ),
    ToolSpec(
        "hia_execute_hom",
        "hom_execution",
        "Execute one Codex-generated Python/HOM batch in the current Houdini UI main thread. Prefer modifying suitable existing nodes, then installed native Houdini nodes and parameter networks, using short HOM batches to orchestrate them; use Python SOP/script or direct code-built geometry only when no reasonable native node solution exists, explaining that exception in task. Raw script remains the primary write interface; optional task/mutable_root/protected_paths/expected_outputs/checks form a lightweight evidence envelope, not an IR or security sandbox. The batch is precompiled and uses one Houdini undo group when available; missing or disabled undo is reported and does not block execution. The runtime requests Undo only for a HOM exception, an observed scope violation, or an undeclared deletion, and only undoes when it can prove the batch's own undo item. Declaring a parent in expected_deletions also covers its automatically deleted descendants; do not enumerate internal child nodes. Requested validation failures, a missing expected deletion, unknown or partial evidence, and NO_OBSERVED_EFFECT remain postcondition evidence; they do not become tool errors or trigger Undo. Read rollback.status and the failure's automatic_retry_safe flag, but never repeat the identical failing batch automatically. Only a verified rolled-back batch permits one corrected bounded retry without first inspecting the scene; otherwise inspect or diff and then submit a corrected batch. Dirty mismatch or unavailability leaves rollback not_proven but is advisory for subsequent Goal work; never save or reload the HIP to fake a clean state. Timeouts, unverified rollback, and external file/render side effects are not automatic-retry safe. expected_outputs adds only critical-path existence and node-error checks; empty-output, geometry-summary, semantic, and fresh-cook validation run only when explicitly requested. Default diffing is targeted: predeclare exact diff_paths or call hia_mark_changed(path) before the first edit; only an explicit diff_root_path expands to a bounded network scan. Results include compact before/after local-network facts, target/connection/control/material/cook/message/scope postconditions, requested fresh output validation, rollback evidence, and a machine-fact execution trace under .runtime. postconditions.status is passed only for explicitly requested, fully observed structural assertions; a bare successful script is not result validation, and visual/render requirements remain unproven until separately observed. Visual or render proof is never fabricated or captured automatically; use the existing capture/render inspection tools when the original request needs it. Scene writes remain bound to the launcher session, Houdini process, and executor module path so a different runtime is rejected before submission. A newer executor source file on disk is only advisory: the write continues against the version already loaded in the same Houdini session, and restart is needed only to validate the newer disk source. Local knowledge remains an optional single batched lookup and is not an execution gate. timeout_seconds is a client wait budget, not a HOM kill deadline; a timeout after network I/O begins may have unknown execution state and must not be retried automatically. An optional checkpoint_label saves one Houdini backup only after a confirmed successful change, preferring the safely saved HIP's .hia/checkpoints directory and otherwise using the launcher-session fallback.",
        _object(
            {
                "script": {"type": "string", "minLength": 1, "maxLength": 524_288},
                "task": {"type": "string", "maxLength": 1024},
                "mutable_root": PATH,
                "protected_paths": PATHS,
                "expected_outputs": PATHS,
                "expected_deletions": EXPECTED_DELETION_PATHS,
                "checks": VALIDATION_CHECKS,
                "semantic_checks": SEMANTIC_CHECKS,
                "fresh_validation": {"type": "boolean", "default": True},
                "require_scene_change": {
                    "type": "boolean",
                    "description": "Require an observed scene change. Defaults on when diff paths, expected outputs, checkpoints, or declared touched paths make a write effect explicit.",
                },
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 60},
                "capture_diff": {"type": "boolean", "default": True},
                "diff_paths": PATHS,
                "diff_root_path": PATH,
                "checkpoint_label": {"type": "string", "maxLength": 128},
            },
            required=("script",),
        ),
        read_only=False,
        aliases=(
            "checkpoint",
            "检查点",
            "备份",
            "modeling",
            "model",
            "建模",
            "几何建模",
        ),
    ),
    ToolSpec(
        "hia_run_effect_experiment",
        "effect_experiment",
        "Run one bounded, temporary, domain-neutral effect comparison in the current Houdini UI session. Preflight a target network and every expanded scalar parameter path before any write, then evaluate one baseline plus two or three named candidates from the same baseline over an inclusive frame range. Explicit cache-reset button parameters are the only cache-clear evidence; force cooking alone is never reported as a reset. Each candidate advances frames sequentially, captures every requested sample with one locked camera/display/framing signature and one derived or requested preview resolution, and returns actual parameter readback, per-frame cook/messages/metrics, embedded-PNG contact-sheet evidence, expected-versus-unexpected deletions, and explicit restoration proof. timeout_seconds is the same 1-300 second client wait budget used by batch HOM; it does not kill work already running on Houdini's UI thread. It never scores candidates, builds EffectSpec, uses PDG/Wedge, or performs knowledge search.",
        _object(
            {
                "target_network": PATH,
                "baseline": EXPERIMENT_BASELINE,
                "candidates": {
                    "type": "array",
                    "items": EXPERIMENT_CANDIDATE,
                    "minItems": 2,
                    "maxItems": 3,
                },
                "frame_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "sample_frames": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 6,
                },
                "preview": _object(
                    {
                        "width": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 1920,
                        },
                        "height": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 1920,
                        },
                        "quality_scale": {
                            "type": "number",
                            "minimum": 0.1,
                            "maximum": 1.0,
                            "default": 1.0,
                        },
                    }
                ),
                "camera_path": PATH,
                "display_mode": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": (
                        "Optional exact assertion for the currently observed "
                        "viewport shading mode; the experiment does not mutate it."
                    ),
                },
                "framing": {
                    "type": "string",
                    "enum": ["current_view", "camera"],
                },
                "capture_mode": {
                    "type": "string",
                    "enum": ["contact_sheet"],
                    "default": "contact_sheet",
                },
                "cache_reset_parms": {
                    "type": "array",
                    "items": PATH,
                    "maxItems": 8,
                },
                "cook_targets": {
                    "type": "array",
                    "items": PATH,
                    "maxItems": 8,
                },
                "metric_targets": {
                    "type": "array",
                    "items": PATH,
                    "maxItems": 4,
                },
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "cook_evidence",
                            "node_messages",
                            "geometry_summary",
                            "image_quality",
                        ],
                    },
                    "maxItems": 4,
                },
                "expected_deletions": {
                    "type": "array",
                    "items": PATH,
                    "maxItems": 16,
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 300,
                    "default": 60,
                },
            },
            required=(
                "target_network",
                "baseline",
                "candidates",
                "frame_range",
                "sample_frames",
            ),
        ),
        read_only=False,
    ),
    ToolSpec(
        "hia_scene_diff",
        "debug_validation",
        "Capture, compare, list, or forget bounded scene snapshots to verify execution effects. Compare classifies expected deletions separately from unexpected deletions; a declared parent covers its deleted descendants. Snapshots contain structural fingerprints, not HIP copies.",
        _object(
            {
                "action": {"type": "string", "enum": ["capture", "compare", "list", "forget"]},
                "snapshot_id": {"type": "string", "maxLength": 128},
                "root_path": PATH,
                "expected_deletions": EXPECTED_DELETION_PATHS,
                "limit": LIMIT,
            },
            required=("action",),
        ),
    ),
    ToolSpec(
        "hia_capture_viewport",
        "visual_feedback",
        "Capture the current visible viewport through Houdini's documented flipbook path only when visual verification is needed. Omitted dimensions derive from the live viewport; one supplied dimension preserves its aspect, and two supplied dimensions are honored exactly. A single frame remains the static default. For animation or simulation, use an explicit frames list or frame_range plus frame_step (at most 24 frames); the runtime locks, evaluates, and captures each requested frame, detects missing/failed/unchanged sequences, and records requested versus actual frames with bounded evidence. validation_paths optionally force-cook critical nodes per frame. Restores the original camera/view, camera lock, and frame state, does not open MPlay or take focus, and reports actual dimensions/aspect, capture quality, and the unverified OS HDR/display boundary. A safely saved current HIP uses its sibling .hia/screenshots directory; otherwise images fall back to HIA_CACHE_DIR/screenshots.",
        _object(
            {
                "mode": {"type": "string", "enum": ["viewport", "flipbook"], "default": "viewport"},
                "camera_path": PATH,
                "frame": {"type": "number"},
                "frames": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 1,
                    "maxItems": 24,
                },
                "frame_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Inclusive start and end frames. Use frame_step to keep a short representative sequence; the runtime rejects reversed ranges, spans over 240 frames, or more than 24 captures.",
                },
                "frame_step": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 240,
                    "default": 1,
                },
                "validation_paths": CAPTURE_VALIDATION_PATHS,
                "expect_change": {
                    "type": "boolean",
                    "description": "For a multi-frame sequence, treat identical captured frames as a failed temporal validation. Defaults true for sequences.",
                },
                "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096},
                "return_image": {"type": "boolean", "default": True},
            }
        ),
    ),
    ToolSpec(
        "hia_local_help_search",
        "local_documentation",
        "The single local-knowledge search entry for versioned official workflows, curated community tutorials, live installed Houdini help, project references, explicitly imported user documents/transcripts, explicitly selected public Thread exports, and explicit project memories. It uses the existing SQLite FTS5 index plus optional local Qwen embeddings; Qwen only encodes text and Codex remains the sole reasoning system. Use one queries batch instead of parallel duplicate searches. source_kinds isolates a real indexed source kind, and card_id/canonical_id performs exact workflow-card lookup. Exact source-kind or card filters automatically resolve their indexed backing source group while the exact document filter prevents unrelated results. refresh=false is strictly read-only; refresh=true performs one explicit incremental refresh, while full vector builds remain an independent CLI operation. If the encoder is unavailable, retrieval degrades completely to lexical. While the vector index is partial, reranking is limited to each query's own lexical candidates; global vector ranking starts only when the index reports complete. Compact is default; full/diagnostic may return bounded reconstructed content for official, community, and user-supplied records without changing their verification level. This tool does not gate hia_execute_hom or automatically ingest chats.",
        _object(
            {
                "query": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 256,
                    "description": "Search text; required unless card_id or canonical_id performs an exact lookup.",
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 2, "maxLength": 256},
                    "minItems": 1,
                    "maxItems": 16,
                    "description": "Batched search text; required unless card_id or canonical_id performs an exact lookup.",
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["houdini", "project", "user", "memory"],
                    },
                    "maxItems": 4,
                },
                "source_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "builtin_official_workflow",
                            "community_tutorial",
                            "user_document",
                            "user_transcript",
                            "thread_export",
                            "project_memory",
                        ],
                    },
                    "maxItems": 6,
                    "description": "Optional exact source-kind filters across the single local index. User, Thread, and memory sources remain explicitly supplied and unverified.",
                },
                "card_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "Exact built-in workflow card ID; may be used without query/queries.",
                },
                "canonical_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "Exact canonical workflow ID; may be used without query/queries.",
                },
                "mode": RETRIEVAL_MODE,
                "offset": OFFSET,
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "response_format": {
                    "type": "string",
                    "enum": ["compact", "full", "diagnostic"],
                    "default": "compact",
                    "description": "compact returns bounded summaries and source identity; full/diagnostic may include bounded reconstructed content for official/community cards and explicit user records.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 4096,
                    "maximum": 262144,
                    "default": 65536,
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, run one explicit incremental source refresh before searching. False is strictly read-only.",
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

CAPABILITY_MATRIX = tuple(
    {
        "domain": spec.domain,
        "tools": [spec.name],
        "status": "implemented",
        "description": spec.description,
        "parameters": list(spec.input_schema.get("properties", {})),
        "aliases": list(spec.aliases),
    }
    for spec in TOOL_SPECS
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
        if tool_name == "hia_local_help_search":
            for identity_name in ("card_id", "canonical_id"):
                if (
                    identity_name in arguments
                    and not str(arguments[identity_name]).strip()
                ):
                    raise InputError(
                        "INVALID_ARGUMENTS",
                        f"{identity_name} must not be blank",
                    )
            if not (
                "query" in arguments
                or "queries" in arguments
                or "card_id" in arguments
                or "canonical_id" in arguments
            ):
                raise InputError(
                    "INVALID_ARGUMENTS",
                    "Provide query, queries, card_id, or canonical_id",
                )
    if tool_name == "hia_inspect" and "path" in arguments and "paths" in arguments:
        raise InputError(
            "INVALID_ARGUMENTS",
            "Provide path or paths, not both",
        )
    if tool_name == "hia_node_help" and "requests" in arguments:
        if set(arguments) != {"requests"}:
            raise InputError(
                "INVALID_ARGUMENTS",
                "Batch node help options belong inside each requests item",
            )
    elif tool_name == "hia_node_help" and not (
        str(arguments.get("node_path") or "").strip()
        or str(arguments.get("node_type") or "").strip()
    ):
        raise InputError(
            "INVALID_ARGUMENTS",
            "Provide node_path, category plus node_type, or node_type as Category/name",
        )
    if (
        tool_name == "hia_scene_diff"
        and arguments.get("action") in {"compare", "forget"}
        and not str(arguments.get("snapshot_id") or "").strip()
    ):
        raise InputError(
            "INVALID_ARGUMENTS",
            "snapshot_id is required for compare or forget",
        )
    if tool_name == "hia_capture_viewport":
        _validate_capture_viewport(arguments)
    if tool_name == "hia_run_effect_experiment":
        _validate_effect_experiment(arguments)
    if tool_name == "hia_project_memory":
        _validate_project_memory(arguments)
    if tool_name in {"hia_validate", "hia_execute_hom"} and "semantic_checks" in arguments:
        _validate_semantic_checks(arguments["semantic_checks"])


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


def _validate_semantic_checks(value: Any) -> None:
    for index, check in enumerate(value):
        check_type = check["type"]
        common = {"id", "type"}
        if check_type in {"presence", "sample"}:
            missing = sorted({"path", "data_kind", "name"}.difference(check))
            allowed = common | {"path", "data_kind", "name", "owner"}
            if check_type == "sample":
                allowed |= {
                    "finite",
                    "nonzero",
                    "min_magnitude",
                    "max_magnitude",
                    "sample_limit",
                }
        else:
            missing = sorted({"source", "target"}.difference(check))
            allowed = common | {"source", "target", "forbidden_targets"}
        if missing:
            raise InputError(
                "INVALID_ARGUMENTS",
                f"semantic_checks[{index}] is missing required fields",
                {"missing": missing},
            )
        unrelated = sorted(set(check).difference(allowed))
        if unrelated:
            raise InputError(
                "INVALID_ARGUMENTS",
                f"semantic_checks[{index}] contains unrelated fields",
                {"fields": unrelated},
            )
        if (
            check_type == "sample"
            and "min_magnitude" in check
            and "max_magnitude" in check
            and check["min_magnitude"] > check["max_magnitude"]
        ):
            raise InputError(
                "INVALID_ARGUMENTS",
                f"semantic_checks[{index}] has an inverted magnitude range",
            )


def _validate_capture_viewport(arguments: Mapping[str, Any]) -> None:
    selectors = [
        name for name in ("frame", "frames", "frame_range") if name in arguments
    ]
    if len(selectors) > 1:
        raise InputError(
            "INVALID_ARGUMENTS",
            "Provide only one of frame, frames, or frame_range",
            {"fields": selectors},
        )
    if "frame_step" in arguments and "frame_range" not in arguments:
        raise InputError(
            "INVALID_ARGUMENTS",
            "frame_step is valid only with frame_range",
        )


def _validate_effect_experiment(arguments: Mapping[str, Any]) -> None:
    baseline = arguments.get("baseline")
    candidates = arguments.get("candidates")
    variants = [
        ("baseline", baseline, True),
        *[
            (f"candidates[{index}]", value, False)
            for index, value in enumerate(candidates or [])
        ],
    ]
    names: list[str] = []
    paths: set[str] = set()
    for field, raw, allow_empty in variants:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or ("baseline" if field == "baseline" else ""))
        names.append(name.casefold())
        parameters = raw.get("parameters")
        if not isinstance(parameters, Mapping):
            continue
        if not parameters and not allow_empty:
            raise InputError(
                "INVALID_ARGUMENTS",
                f"{field}.parameters must not be empty",
            )
        for path, value in parameters.items():
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or path == "/"
                or "\x00" in path
            ):
                raise InputError(
                    "INVALID_ARGUMENTS",
                    f"{field}.parameters requires expanded absolute hou.Parm paths",
                )
            if (
                value is None
                or not isinstance(value, (str, bool, int, float))
                or isinstance(value, float)
                and not math.isfinite(value)
                or isinstance(value, str)
                and (len(value) > 4096 or "\x00" in value)
            ):
                raise InputError(
                    "INVALID_ARGUMENTS",
                    f"{field}.parameters[{path}] must be a scalar bool, int, finite float, or string",
                )
            paths.add(path)
    if len(names) != len(set(names)):
        raise InputError("INVALID_ARGUMENTS", "Variant names must be unique")
    if not 1 <= len(paths) <= 16:
        raise InputError(
            "INVALID_ARGUMENTS",
            "The experiment must touch 1-16 parameters",
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
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError("INVALID_ARGUMENTS", f"{path} must be finite")
    if "minimum" in schema and value < schema["minimum"]:
        raise InputError("INVALID_ARGUMENTS", f"{path} is below its minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise InputError("INVALID_ARGUMENTS", f"{path} exceeds its maximum")
