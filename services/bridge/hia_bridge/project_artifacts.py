"""Small durable store for structured project-team work products.

The lifecycle state keeps identities and counters.  Potentially larger plan,
review, execution and repair payloads live here so they do not get copied into
every project snapshot or prompt.  This store makes no quality judgement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping


ARTIFACT_SCHEMA = "hia-project-artifacts/1"
ARTIFACT_MAX_BYTES = 32 * 1024 * 1024


class ProjectArtifactStore:
    """Atomic project-local JSON storage with explicit project/effect keys."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def project(self, project_id: str) -> dict[str, Any]:
        _require_id(project_id, "project_id")
        with self._lock:
            value = self._read().get("projects", {}).get(project_id, {})
            if not isinstance(value, Mapping):
                raise ValueError("project artifact record is malformed")
            return _json_copy(value)

    def put_effect(
        self,
        project_id: str,
        effect_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> None:
        _require_id(project_id, "project_id")
        _require_id(effect_id, "effect_id")
        _require_id(kind, "kind")
        safe_payload = _json_copy(payload)
        with self._lock:
            root = self._read()
            projects = root.setdefault("projects", {})
            project = projects.setdefault(project_id, {"effects": {}})
            effects = project.setdefault("effects", {})
            existing = effects.get(effect_id)
            value = {"kind": kind, "payload": safe_payload}
            if existing is not None and existing != value:
                raise ValueError("effect artifact identity was reused with new content")
            effects[effect_id] = value
            self._write(root)

    def put_named(self, project_id: str, name: str, payload: Any) -> None:
        _require_id(project_id, "project_id")
        _require_id(name, "artifact name")
        safe_payload = _json_copy(payload)
        with self._lock:
            root = self._read()
            projects = root.setdefault("projects", {})
            project = projects.setdefault(project_id, {"effects": {}})
            project[name] = safe_payload
            self._write(root)

    def get_named(self, project_id: str, name: str) -> Any:
        _require_id(project_id, "project_id")
        _require_id(name, "artifact name")
        return self.project(project_id).get(name)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": ARTIFACT_SCHEMA, "projects": {}}
        if self._path.stat().st_size > ARTIFACT_MAX_BYTES:
            raise ValueError("project artifacts exceed their byte limit")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != ARTIFACT_SCHEMA
            or not isinstance(raw.get("projects"), dict)
        ):
            raise ValueError("project artifact store schema is invalid")
        return raw

    def _write(self, value: Mapping[str, Any]) -> None:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > ARTIFACT_MAX_BYTES:
            raise ValueError("project artifacts exceed their byte limit")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, self._path)


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("project artifacts must be JSON values") from exc


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
