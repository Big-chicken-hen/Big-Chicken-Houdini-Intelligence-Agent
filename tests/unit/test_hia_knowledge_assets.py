from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT))

from hia_mcp_runtime import knowledge_index_cli as cli  # noqa: E402
from hia_mcp_runtime import knowledge_assets as asset_core  # noqa: E402
from hia_mcp_runtime.deterministic_sources import (  # noqa: E402
    NormalizedSource,
)
from hia_mcp_runtime.hybrid_knowledge import (  # noqa: E402
    HybridKnowledgeStore,
)
from hia_mcp_runtime.knowledge_assets import (  # noqa: E402
    ASSET_COLLECTION,
    ASSET_MANIFEST_SCHEMA,
    KnowledgeAssetManager,
    normalize_asset_fragment,
)
from hia_mcp_runtime.knowledge_index import LocalKnowledgeIndex  # noqa: E402
from hia_mcp_runtime.local_extractors import (  # noqa: E402
    ExtractionConfig,
    ExtractionResult,
    LocalAssetExtractor,
)
from tests.unit.test_hia_mcp_v2_hybrid_knowledge import (  # noqa: E402
    FakeEmbedder,
)


TEST_RUN_ROOT = REPOSITORY_ROOT / ".runtime" / "test-runs"
TEST_RUN_ROOT.mkdir(parents=True, exist_ok=True)


class _Store:
    def __init__(self, root: Path, *, embedder: Any | None = None) -> None:
        self.index = LocalKnowledgeIndex(root)
        self._hybrid = HybridKnowledgeStore(
            root,
            index=self.index,
            embedder=embedder or FakeEmbedder(available=False),
        )

    def vectorize_documents(
        self,
        document_ids: tuple[int, ...],
    ) -> dict[str, Any]:
        return self._hybrid.vectorize_documents(document_ids)

    def status(self) -> dict[str, Any]:
        return self._hybrid.status()

    def close(self) -> None:
        self._hybrid.close()


class _UnitExtractor:
    def capabilities(self) -> Mapping[str, Any]:
        return {
            "local_only": True,
            "network_access": False,
            "capabilities": {
                "fixture": {
                    "name": "fixture",
                    "status": "ready",
                    "formats": [".fixture"],
                }
            },
            "formats": {
                ".fixture": {"adapter": "fixture", "status": "ready"}
            },
        }

    def extract(
        self,
        path: Path,
        *,
        asset_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        del path
        start = int((checkpoint or {}).get("next", 0))
        end = min(5, start + 2)
        complete = end >= 5
        return ExtractionResult(
            status="ready" if complete else "partial",
            fragments=tuple(
                {
                    "asset_id": str(asset_id),
                    "fragment_id": f"part-{ordinal}",
                    "text": f"ResumableFixtureNeedle fragment {ordinal}",
                    "locator": {"page": ordinal + 1},
                    "metadata": {"adapter": "fixture"},
                }
                for ordinal in range(start, end)
            ),
            details={"adapter": "fixture"},
            checkpoint={"next": end, "complete": complete},
        )


class _FakeAsrBackend:
    name = "fixture_asr"

    def capability(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "status": "available",
            "available": True,
            "action": "",
        }

    def duration_seconds(self, path: Path) -> float:
        del path
        return 25.0

    def transcribe_slice(
        self,
        path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> tuple[Mapping[str, Any], ...]:
        del path
        return (
            {
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "text": (
                    f"MediaResumeNeedle slice {int(start_seconds)} "
                    f"to {int(end_seconds)}"
                ),
            },
        )


class _CapturingRefreshStore:
    def __init__(self, index: LocalKnowledgeIndex) -> None:
        self.index = index
        self.document_ids: tuple[int, ...] = ()

    def vectorize_documents(
        self,
        document_ids: tuple[int, ...],
    ) -> dict[str, Any]:
        self.document_ids = tuple(document_ids)
        return {"selected": list(document_ids)}

    def status(self) -> dict[str, Any]:
        return {"selected": []}


class _FailingVectorStore(_Store):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fail_next_vector = True

    def vectorize_documents(
        self,
        document_ids: tuple[int, ...],
    ) -> dict[str, Any]:
        if self.fail_next_vector:
            self.fail_next_vector = False
            raise RuntimeError("fixture vector interruption")
        return super().vectorize_documents(document_ids)


def _minimal_project(root: Path) -> None:
    markers = (
        root / "src" / "hia_core" / "embedding_contract.py",
        root
        / "houdini_package"
        / "python_libs"
        / "hia_mcp_runtime"
        / "hybrid_knowledge.py",
    )
    for marker in markers:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("# test marker\n", encoding="utf-8")


def _run_cli(
    root: Path,
    *arguments: str,
) -> tuple[int, list[dict[str, Any]]]:
    output = io.StringIO()
    exit_code = cli.main(
        ("--project-root", str(root), *arguments),
        stdout=output,
    )
    return exit_code, [
        json.loads(line)
        for line in output.getvalue().splitlines()
        if line.strip()
    ]


def _run_cli_with_vectors(
    root: Path,
    *arguments: str,
) -> tuple[int, list[dict[str, Any]]]:
    def open_store(
        project_root: Path,
        read_only: bool = False,
    ) -> _Store:
        del read_only
        return _Store(project_root, embedder=FakeEmbedder())

    with mock.patch.object(cli, "_open_store", side_effect=open_store):
        return _run_cli(root, *arguments)


class KnowledgeAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_RUN_ROOT)
        self.project_root = Path(self._temporary.name)
        _minimal_project(self.project_root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_each_fragment_is_one_document_and_manifest_owns_source_keys(
        self,
    ) -> None:
        source = self.project_root / "long-notes.txt"
        source.write_text(
            "AssetFragmentNeedle " * 100,
            encoding="utf-8",
        )
        store = _Store(self.project_root)
        self.addCleanup(store.close)
        manager = KnowledgeAssetManager(
            self.project_root,
            store=store,
            extractor=LocalAssetExtractor(
                ExtractionConfig(
                    project_root=self.project_root,
                    max_fragment_chars=256,
                )
            ),
        )

        status = manager.import_asset(
            source,
            asset_id="asset-notes",
            batch_size=2,
        )

        manifest_path = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "assets"
            / "asset-notes"
            / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(ASSET_MANIFEST_SCHEMA, manifest["schema"])
        self.assertEqual(status["fragment_count"], len(manifest["source_keys"]))
        self.assertEqual(
            len(manifest["source_keys"]),
            len(set(manifest["source_keys"])),
        )
        self.assertGreater(status["fragment_count"], 1)
        self.assertEqual(status["fragment_count"], status["documents_indexed"])

        with closing(store.index._connect(read_only=True)) as connection:
            rows = connection.execute(
                "SELECT collection, source_key, attributes_json "
                "FROM documents WHERE collection = ? ORDER BY id",
                (ASSET_COLLECTION,),
            ).fetchall()
        self.assertEqual(status["fragment_count"], len(rows))
        self.assertEqual(
            manifest["source_keys"],
            [str(row[1]) for row in rows],
        )
        for row in rows:
            attributes = json.loads(str(row[2]))
            self.assertEqual("asset-notes", attributes["asset_id"])
            self.assertTrue(attributes["fragment_id"])
            self.assertTrue(attributes["locator"])

    def test_partial_units_resume_from_extractor_checkpoint_idempotently(
        self,
    ) -> None:
        source = self.project_root / "recording.fixture"
        source.write_bytes(b"fixture bytes")
        store = _Store(self.project_root)
        self.addCleanup(store.close)
        manager = KnowledgeAssetManager(
            self.project_root,
            store=store,
            extractor=_UnitExtractor(),
        )

        partial = manager.import_asset(
            source,
            asset_id="asset-resume",
            batch_size=2,
        )
        self.assertEqual("partial", partial["status"])
        self.assertEqual(2, partial["next_fragment"])
        self.assertEqual(2, partial["documents_indexed"])

        second = manager.resume_asset(
            "asset-resume",
            batch_size=2,
        )
        self.assertEqual("partial", second["status"])
        self.assertEqual(4, second["documents_indexed"])

        complete = manager.resume_asset("asset-resume", batch_size=2)
        self.assertEqual("complete", complete["status"])
        self.assertEqual(5, complete["fragment_count"])
        self.assertEqual(5, complete["documents_indexed"])

        repeated = manager.resume_asset("asset-resume", batch_size=2)
        self.assertEqual(5, repeated["documents_indexed"])
        with closing(store.index._connect(read_only=True)) as connection:
            self.assertEqual(
                5,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )

    def test_exact_delete_removes_only_manifest_documents(self) -> None:
        source = self.project_root / "delete-me.md"
        source.write_text("DeleteAssetNeedle " * 100, encoding="utf-8")
        store = _Store(self.project_root)
        self.addCleanup(store.close)
        manager = KnowledgeAssetManager(
            self.project_root,
            store=store,
            extractor=LocalAssetExtractor(
                ExtractionConfig(
                    project_root=self.project_root,
                    max_fragment_chars=256,
                )
            ),
        )
        manager.import_asset(
            source,
            asset_id="asset-delete",
            batch_size=2,
        )
        ordinary = NormalizedSource(
            source_kind="user_document",
            source_key="user:user_document:ordinary-kept",
            title="ordinary kept",
            text="OrdinaryKeptNeedle",
            verification="user_supplied_unverified",
            content_hash="ordinary-hash",
            provenance={"selected_path": "ordinary.md"},
            metadata={},
        )
        store.index.refresh_explicit_records((ordinary,))

        result = manager.delete_asset("asset-delete")

        self.assertTrue(result["deleted"])
        self.assertGreater(result["documents_deleted"], 1)
        with closing(store.index._connect(read_only=True)) as connection:
            self.assertEqual(
                0,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )
            self.assertEqual(
                1,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE source_key = 'user:user_document:ordinary-kept'"
                    ).fetchone()[0]
                ),
            )

    def test_vector_failure_preserves_exact_manifest_ownership(self) -> None:
        source = self.project_root / "interrupted.txt"
        source.write_text(
            "InterruptedVectorNeedle " * 20,
            encoding="utf-8",
        )
        store = _FailingVectorStore(self.project_root)
        self.addCleanup(store.close)
        manager = KnowledgeAssetManager(
            self.project_root,
            store=store,
            extractor=LocalAssetExtractor(
                ExtractionConfig(
                    project_root=self.project_root,
                    max_fragment_chars=256,
                )
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "fixture vector interruption",
        ):
            manager.import_asset(
                source,
                asset_id="asset-vector-interrupted",
                batch_size=2,
            )

        asset_root = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "assets"
            / "asset-vector-interrupted"
        )
        manifest = json.loads(
            (asset_root / "manifest.json").read_text(encoding="utf-8")
        )
        checkpoint = json.loads(
            (asset_root / "checkpoint.json").read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["source_keys"])
        self.assertEqual(
            manifest["source_keys"],
            checkpoint["source_keys"],
        )
        self.assertIsNone(checkpoint["extractor_checkpoint"])
        with closing(store.index._connect(read_only=True)) as connection:
            self.assertEqual(
                len(manifest["source_keys"]),
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )

        deleted = manager.delete_asset("asset-vector-interrupted")

        self.assertEqual(
            len(manifest["source_keys"]),
            deleted["documents_deleted"],
        )
        with closing(store.index._connect(read_only=True)) as connection:
            self.assertEqual(
                0,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )

    def test_sources_refresh_vectorizes_only_legacy_user_collection(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        (index.sources_root / "ordinary.md").write_text(
            "OrdinaryRefreshNeedle",
            encoding="utf-8",
        )
        index.refresh(
            {"user"},
            {"houdini_version": "unknown"},
            force=True,
        )
        asset_record, _descriptor = normalize_asset_fragment(
            "asset-isolated",
            {
                "fragment_id": "fragment-1",
                "text": "AssetRefreshNeedle",
                "locator": "page:1",
            },
            ordinal=0,
            asset_title="isolated",
            managed_source_path=".runtime/knowledge/assets/source.txt",
            source_sha256="source-hash",
            extractor_name="fixture",
        )
        asset_refresh = index.refresh_explicit_records(
            (asset_record,),
            collection=ASSET_COLLECTION,
        )
        asset_document_id = int(asset_refresh["document_ids"][0])
        store = _CapturingRefreshStore(index)

        cli._sources_refresh(store)  # noqa: SLF001

        self.assertNotIn(asset_document_id, store.document_ids)
        with closing(index._connect(read_only=True)) as connection:
            selected_collections = {
                str(row[0])
                for row in connection.execute(
                    "SELECT collection FROM documents "
                    f"WHERE id IN ({','.join('?' for _ in store.document_ids)})",
                    list(store.document_ids),
                ).fetchall()
            }
        self.assertEqual({"user"}, selected_collections)

    def test_asset_fragment_identity_preserves_duplicate_text_and_provenance(
        self,
    ) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        records = []
        for ordinal in range(2):
            record, _descriptor = normalize_asset_fragment(
                "asset-locators",
                {
                    "fragment_id": f"locator-{ordinal}",
                    "text": "DuplicateAssetLocatorNeedle",
                    "locator": "L" * (1100 + ordinal),
                },
                ordinal=ordinal,
                asset_title="locators",
                managed_source_path=".runtime/knowledge/assets/source.txt",
                source_sha256="source-hash",
                extractor_name="fixture",
            )
            records.append(record)
        index.refresh_explicit_records(records, collection=ASSET_COLLECTION)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(available=False),
        )
        self.addCleanup(store.close)

        result = store.search_many(
            ("DuplicateAssetLocatorNeedle",),
            {"user"},
            current_houdini_version="unknown",
            offset=0,
            limit=10,
            mode="lexical",
        )[0]

        self.assertEqual(2, len(result["matches"]))
        self.assertEqual(
            {"locator-0", "locator-1"},
            {
                match["provenance"]["fragment_id"]
                for match in result["matches"]
            },
        )
        for match in result["matches"]:
            provenance = match["provenance"]
            self.assertEqual("asset-locators", provenance["asset_id"])
            self.assertLessEqual(len(provenance["locator"]), 1024)

    def test_cli_office_import_reaches_shared_fts_and_vector_tables(self) -> None:
        source = self.project_root / "office.docx"
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>OfficeAssetNeedle first paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>OfficeAssetNeedle second paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", document_xml)

        exit_code, events = _run_cli_with_vectors(
            self.project_root,
            "assets",
            "import",
            "--path",
            str(source),
            "--asset-id",
            "asset-office",
        )

        self.assertEqual(0, exit_code)
        state = events[-1]["result"]
        self.assertEqual("ready", state["status"])
        self.assertEqual(2, state["fragments"])
        self.assertTrue(state["lexical"]["complete"])
        self.assertTrue(state["vector"]["complete"])
        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:
            self.assertEqual(
                2,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )
            self.assertGreaterEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM knowledge_fts "
                        "WHERE knowledge_fts MATCH 'OfficeAssetNeedle'"
                    ).fetchone()[0]
                ),
                2,
            )
            self.assertGreaterEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chunk_vectors"
                    ).fetchone()[0]
                ),
                2,
            )

    def test_cli_media_resume_commits_each_slice_checkpoint_and_vectors(
        self,
    ) -> None:
        source = self.project_root / "recording.wav"
        source.write_bytes(b"fixture wave bytes")
        extractor = LocalAssetExtractor(
            ExtractionConfig(
                project_root=self.project_root,
                media_slice_seconds=10.0,
                max_media_slices_per_call=1,
            ),
            asr_backend=_FakeAsrBackend(),
        )

        with mock.patch.object(
            asset_core,
            "LocalAssetExtractor",
            return_value=extractor,
        ):
            first_exit, first_events = _run_cli_with_vectors(
                self.project_root,
                "assets",
                "import",
                "--path",
                str(source),
                "--asset-id",
                "asset-media",
            )
            second_exit, second_events = _run_cli_with_vectors(
                self.project_root,
                "assets",
                "resume",
                "--asset-id",
                "asset-media",
            )
            third_exit, third_events = _run_cli_with_vectors(
                self.project_root,
                "assets",
                "resume",
                "--asset-id",
                "asset-media",
            )

        self.assertEqual((0, 0, 0), (first_exit, second_exit, third_exit))
        first = first_events[-1]["result"]
        second = second_events[-1]["result"]
        third = third_events[-1]["result"]
        self.assertEqual(("partial", 10, 25, 1), (
            first["status"],
            first["processed_units"],
            first["total_units"],
            first["fragments"],
        ))
        self.assertEqual(("partial", 20, 25, 2), (
            second["status"],
            second["processed_units"],
            second["total_units"],
            second["fragments"],
        ))
        self.assertEqual(("ready", 25, 25, 3), (
            third["status"],
            third["processed_units"],
            third["total_units"],
            third["fragments"],
        ))
        self.assertTrue(third["lexical"]["complete"])
        self.assertTrue(third["vector"]["complete"])

        checkpoint_path = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "assets"
            / "asset-media"
            / "checkpoint.json"
        )
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        self.assertTrue(checkpoint["extractor_checkpoint"]["complete"])
        self.assertEqual(
            25.0,
            checkpoint["extractor_checkpoint"]["next_start_seconds"],
        )
        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:
            self.assertEqual(
                3,
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents "
                        "WHERE collection = ?",
                        (ASSET_COLLECTION,),
                    ).fetchone()[0]
                ),
            )
            self.assertGreaterEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM knowledge_fts "
                        "WHERE knowledge_fts MATCH 'MediaResumeNeedle'"
                    ).fetchone()[0]
                ),
                3,
            )
            self.assertGreaterEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chunk_vectors"
                    ).fetchone()[0]
                ),
                3,
            )

    def test_cli_jsonl_progress_and_stable_launcher_state(self) -> None:
        source = self.project_root / "cli-asset.srt"
        source.write_text(
            "1\n00:00:00,000 --> 00:00:03,000\n"
            "CliAssetNeedle first cue\n\n"
            "2\n00:00:03,000 --> 00:00:06,000\n"
            "CliAssetNeedle second cue\n",
            encoding="utf-8",
        )

        exit_code, events = _run_cli(
            self.project_root,
            "assets",
            "import",
            "--path",
            str(source),
            "--asset-id",
            "asset-cli",
        )

        self.assertEqual(0, exit_code)
        progress = [event for event in events if event["event"] == "progress"]
        self.assertTrue(progress)
        required = {
            "id",
            "title",
            "kind",
            "stage",
            "status",
            "processed_units",
            "total_units",
            "fragments",
            "lexical",
            "vector",
            "error",
            "recoverable",
        }
        self.assertEqual(required, set(progress[-1]["asset"]))
        result = events[-1]["result"]
        self.assertEqual(required, set(result))
        self.assertEqual("asset-cli", result["id"])
        self.assertEqual("ready", result["status"])
        self.assertEqual(result["processed_units"], result["total_units"])

        list_exit, list_events = _run_cli(
            self.project_root,
            "assets",
            "list",
            "--offset",
            "0",
            "--limit",
            "10",
        )
        self.assertEqual(0, list_exit)
        self.assertEqual(
            required,
            set(list_events[-1]["result"]["items"][0]),
        )

        status_exit, status_events = _run_cli(
            self.project_root,
            "assets",
            "status",
            "--asset-id",
            "asset-cli",
        )
        self.assertEqual(0, status_exit)
        self.assertEqual(
            status_events[-1]["result"],
            status_events[-1]["asset"],
        )

        capability_exit, capability_events = _run_cli(
            self.project_root,
            "--format",
            "json",
            "assets",
            "capabilities",
        )
        self.assertEqual(0, capability_exit)
        self.assertEqual(1, len(capability_events))
        self.assertTrue(
            capability_events[0]["result"]["extractors"][0]["suffixes"]
        )


if __name__ == "__main__":
    unittest.main()
