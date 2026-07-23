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

    class _Signal:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def connect(self, callback: object) -> None:
            self.callbacks.append(callback)

        def emit(self) -> None:
            for callback in self.callbacks:
                callback()

    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_gui = types.ModuleType("PySide6.QtGui")
    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_core.Qt = types.SimpleNamespace(
        AlignmentFlag=types.SimpleNamespace(
            AlignLeft=object(),
            AlignRight=object(),
        ),
        TextInteractionFlag=types.SimpleNamespace(
            TextSelectableByMouse=object(),
        ),
    )
    qt_core.Signal = lambda *_args, **_kwargs: _Signal()
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
    def __init__(self) -> None:
        self.markdown_updates: list[str] = []

    def set_markdown(self, _text: str) -> None:
        self.markdown_updates.append(_text)


class _HeadlessMessageCard:
    def __init__(self, role: str = "codex", *_args: object, **_kwargs: object) -> None:
        self.message_role = role
        self.body = _Body()
        self.minimum_width = 0
        self.maximum_width = 0

    def set_attachments(self, _names: object) -> None:
        pass

    def setMinimumWidth(self, width: int) -> None:  # noqa: N802
        self.minimum_width = width

    def setMaximumWidth(self, width: int) -> None:  # noqa: N802
        self.maximum_width = width

    def parentWidget(self) -> None:  # noqa: N802
        return None


class _LayoutMessageCard(_HeadlessMessageCard):
    def __init__(self, role: str, text: str) -> None:
        super().__init__(role)
        self.text = text
        self.size_hint_width = 280
        self.actual_width = 0
        self.markdown_height = 0

    def apply_layout_width(self, width: int) -> None:
        self.actual_width = max(
            self.minimum_width,
            min(self.maximum_width, int(width)),
        )
        content_width = max(1, self.actual_width - 22)
        characters_per_line = max(1, content_width // 7)
        lines = max(1, (len(self.text) + characters_per_line - 1) // characters_per_line)
        self.markdown_height = lines * 18


class _LayoutRow:
    def __init__(self) -> None:
        self.layout: _SimulatedHBoxLayout | None = None

    def setObjectName(self, _name: str) -> None:  # noqa: N802
        pass


class _SimulatedHBoxLayout:
    """Small Qt-layout analogue that preserves alignment sizing semantics."""

    def __init__(self, row: _LayoutRow) -> None:
        self.items: list[tuple[str, object | None, int, object | None]] = []
        row.layout = self

    def setContentsMargins(self, *_margins: int) -> None:  # noqa: N802
        pass

    def setSpacing(self, _spacing: int) -> None:  # noqa: N802
        pass

    def addStretch(self, stretch: int) -> None:  # noqa: N802
        self.items.append(("stretch", None, stretch, None))

    def addWidget(  # noqa: N802
        self,
        widget: _LayoutMessageCard,
        stretch: int = 0,
        alignment: object | None = None,
    ) -> None:
        self.items.append(("widget", widget, stretch, alignment))

    def activate(self, width: int) -> None:
        total_stretch = sum(item[2] for item in self.items)
        for kind, raw_widget, stretch, alignment in self.items:
            if kind != "widget" or not isinstance(raw_widget, _LayoutMessageCard):
                continue
            allocated = (
                raw_widget.size_hint_width
                if alignment is not None
                else int(width * stretch / max(1, total_stretch))
            )
            raw_widget.apply_layout_width(allocated)


class _HeadlessTimer:
    def __init__(self) -> None:
        self.active = False
        self.start_calls = 0
        self.stop_calls = 0

    def isActive(self) -> bool:  # noqa: N802
        return self.active

    def start(self) -> None:
        self.active = True
        self.start_calls += 1

    def stop(self) -> None:
        self.active = False
        self.stop_calls += 1


class _Viewport:
    def __init__(self, width: int) -> None:
        self.current_width = width

    def width(self) -> int:
        return self.current_width


class _ScrollArea:
    def __init__(self, width: int) -> None:
        self._viewport = _Viewport(width)

    def viewport(self) -> _Viewport:
        return self._viewport


def _make_stream_view() -> object:
    view = object.__new__(conversation_module.ConversationView)
    view._active_codex_card = None
    view._active_codex_text = ""
    view._rendered_codex_text = ""
    view._active_codex_entry = None
    view._codex_stream_frozen = False
    view._tool_activity_card = None
    view._protocol_streak_key = None
    view._protocol_streak_widget = None
    view._protocol_streak_entry = None
    view._message_cards = []
    view._transcript = []
    view._stream_flush_timer = _HeadlessTimer()
    view._scroll_timer = _HeadlessTimer()
    view.scroll_area = _ScrollArea(1000)
    view._add_aligned_widget = lambda *_args: None
    view._scroll_to_bottom = lambda: None
    return view


class _Label:
    def __init__(self, text: str) -> None:
        self.text = text

    def setObjectName(self, _name: str) -> None:  # noqa: N802
        pass

    def setWordWrap(self, _enabled: bool) -> None:  # noqa: N802
        pass

    def setTextInteractionFlags(self, _flags: object) -> None:  # noqa: N802
        pass

    def setStyleSheet(self, _style: str) -> None:  # noqa: N802
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
        view._rendered_codex_text = ""
        view._active_codex_entry = None
        view._tool_activity_card = None
        view._tool_activity_entry = None
        view._protocol_streak_key = None
        view._protocol_streak_widget = None
        view._protocol_streak_entry = None
        view._message_cards = []
        view._compaction_notices = {}
        view._long_thread_warning = None
        view._transcript = []
        view.scroll_area = _ScrollArea(1000)
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

    def test_stream_deltas_are_throttled_and_finish_flushes_every_character(self) -> None:
        view = _make_stream_view()
        scrolls: list[bool] = []
        view._scroll_to_bottom = lambda: scrolls.append(True)

        with mock.patch.object(
            conversation_module,
            "_MessageCard",
            _HeadlessMessageCard,
        ):
            view.begin_codex_message()
            card = view._active_codex_card
            entry = view._active_codex_entry
            self.assertIsNotNone(card)
            self.assertIsNotNone(entry)
            self.assertEqual(
                [conversation_module._CODEX_PENDING_TEXT],
                card.body.markdown_updates,
            )
            self.assertEqual("", entry["text"])

            view.append_codex_delta("自动")
            self.assertIs(card, view._active_codex_card)
            self.assertEqual(
                [conversation_module._CODEX_PENDING_TEXT, "自动"],
                card.body.markdown_updates,
            )
            view.append_codex_delta("整理")

        self.assertEqual("自动整理", entry["text"])
        self.assertEqual(1, view._stream_flush_timer.start_calls)

        view.finish_codex_message()

        self.assertEqual(
            [conversation_module._CODEX_PENDING_TEXT, "自动", "自动整理"],
            card.body.markdown_updates,
        )
        self.assertEqual([True, True, True], scrolls)
        self.assertEqual(1, view._stream_flush_timer.stop_calls)
        self.assertIsNone(view._active_codex_card)

    def test_no_delta_finish_is_honest_and_freeze_removes_placeholder(self) -> None:
        with mock.patch.object(
            conversation_module,
            "_MessageCard",
            _HeadlessMessageCard,
        ):
            completed_view = _make_stream_view()
            completed_view.begin_codex_message()
            completed_card = completed_view._active_codex_card
            completed_view.finish_codex_message()
            self.assertEqual(
                [
                    conversation_module._CODEX_PENDING_TEXT,
                    conversation_module._CODEX_NO_TEXT_REPLY,
                ],
                completed_card.body.markdown_updates,
            )
            self.assertEqual(
                conversation_module._CODEX_NO_TEXT_REPLY,
                completed_view._transcript[-1]["text"],
            )

            stopped_view = _make_stream_view()
            stopped_view.begin_codex_message()
            stopped_view.freeze_codex_message()
            stopped_view.append_codex_delta("迟到文本")
            self.assertEqual([], stopped_view._transcript)
            self.assertEqual([], stopped_view._message_cards)
            self.assertTrue(stopped_view._codex_stream_frozen)

    def test_freeze_flushes_without_scroll_and_blocks_late_delta(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        card = _HeadlessMessageCard("codex")
        entry = {"role": "codex", "text": "已收到"}
        stream_timer = _HeadlessTimer()
        scroll_timer = _HeadlessTimer()
        stream_timer.start()
        scroll_timer.start()
        view._active_codex_card = card
        view._active_codex_text = "已收到"
        view._rendered_codex_text = ""
        view._active_codex_entry = entry
        view._codex_stream_frozen = False
        view._stream_flush_timer = stream_timer
        view._scroll_timer = scroll_timer

        view.freeze_codex_message()
        view.append_codex_delta("迟到文本")

        self.assertEqual(["已收到"], card.body.markdown_updates)
        self.assertEqual("已收到", entry["text"])
        self.assertTrue(view._codex_stream_frozen)
        self.assertFalse(stream_timer.isActive())
        self.assertFalse(scroll_timer.isActive())
        self.assertIsNone(view._active_codex_card)

    def test_tool_progress_does_not_force_scroll_to_bottom(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        card = _HeadlessToolCard()
        card.state.update("tool-1", "execute_python", "started")
        view._tool_activity_card = card
        view._tool_activity_entry = {}
        view._protocol_streak_key = None
        view._protocol_streak_widget = None
        view._protocol_streak_entry = None
        scrolls: list[bool] = []
        view._scroll_to_bottom = lambda: scrolls.append(True)

        view.update_tool_progress("tool-1", "创建节点 8/12")

        self.assertEqual([], scrolls)
        self.assertIn("创建节点 8/12", view._tool_activity_entry["details"])

    def test_message_card_widths_follow_viewport_ratios_without_narrow_overflow(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        view.scroll_area = _ScrollArea(1000)
        codex_card = _HeadlessMessageCard("codex")
        user_card = _HeadlessMessageCard("user")
        codex_card.minimum_width = 900
        user_card.minimum_width = 700

        view._update_message_card_width(codex_card)
        view._update_message_card_width(user_card)

        self.assertEqual(0, codex_card.minimum_width)
        self.assertEqual(0, user_card.minimum_width)
        self.assertGreaterEqual(codex_card.maximum_width / 1000, 0.75)
        self.assertLessEqual(codex_card.maximum_width / 1000, 0.85)
        self.assertGreaterEqual(user_card.maximum_width / 1000, 0.60)
        self.assertLessEqual(user_card.maximum_width / 1000, 0.70)
        wide_codex_width = codex_card.maximum_width
        wide_user_width = user_card.maximum_width

        view.scroll_area.viewport().current_width = 20
        view._update_message_card_width(codex_card)
        view._update_message_card_width(user_card)
        self.assertEqual(0, codex_card.minimum_width)
        self.assertEqual(0, user_card.minimum_width)
        self.assertLessEqual(codex_card.maximum_width, 20)
        self.assertLessEqual(user_card.maximum_width, 20)
        self.assertLess(codex_card.maximum_width, wide_codex_width)
        self.assertLess(user_card.maximum_width, wide_user_width)

    def test_message_rows_allocate_real_width_and_reflow_after_resize(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        view.scroll_area = _ScrollArea(800)
        rows: list[_LayoutRow] = []
        view._insert_before_stretch = rows.append
        codex_card = _LayoutMessageCard("codex", "**阶段结果**\n\n" + "长文本 " * 240)
        user_card = _LayoutMessageCard("user", "请继续")

        with (
            mock.patch.object(
                conversation_module.QtWidgets,
                "QWidget",
                _LayoutRow,
                create=True,
            ),
            mock.patch.object(
                conversation_module.QtWidgets,
                "QHBoxLayout",
                _SimulatedHBoxLayout,
                create=True,
            ),
        ):
            view._update_message_card_width(codex_card)
            view._add_aligned_widget(
                codex_card,
                conversation_module.QtCore.Qt.AlignmentFlag.AlignLeft,
            )
            view._update_message_card_width(user_card)
            view._add_aligned_widget(
                user_card,
                conversation_module.QtCore.Qt.AlignmentFlag.AlignRight,
            )

        available_width = 800 - conversation_module._CONTENT_HORIZONTAL_MARGIN
        codex_layout = rows[0].layout
        user_layout = rows[1].layout
        self.assertIsNotNone(codex_layout)
        self.assertIsNotNone(user_layout)
        codex_layout.activate(available_width)
        user_layout.activate(available_width)

        self.assertIsNone(codex_layout.items[0][3])
        self.assertIsNone(user_layout.items[-1][3])
        self.assertGreaterEqual(codex_card.actual_width / available_width, 0.79)
        self.assertLessEqual(codex_card.actual_width / available_width, 0.82)
        self.assertGreater(codex_card.actual_width, codex_card.size_hint_width * 2)
        self.assertLessEqual(user_card.actual_width, int(available_width * 0.68))
        self.assertGreater(user_card.actual_width, user_card.size_hint_width)
        wide_markdown_height = codex_card.markdown_height

        view.scroll_area.viewport().current_width = 440
        view._update_message_card_width(codex_card)
        view._update_message_card_width(user_card)
        narrow_width = 440 - conversation_module._CONTENT_HORIZONTAL_MARGIN
        codex_layout.activate(narrow_width)
        user_layout.activate(narrow_width)

        self.assertLess(codex_card.actual_width, available_width * 0.5)
        self.assertGreater(codex_card.markdown_height, wide_markdown_height)
        self.assertLessEqual(user_card.actual_width, int(narrow_width * 0.68))

    def test_compaction_notice_deduplicates_without_touching_active_turn_state(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        active_card = object()
        active_tool_card = object()
        view._active_codex_card = active_card
        view._active_codex_text = "仍在生成"
        view._active_codex_entry = {"role": "codex", "text": "仍在生成"}
        view._tool_activity_card = active_tool_card
        view._tool_activity_entry = {"role": "tool_activity"}
        view._turn_count = 3
        view._compaction_notices = {}
        view._transcript = []
        inserted: list[object] = []
        scrolls: list[bool] = []
        view._insert_before_stretch = inserted.append
        view._scroll_to_bottom = lambda: scrolls.append(True)

        with mock.patch.object(
            conversation_module.QtWidgets,
            "QLabel",
            _Label,
            create=True,
        ):
            view.add_compaction_notice("compact-1")
            view.add_compaction_notice("compact-1")

        self.assertEqual(1, len(inserted))
        self.assertEqual(1, len(view._transcript))
        self.assertEqual("context_compaction", view._transcript[0]["role"])
        self.assertEqual(
            "Codex 已自动整理较早的对话内容。",
            view._transcript[0]["text"],
        )
        self.assertIs(active_card, view._active_codex_card)
        self.assertIs(active_tool_card, view._tool_activity_card)
        self.assertEqual(3, view._turn_count)
        self.assertEqual([True], scrolls)

    def test_stop_timers_stops_both_owned_timers(self) -> None:
        view = object.__new__(conversation_module.ConversationView)
        view._stream_flush_timer = _HeadlessTimer()
        view._scroll_timer = _HeadlessTimer()
        view._stream_flush_timer.start()
        view._scroll_timer.start()

        view.stop_timers()

        self.assertFalse(view._stream_flush_timer.isActive())
        self.assertFalse(view._scroll_timer.isActive())
        self.assertEqual(1, view._stream_flush_timer.stop_calls)
        self.assertEqual(1, view._scroll_timer.stop_calls)

    def test_source_keeps_code_scroll_and_only_safe_long_thread_actions(self) -> None:
        source = CONVERSATION_VIEW_PATH.read_text(encoding="utf-8")

        self.assertNotIn("_CARD_MAX_WIDTH", source)
        self.assertIn("ScrollBarAsNeeded", source)
        self.assertIn("white-space: pre;", source)
        self.assertNotIn("white-space: pre-wrap;", source)
        self.assertIn(
            "当前对话较长，早期细节可能逐渐减少。开始不同任务时建议新建 Thread。",
            source,
        )
        self.assertEqual(1, source.count('QtWidgets.QPushButton("新建 Thread")'))
        self.assertEqual(1, source.count('QtWidgets.QPushButton("关闭提示")'))
        self.assertNotIn("压缩并继续", source)
        self.assertNotIn("thread/compact/start", source)


if __name__ == "__main__":
    unittest.main()
