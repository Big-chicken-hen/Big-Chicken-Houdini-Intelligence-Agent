from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HiaRuntimeError, HoudiniExecutor  # noqa: E402
from hia_mcp_runtime.hybrid_knowledge import HybridKnowledgeStore  # noqa: E402
from hia_mcp_runtime import knowledge_index  # noqa: E402
from hia_mcp_runtime.knowledge_index import LocalKnowledgeIndex  # noqa: E402


class _FakeHipFile:
    def __init__(self, record_metadata_call: Callable[[str], None]) -> None:
        self._record_metadata_call = record_metadata_call

    def hasUnsavedChanges(self) -> bool:  # noqa: N802
        self._record_metadata_call("hipFile.hasUnsavedChanges")
        return True


class _FakeNodeType:
    def __init__(self, record_metadata_call: Callable[[str], None]) -> None:
        self._record_metadata_call = record_metadata_call

    def _record(self, name: str) -> None:
        self._record_metadata_call(f"node_type.{name}")

    def name(self) -> str:
        self._record("name")
        return "needle"

    def description(self) -> str:
        self._record("description")
        return "Needle installed Houdini node"

    def nameComponents(self) -> tuple[str, ...]:  # noqa: N802
        self._record("nameComponents")
        return ("", "needle", "", "")

    def minNumInputs(self) -> int:  # noqa: N802
        self._record("minNumInputs")
        return 0

    def maxNumInputs(self) -> int:  # noqa: N802
        self._record("maxNumInputs")
        return 1

    def maxNumOutputs(self) -> int:  # noqa: N802
        self._record("maxNumOutputs")
        return 1

    def childTypeCategory(self) -> None:  # noqa: N802
        self._record("childTypeCategory")
        return None

    def deprecated(self) -> bool:
        self._record("deprecated")
        return False


class _FakeCategory:
    def __init__(
        self,
        node_type: _FakeNodeType,
        record_metadata_call: Callable[[str], None],
    ) -> None:
        self._node_type = node_type
        self._record_metadata_call = record_metadata_call

    def name(self) -> str:
        self._record_metadata_call("category.name")
        return "Sop"

    def nodeTypes(self) -> dict[str, _FakeNodeType]:  # noqa: N802
        self._record_metadata_call("category.nodeTypes")
        return {"needle": self._node_type}


class _FakeHou:
    def __init__(
        self,
        help_root: Path,
        record_metadata_call: Callable[[str], None],
    ) -> None:
        self._help_root = help_root
        self._record_metadata_call = record_metadata_call
        self.hipFile = _FakeHipFile(record_metadata_call)
        node_type = _FakeNodeType(record_metadata_call)
        self._category = _FakeCategory(node_type, record_metadata_call)

    def nodeTypeCategories(self) -> dict[str, _FakeCategory]:  # noqa: N802
        self._record_metadata_call("hou.nodeTypeCategories")
        return {"Sop": self._category}

    def applicationVersionString(self) -> str:  # noqa: N802
        self._record_metadata_call("hou.applicationVersionString")
        return "21.0.000"

    def expandString(self, value: str) -> str:  # noqa: N802
        self._record_metadata_call("hou.expandString")
        if value != "$HH/help":
            raise AssertionError(f"unexpected Houdini expansion: {value}")
        return str(self._help_root)


class HiaMcpV2LocalHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name) / "local-help-project"
        self.project_root.mkdir()

        self.help_root = self.project_root / ".runtime" / "fake-hh" / "help"
        self._write(self.help_root / "nodes" / "needle.txt", "Needle current Houdini help")
        self._write(
            self.project_root / ".agents" / "skills" / "current-skill" / "SKILL.md",
            "Needle current Skill entry",
        )
        self._write(
            self.project_root
            / ".agents"
            / "skills"
            / "current-skill"
            / "references"
            / "review.md",
            "Needle current Skill reference",
        )
        self._write(
            self.project_root
            / ".agents"
            / "skills"
            / "current-skill"
            / "agents"
            / "openai.yaml",
            "description: Needle metadata noise",
        )
        self._write(
            self.project_root / "docs" / "HIA-MCP-V2.md",
            "Needle current published project documentation",
        )
        self._write(
            self.project_root / "docs" / "TEST-REPORT.md",
            "Needle historical test report noise",
        )
        self._write(
            self.project_root / "docs" / "P2-V-GATE-B2C.md",
            "Needle historical Gate noise",
        )

        self.in_runner = False
        self.runner_calls = 0
        self.metadata_calls: list[tuple[str, bool]] = []
        self.file_read_states: list[tuple[Path, bool]] = []

        def record_metadata_call(name: str) -> None:
            self.metadata_calls.append((name, self.in_runner))

        def main_thread_runner(callback: Callable[[], Any]) -> Any:
            self.runner_calls += 1
            if self.in_runner:
                raise AssertionError("nested main-thread dispatch")
            self.in_runner = True
            try:
                return callback()
            finally:
                self.in_runner = False

        cache_root = self.project_root / ".runtime" / "cache"
        with mock.patch.dict(
            os.environ,
            {"HIA_CACHE_DIR": str(cache_root)},
            clear=False,
        ):
            self.executor = HoudiniExecutor(
                hou_module=_FakeHou(self.help_root, record_metadata_call),
                main_thread_runner=main_thread_runner,
                project_root=self.project_root,
            )

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _dispatch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        original_read_text = Path.read_text

        def tracked_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            self.file_read_states.append((path, self.in_runner))
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", tracked_read_text):
            return self.executor.dispatch("hia_local_help_search", arguments)

    def _write_pack(
        self,
        root: Path,
        *,
        version: str,
        cards: list[tuple[str, str, str]],
    ) -> None:
        pack_root = root / "knowledge" / "sidefx-official"
        pack_id = "test-official-workflows"
        entries = []
        source_registry = []
        for index, (card_id, title, body) in enumerate(cards, start=1):
            relative = f"cards/{card_id}.md"
            source_id = f"source-{index}"
            source_url = f"https://www.sidefx.com/docs/houdini/{card_id}/"
            self._write(pack_root / relative, body)
            entries.append(
                {
                    "id": card_id,
                    "canonical_id": card_id,
                    "title": title,
                    "path": relative,
                    "url": source_url,
                    "source_ids": [source_id],
                    "houdini_version": "21",
                    "topic": "workflow",
                    "aliases": [title.casefold()],
                }
            )
            source_registry.append(
                {
                    "id": source_id,
                    "title": title,
                    "url": source_url,
                }
            )
        self._write(
            pack_root / "coverage.json",
            json.dumps(
                {
                    "pack_id": pack_id,
                    "pack_version": version,
                    "cards": len(entries),
                }
            ),
        )
        self._write(
            pack_root / "sources.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "pack_id": pack_id,
                    "pack_version": version,
                    "sources": source_registry,
                }
            ),
        )
        self._write(
            pack_root / "manifest.json",
            json.dumps(
                {
                    "schema_version": 2,
                    "pack_id": pack_id,
                    "pack_version": version,
                    "publisher": "Big-Chicken contributors",
                    "card_license": "Apache-2.0",
                    "sources": entries,
                }
            ),
        )

    def test_search_runs_file_io_off_ui_and_uses_only_allowed_sources(self) -> None:
        response = self._dispatch(
            {
                "query": "Needle",
                "limit": 50,
                "refresh": True,
                "response_format": "full",
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(1, self.runner_calls)
        self.assertTrue(self.metadata_calls)
        self.assertTrue(all(in_runner for _name, in_runner in self.metadata_calls))
        self.assertTrue(self.file_read_states)
        self.assertTrue(
            all(not in_runner for _path, in_runner in self.file_read_states)
        )
        self.assertIn("runtime_ui_snapshot_seconds", response["phase_timings"])
        self.assertIn("local_file_search_seconds", response["phase_timings"])
        matches = response["result"]["matches"]
        identities = {(item["source"], item["title"]) for item in matches}

        self.assertEqual(4, response["result"]["total"])
        self.assertEqual(
            {
                ("houdini_node_catalog", "Sop::needle"),
                ("project_skill", "current-skill/SKILL.md"),
                ("project_skill", "current-skill/references/review.md"),
                ("project_docs", "HIA-MCP-V2.md"),
            },
            identities,
        )
        read_names = {path.name for path, _in_runner in self.file_read_states}
        self.assertNotIn("TEST-REPORT.md", read_names)
        self.assertNotIn("P2-V-GATE-B2C.md", read_names)
        self.assertNotIn("openai.yaml", read_names)
        self.assertTrue(
            (
                self.project_root
                / ".runtime"
                / "knowledge"
                / "knowledge.sqlite3"
            ).is_file()
        )
        by_source = {item["source"]: item for item in matches}
        self.assertEqual(
            "verified",
            by_source["houdini_node_catalog"]["metadata"]["verification"],
        )
        self.assertTrue(
            all(
                item["metadata"]["verification"] == "unverified"
                for item in matches
                if item["source"] != "houdini_node_catalog"
            )
        )
        for item in matches:
            self.assertTrue(
                {
                    "url",
                    "author",
                    "accessed_at",
                    "houdini_version",
                    "license",
                    "verification",
                    "evidence",
                    "sha256",
                }.issubset(item["metadata"])
            )

        reads_after_initial_index = len(self.file_read_states)
        second = self._dispatch({"query": "Needle", "limit": 50})
        self.assertTrue(second["ok"])
        self.assertEqual(reads_after_initial_index, len(self.file_read_states))
        self.assertFalse(second["result"]["index"]["refreshed"])
        self.assertEqual(0, second["result"]["files_scanned"])

    def test_refresh_false_is_read_only_and_does_not_initialize_storage(
        self,
    ) -> None:
        database = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "knowledge.sqlite3"
        )

        absent = self._dispatch(
            {
                "query": "Needle",
                "sources": ["project"],
                "mode": "lexical",
            }
        )

        self.assertTrue(absent["ok"])
        self.assertFalse(database.exists())
        self.assertEqual([], self.file_read_states)
        self.assertEqual(
            "read_only",
            absent["result"]["index"]["refresh_reason"],
        )
        self.assertFalse(
            absent["result"]["index"]["corpus"]["available"]
        )
        self.assertTrue(absent["warnings"])

        self._dispatch(
            {
                "query": "Needle",
                "sources": ["project"],
                "mode": "lexical",
                "refresh": True,
            }
        )
        before = database.read_bytes()
        self.file_read_states.clear()
        with mock.patch.object(
            LocalKnowledgeIndex,
            "refresh",
            side_effect=AssertionError("read-only search refreshed sources"),
        ):
            cached = self._dispatch(
                {
                    "query": "Needle",
                    "sources": ["project"],
                    "mode": "lexical",
                }
            )

        self.assertEqual(before, database.read_bytes())
        self.assertEqual([], self.file_read_states)
        self.assertFalse(cached["result"]["index"]["refreshed"])
        self.assertEqual(0, cached["result"]["files_scanned"])
        self.assertTrue(
            cached["result"]["index"]["corpus"]["available"]
        )
        self.assertEqual(
            "ready",
            cached["result"]["index"]["corpus"]["state"],
        )
        self.assertFalse(
            cached["result"]["retrieval"]["encoder"]["loaded"]
        )

    def test_inline_catalog_refresh_has_stable_metadata_hash(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        snapshot = {
            "houdini_version": "21.0.000",
            "houdini_help_root": "",
            "catalog": [
                {
                    "category": "Sop",
                    "name": "needle",
                    "description": "Needle installed Houdini node",
                }
            ],
        }

        first, _warnings = index.refresh(
            {"houdini"},
            snapshot,
            force=True,
        )
        second, _warnings = index.refresh(
            {"houdini"},
            snapshot,
            force=True,
        )

        self.assertEqual(1, first["documents_added"])
        self.assertEqual(1, second["inline_records_scanned"])
        self.assertEqual(0, second["documents_updated"])
        self.assertEqual(0, second["documents_body_changed"])
        self.assertEqual(0, second["documents_metadata_changed"])
        self.assertEqual(1, second["documents_unchanged"])

    def test_node_lookup_and_workflow_intents_fold_catalog_and_help(self) -> None:
        self._write(
            self.help_root / "nodes" / "needle.txt",
            "Needle workflow help explains the complete installed procedure.",
        )
        workflow = self._dispatch(
            {
                "query": "Needle workflow help",
                "sources": ["houdini"],
                "mode": "lexical",
                "refresh": True,
                "response_format": "full",
            }
        )
        self.assertEqual(
            ["houdini_help"],
            [
                match["source"]
                for match in workflow["result"]["matches"]
            ],
        )

        exact = self._dispatch(
            {
                "query": "Needle",
                "sources": ["houdini"],
                "mode": "lexical",
                "response_format": "full",
            }
        )
        self.assertEqual(
            ["houdini_node_catalog"],
            [match["source"] for match in exact["result"]["matches"]],
        )
        self.assertEqual(1, exact["result"]["total"])

    def test_compact_response_obeys_byte_budget_and_reports_timings(self) -> None:
        sources = (
            self.project_root / ".runtime" / "knowledge" / "sources"
        )
        for number in range(12):
            self._write(
                sources / f"budget-{number:02d}.md",
                f"BudgetNeedle {number} " + ("long summary " * 180),
            )

        response = self._dispatch(
            {
                "query": "BudgetNeedle",
                "sources": ["user"],
                "mode": "lexical",
                "limit": 50,
                "refresh": True,
                "max_bytes": 4096,
            }
        )
        result = response["result"]

        self.assertLessEqual(result["response_bytes"], 4096)
        self.assertEqual(
            result["response_bytes"],
            len(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        )
        self.assertTrue(result["truncated"])
        self.assertIsInstance(result["next_offset"], int)
        self.assertLess(len(result["matches"]), result["total"])
        self.assertEqual(
            {
                "fts_seconds",
                "query_encode_seconds",
                "vector_scan_seconds",
                "serialization_seconds",
            },
            set(result["timings"]),
        )
        self.assertTrue(
            all("metadata" not in match for match in result["matches"])
        )

    def test_full_builtin_cards_truncate_content_without_stalling_pagination(
        self,
    ) -> None:
        first_body = (
            "# Full budget card one\n\nBudgetFullCardNeedle\n\n"
            + ("Detailed official workflow step. " * 360)
            + "\n\nFIRST_CARD_END"
        )
        second_body = (
            "# Full budget card two\n\nBudgetFullCardNeedle\n\n"
            + ("Verified troubleshooting guidance. " * 360)
            + "\n\nSECOND_CARD_END"
        )
        self._write_pack(
            self.project_root,
            version="2026.7.26",
            cards=[
                ("full-budget-one", "Full budget card one", first_body),
                ("full-budget-two", "Full budget card two", second_body),
            ],
        )

        complete = self._dispatch(
            {
                "query": "BudgetFullCardNeedle",
                "sources": ["project"],
                "mode": "lexical",
                "refresh": True,
                "response_format": "full",
                "limit": 2,
            }
        )["result"]
        self.assertEqual(2, len(complete["matches"]))
        self.assertTrue(
            all(not match["content_truncated"] for match in complete["matches"])
        )
        self.assertIn(
            "FIRST_CARD_END",
            "\n".join(match["content"] for match in complete["matches"]),
        )
        self.assertIn(
            "SECOND_CARD_END",
            "\n".join(match["content"] for match in complete["matches"]),
        )

        first_page = self._dispatch(
            {
                "query": "BudgetFullCardNeedle",
                "sources": ["project"],
                "mode": "lexical",
                "response_format": "full",
                "limit": 2,
                "max_bytes": 4096,
            }
        )["result"]
        self.assertEqual(1, len(first_page["matches"]))
        self.assertTrue(first_page["matches"][0]["content_truncated"])
        self.assertLessEqual(first_page["response_bytes"], 4096)
        self.assertEqual(1, first_page["next_offset"])

        second_page = self._dispatch(
            {
                "query": "BudgetFullCardNeedle",
                "sources": ["project"],
                "mode": "lexical",
                "response_format": "diagnostic",
                "offset": first_page["next_offset"],
                "limit": 2,
                "max_bytes": 4096,
            }
        )["result"]
        self.assertEqual(1, len(second_page["matches"]))
        self.assertTrue(second_page["matches"][0]["content_truncated"])
        self.assertLessEqual(second_page["response_bytes"], 4096)
        self.assertIsNone(second_page["next_offset"])
        self.assertNotEqual(
            first_page["matches"][0]["metadata"]["card_id"],
            second_page["matches"][0]["metadata"]["card_id"],
        )

    def test_batch_metadata_alone_stays_within_the_byte_budget(self) -> None:
        response = self._dispatch(
            {
                "queries": [
                    f"missing-{number}-" + ("q" * 220)
                    for number in range(16)
                ],
                "sources": ["user"],
                "mode": "lexical",
                "refresh": True,
                "max_bytes": 4096,
            }
        )
        result = response["result"]

        self.assertLessEqual(result["response_bytes"], 4096)
        self.assertEqual(16, result["query_count"])
        self.assertTrue(result["query_echo_truncated"])
        self.assertEqual(
            list(range(16)),
            [entry["query_index"] for entry in result["queries"]],
        )

    def test_batch_pagination_keeps_independent_query_cursors(self) -> None:
        sources = self.project_root / ".runtime" / "knowledge" / "sources"
        for number in range(7):
            self._write(
                sources / f"cursor-alpha-{number}.md",
                f"CursorAlphaNeedle {number} concise result.",
            )
        for number in range(4):
            self._write(
                sources / f"cursor-beta-{number}.md",
                f"CursorBetaNeedle {number} " + ("large result body " * 90),
            )

        first_page = self._dispatch(
            {
                "queries": ["CursorAlphaNeedle", "CursorBetaNeedle"],
                "sources": ["user"],
                "mode": "lexical",
                "refresh": True,
                "limit": 7,
                "max_bytes": 4096,
            }
        )["result"]
        entries = {
            entry["query"]: entry for entry in first_page["queries"]
        }

        self.assertEqual(7, entries["CursorAlphaNeedle"]["total"])
        self.assertEqual(4, entries["CursorBetaNeedle"]["total"])
        alpha_cursor = entries["CursorAlphaNeedle"]["next_offset"]
        beta_cursor = entries["CursorBetaNeedle"]["next_offset"]
        self.assertIsInstance(alpha_cursor, int)
        self.assertIsInstance(beta_cursor, int)
        self.assertNotEqual(alpha_cursor, beta_cursor)
        self.assertIsNone(first_page["next_offset"])

        for query, cursor in (
            ("CursorAlphaNeedle", alpha_cursor),
            ("CursorBetaNeedle", beta_cursor),
        ):
            first_titles = {
                match["title"] for match in entries[query]["matches"]
            }
            second_page = self._dispatch(
                {
                    "query": query,
                    "sources": ["user"],
                    "mode": "lexical",
                    "offset": cursor,
                    "limit": 7,
                    "max_bytes": 4096,
                }
            )["result"]
            second_titles = {
                match["title"] for match in second_page["matches"]
            }
            self.assertTrue(second_titles)
            self.assertTrue(first_titles.isdisjoint(second_titles))

    def test_fts_relaxes_only_after_strict_query_has_zero_matches(self) -> None:
        sources_root = (
            self.project_root / ".runtime" / "knowledge" / "sources"
        )
        self._write(
            sources_root / "rbd-material-fracture.md",
            "RBD Material Fracture workflow for a detailed concrete wall.",
        )
        self._write(
            sources_root / "rbd-bullet-solver.md",
            "RBD Bullet Solver reference.",
        )
        index = LocalKnowledgeIndex(self.project_root)
        index.refresh(
            {"user"},
            {"houdini_version": "21.0.000"},
            force=True,
        )

        strict = index.search(
            "RBD Material Fracture",
            {"user"},
            current_houdini_version="21.0.000",
            offset=0,
            limit=10,
        )
        self.assertEqual(
            {"user:user_document:rbd-material-fracture.md"},
            {
                match["metadata"]["source_key"]
                for match in strict["matches"]
            },
        )

        relaxed = index.search(
            (
                "RBD Material Fracture SOP concrete wall constraints "
                "interior detail workflow"
            ),
            {"user"},
            current_houdini_version="21.0.000",
            offset=0,
            limit=10,
        )
        self.assertEqual(
            {
                "user:user_document:rbd-bullet-solver.md",
                "user:user_document:rbd-material-fracture.md",
            },
            {
                match["metadata"]["source_key"]
                for match in relaxed["matches"]
            },
        )

    def test_installed_help_archives_are_indexed_without_noise_or_reopening(
        self,
    ) -> None:
        archive_path = self.help_root / "nodes.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "sop/archive_needle.txt",
                "ArchiveNeedle creates a tested procedural shape.",
            )
            archive.writestr(
                "sop/archive_second.txt",
                "ArchiveNeedle second installed help page.",
            )
            archive.writestr(
                "examples/archive_noise.txt",
                "ArchiveExampleNoise should not be indexed.",
            )
            archive.writestr(
                "licenses/archive_noise.txt",
                "ArchiveLicenseNoise should not be indexed.",
            )

        with mock.patch.object(
            knowledge_index.zipfile,
            "ZipFile",
            wraps=zipfile.ZipFile,
        ) as zip_file:
            response = self._dispatch(
                {
                    "query": "ArchiveNeedle",
                    "sources": ["houdini"],
                    "limit": 10,
                    "refresh": True,
                }
            )

        self.assertTrue(response["ok"])
        self.assertEqual(2, response["result"]["total"])
        self.assertEqual(
            {"houdini_help_archive"},
            {item["source"] for item in response["result"]["matches"]},
        )
        self.assertEqual(
            {
                "nodes/sop/archive_needle.txt",
                "nodes/sop/archive_second.txt",
            },
            {item["title"] for item in response["result"]["matches"]},
        )
        self.assertEqual(2, zip_file.call_count)
        self.assertEqual(
            0,
            self._dispatch(
                {
                    "query": "ArchiveExampleNoise",
                    "sources": ["houdini"],
                    "refresh": True,
                }
            )["result"]["total"],
        )
        self.assertEqual(
            0,
            self._dispatch(
                {
                    "query": "ArchiveLicenseNoise",
                    "sources": ["houdini"],
                    "refresh": True,
                }
            )["result"]["total"],
        )

    def test_bad_installed_help_archive_is_a_warning(self) -> None:
        (self.help_root / "nodes.zip").write_bytes(b"not-a-zip")

        response = self._dispatch(
            {
                "query": "Needle",
                "sources": ["houdini"],
                "refresh": True,
            }
        )

        self.assertTrue(response["ok"])
        self.assertTrue(
            any("nodes.zip" in warning for warning in response["warnings"])
        )

    def test_bundled_official_cards_use_manifest_metadata_and_stay_original(
        self,
    ) -> None:
        pack_root = self.project_root / "knowledge" / "sidefx-official"
        self._write(
            pack_root / "cards" / "workflow.md",
            "OriginalWorkflowCard explains a deterministic Houdini workflow.",
        )
        self._write(pack_root / "coverage.json", '{"domains": ["modeling"]}')
        model_url = "https://www.sidefx.com/docs/houdini/model/"
        node_url = "https://www.sidefx.com/docs/houdini/nodes/sop/copytopoints"
        workflow_url = "https://www.sidefx.com/docs/houdini/model/workflow"
        reference_url = "https://www.sidefx.com/docs/houdini/model/reference"
        media_url = "https://media.sidefx.com/files/tutorials/workflow.pdf"
        self._write(
            pack_root / "sources.json",
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "source-model",
                            "title": "Modeling",
                            "url": model_url,
                        },
                        {
                            "id": "source-node",
                            "title": "Copy to Points",
                            "url": node_url,
                        },
                        {
                            "id": "source-media",
                            "title": "Workflow PDF",
                            "url": media_url,
                        },
                    ]
                }
            ),
        )
        self._write(
            pack_root / "manifest.json",
            json.dumps(
                {
                    "schema_version": 2,
                    "pack_id": "test-pack",
                    "pack_version": "2026.1",
                    "sources": [
                        {
                            "id": "workflow",
                            "canonical_id": "workflow",
                            "title": "Original SideFX-linked workflow card",
                            "path": "cards/workflow.md",
                            "url": model_url,
                            "official_urls": [model_url, workflow_url],
                            "source_urls": [reference_url],
                            "urls": [node_url],
                            "source_ids": [
                                "source-model",
                                "source-node",
                                "source-media",
                                "source-model",
                            ],
                            "houdini_version": "21",
                            "topic": "modeling",
                            "aliases": ["procedural modeling"],
                        }
                    ],
                }
            ),
        )

        response = self._dispatch(
            {
                "query": "OriginalWorkflowCard",
                "sources": ["project"],
                "refresh": True,
                "response_format": "full",
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(1, response["result"]["total"])
        match = response["result"]["matches"][0]
        self.assertEqual("builtin_official_workflow", match["source"])
        self.assertEqual("builtin_official_workflow", match["source_kind"])
        self.assertEqual(
            "Original SideFX-linked workflow card",
            match["title"],
        )
        self.assertEqual(
            model_url,
            match["metadata"]["url"],
        )
        self.assertEqual(
            [model_url, workflow_url, reference_url, node_url, media_url],
            match["metadata"]["official_urls"],
        )
        self.assertEqual("21", match["metadata"]["houdini_version"])
        self.assertEqual("Apache-2.0", match["metadata"]["license"])
        self.assertEqual("unverified", match["metadata"]["verification"])
        self.assertIn("OriginalWorkflowCard", match["content"])
        self.assertFalse(match["content_truncated"])
        self.assertEqual("2026.1", match["metadata"]["pack_version"])
        runtime_path = response["result"]["index"]["builtin_pack"]["runtime_path"]
        self.assertTrue((self.project_root / runtime_path / "coverage.json").is_file())
        self.assertTrue((self.project_root / runtime_path / "sources.json").is_file())

    def test_builtin_pack_source_registry_rejects_invalid_lists_and_refs(
        self,
    ) -> None:
        cases = (
            (
                "empty-registry",
                {"sources": []},
                ["source-1"],
                "non-empty sources list",
            ),
            (
                "malformed-registry",
                {"sources": "source-1"},
                ["source-1"],
                "non-empty sources list",
            ),
            (
                "duplicate-id",
                {
                    "sources": [
                        {
                            "id": "source-1",
                            "url": "https://www.sidefx.com/docs/houdini/model/",
                        },
                        {
                            "id": "source-1",
                            "url": "https://www.sidefx.com/docs/houdini/sop/",
                        },
                    ]
                },
                ["source-1"],
                "source id is duplicated",
            ),
            (
                "non-sidefx-url",
                {
                    "sources": [
                        {
                            "id": "source-1",
                            "url": "https://example.invalid/not-sidefx",
                        }
                    ]
                },
                ["source-1"],
                "registry source is not SideFX HTTPS",
            ),
            (
                "empty-source-ids",
                {
                    "sources": [
                        {
                            "id": "source-1",
                            "url": "https://www.sidefx.com/docs/houdini/model/",
                        }
                    ]
                },
                [],
                "source_ids list is invalid",
            ),
            (
                "unknown-source-id",
                {
                    "sources": [
                        {
                            "id": "source-1",
                            "url": "https://www.sidefx.com/docs/houdini/model/",
                        }
                    ]
                },
                ["source-missing"],
                "references an unknown source id",
            ),
        )
        for name, sources_payload, source_ids, expected in cases:
            with self.subTest(name=name):
                root = self.project_root / f"invalid-{name}"
                self._write_pack(
                    root,
                    version="2026.7",
                    cards=[("workflow", "Workflow", "Workflow body")],
                )
                pack_root = root / "knowledge" / "sidefx-official"
                self._write(
                    pack_root / "sources.json",
                    json.dumps(sources_payload),
                )
                manifest = json.loads(
                    (pack_root / "manifest.json").read_text(encoding="utf-8")
                )
                manifest["sources"][0]["source_ids"] = source_ids
                self._write(
                    pack_root / "manifest.json",
                    json.dumps(manifest),
                )

                with self.assertRaisesRegex(
                    knowledge_index.KnowledgeIndexError,
                    expected,
                ):
                    knowledge_index._load_builtin_pack(  # noqa: SLF001
                        pack_root / "manifest.json"
                    )

    def test_builtin_pack_rejects_invalid_bodies_and_stale_metadata(
        self,
    ) -> None:
        cases = (
            ("empty-body", "card", "empty"),
            ("invalid-utf8", "card", "valid UTF-8"),
            ("coverage-pack-id", "coverage", "pack_id"),
            ("sources-pack-version", "sources", "pack_version"),
        )
        for name, mutation, expected in cases:
            with self.subTest(name=name):
                root = self.project_root / f"invalid-{name}"
                self._write_pack(
                    root,
                    version="2026.7",
                    cards=[("workflow", "Workflow", "Workflow body")],
                )
                pack_root = root / "knowledge" / "sidefx-official"
                if name == "empty-body":
                    self._write(
                        pack_root / "cards" / "workflow.md",
                        "\ufeff \n\t",
                    )
                elif name == "invalid-utf8":
                    (pack_root / "cards" / "workflow.md").write_bytes(
                        b"\xff\xfe\x00"
                    )
                else:
                    metadata_path = pack_root / f"{mutation}.json"
                    metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                    if name == "coverage-pack-id":
                        metadata["pack_id"] = "stale-pack"
                    else:
                        metadata["pack_version"] = "stale-version"
                    self._write(metadata_path, json.dumps(metadata))

                with self.assertRaisesRegex(
                    knowledge_index.KnowledgeIndexError,
                    expected,
                ):
                    knowledge_index._load_builtin_pack(  # noqa: SLF001
                        pack_root / "manifest.json"
                    )

    def test_builtin_pack_bootstrap_is_offline_idempotent_and_portable(self) -> None:
        fresh_root = self.project_root / "fresh-portable-project"
        fresh_root.mkdir()
        self._write_pack(
            fresh_root,
            version="2026.7.1",
            cards=[
                (
                    "deep-tail",
                    "Deep tail workflow",
                    "Start with an editable source network.\n\n"
                    + ("bounded procedural preparation " * 90)
                    + "\n\nDeepTailVerificationToken confirms the final output.",
                )
            ],
        )
        self.assertFalse((fresh_root / ".runtime").exists())

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("bootstrap must stay offline"),
        ):
            index = LocalKnowledgeIndex(fresh_root)
            first, first_warnings = index.refresh(
                {"project"},
                {"houdini_version": "21.0"},
                force=True,
            )
            active_path = (
                fresh_root / ".runtime" / "knowledge" / "builtin" / "active.json"
            )
            active_mtime = active_path.stat().st_mtime_ns
            second, second_warnings = index.refresh(
                {"project"},
                {"houdini_version": "21.0"},
                force=True,
            )

        self.assertEqual([], first_warnings)
        self.assertEqual([], second_warnings)
        self.assertTrue(first["builtin_pack"]["changed"])
        self.assertFalse(second["builtin_pack"]["changed"])
        self.assertEqual(active_mtime, active_path.stat().st_mtime_ns)
        status = index.corpus_status()
        self.assertEqual("2026.7.1", status["builtin_pack"]["pack_version"])
        self.assertEqual(1, status["builtin_pack"]["documents"])
        self.assertGreaterEqual(status["builtin_pack"]["chunks"], 2)

        moved_root = self.project_root / "moved-portable-project"
        fresh_root.rename(moved_root)
        moved_index = LocalKnowledgeIndex(moved_root, initialize=False)
        moved = moved_index.search(
            "DeepTailVerificationToken",
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=5,
        )
        self.assertEqual(1, moved["total"])
        self.assertEqual(
            "builtin_official_workflow",
            moved["matches"][0]["source"],
        )
        moved_status = moved_index.corpus_status()["builtin_pack"]
        self.assertTrue(moved_status["runtime_path"].startswith(".runtime/"))
        self.assertNotIn(str(fresh_root), moved_status["runtime_path"])

    def test_pack_upgrade_preserves_user_sources_and_project_memory(self) -> None:
        root = self.project_root / "upgrade-project"
        root.mkdir()
        self._write_pack(
            root,
            version="1.0.0",
            cards=[("cloth", "Cloth workflow", "LegacyClothToken setup.")],
        )
        index = LocalKnowledgeIndex(root)
        index.refresh({"project"}, {"houdini_version": "21.0"}, force=True)
        self._write(
            index.sources_root / "artist-notes.md",
            "UserPreservationToken must survive built-in upgrades.",
        )
        index.refresh({"user"}, {"houdini_version": "21.0"}, force=True)
        store = HybridKnowledgeStore(root, index=index, embedder=object())
        memory = store.project_memory(
            {
                "action": "record",
                "memory_type": "decision",
                "title": "Preserve memory",
                "body": "MemoryPreservationToken remains explicit.",
                "scope": "project",
            }
        )["memory"]
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            preserved_before = connection.execute(
                "SELECT source_key, sha256 FROM documents "
                "WHERE source_group IN ('user', 'memory') ORDER BY source_key"
            ).fetchall()

        self._write_pack(
            root,
            version="2.0.0",
            cards=[
                ("cloth", "Cloth workflow", "UpdatedClothToken setup."),
                ("pyro", "Pyro workflow", "NewPyroToken validation."),
            ],
        )
        refresh, warnings = index.refresh(
            {"project"},
            {"houdini_version": "21.0"},
            force=True,
        )
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            preserved_after = connection.execute(
                "SELECT source_key, sha256 FROM documents "
                "WHERE source_group IN ('user', 'memory') ORDER BY source_key"
            ).fetchall()

        self.assertEqual([], warnings)
        self.assertEqual(preserved_before, preserved_after)
        self.assertEqual("2.0.0", refresh["builtin_pack"]["pack_version"])
        corpus = index.corpus_status()
        self.assertEqual(2, corpus["builtin_pack"]["documents"])
        self.assertEqual(1, corpus["user_documents"])
        self.assertEqual(1, corpus["memory_documents"])
        self.assertEqual(0, corpus["project_documents"])
        self.assertEqual(
            memory["id"],
            store.project_memory(
                {
                    "action": "list",
                    "scope": "project",
                    "limit": 10,
                    "offset": 0,
                }
            )["items"][0]["id"],
        )
        store.close()

    def test_deep_workflow_long_query_compact_and_full_card(self) -> None:
        long_body = (
            "# Velocity-advected ripple field workflow\n\n"
            "Build the source surface and verify its topology before simulation.\n\n"
            + ("Use a bounded editable stage with explicit outputs. " * 80)
            + "\n\nSteps: create HeightField Project, advect velocity through the "
            "ripple field, tune boundary damping, inspect the final mask, and "
            "verify the cached output path and cook errors."
        )
        self._write_pack(
            self.project_root,
            version="2026.7.2",
            cards=[
                (
                    "velocity-ripples",
                    "Velocity-advected ripple field",
                    long_body,
                )
            ],
        )
        query = (
            "How do I create a velocity advected ripple field with HeightField "
            "Project boundary damping and verify cached output cook errors"
        )
        compact = self._dispatch(
            {
                "query": query,
                "sources": ["project"],
                "mode": "lexical",
                "refresh": True,
                "limit": 5,
            }
        )["result"]
        self.assertGreaterEqual(compact["total"], 1)
        self.assertEqual(
            "builtin_official_workflow",
            compact["matches"][0]["source_kind"],
        )
        self.assertEqual("velocity-ripples", compact["matches"][0]["card_id"])
        self.assertLessEqual(len(compact["matches"][0]["summary"]), 600)
        inventory = compact["index"]["corpus"]["inventory"]
        self.assertEqual("2026.7.2", inventory["builtin_pack"]["pack_version"])
        self.assertEqual(1, inventory["builtin_pack"]["documents"])

        full = self._dispatch(
            {
                "query": query,
                "sources": ["project"],
                "mode": "lexical",
                "refresh": False,
                "response_format": "full",
                "limit": 1,
            }
        )["result"]["matches"][0]
        self.assertIn("Velocity-advected ripple field workflow", full["content"])
        self.assertIn("verify the cached output path", full["content"])
        self.assertFalse(full["content_truncated"])

    def test_builtin_source_and_card_identity_filters_are_exact(self) -> None:
        self._write(
            self.project_root
            / ".agents"
            / "skills"
            / "current-skill"
            / "SKILL.md",
            "Exact Ripple Workflow " * 80,
        )
        self._write_pack(
            self.project_root,
            version="2026.7.3",
            cards=[
                (
                    "exact-ripple",
                    "Exact Ripple Workflow",
                    "# Exact Ripple Workflow\n\nOfficial workflow exact selection.",
                ),
                (
                    "other-ripple",
                    "Other Ripple Workflow",
                    "# Other Ripple Workflow\n\nOfficial workflow alternate.",
                ),
            ],
        )

        exact_title = self._dispatch(
            {
                "query": "Exact Ripple Workflow",
                "sources": ["project"],
                "mode": "lexical",
                "response_format": "full",
                "refresh": True,
            }
        )["result"]
        self.assertEqual(
            "exact-ripple",
            exact_title["matches"][0]["metadata"]["card_id"],
        )
        self.assertEqual(
            "builtin_official_workflow",
            exact_title["matches"][0]["source"],
        )

        database = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "knowledge.sqlite3"
        )
        before = database.read_bytes()
        with mock.patch.object(
            LocalKnowledgeIndex,
            "refresh",
            side_effect=AssertionError("exact read-only lookup refreshed"),
        ):
            card = self._dispatch(
                {
                    "query": "workflow",
                    "source_kinds": ["builtin_official_workflow"],
                    "card_id": "exact-ripple",
                    "mode": "lexical",
                }
            )["result"]
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(1, card["total"])
        self.assertEqual("exact-ripple", card["matches"][0]["card_id"])
        self.assertEqual(
            "exact-ripple",
            card["matches"][0]["canonical_id"],
        )

        card_only = self._dispatch(
            {
                "source_kinds": ["builtin_official_workflow"],
                "card_id": "exact-ripple",
                "mode": "lexical",
            }
        )["result"]
        self.assertEqual(1, card_only["total"])
        self.assertEqual("exact-ripple", card_only["matches"][0]["card_id"])

        canonical = self._dispatch(
            {
                "source_kinds": ["builtin_official_workflow"],
                "canonical_id": "other-ripple",
                "mode": "lexical",
            }
        )["result"]
        self.assertEqual(1, canonical["total"])
        self.assertEqual("other-ripple", canonical["matches"][0]["card_id"])
        self.assertTrue(
            all(
                match["source_kind"] == "builtin_official_workflow"
                for match in canonical["matches"]
            )
        )

        unknown = self._dispatch(
            {
                "card_id": "unknown-workflow",
                "mode": "lexical",
            }
        )["result"]
        self.assertEqual(0, unknown["total"])
        self.assertEqual([], unknown["matches"])

        with self.assertRaises(HiaRuntimeError) as missing:
            self._dispatch({"mode": "lexical"})
        self.assertEqual("INVALID_ARGUMENTS", missing.exception.code)

    def test_exact_source_filters_resolve_pyro_backing_groups(self) -> None:
        queries = [
            (
                "Houdini 21 high quality SOP Pyro fire flame source mapping "
                "sparse solver disturbance shredding turbulence velocity limits"
            ),
            (
                "Pyro Bake Volume fire flame temperature blackbody smoke "
                "viewport Karma volume shading"
            ),
            (
                "SOP Pyro simulation reset cache configured start "
                "representative frame validation"
            ),
        ]
        self._write_pack(
            self.project_root,
            version="2026.7.pyro",
            cards=[
                (
                    "pyro-intro",
                    "High quality SOP Pyro",
                    "# High quality SOP Pyro\n\n" + queries[0],
                ),
                (
                    "pyro-cache-shade-render",
                    "Pyro cache shade and render",
                    "# Pyro cache shade and render\n\n" + queries[1],
                ),
                (
                    "pyro-simulation-validation",
                    "Pyro simulation validation",
                    "# Pyro simulation validation\n\n" + queries[2],
                ),
            ],
        )
        shutil.copytree(
            REPOSITORY_ROOT / "knowledge" / "community-tutorials",
            self.project_root / "knowledge" / "community-tutorials",
        )
        self._dispatch(
            {
                "query": "Pyro",
                "sources": ["project"],
                "mode": "lexical",
                "refresh": True,
            }
        )
        index = self.executor._knowledge_index  # noqa: SLF001
        self.assertIsNotNone(index)
        self.executor.close()

        vector = [1.0, *([0.0] * 31)]

        def encode(
            *,
            documents: list[str],
            queries: list[str],
        ) -> dict[str, Any]:
            return {
                "document_vectors": [vector for _value in documents],
                "query_vectors": [vector for _value in queries],
                "model_id": "fake-pyro-embedder",
                "model_revision": "test",
                "profile_id": "fake-pyro",
                "requested_profile": "fake-pyro",
                "dim": 32,
                "normalized": True,
                "status": "ready",
                "fallback_reason": "",
                "repair": {},
            }

        embedder = mock.Mock()
        embedder.encode.side_effect = encode
        embedder.status.return_value = encode(documents=[], queries=[])
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )
        self.addCleanup(store.close)
        for _attempt in range(100):
            vector_status = store.build_batch(64)
            if vector_status["complete"]:
                break
        else:
            self.fail("fake vector index did not complete")

        with mock.patch.object(
            self.executor,
            "_hybrid_knowledge_store",
            return_value=store,
        ):
            official = self._dispatch(
                {
                    "queries": queries,
                    "sources": ["houdini"],
                    "source_kinds": ["builtin_official_workflow"],
                    "mode": "hybrid",
                    "refresh": False,
                    "response_format": "full",
                    "limit": 9,
                    "max_bytes": 20000,
                }
            )["result"]
            community = self._dispatch(
                {
                    "query": "Pyro field sourcing bounds cache contract",
                    "sources": ["houdini"],
                    "source_kinds": ["community_tutorial"],
                    "card_id": "pyro-fields-cache-contract",
                    "mode": "hybrid",
                    "refresh": False,
                    "limit": 3,
                }
            )["result"]

        self.assertEqual(3, len(official["queries"]))
        for result in official["queries"]:
            self.assertGreater(result["total"], 0)
            self.assertTrue(result["matches"])
            self.assertEqual("global", result["retrieval"]["ranking_scope"])
            self.assertTrue(
                all(
                    match["source_kind"] == "builtin_official_workflow"
                    for match in result["matches"]
                )
            )
        self.assertEqual(1, community["total"])
        self.assertEqual(
            ["community_tutorial"],
            [match["source_kind"] for match in community["matches"]],
        )
        self.assertEqual(
            "pyro-fields-cache-contract",
            community["matches"][0]["card_id"],
        )

    def test_houdini_versions_match_by_major_release(self) -> None:
        sources_root = (
            self.project_root / ".runtime" / "knowledge" / "sources"
        )
        versions = {
            "major.md": "21",
            "build.md": "21.0.440",
            "older.md": "20.5.999",
        }
        for name, version in versions.items():
            path = sources_root / name
            self._write(path, "MajorVersionNeedle shared workflow")
            self._write(
                Path(str(path) + ".metadata.json"),
                json.dumps({"houdini_version": version}),
            )
        index = LocalKnowledgeIndex(self.project_root)
        index.refresh(
            {"user"},
            {"houdini_version": "21.0.440"},
            force=True,
        )

        build_current = index.search(
            "MajorVersionNeedle",
            {"user"},
            current_houdini_version="21.0.440",
            offset=0,
            limit=10,
        )
        by_title = {
            match["title"]: match["metadata"]
            for match in build_current["matches"]
        }
        self.assertEqual("build.md", build_current["matches"][0]["title"])
        self.assertTrue(by_title["major.md"]["current_version_match"])
        self.assertTrue(by_title["build.md"]["current_version_match"])
        self.assertFalse(by_title["older.md"]["current_version_match"])

        major_current = index.search(
            "MajorVersionNeedle",
            {"user"},
            current_houdini_version="21",
            offset=0,
            limit=10,
        )
        self.assertEqual("major.md", major_current["matches"][0]["title"])
        by_title = {
            match["title"]: match["metadata"]
            for match in major_current["matches"]
        }
        self.assertTrue(by_title["build.md"]["current_version_match"])

    def test_release_knowledge_manifest_matches_archive_policy(self) -> None:
        manifest_path = (
            REPOSITORY_ROOT
            / "knowledge"
            / "sidefx-official"
            / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertFalse(manifest["upstream_content_redistributed"])
        self.assertEqual(
            knowledge_index.HOUDINI_HELP_ARCHIVES,
            frozenset(manifest["installed_help_archives"]),
        )
        identifiers: set[str] = set()
        for entry in manifest["sources"]:
            self.assertNotIn(entry["id"], identifiers)
            identifiers.add(entry["id"])
            self.assertTrue(entry["url"].startswith("https://www.sidefx.com/"))
            self.assertTrue(
                (manifest_path.parent / entry["path"]).is_file()
            )

    def test_pagination_and_structured_contract_are_preserved(self) -> None:
        response = self._dispatch(
            {
                "query": "Needle",
                "sources": ["project"],
                "offset": 1,
                "limit": 1,
                "refresh": True,
            }
        )

        self.assertTrue(
            {
                "ok",
                "result",
                "stdout",
                "warnings",
                "errors",
                "revision",
                "dirty",
                "phase_timings",
            }.issubset(response)
        )
        self.assertTrue(response["ok"])
        self.assertEqual("", response["stdout"])
        self.assertEqual([], response["warnings"])
        self.assertEqual([], response["errors"])
        self.assertEqual(0, response["revision"])
        self.assertTrue(response["dirty"])
        self.assertTrue(
            {
                "query",
                "matches",
                "total",
                "offset",
                "limit",
                "files_scanned",
                "web_searched",
            }.issubset(response["result"])
        )
        self.assertEqual("Needle", response["result"]["query"])
        self.assertEqual(3, response["result"]["total"])
        self.assertEqual(1, response["result"]["offset"])
        self.assertEqual(1, response["result"]["limit"])
        self.assertEqual(1, len(response["result"]["matches"]))
        self.assertFalse(response["result"]["web_searched"])

        empty_sources = self._dispatch(
            {"query": "Needle", "sources": [], "limit": 50}
        )
        self.assertEqual(3, empty_sources["result"]["total"])
        self.assertEqual(
            "read_only",
            empty_sources["result"]["index"]["refresh_reason"],
        )

    def test_batch_queries_refresh_once_without_duplicate_match_payloads(self) -> None:
        response = self._dispatch(
            {
                "queries": ["Needle", "current"],
                "sources": ["project"],
                "limit": 50,
                "refresh": True,
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(1, self.runner_calls)
        result = response["result"]
        self.assertEqual(2, result["query_count"])
        self.assertEqual(
            ["Needle", "current"],
            [item["query"] for item in result["queries"]],
        )
        read_paths = [path.resolve() for path, _in_runner in self.file_read_states]
        self.assertEqual(len(read_paths), len(set(read_paths)))
        self.assertTrue(
            all(not in_runner for _path, in_runner in self.file_read_states)
        )
        self.assertNotIn("matches", result)
        self.assertIn("retrieval", result)
        self.assertIn("index", result)
        self.assertTrue(
            all("retrieval" in item for item in result["queries"])
        )
        self.assertTrue(
            all(
                set(match)
                == {
                    "title",
                    "summary",
                    "source",
                    "source_kind",
                    "card_id",
                    "canonical_id",
                    "pack_version",
                    "url",
                    "houdini_version",
                    "verification",
                    "scores",
                }
                for item in result["queries"]
                for match in item["matches"]
            )
        )

    def test_user_sources_metadata_incremental_refresh_and_version_priority(
        self,
    ) -> None:
        sources_root = (
            self.project_root / ".runtime" / "knowledge" / "sources"
        )
        current = sources_root / "current.md"
        older = sources_root / "older.html"
        captions = sources_root / "captions.vtt"
        self._write(current, "Banana solver current workflow")
        self._write(
            Path(str(current) + ".metadata.json"),
            """{
                "url": "https://example.invalid/current",
                "author": "Example Author",
                "accessed_at": "2026-07-23T00:00:00+00:00",
                "houdini_version": "21.0.000",
                "license": "Example-License",
                "verification": "verified",
                "evidence": "User claim only"
            }""",
        )
        self._write(
            older,
            "<html><body><p>Banana solver older workflow</p></body></html>",
        )
        self._write(
            Path(str(older) + ".metadata.json"),
            '{"houdini_version":"20.0.000"}',
        )
        self._write(
            captions,
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nBanana caption workflow",
        )

        response = self._dispatch(
            {
                "query": "Banana",
                "sources": ["user"],
                "limit": 10,
                "refresh": True,
                "response_format": "full",
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(3, response["result"]["total"])
        matches = response["result"]["matches"]
        self.assertEqual("current.md", matches[0]["title"])
        current_metadata = matches[0]["metadata"]
        self.assertEqual("Example Author", current_metadata["author"])
        self.assertEqual("Example-License", current_metadata["license"])
        self.assertEqual("21.0.000", current_metadata["houdini_version"])
        self.assertTrue(current_metadata["current_version_match"])
        self.assertEqual(
            "user_supplied_unverified",
            current_metadata["verification"],
        )
        original_sha = current_metadata["sha256"]

        self._write(current, "Banana solver revised deterministic workflow")
        revised = self._dispatch(
            {
                "query": "revised",
                "sources": ["user"],
                "refresh": True,
                "response_format": "full",
            }
        )
        self.assertEqual(1, revised["result"]["total"])
        self.assertNotEqual(
            original_sha,
            revised["result"]["matches"][0]["metadata"]["sha256"],
        )
        self.assertGreaterEqual(
            revised["result"]["index"]["documents_updated"],
            1,
        )
        self.assertGreaterEqual(
            revised["result"]["index"]["documents_body_changed"],
            1,
        )

    def test_metadata_only_refresh_has_a_separate_counter(self) -> None:
        source = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / "metadata-only.md"
        )
        sidecar = Path(str(source) + ".metadata.json")
        self._write(source, "MetadataOnlyNeedle stable body")
        self._write(sidecar, '{"author":"First Author"}')
        self._dispatch(
            {
                "query": "MetadataOnlyNeedle",
                "sources": ["user"],
                "mode": "lexical",
                "refresh": True,
            }
        )

        self._write(
            sidecar,
            '{"author":"Second Author","url":"https://example.invalid/revised"}',
        )
        updated = self._dispatch(
            {
                "query": "MetadataOnlyNeedle",
                "sources": ["user"],
                "mode": "lexical",
                "refresh": True,
            }
        )

        index = updated["result"]["index"]
        self.assertEqual(1, index["documents_updated"])
        self.assertEqual(0, index["documents_body_changed"])
        self.assertEqual(1, index["documents_metadata_changed"])

    def test_pdf_support_degrades_explicitly_without_hard_dependency(self) -> None:
        pdf_path = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / "optional.pdf"
        )
        self._write(pdf_path, "not a real PDF")

        response = self._dispatch(
            {
                "query": "optional",
                "sources": ["user"],
                "refresh": True,
            }
        )

        self.assertTrue(response["ok"])
        self.assertTrue(response["warnings"])
        self.assertTrue(
            any("PDF" in warning for warning in response["warnings"])
        )

    def test_two_workers_share_wal_reads_and_serialize_writes(self) -> None:
        source = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / "shared.md"
        )
        self._write(source, "Concurrent Needle knowledge")
        first = LocalKnowledgeIndex(self.project_root)
        second = LocalKnowledgeIndex(self.project_root)
        first.refresh({"user"}, {"houdini_version": "21.0.000"}, force=True)

        def search(index: LocalKnowledgeIndex) -> int:
            result = index.search(
                "Needle",
                {"user"},
                current_houdini_version="21.0.000",
                offset=0,
                limit=10,
            )
            return int(result["total"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            totals = list(pool.map(search, (first, second)))
        self.assertEqual([1, 1], totals)

        def refresh(index: LocalKnowledgeIndex) -> bool:
            stats, _warnings = index.refresh(
                {"user"},
                {"houdini_version": "21.0.000"},
                force=True,
            )
            return bool(stats["refreshed"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            refreshed = list(pool.map(refresh, (first, second)))
        self.assertEqual([True, True], refreshed)
        self.assertEqual(1, search(first))

        with closing(sqlite3.connect(str(first.database_path))) as connection:
            journal_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
        self.assertEqual("wal", str(journal_mode).casefold())

    def test_automatic_refresh_waits_ten_minutes_but_force_is_immediate(
        self,
    ) -> None:
        source = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / "refresh-window.md"
        )
        self._write(source, "Ten minute refresh boundary")
        index = LocalKnowledgeIndex(self.project_root)

        with mock.patch.object(knowledge_index.time, "time", return_value=1000.0):
            index.refresh(
                {"user"},
                {"houdini_version": "21.0.000"},
                force=True,
            )
        with mock.patch.object(knowledge_index.time, "time", return_value=1599.0):
            self.assertEqual(set(), index.refresh_due({"user"}))
            self.assertEqual(
                {"user"},
                index.refresh_due({"user"}, force=True),
            )
        with mock.patch.object(knowledge_index.time, "time", return_value=1600.0):
            self.assertEqual({"user"}, index.refresh_due({"user"}))

    def test_every_index_connection_is_closed_after_each_operation(self) -> None:
        original_connect = sqlite3.connect
        opened: list[sqlite3.Connection] = []

        def tracked_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with mock.patch.object(
            knowledge_index.sqlite3,
            "connect",
            side_effect=tracked_connect,
        ):
            index = LocalKnowledgeIndex(self.project_root)
            index.refresh(
                {"project"},
                {"houdini_version": "21.0.000"},
                force=True,
            )
            index.refresh_due({"project"})
            index.search(
                "Needle",
                {"project"},
                current_houdini_version="21.0.000",
                offset=0,
                limit=10,
            )
            index.active_houdini_version()

        self.assertGreaterEqual(len(opened), 5)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


class BuiltinPackActivationAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name) / "project"
        self.project_root.mkdir()

    def _write_pack(self, version: str, token: str) -> None:
        pack_root = self.project_root / "knowledge" / "sidefx-official"
        card_path = pack_root / "cards" / "workflow.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(
            f"# Atomic workflow\n\n{token} remains searchable.",
            encoding="utf-8",
        )
        common = {
            "schema_version": 1,
            "pack_id": "atomic-pack",
            "pack_version": version,
        }
        (pack_root / "coverage.json").write_text(
            json.dumps({**common, "cards": 1}),
            encoding="utf-8",
        )
        (pack_root / "sources.json").write_text(
            json.dumps(
                {
                    **common,
                    "sources": [
                        {
                            "id": "workflow-source",
                            "title": "Atomic workflow",
                            "url": "https://www.sidefx.com/docs/houdini/atomic/",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (pack_root / "manifest.json").write_text(
            json.dumps(
                {
                    **common,
                    "schema_version": 2,
                    "publisher": "Big-Chicken contributors",
                    "card_license": "Apache-2.0",
                    "sources": [
                        {
                            "id": "workflow",
                            "canonical_id": "workflow",
                            "title": "Atomic workflow",
                            "path": "cards/workflow.md",
                            "url": (
                                "https://www.sidefx.com/docs/houdini/atomic/"
                            ),
                            "source_ids": ["workflow-source"],
                            "houdini_version": "21",
                            "topic": "workflow",
                            "aliases": ["atomic"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _search_total(index: LocalKnowledgeIndex, token: str) -> int:
        return int(
            index.search(
                token,
                {"project"},
                current_houdini_version="21.0",
                offset=0,
                limit=5,
            )["total"]
        )

    def test_new_pack_digest_bypasses_freshness_and_activates_with_db(self) -> None:
        self._write_pack("1.0.0", "OldAtomicToken")
        index = LocalKnowledgeIndex(self.project_root)
        index.refresh({"project"}, {"houdini_version": "21.0"}, force=True)

        active_path = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "builtin"
            / "active.json"
        )
        old_active = active_path.read_bytes()
        self._write_pack("2.0.0", "NewAtomicToken")
        original_connect = index._connect  # noqa: SLF001
        active_seen_during_commit: list[bytes] = []

        class _CommitObserver:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def __getattr__(self, name: str) -> Any:
                return getattr(self._connection, name)

            def commit(self) -> None:
                active_seen_during_commit.append(active_path.read_bytes())
                self._connection.commit()

        def observed_connect(*, read_only: bool = False) -> Any:
            connection = original_connect(read_only=read_only)
            return connection if read_only else _CommitObserver(connection)

        with mock.patch.object(index, "_connect", side_effect=observed_connect):
            stats, warnings = index.refresh(
                {"project"},
                {"houdini_version": "21.0"},
                force=False,
            )

        self.assertEqual([], warnings)
        self.assertEqual([old_active], active_seen_during_commit)
        self.assertEqual(["project"], stats["refresh_groups"])
        self.assertEqual("2.0.0", stats["builtin_pack"]["pack_version"])
        self.assertEqual(0, self._search_total(index, "OldAtomicToken"))
        self.assertEqual(1, self._search_total(index, "NewAtomicToken"))
        self.assertEqual(
            "2.0.0",
            index.corpus_status()["builtin_pack"]["pack_version"],
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        self.assertEqual("2.0.0", active["pack_version"])

    def test_commit_failure_preserves_old_active_and_database(self) -> None:
        self._write_pack("1.0.0", "OldAtomicToken")
        index = LocalKnowledgeIndex(self.project_root)
        index.refresh({"project"}, {"houdini_version": "21.0"}, force=True)
        active_path = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "builtin"
            / "active.json"
        )
        old_active = active_path.read_bytes()
        self._write_pack("2.0.0", "NewAtomicToken")
        original_connect = index._connect  # noqa: SLF001
        active_seen_during_commit: list[bytes] = []

        class _CommitFailure:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def __getattr__(self, name: str) -> Any:
                return getattr(self._connection, name)

            def commit(self) -> None:
                active_seen_during_commit.append(active_path.read_bytes())
                raise sqlite3.OperationalError("injected commit failure")

        def failing_connect(*, read_only: bool = False) -> Any:
            connection = original_connect(read_only=read_only)
            return connection if read_only else _CommitFailure(connection)

        with mock.patch.object(index, "_connect", side_effect=failing_connect):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected commit failure",
            ):
                index.refresh(
                    {"project"},
                    {"houdini_version": "21.0"},
                    force=False,
                )

        self.assertEqual([old_active], active_seen_during_commit)
        self.assertEqual(old_active, active_path.read_bytes())
        self.assertEqual(1, self._search_total(index, "OldAtomicToken"))
        self.assertEqual(0, self._search_total(index, "NewAtomicToken"))
        status = index.corpus_status()["builtin_pack"]
        self.assertEqual("1.0.0", status["pack_version"])
        self.assertTrue(status["complete"])


if __name__ == "__main__":
    unittest.main()
