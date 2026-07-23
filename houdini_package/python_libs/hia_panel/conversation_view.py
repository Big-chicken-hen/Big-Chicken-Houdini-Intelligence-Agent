"""Native PySide6 conversation widgets for the Big-Chicken Houdini Intelligence Agent Panel."""

from __future__ import annotations

import json
from typing import Any, Iterable

from PySide6 import QtCore, QtGui, QtWidgets


_CODEX_CARD_WIDTH_RATIO = 0.82
_USER_CARD_WIDTH_RATIO = 0.68
_CONTENT_HORIZONTAL_MARGIN = 14
_STREAM_FLUSH_INTERVAL_MS = 40
_CODEX_PENDING_TEXT = (
    "Codex 正在处理；当前尚无文字输出。进度可在计划、工具和团队区域查看。"
)
_CODEX_NO_TEXT_REPLY = "本轮未返回文字回复。"
_COMPACTION_NOTICE_TEXT = "Codex 已自动整理较早的对话内容。"
_LONG_THREAD_WARNING_TEXT = (
    "当前对话较长，早期细节可能逐渐减少。开始不同任务时建议新建 Thread。"
)


class _ToolActivityState:
    """Per-turn tool activity keyed by the app-server item id."""

    _STATUS_LABELS = {
        "started": "进行中",
        "completed": "已完成",
        "failed": "失败",
    }

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, str]] = {}

    @property
    def total_count(self) -> int:
        return len(self._calls)

    @property
    def failed_count(self) -> int:
        return sum(call["status"] == "failed" for call in self._calls.values())

    def update(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: Any = None,
    ) -> None:
        normalized_id = str(item_id).strip()
        if not normalized_id:
            raise ValueError("Tool activity requires a non-empty item id")

        normalized_status = str(status).strip().lower()
        if normalized_status not in self._STATUS_LABELS:
            raise ValueError(f"Unsupported tool activity status: {status!r}")

        requested_name = str(tool_name).strip()
        call = self._calls.get(normalized_id)
        if call is None:
            call = {
                "item_id": normalized_id,
                "tool_name": requested_name or "未知工具",
                "status": normalized_status,
                "error": "",
                "progress": "",
            }
            self._calls[normalized_id] = call
        else:
            if requested_name:
                call["tool_name"] = requested_name
            call["status"] = normalized_status

        if normalized_status == "failed":
            if error is not None:
                call["error"] = self._format_error(error)
        elif error is not None:
            call["error"] = self._format_error(error)

    def update_progress(self, item_id: str, message: str) -> None:
        normalized_id = str(item_id).strip()
        normalized_message = str(message).strip()
        if not normalized_id or not normalized_message:
            return
        call = self._calls.get(normalized_id)
        if call is None:
            call = {
                "item_id": normalized_id,
                "tool_name": "未知工具",
                "status": "started",
                "error": "",
                "progress": "",
            }
            self._calls[normalized_id] = call
        call["progress"] = normalized_message

    def summary_text(self) -> str:
        return (
            f"Houdini 工具活动：共 {self.total_count} 次，"
            f"失败 {self.failed_count} 次"
        )

    def detail_text(self) -> str:
        grouped: dict[str, int] = {}
        for call in self._calls.values():
            name = call["tool_name"]
            grouped[name] = grouped.get(name, 0) + 1

        lines = [f"{name} × {count}" for name, count in grouped.items()]
        if self._calls:
            lines.extend(("", "调用明细"))
        for call in self._calls.values():
            status_label = self._STATUS_LABELS[call["status"]]
            lines.append(
                f"{call['tool_name']} [{call['item_id']}] — {status_label}"
            )
            if call["status"] == "failed" and call["error"]:
                lines.append(f"错误：{call['error']}")
            if call["progress"]:
                lines.append(f"进度：{call['progress']}")
        return "\n".join(lines)

    @staticmethod
    def _format_error(error: Any) -> str:
        if error is None:
            return ""
        if isinstance(error, str):
            return error
        try:
            return json.dumps(error, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(error)


class _MarkdownBody(QtWidgets.QTextBrowser):
    """A selectable Markdown view that grows inside the outer scroll area."""

    def __init__(self, *, foreground: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
            | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.setStyleSheet("QTextBrowser { background: transparent; border: 0; }")
        self.document().setDocumentMargin(0)
        self.document().setDefaultStyleSheet(
            "body { color: %s; } "
            "h1, h2, h3 { margin-top: 8px; margin-bottom: 4px; } "
            "p { margin-top: 2px; margin-bottom: 6px; } "
            "pre { background-color: #171b21; padding: 7px; white-space: pre; } "
            "code { font-family: Consolas, 'Courier New', monospace; } "
            "ul, ol { margin-top: 3px; margin-bottom: 5px; }" % foreground
        )
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._sync_height()
        )
        self._sync_height()

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(text)
        self._sync_height()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        document_height = self.document().documentLayout().documentSize().height()
        height = max(28, int(document_height + 2))
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.updateGeometry()


class _MessageCard(QtWidgets.QFrame):
    def __init__(
        self,
        role: str,
        title: str,
        *,
        background: str,
        border: str,
        foreground: str,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("hiaMessageCard")
        self.setProperty("messageRole", role)
        self.message_role = role
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self.setStyleSheet(
            "QFrame#hiaMessageCard {"
            f"background-color: {background}; border: 1px solid {border};"
            "border-radius: 8px;"
            "}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 9)
        layout.setSpacing(5)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("hiaMessageTitle")
        self.title_label.setStyleSheet(
            f"color: {foreground}; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self.title_label)

        self.body = _MarkdownBody(foreground=foreground)
        layout.addWidget(self.body)

        self.attachment_label = QtWidgets.QLabel()
        self.attachment_label.setObjectName("hiaMessageAttachments")
        self.attachment_label.setWordWrap(True)
        self.attachment_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.attachment_label.setStyleSheet("color: #aeb8c5; font-size: 10px;")
        self.attachment_label.hide()
        layout.addWidget(self.attachment_label)

    def set_attachments(self, names: Iterable[str]) -> None:
        attachment_names = [str(name) for name in names if str(name)]
        if not attachment_names:
            self.attachment_label.clear()
            self.attachment_label.hide()
            return
        self.attachment_label.setText("附件：" + "、".join(attachment_names))
        self.attachment_label.show()


class _ToolActivityCard(QtWidgets.QFrame):
    """One collapsible tool activity card for the current turn."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("hiaToolActivity")
        self.setStyleSheet(
            "QFrame#hiaToolActivity { background-color: #20252b;"
            " border: 1px solid #343c45; border-radius: 6px; }"
        )
        self._state = _ToolActivityState()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 6)
        layout.setSpacing(4)

        summary_row = QtWidgets.QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(5)

        self.summary_label = QtWidgets.QLabel(self._state.summary_text())
        self.summary_label.setObjectName("hiaToolActivitySummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.summary_label.setStyleSheet("color: #aeb7c2; font-size: 10px;")
        summary_row.addWidget(self.summary_label, 1)

        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setObjectName("hiaToolActivityToggle")
        self.toggle_button.setText("展开详情")
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setStyleSheet("font-size: 10px; color: #a7b0bc;")
        self.toggle_button.clicked.connect(self._toggle_details)
        summary_row.addWidget(self.toggle_button)
        layout.addLayout(summary_row)

        self.details = QtWidgets.QTextBrowser()
        self.details.setObjectName("hiaToolActivityDetails")
        self.details.setReadOnly(True)
        self.details.setOpenLinks(False)
        self.details.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.details.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.details.setMaximumHeight(220)
        self.details.setStyleSheet(
            "QTextBrowser { color: #aeb7c2; background: #1b2026;"
            " border: 0; padding: 5px; font-size: 10px; }"
        )
        self.details.hide()
        layout.addWidget(self.details)

    @property
    def state(self) -> _ToolActivityState:
        return self._state

    def update_activity(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: Any = None,
    ) -> None:
        normalized_status = str(status).strip().lower()
        self._state.update(item_id, tool_name, normalized_status, error)
        self.summary_label.setText(self._state.summary_text())
        self.details.setPlainText(self._state.detail_text())
        if normalized_status == "completed" and self._state.failed_count == 0:
            self.set_expanded(False)
        elif normalized_status == "failed":
            self.set_expanded(True)

    def update_progress(self, item_id: str, message: str) -> None:
        self._state.update_progress(item_id, message)
        self.summary_label.setText(self._state.summary_text())
        self.details.setPlainText(self._state.detail_text())

    def set_expanded(self, expanded: bool) -> None:
        self.details.setVisible(bool(expanded))
        self.toggle_button.setText("收起详情" if expanded else "展开详情")

    def _toggle_details(self) -> None:
        self.set_expanded(self.details.isHidden())


class _ProtocolWarning(QtWidgets.QFrame):
    def __init__(
        self,
        key: str,
        text: str,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("hiaProtocolWarning")
        self.setProperty("warningKey", key)
        self._messages = [text]

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 3, 7, 3)
        layout.setSpacing(3)

        summary_row = QtWidgets.QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(5)
        self.summary_label = QtWidgets.QLabel()
        self.summary_label.setObjectName("hiaProtocolWarningSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.summary_label.setStyleSheet("color: #8f98a4; font-size: 10px;")
        summary_row.addWidget(self.summary_label, 1)

        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setObjectName("hiaProtocolWarningToggle")
        self.toggle_button.setText("展开详情")
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setStyleSheet("font-size: 10px; color: #a7b0bc;")
        self.toggle_button.clicked.connect(self._toggle_details)
        self.toggle_button.hide()
        summary_row.addWidget(self.toggle_button)
        layout.addLayout(summary_row)

        self.details = QtWidgets.QTextBrowser()
        self.details.setObjectName("hiaProtocolWarningDetails")
        self.details.setReadOnly(True)
        self.details.setOpenLinks(False)
        self.details.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.details.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.details.setMaximumHeight(150)
        self.details.setStyleSheet(
            "QTextBrowser { color: #a5adb8; background: #20242a;"
            " border: 0; padding: 5px; font-size: 10px; }"
        )
        self.details.hide()
        layout.addWidget(self.details)
        self._refresh()

    @property
    def count(self) -> int:
        return len(self._messages)

    def add_occurrence(self, text: str) -> None:
        self._messages.append(text)
        self._refresh()

    def _refresh(self) -> None:
        count = len(self._messages)
        self.setProperty("warningCount", count)
        if count == 1:
            self.summary_label.setText(f"协议警告 · {self._messages[0]}")
            return
        self.summary_label.setText(
            f"协议警告 · 连续重复 {count} 次 · {self._messages[0]}"
        )
        self.details.setPlainText(
            "\n\n".join(
                f"{index}. {message}"
                for index, message in enumerate(self._messages, start=1)
            )
        )
        self.toggle_button.show()

    def _toggle_details(self) -> None:
        expanded = self.details.isHidden()
        self.details.setVisible(expanded)
        self.toggle_button.setText("收起详情" if expanded else "展开详情")


class ConversationView(QtWidgets.QWidget):
    """Scrollable native conversation view with streaming message cards."""

    newThreadRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("hiaConversationView")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setObjectName("hiaConversationScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer.addWidget(self.scroll_area)

        self._content = QtWidgets.QWidget()
        self._content.setObjectName("hiaConversationContent")
        self._layout = QtWidgets.QVBoxLayout(self._content)
        self._layout.setContentsMargins(7, 7, 7, 7)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.scroll_area.setWidget(self._content)

        self._turn_count = 0
        self._active_codex_card: _MessageCard | None = None
        self._active_codex_text = ""
        self._rendered_codex_text = ""
        self._active_codex_entry: dict[str, Any] | None = None
        self._codex_stream_frozen = False
        self._tool_activity_card: _ToolActivityCard | None = None
        self._tool_activity_entry: dict[str, Any] | None = None
        self._protocol_streak_key: str | None = None
        self._protocol_streak_widget: _ProtocolWarning | None = None
        self._protocol_streak_entry: dict[str, Any] | None = None
        self._message_cards: list[_MessageCard] = []
        self._compaction_notices: dict[str, QtWidgets.QLabel] = {}
        self._long_thread_warning: QtWidgets.QFrame | None = None
        self._transcript: list[dict[str, Any]] = []

        self._stream_flush_timer = QtCore.QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.setInterval(_STREAM_FLUSH_INTERVAL_MS)
        self._stream_flush_timer.timeout.connect(self._flush_codex_markdown)

        self._scroll_timer = QtCore.QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(0)
        self._scroll_timer.timeout.connect(self._perform_scroll_to_bottom)

    def is_empty(self) -> bool:
        return not self._transcript

    def clear_messages(self) -> None:
        """Clear only the visible conversation before showing another Thread."""

        self.stop_timers()
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._turn_count = 0
        self._active_codex_card = None
        self._active_codex_text = ""
        self._rendered_codex_text = ""
        self._active_codex_entry = None
        self._codex_stream_frozen = False
        self._tool_activity_card = None
        self._tool_activity_entry = None
        self._reset_protocol_streak()
        self._message_cards = []
        self._compaction_notices = {}
        self._long_thread_warning = None
        self._transcript = []

    def add_user_message(
        self,
        text: str,
        attachment_names: Iterable[str] = (),
        *,
        same_turn: bool = False,
    ) -> None:
        self._finish_active_codex_if_needed()
        if not same_turn:
            self._finish_tool_activity_if_needed()
            self._tool_activity_card = None
            self._tool_activity_entry = None
        self._reset_protocol_streak()
        if not same_turn:
            if self._turn_count:
                self._add_turn_divider()
            self._turn_count += 1

        names = tuple(str(name) for name in attachment_names if str(name))
        card = _MessageCard(
            "user",
            "你",
            background="#344353",
            border="#53697e",
            foreground="#f1f5f9",
        )
        card.body.set_markdown(str(text))
        card.set_attachments(names)
        self._register_message_card(card)
        self._add_aligned_widget(card, QtCore.Qt.AlignmentFlag.AlignRight)
        self._transcript.append(
            {"role": "user", "title": "你", "text": str(text), "attachments": names}
        )
        self._scroll_to_bottom()

    def begin_codex_message(self) -> None:
        self._finish_active_codex_if_needed()
        self._reset_protocol_streak()
        self._codex_stream_frozen = False
        card = _MessageCard(
            "codex",
            "Codex",
            background="#252a31",
            border="#3a424c",
            foreground="#e9edf2",
        )
        card.body.set_markdown(_CODEX_PENDING_TEXT)
        self._register_message_card(card)
        self._add_aligned_widget(card, QtCore.Qt.AlignmentFlag.AlignLeft)
        entry: dict[str, Any] = {"role": "codex", "title": "Codex", "text": ""}
        self._transcript.append(entry)
        self._active_codex_card = card
        self._active_codex_text = ""
        self._rendered_codex_text = ""
        self._active_codex_entry = entry
        self._scroll_to_bottom()

    def append_codex_delta(self, delta: str) -> None:
        if self._codex_stream_frozen:
            return
        delta_text = str(delta)
        if not delta_text:
            return
        self._reset_protocol_streak()
        if self._active_codex_card is None:
            self.begin_codex_message()
        first_delta = not self._active_codex_text
        self._active_codex_text += delta_text
        if self._active_codex_entry is not None:
            self._active_codex_entry["text"] = self._active_codex_text
        if first_delta:
            self._active_codex_card.body.set_markdown(self._active_codex_text)
            self._rendered_codex_text = self._active_codex_text
            self._scroll_to_bottom()
        elif not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def finish_codex_message(self) -> None:
        if self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
        if self._active_codex_card is not None and not self._active_codex_text:
            self._active_codex_card.body.set_markdown(_CODEX_NO_TEXT_REPLY)
            if self._active_codex_entry is not None:
                self._active_codex_entry["text"] = _CODEX_NO_TEXT_REPLY
        else:
            self._flush_codex_markdown()
        self._active_codex_card = None
        self._active_codex_text = ""
        self._rendered_codex_text = ""
        self._active_codex_entry = None
        self._finish_tool_activity_if_needed()
        self._reset_protocol_streak()

    def freeze_codex_message(self) -> None:
        """Render received text once, then stop stream updates and auto-scroll."""

        self._codex_stream_frozen = True
        if self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
        card = self._active_codex_card
        if card is not None and not self._active_codex_text:
            entry = self._active_codex_entry
            if entry in self._transcript:
                self._transcript.remove(entry)
            if card in self._message_cards:
                self._message_cards.remove(card)
            row = card.parentWidget()
            if row is not None:
                self._layout.removeWidget(row)
                row.deleteLater()
        elif card is not None and self._rendered_codex_text != self._active_codex_text:
            card.body.set_markdown(self._active_codex_text)
            self._rendered_codex_text = self._active_codex_text
        self._active_codex_card = None
        self._active_codex_text = ""
        self._rendered_codex_text = ""
        self._active_codex_entry = None
        if self._scroll_timer.isActive():
            self._scroll_timer.stop()

    def update_tool_activity(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: Any = None,
    ) -> None:
        """Create or update the current turn's single tool activity card."""

        self._reset_protocol_streak()
        if self._tool_activity_card is None:
            self._tool_activity_card = _ToolActivityCard()
            self._insert_before_stretch(self._tool_activity_card)
            self._tool_activity_entry = {
                "role": "tool_activity",
                "title": "Houdini 工具活动",
                "summary": "",
                "details": "",
            }
            self._transcript.append(self._tool_activity_entry)

        self._tool_activity_card.update_activity(
            item_id,
            tool_name,
            status,
            error,
        )
        if self._tool_activity_entry is not None:
            state = self._tool_activity_card.state
            self._tool_activity_entry.update(
                {
                    "summary": state.summary_text(),
                    "details": state.detail_text(),
                    "total": state.total_count,
                    "failed": state.failed_count,
                }
            )
        self._scroll_to_bottom()

    def finish_tool_activity(self) -> None:
        """Collapse the current turn's tool details without ending the turn."""

        self._finish_tool_activity_if_needed()

    def update_tool_progress(self, item_id: str, message: str) -> None:
        """Update one tool call's latest progress inside the current Turn card."""

        self._reset_protocol_streak()
        if self._tool_activity_card is None:
            self.update_tool_activity(item_id, "未知工具", "started")
        assert self._tool_activity_card is not None
        self._tool_activity_card.update_progress(item_id, message)
        if self._tool_activity_entry is not None:
            state = self._tool_activity_card.state
            self._tool_activity_entry.update(
                {
                    "summary": state.summary_text(),
                    "details": state.detail_text(),
                    "total": state.total_count,
                    "failed": state.failed_count,
                }
            )

    def add_system_message(self, text: str) -> None:
        self._reset_protocol_streak()
        label = QtWidgets.QLabel(f"System · {text}")
        label.setObjectName("hiaSystemStatus")
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setStyleSheet(
            "color: #89929e; background: transparent; font-size: 10px;"
            " padding: 2px 7px;"
        )
        self._insert_before_stretch(label)
        self._transcript.append(
            {"role": "system", "title": "System", "text": str(text)}
        )
        self._scroll_to_bottom()

    def add_protocol_warning(self, key: str, text: str) -> None:
        normalized_key = str(key)
        message = str(text)
        if (
            self._protocol_streak_key == normalized_key
            and self._protocol_streak_widget is not None
            and self._protocol_streak_entry is not None
        ):
            self._protocol_streak_widget.add_occurrence(message)
            self._protocol_streak_entry["messages"].append(message)
            self._scroll_to_bottom()
            return

        warning = _ProtocolWarning(normalized_key, message)
        self._insert_before_stretch(warning)
        entry: dict[str, Any] = {
            "role": "protocol",
            "title": "协议警告",
            "key": normalized_key,
            "messages": [message],
        }
        self._transcript.append(entry)
        self._protocol_streak_key = normalized_key
        self._protocol_streak_widget = warning
        self._protocol_streak_entry = entry
        self._scroll_to_bottom()

    def add_compaction_notice(self, key: str) -> None:
        """Show one passive notice for one app-server context compaction."""

        normalized_key = str(key).strip() or "context-compaction"
        if normalized_key in self._compaction_notices:
            return

        label = QtWidgets.QLabel(_COMPACTION_NOTICE_TEXT)
        label.setObjectName("hiaCompactionNotice")
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setStyleSheet(
            "color: #9aa4b0; background: transparent; font-size: 10px;"
            " padding: 3px 7px;"
        )
        self._insert_before_stretch(label)
        self._compaction_notices[normalized_key] = label
        self._transcript.append(
            {
                "role": "context_compaction",
                "title": "System",
                "text": _COMPACTION_NOTICE_TEXT,
            }
        )
        self._scroll_to_bottom()

    def show_long_thread_warning(self) -> None:
        """Show the one dismissible long-conversation hint for this view."""

        if self._long_thread_warning is not None:
            return

        warning = QtWidgets.QFrame()
        warning.setObjectName("hiaLongThreadWarning")
        warning.setStyleSheet(
            "QFrame#hiaLongThreadWarning { background-color: #2c2923;"
            " border: 1px solid #554b37; border-radius: 6px; }"
        )
        layout = QtWidgets.QHBoxLayout(warning)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(6)

        label = QtWidgets.QLabel(_LONG_THREAD_WARNING_TEXT)
        label.setObjectName("hiaLongThreadWarningText")
        label.setWordWrap(True)
        label.setStyleSheet("color: #c8bea9; font-size: 10px;")
        layout.addWidget(label, 1)

        new_thread_button = QtWidgets.QPushButton("新建 Thread")
        new_thread_button.setObjectName("hiaLongThreadNewThreadButton")
        new_thread_button.clicked.connect(self.newThreadRequested.emit)
        layout.addWidget(new_thread_button)

        dismiss_button = QtWidgets.QPushButton("关闭提示")
        dismiss_button.setObjectName("hiaLongThreadDismissButton")
        dismiss_button.clicked.connect(warning.hide)
        layout.addWidget(dismiss_button)

        self._long_thread_warning = warning
        self._insert_before_stretch(warning)
        self._transcript.append(
            {
                "role": "long_thread_warning",
                "title": "System",
                "text": _LONG_THREAD_WARNING_TEXT,
            }
        )
        self._scroll_to_bottom()

    def toPlainText(self) -> str:  # noqa: N802
        blocks: list[str] = []
        for entry in self._transcript:
            role = entry.get("role")
            title = str(entry.get("title", ""))
            if role == "protocol":
                messages = [str(message) for message in entry.get("messages", ())]
                text = "\n".join(messages)
            elif role == "tool_activity":
                text = str(entry.get("summary", ""))
            else:
                text = str(entry.get("text", ""))
            attachments = tuple(entry.get("attachments", ()))
            if attachments:
                text += "\n附件：" + "、".join(str(name) for name in attachments)
            blocks.append(f"{title}\n{text}" if title else text)
        return "\n\n".join(blocks)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_message_card_widths()

    def stop_timers(self) -> None:
        """Stop timers owned by this view before its Panel is closed."""

        self._stream_flush_timer.stop()
        self._scroll_timer.stop()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.stop_timers()
        super().closeEvent(event)

    def _register_message_card(self, card: _MessageCard) -> None:
        self._message_cards.append(card)
        self._update_message_card_width(card)

    def _update_message_card_widths(self) -> None:
        for card in self._message_cards:
            self._update_message_card_width(card)

    def _update_message_card_width(self, card: _MessageCard) -> None:
        viewport_width = max(1, int(self.scroll_area.viewport().width()))
        available_width = max(1, viewport_width - _CONTENT_HORIZONTAL_MARGIN)
        ratio = (
            _USER_CARD_WIDTH_RATIO
            if card.message_role == "user"
            else _CODEX_CARD_WIDTH_RATIO
        )
        target_width = max(1, int(available_width * ratio))
        card.setMinimumWidth(0)
        card.setMaximumWidth(target_width)

    def _flush_codex_markdown(self) -> None:
        card = self._active_codex_card
        if card is None or self._rendered_codex_text == self._active_codex_text:
            return
        card.body.set_markdown(self._active_codex_text)
        self._rendered_codex_text = self._active_codex_text
        self._scroll_to_bottom()

    def _finish_active_codex_if_needed(self) -> None:
        if self._active_codex_card is not None:
            self.finish_codex_message()

    def _finish_tool_activity_if_needed(self) -> None:
        if (
            self._tool_activity_card is not None
            and self._tool_activity_card.state.failed_count == 0
        ):
            self._tool_activity_card.set_expanded(False)

    def _reset_protocol_streak(self) -> None:
        self._protocol_streak_key = None
        self._protocol_streak_widget = None
        self._protocol_streak_entry = None

    def _add_turn_divider(self) -> None:
        divider = QtWidgets.QFrame()
        divider.setObjectName("hiaTurnDivider")
        divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        divider.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
        divider.setStyleSheet("color: #353b43; margin: 5px 16px;")
        self._insert_before_stretch(divider)

    def _add_aligned_widget(
        self, widget: QtWidgets.QWidget, alignment: QtCore.Qt.AlignmentFlag
    ) -> None:
        row = QtWidgets.QWidget()
        row.setObjectName("hiaMessageRow")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if alignment == QtCore.Qt.AlignmentFlag.AlignRight:
            row_layout.addStretch(1)
            row_layout.addWidget(widget, 4)
        else:
            row_layout.addWidget(widget, 4)
            row_layout.addStretch(1)
        self._insert_before_stretch(row)

    def _insert_before_stretch(self, widget: QtWidgets.QWidget) -> None:
        self._layout.insertWidget(max(0, self._layout.count() - 1), widget)

    def _scroll_to_bottom(self) -> None:
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _perform_scroll_to_bottom(self) -> None:
        scroll_bar = self.scroll_area.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
