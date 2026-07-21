from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
COMPOSER_PATH = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_panel"
    / "composer.py"
)


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def emit(self, *_args: object) -> None:
        for callback in self.callbacks:
            callback(*_args)


class _DocumentLayout:
    def __init__(self) -> None:
        self.height = 18.0
        self.documentSizeChanged = _Signal()

    def documentSize(self) -> object:  # noqa: N802
        return types.SimpleNamespace(height=lambda: self.height)


class _Document:
    def __init__(self) -> None:
        self.layout = _DocumentLayout()
        self.contentsChanged = _Signal()

    def documentLayout(self) -> _DocumentLayout:  # noqa: N802
        return self.layout


class _TextEdit:
    def __init__(self, _parent: object = None) -> None:
        self._document = _Document()
        self._minimum_height = 0
        self._maximum_height = 16_777_215
        self._size_policy: tuple[object, object] | None = None

    def setAcceptRichText(self, _enabled: bool) -> None:  # noqa: N802
        pass

    def setSizePolicy(self, horizontal: object, vertical: object) -> None:  # noqa: N802
        self._size_policy = (horizontal, vertical)

    def document(self) -> _Document:
        return self._document

    def frameWidth(self) -> int:  # noqa: N802
        return 1

    def fontMetrics(self) -> object:  # noqa: N802
        return types.SimpleNamespace(lineSpacing=lambda: 18)

    def minimumHeight(self) -> int:  # noqa: N802
        return self._minimum_height

    def maximumHeight(self) -> int:  # noqa: N802
        return self._maximum_height

    def setMinimumHeight(self, value: int) -> None:  # noqa: N802
        self._minimum_height = value

    def setMaximumHeight(self, value: int) -> None:  # noqa: N802
        self._maximum_height = value

    def updateGeometry(self) -> None:  # noqa: N802
        pass

    def resizeEvent(self, _event: object) -> None:  # noqa: N802
        pass

    def insertFromMimeData(self, _source: object) -> None:  # noqa: N802
        pass


class _Shortcut:
    def __init__(self, _sequence: object, _parent: object) -> None:
        self.activated = _Signal()

    def setContext(self, _context: object) -> None:  # noqa: N802
        pass


def _load_composer_module() -> types.ModuleType:
    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_gui = types.ModuleType("PySide6.QtGui")
    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_core.Signal = lambda *_args, **_kwargs: _Signal()
    qt_core.Qt = types.SimpleNamespace(
        ShortcutContext=types.SimpleNamespace(WidgetShortcut=object())
    )
    qt_gui.QShortcut = _Shortcut
    qt_gui.QKeySequence = lambda value: value
    qt_widgets.QTextEdit = _TextEdit
    qt_widgets.QWidget = object
    qt_widgets.QSizePolicy = types.SimpleNamespace(
        Policy=types.SimpleNamespace(
            Expanding="expanding",
            Preferred="preferred",
        )
    )
    pyside.QtCore = qt_core
    pyside.QtGui = qt_gui
    pyside.QtWidgets = qt_widgets

    module_name = "hia_panel._composer_layout_test_subject"
    with mock.patch.dict(
        sys.modules,
        {
            "PySide6": pyside,
            "PySide6.QtCore": qt_core,
            "PySide6.QtGui": qt_gui,
            "PySide6.QtWidgets": qt_widgets,
        },
    ):
        spec = importlib.util.spec_from_file_location(module_name, COMPOSER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load composer.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)
    return module


class ComposerLayoutTests(unittest.TestCase):
    def test_initial_editor_reserves_four_lines_without_taking_chat_stretch(self) -> None:
        module = _load_composer_module()
        editor = module.ExpandableTextEdit()

        self.assertEqual(4, editor._MIN_VISIBLE_LINES)
        self.assertGreaterEqual(editor.minimumHeight(), 96)
        self.assertEqual(editor.minimumHeight(), editor.maximumHeight())
        self.assertEqual(("expanding", "preferred"), editor._size_policy)

    def test_content_relayout_and_resize_never_collapse_below_minimum(self) -> None:
        module = _load_composer_module()
        editor = module.ExpandableTextEdit()
        minimum_height = editor.minimumHeight()

        editor.document().documentLayout().height = 400.0
        editor._update_height()
        self.assertEqual(minimum_height, editor.minimumHeight())
        self.assertEqual(editor._MAX_HEIGHT, editor.maximumHeight())

        editor.document().documentLayout().height = 18.0
        editor.resizeEvent(object())
        self.assertEqual(minimum_height, editor.minimumHeight())
        self.assertEqual(minimum_height, editor.maximumHeight())

        reopened = module.ExpandableTextEdit()
        self.assertEqual(minimum_height, reopened.minimumHeight())


if __name__ == "__main__":
    unittest.main()
