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
except ImportError:  # pragma: no cover - exercised by the pure view-model suite.
    QtCore = QtGui = QtWidgets = None  # type: ignore[assignment]


PYSIDE_AVAILABLE = QtWidgets is not None


if PYSIDE_AVAILABLE:

    class ProjectTeamView(QtWidgets.QWidget):
        """One narrow-safe project navigation surface.

        Signals carry only explicit IDs.  The view never infers membership and
        never opens a chat for the project-container row.
        """

        refreshRequested = QtCore.Signal()
        newTaskRequested = QtCore.Signal(str)
        openThreadRequested = QtCore.Signal(str)
        projectSelected = QtCore.Signal(str)
        appendGuidanceRequested = QtCore.Signal(str, object, str)
        continueProjectRequested = QtCore.Signal(str)
        stopProjectRequested = QtCore.Signal(str)
        roleRuntimeRequested = QtCore.Signal(
            str, str, object, object, object
        )

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
            self._build_ui()
            self._connect_signals_once()
            self.render()

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

            action_grid = QtWidgets.QGridLayout()
            self.refresh_button = QtWidgets.QPushButton("刷新")
            self.open_button = QtWidgets.QPushButton("打开所选任务")
            action_grid.addWidget(self.refresh_button, 0, 0)
            action_grid.addWidget(self.open_button, 0, 1)
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
            self.model_combo.setEditable(True)
            self.model_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            self.effort_combo = QtWidgets.QComboBox()
            self.effort_combo.addItems(
                ["默认", "low", "medium", "high", "xhigh", "max", "ultra"]
            )
            self.tier_combo = QtWidgets.QComboBox()
            self.tier_combo.addItems(["默认", "standard", "priority", "flex"])
            self.save_runtime_button = QtWidgets.QPushButton("保存到该角色的下一 Turn")
            runtime_layout.addRow("模型", self.model_combo)
            runtime_layout.addRow("推理", self.effort_combo)
            runtime_layout.addRow("速度", self.tier_combo)
            runtime_layout.addRow(self.save_runtime_button)
            detail.addWidget(self.runtime_widget)

            self.guidance_edit = QtWidgets.QPlainTextEdit()
            self.guidance_edit.setPlaceholderText("为所选项目或角色追加指导")
            self.guidance_edit.setMinimumHeight(64)
            self.guidance_edit.setMaximumHeight(120)
            self.guidance_button = QtWidgets.QPushButton("发送追加指导")
            detail.addWidget(self.guidance_edit)
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
            self.tree.itemDoubleClicked.connect(self._item_double_clicked)
            self.open_button.clicked.connect(self._open_selected)
            self.save_runtime_button.clicked.connect(self._save_runtime)
            self.model_combo.currentTextChanged.connect(self._capture_runtime_draft)
            self.effort_combo.currentTextChanged.connect(self._capture_runtime_draft)
            self.tier_combo.currentTextChanged.connect(self._capture_runtime_draft)
            self.guidance_button.clicked.connect(self._send_guidance)
            self.continue_button.clicked.connect(self._continue_project)
            self.stop_button.clicked.connect(self._stop_project)

        def set_model_catalog(self, model_ids: list[str]) -> None:
            current = self.model_combo.currentText()
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItem("默认")
            for model_id in model_ids:
                if isinstance(model_id, str) and model_id and model_id != "默认":
                    self.model_combo.addItem(model_id)
            self.model_combo.setCurrentText(current or "默认")
            self.model_combo.blockSignals(False)

        def render(self) -> None:
            self._items_by_key.clear()
            self.tree.clear()
            projects_root = QtWidgets.QTreeWidgetItem(["项目"])
            projects_root.setFlags(
                projects_root.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.tree.addTopLevelItem(projects_root)
            for project in self.state.tree.projects:
                project_item = QtWidgets.QTreeWidgetItem(
                    [f"{project.title} · {project.status}"]
                )
                project_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, project.stable_key)
                project_item.setToolTip(0, f"阶段：{project.stage or '尚未开始'}")
                projects_root.addChild(project_item)
                self._items_by_key[project.stable_key] = project_item
                for role in project.roles:
                    role_item = QtWidgets.QTreeWidgetItem(
                        [f"{role.title} · {role.status}"]
                    )
                    role_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, role.stable_key)
                    project_item.addChild(role_item)
                    self._items_by_key[role.stable_key] = role_item
            ordinary_root = QtWidgets.QTreeWidgetItem(["普通任务"])
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
            projects_root.setExpanded(True)
            ordinary_root.setExpanded(True)
            for index in range(projects_root.childCount()):
                projects_root.child(index).setExpanded(True)
            if self.state.selected_key in self._items_by_key:
                self.tree.setCurrentItem(self._items_by_key[self.state.selected_key])
            self._render_selection()

        def _set_collapsed(self, collapsed: bool) -> None:
            self.state.collapsed = bool(collapsed)
            self.navigation_body.setVisible(not collapsed)
            self.detail_surface.setVisible(not collapsed)
            self.collapse_button.setText("展开" if collapsed else "收起")
            if collapsed:
                self.attention_surface.hide()
            else:
                self._render_selection()

        def _selected_key(self) -> str | None:
            item = self.tree.currentItem()
            if item is None:
                return None
            value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            return value if isinstance(value, str) else None

        def _selection_changed(self) -> None:
            self.state.select(self._selected_key())
            self._render_selection()

        def _render_selection(self) -> None:
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            self.open_button.setEnabled(
                isinstance(selected, (RoleViewModel, OrdinaryThreadViewModel))
                and (
                    not isinstance(selected, RoleViewModel)
                    or selected.can_open
                )
            )
            self.runtime_widget.setVisible(isinstance(selected, RoleViewModel))
            project = self._selected_project(selected)
            self.guidance_edit.setVisible(project is not None)
            self.guidance_button.setVisible(project is not None)
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
                    f"项目容器 · {selected.status} · 阶段 {selected.stage or '尚未开始'}\n"
                    "请选择下方角色进入真实对话。"
                )
                self.projectSelected.emit(selected.project_id)
            elif isinstance(selected, RoleViewModel):
                self.selection_title.setText(selected.title)
                self.selection_meta.setText(
                    f"项目角色 · {selected.status}\nThread：{selected.thread_id}"
                )
                draft = self.state.runtime_draft_for(selected)
                self._set_combo_value(self.model_combo, draft.model)
                self._set_combo_value(self.effort_combo, draft.effort)
                self._set_combo_value(self.tier_combo, draft.service_tier)
                self.save_runtime_button.setEnabled(selected.can_set_runtime)
            elif isinstance(selected, OrdinaryThreadViewModel):
                self.selection_title.setText(selected.title)
                self.selection_meta.setText(selected.preview or f"Thread：{selected.thread_id}")
            else:
                self.selection_title.setText("尚未选择任务")
                self.selection_meta.setText("")
            self._render_attention(project)

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
                self._combo_value(self.model_combo),
                self._combo_value(self.effort_combo),
                self._combo_value(self.tier_combo),
            )

        def _send_guidance(self) -> None:
            text = self.guidance_edit.toPlainText().strip()
            selected = find_tree_item(self.state.tree, self.state.selected_key)
            project = self._selected_project(selected)
            if not text or project is None or not project.can_guide:
                return
            thread_id = selected.thread_id if isinstance(selected, RoleViewModel) else None
            self.appendGuidanceRequested.emit(project.project_id, thread_id, text)

        def acknowledge_guidance(self) -> None:
            self.guidance_edit.clear()

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

        @staticmethod
        def _set_combo_value(combo: QtWidgets.QComboBox, value: str | None) -> None:
            blocked = combo.blockSignals(True)
            combo.setCurrentText(value or "默认")
            combo.blockSignals(blocked)

        @staticmethod
        def _combo_value(combo: QtWidgets.QComboBox) -> str | None:
            value = combo.currentText().strip()
            return None if not value or value == "默认" else value


else:

    class ProjectTeamView:  # pragma: no cover - defensive import boundary.
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("ProjectTeamView requires Houdini's PySide6 runtime")
