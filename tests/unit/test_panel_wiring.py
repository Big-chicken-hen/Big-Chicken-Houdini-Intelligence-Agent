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
        self.start_delays: list[int] = []
        self.stop_calls = 0

    def isActive(self) -> bool:  # noqa: N802
        return self._active

    def start(self, delay_ms: int = 0) -> None:
        self._active = True
        self.start_calls += 1
        self.start_delays.append(delay_ms)

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
    qt_core.Qt = types.SimpleNamespace(
        ItemDataRole=types.SimpleNamespace(ToolTipRole=object())
    )
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
        self._item_roles: dict[tuple[int, Any], Any] = {}
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
        self._item_roles.clear()
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

    def findData(self, data: Any) -> int:
        for index, (_label, value) in enumerate(self._items):
            if value == data:
                return index
        return -1

    def setItemData(self, index: int, data: Any, role: Any = None) -> None:
        if role is None:
            label, _old_data = self._items[index]
            self._items[index] = (label, data)
        else:
            self._item_roles[(index, role)] = data


class _ConversationShim:
    _PENDING_TEXT = (
        "Codex 正在处理；当前尚无文字输出。进度可在计划、工具和团队区域查看。"
    )
    _NO_TEXT_REPLY = "本轮未返回文字回复。"

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._active_codex_index: int | None = None
        self._tool_activity_index: int | None = None
        self._protocol_streak_key: str | None = None
        self._compaction_keys: set[str] = set()
        self._long_warning_shown = False
        self.stop_timer_calls = 0
        self.freeze_calls = 0
        self.clear_calls = 0

    def clear_messages(self) -> None:
        self.entries.clear()
        self._active_codex_index = None
        self._tool_activity_index = None
        self._protocol_streak_key = None
        self._compaction_keys.clear()
        self._long_warning_shown = False
        self.clear_calls += 1

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
        self.entries.append(
            {
                "role": "codex",
                "text": "",
                "display_text": self._PENDING_TEXT,
            }
        )
        self._active_codex_index = len(self.entries) - 1

    def append_codex_delta(self, delta: str) -> None:
        self._protocol_streak_key = None
        if self._active_codex_index is None:
            self.begin_codex_message()
        assert self._active_codex_index is not None
        entry = self.entries[self._active_codex_index]
        entry["text"] += delta
        entry.pop("display_text", None)

    def finish_codex_message(self) -> None:
        if self._active_codex_index is not None:
            entry = self.entries[self._active_codex_index]
            if not entry["text"]:
                entry["text"] = self._NO_TEXT_REPLY
                entry.pop("display_text", None)
        self._active_codex_index = None
        self._protocol_streak_key = None

    def freeze_codex_message(self) -> None:
        self.freeze_calls += 1
        if self._active_codex_index is not None:
            entry = self.entries[self._active_codex_index]
            if not entry["text"]:
                removed_index = self._active_codex_index
                self.entries.pop(removed_index)
                if (
                    self._tool_activity_index is not None
                    and self._tool_activity_index > removed_index
                ):
                    self._tool_activity_index -= 1
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
                rendered.append(
                    f"Codex: {entry.get('display_text', entry['text'])}"
                )
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
        self.thread_service_tiers: list[str | None] = []
        self.turn_service_tiers: list[str | None] = []
        self.resume_requests: list[tuple[str, str | None, str]] = []
        self.thread_list_requests = 0
        self.thread_read_requests: list[tuple[str, str]] = []
        self.thread_rename_requests: list[tuple[str, str, str]] = []
        self.goal_get_requests: list[str] = []
        self.goal_set_requests: list[tuple[str, str, str, int | None]] = []
        self.goal_clear_requests: list[str] = []
        self.focus_mode_requests: list[tuple[str, bool]] = []
        self.health_requests = 0
        self.houdini_status_requests = 0
        self.interrupt_contexts: list[str] = []
        self.session_contexts: list[str] = []
        self.model_requests = 0
        self.dispose_calls = 0
        self.capability_reports: list[dict[str, Any]] = []
        self.scene_polls: list[int] = []
        self.scene_results: list[tuple[str, str, dict[str, Any]]] = []
        self.approval_decisions: list[tuple[Any, str]] = []
        self.start_turn_result: str | None = "turn-request"
        self.steer_turn_result: str | None = "steer-request"

    def start_thread(
        self,
        *,
        model: str | None,
        service_tier: str | None,
    ) -> None:
        self.thread_requests.append(model)
        self.thread_service_tiers.append(service_tier)

    def resume_thread(
        self,
        thread_id: str,
        *,
        service_tier: str | None,
        context: str,
    ) -> None:
        self.resume_requests.append((thread_id, service_tier, context))

    def start_turn(
        self,
        text: str,
        *,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
        local_image_paths: list[str],
        context: str,
    ) -> str | None:
        self.turn_requests.append(
            (text, model, effort, list(local_image_paths), context)
        )
        self.turn_service_tiers.append(service_tier)
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

    def get_threads(self) -> None:
        self.thread_list_requests += 1

    def read_thread(self, thread_id: str, *, context: str) -> None:
        self.thread_read_requests.append((thread_id, context))

    def rename_thread(self, thread_id: str, name: str, *, context: str) -> None:
        self.thread_rename_requests.append((thread_id, name, context))

    def get_goal(self, thread_id: str) -> None:
        self.goal_get_requests.append(thread_id)

    def set_goal(
        self,
        objective: str,
        status: str,
        *,
        thread_id: str,
        token_budget: int | None,
    ) -> None:
        self.goal_set_requests.append(
            (thread_id, objective, status, token_budget)
        )

    def clear_goal(self, thread_id: str) -> None:
        self.goal_clear_requests.append(thread_id)

    def set_focus_mode(self, thread_id: str, enabled: bool) -> None:
        self.focus_mode_requests.append((thread_id, enabled))

    def get_health(self) -> str:
        self.health_requests += 1
        return "health-request"

    def get_houdini_status(self) -> str:
        self.houdini_status_requests += 1
        return "houdini-status-request"

    def interrupt(self, *, context: str) -> None:
        self.interrupt_contexts.append(context)

    def resolve_approval(self, request_id: Any, decision: str) -> str:
        self.approval_decisions.append((request_id, decision))
        return f"approval_{decision}"

    def get_session(self, *, context: str = "session") -> None:
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


def _make_panel(*, selected_thread_id: str | None = "thread-1") -> Any:
    panel = object.__new__(HoudiniIntelligencePanel)
    panel._pane_tab = None
    panel._hou_module = None
    panel._event_sequence = 0
    panel._polling_enabled = False
    panel._connected = True
    panel._mcp_backend = "hia_v2"
    panel._authenticated = True
    panel._selected_thread_id = selected_thread_id
    panel._crash_recovery_marker = None
    panel._crash_recovery_health_session = None
    panel._crash_recovery_goal_payload = None
    panel._crash_recovery_thread_payload = None
    panel._crash_recovery_observation = None
    panel._session_action_pending = False
    panel._turn_start_request_pending = False
    panel._interrupt_pending = False
    panel._turn_state = PanelTurnState()
    panel._stream_thread_id = None
    panel._stream_turn_id = None
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
    panel._stop_recovery_state = None
    panel._stopped_source_turn = None
    panel._reconciliation_tokens = {}
    panel._models_requested = False
    panel._models_resolved = True
    panel._threads_requested = False
    panel._thread_history = (
        [
            {
                "thread_id": selected_thread_id,
                "name": "Current thread",
                "preview": "",
                "updated_at": 1_752_825_600,
            }
        ]
        if isinstance(selected_thread_id, str)
        else []
    )
    panel._goal_action_context = None
    panel._current_goal = None
    panel._goal_turn_id = None
    panel._goal_turn_has_text = False
    panel._focus_mode = False
    panel._goal_continuation_paused = False
    panel._goal_continuation_boundary = None
    panel._goal_auto_turn_token = None
    panel._goal_auto_turn_has_progress = False
    panel._goal_continue_after_open_thread_id = None
    panel._team_records = {}
    panel._turn_performance_token = None
    panel._turn_performance_marks = {}
    panel._reconnect_attempt = 0
    panel._reconnecting = False
    panel._reconnect_exhausted_notice_shown = False
    panel._app_server_exit_notice_shown = False
    panel._pending_approvals = deque()
    panel._current_approval = None
    panel._current_approval_offers_persistent_rule = False
    panel._houdini_adapter = None
    panel._houdini_polling_enabled = False
    panel._local_houdini_polling_enabled = False
    panel._houdini_status_pending = False
    panel._houdini_status_turn_token = None
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
    panel._reconnect_timer = _ManualTimer(panel._attempt_bridge_reconnect)
    panel._diagnostic_writer = _DiagnosticWriterShim()
    panel._scene_executor_token = "executor-secret"
    panel._poll_timer = _TimerShim()
    panel._houdini_heartbeat_timer = _TimerShim()
    panel._scene_work_timer = _TimerShim()
    panel._client = _BridgeClientShim()

    panel.connection_label = _Widget()
    panel.auth_label = _Widget()
    panel.thread_status_label = _Widget(
        f"Thread：{selected_thread_id}"
        if isinstance(selected_thread_id, str)
        else "Thread：未选择"
    )
    panel.turn_status_label = _Widget("Turn：空闲")
    panel.houdini_connection_label = _Widget("● Houdini：未连接")
    panel.houdini_mcp_label = _Widget("● HIA MCP V2：不可用")
    panel.native_hython_label = _Widget("● Native Hython：不可用")
    panel.houdini_scene_label = _Widget("场景版本：不可用  ·  未保存：不可用")
    panel.thread_id_edit = _Widget(selected_thread_id or "")
    panel.history_combo = _Widget()
    if panel._thread_history:
        panel.history_combo.addItem("Current thread", panel._thread_history[0])
    else:
        panel.history_combo.addItem("暂无历史会话", None)
    panel.refresh_threads_button = _Widget()
    panel.thread_name_edit = _Widget("Current thread")
    panel.rename_thread_button = _Widget()
    panel.copy_thread_id_button = _Widget()
    panel.new_thread_button = _Widget()
    panel.resume_thread_button = _Widget()
    panel.send_button = _Widget()
    panel.stop_button = _Widget()
    panel.conversation = _ConversationShim()
    panel.welcome_group = _Widget()
    panel.approval_group = _Widget()
    panel.approval_text = _Widget()
    panel.approval_details_button = _Widget("高级详情")
    panel.approval_details_text = _Widget()
    panel.persistent_allow_note = _Widget(
        "持续授权：以后允许协议提供的相同命令规则。"
    )
    panel.persistent_allow_button = _Widget("以后允许相同命令规则")
    panel.approval_details_button.setVisible(False)
    panel.approval_details_text.setVisible(False)
    panel.persistent_allow_note.setVisible(False)
    panel.persistent_allow_button.setVisible(False)
    panel.allow_button = _Widget("允许一次")
    panel.deny_button = _Widget("拒绝")
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
    panel.service_tier_label = _Widget("速度")
    panel.service_tier_combo = _Widget()
    panel.service_tier_combo.addItem("标准", None)
    panel.service_tier_label.setVisible(False)
    panel.service_tier_combo.setVisible(False)
    panel.goal_objective_edit = _Widget()
    panel.goal_status_label = _Widget("状态：未设置")
    panel.goal_activity_label = _Widget("当前跟进：等待下一轮任务进展")
    panel.goal_budget_edit = _Widget()
    panel.goal_metrics_label = _Widget()
    panel.goal_focus_checkbox = _Widget()
    panel.goal_focus_hint_label = _Widget(
        "已关闭：普通聊天，不自动恢复或续做。"
    )
    panel.goal_refresh_button = _Widget()
    panel.goal_save_button = _Widget("保存（继续跟进）")
    panel.goal_clear_button = _Widget()
    panel.team_combo = _Widget()
    panel.team_combo.addItem("暂无子任务", None)
    panel.team_details_text = _Widget()
    panel.performance_label = _Widget()
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


_RECOVERY_GOAL_BINDING = "a" * 64
_RECOVERY_PROMPT_ID = "launcher-session-1"


def _recovery_session(
    *,
    turn_id: str = "pre-crash-turn",
    turn_status: str = "completed",
    turn_active: bool = False,
    focus_mode: bool = True,
    thread_id: str = "thread-1",
) -> dict[str, Any]:
    return {
        "connected": True,
        "authentication": "authenticated",
        "thread_id": thread_id,
        "turn_id": turn_id,
        "turn_status": turn_status,
        "turn_active": turn_active,
        "focus_mode": focus_mode,
    }


def _recovery_goal_payload(
    *,
    thread_id: str = "thread-1",
    focus_mode: bool = True,
    status: str = "active",
    goal_binding: str = _RECOVERY_GOAL_BINDING,
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "focus_mode": focus_mode,
        "goal_binding": goal_binding,
        "goal": {
            "threadId": thread_id,
            "objective": "Finish the recovered asset",
            "status": status,
        },
    }


def _recovery_read_payload(*, include_launcher_prompt: bool = False) -> dict[str, Any]:
    text = (
        f"[HIA launcher recovery {_RECOVERY_PROMPT_ID}] recovered"
        if include_launcher_prompt
        else "Work completed before the crash"
    )
    return {
        "thread_id": "thread-1",
        "result": {
            "thread": {
                "id": "thread-1",
                "turns": [
                    {
                        "items": [
                            {
                                "type": "userMessage",
                                "content": [{"type": "text", "text": text}],
                            },
                            {"type": "agentMessage", "text": "Recovered history"},
                        ]
                    }
                ],
            }
        },
    }


def _prime_crash_recovery_panel(
    panel: Any,
    *,
    initial_session: dict[str, Any] | None = None,
    include_launcher_prompt: bool = False,
) -> None:
    panel._crash_recovery_marker = {
        "thread_id": "thread-1",
        "goal_binding": _RECOVERY_GOAL_BINDING,
        "prompt_id": _RECOVERY_PROMPT_ID,
    }
    panel._on_health(
        {
            "houdini_mcp": {"backend": "hia_v2", "available": True},
            "session": initial_session or _recovery_session(),
        }
    )
    panel._on_action_completed("goal_get", _recovery_goal_payload())
    panel._on_action_completed(
        "thread_read:crash_recovery",
        _recovery_read_payload(include_launcher_prompt=include_launcher_prompt),
    )
    panel._on_action_completed("goal_get", _recovery_goal_payload())


def _complete_steer_sync(
    panel: Any,
    *,
    turn_active: bool,
    turn_id: str | None,
    turn_status: str,
) -> str:
    context = panel._client.session_contexts[-1]
    panel._on_action_completed(
        context,
        {
            "session": {
                "connected": True,
                "authentication": "authenticated",
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "turn_status": turn_status,
                "turn_active": turn_active,
                "focus_mode": False,
            }
        },
    )
    return context


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
        self.assertIn("请重启 launcher", panel.conversation.toPlainText())
        self.assertIn("不会自动重放", panel.conversation.toPlainText())

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

        panel._hou_module = types.SimpleNamespace(
            hipFile=types.SimpleNamespace(hasUnsavedChanges=lambda: False)
        )
        panel._update_houdini_status(report)
        self.assertIn("未保存：否", panel.houdini_scene_label.text())

    def test_scene_status_uses_plain_labels_and_explains_both_values(self) -> None:
        panel_source = (
            PANEL_LIB_ROOT / "hia_panel" / "panel.py"
        ).read_text(encoding="utf-8")

        self.assertIn("场景版本：不可用  ·  未保存：不可用", panel_source)
        self.assertIn("场景版本是当前 Houdini 会话内检测到的场景变化计数", panel_source)
        self.assertIn("未保存表示当前 HIP 是否有尚未保存的修改", panel_source)
        self.assertNotIn("Revision：不可用  ·  Dirty：不可用", panel_source)

    def test_three_columns_can_collapse_and_goal_uses_qtextedit(self) -> None:
        panel_source = (
            PANEL_LIB_ROOT / "hia_panel" / "panel.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("self.history_combo.setMinimumWidth(210)", panel_source)
        self.assertNotIn("right_column.setMinimumWidth(260)", panel_source)
        for column in ("left_column", "center_column", "right_column"):
            self.assertIn(f"{column}.setMinimumWidth(0)", panel_source)
        self.assertIn("self.main_splitter.setChildrenCollapsible(True)", panel_source)
        self.assertIn("for index in range(3):", panel_source)
        self.assertIn("self.main_splitter.setCollapsible(index, True)", panel_source)
        self.assertEqual(
            2,
            panel_source.count("AdjustToMinimumContentsLengthWithIcon"),
        )
        self.assertEqual(2, panel_source.count("setMinimumContentsLength(0)"))
        self.assertIn(
            "for label in (self.thread_status_label, self.turn_status_label):",
            panel_source,
        )
        self.assertIn("QtWidgets.QSizePolicy.Policy.Ignored", panel_source)
        self.assertIn('title[:23] + "…"', panel_source)
        self.assertNotIn('setText(f"Turn：{turn_id', panel_source)
        self.assertGreaterEqual(
            panel_source.count('self.turn_status_label.setText("Turn：运行中")'),
            2,
        )
        self.assertIn("QtCore.Qt.ItemDataRole.ToolTipRole", panel_source)
        self.assertIn(
            "self.goal_objective_edit = QtWidgets.QTextEdit()",
            panel_source,
        )
        self.assertIn("self.conversation.setMinimumHeight(0)", panel_source)
        self.assertIn(
            "center_layout.addWidget(self.conversation, 1)",
            panel_source,
        )
        self.assertIn(
            "QtWidgets.QSizePolicy.Policy.Expanding,\n"
            "            QtWidgets.QSizePolicy.Policy.Expanding,",
            panel_source,
        )
        self.assertIn("center_layout.addWidget(self.input_edit)", panel_source)
        self.assertNotIn("self.input_edit.setFixedHeight", panel_source)
        self.assertNotIn(
            "self.goal_objective_edit = QtWidgets.QPlainTextEdit()",
            panel_source,
        )
        self.assertIn("仅用于长期多步骤任务；普通聊天无需设置", panel_source)
        self.assertIn("长期任务目标（普通聊天可留空）", panel_source)
        self.assertIn(
            'self.goal_status_label = QtWidgets.QLabel("状态：未设置")',
            panel_source,
        )
        self.assertNotIn("goal_status_combo", panel_source)
        self.assertIn('QtWidgets.QPushButton("保存（继续跟进）")', panel_source)
        self.assertIn('QtWidgets.QCheckBox("目标专注模式")', panel_source)
        self.assertIn("普通聊天，不自动恢复或续做", panel_source)
        self.assertNotIn("inputMethodEvent", panel_source)

    def test_turn_start_and_steer_do_not_force_houdini_focus_change(self) -> None:
        panel = _make_panel()

        panel.input_edit.setPlainText("先生成基础模型")
        panel._send()
        start_context = panel._client.turn_requests[-1][-1]
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
        panel._send()
        self.assertEqual(1, len(panel._client.turn_requests))

        turn_id = "turn-focus-1"
        panel._on_action_completed(
            start_context,
            {
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )

        panel.input_edit.setPlainText("继续调整材质")
        panel._send()
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
        panel._send()
        self.assertEqual(1, len(panel._client.steer_requests))

        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel._on_action_completed(
            steer_context,
            {"thread_id": "thread-1", "turn_id": turn_id},
        )
        self.assertEqual(0, panel.input_edit.clear_focus_calls)

    def test_conversation_roles_and_streaming_share_one_codex_card(self) -> None:
        panel = _make_panel()

        panel._add_user_message("生成一个可编辑模型", ())
        panel._begin_codex_message()
        codex_entry = panel.conversation.entries[-1]
        self.assertEqual("", codex_entry["text"])
        self.assertIn("尚无文字输出", codex_entry["display_text"])
        panel._append_codex_delta("正在")
        self.assertNotIn("display_text", codex_entry)
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
        self.assertIn("尚无文字输出", panel.conversation.toPlainText())

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "plan": [{"step": "更新参数", "status": "inProgress"}],
                },
            }
        )

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
        self.assertIn("尚无文字输出", panel.conversation.toPlainText())

        lifecycle(
            "item/completed",
            {
                "id": "reasoning-1",
                "type": "reasoning",
                "text": "不得显示的内部推理",
                "status": "completed",
            },
        )
        lifecycle(
            "item/completed",
            {
                "id": "custom-1",
                "type": "custom_tool_call",
                "raw": {"secret": "不得显示的工具 JSON"},
                "status": "completed",
            },
        )
        panel._render_event(_completed_notification(turn_id))
        rendered = panel.conversation.toPlainText()
        self.assertIn("本轮未返回文字回复。", rendered)
        self.assertNotIn("不得显示的内部推理", rendered)
        self.assertNotIn("不得显示的工具 JSON", rendered)

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
        self.assertNotIn(
            "把顶部再缩短一些", panel._diagnostic_snapshot["user_goal"]
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
        self.assertIn(
            "把顶部再缩短一些", panel._diagnostic_snapshot["user_goal"]
        )

    def test_no_active_steer_falls_back_once_with_exact_snapshot(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel.include_selection_checkbox.setChecked(True)
        panel._selected_node_paths = ("/obj/cabin",)
        original_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\roof.png"
        )
        later_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\later.png"
        )
        panel.input_edit.setPlainText("继续调整屋顶")
        panel.attachment_strip.add_path(original_attachment)
        with mock.patch.object(
            panel,
            "_read_selected_node_paths",
            return_value=("/obj/cabin",),
        ):
            panel._send()
        request_text, _images, steer_context = panel._client.steer_requests[-1]
        self.assertIn("/obj/cabin", request_text)
        self.assertNotIn("继续调整屋顶", panel._diagnostic_snapshot["user_goal"])

        panel.input_edit.setPlainText("后来新增的草稿")
        panel.attachment_strip.add_path(later_attachment)
        failure = {
            "structured_error": {
                "code": "NO_ACTIVE_TURN",
                "message": "The previous Turn ended",
                "details": {
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_active": False,
                    "turn_status": "completed",
                },
            }
        }
        panel._on_request_failed(steer_context, failure)

        self.assertEqual(1, len(panel._client.session_contexts))
        _complete_steer_sync(
            panel,
            turn_active=False,
            turn_id=turn_id,
            turn_status="completed",
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        fallback_text, _model, _effort, images, fallback_context = (
            panel._client.turn_requests[-1]
        )
        self.assertEqual(request_text, fallback_text)
        self.assertEqual([original_attachment], images)
        self.assertEqual("后来新增的草稿", panel.input_edit.toPlainText())
        self.assertEqual(
            [original_attachment, later_attachment],
            panel.attachment_strip.paths(),
        )
        self.assertEqual("继续调整屋顶", panel._diagnostic_snapshot["user_goal"])
        self.assertEqual(goal, panel._current_goal)
        self.assertEqual([], panel._client.goal_set_requests)

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
        panel._render_event(_completed_notification(turn_id, sequence=3))
        self.assertEqual(TurnPhase.STARTING, panel._turn_state.phase)
        self.assertIsNone(panel._turn_state.turn_id)

        panel._on_request_failed(steer_context, failure)
        self.assertEqual(2, len(panel._client.turn_requests))
        panel._on_action_completed(
            fallback_context,
            {
                "thread_id": "thread-1",
                "turn_id": "turn-fallback",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        self.assertIn(
            "上一轮已结束，已作为新消息发送。",
            panel.conversation.toPlainText(),
        )
        self.assertEqual("后来新增的草稿", panel.input_edit.toPlainText())
        self.assertEqual(
            [original_attachment, later_attachment],
            panel.attachment_strip.paths(),
        )

    def test_no_active_steer_fallback_has_strict_error_boundaries(self) -> None:
        cases = (
            (
                "CODEX_RPC_ERROR",
                {"thread_id": "thread-1", "turn_active": False},
            ),
            (
                "NO_ACTIVE_TURN",
                {"thread_id": "thread-1", "turn_active": True},
            ),
            (
                "NO_ACTIVE_TURN",
                {
                    "thread_id": "thread-1",
                    "turn_id": "wrong-turn",
                    "turn_active": False,
                },
            ),
        )
        for error_code, details in cases:
            with self.subTest(error_code=error_code, details=details):
                panel = _make_panel()
                _context, turn_id = _start_active_turn(panel, 1)
                panel.input_edit.setPlainText("保留这条追加")
                panel._send()
                _text, _images, steer_context = panel._client.steer_requests[-1]
                payload_details = {"turn_id": turn_id, **details}
                panel._on_request_failed(
                    steer_context,
                    {
                        "structured_error": {
                            "code": error_code,
                            "message": "steer failed",
                            "details": payload_details,
                        }
                    },
                )
                self.assertEqual(1, len(panel._client.turn_requests))
                self.assertEqual([], panel._client.session_contexts)
                self.assertEqual("保留这条追加", panel.input_edit.toPlainText())
                self.assertEqual([], panel._client.goal_set_requests)

    def test_stale_steer_syncs_new_active_turn_and_retries_once(self) -> None:
        panel = _make_panel()
        _context, old_turn_id = _start_active_turn(panel, 1)
        attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\sync.png"
        )
        panel.input_edit.setPlainText("同步后追加")
        panel.attachment_strip.add_path(attachment)
        panel._send()
        _text, _images, old_context = panel._client.steer_requests[-1]

        panel._on_request_failed(
            old_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": old_turn_id,
                        "active_turn_id": "turn-authoritative",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )
        self.assertEqual(1, len(panel._client.session_contexts))
        self.assertEqual(1, len(panel._client.steer_requests))
        self.assertEqual("同步后追加", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())

        panel._render_event(_completed_notification(old_turn_id, sequence=2))
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": old_turn_id,
                    "itemId": "old-delta",
                    "delta": "旧 Turn 不得显示",
                },
            }
        )
        _complete_steer_sync(
            panel,
            turn_active=True,
            turn_id="turn-authoritative",
            turn_status="inProgress",
        )

        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual("turn-authoritative", panel._turn_state.turn_id)
        self.assertEqual(2, len(panel._client.steer_requests))
        retry_context = panel._client.steer_requests[-1][-1]
        self.assertEqual("同步后追加", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertNotIn("旧 Turn 不得显示", panel.conversation.toPlainText())

        panel._on_action_completed(
            old_context,
            {"thread_id": "thread-1", "turn_id": old_turn_id},
        )
        panel._on_action_completed(
            retry_context,
            {"thread_id": "thread-1", "turn_id": "turn-authoritative"},
        )
        self.assertEqual(2, len(panel._client.steer_requests))
        self.assertEqual("", panel.input_edit.toPlainText())
        self.assertEqual([], panel.attachment_strip.paths())
        self.assertNotIn(retry_context, panel._pending_steer_drafts)

    def test_goal_text_survives_late_stale_steer_reconciliation(self) -> None:
        panel = _make_panel()
        _context, old_turn_id = _start_active_turn(panel, 1)
        panel.input_edit.setPlainText("不要截断 Goal 正文")
        panel._send()
        old_context = panel._client.steer_requests[-1][-1]
        panel._on_request_failed(
            old_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": old_turn_id,
                        "active_turn_id": "goal-turn",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )
        self.assertEqual(1, len(panel._client.session_contexts))

        self.assertTrue(
            panel._apply_session(
                {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": old_turn_id,
                    "turn_status": "completed",
                    "turn_active": False,
                    "focus_mode": False,
                },
                token=panel._turn_state.capture_token(),
                allow_followup=True,
            )
        )
        self.assertTrue(
            panel._apply_goal(
                "thread-1",
                {
                    "threadId": "thread-1",
                    "objective": "继续当前 Goal",
                    "status": "active",
                    "turnId": "goal-turn",
                },
            )
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "goal-turn", "status": "inProgress"},
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn",
                    "itemId": "goal-message",
                    "delta": "Goal 已有正文",
                },
            }
        )
        goal_index = panel.conversation._active_codex_index
        self.assertIsNotNone(goal_index)
        goal_entry = panel.conversation.entries[goal_index]
        codex_count = sum(
            entry["role"] == "codex" for entry in panel.conversation.entries
        )
        freeze_calls = panel.conversation.freeze_calls

        _complete_steer_sync(
            panel,
            turn_active=True,
            turn_id="goal-turn",
            turn_status="inProgress",
        )

        self.assertIs(goal_entry, panel.conversation.entries[goal_index])
        self.assertEqual("Goal 已有正文", goal_entry["text"])
        self.assertNotIn("display_text", goal_entry)
        self.assertEqual(goal_index, panel.conversation._active_codex_index)
        self.assertEqual(freeze_calls, panel.conversation.freeze_calls)
        self.assertEqual(
            codex_count,
            sum(entry["role"] == "codex" for entry in panel.conversation.entries),
        )
        self.assertNotIn(old_context, panel._pending_steer_drafts)

    def test_stale_steer_retry_conflict_preserves_draft_without_loop(self) -> None:
        panel = _make_panel()
        _context, old_turn_id = _start_active_turn(panel, 1)
        original_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\retry.png"
        )
        later_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\later.png"
        )
        panel.input_edit.setPlainText("只能重试一次")
        panel.attachment_strip.add_path(original_attachment)
        panel._send()
        old_context = panel._client.steer_requests[-1][-1]
        panel._on_request_failed(
            old_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": old_turn_id,
                        "active_turn_id": "turn-authoritative",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )
        _complete_steer_sync(
            panel,
            turn_active=True,
            turn_id="turn-authoritative",
            turn_status="inProgress",
        )
        retry_context = panel._client.steer_requests[-1][-1]
        panel.input_edit.setPlainText("重试期间的新草稿")
        panel.attachment_strip.clear()
        panel.attachment_strip.add_path(later_attachment)
        panel._on_request_failed(
            retry_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed again",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": "turn-authoritative",
                        "active_turn_id": "turn-third",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )

        self.assertEqual(1, len(panel._client.session_contexts))
        self.assertEqual(2, len(panel._client.steer_requests))
        restored_text = panel.input_edit.toPlainText()
        self.assertTrue(restored_text.startswith("只能重试一次\n\n"))
        self.assertIn("重试期间的新草稿", restored_text)
        self.assertCountEqual(
            [original_attachment, later_attachment],
            panel.attachment_strip.paths(),
        )
        self.assertNotIn(retry_context, panel._pending_steer_drafts)
        self.assertIn("未再次重试", panel.conversation.toPlainText())

    def test_stale_steer_session_failure_restores_draft_and_attachments(self) -> None:
        panel = _make_panel()
        _context, old_turn_id = _start_active_turn(panel, 1)
        original_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\sync-old.png"
        )
        later_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\sync-new.png"
        )
        panel.input_edit.setPlainText("同步失败时的原提交")
        panel.attachment_strip.add_path(original_attachment)
        panel._send()
        steer_context = panel._client.steer_requests[-1][-1]
        panel._on_request_failed(
            steer_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": old_turn_id,
                        "active_turn_id": "turn-authoritative",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )
        sync_context = panel._client.session_contexts[-1]
        panel.input_edit.setPlainText("同步期间的新草稿")
        panel.attachment_strip.clear()
        panel.attachment_strip.add_path(later_attachment)

        panel._on_request_failed(
            sync_context,
            {
                "structured_error": {
                    "code": "NETWORK_ERROR",
                    "message": "Bridge disconnected",
                }
            },
        )

        restored_text = panel.input_edit.toPlainText()
        self.assertTrue(restored_text.startswith("同步失败时的原提交\n\n"))
        self.assertIn("同步期间的新草稿", restored_text)
        self.assertCountEqual(
            [original_attachment, later_attachment],
            panel.attachment_strip.paths(),
        )
        self.assertNotIn(steer_context, panel._pending_steer_drafts)
        self.assertEqual(1, len(panel._client.steer_requests))
        self.assertEqual(1, len(panel._client.session_contexts))

    def test_stop_permanently_cancels_pending_steer_fallback(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        cancelled_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\cancelled.png"
        )
        later_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\after-stop.png"
        )
        panel.input_edit.setPlainText("已取消的追加")
        panel.attachment_strip.add_path(cancelled_attachment)
        panel._send()
        steer_context = panel._client.steer_requests[-1][-1]
        panel._on_request_failed(
            steer_context,
            {
                "structured_error": {
                    "code": "STALE_ACTIVE_TURN",
                    "message": "The active Turn changed",
                    "details": {
                        "thread_id": "thread-1",
                        "expected_turn_id": turn_id,
                        "active_turn_id": "turn-authoritative",
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )
        sync_context = panel._client.session_contexts[-1]
        panel.input_edit.setPlainText("停止后保留的新草稿")
        panel.attachment_strip.clear()
        panel.attachment_strip.add_path(later_attachment)

        panel._stop()
        self.assertTrue(
            panel._pending_steer_drafts[steer_context]["fallback_cancelled"]
        )
        panel._on_request_failed(
            sync_context,
            {
                "structured_error": {
                    "code": "NETWORK_ERROR",
                    "message": "Bridge disconnected",
                }
            },
        )

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertEqual("停止后保留的新草稿", panel.input_edit.toPlainText())
        self.assertEqual([later_attachment], panel.attachment_strip.paths())
        self.assertNotIn("已取消的追加", panel.input_edit.toPlainText())
        self.assertNotIn(steer_context, panel._pending_steer_drafts)

    def test_fallback_reservation_failure_preserves_original_and_later_draft(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        original_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\original.png"
        )
        later_attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\later.png"
        )
        panel.input_edit.setPlainText("原追加文字")
        panel.attachment_strip.add_path(original_attachment)
        panel._send()
        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel.input_edit.setPlainText("后来编辑内容")
        panel.attachment_strip.add_path(later_attachment)

        with mock.patch.object(panel._turn_state, "begin_start", return_value=False):
            panel._on_request_failed(
                steer_context,
                {
                    "structured_error": {
                        "code": "NO_ACTIVE_TURN",
                        "message": "The previous Turn ended",
                        "details": {
                            "thread_id": "thread-1",
                            "turn_id": turn_id,
                            "turn_active": False,
                            "turn_status": "completed",
                        },
                    }
                },
            )
            _complete_steer_sync(
                panel,
                turn_active=False,
                turn_id=turn_id,
                turn_status="completed",
            )

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertIn("原追加文字", panel.input_edit.toPlainText())
        self.assertIn("后来编辑内容", panel.input_edit.toPlainText())
        self.assertEqual(
            [original_attachment, later_attachment],
            panel.attachment_strip.paths(),
        )

    def test_stale_fallback_active_conflict_clears_pending_without_replay(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        attachment = (
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\conflict.png"
        )
        panel.input_edit.setPlainText("只发送一次")
        panel.attachment_strip.add_path(attachment)
        panel._send()
        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel._on_request_failed(
            steer_context,
            {
                "structured_error": {
                    "code": "NO_ACTIVE_TURN",
                    "message": "The previous Turn ended",
                    "details": {
                        "thread_id": "thread-1",
                        "turn_id": turn_id,
                        "turn_active": False,
                        "turn_status": "completed",
                    },
                }
            },
        )
        _complete_steer_sync(
            panel,
            turn_active=False,
            turn_id=turn_id,
            turn_status="completed",
        )
        fallback_context = panel._client.turn_requests[-1][-1]
        panel._turn_state._generation += 1

        panel._on_request_failed(
            fallback_context,
            {
                "structured_error": {
                    "code": "TURN_ALREADY_ACTIVE",
                    "message": "another caller started a Turn",
                    "details": {
                        "thread_id": "thread-1",
                        "turn_id": "turn-other",
                        "turn_created": False,
                        "turn_active": True,
                        "turn_status": "inProgress",
                    },
                }
            },
        )

        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertEqual("只发送一次", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertNotIn(fallback_context, panel._pending_turn_drafts)
        self.assertEqual(2, len(panel._client.turn_requests))

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
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
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
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
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
            "code": "INVALID_JSONL",
            "method": "app-server/stdout",
            "message": "Ignored invalid JSONL",
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
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
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
        self.assertEqual(0, panel.input_edit.clear_focus_calls)
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
        stop_token = panel._turn_state.capture_token()
        panel._stopping_turn_token = stop_token
        panel._interrupt_tokens["interrupt:old"] = stop_token
        panel._reconciliation_tokens["session_reconcile:old"] = stop_token
        event = _CloseEvent()
        panel._polling_enabled = True
        panel._local_houdini_polling_enabled = True

        panel.closeEvent(event)
        panel.closeEvent(event)

        self.assertFalse(panel._polling_enabled)
        self.assertFalse(panel._local_houdini_polling_enabled)
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
        self.assertIsNone(panel._stopping_turn_token)
        self.assertEqual({}, panel._interrupt_tokens)
        self.assertEqual({}, panel._reconciliation_tokens)

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
                first._mcp_backend = "fxhoudini"
                second._mcp_backend = "fxhoudini"
                first._initialize_houdini_read_adapter(object())
                second._initialize_houdini_read_adapter(object())
        finally:
            method_globals["HoudiniReadAdapter"] = original_adapter

        self.assertEqual(2, len(publisher_ids))
        self.assertNotEqual(publisher_ids[0], publisher_ids[1])
        pattern = re.compile(rf"^panel-{prefix}-[0-9a-f]{{16}}$")
        self.assertTrue(all(pattern.fullmatch(value) for value in publisher_ids))

    def test_hia_v2_does_not_construct_or_start_legacy_b2_polling(self) -> None:
        panel = _make_panel()
        panel._mcp_backend = "hia_v2"
        selected_nodes: list[Any] = []
        dirty = [False]
        hou_module = types.SimpleNamespace(
            selectedNodes=lambda: tuple(selected_nodes),
            hipFile=types.SimpleNamespace(hasUnsavedChanges=lambda: dirty[0]),
        )
        panel._hou_module = hou_module
        method_globals = HoudiniIntelligencePanel._initialize_houdini_read_adapter.__globals__
        original_adapter = method_globals["HoudiniReadAdapter"]

        class _ForbiddenAdapter:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise AssertionError("HIA V2 must not construct the B2 adapter")

        try:
            method_globals["HoudiniReadAdapter"] = _ForbiddenAdapter
            panel._initialize_houdini_read_adapter(hou_module)
        finally:
            method_globals["HoudiniReadAdapter"] = original_adapter

        self.assertIsNone(panel._houdini_adapter)
        panel._houdini_adapter = _ReadAdapterShim()

        panel._start_houdini_read_loop()
        panel._start_local_houdini_loop()

        self.assertFalse(panel._houdini_polling_enabled)
        self.assertTrue(panel._local_houdini_polling_enabled)
        self.assertEqual([0], panel._houdini_heartbeat_timer.start_delays)

        panel._houdini_heartbeat()
        self.assertEqual("当前选择：无", panel.selection_label.text())
        self.assertIn("未保存：否", panel.houdini_scene_label.text())
        self.assertEqual(1, panel._client.houdini_status_requests)
        self.assertEqual([], panel._client.capability_reports)

        selected_nodes.append(types.SimpleNamespace(path=lambda: "/obj/geo1"))
        dirty[0] = True
        panel._houdini_status_pending = False
        panel._houdini_heartbeat()
        self.assertEqual("当前选择：/obj/geo1", panel.selection_label.text())
        self.assertIn("未保存：是", panel.houdini_scene_label.text())

        selected_nodes.append(types.SimpleNamespace(path=lambda: "/obj/geo2"))
        panel._houdini_status_pending = False
        panel._houdini_heartbeat()
        self.assertIn("等 2 个节点", panel.selection_label.text())

        panel._on_action_completed(
            "houdini_status",
            {
                "houdini_mcp": {
                    "backend": "hia_v2",
                    "available": True,
                    "scene_revision": 12,
                },
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": None,
                    "turn_active": False,
                    "focus_mode": False,
                },
            },
        )
        self.assertIn("场景版本：12", panel.houdini_scene_label.text())

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
        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertEqual(1, panel.conversation.freeze_calls)
        self.assertEqual(1, panel._client.houdini_status_requests)
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
        self.assertEqual(
            1,
            rendered.count("Codex 已停止；已发出的 Houdini 操作可能仍在收尾。"),
        )
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
        self.assertEqual(1, panel._client.houdini_status_requests)
        self.assertEqual(1, panel.conversation.freeze_calls)
        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertEqual([], panel._client.session_contexts)
        self.assertIn("停止前文本", panel.conversation.toPlainText())
        self.assertNotIn("不应出现的迟到文本", panel.conversation.toPlainText())
        self.assertEqual("停止期间的草稿", panel.input_edit.toPlainText())
        self.assertEqual([], panel._client.steer_requests)
        self.assertFalse(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())

    def test_stop_before_first_delta_removes_placeholder_and_ignores_late_text(
        self,
    ) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        self.assertIn("尚无文字输出", panel.conversation.toPlainText())

        panel._stop()
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": turn_id,
                    "itemId": "late-message",
                    "delta": "停止后迟到文本",
                },
            }
        )

        rendered = panel.conversation.toPlainText()
        self.assertNotIn("尚无文字输出", rendered)
        self.assertNotIn("本轮未返回文字回复", rendered)
        self.assertNotIn("停止后迟到文本", rendered)
        self.assertEqual(
            [],
            [entry for entry in panel.conversation.entries if entry["role"] == "codex"],
        )

    def test_stop_response_unlocks_on_idle_or_disconnects_on_recovery_failure(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._stop()
        interrupt_context = panel._client.interrupt_contexts[0]

        panel._on_action_completed(
            interrupt_context,
            {
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "interrupted",
                    "turn_active": False,
                },
            },
        )

        self.assert_idle_controls(panel)
        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertIsNone(panel._stopping_turn_token)
        self.assertEqual([], panel._client.session_contexts)

        failed = _make_panel()
        _context, failed_turn_id = _start_active_turn(failed, 2)
        failed.input_edit.setPlainText("保留的草稿")
        failed._stop()
        failed_context = failed._client.interrupt_contexts[0]
        failed._on_request_failed(
            failed_context,
            {
                "structured_error": {
                    "code": "CODEX_STOP_RECOVERY_TIMEOUT",
                    "message": "Stop recovery reached its deadline",
                    "details": {
                        "thread_id": "thread-1",
                        "turn_id": failed_turn_id,
                        "turn_active": False,
                        "turn_status": "stopRecoveryFailed",
                        "connected": False,
                        "recoverable": True,
                    },
                }
            },
        )
        self.assertEqual(TurnPhase.IDLE, failed._turn_state.phase)
        self.assertIsNone(failed._stopping_turn_token)
        self.assertFalse(failed._connected)
        self.assertFalse(failed.send_button.isEnabled())
        self.assertTrue(failed.input_edit.isEnabled())
        self.assertEqual("保留的草稿", failed.input_edit.toPlainText())
        self.assertIn("未连接", failed.connection_label.text())

    def test_stop_background_recovery_preserves_draft_and_reconnects_same_thread(self) -> None:
        panel = _make_panel()
        panel._current_goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._focus_mode = True
        panel.service_tier_combo.addItem("快速", "priority")
        _context, turn_id = _start_active_turn(panel, 1)

        panel._stop()
        panel.input_edit.setPlainText("恢复后再发送")
        panel.attachment_strip.add_path("E:/houdini-intelligence-agent/.runtime/attachments/a.png")

        self.assertEqual(TurnPhase.IDLE, panel._turn_state.phase)
        self.assertEqual("recovering", panel._stop_recovery_state)
        self.assertIn("恢复中", panel.connection_label.text())
        self.assertIn("暂停", panel.goal_activity_label.text())
        self.assertNotIn("正在推进", panel.goal_activity_label.text())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertTrue(panel.model_combo.isEnabled())
        self.assertTrue(panel.effort_combo.isEnabled())
        self.assertTrue(panel.service_tier_combo.isEnabled())
        self.assertEqual(1, len(panel._client.turn_requests))

        panel._polling_enabled = True
        panel._render_event({"type": "process_exit", "returncode": 1})
        self.assertTrue(panel._polling_enabled)
        self.assertEqual("recovering", panel._stop_recovery_state)
        self.assertNotIn("请重启 launcher", panel.conversation.toPlainText())

        interrupt_context = panel._client.interrupt_contexts[0]
        panel._on_action_completed(
            interrupt_context,
            {
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "recovery_pending": True,
                "session": {
                    "connected": False,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": "stopRecovering",
                    "turn_active": False,
                    "focus_mode": True,
                },
            },
        )
        self.assertFalse(panel._interrupt_pending)
        self.assertEqual("recovering", panel._stop_recovery_state)

        panel._render_event(
            {
                "type": "session_state",
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": "interrupted",
                    "turn_active": False,
                    "focus_mode": True,
                },
            }
        )

        self.assertIsNone(panel._stop_recovery_state)
        self.assertTrue(panel._connected)
        self.assertTrue(panel.send_button.isEnabled())
        self.assertTrue(panel.model_combo.isEnabled())
        self.assertEqual("恢复后再发送", panel.input_edit.toPlainText())
        self.assertEqual(
            ["E:/houdini-intelligence-agent/.runtime/attachments/a.png"],
            panel.attachment_strip.paths(),
        )
        self.assertEqual("active", panel._current_goal["status"])
        self.assertTrue(panel._focus_mode)
        self.assertIn("已暂停", panel.goal_activity_label.text())
        self.assertEqual("active", panel._current_goal["status"])
        self.assertEqual(1, len(panel._client.turn_requests))

    def test_stop_background_recovery_failure_is_final_once_without_losing_draft(self) -> None:
        panel = _make_panel()
        panel._current_goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        _context, _turn_id = _start_active_turn(panel, 1)
        panel._stop()
        panel.input_edit.setPlainText("不要丢失")
        attachment = "E:/houdini-intelligence-agent/.runtime/attachments/failure.png"
        panel.attachment_strip.add_path(attachment)
        failure_event = {
            "type": "session_state",
            "session": {
                "connected": False,
                "authentication": "unavailable",
                "thread_id": "thread-1",
                "turn_id": None,
                "turn_status": "stopRecoveryFailed",
                "turn_active": False,
                "focus_mode": False,
            },
        }

        panel._render_event(failure_event)
        panel._render_event(failure_event)

        self.assertEqual("failed", panel._stop_recovery_state)
        self.assertFalse(panel._connected)
        self.assertFalse(panel._interrupt_pending)
        self.assertIsNone(panel._active_interrupt_context)
        self.assertFalse(panel.send_button.isEnabled())
        self.assertTrue(panel.model_combo.isEnabled())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertEqual("不要丢失", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertIn("已暂停", panel.goal_activity_label.text())
        self.assertEqual("active", panel._current_goal["status"])
        self.assertEqual(
            1,
            panel.conversation.toPlainText().count("请重启 launcher"),
        )

    def test_lost_stop_http_response_uses_existing_health_reconnect(self) -> None:
        panel = _make_panel()
        _context, _turn_id = _start_active_turn(panel, 1)
        panel._stop()
        panel.input_edit.setPlainText("网络恢复后保留")
        interrupt_context = panel._client.interrupt_contexts[0]

        panel._on_request_failed(
            interrupt_context,
            {
                "structured_error": {
                    "code": "NETWORK_TIMEOUT",
                    "message": "interrupt response timed out",
                }
            },
        )

        self.assertTrue(panel._reconnecting)
        self.assertTrue(panel._reconnect_timer.isActive())
        self.assertEqual("recovering", panel._stop_recovery_state)
        self.assertIn("恢复中", panel.connection_label.text())
        self.assertEqual("网络恢复后保留", panel.input_edit.toPlainText())
        self.assertFalse(panel.send_button.isEnabled())

        panel._on_health(
            {
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": "interrupted",
                    "turn_active": False,
                    "focus_mode": False,
                }
            }
        )

        self.assertFalse(panel._reconnecting)
        self.assertIsNone(panel._stop_recovery_state)
        self.assertTrue(panel.send_button.isEnabled())
        self.assertEqual("网络恢复后保留", panel.input_edit.toPlainText())

    def test_stop_session_state_stays_static_then_authoritative_idle_unlocks(self) -> None:
        panel = _make_panel()
        _context, turn_id = _start_active_turn(panel, 1)
        panel._stop()
        panel.input_edit.setPlainText("仍可编辑的草稿")

        panel._render_event(
            {
                "type": "session_state",
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "stopRequested",
                    "turn_active": True,
                },
            }
        )
        panel._send()

        self.assertEqual("Turn：已停止", panel.turn_status_label.text())
        self.assertEqual("仍可编辑的草稿", panel.input_edit.toPlainText())
        self.assertTrue(panel.input_edit.isEnabled())
        self.assertFalse(panel.send_button.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertEqual([], panel._client.steer_requests)

        panel._render_event(
            {
                "type": "session_state",
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "interrupted",
                    "turn_active": False,
                },
            }
        )
        self.assert_idle_controls(panel)
        self.assertIsNone(panel._stopping_turn_token)

    def test_late_stop_events_do_not_pollute_the_next_turn(self) -> None:
        panel = _make_panel()
        _context, first_turn_id = _start_active_turn(panel, 1)
        panel._stop()
        old_interrupt_context = panel._client.interrupt_contexts[0]
        panel._render_event(_completed_notification(first_turn_id, sequence=1))
        self.assertEqual(TurnPhase.IDLE, panel._turn_state.phase)
        self.assertFalse(panel.send_button.isEnabled())
        panel._render_event(
            {
                "type": "session_state",
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": "interrupted",
                    "turn_active": False,
                },
            }
        )
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
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": second_turn_id,
                    "itemId": "current-message",
                    "delta": "新 Turn 正常文本",
                },
            }
        )

        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
        self.assertEqual(second_turn_id, panel._turn_state.turn_id)
        self.assertNotIn("旧 Turn 迟到文本", panel.conversation.toPlainText())
        self.assertIn("新 Turn 正常文本", panel.conversation.toPlainText())
        self.assertEqual([], panel._client.session_contexts)

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

    def test_system_drive_approval_is_readable_redacted_and_collapsed(self) -> None:
        panel = _make_panel()
        system_drive = os.environ.get("SystemDrive") or "C:"
        target = system_drive + "\\Users\\Public\\HIA-Approval-Test.txt"
        event = {
            "type": "server_request",
            "request_id": "approval-readable-1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "cwd": str(REPOSITORY_ROOT),
                "command": "serialized fallback with broken PowerShell quoting",
                "commandActions": [
                    {
                        "type": "unknown",
                        "command": (
                            f"Set-Content -LiteralPath '{target}' -Value test; "
                            "$headers = @{ Authorization = 'Bearer approval-secret-token'; "
                            "Cookie = 'session=approval-cookie-secret'; "
                            "'X-Api-Key' = 'approval-api-key-secret' }; "
                            "curl.exe --cookie \"session=curl-cookie-secret\" "
                            "-H \"Authorization: Bearer curl-auth-secret\" "
                            "https://example.com"
                        ),
                    }
                ],
                "availableDecisions": [
                    "accept",
                    "decline",
                    "acceptWithExecpolicyAmendment",
                ],
                "proposedExecpolicyAmendment": ["Set-Content", "-LiteralPath"],
                "authorization": "Bearer approval-secret-token",
                "cookie": "session=approval-cookie-secret",
                "api_key": "approval-api-key-secret",
                "tailMarker": "kept-in-complete-json",
            },
        }

        panel._render_event(event)

        summary = panel.approval_text.toPlainText()
        details = panel.approval_details_text.toPlainText()
        self.assertIn("目的：修改系统盘文件", summary)
        self.assertIn("操作类型：文件写入", summary)
        self.assertIn(target, summary)
        self.assertIn("可能在系统盘创建、修改、移动或删除文件", summary)
        self.assertEqual("允许一次", panel.allow_button.text())
        self.assertEqual("拒绝", panel.deny_button.text())
        self.assertTrue(panel.approval_group.isVisible())
        self.assertTrue(panel.approval_details_button.isVisible())
        self.assertFalse(panel.approval_details_button.isChecked())
        self.assertFalse(panel.approval_details_text.isVisible())
        self.assertFalse(panel.persistent_allow_note.isVisible())
        self.assertFalse(panel.persistent_allow_button.isVisible())
        self.assertTrue(
            details.startswith(
                "原始 command：\nSet-Content -LiteralPath"
            )
        )
        self.assertIn("availableDecisions", details)
        self.assertIn("以后允许相同命令规则", details)
        self.assertIn("持续授权", details)
        self.assertIn("kept-in-complete-json", details)
        for secret in (
            "approval-secret-token",
            "approval-cookie-secret",
            "approval-api-key-secret",
            "curl-cookie-secret",
            "curl-auth-secret",
        ):
            self.assertNotIn(secret, summary + details)
        self.assertIn("[REDACTED]", details)

        panel._toggle_approval_details(True)
        self.assertTrue(panel.approval_details_text.isVisible())
        self.assertTrue(panel.persistent_allow_note.isVisible())
        self.assertTrue(panel.persistent_allow_button.isVisible())
        self.assertIn("持续授权", panel.persistent_allow_note.text())
        self.assertEqual("收起高级详情", panel.approval_details_button.text())
        panel._resolve_approval("allow")
        self.assertEqual(
            [("approval-readable-1", "allow")],
            panel._client.approval_decisions,
        )

    def test_persistent_command_rule_is_an_explicit_advanced_choice(self) -> None:
        panel = _make_panel()
        system_drive = os.environ.get("SystemDrive") or "C:"
        target = system_drive + "\\Users\\Public\\HIA-Approval-Test.txt"
        panel._render_event(
            {
                "type": "server_request",
                "request_id": "approval-rule-choice",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "commandActions": [
                        {
                            "command": (
                                f"Set-Content -LiteralPath '{target}' -Value test"
                            )
                        }
                    ],
                    "proposedExecpolicyAmendment": [
                        "Set-Content",
                        "-LiteralPath",
                    ],
                },
            }
        )

        self.assertFalse(panel.persistent_allow_button.isVisible())
        panel._toggle_approval_details(True)
        self.assertTrue(panel.persistent_allow_button.isVisible())
        panel._resolve_approval("allow_rule")
        self.assertEqual(
            [("approval-rule-choice", "allow_rule")],
            panel._client.approval_decisions,
        )

        panel = _make_panel()
        panel._render_event(
            {
                "type": "server_request",
                "request_id": "approval-rule-not-offered",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "command": f"Remove-Item -LiteralPath '{target}'",
                    "availableDecisions": ["accept", "decline"],
                    "proposedExecpolicyAmendment": ["Remove-Item"],
                },
            }
        )
        panel._toggle_approval_details(True)
        self.assertFalse(panel.persistent_allow_button.isVisible())

    def test_approval_purpose_prefers_the_actual_system_target(self) -> None:
        panel = _make_panel()
        system_drive = os.environ.get("SystemDrive") or "C:"
        source = str(REPOSITORY_ROOT / "source.txt")
        target = system_drive + "\\Users\\Public\\copied.txt"
        panel._render_event(
            {
                "type": "server_request",
                "request_id": "approval-copy-target",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "commandActions": [
                        {
                            "command": (
                                f"Copy-Item -LiteralPath '{source}' "
                                f"-Destination '{target}'"
                            )
                        }
                    ]
                },
            }
        )
        self.assertIn(
            f"目的：修改系统盘文件：{target}",
            panel.approval_text.toPlainText(),
        )

        panel = _make_panel()
        panel._render_event(
            {
                "type": "server_request",
                "request_id": "approval-env-target",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "command": (
                        "Set-Content -LiteralPath "
                        "'${env:SystemDrive}\\HIA-Test.txt' -Value test"
                    )
                },
            }
        )
        self.assertIn(
            "目标路径：${env:SystemDrive}\\HIA-Test.txt",
            panel.approval_text.toPlainText(),
        )

    def test_approval_deny_value_and_optional_persistent_note_are_unchanged(self) -> None:
        panel = _make_panel()
        panel._render_event(
            {
                "type": "server_request",
                "request_id": "approval-readable-2",
                "method": "item/fileChange/requestApproval",
                "params": {
                    "grantRoot": "C:\\ProgramData\\HIA",
                    "reason": "write requested",
                },
            }
        )

        details = panel.approval_details_text.toPlainText()
        self.assertIn("允许修改系统盘目录", panel.approval_text.toPlainText())
        self.assertNotIn("以后允许相同命令规则", details)
        panel._toggle_approval_details(True)
        self.assertFalse(panel.persistent_allow_note.isVisible())
        self.assertFalse(panel.persistent_allow_button.isVisible())
        panel._resolve_approval("deny")
        self.assertEqual(
            [("approval-readable-2", "deny")],
            panel._client.approval_decisions,
        )

    def test_passive_and_unknown_notifications_are_silent(self) -> None:
        panel = _make_panel()
        before = panel.conversation.toPlainText()
        passive_methods = (
            "remoteControl/status/changed",
            "mcpServer/startupStatus/updated",
            "account/rateLimits/updated",
            "skills/changed",
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
        self.assertEqual(before, panel.conversation.toPlainText())

    def test_dynamic_service_tier_is_distinct_from_effort_and_forwarded(self) -> None:
        panel = _make_panel()
        model = {
            "model": "dynamic-model",
            "displayName": "Dynamic Model",
            "description": "",
            "isDefault": True,
            "inputModalities": ["text", "image"],
            "supportedReasoningEfforts": [
                {"reasoningEffort": "high", "description": "Deep reasoning"}
            ],
            "defaultReasoningEffort": "high",
            "serviceTiers": [
                {
                    "id": "priority-live-id",
                    "name": "快速",
                    "description": "来自实时目录的优先处理说明",
                }
            ],
            "defaultServiceTier": "priority-live-id",
        }

        panel._apply_models([model])

        self.assertTrue(panel.service_tier_combo.isVisible())
        self.assertEqual("快速", panel.service_tier_combo.itemText(1))
        self.assertEqual("priority-live-id", panel._selected_service_tier())
        self.assertEqual("high", panel._selected_effort())
        self.assertIn("实时目录", panel.service_tier_combo.toolTip())

        panel.input_edit.setPlainText("use fast service")
        panel._send()
        self.assertEqual("priority-live-id", panel._client.turn_service_tiers[-1])
        self.assertEqual("high", panel._client.turn_requests[-1][2])

        standard_panel = _make_panel()
        standard_panel._apply_models([model])
        standard_panel.service_tier_combo.setCurrentIndex(0)
        standard_panel._on_service_tier_changed(0)
        standard_panel._new_thread()
        self.assertEqual([None], standard_panel._client.thread_service_tiers)

        standard_panel._apply_models(
            [
                {
                    **model,
                    "serviceTiers": [],
                    "defaultServiceTier": None,
                }
            ]
        )
        self.assertFalse(standard_panel.service_tier_combo.isVisible())

    def test_history_refresh_waits_for_explicit_open_before_rendering(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        threads = [
            {
                "thread_id": "019f-history-one",
                "name": "售货机材质",
                "preview": "fallback preview",
                "updated_at": 1_752_825_600,
            },
            {
                "thread_id": "019f-history-two",
                "name": None,
                "preview": "检查当前节点网络",
                "updated_at": 1_752_739_200,
            },
        ]

        panel._apply_threads(threads)

        self.assertEqual(3, panel.history_combo.count())
        self.assertEqual("未选择历史会话", panel.history_combo.itemText(0))
        self.assertIn("售货机材质", panel.history_combo.itemText(1))
        self.assertNotIn("019f-history-one", panel.history_combo.itemText(1))
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual("", panel.conversation.toPlainText())
        panel._refresh_threads()
        self.assertEqual(1, panel._client.thread_list_requests)
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.thread_read_requests)

        panel.history_combo.setCurrentIndex(1)
        panel._on_history_index_changed(1)
        team_event = {
            "type": "codex_notification",
            "method": "item/completed",
            "params": {
                "threadId": "019f-history-one",
                "item": {
                    "type": "subAgentActivity",
                    "agentThreadId": "thread-review",
                    "agentPath": "review/material",
                    "kind": "started",
                },
            },
        }
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "019f-history-one",
                    "goal": {
                        "threadId": "019f-history-one",
                        "objective": "完成材质审阅",
                        "status": "active",
                    },
                },
            }
        )
        panel._render_event(team_event)
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.goal_get_requests)
        self.assertIsNone(panel._current_goal)
        self.assertEqual({}, panel._team_records)
        self.assertEqual("", panel.conversation.toPlainText())
        panel._resume_thread()
        self.assertEqual(
            ("019f-history-one", None, "session_resume"),
            panel._client.resume_requests[-1],
        )

        panel._on_action_completed(
            "session_resume",
            {
                "thread_id": "019f-history-one",
                "focus_mode": True,
                "read": {
                    "thread": {
                        "id": "019f-history-one",
                        "turns": [
                            {
                                "items": [
                                    {
                                        "type": "userMessage",
                                        "content": [
                                            {"type": "text", "text": "继续调整材质"},
                                            {
                                                "type": "localImage",
                                                "path": r"E:\refs\look.png",
                                            },
                                        ],
                                    },
                                    {
                                        "type": "userMessage",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "继续推进当前 Goal；先核对上一轮真实结果，再执行下一项未完成工作。",
                                            }
                                        ],
                                    },
                                    {
                                        "type": "userMessage",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "请继续推进当前 Goal，这是我亲自发送的补充。",
                                            }
                                        ],
                                    },
                                    {"type": "agentMessage", "text": "已经完成。"},
                                    {"type": "commandExecution", "command": "ignored"},
                                ]
                            }
                        ],
                    }
                }
            },
        )
        self.assertEqual(1, panel.conversation.clear_calls)
        self.assertIn("继续调整材质", panel.conversation.toPlainText())
        self.assertNotIn(
            "继续推进当前 Goal；先核对上一轮真实结果，再执行下一项未完成工作。",
            panel.conversation.toPlainText(),
        )
        self.assertIn(
            "请继续推进当前 Goal，这是我亲自发送的补充。",
            panel.conversation.toPlainText(),
        )
        self.assertIn("已经完成", panel.conversation.toPlainText())
        self.assertNotIn("ignored", panel.conversation.toPlainText())
        self.assertEqual(["019f-history-one"], panel._client.goal_get_requests)

        panel._on_action_completed(
            "goal_get",
            {
                "thread_id": "019f-history-one",
                "goal": {
                    "threadId": "019f-history-one",
                    "objective": "完成材质审阅",
                    "status": "active",
                },
                "focus_mode": True,
            },
        )
        panel._render_event(team_event)
        self.assertEqual("完成材质审阅", panel.goal_objective_edit.toPlainText())
        self.assertIn("thread-review", panel._team_records)
        self.assertEqual(1, len(panel._client.turn_requests))

    def test_session_wait_timeouts_are_accurate_and_unlock_retry(self) -> None:
        cases = (
            ("session_resume", "CODEX_REQUEST_TIMEOUT", "会话恢复超时"),
            ("session_start", "CODEX_REQUEST_TIMEOUT", "会话启动超时"),
            ("thread_read:initial", "NETWORK_TIMEOUT", "会话恢复超时"),
        )
        for context, code, expected in cases:
            with self.subTest(context=context, code=code):
                panel = _make_panel()
                panel._session_action_pending = True
                panel._on_request_failed(
                    context,
                    {
                        "structured_error": {
                            "code": code,
                            "message": "Bridge network request timed out",
                        }
                    },
                )

                self.assertIn(expected, panel.conversation.toPlainText())
                self.assertIn("会话服务暂未完成", panel.conversation.toPlainText())
                self.assertNotIn("Bridge network", panel.conversation.toPlainText())
                if context != "thread_read:initial":
                    self.assertFalse(panel._session_action_pending)

        panel = _make_panel()
        panel._session_action_pending = True
        panel._on_request_failed(
            "session_resume",
            {
                "structured_error": {
                    "code": "NETWORK_ERROR",
                    "message": "Bridge network request failed",
                }
            },
        )
        self.assertIn("NETWORK_ERROR", panel.conversation.toPlainText())
        self.assertFalse(panel._session_action_pending)

    def test_history_failure_shows_only_sanitized_code_and_field(self) -> None:
        panel = _make_panel()

        panel._on_request_failed(
            "threads",
            {
                "structured_error": {
                    "code": "INVALID_THREAD_LIST_RESPONSE",
                    "message": "Bearer must-not-be-shown",
                    "details": {"field": "preview"},
                }
            },
        )

        text = panel.conversation.toPlainText()
        self.assertIn("INVALID_THREAD_LIST_RESPONSE", text)
        self.assertIn("field=preview", text)
        self.assertNotIn("must-not-be-shown", text)

    def test_history_click_and_rename_use_codex_thread_identity(self) -> None:
        panel = _make_panel()
        panel._apply_threads(
            [
                {
                    "thread_id": "019f-history-one",
                    "name": "旧名称",
                    "preview": "",
                    "updated_at": 1_752_825_600,
                }
            ]
        )
        panel.history_combo.setCurrentIndex(1)
        panel._on_history_index_changed(1)
        self.assertEqual([], panel._client.resume_requests)
        panel._resume_thread()
        self.assertEqual(
            [("019f-history-one", None, "session_resume")],
            panel._client.resume_requests,
        )
        panel._session_action_pending = False
        panel.thread_name_edit.setText("用户命名")
        panel._rename_thread()
        thread_id, name, context = panel._client.thread_rename_requests[-1]
        self.assertEqual("019f-history-one", thread_id)
        self.assertEqual("用户命名", name)
        self.assertTrue(context.startswith("thread_rename:"))
        panel._on_action_completed(
            context,
            {"thread_id": thread_id, "name": name},
        )
        self.assertIn("用户命名", panel.history_combo.itemText(1))

    def test_initial_and_reopened_panel_stay_empty_until_manual_open(self) -> None:
        first = _make_panel(selected_thread_id=None)
        self.assertIsNone(first._selected_thread_id)
        self.assertEqual("Thread：未选择", first.thread_status_label.text())
        self.assertEqual("", first.conversation.toPlainText())
        self.assertEqual([], first._client.resume_requests)
        self.assertEqual([], first._client.thread_read_requests)
        self.assertEqual([], first._client.goal_get_requests)
        self.assertEqual([], first._client.turn_requests)
        self.assertEqual({}, first._team_records)

        event = _CloseEvent()
        first.closeEvent(event)
        self.assertEqual(1, event.base_close_calls)

        panel = _make_panel(selected_thread_id=None)
        panel._on_health(
            {
                "houdini_mcp": {"backend": "hia_v2", "available": True},
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": "background-turn",
                    "turn_status": "inProgress",
                    "turn_active": True,
                    "focus_mode": True,
                },
            }
        )
        panel._apply_threads(
            [
                {
                    "thread_id": "thread-1",
                    "name": "后台专注任务",
                    "preview": "不应自动打开",
                    "updated_at": 1_752_825_600,
                }
            ]
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "thread-1",
                    "goal": {
                        "threadId": "thread-1",
                        "objective": "后台 Goal",
                        "status": "active",
                    },
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "type": "subAgentActivity",
                        "agentThreadId": "background-child",
                        "kind": "started",
                    },
                },
            }
        )
        panel._on_session(
            {
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": "background-turn",
                    "turn_status": "completed",
                    "turn_active": False,
                    "focus_mode": True,
                }
            }
        )

        self.assertEqual([], panel._client.thread_read_requests)
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.goal_get_requests)
        self.assertEqual([], panel._client.turn_requests)
        self.assertIsNone(panel._selected_thread_id)
        self.assertIsNone(panel._current_goal)
        self.assertFalse(panel.goal_focus_checkbox.isChecked())
        self.assertEqual({}, panel._team_records)
        self.assertEqual("Thread：未选择", panel.thread_status_label.text())
        self.assertEqual("Turn：空闲", panel.turn_status_label.text())
        self.assertEqual("", panel.conversation.toPlainText())

    def test_crash_recovery_marker_is_validated_and_consumed_once(self) -> None:
        environment = {
            "HIA_CRASH_RECOVERY_THREAD_ID": "thread-1",
            "HIA_CRASH_RECOVERY_GOAL_BINDING": _RECOVERY_GOAL_BINDING,
            "HIA_CRASH_RECOVERY_PROMPT_ID": _RECOVERY_PROMPT_ID,
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            marker = HoudiniIntelligencePanel._take_crash_recovery_marker()
            second = HoudiniIntelligencePanel._take_crash_recovery_marker()

        self.assertEqual("thread-1", marker["thread_id"])
        self.assertEqual(_RECOVERY_GOAL_BINDING, marker["goal_binding"])
        self.assertEqual(_RECOVERY_PROMPT_ID, marker["prompt_id"])
        self.assertIsNone(second)

        invalid = dict(environment)
        invalid["HIA_CRASH_RECOVERY_GOAL_BINDING"] = "not-a-binding"
        with mock.patch.dict(os.environ, invalid, clear=False):
            self.assertIsNone(
                HoudiniIntelligencePanel._take_crash_recovery_marker()
            )

    def test_crash_recovery_binds_exact_thread_without_resume_or_duplicate_turn(self) -> None:
        panel = _make_panel(selected_thread_id=None)

        _prime_crash_recovery_panel(panel)

        self.assertEqual(["thread-1", "thread-1"], panel._client.goal_get_requests)
        self.assertEqual(
            [("thread-1", "thread_read:crash_recovery")],
            panel._client.thread_read_requests,
        )
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.turn_requests)
        self.assertEqual("thread-1", panel._selected_thread_id)
        self.assertEqual("active", panel._current_goal["status"])
        self.assertTrue(panel._focus_mode)
        self.assertIn("Recovered history", panel.conversation.toPlainText())
        self.assertNotIn("HIA launcher recovery", panel.conversation.toPlainText())
        self.assertEqual(["session"], panel._client.session_contexts)

    def test_crash_recovery_rejects_stale_thread_focus_goal_or_binding(self) -> None:
        for case in ("session-thread", "session-focus"):
            with self.subTest(case=case):
                panel = _make_panel(selected_thread_id=None)
                panel._crash_recovery_marker = {
                    "thread_id": "thread-1",
                    "goal_binding": _RECOVERY_GOAL_BINDING,
                    "prompt_id": _RECOVERY_PROMPT_ID,
                }
                session = _recovery_session(
                    thread_id="thread-other" if case == "session-thread" else "thread-1",
                    focus_mode=case != "session-focus",
                )
                panel._on_health(
                    {
                        "houdini_mcp": {"backend": "hia_v2", "available": True},
                        "session": session,
                    }
                )
                self.assertIsNone(panel._selected_thread_id)
                self.assertEqual([], panel._client.goal_get_requests)
                self.assertEqual([], panel._client.thread_read_requests)
                self.assertEqual([], panel._client.turn_requests)

        goal_cases = {
            "goal-thread": {"thread_id": "thread-other"},
            "goal-focus": {"focus_mode": False},
            "goal-complete": {"status": "complete"},
            "goal-blocked": {"status": "blocked"},
            "goal-binding": {"goal_binding": "b" * 64},
        }
        for case, overrides in goal_cases.items():
            with self.subTest(case=case):
                panel = _make_panel(selected_thread_id=None)
                panel._crash_recovery_marker = {
                    "thread_id": "thread-1",
                    "goal_binding": _RECOVERY_GOAL_BINDING,
                    "prompt_id": _RECOVERY_PROMPT_ID,
                }
                panel._on_health(
                    {
                        "houdini_mcp": {"backend": "hia_v2", "available": True},
                        "session": _recovery_session(),
                    }
                )
                panel._on_action_completed(
                    "goal_get",
                    _recovery_goal_payload(**overrides),
                )
                self.assertIsNone(panel._selected_thread_id)
                self.assertEqual([], panel._client.thread_read_requests)
                self.assertEqual([], panel._client.resume_requests)
                self.assertEqual([], panel._client.turn_requests)

    def test_crash_recovery_rechecks_goal_after_history_read(self) -> None:
        cases = {
            "thread": {"thread_id": "thread-other"},
            "focus": {"focus_mode": False},
            "status": {"status": "blocked"},
            "binding": {"goal_binding": "b" * 64},
        }
        for case, overrides in cases.items():
            with self.subTest(case=case):
                panel = _make_panel(selected_thread_id=None)
                panel._crash_recovery_marker = {
                    "thread_id": "thread-1",
                    "goal_binding": _RECOVERY_GOAL_BINDING,
                    "prompt_id": _RECOVERY_PROMPT_ID,
                }
                panel._on_health(
                    {
                        "houdini_mcp": {"backend": "hia_v2", "available": True},
                        "session": _recovery_session(),
                    }
                )
                panel._on_action_completed("goal_get", _recovery_goal_payload())
                panel._on_action_completed(
                    "thread_read:crash_recovery",
                    _recovery_read_payload(),
                )

                self.assertEqual(["thread-1", "thread-1"], panel._client.goal_get_requests)
                self.assertIsNone(panel._selected_thread_id)
                panel._on_action_completed(
                    "goal_get",
                    _recovery_goal_payload(**overrides),
                )

                self.assertIsNone(panel._selected_thread_id)
                self.assertIsNone(panel._current_goal)
                self.assertEqual([], panel._client.turn_requests)
                self.assertEqual("", panel.conversation.toPlainText())

    def test_crash_recovery_bind_does_not_reapply_the_initial_session(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        panel._crash_recovery_marker = {
            "thread_id": "thread-1",
            "goal_binding": _RECOVERY_GOAL_BINDING,
            "prompt_id": _RECOVERY_PROMPT_ID,
        }
        panel._on_health(
            {
                "houdini_mcp": {"backend": "hia_v2", "available": True},
                "session": _recovery_session(turn_id="initial-turn"),
            }
        )
        panel._on_action_completed("goal_get", _recovery_goal_payload())
        panel._on_action_completed(
            "thread_read:crash_recovery",
            _recovery_read_payload(),
        )

        with mock.patch.object(
            panel,
            "_apply_session",
            wraps=panel._apply_session,
        ) as apply_session:
            panel._on_action_completed("goal_get", _recovery_goal_payload())

        apply_session.assert_not_called()
        self.assertEqual(["session"], panel._client.session_contexts)
        self.assertEqual("thread-1", panel._selected_thread_id)

    def test_recovered_launcher_turn_continues_two_rounds_once_each(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        _prime_crash_recovery_panel(panel)
        self.assertEqual([], panel._client.turn_requests)

        panel._on_events(
            {
                "events": [
                    {
                        "seq": 1,
                        "type": "codex_notification",
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {
                                "id": "launcher-recovery-turn",
                                "status": "inProgress",
                            },
                        },
                    },
                    _completed_notification("launcher-recovery-turn", sequence=2),
                ],
                "gap": False,
            }
        )
        self.assertEqual(1, len(panel._client.turn_requests))
        first_context = panel._client.turn_requests[-1][-1]

        panel._on_events(
            {
                "events": [
                    _completed_notification("launcher-recovery-turn", sequence=3),
                    {
                        "seq": 4,
                        "type": "session_state",
                        "session": _recovery_session(
                            turn_id="launcher-recovery-turn"
                        ),
                    },
                ],
                "gap": False,
            }
        )
        self.assertEqual(1, len(panel._client.turn_requests))

        panel._on_action_completed(
            first_context,
            {
                "thread_id": "thread-1",
                "turn_id": "auto-turn-1",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        panel._on_events(
            {
                "events": [
                    {
                        "seq": 5,
                        "type": "codex_notification",
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "auto-turn-1",
                            "itemId": "message-1",
                            "delta": "Finished another meaningful stage.",
                        },
                    },
                    _completed_notification("auto-turn-1", sequence=6),
                ],
                "gap": False,
            }
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertTrue(
            all(request[0] == panel._client.turn_requests[0][0] for request in panel._client.turn_requests)
        )
        self.assertEqual([], panel._client.resume_requests)

    def test_late_panel_bind_recovers_completed_launcher_turn_without_duplication(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        completed = _recovery_session(
            turn_id="launcher-recovery-turn",
            turn_status="completed",
            turn_active=False,
        )
        _prime_crash_recovery_panel(
            panel,
            initial_session=completed,
            include_launcher_prompt=True,
        )
        self.assertEqual([], panel._client.turn_requests)
        self.assertNotIn("HIA launcher recovery", panel.conversation.toPlainText())

        panel._on_session({"session": completed})
        panel._on_session({"session": completed})

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertIsNone(panel._crash_recovery_observation)
        self.assertEqual([], panel._client.resume_requests)

    def test_recovery_completion_between_reads_is_correlated_once(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        _prime_crash_recovery_panel(panel)

        completed = _recovery_session(turn_id="launcher-recovery-turn")
        panel._on_session({"session": completed})
        self.assertEqual(
            ("thread-1", "thread_read:crash_recovery_recheck"),
            panel._client.thread_read_requests[-1],
        )
        self.assertEqual([], panel._client.turn_requests)

        panel._on_action_completed(
            "thread_read:crash_recovery_recheck",
            _recovery_read_payload(include_launcher_prompt=True),
        )
        panel._on_action_completed(
            "thread_read:crash_recovery_recheck",
            _recovery_read_payload(include_launcher_prompt=True),
        )

        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertIsNone(panel._crash_recovery_observation)

    def test_unselected_panel_network_timeout_does_not_restore_or_render(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        failure = {
            "structured_error": {
                "code": "NETWORK_TIMEOUT",
                "message": "Bridge request timed out",
            }
        }

        panel._on_request_failed("threads", failure)
        panel._reconnect_timer.fire()
        panel._on_request_failed("health", failure)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "warning",
                "params": {"message": "background warning"},
            }
        )
        panel._render_event(
            {
                "type": "protocol_warning",
                "code": "BACKGROUND_PROTOCOL_WARNING",
                "message": "background protocol warning",
            }
        )
        panel._on_events({"events": [], "gap": True})

        self.assertIsNone(panel._selected_thread_id)
        self.assertEqual("", panel.conversation.toPlainText())
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.thread_read_requests)
        self.assertEqual([], panel._client.goal_get_requests)
        self.assertEqual({}, panel._team_records)

    def test_long_history_is_complete_and_missing_name_does_not_clear(self) -> None:
        panel = _make_panel()
        panel._apply_threads(
            [
                {
                    "thread_id": "thread-1",
                    "name": "保留名称",
                    "preview": "preview",
                    "updated_at": 1_752_825_600,
                },
                {
                    "thread_id": "thread-2",
                    "name": "候选会话",
                    "preview": "preview two",
                    "updated_at": 1_752_739_200,
                },
            ]
        )
        panel.history_combo.setCurrentIndex(2)
        panel._on_history_index_changed(2)
        panel._apply_threads(panel._thread_history)
        self.assertEqual("thread-2", panel._selected_history_record()["thread_id"])

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/name/updated",
                "params": {"threadId": "thread-1"},
            }
        )
        self.assertEqual("保留名称", panel._thread_history[0]["name"])

        panel._selected_thread_id = "thread-1"
        panel._turn_state = PanelTurnState()
        panel._render_thread_read(
            {
                "read": {
                    "thread": {
                        "id": "thread-1",
                        "turns": [
                            {
                                "items": [
                                    {
                                        "type": "agentMessage",
                                        "text": f"message-{index}",
                                    }
                                    for index in range(172)
                                ]
                            }
                        ],
                    }
                }
            }
        )
        codex_entries = [
            entry for entry in panel.conversation.entries if entry["role"] == "codex"
        ]
        self.assertEqual(172, len(codex_entries))
        self.assertIn("message-0", panel.conversation.toPlainText())
        self.assertIn("message-171", panel.conversation.toPlainText())
        self.assertNotIn("仅展示最近 100 条", panel.conversation.toPlainText())

    def test_bridge_reconnect_is_bounded_and_never_replays_turn(self) -> None:
        panel = _make_panel()
        panel.input_edit.setPlainText("保留的草稿")
        attachment = r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\ref.png"
        panel.attachment_strip.add_path(attachment)
        failure = {
            "structured_error": {
                "code": "NETWORK_ERROR",
                "message": "Bridge unavailable",
            }
        }

        panel._on_request_failed("events", failure)
        self.assertEqual([500], panel._reconnect_timer.start_delays)
        self.assertEqual("保留的草稿", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertEqual("thread-1", panel._selected_thread_id)

        for expected_delay in (1_000, 2_000, 4_000, 8_000):
            panel._reconnect_timer.fire()
            panel._on_request_failed("health", failure)
            self.assertEqual(expected_delay, panel._reconnect_timer.start_delays[-1])
        panel._reconnect_timer.fire()
        panel._on_request_failed("health", failure)

        self.assertEqual([500, 1_000, 2_000, 4_000, 8_000], panel._reconnect_timer.start_delays)
        self.assertEqual(5, panel._client.health_requests)
        self.assertEqual([], panel._client.turn_requests)
        self.assertIn("请重启 launcher", panel.conversation.toPlainText())

    def test_reconcile_network_failure_releases_lock_before_health_recovery(self) -> None:
        panel = _make_panel()
        context = "session_reconcile:1:1:network"
        panel._reconciliation_tokens[context] = panel._turn_state.capture_token()
        panel._refresh_controls()
        self.assertFalse(panel.new_thread_button.isEnabled())

        panel._on_request_failed(
            context,
            {
                "structured_error": {
                    "code": "NETWORK_ERROR",
                    "message": "Bridge unavailable",
                }
            },
        )
        self.assertEqual({}, panel._reconciliation_tokens)

        panel._reconnect_timer.fire()
        panel._on_health(
            {
                "houdini_mcp": {"backend": "hia_v2", "available": True},
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_active": False,
                },
            }
        )
        self.assertFalse(panel.new_thread_button.isEnabled())
        self.assertEqual(["thread-1"], panel._client.goal_get_requests)
        panel._on_action_completed(
            "goal_get",
            {"thread_id": "thread-1", "goal": None},
        )
        self.assertTrue(panel.new_thread_button.isEnabled())
        self.assertEqual([], panel._client.session_contexts)

    def test_catalog_network_failures_are_refetched_after_reconnect(self) -> None:
        panel = _make_panel()
        panel._models_requested = True
        panel._models_resolved = False
        panel._threads_requested = True
        failure = {
            "structured_error": {
                "code": "NETWORK_ERROR",
                "message": "Bridge unavailable",
            }
        }

        panel._on_request_failed("models", failure)
        panel._on_request_failed("threads", failure)
        self.assertFalse(panel._models_requested)
        self.assertFalse(panel._threads_requested)

        panel._reconnect_timer.fire()
        panel._on_health(
            {
                "houdini_mcp": {"backend": "hia_v2", "available": True},
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_active": False,
                },
            }
        )
        self.assertEqual(1, panel._client.model_requests)
        self.assertEqual(1, panel._client.thread_list_requests)

    def test_live_model_tiers_do_not_auto_open_history(self) -> None:
        panel = _make_panel()
        panel._selected_thread_id = None
        panel._models_resolved = False
        panel._thread_history = []
        panel._apply_threads(
            [
                {
                    "thread_id": "thread-fast",
                    "name": "Fast history",
                    "preview": "",
                    "updated_at": 1_752_825_600,
                }
            ]
        )
        self.assertEqual([], panel._client.resume_requests)

        panel._on_action_completed(
            "models",
            {
                "models": [
                    {
                        "model": "dynamic-model",
                        "displayName": "Dynamic Model",
                        "description": "",
                        "isDefault": True,
                        "inputModalities": ["text"],
                        "supportedReasoningEfforts": [],
                        "defaultReasoningEffort": None,
                        "serviceTiers": [
                            {
                                "id": "live-fast",
                                "name": "快速",
                                "description": "live model/list tier",
                            }
                        ],
                        "defaultServiceTier": "live-fast",
                    }
                ]
            },
        )
        self.assertEqual([], panel._client.resume_requests)
        panel.history_combo.setCurrentIndex(1)
        panel._on_history_index_changed(1)
        panel._resume_thread()
        self.assertEqual(
            [("thread-fast", "live-fast", "session_resume")],
            panel._client.resume_requests,
        )

    def test_process_exit_freezes_active_turn_without_marking_terminal(self) -> None:
        panel = _make_panel()
        _context, _turn_id = _start_active_turn(panel, 1)
        self.assertTrue(panel._turn_state.busy)

        panel._render_event({"type": "process_exit"})

        self.assertEqual(1, panel.conversation.freeze_calls)
        self.assertTrue(panel._turn_state.busy)
        self.assertEqual(
            "Turn：状态待确认（app-server 已退出）",
            panel.turn_status_label.text(),
        )
        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertIn("请重启 launcher", panel.conversation.toPlainText())

    def test_successful_reconnect_syncs_session_and_read_without_replay(self) -> None:
        panel = _make_panel()
        panel.input_edit.setPlainText("draft")
        panel._on_request_failed(
            "events",
            {
                "structured_error": {
                    "code": "NETWORK_TIMEOUT",
                    "message": "timeout",
                }
            },
        )
        panel._reconnect_timer.fire()
        panel._on_health(
            {
                "houdini_mcp": {"backend": "hia_v2", "available": True},
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_active": False,
                },
            }
        )

        self.assertEqual(0, panel._reconnect_attempt)
        self.assertFalse(panel._reconnecting)
        self.assertEqual([], panel._client.session_contexts)
        self.assertEqual([], panel._client.thread_read_requests)
        self.assertEqual("draft", panel.input_edit.toPlainText())
        self.assertEqual([], panel._client.turn_requests)

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

    def test_goal_stays_bound_to_the_selected_codex_thread(self) -> None:
        panel = _make_panel()
        panel._request_goal()
        self.assertEqual(["thread-1"], panel._client.goal_get_requests)

        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋材质与灯光",
            "status": "active",
            "tokenBudget": 20_000,
            "tokensUsed": 120,
            "timeUsedSeconds": 8,
        }
        panel._on_action_completed(
            "goal_get",
            {"thread_id": "thread-1", "goal": goal},
        )
        self.assertEqual("完成木屋材质与灯光", panel.goal_objective_edit.toPlainText())
        self.assertEqual("20000", panel.goal_budget_edit.text())

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "older-thread",
                    "goal": {**goal, "threadId": "older-thread", "objective": "旧 Goal"},
                },
            }
        )
        self.assertEqual("完成木屋材质与灯光", panel.goal_objective_edit.toPlainText())

        panel.goal_objective_edit.setPlainText("主任务新 Goal")
        panel.goal_budget_edit.setText("30000")
        panel._save_goal()
        self.assertEqual(
            [("thread-1", "主任务新 Goal", "active", 30_000)],
            panel._client.goal_set_requests,
        )
        panel._on_action_completed(
            "goal_set",
            {
                "thread_id": "thread-1",
                "goal": {**goal, "objective": "主任务新 Goal"},
            },
        )
        panel._clear_goal()
        self.assertEqual(["thread-1"], panel._client.goal_clear_requests)
        panel._on_action_completed(
            "goal_clear",
            {"thread_id": "thread-1", "cleared": True},
        )
        self.assertEqual("", panel.goal_objective_edit.toPlainText())

    def test_goal_status_is_read_only_and_blocked_requires_explicit_continue(self) -> None:
        panel = _make_panel()
        self.assertFalse(hasattr(panel, "goal_status_combo"))
        active_goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", active_goal)
        self.assertEqual("状态：正在跟进", panel.goal_status_label.text())
        self.assertEqual("保存（继续跟进）", panel.goal_save_button.text())

        panel._apply_focus_mode("thread-1", True)
        panel._apply_goal(
            "thread-1",
            {**active_goal, "status": "blocked"},
        )
        self.assertIn("未提供原因", panel.goal_status_label.text())
        blocked_goal = {
            **active_goal,
            "status": "blocked",
            "reason": "请先保存 HIP",
        }
        panel._apply_goal("thread-1", blocked_goal)
        self.assertIn("状态：等待你处理", panel.goal_status_label.text())
        self.assertIn("请先保存 HIP", panel.goal_status_label.text())
        self.assertEqual("继续跟进", panel.goal_save_button.text())
        self.assertIn("完成后点继续跟进", panel.goal_activity_label.text())
        self.assertFalse(panel.goal_focus_checkbox.isChecked())
        self.assertEqual([], panel._client.goal_set_requests)

        panel._save_goal()
        self.assertEqual(
            [("thread-1", "完成木屋", "active", None)],
            panel._client.goal_set_requests,
        )
        panel._on_action_completed(
            "goal_set",
            {"thread_id": "thread-1", "goal": active_goal},
        )
        self.assertEqual("状态：正在跟进", panel.goal_status_label.text())

        complete_goal = {**active_goal, "status": "complete"}
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {"threadId": "thread-1", "goal": complete_goal},
            }
        )
        self.assertEqual("状态：已完成", panel.goal_status_label.text())
        self.assertEqual("当前跟进：Goal 已完成", panel.goal_activity_label.text())

    def test_native_goal_turn_activity_is_correlated_without_polluting_normal_turns(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn-a",
                    "goal": goal,
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "goal-turn-a", "status": "inProgress"},
                },
            }
        )
        self.assertIn("尚无文字输出", panel.conversation.toPlainText())
        self.assertIn("尚无文字输出", panel.goal_activity_label.text())
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn-a",
                    "plan": [
                        {"step": "检查场景", "status": "completed"},
                        {"step": "保存 HIP", "status": "inProgress"},
                        {"step": "继续建模", "status": "pending"},
                    ],
                },
            }
        )
        self.assertEqual("goal-turn-a", panel._goal_turn_id)
        self.assertIn("保存 HIP", panel.goal_activity_label.text())
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn-a",
                    "itemId": "goal-message-a",
                    "delta": "请先保存 HIP，然后我会继续。",
                },
            }
        )
        self.assertIn("请先保存 HIP", panel.conversation.toPlainText())
        self.assertNotIn("尚无文字输出", panel.goal_activity_label.text())
        self.assertEqual(
            1,
            sum(
                entry["role"] == "codex"
                for entry in panel.conversation.entries
            ),
        )

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn-a",
                    "plan": [
                        {"step": "检查场景", "status": "completed"},
                        {"step": "保存 HIP", "status": "completed"},
                    ],
                },
            }
        )
        self.assertIn("Codex 正在推进 Goal", panel.goal_activity_label.text())
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "goal-turn-a", "status": "completed"},
                },
            }
        )
        self.assertIsNone(panel._goal_turn_id)
        self.assertIsNone(panel.conversation._active_codex_index)
        self.assertIn("等待下一轮任务进展", panel.goal_activity_label.text())

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "goal-turn-b", "status": "inProgress"},
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "goal-turn-b",
                    "plan": [{"step": "替代方案", "status": "pending"}],
                },
            }
        )
        self.assertIn("替代方案", panel.goal_activity_label.text())
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "other-thread",
                    "turnId": "other-turn",
                    "plan": [{"step": "不得显示", "status": "inProgress"}],
                },
            }
        )
        self.assertNotIn("不得显示", panel.goal_activity_label.text())
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "goal-turn-b", "status": "completed"},
                },
            }
        )

        _context, normal_turn_id = _start_active_turn(panel, 3)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": normal_turn_id,
                    "itemId": "normal-message",
                    "delta": "普通聊天回复",
                },
            }
        )
        self.assertIn("普通聊天回复", panel.conversation.toPlainText())
        self.assertIsNone(panel._goal_turn_id)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": normal_turn_id,
                    "plan": [{"step": "普通聊天计划", "status": "inProgress"}],
                },
            }
        )
        self.assertNotIn("普通聊天计划", panel.goal_activity_label.text())
        panel._render_event(_completed_notification(normal_turn_id, sequence=99))
        self.assertIn("等待下一轮任务进展", panel.goal_activity_label.text())

    def test_focused_goal_completion_continues_without_fake_user_message(self) -> None:
        panel = _make_panel()
        panel._apply_models(
            [
                {
                    "model": "goal-model",
                    "displayName": "Goal Model",
                    "isDefault": True,
                    "inputModalities": ["text", "image"],
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "high", "description": "High"}
                    ],
                    "defaultReasoningEffort": "high",
                    "serviceTiers": [
                        {"id": "priority", "name": "快速", "description": "Fast"}
                    ],
                    "defaultServiceTier": "priority",
                }
            ]
        )
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._apply_focus_mode("thread-1", True)
        panel.input_edit.setPlainText("用户正在编辑的追加内容")
        attachment = "E:/houdini-intelligence-agent/.runtime/attachments/reference.png"
        panel.attachment_strip.add_path(attachment)
        system_count = sum(
            entry["role"] == "system" for entry in panel.conversation.entries
        )

        panel._on_events(
            {
                "events": [
                    {
                        "seq": 1,
                        "type": "codex_notification",
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "goal-turn", "status": "inProgress"},
                        },
                    },
                    {
                        "seq": 2,
                        "type": "codex_notification",
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "goal-turn", "status": "completed"},
                        },
                    },
                    {
                        "seq": 3,
                        "type": "codex_notification",
                        "method": "thread/goal/updated",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "goal-turn",
                            "goal": goal,
                        },
                    },
                ],
                "gap": False,
            }
        )

        self.assertEqual(1, len(panel._client.turn_requests))
        text, model, effort, images, context = panel._client.turn_requests[0]
        self.assertEqual(
            "继续推进当前 Goal；先核对上一轮真实结果，再执行下一项未完成工作。",
            text,
        )
        self.assertEqual("goal-model", model)
        self.assertEqual("high", effort)
        self.assertEqual("priority", panel._client.turn_service_tiers[0])
        self.assertEqual([], images)
        self.assertEqual("用户正在编辑的追加内容", panel.input_edit.toPlainText())
        self.assertEqual([attachment], panel.attachment_strip.paths())
        self.assertNotIn(text, panel.conversation.toPlainText())
        self.assertEqual(
            0,
            sum(entry["role"] == "user" for entry in panel.conversation.entries),
        )
        self.assertEqual(
            system_count,
            sum(entry["role"] == "system" for entry in panel.conversation.entries),
        )

        panel._on_events(
            {
                "events": [
                    {
                        "seq": 4,
                        "type": "codex_notification",
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "goal-turn", "status": "completed"},
                        },
                    },
                    {
                        "seq": 5,
                        "type": "session_state",
                        "session": {
                            "connected": True,
                            "authentication": "authenticated",
                            "thread_id": "thread-1",
                            "turn_id": "goal-turn",
                            "turn_status": "completed",
                            "turn_active": False,
                            "focus_mode": True,
                        },
                    },
                ],
                "gap": False,
            }
        )
        panel._apply_threads(panel._thread_history)
        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertEqual([], panel._client.session_contexts)

        panel._on_action_completed(
            context,
            {
                "thread_id": "thread-1",
                "turn_id": "auto-turn",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        self.assertEqual("追加指令", panel.send_button.text())
        panel._send()
        steer_text, steer_images, steer_context = panel._client.steer_requests[-1]
        self.assertEqual("用户正在编辑的追加内容", steer_text)
        self.assertEqual([attachment], steer_images)
        panel._on_action_completed(
            steer_context,
            {"thread_id": "thread-1", "turn_id": "auto-turn"},
        )
        self.assertIn("用户正在编辑的追加内容", panel.conversation.toPlainText())
        panel._on_events(
            {
                "events": [
                    {
                        "seq": 6,
                        "type": "codex_notification",
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "auto-turn",
                            "itemId": "auto-message",
                            "delta": "已完成这一阶段。",
                        },
                    },
                    _completed_notification("auto-turn", sequence=7),
                ],
                "gap": False,
            }
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertNotIn(
            panel._client.turn_requests[-1][0],
            [
                entry.get("text", "")
                for entry in panel.conversation.entries
                if entry.get("role") == "user"
            ],
        )

    def test_normal_turn_completion_continues_only_once(self) -> None:
        panel = _make_panel()
        panel._apply_goal(
            "thread-1",
            {"threadId": "thread-1", "objective": "完成木屋", "status": "active"},
        )
        panel._apply_focus_mode("thread-1", True)
        _context, turn_id = _start_active_turn(panel, 1)

        panel._on_events(
            {"events": [_completed_notification(turn_id)], "gap": False}
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertEqual(
            "继续推进当前 Goal；先核对上一轮真实结果，再执行下一项未完成工作。",
            panel._client.turn_requests[-1][0],
        )
        self.assertNotEqual(
            panel._client.turn_requests[0][0], panel._client.turn_requests[1][0]
        )

        panel._on_events(
            {
                "events": [
                    _completed_notification(turn_id, sequence=2),
                    {
                        "seq": 3,
                        "type": "session_state",
                        "session": {
                            "connected": True,
                            "authentication": "authenticated",
                            "thread_id": "thread-1",
                            "turn_id": turn_id,
                            "turn_status": "completed",
                            "turn_active": False,
                            "focus_mode": True,
                        },
                    },
                ],
                "gap": False,
            }
        )
        self.assertEqual(2, len(panel._client.turn_requests))

    def test_stop_pauses_goal_continuation_until_explicit_save(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._apply_focus_mode("thread-1", True)
        _context, turn_id = _start_active_turn(panel, 1)

        panel._stop()
        panel._on_events(
            {"events": [_completed_notification(turn_id)], "gap": False}
        )
        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertTrue(panel._goal_continuation_paused)
        self.assertEqual("active", panel._current_goal["status"])

        interrupt_context = panel._client.interrupt_contexts[-1]
        panel._on_action_completed(
            interrupt_context,
            {
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": turn_id,
                    "turn_status": "interrupted",
                    "turn_active": False,
                    "focus_mode": True,
                }
            },
        )
        self.assertIn("已暂停", panel.goal_activity_label.text())
        panel._save_goal()
        panel._on_events(
            {
                "events": [
                    {
                        "seq": 2,
                        "type": "codex_notification",
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "goal-save-turn", "status": "inProgress"},
                        },
                    },
                    _completed_notification("goal-save-turn", sequence=3),
                ],
                "gap": False,
            }
        )
        self.assertEqual(1, len(panel._client.turn_requests))
        panel._on_action_completed(
            "goal_set",
            {"thread_id": "thread-1", "goal": goal, "focus_mode": True},
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertFalse(panel._goal_continuation_paused)

    def test_goal_save_waits_for_native_goal_completion_before_continuing(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._apply_focus_mode("thread-1", True)

        panel._save_goal()
        panel._on_action_completed(
            "goal_set",
            {"thread_id": "thread-1", "goal": goal, "focus_mode": True},
        )
        self.assertEqual([], panel._client.turn_requests)

        panel._on_events(
            {
                "events": [
                    {
                        "seq": 1,
                        "type": "codex_notification",
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "goal-save-turn", "status": "inProgress"},
                        },
                    },
                    _completed_notification("goal-save-turn", sequence=2),
                ],
                "gap": False,
            }
        )
        self.assertEqual(1, len(panel._client.turn_requests))
        self.assertFalse(panel._goal_continuation_paused)

    def test_goal_continuation_respects_all_existing_safety_gates(self) -> None:
        cases = (
            "focus-off",
            "goal-complete",
            "goal-blocked",
            "goal-cleared",
            "goal-completes-same-batch",
            "disconnected",
            "recovering",
            "approval",
            "goal-action",
            "session-action",
            "steer",
            "reconciliation",
            "scene-capability",
            "scene-work",
        )
        for case in cases:
            with self.subTest(case=case):
                panel = _make_panel()
                goal = {
                    "threadId": "thread-1",
                    "objective": "完成木屋",
                    "status": "active",
                }
                panel._apply_goal("thread-1", goal)
                panel._apply_focus_mode("thread-1", True)
                _context, turn_id = _start_active_turn(panel, 1)
                if case == "focus-off":
                    panel._apply_focus_mode("thread-1", False)
                elif case == "goal-complete":
                    panel._apply_goal("thread-1", {**goal, "status": "complete"})
                elif case == "goal-blocked":
                    panel._apply_goal("thread-1", {**goal, "status": "blocked"})
                elif case == "goal-cleared":
                    panel._apply_goal("thread-1", None)
                elif case == "disconnected":
                    panel._connected = False
                elif case == "recovering":
                    panel._stop_recovery_state = "recovering"
                elif case == "approval":
                    panel._current_approval = {"request_id": "approval-1"}
                elif case == "goal-action":
                    panel._goal_action_context = "goal_get"
                elif case == "session-action":
                    panel._session_action_pending = True
                elif case == "steer":
                    panel._turn_steer_request_pending = True
                elif case == "reconciliation":
                    panel._reconciliation_tokens["session_reconcile:test"] = (
                        panel._turn_state.capture_token()
                    )
                elif case == "scene-capability":
                    panel._scene_capability_pending = True
                elif case == "scene-work":
                    panel._scene_work_pending = True

                events = [_completed_notification(turn_id)]
                if case == "goal-completes-same-batch":
                    events.append(
                        {
                            "seq": 2,
                            "type": "codex_notification",
                            "method": "thread/goal/updated",
                            "params": {
                                "threadId": "thread-1",
                                "goal": {**goal, "status": "complete"},
                            },
                        }
                    )
                panel._on_events({"events": events, "gap": False})
                self.assertEqual(1, len(panel._client.turn_requests))

    def test_empty_auto_continuation_pauses_without_a_fast_loop(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._apply_focus_mode("thread-1", True)
        _context, turn_id = _start_active_turn(panel, 1)
        panel._on_events(
            {"events": [_completed_notification(turn_id)], "gap": False}
        )
        self.assertEqual(2, len(panel._client.turn_requests))

        auto_context = panel._client.turn_requests[-1][-1]
        panel._on_action_completed(
            auto_context,
            {
                "thread_id": "thread-1",
                "turn_id": "auto-empty",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        panel._on_events(
            {
                "events": [_completed_notification("auto-empty", sequence=2)],
                "gap": False,
            }
        )

        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertTrue(panel._goal_continuation_paused)
        self.assertEqual("active", panel._current_goal["status"])
        self.assertIn("已暂停", panel.goal_activity_label.text())
        pause_notices = [
            entry
            for entry in panel.conversation.entries
            if entry.get("role") == "system"
            and "没有返回文字或工具活动" in entry.get("text", "")
        ]
        self.assertEqual(1, len(pause_notices))

        panel._on_events(
            {
                "events": [
                    _completed_notification("auto-empty", sequence=3),
                    {
                        "seq": 4,
                        "type": "session_state",
                        "session": {
                            "connected": True,
                            "authentication": "authenticated",
                            "thread_id": "thread-1",
                            "turn_id": "auto-empty",
                            "turn_status": "completed",
                            "turn_active": False,
                            "focus_mode": True,
                        },
                    },
                ],
                "gap": False,
            }
        )
        self.assertEqual(2, len(panel._client.turn_requests))
        self.assertEqual(
            1,
            sum(
                entry.get("role") == "system"
                and "没有返回文字或工具活动" in entry.get("text", "")
                for entry in panel.conversation.entries
            ),
        )

    def test_non_mcp_tool_activity_counts_as_auto_continuation_progress(self) -> None:
        panel = _make_panel()
        panel._apply_goal(
            "thread-1",
            {"threadId": "thread-1", "objective": "完成木屋", "status": "active"},
        )
        panel._apply_focus_mode("thread-1", True)
        _context, turn_id = _start_active_turn(panel, 1)
        panel._on_events(
            {"events": [_completed_notification(turn_id)], "gap": False}
        )

        auto_context = panel._client.turn_requests[-1][-1]
        panel._on_action_completed(
            auto_context,
            {
                "thread_id": "thread-1",
                "turn_id": "auto-command",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        panel._on_events(
            {
                "events": [
                    {
                        "seq": 2,
                        "type": "codex_notification",
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "auto-command",
                            "item": {
                                "id": "command-1",
                                "type": "commandExecution",
                                "status": "completed",
                            },
                        },
                    },
                    _completed_notification("auto-command", sequence=3),
                ],
                "gap": False,
            }
        )

        self.assertFalse(panel._goal_continuation_paused)
        self.assertEqual(3, len(panel._client.turn_requests))

    def test_goal_update_cannot_rebind_an_active_auto_turn(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._apply_focus_mode("thread-1", True)
        _context, turn_id = _start_active_turn(panel, 1)
        panel._on_events(
            {"events": [_completed_notification(turn_id)], "gap": False}
        )

        auto_context = panel._client.turn_requests[-1][-1]
        panel._on_action_completed(
            auto_context,
            {
                "thread_id": "thread-1",
                "turn_id": "auto-turn",
                "turn_active": True,
                "turn_status": "inProgress",
            },
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "auto-turn",
                    "goal": goal,
                },
            }
        )
        self.assertIsNone(panel._goal_turn_id)

        panel._on_events(
            {
                "events": [
                    {
                        "seq": 2,
                        "type": "codex_notification",
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "auto-turn",
                            "itemId": "message-1",
                            "delta": "已完成一个阶段。",
                        },
                    },
                    _completed_notification("auto-turn", sequence=3),
                ],
                "gap": False,
            }
        )
        self.assertEqual(3, len(panel._client.turn_requests))
        self.assertEqual(TurnPhase.STARTING, panel._turn_state.phase)
        self.assertIsNone(panel._turn_state.turn_id)

    def test_turn_and_focus_failures_never_rewrite_authoritative_goal(self) -> None:
        panel = _make_panel()
        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        panel._apply_goal("thread-1", goal)
        panel._set_focus_mode(False)
        self.assertEqual([], panel._client.goal_set_requests)

        _context, _turn_id = _start_active_turn(panel, 1)
        panel.input_edit.setPlainText("追加失败也不能阻塞 Goal")
        panel._send()
        _text, _images, steer_context = panel._client.steer_requests[-1]
        panel._on_request_failed(
            steer_context,
            {
                "structured_error": {
                    "code": "CODEX_RPC_ERROR",
                    "message": "temporary failure",
                }
            },
        )
        panel._stop()

        self.assertEqual(goal, panel._current_goal)
        self.assertEqual("状态：正在跟进", panel.goal_status_label.text())
        self.assertEqual([], panel._client.goal_set_requests)

    def test_focus_mode_requires_active_goal_and_restores_from_session(self) -> None:
        panel = _make_panel()
        panel.goal_focus_checkbox.setChecked(True)
        panel._set_focus_mode(True)
        self.assertEqual([], panel._client.focus_mode_requests)
        self.assertFalse(panel.goal_focus_checkbox.isChecked())
        self.assertIn("请先填写并保存", panel.goal_focus_hint_label.text())

        goal = {
            "threadId": "thread-1",
            "objective": "完成木屋",
            "status": "active",
        }
        self.assertTrue(panel._apply_goal("thread-1", goal))
        panel.goal_focus_checkbox.setChecked(True)
        panel._set_focus_mode(True)
        self.assertEqual([("thread-1", True)], panel._client.focus_mode_requests)
        self.assertEqual("focus_set", panel._goal_action_context)
        panel._on_action_completed(
            "focus_set",
            {"thread_id": "thread-1", "focus_mode": True},
        )
        self.assertTrue(panel.goal_focus_checkbox.isChecked())
        self.assertIn("异常退出后会尝试恢复", panel.goal_focus_hint_label.text())

        restored = _make_panel()
        token = restored._turn_state.capture_token()
        self.assertTrue(
            restored._apply_session(
                {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_id": None,
                    "turn_status": None,
                    "turn_active": False,
                    "focus_mode": True,
                },
                token=token,
                allow_followup=False,
            )
        )
        self.assertTrue(restored.goal_focus_checkbox.isChecked())

        restored._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "thread-1",
                    "goal": {
                        "threadId": "thread-1",
                        "objective": "完成木屋",
                        "status": "blocked",
                    },
                },
            }
        )
        self.assertFalse(restored.goal_focus_checkbox.isChecked())
        restored._render_event(
            {
                "type": "codex_notification",
                "method": "thread/goal/updated",
                "params": {
                    "threadId": "thread-1",
                    "goal": {
                        "threadId": "thread-1",
                        "objective": "完成木屋",
                        "status": "active",
                    },
                },
            }
        )
        self.assertFalse(restored.goal_focus_checkbox.isChecked())

    def test_goal_inflight_blocks_thread_switch_and_rebinds_after_resume(self) -> None:
        panel = _make_panel()
        panel._turn_performance_marks = {"sent": 1.0, "ack": 2.0}
        panel._render_turn_performance()
        panel._request_goal()

        self.assertEqual("goal_get", panel._goal_action_context)
        self.assertFalse(panel.new_thread_button.isEnabled())
        self.assertFalse(panel.resume_thread_button.isEnabled())
        self.assertFalse(panel.history_combo.isEnabled())
        self.assertFalse(panel.thread_id_edit.isEnabled())

        panel._new_thread()
        panel._request_thread_resume("thread-2", context="session_resume")
        self.assertEqual([], panel._client.thread_requests)
        self.assertEqual([], panel._client.resume_requests)

        panel._session_action_pending = True
        panel._on_action_completed(
            "session_resume",
            {
                "thread_id": "thread-2",
                "read": {"thread": {"id": "thread-2", "turns": []}},
            },
        )
        self.assertEqual("thread-2", panel._selected_thread_id)
        self.assertEqual(
            ["thread-1", "thread-2"],
            panel._client.goal_get_requests,
        )
        self.assertEqual({}, panel._turn_performance_marks)
        self.assertIn("发送 → ACK：—", panel.performance_label.text())

        panel._on_action_completed(
            "goal_get",
            {
                "thread_id": "thread-1",
                "goal": {
                    "threadId": "thread-1",
                    "objective": "旧任务 Goal",
                    "status": "active",
                },
            },
        )
        self.assertNotIn("旧任务 Goal", panel.goal_objective_edit.toPlainText())
        self.assertEqual("goal_get", panel._goal_action_context)
        self.assertEqual(
            ["thread-1", "thread-2", "thread-2"],
            panel._client.goal_get_requests,
        )

        panel._on_action_completed(
            "goal_get",
            {
                "thread_id": "thread-2",
                "goal": {
                    "threadId": "thread-2",
                    "objective": "新任务 Goal",
                    "status": "active",
                },
            },
        )
        self.assertIsNone(panel._goal_action_context)
        self.assertEqual("新任务 Goal", panel.goal_objective_edit.toPlainText())

        blocked = _make_panel()
        blocked._session_action_pending = True
        blocked.goal_objective_edit.setPlainText("不会发送")
        blocked._request_goal()
        blocked._save_goal()
        blocked._clear_goal()
        self.assertEqual([], blocked._client.goal_get_requests)
        self.assertEqual([], blocked._client.goal_set_requests)
        self.assertEqual([], blocked._client.goal_clear_requests)

    def test_native_subagent_events_stay_in_team_panel_and_open_on_demand(self) -> None:
        panel = _make_panel()
        conversation_before = list(panel.conversation.entries)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-lead",
                    "item": {
                        "id": "collab-1",
                        "type": "collabAgentToolCall",
                        "tool": "spawnAgent",
                        "status": "completed",
                        "senderThreadId": "thread-1",
                        "receiverThreadIds": ["thread-review"],
                        "agentsStates": {
                            "thread-review": {
                                "status": "completed",
                                "message": "材质审阅发现：粗糙度过低。",
                            }
                        },
                        "prompt": "审阅材质与灯光",
                        "model": "catalog-review-model",
                    },
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-review",
                    "turnId": "turn-review",
                    "item": {
                        "id": "tool-1",
                        "type": "mcpToolCall",
                        "tool": "web/search",
                        "status": "failed",
                        "error": {"message": "reference unavailable"},
                    },
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-review",
                    "turn": {"id": "turn-review", "status": "completed"},
                },
            }
        )

        panel._on_team_selected()

        self.assertEqual("thread-1", panel._selected_thread_id)
        self.assertEqual(conversation_before, panel.conversation.entries)
        self.assertEqual([], panel._client.thread_read_requests)
        details = panel.team_details_text.toPlainText()
        self.assertIn("审阅材质与灯光", details)
        self.assertIn("web/search：failed", details)
        self.assertIn("reference unavailable", details)
        self.assertIn("材质审阅发现：粗糙度过低", details)
        self.assertIn("协议未单独报告", details)
        self.assertEqual([], panel._client.session_contexts)

    def test_team_events_are_scoped_to_the_current_root_thread(self) -> None:
        panel = _make_panel()
        panel._selected_thread_id = "thread-2"

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "old-root",
                    "turnId": "old-turn",
                    "item": {
                        "id": "old-activity",
                        "type": "subAgentActivity",
                        "agentThreadId": "old-child",
                        "agentPath": "old/review",
                        "kind": "started",
                    },
                },
            }
        )
        self.assertEqual({}, panel._team_records)

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-2",
                    "turnId": "turn-2",
                    "item": {
                        "id": "activity-2",
                        "type": "subAgentActivity",
                        "agentThreadId": "child-2",
                        "agentPath": "review/material",
                        "kind": "started",
                    },
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "child-2",
                    "turnId": "child-turn",
                    "itemId": "agent-message",
                    "delta": "公开审阅结论",
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": "child-2",
                    "turn": {"id": "child-turn", "status": "completed"},
                },
            }
        )

        self.assertNotIn("公开审阅结论", panel.conversation.toPlainText())
        self.assertNotIn("old-child", panel._team_records)
        self.assertEqual(
            "thread-2",
            panel._team_records["child-2"]["root_thread_id"],
        )
        self.assertEqual(
            "公开审阅结论",
            panel._team_records["child-2"]["message"],
        )
        self.assertEqual("completed", panel._team_records["child-2"]["status"])
        bounded = panel._bounded_team_text("x" * 70_000)
        self.assertLess(len(bounded), 66_000)
        self.assertIn("已截断显示", bounded)

    def test_turn_performance_reports_three_local_spans(self) -> None:
        panel = _make_panel()
        panel_time = HoudiniIntelligencePanel._begin_turn_performance.__globals__["time"]
        with mock.patch.object(
            panel_time,
            "monotonic",
            side_effect=(10.0, 12.0, 15.0, 20.0),
        ):
            context, turn_id = _start_active_turn(panel, 1)
            self.assertTrue(context.startswith("turn_start:"))
            panel._render_event(
                {
                    "type": "codex_notification",
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": turn_id,
                        "itemId": "agent-1",
                        "delta": "首个文本",
                    },
                }
            )
            panel._render_event(_completed_notification(turn_id))

        self.assertEqual(
            "发送 → ACK：2.00s\nACK → 首个文本：3.00s\n首个文本 → 完成：5.00s",
            panel.performance_label.text(),
        )


if __name__ == "__main__":
    unittest.main()
