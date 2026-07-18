"""Project-local storage for images attached to a Codex turn."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Union


def _default_project_root() -> Path:
    configured = os.environ.get("HIA_PROJECT_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3]


_DEFAULT_PROJECT_ROOT = _default_project_root()
_SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_SAFE_THREAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class AttachmentStore:
    """Copy turn images into ``.runtime/attachments/<thread-id>``."""

    def __init__(
        self,
        project_root: Union[str, Path] = _DEFAULT_PROJECT_ROOT,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._attachments_root = (
            self._project_root / ".runtime" / "attachments"
        ).resolve()
        self._require_descendant(self._attachments_root, self._project_root)

    def copy_file(self, thread_id: str, source: Union[str, Path]) -> str:
        """Copy one supported image without replacing an existing attachment."""

        source_path = Path(source)
        suffix = source_path.suffix.lower()
        if suffix not in _SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError("attachment must be PNG, JPG, JPEG, or WEBP")
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))

        directory = self._thread_directory(thread_id)
        destination = self._unique_path(directory, suffix)
        with source_path.open("rb") as source_file, destination.open("xb") as target_file:
            shutil.copyfileobj(source_file, target_file)
        return str(destination)

    def clipboard_path(self, thread_id: str) -> str:
        """Return a unique project-local PNG path suitable for ``QImage.save``."""

        directory = self._thread_directory(thread_id)
        return str(self._unique_path(directory, ".png"))

    def new_clipboard_path(self, thread_id: str) -> str:
        """Compatibility spelling for callers that prefer an explicit verb."""

        return self.clipboard_path(thread_id)

    def _thread_directory(self, thread_id: str) -> Path:
        if not isinstance(thread_id, str) or not _SAFE_THREAD_ID.fullmatch(thread_id):
            raise ValueError("thread_id must be one safe directory name")
        if thread_id.upper() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("thread_id is reserved on Windows")

        directory = (self._attachments_root / thread_id).resolve()
        self._require_descendant(directory, self._attachments_root)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _unique_path(directory: Path, suffix: str) -> Path:
        while True:
            candidate = directory / f"{uuid.uuid4().hex}{suffix}"
            if not candidate.exists():
                return candidate

    @staticmethod
    def _require_descendant(candidate: Path, parent: Path) -> None:
        try:
            candidate.relative_to(parent)
        except ValueError as error:
            raise ValueError("attachment path escapes the project root") from error


__all__ = ["AttachmentStore"]
