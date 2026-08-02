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


MAX_SCRIPT_CHARS = 524_288
MAX_FLIPBOOK_FRAME_SPAN = 240.0
MAX_CAPTURE_FRAMES = 24
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
            "hia_execute_hom": self._execute_hom_direct,
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

        def run() -> dict[str, Any]:
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
            "current_network": _safe_path(current_network) or "unavailable",
            "current_node": _safe_path(current_node) or "unavailable",
            "selection": [_safe_path(node) for node in selected_nodes],
            "scene_revision": self.scene_revision,
            "goal_focus_mode": self._goal_focus_mode(),
            "available_contexts": contexts,
            "ui_available": bool(_safe_call(self._hou, "isUIAvailable", True)),
        }
        if bool(arguments.get("include_graph", False)):
            depth = _bounded_int(arguments.get("graph_depth", 1), 0, 3)
            limit = _limit(arguments)
            root_path = _safe_path(current_network)
            result["graph"] = (
                self._graph_records(root_path, depth=depth, query="", limit=limit)
                if root_path
                else {"status": "unavailable", "reason": "current_network_unavailable"}
            )
        if bool(arguments.get("include_runtime_capabilities", False)):
            result["runtime_capabilities"] = self._runtime_capability_probe(
                current_node
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
                "status": "not_requested", "error": "",
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
                    "error": "",
                })
                pack["sources"].append(
                    {"id": "local_knowledge", "kind": "cached_sqlite_fts5", "database": database}
                )
            except Exception as exc:
                knowledge.update({
                    "hits": [], "status": "unavailable",
                    "error": _bounded_text(_redact_text(str(exc)), 1024),
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
        return self._success({"nodes": page, "total": len(records), "offset": offset, "limit": limit})

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
        if set(arguments) != {"requests"}:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "Batch node help options belong inside each requests item",
            )
        results = []
        error_count = 0
        for index, request in enumerate(requests):
            if not isinstance(request, Mapping):
                raise HiaRuntimeError(
                    "INVALID_ARGUMENTS",
                    "Each requests item must be an object",
                    {"index": index},
                )
            try:
                results.append(
                    {
                        "index": index,
                        "request": dict(request),
                        "ok": True,
                        "result": self._node_help_result(request),
                    }
                )
            except HiaRuntimeError as exc:
                error_count += 1
                results.append(
                    {
                        "index": index,
                        "request": dict(request),
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
        nodes = self._resolve_nodes(arguments)
        limit = _limit(arguments)
        include_attributes = bool(arguments.get("include_attributes", True))
        sample_limit = _bounded_int(arguments.get("sample_limit", 0), 0, 100)
        records = [
            self._geometry_record(node, include_attributes=include_attributes, sample_limit=sample_limit)
            for node in nodes[:limit]
        ]
        return self._success({"geometry": records, "total": len(nodes), "limit": limit})

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
            iterator: Iterable[Any] = [root_prim] if root_prim else []
            if root_prim:
                iterator = [root_prim, *list(root_prim.GetDescendants())]
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
        nodes = self._resolve_nodes(arguments)
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
                "offset": offset,
                "limit": limit,
                "frame": _json_value(_safe_call(self._hou, "frame", 0.0)),
                "frame_range": _json_value(frame_range),
                "playbar_range": _json_value(playback_range),
                "take": _safe_name(take),
            }
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
                if mutable_root and not _houdini_path_is_within(path, mutable_root):
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
                iterator = getattr(geometry, iterator_name, None)
                if callable(iterator):
                    elements = iterator()
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

    def _execute_hom_direct(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one HOM script without hidden validation or recovery work."""

        started = time.monotonic()
        unexpected = sorted(set(arguments).difference({"script", "timeout_seconds"}))
        if unexpected:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "hia_execute_hom accepts only script and timeout_seconds",
                {"fields": unexpected},
            )
        script = arguments.get("script")
        if not isinstance(script, str) or not script.strip():
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "script must be a non-empty string",
            )
        if len(script) > MAX_SCRIPT_CHARS:
            raise HiaRuntimeError(
                "REQUEST_TOO_LARGE",
                "The HOM script exceeds the character limit",
                {"limit": MAX_SCRIPT_CHARS},
            )
        timeout_value = arguments.get("timeout_seconds", 60.0)
        if isinstance(timeout_value, bool) or not isinstance(
            timeout_value,
            (int, float),
        ):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "timeout_seconds must be a number",
            )
        timeout_seconds = float(timeout_value)
        if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 300:
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "timeout_seconds must be between 1 and 300",
            )

        script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
        dirty_before = self._dirty_observation()
        stdout = io.StringIO()
        stderr = io.StringIO()
        warning_records: list[str] = []
        namespace: dict[str, Any] = {
            "__name__": "__hia_execute_hom__",
            "hou": self._hou,
            "hia_result": None,
        }
        failure: dict[str, Any] | None = None
        started_execution = False
        try:
            compiled_script = compile(script, "<hia_execute_hom>", "exec")
        except (SyntaxError, ValueError) as exc:
            failure = {
                "code": "INVALID_HOM_SCRIPT",
                "message": _bounded_text(_redact_text(str(exc)), 2048),
                "partial_scene_changes_possible": False,
            }
        else:
            try:
                with self._hou.undos.group(
                    "HIA MCP V2 execute"
                ), contextlib.redirect_stdout(
                    stdout
                ), contextlib.redirect_stderr(stderr), python_warnings.catch_warnings(
                    record=True
                ) as caught:
                    python_warnings.simplefilter("always")
                    started_execution = True
                    exec(compiled_script, namespace, namespace)
                    warning_records.extend(str(item.message) for item in caught)
            except Exception as exc:
                failure = {
                    "code": (
                        "HOM_EXECUTION_FAILED"
                        if started_execution
                        else "UNDO_GROUP_UNAVAILABLE"
                    ),
                    "message": _bounded_text(_redact_text(str(exc)), 2048),
                    "traceback": _bounded_text(
                        _redact_text(traceback.format_exc(limit=20)),
                        20_000,
                    ),
                    "partial_scene_changes_possible": started_execution,
                }
        if stderr.getvalue().strip():
            warning_records.append(stderr.getvalue())

        dirty_after = self._dirty_observation()
        if failure is not None and not started_execution:
            scene_change_status = "unchanged"
        elif dirty_before is False and dirty_after is False:
            scene_change_status = "unknown" if failure is not None else "unchanged"
        elif (
            dirty_before is not None
            and dirty_after is not None
            and dirty_before != dirty_after
        ):
            scene_change_status = "changed"
        else:
            scene_change_status = "unknown"
        if scene_change_status != "unchanged":
            with self._state_lock:
                self._scene_revision += 1

        return {
            "ok": failure is None,
            "result": (
                _json_value(namespace.get("hia_result"))
                if failure is None
                else None
            ),
            "stdout": _bounded_text(
                _redact_text(stdout.getvalue()),
                MAX_TEXT_CHARS,
            ),
            "warnings": [
                _bounded_text(_redact_text(value), 4096)
                for value in warning_records[:100]
            ],
            "errors": [failure] if failure is not None else [],
            "revision": self.scene_revision,
            "dirty": dirty_after,
            "elapsed_seconds": _seconds(time.monotonic() - started),
            "script_sha256": script_sha256,
            "scene_change_status": scene_change_status,
        }

    def _scene_diff(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", ""))
        snapshot_id = str(arguments.get("snapshot_id", ""))
        root_path = str(arguments.get("root_path", "/"))
        limit = _limit(arguments)
        if action == "capture":
            nodes, truncated = self._snapshot_map(root_path)
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
                {"snapshot_id": snapshot_id, "root_path": root_path, "node_count": len(nodes), "truncated": truncated}
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
            maximum=64,
        )
        current, truncated = self._snapshot_map(snapshot.root_path)
        diff = self._diff_maps(snapshot.nodes, current)
        observed_deleted = set(diff["deleted"])
        expected_deleted = set(expected_deletions)
        diff["expected_deletions"] = sorted(
            observed_deleted.intersection(expected_deleted)
        )
        diff["missing_expected_deletions"] = sorted(
            expected_deleted.difference(observed_deleted)
        )
        diff["unexpected_deletions"] = sorted(
            observed_deleted.difference(expected_deleted)
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
                    if exc.code == "VIEWPORT_CAPTURE_UNAVAILABLE":
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
                "VIEWPORT_CAPTURE_UNAVAILABLE",
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
            temporal_status = "failed"
            errors.append(
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
            "ok": bool(successful) and temporal_status != "failed",
            "result": first_result,
            "warnings": list(dict.fromkeys(warnings))[:32],
            "errors": list(dict.fromkeys(errors))[:32],
            "revision": self.scene_revision,
            "dirty": self._dirty(),
        }
        return result

    def _capture_viewport(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not bool(_safe_call(self._hou, "isUIAvailable", True)):
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                "Viewport capture requires a graphical Houdini session",
            )
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
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                "No Scene Viewer pane is available",
            )
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
                raise HiaRuntimeError(
                    "VIEWPORT_CAPTURE_UNAVAILABLE",
                    "The viewport camera does not exist",
                    {"path": camera_path},
                )
        capture_dir = self._screenshot_directory()
        storage_scope = "project_cache"
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
            if not callable(getattr(scene_viewer, "flipbook", None)) or not callable(
                getattr(scene_viewer, "flipbookSettings", None)
            ):
                raise RuntimeError(
                    "The documented SceneViewer.flipbook capture API is unavailable"
                )
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
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                _bounded_text(_redact_text(str(capture_error)), 2048),
                details,
            ) from capture_error
        if restore_errors:
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                "The viewport image was captured but the original viewer state could not be fully restored",
                {"errors": restore_errors},
            )
        if not output_path.is_file():
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                "Houdini did not produce the expected viewport image",
                {"path": str(output_path)},
            )
        with output_path.open("rb") as stream:
            actual_width, actual_height = _png_dimensions(stream.read(24))
        if (actual_width, actual_height) != (width, height):
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                "Houdini did not honor the requested flipbook resolution",
                {
                    "requested": [width, height],
                    "actual": [actual_width, actual_height],
                },
            )
        resolution_state = source_state.setdefault("resolution", {})
        resolution_state.update(
            {
                "source": resolution_source,
                "actual": [actual_width, actual_height],
                "requested_aspect": round(width / height, 8),
                "actual_aspect": round(actual_width / actual_height, 8),
            }
        )
        if camera_path:
            observed_camera_path = source_state.get("camera", {}).get("path")
            if observed_camera_path != camera_path:
                raise HiaRuntimeError(
                    "VIEWPORT_CAPTURE_UNAVAILABLE",
                    "The effective viewport camera could not be verified",
                    {
                        "requested_camera": camera_path,
                        "observed_camera": observed_camera_path,
                    },
                )
        absolute_path = str(output_path.resolve(strict=True))
        display_path = output_path.relative_to(self._project_root).as_posix()
        warnings: list[str] = []
        errors: list[str] = []
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
            "ok": True,
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
                "frame_lock": frame_lock_evidence,
                "cook_cache_evidence": capture_cook_evidence,
                "capture_api": capture_api,
                "capture_ok": True,
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
        try:
            hybrid_knowledge = self._hybrid_knowledge_store(
                initialize=refresh_requested
            )
            knowledge_index = hybrid_knowledge.index
            refreshable_sources = sources.intersection(SOURCE_GROUPS)
        except Exception as exc:
            raise HiaRuntimeError(
                "LOCAL_HELP_INDEX_UNAVAILABLE",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
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
            "dirty": snapshot.get("dirty"),
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
        return {
            "ok": True,
            "result": payload,
            "stdout": "",
            "warnings": [],
            "errors": [],
            "revision": int(scene["revision"]),
            "dirty": scene["dirty"],
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

    def _dirty(self) -> bool | None:
        return self._dirty_observation()

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
        return current_network, current_node

    def _current_network_path(self) -> str:
        return _safe_path(self._current_ui_nodes()[0])

    def _current_node_path(self) -> str:
        return _safe_path(self._current_ui_nodes()[1])

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
            "truncated": {
                "inputs": len(inputs) > 16,
                "outputs": len(outputs) > 16,
                "upstream": bool(queue),
                "controls": control_candidate_count > 8,
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

    def _screenshot_directory(self) -> Path:
        """Return the single project-local screenshot directory."""

        runtime_root = self._project_root / ".runtime"
        cache_root = runtime_root / "cache"
        screenshot_root = cache_root / "screenshots"
        try:
            for directory in (runtime_root, cache_root, screenshot_root):
                if os.path.lexists(directory):
                    if _is_reparse_point(directory) or not directory.is_dir():
                        raise RuntimeError(
                            "The screenshot path is not an ordinary directory"
                        )
                else:
                    directory.mkdir()
                if _is_reparse_point(directory) or not directory.is_dir():
                    raise RuntimeError(
                        "The screenshot path is not an ordinary directory"
                    )
            resolved_screenshot_root = screenshot_root.resolve(strict=True)
            if (
                not _is_within(resolved_screenshot_root, self._project_root)
                or resolved_screenshot_root.parent != cache_root.resolve(strict=True)
                or os.path.normcase(str(resolved_screenshot_root))
                != os.path.normcase(str(self._screenshot_root))
            ):
                raise RuntimeError(
                    "The screenshot path escaped .runtime/cache/screenshots"
                )
        except Exception as exc:
            raise HiaRuntimeError(
                "VIEWPORT_CAPTURE_UNAVAILABLE",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        return resolved_screenshot_root
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
            "VIEWPORT_CAPTURE_UNAVAILABLE",
            "Houdini produced an invalid PNG viewport image",
        )
    width, height = struct.unpack(">II", raw[16:24])
    if width <= 0 or height <= 0:
        raise HiaRuntimeError(
            "VIEWPORT_CAPTURE_UNAVAILABLE",
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
    if ranking_scope:
        result["ranking_scope"] = ranking_scope
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
