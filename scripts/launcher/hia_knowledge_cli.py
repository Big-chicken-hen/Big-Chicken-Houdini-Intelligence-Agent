"""Project-local launcher compatibility CLI for managed knowledge sources.

This helper deliberately does not implement source storage, retrieval, or
vector indexing.  Source list/import/delete/refresh operations delegate to the
stable ``hia_mcp_runtime.knowledge_index_cli`` contract.  The only source-side
compatibility logic retained here enumerates supported ordinary files for the
launcher folder-import convenience action.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import site
import stat
import struct
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence


CLI_SCHEMA = "hia-knowledge-launcher-cli/1"
MODEL_MANIFEST_NAME = ".hia-embedding-model.json"
VENV_MARKER_NAME = ".hia-managed-venv.json"
VENV_MARKER_SCHEMA = "hia-managed-python-venv/1"
MAX_SIDECAR_BYTES = 64 * 1024
DEFAULT_LIST_LIMIT = 500
MAX_LIST_LIMIT = 500
EXIT_RUNTIME_ERROR = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_NOT_FOUND = 3
_SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODULE_CACHE: dict[tuple[str, str], ModuleType] = {}


class CliError(RuntimeError):
    """A bounded, machine-readable user-facing failure."""

    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(
            "INVALID_ARGUMENTS",
            message,
            exit_code=EXIT_INVALID_ARGUMENTS,
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="hia-knowledge")
    parser.add_argument(
        "--project-root",
        default="",
        help="Exact HIA project root; defaults to the helper's repository.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Report environment, models, index, and sources")
    commands.add_parser(
        "environment-status",
        help="Report the project-local knowledge/embedding environment",
    )
    import_file = commands.add_parser(
        "import-file",
        help="Copy one or more supported files into managed sources",
    )
    import_file.add_argument("--path", action="append", required=True)
    import_folder = commands.add_parser(
        "import-folder",
        help="Copy supported ordinary files from one or more folders",
    )
    import_folder.add_argument("--path", action="append", required=True)
    source_list = commands.add_parser("list", help="List managed source copies")
    source_list.add_argument("--offset", type=int, default=0)
    source_list.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT)
    source_delete = commands.add_parser(
        "delete",
        help="Delete one exact managed copy and its sidecar",
    )
    source_delete.add_argument("--source-id", required=True)
    commands.add_parser("rescan", help="Refresh only the managed user corpus")
    thread_import = commands.add_parser(
        "thread-import",
        help="Replace one Thread from a project-local public snapshot",
    )
    thread_import.add_argument("--thread-id", required=True)
    thread_import.add_argument("--snapshot-file", required=True)
    thread_remove = commands.add_parser(
        "thread-remove",
        help="Remove all indexed messages for one Thread",
    )
    thread_remove.add_argument("--thread-id", required=True)
    return parser


def _path_exists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_reparse(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(value, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & reparse_flag)


def _is_ordinary_file(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(value.st_mode) and not _is_reparse(path)


def _is_ordinary_directory(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(value.st_mode) and not _is_reparse(path)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _is_within(path: Path, root: Path, *, allow_root: bool = False) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return allow_root or bool(relative.parts)


def _assert_no_reparse_ancestors(path: Path) -> None:
    """Reject reparse points from the filesystem anchor through ``path``."""

    absolute = Path(os.path.abspath(path))
    chain = [absolute, *absolute.parents]
    for candidate in reversed(chain):
        if not _path_exists(candidate):
            continue
        if _is_reparse(candidate):
            raise CliError(
                "UNSAFE_REPARSE_PATH",
                "The selected path contains a reparse point",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )


def _project_root(raw: str) -> Path:
    selected = Path(raw) if raw.strip() else _SOURCE_PROJECT_ROOT
    absolute = Path(os.path.abspath(selected))
    if not _is_ordinary_directory(absolute):
        raise CliError(
            "PROJECT_ROOT_INVALID",
            "The selected HIA project root is not an ordinary directory",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    _assert_no_reparse_ancestors(absolute)
    try:
        root = absolute.resolve(strict=True)
    except OSError as exc:
        raise CliError(
            "PROJECT_ROOT_INVALID",
            "The selected HIA project root is unavailable",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    if not _same_path(root, absolute):
        raise CliError(
            "PROJECT_ROOT_INVALID",
            "The selected HIA project root is not an exact resolved path",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    required = (
        root / "src" / "hia_core" / "embedding_contract.py",
        root
        / "houdini_package"
        / "python_libs"
        / "hia_mcp_runtime"
        / "knowledge_index.py",
    )
    if not all(_is_ordinary_file(path) for path in required):
        raise CliError(
            "PROJECT_ROOT_INVALID",
            "The selected HIA project root is missing required runtime files",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return root


def _managed_venv_root(project_root: Path) -> Path:
    contract = _load_project_module(project_root, "contract")
    layout = contract.runtime_layout(project_root)
    return Path(layout["venv_root"]).resolve(strict=False)


def _current_interpreter_is_project_managed(project_root: Path) -> bool:
    executable_path = Path(sys.executable)
    if not _is_ordinary_file(executable_path):
        return False
    try:
        executable = executable_path.resolve(strict=True)
    except OSError:
        return False
    return _is_within(executable, _managed_venv_root(project_root))


def _strip_unmanaged_site_paths(project_root: Path) -> None:
    """Keep third-party imports confined to the exact project-local venv."""

    os.environ["PYTHONNOUSERSITE"] = "1"
    site.ENABLE_USER_SITE = False
    managed_venv = _managed_venv_root(project_root)
    allow_managed_site = _current_interpreter_is_project_managed(project_root)
    retained: list[str] = []
    for value in sys.path:
        if not value:
            retained.append(value)
            continue
        folded = value.replace("\\", "/").casefold()
        if "site-packages" not in folded and "dist-packages" not in folded:
            retained.append(value)
            continue
        try:
            candidate = Path(value).resolve(strict=False)
        except OSError:
            continue
        if allow_managed_site and _is_within(candidate, managed_venv):
            retained.append(value)
    sys.path[:] = retained


def _load_project_module(project_root: Path, kind: str) -> ModuleType:
    cache_key = (os.path.normcase(str(project_root)), kind)
    cached = _MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if kind == "knowledge":
        path = (
            project_root
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "knowledge_index.py"
        )
    elif kind == "contract":
        path = project_root / "src" / "hia_core" / "embedding_contract.py"
    else:
        raise AssertionError(f"unknown project module: {kind}")
    module_name = (
        f"_hia_launcher_{kind}_"
        f"{hashlib.sha256(str(project_root).encode('utf-8')).hexdigest()[:12]}"
    )
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise CliError(
            "PROJECT_RUNTIME_UNAVAILABLE",
            f"The project {kind} runtime could not be loaded",
        )
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise CliError(
            "PROJECT_RUNTIME_UNAVAILABLE",
            f"The project {kind} runtime could not be loaded",
        ) from exc
    _MODULE_CACHE[cache_key] = module
    return module


def _supported_contract(
    project_root: Path,
) -> tuple[
    ModuleType,
    frozenset[str],
    frozenset[str],
    int,
]:
    module = _load_project_module(project_root, "knowledge")
    try:
        text_suffixes = frozenset(
            str(value).casefold() for value in module.USER_TEXT_SUFFIXES
        )
        optional_suffixes = frozenset(
            str(value).casefold() for value in module.USER_OPTIONAL_SUFFIXES
        )
        media_suffixes = frozenset(
            str(value).casefold() for value in module.MEDIA_SUFFIXES
        )
        maximum = int(module.MAX_DOCUMENT_BYTES)
        index_type = module.LocalKnowledgeIndex
    except (AttributeError, TypeError, ValueError) as exc:
        raise CliError(
            "KNOWLEDGE_CONTRACT_INVALID",
            "The project knowledge source contract is incomplete",
        ) from exc
    folder_supported = text_suffixes | optional_suffixes
    supported = folder_supported | media_suffixes
    expected = frozenset({".md", ".txt", ".html", ".htm", ".srt", ".vtt", ".pdf"})
    if (
        folder_supported != expected
        or not media_suffixes
        or any(
            not value.startswith(".") or value in folder_supported
            for value in media_suffixes
        )
        or maximum <= 0
        or not callable(index_type)
    ):
        raise CliError(
            "KNOWLEDGE_CONTRACT_INVALID",
            "The project knowledge source contract is unsupported",
        )
    return module, supported, folder_supported, maximum


def _assert_managed_chain(project_root: Path, target: Path) -> None:
    root = project_root.resolve(strict=True)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise CliError(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source path escaped the project root",
            exit_code=EXIT_INVALID_ARGUMENTS,
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        if not _path_exists(current):
            continue
        if _is_reparse(current):
            raise CliError(
                "UNSAFE_MANAGED_SOURCE_PATH",
                "The managed source path contains a reparse point",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
        if current != target and not _is_ordinary_directory(current):
            raise CliError(
                "UNSAFE_MANAGED_SOURCE_PATH",
                "A managed source ancestor is not an ordinary directory",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
    resolved = target.resolve(strict=False)
    if not _is_within(resolved, root):
        raise CliError(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source path escaped the project root",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )


def _open_index(
    project_root: Path,
    knowledge_module: ModuleType,
    *,
    initialize: bool = True,
) -> Any:
    expected = project_root / ".runtime" / "knowledge" / "sources"
    _assert_managed_chain(project_root, expected)
    try:
        index = knowledge_module.LocalKnowledgeIndex(
            project_root,
            initialize=initialize,
        )
    except Exception as exc:
        raise CliError(
            "KNOWLEDGE_INDEX_UNAVAILABLE",
            "The project-local knowledge index could not be initialized",
        ) from exc
    _assert_managed_chain(project_root, expected)
    if not _is_ordinary_directory(expected):
        raise CliError(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The managed source root is not an ordinary directory",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    if not _same_path(Path(index.sources_root).resolve(strict=True), expected):
        raise CliError(
            "UNSAFE_MANAGED_SOURCE_PATH",
            "The knowledge runtime selected a different managed source root",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    return index


def _core_source_cli_path(project_root: Path) -> Path:
    path = (
        project_root
        / "houdini_package"
        / "python_libs"
        / "hia_mcp_runtime"
        / "knowledge_index_cli.py"
    )
    _assert_managed_chain(project_root, path)
    if not _is_ordinary_file(path):
        raise CliError(
            "KNOWLEDGE_SOURCE_CLI_UNAVAILABLE",
            "The stable project knowledge source CLI is unavailable",
        )
    return path


def _core_knowledge_action(
    project_root: Path,
    command_parts: Sequence[str],
    arguments: Sequence[str] = (),
) -> dict[str, Any]:
    """Invoke the stable MCP-owned knowledge CLI and return its result."""

    contract = _load_project_module(project_root, "contract")
    layout = dict(contract.runtime_layout(project_root))
    python_options = ["-I"]
    if not _current_interpreter_is_project_managed(project_root):
        python_options.append("-S")
    python_options.append("-B")
    command = [
        sys.executable,
        *python_options,
        str(_core_source_cli_path(project_root)),
        "--project-root",
        str(project_root),
        "--format",
        "json",
        *command_parts,
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            env=_safe_child_environment(project_root, layout),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CliError(
            "KNOWLEDGE_SOURCE_CLI_FAILED",
            "The stable project knowledge source CLI could not be started",
        ) from exc
    try:
        payload = json.loads(completed.stdout.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise CliError(
            "KNOWLEDGE_SOURCE_CLI_FAILED",
            "The stable project knowledge source CLI returned invalid JSON",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CliError(
            "KNOWLEDGE_SOURCE_CLI_FAILED",
            "The stable project knowledge source CLI returned invalid JSON",
        )
    if completed.returncode != 0 or str(payload.get("event") or "") == "error":
        raise CliError(
            str(payload.get("code") or "KNOWLEDGE_SOURCE_CLI_FAILED"),
            str(
                payload.get("message")
                or "The stable project knowledge source operation failed"
            ),
            exit_code=(
                completed.returncode
                if completed.returncode in {
                    EXIT_RUNTIME_ERROR,
                    EXIT_INVALID_ARGUMENTS,
                    EXIT_NOT_FOUND,
                }
                else EXIT_RUNTIME_ERROR
            ),
        )
    if (
        str(payload.get("event") or "") != "completed"
        or not isinstance(payload.get("result"), Mapping)
    ):
        raise CliError(
            "KNOWLEDGE_SOURCE_CLI_FAILED",
            "The stable project knowledge source CLI returned an incomplete result",
        )
    return dict(payload["result"])


def _core_source_action(
    project_root: Path,
    source_command: str,
    arguments: Sequence[str] = (),
) -> dict[str, Any]:
    return _core_knowledge_action(
        project_root,
        ("sources", source_command),
        arguments,
    )


def _core_thread_action(
    project_root: Path,
    thread_command: str,
    arguments: Sequence[str],
) -> dict[str, Any]:
    return _core_knowledge_action(
        project_root,
        ("thread", thread_command),
        arguments,
    )


def _compat_source_record(value: Mapping[str, Any]) -> dict[str, Any]:
    source_id = str(value.get("source_id") or "")
    managed_path = str(value.get("managed_path") or source_id)
    current = bool(value.get("current"))
    return {
        "source_id": source_id,
        "source_kind": str(value.get("source_kind") or "user_document"),
        "name": str(value.get("name") or Path(source_id).name),
        "source": str(value.get("original_path") or ""),
        "format": Path(managed_path).suffix.casefold().lstrip("."),
        "size_bytes": int(value.get("size_bytes") or 0),
        "managed_path": managed_path,
        "imported_at": str(value.get("imported_at") or ""),
        "sha256": str(value.get("sha256") or ""),
        "index_status": "indexed" if current else "not_indexed",
        "document_id": value.get("document_id"),
        "chunk_count": int(value.get("chunk_count") or 0),
        "vector_count": int(value.get("vector_count") or 0),
        "current": current,
        "stale": bool(value.get("stale")),
        "deletion_notice": "Deleting this managed copy never deletes the original file.",
    }


def _list_sources(
    project_root: Path,
    *,
    offset: int = 0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    if (
        isinstance(offset, bool)
        or isinstance(limit, bool)
        or offset < 0
        or not 1 <= limit <= MAX_LIST_LIMIT
    ):
        raise CliError(
            "INVALID_ARGUMENTS",
            f"offset must be non-negative and limit must be 1-{MAX_LIST_LIMIT}",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    core = _core_source_action(
        project_root,
        "list",
        ("--offset", str(offset), "--limit", str(limit)),
    )
    return {
        "items": [
            _compat_source_record(value)
            for value in list(core.get("items") or [])
            if isinstance(value, Mapping)
        ],
        "total": int(core.get("total") or 0),
        "offset": int(core.get("offset") or offset),
        "limit": int(core.get("limit") or limit),
        "managed_root": str(
            project_root / ".runtime" / "knowledge" / "sources"
        ),
        "deletion_notice": "Deleting a managed copy never deletes the original file.",
    }


def _external_folder(path_text: str) -> Path:
    absolute = Path(os.path.abspath(Path(path_text)))
    _assert_no_reparse_ancestors(absolute)
    if not _is_ordinary_directory(absolute):
        raise CliError(
            "SOURCE_FOLDER_NOT_FOUND",
            "The selected source folder is not an ordinary directory",
            exit_code=EXIT_NOT_FOUND,
        )
    try:
        return absolute.resolve(strict=True)
    except OSError as exc:
        raise CliError(
            "SOURCE_FOLDER_NOT_FOUND",
            "The selected source folder could not be resolved",
            exit_code=EXIT_NOT_FOUND,
        ) from exc


def _folder_candidates(
    folder: Path,
    supported: frozenset[str],
    maximum_bytes: int,
) -> tuple[list[Path], list[dict[str, Any]], int]:
    candidates: list[Path] = []
    skipped: list[dict[str, Any]] = []
    unsupported = 0
    for directory, directory_names, file_names in os.walk(
        folder,
        topdown=True,
        followlinks=False,
    ):
        base = Path(directory)
        if not _is_ordinary_directory(base):
            raise CliError(
                "UNSAFE_SOURCE_FOLDER",
                "The selected source folder contains an unsafe directory",
                exit_code=EXIT_INVALID_ARGUMENTS,
            )
        for name in sorted(directory_names):
            child = base / name
            if not _is_ordinary_directory(child):
                raise CliError(
                    "UNSAFE_SOURCE_FOLDER",
                    "The selected source folder contains a reparse point",
                    exit_code=EXIT_INVALID_ARGUMENTS,
                )
        directory_names[:] = sorted(directory_names)
        for name in sorted(file_names):
            path = base / name
            if not _is_ordinary_file(path):
                raise CliError(
                    "UNSAFE_SOURCE_FOLDER",
                    "The selected source folder contains a reparse point or non-file",
                    exit_code=EXIT_INVALID_ARGUMENTS,
                )
            if path.suffix.casefold() not in supported:
                unsupported += 1
                continue
            size = int(path.stat().st_size)
            if size > maximum_bytes:
                skipped.append(
                    {
                        "path": str(path),
                        "reason": "source_too_large",
                        "size_bytes": size,
                        "maximum_bytes": maximum_bytes,
                    }
                )
                continue
            candidates.append(path.resolve(strict=True))
    return candidates, skipped, unsupported


def _import_files(
    project_root: Path,
    paths: Sequence[str],
    supported: frozenset[str],
    maximum_bytes: int,
) -> dict[str, Any]:
    imported: list[dict[str, Any]] = []
    refreshes: list[dict[str, Any]] = []
    vectors: list[dict[str, Any]] = []
    warnings: list[str] = []
    for raw_path in paths:
        delegated_path = str(Path(os.path.abspath(Path(raw_path))))
        core = _core_source_action(
            project_root,
            "import",
            ("--path", delegated_path),
        )
        source = core.get("source")
        copied = bool(core.get("copied"))
        item = (
            _compat_source_record(source)
            if isinstance(source, Mapping)
            else {"source_id": "", "copied": copied}
        )
        item["copied"] = copied
        item["media_copied"] = bool(core.get("media_copied", False))
        item["transcript_managed"] = bool(
            core.get("transcript_managed", False)
        )
        item["status"] = "imported" if copied else "already_imported"
        imported.append(item)
        if isinstance(core.get("refresh"), Mapping):
            refreshes.append(dict(core["refresh"]))
        if isinstance(core.get("vector"), Mapping):
            vectors.append(dict(core["vector"]))
        warnings.extend(str(value) for value in list(core.get("warnings") or []))
    return {
        "items": imported,
        "imported": sum(bool(item["copied"]) for item in imported),
        "already_imported": sum(not bool(item["copied"]) for item in imported),
        "refresh": refreshes[-1] if refreshes else {},
        "vector": vectors[-1] if vectors else {},
        "warnings": warnings,
        "supported_formats": sorted(supported),
        "maximum_document_bytes": maximum_bytes,
    }


def _import_folders(
    project_root: Path,
    paths: Sequence[str],
    supported: frozenset[str],
    maximum_bytes: int,
) -> dict[str, Any]:
    all_candidates: list[Path] = []
    skipped: list[dict[str, Any]] = []
    unsupported = 0
    seen: set[str] = set()
    for raw in paths:
        folder = _external_folder(raw)
        candidates, folder_skipped, folder_unsupported = _folder_candidates(
            folder,
            supported,
            maximum_bytes,
        )
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key not in seen:
                seen.add(key)
                all_candidates.append(candidate)
        skipped.extend(folder_skipped)
        unsupported += folder_unsupported
    delegated = _import_files(
        project_root,
        [str(source) for source in all_candidates],
        supported,
        maximum_bytes,
    )
    return {
        "items": list(delegated["items"]),
        "imported": int(delegated["imported"]),
        "already_imported": int(delegated["already_imported"]),
        "skipped": skipped,
        "unsupported_files": unsupported,
        "refresh": dict(delegated["refresh"]),
        "warnings": list(delegated["warnings"]),
        "supported_formats": sorted(supported),
        "maximum_document_bytes": maximum_bytes,
    }


def _delete_source(
    project_root: Path,
    source_id: str,
) -> dict[str, Any]:
    result = _core_source_action(
        project_root,
        "delete",
        ("--source-id", source_id),
    )
    result["original_deleted"] = False
    result["notice"] = (
        "Only the project-managed copy was deleted; "
        "the original file was not touched."
    )
    return result


def _refresh_sources(project_root: Path) -> dict[str, Any]:
    return _core_source_action(project_root, "refresh")


def _import_thread(
    project_root: Path,
    thread_id: str,
    snapshot_file: str,
) -> dict[str, Any]:
    return _core_thread_action(
        project_root,
        "import",
        (
            "--thread-id",
            thread_id,
            "--snapshot-file",
            snapshot_file,
        ),
    )


def _remove_thread(
    project_root: Path,
    thread_id: str,
) -> dict[str, Any]:
    return _core_thread_action(
        project_root,
        "remove",
        ("--thread-id", thread_id),
    )


def _safe_child_environment(project_root: Path, layout: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        folded = name.casefold()
        if (
            folded in {
                "pythonhome",
                "pythonpath",
                "virtual_env",
                "conda_prefix",
            }
            or folded.startswith("pip_")
            or folded.startswith("uv_")
        ):
            environment.pop(name, None)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "UV_CACHE_DIR": str(layout["cache_root"]),
            "HF_HOME": str(layout["huggingface_cache"]),
            "HUGGINGFACE_HUB_CACHE": str(layout["huggingface_cache"]),
            "TRANSFORMERS_CACHE": str(layout["transformers_cache"]),
            "TORCH_HOME": str(layout["torch_cache"]),
            "TMP": str(layout["temp_root"]),
            "TEMP": str(layout["temp_root"]),
        }
    )
    return environment


_PYTHON_PROBE = r"""
import importlib.metadata
import json
import platform
import struct
import sys

def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return ""

payload = {
    "executable": sys.executable,
    "version": platform.python_version(),
    "bits": struct.calcsize("P") * 8,
    "prefix": sys.prefix,
    "base_prefix": getattr(sys, "base_prefix", sys.prefix),
    "pypdf_version": version("pypdf"),
    "embedding_worker_version": version("hia_embedding_worker"),
    "torch_installed": False,
    "torch_version": version("torch"),
    "torch_cuda_build": "",
    "cuda_available": False,
    "gpu_name": "",
}
try:
    import torch
    payload["torch_installed"] = True
    payload["torch_version"] = str(torch.__version__)
    payload["torch_cuda_build"] = str(torch.version.cuda or "")
    payload["cuda_available"] = bool(torch.cuda.is_available())
    if payload["cuda_available"]:
        payload["gpu_name"] = str(torch.cuda.get_device_name(0))
except Exception as exc:
    payload["torch_error"] = type(exc).__name__
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
"""


def _python_probe(
    project_root: Path,
    python_path: Path,
    layout: Mapping[str, str],
) -> dict[str, Any]:
    if not _is_ordinary_file(python_path):
        return {
            "available": False,
            "path": str(python_path),
            "error": "missing",
        }
    _assert_managed_chain(project_root, python_path)
    try:
        completed = subprocess.run(
            [str(python_path), "-I", "-B", "-c", _PYTHON_PROBE],
            cwd=project_root,
            env=_safe_child_environment(project_root, layout),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            "available": False,
            "path": str(python_path),
            "error": "probe_failed",
        }
    if completed.returncode != 0:
        return {
            "available": False,
            "path": str(python_path),
            "error": "probe_failed",
        }
    try:
        value = json.loads(completed.stdout.strip())
    except (TypeError, json.JSONDecodeError):
        return {
            "available": False,
            "path": str(python_path),
            "error": "invalid_probe",
        }
    if not isinstance(value, Mapping):
        return {
            "available": False,
            "path": str(python_path),
            "error": "invalid_probe",
        }
    output = dict(value)
    output["available"] = True
    output["path"] = str(python_path)
    return output


def _uv_status(
    project_root: Path,
    layout: Mapping[str, str],
) -> dict[str, Any]:
    uv_root = Path(layout["toolchain_root"]) / "uv"
    if not _path_exists(uv_root):
        return {"available": False, "path": "", "version": "", "source": "project"}
    try:
        _assert_managed_chain(project_root, uv_root)
    except CliError:
        return {
            "available": False,
            "path": str(uv_root),
            "version": "",
            "source": "project",
            "error": "unsafe_path",
        }
    if not _is_ordinary_directory(uv_root):
        return {
            "available": False,
            "path": str(uv_root),
            "version": "",
            "source": "project",
            "error": "unsafe_path",
        }
    candidates: list[tuple[str, Path]] = []
    for directory in uv_root.iterdir():
        if not _is_ordinary_directory(directory):
            continue
        executable = directory / ("uv.exe" if os.name == "nt" else "uv")
        if _is_ordinary_file(executable):
            candidates.append((directory.name, executable))
    if not candidates:
        return {"available": False, "path": "", "version": "", "source": "project"}
    declared_version, executable = sorted(candidates)[-1]
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            cwd=project_root,
            env=_safe_child_environment(project_root, layout),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        text = completed.stdout.strip() if completed.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        text = ""
    match = re.match(r"^uv\s+([0-9][^\s]*)(?:\s|$)", text)
    return {
        "available": bool(match),
        "path": str(executable),
        "version": match.group(1) if match else declared_version,
        "declared_version": declared_version,
        "source": "project",
        "probe": text,
    }


def _model_status(
    project_root: Path,
    contract_module: ModuleType,
    layout: Mapping[str, str],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    models_boundary = (project_root / ".runtime" / "models").resolve(strict=False)
    for profile_id, profile in contract_module.PROFILE_REGISTRY.items():
        configured = os.environ.get(profile.model_dir_environment, "").strip()
        raw_path = Path(configured) if configured else project_root / profile.model_directory
        path = (
            raw_path
            if raw_path.is_absolute()
            else project_root / raw_path
        ).resolve(strict=False)
        item: dict[str, Any] = {
            "profile_id": str(profile_id),
            "model_id": str(profile.model_id),
            "path": str(path),
            "configured_by_environment": bool(configured),
            "installed": False,
            "manifest_valid": False,
            "revision": "",
            "status": "missing",
        }
        if not _is_within(path, models_boundary):
            item["status"] = "unsafe_path"
            items.append(item)
            continue
        if not _path_exists(path):
            items.append(item)
            continue
        try:
            _assert_managed_chain(project_root, path)
        except CliError:
            item["status"] = "unsafe_path"
            items.append(item)
            continue
        if not _is_ordinary_directory(path):
            item["status"] = "unsafe_path"
            items.append(item)
            continue
        manifest_path = path / MODEL_MANIFEST_NAME
        config_path = path / "config.json"
        weights = [
            child
            for child in path.iterdir()
            if child.suffix.casefold() == ".safetensors"
            and _is_ordinary_file(child)
        ]
        if (
            not _is_ordinary_file(manifest_path)
            or manifest_path.stat().st_size > MAX_SIDECAR_BYTES
            or not _is_ordinary_file(config_path)
            or not weights
        ):
            item["status"] = "incomplete"
            items.append(item)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            item["status"] = "invalid_manifest"
            items.append(item)
            continue
        revision = str(manifest.get("revision") or "") if isinstance(manifest, Mapping) else ""
        try:
            manifest_contract = int(manifest.get("contract_version", -1))
        except (AttributeError, TypeError, ValueError):
            manifest_contract = -1
        valid = bool(
            isinstance(manifest, Mapping)
            and manifest_contract == int(contract_module.EMBEDDING_CONTRACT_VERSION)
            and str(manifest.get("profile_id") or "") == str(profile_id)
            and str(manifest.get("model_id") or "") == str(profile.model_id)
            and revision
        )
        item.update(
            {
                "installed": valid,
                "manifest_valid": valid,
                "revision": revision,
                "status": "installed" if valid else "invalid_manifest",
            }
        )
        items.append(item)
    return {
        "items": items,
        "installed_profiles": [
            item["profile_id"] for item in items if item["installed"]
        ],
    }


def _launcher_settings(
    project_root: Path,
    contract_module: ModuleType,
) -> tuple[dict[str, Any], dict[str, str]]:
    public = contract_module.launcher_contract()
    raw_keys = public.get("settings") if isinstance(public, Mapping) else None
    if not isinstance(raw_keys, Mapping):
        raise CliError(
            "EMBEDDING_CONTRACT_INVALID",
            "The embedding settings contract is unavailable",
        )
    keys = {
        "profile": str(raw_keys.get("profile") or ""),
        "device": str(raw_keys.get("device") or ""),
    }
    if not keys["profile"] or not keys["device"]:
        raise CliError(
            "EMBEDDING_CONTRACT_INVALID",
            "The embedding settings contract is incomplete",
        )
    settings_path = project_root / ".runtime" / "launcher" / "settings.json"
    if not _path_exists(settings_path):
        return {}, keys
    _assert_managed_chain(project_root, settings_path)
    if (
        not _is_ordinary_file(settings_path)
        or settings_path.stat().st_size > MAX_SIDECAR_BYTES
    ):
        raise CliError(
            "LAUNCHER_SETTINGS_INVALID",
            "The project-local launcher settings file is unsafe",
            exit_code=EXIT_INVALID_ARGUMENTS,
        )
    try:
        value = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, keys
    return (dict(value) if isinstance(value, Mapping) else {}), keys


def _environment_status(project_root: Path) -> dict[str, Any]:
    contract = _load_project_module(project_root, "contract")
    public_contract = contract.launcher_contract()
    layout = dict(contract.runtime_layout(project_root))
    for key, value in layout.items():
        path = Path(value)
        if not path.is_absolute() or not _is_within(
            path.resolve(strict=False),
            project_root,
        ):
            raise CliError(
                "EMBEDDING_CONTRACT_INVALID",
                f"The embedding runtime path {key} escaped the project",
            )
    venv_path = Path(layout["venv_root"]).resolve(strict=False)
    python_path = Path(layout["worker_python"]).resolve(strict=False)
    probe = _python_probe(project_root, python_path, layout)
    expected_prefix = venv_path
    actual_executable = Path(str(probe.get("executable") or python_path)).resolve(
        strict=False
    )
    actual_prefix = Path(str(probe.get("prefix") or venv_path)).resolve(strict=False)
    base_prefix_text = str(probe.get("base_prefix") or "")
    base_prefix = (
        Path(base_prefix_text).resolve(strict=False)
        if base_prefix_text
        else None
    )
    marker_path = venv_path / VENV_MARKER_NAME
    marker_valid = False
    marker: dict[str, Any] = {}
    if _is_ordinary_file(marker_path) and marker_path.stat().st_size <= MAX_SIDECAR_BYTES:
        try:
            raw_marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker = dict(raw_marker) if isinstance(raw_marker, Mapping) else {}
            marker_valid = bool(
                marker.get("schema") == VENV_MARKER_SCHEMA
                and marker.get("role") == "hia-embedding"
                and str(marker.get("python_version") or "")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            marker_valid = False
    python_status = {
        **probe,
        "expected_path": str(python_path),
        "version": str(probe.get("version") or ""),
        "bits": int(probe.get("bits") or struct.calcsize("P") * 8),
        "prefix": str(probe.get("prefix") or ""),
        "base_prefix": base_prefix_text,
        "is_venv": bool(
            probe.get("available")
            and str(probe.get("prefix") or "")
            != str(probe.get("base_prefix") or "")
        ),
        "executable_is_project_local": _is_within(actual_executable, project_root),
        "prefix_is_expected_venv": _same_path(actual_prefix, expected_prefix),
        "base_prefix_is_project_local": bool(
            base_prefix is not None and _is_within(base_prefix, project_root)
        ),
        "resolution": "project-relative contract; re-resolved after project moves",
    }
    portable = bool(
        probe.get("available")
        and marker_valid
        and python_status["executable_is_project_local"]
        and python_status["prefix_is_expected_venv"]
        and python_status["base_prefix_is_project_local"]
    )
    python_status["portable"] = portable
    python_status["managed_marker"] = {
        "path": str(marker_path),
        "valid": marker_valid,
        "schema": str(marker.get("schema") or ""),
    }
    pypdf_version = str(probe.get("pypdf_version") or "")
    worker_version = str(probe.get("embedding_worker_version") or "")
    torch_installed = bool(probe.get("torch_installed"))
    cuda_available = bool(probe.get("cuda_available"))
    models = _model_status(project_root, contract, layout)
    if not _path_exists(venv_path):
        environment_state = "missing"
    elif not portable or pypdf_version != "6.14.2":
        environment_state = "repair_required"
    else:
        environment_state = "ready"
    settings, setting_keys = _launcher_settings(project_root, contract)
    environment_profile = os.environ.get(
        contract.EMBEDDING_PROFILE_ENVIRONMENT,
        "",
    ).strip()
    stored_profile = str(settings.get(setting_keys["profile"]) or "").strip()
    requested_profile = (
        environment_profile
        or stored_profile
        or str(public_contract["default_profile"])
    )
    profile_source = (
        "environment"
        if environment_profile
        else "settings"
        if stored_profile
        else "default"
    )
    installed_profiles = list(models["installed_profiles"])
    if requested_profile in installed_profiles:
        active_profile = requested_profile
    else:
        active_profile = ""
    environment_device = os.environ.get(
        contract.EMBEDDING_DEVICE_ENVIRONMENT,
        "",
    ).strip().casefold()
    stored_device = str(settings.get(setting_keys["device"]) or "").strip().casefold()
    requested_device = environment_device or stored_device or "auto"
    if requested_device not in {"auto", "cuda", "cpu"}:
        requested_device = "auto"
    device_source = (
        "environment"
        if environment_device
        else "settings"
        if stored_device
        else "default"
    )
    embedding_prerequisites = bool(
        portable
        and active_profile == requested_profile
        and torch_installed
        and worker_version
    )
    if not embedding_prerequisites:
        embedding_mode = "unavailable"
        active_device = ""
    elif requested_device == "cpu":
        embedding_mode = "cpu_embedding"
        active_device = "cpu"
    elif requested_device == "cuda" and cuda_available:
        embedding_mode = "cuda"
        active_device = "cuda"
    elif requested_device == "cuda":
        embedding_mode = "unavailable"
        active_device = ""
    elif cuda_available:
        embedding_mode = "cuda"
        active_device = "cuda"
    else:
        embedding_mode = "cpu_embedding"
        active_device = "cpu"
    embedding_environment: dict[str, str] = {}
    if embedding_mode != "unavailable" and active_profile == requested_profile:
        profile = contract.PROFILE_REGISTRY[active_profile]
        model = next(
            item
            for item in models["items"]
            if item["profile_id"] == active_profile
        )
        embedding_environment = {
            str(contract.EMBEDDING_PYTHON_ENVIRONMENT): str(python_path),
            str(contract.EMBEDDING_PROFILE_ENVIRONMENT): active_profile,
            str(contract.EMBEDDING_DIMENSION_ENVIRONMENT): str(
                profile.default_dimension
            ),
            str(contract.EMBEDDING_DEVICE_ENVIRONMENT): active_device,
            str(profile.model_dir_environment): str(model["path"]),
            str(profile.model_revision_environment): str(model["revision"]),
        }
    try:
        disk = shutil.disk_usage(project_root)
        free_bytes = int(disk.free)
        total_bytes = int(disk.total)
    except OSError:
        free_bytes = -1
        total_bytes = -1
    repair_space_hint = 2 * 1024 * 1024 * 1024
    return {
        "state": environment_state,
        "project_root": str(project_root),
        "venv": {
            "path": str(venv_path),
            "exists": _is_ordinary_directory(venv_path),
            "resolved_from_project": True,
            "portable": portable,
            "repair_required": environment_state == "repair_required",
        },
        "python": python_status,
        "uv": _uv_status(project_root, layout),
        "parser": {
            "name": "pypdf",
            "required_version": "6.14.2",
            "installed": pypdf_version == "6.14.2",
            "version": pypdf_version,
            "repair_action": "environment-repair",
        },
        "torch": {
            "installed": torch_installed,
            "version": str(probe.get("torch_version") or ""),
            "cuda_build": str(probe.get("torch_cuda_build") or ""),
            "cuda_available": cuda_available,
            "gpu_name": str(probe.get("gpu_name") or ""),
            "error": str(probe.get("torch_error") or ""),
        },
        "embedding_worker": {
            "installed": bool(worker_version),
            "version": worker_version,
        },
        "models": models,
        "requested_profile": requested_profile,
        "active_profile": active_profile,
        "profile_source": profile_source,
        "selected_profile_available": active_profile == requested_profile,
        "requested_device": requested_device,
        "active_device": active_device,
        "device_source": device_source,
        "embedding_mode": embedding_mode,
        "embedding_runtime_environment": embedding_environment,
        "houdini_launch_blocked": embedding_mode == "unavailable",
        "repair_actions": {
            "parser_only": "environment-repair",
            "embedding": "Use the explicit embedding installer only when embeddings are wanted.",
        },
        "storage": {
            "free_bytes": free_bytes,
            "total_bytes": total_bytes,
            "repair_space_hint_bytes": repair_space_hint,
            "repair_space_hint_satisfied": (
                None if free_bytes < 0 else free_bytes >= repair_space_hint
            ),
        },
        "proxy": {
            "http_configured": bool(
                os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
            ),
            "https_configured": bool(
                os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            ),
            "no_proxy_configured": bool(
                os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
            ),
            "values_redacted": True,
        },
        "layout": layout,
    }


def _index_status(index: Any) -> dict[str, Any]:
    with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
        document_count = int(
            connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        )
        user_document_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE source_group = 'user'"
            ).fetchone()[0]
        )
        chunk_count = int(
            connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        )
        vector_count = int(
            connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
        )
    database_path = Path(index.database_path).resolve(strict=False)
    return {
        "available": _is_ordinary_file(database_path),
        "initialized": True,
        "status": "ready",
        "path": str(database_path),
        "fts5_available": True,
        "document_count": document_count,
        "user_document_count": user_document_count,
        "chunk_count": chunk_count,
        "vector_count": vector_count,
        "vector_pending": max(0, chunk_count - vector_count),
        "total_chunks": chunk_count,
        "vector_chunks": vector_count,
        "pending_chunks": max(0, chunk_count - vector_count),
        "complete": vector_count >= chunk_count,
        "partial": 0 < vector_count < chunk_count,
    }


def _status(
    project_root: Path,
    knowledge_module: ModuleType,
    supported: frozenset[str],
    folder_supported: frozenset[str],
    maximum_bytes: int,
) -> dict[str, Any]:
    environment = _environment_status(project_root)
    warnings: list[str] = []
    try:
        sources = _list_sources(project_root)
    except CliError as exc:
        sources = {
            "items": [],
            "total": 0,
            "managed_root": str(
                project_root / ".runtime" / "knowledge" / "sources"
            ),
            "deletion_notice": (
                "Deleting a managed copy never deletes the original file."
            ),
        }
        warnings.append(exc.message)
    database_path = (
        project_root / ".runtime" / "knowledge" / "knowledge.sqlite3"
    ).resolve(strict=False)
    if not _path_exists(database_path):
        index_value = {
            "available": False,
            "initialized": False,
            "status": "not_initialized",
            "path": str(database_path),
            "fts5_available": False,
            "document_count": 0,
            "user_document_count": 0,
            "chunk_count": 0,
            "vector_count": 0,
            "vector_pending": 0,
            "total_chunks": 0,
            "vector_chunks": 0,
            "pending_chunks": 0,
            "complete": False,
            "partial": False,
            "error": "KNOWLEDGE_INDEX_NOT_INITIALIZED",
        }
    else:
        try:
            index = _open_index(
                project_root,
                knowledge_module,
                initialize=False,
            )
            index_value = _index_status(index)
        except CliError as exc:
            index_value = {
                "available": False,
                "initialized": False,
                "status": "unavailable",
                "path": str(database_path),
                "fts5_available": False,
                "error": exc.code,
            }
            warnings.append(exc.message)
    return {
        "environment": environment,
        "index": index_value,
        "sources": sources,
        "source_contract": {
            "supported_formats": sorted(supported),
            "folder_supported_formats": sorted(folder_supported),
            "maximum_document_bytes": maximum_bytes,
            "managed_copy": True,
            "media_copy_policy": "transcript_sidecar_only",
            "deletion_notice": (
                "Deleting a managed copy never deletes the original file."
            ),
        },
        "retrieval": {
            "mode": environment["embedding_mode"],
            "fts5_available": bool(index_value.get("fts5_available")),
            "houdini_launch_blocked": bool(
                environment.get("houdini_launch_blocked")
            ),
        },
        "warning": " ".join(warnings),
    }


def _safe_message(error: BaseException) -> str:
    value = str(getattr(error, "message", "") or error).strip()
    value = re.sub(
        r"(?i)\b(token|secret|password|authorization)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        value,
    )
    return " ".join(value.split())[:2048] or "Knowledge operation failed"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _emit(
    *,
    ok: bool,
    action: str,
    project_root: Path | None,
    result: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema": CLI_SCHEMA,
        "ok": ok,
        "action": action,
        "timestamp_utc": _utc_now(),
    }
    if project_root is not None:
        payload["project_root"] = str(project_root)
    if result is not None:
        payload["result"] = dict(result)
    if error is not None:
        payload["error"] = dict(error)
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    action = "unknown"
    root: Path | None = None
    try:
        arguments = _parser().parse_args(argv)
        action = str(arguments.command)
        root = _project_root(str(arguments.project_root or ""))
        _strip_unmanaged_site_paths(root)
        (
            knowledge_module,
            supported,
            folder_supported,
            maximum_bytes,
        ) = _supported_contract(root)
        if action == "environment-status":
            result = _environment_status(root)
        elif action == "status":
            result = _status(
                root,
                knowledge_module,
                supported,
                folder_supported,
                maximum_bytes,
            )
        elif action == "import-file":
            result = _import_files(
                root,
                list(arguments.path),
                supported,
                maximum_bytes,
            )
        elif action == "import-folder":
            result = _import_folders(
                root,
                list(arguments.path),
                folder_supported,
                maximum_bytes,
            )
        elif action == "thread-import":
            result = _import_thread(
                root,
                str(arguments.thread_id),
                str(arguments.snapshot_file),
            )
        elif action == "thread-remove":
            result = _remove_thread(
                root,
                str(arguments.thread_id),
            )
        elif action == "list":
            result = _list_sources(
                root,
                offset=int(arguments.offset),
                limit=int(arguments.limit),
            )
        elif action == "delete":
            result = _delete_source(
                root,
                str(arguments.source_id),
            )
        else:
            result = _refresh_sources(root)
        _emit(ok=True, action=action, project_root=root, result=result)
        return 0
    except CliError as exc:
        _emit(
            ok=False,
            action=action,
            project_root=root,
            error={"code": exc.code, "message": exc.message},
        )
        return exc.exit_code
    except KeyboardInterrupt:
        _emit(
            ok=False,
            action=action,
            project_root=root,
            error={
                "code": "INTERRUPTED",
                "message": "The knowledge operation was interrupted",
            },
        )
        return 130
    except Exception as exc:
        _emit(
            ok=False,
            action=action,
            project_root=root,
            error={
                "code": "KNOWLEDGE_OPERATION_FAILED",
                "message": _safe_message(exc),
            },
        )
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
