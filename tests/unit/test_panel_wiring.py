from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
import types
import unittest
from collections import deque
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
sys.path.insert(0, str(PANEL_LIB_ROOT))

from hia_panel.turn_state import PanelTurnState, TurnPhase, TurnStateToken  # noqa: E402


class _HeadlessQWidget:
    def closeEvent(self, event: Any) -> None:
        event.base_close_calls += 1


class _HeadlessTimer:
    @staticmethod
    def singleShot(_delay_ms: int, callback: Any) -> None:
        callback()


class _ManualTimer:
    def __init__(self, callback: Any) -> None:
        self._callback = callback
        self._active = False
        self.start_calls = 0
        self.stop_calls = 0

    def isActive(self) -> bool:  # noqa: N802
        return self._active

    def start(self) -> None:
        self._active = True
        self.start_calls += 1

    def stop(self) -> None:
        self._active = False
        self.stop_calls += 1

    def fire(self) -> None:
        if not self._active:
            return
        self._active = False
        self._callback()


class _HeadlessTextCursor:
    class MoveOperation:
        End = object()


def _slot(*_types: object, **_kwargs: object) -> Any:
    def decorator(function: Any) -> Any:
        return function

    return decorator


def _load_real_panel_class() -> type:
    """Load the real Panel module with the smallest possible Qt surface."""

    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_gui = types.ModuleType("PySide6.QtGui")
    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_core.Slot = _slot
    qt_core.QTimer = _HeadlessTimer
    qt_gui.QTextCursor = _HeadlessTextCursor
    qt_widgets.QWidget = _HeadlessQWidget
    pyside.QtCore = qt_core
    pyside.QtGui = qt_gui
    pyside.QtWidgets = qt_widgets

    bridge_module = types.ModuleType("hia_panel.bridge_client")
    bridge_module.BridgeClient = type("BridgeClient", (), {})
    replacements = {
        "PySide6": pyside,
        "PySide6.QtCore": qt_core,
        "PySide6.QtGui": qt_gui,
        "PySide6.QtWidgets": qt_widgets,
        "hia_panel.bridge_client": bridge_module,
    }
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in replacements}
    module_name = "hia_panel._headless_panel_wiring"
    saved_panel_module = sys.modules.get(module_name, missing)
    try:
        sys.modules.update(replacements)
        spec = importlib.util.spec_from_file_location(
            module_name,
            PANEL_LIB_ROOT / "hia_panel" / "panel.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load the real hia_panel.panel module")
        panel_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = panel_module
        spec.loader.exec_module(panel_module)
        return panel_module.HoudiniIntelligencePanel
    finally:
        if saved_panel_module is missing:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = saved_panel_module
        for name, original in saved.items():
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


HoudiniIntelligencePanel = _load_real_panel_class()


class _Widget:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._enabled = True
        self._visible = True
        self._items: list[tuple[str, Any]] = []
        self._current_index = -1
        self._signals_blocked = False
        self._checked = False
        self._tooltip = ""
        self._style_sheet = ""
        self.clear_focus_calls = 0

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:
        return self._enabled

    def setVisible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def isVisible(self) -> bool:
        return self._visible

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setToolTip(self, text: str) -> None:
        self._tooltip = text

    def toolTip(self) -> str:
        return self._tooltip

    def setStyleSheet(self, style_sheet: str) -> None:
        self._style_sheet = style_sheet

    def styleSheet(self) -> str:
        return self._style_sheet

    def setChecked(self, checked: bool) -> None:
        self._checked = bool(checked)

    def isChecked(self) -> bool:
        return self._checked

    def setPlainText(self, text: str) -> None:
        self._text = text

    def toPlainText(self) -> str:
        return self._text

    def insertPlainText(self, text: str) -> None:
        self._text += text

    def moveCursor(self, _operation: object) -> None:
        pass

    def clear(self) -> None:
        self._text = ""
        self._items.clear()
        self._current_index = -1

    def clearFocus(self) -> None:  # noqa: N802
        self.clear_focus_calls += 1

    def blockSignals(self, blocked: bool) -> bool:
        previous = self._signals_blocked
        self._signals_blocked = bool(blocked)
        return previous

    def addItem(self, label: str, data: Any = None) -> None:
        self._items.append((label, data))
        if self._current_index < 0:
            self._current_index = 0

    def count(self) -> int:
        return len(self._items)

    def currentData(self) -> Any:
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index][1]
        return None

    def setCurrentIndex(self, index: int) -> None:
        self._current_index = index

    def currentIndex(self) -> int:
        return self._current_index

    def itemText(self, index: int) -> str:
        return self._items[index][0]

    def itemData(self, index: int) -> Any:
        return self._items[index][1]


class _ConversationShim:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._active_codex_index: int | None = None
        self._tool_activity_index: int | None = None
        self._protocol_streak_key: str | None = None
        self._compaction_keys: set[str] = set()
        self._long_warning_shown = False
        self.stop_timer_calls = 0
        self.freeze_calls = 0

    def add_user_message(
        self,
        text: str,
        attachment_names: tuple[str, ...],
        *,
        same_turn: bool = False,
    ) -> None:
        self._protocol_streak_key = None
        self._active_codex_index = None
        if not same_turn:
            self._tool_activity_index = None
        self.entries.append(
            {
                "role": "user",
                "text": text,
                "attachments": tuple(attachment_names),
                "same_turn": same_turn,
            }
        )

    def begin_codex_message(self) -> None:
        self._protocol_streak_key = None
        self.entries.append({"role": "codex", "text": ""})
        self._active_codex_index = len(self.entries) - 1

    def append_codex_delta(self, delta: str) -> None:
        self._protocol_streak_key = None
        if self._active_codex_index is None:
            self.begin_codex_message()
        assert self._active_codex_index is not None
        self.entries[self._active_codex_index]["text"] += delta

    def finish_codex_message(self) -> None:
        self._active_codex_index = None
        self._protocol_streak_key = None

    def freeze_codex_message(self) -> None:
        self.freeze_calls += 1
        self._active_codex_index = None
        self._protocol_streak_key = None

    def add_system_message(self, text: str) -> None:
        self._protocol_streak_key = None
        self.entries.append({"role": "system", "text": text})

    def update_tool_activity(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: object = None,
    ) -> None:
        if self._tool_activity_index is None:
            self.entries.append(
                {
                    "role": "tool_activity",
                    "calls": {},
                    "collapsed": True,
                }
            )
            self._tool_activity_index = len(self.entries) - 1
        entry = self.entries[self._tool_activity_index]
        calls = entry["calls"]
        call = calls.setdefault(
            item_id,
            {"tool": tool_name, "status": status, "error": None, "progress": ""},
        )
        call["tool"] = tool_name or call["tool"]
        call["status"] = status
        if error is not None:
            call["error"] = error
        entry["total"] = len(calls)
        entry["failed"] = sum(
            value["status"] == "failed" for value in calls.values()
        )
        entry["collapsed"] = entry["failed"] == 0

    def update_tool_progress(self, item_id: str, message: str) -> None:
        if self._tool_activity_index is None:
            self.update_tool_activity(item_id, "未知工具", "started")
        assert self._tool_activity_index is not None
        self.entries[self._tool_activity_index]["calls"][item_id]["progress"] = message

    def add_protocol_warning(self, key: str, text: str) -> None:
        if self._protocol_streak_key == key and self.entries:
            entry = self.entries[-1]
            if entry.get("role") == "protocol" and entry.get("key") == key:
                entry["messages"].append(text)
                entry["count"] += 1
                return
        self.entries.append(
            {
                "role": "protocol",
                "key": key,
                "text": text,
                "messages": [text],
                "count": 1,
                "collapsed": True,
            }
        )
        self._protocol_streak_key = key

    def add_compaction_notice(self, key: str) -> None:
        if key in self._compaction_keys:
            return
        self._compaction_keys.add(key)
        self.entries.append(
            {
                "role": "context_compaction",
                "key": key,
                "text": "Codex 已自动整理较早的对话内容。",
            }
        )

    def show_long_thread_warning(self) -> None:
        if self._long_warning_shown:
            return
        self._long_warning_shown = True
        self.entries.append(
            {
                "role": "long_thread_warning",
                "text": (
                    "当前对话较长，早期细节可能逐渐减少。"
                    "开始不同任务时建议新建 Thread。"
                ),
            }
        )

    def stop_timers(self) -> None:
        self.stop_timer_calls += 1

    def toPlainText(self) -> str:  # noqa: N802
        rendered: list[str] = []
        for entry in self.entries:
            role = entry["role"]
            if role == "user":
                rendered.append(f"你: {entry['text']}")
            elif role == "codex":
                rendered.append(f"Codex: {entry['text']}")
            elif role == "protocol":
                rendered.append(
                    f"System: {entry['text']} (重复 {entry['count']} 次)"
                )
            elif role == "tool_activity":
                rendered.append(
                    "Houdini 工具活动："
                    f"共 {entry.get('total', 0)} 次，失败 {entry.get('failed', 0)} 次"
                )
            else:
                rendered.append(f"System: {entry['text']}")
        return "\n".join(rendered)


class _AttachmentStripShim:
    def __init__(self) -> None:
        self._paths: list[str] = []
        self._enabled = True

    def paths(self) -> list[str]:
        return list(self._paths)

    def add_path(self, path: str) -> bool:
        if path in self._paths:
            return False
        self._paths.append(path)
        return True

    def remove(self, path: str) -> bool:
        if path not in self._paths:
            return False
        self._paths.remove(path)
        return True

    def clear(self) -> None:
        self._paths.clear()

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:
        return self._enabled


class _BridgeClientShim:
    def __init__(self) -> None:
        self.turn_requests: list[
            tuple[str, str | None, str | None, list[str], str]
        ] = []
        self.steer_requests: list[tuple[str, list[str], str]] = []
        self.thread_requests: list[str | None] = []
        self.interrupt_contexts: list[str] = []
        self.session_contexts: list[str] = []
        self.model_requests = 0
        self.dispose_calls = 0
        self.capability_reports: list[dict[str, Any]] = []
        self.scene_polls: list[int] = []
        self.scene_results: list[tuple[str, str, dict[str, Any]]] = []
        self.start_turn_result: str | None = "turn-request"
        self.steer_turn_result: str | None = "steer-request"

    def start_thread(self, *, model: str | None) -> None:
        self.thread_requests.append(model)

    def start_turn(
        self,
        text: str,
        *,
        model: str | None,
        effort: str | None,
        local_image_paths: list[str],
        context: str,
    ) -> str | None:
        self.turn_requests.append(
            (text, model, effort, list(local_image_paths), context)
        )
        return self.start_turn_result

    def steer_turn(
        self,
        text: str,
        *,
        local_image_paths: list[str],
        context: str,
    ) -> str | None:
        self.steer_requests.append((text, list(local_image_paths), context))
        return self.steer_turn_result

    def get_models(self) -> None:
        self.model_requests += 1

    def interrupt(self, *, context: str) -> None:
        self.interrupt_contexts.append(context)

    def get_session(self, *, context: str) -> None:
        self.session_contexts.append(context)

    def dispose(self) -> None:
        self.dispose_calls += 1

    def publish_houdini_capabilities(self, report: dict[str, Any]) -> str:
        self.capability_reports.append(dict(report))
        return "capability-request"

    def poll_scene_work(self, wait_ms: int) -> str:
        self.scene_polls.append(wait_ms)
        return "work-poll"

    def complete_scene_work(
        self,
        request_id: str,
        executor_token: str,
        result: dict[str, Any],
    ) -> str:
        self.scene_results.append((request_id, executor_token, dict(result)))
        return "result-request"


class _ReadAdapterShim:
    def __init__(self) -> None:
        self.dispose_calls = 0
        self.execute_threads: list[int] = []
        self.refresh_report: dict[str, Any] | None = None

    def refresh(self) -> dict[str, Any]:
        if self.refresh_report is None:
            raise AssertionError("refresh_report was not configured")
        return dict(self.refresh_report)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        absolute_deadline: float | None,
    ) -> dict[str, Any]:
        self.execute_threads.append(__import__("threading").get_ident())
        return {
            "ok": True,
            "tool": tool_name,
            "request_id": arguments["request_id"],
            "deadline": absolute_deadline,
        }

    def dispose(self) -> None:
        self.dispose_calls += 1


class _CloseEvent:
    def __init__(self) -> None:
        self.base_close_calls = 0


class _TimerShim:
    def __init__(self) -> None:
        self.active = False
        self.start_delays: list[int] = []
        self.stop_calls = 0

    def start(self, delay_ms: int = 0) -> None:
        self.active = True
        self.start_delays.append(delay_ms)

    def stop(self) -> None:
        self.active = False
        self.stop_calls += 1


class _DialogShim:
    def __init__(self) -> None:
        self.close_calls = 0
        self.delete_later_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def deleteLater(self) -> None:
        self.delete_later_calls += 1


class _DiagnosticWriterShim:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._paths: dict[str, str] = {}

    def record(
        self,
        turn_key: str,
        *,
        snapshot: dict[str, Any],
        occurrence: dict[str, Any],
        slug: str,
    ) -> str:
        path = self._paths.setdefault(
            turn_key,
            str(
                REPOSITORY_ROOT
                / ".runtime"
                / "diagnostics"
                / f"20260718-120000-{slug}.md"
            ),
        )
        self.records.append(
            {
                "turn_key": turn_key,
                "snapshot": dict(snapshot),
                "occurrence": dict(occurrence),
                "slug": slug,
                "path": path,
            }
        )
        return path

    def path_for(self, turn_key: str) -> str | None:
        return self._paths.get(turn_key)


def _make_panel() -> Any:
    panel = object.__new__(HoudiniIntelligencePanel)
    panel._pane_tab = None
    panel._hou_module = None
    panel._event_sequence = 0
    panel._polling_enabled = False
    panel._connected = True
    panel._mcp_backend = "hia_v2"
    panel._authenticated = True
    panel._selected_thread_id = "thread-1"
    panel._session_action_pending = False
    panel._turn_start_request_pending = False
    panel._interrupt_pending = False
    panel._turn_state = PanelTurnState()
    panel._turn_start_tokens = {}
    panel._pending_turn_drafts = {}
    panel._active_turn_start_context = None
    panel._turn_steer_request_pending = False
    panel._turn_steer_tokens = {}
    panel._pending_steer_drafts = {}
    panel._active_turn_steer_context = None
    panel._interrupt_tokens = {}
    panel._active_interrupt_context = None
    panel._stopping_turn_token = None
    panel._reconciliation_tokens = {}
    panel._models_requested = False
    panel._pending_approvals = deque()
    panel._current_approval = None
    panel._houdini_adapter = None
    panel._houdini_polling_enabled = False
    panel._scene_capability_pending = False
    panel._scene_work_pending = False
    panel._scene_attestation_digest = None
    panel._scene_catalog_digest = None
    panel._last_houdini_report = None
    panel._attested_houdini_report_identity = None
    panel._pending_houdini_report_identity = None
    panel._selected_node_paths = ()
    panel._attachment_dialog = None
    panel._diagnostic_turn_key = None
    panel._diagnostic_draft_key = None
    panel._diagnostic_snapshot = {}
    panel._diagnostic_tool_states = {}
    panel._diagnostic_event_errors = []
    panel._last_report_path = None
    panel._diagnostic_writer_error = None
    panel._stop_reconcile_timer = _ManualTimer(panel._reconcile_stopping_turn)
    panel._diagnostic_writer = _DiagnosticWriterShim()
    panel._scene_executor_token = "executor-secret"
    panel._poll_timer = _TimerShim()
    panel._houdini_heartbeat_timer = _TimerShim()
    panel._scene_work_timer = _TimerShim()
    panel._client = _BridgeClientShim()

    panel.connection_label = _Widget()
    panel.auth_label = _Widget()
    panel.thread_status_label = _Widget("Thread：thread-1")
    panel.turn_status_label = _Widget("Turn：空闲")
    panel.houdini_connection_label = _Widget("● Houdini：未连接")
    panel.houdini_mcp_label = _Widget("● HIA MCP V2：不可用")
    panel.native_hython_label = _Widget("● Native Hython：不可用")
    panel.houdini_scene_label = _Widget("场景版本：不可用  ·  未保存：不可用")
    panel.thread_id_edit = _Widget("thread-1")
    panel.new_thread_button = _Widget()
    panel.resume_thread_button = _Widget()
    panel.send_button = _Widget()
    panel.stop_button = _Widget()
    panel.conversation = _ConversationShim()
    panel.welcome_group = _Widget()
    panel.approval_group = _Widget()
    panel.approval_text = _Widget()
    panel.allow_button = _Widget()
    panel.deny_button = _Widget()
    panel.input_edit = _Widget()
    panel.add_image_button = _Widget()
    panel.report_issue_button = _Widget()
    panel.copy_report_path_button = _Widget()
    panel.attachment_strip = _AttachmentStripShim()
    panel.selection_label = _Widget("当前选择：无")
    panel.include_selection_checkbox = _Widget()
    panel.model_combo = _Widget()
    panel.model_combo.addItem("Codex 默认", None)
    panel.effort_combo = _Widget()
    panel.effort_combo.addItem("Codex 默认", None)
    panel._refresh_controls()
    return panel


def _start_active_turn(panel: Any, turn_number: int) -> tuple[str, str]:
    panel.input_edit.setPlainText(f"request {turn_number}")
    panel._send()
    _text, _model, _effort, _images, context = panel._client.turn_requests[-1]
    turn_id = f"turn-{turn_number}"
    panel._on_action_completed(
        context,
        {
            "ok": True,
            "thread_id": "thread-1",
            "turn_id": turn_id,
            "turn_active": True,
            "turn_status": "inProgress",
        },
    )
    return context, turn_id


def _completed_notification(turn_id: str, *, sequence: int = 1) -> dict[str, Any]:
    return {
        "seq": sequence,
        "type": "codex_notification",
        "method": "turn/completed",
        "params": {
            "threadId": "thread-1",
            "turn": {"id": turn_id, "status": "completed"},
        },
    }


def _available_houdini_report() -> dict[str, Any]:
    return {
        "available": True,
        "houdini_build": "21.0.440",
        "hip_session_id": "hip-session-1234567890",
        "scene_revision": 7,
        "observer_sequence": 1,
        "catalog": [
            {"canonical_type_name": type_name, "available": True}
            for type_name in (
                "Object/geo",
                "Sop/box",
                "Sop/transform",
                "Sop/merge",
                "Sop/null",
            )
        ],
    }


class PanelWiringTests(unittest.TestCase):
    def assert_idle_controls(self, panel: Any) -> None:
        self.assertEqual(TurnPhase.IDLE, panel._turn_state.phase)
        self.assertTrue(panel.new_thread_button.isEnabled())
        self.assertTrue(panel.resume_thread_button.isEnabled())
        self.assertTrue(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())

    def test_mcp_backend_initialization_defaults_to_hia_and_rejects_unknown(self) -> None:
        cases = (
            (None, "hia_v2"),
            ("", "hia_v2"),
            ("hia_v2", "hia_v2"),
            ("fxhoudini", "fxhoudini"),
            ("FXHoudiniMCP", None),
            (["hia_v2"], None),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    expected,
                    HoudiniIntelligencePanel._initial_mcp_backend(value),
                )

    def test_health_maps_selected_backend_to_one_existing_status_label(self) -> None:
        cases = (
            (
                "hia_v2",
                True,
                "● HIA MCP V2：可用",
                "#67c587",
                "HIA MCP V2 当前 Houdini 会话状态",
            ),
            (
                "hia_v2",
                False,
                "● HIA MCP V2：不可用",
                "#9aa0a8",
                "HIA MCP V2 当前 Houdini 会话状态",
            ),
            (
                "fxhoudini",
                True,
                "● FXHoudiniMCP：回退",
                "#67c587",
                "FXHoudiniMCP 1.3.0 兼容回退当前 Houdini 会话状态",
            ),
            (
                "fxhoudini",
                False,
                "● FXHoudiniMCP：不可用",
                "#9aa0a8",
                "FXHoudiniMCP 1.3.0 兼容回退当前 Houdini 会话状态",
            ),
        )
        for backend, available, expected_text, color, tooltip in cases:
            with self.subTest(backend=backend, available=available):
                panel = _make_panel()
                panel._on_health(
                    {
                        "houdini_mcp": {
                            "backend": backend,
                            "available": available,
                        },
                        "session": {
                            "connected": True,
                            "authentication": "authenticated",
                            "thread_id": "thread-1",
                            "turn_active": False,
                        },
                    }
                )
                self.assertEqual(expected_text, panel.houdini_mcp_label.text())
                self.assertIn(color, panel.houdini_mcp_label.styleSheet())
                self.assertEqual(tooltip, panel.houdini_mcp_label.toolTip())
                self.assertEqual(backend, panel._mcp_backend)

    def test_unknown_health_backend_fails_closed_without_echoing_payload(self) -> None:
        for backend in ("Bearer attacker-secret", ["hia_v2"], None):
            with self.subTest(backend=backend):
                panel = _make_panel()
                panel._set_mcp_status(backend, True)
                rendered = panel.houdini_mcp_label.text()
                self.assertEqual("● MCP：不可用", rendered)
                self.assertNotIn("attacker-secret", rendered)
                self.assertEqual("当前 Houdini MCP 后端状态", panel.houdini_mcp_label.toolTip())
                self.assertIn("#9aa0a8", panel.houdini_mcp_label.styleSheet())
                self.assertIsNone(panel._mcp_backend)

    def test_bridge_failures_and_process_exit_clear_mcp_availability(self) -> None:
        failure = {
            "structured_error": {
                "code": "NETWORK_ERROR",
                "message": "Bridge is unavailable",
            }
        }
        for context in ("health", "session"):
            with self.subTest(context=context):
                panel = _make_panel()
                panel._set_mcp_status("hia_v2", True)
                panel._on_request_failed(context, failure)
                self.assertEqual(
                    "● HIA MCP V2：不可用",
                    panel.houdini_mcp_label.text(),
                )
                self.assertIn("#9aa0a8", panel.houdini_mcp_label.styleSheet())

        panel = _make_panel()
        panel._set_mcp_status("fxhoudini", True)
        panel._render_event({"type": "process_exit"})
        self.assertEqual("● FXHoudiniMCP：不可用", panel.houdini_mcp_label.text())
        self.assertIn("#9aa0a8", panel.houdini_mcp_label.styleSheet())

    def test_dirty_status_reads_current_houdini_session(self) -> None:
        panel = _make_panel()
        panel.houdini_scene_label = _Widget()
        report = _available_houdini_report()

        for dirty, expected in ((False, "未保存：否"), (True, "未保存：是")):
            with self.subTest(dirty=dirty):
                panel._hou_module = types.SimpleNamespace(
                    hipFile=types.SimpleNamespace(
                        hasUnsavedChanges=lambda value=dirty: value
                    )
                )
                panel._update_houdini_status(report)
                self.assertIn(expected, panel.houdini_scene_label.text())

        panel._hou_module = None
        panel._update_houdini_status(report)
        self.assertIn("未保存：不可用", panel.houdini_scene_label.text())

        panel._hou_module = types.SimpleNamespace(
            hipFile=types.SimpleNamespace(
                hasUnsavedChanges=mock.Mock(side_effect=RuntimeError("unavailable"))
            )
        )
        panel._update_houdini_status(report)
        self.assertIn("未保存：不可用", panel.houdini_scene_label.text())

    def test_scene_status_uses_plain_labels_and_explains_both_values(self) -> None:
        panel_source = (
            PANEL_LIB_ROOT / "hia_panel" / "panel.py"
        ).read_text(encoding="utf-8")

        self.assertIn("场景版本：不可用  ·  未保存：不可用", panel_source)
        self.assertIn("场景版本是当前 Houdini 会话内检测到的场景变化计数", panel_source)
        self.assertIn("未保存表示当前 HIP 是否有尚未保存的修改", panel_source)
        self.assertNotIn("Revision：不可用  ·  Dirty：不可用", panel_source)

    def test_successful_turn_start_and_steer_each_clear_focus_once(self) -> None:
        panel = _make_panel()

        _context, _turn_id = _start_active_turn(panel, 1)
        self.assertEqual(1, panel.input_edit.clear_focus_calls)

        panel.input_edit.setPlainText("继续调整材质")
        panel._send()
        self.assertEqual(2, panel.input_edit.clear_focus_calls)

        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel._on_action_completed(
            steer_context,
            {"thread_id": "thread-1", "turn_id": _turn_id},
        )
        self.assertEqual(2, panel.input_edit.clear_focus_calls)

    def test_conversation_roles_and_streaming_share_one_codex_card(self) -> None:
        panel = _make_panel()

        panel._add_user_message("生成一个可编辑模型", ())
        panel._begin_codex_message()
        panel._append_codex_delta("正在")
        panel._append_system("实时 MCP 可用")
        panel._append_codex_delta("处理")

        self.assertEqual(
            ["user", "codex", "system"],
            [entry["role"] for entry in panel.conversation.entries],
        )
        codex_entries = [
            entry
            for entry in panel.conversation.entries
            if entry["role"] == "codex"
        ]
        self.assertEqual(1, len(codex_entries))
        self.assertEqual("正在处理", codex_entries[0]["text"])
        self.assertIn("你: 生成一个可编辑模型", panel.conversation.toPlainText())
        self.assertIn("Codex: 正在处理", panel.conversation.toPlainText())
        self.assertIn("System: 实时 MCP 可用", panel.conversation.toPlainText())

    def test_stream_events_are_correlated_to_the_active_thread_and_turn(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)

        base_event = {
            "type": "codex_notification",
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "stale-turn",
                "itemId": "agent-message-1",
                "delta": "stale",
            },
        }
        panel._render_event(base_event)
        self.assertNotIn("stale", panel.conversation.toPlainText())

        current_event = dict(base_event)
        current_event["params"] = dict(base_event["params"])
        current_event["params"]["turnId"] = turn_id
        current_event["params"]["delta"] = "current"
        panel._render_event(current_event)
        self.assertIn("current", panel.conversation.toPlainText())

    def test_stale_start_ack_and_notification_do_not_rebind_diagnostics(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        expected = dict(panel._diagnostic_snapshot)

        stale_context = "turn_start:stale"
        panel._turn_start_tokens[stale_context] = TurnStateToken(
            generation=0,
            revision=0,
            thread_id="thread-old",
            turn_id=None,
        )
        panel._on_action_completed(
            stale_context,
            {
                "thread_id": "thread-old",
                "turn_id": "turn-old",
                "turn_active": True,
            },
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-old",
                    "turn": {"id": "turn-old", "status": "inProgress"},
                },
            }
        )

        self.assertEqual(expected, panel._diagnostic_snapshot)
        self.assertEqual(turn_id, panel._diagnostic_snapshot["turn_id"])

    def test_mcp_lifecycle_and_progress_update_one_tool_activity_entry(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)

        def lifecycle(method: str, item: dict[str, Any]) -> None:
            panel._render_event(
                {
                    "type": "codex_notification",
                    "method": method,
                    "params": {
                        "threadId": "thread-1",
                        "turnId": turn_id,
                        "item": item,
                    },
                }
            )

        lifecycle(
            "item/started",
            {
                "id": "tool-1",
                "type": "mcpToolCall",
                "server": "houdini_intelligence",
                "tool": "set_parameters",
                "status": "inProgress",
            },
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/mcpToolCall/progress",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "tool-1",
                    "message": "更新参数 9/18",
                },
            }
        )
        lifecycle(
            "item/completed",
            {
                "id": "tool-1",
                "type": "mcpToolCall",
                "server": "houdini_intelligence",
                "tool": "set_parameters",
                "status": "completed",
            },
        )
        lifecycle(
            "item/started",
            {
                "id": "tool-2",
                "type": "mcpToolCall",
                "server": "houdini_intelligence",
                "tool": "set_parameters",
                "status": "inProgress",
            },
        )
        real_error = {"message": "hou.OperationFailed: invalid parameter"}
        lifecycle(
            "item/completed",
            {
                "id": "tool-2",
                "type": "mcpToolCall",
                "server": "houdini_intelligence",
                "tool": "set_parameters",
                "status": "failed",
                "error": real_error,
            },
        )

        tool_entries = [
            entry
            for entry in panel.conversation.entries
            if entry["role"] == "tool_activity"
        ]
        self.assertEqual(1, len(tool_entries))
        entry = tool_entries[0]
        self.assertEqual(2, entry["total"])
        self.assertEqual(1, entry["failed"])
        self.assertEqual("completed", entry["calls"]["tool-1"]["status"])
        self.assertEqual("更新参数 9/18", entry["calls"]["tool-1"]["progress"])
        self.assertEqual(real_error, entry["calls"]["tool-2"]["error"])
        self.assertFalse(entry["collapsed"])
        system_text = "\n".join(
            entry["text"]
            for entry in panel.conversation.entries
            if entry["role"] == "system"
        )
        self.assertNotIn("工具 ·", system_text)
        self.assertNotIn("工具进度", system_text)

    def test_active_turn_send_uses_real_steer_without_new_generation(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        before = panel._turn_state.capture_token()
        attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\follow-up.png"
        )

        self.assertEqual("追加指令", panel.send_button.text())
        self.assertTrue(panel.send_button.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertTrue(panel.add_image_button.isEnabled())
        self.assertFalse(panel.new_thread_button.isEnabled())
        self.assertFalse(panel.resume_thread_button.isEnabled())
        self.assertFalse(panel.model_combo.isEnabled())
        self.assertFalse(panel.effort_combo.isEnabled())

        panel.input_edit.setPlainText("把顶部再缩短一些")
        panel.attachment_strip.add_path(attachment)
        panel._send()

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertEqual(1, len(panel._client.steer_requests))
        text, images, steer_context = panel._client.steer_requests[0]
        self.assertEqual("把顶部再缩短一些", text)
        self.assertEqual([attachment], images)
        self.assertRegex(steer_context, r"^turn_steer:\d+:\d+:[0-9a-f]{32}$")
        self.assertEqual(before, panel._turn_state.capture_token())
        self.assertEqual("追加中…", panel.send_button.text())
        self.assertEqual(
            1,
            sum(entry["role"] == "user" for entry in panel.conversation.entries),
        )

        panel._on_action_completed(
            steer_context,
            {
                "ok": True,
                "thread_id": "thread-1",
                "turn_id": turn_id,
            },
        )

        self.assertEqual(before, panel._turn_state.capture_token())
        user_entries = [
            entry for entry in panel.conversation.entries if entry["role"] == "user"
        ]
        self.assertEqual(2, len(user_entries))
        self.assertTrue(user_entries[1]["same_turn"])
        self.assertEqual("把顶部再缩短一些", user_entries[1]["text"])
        self.assertEqual(("follow-up.png",), user_entries[1]["attachments"])
        self.assertEqual("", panel.input_edit.toPlainText())
        self.assertEqual([], panel.attachment_strip.paths())
        self.assertEqual("追加指令", panel.send_button.text())

    def test_active_session_snapshot_restores_stream_correlation_for_steer(self) -> None:
        panel = _make_panel()
        turn_id = "turn-restored-active"

        applied = panel._apply_session(
            {
                "connected": True,
                "authentication": "authenticated",
                "account": {"account": {"type": "chatgpt"}},
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "turn_status": "inProgress",
                "turn_active": True,
            },
            token=panel._turn_state.capture_token(),
            allow_followup=False,
        )

        self.assertTrue(applied)
        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual("thread-1", panel._stream_thread_id)
        self.assertEqual(turn_id, panel._stream_turn_id)
        self.assertEqual("追加指令", panel.send_button.text())
        self.assertEqual(f"thread-1:{turn_id}", panel._diagnostic_turn_key)
        self.assertEqual(turn_id, panel._diagnostic_snapshot["turn_id"])

        panel.input_edit.setPlainText("恢复后继续修改")
        panel._send()
        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel._on_action_completed(
            steer_context,
            {
                "ok": True,
                "thread_id": "thread-1",
                "turn_id": turn_id,
            },
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "restored-message",
                    "delta": "已继续处理",
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "item": {
                        "id": "restored-tool",
                        "type": "mcpToolCall",
                        "server": "houdini_intelligence",
                        "tool": "execute_python",
                        "status": "inProgress",
                    },
                },
            }
        )

        self.assertIn("已继续处理", panel.conversation.toPlainText())
        self.assertEqual(
            1,
            sum(
                entry["role"] == "tool_activity"
                for entry in panel.conversation.entries
            ),
        )

    def test_restored_active_turn_can_write_its_final_failure_report(self) -> None:
        panel = _make_panel()
        turn_id = "turn-restored-failure"
        panel._apply_session(
            {
                "connected": True,
                "authentication": "authenticated",
                "account": {"account": {"type": "chatgpt"}},
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "turn_status": "inProgress",
                "turn_active": True,
            },
            token=panel._turn_state.capture_token(),
            allow_followup=False,
        )

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "failed"},
                },
            }
        )

        self.assertEqual(1, len(panel._diagnostic_writer.records))
        self.assertEqual(
            f"thread-1:{turn_id}",
            panel._diagnostic_writer.records[0]["turn_key"],
        )

    def test_failed_steer_keeps_draft_and_shows_short_review_hint(self) -> None:
        panel = _make_panel()
        _context, _turn_id = _start_active_turn(panel, 1)
        attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\follow-up.webp"
        )
        panel.input_edit.setPlainText("继续调整材质")
        panel.attachment_strip.add_path(attachment)
        before = panel._turn_state.capture_token()

        panel._send()
        _text, _images, steer_context = panel._client.steer_requests[-1]
        self.assertEqual(2, panel.input_edit.clear_focus_calls)
        panel._on_request_failed(
            steer_context,
            {
                "structured_error": {
                    "code": "TURN_NOT_STEERABLE",
                    "message": "active review cannot be steered",
                    "details": {"turn_kind": "review"},
                }
            },
        )

        self.assertEqual(before, panel._turn_state.capture_token())
        self.assertEqual("继续调整材质", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertEqual("追加指令", panel.send_button.text())
        self.assertEqual(2, panel.input_edit.clear_focus_calls)
        self.assertIn("当前 review Turn 暂不能追加指令。", panel.conversation.toPlainText())
        self.assertNotIn("CODEX_RPC_ERROR", panel.conversation.toPlainText())
        self.assertEqual(
            1,
            sum(entry["role"] == "user" for entry in panel.conversation.entries),
        )

    def test_session_connection_snapshot_updates_compact_codex_status(self) -> None:
        panel = _make_panel()
        panel._diagnostic_turn_key = "thread-1:old-turn"
        panel._diagnostic_snapshot = {"turn_id": "old-turn"}
        panel._apply_session(
            {
                "connected": False,
                "authentication": "login_required",
                "turn_active": False,
            },
            token=panel._turn_state.capture_token(),
            allow_followup=False,
        )

        self.assertIn("Codex：未连接", panel.connection_label.text())
        self.assertIn("#9aa0a8", panel.connection_label.styleSheet())
        self.assertIsNone(panel._diagnostic_turn_key)
        self.assertEqual({}, panel._diagnostic_snapshot)

    def test_consecutive_protocol_warnings_collapse_into_one_status_entry(self) -> None:
        panel = _make_panel()
        warning = {
            "type": "protocol_warning",
            "code": "UNKNOWN_NOTIFICATION_IGNORED",
            "method": "future/notification",
            "message": "Recorded and ignored",
        }

        panel._render_event(warning)
        panel._render_event(warning)

        protocol_entries = [
            entry
            for entry in panel.conversation.entries
            if entry["role"] == "protocol"
        ]
        self.assertEqual(1, len(protocol_entries))
        self.assertEqual(2, protocol_entries[0]["count"])
        self.assertTrue(protocol_entries[0]["collapsed"])

        panel._append_system("status boundary")
        panel._render_event(warning)
        self.assertEqual(
            2,
            sum(
                entry["role"] == "protocol"
                for entry in panel.conversation.entries
            ),
        )

    def test_automatic_compaction_notifications_deduplicate_without_changing_turn(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._update_tool_activity("tool-1", "execute_python", "started")
        before_state = panel._turn_state.capture_token()
        active_codex_index = panel.conversation._active_codex_index
        active_tool_index = panel.conversation._tool_activity_index

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/compacted",
                "params": {"threadId": "thread-1", "turnId": turn_id},
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "item": {
                        "id": "compaction-1",
                        "type": "contextCompaction",
                        "status": "completed",
                    },
                },
            }
        )

        notices = [
            entry
            for entry in panel.conversation.entries
            if entry["role"] == "context_compaction"
        ]
        self.assertEqual(1, len(notices))
        self.assertEqual("Codex 已自动整理较早的对话内容。", notices[0]["text"])
        self.assertEqual(before_state, panel._turn_state.capture_token())
        self.assertEqual(active_codex_index, panel.conversation._active_codex_index)
        self.assertEqual(active_tool_index, panel.conversation._tool_activity_index)

    def test_long_thread_warning_is_short_deduplicated_and_hides_internal_fields(self) -> None:
        panel = _make_panel()
        warning = {
            "type": "codex_notification",
            "method": "warning",
            "params": {
                "code": "CONTEXT_WINDOW_LOW",
                "message": "Long conversation approaching context window",
                "threadId": "thread-secret",
            },
        }

        panel._render_event(warning)
        panel._render_event(warning)

        entries = [
            entry
            for entry in panel.conversation.entries
            if entry["role"] == "long_thread_warning"
        ]
        self.assertEqual(1, len(entries))
        self.assertEqual(
            "当前对话较长，早期细节可能逐渐减少。开始不同任务时建议新建 Thread。",
            entries[0]["text"],
        )
        rendered = panel.conversation.toPlainText()
        self.assertNotIn("thread-secret", rendered)
        self.assertNotIn("threadId", rendered)
        self.assertNotIn("{", rendered)

    def test_request_user_input_and_known_protocol_rejection_are_concise(self) -> None:
        panel = _make_panel()

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "warning",
                "params": {
                    "message": "item/tool/requestUserInput was rejected",
                    "threadId": "hidden-thread",
                },
            }
        )
        panel._render_event(
            {
                "type": "protocol_warning",
                "code": "UNKNOWN_SERVER_REQUEST_REJECTED",
                "method": "item/tool/requestUserInput",
                "message": "raw request payload rejected",
            }
        )
        panel._render_event(
            {
                "type": "protocol_warning",
                "code": "SERVER_REQUEST_REJECTED",
                "method": "future/interaction",
                "message": "raw internal details",
            }
        )

        rendered = panel.conversation.toPlainText()
        self.assertIn(
            "Codex 的额外提问在当前 Panel 中不可用；已继续采用合理默认值。",
            rendered,
        )
        self.assertIn(
            "Codex 请求了当前 Panel 不提供的额外提问；已安全忽略并继续。",
            rendered,
        )
        self.assertIn(
            "Codex 请求了当前稳定协议不支持的额外交互；已安全忽略。",
            rendered,
        )
        self.assertNotIn("hidden-thread", rendered)
        self.assertNotIn("raw request payload", rendered)
        self.assertNotIn("raw internal details", rendered)

    def test_failed_turn_keeps_text_and_attachments_for_retry(self) -> None:
        panel = _make_panel()
        attachment = r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\reference.png"
        panel.input_edit.setPlainText("参考图片修改当前场景")
        panel.attachment_strip.add_path(attachment)

        panel._send()
        _text, _model, _effort, images, context = panel._client.turn_requests[-1]
        self.assertEqual([attachment], images)
        self.assertEqual("发送中…", panel.send_button.text())
        self.assertEqual(1, panel.input_edit.clear_focus_calls)
        panel._on_request_failed(
            context,
            {
                "structured_error": {
                    "code": "TURN_START_FAILED",
                    "message": "not created",
                    "details": {
                        "thread_id": "thread-1",
                        "turn_created": False,
                        "turn_active": False,
                    },
                }
            },
        )

        self.assertEqual("参考图片修改当前场景", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertEqual(1, panel.input_edit.clear_focus_calls)
        records = panel._diagnostic_writer.records
        self.assertEqual(1, len(records))
        self.assertEqual("turn-start-failure", records[0]["slug"])
        self.assertEqual(
            "TURN_START_FAILED",
            records[0]["occurrence"]["error_code"],
        )

    def test_failed_turn_writes_one_final_runtime_report(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "warning",
                "params": {"message": "temporary renderer warning"},
            }
        )
        self.assertEqual([], panel._diagnostic_writer.records)

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "failed"},
                },
            }
        )

        records = panel._diagnostic_writer.records
        self.assertEqual(1, len(records))
        self.assertEqual("turn-failure", records[0]["slug"])
        self.assertEqual("TURN_FAILED", records[0]["occurrence"]["error_code"])
        self.assertEqual(
            ["temporary renderer warning"],
            records[0]["snapshot"]["warnings"],
        )
        self.assertIn("问题报告已保存：", panel.conversation.toPlainText())

    def test_final_execute_python_traceback_is_preserved_in_report(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        traceback_text = (
            "Traceback (most recent call last):\n"
            "  File \"<hom>\", line 3, in <module>\n"
            "hou.OperationFailed: invalid node"
        )

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "item": {
                        "id": "execute-1",
                        "type": "mcpToolCall",
                        "server": "houdini_intelligence",
                        "tool": "execute_python",
                        "status": "failed",
                        "error": {
                            "code": "EXECUTE_PYTHON_FAILED",
                            "message": traceback_text,
                        },
                    },
                },
            }
        )
        panel._render_event(_completed_notification(turn_id))

        records = panel._diagnostic_writer.records
        self.assertEqual(1, len(records))
        occurrence = records[0]["occurrence"]
        self.assertEqual("EXECUTE_PYTHON_FAILED", occurrence["error_code"])
        self.assertIn("hou.OperationFailed: invalid node", occurrence["traceback"])
        self.assertIn("execute_python", " ".join(records[0]["snapshot"]["tool_order"]))

    def test_manual_dissatisfaction_writes_report_and_exposes_copy_path(self) -> None:
        panel = _make_panel()
        panel.input_edit.setPlainText("生成结果比例不符合预期")

        panel._record_manual_issue()

        records = panel._diagnostic_writer.records
        self.assertEqual(1, len(records))
        self.assertTrue(records[0]["occurrence"]["manual"])
        self.assertEqual("主观质量反馈", records[0]["occurrence"]["stage"])
        self.assertTrue(panel.copy_report_path_button.isVisible())
        self.assertEqual(records[0]["path"], panel._last_report_path)

    def test_multiple_reports_for_one_turn_reuse_the_same_report_path(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "failed"},
                },
            }
        )
        panel._record_manual_issue()

        records = panel._diagnostic_writer.records
        self.assertEqual(2, len(records))
        self.assertEqual(records[0]["turn_key"], records[1]["turn_key"])
        self.assertEqual(records[0]["path"], records[1]["path"])

    def test_pre_turn_input_failure_merges_into_the_following_failed_turn(self) -> None:
        panel = _make_panel()
        panel.input_edit.setPlainText("use the selected reference")
        panel._record_pre_turn_issue(
            "图片复制",
            "ATTACHMENT_COPY_FAILED",
            "copy failed",
            attachment="reference.png",
        )
        first = panel._diagnostic_writer.records[0]

        _context, turn_id = _start_active_turn(panel, 1)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "failed"},
                },
            }
        )

        records = panel._diagnostic_writer.records
        self.assertEqual(2, len(records))
        self.assertEqual(first["turn_key"], records[1]["turn_key"])
        self.assertEqual(first["path"], records[1]["path"])
        self.assertEqual(turn_id, records[1]["snapshot"]["turn_id"])

    def test_successful_turn_creates_no_runtime_report(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)

        panel._render_event(_completed_notification(turn_id))

        self.assertEqual([], panel._diagnostic_writer.records)
        self.assertIsNone(panel._last_report_path)

    def test_scene_modified_does_not_treat_preexisting_dirty_as_turn_change(self) -> None:
        for initial_dirty, final_dirty, expected in (
            (True, True, "待确认"),
            (False, True, True),
        ):
            with self.subTest(initial_dirty=initial_dirty, final_dirty=final_dirty):
                panel = _make_panel()
                dirty = [initial_dirty]
                panel._hou_module = types.SimpleNamespace(
                    hipFile=types.SimpleNamespace(
                        hasUnsavedChanges=lambda: dirty[0]
                    )
                )
                panel._last_houdini_report = {"scene_revision": "不可用"}
                panel._diagnostic_snapshot = panel._new_diagnostic_snapshot(
                    thread_id="thread-1",
                    user_goal="modify scene",
                    attachment_paths=(),
                )

                dirty[0] = final_dirty
                panel._refresh_diagnostic_scene_result()

                self.assertEqual(
                    expected,
                    panel._diagnostic_snapshot["scene_modified"],
                )

    def test_switching_threads_clears_attachment_references_without_deleting(self) -> None:
        panel = _make_panel()
        attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\reference.png"
        )
        panel.attachment_strip.add_path(attachment)

        panel._on_action_completed(
            "session_resume",
            {"thread_id": "thread-2"},
        )

        self.assertEqual([], panel.attachment_strip.paths())
        self.assertEqual("thread-2", panel._selected_thread_id)

    def test_selection_and_multiple_images_reach_one_turn_request(self) -> None:
        panel = _make_panel()
        paths = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\one.png",
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\two.webp",
        )
        panel.attachment_strip.add_path(paths[0])
        panel.attachment_strip.add_path(paths[1])
        panel.include_selection_checkbox.setChecked(True)
        panel._hou_module = types.SimpleNamespace(
            selectedNodes=lambda: (
                types.SimpleNamespace(path=lambda: "/obj/geo1/box1"),
                types.SimpleNamespace(path=lambda: "/obj/geo2"),
            )
        )
        panel.input_edit.setPlainText("按参考图修改")

        panel._send()

        text, _model, _effort, images, _context = panel._client.turn_requests[-1]
        self.assertEqual(list(paths), images)
        self.assertIn("按参考图修改", text)
        self.assertIn("当前 Houdini 选择（只读上下文）", text)
        self.assertIn("- /obj/geo1/box1", text)
        self.assertIn("- /obj/geo2", text)
        self.assertEqual("当前选择：/obj/geo1/box1 等 2 个节点", panel.selection_label.text())
        user_entry = panel.conversation.entries[0]
        self.assertEqual("user", user_entry["role"])
        self.assertEqual(("one.png", "two.webp"), user_entry["attachments"])

    def test_compact_status_has_no_legacy_catalog_or_schema_labels(self) -> None:
        panel = _make_panel()
        panel._hou_module = types.SimpleNamespace(
            hipFile=types.SimpleNamespace(hasUnsavedChanges=lambda: False)
        )

        panel._update_houdini_status(_available_houdini_report())

        self.assertEqual("● Houdini：已连接", panel.houdini_connection_label.text())
        self.assertEqual(
            "场景版本：7  ·  未保存：否", panel.houdini_scene_label.text()
        )
        self.assertFalse(hasattr(panel, "houdini_catalog_label"))
        self.assertFalse(hasattr(panel, "houdini_schema_label"))
        self.assertFalse(hasattr(panel, "houdini_tools_label"))

    def test_close_event_only_disposes_local_client_and_is_repeat_safe(self) -> None:
        panel = _make_panel()
        client = panel._client
        conversation = panel.conversation
        dialog = _DialogShim()
        panel._attachment_dialog = dialog
        timers = (
            panel._poll_timer,
            panel._houdini_heartbeat_timer,
            panel._scene_work_timer,
        )
        for timer in timers:
            timer.start(25)
        event = _CloseEvent()
        panel._polling_enabled = True

        panel.closeEvent(event)
        panel.closeEvent(event)

        self.assertFalse(panel._polling_enabled)
        self.assertIsNone(panel._client)
        self.assertEqual(1, client.dispose_calls)
        self.assertEqual(2, event.base_close_calls)
        self.assertFalse(hasattr(client, "shutdown"))
        self.assertEqual(1, dialog.close_calls)
        self.assertIsNone(panel._attachment_dialog)
        self.assertEqual(2, conversation.stop_timer_calls)
        for timer in timers:
            self.assertFalse(timer.active)
            self.assertEqual(2, timer.stop_calls)

    def test_attachment_dialog_finished_releases_only_the_finished_dialog(self) -> None:
        panel = _make_panel()
        finished = _DialogShim()
        panel._attachment_dialog = finished

        panel._attachment_dialog_finished(0)

        self.assertIsNone(panel._attachment_dialog)
        self.assertEqual(1, finished.delete_later_calls)

        current = _DialogShim()
        panel._attachment_dialog = current
        panel._attachment_dialog_destroyed(finished)
        self.assertIs(current, panel._attachment_dialog)

    def test_b2_capability_status_and_work_stay_on_panel_thread(self) -> None:
        panel = _make_panel()
        adapter = _ReadAdapterShim()
        panel._houdini_adapter = adapter
        panel._houdini_polling_enabled = True
        panel._last_houdini_report = _available_houdini_report()
        panel._pending_houdini_report_identity = panel._houdini_report_identity(
            panel._last_houdini_report
        )
        scheduled: list[int] = []
        panel._schedule_scene_work_poll = scheduled.append

        panel._on_action_completed(
            "scene_capabilities",
            {
                "ok": True,
                "available": True,
                "attestation_digest": "a" * 64,
                "catalog_digest": "b" * 64,
                "observer_sequence": 1,
            },
        )
        self.assertEqual("a" * 64, panel._scene_attestation_digest)
        self.assertIn("场景版本：7", panel.houdini_scene_label.text())
        self.assertEqual([0], scheduled)

        panel._on_action_completed(
            "scene_work",
            {
                "ok": True,
                "work": {
                    "kind": "execute",
                    "request_id": "read-request-1",
                    "executor_token": "one-request-token",
                    "attestation_digest": "a" * 64,
                    "tool_name": "houdini_scene_info",
                    "arguments": {"request_id": "read-request-1"},
                    "absolute_deadline": 123.0,
                },
            },
        )
        self.assertEqual(1, len(panel._client.scene_results))
        request_id, executor_token, result = panel._client.scene_results[0]
        self.assertEqual("read-request-1", request_id)
        self.assertEqual("one-request-token", executor_token)
        self.assertTrue(result["ok"])
        self.assertEqual([__import__("threading").get_ident()], adapter.execute_threads)

    def test_b2_stale_work_is_not_executed_and_close_is_local(self) -> None:
        panel = _make_panel()
        adapter = _ReadAdapterShim()
        panel._houdini_adapter = adapter
        panel._houdini_polling_enabled = True
        panel._scene_attestation_digest = "c" * 64
        panel._last_houdini_report = _available_houdini_report()
        panel._schedule_scene_work_poll = lambda _delay: None

        panel._on_action_completed(
            "scene_work",
            {
                "ok": True,
                "work": {
                    "kind": "execute",
                    "request_id": "stale-read",
                    "executor_token": "one-request-token",
                    "attestation_digest": "d" * 64,
                    "tool_name": "houdini_scene_info",
                    "arguments": {"request_id": "stale-read"},
                    "absolute_deadline": 123.0,
                },
            },
        )
        self.assertEqual([], adapter.execute_threads)
        self.assertEqual([], panel._client.scene_results)
        self.assertIsNone(panel._scene_attestation_digest)

        client = panel._client
        event = _CloseEvent()
        panel.closeEvent(event)
        panel.closeEvent(event)
        self.assertEqual(1, adapter.dispose_calls)
        self.assertEqual(1, client.dispose_calls)
        self.assertFalse(hasattr(client, "shutdown"))

    def test_b2_local_report_is_pending_until_bridge_ack_and_failures_close(self) -> None:
        panel = _make_panel()
        report = _available_houdini_report()
        panel._last_houdini_report = report

        panel._update_houdini_status(report, attested=False, pending=True)
        self.assertIn("场景版本：7", panel.houdini_scene_label.text())

        for context in (
            "scene_capabilities",
            "scene_work",
            "scene_result:read-request-1",
        ):
            panel._pending_houdini_report_identity = panel._houdini_report_identity(
                report
            )
            panel._on_action_completed(
                "scene_capabilities",
                {
                    "available": True,
                    "attestation_digest": "a" * 64,
                    "catalog_digest": "b" * 64,
                    "observer_sequence": 1,
                },
            )
            self.assertEqual("a" * 64, panel._scene_attestation_digest)
            panel._on_request_failed(
                context,
                {
                    "structured_error": {
                        "code": "CAPABILITY_MISMATCH",
                        "message": "read capability changed",
                    }
                },
            )
            self.assertIsNone(panel._scene_attestation_digest)

    def test_b2_renewal_and_work_response_interleave_does_not_drop_claim(self) -> None:
        panel = _make_panel()
        adapter = _ReadAdapterShim()
        report = _available_houdini_report()
        adapter.refresh_report = report
        panel._houdini_adapter = adapter
        panel._houdini_polling_enabled = True
        panel._last_houdini_report = report
        panel._scene_attestation_digest = "a" * 64
        panel._scene_catalog_digest = "b" * 64
        panel._attested_houdini_report_identity = panel._houdini_report_identity(report)
        panel._scene_work_pending = True
        panel._update_houdini_status(report, attested=True)
        scheduled: list[int] = []
        panel._schedule_houdini_heartbeat = scheduled.append

        panel._houdini_heartbeat()

        self.assertEqual("a" * 64, panel._scene_attestation_digest)
        self.assertTrue(panel._scene_capability_pending)
        self.assertTrue(panel._scene_work_pending)
        self.assertEqual([report], panel._client.capability_reports)
        self.assertIn("场景版本：7", panel.houdini_scene_label.text())
        self.assertEqual([1_000], scheduled)

        panel._on_action_completed(
            "scene_work",
            {
                "work": {
                    "kind": "execute",
                    "request_id": "renewal-interleave-read",
                    "executor_token": "one-request-token",
                    "attestation_digest": "a" * 64,
                    "tool_name": "houdini_scene_info",
                    "arguments": {"request_id": "renewal-interleave-read"},
                    "absolute_deadline": 123.0,
                }
            },
        )
        self.assertEqual(1, len(adapter.execute_threads))
        self.assertEqual(1, len(panel._client.scene_results))

    def test_b2_changed_report_revokes_old_ui_attestation_until_ack(self) -> None:
        panel = _make_panel()
        adapter = _ReadAdapterShim()
        attested_report = _available_houdini_report()
        changed_report = _available_houdini_report()
        changed_report["scene_revision"] = 8
        changed_report["observer_sequence"] = 2
        adapter.refresh_report = changed_report
        panel._houdini_adapter = adapter
        panel._houdini_polling_enabled = True
        panel._last_houdini_report = attested_report
        panel._scene_attestation_digest = "a" * 64
        panel._scene_catalog_digest = "b" * 64
        panel._attested_houdini_report_identity = panel._houdini_report_identity(
            attested_report
        )
        panel._update_houdini_status(attested_report, attested=True)
        panel._schedule_houdini_heartbeat = lambda _delay: None

        panel._houdini_heartbeat()

        self.assertIsNone(panel._scene_attestation_digest)
        self.assertTrue(panel._scene_capability_pending)
        self.assertEqual([changed_report], panel._client.capability_reports)
        self.assertIn("场景版本：8", panel.houdini_scene_label.text())

    def test_b2_capability_ack_must_match_pending_report_and_full_catalog(self) -> None:
        panel = _make_panel()
        report = _available_houdini_report()
        panel._last_houdini_report = report
        panel._pending_houdini_report_identity = panel._houdini_report_identity(report)

        panel._on_action_completed(
            "scene_capabilities",
            {
                "available": True,
                "attestation_digest": "a" * 64,
                "catalog_digest": "b" * 64,
                "observer_sequence": 2,
            },
        )
        self.assertIsNone(panel._scene_attestation_digest)

        incomplete_report = _available_houdini_report()
        incomplete_report["catalog"] = incomplete_report["catalog"][:-1]
        panel._last_houdini_report = incomplete_report
        panel._pending_houdini_report_identity = panel._houdini_report_identity(
            incomplete_report
        )
        panel._on_action_completed(
            "scene_capabilities",
            {
                "available": True,
                "attestation_digest": "a" * 64,
                "catalog_digest": "b" * 64,
                "observer_sequence": 1,
            },
        )
        self.assertIsNone(panel._scene_attestation_digest)

    def test_b2_panel_publisher_is_nonce_bound_and_unique_per_instance(self) -> None:
        nonce = "process-nonce-0123456789abcdef"
        prefix = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:16]
        publisher_ids: list[str] = []

        class _CapturingAdapter:
            def __init__(
                self,
                _hou_module: Any,
                *,
                publisher_id: str,
                pyside_version: str,
                fingerprint_key: bytes,
            ) -> None:
                del pyside_version, fingerprint_key
                publisher_ids.append(publisher_id)

            def start(self) -> dict[str, Any]:
                return _available_houdini_report()

        method_globals = HoudiniIntelligencePanel._initialize_houdini_read_adapter.__globals__
        original_adapter = method_globals["HoudiniReadAdapter"]
        environment = {
            "HIA_SCENE_PROFILE": "p2-v-b2-read-only",
            "HIA_BRIDGE_LAUNCH_ID": "bridge-launch-1",
            "HIA_BRIDGE_GENERATION": "1",
            "HIA_HOUDINI_PROCESS_NONCE": nonce,
            "HIA_HOUDINI_SCHEMA_VERSION": "0.2.0",
            "HIA_HOUDINI_SCHEMA_DIGEST": "c" * 64,
        }
        try:
            method_globals["HoudiniReadAdapter"] = _CapturingAdapter
            with mock.patch.dict(os.environ, environment, clear=False):
                first = _make_panel()
                second = _make_panel()
                first._initialize_houdini_read_adapter(object())
                second._initialize_houdini_read_adapter(object())
        finally:
            method_globals["HoudiniReadAdapter"] = original_adapter

        self.assertEqual(2, len(publisher_ids))
        self.assertNotEqual(publisher_ids[0], publisher_ids[1])
        pattern = re.compile(rf"^panel-{prefix}-[0-9a-f]{{16}}$")
        self.assertTrue(all(pattern.fullmatch(value) for value in publisher_ids))

    def test_no_active_interrupt_is_authoritative_after_final_delta(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "agent-message-1",
                    "delta": "final visible delta",
                },
            }
        )
        self.assertIn("final visible delta", panel.conversation.toPlainText())
        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)

        panel._stop()
        self.assertEqual(1, len(panel._client.interrupt_contexts))
        self.assertEqual("Turn：正在停止…", panel.turn_status_label.text())
        self.assertEqual(1, panel.conversation.freeze_calls)
        self.assertFalse(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())
        interrupt_context = panel._client.interrupt_contexts[0]
        self.assertRegex(interrupt_context, r"^interrupt:\d+:\d+$")
        panel._on_request_failed(
            interrupt_context,
            {
                "ok": False,
                "structured_error": {
                    "code": "NO_ACTIVE_TURN",
                    "message": "No interruptible active Turn is available",
                    "details": {
                        "thread_id": "thread-1",
                        "turn_status": "completed",
                        "turn_active": False,
                    },
                },
            },
        )

        self.assert_idle_controls(panel)
        rendered = panel.conversation.toPlainText()
        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertIn("Turn 已停止", rendered)
        self.assertNotIn("NO_ACTIVE_TURN", rendered)
        self.assertNotIn("No interruptible active Turn", rendered)

    def test_stop_freezes_visible_stream_and_sends_one_interrupt(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "agent-message-1",
                    "delta": "停止前文本",
                },
            }
        )

        panel._stop()
        panel._stop()
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "inProgress"},
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "agent-message-1",
                    "delta": "不应出现的迟到文本",
                },
            }
        )
        panel.input_edit.setPlainText("停止期间的草稿")
        panel._send()

        self.assertEqual(1, len(panel._client.interrupt_contexts))
        self.assertEqual(1, panel.conversation.freeze_calls)
        self.assertEqual("Turn：正在停止…", panel.turn_status_label.text())
        self.assertIn("停止前文本", panel.conversation.toPlainText())
        self.assertNotIn("不应出现的迟到文本", panel.conversation.toPlainText())
        self.assertEqual("停止期间的草稿", panel.input_edit.toPlainText())
        self.assertEqual([], panel._client.steer_requests)
        self.assertFalse(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())

    def test_interrupt_ack_schedules_one_delayed_session_reconciliation(self) -> None:
        panel = _make_panel()
        _context, _turn_id = _start_active_turn(panel, 1)
        panel._stop()
        interrupt_context = panel._client.interrupt_contexts[0]

        panel._on_action_completed(interrupt_context, {"ok": True})

        self.assertEqual([], panel._client.session_contexts)
        self.assertTrue(panel._stop_reconcile_timer.isActive())
        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertFalse(panel.send_button.isEnabled())
        panel._stop_reconcile_timer.fire()
        panel._stop_reconcile_timer.fire()
        self.assertEqual(1, len(panel._client.session_contexts))
        self.assertEqual(1, panel._stop_reconcile_timer.start_calls)

    def test_stop_reconciliation_still_active_is_static_and_keeps_draft(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._stop()
        interrupt_context = panel._client.interrupt_contexts[0]
        panel._on_action_completed(interrupt_context, {"ok": True})
        panel._stop_reconcile_timer.fire()
        reconcile_context = panel._client.session_contexts[0]
        panel.input_edit.setPlainText("仍可编辑的草稿")

        panel._on_action_completed(
            reconcile_context,
            {
                "ok": True,
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "account": {"account": {"type": "chatgpt"}},
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "inProgress",
                    "turn_active": True,
                },
            },
        )
        panel._send()

        self.assertEqual(
            "Turn：Codex/Houdini 工具仍在结束",
            panel.turn_status_label.text(),
        )
        self.assertEqual("仍可编辑的草稿", panel.input_edit.toPlainText())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertEqual([], panel._client.steer_requests)
        self.assertEqual(1, len(panel._client.session_contexts))

    def test_stop_reconciliation_idle_marks_stopped_and_restores_send(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._stop()
        interrupt_context = panel._client.interrupt_contexts[0]
        panel._on_action_completed(interrupt_context, {"ok": True})
        panel._stop_reconcile_timer.fire()
        reconcile_context = panel._client.session_contexts[0]
        panel.input_edit.setPlainText("停止后发送")

        panel._on_action_completed(
            reconcile_context,
            {
                "ok": True,
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "account": {"account": {"type": "chatgpt"}},
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "completed",
                    "turn_active": False,
                },
            },
        )

        self.assert_idle_controls(panel)
        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertIsNone(panel._stopping_turn_token)
        panel._send()
        self.assertEqual(2, len(panel._client.turn_requests))

    def test_late_stop_events_do_not_pollute_the_next_turn(self) -> None:
        panel = _make_panel()
        _context, first_turn_id = _start_active_turn(panel, 1)
        panel._stop()
        old_interrupt_context = panel._client.interrupt_contexts[0]
        panel._render_event(_completed_notification(first_turn_id, sequence=1))
        self.assert_idle_controls(panel)

        _second_context, second_turn_id = _start_active_turn(panel, 2)
        panel._on_action_completed(old_interrupt_context, {"ok": True})
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": first_turn_id,
                    "itemId": "old-message",
                    "delta": "旧 Turn 迟到文本",
                },
            }
        )
        panel._render_event(_completed_notification(first_turn_id, sequence=2))

        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual(second_turn_id, panel._turn_state.turn_id)
        self.assertNotIn("旧 Turn 迟到文本", panel.conversation.toPlainText())
        self.assertEqual([], panel._client.session_contexts)
        self.assertFalse(panel._stop_reconcile_timer.isActive())

    def test_completion_cancels_inflight_stop_reconciliation(self) -> None:
        panel = _make_panel()
        _context, first_turn_id = _start_active_turn(panel, 1)
        panel._stop()
        interrupt_context = panel._client.interrupt_contexts[0]
        panel._on_action_completed(interrupt_context, {"ok": True})
        panel._stop_reconcile_timer.fire()
        old_reconcile_context = panel._client.session_contexts[0]

        panel._render_event(_completed_notification(first_turn_id, sequence=1))
        self.assert_idle_controls(panel)
        self.assertEqual({}, panel._reconciliation_tokens)

        _second_context, second_turn_id = _start_active_turn(panel, 2)
        panel._on_action_completed(
            old_reconcile_context,
            {
                "ok": True,
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": first_turn_id,
                    "turn_status": "completed",
                    "turn_active": False,
                },
            },
        )

        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual(second_turn_id, panel._turn_state.turn_id)

    def test_unmatched_completion_and_gap_request_one_bounded_session_sync(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._on_events(
            {
                "events": [_completed_notification("turn-stale", sequence=1)],
                "gap": True,
            }
        )

        self.assertEqual(1, len(panel._client.session_contexts))
        reconcile_context = panel._client.session_contexts[0]
        self.assertRegex(
            reconcile_context,
            r"^session_reconcile:\d+:\d+:[a-z_]+$",
        )
        self.assertIn(reconcile_context, panel._reconciliation_tokens)
        self.assertFalse(panel.new_thread_button.isEnabled())
        self.assertFalse(panel.resume_thread_button.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())

        panel._on_action_completed(
            reconcile_context,
            {
                "ok": True,
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "account": {"account": {"type": "chatgpt"}},
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "completed",
                    "turn_active": False,
                },
            },
        )

        self.assert_idle_controls(panel)
        self.assertEqual([], list(panel._reconciliation_tokens))
        self.assertEqual(1, len(panel._client.session_contexts))

    def test_completion_before_ack_retries_one_stale_reconciliation(self) -> None:
        panel = _make_panel()
        panel.input_edit.setPlainText("completion before acknowledgement")
        panel._send()
        _text, _model, _effort, _images, start_context = (
            panel._client.turn_requests[-1]
        )
        turn_id = "turn-early-completion"

        panel._on_events(
            {
                "events": [_completed_notification(turn_id, sequence=1)],
                "gap": False,
            }
        )
        self.assertEqual(TurnPhase.STARTING, panel._turn_state.phase)
        self.assertEqual(1, len(panel._client.session_contexts))
        stale_context = panel._client.session_contexts[0]

        panel._on_action_completed(
            start_context,
            {
                "ok": True,
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)

        terminal_session = {
            "connected": True,
            "authentication": "authenticated",
            "account": {"account": {"type": "chatgpt"}},
            "thread_id": "thread-1",
            "turn_id": turn_id,
            "turn_status": "completed",
            "turn_active": False,
        }
        panel._on_action_completed(
            stale_context,
            {"ok": True, "session": terminal_session},
        )

        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual(2, len(panel._client.session_contexts))
        followup_context = panel._client.session_contexts[1]
        self.assertNotEqual(stale_context, followup_context)

        panel._on_action_completed(
            followup_context,
            {"ok": True, "session": terminal_session},
        )

        self.assert_idle_controls(panel)
        self.assertEqual(2, len(panel._client.session_contexts))
        self.assertEqual([], list(panel._reconciliation_tokens))

    def test_four_turns_complete_through_panel_callbacks(self) -> None:
        panel = _make_panel()
        for turn_number in range(1, 5):
            panel.input_edit.setPlainText(f"request {turn_number}")
            panel._send()
            self.assertEqual("发送中…", panel.send_button.text())
            _text, _model, _effort, _images, context = (
                panel._client.turn_requests[-1]
            )
            turn_id = f"turn-{turn_number}"
            panel._on_action_completed(
                context,
                {
                    "ok": True,
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_active": True,
                    "turn_status": "inProgress",
                },
            )
            self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
            self.assertTrue(panel.send_button.isEnabled())
            self.assertEqual("追加指令", panel.send_button.text())
            self.assertTrue(panel.stop_button.isEnabled())

            panel._on_events(
                {
                    "events": [
                        _completed_notification(turn_id, sequence=turn_number)
                    ],
                    "gap": False,
                }
            )
            self.assert_idle_controls(panel)
            self.assertEqual("发送", panel.send_button.text())

        self.assertEqual(4, len(panel._client.turn_requests))
        self.assertEqual([], panel._client.session_contexts)

    def test_passive_notifications_are_silent_but_warning_shows_method(self) -> None:
        panel = _make_panel()
        before = panel.conversation.toPlainText()
        passive_methods = (
            "remoteControl/status/changed",
            "mcpServer/startupStatus/updated",
            "account/rateLimits/updated",
        )
        panel._on_events(
            {
                "events": [
                    {
                        "seq": index,
                        "type": "codex_notification",
                        "method": method,
                        "params": {"status": "observed"},
                    }
                    for index, method in enumerate(passive_methods, start=1)
                ],
                "gap": False,
            }
        )

        self.assertEqual(before, panel.conversation.toPlainText())
        self.assertEqual([], panel._client.session_contexts)
        panel._render_event(
            {
                "type": "protocol_warning",
                "code": "UNKNOWN_NOTIFICATION_IGNORED",
                "method": "future/unknown/notification",
                "message": "Recorded and ignored",
            }
        )
        warning = panel.conversation.toPlainText()
        self.assertIn("UNKNOWN_NOTIFICATION_IGNORED", warning)
        self.assertIn("future/unknown/notification", warning)
        self.assertIn("协议提示：", warning)

    def test_unicode_model_and_effort_reach_turn_start_without_changes(self) -> None:
        panel = _make_panel()
        panel._on_action_completed(
            "models",
            {
                "models": [
                    {
                        "model": "catalog-model-one",
                        "displayName": "Catalog Model One",
                        "description": "Test model from model/list",
                        "isDefault": True,
                        "inputModalities": ["text", "image"],
                        "supportedReasoningEfforts": [
                            {
                                "reasoningEffort": "medium",
                                "description": "Balanced",
                            },
                            {
                                "reasoningEffort": "high",
                                "description": "More reasoning",
                            },
                        ],
                        "defaultReasoningEffort": "medium",
                    }
                ]
            },
        )
        original = "中文输入测试：请生成一张四条腿的桌子，尺寸为 120×60×75 厘米。"
        panel.input_edit.setPlainText(original)
        panel._send()

        text, model, effort, images, _context = panel._client.turn_requests[-1]
        self.assertEqual(original, text)
        self.assertEqual("catalog-model-one", model)
        self.assertEqual("medium", effort)
        self.assertEqual([], images)
        self.assertFalse(panel.model_combo.isEnabled())
        self.assertFalse(panel.effort_combo.isEnabled())

    def test_new_thread_uses_catalog_model_and_effort_updates_per_model(self) -> None:
        panel = _make_panel()
        panel._on_action_completed(
            "models",
            {
                "models": [
                    {
                        "model": "catalog-model-low",
                        "displayName": "Low Model",
                        "description": "",
                        "isDefault": False,
                        "inputModalities": ["text"],
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "low", "description": "Fast"}
                        ],
                        "defaultReasoningEffort": "low",
                    },
                    {
                        "model": "catalog-model-high",
                        "displayName": "High Model",
                        "description": "",
                        "isDefault": True,
                        "inputModalities": ["text"],
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "high", "description": "Deep"}
                        ],
                        "defaultReasoningEffort": "high",
                    },
                ]
            },
        )

        self.assertEqual("catalog-model-high", panel._selected_model_id())
        self.assertEqual("high", panel._selected_effort())
        panel.model_combo.setCurrentIndex(1)
        panel._on_model_changed(1)
        self.assertEqual("catalog-model-low", panel._selected_model_id())
        self.assertEqual("low", panel._selected_effort())
        panel._new_thread()
        self.assertEqual(["catalog-model-low"], panel._client.thread_requests)

    def test_model_list_failure_falls_back_without_blocking_chat(self) -> None:
        panel = _make_panel()
        panel._on_action_completed(
            "models",
            {
                "models": [
                    {
                        "model": "catalog-model",
                        "displayName": "Catalog Model",
                        "isDefault": True,
                        "supportedReasoningEfforts": [],
                        "defaultReasoningEffort": None,
                    }
                ]
            },
        )
        panel._on_request_failed(
            "models",
            {
                "ok": False,
                "structured_error": {
                    "code": "MODEL_LIST_UNAVAILABLE",
                    "message": "offline",
                },
            },
        )

        self.assertEqual(1, panel.model_combo.count())
        self.assertEqual("Codex 默认", panel.model_combo.itemText(0))
        self.assertIsNone(panel._selected_model_id())
        self.assertIsNone(panel._selected_effort())
        self.assertTrue(panel.send_button.isEnabled())
        self.assertIn("继续使用 Codex 默认", panel.conversation.toPlainText())

        panel.input_edit.setPlainText("fallback chat remains available")
        panel._send()
        text, model, effort, images, _context = panel._client.turn_requests[-1]
        self.assertEqual("fallback chat remains available", text)
        self.assertIsNone(model)
        self.assertIsNone(effort)
        self.assertEqual([], images)


if __name__ == "__main__":
    unittest.main()
