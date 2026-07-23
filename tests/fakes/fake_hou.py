"""Small read-only HOM-shaped fixture for the Gate B2 Panel adapter tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


class FakeEnumValue:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"FakeEnumValue({self._name!r})"


class FakeParmTemplate:
    def __init__(
        self,
        owner: "FakeHou",
        name: str,
        label: str,
        *,
        value_type: str = "Float",
        tuple_size: int = 1,
        default: tuple[Any, ...] | None = None,
        minimum: float = -10.0,
        maximum: float = 10.0,
        min_strict: bool = False,
        max_strict: bool = False,
    ) -> None:
        self._owner = owner
        self._name = name
        self._label = label
        self._type = FakeEnumValue(value_type)
        self._tuple_size = tuple_size
        self._default = default or tuple(0.0 for _ in range(tuple_size))
        self._minimum = minimum
        self._maximum = maximum
        self._min_strict = min_strict
        self._max_strict = max_strict

    def name(self) -> str:
        self._owner._record("parm_template.name")
        return self._name

    def label(self) -> str:
        self._owner._record("parm_template.label")
        return self._label

    def type(self) -> FakeEnumValue:
        self._owner._record("parm_template.type")
        return self._type

    def numComponents(self) -> int:
        self._owner._record("parm_template.num_components")
        return self._tuple_size

    def defaultValue(self) -> tuple[Any, ...]:
        self._owner._record("parm_template.default")
        return self._default

    def minValue(self) -> float:
        self._owner._record("parm_template.minimum")
        return self._minimum

    def maxValue(self) -> float:
        self._owner._record("parm_template.maximum")
        return self._maximum

    def minIsStrict(self) -> bool:
        self._owner._record("parm_template.minimum_strict")
        return self._min_strict

    def maxIsStrict(self) -> bool:
        self._owner._record("parm_template.maximum_strict")
        return self._max_strict


class FakeParmTemplateGroup:
    def __init__(self, owner: "FakeHou", entries: tuple[FakeParmTemplate, ...]) -> None:
        self._owner = owner
        self._entries = entries

    def entries(self) -> tuple[FakeParmTemplate, ...]:
        self._owner._record("parm_template_group.entries")
        return self._entries


class FakeNodeType:
    def __init__(
        self,
        owner: "FakeHou",
        name: str,
        *,
        input_count: int,
        output_count: int,
        templates: tuple[FakeParmTemplate, ...] = (),
        definition: object | None = None,
    ) -> None:
        self._owner = owner
        self._name = name
        self._input_count = input_count
        self._output_count = output_count
        self._group = FakeParmTemplateGroup(owner, templates)
        self._definition = definition

    def name(self) -> str:
        self._owner._record("node_type.name")
        return self._name

    def definition(self) -> object | None:
        self._owner._record("node_type.definition")
        return self._definition

    def maxNumInputs(self) -> int:
        self._owner._record("node_type.max_inputs")
        return self._input_count

    def maxNumOutputs(self) -> int:
        self._owner._record("node_type.max_outputs")
        return self._output_count

    def parmTemplateGroup(self) -> FakeParmTemplateGroup:
        self._owner._record("node_type.parm_template_group")
        return self._group


class FakeNodeTypeCategory:
    def __init__(self, owner: "FakeHou", node_types: dict[str, FakeNodeType]) -> None:
        self._owner = owner
        self._node_types = node_types

    def nodeTypes(self) -> dict[str, FakeNodeType]:
        self._owner._record("node_type_category.node_types")
        return dict(self._node_types)


class FakeConnection:
    pass


class FakeNode:
    def __init__(
        self,
        owner: "FakeHou",
        name: str,
        path: str,
        *,
        user_data: dict[str, str] | None = None,
        connection_count: int = 0,
    ) -> None:
        self._owner = owner
        self._session_id = owner._next_node_session_id()
        self._name = name
        self._path = path
        self._user_data = dict(user_data or {})
        self._connections = tuple(FakeConnection() for _ in range(connection_count))
        self._children: list[FakeNode] = []
        self._callbacks: list[tuple[tuple[Any, ...], Any]] = []

    def add_child(self, child: "FakeNode", *, notify: bool = False) -> None:
        self._children.append(child)
        if notify:
            self.emit(
                self._owner.nodeEventType.ChildCreated,
                child_node=child,
            )

    def children(self) -> tuple["FakeNode", ...]:
        self._owner._record("node.children")
        return tuple(self._children)

    def path(self) -> str:
        self._owner._record("node.path")
        return self._path

    def sessionId(self) -> int:
        self._owner._record("node.session_id")
        return self._session_id

    def name(self) -> str:
        self._owner._record("node.name")
        return self._name

    def userData(self, key: str) -> str | None:
        self._owner._record("node.user_data")
        return self._user_data.get(key)

    def inputConnections(self) -> tuple[FakeConnection, ...]:
        self._owner._record("node.input_connections")
        return self._connections

    def addEventCallback(self, event_types: tuple[Any, ...], callback: Any) -> None:
        self._owner._record("node.add_event_callback")
        if self._owner.reject_node_observers:
            raise RuntimeError("node observers unavailable")
        self._callbacks.append((tuple(event_types), callback))

    def removeEventCallback(self, event_types: tuple[Any, ...], callback: Any) -> None:
        self._owner._record("node.remove_event_callback")
        expected = (tuple(event_types), callback)
        self._callbacks = [item for item in self._callbacks if item != expected]

    def eventCallbacks(self) -> tuple[tuple[tuple[Any, ...], Any], ...]:
        self._owner._record("node.event_callbacks")
        if self._owner.hide_node_event_callbacks:
            return ()
        return tuple(self._callbacks)

    def emit(
        self,
        event_type: Any,
        *,
        callback_source: "FakeNode | None" = None,
        **event_details: Any,
    ) -> None:
        for event_types, callback in tuple(self._callbacks):
            if event_type in event_types:
                callback(
                    node=self if callback_source is None else callback_source,
                    event_type=event_type,
                    **event_details,
                )

    @property
    def callback_count(self) -> int:
        return len(self._callbacks)


class FakeNodeWrapper:
    """A fresh Python wrapper for one existing fake HOM node.

    Real Houdini can return multiple ``hou.Node`` Python objects for the same
    underlying node.  ``equivalent=False`` models an adversarial replacement
    that reuses the same path and session ID but does not compare equal.
    """

    def __init__(self, node: FakeNode, *, equivalent: bool = True) -> None:
        self._node = node
        self._equivalent = bool(equivalent)

    def __eq__(self, other: object) -> bool:
        if not self._equivalent:
            return False
        if isinstance(other, FakeNodeWrapper):
            return bool(other._equivalent and self._node is other._node)
        return self._node is other

    def __getattr__(self, name: str) -> Any:
        return getattr(self._node, name)


class FakeHipFile:
    def __init__(self, owner: "FakeHou") -> None:
        self._owner = owner
        self._callbacks: list[Any] = []
        self._dirty = False

    def addEventCallback(self, callback: Any) -> None:
        self._owner._record("hip_file.add_event_callback")
        if self._owner.reject_hip_observer:
            raise RuntimeError("HIP observer unavailable")
        self._callbacks.append(callback)

    def removeEventCallback(self, callback: Any) -> None:
        self._owner._record("hip_file.remove_event_callback")
        self._callbacks = [item for item in self._callbacks if item != callback]

    def hasUnsavedChanges(self) -> bool:
        self._owner._record("hip_file.has_unsaved_changes")
        return self._dirty

    def emit(self, event_type: Any, **event_details: Any) -> None:
        for callback in tuple(self._callbacks):
            callback(event_type, **event_details)

    @property
    def callback_count(self) -> int:
        return len(self._callbacks)


@dataclass(frozen=True)
class FakeHouCall:
    name: str
    thread_id: int


class FakeHou:
    """Module-like object exposing only reads and observer registration."""

    def __init__(
        self,
        *,
        build: str = "21.0.440",
        reject_hip_observer: bool = False,
        reject_node_observers: bool = False,
        hide_node_event_callbacks: bool = False,
        missing_node_event: str | None = None,
        missing_node_types: tuple[tuple[str, str], ...] = (),
        parameter_conflict: tuple[str, str, str] | None = None,
        return_fresh_node_wrappers: bool = False,
        node_wrappers_equivalent: bool = True,
    ) -> None:
        self.calls: list[FakeHouCall] = []
        self.build = build
        self.reject_hip_observer = reject_hip_observer
        self.reject_node_observers = reject_node_observers
        self.hide_node_event_callbacks = hide_node_event_callbacks
        self.return_fresh_node_wrappers = bool(return_fresh_node_wrappers)
        self.node_wrappers_equivalent = bool(node_wrappers_equivalent)
        self._frame = 1.0
        self._fps = 24.0
        self._session_id_counter = 0

        node_events = {
            name: FakeEnumValue(name)
            for name in (
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
            if name != missing_node_event
        }
        self.nodeEventType = SimpleNamespace(**node_events)
        self.hipFileEventType = SimpleNamespace(
            AfterLoad=FakeEnumValue("AfterLoad"),
            AfterClear=FakeEnumValue("AfterClear"),
            AfterSave=FakeEnumValue("AfterSave"),
            AfterMerge=FakeEnumValue("AfterMerge"),
        )
        self.hipFile = FakeHipFile(self)

        self.root = FakeNode(self, "", "/")
        self.obj = FakeNode(self, "obj", "/obj")
        self.root.add_child(self.obj)
        self._nodes: dict[str, FakeNode] = {"/": self.root, "/obj": self.obj}

        size = FakeParmTemplate(
            self,
            "size",
            "Size",
            tuple_size=3,
            default=(1.0, 1.0, 1.0),
            minimum=0.0,
            maximum=1000.0,
        )
        translate = FakeParmTemplate(
            self,
            "t",
            "Translate",
            tuple_size=3,
            default=(0.0, 0.0, 0.0),
            minimum=-1_000_000.0,
            maximum=1_000_000.0,
        )
        if parameter_conflict is not None:
            context, node_name, parameter_name = parameter_conflict
            if (context, node_name, parameter_name) == ("Sop", "box", "size"):
                size = FakeParmTemplate(
                    self,
                    "size",
                    "Size",
                    value_type="String",
                    tuple_size=1,
                    default=("unsafe",),
                )

        object_types = {
            "geo": FakeNodeType(self, "geo", input_count=0, output_count=1)
        }
        sop_types = {
            "box": FakeNodeType(
                self,
                "box",
                input_count=0,
                output_count=1,
                templates=(size, translate),
            ),
            "xform": FakeNodeType(
                self,
                "xform",
                input_count=1,
                output_count=1,
                templates=(translate,),
            ),
            "merge": FakeNodeType(
                self, "merge", input_count=9999, output_count=1
            ),
            "null": FakeNodeType(self, "null", input_count=1, output_count=1),
        }
        for context, name in missing_node_types:
            target = object_types if context == "Object" else sop_types
            live_name = "xform" if (context, name) == ("Sop", "transform") else name
            target.pop(live_name, None)
        self._categories = {
            "Object": FakeNodeTypeCategory(self, object_types),
            "Sop": FakeNodeTypeCategory(self, sop_types),
        }

    def _record(self, name: str) -> None:
        self.calls.append(FakeHouCall(name, threading.get_ident()))

    def _next_node_session_id(self) -> int:
        self._session_id_counter += 1
        return self._session_id_counter

    def applicationVersionString(self) -> str:
        self._record("application_version")
        return self.build

    def nodeTypeCategories(self) -> dict[str, FakeNodeTypeCategory]:
        self._record("node_type_categories")
        return dict(self._categories)

    def node(self, path: str) -> FakeNode | FakeNodeWrapper | None:
        self._record("node.lookup")
        node = self._nodes.get(path)
        if node is None or not self.return_fresh_node_wrappers:
            return node
        return FakeNodeWrapper(
            node,
            equivalent=self.node_wrappers_equivalent,
        )

    def node_wrapper(
        self, path: str, *, equivalent: bool = True
    ) -> FakeNodeWrapper:
        node = self._nodes[path]
        return FakeNodeWrapper(node, equivalent=equivalent)

    def frame(self) -> float:
        self._record("frame")
        return self._frame

    def fps(self) -> float:
        self._record("fps")
        return self._fps

    def trigger_manual_change(self, path: str = "/obj") -> None:
        self.hipFile._dirty = True
        self._nodes[path].emit(self.nodeEventType.ParmTupleChanged)

    def trigger_load(self) -> None:
        self.hipFile._dirty = False
        self.hipFile.emit(
            self.hipFileEventType.AfterLoad,
            old_hip_file="hidden-old.hip",
            new_hip_file="hidden-new.hip",
        )

    def trigger_clear(self) -> None:
        self.hipFile._dirty = False
        self.hipFile.emit(
            self.hipFileEventType.AfterClear,
            old_hip_file="hidden-old.hip",
        )

    def trigger_save(self) -> None:
        self.hipFile._dirty = False
        self.hipFile.emit(
            self.hipFileEventType.AfterSave,
            old_hip_file="hidden-old.hip",
            new_hip_file="hidden-new.hip",
        )

    def replace_node_instance(self, path: str) -> FakeNode:
        """Replace one live node with a new session identity at the same path."""

        old = self._nodes[path]
        replacement = FakeNode(
            self,
            old._name,
            old._path,
            user_data=old._user_data,
            connection_count=len(old._connections),
        )
        replacement._children = list(old._children)
        parent_path = path.rsplit("/", 1)[0] or "/"
        parent = self._nodes[parent_path]
        parent._children = [
            replacement if child is old else child for child in parent._children
        ]
        self._nodes[path] = replacement
        return replacement

    def add_hia_graph(
        self,
        name: str,
        digest: str,
        *,
        node_count: int = 2,
        connection_count: int = 1,
        notify: bool = True,
    ) -> FakeNode:
        graph = FakeNode(
            self,
            name,
            f"/obj/{name}",
            user_data={
                "hia_ownership": "hia_owned",
                "hia_graph_digest": digest,
            },
        )
        remaining = connection_count
        for index in range(node_count):
            count = 1 if remaining > 0 else 0
            remaining -= count
            child = FakeNode(
                self,
                f"node_{index}",
                f"/obj/{name}/node_{index}",
                connection_count=count,
            )
            graph.add_child(child)
            self._nodes[child.path()] = child
        self.obj.add_child(graph, notify=notify)
        self._nodes[graph.path()] = graph
        return graph

    @property
    def observer_callback_count(self) -> int:
        return self.hipFile.callback_count + sum(
            node.callback_count for node in self._nodes.values()
        )


__all__ = ["FakeHou", "FakeHouCall"]
