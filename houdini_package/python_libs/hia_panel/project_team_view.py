"""Native project container and role-Thread view for the Houdini Panel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6 import QtCore, QtWidgets

def _configure_text_tool_button(button: QtWidgets.QToolButton) -> None:
    """Keep the complete outcome label visible in narrow Houdini panes."""

    button.setToolButtonStyle(
        QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
    )
    button.setAutoRaise(False)
    button.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
    button.ensurePolished()
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    hint = button.sizeHint()
    button.setMinimumSize(
        max(88, text_width + 24, hint.width()),
        max(28, hint.height()),
    )
    button.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.MinimumExpanding,
        QtWidgets.QSizePolicy.Policy.Fixed,
    )


class ResponsiveModeButtonPair(QtWidgets.QWidget):
    """Keep two direct mode buttons horizontal, wrapping only when needed."""

    def __init__(
        self,
        first: QtWidgets.QToolButton,
        second: QtWidgets.QToolButton,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._buttons = (first, second)
        self._layout = QtWidgets.QBoxLayout(
            QtWidgets.QBoxLayout.Direction.LeftToRight,
            self,
        )
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        for button in self._buttons:
            self._layout.addWidget(button, 1)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self.setMinimumHeight(
            max(button.minimumHeight() for button in self._buttons)
        )

    def horizontalMinimumWidth(self) -> int:  # noqa: N802
        return (
            sum(button.minimumWidth() for button in self._buttons)
            + self._layout.spacing()
        )

    def isWrapped(self) -> bool:  # noqa: N802
        return (
            self._layout.direction()
            == QtWidgets.QBoxLayout.Direction.TopToBottom
        )

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        heights = [button.minimumHeight() for button in self._buttons]
        if width < self.horizontalMinimumWidth():
            return sum(heights) + self._layout.spacing()
        return max(heights)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802
        width = max(button.minimumWidth() for button in self._buttons)
        return QtCore.QSize(width, self.heightForWidth(width))

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        width = self.horizontalMinimumWidth()
        return QtCore.QSize(width, self.heightForWidth(width))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        direction = (
            QtWidgets.QBoxLayout.Direction.TopToBottom
            if event.size().width() < self.horizontalMinimumWidth()
            else QtWidgets.QBoxLayout.Direction.LeftToRight
        )
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
        required_height = self.heightForWidth(event.size().width())
        if self.minimumHeight() != required_height:
            self.setMinimumHeight(required_height)
        self.updateGeometry()
        super().resizeEvent(event)


class ProjectHistoryTree(QtWidgets.QTreeWidget):
    """Tree projection of Bridge-owned projects plus ordinary Threads.

    The small combo-like API keeps the Panel's existing history selection and
    thread-open paths intact.  Membership always comes from the supplied
    project snapshot; this widget owns no conversation state.
    """

    currentIndexChanged = QtCore.Signal(int)
    threadOpenRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectAndTaskHistoryTree")
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self._flat_items: list[QtWidgets.QTreeWidgetItem] = []
        self.currentItemChanged.connect(self._emit_current_index)
        self.itemDoubleClicked.connect(self._emit_thread_open)

    @staticmethod
    def _payload(item: QtWidgets.QTreeWidgetItem | None) -> dict[str, Any]:
        if item is None:
            return {}
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _natural_title(record: Mapping[str, Any], fallback: str) -> str:
        for key in ("name", "title", "preview"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return fallback

    def _register(
        self,
        item: QtWidgets.QTreeWidgetItem,
        payload: Mapping[str, Any] | None,
    ) -> QtWidgets.QTreeWidgetItem:
        item.setData(
            0,
            QtCore.Qt.ItemDataRole.UserRole,
            dict(payload) if isinstance(payload, Mapping) else None,
        )
        self._flat_items.append(item)
        return item

    def _group_item(self, title: str, group: str) -> QtWidgets.QTreeWidgetItem:
        item = self._register(
            QtWidgets.QTreeWidgetItem((title,)),
            {"kind": "group", "group": group},
        )
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.addTopLevelItem(item)
        item.setExpanded(True)
        return item

    def _placeholder(
        self,
        parent: QtWidgets.QTreeWidgetItem,
        text: str,
    ) -> None:
        item = self._register(
            QtWidgets.QTreeWidgetItem((text,)),
            {"kind": "placeholder"},
        )
        item.setDisabled(True)
        parent.addChild(item)

    def set_history_records(
        self,
        records: list[dict[str, Any]],
        *,
        selected_thread_id: str | None = None,
        selected_project_id: str | None = None,
    ) -> int:
        """Render two explicit sections and return the restored flat index."""

        expanded_projects: dict[str, bool] = {}
        for item in self._flat_items:
            payload = self._payload(item)
            project_id = payload.get("project_id")
            if payload.get("kind") == "project" and isinstance(project_id, str):
                expanded_projects[project_id] = item.isExpanded()

        self.clear()
        projects_group = self._group_item("项目", "projects")
        tasks_group = self._group_item("普通任务", "tasks")
        selected_item: QtWidgets.QTreeWidgetItem | None = None
        project_count = 0
        task_count = 0

        for record in records:
            if not isinstance(record, dict) or record.get("kind") != "project":
                continue
            project_count += 1
            project_payload = dict(record)
            title = self._natural_title(record, "未命名项目")
            status = str(record.get("status_label") or "状态待同步")
            project_item = self._register(
                QtWidgets.QTreeWidgetItem((f"{title} · {status}",)),
                project_payload,
            )
            progress = record.get("progress")
            progress_label = (
                progress.get("label")
                if isinstance(progress, Mapping)
                else None
            )
            project_item.setToolTip(
                0,
                f"项目：{title}\n状态：{status}\n"
                f"进度：{progress_label or '待同步'}",
            )
            projects_group.addChild(project_item)
            project_id = record.get("project_id")
            project_item.setExpanded(
                expanded_projects.get(project_id, True)
                if isinstance(project_id, str)
                else True
            )
            if record.get("project_id") == selected_project_id:
                selected_item = project_item

            threads = record.get("threads")
            for thread in threads if isinstance(threads, (list, tuple)) else ():
                if not isinstance(thread, Mapping):
                    continue
                role_title = str(thread.get("role_title") or "项目角色")
                thread_title = self._natural_title(
                    thread,
                    f"{role_title} 任务",
                )
                thread_id = thread.get("thread_id")
                status_label = str(
                    thread.get("status_label") or "状态待同步"
                )
                payload = dict(thread)
                payload.update(
                    {
                        "kind": "thread",
                        "project_id": record.get("project_id"),
                        "name": thread_title,
                        "managed_project_thread": True,
                    }
                )
                thread_item = self._register(
                    QtWidgets.QTreeWidgetItem(
                        (f"{role_title} · {status_label}",)
                    ),
                    payload,
                )
                tooltip = f"{role_title}：{thread_title}"
                if isinstance(thread_id, str):
                    tooltip += f"\nCodex Thread ID：{thread_id}"
                thread_item.setToolTip(0, tooltip)
                if not isinstance(thread_id, str):
                    thread_item.setDisabled(True)
                project_item.addChild(thread_item)
                if thread_id == selected_thread_id:
                    selected_item = thread_item

        for record in records:
            if not isinstance(record, dict) or record.get("kind") == "project":
                continue
            thread_id = record.get("thread_id")
            if not isinstance(thread_id, str):
                continue
            task_count += 1
            payload = dict(record)
            payload["kind"] = "thread"
            payload["managed_project_thread"] = False
            title = self._natural_title(record, "未命名普通任务")
            payload["name"] = title
            item = self._register(QtWidgets.QTreeWidgetItem((title,)), payload)
            item.setToolTip(
                0,
                f"普通任务：{title}\nCodex Thread ID：{thread_id}",
            )
            tasks_group.addChild(item)
            if thread_id == selected_thread_id:
                selected_item = item

        if project_count == 0:
            self._placeholder(projects_group, "暂无项目")
        if task_count == 0:
            self._placeholder(tasks_group, "暂无普通任务")

        if selected_item is not None:
            parent = selected_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.setCurrentItem(selected_item)
            self.scrollToItem(selected_item)
        else:
            self.setCurrentItem(None)
            self.clearSelection()
        return self.currentHistoryIndex()

    def _emit_current_index(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        _previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        try:
            index = self._flat_items.index(current) if current is not None else -1
        except ValueError:
            index = -1
        self.currentIndexChanged.emit(index)

    def _emit_thread_open(
        self,
        item: QtWidgets.QTreeWidgetItem,
        _column: int,
    ) -> None:
        payload = self._payload(item)
        thread_id = payload.get("thread_id")
        if payload.get("kind") == "thread" and isinstance(thread_id, str):
            self.threadOpenRequested.emit(thread_id)

    # QComboBox-compatible helpers used by the established Panel history path.
    def addItem(self, label: str, data: Any = None) -> None:
        item = self._register(QtWidgets.QTreeWidgetItem((label,)), data)
        self.addTopLevelItem(item)
        if self.currentItem() is None:
            self.setCurrentItem(item)

    def count(self) -> int:
        return len(self._flat_items)

    def currentData(self) -> Any:
        return self._payload(self.currentItem()) or None

    def currentHistoryIndex(self) -> int:
        current = self.currentItem()
        try:
            return self._flat_items.index(current) if current is not None else -1
        except ValueError:
            return -1

    def setCurrentHistoryIndex(self, index: int) -> None:
        if 0 <= index < len(self._flat_items):
            self.setCurrentItem(self._flat_items[index])
        else:
            self.setCurrentItem(None)
            self.clearSelection()

    def itemText(self, index: int) -> str:
        return self._flat_items[index].text(0)

    def itemData(self, index: int) -> Any:
        return self._payload(self._flat_items[index]) or None

    def setItemData(self, index: int, data: Any, role: Any = None) -> None:
        item = self._flat_items[index]
        data_role = (
            QtCore.Qt.ItemDataRole.UserRole if role is None else role
        )
        item.setData(0, data_role, data)

    def clear(self) -> None:
        self._flat_items = []
        super().clear()


class ProjectTeamPage(QtWidgets.QScrollArea):
    """Render authoritative projects without creating another chat surface."""

    refreshRequested = QtCore.Signal()
    threadOpenRequested = QtCore.Signal(str)
    modelChooserRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectTeamPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QtWidgets.QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(7)

        heading = self._plain_label("项目与团队")
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        project_help = self._plain_label(
            "每个项目集中收纳监督、方案、执行和两类审查角色；"
            "普通任务单独列在项目外。"
        )
        project_help.setStyleSheet("color: #aeb7c2;")
        layout.addWidget(project_help)

        projects_group = QtWidgets.QGroupBox("项目与角色 Threads")
        projects_layout = QtWidgets.QVBoxLayout(projects_group)
        snapshot_actions = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新项目状态")
        self.settings_status_label = self._plain_label("尚未收到项目快照。")
        snapshot_actions.addWidget(self.refresh_button)
        snapshot_actions.addWidget(self.settings_status_label, 1)
        projects_layout.addLayout(snapshot_actions)
        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setObjectName("projectTeamTree")
        self.project_tree.setColumnCount(5)
        self.project_tree.setHeaderLabels(
            ("项目 / 角色", "Thread", "阶段 / 状态", "模型", "进度")
        )
        self.project_tree.setRootIsDecorated(True)
        self.project_tree.setAlternatingRowColors(True)
        self.project_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_tree.setMinimumHeight(210)
        header = self.project_tree.header()
        header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        for column in (2, 3, 4):
            header.setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            )
        projects_layout.addWidget(self.project_tree)
        self.empty_projects_label = self._plain_label(
            "尚无项目。提交任务后，项目容器及其角色 Thread 会显示在这里。"
        )
        self.empty_projects_label.setStyleSheet("color: #aeb7c2;")
        projects_layout.addWidget(self.empty_projects_label)

        self.selection_summary_label = self._plain_label("尚未选择项目。")
        projects_layout.addWidget(self.selection_summary_label)
        self.selection_progress = QtWidgets.QProgressBar()
        self.selection_progress.setRange(0, 100)
        self.selection_progress.setValue(0)
        self.selection_progress.setFormat("进度待同步")
        projects_layout.addWidget(self.selection_progress)

        thread_actions = QtWidgets.QHBoxLayout()
        self.open_thread_button = QtWidgets.QPushButton("打开所选 Thread")
        self.model_button = QtWidgets.QPushButton("选择指导模型")
        self.model_button.setToolTip(
            "使用 Panel 已有的模型选择器；所选模型会随发给该 Thread 的下一条指导生效，"
            "不会单独修改 Thread。"
        )
        thread_actions.addWidget(self.open_thread_button)
        thread_actions.addWidget(self.model_button)
        thread_actions.addStretch(1)
        projects_layout.addLayout(thread_actions)

        self.action_status_label = self._plain_label(
            "选择角色后打开；在中央输入区追加文字或图片，模型选择对下一条指导生效。"
        )
        projects_layout.addWidget(self.action_status_label)
        layout.addWidget(projects_group)

        layout.addStretch(1)
        self.setWidget(content)

        self._busy = False
        self._snapshot: dict[str, Any] = {}
        self._selected_project_id: str | None = None
        self._selected_thread_id: str | None = None
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        self.project_tree.currentItemChanged.connect(
            self._on_selection_changed
        )
        self.project_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._request_open_thread()
        )
        self.open_thread_button.clicked.connect(self._request_open_thread)
        self.model_button.clicked.connect(self._request_model_chooser)
        self._update_thread_actions()

    @staticmethod
    def _plain_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.refresh_button.setEnabled(not self._busy)
        self._update_thread_actions()

    def set_status(self, text: str) -> None:
        self.settings_status_label.setText(text)

    def set_action_status(self, text: str) -> None:
        self.action_status_label.setText(text)

    @staticmethod
    def _item_payload(item: QtWidgets.QTreeWidgetItem | None) -> dict[str, Any]:
        if item is None:
            return {}
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        return value if isinstance(value, dict) else {}

    def _selected_payload(self) -> dict[str, Any]:
        return self._item_payload(self.project_tree.currentItem())

    def _on_selection_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        _previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        payload = self._item_payload(current)
        kind = payload.get("kind")
        self._selected_project_id = (
            payload.get("project_id")
            if isinstance(payload.get("project_id"), str)
            else None
        )
        self._selected_thread_id = (
            payload.get("thread_id")
            if kind == "thread" and isinstance(payload.get("thread_id"), str)
            else None
        )
        progress = payload.get("progress")
        progress = progress if isinstance(progress, Mapping) else {}
        percent = progress.get("percent")
        self.selection_progress.setValue(
            percent if isinstance(percent, int) else 0
        )
        self.selection_progress.setFormat(str(progress.get("label") or "进度待同步"))
        if kind == "project":
            self.selection_summary_label.setText(
                f"项目：{payload.get('title') or '未命名项目'} · "
                f"{payload.get('status_label') or '状态待同步'} · "
                f"阶段：{payload.get('stage') or '待同步'}"
            )
        elif kind == "thread":
            self.selection_summary_label.setText(
                f"{payload.get('role_title') or '角色'} · "
                f"{payload.get('status_label') or '状态待同步'} · "
                f"{payload.get('model_label') or '模型待同步'}"
            )
        else:
            self.selection_summary_label.setText("尚未选择项目。")
        self._update_thread_actions()

    def _update_thread_actions(self) -> None:
        payload = self._selected_payload()
        thread_selected = payload.get("kind") == "thread"
        self.open_thread_button.setEnabled(
            thread_selected
            and payload.get("can_open") is True
            and not self._busy
        )
        self.model_button.setEnabled(
            thread_selected
            and payload.get("can_choose_model") is True
            and not self._busy
        )

    def _request_open_thread(self) -> None:
        payload = self._selected_payload()
        thread_id = payload.get("thread_id")
        if payload.get("can_open") is True and isinstance(thread_id, str):
            self.threadOpenRequested.emit(thread_id)

    def _request_model_chooser(self) -> None:
        payload = self._selected_payload()
        thread_id = payload.get("thread_id")
        if payload.get("can_choose_model") is True and isinstance(thread_id, str):
            self.modelChooserRequested.emit(thread_id)

    def select_project(self, project_id: str) -> bool:
        for index in range(self.project_tree.topLevelItemCount()):
            item = self.project_tree.topLevelItem(index)
            payload = self._item_payload(item)
            if payload.get("project_id") != project_id:
                continue
            self.project_tree.setCurrentItem(item)
            item.setExpanded(True)
            self.project_tree.scrollToItem(item)
            return True
        return False

    def select_thread(self, thread_id: str) -> bool:
        for index in range(self.project_tree.topLevelItemCount()):
            project_item = self.project_tree.topLevelItem(index)
            for child_index in range(project_item.childCount()):
                item = project_item.child(child_index)
                payload = self._item_payload(item)
                if payload.get("thread_id") != thread_id:
                    continue
                project_item.setExpanded(True)
                self.project_tree.setCurrentItem(item)
                self.project_tree.scrollToItem(item)
                return True
        return False

    def set_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        previous_project_id = self._selected_project_id
        previous_thread_id = self._selected_thread_id
        self._snapshot = dict(snapshot)
        if snapshot.get("available") is True:
            self.settings_status_label.setText("已同步项目快照。")
        else:
            self.settings_status_label.setText("尚未收到项目快照。")

        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        projects = snapshot.get("projects")
        project_values = projects if isinstance(projects, list) else []
        selected_item: QtWidgets.QTreeWidgetItem | None = None
        first_item: QtWidgets.QTreeWidgetItem | None = None
        for project in project_values:
            if not isinstance(project, Mapping):
                continue
            progress = project.get("progress")
            progress = progress if isinstance(progress, Mapping) else {}
            project_payload = {
                "kind": "project",
                "project_id": project.get("project_id"),
                "title": project.get("title"),
                "stage": project.get("stage"),
                "status_label": project.get("status_label"),
                "progress": dict(progress),
            }
            project_item = QtWidgets.QTreeWidgetItem(
                (
                    str(project.get("title") or "未命名项目"),
                    "",
                    str(
                        project.get("stage")
                        or project.get("status_label")
                        or "状态待同步"
                    ),
                    "",
                    str(progress.get("label") or "进度待同步"),
                )
            )
            project_item.setData(
                0, QtCore.Qt.ItemDataRole.UserRole, project_payload
            )
            project_item.setFirstColumnSpanned(False)
            self.project_tree.addTopLevelItem(project_item)
            project_item.setExpanded(True)
            if first_item is None:
                first_item = project_item
            if project.get("project_id") == previous_project_id:
                selected_item = project_item
            threads = project.get("threads")
            for thread in threads if isinstance(threads, list) else []:
                if not isinstance(thread, Mapping):
                    continue
                thread_progress = thread.get("progress")
                thread_progress = (
                    thread_progress
                    if isinstance(thread_progress, Mapping)
                    else {}
                )
                thread_payload = {
                    "kind": "thread",
                    "project_id": project.get("project_id"),
                    "thread_id": thread.get("thread_id"),
                    "role_title": thread.get("role_title"),
                    "status_label": thread.get("status_label"),
                    "model_label": thread.get("model_label"),
                    "progress": dict(thread_progress),
                    "can_open": thread.get("can_open") is True,
                    "can_choose_model": thread.get("can_choose_model") is True,
                    "can_guide": thread.get("can_guide") is True,
                }
                thread_item = QtWidgets.QTreeWidgetItem(
                    (
                        str(thread.get("role_title") or "角色"),
                        str(thread.get("title") or "Thread 待建立"),
                        str(thread.get("status_label") or "状态待同步"),
                        str(thread.get("model_label") or "模型待同步"),
                        str(thread_progress.get("label") or "进度待同步"),
                    )
                )
                thread_item.setData(
                    0, QtCore.Qt.ItemDataRole.UserRole, thread_payload
                )
                thread_id = thread.get("thread_id")
                if isinstance(thread_id, str):
                    thread_item.setToolTip(1, f"Thread ID：{thread_id}")
                project_item.addChild(thread_item)
                if thread_id == previous_thread_id:
                    selected_item = thread_item
        self.project_tree.blockSignals(False)
        self.empty_projects_label.setVisible(not bool(project_values))
        self.project_tree.setVisible(bool(project_values))
        target = selected_item or first_item
        if target is not None:
            self.project_tree.setCurrentItem(target)
            self._on_selection_changed(target, None)
        else:
            self._selected_project_id = None
            self._selected_thread_id = None
            self._on_selection_changed(None, None)
