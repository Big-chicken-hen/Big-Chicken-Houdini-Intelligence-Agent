"""Validation-only Windows path policy.

This module deliberately contains no deletion, move, cleanup, quarantine, or
filesystem-writing operation.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_project_root() -> Path:
    configured = os.environ.get("HIA_PROJECT_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _default_project_root()
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


@dataclass(frozen=True)
class PathPolicyError(ValueError):
    """Structured, JSON-serializable path rejection."""

    code: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "structured_error": {
                "code": self.code,
                "message": self.message,
                "path": self.path,
            },
        }


def _reject(code: str, message: str, path: str | None) -> None:
    raise PathPolicyError(code=code, message=message, path=path)


def _is_reparse_point(path: Path) -> bool:
    """Return whether an existing path is any Windows reparse point."""

    try:
        file_attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _check_existing_components_for_reparse(root: Path, candidate: Path) -> None:
    current = root
    if _is_reparse_point(current):
        _reject("REPARSE_POINT", "Project root is a reparse point", str(root))

    relative_parts = candidate.parts[len(root.parts) :]
    for part in relative_parts:
        current = current / part
        if _is_reparse_point(current):
            _reject("REPARSE_POINT", "Path traverses a reparse point", str(current))


def validate_project_subpath(
    raw_path: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] = PROJECT_ROOT,
) -> Path:
    """Validate and return an ordinary strict child of the approved project root.

    Relative paths are interpreted beneath ``project_root``. Absolute paths must
    already be beneath it. Comparison follows Windows case-insensitive semantics.
    The function performs validation only and never creates or changes a path.
    """

    try:
        raw = os.fspath(raw_path)
    except TypeError:
        _reject("INVALID_TYPE", "Path must be a string or path-like value", None)

    if not isinstance(raw, str):
        _reject("INVALID_TYPE", "Byte paths are not accepted", None)
    if not raw or not raw.strip():
        _reject("EMPTY_PATH", "Path must not be empty", raw)
    if "\x00" in raw:
        _reject("INVALID_PATH", "Path must not contain a NUL character", raw)

    root_raw = os.fspath(project_root)
    if not isinstance(root_raw, str) or not root_raw.strip():
        _reject("INVALID_ROOT", "Project root must be a non-empty string path", None)

    lowered_raw = raw.casefold()
    lowered_root = root_raw.casefold()
    if raw.startswith(("\\\\", "//", "\\??\\")):
        _reject("UNC_OR_DEVICE_PATH", "UNC and device paths are forbidden", raw)
    if lowered_raw.startswith(("\\\\?\\", "\\\\.\\")):
        _reject("UNC_OR_DEVICE_PATH", "UNC and device paths are forbidden", raw)
    if root_raw.startswith(("\\\\", "//", "\\??\\")) or lowered_root.startswith(
        ("\\\\?\\", "\\\\.\\")
    ):
        _reject("INVALID_ROOT", "Project root must not be UNC or a device path", root_raw)

    root_drive, root_tail = ntpath.splitdrive(root_raw)
    raw_drive, raw_tail = ntpath.splitdrive(raw)
    if not root_drive or not ntpath.isabs(root_raw):
        _reject("INVALID_ROOT", "Project root must be an absolute local path", root_raw)
    if ntpath.normpath(root_tail) in ("\\", "/", "."):
        _reject("DRIVE_ROOT", "A drive root is forbidden", root_raw)

    if raw_drive and ntpath.normcase(raw_tail) in ("\\", "/", "."):
        _reject("DRIVE_ROOT", "A drive root is forbidden", raw)
    if ":" in raw_tail:
        _reject("ADS_FORBIDDEN", "Alternate data stream syntax is forbidden", raw)

    raw_parts = [part.casefold() for part in raw_tail.replace("/", "\\").split("\\")]
    if "appdata" in raw_parts:
        _reject("APPDATA_FORBIDDEN", "AppData paths are forbidden", raw)

    normalized_root = ntpath.normpath(ntpath.abspath(root_raw))
    if raw_drive or ntpath.isabs(raw):
        normalized_candidate = ntpath.normpath(ntpath.abspath(raw))
    else:
        normalized_candidate = ntpath.normpath(ntpath.join(normalized_root, raw))

    root_compare = ntpath.normcase(normalized_root)
    candidate_compare = ntpath.normcase(normalized_candidate)
    try:
        common = ntpath.commonpath((root_compare, candidate_compare))
    except ValueError:
        _reject("OUTSIDE_PROJECT", "Path is on another drive or outside the project", raw)
    if common != root_compare:
        _reject("OUTSIDE_PROJECT", "Path escapes the project root", raw)
    if candidate_compare == root_compare:
        _reject("PROJECT_ROOT_FORBIDDEN", "Only strict project subpaths are allowed", raw)

    candidate_parts = [part.casefold() for part in Path(normalized_candidate).parts]
    if "appdata" in candidate_parts:
        _reject("APPDATA_FORBIDDEN", "AppData paths are forbidden", raw)

    root_path = Path(normalized_root)
    candidate_path = Path(normalized_candidate)
    _check_existing_components_for_reparse(root_path, candidate_path)
    return candidate_path
