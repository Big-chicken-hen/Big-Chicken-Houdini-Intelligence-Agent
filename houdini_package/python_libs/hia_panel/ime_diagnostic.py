"""Offline stock-widget IME diagnostic for the Houdini Python Panel host."""

from __future__ import annotations

import json
from typing import Any

from PySide6 import QtCore, QtWidgets


class ImeDiagnosticPanel(QtWidgets.QWidget):
    """Compare three unmodified Qt text widgets without any network activity."""

    def __init__(
        self,
        pane_tab: Any = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pane_tab = pane_tab

        self.line_edit = QtWidgets.QLineEdit()
        self.text_edit = QtWidgets.QTextEdit()
        self.plain_text_edit = QtWidgets.QPlainTextEdit()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.text_edit)
        layout.addWidget(self.plain_text_edit)

        self._last_state: dict[str, object] | None = None
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._emit_state_if_changed)
        self._status_timer.start()

    @staticmethod
    def _input_method_hints(widget: QtWidgets.QWidget) -> int:
        hints = widget.inputMethodHints()
        return int(getattr(hints, "value", hints))

    @staticmethod
    def _control_state(widget: QtWidgets.QWidget) -> dict[str, object]:
        return {
            "hasFocus": bool(widget.hasFocus()),
            "WA_InputMethodEnabled": bool(
                widget.testAttribute(
                    QtCore.Qt.WidgetAttribute.WA_InputMethodEnabled
                )
            ),
            "inputMethodHints": ImeDiagnosticPanel._input_method_hints(widget),
        }

    def _focus_widget_name(self) -> str | None:
        focused = QtWidgets.QApplication.focusWidget()
        if focused is self.line_edit:
            return "QLineEdit"
        if focused is self.text_edit:
            return "QTextEdit"
        if focused is self.plain_text_edit:
            return "QPlainTextEdit"
        if focused is None:
            return None
        return "other"

    def _diagnostic_state(self) -> dict[str, object]:
        input_method = QtWidgets.QApplication.inputMethod()
        return {
            "focusWidget": self._focus_widget_name(),
            "QLineEdit": self._control_state(self.line_edit),
            "QTextEdit": self._control_state(self.text_edit),
            "QPlainTextEdit": self._control_state(self.plain_text_edit),
            "inputMethod().isVisible": bool(input_method.isVisible()),
        }

    def _emit_state_if_changed(self) -> None:
        state = self._diagnostic_state()
        if state == self._last_state:
            return
        self._last_state = state
        print(json.dumps(state, ensure_ascii=False, sort_keys=True), flush=True)
