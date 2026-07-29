from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(
    0,
    str(REPOSITORY_ROOT / "houdini_package" / "python_libs"),
)

from hia_mcp_runtime.deterministic_sources import (  # noqa: E402
    normalize_selected_file,
    normalize_thread_export,
)
from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
from hia_mcp_runtime.hybrid_knowledge import HybridKnowledgeStore  # noqa: E402
from hia_mcp_runtime.knowledge_index import (  # noqa: E402
    LocalKnowledgeIndex,
    SOURCE_KIND_GROUPS,
)
from tests.unit.test_hia_mcp_v2_hybrid_knowledge import (  # noqa: E402
    FakeEmbedder,
    _build_all_vectors,
    _seed_document,
)
from tests.unit.test_hia_mcp_v2_local_help import _FakeHou  # noqa: E402


class HiaMcpV2ExplicitSourceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="hia-explicit-ingestion-",
            dir=REPOSITORY_ROOT / "tests",
        )
        self.addCleanup(self.temp.cleanup)
        self.project_root = Path(self.temp.name) / "project"
        self.project_root.mkdir()
        self.index = LocalKnowledgeIndex(self.project_root)
        self.embedder = FakeEmbedder()
        self.store = HybridKnowledgeStore(
            self.project_root,
            index=self.index,
            embedder=self.embedder,
        )
        self.addCleanup(self.store.close)

    def write_text(self, name: str, text: str) -> Path:
        path = self.project_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def search(
        self,
        query: str,
        source_kind: str,
        mode: str,
    ) -> dict[str, Any]:
        return self.store.search_many(
            (query,),
            {SOURCE_KIND_GROUPS[source_kind]},
            current_houdini_version="21.0.440",
            offset=0,
            limit=20,
            mode=mode,
            source_kinds={source_kind},
        )[0]

    def database_fingerprint(self) -> tuple[tuple[str, int, int, str], ...]:
        output = []
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.index.database_path) + suffix)
            if not path.exists():
                continue
            raw = path.read_bytes()
            output.append(
                (
                    path.name,
                    path.stat().st_mtime_ns,
                    len(raw),
                    hashlib.sha256(raw).hexdigest(),
                )
            )
        return tuple(output)

    def test_all_explicit_source_kinds_support_lexical_vector_and_hybrid(self) -> None:
        document = self.write_text(
            "fixtures/source.md",
            "DocumentNeedle explains sparse velocity advection.",
        )
        captions = self.write_text(
            "fixtures/lesson.srt",
            "1\n00:00:00,000 --> 00:00:01,000\n"
            "TranscriptNeedle configures a Vellum cache.\n",
        )
        document_batch = normalize_selected_file(
            document,
            source_id="source.md",
        )
        transcript_batch = normalize_selected_file(
            captions,
            source_id="lesson.srt",
        )
        thread_batch = normalize_thread_export(
            "thread-visible",
            [
                {
                    "id": "user-1",
                    "turn_id": "turn-1",
                    "role": "user",
                    "type": "message",
                    "content": "ThreadUserNeedle requests a packed cache.",
                },
                {
                    "id": "reasoning-1",
                    "turn_id": "turn-1",
                    "role": "assistant",
                    "type": "reasoning",
                    "content": "ReasoningNeedle must remain private.",
                },
                {
                    "id": "assistant-1",
                    "turn_id": "turn-1",
                    "role": "assistant",
                    "phase": "final",
                    "content": "ThreadFinalNeedle uses an explicit OUT node.",
                },
            ],
            explicitly_selected=True,
        )
        refreshed = self.store.refresh_explicit_sources(
            (
                *document_batch.records,
                *transcript_batch.records,
                *thread_batch.records,
            )
        )
        self.assertEqual(4, refreshed["refresh"]["documents_added"])

        memory = self.store.project_memory(
            {
                "action": "record",
                "memory_type": "decision",
                "title": "MemoryNeedle cache decision",
                "body": "Keep stage checkpoints explicit.",
                "tags": ["cache"],
                "scope": "project",
                "source_thread_id": "thread-visible",
                "source_turn_id": "turn-1",
            }
        )
        self.assertEqual("active", memory["memory"]["status"])
        self.assertEqual(
            "user_supplied_unverified",
            self.search("MemoryNeedle", "project_memory", "lexical")[
                "matches"
            ][0]["metadata"]["verification"],
        )
        self.assertTrue(self.store.status()["complete"])

        cases = (
            ("DocumentNeedle", "user_document"),
            ("TranscriptNeedle", "user_transcript"),
            ("ThreadFinalNeedle", "thread_export"),
            ("MemoryNeedle", "project_memory"),
        )
        for query, source_kind in cases:
            for mode in ("lexical", "vector", "hybrid"):
                with self.subTest(query=query, source_kind=source_kind, mode=mode):
                    result = self.search(query, source_kind, mode)
                    self.assertTrue(result["matches"])
                    self.assertTrue(
                        all(
                            item["source_kind"] == source_kind
                            for item in result["matches"]
                        )
                    )
                    self.assertEqual(
                        "user_supplied_unverified",
                        result["matches"][0]["metadata"]["verification"],
                    )

        private = self.search(
            "ReasoningNeedle",
            "thread_export",
            "lexical",
        )
        self.assertEqual([], private["matches"])

    def test_managed_refresh_updates_and_deletes_fts_chunks_and_vectors(self) -> None:
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / "simulation.md"
        )
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_text("OldManagedNeedle cache workflow", encoding="utf-8")

        added, warnings = self.index.refresh(
            {"user"},
            {"houdini_version": "21.0.440"},
            force=True,
        )
        self.assertFalse(warnings)
        self.assertEqual(1, added["documents_added"])
        first = self.search("OldManagedNeedle", "user_document", "lexical")
        self.assertTrue(first["matches"])
        source_key = first["matches"][0]["metadata"]["source_key"]
        document_id = first["matches"][0]["metadata"]["document_id"]
        self.assertEqual(
            "user:user_document:simulation.md",
            source_key,
        )
        self.assertTrue(_build_all_vectors(self.store)["complete"])

        managed.write_text("NewManagedNeedle cache workflow", encoding="utf-8")
        updated, warnings = self.index.refresh(
            {"user"},
            {"houdini_version": "21.0.440"},
            force=True,
        )
        self.assertFalse(warnings)
        self.assertEqual(1, updated["documents_body_changed"])
        current = self.search("NewManagedNeedle", "user_document", "lexical")
        self.assertEqual(
            document_id,
            current["matches"][0]["metadata"]["document_id"],
        )
        self.assertEqual(
            [],
            self.search(
                "OldManagedNeedle",
                "user_document",
                "lexical",
            )["matches"],
        )
        self.assertTrue(self.store.status()["partial"])
        self.assertTrue(_build_all_vectors(self.store)["complete"])

        managed.unlink()
        removed, warnings = self.index.refresh(
            {"user"},
            {"houdini_version": "21.0.440"},
            force=True,
        )
        self.assertFalse(warnings)
        self.assertEqual(1, removed["documents_removed"])
        with closing(self.index._connect(read_only=True)) as connection:  # noqa: SLF001
            counts = tuple(
                int(value)
                for value in connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM documents WHERE source_key = ?), "
                    "(SELECT COUNT(*) FROM chunks WHERE document_id = ?), "
                    "(SELECT COUNT(*) FROM chunk_vectors WHERE chunk_id IN "
                    "(SELECT id FROM chunks WHERE document_id = ?))",
                    (source_key, document_id, document_id),
                ).fetchone()
            )
        self.assertEqual((0, 0, 0), counts)

    def test_media_without_transcript_is_not_indexed(self) -> None:
        media = self.write_text("fixtures/silent.mp4", "fixture media bytes")
        batch = normalize_selected_file(media, source_id="silent.mp4")
        self.assertEqual("transcript_required", batch.status)
        before = self.index.corpus_status()
        result = self.store.refresh_explicit_sources(batch.records)
        after = self.index.corpus_status()
        self.assertEqual(0, result["refresh"]["records_received"])
        self.assertEqual(before, after)

    def test_explicit_thread_update_and_delete_reuse_one_stable_record(self) -> None:
        original = normalize_thread_export(
            "thread-stable",
            [
                {
                    "id": "message-1",
                    "turn_id": "turn-1",
                    "role": "assistant",
                    "phase": "final",
                    "content": "OriginalThreadNeedle",
                }
            ],
            explicitly_selected=True,
        ).records[0]
        added = self.store.refresh_explicit_sources((original,))
        self.assertEqual(1, added["refresh"]["documents_added"])

        edited = normalize_thread_export(
            "thread-stable",
            [
                {
                    "id": "message-1",
                    "turn_id": "turn-1",
                    "role": "assistant",
                    "phase": "final",
                    "content": "EditedThreadNeedle",
                }
            ],
            explicitly_selected=True,
        ).records[0]
        self.assertEqual(original.source_key, edited.source_key)
        updated = self.store.refresh_explicit_sources((edited,))
        self.assertEqual(1, updated["refresh"]["documents_updated"])
        self.assertEqual(
            [],
            self.search(
                "OriginalThreadNeedle",
                "thread_export",
                "lexical",
            )["matches"],
        )
        self.assertTrue(
            self.search(
                "EditedThreadNeedle",
                "thread_export",
                "hybrid",
            )["matches"]
        )

        removed = self.store.refresh_explicit_sources(
            (),
            remove_source_keys=(edited.source_key,),
        )
        self.assertEqual(1, removed["refresh"]["documents_removed"])
        self.assertEqual(
            [],
            self.search(
                "EditedThreadNeedle",
                "thread_export",
                "lexical",
            )["matches"],
        )

    def test_memory_supersede_invalidates_old_and_delete_removes_replacement(self) -> None:
        original = self.store.project_memory(
            {
                "action": "record",
                "memory_type": "workflow",
                "title": "OldMemoryNeedle",
                "body": "Use the old cache layout.",
                "scope": "project",
            }
        )["memory"]
        replacement = self.store.project_memory(
            {
                "action": "supersede",
                "memory_id": original["id"],
                "memory_type": "workflow",
                "title": "NewMemoryNeedle",
                "body": "Use the revised cache layout.",
            }
        )["replacement"]

        self.assertEqual(
            [],
            self.search(
                "OldMemoryNeedle",
                "project_memory",
                "lexical",
            )["matches"],
        )
        self.assertTrue(
            self.search(
                "NewMemoryNeedle",
                "project_memory",
                "hybrid",
            )["matches"]
        )
        with closing(self.index._connect(read_only=True)) as connection:  # noqa: SLF001
            old_status = str(
                connection.execute(
                    "SELECT status FROM project_memories WHERE id = ?",
                    (original["id"],),
                ).fetchone()[0]
            )
            replacement_document = int(
                connection.execute(
                    "SELECT document_id FROM project_memories WHERE id = ?",
                    (replacement["id"],),
                ).fetchone()[0]
            )
        self.assertEqual("superseded", old_status)

        self.store.project_memory(
            {"action": "delete", "memory_id": replacement["id"]}
        )
        self.assertEqual(
            [],
            self.search(
                "NewMemoryNeedle",
                "project_memory",
                "lexical",
            )["matches"],
        )
        with closing(self.index._connect(read_only=True)) as connection:  # noqa: SLF001
            remaining = tuple(
                int(value)
                for value in connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM documents WHERE id = ?), "
                    "(SELECT COUNT(*) FROM chunks WHERE document_id = ?), "
                    "(SELECT COUNT(*) FROM chunk_vectors WHERE chunk_id IN "
                    "(SELECT id FROM chunks WHERE document_id = ?))",
                    (
                        replacement_document,
                        replacement_document,
                        replacement_document,
                    ),
                ).fetchone()
            )
        self.assertEqual((0, 0, 0), remaining)

    def test_source_filters_full_content_dedup_and_refresh_false_are_stable(
        self,
    ) -> None:
        user_path = self.write_text(
            "fixtures/full.md",
            "FullUserNeedle\n\nComplete user material body.",
        )
        user_record = normalize_selected_file(
            user_path,
            source_id="full.md",
        ).records[0]
        self.store.refresh_explicit_sources((user_record,))
        official_id = _seed_document(
            self.index,
            source_key="official:duplicate",
            title="Official duplicate",
            bodies=("CrossSourceDuplicateNeedle identical body",),
            collection="builtin",
            source_group="project",
            source="builtin_official_workflow",
        )
        community_id = _seed_document(
            self.index,
            source_key="community:duplicate",
            title="Community duplicate",
            bodies=("CrossSourceDuplicateNeedle identical body",),
            collection="community",
            source_group="project",
            source="community_tutorial",
        )
        with closing(self.index._connect()) as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE documents SET verification = ? WHERE id = ?",
                ("community_unverified", community_id),
            )
        _build_all_vectors(self.store)

        unfiltered = self.store.search_many(
            ("CrossSourceDuplicateNeedle",),
            {"project"},
            current_houdini_version="21.0.440",
            offset=0,
            limit=20,
            mode="lexical",
        )[0]
        duplicate_ids = {
            item["metadata"]["document_id"]
            for item in unfiltered["matches"]
            if item["metadata"]["document_id"] in {official_id, community_id}
        }
        self.assertEqual(1, len(duplicate_ids))
        self.assertTrue(
            self.search(
                "CrossSourceDuplicateNeedle",
                "community_tutorial",
                "lexical",
            )["matches"]
        )

        metadata_calls: list[str] = []
        hou = _FakeHou(
            self.project_root / "fake-help",
            metadata_calls.append,
        )
        cache = self.project_root / ".runtime" / "cache"
        with mock.patch.dict(os.environ, {"HIA_CACHE_DIR": str(cache)}):
            executor = HoudiniExecutor(
                hou_module=hou,
                main_thread_runner=lambda callback: callback(),
                project_root=self.project_root,
            )
        executor._hybrid_knowledge_store = (  # type: ignore[method-assign]
            lambda *, initialize: self.store
        )
        before = self.database_fingerprint()
        response = executor.dispatch(
            "hia_local_help_search",
            {
                "query": "FullUserNeedle",
                "source_kinds": ["user_document"],
                "mode": "lexical",
                "response_format": "full",
                "refresh": False,
            },
        )
        after = self.database_fingerprint()

        self.assertEqual(before, after)
        self.assertEqual(1, len(response["result"]["matches"]))
        full = response["result"]["matches"][0]
        self.assertEqual("user_document", full["source_kind"])
        self.assertEqual("user_supplied_unverified", full["metadata"]["verification"])
        self.assertIn("Complete user material body.", full["content"])
        self.assertFalse(full["content_truncated"])

        community = executor.dispatch(
            "hia_local_help_search",
            {
                "query": "CrossSourceDuplicateNeedle",
                "source_kinds": ["community_tutorial"],
                "mode": "lexical",
                "response_format": "diagnostic",
                "refresh": False,
            },
        )["result"]["matches"][0]
        self.assertEqual("community_tutorial", community["source_kind"])
        self.assertEqual(
            "community_unverified",
            community["metadata"]["verification"],
        )
        self.assertIn("identical body", community["content"])


if __name__ == "__main__":
    unittest.main()
