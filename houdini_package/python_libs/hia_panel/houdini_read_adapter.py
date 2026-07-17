"""Fail-closed, main-thread-only Houdini reads for Gate B2.

The module deliberately does not import Houdini or Qt.  The Python Panel is
the only boundary allowed to inject the live HOM module.  Tests inject a small
fake with the same read-only surface.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import math
import platform
import re
import secrets
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any


_READ_TOOLS = frozenset({"houdini_scene_info", "houdini_node_type_info"})
_DISABLED_TOOLS = frozenset(
    {"houdini_graph_validate", "houdini_graph_apply", "houdini_graph_verify"}
)
_ALLOWED_TYPES = (
    ("Object", "geo"),
    ("Sop", "box"),
    ("Sop", "transform"),
    ("Sop", "merge"),
    ("Sop", "null"),
)
_LIVE_TYPE_NAMES = {
    ("Object", "geo"): "geo",
    ("Sop", "box"): "box",
    ("Sop", "transform"): "xform",
    ("Sop", "merge"): "merge",
    ("Sop", "null"): "null",
}
_SAFE_PARAMETERS: dict[tuple[str, str], dict[str, tuple[str, int]]] = {
    ("Object", "geo"): {},
    ("Sop", "box"): {"size": ("float", 3), "t": ("float", 3)},
    ("Sop", "transform"): {"t": ("float", 3)},
    ("Sop", "merge"): {},
    ("Sop", "null"): {},
}
_REQUIRED_NODE_EVENT_NAMES = (
    "BeingDeleted",
    "FlagChanged",
    "NameChanged",
    "AppearanceChanged",
    "PositionChanged",
    "InputRewired",
    "ParmTupleChanged",
    "ParmTupleAnimated",
    "ParmTupleChannelChanged",
    "ParmTupleLockChanged",
    "ChildCreated",
    "ChildDeleted",
    "ChildReordered",
    "ChildSwitched",
    "NetworkBoxCreated",
    "NetworkBoxChanged",
    "NetworkBoxDeleted",
    "StickyNoteCreated",
    "StickyNoteChanged",
    "StickyNoteDeleted",
    "IndirectInputCreated",
    "IndirectInputRewired",
    "IndirectInputDeleted",
    "SpareParmTemplatesChanged",
    "CustomDataChanged",
)
_GRAPH_NAME = re.compile(r"^HIA_Graph_[A-Za-z0-9][A-Za-z0-9_]{0,63}$")
_DIGEST = re.compile(r"^[A-Fa-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_OBSERVED_NODES = 4096
_MAX_TEMPLATE_ENTRIES = 4096
_MAX_GRAPH_SUMMARIES = 128
_MAX_GRAPH_NODES = 128
_MAX_GRAPH_CONNECTIONS = 256
_MAX_PARAMETERS = 512
_MAX_INPUTS = 65535
_MAX_OUTPUTS = 64
_MAX_SESSION_ID = 9_007_199_254_740_991
_MAX_BUILD_LENGTH = 128
_MAX_VERSION_LENGTH = 128
_HOUDINI_BUILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


class HoudiniReadAdapterError(RuntimeError):
    """A structured local failure that never includes HOM data or tracebacks."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": [],
        }


class HoudiniReadAdapter:
    """Read a bounded live capability slice without ever mutating the scene."""

    def __init__(
        self,
        hou_module: Any,
        *,
        publisher_id: str | None = None,
        python_version: str | None = None,
        pyside_version: str,
        main_thread_id: int | None = None,
        fingerprint_key: bytes | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        # Construction stores the injected object but intentionally performs no
        # attribute access on it.  This keeps construction safe for test and
        # worker-thread misuse; start() is the first HOM boundary.
        self._hou = hou_module
        self._main_thread_id = (
            threading.get_ident() if main_thread_id is None else int(main_thread_id)
        )
        self._publisher_id = publisher_id or f"hia-panel-{uuid.uuid4().hex}"
        if _IDENTIFIER.fullmatch(self._publisher_id) is None:
            raise ValueError("publisher_id must be a bounded protocol identifier")
        self._python_version = _bounded_plain_text(
            python_version or platform.python_version(),
            _MAX_VERSION_LENGTH,
            "python version",
        )
        self._pyside_version = _bounded_plain_text(
            pyside_version,
            _MAX_VERSION_LENGTH,
            "PySide version",
        )
        key = fingerprint_key or secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 16:
            raise ValueError("fingerprint_key must contain at least 16 bytes")
        self._fingerprint_key = bytes(key)
        self._clock = clock
        self._state_lock = threading.RLock()

        self._started = False
        self._disposed = False
        self._houdini_build = "unknown"
        self._build_valid = False
        self._hip_session_id = f"hip-{uuid.uuid4().hex}"
        self._scene_revision = 0
        self._observer_sequence = 0
        self._session_observer_reliable = False
        self._revision_observer_reliable = False
        self._observer_violation = False
        self._catalog_valid = False
        self._catalog: list[dict[str, Any]] = _unavailable_catalog()
        self._hip_callback_installed = False
        self._lifecycle_events: tuple[Any, ...] = ()
        self._hip_revision_events: tuple[Any, ...] = ()
        self._node_event_types: tuple[Any, ...] = ()
        self._observed_nodes: dict[str, tuple[int, Any]] = {}
        self._last_refresh = 0.0

    @property
    def main_thread_id(self) -> int:
        return self._main_thread_id

    @property
    def publisher_id(self) -> str:
        return self._publisher_id

    def start(self) -> dict[str, Any]:
        """Install read observers and return the first immutable publication."""

        self._assert_main_thread()
        if self._disposed:
            raise HoudiniReadAdapterError(
                "HOUDINI_UNAVAILABLE", "The Houdini read adapter is disposed"
            )
        if self._started:
            return self.refresh()

        self._started = True
        try:
            build = _bounded_plain_text(
                self._hou.applicationVersionString(), _MAX_BUILD_LENGTH, "Houdini build"
            )
            if _HOUDINI_BUILD.fullmatch(build) is None:
                raise ValueError("Houdini build has an unsafe format")
            self._houdini_build = build
            self._build_valid = True
        except Exception:
            self._houdini_build = "unknown"
            self._build_valid = False

        self._session_observer_reliable = self._install_hip_observer()
        self._revision_observer_reliable = self._refresh_node_observers()
        catalog, valid = self._read_catalog()
        self._catalog = catalog
        self._catalog_valid = valid
        self._last_refresh = float(self._clock())
        self._advance_observer_sequence()
        return self.capability_report()

    def refresh(self) -> dict[str, Any]:
        """Reconcile observers and the five-type catalog on the UI main thread."""

        self._assert_main_thread()
        if self._disposed:
            raise HoudiniReadAdapterError(
                "HOUDINI_UNAVAILABLE", "The Houdini read adapter is disposed"
            )
        if not self._started:
            return self.start()

        # An observer callback delivered outside the Houdini UI thread means
        # this adapter can no longer prove that it saw every intervening scene
        # transition.  Do not let a later main-thread refresh silently restore
        # trust; a new Panel adapter/lease is required.
        with self._state_lock:
            if self._observer_violation:
                return self.capability_report()

        old_reliability = (
            self._session_observer_reliable,
            self._revision_observer_reliable,
        )
        old_catalog = self._catalog
        old_catalog_valid = self._catalog_valid

        if not self._hip_callback_installed:
            self._session_observer_reliable = self._install_hip_observer()
        self._revision_observer_reliable = self._refresh_node_observers()
        catalog, valid = self._read_catalog()
        self._catalog = catalog
        self._catalog_valid = valid
        self._last_refresh = float(self._clock())

        if (
            old_reliability
            != (
                self._session_observer_reliable,
                self._revision_observer_reliable,
            )
            or old_catalog_valid != valid
            or old_catalog != catalog
        ):
            self._advance_observer_sequence()
        return self.capability_report()

    def capability_report(self) -> dict[str, Any]:
        """Return only Panel-published fields; Bridge identity is never accepted here."""

        with self._state_lock:
            available = bool(
                self._started
                and not self._disposed
                and self._session_observer_reliable
                and self._revision_observer_reliable
                and self._build_valid
                and self._catalog_valid
            )
            return {
                "available": available,
                "publisher_id": self._publisher_id,
                "houdini_build": self._houdini_build,
                "python_version": self._python_version,
                "pyside_version": self._pyside_version,
                "hip_session_id": self._hip_session_id,
                "hip_fingerprint": self._hip_fingerprint(),
                "scene_revision": self._scene_revision,
                "observer_sequence": self._observer_sequence,
                "session_observer_reliable": self._session_observer_reliable,
                "revision_observer_reliable": self._revision_observer_reliable,
                "catalog": copy.deepcopy(self._catalog),
            }

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        absolute_deadline: float | None = None,
    ) -> dict[str, Any]:
        """Execute one of the two B2 reads, returning a JSON-serializable result."""

        if tool_name in _DISABLED_TOOLS or tool_name not in _READ_TOOLS:
            return _disabled_tool_error(tool_name)
        if not isinstance(arguments, Mapping):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Tool arguments must be a JSON object"
            )
        if threading.get_ident() != self._main_thread_id:
            try:
                return self._error_output(
                    arguments,
                    "MAIN_THREAD_REQUIRED",
                    "Houdini reads are permitted only on the UI main thread",
                )
            except HoudiniReadAdapterError:
                # Invalid arguments cannot form a schema envelope, but an
                # off-main-thread caller must still receive the authoritative
                # thread failure without any HOM access.
                return {
                    "ok": False,
                    "structured_error": {
                        "code": "MAIN_THREAD_REQUIRED",
                        "message": (
                            "Houdini reads are permitted only on the UI main thread"
                        ),
                        "details": [],
                    },
                }

        if absolute_deadline is not None:
            try:
                deadline = float(absolute_deadline)
            except (TypeError, ValueError):
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "The trusted deadline is invalid"
                )
            if not math.isfinite(deadline) or float(self._clock()) >= deadline:
                return self._error_output(
                    arguments,
                    "DEADLINE_EXCEEDED",
                    "The read request deadline has expired",
                )

        try:
            report = self.refresh()
        except HoudiniReadAdapterError as exc:
            return self._error_output(arguments, exc.code, exc.message)
        if not report["available"]:
            return self._error_output(
                arguments,
                "HOUDINI_UNAVAILABLE",
                "Reliable read-only Houdini observation is unavailable",
            )

        session_id = arguments.get("hip_session_id")
        if session_id != self._hip_session_id:
            return self._error_output(
                arguments,
                "HIP_SESSION_MISMATCH",
                "The requested HIP session is no longer current",
            )
        revision = arguments.get("base_scene_revision")
        if revision != self._scene_revision:
            return self._error_output(
                arguments,
                "SCENE_CONFLICT",
                "The requested scene revision is no longer current",
            )

        if tool_name == "houdini_scene_info":
            return self._execute_scene_info(arguments)
        return self._execute_node_type_info(arguments)

    def dispose(self) -> None:
        """Remove only callbacks owned by this adapter; never stop the Bridge."""

        self._assert_main_thread()
        if self._disposed:
            return
        self._remove_node_observers()
        if self._hip_callback_installed:
            try:
                self._hou.hipFile.removeEventCallback(self._on_hip_event)
            except Exception:
                pass
        self._hip_callback_installed = False
        self._started = False
        self._disposed = True
        self._session_observer_reliable = False
        self._revision_observer_reliable = False
        self._advance_observer_sequence()

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise HoudiniReadAdapterError(
                "MAIN_THREAD_REQUIRED",
                "Houdini reads are permitted only on the UI main thread",
            )

    def _advance_observer_sequence(self) -> None:
        with self._state_lock:
            self._observer_sequence += 1

    def _install_hip_observer(self) -> bool:
        self._assert_main_thread()
        try:
            event_namespace = self._hou.hipFileEventType
            lifecycle = (
                getattr(event_namespace, "AfterLoad"),
                getattr(event_namespace, "AfterClear"),
            )
            revision_events = (
                getattr(event_namespace, "AfterSave"),
                getattr(event_namespace, "AfterMerge"),
            )
            if any(value is None for value in lifecycle + revision_events):
                return False
            self._hou.hipFile.addEventCallback(self._on_hip_event)
        except Exception:
            return False
        self._lifecycle_events = lifecycle
        self._hip_revision_events = revision_events
        self._hip_callback_installed = True
        return True

    def _refresh_node_observers(self) -> bool:
        self._assert_main_thread()
        try:
            event_namespace = self._hou.nodeEventType
            event_types = tuple(
                getattr(event_namespace, name) for name in _REQUIRED_NODE_EVENT_NAMES
            )
            if any(value is None for value in event_types):
                return False
            # B2's active live contexts are Object/SOP only.  Observing the
            # bounded /obj subtree avoids touching unrelated DCC contexts.
            root = self._hou.node("/obj")
            if root is None:
                return False
            nodes = self._bounded_node_tree(root)
        except Exception:
            return False

        seen: dict[str, tuple[int, Any]] = {}
        seen_session_ids: set[int] = set()
        previous_by_session_id = {
            session_id: (path, node)
            for path, (session_id, node) in self._observed_nodes.items()
        }
        reliable = True
        for node in nodes:
            try:
                path = str(node.path())
                session_id = _bounded_nonnegative_int(
                    node.sessionId(),
                    _MAX_SESSION_ID,
                )
            except Exception:
                reliable = False
                continue
            if not path or path in seen or session_id in seen_session_ids:
                reliable = False
                continue
            previous = previous_by_session_id.get(session_id)
            if previous is not None:
                seen[path] = (session_id, previous[1])
                seen_session_ids.add(session_id)
                continue
            try:
                node.addEventCallback(event_types, self._on_node_event)
            except Exception:
                reliable = False
                continue
            seen[path] = (session_id, node)
            seen_session_ids.add(session_id)

        for _path, (session_id, node) in tuple(self._observed_nodes.items()):
            if session_id in seen_session_ids:
                continue
            try:
                node.removeEventCallback(self._node_event_types, self._on_node_event)
            except Exception:
                # A deleted node can reject callback removal.  Its parent delete
                # event already advanced the revision, so no live node remains
                # unobserved because of this cleanup failure.
                pass
        self._node_event_types = event_types
        self._observed_nodes = seen
        return reliable and len(seen) == len(nodes)

    def _bounded_node_tree(self, root: Any) -> list[Any]:
        stack = [root]
        nodes: list[Any] = []
        while stack:
            node = stack.pop()
            nodes.append(node)
            if len(nodes) > _MAX_OBSERVED_NODES:
                raise HoudiniReadAdapterError(
                    "HOUDINI_UNAVAILABLE",
                    "The scene exceeds the bounded observer capacity",
                )
            children = tuple(node.children())
            stack.extend(reversed(children))
        return nodes

    def _remove_node_observers(self) -> None:
        self._assert_main_thread()
        for _session_id, node in tuple(self._observed_nodes.values()):
            try:
                node.removeEventCallback(self._node_event_types, self._on_node_event)
            except Exception:
                pass
        self._observed_nodes.clear()

    def _on_hip_event(self, event_type: Any, **event_details: Any) -> None:
        # AfterLoad/AfterClear may include old/new HIP path keywords.  They are
        # deliberately ignored so no user file path enters state or logs.
        del event_details
        if self._disposed:
            return
        if threading.get_ident() != self._main_thread_id:
            with self._state_lock:
                changed = (
                    self._session_observer_reliable
                    or self._revision_observer_reliable
                    or not self._observer_violation
                )
                self._observer_violation = True
                self._session_observer_reliable = False
                self._revision_observer_reliable = False
                if changed:
                    self._observer_sequence += 1
            return
        if any(event_type == value for value in self._lifecycle_events):
            # The old node wrappers may already be invalid after a load/clear;
            # exact callback removal is nevertheless attempted and bounded.
            # Never use remove-all APIs, which could remove user callbacks.
            self._remove_node_observers()
            with self._state_lock:
                self._hip_session_id = f"hip-{uuid.uuid4().hex}"
                self._scene_revision = 0
                self._revision_observer_reliable = False
                self._observer_sequence += 1
        elif any(event_type == value for value in self._hip_revision_events):
            # Save/merge do not replace the HIP session, but they can change
            # observable dirty or scene state.  Advance the read snapshot
            # without ever retaining the old/new file paths passed by HOM.
            with self._state_lock:
                self._scene_revision += 1
                self._observer_sequence += 1

    def _on_node_event(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        if self._disposed:
            return
        if threading.get_ident() != self._main_thread_id:
            with self._state_lock:
                changed = (
                    self._revision_observer_reliable
                    or not self._observer_violation
                )
                self._observer_violation = True
                self._revision_observer_reliable = False
                if changed:
                    self._observer_sequence += 1
            return
        with self._state_lock:
            self._scene_revision += 1
            self._observer_sequence += 1

    def _read_catalog(self) -> tuple[list[dict[str, Any]], bool]:
        self._assert_main_thread()
        try:
            categories = self._hou.nodeTypeCategories()
            if not isinstance(categories, Mapping):
                return _unavailable_catalog(), False
        except Exception:
            return _unavailable_catalog(), False

        records: list[dict[str, Any]] = []
        valid = True
        for context, requested_name in _ALLOWED_TYPES:
            record = self._read_node_type(categories, context, requested_name)
            records.append(record)
            valid = valid and bool(record["available"])
        return records, valid

    def _read_node_type(
        self,
        categories: Mapping[str, Any],
        context: str,
        requested_name: str,
    ) -> dict[str, Any]:
        unavailable = _unavailable_node_type(context, requested_name)
        try:
            category = categories.get(context)
            if category is None:
                return unavailable
            node_types = category.nodeTypes()
            if not isinstance(node_types, Mapping):
                return unavailable
            live_name = _LIVE_TYPE_NAMES[(context, requested_name)]
            node_type = node_types.get(live_name)
            if node_type is None:
                return unavailable
            resolved_name = _bounded_plain_text(
                node_type.name(), 256, "canonical node type"
            )
            if resolved_name != live_name:
                return unavailable
            input_count = _bounded_nonnegative_int(
                node_type.maxNumInputs(), _MAX_INPUTS
            )
            output_count = _bounded_nonnegative_int(
                node_type.maxNumOutputs(), _MAX_OUTPUTS
            )
            templates = self._template_map(node_type.parmTemplateGroup())
            parameters: list[dict[str, Any]] = []
            for name, (expected_type, expected_size) in _SAFE_PARAMETERS[
                (context, requested_name)
            ].items():
                template = templates.get(name)
                if template is None:
                    return unavailable
                parameters.append(
                    self._parameter_record(
                        template,
                        expected_name=name,
                        expected_type=expected_type,
                        expected_size=expected_size,
                    )
                )
            if len(parameters) > _MAX_PARAMETERS:
                return unavailable
        except Exception:
            return unavailable

        return {
            "context": context,
            "requested_name": requested_name,
            "resolved_name": resolved_name,
            "available": True,
            "creatable": False,
            "schema_source": "live_houdini_instance",
            "parameters": parameters,
            "input_count": input_count,
            "output_count": output_count,
        }

    def _template_map(self, group: Any) -> dict[str, Any]:
        entries = list(group.entries())
        templates: dict[str, Any] = {}
        visited = 0
        while entries:
            template = entries.pop(0)
            visited += 1
            if visited > _MAX_TEMPLATE_ENTRIES:
                raise ValueError("Parameter template hierarchy is too large")
            name = str(template.name())
            nested = getattr(template, "parmTemplates", None)
            if callable(nested):
                children = tuple(nested())
                if children:
                    entries[0:0] = list(children)
                    continue
            if name in templates:
                raise ValueError("Parameter template names are not unique")
            templates[name] = template
        return templates

    def _parameter_record(
        self,
        template: Any,
        *,
        expected_name: str,
        expected_type: str,
        expected_size: int,
    ) -> dict[str, Any]:
        if str(template.name()) != expected_name:
            raise ValueError("Parameter name mismatch")
        type_name = _enum_name(template.type())
        if expected_type == "float" and type_name.casefold() != "float":
            raise ValueError("Parameter type mismatch")
        tuple_size = _bounded_positive_int(template.numComponents(), 16)
        if tuple_size != expected_size:
            raise ValueError("Parameter tuple size mismatch")

        label = _bounded_plain_text(template.label(), 256, "parameter label")
        defaults = tuple(template.defaultValue())
        if len(defaults) != tuple_size:
            raise ValueError("Parameter default tuple size mismatch")
        values = [_bounded_float(value) for value in defaults]
        minimum = _bounded_float(template.minValue())
        maximum = _bounded_float(template.maxValue())
        if minimum > maximum:
            raise ValueError("Parameter numeric range is inverted")
        numeric_range = {
            "min_value": minimum,
            "max_value": maximum,
            "min_is_strict": bool(template.minIsStrict()),
            "max_is_strict": bool(template.maxIsStrict()),
        }
        default_value: dict[str, Any]
        value_type: str
        if tuple_size == 1:
            value_type = expected_type
            default_value = {"type": expected_type, "value": values[0]}
        else:
            value_type = "tuple"
            default_value = {
                "type": "tuple",
                "items_type": expected_type,
                "value": values,
            }
        return {
            "name": expected_name,
            "label": label,
            "value_type": value_type,
            "tuple_size": tuple_size,
            "writable": False,
            "allows_expression": False,
            "default_value": default_value,
            "numeric_range": numeric_range,
        }

    def _execute_scene_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        include_summaries = arguments.get("include_graph_summaries")
        if not isinstance(include_summaries, bool):
            return self._error_output(
                arguments,
                "INVALID_ARGUMENT",
                "include_graph_summaries must be a boolean",
            )
        try:
            frame = _bounded_float(self._hou.frame(), -1_000_000, 1_000_000)
            fps = _bounded_float(self._hou.fps(), 0, 1_000, exclusive_minimum=True)
            dirty = self._hou.hipFile.hasUnsavedChanges()
            if not isinstance(dirty, bool):
                raise ValueError("HIP dirty state is not boolean")
            summaries, truncated = self._read_hia_graph_summaries(include_summaries)
        except Exception:
            return self._error_output(
                arguments,
                "HOUDINI_UNAVAILABLE",
                "The bounded scene information could not be read safely",
            )

        output = self._common_output(arguments, ok=True)
        output["result"] = {
            "houdini_build": self._houdini_build,
            "hip_fingerprint": self._hip_fingerprint(),
            "current_frame": frame,
            "fps": fps,
            "dirty": dirty,
            "enabled_contexts": ["Object", "Sop"],
            "hia_graphs": summaries,
            "graph_summaries_truncated": truncated,
        }
        return output

    def _read_hia_graph_summaries(
        self, include_summaries: bool
    ) -> tuple[list[dict[str, Any]], bool]:
        if not include_summaries:
            return [], False
        object_root = self._hou.node("/obj")
        if object_root is None:
            raise ValueError("Object context is unavailable")
        children = tuple(object_root.children())
        summaries: list[dict[str, Any]] = []
        truncated = False
        for child in children:
            name = str(child.name())
            if _GRAPH_NAME.fullmatch(name) is None:
                continue
            if child.userData("hia_ownership") != "hia_owned":
                continue
            digest = child.userData("hia_graph_digest")
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                continue
            graph_nodes = tuple(child.children())
            if not 1 <= len(graph_nodes) <= _MAX_GRAPH_NODES:
                truncated = True
                continue
            connection_count = 0
            for graph_node in graph_nodes:
                connection_count += len(tuple(graph_node.inputConnections()))
                if connection_count > _MAX_GRAPH_CONNECTIONS:
                    truncated = True
                    break
            if connection_count > _MAX_GRAPH_CONNECTIONS:
                continue
            if len(summaries) >= _MAX_GRAPH_SUMMARIES:
                truncated = True
                continue
            summaries.append(
                {
                    "root_path": f"/obj/{name}",
                    "context": "Object",
                    "ownership": "hia_owned",
                    "graph_digest": digest.casefold(),
                    "node_count": len(graph_nodes),
                    "connection_count": connection_count,
                    # Gate B2 never cooks and deliberately does not query
                    # potentially blocking cook/error state.
                    "cook_state": "unknown",
                }
            )
        summaries.sort(key=lambda item: item["root_path"])
        return summaries, truncated

    def _execute_node_type_info(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        requested = arguments.get("node_types")
        if (
            not isinstance(requested, Sequence)
            or isinstance(requested, (str, bytes, bytearray))
            or not 1 <= len(requested) <= 16
        ):
            return self._error_output(
                arguments,
                "INVALID_ARGUMENT",
                "node_types must contain between one and sixteen queries",
            )
        catalog = {
            (item["context"], item["requested_name"]): item for item in self._catalog
        }
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for query in requested:
            if not isinstance(query, Mapping) or set(query) != {"context", "name"}:
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "A node-type query is malformed"
                )
            key = (query.get("context"), query.get("name"))
            if key not in catalog:
                return self._error_output(
                    arguments,
                    "NODE_TYPE_NOT_ALLOWED",
                    "The requested node type is outside the B2 allowlist",
                )
            if key in seen:
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "Node-type queries must be unique"
                )
            seen.add(key)
            results.append(copy.deepcopy(catalog[key]))

        output = self._common_output(arguments, ok=True)
        output["result"] = {"node_types": results}
        return output

    def _common_output(
        self, arguments: Mapping[str, Any], *, ok: bool
    ) -> dict[str, Any]:
        required = (
            "request_id",
            "thread_id",
            "turn_id",
            "hip_session_id",
            "base_scene_revision",
            "idempotency_key",
        )
        if any(name not in arguments for name in required):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Required correlation fields are missing"
            )
        return {
            "ok": ok,
            "request_id": arguments["request_id"],
            "thread_id": arguments["thread_id"],
            "turn_id": arguments["turn_id"],
            "hip_session_id": arguments["hip_session_id"],
            "base_scene_revision": arguments["base_scene_revision"],
            "idempotency_key": arguments["idempotency_key"],
            "scene_revision": self._scene_revision,
            "result": None,
            "warnings": [],
            "structured_error": None,
        }

    def _error_output(
        self,
        arguments: Mapping[str, Any],
        code: str,
        message: str,
    ) -> dict[str, Any]:
        output = self._common_output(arguments, ok=False)
        output["structured_error"] = {
            "code": code,
            "message": message,
            "details": [],
        }
        return output

    def _hip_fingerprint(self) -> str:
        payload = "\x1f".join(
            (
                "hia-b2-safe-hip-fingerprint-v1",
                self._publisher_id,
                self._houdini_build,
                self._hip_session_id,
                str(self._scene_revision),
            )
        ).encode("utf-8")
        return hmac.new(self._fingerprint_key, payload, hashlib.sha256).hexdigest()


def _unavailable_catalog() -> list[dict[str, Any]]:
    return [_unavailable_node_type(context, name) for context, name in _ALLOWED_TYPES]


def _unavailable_node_type(context: str, requested_name: str) -> dict[str, Any]:
    return {
        "context": context,
        "requested_name": requested_name,
        "resolved_name": None,
        "available": False,
        "creatable": False,
        "schema_source": "live_houdini_instance",
        "parameters": [],
        "input_count": 0,
        "output_count": 0,
    }


def _disabled_tool_error(tool_name: Any) -> dict[str, Any]:
    safe_name = str(tool_name)
    if len(safe_name) > 128 or any(ord(character) < 32 for character in safe_name):
        safe_name = "<invalid>"
    return {
        "ok": False,
        "structured_error": {
            "code": "TOOL_NOT_ALLOWED",
            "message": f"Tool {safe_name} is disabled in Gate B2",
            "details": [],
        },
    }


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if callable(name):
        name = name()
    if isinstance(name, str) and name:
        return name.rsplit(".", 1)[-1]
    return str(value).rsplit(".", 1)[-1]


def _bounded_plain_text(value: Any, maximum: int, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field_name} is not a bounded string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} contains control characters")
    return value


def _bounded_nonnegative_int(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError("Integer value is outside the allowed range")
    return value


def _bounded_positive_int(value: Any, maximum: int) -> int:
    result = _bounded_nonnegative_int(value, maximum)
    if result == 0:
        raise ValueError("Integer value must be positive")
    return result


def _bounded_float(
    value: Any,
    minimum: float = -1_000_000_000,
    maximum: float = 1_000_000_000,
    *,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Numeric value has the wrong type")
    result = float(value)
    if not math.isfinite(result) or result > maximum:
        raise ValueError("Numeric value is outside the allowed range")
    if exclusive_minimum and result <= minimum:
        raise ValueError("Numeric value is outside the allowed range")
    if not exclusive_minimum and result < minimum:
        raise ValueError("Numeric value is outside the allowed range")
    return result


__all__ = ["HoudiniReadAdapter", "HoudiniReadAdapterError"]
