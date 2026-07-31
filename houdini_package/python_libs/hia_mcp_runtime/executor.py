"""Live Houdini handlers marshalled through the UI main thread.

The runtime contains no language model, planner, asset semantics, or node-type
allowlist.  It exposes bounded observations and one general HOM execution
primitive so Codex can operate the current Houdini session directly.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import io
import json
import math
import os
import re
import stat
import struct
import threading
import time
import traceback
import uuid
import warnings as python_warnings
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .hybrid_knowledge import HybridKnowledgeError, HybridKnowledgeStore
from .knowledge_index import (
    FILTERABLE_SOURCE_KINDS,
    LocalKnowledgeIndex,
    SEARCH_SOURCE_GROUPS,
    SOURCE_KIND_GROUPS,
    SOURCE_GROUPS,
)
from .viewport_quality import analyze_png_quality


MAX_SCRIPT_CHARS = 524_288
MAX_FLIPBOOK_FRAME_SPAN = 240.0
MAX_CAPTURE_FRAMES = 24
MAX_EXPERIMENT_SAMPLE_FRAMES = 6
MAX_EXPERIMENT_PARAMETERS = 16
MAX_EXPERIMENT_COOK_CALLS = 1024
MAX_BATCH_QUERIES = 16
MAX_TEXT_CHARS = 65_536
MAX_SNAPSHOT_NODES = 10_000
MAX_SNAPSHOTS = 16
MAX_TARGETED_DIFF_PATHS = 128
MAX_CONTEXT_PACK_BYTES = 32_768
DEFAULT_CONTEXT_PACK_BYTES = 16_384
MAX_CONTEXT_PACK_ENTITIES = 16
MAX_CONTEXT_PACK_QUERIES = 4
MAX_RECENT_EVIDENCE = 32
MAX_NETWORK_EVIDENCE_PATHS = 16
MAX_VALIDATION_GEOMETRY_PATHS = 16
MAX_COOK_EVIDENCE_PATHS = 64
MAX_SEMANTIC_CHECKS = 32
MAX_SEMANTIC_SAMPLES = 256
DEFAULT_LOCAL_HELP_BYTES = 65_536
MAX_LOCAL_HELP_BYTES = 262_144
MAX_LOCAL_HELP_SUMMARY_CHARS = 600
MAX_FULL_KNOWLEDGE_CARD_CHARS = 48_000
FOCUS_STATE_MAX_BYTES = 1_048_576
STAGE_CHECKPOINT_MARKER = ".hia-stage-checkpoint.json"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
_NODE_DIGEST_UNAVAILABLE = object()
_SEMANTIC_UNSUPPORTED_REASONS = frozenset(
    {
        "unsupported_node_category",
        "needs_to_cook_unavailable",
        "geometry_method_unavailable",
        "attribute_lookup_unavailable",
        "attribute_sampling_unavailable",
        "volume_lookup_unavailable",
        "volume_sampling_unavailable",
    }
)
VALIDATION_CHECK_NAMES = (
    "node_errors",
    "empty_output",
    "critical_paths",
    "geometry_summary",
    "changed_scope",
    "semantic_expectations",
)

class HiaRuntimeError(Exception):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass
class _Snapshot:
    snapshot_id: str
    root_path: str
    nodes: dict[str, str]
    truncated: bool
    scene_revision: int
    created_at: float


class HoudiniExecutor:
    """Dispatch broad HIA tools through ``hdefereval`` in a UI session."""

    TOOL_NAMES = (
        "hia_context",
        "hia_inspect",
        "hia_scene_graph",
        "hia_search_node_types",
        "hia_node_help",
        "hia_geometry_summary",
        "hia_material_render_summary",
        "hia_solaris_summary",
        "hia_animation_summary",
        "hia_simulation_summary",
        "hia_validate",
        "hia_execute_hom",
        "hia_run_effect_experiment",
        "hia_scene_diff",
        "hia_capture_viewport",
        "hia_local_help_search",
        "hia_project_memory",
    )

    def __init__(
        self,
        *,
        hou_module: Any | None = None,
        main_thread_runner: Callable[[Callable[[], Any]], Any] | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if hou_module is None:
            try:
                import hou as hou_module  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise HiaRuntimeError("HOUDINI_UNAVAILABLE", "The hou module is unavailable") from exc
        if main_thread_runner is None:
            try:
                import hdefereval  # type: ignore[import-not-found]

                main_thread_runner = hdefereval.executeInMainThreadWithResult
            except ImportError as exc:
                raise HiaRuntimeError(
                    "UI_MAIN_THREAD_UNAVAILABLE",
                    "HIA MCP V2 requires a graphical Houdini UI main-thread dispatcher",
                ) from exc
        self._hou = hou_module
        self._run_on_main_thread = main_thread_runner
        self._project_root = Path(project_root or os.getcwd()).resolve()
        self._runtime_root = self._project_root / ".runtime" / "hia-mcp-v2"
        expected_cache_root = (self._project_root / ".runtime" / "cache").resolve()
        if not _is_within(expected_cache_root, self._project_root):
            raise HiaRuntimeError(
                "INVALID_CACHE_DIR",
                "The project cache path escaped the project root",
            )
        configured_cache_root = os.environ.get("HIA_CACHE_DIR")
        if configured_cache_root:
            candidate_cache_root = Path(configured_cache_root)
            if not candidate_cache_root.is_absolute():
                raise HiaRuntimeError(
                    "INVALID_CACHE_DIR",
                    "HIA_CACHE_DIR must be an absolute project cache path",
                )
            candidate_cache_root = candidate_cache_root.resolve()
            if os.path.normcase(str(candidate_cache_root)) != os.path.normcase(
                str(expected_cache_root)
            ):
                raise HiaRuntimeError(
                    "INVALID_CACHE_DIR",
                    "HIA_CACHE_DIR must be the project .runtime/cache directory",
                )
        self._cache_root = expected_cache_root
        self._screenshot_root = self._cache_root / "screenshots"
        self._state_lock = threading.RLock()
        self._scene_revision = 0
        self._snapshots: dict[str, _Snapshot] = {}
        self._recent_evidence: deque[dict[str, Any]] = deque(
            maxlen=MAX_RECENT_EVIDENCE
        )
        self._trace_session_id = uuid.uuid4().hex
        self._trace_lock = threading.Lock()
        self._knowledge_index: LocalKnowledgeIndex | None = None
        self._hybrid_knowledge: HybridKnowledgeStore | None = None
        self._handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "hia_context": self._context,
            "hia_inspect": self._inspect,
            "hia_scene_graph": self._scene_graph,
            "hia_search_node_types": self._search_node_types,
            "hia_node_help": self._node_help,
            "hia_geometry_summary": self._geometry_summary,
            "hia_material_render_summary": self._material_render_summary,
            "hia_solaris_summary": self._solaris_summary,
            "hia_animation_summary": self._animation_summary,
            "hia_simulation_summary": self._simulation_summary,
            "hia_validate": self._validate,
            "hia_execute_hom": self._execute_hom,
            "hia_run_effect_experiment": self._run_effect_experiment,
            "hia_scene_diff": self._scene_diff,
            "hia_capture_viewport": self._capture_viewport,
        }

    @property
    def scene_revision(self) -> int:
        with self._state_lock:
            return self._scene_revision

    def dispatch(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "Tool arguments must be an object")
        copied_arguments = dict(arguments)
        if tool_name == "hia_local_help_search":
            return self._dispatch_local_help(copied_arguments)
        if tool_name == "hia_project_memory":
            return self._dispatch_project_memory(copied_arguments)
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise HiaRuntimeError("TOOL_NOT_FOUND", "Unknown HIA MCP V2 runtime tool", {"tool": tool_name})

        dispatch_requested = time.monotonic()
        ui_started = dispatch_requested

        def run() -> dict[str, Any]:
            nonlocal ui_started
            ui_started = time.monotonic()
            try:
                value = handler(copied_arguments)
            except HiaRuntimeError:
                raise
            except Exception as exc:
                raise HiaRuntimeError(
                    "HOUDINI_EXECUTION_ERROR",
                    _bounded_text(_redact_text(str(exc)), 2048),
                    {"traceback": _bounded_text(_redact_text(traceback.format_exc(limit=12)), 12_000)},
                ) from exc
            if not isinstance(value, dict):
                raise HiaRuntimeError(
                    "INVALID_HANDLER_RESULT",
                    "Houdini runtime handler returned a non-object",
                )
            return value

        try:
            result = self._run_on_main_thread(run)
        except HiaRuntimeError:
            raise
        except Exception as exc:
            raise HiaRuntimeError(
                "UI_MAIN_THREAD_DISPATCH_FAILED",
                "The call could not be executed on Houdini's UI main thread",
                {"reason": _bounded_text(_redact_text(str(exc)), 1024)},
            ) from exc
        if tool_name == "hia_context" and self._context_pack_requested(
            copied_arguments
        ):
            result = self._enrich_context_pack(result, copied_arguments)
        if tool_name == "hia_execute_hom":
            result["phase_timings"]["queue_seconds"] = _seconds(
                ui_started - dispatch_requested
            )
            result["phase_timings"]["total_seconds"] = _seconds(
                time.monotonic() - dispatch_requested
            )
            result = self._record_execution_trace(result)
        return result

    def _context(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        current_network, current_node = self._current_ui_nodes()
        selected_nodes = list(_safe_call(self._hou, "selectedNodes", ()))
        contexts = []
        categories = _safe_call(self._hou, "nodeTypeCategories", {})
        if isinstance(categories, Mapping):
            contexts = sorted(str(name) for name in categories)
        take = _safe_call(getattr(self._hou, "takes", None), "currentTake", None)
        frame_range, playback_range = _houdini_frame_ranges(self._hou)
        result: dict[str, Any] = {
            "houdini_build": _application_version(self._hou),
            "hip_path": str(_safe_call(self._hou.hipFile, "path", "")),
            "frame": _json_value(_safe_call(self._hou, "frame", 0.0)),
            "fps": _json_value(_safe_call(self._hou, "fps", 0.0)),
            "frame_range": _json_value(frame_range),
            "playbar_range": _json_value(playback_range),
            "take": _safe_name(take),
            "dirty": self._dirty(),
            "current_network": _safe_path(current_network),
            "current_node": _safe_path(current_node),
            "selection": [_safe_path(node) for node in selected_nodes],
            "scene_revision": self.scene_revision,
            "goal_focus_mode": self._goal_focus_mode(),
            "available_contexts": contexts,
            "ui_available": bool(_safe_call(self._hou, "isUIAvailable", True)),
        }
        if bool(arguments.get("include_graph", False)):
            depth = _bounded_int(arguments.get("graph_depth", 1), 0, 3)
            limit = _limit(arguments)
            root_path = _safe_path(current_network) or "/"
            graph = self._graph_records(root_path, depth=depth, query="", limit=limit)
            result["graph"] = graph
        if bool(arguments.get("include_runtime_capabilities", False)):
            probe_target = current_node or (selected_nodes[-1] if selected_nodes else None)
            result["runtime_capabilities"] = self._runtime_capability_probe(
                probe_target
            )
        if self._context_pack_requested(arguments):
            result["context_pack"] = self._context_pack_live_snapshot(
                arguments,
                selected_nodes=selected_nodes,
                current_node=current_node,
            )
        return self._success(result)

    def _runtime_capability_probe(self, target_node: Any | None) -> dict[str, Any]:
        documentation = "https://www.sidefx.com/docs/houdini/hom/"
        specs = (
            ("hou.OpNode.needsToCook", "OpNode", "needsToCook", "observe"),
            (
                "hou.OpNode.isTimeDependent",
                "OpNode",
                "isTimeDependent",
                "observe_last_cook",
            ),
            ("hou.OpNode.cookCount", "OpNode", "cookCount", "observe"),
            ("hou.OpNode.lastCookTime", "OpNode", "lastCookTime", "observe"),
            ("hou.OpNode.cook", "OpNode", "cook", "do_not_invoke"),
            ("hou.SopNode.geometry", "SopNode", "geometry", "do_not_invoke"),
            (
                "hou.Geometry.findPointAttrib",
                "Geometry",
                "findPointAttrib",
                "do_not_invoke",
            ),
            ("hou.Geometry.primByName", "Geometry", "primByName", "do_not_invoke"),
            ("hou.Volume.sample", "Volume", "sample", "do_not_invoke"),
            ("hou.VDB.samplev", "VDB", "samplev", "do_not_invoke"),
        )
        capabilities = []
        for name, owner_name, method_name, probe_kind in specs:
            owner = getattr(self._hou, owner_name, None)
            class_callable = callable(getattr(owner, method_name, None))
            bound = (
                getattr(target_node, method_name, None)
                if owner_name in {"OpNode", "SopNode"} and target_node is not None
                else None
            )
            is_callable = class_callable or callable(bound)
            status = "unavailable"
            value = None
            error = None
            if not is_callable:
                status = "unavailable"
            elif probe_kind == "do_not_invoke":
                status = "callable_not_invoked"
            elif not callable(bound):
                status = "no_target"
            else:
                try:
                    value = (
                        bound(for_last_cook=True)
                        if probe_kind == "observe_last_cook"
                        else bound()
                    )
                    status = "observed"
                except Exception as exc:
                    status = "error"
                    error = _bounded_text(_redact_text(str(exc)), 1024)
            capabilities.append(
                {
                    "name": name,
                    "documented": True,
                    "callable": is_callable,
                    "probe_status": status,
                    "value": _json_value(value) if status == "observed" else None,
                    "error": error,
                }
            )
        return {
            "schema": "hia-runtime-capabilities/1",
            "houdini_build": _application_version(self._hou),
            "target_path": _safe_path(target_node) or None,
            "documentation": documentation,
            "capabilities": capabilities,
        }

    @staticmethod
    def _context_pack_requested(arguments: Mapping[str, Any]) -> bool:
        if "include_context_pack" in arguments:
            return arguments.get("include_context_pack") is True
        return any(
            arguments.get(name)
            for name in (
                "task",
                "change_scope",
                "knowledge_queries",
            )
        )

    def _context_pack_live_snapshot(
        self,
        arguments: Mapping[str, Any],
        *,
        selected_nodes: list[Any],
        current_node: Any | None,
    ) -> dict[str, Any]:
        task = arguments.get("task", "")
        if not isinstance(task, str) or len(task) > 1024:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", "task must be at most 1024 characters"
            )
        scope_paths = self._absolute_node_paths(
            arguments.get("change_scope") or [],
            field_name="change_scope", maximum=32,
        )
        selection_paths = [
            path for path in dict.fromkeys(_safe_path(node) for node in selected_nodes)
            if path
        ]
        current_path = _safe_path(current_node)
        roles: dict[str, list[str]] = {}
        for path in selection_paths:
            roles[path] = ["selection"]
        if current_path and "current_node" not in roles.setdefault(current_path, []):
            roles[current_path].append("current_node")
        for path in scope_paths:
            if "change_scope" not in roles.setdefault(path, []):
                roles[path].append("change_scope")
        entities = []
        for path, path_roles in list(roles.items())[:MAX_CONTEXT_PACK_ENTITIES]:
            node = self._hou.node(path)
            if node is None:
                record = {"path": path, "exists": False}
            else:
                record = self._node_record(
                    node,
                    views={"connections", "flags", "errors"},
                    query="", depth=0, limit=16,
                )
                record["exists"] = True
            record["roles"] = path_roles
            entities.append(record)
        relevant_paths = list(dict.fromkeys([*scope_paths, *selection_paths, current_path]))
        return {
            "version": 1,
            "task": _bounded_text(task.strip(), 1024),
            "scene": {
                "houdini_build": _application_version(self._hou),
                "scene_revision": self.scene_revision,
            },
            "scope": {
                "selection": selection_paths[:32],
                "current_node": _bounded_text(current_path, 512) or None,
                "change_scope": scope_paths,
            },
            "entities": entities,
            "knowledge": {
                "mode": "lexical", "queries": [], "hits": [],
                "status": "not_requested", "fallback_reason": "",
            },
            "recent_evidence": self._recent_evidence_snapshot(
                [path for path in relevant_paths if path]
            ),
            "sources": [
                {"id": "live_houdini", "kind": "live_scene"},
                {"id": "runtime_evidence", "kind": "bounded_current_session_facts"},
            ],
        }

    def _enrich_context_pack(
        self,
        response: dict[str, Any],
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = response.get("result")
        pack = payload.get("context_pack") if isinstance(payload, dict) else None
        if not isinstance(pack, dict):
            return response
        max_bytes = _bounded_int(
            arguments.get("context_pack_max_bytes", DEFAULT_CONTEXT_PACK_BYTES),
            4096, MAX_CONTEXT_PACK_BYTES,
        )
        raw_queries = arguments.get("knowledge_queries")
        if raw_queries is None:
            candidate = str(arguments.get("task") or "").strip()
            queries = [_bounded_text(candidate, 256)] if len(candidate) >= 2 else []
        else:
            if not isinstance(raw_queries, list) or len(raw_queries) > MAX_CONTEXT_PACK_QUERIES:
                raise HiaRuntimeError("INVALID_ARGUMENTS", "knowledge_queries must contain at most 4 strings")
            queries = []
            for index, value in enumerate(raw_queries):
                query = value.strip() if isinstance(value, str) else ""
                if not 2 <= len(query) <= 256:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Each knowledge query must contain 2-256 characters",
                        {"index": index},
                    )
                if query.casefold() not in {item.casefold() for item in queries}:
                    queries.append(query)
        knowledge = pack["knowledge"]
        knowledge["queries"] = queries
        if queries:
            try:
                store = self._hybrid_knowledge_store()
                search_results = store.search_many(
                    queries,
                    set(SEARCH_SOURCE_GROUPS),
                    current_houdini_version=str(payload.get("houdini_build") or "unknown"),
                    offset=0, limit=4, mode="lexical",
                )
                merged: dict[str, dict[str, Any]] = {}
                for query, search_result in zip(queries, search_results):
                    for match in search_result.get("matches", []):
                        metadata = match.get("metadata")
                        metadata = metadata if isinstance(metadata, Mapping) else {}
                        key = str(metadata.get("source_key") or f"{match.get('source', '')}:{match.get('title', '')}")
                        hit = merged.get(key)
                        if hit is None:
                            hit = {
                                "source": str(match.get("source") or ""),
                                "title": _bounded_text(str(match.get("title") or ""), 256),
                                "snippet": _bounded_text(str(match.get("snippet") or ""), 600),
                                "matched_queries": [],
                                "provenance": {
                                    name: _bounded_text(str(metadata.get(name) or ""), maximum)
                                    for name, maximum in (
                                        ("source_key", 512), ("url", 1024),
                                        ("houdini_version", 128),
                                        ("verification", 128), ("evidence", 512),
                                    )
                                },
                            }
                            merged[key] = hit
                        if query not in hit["matched_queries"]:
                            hit["matched_queries"].append(query)
                database = store.index.relative_database_path
                knowledge.update({
                    "hits": list(merged.values())[:12], "status": "ready",
                    "fallback_reason": "",
                })
                pack["sources"].append(
                    {"id": "local_knowledge", "kind": "cached_sqlite_fts5", "database": database}
                )
            except Exception as exc:
                knowledge.update({
                    "hits": [], "status": "degraded",
                    "fallback_reason": _bounded_text(_redact_text(str(exc)), 1024),
                })
        return self._fit_context_pack(response, pack, max_bytes)

    @staticmethod
    def _fit_context_pack(
        response: dict[str, Any],
        pack: dict[str, Any],
        max_bytes: int,
    ) -> dict[str, Any]:
        limits = {"max_bytes": max_bytes, "truncated": False}
        pack["limits"] = limits
        trim_targets = (
            pack["knowledge"]["hits"],
            pack["recent_evidence"],
            pack["entities"],
            pack["scope"]["selection"],
            pack["scope"]["change_scope"],
        )
        while len(json.dumps(
            pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")) > max_bytes:
            for values in trim_targets:
                if values:
                    values.pop()
                    break
            else:
                if len(pack["task"]) > 128:
                    pack["task"] = _bounded_text(pack["task"], 128)
                else:
                    raise HiaRuntimeError(
                        "RESPONSE_TOO_LARGE",
                        "Context Pack metadata exceeds its budget",
                        {"limit": max_bytes},
                    )
            limits["truncated"] = True
        return response

    def _inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if "path" in arguments and "paths" in arguments:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Provide path or paths, not both",
            )
        requested_paths = (
            [str(arguments["path"])]
            if "path" in arguments
            else [str(value) for value in arguments.get("paths", [])]
        )
        missing_paths: list[str] = []
        if requested_paths:
            nodes = []
            for path in requested_paths:
                node = self._hou.node(path)
                if node is None:
                    missing_paths.append(path)
                else:
                    nodes.append(node)
        else:
            nodes = self._resolve_nodes(arguments)
        views = set(arguments.get("views") or ["parameters", "connections", "flags", "errors"])
        query = str(arguments.get("query", "")).casefold()
        depth = _bounded_int(arguments.get("depth", 0), 0, 3)
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        records = []
        for node in nodes:
            record = self._node_record(node, views=views, query=query, depth=depth, limit=limit)
            records.append(record)
        page = records[offset : offset + limit]
        return self._success(
            {
                "nodes": page,
                "total": len(records),
                "missing_paths": missing_paths[:64],
                "offset": offset,
                "limit": limit,
            },
            warnings=(
                [
                    "Some requested inspect paths do not exist; existing paths "
                    "were still returned"
                ]
                if missing_paths
                else None
            ),
        )

    def _scene_graph(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        root_path = str(arguments.get("root_path") or self._current_network_path() or "/")
        query = str(arguments.get("query", "")).casefold()
        depth = _bounded_int(arguments.get("depth", 2), 0, 6)
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        graph = self._graph_records(root_path, depth=depth, query=query, limit=offset + limit)
        nodes = graph["nodes"]
        selected_paths = {item["path"] for item in nodes[offset : offset + limit]}
        edges = [
            edge
            for edge in graph["edges"]
            if edge["from"] in selected_paths or edge["to"] in selected_paths
        ] if bool(arguments.get("include_dependencies", True)) else []
        return self._success(
            {
                "root_path": root_path,
                "nodes": nodes[offset : offset + limit],
                "edges": edges,
                "total": graph["total"],
                "offset": offset,
                "limit": limit,
                "truncated": graph["truncated"],
            }
        )

    def _search_node_types(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        queries, is_batch = _query_values(arguments)
        requested_contexts = {str(value).casefold() for value in arguments.get("contexts", [])}
        include_deprecated = bool(arguments.get("include_deprecated", False))
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        catalog = self._node_type_catalog(requested_contexts, include_deprecated)
        if not is_batch:
            matches = self._filter_node_type_catalog(catalog, queries[0])
            return self._success(
                {
                    "node_types": matches[offset : offset + limit],
                    "total": len(matches),
                    "offset": offset,
                    "limit": limit,
                }
            )

        query_results = []
        merged = []
        merged_keys: set[tuple[str, str]] = set()
        for query in queries:
            matches = self._filter_node_type_catalog(catalog, query)
            page = matches[offset : offset + limit]
            query_results.append(
                {
                    "query": query,
                    "node_types": page,
                    "total": len(matches),
                    "offset": offset,
                    "limit": limit,
                }
            )
            for record in page:
                key = (
                    str(record.get("category", "")).casefold(),
                    str(record.get("name", "")).casefold(),
                )
                if key not in merged_keys:
                    merged_keys.add(key)
                    merged.append(record)
        return self._success(
            {
                "queries": query_results,
                "query_count": len(query_results),
                "node_types": merged,
                "total": len(merged),
            }
        )

    def _node_help(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        requests = arguments.get("requests")
        if requests is None:
            return self._success(self._node_help_result(arguments))
        if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_BATCH_QUERIES:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                f"requests must contain between 1 and {MAX_BATCH_QUERIES} objects",
            )
        shared_keys = {
            "node_path",
            "category",
            "node_type",
            "include_parameters",
            "parameter_query",
            "offset",
            "limit",
        }
        unexpected_keys = set(arguments) - shared_keys - {"requests"}
        if unexpected_keys:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Unknown batch node help options",
                {"keys": sorted(unexpected_keys)},
            )
        shared_defaults = {
            key: arguments[key] for key in shared_keys if key in arguments
        }
        results = []
        error_count = 0
        for index, request in enumerate(requests):
            if not isinstance(request, Mapping):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Each requests item must be an object",
                    {"index": index},
                )
            effective_request = dict(shared_defaults)
            effective_request.update(request)
            try:
                results.append(
                    {
                        "index": index,
                        "request": effective_request,
                        "ok": True,
                        "result": self._node_help_result(effective_request),
                    }
                )
            except HiaRuntimeError as exc:
                error_count += 1
                results.append(
                    {
                        "index": index,
                        "request": effective_request,
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                    }
                )
        return self._success(
            {
                "results": results,
                "request_count": len(results),
                "ok_count": len(results) - error_count,
                "error_count": error_count,
            }
        )

    def _node_help_result(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        node_type = None
        node = None
        node_path = str(arguments.get("node_path", ""))
        if node_path:
            node = self._hou.node(node_path)
            if node is None:
                raise HiaRuntimeError("NODE_NOT_FOUND", "The requested Houdini node does not exist", {"path": node_path})
            node_type = node.type()
        else:
            category_name = str(arguments.get("category", "")).strip()
            type_name = str(arguments.get("node_type", "")).strip()
            if "/" in type_name:
                category_prefix, bare_type_name = (
                    part.strip() for part in type_name.split("/", 1)
                )
                if not category_prefix or not bare_type_name:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Qualified node_type must use non-empty Category/name segments",
                        {"category": category_name, "node_type": type_name},
                    )
                if (
                    category_name
                    and category_name.casefold() != category_prefix.casefold()
                ):
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "category conflicts with the node_type category prefix",
                        {
                            "category": category_name,
                            "node_type_category": category_prefix,
                        },
                    )
                category_name = category_name or category_prefix
                type_name = bare_type_name
            if not category_name or not type_name:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Provide node_path, category plus node_type, or node_type as Category/name",
                )
            categories = self._hou.nodeTypeCategories()
            category = next(
                (value for key, value in categories.items() if str(key).casefold() == category_name.casefold()),
                None,
            )
            if category is not None:
                node_type = category.nodeTypes().get(type_name)
                if node_type is None:
                    node_type = next(
                        (value for key, value in category.nodeTypes().items() if str(key).casefold() == type_name.casefold()),
                        None,
                    )
        if node_type is None:
            raise HiaRuntimeError("NODE_TYPE_NOT_FOUND", "The installed Houdini node type was not found")
        parameter_query = str(arguments.get("parameter_query", "")).casefold()
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        parameters = []
        if bool(arguments.get("include_parameters", True)):
            parameters = self._parameter_templates(
                node_type,
                parameter_query,
                node=node,
            )
        category = _safe_call(node_type, "category", None)
        definition = _safe_call(node_type, "definition", None)
        result = {
            "category": _safe_name(category),
            "name": str(_safe_call(node_type, "name", "")),
            "name_components": _json_value(_safe_call(node_type, "nameComponents", ())),
            "description": str(_safe_call(node_type, "description", "")),
            "min_inputs": _json_value(_safe_call(node_type, "minNumInputs", None)),
            "max_inputs": _json_value(_safe_call(node_type, "maxNumInputs", None)),
            "max_outputs": _json_value(_safe_call(node_type, "maxNumOutputs", None)),
            "child_context": _safe_name(_safe_call(node_type, "childTypeCategory", None)),
            "deprecated": bool(_safe_call(node_type, "deprecated", False)),
            "help_url": str(_safe_call(node_type, "helpUrl", "")),
            "source_path": _redact_path(str(_safe_call(node_type, "sourcePath", ""))),
            "definition_library": _redact_path(str(_safe_call(definition, "libraryFilePath", ""))),
            "node_path": node_path or None,
            "parameter_name_mode": (
                "runtime_instances" if node is not None else "template_patterns"
            ),
            "parameters": parameters[offset : offset + limit],
            "parameter_total": len(parameters),
            "offset": offset,
            "limit": limit,
        }
        return result

    def _geometry_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        nodes, missing_paths = self._resolve_nodes_with_missing(arguments)
        limit = _limit(arguments)
        include_attributes = bool(arguments.get("include_attributes", True))
        sample_limit = _bounded_int(arguments.get("sample_limit", 0), 0, 100)
        records = [
            self._geometry_record(node, include_attributes=include_attributes, sample_limit=sample_limit)
            for node in nodes[:limit]
        ]
        return self._success(
            {
                "geometry": records,
                "total": len(nodes),
                "missing_paths": missing_paths,
                "limit": limit,
            },
            warnings=(
                [
                    "Some requested geometry paths do not exist; existing "
                    "paths were still summarized"
                ]
                if missing_paths
                else None
            ),
        )

    def _material_render_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        roots = arguments.get("root_paths") or ["/mat", "/shop", "/out", "/stage"]
        query = str(arguments.get("query", "")).casefold()
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        records = []
        for node in self._nodes_below_paths(roots, maximum=10_000):
            type_info = self._type_record(node)
            haystack = f"{_safe_path(node)} {type_info['name']} {type_info['category']}".casefold()
            parameter_refs = self._interesting_parameters(
                node,
                ("material", "shop", "texture", "file", "image", "render", "camera", "karma", "output", "resolution"),
            )
            if query and query not in haystack and not any(query in json.dumps(item).casefold() for item in parameter_refs):
                continue
            records.append(
                {
                    "path": _safe_path(node),
                    "type": type_info,
                    "parameters": parameter_refs,
                    "inputs": [_safe_path(value) if value is not None else None for value in _safe_call(node, "inputs", ())],
                    "errors": list(_safe_call(node, "errors", ())),
                    "warnings": list(_safe_call(node, "warnings", ())),
                    "time_dependent": bool(_safe_call(node, "isTimeDependent", False)),
                }
            )
        records.sort(key=lambda item: item["path"])
        return self._success({"nodes": records[offset : offset + limit], "total": len(records), "offset": offset, "limit": limit})

    def _solaris_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("lop_path") or self._current_node_path())
        node = self._hou.node(path) if path else None
        if node is None:
            raise HiaRuntimeError("NODE_NOT_FOUND", "A LOP node is required for Solaris inspection", {"path": path})
        stage_method = getattr(node, "stage", None)
        if not callable(stage_method):
            raise HiaRuntimeError("USD_STAGE_UNAVAILABLE", "The selected node does not expose a USD stage")
        try:
            stage = stage_method()
        except Exception as exc:
            raise HiaRuntimeError("USD_STAGE_ERROR", _bounded_text(_redact_text(str(exc)), 2048)) from exc
        if stage is None:
            raise HiaRuntimeError("USD_STAGE_UNAVAILABLE", "The LOP node returned no composed USD stage")
        prim_path = str(arguments.get("prim_path", ""))
        query = str(arguments.get("query", "")).casefold()
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        if prim_path:
            root_prim = stage.GetPrimAtPath(prim_path)
            iterator: Iterable[Any] = (
                _usd_prim_subtree(root_prim) if root_prim else ()
            )
        else:
            iterator = stage.Traverse()
        prims = []
        for prim in iterator:
            record = {
                "path": str(prim.GetPath()),
                "type": str(prim.GetTypeName()),
                "active": bool(prim.IsActive()),
                "loaded": bool(prim.IsLoaded()),
                "instance": bool(prim.IsInstance()),
                "instance_proxy": bool(prim.IsInstanceProxy()),
                "variant_sets": list(prim.GetVariantSets().GetNames()),
                "material_bindings": [
                    str(rel.GetTargets()[0]) if rel.GetTargets() else ""
                    for rel in prim.GetRelationships()
                    if "material:binding" in str(rel.GetName())
                ],
            }
            if query and query not in json.dumps(record, ensure_ascii=False).casefold():
                continue
            prims.append(record)
            if len(prims) >= offset + limit + 1:
                break
        layers = []
        for layer in list(stage.GetLayerStack())[:100]:
            layers.append(
                {
                    "identifier": _redact_path(str(getattr(layer, "identifier", ""))),
                    "anonymous": bool(getattr(layer, "anonymous", False)),
                    "dirty": bool(getattr(layer, "dirty", False)),
                }
            )
        return self._success(
            {
                "lop_path": path,
                "prims": prims[offset : offset + limit],
                "has_more": len(prims) > offset + limit,
                "offset": offset,
                "limit": limit,
                "layers": layers,
                "errors": list(_safe_call(node, "errors", ())),
                "warnings": list(_safe_call(node, "warnings", ())),
            }
        )

    def _animation_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        nodes, missing_paths = self._resolve_nodes_with_missing(arguments)
        include_static = bool(arguments.get("include_static", False))
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        channels = []
        for node in nodes:
            for parm in _safe_call(node, "parms", ()):
                keyframes = list(_safe_call(parm, "keyframes", ()))
                time_dependent = bool(_safe_call(parm, "isTimeDependent", False))
                expression = ""
                try:
                    expression = str(parm.expression())
                except Exception:
                    pass
                if not include_static and not keyframes and not time_dependent and not expression:
                    continue
                channels.append(
                    {
                        "node_path": _safe_path(node),
                        "parameter": str(_safe_call(parm, "name", "")),
                        "time_dependent": time_dependent,
                        "expression": _bounded_text(expression, 4096),
                        "keyframes": [self._keyframe_record(value) for value in keyframes[:500]],
                        "value": _json_value(_safe_parm_value(parm)),
                    }
                )
        take = _safe_call(getattr(self._hou, "takes", None), "currentTake", None)
        frame_range, playback_range = _houdini_frame_ranges(self._hou)
        return self._success(
            {
                "channels": channels[offset : offset + limit],
                "total": len(channels),
                "missing_paths": missing_paths,
                "offset": offset,
                "limit": limit,
                "frame": _json_value(_safe_call(self._hou, "frame", 0.0)),
                "frame_range": _json_value(frame_range),
                "playbar_range": _json_value(playback_range),
                "take": _safe_name(take),
            },
            warnings=(
                [
                    "Some requested animation paths do not exist; existing "
                    "paths were still summarized"
                ]
                if missing_paths
                else None
            ),
        )

    def _simulation_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        roots = arguments.get("root_paths") or ["/obj", "/stage", "/out"]
        query = str(arguments.get("query", "")).casefold()
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _limit(arguments)
        records = []
        classification_terms = ("dop", "vellum", "pyro", "flip", "rbd", "solver", "cache", "sim")
        for node in self._nodes_below_paths(roots, maximum=10_000):
            type_info = self._type_record(node)
            haystack = f"{_safe_path(node)} {type_info['name']} {type_info['category']}".casefold()
            time_dependent = bool(_safe_call(node, "isTimeDependent", False))
            cache_parameters = self._interesting_parameters(node, ("cache", "file", "checkpoint", "substep", "start", "end", "memory"))
            if not any(term in haystack for term in classification_terms) and not cache_parameters and not time_dependent:
                continue
            record = {
                "path": _safe_path(node),
                "type": type_info,
                "time_dependent": time_dependent,
                "cache_parameters": cache_parameters,
                "errors": list(_safe_call(node, "errors", ())),
                "warnings": list(_safe_call(node, "warnings", ())),
                "cook_count": _json_value(_safe_call(node, "cookCount", None)),
                "cook_time": _json_value(_safe_call(node, "cookTime", None)),
            }
            if query and query not in json.dumps(record, ensure_ascii=False).casefold():
                continue
            records.append(record)
        records.sort(key=lambda item: item["path"])
        return self._success({"nodes": records[offset : offset + limit], "total": len(records), "offset": offset, "limit": limit})

    def _validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        limit = _limit(arguments)
        query = str(arguments.get("query", "")).casefold()
        requested_paths = self._absolute_node_paths(
            arguments.get("paths") or [],
            field_name="paths",
            maximum=MAX_SNAPSHOT_NODES,
        )
        paths = list(requested_paths)
        root_path = str(arguments.get("root_path", ""))
        root = None
        if root_path:
            root = self._hou.node(root_path)
            if root is None:
                raise HiaRuntimeError("NODE_NOT_FOUND", "Validation root does not exist", {"path": root_path})
            if not paths:
                pending_nodes = [root]
                while pending_nodes and len(paths) < limit:
                    node = pending_nodes.pop(0)
                    node_path = _safe_path(node)
                    if node_path and node_path not in paths:
                        paths.append(node_path)
                    remaining = limit - len(paths)
                    if remaining > 0:
                        pending_nodes.extend(
                            list(_safe_call(node, "children", ()))[:remaining]
                        )
        semantic_checks = self._semantic_checks(
            arguments.get("semantic_checks") or []
        )
        semantic_paths = self._semantic_check_paths(semantic_checks)
        paths.extend(path for path in semantic_paths if path not in paths)
        if not paths:
            paths = [_safe_path(node) for node in _safe_call(self._hou, "selectedNodes", ())]
        paths = self._absolute_node_paths(
            paths,
            field_name="paths",
            maximum=MAX_SNAPSHOT_NODES,
        )
        expected = self._absolute_node_paths(
            arguments.get("expected_paths") or [],
            field_name="expected_paths",
            maximum=64,
        )
        if not paths and not expected:
            raise HiaRuntimeError(
                "VALIDATION_TARGET_REQUIRED",
                "Validation requires paths, root_path, semantic check paths, expected_paths, or a current selection",
            )
        changed_paths = self._absolute_node_paths(
            arguments.get("changed_paths") or [],
            field_name="changed_paths",
            maximum=64,
        )
        protected_paths = self._absolute_node_paths(
            arguments.get("protected_paths") or [],
            field_name="protected_paths",
            maximum=64,
        )
        mutable_root = self._optional_node_path(
            arguments.get("mutable_root"),
            field_name="mutable_root",
        )
        checks = self._validation_check_names(
            arguments.get("checks"),
            default=("node_errors", "critical_paths"),
        )
        if bool(arguments.get("cook", False)) and "node_errors" not in checks:
            checks.append("node_errors")
        if expected and "critical_paths" not in checks:
            checks.append("critical_paths")
        if semantic_checks and "semantic_expectations" not in checks:
            checks.append("semantic_expectations")
        if "cook" in arguments:
            cook_requested = bool(arguments["cook"])
        else:
            cook_requested = bool(
                {"empty_output", "geometry_summary", "semantic_expectations"}
                .intersection(checks)
            )
        validated_path_limit = max(limit, min(len(semantic_paths), 64))
        validated_paths = list(
            dict.fromkeys([*semantic_paths, *paths])
        )[:validated_path_limit]
        explicit_output_paths = list(
            dict.fromkeys([*requested_paths, *expected])
        )
        if not explicit_output_paths and not root_path:
            explicit_output_paths = list(validated_paths)
        elif not explicit_output_paths and root is not None:
            category = str(self._type_record(root).get("category") or "")
            if category.casefold() == "sop":
                explicit_output_paths = [root_path]
        validation = self._run_domain_validation(
            paths=validated_paths,
            checks=checks,
            cook=cook_requested,
            expected_paths=expected,
            explicit_output_paths=explicit_output_paths,
            changed_paths=changed_paths,
            mutable_root=mutable_root,
            protected_paths=protected_paths,
            semantic_checks=semantic_checks,
            finding_limit=limit,
        )
        node_check = next(
            (item for item in validation["check_results"] if item["check"] == "node_errors"),
            {"findings": [], "finding_count": 0, "evidence": {}},
        )
        findings = list(node_check["findings"])
        if query:
            findings = [
                item for item in findings
                if query in json.dumps(item, ensure_ascii=False).casefold()
            ]
            finding_total = len(findings)
            counts = {
                severity: sum(item["severity"] == severity[:-1] for item in findings)
                for severity in ("errors", "warnings")
            }
        else:
            finding_total = int(node_check["finding_count"])
            evidence = node_check["evidence"]
            counts = {
                "errors": int(evidence.get("errors", 0)),
                "warnings": int(evidence.get("warnings", 0)),
            }
        result = {
            "valid": validation["valid"],
            "complete": validation["complete"],
            "findings": findings[:limit],
            "finding_total": finding_total,
            "missing_expected_paths": validation["missing_expected_paths"],
            "counts": counts,
            "cooked": bool(
                cook_requested
                and validation["cook_cache_evidence"]["assessment"]
                == "recompute_verified"
            ),
            "cook_requested": cook_requested,
            "freshness": validation["cook_cache_evidence"]["assessment"],
            "cook_cache_evidence": validation["cook_cache_evidence"],
            "messages": validation["messages"],
            "check_results": validation["check_results"],
            "check_summary": validation["check_summary"],
        }
        evidence_paths = list(
            dict.fromkeys(
                [
                    *validated_paths,
                    *expected,
                    *changed_paths,
                    *protected_paths,
                    *semantic_paths,
                    *([mutable_root] if mutable_root else []),
                ]
            )
        )
        evidence_path_total = len(
            dict.fromkeys(
                [
                    *paths,
                    *expected,
                    *changed_paths,
                    *protected_paths,
                    *semantic_paths,
                    *([mutable_root] if mutable_root else []),
                ]
            )
        )
        self._remember_evidence(
            {
                "kind": "validation",
                "timestamp": _utc_now(),
                "paths": evidence_paths,
                "path_count": evidence_path_total,
                "status": (
                    "passed"
                    if validation["valid"] and validation["complete"]
                    else ("partial" if validation["valid"] else "failed")
                ),
                "complete": validation["complete"],
                "checks": [
                    {
                        "check": item["check"],
                        "status": item["status"],
                        "finding_count": item["finding_count"],
                    }
                    for item in validation["check_results"]
                ],
                "error_codes": [
                    finding["code"]
                    for item in validation["check_results"]
                    for finding in item["findings"]
                    if finding["severity"] == "error"
                ][:32],
            }
        )
        return self._success(result)

    def _run_domain_validation(
        self,
        *,
        paths: list[str],
        checks: list[str],
        cook: bool,
        expected_paths: list[str],
        explicit_output_paths: list[str],
        changed_paths: list[str],
        mutable_root: str,
        protected_paths: list[str],
        semantic_checks: list[dict[str, Any]],
        finding_limit: int,
        scope_complete: bool = False,
        change_provenance: str = "caller_declared",
    ) -> dict[str, Any]:
        nodes = {path: self._hou.node(path) for path in paths}
        missing_expected_paths = [
            path for path in expected_paths if self._hou.node(path) is None
        ]
        frame = _json_value(_safe_call(self._hou, "frame", None))
        cook_records: list[dict[str, Any]] = []
        cook_errors: dict[str, str] = {}
        for path, node in nodes.items():
            if node is None:
                cook_records.append(
                    {
                        "path": path,
                        "frame": frame,
                        "state": "unavailable",
                        "reason": "node_not_found",
                        "recompute": "recompute_not_proven",
                    }
                )
                continue
            before = self._cook_state(node)
            cook_started = False
            cook_completed = False
            if cook:
                cook_started = True
                try:
                    node.cook(force=True)
                    cook_completed = True
                except Exception as exc:
                    cook_errors[path] = _bounded_text(
                        _redact_text(str(exc)), 2048
                    )
            after = self._cook_state(node) if cook_started else dict(before)
            cook_records.append(
                self._cook_evidence_record(
                    path=path,
                    frame=frame,
                    requested=cook,
                    started=cook_started,
                    completed=cook_completed,
                    before=before,
                    after=after,
                    error=cook_errors.get(path),
                )
            )
        cook_cache_evidence = self._summarize_cook_evidence(
            cook_records,
            requested=cook,
        )
        geometry_paths = set(
            paths[:MAX_VALIDATION_GEOMETRY_PATHS]
            if {"empty_output", "geometry_summary"}.intersection(checks)
            else ()
        )
        geometry_cache: dict[str, dict[str, Any]] = {}

        def geometry_record(path: str) -> dict[str, Any]:
            cached = geometry_cache.get(path)
            if cached is not None:
                return cached
            node = nodes[path]
            if node is None:
                record = {"node_path": path, "available": False}
            elif path in geometry_paths:
                record = self._geometry_record(
                    node,
                    include_attributes=False,
                    sample_limit=0,
                    allow_cook=cook,
                )
            else:
                record = {
                    "node_path": path,
                    "available": False,
                    "reason": "geometry_limit",
                }
            geometry_cache[path] = record
            return record

        check_results: list[dict[str, Any]] = []

        def add_result(
            name: str,
            status: str,
            findings: list[dict[str, Any]],
            evidence: Mapping[str, Any],
            *,
            total: int | None = None,
            truncated: bool = False,
        ) -> None:
            finding_total = len(findings) if total is None else total
            check_results.append({
                "check": name, "status": status, "finding_count": finding_total,
                "findings": findings[:finding_limit], "evidence": _json_value(evidence),
                "truncated": truncated or finding_total > finding_limit,
            })

        for check in checks:
            if check == "node_errors":
                findings: list[dict[str, Any]] = []
                totals = {"errors": 0, "warnings": 0, "findings": 0}
                interrupted_paths: list[str] = []
                interrupted_message = ""
                root_cause = ""
                explicit_outputs = {
                    path
                    for path in explicit_output_paths
                    if nodes.get(path) is not None
                }
                active_output_paths: set[str] = set()
                pending = [nodes[path] for path in explicit_outputs]
                while pending and len(active_output_paths) < 256:
                    active_node = pending.pop()
                    active_path = _safe_path(active_node)
                    if not active_path or active_path in active_output_paths:
                        continue
                    active_output_paths.add(active_path)
                    pending.extend(
                        value
                        for value in _safe_call(active_node, "inputs", ())
                        if value is not None
                    )
                error_source_nodes = dict(nodes)
                descendant_queue = [
                    child
                    for path, node in nodes.items()
                    if node is not None and path not in explicit_outputs
                    for child in _safe_call(node, "children", ())
                ]
                while descendant_queue and len(error_source_nodes) < 256:
                    descendant = descendant_queue.pop()
                    descendant_path = _safe_path(descendant)
                    if (
                        not descendant_path
                        or descendant_path in error_source_nodes
                    ):
                        continue
                    error_source_nodes[descendant_path] = descendant
                    descendant_queue.extend(
                        _safe_call(descendant, "children", ())
                    )
                message_cache = {
                    path: {
                        "errors": [
                            str(value)
                            for value in list(_safe_call(node, "errors", ()))[:32]
                        ],
                        "warnings": [
                            str(value)
                            for value in list(_safe_call(node, "warnings", ()))[:32]
                        ],
                    }
                    for path, node in error_source_nodes.items()
                    if node is not None
                }
                explicit_outputs_clean = bool(explicit_outputs) and all(
                    not message_cache[path]["errors"]
                    and not message_cache[path]["warnings"]
                    for path in explicit_outputs
                )
                dormant_locked_paths = {
                    path
                    for path, node in error_source_nodes.items()
                    if (
                        node is not None
                        and explicit_outputs_clean
                        and path not in explicit_outputs
                        and path not in active_output_paths
                        and _safe_call(node, "isInsideLockedHDA", None) is True
                        and not bool(_safe_call(node, "isDisplayFlagSet", False))
                        and not bool(_safe_call(node, "isRenderFlagSet", False))
                    )
                }
                dormant_messages = {
                    (severity, " ".join(message.split()).casefold())
                    for path in dormant_locked_paths
                    for severity in ("errors", "warnings")
                    for message in message_cache[path][severity]
                }
                ignored_dormant = 0
                ignored_parent_duplicates = 0

                def note(path: str, severity: str, code: str, message: str) -> None:
                    totals["findings"] += 1
                    totals["errors" if severity == "error" else "warnings"] += 1
                    if len(findings) < finding_limit:
                        findings.append(self._finding(path, severity, code, message))

                for path, node in nodes.items():
                    if node is None:
                        note(path, "error", "NODE_NOT_FOUND", "Node does not exist")
                        continue
                    if path in dormant_locked_paths:
                        ignored_dormant += (
                            len(message_cache[path]["errors"])
                            + len(message_cache[path]["warnings"])
                            + int(bool(cook_errors.get(path)))
                        )
                        continue
                    cook_error = cook_errors.get(path)
                    if cook_error:
                        if _is_cooking_interrupted_message(cook_error):
                            interrupted_paths.append(path)
                            interrupted_message = interrupted_message or cook_error
                        else:
                            root_cause = root_cause or cook_error
                            note(path, "error", "COOK_FAILED", cook_error)
                    for text in message_cache[path]["errors"]:
                        if (
                            path not in explicit_outputs
                            and path not in active_output_paths
                            and (
                                "errors",
                                " ".join(text.split()).casefold(),
                            )
                            in dormant_messages
                        ):
                            ignored_parent_duplicates += 1
                            continue
                        if _is_cooking_interrupted_message(text):
                            interrupted_paths.append(path)
                            interrupted_message = interrupted_message or text
                        else:
                            root_cause = root_cause or text
                            note(path, "error", "NODE_ERROR", text)
                    for text in message_cache[path]["warnings"]:
                        if (
                            path not in explicit_outputs
                            and path not in active_output_paths
                            and (
                                "warnings",
                                " ".join(text.split()).casefold(),
                            )
                            in dormant_messages
                        ):
                            ignored_parent_duplicates += 1
                            continue
                        note(path, "warning", "NODE_WARNING", text)
                    minimum = _safe_call(_safe_call(node, "type", None), "minNumInputs", 0)
                    connected = sum(value is not None for value in _safe_call(node, "inputs", ()))
                    if isinstance(minimum, int) and minimum > connected:
                        note(path, "error", "MISSING_INPUT", "Required input slots are unconnected")
                interrupted_paths = list(dict.fromkeys(interrupted_paths))
                interrupted_evidence = None
                if interrupted_paths:
                    representative_path = interrupted_paths[0]
                    affected_count = len(interrupted_paths)
                    cause = root_cause or interrupted_message or "Cooking was interrupted"
                    note(
                        representative_path,
                        "error",
                        "COOK_INTERRUPTED",
                        (
                            f"{cause}; Cooking was interrupted on {affected_count} "
                            f"node(s), representative path: {representative_path}"
                        ),
                    )
                    interrupted_evidence = {
                        "root_cause": _bounded_text(_redact_text(cause), 2048),
                        "representative_path": representative_path,
                        "affected_count": affected_count,
                    }
                add_result(
                    check,
                    "fail" if totals["errors"] else ("pass" if nodes else "skipped"),
                    findings,
                    {
                        "paths_checked": len(nodes), "cook_requested": cook,
                        "errors": totals["errors"], "warnings": totals["warnings"],
                        "interrupted": interrupted_evidence,
                        "ignored_dormant_locked_asset_internal": {
                            "paths": sorted(dormant_locked_paths)[:32],
                            "message_count": ignored_dormant,
                        },
                        "ignored_parent_aggregate_duplicates": (
                            ignored_parent_duplicates
                        ),
                    },
                    total=totals["findings"],
                )
                continue

            if check == "empty_output":
                findings = []
                summaries = []
                unavailable = 0
                explicit_candidates = [
                    path
                    for path in dict.fromkeys(explicit_output_paths)
                    if path in nodes and nodes[path] is not None
                ]
                output_paths = [
                    path
                    for path in explicit_candidates
                    if str(
                        self._type_record(nodes[path]).get("category") or ""
                    ).casefold()
                    == "sop"
                ]
                ignored_explicit = [
                    {
                        "path": path,
                        "category": self._type_record(nodes[path]).get(
                            "category"
                        ),
                        "reason": "unsupported_node_category",
                    }
                    for path in explicit_candidates
                    if path not in output_paths
                ]
                if not explicit_output_paths and not output_paths:
                    for path, node in nodes.items():
                        if node is None:
                            continue
                        type_info = self._type_record(node)
                        if str(type_info.get("category") or "").casefold() != "sop":
                            continue
                        name = _safe_name(node).upper()
                        if (
                            name.startswith("OUT_")
                            or bool(_safe_call(node, "isDisplayFlagSet", False))
                            or bool(_safe_call(node, "isRenderFlagSet", False))
                        ):
                            output_paths.append(path)
                for path in output_paths:
                    record = geometry_record(path)
                    numeric = [
                        record.get(name)
                        for name in ("point_count", "vertex_count", "primitive_count")
                        if isinstance(record.get(name), (int, float))
                    ]
                    if not record["available"] or not numeric:
                        unavailable += 1
                    elif all(value == 0 for value in numeric):
                        summaries.append({**record, "empty": True})
                        findings.append(self._finding(
                            path, "error", "EMPTY_OUTPUT",
                            "Geometry output contains no points, vertices, or primitives",
                        ))
                    else:
                        summaries.append({**record, "empty": False})
                measurable = len(output_paths) - unavailable
                status = (
                    "fail"
                    if findings
                    else (
                        (
                            "unknown"
                            if explicit_output_paths
                            else "skipped"
                        )
                        if not output_paths
                        else (
                            ("partial" if measurable else "unknown")
                            if unavailable
                            else "pass"
                        )
                    )
                )
                add_result(check, status, findings, {
                    "geometry": summaries[:finding_limit],
                    "output_paths": output_paths,
                    "ignored_non_output_paths": len(nodes) - len(output_paths),
                    "ignored_non_output_path_details": ignored_explicit[
                        :finding_limit
                    ],
                    "measurable_paths": measurable,
                    "unavailable_paths": unavailable,
                })
                continue

            if check == "critical_paths":
                findings = [
                    self._finding(path, "error", "CRITICAL_PATH_MISSING", "Expected Houdini node path does not exist")
                    for path in missing_expected_paths
                ]
                add_result(
                    check,
                    "fail" if missing_expected_paths else ("pass" if expected_paths else "skipped"),
                    findings,
                    {
                        "expected_paths": expected_paths,
                        "missing_paths": missing_expected_paths,
                    },
                )
                continue

            if check == "geometry_summary":
                selected = paths[:MAX_VALIDATION_GEOMETRY_PATHS]
                records = []
                for path in selected:
                    record = geometry_record(path)
                    records.append({
                        name: _json_value(record.get(name))
                        for name in (
                            "node_path", "available", "category",
                            "point_count", "vertex_count",
                            "primitive_count", "bbox", "primitive_kinds",
                            "packed_primitive_count", "volume_primitive_count",
                            "topology", "reason", "error", "errors",
                        ) if name in record
                    })
                ignored_unsupported = [
                    record
                    for record in records
                    if record.get("reason") == "unsupported_node_category"
                ]
                observed_records = [
                    record
                    for record in records
                    if record.get("reason") != "unsupported_node_category"
                ]
                measurable = sum(
                    bool(record.get("available"))
                    and any(
                        isinstance(record.get(name), (int, float))
                        for name in (
                            "point_count",
                            "vertex_count",
                            "primitive_count",
                        )
                    )
                    for record in observed_records
                )
                unavailable = len(observed_records) - measurable
                truncated = len(paths) > len(selected)
                status = (
                    "skipped"
                    if not records
                    else (
                        "pass"
                        if measurable and not unavailable and not truncated
                        else ("partial" if measurable else "unknown")
                    )
                )
                add_result(check, status, [], {
                    "geometry": records, "available_paths": measurable,
                    "unavailable_paths": unavailable,
                    "ignored_unsupported_paths": len(ignored_unsupported),
                    "total_paths": len(paths),
                }, truncated=truncated)
                continue

            if check == "semantic_expectations":
                expectation_results = []
                findings = []
                semantic_geometry_cache: dict[str, dict[str, Any]] = {}
                for expectation in semantic_checks:
                    result = self._evaluate_semantic_expectation(
                        expectation,
                        cook=cook,
                        geometry_cache=semantic_geometry_cache,
                    )
                    expectation_results.append(result)
                    findings.extend(result["findings"])
                expectation_statuses = {
                    status: sum(
                        result["status"] == status
                        for result in expectation_results
                    )
                    for status in ("pass", "fail", "unsupported", "unknown")
                }
                status = (
                    "skipped"
                    if not expectation_results
                    else (
                        "fail"
                        if expectation_statuses["fail"]
                        else (
                            "unsupported"
                            if expectation_statuses["unsupported"]
                            else (
                                "unknown"
                                if expectation_statuses["unknown"]
                                else "pass"
                            )
                        )
                    )
                )
                add_result(
                    check,
                    status,
                    findings,
                    {
                        "expectations": expectation_results,
                        "summary": expectation_statuses,
                    },
                )
                continue

            findings = []
            for path in changed_paths:
                protected = next(
                    (root for root in protected_paths if _houdini_path_is_within(path, root)),
                    "",
                )
                if protected:
                    findings.append(self._finding(
                        path, "error", "PROTECTED_PATH_CHANGED",
                        f"Observed change is inside protected path {protected}",
                    ))
                elif mutable_root and not _houdini_path_is_within(path, mutable_root):
                    findings.append(self._finding(
                        path, "error", "OUTSIDE_MUTABLE_ROOT",
                        f"Observed change is outside mutable root {mutable_root}",
                    ))
            scope_state = (
                "scope_violation"
                if findings
                else (
                    "observed_no_out_of_scope_change"
                    if scope_complete
                    else "scope_not_observable"
                )
            )
            evidence = {
                "mutable_root": mutable_root or None,
                "protected_paths": protected_paths,
                "scope_state": scope_state,
                "scope_complete": scope_complete,
                "change_provenance": change_provenance,
                "observed_changed_paths": (
                    changed_paths if change_provenance == "observed" else []
                ),
                "declared_changed_paths": (
                    changed_paths
                    if change_provenance == "caller_declared"
                    else []
                ),
            }
            add_result(
                check,
                (
                    "fail"
                    if findings
                    else ("pass" if scope_complete else "unknown")
                ),
                findings,
                evidence,
            )

        statuses = {
            status: sum(result["status"] == status for result in check_results)
            for status in (
                "pass",
                "partial",
                "fail",
                "unsupported",
                "unknown",
                "skipped",
            )
        }
        messages = self._validation_messages(
            check_results,
            cook_cache_evidence,
        )
        return {
            "valid": statuses["fail"] == 0,
            "complete": (
                statuses["unknown"] == 0
                and statuses["partial"] == 0
                and statuses["unsupported"] == 0
                and (
                    cook_cache_evidence["assessment"] == "recompute_verified"
                    if cook
                    else cook_cache_evidence["assessment"] != "stale_cache_risk"
                )
            ),
            "missing_expected_paths": missing_expected_paths,
            "check_results": check_results,
            "check_summary": statuses,
            "cook_cache_evidence": cook_cache_evidence,
            "messages": messages,
        }

    @staticmethod
    def _finding(
        path: str,
        severity: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "severity": severity,
            "code": code,
            "message": _bounded_text(_redact_text(message), 2048),
        }

    @staticmethod
    def _cook_state(node: Any) -> dict[str, Any]:
        values: dict[str, Any] = {}
        unavailable: dict[str, str] = {}
        probes = (
            ("needs_to_cook", "needsToCook", (), {}),
            (
                "time_dependent_last_cook",
                "isTimeDependent",
                (),
                {"for_last_cook": True},
            ),
            ("cook_count", "cookCount", (), {}),
            ("last_cook_time_ms", "lastCookTime", (), {}),
        )
        for key, method_name, args, kwargs in probes:
            method = getattr(node, method_name, None)
            if not callable(method):
                values[key] = None
                unavailable[key] = "not_callable"
                continue
            try:
                value = method(*args, **kwargs)
                if key in {"needs_to_cook", "time_dependent_last_cook"}:
                    value = bool(value)
                elif key == "cook_count":
                    value = (
                        int(value)
                        if isinstance(value, int) and not isinstance(value, bool)
                        else None
                    )
                elif key == "last_cook_time_ms":
                    value = (
                        float(value)
                        if isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                        else None
                    )
                values[key] = value
                if value is None:
                    unavailable[key] = "invalid_result"
            except Exception as exc:
                values[key] = None
                unavailable[key] = _bounded_text(_redact_text(str(exc)), 512)
        return {"values": values, "unavailable": unavailable}

    @staticmethod
    def _cook_evidence_record(
        *,
        path: str,
        frame: Any,
        requested: bool,
        started: bool,
        completed: bool,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        error: str | None,
    ) -> dict[str, Any]:
        before_values = dict(before.get("values") or {})
        after_values = dict(after.get("values") or {})
        before_count = before_values.get("cook_count")
        after_count = after_values.get("cook_count")
        count_delta = (
            after_count - before_count
            if isinstance(before_count, int) and isinstance(after_count, int)
            else None
        )
        needs_before = before_values.get("needs_to_cook")
        if requested and completed and isinstance(count_delta, int) and count_delta > 0:
            recompute = "recompute_verified"
        elif not requested and needs_before is True:
            recompute = "stale_cache_risk"
        else:
            recompute = "recompute_not_proven"
        cache_hit = "not_requested"
        if requested:
            cache_hit = (
                "observed"
                if (
                    completed
                    and needs_before is False
                    and count_delta == 0
                )
                else "not_proven"
            )
        out_of_date = (
            "observed"
            if needs_before is True
            else ("not_observed" if needs_before is False else "unavailable")
        )
        return {
            "path": path,
            "frame": frame,
            "signal_provenance": "HOM_observed",
            "before": before_values,
            "after": after_values,
            "cook_requested": requested,
            "cook_started": "observed" if started else "not_requested",
            "cook_completed": completed,
            "cook_count_delta": count_delta,
            "evidence": {
                "reset": "not_observed",
                "cache_hit": cache_hit,
                "out_of_date": out_of_date,
                "dependency_invalidation": "not_proven",
            },
            "recompute": recompute,
            "unavailable": {
                "before": dict(before.get("unavailable") or {}),
                "after": dict(after.get("unavailable") or {}),
            },
            "error": error,
        }

    @staticmethod
    def _summarize_cook_evidence(
        records: list[dict[str, Any]],
        *,
        requested: bool,
    ) -> dict[str, Any]:
        states = [str(record.get("recompute") or "") for record in records]
        if "stale_cache_risk" in states:
            assessment = "stale_cache_risk"
        elif records and all(state == "recompute_verified" for state in states):
            assessment = "recompute_verified"
        else:
            assessment = "recompute_not_proven"
        return {
            "schema": "hia-cook-cache-evidence/1",
            "cook_requested": requested,
            "assessment": assessment,
            "counts": {
                state: states.count(state)
                for state in (
                    "recompute_verified",
                    "recompute_not_proven",
                    "stale_cache_risk",
                )
            },
            "targets": records[:MAX_COOK_EVIDENCE_PATHS],
            "target_count": len(records),
            "calls_started": sum(
                record.get("cook_started") == "observed" for record in records
            ),
            "calls_completed": sum(
                record.get("cook_completed") is True for record in records
            ),
            "truncated": len(records) > MAX_COOK_EVIDENCE_PATHS,
            "limitations": (
                "HOM exposes last-cook signals but not a universal reset, "
                "cache-hit, or dependency-invalidation event stream"
            ),
        }

    @staticmethod
    def _validation_messages(
        check_results: list[dict[str, Any]],
        cook_cache_evidence: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if cook_cache_evidence.get("assessment") == "stale_cache_risk":
            messages.append(
                {
                    "level": "warning",
                    "code": "STALE_CACHE_RISK",
                    "check": "cook_cache_evidence",
                    "message": (
                        "At least one target needed to cook while cook=false; "
                        "last-cook errors or data do not prove current output"
                    ),
                }
            )
        elif (
            cook_cache_evidence.get("cook_requested")
            and cook_cache_evidence.get("assessment") == "recompute_not_proven"
        ):
            messages.append(
                {
                    "level": "notice",
                    "code": "RECOMPUTE_NOT_PROVEN",
                    "check": "cook_cache_evidence",
                    "message": (
                        "Fresh cooking was requested, but the available HOM "
                        "signals do not prove that every target recomputed"
                    ),
                }
            )
        failure_codes = {
            "changed_scope": "SCOPE_VIOLATION",
            "semantic_expectations": "SEMANTIC_VALIDATION_FAILED",
        }
        for result in check_results:
            check = str(result.get("check") or "")
            status = str(result.get("status") or "")
            if status == "fail":
                messages.append(
                    {
                        "level": "error",
                        "code": failure_codes.get(
                            check, f"{check.upper()}_FAILED"
                        ),
                        "check": check,
                        "message": f"{check} reported one or more failures",
                    }
                )
            elif check == "changed_scope" and status == "unknown":
                messages.append(
                    {
                        "level": "notice",
                        "code": "SCOPE_NOT_OBSERVABLE",
                        "check": check,
                        "message": (
                            "The available targeted evidence cannot prove that "
                            "no out-of-scope change occurred"
                        ),
                    }
                )
            elif status == "unsupported":
                messages.append(
                    {
                        "level": "notice",
                        "code": f"{check.upper()}_UNSUPPORTED",
                        "check": check,
                        "message": (
                            f"{check} is not supported for at least one target "
                            "node category"
                        ),
                    }
                )
            elif status in {"unknown", "partial"}:
                messages.append(
                    {
                        "level": "notice",
                        "code": f"{check.upper()}_NOT_PROVEN",
                        "check": check,
                        "message": f"{check} could not be fully observed",
                    }
                )
        return messages[:16]

    def _semantic_checks(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", "semantic_checks must be an array"
            )
        if len(value) > MAX_SEMANTIC_CHECKS:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "semantic_checks exceeds the bounded expectation limit",
                {"limit": MAX_SEMANTIC_CHECKS},
            )
        normalized = []
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"semantic_checks[{index}] must be an object",
                )
            check_type = str(raw.get("type") or "").strip().casefold()
            if check_type not in {"presence", "sample", "mapping"}:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"semantic_checks[{index}].type is unsupported",
                )
            common = {"id", "type"}
            if check_type == "mapping":
                allowed = common | {"source", "target", "forbidden_targets"}
            else:
                allowed = common | {"path", "data_kind", "name", "owner"}
                if check_type == "sample":
                    allowed |= {
                        "finite",
                        "nonzero",
                        "min_magnitude",
                        "max_magnitude",
                        "sample_limit",
                    }
            unrelated = sorted(set(raw).difference(allowed))
            if unrelated:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"semantic_checks[{index}] contains unrelated fields",
                    {"fields": unrelated},
                )
            check_id = str(raw.get("id") or f"semantic-{index + 1}").strip()
            if not check_id or len(check_id) > 128:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"semantic_checks[{index}].id is invalid",
                )
            item: dict[str, Any] = {"id": check_id, "type": check_type}
            if check_type == "mapping":
                item["source"] = self._semantic_ref(
                    raw.get("source"),
                    field_name=f"semantic_checks[{index}].source",
                )
                item["target"] = self._semantic_ref(
                    raw.get("target"),
                    field_name=f"semantic_checks[{index}].target",
                )
                forbidden = raw.get("forbidden_targets") or []
                if not isinstance(forbidden, list) or len(forbidden) > 16:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        f"semantic_checks[{index}].forbidden_targets is invalid",
                    )
                item["forbidden_targets"] = [
                    self._semantic_ref(
                        reference,
                        field_name=(
                            f"semantic_checks[{index}]."
                            f"forbidden_targets[{ref_index}]"
                        ),
                    )
                    for ref_index, reference in enumerate(forbidden)
                ]
            else:
                item["reference"] = self._semantic_ref(
                    raw,
                    field_name=f"semantic_checks[{index}]",
                )
                if check_type == "sample":
                    finite = raw.get("finite", True)
                    nonzero = raw.get("nonzero", False)
                    if not isinstance(finite, bool) or not isinstance(
                        nonzero, bool
                    ):
                        raise HiaRuntimeError(
                            "INVALID_ARGUMENTS",
                            f"semantic_checks[{index}] sample flags must be booleans",
                        )
                    sample_limit = raw.get("sample_limit", 64)
                    if (
                        isinstance(sample_limit, bool)
                        or not isinstance(sample_limit, int)
                        or not 1 <= sample_limit <= MAX_SEMANTIC_SAMPLES
                    ):
                        raise HiaRuntimeError(
                            "INVALID_ARGUMENTS",
                            f"semantic_checks[{index}].sample_limit is invalid",
                        )
                    criteria: dict[str, Any] = {
                        "finite": finite,
                        "nonzero": nonzero,
                        "sample_limit": sample_limit,
                    }
                    for name in ("min_magnitude", "max_magnitude"):
                        if name not in raw:
                            continue
                        candidate = raw[name]
                        if (
                            isinstance(candidate, bool)
                            or not isinstance(candidate, (int, float))
                            or not math.isfinite(float(candidate))
                            or float(candidate) < 0
                        ):
                            raise HiaRuntimeError(
                                "INVALID_ARGUMENTS",
                                f"semantic_checks[{index}].{name} is invalid",
                            )
                        criteria[name] = float(candidate)
                    if (
                        "min_magnitude" in criteria
                        and "max_magnitude" in criteria
                        and criteria["min_magnitude"] > criteria["max_magnitude"]
                    ):
                        raise HiaRuntimeError(
                            "INVALID_ARGUMENTS",
                            f"semantic_checks[{index}] magnitude range is inverted",
                        )
                    item["criteria"] = criteria
            normalized.append(item)
        if len(self._semantic_check_paths(normalized)) > 64:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "semantic_checks references too many distinct Houdini nodes",
                {"limit": 64},
            )
        return normalized

    def _semantic_ref(self, value: Any, *, field_name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", f"{field_name} must define a data reference"
            )
        path = self._absolute_node_paths(
            [value.get("path")],
            field_name=f"{field_name}.path",
            maximum=1,
        )[0]
        data_kind = str(value.get("data_kind") or "").strip().casefold()
        if data_kind not in {"attribute", "volume", "field"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", f"{field_name}.data_kind is invalid"
            )
        name = str(value.get("name") or "").strip()
        if not name or len(name) > 256:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", f"{field_name}.name is invalid"
            )
        owner = str(value.get("owner") or "point").strip().casefold()
        if owner not in {"point", "primitive", "vertex", "detail"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS", f"{field_name}.owner is invalid"
            )
        return {
            "path": path,
            "data_kind": data_kind,
            "name": name,
            "owner": owner if data_kind == "attribute" else None,
        }

    @staticmethod
    def _semantic_check_paths(checks: list[dict[str, Any]]) -> list[str]:
        paths = []
        for check in checks:
            if check["type"] == "mapping":
                references = [
                    check["source"],
                    check["target"],
                    *check["forbidden_targets"],
                ]
            else:
                references = [check["reference"]]
            for reference in references:
                path = str(reference["path"])
                if path not in paths:
                    paths.append(path)
        return paths

    def _evaluate_semantic_expectation(
        self,
        expectation: Mapping[str, Any],
        *,
        cook: bool,
        geometry_cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        check_type = str(expectation["type"])
        check_id = str(expectation["id"])
        if check_type == "mapping":
            source = self._semantic_data_observation(
                expectation["source"],
                cook=cook,
                sample_limit=0,
                geometry_cache=geometry_cache,
            )
            target = self._semantic_data_observation(
                expectation["target"],
                cook=cook,
                sample_limit=0,
                geometry_cache=geometry_cache,
            )
            forbidden = [
                self._semantic_data_observation(
                    reference,
                    cook=cook,
                    sample_limit=0,
                    geometry_cache=geometry_cache,
                )
                for reference in expectation["forbidden_targets"]
            ]
            findings = []
            if source.get("state") == "observed" and source.get("exists") is False:
                findings.append(
                    self._finding(
                        expectation["source"]["path"],
                        "error",
                        "SEMANTIC_SOURCE_MISSING",
                        "The declared source field or attribute is missing",
                    )
                )
            if target.get("state") == "observed" and target.get("exists") is False:
                findings.append(
                    self._finding(
                        expectation["target"]["path"],
                        "error",
                        "SEMANTIC_TARGET_MISSING",
                        "The expected target field or attribute is missing",
                    )
                )
            for reference, observation in zip(
                expectation["forbidden_targets"], forbidden, strict=True
            ):
                if observation.get("state") == "observed" and observation.get(
                    "exists"
                ) is True:
                    findings.append(
                        self._finding(
                            reference["path"],
                            "error",
                            "FORBIDDEN_MAPPING_PRESENT",
                            (
                                "A field or attribute explicitly forbidden by "
                                "the mapping contract is present"
                            ),
                        )
                    )
            observations = [source, target, *forbidden]
            status = (
                "fail"
                if findings
                else (
                    "unsupported"
                    if any(
                        self._semantic_unavailable_status(item) == "unsupported"
                        for item in observations
                    )
                    else (
                        "unknown"
                        if any(
                            item.get("state") != "observed"
                            for item in observations
                        )
                        else "pass"
                    )
                )
            )
            return {
                "id": check_id,
                "type": check_type,
                "expectation_provenance": "caller_declared",
                "status": status,
                "mapping_proof": "presence_contract",
                "source": source,
                "target": target,
                "forbidden_targets": forbidden,
                "findings": findings,
            }

        reference = expectation["reference"]
        sample_limit = (
            int(expectation["criteria"]["sample_limit"])
            if check_type == "sample"
            else 0
        )
        observation = self._semantic_data_observation(
            reference,
            cook=cook,
            sample_limit=sample_limit,
            geometry_cache=geometry_cache,
        )
        findings = []
        if observation.get("state") != "observed":
            status = (
                self._semantic_unavailable_status(observation)
            )
        elif observation.get("exists") is False:
            findings.append(
                self._finding(
                    reference["path"],
                    "error",
                    "SEMANTIC_DATA_MISSING",
                    "The expected field, volume, or attribute is missing",
                )
            )
            status = "fail"
        elif check_type == "presence":
            status = "pass"
        else:
            criteria = expectation["criteria"]
            sample = observation.get("sample")
            if not isinstance(sample, Mapping) or sample.get("state") != "observed":
                status = self._semantic_unavailable_status(
                    sample if isinstance(sample, Mapping) else {}
                )
            else:
                if criteria["finite"] and not sample.get("all_finite"):
                    findings.append(
                        self._finding(
                            reference["path"],
                            "error",
                            "NONFINITE_SAMPLES",
                            "One or more bounded samples are NaN or infinite",
                        )
                    )
                if criteria["nonzero"] and sample.get("all_zero"):
                    findings.append(
                        self._finding(
                            reference["path"],
                            "error",
                            "ALL_ZERO_SAMPLES",
                            "All bounded numeric samples have zero magnitude",
                        )
                    )
                minimum = criteria.get("min_magnitude")
                observed_minimum = sample.get("min_magnitude")
                if (
                    isinstance(minimum, (int, float))
                    and isinstance(observed_minimum, (int, float))
                    and observed_minimum < minimum
                ):
                    findings.append(
                        self._finding(
                            reference["path"],
                            "error",
                            "MAGNITUDE_BELOW_MINIMUM",
                            "A bounded sample magnitude is below the expected minimum",
                        )
                    )
                maximum = criteria.get("max_magnitude")
                observed_maximum = sample.get("max_magnitude")
                if (
                    isinstance(maximum, (int, float))
                    and isinstance(observed_maximum, (int, float))
                    and observed_maximum > maximum
                ):
                    findings.append(
                        self._finding(
                            reference["path"],
                            "error",
                            "MAGNITUDE_ABOVE_MAXIMUM",
                            "A bounded sample magnitude exceeds the expected maximum",
                        )
                    )
                status = "fail" if findings else "pass"
        return {
            "id": check_id,
            "type": check_type,
            "expectation_provenance": "caller_declared",
            "status": status,
            "reference": reference,
            "criteria": expectation.get("criteria"),
            "observation": observation,
            "findings": findings,
        }

    @staticmethod
    def _semantic_unavailable_status(observation: Mapping[str, Any]) -> str:
        return (
            "unsupported"
            if str(observation.get("reason") or "")
            in _SEMANTIC_UNSUPPORTED_REASONS
            else "unknown"
        )

    def _semantic_data_observation(
        self,
        reference: Mapping[str, Any],
        *,
        cook: bool,
        sample_limit: int,
        geometry_cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        path = str(reference["path"])
        cached = geometry_cache.get(path)
        if cached is None:
            node = self._hou.node(path)
            if node is None:
                cached = {
                    "state": "observed",
                    "node_exists": False,
                    "geometry": None,
                    "reason": "node_not_found",
                }
            elif str(self._type_record(node).get("category") or "").casefold() != "sop":
                cached = {
                    "state": "unavailable",
                    "node_exists": True,
                    "geometry": None,
                    "reason": "unsupported_node_category",
                }
            else:
                needs_method = getattr(node, "needsToCook", None)
                if not callable(needs_method):
                    cached = {
                        "state": "unavailable",
                        "node_exists": True,
                        "geometry": None,
                        "reason": "needs_to_cook_unavailable",
                    }
                else:
                    try:
                        needs_to_cook = bool(needs_method())
                    except Exception as exc:
                        cached = {
                            "state": "unavailable",
                            "node_exists": True,
                            "geometry": None,
                            "reason": "needs_to_cook_failed",
                            "error": _bounded_text(_redact_text(str(exc)), 1024),
                        }
                    else:
                        if needs_to_cook:
                            cached = {
                                "state": "unavailable",
                                "node_exists": True,
                                "geometry": None,
                                "reason": (
                                    "cook_incomplete"
                                    if cook
                                    else "cook_not_requested"
                                ),
                            }
                        else:
                            geometry_method = getattr(node, "geometry", None)
                            if not callable(geometry_method):
                                cached = {
                                    "state": "unavailable",
                                    "node_exists": True,
                                    "geometry": None,
                                    "reason": "geometry_method_unavailable",
                                }
                            else:
                                try:
                                    geometry = geometry_method()
                                except Exception as exc:
                                    cached = {
                                        "state": "unavailable",
                                        "node_exists": True,
                                        "geometry": None,
                                        "reason": "geometry_unavailable",
                                        "error": _bounded_text(
                                            _redact_text(str(exc)), 1024
                                        ),
                                    }
                                else:
                                    cached = {
                                        "state": "observed",
                                        "node_exists": True,
                                        "geometry": geometry,
                                    }
            geometry_cache[path] = cached
        if not cached.get("node_exists", False):
            return {
                "state": "observed",
                "exists": False,
                "reason": cached.get("reason"),
            }
        if cached.get("state") != "observed":
            return {
                "state": "unavailable",
                "exists": None,
                "reason": cached.get("reason"),
                "error": cached.get("error"),
            }
        geometry = cached.get("geometry")
        kind = str(reference["data_kind"])
        name = str(reference["name"])
        if kind == "attribute":
            owner = str(reference["owner"])
            find_method = getattr(
                geometry,
                {
                    "point": "findPointAttrib",
                    "primitive": "findPrimAttrib",
                    "vertex": "findVertexAttrib",
                    "detail": "findGlobalAttrib",
                }[owner],
                None,
            )
            if not callable(find_method):
                return {
                    "state": "unavailable",
                    "exists": None,
                    "reason": "attribute_lookup_unavailable",
                }
            try:
                attribute = find_method(name)
            except Exception as exc:
                return {
                    "state": "unavailable",
                    "exists": None,
                    "reason": "attribute_lookup_failed",
                    "error": _bounded_text(_redact_text(str(exc)), 1024),
                }
            if attribute is None:
                return {"state": "observed", "exists": False}
            result: dict[str, Any] = {"state": "observed", "exists": True}
            if sample_limit:
                result["sample"] = self._semantic_attribute_samples(
                    geometry,
                    attribute,
                    owner=owner,
                    limit=sample_limit,
                )
            return result

        prim_by_name = getattr(geometry, "primByName", None)
        if not callable(prim_by_name):
            return {
                "state": "unavailable",
                "exists": None,
                "reason": "volume_lookup_unavailable",
            }
        try:
            primitive = prim_by_name(name)
        except Exception as exc:
            return {
                "state": "unavailable",
                "exists": None,
                "reason": "volume_lookup_failed",
                "error": _bounded_text(_redact_text(str(exc)), 1024),
            }
        if primitive is None:
            return {"state": "observed", "exists": False}
        if not any(
            callable(getattr(primitive, method, None))
            for method in ("sample", "samplev", "voxel")
        ):
            return {
                "state": "observed",
                "exists": False,
                "reason": "named_primitive_is_not_volume",
            }
        result = {"state": "observed", "exists": True}
        if sample_limit:
            result["sample"] = self._semantic_volume_samples(
                primitive,
                limit=sample_limit,
            )
        return result

    def _semantic_attribute_samples(
        self,
        geometry: Any,
        attribute: Any,
        *,
        owner: str,
        limit: int,
    ) -> dict[str, Any]:
        try:
            if owner == "detail":
                values = [geometry.attribValue(attribute)]
            else:
                iterator_name = {
                    "point": "iterPoints",
                    "primitive": "iterPrims",
                    "vertex": "iterVertices",
                }[owner]
                fallback_name = {
                    "point": "points",
                    "primitive": "prims",
                    "vertex": "vertices",
                }[owner]
                iterator = getattr(geometry, iterator_name, None)
                fallback = getattr(geometry, fallback_name, None)
                if callable(iterator):
                    elements = iterator()
                elif callable(fallback):
                    elements = fallback()
                else:
                    return {
                        "state": "unavailable",
                        "reason": "attribute_sampling_unavailable",
                    }
                values = []
                for element in elements:
                    if len(values) >= limit:
                        break
                    values.append(element.attribValue(attribute))
        except Exception as exc:
            return {
                "state": "unavailable",
                "reason": "attribute_sampling_failed",
                "error": _bounded_text(_redact_text(str(exc)), 1024),
            }
        return self._numeric_sample_stats(values, limit=limit)

    def _semantic_volume_samples(
        self,
        primitive: Any,
        *,
        limit: int,
    ) -> dict[str, Any]:
        values = []
        try:
            bounding_box = primitive.boundingBox()
            minimum = tuple(bounding_box.minvec())
            maximum = tuple(bounding_box.maxvec())
            center = tuple(bounding_box.center())
            positions = [center]
            for x in (minimum[0], maximum[0]):
                for y in (minimum[1], maximum[1]):
                    for z in (minimum[2], maximum[2]):
                        positions.append((x, y, z))
            sample_vector = getattr(primitive, "samplev", None)
            sample_scalar = getattr(primitive, "sample", None)
            if not callable(sample_vector) and not callable(sample_scalar):
                return {
                    "state": "unavailable",
                    "reason": "volume_sampling_unavailable",
                }
            for position in positions[:limit]:
                if callable(sample_vector):
                    try:
                        values.append(sample_vector(position))
                        continue
                    except Exception:
                        pass
                if not callable(sample_scalar):
                    raise RuntimeError("volume sample method is unavailable")
                values.append(sample_scalar(position))
        except Exception as exc:
            return {
                "state": "unavailable",
                "reason": "volume_sampling_failed",
                "error": _bounded_text(_redact_text(str(exc)), 1024),
            }
        return self._numeric_sample_stats(values, limit=limit)

    @staticmethod
    def _numeric_sample_stats(
        values: Iterable[Any],
        *,
        limit: int,
    ) -> dict[str, Any]:
        sample_count = 0
        finite_count = 0
        nonzero_count = 0
        finite_magnitudes = []
        for value in values:
            if sample_count >= limit:
                break
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                components = [float(value)]
            else:
                try:
                    components = [float(component) for component in value]
                except (TypeError, ValueError):
                    continue
                if not components:
                    continue
            sample_count += 1
            if not all(math.isfinite(component) for component in components):
                continue
            finite_count += 1
            magnitude = math.sqrt(
                sum(component * component for component in components)
            )
            finite_magnitudes.append(magnitude)
            if magnitude > 0.0:
                nonzero_count += 1
        if not sample_count:
            return {
                "state": "unavailable",
                "reason": "no_numeric_samples",
                "sample_limit": limit,
            }
        return {
            "state": "observed",
            "sample_limit": limit,
            "sample_count": sample_count,
            "finite_count": finite_count,
            "nonzero_count": nonzero_count,
            "all_finite": finite_count == sample_count,
            "all_zero": finite_count > 0 and nonzero_count == 0,
            "min_magnitude": (
                min(finite_magnitudes) if finite_magnitudes else None
            ),
            "max_magnitude": (
                max(finite_magnitudes) if finite_magnitudes else None
            ),
        }

    def _execute_hom(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        execute_started = time.monotonic()
        execution_id = uuid.uuid4().hex
        script = arguments.get("script")
        if not isinstance(script, str) or not script.strip():
            raise HiaRuntimeError("INVALID_ARGUMENTS", "script must be a non-empty string")
        if len(script) > MAX_SCRIPT_CHARS:
            raise HiaRuntimeError("REQUEST_TOO_LARGE", "The HOM script exceeds the character limit", {"limit": MAX_SCRIPT_CHARS})
        try:
            compiled_script = compile(script, "<hia_execute_hom>", "exec")
        except (SyntaxError, ValueError) as exc:
            raise HiaRuntimeError(
                "INVALID_HOM_SCRIPT",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        try:
            timeout_seconds = float(arguments.get("timeout_seconds", 60.0))
        except (TypeError, ValueError) as exc:
            raise HiaRuntimeError("INVALID_ARGUMENTS", "timeout_seconds must be a number") from exc
        if not 1 <= timeout_seconds <= 300:
            raise HiaRuntimeError("INVALID_ARGUMENTS", "timeout_seconds must be between 1 and 300")
        task = arguments.get("task", "")
        if not isinstance(task, str) or len(task) > 1024:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "task must be a string of at most 1024 characters",
            )
        mutable_root = self._optional_node_path(
            arguments.get("mutable_root"),
            field_name="mutable_root",
        )
        protected_paths = self._absolute_node_paths(
            arguments.get("protected_paths") or [],
            field_name="protected_paths",
            maximum=64,
        )
        expected_outputs = self._absolute_node_paths(
            arguments.get("expected_outputs") or [],
            field_name="expected_outputs",
            maximum=64,
        )
        expected_deletions = self._absolute_node_paths(
            arguments.get("expected_deletions") or [],
            field_name="expected_deletions",
            maximum=1_024,
        )
        requested_checks = self._validation_check_names(
            arguments.get("checks"),
            default=(),
        )
        semantic_checks = self._semantic_checks(
            arguments.get("semantic_checks") or []
        )
        semantic_paths = self._semantic_check_paths(semantic_checks)
        if semantic_checks and "semantic_expectations" not in requested_checks:
            requested_checks.append("semantic_expectations")
        if mutable_root:
            outside = [
                path
                for path in [*expected_outputs, *expected_deletions]
                if not _houdini_path_is_within(path, mutable_root)
            ]
            if outside:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "expected_outputs and expected_deletions must be inside mutable_root",
                    {"paths": outside},
                )
        protected_expected = [
            path
            for path in [*expected_outputs, *expected_deletions]
            if any(
                _houdini_path_is_within(path, protected)
                for protected in protected_paths
            )
        ]
        if protected_expected:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "expected_outputs and expected_deletions cannot be inside protected_paths",
                {"paths": protected_expected},
            )
        if mutable_root and any(
            _houdini_path_is_within(mutable_root, protected)
            for protected in protected_paths
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "mutable_root cannot be inside a protected path",
                {"mutable_root": mutable_root},
            )

        capture_diff = bool(arguments.get("capture_diff", True))
        full_diff = capture_diff and "diff_root_path" in arguments
        diff_root = str(arguments.get("diff_root_path", "/"))
        requested_diff_paths = arguments.get("diff_paths", [])
        if not isinstance(requested_diff_paths, list):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "diff_paths must be an array")
        checkpoint_label = str(arguments.get("checkpoint_label", "")).strip()
        if checkpoint_label and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", checkpoint_label) is None:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "checkpoint_label must use 1-128 letters, numbers, dot, underscore, or dash",
            )
        fresh_validation = bool(arguments.get("fresh_validation", True))
        explicit_require_change = arguments.get("require_scene_change")
        if explicit_require_change is not None and not isinstance(
            explicit_require_change, bool
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "require_scene_change must be a boolean",
            )
        undos = getattr(self._hou, "undos", None)
        undo_group = getattr(undos, "group", None)
        undo_labels = getattr(undos, "undoLabels", None)
        perform_undo = getattr(undos, "performUndo", None)
        undos_enabled = getattr(undos, "areEnabled", None)
        undo_available = all(
            callable(value)
            for value in (undo_group, undo_labels, perform_undo, undos_enabled)
        )
        undo_unavailable_error: dict[str, Any] | None = None
        undo_labels_before: tuple[str, ...] = ()
        if not undo_available:
            undo_unavailable_error = {
                "code": "UNDO_ROLLBACK_UNAVAILABLE",
                "message": (
                    "The current Houdini runtime does not expose the undo-group "
                    "API; the HOM batch may still run, but an execution failure "
                    "cannot be rolled back automatically"
                ),
            }
        else:
            try:
                if not bool(undos_enabled()):
                    undo_available = False
                    undo_unavailable_error = {
                        "code": "UNDO_ROLLBACK_UNAVAILABLE",
                        "message": (
                            "Houdini undo recording is disabled; the HOM batch "
                            "may still run, but an execution failure cannot be "
                            "rolled back automatically"
                        ),
                    }
                else:
                    undo_labels_before = tuple(
                        str(value) for value in undo_labels()
                    )
            except Exception as exc:
                undo_available = False
                undo_unavailable_error = {
                    "code": "UNDO_ROLLBACK_UNAVAILABLE",
                    "message": (
                        "The Houdini undo stack could not be inspected before "
                        "execution; the HOM batch may still run"
                    ),
                    "details": {
                        "reason": _bounded_text(_redact_text(str(exc)), 1024)
                    },
                }
        undo_label = f"HIA MCP V2 {execution_id}"

        revision_before = self.scene_revision
        targeted_before: dict[str, str | None | object] = {}
        marker_only_baselines: set[str] = set()
        targeted_truncated = False
        protected_snapshot_roots = [
            path
            for path in protected_paths
            if not any(
                path != other
                and _houdini_path_is_within(path, other)
                for other in protected_paths
            )
        ]
        protected_before: dict[
            str,
            tuple[dict[str, str], bool] | object,
        ] = {}
        for path in protected_snapshot_roots:
            try:
                protected_before[path] = self._snapshot_map(path)
            except HiaRuntimeError as exc:
                if exc.code == "NODE_NOT_FOUND":
                    protected_before[path] = ({}, False)
                else:
                    protected_before[path] = _NODE_DIGEST_UNAVAILABLE
            except Exception:
                protected_before[path] = _NODE_DIGEST_UNAVAILABLE

        def add_targeted_baseline(path: str, *, marker_only: bool = False) -> None:
            nonlocal targeted_truncated
            if path in targeted_before:
                return
            if len(targeted_before) >= MAX_TARGETED_DIFF_PATHS:
                targeted_truncated = True
                return
            targeted_before[path] = self._node_digest(path)
            if marker_only:
                marker_only_baselines.add(path)

        if full_diff:
            try:
                before_nodes, before_truncated = self._snapshot_map(diff_root)
            except HiaRuntimeError as exc:
                if exc.code != "NODE_NOT_FOUND":
                    raise
                before_nodes, before_truncated = {}, False
        elif capture_diff:
            for value in requested_diff_paths:
                if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "diff_paths must contain absolute Houdini node paths",
                    )
                add_targeted_baseline(value)
        for path in [*protected_paths, *expected_outputs, *expected_deletions]:
            add_targeted_baseline(path)
        all_network_evidence_paths = list(
            dict.fromkeys(
                [
                    *expected_outputs,
                    *expected_deletions,
                    *semantic_paths,
                    *[
                        path
                        for path in requested_diff_paths
                        if isinstance(path, str) and path.startswith("/")
                    ],
                    *([mutable_root] if mutable_root else []),
                    *protected_paths,
                ]
            )
        )
        network_evidence_paths = all_network_evidence_paths[
            :MAX_NETWORK_EVIDENCE_PATHS
        ]
        network_evidence_before = [
            self._local_network_evidence(path)
            for path in network_evidence_paths
        ]
        dirty_before_state = self._dirty_observation()
        dirty_before = (
            dirty_before_state
            if dirty_before_state is not None
            else False
        )
        marked: set[str] = set()

        def mark_changed(value: Any) -> str:
            nonlocal targeted_truncated
            path = value if isinstance(value, str) else _safe_path(value)
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError("hia_mark_changed expects a Houdini node or absolute node path")
            if capture_diff and not full_diff:
                add_targeted_baseline(path, marker_only=True)
            if len(marked) >= MAX_TARGETED_DIFF_PATHS and path not in marked:
                targeted_truncated = True
                return path
            marked.add(path)
            return path

        namespace: dict[str, Any] = {
            "__name__": "__hia_execute_hom__",
            "hou": self._hou,
            "hia_result": None,
            "hia_changed_paths": [],
            "hia_mark_changed": mark_changed,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        warning_records: list[str] = []
        if undo_unavailable_error is not None:
            warning_records.append(str(undo_unavailable_error["message"]))
        hom_started = time.monotonic()
        failure: dict[str, Any] | None = None
        rollback: dict[str, Any] = {
            "requested": False,
            "reason": None,
            "status": "not_needed",
            "undo_label": undo_label,
            "houdini_scene_changes_rolled_back": False,
            "external_side_effects_rollbackable": False,
            "error": None,
        }
        try:
            undo_context = (
                undo_group(undo_label)
                if undo_available
                else contextlib.nullcontext()
            )
            with undo_context:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), python_warnings.catch_warnings(record=True) as caught:
                    python_warnings.simplefilter("always")
                    exec(compiled_script, namespace, namespace)
                    warning_records.extend(str(item.message) for item in caught)
        except Exception as exc:
            failure = {
                "code": "HOM_EXECUTION_FAILED",
                "message": _bounded_text(_redact_text(str(exc)), 2048),
                "traceback": _bounded_text(_redact_text(traceback.format_exc(limit=20)), 20_000),
                "partial_scene_changes_possible": True,
                "automatic_retry_safe": False,
            }
        hom_seconds = time.monotonic() - hom_started
        if stderr.getvalue().strip():
            warning_records.append(stderr.getvalue())
        explicit = namespace.get("hia_changed_paths", [])
        explicitly_changed: set[str] = set()
        if isinstance(explicit, (list, tuple, set)):
            for value in explicit:
                if isinstance(value, str) and value.startswith("/"):
                    if len(explicitly_changed) >= MAX_TARGETED_DIFF_PATHS and value not in explicitly_changed:
                        targeted_truncated = True
                        continue
                    explicitly_changed.add(value)

        def rollback_batch(reason: str) -> None:
            rollback.update(
                {
                    "requested": True,
                    "reason": reason,
                    "status": "not_proven",
                }
            )
            if not undo_available:
                rollback["error"] = dict(
                    undo_unavailable_error
                    or {
                        "code": "UNDO_ROLLBACK_UNAVAILABLE",
                        "message": "Houdini undo rollback is unavailable",
                    }
                )
                return
            try:
                current_labels = tuple(str(value) for value in undo_labels())
                if not current_labels or current_labels[0] != undo_label:
                    rollback["error"] = {
                        "code": "UNDO_ITEM_NOT_FOUND",
                        "message": (
                            "The batch's undo item is not the current Houdini "
                            "undo item, so no unrelated user action was undone"
                        ),
                    }
                    return
                perform_undo()
                remaining_labels = tuple(str(value) for value in undo_labels())
                if remaining_labels != undo_labels_before:
                    rollback["error"] = {
                        "code": "UNDO_STACK_NOT_RESTORED",
                        "message": (
                            "Houdini performed undo, but the undo stack does "
                            "not match its pre-batch state"
                        ),
                    }
                    return
                dirty_after_undo = self._dirty_observation()
                if dirty_before_state is None or dirty_after_undo is None:
                    rollback["error"] = {
                        "code": "DIRTY_STATE_UNAVAILABLE",
                        "message": (
                            "Houdini performed undo, but the HIP dirty state "
                            "could not be observed before and after the batch"
                        ),
                        "details": {
                            "dirty_before_available": (
                                dirty_before_state is not None
                            ),
                            "dirty_after_undo_available": (
                                dirty_after_undo is not None
                            ),
                        },
                    }
                    return
                if dirty_after_undo != dirty_before:
                    rollback["error"] = {
                        "code": "DIRTY_STATE_NOT_RESTORED",
                        "message": (
                            "Houdini performed undo, but the HIP dirty state "
                            "does not match its pre-batch state"
                        ),
                        "details": {
                            "dirty_before": dirty_before,
                            "dirty_after_undo": dirty_after_undo,
                        },
                    }
                    return
                rollback["status"] = "rolled_back"
                rollback["houdini_scene_changes_rolled_back"] = True
                if failure is not None:
                    failure["partial_scene_changes_possible"] = False
                    failure["houdini_scene_rollback"] = "verified"
                    failure["automatic_retry_safe"] = bool(
                        str(failure.get("code") or "")
                        not in {
                            "EXECUTION_TIMEOUT",
                            "HOM_EXECUTION_TIMEOUT",
                        }
                        and not failure.get(
                            "external_side_effects_possible",
                            False,
                        )
                    )
            except Exception as exc:
                rollback["error"] = {
                    "code": "UNDO_FAILED",
                    "message": _bounded_text(_redact_text(str(exc)), 2048),
                }

        if failure is not None:
            rollback_batch("hom_execution_failed")

        if full_diff:
            try:
                after_nodes, after_truncated = self._snapshot_map(diff_root)
            except HiaRuntimeError as exc:
                if exc.code != "NODE_NOT_FOUND":
                    raise
                after_nodes, after_truncated = {}, False
            full_delta = self._diff_maps(before_nodes, after_nodes)
            full_verified = {
                path
                for key in ("created", "deleted", "changed")
                for path in full_delta[key]
            }
            full_claimed = marked | explicitly_changed
            diff: dict[str, Any] = {
                **full_delta,
                "mode": "full",
                "root_path": diff_root,
                "declared_or_touched_paths": sorted(full_claimed)[:MAX_TARGETED_DIFF_PATHS],
                "unverified_paths": sorted(full_claimed - full_verified)[:MAX_TARGETED_DIFF_PATHS],
                "truncated": before_truncated or after_truncated or targeted_truncated,
            }
        elif capture_diff:
            candidate_paths = list(
                dict.fromkeys(
                    [
                        *targeted_before,
                        *sorted(marked),
                        *sorted(explicitly_changed),
                    ]
                )
            )
            if len(candidate_paths) > MAX_TARGETED_DIFF_PATHS:
                candidate_paths = candidate_paths[:MAX_TARGETED_DIFF_PATHS]
                targeted_truncated = True
            after_states = {
                path: self._node_digest(path)
                for path in candidate_paths
            }
            created: list[str] = []
            deleted: list[str] = []
            changed: list[str] = []
            unverified: list[str] = []
            claimed = marked | explicitly_changed
            for path in candidate_paths:
                if path not in targeted_before:
                    if path in claimed:
                        unverified.append(path)
                    continue
                before = targeted_before[path]
                after = after_states[path]
                if before is _NODE_DIGEST_UNAVAILABLE or after is _NODE_DIGEST_UNAVAILABLE:
                    unverified.append(path)
                    continue
                if before is None and after is not None:
                    created.append(path)
                elif before is not None and after is None:
                    deleted.append(path)
                elif before is not None and after is not None and before != after:
                    changed.append(path)
                elif path in claimed and path in marker_only_baselines:
                    # A marker may have been called after the edit.  Without an
                    # earlier baseline, unchanged-at-return cannot be promoted
                    # to a verified scene diff.
                    unverified.append(path)
            diff = {
                "created": created,
                "deleted": deleted,
                "changed": changed,
                "mode": "targeted",
                "root_path": None,
                "declared_or_touched_paths": sorted(set(candidate_paths)),
                "unverified_paths": sorted(set(unverified)),
                "truncated": targeted_truncated,
            }
        else:
            diff = None
        protected_delta = {
            "created": set(),
            "deleted": set(),
            "changed": set(),
        }
        protected_unverified_paths: set[str] = set()
        for path, before_state in protected_before.items():
            if before_state is _NODE_DIGEST_UNAVAILABLE:
                protected_unverified_paths.add(path)
                continue
            before_map, before_was_truncated = before_state
            try:
                after_map, after_was_truncated = self._snapshot_map(path)
            except HiaRuntimeError as exc:
                if exc.code == "NODE_NOT_FOUND":
                    after_map, after_was_truncated = {}, False
                else:
                    protected_unverified_paths.add(path)
                    continue
            except Exception:
                protected_unverified_paths.add(path)
                continue
            protected_diff = self._diff_maps(before_map, after_map)
            for key in protected_delta:
                protected_delta[key].update(protected_diff[key])
            if before_was_truncated or after_was_truncated:
                protected_unverified_paths.add(path)
        protected_observed_paths = {
            path
            for values in protected_delta.values()
            for path in values
        }
        if isinstance(diff, dict):
            for key in protected_delta:
                diff[key] = sorted(
                    set(diff.get(key, [])) | protected_delta[key]
                )
            diff["unverified_paths"] = sorted(
                set(diff.get("unverified_paths", []))
                | protected_unverified_paths
            )
        if isinstance(diff, dict):
            observed_deleted = set(str(path) for path in diff["deleted"])
            (
                diff["expected_deletions"],
                diff["missing_expected_deletions"],
                diff["unexpected_deletions"],
            ) = _classify_expected_deletions(
                observed_deleted,
                expected_deletions,
            )

        verified_diff_paths = (
            {
                str(path)
                for key in ("created", "deleted", "changed")
                for path in diff.get(key, [])
            }
            if isinstance(diff, Mapping)
            else set()
        )
        envelope_observed_paths: set[str] = set()
        envelope_unverified_paths: set[str] = set()
        if full_diff or not capture_diff:
            for path in dict.fromkeys([*protected_paths, *expected_outputs]):
                before_state = targeted_before.get(path, _NODE_DIGEST_UNAVAILABLE)
                after_state = self._node_digest(path)
                if (
                    before_state is _NODE_DIGEST_UNAVAILABLE
                    or after_state is _NODE_DIGEST_UNAVAILABLE
                ):
                    envelope_unverified_paths.add(path)
                elif before_state != after_state:
                    envelope_observed_paths.add(path)
        observed_paths = (
            verified_diff_paths
            | envelope_observed_paths
            | protected_observed_paths
        )
        changed_paths = list(
            dict.fromkeys(
                [
                    *sorted(protected_observed_paths),
                    *sorted(observed_paths),
                ]
            )
        )[:MAX_TARGETED_DIFF_PATHS]
        dirty_after_script = self._dirty()
        observed_change = bool(observed_paths) or dirty_before != dirty_after_script
        unverified_paths = (
            set(str(path) for path in diff.get("unverified_paths", []))
            if isinstance(diff, Mapping)
            else set()
        ) | envelope_unverified_paths | protected_unverified_paths
        if observed_change:
            scene_change_status = "changed"
        elif failure is not None or unverified_paths or marked or explicitly_changed or not capture_diff:
            scene_change_status = "unknown"
        else:
            scene_change_status = "unchanged"

        validation_started = time.monotonic()
        effective_checks = list(requested_checks)
        if expected_outputs and "critical_paths" not in effective_checks:
            effective_checks.append("critical_paths")
        if expected_outputs and "node_errors" not in effective_checks:
            effective_checks.append("node_errors")
        if (mutable_root or protected_paths) and "changed_scope" not in effective_checks:
            effective_checks.append("changed_scope")
        observed_expected_deletions = {
            str(path)
            for path in (
                diff.get("deleted", [])
                if isinstance(diff, Mapping)
                else []
            )
            if any(
                _houdini_path_is_within(str(path), expected_root)
                for expected_root in expected_deletions
            )
        }
        validation_paths = [
            path
            for path in dict.fromkeys(
                [*expected_outputs, *changed_paths, *semantic_paths]
            )
            if path not in observed_expected_deletions
        ][:64]
        if not validation_paths and any(
            name in {"node_errors", "empty_output", "geometry_summary"}
            for name in effective_checks
        ):
            validation_paths = [
                _safe_path(node)
                for node in list(_safe_call(self._hou, "selectedNodes", ()))[:64]
                if _safe_path(node)
            ]
        full_diff_result = isinstance(diff, Mapping) and diff.get("mode") == "full"
        diff_root_observed = (
            str(diff.get("root_path") or "") if full_diff_result else ""
        )
        scope_complete = bool(
            full_diff_result
            and not diff.get("truncated")
            and not unverified_paths
            and (
                diff_root_observed == "/"
                if mutable_root or not protected_paths
                else all(
                    _houdini_path_is_within(path, diff_root_observed)
                    for path in protected_paths
                )
            )
        )
        validation_cook = bool(
            fresh_validation
            and validation_paths
            and {
                "empty_output",
                "geometry_summary",
                "semantic_expectations",
            }.intersection(effective_checks)
        )
        validation = self._run_domain_validation(
            paths=validation_paths,
            checks=effective_checks,
            cook=validation_cook,
            expected_paths=expected_outputs,
            explicit_output_paths=list(expected_outputs),
            changed_paths=changed_paths,
            mutable_root=mutable_root,
            protected_paths=protected_paths,
            semantic_checks=semantic_checks,
            finding_limit=64,
            scope_complete=scope_complete,
            change_provenance="observed",
        )
        network_evidence_after = [
            self._local_network_evidence(path)
            for path in network_evidence_paths
        ]
        before_facts = {
            str(item.get("path") or ""): item
            for item in network_evidence_before
        }
        fact_differences = []
        for after_fact in network_evidence_after:
            path = str(after_fact.get("path") or "")
            before_fact = before_facts.get(path, {"path": path, "exists": False})
            changed_fields = [
                field
                for field in (
                    "exists",
                    "type",
                    "inputs",
                    "outputs",
                    "controls",
                    "material_entries",
                    "material_role",
                    "flags",
                    "cook_state",
                    "errors",
                    "warnings",
                )
                if before_fact.get(field) != after_fact.get(field)
            ]
            if changed_fields:
                fact_differences.append(
                    {"path": path, "changed_fields": changed_fields}
                )
        validation_seconds = time.monotonic() - validation_started
        require_scene_change = (
            bool(explicit_require_change)
            if explicit_require_change is not None
            else bool(
                expected_outputs
                or requested_diff_paths
                or checkpoint_label
                or expected_deletions
                or marked
                or explicitly_changed
            )
        )
        scope_check = next(
            (
                item
                for item in validation["check_results"]
                if item["check"] == "changed_scope"
            ),
            None,
        )
        scope_findings = (
            list(scope_check.get("findings") or [])
            if isinstance(scope_check, Mapping)
            else []
        )
        scope_violation = bool(
            isinstance(scope_check, Mapping)
            and scope_check.get("status") == "fail"
            and scope_findings
        )
        if failure is None and scope_violation:
            failure = {
                "code": "PATH_SCOPE_VIOLATION",
                "message": (
                    "The batch made an observed change outside its mutable "
                    "scope or inside a protected path"
                ),
                "details": {
                    "paths": list(
                        dict.fromkeys(
                            str(item.get("path") or "")
                            for item in scope_findings
                            if str(item.get("path") or "").startswith("/")
                        )
                    )[:64],
                },
                "partial_scene_changes_possible": observed_change,
                "automatic_retry_safe": False,
            }
        if (
            failure is None
            and isinstance(diff, Mapping)
            and diff.get("unexpected_deletions")
        ):
            failure = {
                "code": "UNEXPECTED_DELETION",
                "message": "The batch deleted one or more undeclared node paths",
                "details": {
                    "paths": list(diff["unexpected_deletions"])[:64],
                },
                "partial_scene_changes_possible": observed_change,
                "automatic_retry_safe": False,
            }
        if (
            failure is None
            and isinstance(diff, Mapping)
            and diff.get("missing_expected_deletions")
        ):
            warning_records.append(
                "Expected deletion was not observed for: "
                + ", ".join(
                    str(path)
                    for path in list(diff["missing_expected_deletions"])[:64]
                )
            )
        if failure is None and require_scene_change and not observed_change:
            warning_records.append(
                "The batch requested scene-change evidence, but the observed "
                "diff and dirty state did not prove a change; the HOM result "
                "is retained for Codex to inspect rather than rolled back"
            )
        no_observed_effect = bool(require_scene_change and not observed_change)
        missing_expected_deletions = bool(
            isinstance(diff, Mapping)
            and diff.get("missing_expected_deletions")
        )
        rollback_required = failure is not None
        if rollback_required and not rollback["requested"]:
            rollback_batch("postcondition_failed")
        attempted_changed_paths = list(changed_paths)
        attempted_scene_change_status = scene_change_status
        if rollback["status"] == "rolled_back":
            changed_paths = []
            scene_change_status = "rolled_back"
        elif observed_change:
            with self._state_lock:
                self._scene_revision += 1
        for message in validation["messages"]:
            if message["level"] not in {"error", "warning"}:
                continue
            warning_text = (
                f"[{message['level'].upper()}] {message['code']}: "
                f"{message['message']}"
            )
            if failure is not None:
                warning_text += (
                    f"; rollback status is {rollback['status']}; "
                    "automatic_retry_safe="
                    f"{str(bool(failure.get('automatic_retry_safe'))).lower()}"
                )
            warning_records.append(warning_text)

        node_error_check = next(
            (
                item
                for item in validation["check_results"]
                if item["check"] == "node_errors"
            ),
            None,
        )
        node_findings = (
            list(node_error_check.get("findings") or [])
            if isinstance(node_error_check, Mapping)
            else []
        )
        missing_input_count = sum(
            finding.get("code") == "MISSING_INPUT"
            for finding in node_findings
        )
        postconditions_requested = bool(
            expected_outputs
            or expected_deletions
            or requested_checks
            or semantic_checks
            or mutable_root
            or protected_paths
            or require_scene_change
        )
        postcondition_evidence_observed = bool(
            any(
                item.get("status") == "pass"
                for item in validation["check_results"]
            )
            or (
                expected_deletions
                and isinstance(diff, Mapping)
                and not diff.get("missing_expected_deletions")
            )
        )

        execution_evidence = {
            "envelope": {
                "task": _bounded_text(task.strip(), 1024),
                "mutable_root": mutable_root or None,
                "protected_paths": protected_paths,
                "expected_outputs": expected_outputs,
                "expected_deletions": expected_deletions,
                "checks": requested_checks,
                "semantic_check_count": len(semantic_checks),
                "fresh_validation": fresh_validation,
                "require_scene_change": require_scene_change,
                "authoring_policy": "native_nodes_preferred",
            },
            "before": {"revision": revision_before, "dirty": dirty_before},
            "attempted": {
                "scene_change_status": attempted_scene_change_status,
                "changed_paths": attempted_changed_paths,
            },
            "network_facts": {
                "scope": {
                    "mutable_root": mutable_root or None,
                    "protected_paths": protected_paths,
                },
                "before": network_evidence_before,
                "after_attempt": network_evidence_after,
                "differences": fact_differences,
                "path_count": len(network_evidence_paths),
                "truncated": (
                    len(all_network_evidence_paths)
                    > MAX_NETWORK_EVIDENCE_PATHS
                ),
            },
            "postconditions": {
                "status": (
                    "not_requested"
                    if not postconditions_requested
                    else (
                        "failed"
                        if (
                            failure is not None
                            or not validation["valid"]
                            or missing_expected_deletions
                        )
                        else (
                            "not_proven"
                            if no_observed_effect
                            else (
                                "partial"
                                if not validation["complete"]
                                else (
                                    "passed"
                                    if postcondition_evidence_observed
                                    else "not_proven"
                                )
                            )
                        )
                    )
                ),
                "target_paths": expected_outputs,
                "target_existence": (
                    "not_requested"
                    if not expected_outputs
                    else (
                        "failed"
                        if validation["missing_expected_paths"]
                        else "observed"
                    )
                ),
                "missing_targets": validation["missing_expected_paths"],
                "required_input_connections": (
                    "not_requested"
                    if node_error_check is None
                    else ("failed" if missing_input_count else "observed")
                ),
                "missing_required_input_count": missing_input_count,
                "control_parameter_facts": sum(
                    len(item.get("controls") or [])
                    for item in network_evidence_after
                ),
                "material_entry_facts": sum(
                    len(item.get("material_entries") or [])
                    for item in network_evidence_after
                ),
                "cook_freshness": validation["cook_cache_evidence"][
                    "assessment"
                ],
                "scope_state": (
                    scope_check["evidence"].get("scope_state")
                    if isinstance(scope_check, Mapping)
                    else "not_requested"
                ),
                "node_errors": sum(
                    len(item.get("errors") or [])
                    for item in network_evidence_after
                ),
                "node_warnings": sum(
                    len(item.get("warnings") or [])
                    for item in network_evidence_after
                ),
                "visual_or_render_evidence": {
                    "status": "not_observed",
                    "reason": (
                        "hia_execute_hom does not automatically capture the "
                        "viewport or render output"
                    ),
                },
            },
            "validation": validation,
        }

        focus_target = self._goal_focus_target() if checkpoint_label else None
        checkpoint: dict[str, Any] = {
            "requested": bool(checkpoint_label),
            "label": checkpoint_label or None,
            "created": False,
            "path": None,
            "storage_scope": None,
            "error": None,
        }
        if checkpoint_label:
            if failure is not None:
                checkpoint["skipped_reason"] = str(
                    failure.get("code") or "EXECUTION_FAILED"
                )
            elif not observed_change:
                checkpoint["skipped_reason"] = "NO_CONFIRMED_SCENE_CHANGE"
            elif focus_target is None:
                checkpoint["skipped_reason"] = "FOCUS_MODE_DISABLED"
            else:
                try:
                    session_checkpoint_directory = self._checkpoint_directory()
                    (
                        checkpoint_directory,
                        checkpoint_storage_scope,
                        source_hip_path,
                    ) = self._artifact_directory(
                        "checkpoints",
                        fallback=session_checkpoint_directory,
                    )
                    checkpoint["storage_scope"] = checkpoint_storage_scope
                except Exception as exc:
                    checkpoint["skipped_reason"] = "CHECKPOINT_CONFIGURATION_INVALID"
                    checkpoint["error"] = {
                        "code": "CHECKPOINT_CONFIGURATION_INVALID",
                        "message": _bounded_text(_redact_text(str(exc)), 2048),
                    }
                    warning_records.append(
                        "Checkpoint was skipped after the HOM batch completed; do not retry the scene write automatically"
                    )
                else:
                    try:
                        with self._houdini_backup_directory(checkpoint_directory):
                            returned_path = self._hou.hipFile.saveAsBackup()
                        if not isinstance(returned_path, str) or not returned_path.strip():
                            raise RuntimeError("Houdini did not return the backup path")
                        candidate = Path(returned_path.strip())
                        if not candidate.is_absolute():
                            candidate = checkpoint_directory / candidate
                        if _is_reparse_point(candidate):
                            raise RuntimeError(
                                "Houdini returned a reparse-point checkpoint path"
                            )
                        checkpoint_path = candidate.resolve(strict=True)
                        if not checkpoint_path.is_file() or not _is_within(
                            checkpoint_path, checkpoint_directory
                        ):
                            raise RuntimeError(
                                "Houdini returned a backup path outside the configured checkpoint directory"
                            )
                        if self._goal_focus_target() != focus_target:
                            raise RuntimeError(
                                "Target focus mode, active Thread, or Goal changed before the checkpoint completed"
                            )
                        if checkpoint_storage_scope == "hip":
                            current_hip_directory = (
                                self._saved_hip_artifact_directory("checkpoints")
                            )
                            if (
                                current_hip_directory is None
                                or current_hip_directory[0] != checkpoint_directory
                                or current_hip_directory[1] != source_hip_path
                            ):
                                raise RuntimeError(
                                    "The current saved HIP changed before the checkpoint completed"
                                )
                        focus_thread_id, goal_binding = focus_target
                        self._write_stage_checkpoint_marker(
                            session_checkpoint_directory,
                            checkpoint_path,
                            focus_thread_id,
                            goal_binding,
                            storage_scope=checkpoint_storage_scope,
                            source_hip_path=source_hip_path,
                        )
                        checkpoint["created"] = True
                        checkpoint["path"] = str(checkpoint_path)
                    except Exception as exc:
                        checkpoint["error"] = {
                            "code": "CHECKPOINT_FAILED",
                            "message": _bounded_text(_redact_text(str(exc)), 2048),
                        }
                        warning_records.append(
                            "Checkpoint failed after the HOM batch completed; do not retry the scene write automatically"
                        )
        dirty_after = self._dirty()
        redacted_stdout = _bounded_text(_redact_text(stdout.getvalue()), MAX_TEXT_CHARS)
        redacted_warnings = [_bounded_text(_redact_text(value), 4096) for value in warning_records[:100]]
        errors = [failure] if failure is not None else []
        script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
        result = {
            "ok": failure is None,
            "result": _json_value(namespace.get("hia_result")),
            "stdout": redacted_stdout,
            "warnings": redacted_warnings,
            "errors": errors,
            "created_or_changed_paths": changed_paths,
            "scene_change_status": scene_change_status,
            "revision": self.scene_revision,
            "dirty": dirty_after,
            "elapsed_seconds": _seconds(hom_seconds),
            "diff": diff,
            "checkpoint": checkpoint,
            "execution_evidence": execution_evidence,
            "rollback": rollback,
            "execution_trace": {
                "schema": "hia-execution-trace/1",
                "trace_id": execution_id,
                "script_sha256": script_sha256,
                "recorded": False,
                "relative_path": None,
                "error": None,
            },
            "execution_limit": {
                "requested_timeout_seconds": timeout_seconds,
                "timeout_kind": "client_wait_budget",
                "cancel_before_main_thread": True,
                "interruptible_after_main_thread_entry": False,
                "hom_may_continue_after_client_timeout": True,
                "automatic_retry_after_timeout": False,
            },
            "structured_error": failure,
        }
        result["phase_timings"] = {
            "queue_seconds": 0.0,
            "hom_seconds": _seconds(hom_seconds),
            "validation_seconds": _seconds(validation_seconds),
            "total_seconds": _seconds(time.monotonic() - execute_started),
        }
        return result

    def _record_execution_trace(self, result: dict[str, Any]) -> dict[str, Any]:
        trace = result.get("execution_trace")
        evidence = result.get("execution_evidence")
        if not isinstance(trace, dict) or not isinstance(evidence, Mapping):
            return result
        before = evidence.get("before") if isinstance(evidence.get("before"), Mapping) else {}
        validation = evidence.get("validation") if isinstance(evidence.get("validation"), Mapping) else {}
        changed = list(result.get("created_or_changed_paths") or [])
        timings = result.get("phase_timings")
        timings = timings if isinstance(timings, Mapping) else {}
        record = {
            "schema": "hia-execution-trace/1", "timestamp": _utc_now(),
            "trace_id": str(trace.get("trace_id") or ""),
            "script_sha256": str(trace.get("script_sha256") or ""),
            "revision": {"before": before.get("revision"), "after": result.get("revision")},
            "scene_change_status": result.get("scene_change_status"),
            "changed_paths": [
                _bounded_text(_redact_text(str(value)), 512) for value in changed[:32]
            ],
            "changed_path_count": len(changed), "changed_paths_truncated": len(changed) > 32,
            "checks": [
                {"check": item.get("check"), "status": item.get("status"),
                 "finding_count": item.get("finding_count")}
                for item in validation.get("check_results", [])
                if isinstance(item, Mapping)
            ][: len(VALIDATION_CHECK_NAMES)],
            "error_codes": [
                item.get("code")
                for item in result.get("errors", [])
                if isinstance(item, Mapping)
            ][:32],
            "phase_timings": {
                name: _seconds(float(timings.get(name) or 0.0))
                for name in (
                    "queue_seconds",
                    "hom_seconds",
                    "validation_seconds",
                    "total_seconds",
                )
            },
        }
        try:
            runtime_parent = self._runtime_root.parent
            runtime_parent.mkdir(parents=True, exist_ok=True)
            resolved_runtime_parent = runtime_parent.resolve(strict=True)
            if not _is_within(resolved_runtime_parent, self._project_root):
                raise RuntimeError("Execution trace runtime directory escaped the project")
            self._runtime_root.mkdir(exist_ok=True)
            resolved_runtime_root = self._runtime_root.resolve(strict=True)
            if not _is_within(resolved_runtime_root, resolved_runtime_parent):
                raise RuntimeError("Execution trace runtime directory escaped .runtime")
            trace_directory = self._runtime_root / "execution-traces"
            trace_directory.mkdir(exist_ok=True)
            resolved_trace_directory = trace_directory.resolve(strict=True)
            if not _is_within(resolved_trace_directory, resolved_runtime_root):
                raise RuntimeError("Execution trace directory escaped the HIA runtime")
            trace_path = (
                resolved_trace_directory / f"{self._trace_session_id}.jsonl"
            ).resolve(strict=False)
            if not _is_within(trace_path, resolved_trace_directory):
                raise RuntimeError("Execution trace file escaped its directory")
            line = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if len(line.encode("utf-8")) > 65_536:
                raise RuntimeError("Execution trace record exceeded 65536 bytes")
            with self._trace_lock, trace_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
            trace["recorded"] = True
            trace["relative_path"] = trace_path.relative_to(self._project_root).as_posix()
        except Exception as exc:
            trace["error"] = {
                "code": "EXECUTION_TRACE_WRITE_FAILED",
                "message": _bounded_text(_redact_text(str(exc)), 1024),
            }
            result.setdefault("warnings", []).append(
                "Execution trace recording failed after the HOM call completed; do not retry the scene write automatically"
            )
        self._remember_evidence({
            "kind": "execution", "timestamp": record["timestamp"],
            "paths": [
                _bounded_text(_redact_text(str(value)), 512)
                for value in changed[:64]
            ],
            "path_count": record["changed_path_count"],
            "status": (
                str(record["scene_change_status"])
                if result.get("ok")
                else "failed"
            ),
            "complete": bool(validation.get("complete", True)),
            "checks": record["checks"],
            "error_codes": record["error_codes"],
        })
        return result

    def _effect_experiment_preflight(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_timeout = arguments.get("timeout_seconds", 60.0)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(float(raw_timeout))
            or not 1 <= float(raw_timeout) <= 300
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "timeout_seconds must be between 1 and 300",
            )
        timeout_seconds = float(raw_timeout)

        def scalar(value: Any, field: str) -> Any:
            if value is None or not isinstance(value, (str, bool, int, float)):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field} must be a scalar bool, int, finite float, or string",
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise HiaRuntimeError("INVALID_ARGUMENTS", f"{field} must be finite")
            if isinstance(value, str) and (len(value) > 4096 or "\x00" in value):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field} contains an unsafe string",
                )
            return value

        def variant(
            raw: Any,
            field: str,
            *,
            allow_empty: bool = False,
            default_name: str = "",
        ) -> dict[str, Any]:
            if not isinstance(raw, Mapping):
                raise HiaRuntimeError("INVALID_ARGUMENTS", f"{field} must be an object")
            name = str(raw.get("name") or default_name).strip()
            values = raw.get("parameters")
            if (
                not name
                or len(name) > 64
                or any(ord(character) < 32 for character in name)
                or not isinstance(values, Mapping)
                or (not values and not allow_empty)
                or len(values) > MAX_EXPERIMENT_PARAMETERS
            ):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field} has an invalid name or parameter count",
                )
            parameters: dict[str, Any] = {}
            for key, value in values.items():
                path = str(key).strip() if isinstance(key, str) else ""
                if not _valid_houdini_node_path(path) or path == "/":
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        f"{field}.parameters requires expanded absolute hou.Parm paths",
                    )
                parameters[path] = scalar(value, f"{field}.parameters[{path}]")
            return {"name": name, "parameters": parameters}

        target = str(arguments.get("target_network") or "").strip()
        if not _valid_houdini_node_path(target) or target == "/":
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "target_network must be a non-root absolute node path",
            )
        if self._hou.node(target) is None:
            raise HiaRuntimeError(
                "NODE_NOT_FOUND",
                "The experiment target network does not exist",
                {"path": target},
            )
        baseline = variant(
            arguments.get("baseline"),
            "baseline",
            allow_empty=True,
            default_name="baseline",
        )
        raw_candidates = arguments.get("candidates")
        if not isinstance(raw_candidates, (list, tuple)) or not 2 <= len(raw_candidates) <= 3:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "candidates must contain two or three entries",
            )
        candidates = [
            variant(value, f"candidates[{index}]")
            for index, value in enumerate(raw_candidates)
        ]
        names = [baseline["name"], *[value["name"] for value in candidates]]
        if len(names) != len({value.casefold() for value in names}):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "Variant names must be unique")

        input_paths = list(
            dict.fromkeys(
                [
                    *baseline["parameters"],
                    *[
                        path
                        for candidate in candidates
                        for path in candidate["parameters"]
                    ],
                ]
            )
        )
        if not input_paths or len(input_paths) > MAX_EXPERIMENT_PARAMETERS:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                f"The experiment must touch 1-{MAX_EXPERIMENT_PARAMETERS} parameters",
            )
        hou_parm = getattr(self._hou, "parm", None)
        if not callable(hou_parm):
            raise HiaRuntimeError(
                "EXPERIMENT_PARM_API_UNAVAILABLE",
                "The current runtime does not expose hou.parm",
            )
        by_input: dict[str, Any] = {}
        by_path: dict[str, Any] = {}
        originals: dict[str, Any] = {}
        for requested_path in input_paths:
            parm = hou_parm(requested_path)
            actual_path = _safe_path(parm) if parm is not None else ""
            owner_path = actual_path.rsplit("/", 1)[0] if "/" in actual_path else ""
            if (
                parm is None
                or not _valid_houdini_node_path(actual_path)
                or not _houdini_path_is_within(owner_path, target)
                or actual_path in by_path
                or not callable(getattr(parm, "set", None))
                or bool(_safe_call(parm, "isLocked", False))
                or bool(_safe_call(parm, "isDisabled", False))
            ):
                raise HiaRuntimeError(
                    "INVALID_EXPERIMENT_PARAMETER",
                    "A parameter is missing, duplicated, outside the target, or not writable",
                    {"requested_path": requested_path, "actual_path": actual_path},
                )
            keyframes = getattr(parm, "keyframes", None)
            time_dependent = getattr(parm, "isTimeDependent", None)
            if not callable(keyframes) or not callable(time_dependent):
                raise HiaRuntimeError(
                    "EXPERIMENT_RESTORE_NOT_PROVABLE",
                    "The parameter animation state cannot be inspected",
                    {"path": actual_path},
                )
            if list(keyframes()) or bool(time_dependent()):
                raise HiaRuntimeError(
                    "EXPERIMENT_PARM_NOT_STATIC",
                    "Only unkeyed, non-time-dependent scalar parameters can be restored safely",
                    {"path": actual_path},
                )
            try:
                original = scalar(parm.eval(), f"original parameter {actual_path}")
            except HiaRuntimeError:
                raise
            except Exception as exc:
                raise HiaRuntimeError(
                    "EXPERIMENT_RESTORE_NOT_PROVABLE",
                    "The original parameter value could not be read",
                    {"path": actual_path, "reason": _bounded_text(_redact_text(str(exc)), 1024)},
                ) from exc
            by_input[requested_path] = parm
            by_path[actual_path] = parm
            originals[actual_path] = original

        def normalize(values: Mapping[str, Any]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for requested_path, value in values.items():
                actual_path = _safe_path(by_input[requested_path])
                if actual_path in result:
                    raise HiaRuntimeError(
                        "INVALID_EXPERIMENT_PARAMETER",
                        "A variant resolves duplicate runtime parameters",
                        {"path": actual_path},
                    )
                result[actual_path] = value
            return result

        baseline_values = normalize(baseline["parameters"])
        candidate_values = [normalize(value["parameters"]) for value in candidates]
        baseline_state = dict(originals)
        baseline_state.update(baseline_values)

        reset_parms: list[tuple[str, Any]] = []
        for requested_path in self._absolute_node_paths(
            arguments.get("cache_reset_parms") or [],
            field_name="cache_reset_parms",
            maximum=8,
        ):
            parm = hou_parm(requested_path)
            actual_path = _safe_path(parm) if parm is not None else ""
            owner_path = actual_path.rsplit("/", 1)[0] if "/" in actual_path else ""
            if (
                parm is None
                or not _houdini_path_is_within(owner_path, target)
                or not callable(getattr(parm, "pressButton", None))
                or bool(_safe_call(parm, "isLocked", False))
                or bool(_safe_call(parm, "isDisabled", False))
                or actual_path in by_path
            ):
                raise HiaRuntimeError(
                    "INVALID_CACHE_RESET_PARAMETER",
                    "cache_reset_parms must be enabled buttons inside target_network",
                    {"path": requested_path},
                )
            reset_parms.append((actual_path, parm))

        def nodes(field: str, maximum: int, *, require_cook: bool) -> list[tuple[str, Any]]:
            values = self._absolute_node_paths(
                arguments.get(field) or [],
                field_name=field,
                maximum=maximum,
            )
            result = []
            for path in values:
                node = self._hou.node(path)
                if (
                    node is None
                    or not _houdini_path_is_within(path, target)
                    or (require_cook and not callable(getattr(node, "cook", None)))
                ):
                    raise HiaRuntimeError(
                        "INVALID_EXPERIMENT_TARGET",
                        f"{field} contains an unavailable target",
                        {"path": path},
                    )
                result.append((path, node))
            return result

        cook_nodes = nodes("cook_targets", 8, require_cook=True)
        metric_nodes = nodes("metric_targets", 4, require_cook=False)
        expected_deletions = self._absolute_node_paths(
            arguments.get("expected_deletions") or [],
            field_name="expected_deletions",
            maximum=16,
        )
        if target in expected_deletions or any(
            not _houdini_path_is_within(path, target)
            for path in expected_deletions
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "expected_deletions must be descendants of target_network",
            )

        raw_range = arguments.get("frame_range")
        samples = arguments.get("sample_frames")
        if (
            not isinstance(raw_range, (list, tuple))
            or len(raw_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_range)
            or not isinstance(samples, (list, tuple))
            or not 1 <= len(samples) <= MAX_EXPERIMENT_SAMPLE_FRAMES
            or any(isinstance(value, bool) or not isinstance(value, int) for value in samples)
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "frame_range and sample_frames require bounded whole frames",
            )
        start, end = map(int, raw_range)
        sample_frames = [int(value) for value in samples]
        if (
            end < start
            or end - start > int(MAX_FLIPBOOK_FRAME_SPAN)
            or any(a >= b for a, b in zip(sample_frames, sample_frames[1:]))
            or any(value < start or value > end for value in sample_frames)
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Frame range or ordered sample frames are out of bounds",
            )
        cook_calls = (1 + len(candidates)) * (end - start + 1) * len(cook_nodes)
        if cook_calls > MAX_EXPERIMENT_COOK_CALLS:
            raise HiaRuntimeError(
                "REQUEST_TOO_LARGE",
                "The bounded cook-call budget was exceeded",
                {"cook_calls": cook_calls, "maximum": MAX_EXPERIMENT_COOK_CALLS},
            )
        if str(arguments.get("capture_mode", "contact_sheet")) != "contact_sheet":
            raise HiaRuntimeError("INVALID_ARGUMENTS", "capture_mode must be contact_sheet")

        raw_metrics = arguments.get("metrics")
        metrics = (
            {"cook_evidence", "node_messages", "image_quality"}
            if raw_metrics is None
            else {str(value) for value in raw_metrics}
            if isinstance(raw_metrics, (list, tuple))
            else set()
        )
        if raw_metrics is not None and not isinstance(raw_metrics, (list, tuple)):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "metrics must be an array")
        if metric_nodes and raw_metrics is None:
            metrics.add("geometry_summary")
        allowed_metrics = {
            "cook_evidence",
            "node_messages",
            "geometry_summary",
            "image_quality",
        }
        if not metrics.issubset(allowed_metrics):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "metrics contains an unsupported value")
        if "geometry_summary" in metrics and not metric_nodes:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "geometry_summary requires metric_targets",
            )

        if not bool(_safe_call(self._hou, "isUIAvailable", True)):
            raise HiaRuntimeError("VIEWPORT_UNAVAILABLE", "A Houdini UI session is required")
        scene_viewer = self._hou.ui.curDesktop().paneTabOfType(
            self._hou.paneTabType.SceneViewer
        )
        if (
            scene_viewer is None
            or not callable(getattr(scene_viewer, "flipbook", None))
            or not callable(getattr(scene_viewer, "flipbookSettings", None))
        ):
            raise HiaRuntimeError("VIEWPORT_UNAVAILABLE", "SceneViewer.flipbook is unavailable")
        viewport = scene_viewer.curViewport()
        preview = arguments.get("preview") or {}
        if not isinstance(preview, Mapping):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "preview must be an object")
        resolution_request = {
            key: preview[key] for key in ("width", "height") if key in preview
        }
        base_width, base_height, source = self._capture_resolution(
            resolution_request,
            viewport,
        )
        try:
            quality_scale = float(preview.get("quality_scale", 1.0))
        except (TypeError, ValueError) as exc:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "preview.quality_scale must be numeric",
            ) from exc
        if not math.isfinite(quality_scale) or not 0.1 <= quality_scale <= 1.0:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "preview.quality_scale must be between 0.1 and 1.0",
            )
        scale = max(quality_scale, 64 / base_width, 64 / base_height)
        if not resolution_request:
            scale = min(scale, 1920 / max(base_width, base_height))
        width, height = round(base_width * scale), round(base_height * scale)
        if not 64 <= width <= 1920 or not 64 <= height <= 1920:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "The preview resolves outside 64-1920 pixels",
            )
        if scale != 1:
            source += "_quality_scaled"

        camera_path = str(arguments.get("camera_path") or "").strip()
        if camera_path and (
            not _valid_houdini_node_path(camera_path)
            or self._hou.node(camera_path) is None
        ):
            raise HiaRuntimeError("NODE_NOT_FOUND", "The experiment camera does not exist")
        framing = str(
            arguments.get("framing", "camera" if camera_path else "current_view")
        )
        if framing not in {"camera", "current_view"} or (
            (framing == "camera") != bool(camera_path)
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "framing and camera_path are inconsistent",
            )
        initial_view = _viewport_capture_source_state(
            self._hou,
            scene_viewer,
            viewport,
            scene_viewer.flipbookSettings().stash(),
            "",
            (width, height),
        )
        observed_display = str(
            initial_view.get("display_options", {}).get("shading", "unverified")
        )
        requested_display = str(arguments.get("display_mode") or "").strip()
        if requested_display and (
            observed_display == "unverified"
            or requested_display.casefold() != observed_display.casefold()
        ):
            raise HiaRuntimeError(
                "VIEWPORT_DISPLAY_MODE_MISMATCH",
                "The current viewport shading mode does not match display_mode",
                {"requested": requested_display, "observed": observed_display},
            )

        undos = getattr(self._hou, "undos", None)
        undo_group = getattr(undos, "group", None)
        undo_labels = getattr(undos, "undoLabels", None)
        perform_undo = getattr(undos, "performUndo", None)
        undos_enabled = getattr(undos, "areEnabled", None)
        if not all(
            callable(value)
            for value in (undo_group, undo_labels, perform_undo, undos_enabled)
        ) or not bool(undos_enabled()):
            raise HiaRuntimeError(
                "UNDO_ROLLBACK_UNAVAILABLE",
                "A private Houdini undo boundary is unavailable",
            )
        initial_snapshot, truncated = self._snapshot_map(target)
        if truncated:
            raise HiaRuntimeError(
                "EXPERIMENT_TARGET_TOO_LARGE",
                "The bounded target snapshot was truncated",
            )
        missing = sorted(set(expected_deletions).difference(initial_snapshot))
        if missing:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "expected_deletions was absent before execution",
                {"paths": missing},
            )
        capture_dir, storage_scope, _hip = self._artifact_directory(
            "screenshots",
            fallback=self._screenshot_root,
        )
        return {
            "target": target,
            "timeout_seconds": timeout_seconds,
            "baseline_name": baseline["name"],
            "candidates": [
                {"name": value["name"], "parameters": parameters}
                for value, parameters in zip(candidates, candidate_values, strict=True)
            ],
            "baseline_state": baseline_state,
            "baseline_overrides": baseline_values,
            "parms": by_path,
            "originals": originals,
            "reset_parms": reset_parms,
            "cook_nodes": cook_nodes,
            "metric_nodes": metric_nodes,
            "expected_deletions": expected_deletions,
            "start": start,
            "end": end,
            "sample_frames": sample_frames,
            "metrics": metrics,
            "scene_viewer": scene_viewer,
            "viewport": viewport,
            "initial_view": initial_view,
            "display_mode": observed_display,
            "camera_path": camera_path,
            "framing": framing,
            "width": width,
            "height": height,
            "quality_scale": quality_scale,
            "resolution_source": source,
            "undo_group": undo_group,
            "undo_labels": undo_labels,
            "perform_undo": perform_undo,
            "undo_labels_before": tuple(str(value) for value in undo_labels()),
            "initial_snapshot": initial_snapshot,
            "capture_dir": capture_dir,
            "storage_scope": storage_scope,
        }

    def _run_effect_experiment(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        cfg = self._effect_experiment_preflight(arguments)
        original_frame = float(_safe_call(self._hou, "frame", 1.0))
        dirty_before = self._dirty()
        label = f"HIA Effect Experiment {uuid.uuid4().hex}"
        variants = [
            {"name": cfg["baseline_name"], "kind": "baseline", "parameters": {}},
            *[
                {"name": value["name"], "kind": "candidate", "parameters": value["parameters"]}
                for value in cfg["candidates"]
            ],
        ]
        variant_results: list[dict[str, Any]] = []
        cells: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[dict[str, Any]] = []
        restore_errors: list[dict[str, Any]] = []
        fixed_state: dict[str, Any] | None = None
        fixed_digest: str | None = None
        view_lock_failed = False
        abort = False

        def same(expected: Any, actual: Any) -> bool:
            if (
                isinstance(expected, (int, float))
                and not isinstance(expected, bool)
                and isinstance(actual, (int, float))
                and not isinstance(actual, bool)
            ):
                return math.isclose(
                    float(expected),
                    float(actual),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            return expected == actual

        def digest(value: Any) -> str:
            payload = json.dumps(
                _json_value(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(payload).hexdigest()

        def classify_creations(paths: Iterable[str]) -> tuple[list[str], list[str]]:
            created = sorted(set(paths))
            ignored = [
                path
                for path in created
                if _safe_call(
                    self._hou.node(path),
                    "isInsideLockedHDA",
                    None,
                )
                is True
            ]
            return ignored, sorted(set(created) - set(ignored))

        def view_core(state: Mapping[str, Any]) -> dict[str, Any]:
            camera = dict(state.get("camera") or {})
            camera.pop("requested_path", None)
            return {
                "scene_viewer": state.get("scene_viewer"),
                "viewport": state.get("viewport"),
                "camera": camera,
                "display_options": state.get("display_options"),
                "color_management": state.get("color_management"),
            }

        def restore() -> list[dict[str, Any]]:
            failures = []
            for path, original in cfg["originals"].items():
                parm = cfg["parms"][path]
                try:
                    if not same(original, parm.eval()):
                        parm.set(original)
                    if not same(original, parm.eval()):
                        raise RuntimeError("parameter readback mismatch")
                except Exception as exc:
                    failures.append(
                        {
                            "operation": "restore_parameter",
                            "path": path,
                            "message": _bounded_text(_redact_text(str(exc)), 1024),
                        }
                    )
            try:
                self._hou.setFrame(original_frame)
                if not math.isclose(
                    float(self._hou.frame()),
                    original_frame,
                    rel_tol=0,
                    abs_tol=1e-6,
                ):
                    raise RuntimeError("frame readback mismatch")
            except Exception as exc:
                failures.append(
                    {
                        "operation": "restore_frame",
                        "message": _bounded_text(_redact_text(str(exc)), 1024),
                    }
                )
            return failures

        try:
            with cfg["undo_group"](label):
                try:
                    for variant in variants:
                        if abort:
                            variant_results.append(
                                {
                                    "name": variant["name"],
                                    "kind": variant["kind"],
                                    "status": "not_run",
                                    "errors": [{"code": "STRUCTURAL_CHANGE_ABORT"}],
                                }
                            )
                            continue
                        variant_started = time.monotonic()
                        local_errors: list[dict[str, Any]] = []
                        local_warnings: list[str] = []
                        cook_records: list[dict[str, Any]] = []
                        messages: list[dict[str, Any]] = []
                        frame_records: list[dict[str, Any]] = []
                        actual_parameters: list[dict[str, Any]] = []
                        reset_status = "not_proven" if not cfg["reset_parms"] else "pending"
                        effective = dict(cfg["baseline_state"])
                        effective.update(variant["parameters"])
                        try:
                            for path, value in cfg["baseline_state"].items():
                                cfg["parms"][path].set(value)
                            for path, value in variant["parameters"].items():
                                cfg["parms"][path].set(value)
                            for path, expected in effective.items():
                                actual = cfg["parms"][path].eval()
                                if not same(expected, actual):
                                    raise RuntimeError(f"{path} readback mismatch")
                                actual_parameters.append(
                                    {
                                        "path": path,
                                        "requested": _json_value(expected),
                                        "actual": _json_value(actual),
                                        "source": (
                                            "candidate"
                                            if path in variant["parameters"]
                                            else "baseline_override"
                                            if path in cfg["baseline_overrides"]
                                            else "scene_baseline"
                                        ),
                                    }
                                )
                            self._hou.setFrame(cfg["start"])
                            if float(self._hou.frame()) != float(cfg["start"]):
                                raise RuntimeError("configured start frame mismatch")
                            for _path, parm in cfg["reset_parms"]:
                                parm.pressButton()
                            if cfg["reset_parms"]:
                                reset_status = "observed"

                            for frame in range(cfg["start"], cfg["end"] + 1):
                                self._hou.setFrame(frame)
                                if float(self._hou.frame()) != float(frame):
                                    raise RuntimeError(f"frame {frame} lock failed")
                                frame_failed = False
                                for path, node in cfg["cook_nodes"]:
                                    before = self._cook_state(node)
                                    cook_error = None
                                    completed = False
                                    try:
                                        node.cook(force=True)
                                        completed = True
                                    except Exception as exc:
                                        cook_error = _bounded_text(_redact_text(str(exc)), 2048)
                                    after = self._cook_state(node)
                                    record = self._cook_evidence_record(
                                        path=path,
                                        frame=frame,
                                        requested=True,
                                        started=True,
                                        completed=completed,
                                        before=before,
                                        after=after,
                                        error=cook_error,
                                    )
                                    record["evidence"]["reset"] = reset_status
                                    cook_records.append(record)
                                    node_errors = [
                                        _bounded_text(_redact_text(str(value)), 1024)
                                        for value in list(_safe_call(node, "errors", ()))[:8]
                                        if not _is_cooking_interrupted_message(value)
                                    ]
                                    node_warnings = [
                                        _bounded_text(_redact_text(str(value)), 1024)
                                        for value in list(_safe_call(node, "warnings", ()))[:8]
                                    ]
                                    if node_errors or node_warnings:
                                        messages.append(
                                            {
                                                "frame": frame,
                                                "path": path,
                                                "errors": node_errors,
                                                "warnings": node_warnings,
                                            }
                                        )
                                    frame_failed = frame_failed or bool(cook_error or node_errors)
                                if frame_failed:
                                    raise RuntimeError(f"Cook failed at frame {frame}")
                                if frame not in cfg["sample_frames"]:
                                    continue
                                capture = self._capture_viewport(
                                    {
                                        "mode": "flipbook",
                                        "camera_path": cfg["camera_path"],
                                        "frame": frame,
                                        "width": cfg["width"],
                                        "height": cfg["height"],
                                        "return_image": False,
                                    }
                                )
                                observed = capture["result"]
                                if not capture["ok"] or any(
                                    not isinstance(observed.get(key), (int, float))
                                    or not math.isclose(
                                        float(observed[key]),
                                        float(frame),
                                        rel_tol=0,
                                        abs_tol=1e-6,
                                    )
                                    for key in ("actual_frame", "cook_frame")
                                ):
                                    raise RuntimeError(f"Capture frame {frame} was not proven")
                                source = observed.get("source_state") or {}
                                capture_state = {
                                    "camera": source.get("camera"),
                                    "viewport": source.get("viewport"),
                                    "display_options": source.get("display_options"),
                                    "color_management": source.get("color_management"),
                                    "resolution": [
                                        observed.get("width"),
                                        observed.get("height"),
                                        observed.get("aspect_ratio"),
                                    ],
                                }
                                capture_digest = digest(capture_state)
                                if fixed_digest is None:
                                    fixed_digest, fixed_state = capture_digest, capture_state
                                elif capture_digest != fixed_digest:
                                    view_lock_failed = True
                                    raise RuntimeError("Viewport state changed between captures")
                                frame_record = {
                                    "requested_frame": frame,
                                    "actual_frame": observed["actual_frame"],
                                    "cook_frame": observed["cook_frame"],
                                    "absolute_path": observed["absolute_path"],
                                    "width": observed["width"],
                                    "height": observed["height"],
                                    "aspect_ratio": observed["aspect_ratio"],
                                    "quality_status": observed["quality_status"],
                                }
                                if "image_quality" in cfg["metrics"]:
                                    frame_record["quality_metrics"] = observed.get("quality_metrics")
                                if "geometry_summary" in cfg["metrics"]:
                                    frame_record["geometry"] = [
                                        self._geometry_record(
                                            node,
                                            include_attributes=False,
                                            sample_limit=0,
                                            allow_cook=False,
                                        )
                                        for _path, node in cfg["metric_nodes"]
                                    ]
                                frame_records.append(frame_record)
                                cells.append(
                                    {
                                        "variant": variant["name"],
                                        "frame": frame,
                                        "absolute_path": observed["absolute_path"],
                                        "width": observed["width"],
                                        "height": observed["height"],
                                    }
                                )
                                local_warnings.extend(capture.get("warnings", []))
                        except Exception as exc:
                            if reset_status == "pending":
                                reset_status = "failed"
                            local_errors.append(
                                {
                                    "code": "EXPERIMENT_CANDIDATE_FAILED",
                                    "message": _bounded_text(_redact_text(str(exc)), 2048),
                                }
                            )

                        try:
                            snapshot, truncated = self._snapshot_map(cfg["target"])
                            structural = self._diff_maps(cfg["initial_snapshot"], snapshot)
                            deleted = set(structural["deleted"])
                            (
                                expected_observed,
                                expected_missing,
                                unexpected_deleted,
                            ) = _classify_expected_deletions(
                                deleted,
                                cfg["expected_deletions"],
                            )
                            (
                                ignored_locked_asset_internal,
                                unexpected_creations,
                            ) = classify_creations(
                                structural["created"]
                            )
                            structural.update(
                                {
                                    "expected_deletions": expected_observed,
                                    "missing_expected_deletions": expected_missing,
                                    "unexpected_deletions": unexpected_deleted,
                                    "unexpected_creations": unexpected_creations,
                                    "ignored_locked_asset_internal": (
                                        ignored_locked_asset_internal
                                    ),
                                    "truncated": truncated,
                                }
                            )
                            for code, key in (
                                ("EXPECTED_DELETION_NOT_OBSERVED", "missing_expected_deletions"),
                                ("UNEXPECTED_DELETION", "unexpected_deletions"),
                                ("UNEXPECTED_CREATION", "unexpected_creations"),
                            ):
                                if structural[key]:
                                    local_errors.append(
                                        {"code": code, "paths": structural[key][:16]}
                                    )
                            abort = bool(
                                structural["unexpected_creations"]
                                or structural["deleted"]
                            )
                        except Exception as exc:
                            structural = {
                                "status": "unavailable",
                                "error": _bounded_text(_redact_text(str(exc)), 1024),
                            }
                            local_errors.append({"code": "STRUCTURAL_DIFF_UNAVAILABLE"})

                        summary = self._summarize_cook_evidence(
                            cook_records,
                            requested=bool(cfg["cook_nodes"]),
                        )
                        freshness = (
                            "recompute_verified"
                            if reset_status == "observed"
                            and cook_records
                            and summary["assessment"] == "recompute_verified"
                            else "recompute_not_proven"
                        )
                        try:
                            hashes, temporal_evidence = _temporal_image_evidence(
                                [
                                    Path(str(value["absolute_path"]))
                                    for value in frame_records
                                ]
                            )
                            for value, image_hash in zip(
                                frame_records,
                                hashes,
                                strict=True,
                            ):
                                value["sha256"] = image_hash
                        except Exception as exc:
                            temporal_evidence = {
                                "sample_count": len(frame_records),
                                "unique_hash_count": 0,
                                "no_change_detected": False,
                                "simulation_advancement": "unavailable",
                            }
                            local_errors.append(
                                {
                                    "code": "TEMPORAL_EVIDENCE_UNAVAILABLE",
                                    "message": _bounded_text(
                                        _redact_text(str(exc)),
                                        1024,
                                    ),
                                }
                            )
                        variant_results.append(
                            {
                                "name": variant["name"],
                                "kind": variant["kind"],
                                "status": "failed" if local_errors else "completed",
                                "actual_parameters": actual_parameters,
                                "frame_range": [cfg["start"], cfg["end"]],
                                "frames_evaluated": cfg["end"] - cfg["start"] + 1,
                                "sample_frames": frame_records,
                                "temporal_evidence": temporal_evidence,
                                "cache_reset": {
                                    "status": reset_status,
                                    "parameters": [path for path, _parm in cfg["reset_parms"]],
                                },
                                "freshness": freshness,
                                "cook_errors_and_warnings": (
                                    messages
                                    if "node_messages" in cfg["metrics"]
                                    else [value for value in messages if value["errors"]]
                                ),
                                "cook_evidence": (
                                    summary
                                    if "cook_evidence" in cfg["metrics"]
                                    else {
                                        key: summary[key]
                                        for key in (
                                            "assessment",
                                            "counts",
                                            "target_count",
                                            "calls_completed",
                                        )
                                    }
                                ),
                                "structural_diff": {
                                    key: value[:64] if isinstance(value, list) else value
                                    for key, value in structural.items()
                                },
                                "warnings": list(dict.fromkeys(local_warnings))[:32],
                                "errors": local_errors,
                                "elapsed_seconds": _seconds(time.monotonic() - variant_started),
                            }
                        )
                finally:
                    restore_errors.extend(restore())
        except Exception as exc:
            errors.append(
                {
                    "code": "EXPERIMENT_EXECUTION_FAILED",
                    "message": _bounded_text(_redact_text(str(exc)), 2048),
                }
            )

        undo = {"label": label, "status": "not_proven", "user_history_untouched": True}
        try:
            labels = tuple(str(value) for value in cfg["undo_labels"]())
            if labels == cfg["undo_labels_before"]:
                undo["status"] = "no_undo_item"
            elif labels and labels[0] == label:
                cfg["perform_undo"]()
                if tuple(str(value) for value in cfg["undo_labels"]()) != cfg["undo_labels_before"]:
                    raise RuntimeError("undo labels were not restored")
                undo["status"] = "rolled_back_own_item"
            else:
                raise RuntimeError("own undo item was not current; user history was not touched")
        except Exception as exc:
            undo["error"] = _bounded_text(_redact_text(str(exc)), 1024)
        restore_errors.extend(restore())

        parm_evidence = []
        for path, original in cfg["originals"].items():
            try:
                actual = cfg["parms"][path].eval()
                restored = same(original, actual)
            except Exception as exc:
                actual, restored = f"<readback failed: {exc}>", False
            parm_evidence.append(
                {
                    "path": path,
                    "original": _json_value(original),
                    "actual": _json_value(actual),
                    "restored": restored,
                }
            )
        try:
            final_snapshot, truncated = self._snapshot_map(cfg["target"])
            network_diff = self._diff_maps(cfg["initial_snapshot"], final_snapshot)
            (
                ignored_locked_asset_internal,
                unexpected_creations,
            ) = classify_creations(network_diff["created"])
            network_diff.update(
                {
                    "unexpected_creations": unexpected_creations,
                    "ignored_locked_asset_internal": (
                        ignored_locked_asset_internal
                    ),
                    "truncated": truncated,
                }
            )
        except Exception as exc:
            network_diff = {
                "status": "unavailable",
                "error": _bounded_text(_redact_text(str(exc)), 1024),
            }
            restore_errors.append({"operation": "verify_network_restoration"})
        final_view = _viewport_capture_source_state(
            self._hou,
            cfg["scene_viewer"],
            cfg["viewport"],
            cfg["scene_viewer"].flipbookSettings().stash(),
            "",
            (cfg["width"], cfg["height"]),
        )
        final_frame = _safe_call(self._hou, "frame", None)
        frame_restored = isinstance(final_frame, (int, float)) and math.isclose(
            float(final_frame),
            original_frame,
            rel_tol=0,
            abs_tol=1e-6,
        )
        network_restored = (
            network_diff.get("status") != "unavailable"
            and not network_diff.get("unexpected_creations")
            and not network_diff.get("deleted")
            and not network_diff.get("changed")
            and not network_diff.get("truncated")
        )
        view_restored = digest(view_core(cfg["initial_view"])) == digest(view_core(final_view))
        dirty_after = self._dirty()
        restoration_verified = (
            not restore_errors
            and all(value["restored"] for value in parm_evidence)
            and frame_restored
            and network_restored
            and view_restored
            and dirty_after == dirty_before
            and undo["status"] in {"no_undo_item", "rolled_back_own_item"}
        )
        restoration = {
            "status": "verified" if restoration_verified else "failed",
            "parameters": parm_evidence,
            "frame": {"before": original_frame, "after": _json_value(final_frame), "restored": frame_restored},
            "view_state_restored": view_restored,
            "network": network_diff,
            "dirty": {"before": dirty_before, "after": dirty_after, "restored": dirty_after == dirty_before},
            "undo": undo,
            "cache_state": "not_restorable_or_observable_through_generic_HOM",
            "errors": restore_errors[:32],
        }
        if restore_errors:
            errors.append({"code": "EXPERIMENT_RESTORE_FAILED", "details": restore_errors[:32]})

        try:
            contact_sheet = (
                self._write_effect_contact_sheet(
                    capture_dir=cfg["capture_dir"],
                    storage_scope=cfg["storage_scope"],
                    variants=[value["name"] for value in variants],
                    sample_frames=cfg["sample_frames"],
                    cells=cells,
                )
                if cells
                else {"status": "not_created", "reason": "No sample frame was captured"}
            )
        except Exception as exc:
            contact_sheet = {
                "status": "failed",
                "error": _bounded_text(_redact_text(str(exc)), 2048),
            }
            errors.append({"code": "CONTACT_SHEET_FAILED", "message": contact_sheet["error"]})

        incomplete = [
            value["name"]
            for value in variant_results
            if value.get("status") != "completed"
        ]
        if incomplete:
            errors.append({"code": "EXPERIMENT_VARIANTS_INCOMPLETE", "variants": incomplete})
        if not cfg["reset_parms"]:
            warnings.append(
                "No cache_reset_parms were supplied; force cooking does not prove retained simulation steps were cleared"
            )
        if not cfg["cook_nodes"]:
            warnings.append(
                "No cook_targets were supplied; viewport evaluation does not prove target recomputation"
            )
        complete = (
            not errors
            and len(variant_results) == len(variants)
            and restoration_verified
            and contact_sheet.get("status") == "created"
            and fixed_digest is not None
            and not view_lock_failed
        )
        return {
            "ok": complete,
            "result": {
                "status": "completed" if complete else "partial" if cells and restoration_verified else "failed",
                "target_network": cfg["target"],
                "variants": variant_results,
                "frame_range": [cfg["start"], cfg["end"]],
                "sample_frames": cfg["sample_frames"],
                "preview": {
                    "width": cfg["width"],
                    "height": cfg["height"],
                    "aspect_ratio": round(cfg["width"] / cfg["height"], 8),
                    "quality_scale": cfg["quality_scale"],
                    "resolution_source": cfg["resolution_source"],
                },
                "view_lock": {
                    "status": "verified" if fixed_digest and not view_lock_failed else "not_proven",
                    "framing": cfg["framing"],
                    "camera_path": cfg["camera_path"] or None,
                    "display_mode": cfg["display_mode"],
                    "signature": fixed_digest,
                    "state": fixed_state,
                },
                "cache_reset": (
                    "observed"
                    if cfg["reset_parms"]
                    and variant_results
                    and all(
                        value.get("cache_reset", {}).get("status") == "observed"
                        for value in variant_results
                        if value.get("status") != "not_run"
                    )
                    else "not_proven"
                ),
                "contact_sheet": contact_sheet,
                "restoration": restoration,
                "incomplete_variants": incomplete,
                "elapsed_seconds": _seconds(time.monotonic() - started),
                "limitations": [
                    "Generic HOM cannot restore or universally observe cache contents",
                    "The tool records evidence but Codex evaluates candidate quality",
                ],
            },
            "warnings": warnings[:32],
            "errors": errors[:32],
            "revision": self.scene_revision,
            "dirty": dirty_after,
            "execution_limit": {
                "requested_timeout_seconds": cfg["timeout_seconds"],
                "timeout_kind": "client_wait_budget",
                "interruptible_after_main_thread_entry": False,
                "hom_may_continue_after_client_timeout": True,
                "automatic_retry_after_timeout": False,
            },
        }

    def _write_effect_contact_sheet(
        self,
        *,
        capture_dir: Path,
        storage_scope: str,
        variants: list[str],
        sample_frames: list[int],
        cells: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not cells:
            raise RuntimeError("No captured frames were supplied")
        cell_by_key = {
            (str(cell["variant"]), int(cell["frame"])): cell
            for cell in cells
        }
        first = cells[0]
        source_width = int(first["width"])
        source_height = int(first["height"])
        aspect = source_width / source_height
        image_width = min(320, max(1, int(round(180 * aspect))))
        image_height = min(180, max(1, int(round(320 / aspect))))
        if image_width / image_height > aspect:
            image_width = max(1, int(round(image_height * aspect)))
        else:
            image_height = max(1, int(round(image_width / aspect)))
        cell_width = 340
        cell_height = image_height + 58
        sheet_width = cell_width * len(sample_frames)
        sheet_height = cell_height * len(variants)
        parts = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{sheet_width}" height="{sheet_height}" '
                f'viewBox="0 0 {sheet_width} {sheet_height}">'
            ),
            "<rect width=\"100%\" height=\"100%\" fill=\"#15181d\"/>",
            (
                "<style>text{font-family:Segoe UI,Arial,sans-serif;"
                "fill:#f2f4f8;font-size:14px}.frame{fill:#b9c2cf;"
                "font-size:12px}.missing{fill:#2c313a;stroke:#d85c5c;"
                "stroke-width:2}</style>"
            ),
        ]
        sources: list[dict[str, Any]] = []
        for row, variant in enumerate(variants):
            for column, frame in enumerate(sample_frames):
                x = column * cell_width
                y = row * cell_height
                label = html.escape(variant)
                parts.append(
                    f'<text x="{x + 10}" y="{y + 20}">{label}</text>'
                )
                parts.append(
                    (
                        f'<text class="frame" x="{x + 10}" y="{y + 39}">'
                        f'frame {frame}</text>'
                    )
                )
                cell = cell_by_key.get((variant, frame))
                image_x = x + (cell_width - image_width) // 2
                image_y = y + 48
                if cell is None:
                    parts.append(
                        (
                            f'<rect class="missing" x="{image_x}" '
                            f'y="{image_y}" width="{image_width}" '
                            f'height="{image_height}"/>'
                        )
                    )
                    continue
                source_path = Path(str(cell["absolute_path"])).resolve(
                    strict=True
                )
                if source_path.parent != capture_dir.resolve(strict=True):
                    raise RuntimeError(
                        "A contact-sheet source escaped its capture directory"
                    )
                encoded = base64.b64encode(source_path.read_bytes()).decode(
                    "ascii"
                )
                parts.append(
                    (
                        f'<image x="{image_x}" y="{image_y}" '
                        f'width="{image_width}" height="{image_height}" '
                        f'preserveAspectRatio="xMidYMid meet" '
                        f'href="data:image/png;base64,{encoded}"/>'
                    )
                )
                sources.append(
                    {
                        "variant": variant,
                        "frame": frame,
                        "absolute_path": str(source_path),
                        "width": int(cell["width"]),
                        "height": int(cell["height"]),
                    }
                )
        parts.append("</svg>")
        identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        output_path = capture_dir / f"effect-experiment-{identifier}.svg"
        output_path.write_text("".join(parts), encoding="utf-8")
        absolute_path = str(output_path.resolve(strict=True))
        display_path = (
            output_path.relative_to(self._project_root).as_posix()
            if storage_scope == "runtime_fallback"
            else absolute_path
        )
        return {
            "status": "created",
            "path": display_path,
            "absolute_path": absolute_path,
            "storage_scope": storage_scope,
            "mime_type": "image/svg+xml",
            "embedded_images": True,
            "width": sheet_width,
            "height": sheet_height,
            "columns": len(sample_frames),
            "rows": len(variants),
            "variants": variants,
            "frames": sample_frames,
            "source_frames": sources,
        }

    def _scene_diff(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", ""))
        snapshot_id = str(arguments.get("snapshot_id", ""))
        root_path = str(arguments.get("root_path", "/"))
        limit = _limit(arguments)
        if action == "capture":
            try:
                nodes, truncated = self._snapshot_map(root_path)
                root_exists = True
            except HiaRuntimeError as exc:
                if exc.code != "NODE_NOT_FOUND":
                    raise
                nodes, truncated = {}, False
                root_exists = False
            snapshot_id = snapshot_id or f"snapshot-{uuid.uuid4().hex}"
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", snapshot_id):
                raise HiaRuntimeError("INVALID_ARGUMENTS", "snapshot_id has an invalid format")
            with self._state_lock:
                if snapshot_id not in self._snapshots and len(self._snapshots) >= MAX_SNAPSHOTS:
                    oldest = min(self._snapshots.values(), key=lambda item: item.created_at)
                    self._snapshots.pop(oldest.snapshot_id, None)
                self._snapshots[snapshot_id] = _Snapshot(
                    snapshot_id=snapshot_id,
                    root_path=root_path,
                    nodes=nodes,
                    truncated=truncated,
                    scene_revision=self._scene_revision,
                    created_at=time.time(),
                )
            return self._success(
                {
                    "snapshot_id": snapshot_id,
                    "root_path": root_path,
                    "root_exists": root_exists,
                    "node_count": len(nodes),
                    "truncated": truncated,
                }
            )
        if action == "list":
            with self._state_lock:
                entries = [
                    {
                        "snapshot_id": item.snapshot_id,
                        "root_path": item.root_path,
                        "node_count": len(item.nodes),
                        "truncated": item.truncated,
                        "scene_revision": item.scene_revision,
                    }
                    for item in self._snapshots.values()
                ]
            return self._success({"snapshots": entries[:limit], "total": len(entries)})
        if not snapshot_id:
            raise HiaRuntimeError("INVALID_ARGUMENTS", "snapshot_id is required for compare or forget")
        with self._state_lock:
            snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise HiaRuntimeError("SNAPSHOT_NOT_FOUND", "The scene snapshot does not exist", {"snapshot_id": snapshot_id})
        if action == "forget":
            with self._state_lock:
                self._snapshots.pop(snapshot_id, None)
            return self._success({"forgotten": snapshot_id})
        if action != "compare":
            raise HiaRuntimeError("INVALID_ARGUMENTS", "Unsupported scene diff action")
        expected_deletions = self._absolute_node_paths(
            arguments.get("expected_deletions") or [],
            field_name="expected_deletions",
            maximum=1_024,
        )
        try:
            current, truncated = self._snapshot_map(snapshot.root_path)
            root_exists = True
        except HiaRuntimeError as exc:
            if exc.code != "NODE_NOT_FOUND":
                raise
            current, truncated = {}, False
            root_exists = False
        diff = self._diff_maps(snapshot.nodes, current)
        observed_deleted = set(diff["deleted"])
        (
            diff["expected_deletions"],
            diff["missing_expected_deletions"],
            diff["unexpected_deletions"],
        ) = _classify_expected_deletions(
            observed_deleted,
            expected_deletions,
        )
        for key in (
            "created",
            "deleted",
            "changed",
            "expected_deletions",
            "missing_expected_deletions",
            "unexpected_deletions",
        ):
            diff[key] = diff[key][:limit]
        return self._success(
            {
                "snapshot_id": snapshot_id,
                "root_path": snapshot.root_path,
                "root_exists": root_exists,
                "diff": diff,
                "truncated": snapshot.truncated or truncated,
                "snapshot_revision": snapshot.scene_revision,
                "current_revision": self.scene_revision,
            }
        )

    def _capture_frame_values(
        self,
        arguments: Mapping[str, Any],
        *,
        mode: str,
        current_frame: float,
    ) -> list[float]:
        selectors = [
            name
            for name in ("frame", "frames", "frame_range")
            if name in arguments
        ]
        if len(selectors) > 1:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Provide only one of frame, frames, or frame_range",
                {"fields": selectors},
            )
        if "frame_step" in arguments and "frame_range" not in arguments:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "frame_step is valid only with frame_range",
            )

        def finite_frame(value: Any, field: str) -> float:
            try:
                frame = float(value)
            except (TypeError, ValueError) as exc:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field} must contain finite frame numbers",
                ) from exc
            if not math.isfinite(frame):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field} must contain finite frame numbers",
                )
            return frame

        if "frame" in arguments:
            frames = [finite_frame(arguments["frame"], "frame")]
        elif "frames" in arguments:
            raw_frames = arguments["frames"]
            if (
                not isinstance(raw_frames, (list, tuple))
                or not 1 <= len(raw_frames) <= MAX_CAPTURE_FRAMES
            ):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"frames must contain 1-{MAX_CAPTURE_FRAMES} frame numbers",
                )
            frames = [
                finite_frame(value, f"frames[{index}]")
                for index, value in enumerate(raw_frames)
            ]
        elif "frame_range" in arguments:
            raw_range = arguments["frame_range"]
            if (
                not isinstance(raw_range, (list, tuple))
                or len(raw_range) != 2
            ):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "frame_range must contain exactly two frame numbers",
                )
            start = finite_frame(raw_range[0], "frame_range")
            end = finite_frame(raw_range[1], "frame_range")
            if end < start:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "frame_range end must not precede its start",
                )
            if end - start > MAX_FLIPBOOK_FRAME_SPAN:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"frame_range may span at most {int(MAX_FLIPBOOK_FRAME_SPAN)} frames",
                    {
                        "frame_range": [start, end],
                        "maximum_frame_span": int(MAX_FLIPBOOK_FRAME_SPAN),
                    },
                )
            if not start.is_integer() or not end.is_integer():
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Multi-frame frame_range endpoints must be whole frames",
                )
            step = _bounded_int(arguments.get("frame_step", 1), 1, 240)
            frames = [
                float(value)
                for value in range(int(start), int(end) + 1, step)
            ]
            if len(frames) > MAX_CAPTURE_FRAMES:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"A capture sequence may contain at most {MAX_CAPTURE_FRAMES} frames",
                    {
                        "frame_count": len(frames),
                        "maximum_frame_count": MAX_CAPTURE_FRAMES,
                    },
                )
        else:
            frames = [current_frame]
        if len(frames) > 1:
            if mode != "flipbook":
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Multiple frames require mode=flipbook",
                )
            if any(not frame.is_integer() for frame in frames):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Capture sequences require whole frame numbers",
                )
            if any(
                current >= following
                for current, following in zip(frames, frames[1:])
            ):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Sequence frames must be strictly increasing",
                )
        return frames

    @staticmethod
    def _capture_resolution(
        arguments: Mapping[str, Any],
        viewport: Any,
    ) -> tuple[int, int, str]:
        raw_width = arguments.get("width")
        raw_height = arguments.get("height")
        width = (
            _bounded_int(raw_width, 64, 4096)
            if raw_width is not None
            else None
        )
        height = (
            _bounded_int(raw_height, 64, 4096)
            if raw_height is not None
            else None
        )
        size = _safe_call(viewport, "size", ())
        viewport_width = viewport_height = 0
        if (
            isinstance(size, (list, tuple))
            and len(size) == 4
            and all(isinstance(value, (int, float)) for value in size)
        ):
            viewport_width = int(round(float(size[2])))
            viewport_height = int(round(float(size[3])))
        if width is not None and height is not None:
            return width, height, "requested"
        if viewport_width <= 0 or viewport_height <= 0:
            raise HiaRuntimeError(
                "VIEWPORT_RESOLUTION_UNAVAILABLE",
                "Specify width and height because the live viewport size could not be observed",
            )
        aspect = viewport_width / viewport_height
        if width is None and height is None:
            scale = min(1.0, 4096 / max(viewport_width, viewport_height))
            width = int(round(viewport_width * scale))
            height = int(round(viewport_height * scale))
            source = "viewport"
        elif width is None:
            width = int(round(height * aspect))
            source = "requested_height_viewport_aspect"
        else:
            height = int(round(width / aspect))
            source = "requested_width_viewport_aspect"
        if not 64 <= width <= 4096 or not 64 <= height <= 4096:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "The derived viewport resolution is outside 64-4096 pixels",
                {
                    "derived_resolution": [width, height],
                    "viewport_resolution": [viewport_width, viewport_height],
                },
            )
        return width, height, source

    def _capture_viewport_sequence(
        self,
        arguments: Mapping[str, Any],
        frames: list[float],
    ) -> dict[str, Any]:
        original_frame = float(_safe_call(self._hou, "frame", 1.0))
        frame_records: list[dict[str, Any]] = []
        successful: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        restore_error: str | None = None
        try:
            for requested_frame in frames:
                member_arguments = dict(arguments)
                for name in ("frame", "frames", "frame_step", "expect_change"):
                    member_arguments.pop(name, None)
                member_arguments["mode"] = "flipbook"
                member_arguments["frame_range"] = [
                    requested_frame,
                    requested_frame,
                ]
                member_arguments["return_image"] = False
                try:
                    response = self._capture_viewport(member_arguments)
                except HiaRuntimeError as exc:
                    frame_records.append(
                        {
                            "requested_frame": requested_frame,
                            "actual_frame": None,
                            "cook_frame": None,
                            "status": "failed",
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                            },
                        }
                    )
                    errors.append(
                        f"Frame {requested_frame:g}: {exc.code}: {exc.message}"
                    )
                    if exc.code == "VIEWPORT_STATE_RESTORE_FAILED":
                        break
                    continue
                captured = response["result"]
                path = Path(captured["absolute_path"])
                record = {
                    "requested_frame": requested_frame,
                    "actual_frame": captured.get("actual_frame"),
                    "cook_frame": captured.get("cook_frame"),
                    "status": "captured" if response["ok"] else "failed",
                    "width": captured.get("width"),
                    "height": captured.get("height"),
                    "absolute_path": str(path),
                }
                frame_records.append(record)
                if response["ok"]:
                    successful.append(response)
                else:
                    errors.extend(str(value) for value in response["errors"])
                warnings.extend(str(value) for value in response["warnings"])
        finally:
            try:
                self._hou.setFrame(original_frame)
                restored_frame = float(self._hou.frame())
                if not math.isclose(
                    restored_frame,
                    original_frame,
                    rel_tol=0.0,
                    abs_tol=1.0e-6,
                ):
                    raise RuntimeError(
                        f"restored frame is {restored_frame:g}, expected {original_frame:g}"
                    )
            except Exception as exc:
                restore_error = _bounded_text(_redact_text(str(exc)), 1024)
        if restore_error is not None:
            raise HiaRuntimeError(
                "VIEWPORT_STATE_RESTORE_FAILED",
                "The frame sequence finished, but the original frame could not be restored",
                {"errors": [{"operation": "restore_frame", "message": restore_error}]},
            )

        captured_records = [
            record for record in frame_records if record["status"] == "captured"
        ]
        hashes, temporal_evidence = _temporal_image_evidence(
            [Path(str(record["absolute_path"])) for record in captured_records]
        )
        for record, digest in zip(captured_records, hashes, strict=True):
            record["sha256"] = digest
        no_change = bool(temporal_evidence["no_change_detected"])
        expect_change = bool(arguments.get("expect_change", True))
        missing_frames = [
            record["requested_frame"]
            for record in frame_records
            if record["status"] != "captured"
        ]
        if missing_frames:
            temporal_status = "failed"
        elif no_change and expect_change:
            temporal_status = "not_proven"
            warnings.append(
                "All captured frames are byte-identical; temporal change or simulation advancement was not proven"
            )
        else:
            temporal_status = "passed"
        evidence_paths = [
            record["absolute_path"]
            for record in (
                captured_records[:1] + captured_records[-1:]
                if len(captured_records) > 1
                else captured_records
            )
        ]
        compact_frames = []
        for record in frame_records:
            compact = {
                key: value
                for key, value in record.items()
                if key != "absolute_path"
            }
            if record.get("absolute_path") in evidence_paths:
                compact["evidence_path"] = record["absolute_path"]
            compact_frames.append(compact)
        first = successful[0] if successful else None
        first_result = dict(first["result"]) if first is not None else {}
        first_result.update(
            {
                "mode": "sequence",
                "requested_frames": frames,
                "actual_frames": [
                    record.get("actual_frame") for record in frame_records
                ],
                "sequence": {
                    "status": temporal_status,
                    "requested_count": len(frames),
                    "captured_count": len(captured_records),
                    "failed_count": len(frames) - len(captured_records),
                    "missing_or_failed_frames": missing_frames,
                    **temporal_evidence,
                    "temporal_jump_detected": bool(missing_frames),
                    "frames": compact_frames,
                    "evidence_paths": evidence_paths,
                },
            }
        )
        result: dict[str, Any] = {
            "ok": bool(successful) and not missing_frames,
            "result": first_result,
            "warnings": list(dict.fromkeys(warnings))[:32],
            "errors": list(dict.fromkeys(errors))[:32],
            "revision": self.scene_revision,
            "dirty": self._dirty(),
        }
        return result

    def _capture_viewport(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not bool(_safe_call(self._hou, "isUIAvailable", True)):
            raise HiaRuntimeError("VIEWPORT_UNAVAILABLE", "Viewport capture requires a graphical Houdini session")
        mode = str(arguments.get("mode", "viewport"))
        if mode not in {"viewport", "flipbook"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Viewport capture mode must be viewport or flipbook",
                {"mode": mode},
            )
        current_frame = float(_safe_call(self._hou, "frame", 1.0))
        frame_values = self._capture_frame_values(
            arguments,
            mode=mode,
            current_frame=current_frame,
        )
        if len(frame_values) > 1:
            return self._capture_viewport_sequence(arguments, frame_values)
        requested_frame = frame_values[0]
        validated_frame_range = (requested_frame, requested_frame)
        desktop = self._hou.ui.curDesktop()
        scene_viewer = desktop.paneTabOfType(self._hou.paneTabType.SceneViewer)
        if scene_viewer is None:
            raise HiaRuntimeError("VIEWPORT_UNAVAILABLE", "No Scene Viewer pane is available")
        viewport = scene_viewer.curViewport()
        width, height, resolution_source = self._capture_resolution(
            arguments,
            viewport,
        )
        camera_path = str(arguments.get("camera_path", ""))
        camera = None
        if camera_path:
            camera = self._hou.node(camera_path)
            if camera is None:
                raise HiaRuntimeError("NODE_NOT_FOUND", "The viewport camera does not exist", {"path": camera_path})
        capture_dir, storage_scope, _source_hip_path = self._artifact_directory(
            "screenshots",
            fallback=self._screenshot_root,
        )
        identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        frame = int(round(requested_frame))
        output_path = capture_dir / f"viewport-{identifier}-{frame:04d}.png"
        original_camera = viewport.camera() if camera is not None else None
        saved_default_camera = None
        original_camera_lock = False
        if camera is not None:
            if original_camera is None:
                saved_default_camera = viewport.defaultCamera().stash()
            original_camera_lock = bool(viewport.isCameraLockedToView())
        original_frame = float(self._hou.frame())
        validation_paths = self._absolute_node_paths(
            arguments.get("validation_paths") or [],
            field_name="validation_paths",
            maximum=16,
        )
        frame_lock_evidence: dict[str, Any] = {
            "requested_frame": requested_frame,
            "frame_before": original_frame,
            "frame_before_capture": None,
            "cook_frame": None,
            "actual_frame": None,
            "restored_frame": None,
        }
        per_frame_cook_records: list[dict[str, Any]] = []
        capture_error: Exception | None = None
        capture_api = ""
        source_state: dict[str, Any] = {}
        restore_errors: list[dict[str, str]] = []
        try:
            self._hou.setFrame(requested_frame)
            actual_before_capture = float(self._hou.frame())
            frame_lock_evidence["frame_before_capture"] = actual_before_capture
            if not math.isclose(
                actual_before_capture,
                requested_frame,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            ):
                raise RuntimeError(
                    f"Houdini set frame {actual_before_capture:g}, expected {requested_frame:g}"
                )
            for path in validation_paths:
                node = self._hou.node(path)
                if node is None:
                    raise RuntimeError(
                        f"Validation target does not exist at frame {requested_frame:g}: {path}"
                    )
                before = self._cook_state(node)
                error = None
                completed = False
                try:
                    node.cook(force=True)
                    completed = True
                except Exception as exc:
                    error = _bounded_text(_redact_text(str(exc)), 2048)
                after = self._cook_state(node)
                record = self._cook_evidence_record(
                    path=path,
                    frame=requested_frame,
                    requested=True,
                    started=True,
                    completed=completed,
                    before=before,
                    after=after,
                    error=error,
                )
                per_frame_cook_records.append(record)
                if error is not None:
                    raise RuntimeError(
                        f"Fresh cook failed at frame {requested_frame:g} for {path}: {error}"
                    )
            cook_frame = float(self._hou.frame())
            frame_lock_evidence["cook_frame"] = cook_frame
            if not math.isclose(
                cook_frame,
                requested_frame,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            ):
                raise RuntimeError(
                    f"Cook changed frame to {cook_frame:g}, expected {requested_frame:g}"
                )
            if camera is not None:
                viewport.lockCameraToView(False)
                viewport.setCamera(camera)
            can_flipbook = callable(getattr(scene_viewer, "flipbook", None)) and callable(
                getattr(scene_viewer, "flipbookSettings", None)
            )
            if can_flipbook:
                settings = scene_viewer.flipbookSettings().stash()
                source_state = _viewport_capture_source_state(
                    self._hou,
                    scene_viewer,
                    viewport,
                    settings,
                    camera_path,
                    (width, height),
                )
                start, end = validated_frame_range
                prefix = "viewport" if mode == "viewport" else "flipbook"
                pattern = capture_dir / f"{prefix}-{identifier}-$F4.png"
                settings.frameRange((start, end))
                settings.output(str(pattern))
                settings.resolution((width, height))
                settings.useResolution(True)
                settings.outputZoom(100)
                settings.useSheetSize(False)
                settings.outputToMPlay(False)
                crop_method = getattr(settings, "cropOutMaskOverlay", None)
                if callable(crop_method):
                    try:
                        crop_method(False)
                    except TypeError:
                        pass
                scene_viewer.flipbook(viewport, settings, open_dialog=False)
                output_path = capture_dir / (
                    f"{prefix}-{identifier}-{int(round(start)):04d}.png"
                )
                capture_api = "scene_viewer.flipbook"
            elif mode == "viewport" and callable(
                getattr(viewport, "saveViewToImage", None)
            ):
                source_state = _viewport_capture_source_state(
                    self._hou,
                    scene_viewer,
                    viewport,
                    None,
                    camera_path,
                    (width, height),
                )
                viewport.saveViewToImage(str(output_path))
                capture_api = "viewport.saveViewToImage_fallback"
            else:
                raise RuntimeError(
                    "The documented SceneViewer.flipbook capture API is unavailable"
                )
            actual_frame = float(self._hou.frame())
            frame_lock_evidence["actual_frame"] = actual_frame
            if not math.isclose(
                actual_frame,
                requested_frame,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            ):
                raise RuntimeError(
                    f"Captured frame is {actual_frame:g}, expected {requested_frame:g}"
                )
        except Exception as exc:
            capture_error = exc
        finally:
            def restore(operation: str, callback: Callable[[], Any]) -> None:
                try:
                    callback()
                except Exception as exc:
                    restore_errors.append(
                        {
                            "operation": operation,
                            "message": _bounded_text(_redact_text(str(exc)), 1024),
                        }
                    )

            if camera is not None:
                restore("unlock_camera", lambda: viewport.lockCameraToView(False))
                if original_camera is None:
                    restore("use_default_camera", viewport.useDefaultCamera)
                    restore(
                        "restore_default_camera",
                        lambda: viewport.setDefaultCamera(saved_default_camera),
                    )
                else:
                    restore("restore_camera", lambda: viewport.setCamera(original_camera))
                restore(
                    "restore_camera_lock",
                    lambda: viewport.lockCameraToView(original_camera_lock),
                )
            restore("restore_frame", lambda: self._hou.setFrame(original_frame))
            frame_lock_evidence["restored_frame"] = _json_value(
                _safe_call(self._hou, "frame", None)
            )
            restored_frame = frame_lock_evidence["restored_frame"]
            if (
                not isinstance(restored_frame, (int, float))
                or not math.isclose(
                    float(restored_frame),
                    original_frame,
                    rel_tol=0.0,
                    abs_tol=1.0e-6,
                )
            ):
                restore_errors.append(
                    {
                        "operation": "verify_restored_frame",
                        "message": (
                            f"Restored frame {restored_frame!r} does not match "
                            f"original frame {original_frame:g}"
                        ),
                    }
                )
        if capture_error is not None:
            details = {
                "frame_lock": frame_lock_evidence,
                "restore_errors": restore_errors,
            }
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_FAILED",
                _bounded_text(_redact_text(str(capture_error)), 2048),
                details,
            ) from capture_error
        if restore_errors:
            raise HiaRuntimeError(
                "VIEWPORT_STATE_RESTORE_FAILED",
                "The viewport image was captured but the original viewer state could not be fully restored",
                {"errors": restore_errors},
            )
        if not output_path.is_file():
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_FAILED",
                "Houdini did not produce the expected viewport image",
                {"path": str(output_path)},
            )
        with output_path.open("rb") as stream:
            actual_width, actual_height = _png_dimensions(stream.read(24))
        resolution_state = source_state.setdefault("resolution", {})
        resolution_state.update(
            {
                "source": resolution_source,
                "actual": [actual_width, actual_height],
                "requested_aspect": round(width / height, 8),
                "actual_aspect": round(actual_width / actual_height, 8),
            }
        )
        quality = analyze_png_quality(
            output_path,
            expected_resolution=(width, height),
            require_exact_resolution=True,
        )
        if camera_path:
            observed_camera_path = source_state.get("camera", {}).get("path")
            if observed_camera_path == "unverified":
                if quality["status"] == "passed":
                    quality["status"] = "warning"
                quality["reasons"].append(
                    {
                        "code": "camera_state_unverified",
                        "message": "The effective viewport camera could not be queried",
                    }
                )
            elif observed_camera_path != camera_path:
                quality["status"] = "failed"
                quality["reasons"].append(
                    {
                        "code": "camera_mismatch",
                        "message": (
                            f"Captured camera {observed_camera_path!r} does not match "
                            f"requested camera {camera_path!r}"
                        ),
                    }
                )
        absolute_path = str(output_path.resolve(strict=True))
        display_path = (
            output_path.relative_to(self._project_root).as_posix()
            if storage_scope == "runtime_fallback"
            else absolute_path
        )
        quality_status = str(quality["status"])
        visual_match = (
            quality_status
            if quality_status in {"failed", "warning"}
            else "unverified"
        )
        warnings: list[str] = []
        errors: list[str] = []
        if capture_api != "scene_viewer.flipbook":
            warnings.append(
                "Documented flipbook capture was unavailable; the legacy viewport image API does not prove display-transform parity"
            )
        if quality_status in {"warning", "unverified"}:
            warnings.extend(
                str(reason["message"]) for reason in quality["reasons"]
            )
        elif quality_status == "failed":
            errors.extend(str(reason["message"]) for reason in quality["reasons"])
        capture_cook_evidence: dict[str, Any]
        if validation_paths:
            capture_cook_evidence = self._summarize_cook_evidence(
                per_frame_cook_records,
                requested=True,
            )
        else:
            capture_cook_evidence = {
                "schema": "hia-cook-cache-evidence/1",
                "cook_requested": False,
                "assessment": "viewport_evaluation_only",
                "target_count": 0,
                "targets": [],
                "limitations": (
                    "The flipbook evaluates the viewer at the locked frame, "
                    "but no node-specific recomputation is proven without "
                    "validation_paths"
                ),
            }
        result: dict[str, Any] = {
            "ok": quality_status != "failed",
            "result": {
                "path": display_path,
                "absolute_path": absolute_path,
                "storage_scope": storage_scope,
                "mode": mode,
                "width": actual_width,
                "height": actual_height,
                "aspect_ratio": round(actual_width / actual_height, 8),
                "requested_width": width,
                "requested_height": height,
                "requested_aspect_ratio": round(width / height, 8),
                "resolution_source": resolution_source,
                "requested_frame": requested_frame,
                "actual_frame": frame_lock_evidence["actual_frame"],
                "cook_frame": frame_lock_evidence["cook_frame"],
                "quality_frame": requested_frame,
                "frame_lock": frame_lock_evidence,
                "cook_cache_evidence": capture_cook_evidence,
                "capture_api": capture_api,
                "capture_ok": True,
                "quality_status": quality_status,
                "quality_reasons": quality["reasons"],
                "quality_metrics": quality["metrics"],
                "visual_match": visual_match,
                "display_match": "unverified",
                "hdr_display_mismatch_risk": "unverified",
                "display_match_reason": (
                    "HOM exposes Houdini viewer color settings but not the OS HDR "
                    "and compositor path used for the user's physical display"
                ),
                "source_state": source_state,
            },
            "warnings": warnings,
            "errors": errors,
            "revision": self.scene_revision,
            "dirty": self._dirty(),
        }
        if bool(arguments.get("return_image", True)):
            if output_path.stat().st_size <= 3_000_000:
                raw = output_path.read_bytes()
                result["image"] = {"mime_type": "image/png", "data_base64": base64.b64encode(raw).decode("ascii")}
            else:
                result["warnings"].append("Image exceeded inline MCP size; returning its local path only")
        return result

    def _dispatch_local_help(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        card_id = str(arguments.get("card_id") or "").strip()
        canonical_id = str(arguments.get("canonical_id") or "").strip()
        if "card_id" in arguments and not card_id:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "card_id must not be blank",
            )
        if "canonical_id" in arguments and not canonical_id:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "canonical_id must not be blank",
            )
        if "query" in arguments or "queries" in arguments:
            queries, is_batch = _query_values(
                arguments,
                minimum_length=2,
                maximum_length=256,
            )
        elif card_id or canonical_id:
            queries = [card_id or canonical_id]
            is_batch = False
        else:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Provide query, queries, card_id, or canonical_id",
            )
        raw_source_kinds = arguments.get("source_kinds")
        if raw_source_kinds is None:
            source_kinds: set[str] = set()
        elif not isinstance(raw_source_kinds, (list, tuple, set)):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "source_kinds must be an array",
            )
        else:
            source_kinds = {str(value) for value in raw_source_kinds}
        invalid_source_kinds = source_kinds.difference(
            FILTERABLE_SOURCE_KINDS
        )
        if invalid_source_kinds:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "source_kinds contains an unsupported local knowledge source kind",
                {"source_kinds": sorted(invalid_source_kinds)},
            )
        targeted_filter = bool(source_kinds or card_id or canonical_id)
        inferred_sources = {
            SOURCE_KIND_GROUPS[kind] for kind in source_kinds
        }
        if card_id or canonical_id:
            inferred_sources.add("project")
        raw_sources = arguments.get("sources")
        if raw_sources is None:
            sources = (
                inferred_sources
                if targeted_filter
                else set(SEARCH_SOURCE_GROUPS)
            )
        elif not isinstance(raw_sources, (list, tuple, set)):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "sources must be an array",
            )
        elif not raw_sources:
            sources = (
                inferred_sources
                if targeted_filter
                else set(SEARCH_SOURCE_GROUPS)
            )
        else:
            sources = {str(value) for value in raw_sources}
        if targeted_filter:
            sources.update(inferred_sources)
        invalid_sources = sources.difference(SEARCH_SOURCE_GROUPS)
        if invalid_sources:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "sources contains an unsupported local knowledge source",
                {"sources": sorted(invalid_sources)},
            )
        refresh_requested = arguments.get("refresh", False)
        if not isinstance(refresh_requested, bool):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "refresh must be a boolean",
            )
        response_format = str(
            arguments.get("response_format") or "compact"
        ).casefold()
        if response_format not in {"compact", "full", "diagnostic"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "response_format must be compact, full, or diagnostic",
            )
        max_bytes = _bounded_int(
            arguments.get("max_bytes", DEFAULT_LOCAL_HELP_BYTES),
            4096,
            MAX_LOCAL_HELP_BYTES,
        )
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _bounded_int(arguments.get("limit", 10), 1, 50)
        mode = str(arguments.get("mode") or "hybrid").casefold()
        if mode not in {"lexical", "vector", "hybrid"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "mode must be lexical, vector, or hybrid",
            )
        hybrid_knowledge: HybridKnowledgeStore | None = None
        knowledge_index: LocalKnowledgeIndex | None = None
        index_unavailable_reason = ""
        try:
            hybrid_knowledge = self._hybrid_knowledge_store(
                initialize=refresh_requested
            )
            knowledge_index = hybrid_knowledge.index
            refreshable_sources = sources.intersection(SOURCE_GROUPS)
        except Exception as exc:
            if refresh_requested:
                raise HiaRuntimeError(
                    "LOCAL_HELP_INDEX_UNAVAILABLE",
                    _bounded_text(_redact_text(str(exc)), 2048),
                ) from exc
            refreshable_sources = sources.intersection(SOURCE_GROUPS)
            index_unavailable_reason = _bounded_text(
                _redact_text(str(exc)),
                2048,
            )
        ui_requested = time.monotonic()
        ui_started = ui_requested
        ui_finished = ui_requested

        def snapshot_on_ui_thread() -> dict[str, Any]:
            nonlocal ui_started, ui_finished
            ui_started = time.monotonic()
            try:
                catalog = []
                houdini_help_root = ""
                houdini_version = _application_version(self._hou) or "unknown"
                if refresh_requested and "houdini" in sources:
                    houdini_help_root = str(self._hou.expandString("$HH/help"))
                    catalog = self._node_type_matches("", set(), True)
                return {
                    "catalog": catalog,
                    "houdini_help_root": houdini_help_root,
                    "houdini_version": houdini_version,
                    "revision": self.scene_revision,
                    "dirty": self._dirty(),
                }
            finally:
                ui_finished = time.monotonic()

        try:
            snapshot = self._run_on_main_thread(snapshot_on_ui_thread)
        except HiaRuntimeError:
            raise
        except Exception as exc:
            raise HiaRuntimeError(
                "UI_MAIN_THREAD_DISPATCH_FAILED",
                "Local help metadata could not be read from Houdini's UI main thread",
                {"reason": _bounded_text(_redact_text(str(exc)), 1024)},
            ) from exc
        current_houdini_version = str(
            snapshot.get("houdini_version") or "unknown"
        )
        refresh_started = time.monotonic()
        try:
            if refresh_requested:
                assert knowledge_index is not None
                refresh_stats, index_warnings = knowledge_index.refresh(
                    refreshable_sources,
                    snapshot,
                    force=True,
                )
            else:
                refresh_stats = _local_help_refresh_stats(
                    refreshable_sources,
                    reason="read_only",
                )
                index_warnings = []
            refresh_finished = time.monotonic()
            if hybrid_knowledge is None or knowledge_index is None:
                search_results = [
                    {
                        "matches": [],
                        "total": 0,
                        "tokenizer": "",
                        "retrieval": {
                            "requested_mode": mode,
                            "mode_used": "unavailable",
                            "lexical": {
                                "available": False,
                                "engine": "SQLite FTS5",
                            },
                            "encoder": {"available": False},
                            "corpus": {
                                "available": False,
                                "complete": False,
                                "partial": False,
                                "ranking_scope": "none",
                                "global_recall": False,
                                "fallback_reason": (
                                    "INDEX_NOT_INITIALIZED"
                                ),
                            },
                            "fallback_reason": "INDEX_NOT_INITIALIZED",
                            "timings": {
                                "fts_seconds": 0.0,
                                "query_encode_seconds": 0.0,
                                "vector_scan_seconds": 0.0,
                            },
                        },
                    }
                    for _query in queries
                ]
                index_warnings.append(
                    "Local knowledge index is not initialized; run an explicit "
                    "refresh or the independent knowledge CLI before read-only "
                    f"search ({index_unavailable_reason})"
                )
                database = ".runtime/knowledge/knowledge.sqlite3"
            else:
                search_results = hybrid_knowledge.search_many(
                    queries,
                    sources,
                    current_houdini_version=current_houdini_version,
                    offset=offset,
                    limit=limit,
                    mode=mode,
                    allow_index_updates=refresh_requested,
                    source_kinds=source_kinds,
                    card_id=card_id,
                    canonical_id=canonical_id,
                )
                if response_format in {"full", "diagnostic"}:
                    for search_result in search_results:
                        for match in search_result.get("matches", []):
                            if (
                                not isinstance(match, dict)
                                or str(
                                    match.get("source_kind")
                                    or match.get("source")
                                    or ""
                                )
                                not in FILTERABLE_SOURCE_KINDS
                            ):
                                continue
                            metadata = match.get("metadata")
                            if not isinstance(metadata, Mapping):
                                continue
                            document_id = metadata.get("document_id")
                            if not isinstance(document_id, int):
                                continue
                            content, content_truncated = (
                                knowledge_index.document_content(
                                    document_id,
                                    max_chars=MAX_FULL_KNOWLEDGE_CARD_CHARS,
                                )
                            )
                            match["content"] = content
                            match["content_truncated"] = content_truncated
                database = knowledge_index.relative_database_path
        except HiaRuntimeError:
            raise
        except Exception as exc:
            raise HiaRuntimeError(
                "LOCAL_HELP_SEARCH_FAILED",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        serialization_started = time.monotonic()
        result_payload = self._local_help_payload(
            queries=queries,
            search_results=search_results,
            is_batch=is_batch,
            offset=offset,
            limit=limit,
            response_format=response_format,
            max_bytes=max_bytes,
            refresh_stats=refresh_stats,
            database=database,
        )
        serialization_finished = time.monotonic()
        result_payload["timings"]["serialization_seconds"] = _seconds(
            serialization_finished - serialization_started
        )
        result_payload["response_bytes"] = 0
        for _unused in range(3):
            measured_bytes = _json_size(result_payload)
            if measured_bytes == result_payload["response_bytes"]:
                break
            result_payload["response_bytes"] = measured_bytes
        result = {
            "ok": True,
            "result": result_payload,
            "stdout": "",
            "warnings": [
                _bounded_text(_redact_text(value), 4096)
                for value in index_warnings
            ],
            "errors": [],
            "revision": int(snapshot.get("revision", self.scene_revision)),
            "dirty": bool(snapshot.get("dirty", False)),
        }
        search_finished = time.monotonic()
        result["phase_timings"] = {
            "runtime_ui_queue_seconds": _seconds(ui_started - ui_requested),
            "runtime_ui_snapshot_seconds": _seconds(ui_finished - ui_started),
            "local_index_refresh_seconds": _seconds(
                refresh_finished - refresh_started
            ),
            "local_index_query_seconds": _seconds(
                search_finished - refresh_finished
            ),
            "local_file_search_seconds": _seconds(
                search_finished - refresh_started
            ),
        }
        return result

    def _local_help_payload(
        self,
        *,
        queries: list[str],
        search_results: list[dict[str, Any]],
        is_batch: bool,
        offset: int,
        limit: int,
        response_format: str,
        max_bytes: int,
        refresh_stats: Mapping[str, Any],
        database: str,
    ) -> dict[str, Any]:
        public_retrieval, corpus, timings = _local_help_public_status(
            [result.get("retrieval", {}) for result in search_results]
        )
        index = {
            **dict(refresh_stats),
            "database": database,
            "fts": "FTS5",
            "tokenizer": str(
                search_results[0].get("tokenizer") or ""
            ),
            "corpus": corpus,
        }
        common = {
            "response_format": response_format,
            "files_scanned": int(refresh_stats.get("files_scanned", 0)),
            "inline_records_scanned": int(
                refresh_stats.get("inline_records_scanned", 0)
            ),
            "web_searched": False,
            "retrieval": public_retrieval,
            "index": index,
            "timings": timings,
            "truncated": False,
            "next_offset": None,
        }
        prepared = [
            [
                _local_help_match(match, response_format)
                for match in result.get("matches", [])
                if isinstance(match, Mapping)
            ]
            for result in search_results
        ]
        if not is_batch:
            result: dict[str, Any] = {
                "query": queries[0],
                "matches": [],
                "total": int(search_results[0].get("total", 0)),
                "offset": offset,
                "limit": limit,
                **common,
            }
            for match in prepared[0]:
                if not _append_local_help_match_within_budget(
                    result["matches"],
                    match,
                    result,
                    max_bytes - 256,
                ):
                    break
            returned = len(result["matches"])
            consumed = returned or (1 if prepared[0] else 0)
            has_more = (
                returned < len(prepared[0])
                or offset + len(prepared[0]) < result["total"]
            )
            result["truncated"] = has_more
            result["next_offset"] = offset + consumed if has_more else None
            return result

        query_entries = [
            {
                "query_index": query_index,
                "query": query,
                "matches": [],
                "total": int(search_result.get("total", 0)),
                "truncated": False,
                "next_offset": None,
                "retrieval": _local_help_query_status(
                    search_result.get("retrieval", {})
                ),
            }
            for query_index, (query, search_result) in enumerate(
                zip(queries, search_results)
            )
        ]
        result = {
            "queries": query_entries,
            "query_count": len(query_entries),
            "offset": offset,
            "limit": limit,
            "query_echo_truncated": False,
            **common,
        }
        if _json_size(result) > max_bytes - 256:
            for entry in query_entries:
                entry["query"] = _bounded_text(str(entry["query"]), 48)
            result["query_echo_truncated"] = True
        if _json_size(result) > max_bytes - 256:
            for entry in query_entries:
                entry["query"] = ""
        if _json_size(result) > max_bytes - 256:
            raise HiaRuntimeError(
                "RESPONSE_TOO_LARGE",
                "Local-help batch metadata exceeds max_bytes",
                {"limit": max_bytes},
            )
        maximum_matches = max((len(values) for values in prepared), default=0)
        budget_exhausted = False
        for position in range(maximum_matches):
            for query_index, values in enumerate(prepared):
                if position >= len(values):
                    continue
                if not _append_local_help_match_within_budget(
                    query_entries[query_index]["matches"],
                    values[position],
                    result,
                    max_bytes - 256,
                ):
                    budget_exhausted = True
                    break
            if budget_exhausted:
                break
        next_offsets: list[int] = []
        for entry, values in zip(query_entries, prepared):
            returned = len(entry["matches"])
            consumed = returned or (1 if values else 0)
            has_more = (
                returned < len(values)
                or offset + len(values) < int(entry["total"])
            )
            entry["truncated"] = has_more
            entry["next_offset"] = offset + consumed if has_more else None
            if has_more:
                next_offsets.append(offset + consumed)
        result["truncated"] = bool(next_offsets)
        result["next_offset"] = (
            next_offsets[0]
            if next_offsets and len(set(next_offsets)) == 1
            else None
        )
        return result

    def _dispatch_project_memory(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            payload = self._hybrid_knowledge_store(
                initialize=True
            ).project_memory(arguments)
        except HybridKnowledgeError as exc:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        except Exception as exc:
            raise HiaRuntimeError(
                "PROJECT_MEMORY_FAILED",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        scene = self._run_on_main_thread(
            lambda: {
                "revision": self.scene_revision,
                "dirty": self._dirty(),
            }
        )
        fallback_reason = ""
        retrieval = payload.get("retrieval")
        if isinstance(retrieval, Mapping):
            vector = retrieval.get("vector")
            if isinstance(vector, Mapping):
                fallback_reason = str(vector.get("fallback_reason") or "")
        return {
            "ok": True,
            "result": payload,
            "stdout": "",
            "warnings": (
                [
                    "Project memory was stored in SQLite/FTS5; optional "
                    f"vector encoding degraded: {fallback_reason}"
                ]
                if fallback_reason
                else []
            ),
            "errors": [],
            "revision": int(scene["revision"]),
            "dirty": bool(scene["dirty"]),
        }

    def _hybrid_knowledge_store(
        self,
        *,
        initialize: bool = False,
    ) -> HybridKnowledgeStore:
        with self._state_lock:
            if self._hybrid_knowledge is None:
                if self._knowledge_index is None:
                    self._knowledge_index = LocalKnowledgeIndex(
                        self._project_root,
                        initialize=initialize,
                    )
                self._hybrid_knowledge = HybridKnowledgeStore(
                    self._project_root,
                    index=self._knowledge_index,
                )
            return self._hybrid_knowledge

    def close(self) -> None:
        with self._state_lock:
            hybrid = self._hybrid_knowledge
            self._hybrid_knowledge = None
        if hybrid is not None:
            hybrid.close()

    def _success(self, result: Any, *, warnings: list[str] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "result": _json_value(result),
            "stdout": "",
            "warnings": [_bounded_text(_redact_text(value), 4096) for value in (warnings or [])],
            "errors": [],
            "revision": self.scene_revision,
            "dirty": self._dirty(),
        }

    @staticmethod
    def _validation_check_names(
        raw_checks: Any,
        *,
        default: tuple[str, ...],
    ) -> list[str]:
        if raw_checks is None:
            return list(default)
        if not isinstance(raw_checks, list) or len(raw_checks) > len(
            VALIDATION_CHECK_NAMES
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "checks must be an array containing supported domain checks",
            )
        checks: list[str] = []
        for value in raw_checks:
            name = str(value)
            if name not in VALIDATION_CHECK_NAMES:
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "checks contains an unsupported domain check",
                    {"check": name},
                )
            if name not in checks:
                checks.append(name)
        return checks

    def _absolute_node_paths(
        self,
        values: Any,
        *,
        field_name: str,
        maximum: int,
    ) -> list[str]:
        if not isinstance(values, (list, tuple)) or len(values) > maximum:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                f"{field_name} must contain at most {maximum} Houdini node paths",
            )
        paths: list[str] = []
        for index, value in enumerate(values):
            if not isinstance(value, str):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field_name} must contain strings",
                    {"index": index},
                )
            path = value.strip()
            if not _valid_houdini_node_path(path):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    f"{field_name} must contain absolute Houdini node paths",
                    {"index": index, "path": _bounded_text(value, 256)},
                )
            if path not in paths:
                paths.append(path)
        return paths

    def _optional_node_path(self, value: Any, *, field_name: str) -> str:
        if value is None or value == "":
            return ""
        return self._absolute_node_paths(
            [value],
            field_name=field_name,
            maximum=1,
        )[0]

    def _remember_evidence(self, evidence: Mapping[str, Any]) -> None:
        with self._state_lock:
            self._recent_evidence.append(_json_value(evidence))

    def _recent_evidence_snapshot(self, scope_paths: list[str]) -> list[dict[str, Any]]:
        with self._state_lock:
            entries = list(self._recent_evidence)
        related = []
        for entry in reversed(entries):
            evidence_paths = [
                str(path) for path in entry.get("paths", []) if isinstance(path, str)
            ]
            if scope_paths:
                if not evidence_paths or not any(
                    _houdini_path_is_within(evidence_path, scope_path)
                    or _houdini_path_is_within(scope_path, evidence_path)
                    for evidence_path in evidence_paths
                    for scope_path in scope_paths
                ):
                    continue
            matched_paths = [
                path
                for path in evidence_paths
                if any(
                    _houdini_path_is_within(path, scope_path)
                    or _houdini_path_is_within(scope_path, path)
                    for scope_path in scope_paths
                )
            ]
            representative_paths = list(
                dict.fromkeys([*matched_paths, *evidence_paths])
            )[:4]
            projected = {
                key: value
                for key, value in entry.items()
                if key != "paths"
            }
            projected["paths"] = [
                _bounded_text(_redact_text(path), 512)
                for path in representative_paths
            ]
            path_count = entry.get("path_count")
            projected["path_count"] = (
                path_count
                if isinstance(path_count, int) and path_count >= len(evidence_paths)
                else len(evidence_paths)
            )
            related.append(projected)
            if len(related) >= 8:
                break
        return related

    def _dirty_observation(self) -> bool | None:
        probe = getattr(self._hou.hipFile, "hasUnsavedChanges", None)
        if not callable(probe):
            return None
        try:
            value = probe()
        except Exception:
            return None
        return value if isinstance(value, bool) else None

    def _dirty(self) -> bool:
        observed = self._dirty_observation()
        return observed if observed is not None else False

    def _current_ui_nodes(self) -> tuple[Any | None, Any | None]:
        current_network = None
        current_node = None
        try:
            desktop = self._hou.ui.curDesktop()
            network_editor = desktop.paneTabOfType(self._hou.paneTabType.NetworkEditor)
            if network_editor is not None:
                current_network = network_editor.pwd()
                current_node = network_editor.currentNode()
        except Exception:
            pass
        selected = list(_safe_call(self._hou, "selectedNodes", ()))
        if current_node is None and selected:
            current_node = selected[-1]
        if current_network is None and current_node is not None:
            current_network = _safe_call(current_node, "parent", None)
        return current_network, current_node

    def _current_network_path(self) -> str:
        return _safe_path(self._current_ui_nodes()[0])

    def _current_node_path(self) -> str:
        return _safe_path(self._current_ui_nodes()[1])

    def _resolve_nodes(self, arguments: Mapping[str, Any]) -> list[Any]:
        paths = [str(value) for value in arguments.get("paths", [])]
        if not paths and bool(arguments.get("use_selection", True)):
            nodes = list(_safe_call(self._hou, "selectedNodes", ()))
        else:
            nodes = []
            missing = []
            for path in paths:
                node = self._hou.node(path)
                if node is None:
                    missing.append(path)
                else:
                    nodes.append(node)
            if missing:
                raise HiaRuntimeError("NODE_NOT_FOUND", "One or more requested nodes do not exist", {"paths": missing[:64]})
        if not nodes:
            current = self._current_ui_nodes()[1]
            if current is not None:
                nodes = [current]
        if not nodes:
            raise HiaRuntimeError("NO_TARGET_NODES", "No paths, selection, or current node are available")
        return nodes[:64]

    def _resolve_nodes_with_missing(
        self,
        arguments: Mapping[str, Any],
    ) -> tuple[list[Any], list[str]]:
        paths = [str(value) for value in arguments.get("paths", [])]
        if not paths:
            return self._resolve_nodes(arguments), []
        nodes = []
        missing = []
        for path in paths:
            node = self._hou.node(path)
            if node is None:
                missing.append(path)
            else:
                nodes.append(node)
        if not nodes:
            raise HiaRuntimeError(
                "NODE_NOT_FOUND",
                "None of the requested nodes exist",
                {"paths": missing[:64]},
            )
        return nodes[:64], missing[:64]

    def _node_record(
        self,
        node: Any,
        *,
        views: set[str],
        query: str,
        depth: int,
        limit: int,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {"path": _safe_path(node), "name": _safe_name(node), "type": self._type_record(node)}
        if "connections" in views:
            record["inputs"] = [_safe_path(value) if value is not None else None for value in _safe_call(node, "inputs", ())]
            record["outputs"] = [_safe_path(value) for value in _safe_call(node, "outputs", ())]
        if "flags" in views:
            record["flags"] = {
                "selected": bool(_safe_call(node, "isSelected", False)),
                "current": bool(_safe_call(node, "isCurrent", False)),
                "display": bool(_safe_call(node, "isDisplayFlagSet", False)),
                "render": bool(_safe_call(node, "isRenderFlagSet", False)),
                "bypassed": bool(_safe_call(node, "isBypassed", False)),
                "locked_hda": bool(_safe_call(node, "isLockedHDA", False)),
            }
        if "errors" in views:
            record["errors"] = list(_safe_call(node, "errors", ()))
            record["warnings"] = list(_safe_call(node, "warnings", ()))
        if "parameters" in views:
            parms = []
            for parm in _safe_call(node, "parms", ()):
                item = self._parm_record(parm)
                if query and query not in json.dumps(item, ensure_ascii=False).casefold():
                    continue
                parms.append(item)
                if len(parms) >= limit:
                    break
            record["parameters"] = parms
        if "geometry" in views:
            record["geometry"] = self._geometry_record(node, include_attributes=True, sample_limit=0)
        if "children" in views or depth > 0:
            record["graph"] = self._graph_records(_safe_path(node), depth=depth, query=query, limit=limit)
        if "evidence" in views:
            record["network_evidence"] = self._local_network_evidence(
                _safe_path(node)
            )
        return record

    def _local_network_evidence(self, path: str) -> dict[str, Any]:
        try:
            node = self._hou.node(path)
        except Exception as exc:
            return {
                "path": path,
                "exists": None,
                "observation": "unavailable",
                "error": _bounded_text(_redact_text(str(exc)), 1024),
            }
        if node is None:
            return {"path": path, "exists": False}

        inputs = list(_safe_call(node, "inputs", ()))
        outputs = list(_safe_call(node, "outputs", ()))
        upstream = []
        queue = [
            (value, 1)
            for value in inputs
            if value is not None
        ]
        visited = {path}
        while queue and len(upstream) < 8:
            upstream_node, depth = queue.pop(0)
            upstream_path = _safe_path(upstream_node)
            if not upstream_path or upstream_path in visited:
                continue
            visited.add(upstream_path)
            upstream.append(
                {
                    "path": upstream_path,
                    "depth": depth,
                    "type": self._type_record(upstream_node),
                }
            )
            if depth < 2:
                queue.extend(
                    (value, depth + 1)
                    for value in _safe_call(upstream_node, "inputs", ())
                    if value is not None
                )

        controls = []
        material_entries = []
        control_candidate_count = 0
        control_terms = (
            "material", "shop", "shader", "mtlx", "file", "path", "scale",
            "size", "seed", "enable", "mode", "density", "color", "rough",
            "metal", "output",
        )
        material_terms = ("material", "shop", "shader", "mtlx")
        for parm in _safe_call(node, "parms", ()):
            template = _safe_call(parm, "parmTemplate", None)
            if bool(_safe_call(template, "isHidden", False)):
                continue
            name = str(_safe_call(parm, "name", ""))
            label = str(_safe_call(template, "label", ""))
            haystack = f"{name} {label}".casefold()
            referenced = _safe_call(parm, "getReferencedParm", parm)
            reference_path = (
                _safe_path(referenced)
                if referenced is not None and referenced is not parm
                else ""
            )
            at_default = _safe_call(parm, "isAtDefault", None)
            time_dependent = bool(_safe_call(parm, "isTimeDependent", False))
            if not (
                at_default is False
                or reference_path
                or time_dependent
                or any(term in haystack for term in control_terms)
            ):
                continue
            control_candidate_count += 1
            control = {
                "name": name,
                "label": label,
                "value": _json_value(_safe_parm_value(parm)),
                "raw_value": _bounded_text(
                    _redact_text(_safe_unexpanded_string(parm)), 512
                ),
                "at_default": at_default if isinstance(at_default, bool) else None,
                "reference": reference_path or None,
                "time_dependent": time_dependent,
            }
            if len(controls) < 8:
                controls.append(control)
            if (
                len(material_entries) < 8
                and any(term in haystack for term in material_terms)
            ):
                material_entries.append(control)

        child_nodes = list(_safe_call(node, "children", ()))
        assessed_nodes = [node, *child_nodes[:31]]
        signals = []
        type_counts: dict[str, int] = {}
        box_count = 0
        for candidate in assessed_nodes:
            candidate_path = _safe_path(candidate)
            type_info = self._type_record(candidate)
            type_name = str(type_info.get("name") or "")
            base_type = type_name.split("::", 1)[0].casefold()
            category = str(type_info.get("category") or "").casefold()
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
            candidate_inputs = list(_safe_call(candidate, "inputs", ()))
            minimum = _safe_call(_safe_call(candidate, "type", None), "minNumInputs", 0)
            connected = sum(value is not None for value in candidate_inputs)
            if isinstance(minimum, int) and minimum > connected:
                signals.append(
                    {
                        "code": "MISSING_REQUIRED_INPUT",
                        "severity": "error",
                        "path": candidate_path,
                        "observed": {"minimum": minimum, "connected": connected},
                    }
                )
            if category == "sop" and "python" in base_type:
                signals.append(
                    {
                        "code": "PYTHON_GEOMETRY_AUTHORING",
                        "severity": "warning",
                        "path": candidate_path,
                        "observed": {"type": type_name},
                    }
                )
            if category == "sop" and base_type == "box":
                box_count += 1
        if box_count >= 8:
            signals.append(
                {
                    "code": "BOX_PRIMITIVE_HEAVY",
                    "severity": "notice",
                    "path": path,
                    "observed": {"box_nodes": box_count},
                }
            )
        for type_name, count in sorted(type_counts.items()):
            if type_name and count >= 8:
                signals.append(
                    {
                        "code": "REPEATED_NODE_TYPE_CLUSTER",
                        "severity": "notice",
                        "path": path,
                        "observed": {"type": type_name, "count": count},
                    }
                )

        cook_state = self._cook_state(node)
        node_type = self._type_record(node)
        node_type_name = str(node_type.get("name") or "").casefold()
        node_category = str(node_type.get("category") or "").casefold()
        material_role = (
            "material_network"
            if (
                node_category in {"vop", "shop"}
                or any(
                    term in node_type_name
                    for term in ("material", "mtlx", "shader")
                )
            )
            else ("binding_consumer" if material_entries else None)
        )
        return {
            "path": path,
            "exists": True,
            "type": node_type,
            "inputs": [
                {"index": index, "path": _safe_path(value) if value else None}
                for index, value in enumerate(inputs[:16])
            ],
            "outputs": [_safe_path(value) for value in outputs[:16]],
            "upstream_chain": upstream,
            "controls": controls,
            "material_entries": material_entries,
            "material_role": material_role,
            "flags": {
                "display": bool(_safe_call(node, "isDisplayFlagSet", False)),
                "render": bool(_safe_call(node, "isRenderFlagSet", False)),
                "bypassed": bool(_safe_call(node, "isBypassed", False)),
            },
            "cook_state": cook_state["values"],
            "errors": [
                _bounded_text(_redact_text(str(value)), 1024)
                for value in list(_safe_call(node, "errors", ()))[:8]
            ],
            "warnings": [
                _bounded_text(_redact_text(str(value)), 1024)
                for value in list(_safe_call(node, "warnings", ()))[:8]
            ],
            "quality_evidence": {
                "signals": signals[:16],
                "assessed_nodes": len(assessed_nodes),
                "subjective_quality_proven": False,
                "limitations": [
                    "Repeated node types are factual counts, not proof of poor design",
                    "Hard-coded geometry is only identified when a Python SOP or script node is observable",
                    "Geometry intersections are not inferred from bounding boxes",
                ],
            },
            "truncated": {
                "inputs": len(inputs) > 16,
                "outputs": len(outputs) > 16,
                "upstream": bool(queue),
                "controls": control_candidate_count > 8,
                "assessed_children": len(child_nodes) > 31,
            },
        }

    def _graph_records(self, root_path: str, *, depth: int, query: str, limit: int) -> dict[str, Any]:
        root = self._hou.node(root_path)
        if root is None:
            raise HiaRuntimeError("NODE_NOT_FOUND", "Graph root does not exist", {"path": root_path})
        queue: list[tuple[Any, int]] = [(root, 0)]
        visited: set[str] = set()
        records = []
        edges = []
        total = 0
        while queue:
            node, level = queue.pop(0)
            path = _safe_path(node)
            if path in visited:
                continue
            visited.add(path)
            total += 1
            type_record = self._type_record(node)
            haystack = f"{path} {type_record['name']} {type_record['description']}".casefold()
            if not query or query in haystack:
                if len(records) < limit:
                    records.append(
                        {
                            "path": path,
                            "parent": _safe_path(_safe_call(node, "parent", None)),
                            "type": type_record,
                            "depth": level,
                            "errors": list(_safe_call(node, "errors", ())),
                            "warnings": list(_safe_call(node, "warnings", ())),
                        }
                    )
            for input_index, input_node in enumerate(_safe_call(node, "inputs", ())):
                if input_node is not None:
                    edges.append({"from": _safe_path(input_node), "to": path, "input_index": input_index})
            if level < depth:
                for child in _safe_call(node, "children", ()):
                    queue.append((child, level + 1))
            if total >= MAX_SNAPSHOT_NODES:
                break
        return {"nodes": records, "edges": edges, "total": total, "truncated": bool(queue)}

    def _node_type_matches(
        self,
        query: str,
        contexts: set[str],
        include_deprecated: bool,
    ) -> list[dict[str, Any]]:
        return self._filter_node_type_catalog(
            self._node_type_catalog(contexts, include_deprecated),
            query,
        )

    def _node_type_catalog(
        self,
        contexts: set[str],
        include_deprecated: bool,
    ) -> list[dict[str, Any]]:
        categories = self._hou.nodeTypeCategories()
        matches = []
        for context_name, category in categories.items():
            context_text = str(context_name)
            category_name = _safe_name(category) or context_text
            if contexts and context_text.casefold() not in contexts and category_name.casefold() not in contexts:
                continue
            for type_name, node_type in category.nodeTypes().items():
                deprecated = bool(_safe_call(node_type, "deprecated", False))
                if deprecated and not include_deprecated:
                    continue
                record = {
                    "category": category_name,
                    "name": str(type_name),
                    "resolved_name": str(_safe_call(node_type, "name", type_name)),
                    "description": str(_safe_call(node_type, "description", "")),
                    "name_components": _json_value(_safe_call(node_type, "nameComponents", ())),
                    "min_inputs": _json_value(_safe_call(node_type, "minNumInputs", None)),
                    "max_inputs": _json_value(_safe_call(node_type, "maxNumInputs", None)),
                    "max_outputs": _json_value(_safe_call(node_type, "maxNumOutputs", None)),
                    "child_context": _safe_name(_safe_call(node_type, "childTypeCategory", None)),
                    "deprecated": deprecated,
                }
                matches.append(record)
        matches.sort(key=lambda item: (item["category"].casefold(), item["name"].casefold()))
        return matches

    @staticmethod
    def _filter_node_type_catalog(
        catalog: Iterable[Mapping[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        folded = str(query).casefold()
        if not folded:
            return [dict(record) for record in catalog]
        return [
            dict(record)
            for record in catalog
            if folded in json.dumps(record, ensure_ascii=False).casefold()
        ]

    def _parameter_templates(
        self,
        node_type: Any,
        query: str,
        *,
        node: Any | None = None,
    ) -> list[dict[str, Any]]:
        if node is not None:
            records = []
            for parm in list(_safe_call(node, "parms", ()))[:10_000]:
                template = _safe_call(parm, "parmTemplate", None)
                runtime_name = str(_safe_call(parm, "name", ""))
                template_name = str(_safe_call(template, "name", ""))
                record = {
                    "name": runtime_name,
                    "runtime_name": runtime_name,
                    "template_name": template_name,
                    "name_kind": "runtime_instance",
                    "multiparm_instance": bool(
                        _safe_call(parm, "isMultiParmInstance", False)
                    ),
                    "multiparm_indices": _json_value(
                        _safe_call(parm, "multiParmInstanceIndices", ())
                    ),
                    "label": str(_safe_call(template, "label", "")),
                    "type": _safe_name(_safe_call(template, "type", None)),
                    "components": _json_value(
                        _safe_call(template, "numComponents", None)
                    ),
                    "default": _json_value(
                        _safe_call(template, "defaultValue", None)
                    ),
                    "tags": _json_value(_safe_call(template, "tags", {})),
                    "hidden": bool(_safe_call(template, "isHidden", False)),
                }
                if query and query not in json.dumps(
                    record, ensure_ascii=False
                ).casefold():
                    continue
                records.append(record)
            return records
        group = node_type.parmTemplateGroup()
        entries = list(_safe_call(group, "entries", ()))
        flattened = []
        while entries and len(flattened) < 10_000:
            template = entries.pop(0)
            children = list(_safe_call(template, "parmTemplates", ()))
            if children:
                entries[0:0] = children
                continue
            record = {
                "name": str(_safe_call(template, "name", "")),
                "runtime_name": None,
                "template_name": str(_safe_call(template, "name", "")),
                "name_kind": "template_pattern",
                "multiparm_instance": None,
                "multiparm_indices": [],
                "label": str(_safe_call(template, "label", "")),
                "type": _safe_name(_safe_call(template, "type", None)),
                "components": _json_value(_safe_call(template, "numComponents", None)),
                "default": _json_value(_safe_call(template, "defaultValue", None)),
                "tags": _json_value(_safe_call(template, "tags", {})),
                "hidden": bool(_safe_call(template, "isHidden", False)),
            }
            if query and query not in json.dumps(record, ensure_ascii=False).casefold():
                continue
            flattened.append(record)
        return flattened

    def _geometry_record(
        self,
        node: Any,
        *,
        include_attributes: bool,
        sample_limit: int,
        allow_cook: bool = True,
    ) -> dict[str, Any]:
        type_info = self._type_record(node)
        base = {
            "node_path": _safe_path(node),
            "available": False,
            "category": type_info["category"],
            "errors": list(_safe_call(node, "errors", ())),
        }
        if str(type_info["category"]).casefold() != "sop":
            base["reason"] = "unsupported_node_category"
            return base
        if not allow_cook and bool(_safe_call(node, "needsToCook", False)):
            base["reason"] = "cook_not_requested"
            return base
        try:
            geometry = node.geometry()
        except Exception as exc:
            base["error"] = _bounded_text(_redact_text(str(exc)), 2048)
            return base
        if geometry is None:
            return base
        bbox = _safe_call(geometry, "boundingBox", None)
        primitive_kinds: dict[str, int] = {}
        vertex_counts = []
        for prim in list(_safe_call(geometry, "prims", ()))[:5000]:
            kind = _safe_name(_safe_call(prim, "type", None)) or type(prim).__name__
            primitive_kinds[kind] = primitive_kinds.get(kind, 0) + 1
            vertex_counts.append(len(list(_safe_call(prim, "vertices", ()))))
        record: dict[str, Any] = {
            "node_path": _safe_path(node),
            "available": True,
            "point_count": _json_value(_safe_call(geometry, "intrinsicValue", None, "pointcount")),
            "vertex_count": _json_value(_safe_call(geometry, "intrinsicValue", None, "vertexcount")),
            "primitive_count": _json_value(_safe_call(geometry, "intrinsicValue", None, "primitivecount")),
            "bbox": {
                "min": _json_value(_safe_call(bbox, "minvec", None)),
                "max": _json_value(_safe_call(bbox, "maxvec", None)),
                "size": _json_value(_safe_call(bbox, "sizevec", None)),
                "center": _json_value(_safe_call(bbox, "center", None)),
            },
            "primitive_kinds": primitive_kinds,
            "packed_primitive_count": sum(count for name, count in primitive_kinds.items() if "pack" in name.casefold()),
            "volume_primitive_count": sum(count for name, count in primitive_kinds.items() if "volume" in name.casefold() or "vdb" in name.casefold()),
            "topology": {
                "sampled_primitives": len(vertex_counts),
                "min_vertices_per_primitive": min(vertex_counts) if vertex_counts else 0,
                "max_vertices_per_primitive": max(vertex_counts) if vertex_counts else 0,
            },
            "groups": {
                "point": [str(_safe_call(value, "name", "")) for value in _safe_call(geometry, "pointGroups", ())],
                "primitive": [str(_safe_call(value, "name", "")) for value in _safe_call(geometry, "primGroups", ())],
                "edge": [str(_safe_call(value, "name", "")) for value in _safe_call(geometry, "edgeGroups", ())],
            },
            "errors": base["errors"],
        }
        if include_attributes:
            record["attributes"] = {
                "global": [self._attribute_record(value) for value in _safe_call(geometry, "globalAttribs", ())],
                "point": [self._attribute_record(value) for value in _safe_call(geometry, "pointAttribs", ())],
                "vertex": [self._attribute_record(value) for value in _safe_call(geometry, "vertexAttribs", ())],
                "primitive": [self._attribute_record(value) for value in _safe_call(geometry, "primAttribs", ())],
            }
        if sample_limit:
            record["point_samples"] = [
                {"number": _json_value(_safe_call(point, "number", None)), "position": _json_value(_safe_call(point, "position", None))}
                for point in list(_safe_call(geometry, "points", ()))[:sample_limit]
            ]
        return record

    def _attribute_record(self, attribute: Any) -> dict[str, Any]:
        return {
            "name": str(_safe_call(attribute, "name", "")),
            "data_type": _safe_name(_safe_call(attribute, "dataType", None)),
            "size": _json_value(_safe_call(attribute, "size", None)),
            "type_info": _safe_name(_safe_call(attribute, "typeInfo", None)),
            "is_array": bool(_safe_call(attribute, "isArrayType", False)),
        }

    def _interesting_parameters(self, node: Any, terms: tuple[str, ...]) -> list[dict[str, Any]]:
        records = []
        for parm in _safe_call(node, "parms", ()):
            template = _safe_call(parm, "parmTemplate", None)
            name = str(_safe_call(parm, "name", ""))
            label = str(_safe_call(template, "label", ""))
            haystack = f"{name} {label}".casefold()
            if not any(term in haystack for term in terms):
                continue
            records.append(self._parm_record(parm))
            if len(records) >= 100:
                break
        return records

    def _parm_record(self, parm: Any) -> dict[str, Any]:
        template = _safe_call(parm, "parmTemplate", None)
        return {
            "name": str(_safe_call(parm, "name", "")),
            "label": str(_safe_call(template, "label", "")),
            "value": _json_value(_safe_parm_value(parm)),
            "raw_value": _bounded_text(_redact_text(_safe_unexpanded_string(parm)), 4096),
            "time_dependent": bool(_safe_call(parm, "isTimeDependent", False)),
            "keyframe_count": len(list(_safe_call(parm, "keyframes", ()))),
            "disabled": bool(_safe_call(parm, "isDisabled", False)),
            "locked": bool(_safe_call(parm, "isLocked", False)),
        }

    def _keyframe_record(self, keyframe: Any) -> dict[str, Any]:
        return {
            "frame": _json_value(_safe_call(keyframe, "frame", None)),
            "time": _json_value(_safe_call(keyframe, "time", None)),
            "value": _json_value(_safe_call(keyframe, "value", None)),
            "expression": _bounded_text(str(_safe_call(keyframe, "expression", "")), 4096),
        }

    def _type_record(self, node: Any) -> dict[str, Any]:
        node_type = _safe_call(node, "type", None)
        category = _safe_call(node_type, "category", None)
        return {
            "name": str(_safe_call(node_type, "name", "")),
            "category": _safe_name(category),
            "description": str(_safe_call(node_type, "description", "")),
            "name_components": _json_value(_safe_call(node_type, "nameComponents", ())),
        }

    def _nodes_below_paths(self, roots: Iterable[str], *, maximum: int) -> list[Any]:
        result = []
        seen = set()
        for root_path in roots:
            root = self._hou.node(str(root_path))
            if root is None:
                continue
            for node in [root, *_safe_call(root, "allSubChildren", ())]:
                path = _safe_path(node)
                if path in seen:
                    continue
                seen.add(path)
                result.append(node)
                if len(result) >= maximum:
                    return result
        return result

    def _node_digest(self, path: str) -> str | None | object:
        try:
            node = self._hou.node(path)
            if node is None:
                return None
            return self._node_digest_value(node)
        except Exception:
            return _NODE_DIGEST_UNAVAILABLE

    def _artifact_directory(
        self,
        leaf_name: str,
        *,
        fallback: Path,
    ) -> tuple[Path, str, Path | None]:
        hip_directory = self._saved_hip_artifact_directory(leaf_name)
        if hip_directory is not None:
            directory, hip_path = hip_directory
            return directory, "hip", hip_path
        if leaf_name == "screenshots":
            runtime_directory = self._project_root / ".runtime"
            configured_cache = runtime_directory / "cache"
            for directory in (runtime_directory, configured_cache, fallback):
                if os.path.lexists(directory):
                    if _is_reparse_point(directory) or not directory.is_dir():
                        raise RuntimeError(
                            "The runtime screenshot fallback is not an ordinary directory"
                        )
                else:
                    directory.mkdir()
                if _is_reparse_point(directory) or not directory.is_dir():
                    raise RuntimeError(
                        "The runtime screenshot fallback is not an ordinary directory"
                    )
            safe_root = self._cache_root
        else:
            safe_root = (
                self._project_root / ".runtime" / "launcher-sessions"
            ).resolve(strict=True)
        if not os.path.lexists(fallback) or _is_reparse_point(fallback):
            raise RuntimeError("The runtime fallback directory is not ordinary")
        fallback = fallback.resolve(strict=True)
        if (
            not fallback.is_dir()
            or not _is_within(fallback, safe_root)
            or (leaf_name == "screenshots" and fallback.parent != safe_root)
        ):
            raise RuntimeError("The runtime fallback directory is not an ordinary directory")
        return fallback, "runtime_fallback", None

    def _saved_hip_artifact_directory(
        self,
        leaf_name: str,
    ) -> tuple[Path, Path] | None:
        if leaf_name not in {"screenshots", "checkpoints"}:
            raise ValueError("Unsupported HIP-local artifact directory")
        raw_path = _safe_call(self._hou.hipFile, "path", "")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        if bool(_safe_call(self._hou.hipFile, "isNewFile", False)):
            return None
        configured = Path(raw_path.strip())
        if (
            not configured.is_absolute()
            or ".." in configured.parts
            or (
                os.name == "nt"
                and re.fullmatch(r"[A-Za-z]:", configured.drive) is None
            )
            or re.fullmatch(
                r"untitled(?:\d+)?\.hip(?:lc|nc)?",
                configured.name,
                flags=re.IGNORECASE,
            )
            or re.fullmatch(r".+\.hip(?:lc|nc)?", configured.name, flags=re.IGNORECASE)
            is None
        ):
            return None
        try:
            if not _has_ordinary_lexical_path_chain(configured):
                return None
            hip_path = configured.resolve(strict=True)
            parent = hip_path.parent
            if (
                not hip_path.is_file()
                or parent == parent.parent
                or not parent.is_dir()
                or _is_reparse_point(parent)
                or not os.access(parent, os.W_OK)
            ):
                return None
            hia_directory = parent / ".hia"
            artifact_directory = hia_directory / leaf_name
            for directory in (hia_directory, artifact_directory):
                if os.path.lexists(directory):
                    if _is_reparse_point(directory) or not directory.is_dir():
                        return None
                else:
                    directory.mkdir()
                if _is_reparse_point(directory) or not directory.is_dir():
                    return None
            resolved_hia = hia_directory.resolve(strict=True)
            resolved_artifact = artifact_directory.resolve(strict=True)
            if (
                resolved_hia.parent != parent
                or resolved_artifact.parent != resolved_hia
                or not resolved_hia.is_dir()
                or not resolved_artifact.is_dir()
                or _is_reparse_point(hia_directory)
                or _is_reparse_point(artifact_directory)
            ):
                return None
            probe_path = resolved_artifact / f".hia-write-probe-{uuid.uuid4().hex}"
            descriptor = os.open(
                probe_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            probe_path.unlink()
            return resolved_artifact, hip_path
        except (OSError, RuntimeError):
            if "probe_path" in locals():
                try:
                    probe_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return None

    @contextlib.contextmanager
    def _houdini_backup_directory(self, directory: Path) -> Iterable[None]:
        environment_name = "HOUDINI_BACKUP_DIR"
        previous_os_value = os.environ.get(environment_name)
        getenv = getattr(self._hou, "getenv", None)
        putenv = getattr(self._hou, "putenv", None)
        unsetenv = getattr(self._hou, "unsetenv", None)
        previous_hou_value = (
            getenv(environment_name) if callable(getenv) else previous_os_value
        )
        value = str(directory)
        try:
            os.environ[environment_name] = value
            if callable(putenv):
                putenv(environment_name, value)
            yield
        finally:
            if previous_os_value is None:
                os.environ.pop(environment_name, None)
            else:
                os.environ[environment_name] = previous_os_value
            if previous_hou_value is None:
                if callable(unsetenv):
                    unsetenv(environment_name)
            elif callable(putenv):
                putenv(environment_name, previous_hou_value)

    def _checkpoint_directory(self) -> Path:
        raw_path = os.environ.get("HOUDINI_BACKUP_DIR", "").strip()
        if not raw_path:
            raise RuntimeError("HOUDINI_BACKUP_DIR is not configured")
        configured = Path(raw_path)
        if not configured.is_absolute():
            raise RuntimeError("HOUDINI_BACKUP_DIR must be absolute")
        if (
            _is_reparse_point(configured)
            or _is_reparse_point(configured.parent)
            or _is_reparse_point(configured.parent.parent)
        ):
            raise RuntimeError("HOUDINI_BACKUP_DIR must not use a reparse point")
        try:
            directory = configured.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("HOUDINI_BACKUP_DIR does not exist") from exc
        sessions_root = (self._project_root / ".runtime" / "launcher-sessions").resolve()
        session_id = directory.parent.name
        if (
            not directory.is_dir()
            or not _is_within(directory, self._project_root)
            or directory.name.casefold() != "checkpoints"
            or re.fullmatch(r"[0-9a-fA-F]{32}", session_id) is None
            or directory.parent.parent != sessions_root
        ):
            raise RuntimeError(
                "HOUDINI_BACKUP_DIR must be the current project launcher session checkpoints directory"
            )
        return directory

    def _goal_focus_mode(self) -> bool:
        return self._goal_focus_target() is not None

    def _goal_focus_target(self) -> tuple[str, str] | None:
        raw_path = os.environ.get("HIA_FOCUS_STATE_PATH", "").strip()
        if not raw_path:
            return None
        configured = Path(raw_path)
        expected = self._project_root / ".runtime" / "bridge" / "focus-mode.json"
        try:
            resolved = configured.resolve(strict=True)
            if (
                not configured.is_absolute()
                or configured.is_symlink()
                or resolved != expected
                or not resolved.is_file()
                or resolved.stat().st_size > FOCUS_STATE_MAX_BYTES
            ):
                return None
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        thread_id = payload.get("active_thread_id")
        enabled = payload.get("enabled_thread_ids")
        bindings = payload.get("goal_bindings")
        goal_binding = bindings.get(thread_id) if isinstance(bindings, dict) else None
        if (
            not isinstance(thread_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", thread_id) is None
            or not isinstance(enabled, list)
            or thread_id not in enabled
            or not isinstance(goal_binding, str)
            or re.fullmatch(r"[0-9a-f]{64}", goal_binding) is None
        ):
            return None
        return thread_id, goal_binding

    @staticmethod
    def _write_stage_checkpoint_marker(
        session_checkpoint_directory: Path,
        checkpoint_path: Path,
        thread_id: str,
        goal_binding: str,
        *,
        storage_scope: str,
        source_hip_path: Path | None,
    ) -> None:
        if storage_scope not in {"hip", "runtime_fallback"}:
            raise RuntimeError("Checkpoint storage scope is invalid")
        if storage_scope == "hip" and source_hip_path is None:
            raise RuntimeError("HIP-local checkpoints require a source HIP path")
        if storage_scope == "runtime_fallback" and source_hip_path is not None:
            raise RuntimeError("Runtime checkpoints must not claim a source HIP path")
        payload = {
            "version": 2,
            "launcher_session_id": session_checkpoint_directory.parent.name,
            "thread_id": thread_id,
            "goal_binding": goal_binding,
            "storage_scope": storage_scope,
            "source_hip_path": (
                str(source_hip_path) if source_hip_path is not None else None
            ),
            "checkpoint_file": checkpoint_path.name,
        }
        marker_directories = (
            [checkpoint_path.parent, session_checkpoint_directory]
            if storage_scope == "hip"
            else [session_checkpoint_directory]
        )
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        for marker_directory in marker_directories:
            marker = marker_directory / STAGE_CHECKPOINT_MARKER
            temporary = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, marker)

    def _node_digest_value(self, node: Any) -> str:
        payload = {
            "type": self._type_record(node),
            "inputs": [
                _safe_path(value) if value is not None else None
                for value in _safe_call(node, "inputs", ())
            ],
            "outputs": [
                _safe_path(value)
                for value in _safe_call(node, "outputs", ())
            ],
            "flags": {
                "display": bool(_safe_call(node, "isDisplayFlagSet", False)),
                "render": bool(_safe_call(node, "isRenderFlagSet", False)),
                "bypassed": bool(_safe_call(node, "isBypassed", False)),
            },
            "parameters": [
                (
                    str(_safe_call(parm, "name", "")),
                    self._persistent_parm_digest_value(parm),
                )
                for parm in list(_safe_call(node, "parms", ()))[:256]
            ],
        }
        encoded = json.dumps(
            _json_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _persistent_parm_digest_value(self, parm: Any) -> dict[str, Any]:
        keyframes = list(_safe_call(parm, "keyframes", ()))
        if keyframes:
            return {
                "kind": "keyframes",
                "values": [
                    self._keyframe_record(value)
                    for value in keyframes[:256]
                ],
                "truncated": len(keyframes) > 256,
            }
        try:
            return {
                "kind": "raw_string",
                "value": str(parm.unexpandedString()),
            }
        except Exception:
            pass
        expression = getattr(parm, "expression", None)
        if callable(expression):
            try:
                return {
                    "kind": "expression",
                    "value": str(expression()),
                }
            except Exception:
                pass
        return {
            "kind": "static_value",
            "value": _json_value(_safe_parm_value(parm)),
        }

    def _snapshot_map(self, root_path: str) -> tuple[dict[str, str], bool]:
        root = self._hou.node(root_path)
        if root is None:
            raise HiaRuntimeError("NODE_NOT_FOUND", "Snapshot root does not exist", {"path": root_path})
        nodes = [root, *_safe_call(root, "allSubChildren", ())]
        truncated = len(nodes) > MAX_SNAPSHOT_NODES
        result = {}
        for node in nodes[:MAX_SNAPSHOT_NODES]:
            path = _safe_path(node)
            result[path] = self._node_digest_value(node)
        return result, truncated

    @staticmethod
    def _diff_maps(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
        before_paths = set(before)
        after_paths = set(after)
        return {
            "created": sorted(after_paths - before_paths),
            "deleted": sorted(before_paths - after_paths),
            "changed": sorted(path for path in before_paths & after_paths if before[path] != after[path]),
        }


def _seconds(value: float) -> float:
    return round(max(0.0, float(value)), 6)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_houdini_node_path(path: str) -> bool:
    if (
        not path
        or len(path) > 4096
        or not path.startswith("/")
        or "\\" in path
        or "\x00" in path
    ):
        return False
    if path == "/":
        return True
    parts = path[1:].split("/")
    return all(part and part not in {".", ".."} for part in parts)


def _houdini_path_is_within(path: str, root: str) -> bool:
    if not _valid_houdini_node_path(path) or not _valid_houdini_node_path(root):
        return False
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")


def _classify_expected_deletions(
    observed_paths: Iterable[str],
    expected_roots: Iterable[str],
) -> tuple[list[str], list[str], list[str]]:
    observed = {str(path) for path in observed_paths}
    expected = {str(path) for path in expected_roots}
    expected_observed = sorted(expected.intersection(observed))
    expected_missing = sorted(expected.difference(observed))
    unexpected = sorted(
        path
        for path in observed
        if not any(
            _houdini_path_is_within(path, expected_root)
            for expected_root in expected
        )
    )
    return expected_observed, expected_missing, unexpected


def _temporal_image_evidence(
    paths: Iterable[Path],
) -> tuple[list[str], dict[str, Any]]:
    hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    ]
    unique_count = len(set(hashes))
    no_change = len(hashes) > 1 and unique_count == 1
    return hashes, {
        "sample_count": len(hashes),
        "unique_hash_count": unique_count,
        "no_change_detected": no_change,
        "simulation_advancement": (
            "observed_visual_change"
            if unique_count > 1
            else ("not_proven" if no_change else "unavailable")
        ),
    }


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    if (
        len(raw) < 24
        or raw[:8] != b"\x89PNG\r\n\x1a\n"
        or raw[12:16] != b"IHDR"
    ):
        raise HiaRuntimeError(
            "VIEWPORT_CAPTURE_FAILED",
            "Houdini produced an invalid PNG viewport image",
        )
    width, height = struct.unpack(">II", raw[16:24])
    if width <= 0 or height <= 0:
        raise HiaRuntimeError(
            "VIEWPORT_CAPTURE_FAILED",
            "Houdini produced invalid viewport image dimensions",
        )
    return width, height


def _capture_observation(
    value: Any,
    name: str,
    *args: Any,
) -> tuple[Any, bool]:
    if value is None:
        return None, False
    attribute = getattr(value, name, None)
    if attribute is None:
        return None, False
    try:
        observed = attribute(*args) if callable(attribute) else attribute
    except Exception:
        return None, False
    return observed, True


def _viewport_capture_source_state(
    hou_module: Any,
    scene_viewer: Any,
    viewport: Any,
    flipbook_settings: Any,
    requested_camera_path: str,
    requested_resolution: tuple[int, int],
) -> dict[str, Any]:
    unverified: list[str] = []

    def observe(label: str, value: Any, name: str, *args: Any) -> Any:
        observed, available = _capture_observation(value, name, *args)
        if not available:
            unverified.append(label)
            return "unverified"
        return _json_value(observed)

    def observe_map(
        prefix: str,
        value: Any,
        specs: Mapping[str, tuple[Any, ...]],
    ) -> dict[str, Any]:
        return {
            key: observe(f"{prefix}.{key}", value, str(spec[0]), *spec[1:])
            for key, spec in specs.items()
        }

    active_camera, camera_available = _capture_observation(viewport, "camera")
    if not camera_available:
        camera_mode = "unverified"
        active_camera_path: Any = "unverified"
        unverified.extend(("camera.mode", "camera.path"))
    elif active_camera is None:
        camera_mode = "free_view"
        active_camera_path = None
    else:
        camera_mode = "camera"
        active_camera_path = observe(
            "camera.path",
            active_camera,
            "path",
        )

    settings, settings_available = _capture_observation(viewport, "settings")
    if not settings_available:
        settings = None
        unverified.append("display_options")

    shading: Any = "unverified"
    display_set_type = getattr(
        getattr(hou_module, "displaySetType", None),
        "DisplayModel",
        None,
    )
    if settings is not None and display_set_type is not None:
        display_set, display_set_available = _capture_observation(
            settings,
            "displaySet",
            display_set_type,
        )
        if display_set_available:
            shading = observe(
                "display_options.shading",
                display_set,
                "shadedMode",
            )
        else:
            unverified.append("display_options.shading")
    else:
        unverified.append("display_options.shading")

    lut = observe(
        "color_management.flipbook_lut",
        flipbook_settings,
        "LUT",
    )
    if isinstance(lut, str) and lut != "unverified":
        lut = _redact_path(lut)

    display_options = observe_map(
        "display_options",
        settings,
        {
            "viewport_type": ("viewportType",),
            "lighting": ("lighting",),
            "materials": ("showingMaterials",),
            "diffuse": ("showingDiffuse",),
            "specular": ("showingSpecular",),
            "ambient": ("showingAmbient",),
            "emission": ("showingEmission",),
            "transparency": ("usingTransparency",),
            "geometry_color": ("showingGeometryColor",),
        },
    )
    display_options["shading"] = shading
    color_management = {
        **observe_map(
            "color_management",
            scene_viewer,
            {
                "ocio_enabled": ("usingOCIO",),
                "ocio_display": ("getOCIODisplay",),
                "ocio_view": ("getOCIOView",),
            },
        ),
        "ocio_look": "unverified",
        "viewport_exposure": "unverified",
        "viewport_gamma": "unverified",
        **observe_map(
            "color_management",
            flipbook_settings,
            {
                "flipbook_gamma_override": ("overrideGamma",),
                "flipbook_gamma": ("gamma",),
                "flipbook_lut_override": ("overrideLUT",),
            },
        ),
        "flipbook_lut": lut,
    }
    unverified.extend(
        (
            "color_management.ocio_look",
            "color_management.viewport_exposure",
            "color_management.viewport_gamma",
            "hdr.os_hdr",
            "hdr.os_compositor",
        )
    )
    return {
        "scene_viewer": {
            **observe_map(
                "scene_viewer",
                scene_viewer,
                {"name": ("name",), "layout": ("viewportLayout",)},
            ),
            "viewport_selection": "curViewport",
        },
        "viewport": observe_map(
            "viewport",
            viewport,
            {
                "name": ("name",),
                "visible": ("isVisible",),
                "size": ("size",),
                "projection": ("type",),
            },
        ),
        "camera": {
            "mode": camera_mode,
            "path": active_camera_path,
            "requested_path": requested_camera_path or None,
            **observe_map(
                "camera",
                viewport,
                {"locked_to_view": ("isCameraLockedToView",)},
            ),
        },
        "resolution": {
            "requested": list(requested_resolution),
            **observe_map(
                "resolution",
                settings,
                {
                    "aspect_ratio_enforced": ("usingAspectRatio",),
                    "display_aspect": ("aspectRatio",),
                    "view_aspect": ("viewAspectRatio", False),
                    "masked_view_aspect": ("viewAspectRatio", True),
                },
            ),
            **observe_map(
                "resolution",
                flipbook_settings,
                {"crop_out_view_mask_overlay": ("cropOutMaskOverlay",)},
            ),
        },
        "display_options": display_options,
        "color_management": color_management,
        "hdr": {
            "os_hdr": "unverified",
            "os_compositor": "unverified",
        },
        "unverified": sorted(set(unverified)),
    }


def _usd_prim_subtree(root_prim: Any) -> Iterable[Any]:
    """Yield one USD prim subtree in stable depth-first order, including root."""

    pending = [root_prim]
    while pending:
        prim = pending.pop()
        yield prim
        children = list(prim.GetChildren())
        pending.extend(reversed(children))


def _safe_call(value: Any, name: str, default: Any, *args: Any) -> Any:
    if value is None:
        return default
    method = getattr(value, name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except Exception:
        return default


def _is_cooking_interrupted_message(value: Any) -> bool:
    text = " ".join(str(value or "").casefold().split()).rstrip(".")
    return text == "cooking was interrupted"


def _safe_path(value: Any) -> str:
    result = _safe_call(value, "path", "")
    return str(result) if result is not None else ""


def _safe_name(value: Any) -> str:
    result = _safe_call(value, "name", "")
    if not result:
        result = str(value) if value is not None else ""
        result = result.rsplit(".", 1)[-1]
    return str(result)


def _safe_parm_value(parm: Any) -> Any:
    try:
        return parm.eval()
    except Exception as exc:
        return f"<evaluation failed: {_bounded_text(_redact_text(str(exc)), 256)}>"


def _safe_unexpanded_string(parm: Any) -> str:
    try:
        return str(parm.unexpandedString())
    except Exception:
        return str(_safe_parm_value(parm))


def _json_value(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 8:
        return "<max depth>"
    if isinstance(value, float) and not math.isfinite(value):
        label = "nan" if math.isnan(value) else (
            "infinity" if value > 0 else "-infinity"
        )
        return f"<non-finite float: {label}>"
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, Mapping):
        return {
            _bounded_text(str(key), 256): _json_value(item, _depth=_depth + 1)
            for key, item in list(value.items())[:1000]
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item, _depth=_depth + 1) for item in list(value)[:1000]]
    try:
        return [_json_value(item, _depth=_depth + 1) for item in list(value)[:1000]]
    except (TypeError, AttributeError):
        return _bounded_text(_redact_text(str(value)), 4096)


def _application_version(hou_module: Any) -> str:
    text = _safe_call(hou_module, "applicationVersionString", "")
    if text:
        return str(text)
    return ".".join(str(value) for value in _safe_call(hou_module, "applicationVersion", ()))


def _houdini_frame_ranges(hou_module: Any) -> tuple[Any, Any]:
    playbar = getattr(hou_module, "playbar", None)
    frame_range = _safe_call(playbar, "frameRange", _safe_call(hou_module, "frameRange", ()))
    playback_range = _safe_call(
        playbar,
        "playbackRange",
        _safe_call(hou_module, "playbarRange", ()),
    )
    return frame_range, playback_range


def _limit(arguments: Mapping[str, Any]) -> int:
    return _bounded_int(arguments.get("limit", DEFAULT_LIMIT), 1, MAX_LIMIT)


def _local_help_refresh_stats(
    groups: Iterable[str],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "refreshed": False,
        "refresh_reason": reason,
        "refresh_requested_groups": sorted(set(groups)),
        "refresh_groups": [],
        "files_scanned": 0,
        "inline_records_scanned": 0,
        "documents_added": 0,
        "documents_updated": 0,
        "documents_body_changed": 0,
        "documents_metadata_changed": 0,
        "documents_removed": 0,
        "documents_unchanged": 0,
    }


def _local_help_public_status(
    values: list[Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, float]]:
    retrievals = [
        value for value in values if isinstance(value, Mapping)
    ]
    first = retrievals[0] if retrievals else {}
    raw_vector = first.get("vector")
    vector = dict(raw_vector) if isinstance(raw_vector, Mapping) else {}
    raw_encoder = first.get("encoder")
    if isinstance(raw_encoder, Mapping):
        encoder = dict(raw_encoder)
    else:
        encoder = {
            key: value
            for key, value in vector.items()
            if key != "index"
        }
    raw_corpus = first.get("corpus")
    if isinstance(raw_corpus, Mapping):
        corpus = dict(raw_corpus)
    elif isinstance(vector.get("index"), Mapping):
        corpus = dict(vector["index"])
    else:
        corpus = {}
    scopes = {
        str(_local_help_query_status(value).get("ranking_scope") or "")
        for value in retrievals
    }
    scopes.discard("")
    if len(scopes) == 1:
        corpus["ranking_scope"] = next(iter(scopes))
    elif len(scopes) > 1:
        corpus["ranking_scope"] = "mixed"
    corpus["global_recall"] = bool(
        corpus.get("complete")
        and corpus.get("ranking_scope") == "global"
    )
    modes = {
        str(value.get("mode_used") or "")
        for value in retrievals
    }
    modes.discard("")
    fallback_reasons = sorted(
        {
            str(value.get("fallback_reason") or "")
            for value in retrievals
            if str(value.get("fallback_reason") or "")
        }
    )
    public = {
        "requested_mode": str(first.get("requested_mode") or ""),
        "mode_used": (
            next(iter(modes))
            if len(modes) == 1
            else "mixed"
            if modes
            else ""
        ),
        "lexical": dict(first.get("lexical") or {}),
        "encoder": encoder,
        "fallback_reason": "; ".join(fallback_reasons),
    }
    timing_names = (
        "fts_seconds",
        "query_encode_seconds",
        "vector_scan_seconds",
    )
    timings = {
        name: max(
            (
                float(value.get("timings", {}).get(name, 0.0))
                for value in retrievals
                if isinstance(value.get("timings"), Mapping)
            ),
            default=0.0,
        )
        for name in timing_names
    }
    return public, corpus, timings


def _local_help_query_status(value: Any) -> dict[str, Any]:
    retrieval = value if isinstance(value, Mapping) else {}
    raw_corpus = retrieval.get("corpus")
    raw_vector = retrieval.get("vector")
    if isinstance(raw_corpus, Mapping):
        corpus = raw_corpus
    elif (
        isinstance(raw_vector, Mapping)
        and isinstance(raw_vector.get("index"), Mapping)
    ):
        corpus = raw_vector["index"]
    else:
        corpus = {}
    result = {
        "mode_used": str(retrieval.get("mode_used") or ""),
    }
    ranking_scope = str(corpus.get("ranking_scope") or "")
    fallback_reason = str(retrieval.get("fallback_reason") or "")
    if ranking_scope:
        result["ranking_scope"] = ranking_scope
    if fallback_reason:
        result["fallback_reason"] = _bounded_text(fallback_reason, 512)
    return result


def _local_help_match(
    value: Mapping[str, Any],
    response_format: str,
) -> dict[str, Any]:
    if response_format != "compact":
        converted = _json_value(dict(value))
        return dict(converted) if isinstance(converted, Mapping) else {}
    metadata = value.get("metadata")
    details = metadata if isinstance(metadata, Mapping) else {}
    scores: dict[str, float] = {}
    for public_name, key in (
        ("lexical", "lexical_score"),
        ("vector", "vector_score"),
        ("hybrid", "hybrid_score"),
    ):
        raw = details.get(key, value.get(key))
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            scores[public_name] = round(float(raw), 8)
    return {
        "title": _bounded_text(str(value.get("title") or ""), 512),
        "summary": _bounded_text(
            str(value.get("snippet") or ""),
            MAX_LOCAL_HELP_SUMMARY_CHARS,
        ),
        "source": _bounded_text(str(value.get("source") or ""), 128),
        "source_kind": _bounded_text(
            str(
                value.get("source_kind")
                or details.get("source_kind")
                or value.get("source")
                or ""
            ),
            128,
        ),
        "card_id": _bounded_text(str(details.get("card_id") or ""), 128),
        "canonical_id": _bounded_text(
            str(details.get("canonical_id") or ""),
            128,
        ),
        "pack_version": _bounded_text(
            str(details.get("pack_version") or ""),
            128,
        ),
        "url": _bounded_text(str(details.get("url") or ""), 2048),
        "houdini_version": _bounded_text(
            str(details.get("houdini_version") or ""),
            128,
        ),
        "verification": _bounded_text(
            str(details.get("verification") or ""),
            128,
        ),
        "scores": scores,
    }


def _append_local_help_match_within_budget(
    matches: list[dict[str, Any]],
    match: dict[str, Any],
    payload: Mapping[str, Any],
    maximum_bytes: int,
) -> bool:
    matches.append(match)
    if _json_size(payload) <= maximum_bytes:
        return True
    content = match.get("content")
    if not isinstance(content, str):
        matches.pop()
        return False

    original_truncated = match.get("content_truncated")
    match["content"] = ""
    match["content_truncated"] = True
    if _json_size(payload) > maximum_bytes:
        matches.pop()
        match["content"] = content
        match["content_truncated"] = original_truncated
        return False

    lower = 0
    upper = len(content)
    while lower < upper:
        candidate = (lower + upper + 1) // 2
        match["content"] = content[:candidate]
        if _json_size(payload) <= maximum_bytes:
            lower = candidate
        else:
            upper = candidate - 1
    match["content"] = content[:lower]
    return True


def _json_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _query_values(
    arguments: Mapping[str, Any],
    *,
    minimum_length: int = 0,
    maximum_length: int = 512,
) -> tuple[list[str], bool]:
    raw_queries = arguments.get("queries")
    if raw_queries is None:
        raw_query = arguments.get("query", "")
        if not isinstance(raw_query, str):
            raise HiaRuntimeError("INVALID_ARGUMENTS", "query must be a string")
        query = raw_query.strip() if minimum_length else raw_query
        if not minimum_length <= len(query) <= maximum_length:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                f"query must contain between {minimum_length} and {maximum_length} characters",
            )
        return [query], False
    if "query" in arguments:
        raise HiaRuntimeError(
            "INVALID_ARGUMENTS",
            "Provide query or queries, not both",
        )
    if not isinstance(raw_queries, list) or not 1 <= len(raw_queries) <= MAX_BATCH_QUERIES:
        raise HiaRuntimeError(
            "INVALID_ARGUMENTS",
            f"queries must contain between 1 and {MAX_BATCH_QUERIES} strings",
        )
    queries: list[str] = []
    seen: set[str] = set()
    for index, raw_query in enumerate(raw_queries):
        if not isinstance(raw_query, str):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Each queries item must be a string",
                {"index": index},
            )
        query = raw_query.strip() if minimum_length else raw_query
        if not minimum_length <= len(query) <= maximum_length:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                f"Each query must contain between {minimum_length} and {maximum_length} characters",
                {"index": index},
            )
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            queries.append(query)
    return queries, True


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HiaRuntimeError("INVALID_ARGUMENTS", f"Integer must be between {minimum} and {maximum}")
    return value


def _bounded_text(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 13] + "<truncated>"


def _redact_text(value: str) -> str:
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", str(value))
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)(\s*[:=]\s*)[^\s,;\"']+", r"\1\2[REDACTED]", text)
    text = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s]+", r"%USERPROFILE%", text)
    for name, secret in os.environ.items():
        if re.search(r"(?i)(token|secret|password|api.?key)", name) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return text


def _redact_path(value: str) -> str:
    return _redact_text(value)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return True


def _has_ordinary_lexical_path_chain(path: Path) -> bool:
    current = path
    first = True
    while True:
        try:
            if (
                not os.path.lexists(current)
                or _is_reparse_point(current)
                or (first and not current.is_file())
                or (not first and not current.is_dir())
            ):
                return False
        except OSError:
            return False
        parent = current.parent
        if parent == current:
            return True
        current = parent
        first = False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
