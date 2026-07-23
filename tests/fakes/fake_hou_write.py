"""Minimal HOM-shaped write fixture for Gate B4A offline adapter tests.

The fixture deliberately models only the small surface used by the dormant
write adapter.  It never imports :mod:`hou`, Qt, filesystem, network, or
process APIs.  Its five catalog entries are the first certified test sample,
not a type switch in the transaction engine: every entry follows the same
``createNode``/parameter/connection/observation path.
"""

from __future__ import annotations

import copy
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping


MUTATION_PHASES = (
    "create_root",
    "create_nodes",
    "set_parameters",
    "connect_nodes",
    "set_flags_layout",
    "postcondition",
    "commit",
)

UNDO_FAILURE_POINTS = ("group", "enter", "enter_after_active", "exit")


# Test data only.  The writer consumes the injected catalog generically and
# must not import or reproduce this five-entry sample as a production allowlist.
CERTIFIED_WRITE_CATALOG: list[dict[str, Any]] = [
    {
        "context": "Object",
        "category": "Object",
        "requested_name": "geo",
        "resolved_name": "geo",
        "available": True,
        "creatable": True,
        "input_count": 0,
        "output_count": 1,
        "parameters": [],
        "risk_level": "ordinary_graph_write",
    },
    {
        "context": "Sop",
        "category": "Sop",
        "requested_name": "box",
        "resolved_name": "box",
        "available": True,
        "creatable": True,
        "input_count": 0,
        "output_count": 1,
        "parameters": [
            {
                "name": "size",
                "value_type": "tuple",
                "tuple_size": 3,
                "items_type": "float",
                "writable": True,
                "allows_expression": False,
                "default": [1.0, 1.0, 1.0],
            },
            {
                "name": "t",
                "value_type": "tuple",
                "tuple_size": 3,
                "items_type": "float",
                "writable": True,
                "allows_expression": False,
                "default": [0.0, 0.0, 0.0],
            },
        ],
        "risk_level": "ordinary_graph_write",
    },
    {
        "context": "Sop",
        "category": "Sop",
        "requested_name": "transform",
        "resolved_name": "xform",
        "available": True,
        "creatable": True,
        "input_count": 1,
        "output_count": 1,
        "parameters": [
            {
                "name": "t",
                "value_type": "tuple",
                "tuple_size": 3,
                "items_type": "float",
                "writable": True,
                "allows_expression": False,
                "default": [0.0, 0.0, 0.0],
            }
        ],
        "risk_level": "ordinary_graph_write",
    },
    {
        "context": "Sop",
        "category": "Sop",
        "requested_name": "merge",
        "resolved_name": "merge",
        "available": True,
        "creatable": True,
        "input_count": 9999,
        "output_count": 1,
        "parameters": [],
        "risk_level": "ordinary_graph_write",
    },
    {
        "context": "Sop",
        "category": "Sop",
        "requested_name": "null",
        "resolved_name": "null",
        "available": True,
        "creatable": True,
        "input_count": 1,
        "output_count": 1,
        "parameters": [],
        "risk_level": "ordinary_graph_write",
    },
]


def _parameter_specs(entry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(parameter["name"]): {
            **dict(parameter),
            "tuple_length": int(parameter.get("tuple_size", 1)),
            "type": parameter.get("items_type") or parameter.get("value_type"),
        }
        for parameter in entry.get("parameters", ())
    }


class InjectedHouFailure(RuntimeError):
    """Deterministic fake ``Exception`` raised at one transaction phase."""

    def __init__(self, phase: str) -> None:
        super().__init__(f"injected fake HOM failure at {phase}")
        self.phase = phase


class InjectedHouBaseException(BaseException):
    """Deterministic non-``Exception`` for containment tests."""

    def __init__(self, phase: str) -> None:
        super().__init__(f"injected fake HOM BaseException at {phase}")
        self.phase = phase


@dataclass(frozen=True)
class FakeHouCall:
    name: str
    path: str | None
    thread_id: int


@dataclass(frozen=True)
class FakeMutation:
    operation: str
    path: str
    callback_source: Any
    detail: tuple[Any, ...]
    thread_id: int


@dataclass(frozen=True)
class FakeNodeEventType:
    """Identity-stable HOM-like node-event enum used by strict B4B tests."""

    name: str


class FakeNodeType:
    def __init__(self, resolved_name: str) -> None:
        self._resolved_name = resolved_name

    def name(self) -> str:
        return self._resolved_name


class FakeConnection:
    def __init__(
        self,
        source: "FakeNode",
        destination: "FakeNode",
        output_index: int,
        input_index: int,
    ) -> None:
        self._source = source
        self._destination = destination
        self._output_index = output_index
        self._input_index = input_index

    def inputNode(self) -> "FakeNode":
        return self._source

    def outputNode(self) -> "FakeNode":
        return self._destination

    def outputIndex(self) -> int:
        return self._output_index

    def inputIndex(self) -> int:
        return self._input_index


class FakeParm:
    def __init__(self, node: "FakeNode", name: str, specification: Mapping[str, Any]) -> None:
        self._node = node
        self._name = name
        self._specification = specification

    def set(self, value: Any) -> None:
        self._node._set_parameter(self._name, value, tuple_access=False)

    def eval(self) -> Any:
        self._node._owner._record("parm.eval", self._node.path())
        value = self._node._parameter_values[self._name]
        if isinstance(value, tuple) and len(value) == 1:
            return value[0]
        return copy.deepcopy(value)

    def expression(self) -> str:
        raise RuntimeError("fake parameters never contain expressions")


class FakeParmTuple(FakeParm):
    def set(self, value: Any) -> None:
        self._node._set_parameter(self._name, value, tuple_access=True)

    def eval(self) -> tuple[Any, ...]:
        self._node._owner._record("parm_tuple.eval", self._node.path())
        value = self._node._parameter_values[self._name]
        return tuple(value) if isinstance(value, tuple) else (value,)


class FakeNode:
    """Small generic node object; behavior comes entirely from its catalog entry."""

    def __init__(
        self,
        owner: "FakeHouWrite",
        parent: "FakeNode | None",
        name: str,
        path: str,
        resolved_type: str,
        catalog_entry: Mapping[str, Any] | None,
    ) -> None:
        self._owner = owner
        self._parent = parent
        self._name = name
        self._path = path
        self._resolved_type = resolved_type
        self._catalog_entry = copy.deepcopy(dict(catalog_entry or {}))
        self._session_id = owner._next_session_id()
        self._children: list[FakeNode] = []
        self._inputs: dict[int, FakeConnection] = {}
        self._display = False
        self._render = False
        self._user_data: dict[str, str] = {}
        self._destroyed = False
        self._parameter_values: dict[str, Any] = {}
        self._callbacks: list[tuple[tuple[Any, ...], Any]] = []
        for parm_name, parm_spec in _parameter_specs(self._catalog_entry).items():
            default = parm_spec.get("default")
            if default is None:
                length = int(parm_spec.get("tuple_length", 1))
                scalar: Any = "" if parm_spec.get("type") == "string" else 0
                default = scalar if length == 1 else tuple(scalar for _ in range(length))
            if isinstance(default, list):
                default = tuple(default)
            self._parameter_values[parm_name] = copy.deepcopy(default)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        return bool(
            self._owner.path_equality
            and isinstance(other, FakeNode)
            and other._owner is self._owner
            and other._path == self._path
        )

    def __hash__(self) -> int:
        if self._owner.path_equality:
            return hash((id(self._owner), self._path))
        return id(self)

    def _assert_live(self) -> None:
        if self._destroyed:
            raise RuntimeError("fake node is destroyed")

    def sessionId(self) -> int:
        self._owner._record("node.session_id", self._path)
        return self._session_id

    def parent(self) -> "FakeNode | None":
        self._owner._record("node.parent", self._path)
        return self._parent

    def path(self) -> str:
        self._owner._record("node.path", self._path)
        return self._path

    def name(self) -> str:
        self._owner._record("node.name", self._path)
        return self._name

    def type(self) -> FakeNodeType:
        self._owner._record("node.type", self._path)
        return FakeNodeType(self._resolved_type)

    def children(self) -> tuple["FakeNode", ...]:
        self._owner._record("node.children", self._path)
        return tuple(self._children)

    def addEventCallback(
        self, event_types: tuple[Any, ...], callback: Any
    ) -> None:
        self._owner._record("node.add_event_callback", self._path)
        if self._owner.reject_observer_paths and self._path in self._owner.reject_observer_paths:
            raise RuntimeError("injected fake observer registration failure")
        entry = (tuple(event_types), callback)
        if entry not in self._callbacks:
            self._callbacks.append(entry)

    def removeEventCallback(
        self, event_types: tuple[Any, ...], callback: Any
    ) -> None:
        self._owner._record("node.remove_event_callback", self._path)
        expected = (tuple(event_types), callback)
        self._callbacks = [item for item in self._callbacks if item != expected]

    def eventCallbacks(self) -> tuple[tuple[tuple[Any, ...], Any], ...]:
        self._owner._record("node.event_callbacks", self._path)
        if self._path in self._owner.tamper_observer_readback_paths:
            return ()
        return tuple(self._callbacks)

    def _emit(self, event_type: Any, **event_details: Any) -> None:
        self._owner._emit_node_event(self, event_type, event_details)

    def createNode(
        self,
        node_type_name: str,
        node_name: str | None = None,
        run_init_scripts: bool = True,
        exact_type_name: bool = False,
    ) -> "FakeNode":
        self._assert_live()
        self._owner._assert_mutation_allowed("createNode")
        name = node_name or node_type_name
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise ValueError("invalid fake node name")
        if self._path == "/obj" and self._owner.return_preexisting_root:
            return self._owner.sentinel
        if self._path not in {"/", "/obj"} and self._owner.return_preexisting_child:
            return self._owner.sentinel
        child_path = f"/{name}" if self._path == "/" else f"{self._path}/{name}"
        if self._owner.node(child_path) is not None:
            raise RuntimeError("fake node path already exists")
        entry = self._owner._catalog_for_resolved_type(self, node_type_name)
        child = FakeNode(
            self._owner,
            self,
            name,
            child_path,
            node_type_name,
            entry,
        )
        if self._owner.coupled_display_flag_events and self._path != "/obj" and not self._children:
            child._display = True
        self._children.append(child)
        self._owner._registry[child_path] = child
        self._emit(self._owner.nodeEventType.ChildCreated, child_node=child)
        self._owner._mutate(
            "createNode",
            child_path,
            self,
            node_type_name,
            bool(run_init_scripts),
            bool(exact_type_name),
        )
        phase = "create_root" if self._path == "/obj" else "create_nodes"
        self._owner._note_phase_mutation(phase)
        self._owner._raise_create_node_after_registration(child_path)
        if phase == "create_root" and "created_root_name" in self._owner.tamper_hooks:
            child._name = f"{name}_other"
        return child

    def parm(self, name: str) -> FakeParm | None:
        self._owner._record("node.parm", self._path)
        specification = _parameter_specs(self._catalog_entry).get(name)
        if specification is None:
            return None
        return FakeParm(self, name, specification)

    def parmTuple(self, name: str) -> FakeParmTuple | None:
        self._owner._record("node.parm_tuple", self._path)
        specification = _parameter_specs(self._catalog_entry).get(name)
        if specification is None:
            return None
        return FakeParmTuple(self, name, specification)

    def _set_parameter(self, name: str, value: Any, *, tuple_access: bool) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("setParm")
        specification = _parameter_specs(self._catalog_entry).get(name)
        if specification is None:
            raise LookupError("parameter is absent from the fake catalog")
        length = int(specification.get("tuple_length", 1))
        if length > 1:
            if not tuple_access or not isinstance(value, (list, tuple)) or len(value) != length:
                raise TypeError("tuple parameter value does not match fake catalog")
            stored: Any = tuple(value)
        else:
            if tuple_access and isinstance(value, (list, tuple)):
                if len(value) != 1:
                    raise TypeError("scalar parameter tuple has the wrong length")
                stored = value[0]
            else:
                stored = value
        if "parameter" not in self._owner.tamper_hooks:
            self._parameter_values[name] = copy.deepcopy(stored)
        self._owner._mutate(
            "setParm", self._path, self, name, copy.deepcopy(stored)
        )
        self._emit(
            self._owner.nodeEventType.ParmTupleChanged,
            parm_tuple=self.parmTuple(name),
        )
        self._owner._note_phase_mutation("set_parameters")

    def setInput(
        self,
        input_index: int,
        source_node: "FakeNode | None",
        output_index: int = 0,
    ) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("setInput")
        if source_node is None:
            self._inputs.pop(input_index, None)
            source_path = ""
        else:
            source_node._assert_live()
            if "connection" not in self._owner.tamper_hooks:
                self._inputs[input_index] = FakeConnection(
                    source_node, self, output_index, input_index
                )
            source_path = source_node._path
        self._owner._mutate(
            "setInput",
            self._path,
            self,
            input_index,
            source_path,
            output_index,
        )
        self._emit(
            self._owner.nodeEventType.InputRewired,
            input_index=input_index,
        )
        self._owner._note_phase_mutation("connect_nodes")

    def inputConnections(self) -> tuple[FakeConnection, ...]:
        self._owner._record("node.input_connections", self._path)
        connections = tuple(
            self._inputs[index] for index in sorted(self._inputs)
        )
        if "connection_destination" in self._owner.tamper_hooks:
            return tuple(
                FakeConnection(
                    connection._source,
                    self._owner.sentinel,
                    connection._output_index,
                    connection._input_index,
                )
                for connection in connections
            )
        if "duplicate_connection" in self._owner.tamper_hooks and connections:
            return connections + (connections[0],)
        return connections

    def outputConnections(self) -> tuple[FakeConnection, ...]:
        self._owner._record("node.output_connections", self._path)
        connections: list[FakeConnection] = []
        seen_nodes: set[int] = set()
        for node in self._owner._registry.values():
            if id(node) in seen_nodes:
                continue
            seen_nodes.add(id(node))
            for connection in node._inputs.values():
                if connection._source is self:
                    connections.append(connection)
        return tuple(connections)

    def setDisplayFlag(self, value: bool) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("setDisplayFlag")
        desired = bool(value)
        if (
            self._owner.coupled_display_flag_events
            and self._parent is not None
            and self._display is not desired
        ):
            changed_siblings: list[FakeNode] = []
            switched = self
            if desired:
                for sibling in self._parent._children:
                    if sibling is not self and sibling._display:
                        sibling._display = False
                        changed_siblings.append(sibling)
            else:
                candidates = [
                    sibling for sibling in self._parent._children if sibling is not self
                ]
                if candidates:
                    switched = candidates[-1]
                    if not switched._display:
                        switched._display = True
                        changed_siblings.append(switched)
            if "display_flag" not in self._owner.tamper_hooks:
                self._display = desired
            self._owner._mutate("setDisplayFlag", self._path, self, desired)
            self._parent._emit(
                self._owner.nodeEventType.ChildSwitched,
                child_node=switched,
            )
            for sibling in changed_siblings:
                sibling._emit(self._owner.nodeEventType.FlagChanged)
            self._emit(self._owner.nodeEventType.FlagChanged)
            self._owner._note_phase_mutation("set_flags_layout")
            return
        if "display_flag" not in self._owner.tamper_hooks:
            self._display = desired
        self._owner._mutate("setDisplayFlag", self._path, self, desired)
        self._emit(self._owner.nodeEventType.FlagChanged)
        if self._parent is not None:
            self._parent._emit(
                self._owner.nodeEventType.ChildSwitched,
                child_node=self,
            )
        self._owner._note_phase_mutation("set_flags_layout")

    def isDisplayFlagSet(self) -> bool:
        self._owner._record("node.display_flag", self._path)
        return self._display

    def setRenderFlag(self, value: bool) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("setRenderFlag")
        if "render_flag" not in self._owner.tamper_hooks:
            self._render = bool(value)
        self._owner._mutate("setRenderFlag", self._path, self, bool(value))
        self._emit(self._owner.nodeEventType.FlagChanged)
        if self._parent is not None:
            self._parent._emit(
                self._owner.nodeEventType.ChildSwitched,
                child_node=self,
            )
        self._owner._note_phase_mutation("set_flags_layout")

    def isRenderFlagSet(self) -> bool:
        self._owner._record("node.render_flag", self._path)
        return self._render

    def setUserData(self, key: str, value: str) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("setUserData")
        self._user_data[str(key)] = str(value)
        self._owner._mutate(
            "setUserData", self._path, self, str(key), str(value)
        )
        self._emit(self._owner.nodeEventType.CustomDataChanged)

    def userData(self, key: str) -> str | None:
        self._owner._record("node.user_data", self._path)
        return self._user_data.get(key)

    def errors(self) -> tuple[str, ...]:
        self._owner._record("node.errors", self._path)
        if (
            "root_error" in self._owner.tamper_hooks
            and self._parent is not None
            and self._parent._path == "/obj"
        ):
            return ("fake root error",)
        return () if "cook_error" not in self._owner.tamper_hooks else ("fake cook error",)

    def destroy(self) -> None:
        self._assert_live()
        self._owner._assert_mutation_allowed("destroy")
        self._owner.destroy_attempts.append(self._path)
        self._owner._record("node.destroy.attempt", self._path)
        if self._owner.destroy_base_exception is not None:
            raise self._owner.destroy_base_exception
        if self._owner.destroy_raises:
            raise RuntimeError("injected fake destroy failure")
        if self._owner._registry.get(self._path) is not self:
            raise RuntimeError("fake node identity no longer owns the registered path")
        if self._parent is None or self not in self._parent._children:
            raise RuntimeError("fake node parent identity mismatch")
        for child in tuple(self._children):
            child._destroy_exact()
        self._emit(self._owner.nodeEventType.BeingDeleted)
        self._parent._emit(
            self._owner.nodeEventType.ChildDeleted,
            child_node=self,
        )
        self._parent._children.remove(self)
        if not self._owner.destroy_retains_registry:
            self._owner._registry.pop(self._path, None)
        if self._owner.destroy_drifts_parent_fingerprint:
            self._owner.sentinel._name = f"{self._owner.sentinel._name}_drift"
        self._destroyed = True
        self._owner._last_destroyed_root = self
        self._owner._mutate("destroy", self._path, self._parent)

    def _destroy_exact(self) -> None:
        for child in tuple(self._children):
            child._destroy_exact()
        self._emit(self._owner.nodeEventType.BeingDeleted)
        if self._parent is not None:
            self._parent._emit(
                self._owner.nodeEventType.ChildDeleted,
                child_node=self,
            )
            if self in self._parent._children:
                self._parent._children.remove(self)
        if self._owner._registry.get(self._path) is self:
            self._owner._registry.pop(self._path, None)
        self._destroyed = True


class _UndoGroup(AbstractContextManager[None]):
    def __init__(self, owner: "FakeHouWrite", label: str) -> None:
        self._owner = owner
        self._label = label

    def __enter__(self) -> None:
        self._owner._raise_undo_boundary("enter")
        if self._owner._undo_active:
            raise RuntimeError("nested fake Undo groups are unavailable")
        self._owner._undo_active = True
        self._owner.undo_group_entries += 1
        self._owner.undo_labels.append(self._label)
        self._owner._record("undos.group.enter", None)
        self._owner._raise_undo_boundary("enter_after_active")
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._owner._record("undos.group.exit", None)
        self._owner._undo_active = False
        self._owner._raise_undo_boundary("exit")
        if exc_type is None and self._owner.undo_exit_tampers_success:
            self._owner.sentinel._name = f"{self._owner.sentinel._name}_exit"
        if exc_type is not None and self._owner.undo_exit_resurrects_root:
            resurrected = self._owner._last_destroyed_root
            if resurrected is not None:
                resurrected._destroyed = False
                if not any(
                    child is resurrected
                    for child in resurrected._parent._children
                ):
                    resurrected._parent._children.append(resurrected)
                self._owner._registry[resurrected._path] = resurrected
        if exc_type is None:
            self._owner.undo_group_commits += 1
        else:
            self._owner.undo_group_failures += 1
        return False


class FakeUndos:
    def __init__(self, owner: "FakeHouWrite") -> None:
        self._owner = owner

    def group(self, label: str) -> _UndoGroup:
        self._owner._record("undos.group", None)
        self._owner._raise_undo_boundary("group")
        return _UndoGroup(self._owner, label)


class FakeHouWrite:
    """Module-like fake exposing a catalog-driven, write-only HOM subset."""

    def __init__(
        self,
        *,
        catalog: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
        failure_phase: str | None = None,
        base_exception_phase: str | None = None,
        tamper_hooks: set[str] | frozenset[str] = frozenset(),
        mutation_callback: Callable[[FakeMutation], None] | None = None,
        undo_failure_point: str | None = None,
        create_node_failure_after_registration: int | None = None,
        path_equality: bool = False,
        coupled_display_flag_events: bool = False,
    ) -> None:
        if failure_phase is not None and failure_phase not in MUTATION_PHASES:
            raise ValueError("unknown fake failure phase")
        if base_exception_phase is not None and base_exception_phase not in MUTATION_PHASES:
            raise ValueError("unknown fake BaseException phase")
        if undo_failure_point is not None and undo_failure_point not in UNDO_FAILURE_POINTS:
            raise ValueError("unknown fake Undo failure point")
        if (
            create_node_failure_after_registration is not None
            and create_node_failure_after_registration < 1
        ):
            raise ValueError("create-node failure index must be positive")
        self.catalog = copy.deepcopy(list(catalog or CERTIFIED_WRITE_CATALOG))
        self.failure_phase = failure_phase
        self.base_exception_phase = base_exception_phase
        self.tamper_hooks = frozenset(tamper_hooks)
        self.destroy_raises = False
        self.destroy_base_exception: BaseException | None = None
        self.destroy_retains_registry = False
        self.destroy_drifts_parent_fingerprint = False
        self.destroy_attempts: list[str] = []
        self.return_preexisting_root = False
        self.return_preexisting_child = False
        self.undo_failure_point = undo_failure_point
        self.undo_exit_tampers_success = False
        self.undo_exit_resurrects_root = False
        self._last_destroyed_root: FakeNode | None = None
        self.create_node_failure_after_registration = (
            create_node_failure_after_registration
        )
        event_names = (
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
        self.nodeEventType = SimpleNamespace(
            **{name: FakeNodeEventType(name) for name in event_names}
        )
        self.reject_observer_paths: set[str] = set()
        self.tamper_observer_readback_paths: set[str] = set()
        self.suppressed_event_operations: set[str] = set()
        self.event_type_overrides: dict[str, Any] = {}
        self.event_source_overrides: dict[str, FakeNode] = {}
        self.deferred_event_operations: set[str] = set()
        self._deferred_node_events: list[
            tuple[FakeNode, Any, dict[str, Any]]
        ] = []
        self.path_equality = bool(path_equality)
        self.coupled_display_flag_events = bool(coupled_display_flag_events)
        self._create_node_registrations = 0
        self._mutation_callback = mutation_callback
        self._triggered_failures: set[tuple[str, str]] = set()
        self.phase_mutations: list[str] = []
        self._session_counter = 0
        self._registry: dict[str, FakeNode] = {}
        self.call_log: list[FakeHouCall] = []
        self.calls = self.call_log
        self.mutation_log: list[FakeMutation] = []
        self.undo_group_entries = 0
        self.undo_group_commits = 0
        self.undo_group_failures = 0
        self.undo_labels: list[str] = []
        self._undo_active = False
        self.undos = FakeUndos(self)

        root = FakeNode(self, None, "", "/", "root", None)
        obj = FakeNode(self, root, "obj", "/obj", "obj", None)
        root._children.append(obj)
        self._registry.update({"/": root, "/obj": obj})

        # A user-owned object proves confined rollback.  Construction is not
        # part of the transaction evidence and is therefore removed from logs.
        sentinel_entry = next(
            entry
            for entry in self.catalog
            if entry["context"] == "Object" and entry["requested_name"] == "geo"
        )
        sentinel = FakeNode(
            self,
            obj,
            "User_Sentinel",
            "/obj/User_Sentinel",
            str(sentinel_entry["resolved_name"]),
            sentinel_entry,
        )
        obj._children.append(sentinel)
        self._registry[sentinel._path] = sentinel
        sentinel._user_data["owner"] = "user"
        self.sentinel = sentinel
        self.call_log.clear()
        self.mutation_log.clear()

    def _next_session_id(self) -> int:
        self._session_counter += 1
        return self._session_counter

    def _record(self, name: str, path: str | None) -> None:
        self.call_log.append(FakeHouCall(name, path, threading.get_ident()))

    def _mutate(
        self,
        operation: str,
        path: str,
        callback_source: Any,
        *detail: Any,
    ) -> None:
        self._assert_mutation_allowed(operation)
        mutation = FakeMutation(
            operation,
            path,
            callback_source,
            tuple(detail),
            threading.get_ident(),
        )
        self.mutation_log.append(mutation)
        if self._mutation_callback is not None:
            self._mutation_callback(mutation)

    def _emit_node_event(
        self,
        source: FakeNode,
        event_type: Any,
        event_details: Mapping[str, Any],
    ) -> None:
        operation = event_type.name
        if operation in self.suppressed_event_operations:
            return
        emitted_type = self.event_type_overrides.get(operation, event_type)
        emitted_source = self.event_source_overrides.get(operation, source)
        payload = dict(event_details)
        if operation in self.deferred_event_operations:
            self._deferred_node_events.append(
                (emitted_source, emitted_type, payload)
            )
            return
        for event_types, callback in tuple(source._callbacks):
            if emitted_type in event_types:
                callback(
                    node=emitted_source,
                    event_type=emitted_type,
                    **payload,
                )

    def flush_deferred_events(self) -> None:
        pending = tuple(self._deferred_node_events)
        self._deferred_node_events.clear()
        for source, event_type, details in pending:
            for event_types, callback in tuple(source._callbacks):
                if event_type in event_types:
                    callback(node=source, event_type=event_type, **details)

    def _assert_mutation_allowed(self, operation: str) -> None:
        if not self._undo_active:
            raise AssertionError(
                f"fake HOM mutation {operation!r} occurred outside an active Undo group"
            )

    def _raise_undo_boundary(self, point: str) -> None:
        if self.undo_failure_point == point:
            raise RuntimeError(f"injected fake Undo {point} failure")

    def _raise_create_node_after_registration(self, path: str) -> None:
        self._create_node_registrations += 1
        if self.create_node_failure_after_registration == self._create_node_registrations:
            raise RuntimeError(
                f"injected fake createNode failure after registering {path}"
            )

    def _note_phase_mutation(self, phase: str) -> None:
        self.phase_mutations.append(phase)

    def phase_hook(self, phase: str) -> None:
        """Adapter test hook that raises only after one named phase completes.

        Assigning this callable to the dormant adapter's test-only phase hook
        makes the failure happen after the adapter has captured its rollback
        proof.  The fake HOM methods themselves therefore never raise after a
        successful mutation but before returning its object reference.
        """

        if phase not in MUTATION_PHASES:
            raise ValueError("unknown fake transaction phase")
        self._raise_once(phase)

    def _raise_once(self, phase: str) -> None:
        if self.base_exception_phase == phase and ("base", phase) not in self._triggered_failures:
            self._triggered_failures.add(("base", phase))
            raise InjectedHouBaseException(phase)
        if self.failure_phase == phase and ("exception", phase) not in self._triggered_failures:
            self._triggered_failures.add(("exception", phase))
            raise InjectedHouFailure(phase)

    def _catalog_for_resolved_type(
        self, parent: FakeNode, resolved_type: str
    ) -> dict[str, Any]:
        expected_context = "Object" if parent._path == "/obj" else "Sop"
        matches = [
            entry
            for entry in self.catalog
            if entry.get("context") == expected_context
            and entry.get("resolved_name") == resolved_type
            and entry.get("available") is True
            and entry.get("creatable") is True
        ]
        if len(matches) != 1:
            raise LookupError("resolved type is absent or ambiguous in fake catalog")
        return copy.deepcopy(matches[0])

    def node(self, path: str) -> FakeNode | None:
        self._record("node.lookup", path)
        return self._registry.get(path)

    def replace_node_identity(self, path: str) -> FakeNode:
        """Install a new object at the same path without destroying the old one."""

        old = self._registry[path]
        parent = old._parent
        replacement = FakeNode(
            self,
            parent,
            old._name,
            old._path,
            old._resolved_type,
            old._catalog_entry,
        )
        replacement._children = list(old._children)
        replacement._inputs = dict(old._inputs)
        replacement._parameter_values = copy.deepcopy(old._parameter_values)
        replacement._display = old._display
        replacement._render = old._render
        replacement._user_data = dict(old._user_data)
        if parent is not None:
            parent._children = [replacement if child is old else child for child in parent._children]
        self._registry[path] = replacement
        self._mutate(
            "replaceIdentity",
            path,
            replacement,
            old._session_id,
            replacement._session_id,
        )
        return replacement

    def duplicate_node_wrapper(self, path: str) -> FakeNode:
        """Return another Python wrapper for the same fake HOM node identity."""

        original = self._registry[path]
        duplicate = object.__new__(FakeNode)
        duplicate.__dict__ = original.__dict__.copy()
        return duplicate

    def seed_preexisting_child(
        self,
        parent_path: str,
        resolved_type: str,
        name: str,
    ) -> FakeNode:
        """Create immutable test setup without masquerading as a transaction mutation."""

        parent = self._registry[parent_path]
        child_path = f"{parent_path.rstrip('/')}/{name}"
        if child_path in self._registry:
            raise RuntimeError("fake setup path already exists")
        entry = self._catalog_for_resolved_type(parent, resolved_type)
        child = FakeNode(self, parent, name, child_path, resolved_type, entry)
        parent._children.append(child)
        self._registry[child_path] = child
        return child

    @staticmethod
    def snapshot_node(node: FakeNode) -> dict[str, Any]:
        """Return a detached, content-complete fake-node snapshot without HOM calls."""

        return {
            "identity": id(node),
            "session_id": node._session_id,
            "parent_identity": None if node._parent is None else id(node._parent),
            "name": node._name,
            "path": node._path,
            "resolved_type": node._resolved_type,
            "catalog_entry": copy.deepcopy(node._catalog_entry),
            "children": tuple(child._session_id for child in node._children),
            "inputs": tuple(
                (
                    index,
                    connection._source._session_id,
                    connection._destination._session_id,
                    connection._output_index,
                    connection._input_index,
                )
                for index, connection in sorted(node._inputs.items())
            ),
            "parameters": copy.deepcopy(node._parameter_values),
            "display": node._display,
            "render": node._render,
            "user_data": copy.deepcopy(node._user_data),
            "destroyed": node._destroyed,
        }

    def inject_extra_child(
        self, parent: FakeNode, *, name: str = "unexpected_extra", resolved_type: str = "null"
    ) -> FakeNode:
        """Tamper with observed state without using adapter semantics."""

        child = parent.createNode(resolved_type, name)
        self._mutate("tamper.extraChild", child._path, parent)
        return child

    @property
    def registry_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._registry))

    @property
    def undo_group_count(self) -> int:
        return self.undo_group_entries


def certified_write_catalog() -> list[dict[str, Any]]:
    """Return a detached catalog snapshot for dependency injection in tests."""

    return copy.deepcopy(CERTIFIED_WRITE_CATALOG)


__all__ = [
    "CERTIFIED_WRITE_CATALOG",
    "FakeConnection",
    "FakeHouCall",
    "FakeHouWrite",
    "FakeMutation",
    "FakeNode",
    "FakeNodeEventType",
    "FakeNodeType",
    "FakeParm",
    "FakeParmTuple",
    "InjectedHouBaseException",
    "InjectedHouFailure",
    "MUTATION_PHASES",
    "UNDO_FAILURE_POINTS",
    "certified_write_catalog",
]
