"""PySide6 Houdini Python Panel for the P1-V protocol slice."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import PySide6
from PySide6 import QtCore, QtGui, QtWidgets

from .approval_card import format_approval_card
from .attachment_store import AttachmentStore
from .bridge_client import BridgeClient
from .houdini_read_adapter import HoudiniReadAdapter, HoudiniReadAdapterError
from .network_response import format_bridge_error
from .runtime_diagnostics import RuntimeDiagnosticWriter
from .turn_state import PanelTurnState, TurnPhase, TurnStateToken


_PASSIVE_STATUS_NOTIFICATIONS = frozenset(
    {
        "remoteControl/status/changed",
        "mcpServer/startupStatus/updated",
        "account/rateLimits/updated",
        "skills/changed",
    }
)
_TURN_START_CONTEXT_PREFIX = "turn_start:"
_TURN_STEER_CONTEXT_PREFIX = "turn_steer:"
_INTERRUPT_CONTEXT_PREFIX = "interrupt:"
_SESSION_RECONCILE_CONTEXT_PREFIX = "session_reconcile:"
_MODELS_CONTEXT = "models"
_THREADS_CONTEXT = "threads"
_THREAD_READ_CONTEXT_PREFIX = "thread_read:"
_CRASH_RECOVERY_READ_CONTEXT = "thread_read:crash_recovery"
_CRASH_RECOVERY_RECHECK_CONTEXT = "thread_read:crash_recovery_recheck"
_THREAD_RENAME_CONTEXT_PREFIX = "thread_rename:"
_GOAL_GET_CONTEXT = "goal_get"
_GOAL_SET_CONTEXT = "goal_set"
_GOAL_CLEAR_CONTEXT = "goal_clear"
_FOCUS_SET_CONTEXT = "focus_set"
_HOUDINI_STATUS_CONTEXT = "houdini_status"
_CODEX_DEFAULT_LABEL = "Codex 默认"
_CODEX_STANDARD_TIER_LABEL = "标准"
_SCENE_CAPABILITY_CONTEXT = "scene_capabilities"
_SCENE_WORK_CONTEXT = "scene_work"
_SCENE_RESULT_CONTEXT_PREFIX = "scene_result:"
_SCENE_HEARTBEAT_MS = 1_000
_SCENE_IDLE_POLL_MS = 100
_RECONNECT_DELAYS_MS = (500, 1_000, 2_000, 4_000, 8_000)
_RECONNECTABLE_ERROR_CODES = frozenset({"NETWORK_ERROR", "NETWORK_TIMEOUT"})
_SESSION_WAIT_TIMEOUT_CODES = frozenset(
    {"NETWORK_TIMEOUT", "CODEX_REQUEST_TIMEOUT"}
)
_MAX_TURN_IMAGES = 16
_LONG_THREAD_WARNING = (
    "当前对话较长，早期细节可能逐渐减少。开始不同任务时建议新建 Thread。"
)
_COMPACTION_NOTICE = "Codex 已自动整理较早的对话内容。"
_DEFAULT_MCP_BACKEND = "hia_v2"
_GOAL_OBJECTIVE_MAX_LENGTH = 4_000
_GOAL_CONTINUE_INSTRUCTION = (
    "继续推进当前 Goal；先核对上一轮真实结果，再执行下一项未完成工作。"
)
_GOAL_STATUS_LABELS = {
    "active": "正在跟进",
    "complete": "已完成",
    "blocked": "等待你处理",
    "paused": "暂时受阻",
    "usageLimited": "暂时受阻",
    "budgetLimited": "暂时受阻",
}
_GOAL_RUNNING_NO_TEXT = "当前跟进：Codex 正在推进 Goal（尚无文字输出）"
_GOAL_RUNNING_WITH_TEXT = "当前跟进：Codex 正在推进 Goal"
_TEAM_RECORD_LIMIT = 32
_TEAM_EVENT_LIMIT = 24
_TEAM_TEXT_LIMIT = 65_536
_CRASH_RECOVERY_THREAD_ENV = "HIA_CRASH_RECOVERY_THREAD_ID"
_CRASH_RECOVERY_GOAL_BINDING_ENV = "HIA_CRASH_RECOVERY_GOAL_BINDING"
_CRASH_RECOVERY_PROMPT_ENV = "HIA_CRASH_RECOVERY_PROMPT_ID"
_CRASH_RECOVERY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_CRASH_RECOVERY_PROMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CRASH_RECOVERY_GOAL_BINDING = re.compile(r"[0-9a-f]{64}\Z")
_MCP_BACKEND_PRESENTATION = {
    "hia_v2": ("HIA MCP V2", "HIA MCP V2 当前 Houdini 会话状态"),
    "fxhoudini": (
        "FXHoudiniMCP",
        "FXHoudiniMCP 1.3.0 兼容回退当前 Houdini 会话状态",
    ),
}


class HoudiniIntelligencePanel(QtWidgets.QWidget):
    """Conversation UI with current-session Houdini and MCP status."""

    @staticmethod
    def _take_crash_recovery_marker() -> dict[str, str] | None:
        values = {
            "thread_id": os.environ.pop(_CRASH_RECOVERY_THREAD_ENV, ""),
            "goal_binding": os.environ.pop(
                _CRASH_RECOVERY_GOAL_BINDING_ENV, ""
            ),
            "prompt_id": os.environ.pop(_CRASH_RECOVERY_PROMPT_ENV, ""),
        }
        if (
            _CRASH_RECOVERY_ID.fullmatch(values["thread_id"]) is None
            or _CRASH_RECOVERY_GOAL_BINDING.fullmatch(values["goal_binding"])
            is None
            or _CRASH_RECOVERY_PROMPT_ID.fullmatch(values["prompt_id"])
            is None
        ):
            return None
        return values

    def __init__(
        self,
        pane_tab: Any = None,
        parent: QtWidgets.QWidget | None = None,
        *,
        hou_module: Any | None = None,
    ):
        super().__init__(parent)
        self._pane_tab = pane_tab
        self._hou_module = hou_module
        self._event_sequence = 0
        self._polling_enabled = False
        self._connected = False
        self._mcp_backend: str | None = self._initial_mcp_backend(
            os.environ.get("HIA_MCP_BACKEND")
        )
        self._authenticated = False
        self._selected_thread_id: str | None = None
        self._crash_recovery_marker = self._take_crash_recovery_marker()
        self._crash_recovery_health_session: dict[str, Any] | None = None
        self._crash_recovery_goal_payload: dict[str, Any] | None = None
        self._crash_recovery_thread_payload: dict[str, Any] | None = None
        self._crash_recovery_observation: dict[str, Any] | None = None
        self._session_action_pending = False
        self._turn_start_request_pending = False
        self._interrupt_pending = False
        self._turn_state = PanelTurnState()
        self._turn_start_tokens: dict[str, TurnStateToken] = {}
        self._pending_turn_drafts: dict[str, dict[str, Any]] = {}
        self._active_turn_start_context: str | None = None
        self._turn_steer_request_pending = False
        self._turn_steer_tokens: dict[str, TurnStateToken] = {}
        self._pending_steer_drafts: dict[str, dict[str, Any]] = {}
        self._active_turn_steer_context: str | None = None
        self._stream_thread_id: str | None = None
        self._stream_turn_id: str | None = None
        self._interrupt_tokens: dict[str, TurnStateToken] = {}
        self._active_interrupt_context: str | None = None
        self._stopping_turn_token: TurnStateToken | None = None
        self._stop_recovery_state: str | None = None
        self._stopped_source_turn: tuple[str, str] | None = None
        self._reconciliation_tokens: dict[str, TurnStateToken] = {}
        self._models_requested = False
        self._models_resolved = False
        self._threads_requested = False
        self._thread_history: list[dict[str, Any]] = []
        self._goal_action_context: str | None = None
        self._current_goal: dict[str, Any] | None = None
        self._goal_turn_id: str | None = None
        self._goal_turn_has_text = False
        self._focus_mode = False
        self._goal_continuation_paused = False
        self._goal_continuation_boundary: tuple[str, str] | None = None
        self._goal_auto_turn_token: TurnStateToken | None = None
        self._goal_auto_turn_has_progress = False
        self._goal_continue_after_open_thread_id: str | None = None
        self._team_records: dict[str, dict[str, Any]] = {}
        self._turn_performance_token: TurnStateToken | None = None
        self._turn_performance_marks: dict[str, float] = {}
        self._reconnect_attempt = 0
        self._reconnecting = False
        self._reconnect_exhausted_notice_shown = False
        self._app_server_exit_notice_shown = False
        self._pending_approvals: deque[dict[str, Any]] = deque()
        self._current_approval: dict[str, Any] | None = None
        self._current_approval_offers_persistent_rule = False
        self._houdini_adapter: HoudiniReadAdapter | None = None
        self._houdini_polling_enabled = False
        self._local_houdini_polling_enabled = False
        self._houdini_status_pending = False
        self._houdini_status_turn_token: TurnStateToken | None = None
        self._scene_capability_pending = False
        self._scene_work_pending = False
        self._scene_attestation_digest: str | None = None
        self._scene_catalog_digest: str | None = None
        self._last_houdini_report: dict[str, Any] | None = None
        self._attested_houdini_report_identity: str | None = None
        self._pending_houdini_report_identity: str | None = None
        self._selected_node_paths: tuple[str, ...] = ()
        self._attachment_store = AttachmentStore()
        self._attachment_dialog: Any | None = None
        self._diagnostic_turn_key: str | None = None
        self._diagnostic_draft_key: str | None = None
        self._diagnostic_snapshot: dict[str, Any] = {}
        self._diagnostic_tool_states: dict[str, dict[str, Any]] = {}
        self._diagnostic_event_errors: list[dict[str, Any]] = []
        self._last_report_path: str | None = None
        self._diagnostic_writer_error: str | None = None
        try:
            self._diagnostic_writer: RuntimeDiagnosticWriter | None = (
                RuntimeDiagnosticWriter()
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._diagnostic_writer = None
            self._diagnostic_writer_error = f"{type(exc).__name__}: {exc}"
        self._scene_executor_token = os.environ.get("HIA_SCENE_EXECUTOR_TOKEN", "")

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._poll_once)
        self._houdini_heartbeat_timer = QtCore.QTimer(self)
        self._houdini_heartbeat_timer.setSingleShot(True)
        self._houdini_heartbeat_timer.timeout.connect(self._houdini_heartbeat)
        self._scene_work_timer = QtCore.QTimer(self)
        self._scene_work_timer.setSingleShot(True)
        self._scene_work_timer.timeout.connect(self._poll_scene_work)
        self._reconnect_timer = QtCore.QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_bridge_reconnect)
        self._build_ui()
        self._initialize_houdini_read_adapter(hou_module)
        self._refresh_selection_status()
        self._update_houdini_status(self._last_houdini_report or {})
        self._start_local_houdini_loop()

        base_url = os.environ.get("HIA_BRIDGE_URL", "")
        token = os.environ.get("HIA_BRIDGE_TOKEN", "")
        if not base_url or not token:
            self._set_connection("未连接：启动器未提供 Bridge 会话", False)
            self._refresh_controls()
            self._client = None
            return

        self._client = BridgeClient(
            base_url,
            token,
            self,
            scene_executor_token=self._scene_executor_token or None,
        )
        self._client.healthReceived.connect(self._on_health)
        self._client.sessionReceived.connect(self._on_session)
        self._client.eventsReceived.connect(self._on_events)
        self._client.actionCompleted.connect(self._on_action_completed)
        self._client.requestFailed.connect(self._on_request_failed)
        self._client.get_health()

    def _build_ui(self) -> None:
        from .composer import AttachmentStrip, ExpandableTextEdit
        from .conversation_view import ConversationView

        self.setObjectName("houdiniIntelligencePanel")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(12)
        self.connection_label = QtWidgets.QLabel("● Codex：正在检查…")
        self.connection_label.setToolTip("Codex app-server 与 Bridge 的连接状态")
        self.houdini_connection_label = QtWidgets.QLabel("● Houdini：未连接")
        self.houdini_connection_label.setToolTip("当前 Houdini 会话是否可访问")
        self.houdini_mcp_label = QtWidgets.QLabel()
        self._set_mcp_status(self._mcp_backend, False)
        hython_exe = os.environ.get("HIA_HYTHON_EXE", "")
        hython_available = bool(hython_exe and os.path.isfile(hython_exe))
        self.native_hython_label = QtWidgets.QLabel(
            "● Native Hython：可用"
            if hython_available
            else "● Native Hython：不可用"
        )
        self.native_hython_label.setToolTip("仅用于明确要求的离线或批处理任务")
        self.native_hython_label.setStyleSheet(
            "color: #67c587;" if hython_available else "color: #9aa0a8;"
        )
        self.houdini_scene_label = QtWidgets.QLabel(
            "场景版本：不可用  ·  未保存：不可用"
        )
        self.houdini_scene_label.setToolTip(
            "场景版本是当前 Houdini 会话内检测到的场景变化计数。\n"
            "未保存表示当前 HIP 是否有尚未保存的修改。"
        )
        status_row.addWidget(self.connection_label)
        status_row.addWidget(self.houdini_connection_label)
        status_row.addWidget(self.houdini_mcp_label)
        status_row.addWidget(self.native_hython_label)
        status_row.addStretch(1)
        status_row.addWidget(self.houdini_scene_label)
        root.addLayout(status_row)

        session_row = QtWidgets.QHBoxLayout()
        session_row.setSpacing(8)
        self.auth_label = QtWidgets.QLabel("认证：未知")
        self.thread_status_label = QtWidgets.QLabel("Thread：未选择")
        self.turn_status_label = QtWidgets.QLabel("Turn：空闲")
        self.thread_status_label.setToolTip("当前未选择 Codex Thread")
        self.turn_status_label.setToolTip("当前 Codex Turn 状态")
        for label in (self.thread_status_label, self.turn_status_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
        session_row.addWidget(self.auth_label)
        session_row.addWidget(self.thread_status_label)
        session_row.addWidget(self.turn_status_label)
        session_row.addStretch(1)
        session_row.addWidget(QtWidgets.QLabel("模型"))
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        self.model_combo.setMaximumWidth(220)
        self.model_combo.setToolTip("当前 Codex 模型")
        session_row.addWidget(self.model_combo)
        session_row.addWidget(QtWidgets.QLabel("推理"))
        self.effort_combo = QtWidgets.QComboBox()
        self.effort_combo.addItem(_CODEX_DEFAULT_LABEL, None)
        self.effort_combo.setMaximumWidth(140)
        self.effort_combo.setToolTip("当前推理强度")
        session_row.addWidget(self.effort_combo)
        self.service_tier_label = QtWidgets.QLabel("速度")
        self.service_tier_combo = QtWidgets.QComboBox()
        self.service_tier_combo.addItem(_CODEX_STANDARD_TIER_LABEL, None)
        self.service_tier_combo.setMaximumWidth(150)
        self.service_tier_combo.setToolTip(
            "速度档位来自当前模型的实时 model/list。"
        )
        self.service_tier_label.setVisible(False)
        self.service_tier_combo.setVisible(False)
        session_row.addWidget(self.service_tier_label)
        session_row.addWidget(self.service_tier_combo)
        root.addLayout(session_row)

        self.main_splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Horizontal, self
        )
        self.main_splitter.setObjectName("mainThreeColumnSplitter")
        self.main_splitter.setChildrenCollapsible(True)
        left_column = QtWidgets.QWidget(self.main_splitter)
        left_column.setMinimumWidth(0)
        left_layout = QtWidgets.QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QtWidgets.QLabel("历史任务"))
        self.history_combo = QtWidgets.QComboBox()
        self.history_combo.addItem("暂无历史会话", None)
        self.history_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.history_combo.setMinimumContentsLength(0)
        self.history_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.history_combo.setToolTip("当前项目最近 20 条未归档 Codex 会话")
        left_layout.addWidget(self.history_combo)
        history_action_row = QtWidgets.QHBoxLayout()
        self.refresh_threads_button = QtWidgets.QPushButton("刷新")
        self.new_thread_button = QtWidgets.QPushButton("新建")
        self.resume_thread_button = QtWidgets.QPushButton("打开")
        history_action_row.addWidget(self.refresh_threads_button)
        history_action_row.addWidget(self.new_thread_button)
        history_action_row.addWidget(self.resume_thread_button)
        left_layout.addLayout(history_action_row)
        self.thread_name_edit = QtWidgets.QLineEdit()
        self.thread_name_edit.setPlaceholderText("会话名称")
        left_layout.addWidget(self.thread_name_edit)
        history_name_row = QtWidgets.QHBoxLayout()
        self.rename_thread_button = QtWidgets.QPushButton("重命名")
        self.copy_thread_id_button = QtWidgets.QPushButton("复制 ID")
        history_name_row.addWidget(self.rename_thread_button)
        history_name_row.addWidget(self.copy_thread_id_button)
        left_layout.addLayout(history_name_row)
        self.thread_id_edit = QtWidgets.QLineEdit()
        self.thread_id_edit.setVisible(False)
        left_layout.addWidget(self.thread_id_edit)
        left_layout.addStretch(1)

        center_column = QtWidgets.QWidget(self.main_splitter)
        center_column.setMinimumWidth(0)
        center_layout = QtWidgets.QVBoxLayout(center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)

        right_column = QtWidgets.QWidget(self.main_splitter)
        right_column.setMinimumWidth(0)
        right_layout = QtWidgets.QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.welcome_group = QtWidgets.QFrame()
        self.welcome_group.setObjectName("welcomeCard")
        self.welcome_group.setStyleSheet(
            "QFrame#welcomeCard { background: #252a31; border: 1px solid #3b424c; "
            "border-radius: 8px; }"
        )
        welcome_layout = QtWidgets.QVBoxLayout(self.welcome_group)
        welcome_title = QtWidgets.QLabel("从自然语言开始操作当前 Houdini 场景")
        welcome_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        welcome_layout.addWidget(welcome_title)
        welcome_help = QtWidgets.QLabel(
            "描述要对当前场景做的修改，也可以包含当前选择或参考图片；"
            "Codex 工作时仍可继续追加要求。"
        )
        welcome_help.setWordWrap(True)
        welcome_help.setStyleSheet("color: #aeb7c2; font-size: 11px;")
        welcome_layout.addWidget(welcome_help)
        prompt_grid = QtWidgets.QGridLayout()
        prompts = (
            "在当前场景中生成模型",
            "分析当前选中的节点",
            "修改当前选中的节点网络",
            "根据参考图片建模",
            "检查当前场景中的错误",
        )
        self._welcome_buttons = []
        for index, prompt in enumerate(prompts):
            button = QtWidgets.QPushButton(prompt)
            button.clicked.connect(
                lambda _checked=False, value=prompt: self._fill_prompt(value)
            )
            prompt_grid.addWidget(button, index // 3, index % 3)
            self._welcome_buttons.append(button)
        welcome_layout.addLayout(prompt_grid)
        center_layout.addWidget(self.welcome_group)

        self.conversation = ConversationView(self)
        self.conversation.setMinimumHeight(0)
        self.conversation.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        if hasattr(self.conversation, "newThreadRequested"):
            self.conversation.newThreadRequested.connect(self._new_thread)
        center_layout.addWidget(self.conversation, 1)

        self.approval_group = QtWidgets.QGroupBox("审批请求")
        approval_layout = QtWidgets.QVBoxLayout(self.approval_group)
        self.approval_text = QtWidgets.QPlainTextEdit()
        self.approval_text.setReadOnly(True)
        self.approval_text.setMaximumHeight(150)
        approval_layout.addWidget(self.approval_text)
        self.approval_details_button = QtWidgets.QPushButton("高级详情")
        self.approval_details_button.setCheckable(True)
        self.approval_details_button.setChecked(False)
        self.approval_details_button.setVisible(False)
        approval_layout.addWidget(self.approval_details_button)
        self.approval_details_text = QtWidgets.QPlainTextEdit()
        self.approval_details_text.setReadOnly(True)
        self.approval_details_text.setMaximumHeight(220)
        self.approval_details_text.setVisible(False)
        approval_layout.addWidget(self.approval_details_text)
        self.persistent_allow_note = QtWidgets.QLabel(
            "持续授权：以后允许协议提供的相同命令规则。"
        )
        self.persistent_allow_note.setVisible(False)
        approval_layout.addWidget(self.persistent_allow_note)
        self.persistent_allow_button = QtWidgets.QPushButton(
            "以后允许相同命令规则"
        )
        self.persistent_allow_button.setVisible(False)
        approval_layout.addWidget(self.persistent_allow_button)
        approval_buttons = QtWidgets.QHBoxLayout()
        self.allow_button = QtWidgets.QPushButton("允许一次")
        self.deny_button = QtWidgets.QPushButton("拒绝")
        approval_buttons.addStretch(1)
        approval_buttons.addWidget(self.allow_button)
        approval_buttons.addWidget(self.deny_button)
        approval_layout.addLayout(approval_buttons)
        self.approval_group.setVisible(False)
        center_layout.addWidget(self.approval_group)

        selection_row = QtWidgets.QHBoxLayout()
        self.selection_label = QtWidgets.QLabel("当前选择：无")
        self.selection_label.setToolTip("从 Houdini 主线程只读获取的当前节点选择")
        self.include_selection_checkbox = QtWidgets.QCheckBox("包含当前选择")
        self.include_selection_checkbox.setChecked(False)
        selection_row.addWidget(self.selection_label, 1)
        selection_row.addWidget(self.include_selection_checkbox)
        center_layout.addLayout(selection_row)

        self.attachment_strip = AttachmentStrip(self)
        center_layout.addWidget(self.attachment_strip)

        self.input_edit = ExpandableTextEdit(self)
        self.input_edit.setPlaceholderText(
            "输入自然语言请求；Enter 换行，Ctrl+Enter 发送。"
        )
        center_layout.addWidget(self.input_edit)

        action_row = QtWidgets.QHBoxLayout()
        self.add_image_button = QtWidgets.QPushButton("添加图片")
        self.report_issue_button = QtWidgets.QPushButton("记录本次问题")
        self.copy_report_path_button = QtWidgets.QPushButton("复制报告路径")
        self.copy_report_path_button.setVisible(False)
        self.send_button = QtWidgets.QPushButton("发送")
        self.stop_button = QtWidgets.QPushButton("停止")
        action_row.addWidget(self.add_image_button)
        action_row.addWidget(self.report_issue_button)
        action_row.addWidget(self.copy_report_path_button)
        action_row.addStretch(1)
        action_row.addWidget(self.send_button)
        action_row.addWidget(self.stop_button)
        center_layout.addLayout(action_row)

        self.goal_group = QtWidgets.QGroupBox("Goal")
        goal_layout = QtWidgets.QVBoxLayout(self.goal_group)
        goal_help = QtWidgets.QLabel(
            "仅用于长期多步骤任务；普通聊天无需设置。填写目标后点保存，状态由 Codex 更新。"
        )
        goal_help.setWordWrap(True)
        goal_layout.addWidget(goal_help)
        self.goal_focus_checkbox = QtWidgets.QCheckBox("目标专注模式")
        self.goal_focus_checkbox.setToolTip(
            "开启后，仅在重要阶段完成时保留恢复点；Houdini 异常退出才自动恢复并继续当前 Goal。"
        )
        goal_layout.addWidget(self.goal_focus_checkbox)
        self.goal_focus_hint_label = QtWidgets.QLabel(
            "已关闭：普通聊天，不自动恢复或续做。"
        )
        self.goal_focus_hint_label.setWordWrap(True)
        goal_layout.addWidget(self.goal_focus_hint_label)
        self.goal_objective_edit = QtWidgets.QTextEdit()
        self.goal_objective_edit.setPlaceholderText(
            "长期任务目标（普通聊天可留空）"
        )
        self.goal_objective_edit.setMaximumHeight(110)
        goal_layout.addWidget(self.goal_objective_edit)
        goal_state_row = QtWidgets.QHBoxLayout()
        self.goal_status_label = QtWidgets.QLabel("状态：未设置")
        self.goal_status_label.setWordWrap(True)
        goal_state_row.addWidget(self.goal_status_label, 1)
        self.goal_budget_edit = QtWidgets.QLineEdit()
        self.goal_budget_edit.setPlaceholderText("Token 预算（可选）")
        goal_state_row.addWidget(self.goal_budget_edit)
        goal_layout.addLayout(goal_state_row)
        self.goal_activity_label = QtWidgets.QLabel(
            "当前跟进：等待下一轮任务进展"
        )
        self.goal_activity_label.setWordWrap(True)
        goal_layout.addWidget(self.goal_activity_label)
        self.goal_metrics_label = QtWidgets.QLabel("尚未读取 Goal")
        self.goal_metrics_label.setWordWrap(True)
        goal_layout.addWidget(self.goal_metrics_label)
        goal_button_row = QtWidgets.QHBoxLayout()
        self.goal_refresh_button = QtWidgets.QPushButton("刷新")
        self.goal_save_button = QtWidgets.QPushButton("保存（继续跟进）")
        self.goal_clear_button = QtWidgets.QPushButton("清除")
        goal_button_row.addWidget(self.goal_refresh_button)
        goal_button_row.addWidget(self.goal_save_button)
        goal_button_row.addWidget(self.goal_clear_button)
        goal_layout.addLayout(goal_button_row)
        right_layout.addWidget(self.goal_group)

        self.team_group = QtWidgets.QGroupBox("团队")
        team_layout = QtWidgets.QVBoxLayout(self.team_group)
        self.team_combo = QtWidgets.QComboBox()
        self.team_combo.addItem("暂无子任务", None)
        self.team_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.team_combo.setMinimumContentsLength(0)
        self.team_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.team_combo.setToolTip("仅显示原生子任务的可观察事件")
        team_layout.addWidget(self.team_combo)
        self.team_details_text = QtWidgets.QPlainTextEdit()
        self.team_details_text.setReadOnly(True)
        self.team_details_text.setPlaceholderText(
            "选择子任务后查看任务、状态、工具、错误与公开回复。"
        )
        team_layout.addWidget(self.team_details_text, 1)
        right_layout.addWidget(self.team_group, 1)

        self.performance_group = QtWidgets.QGroupBox("本次 Turn 用时")
        performance_layout = QtWidgets.QVBoxLayout(self.performance_group)
        self.performance_label = QtWidgets.QLabel(
            "发送 → ACK：—\nACK → 首个文本：—\n首个文本 → 完成：—"
        )
        self.performance_label.setWordWrap(True)
        performance_layout.addWidget(self.performance_label)
        right_layout.addWidget(self.performance_group)

        self.main_splitter.addWidget(left_column)
        self.main_splitter.addWidget(center_column)
        self.main_splitter.addWidget(right_column)
        for index in range(3):
            self.main_splitter.setCollapsible(index, True)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([230, 720, 300])
        root.addWidget(self.main_splitter, 1)

        self.new_thread_button.clicked.connect(self._new_thread)
        self.resume_thread_button.clicked.connect(self._resume_thread)
        self.add_image_button.clicked.connect(self._choose_images)
        self.report_issue_button.clicked.connect(self._record_manual_issue)
        self.copy_report_path_button.clicked.connect(self._copy_report_path)
        self.input_edit.sendRequested.connect(self._send)
        self.input_edit.imagePasted.connect(self._add_clipboard_image)
        self.send_button.clicked.connect(self._send)
        self.stop_button.clicked.connect(self._stop)
        self.approval_details_button.toggled.connect(self._toggle_approval_details)
        self.persistent_allow_button.clicked.connect(
            lambda: self._resolve_approval("allow_rule")
        )
        self.allow_button.clicked.connect(lambda: self._resolve_approval("allow"))
        self.deny_button.clicked.connect(lambda: self._resolve_approval("deny"))
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.service_tier_combo.currentIndexChanged.connect(
            self._on_service_tier_changed
        )
        self.history_combo.currentIndexChanged.connect(
            self._on_history_index_changed
        )
        self.goal_refresh_button.clicked.connect(self._request_goal)
        self.goal_save_button.clicked.connect(self._save_goal)
        self.goal_clear_button.clicked.connect(self._clear_goal)
        self.goal_focus_checkbox.toggled.connect(self._set_focus_mode)
        self.team_combo.currentIndexChanged.connect(self._on_team_selected)
        self.refresh_threads_button.clicked.connect(self._refresh_threads)
        self.rename_thread_button.clicked.connect(self._rename_thread)
        self.copy_thread_id_button.clicked.connect(self._copy_thread_id)
        self._refresh_controls()

    @staticmethod
    def _set_status_indicator(
        label: Any,
        name: str,
        value: str,
        available: bool,
        tooltip: str | None = None,
    ) -> None:
        label.setText(f"● {name}：{value}")
        label.setStyleSheet(
            "color: #67c587;" if available else "color: #9aa0a8;"
        )
        if tooltip and hasattr(label, "setToolTip"):
            label.setToolTip(tooltip)

    @staticmethod
    def _initial_mcp_backend(value: Any) -> str | None:
        if value is None or value == "":
            return _DEFAULT_MCP_BACKEND
        if isinstance(value, str) and value in _MCP_BACKEND_PRESENTATION:
            return value
        return None

    def _set_mcp_status(self, backend: Any, available: bool) -> None:
        normalized_backend = (
            backend
            if isinstance(backend, str) and backend in _MCP_BACKEND_PRESENTATION
            else None
        )
        self._mcp_backend = normalized_backend
        is_available = available is True
        if normalized_backend is None:
            name = "MCP"
            value = "不可用"
            tooltip = "当前 Houdini MCP 后端状态"
            is_available = False
        else:
            name, tooltip = _MCP_BACKEND_PRESENTATION[normalized_backend]
            value = (
                "回退"
                if is_available and normalized_backend == "fxhoudini"
                else ("可用" if is_available else "不可用")
            )
        label = getattr(self, "houdini_mcp_label", None)
        if label is not None:
            self._set_status_indicator(
                label,
                name,
                value,
                is_available,
                tooltip,
            )

    def _initialize_houdini_read_adapter(self, hou_module: Any | None) -> None:
        """Construct the live reader on the UI thread from launcher-only state."""

        if hou_module is None:
            label = getattr(self, "houdini_connection_label", None)
            if label is not None:
                self._set_status_indicator(label, "Houdini", "未连接", False)
            return
        label = getattr(self, "houdini_connection_label", None)
        if label is not None:
            self._set_status_indicator(label, "Houdini", "已连接", True)
        if self._mcp_backend != "fxhoudini":
            return
        profile = os.environ.get("HIA_SCENE_PROFILE", "")
        launch_id = os.environ.get("HIA_BRIDGE_LAUNCH_ID", "")
        generation = os.environ.get("HIA_BRIDGE_GENERATION", "")
        process_nonce = os.environ.get("HIA_HOUDINI_PROCESS_NONCE", "")
        schema_version = os.environ.get("HIA_HOUDINI_SCHEMA_VERSION", "")
        schema_digest = os.environ.get("HIA_HOUDINI_SCHEMA_DIGEST", "")
        digest_valid = len(schema_digest) == 64 and all(
            character in "0123456789abcdefABCDEF" for character in schema_digest
        )
        try:
            generation_valid = int(generation) >= 0
        except (TypeError, ValueError):
            generation_valid = False
        if not (
            profile == "p2-v-b2-read-only"
            and launch_id
            and generation_valid
            and len(process_nonce) >= 16
            and self._scene_executor_token
            and schema_version == "0.2.0"
            and digest_valid
        ):
            return

        publisher_hash = hashlib.sha256(process_nonce.encode("utf-8")).hexdigest()
        publisher_id = f"panel-{publisher_hash[:16]}-{uuid.uuid4().hex[:16]}"
        fingerprint_key = hashlib.sha256(
            b"hia-b2-fingerprint\0" + process_nonce.encode("utf-8")
        ).digest()
        try:
            adapter = HoudiniReadAdapter(
                hou_module,
                publisher_id=publisher_id,
                pyside_version=str(getattr(PySide6, "__version__", "unknown")),
                fingerprint_key=fingerprint_key,
            )
            report = adapter.start()
        except (HoudiniReadAdapterError, TypeError, ValueError):
            label = getattr(self, "houdini_connection_label", None)
            if label is not None:
                self._set_status_indicator(label, "Houdini", "已连接", True)
            return
        self._houdini_adapter = adapter
        self._last_houdini_report = dict(report) if isinstance(report, dict) else None
        self._update_houdini_status(report, attested=False, pending=True)

    def _read_dirty_state(self):
        hou_module = getattr(self, "_hou_module", None)
        if hou_module is None:
            return None
        try:
            value = hou_module.hipFile.hasUnsavedChanges()
        except Exception:
            return None
        return value if isinstance(value, bool) else None

    def _update_houdini_status(
        self,
        report: Any,
        *,
        attested: bool = False,
        pending: bool = False,
    ) -> None:
        if not isinstance(report, dict):
            report = {}
        del attested, pending
        revision = report.get("scene_revision")
        dirty = self._read_dirty_state()
        houdini_connection_label = getattr(self, "houdini_connection_label", None)
        if houdini_connection_label is not None:
            local_hou_available = getattr(self, "_hou_module", None) is not None
            self._set_status_indicator(
                houdini_connection_label,
                "Houdini",
                "已连接" if local_hou_available else "未连接",
                local_hou_available,
            )
        revision_text = (
            str(revision)
            if isinstance(revision, int) and not isinstance(revision, bool)
            else "不可用"
        )
        dirty_text = "是" if dirty is True else ("否" if dirty is False else "不可用")
        houdini_scene_label = getattr(self, "houdini_scene_label", None)
        if houdini_scene_label is not None:
            houdini_scene_label.setText(
                f"场景版本：{revision_text}  ·  未保存：{dirty_text}"
            )

    def _fail_closed_houdini_status(self, _status: str) -> None:
        """Invalidate the UI-visible live capability until a fresh Bridge ACK."""

        self._scene_attestation_digest = None
        self._scene_catalog_digest = None
        self._attested_houdini_report_identity = None
        self._update_houdini_status(
            self._last_houdini_report,
            attested=False,
            pending=False,
        )

    @staticmethod
    def _houdini_report_identity(report: Any) -> str | None:
        if not isinstance(report, dict):
            return None
        try:
            canonical = json.dumps(
                report,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _houdini_report_is_locally_available(report: Any) -> bool:
        if not isinstance(report, dict) or report.get("available") is not True:
            return False
        catalog = report.get("catalog")
        return bool(
            isinstance(catalog, list)
            and len(catalog) == 5
            and all(
                isinstance(item, dict) and item.get("available") is True
                for item in catalog
            )
        )

    def _start_houdini_read_loop(self) -> None:
        if (
            self._mcp_backend != "fxhoudini"
            or self._houdini_polling_enabled
            or self._houdini_adapter is None
            or self._client is None
        ):
            return
        self._houdini_polling_enabled = True
        self._schedule_houdini_heartbeat(0)

    def _start_local_houdini_loop(self) -> None:
        if self._local_houdini_polling_enabled or self._hou_module is None:
            return
        self._local_houdini_polling_enabled = True
        self._schedule_houdini_heartbeat(0)

    def _schedule_houdini_heartbeat(self, delay_ms: int) -> None:
        if not (
            self._local_houdini_polling_enabled
            or self._houdini_polling_enabled
        ):
            return
        timer = getattr(self, "_houdini_heartbeat_timer", None)
        if timer is None:
            QtCore.QTimer.singleShot(delay_ms, self._houdini_heartbeat)
            return
        timer.stop()
        timer.start(max(0, int(delay_ms)))

    @QtCore.Slot()
    def _houdini_heartbeat(self) -> None:
        if not (
            self._local_houdini_polling_enabled
            or self._houdini_polling_enabled
        ):
            return
        self._refresh_selection_status()
        self._update_houdini_status(self._last_houdini_report or {})
        if (
            self._mcp_backend == "hia_v2"
            and self._client is not None
            and not self._houdini_status_pending
        ):
            request_id = self._client.get_houdini_status()
            self._houdini_status_pending = request_id is not None
            self._houdini_status_turn_token = (
                self._turn_state.capture_token() if request_id is not None else None
            )
        if (
            self._houdini_polling_enabled
            and self._client is not None
            and self._houdini_adapter is not None
            and not self._scene_capability_pending
        ):
            try:
                report = self._houdini_adapter.refresh()
            except HoudiniReadAdapterError:
                self._fail_closed_houdini_status("Catalog：观察失败")
            else:
                self._last_houdini_report = (
                    dict(report) if isinstance(report, dict) else None
                )
                report_identity = self._houdini_report_identity(report)
                renewing_current_report = (
                    report_identity is not None
                    and report_identity == self._attested_houdini_report_identity
                    and self._scene_attestation_digest is not None
                )
                if not renewing_current_report:
                    self._fail_closed_houdini_status("Catalog：待 Bridge 认证")
                request_id = self._client.publish_houdini_capabilities(report)
                self._scene_capability_pending = request_id is not None
                self._pending_houdini_report_identity = (
                    report_identity if self._scene_capability_pending else None
                )
                if request_id is None:
                    self._fail_closed_houdini_status("Catalog：同步失败")
                elif not renewing_current_report:
                    self._update_houdini_status(
                        report,
                        attested=False,
                        pending=True,
                    )
        self._schedule_houdini_heartbeat(_SCENE_HEARTBEAT_MS)

    def _apply_houdini_status(self, status: Any) -> None:
        if not isinstance(status, dict) or status.get("backend") != "hia_v2":
            return
        revision = status.get("scene_revision")
        report = {
            "scene_revision": (
                revision
                if isinstance(revision, int)
                and not isinstance(revision, bool)
                and revision >= 0
                else None
            )
        }
        self._last_houdini_report = report
        self._update_houdini_status(report)

    def _schedule_scene_work_poll(self, delay_ms: int) -> None:
        if (
            not self._houdini_polling_enabled
            or self._scene_attestation_digest is None
        ):
            return
        timer = getattr(self, "_scene_work_timer", None)
        if timer is None:
            QtCore.QTimer.singleShot(delay_ms, self._poll_scene_work)
            return
        timer.stop()
        timer.start(max(0, int(delay_ms)))

    @QtCore.Slot()
    def _poll_scene_work(self) -> None:
        if (
            not self._houdini_polling_enabled
            or self._client is None
            or self._scene_attestation_digest is None
            or self._scene_work_pending
        ):
            return
        request_id = self._client.poll_scene_work(250)
        self._scene_work_pending = request_id is not None

    def _handle_scene_action(self, context: str, payload: dict[str, Any]) -> bool:
        if context == _SCENE_CAPABILITY_CONTEXT:
            self._scene_capability_pending = False
            pending_identity = self._pending_houdini_report_identity
            self._pending_houdini_report_identity = None
            digest = payload.get("attestation_digest")
            catalog_digest = payload.get("catalog_digest")
            report = self._last_houdini_report
            report_identity = self._houdini_report_identity(report)
            observer_sequence = payload.get("observer_sequence")
            digest_valid = (
                isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
            )
            catalog_digest_valid = (
                isinstance(catalog_digest, str)
                and len(catalog_digest) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in catalog_digest
                )
            )
            if (
                payload.get("available") is True
                and digest_valid
                and catalog_digest_valid
                and isinstance(report, dict)
                and self._houdini_report_is_locally_available(report)
                and report_identity is not None
                and pending_identity == report_identity
                and isinstance(observer_sequence, int)
                and not isinstance(observer_sequence, bool)
                and observer_sequence == report.get("observer_sequence")
                and (
                    self._attested_houdini_report_identity != report_identity
                    or self._scene_attestation_digest is None
                    or digest == self._scene_attestation_digest
                )
            ):
                self._scene_attestation_digest = digest
                self._scene_catalog_digest = catalog_digest
                self._attested_houdini_report_identity = report_identity
                self._update_houdini_status(report, attested=True)
                self._schedule_scene_work_poll(0)
            else:
                self._fail_closed_houdini_status("Catalog：不可用")
            return True

        if context == _SCENE_WORK_CONTEXT:
            self._scene_work_pending = False
            work = payload.get("work")
            if work is None:
                self._schedule_scene_work_poll(_SCENE_IDLE_POLL_MS)
                return True
            if not isinstance(work, dict) or work.get("kind") != "execute":
                self._fail_closed_houdini_status("Catalog：拒绝异常工作项")
                return True
            request_id = work.get("request_id")
            executor_token = work.get("executor_token")
            attestation_digest = work.get("attestation_digest")
            tool_name = work.get("tool_name")
            arguments = work.get("arguments")
            deadline = work.get("absolute_deadline")
            if attestation_digest != self._scene_attestation_digest:
                self._fail_closed_houdini_status("Catalog：能力快照已变化")
                return True
            if (
                self._client is None
                or self._houdini_adapter is None
                or not isinstance(request_id, str)
                or not isinstance(executor_token, str)
                or not isinstance(tool_name, str)
                or not isinstance(arguments, dict)
            ):
                self._fail_closed_houdini_status("Catalog：工作项字段无效")
                return True
            try:
                result = self._houdini_adapter.execute(
                    tool_name,
                    arguments,
                    absolute_deadline=deadline,
                )
            except (HoudiniReadAdapterError, TypeError, ValueError) as exc:
                self._fail_closed_houdini_status("Catalog：只读执行失败")
                if self._turn_state.busy:
                    self._record_final_runtime_failure(
                        "HOM 执行",
                        "HOM_EXECUTION_FAILED",
                        str(exc),
                        slug="hom-failure",
                    )
                return True
            submitted = self._client.complete_scene_work(
                request_id,
                executor_token,
                result,
            )
            if submitted is None:
                self._fail_closed_houdini_status("Catalog：结果提交失败")
                if self._turn_state.busy:
                    self._record_final_runtime_failure(
                        "Bridge 结果提交",
                        "BRIDGE_RESULT_SUBMIT_FAILED",
                        "Houdini 执行结果无法提交给 Bridge",
                        slug="bridge-result-failure",
                    )
            return True

        if context.startswith(_SCENE_RESULT_CONTEXT_PREFIX):
            self._schedule_scene_work_poll(0)
            return True
        return False

    def _set_connection(self, text: str, connected: bool) -> None:
        self._connected = connected
        self._set_status_indicator(
            self.connection_label,
            "Codex",
            "已连接" if connected else "未连接",
            connected,
            text,
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
        thread_switch_enabled = (
            session_enabled
            and self._goal_action_context is None
            and not self._turn_steer_request_pending
        )
        request_ready = session_enabled and not self._turn_steer_request_pending
        stopping = self._is_stopping_turn()
        steer_available = controls.stop and not stopping
        history_record = self._selected_history_record()
        history_available = history_record is not None
        self.new_thread_button.setEnabled(
            controls.new_thread and thread_switch_enabled
        )
        self.resume_thread_button.setEnabled(
            controls.resume_thread and thread_switch_enabled and history_available
        )
        self.history_combo.setEnabled(
            self._connected
            and not self._turn_state.busy
            and thread_switch_enabled
        )
        self.refresh_threads_button.setEnabled(
            self._connected and not self._session_action_pending
        )
        self.thread_name_edit.setEnabled(
            history_available and not self._turn_state.busy and session_enabled
        )
        self.rename_thread_button.setEnabled(
            history_available and not self._turn_state.busy and session_enabled
        )
        self.copy_thread_id_button.setEnabled(history_available)
        self.send_button.setEnabled(
            (controls.send or steer_available) and request_ready
        )
        self.stop_button.setEnabled(
            controls.stop and not stopping and not self._interrupt_pending
        )
        self.send_button.setText(
            "发送中…"
            if self._turn_start_request_pending
            else (
                "追加中…"
                if self._turn_steer_request_pending
                else (
                    "追加指令"
                    if steer_available
                    else (
                        "已停止"
                        if stopping
                        else ("Codex 回复中…" if self._turn_state.busy else "发送")
                    )
                )
            )
        )
        self.thread_id_edit.setEnabled(
            not self._turn_state.busy and thread_switch_enabled
        )
        selection_enabled = (
            (self._connected or self._stop_recovery_state in {"recovering", "failed"})
            and not self._turn_state.busy
            and session_enabled
        )
        self.model_combo.setEnabled(selection_enabled)
        self.effort_combo.setEnabled(selection_enabled)
        self.service_tier_combo.setEnabled(
            selection_enabled and self.service_tier_combo.count() > 1
        )
        composer_enabled = stopping or (
            (not self._turn_state.busy or steer_available) and request_ready
        )
        editor_enabled = composer_enabled or bool(
            self._turn_start_request_pending
            or self._turn_steer_request_pending
            or self._reconciliation_tokens
        )
        input_edit = getattr(self, "input_edit", None)
        if input_edit is not None:
            input_edit.setEnabled(editor_enabled)
        add_image_button = getattr(self, "add_image_button", None)
        if add_image_button is not None:
            add_image_button.setEnabled(
                composer_enabled
                and isinstance(self._selected_thread_id, str)
                and self._selected_model_supports_images()
            )
        include_selection = getattr(self, "include_selection_checkbox", None)
        if include_selection is not None:
            include_selection.setEnabled(composer_enabled)
        attachment_strip = getattr(self, "attachment_strip", None)
        if attachment_strip is not None:
            attachment_strip.setEnabled(composer_enabled)
        goal_enabled = (
            self._connected
            and isinstance(self._selected_thread_id, str)
            and self._goal_action_context is None
            and not self._session_action_pending
            and not self._turn_state.busy
            and not self._turn_steer_request_pending
        )
        for name in (
            "goal_objective_edit",
            "goal_budget_edit",
            "goal_refresh_button",
            "goal_save_button",
            "goal_clear_button",
            "goal_focus_checkbox",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(goal_enabled)

    @QtCore.Slot(dict)
    def _on_health(self, payload: dict[str, Any]) -> None:
        was_reconnecting = self._reconnecting or self._reconnect_attempt > 0
        self._reconnect_timer.stop()
        self._reconnecting = False
        self._reconnect_attempt = 0
        self._reconnect_exhausted_notice_shown = False
        self._set_connection("已连接（stdio JSONL）", True)
        houdini_mcp = payload.get("houdini_mcp")
        mcp_backend = (
            houdini_mcp.get("backend") if isinstance(houdini_mcp, dict) else None
        )
        mcp_available = (
            isinstance(houdini_mcp, dict) and houdini_mcp.get("available") is True
        )
        self._set_mcp_status(mcp_backend, mcp_available)
        self._apply_houdini_status(houdini_mcp)
        session = payload.get("session", {})
        self._apply_session(
            session,
            token=self._turn_state.capture_token(),
            allow_followup=True,
        )
        if not self._connected:
            if self._stop_recovery_state == "recovering":
                self._polling_enabled = True
                self._schedule_poll(0)
                self._refresh_controls()
                return
            self._polling_enabled = False
            if not self._app_server_exit_notice_shown:
                self._app_server_exit_notice_shown = True
                if isinstance(self._selected_thread_id, str):
                    self._append_system(
                        "Codex app-server 当前不可用，请重启 launcher；"
                        "不会自动重放 Turn 或 Houdini 操作。"
                    )
            self._refresh_controls()
            return
        self._maybe_request_crash_recovery_goal(session)
        self._app_server_exit_notice_shown = False
        self._polling_enabled = True
        self._schedule_poll(0)
        if not self._models_requested and self._client is not None:
            self._models_requested = True
            self._client.get_models()
        if not self._threads_requested and self._client is not None:
            self._threads_requested = True
            self._client.get_threads()
        if was_reconnecting and self._client is not None:
            if isinstance(self._selected_thread_id, str):
                self._append_system(
                    "已重新连接；已同步会话状态，不会自动重放 Turn。"
                )
        if isinstance(self._selected_thread_id, str):
            self._request_goal()
        self._start_houdini_read_loop()

    @QtCore.Slot(dict)
    def _on_session(self, payload: dict[str, Any]) -> None:
        session = payload.get("session", {})
        self._apply_session(
            session,
            token=self._turn_state.capture_token(),
            allow_followup=True,
        )
        if isinstance(session, dict):
            self._reconcile_crash_recovery_session(session)
        self._maybe_start_goal_continuation()

    def _apply_session(
        self,
        session: dict[str, Any],
        *,
        token: TurnStateToken,
        allow_followup: bool,
        steer_recovery: bool = False,
    ) -> bool:
        """Apply a correlated snapshot, rejecting stale Turn state atomically."""

        if not self._turn_state.token_is_current(token):
            return False

        was_busy = self._turn_state.busy
        previous_turn_token = self._turn_state.capture_token()
        previous_turn_was_auto = self._goal_auto_turn_is_current(
            previous_turn_token.thread_id
        )
        stopping = self._is_stopping_turn()
        thread_id = session.get("thread_id")
        turn_id = session.get("turn_id")
        turn_status = session.get("turn_status")
        turn_active = session.get("turn_active")
        if (
            turn_active is False
            and self._goal_continuation_boundary == (thread_id, turn_id)
            and isinstance(self._goal_auto_turn_token, TurnStateToken)
        ):
            # A repeated idle snapshot for the completed source Turn cannot
            # roll back the continuation that has already reserved a new Turn.
            turn_active = None
        previous_recovery_state = self._stop_recovery_state
        if turn_status == "stopRecovering":
            self._stop_recovery_state = "recovering"
        elif turn_status == "stopRecoveryFailed":
            self._stop_recovery_state = "failed"
        elif (
            previous_recovery_state == "recovering"
            and turn_active is True
            and turn_status in {"stopping", "stopRequested"}
        ):
            self._stop_recovery_state = "recovering"
        elif session.get("connected") is True:
            self._stop_recovery_state = None
        session_is_selected = (
            isinstance(thread_id, str)
            and bool(thread_id)
            and thread_id == self._selected_thread_id
        )
        previous_thread_id = self._selected_thread_id
        selected_thread_changed = False
        state_applied = True
        suppress_active_stop_snapshot = (
            self._stop_recovery_state == "recovering"
            and session_is_selected
            and turn_active is True
            and turn_status in {"stopping", "stopRequested"}
        )
        if isinstance(turn_active, bool) and not suppress_active_stop_snapshot:
            if (
                isinstance(thread_id, str)
                and thread_id
                and (session_is_selected or self._selected_thread_id is None)
            ):
                if steer_recovery:
                    state_applied = self._turn_state.reconcile_steer_snapshot(
                        token,
                        thread_id,
                        turn_id if isinstance(turn_id, str) else None,
                        turn_active=turn_active,
                    )
                else:
                    state_applied = self._turn_state.reconcile_snapshot(
                        token,
                        thread_id,
                        turn_id if isinstance(turn_id, str) else None,
                        turn_status if isinstance(turn_status, str) else None,
                        turn_active=turn_active,
                    )
            elif turn_active or self._turn_state.busy:
                state_applied = False

        if (
            state_applied
            and not suppress_active_stop_snapshot
            and turn_active is True
            and turn_status in {"stopping", "stopRequested"}
            and session_is_selected
        ):
            if not self._is_stopping_turn():
                self._stopping_turn_token = self._turn_state.capture_token()
                self._stream_thread_id = None
                self._stream_turn_id = None
                self._freeze_codex_message()
                if thread_id == self._selected_thread_id:
                    self._append_system(
                        "Codex 已停止；已发出的 Houdini 操作可能仍在收尾。"
                    )
            stopping = True

        if self._stop_recovery_state in {"recovering", "failed"}:
            active_interrupt_context = self._active_interrupt_context
            if active_interrupt_context is not None:
                self._interrupt_tokens.pop(active_interrupt_context, None)
            self._active_interrupt_context = None
            self._interrupt_pending = False
            self._stopping_turn_token = None

        self._connected = bool(session.get("connected")) and (
            self._stop_recovery_state is None
        )
        connection_state = (
            "恢复中"
            if self._stop_recovery_state == "recovering"
            else ("未连接" if not self._connected else "已连接")
        )
        self._set_status_indicator(
            self.connection_label,
            "Codex",
            connection_state,
            self._connected,
            (
                "Codex app-server 正在恢复同一 Thread"
                if self._stop_recovery_state == "recovering"
                else "Codex app-server 会话状态"
            ),
        )
        authentication = session.get("authentication")
        if self._stop_recovery_state == "recovering":
            self.auth_label.setText("认证：恢复中")
        elif authentication == "authenticated":
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

        if state_applied and session_is_selected:
            if (
                isinstance(self._selected_thread_id, str)
                and self._selected_thread_id != thread_id
            ):
                self.attachment_strip.clear()
                self._stream_thread_id = None
                self._stream_turn_id = None
                self._clear_diagnostic_context()
            if previous_thread_id != thread_id:
                selected_thread_changed = True
                self._stopped_source_turn = None
                self._goal_action_context = None
                self._clear_goal_display()
                self._team_records.clear()
                self._refresh_team_combo()
                self._clear_turn_performance()
            self._selected_thread_id = thread_id
            self._apply_focus_mode(thread_id, session.get("focus_mode", False))
            self.thread_id_edit.setText(thread_id)
            self.thread_status_label.setText(
                f"Thread：{self._history_title(thread_id)}"
            )
            self.thread_status_label.setToolTip(
                f"{self._history_title(thread_id, full=True)}\n"
                f"Codex Thread ID：{thread_id}"
            )
        elif (
            state_applied
            and not self._turn_state.busy
            and not isinstance(thread_id, str)
        ):
            selected_thread_changed = previous_thread_id is not None
            self._goal_action_context = None
            self._selected_thread_id = None
            self._stopped_source_turn = None
            self._focus_mode = False
            focus_checkbox = getattr(self, "goal_focus_checkbox", None)
            if focus_checkbox is not None:
                focus_checkbox.blockSignals(True)
                focus_checkbox.setChecked(False)
                focus_checkbox.blockSignals(False)
            focus_hint = getattr(self, "goal_focus_hint_label", None)
            if focus_hint is not None:
                focus_hint.setText("已关闭：普通聊天，不自动恢复或续做。")
            self._clear_goal_display()
            self._team_records.clear()
            self._refresh_team_combo()
            self.thread_id_edit.setText("")
            self.thread_status_label.setText("Thread：未选择")
            self.thread_status_label.setToolTip("当前未选择 Codex Thread")
            self._clear_diagnostic_context()
            if selected_thread_changed:
                self._clear_turn_performance()

        if (
            state_applied
            and not suppress_active_stop_snapshot
            and turn_active is True
            and isinstance(thread_id, str)
            and isinstance(turn_id, str)
            and session_is_selected
            and not stopping
        ):
            self._stream_thread_id = thread_id
            self._stream_turn_id = turn_id
            if not self._diagnostic_snapshot:
                self._diagnostic_turn_key = f"{thread_id}:{turn_id}"
                self._diagnostic_snapshot = self._new_diagnostic_snapshot(
                    thread_id=thread_id,
                    user_goal="恢复中的活动 Turn",
                    attachment_paths=self._attachment_paths(),
                )
                self._diagnostic_tool_states = {}
                self._diagnostic_event_errors = []
            self._bind_diagnostic_turn(thread_id, turn_id)
        elif (
            state_applied
            and session_is_selected
            and turn_active is False
            and not self._turn_state.busy
        ):
            self._stream_thread_id = None
            self._stream_turn_id = None

        if state_applied and session_is_selected and isinstance(turn_active, bool):
            if suppress_active_stop_snapshot:
                self.turn_status_label.setText("Turn：已停止")
            elif stopping and turn_active:
                self.turn_status_label.setText("Turn：已停止")
            elif stopping:
                self._mark_turn_terminal("interrupted")
            elif allow_followup and was_busy and not self._turn_state.busy:
                self._mark_turn_terminal(
                    turn_status if isinstance(turn_status, str) else None
                )
                self._queue_goal_continuation(
                    previous_turn_token.thread_id,
                    previous_turn_token.turn_id,
                    turn_status,
                    auto_turn=previous_turn_was_auto,
                )
            else:
                self.turn_status_label.setText(
                    self._turn_status_text(
                        turn_status if isinstance(turn_status, str) else None,
                        active=turn_active,
                    )
                )
        if not state_applied and allow_followup:
            self._request_session_reconciliation("session_conflict")
        if selected_thread_changed and isinstance(self._selected_thread_id, str):
            self._request_goal()
        if self._stop_recovery_state == "failed":
            self._polling_enabled = False
            self._poll_timer.stop()
            if previous_recovery_state != "failed" and isinstance(
                self._selected_thread_id, str
            ):
                self._append_system(
                    "Codex 自动恢复未成功；草稿和附件已保留，请重启 launcher。"
                )
        elif (
            previous_recovery_state == "recovering"
            and self._stop_recovery_state is None
            and self._connected
            and isinstance(self._selected_thread_id, str)
        ):
            self._append_system(
                "Codex 已恢复并重新连接当前会话；未重放已停止的 Turn。"
            )
        if previous_recovery_state != self._stop_recovery_state:
            self.goal_activity_label.setText(self._goal_waiting_activity_text())
        self._refresh_controls()
        return state_applied

    def _fill_prompt(self, text: str) -> None:
        self.input_edit.setPlainText(text)

    def _read_selected_node_paths(
        self,
        *,
        report_failure: bool = False,
    ) -> tuple[str, ...]:
        hou_module = getattr(self, "_hou_module", None)
        if hou_module is None:
            return ()
        try:
            nodes = tuple(hou_module.selectedNodes())
            paths = tuple(node.path() for node in nodes)
        except Exception as exc:
            if report_failure:
                self._record_pre_turn_issue(
                    "读取当前选择",
                    "SELECTION_READ_FAILED",
                    str(exc),
                )
            return ()
        return tuple(
            path for path in dict.fromkeys(paths) if isinstance(path, str) and path
        )

    def _refresh_selection_status(
        self, paths: tuple[str, ...] | None = None
    ) -> None:
        if paths is None:
            paths = self._read_selected_node_paths()
        self._selected_node_paths = paths
        label = getattr(self, "selection_label", None)
        if label is None:
            return
        if not paths:
            label.setText("当前选择：无")
        elif len(paths) == 1:
            label.setText(f"当前选择：{paths[0]}")
        else:
            label.setText(f"当前选择：{paths[0]} 等 {len(paths)} 个节点")
        if hasattr(label, "setToolTip"):
            label.setToolTip("\n".join(paths) if paths else "当前没有选中节点")

    def _attachment_paths(self) -> tuple[str, ...]:
        strip = getattr(self, "attachment_strip", None)
        if strip is None or not hasattr(strip, "paths"):
            return ()
        try:
            values = strip.paths()
        except Exception:
            return ()
        return tuple(path for path in values if isinstance(path, str) and path)

    def _add_attachment_path(self, path: str) -> None:
        if len(self._attachment_paths()) >= _MAX_TURN_IMAGES:
            self._append_system(
                f"每轮最多添加 {_MAX_TURN_IMAGES} 张图片。"
            )
            return
        strip = getattr(self, "attachment_strip", None)
        if strip is not None and hasattr(strip, "add_path"):
            strip.add_path(path)

    def _choose_images(self) -> None:
        thread_id = self._selected_thread_id
        if not isinstance(thread_id, str) or not thread_id:
            self._append_system("请先新建或恢复 Thread，再添加图片。")
            return
        if not self._selected_model_supports_images():
            self._append_system("当前模型不支持图片输入，请先选择支持图片的模型。")
            return
        if self._attachment_dialog is not None:
            return
        project_root = Path(__file__).resolve().parents[3]
        dialog = QtWidgets.QFileDialog(
            self,
            "添加参考图片",
            str(project_root),
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        dialog.setOption(
            QtWidgets.QFileDialog.Option.DontUseNativeDialog,
            True,
        )
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.filesSelected.connect(self._accept_chosen_images)
        dialog.finished.connect(self._attachment_dialog_finished)
        dialog.destroyed.connect(self._attachment_dialog_destroyed)
        self._attachment_dialog = dialog
        dialog.show()

    @QtCore.Slot(list)
    def _accept_chosen_images(self, paths: list[str]) -> None:
        thread_id = self._selected_thread_id
        if not isinstance(thread_id, str) or not thread_id:
            return
        remaining = max(0, _MAX_TURN_IMAGES - len(self._attachment_paths()))
        if len(paths) > remaining:
            self._append_system(
                f"每轮最多添加 {_MAX_TURN_IMAGES} 张图片；已忽略多余选择。"
            )
        for source in paths[:remaining]:
            try:
                stored = self._attachment_store.copy_file(thread_id, source)
            except (OSError, TypeError, ValueError) as exc:
                self._append_system(f"图片添加失败：{exc}")
                self._record_pre_turn_issue(
                    "图片复制",
                    "ATTACHMENT_COPY_FAILED",
                    str(exc),
                    attachment=source,
                )
            else:
                self._add_attachment_path(stored)

    @QtCore.Slot(int)
    def _attachment_dialog_finished(self, _result: int) -> None:
        dialog = self._attachment_dialog
        self._attachment_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    @QtCore.Slot(object)
    def _attachment_dialog_destroyed(self, dialog: Any = None) -> None:
        if self._attachment_dialog is dialog:
            self._attachment_dialog = None

    @QtCore.Slot(object)
    def _add_clipboard_image(self, image: Any) -> None:
        thread_id = self._selected_thread_id
        if not isinstance(thread_id, str) or not thread_id:
            self._append_system("请先新建或恢复 Thread，再粘贴图片。")
            return
        if not self._selected_model_supports_images():
            self._append_system("当前模型不支持图片输入，请先选择支持图片的模型。")
            return
        if len(self._attachment_paths()) >= _MAX_TURN_IMAGES:
            self._append_system(f"每轮最多添加 {_MAX_TURN_IMAGES} 张图片。")
            return
        try:
            path = self._attachment_store.clipboard_path(thread_id)
            if hasattr(image, "toImage"):
                image = image.toImage()
            if not hasattr(image, "save") or image.save(path, "PNG") is not True:
                raise ValueError("剪贴板图片无法保存为 PNG")
        except (OSError, TypeError, ValueError) as exc:
            self._append_system(f"剪贴板图片添加失败：{exc}")
            self._record_pre_turn_issue(
                "剪贴板图片",
                "CLIPBOARD_IMAGE_FAILED",
                str(exc),
            )
            return
        self._add_attachment_path(path)

    def _request_text_with_selection(self, text: str) -> str:
        checkbox = getattr(self, "include_selection_checkbox", None)
        if checkbox is None or not checkbox.isChecked():
            return text
        paths = self._read_selected_node_paths(report_failure=True)
        self._selected_node_paths = paths
        self._refresh_selection_status(paths)
        if not paths:
            return text
        selection = "\n".join(f"- {path}" for path in paths)
        prefix = "\n\n" if text else ""
        return f"{text}{prefix}当前 Houdini 选择（只读上下文）：\n{selection}"

    def _add_user_message(
        self,
        text: str,
        attachment_paths: tuple[str, ...],
        *,
        same_turn: bool = False,
    ) -> None:
        names = tuple(os.path.basename(path) for path in attachment_paths)
        if hasattr(self.conversation, "add_user_message"):
            self.conversation.add_user_message(
                text or "（仅图片）",
                names,
                same_turn=same_turn,
            )
        else:
            self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self.conversation.insertPlainText(f"\nYou: {text or '（仅图片）'}\n")

    def _begin_codex_message(self) -> None:
        if hasattr(self.conversation, "begin_codex_message"):
            self.conversation.begin_codex_message()
        else:
            self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self.conversation.insertPlainText("Codex: ")

    def _append_codex_delta(self, delta: str) -> None:
        if hasattr(self.conversation, "append_codex_delta"):
            self.conversation.append_codex_delta(delta)
        else:
            self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self.conversation.insertPlainText(delta)

    def _freeze_codex_message(self) -> None:
        if hasattr(self.conversation, "freeze_codex_message"):
            self.conversation.freeze_codex_message()
        else:
            self._finish_codex_message()

    def _is_stopping_turn(self) -> bool:
        return (
            isinstance(self._stopping_turn_token, TurnStateToken)
            and self._turn_state.token_is_current(self._stopping_turn_token)
        )

    def _event_matches_active_stream(
        self,
        params: dict[str, Any],
        *,
        require_item_id: bool = False,
    ) -> bool:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if (
            self._is_stopping_turn()
            or not self._turn_state.busy
            or not isinstance(thread_id, str)
            or not isinstance(turn_id, str)
            or thread_id != self._stream_thread_id
            or turn_id != self._stream_turn_id
        ):
            return False
        return not require_item_id or isinstance(params.get("itemId"), str)

    def _event_matches_goal_stream(
        self,
        params: dict[str, Any],
        *,
        require_item_id: bool = False,
    ) -> bool:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if (
            self._is_stopping_turn()
            or self._turn_state.busy
            or not self._goal_turn_matches(thread_id, turn_id)
            or thread_id != self._stream_thread_id
            or turn_id != self._stream_turn_id
        ):
            return False
        return not require_item_id or isinstance(params.get("itemId"), str)

    def _event_is_stale_source_turn(
        self,
        thread_id: Any,
        turn_id: Any,
    ) -> bool:
        if self._stopped_source_turn == (thread_id, turn_id):
            return True
        if (
            self._goal_continuation_boundary == (thread_id, turn_id)
            and isinstance(self._goal_auto_turn_token, TurnStateToken)
        ):
            return True
        context = self._active_turn_start_context
        draft = self._pending_turn_drafts.get(context) if context else None
        if (
            isinstance(draft, dict)
            and draft.get("steer_fallback") is True
            and thread_id == draft.get("steer_source_thread_id")
            and turn_id == draft.get("steer_source_turn_id")
        ):
            return True
        for pending in self._pending_steer_drafts.values():
            source_token = pending.get("source_token")
            if (
                pending.get("state_sync_attempted") is True
                and isinstance(source_token, TurnStateToken)
                and thread_id == source_token.thread_id
                and turn_id == source_token.turn_id
            ):
                return True
        return False

    def _finish_codex_message(self) -> None:
        if hasattr(self.conversation, "finish_codex_message"):
            self.conversation.finish_codex_message()

    def _update_tool_activity(
        self,
        item_id: str,
        tool_name: str,
        status: str,
        error: Any = None,
    ) -> None:
        if hasattr(self.conversation, "update_tool_activity"):
            self.conversation.update_tool_activity(
                item_id,
                tool_name,
                status,
                error,
            )

    def _update_tool_progress(self, item_id: str, message: str) -> None:
        if hasattr(self.conversation, "update_tool_progress"):
            self.conversation.update_tool_progress(item_id, message)

    def _accept_sent_draft(self, context: str) -> dict[str, Any] | None:
        draft = self._pending_turn_drafts.pop(context, None)
        if not isinstance(draft, dict):
            return None
        if draft.get("goal_auto_continue") is True:
            return draft
        text = draft.get("text")
        if isinstance(text, str) and self.input_edit.toPlainText() == text:
            self.input_edit.clear()
        paths = tuple(draft.get("attachment_paths") or ())
        if paths and paths == self._attachment_paths():
            self.attachment_strip.clear()
        return draft

    def _accept_steered_draft(self, context: str) -> None:
        draft = self._pending_steer_drafts.pop(context, None)
        if not isinstance(draft, dict):
            return
        text = draft.get("text")
        paths = tuple(draft.get("attachment_paths") or ())
        if isinstance(text, str):
            self._merge_diagnostic_user_input(text, paths)
            self._add_user_message(text, paths, same_turn=True)
            if self.input_edit.toPlainText() == text:
                self.input_edit.clear()
        if paths and paths == self._attachment_paths():
            self.attachment_strip.clear()

    def _restore_steer_draft_to_composer(self, draft: Any) -> None:
        if (
            not isinstance(draft, dict)
            or draft.get("fallback_cancelled") is True
        ):
            return
        text = draft.get("text")
        current_text = self.input_edit.toPlainText()
        if (
            isinstance(text, str)
            and text.strip()
            and text not in current_text
        ):
            self.input_edit.setPlainText(
                f"{text}\n\n{current_text}" if current_text else text
            )
        current_paths = set(self._attachment_paths())
        for path in tuple(draft.get("attachment_paths") or ()):
            if isinstance(path, str) and path and path not in current_paths:
                self.attachment_strip.add_path(path)
                current_paths.add(path)

    @staticmethod
    def _bounded_goal_summary(text: Any, limit: int = 360) -> str:
        value = " ".join(str(text or "").split())
        return value if len(value) <= limit else value[:limit] + "…"

    def _clear_diagnostic_context(self) -> None:
        self._diagnostic_turn_key = None
        self._diagnostic_draft_key = None
        self._diagnostic_snapshot = {}
        self._diagnostic_tool_states = {}
        self._diagnostic_event_errors = []

    @classmethod
    def _diagnostic_error_text(cls, value: Any) -> str:
        if isinstance(value, str):
            return cls._bounded_goal_summary(value, 2_000)
        if isinstance(value, dict):
            for key in ("message", "error", "detail", "reason"):
                if key in value:
                    text = cls._diagnostic_error_text(value.get(key))
                    if text:
                        return text
            code = value.get("code")
            return str(code) if isinstance(code, (str, int)) else ""
        if isinstance(value, (list, tuple)):
            parts = [cls._diagnostic_error_text(item) for item in value[:8]]
            return " | ".join(part for part in parts if part)
        return cls._bounded_goal_summary(value, 2_000) if value is not None else ""

    @staticmethod
    def _diagnostic_error_code(value: Any, fallback: str) -> str:
        if isinstance(value, dict):
            for key in ("code", "error_code", "errorCode"):
                code = value.get(key)
                if isinstance(code, (str, int)) and str(code):
                    return str(code)
        return fallback

    def _diagnostic_scene_fields(self) -> dict[str, Any]:
        report = (
            self._last_houdini_report
            if isinstance(self._last_houdini_report, dict)
            else {}
        )
        dirty = self._read_dirty_state()
        revision = report.get("scene_revision")
        return {
            "houdini_build": report.get("houdini_build", "不可用"),
            "python_version": report.get(
                "python_version",
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            ),
            "plugin_or_git_commit": os.environ.get(
                "HIA_GIT_COMMIT",
                "hia_panel（Git commit 不可用）",
            ),
            "selection": list(self._selected_node_paths),
            "scene_revision": (
                revision
                if isinstance(revision, int) and not isinstance(revision, bool)
                else "不可用"
            ),
            "dirty": dirty if isinstance(dirty, bool) else "不可用",
        }

    def _new_diagnostic_snapshot(
        self,
        *,
        thread_id: str | None,
        user_goal: str,
        attachment_paths: tuple[str, ...],
    ) -> dict[str, Any]:
        scene = self._diagnostic_scene_fields()
        return {
            **scene,
            "_initial_scene_revision": scene.get("scene_revision"),
            "_initial_dirty": scene.get("dirty"),
            "status": "进行中",
            "thread_id": thread_id or "不可用",
            "turn_id": "尚未确认",
            "model": self._selected_model_id() or "Codex 默认",
            "effort": self._selected_effort() or "Codex 默认",
            "user_goal": self._bounded_goal_summary(user_goal) or "未提供",
            "expected": "按自然语言请求在当前 Houdini 场景中完成可编辑结果",
            "actual": "等待执行结果",
            "stage": "Turn 建立",
            "tool_order": [],
            "error_code": "未提供",
            "error_text": "未提供",
            "traceback": "未提供",
            "retries": 0,
            "recovery": "未观察到恢复操作",
            "nodes": [],
            "scene_modified": "待确认",
            "root_path": "未观察到",
            "manual_check": "等待真实 Houdini GUI 验收",
            "undo": "未执行",
            "attachments": [os.path.basename(path) for path in attachment_paths],
            "reproduction": "在相同当前场景状态下重新发送本轮请求",
            "workaround": "无",
            "impact": "待确认",
            "next_step": "读取真实最终状态与错误后处理",
            "hypotheses": "待验证假设：无",
        }

    def _begin_diagnostic_turn(
        self,
        thread_id: str,
        token: TurnStateToken,
        text: str,
        attachment_paths: tuple[str, ...],
    ) -> None:
        key = self._diagnostic_draft_key or f"{thread_id}:{token.generation}"
        self._diagnostic_draft_key = None
        self._diagnostic_turn_key = key
        self._diagnostic_snapshot = self._new_diagnostic_snapshot(
            thread_id=thread_id,
            user_goal=text,
            attachment_paths=attachment_paths,
        )
        self._diagnostic_tool_states = {}
        self._diagnostic_event_errors = []

    def _merge_diagnostic_user_input(
        self,
        text: str,
        attachment_paths: tuple[str, ...],
    ) -> None:
        if not self._diagnostic_snapshot:
            return
        summary = self._bounded_goal_summary(text)
        existing = str(self._diagnostic_snapshot.get("user_goal", ""))
        if summary:
            self._diagnostic_snapshot["user_goal"] = self._bounded_goal_summary(
                f"{existing}；追加：{summary}",
                720,
            )
        attachments = list(self._diagnostic_snapshot.get("attachments") or [])
        for path in attachment_paths:
            name = os.path.basename(path)
            if name and name not in attachments:
                attachments.append(name)
        self._diagnostic_snapshot["attachments"] = attachments

    def _bind_diagnostic_turn(self, thread_id: Any, turn_id: Any) -> None:
        if not self._diagnostic_snapshot:
            return
        if isinstance(thread_id, str) and thread_id:
            self._diagnostic_snapshot["thread_id"] = thread_id
        if isinstance(turn_id, str) and turn_id:
            self._diagnostic_snapshot["turn_id"] = turn_id
        if (
            isinstance(thread_id, str)
            and thread_id
            and isinstance(turn_id, str)
            and turn_id
        ):
            current_key = self._diagnostic_turn_key
            writer = getattr(self, "_diagnostic_writer", None)
            current_path = None
            if (
                isinstance(current_key, str)
                and writer is not None
                and hasattr(writer, "path_for")
            ):
                current_path = writer.path_for(current_key)
            if not current_path:
                self._diagnostic_turn_key = f"{thread_id}:{turn_id}"
        self._diagnostic_snapshot["stage"] = "Turn 执行"

    @staticmethod
    def _diagnostic_node_paths(value: Any) -> tuple[list[str], str | None]:
        nodes: list[str] = []
        root_path: str | None = None
        pending: list[tuple[Any, int, str]] = [(value, 0, "")]
        while pending and len(nodes) < 64:
            current, depth, field = pending.pop()
            if depth > 5:
                continue
            if isinstance(current, dict):
                for key, child in list(current.items())[:64]:
                    pending.append((child, depth + 1, str(key)))
            elif isinstance(current, (list, tuple)):
                pending.extend((child, depth + 1, field) for child in current[:64])
            elif isinstance(current, str) and current.startswith("/"):
                normalized = field.replace("_", "").casefold()
                if any(
                    marker in normalized
                    for marker in ("node", "rootpath", "created", "changed")
                ):
                    if current not in nodes:
                        nodes.append(current)
                    if "root" in normalized and root_path is None:
                        root_path = current
        return nodes, root_path

    def _record_diagnostic_tool(
        self,
        item_id: str,
        item: dict[str, Any],
        status: str,
    ) -> None:
        if not self._diagnostic_snapshot:
            return
        tool_name = item.get("tool")
        tool = tool_name if isinstance(tool_name, str) and tool_name else "MCP"
        error = item.get("error")
        state = self._diagnostic_tool_states.setdefault(
            item_id,
            {"tool": tool, "status": status, "error": "", "recovered": False},
        )
        state["tool"] = tool
        state["status"] = status
        if error is not None:
            state["error"] = self._diagnostic_error_text(error)
            state["error_code"] = self._diagnostic_error_code(
                error,
                "MCP_TOOL_FAILED",
            )
        if status == "completed":
            for previous in self._diagnostic_tool_states.values():
                if (
                    previous is not state
                    and previous.get("tool") == tool
                    and previous.get("status") == "failed"
                ):
                    previous["recovered"] = True
        nodes, root_path = self._diagnostic_node_paths(item)
        existing_nodes = list(self._diagnostic_snapshot.get("nodes") or [])
        for node in nodes:
            if node not in existing_nodes:
                existing_nodes.append(node)
        self._diagnostic_snapshot["nodes"] = existing_nodes
        if root_path:
            self._diagnostic_snapshot["root_path"] = root_path
        self._diagnostic_snapshot["tool_order"] = [
            f"{index}. {entry.get('tool', 'MCP')} [{entry.get('status', 'unknown')}]"
            for index, entry in enumerate(
                self._diagnostic_tool_states.values(),
                start=1,
            )
        ]

    def _remember_codex_error(self, method: str, params: dict[str, Any]) -> None:
        if not self._diagnostic_snapshot:
            return
        text = self._diagnostic_error_text(params)
        if method in {"warning", "guardianWarning", "configWarning"}:
            if text:
                warnings = list(self._diagnostic_snapshot.get("_warnings") or [])
                if text not in warnings:
                    warnings.append(text)
                self._diagnostic_snapshot["_warnings"] = warnings
            return
        if method != "error":
            return
        self._diagnostic_event_errors.append(
            {
                "tool": "Codex app-server",
                "error_code": self._diagnostic_error_code(
                    params,
                    "CODEX_NOTIFICATION_ERROR",
                ),
                "error_text": text or "Codex reported an error",
                "traceback": text if "traceback" in text.casefold() else "",
            }
        )

    def _refresh_diagnostic_scene_result(self) -> None:
        if not self._diagnostic_snapshot:
            return
        initial_revision = self._diagnostic_snapshot.get(
            "_initial_scene_revision",
            self._diagnostic_snapshot.get("scene_revision"),
        )
        initial_dirty = self._diagnostic_snapshot.get(
            "_initial_dirty",
            self._diagnostic_snapshot.get("dirty"),
        )
        current = self._diagnostic_scene_fields()
        self._diagnostic_snapshot.update(current)
        current_revision = current.get("scene_revision")
        if isinstance(initial_revision, int) and isinstance(current_revision, int):
            self._diagnostic_snapshot["scene_modified"] = (
                current_revision != initial_revision
            )
        elif initial_dirty is False and current.get("dirty") is True:
            self._diagnostic_snapshot["scene_modified"] = True
        else:
            self._diagnostic_snapshot["scene_modified"] = "待确认"

    def _finalize_diagnostic_turn(self, status: str | None) -> None:
        if not self._diagnostic_snapshot:
            return
        self._refresh_diagnostic_scene_result()
        terminal_status = status or "completed"
        unresolved = [
            state
            for state in self._diagnostic_tool_states.values()
            if state.get("status") == "failed" and state.get("recovered") is not True
        ]
        errors = unresolved + list(self._diagnostic_event_errors)
        self._diagnostic_snapshot["status"] = terminal_status
        warnings = list(self._diagnostic_snapshot.get("_warnings") or [])
        if warnings:
            self._diagnostic_snapshot["warnings"] = warnings
        recovered_count = sum(
            state.get("recovered") is True
            for state in self._diagnostic_tool_states.values()
        )
        self._diagnostic_snapshot["retries"] = recovered_count
        if recovered_count:
            self._diagnostic_snapshot["recovery"] = (
                f"{recovered_count} 个失败工具调用随后由同名工具成功恢复"
            )
        if terminal_status != "failed" and not errors:
            self._diagnostic_snapshot["actual"] = "Turn 正常完成"
            return

        first = errors[0] if errors else {}
        error_texts = [str(error.get("error", "")) for error in unresolved]
        error_texts.extend(
            str(error.get("error_text", "")) for error in self._diagnostic_event_errors
        )
        error_text = " | ".join(dict.fromkeys(text for text in error_texts if text))
        error_code = str(
            first.get("error_code")
            or ("TURN_FAILED" if terminal_status == "failed" else "FINAL_TOOL_FAILURE")
        )
        traceback_text = next(
            (
                text
                for text in error_texts
                if "traceback" in text.casefold() or "\n  file " in text.casefold()
            ),
            "",
        )
        self._diagnostic_snapshot.update(
            {
                "actual": "Turn 失败" if terminal_status == "failed" else "Turn 完成但仍有最终工具失败",
                "stage": "Turn 最终状态",
                "error_code": error_code,
                "error_text": error_text or "Codex Turn reported failure",
                "traceback": traceback_text or "未提供",
                "impact": "当前请求可能未完成或场景结果需要人工确认",
                "next_step": "把此报告交给 Codex，并检查当前场景中的真实节点与错误",
            }
        )
        self._write_runtime_diagnostic(
            {
                "status": terminal_status,
                "stage": "Turn 最终状态",
                "error_code": error_code,
                "error_text": self._diagnostic_snapshot["error_text"],
                "traceback": traceback_text,
                "retries": recovered_count,
                "recovery": self._diagnostic_snapshot.get("recovery"),
                "impact": self._diagnostic_snapshot["impact"],
                "next_step": self._diagnostic_snapshot["next_step"],
            },
            slug="turn-failure",
        )

    def _record_pre_turn_issue(
        self,
        stage: str,
        error_code: str,
        error_text: str,
        *,
        attachment: str | None = None,
    ) -> None:
        thread_id = self._selected_thread_id
        if self._diagnostic_draft_key is None:
            self._diagnostic_draft_key = (
                f"{thread_id or 'no-thread'}:draft:{uuid.uuid4().hex}"
            )
            self._diagnostic_snapshot = {}
        self._diagnostic_turn_key = self._diagnostic_draft_key
        if not self._diagnostic_snapshot:
            composer = getattr(self, "input_edit", None)
            goal = composer.toPlainText() if composer is not None else ""
            attachments = self._attachment_paths()
            if attachment:
                attachments = (*attachments, attachment)
            self._diagnostic_snapshot = self._new_diagnostic_snapshot(
                thread_id=thread_id,
                user_goal=goal or stage,
                attachment_paths=attachments,
            )
        self._diagnostic_snapshot.update(
            {
                "status": "failed",
                "stage": stage,
                "actual": error_text,
                "error_code": error_code,
                "error_text": error_text,
                "impact": "本次输入上下文未能完整准备",
                "next_step": "检查原始文件或当前 Houdini 选择后重试",
            }
        )
        self._write_runtime_diagnostic(
            {
                "status": "failed",
                "stage": stage,
                "error_code": error_code,
                "error_text": error_text,
                "impact": self._diagnostic_snapshot["impact"],
                "next_step": self._diagnostic_snapshot["next_step"],
            },
            slug="input-failure",
        )

    def _record_final_runtime_failure(
        self,
        stage: str,
        error_code: str,
        error_text: str,
        *,
        slug: str,
        traceback_text: str = "",
        recovery: str = "未恢复",
        impact: str = "当前请求未能可靠完成",
        next_step: str = "检查当前场景与真实错误后再决定是否重试",
    ) -> None:
        if not isinstance(self._diagnostic_turn_key, str):
            thread_id = self._selected_thread_id
            self._diagnostic_turn_key = (
                f"{thread_id or 'no-thread'}:runtime:{uuid.uuid4().hex}"
            )
            composer = getattr(self, "input_edit", None)
            goal = composer.toPlainText() if composer is not None else ""
            self._diagnostic_snapshot = self._new_diagnostic_snapshot(
                thread_id=thread_id,
                user_goal=goal or stage,
                attachment_paths=self._attachment_paths(),
            )
        self._refresh_diagnostic_scene_result()
        self._diagnostic_snapshot.update(
            {
                "status": "failed",
                "stage": stage,
                "actual": error_text,
                "error_code": error_code,
                "error_text": error_text,
                "traceback": traceback_text or "未提供",
                "recovery": recovery,
                "impact": impact,
                "next_step": next_step,
            }
        )
        self._write_runtime_diagnostic(
            {
                "status": "failed",
                "stage": stage,
                "error_code": error_code,
                "error_text": error_text,
                "traceback": traceback_text,
                "recovery": recovery,
                "impact": impact,
                "next_step": next_step,
            },
            slug=slug,
        )

    def _write_runtime_diagnostic(
        self,
        occurrence: dict[str, Any],
        *,
        slug: str,
    ) -> str | None:
        warnings = list(self._diagnostic_snapshot.get("_warnings") or [])
        if warnings:
            self._diagnostic_snapshot["warnings"] = warnings
        writer = getattr(self, "_diagnostic_writer", None)
        turn_key = self._diagnostic_turn_key
        if writer is None or not isinstance(turn_key, str):
            reason = self._diagnostic_writer_error or "运行时诊断写入器不可用"
            self._append_system(f"问题报告保存失败：{reason}")
            return None
        try:
            path = writer.record(
                turn_key,
                snapshot=dict(self._diagnostic_snapshot),
                occurrence=occurrence,
                slug=slug,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._append_system(f"问题报告保存失败：{type(exc).__name__}: {exc}")
            return None
        self._last_report_path = path
        button = getattr(self, "copy_report_path_button", None)
        if button is not None:
            button.setVisible(True)
        self._append_system(f"问题报告已保存：{path}")
        return path

    def _record_manual_issue(self) -> None:
        if not isinstance(self._diagnostic_turn_key, str):
            thread_id = self._selected_thread_id
            self._diagnostic_turn_key = (
                f"{thread_id or 'no-thread'}:manual:{uuid.uuid4().hex}"
            )
            composer = getattr(self, "input_edit", None)
            goal = composer.toPlainText() if composer is not None else ""
            self._diagnostic_snapshot = self._new_diagnostic_snapshot(
                thread_id=thread_id,
                user_goal=goal or "用户手动记录本次问题",
                attachment_paths=self._attachment_paths(),
            )
        self._refresh_diagnostic_scene_result()
        self._diagnostic_snapshot.update(
            {
                "status": "用户记录",
                "stage": "主观质量反馈",
                "actual": "用户认为本次结果需要进一步检查",
                "manual_check": "用户已手动标记；具体质量判断由用户与 Codex 后续确认",
                "impact": "结果质量或完成度未达到用户预期",
                "next_step": "把此报告交给 Codex，并说明期望与实际差异",
            }
        )
        self._write_runtime_diagnostic(
            {
                "status": "用户记录",
                "stage": "主观质量反馈",
                "manual": True,
                "error_text": "用户手动标记本次结果需要复查",
                "impact": self._diagnostic_snapshot["impact"],
                "next_step": self._diagnostic_snapshot["next_step"],
            },
            slug="user-report",
        )

    def _copy_report_path(self) -> None:
        path = self._last_report_path
        if not isinstance(path, str) or not path:
            return
        try:
            QtWidgets.QApplication.clipboard().setText(path)
        except Exception as exc:
            self._append_system(f"复制报告路径失败：{type(exc).__name__}: {exc}")
            return
        self._append_system("问题报告路径已复制。")

    def _selected_history_record(self) -> dict[str, Any] | None:
        combo = getattr(self, "history_combo", None)
        record = combo.currentData() if combo is not None else None
        return record if isinstance(record, dict) else None

    def _history_title(self, thread_id: str, *, full: bool = False) -> str:
        for record in self._thread_history:
            if record.get("thread_id") != thread_id:
                continue
            for field in ("name", "preview"):
                value = record.get(field)
                if isinstance(value, str) and value.strip():
                    title = " ".join(value.split())
                    if full:
                        return title
                    return title if len(title) <= 24 else title[:23] + "…"
        return (
            f"{thread_id[:4]}…{thread_id[-4:]}"
            if len(thread_id) > 9
            else thread_id
        )

    @staticmethod
    def _history_label(record: dict[str, Any]) -> str:
        thread_id = str(record.get("thread_id") or "")
        title = ""
        for field in ("name", "preview"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                title = " ".join(value.split())[:72]
                break
        if not title:
            title = (
                f"{thread_id[:4]}…{thread_id[-4:]}"
                if len(thread_id) > 9
                else thread_id or "未命名会话"
            )
        updated = record.get("updated_at")
        try:
            updated_text = datetime.fromtimestamp(int(updated)).strftime(
                "%Y-%m-%d %H:%M"
            )
        except (OSError, OverflowError, TypeError, ValueError):
            updated_text = "时间未知"
        return f"{title}  ·  {updated_text}"

    def _apply_threads(self, raw_threads: Any) -> None:
        previous_record = self._selected_history_record()
        previous_choice = (
            previous_record.get("thread_id")
            if previous_record is not None
            else self._selected_thread_id
        )
        threads = raw_threads if isinstance(raw_threads, list) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_record in threads[:20]:
            if not isinstance(raw_record, dict):
                continue
            thread_id = raw_record.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id or thread_id in seen:
                continue
            seen.add(thread_id)
            records.append(dict(raw_record))
        self._thread_history = records

        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        selected_index = 0
        self.history_combo.addItem(
            "未选择历史会话" if records else "暂无历史会话",
            None,
        )
        if records:
            for record in records:
                self.history_combo.addItem(self._history_label(record), record)
                index = self.history_combo.count() - 1
                thread_id = record["thread_id"]
                self.history_combo.setItemData(
                    index,
                    f"{self._history_title(thread_id, full=True)}\n"
                    f"Codex Thread ID：{thread_id}",
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
                if thread_id == previous_choice:
                    selected_index = index
        self.history_combo.setCurrentIndex(selected_index)
        self.history_combo.blockSignals(False)
        self._on_history_index_changed(selected_index)

    def _on_history_index_changed(self, _index: int = -1) -> None:
        record = self._selected_history_record()
        thread_id = record.get("thread_id") if record is not None else None
        name = record.get("name") if record is not None else None
        self.thread_id_edit.setText(thread_id if isinstance(thread_id, str) else "")
        self.thread_name_edit.setText(name if isinstance(name, str) else "")
        self._refresh_controls()

    def _refresh_threads(self) -> None:
        if self._client is None or not self._connected:
            return
        self._threads_requested = True
        self._client.get_threads()

    def _request_thread_resume(self, thread_id: str, *, context: str) -> None:
        if (
            self._client is not None
            and not self._turn_state.busy
            and self._goal_action_context is None
            and not self._session_action_pending
            and not self._turn_start_request_pending
            and not self._reconciliation_tokens
        ):
            self._session_action_pending = True
            self._refresh_controls()
            self._client.resume_thread(
                thread_id,
                service_tier=self._selected_service_tier(),
                context=context,
            )

    def _rename_thread(self) -> None:
        record = self._selected_history_record()
        name = self.thread_name_edit.text().strip()
        thread_id = record.get("thread_id") if record is not None else None
        if not isinstance(thread_id, str) or not name:
            self._append_system("请选择历史会话并输入名称。")
            return
        if self._client is None or self._session_action_pending:
            return
        self._session_action_pending = True
        self._refresh_controls()
        self._client.rename_thread(
            thread_id,
            name,
            context=f"{_THREAD_RENAME_CONTEXT_PREFIX}{uuid.uuid4().hex}",
        )

    def _copy_thread_id(self) -> None:
        record = self._selected_history_record()
        thread_id = record.get("thread_id") if record is not None else None
        if not isinstance(thread_id, str):
            return
        try:
            QtWidgets.QApplication.clipboard().setText(thread_id)
        except Exception as exc:
            self._append_system(f"复制 Thread ID 失败：{type(exc).__name__}: {exc}")
            return
        self._append_system("Thread ID 已复制。")

    def _update_history_name(self, thread_id: Any, name: Any) -> None:
        if not isinstance(thread_id, str):
            return
        normalized_name = name if isinstance(name, str) else None
        changed = False
        for record in self._thread_history:
            if record.get("thread_id") == thread_id:
                record["name"] = normalized_name
                changed = True
        if changed:
            self._apply_threads(self._thread_history)
        if thread_id == self._selected_thread_id:
            self.thread_status_label.setText(
                f"Thread：{self._history_title(thread_id)}"
            )
            self.thread_status_label.setToolTip(
                f"{self._history_title(thread_id, full=True)}\n"
                f"Codex Thread ID：{thread_id}"
            )

    def _discard_crash_recovery_candidate(self) -> None:
        self._crash_recovery_marker = None
        self._crash_recovery_health_session = None
        self._crash_recovery_goal_payload = None
        self._crash_recovery_thread_payload = None

    def _maybe_request_crash_recovery_goal(
        self,
        session: dict[str, Any],
    ) -> bool:
        marker = self._crash_recovery_marker
        if not isinstance(marker, dict):
            return False
        if isinstance(self._selected_thread_id, str):
            self._discard_crash_recovery_candidate()
            return False
        if not self._connected or session.get("authentication") != "authenticated":
            return False
        if (
            session.get("thread_id") != marker["thread_id"]
            or session.get("focus_mode") is not True
        ):
            self._discard_crash_recovery_candidate()
            return False
        if (
            self._client is None
            or self._goal_action_context is not None
            or self._crash_recovery_goal_payload is not None
        ):
            return False
        self._crash_recovery_health_session = dict(session)
        self._goal_action_context = _GOAL_GET_CONTEXT
        self._refresh_controls()
        self._client.get_goal(marker["thread_id"])
        return True

    def _crash_recovery_goal_matches(self, payload: dict[str, Any]) -> bool:
        marker = self._crash_recovery_marker
        goal = payload.get("goal")
        return bool(
            isinstance(marker, dict)
            and isinstance(goal, dict)
            and payload.get("thread_id") == marker["thread_id"]
            and payload.get("focus_mode") is True
            and payload.get("goal_binding") == marker["goal_binding"]
            and goal.get("threadId") == marker["thread_id"]
            and goal.get("status") == "active"
        )

    @staticmethod
    def _thread_read_has_recovery_prompt(
        payload: dict[str, Any],
        prompt_id: str,
    ) -> bool:
        raw_result = payload.get("read", payload.get("result"))
        thread = raw_result.get("thread") if isinstance(raw_result, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        prefix = f"[HIA launcher recovery {prompt_id}]"
        for turn in turns if isinstance(turns, list) else ():
            items = turn.get("items") if isinstance(turn, dict) else None
            for item in items if isinstance(items, list) else ():
                if not isinstance(item, dict) or item.get("type") != "userMessage":
                    continue
                content = item.get("content")
                for entry in content if isinstance(content, list) else ():
                    if (
                        isinstance(entry, dict)
                        and entry.get("type") == "text"
                        and isinstance(entry.get("text"), str)
                        and entry["text"].startswith(prefix)
                    ):
                        return True
        return False

    def _complete_crash_recovery_bind(self, payload: dict[str, Any]) -> bool:
        marker = self._crash_recovery_marker
        goal_payload = self._crash_recovery_goal_payload
        initial_session = self._crash_recovery_health_session
        raw_result = payload.get("read", payload.get("result"))
        thread = raw_result.get("thread") if isinstance(raw_result, dict) else None
        if (
            not isinstance(marker, dict)
            or not isinstance(goal_payload, dict)
            or not isinstance(initial_session, dict)
            or not isinstance(thread, dict)
            or thread.get("id") != marker["thread_id"]
            or not self._crash_recovery_goal_matches(goal_payload)
            or self._selected_thread_id is not None
        ):
            self._discard_crash_recovery_candidate()
            return False

        thread_id = marker["thread_id"]
        prompt_seen = self._thread_read_has_recovery_prompt(
            payload,
            marker["prompt_id"],
        )
        self._selected_thread_id = thread_id
        self._clear_goal_display()
        self._team_records.clear()
        self._refresh_team_combo()
        self._clear_turn_performance()
        self.thread_id_edit.setText(thread_id)
        for index in range(self.history_combo.count()):
            record = self.history_combo.itemData(index)
            if isinstance(record, dict) and record.get("thread_id") == thread_id:
                self.history_combo.setCurrentIndex(index)
                self._on_history_index_changed(index)
                break
        self.thread_status_label.setText(
            f"Thread：{self._history_title(thread_id)}"
        )
        self.thread_status_label.setToolTip(
            f"{self._history_title(thread_id, full=True)}\n"
            f"Codex Thread ID：{thread_id}"
        )
        if not self._render_thread_read(
            payload,
            allow_active=True,
            hidden_user_prefix=(
                f"[HIA launcher recovery {marker['prompt_id']}]"
            ),
        ):
            self._selected_thread_id = None
            self._discard_crash_recovery_candidate()
            return False
        self._apply_goal(thread_id, goal_payload.get("goal"))
        self._apply_focus_mode(thread_id, True)
        self._crash_recovery_observation = {
            "thread_id": thread_id,
            "prompt_id": marker["prompt_id"],
            "prompt_seen": prompt_seen,
            "initial_turn_id": initial_session.get("turn_id"),
            "reread_requested": False,
            "terminal_turn_id": None,
            "terminal_status": None,
        }
        self._discard_crash_recovery_candidate()
        if self._client is not None:
            self._client.get_session()
        self._refresh_controls()
        return True

    def _reconcile_crash_recovery_session(
        self,
        session: dict[str, Any],
    ) -> None:
        observation = self._crash_recovery_observation
        if not isinstance(observation, dict):
            return
        thread_id = observation.get("thread_id")
        if (
            session.get("thread_id") != thread_id
            or session.get("focus_mode") is not True
            or session.get("connected") is not True
        ):
            self._crash_recovery_observation = None
            return
        if session.get("turn_active") is not False:
            return
        turn_id = session.get("turn_id")
        status = session.get("turn_status")
        if not isinstance(turn_id, str):
            return
        if observation.get("prompt_seen") is True:
            self._crash_recovery_observation = None
            self._queue_goal_continuation(thread_id, turn_id, status)
            return
        if (
            turn_id == observation.get("initial_turn_id")
            or observation.get("reread_requested") is True
            or self._client is None
        ):
            return
        observation["reread_requested"] = True
        observation["terminal_turn_id"] = turn_id
        observation["terminal_status"] = status
        self._client.read_thread(
            thread_id,
            context=_CRASH_RECOVERY_RECHECK_CONTEXT,
        )

    def _complete_crash_recovery_recheck(self, payload: dict[str, Any]) -> None:
        observation = self._crash_recovery_observation
        if not isinstance(observation, dict):
            return
        thread_id = observation.get("thread_id")
        raw_result = payload.get("read", payload.get("result"))
        thread = raw_result.get("thread") if isinstance(raw_result, dict) else None
        marker_prompt = observation.get("prompt_id")
        valid = (
            isinstance(thread, dict)
            and thread.get("id") == thread_id
            and isinstance(marker_prompt, str)
            and bool(marker_prompt)
            and self._thread_read_has_recovery_prompt(payload, marker_prompt)
        )
        turn_id = observation.get("terminal_turn_id")
        status = observation.get("terminal_status")
        self._crash_recovery_observation = None
        if valid and isinstance(turn_id, str) and not self._turn_state.busy:
            self._queue_goal_continuation(thread_id, turn_id, status)
            self._maybe_start_goal_continuation()

    def _request_goal(self) -> None:
        thread_id = self._selected_thread_id
        if (
            self._client is None
            or not self._connected
            or self._session_action_pending
            or self._goal_action_context is not None
            or not isinstance(thread_id, str)
        ):
            return
        self._goal_action_context = _GOAL_GET_CONTEXT
        self._refresh_controls()
        self._client.get_goal(thread_id)

    def _clear_goal_display(self, text: str = "尚未读取 Goal") -> None:
        self._current_goal = None
        self._goal_turn_id = None
        self._goal_turn_has_text = False
        self._goal_continuation_paused = False
        self._goal_continuation_boundary = None
        self._goal_auto_turn_token = None
        self._goal_auto_turn_has_progress = False
        self._goal_continue_after_open_thread_id = None
        objective = getattr(self, "goal_objective_edit", None)
        if objective is not None:
            objective.setPlainText("")
        budget = getattr(self, "goal_budget_edit", None)
        if budget is not None:
            budget.setText("")
        metrics = getattr(self, "goal_metrics_label", None)
        if metrics is not None:
            metrics.setText(text)
        status = getattr(self, "goal_status_label", None)
        if status is not None:
            status.setText("状态：未设置")
        activity = getattr(self, "goal_activity_label", None)
        if activity is not None:
            activity.setText("当前跟进：等待下一轮任务进展")
        save_button = getattr(self, "goal_save_button", None)
        if save_button is not None:
            save_button.setText("保存（继续跟进）")

    def _apply_focus_mode(self, thread_id: Any, enabled: Any) -> bool:
        if thread_id != self._selected_thread_id or not isinstance(enabled, bool):
            return False
        self._focus_mode = enabled
        if not enabled:
            self._goal_continuation_boundary = None
            self._goal_auto_turn_token = None
            self._goal_auto_turn_has_progress = False
        checkbox = getattr(self, "goal_focus_checkbox", None)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        hint = getattr(self, "goal_focus_hint_label", None)
        if hint is not None:
            if enabled and self._goal_continuation_paused:
                hint.setText(
                    "已暂停自动续做；点击“保存（继续跟进）”或重新开启专注模式后恢复。"
                )
            else:
                hint.setText(
                    "已开启：Houdini 异常退出后会尝试恢复，并继续当前 Goal。"
                    if enabled
                    else "已关闭：普通聊天，不自动恢复或续做。"
                )
        return True

    def _pause_goal_continuation(self, message: str, *, notify: bool) -> None:
        already_paused = self._goal_continuation_paused
        self._goal_continuation_paused = True
        self._goal_continuation_boundary = None
        self._goal_auto_turn_token = None
        self._goal_auto_turn_has_progress = False
        if isinstance(self._current_goal, dict):
            self.goal_activity_label.setText(
                "当前跟进：已暂停；点击“保存（继续跟进）”后恢复"
            )
        if self._focus_mode:
            self.goal_focus_hint_label.setText(
                "已暂停自动续做；点击“保存（继续跟进）”或重新开启专注模式后恢复。"
            )
        if notify and not already_paused:
            self._append_system(message)

    def _goal_auto_turn_is_current(self, thread_id: Any) -> bool:
        token = self._goal_auto_turn_token
        return (
            isinstance(token, TurnStateToken)
            and token.thread_id == thread_id
            and self._turn_state.token_generation_is_current(token)
        )

    def _mark_goal_auto_turn_progress(self) -> None:
        token = self._goal_auto_turn_token
        if (
            isinstance(token, TurnStateToken)
            and self._turn_state.token_generation_is_current(token)
        ):
            self._goal_auto_turn_has_progress = True

    def _queue_goal_continuation(
        self,
        thread_id: Any,
        turn_id: Any,
        status: Any,
        *,
        auto_turn: bool = False,
    ) -> None:
        observation = self._crash_recovery_observation
        if (
            isinstance(observation, dict)
            and observation.get("thread_id") == thread_id
            and (
                observation.get("prompt_seen") is True
                or turn_id != observation.get("initial_turn_id")
            )
        ):
            self._crash_recovery_observation = None
        was_auto_turn = auto_turn or self._goal_auto_turn_is_current(thread_id)
        auto_turn_had_progress = self._goal_auto_turn_has_progress
        if was_auto_turn:
            self._goal_auto_turn_token = None
            self._goal_auto_turn_has_progress = False
            if status != "completed":
                self._pause_goal_continuation(
                    "目标专注模式已暂停：自动续轮未正常完成。",
                    notify=True,
                )
                return
        if (
            status != "completed"
            or thread_id != self._selected_thread_id
            or not isinstance(thread_id, str)
            or not isinstance(turn_id, str)
        ):
            return
        if was_auto_turn and not auto_turn_had_progress:
            self._pause_goal_continuation(
                "目标专注模式已暂停：上一轮没有返回文字或工具活动，未继续创建空 Turn。",
                notify=True,
            )
            return
        if (
            not self._focus_mode
            or not isinstance(self._current_goal, dict)
            or self._current_goal.get("status") != "active"
        ):
            return
        self._goal_continuation_boundary = (thread_id, turn_id)

    def _goal_continuation_is_safe(self) -> bool:
        return bool(
            self._client is not None
            and self._connected
            and self._authenticated
            and isinstance(self._selected_thread_id, str)
            and isinstance(self._current_goal, dict)
            and self._current_goal.get("status") == "active"
            and self._focus_mode
            and not self._goal_continuation_paused
            and not self._turn_state.busy
            and self._goal_turn_id is None
            and not self._session_action_pending
            and not self._turn_start_request_pending
            and not self._turn_steer_request_pending
            and not self._reconciliation_tokens
            and not self._interrupt_pending
            and not self._is_stopping_turn()
            and self._stop_recovery_state is None
            and self._goal_action_context is None
            and self._current_approval is None
            and not self._pending_approvals
            and not self._scene_capability_pending
            and not self._scene_work_pending
        )

    def _maybe_start_goal_continuation(
        self,
        *,
        explicit_source: str | None = None,
    ) -> bool:
        thread_id = self._selected_thread_id
        if explicit_source is not None and isinstance(thread_id, str):
            self._goal_continuation_paused = False
            if self._goal_continuation_boundary is None:
                self._goal_continuation_boundary = (
                    thread_id, f"explicit:{explicit_source}"
                )
        boundary = self._goal_continuation_boundary
        if (
            not isinstance(boundary, tuple)
            or len(boundary) != 2
            or not self._goal_continuation_is_safe()
        ):
            return False
        if not self._start_new_turn(
            _GOAL_CONTINUE_INSTRUCTION,
            (),
            boundary[0],
            request_text=_GOAL_CONTINUE_INSTRUCTION,
            goal_auto_continue=True,
        ):
            return False
        self.goal_activity_label.setText(_GOAL_RUNNING_NO_TEXT)
        return True

    def _set_focus_mode(self, enabled: bool) -> None:
        thread_id = self._selected_thread_id
        if (
            self._client is None
            or self._session_action_pending
            or self._goal_action_context is not None
            or not isinstance(thread_id, str)
        ):
            return
        goal = self._current_goal
        if enabled and (
            not isinstance(goal, dict) or goal.get("status") != "active"
        ):
            self.goal_focus_checkbox.blockSignals(True)
            self.goal_focus_checkbox.setChecked(False)
            self.goal_focus_checkbox.blockSignals(False)
            self.goal_focus_hint_label.setText(
                "请先填写并保存 Goal，再开启目标专注模式。"
            )
            return
        self._goal_action_context = _FOCUS_SET_CONTEXT
        self.goal_focus_hint_label.setText("正在更新目标专注模式…")
        self._refresh_controls()
        self._client.set_focus_mode(thread_id, enabled)

    def _save_goal(self) -> None:
        thread_id = self._selected_thread_id
        if (
            self._client is None
            or self._session_action_pending
            or self._goal_action_context is not None
            or not isinstance(thread_id, str)
        ):
            return
        objective = self.goal_objective_edit.toPlainText().strip()
        if not objective:
            self._append_system("Goal 目标不能为空；如需移除请使用“清除”。")
            return
        if len(objective) > _GOAL_OBJECTIVE_MAX_LENGTH:
            self._append_system(
                f"Goal 目标不能超过 {_GOAL_OBJECTIVE_MAX_LENGTH} 个字符。"
            )
            return
        budget_text = self.goal_budget_edit.text().strip()
        token_budget: int | None = None
        if budget_text:
            try:
                token_budget = int(budget_text)
            except ValueError:
                self._append_system("Goal Token 预算必须是正整数。")
                return
            if token_budget <= 0:
                self._append_system("Goal Token 预算必须是正整数。")
                return
        self._goal_action_context = _GOAL_SET_CONTEXT
        self.goal_activity_label.setText(_GOAL_RUNNING_NO_TEXT)
        self._refresh_controls()
        self._client.set_goal(
            objective,
            "active",
            thread_id=thread_id,
            token_budget=token_budget,
        )

    def _clear_goal(self) -> None:
        thread_id = self._selected_thread_id
        if (
            self._client is None
            or self._session_action_pending
            or self._goal_action_context is not None
            or not isinstance(thread_id, str)
        ):
            return
        self._goal_action_context = _GOAL_CLEAR_CONTEXT
        self._refresh_controls()
        self._client.clear_goal(thread_id)

    def _apply_goal(self, thread_id: Any, raw_goal: Any) -> bool:
        if thread_id != self._selected_thread_id:
            return False
        if raw_goal is None:
            visible_goal_turn_id = self._goal_turn_id
            if (
                isinstance(visible_goal_turn_id, str)
                and not self._turn_state.busy
                and self._stream_thread_id == thread_id
                and self._stream_turn_id == visible_goal_turn_id
            ):
                self._finish_codex_message()
                self._stream_thread_id = None
                self._stream_turn_id = None
            self._current_goal = None
            self._goal_turn_id = None
            self._goal_turn_has_text = False
            self._clear_goal_display("当前 Thread 未设置 Goal")
            self._apply_focus_mode(thread_id, False)
            return True
        if not isinstance(raw_goal, dict):
            return False
        goal_thread_id = raw_goal.get("threadId")
        objective = raw_goal.get("objective")
        status = raw_goal.get("status")
        if (
            goal_thread_id != thread_id
            or not isinstance(objective, str)
            or not isinstance(status, str)
        ):
            return False
        status_label = _GOAL_STATUS_LABELS.get(status)
        if status_label is None:
            return False
        previous_goal = self._current_goal
        self.goal_objective_edit.setPlainText(objective)
        self._current_goal = dict(raw_goal)
        raw_goal_turn_id = raw_goal.get("turnId")
        completed_goal_boundary = (
            self._goal_continuation_boundary == (thread_id, raw_goal_turn_id)
        )
        if (
            status == "active"
            and isinstance(raw_goal_turn_id, str)
            and not completed_goal_boundary
        ):
            if raw_goal_turn_id != self._goal_turn_id:
                self._goal_turn_has_text = False
            self._goal_turn_id = raw_goal_turn_id
        elif status != "active":
            visible_goal_turn_id = self._goal_turn_id
            if (
                isinstance(visible_goal_turn_id, str)
                and not self._turn_state.busy
                and self._stream_thread_id == thread_id
                and self._stream_turn_id == visible_goal_turn_id
            ):
                self._finish_codex_message()
                self._stream_thread_id = None
                self._stream_turn_id = None
            self._goal_turn_id = None
            self._goal_turn_has_text = False
        status_text = f"状态：{status_label}"
        if status in {"blocked", "paused", "usageLimited", "budgetLimited"}:
            reason = self._notice_text(raw_goal)
            if not reason:
                reason = {
                    "paused": "Goal 已暂停",
                    "usageLimited": "用量受限",
                    "budgetLimited": "预算受限",
                }.get(status, "未提供原因")
            status_text += f" · 原因：{self._bounded_goal_summary(reason, 160)}"
        self.goal_status_label.setText(status_text)
        self.goal_save_button.setText(
            "继续跟进" if status == "blocked" else "保存（继续跟进）"
        )
        if (
            not isinstance(previous_goal, dict)
            or previous_goal.get("objective") != objective
            or previous_goal.get("status") != status
        ):
            if status == "complete":
                activity_text = "当前跟进：Goal 已完成"
            elif status == "blocked":
                activity_text = (
                    "当前跟进：等待你完成上轮要求，完成后点继续跟进"
                )
            else:
                activity_text = self._goal_waiting_activity_text()
            self.goal_activity_label.setText(activity_text)
        token_budget = raw_goal.get("tokenBudget")
        self.goal_budget_edit.setText(
            str(token_budget) if isinstance(token_budget, int) else ""
        )
        tokens_used = raw_goal.get("tokensUsed")
        time_used = raw_goal.get("timeUsedSeconds")
        metrics = []
        if isinstance(tokens_used, int):
            metrics.append(f"已用 {tokens_used:,} tokens")
        if isinstance(time_used, int):
            metrics.append(f"已用 {time_used}s")
        self.goal_metrics_label.setText(" · ".join(metrics) or "Codex 原生 Goal")
        if status != "active":
            self._goal_continuation_boundary = None
            self._goal_auto_turn_token = None
            self._goal_auto_turn_has_progress = False
            self._apply_focus_mode(thread_id, False)
        return True

    def _goal_turn_matches(self, thread_id: Any, turn_id: Any) -> bool:
        return (
            isinstance(self._current_goal, dict)
            and self._current_goal.get("status") == "active"
            and thread_id == self._selected_thread_id
            and isinstance(turn_id, str)
            and turn_id == self._goal_turn_id
        )

    def _can_bind_goal_turn(self, thread_id: Any, turn_id: Any) -> bool:
        goal_set_pending = self._goal_action_context == _GOAL_SET_CONTEXT
        active_goal = (
            isinstance(self._current_goal, dict)
            and self._current_goal.get("status") == "active"
        )
        return (
            thread_id == self._selected_thread_id
            and isinstance(turn_id, str)
            and self._goal_turn_id in {None, turn_id}
            and not self._turn_state.busy
            and not self._turn_start_request_pending
            and not self._is_stopping_turn()
            and (goal_set_pending or active_goal)
        )

    def _goal_waiting_activity_text(self) -> str:
        status = (
            self._current_goal.get("status")
            if isinstance(self._current_goal, dict)
            else None
        )
        if status == "active" and self._stop_recovery_state == "recovering":
            return "当前跟进：Codex 正在恢复，Goal 已暂停"
        if status == "active" and (
            self._stop_recovery_state == "failed" or not self._connected
        ):
            return "当前跟进：已暂停，等待重连"
        if status == "active" and self._goal_continuation_paused:
            return "当前跟进：已暂停；点击“保存（继续跟进）”后恢复"
        if status == "blocked":
            return "当前跟进：等待你完成上轮要求，完成后点继续跟进"
        if status == "complete":
            return "当前跟进：Goal 已完成"
        if (
            isinstance(self._goal_turn_id, str)
            or self._goal_action_context == _GOAL_SET_CONTEXT
            or (
                isinstance(self._goal_auto_turn_token, TurnStateToken)
                and self._turn_state.busy
            )
        ):
            return (
                _GOAL_RUNNING_WITH_TEXT
                if self._goal_turn_has_text
                else _GOAL_RUNNING_NO_TEXT
            )
        return "当前跟进：等待下一轮任务进展"

    @staticmethod
    def _bounded_team_text(value: Any, limit: int = _TEAM_TEXT_LIMIT) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = "".join(
            " " if ord(character) < 32 and character not in "\n\t" else character
            for character in value
        ).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + "\n[…内容过长，已截断显示…]"

    def _team_source_is_current(self, thread_id: Any) -> bool:
        root_thread_id = self._selected_thread_id
        if not isinstance(root_thread_id, str) or not root_thread_id:
            return False
        if thread_id == root_thread_id:
            return True
        record = self._team_records.get(thread_id)
        return (
            isinstance(record, dict)
            and record.get("root_thread_id") == root_thread_id
        )

    def _team_record(
        self,
        thread_id: Any,
        *,
        source_thread_id: Any,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or thread_id == self._selected_thread_id
            or not self._team_source_is_current(source_thread_id)
        ):
            return None
        record = self._team_records.get(thread_id)
        if record is not None:
            return (
                record
                if record.get("root_thread_id") == self._selected_thread_id
                else None
            )
        if len(self._team_records) >= _TEAM_RECORD_LIMIT:
            stale_id = next(iter(self._team_records))
            self._team_records.pop(stale_id, None)
        record = {
            "root_thread_id": self._selected_thread_id,
            "task": "",
            "status": "pendingInit",
            "path": "",
            "message": "",
            "events": [],
        }
        self._team_records[thread_id] = record
        return record

    def _team_note(self, record: dict[str, Any], text: Any) -> None:
        rendered = self._bounded_team_text(text, 2_000)
        if rendered:
            record["events"] = (record.get("events", []) + [rendered])[
                -_TEAM_EVENT_LIMIT:
            ]

    def _refresh_team_combo(self) -> None:
        combo = getattr(self, "team_combo", None)
        if combo is None:
            return
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        selected_index = 0
        if not self._team_records:
            combo.addItem("暂无子任务", None)
        else:
            for thread_id in self._team_records:
                record = self._team_records.get(thread_id, {})
                status = record.get("status") or "unknown"
                title = record.get("path") or record.get("task")
                title = self._bounded_team_text(title, 48) or thread_id[-8:]
                combo.addItem(f"{title} · {status}", thread_id)
                index = combo.count() - 1
                full_title = self._bounded_team_text(
                    record.get("path") or record.get("task"), 2_000
                )
                combo.setItemData(
                    index,
                    (f"{full_title}\n" if full_title else "")
                    + f"Codex 子任务 Thread ID：{thread_id}",
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
                if thread_id == selected:
                    selected_index = index
        combo.setCurrentIndex(selected_index)
        combo.blockSignals(False)
        self._render_team_details(combo.currentData())

    def _render_team_details(self, thread_id: Any) -> None:
        details = getattr(self, "team_details_text", None)
        if details is None:
            return
        record = self._team_records.get(thread_id)
        if not isinstance(record, dict):
            details.setPlainText("")
            return
        lines = [
            f"任务：{record.get('task') or '协议未提供'}",
            f"状态：{record.get('status') or 'unknown'}",
            f"路径：{record.get('path') or '未提供'}",
        ]
        events = record.get("events")
        if isinstance(events, list) and events:
            lines.extend(("", "工具 / 活动：", *[f"- {item}" for item in events]))
        message = record.get("message")
        if isinstance(message, str) and message:
            lines.extend(("", "最终回复 / 审阅发现 / 错误：", message))
        lines.extend(
            (
                "",
                "主任务采纳：协议未单独报告；以主任务公开回复为准。",
            )
        )
        details.setPlainText("\n".join(str(line) for line in lines))

    def _on_team_selected(self, _index: int = -1) -> None:
        self._render_team_details(self.team_combo.currentData())

    def _update_team_item(self, method: str, params: dict[str, Any]) -> bool:
        item = params.get("item")
        if not isinstance(item, dict):
            return False
        source_thread_id = params.get("threadId")
        if not self._team_source_is_current(source_thread_id):
            return False
        item_type = item.get("type")
        if item_type == "collabAgentToolCall":
            sender = item.get("senderThreadId")
            if sender != source_thread_id:
                return False
            receiver_ids = item.get("receiverThreadIds")
            receiver_ids = receiver_ids if isinstance(receiver_ids, list) else []
            states = item.get("agentsStates")
            states = states if isinstance(states, dict) else {}
            for thread_id in dict.fromkeys([*receiver_ids, *states.keys()]):
                record = self._team_record(
                    thread_id,
                    source_thread_id=source_thread_id,
                )
                if record is None:
                    continue
                prompt = self._bounded_team_text(item.get("prompt"))
                if prompt:
                    record["task"] = prompt
                state = states.get(thread_id)
                if isinstance(state, dict):
                    status = state.get("status")
                    if isinstance(status, str):
                        record["status"] = status
                    message = self._bounded_team_text(state.get("message"))
                    if message:
                        record["message"] = message
                self._team_note(
                    record,
                    f"{item.get('tool') or '协作'}："
                    f"{item.get('status') or method.rsplit('/', 1)[-1]}",
                )
            self._refresh_team_combo()
            return True
        if item_type == "subAgentActivity":
            record = self._team_record(
                item.get("agentThreadId"),
                source_thread_id=source_thread_id,
            )
            if record is None:
                return False
            path = self._bounded_team_text(item.get("agentPath"), 512)
            if path:
                record["path"] = path
            kind = item.get("kind")
            if isinstance(kind, str):
                if kind == "started":
                    record["status"] = "running"
                elif kind == "interrupted":
                    record["status"] = "interrupted"
                self._team_note(record, f"子任务活动：{kind}")
            self._refresh_team_combo()
            return True
        return False

    def _handle_child_thread_event(
        self,
        method: str,
        params: dict[str, Any],
    ) -> bool:
        thread_id = params.get("threadId")
        record = self._team_records.get(thread_id)
        if (
            not isinstance(record, dict)
            or record.get("root_thread_id") != self._selected_thread_id
        ):
            return False
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                record["message"] = self._bounded_team_text(
                    str(record.get("message") or "") + delta
                )
            return True
        elif method in {"item/started", "item/completed"}:
            item = params.get("item")
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type not in {
                    "reasoning",
                    "contextCompaction",
                    "collabAgentToolCall",
                    "subAgentActivity",
                }:
                    label = item.get("tool") or item.get("command") or item_type
                    status = item.get("status") or method.rsplit("/", 1)[-1]
                    self._team_note(
                        record,
                        f"{self._bounded_team_text(label, 512) or '工具'}：{status}",
                    )
                    error = self._notice_text(item.get("error"))
                    if error:
                        self._team_note(record, f"错误：{error}")
        elif method == "turn/started":
            record["status"] = "running"
        elif method == "turn/completed":
            turn = params.get("turn")
            status = turn.get("status") if isinstance(turn, dict) else None
            record["status"] = status if isinstance(status, str) else "completed"
        else:
            return False
        self._refresh_team_combo()
        return True

    def _render_thread_read(
        self,
        payload: dict[str, Any],
        *,
        allow_active: bool = False,
        hidden_user_prefix: str | None = None,
    ) -> bool:
        if self._turn_state.busy and not allow_active:
            return False
        raw_result = payload.get("read", payload.get("result"))
        if not isinstance(raw_result, dict):
            return False
        thread = raw_result.get("thread")
        if not isinstance(thread, dict):
            return False
        thread_id = thread.get("id")
        if thread_id != self._selected_thread_id:
            return False
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return False

        restored: list[tuple[str, Any]] = []
        for turn in turns:
            items = turn.get("items") if isinstance(turn, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "userMessage":
                    texts: list[str] = []
                    attachments: list[str] = []
                    content = item.get("content")
                    for entry in (content if isinstance(content, list) else []):
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("type") == "text" and isinstance(
                            entry.get("text"), str
                        ):
                            texts.append(entry["text"])
                        elif entry.get("type") == "localImage" and isinstance(
                            entry.get("path"), str
                        ):
                            attachments.append(Path(entry["path"]).name)
                    restored_text = "\n".join(texts)
                    if (
                        not attachments
                        and (
                            restored_text == _GOAL_CONTINUE_INSTRUCTION
                            or (
                                isinstance(hidden_user_prefix, str)
                                and restored_text.startswith(hidden_user_prefix)
                            )
                        )
                    ):
                        continue
                    restored.append(("user", (restored_text, tuple(attachments))))
                elif item_type == "agentMessage" and isinstance(
                    item.get("text"), str
                ):
                    restored.append(("agent", item["text"]))
        if hasattr(self.conversation, "clear_messages"):
            self.conversation.clear_messages()
        rendered = False
        for role, value in restored:
            if role == "user":
                text, attachments = value
                self.conversation.add_user_message(text, attachments)
            else:
                self.conversation.begin_codex_message()
                self.conversation.append_codex_delta(value)
                self.conversation.finish_codex_message()
            rendered = True
        if rendered:
            self.welcome_group.setVisible(False)
        return True

    def _new_thread(self) -> None:
        if (
            self._client is not None
            and not self._turn_state.busy
            and self._goal_action_context is None
            and not self._session_action_pending
            and not self._turn_start_request_pending
            and not self._reconciliation_tokens
        ):
            self._session_action_pending = True
            self._refresh_controls()
            self._client.start_thread(
                model=self._selected_model_id(),
                service_tier=self._selected_service_tier(),
            )

    def _resume_thread(self) -> None:
        record = self._selected_history_record()
        thread_id = record.get("thread_id") if record is not None else None
        if not isinstance(thread_id, str) or not thread_id:
            self._append_system("请先选择历史会话。")
            return
        self._request_thread_resume(thread_id, context="session_resume")

    def _send(self) -> None:
        text = self.input_edit.toPlainText()
        attachment_paths = self._attachment_paths()
        if not text.strip() and not attachment_paths:
            return
        if not self._connected or not self._authenticated:
            return
        if self._is_stopping_turn():
            return
        if attachment_paths and not self._selected_model_supports_images():
            self._append_system("当前模型不支持图片输入，请选择其他模型后再发送。")
            return
        thread_id = self._selected_thread_id
        if not isinstance(thread_id, str) or not thread_id:
            self._append_system("请先新建或恢复 Thread。")
            return
        if (
            self._session_action_pending
            or self._turn_start_request_pending
            or self._turn_steer_request_pending
            or self._reconciliation_tokens
        ):
            return
        if self._client is None:
            return
        if self._turn_state.busy:
            if (
                self._turn_state.phase is not TurnPhase.IN_PROGRESS
                or not isinstance(self._turn_state.turn_id, str)
            ):
                self._append_system("当前 Turn 正在建立或同步，暂不能追加指令。")
                self._refresh_controls()
                return
            self._steer_active_turn(text, attachment_paths, thread_id)
            return
        if not self._start_new_turn(text, attachment_paths, thread_id):
            self._append_system("当前 Turn 暂不能发送。")
            self._refresh_controls()
        elif self._goal_continuation_paused:
            self._goal_continuation_paused = False
            self._apply_focus_mode(thread_id, self._focus_mode)
        return

    def _start_new_turn(
        self,
        text: str,
        attachment_paths: tuple[str, ...],
        thread_id: str,
        *,
        request_text: str | None = None,
        steer_fallback: bool = False,
        steer_source_thread_id: str | None = None,
        steer_source_turn_id: str | None = None,
        goal_auto_continue: bool = False,
    ) -> bool:
        if self._client is None or not self._turn_state.begin_start(thread_id):
            return False
        submitted_text = (
            request_text
            if isinstance(request_text, str)
            else self._request_text_with_selection(text)
        )
        if not goal_auto_continue:
            self._add_user_message(text, attachment_paths)
        self._begin_codex_message()
        self._stream_thread_id = thread_id
        self._stream_turn_id = None
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        self.turn_status_label.setText("Turn：正在创建")
        token = self._turn_state.capture_token()
        self._begin_turn_performance(token)
        context = (
            f"{_TURN_START_CONTEXT_PREFIX}{token.generation}:{token.revision}"
        )
        self._turn_start_tokens[context] = token
        self._pending_turn_drafts[context] = {
            "text": text,
            "attachment_paths": attachment_paths,
            "steer_fallback": steer_fallback,
            "steer_source_thread_id": steer_source_thread_id,
            "steer_source_turn_id": steer_source_turn_id,
            "goal_auto_continue": goal_auto_continue,
        }
        if goal_auto_continue:
            self._goal_auto_turn_token = token
            self._goal_auto_turn_has_progress = False
        else:
            self._goal_auto_turn_token = None
            self._goal_auto_turn_has_progress = False
        self._begin_diagnostic_turn(
            thread_id,
            token,
            text,
            attachment_paths,
        )
        self._active_turn_start_context = context
        self._turn_start_request_pending = True
        self._refresh_controls()
        self._client.start_turn(
            submitted_text,
            model=self._selected_model_id(),
            effort=self._selected_effort(),
            service_tier=self._selected_service_tier(),
            local_image_paths=list(attachment_paths),
            context=context,
        )
        return True

    def _steer_active_turn(
        self,
        text: str,
        attachment_paths: tuple[str, ...],
        thread_id: str,
    ) -> None:
        token = self._turn_state.capture_token()
        if token.thread_id != thread_id or not isinstance(token.turn_id, str):
            self._append_system("当前活动 Turn 不明确，暂不能追加指令。")
            return
        context = (
            f"{_TURN_STEER_CONTEXT_PREFIX}{token.generation}:"
            f"{token.revision}:{uuid.uuid4().hex}"
        )
        request_text = self._request_text_with_selection(text)
        self._turn_steer_tokens[context] = token
        self._pending_steer_drafts[context] = {
            "text": text,
            "attachment_paths": attachment_paths,
            "request_text": request_text,
            "source_token": token,
            "state_sync_attempted": False,
            "retry_attempted": False,
        }
        self._active_turn_steer_context = context
        self._turn_steer_request_pending = True
        self._refresh_controls()
        self._client.steer_turn(
            request_text,
            local_image_paths=list(attachment_paths),
            context=context,
        )

    def _fallback_no_active_steer(
        self,
        context: str,
        token: TurnStateToken,
        details: dict[str, Any],
    ) -> bool:
        draft = self._pending_steer_drafts.get(context)
        if (
            not isinstance(draft, dict)
            or self._client is None
            or not self._turn_state.token_generation_is_current(token)
            or token.thread_id != self._selected_thread_id
            or details.get("turn_active") is not False
            or details.get("thread_id") != token.thread_id
            or details.get("turn_id") != token.turn_id
        ):
            self._pending_steer_drafts.pop(context, None)
            return False
        if (
            draft.get("fallback_cancelled") is True
            or self._is_stopping_turn()
            or self._interrupt_pending
        ):
            self._pending_steer_drafts.pop(context, None)
            return True
        reconciled = self._turn_state.reconcile_no_active_error(token, details)
        terminal = reconciled
        if not reconciled:
            terminal = (
                not self._turn_state.busy
                and self._turn_state.token_generation_is_current(token)
                and token.thread_id == self._selected_thread_id
            )
        if not terminal:
            self._pending_steer_drafts.pop(context, None)
            self._append_system(
                "上一轮状态已变化；追加文字和图片已保留，未自动重试。"
            )
            return True
        if reconciled:
            self._mark_turn_terminal(
                details.get("turn_status")
                if isinstance(details.get("turn_status"), str)
                else "completed"
            )

        text = draft.get("text")
        attachment_paths = tuple(draft.get("attachment_paths") or ())
        if not isinstance(text, str) or not isinstance(token.thread_id, str):
            self._pending_steer_drafts.pop(context, None)
            return True
        started = self._start_new_turn(
            text,
            attachment_paths,
            token.thread_id,
            request_text=(
                draft.get("request_text")
                if isinstance(draft.get("request_text"), str)
                else None
            ),
            steer_fallback=True,
            steer_source_thread_id=token.thread_id,
            steer_source_turn_id=token.turn_id,
        )
        if started:
            self._pending_steer_drafts.pop(context, None)
            return True

        self._restore_steer_draft_to_composer(draft)
        self._pending_steer_drafts.pop(context, None)
        self._append_system("上一轮已结束；追加文字和图片已保留，可再次发送。")
        return True

    def _begin_steer_state_sync(
        self,
        context: str,
        token: TurnStateToken,
        error_code: str,
        details: dict[str, Any],
    ) -> bool:
        draft = self._pending_steer_drafts.get(context)
        if (
            not isinstance(draft, dict)
            or draft.get("state_sync_attempted") is True
            or draft.get("retry_attempted") is True
            or draft.get("fallback_cancelled") is True
            or self._is_stopping_turn()
            or self._interrupt_pending
            or token.thread_id != self._selected_thread_id
        ):
            return False
        if error_code == "NO_ACTIVE_TURN":
            valid_error = (
                details.get("thread_id") == token.thread_id
                and details.get("turn_id") == token.turn_id
                and details.get("turn_active") is False
            )
        else:
            valid_error = (
                error_code == "STALE_ACTIVE_TURN"
                and details.get("thread_id") == token.thread_id
                and details.get("expected_turn_id") == token.turn_id
                and isinstance(details.get("active_turn_id"), str)
                and details.get("turn_active") is True
            )
        if not valid_error:
            return False
        draft["source_token"] = token
        draft["state_sync_attempted"] = True
        sync_context = self._request_session_reconciliation("stale_steer")
        if not isinstance(sync_context, str):
            return False
        draft["reconciliation_context"] = sync_context
        return True

    def _pending_steer_reconciliation(
        self,
        context: str,
    ) -> tuple[str, dict[str, Any]] | None:
        for steer_context, draft in self._pending_steer_drafts.items():
            if draft.get("reconciliation_context") == context:
                return steer_context, draft
        return None

    def _complete_steer_reconciliation(
        self,
        context: str,
        payload: dict[str, Any],
    ) -> bool:
        pending = self._pending_steer_reconciliation(context)
        if pending is None:
            return False
        steer_context, draft = pending
        token = self._reconciliation_tokens.pop(context, None)
        source_token = draft.get("source_token")
        draft.pop("reconciliation_context", None)
        session = payload.get("session")
        if (
            draft.get("fallback_cancelled") is True
            or self._is_stopping_turn()
            or self._interrupt_pending
        ):
            self._pending_steer_drafts.pop(steer_context, None)
            return True
        if (
            not isinstance(token, TurnStateToken)
            or token != source_token
            or not isinstance(session, dict)
            or session.get("thread_id") != self._selected_thread_id
            or not isinstance(session.get("turn_active"), bool)
        ):
            self._restore_steer_draft_to_composer(draft)
            self._pending_steer_drafts.pop(steer_context, None)
            self._append_system(
                "未能确认当前 Turn；追加文字和图片已保留，未自动重试。"
            )
            return True

        thread_id = session.get("thread_id")
        turn_id = session.get("turn_id")
        turn_active = session.get("turn_active") is True
        if turn_active and turn_id == self._goal_turn_id:
            reconciled = self._turn_state.reconcile_steer_snapshot(
                token,
                thread_id,
                None,
                turn_active=False,
            )
            if not reconciled:
                self._restore_steer_draft_to_composer(draft)
                self._pending_steer_drafts.pop(steer_context, None)
                self._append_system(
                    "当前 Turn 再次变化；追加文字和图片已保留，未自动重试。"
                )
                return True
            if (
                self._stream_thread_id != thread_id
                or self._stream_turn_id != turn_id
            ):
                self._freeze_codex_message()
                self._stream_thread_id = thread_id
                self._stream_turn_id = turn_id
                self._begin_codex_message()
            self.goal_activity_label.setText(self._goal_waiting_activity_text())
            self._restore_steer_draft_to_composer(draft)
            self._pending_steer_drafts.pop(steer_context, None)
            self._append_system(
                "当前 Goal Turn 正在运行；追加文字和图片已保留，未发送到 Goal。"
            )
            return True

        previous_stream_turn_id = self._stream_turn_id
        applied = self._apply_session(
            session,
            token=token,
            allow_followup=False,
            steer_recovery=True,
        )
        if not applied:
            self._restore_steer_draft_to_composer(draft)
            self._pending_steer_drafts.pop(steer_context, None)
            self._append_system(
                "当前 Turn 再次变化；追加文字和图片已保留，未自动重试。"
            )
            return True

        if turn_active:
            if not isinstance(turn_id, str) or draft.get("retry_attempted") is True:
                self._restore_steer_draft_to_composer(draft)
                self._pending_steer_drafts.pop(steer_context, None)
                self._append_system(
                    "当前 Turn 再次变化；追加文字和图片已保留，未自动重试。"
                )
                return True
            if previous_stream_turn_id != turn_id:
                self._freeze_codex_message()
                self._stream_thread_id = thread_id
                self._stream_turn_id = turn_id
                self._begin_codex_message()
            retry_token = self._turn_state.capture_token()
            retry_context = (
                f"{_TURN_STEER_CONTEXT_PREFIX}{retry_token.generation}:"
                f"{retry_token.revision}:{uuid.uuid4().hex}"
            )
            draft["retry_attempted"] = True
            self._pending_steer_drafts.pop(steer_context, None)
            self._pending_steer_drafts[retry_context] = draft
            self._turn_steer_tokens[retry_context] = retry_token
            self._active_turn_steer_context = retry_context
            self._turn_steer_request_pending = True
            self._refresh_controls()
            self._client.steer_turn(
                draft.get("request_text")
                if isinstance(draft.get("request_text"), str)
                else "",
                local_image_paths=list(draft.get("attachment_paths") or ()),
                context=retry_context,
            )
            return True

        self._mark_turn_terminal(
            session.get("turn_status")
            if isinstance(session.get("turn_status"), str)
            else "completed"
        )
        self._fallback_no_active_steer(
            steer_context,
            source_token,
            {
                "thread_id": source_token.thread_id,
                "turn_id": source_token.turn_id,
                "turn_active": False,
                "turn_status": session.get("turn_status") or "completed",
            },
        )
        return True

    def _stop(self) -> None:
        controls = self._turn_state.derive_controls(
            connected=self._connected,
            authenticated=self._authenticated,
            selected_thread_id=self._selected_thread_id,
        )
        if self._client is not None and controls.stop and not self._interrupt_pending:
            token = self._turn_state.capture_token()
            if self._stopping_turn_token is not None:
                return
            context = (
                f"{_INTERRUPT_CONTEXT_PREFIX}{token.generation}:{token.revision}"
            )
            self._interrupt_tokens[context] = token
            self._active_interrupt_context = context
            self._stopping_turn_token = token
            self._interrupt_pending = True
            self._pause_goal_continuation("", notify=False)
            for draft in self._pending_steer_drafts.values():
                if isinstance(draft, dict):
                    draft["fallback_cancelled"] = True
            self._active_turn_steer_context = None
            self._turn_steer_request_pending = False
            self._reconciliation_tokens.clear()
            self._stop_recovery_state = "recovering"
            self._stopped_source_turn = (
                (token.thread_id, token.turn_id)
                if isinstance(token.thread_id, str)
                and isinstance(token.turn_id, str)
                else None
            )
            self._connected = False
            self._set_status_indicator(
                self.connection_label,
                "Codex",
                "恢复中",
                False,
                "已停止当前 Turn，Codex app-server 正在收口或恢复",
            )
            self.auth_label.setText("认证：恢复中")
            self._goal_turn_id = None
            self._goal_turn_has_text = False
            self._stream_thread_id = None
            self._stream_turn_id = None
            self._freeze_codex_message()
            if isinstance(token.thread_id, str) and isinstance(token.turn_id, str):
                self._turn_state.observe_completed(token.thread_id, token.turn_id)
            self._record_turn_performance("completed")
            self._finalize_diagnostic_turn("interrupted")
            self._turn_start_request_pending = False
            self._active_turn_start_context = None
            self._stopping_turn_token = None
            self.turn_status_label.setText("Turn：已停止")
            self.turn_status_label.setToolTip(
                "Codex 已停止接收该 Turn 的后续输出；已发出的 Houdini 操作可能仍在收尾"
            )
            self._append_system(
                "Codex 已停止；已发出的 Houdini 操作可能仍在收尾。"
            )
            self.goal_activity_label.setText(self._goal_waiting_activity_text())
            self._refresh_controls()
            if not self._houdini_status_pending:
                request_id = self._client.get_houdini_status()
                self._houdini_status_pending = request_id is not None
                self._houdini_status_turn_token = (
                    token if request_id is not None else None
                )
            self._client.interrupt(context=context)

    @QtCore.Slot(str, dict)
    def _on_action_completed(self, context: str, payload: dict[str, Any]) -> None:
        if self._handle_scene_action(context, payload):
            self._maybe_start_goal_continuation()
            return
        if context == _HOUDINI_STATUS_CONTEXT:
            self._houdini_status_pending = False
            token = self._houdini_status_turn_token
            self._houdini_status_turn_token = None
            houdini_mcp = payload.get("houdini_mcp")
            if isinstance(houdini_mcp, dict):
                self._set_mcp_status(
                    houdini_mcp.get("backend"),
                    houdini_mcp.get("available") is True,
                )
            self._apply_houdini_status(houdini_mcp)
            session = payload.get("session")
            if isinstance(token, TurnStateToken) and isinstance(session, dict):
                self._apply_session(
                    session,
                    token=token,
                    allow_followup=True,
                )
            return
        if context == _MODELS_CONTEXT:
            self._models_resolved = True
            self._apply_models(payload.get("models"))
            self._refresh_controls()
            return
        if context == _THREADS_CONTEXT:
            self._threads_requested = True
            self._apply_threads(payload.get("threads"))
            self._refresh_controls()
            return
        if (
            context == _GOAL_GET_CONTEXT
            and isinstance(self._crash_recovery_marker, dict)
            and isinstance(self._crash_recovery_health_session, dict)
        ):
            if context != self._goal_action_context:
                return
            self._goal_action_context = None
            if (
                self._selected_thread_id is not None
                or not self._crash_recovery_goal_matches(payload)
                or self._client is None
            ):
                self._discard_crash_recovery_candidate()
            elif self._crash_recovery_thread_payload is None:
                self._crash_recovery_goal_payload = dict(payload)
                self._client.read_thread(
                    self._crash_recovery_marker["thread_id"],
                    context=_CRASH_RECOVERY_READ_CONTEXT,
                )
            else:
                thread_payload = self._crash_recovery_thread_payload
                self._crash_recovery_goal_payload = dict(payload)
                self._complete_crash_recovery_bind(thread_payload)
            self._refresh_controls()
            return
        if context in {
            _GOAL_GET_CONTEXT,
            _GOAL_SET_CONTEXT,
            _GOAL_CLEAR_CONTEXT,
            _FOCUS_SET_CONTEXT,
        }:
            if context != self._goal_action_context:
                return
            self._goal_action_context = None
            thread_id = payload.get("thread_id")
            if thread_id != self._selected_thread_id:
                self._request_goal()
                self._refresh_controls()
                return
            explicit_continue: str | None = None
            if context == _FOCUS_SET_CONTEXT:
                if self._apply_focus_mode(thread_id, payload.get("focus_mode")):
                    self._append_system(
                        "目标专注模式已开启。"
                        if payload.get("focus_mode") is True
                        else "目标专注模式已关闭；Goal 保持不变。"
                    )
                    if payload.get("focus_mode") is True:
                        explicit_continue = "focus"
            elif context == _GOAL_CLEAR_CONTEXT:
                if payload.get("cleared") is True:
                    self._apply_goal(thread_id, None)
                    self._append_system("Goal 已清除。")
            elif self._apply_goal(thread_id, payload.get("goal")):
                if context == _GOAL_SET_CONTEXT:
                    self._append_system("Goal 已保存到当前 Codex Thread。")
                    self._goal_continuation_paused = False
            self._apply_focus_mode(thread_id, payload.get("focus_mode"))
            if (
                context == _GOAL_GET_CONTEXT
                and self._goal_continue_after_open_thread_id == thread_id
            ):
                self._goal_continue_after_open_thread_id = None
                explicit_continue = "manual-open"
            self._refresh_controls()
            self._maybe_start_goal_continuation(
                explicit_source=explicit_continue,
            )
            return
        if context == _CRASH_RECOVERY_READ_CONTEXT:
            marker = self._crash_recovery_marker
            raw_result = payload.get("read", payload.get("result"))
            thread = (
                raw_result.get("thread") if isinstance(raw_result, dict) else None
            )
            if (
                isinstance(marker, dict)
                and isinstance(thread, dict)
                and thread.get("id") == marker["thread_id"]
                and self._selected_thread_id is None
                and self._client is not None
            ):
                self._crash_recovery_thread_payload = dict(payload)
                self._goal_action_context = _GOAL_GET_CONTEXT
                self._client.get_goal(marker["thread_id"])
            else:
                self._discard_crash_recovery_candidate()
            self._refresh_controls()
            return
        if context == _CRASH_RECOVERY_RECHECK_CONTEXT:
            self._complete_crash_recovery_recheck(payload)
            self._refresh_controls()
            return
        if context.startswith(_THREAD_READ_CONTEXT_PREFIX):
            self._render_thread_read(payload)
            self._refresh_controls()
            return
        if context.startswith(_THREAD_RENAME_CONTEXT_PREFIX):
            self._session_action_pending = False
            self._update_history_name(
                payload.get("thread_id"), payload.get("name")
            )
            self._append_system("会话名称已更新。")
            self._refresh_controls()
            return

        if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
            if self._complete_steer_reconciliation(context, payload):
                self._refresh_controls()
                return
            token = self._reconciliation_tokens.pop(context, None)
            session = payload.get("session")
            was_stopping = (
                isinstance(token, TurnStateToken)
                and token == self._stopping_turn_token
            )
            if isinstance(token, TurnStateToken) and isinstance(session, dict):
                applied = self._apply_session(
                    session,
                    token=token,
                    allow_followup=False,
                )
                if (
                    applied
                    and session.get("turn_active") is False
                    and not was_stopping
                ):
                    turn_status = (
                        session.get("turn_status")
                        if isinstance(session.get("turn_status"), str)
                        else None
                    )
                    auto_turn = self._goal_auto_turn_is_current(token.thread_id)
                    self._mark_turn_terminal(turn_status)
                    self._queue_goal_continuation(
                        token.thread_id,
                        token.turn_id,
                        turn_status,
                        auto_turn=auto_turn,
                    )
                    self._maybe_start_goal_continuation()
                elif (
                    not applied
                    and self._turn_state.busy
                    and self._turn_state.token_generation_is_current(token)
                    and not was_stopping
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
                previous_thread_id = self._selected_thread_id
                if (
                    isinstance(self._selected_thread_id, str)
                    and self._selected_thread_id != thread_id
                ):
                    self.attachment_strip.clear()
                    self._clear_diagnostic_context()
                if previous_thread_id != thread_id:
                    self._goal_action_context = None
                    self._clear_goal_display()
                    self._team_records.clear()
                    self._refresh_team_combo()
                    self._clear_turn_performance()
                self._selected_thread_id = thread_id
                self._apply_focus_mode(
                    thread_id,
                    payload.get("focus_mode", False),
                )
                self.thread_id_edit.setText(thread_id)
                for index in range(self.history_combo.count()):
                    record = self.history_combo.itemData(index)
                    if (
                        isinstance(record, dict)
                        and record.get("thread_id") == thread_id
                    ):
                        self.history_combo.setCurrentIndex(index)
                        self._on_history_index_changed(index)
                        break
                self.thread_status_label.setText(
                    f"Thread：{self._history_title(thread_id)}"
                )
                self.thread_status_label.setToolTip(
                    f"{self._history_title(thread_id, full=True)}\n"
                    f"Codex Thread ID：{thread_id}"
                )
                if context == "session_start":
                    if hasattr(self.conversation, "clear_messages"):
                        self.conversation.clear_messages()
                    self._append_system("已新建会话。")
                    if self._client is not None:
                        self._client.get_threads()
                else:
                    self._render_thread_read(payload)
                    self._append_system("已恢复会话。")
                    self._goal_continue_after_open_thread_id = thread_id
                self._request_goal()
        elif context.startswith(_TURN_START_CONTEXT_PREFIX):
            token = self._turn_start_tokens.pop(context, None)
            if context == self._active_turn_start_context:
                self._active_turn_start_context = None
                self._turn_start_request_pending = False
            accepted_draft = (
                self._accept_sent_draft(context)
                if isinstance(token, TurnStateToken)
                and self._turn_state.token_generation_is_current(token)
                else None
            )
            auto_start = (
                isinstance(accepted_draft, dict)
                and accepted_draft.get("goal_auto_continue") is True
            )
            thread_id = payload.get("thread_id")
            turn_id = payload.get("turn_id")
            state_changed = False
            if (
                isinstance(token, TurnStateToken)
                and isinstance(thread_id, str)
                and isinstance(turn_id, str)
            ):
                if self._stream_thread_id == thread_id:
                    if self._stream_turn_id in {None, turn_id}:
                        self._stream_turn_id = turn_id
                    else:
                        self._request_session_reconciliation(
                            "mismatched_stream_turn_ack"
                        )
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
                if thread_id == token.thread_id:
                    self._record_turn_performance("ack", token=token)
            if state_changed:
                self._bind_diagnostic_turn(thread_id, turn_id)
                if (
                    isinstance(accepted_draft, dict)
                    and accepted_draft.get("steer_fallback") is True
                ):
                    self._append_system(
                        "上一轮已结束，已作为新消息发送。"
                    )
                if self._turn_state.busy:
                    self.turn_status_label.setText("Turn：运行中")
                    self.turn_status_label.setToolTip(
                        f"Codex Turn ID：{turn_id}"
                        if isinstance(turn_id, str)
                        else "当前 Codex Turn 正在运行"
                    )
                    if auto_start:
                        self.goal_activity_label.setText(
                            self._goal_waiting_activity_text()
                        )
                else:
                    turn_status = (
                        payload.get("turn_status")
                        if isinstance(payload.get("turn_status"), str)
                        else None
                    )
                    self._mark_turn_terminal(turn_status)
                    self._queue_goal_continuation(
                        thread_id,
                        turn_id,
                        turn_status,
                        auto_turn=auto_start,
                    )
                    self._maybe_start_goal_continuation()
            else:
                self._request_session_reconciliation("late_turn_start_ack")
        elif context.startswith(_TURN_STEER_CONTEXT_PREFIX):
            token = self._turn_steer_tokens.pop(context, None)
            if context == self._active_turn_steer_context:
                self._active_turn_steer_context = None
                self._turn_steer_request_pending = False
            if not isinstance(token, TurnStateToken):
                self._refresh_controls()
                return
            thread_id = payload.get("thread_id")
            turn_id = payload.get("turn_id")
            pending_draft = self._pending_steer_drafts.get(context)
            if (
                isinstance(pending_draft, dict)
                and pending_draft.get("fallback_cancelled") is True
            ):
                self._pending_steer_drafts.pop(context, None)
                self._refresh_controls()
                return
            if (
                self._turn_state.token_generation_is_current(token)
                and thread_id == token.thread_id
                and turn_id == token.turn_id
            ):
                self._accept_steered_draft(context)
            else:
                self._pending_steer_drafts.pop(context, None)
                self._append_system("追加响应与当前 Turn 不匹配；输入和附件已保留。")
        elif context.startswith(_INTERRUPT_CONTEXT_PREFIX):
            token = self._interrupt_tokens.pop(context, None)
            if context == self._active_interrupt_context:
                self._active_interrupt_context = None
                self._interrupt_pending = False
            session = payload.get("session")
            if (
                isinstance(token, TurnStateToken)
                and token.thread_id == self._selected_thread_id
                and isinstance(session, dict)
            ):
                self._apply_session(
                    session,
                    token=self._turn_state.capture_token(),
                    allow_followup=True,
                )
        elif context.startswith("approval_"):
            self._current_approval = None
            self._current_approval_offers_persistent_rule = False
            self.approval_group.setVisible(False)
            self.approval_details_button.setChecked(False)
            self.approval_details_button.setVisible(False)
            self.approval_details_text.setVisible(False)
            self.persistent_allow_note.setVisible(False)
            self.persistent_allow_button.setVisible(False)
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
            self.persistent_allow_button.setEnabled(True)
            self._show_next_approval()
            self._maybe_start_goal_continuation()
        self._refresh_controls()

    @QtCore.Slot(dict)
    def _on_events(self, payload: dict[str, Any]) -> None:
        for event in payload.get("events", []):
            sequence = event.get("seq")
            try:
                self._render_event(event)
            except Exception as exc:
                if isinstance(self._selected_thread_id, str):
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
            if isinstance(self._selected_thread_id, str):
                self._append_system("事件缓冲出现间隙；正在同步 Turn 状态。")
            self._request_session_reconciliation("event_gap")
        self._maybe_start_goal_continuation()
        self._schedule_poll(0)

    @classmethod
    def _notice_text(cls, value: Any) -> str:
        if isinstance(value, str):
            return cls._bounded_goal_summary(value, 2_000)
        if isinstance(value, dict):
            for key in ("message", "error", "detail", "reason", "title"):
                if key in value:
                    text = cls._notice_text(value.get(key))
                    if text:
                        return text
        if isinstance(value, (list, tuple)):
            parts = [cls._notice_text(item) for item in value[:8]]
            return " | ".join(part for part in parts if part)
        return ""

    @classmethod
    def _is_long_thread_warning(cls, params: dict[str, Any]) -> bool:
        code = params.get("code")
        message = cls._notice_text(params)
        signature = f"{code or ''} {message}".casefold()
        markers = (
            "long conversation",
            "long thread",
            "context window",
            "context length",
            "weighted tokens left",
            "conversation is getting long",
            "对话较长",
            "上下文窗口",
        )
        return any(marker in signature for marker in markers)

    def _show_compaction_notice(self, key: str) -> None:
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        if hasattr(self.conversation, "add_compaction_notice"):
            self.conversation.add_compaction_notice(key)
        else:
            self._append_system(_COMPACTION_NOTICE)

    def _show_long_thread_warning(self) -> None:
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        if hasattr(self.conversation, "show_long_thread_warning"):
            self.conversation.show_long_thread_warning()
        else:
            self._append_system(_LONG_THREAD_WARNING)

    def _show_codex_notice(self, method: str, params: dict[str, Any]) -> None:
        self._remember_codex_error(method, params)
        if not isinstance(self._selected_thread_id, str):
            return
        if self._is_long_thread_warning(params):
            self._show_long_thread_warning()
            return
        message = self._notice_text(params)
        code = params.get("code")
        if "request_user_input" in message.casefold() or "requestuserinput" in message.casefold():
            self._append_system(
                "Codex 的额外提问在当前 Panel 中不可用；已继续采用合理默认值。"
            )
            return
        prefix = str(code) if isinstance(code, (str, int)) else method
        self._append_system(
            f"{prefix}：{message}" if message else f"{prefix}：未提供详细信息"
        )

    def _show_protocol_notice(self, event: dict[str, Any]) -> None:
        if not isinstance(self._selected_thread_id, str):
            return
        method = event.get("method")
        code = event.get("code")
        if code == "UNKNOWN_NOTIFICATION_IGNORED":
            return
        message = self._notice_text(event) or str(event.get("message") or "")
        method_text = str(method) if isinstance(method, str) else ""
        signature = f"{method_text} {code or ''} {message}".casefold()
        if "requestuserinput" in signature or "request_user_input" in signature:
            text = "Codex 请求了当前 Panel 不提供的额外提问；已安全忽略并继续。"
            key = "known-request-user-input"
        elif code == "SERVER_REQUEST_REJECTED":
            text = "Codex 请求了当前稳定协议不支持的额外交互；已安全忽略。"
            key = "known-server-request-rejected"
        else:
            parts = [str(part) for part in (code, method_text, message) if part]
            text = "协议提示：" + " · ".join(parts or ["未知协议事件"])
            key = f"{code}|{method_text}|{message}"
        self._append_protocol_warning(key, text)

    def _render_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "session_state":
            session = event.get("session")
            if isinstance(session, dict):
                self._apply_session(
                    session,
                    token=self._turn_state.capture_token(),
                    allow_followup=True,
                )
            return
        if event_type == "codex_notification":
            method = event.get("method")
            raw_params = event.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            if method == "thread/goal/updated":
                thread_id = params.get("threadId")
                goal = params.get("goal")
                if (
                    thread_id == self._selected_thread_id
                    and isinstance(goal, dict)
                    and "status" in goal
                    and goal.get("status") != "active"
                ):
                    self._apply_focus_mode(thread_id, False)
                if self._apply_goal(thread_id, goal):
                    goal_turn_id = params.get("turnId")
                    if (
                        isinstance(goal, dict)
                        and goal.get("status") == "active"
                        and isinstance(goal_turn_id, str)
                        and self._goal_continuation_boundary
                        != (thread_id, goal_turn_id)
                        and self._can_bind_goal_turn(thread_id, goal_turn_id)
                    ):
                        self._goal_turn_id = goal_turn_id
                return
            if method == "thread/goal/cleared":
                self._apply_goal(params.get("threadId"), None)
                return
            if method in {"item/started", "item/completed"}:
                if self._update_team_item(method, params):
                    if self._event_matches_active_stream(params):
                        self._mark_goal_auto_turn_progress()
                    return
            if isinstance(method, str) and self._handle_child_thread_event(
                method, params
            ):
                return
            thread_id = params.get("threadId")
            if (
                isinstance(thread_id, str)
                and thread_id != self._selected_thread_id
                and method
                in {
                    "item/started",
                    "item/completed",
                    "item/agentMessage/delta",
                    "item/mcpToolCall/progress",
                    "turn/started",
                    "turn/completed",
                    "turn/plan/updated",
                    "thread/compacted",
                }
            ):
                return
            if method == "item/agentMessage/delta":
                if self._event_is_stale_source_turn(
                    params.get("threadId"), params.get("turnId")
                ):
                    return
                delta = params.get("delta")
                active_stream = self._event_matches_active_stream(
                    params, require_item_id=True
                )
                goal_stream = self._event_matches_goal_stream(
                    params, require_item_id=True
                )
                if isinstance(delta, str) and (active_stream or goal_stream):
                    if active_stream:
                        self._record_turn_performance("first_delta")
                        if delta.strip():
                            self._mark_goal_auto_turn_progress()
                    self._append_codex_delta(delta)
                    if goal_stream:
                        self._goal_turn_has_text = True
                        if self.goal_activity_label.text() == _GOAL_RUNNING_NO_TEXT:
                            self.goal_activity_label.setText(
                                _GOAL_RUNNING_WITH_TEXT
                            )
            elif method == "turn/plan/updated":
                active_stream = self._event_matches_active_stream(params)
                goal_plan = self._goal_turn_matches(
                    params.get("threadId"), params.get("turnId")
                )
                if active_stream or goal_plan:
                    steps = params.get("plan") or []
                    plan_steps = [step for step in steps if isinstance(step, dict)]
                    rendered = " | ".join(
                        f"{step.get('status', '?')}: {step.get('step', '')}"
                        for step in plan_steps
                    )
                    self._append_system(f"计划：{rendered}")
                    current_step = next(
                        (
                            step.get("step")
                            for step in plan_steps
                            if step.get("status")
                            in {"inProgress", "in_progress", "active"}
                            and isinstance(step.get("step"), str)
                            and step.get("step").strip()
                        ),
                        None,
                    )
                    if current_step is None:
                        current_step = next(
                            (
                                step.get("step")
                                for step in plan_steps
                                if step.get("status") == "pending"
                                if isinstance(step.get("step"), str)
                                and step.get("step").strip()
                            ),
                            None,
                        )
                    if goal_plan:
                        if isinstance(current_step, str):
                            self.goal_activity_label.setText(
                                "当前跟进："
                                + self._bounded_goal_summary(current_step, 240)
                            )
                        else:
                            self.goal_activity_label.setText(
                                self._goal_waiting_activity_text()
                            )
            elif method in {"item/started", "item/completed"}:
                item = params.get("item")
                active_stream = self._event_matches_active_stream(params)
                if (
                    active_stream
                    and isinstance(item, dict)
                    and item.get("type")
                    in {
                        "collabAgentToolCall",
                        "commandExecution",
                        "dynamicToolCall",
                        "fileChange",
                        "mcpToolCall",
                        "webSearch",
                    }
                ):
                    self._mark_goal_auto_turn_progress()
                if (
                    isinstance(item, dict)
                    and item.get("type") == "contextCompaction"
                ):
                    if method == "item/completed":
                        thread_id = params.get("threadId")
                        turn_id = params.get("turnId")
                        item_id = item.get("id")
                        key = f"{thread_id or ''}:{turn_id or item_id or 'compaction'}"
                        self._show_compaction_notice(key)
                if (
                    active_stream
                    and isinstance(item, dict)
                    and item.get("type") == "mcpToolCall"
                ):
                    item_id = item.get("id")
                    tool = item.get("tool")
                    raw_status = item.get("status")
                    status = (
                        "started"
                        if method == "item/started" or raw_status == "inProgress"
                        else raw_status
                    )
                    if (
                        isinstance(item_id, str)
                        and isinstance(status, str)
                        and status in {"started", "completed", "failed"}
                    ):
                        self._update_tool_activity(
                            item_id,
                            tool if isinstance(tool, str) and tool else "MCP",
                            status,
                            item.get("error") if status == "failed" else None,
                        )
                        self._record_diagnostic_tool(item_id, item, status)
            elif method == "item/mcpToolCall/progress":
                message = params.get("message")
                item_id = params.get("itemId")
                if (
                    isinstance(message, str)
                    and message
                    and isinstance(item_id, str)
                    and self._event_matches_active_stream(
                        params, require_item_id=True
                    )
                ):
                    self._update_tool_progress(item_id, message)
                    self._mark_goal_auto_turn_progress()
            elif method == "turn/started":
                raw_turn = params.get("turn")
                turn = raw_turn if isinstance(raw_turn, dict) else {}
                thread_id = params.get("threadId")
                turn_id = turn.get("id")
                if isinstance(thread_id, str) and isinstance(turn_id, str):
                    if self._event_is_stale_source_turn(thread_id, turn_id):
                        return
                    if self._can_bind_goal_turn(thread_id, turn_id):
                        if turn_id != self._goal_turn_id:
                            self._goal_turn_has_text = False
                        self._goal_turn_id = turn_id
                        if (
                            self._stream_thread_id != thread_id
                            or self._stream_turn_id != turn_id
                        ):
                            self._stream_thread_id = thread_id
                            self._stream_turn_id = turn_id
                            self._begin_codex_message()
                        self.goal_activity_label.setText(
                            self._goal_waiting_activity_text()
                        )
                    elif self._turn_state.observe_started(thread_id, turn_id):
                        if not self._is_stopping_turn():
                            self._bind_diagnostic_turn(thread_id, turn_id)
                            if self._stream_thread_id == thread_id:
                                if self._stream_turn_id in {None, turn_id}:
                                    self._stream_turn_id = turn_id
                                else:
                                    self._request_session_reconciliation(
                                        "mismatched_stream_turn_started"
                                    )
                            self.turn_status_label.setText("Turn：运行中")
                            self.turn_status_label.setToolTip(
                                f"Codex Turn ID：{turn_id}"
                            )
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
                    if self._event_is_stale_source_turn(thread_id, turn_id):
                        return
                    if self._goal_turn_matches(thread_id, turn_id):
                        turn_status = (
                            turn.get("status")
                            if isinstance(turn.get("status"), str)
                            else None
                        )
                        if (
                            not self._turn_state.busy
                            and self._stream_thread_id == thread_id
                            and self._stream_turn_id == turn_id
                        ):
                            self._finish_codex_message()
                            self._stream_thread_id = None
                            self._stream_turn_id = None
                        self._goal_turn_id = None
                        self._goal_turn_has_text = False
                        self.goal_activity_label.setText(
                            self._goal_waiting_activity_text()
                        )
                        self._queue_goal_continuation(
                            thread_id,
                            turn_id,
                            turn_status,
                        )
                    elif self._turn_state.observe_completed(thread_id, turn_id):
                        auto_turn = self._goal_auto_turn_is_current(thread_id)
                        turn_status = (
                            turn.get("status")
                            if isinstance(turn.get("status"), str)
                            else None
                        )
                        self._mark_turn_terminal(
                            turn_status
                        )
                        self._queue_goal_continuation(
                            thread_id,
                            turn_id,
                            turn_status,
                            auto_turn=auto_turn,
                        )
                        self._refresh_controls()
                    elif self._turn_state.phase in {
                        TurnPhase.STARTING,
                        TurnPhase.RECONCILING,
                    }:
                        self._request_session_reconciliation(
                            "unmatched_turn_completed"
                        )
                else:
                    self._request_session_reconciliation(
                        "unmatched_turn_completed"
                    )
            elif method == "thread/compacted":
                thread_id = params.get("threadId")
                turn_id = params.get("turnId")
                self._show_compaction_notice(
                    f"{thread_id or ''}:{turn_id or 'compaction'}"
                )
            elif method == "thread/name/updated":
                if "threadName" in params:
                    self._update_history_name(
                        params.get("threadId"), params.get("threadName")
                    )
            elif method in _PASSIVE_STATUS_NOTIFICATIONS:
                # P1 receive-only observation.  These notifications never
                # authorize a request, remote control, or a new MCP tool.
                pass
            elif method in {"error", "warning", "guardianWarning", "configWarning"}:
                self._show_codex_notice(method, params)
        elif event_type == "server_request":
            self._pending_approvals.append(event)
            self._show_next_approval()
        elif event_type == "protocol_warning":
            self._show_protocol_notice(event)
        elif event_type == "process_exit":
            if self._stop_recovery_state == "recovering":
                self._connected = False
                self._set_status_indicator(
                    self.connection_label,
                    "Codex",
                    "恢复中",
                    False,
                    "旧 app-server 已退出，正在恢复同一 Thread",
                )
                self.goal_activity_label.setText(
                    self._goal_waiting_activity_text()
                )
                self._refresh_controls()
                return
            self._polling_enabled = False
            self._reconnecting = False
            self._reconnect_timer.stop()
            if (
                self._turn_state.busy
                and isinstance(self._selected_thread_id, str)
                and self._turn_state.thread_id == self._selected_thread_id
            ):
                self._freeze_codex_message()
                self.turn_status_label.setText(
                    "Turn：状态待确认（app-server 已退出）"
                )
                self._diagnostic_snapshot.update(
                    {
                        "status": "failed",
                        "stage": "Codex app-server",
                        "actual": "Codex app-server 在活动 Turn 中退出",
                        "error_code": "CODEX_PROCESS_EXITED",
                        "error_text": "Codex app-server 在活动 Turn 中退出",
                        "impact": "活动 Turn 无法继续，场景结果需要人工确认",
                    }
                )
                self._write_runtime_diagnostic(
                    {
                        "status": "failed",
                        "stage": "Codex app-server",
                        "error_code": "CODEX_PROCESS_EXITED",
                        "error_text": "Codex app-server 在活动 Turn 中退出",
                        "impact": "活动 Turn 无法继续，场景结果需要人工确认",
                        "next_step": "重新连接后检查当前场景，再决定是否重试",
                    },
                    slug="codex-process-exit",
                )
            self._set_connection("app-server 已退出", False)
            self._set_mcp_status(self._mcp_backend, False)
            if not self._app_server_exit_notice_shown:
                self._app_server_exit_notice_shown = True
                if isinstance(self._selected_thread_id, str):
                    self._append_system(
                        "Codex app-server 已退出，请重启 launcher；"
                        "不会自动重放状态不明的 Turn 或 Houdini 操作。"
                    )
            self._refresh_controls()

    def _show_next_approval(self) -> None:
        if self._current_approval is not None or not self._pending_approvals:
            return
        self._current_approval = self._pending_approvals.popleft()
        card = format_approval_card(self._current_approval)
        self._current_approval_offers_persistent_rule = (
            card.offers_persistent_rule
        )
        self.approval_text.setPlainText(card.summary)
        self.approval_details_text.setPlainText(card.advanced_details)
        self.approval_details_button.setChecked(False)
        self.approval_details_button.setText("高级详情")
        self.approval_details_button.setVisible(True)
        self.approval_details_text.setVisible(False)
        self.persistent_allow_note.setVisible(False)
        self.persistent_allow_button.setVisible(False)
        self.approval_group.setVisible(True)

    def _toggle_approval_details(self, checked: bool) -> None:
        self.approval_details_button.setText(
            "收起高级详情" if checked else "高级详情"
        )
        self.approval_details_text.setVisible(bool(checked))
        show_persistent = bool(checked) and bool(
            self._current_approval_offers_persistent_rule
        )
        self.persistent_allow_note.setVisible(show_persistent)
        self.persistent_allow_button.setVisible(show_persistent)

    def _resolve_approval(self, decision: str) -> None:
        if self._current_approval is None or self._client is None:
            return
        self.allow_button.setEnabled(False)
        self.deny_button.setEnabled(False)
        self.persistent_allow_button.setEnabled(False)
        self._client.resolve_approval(
            self._current_approval.get("request_id"),
            decision,
        )

    @QtCore.Slot(str, dict)
    def _on_request_failed(self, context: str, payload: dict[str, Any]) -> None:
        if context == _HOUDINI_STATUS_CONTEXT:
            self._houdini_status_pending = False
            self._houdini_status_turn_token = None
            self._apply_houdini_status(
                {"backend": "hia_v2", "scene_revision": None}
            )
            return
        if context == _SCENE_CAPABILITY_CONTEXT:
            self._scene_capability_pending = False
            self._pending_houdini_report_identity = None
            error = payload.get("structured_error")
            code = error.get("code") if isinstance(error, dict) else None
            self._fail_closed_houdini_status(
                f"Catalog：{code}" if isinstance(code, str) else "Catalog：同步失败"
            )
            return
        if context == _SCENE_WORK_CONTEXT:
            self._scene_work_pending = False
            self._fail_closed_houdini_status("Catalog：工作请求失败")
            if self._turn_state.busy:
                error = payload.get("structured_error")
                self._record_final_runtime_failure(
                    "Houdini 工作请求",
                    self._diagnostic_error_code(error, "HOUDINI_WORK_FAILED"),
                    format_bridge_error(payload),
                    slug="houdini-work-failure",
                )
            return
        if context.startswith(_SCENE_RESULT_CONTEXT_PREFIX):
            self._fail_closed_houdini_status("Catalog：结果提交失败")
            if self._turn_state.busy:
                error = payload.get("structured_error")
                self._record_final_runtime_failure(
                    "Bridge 结果提交",
                    self._diagnostic_error_code(error, "BRIDGE_RESULT_FAILED"),
                    format_bridge_error(payload),
                    slug="bridge-result-failure",
                )
            return
        if context == _MODELS_CONTEXT:
            model_error = payload.get("structured_error")
            model_error_code = (
                model_error.get("code") if isinstance(model_error, dict) else None
            )
            self._apply_models([])
            if model_error_code in _RECONNECTABLE_ERROR_CODES:
                self._models_requested = False
                self._models_resolved = False
                self._schedule_bridge_reconnect()
                self._refresh_controls()
                return
            self._models_resolved = True
            if isinstance(self._selected_thread_id, str):
                self._append_system("模型列表暂不可用，继续使用 Codex 默认。")
            self._refresh_controls()
            return

        error = payload.get("structured_error") or {}
        details = error.get("details") if isinstance(error, dict) else {}
        details = details if isinstance(details, dict) else {}
        error_code = error.get("code") if isinstance(error, dict) else None
        formatted_error = format_bridge_error(payload)

        if (
            context == _GOAL_GET_CONTEXT
            and isinstance(self._crash_recovery_marker, dict)
            and isinstance(self._crash_recovery_health_session, dict)
            and self._selected_thread_id is None
        ):
            if context == self._goal_action_context:
                self._goal_action_context = None
            self._discard_crash_recovery_candidate()
            self._refresh_controls()
            return
        if context == _CRASH_RECOVERY_READ_CONTEXT:
            self._discard_crash_recovery_candidate()
            self._refresh_controls()
            return
        if context == _CRASH_RECOVERY_RECHECK_CONTEXT:
            self._crash_recovery_observation = None
            self._refresh_controls()
            return

        if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
            pending = self._pending_steer_reconciliation(context)
            if pending is not None:
                steer_context, draft = pending
                self._reconciliation_tokens.pop(context, None)
                if draft.get("fallback_cancelled") is True:
                    self._pending_steer_drafts.pop(steer_context, None)
                    self._refresh_controls()
                    return
                self._restore_steer_draft_to_composer(draft)
                self._pending_steer_drafts.pop(steer_context, None)
                self._append_system(
                    "当前 Turn 状态尚未同步；追加文字和图片已保留，未自动重试。"
                )
                if error_code in _RECONNECTABLE_ERROR_CODES:
                    self._schedule_bridge_reconnect()
                self._refresh_controls()
                return

        if context in {
            _GOAL_GET_CONTEXT,
            _GOAL_SET_CONTEXT,
            _GOAL_CLEAR_CONTEXT,
            _FOCUS_SET_CONTEXT,
        }:
            if context != self._goal_action_context:
                return
            self._goal_action_context = None
            if context == _FOCUS_SET_CONTEXT:
                self._apply_focus_mode(
                    self._selected_thread_id,
                    self._focus_mode,
                )
                self.goal_focus_hint_label.setText(
                    "请确认当前 Thread 已保存状态为“进行中”的 Goal 后重试。"
                    if error_code == "ACTIVE_GOAL_REQUIRED"
                    else f"目标专注模式更新失败：{formatted_error}"
                )
            else:
                self._append_system(f"Goal 操作失败：{formatted_error}")
            self._refresh_controls()
            return
        reconnect_context = (
            context in {"health", "session", "events"}
            or context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX)
        )
        if error_code in _RECONNECTABLE_ERROR_CODES and reconnect_context:
            if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
                self._reconciliation_tokens.pop(context, None)
            self._schedule_bridge_reconnect()
            self._refresh_controls()
            return

        if context.startswith(_INTERRUPT_CONTEXT_PREFIX):
            token = self._interrupt_tokens.pop(context, None)
            if context == self._active_interrupt_context:
                self._active_interrupt_context = None
                self._interrupt_pending = False
            valid_stop = (
                isinstance(token, TurnStateToken)
                and token.thread_id == self._selected_thread_id
                and self._stop_recovery_state == "recovering"
            )
            if (
                valid_stop
                and error_code == "NO_ACTIVE_TURN"
                and details.get("turn_active") is False
                and details.get("thread_id") == token.thread_id
            ):
                self._stop_recovery_state = None
                self._connected = True
                self._set_status_indicator(
                    self.connection_label,
                    "Codex",
                    "已连接",
                    True,
                    "Codex app-server 会话状态",
                )
                self.goal_activity_label.setText(
                    self._goal_waiting_activity_text()
                )
                self.allow_button.setEnabled(True)
                self.deny_button.setEnabled(True)
                self._refresh_controls()
                return
            if valid_stop:
                if details.get("turn_status") == "stopRecoveryFailed":
                    self._apply_session(
                        {
                            "connected": False,
                            "authentication": "unavailable",
                            "thread_id": token.thread_id,
                            "turn_id": None,
                            "turn_status": "stopRecoveryFailed",
                            "turn_active": False,
                            "focus_mode": self._focus_mode,
                        },
                        token=self._turn_state.capture_token(),
                        allow_followup=True,
                    )
                else:
                    self._schedule_bridge_reconnect()
                self.allow_button.setEnabled(True)
                self.deny_button.setEnabled(True)
                self._refresh_controls()
                return
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
            self._refresh_controls()
            return

        if context.startswith(_SESSION_RECONCILE_CONTEXT_PREFIX):
            self._reconciliation_tokens.pop(context, None)
            if (
                self._turn_state.phase is TurnPhase.RECONCILING
                and self._turn_state.turn_id is None
            ):
                self._record_final_runtime_failure(
                    "Turn 状态同步",
                    str(error_code or "TURN_STATE_UNCERTAIN"),
                    formatted_error,
                    slug="turn-state-uncertain",
                    recovery="限定状态同步失败，Turn 是否已建立仍不确定",
                    impact="当前场景可能已修改，也可能尚未开始执行",
                    next_step="不要盲目重试；先检查当前场景与 Bridge 会话状态",
                )

        if context.startswith(_TURN_STEER_CONTEXT_PREFIX):
            token = self._turn_steer_tokens.pop(context, None)
            if context == self._active_turn_steer_context:
                self._active_turn_steer_context = None
                self._turn_steer_request_pending = False
            if not isinstance(token, TurnStateToken):
                self._refresh_controls()
                return
            pending_draft = self._pending_steer_drafts.get(context)
            if (
                isinstance(pending_draft, dict)
                and pending_draft.get("fallback_cancelled") is True
            ):
                self._pending_steer_drafts.pop(context, None)
                self._refresh_controls()
                return
            if (
                isinstance(pending_draft, dict)
                and pending_draft.get("retry_attempted") is True
            ):
                self._restore_steer_draft_to_composer(pending_draft)
                self._pending_steer_drafts.pop(context, None)
                self._append_system(
                    "当前 Turn 在同步后再次变化；追加文字和图片已保留，未再次重试。"
                )
                self.allow_button.setEnabled(True)
                self.deny_button.setEnabled(True)
                self._refresh_controls()
                return
            if error_code in {"NO_ACTIVE_TURN", "STALE_ACTIVE_TURN"}:
                if not self._begin_steer_state_sync(
                    context,
                    token,
                    str(error_code),
                    details,
                ):
                    self._restore_steer_draft_to_composer(pending_draft)
                    self._pending_steer_drafts.pop(context, None)
                    self._append_system(
                        "当前 Turn 状态已变化；输入和附件已保留，未自动重试。"
                    )
                self.allow_button.setEnabled(True)
                self.deny_button.setEnabled(True)
                self._refresh_controls()
                return

            self._pending_steer_drafts.pop(context, None)
            if error_code == "TURN_NOT_STEERABLE":
                turn_kind = details.get("turn_kind")
                label = "review" if turn_kind == "review" else "compact"
                self._append_system(f"当前 {label} Turn 暂不能追加指令。")
            else:
                self._append_system(
                    f"追加指令失败：{formatted_error}"
                )
                self._record_final_runtime_failure(
                    "追加当前 Turn",
                    str(error_code or "TURN_STEER_FAILED"),
                    formatted_error,
                    slug="turn-steer-failure",
                )
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
            self._refresh_controls()
            return

        if context == _THREADS_CONTEXT:
            if error_code in _RECONNECTABLE_ERROR_CODES:
                self._threads_requested = False
                self._schedule_bridge_reconnect()
                self._refresh_controls()
                return
            code_text = (
                " ".join(error_code.split())[:80]
                if isinstance(error_code, str) and error_code.strip()
                else "BRIDGE_REQUEST_FAILED"
            )
            field = details.get("field")
            field_text = (
                " ".join(field.split())[:80]
                if isinstance(field, str) and field.strip()
                else None
            )
            diagnostic = code_text + (
                f"，field={field_text}" if field_text is not None else ""
            )
            if isinstance(self._selected_thread_id, str):
                self._append_system(
                    f"历史会话暂不可用（{diagnostic}）；可稍后手动刷新。"
                )
            self._refresh_controls()
            return
        if context.startswith(_THREAD_READ_CONTEXT_PREFIX):
            if error_code in _SESSION_WAIT_TIMEOUT_CODES:
                self._append_system(
                    f"会话恢复超时（{error_code}）：会话服务暂未完成；可稍后重试。"
                )
            else:
                self._append_system("会话状态已恢复，但历史内容读取失败。")
            self._refresh_controls()
            return
        if context.startswith(_THREAD_RENAME_CONTEXT_PREFIX):
            self._session_action_pending = False
            self._append_system(f"重命名失败：{formatted_error}")
            self._refresh_controls()
            return
        if (
            context in {"session_start", "session_resume"}
            and error_code in _SESSION_WAIT_TIMEOUT_CODES
        ):
            self._session_action_pending = False
            action_label = "会话启动" if context == "session_start" else "会话恢复"
            self._append_system(
                f"{action_label}超时（{error_code}）：会话服务暂未完成；可稍后重试。"
            )
            self._refresh_controls()
            return

        if (
            isinstance(self._selected_thread_id, str)
            or context not in {"health", "session", "events"}
        ):
            self._append_system(f"{context} 失败：{formatted_error}")
        if context in {"health", "session"}:
            self._set_connection("连接失败", False)
            self._set_mcp_status(self._mcp_backend, False)
        if context == "events":
            self._schedule_poll(1500)
        elif context in {"session_start", "session_resume"}:
            self._session_action_pending = False
        elif context.startswith(_TURN_START_CONTEXT_PREFIX):
            token = self._turn_start_tokens.pop(context, None)
            failed_draft = self._pending_turn_drafts.get(context)
            if (
                isinstance(failed_draft, dict)
                and failed_draft.get("goal_auto_continue") is True
            ):
                self._pause_goal_continuation("", notify=False)
            if context == self._active_turn_start_context:
                self._active_turn_start_context = None
                self._turn_start_request_pending = False
            active_turn = details.get("turn_active") is True
            turn_not_created = details.get("turn_created") is False
            if active_turn and not turn_not_created:
                self._accept_sent_draft(context)
            failure_thread_id = details.get("thread_id")
            failure_turn_id = details.get("turn_id")
            state_reconciled = False
            if turn_not_created and active_turn:
                self._pending_turn_drafts.pop(context, None)
                if (
                    isinstance(token, TurnStateToken)
                    and isinstance(failure_thread_id, str)
                    and isinstance(failure_turn_id, str)
                ):
                    state_reconciled = self._turn_state.acknowledge_start(
                        token,
                        failure_thread_id,
                        failure_turn_id,
                    )
                if state_reconciled:
                    self._stream_thread_id = failure_thread_id
                    self._stream_turn_id = failure_turn_id
                self._append_system(
                    "已有另一轮开始；本次文字和图片已保留，未重复发送。"
                )
            elif active_turn and isinstance(token, TurnStateToken):
                if (
                    isinstance(failure_thread_id, str)
                    and isinstance(failure_turn_id, str)
                ):
                    state_reconciled = self._turn_state.acknowledge_start(
                        token,
                        failure_thread_id,
                        failure_turn_id,
                    )
            elif turn_not_created:
                self._pending_turn_drafts.pop(context, None)
                self._stream_thread_id = None
                self._stream_turn_id = None
                self._finish_codex_message()
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
                self._record_final_runtime_failure(
                    "Turn 建立",
                    str(error_code or "TURN_START_FAILED"),
                    formatted_error,
                    slug="turn-start-failure",
                    recovery="Bridge 已确认 Turn 未创建",
                    impact="本次请求未开始，输入和附件已保留",
                    next_step="检查 Bridge/Codex 错误后重新发送",
                )
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
        self.allow_button.setEnabled(True)
        self.deny_button.setEnabled(True)
        self.persistent_allow_button.setEnabled(True)
        self._refresh_controls()

    def _request_session_reconciliation(self, reason: str) -> str | None:
        """Issue at most one correlated session GET for the current generation."""

        if self._client is None or self._reconciliation_tokens:
            return None
        token = self._turn_state.claim_reconciliation(reason)
        if token is None:
            return None
        context = (
            f"{_SESSION_RECONCILE_CONTEXT_PREFIX}"
            f"{token.generation}:{token.revision}:{reason}"
        )
        self._reconciliation_tokens[context] = token
        self._refresh_controls()
        self._client.get_session(context=context)
        return context

    @QtCore.Slot(int)
    def _on_model_changed(self, _index: int = -1) -> None:
        self._update_reasoning_efforts()
        self._update_service_tiers()
        self._refresh_controls()

    @QtCore.Slot(int)
    def _on_service_tier_changed(self, _index: int = -1) -> None:
        record = self.service_tier_combo.currentData()
        description = record.get("description") if isinstance(record, dict) else None
        self.service_tier_combo.setToolTip(
            description
            if isinstance(description, str) and description
            else "标准：不覆盖服务速度；可用档位来自实时 model/list。"
        )

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
        self._update_service_tiers()

    def _selected_model_record(self) -> dict[str, Any] | None:
        record = self.model_combo.currentData()
        return record if isinstance(record, dict) else None

    def _selected_model_id(self) -> str | None:
        record = self._selected_model_record()
        model_id = record.get("model") if record is not None else None
        return model_id if isinstance(model_id, str) and model_id else None

    def _selected_model_supports_images(self) -> bool:
        record = self._selected_model_record()
        if record is None:
            return True
        modalities = record.get("inputModalities")
        return isinstance(modalities, list) and "image" in modalities

    def _selected_effort(self) -> str | None:
        effort = self.effort_combo.currentData()
        return effort if isinstance(effort, str) and effort else None

    def _selected_service_tier(self) -> str | None:
        record = self.service_tier_combo.currentData()
        tier_id = record.get("id") if isinstance(record, dict) else None
        return tier_id if isinstance(tier_id, str) and tier_id else None

    def _update_service_tiers(self) -> None:
        previous_tier = self._selected_service_tier()
        record = self._selected_model_record()
        raw_tiers = record.get("serviceTiers", []) if record is not None else []
        default_tier = record.get("defaultServiceTier") if record is not None else None
        tiers = raw_tiers if isinstance(raw_tiers, list) else []

        self.service_tier_combo.blockSignals(True)
        self.service_tier_combo.clear()
        self.service_tier_combo.addItem(_CODEX_STANDARD_TIER_LABEL, None)
        previous_index: int | None = None
        default_index: int | None = None
        seen: set[str] = set()
        for raw_tier in tiers:
            if not isinstance(raw_tier, dict):
                continue
            tier_id = raw_tier.get("id")
            name = raw_tier.get("name")
            if not isinstance(tier_id, str) or not tier_id or tier_id in seen:
                continue
            seen.add(tier_id)
            tier_record = dict(raw_tier)
            label = name if isinstance(name, str) and name else tier_id
            self.service_tier_combo.addItem(label, tier_record)
            index = self.service_tier_combo.count() - 1
            if tier_id == previous_tier:
                previous_index = index
            if tier_id == default_tier:
                default_index = index
        selected_index = (
            previous_index
            if previous_index is not None
            else default_index if default_index is not None else 0
        )
        self.service_tier_combo.setCurrentIndex(selected_index)
        self.service_tier_combo.blockSignals(False)
        available = self.service_tier_combo.count() > 1
        self.service_tier_label.setVisible(available)
        self.service_tier_combo.setVisible(available)
        self._on_service_tier_changed(selected_index)

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

    def _begin_turn_performance(self, token: TurnStateToken) -> None:
        self._turn_performance_token = token
        self._turn_performance_marks = {"sent": time.monotonic()}
        self._render_turn_performance()

    def _clear_turn_performance(self) -> None:
        self._turn_performance_token = None
        self._turn_performance_marks = {}
        self._render_turn_performance()

    def _record_turn_performance(
        self,
        stage: str,
        *,
        token: TurnStateToken | None = None,
    ) -> None:
        active_token = self._turn_performance_token
        if not isinstance(active_token, TurnStateToken):
            return
        if token is not None and token != active_token:
            return
        if active_token.thread_id != self._selected_thread_id:
            return
        if stage not in {"ack", "first_delta", "completed"}:
            return
        if stage not in self._turn_performance_marks:
            self._turn_performance_marks[stage] = time.monotonic()
        self._render_turn_performance()

    @staticmethod
    def _duration_text(start: Any, end: Any, *, early_text: str) -> str:
        if not isinstance(start, float) or not isinstance(end, float):
            return "—"
        delta = end - start
        if delta < 0:
            return early_text
        return f"{delta:.2f}s"

    def _render_turn_performance(self) -> None:
        label = getattr(self, "performance_label", None)
        if label is None:
            return
        marks = self._turn_performance_marks
        label.setText(
            "发送 → ACK："
            + self._duration_text(
                marks.get("sent"), marks.get("ack"), early_text="—"
            )
            + "\nACK → 首个文本："
            + self._duration_text(
                marks.get("ack"),
                marks.get("first_delta"),
                early_text="首个文本先于 ACK 到达",
            )
            + "\n首个文本 → 完成："
            + self._duration_text(
                marks.get("first_delta"),
                marks.get("completed"),
                early_text="—",
            )
        )

    def _mark_turn_terminal(self, status: str | None) -> None:
        """Clear request-only UI locks after an authoritative terminal state."""

        self._record_turn_performance("completed")
        self._finalize_diagnostic_turn(status)
        self._turn_start_request_pending = False
        self._active_turn_start_context = None
        self._interrupt_pending = False
        stopping_token = self._stopping_turn_token
        stopped_by_request = isinstance(stopping_token, TurnStateToken)
        active_interrupt_context = self._active_interrupt_context
        if active_interrupt_context is not None:
            self._interrupt_tokens.pop(active_interrupt_context, None)
        self._active_interrupt_context = None
        if isinstance(stopping_token, TurnStateToken):
            for context, token in tuple(self._reconciliation_tokens.items()):
                if token == stopping_token:
                    self._reconciliation_tokens.pop(context, None)
            for context, draft in tuple(self._pending_steer_drafts.items()):
                if (
                    isinstance(draft, dict)
                    and draft.get("fallback_cancelled") is True
                ):
                    self._pending_steer_drafts.pop(context, None)
                    self._turn_steer_tokens.pop(context, None)
        self._stopping_turn_token = None
        self.turn_status_label.setText(
            "Turn：已停止"
            if stopped_by_request
            else self._turn_status_text(status or "completed", active=False)
        )
        self._finish_codex_message()
        self._stream_thread_id = None
        self._stream_turn_id = None
        self.goal_activity_label.setText(self._goal_waiting_activity_text())

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

    def _schedule_bridge_reconnect(self) -> None:
        if self._client is None:
            return
        timer = getattr(self, "_reconnect_timer", None)
        if timer is None:
            return
        if timer.isActive():
            return

        first_attempt = not self._reconnecting
        self._reconnecting = True
        self._polling_enabled = False
        self._houdini_polling_enabled = False
        self._houdini_status_pending = False
        self._houdini_status_turn_token = None
        self._poll_timer.stop()
        self._scene_work_timer.stop()
        self._scene_capability_pending = False
        self._scene_work_pending = False
        if self._stop_recovery_state == "recovering":
            self._connected = False
            self._set_status_indicator(
                self.connection_label,
                "Codex",
                "恢复中",
                False,
                "Stop 响应未确认，正在通过 Bridge health 同步恢复状态",
            )
        else:
            self._set_connection("Bridge 连接中断，正在重连", False)
        self._set_mcp_status(self._mcp_backend, False)
        if first_attempt:
            if isinstance(self._selected_thread_id, str):
                self._append_system(
                    "Bridge 连接中断，正在有限重连；草稿、附件和当前会话已保留。"
                )

        if self._reconnect_attempt >= len(_RECONNECT_DELAYS_MS):
            if not self._reconnect_exhausted_notice_shown:
                self._reconnect_exhausted_notice_shown = True
                if isinstance(self._selected_thread_id, str):
                    self._append_system(
                        "自动重连未成功，请重启 launcher；不会自动重放 Turn。"
                    )
            return

        delay_ms = _RECONNECT_DELAYS_MS[self._reconnect_attempt]
        self._reconnect_attempt += 1
        timer.start(delay_ms)

    @QtCore.Slot()
    def _attempt_bridge_reconnect(self) -> None:
        if not self._reconnecting or self._client is None:
            return
        if self._client.get_health() is None:
            self._schedule_bridge_reconnect()

    def _schedule_poll(self, delay_ms: int) -> None:
        if not self._polling_enabled or self._client is None:
            return
        timer = getattr(self, "_poll_timer", None)
        if timer is None:
            QtCore.QTimer.singleShot(delay_ms, self._poll_once)
            return
        timer.stop()
        timer.start(max(0, int(delay_ms)))

    @QtCore.Slot()
    def _poll_once(self) -> None:
        if not self._polling_enabled or self._client is None:
            return
        self._client.poll_events(self._event_sequence)

    def _append_system(self, text: str) -> None:
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        if hasattr(self.conversation, "add_system_message"):
            self.conversation.add_system_message(text)
        else:
            self.conversation.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self.conversation.insertPlainText(f"\n[System] {text}\n")

    def _append_protocol_warning(self, key: str, text: str) -> None:
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        if hasattr(self.conversation, "add_protocol_warning"):
            self.conversation.add_protocol_warning(key, text)
        else:
            self._append_system(text)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._polling_enabled = False
        self._houdini_polling_enabled = False
        self._local_houdini_polling_enabled = False
        self._houdini_status_pending = False
        self._houdini_status_turn_token = None
        for timer_name in (
            "_poll_timer",
            "_houdini_heartbeat_timer",
            "_scene_work_timer",
            "_reconnect_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        self._interrupt_pending = False
        self._interrupt_tokens.clear()
        self._active_interrupt_context = None
        self._reconciliation_tokens.clear()
        self._stopping_turn_token = None
        self._goal_continuation_boundary = None
        self._goal_auto_turn_token = None
        conversation = getattr(self, "conversation", None)
        if conversation is not None and hasattr(conversation, "stop_timers"):
            conversation.stop_timers()
        dialog = getattr(self, "_attachment_dialog", None)
        if dialog is not None:
            dialog.close()
        self._attachment_dialog = None
        self._scene_capability_pending = False
        self._scene_work_pending = False
        self._scene_attestation_digest = None
        self._scene_catalog_digest = None
        self._last_houdini_report = None
        self._attested_houdini_report_identity = None
        self._pending_houdini_report_identity = None
        adapter = getattr(self, "_houdini_adapter", None)
        self._houdini_adapter = None
        if adapter is not None:
            try:
                adapter.dispose()
            except HoudiniReadAdapterError:
                pass
        client = self._client
        self._client = None
        if client is not None:
            client.dispose()
        super().closeEvent(event)
