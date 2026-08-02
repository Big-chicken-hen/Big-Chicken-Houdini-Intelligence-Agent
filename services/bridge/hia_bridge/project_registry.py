"""Atomic project identity and state persistence.

The registry owns durable Project/Role/Thread/Goal associations and the original
user text.  It does not run projects or infer membership from titles, cwd, or
model names.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping

from .project_contracts import (
    ProjectState,
    authoritative_task_identity,
    project_state_from_dict,
    project_state_to_dict,
)


REGISTRY_SCHEMA = "hia-project-registry/2"
REGISTRY_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ProjectAttachment:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ProjectRecord:
    state: ProjectState
    authoritative_task_text: str
    attachments: tuple[ProjectAttachment, ...] = ()

    def __post_init__(self) -> None:
        task_id, digest = authoritative_task_identity(self.authoritative_task_text)
        if task_id != self.state.authoritative_task_id:
            raise ValueError("authoritative task ID does not match the stored text")
        if digest != self.state.authoritative_task_sha256:
            raise ValueError("authoritative task hash does not match the stored text")


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def list(self) -> tuple[ProjectRecord, ...]:
        with self._lock:
            records = self._read_all()
            return tuple(records[key] for key in sorted(records))

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            return self._read_all().get(project_id)

    def require(self, project_id: str) -> ProjectRecord:
        record = self.get(project_id)
        if record is None:
            raise KeyError(project_id)
        return record

    def put(self, record: ProjectRecord, *, expected_revision: int | None = None) -> None:
        with self._lock:
            records = self._read_all()
            existing = records.get(record.state.project_id)
            if expected_revision is not None:
                actual = existing.state.revision if existing is not None else None
                if actual != expected_revision:
                    raise ValueError(
                        f"project revision mismatch: expected {expected_revision}, got {actual}"
                    )
            records[record.state.project_id] = record
            self._write_all(records)

    def _read_all(self) -> dict[str, ProjectRecord]:
        if not self._path.exists():
            return {}
        if self._path.stat().st_size > REGISTRY_MAX_BYTES:
            raise ValueError("project registry exceeds its byte limit")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != REGISTRY_SCHEMA:
            raise ValueError("project registry schema is invalid")
        projects = raw.get("projects")
        if not isinstance(projects, list):
            raise ValueError("project registry projects must be a list")
        parsed: dict[str, ProjectRecord] = {}
        for item in projects:
            if not isinstance(item, dict):
                raise ValueError("project registry entry is malformed")
            text = item.get("authoritative_task_text")
            state = item.get("state")
            if not isinstance(text, str) or not isinstance(state, dict):
                raise ValueError("project registry entry is incomplete")
            raw_attachments = item.get("attachments", [])
            if not isinstance(raw_attachments, list):
                raise ValueError("project attachments must be a list")
            attachments: list[ProjectAttachment] = []
            for raw_attachment in raw_attachments:
                if not isinstance(raw_attachment, Mapping):
                    raise ValueError("project attachment is malformed")
                path = raw_attachment.get("path")
                digest = raw_attachment.get("sha256")
                size = raw_attachment.get("size_bytes")
                if (
                    not isinstance(path, str)
                    or not path
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or size < 0
                ):
                    raise ValueError("project attachment identity is invalid")
                attachments.append(ProjectAttachment(path, digest, size))
            record = ProjectRecord(
                project_state_from_dict(state), text, tuple(attachments)
            )
            if record.state.project_id in parsed:
                raise ValueError("project registry contains a duplicate project_id")
            parsed[record.state.project_id] = record
        all_threads: list[str] = []
        for record in parsed.values():
            all_threads.extend(binding.thread_id for binding in record.state.roles.values())
        if len(all_threads) != len(set(all_threads)):
            raise ValueError("project registry reuses a Thread across projects")
        return parsed

    def _write_all(self, records: dict[str, ProjectRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema": REGISTRY_SCHEMA,
            "projects": [
                {
                    "authoritative_task_text": record.authoritative_task_text,
                    "attachments": [
                        {
                            "path": attachment.path,
                            "sha256": attachment.sha256,
                            "size_bytes": attachment.size_bytes,
                        }
                        for attachment in record.attachments
                    ],
                    "state": project_state_to_dict(record.state),
                }
                for _, record in sorted(records.items())
            ],
        }
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > REGISTRY_MAX_BYTES:
            raise ValueError("project registry exceeds its byte limit")
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, self._path)
