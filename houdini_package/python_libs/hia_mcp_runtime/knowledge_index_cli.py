"""One-shot, resumable JSONL builder for the local vector index."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO


_SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_CORE = _SOURCE_PROJECT_ROOT / "src"
if str(_SOURCE_CORE) not in sys.path:
    sys.path.insert(0, str(_SOURCE_CORE))

from hia_core.embedding_contract import (  # noqa: E402
    KNOWLEDGE_INDEX_CLI_PROTOCOL,
    KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE,
    KNOWLEDGE_INDEX_MAX_BATCH_SIZE,
)

from .hybrid_knowledge import HybridKnowledgeStore  # noqa: E402


PROJECT_ROOT_ENVIRONMENT = "HIA_PROJECT_ROOT"


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
    def __init__(self, output: TextIO) -> None:
        self._output = output
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
    parser = _Parser(prog="python -m hia_mcp_runtime.knowledge_index_cli")
    parser.add_argument(
        "--project-root",
        default="",
        help=("Project root; defaults to HIA_PROJECT_ROOT or the package checkout"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Report vector-index status")
    build = commands.add_parser("build", help="Build all pending vector batches")
    build.add_argument(
        "--batch-size",
        type=int,
        default=KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE,
        help=(f"Committed chunks per batch (1-{KNOWLEDGE_INDEX_MAX_BATCH_SIZE})"),
    )
    return parser


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
            exit_code=2,
        )
    return value


def _error_code(error: BaseException) -> str:
    value = getattr(error, "code", "")
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
        return value
    return "VECTOR_INDEX_BUILD_FAILED"


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
    emitter = _Emitter(stdout or sys.stdout)
    action = "unknown"
    store: HybridKnowledgeStore | None = None
    last_status: Mapping[str, Any] = {}
    try:
        arguments = _parser().parse_args(argv)
        action = str(arguments.command)
        root = _project_root(str(arguments.project_root or ""))
        batch_size = _batch_size(int(arguments.batch_size)) if action == "build" else 0
        store = HybridKnowledgeStore(root)
        source_refresh: Mapping[str, Any] = {}
        source_warnings: list[str] = []
        if action == "build":
            active_version = store.index.active_houdini_version() or "unknown"
            source_refresh, source_warnings = store.index.refresh(
                {"project"},
                {"houdini_version": active_version},
                force=True,
            )
        last_status = store.status()
        emitter.emit(
            "start",
            action,
            project_root=str(root),
            batch_size=batch_size,
            source_refresh=dict(source_refresh),
            source_warnings=source_warnings,
            index=dict(last_status),
        )
        if action == "status":
            emitter.emit("completed", action, index=dict(last_status))
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
        )
        return 0
    except KeyboardInterrupt:
        if store is not None:
            try:
                last_status = store.status()
            except Exception:
                pass
        emitter.emit(
            "error",
            action,
            code="INTERRUPTED",
            message="Vector index build was interrupted",
            recoverable=True,
            index=dict(last_status),
        )
        return 130
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
            code=_error_code(exc),
            message=_safe_message(exc),
            recoverable=True,
            index=dict(last_status),
        )
        return 1
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
