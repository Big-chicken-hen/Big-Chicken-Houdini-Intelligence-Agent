"""Strict lexical, vector, and hybrid retrieval plus explicit project memory."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import operator
import os
import sqlite3
import sys
import threading
import time
import uuid
from array import array
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .deterministic_sources import (
    NormalizedSource,
    normalize_project_memory,
)
from .knowledge_index import (
    FILTERABLE_SOURCE_KINDS,
    SEARCH_SOURCE_GROUPS,
    KnowledgeIndexError,
    LocalKnowledgeIndex,
    _Candidate,
    _houdini_versions_match,
    _matching_snippet,
    _utc_now,
)


MEMORY_TYPES = frozenset(
    {"decision", "preference", "asset", "lesson", "workflow"}
)
SEARCH_MODES = frozenset({"lexical", "vector", "hybrid"})
VECTOR_BATCH_SIZE = 32
MAX_VECTOR_CANDIDATES = 20_000
MAX_QUERY_VECTOR_SYNCS = 32
MAX_VECTOR_CANDIDATE_DOCUMENTS = 900
MAX_VECTOR_TOP_K = 512
MIN_VECTOR_TOP_K = 64
VECTOR_HYDRATE_BATCH_SIZE = 500
DEFAULT_VECTOR_BUILD_BATCH = 32
MAX_VECTOR_BUILD_BATCH = 64
PARTIAL_CANDIDATES_REASON = (
    "VECTOR_INDEX_PARTIAL_LEXICAL_CANDIDATES_ONLY"
)
PARTIAL_NO_CANDIDATES_REASON = (
    "VECTOR_INDEX_PARTIAL_NO_LEXICAL_CANDIDATES"
)
RRF_K = 60.0
SOURCE_INTENT_BONUS = 0.001

_SOURCE_KINDS = {
    "builtin_official_workflow": "builtin_official_workflow",
    "bundled_knowledge_card": "builtin_official_workflow",
    "community_tutorial": "community_tutorial",
    "houdini_help": "live_node_help",
    "houdini_help_archive": "live_node_help",
    "houdini_node_catalog": "live_node_catalog",
    "user_document": "user_document",
    "user_transcript": "user_transcript",
    "thread_export": "thread_export",
    "project_memory": "project_memory",
    "project_docs": "project_reference",
    "project_skill": "project_reference",
}
_WORKFLOW_INTENT_TERMS = frozenset(
    {
        "how",
        "workflow",
        "tutorial",
        "guide",
        "steps",
        "setup",
        "troubleshoot",
        "如何",
        "工作流",
        "教程",
        "步骤",
        "故障",
    }
)
_MEMORY_INTENT_TERMS = frozenset(
    {
        "memory",
        "decision",
        "preference",
        "remember",
        "lesson",
        "记忆",
        "决定",
        "决策",
        "偏好",
        "教训",
    }
)
_USER_SOURCE_INTENT_TERMS = frozenset(
    {
        "user source",
        "my document",
        "imported document",
        "imported source",
        "用户资料",
        "我的文档",
        "导入资料",
    }
)


class HybridKnowledgeError(KnowledgeIndexError):
    """Raised for invalid explicit-memory operations."""


class _VectorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class _EmbeddingBatch:
    document_vectors: tuple[tuple[float, ...], ...]
    query_vectors: tuple[tuple[float, ...], ...]
    model_id: str
    model_revision: str
    profile_id: str
    requested_profile: str
    dim: int
    normalized: bool
    status: str
    repair: Mapping[str, Any]

    @property
    def signature(self) -> str:
        return (
            f"{self.profile_id}|{self.model_id}|{self.model_revision}|"
            f"{self.dim}|normalized={int(self.normalized)}"
        )


class HybridKnowledgeStore:
    """Compose the lexical index, optional vectors, and explicit memories."""

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        *,
        index: LocalKnowledgeIndex | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.index = index or LocalKnowledgeIndex(self.project_root)
        self._embedder = embedder
        self._embedder_initialized = embedder is not None
        self._embedder_error = ""
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            embedder = self._embedder
            self._embedder = None
            self._embedder_initialized = True
        if embedder is not None:
            close = getattr(embedder, "close", None)
            if callable(close):
                close()

    def status(self) -> dict[str, Any]:
        """Return the active vector-index state without encoding any text."""

        try:
            batch = self._configured_embedding_batch()
        except Exception as exc:
            with closing(
                self.index._connect(read_only=True)  # noqa: SLF001
            ) as connection:
                total_chunks = int(
                    connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                )
            return {
                "available": False,
                "status": "unavailable",
                "requested_profile": "",
                "active_profile": "",
                "profile_id": "",
                "model_id": "",
                "model_revision": "",
                "dim": 0,
                "normalized": False,
                "error": _bounded_reason(exc),
                "repair": {},
                "complete": total_chunks == 0,
                "partial": total_chunks > 0,
                "vector_chunks": 0,
                "total_chunks": total_chunks,
                "pending_chunks": total_chunks,
                "chunks_indexed_this_call": 0,
                "last_batch_count": 0,
                "corpus": self.index.corpus_status(),
            }
        result = self._index_build_status(
            batch,
            self._vector_progress(batch, 0),
        )
        result["corpus"] = self.index.corpus_status()
        return result

    def build_batch(
        self,
        max_chunks: int = DEFAULT_VECTOR_BUILD_BATCH,
    ) -> dict[str, Any]:
        """Encode one bounded, resumable batch of missing vector chunks."""

        if (
            isinstance(max_chunks, bool)
            or not isinstance(max_chunks, int)
            or not 1 <= max_chunks <= MAX_VECTOR_BUILD_BATCH
        ):
            raise HybridKnowledgeError(
                f"max_chunks must be between 1 and {MAX_VECTOR_BUILD_BATCH}"
            )
        configured = self._configured_embedding_batch()
        progress = self._vector_progress(configured, 0)
        if progress["complete"]:
            return self._index_build_status(configured, progress)

        seed_rows = self._missing_vector_rows(
            configured,
            max_chunks=1,
        )
        if not seed_rows:
            return self._index_build_status(configured, progress)

        actual = self._encode(
            documents=(str(seed_rows[0][1]),),
            queries=(),
        )
        self._activate_vector_layer(actual)
        indexed = self._store_vectors(
            actual,
            seed_rows,
            actual.document_vectors,
        )
        remaining = max_chunks - indexed
        if remaining > 0:
            progress = self._sync_missing_vectors(
                actual,
                max_chunks=remaining,
            )
            indexed += int(progress["chunks_indexed_this_call"])
        else:
            progress = self._vector_progress(actual, 0)
        progress["chunks_indexed_this_call"] = indexed
        progress["last_batch_count"] = indexed
        return self._index_build_status(actual, progress)

    def refresh_explicit_sources(
        self,
        records: Iterable[NormalizedSource],
        *,
        remove_source_keys: Iterable[str] = (),
        replace_thread_id: str = "",
    ) -> dict[str, Any]:
        """Explicitly upsert normalized user records and reuse vector storage."""

        with self._lock:
            refresh = self.index.refresh_explicit_records(
                records,
                remove_source_keys=remove_source_keys,
                replace_thread_id=replace_thread_id,
            )
            document_ids = tuple(
                int(value) for value in refresh.get("document_ids", ())
            )
            vector = (
                self._vectorize_documents(document_ids)
                if document_ids
                else self.status()
            )
        return {"refresh": refresh, "vector": vector}

    def vectorize_documents(
        self,
        document_ids: Sequence[int],
    ) -> dict[str, Any]:
        """Synchronize vectors for an explicit bounded document selection."""

        selected = tuple(dict.fromkeys(int(value) for value in document_ids))
        if not selected or any(value <= 0 for value in selected):
            raise HybridKnowledgeError(
                "document_ids must contain positive document identifiers"
            )
        with self._lock:
            return self._vectorize_documents(selected)

    def search_many(
        self,
        queries: Sequence[str],
        source_groups: Iterable[str],
        *,
        current_houdini_version: str,
        offset: int,
        limit: int,
        mode: str = "hybrid",
        memory_scope: str = "",
        include_superseded: bool = False,
        allow_index_updates: bool = False,
        source_kinds: Iterable[str] = (),
        card_id: str = "",
        canonical_id: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(allow_index_updates, bool):
            raise HybridKnowledgeError("allow_index_updates must be a boolean")
        requested_mode = str(mode or "hybrid").casefold()
        if requested_mode not in SEARCH_MODES:
            raise HybridKnowledgeError(
                f"Unsupported local retrieval mode: {requested_mode}"
            )
        groups = set(source_groups)
        invalid = groups.difference(SEARCH_SOURCE_GROUPS)
        if invalid:
            raise HybridKnowledgeError(
                f"Unsupported local knowledge sources: {sorted(invalid)!r}"
            )
        kinds = set(source_kinds)
        invalid_kinds = kinds.difference(FILTERABLE_SOURCE_KINDS)
        if invalid_kinds:
            raise HybridKnowledgeError(
                "Unsupported local knowledge source kinds: "
                f"{sorted(invalid_kinds)!r}"
            )
        if not queries:
            return []
        filtered_document_ids = self.index.filtered_document_ids(
            source_kinds=kinds,
            card_id=card_id,
            canonical_id=canonical_id,
        )
        exact_identity = bool(str(card_id).strip() or str(canonical_id).strip())

        lexical_limit = min(
            MAX_VECTOR_CANDIDATES,
            max(20, offset + limit * 4)
            if requested_mode == "lexical"
            else max(200, offset + limit * 4),
        )
        lexical_offset = 0
        fts_started = time.perf_counter()
        lexical_results = [
            self.index.search(
                query,
                groups,
                current_houdini_version=current_houdini_version,
                offset=lexical_offset,
                limit=lexical_limit,
                memory_scope=memory_scope,
                include_superseded=include_superseded,
                document_ids=filtered_document_ids,
                exact_identity=exact_identity,
            )
            for query in queries
        ]
        for query, result in zip(queries, lexical_results):
            raw_matches = [
                _with_source_kind(match)
                for match in result.get("matches", ())
                if isinstance(match, Mapping)
            ]
            result["matches"] = _fold_canonical_matches(
                _rank_lexical_matches(raw_matches, query),
            )
            result["total"] = max(
                0,
                int(result.get("total", 0))
                - (len(raw_matches) - len(result["matches"])),
            )
        timings = {
            "fts_seconds": max(0.0, time.perf_counter() - fts_started),
            "query_encode_seconds": 0.0,
            "vector_scan_seconds": 0.0,
        }
        source_inventory = self.index.corpus_status()
        if requested_mode == "lexical":
            status = self._retrieval_status(
                requested_mode=requested_mode,
                mode_used="lexical",
                vector_available=False,
                timings=timings,
                source_inventory=source_inventory,
            )
            return [
                {
                    **result,
                    "matches": result["matches"][offset : offset + limit],
                    "retrieval": status,
                }
                for result in lexical_results
            ]

        query_batch: _EmbeddingBatch | None = None
        try:
            encode_started = time.perf_counter()
            query_batch = self._encode(documents=(), queries=queries)
            timings["query_encode_seconds"] = max(
                0.0,
                time.perf_counter() - encode_started,
            )
            candidate_document_ids = [
                _lexical_document_ids((result,))
                for result in lexical_results
            ]
            candidate_chunk_ids = _interleave_unique(
                [
                    _lexical_chunk_ids((result,))
                    for result in lexical_results
                ],
                limit=MAX_VECTOR_CANDIDATE_DOCUMENTS,
            )
            if allow_index_updates:
                self._activate_vector_layer(query_batch)
                sync = self._sync_query_candidate_vectors(
                    query_batch,
                    candidate_chunk_ids,
                )
            else:
                sync = self._vector_progress(query_batch, 0)
            if sync["signature_compatible"]:
                vector_started = time.perf_counter()
                vector_rankings = self._stream_vector_rankings(
                    queries,
                    query_batch,
                    groups,
                    memory_scope=memory_scope,
                    include_superseded=include_superseded,
                    top_k=min(
                        MAX_VECTOR_TOP_K,
                        max(MIN_VECTOR_TOP_K, (offset + limit) * 8),
                    ),
                    candidate_document_ids_by_query=(
                        None
                        if sync["complete"]
                        else candidate_document_ids
                    ),
                    allowed_document_ids=filtered_document_ids,
                    current_houdini_version=current_houdini_version,
                )
                timings["vector_scan_seconds"] = max(
                    0.0,
                    time.perf_counter() - vector_started,
                )
            else:
                vector_rankings = [[] for _query in queries]
        except Exception as exc:
            if timings["query_encode_seconds"] == 0.0:
                timings["query_encode_seconds"] = max(
                    0.0,
                    time.perf_counter() - encode_started,
                )
            raise HybridKnowledgeError(
                f"{requested_mode} retrieval requires an available vector encoder: "
                f"{_bounded_reason(exc)}"
            ) from exc

        output: list[dict[str, Any]] = []
        for query, lexical, vector_matches, document_ids in zip(
            queries,
            lexical_results,
            vector_rankings,
            candidate_document_ids,
        ):
            index_status = dict(sync)
            signature_compatible = bool(sync["signature_compatible"])
            if not signature_compatible:
                raise HybridKnowledgeError(
                    f"{requested_mode} retrieval requires a compatible vector index"
                )
            elif sync["complete"]:
                index_status.update(
                    {"ranking_scope": "global", "partial_reason": ""}
                )
                mode_used = requested_mode
            elif document_ids:
                index_status.update(
                    {
                        "ranking_scope": "lexical_candidates",
                        "partial_reason": PARTIAL_CANDIDATES_REASON,
                    }
                )
                mode_used = requested_mode
            else:
                raise HybridKnowledgeError(
                    f"{requested_mode} retrieval requires indexed vector candidates"
                )
            status = self._retrieval_status(
                requested_mode=requested_mode,
                mode_used=mode_used,
                vector_available=signature_compatible,
                batch=query_batch,
                sync=index_status,
                timings=timings,
                source_inventory=source_inventory,
            )
            combined = self._combine_matches(
                lexical["matches"],
                vector_matches,
                mode_used,
                query=query,
            )
            total = len(combined)
            output.append(
                {
                    "matches": combined[offset : offset + limit],
                    "total": total,
                    "tokenizer": lexical["tokenizer"],
                    "retrieval": status,
                }
            )
        return output

    def project_memory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").casefold()
        if action == "record":
            return self._record_memory(arguments)
        if action == "search":
            return self._search_memories(arguments)
        if action == "list":
            return self._list_memories(arguments)
        if action == "delete":
            return self._delete_memory(arguments)
        if action == "supersede":
            return self._supersede_memory(arguments)
        raise HybridKnowledgeError("Unsupported project-memory action")

    def _record_memory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        values = _memory_values(arguments)
        memory_id = f"mem_{uuid.uuid4().hex}"
        now = _utc_now()
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                document_id = self._insert_memory(
                    connection,
                    memory_id=memory_id,
                    values=values,
                    now=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        vector_status = self._vectorize_documents((document_id,))
        return {
            "memory": self._get_memory(memory_id),
            "retrieval": vector_status,
        }

    def _search_memories(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise HybridKnowledgeError("query is required for memory search")
        scope = _scope(arguments)
        mode = str(arguments.get("mode") or "hybrid").casefold()
        offset = _integer(arguments.get("offset", 0), 0, 1_000_000)
        limit = _integer(arguments.get("limit", 20), 1, 100)
        include_superseded = bool(arguments.get("include_superseded", False))
        memory_type = str(arguments.get("memory_type") or "").casefold()
        if memory_type and memory_type not in MEMORY_TYPES:
            raise HybridKnowledgeError("Unsupported memory type")
        tags = _filter_tags(arguments)
        search = self.search_many(
            (query,),
            {"memory"},
            current_houdini_version="",
            offset=0,
            limit=MAX_VECTOR_CANDIDATES,
            mode=mode,
            memory_scope=scope,
            include_superseded=include_superseded,
        )[0]
        matches = [
            match
            for match in search["matches"]
            if _memory_match_filters(match, memory_type, tags)
        ]
        return {
            "query": query,
            "scope": scope,
            "matches": matches[offset : offset + limit],
            "total": len(matches),
            "offset": offset,
            "limit": limit,
            "retrieval": search["retrieval"],
        }

    def _list_memories(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(arguments)
        include_superseded = bool(arguments.get("include_superseded", False))
        memory_type = str(arguments.get("memory_type") or "").casefold()
        if memory_type and memory_type not in MEMORY_TYPES:
            raise HybridKnowledgeError("Unsupported memory type")
        tags = _filter_tags(arguments)
        offset = _integer(arguments.get("offset", 0), 0, 1_000_000)
        limit = _integer(arguments.get("limit", 50), 1, 200)
        conditions = ["scope = ?"]
        parameters: list[Any] = [scope]
        if not include_superseded:
            conditions.append("status = 'active'")
        if memory_type:
            conditions.append("memory_type = ?")
            parameters.append(memory_type)
        where = " AND ".join(conditions)
        with closing(
            self.index._connect(read_only=True)  # noqa: SLF001
        ) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM project_memories WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM project_memories WHERE {where} "
                "ORDER BY updated_at DESC, id",
                parameters,
            ).fetchall()
        items = [
            _memory_row(row)
            for row in rows
            if not tags or tags.issubset(set(_memory_row(row)["tags"]))
        ]
        return {
            "items": items[offset : offset + limit],
            "total": len(items) if tags else total,
            "offset": offset,
            "limit": limit,
            "scope": scope,
        }

    def _delete_memory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        memory_id = _memory_id(arguments)
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT document_id FROM project_memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
                if row is None:
                    raise HybridKnowledgeError("Project memory was not found")
                document_id = int(row[0]) if row[0] is not None else None
                connection.execute(
                    "DELETE FROM project_memories WHERE id = ?",
                    (memory_id,),
                )
                if document_id is not None:
                    self.index._delete_document(  # noqa: SLF001
                        connection,
                        document_id,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"memory_id": memory_id, "deleted": True}

    def _supersede_memory(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        memory_id = _memory_id(arguments)
        values = _memory_values(arguments)
        replacement_id = f"mem_{uuid.uuid4().hex}"
        now = _utc_now()
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, document_id, scope, source_thread_id, "
                    "source_turn_id FROM project_memories "
                    "WHERE id = ?",
                    (memory_id,),
                ).fetchone()
                if row is None:
                    raise HybridKnowledgeError("Project memory was not found")
                if str(row[0]) != "active":
                    raise HybridKnowledgeError(
                        "Only an active project memory can be superseded"
                    )
                if "scope" not in arguments:
                    values["scope"] = str(row[2])
                if "source_thread_id" not in arguments:
                    values["source_thread_id"] = str(row[3])
                if "source_turn_id" not in arguments:
                    values["source_turn_id"] = str(row[4])
                replacement_document_id = self._insert_memory(
                    connection,
                    memory_id=replacement_id,
                    values=values,
                    now=now,
                )
                connection.execute(
                    "UPDATE project_memories SET status = 'superseded', "
                    "superseded_by = ?, updated_at = ? WHERE id = ?",
                    (replacement_id, now, memory_id),
                )
                if row[1] is not None:
                    self._mark_document_superseded(
                        connection,
                        int(row[1]),
                        replacement_id,
                        now,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        vector_status = self._vectorize_documents((replacement_document_id,))
        return {
            "superseded": self._get_memory(memory_id),
            "replacement": self._get_memory(replacement_id),
            "retrieval": vector_status,
        }

    def _insert_memory(
        self,
        connection: Any,
        *,
        memory_id: str,
        values: Mapping[str, Any],
        now: str,
    ) -> int:
        normalized = normalize_project_memory(
            {
                "stable_id": memory_id,
                "memory_type": values["memory_type"],
                "title": values["title"],
                "body": values["body"],
                "tags": values["tags"],
                "scope": values["scope"],
                "status": "active",
                "source_thread_id": values["source_thread_id"],
                "source_turn_id": values["source_turn_id"],
                "created_at": now,
                "updated_at": now,
            }
        )
        record = normalized.records[0]
        attributes = {
            **dict(record.metadata),
            **dict(record.provenance),
            "memory_id": memory_id,
            "memory_type": values["memory_type"],
            "tags": values["tags"],
            "scope": values["scope"],
            "status": "active",
            "superseded_by": "",
            "source_thread_id": values["source_thread_id"],
            "source_turn_id": values["source_turn_id"],
            "created_at": now,
            "updated_at": now,
        }
        text = record.text
        candidate = _Candidate(
            collection="memory",
            source_group="memory",
            source="project_memory",
            source_key=record.source_key,
            title=record.title,
            source_path="",
            url="",
            author="Explicit project memory",
            houdini_version="any",
            license_name="project-private",
            verification=record.verification,
            evidence=(
                "Explicit user-directed project memory; not independently "
                "verified"
            ),
            attributes=attributes,
            inline_text=text,
        )
        metadata = {
            "url": "",
            "author": candidate.author,
            "accessed_at": now,
            "houdini_version": "any",
            "license": "project-private",
            "verification": record.verification,
            "evidence": candidate.evidence,
            "attributes": attributes,
        }
        metadata_json = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.index._replace_document(  # noqa: SLF001
            connection,
            candidate,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            hashlib.sha256(metadata_json.encode("utf-8")).hexdigest(),
            metadata,
            None,
        )
        document_id = int(
            connection.execute(
                "SELECT id FROM documents WHERE source_key = ?",
                (candidate.source_key,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO project_memories(
                id, document_id, memory_type, title, body, tags_json, scope,
                status, superseded_by, created_at, updated_at,
                source_thread_id, source_turn_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'active', '', ?, ?, ?, ?)
            """,
            (
                memory_id,
                document_id,
                values["memory_type"],
                values["title"],
                values["body"],
                json.dumps(values["tags"], ensure_ascii=False),
                values["scope"],
                now,
                now,
                values["source_thread_id"],
                values["source_turn_id"],
            ),
        )
        return document_id

    @staticmethod
    def _mark_document_superseded(
        connection: Any,
        document_id: int,
        replacement_id: str,
        now: str,
    ) -> None:
        row = connection.execute(
            "SELECT attributes_json FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return
        try:
            attributes = json.loads(str(row[0]) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            attributes = {}
        if not isinstance(attributes, dict):
            attributes = {}
        attributes.update(
            {
                "status": "superseded",
                "superseded_by": replacement_id,
                "updated_at": now,
            }
        )
        attributes_json = json.dumps(
            attributes,
            ensure_ascii=False,
            sort_keys=True,
        )
        connection.execute(
            "UPDATE documents SET attributes_json = ?, "
            "metadata_sha256 = ?, indexed_at = ? WHERE id = ?",
            (
                attributes_json,
                hashlib.sha256(attributes_json.encode("utf-8")).hexdigest(),
                now,
                document_id,
            ),
        )

    def _get_memory(self, memory_id: str) -> dict[str, Any]:
        with closing(
            self.index._connect(read_only=True)  # noqa: SLF001
        ) as connection:
            row = connection.execute(
                "SELECT * FROM project_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise HybridKnowledgeError("Project memory was not found")
        return _memory_row(row)

    def _embedder_instance(self) -> Any | None:
        with self._lock:
            if self._embedder_initialized:
                return self._embedder
            self._embedder_initialized = True
            try:
                from .embedding_client import EmbeddingClient

                self._embedder = EmbeddingClient.from_environment(
                    self.project_root
                )
            except Exception as exc:
                self._embedder_error = _bounded_reason(exc)
                self._embedder = None
            return self._embedder

    def _configured_embedding_batch(self) -> _EmbeddingBatch:
        embedder = self._embedder_instance()
        if embedder is None:
            raise HybridKnowledgeError(
                self._embedder_error
                or "No installed local embedding profile is configured"
            )
        status_method = getattr(embedder, "status", None)
        if not callable(status_method):
            raise HybridKnowledgeError(
                "The embedding runtime does not expose status"
            )
        raw = status_method()
        if not isinstance(raw, Mapping):
            raise HybridKnowledgeError(
                "The embedding runtime returned invalid status"
            )
        return _embedding_batch(raw)

    def _encode(
        self,
        *,
        documents: Sequence[str],
        queries: Sequence[str],
    ) -> _EmbeddingBatch:
        embedder = self._embedder_instance()
        if embedder is None:
            raise _VectorUnavailable(
                self._embedder_error
                or "No installed local embedding profile is configured"
            )
        raw = embedder.encode(
            documents=list(documents),
            queries=list(queries),
        )
        batch = _embedding_batch(raw)
        if len(batch.document_vectors) != len(documents):
            raise _VectorUnavailable(
                "Embedding worker returned the wrong document vector count"
            )
        if len(batch.query_vectors) != len(queries):
            raise _VectorUnavailable(
                "Embedding worker returned the wrong query vector count"
            )
        return batch

    def _activate_vector_layer(self, batch: _EmbeddingBatch) -> None:
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                active = self.index._meta_value_from_connection(  # noqa: SLF001
                    connection,
                    "active_vector_signature",
                )
                if active != batch.signature:
                    connection.execute("DELETE FROM chunk_vectors")
                    self.index._set_meta(  # noqa: SLF001
                        connection,
                        "active_vector_signature",
                        batch.signature,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _missing_vector_rows(
        self,
        batch: _EmbeddingBatch,
        *,
        document_ids: Sequence[int] = (),
        chunk_ids: Sequence[int] = (),
        max_chunks: int | None = None,
    ) -> list[Sequence[Any]]:
        parameters: list[Any] = []
        if document_ids:
            placeholders = ",".join("?" for _value in document_ids)
            filters = [f"c.document_id IN ({placeholders})"]
            parameters.extend(int(value) for value in document_ids)
        else:
            filters = []
        if chunk_ids:
            placeholders = ",".join("?" for _value in chunk_ids)
            filters.append(f"c.id IN ({placeholders})")
            parameters.extend(int(value) for value in chunk_ids)
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            active = self.index._meta_value_from_connection(  # noqa: SLF001
                connection,
                "active_vector_signature",
            )
            if active == batch.signature:
                corrupt = connection.execute(
                    "SELECT COUNT(*) FROM chunk_vectors "
                    "WHERE model_id = ? AND (dim <> ? OR normalized <> 1)",
                    (batch.model_id, batch.dim),
                ).fetchone()[0]
                if int(corrupt):
                    raise _VectorUnavailable(
                        "Stored vector dimension or normalization is corrupt"
                    )
                filters.insert(
                    0,
                    "(cv.chunk_id IS NULL OR cv.content_hash <> c.content_hash)",
                )
                sql = (
                    "SELECT c.id, c.body, c.content_hash FROM chunks c "
                    "LEFT JOIN chunk_vectors cv "
                    "ON cv.chunk_id = c.id AND cv.model_id = ? WHERE "
                )
                parameters.insert(0, batch.model_id)
            else:
                # A profile/model/dimension switch invalidates only the vector
                # layer.  Treat every scoped chunk as missing until encode()
                # confirms the exact requested runtime profile.
                sql = (
                    "SELECT c.id, c.body, c.content_hash FROM chunks c WHERE "
                )
            sql += " AND ".join(filters or ("1 = 1",)) + " ORDER BY c.id"
            if max_chunks is not None:
                if max_chunks <= 0:
                    rows = []
                else:
                    rows = connection.execute(
                        sql + " LIMIT ?",
                        [*parameters, int(max_chunks)],
                    ).fetchall()
            else:
                rows = connection.execute(sql, parameters).fetchall()
        return list(rows)

    def _sync_missing_vectors(
        self,
        batch: _EmbeddingBatch,
        *,
        document_ids: Sequence[int] = (),
        chunk_ids: Sequence[int] = (),
        max_chunks: int | None = None,
    ) -> dict[str, Any]:
        rows = self._missing_vector_rows(
            batch,
            document_ids=document_ids,
            chunk_ids=chunk_ids,
            max_chunks=max_chunks,
        )
        indexed = 0
        for start in range(0, len(rows), VECTOR_BATCH_SIZE):
            part = rows[start : start + VECTOR_BATCH_SIZE]
            encoded = self._encode(
                documents=[str(row[1]) for row in part],
                queries=(),
            )
            if encoded.signature != batch.signature:
                raise _VectorUnavailable(
                    "Embedding profile changed while vectors were being indexed"
                )
            indexed += self._store_vectors(
                batch,
                part,
                encoded.document_vectors,
            )
        return self._vector_progress(batch, indexed)

    def _sync_query_candidate_vectors(
        self,
        batch: _EmbeddingBatch,
        candidate_chunk_ids: Sequence[int],
    ) -> dict[str, Any]:
        if not candidate_chunk_ids:
            return self._vector_progress(batch, 0)
        return self._sync_missing_vectors(
            batch,
            chunk_ids=candidate_chunk_ids[:MAX_VECTOR_CANDIDATE_DOCUMENTS],
            max_chunks=MAX_QUERY_VECTOR_SYNCS,
        )

    def _vector_progress(
        self,
        batch: _EmbeddingBatch,
        indexed_this_call: int,
    ) -> dict[str, Any]:
        with closing(
            self.index._connect(read_only=True)  # noqa: SLF001
        ) as connection:
            total_chunks = int(
                connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            )
            active_signature = self.index._meta_value_from_connection(  # noqa: SLF001
                connection,
                "active_vector_signature",
            )
            signature_compatible = active_signature == batch.signature
            if signature_compatible:
                corrupt = connection.execute(
                    "SELECT COUNT(*) FROM chunk_vectors "
                    "WHERE model_id = ? AND (dim <> ? OR normalized <> 1)",
                    (batch.model_id, batch.dim),
                ).fetchone()[0]
                if int(corrupt):
                    raise _VectorUnavailable(
                        "Stored vector dimension or normalization is corrupt"
                    )
            vector_chunks = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chunk_vectors cv "
                        "JOIN chunks c ON c.id = cv.chunk_id "
                        "WHERE cv.model_id = ? AND cv.dim = ? "
                        "AND cv.normalized = 1 "
                        "AND cv.content_hash = c.content_hash",
                        (batch.model_id, batch.dim),
                    ).fetchone()[0]
                )
                if signature_compatible
                else 0
            )
        pending_chunks = max(0, total_chunks - vector_chunks)
        return {
            "complete": pending_chunks == 0,
            "partial": pending_chunks > 0,
            "signature_compatible": signature_compatible,
            "vector_chunks": vector_chunks,
            "total_chunks": total_chunks,
            "pending_chunks": pending_chunks,
            "chunks_indexed_this_call": int(indexed_this_call),
            "last_batch_count": int(indexed_this_call),
        }

    @staticmethod
    def _index_build_status(
        batch: _EmbeddingBatch,
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "available": True,
            "status": batch.status,
            "requested_profile": batch.requested_profile,
            "active_profile": batch.profile_id,
            "profile_id": batch.profile_id,
            "model_id": batch.model_id,
            "model_revision": batch.model_revision,
            "dim": batch.dim,
            "normalized": batch.normalized,
            "repair": dict(batch.repair),
            **dict(progress),
        }

    def _vectorize_documents(
        self,
        document_ids: Sequence[int],
    ) -> dict[str, Any]:
        try:
            with closing(self.index._connect()) as connection:  # noqa: SLF001
                row = connection.execute(
                    "SELECT id, body, content_hash FROM chunks "
                    "WHERE document_id = ? "
                    "ORDER BY ordinal LIMIT 1",
                    (int(document_ids[0]),),
                ).fetchone()
            if row is None:
                raise _VectorUnavailable("Memory document has no searchable text")
            first = self._encode(documents=(str(row[1]),), queries=())
            self._activate_vector_layer(first)
            seeded = self._store_vectors(
                first,
                (row,),
                first.document_vectors,
            )
            sync = self._sync_missing_vectors(
                first,
                document_ids=document_ids,
            )
            sync["chunks_indexed_this_call"] = (
                int(sync["chunks_indexed_this_call"]) + seeded
            )
            return self._retrieval_status(
                requested_mode="hybrid",
                mode_used="hybrid",
                vector_available=True,
                batch=first,
                sync=sync,
            )
        except Exception as exc:
            raise HybridKnowledgeError(
                "Vector indexing requires an available encoder: "
                f"{_bounded_reason(exc)}"
            ) from exc

    def _store_vectors(
        self,
        batch: _EmbeddingBatch,
        rows: Sequence[Sequence[Any]],
        vectors: Sequence[Sequence[float]],
    ) -> int:
        indexed = 0
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row, vector in zip(rows, vectors):
                    current = connection.execute(
                        "SELECT content_hash FROM chunks WHERE id = ?",
                        (int(row[0]),),
                    ).fetchone()
                    if current is None or str(current[0]) != str(row[2]):
                        continue
                    connection.execute(
                        """
                        INSERT INTO chunk_vectors(
                            chunk_id, model_id, profile_id, dim,
                            content_hash, normalized, vector_blob,
                            indexed_at
                        ) VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                        ON CONFLICT(chunk_id, model_id) DO UPDATE SET
                            profile_id = excluded.profile_id,
                            dim = excluded.dim,
                            content_hash = excluded.content_hash,
                            normalized = 1,
                            vector_blob = excluded.vector_blob,
                            indexed_at = excluded.indexed_at
                        """,
                        (
                            int(row[0]),
                            batch.model_id,
                            batch.profile_id,
                            batch.dim,
                            str(row[2]),
                            _vector_blob(vector),
                            _utc_now(),
                        ),
                    )
                    indexed += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return indexed

    def _stream_vector_rankings(
        self,
        queries: Sequence[str],
        batch: _EmbeddingBatch,
        source_groups: set[str],
        *,
        memory_scope: str,
        include_superseded: bool,
        top_k: int,
        candidate_document_ids_by_query: (
            Sequence[Sequence[int]] | None
        ) = None,
        allowed_document_ids: Sequence[int] | None = None,
        current_houdini_version: str = "",
    ) -> list[list[dict[str, Any]]]:
        groups = sorted(source_groups)
        placeholders = ",".join("?" for _value in groups)
        conditions = [
            "cv.model_id = ?",
            "cv.dim = ?",
            "cv.normalized = 1",
            "cv.content_hash = c.content_hash",
            f"d.source_group IN ({placeholders})",
        ]
        parameters: list[Any] = [batch.model_id, batch.dim, *groups]
        if allowed_document_ids is not None:
            allowed_ids = tuple(
                dict.fromkeys(int(value) for value in allowed_document_ids)
            )
            if not allowed_ids:
                return [[] for _query in queries]
            placeholders = ",".join("?" for _value in allowed_ids)
            conditions.append(f"d.id IN ({placeholders})")
            parameters.extend(allowed_ids)
        candidate_sets: list[frozenset[int]] | None = None
        if candidate_document_ids_by_query is not None:
            if len(candidate_document_ids_by_query) != len(queries):
                raise _VectorUnavailable(
                    "Vector candidate scopes do not match the query batch"
                )
            candidate_lists = [
                [int(value) for value in values]
                for values in candidate_document_ids_by_query
            ]
            candidate_union = _interleave_unique(
                candidate_lists,
                limit=MAX_VECTOR_CANDIDATE_DOCUMENTS,
            )
            if not candidate_union:
                return [[] for _query in queries]
            allowed_candidates = frozenset(candidate_union)
            candidate_sets = [
                frozenset(values).intersection(allowed_candidates)
                for values in candidate_lists
            ]
            placeholders = ",".join("?" for _value in candidate_union)
            conditions.append(f"d.id IN ({placeholders})")
            parameters.extend(candidate_union)
        if "memory" in groups:
            memory = [
                "pm.document_id = d.id",
            ]
            if not include_superseded:
                memory.append("pm.status = 'active'")
            if memory_scope:
                memory.append("pm.scope = ?")
                parameters.append(memory_scope)
            conditions.append(
                "(d.source_group <> 'memory' OR EXISTS (SELECT 1 "
                "FROM project_memories pm WHERE "
                + " AND ".join(memory)
                + "))"
            )
        heaps: list[list[tuple[float, int, int, int]]] = [
            [] for _query in queries
        ]
        serial = 0
        with closing(
            self.index._connect(read_only=True)  # noqa: SLF001
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            cursor = connection.execute(
                """
                SELECT d.id AS document_id, c.id AS chunk_id, c.ordinal,
                       cv.vector_blob
                FROM chunk_vectors cv
                JOIN chunks c ON c.id = cv.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE
                """
                + " AND ".join(conditions)
                + " ORDER BY d.id, c.ordinal",
                parameters,
            )
            for row in cursor:
                vector = _decode_vector(row["vector_blob"], batch.dim)
                serial += 1
                document_id = int(row["document_id"])
                chunk_id = int(row["chunk_id"])
                for index, query_vector in enumerate(batch.query_vectors):
                    if (
                        candidate_sets is not None
                        and document_id not in candidate_sets[index]
                    ):
                        continue
                    score = math.fsum(
                        map(operator.mul, query_vector, vector)
                    )
                    heap = heaps[index]
                    entry = (score, serial, document_id, chunk_id)
                    if len(heap) < top_k:
                        heapq.heappush(heap, entry)
                    elif score > heap[0][0]:
                        heapq.heapreplace(heap, entry)

            ranked_entries = [
                sorted(heap, reverse=True)
                for heap in heaps
            ]
            selected_chunk_ids = tuple(
                dict.fromkeys(
                    chunk_id
                    for entries in ranked_entries
                    for _score, _serial, _document_id, chunk_id in entries
                )
            )
            rows_by_chunk: dict[int, sqlite3.Row] = {}
            for start in range(
                0,
                len(selected_chunk_ids),
                VECTOR_HYDRATE_BATCH_SIZE,
            ):
                chunk_ids = selected_chunk_ids[
                    start : start + VECTOR_HYDRATE_BATCH_SIZE
                ]
                placeholders = ",".join("?" for _value in chunk_ids)
                hydrated = connection.execute(
                    """
                    SELECT d.id, d.source, d.title, d.source_path, d.url,
                           d.author, d.accessed_at, d.houdini_version,
                           d.license, d.verification, d.evidence, d.sha256,
                           d.source_key, d.collection, d.attributes_json,
                           c.id AS chunk_id, c.ordinal, c.body,
                           c.content_hash AS chunk_hash
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE c.id IN (
                    """
                    + placeholders
                    + ")",
                    chunk_ids,
                )
                for row in hydrated:
                    rows_by_chunk[int(row["chunk_id"])] = row

        rankings: list[list[dict[str, Any]]] = []
        for query, entries in zip(queries, ranked_entries):
            matches: list[dict[str, Any]] = []
            seen_documents: set[int] = set()
            for score, _serial, document_id, chunk_id in entries:
                if document_id in seen_documents:
                    continue
                row = rows_by_chunk.get(chunk_id)
                if row is None:
                    continue
                seen_documents.add(document_id)
                matches.append(
                    self._vector_match(
                        row,
                        query=query,
                        score=float(score),
                        current_houdini_version=current_houdini_version,
                    )
                )
            rankings.append(matches)
        return rankings

    @staticmethod
    def _vector_match(
        row: sqlite3.Row,
        *,
        query: str,
        score: float,
        current_houdini_version: str = "",
    ) -> dict[str, Any]:
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
                "current_houdini_version": current_houdini_version,
                "current_version_match": _houdini_versions_match(
                    str(row["houdini_version"]),
                    current_houdini_version,
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
                "vector_score": score,
            }
        )
        return _with_source_kind({
            "source": row["source"],
            "title": row["title"],
            "snippet": _matching_snippet(row["body"], query),
            "metadata": metadata,
            "vector_score": score,
        })

    @staticmethod
    def _combine_matches(
        lexical_matches: Sequence[Mapping[str, Any]],
        vector_matches: Sequence[Mapping[str, Any]],
        mode: str,
        *,
        query: str = "",
    ) -> list[dict[str, Any]]:
        if mode == "vector":
            selected = [_with_source_kind(value) for value in vector_matches]
            for rank, value in enumerate(selected, 1):
                value["metadata"] = dict(value.get("metadata") or {})
                value["_score"] = 1.0 / (RRF_K + rank)
            by_key = {
                str(
                    value["metadata"].get("source_key")
                    or f"{value.get('source')}:{value.get('title')}"
                ): value
                for value in selected
            }
        else:
            by_key: dict[str, dict[str, Any]] = {}
            for rank, raw in enumerate(lexical_matches, 1):
                value = _with_source_kind(raw)
                metadata = dict(value.get("metadata") or {})
                key = str(
                    metadata.get("source_key")
                    or f"{value.get('source')}:{value.get('title')}"
                )
                by_key[key] = {
                    **value,
                    "metadata": metadata,
                    "_score": 1.0 / (RRF_K + rank),
                }
            for rank, raw in enumerate(vector_matches, 1):
                value = _with_source_kind(raw)
                metadata = dict(value.get("metadata") or {})
                key = str(
                    metadata.get("source_key")
                    or f"{value.get('source')}:{value.get('title')}"
                )
                score = 1.0 / (RRF_K + rank)
                if key in by_key:
                    existing = by_key[key]
                    existing["_score"] += score
                    existing_metadata = existing["metadata"]
                    existing_metadata["vector_score"] = metadata.get(
                        "vector_score"
                    )
                    if not existing.get("snippet"):
                        existing["snippet"] = value.get("snippet")
                else:
                    by_key[key] = {
                        **value,
                        "metadata": metadata,
                        "_score": score,
                    }

        intent = _query_intent(query, tuple(by_key.values()))
        for value in by_key.values():
            bonus = _source_intent_bonus(intent, value["source_kind"])
            value["_rank_score"] = float(value["_score"]) + bonus
        combined = sorted(
            by_key.values(),
            key=lambda value: (
                _exact_identity_tier(query, value),
                -float(value["_rank_score"]),
                _pack_version_sort_key(value["metadata"].get("pack_version")),
                str(value.get("title", "")).casefold(),
                str(value["metadata"].get("source_key") or ""),
            ),
        )
        folded: list[dict[str, Any]] = []
        seen_canonical: set[tuple[str, str]] = set()
        seen_content_hashes: set[str] = set()
        for value in combined:
            metadata = value["metadata"]
            content_sha256 = str(metadata.get("sha256") or "").casefold()
            if content_sha256 and content_sha256 in seen_content_hashes:
                continue
            canonical_id = str(metadata.get("canonical_id") or "").strip()
            if canonical_id:
                canonical_key = (
                    str(value.get("source_kind") or "").casefold(),
                    canonical_id.casefold(),
                )
                if canonical_key in seen_canonical:
                    continue
                seen_canonical.add(canonical_key)
            if content_sha256:
                seen_content_hashes.add(content_sha256)
            score = float(value.pop("_rank_score"))
            value.pop("_score", None)
            value["metadata"]["hybrid_score"] = score
            value["provenance"] = _provenance(
                value["metadata"],
                source_kind=value["source_kind"],
            )
            folded.append(value)
        return folded

    def _retrieval_status(
        self,
        *,
        requested_mode: str,
        mode_used: str,
        vector_available: bool,
        batch: _EmbeddingBatch | None = None,
        sync: Mapping[str, Any] | None = None,
        timings: Mapping[str, Any] | None = None,
        source_inventory: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        corpus = dict(sync or {})
        signature_compatible = bool(
            corpus.get("signature_compatible", batch is not None)
        )
        partial = bool(corpus) and not bool(corpus.get("complete", False))
        if batch is None:
            encoder = {
                "available": False,
                "status": "not_requested",
                "installed": False,
                "ready": False,
                "initialized": False,
                "loaded": False,
                "requested_profile": "",
                "active_profile": "",
                "model_id": "",
                "model_revision": "",
                "dim": 0,
                "normalized": False,
                "repair": {},
            }
        else:
            encoder = {
                "available": True,
                "status": batch.status,
                "installed": True,
                "ready": True,
                "initialized": True,
                "loaded": True,
                "requested_profile": batch.requested_profile,
                "active_profile": batch.profile_id,
                "model_id": batch.model_id,
                "model_revision": batch.model_revision,
                "dim": batch.dim,
                "normalized": batch.normalized,
                "repair": dict(batch.repair),
            }
        vector_state = (
            "incompatible"
            if batch is not None and not signature_compatible
            else "partial"
            if vector_available and partial
            else "ready"
            if vector_available
            else "not_requested"
        )
        corpus_state = (
            "incompatible"
            if batch is not None and not signature_compatible
            else "complete"
            if bool(corpus.get("complete", False))
            else "partial"
            if partial
            else "ready"
        )
        corpus_status = {
            **corpus,
            "available": True,
            "lexical_available": True,
            "vector_available": vector_available,
            "state": corpus_state,
            "global_recall": bool(
                vector_available
                and signature_compatible
                and corpus.get("complete", False)
            ),
            "inventory": dict(source_inventory or {}),
        }
        return {
            "requested_mode": requested_mode,
            "mode_used": mode_used,
            "lexical": {"available": True, "engine": "SQLite FTS5"},
            "encoder": encoder,
            "corpus": corpus_status,
            "vector": {
                "available": vector_available,
                "state": vector_state,
                "status": encoder["status"],
                "installed": encoder["installed"],
                "ready": encoder["ready"],
                "partial": partial,
                "initialized": encoder["initialized"],
                "loaded": encoder["loaded"],
                "requested_profile": encoder["requested_profile"],
                "active_profile": encoder["active_profile"],
                "model_id": encoder["model_id"],
                "model_revision": encoder["model_revision"],
                "dim": encoder["dim"],
                "normalized": encoder["normalized"],
                "repair": encoder["repair"],
                "index": corpus,
            },
            "timings": {
                name: max(0.0, float((timings or {}).get(name) or 0.0))
                for name in (
                    "fts_seconds",
                    "query_encode_seconds",
                    "vector_scan_seconds",
                )
            },
        }


def _embedding_batch(value: Any) -> _EmbeddingBatch:
    def field(name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    dim = int(field("dim", 0))
    if not 32 <= dim <= 4096:
        raise _VectorUnavailable("Embedding worker returned an invalid dimension")
    normalized = bool(field("normalized", False))
    if not normalized:
        raise _VectorUnavailable(
            "Embedding worker did not return normalized vectors"
        )

    def vectors(name: str) -> tuple[tuple[float, ...], ...]:
        raw_vectors = field(name, ())
        if not isinstance(raw_vectors, (list, tuple)):
            raise _VectorUnavailable("Embedding worker vectors are malformed")
        output = []
        for raw in raw_vectors:
            if not isinstance(raw, (list, tuple)) or len(raw) != dim:
                raise _VectorUnavailable(
                    "Embedding worker vector dimension does not match"
                )
            vector = tuple(float(component) for component in raw)
            if not all(math.isfinite(component) for component in vector):
                raise _VectorUnavailable(
                    "Embedding worker returned a non-finite vector"
                )
            norm = math.sqrt(math.fsum(component * component for component in vector))
            if not 0.98 <= norm <= 1.02:
                raise _VectorUnavailable(
                    "Embedding worker vector is not L2-normalized"
                )
            output.append(vector)
        return tuple(output)

    model_id = str(field("model_id") or "")
    profile_id = str(field("profile_id") or field("active_profile") or "")
    if not model_id or not profile_id:
        raise _VectorUnavailable("Embedding worker omitted its model identity")
    requested_profile = str(field("requested_profile") or profile_id)
    if requested_profile != profile_id:
        raise _VectorUnavailable(
            "Embedding worker did not activate the requested profile"
        )
    status = str(field("status") or "ready")
    if status != "ready":
        raise _VectorUnavailable(
            "Embedding worker is not ready for the requested profile: " + status
        )
    repair = field("repair", {})
    return _EmbeddingBatch(
        document_vectors=vectors("document_vectors"),
        query_vectors=vectors("query_vectors"),
        model_id=model_id,
        model_revision=str(field("model_revision") or ""),
        profile_id=profile_id,
        requested_profile=requested_profile,
        dim=dim,
        normalized=True,
        status=status,
        repair=dict(repair) if isinstance(repair, Mapping) else {},
    )


def _lexical_document_ids(
    results: Sequence[Mapping[str, Any]],
) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for result in results:
        matches = result.get("matches")
        if not isinstance(matches, (list, tuple)):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            metadata = match.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            value = metadata.get("document_id")
            if isinstance(value, bool):
                continue
            try:
                document_id = int(value)
            except (TypeError, ValueError):
                continue
            if document_id > 0 and document_id not in seen:
                seen.add(document_id)
                output.append(document_id)
                if len(output) >= MAX_VECTOR_CANDIDATE_DOCUMENTS:
                    return output
    return output


def _lexical_chunk_ids(
    results: Sequence[Mapping[str, Any]],
) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for result in results:
        matches = result.get("matches")
        if not isinstance(matches, (list, tuple)):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            metadata = match.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            value = metadata.get("chunk_id")
            if isinstance(value, bool):
                continue
            try:
                chunk_id = int(value)
            except (TypeError, ValueError):
                continue
            if chunk_id > 0 and chunk_id not in seen:
                seen.add(chunk_id)
                output.append(chunk_id)
                if len(output) >= MAX_VECTOR_CANDIDATE_DOCUMENTS:
                    return output
    return output


def _interleave_unique(
    values_by_query: Sequence[Sequence[int]],
    *,
    limit: int,
) -> list[int]:
    """Round-robin candidate ids so one batched query cannot starve another."""

    output: list[int] = []
    seen: set[int] = set()
    max_length = max((len(values) for values in values_by_query), default=0)
    for position in range(max_length):
        for values in values_by_query:
            if position >= len(values):
                continue
            value = int(values[position])
            if value in seen:
                continue
            seen.add(value)
            output.append(value)
            if len(output) >= limit:
                return output
    return output


def _vector_blob(vector: Sequence[float]) -> bytes:
    values = array("f", (float(value) for value in vector))
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _decode_vector(raw: Any, dim: int) -> array:
    blob = bytes(raw)
    if len(blob) != dim * 4:
        raise _VectorUnavailable("Stored vector dimension is corrupt")
    values = array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    if not all(map(math.isfinite, values)):
        raise _VectorUnavailable("Stored vector contains non-finite values")
    norm = math.sqrt(math.fsum(map(operator.mul, values, values)))
    if not 0.95 <= norm <= 1.05:
        raise _VectorUnavailable("Stored vector normalization is corrupt")
    return values


def _memory_values(arguments: Mapping[str, Any]) -> dict[str, Any]:
    memory_type = str(arguments.get("memory_type") or "").casefold()
    if memory_type not in MEMORY_TYPES:
        raise HybridKnowledgeError("A supported memory_type is required")
    title = str(arguments.get("title") or "").strip()
    body = str(arguments.get("body") or "").strip()
    if not title or len(title) > 512:
        raise HybridKnowledgeError("Memory title must contain 1 to 512 characters")
    if not body or len(body) > 65_536:
        raise HybridKnowledgeError("Memory body must contain 1 to 65536 characters")
    raw_tags = arguments.get("tags", [])
    if not isinstance(raw_tags, (list, tuple)) or len(raw_tags) > 32:
        raise HybridKnowledgeError("Memory tags must be an array of at most 32 items")
    tags = []
    for value in raw_tags:
        tag = str(value).strip()
        if not tag or len(tag) > 128:
            raise HybridKnowledgeError(
                "Each memory tag must contain 1 to 128 characters"
            )
        if tag not in tags:
            tags.append(tag)
    return {
        "memory_type": memory_type,
        "title": title,
        "body": body,
        "tags": tags,
        "scope": _scope(arguments),
        "source_thread_id": _bounded_identifier(
            arguments.get("source_thread_id", "")
        ),
        "source_turn_id": _bounded_identifier(
            arguments.get("source_turn_id", "")
        ),
    }


def _filter_tags(arguments: Mapping[str, Any]) -> set[str]:
    raw_tags = arguments.get("tags", [])
    if not isinstance(raw_tags, (list, tuple)) or len(raw_tags) > 32:
        raise HybridKnowledgeError(
            "Memory tags must be an array of at most 32 items"
        )
    output: set[str] = set()
    for value in raw_tags:
        tag = str(value).strip()
        if not tag or len(tag) > 128:
            raise HybridKnowledgeError(
                "Each memory tag must contain 1 to 128 characters"
            )
        output.add(tag)
    return output


def _memory_match_filters(
    match: Mapping[str, Any],
    memory_type: str,
    tags: set[str],
) -> bool:
    metadata = match.get("metadata")
    if not isinstance(metadata, Mapping):
        return not memory_type and not tags
    if memory_type and str(metadata.get("memory_type") or "") != memory_type:
        return False
    raw_tags = metadata.get("tags")
    match_tags = (
        {str(value) for value in raw_tags}
        if isinstance(raw_tags, (list, tuple))
        else set()
    )
    return tags.issubset(match_tags)


def _memory_id(arguments: Mapping[str, Any]) -> str:
    value = str(arguments.get("memory_id") or "")
    if not value.startswith("mem_") or len(value) != 36:
        raise HybridKnowledgeError("A valid generated memory_id is required")
    try:
        int(value[4:], 16)
    except ValueError as exc:
        raise HybridKnowledgeError(
            "A valid generated memory_id is required"
        ) from exc
    return value


def _scope(arguments: Mapping[str, Any]) -> str:
    value = str(arguments.get("scope") or "project").strip()
    if not value or len(value) > 256:
        raise HybridKnowledgeError("scope must contain 1 to 256 characters")
    return value


def _bounded_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 256:
        raise HybridKnowledgeError(
            "Source thread and turn identifiers are limited to 256 characters"
        )
    return text


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise HybridKnowledgeError("Pagination values must be integers")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HybridKnowledgeError("Pagination values must be integers") from exc
    if not minimum <= parsed <= maximum:
        raise HybridKnowledgeError("Pagination value is outside its allowed range")
    return parsed


def _memory_row(row: Sequence[Any]) -> dict[str, Any]:
    try:
        tags = json.loads(str(row[5]) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        tags = []
    return {
        "id": str(row[0]),
        "memory_type": str(row[2]),
        "title": str(row[3]),
        "body": str(row[4]),
        "tags": tags if isinstance(tags, list) else [],
        "scope": str(row[6]),
        "status": str(row[7]),
        "superseded_by": str(row[8]),
        "created_at": str(row[9]),
        "updated_at": str(row[10]),
        "source_thread_id": str(row[11]),
        "source_turn_id": str(row[12]),
    }


def _with_source_kind(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["source_kind"] = _SOURCE_KINDS.get(
        str(result.get("source") or ""),
        "project_reference",
    )
    return result


def _fold_canonical_matches(
    matches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_content_hashes: set[str] = set()
    for raw in matches:
        value = dict(raw)
        metadata = value.get("metadata")
        details = metadata if isinstance(metadata, Mapping) else {}
        content_sha256 = str(details.get("sha256") or "").casefold()
        if content_sha256 and content_sha256 in seen_content_hashes:
            continue
        canonical_id = str(details.get("canonical_id") or "").strip()
        if canonical_id:
            key = (
                str(value.get("source_kind") or "").casefold(),
                canonical_id.casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
        if content_sha256:
            seen_content_hashes.add(content_sha256)
        output.append(value)
    return output


def _query_intent(
    query: str,
    matches: Sequence[Mapping[str, Any]],
) -> str:
    folded = query.strip().casefold()
    query_identifier = _node_identifier(folded)
    if query_identifier and any(
        match.get("source_kind") == "live_node_catalog"
        and _node_identifier(str(match.get("title") or ""))
        == query_identifier
        for match in matches
    ):
        return "exact_node"
    if _matches_intent(folded, _MEMORY_INTENT_TERMS):
        return "memory"
    if _matches_intent(folded, _USER_SOURCE_INTENT_TERMS):
        return "user_source"
    if _matches_intent(folded, _WORKFLOW_INTENT_TERMS):
        return "workflow"
    return ""


def _rank_lexical_matches(
    matches: Sequence[Mapping[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    prepared = [dict(value) for value in matches]
    intent = _query_intent(query, prepared)
    ranked = [
        (
            _exact_identity_tier(query, value),
            -(
                1.0 / (RRF_K + rank)
                + _source_intent_bonus(
                    intent,
                    str(value.get("source_kind") or ""),
                )
            ),
            index,
            str(value.get("title") or "").casefold(),
            value,
        )
        for index, (rank, value) in enumerate(zip(range(1, len(prepared) + 1), prepared))
    ]
    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [item[4] for item in ranked]


def _exact_identity_tier(
    query: str,
    value: Mapping[str, Any],
) -> int:
    query_key = str(query).strip().casefold()
    if not query_key:
        return 1
    identities = {str(value.get("title") or "").strip().casefold()}
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        for name in ("card_id", "canonical_id"):
            identity = str(metadata.get(name) or "").strip().casefold()
            if identity:
                identities.add(identity)
        aliases = metadata.get("aliases")
        if isinstance(aliases, list):
            identities.update(
                str(alias).strip().casefold()
                for alias in aliases
                if isinstance(alias, str) and alias.strip()
            )
    return 0 if query_key in identities else 1


def _matches_intent(query: str, terms: frozenset[str]) -> bool:
    words = {
        "".join(character for character in value if character.isalnum())
        for value in query.split()
    }
    return any(
        term in query
        if " " in term or any(ord(character) > 127 for character in term)
        else term in words
        for term in terms
    )


def _node_identifier(value: str) -> str:
    tail = value.replace("\\", "/").rsplit("/", 1)[-1].rsplit("::", 1)[-1]
    return "".join(character for character in tail.casefold() if character.isalnum())


def _source_intent_bonus(intent: str, source_kind: str) -> float:
    priorities = {
        "exact_node": {
            "live_node_catalog": 2,
            "live_node_help": 1,
        },
        "workflow": {
            "builtin_official_workflow": 2,
            "community_tutorial": 1,
            "live_node_help": 1,
        },
        "memory": {"project_memory": 2},
        "user_source": {
            "user_document": 2,
            "user_transcript": 2,
            "thread_export": 2,
        },
    }
    return SOURCE_INTENT_BONUS * priorities.get(intent, {}).get(source_kind, 0)


def _pack_version_sort_key(value: Any) -> tuple[int, ...]:
    digits: list[int] = []
    current = ""
    for character in str(value or ""):
        if character.isdigit():
            current += character
        elif current:
            digits.append(int(current))
            current = ""
    if current:
        digits.append(int(current))
    return tuple(-part for part in digits[:4]) or (0,)


def _provenance(
    metadata: Mapping[str, Any],
    *,
    source_kind: str = "",
) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "source_key": str(metadata.get("source_key") or ""),
        "canonical_id": str(metadata.get("canonical_id") or ""),
        "pack_version": str(metadata.get("pack_version") or ""),
        "path": str(metadata.get("path") or ""),
        "url": str(metadata.get("url") or ""),
        "author": str(metadata.get("author") or ""),
        "accessed_at": str(metadata.get("accessed_at") or ""),
        "license": str(metadata.get("license") or ""),
        "verification": str(metadata.get("verification") or ""),
        "houdini_version": str(metadata.get("houdini_version") or ""),
        "content_hash": str(metadata.get("content_hash") or ""),
    }


def _bounded_reason(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (text or type(exc).__name__)[:1024]
