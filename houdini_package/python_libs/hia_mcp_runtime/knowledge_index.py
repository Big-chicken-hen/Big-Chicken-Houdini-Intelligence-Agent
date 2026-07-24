"""Deterministic project-local lexical knowledge index.

This module deliberately contains no model, embeddings, planner, semantic
memory, service, or scheduler.  It turns explicitly allowed local sources into
short text chunks and searches them with Python's sqlite3 FTS5 support.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping


DATABASE_FILENAME = "knowledge.sqlite3"
SCHEMA_VERSION = "1"
REFRESH_INTERVAL_SECONDS = 10 * 60.0
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP_CHARS = 120

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
SOURCE_GROUPS = frozenset({"houdini", "project", "user"})


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
    stat_size: int = 0
    stat_mtime_ns: int = 0


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

    def __init__(self, project_root: str | os.PathLike[str]) -> None:
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
        self.knowledge_root.mkdir(parents=True, exist_ok=True)
        self.sources_root.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    @property
    def relative_database_path(self) -> str:
        return self.database_path.relative_to(self.project_root).as_posix()

    def active_houdini_version(self) -> str:
        return self._meta_value("active_houdini_version")

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
        with closing(self._connect()) as connection:
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
            "files_scanned": 0,
            "documents_added": 0,
            "documents_updated": 0,
            "documents_removed": 0,
            "documents_unchanged": 0,
        }
        warnings: list[str] = []
        if not requested:
            return stats, warnings

        current_version = str(snapshot.get("houdini_version") or "unknown")
        now = time.time()
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

                for group in sorted(groups):
                    if group == "houdini":
                        collection = f"houdini:{current_version}"
                    else:
                        collection = group
                    candidates, collection_warnings = self._collect_candidates(
                        group,
                        snapshot,
                        current_version,
                    )
                    warnings.extend(collection_warnings)
                    collection_stats = self._refresh_collection(
                        connection,
                        collection,
                        candidates,
                        warnings,
                    )
                    for key, value in collection_stats.items():
                        stats[key] += value
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
                if "houdini" in groups:
                    self._set_meta(
                        connection,
                        "active_houdini_version",
                        current_version,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return stats, warnings

    def search(
        self,
        query: str,
        source_groups: Iterable[str],
        *,
        current_houdini_version: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        groups = sorted(set(source_groups))
        if not groups:
            return {"matches": [], "total": 0, "tokenizer": self._tokenizer()}
        invalid = set(groups).difference(SOURCE_GROUPS)
        if invalid:
            raise KnowledgeIndexError(
                f"Unsupported local knowledge sources: {sorted(invalid)!r}"
            )

        placeholders = ",".join("?" for _value in groups)
        tokenizer = self._tokenizer()
        use_fts = len(query) >= 3 and (
            tokenizer == "trigram" or _is_ascii_word_query(query)
        )
        version = current_houdini_version or "unknown"
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            if use_fts:
                expression = _fts_expression(query, tokenizer)
                where = (
                    "knowledge_fts MATCH ? "
                    f"AND d.source_group IN ({placeholders})"
                )
                parameters: list[Any] = [expression, *groups]
                count_sql = f"""
                    SELECT COUNT(DISTINCT d.id)
                    FROM knowledge_fts
                    JOIN chunks c ON c.id = knowledge_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                """
                select_sql = f"""
                    SELECT d.*, c.ordinal, c.body,
                           MIN(knowledge_fts.rank) AS lexical_score
                    FROM knowledge_fts
                    JOIN chunks c ON c.id = knowledge_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                    GROUP BY d.id
                    ORDER BY
                        CASE
                            WHEN d.houdini_version = ? THEN 0
                            WHEN d.houdini_version IN ('', 'any', 'current') THEN 1
                            ELSE 2
                        END,
                        CASE d.verification WHEN 'verified' THEN 0 ELSE 1 END,
                        lexical_score,
                        d.title COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                total = int(
                    connection.execute(count_sql, parameters).fetchone()[0]
                )
                rows = connection.execute(
                    select_sql,
                    [*parameters, version, limit, offset],
                ).fetchall()
            else:
                escaped = _escape_like(query)
                pattern = f"%{escaped}%"
                where = (
                    "(d.title LIKE ? ESCAPE '\\' "
                    "OR c.body LIKE ? ESCAPE '\\') "
                    f"AND d.source_group IN ({placeholders})"
                )
                parameters = [pattern, pattern, *groups]
                count_sql = f"""
                    SELECT COUNT(DISTINCT d.id)
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                """
                select_sql = f"""
                    SELECT d.*, c.ordinal, c.body,
                           MIN(
                               CASE WHEN d.title LIKE ? ESCAPE '\\'
                                    THEN -2.0 ELSE -1.0 END
                           ) AS lexical_score
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
                    GROUP BY d.id
                    ORDER BY
                        CASE
                            WHEN d.houdini_version = ? THEN 0
                            WHEN d.houdini_version IN ('', 'any', 'current') THEN 1
                            ELSE 2
                        END,
                        CASE d.verification WHEN 'verified' THEN 0 ELSE 1 END,
                        lexical_score,
                        d.title COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                total = int(
                    connection.execute(count_sql, parameters).fetchone()[0]
                )
                rows = connection.execute(
                    select_sql,
                    [pattern, *parameters, version, limit, offset],
                ).fetchall()

        matches: list[dict[str, Any]] = []
        for row in rows:
            try:
                attributes = json.loads(row["attributes_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attributes = {}
            metadata = dict(attributes) if isinstance(attributes, Mapping) else {}
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
                        and row["houdini_version"] == version
                    ),
                    "license": row["license"],
                    "verification": row["verification"],
                    "evidence": row["evidence"],
                    "sha256": row["sha256"],
                    "chunk_ordinal": int(row["ordinal"]),
                    "lexical_score": float(row["lexical_score"]),
                }
            )
            matches.append(
                {
                    "source": row["source"],
                    "title": row["title"],
                    "snippet": _matching_snippet(row["body"], query),
                    "metadata": metadata,
                }
            )
        return {"matches": matches, "total": total, "tokenizer": tokenizer}

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
                    UNIQUE(document_id, ordinal)
                );
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=10.0,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _tokenizer(self) -> str:
        return self._meta_value("fts_tokenizer") or "trigram"

    def _meta_value(self, key: str) -> str:
        with closing(self._connect()) as connection:
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

    def _collect_candidates(
        self,
        source_group: str,
        snapshot: Mapping[str, Any],
        current_version: str,
    ) -> tuple[list[_Candidate], list[str]]:
        if source_group == "houdini":
            return self._collect_houdini_candidates(snapshot, current_version)
        if source_group == "project":
            return self._collect_project_candidates(), []
        return self._collect_user_candidates(), []

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
                    if path.suffix.casefold() not in HOUDINI_TEXT_SUFFIXES:
                        continue
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

    def _collect_user_candidates(self) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        if not self.sources_root.is_dir():
            return candidates
        for path in _walk_files(self.sources_root):
            suffix = path.suffix.casefold()
            if suffix not in USER_TEXT_SUFFIXES | USER_OPTIONAL_SUFFIXES:
                continue
            candidate = self._path_candidate(
                collection="user",
                source_group="user",
                source="user_document",
                root=self.sources_root,
                path=path,
                title_prefix="",
                url_prefix="project://.runtime/knowledge/sources/",
                author="Unknown",
                houdini_version="any",
                license_name="Unspecified",
                evidence=(
                    "User-authorized local source; unverified until checked "
                    "in a real Houdini session"
                ),
                allow_sidecar=True,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

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
            "documents_added": 0,
            "documents_updated": 0,
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

        for source_key, row in existing_rows.items():
            if source_key not in present_keys:
                self._delete_document(connection, int(row[0]))
                stats["documents_removed"] += 1

        for candidate in candidates:
            existing = existing_rows.get(candidate.source_key)
            if candidate.path is not None:
                stats["files_scanned"] += 1
                if (
                    existing is not None
                    and int(existing[4]) == candidate.stat_size
                    and int(existing[5]) == candidate.stat_mtime_ns
                ):
                    stats["documents_unchanged"] += 1
                    continue

            text, metadata, read_warnings = self._read_candidate(candidate)
            warnings.extend(read_warnings)
            if text is None:
                continue
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            metadata_json = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            metadata_sha256 = hashlib.sha256(
                metadata_json.encode("utf-8")
            ).hexdigest()
            if (
                existing is not None
                and str(existing[2]) == text_sha256
            ):
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
    ) -> tuple[str | None, dict[str, Any], list[str]]:
        warnings: list[str] = []
        metadata = {
            "url": candidate.url,
            "author": candidate.author,
            "accessed_at": _utc_now(),
            "houdini_version": candidate.houdini_version,
            "license": candidate.license_name,
            "verification": candidate.verification,
            "evidence": candidate.evidence,
            "attributes": dict(candidate.attributes),
        }
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
        if existing_id is not None:
            self._delete_document(connection, existing_id)
        cursor = connection.execute(
            """
            INSERT INTO documents(
                collection, source_group, source, source_key, title,
                source_path, url, author, accessed_at, houdini_version,
                license, verification, evidence, sha256, metadata_sha256,
                stat_size, stat_mtime_ns, indexed_at, attributes_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.collection,
                candidate.source_group,
                candidate.source,
                candidate.source_key,
                candidate.title,
                candidate.source_path,
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
            ),
        )
        document_id = int(cursor.lastrowid)
        chunks = _chunk_text(text)
        for ordinal, body in enumerate(chunks):
            chunk_cursor = connection.execute(
                "INSERT INTO chunks(document_id, ordinal, body) "
                "VALUES(?, ?, ?)",
                (document_id, ordinal, body),
            )
            connection.execute(
                "INSERT INTO knowledge_fts(rowid, title, body) "
                "VALUES(?, ?, ?)",
                (int(chunk_cursor.lastrowid), candidate.title, body),
            )

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
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return '"' + query.replace('"', '""') + '"'
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


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
