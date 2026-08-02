"""Compact Qt view for project containers and their real role Threads."""

from __future__ import annotations

from typing import Any

from .project_team import (
    OrdinaryThreadViewModel,
    ProjectPanelState,
    ProjectViewModel,
    RoleRuntimeDraft,
    RoleViewModel,
    find_tree_item,
)

try:  # Houdini supplies PySide6; ordinary unit-test Python intentionally may not.
    from PySide6 import QtCore, QtGui, QtWidgets
    from .composer import ExpandableTextEdit
except ImportError:  # pragma: no cover - exercised by the pure view-model suite.
    QtCore = QtGui = QtWidgets = None  # type: ignore[assignment]
    ExpandableTextEdit = None  # type: ignore[assignment,misc]


PYSIDE_AVAILABLE = QtWidgets is not None

_STATUS_LABELS = {
    "planning": "正在制定方案",
    "executing": "正在执行",
    "reviewing": "正在审查",
    "waiting_user": "等待用户",
    "completed": "已完成",
    "stopped": "已停止",
    "pending": "等待开始",
    "waiting": "等待开始",
    "running": "进行中",
    "interrupted": "已中断",
    "failed": "失败",
}


def _status_label(value: str) -> str:
    return _STATUS_LABELS.get(value, value or "未知")


if PYSIDE_AVAILABLE:

    class ProjectTeamView(QtWidgets.QWidget):
        """One narrow-safe project navigation surface.

        Signals carry only explicit IDs.  The view never infers membership and
        never opens a chat for the project-container row.
        """

        refreshRequested = QtCore.Signal()
        newTaskRequested = QtCore.Signal(str)
        openThreadRequested = QtCore.Signal(str)
        deleteThreadRequested = QtCore.Signal(str)
        renameThreadRequested = QtCore.Signal(str, str)
        copyThreadIdRequested = QtCore.Signal(str)
        projectSelected = QtCore.Signal(str)
        projectContextChanged = QtCore.Signal(bool)
        appendGuidanceRequested = QtCore.Signal(str, object, str, object)
        continueProjectRequested = QtCore.Signal(str)
        stopProjectRequested = QtCore.Signal(str)
        collapsedChanged = QtCore.Signal(bool)
        roleRuntimeRequested = QtCore.Signal(
            str, str, object, object, object
        )
        modelCatalogRefreshRequested = QtCore.Signal()

        _BACKGROUND = "#101218"
        _SURFACE = "#171a22"
        _BORDER = "#3a414d"
        _TEXT = "#e5e9f0"
        _MUTED = "#aeb7c2"

        def __init__(
            self,
            state: ProjectPanelState | None = None,
            parent: QtWidgets.QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.state = state or ProjectPanelState()
            self._items_by_key: dict[str, QtWidgets.QTreeWidgetItem] = {}
            self._model_catalog: dict[str, dict[str, Any]] = {}
            self._build_ui()
            self._connect_signals_once()
            self.refresh_view()

        def _build_ui(self) -> None:
            self.setObjectName("hiaProjectTeamViewV2")
            self.setMinimumWidth(300)
            self.setStyleSheet(
                f"""
                QWidget#hiaProjectTeamViewV2 {{
                    background-color: {self._BACKGROUND};
                    color: {self._TEXT};
                }}
                QFrame#projectDetailSurface, QFrame#attentionSurface {{
                    background-color: {self._SURFACE};
                    border: 1px solid {self._BORDER};
                    border-radius: 4px;
                }}
                QLabel {{ background-color: transparent; color: {self._TEXT}; }}
                QLabel[muted="true"] {{ color: {self._MUTED}; }}
                QTreeWidget, QPlainTextEdit, QComboBox {{
                    background-color: #0d0f15;
                    color: {self._TEXT};
                    border: 1px solid {self._BORDER};
                }}
                QPushButton, QToolButton {{
                    background-color: #262b37;
                    color: {self._TEXT};
                    border: 1px solid #444b59;
                    padding: 5px 8px;
                }}
                QPushButton:disabled, QToolButton:disabled {{ color: #737b89; }}
                """
            )
            palette = self.palette()
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(self._BACKGROUND))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#0d0f15"))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(self._TEXT))
            self.setPalette(palette)
            self.setAutoFillBackground(True)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(6)

            header = QtWidgets.QHBoxLayout()
            self.title_label = QtWidgets.QLabel("项目与普通任务")
            self.title_label.setStyleSheet("font-weight: 600;")
            header.addWidget(self.title_label, 1)
            self.collapse_button = QtWidgets.QToolButton()
            self.collapse_button.setMinimumWidth(56)
            self.collapse_button.setText("收起")
            self.collapse_button.setCheckable(True)
            self.collapse_button.setToolTip("折叠或展开左侧项目与任务导航")
            header.addWidget(self.collapse_button)
            root.addLayout(header)

            self.navigation_body = QtWidgets.QWidget()
            navigation_layout = QtWidgets.QVBoxLayout(self.navigation_body)
            navigation_layout.setContentsMargins(0, 0, 0, 0)
            navigation_layout.setSpacing(6)

            create_grid = QtWidgets.QGridLayout()
            create_grid.setContentsMargins(0, 0, 0, 0)
            create_grid.setHorizontalSpacing(6)
            create_grid.setVerticalSpacing(4)
            self.new_single_button = QtWidgets.QPushButton("新建普通任务（单个 AI）")
            self.new_project_button = QtWidgets.QPushButton("新建项目（项目团队）")
            self.new_single_button.setToolTip("新任务由当前一个 AI 负责")
            self.new_project_button.setToolTip("创建一个项目和五个职责明确的角色任务")
            create_grid.addWidget(self.new_single_button, 0, 0)
            create_grid.addWidget(self.new_project_button, 1, 0)
            navigation_layout.addLayout(create_grid)

            self.tree = QtWidgets.QTreeWidget()
            self.tree.setHeaderHidden(True)
            self.tree.setRootIsDecorated(True)
            self.tree.setIndentation(16)
            self.tree.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
            self.tree.setUniformRowHeights(True)
            self.tree.setMinimumHeight(150)
            navigation_layout.addWidget(self.tree, 1)

            self.ordinary_actions = QtWidgets.QWidget()
            ordinary_actions_layout = QtWidgets.QGridLayout(self.ordinary_actions)
            ordinary_actions_layout.setContentsMargins(0, 0, 0, 0)
            ordinary_actions_layout.setHorizontalSpacing(6)
            ordinary_actions_layout.setVerticalSpacing(4)
            self.ordinary_name_edit = QtWidgets.QLineEdit()
            self.ordinary_name_edit.setPlaceholderText("普通任务名称")
            self.rename_button = QtWidgets.QPushButton("重命名")
            self.copy_id_button = QtWidgets.QPushButton("复制 ID")
            ordinary_actions_layout.addWidget(self.ordinary_name_edit, 0, 0, 1, 2)
            ordinary_actions_layout.addWidget(self.rename_button, 1, 0)
            ordinary_actions_layout.addWidget(self.copy_id_button, 1, 1)
            navigation_layout.addWidget(self.ordinary_actions)

            action_grid = QtWidgets.QGridLayout()
            self.refresh_button = QtWidgets.QPushButton("刷新")
            self.delete_button = QtWidgets.QPushButton("删除所选任务")
            self.delete_button.setToolTip(
                "永久删除所选普通任务、原生聊天记录和该任务的本地附件缓存"
            )
            action_grid.addWidget(self.refresh_button, 0, 0)
            action_grid.addWidget(self.delete_button, 0, 1)
            action_grid.setColumnStretch(1, 1)
            navigation_layout.addLayout(action_grid)
            root.addWidget(self.navigation_body, 1)

            self.detail_surface = QtWidgets.QFrame()
            self.detail_surface.setObjectName("projectDetailSurface")
            detail = QtWidgets.QVBoxLayout(self.detail_surface)
            detail.setContentsMargins(8, 8, 8, 8)
            detail.setSpacing(5)
            self.selection_title = QtWidgets.QLabel("尚未选择任务")
            self.selection_title.setWordWrap(True)
            self.selection_meta = QtWidgets.QLabel("")
            self.selection_meta.setProperty("muted", True)
            self.selection_meta.setWordWrap(True)
            detail.addWidget(self.selection_title)
            detail.addWidget(self.selection_meta)

            self.runtime_widget = QtWidgets.QWidget()
            runtime_layout = QtWidgets.QFormLayout(self.runtime_widget)
            runtime_layout.setContentsMargins(0, 3, 0, 0)
            runtime_layout.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            runtime_layout.setRowWrapPolicy(
                QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
            )
            self.model_combo = QtWidgets.QComboBox()
            self.model_combo.setEditable(False)
            self.model_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            self.effort_combo = QtWidgets.QComboBox()
            self.tier_combo = QtWidgets.QComboBox()
            self.refresh_models_button = QtWidgets.QPushButton("刷新模型目录")
            self.runtime_status_label = QtWidgets.QLabel()
            self.runtime_status_label.setProperty("muted", True)
            self.runtime_status_label.setWordWrap(True)
            self.save_runtime_button = QtWidgets.QPushButton("保存到该角色的下一 Turn")
            runtime_layout.addRow("模型", self.model_combo)
            runtime_layout.addRow("推理", self.effort_combo)
            runtime_layout.addRow("速度", self.tier_combo)
            runtime_layout.addRow(self.refresh_models_button)
            runtime_layout.addRow(self.runtime_status_label)
            runtime_layout.addRow(self.save_runtime_button)
            self._show_catalog_unavailable()
            detail.addWidget(self.runtime_widget)

            # Reuse the main composer editor: it deliberately leaves IME
            # composition to Qt and binds sending only to Ctrl+Enter.
            self.guidance_edit = ExpandableTextEdit()
            self.guidance_edit.setPlaceholderText("为所选项目或角色追加指导")
            self.guidance_edit.setMinimumHeight(64)
            self.guidance_edit.setMaximumHeight(120)
            self.guidance_scope_widget = QtWidgets.QWidget()
            guidance_scope = QtWidgets.QVBoxLayout(self.guidance_scope_widget)
            guidance_scope.setContentsMargins(0, 0, 0, 0)
            guidance_scope.setSpacing(4)
            self.current_step_guidance_button = QtWidgets.QToolButton()
            self.current_step_guidance_button.setText("补充当前步骤")
            self.current_step_guidance_button.setCheckable(True)
            self.replan_guidance_button = QtWidgets.QToolButton()
            self.replan_guidance_button.setText("修改整体方案")
            self.replan_guidance_button.setCheckable(True)
            self.guidance_scope_group = QtWidgets.QButtonGroup(self)
            self.guidance_scope_group.setExclusive(True)
            self.guidance_scope_group.addButton(
                self.current_step_guidance_button, 0
            )
            self.guidance_scope_group.addButton(self.replan_guidance_button, 1)
            self.current_step_guidance_button.setChecked(True)
            guidance_scope.addWidget(self.current_step_guidance_button)
            guidance_scope.addWidget(self.replan_guidance_button)
            # Natural-language guidance is classified by the project roles.
            # Do not expose internal routing or requirement-delta controls.
            self.guidance_scope_widget.setVisible(False)
            self.requirement_change_widget = QtWidgets.QWidget()
            requirement_change_layout = QtWidgets.QFormLayout(
                self.requirement_change_widget
            )
            requirement_change_layout.setContentsMargins(0, 0, 0, 0)
            self.requirement_change_combo = QtWidgets.QComboBox()
            self.requirement_change_combo.addItem("新增需求", "add")
            self.requirement_change_combo.addItem("替换已有需求", "replace")
            self.requirement_change_combo.addItem("取消已有需求", "remove")
            self.requirement_target_combo = QtWidgets.QComboBox()
            requirement_change_layout.addRow("变更类型", self.requirement_change_combo)
            requirement_change_layout.addRow("已有需求", self.requirement_target_combo)
            self.guidance_button = QtWidgets.QPushButton("发送追加指导")
            detail.addWidget(self.guidance_edit)
            detail.addWidget(self.guidance_scope_widget)
            detail.addWidget(self.requirement_change_widget)
            self.requirement_change_widget.setVisible(False)
            detail.addWidget(self.guidance_button)
            root.addWidget(self.detail_surface)

            self.attention_surface = QtWidgets.QFrame()
            self.attention_surface.setObjectName("attentionSurface")
            attention_layout = QtWidgets.QVBoxLayout(self.attention_surface)
            attention_layout.setContentsMargins(8, 8, 8, 8)
            self.attention_title = QtWidgets.QLabel("项目需要处理")
            self.attention_title.setStyleSheet("font-weight: 600; color: #ffcc66;")
            self.attention_details = QtWidgets.QLabel()
            self.attention_details.setWordWrap(True)
            attention_layout.addWidget(self.attention_title)
            attention_layout.addWidget(self.attention_details)
            attention_buttons = QtWidgets.QGridLayout()
            self.continue_button = QtWidgets.QPushButton("继续")
            self.stop_button = QtWidgets.QPushButton("停止项目")
            attention_buttons.addWidget(self.continue_button, 0, 0)
            attention_buttons.addWidget(self.stop_button, 0, 1)
            attention_layout.addLayout(attention_buttons)
            root.addWidget(self.attention_surface)

        def _connect_signals_once(self) -> None:
            # This method is called only from __init__; render/show never reconnect.
            self.refresh_button.clicked.connect(self.refreshRequested.emit)
            self.new_single_button.clicked.connect(
                lambda: self.newTaskRequested.emit("single")
            )
            self.new_project_button.clicked.connect(
                lambda: self.newTaskRequested.emit("team")
            )
            self.collapse_button.toggled.connect(self._set_collapsed)
            self.tree.itemSelectionChanged.connect(self._selection_changed)
            self.tree.itemExpanded.connect(
                lambda item: self._remember_expansion(item, True)
            )
            self.tree.itemCollapsed.connect(
                lambda item: self._remember_expansion(item, False)
            )
            self.tree.itemDoubleClicked.connect(self._item_double_clicked)
            self.delete_button.clicked.connect(self._delete_selected)
            self.rename_button.clicked.connect(self._rename_selected)
            self.copy_id_button.clicked.connect(self._copy_selected_id)
            self.ordinary_name_edit.returnPressed.connect(self._rename_selected)
            self.save_runtime_button.clicked.connect(self._save_runtime)
            self.refresh_models_button.clicked.connect(
                self.modelCatalogRefreshRequested.emit
            )
            self.model_combo.currentIndexChanged.connect(self._model_changed)
            self.effort_combo.currentTextChanged.connect(self._capture_runtime_draft)
            self.tier_combo.currentTextChanged.connect(self._capture_runtime_draft)
            self.guidance_button.clicked.connect(self._send_guidance)
            self.guidance_edit.sendRequested.connect(self._send_guidance)
            self.replan_guidance_button.toggled.connect(
                self.requirement_change_widget.setVisible
            )
            self.requirement_change_combo.currentIndexChanged.connect(
                self._update_requirement_change_controls
            )
            self.continue_button.clicked.connect(self._continue_project)
            self.stop_button.clicked.connect(self._stop_project)

        def set_model_catalog(self, models: list[dict[str, Any]]) -> None:
            current = self._selected_model_id()
            self._model_catalog = {}
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            default_index = -1
            for raw_model in models:
                if not isinstance(raw_model, dict):
                    continue
                model_id = raw_model.get("model")
                if (
                    not isinstance(model_id, str)
                    or not model_id
                    or model_id in self._model_catalog
                ):
                    continue
                record = dict(raw_model)
                self._model_catalog[model_id] = record
                display_name = record.get("displayName")
                label = (
                    display_name
                    if isinstance(display_name, str) and display_name
                    else model_id
                )
                self.model_combo.addItem(label, record)
                if record.get("isDefault") is True:
                    default_index = self.model_combo.count() - 1
            if not self._model_catalog:
                self.model_combo.blockSignals(False)
                self._show_catalog_unavailable()
                self._render_selection()
                return
            selected = self._model_index(current)
            default_candidate_index = default_index
            if default_candidate_index < 0 and self.model_combo.count() > 0:
                default_candidate_index = 0
            self.model_combo.setCurrentIndex(
                selected if selected >= 0 else default_candidate_index
            )
            self.model_combo.blockSignals(False)
            self._update_runtime_capabilities(None, None)
            self._render_selection()

        def _show_catalog_unavailable(self) -> None:
            """Render an honest, recoverable empty-catalog state."""

            blocked = self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItem("实时模型目录尚未加载", None)
            self.model_combo.setCurrentIndex(0)
            self.model_combo.blockSignals(blocked)
            self._replace_options(
                self.effort_combo,
                [],
                id_key="reasoningEffort",
                label_key="reasoningEffort",
                preferred=None,
                default_value=None,
            )
            self._replace_options(
                self.tier_combo,
                [],
                id_key="id",
                label_key="name",
                preferred=None,
                default_value=None,
            )
            self.runtime_status_label.setText(
                "实时模型目录不可用；可刷新目录后再为该角色选择模型。"
            )

        def _model_changed(self, _index: int = -1) -> None:
            previous_effort = self._combo_value(self.effort_combo)
            previous_tier = self._combo_value(self.tier_combo)
            self._update_runtime_capabilities(previous_effort, previous_tier)
            self._capture_runtime_draft()

        def _update_runtime_capabilities(
            self,
            preferred_effort: str | None,
            preferred_tier: str | None,
        ) -> None:
            record = self._selected_model_record()
            efforts = (
                record.get("supportedReasoningEfforts", [])
                if record is not None
                else []
            )
            tiers = record.get("serviceTiers", []) if record is not None else []
            default_effort = record.get("defaultReasoningEffort") if record else None
            default_tier = record.get("defaultServiceTier") if record else None
            self._replace_options(
                self.effort_combo,
                efforts,
                id_key="reasoningEffort",
                label_key="reasoningEffort",
                preferred=preferred_effort,
                default_value=default_effort,
            )
            self._replace_options(
                self.tier_combo,
                tiers,
                id_key="id",
                label_key="name",
                preferred=preferred_tier,
                default_value=default_tier,
            )
            modalities = record.get("inputModalities", []) if record else []
            self.model_combo.setToolTip(
                "输入能力：" + ", ".join(modalities)
                if isinstance(modalities, list) and modalities
                else "模型能力来自实时 model/list"
            )

        @staticmethod
        def _replace_options(
            combo: QtWidgets.QComboBox,
            records: Any,
            *,
            id_key: str,
            label_key: str,
            preferred: str | None,
            default_value: Any,
        ) -> None:
            blocked = combo.blockSignals(True)
            combo.clear()
            default_suffix = (
                f"（{default_value}）"
                if isinstance(default_value, str) and default_value
                else ""
            )
            combo.addItem(f"默认{default_suffix}", None)
            if isinstance(records, list):
                for item in records:
                    if not isinstance(item, dict):
                        continue
                    option_id = item.get(id_key)
                    if not isinstance(option_id, str) or not option_id:
                        continue
                    label = item.get(label_key)
                    combo.addItem(
                        label if isinstance(label, str) and label else option_id,
                        option_id,
                    )
            index = combo.findData(preferred) if preferred is not None else 0
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(blocked)

        def refresh_view(self) -> None:
            selected_key = self.state.selected_key
            blocked = self.tree.blockSignals(True)
            self._items_by_key.clear()
            self.tree.clear()
            projects_root = QtWidgets.QTreeWidgetItem(
                [f"项目（{len(self.state.tree.projects)}）"]
            )
            projects_root.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "projects")
            projects_root.setFlags(
                projects_root.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.tree.addTopLevelItem(projects_root)
            for project in self.state.tree.projects:
                project_item = QtWidgets.QTreeWidgetItem(
                    [f"{project.title} · {_status_label(project.status)}"]
                )
                project_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, project.stable_key)
                project_item.setData(
                    0, QtCore.Qt.ItemDataRole.UserRole + 1, f"project:{project.project_id}"
                )
                project_item.setToolTip(0, f"阶段：{project.stage or '尚未开始'}")
                projects_root.addChild(project_item)
                self._items_by_key[project.stable_key] = project_item
                for role in project.roles:
                    role_item = QtWidgets.QTreeWidgetItem(
                        [f"{role.title} · {_status_label(role.status)}"]
                    )
                    role_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, role.stable_key)
                    project_item.addChild(role_item)
                    self._items_by_key[role.stable_key] = role_item
            ordinary_root = QtWidgets.QTreeWidgetItem(
                [f"普通任务（{len(self.state.tree.ordinary_threads)}）"]
            )
            ordinary_root.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "ordinary")
            ordinary_root.setFlags(
                ordinary_root.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.tree.addTopLevelItem(ordinary_root)
            for thread in self.state.tree.ordinary_threads:
                item = QtWidgets.QTreeWidgetItem([thread.title])
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, thread.stable_key)
                item.setToolTip(0, thread.preview)
                ordinary_root.addChild(item)
                self._items_by_key[thread.stable_key] = item
            projects_root.setExpanded(self.state.projects_group_expanded)
            ordinary_root.setExpanded(self.state.ordinary_group_expanded)
            for index in range(projects_root.childCount()):
                project_item = projects_root.child(index)
                project = self.state.tree.projects[index]
                project_item.setExpanded(
                    self.state.project_expanded.get(project.project_id, True)
                )
            if selected_key in self._items_by_key:
                self.tree.setCurrentItem(self._items_by_key[selected_key])
            self.tree.blockSignals(blocked)
            self._render_selection()

        def _remember_expansion(
            self,
            item: QtWidgets.QTreeWidgetItem,
            expanded: bool,
        ) -> None:
            marker = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            if marker == "projects":
                self.state.projects_group_expanded = expanded
            elif marker == "ordinary":
                self.state.ordinary_group_expanded = expanded
            elif isinstance(marker, str) and marker.startswith("project:"):
                self.state.project_expanded[marker.removeprefix("project:")] = expanded

        def _set_collapsed(self, collapsed: bool) -> None:
            self.state.collapsed = bool(collapsed)
            self.navigation_body.setVisible(not collapsed)
            self.detail_surface.setVisible(not collapsed)
            self.title_label.setVisible(not collapsed)
            self.setMinimumWidth(42 if collapsed else 300)
            self.collapse_button.setText("展开" if collapsed else "收起")
            if collapsed:
                self.attention_surface.hide()
            else:
                self._render_selection()
            self.collapsedChanged.emit(bool(collapsed))

        def _selected_key(self) -> str | None:
            item = self.tree.currentItem()
            if item is None:
                return None
            value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            return value if isinstance(value, str) else None

        def _selection_changed(self) -> None:
            self.state.select(self._selected_key())
            self.set_delete_confirmation(None)
            self._render_selection()

        def _render_selection(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            self.delete_button.setEnabled(
                isinstance(selected, OrdinaryThreadViewModel)
            )
            ordinary_selected = isinstance(selected, OrdinaryThreadViewModel)
            self.ordinary_actions.setVisible(ordinary_selected)
            self.rename_button.setEnabled(ordinary_selected)
            self.copy_id_button.setEnabled(ordinary_selected)
            self.ordinary_name_edit.setEnabled(ordinary_selected)
            if ordinary_selected:
                self.ordinary_name_edit.setText(selected.title)
            else:
                self.ordinary_name_edit.clear()
            self.runtime_widget.setVisible(isinstance(selected, RoleViewModel))
            project = self._selected_project(selected)
            self._render_requirement_choices(project)
            show_guidance = bool(project and project.can_guide)
            self.guidance_edit.setVisible(show_guidance)
            self.guidance_scope_widget.setVisible(False)
            self.guidance_button.setVisible(show_guidance)
            self.guidance_button.setEnabled(
                bool(
                    project
                    and project.can_guide
                    and (
                        not isinstance(selected, RoleViewModel)
                        or selected.can_guide
                    )
                )
            )
            if isinstance(selected, ProjectViewModel):
                self.selection_title.setText(selected.title)
                self.selection_meta.setText(
                    f"项目容器 · {_status_label(selected.status)} · 阶段 {selected.stage or '尚未开始'}\n"
                    "请选择下方角色进入真实对话。"
                )
                self.projectSelected.emit(selected.project_id)
            elif isinstance(selected, RoleViewModel):
                self.selection_title.setText(
                    f"{selected.title}（项目 Goal 持有者）"
                    if selected.role == "supervisor"
                    else selected.title
                )
                self.selection_meta.setText(
                    f"项目角色 · {_status_label(selected.status)}\nThread：{selected.thread_id}"
                )
                draft = self.state.runtime_draft_for(selected)
                self._set_model_value(draft.model)
                self._update_runtime_capabilities(
                    draft.effort,
                    draft.service_tier,
                )
                record = self._selected_model_record()
                modalities = record.get("inputModalities") if record else None
                has_catalog = bool(self._model_catalog)
                supports_text = (
                    isinstance(modalities, list) and "text" in modalities
                )
                editable = selected.can_set_runtime
                self.model_combo.setEnabled(editable and has_catalog)
                self.effort_combo.setEnabled(editable and record is not None)
                self.tier_combo.setEnabled(editable and record is not None)
                self.refresh_models_button.setEnabled(editable)
                self.save_runtime_button.setEnabled(
                    editable and record is not None and supports_text
                )
                if not editable:
                    self.runtime_status_label.setText(
                        "该项目已结束，角色运行设置不可再修改。"
                    )
                elif not has_catalog:
                    self.runtime_status_label.setText(
                        "实时模型目录不可用；可刷新目录后再为该角色选择模型。"
                    )
                elif not supports_text:
                    self.runtime_status_label.setText(
                        "所选模型不支持文本输入，不能用于项目角色。"
                    )
                else:
                    self.runtime_status_label.setText(
                        "仅影响该角色的下一 Turn；其他角色和左上角设置保持不变。"
                    )
            elif isinstance(selected, OrdinaryThreadViewModel):
                self.selection_title.setText(selected.title)
                self.selection_meta.setText(selected.preview or f"Thread：{selected.thread_id}")
            else:
                self.selection_title.setText("尚未选择任务")
                self.selection_meta.setText("")
            self.detail_surface.setVisible(
                isinstance(selected, (ProjectViewModel, RoleViewModel))
            )
            self.projectContextChanged.emit(project is not None)
            self._render_attention(project)

        def _render_requirement_choices(
            self, project: ProjectViewModel | None
        ) -> None:
            blocked = self.requirement_target_combo.blockSignals(True)
            current = self.requirement_target_combo.currentData()
            self.requirement_target_combo.clear()
            if project is not None:
                for item in project.requirements:
                    if item.status in {
                        "active",
                        "planned",
                        "verified",
                        "failed",
                        "blocked",
                    }:
                        self.requirement_target_combo.addItem(
                            f"{item.requirement_id} ({item.kind})",
                            item.requirement_id,
                        )
            index = self.requirement_target_combo.findData(current)
            self.requirement_target_combo.setCurrentIndex(index if index >= 0 else 0)
            self.requirement_target_combo.blockSignals(blocked)
            self.requirement_change_widget.setVisible(False)
            self._update_requirement_change_controls()

        def _update_requirement_change_controls(self, _index: int = 0) -> None:
            operation = self.requirement_change_combo.currentData()
            self.requirement_target_combo.setVisible(operation in {"replace", "remove"})

        def _render_attention(self, project: ProjectViewModel | None) -> None:
            visible = bool(project and project.attention.visible)
            self.attention_surface.setVisible(visible and not self.state.collapsed)
            if not visible or project is None:
                return
            attention = project.attention
            evidence = ", ".join(attention.latest_evidence_ids) or "无"
            self.attention_details.setText(
                f"阶段：{attention.stage or '未知'}\n"
                f"原因：{attention.reason or '未提供'}\n"
                f"已消耗 Turn：{attention.consumed_turns}\n"
                f"最近错误：{attention.last_error or '无'}\n"
                f"最近证据：{evidence}\n"
                f"最近修复卡：{attention.latest_repair_card or '无'}"
            )
            self.continue_button.setEnabled(project.can_continue)
            self.stop_button.setEnabled(project.can_stop)

        def _selected_project(self, selected: Any) -> ProjectViewModel | None:
            if isinstance(selected, ProjectViewModel):
                return selected
            if isinstance(selected, RoleViewModel):
                return next(
                    (
                        project
                        for project in self.state.tree.projects
                        if project.project_id == selected.project_id
                    ),
                    None,
                )
            return None

        def _item_double_clicked(self, _item: Any, _column: int) -> None:
            self._open_selected()

        def _open_selected(self) -> None:
            thread_id = self.state.selected_chat_thread_id()
            if thread_id:
                self.openThreadRequested.emit(thread_id)

        def _delete_selected(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            if isinstance(selected, OrdinaryThreadViewModel):
                self.deleteThreadRequested.emit(selected.thread_id)

        def _rename_selected(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            name = self.ordinary_name_edit.text().strip()
            if isinstance(selected, OrdinaryThreadViewModel) and name:
                self.renameThreadRequested.emit(selected.thread_id, name)

        def _copy_selected_id(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            if isinstance(selected, OrdinaryThreadViewModel):
                self.copyThreadIdRequested.emit(selected.thread_id)

        def set_delete_confirmation(self, thread_id: str | None) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            confirmed = (
                isinstance(selected, OrdinaryThreadViewModel)
                and selected.thread_id == thread_id
            )
            self.delete_button.setText(
                "再次点击确认删除" if confirmed else "删除所选任务"
            )
            self.delete_button.setToolTip(
                "再次点击将永久删除该普通任务和本地附件缓存"
                if confirmed
                else "永久删除所选普通任务、原生聊天记录和该任务的本地附件缓存"
            )

        def _save_runtime(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            if not isinstance(selected, RoleViewModel) or not selected.can_set_runtime:
                return
            draft = self._current_runtime_draft()
            self.state.set_runtime_draft(selected.stable_key, draft)
            self.roleRuntimeRequested.emit(
                selected.project_id,
                selected.thread_id,
                draft.model,
                draft.effort,
                draft.service_tier,
            )

        def _capture_runtime_draft(self, _value: str = "") -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            if isinstance(selected, RoleViewModel) and selected.can_set_runtime:
                self.state.set_runtime_draft(
                    selected.stable_key,
                    self._current_runtime_draft(),
                )

        def _current_runtime_draft(self) -> RoleRuntimeDraft:
            return RoleRuntimeDraft(
                self._selected_model_id(),
                self._combo_value(self.effort_combo),
                self._combo_value(self.tier_combo),
            )

        def _send_guidance(self) -> None:
            text = self.guidance_edit.toPlainText().strip()
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            project = self._selected_project(selected)
            if not text or project is None or not project.can_guide:
                return
            thread_id = (
                selected.thread_id
                if isinstance(selected, RoleViewModel)
                else None
            )
            self.appendGuidanceRequested.emit(
                project.project_id,
                thread_id,
                text,
                None,
            )

        def acknowledge_guidance(
            self,
            submitted_text: str,
            requirement_change: object = None,
        ) -> bool:
            """Clear only the exact draft acknowledged by this response."""

            if self.guidance_edit.toPlainText().strip() != submitted_text:
                return False
            if requirement_change is not None:
                return False
            self.guidance_edit.clear()
            return True

        def _current_requirement_change(self) -> dict[str, Any] | None:
            if not self.replan_guidance_button.isChecked():
                return None
            operation = self.requirement_change_combo.currentData()
            target = self.requirement_target_combo.currentData()
            return {
                "operation": operation,
                **(
                    {"target_requirement_id": target}
                    if operation in {"replace", "remove"} and target
                    else {}
                ),
            }

        def _continue_project(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            project = self._selected_project(selected)
            if project and project.can_continue:
                self.continueProjectRequested.emit(project.project_id)

        def _stop_project(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            project = self._selected_project(selected)
            if project and project.can_stop:
                self.stopProjectRequested.emit(project.project_id)

        def _selected_model_record(self) -> dict[str, Any] | None:
            value = self.model_combo.currentData()
            return value if isinstance(value, dict) else None

        def _selected_model_id(self) -> str | None:
            record = self._selected_model_record()
            model_id = record.get("model") if record is not None else None
            return model_id if isinstance(model_id, str) and model_id else None

        def _model_index(self, model_id: str | None) -> int:
            if model_id is None:
                return -1
            for index in range(self.model_combo.count()):
                record = self.model_combo.itemData(index)
                if isinstance(record, dict) and record.get("model") == model_id:
                    return index
            return -1

        def _set_model_value(self, model_id: str | None) -> None:
            if not self._model_catalog:
                self._show_catalog_unavailable()
                return
            blocked = self.model_combo.blockSignals(True)
            index = self._model_index(model_id)
            if index < 0:
                index = next(
                    (
                        candidate
                        for candidate in range(self.model_combo.count())
                        if isinstance(self.model_combo.itemData(candidate), dict)
                        and self.model_combo.itemData(candidate).get("isDefault") is True
                    ),
                    -1,
                )
            self.model_combo.setCurrentIndex(index)
            self.model_combo.blockSignals(blocked)

        @staticmethod
        def _combo_value(combo: QtWidgets.QComboBox) -> str | None:
            value = combo.currentData()
            return value if isinstance(value, str) and value else None


else:

    class ProjectTeamView:  # pragma: no cover - defensive import boundary.
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("ProjectTeamView requires Houdini's PySide6 runtime")
