"""Project-local knowledge, source, memory, and vector-index CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO


_SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_CORE = _SOURCE_PROJECT_ROOT / "src"
_SOURCE_RUNTIME = _SOURCE_PROJECT_ROOT / "houdini_package" / "python_libs"
for _source_path in (_SOURCE_RUNTIME, _SOURCE_CORE):
    if str(_source_path) not in sys.path:
        sys.path.insert(0, str(_source_path))

from hia_core.embedding_contract import (  # noqa: E402
    DEFAULT_EMBEDDING_PROFILE,
    EMBEDDING_DEVICE_ENVIRONMENT,
    EMBEDDING_PROFILE_ENVIRONMENT,
    EMBEDDING_PYTHON_ENVIRONMENT,
    KNOWLEDGE_INDEX_CLI_PROTOCOL,
    KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE,
    KNOWLEDGE_INDEX_MAX_BATCH_SIZE,
    PROFILE_REGISTRY,
    runtime_layout,
)

if __package__:
    from .deterministic_sources import (  # noqa: E402
        CAPTION_SUFFIXES,
        MAX_SOURCE_BYTES,
        MEDIA_SUFFIXES,
        SourceAdapterError,
        find_media_sidecar,
        normalize_selected_file,
        normalize_thread_export,
    )
    from .hybrid_knowledge import (  # noqa: E402
        HybridKnowledgeError,
        HybridKnowledgeStore,
    )
    from .knowledge_index import (  # noqa: E402
        KnowledgeIndexError,
        LocalKnowledgeIndex,
        MAX_DOCUMENT_BYTES,
        USER_OPTIONAL_SUFFIXES,
        USER_TEXT_SUFFIXES,
        builtin_pack_status,
    )
else:
    from hia_mcp_runtime.deterministic_sources import (  # noqa: E402
        CAPTION_SUFFIXES,
        MAX_SOURCE_BYTES,
        MEDIA_SUFFIXES,
        SourceAdapterError,
        find_media_sidecar,
        normalize_selected_file,
        normalize_thread_export,
    )
    from hia_mcp_runtime.hybrid_knowledge import (  # noqa: E402
        HybridKnowledgeError,
        HybridKnowledgeStore,
    )
    from hia_mcp_runtime.knowledge_index import (  # noqa: E402
        KnowledgeIndexError,
        LocalKnowledgeIndex,
        MAX_DOCUMENT_BYTES,
        USER_OPTIONAL_SUFFIXES,
        USER_TEXT_SUFFIXES,
        builtin_pack_status,
    )


PROJECT_ROOT_ENVIRONMENT = "HIA_PROJECT_ROOT"
OUTPUT_FORMATS = ("jsonl", "json")
SUPPORTED_SOURCE_SUFFIXES = USER_TEXT_SUFFIXES | USER_OPTIONAL_SUFFIXES
IMPORT_SOURCE_SUFFIXES = SUPPORTED_SOURCE_SUFFIXES | MEDIA_SUFFIXES
SOURCE_SIDECAR_SCHEMA = "hia-managed-source/1"
THREAD_SNAPSHOT_SCHEMA = "hia-thread-snapshot/1"
MAX_THREAD_SNAPSHOT_BYTES = MAX_SOURCE_BYTES
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500
EXIT_RUNTIME_ERROR = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_NOT_FOUND = 3
EXIT_INTERRUPTED = 130


class _CliFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 1,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.recoverable = recoverable


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliFailure(
            "INVALID_ARGUMENTS",
            message,
            exit_code=2,
        )


class _Emitter:
    def __init__(self, output: TextIO, output_format: str) -> None:
        self._output = output
        self._format = output_format
        self._sequence = 0

    def emit(self, event: str, action: str, **fields: Any) -> None:
        self._sequence += 1
        payload = {
            "protocol": KNOWLEDGE_INDEX_CLI_PROTOCOL,
            "event": event,
            "action": action,
            "sequence": self._sequence,
            "timestamp_utc": _utc_now(),
            **fields,
        }
        if self._format == "json" and event not in {"completed", "error"}:
            return
        self._output.write(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        self._output.flush()


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="hia knowledge")
    parser.add_argument(
        "--project-root",
        default="",
        help=("Project root; defaults to HIA_PROJECT_ROOT or the package checkout"),
    )
    parser.add_argument(
        "--format",
        choices=OUTPUT_FORMATS,
        default="jsonl",
        help="Output one JSON result or a streaming JSONL event sequence",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "bootstrap",
        help="Idempotently install and index the bundled offline knowledge pack",
    )
    commands.add_parser("status", help="Report vector-index status")
    build = commands.add_parser("build", help="Build all pending vector batches")
    build.add_argument(
        "--batch-size",
        type=int,
        default=KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE,
        help=(f"Committed chunks per batch (1-{KNOWLEDGE_INDEX_MAX_BATCH_SIZE})"),
    )

    sources = commands.add_parser("sources", help="Manage copied user sources")
    source_commands = sources.add_subparsers(
        dest="source_command",
        required=True,
    )
    source_list = source_commands.add_parser("list", help="List managed sources")
    _pagination(source_list)
    source_import = source_commands.add_parser(
        "import",
        help="Copy one supported file into project-managed sources",
    )
    source_import.add_argument("--path", required=True)
    source_delete = source_commands.add_parser(
        "delete",
        help="Delete one exact managed source and its index rows",
    )
    source_delete.add_argument("--source-id", required=True)
    source_commands.add_parser(
        "refresh",
        help="Refresh only the managed user-source corpus",
    )

    thread = commands.add_parser(
        "thread",
        help="Import or remove one explicitly selected Thread snapshot",
    )
    thread_commands = thread.add_subparsers(
        dest="thread_command",
        required=True,
    )
    thread_import = thread_commands.add_parser(
        "import",
        help="Replace one Thread's indexed public messages from bounded JSON",
    )
    thread_import.add_argument("--thread-id", required=True)
    thread_import.add_argument("--snapshot-file", required=True)
    thread_remove = thread_commands.add_parser(
        "remove",
        help="Remove all indexed messages for one Thread",
    )
    thread_remove.add_argument("--thread-id", required=True)

    memory = commands.add_parser("memory", help="Manage explicit project memory")
    memory_commands = memory.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_list = memory_commands.add_parser("list", help="List project memories")
    _memory_filters(memory_list)
    memory_record = memory_commands.add_parser(
        "record",
        help="Record one explicit durable memory",
    )
    _memory_write_fields(memory_record, include_id=False)
    memory_delete = memory_commands.add_parser(
        "delete",
        help="Delete one project memory",
    )
    memory_delete.add_argument("--memory-id", required=True)
    memory_supersede = memory_commands.add_parser(
        "supersede",
        help="Replace one active project memory",
    )
    _memory_write_fields(memory_supersede, include_id=True)
    return parser


def _pagination(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT)


def _memory_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--memory-type",
        choices=("decision", "preference", "asset", "lesson", "workflow"),
        default="",
    )
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--scope", default="project")
    parser.add_argument("--include-superseded", action="store_true")
    _pagination(parser)


def _memory_write_fields(
    parser: argparse.ArgumentParser,
    *,
    include_id: bool,
) -> None:
    if include_id:
        parser.add_argument("--memory-id", required=True)
    parser.add_argument(
        "--memory-type",
        choices=("decision", "preference", "asset", "lesson", "workflow"),
        required=True,
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--scope", default="")
    parser.add_argument("--source-thread-id", default="")
    parser.add_argument("--source-turn-id", default="")


def _project_root(raw: str) -> Path:
    selected = raw.strip() or os.environ.get(PROJECT_ROOT_ENVIRONMENT, "").strip()
    root = Path(selected).resolve() if selected else _SOURCE_PROJECT_ROOT
    required = (
        root / "src" / "hia_core" / "embedding_contract.py",
        root
        / "houdini_package"
        / "python_libs"
        / "hia_mcp_runtime"
        / "hybrid_knowledge.py",
    )
    if not all(path.is_file() for path in required):
        raise _CliFailure(
            "PROJECT_ROOT_INVALID",
            "The selected HIA project root is missing required runtime files",
            exit_code=2,
        )
    project_source = root / "src"
    if str(project_source) not in sys.path:
        sys.path.insert(0, str(project_source))
    return root


def _batch_size(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= KNOWLEDGE_INDEX_MAX_BATCH_SIZE:
        raise _CliFailure(
            "INVALID_ARGUMENTS",
            f"batch-size must be between 1 and {KNOWLEDGE_INDEX_MAX_BATCH_SIZE}",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return value


def _open_store(
    project_root: Path,
    *,
    read_only: bool = False,
) -> HybridKnowledgeStore:
    index = LocalKnowledgeIndex(
        project_root,
        initialize=not read_only,
    )
    return HybridKnowledgeStore(project_root, index=index)


def _output_format(argv: Sequence[str] | None) -> str:
    values = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(values):
        if value.startswith("--format="):
            selected = value.partition("=")[2]
            return selected if selected in OUTPUT_FORMATS else "jsonl"
        if value == "--format" and index + 1 < len(values):
            selected = values[index + 1]
            return selected if selected in OUTPUT_FORMATS else "jsonl"
    return "jsonl"


def _action(arguments: argparse.Namespace) -> str:
    command = str(arguments.command)
    if command == "sources":
        return f"sources.{arguments.source_command}"
    if command == "thread":
        return f"thread.{arguments.thread_command}"
    if command == "memory":
        return f"memory.{arguments.memory_command}"
    return command


def _validated_pagination(
    arguments: argparse.Namespace,
    *,
    maximum: int = MAX_LIST_LIMIT,
) -> tuple[int, int]:
    offset = arguments.offset
    limit = arguments.limit
    if (
        isinstance(offset, bool)
        or isinstance(limit, bool)
        or not isinstance(offset, int)
        or not isinstance(limit, int)
        or offset < 0
        or not 1 <= limit <= maximum
    ):
        raise _CliFailure(
            "INVALID_ARGUMENTS",
            f"offset must be non-negative and limit must be 1-{maximum}",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return offset, limit


def _runtime_status_path(
    raw_value: str,
    *,
    default: str,
    allowed_root: str,
) -> tuple[Path, bool]:
    selected = raw_value.strip()
    candidate = Path(selected) if selected else Path(default)
    if selected and not candidate.is_absolute():
        return candidate, False
    try:
        resolved = candidate.resolve()
        safe = resolved.is_relative_to(Path(allowed_root).resolve())
    except (OSError, ValueError):
        return candidate, False
    return resolved, safe


def _status_details(
    store: HybridKnowledgeStore | None,
    project_root: Path,
) -> dict[str, Any]:
    layout = runtime_layout(project_root)
    if store is None:
        index_status = {
            "available": False,
            "status": "not_initialized",
            "requested_profile": (
                os.environ.get(EMBEDDING_PROFILE_ENVIRONMENT, "").strip()
                or DEFAULT_EMBEDDING_PROFILE
            ),
            "active_profile": "",
            "profile_id": "",
            "model_id": "",
            "model_revision": "",
            "dim": 0,
            "normalized": False,
            "degraded": True,
            "fallback_reason": "KNOWLEDGE_INDEX_NOT_INITIALIZED",
            "repair": {"action": "sources.refresh_or_build"},
            "complete": False,
            "partial": False,
            "vector_chunks": 0,
            "total_chunks": 0,
            "pending_chunks": 0,
            "chunks_indexed_this_call": 0,
            "last_batch_count": 0,
            "corpus": {
                "documents": 0,
                "chunks": 0,
                "builtin_pack": builtin_pack_status(project_root),
                "user_documents": 0,
                "memory_documents": 0,
                "houdini_documents": 0,
                "project_documents": 0,
            },
        }
        embedder = None
        database_path = Path(layout["knowledge_database"])
    else:
        index_status = dict(store.status())
        if "corpus" not in index_status:
            corpus_status = getattr(store.index, "corpus_status", None)
            if callable(corpus_status):
                index_status["corpus"] = corpus_status()
        embedder = getattr(store, "_embedder", None)
        database_path = Path(store.index.database_path)
    raw_runtime: dict[str, Any] = {}
    status_method = getattr(embedder, "status", None)
    if callable(status_method):
        value = status_method()
        if isinstance(value, Mapping):
            raw_runtime = dict(value)

    configured_python = str(
        getattr(embedder, "_python_path", "")
        or os.environ.get(EMBEDDING_PYTHON_ENVIRONMENT, "")
    )
    python_path, python_path_valid = _runtime_status_path(
        configured_python,
        default=layout["worker_python"],
        allowed_root=layout["venv_root"],
    )
    active_profile = str(
        raw_runtime.get("active_profile")
        or index_status.get("active_profile")
        or os.environ.get(EMBEDDING_PROFILE_ENVIRONMENT, "").strip()
        or DEFAULT_EMBEDDING_PROFILE
    )
    model_directories = getattr(embedder, "_model_directories", {})
    model_path_value = (
        model_directories.get(active_profile)
        if isinstance(model_directories, Mapping)
        else None
    )
    if model_path_value is None:
        profile = PROFILE_REGISTRY.get(active_profile)
        if profile is not None:
            configured_model = os.environ.get(
                profile.model_dir_environment,
                "",
            ).strip()
            model_path_value = (
                configured_model
                or str(project_root / profile.model_directory)
            )
    model_path, model_path_valid = _runtime_status_path(
        str(model_path_value or ""),
        default=str(
            project_root
            / PROFILE_REGISTRY[active_profile].model_directory
        )
        if active_profile in PROFILE_REGISTRY
        else layout["models_root"],
        allowed_root=layout["models_root"],
    )
    device = str(
        getattr(embedder, "_device", "")
        or os.environ.get(EMBEDDING_DEVICE_ENVIRONMENT, "").strip()
        or "auto"
    )
    cuda_available = raw_runtime.get("cuda_available")
    if not isinstance(cuda_available, bool):
        cuda_available = None
    venv_path = (
        python_path.parent.parent
        if python_path.name.casefold().startswith("python")
        and python_path.parent.name.casefold() == "scripts"
        else Path(layout["venv_root"])
    )
    installed = raw_runtime.get("installed")
    ready = raw_runtime.get("ready")
    degraded = raw_runtime.get("degraded")
    runtime_status = str(
        raw_runtime.get("status")
        or (index_status.get("status") if store is not None else "")
        or "unavailable"
    )
    embedding = {
        **raw_runtime,
        "available": bool(
            raw_runtime.get("available", False)
            or raw_runtime.get("ready", False)
            or (store is not None and index_status.get("available", False))
        ),
        "status": runtime_status,
        "installed": installed if isinstance(installed, bool) else False,
        "ready": ready if isinstance(ready, bool) else False,
        "degraded": (
            degraded
            if isinstance(degraded, bool)
            else bool(store is not None and index_status.get("degraded", False))
        ),
        "device": device,
        "cuda_available": cuda_available,
        "cuda_status": (
            "reported" if cuda_available is not None else "not_reported"
        ),
        "fallback_reason": str(
            raw_runtime.get("fallback_reason")
            or (
                index_status.get("fallback_reason")
                if store is not None
                else ""
            )
            or (
                "EMBEDDING_RUNTIME_PATH_INVALID"
                if not python_path_valid or not model_path_valid
                else ""
            )
            or ""
        ),
    }
    runtime = {
        "python": {
            "path": str(python_path),
            "exists": python_path_valid and python_path.is_file(),
            "path_valid": python_path_valid,
        },
        "venv": {
            "path": str(venv_path),
            "exists": python_path_valid and venv_path.is_dir(),
        },
        "model": {
            "profile_id": active_profile,
            "model_id": str(
                raw_runtime.get("model_id")
                or index_status.get("model_id")
                or ""
            ),
            "path": str(model_path),
            "exists": model_path_valid and model_path.is_dir(),
            "path_valid": model_path_valid,
        },
        "index": {
            "path": str(database_path),
            "exists": database_path.is_file(),
        },
    }
    installation = {
        "installed": installed if isinstance(installed, bool) else None,
        "worker_python_exists": python_path_valid and python_path.is_file(),
        "venv_exists": python_path_valid and venv_path.is_dir(),
        "model_exists": model_path_valid and model_path.is_dir(),
    }
    return {
        "index": index_status,
        "embedding": embedding,
        "runtime": runtime,
        "installation": installation,
    }


def _source_root(index: Any) -> tuple[Path, Path]:
    project_root = Path(index.project_root).resolve(strict=True)
    raw_parts = (
        project_root / ".runtime",
        project_root / ".runtime" / "knowledge",
        project_root / ".runtime" / "knowledge" / "sources",
    )
    for part in raw_parts:
        if not os.path.lexists(part):
            raise _CliFailure(
                "SOURCE_ROOT_UNAVAILABLE",
                "The managed source directory is unavailable",
            )
        if _is_reparse_point(part):
            raise _CliFailure(
                "UNSAFE_MANAGED_SOURCE_PATH",
                "The managed source directory contains a reparse point",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
    raw_root = raw_parts[-1]
    try:
        resolved_root = raw_root.resolve(strict=True)
        resolved_root.relative_to(project_root)
    except (OSError, ValueError) as exc:
        raise _CliFailure(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source directory escaped the project root",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    if not resolved_root.is_dir():
        raise _CliFailure(
            "SOURCE_ROOT_UNAVAILABLE",
            "The managed source path is not a directory",
        )
    return raw_root, resolved_root


def _is_reparse_point(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(value, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & reparse_flag)


def _source_relative(value: str) -> Path:
    text = value.strip().replace("\\", "/")
    relative = Path(text)
    if (
        not text
        or relative.is_absolute()
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _CliFailure(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "source-id must be an exact relative managed-file path",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    if relative.suffix.casefold() not in SUPPORTED_SOURCE_SUFFIXES:
        raise _CliFailure(
            "UNSUPPORTED_SOURCE_TYPE",
            "The managed source type is unsupported",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return relative


def _managed_file(
    index: Any,
    source_id: str,
    *,
    must_exist: bool = True,
) -> tuple[Path, Path, Path]:
    raw_root, resolved_root = _source_root(index)
    relative = _source_relative(source_id)
    candidate = raw_root / relative
    current = raw_root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _is_reparse_point(current):
            raise _CliFailure(
                "UNSAFE_MANAGED_SOURCE_PATH",
                "The managed source path contains a reparse point",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
    if must_exist and not os.path.lexists(candidate):
        raise _CliFailure(
            "SOURCE_NOT_FOUND",
            "The managed source was not found",
            exit_code=EXIT_NOT_FOUND,
        )
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise _CliFailure(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source escaped its project directory",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    if must_exist:
        value = os.lstat(candidate)
        if not stat.S_ISREG(value.st_mode) or _is_reparse_point(candidate):
            raise _CliFailure(
                "UNSAFE_MANAGED_SOURCE_PATH",
                "The managed source is not an ordinary file",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
    return candidate, resolved, relative


def _sidecar_path(source_path: Path) -> Path:
    return Path(str(source_path) + ".metadata.json")


def _validated_sidecar(
    index: Any,
    source_path: Path,
) -> Path | None:
    sidecar = _sidecar_path(source_path)
    if not os.path.lexists(sidecar):
        return None
    _raw_root, resolved_root = _source_root(index)
    try:
        resolved = sidecar.resolve(strict=True)
        resolved.relative_to(resolved_root)
        value = os.lstat(sidecar)
    except (OSError, ValueError) as exc:
        raise _CliFailure(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source metadata escaped its project directory",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    if not stat.S_ISREG(value.st_mode) or _is_reparse_point(sidecar):
        raise _CliFailure(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source metadata is not an ordinary file",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return sidecar


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _sidecar_value(index: Any, source_path: Path) -> dict[str, Any]:
    sidecar = _validated_sidecar(index, source_path)
    if sidecar is None:
        return {}
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CliFailure(
            "SOURCE_METADATA_INVALID",
            "The managed source metadata is invalid",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    return dict(value) if isinstance(value, Mapping) else {}


def _source_record(index: Any, source_id: str) -> dict[str, Any]:
    source_path, _resolved, relative = _managed_file(index, source_id)
    sidecar = _validated_sidecar(index, source_path)
    metadata = _sidecar_value(index, source_path)
    digest = _hash_file(source_path)
    source_stat = source_path.stat()
    stat_size = int(source_stat.st_size)
    stat_mtime_ns = int(source_stat.st_mtime_ns)
    if sidecar is not None:
        sidecar_stat = sidecar.stat()
        stat_size += int(sidecar_stat.st_size)
        stat_mtime_ns = max(stat_mtime_ns, int(sidecar_stat.st_mtime_ns))
    source_kind = _managed_source_kind(source_path, metadata)
    source_key = f"user:{source_kind}:{relative.as_posix()}"
    with closing(
        index._connect(read_only=True)  # noqa: SLF001
    ) as connection:
        row = connection.execute(
            "SELECT id, stat_size, stat_mtime_ns FROM documents "
            "WHERE collection = 'user' AND source_group = 'user' "
            "AND source = ? AND source_key = ?",
            (source_kind, source_key),
        ).fetchone()
        document_id = int(row[0]) if row is not None else None
        chunk_count = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            if document_id is not None
            else 0
        )
        vector_count = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunk_vectors "
                    "WHERE chunk_id IN "
                    "(SELECT id FROM chunks WHERE document_id = ?)",
                    (document_id,),
                ).fetchone()[0]
            )
            if document_id is not None
            else 0
        )
    current = bool(
        row is not None
        and int(row[1]) == stat_size
        and int(row[2]) == stat_mtime_ns
    )
    return {
        "source_id": relative.as_posix(),
        "source_kind": source_kind,
        "name": str(metadata.get("original_name") or source_path.name),
        "managed_path": source_path.relative_to(index.project_root).as_posix(),
        "original_path": str(metadata.get("original_path") or ""),
        "sha256": digest,
        "source_sha256": str(metadata.get("source_sha256") or ""),
        "size_bytes": source_path.stat().st_size,
        "imported_at": str(metadata.get("imported_at") or ""),
        "indexed": row is not None,
        "current": current,
        "stale": bool(row is not None and not current),
        "document_id": document_id,
        "chunk_count": chunk_count,
        "vector_count": vector_count,
    }


def _iter_source_ids(index: Any) -> list[str]:
    raw_root, _resolved_root = _source_root(index)
    output: list[str] = []
    for directory, directory_names, file_names in os.walk(
        raw_root,
        topdown=True,
        followlinks=False,
    ):
        base = Path(directory)
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if not _is_reparse_point(base / name)
        ]
        for name in sorted(file_names):
            path = base / name
            if (
                path.suffix.casefold() in SUPPORTED_SOURCE_SUFFIXES
                and not _is_reparse_point(path)
            ):
                output.append(path.relative_to(raw_root).as_posix())
    return output


def _sources_list(index: Any, arguments: argparse.Namespace) -> dict[str, Any]:
    offset, limit = _validated_pagination(arguments)
    source_ids = _iter_source_ids(index)
    return {
        "items": [
            _source_record(index, value)
            for value in source_ids[offset : offset + limit]
        ],
        "total": len(source_ids),
        "offset": offset,
        "limit": limit,
    }


def _safe_import_name(source: Path) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", source.stem)
    stem = stem.strip(" ._")[:80] or "source"
    return f"{stem}-{uuid.uuid4().hex}{source.suffix.casefold()}"


def _managed_source_kind(
    source_path: Path,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    declared = str((metadata or {}).get("source_kind") or "")
    if declared in {"user_document", "user_transcript"}:
        return declared
    return (
        "user_transcript"
        if source_path.suffix.casefold() in CAPTION_SUFFIXES
        else "user_document"
    )


def _media_import_source(source: Path) -> tuple[Path, dict[str, Any]]:
    if source.suffix.casefold() not in MEDIA_SUFFIXES:
        return source, {}
    batch = normalize_selected_file(source)
    if batch.status != "ready":
        code = (
            "TRANSCRIPT_REQUIRED"
            if batch.status == "transcript_required"
            else "MEDIA_SOURCE_UNAVAILABLE"
        )
        raise _CliFailure(
            code,
            (
                batch.warnings[0]
                if batch.warnings
                else "The selected media has no deterministic transcript"
            ),
            exit_code=(
                EXIT_NOT_FOUND
                if code == "TRANSCRIPT_REQUIRED"
                else EXIT_INVALID_ARGUMENTS
            ),
        )
    transcript = find_media_sidecar(source)
    if transcript is None:
        raise _CliFailure(
            "TRANSCRIPT_REQUIRED",
            "The selected media has no deterministic transcript sidecar",
            exit_code=EXIT_NOT_FOUND,
        )
    return transcript, {
        "source_kind": "user_transcript",
        "selected_media_path": str(source),
        "selected_media_name": source.name,
        "selected_media_size_bytes": int(source.stat().st_size),
        "transcript_original_path": str(transcript),
        "transcript_original_name": transcript.name,
        "transcript_format": transcript.suffix.casefold().lstrip("."),
        "media_copied": False,
        "transcript_managed": True,
    }


def _sources_import(store: Any, raw_path: str) -> dict[str, Any]:
    index = store.index
    candidate = Path(raw_path).expanduser()
    try:
        source = candidate.resolve(strict=True)
        value = os.lstat(candidate)
    except OSError as exc:
        raise _CliFailure(
            "SOURCE_NOT_FOUND",
            "The selected source file was not found",
            exit_code=EXIT_NOT_FOUND,
        ) from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or _is_reparse_point(candidate)
        or source.suffix.casefold() not in IMPORT_SOURCE_SUFFIXES
    ):
        raise _CliFailure(
            "UNSUPPORTED_SOURCE_TYPE",
            "The selected source must be an ordinary supported file",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    managed_source, media_metadata = _media_import_source(source)
    if managed_source.stat().st_size > MAX_DOCUMENT_BYTES:
        raise _CliFailure(
            "SOURCE_TOO_LARGE",
            f"Sources are limited to {MAX_DOCUMENT_BYTES} bytes",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    raw_root, resolved_root = _source_root(index)
    try:
        source.relative_to(resolved_root)
    except ValueError:
        pass
    else:
        raise _CliFailure(
            "SOURCE_ALREADY_MANAGED",
            "The selected file is already inside managed sources",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )

    target = raw_root / _safe_import_name(managed_source)
    temporary = raw_root / f".import-{uuid.uuid4().hex}.tmp"
    sidecar = _sidecar_path(target)
    sidecar_temporary = raw_root / f".import-{uuid.uuid4().hex}.metadata.tmp"
    created: list[Path] = []
    try:
        shutil.copyfile(managed_source, temporary)
        digest = _hash_file(temporary)
        imported_at = _utc_now()
        metadata = {
            "schema": SOURCE_SIDECAR_SCHEMA,
            "source_id": target.name,
            "original_path": str(source),
            "original_name": source.name,
            "source_sha256": digest,
            "imported_at": imported_at,
            "accessed_at": imported_at,
            "evidence": (
                "User-selected source copied into project-managed local knowledge"
            ),
            **media_metadata,
        }
        sidecar_temporary.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        if os.path.lexists(target) or os.path.lexists(sidecar):
            raise _CliFailure(
                "SOURCE_IMPORT_COLLISION",
                "The generated managed source path already exists",
            )
        os.rename(temporary, target)
        created.append(target)
        os.rename(sidecar_temporary, sidecar)
        created.append(sidecar)
    except BaseException:
        for path in (*created, temporary, sidecar_temporary):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    version = index.active_houdini_version() or "unknown"
    try:
        refresh, warnings = index.refresh(
            {"user"},
            {"houdini_version": version},
            force=True,
        )
        refresh_result = dict(refresh)
        refresh_result.setdefault("groups", ["user"])
    except Exception as exc:
        refresh_result = {
            "refreshed": False,
            "groups": ["user"],
            "refresh_reason": "refresh_failed",
        }
        warnings = [
            "The source copy succeeded but indexing did not: "
            f"{_safe_message(exc)}. Run sources refresh to retry."
        ]
    source_record = _source_record(index, target.name)
    document_id = source_record.get("document_id")
    vector = (
        store.vectorize_documents((int(document_id),))
        if document_id is not None
        else store.status()
    )
    source_record = _source_record(index, target.name)
    return {
        "source": source_record,
        "copied": True,
        "indexed": bool(source_record["current"]),
        "media_copied": bool(media_metadata.get("media_copied", False)),
        "transcript_managed": bool(
            media_metadata.get("transcript_managed", False)
        ),
        "refresh": refresh_result,
        "vector": vector,
        "warnings": list(warnings),
    }


def _sources_delete(index: Any, source_id: str) -> dict[str, Any]:
    source_path, _resolved, relative = _managed_file(index, source_id)
    sidecar = _validated_sidecar(index, source_path)
    source_kind = _managed_source_kind(
        source_path,
        _sidecar_value(index, source_path),
    )
    source_key = f"user:{source_kind}:{relative.as_posix()}"
    suffix = uuid.uuid4().hex
    source_tombstone = source_path.with_name(f".delete-{suffix}.tmp")
    sidecar_tombstone = (
        sidecar.with_name(f".delete-{suffix}.metadata.tmp")
        if sidecar is not None
        else None
    )
    moved: list[tuple[Path, Path]] = []
    with closing(index._connect()) as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT id FROM documents "
                "WHERE collection = 'user' AND source_group = 'user' "
                "AND source = ? AND source_key = ?",
                (source_kind, source_key),
            ).fetchone()
            document_id = int(row[0]) if row is not None else None
            chunk_count = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
                        (document_id,),
                    ).fetchone()[0]
                )
                if document_id is not None
                else 0
            )
            vector_count = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chunk_vectors "
                        "WHERE chunk_id IN "
                        "(SELECT id FROM chunks WHERE document_id = ?)",
                        (document_id,),
                    ).fetchone()[0]
                )
                if document_id is not None
                else 0
            )
            os.rename(source_path, source_tombstone)
            moved.append((source_tombstone, source_path))
            if sidecar is not None:
                assert sidecar_tombstone is not None
                os.rename(sidecar, sidecar_tombstone)
                moved.append((sidecar_tombstone, sidecar))
            if document_id is not None:
                index._delete_document(connection, document_id)  # noqa: SLF001
            connection.commit()
        except BaseException as exc:
            connection.rollback()
            recovery_errors: list[str] = []
            for tombstone, original in reversed(moved):
                try:
                    if os.path.lexists(tombstone) and not os.path.lexists(original):
                        os.rename(tombstone, original)
                except OSError as recovery_error:
                    recovery_errors.append(_safe_message(recovery_error))
            if recovery_errors:
                raise _CliFailure(
                    "SOURCE_DELETE_RECOVERY_FAILED",
                    "The database delete failed and a managed source could not "
                    "be restored: " + "; ".join(recovery_errors),
                ) from exc
            raise
    cleanup_warnings: list[str] = []
    for tombstone, _original in moved:
        try:
            tombstone.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_warnings.append(_safe_message(exc))
    return {
        "source_id": relative.as_posix(),
        "deleted": True,
        "document_deleted": document_id is not None,
        "chunks_deleted": chunk_count,
        "vectors_deleted": vector_count,
        "warnings": cleanup_warnings,
    }


def _sources_refresh(store: Any) -> dict[str, Any]:
    index = store.index
    version = index.active_houdini_version() or "unknown"
    refresh, warnings = index.refresh(
        {"user"},
        {"houdini_version": version},
        force=True,
    )
    refresh_result = dict(refresh)
    refresh_result.setdefault("groups", ["user"])
    document_ids = index.filtered_document_ids(
        source_kinds={"user_document", "user_transcript"},
    ) or ()
    vector = (
        store.vectorize_documents(document_ids)
        if document_ids
        else store.status()
    )
    return {
        "refresh": refresh_result,
        "vector": vector,
        "warnings": list(warnings),
    }


def _validated_thread_id(value: Any) -> str:
    thread_id = str(value or "").strip()
    if (
        not thread_id
        or len(thread_id) > 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", thread_id)
        is None
    ):
        raise _CliFailure(
            "INVALID_THREAD_ID",
            "thread-id must be a stable identifier of at most 256 characters",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return thread_id


def _thread_snapshot_path(
    project_root: Path,
    raw_path: str,
) -> tuple[Path, str]:
    text = str(raw_path or "").strip().replace("\\", "/")
    relative = Path(text)
    if (
        not text
        or relative.is_absolute()
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or relative.suffix.casefold() != ".json"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _CliFailure(
            "UNSAFE_THREAD_SNAPSHOT",
            "snapshot-file must be a project-relative JSON file",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    candidate = project_root / relative
    current = project_root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _is_reparse_point(current):
            raise _CliFailure(
                "UNSAFE_THREAD_SNAPSHOT",
                "The Thread snapshot path contains a reparse point",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(project_root.resolve(strict=True))
        value = os.lstat(candidate)
    except (OSError, ValueError) as exc:
        raise _CliFailure(
            "THREAD_SNAPSHOT_NOT_FOUND",
            "The project-local Thread snapshot was not found",
            exit_code=EXIT_NOT_FOUND,
        ) from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or _is_reparse_point(candidate)
        or not 0 < resolved.stat().st_size <= MAX_THREAD_SNAPSHOT_BYTES
    ):
        raise _CliFailure(
            "INVALID_THREAD_SNAPSHOT",
            "The Thread snapshot must be a bounded ordinary JSON file",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return resolved, relative.as_posix()


def _thread_import(
    store: HybridKnowledgeStore,
    project_root: Path,
    arguments: argparse.Namespace,
) -> dict[str, Any]:
    thread_id = _validated_thread_id(arguments.thread_id)
    snapshot_path, relative_path = _thread_snapshot_path(
        project_root,
        str(arguments.snapshot_file),
    )
    try:
        snapshot = json.loads(
            snapshot_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CliFailure(
            "INVALID_THREAD_SNAPSHOT",
            "The Thread snapshot is not valid UTF-8 JSON",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    if (
        not isinstance(snapshot, Mapping)
        or set(snapshot) != {"schema", "thread_id", "messages"}
        or str(snapshot.get("schema") or "") != THREAD_SNAPSHOT_SCHEMA
        or str(snapshot.get("thread_id") or "") != thread_id
        or not isinstance(snapshot.get("messages"), list)
        or not all(
            isinstance(message, Mapping)
            for message in snapshot.get("messages", ())
        )
    ):
        raise _CliFailure(
            "INVALID_THREAD_SNAPSHOT",
            "The Thread snapshot contract is invalid or does not match thread-id",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    messages = list(snapshot["messages"])
    try:
        batch = normalize_thread_export(
            thread_id,
            messages,
            explicitly_selected=True,
        )
    except SourceAdapterError as exc:
        raise _CliFailure(
            "INVALID_THREAD_SNAPSHOT",
            str(exc),
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    indexed = store.refresh_explicit_sources(
        batch.records,
        replace_thread_id=thread_id,
    )
    return {
        "status": "replaced",
        "thread_id": thread_id,
        "source_kind": "thread_export",
        "snapshot_schema": THREAD_SNAPSHOT_SCHEMA,
        "snapshot_file": relative_path,
        "messages_received": len(messages),
        "records_indexed": len(batch.records),
        "messages_excluded": len(messages) - len(batch.records),
        "refresh": dict(indexed["refresh"]),
        "retrieval": dict(indexed["vector"]),
        "warnings": list(batch.warnings),
    }


def _thread_remove(
    store: HybridKnowledgeStore,
    thread_id_value: Any,
) -> dict[str, Any]:
    thread_id = _validated_thread_id(thread_id_value)
    indexed = store.refresh_explicit_sources(
        (),
        replace_thread_id=thread_id,
    )
    return {
        "status": "removed",
        "thread_id": thread_id,
        "source_kind": "thread_export",
        "refresh": dict(indexed["refresh"]),
        "retrieval": dict(indexed["vector"]),
    }


def _memory_arguments(arguments: argparse.Namespace) -> dict[str, Any]:
    action = str(arguments.memory_command)
    output: dict[str, Any] = {"action": action}
    if action in {"record", "supersede"}:
        output.update(
            {
                "memory_type": arguments.memory_type,
                "title": arguments.title,
                "body": arguments.body,
            }
        )
        if arguments.tag:
            output["tags"] = list(arguments.tag)
        for argument_name, field_name in (
            ("scope", "scope"),
            ("source_thread_id", "source_thread_id"),
            ("source_turn_id", "source_turn_id"),
        ):
            value = str(getattr(arguments, argument_name) or "")
            if value:
                output[field_name] = value
        if action == "supersede":
            output["memory_id"] = arguments.memory_id
    elif action == "list":
        if arguments.memory_type:
            output["memory_type"] = arguments.memory_type
        if arguments.tag:
            output["tags"] = list(arguments.tag)
        output["scope"] = arguments.scope
        output["include_superseded"] = bool(arguments.include_superseded)
        output["offset"], output["limit"] = _validated_pagination(
            arguments,
            maximum=200,
        )
    else:
        output["memory_id"] = arguments.memory_id
    return output


def _error_code(error: BaseException, action: str) -> str:
    value = getattr(error, "code", "")
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
        return value
    prefix = re.sub(r"[^A-Za-z0-9]+", "_", action).strip("_").upper()
    return f"{prefix or 'KNOWLEDGE_OPERATION'}_FAILED"


def _safe_message(error: BaseException) -> str:
    message = str(getattr(error, "message", "") or error).strip()
    message = re.sub(
        r"(?i)\b(bearer\s+)[^\s,;]+",
        r"\1<redacted>",
        message,
    )
    message = re.sub(
        r"(?i)\b(token|secret|password)\s*=\s*[^\s,;]+",
        r"\1=<redacted>",
        message,
    )
    return " ".join(message.split())[:2048] or "Vector index build failed"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
) -> int:
    emitter = _Emitter(stdout or sys.stdout, _output_format(argv))
    action = "unknown"
    store: HybridKnowledgeStore | None = None
    last_status: Mapping[str, Any] = {}
    try:
        arguments = _parser().parse_args(argv)
        action = _action(arguments)
        root = _project_root(str(arguments.project_root or ""))
        batch_size = (
            _batch_size(int(arguments.batch_size))
            if action == "build"
            else 0
        )
        try:
            store = _open_store(
                root,
                read_only=action in {"status", "sources.list"},
            )
        except KnowledgeIndexError:
            database_path = Path(runtime_layout(root)["knowledge_database"])
            if action != "status" or database_path.is_file():
                raise
            status_details = _status_details(None, root)
            emitter.emit(
                "start",
                action,
                project_root=str(root),
                batch_size=0,
                source_refresh={},
                source_warnings=[],
                **status_details,
            )
            emitter.emit("completed", action, **status_details)
            return 0

        if action.startswith("sources."):
            emitter.emit("start", action, project_root=str(root))
            if action == "sources.list":
                result = _sources_list(store.index, arguments)
            elif action == "sources.import":
                result = _sources_import(store, str(arguments.path))
            elif action == "sources.delete":
                result = _sources_delete(
                    store.index,
                    str(arguments.source_id),
                )
            else:
                result = _sources_refresh(store)
            emitter.emit("completed", action, result=result)
            return 0

        if action.startswith("thread."):
            emitter.emit("start", action, project_root=str(root))
            result = (
                _thread_import(store, root, arguments)
                if action == "thread.import"
                else _thread_remove(store, arguments.thread_id)
            )
            emitter.emit("completed", action, result=result)
            return 0

        if action.startswith("memory."):
            emitter.emit("start", action, project_root=str(root))
            try:
                result = store.project_memory(_memory_arguments(arguments))
            except HybridKnowledgeError as exc:
                message = _safe_message(exc)
                if "not found" in message.casefold():
                    raise _CliFailure(
                        "MEMORY_NOT_FOUND",
                        message,
                        exit_code=EXIT_NOT_FOUND,
                    ) from exc
                raise _CliFailure(
                    "MEMORY_OPERATION_REJECTED",
                    message,
                    exit_code=EXIT_INVALID_ARGUMENTS,
                ) from exc
            emitter.emit("completed", action, result=result)
            return 0

        source_refresh: Mapping[str, Any] = {}
        source_warnings: list[str] = []
        if action in {"bootstrap", "build"}:
            active_version = store.index.active_houdini_version() or "unknown"
            source_refresh, source_warnings = store.index.refresh(
                {"project"},
                {"houdini_version": active_version},
                force=True,
            )
        status_details = _status_details(store, root)
        last_status = status_details["index"]
        emitter.emit(
            "start",
            action,
            project_root=str(root),
            batch_size=batch_size,
            source_refresh=dict(source_refresh),
            source_warnings=source_warnings,
            **status_details,
        )
        if action == "bootstrap":
            pack_status = source_refresh.get("builtin_pack")
            if not (
                isinstance(pack_status, Mapping)
                and pack_status.get("installed")
            ):
                raise _CliFailure(
                    "BUILTIN_PACK_UNAVAILABLE",
                    "The bundled offline knowledge pack is missing or invalid",
                    recoverable=True,
                )
            emitter.emit(
                "completed",
                action,
                source_refresh=dict(source_refresh),
                source_warnings=source_warnings,
                **status_details,
            )
            return 0
        if action == "status":
            emitter.emit("completed", action, **status_details)
            return 0

        batch_number = 0
        while not bool(last_status.get("complete", False)):
            next_status = store.build_batch(batch_size)
            batch_number += 1
            indexed = int(next_status.get("chunks_indexed_this_call", 0))
            last_status = next_status
            emitter.emit(
                "progress",
                action,
                batch_number=batch_number,
                indexed_this_batch=indexed,
                index=dict(last_status),
            )
            if not bool(last_status.get("complete", False)) and indexed <= 0:
                raise _CliFailure(
                    "VECTOR_INDEX_NO_PROGRESS",
                    "The vector index build made no progress",
                    recoverable=True,
                )
        emitter.emit(
            "completed",
            action,
            batches=batch_number,
            index=dict(last_status),
            embedding=status_details["embedding"],
            runtime=status_details["runtime"],
            installation=status_details["installation"],
        )
        return 0
    except KeyboardInterrupt:
        if store is not None and action in {"status", "build"}:
            try:
                last_status = store.status()
            except Exception:
                pass
        emitter.emit(
            "error",
            action,
            code="INTERRUPTED",
            message="The knowledge operation was interrupted",
            recoverable=True,
            index=dict(last_status),
        )
        return EXIT_INTERRUPTED
    except _CliFailure as exc:
        emitter.emit(
            "error",
            action,
            code=exc.code,
            message=exc.message,
            recoverable=exc.recoverable,
            index=dict(last_status),
        )
        return exc.exit_code
    except Exception as exc:
        emitter.emit(
            "error",
            action,
            code=_error_code(exc, action),
            message=_safe_message(exc),
            recoverable=True,
            index=dict(last_status),
        )
        return EXIT_RUNTIME_ERROR
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
