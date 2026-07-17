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

from hia_panel.turn_state import PanelTurnState, TurnPhase  # noqa: E402


class _HeadlessQWidget:
    def closeEvent(self, event: Any) -> None:
        event.base_close_calls += 1


class _HeadlessTimer:
    @staticmethod
    def singleShot(_delay_ms: int, callback: Any) -> None:
        callback()


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

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:
        return self._enabled

    def setVisible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

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


class _BridgeClientShim:
    def __init__(self) -> None:
        self.turn_requests: list[tuple[str, str | None, str | None, str]] = []
        self.thread_requests: list[str | None] = []
        self.interrupt_contexts: list[str] = []
        self.session_contexts: list[str] = []
        self.model_requests = 0
        self.dispose_calls = 0
        self.capability_reports: list[dict[str, Any]] = []
        self.scene_polls: list[int] = []
        self.scene_results: list[tuple[str, str, dict[str, Any]]] = []

    def start_thread(self, *, model: str | None) -> None:
        self.thread_requests.append(model)

    def start_turn(
        self,
        text: str,
        *,
        model: str | None,
        effort: str | None,
        context: str,
    ) -> None:
        self.turn_requests.append((text, model, effort, context))

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


def _make_panel() -> Any:
    panel = object.__new__(HoudiniIntelligencePanel)
    panel._pane_tab = None
    panel._event_sequence = 0
    panel._polling_enabled = False
    panel._connected = True
    panel._authenticated = True
    panel._selected_thread_id = "thread-1"
    panel._session_action_pending = False
    panel._turn_start_request_pending = False
    panel._interrupt_pending = False
    panel._turn_state = PanelTurnState()
    panel._turn_start_tokens = {}
    panel._active_turn_start_context = None
    panel._interrupt_tokens = {}
    panel._active_interrupt_context = None
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
    panel._scene_executor_token = "executor-secret"
    panel._client = _BridgeClientShim()

    panel.connection_label = _Widget()
    panel.auth_label = _Widget()
    panel.thread_status_label = _Widget("Thread：thread-1")
    panel.turn_status_label = _Widget("Turn：空闲")
    panel.houdini_build_label = _Widget("Build：不可用")
    panel.houdini_session_label = _Widget("HIP Session：不可用")
    panel.houdini_revision_label = _Widget("Revision：不可用")
    panel.houdini_catalog_label = _Widget("Catalog：未验证")
    panel.houdini_schema_label = _Widget("Schema：未验证")
    panel.houdini_tools_label = _Widget()
    panel.thread_id_edit = _Widget("thread-1")
    panel.new_thread_button = _Widget()
    panel.resume_thread_button = _Widget()
    panel.send_button = _Widget()
    panel.stop_button = _Widget()
    panel.conversation = _Widget()
    panel.approval_group = _Widget()
    panel.approval_text = _Widget()
    panel.allow_button = _Widget()
    panel.deny_button = _Widget()
    panel.input_edit = _Widget()
    panel.model_combo = _Widget()
    panel.model_combo.addItem("Codex 默认", None)
    panel.effort_combo = _Widget()
    panel.effort_combo.addItem("Codex 默认", None)
    panel._refresh_controls()
    return panel


def _start_active_turn(panel: Any, turn_number: int) -> tuple[str, str]:
    panel.input_edit.setPlainText(f"request {turn_number}")
    panel._send()
    _text, _model, _effort, context = panel._client.turn_requests[-1]
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

    def test_close_event_only_disposes_local_client_and_is_repeat_safe(self) -> None:
        panel = _make_panel()
        client = panel._client
        event = _CloseEvent()
        panel._polling_enabled = True

        panel.closeEvent(event)
        panel.closeEvent(event)

        self.assertFalse(panel._polling_enabled)
        self.assertIsNone(panel._client)
        self.assertEqual(1, client.dispose_calls)
        self.assertEqual(2, event.base_close_calls)
        self.assertFalse(hasattr(client, "shutdown"))

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
        self.assertIn("匹配", panel.houdini_catalog_label.text())
        self.assertIn("scene_info：可用", panel.houdini_tools_label.text())
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
        self.assertIn("scene_info：不可用", panel.houdini_tools_label.text())

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
        self.assertIn("scene_info：待认证", panel.houdini_tools_label.text())
        self.assertNotIn("scene_info：可用", panel.houdini_tools_label.text())

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
            self.assertIn("scene_info：可用", panel.houdini_tools_label.text())
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
            self.assertIn("scene_info：不可用", panel.houdini_tools_label.text())

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
        self.assertIn("scene_info：可用", panel.houdini_tools_label.text())
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
        self.assertIn("scene_info：待认证", panel.houdini_tools_label.text())

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
        self.assertIn("scene_info：不可用", panel.houdini_tools_label.text())

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
        self.assertIn("scene_info：不可用", panel.houdini_tools_label.text())

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
                "params": {"delta": "final visible delta"},
            }
        )
        self.assertIn("final visible delta", panel.conversation.toPlainText())
        self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)

        panel._stop()
        self.assertEqual(1, len(panel._client.interrupt_contexts))
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
                        "turn_id": turn_id,
                        "turn_status": "completed",
                        "turn_active": False,
                    },
                },
            },
        )

        self.assert_idle_controls(panel)
        rendered = panel.conversation.toPlainText()
        self.assertIn("Turn 已完成", rendered)
        self.assertNotIn("NO_ACTIVE_TURN", rendered)
        self.assertNotIn("No interruptible active Turn", rendered)

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
        _text, _model, _effort, start_context = panel._client.turn_requests[-1]
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
            _context, turn_id = _start_active_turn(panel, turn_number)
            self.assertEqual(TurnPhase.IN_PROGRESS, panel._turn_state.phase)
            self.assertFalse(panel.send_button.isEnabled())
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
        self.assertIn("method=future/unknown/notification", warning)

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

        text, model, effort, _context = panel._client.turn_requests[-1]
        self.assertEqual(original, text)
        self.assertEqual("catalog-model-one", model)
        self.assertEqual("medium", effort)
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
        text, model, effort, _context = panel._client.turn_requests[-1]
        self.assertEqual("fallback chat remains available", text)
        self.assertIsNone(model)
        self.assertIsNone(effort)


if __name__ == "__main__":
    unittest.main()
