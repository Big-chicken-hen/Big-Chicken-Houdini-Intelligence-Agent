"""PySide6 Houdini Python Panel for the P1-V protocol slice."""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from collections import deque
from typing import Any

import PySide6
from PySide6 import QtCore, QtGui, QtWidgets

from .attachment_store import AttachmentStore
from .bridge_client import BridgeClient
from .houdini_read_adapter import HoudiniReadAdapter, HoudiniReadAdapterError
from .network_response import format_bridge_error
from .turn_state import PanelTurnState, TurnPhase, TurnStateToken


_PASSIVE_STATUS_NOTIFICATIONS = frozenset(
    {
        "remoteControl/status/changed",
        "mcpServer/startupStatus/updated",
        "account/rateLimits/updated",
    }
)
_TURN_START_CONTEXT_PREFIX = "turn_start:"
_TURN_STEER_CONTEXT_PREFIX = "turn_steer:"
_INTERRUPT_CONTEXT_PREFIX = "interrupt:"
_SESSION_RECONCILE_CONTEXT_PREFIX = "session_reconcile:"
_MODELS_CONTEXT = "models"
_CODEX_DEFAULT_LABEL = "Codex 默认"
_SCENE_CAPABILITY_CONTEXT = "scene_capabilities"
_SCENE_WORK_CONTEXT = "scene_work"
_SCENE_RESULT_CONTEXT_PREFIX = "scene_result:"
_SCENE_HEARTBEAT_MS = 1_000
_SCENE_IDLE_POLL_MS = 100
_MAX_TURN_IMAGES = 16


class HoudiniIntelligencePanel(QtWidgets.QWidget):
    """Conversation UI with current-session Houdini and MCP status."""

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
        self._authenticated = False
        self._selected_thread_id: str | None = None
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
        self._reconciliation_tokens: dict[str, TurnStateToken] = {}
        self._models_requested = False
        self._pending_approvals: deque[dict[str, Any]] = deque()
        self._current_approval: dict[str, Any] | None = None
        self._houdini_adapter: HoudiniReadAdapter | None = None
        self._houdini_polling_enabled = False
        self._scene_capability_pending = False
        self._scene_work_pending = False
        self._scene_attestation_digest: str | None = None
        self._scene_catalog_digest: str | None = None
        self._last_houdini_report: dict[str, Any] | None = None
        self._attested_houdini_report_identity: str | None = None
        self._pending_houdini_report_identity: str | None = None
        self._selected_node_paths: tuple[str, ...] = ()
        self._attachment_store = AttachmentStore()
        self._scene_executor_token = os.environ.get("HIA_SCENE_EXECUTOR_TOKEN", "")
        self._build_ui()
        self._initialize_houdini_read_adapter(hou_module)
        self._refresh_selection_status()

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
        self.houdini_mcp_label = QtWidgets.QLabel("● 实时 MCP：不可用")
        self.houdini_mcp_label.setToolTip("FXHoudini MCP 是否可用于当前会话")
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
            "Revision：不可用  ·  Dirty：不可用"
        )
        self.houdini_scene_label.setToolTip("当前 HIP 的 revision 与未保存状态")
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
        root.addLayout(session_row)

        thread_row = QtWidgets.QHBoxLayout()
        self.thread_id_edit = QtWidgets.QLineEdit()
        self.thread_id_edit.setPlaceholderText("Codex Thread ID（恢复时填写）")
        self.new_thread_button = QtWidgets.QPushButton("新建 Thread")
        self.resume_thread_button = QtWidgets.QPushButton("恢复 Thread")
        thread_row.addWidget(self.thread_id_edit, 1)
        thread_row.addWidget(self.new_thread_button)
        thread_row.addWidget(self.resume_thread_button)
        root.addLayout(thread_row)

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
        root.addWidget(self.welcome_group)

        self.conversation = ConversationView(self)
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

        selection_row = QtWidgets.QHBoxLayout()
        self.selection_label = QtWidgets.QLabel("当前选择：无")
        self.selection_label.setToolTip("从 Houdini 主线程只读获取的当前节点选择")
        self.include_selection_checkbox = QtWidgets.QCheckBox("包含当前选择")
        self.include_selection_checkbox.setChecked(False)
        selection_row.addWidget(self.selection_label, 1)
        selection_row.addWidget(self.include_selection_checkbox)
        root.addLayout(selection_row)

        self.attachment_strip = AttachmentStrip(self)
        root.addWidget(self.attachment_strip)

        self.input_edit = ExpandableTextEdit(self)
        self.input_edit.setPlaceholderText(
            "输入自然语言请求；Enter 换行，Ctrl+Enter 发送。"
        )
        root.addWidget(self.input_edit)

        action_row = QtWidgets.QHBoxLayout()
        self.add_image_button = QtWidgets.QPushButton("添加图片")
        self.send_button = QtWidgets.QPushButton("发送")
        self.stop_button = QtWidgets.QPushButton("停止")
        action_row.addWidget(self.add_image_button)
        action_row.addStretch(1)
        action_row.addWidget(self.send_button)
        action_row.addWidget(self.stop_button)
        root.addLayout(action_row)

        self.new_thread_button.clicked.connect(self._new_thread)
        self.resume_thread_button.clicked.connect(self._resume_thread)
        self.add_image_button.clicked.connect(self._choose_images)
        self.input_edit.sendRequested.connect(self._send)
        self.input_edit.imagePasted.connect(self._add_clipboard_image)
        self.send_button.clicked.connect(self._send)
        self.stop_button.clicked.connect(self._stop)
        self.allow_button.clicked.connect(lambda: self._resolve_approval("allow"))
        self.deny_button.clicked.connect(lambda: self._resolve_approval("deny"))
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
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
            return
        del attested, pending
        revision = report.get("scene_revision")
        dirty = self._read_dirty_state()
        houdini_connection_label = getattr(self, "houdini_connection_label", None)
        if houdini_connection_label is not None:
            self._set_status_indicator(
                houdini_connection_label, "Houdini", "已连接", True
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
                f"Revision：{revision_text}  ·  Dirty：{dirty_text}"
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
            self._houdini_polling_enabled
            or self._houdini_adapter is None
            or self._client is None
        ):
            return
        self._houdini_polling_enabled = True
        self._schedule_houdini_heartbeat(0)

    def _schedule_houdini_heartbeat(self, delay_ms: int) -> None:
        if not self._houdini_polling_enabled:
            return
        QtCore.QTimer.singleShot(delay_ms, self._houdini_heartbeat)

    @QtCore.Slot()
    def _houdini_heartbeat(self) -> None:
        self._refresh_selection_status()
        if (
            not self._houdini_polling_enabled
            or self._client is None
            or self._houdini_adapter is None
        ):
            return
        if not self._scene_capability_pending:
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

    def _schedule_scene_work_poll(self, delay_ms: int) -> None:
        if (
            not self._houdini_polling_enabled
            or self._scene_attestation_digest is None
        ):
            return
        QtCore.QTimer.singleShot(delay_ms, self._poll_scene_work)

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
            except (HoudiniReadAdapterError, TypeError, ValueError):
                self._fail_closed_houdini_status("Catalog：只读执行失败")
                return True
            submitted = self._client.complete_scene_work(
                request_id,
                executor_token,
                result,
            )
            if submitted is None:
                self._fail_closed_houdini_status("Catalog：结果提交失败")
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
        request_ready = session_enabled and not self._turn_steer_request_pending
        steer_available = controls.stop
        self.new_thread_button.setEnabled(controls.new_thread and session_enabled)
        self.resume_thread_button.setEnabled(controls.resume_thread and session_enabled)
        self.send_button.setEnabled(
            (controls.send or steer_available) and request_ready
        )
        self.stop_button.setEnabled(controls.stop and not self._interrupt_pending)
        self.send_button.setText(
            "发送中…"
            if self._turn_start_request_pending
            else (
                "追加中…"
                if self._turn_steer_request_pending
                else (
                    "追加指令"
                    if steer_available
                    else ("Codex 回复中…" if self._turn_state.busy else "发送")
                )
            )
        )
        self.thread_id_edit.setEnabled(not self._turn_state.busy and session_enabled)
        selection_enabled = (
            self._connected
            and not self._turn_state.busy
            and session_enabled
        )
        self.model_combo.setEnabled(selection_enabled)
        self.effort_combo.setEnabled(selection_enabled)
        composer_enabled = (
            (not self._turn_state.busy or steer_available)
            and request_ready
        )
        input_edit = getattr(self, "input_edit", None)
        if input_edit is not None:
            input_edit.setEnabled(composer_enabled)
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

    @QtCore.Slot(dict)
    def _on_health(self, payload: dict[str, Any]) -> None:
        self._set_connection("已连接（stdio JSONL）", True)
        houdini_mcp = payload.get("houdini_mcp")
        houdini_mcp_label = getattr(self, "houdini_mcp_label", None)
        if houdini_mcp_label is not None:
            mcp_available = (
                isinstance(houdini_mcp, dict)
                and houdini_mcp.get("available") is True
            )
            self._set_status_indicator(
                houdini_mcp_label,
                "实时 MCP",
                "可用" if mcp_available else "不可用",
                mcp_available,
                "FXHoudini MCP 当前会话状态",
            )
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
        self._start_houdini_read_loop()

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
        self._set_status_indicator(
            self.connection_label,
            "Codex",
            "已连接" if self._connected else "未连接",
            self._connected,
            "Codex app-server 会话状态",
        )
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
            if (
                isinstance(self._selected_thread_id, str)
                and self._selected_thread_id != thread_id
            ):
                self.attachment_strip.clear()
                self._stream_thread_id = None
                self._stream_turn_id = None
            self._selected_thread_id = thread_id
            self.thread_id_edit.setText(thread_id)
            self.thread_status_label.setText(f"Thread：{thread_id}")
        elif state_applied and not self._turn_state.busy:
            self._selected_thread_id = None
            self.thread_status_label.setText("Thread：未选择")

        if (
            state_applied
            and turn_active is True
            and isinstance(thread_id, str)
            and isinstance(turn_id, str)
        ):
            self._stream_thread_id = thread_id
            self._stream_turn_id = turn_id
        elif state_applied and turn_active is False and not self._turn_state.busy:
            self._stream_thread_id = None
            self._stream_turn_id = None

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

    def _fill_prompt(self, text: str) -> None:
        self.input_edit.setPlainText(text)

    def _read_selected_node_paths(self) -> tuple[str, ...]:
        hou_module = getattr(self, "_hou_module", None)
        if hou_module is None:
            return ()
        try:
            nodes = tuple(hou_module.selectedNodes())
            paths = tuple(node.path() for node in nodes)
        except Exception:
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
        paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "添加参考图片",
            r"E:\houdini-intelligence-agent",
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
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
            else:
                self._add_attachment_path(stored)

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
            return
        self._add_attachment_path(path)

    def _request_text_with_selection(self, text: str) -> str:
        checkbox = getattr(self, "include_selection_checkbox", None)
        if checkbox is None or not checkbox.isChecked():
            return text
        paths = self._read_selected_node_paths()
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

    def _event_matches_active_stream(
        self,
        params: dict[str, Any],
        *,
        require_item_id: bool = False,
    ) -> bool:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if (
            not self._turn_state.busy
            or not isinstance(thread_id, str)
            or not isinstance(turn_id, str)
            or thread_id != self._stream_thread_id
            or turn_id != self._stream_turn_id
        ):
            return False
        return not require_item_id or isinstance(params.get("itemId"), str)

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

    def _accept_sent_draft(self, context: str) -> None:
        draft = self._pending_turn_drafts.pop(context, None)
        if not isinstance(draft, dict):
            return
        text = draft.get("text")
        if isinstance(text, str) and self.input_edit.toPlainText() == text:
            self.input_edit.clear()
        paths = tuple(draft.get("attachment_paths") or ())
        if paths and paths == self._attachment_paths():
            self.attachment_strip.clear()

    def _accept_steered_draft(self, context: str) -> None:
        draft = self._pending_steer_drafts.pop(context, None)
        if not isinstance(draft, dict):
            return
        text = draft.get("text")
        paths = tuple(draft.get("attachment_paths") or ())
        if isinstance(text, str):
            self._add_user_message(text, paths, same_turn=True)
            if self.input_edit.toPlainText() == text:
                self.input_edit.clear()
        if paths and paths == self._attachment_paths():
            self.attachment_strip.clear()

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
        attachment_paths = self._attachment_paths()
        if not text.strip() and not attachment_paths:
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
        if not self._turn_state.begin_start(thread_id):
            self._append_system("当前 Turn 暂不能发送。")
            self._refresh_controls()
            return
        request_text = self._request_text_with_selection(text)
        self._add_user_message(text, attachment_paths)
        self._begin_codex_message()
        self._stream_thread_id = thread_id
        self._stream_turn_id = None
        welcome_group = getattr(self, "welcome_group", None)
        if welcome_group is not None:
            welcome_group.setVisible(False)
        self.turn_status_label.setText("Turn：正在创建")
        token = self._turn_state.capture_token()
        context = (
            f"{_TURN_START_CONTEXT_PREFIX}{token.generation}:{token.revision}"
        )
        self._turn_start_tokens[context] = token
        self._pending_turn_drafts[context] = {
            "text": text,
            "attachment_paths": attachment_paths,
        }
        self._active_turn_start_context = context
        self._turn_start_request_pending = True
        self._refresh_controls()
        self._client.start_turn(
            request_text,
            model=self._selected_model_id(),
            effort=self._selected_effort(),
            local_image_paths=list(attachment_paths),
            context=context,
        )

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
        self._turn_steer_tokens[context] = token
        self._pending_steer_drafts[context] = {
            "text": text,
            "attachment_paths": attachment_paths,
        }
        self._active_turn_steer_context = context
        self._turn_steer_request_pending = True
        self._refresh_controls()
        self._client.steer_turn(
            self._request_text_with_selection(text),
            local_image_paths=list(attachment_paths),
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
        if self._handle_scene_action(context, payload):
            return
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
                if (
                    isinstance(self._selected_thread_id, str)
                    and self._selected_thread_id != thread_id
                ):
                    self.attachment_strip.clear()
                self._selected_thread_id = thread_id
                self.thread_id_edit.setText(thread_id)
                self.thread_status_label.setText(f"Thread：{thread_id}")
                action = "已新建" if context == "session_start" else "已恢复"
                self._append_system(f"{action} Thread：{thread_id}")
        elif context.startswith(_TURN_START_CONTEXT_PREFIX):
            self._accept_sent_draft(context)
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
        elif context.startswith(_TURN_STEER_CONTEXT_PREFIX):
            token = self._turn_steer_tokens.pop(context, None)
            if context == self._active_turn_steer_context:
                self._active_turn_steer_context = None
                self._turn_steer_request_pending = False
            thread_id = payload.get("thread_id")
            turn_id = payload.get("turn_id")
            if (
                isinstance(token, TurnStateToken)
                and self._turn_state.token_generation_is_current(token)
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
                if isinstance(delta, str) and self._event_matches_active_stream(
                    params, require_item_id=True
                ):
                    self._append_codex_delta(delta)
            elif method == "turn/plan/updated":
                if self._event_matches_active_stream(params):
                    steps = params.get("plan") or []
                    rendered = " | ".join(
                        f"{step.get('status', '?')}: {step.get('step', '')}"
                        for step in steps
                        if isinstance(step, dict)
                    )
                    self._append_system(f"计划：{rendered}")
            elif method in {"item/started", "item/completed"}:
                item = params.get("item")
                if (
                    self._event_matches_active_stream(params)
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
            elif method == "turn/started":
                raw_turn = params.get("turn")
                turn = raw_turn if isinstance(raw_turn, dict) else {}
                thread_id = params.get("threadId")
                turn_id = turn.get("id")
                if isinstance(thread_id, str) and isinstance(turn_id, str):
                    if self._turn_state.observe_started(thread_id, turn_id):
                        if self._stream_thread_id == thread_id:
                            if self._stream_turn_id in {None, turn_id}:
                                self._stream_turn_id = turn_id
                            else:
                                self._request_session_reconciliation(
                                    "mismatched_stream_turn_started"
                                )
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
            code = event.get("code", "")
            message = event.get("message", "")
            text = f"协议警告：{code}{method_text} {message}"
            self._append_protocol_warning(
                f"{code}|{method}|{message}",
                text,
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
            return
        if context.startswith(_SCENE_RESULT_CONTEXT_PREFIX):
            self._fail_closed_houdini_status("Catalog：结果提交失败")
            return
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

        if context.startswith(_TURN_STEER_CONTEXT_PREFIX):
            self._turn_steer_tokens.pop(context, None)
            self._pending_steer_drafts.pop(context, None)
            if context == self._active_turn_steer_context:
                self._active_turn_steer_context = None
                self._turn_steer_request_pending = False
            if error_code == "TURN_NOT_STEERABLE":
                turn_kind = details.get("turn_kind")
                label = "review" if turn_kind == "review" else "compact"
                self._append_system(f"当前 {label} Turn 暂不能追加指令。")
            elif error_code == "NO_ACTIVE_TURN":
                self._append_system("当前 Turn 已结束；输入和附件已保留。")
            else:
                self._append_system(
                    f"追加指令失败：{format_bridge_error(payload)}"
                )
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
            self._refresh_controls()
            return

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
            if active_turn:
                self._accept_sent_draft(context)
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
        self._refresh_controls()

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

    def _selected_model_supports_images(self) -> bool:
        record = self._selected_model_record()
        if record is None:
            return True
        modalities = record.get("inputModalities")
        return isinstance(modalities, list) and "image" in modalities

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
        self._finish_codex_message()
        self._stream_thread_id = None
        self._stream_turn_id = None

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
        QtCore.QTimer.singleShot(delay_ms, self._poll_once)

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
