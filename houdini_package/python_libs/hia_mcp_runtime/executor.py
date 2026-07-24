"""Live Houdini handlers marshalled through the UI main thread.

The runtime contains no language model, planner, asset semantics, or node-type
allowlist.  It exposes bounded observations and one general HOM execution
primitive so Codex can operate the current Houdini session directly.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import struct
import threading
import time
import traceback
import uuid
import warnings as python_warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .knowledge_index import (
    LocalKnowledgeIndex,
    SOURCE_GROUPS,
)


MAX_SCRIPT_CHARS = 524_288
MAX_FLIPBOOK_FRAME_SPAN = 240.0
MAX_BATCH_QUERIES = 16
MAX_TEXT_CHARS = 65_536
MAX_SNAPSHOT_NODES = 10_000
MAX_SNAPSHOTS = 16
MAX_TARGETED_DIFF_PATHS = 128
FOCUS_STATE_MAX_BYTES = 1_048_576
STAGE_CHECKPOINT_MARKER = ".hia-stage-checkpoint.json"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
_NODE_DIGEST_UNAVAILABLE = object()

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
        self._knowledge_index: LocalKnowledgeIndex | None = None
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
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise HiaRuntimeError("TOOL_NOT_FOUND", "Unknown HIA MCP V2 runtime tool", {"tool": tool_name})

        dispatch_requested = time.monotonic()
        ui_started = dispatch_requested
        ui_finished = dispatch_requested

        def run() -> dict[str, Any]:
            nonlocal ui_started, ui_finished
            ui_started = time.monotonic()
            try:
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
            finally:
                ui_finished = time.monotonic()

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
        returned = time.monotonic()
        if tool_name == "hia_execute_hom":
            result["phase_timings"].update(
                {
                    "runtime_ui_queue_seconds": _seconds(ui_started - dispatch_requested),
                    "runtime_ui_main_thread_seconds": _seconds(ui_finished - ui_started),
                    "runtime_ui_return_seconds": _seconds(returned - ui_finished),
                }
            )
        return result

    def _context(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        current_network, current_node = self._current_ui_nodes()
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
            "selection": [_safe_path(node) for node in _safe_call(self._hou, "selectedNodes", ())],
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
        return self._success(result)

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
            parameters = self._parameter_templates(node_type, parameter_query)
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
        paths = list(arguments.get("paths") or [])
        root_path = str(arguments.get("root_path", ""))
        if root_path:
            root = self._hou.node(root_path)
            if root is None:
                raise HiaRuntimeError("NODE_NOT_FOUND", "Validation root does not exist", {"path": root_path})
            paths.extend(_safe_path(node) for node in [root, *_safe_call(root, "allSubChildren", ())])
        if not paths:
            paths = [_safe_path(node) for node in _safe_call(self._hou, "selectedNodes", ())]
        expected = [str(value) for value in arguments.get("expected_paths", [])]
        missing_expected = [path for path in expected if self._hou.node(path) is None]
        findings = []
        for path in paths[:limit]:
            node = self._hou.node(path)
            if node is None:
                findings.append({"path": path, "severity": "error", "code": "NODE_NOT_FOUND", "message": "Node does not exist"})
                continue
            if bool(arguments.get("cook", False)):
                try:
                    node.cook(force=False)
                except Exception as exc:
                    findings.append(
                        {"path": path, "severity": "error", "code": "COOK_FAILED", "message": _bounded_text(_redact_text(str(exc)), 2048)}
                    )
            errors = list(_safe_call(node, "errors", ()))
            node_warnings = list(_safe_call(node, "warnings", ()))
            for message in errors:
                findings.append({"path": path, "severity": "error", "code": "NODE_ERROR", "message": str(message)})
            for message in node_warnings:
                findings.append({"path": path, "severity": "warning", "code": "NODE_WARNING", "message": str(message)})
            minimum_inputs = _safe_call(_safe_call(node, "type", None), "minNumInputs", 0)
            inputs = list(_safe_call(node, "inputs", ()))
            if isinstance(minimum_inputs, int) and minimum_inputs > sum(value is not None for value in inputs):
                findings.append(
                    {"path": path, "severity": "error", "code": "MISSING_INPUT", "message": "Required input slots are unconnected"}
                )
        if query:
            findings = [item for item in findings if query in json.dumps(item, ensure_ascii=False).casefold()]
        counts = {
            "errors": sum(item["severity"] == "error" for item in findings),
            "warnings": sum(item["severity"] == "warning" for item in findings),
        }
        return self._success(
            {
                "valid": counts["errors"] == 0 and not missing_expected,
                "findings": findings[:limit],
                "finding_total": len(findings),
                "missing_expected_paths": missing_expected,
                "counts": counts,
                "cooked": bool(arguments.get("cook", False)),
            }
        )

    def _execute_hom(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        execute_started = time.monotonic()
        script = arguments.get("script")
        if not isinstance(script, str) or not script.strip():
            raise HiaRuntimeError("INVALID_ARGUMENTS", "script must be a non-empty string")
        if len(script) > MAX_SCRIPT_CHARS:
            raise HiaRuntimeError("REQUEST_TOO_LARGE", "The HOM script exceeds the character limit", {"limit": MAX_SCRIPT_CHARS})
        try:
            timeout_seconds = float(arguments.get("timeout_seconds", 60.0))
        except (TypeError, ValueError) as exc:
            raise HiaRuntimeError("INVALID_ARGUMENTS", "timeout_seconds must be a number") from exc
        if not 1 <= timeout_seconds <= 300:
            raise HiaRuntimeError("INVALID_ARGUMENTS", "timeout_seconds must be between 1 and 300")

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

        pre_diff_started = time.monotonic()
        targeted_before: dict[str, str | None | object] = {}
        marker_only_baselines: set[str] = set()
        targeted_truncated = False

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
            before_nodes, before_truncated = self._snapshot_map(diff_root)
        elif capture_diff:
            for value in requested_diff_paths:
                if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "diff_paths must contain absolute Houdini node paths",
                    )
                add_targeted_baseline(value)
        pre_diff_seconds = time.monotonic() - pre_diff_started

        dirty_before = self._dirty()
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
        hom_started = time.monotonic()
        failure: dict[str, Any] | None = None
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), python_warnings.catch_warnings(record=True) as caught:
                python_warnings.simplefilter("always")
                exec(compile(script, "<hia_execute_hom>", "exec"), namespace, namespace)
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

        post_diff_started = time.monotonic()
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
        post_diff_seconds = time.monotonic() - post_diff_started

        verified_diff_paths = (
            {
                str(path)
                for key in ("created", "deleted", "changed")
                for path in diff.get(key, [])
            }
            if isinstance(diff, Mapping)
            else set()
        )
        changed_paths = sorted(verified_diff_paths)[:MAX_TARGETED_DIFF_PATHS]
        dirty_after_script = self._dirty()
        observed_change = bool(verified_diff_paths) or dirty_before != dirty_after_script
        unverified_paths = (
            set(str(path) for path in diff.get("unverified_paths", []))
            if isinstance(diff, Mapping)
            else set()
        )
        if observed_change:
            scene_change_status = "changed"
        elif failure is not None or unverified_paths or marked or explicitly_changed or not capture_diff:
            scene_change_status = "unknown"
        else:
            scene_change_status = "unchanged"
        if observed_change:
            with self._state_lock:
                self._scene_revision += 1

        checkpoint_started = time.monotonic()
        focus_target = self._goal_focus_target() if checkpoint_label else None
        checkpoint: dict[str, Any] = {
            "requested": bool(checkpoint_label),
            "label": checkpoint_label or None,
            "created": False,
            "path": None,
            "error": None,
        }
        if checkpoint_label:
            if failure is not None:
                checkpoint["skipped_reason"] = "HOM_EXECUTION_FAILED"
            elif not observed_change:
                checkpoint["skipped_reason"] = "NO_CONFIRMED_SCENE_CHANGE"
            elif focus_target is None:
                checkpoint["skipped_reason"] = "FOCUS_MODE_DISABLED"
            else:
                try:
                    checkpoint_directory = self._checkpoint_directory()
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
                        returned_path = self._hou.hipFile.saveAsBackup()
                        if not isinstance(returned_path, str) or not returned_path.strip():
                            raise RuntimeError("Houdini did not return the backup path")
                        candidate = Path(returned_path.strip())
                        if not candidate.is_absolute():
                            candidate = checkpoint_directory / candidate
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
                        focus_thread_id, goal_binding = focus_target
                        self._write_stage_checkpoint_marker(
                            checkpoint_directory,
                            checkpoint_path,
                            focus_thread_id,
                            goal_binding,
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
        checkpoint_seconds = time.monotonic() - checkpoint_started

        normalization_started = time.monotonic()
        dirty_after = self._dirty()
        redacted_stdout = _bounded_text(_redact_text(stdout.getvalue()), MAX_TEXT_CHARS)
        redacted_warnings = [_bounded_text(_redact_text(value), 4096) for value in warning_records[:100]]
        errors = [failure] if failure is not None else []
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
        normalization_seconds = time.monotonic() - normalization_started
        result["phase_timings"] = {
            "runtime_pre_diff_seconds": _seconds(pre_diff_seconds),
            "runtime_hom_seconds": _seconds(hom_seconds),
            "runtime_post_diff_seconds": _seconds(post_diff_seconds),
            "runtime_checkpoint_seconds": _seconds(checkpoint_seconds),
            "runtime_result_normalization_seconds": _seconds(normalization_seconds),
            "runtime_execute_total_seconds": _seconds(time.monotonic() - execute_started),
        }
        return result

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
        current, truncated = self._snapshot_map(snapshot.root_path)
        diff = self._diff_maps(snapshot.nodes, current)
        for key in ("created", "deleted", "changed"):
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
        width = _bounded_int(arguments.get("width", 640), 64, 4096)
        height = _bounded_int(arguments.get("height", 360), 64, 4096)
        validated_frame_range: tuple[float, float] | None = None
        if mode == "flipbook":
            raw_frame_range = arguments.get("frame_range")
            if raw_frame_range is None:
                current_frame = float(_safe_call(self._hou, "frame", 1.0))
                validated_frame_range = (current_frame, current_frame)
            else:
                if (
                    not isinstance(raw_frame_range, (list, tuple))
                    or len(raw_frame_range) != 2
                ):
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Flipbook frame_range must contain exactly two frame numbers",
                    )
                try:
                    start = float(raw_frame_range[0])
                    end = float(raw_frame_range[1])
                except (TypeError, ValueError) as exc:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Flipbook frame_range values must be finite numbers",
                    ) from exc
                if not math.isfinite(start) or not math.isfinite(end):
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Flipbook frame_range values must be finite numbers",
                    )
                if end < start:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        "Flipbook frame_range end must not precede its start",
                    )
                if end - start > MAX_FLIPBOOK_FRAME_SPAN:
                    raise HiaRuntimeError(
                        "INVALID_ARGUMENTS",
                        f"Flipbook frame_range may span at most {int(MAX_FLIPBOOK_FRAME_SPAN)} frames",
                        {
                            "frame_range": [start, end],
                            "maximum_frame_span": int(MAX_FLIPBOOK_FRAME_SPAN),
                        },
                    )
                validated_frame_range = (start, end)
        desktop = self._hou.ui.curDesktop()
        scene_viewer = desktop.paneTabOfType(self._hou.paneTabType.SceneViewer)
        if scene_viewer is None:
            raise HiaRuntimeError("VIEWPORT_UNAVAILABLE", "No Scene Viewer pane is available")
        viewport = scene_viewer.curViewport()
        camera_path = str(arguments.get("camera_path", ""))
        camera = None
        if camera_path:
            camera = self._hou.node(camera_path)
            if camera is None:
                raise HiaRuntimeError("NODE_NOT_FOUND", "The viewport camera does not exist", {"path": camera_path})
        capture_dir = self._screenshot_root
        capture_dir.mkdir(parents=True, exist_ok=True)
        if not _is_within(capture_dir, self._cache_root):
            raise HiaRuntimeError("PATH_OUTSIDE_CACHE", "Viewport output must stay under HIA_CACHE_DIR")
        identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        frame = int(round(float(_safe_call(self._hou, "frame", 1.0))))
        output_path = capture_dir / f"viewport-{identifier}-{frame:04d}.png"
        original_camera = viewport.camera() if camera is not None else None
        saved_default_camera = None
        original_camera_lock = False
        if camera is not None:
            if original_camera is None:
                saved_default_camera = viewport.defaultCamera().stash()
            original_camera_lock = bool(viewport.isCameraLockedToView())
        original_frame = self._hou.frame() if mode != "viewport" else None
        capture_error: Exception | None = None
        restore_errors: list[dict[str, str]] = []
        try:
            if camera is not None:
                viewport.lockCameraToView(False)
                viewport.setCamera(camera)
            if mode == "viewport" and callable(getattr(viewport, "saveViewToImage", None)):
                viewport.saveViewToImage(str(output_path))
            else:
                settings = scene_viewer.flipbookSettings().stash()
                assert validated_frame_range is not None
                start, end = validated_frame_range
                pattern = capture_dir / f"flipbook-{identifier}-$F4.png"
                settings.frameRange((start, end))
                settings.output(str(pattern))
                settings.resolution((width, height))
                settings.useResolution(True)
                settings.outputZoom(100)
                settings.useSheetSize(False)
                settings.outputToMPlay(False)
                scene_viewer.flipbook(viewport, settings, open_dialog=False)
                output_path = capture_dir / f"flipbook-{identifier}-{int(round(start)):04d}.png"
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
            if original_frame is not None:
                restore("restore_frame", lambda: self._hou.setFrame(original_frame))
        if capture_error is not None:
            details = {"restore_errors": restore_errors} if restore_errors else None
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
                {"relative_path": output_path.relative_to(self._project_root).as_posix()},
            )
        with output_path.open("rb") as stream:
            actual_width, actual_height = _png_dimensions(stream.read(24))
        relative = output_path.relative_to(self._project_root).as_posix()
        result: dict[str, Any] = {
            "ok": True,
            "result": {
                "path": relative,
                "mode": mode,
                "width": actual_width,
                "height": actual_height,
            },
            "warnings": [],
            "errors": [],
            "revision": self.scene_revision,
            "dirty": self._dirty(),
        }
        if bool(arguments.get("return_image", True)):
            if output_path.stat().st_size <= 3_000_000:
                raw = output_path.read_bytes()
                result["image"] = {"mime_type": "image/png", "data_base64": base64.b64encode(raw).decode("ascii")}
            else:
                result["warnings"].append("Image exceeded inline MCP size; returning the project runtime path only")
        return result

    def _dispatch_local_help(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        queries, is_batch = _query_values(
            arguments,
            minimum_length=2,
            maximum_length=256,
        )
        raw_sources = arguments.get("sources")
        if raw_sources is None:
            sources = set(SOURCE_GROUPS)
        elif not isinstance(raw_sources, (list, tuple, set)):
            raise HiaRuntimeError(
                "INVALID_ARGUMENTS",
                "sources must be an array",
            )
        elif not raw_sources:
            sources = set(SOURCE_GROUPS)
        else:
            sources = {str(value) for value in raw_sources}
        invalid_sources = sources.difference(SOURCE_GROUPS)
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
        offset = _bounded_int(arguments.get("offset", 0), 0, 1_000_000)
        limit = _bounded_int(arguments.get("limit", 10), 1, 50)
        try:
            knowledge_index = self._local_knowledge_index()
            refresh_groups = knowledge_index.refresh_due(
                sources,
                force=refresh_requested,
            )
            indexed_houdini_version = (
                knowledge_index.active_houdini_version()
                if "houdini" in sources
                else ""
            )
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
                if "houdini" in sources:
                    houdini_help_root = str(self._hou.expandString("$HH/help"))
                    if (
                        "houdini" in refresh_groups
                        or indexed_houdini_version != houdini_version
                    ):
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
        if (
            "houdini" in sources
            and indexed_houdini_version != current_houdini_version
        ):
            refresh_groups.add("houdini")
        refresh_started = time.monotonic()
        try:
            refresh_stats, index_warnings = knowledge_index.refresh(
                refresh_groups,
                snapshot,
                force=refresh_requested,
            )
            refresh_finished = time.monotonic()
            query_results = []
            for query in queries:
                search_result = knowledge_index.search(
                    query,
                    sources,
                    current_houdini_version=current_houdini_version,
                    offset=offset,
                    limit=limit,
                )
                query_results.append(
                    {
                        "query": query,
                        "matches": search_result["matches"],
                        "total": search_result["total"],
                        "offset": offset,
                        "limit": limit,
                        "files_scanned": refresh_stats["files_scanned"],
                        "web_searched": False,
                        "index": {
                            **refresh_stats,
                            "database": knowledge_index.relative_database_path,
                            "fts": "FTS5",
                            "tokenizer": search_result["tokenizer"],
                        },
                    }
                )
        except HiaRuntimeError:
            raise
        except Exception as exc:
            raise HiaRuntimeError(
                "LOCAL_HELP_SEARCH_FAILED",
                _bounded_text(_redact_text(str(exc)), 2048),
            ) from exc
        if not is_batch:
            result_payload: dict[str, Any] = query_results[0]
        else:
            merged: list[dict[str, Any]] = []
            merged_index: dict[tuple[str, str], dict[str, Any]] = {}
            for query_result in query_results:
                query = str(query_result["query"])
                for match in query_result["matches"]:
                    key = (
                        str(match.get("source", "")),
                        str(match.get("title", "")),
                    )
                    existing = merged_index.get(key)
                    if existing is None:
                        existing = dict(match)
                        existing["matched_queries"] = [query]
                        merged_index[key] = existing
                        merged.append(existing)
                    else:
                        existing["matched_queries"].append(query)
            result_payload = {
                "queries": query_results,
                "query_count": len(query_results),
                "matches": merged,
                "total": len(merged),
                "files_scanned": refresh_stats["files_scanned"],
                "web_searched": False,
                "index": {
                    **refresh_stats,
                    "database": knowledge_index.relative_database_path,
                    "fts": "FTS5",
                    "tokenizer": query_results[0]["index"]["tokenizer"],
                },
            }
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

    def _local_knowledge_index(self) -> LocalKnowledgeIndex:
        with self._state_lock:
            if self._knowledge_index is None:
                self._knowledge_index = LocalKnowledgeIndex(self._project_root)
            return self._knowledge_index

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

    def _dirty(self) -> bool:
        return bool(_safe_call(self._hou.hipFile, "hasUnsavedChanges", False))

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
        return record

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

    def _parameter_templates(self, node_type: Any, query: str) -> list[dict[str, Any]]:
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

    def _geometry_record(self, node: Any, *, include_attributes: bool, sample_limit: int) -> dict[str, Any]:
        base = {"node_path": _safe_path(node), "available": False, "errors": list(_safe_call(node, "errors", ()))}
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

    def _checkpoint_directory(self) -> Path:
        raw_path = os.environ.get("HOUDINI_BACKUP_DIR", "").strip()
        if not raw_path:
            raise RuntimeError("HOUDINI_BACKUP_DIR is not configured")
        configured = Path(raw_path)
        if not configured.is_absolute():
            raise RuntimeError("HOUDINI_BACKUP_DIR must be absolute")
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
        checkpoint_directory: Path,
        checkpoint_path: Path,
        thread_id: str,
        goal_binding: str,
    ) -> None:
        marker = checkpoint_directory / STAGE_CHECKPOINT_MARKER
        temporary = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "version": 1,
            "thread_id": thread_id,
            "goal_binding": goal_binding,
            "checkpoint_file": checkpoint_path.name,
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, marker)

    def _node_digest_value(self, node: Any) -> str:
        payload = {
            "type": self._type_record(node),
            "inputs": [
                _safe_path(value) if value is not None else None
                for value in _safe_call(node, "inputs", ())
            ],
            "flags": {
                "display": bool(_safe_call(node, "isDisplayFlagSet", False)),
                "render": bool(_safe_call(node, "isRenderFlagSet", False)),
                "bypassed": bool(_safe_call(node, "isBypassed", False)),
            },
            "parameters": [
                (str(_safe_call(parm, "name", "")), _safe_unexpanded_string(parm))
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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
