from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
RUNTIME_TMP_ROOT = REPOSITORY_ROOT / ".runtime" / "tmp"
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
from hia_mcp_runtime import hybrid_knowledge as hybrid_module  # noqa: E402
from hia_mcp_runtime.hybrid_knowledge import (  # noqa: E402
    HybridKnowledgeStore,
)
from hia_mcp_runtime.knowledge_index import (  # noqa: E402
    LocalKnowledgeIndex,
    _Candidate,
    _utc_now,
)


class FakeEmbedder:
    """Deterministic duck-typed encoder; it never imports a model runtime."""

    def __init__(
        self,
        *,
        available: bool = True,
        model_id: str = "Qwen/Qwen3-Embedding-0.6B",
        active_profile: str = "qwen3-embedding-0.6b",
        requested_profile: str | None = None,
        dim: int = 32,
        fallback_reason: str = "",
        state_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.available = available
        self.model_id = model_id
        self.active_profile = active_profile
        self.requested_profile = requested_profile or active_profile
        self.dim = dim
        self.fallback_reason = fallback_reason
        self.state_probe = state_probe
        self.calls: list[dict[str, tuple[str, ...]]] = []
        self.closed = False

    def reconfigure(
        self,
        *,
        model_id: str,
        active_profile: str,
        requested_profile: str,
        dim: int,
        fallback_reason: str = "",
    ) -> None:
        self.model_id = model_id
        self.active_profile = active_profile
        self.requested_profile = requested_profile
        self.dim = dim
        self.fallback_reason = fallback_reason

    def encode(
        self,
        *,
        documents: Sequence[str],
        queries: Sequence[str],
    ) -> Mapping[str, Any]:
        if self.state_probe is not None and self.state_probe():
            raise AssertionError("Embedding work entered the Houdini UI thread")
        document_values = tuple(str(value) for value in documents)
        query_values = tuple(str(value) for value in queries)
        self.calls.append(
            {"documents": document_values, "queries": query_values}
        )
        if not self.available:
            raise RuntimeError("No installed local embedding model")
        return {
            "document_vectors": [
                _fake_vector(value, self.dim) for value in document_values
            ],
            "query_vectors": [
                _fake_vector(value, self.dim) for value in query_values
            ],
            "model_id": self.model_id,
            "profile_id": self.active_profile,
            "active_profile": self.active_profile,
            "requested_profile": self.requested_profile,
            "dim": self.dim,
            "normalized": True,
            "status": "degraded" if self.fallback_reason else "ready",
            "fallback_reason": self.fallback_reason,
            "repair": {},
        }

    def status(self) -> Mapping[str, Any]:
        return {
            "available": self.available,
            "status": "degraded" if self.fallback_reason else "ready",
            "model_id": self.model_id if self.available else "",
            "model_revision": "",
            "profile_id": self.active_profile if self.available else "",
            "active_profile": self.active_profile if self.available else "",
            "requested_profile": self.requested_profile,
            "dim": self.dim if self.available else 0,
            "normalized": self.available,
            "fallback_reason": self.fallback_reason,
            "repair": {},
        }

    def close(self) -> None:
        self.closed = True

    @property
    def document_inputs(self) -> list[str]:
        return [
            value
            for call in self.calls
            for value in call["documents"]
        ]

    @property
    def query_calls(self) -> list[tuple[str, ...]]:
        return [
            call["queries"]
            for call in self.calls
            if call["queries"]
        ]


class MappedEmbedder(FakeEmbedder):
    """Fake encoder with selected texts pinned to orthogonal unit vectors."""

    def __init__(self, axes: Mapping[str, int]) -> None:
        super().__init__()
        self.axes = dict(axes)

    def encode(
        self,
        *,
        documents: Sequence[str],
        queries: Sequence[str],
    ) -> Mapping[str, Any]:
        result = dict(super().encode(documents=documents, queries=queries))
        result["document_vectors"] = [
            self._vector(str(value)) for value in documents
        ]
        result["query_vectors"] = [
            self._vector(str(value)) for value in queries
        ]
        return result

    def _vector(self, text: str) -> list[float]:
        axis = self.axes.get(text)
        if axis is None:
            return _fake_vector(text, self.dim)
        values = [0.0] * self.dim
        values[axis] = 1.0
        return values


def _fake_vector(text: str, dim: int) -> list[float]:
    values = [0.0] * dim
    tokens = re.findall(r"[\w.-]+", text.casefold()) or [text.casefold()]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        values[int.from_bytes(digest[:2], "little") % dim] += (
            1.0 if digest[2] % 2 else -1.0
        )
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not norm:
        values[0] = 1.0
        norm = 1.0
    return [value / norm for value in values]


def _seed_document(
    index: LocalKnowledgeIndex,
    *,
    source_key: str,
    title: str,
    bodies: Sequence[str],
) -> int:
    now = _utc_now()
    text = "\n\n".join(bodies)
    candidate = _Candidate(
        collection="user",
        source_group="user",
        source="user_document",
        source_key=source_key,
        title=title,
        source_path=f".runtime/knowledge/sources/{source_key}.md",
        url=f"project://.runtime/knowledge/sources/{source_key}.md",
        author="Hybrid test",
        houdini_version="any",
        license_name="test-only",
        verification="verified",
        evidence="Deterministic unit-test fixture",
        attributes={"fixture": source_key},
        inline_text=text,
    )
    metadata = {
        "url": candidate.url,
        "author": candidate.author,
        "accessed_at": now,
        "houdini_version": "any",
        "license": candidate.license_name,
        "verification": candidate.verification,
        "evidence": candidate.evidence,
        "attributes": dict(candidate.attributes),
    }
    metadata_json = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with closing(index._connect()) as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            index._replace_document(  # noqa: SLF001
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
                    (source_key,),
                ).fetchone()[0]
            )
            index._sync_chunks(  # noqa: SLF001
                connection,
                document_id=document_id,
                title=title,
                bodies=list(bodies),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return document_id


def _replace_chunks(
    index: LocalKnowledgeIndex,
    document_id: int,
    *,
    title: str,
    bodies: Sequence[str],
) -> None:
    with closing(index._connect()) as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            index._sync_chunks(  # noqa: SLF001
                connection,
                document_id=document_id,
                title=title,
                bodies=list(bodies),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _rows(
    index: LocalKnowledgeIndex,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[tuple[Any, ...]]:
    with closing(index._connect()) as connection:  # noqa: SLF001
        return [
            tuple(row)
            for row in connection.execute(sql, tuple(parameters)).fetchall()
        ]


def _build_all_vectors(
    store: HybridKnowledgeStore,
    *,
    batch_size: int = 64,
) -> Mapping[str, Any]:
    for _attempt in range(10_000):
        status = store.build_batch(batch_size)
        if status["complete"]:
            return status
        if int(status["chunks_indexed_this_call"]) <= 0:
            raise AssertionError("Vector build made no progress")
    raise AssertionError("Vector build did not converge")


class HybridKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        RUNTIME_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="hia-hybrid-test-",
            dir=RUNTIME_TMP_ROOT,
        )
        self.project_root = Path(self._temporary.name) / "project"
        self.project_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_default_hybrid_without_model_preserves_lexical_results(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        _seed_document(
            index,
            source_key="fallback",
            title="Fallback workflow",
            bodies=("lexical fallback needle remains searchable",),
        )
        embedder = FakeEmbedder(available=False)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        lexical = index.search(
            "fallback needle",
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )

        result = store.search_many(
            ("fallback needle",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]

        self.assertEqual(lexical["matches"], result["matches"])
        self.assertEqual(lexical["total"], result["total"])
        self.assertEqual(lexical["tokenizer"], result["tokenizer"])
        self.assertEqual("hybrid", result["retrieval"]["requested_mode"])
        self.assertEqual("lexical", result["retrieval"]["mode_used"])
        self.assertFalse(result["retrieval"]["vector"]["available"])
        self.assertIn(
            "No installed local embedding model",
            result["retrieval"]["fallback_reason"],
        )

    def test_chunk_hash_incremental_vector_sync_reencodes_only_change(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        first_body = "alpha unchanged chunk"
        old_second_body = "beta old chunk"
        document_id = _seed_document(
            index,
            source_key="incremental",
            title="Incremental chunks",
            bodies=(first_body, old_second_body),
        )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        initial_build = store.build_batch(64)
        self.assertTrue(initial_build["complete"])
        self.assertEqual(2, initial_build["chunks_indexed_this_call"])
        first_vectors = _rows(
            index,
            "SELECT c.id, c.ordinal, c.content_hash, cv.vector_blob "
            "FROM chunks c JOIN chunk_vectors cv ON cv.chunk_id = c.id "
            "WHERE c.document_id = ? ORDER BY c.ordinal",
            (document_id,),
        )
        self.assertEqual([first_body, old_second_body], embedder.document_inputs)

        embedder.calls.clear()
        store.search_many(
            ("alpha",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )
        self.assertEqual([], embedder.document_inputs)

        changed_second_body = "gamma changed chunk"
        _replace_chunks(
            index,
            document_id,
            title="Incremental chunks",
            bodies=(first_body, changed_second_body),
        )
        remaining_vector_ids = {
            row[0]
            for row in _rows(
                index,
                "SELECT chunk_id FROM chunk_vectors ORDER BY chunk_id",
            )
        }
        self.assertIn(first_vectors[0][0], remaining_vector_ids)
        self.assertNotIn(first_vectors[1][0], remaining_vector_ids)

        embedder.calls.clear()
        store.search_many(
            ("alpha",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )
        self.assertEqual([], embedder.document_inputs)

        build = store.build_batch(64)
        self.assertTrue(build["complete"])
        self.assertEqual(1, build["chunks_indexed_this_call"])
        self.assertEqual([changed_second_body], embedder.document_inputs)
        revised_vectors = _rows(
            index,
            "SELECT c.id, c.ordinal, c.content_hash, cv.vector_blob "
            "FROM chunks c JOIN chunk_vectors cv ON cv.chunk_id = c.id "
            "WHERE c.document_id = ? ORDER BY c.ordinal",
            (document_id,),
        )
        self.assertEqual(first_vectors[0], revised_vectors[0])
        self.assertEqual(first_vectors[1][0], revised_vectors[1][0])
        self.assertNotEqual(first_vectors[1][2], revised_vectors[1][2])
        self.assertNotEqual(first_vectors[1][3], revised_vectors[1][3])

    def test_build_batch_commits_progress_before_interrupt_and_resumes(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        bodies = [
            f"resumable vector chunk {number}"
            for number in range(5)
        ]
        for number, body in enumerate(bodies):
            _seed_document(
                index,
                source_key=f"resume-{number}",
                title=f"Resume {number}",
                bodies=(body,),
            )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        original_encode = store._encode  # noqa: SLF001
        document_calls = 0

        def interrupt_second_document_batch(
            *,
            documents: Sequence[str],
            queries: Sequence[str],
        ) -> Any:
            nonlocal document_calls
            if documents:
                document_calls += 1
                if document_calls == 2:
                    raise KeyboardInterrupt
            return original_encode(documents=documents, queries=queries)

        with mock.patch.object(
            store,
            "_encode",
            side_effect=interrupt_second_document_batch,
        ):
            with self.assertRaises(KeyboardInterrupt):
                store.build_batch(5)

        interrupted = store.status()
        self.assertEqual(1, interrupted["vector_chunks"])
        self.assertEqual(4, interrupted["pending_chunks"])
        embedder.calls.clear()

        resumed = _build_all_vectors(store)

        self.assertTrue(resumed["complete"])
        self.assertEqual(5, resumed["vector_chunks"])
        self.assertEqual(set(bodies[1:]), set(embedder.document_inputs))

    def test_cold_index_budget_prioritizes_lexical_candidate_and_progresses(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        target_bodies = [
            f"priorityneedle candidate chunk {number:03d}"
            for number in range(40)
        ]
        target_document_ids = [
            _seed_document(
                index,
                source_key=f"priority-candidate-{number:03d}",
                title=f"Priorityneedle candidate {number:03d}",
                bodies=(body,),
            )
            for number, body in enumerate(target_bodies)
        ]
        backlog_bodies = [
            f"backlog chunk {number:03d} unrelated archive"
            for number in range(60)
        ]
        for number, body in enumerate(backlog_bodies):
            _seed_document(
                index,
                source_key=f"backlog-{number:03d}",
                title=f"Backlog {number:03d}",
                bodies=(body,),
            )
        total_chunks = len(target_bodies) + len(backlog_bodies)
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        first = store.search_many(
            ("priorityneedle",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]
        first_progress = first["retrieval"]["vector"]["index"]
        self.assertEqual(
            hybrid_module.MAX_QUERY_VECTOR_SYNCS,
            len(embedder.document_inputs),
        )
        self.assertLessEqual(
            len(embedder.document_inputs),
            hybrid_module.MAX_QUERY_VECTOR_SYNCS,
        )
        self.assertTrue(
            all("priorityneedle" in body for body in embedder.document_inputs)
        )
        target_chunk_ids = {
            int(row[0])
            for row in _rows(
                index,
                "SELECT id FROM chunks WHERE document_id IN ("
                + ",".join("?" for _value in target_document_ids)
                + ")",
                target_document_ids,
            )
        }
        indexed_chunk_ids = {
            int(row[0])
            for row in _rows(index, "SELECT chunk_id FROM chunk_vectors")
        }
        self.assertEqual(32, len(target_chunk_ids.intersection(indexed_chunk_ids)))
        self.assertEqual(
            {
                "complete": False,
                "partial": True,
                "vector_chunks": 32,
                "total_chunks": total_chunks,
                "pending_chunks": total_chunks - 32,
                "chunks_indexed_this_call": 32,
                "last_batch_count": 32,
                "ranking_scope": "lexical_candidates",
                "partial_reason": (
                    hybrid_module.PARTIAL_CANDIDATES_REASON
                ),
            },
            first_progress,
        )

        embedder.calls.clear()
        second = store.search_many(
            ("priorityneedle",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]
        second_progress = second["retrieval"]["vector"]["index"]
        self.assertEqual(8, len(embedder.document_inputs))
        self.assertTrue(
            all("priorityneedle" in body for body in embedder.document_inputs)
        )
        indexed_chunk_ids = {
            int(row[0])
            for row in _rows(index, "SELECT chunk_id FROM chunk_vectors")
        }
        self.assertTrue(target_chunk_ids.issubset(indexed_chunk_ids))
        self.assertEqual(
            {
                "complete": False,
                "partial": True,
                "vector_chunks": 40,
                "total_chunks": total_chunks,
                "pending_chunks": total_chunks - 40,
                "chunks_indexed_this_call": 8,
                "last_batch_count": 8,
                "ranking_scope": "lexical_candidates",
                "partial_reason": (
                    hybrid_module.PARTIAL_CANDIDATES_REASON
                ),
            },
            second_progress,
        )

    def test_partial_index_uses_relaxed_lexical_candidates_only(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        biased_body = "legacy CHOP Warp operator"
        biased_document_id = _seed_document(
            index,
            source_key="biased-chop",
            title="CHOP Warp",
            bodies=(biased_body,),
        )
        _seed_document(
            index,
            source_key="ripple-target",
            title="Ripple Solver",
            bodies=("ripple lexical candidate",),
        )
        _seed_document(
            index,
            source_key="rbd-material-fracture",
            title="RBD Material Fracture",
            bodies=(
                "Prepare a concrete wall with the fracture SOP, add interior "
                "detail, and configure constraint geometry.",
            ),
        )
        _seed_document(
            index,
            source_key="pending-filler",
            title="Pending filler",
            bodies=("unrelated pending document",),
        )
        semantic_query = "velocity advected field"
        embedder = MappedEmbedder(
            {
                biased_body: 0,
                semantic_query: 0,
            }
        )
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        store._vectorize_documents((biased_document_id,))  # noqa: SLF001

        long_query = (
            "RBD Material Fracture SOP concrete wall constraints "
            "interior detail workflow"
        )
        relaxed_candidate = store.search_many(
            (long_query,),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="hybrid",
        )[0]
        self.assertEqual(
            {"rbd-material-fracture"},
            {
                match["metadata"]["source_key"]
                for match in relaxed_candidate["matches"]
            },
        )
        relaxed_index = relaxed_candidate["retrieval"]["vector"]["index"]
        self.assertFalse(relaxed_index["complete"])
        self.assertTrue(relaxed_index["partial"])
        self.assertEqual(
            "lexical_candidates",
            relaxed_index["ranking_scope"],
        )

        lexical_candidate = store.search_many(
            ("ripple",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="hybrid",
        )[0]
        self.assertEqual(
            {"ripple-target"},
            {
                match["metadata"]["source_key"]
                for match in lexical_candidate["matches"]
            },
        )
        candidate_index = lexical_candidate["retrieval"]["vector"]["index"]
        self.assertFalse(candidate_index["complete"])
        self.assertEqual(
            "lexical_candidates",
            candidate_index["ranking_scope"],
        )

        no_candidate = store.search_many(
            (semantic_query,),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="vector",
        )[0]
        self.assertEqual([], no_candidate["matches"])
        self.assertEqual("lexical", no_candidate["retrieval"]["mode_used"])
        self.assertEqual(
            "none",
            no_candidate["retrieval"]["vector"]["index"]["ranking_scope"],
        )
        self.assertEqual(
            hybrid_module.PARTIAL_NO_CANDIDATES_REASON,
            no_candidate["retrieval"]["fallback_reason"],
        )

        completed = _build_all_vectors(store)
        self.assertTrue(completed["complete"])
        global_result = store.search_many(
            (semantic_query,),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="vector",
        )[0]
        self.assertEqual("vector", global_result["retrieval"]["mode_used"])
        self.assertEqual(
            "global",
            global_result["retrieval"]["vector"]["index"]["ranking_scope"],
        )
        self.assertEqual(
            "biased-chop",
            global_result["matches"][0]["metadata"]["source_key"],
        )

    def test_partial_multi_query_ranking_keeps_candidate_sets_isolated(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        alpha_body = "alpha lexical target"
        beta_body = "beta lexical target"
        _seed_document(
            index,
            source_key="alpha-candidate",
            title="Alpha candidate",
            bodies=(alpha_body,),
        )
        _seed_document(
            index,
            source_key="beta-candidate",
            title="Beta candidate",
            bodies=(beta_body,),
        )
        _seed_document(
            index,
            source_key="pending",
            title="Pending",
            bodies=("unrelated pending backlog",),
        )
        embedder = MappedEmbedder(
            {
                alpha_body: 1,
                beta_body: 0,
                "alpha": 0,
                "beta": 1,
            }
        )
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        results = store.search_many(
            ("alpha", "beta"),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )

        self.assertEqual([alpha_body, beta_body], embedder.document_inputs)
        self.assertEqual(
            {"alpha-candidate"},
            {
                match["metadata"]["source_key"]
                for match in results[0]["matches"]
            },
        )
        self.assertEqual(
            {"beta-candidate"},
            {
                match["metadata"]["source_key"]
                for match in results[1]["matches"]
            },
        )
        for result in results:
            self.assertEqual(
                "lexical_candidates",
                result["retrieval"]["vector"]["index"]["ranking_scope"],
            )

    def test_vector_ranking_streams_one_cursor_with_bounded_top_k(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        for number in range(120):
            _seed_document(
                index,
                source_key=f"stream-{number:03d}",
                title=f"Stream candidate {number:03d}",
                bodies=(f"stream ranking candidate {number:03d}",),
            )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        build = _build_all_vectors(store)
        self.assertTrue(build["complete"])
        vector_count = int(
            _rows(index, "SELECT COUNT(*) FROM chunk_vectors")[0][0]
        )
        self.assertEqual(120, vector_count)
        self.assertFalse(hasattr(HybridKnowledgeStore, "_load_vector_rows"))

        batch = store._encode(  # noqa: SLF001
            documents=(),
            queries=("stream 1", "stream 2", "stream 3"),
        )
        tracking = {
            "vector_selects": 0,
            "fetchall_calls": 0,
            "rows_iterated": 0,
            "decoded_active": 0,
            "decoded_max_active": 0,
            "decoded_calls": 0,
        }
        original_connect = index._connect  # noqa: SLF001
        original_decode = hybrid_module._decode_vector  # noqa: SLF001

        class TrackingCursor:
            def __init__(self, cursor: Any) -> None:
                self._cursor = cursor

            def __iter__(self) -> Any:
                for row in self._cursor:
                    tracking["rows_iterated"] += 1
                    yield row

            def fetchall(self) -> Any:
                tracking["fetchall_calls"] += 1
                raise AssertionError(
                    "Vector ranking must iterate instead of fetchall"
                )

            def __getattr__(self, name: str) -> Any:
                return getattr(self._cursor, name)

        class TrackingConnection:
            def __init__(self, connection: Any) -> None:
                object.__setattr__(self, "_connection", connection)

            def __setattr__(self, name: str, value: Any) -> None:
                setattr(self._connection, name, value)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._connection, name)

            def execute(
                self,
                sql: str,
                parameters: Sequence[Any] = (),
            ) -> Any:
                cursor = self._connection.execute(sql, parameters)
                normalized = " ".join(sql.split())
                if "FROM chunk_vectors cv JOIN chunks c" in normalized:
                    tracking["vector_selects"] += 1
                    return TrackingCursor(cursor)
                return cursor

            def close(self) -> None:
                self._connection.close()

        class TrackedVector:
            def __init__(self, values: Sequence[float]) -> None:
                self._values = values
                tracking["decoded_active"] += 1
                tracking["decoded_max_active"] = max(
                    tracking["decoded_max_active"],
                    tracking["decoded_active"],
                )

            def __iter__(self) -> Any:
                return iter(self._values)

            def __del__(self) -> None:
                tracking["decoded_active"] -= 1

        def tracked_connect() -> TrackingConnection:
            return TrackingConnection(original_connect())

        def tracked_decode(raw: Any, dim: int) -> TrackedVector:
            tracking["decoded_calls"] += 1
            return TrackedVector(original_decode(raw, dim))

        with (
            mock.patch.object(index, "_connect", side_effect=tracked_connect),
            mock.patch.object(
                hybrid_module,
                "_decode_vector",
                side_effect=tracked_decode,
            ),
        ):
            rankings = store._stream_vector_rankings(  # noqa: SLF001
                ("stream 1", "stream 2", "stream 3"),
                batch,
                {"user"},
                memory_scope="",
                include_superseded=False,
                top_k=17,
            )
        gc.collect()

        self.assertEqual(1, tracking["vector_selects"])
        self.assertEqual(0, tracking["fetchall_calls"])
        self.assertEqual(vector_count, tracking["rows_iterated"])
        self.assertEqual(vector_count, tracking["decoded_calls"])
        self.assertLessEqual(tracking["decoded_max_active"], 2)
        self.assertEqual(0, tracking["decoded_active"])
        self.assertEqual(3, len(rankings))
        self.assertTrue(all(len(ranking) <= 17 for ranking in rankings))

    def test_batch_queries_encode_once_and_rrf_deduplicates_with_provenance(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        _seed_document(
            index,
            source_key="alpha",
            title="Alpha workflow",
            bodies=("alpha shared retrieval workflow",),
        )
        _seed_document(
            index,
            source_key="beta",
            title="Beta workflow",
            bodies=("beta shared retrieval workflow",),
        )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        results = store.search_many(
            ("alpha workflow", "beta workflow"),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )

        self.assertEqual(
            [("alpha workflow", "beta workflow")],
            embedder.query_calls,
        )
        self.assertEqual(2, len(results))
        for result in results:
            source_keys = [
                match["metadata"]["source_key"]
                for match in result["matches"]
            ]
            self.assertEqual(len(source_keys), len(set(source_keys)))
            self.assertEqual("hybrid", result["retrieval"]["mode_used"])
            self.assertTrue(result["retrieval"]["vector"]["available"])
            for match in result["matches"]:
                self.assertIn("hybrid_score", match["metadata"])
                self.assertEqual(
                    match["metadata"]["source_key"],
                    match["provenance"]["source_key"],
                )
                self.assertTrue(match["provenance"]["content_hash"])

    def test_vector_signature_switch_rebuilds_only_vector_layer(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        _seed_document(
            index,
            source_key="profile-switch",
            title="Profile switch",
            bodies=("profile alpha chunk", "profile beta chunk"),
        )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        _build_all_vectors(store)
        documents_before = _rows(
            index,
            "SELECT id, source_key, sha256 FROM documents ORDER BY id",
        )
        chunks_before = _rows(
            index,
            "SELECT id, document_id, ordinal, body, content_hash "
            "FROM chunks ORDER BY id",
        )
        fts_before = _rows(
            index,
            "SELECT rowid, title, body FROM knowledge_fts ORDER BY rowid",
        )
        self.assertTrue(
            all(
                row[0] == "Qwen/Qwen3-Embedding-0.6B" and row[1] == 32
                for row in _rows(
                    index,
                    "SELECT model_id, dim FROM chunk_vectors",
                )
            )
        )

        embedder.reconfigure(
            model_id="Qwen/Qwen3-Embedding-8B",
            active_profile="qwen3-embedding-8b",
            requested_profile="qwen3-embedding-8b",
            dim=64,
        )
        _build_all_vectors(store)

        self.assertEqual(documents_before, _rows(
            index,
            "SELECT id, source_key, sha256 FROM documents ORDER BY id",
        ))
        self.assertEqual(chunks_before, _rows(
            index,
            "SELECT id, document_id, ordinal, body, content_hash "
            "FROM chunks ORDER BY id",
        ))
        self.assertEqual(fts_before, _rows(
            index,
            "SELECT rowid, title, body FROM knowledge_fts ORDER BY rowid",
        ))
        vectors_after = _rows(
            index,
            "SELECT model_id, profile_id, dim FROM chunk_vectors ORDER BY chunk_id",
        )
        self.assertEqual(len(chunks_before), len(vectors_after))
        self.assertTrue(
            all(
                row == (
                    "Qwen/Qwen3-Embedding-8B",
                    "qwen3-embedding-8b",
                    64,
                )
                for row in vectors_after
            )
        )

    def test_corrupt_blob_and_dimension_fall_back_to_lexical(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        _seed_document(
            index,
            source_key="corruption",
            title="Corruption fallback",
            bodies=("corruption fallback remains lexical",),
        )
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        store.search_many(
            ("corruption fallback",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )
        original = _rows(
            index,
            "SELECT chunk_id, dim, vector_blob FROM chunk_vectors",
        )[0]

        with closing(index._connect()) as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE chunk_vectors SET vector_blob = ? WHERE chunk_id = ?",
                (b"broken", original[0]),
            )
        corrupt_blob = store.search_many(
            ("corruption fallback",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]
        self.assertEqual("lexical", corrupt_blob["retrieval"]["mode_used"])
        self.assertTrue(corrupt_blob["matches"])
        self.assertIn(
            "Stored vector dimension is corrupt",
            corrupt_blob["retrieval"]["fallback_reason"],
        )

        with closing(index._connect()) as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE chunk_vectors SET vector_blob = ?, dim = ? "
                "WHERE chunk_id = ?",
                (original[2], original[1] + 1, original[0]),
            )
        wrong_dim = store.search_many(
            ("corruption fallback",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]
        self.assertEqual("lexical", wrong_dim["retrieval"]["mode_used"])
        self.assertTrue(wrong_dim["matches"])
        self.assertIn(
            "dimension or normalization is corrupt",
            wrong_dim["retrieval"]["fallback_reason"],
        )

    def test_requested_8b_can_report_degraded_active_06_profile(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        _seed_document(
            index,
            source_key="profile-fallback",
            title="Profile fallback",
            bodies=("profile fallback evidence",),
        )
        embedder = FakeEmbedder(
            requested_profile="qwen3-embedding-8b",
            active_profile="qwen3-embedding-0.6b",
            fallback_reason="8B unavailable; using 0.6B",
        )
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        result = store.search_many(
            ("profile fallback",),
            {"user"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
        )[0]
        vector = result["retrieval"]["vector"]

        self.assertEqual("hybrid", result["retrieval"]["mode_used"])
        self.assertTrue(vector["available"])
        self.assertEqual("degraded", vector["state"])
        self.assertEqual("qwen3-embedding-8b", vector["requested_profile"])
        self.assertEqual("qwen3-embedding-0.6b", vector["active_profile"])
        self.assertEqual(
            "Qwen/Qwen3-Embedding-0.6B",
            vector["model_id"],
        )
        self.assertIn("using 0.6B", vector["fallback_reason"])

    def test_memory_crud_supersede_scope_types_and_vector_deletion(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        recorded: dict[str, dict[str, Any]] = {}
        for memory_type in (
            "decision",
            "preference",
            "asset",
            "lesson",
            "workflow",
        ):
            scope = "asset:/obj/hero" if memory_type == "asset" else "project"
            response = store.project_memory(
                {
                    "action": "record",
                    "memory_type": memory_type,
                    "title": f"{memory_type} durable title",
                    "body": f"{memory_type} unique durable body",
                    "tags": [memory_type, "durable"],
                    "scope": scope,
                    "source_thread_id": "thread-123",
                    "source_turn_id": f"turn-{memory_type}",
                }
            )
            memory = response["memory"]
            recorded[memory_type] = memory
            self.assertRegex(memory["id"], r"^mem_[0-9a-f]{32}$")
            datetime.fromisoformat(memory["created_at"])
            datetime.fromisoformat(memory["updated_at"])
            self.assertEqual("active", memory["status"])
            self.assertEqual("thread-123", memory["source_thread_id"])
            self.assertEqual(
                f"turn-{memory_type}",
                memory["source_turn_id"],
            )

        project_items = store.project_memory(
            {"action": "list", "scope": "project", "limit": 20}
        )["items"]
        self.assertEqual(
            {"decision", "preference", "lesson", "workflow"},
            {item["memory_type"] for item in project_items},
        )
        asset_items = store.project_memory(
            {"action": "list", "scope": "asset:/obj/hero", "limit": 20}
        )["items"]
        self.assertEqual([recorded["asset"]["id"]], [
            item["id"] for item in asset_items
        ])
        self.assertEqual(
            recorded["decision"]["created_at"],
            next(
                item["created_at"]
                for item in project_items
                if item["id"] == recorded["decision"]["id"]
            ),
        )

        wrong_scope = store.project_memory(
            {
                "action": "search",
                "query": "asset unique durable",
                "scope": "project",
            }
        )
        right_scope = store.project_memory(
            {
                "action": "search",
                "query": "asset unique durable",
                "scope": "asset:/obj/hero",
            }
        )
        self.assertNotIn(
            recorded["asset"]["id"],
            {
                match["metadata"]["memory_id"]
                for match in wrong_scope["matches"]
            },
        )
        self.assertTrue(
            all(
                match["metadata"]["scope"] == "project"
                for match in wrong_scope["matches"]
            )
        )
        self.assertEqual(1, right_scope["total"])
        self.assertEqual(
            recorded["asset"]["id"],
            right_scope["matches"][0]["metadata"]["memory_id"],
        )

        superseded = store.project_memory(
            {
                "action": "supersede",
                "memory_id": recorded["decision"]["id"],
                "memory_type": "decision",
                "title": "decision replacement title",
                "body": "decision replacement durable body",
                "tags": ["decision", "replacement"],
                "scope": "project",
                "source_thread_id": "thread-123",
                "source_turn_id": "turn-replacement",
            }
        )
        old_memory = superseded["superseded"]
        replacement = superseded["replacement"]
        self.assertEqual("superseded", old_memory["status"])
        self.assertEqual(replacement["id"], old_memory["superseded_by"])
        self.assertEqual("active", replacement["status"])
        self.assertNotEqual(old_memory["id"], replacement["id"])
        active_ids = {
            item["id"]
            for item in store.project_memory(
                {"action": "list", "scope": "project", "limit": 20}
            )["items"]
        }
        self.assertNotIn(old_memory["id"], active_ids)
        all_ids = {
            item["id"]
            for item in store.project_memory(
                {
                    "action": "list",
                    "scope": "project",
                    "include_superseded": True,
                    "limit": 20,
                }
            )["items"]
        }
        self.assertIn(old_memory["id"], all_ids)
        self.assertIn(replacement["id"], all_ids)

        asset_id = recorded["asset"]["id"]
        asset_document_id = int(_rows(
            index,
            "SELECT document_id FROM project_memories WHERE id = ?",
            (asset_id,),
        )[0][0])
        asset_chunk_ids = {
            int(row[0])
            for row in _rows(
                index,
                "SELECT id FROM chunks WHERE document_id = ?",
                (asset_document_id,),
            )
        }
        self.assertTrue(asset_chunk_ids)
        self.assertTrue(
            _rows(
                index,
                "SELECT chunk_id FROM chunk_vectors "
                "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)",
                (asset_document_id,),
            )
        )
        deleted = store.project_memory(
            {"action": "delete", "memory_id": asset_id}
        )
        self.assertTrue(deleted["deleted"])
        self.assertEqual([], _rows(
            index,
            "SELECT id FROM project_memories WHERE id = ?",
            (asset_id,),
        ))
        self.assertEqual([], _rows(
            index,
            "SELECT id FROM documents WHERE id = ?",
            (asset_document_id,),
        ))
        self.assertEqual([], _rows(
            index,
            "SELECT id FROM chunks WHERE document_id = ?",
            (asset_document_id,),
        ))
        placeholders = ",".join("?" for _value in asset_chunk_ids)
        self.assertEqual([], _rows(
            index,
            f"SELECT chunk_id FROM chunk_vectors WHERE chunk_id IN ({placeholders})",
            tuple(sorted(asset_chunk_ids)),
        ))
        lexical_after_delete = index.search(
            "asset unique durable",
            {"memory"},
            current_houdini_version="",
            offset=0,
            limit=10,
            memory_scope="asset:/obj/hero",
            include_superseded=True,
        )
        self.assertEqual(0, lexical_after_delete["total"])

    def test_connections_close_database_is_deletable_and_executor_stays_off_ui(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        state = {"in_ui": False, "runner_calls": 0, "dirty_calls": 0}
        embedder = FakeEmbedder(
            state_probe=lambda: bool(state["in_ui"]),
        )
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        original_project_memory = store.project_memory

        def checked_project_memory(
            arguments: Mapping[str, Any],
        ) -> dict[str, Any]:
            self.assertFalse(state["in_ui"])
            return original_project_memory(arguments)

        store.project_memory = checked_project_memory  # type: ignore[method-assign]

        class FakeHipFile:
            @staticmethod
            def hasUnsavedChanges() -> bool:
                if not state["in_ui"]:
                    raise AssertionError("Dirty snapshot ran outside UI thread")
                state["dirty_calls"] += 1
                return False

        class FakeHou:
            hipFile = FakeHipFile()

        def runner(callback: Callable[[], Any]) -> Any:
            self.assertFalse(state["in_ui"])
            state["runner_calls"] += 1
            state["in_ui"] = True
            try:
                return callback()
            finally:
                state["in_ui"] = False

        expected_cache = (self.project_root / ".runtime" / "cache").resolve()
        with mock.patch.dict(
            os.environ,
            {"HIA_CACHE_DIR": str(expected_cache)},
        ):
            executor = HoudiniExecutor(
                hou_module=FakeHou(),
                main_thread_runner=runner,
                project_root=self.project_root,
            )
        executor._knowledge_index = index  # noqa: SLF001
        executor._hybrid_knowledge = store  # noqa: SLF001

        response = executor.dispatch(
            "hia_project_memory",
            {
                "action": "record",
                "memory_type": "workflow",
                "title": "UI thread boundary",
                "body": "SQLite and embedding work stay outside the UI thread.",
            },
        )
        self.assertTrue(response["ok"])
        self.assertEqual(1, state["runner_calls"])
        self.assertEqual(1, state["dirty_calls"])
        self.assertTrue(embedder.calls)

        executor.close()
        self.assertTrue(embedder.closed)
        database_path = index.database_path
        database_path.unlink()
        self.assertFalse(database_path.exists())


if __name__ == "__main__":
    unittest.main()
