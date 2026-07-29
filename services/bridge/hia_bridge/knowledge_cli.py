"""Thin, bounded access to the project-local knowledge CLI."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_IMPORT_PATHS = 64
_MAX_SOURCE_ID_LENGTH = 512
_MAX_THREAD_MESSAGES = 10_000
_MAX_THREAD_MESSAGE_CHARS = 262_144
_MAX_THREAD_SNAPSHOT_BYTES = 4 * 1024 * 1024
_READ_TIMEOUT_SECONDS = 30.0
_THREAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_THREAD_SNAPSHOT_NAME = re.compile(
    r"thread-snapshot-[0-9a-f]{32}\.json\Z"
)
_SECRET_TEXT = re.compile(
    r"(?i)\b(token|secret|password|authorization|cookie|api[_-]?key)"
    r"\s*[:=]\s*\S+"
)
_JOB_ACTIONS = {
    "repair": "environment-repair",
    "import_files": "import-file",
    "import_folder": "import-folder",
    "delete": "delete",
    "rebuild": "index-build",
    "import_thread": "thread-import",
    "remove_thread": "thread-remove",
}


class KnowledgeCliError(RuntimeError):
    """Structured error raised by the fixed knowledge CLI adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = dict(details or {})


def _bounded_text(value: Any, limit: int = 2_048) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\x00", " ").split())
    return _SECRET_TEXT.sub(r"\1=[REDACTED]", text)[:limit]


def _safe_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _first_integer(*values: Any) -> int | None:
    for value in values:
        selected = _safe_integer(value)
        if selected is not None:
            return selected
    return None


def _is_reparse_point(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(value, "st_file_attributes", 0))
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & flag)


def _ordinary_project_file(project_root: Path, relative: str) -> Path:
    root = project_root.resolve(strict=True)
    parts = PurePosixPath(relative).parts
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise KnowledgeCliError(
            "KNOWLEDGE_CLI_UNAVAILABLE",
            "The project-local knowledge CLI is unavailable",
            http_status=503,
        ) from exc
    current = root
    if any(
        _is_reparse_point(current := current / part)
        for part in parts
    ) or not resolved.is_file():
        raise KnowledgeCliError(
            "KNOWLEDGE_CLI_UNAVAILABLE",
            "The project-local knowledge CLI is unavailable",
            http_status=503,
        )
    return resolved


def _project_directory(project_root: Path, relative: str) -> Path:
    root = project_root.resolve(strict=True)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            raise KnowledgeCliError(
                "UNSAFE_KNOWLEDGE_LOG_PATH",
                "The project knowledge log directory is unavailable",
                http_status=503,
            ) from exc
        if not current.is_dir() or _is_reparse_point(current):
            raise KnowledgeCliError(
                "UNSAFE_KNOWLEDGE_LOG_PATH",
                "The project knowledge log directory is unsafe",
                http_status=503,
            )
        try:
            current.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise KnowledgeCliError(
                "UNSAFE_KNOWLEDGE_LOG_PATH",
                "The project knowledge log directory escaped the project root",
                http_status=503,
            ) from exc
    return current


def _source_id(value: Any) -> str:
    if not isinstance(value, str):
        raise KnowledgeCliError(
            "INVALID_KNOWLEDGE_SOURCE_ID",
            "An exact managed source ID is required",
        )
    text = value.strip().replace("\\", "/")
    relative = PurePosixPath(text)
    if (
        not text
        or len(text) > _MAX_SOURCE_ID_LENGTH
        or text.startswith("/")
        or ":" in text
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise KnowledgeCliError(
            "INVALID_KNOWLEDGE_SOURCE_ID",
            "An exact managed source ID is required",
        )
    return text


def _selected_paths(value: Any, *, folders: bool) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_IMPORT_PATHS:
        raise KnowledgeCliError(
            "INVALID_KNOWLEDGE_IMPORT_PATHS",
            "Select one or more ordinary local paths",
        )
    selected: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or "\x00" in item
            or "\r" in item
            or "\n" in item
        ):
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_IMPORT_PATHS",
                "Select one or more ordinary local paths",
            )
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_IMPORT_PATHS",
                "Knowledge import paths must be absolute",
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_IMPORT_PATHS",
                "A selected knowledge path is unavailable",
            ) from exc
        valid_type = resolved.is_dir() if folders else resolved.is_file()
        if not valid_type or _is_reparse_point(candidate):
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_IMPORT_PATHS",
                "A selected knowledge path is not an ordinary local "
                + ("folder" if folders else "file"),
            )
        selected.append(str(resolved))
    return selected


def _thread_id(value: Any) -> str:
    if not isinstance(value, str):
        raise KnowledgeCliError(
            "INVALID_THREAD_ID",
            "An exact selected Thread ID is required",
        )
    selected = value.strip()
    if _THREAD_ID.fullmatch(selected) is None:
        raise KnowledgeCliError(
            "INVALID_THREAD_ID",
            "An exact selected Thread ID is required",
        )
    return selected


def _optional_thread_identifier(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    selected = value.strip()
    return selected if _THREAD_ID.fullmatch(selected) is not None else ""


def _thread_snapshot_bytes(
    thread_id: str,
    projection: Any,
) -> bytes:
    if (
        not isinstance(projection, Mapping)
        or projection.get("thread_id") != thread_id
        or not isinstance(projection.get("result"), Mapping)
    ):
        raise KnowledgeCliError(
            "INVALID_THREAD_PROJECTION",
            "The selected Thread projection was malformed",
            http_status=502,
        )
    thread = projection["result"].get("thread")
    if (
        not isinstance(thread, Mapping)
        or thread.get("id") != thread_id
        or not isinstance(thread.get("turns"), list)
    ):
        raise KnowledgeCliError(
            "INVALID_THREAD_PROJECTION",
            "The selected Thread projection was malformed",
            http_status=502,
        )

    messages: list[dict[str, Any]] = []
    for turn in thread["turns"]:
        if not isinstance(turn, Mapping) or not isinstance(
            turn.get("items"),
            list,
        ):
            raise KnowledgeCliError(
                "INVALID_THREAD_PROJECTION",
                "The selected Thread projection was malformed",
                http_status=502,
            )
        turn_id = _optional_thread_identifier(turn.get("id"))
        for item in turn["items"]:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            role = ""
            text_parts: list[str] = []
            if item_type == "userMessage":
                role = "user"
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for entry in content:
                    if (
                        isinstance(entry, Mapping)
                        and entry.get("type") == "text"
                        and isinstance(entry.get("text"), str)
                    ):
                        text_parts.append(entry["text"])
            elif item_type == "agentMessage" and isinstance(
                item.get("text"),
                str,
            ):
                role = "assistant"
                text_parts.append(item["text"])
            else:
                continue
            text = "\n".join(part for part in text_parts if part)
            if not text:
                continue
            if len(messages) >= _MAX_THREAD_MESSAGES:
                raise KnowledgeCliError(
                    "THREAD_SNAPSHOT_TOO_LARGE",
                    "The selected Thread exceeds the public message limit",
                    http_status=413,
                )
            message: dict[str, Any] = {
                "role": role,
                "content": text[:_MAX_THREAD_MESSAGE_CHARS],
            }
            item_id = _optional_thread_identifier(item.get("id"))
            if item_id:
                message["id"] = item_id
            if turn_id:
                message["turn_id"] = turn_id
            if role == "assistant":
                message["phase"] = "final_answer"
            messages.append(message)

    encoded = json.dumps(
        {
            "schema": "hia-thread-snapshot/1",
            "thread_id": thread_id,
            "messages": messages,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if not 0 < len(encoded) <= _MAX_THREAD_SNAPSHOT_BYTES:
        raise KnowledgeCliError(
            "THREAD_SNAPSHOT_TOO_LARGE",
            "The selected Thread exceeds the snapshot byte limit",
            http_status=413,
        )
    return encoded


def _snapshot_relative_path(project_root: Path, snapshot_path: Path) -> str:
    try:
        return snapshot_path.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise KnowledgeCliError(
            "UNSAFE_THREAD_SNAPSHOT",
            "The temporary Thread snapshot escaped the project root",
            http_status=503,
        ) from exc


def _write_thread_snapshot(
    project_root: Path,
    directory: Path,
    payload: bytes,
) -> tuple[Path, str]:
    snapshot_path = directory / f"thread-snapshot-{uuid.uuid4().hex}.json"
    try:
        with snapshot_path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        value = os.lstat(snapshot_path)
        resolved = snapshot_path.resolve(strict=True)
        resolved.relative_to(project_root)
        if (
            not stat.S_ISREG(value.st_mode)
            or _is_reparse_point(snapshot_path)
            or resolved.parent != directory.resolve(strict=True)
        ):
            raise OSError("temporary Thread snapshot is not an ordinary file")
    except (OSError, ValueError) as exc:
        _remove_thread_snapshot(project_root, snapshot_path)
        raise KnowledgeCliError(
            "UNSAFE_THREAD_SNAPSHOT",
            "The project-local Thread snapshot could not be created safely",
            http_status=503,
        ) from exc
    return (
        snapshot_path,
        _snapshot_relative_path(project_root, snapshot_path),
    )


def _remove_thread_snapshot(
    project_root: Path,
    snapshot_path: Path,
) -> str:
    try:
        relative = _snapshot_relative_path(project_root, snapshot_path)
    except KnowledgeCliError:
        return "Thread snapshot cleanup was refused: [outside project]"
    expected_directory = project_root / ".runtime" / "cache" / "knowledge-cli"
    if (
        snapshot_path.parent != expected_directory
        or _THREAD_SNAPSHOT_NAME.fullmatch(snapshot_path.name) is None
    ):
        return f"Thread snapshot cleanup was refused: {relative}"
    current = project_root
    for part in (".runtime", "cache", "knowledge-cli"):
        current = current / part
        if (
            not current.is_dir()
            or _is_reparse_point(current)
        ):
            return f"Thread snapshot cleanup was refused: {relative}"
        try:
            current.resolve(strict=True).relative_to(project_root)
        except (OSError, ValueError):
            return f"Thread snapshot cleanup was refused: {relative}"
    if not os.path.lexists(snapshot_path):
        return ""
    try:
        value = os.lstat(snapshot_path)
        if (
            not stat.S_ISREG(value.st_mode)
            or _is_reparse_point(snapshot_path)
            or snapshot_path.resolve(strict=True).parent
            != expected_directory.resolve(strict=True)
        ):
            return f"Thread snapshot cleanup was refused: {relative}"
        snapshot_path.unlink()
    except OSError:
        return f"Thread snapshot cleanup failed: {relative}"
    return ""


def _source_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    source_id = _source_id(value.get("source_id"))
    return {
        "source_id": source_id,
        "name": _bounded_text(value.get("name"), 512)
        or PurePosixPath(source_id).name,
        "original_path": _bounded_text(
            value.get("source") or value.get("original_path"),
            2_048,
        ),
        "format": _bounded_text(value.get("format"), 32),
        "size_bytes": _safe_integer(value.get("size_bytes")),
        "managed_path": _bounded_text(value.get("managed_path"), 2_048),
        "imported_at": _bounded_text(value.get("imported_at"), 128),
        "index_status": _bounded_text(value.get("index_status"), 64),
        "chunk_count": _safe_integer(value.get("chunk_count")),
        "vector_count": _safe_integer(value.get("vector_count")),
        "current": value.get("current") is True,
        "stale": value.get("stale") is True,
    }


def _source_projection(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    items: list[dict[str, Any]] = []
    raw_items = raw.get("items")
    if isinstance(raw_items, list):
        for item in raw_items[:200]:
            try:
                projected = _source_record(item)
            except KnowledgeCliError:
                continue
            if projected is not None:
                items.append(projected)
    total = _safe_integer(raw.get("total"))
    return {
        "items": items,
        "total": total,
        "deletion_notice": (
            "Deleting a managed copy never deletes the original file."
        ),
    }


def _index_projection(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    corpus = raw.get("corpus")
    corpus = corpus if isinstance(corpus, Mapping) else {}
    complete = raw.get("complete")
    return {
        "available": raw.get("available") is True,
        "fts5_available": raw.get("fts5_available") is True,
        "complete": complete if isinstance(complete, bool) else None,
        "document_count": _first_integer(
            raw.get("document_count"),
            corpus.get("documents"),
        ),
        "user_document_count": _first_integer(
            raw.get("user_document_count"),
            corpus.get("user_documents"),
        ),
        "chunk_count": _first_integer(
            raw.get("chunk_count"),
            raw.get("total_chunks"),
        ),
        "vector_count": _first_integer(
            raw.get("vector_count"),
            raw.get("vector_chunks"),
        ),
        "vector_pending": _first_integer(
            raw.get("vector_pending"),
            raw.get("pending_chunks"),
        ),
    }


def _environment_projection(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "state": _bounded_text(raw.get("state"), 64) or "missing",
        "embedding_mode": _bounded_text(raw.get("embedding_mode"), 64)
        or "fts5",
        "active_profile": _bounded_text(raw.get("active_profile"), 128),
        "fallback_non_blocking": raw.get("fallback_non_blocking") is not False,
    }


def _find_log_path(value: Any) -> str:
    if isinstance(value, Mapping):
        direct = value.get("log_path")
        if isinstance(direct, str) and direct.strip():
            return _bounded_text(direct, 2_048)
        for nested in value.values():
            found = _find_log_path(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_log_path(nested)
            if found:
                return found
    return ""


class KnowledgeCliRunner:
    """Run only the fixed project knowledge CLI, with at most one mutation job."""

    def __init__(
        self,
        project_root: str | os.PathLike[str] | None = None,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        run_factory: Callable[..., Any] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        configured_root = (
            str(project_root)
            if project_root is not None
            else os.environ.get("HIA_PROJECT_ROOT", "").strip()
        )
        root = (
            Path(configured_root)
            if configured_root
            else Path(__file__).resolve().parents[3]
        )
        self._project_root = root.resolve(strict=True)
        if not self._project_root.is_dir() or _is_reparse_point(self._project_root):
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_UNAVAILABLE",
                "The project root is not an ordinary directory",
                http_status=503,
            )
        self._script = _ordinary_project_file(
            self._project_root,
            "scripts/hia-knowledge.ps1",
        )
        self._popen_factory = popen_factory
        self._run_factory = run_factory
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._job: dict[str, Any] | None = None
        self._closed = False

    @property
    def project_root(self) -> Path:
        return self._project_root

    def handle(
        self,
        arguments: Mapping[str, Any],
        *,
        thread_reader: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_REQUEST",
                "Knowledge request must be an object",
            )
        action = arguments.get("action")
        if action == "status":
            self._require_fields(arguments, {"action"})
            return self._read_status()
        if action == "list":
            self._require_fields(arguments, {"action"})
            return self._read_sources()
        if action == "start":
            operation = arguments.get("operation")
            expected = {"action", "operation"}
            if operation == "import_files" or operation == "import_folder":
                expected.add("paths")
            elif operation == "delete":
                expected.add("source_id")
            elif operation in {"import_thread", "remove_thread"}:
                expected.add("thread_id")
            self._require_fields(arguments, expected)
            thread_projection: Mapping[str, Any] | None = None
            if operation == "import_thread":
                selected_thread_id = _thread_id(arguments.get("thread_id"))
                if thread_reader is None:
                    raise KnowledgeCliError(
                        "THREAD_READ_UNAVAILABLE",
                        "The selected Thread cannot be read by this Bridge",
                        http_status=503,
                    )
                thread_projection = thread_reader(selected_thread_id)
            return self._start_job(
                str(operation),
                arguments,
                thread_projection=thread_projection,
            )
        if action == "job_status":
            self._require_fields(arguments, {"action", "job_id"})
            return {
                "action": "job_status",
                "job": self._job_snapshot(arguments.get("job_id")),
            }
        if action == "cancel":
            self._require_fields(arguments, {"action", "job_id"})
            return {
                "action": "cancel",
                "job": self._cancel_job(arguments.get("job_id")),
            }
        raise KnowledgeCliError(
            "INVALID_KNOWLEDGE_ACTION",
            "Knowledge action must be status, list, start, job_status, or cancel",
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._refresh_job_locked()
            job = self._job
            process = job.get("process") if isinstance(job, dict) else None
            operation = job.get("operation") if isinstance(job, dict) else None
        if (
            process is not None
            and process.poll() is None
            and operation != "repair"
        ):
            self._terminate_process(process)
            with self._lock:
                if self._job is job and job.get("state") == "running":
                    job["state"] = "cancelled"
                    job["error"] = {
                        "code": "KNOWLEDGE_JOB_CANCELLED",
                        "message": "The Bridge closed this knowledge job.",
                    }
                    self._cleanup_job_snapshot_locked(job)

    def _require_fields(
        self,
        arguments: Mapping[str, Any],
        expected: set[str],
    ) -> None:
        if set(arguments) != expected:
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_REQUEST",
                "Knowledge request fields do not match the selected action",
            )

    def _powershell(self) -> Path:
        system_root = os.environ.get("SystemRoot", "").strip()
        if not system_root:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_UNAVAILABLE",
                "Windows PowerShell is unavailable",
                http_status=503,
            )
        candidate = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_UNAVAILABLE",
                "Windows PowerShell is unavailable",
                http_status=503,
            ) from exc
        if not resolved.is_file() or _is_reparse_point(candidate):
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_UNAVAILABLE",
                "Windows PowerShell is unavailable",
                http_status=503,
            )
        return resolved

    def _command(self, cli_action: str, extra: Sequence[str] = ()) -> list[str]:
        return [
            str(self._powershell()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._script),
            "-Action",
            cli_action,
            *extra,
            "-Json",
        ]

    def _safe_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        sensitive_names = (
            "HIA_BRIDGE_TOKEN",
            "HIA_MCP_V2_TOKEN",
            "FXHOUDINIMCP_TOKEN",
            "OPENAI_API_KEY",
        )
        for name in sensitive_names:
            environment.pop(name, None)
        return environment

    def _run_read(self, cli_action: str) -> dict[str, Any]:
        if self._closed:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_CLOSED",
                "The knowledge CLI adapter is closing",
                http_status=503,
            )
        try:
            completed = self._run_factory(
                self._command(cli_action),
                cwd=str(self._project_root),
                env=self._safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_READ_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_TIMEOUT",
                "The project knowledge status request timed out",
                http_status=504,
            ) from exc
        except OSError as exc:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_UNAVAILABLE",
                "The project knowledge CLI could not start",
                http_status=503,
            ) from exc
        stdout = bytes(completed.stdout or b"")
        stderr = bytes(completed.stderr or b"")
        payload = self._parse_output(stdout)
        wrapper_success = payload.get("ok") is True
        core_success = payload.get("event") == "completed"
        if completed.returncode != 0 or not (wrapper_success or core_success):
            self._raise_cli_failure(payload, stderr)
        result = payload.get("result") if wrapper_success else payload
        if not isinstance(result, Mapping):
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_CLI_RESPONSE",
                "The project knowledge CLI returned an invalid result",
                http_status=502,
            )
        return dict(result)

    def _read_status(self) -> dict[str, Any]:
        result = self._run_read("status")
        environment = _environment_projection(result.get("environment"))
        index = _index_projection(result.get("index"))
        built_in = self._pack_manifest_metadata()
        if environment["state"] == "ready":
            try:
                index_status = self._run_read("index-status")
            except KnowledgeCliError:
                index_status = {}
            core_index = (
                index_status.get("index")
                if isinstance(index_status.get("index"), Mapping)
                else {}
            )
            if core_index:
                core_projection = _index_projection(core_index)
                for key, value in core_projection.items():
                    if key in {"available", "fts5_available"}:
                        if key in core_index:
                            index[key] = value
                    elif value is not None:
                        index[key] = value
                corpus = core_index.get("corpus")
                corpus = corpus if isinstance(corpus, Mapping) else {}
                raw_pack = corpus.get("builtin_pack")
                raw_pack = raw_pack if isinstance(raw_pack, Mapping) else {}
                complete = raw_pack.get("complete")
                built_in = {
                    "installed": (
                        raw_pack.get("installed")
                        if isinstance(raw_pack.get("installed"), bool)
                        else None
                    ),
                    "pack_id": _bounded_text(raw_pack.get("pack_id"), 256),
                    "version": _bounded_text(
                        raw_pack.get("pack_version"),
                        128,
                    ),
                    "card_count": _safe_integer(raw_pack.get("cards")),
                    "complete": (
                        complete if isinstance(complete, bool) else None
                    ),
                }
        return {
            "action": "status",
            "environment": environment,
            "index": index,
            "sources": _source_projection(result.get("sources")),
            "built_in": built_in,
            "job": self._running_job_snapshot(),
        }

    def _read_sources(self) -> dict[str, Any]:
        result = self._run_read("list")
        return {
            "action": "list",
            "sources": _source_projection(result),
        }

    def _pack_manifest_metadata(self) -> dict[str, Any]:
        """Read only bounded release metadata; never infer runtime index state."""

        fallback = {
            "installed": None,
            "pack_id": "",
            "version": "",
            "card_count": None,
            "complete": None,
        }
        try:
            manifest_path = _ordinary_project_file(
                self._project_root,
                "knowledge/sidefx-official/manifest.json",
            )
            raw = manifest_path.read_bytes()
            if not 0 < len(raw) <= 1_048_576:
                return fallback
            manifest = json.loads(raw.decode("utf-8"))
        except (KnowledgeCliError, OSError, UnicodeError, json.JSONDecodeError):
            return fallback
        if not isinstance(manifest, Mapping):
            return fallback
        sources = manifest.get("sources")
        return {
            **fallback,
            "pack_id": _bounded_text(manifest.get("pack_id"), 256),
            "version": _bounded_text(
                manifest.get("pack_version")
                or manifest.get("version")
                or manifest.get("schema_version"),
                128,
            ),
            "card_count": len(sources) if isinstance(sources, list) else None,
        }

    def _start_job(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        thread_projection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cli_action = _JOB_ACTIONS.get(operation)
        if cli_action is None:
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_OPERATION",
                "Knowledge operation must be repair, import_files, import_folder, "
                "delete, rebuild, import_thread, or remove_thread",
            )
        extra: list[str] = []
        source_id = ""
        selected_thread_id = ""
        snapshot_payload: bytes | None = None
        if operation == "import_files":
            paths = _selected_paths(arguments.get("paths"), folders=False)
            extra = ["-Path", *paths]
        elif operation == "import_folder":
            paths = _selected_paths(arguments.get("paths"), folders=True)
            if len(paths) != 1:
                raise KnowledgeCliError(
                    "INVALID_KNOWLEDGE_IMPORT_PATHS",
                    "Select exactly one ordinary local folder",
                )
            extra = ["-Path", paths[0]]
        elif operation == "delete":
            source_id = _source_id(arguments.get("source_id"))
            extra = ["-SourceId", source_id]
        elif operation == "rebuild":
            extra = ["-BatchSize", "32"]
        elif operation in {"import_thread", "remove_thread"}:
            selected_thread_id = _thread_id(arguments.get("thread_id"))
            extra = ["-ThreadId", selected_thread_id]
            if operation == "import_thread":
                snapshot_payload = _thread_snapshot_bytes(
                    selected_thread_id,
                    thread_projection,
                )

        with self._lock:
            if self._closed:
                raise KnowledgeCliError(
                    "KNOWLEDGE_CLI_CLOSED",
                    "The knowledge CLI adapter is closing",
                    http_status=503,
                )
            self._refresh_job_locked()
            if (
                isinstance(self._job, dict)
                and self._job.get("state") == "running"
            ):
                raise KnowledgeCliError(
                    "KNOWLEDGE_JOB_ACTIVE",
                    "Another knowledge operation is already running",
                    http_status=409,
                    details={"job_id": self._job["job_id"]},
                )
            job_id = uuid.uuid4().hex
            log_directory = _project_directory(
                self._project_root,
                ".runtime/cache/knowledge-cli",
            )
            log_path = log_directory / f"panel-{job_id}.log"
            snapshot_path: Path | None = None
            snapshot_relative = ""
            if snapshot_payload is not None:
                snapshot_path, snapshot_relative = _write_thread_snapshot(
                    self._project_root,
                    log_directory,
                    snapshot_payload,
                )
                extra.extend(["-SnapshotFile", snapshot_relative])
            command = self._command(cli_action, extra)
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                with log_path.open("xb") as output:
                    process = self._popen_factory(
                        command,
                        cwd=str(self._project_root),
                        env=self._safe_environment(),
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        creationflags=creationflags,
                    )
            except OSError as exc:
                cleanup_warning = (
                    _remove_thread_snapshot(
                        self._project_root,
                        snapshot_path,
                    )
                    if snapshot_path is not None
                    else ""
                )
                details = {"log_path": str(log_path)}
                if cleanup_warning:
                    details["warning"] = cleanup_warning
                raise KnowledgeCliError(
                    "KNOWLEDGE_CLI_UNAVAILABLE",
                    "The project knowledge operation could not start",
                    http_status=503,
                    details=details,
                ) from exc
            self._job = {
                "job_id": job_id,
                "operation": operation,
                "cli_action": cli_action,
                "source_id": source_id,
                "thread_id": selected_thread_id,
                "state": "running",
                "cancellable": operation in {
                    "import_files",
                    "import_folder",
                    "rebuild",
                    "import_thread",
                },
                "started_at": self._monotonic(),
                "log_path": str(log_path),
                "process": process,
                "snapshot_path": snapshot_path,
                "snapshot_relative": snapshot_relative,
                "warnings": [],
                "result": None,
                "error": None,
            }
            return {"action": "start", "job": self._public_job(self._job)}

    def _job_snapshot(self, value: Any) -> dict[str, Any]:
        job_id = self._exact_job_id(value)
        with self._lock:
            self._refresh_job_locked()
            if not isinstance(self._job, dict) or self._job.get("job_id") != job_id:
                raise KnowledgeCliError(
                    "KNOWLEDGE_JOB_NOT_FOUND",
                    "The requested knowledge job is unavailable",
                    http_status=404,
                )
            return self._public_job(self._job)

    def _running_job_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            self._refresh_job_locked()
            if (
                isinstance(self._job, dict)
                and self._job.get("state") == "running"
            ):
                return self._public_job(self._job)
        return None

    def _cancel_job(self, value: Any) -> dict[str, Any]:
        job_id = self._exact_job_id(value)
        with self._lock:
            self._refresh_job_locked()
            if not isinstance(self._job, dict) or self._job.get("job_id") != job_id:
                raise KnowledgeCliError(
                    "KNOWLEDGE_JOB_NOT_FOUND",
                    "The requested knowledge job is unavailable",
                    http_status=404,
                )
            job = self._job
            process = job.get("process")
            if job.get("state") == "running" and process is not None:
                if job.get("cancellable") is not True:
                    raise KnowledgeCliError(
                        "KNOWLEDGE_JOB_NOT_CANCELLABLE",
                        "This knowledge operation has no safe cancellation "
                        "contract and will continue in the background",
                        http_status=409,
                        details={"job_id": job_id},
                    )
                self._terminate_process(process)
                job["state"] = "cancelled"
                job["error"] = {
                    "code": "KNOWLEDGE_JOB_CANCELLED",
                    "message": (
                        "This CLI operation was stopped. Completed safe steps "
                        "were not rolled back."
                    ),
                }
                self._cleanup_job_snapshot_locked(job)
            return self._public_job(job)

    def _exact_job_id(self, value: Any) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{32}", value) is None
        ):
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_JOB_ID",
                "An exact knowledge job ID is required",
            )
        return value

    def _refresh_job_locked(self) -> None:
        job = self._job
        if not isinstance(job, dict) or job.get("state") != "running":
            return
        process = job.get("process")
        return_code = process.poll()
        if return_code is None:
            return
        try:
            raw = Path(job["log_path"]).read_bytes()
            payload = self._parse_output(raw)
        except (OSError, KnowledgeCliError) as exc:
            try:
                raw_text = Path(job["log_path"]).read_text(
                    encoding="utf-8-sig",
                    errors="replace",
                )
            except OSError:
                raw_text = ""
            detail = _bounded_text(raw_text, 2_048)
            payload = {}
            job["state"] = "failed"
            job["error"] = {
                "code": "INVALID_KNOWLEDGE_CLI_RESPONSE",
                "message": detail
                or _bounded_text(str(exc), 1_024)
                or "The project knowledge CLI returned invalid output",
            }
            self._cleanup_job_snapshot_locked(job)
            return
        wrapper_success = payload.get("ok") is True
        core_success = payload.get("event") == "completed"
        repair_success = (
            job.get("operation") == "repair"
            and isinstance(payload.get("status"), str)
        )
        if return_code != 0 or not (
            wrapper_success or core_success or repair_success
        ):
            error = payload.get("error")
            job["state"] = "failed"
            job["error"] = {
                "code": _bounded_text(
                    error.get("code") if isinstance(error, Mapping) else None,
                    128,
                )
                or "KNOWLEDGE_CLI_FAILED",
                "message": _bounded_text(
                    error.get("message") if isinstance(error, Mapping) else None,
                    2_048,
                )
                or "The project knowledge operation failed",
            }
            self._cleanup_job_snapshot_locked(job)
            return
        result = payload.get("result") if wrapper_success else payload
        job["state"] = "completed"
        job["result"] = self._job_result(
            str(job.get("operation") or ""),
            result,
            payload,
            str(job.get("source_id") or ""),
            str(job.get("thread_id") or ""),
        )
        self._cleanup_job_snapshot_locked(job)

    def _cleanup_job_snapshot_locked(self, job: dict[str, Any]) -> None:
        snapshot_path = job.get("snapshot_path")
        if not isinstance(snapshot_path, Path):
            return
        warning = _remove_thread_snapshot(
            self._project_root,
            snapshot_path,
        )
        job["snapshot_path"] = None
        if warning:
            warnings = job.setdefault("warnings", [])
            if isinstance(warnings, list) and warning not in warnings:
                warnings.append(warning)

    def _job_result(
        self,
        operation: str,
        result: Any,
        payload: Mapping[str, Any],
        source_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        raw = result if isinstance(result, Mapping) else {}
        projected: dict[str, Any] = {
            "log_path": _find_log_path(payload),
        }
        if operation in {"import_files", "import_folder"}:
            projected.update(
                {
                    "imported": _safe_integer(raw.get("imported")),
                    "already_imported": _safe_integer(
                        raw.get("already_imported")
                    ),
                }
            )
        elif operation == "delete":
            projected.update(
                {
                    "source_id": _bounded_text(
                        raw.get("source_id") or source_id,
                        _MAX_SOURCE_ID_LENGTH,
                    ),
                    "deleted": raw.get("deleted") is True,
                    "original_deleted": raw.get("original_deleted") is True,
                }
            )
        elif operation == "rebuild":
            index_value = raw.get("index") if isinstance(raw.get("index"), Mapping) else raw
            projected["index"] = _index_projection(index_value)
        elif operation == "import_thread":
            projected.update(
                {
                    "thread_id": _bounded_text(
                        raw.get("thread_id") or thread_id,
                        256,
                    ),
                    "status": _bounded_text(raw.get("status"), 64),
                    "messages_received": _safe_integer(
                        raw.get("messages_received")
                    ),
                    "records_indexed": _safe_integer(
                        raw.get("records_indexed")
                    ),
                    "messages_excluded": _safe_integer(
                        raw.get("messages_excluded")
                    ),
                }
            )
        elif operation == "remove_thread":
            projected.update(
                {
                    "thread_id": _bounded_text(
                        raw.get("thread_id") or thread_id,
                        256,
                    ),
                    "status": _bounded_text(raw.get("status"), 64),
                }
            )
        return projected

    def _public_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        elapsed = max(0.0, self._monotonic() - float(job["started_at"]))
        result = job.get("result")
        error = job.get("error")
        warnings = job.get("warnings")
        return {
            "job_id": job["job_id"],
            "operation": job["operation"],
            "state": job["state"],
            "cancellable": job.get("cancellable") is True,
            "elapsed_seconds": round(elapsed, 1),
            "log_path": job["log_path"],
            "result": dict(result) if isinstance(result, Mapping) else None,
            "error": dict(error) if isinstance(error, Mapping) else None,
            "warnings": (
                [_bounded_text(value, 2_048) for value in warnings[:8]]
                if isinstance(warnings, list)
                else []
            ),
        }

    def _parse_output(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > _MAX_OUTPUT_BYTES:
            raise KnowledgeCliError(
                "KNOWLEDGE_CLI_RESPONSE_TOO_LARGE",
                "The project knowledge CLI response was too large",
                http_status=502,
            )
        try:
            lines = raw.decode("utf-8-sig").splitlines()
        except UnicodeError as exc:
            raise KnowledgeCliError(
                "INVALID_KNOWLEDGE_CLI_RESPONSE",
                "The project knowledge CLI returned invalid text",
                http_status=502,
            ) from exc
        for line in reversed(lines):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise KnowledgeCliError(
            "INVALID_KNOWLEDGE_CLI_RESPONSE",
            "The project knowledge CLI returned invalid JSON",
            http_status=502,
        )

    def _raise_cli_failure(
        self,
        payload: Mapping[str, Any],
        stderr: bytes,
    ) -> None:
        error = payload.get("error")
        code = (
            _bounded_text(error.get("code"), 128)
            if isinstance(error, Mapping)
            else ""
        )
        message = (
            _bounded_text(error.get("message"), 2_048)
            if isinstance(error, Mapping)
            else ""
        )
        if not message and stderr:
            message = _bounded_text(stderr.decode("utf-8", "replace"), 2_048)
        raise KnowledgeCliError(
            code or "KNOWLEDGE_CLI_FAILED",
            message or "The project knowledge CLI failed",
            http_status=503,
        )

    def _terminate_process(self, process: Any) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                self._run_factory(
                    [
                        "taskkill.exe",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
