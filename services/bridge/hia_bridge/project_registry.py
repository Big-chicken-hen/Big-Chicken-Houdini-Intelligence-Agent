"""Single durable registry for project identity and restart boundaries.

Only the authoritative task (user text plus content-addressed attachment
references), current coarse state, current stage identity, five native role
Thread identities, requirements, and guidance revision cross a Bridge restart.
Role transcripts remain authoritative in native Codex Thread history;
execution receipts, plans, reviews, evidence bodies, pending actions, restart
replay, model settings, and host errors are deliberately not mirrored here.
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
    ProjectStatus,
    Requirement,
    RequirementStatus,
    Role,
    RoleThread,
    StageState,
    authoritative_task_identity,
)
from .project_attachments import (
    ProjectAttachmentRef,
    resolve_project_attachment_paths,
)


REGISTRY_SCHEMA = "hia-project-registry/4"
REGISTRY_MAX_BYTES = 16 * 1024 * 1024
_PERSISTED_KEYS = frozenset(
    {
        "project_id",
        "authoritative_task",
        "current_status",
        "current_stage_id",
        "role_thread_ids",
        "requirements",
        "guidance_revision",
    }
)
_RESTART_TERMINAL = frozenset(
    {ProjectStatus.COMPLETED, ProjectStatus.FAILED, ProjectStatus.STOPPED}
)


@dataclass(frozen=True)
class ProjectRecord:
    state: ProjectState
    authoritative_task_text: str
    attachments: tuple[ProjectAttachmentRef, ...] = ()

    def __post_init__(self) -> None:
        attachments = tuple(self.attachments)
        task_id, digest = authoritative_task_identity(
            self.authoritative_task_text,
            tuple(item.sha256 for item in attachments),
        )
        if task_id != self.state.authoritative_task_id:
            raise ValueError("authoritative task ID does not match the stored text")
        if digest != self.state.authoritative_task_sha256:
            raise ValueError("authoritative task hash does not match the stored text")
        if len({item.sha256 for item in attachments}) != len(attachments):
            raise ValueError("authoritative task attachment hashes must be unique")
        object.__setattr__(self, "attachments", attachments)


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.RLock()
        self._records = self._load_once()

    @property
    def path(self) -> Path:
        return self._path

    def list(self) -> tuple[ProjectRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            return self._records.get(project_id)

    def require(self, project_id: str) -> ProjectRecord:
        record = self.get(project_id)
        if record is None:
            raise KeyError(project_id)
        return record

    def attachment_paths(
        self,
        record: ProjectRecord,
        project_root: Path,
    ) -> tuple[str, ...]:
        if self.get(record.state.project_id) != record:
            raise ValueError("project attachment record is not current")
        return resolve_project_attachment_paths(
            project_root,
            record.state.project_id,
            record.attachments,
        )

    def put(self, record: ProjectRecord, *, expected_revision: int | None = None) -> None:
        with self._lock:
            existing = self._records.get(record.state.project_id)
            if expected_revision is not None:
                actual = existing.state.revision if existing is not None else None
                if actual != expected_revision:
                    raise ValueError(
                        f"project revision mismatch: expected {expected_revision}, got {actual}"
                    )
            self._records[record.state.project_id] = record
            self._validate_thread_uniqueness(self._records)
            self._write_all()

    def _load_once(self) -> dict[str, ProjectRecord]:
        if not self._path.exists():
            return {}
        if self._path.stat().st_size > REGISTRY_MAX_BYTES:
            raise ValueError("project registry exceeds its byte limit")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("project registry document is invalid")
        if raw.get("schema") != REGISTRY_SCHEMA:
            raise ValueError("UNSUPPORTED_LEGACY_SCHEMA: unsupported legacy schema")
        projects = raw.get("projects")
        if not isinstance(projects, list):
            raise ValueError("project registry projects must be a list")
        parsed: dict[str, ProjectRecord] = {}
        for item in projects:
            record = self._parse_entry(item)
            if record.state.project_id in parsed:
                raise ValueError("project registry contains a duplicate project_id")
            parsed[record.state.project_id] = record
        self._validate_thread_uniqueness(parsed)
        return parsed

    def _parse_entry(self, item: Any) -> ProjectRecord:
        if not isinstance(item, Mapping) or set(item) != _PERSISTED_KEYS:
            raise ValueError("project registry entry has unsupported fields")
        project_id = _required_text(item, "project_id")
        raw_task = item.get("authoritative_task")
        if not isinstance(raw_task, Mapping) or set(raw_task) != {
            "text",
            "attachments",
        }:
            raise ValueError("authoritative_task must contain text and attachments")
        task_text = raw_task.get("text")
        if not isinstance(task_text, str):
            raise ValueError("authoritative task text must be a string")
        raw_attachments = raw_task.get("attachments")
        if not isinstance(raw_attachments, list):
            raise ValueError("authoritative task attachments must be a list")
        attachments: list[ProjectAttachmentRef] = []
        for raw_attachment in raw_attachments:
            if not isinstance(raw_attachment, Mapping) or set(raw_attachment) != {
                "sha256",
                "file_name",
            }:
                raise ValueError("authoritative task attachment is malformed")
            attachments.append(
                ProjectAttachmentRef(
                    sha256=_required_text(raw_attachment, "sha256"),
                    file_name=_required_text(raw_attachment, "file_name"),
                )
            )
        persisted_status = ProjectStatus(_required_text(item, "current_status"))
        status = (
            persisted_status
            if persisted_status in _RESTART_TERMINAL
            else ProjectStatus.STOPPED
        )
        raw_stage_id = item.get("current_stage_id")
        if raw_stage_id is not None and not isinstance(raw_stage_id, str):
            raise ValueError("current_stage_id must be string or null")
        raw_roles = item.get("role_thread_ids")
        if not isinstance(raw_roles, Mapping) or set(raw_roles) != {r.value for r in Role}:
            raise ValueError("role_thread_ids must contain exactly five roles")
        roles: dict[Role, RoleThread] = {}
        for role in Role:
            thread_id = raw_roles.get(role.value)
            if not isinstance(thread_id, str) or not thread_id:
                raise ValueError("role Thread ID must be non-empty")
            roles[role] = RoleThread(role=role, thread_id=thread_id)

        raw_requirements = item.get("requirements")
        if not isinstance(raw_requirements, list):
            raise ValueError("requirements must be a list")
        requirements: list[Requirement] = []
        for raw_requirement in raw_requirements:
            if not isinstance(raw_requirement, Mapping):
                raise ValueError("requirement is malformed")
            requirements.append(
                Requirement(
                    requirement_id=_required_text(raw_requirement, "requirement_id"),
                    kind=_required_text(raw_requirement, "kind"),
                    status=RequirementStatus(
                        _required_text(raw_requirement, "status")
                    ),
                    source_ref=str(raw_requirement.get("source_ref") or ""),
                    superseded_by=(
                        str(raw_requirement["superseded_by"])
                        if raw_requirement.get("superseded_by") is not None
                        else None
                    ),
                )
            )
        guidance_revision = item.get("guidance_revision")
        if (
            not isinstance(guidance_revision, int)
            or isinstance(guidance_revision, bool)
            or guidance_revision < 0
        ):
            raise ValueError("guidance_revision must be a non-negative integer")
        task_id, digest = authoritative_task_identity(
            task_text,
            tuple(item.sha256 for item in attachments),
        )
        state = ProjectState(
            project_id=project_id,
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
            roles=roles,
            requirements=tuple(requirements),
            stage=StageState(stage_id=raw_stage_id),
            guidance_revision=guidance_revision,
            revision=0,
        )
        return ProjectRecord(state, task_text, tuple(attachments))

    @staticmethod
    def _validate_thread_uniqueness(records: Mapping[str, ProjectRecord]) -> None:
        thread_ids = [
            binding.thread_id
            for record in records.values()
            for binding in record.state.roles.values()
        ]
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("project registry reuses a Thread across projects")

    def _write_all(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema": REGISTRY_SCHEMA,
            "projects": [
                _record_to_entry(self._records[key]) for key in sorted(self._records)
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


def _record_to_entry(record: ProjectRecord) -> dict[str, Any]:
    state = record.state
    if set(state.roles) != set(Role):
        raise ValueError("a persisted project must contain exactly five role Threads")
    return {
        "project_id": state.project_id,
        "authoritative_task": {
            "text": record.authoritative_task_text,
            "attachments": [
                {
                    "sha256": item.sha256,
                    "file_name": item.file_name,
                }
                for item in record.attachments
            ],
        },
        "current_status": state.status.value,
        "current_stage_id": state.stage.stage_id,
        "role_thread_ids": {
            role.value: state.roles[role].thread_id for role in Role
        },
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "kind": item.kind,
                "status": item.status.value,
                "source_ref": item.source_ref,
                "superseded_by": item.superseded_by,
            }
            for item in state.requirements
        ],
        "guidance_revision": state.guidance_revision,
    }


def _required_text(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{name} must be a non-empty string")
    return item
