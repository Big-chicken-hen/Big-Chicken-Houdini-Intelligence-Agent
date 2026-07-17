"""Focused fake-HOM and strict read facade for Gate B4B acceptance tests.

Only tests call :meth:`FakeHouB4B.manual_undo`; the acceptance controller has
no reference to that helper and therefore cannot automate Undo.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Mapping, Sequence

from fake_hou_write import FakeHouWrite, FakeNode, certified_write_catalog
from hia_core.houdini_contract import canonical_json_sha256


_SILENT_READBACK_OPERATIONS = frozenset(
    {
        "set_user_data:hia_ownership",
        "set_user_data:hia_transaction_id",
        "set_user_data:hia_graph_digest",
    }
)


class _FakeHipFile:
    def __init__(self) -> None:
        self.new_file = True
        self.dirty = False

    def isNewFile(self) -> bool:
        return self.new_file

    def hasUnsavedChanges(self) -> bool:
        return self.dirty


class FakeHouB4B(FakeHouWrite):
    """Blank unsaved HIP facade over the existing generic fake graph HOM."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        obj = self._registry["/obj"]
        obj._children = [child for child in obj._children if child is not self.sentinel]
        self._registry.pop("/obj/User_Sentinel", None)
        self.call_log.clear()
        self.mutation_log.clear()
        self.hipFile = _FakeHipFile()
        self._selection: tuple[FakeNode, ...] = ()
        self._current: FakeNode | None = obj
        self.manual_undo_calls = 0
        self.read_facade: FakeB4BReadFacade | None = None

    def applicationVersionString(self) -> str:
        self._record("application.version", None)
        return "21.0.440"

    def selectedNodes(self, *, include_hidden: bool = False) -> tuple[FakeNode, ...]:
        del include_hidden
        self._record("selected_nodes", None)
        return tuple(self._selection)

    def pwd(self) -> FakeNode | None:
        self._record("pwd", None)
        return self._current

    def set_selection_for_test(self, *nodes: FakeNode) -> None:
        self._selection = tuple(nodes)

    def set_current_for_test(self, node: FakeNode | None) -> None:
        self._current = node

    def _mutate(
        self,
        operation: str,
        path: str,
        callback_source: Any,
        *detail: Any,
    ) -> None:
        super()._mutate(operation, path, callback_source, *detail)
        self.hipFile.dirty = True

    def manual_undo(self) -> None:
        """Model one user-owned Ctrl+Z action outside controller authority."""

        self.manual_undo_calls += 1
        root = self._registry.get("/obj/HIA_Graph_stairs_demo")
        if root is None:
            raise RuntimeError("the fake accepted graph is absent")
        root._destroy_exact()
        self.hipFile.dirty = False
        if self.read_facade is None:
            raise RuntimeError("the fake strict read facade is absent")
        self.read_facade.note_manual_undo()


class FakeB4BReadFacade:
    """Strict event/revision authority shaped like ``HoudiniReadAdapter``."""

    def __init__(
        self,
        hou_module: FakeHouB4B,
        *,
        catalog: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self._hou = hou_module
        self._catalog = copy.deepcopy(list(catalog or certified_write_catalog()))
        self._main_thread_id = threading.get_ident()
        self._started = False
        self._available = True
        self._hip_session_id = "hip-b4b-fake"
        self._scene_revision = 0
        self._observer_sequence = 0
        self._active: dict[str, Any] | None = None
        self._last_evidence: dict[str, Any] | None = None
        self._journal: list[dict[str, Any]] = []
        self._journal_sequence = 0
        self._observed: dict[str, FakeNode] = {}
        self.reject_observer_paths: set[str] = set()
        self.hou_module = hou_module
        hou_module.read_facade = self

    @property
    def strict_event_evidence(self) -> bool:
        return True

    @property
    def main_thread_id(self) -> int:
        return self._main_thread_id

    def start(self) -> dict[str, Any]:
        self._assert_main_thread()
        if not self._started:
            self._started = True
            self._install_observer(self._hou.node("/obj"))
            self._observer_sequence += 1
        return self.capability_report()

    def refresh(self) -> dict[str, Any]:
        self._assert_main_thread()
        if not self._started:
            return self.start()
        return self.capability_report()

    def capability_report(self) -> dict[str, Any]:
        self._assert_main_thread()
        return {
            "available": bool(self._started and self._available and self._active is None),
            "publisher_id": "fake-b4b-panel",
            "houdini_build": "21.0.440",
            "python_version": "3.11.0",
            "pyside_version": "6.0.0",
            "hip_session_id": self._hip_session_id,
            "hip_fingerprint": self._fingerprint(),
            "scene_revision": self._scene_revision,
            "observer_sequence": self._observer_sequence,
            "session_observer_reliable": True,
            "revision_observer_reliable": True,
            "catalog": copy.deepcopy(self._catalog),
        }

    def begin_owned_write(
        self,
        transaction_id: str,
        *,
        expected_hip_session_id: str,
        expected_scene_revision: int,
        expected_hip_fingerprint: str,
    ) -> object:
        self._assert_main_thread()
        if self._active is not None:
            raise RuntimeError("another fake owned write is active")
        report = self.capability_report()
        if (
            report["available"] is not True
            or expected_hip_session_id != report["hip_session_id"]
            or expected_scene_revision != report["scene_revision"]
            or expected_hip_fingerprint != report["hip_fingerprint"]
        ):
            raise RuntimeError("fake owned-write baseline mismatch")
        token = object()
        self._active = {
            "token": token,
            "transaction_id": transaction_id,
            "base_revision": self._scene_revision,
            "base_fingerprint": expected_hip_fingerprint,
            "expectation": None,
            "invalid": False,
            "events": [],
            "mutations": [],
            "observer_installations": [
                {"path": path, "session_id": node.sessionId()}
                for path, node in sorted(self._observed.items())
            ],
        }
        return token

    def begin_owned_mutation(
        self,
        token: object,
        *,
        expected_callback_source: object | None = None,
        operation: str | None = None,
        event_source_rules: Mapping[str, Sequence[object]] | None = None,
        allowed_child_subjects: Sequence[object] | None = None,
        required_event_types: Sequence[str] | None = None,
        allow_zero_events: bool = False,
    ) -> object:
        self._assert_main_thread()
        active = self._require_active(token)
        if active["invalid"] or active["expectation"] is not None:
            raise RuntimeError("fake mutation evidence is unavailable")
        normalized_operation = operation or "owned_mutation"
        if type(allow_zero_events) is not bool or (
            allow_zero_events
            and normalized_operation not in _SILENT_READBACK_OPERATIONS
        ):
            raise RuntimeError("fake zero-event mutation policy is invalid")
        expectation_token = object()
        active["expectation"] = {
            "token": expectation_token,
            "operation": normalized_operation,
            "callback_source": expected_callback_source,
            "rules": {
                str(name): tuple(sources)
                for name, sources in dict(event_source_rules or {}).items()
            },
            "allowed_children": tuple(allowed_child_subjects or ()),
            "required": frozenset(str(name) for name in (required_event_types or ())),
            "seen_types": set(),
            "seen_children": [],
            "count": 0,
            "allow_zero_events": allow_zero_events,
        }
        return expectation_token

    def finish_owned_mutation(
        self,
        token: object,
        expectation_token: object,
        *,
        expected_child_subjects: Sequence[object] | None = None,
        require_all_child_subjects: bool = False,
        exact_readback_proven: bool = False,
    ) -> int:
        self._assert_main_thread()
        active = self._require_active(token)
        expectation = active["expectation"]
        if expectation is None or expectation_token is not expectation["token"]:
            raise RuntimeError("fake mutation expectation identity mismatch")
        active["expectation"] = None
        expected_children = tuple(expected_child_subjects or ())
        children_ok = all(
            any(seen is expected for seen in expectation["seen_children"])
            for expected in expected_children
        )
        if require_all_child_subjects:
            children_ok = children_ok and len(expectation["seen_children"]) == len(
                expected_children
            )
        allow_zero_events = expectation["allow_zero_events"] is True
        evidence_ok = bool(
            (expectation["count"] > 0 or allow_zero_events)
            and (not allow_zero_events or exact_readback_proven is True)
            and expectation["required"].issubset(expectation["seen_types"])
            and children_ok
            and not active["invalid"]
        )
        mutation_record = {
            "operation": expectation["operation"],
            "event_count": expectation["count"],
            "event_types": sorted(expectation["seen_types"]),
            "no_op": False,
        }
        if allow_zero_events:
            mutation_record["exact_readback_proven"] = exact_readback_proven
        active["mutations"].append(mutation_record)
        if not evidence_ok:
            active["invalid"] = True
            raise RuntimeError("fake strict mutation evidence is incomplete")
        return expectation["count"]

    def install_owned_node_observer(self, token: object, node: object) -> dict[str, Any]:
        self._assert_main_thread()
        active = self._require_active(token)
        if active["invalid"] or active["expectation"] is not None:
            raise RuntimeError("fake observer installation is unsafe")
        if not isinstance(node, FakeNode) or node.path() in self.reject_observer_paths:
            active["invalid"] = True
            raise RuntimeError("fake observer installation failed")
        self._install_observer(node)
        identity = {"path": node.path(), "session_id": node.sessionId()}
        active["observer_installations"].append(copy.deepcopy(identity))
        return identity

    def record_owned_noop(self, token: object, *, operation: str) -> None:
        self._assert_main_thread()
        active = self._require_active(token)
        if active["invalid"] or active["expectation"] is not None:
            raise RuntimeError("fake no-op evidence is unsafe")
        active["mutations"].append(
            {
                "operation": str(operation),
                "event_count": 0,
                "event_types": [],
                "no_op": True,
            }
        )

    def finish_owned_write(self, token: object, *, outcome: str) -> dict[str, Any]:
        self._assert_main_thread()
        active = self._require_active(token)
        if active["expectation"] is not None or active["invalid"]:
            self._active = None
            self._available = False
            raise RuntimeError("fake owned-write evidence is unsafe")
        if outcome == "rolled_back":
            self._scene_revision = active["base_revision"]
        else:
            self._scene_revision = active["base_revision"] + 1
        self._observer_sequence += 1
        self._last_evidence = {
            "transaction_id": active["transaction_id"],
            "outcome": outcome,
            "event_count": len(active["events"]),
            "events": copy.deepcopy(active["events"]),
            "mutations": copy.deepcopy(active["mutations"]),
            "observer_installations": copy.deepcopy(
                active["observer_installations"]
            ),
        }
        self._active = None
        return self.capability_report()

    def event_journal_snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._journal))

    def last_owned_evidence(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._last_evidence)

    def note_manual_undo(self) -> None:
        self._assert_main_thread()
        if self._active is not None:
            raise RuntimeError("manual Undo cannot overlap an owned write")
        self._scene_revision += 1
        self._observer_sequence += 1

    def drift_revision_for_test(self) -> None:
        self._scene_revision += 1
        self._observer_sequence += 1

    def _install_observer(self, node: FakeNode | None) -> None:
        if node is None:
            raise RuntimeError("fake observer node is absent")
        path = node.path()
        if path in self.reject_observer_paths:
            raise RuntimeError("fake observer registration rejected")
        event_types = tuple(vars(self._hou.nodeEventType).values())
        node.addEventCallback(event_types, self._on_node_event)
        if not any(
            callback == self._on_node_event
            for _events, callback in node.eventCallbacks()
        ):
            raise RuntimeError("fake observer readback mismatch")
        self._observed[path] = node

    def _on_node_event(self, *args: Any, **kwargs: Any) -> None:
        del args
        source = kwargs.get("node")
        event_type = kwargs.get("event_type")
        child = kwargs.get("child_node")
        event_name = getattr(event_type, "name", "unknown")
        active = self._active
        expectation = None if active is None else active["expectation"]
        operation = "manual_undo" if expectation is None else expectation["operation"]
        matched = active is None
        if expectation is not None:
            rules = expectation["rules"]
            if rules:
                matched = any(
                    source is candidate for candidate in rules.get(event_name, ())
                )
            else:
                matched = source is expectation["callback_source"]
            allowed = expectation["allowed_children"]
            if matched and event_name in {"ChildCreated", "ChildDeleted", "ChildSwitched"}:
                matched = child is not None and (
                    not allowed or any(child is candidate for candidate in allowed)
                )
        record = self._append_event(
            operation=operation,
            event_type=event_name,
            source=source,
            child=child,
            matched=matched,
        )
        if active is None:
            return
        active["events"].append(copy.deepcopy(record))
        if expectation is None or not matched:
            active["invalid"] = True
            return
        expectation["count"] += 1
        expectation["seen_types"].add(event_name)
        if child is not None:
            expectation["seen_children"].append(child)

    def _append_event(
        self,
        *,
        operation: str,
        event_type: str,
        source: Any,
        child: Any,
        matched: bool,
    ) -> dict[str, Any]:
        if len(self._journal) >= 512:
            self._available = False
            if self._active is not None:
                self._active["invalid"] = True
            raise RuntimeError("fake strict event journal exceeded its bound")
        self._journal_sequence += 1
        record = {
            "sequence": self._journal_sequence,
            "operation": operation,
            "event_type": event_type,
            "source_path": source.path() if isinstance(source, FakeNode) else None,
            "source_session_id": (
                source.sessionId() if isinstance(source, FakeNode) else None
            ),
            "child_path": child.path() if isinstance(child, FakeNode) else None,
            "child_session_id": (
                child.sessionId() if isinstance(child, FakeNode) else None
            ),
            "main_thread": threading.get_ident() == self._main_thread_id,
            "matched": bool(matched),
        }
        self._journal.append(record)
        return record

    def _require_active(self, token: object) -> dict[str, Any]:
        active = self._active
        if active is None or token is not active["token"]:
            raise RuntimeError("fake owned-write token mismatch")
        return active

    def _fingerprint(self) -> str:
        obj = self._hou.node("/obj")
        children = [] if obj is None else [
            {
                "path": child.path(),
                "session_id": child.sessionId(),
                "type": child.type().name(),
            }
            for child in obj.children()
        ]
        return canonical_json_sha256(
            {
                "profile": "fake-b4b-strict-read-v1",
                "hip_session_id": self._hip_session_id,
                "scene_revision": self._scene_revision,
                "children": children,
            }
        )

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("fake strict read occurred off main thread")


__all__ = ["FakeB4BReadFacade", "FakeHouB4B"]
