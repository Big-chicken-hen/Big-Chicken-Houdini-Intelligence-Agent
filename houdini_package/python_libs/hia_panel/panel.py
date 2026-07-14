"""PySide6 Houdini Python Panel for the P1-V protocol slice."""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .bridge_client import BridgeClient
from .network_response import format_bridge_error
from .turn_state import PanelTurnState, TurnStateToken


_PASSIVE_STATUS_NOTIFICATIONS = frozenset(
    {
        "remoteControl/status/changed",
        "mcpServer/startupStatus/updated",
        "account/rateLimits/updated",
    }
)
_TURN_START_CONTEXT_PREFIX = "turn_start:"
_INTERRUPT_CONTEXT_PREFIX = "interrupt:"
_SESSION_RECONCILE_CONTEXT_PREFIX = "session_reconcile:"
_MODELS_CONTEXT = "models"
_CODEX_DEFAULT_LABEL = "Codex 默认"


class HoudiniIntelligencePanel(QtWidgets.QWidget):
    """Conversation UI only; this class never calls hou or modifies a scene."""

    def __init__(self, pane_tab: Any = None, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._pane_tab = pane_tab
        self._event_sequence = 0
        self._polling_enabled = False
        self._connected = False
        self._authenticated = False
        self._selected_thread_id: str | None = None
        self._session_action_pending = False
        self._turn_start_request_pending = False
        self._interrupt_pending = False
        self._turn_state = PanelTurnState()
        self._turn_start_tokens: dict[str, TurnStateToken] = {}
        self._active_turn_start_context: str | None = None
        self._interrupt_tokens: dict[str, TurnStateToken] = {}
        self._active_interrupt_context: str | None = None
        self._reconciliation_tokens: dict[str, TurnStateToken] = {}
        self._models_requested = False
        self._pending_approvals: deque[dict[str, Any]] = deque()
        self._current_approval: dict[str, Any] | None = None
        self._build_ui()

        base_url = os.environ.get("HIA_BRIDGE_URL", "")
        token = os.environ.get("HIA_BRIDGE_TOKEN", "")
        if not base_url or not token:
            self._set_connection("未连接：启动器未提供 Bridge 会话", False)
            self._refresh_controls()
            self._client = None
            return

        self._client = BridgeClient(base_url, token, self)
        self._client.healthReceived.connect(self._on_health)
        self._client.sessionReceived.connect(self._on_session)
        self._client.eventsReceived.connect(self._on_events)
        self._client.actionCompleted.connect(self._on_action_completed)
        self._client.requestFailed.connect(self._on_request_failed)
        self._client.get_health()

    def _build_ui(self) -> None:
        self.setObjectName("houdiniIntelligencePanel")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        status_row = QtWidgets.QHBoxLayout()
        self.connection_label = QtWidgets.QLabel("Codex：正在检查…")
        self.auth_label = QtWidgets.QLabel("认证：未知")
        self.thread_status_label = QtWidgets.QLabel("Thread：未选择")
        self.turn_status_label = QtWidgets.QLabel("Turn：空闲")
        status_row.addWidget(self.connection_label)
        status_row.addWidget(self.auth_label)
        status_row.addStretch(1)
        status_row.addWidget(self.thread_status_label)
        status_row.addWidget(self.turn_status_label)
        root.addLayout(status_row)

        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("模型"))
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(QtWidgets.QLabel("推理强度"))
        self.effort_combo = QtWidgets.QComboBox()
        self.effort_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        model_row.addWidget(self.effort_combo)
        root.addLayout(model_row)

        thread_row = QtWidgets.QHBoxLayout()
        self.thread_id_edit = QtWidgets.QLineEdit()
        self.thread_id_edit.setPlaceholderText("Codex Thread ID（恢复时填写）")
        self.new_thread_button = QtWidgets.QPushButton("新建 Thread")
        self.resume_thread_button = QtWidgets.QPushButton("恢复 Thread")
        thread_row.addWidget(self.thread_id_edit, 1)
        thread_row.addWidget(self.new_thread_button)
        thread_row.addWidget(self.resume_thread_button)
        root.addLayout(thread_row)

        self.conversation = QtWidgets.QPlainTextEdit()
        self.conversation.setReadOnly(True)
        self.conversation.setPlaceholderText("Codex 回复、稳定计划和生命周期事件显示在这里。")
        self.conversation.setFont(
            QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        )
        root.addWidget(self.conversation, 1)

        self.approval_group = QtWidgets.QGroupBox("审批请求")
        approval_layout = QtWidgets.QVBoxLayout(self.approval_group)
        self.approval_text = QtWidgets.QPlainTextEdit()
        self.approval_text.setReadOnly(True)
        self.approval_text.setMaximumHeight(150)
        approval_layout.addWidget(self.approval_text)
        approval_buttons = QtWidgets.QHBoxLayout()
        self.allow_button = QtWidgets.QPushButton("允许")
        self.deny_button = QtWidgets.QPushButton("拒绝")
        approval_buttons.addStretch(1)
        approval_buttons.addWidget(self.allow_button)
        approval_buttons.addWidget(self.deny_button)
        approval_layout.addLayout(approval_buttons)
        self.approval_group.setVisible(False)
        root.addWidget(self.approval_group)

        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText("输入自然语言请求。本阶段只连接 Codex，不操作 Houdini 场景。")
        self.input_edit.setMaximumHeight(110)
        self.input_edit.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_InputMethodEnabled,
            True,
        )
        self.input_edit.setInputMethodHints(QtCore.Qt.InputMethodHint.ImhNone)
        self.input_edit.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        root.addWidget(self.input_edit)

        action_row = QtWidgets.QHBoxLayout()
        self.send_button = QtWidgets.QPushButton("发送")
        self.stop_button = QtWidgets.QPushButton("停止")
        action_row.addStretch(1)
        action_row.addWidget(self.send_button)
        action_row.addWidget(self.stop_button)
        root.addLayout(action_row)

        self.new_thread_button.clicked.connect(self._new_thread)
        self.resume_thread_button.clicked.connect(self._resume_thread)
        self.send_button.clicked.connect(self._send)
        self.stop_button.clicked.connect(self._stop)
        self.allow_button.clicked.connect(lambda: self._resolve_approval("allow"))
        self.deny_button.clicked.connect(lambda: self._resolve_approval("deny"))
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._refresh_controls()

    def _set_connection(self, text: str, connected: bool) -> None:
        self._connected = connected
        self.connection_label.setText(f"Codex：{text}")
        self.connection_label.setStyleSheet(
            "color: #7ad97a;" if connected else "color: #e6a65c;"
        )

    def _refresh_controls(self) -> None:
        controls = self._turn_state.derive_controls(
            connected=self._connected,
            authenticated=self._authenticated,
            selected_thread_id=self._selected_thread_id,
        )
        session_enabled = not (
            self._session_action_pending
            or self._turn_start_request_pending
            or bool(self._reconciliation_tokens)
        )
        self.new_thread_button.setEnabled(controls.new_thread and session_enabled)
        self.resume_thread_button.setEnabled(controls.resume_thread and session_enabled)
        self.send_button.setEnabled(controls.send and session_enabled)
        self.stop_button.setEnabled(controls.stop and not self._interrupt_pending)
        self.thread_id_edit.setEnabled(not self._turn_state.busy and session_enabled)
        selection_enabled = (
            self._connected
            and not self._turn_state.busy
            and session_enabled
        )
        self.model_combo.setEnabled(selection_enabled)
        self.effort_combo.setEnabled(selection_enabled)

    @QtCore.Slot(dict)
    def _on_health(self, payload: dict[str, Any]) -> None:
        self._set_connection("已连接（stdio JSONL）", True)
        self._apply_session(
            payload.get("session", {}),
            token=self._turn_state.capture_token(),
            allow_followup=True,
        )
        self._polling_enabled = True
        self._schedule_poll(0)
        if not self._models_requested and self._client is not None:
            self._models_requested = True
            self._client.get_models()

    @QtCore.Slot(dict)
    def _on_session(self, payload: dict[str, Any]) -> None:
        self._apply_session(
            payload.get("session", {}),
            token=self._turn_state.capture_token(),
            allow_followup=True,
        )

    def _apply_session(
        self,
        session: dict[str, Any],
        *,
        token: TurnStateToken,
        allow_followup: bool,
    ) -> bool:
        """Apply a correlated snapshot, rejecting stale Turn state atomically."""

        if not self._turn_state.token_is_current(token):
            return False

        thread_id = session.get("thread_id")
        turn_id = session.get("turn_id")
        turn_status = session.get("turn_status")
        turn_active = session.get("turn_active")
        state_applied = True
        if isinstance(turn_active, bool):
            if isinstance(thread_id, str) and thread_id:
                state_applied = self._turn_state.reconcile_snapshot(
                    token,
                    thread_id,
                    turn_id if isinstance(turn_id, str) else None,
                    turn_status if isinstance(turn_status, str) else None,
                    turn_active=turn_active,
                )
            elif turn_active or self._turn_state.busy:
                state_applied = False

        self._connected = bool(session.get("connected"))
        authentication = session.get("authentication")
        if authentication == "authenticated":
            self._authenticated = True
            account = session.get("account") or {}
            account_data = account.get("account") or {}
            account_type = account_data.get("type", "已认证")
            self.auth_label.setText(f"认证：{account_type}")
        elif authentication == "login_required":
            self._authenticated = False
            self.auth_label.setText("认证：需要登录")
        elif authentication == "account_error":
            self._authenticated = False
            self.auth_label.setText("认证：检查失败")
        else:
            self._authenticated = False
            self.auth_label.setText("认证：不可用")

        if state_applied and isinstance(thread_id, str) and thread_id:
            self._selected_thread_id = thread_id
            self.thread_id_edit.setText(thread_id)
            self.thread_status_label.setText(f"Thread：{thread_id}")
        elif state_applied and not self._turn_state.busy:
            self._selected_thread_id = None
            self.thread_status_label.setText("Thread：未选择")

        if state_applied and isinstance(turn_active, bool):
            self.turn_status_label.setText(
                self._turn_status_text(
                    turn_status if isinstance(turn_status, str) else None,
                    active=turn_active,
                )
            )
        elif not state_applied and allow_followup:
            self._request_session_reconciliation("session_conflict")
        self._refresh_controls()
        return state_applied

    def _new_thread(self) -> None:
        if (
            self._client is not None
            and not self._turn_state.busy
            and not self._session_action_pending
            and not self._turn_start_request_pending
            and not self._reconciliation_tokens
        ):
            self._session_action_pending = True
            self._refresh_controls()
            self._client.start_thread(model=self._selected_model_id())

    def _resume_thread(self) -> None:
        thread_id = self.thread_id_edit.text().strip()
        if not thread_id:
            self._append_system("请先输入 Thread ID。")
            return
        if (
            self._client is not None
            and not self._turn_state.busy
            and not self._session_action_pending
            and not self._turn_start_request_pending
            and not self._reconciliation_tokens
        ):
            self._session_action_pending = True
            self._refresh_controls()
            self._client.resume_thread(thread_id)

    def _send(self) -> None:
        text = self.input_edit.toPlainText()
        if not text.strip():
            return
        thread_id = self._selected_thread_id
        if not isinstance(thread_id, str) or not thread_id:
            self._append_system("请先新建或恢复 Thread。")
            return
        if (
            self._session_action_pending
            or self._turn_start_request_pending
            or self._reconciliation_tokens
        ):
            return
        if self._client is None or not self._turn_state.begin_start(thread_id):
            self._append_system("当前 Turn 尚未完成，不能再次发送。")
            self._refresh_controls()
            return
        self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.conversation.insertPlainText(f"\nYou: {text}\nCodex: ")
        self.input_edit.clear()
        self.turn_status_label.setText("Turn：正在创建")
        token = self._turn_state.capture_token()
        context = (
            f"{_TURN_START_CONTEXT_PREFIX}{token.generation}:{token.revision}"
        )
        self._turn_start_tokens[context] = token
        self._active_turn_start_context = context
        self._turn_start_request_pending = True
        self._refresh_controls()
        self._client.start_turn(
            text,
            model=self._selected_model_id(),
            effort=self._selected_effort(),
            context=context,
        )

    def _stop(self) -> None:
        controls = self._turn_state.derive_controls(
            connected=self._connected,
            authenticated=self._authenticated,
            selected_thread_id=self._selected_thread_id,
        )
        if self._client is not None and controls.stop and not self._interrupt_pending:
            token = self._turn_state.capture_token()
            context = (
                f"{_INTERRUPT_CONTEXT_PREFIX}{token.generation}:{token.revision}"
            )
            self._interrupt_tokens[context] = token
            self._active_interrupt_context = context
            self._interrupt_pending = True
            self._refresh_controls()
            self._client.interrupt(context=context)

    @QtCore.Slot(str, dict)
    def _on_action_completed(self, context: str, payload: dict[str, Any]) -> None:
        if context == _MODELS_CONTEXT:
            self._apply_models(payload.get("models"))
            self._refresh_controls()
            return

        if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
            token = self._reconciliation_tokens.pop(context, None)
            session = payload.get("session")
            if isinstance(token, TurnStateToken) and isinstance(session, dict):
                applied = self._apply_session(
                    session,
                    token=token,
                    allow_followup=False,
                )
                if applied and session.get("turn_active") is False:
                    self._mark_turn_terminal(
                        session.get("turn_status")
                        if isinstance(session.get("turn_status"), str)
                        else None
                    )
                elif (
                    not applied
                    and self._turn_state.busy
                    and self._turn_state.token_generation_is_current(token)
                ):
                    # A completion can arrive before its delayed turn/start ACK.
                    # The ACK advances the revision, making the already-issued
                    # snapshot stale.  Retry once for this generation using a
                    # fresh token; the state object bounds this reason to one
                    # request and rejects snapshots from a newer Turn.
                    self._request_session_reconciliation(
                        "stale_reconciliation_followup"
                    )
            self._refresh_controls()
            return

        if context in {"session_start", "session_resume"}:
            self._session_action_pending = False
            thread_id = payload.get("thread_id")
            if isinstance(thread_id, str):
                self._selected_thread_id = thread_id
                self.thread_id_edit.setText(thread_id)
                self.thread_status_label.setText(f"Thread：{thread_id}")
                action = "已新建" if context == "session_start" else "已恢复"
                self._append_system(f"{action} Thread：{thread_id}")
        elif context.startswith(_TURN_START_CONTEXT_PREFIX):
            token = self._turn_start_tokens.pop(context, None)
            if context == self._active_turn_start_context:
                self._active_turn_start_context = None
                self._turn_start_request_pending = False
            thread_id = payload.get("thread_id")
            turn_id = payload.get("turn_id")
            state_changed = False
            if (
                isinstance(token, TurnStateToken)
                and isinstance(thread_id, str)
                and isinstance(turn_id, str)
            ):
                if payload.get("turn_active") is False:
                    acknowledged = self._turn_state.acknowledge_start(
                        token,
                        thread_id,
                        turn_id,
                    )
                    state_changed = acknowledged and self._turn_state.observe_completed(
                        thread_id,
                        turn_id,
                    )
                else:
                    state_changed = self._turn_state.acknowledge_start(
                        token,
                        thread_id,
                        turn_id,
                    )
            if state_changed:
                if self._turn_state.busy:
                    self.turn_status_label.setText(f"Turn：{turn_id or '运行中'}")
                else:
                    self._mark_turn_terminal(
                        payload.get("turn_status")
                        if isinstance(payload.get("turn_status"), str)
                        else None
                    )
            else:
                self._request_session_reconciliation("late_turn_start_ack")
        elif context.startswith(_INTERRUPT_CONTEXT_PREFIX):
            token = self._interrupt_tokens.pop(context, None)
            if context == self._active_interrupt_context:
                self._active_interrupt_context = None
                self._interrupt_pending = False
            if (
                isinstance(token, TurnStateToken)
                and self._turn_state.token_generation_is_current(token)
            ):
                self._append_system("已发送停止请求。")
        elif context.startswith("approval_"):
            self._current_approval = None
            self.approval_group.setVisible(False)
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
            self._show_next_approval()
        self._refresh_controls()

    @QtCore.Slot(dict)
    def _on_events(self, payload: dict[str, Any]) -> None:
        for event in payload.get("events", []):
            sequence = event.get("seq")
            try:
                self._render_event(event)
            except Exception as exc:
                self._append_system(
                    f"事件处理失败：{type(exc).__name__}；正在同步 Turn 状态。"
                )
                failure_key = (
                    str(sequence) if isinstance(sequence, int) else "unknown"
                )
                self._request_session_reconciliation(
                    f"event_render_failure_{failure_key}"
                )
            finally:
                # Commit the cursor only after the event was rendered or an
                # authoritative bounded reconciliation was requested.
                if isinstance(sequence, int):
                    self._event_sequence = max(self._event_sequence, sequence)
        if payload.get("gap"):
            self._append_system("事件缓冲出现间隙；正在同步 Turn 状态。")
            self._request_session_reconciliation("event_gap")
        self._schedule_poll(0)

    def _render_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "codex_notification":
            method = event.get("method")
            raw_params = event.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
                    self.conversation.insertPlainText(delta)
            elif method == "turn/plan/updated":
                steps = params.get("plan") or []
                rendered = " | ".join(
                    f"{step.get('status', '?')}: {step.get('step', '')}"
                    for step in steps
                    if isinstance(step, dict)
                )
                self._append_system(f"计划：{rendered}")
            elif method == "turn/started":
                raw_turn = params.get("turn")
                turn = raw_turn if isinstance(raw_turn, dict) else {}
                thread_id = params.get("threadId")
                turn_id = turn.get("id")
                if isinstance(thread_id, str) and isinstance(turn_id, str):
                    if self._turn_state.observe_started(thread_id, turn_id):
                        self.turn_status_label.setText(f"Turn：{turn_id}")
                        self._refresh_controls()
                    else:
                        self._request_session_reconciliation(
                            "unmatched_turn_started"
                        )
                else:
                    self._request_session_reconciliation("unmatched_turn_started")
            elif method == "turn/completed":
                raw_turn = params.get("turn")
                turn = raw_turn if isinstance(raw_turn, dict) else {}
                thread_id = params.get("threadId")
                turn_id = turn.get("id")
                if isinstance(thread_id, str) and isinstance(turn_id, str):
                    if self._turn_state.observe_completed(thread_id, turn_id):
                        self._mark_turn_terminal(
                            turn.get("status")
                            if isinstance(turn.get("status"), str)
                            else None
                        )
                        self._refresh_controls()
                    else:
                        self._request_session_reconciliation(
                            "unmatched_turn_completed"
                        )
                else:
                    self._request_session_reconciliation(
                        "unmatched_turn_completed"
                    )
            elif method in _PASSIVE_STATUS_NOTIFICATIONS:
                # P1 receive-only observation.  These notifications never
                # authorize a request, remote control, or a new MCP tool.
                pass
            elif method in {"error", "warning", "guardianWarning", "configWarning"}:
                self._append_system(f"{method}：{json.dumps(params, ensure_ascii=False)}")
        elif event_type == "server_request":
            self._pending_approvals.append(event)
            self._show_next_approval()
        elif event_type == "protocol_warning":
            method = event.get("method")
            method_text = f" method={method}" if isinstance(method, str) else ""
            self._append_system(
                f"协议警告：{event.get('code', '')}{method_text} "
                f"{event.get('message', '')}"
            )
        elif event_type == "process_exit":
            self._set_connection("app-server 已退出", False)
            self._refresh_controls()

    def _show_next_approval(self) -> None:
        if self._current_approval is not None or not self._pending_approvals:
            return
        self._current_approval = self._pending_approvals.popleft()
        display = {
            "method": self._current_approval.get("method"),
            "params": self._current_approval.get("params", {}),
        }
        text = json.dumps(display, ensure_ascii=False, indent=2)
        self.approval_text.setPlainText(text[:12000])
        self.approval_group.setVisible(True)

    def _resolve_approval(self, decision: str) -> None:
        if self._current_approval is None or self._client is None:
            return
        self.allow_button.setEnabled(False)
        self.deny_button.setEnabled(False)
        self._client.resolve_approval(
            self._current_approval.get("request_id"),
            decision,
        )

    @QtCore.Slot(str, dict)
    def _on_request_failed(self, context: str, payload: dict[str, Any]) -> None:
        if context == _MODELS_CONTEXT:
            self._apply_models([])
            self._append_system("模型列表暂不可用，继续使用 Codex 默认。")
            self._refresh_controls()
            return

        error = payload.get("structured_error") or {}
        details = error.get("details") if isinstance(error, dict) else {}
        details = details if isinstance(details, dict) else {}
        error_code = error.get("code") if isinstance(error, dict) else None

        if context.startswith(_INTERRUPT_CONTEXT_PREFIX):
            token = self._interrupt_tokens.pop(context, None)
            if context == self._active_interrupt_context:
                self._active_interrupt_context = None
                self._interrupt_pending = False
            if error_code == "NO_ACTIVE_TURN" and details.get("turn_active") is False:
                applied = (
                    isinstance(token, TurnStateToken)
                    and self._turn_state.reconcile_no_active_error(token, details)
                )
                if applied or not self._turn_state.busy:
                    self._mark_turn_terminal(
                        details.get("turn_status")
                        if isinstance(details.get("turn_status"), str)
                        else None
                    )
                    self._append_system("Turn 已完成（已与 Bridge 同步）。")
                else:
                    self._request_session_reconciliation(
                        "stale_no_active_turn_response"
                    )
                    self._append_system("停止响应属于较早状态；正在同步当前 Turn。")
                self.allow_button.setEnabled(True)
                self.deny_button.setEnabled(True)
                self._refresh_controls()
                return

        if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
            self._reconciliation_tokens.pop(context, None)

        self._append_system(f"{context} 失败：{format_bridge_error(payload)}")
        if context in {"health", "session"}:
            self._set_connection("连接失败", False)
        if context == "events":
            self._schedule_poll(1500)
        elif context in {"session_start", "session_resume"}:
            self._session_action_pending = False
        elif context.startswith(_TURN_START_CONTEXT_PREFIX):
            token = self._turn_start_tokens.pop(context, None)
            if context == self._active_turn_start_context:
                self._active_turn_start_context = None
                self._turn_start_request_pending = False
            active_turn = details.get("turn_active") is True
            failure_thread_id = details.get("thread_id")
            failure_turn_id = details.get("turn_id")
            state_reconciled = False
            if (
                active_turn
                and isinstance(token, TurnStateToken)
                and isinstance(failure_thread_id, str)
                and isinstance(failure_turn_id, str)
            ):
                state_reconciled = self._turn_state.acknowledge_start(
                    token,
                    failure_thread_id,
                    failure_turn_id,
                )
            elif details.get("turn_created") is False and not active_turn:
                thread_id = self._selected_thread_id or self._turn_state.thread_id
                if isinstance(token, TurnStateToken) and isinstance(thread_id, str):
                    state_reconciled = self._turn_state.reconcile_snapshot(
                        token,
                        thread_id,
                        failure_turn_id
                        if isinstance(failure_turn_id, str)
                        else None,
                        details.get("turn_status")
                        if isinstance(details.get("turn_status"), str)
                        else None,
                        turn_active=False,
                    )
                    if state_reconciled and not self._turn_state.busy:
                        self.turn_status_label.setText("Turn：未创建")
            else:
                thread_id = self._selected_thread_id or self._turn_state.thread_id
                if (
                    isinstance(token, TurnStateToken)
                    and self._turn_state.token_generation_is_current(token)
                    and isinstance(thread_id, str)
                ):
                    if self._turn_state.mark_start_uncertain(thread_id):
                        self.turn_status_label.setText("Turn：状态待确认")
            if not state_reconciled:
                self._request_session_reconciliation("turn_start_failure")
        elif context.startswith(_INTERRUPT_CONTEXT_PREFIX):
            # Non-authoritative interrupt failures remain ordinary failures.
            self._interrupt_tokens.pop(context, None)
            if context == self._active_interrupt_context:
                self._active_interrupt_context = None
                self._interrupt_pending = False
        self.allow_button.setEnabled(True)
        self.deny_button.setEnabled(True)
        self._refresh_controls()

    def _request_session_reconciliation(self, reason: str) -> bool:
        """Issue at most one correlated session GET for the current generation."""

        if self._client is None or self._reconciliation_tokens:
            return False
        token = self._turn_state.claim_reconciliation(reason)
        if token is None:
            return False
        context = (
            f"{_SESSION_RECONCILE_CONTEXT_PREFIX}"
            f"{token.generation}:{token.revision}:{reason}"
        )
        self._reconciliation_tokens[context] = token
        self._refresh_controls()
        self._client.get_session(context=context)
        return True

    @QtCore.Slot(int)
    def _on_model_changed(self, _index: int = -1) -> None:
        self._update_reasoning_efforts()

    def _apply_models(self, raw_models: Any) -> None:
        """Replace the selector with the bounded, Bridge-filtered model catalog."""

        previous_model = self._selected_model_id()
        models = raw_models if isinstance(raw_models, list) else []
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        selected_index = 0
        seen: set[str] = set()
        for raw_model in models:
            if not isinstance(raw_model, dict):
                continue
            model_id = raw_model.get("model")
            if not isinstance(model_id, str) or not model_id or model_id in seen:
                continue
            seen.add(model_id)
            record = dict(raw_model)
            display_name = record.get("displayName")
            label = display_name if isinstance(display_name, str) and display_name else model_id
            self.model_combo.addItem(label, record)
            index = self.model_combo.count() - 1
            if model_id == previous_model:
                selected_index = index
            elif previous_model is None and record.get("isDefault") is True:
                selected_index = index
        self.model_combo.setCurrentIndex(selected_index)
        self.model_combo.blockSignals(False)
        self._update_reasoning_efforts()

    def _selected_model_record(self) -> dict[str, Any] | None:
        record = self.model_combo.currentData()
        return record if isinstance(record, dict) else None

    def _selected_model_id(self) -> str | None:
        record = self._selected_model_record()
        model_id = record.get("model") if record is not None else None
        return model_id if isinstance(model_id, str) and model_id else None

    def _selected_effort(self) -> str | None:
        effort = self.effort_combo.currentData()
        return effort if isinstance(effort, str) and effort else None

    def _update_reasoning_efforts(self) -> None:
        previous_effort = self._selected_effort()
        record = self._selected_model_record()
        raw_options = (
            record.get("supportedReasoningEfforts", [])
            if record is not None
            else []
        )
        default_effort = (
            record.get("defaultReasoningEffort")
            if record is not None
            else None
        )
        options = raw_options if isinstance(raw_options, list) else []
        self.effort_combo.blockSignals(True)
        self.effort_combo.clear()
        self.effort_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        previous_index: int | None = None
        default_index: int | None = None
        seen: set[str] = set()
        for raw_option in options:
            if isinstance(raw_option, dict):
                effort = raw_option.get("reasoningEffort")
            else:
                effort = raw_option
            if not isinstance(effort, str) or not effort or effort in seen:
                continue
            seen.add(effort)
            self.effort_combo.addItem(effort, effort)
            index = self.effort_combo.count() - 1
            if effort == previous_effort:
                previous_index = index
            if effort == default_effort:
                default_index = index
        selected_index = previous_index or default_index or 0
        self.effort_combo.setCurrentIndex(selected_index)
        self.effort_combo.blockSignals(False)

    def _mark_turn_terminal(self, status: str | None) -> None:
        """Clear request-only UI locks after an authoritative terminal state."""

        self._turn_start_request_pending = False
        self._active_turn_start_context = None
        self._interrupt_pending = False
        self._active_interrupt_context = None
        self.turn_status_label.setText(
            self._turn_status_text(status or "completed", active=False)
        )

    @staticmethod
    def _turn_status_text(status: str | None, *, active: bool) -> str:
        if active:
            active_labels = {
                "starting": "Turn：正在创建",
                "inProgress": "Turn：运行中",
                "startUnknown": "Turn：状态待确认",
            }
            return active_labels.get(status, f"Turn：{status or '运行中'}")
        terminal_labels = {
            "completed": "Turn：已完成",
            "interrupted": "Turn：已停止",
            "failed": "Turn：失败",
        }
        return terminal_labels.get(status, "Turn：空闲")

    def _schedule_poll(self, delay_ms: int) -> None:
        if not self._polling_enabled or self._client is None:
            return
        QtCore.QTimer.singleShot(
            delay_ms,
            lambda: self._client.poll_events(self._event_sequence),
        )

    def _append_system(self, text: str) -> None:
        self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.conversation.insertPlainText(f"\n[System] {text}\n")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._polling_enabled = False
        if self._client is not None:
            self._client.shutdown()
        super().closeEvent(event)
