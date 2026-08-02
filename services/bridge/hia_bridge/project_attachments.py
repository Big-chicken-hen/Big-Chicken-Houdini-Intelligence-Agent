"""Project-owned image paths for one explicit project start.

This module has no recovery, fallback, watcher, or persistence layer.  The
Panel writes selected images into one draft directory; project creation checks
those exact files once and moves them into the new project's directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable, Sequence


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_MAX_IMAGES = 16
_MAX_TOTAL_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class ProjectAttachmentCandidate:
    source_path: Path
    sha256: str
    suffix: str
    size: int


@dataclass(frozen=True)
class ProjectAttachmentRef:
    sha256: str
    file_name: str

    def __post_init__(self) -> None:
        _require_sha256(self.sha256)
        if self.file_name != f"{self.sha256}{Path(self.file_name).suffix.lower()}":
            raise ValueError("project attachment filename must be content-addressed")
        if Path(self.file_name).suffix.lower() not in _IMAGE_SUFFIXES:
            raise ValueError("project attachment filename has an unsupported suffix")


def inspect_project_draft(
    project_root: Path,
    draft_id: str | None,
    selected_paths: Sequence[str],
) -> tuple[ProjectAttachmentCandidate, ...]:
    """Validate and hash only the selected files in one project draft."""

    if len(selected_paths) > _MAX_IMAGES:
        raise ValueError(f"a project task accepts at most {_MAX_IMAGES} images")
    if not selected_paths:
        if draft_id not in {None, ""}:
            _safe_id(str(draft_id), "draft_id")
        return ()
    draft_id = _safe_id(draft_id, "draft_id")
    root = Path(project_root).resolve()
    draft_dir = (
        root / ".runtime" / "project-attachments" / "drafts" / draft_id
    ).resolve()
    _require_descendant(draft_dir, root)

    total = 0
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()
    candidates: list[ProjectAttachmentCandidate] = []
    for raw_path in selected_paths:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("project image path must be a non-empty string")
        path = Path(raw_path).resolve(strict=True)
        _require_descendant(path, draft_dir)
        if path in seen_paths:
            raise ValueError("project image paths must be unique")
        seen_paths.add(path)
        suffix = path.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES or not path.is_file():
            raise ValueError("project image must be PNG, JPG, JPEG, or WEBP")
        size = path.stat().st_size
        total += size
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("project images exceed 128 MiB")
        digest = _sha256(path)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        candidates.append(
            ProjectAttachmentCandidate(
                source_path=path,
                sha256=digest,
                suffix=suffix,
                size=size,
            )
        )
    return tuple(candidates)


def finalize_project_attachments(
    project_root: Path,
    project_id: str,
    candidates: Sequence[ProjectAttachmentCandidate],
) -> tuple[ProjectAttachmentRef, ...]:
    """Move validated draft images into their one final project directory."""

    project_id = _safe_id(project_id, "project_id")
    if project_id == "drafts":
        raise ValueError("project_id is reserved")
    root = Path(project_root).resolve()
    directory = (root / ".runtime" / "project-attachments" / project_id).resolve()
    _require_descendant(directory, root)
    if not candidates:
        return ()
    directory.mkdir(parents=True, exist_ok=True)
    references: list[ProjectAttachmentRef] = []
    for candidate in candidates:
        source = candidate.source_path.resolve(strict=True)
        if _sha256(source) != candidate.sha256:
            raise ValueError("project image changed after draft validation")
        file_name = f"{candidate.sha256}{candidate.suffix}"
        destination = directory / file_name
        if destination.exists():
            raise ValueError("project attachment destination already exists")
        os.replace(source, destination)
        references.append(
            ProjectAttachmentRef(
                sha256=candidate.sha256,
                file_name=file_name,
            )
        )
    return tuple(references)


def resolve_project_attachment_paths(
    project_root: Path,
    project_id: str,
    references: Iterable[ProjectAttachmentRef],
) -> tuple[str, ...]:
    """Resolve persisted content-addressed references without another owner."""

    project_id = _safe_id(project_id, "project_id")
    root = Path(project_root).resolve()
    directory = (root / ".runtime" / "project-attachments" / project_id).resolve()
    _require_descendant(directory, root)
    paths: list[str] = []
    for reference in references:
        path = (directory / reference.file_name).resolve(strict=True)
        _require_descendant(path, directory)
        if not path.is_file() or _sha256(path) != reference.sha256:
            raise ValueError("project attachment does not match its persisted hash")
        paths.append(str(path))
    return tuple(paths)


def _safe_id(value: str | None, name: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be one safe directory name")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("project attachment SHA-256 is invalid")


def _require_descendant(candidate: Path, parent: Path) -> None:
    try:
        candidate.relative_to(parent)
    except ValueError as exc:
        raise ValueError("project attachment path escapes its owned directory") from exc


__all__ = [
    "ProjectAttachmentCandidate",
    "ProjectAttachmentRef",
    "finalize_project_attachments",
    "inspect_project_draft",
    "resolve_project_attachment_paths",
]
