from __future__ import annotations

import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hia_core.vending_machine import build  # noqa: E402


class _ParmTuple:
    def __init__(self) -> None:
        self.value = None

    def set(self, value: object) -> None:
        self.value = value


class _Node:
    def __init__(self, type_name: str, name: str, parent: _Node | None = None) -> None:
        self.type_name = type_name
        self.name = name
        self.parent = parent
        self.children: list[_Node] = []
        self.parms: dict[str, _ParmTuple] = {}
        self.inputs: list[_Node] = []
        self.display = False
        self.render = False
        self.selected = False
        self.layout_called = False

    def path(self) -> str:
        return f"{self.parent.path()}/{self.name}" if self.parent else self.name

    def createNode(self, type_name: str, node_name: str, **_kwargs: object) -> _Node:
        node = _Node(type_name, node_name, self)
        self.children.append(node)
        return node

    def parmTuple(self, name: str) -> _ParmTuple:
        return self.parms.setdefault(name, _ParmTuple())

    def setInput(self, _index: int, node: _Node) -> None:
        self.inputs = [node]

    def setNextInput(self, node: _Node) -> None:
        self.inputs.append(node)

    def setDisplayFlag(self, value: bool) -> None:
        self.display = value

    def setRenderFlag(self, value: bool) -> None:
        self.render = value

    def setSelected(self, value: bool, *, clear_all_selected: bool) -> None:
        self.selected = value and clear_all_selected

    def layoutChildren(self) -> None:
        self.layout_called = True


class VendingMachineBuilderTests(unittest.TestCase):
    def test_build_creates_one_editable_root_and_returns_its_path(self) -> None:
        obj = _Node("obj", "/obj")
        fake_hou = SimpleNamespace(
            node=lambda path: obj if path == "/obj" else None,
            undos=SimpleNamespace(group=lambda _label: nullcontext()),
        )

        with mock.patch.dict(sys.modules, {"hou": fake_hou}):
            root_path = build()

        self.assertEqual("/obj/HIA_Result", root_path)
        self.assertEqual(1, len(obj.children))
        root = obj.children[0]
        self.assertEqual("geo", root.type_name)
        self.assertEqual(32, len(root.children))
        self.assertTrue(root.layout_called)
        self.assertTrue(root.display)
        self.assertTrue(root.selected)
        output = root.children[-1]
        self.assertEqual("OUT_VENDING_MACHINE", output.name)
        self.assertTrue(output.display)
        self.assertTrue(output.render)
        self.assertEqual("merge", output.inputs[0].type_name)


if __name__ == "__main__":
    unittest.main()
