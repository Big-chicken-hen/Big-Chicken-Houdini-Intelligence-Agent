"""Project-local SQLite knowledge index with an FTS5 reliability baseline.

This module owns allowed-source ingestion and lexical search.  Optional vector
encoding lives in a separate process; no model dependency is imported here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

if __package__:
    from .deterministic_sources import (
        MEDIA_SUFFIXES,
        NormalizedSource,
        SourceAdapterError,
        normalize_selected_file,
    )
else:
    import importlib.util
    import sys

    _adapter_path = Path(__file__).with_name("deterministic_sources.py")
    _adapter_name = (
        "_hia_deterministic_sources_"
        + hashlib.sha256(str(_adapter_path).encode("utf-8")).hexdigest()[:12]
    )
    _adapter = sys.modules.get(_adapter_name)
    if _adapter is None:
        _adapter_spec = importlib.util.spec_from_file_location(
            _adapter_name,
            _adapter_path,
        )
        if _adapter_spec is None or _adapter_spec.loader is None:
            raise ImportError("deterministic source adapter is unavailable")
        _adapter = importlib.util.module_from_spec(_adapter_spec)
        sys.modules[_adapter_name] = _adapter
        try:
            _adapter_spec.loader.exec_module(_adapter)
        except Exception:
            sys.modules.pop(_adapter_name, None)
            raise
    MEDIA_SUFFIXES = _adapter.MEDIA_SUFFIXES
    NormalizedSource = _adapter.NormalizedSource
    SourceAdapterError = _adapter.SourceAdapterError
    normalize_selected_file = _adapter.normalize_selected_file


DATABASE_FILENAME = "knowledge.sqlite3"
SCHEMA_VERSION = "2"
REFRESH_INTERVAL_SECONDS = 10 * 60.0
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP_CHARS = 120
MAX_LEXICAL_RANK_CANDIDATES = 2_000

PROJECT_HELP_DOCUMENTS = (
    "ARCHITECTURE.md",
    "DIAGNOSTICS.md",
    "HIA-MCP-V2.md",
    "ROADMAP.md",
    "SAFETY.md",
)
PROJECT_TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".html", ".htm", ".json", ".yaml", ".yml"}
)
HOUDINI_TEXT_SUFFIXES = PROJECT_TEXT_SUFFIXES
USER_TEXT_SUFFIXES = frozenset({".md", ".txt", ".html", ".htm", ".srt", ".vtt"})
USER_OPTIONAL_SUFFIXES = frozenset({".pdf"})
USER_INGEST_SUFFIXES = USER_TEXT_SUFFIXES | USER_OPTIONAL_SUFFIXES | MEDIA_SUFFIXES
SOURCE_GROUPS = frozenset({"houdini", "project", "user"})
SEARCH_SOURCE_GROUPS = SOURCE_GROUPS | frozenset({"memory"})
BUNDLED_KNOWLEDGE_MANIFEST = Path("knowledge/sidefx-official/manifest.json")
BUNDLED_COMMUNITY_KNOWLEDGE_MANIFEST = Path(
    "knowledge/community-tutorials/manifest.json"
)
BUILTIN_KNOWLEDGE_DIRECTORY = "builtin"
BUILTIN_ACTIVE_FILENAME = "active.json"
BUILTIN_PACK_MARKER = ".pack.json"
BUILTIN_WORKFLOW_SOURCE = "builtin_official_workflow"
COMMUNITY_KNOWLEDGE_DIRECTORY = "community"
COMMUNITY_TUTORIAL_SOURCE = "community_tutorial"
USER_DOCUMENT_SOURCE = "user_document"
USER_TRANSCRIPT_SOURCE = "user_transcript"
THREAD_EXPORT_SOURCE = "thread_export"
PROJECT_MEMORY_SOURCE = "project_memory"
USER_FILTERABLE_SOURCE_KINDS = frozenset(
    {
        USER_DOCUMENT_SOURCE,
        USER_TRANSCRIPT_SOURCE,
        THREAD_EXPORT_SOURCE,
        PROJECT_MEMORY_SOURCE,
    }
)
FILTERABLE_SOURCE_KINDS = frozenset(
    {
        BUILTIN_WORKFLOW_SOURCE,
        COMMUNITY_TUTORIAL_SOURCE,
        *USER_FILTERABLE_SOURCE_KINDS,
    }
)
SOURCE_KIND_GROUPS = {
    BUILTIN_WORKFLOW_SOURCE: "project",
    COMMUNITY_TUTORIAL_SOURCE: "project",
    USER_DOCUMENT_SOURCE: "user",
    USER_TRANSCRIPT_SOURCE: "user",
    THREAD_EXPORT_SOURCE: "user",
    PROJECT_MEMORY_SOURCE: "memory",
}
BUILTIN_METADATA_FILES = ("coverage.json", "sources.json")
MAX_BUILTIN_CARDS = 4096
MAX_BUILTIN_MANIFEST_BYTES = 1024 * 1024
HOUDINI_HELP_ARCHIVES = frozenset(
    {
        "anim.zip",
        "basics.zip",
        "fluid.zip",
        "hom.zip",
        "model.zip",
        "network.zip",
        "news.zip",
        "nodes.zip",
        "pyro.zip",
        "render.zip",
        "shade.zip",
        "solaris.zip",
        "tops.zip",
        "vellum.zip",
        "vex.zip",
    }
)
HOUDINI_HELP_EXCLUDED_PREFIXES = (
    "examples/",
    "files/",
    "licenses/",
    "videos/",
)


class KnowledgeIndexError(RuntimeError):
    """Raised when the deterministic local index cannot be used."""


@dataclass(frozen=True)
class _Candidate:
    collection: str
    source_group: str
    source: str
    source_key: str
    title: str
    source_path: str
    url: str
    author: str
    houdini_version: str
    license_name: str
    verification: str
    evidence: str
    attributes: Mapping[str, Any]
    path: Path | None = None
    sidecar_path: Path | None = None
    inline_text: str | None = None
    archive_member: str = ""
    stat_size: int = 0
    stat_mtime_ns: int = 0


@dataclass(frozen=True)
class _BuiltinPack:
    root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    entries: tuple[Mapping[str, Any], ...]
    metadata_files: tuple[str, ...]
    pack_id: str
    pack_version: str
    digest: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.casefold() in {"script", "style"}:
            self._hidden_depth += 1
        elif not self._hidden_depth and tag.casefold() in {
            "br",
            "p",
            "div",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif not self._hidden_depth and tag.casefold() in {
            "p",
            "div",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


class LocalKnowledgeIndex:
    """Incremental SQLite/FTS5 index stored below ``.runtime/knowledge``."""

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        *,
        initialize: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.knowledge_root = (
            self.project_root / ".runtime" / "knowledge"
        ).resolve()
        if not _is_within(self.knowledge_root, self.project_root):
            raise KnowledgeIndexError(
                "The local knowledge directory escaped the project root"
            )
        self.sources_root = self.knowledge_root / "sources"
        self.database_path = self.knowledge_root / DATABASE_FILENAME
        if initialize:
            self.knowledge_root.mkdir(parents=True, exist_ok=True)
            self.sources_root.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
        elif not self.database_path.is_file():
            raise KnowledgeIndexError(
                "The local knowledge index is not initialized; run an explicit "
                "refresh before using read-only search"
            )

    @property
    def relative_database_path(self) -> str:
        return self.database_path.relative_to(self.project_root).as_posix()

    def active_houdini_version(self) -> str:
        return self._meta_value("active_houdini_version", read_only=True)

    @property
    def builtin_root(self) -> Path:
        return self.knowledge_root / BUILTIN_KNOWLEDGE_DIRECTORY

    @property
    def community_root(self) -> Path:
        return self.knowledge_root / COMMUNITY_KNOWLEDGE_DIRECTORY

    def bootstrap_builtin_pack(self) -> tuple[dict[str, Any], list[str]]:
        """Stage the bundled pack; refresh publishes it after SQLite commits."""

        warnings: list[str] = []
        source_manifest = self.project_root / BUNDLED_KNOWLEDGE_MANIFEST
        try:
            source_pack = _load_builtin_pack(source_manifest)
        except FileNotFoundError:
            active = self._active_builtin_pack()
            if active is None:
                return (
                    {
                        "available": False,
                        "installed": False,
                        "changed": False,
                        "pack_id": "",
                        "pack_version": "",
                        "digest": "",
                        "cards": 0,
                        "runtime_path": "",
                        "fallback_reason": "BUNDLED_PACK_NOT_FOUND",
                    },
                    [],
                )
            warnings.append(
                "Bundled source pack was unavailable; the installed runtime "
                "copy remains active."
            )
            return self._builtin_pack_result(active, changed=False), warnings

        self.builtin_root.mkdir(parents=True, exist_ok=True)
        target_parent = self.builtin_root / _safe_pack_component(source_pack.pack_id)
        target_parent.mkdir(parents=True, exist_ok=True)
        target_name = (
            f"{_safe_pack_component(source_pack.pack_version)}-"
            f"{source_pack.digest[:16]}"
        )
        target = target_parent / target_name
        changed = False
        if target.exists() and not _installed_pack_matches(target, source_pack):
            target = target_parent / f"{target_name}-repair-{uuid.uuid4().hex[:8]}"
        if not target.exists():
            self._copy_builtin_pack(source_pack, target)
            changed = True

        installed_pack = _load_builtin_pack(target / "manifest.json")
        active_payload = self._builtin_pack_active_payload(installed_pack)
        active_path = self.builtin_root / BUILTIN_ACTIVE_FILENAME
        current_payload = _read_json_object(active_path)
        if current_payload != active_payload:
            changed = True
        return self._builtin_pack_result(installed_pack, changed=changed), warnings

    def bootstrap_community_pack(self) -> tuple[dict[str, Any], list[str]]:
        """Stage the curated community pack beside, never as, official data."""

        warnings: list[str] = []
        source_manifest = (
            self.project_root / BUNDLED_COMMUNITY_KNOWLEDGE_MANIFEST
        )
        try:
            source_pack = _load_community_pack(source_manifest)
        except FileNotFoundError:
            active = self._active_community_pack()
            if active is None:
                return (
                    {
                        "available": False,
                        "installed": False,
                        "changed": False,
                        "pack_id": "",
                        "pack_version": "",
                        "digest": "",
                        "cards": 0,
                        "runtime_path": "",
                        "fallback_reason": "COMMUNITY_PACK_NOT_FOUND",
                    },
                    [],
                )
            warnings.append(
                "Bundled community tutorial pack was unavailable; the "
                "installed runtime copy remains active."
            )
            return self._builtin_pack_result(active, changed=False), warnings

        self.community_root.mkdir(parents=True, exist_ok=True)
        target_parent = (
            self.community_root / _safe_pack_component(source_pack.pack_id)
        )
        target_parent.mkdir(parents=True, exist_ok=True)
        target_name = (
            f"{_safe_pack_component(source_pack.pack_version)}-"
            f"{source_pack.digest[:16]}"
        )
        target = target_parent / target_name
        changed = False
        if target.exists() and not _installed_community_pack_matches(
            target,
            source_pack,
        ):
            target = (
                target_parent
                / f"{target_name}-repair-{uuid.uuid4().hex[:8]}"
            )
        if not target.exists():
            self._copy_community_pack(source_pack, target)
            changed = True

        installed_pack = _load_community_pack(target / "manifest.json")
        active_payload = self._builtin_pack_active_payload(installed_pack)
        active_path = self.community_root / BUILTIN_ACTIVE_FILENAME
        if _read_json_object(active_path) != active_payload:
            changed = True
        return self._builtin_pack_result(installed_pack, changed=changed), warnings

    def corpus_status(self) -> dict[str, Any]:
        """Return read-only document and pack counts for callers and launchers."""

        with closing(self._connect(read_only=True)) as connection:
            document_rows = connection.execute(
                "SELECT source_group, source, COUNT(*) FROM documents "
                "GROUP BY source_group, source"
            ).fetchall()
            chunk_rows = connection.execute(
                "SELECT d.source_group, d.source, COUNT(*) "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "GROUP BY d.source_group, d.source"
            ).fetchall()
            total_documents = int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            total_chunks = int(
                connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            )
            pack_json = self._meta_value_from_connection(
                connection,
                "builtin_pack",
            )
            community_pack_json = self._meta_value_from_connection(
                connection,
                "community_pack",
            )
        documents_by_source = {
            str(source): int(count)
            for _group, source, count in document_rows
        }
        chunks_by_source = {
            str(source): int(count)
            for _group, source, count in chunk_rows
        }
        try:
            pack = json.loads(pack_json) if pack_json else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            pack = {}
        if not isinstance(pack, Mapping):
            pack = {}
        try:
            community_pack_value = (
                json.loads(community_pack_json) if community_pack_json else {}
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            community_pack_value = {}
        if not isinstance(community_pack_value, Mapping):
            community_pack_value = {}
        builtin_documents = documents_by_source.get(BUILTIN_WORKFLOW_SOURCE, 0)
        builtin_chunks = chunks_by_source.get(BUILTIN_WORKFLOW_SOURCE, 0)
        builtin_pack = {
            "installed": bool(pack),
            "pack_id": str(pack.get("pack_id") or ""),
            "pack_version": str(pack.get("pack_version") or ""),
            "digest": str(pack.get("digest") or ""),
            "cards": int(pack.get("cards") or 0),
            "documents": builtin_documents,
            "chunks": builtin_chunks,
            "runtime_path": str(pack.get("runtime_path") or ""),
        }
        builtin_pack["complete"] = bool(
            builtin_pack["installed"]
            and builtin_pack["cards"] == builtin_documents
        )
        community_documents = documents_by_source.get(
            COMMUNITY_TUTORIAL_SOURCE,
            0,
        )
        community_chunks = chunks_by_source.get(COMMUNITY_TUTORIAL_SOURCE, 0)
        community_pack = {
            "installed": bool(community_pack_value),
            "pack_id": str(community_pack_value.get("pack_id") or ""),
            "pack_version": str(
                community_pack_value.get("pack_version") or ""
            ),
            "digest": str(community_pack_value.get("digest") or ""),
            "cards": int(community_pack_value.get("cards") or 0),
            "documents": community_documents,
            "chunks": community_chunks,
            "runtime_path": str(
                community_pack_value.get("runtime_path") or ""
            ),
        }
        community_pack["complete"] = bool(
            community_pack["installed"]
            and community_pack["cards"] == community_documents
        )
        return {
            "documents": total_documents,
            "chunks": total_chunks,
            "builtin_pack": builtin_pack,
            "community_pack": community_pack,
            "user_documents": sum(
                int(count)
                for group, _source, count in document_rows
                if str(group) == "user"
            ),
            "memory_documents": documents_by_source.get("project_memory", 0),
            "houdini_documents": sum(
                int(count)
                for group, _source, count in document_rows
                if str(group) == "houdini"
            ),
            "project_documents": sum(
                int(count)
                for group, source, count in document_rows
                if (
                    str(group) == "project"
                    and str(source)
                    not in {
                        BUILTIN_WORKFLOW_SOURCE,
                        COMMUNITY_TUTORIAL_SOURCE,
                    }
                )
            ),
        }

    def document_content(
        self,
        document_id: int,
        *,
        max_chars: int = MAX_DOCUMENT_BYTES,
    ) -> tuple[str, bool]:
        """Reconstruct one indexed document body from its ordered chunks."""

        bounded_chars = max(1, min(int(max_chars), MAX_DOCUMENT_BYTES))
        with closing(self._connect(read_only=True)) as connection:
            rows = connection.execute(
                "SELECT body FROM chunks WHERE document_id = ? ORDER BY ordinal",
                (int(document_id),),
            ).fetchall()
        content = _join_chunk_bodies([str(row[0]) for row in rows])
        if len(content) <= bounded_chars:
            return content, False
        return content[:bounded_chars].rstrip(), True

    def refresh_explicit_records(
        self,
        records: Iterable[NormalizedSource],
        *,
        remove_source_keys: Iterable[str] = (),
        replace_thread_id: str = "",
        collection: str = "explicit_user",
    ) -> dict[str, Any]:
        """Transactionally upsert or remove explicitly supplied user records."""

        collection_name = str(collection or "").strip()
        if collection_name not in {"explicit_user", "asset"}:
            raise KnowledgeIndexError(
                "Explicit records require the explicit_user or asset collection"
            )
        thread_id = str(replace_thread_id or "").strip()
        if thread_id and collection_name != "explicit_user":
            raise KnowledgeIndexError(
                "Thread replacement requires the explicit_user collection"
            )
        if thread_id and (
            len(thread_id) > 256
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", thread_id)
            is None
        ):
            raise KnowledgeIndexError("replace_thread_id is invalid")
        prepared: list[_Candidate] = []
        by_key: dict[str, _Candidate] = {}
        for record in records:
            if not isinstance(record, NormalizedSource):
                raise KnowledgeIndexError(
                    "Explicit source records must be normalized first"
                )
            if record.source_kind not in {
                USER_DOCUMENT_SOURCE,
                USER_TRANSCRIPT_SOURCE,
                THREAD_EXPORT_SOURCE,
            }:
                raise KnowledgeIndexError(
                    "Only explicit document, transcript, and thread records "
                    "may use this ingestion path"
                )
            if thread_id and (
                record.source_kind != THREAD_EXPORT_SOURCE
                or str(record.provenance.get("thread_id") or "")
                != thread_id
            ):
                raise KnowledgeIndexError(
                    "Thread replacement records must belong to replace_thread_id"
                )
            candidate = self._normalized_user_candidate(
                record,
                collection=collection_name,
            )
            if candidate.source_key in by_key:
                raise KnowledgeIndexError(
                    "Explicit source batch contains duplicate source keys"
                )
            by_key[candidate.source_key] = candidate
            prepared.append(candidate)

        removals = {str(value).strip() for value in remove_source_keys}
        if "" in removals:
            raise KnowledgeIndexError("Explicit removal keys must not be blank")
        conflicts = removals.intersection(by_key)
        if conflicts:
            raise KnowledgeIndexError(
                "An explicit source cannot be updated and removed together"
            )
        stats: dict[str, Any] = {
            "refreshed": bool(prepared or removals or thread_id),
            "refresh_reason": (
                "thread_replace" if thread_id else "explicit_records"
            ),
            "records_received": len(prepared),
            "collection": collection_name,
            "documents_added": 0,
            "documents_updated": 0,
            "documents_removed": 0,
            "documents_unchanged": 0,
            "document_ids": [],
        }
        if thread_id:
            stats["thread_id"] = thread_id
        if not prepared and not removals and not thread_id:
            return stats

        allowed_sources = (
            USER_DOCUMENT_SOURCE,
            USER_TRANSCRIPT_SOURCE,
            THREAD_EXPORT_SOURCE,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if thread_id:
                    rows = connection.execute(
                        "SELECT source_key, attributes_json FROM documents "
                        "WHERE source_group = 'user' "
                        "AND source = 'thread_export'"
                    ).fetchall()
                    removals.update(
                        str(row[0])
                        for row in rows
                        if (
                            _metadata_thread_id(str(row[1])) == thread_id
                            and str(row[0]) not in by_key
                        )
                    )
                for source_key in sorted(removals):
                    row = connection.execute(
                        "SELECT id FROM documents WHERE source_group = 'user' "
                        "AND source IN (?, ?, ?) AND source_key = ?",
                        (*allowed_sources, source_key),
                    ).fetchone()
                    if row is not None:
                        self._delete_document(connection, int(row[0]))
                        stats["documents_removed"] += 1

                for candidate in prepared:
                    text = str(candidate.inline_text or "")
                    text_sha256 = hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest()
                    metadata = self._candidate_metadata(candidate)
                    metadata_sha256 = hashlib.sha256(
                        _stable_metadata_json(metadata).encode("utf-8")
                    ).hexdigest()
                    existing = connection.execute(
                        "SELECT id, title, sha256, metadata_sha256 "
                        "FROM documents WHERE source_key = ?",
                        (candidate.source_key,),
                    ).fetchone()
                    if (
                        existing is not None
                        and str(existing[1]) == candidate.title
                        and str(existing[2]) == text_sha256
                        and str(existing[3]) == metadata_sha256
                    ):
                        document_id = int(existing[0])
                        stats["documents_unchanged"] += 1
                    else:
                        self._replace_document(
                            connection,
                            candidate,
                            text,
                            text_sha256,
                            metadata_sha256,
                            metadata,
                            int(existing[0]) if existing is not None else None,
                        )
                        document_id = int(
                            connection.execute(
                                "SELECT id FROM documents WHERE source_key = ?",
                                (candidate.source_key,),
                            ).fetchone()[0]
                        )
                        if existing is None:
                            stats["documents_added"] += 1
                        else:
                            stats["documents_updated"] += 1
                    stats["document_ids"].append(document_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        stats["document_ids"] = sorted(set(stats["document_ids"]))
        return stats

    def filtered_document_ids(
        self,
        *,
        source_kinds: Iterable[str] = (),
        card_id: str = "",
        canonical_id: str = "",
    ) -> tuple[int, ...] | None:
        """Resolve optional pack identity filters without requiring JSON1."""

        kinds = {str(value) for value in source_kinds}
        invalid = kinds.difference(FILTERABLE_SOURCE_KINDS)
        if invalid:
            raise KnowledgeIndexError(
                f"Unsupported local knowledge source kinds: {sorted(invalid)!r}"
            )
        card_key = str(card_id).strip().casefold()
        canonical_key = str(canonical_id).strip().casefold()
        if not kinds and not card_key and not canonical_key:
            return None

        with closing(self._connect(read_only=True)) as connection:
            rows = connection.execute(
                "SELECT id, source, attributes_json FROM documents"
            )
            matches: list[int] = []
            for document_id, source, attributes_json in rows:
                if kinds and str(source) not in kinds:
                    continue
                if not card_key and not canonical_key:
                    matches.append(int(document_id))
                    continue
                if (
                    (card_key or canonical_key)
                    and str(source)
                    not in {
                        BUILTIN_WORKFLOW_SOURCE,
                        COMMUNITY_TUTORIAL_SOURCE,
                    }
                ):
                    continue
                try:
                    attributes = json.loads(str(attributes_json) or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(attributes, Mapping):
                    continue
                if card_key and (
                    str(attributes.get("card_id") or "").strip().casefold()
                    != card_key
                ):
                    continue
                if canonical_key and (
                    str(attributes.get("canonical_id") or "").strip().casefold()
                    != canonical_key
                ):
                    continue
                matches.append(int(document_id))
        return tuple(matches)

    def _active_builtin_pack(self) -> _BuiltinPack | None:
        state = _read_json_object(self.builtin_root / BUILTIN_ACTIVE_FILENAME)
        relative = str(state.get("runtime_path") or "")
        if not relative:
            return None
        target = (self.project_root / relative).resolve()
        if not _is_within(target, self.builtin_root.resolve()):
            return None
        marker = _read_json_object(target / BUILTIN_PACK_MARKER)
        if (
            str(marker.get("digest") or "") != str(state.get("digest") or "")
            or not (target / "manifest.json").is_file()
        ):
            return None
        try:
            pack = _load_builtin_pack(target / "manifest.json")
        except (FileNotFoundError, KnowledgeIndexError):
            return None
        if pack.digest != str(state.get("digest") or ""):
            return None
        return pack

    def _active_community_pack(self) -> _BuiltinPack | None:
        state = _read_json_object(
            self.community_root / BUILTIN_ACTIVE_FILENAME
        )
        relative = str(state.get("runtime_path") or "")
        if not relative:
            return None
        target = (self.project_root / relative).resolve()
        if not _is_within(target, self.community_root.resolve()):
            return None
        marker = _read_json_object(target / BUILTIN_PACK_MARKER)
        if (
            str(marker.get("digest") or "") != str(state.get("digest") or "")
            or not (target / "manifest.json").is_file()
        ):
            return None
        try:
            pack = _load_community_pack(target / "manifest.json")
        except (FileNotFoundError, KnowledgeIndexError):
            return None
        if pack.digest != str(state.get("digest") or ""):
            return None
        return pack

    def _copy_builtin_pack(self, pack: _BuiltinPack, target: Path) -> None:
        target_parent = target.parent.resolve()
        if not _is_within(target_parent, self.builtin_root.resolve()):
            raise KnowledgeIndexError(
                "The built-in knowledge target escaped project runtime"
            )
        staging = target.parent / f".install-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            shutil.copyfile(pack.manifest_path, staging / "manifest.json")
            for entry in pack.entries:
                relative = PurePosixPath(str(entry["path"]))
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(
                    pack.root.joinpath(*relative.parts),
                    destination,
                )
            for relative_text in pack.metadata_files:
                relative = PurePosixPath(relative_text)
                shutil.copyfile(
                    pack.root.joinpath(*relative.parts),
                    staging.joinpath(*relative.parts),
                )
            marker = {
                "schema_version": 1,
                "pack_id": pack.pack_id,
                "pack_version": pack.pack_version,
                "digest": pack.digest,
                "cards": len(pack.entries),
                "metadata_files": list(pack.metadata_files),
            }
            (staging / BUILTIN_PACK_MARKER).write_text(
                json.dumps(
                    marker,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise

    def _copy_community_pack(self, pack: _BuiltinPack, target: Path) -> None:
        target_parent = target.parent.resolve()
        if not _is_within(target_parent, self.community_root.resolve()):
            raise KnowledgeIndexError(
                "The community knowledge target escaped project runtime"
            )
        staging = target.parent / f".install-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            shutil.copyfile(pack.manifest_path, staging / "manifest.json")
            for entry in pack.entries:
                relative = PurePosixPath(str(entry["path"]))
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(
                    pack.root.joinpath(*relative.parts),
                    destination,
                )
            for relative_text in pack.metadata_files:
                relative = PurePosixPath(relative_text)
                shutil.copyfile(
                    pack.root.joinpath(*relative.parts),
                    staging.joinpath(*relative.parts),
                )
            marker = {
                "schema_version": 1,
                "pack_id": pack.pack_id,
                "pack_version": pack.pack_version,
                "digest": pack.digest,
                "cards": len(pack.entries),
                "metadata_files": list(pack.metadata_files),
                "source_kind": COMMUNITY_TUTORIAL_SOURCE,
            }
            (staging / BUILTIN_PACK_MARKER).write_text(
                json.dumps(
                    marker,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise

    def _builtin_pack_result(
        self,
        pack: _BuiltinPack,
        *,
        changed: bool,
    ) -> dict[str, Any]:
        runtime_path = pack.root.relative_to(self.project_root).as_posix()
        return {
            "available": True,
            "installed": True,
            "changed": changed,
            "pack_id": pack.pack_id,
            "pack_version": pack.pack_version,
            "digest": pack.digest,
            "cards": len(pack.entries),
            "runtime_path": runtime_path,
            "fallback_reason": "",
        }

    def _builtin_pack_active_payload(
        self,
        pack: _BuiltinPack,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "pack_id": pack.pack_id,
            "pack_version": pack.pack_version,
            "digest": pack.digest,
            "cards": len(pack.entries),
            "runtime_path": pack.root.relative_to(self.project_root).as_posix(),
        }

    def refresh_due(
        self,
        source_groups: Iterable[str],
        *,
        force: bool = False,
    ) -> set[str]:
        groups = set(source_groups)
        if force:
            return groups
        now = time.time()
        due: set[str] = set()
        with closing(self._connect(read_only=True)) as connection:
            for group in groups:
                value = self._meta_value_from_connection(
                    connection,
                    f"refreshed_group:{group}",
                )
                try:
                    refreshed_at = float(value)
                except (TypeError, ValueError):
                    refreshed_at = 0.0
                if now - refreshed_at >= REFRESH_INTERVAL_SECONDS:
                    due.add(group)
        return due

    def refresh(
        self,
        source_groups: Iterable[str],
        snapshot: Mapping[str, Any],
        *,
        force: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        requested = set(source_groups)
        invalid = requested.difference(SOURCE_GROUPS)
        if invalid:
            raise KnowledgeIndexError(
                f"Unsupported local knowledge sources: {sorted(invalid)!r}"
            )
        stats: dict[str, Any] = {
            "refreshed": False,
            "refresh_reason": "explicit" if force else "due",
            "refresh_requested_groups": sorted(requested),
            "refresh_groups": [],
            "files_scanned": 0,
            "inline_records_scanned": 0,
            "documents_added": 0,
            "documents_updated": 0,
            "documents_body_changed": 0,
            "documents_metadata_changed": 0,
            "documents_removed": 0,
            "documents_unchanged": 0,
        }
        warnings: list[str] = []
        if not requested:
            return stats, warnings

        builtin_pack: Mapping[str, Any] = {}
        prepared_builtin_pack: _BuiltinPack | None = None
        community_pack: Mapping[str, Any] = {}
        prepared_community_pack: _BuiltinPack | None = None
        if "project" in requested:
            builtin_pack, builtin_warnings = self.bootstrap_builtin_pack()
            stats["builtin_pack"] = dict(builtin_pack)
            warnings.extend(builtin_warnings)
            if builtin_pack.get("installed"):
                runtime_path = (
                    self.project_root
                    / str(builtin_pack.get("runtime_path") or "")
                ).resolve()
                if not _is_within(runtime_path, self.builtin_root.resolve()):
                    raise KnowledgeIndexError(
                        "The prepared built-in knowledge pack escaped project runtime"
                    )
                prepared_builtin_pack = _load_builtin_pack(
                    runtime_path / "manifest.json"
                )
            community_pack, community_warnings = (
                self.bootstrap_community_pack()
            )
            stats["community_pack"] = dict(community_pack)
            warnings.extend(community_warnings)
            if community_pack.get("installed"):
                runtime_path = (
                    self.project_root
                    / str(community_pack.get("runtime_path") or "")
                ).resolve()
                if not _is_within(
                    runtime_path,
                    self.community_root.resolve(),
                ):
                    raise KnowledgeIndexError(
                        "The prepared community knowledge pack escaped "
                        "project runtime"
                    )
                prepared_community_pack = _load_community_pack(
                    runtime_path / "manifest.json"
                )
        current_version = str(snapshot.get("houdini_version") or "unknown")
        now = time.time()
        active_path = self.builtin_root / BUILTIN_ACTIVE_FILENAME
        active_payload_to_publish: dict[str, Any] | None = None
        community_active_path = (
            self.community_root / BUILTIN_ACTIVE_FILENAME
        )
        community_active_payload_to_publish: dict[str, Any] | None = None
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                groups = set(requested)
                if not force:
                    active_version = self._meta_value_from_connection(
                        connection,
                        "active_houdini_version",
                    )
                    for group in tuple(groups):
                        value = self._meta_value_from_connection(
                            connection,
                            f"refreshed_group:{group}",
                        )
                        try:
                            fresh = (
                                now - float(value)
                                < REFRESH_INTERVAL_SECONDS
                            )
                        except (TypeError, ValueError):
                            fresh = False
                        if group == "houdini":
                            fresh = fresh and active_version == current_version
                        if fresh:
                            groups.discard(group)
                    if prepared_builtin_pack is not None:
                        indexed_pack_json = self._meta_value_from_connection(
                            connection,
                            "builtin_pack",
                        )
                        try:
                            indexed_pack = (
                                json.loads(indexed_pack_json)
                                if indexed_pack_json
                                else {}
                            )
                        except (TypeError, ValueError, json.JSONDecodeError):
                            indexed_pack = {}
                        if (
                            not isinstance(indexed_pack, Mapping)
                            or str(indexed_pack.get("digest") or "")
                            != prepared_builtin_pack.digest
                        ):
                            groups.add("project")
                    if prepared_community_pack is not None:
                        indexed_pack_json = self._meta_value_from_connection(
                            connection,
                            "community_pack",
                        )
                        try:
                            indexed_pack = (
                                json.loads(indexed_pack_json)
                                if indexed_pack_json
                                else {}
                            )
                        except (
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ):
                            indexed_pack = {}
                        if (
                            not isinstance(indexed_pack, Mapping)
                            or str(indexed_pack.get("digest") or "")
                            != prepared_community_pack.digest
                        ):
                            groups.add("project")

                for group in sorted(groups):
                    if group == "houdini":
                        collections = [
                            (
                                f"houdini:{current_version}",
                                *self._collect_houdini_candidates(
                                    snapshot,
                                    current_version,
                                ),
                            )
                        ]
                    elif group == "project":
                        collections = [
                            (
                                "builtin",
                                self._collect_builtin_knowledge_candidates(
                                    prepared_builtin_pack,
                                ),
                                [],
                            ),
                            (
                                "community",
                                self._collect_community_tutorial_candidates(
                                    prepared_community_pack,
                                ),
                                [],
                            ),
                            (
                                "project",
                                self._collect_project_candidates(),
                                [],
                            ),
                        ]
                    else:
                        user_candidates, user_warnings = (
                            self._collect_user_candidates()
                        )
                        collections = [
                            (
                                "user",
                                user_candidates,
                                user_warnings,
                            )
                        ]
                    for collection, candidates, collection_warnings in collections:
                        warnings.extend(collection_warnings)
                        collection_stats = self._refresh_collection(
                            connection,
                            collection,
                            candidates,
                            warnings,
                        )
                        for key, value in collection_stats.items():
                            stats[key] += value
                    stats["refresh_groups"].append(group)
                    self._set_meta(
                        connection,
                        f"refreshed_collection:{collection}",
                        str(now),
                    )
                    self._set_meta(
                        connection,
                        f"refreshed_group:{group}",
                        str(now),
                    )
                    stats["refreshed"] = True
                if "project" in groups and builtin_pack.get("installed"):
                    self._set_meta(
                        connection,
                        "builtin_pack",
                        json.dumps(
                            dict(builtin_pack),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                if "project" in groups and community_pack.get("installed"):
                    self._set_meta(
                        connection,
                        "community_pack",
                        json.dumps(
                            dict(community_pack),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                if requested and not groups:
                    stats["refresh_reason"] = "not_due"
                if "houdini" in groups:
                    self._set_meta(
                        connection,
                        "active_houdini_version",
                        current_version,
                    )
                if prepared_builtin_pack is not None:
                    active_payload = self._builtin_pack_active_payload(
                        prepared_builtin_pack
                    )
                    if _read_json_object(active_path) != active_payload:
                        active_payload_to_publish = active_payload
                if prepared_community_pack is not None:
                    active_payload = self._builtin_pack_active_payload(
                        prepared_community_pack
                    )
                    if (
                        _read_json_object(community_active_path)
                        != active_payload
                    ):
                        community_active_payload_to_publish = active_payload
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if active_payload_to_publish is not None:
            _write_json_atomic(active_path, active_payload_to_publish)
        if community_active_payload_to_publish is not None:
            _write_json_atomic(
                community_active_path,
                community_active_payload_to_publish,
            )
        return stats, warnings

    def search(
        self,
        query: str,
        source_groups: Iterable[str],
        *,
        current_houdini_version: str,
        offset: int,
        limit: int,
        memory_scope: str = "",
        include_superseded: bool = False,
        document_ids: Iterable[int] | None = None,
        exact_identity: bool = False,
    ) -> dict[str, Any]:
        groups = sorted(set(source_groups))
        if not groups:
            return {"matches": [], "total": 0, "tokenizer": self._tokenizer()}
        invalid = set(groups).difference(SEARCH_SOURCE_GROUPS)
        if invalid:
            raise KnowledgeIndexError(
                f"Unsupported local knowledge sources: {sorted(invalid)!r}"
            )

        selected_document_ids = (
            None
            if document_ids is None
            else tuple(dict.fromkeys(int(value) for value in document_ids))
        )
        if selected_document_ids == ():
            return {
                "matches": [],
                "total": 0,
                "tokenizer": self._tokenizer(read_only=True),
                "timings": {"fts_seconds": 0.0},
            }
        placeholders = ",".join("?" for _value in groups)
        document_filter = ""
        document_parameters: list[Any] = []
        if selected_document_ids is not None:
            document_placeholders = ",".join(
                "?" for _value in selected_document_ids
            )
            document_filter = f" AND d.id IN ({document_placeholders})"
            document_parameters.extend(selected_document_ids)
        memory_filter = ""
        memory_parameters: list[Any] = []
        if "memory" in groups:
            memory_filter = (
                " AND (d.source_group <> 'memory' OR EXISTS ("
                "SELECT 1 FROM project_memories pm "
                "WHERE pm.document_id = d.id"
            )
            if not include_superseded:
                memory_filter += " AND pm.status = 'active'"
            if memory_scope:
                memory_filter += " AND pm.scope = ?"
                memory_parameters.append(memory_scope)
            memory_filter += "))"
        search_started = time.monotonic()
        tokenizer = self._tokenizer(read_only=True)
        search_query = "" if exact_identity else query
        query_tokens = _fts_tokens(search_query)
        use_fts = len(search_query) >= 3 and bool(query_tokens)
        version = current_houdini_version or "unknown"
        version_parameters = _version_order_parameters(version)
        if offset >= MAX_LEXICAL_RANK_CANDIDATES:
            candidate_limit = limit
            candidate_offset = offset
            page_offset = 0
        else:
            candidate_limit = min(
                MAX_LEXICAL_RANK_CANDIDATES,
                max(100, offset + limit * 4),
            )
            candidate_offset = 0
            page_offset = offset
        with closing(self._connect(read_only=True)) as connection:
            connection.row_factory = sqlite3.Row
            if use_fts:
                expression = _fts_expression(search_query, tokenizer)
                relaxed_expression = _fts_relaxed_expression(
                    search_query,
                    tokenizer,
                )
                if len(query_tokens) >= 4 and relaxed_expression:
                    expression = relaxed_expression
                where = (
                    "knowledge_fts MATCH ? "
                    f"AND d.source_group IN ({placeholders})"
                    f"{document_filter}"
                    f"{memory_filter}"
                )
                parameters: list[Any] = [
                    expression,
                    *groups,
                    *document_parameters,
                    *memory_parameters,
                ]
                count_sql = f"""
                    SELECT COUNT(DISTINCT d.id)
                    FROM knowledge_fts
                    JOIN chunks c ON c.id = knowledge_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                """
                select_sql = f"""
                    SELECT d.*, c.id AS chunk_id, c.ordinal, c.body,
                           c.content_hash AS chunk_hash,
                           MIN(knowledge_fts.rank) AS lexical_score
                    FROM knowledge_fts
                    JOIN chunks c ON c.id = knowledge_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                    GROUP BY d.id
                    ORDER BY
                        CASE WHEN d.title = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                        lexical_score,
                        CASE
                            WHEN d.houdini_version = ? THEN 0
                            WHEN ? <> '' AND (
                                d.houdini_version = ?
                                OR d.houdini_version LIKE ?
                            ) THEN 1
                            WHEN d.houdini_version IN ('', 'any', 'current') THEN 2
                            ELSE 3
                        END,
                        CASE d.verification WHEN 'verified' THEN 0 ELSE 1 END,
                        d.title COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                total = int(
                    connection.execute(count_sql, parameters).fetchone()[0]
                )
                if (
                    total == 0
                    and relaxed_expression
                    and relaxed_expression != expression
                ):
                    parameters[0] = relaxed_expression
                    total = int(
                        connection.execute(
                            count_sql,
                            parameters,
                        ).fetchone()[0]
                    )
                rows = connection.execute(
                    select_sql,
                    [
                        *parameters,
                        query,
                        *version_parameters,
                        candidate_limit,
                        candidate_offset,
                    ],
                ).fetchall()
            else:
                escaped = _escape_like(search_query)
                pattern = f"%{escaped}%"
                where = (
                    "(d.title LIKE ? ESCAPE '\\' "
                    "OR c.body LIKE ? ESCAPE '\\') "
                    f"AND d.source_group IN ({placeholders})"
                    f"{document_filter}"
                    f"{memory_filter}"
                )
                parameters = [
                    pattern,
                    pattern,
                    *groups,
                    *document_parameters,
                    *memory_parameters,
                ]
                count_sql = f"""
                    SELECT COUNT(DISTINCT d.id)
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                """
                select_sql = f"""
                    SELECT d.*, c.id AS chunk_id, c.ordinal, c.body,
                           c.content_hash AS chunk_hash,
                           MIN(
                               CASE WHEN d.title LIKE ? ESCAPE '\\'
                                    THEN -2.0 ELSE -1.0 END
                           ) AS lexical_score
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                    GROUP BY d.id
                    ORDER BY
                        CASE WHEN d.title = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                        lexical_score,
                        CASE
                            WHEN d.houdini_version = ? THEN 0
                            WHEN ? <> '' AND (
                                d.houdini_version = ?
                                OR d.houdini_version LIKE ?
                            ) THEN 1
                            WHEN d.houdini_version IN ('', 'any', 'current') THEN 2
                            ELSE 3
                        END,
                        CASE d.verification WHEN 'verified' THEN 0 ELSE 1 END,
                        d.title COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                total = int(
                    connection.execute(count_sql, parameters).fetchone()[0]
                )
                rows = connection.execute(
                    select_sql,
                    [
                        pattern,
                        *parameters,
                        query,
                        *version_parameters,
                        candidate_limit,
                        candidate_offset,
                    ],
                ).fetchall()

        rows = sorted(
            rows,
            key=lambda row: _lexical_rank_key(row, query, version),
        )
        matches: list[dict[str, Any]] = []
        for row in rows:
            try:
                attributes = json.loads(row["attributes_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attributes = {}
            metadata = dict(attributes) if isinstance(attributes, Mapping) else {}
            version_status = _houdini_version_status(
                str(row["houdini_version"]),
                version,
            )
            metadata.update(
                {
                    "path": row["source_path"],
                    "url": row["url"],
                    "author": row["author"],
                    "accessed_at": row["accessed_at"],
                    "houdini_version": row["houdini_version"],
                    "current_houdini_version": version,
                    "current_version_match": (
                        bool(row["houdini_version"])
                        and _houdini_versions_match(
                            str(row["houdini_version"]),
                            version,
                        )
                    ),
                    "license": row["license"],
                    "verification": row["verification"],
                    "evidence": row["evidence"],
                    "sha256": row["sha256"],
                    "document_id": int(row["id"]),
                    "chunk_id": int(row["chunk_id"]),
                    "source_key": row["source_key"],
                    "collection": row["collection"],
                    "content_hash": row["chunk_hash"],
                    "chunk_ordinal": int(row["ordinal"]),
                    "lexical_score": float(row["lexical_score"]),
                }
            )
            if version_status != "match":
                metadata["current_version_status"] = version_status
            matches.append(
                {
                    "source": row["source"],
                    "title": row["title"],
                    "snippet": _matching_snippet(row["body"], query),
                    "metadata": metadata,
                }
            )
        raw_match_count = len(matches)
        matches = _fold_node_catalog_matches(matches)
        total = max(0, total - (raw_match_count - len(matches)))
        matches = matches[page_offset : page_offset + limit]
        return {
            "matches": matches,
            "total": total,
            "tokenizer": tokenizer,
            "timings": {
                "fts_seconds": round(
                    max(0.0, time.monotonic() - search_started),
                    6,
                )
            },
        }

    def _initialize_database(self) -> None:
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    collection TEXT NOT NULL,
                    source_group TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    houdini_version TEXT NOT NULL,
                    license TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_sha256 TEXT NOT NULL,
                    stat_size INTEGER NOT NULL,
                    stat_mtime_ns INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL,
                    attributes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_collection
                    ON documents(collection);
                CREATE INDEX IF NOT EXISTS idx_documents_source_group
                    ON documents(source_group);
                CREATE INDEX IF NOT EXISTS idx_documents_houdini_version
                    ON documents(houdini_version);
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL
                        REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(document_id, ordinal)
                );
                """
            )
            chunk_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(chunks)")
            }
            if "content_hash" not in chunk_columns:
                connection.execute(
                    "ALTER TABLE chunks ADD COLUMN "
                    "content_hash TEXT NOT NULL DEFAULT ''"
                )
            for chunk_id, body in connection.execute(
                "SELECT id, body FROM chunks WHERE content_hash = ''"
            ).fetchall():
                connection.execute(
                    "UPDATE chunks SET content_hash = ? WHERE id = ?",
                    (
                        hashlib.sha256(str(body).encode("utf-8")).hexdigest(),
                        int(chunk_id),
                    ),
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    chunk_id INTEGER NOT NULL
                        REFERENCES chunks(id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    normalized INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(chunk_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chunk_vectors_model
                    ON chunk_vectors(model_id, dim);
                CREATE TABLE IF NOT EXISTS project_memories (
                    id TEXT PRIMARY KEY,
                    document_id INTEGER UNIQUE
                        REFERENCES documents(id) ON DELETE SET NULL,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    superseded_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_thread_id TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_memories_scope_status
                    ON project_memories(scope, status);
                """
            )
            existing_fts = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'knowledge_fts'"
            ).fetchone()
            if existing_fts is None:
                tokenizer = "trigram"
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE knowledge_fts "
                        "USING fts5(title, body, tokenize='trigram')"
                    )
                except sqlite3.OperationalError as trigram_error:
                    tokenizer = "unicode61"
                    try:
                        connection.execute(
                            "CREATE VIRTUAL TABLE knowledge_fts "
                            "USING fts5(title, body, tokenize='unicode61')"
                        )
                    except sqlite3.OperationalError as fts_error:
                        raise KnowledgeIndexError(
                            "Python sqlite3 was built without usable FTS5 support"
                        ) from fts_error
                    if "tokenizer" not in str(trigram_error).casefold():
                        tokenizer = "unicode61"
                self._set_meta(connection, "fts_tokenizer", tokenizer)
            elif not self._meta_value_from_connection(
                connection,
                "fts_tokenizer",
            ):
                self._set_meta(connection, "fts_tokenizer", "trigram")
            self._set_meta(connection, "schema_version", SCHEMA_VERSION)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            target = self.database_path.as_uri() + "?mode=ro"
            if not Path(str(self.database_path) + "-wal").exists():
                target += "&immutable=1"
        else:
            target = str(self.database_path)
        connection = sqlite3.connect(
            target,
            timeout=10.0,
            isolation_level=None,
            uri=read_only,
        )
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _tokenizer(self, *, read_only: bool = False) -> str:
        return (
            self._meta_value("fts_tokenizer", read_only=read_only)
            or "trigram"
        )

    def _meta_value(self, key: str, *, read_only: bool = False) -> str:
        with closing(self._connect(read_only=read_only)) as connection:
            return self._meta_value_from_connection(connection, key)

    @staticmethod
    def _meta_value_from_connection(
        connection: sqlite3.Connection,
        key: str,
    ) -> str:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row[0]) if row else ""

    @staticmethod
    def _set_meta(
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _collect_houdini_candidates(
        self,
        snapshot: Mapping[str, Any],
        current_version: str,
    ) -> tuple[list[_Candidate], list[str]]:
        collection = f"houdini:{current_version}"
        candidates: list[_Candidate] = []
        for record in snapshot.get("catalog", ()):
            if not isinstance(record, Mapping):
                continue
            category = str(record.get("category", ""))
            name = str(record.get("name", ""))
            if not category or not name:
                continue
            title = f"{category}::{name}"
            content = json.dumps(record, ensure_ascii=False, sort_keys=True)
            candidates.append(
                _Candidate(
                    collection=collection,
                    source_group="houdini",
                    source="houdini_node_catalog",
                    source_key=f"{collection}:catalog:{title}",
                    title=title,
                    source_path=title,
                    url=f"houdini-node://{category}/{name}",
                    author="SideFX",
                    houdini_version=current_version,
                    license_name="Houdini installation license",
                    verification="verified",
                    evidence=(
                        "Live node-type catalog read from the current Houdini "
                        f"{current_version} session through hou"
                    ),
                    attributes=dict(record),
                    inline_text=content,
                    stat_size=len(content.encode("utf-8")),
                )
            )

        warnings: list[str] = []
        expanded = str(snapshot.get("houdini_help_root") or "")
        if expanded:
            help_root = Path(expanded)
            try:
                resolved_help_root = help_root.resolve()
            except OSError:
                resolved_help_root = help_root
            if resolved_help_root.is_dir():
                for path in _walk_files(resolved_help_root):
                    relative = path.relative_to(resolved_help_root).as_posix()
                    folded_relative = relative.casefold()
                    if any(
                        folded_relative.startswith(prefix)
                        for prefix in HOUDINI_HELP_EXCLUDED_PREFIXES
                    ):
                        continue
                    suffix = path.suffix.casefold()
                    if suffix in HOUDINI_TEXT_SUFFIXES:
                        candidate = self._path_candidate(
                            collection=collection,
                            source_group="houdini",
                            source="houdini_help",
                            root=resolved_help_root,
                            path=path,
                            title_prefix="",
                            url_prefix="houdini-help://",
                            author="SideFX",
                            houdini_version=current_version,
                            license_name="SideFX Houdini documentation terms",
                            evidence=(
                                "Installed Houdini help; unverified against live "
                                "scene behavior"
                            ),
                        )
                        if candidate is not None:
                            candidates.append(candidate)
                    elif (
                        suffix == ".zip"
                        and path.name.casefold() in HOUDINI_HELP_ARCHIVES
                    ):
                        archive_candidates, archive_warnings = (
                            self._archive_candidates(
                                collection=collection,
                                root=resolved_help_root,
                                archive_path=path,
                                houdini_version=current_version,
                            )
                        )
                        candidates.extend(archive_candidates)
                        warnings.extend(archive_warnings)
            else:
                warnings.append(
                    "Houdini help root was unavailable; catalog indexing "
                    "continued without local help files"
                )
        return candidates, warnings

    def _collect_project_candidates(self) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        skills_root = self.project_root / ".agents" / "skills"
        if skills_root.is_dir():
            for skill_root in sorted(skills_root.iterdir(), key=lambda p: p.name):
                if not skill_root.is_dir():
                    continue
                skill_entry = skill_root / "SKILL.md"
                if skill_entry.is_file():
                    candidate = self._path_candidate(
                        collection="project",
                        source_group="project",
                        source="project_skill",
                        root=skills_root,
                        path=skill_entry,
                        title_prefix="",
                        url_prefix="project://.agents/skills/",
                        author="Big-Chicken contributors",
                        houdini_version="any",
                        license_name="Apache-2.0",
                        evidence=(
                            "Published project Skill; unverified until checked "
                            "in a real Houdini session"
                        ),
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                references = skill_root / "references"
                if references.is_dir():
                    for path in _walk_files(references):
                        if path.suffix.casefold() not in PROJECT_TEXT_SUFFIXES:
                            continue
                        candidate = self._path_candidate(
                            collection="project",
                            source_group="project",
                            source="project_skill",
                            root=skills_root,
                            path=path,
                            title_prefix="",
                            url_prefix="project://.agents/skills/",
                            author="Big-Chicken contributors",
                            houdini_version="any",
                            license_name="Apache-2.0",
                            evidence=(
                                "Published project Skill reference; unverified "
                                "until checked in a real Houdini session"
                            ),
                        )
                        if candidate is not None:
                            candidates.append(candidate)

        docs_root = self.project_root / "docs"
        for name in PROJECT_HELP_DOCUMENTS:
            path = docs_root / name
            if not path.is_file():
                continue
            candidate = self._path_candidate(
                collection="project",
                source_group="project",
                source="project_docs",
                root=docs_root,
                path=path,
                title_prefix="",
                url_prefix="project://docs/",
                author="Big-Chicken contributors",
                houdini_version="any",
                license_name="Apache-2.0",
                evidence=(
                    "Current published project documentation; unverified "
                    "until checked in a real Houdini session"
                ),
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _collect_builtin_knowledge_candidates(
        self,
        pack: _BuiltinPack | None = None,
    ) -> list[_Candidate]:
        pack = pack or self._active_builtin_pack()
        if pack is None:
            return []
        candidates: list[_Candidate] = []
        publisher = str(
            pack.manifest.get("publisher") or "Big-Chicken contributors"
        )
        license_name = str(pack.manifest.get("card_license") or "Apache-2.0")
        for entry in pack.entries:
            relative_path = str(entry.get("path") or "")
            identifier = str(entry.get("id") or "")
            title = str(entry.get("title") or "")
            upstream_url = str(entry.get("url") or "")
            relative = PurePosixPath(relative_path)
            path = pack.root.joinpath(*relative.parts)
            candidate = self._path_candidate(
                collection="builtin",
                source_group="project",
                source=BUILTIN_WORKFLOW_SOURCE,
                root=pack.root,
                path=path,
                title_prefix="",
                url_prefix=(
                    "project://.runtime/knowledge/builtin/"
                    f"{pack.pack_id}/{pack.pack_version}/"
                ),
                author=publisher,
                houdini_version=str(entry.get("houdini_version") or "any"),
                license_name=license_name,
                evidence=(
                    "Versioned built-in workflow card linked to an official "
                    "SideFX source; verify version-sensitive behavior in the "
                    "active Houdini session."
                ),
            )
            if candidate is None:
                continue
            aliases = entry.get("aliases")
            if not isinstance(aliases, list):
                aliases = []
            official_urls = entry.get("official_urls")
            if not isinstance(official_urls, list):
                official_urls = [upstream_url]
            candidates.append(
                replace(
                    candidate,
                    source_key=f"builtin:{pack.pack_id}:{identifier}",
                    title=title,
                    url=upstream_url,
                    attributes={
                        "source_kind": BUILTIN_WORKFLOW_SOURCE,
                        "knowledge_pack": pack.pack_id,
                        "pack_version": pack.pack_version,
                        "pack_digest": pack.digest,
                        "card_id": identifier,
                        "canonical_id": str(
                            entry.get("canonical_id") or identifier
                        ),
                        "aliases": [
                            str(value)
                            for value in aliases
                            if isinstance(value, str) and value.strip()
                        ][:32],
                        "topic": str(entry.get("topic") or ""),
                        "summary": str(entry.get("summary") or ""),
                        "upstream_author": "SideFX",
                        "upstream_url": upstream_url,
                        "official_urls": [
                            str(value)
                            for value in official_urls
                            if isinstance(value, str) and value.strip()
                        ][:32],
                    },
                )
            )
        return candidates

    def _collect_community_tutorial_candidates(
        self,
        pack: _BuiltinPack | None = None,
    ) -> list[_Candidate]:
        pack = pack or self._active_community_pack()
        if pack is None:
            return []
        candidates: list[_Candidate] = []
        license_name = str(
            pack.manifest.get("card_license") or "Apache-2.0"
        )
        for entry in pack.entries:
            relative_path = str(entry.get("path") or "")
            identifier = str(entry.get("id") or "")
            title = str(entry.get("title") or "")
            upstream_url = str(entry.get("url") or "")
            relative = PurePosixPath(relative_path)
            path = pack.root.joinpath(*relative.parts)
            candidate = self._path_candidate(
                collection="community",
                source_group="project",
                source=COMMUNITY_TUTORIAL_SOURCE,
                root=pack.root,
                path=path,
                title_prefix="",
                url_prefix=(
                    "project://.runtime/knowledge/community/"
                    f"{pack.pack_id}/{pack.pack_version}/"
                ),
                author=str(entry.get("author") or "Community tutorial authors"),
                houdini_version=str(
                    entry.get("houdini_version") or "unknown"
                ),
                license_name=license_name,
                evidence=(
                    "Original project-authored summary of attributed community "
                    "tutorials. Upstream behavior has not been reproduced in "
                    "the active Houdini build."
                ),
            )
            if candidate is None:
                continue
            aliases = entry.get("aliases")
            if not isinstance(aliases, list):
                aliases = []
            source_records = entry.get("source_records")
            if not isinstance(source_records, list):
                source_records = []
            candidates.append(
                replace(
                    candidate,
                    source_key=f"community:{pack.pack_id}:{identifier}",
                    title=title,
                    url=upstream_url,
                    verification="community_unverified",
                    attributes={
                        "source_kind": COMMUNITY_TUTORIAL_SOURCE,
                        "knowledge_pack": pack.pack_id,
                        "pack_version": pack.pack_version,
                        "pack_digest": pack.digest,
                        "card_id": identifier,
                        "canonical_id": str(
                            entry.get("canonical_id")
                            or f"community:{identifier}"
                        ),
                        "aliases": [
                            str(value)
                            for value in aliases
                            if isinstance(value, str) and value.strip()
                        ][:32],
                        "topic": str(entry.get("topic") or ""),
                        "summary": str(entry.get("summary") or ""),
                        "upstream_author": str(
                            entry.get("author") or ""
                        ),
                        "upstream_url": upstream_url,
                        "source_ids": [
                            str(value)
                            for value in entry.get("source_ids", ())
                            if isinstance(value, str) and value.strip()
                        ][:32],
                        "community_sources": [
                            dict(value)
                            for value in source_records
                            if isinstance(value, Mapping)
                        ][:32],
                    },
                )
            )
        return candidates

    @staticmethod
    def _archive_candidates(
        *,
        collection: str,
        root: Path,
        archive_path: Path,
        houdini_version: str,
    ) -> tuple[list[_Candidate], list[str]]:
        warnings: list[str] = []
        candidates: list[_Candidate] = []
        try:
            resolved_root = root.resolve()
            resolved_archive = archive_path.resolve()
            archive_relative = resolved_archive.relative_to(
                resolved_root
            ).as_posix()
            archive_stat = resolved_archive.stat()
            with zipfile.ZipFile(resolved_archive) as archive:
                for info in sorted(
                    archive.infolist(),
                    key=lambda value: value.filename.casefold(),
                ):
                    member = info.filename.replace("\\", "/").lstrip("/")
                    folded_member = member.casefold()
                    if (
                        info.is_dir()
                        or not folded_member.endswith(".txt")
                        or any(
                            folded_member.startswith(prefix)
                            for prefix in HOUDINI_HELP_EXCLUDED_PREFIXES
                        )
                    ):
                        continue
                    candidates.append(
                        _Candidate(
                            collection=collection,
                            source_group="houdini",
                            source="houdini_help_archive",
                            source_key=(
                                f"{collection}:houdini_help_archive:"
                                f"{archive_relative}!/{member}"
                            ),
                            title=f"{archive_path.stem}/{member}",
                            source_path=f"{archive_relative}!/{member}",
                            url=(
                                f"houdini-help://{archive_path.stem}/{member}"
                            ),
                            author="SideFX",
                            houdini_version=houdini_version,
                            license_name=(
                                "SideFX Houdini documentation terms"
                            ),
                            verification="unverified",
                            evidence=(
                                "Installed Houdini help archive matching the "
                                "active Houdini version; unverified against "
                                "live scene behavior"
                            ),
                            attributes={
                                "archive": archive_relative,
                                "member": member,
                            },
                            path=resolved_archive,
                            archive_member=member,
                            stat_size=int(archive_stat.st_size),
                            stat_mtime_ns=int(archive_stat.st_mtime_ns),
                        )
                    )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            warnings.append(
                f"{archive_path.name}: installed help archive was skipped ({exc})"
            )
        return candidates, warnings

    def _collect_user_candidates(
        self,
    ) -> tuple[list[_Candidate], list[str]]:
        candidates: list[_Candidate] = []
        warnings: list[str] = []
        if not self.sources_root.is_dir():
            return candidates, warnings
        paths = [
            path
            for path in _walk_files(self.sources_root)
            if path.suffix.casefold() in USER_INGEST_SUFFIXES
        ]
        media_paths = [
            path for path in paths if path.suffix.casefold() in MEDIA_SUFFIXES
        ]
        claimed_sidecars = {
            transcript.resolve()
            for media_path in media_paths
            for transcript in [_first_media_sidecar(media_path)]
            if transcript is not None
        }
        ordered_paths = media_paths + [
            path
            for path in paths
            if (
                path.suffix.casefold() not in MEDIA_SUFFIXES
                and _resolved_or_self(path) not in claimed_sidecars
            )
        ]
        for path in ordered_paths:
            try:
                relative = path.resolve().relative_to(
                    self.sources_root.resolve()
                ).as_posix()
            except (OSError, ValueError):
                warnings.append(
                    f"{path.name}: managed source escaped its project directory"
                )
                continue
            metadata = _managed_source_metadata(path)
            try:
                batch = normalize_selected_file(
                    path,
                    source_id=relative,
                    source_kind=str(metadata.get("source_kind") or ""),
                )
            except SourceAdapterError as exc:
                warnings.append(f"{relative}: {exc}")
                continue
            warnings.extend(
                f"{relative}: {warning}" for warning in batch.warnings
            )
            if batch.status != "ready":
                continue
            transcript = (
                _first_media_sidecar(path)
                if path.suffix.casefold() in MEDIA_SUFFIXES
                else None
            )
            stat_paths = [path]
            if transcript is not None:
                stat_paths.append(transcript)
            metadata_path = Path(str(path) + ".metadata.json")
            if metadata_path.is_file():
                stat_paths.append(metadata_path)
            stat_size, stat_mtime_ns = _combined_stat(stat_paths)
            for record in batch.records:
                candidates.append(
                    self._normalized_user_candidate(
                        record,
                        collection="user",
                        path=path,
                        source_path=relative,
                        title=str(
                            metadata.get("original_name") or relative
                        ),
                        stat_size=stat_size,
                        stat_mtime_ns=stat_mtime_ns,
                        additional_metadata=metadata,
                    )
                )
        return candidates, warnings

    @staticmethod
    def _normalized_user_candidate(
        record: NormalizedSource,
        *,
        collection: str = "explicit_user",
        path: Path | None = None,
        source_path: str = "",
        title: str = "",
        stat_size: int = 0,
        stat_mtime_ns: int = 0,
        additional_metadata: Mapping[str, Any] | None = None,
    ) -> _Candidate:
        text = str(record.text)
        if (
            not text.strip()
            or len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES
        ):
            raise KnowledgeIndexError(
                "Explicit normalized source body is empty or too large"
            )
        source_key = str(record.source_key).strip()
        if not source_key or len(source_key) > 1024:
            raise KnowledgeIndexError(
                "Explicit normalized source key is invalid"
            )
        expected_prefix = (
            "thread_export:"
            if record.source_kind == THREAD_EXPORT_SOURCE
            else f"user:{record.source_kind}:"
        )
        if not source_key.startswith(expected_prefix):
            raise KnowledgeIndexError(
                "Explicit normalized source key does not match its source kind"
            )
        effective_title = str(title or record.title).strip()
        if not effective_title or len(effective_title) > 512:
            raise KnowledgeIndexError(
                "Explicit normalized source title is invalid"
            )
        provenance = dict(record.provenance)
        metadata = {
            **dict(additional_metadata or {}),
            **dict(record.metadata),
        }
        attributes = {
            **metadata,
            "source_kind": record.source_kind,
            "content_hash": record.content_hash,
            "provenance": provenance,
        }
        effective_source_path = (
            source_path
            or str(provenance.get("selected_path") or "")
            or source_key
        )
        return _Candidate(
            collection=collection,
            source_group="user",
            source=record.source_kind,
            source_key=source_key,
            title=effective_title,
            source_path=effective_source_path,
            url=str(
                metadata.get("url")
                or (
                    "project://.runtime/knowledge/sources/"
                    f"{source_path}"
                    if source_path
                    else ""
                )
            ),
            author=str(metadata.get("author") or "User-supplied source"),
            houdini_version=str(metadata.get("houdini_version") or "any"),
            license_name=str(metadata.get("license") or "project-private"),
            verification=record.verification,
            evidence=str(
                metadata.get("evidence")
                or "Explicit user-supplied source; not independently verified"
            ),
            attributes=attributes,
            path=path,
            inline_text=text,
            stat_size=int(stat_size),
            stat_mtime_ns=int(stat_mtime_ns),
        )

    @staticmethod
    def _candidate_metadata(candidate: _Candidate) -> dict[str, Any]:
        attributes = dict(candidate.attributes)
        return {
            "url": candidate.url,
            "author": candidate.author,
            "accessed_at": str(
                attributes.get("accessed_at")
                or attributes.get("updated_at")
                or ""
            ),
            "houdini_version": candidate.houdini_version,
            "license": candidate.license_name,
            "verification": candidate.verification,
            "evidence": candidate.evidence,
            "attributes": attributes,
        }

    def _path_candidate(
        self,
        *,
        collection: str,
        source_group: str,
        source: str,
        root: Path,
        path: Path,
        title_prefix: str,
        url_prefix: str,
        author: str,
        houdini_version: str,
        license_name: str,
        evidence: str,
        allow_sidecar: bool = False,
    ) -> _Candidate | None:
        try:
            resolved_root = root.resolve()
            resolved_path = path.resolve()
            relative = resolved_path.relative_to(resolved_root).as_posix()
            stat = resolved_path.stat()
        except (OSError, ValueError):
            return None
        if not resolved_path.is_file():
            return None
        sidecar_path = (
            Path(str(resolved_path) + ".metadata.json")
            if allow_sidecar
            else None
        )
        stat_size = int(stat.st_size)
        stat_mtime_ns = int(stat.st_mtime_ns)
        if sidecar_path is not None and sidecar_path.is_file():
            try:
                sidecar_stat = sidecar_path.stat()
                stat_size += int(sidecar_stat.st_size)
                stat_mtime_ns = max(
                    stat_mtime_ns,
                    int(sidecar_stat.st_mtime_ns),
                )
            except OSError:
                pass
        return _Candidate(
            collection=collection,
            source_group=source_group,
            source=source,
            source_key=f"{collection}:{source}:{relative}",
            title=f"{title_prefix}{relative}",
            source_path=relative,
            url=f"{url_prefix}{relative}",
            author=author,
            houdini_version=houdini_version,
            license_name=license_name,
            verification="unverified",
            evidence=evidence,
            attributes={},
            path=resolved_path,
            sidecar_path=sidecar_path,
            stat_size=stat_size,
            stat_mtime_ns=stat_mtime_ns,
        )

    def _refresh_collection(
        self,
        connection: sqlite3.Connection,
        collection: str,
        candidates: list[_Candidate],
        warnings: list[str],
    ) -> dict[str, int]:
        stats = {
            "files_scanned": 0,
            "inline_records_scanned": 0,
            "documents_added": 0,
            "documents_updated": 0,
            "documents_body_changed": 0,
            "documents_metadata_changed": 0,
            "documents_removed": 0,
            "documents_unchanged": 0,
        }
        existing_rows = {
            str(row[1]): row
            for row in connection.execute(
                "SELECT id, source_key, sha256, metadata_sha256, "
                "stat_size, stat_mtime_ns "
                "FROM documents WHERE collection = ?",
                (collection,),
            ).fetchall()
        }
        present_keys = {candidate.source_key for candidate in candidates}
        archive_cache: dict[Path, zipfile.ZipFile] = {}

        try:
            for source_key, row in existing_rows.items():
                if source_key not in present_keys:
                    self._delete_document(connection, int(row[0]))
                    stats["documents_removed"] += 1

            for candidate in candidates:
                existing = existing_rows.get(candidate.source_key)
                if candidate.inline_text is not None:
                    stats["inline_records_scanned"] += 1
                if candidate.path is not None:
                    stats["files_scanned"] += 1
                    if (
                        existing is not None
                        and int(existing[4]) == candidate.stat_size
                        and int(existing[5]) == candidate.stat_mtime_ns
                    ):
                        stats["documents_unchanged"] += 1
                        continue

                text, metadata, read_warnings = self._read_candidate(
                    candidate,
                    archive_cache=archive_cache,
                )
                warnings.extend(read_warnings)
                if text is None:
                    continue
                text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                metadata_json = _stable_metadata_json(metadata)
                metadata_sha256 = hashlib.sha256(
                    metadata_json.encode("utf-8")
                ).hexdigest()
                if existing is not None and str(existing[2]) == text_sha256:
                    if str(existing[3]) == metadata_sha256:
                        connection.execute(
                            "UPDATE documents SET stat_size = ?, "
                            "stat_mtime_ns = ? WHERE id = ?",
                            (
                                candidate.stat_size,
                                candidate.stat_mtime_ns,
                                int(existing[0]),
                            ),
                        )
                        stats["documents_unchanged"] += 1
                    else:
                        self._update_document_metadata(
                            connection,
                            candidate,
                            metadata_sha256,
                            metadata,
                            int(existing[0]),
                        )
                        stats["documents_updated"] += 1
                        stats["documents_metadata_changed"] += 1
                    continue

                if not text.strip():
                    if existing is not None:
                        self._delete_document(connection, int(existing[0]))
                        stats["documents_removed"] += 1
                    continue
                self._replace_document(
                    connection,
                    candidate,
                    text,
                    text_sha256,
                    metadata_sha256,
                    metadata,
                    int(existing[0]) if existing is not None else None,
                )
                if existing is None:
                    stats["documents_added"] += 1
                else:
                    stats["documents_updated"] += 1
                    stats["documents_body_changed"] += 1
        finally:
            for archive in archive_cache.values():
                archive.close()
        return stats

    @staticmethod
    def _update_document_metadata(
        connection: sqlite3.Connection,
        candidate: _Candidate,
        metadata_sha256: str,
        metadata: Mapping[str, Any],
        document_id: int,
    ) -> None:
        connection.execute(
            """
            UPDATE documents
            SET url = ?, author = ?, accessed_at = ?, houdini_version = ?,
                license = ?, verification = ?, evidence = ?,
                metadata_sha256 = ?, stat_size = ?, stat_mtime_ns = ?,
                indexed_at = ?, attributes_json = ?
            WHERE id = ?
            """,
            (
                str(metadata.get("url", candidate.url)),
                str(metadata.get("author", candidate.author)),
                str(metadata.get("accessed_at", _utc_now())),
                str(
                    metadata.get(
                        "houdini_version",
                        candidate.houdini_version,
                    )
                ),
                str(metadata.get("license", candidate.license_name)),
                str(metadata.get("verification", candidate.verification)),
                str(metadata.get("evidence", candidate.evidence)),
                metadata_sha256,
                candidate.stat_size,
                candidate.stat_mtime_ns,
                _utc_now(),
                json.dumps(
                    metadata.get("attributes", candidate.attributes),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                document_id,
            ),
        )

    def _read_candidate(
        self,
        candidate: _Candidate,
        *,
        archive_cache: dict[Path, zipfile.ZipFile] | None = None,
    ) -> tuple[str | None, dict[str, Any], list[str]]:
        warnings: list[str] = []
        metadata = self._candidate_metadata(candidate)
        if candidate.sidecar_path is not None and candidate.sidecar_path.is_file():
            try:
                sidecar_value = json.loads(
                    candidate.sidecar_path.read_text(
                        encoding="utf-8",
                        errors="strict",
                    )
                )
                if isinstance(sidecar_value, Mapping):
                    for key in (
                        "url",
                        "author",
                        "accessed_at",
                        "houdini_version",
                        "license",
                        "evidence",
                    ):
                        value = sidecar_value.get(key)
                        if value is not None:
                            metadata[key] = _bounded(str(value), 2048)
                    metadata["verification"] = "unverified"
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(
                    f"{candidate.title}: metadata sidecar was ignored ({exc})"
                )

        if candidate.inline_text is not None:
            return _normalize_text(candidate.inline_text), metadata, warnings
        if candidate.path is None:
            return None, metadata, warnings
        if candidate.archive_member:
            text, archive_warning = _read_zip_text(
                candidate.path,
                candidate.archive_member,
                archive_cache=archive_cache,
            )
            if archive_warning:
                warnings.append(f"{candidate.title}: {archive_warning}")
            if text is None:
                return None, metadata, warnings
            return _normalize_text(text), metadata, warnings
        try:
            raw_size = candidate.path.stat().st_size
        except OSError as exc:
            warnings.append(f"{candidate.title}: source could not be read ({exc})")
            return None, metadata, warnings
        if raw_size > MAX_DOCUMENT_BYTES:
            warnings.append(
                f"{candidate.title}: source exceeded the {MAX_DOCUMENT_BYTES} "
                "byte local-index limit and was skipped"
            )
            return None, metadata, warnings

        suffix = candidate.path.suffix.casefold()
        if suffix == ".pdf":
            text, pdf_warning = _read_optional_pdf(candidate.path)
            if pdf_warning:
                warnings.append(f"{candidate.title}: {pdf_warning}")
            if text is None:
                return None, metadata, warnings
        else:
            try:
                text = candidate.path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            except OSError as exc:
                warnings.append(
                    f"{candidate.title}: source could not be read ({exc})"
                )
                return None, metadata, warnings
        if suffix in {".html", ".htm"}:
            text = _html_to_text(text)
        elif suffix in {".srt", ".vtt"}:
            text = _captions_to_text(text)
        return _normalize_text(text), metadata, warnings

    def _replace_document(
        self,
        connection: sqlite3.Connection,
        candidate: _Candidate,
        text: str,
        text_sha256: str,
        metadata_sha256: str,
        metadata: Mapping[str, Any],
        existing_id: int | None,
    ) -> None:
        values = (
            candidate.collection,
            candidate.source_group,
            candidate.source,
            candidate.source_key,
            candidate.title,
            candidate.source_path,
            str(metadata.get("url", candidate.url)),
            str(metadata.get("author", candidate.author)),
            str(metadata.get("accessed_at", _utc_now())),
            str(metadata.get("houdini_version", candidate.houdini_version)),
            str(metadata.get("license", candidate.license_name)),
            str(metadata.get("verification", candidate.verification)),
            str(metadata.get("evidence", candidate.evidence)),
            text_sha256,
            metadata_sha256,
            candidate.stat_size,
            candidate.stat_mtime_ns,
            _utc_now(),
            json.dumps(
                metadata.get("attributes", candidate.attributes),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        if existing_id is None:
            cursor = connection.execute(
                """
                INSERT INTO documents(
                    collection, source_group, source, source_key, title,
                    source_path, url, author, accessed_at, houdini_version,
                    license, verification, evidence, sha256, metadata_sha256,
                    stat_size, stat_mtime_ns, indexed_at, attributes_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            document_id = int(cursor.lastrowid)
        else:
            connection.execute(
                """
                UPDATE documents
                SET collection = ?, source_group = ?, source = ?,
                    source_key = ?, title = ?, source_path = ?, url = ?,
                    author = ?, accessed_at = ?, houdini_version = ?,
                    license = ?, verification = ?, evidence = ?, sha256 = ?,
                    metadata_sha256 = ?, stat_size = ?, stat_mtime_ns = ?,
                    indexed_at = ?, attributes_json = ?
                WHERE id = ?
                """,
                (*values, existing_id),
            )
            document_id = existing_id
        self._sync_chunks(
            connection,
            document_id=document_id,
            title=candidate.title,
            bodies=_chunk_text(text),
        )

    @staticmethod
    def _sync_chunks(
        connection: sqlite3.Connection,
        *,
        document_id: int,
        title: str,
        bodies: list[str],
    ) -> None:
        existing = {
            int(row[1]): (int(row[0]), str(row[2]), str(row[3]))
            for row in connection.execute(
                "SELECT id, ordinal, body, content_hash "
                "FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchall()
        }
        for ordinal, body in enumerate(bodies):
            content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            prior = existing.pop(ordinal, None)
            if prior is not None and prior[1] == body and prior[2] == content_hash:
                connection.execute(
                    "DELETE FROM knowledge_fts WHERE rowid = ?",
                    (prior[0],),
                )
                connection.execute(
                    "INSERT INTO knowledge_fts(rowid, title, body) "
                    "VALUES(?, ?, ?)",
                    (prior[0], title, body),
                )
                continue
            if prior is None:
                cursor = connection.execute(
                    "INSERT INTO chunks(document_id, ordinal, body, content_hash) "
                    "VALUES(?, ?, ?, ?)",
                    (document_id, ordinal, body, content_hash),
                )
                chunk_id = int(cursor.lastrowid)
            else:
                chunk_id = prior[0]
                connection.execute(
                    "DELETE FROM knowledge_fts WHERE rowid = ?",
                    (chunk_id,),
                )
                connection.execute(
                    "DELETE FROM chunk_vectors WHERE chunk_id = ?",
                    (chunk_id,),
                )
                connection.execute(
                    "UPDATE chunks SET body = ?, content_hash = ? WHERE id = ?",
                    (body, content_hash, chunk_id),
                )
            connection.execute(
                "INSERT INTO knowledge_fts(rowid, title, body) VALUES(?, ?, ?)",
                (chunk_id, title, body),
            )
        for chunk_id, _body, _content_hash in existing.values():
            connection.execute(
                "DELETE FROM knowledge_fts WHERE rowid = ?",
                (chunk_id,),
            )
            connection.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))

    @staticmethod
    def _delete_document(
        connection: sqlite3.Connection,
        document_id: int,
    ) -> None:
        chunk_ids = connection.execute(
            "SELECT id FROM chunks WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        connection.executemany(
            "DELETE FROM knowledge_fts WHERE rowid = ?",
            chunk_ids,
        )
        connection.execute(
            "DELETE FROM documents WHERE id = ?",
            (document_id,),
        )


def builtin_pack_status(
    project_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Inspect the installed runtime pack without creating or changing files."""

    root = Path(project_root).resolve()
    builtin_root = root / ".runtime" / "knowledge" / BUILTIN_KNOWLEDGE_DIRECTORY
    state = _read_json_object(builtin_root / BUILTIN_ACTIVE_FILENAME)
    relative = str(state.get("runtime_path") or "")
    if not relative:
        return {
            "installed": False,
            "pack_id": "",
            "pack_version": "",
            "digest": "",
            "cards": 0,
            "runtime_path": "",
            "fallback_reason": "BUILTIN_PACK_NOT_BOOTSTRAPPED",
        }
    target = (root / relative).resolve()
    if not _is_within(target, builtin_root.resolve()):
        return {
            "installed": False,
            "pack_id": "",
            "pack_version": "",
            "digest": "",
            "cards": 0,
            "runtime_path": "",
            "fallback_reason": "BUILTIN_PACK_PATH_INVALID",
        }
    marker = _read_json_object(target / BUILTIN_PACK_MARKER)
    if str(marker.get("digest") or "") != str(state.get("digest") or ""):
        return {
            "installed": False,
            "pack_id": str(state.get("pack_id") or ""),
            "pack_version": str(state.get("pack_version") or ""),
            "digest": str(state.get("digest") or ""),
            "cards": int(state.get("cards") or 0),
            "runtime_path": relative,
            "fallback_reason": "BUILTIN_PACK_MARKER_INVALID",
        }
    return {
        "installed": True,
        "pack_id": str(state.get("pack_id") or ""),
        "pack_version": str(state.get("pack_version") or ""),
        "digest": str(state.get("digest") or ""),
        "cards": int(state.get("cards") or 0),
        "runtime_path": relative,
        "fallback_reason": "",
    }


def _load_builtin_pack(manifest_path: Path) -> _BuiltinPack:
    try:
        manifest_stat = manifest_path.stat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise KnowledgeIndexError(
            f"Built-in knowledge manifest could not be inspected: {exc}"
        ) from exc
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or manifest_stat.st_size <= 0
        or manifest_stat.st_size > MAX_BUILTIN_MANIFEST_BYTES
    ):
        raise KnowledgeIndexError("Built-in knowledge manifest is not a valid file")
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeIndexError("Built-in knowledge manifest is invalid") from exc
    if not isinstance(manifest, Mapping):
        raise KnowledgeIndexError("Built-in knowledge manifest must be an object")
    pack_id = str(manifest.get("pack_id") or "").strip()
    pack_version = str(
        manifest.get("pack_version")
        or manifest.get("version")
        or manifest.get("schema_version")
        or ""
    ).strip()
    _safe_pack_component(pack_id)
    _safe_pack_component(pack_version)
    raw_entries = manifest.get("sources")
    if (
        not isinstance(raw_entries, list)
        or not raw_entries
        or len(raw_entries) > MAX_BUILTIN_CARDS
    ):
        raise KnowledgeIndexError(
            "Built-in knowledge manifest has no valid bounded card list"
        )

    root = manifest_path.parent.resolve()
    entries: list[Mapping[str, Any]] = []
    identifiers: set[str] = set()
    digest = hashlib.sha256()
    digest.update(raw_manifest)
    metadata_files: list[str] = []
    metadata_values: dict[str, Mapping[str, Any] | list[Any]] = {}
    for name in BUILTIN_METADATA_FILES:
        metadata_path = root / name
        if not metadata_path.is_file():
            if "pack_version" in manifest:
                raise KnowledgeIndexError(
                    f"Built-in knowledge metadata file is missing: {name}"
                )
            continue
        if metadata_path.is_symlink() or metadata_path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise KnowledgeIndexError(
                f"Built-in knowledge metadata file is invalid: {name}"
            )
        try:
            metadata_value = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise KnowledgeIndexError(
                f"Built-in knowledge metadata JSON is invalid: {name}"
            ) from exc
        if not isinstance(metadata_value, (Mapping, list)):
            raise KnowledgeIndexError(
                f"Built-in knowledge metadata JSON has no usable root: {name}"
            )
        if isinstance(metadata_value, Mapping):
            for field, expected in (
                ("pack_id", pack_id),
                ("pack_version", pack_version),
            ):
                if (
                    field in metadata_value
                    and str(metadata_value.get(field) or "").strip() != expected
                ):
                    raise KnowledgeIndexError(
                        f"Built-in knowledge metadata {name} {field} "
                        "does not match the manifest"
                    )
        metadata_values[name] = metadata_value
        metadata_files.append(name)
        digest.update(b"\0metadata\0")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(metadata_path.read_bytes())

    source_registry: dict[str, str] = {}
    sources_metadata = metadata_values.get("sources.json")
    if sources_metadata is not None:
        raw_registry = (
            sources_metadata.get("sources")
            if isinstance(sources_metadata, Mapping)
            else None
        )
        if not isinstance(raw_registry, list) or not raw_registry:
            raise KnowledgeIndexError(
                "Built-in knowledge source registry must contain a non-empty "
                "sources list"
            )
        for raw_source in raw_registry:
            if not isinstance(raw_source, Mapping):
                raise KnowledgeIndexError(
                    "Built-in knowledge source registry entry must be an object"
                )
            source_id = str(raw_source.get("id") or "").strip()
            source_url_value = raw_source.get("url")
            source_url = (
                source_url_value.strip()
                if isinstance(source_url_value, str)
                else ""
            )
            _safe_pack_component(source_id)
            if source_id in source_registry:
                raise KnowledgeIndexError(
                    f"Built-in knowledge source id is duplicated: {source_id}"
                )
            if not source_url or not _is_sidefx_https_url(source_url):
                raise KnowledgeIndexError(
                    "Built-in knowledge registry source is not SideFX HTTPS: "
                    f"{source_id}"
                )
            source_registry[source_id] = source_url

    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise KnowledgeIndexError("Built-in knowledge card entry must be an object")
        entry = dict(raw_entry)
        identifier = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        relative_text = str(entry.get("path") or "").replace("\\", "/").strip()
        upstream_url = str(entry.get("url") or "").strip()
        official_urls: list[str] = []
        if upstream_url:
            official_urls.append(upstream_url)
        for field in ("official_urls", "source_urls", "urls"):
            raw_urls = entry.get(field)
            if raw_urls is None:
                continue
            if not isinstance(raw_urls, list):
                raise KnowledgeIndexError(
                    f"Built-in knowledge card URL list is invalid: {identifier}"
                )
            for value in raw_urls:
                if not isinstance(value, str) or not value.strip():
                    raise KnowledgeIndexError(
                        f"Built-in knowledge card URL list is invalid: {identifier}"
                    )
                official_urls.append(value.strip())

        raw_source_ids = entry.get("source_ids")
        source_ids: list[str] = []
        if raw_source_ids is not None:
            if not isinstance(raw_source_ids, list) or not raw_source_ids:
                raise KnowledgeIndexError(
                    f"Built-in knowledge card source_ids list is invalid: {identifier}"
                )
            for value in raw_source_ids:
                if not isinstance(value, str) or not value.strip():
                    raise KnowledgeIndexError(
                        "Built-in knowledge card source_ids list is invalid: "
                        f"{identifier}"
                    )
                source_id = value.strip()
                if source_id not in source_registry:
                    raise KnowledgeIndexError(
                        "Built-in knowledge card references an unknown source id: "
                        f"{identifier}:{source_id}"
                    )
                source_ids.append(source_id)
                official_urls.append(source_registry[source_id])

        official_urls = list(dict.fromkeys(official_urls))
        if not upstream_url and official_urls:
            upstream_url = official_urls[0]
        _safe_pack_component(identifier)
        if identifier in identifiers:
            raise KnowledgeIndexError(
                f"Built-in knowledge card id is duplicated: {identifier}"
            )
        identifiers.add(identifier)
        if not title:
            raise KnowledgeIndexError(
                f"Built-in knowledge card has no title: {identifier}"
            )
        relative = PurePosixPath(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise KnowledgeIndexError(
                f"Built-in knowledge card path is invalid: {identifier}"
            )
        source_path = root.joinpath(*relative.parts)
        try:
            resolved_source = source_path.resolve()
            source_stat = resolved_source.stat()
        except OSError as exc:
            raise KnowledgeIndexError(
                f"Built-in knowledge card is missing: {identifier}"
            ) from exc
        if (
            not _is_within(resolved_source, root)
            or not resolved_source.is_file()
            or source_path.is_symlink()
            or source_stat.st_size > MAX_DOCUMENT_BYTES
        ):
            raise KnowledgeIndexError(
                f"Built-in knowledge card is not an allowed file: {identifier}"
            )
        for official_url in official_urls:
            if not _is_sidefx_https_url(official_url):
                raise KnowledgeIndexError(
                    "Built-in knowledge card source is not SideFX HTTPS: "
                    f"{identifier}"
                )
        if not official_urls:
            raise KnowledgeIndexError(
                f"Built-in knowledge card has no official source: {identifier}"
            )
        canonical_id = str(entry.get("canonical_id") or identifier).strip()
        _safe_pack_component(canonical_id)
        digest.update(b"\0")
        digest.update(identifier.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            raw_content = resolved_source.read_bytes()
            decoded_content = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise KnowledgeIndexError(
                f"Built-in knowledge card must be valid UTF-8: {identifier}"
            ) from exc
        except OSError as exc:
            raise KnowledgeIndexError(
                f"Built-in knowledge card could not be read: {identifier}"
            ) from exc
        if not _normalize_text(decoded_content.lstrip("\ufeff")):
            raise KnowledgeIndexError(
                f"Built-in knowledge card body is empty: {identifier}"
            )
        digest.update(raw_content)
        content_sha256 = hashlib.sha256(raw_content).hexdigest()
        declared_sha256 = str(entry.get("content_sha256") or "").casefold()
        if declared_sha256 and (
            not re.fullmatch(r"[0-9a-f]{64}", declared_sha256)
            or declared_sha256 != content_sha256
        ):
            raise KnowledgeIndexError(
                f"Built-in knowledge card content hash is invalid: {identifier}"
            )
        entry["id"] = identifier
        entry["title"] = title
        entry["path"] = relative.as_posix()
        entry["url"] = upstream_url
        entry["official_urls"] = official_urls
        if raw_source_ids is not None:
            entry["source_ids"] = list(dict.fromkeys(source_ids))
        entry["canonical_id"] = canonical_id
        entry["content_sha256"] = content_sha256
        entries.append(entry)
    return _BuiltinPack(
        root=root,
        manifest_path=manifest_path.resolve(),
        manifest=dict(manifest),
        entries=tuple(entries),
        metadata_files=tuple(metadata_files),
        pack_id=pack_id,
        pack_version=pack_version,
        digest=digest.hexdigest(),
    )


def _load_community_pack(manifest_path: Path) -> _BuiltinPack:
    """Load an attributed summary pack without treating it as official data."""

    try:
        manifest_stat = manifest_path.stat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise KnowledgeIndexError(
            f"Community knowledge manifest could not be inspected: {exc}"
        ) from exc
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or manifest_stat.st_size <= 0
        or manifest_stat.st_size > MAX_BUILTIN_MANIFEST_BYTES
    ):
        raise KnowledgeIndexError(
            "Community knowledge manifest is not a valid file"
        )
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeIndexError(
            "Community knowledge manifest is invalid"
        ) from exc
    if not isinstance(manifest, Mapping):
        raise KnowledgeIndexError(
            "Community knowledge manifest must be an object"
        )
    pack_id = str(manifest.get("pack_id") or "").strip()
    pack_version = str(manifest.get("pack_version") or "").strip()
    _safe_pack_component(pack_id)
    _safe_pack_component(pack_version)
    if str(manifest.get("source_kind") or "") != COMMUNITY_TUTORIAL_SOURCE:
        raise KnowledgeIndexError(
            "Community knowledge source_kind must be community_tutorial"
        )
    if str(manifest.get("verification") or "") != "community_unverified":
        raise KnowledgeIndexError(
            "Community knowledge verification must remain "
            "community_unverified"
        )
    if manifest.get("upstream_content_redistributed") is not False:
        raise KnowledgeIndexError(
            "Community knowledge must declare that upstream content is not "
            "redistributed"
        )
    raw_entries = manifest.get("sources")
    if (
        not isinstance(raw_entries, list)
        or not raw_entries
        or len(raw_entries) > MAX_BUILTIN_CARDS
    ):
        raise KnowledgeIndexError(
            "Community knowledge manifest has no valid bounded card list"
        )

    root = manifest_path.parent.resolve()
    digest = hashlib.sha256()
    digest.update(raw_manifest)
    metadata_files: list[str] = []
    metadata_values: dict[str, Mapping[str, Any]] = {}
    for name in BUILTIN_METADATA_FILES:
        metadata_path = root / name
        try:
            metadata_stat = metadata_path.stat()
        except OSError as exc:
            raise KnowledgeIndexError(
                f"Community knowledge metadata file is missing: {name}"
            ) from exc
        if (
            not metadata_path.is_file()
            or metadata_path.is_symlink()
            or metadata_stat.st_size <= 0
            or metadata_stat.st_size > MAX_DOCUMENT_BYTES
        ):
            raise KnowledgeIndexError(
                f"Community knowledge metadata file is invalid: {name}"
            )
        try:
            raw_metadata = metadata_path.read_bytes()
            metadata_value = json.loads(raw_metadata.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise KnowledgeIndexError(
                f"Community knowledge metadata JSON is invalid: {name}"
            ) from exc
        if not isinstance(metadata_value, Mapping):
            raise KnowledgeIndexError(
                f"Community knowledge metadata must be an object: {name}"
            )
        for field, expected in (
            ("pack_id", pack_id),
            ("pack_version", pack_version),
        ):
            if str(metadata_value.get(field) or "").strip() != expected:
                raise KnowledgeIndexError(
                    f"Community knowledge metadata {name} {field} does not "
                    "match the manifest"
                )
        metadata_values[name] = metadata_value
        metadata_files.append(name)
        digest.update(b"\0metadata\0")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw_metadata)

    raw_registry = metadata_values["sources.json"].get("sources")
    if (
        not isinstance(raw_registry, list)
        or not raw_registry
        or len(raw_registry) > MAX_BUILTIN_CARDS * 8
    ):
        raise KnowledgeIndexError(
            "Community knowledge source registry must contain a non-empty "
            "bounded sources list"
        )
    source_registry: dict[str, dict[str, Any]] = {}
    source_urls: set[str] = set()
    required_source_text = (
        "title",
        "author",
        "url",
        "accessed",
        "type",
        "license_or_usage",
        "houdini_version",
        "verification",
    )
    for raw_source in raw_registry:
        if not isinstance(raw_source, Mapping):
            raise KnowledgeIndexError(
                "Community knowledge source registry entry must be an object"
            )
        source = dict(raw_source)
        source_id = str(source.get("id") or "").strip()
        _safe_pack_component(source_id)
        if source_id in source_registry:
            raise KnowledgeIndexError(
                f"Community knowledge source id is duplicated: {source_id}"
            )
        for field in required_source_text:
            if not isinstance(source.get(field), str) or not str(
                source.get(field)
            ).strip():
                raise KnowledgeIndexError(
                    "Community knowledge source is missing required text: "
                    f"{source_id}:{field}"
                )
        source_url = str(source["url"]).strip()
        if not _is_public_https_url(source_url):
            raise KnowledgeIndexError(
                "Community knowledge registry source is not public HTTPS: "
                f"{source_id}"
            )
        if source_url in source_urls:
            raise KnowledgeIndexError(
                "Community knowledge registry URL is duplicated: "
                f"{source_url}"
            )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(source["accessed"])):
            raise KnowledgeIndexError(
                "Community knowledge source accessed date is invalid: "
                f"{source_id}"
            )
        if source["verification"] != "community_unverified":
            raise KnowledgeIndexError(
                "Community knowledge source verification must remain "
                f"community_unverified: {source_id}"
            )
        if not isinstance(source.get("free"), bool):
            raise KnowledgeIndexError(
                f"Community knowledge source free flag is invalid: {source_id}"
            )
        source["id"] = source_id
        source["url"] = source_url
        source_registry[source_id] = source
        source_urls.add(source_url)

    entries: list[Mapping[str, Any]] = []
    identifiers: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise KnowledgeIndexError(
                "Community knowledge card entry must be an object"
            )
        entry = dict(raw_entry)
        identifier = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        author = str(entry.get("author") or "").strip()
        houdini_version = str(
            entry.get("houdini_version") or ""
        ).strip()
        relative_text = str(entry.get("path") or "").replace(
            "\\",
            "/",
        ).strip()
        _safe_pack_component(identifier)
        if identifier in identifiers:
            raise KnowledgeIndexError(
                f"Community knowledge card id is duplicated: {identifier}"
            )
        identifiers.add(identifier)
        if not title or not author or not houdini_version:
            raise KnowledgeIndexError(
                "Community knowledge card requires title, author, and "
                f"houdini_version: {identifier}"
            )
        if entry.get("verification") != "community_unverified":
            raise KnowledgeIndexError(
                "Community knowledge card verification must remain "
                f"community_unverified: {identifier}"
            )
        raw_source_ids = entry.get("source_ids")
        if not isinstance(raw_source_ids, list) or not raw_source_ids:
            raise KnowledgeIndexError(
                "Community knowledge card source_ids list is invalid: "
                f"{identifier}"
            )
        source_ids: list[str] = []
        source_records: list[dict[str, Any]] = []
        for value in raw_source_ids:
            source_id = str(value).strip() if isinstance(value, str) else ""
            if not source_id or source_id not in source_registry:
                raise KnowledgeIndexError(
                    "Community knowledge card references an unknown source: "
                    f"{identifier}:{source_id}"
                )
            if source_id in source_ids:
                raise KnowledgeIndexError(
                    "Community knowledge card source id is duplicated: "
                    f"{identifier}:{source_id}"
                )
            source_ids.append(source_id)
            source_records.append(dict(source_registry[source_id]))
        upstream_url = str(entry.get("url") or "").strip()
        if not _is_public_https_url(upstream_url) or upstream_url not in {
            record["url"] for record in source_records
        }:
            raise KnowledgeIndexError(
                "Community knowledge card URL must identify one of its "
                f"registered sources: {identifier}"
            )
        relative = PurePosixPath(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.parts[0] != "cards"
            or relative.suffix.casefold() != ".md"
        ):
            raise KnowledgeIndexError(
                f"Community knowledge card path is invalid: {identifier}"
            )
        source_path = root.joinpath(*relative.parts)
        try:
            resolved_source = source_path.resolve()
            source_stat = resolved_source.stat()
        except OSError as exc:
            raise KnowledgeIndexError(
                f"Community knowledge card is missing: {identifier}"
            ) from exc
        if (
            not _is_within(resolved_source, root)
            or not resolved_source.is_file()
            or source_path.is_symlink()
            or source_stat.st_size <= 0
            or source_stat.st_size > MAX_DOCUMENT_BYTES
        ):
            raise KnowledgeIndexError(
                "Community knowledge card is not an allowed file: "
                f"{identifier}"
            )
        try:
            raw_content = resolved_source.read_bytes()
            decoded_content = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise KnowledgeIndexError(
                f"Community knowledge card must be UTF-8: {identifier}"
            ) from exc
        except OSError as exc:
            raise KnowledgeIndexError(
                f"Community knowledge card could not be read: {identifier}"
            ) from exc
        if not _normalize_text(decoded_content.lstrip("\ufeff")):
            raise KnowledgeIndexError(
                f"Community knowledge card body is empty: {identifier}"
            )
        canonical_id = str(
            entry.get("canonical_id") or identifier
        ).strip()
        _safe_pack_component(canonical_id)
        digest.update(b"\0card\0")
        digest.update(identifier.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw_content)
        content_sha256 = hashlib.sha256(raw_content).hexdigest()
        declared_sha256 = str(entry.get("content_sha256") or "").casefold()
        if declared_sha256 and (
            not re.fullmatch(r"[0-9a-f]{64}", declared_sha256)
            or declared_sha256 != content_sha256
        ):
            raise KnowledgeIndexError(
                f"Community knowledge content hash is invalid: {identifier}"
            )
        entry["id"] = identifier
        entry["title"] = title
        entry["author"] = author
        entry["houdini_version"] = houdini_version
        entry["path"] = relative.as_posix()
        entry["url"] = upstream_url
        entry["source_ids"] = source_ids
        entry["source_records"] = source_records
        entry["canonical_id"] = canonical_id
        entry["content_sha256"] = content_sha256
        entries.append(entry)
    return _BuiltinPack(
        root=root,
        manifest_path=manifest_path.resolve(),
        manifest=dict(manifest),
        entries=tuple(entries),
        metadata_files=tuple(metadata_files),
        pack_id=pack_id,
        pack_version=pack_version,
        digest=digest.hexdigest(),
    )


def _is_sidefx_https_url(value: str) -> bool:
    parsed_url = urlsplit(value)
    return (
        parsed_url.scheme.casefold() == "https"
        and (parsed_url.hostname or "").casefold()
        in {"sidefx.com", "www.sidefx.com", "media.sidefx.com"}
    )


def _is_public_https_url(value: str) -> bool:
    parsed_url = urlsplit(value)
    hostname = (parsed_url.hostname or "").casefold().rstrip(".")
    return bool(
        parsed_url.scheme.casefold() == "https"
        and hostname
        and parsed_url.username is None
        and parsed_url.password is None
        and hostname not in {"localhost", "::1"}
        and not hostname.startswith("127.")
        and not hostname.endswith(".local")
    )


def _safe_pack_component(value: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}", value):
        raise KnowledgeIndexError(
            "Built-in knowledge pack identifiers must be portable ASCII names"
        )
    return value


def _installed_pack_matches(target: Path, source_pack: _BuiltinPack) -> bool:
    marker = _read_json_object(target / BUILTIN_PACK_MARKER)
    if str(marker.get("digest") or "") != source_pack.digest:
        return False
    try:
        installed = _load_builtin_pack(target / "manifest.json")
    except (FileNotFoundError, KnowledgeIndexError):
        return False
    return installed.digest == source_pack.digest


def _installed_community_pack_matches(
    target: Path,
    source_pack: _BuiltinPack,
) -> bool:
    marker = _read_json_object(target / BUILTIN_PACK_MARKER)
    if str(marker.get("digest") or "") != source_pack.digest:
        return False
    try:
        installed = _load_community_pack(target / "manifest.json")
    except (FileNotFoundError, KnowledgeIndexError):
        return False
    return installed.digest == source_pack.digest


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > MAX_BUILTIN_MANIFEST_BYTES
        ):
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.is_file():
            temporary.unlink()


def _join_chunk_bodies(bodies: Iterable[str]) -> str:
    output = ""
    for body in bodies:
        if not output:
            output = body
            continue
        maximum = min(len(output), len(body), CHUNK_OVERLAP_CHARS * 2)
        overlap = 0
        for size in range(maximum, 0, -1):
            if output.endswith(body[:size]):
                overlap = size
                break
        if not overlap and not output.endswith(("\n", " ")):
            output += "\n\n"
        output += body[overlap:]
    return output


def _stable_metadata_json(metadata: Mapping[str, Any]) -> str:
    return json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _metadata_thread_id(attributes_json: str) -> str:
    try:
        attributes = json.loads(attributes_json)
        provenance = attributes.get("provenance", {})
        return str(provenance.get("thread_id") or "")
    except (AttributeError, TypeError, json.JSONDecodeError):
        return ""


_WORKFLOW_QUERY_TERMS = frozenset(
    {
        "how",
        "workflow",
        "help",
        "tutorial",
        "guide",
        "如何",
        "教程",
        "工作流",
        "帮助",
    }
)
_NODE_CATALOG_SOURCES = frozenset({"houdini_node_catalog"})
_NODE_HELP_SOURCES = frozenset(
    {"houdini_help", "houdini_help_archive"}
)


def _lexical_rank_key(
    row: Mapping[str, Any],
    query: str,
    current_version: str,
) -> tuple[Any, ...]:
    source = str(row["source"])
    canonical = _canonical_node_key(
        source,
        str(row["title"]),
        str(row["source_path"]),
    )
    query_key = _identifier_key(
        re.split(r"::|[/\\]", query.strip())[-1]
    )
    workflow = any(
        term in query.casefold()
        for term in _WORKFLOW_QUERY_TERMS
    )
    exact_node = bool(canonical and query_key == canonical)
    if workflow:
        source_tier = (
            0
            if source in _NODE_HELP_SOURCES
            else 2
            if source in _NODE_CATALOG_SOURCES
            else 1
        )
    elif exact_node:
        source_tier = (
            0
            if source in _NODE_CATALOG_SOURCES
            else 1
            if source in _NODE_HELP_SOURCES
            else 2
        )
    else:
        source_tier = 1
    version = str(row["houdini_version"])
    version_tier = (
        0
        if version == current_version
        else 1
        if _houdini_versions_match(version, current_version)
        else 2
        if version in {"", "any", "current"}
        else 3
    )
    lexical_score = float(row["lexical_score"])
    query_tokens = _fts_tokens(query)
    if len(query_tokens) < 4:
        # BM25 differences for very short queries are often only document-
        # length noise.  Compare bounded, observable token evidence first;
        # when that evidence is equal, prefer the current-version hint.  A
        # genuinely stronger older workflow can therefore win without making
        # version metadata a filter or teaching the same workflow per release.
        rank_tiers = (
            *_short_query_token_evidence(row, query_tokens),
            version_tier,
            lexical_score,
        )
    else:
        rank_tiers = (lexical_score, version_tier)
    return (
        _exact_identity_tier(row, query),
        source_tier,
        *rank_tiers,
        0 if str(row["verification"]) == "verified" else 1,
        str(row["title"]).casefold(),
    )


def _short_query_token_evidence(
    row: Mapping[str, Any],
    query_tokens: tuple[str, ...],
) -> tuple[int, int]:
    unique_query_tokens = tuple(
        dict.fromkeys(token.casefold() for token in query_tokens)
    )
    document_tokens = tuple(
        token.casefold()
        for token in _fts_tokens(
            f'{str(row["title"])} {str(row["body"])}'
        )
    )
    counts = {
        token: document_tokens.count(token)
        for token in unique_query_tokens
    }
    coverage = sum(1 for count in counts.values() if count)
    bounded_frequency = sum(min(count, 3) for count in counts.values())
    return (-coverage, -bounded_frequency)


def _exact_identity_tier(row: Mapping[str, Any], query: str) -> int:
    query_key = str(query).strip().casefold()
    if not query_key:
        return 1
    values = {str(row["title"]).strip().casefold()}
    try:
        attributes = json.loads(str(row["attributes_json"]) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        attributes = {}
    if isinstance(attributes, Mapping):
        for name in ("card_id", "canonical_id"):
            value = str(attributes.get(name) or "").strip().casefold()
            if value:
                values.add(value)
        aliases = attributes.get("aliases")
        if isinstance(aliases, list):
            values.update(
                str(value).strip().casefold()
                for value in aliases
                if isinstance(value, str) and value.strip()
            )
    return 0 if query_key in values else 1


def _houdini_version_majors(value: str) -> tuple[str, ...]:
    folded = str(value or "").strip().casefold()
    if folded in {"", "any", "current", "unknown"}:
        return ()

    majors: set[int] = set()
    ranges: list[tuple[int, int]] = []
    range_pattern = re.compile(
        r"(?<![a-z0-9])h?\s*(\d{1,2})(?:\.\d+){0,2}\s*"
        r"[-\N{EN DASH}\N{EM DASH}]\s*h?\s*"
        r"(\d{1,2})(?:\.\d+){0,2}(?!\d)",
        flags=re.IGNORECASE,
    )
    for match in range_pattern.finditer(folded):
        start = int(match.group(1))
        end = int(match.group(2))
        if start <= end and end - start <= 50:
            majors.update(range(start, end + 1))
        else:
            majors.update((start, end))
        ranges.append(match.span())

    remainder = list(folded)
    for start, end in ranges:
        remainder[start:end] = " " * (end - start)
    token_pattern = re.compile(
        r"(?<![a-z0-9])h?\s*(\d{1,2})(?:\.\d+){0,2}(?!\d)",
        flags=re.IGNORECASE,
    )
    majors.update(
        int(match.group(1))
        for match in token_pattern.finditer("".join(remainder))
    )
    return tuple(str(value) for value in sorted(majors))


def _houdini_version_major(value: str) -> str:
    majors = _houdini_version_majors(value)
    return majors[0] if majors else ""


def _houdini_versions_match(left: str, right: str) -> bool:
    left_majors = set(_houdini_version_majors(left))
    right_majors = set(_houdini_version_majors(right))
    return bool(left_majors.intersection(right_majors))


def _houdini_version_status(declared: str, current: str) -> str:
    declared_majors = set(_houdini_version_majors(declared))
    current_majors = set(_houdini_version_majors(current))
    if not declared_majors or not current_majors:
        return "unknown"
    return "match" if declared_majors.intersection(current_majors) else "mismatch"


def _version_order_parameters(version: str) -> tuple[str, str, str, str]:
    major = _houdini_version_major(version)
    return version, major, major, f"{major}.%" if major else ""


def _fold_node_catalog_matches(
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in matches:
        metadata = match.get("metadata")
        source_path = (
            str(metadata.get("path") or "")
            if isinstance(metadata, Mapping)
            else ""
        )
        key = _canonical_node_key(
            str(match.get("source") or ""),
            str(match.get("title") or ""),
            source_path,
        )
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        output.append(match)
    return output


def _canonical_node_key(source: str, title: str, source_path: str) -> str:
    if source in _NODE_CATALOG_SOURCES:
        parts = [part for part in title.split("::") if part]
        value = parts[-1] if parts else title
    elif source in _NODE_HELP_SOURCES:
        member = source_path.replace("\\", "/").rsplit("!/", 1)[-1]
        value = Path(member).stem
    else:
        return ""
    key = _identifier_key(value)
    return "" if key in {"", "index", "overview"} else key


def _identifier_key(value: str) -> str:
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )


def _first_media_sidecar(media: Path) -> Path | None:
    for suffix in (".srt", ".vtt", ".txt"):
        for candidate in (media.with_suffix(suffix), Path(str(media) + suffix)):
            try:
                if candidate.is_file() and not candidate.is_symlink():
                    return candidate.resolve()
            except OSError:
                continue
    return None


def _resolved_or_self(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _combined_stat(paths: Iterable[Path]) -> tuple[int, int]:
    size = 0
    modified = 0
    for path in paths:
        try:
            value = path.stat()
        except OSError:
            continue
        size += int(value.st_size)
        modified = max(modified, int(value.st_mtime_ns))
    return size, modified


def _managed_source_metadata(path: Path) -> dict[str, Any]:
    sidecar = Path(str(path) + ".metadata.json")
    if not sidecar.is_file() or sidecar.is_symlink():
        return {}
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "accessed_at",
        "author",
        "evidence",
        "houdini_version",
        "imported_at",
        "license",
        "original_name",
        "media_copied",
        "selected_media_name",
        "selected_media_path",
        "selected_media_size_bytes",
        "source_kind",
        "source_id",
        "source_sha256",
        "transcript_format",
        "transcript_managed",
        "transcript_original_name",
        "transcript_original_path",
        "url",
    }
    return {
        key: value[key]
        for key in sorted(allowed.intersection(value))
        if isinstance(value[key], (str, int, float, bool))
    }


def _walk_files(root: Path) -> Iterable[Path]:
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        base = Path(directory)
        for name in file_names:
            yield base / name


def _read_optional_pdf(path: Path) -> tuple[str | None, str]:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return (
            None,
            "PDF was skipped because optional pypdf support is not installed",
        )
    try:
        reader = PdfReader(str(path))
        parts = [str(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        return None, f"optional PDF parsing failed ({exc})"
    return "\n\n".join(parts), ""


def _read_zip_text(
    path: Path,
    member: str,
    *,
    archive_cache: dict[Path, zipfile.ZipFile] | None = None,
) -> tuple[str | None, str]:
    try:
        archive: zipfile.ZipFile
        close_archive = archive_cache is None
        if archive_cache is None:
            archive = zipfile.ZipFile(path)
        else:
            cached_archive = archive_cache.get(path)
            if cached_archive is None:
                archive = zipfile.ZipFile(path)
                archive_cache[path] = archive
            else:
                archive = cached_archive
        try:
            info = archive.getinfo(member)
            if info.file_size > MAX_DOCUMENT_BYTES:
                return (
                    None,
                    f"archive member exceeded the {MAX_DOCUMENT_BYTES} "
                    "byte local-index limit and was skipped",
                )
            with archive.open(info, "r") as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        finally:
            if close_archive:
                archive.close()
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return None, f"installed help archive member could not be read ({exc})"
    if len(raw) > MAX_DOCUMENT_BYTES:
        return (
            None,
            f"archive member exceeded the {MAX_DOCUMENT_BYTES} "
            "byte local-index limit and was skipped",
        )
    return raw.decode("utf-8", errors="ignore"), ""


def _html_to_text(text: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return text
    return "".join(parser.parts)


def _captions_to_text(text: str) -> str:
    lines: list[str] = []
    timestamp = re.compile(
        r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.casefold() == "webvtt"
            or stripped.isdecimal()
            or timestamp.match(stripped)
            or stripped.startswith(("NOTE", "STYLE", "REGION"))
        ):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", text)
    ]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        end = min(length, cursor + MAX_CHUNK_CHARS)
        if end < length:
            boundary = max(
                text.rfind("\n\n", cursor + MAX_CHUNK_CHARS // 2, end),
                text.rfind(". ", cursor + MAX_CHUNK_CHARS // 2, end),
                text.rfind("。", cursor + MAX_CHUNK_CHARS // 2, end),
            )
            if boundary > cursor:
                end = boundary + 1
        chunk = text[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        cursor = max(cursor + 1, end - CHUNK_OVERLAP_CHARS)
    return chunks


def _matching_snippet(text: str, query: str) -> str:
    folded = text.casefold()
    position = folded.find(query.casefold())
    if position < 0:
        position = 0
    start = max(0, position - 220)
    end = min(len(text), position + len(query) + 520)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    if start:
        snippet = "… " + snippet
    if end < len(text):
        snippet += " …"
    return _bounded(snippet, 1000)


def _fts_expression(query: str, tokenizer: str) -> str:
    if tokenizer == "trigram":
        return '"' + query.replace('"', '""') + '"'
    tokens = _fts_tokens(query)
    if not tokens:
        return '"' + query.replace('"', '""') + '"'
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _fts_relaxed_expression(query: str, tokenizer: str) -> str:
    tokens = _fts_tokens(query)
    if tokenizer == "trigram":
        tokens = tuple(token for token in tokens if len(token) >= 3)
    return " OR ".join(
        '"' + token.replace('"', '""') + '"'
        for token in tokens
    )


def _fts_tokens(query: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", query, flags=re.UNICODE))


def _is_ascii_word_query(query: str) -> bool:
    return bool(re.fullmatch(r"[\w\s.-]+", query, flags=re.ASCII))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum]
