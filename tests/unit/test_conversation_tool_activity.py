from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
CONVERSATION_VIEW_PATH = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_panel"
    / "conversation_view.py"
)


def _load_conversation_module() -> types.ModuleType:
    class _QtBase:
        pass

    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_gui = types.ModuleType("PySide6.QtGui")
    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_core.Qt = types.SimpleNamespace(
        AlignmentFlag=types.SimpleNamespace(
            AlignLeft=object(),
            AlignRight=object(),
        )
    )
    qt_widgets.QWidget = _QtBase
    qt_widgets.QFrame = _QtBase
    qt_widgets.QTextBrowser = _QtBase
    pyside.QtCore = qt_core
    pyside.QtGui = qt_gui
    pyside.QtWidgets = qt_widgets

    module_name = "hia_panel._conversation_tool_activity_test_subject"
    replacements = {
        "PySide6": pyside,
        "PySide6.QtCore": qt_core,
        "PySide6.QtGui": qt_gui,
        "PySide6.QtWidgets": qt_widgets,
    }
    with mock.patch.dict(sys.modules, replacements):
        spec = importlib.util.spec_from_file_location(
            module_name,
            CONVERSATION_VIEW_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load conversation_view.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)
    return module


conversation_module = _load_conversation_module()


class _TextWidget:
    def __init__(self) -> None:
        self.text = ""
        self.visible = False

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = text

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self.text = text

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible = bool(visible)

    def isHidden(self) -> bool:  # noqa: N802
        return not self.visible


class _HeadlessToolCard:
    def __init__(self) -> None:
        self.state = conversation_module._ToolActivityState()
        self.expanded = False

    def update_activity(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: object = None,
    ) -> None:
        self.state.update(item_id, tool_name, status, error)

    def update_progress(self, item_id: str, message: str) -> None:
        self.state.update_progress(item_id, message)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)


class _Body:
    def set_markdown(self, _text: str) -> None:
        pass


class _HeadlessMessageCard:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.body = _Body()

    def set_attachments(self, _names: object) -> None:
        pass


class ConversationToolActivityTests(unittest.TestCase):
    def test_state_counts_unique_items_and_groups_tools(self) -> None:
        state = conversation_module._ToolActivityState()
        tools = (
            [(f"set-{index}", "set_parameters") for index in range(18)]
            + [(f"shot-{index}", "capture_screenshot") for index in range(3)]
            + [(f"exec-{index}", "execute_python") for index in range(6)]
        )
        for item_id, tool_name in tools:
            state.update(item_id, tool_name, "started")
            state.update(item_id, tool_name, "completed")

        state.update("set-2", "", "failed", "bad parm tuple")
        state.update("set-2", "", "failed")
        state.update(
            "shot-1",
            "capture_screenshot",
            "failed",
            {"code": "capture_failed", "message": "viewport unavailable"},
        )

        self.assertEqual(state.total_count, 27)
        self.assertEqual(state.failed_count, 2)
        self.assertEqual(
            state.summary_text(),
            "Houdini 工具活动：共 27 次，失败 2 次",
        )
        details = state.detail_text()
        self.assertIn("set_parameters × 18", details)
        self.assertIn("capture_screenshot × 3", details)
        self.assertIn("execute_python × 6", details)
        self.assertEqual(details.count("set-2"), 1)
        self.assertIn("错误：bad parm tuple", details)
        self.assertIn('"message": "viewport unavailable"', details)

    def test_card_collapses_completed_and_expands_real_failure(self) -> None:
        card = object.__new__(conversation_module._ToolActivityCard)
        card._state = conversation_module._ToolActivityState()
        card.summary_label = _TextWidget()
        card.details = _TextWidget()
        card.toggle_button = _TextWidget()

        card.set_expanded(True)
        card.update_activity("item-1", "execute_python", "completed")
        self.assertFalse(card.details.visible)
        self.assertEqual(card.toggle_button.text, "展开详情")

        real_error = "hou.OperationFailed: Invalid node type name"
        card.update_activity("item-2", "create_node", "failed", real_error)
        self.assertTrue(card.details.visible)
        self.assertEqual(card.toggle_button.text, "收起详情")
        self.assertIn(real_error, card.details.text)

        card.update_activity("item-3", "read_node", "completed")
        self.assertTrue(card.details.visible)

    def test_progress_stays_inside_the_matching_item_detail(self) -> None:
        state = conversation_module._ToolActivityState()
        state.update("item-1", "execute_python", "started")
        state.update_progress("item-1", "创建节点 12/20")
        state.update_progress("item-1", "创建节点 20/20")

        self.assertEqual(1, state.total_count)
        details = state.detail_text()
        self.assertIn("进度：创建节点 20/20", details)
        self.assertNotIn("创建节点 12/20", details)

    def test_view_reuses_one_card_until_a_new_turn(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        view._turn_count = 1
        view._active_codex_card = None
        view._active_codex_text = ""
        view._active_codex_entry = None
        view._tool_activity_card = None
        view._tool_activity_entry = None
        view._protocol_streak_key = None
        view._protocol_streak_widget = None
        view._protocol_streak_entry = None
        view._transcript = []
        inserted: list[object] = []
        view._insert_before_stretch = inserted.append
        view._scroll_to_bottom = lambda: None
        view._add_aligned_widget = lambda *_args: None
        view._add_turn_divider = lambda: None

        with (
            mock.patch.object(
                conversation_module,
                "_ToolActivityCard",
                _HeadlessToolCard,
            ),
            mock.patch.object(
                conversation_module,
                "_MessageCard",
                _HeadlessMessageCard,
            ),
        ):
            view.update_tool_activity("item-1", "create_node", "started")
            view.update_tool_activity("item-1", "create_node", "completed")
            view.update_tool_activity("item-2", "set_parameters", "started")

            self.assertEqual(len(inserted), 1)
            self.assertEqual(len(view._transcript), 1)
            self.assertEqual(view._transcript[0]["role"], "tool_activity")
            self.assertEqual(view._transcript[0]["total"], 2)

            view.add_user_message("同一 Turn 的追加要求", (), same_turn=True)
            view.update_tool_progress("item-2", "继续执行")
            self.assertEqual(len(inserted), 1)
            self.assertEqual(view._transcript[0]["total"], 2)
            self.assertIn("继续执行", view._transcript[0]["details"])

            view.add_user_message("下一轮", ())
            view.update_tool_activity("item-3", "execute_python", "started")

        self.assertEqual(len(inserted), 2)
        tool_entries = [
            entry
            for entry in view._transcript
            if entry.get("role") == "tool_activity"
        ]
        self.assertEqual(len(tool_entries), 2)
        self.assertEqual(tool_entries[1]["total"], 1)
        self.assertFalse(any(entry.get("role") == "system" for entry in tool_entries))


if __name__ == "__main__":
    unittest.main()
