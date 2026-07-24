from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
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
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=runtime_tmp)
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

    def test_search_runs_file_io_off_ui_and_uses_only_allowed_sources(self) -> None:
        response = self._dispatch({"query": "Needle", "limit": 50})

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

        self.assertEqual(5, response["result"]["total"])
        self.assertEqual(
            {
                ("houdini_node_catalog", "Sop::needle"),
                ("project_skill", "current-skill/SKILL.md"),
                ("project_skill", "current-skill/references/review.md"),
                ("project_docs", "HIA-MCP-V2.md"),
                ("houdini_help", "nodes/needle.txt"),
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

    def test_pagination_and_structured_contract_are_preserved(self) -> None:
        response = self._dispatch(
            {
                "query": "Needle",
                "sources": ["project"],
                "offset": 1,
                "limit": 1,
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
        self.assertEqual(5, empty_sources["result"]["total"])

    def test_batch_queries_refresh_index_once_and_merge_matches(self) -> None:
        response = self._dispatch(
            {
                "queries": ["Needle", "current"],
                "sources": ["project"],
                "limit": 50,
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
        self.assertTrue(
            any(
                len(match["matched_queries"]) == 2
                for match in result["matches"]
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
        self.assertEqual("unverified", current_metadata["verification"])
        original_sha = current_metadata["sha256"]

        self._write(current, "Banana solver revised deterministic workflow")
        revised = self._dispatch(
            {
                "query": "revised",
                "sources": ["user"],
                "refresh": True,
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

        with sqlite3.connect(str(first.database_path)) as connection:
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


if __name__ == "__main__":
    unittest.main()
