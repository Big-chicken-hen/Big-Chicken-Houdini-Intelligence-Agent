"""Local, one-shot Gate B4B acceptance Panel.

This is a deliberately thin UI over :mod:`hia_panel.b4b_acceptance`.  It has
no Bridge, MCP, network, worker, or automatic scene-operation path.  The user
must prepare and review the complete JSON approval, explicitly confirm one
Apply, invoke Houdini Undo manually, and then request a read-only verification.
"""

from __future__ import annotations

import json
from typing import Any

import PySide6
from PySide6 import QtCore, QtWidgets


_TARGET_PATH = "/obj/HIA_Graph_stairs_demo"
_PREPARED_STATE = "APPROVAL_PRESENTED"
_WAIT_UNDO_STATE = "WAIT_MANUAL_UNDO"
_VERIFIED_STATE = "VERIFIED"
_MANUAL_UNDO_GUIDANCE = (
    "先点击 Network View 或 Scene View，再使用 Houdini 主菜单 Edit > Undo，"
    "或在该视图中按 Ctrl+Z 一次；不要在本 Panel 的文本区域内按 Ctrl+Z，"
    "也不要 Redo。"
)

# Houdini can destroy and recreate a Python Panel widget without restarting its
# Python process.  The controller and one-shot latch therefore live at module
# scope: closing and reopening the pane never grants another Apply attempt.
_SHARED_CONTROLLER: Any | None = None
_SHARED_HOU_MODULE: Any | None = None
_APPLY_CONSUMED = False
_APPLY_ACCEPTANCE_PASSED = False
_MANUAL_UNDO_REQUIRED = False
_UNDO_VERIFIED = False


def _shared_controller(hou_module: Any) -> Any:
    global _SHARED_CONTROLLER, _SHARED_HOU_MODULE

    if _SHARED_CONTROLLER is None:
        from .b4b_acceptance import B4BAcceptanceController

        _SHARED_CONTROLLER = B4BAcceptanceController(
            hou_module,
            pyside_version=str(getattr(PySide6, "__version__", "unknown")),
        )
        _SHARED_HOU_MODULE = hou_module
    elif _SHARED_HOU_MODULE is not hou_module:
        raise RuntimeError("The B4B controller is already bound to another HOM module")
    return _SHARED_CONTROLLER


def _state_name(controller: Any) -> str:
    value = getattr(controller, "state", None)
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    enum_name = getattr(value, "name", None)
    return enum_name if isinstance(enum_name, str) else "UNAVAILABLE"


def _safe_failure_snapshot(operation: str) -> dict[str, Any]:
    return {
        "ok": False,
        "state": "FAILED",
        "message": f"{operation} failed inside the local B4B controller",
        "manual_undo_required": False,
        "baseline": None,
        "capability": None,
        "approval": None,
        "apply_result": None,
        "verification": None,
        "event_journal": [],
    }


def _unknown_write_state_snapshot(operation: str) -> dict[str, Any]:
    """Return a display-only report without claiming that no Undo is required."""

    return {
        "ok": False,
        "state": "WRITE_STATE_UNKNOWN",
        "message": (
            f"{operation} failed and no authoritative controller report is available. "
            "写入状态不确定；不会推断 manual_undo_required=false。"
            "不得重试 Apply；请保持当前 Houdini 会话并停止本次验收。"
        ),
        "manual_undo_required": None,
        "baseline": None,
        "capability": None,
        "approval": None,
        "apply_result": None,
        "verification": None,
        "event_journal": [],
    }


def _authoritative_controller_report(controller: Any) -> dict[str, Any] | None:
    """Read the controller-owned snapshot without exposing exception details."""

    try:
        report = controller.report
    except Exception:
        return None
    return report if isinstance(report, dict) else None


class B4BStairsAcceptancePanel(QtWidgets.QWidget):
    """Manual GUI for exactly one approved stairs apply and one manual Undo."""

    def __init__(
        self,
        pane_tab: Any = None,
        parent: QtWidgets.QWidget | None = None,
        *,
        hou_module: Any,
    ) -> None:
        super().__init__(parent)
        self._pane_tab = pane_tab
        self._controller: Any | None = None
        self._build_ui()

        try:
            self._controller = _shared_controller(hou_module)
        except Exception:
            self._show_snapshot(_safe_failure_snapshot("controller construction"))
            self.status_label.setText("Controller：不可用；未执行任何场景操作")
        else:
            report = getattr(self._controller, "report", None)
            if isinstance(report, dict):
                self._show_snapshot(report)
        self._refresh_controls()

    def _build_ui(self) -> None:
        self.setObjectName("hiaB4BStairsAcceptancePanel")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        title = QtWidgets.QLabel("P2-V Gate B4B：Stairs 单次本地验收")
        title.setStyleSheet("font-weight: bold;")
        root.addWidget(title)

        warning = QtWidgets.QLabel(
            "仅限空白、未保存、可丢弃的 HIP。目标固定为 "
            f"{_TARGET_PATH}。本 Houdini 进程只允许一次 Apply；不会自动 Undo。"
        )
        warning.setWordWrap(True)
        root.addWidget(warning)

        deadline = QtWidgets.QLabel(
            "Prepare 只读生成完整审批 JSON；审批窗口为 60 秒。"
            "超时、状态漂移或能力不匹配均 fail-closed，不会自动重试。"
        )
        deadline.setWordWrap(True)
        root.addWidget(deadline)

        undo_guidance = QtWidgets.QLabel(f"手动 Undo：{_MANUAL_UNDO_GUIDANCE}")
        undo_guidance.setWordWrap(True)
        root.addWidget(undo_guidance)

        self.state_label = QtWidgets.QLabel("State：NEW")
        self.status_label = QtWidgets.QLabel("尚未 Prepare；未执行任何场景操作")
        self.status_label.setWordWrap(True)
        root.addWidget(self.state_label)
        root.addWidget(self.status_label)

        self.report_view = QtWidgets.QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText(
            "Prepare 后在此显示完整 baseline、capability、normalized graph、"
            "digest、approval payload、apply/verification 与事件记录 JSON。"
        )
        root.addWidget(self.report_view, 1)

        self.confirm_checkbox = QtWidgets.QCheckBox(
            "我已完整核对上述 JSON，并确认只对 "
            f"{_TARGET_PATH} 执行一次 Apply；不保存 HIP。"
        )
        self.confirm_checkbox.setEnabled(False)
        root.addWidget(self.confirm_checkbox)

        buttons = QtWidgets.QHBoxLayout()
        self.prepare_button = QtWidgets.QPushButton("Prepare（只读）")
        self.apply_button = QtWidgets.QPushButton("Apply 一次（不可重试）")
        self.verify_button = QtWidgets.QPushButton("Verify Manual Undo（只读）")
        buttons.addWidget(self.prepare_button)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.verify_button)
        root.addLayout(buttons)

        self.prepare_button.clicked.connect(self._prepare)
        self.apply_button.clicked.connect(self._apply_once)
        self.verify_button.clicked.connect(self._verify_manual_undo)
        self.confirm_checkbox.toggled.connect(self._refresh_controls)

    def _controller_state(self) -> str:
        if self._controller is None:
            return "UNAVAILABLE"
        return _state_name(self._controller)

    def _show_snapshot(self, snapshot: Any) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            snapshot = _safe_failure_snapshot("controller response validation")
        self.report_view.setPlainText(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
        )
        state = snapshot.get("state")
        if isinstance(state, str):
            self.state_label.setText(f"State：{state}")
        message = snapshot.get("message")
        if isinstance(message, str) and message:
            self.status_label.setText(message)
        return snapshot

    @QtCore.Slot()
    def _prepare(self) -> None:
        if self._controller is None or _APPLY_CONSUMED:
            self._refresh_controls()
            return
        try:
            snapshot = self._controller.prepare()
        except Exception:
            snapshot = _authoritative_controller_report(self._controller)
        if not isinstance(snapshot, dict):
            snapshot = _safe_failure_snapshot("prepare")
        snapshot = self._show_snapshot(snapshot)
        self.confirm_checkbox.setChecked(False)
        self._refresh_controls()

    @QtCore.Slot()
    def _apply_once(self) -> None:
        global _APPLY_CONSUMED, _APPLY_ACCEPTANCE_PASSED
        global _MANUAL_UNDO_REQUIRED

        if (
            self._controller is None
            or _APPLY_CONSUMED
            or self._controller_state() != _PREPARED_STATE
            or not self.confirm_checkbox.isChecked()
        ):
            self._refresh_controls()
            return

        # Consume before crossing the controller boundary.  Failure, timeout,
        # close/reopen, or a raised exception must never grant a second attempt.
        _APPLY_CONSUMED = True
        self._refresh_controls()
        try:
            snapshot = self._controller.apply_once(confirmed=True)
        except Exception:
            snapshot = _authoritative_controller_report(self._controller)
        else:
            if not isinstance(snapshot, dict):
                snapshot = _authoritative_controller_report(self._controller)
        authoritative = isinstance(snapshot, dict)
        if not authoritative:
            snapshot = _unknown_write_state_snapshot("apply")
        snapshot = self._show_snapshot(snapshot)
        if not authoritative:
            _APPLY_ACCEPTANCE_PASSED = False
            self._refresh_controls()
            return
        _APPLY_ACCEPTANCE_PASSED = bool(
            snapshot.get("ok") is True
            and snapshot.get("state") == _WAIT_UNDO_STATE
        )
        _MANUAL_UNDO_REQUIRED = snapshot.get("manual_undo_required") is True
        if _MANUAL_UNDO_REQUIRED and _APPLY_ACCEPTANCE_PASSED:
            self.status_label.setText(
                "Apply 验证通过。" + _MANUAL_UNDO_GUIDANCE
                + "然后回到本 Panel 点击 Verify Manual Undo。"
            )
        elif _MANUAL_UNDO_REQUIRED:
            self.status_label.setText(
                "写入可能已经发生，但 Apply 验收证据未通过。"
                + _MANUAL_UNDO_GUIDANCE
                + "然后回到本 Panel 点击 Verify Manual Undo 进行清理验证；"
                "清理通过也不代表本次 Gate 验收成功。"
            )
        self._refresh_controls()

    @QtCore.Slot()
    def _verify_manual_undo(self) -> None:
        global _MANUAL_UNDO_REQUIRED, _UNDO_VERIFIED

        if (
            self._controller is None
            or not _MANUAL_UNDO_REQUIRED
            or _UNDO_VERIFIED
            or self._controller_state() != _WAIT_UNDO_STATE
        ):
            self._refresh_controls()
            return
        try:
            snapshot = self._controller.verify_manual_undo()
        except Exception:
            snapshot = _authoritative_controller_report(self._controller)
        else:
            if not isinstance(snapshot, dict):
                snapshot = _authoritative_controller_report(self._controller)
        authoritative = isinstance(snapshot, dict)
        if not authoritative:
            snapshot = _unknown_write_state_snapshot("manual Undo verification")
        snapshot = self._show_snapshot(snapshot)
        if not authoritative:
            self._refresh_controls()
            return
        _MANUAL_UNDO_REQUIRED = snapshot.get("manual_undo_required") is True
        verification = snapshot.get("verification")
        _UNDO_VERIFIED = bool(
            not _MANUAL_UNDO_REQUIRED
            and isinstance(verification, dict)
            and verification.get("ok") is True
        )
        if _UNDO_VERIFIED:
            if _APPLY_ACCEPTANCE_PASSED and snapshot.get("state") == _VERIFIED_STATE:
                self.status_label.setText(
                    "Manual Undo 只读验证通过；B4B 本地验收已结束。"
                )
            else:
                self.status_label.setText(
                    "Manual Undo 清理已由只读检查确认，但此前 Apply 验收"
                    "证据未通过；本次 Gate 不能标记成功。"
                )
        self._refresh_controls()

    @QtCore.Slot()
    def _refresh_controls(self) -> None:
        state = self._controller_state()
        self.state_label.setText(f"State：{state}")
        controller_available = self._controller is not None
        prepared = state == _PREPARED_STATE

        self.prepare_button.setEnabled(
            controller_available and state == "NEW" and not _APPLY_CONSUMED
        )
        self.confirm_checkbox.setEnabled(prepared and not _APPLY_CONSUMED)
        self.apply_button.setEnabled(
            prepared
            and not _APPLY_CONSUMED
            and self.confirm_checkbox.isChecked()
        )
        self.verify_button.setEnabled(
            controller_available
            and _MANUAL_UNDO_REQUIRED
            and not _UNDO_VERIFIED
            and state == _WAIT_UNDO_STATE
        )


__all__ = ["B4BStairsAcceptancePanel"]
