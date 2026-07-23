"""Native PySide6 message composer helpers for the Big-Chicken Houdini Intelligence Agent Panel."""

from __future__ import annotations

import math
import os
from typing import Iterable

from PySide6 import QtCore, QtGui, QtWidgets


class ExpandableTextEdit(QtWidgets.QTextEdit):
    """A compact QTextEdit that grows naturally without disturbing IME input."""

    sendRequested = QtCore.Signal()
    imagePasted = QtCore.Signal(object)

    _MIN_VISIBLE_LINES = 4
    _MIN_HEIGHT = 96
    _MAX_HEIGHT = 180

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self._send_shortcuts: list[QtGui.QShortcut] = []
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QtGui.QShortcut(
                QtGui.QKeySequence(sequence),
                self,
            )
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self.sendRequested.emit)
            self._send_shortcuts.append(shortcut)

        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._update_height()
        )
        self.document().contentsChanged.connect(self._update_height)
        self._update_height()

    def insertFromMimeData(self, source: QtCore.QMimeData) -> None:  # noqa: N802
        if source.hasImage():
            self.imagePasted.emit(source.imageData())
            return
        super().insertFromMimeData(source)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_height()

    def _update_height(self) -> None:
        minimum_height = min(
            self._MAX_HEIGHT,
            max(
                self._MIN_HEIGHT,
                (self.fontMetrics().lineSpacing() * self._MIN_VISIBLE_LINES)
                + (self.frameWidth() * 2)
                + 12,
            ),
        )
        document_height = self.document().documentLayout().documentSize().height()
        content_height = math.ceil(document_height) + (self.frameWidth() * 2) + 10
        target = max(minimum_height, min(self._MAX_HEIGHT, content_height))
        if (
            self.minimumHeight() == minimum_height
            and self.maximumHeight() == target
        ):
            return
        self.setMinimumHeight(minimum_height)
        self.setMaximumHeight(target)
        self.updateGeometry()


class AttachmentStrip(QtWidgets.QWidget):
    """A non-owning thumbnail strip for image paths queued for one turn."""

    _SUPPORTED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("hiaAttachmentStrip")
        self._paths: list[str] = []
        self._items: dict[str, QtWidgets.QWidget] = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setObjectName("hiaAttachmentScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setMaximumHeight(108)
        outer.addWidget(self._scroll_area)

        self._content = QtWidgets.QWidget()
        self._content.setObjectName("hiaAttachmentContent")
        self._layout = QtWidgets.QHBoxLayout(self._content)
        self._layout.setContentsMargins(2, 3, 2, 3)
        self._layout.setSpacing(7)
        self._layout.addStretch(1)
        self._scroll_area.setWidget(self._content)
        self.hide()

    def paths(self) -> list[str]:
        return list(self._paths)

    def set_paths(self, paths: Iterable[str | os.PathLike[str]]) -> None:
        self.clear()
        for path in paths:
            self.add_path(path)

    def add_path(self, path: str | os.PathLike[str]) -> bool:
        display_path = os.fspath(path)
        if not isinstance(display_path, str) or not display_path:
            return False
        if os.path.splitext(display_path)[1].lower() not in self._SUPPORTED_SUFFIXES:
            return False
        key = self._path_key(display_path)
        if key in self._items:
            return False

        item = self._make_item(display_path)
        self._paths.append(display_path)
        self._items[key] = item
        self._layout.insertWidget(max(0, self._layout.count() - 1), item)
        self.show()
        return True

    def remove(self, path: str | os.PathLike[str]) -> bool:
        display_path = os.fspath(path)
        key = self._path_key(display_path)
        item = self._items.pop(key, None)
        if item is None:
            return False
        self._paths = [
            existing
            for existing in self._paths
            if self._path_key(existing) != key
        ]
        self._layout.removeWidget(item)
        item.hide()
        item.setParent(None)
        item.deleteLater()
        if not self._paths:
            self.hide()
        return True

    def clear(self) -> None:
        for item in tuple(self._items.values()):
            self._layout.removeWidget(item)
            item.hide()
            item.setParent(None)
            item.deleteLater()
        self._items.clear()
        self._paths.clear()
        self.hide()

    def _make_item(self, path: str) -> QtWidgets.QWidget:
        item = QtWidgets.QFrame()
        item.setObjectName("hiaAttachmentItem")
        item.setProperty("attachmentPath", path)
        item.setToolTip(path)
        item.setStyleSheet(
            "QFrame#hiaAttachmentItem { background: #292f37;"
            " border: 1px solid #3f4854; border-radius: 6px; }"
        )
        layout = QtWidgets.QVBoxLayout(item)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        thumbnail = QtWidgets.QLabel()
        thumbnail.setObjectName("hiaAttachmentThumbnail")
        thumbnail.setFixedSize(70, 58)
        thumbnail.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            thumbnail.setText("图片")
            thumbnail.setStyleSheet("color: #9aa4b0; background: #20242a;")
        else:
            thumbnail.setPixmap(
                pixmap.scaled(
                    thumbnail.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(thumbnail)

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(3)
        filename = QtWidgets.QLabel(os.path.basename(path) or path)
        filename.setObjectName("hiaAttachmentFilename")
        filename.setMaximumWidth(82)
        filename.setToolTip(path)
        filename.setStyleSheet("color: #cbd2da; font-size: 9px;")
        footer.addWidget(filename, 1)

        remove_button = QtWidgets.QToolButton()
        remove_button.setObjectName("hiaAttachmentRemoveButton")
        remove_button.setText("移除")
        remove_button.setAutoRaise(True)
        remove_button.setToolTip("移除此图片")
        remove_button.setStyleSheet("color: #b9c1ca; font-size: 9px;")
        remove_button.clicked.connect(
            lambda _checked=False, attachment_path=path: self.remove(attachment_path)
        )
        footer.addWidget(remove_button)
        layout.addLayout(footer)
        return item

    @staticmethod
    def _path_key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))
