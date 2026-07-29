from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT))

from hia_core.embedding_contract import (  # noqa: E402
    KNOWLEDGE_INDEX_CLI_PROTOCOL,
    runtime_layout,
)
from hia_mcp_runtime import knowledge_index_cli as cli  # noqa: E402
from hia_mcp_runtime.hybrid_knowledge import (  # noqa: E402
    HybridKnowledgeStore,
)
from hia_mcp_runtime.knowledge_index import LocalKnowledgeIndex  # noqa: E402
from tests.unit.test_hia_mcp_v2_hybrid_knowledge import (  # noqa: E402
    FakeEmbedder,
    _build_all_vectors,
)


TEST_RUN_ROOT = REPOSITORY_ROOT / ".runtime" / "test-runs"
TEST_RUN_ROOT.mkdir(parents=True, exist_ok=True)


def _status(
    *,
    total: int,
    vector: int,
    last_batch: int = 0,
) -> dict[str, Any]:
    pending = total - vector
    return {
        "available": True,
        "status": "ready",
        "requested_profile": "qwen3-embedding-0.6b",
        "active_profile": "qwen3-embedding-0.6b",
        "profile_id": "qwen3-embedding-0.6b",
        "model_id": "Qwen/Qwen3-Embedding-0.6B",
        "model_revision": "test",
        "dim": 1024,
        "normalized": True,
        "degraded": False,
        "fallback_reason": "",
        "repair": {},
        "complete": pending == 0,
        "partial": pending > 0,
        "vector_chunks": vector,
        "total_chunks": total,
        "pending_chunks": pending,
        "chunks_indexed_this_call": last_batch,
        "last_batch_count": last_batch,
    }


class FakeStore:
    total = 0
    vector = 0
    build_calls = 0
    interrupt_on_call: int | None = None
    close_calls = 0
    refresh_calls = 0
    memory_calls: list[dict[str, Any]] = []

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.index = self
        self.database_path = (
            self.project_root / ".runtime" / "knowledge" / "knowledge.sqlite3"
        )
        self._embedder = None

    @classmethod
    def reset(cls, *, total: int, vector: int = 0) -> None:
        cls.total = total
        cls.vector = vector
        cls.build_calls = 0
        cls.interrupt_on_call = None
        cls.close_calls = 0
        cls.refresh_calls = 0
        cls.memory_calls = []

    def active_houdini_version(self) -> str:
        return "21.0.000"

    def refresh(
        self,
        source_groups: set[str],
        snapshot: dict[str, str],
        *,
        force: bool,
    ) -> tuple[dict[str, Any], list[str]]:
        type(self).refresh_calls += 1
        if source_groups not in ({"project"}, {"user"}) or not force:
            raise AssertionError("unexpected source refresh")
        if snapshot != {"houdini_version": "21.0.000"}:
            raise AssertionError("unexpected source snapshot")
        return {
            "refreshed": True,
            "groups": sorted(source_groups),
        }, []

    def status(self) -> dict[str, Any]:
        return _status(total=self.total, vector=self.vector)

    def build_batch(self, max_chunks: int) -> dict[str, Any]:
        type(self).build_calls += 1
        if type(self).build_calls == type(self).interrupt_on_call:
            raise KeyboardInterrupt
        indexed = min(max_chunks, self.total - self.vector)
        type(self).vector += indexed
        return _status(
            total=self.total,
            vector=self.vector,
            last_batch=indexed,
        )

    def vectorize_documents(
        self,
        document_ids: tuple[int, ...],
    ) -> dict[str, Any]:
        del document_ids
        return _status(total=self.total, vector=self.vector)

    def project_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        type(self).memory_calls.append(dict(arguments))
        return {"received": dict(arguments)}

    def close(self) -> None:
        type(self).close_calls += 1


class RealIndexStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.index = LocalKnowledgeIndex(self.project_root)
        self._hybrid = HybridKnowledgeStore(
            self.project_root,
            index=self.index,
            embedder=FakeEmbedder(available=False),
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


class RealHybridStore(RealIndexStore):
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.index = LocalKnowledgeIndex(self.project_root)
        self._hybrid = HybridKnowledgeStore(
            self.project_root,
            index=self.index,
            embedder=FakeEmbedder(),
        )


def _run(
    *arguments: str,
    store_type: type[Any] = FakeStore,
    project_root: Path = REPOSITORY_ROOT,
    open_modes: list[bool] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    output = io.StringIO()

    def open_store(root: Path, read_only: bool = False) -> Any:
        if open_modes is not None:
            open_modes.append(read_only)
        return store_type(root)

    with mock.patch.object(
        cli,
        "_open_store",
        side_effect=open_store,
    ):
        exit_code = cli.main(
            (
                "--project-root",
                str(project_root),
                *arguments,
            ),
            stdout=output,
        )
    events = [json.loads(line) for line in output.getvalue().splitlines() if line]
    return exit_code, events


def _run_real(
    project_root: Path,
    *arguments: str,
) -> tuple[int, list[dict[str, Any]]]:
    output = io.StringIO()
    exit_code = cli.main(
        (
            "--project-root",
            str(project_root),
            *arguments,
        ),
        stdout=output,
    )
    events = [
        json.loads(line)
        for line in output.getvalue().splitlines()
        if line
    ]
    return exit_code, events


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


class KnowledgeIndexCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_RUN_ROOT)
        self.project_root = Path(self._temporary.name)
        _minimal_project(self.project_root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_module_and_direct_project_relative_script_are_runnable(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(RUNTIME_PACKAGE_ROOT), str(SOURCE_ROOT))
        )

        module = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "hia_mcp_runtime.knowledge_index_cli",
                "--help",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        direct_environment = dict(os.environ)
        direct_environment.pop("PYTHONPATH", None)
        direct = subprocess.run(
            [
                sys.executable,
                "-B",
                str(
                    RUNTIME_PACKAGE_ROOT
                    / "hia_mcp_runtime"
                    / "knowledge_index_cli.py"
                ),
                "--help",
            ],
            cwd=REPOSITORY_ROOT,
            env=direct_environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(
            0,
            module.returncode,
            module.stdout + module.stderr,
        )
        self.assertEqual(
            0,
            direct.returncode,
            direct.stdout + direct.stderr,
        )
        self.assertIn(
            "{bootstrap,status,build,sources,thread,memory}",
            module.stdout,
        )
        self.assertIn(
            "{bootstrap,status,build,sources,thread,memory}",
            direct.stdout,
        )

    def test_status_emits_stable_start_and_completed_jsonl(self) -> None:
        FakeStore.reset(total=8, vector=3)

        exit_code, events = _run("status")

        self.assertEqual(0, exit_code)
        self.assertEqual(["start", "completed"], [event["event"] for event in events])
        self.assertEqual([1, 2], [event["sequence"] for event in events])
        self.assertTrue(
            all(event["protocol"] == KNOWLEDGE_INDEX_CLI_PROTOCOL for event in events)
        )
        self.assertEqual(5, events[-1]["index"]["pending_chunks"])
        self.assertIn("embedding", events[-1])
        self.assertIn("runtime", events[-1])
        self.assertIn("installation", events[-1])
        self.assertIn("python", events[-1]["runtime"])
        self.assertIn("venv", events[-1]["runtime"])
        self.assertIn("model", events[-1]["runtime"])
        self.assertIn("index", events[-1]["runtime"])
        self.assertIsNone(events[-1]["embedding"]["cuda_available"])
        self.assertEqual(
            "not_reported",
            events[-1]["embedding"]["cuda_status"],
        )
        self.assertEqual(0, FakeStore.refresh_calls)
        self.assertEqual(1, FakeStore.close_calls)

    def test_partial_status_and_source_list_are_strictly_read_only(self) -> None:
        FakeStore.reset(total=31812, vector=30987)
        knowledge_root = self.project_root / ".runtime" / "knowledge"
        sources_root = knowledge_root / "sources"
        sources_root.mkdir(parents=True)
        database = knowledge_root / "knowledge.sqlite3"
        database.write_bytes(b"read-only-index-sentinel")
        before_bytes = database.read_bytes()
        before_mtime = database.stat().st_mtime_ns
        before_files = sorted(
            path.relative_to(self.project_root).as_posix()
            for path in self.project_root.rglob("*")
            if path.is_file()
        )
        status_open_modes: list[bool] = []

        status_exit, status_events = _run(
            "status",
            project_root=self.project_root,
            open_modes=status_open_modes,
        )

        self.assertEqual(0, status_exit)
        self.assertEqual([True], status_open_modes)
        index = status_events[-1]["index"]
        self.assertEqual(31812, index["total_chunks"])
        self.assertEqual(30987, index["vector_chunks"])
        self.assertEqual(825, index["pending_chunks"])
        self.assertTrue(index["partial"])
        self.assertFalse(index["complete"])
        self.assertEqual(0, FakeStore.build_calls)
        self.assertEqual(0, FakeStore.refresh_calls)

        source_open_modes: list[bool] = []
        source_exit, source_events = _run(
            "sources",
            "list",
            project_root=self.project_root,
            open_modes=source_open_modes,
        )

        self.assertEqual(0, source_exit)
        self.assertEqual([True], source_open_modes)
        self.assertEqual([], source_events[-1]["result"]["items"])
        self.assertEqual(0, source_events[-1]["result"]["total"])
        self.assertEqual(0, FakeStore.build_calls)
        self.assertEqual(0, FakeStore.refresh_calls)
        self.assertEqual(before_bytes, database.read_bytes())
        self.assertEqual(before_mtime, database.stat().st_mtime_ns)
        self.assertEqual(
            before_files,
            sorted(
                path.relative_to(self.project_root).as_posix()
                for path in self.project_root.rglob("*")
                if path.is_file()
            ),
        )

    def test_status_does_not_initialize_an_absent_database(self) -> None:
        runtime_root = self.project_root / ".runtime"

        exit_code, events = _run_real(self.project_root, "status")

        self.assertEqual(0, exit_code)
        self.assertFalse(runtime_root.exists())
        self.assertEqual("not_initialized", events[-1]["index"]["status"])
        self.assertEqual(
            "KNOWLEDGE_INDEX_NOT_INITIALIZED",
            events[-1]["index"]["fallback_reason"],
        )

    def test_bootstrap_installs_builtin_pack_idempotently_with_jsonl_status(
        self,
    ) -> None:
        pack_root = self.project_root / "knowledge" / "sidefx-official"
        (pack_root / "cards").mkdir(parents=True)
        (pack_root / "cards" / "workflow.md").write_text(
            "OfflineBootstrapToken complete workflow body.",
            encoding="utf-8",
        )
        (pack_root / "coverage.json").write_text("{}", encoding="utf-8")
        (pack_root / "sources.json").write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "source-workflow",
                            "url": "https://www.sidefx.com/docs/houdini/",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (pack_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "pack_id": "cli-test-pack",
                    "pack_version": "2026.7",
                    "sources": [
                        {
                            "id": "workflow",
                            "canonical_id": "workflow",
                            "title": "CLI workflow",
                            "path": "cards/workflow.md",
                            "url": "https://www.sidefx.com/docs/houdini/",
                            "source_ids": ["source-workflow"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        class BootstrapStore:
            def __init__(self, root: Path) -> None:
                self.index = LocalKnowledgeIndex(root)
                self._embedder = None

            def status(self) -> dict[str, Any]:
                with closing(self.index._connect(read_only=True)) as connection:  # noqa: SLF001
                    total = int(
                        connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                    )
                result = _status(total=total, vector=0)
                result["corpus"] = self.index.corpus_status()
                return result

            def close(self) -> None:
                return None

        def run_bootstrap() -> tuple[int, list[dict[str, Any]]]:
            output = io.StringIO()
            with mock.patch.object(
                cli,
                "_open_store",
                side_effect=lambda root, read_only=False: BootstrapStore(root),
            ):
                code = cli.main(
                    (
                        "--project-root",
                        str(self.project_root),
                        "bootstrap",
                    ),
                    stdout=output,
                )
            return code, [
                json.loads(line)
                for line in output.getvalue().splitlines()
                if line
            ]

        first_code, first_events = run_bootstrap()
        active = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "builtin"
            / "active.json"
        )
        active_mtime = active.stat().st_mtime_ns
        second_code, second_events = run_bootstrap()

        self.assertEqual(0, first_code)
        self.assertEqual(0, second_code)
        self.assertEqual(
            ["start", "completed"],
            [event["event"] for event in first_events],
        )
        self.assertTrue(
            first_events[-1]["source_refresh"]["builtin_pack"]["changed"]
        )
        self.assertFalse(
            second_events[-1]["source_refresh"]["builtin_pack"]["changed"]
        )
        self.assertEqual(active_mtime, active.stat().st_mtime_ns)
        corpus = second_events[-1]["index"]["corpus"]
        self.assertEqual("2026.7", corpus["builtin_pack"]["pack_version"])
        self.assertEqual(1, corpus["builtin_pack"]["documents"])

    def test_status_keeps_an_existing_database_byte_identical(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        database = index.database_path
        before_bytes = database.read_bytes()
        before_mtime = database.stat().st_mtime_ns
        before_names = sorted(path.name for path in database.parent.iterdir())

        exit_code, events = _run_real(self.project_root, "status")

        self.assertEqual(0, exit_code)
        self.assertEqual(before_bytes, database.read_bytes())
        self.assertEqual(before_mtime, database.stat().st_mtime_ns)
        self.assertEqual(
            before_names,
            sorted(path.name for path in database.parent.iterdir()),
        )
        self.assertEqual(str(database), events[-1]["runtime"]["index"]["path"])

    def test_json_format_emits_one_terminal_document(self) -> None:
        FakeStore.reset(total=8, vector=8)

        exit_code, events = _run("--format", "json", "status")

        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(events))
        self.assertEqual("completed", events[0]["event"])
        self.assertEqual("status", events[0]["action"])

    def test_status_uses_environment_paths_without_hardware_probe(self) -> None:
        FakeStore.reset(total=0)
        python_path = Path(runtime_layout(self.project_root)["worker_python"])
        model_path = (
            self.project_root
            / ".runtime"
            / "models"
            / "qwen3-embedding"
            / "custom-model"
        )
        python_path.parent.mkdir(parents=True)
        python_path.write_bytes(b"python")
        model_path.mkdir(parents=True)
        environment = {
            "HIA_EMBEDDING_PYTHON": str(python_path),
            "HIA_EMBEDDING_PROFILE": "qwen3-embedding-0.6b",
            "HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B": str(model_path),
            "HIA_EMBEDDING_DEVICE": "cuda",
        }

        with mock.patch.dict(os.environ, environment, clear=False):
            exit_code, events = _run(
                "status",
                project_root=self.project_root,
            )

        self.assertEqual(0, exit_code)
        result = events[-1]
        self.assertEqual(str(python_path), result["runtime"]["python"]["path"])
        self.assertTrue(result["runtime"]["python"]["exists"])
        self.assertTrue(result["runtime"]["python"]["path_valid"])
        self.assertEqual(str(model_path), result["runtime"]["model"]["path"])
        self.assertTrue(result["runtime"]["model"]["exists"])
        self.assertTrue(result["runtime"]["model"]["path_valid"])
        self.assertEqual("cuda", result["embedding"]["device"])
        self.assertIsNone(result["embedding"]["cuda_available"])

    def test_status_rejects_relative_runtime_paths_without_cwd_guessing(
        self,
    ) -> None:
        FakeStore.reset(total=0)
        environment = {
            "HIA_EMBEDDING_PYTHON": "relative/Scripts/python.exe",
            "HIA_EMBEDDING_PROFILE": "qwen3-embedding-0.6b",
            "HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B": "relative/model",
        }

        with mock.patch.dict(os.environ, environment, clear=False):
            exit_code, events = _run(
                "status",
                project_root=self.project_root,
            )

        self.assertEqual(0, exit_code)
        result = events[-1]
        self.assertFalse(result["runtime"]["python"]["path_valid"])
        self.assertFalse(result["runtime"]["python"]["exists"])
        self.assertFalse(result["runtime"]["model"]["path_valid"])
        self.assertFalse(result["runtime"]["model"]["exists"])
        self.assertEqual(
            "EMBEDDING_RUNTIME_PATH_INVALID",
            result["embedding"]["fallback_reason"],
        )

    def test_status_rejects_legacy_toolchain_python_after_venv_migration(
        self,
    ) -> None:
        FakeStore.reset(total=0)
        layout = runtime_layout(self.project_root)
        legacy_python = (
            Path(layout["legacy_venv_root"]) / "Scripts" / "python.exe"
        )
        legacy_python.parent.mkdir(parents=True)
        legacy_python.write_bytes(b"legacy")
        environment = {
            "HIA_EMBEDDING_PYTHON": str(legacy_python),
            "HIA_EMBEDDING_PROFILE": "qwen3-embedding-0.6b",
        }

        with mock.patch.dict(os.environ, environment, clear=False):
            exit_code, events = _run(
                "status",
                project_root=self.project_root,
            )

        self.assertEqual(0, exit_code)
        result = events[-1]
        self.assertEqual(
            str(legacy_python),
            result["runtime"]["python"]["path"],
        )
        self.assertFalse(result["runtime"]["python"]["path_valid"])
        self.assertFalse(result["runtime"]["python"]["exists"])
        self.assertEqual(
            "EMBEDDING_RUNTIME_PATH_INVALID",
            result["embedding"]["fallback_reason"],
        )

    def test_build_emits_committed_progress_and_completion(self) -> None:
        FakeStore.reset(total=5, vector=1)

        exit_code, events = _run("build", "--batch-size", "2")

        self.assertEqual(0, exit_code)
        self.assertEqual(
            ["start", "progress", "progress", "completed"],
            [event["event"] for event in events],
        )
        self.assertEqual(
            [2, 2],
            [
                event["indexed_this_batch"]
                for event in events
                if event["event"] == "progress"
            ],
        )
        self.assertTrue(events[-1]["index"]["complete"])
        self.assertEqual(0, events[-1]["index"]["pending_chunks"])
        self.assertEqual(2, events[-1]["batches"])
        self.assertEqual(1, FakeStore.refresh_calls)
        self.assertTrue(events[0]["source_refresh"]["refreshed"])
        self.assertEqual(1, FakeStore.close_calls)

    def test_interrupted_build_is_resumable_from_committed_batch(self) -> None:
        FakeStore.reset(total=5)
        FakeStore.interrupt_on_call = 2

        first_exit, first_events = _run("build", "--batch-size", "2")

        self.assertEqual(130, first_exit)
        self.assertEqual(
            ["start", "progress", "error"],
            [event["event"] for event in first_events],
        )
        self.assertEqual("INTERRUPTED", first_events[-1]["code"])
        self.assertTrue(first_events[-1]["recoverable"])
        self.assertEqual(2, first_events[-1]["index"]["vector_chunks"])
        self.assertEqual(3, first_events[-1]["index"]["pending_chunks"])

        FakeStore.interrupt_on_call = None
        second_exit, second_events = _run("build", "--batch-size", "2")

        self.assertEqual(0, second_exit)
        self.assertEqual(2, second_events[0]["index"]["vector_chunks"])
        self.assertEqual(
            [2, 1],
            [
                event["indexed_this_batch"]
                for event in second_events
                if event["event"] == "progress"
            ],
        )
        self.assertTrue(second_events[-1]["index"]["complete"])
        self.assertEqual(2, FakeStore.close_calls)

    def test_invalid_batch_size_is_a_structured_error(self) -> None:
        FakeStore.reset(total=5)

        exit_code, events = _run("build", "--batch-size", "0")

        self.assertEqual(2, exit_code)
        self.assertEqual(1, len(events))
        self.assertEqual("error", events[0]["event"])
        self.assertEqual("INVALID_ARGUMENTS", events[0]["code"])
        self.assertEqual("build", events[0]["action"])
        self.assertFalse(events[0]["recoverable"])
        self.assertEqual(0, FakeStore.close_calls)

    def test_memory_commands_map_once_to_existing_store_contract(self) -> None:
        FakeStore.reset(total=0)
        calls = (
            (
                (
                    "memory",
                    "record",
                    "--memory-type",
                    "decision",
                    "--title",
                    "Render backend",
                    "--body",
                    "Use Karma XPU.",
                    "--tag",
                    "render",
                    "--scope",
                    "project",
                    "--source-thread-id",
                    "thread-1",
                    "--source-turn-id",
                    "turn-2",
                ),
                {
                    "action": "record",
                    "memory_type": "decision",
                    "title": "Render backend",
                    "body": "Use Karma XPU.",
                    "tags": ["render"],
                    "scope": "project",
                    "source_thread_id": "thread-1",
                    "source_turn_id": "turn-2",
                },
            ),
            (
                (
                    "memory",
                    "list",
                    "--memory-type",
                    "lesson",
                    "--tag",
                    "pyro",
                    "--include-superseded",
                    "--offset",
                    "2",
                    "--limit",
                    "4",
                ),
                {
                    "action": "list",
                    "memory_type": "lesson",
                    "tags": ["pyro"],
                    "scope": "project",
                    "include_superseded": True,
                    "offset": 2,
                    "limit": 4,
                },
            ),
            (
                ("memory", "delete", "--memory-id", "mem_" + "a" * 32),
                {"action": "delete", "memory_id": "mem_" + "a" * 32},
            ),
            (
                (
                    "memory",
                    "supersede",
                    "--memory-id",
                    "mem_" + "b" * 32,
                    "--memory-type",
                    "workflow",
                    "--title",
                    "Updated cache workflow",
                    "--body",
                    "Use the approved cache path.",
                ),
                {
                    "action": "supersede",
                    "memory_id": "mem_" + "b" * 32,
                    "memory_type": "workflow",
                    "title": "Updated cache workflow",
                    "body": "Use the approved cache path.",
                },
            ),
        )

        for arguments, expected in calls:
            exit_code, events = _run(*arguments)
            self.assertEqual(0, exit_code)
            self.assertEqual(expected, events[-1]["result"]["received"])

        self.assertEqual(
            [expected for _arguments, expected in calls],
            FakeStore.memory_calls,
        )

    def test_memory_cli_reuses_real_record_supersede_list_and_delete(
        self,
    ) -> None:
        record_exit, record_events = _run_real(
            self.project_root,
            "memory",
            "record",
            "--memory-type",
            "decision",
            "--title",
            "Use Karma XPU",
            "--body",
            "Use Karma XPU for approved previews.",
            "--tag",
            "render",
        )
        self.assertEqual(0, record_exit)
        memory_id = record_events[-1]["result"]["memory"]["id"]

        supersede_exit, supersede_events = _run_real(
            self.project_root,
            "memory",
            "supersede",
            "--memory-id",
            memory_id,
            "--memory-type",
            "decision",
            "--title",
            "Use Karma CPU",
            "--body",
            "Use Karma CPU for deterministic previews.",
        )
        self.assertEqual(0, supersede_exit)
        replacement = supersede_events[-1]["result"]["replacement"]
        self.assertEqual("active", replacement["status"])
        self.assertEqual(
            replacement["id"],
            supersede_events[-1]["result"]["superseded"]["superseded_by"],
        )

        list_exit, list_events = _run_real(
            self.project_root,
            "memory",
            "list",
            "--include-superseded",
        )
        self.assertEqual(0, list_exit)
        listed = {
            item["id"]: item["status"]
            for item in list_events[-1]["result"]["items"]
        }
        self.assertEqual("superseded", listed[memory_id])
        self.assertEqual("active", listed[replacement["id"]])

        delete_exit, _delete_events = _run_real(
            self.project_root,
            "memory",
            "delete",
            "--memory-id",
            replacement["id"],
        )
        self.assertEqual(0, delete_exit)
        missing_exit, missing_events = _run_real(
            self.project_root,
            "memory",
            "delete",
            "--memory-id",
            replacement["id"],
        )
        self.assertEqual(3, missing_exit)
        self.assertEqual("MEMORY_NOT_FOUND", missing_events[-1]["code"])

    def test_source_import_list_refresh_and_exact_delete_clean_database(
        self,
    ) -> None:
        original = self.project_root / "selected-rbd.md"
        original.write_bytes(
            b"RBD constraints  and fracture workflow.\r\n\r\n"
        )
        original_bytes = original.read_bytes()

        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(original),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )

        self.assertEqual(0, import_exit)
        imported = import_events[-1]["result"]
        self.assertTrue(imported["copied"])
        self.assertEqual(["user"], imported["refresh"]["groups"])
        source = imported["source"]
        source_id = source["source_id"]
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / Path(source_id)
        )
        sidecar = Path(str(managed) + ".metadata.json")
        self.assertEqual(original_bytes, original.read_bytes())
        self.assertEqual(original_bytes, managed.read_bytes())
        provenance = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual("hia-managed-source/1", provenance["schema"])
        self.assertEqual(str(original.resolve()), provenance["original_path"])
        self.assertEqual(source["sha256"], provenance["source_sha256"])

        list_exit, list_events = _run(
            "sources",
            "list",
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, list_exit)
        self.assertEqual([source_id], [
            item["source_id"]
            for item in list_events[-1]["result"]["items"]
        ])
        self.assertTrue(list_events[-1]["result"]["items"][0]["indexed"])
        self.assertTrue(list_events[-1]["result"]["items"][0]["current"])
        self.assertFalse(list_events[-1]["result"]["items"][0]["stale"])

        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect()) as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT d.id, c.id FROM documents d "
                "JOIN chunks c ON c.document_id = d.id "
                "WHERE d.source_key = ? LIMIT 1",
                (f"user:user_document:{source_id}",),
            ).fetchone()
            self.assertIsNotNone(row)
            document_id, chunk_id = int(row[0]), int(row[1])
            connection.execute(
                "INSERT INTO chunk_vectors("
                "chunk_id, model_id, profile_id, dim, content_hash, "
                "normalized, vector_blob, indexed_at"
                ") SELECT id, 'test-model', 'test-profile', 1, "
                "content_hash, 1, ?, '2026-07-26T00:00:00Z' "
                "FROM chunks WHERE id = ?",
                (sqlite3.Binary(b"\x00\x00\x80?"), chunk_id),
            )

        refresh_exit, refresh_events = _run(
            "sources",
            "refresh",
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, refresh_exit)
        self.assertEqual(
            ["user"],
            refresh_events[-1]["result"]["refresh"]["groups"],
        )

        delete_exit, delete_events = _run(
            "sources",
            "delete",
            "--source-id",
            source_id,
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, delete_exit)
        deleted = delete_events[-1]["result"]
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["document_deleted"])
        self.assertGreaterEqual(deleted["chunks_deleted"], 1)
        self.assertEqual(1, deleted["vectors_deleted"])
        self.assertFalse(managed.exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(original_bytes, original.read_bytes())

        with closing(index._connect()) as connection:  # noqa: SLF001
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM chunk_vectors WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_fts WHERE rowid = ?",
                    (chunk_id,),
                ).fetchone()[0],
            )

    def test_source_import_synchronizes_all_vectors_and_delete_cleans_them(
        self,
    ) -> None:
        original = self.project_root / "selected-vector-workflow.md"
        original.write_text(
            "VectorImportNeedle explains deterministic cache validation. " * 300,
            encoding="utf-8",
        )

        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(original),
            store_type=RealHybridStore,
            project_root=self.project_root,
        )

        self.assertEqual(0, import_exit)
        imported = import_events[-1]["result"]
        source = imported["source"]
        self.assertGreater(source["chunk_count"], 1)
        self.assertEqual(source["chunk_count"], source["vector_count"])
        self.assertEqual(0, imported["vector"]["corpus"]["pending_chunks"])
        self.assertTrue(imported["vector"]["corpus"]["complete"])

        delete_exit, delete_events = _run(
            "sources",
            "delete",
            "--source-id",
            source["source_id"],
            store_type=RealHybridStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, delete_exit)
        deleted = delete_events[-1]["result"]
        self.assertEqual(source["chunk_count"], deleted["chunks_deleted"])
        self.assertEqual(source["vector_count"], deleted["vectors_deleted"])

    def test_source_import_without_encoder_stays_lexical_then_refresh_backfills(
        self,
    ) -> None:
        original = self.project_root / "selected-pending-workflow.md"
        original.write_text(
            "PendingVectorNeedle remains searchable through SQLite FTS5. " * 120,
            encoding="utf-8",
        )

        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(original),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )

        self.assertEqual(0, import_exit)
        imported = import_events[-1]["result"]
        source = imported["source"]
        self.assertTrue(imported["indexed"])
        self.assertGreater(source["chunk_count"], 0)
        self.assertEqual(0, source["vector_count"])
        self.assertEqual("lexical", imported["vector"]["mode_used"])
        self.assertFalse(imported["vector"]["vector"]["available"])
        self.assertTrue(imported["vector"]["vector"]["degraded"])
        self.assertTrue(imported["vector"]["vector"]["fallback_reason"])
        self.assertEqual(
            source["chunk_count"],
            imported["vector"]["corpus"]["pending_chunks"],
        )
        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            lexical_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_fts "
                    "WHERE knowledge_fts MATCH 'PendingVectorNeedle'"
                ).fetchone()[0]
            )
        self.assertGreater(lexical_count, 0)

        refresh_exit, refresh_events = _run(
            "sources",
            "refresh",
            store_type=RealHybridStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, refresh_exit)
        vector = refresh_events[-1]["result"]["vector"]
        self.assertTrue(vector["vector"]["available"])
        self.assertEqual(0, vector["corpus"]["pending_chunks"])
        self.assertTrue(vector["corpus"]["complete"])

        list_exit, list_events = _run(
            "sources",
            "list",
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, list_exit)
        refreshed = list_events[-1]["result"]["items"][0]
        self.assertEqual(refreshed["chunk_count"], refreshed["vector_count"])

    def test_source_list_hashes_only_the_requested_page(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        for number in range(7):
            (index.sources_root / f"source-{number}.md").write_text(
                f"source {number}",
                encoding="utf-8",
            )

        with mock.patch.object(
            cli,
            "_hash_file",
            wraps=cli._hash_file,  # noqa: SLF001
        ) as hash_file:
            exit_code, events = _run(
                "sources",
                "list",
                "--offset",
                "2",
                "--limit",
                "3",
                store_type=RealIndexStore,
                project_root=self.project_root,
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(7, events[-1]["result"]["total"])
        self.assertEqual(3, len(events[-1]["result"]["items"]))
        self.assertEqual(3, hash_file.call_count)

    def test_caption_import_list_and_delete_use_transcript_identity(self) -> None:
        original = self.project_root / "selected-lesson.srt"
        original.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nTranscriptCliNeedle\n",
            encoding="utf-8",
        )
        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(original),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, import_exit)
        imported_source = import_events[-1]["result"]["source"]
        source_id = imported_source["source_id"]
        self.assertEqual("user_transcript", imported_source["source_kind"])
        self.assertTrue(imported_source["indexed"])

        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT source, source_key FROM documents WHERE source_key = ?",
                (f"user:user_transcript:{source_id}",),
            ).fetchone()
        self.assertEqual(
            ("user_transcript", f"user:user_transcript:{source_id}"),
            tuple(row),
        )

        delete_exit, delete_events = _run(
            "sources",
            "delete",
            "--source-id",
            source_id,
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, delete_exit)
        self.assertTrue(delete_events[-1]["result"]["document_deleted"])

    def test_media_import_manages_only_sidecar_and_requires_transcript(self) -> None:
        video = self.project_root / "large-tutorial.mp4"
        with video.open("wb") as stream:
            stream.seek(6 * 1024 * 1024)
            stream.write(b"x")
        transcript = video.with_suffix(".txt")
        transcript.write_text(
            "MediaTranscriptNeedle builds a deterministic cache.",
            encoding="utf-8",
        )

        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(video),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, import_exit)
        result = import_events[-1]["result"]
        self.assertFalse(result["media_copied"])
        self.assertTrue(result["transcript_managed"])
        source = result["source"]
        self.assertEqual("user_transcript", source["source_kind"])
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / source["source_id"]
        )
        self.assertEqual(".txt", managed.suffix)
        self.assertEqual(transcript.read_bytes(), managed.read_bytes())
        self.assertFalse(
            any(
                path.suffix.casefold() == ".mp4"
                for path in managed.parent.iterdir()
            )
        )

        index = LocalKnowledgeIndex(self.project_root)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )
        self.addCleanup(store.close)
        self.assertTrue(_build_all_vectors(store)["complete"])
        delete_exit, delete_events = _run(
            "sources",
            "delete",
            "--source-id",
            source["source_id"],
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, delete_exit)
        deleted = delete_events[-1]["result"]
        self.assertTrue(deleted["document_deleted"])
        self.assertGreaterEqual(deleted["chunks_deleted"], 1)
        self.assertGreaterEqual(deleted["vectors_deleted"], 1)
        self.assertFalse(managed.exists())
        self.assertTrue(video.exists())

        silent = self.project_root / "silent.mov"
        silent.write_bytes(b"media-without-sidecar")
        missing_exit, missing_events = _run(
            "sources",
            "import",
            "--path",
            str(silent),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(3, missing_exit)
        self.assertEqual("error", missing_events[-1]["event"])
        self.assertEqual("TRANSCRIPT_REQUIRED", missing_events[-1]["code"])

    def test_thread_snapshot_replace_search_and_remove(self) -> None:
        snapshots = self.project_root / ".runtime" / "thread-snapshots"
        snapshots.mkdir(parents=True)
        snapshot = snapshots / "thread-1.json"
        snapshot.write_text(
            json.dumps(
                {
                    "schema": "hia-thread-snapshot/1",
                    "thread_id": "thread-1",
                    "messages": [
                        {
                            "id": "user-1",
                            "turn_id": "turn-1",
                            "role": "user",
                            "content": "ThreadSnapshotUserNeedle",
                        },
                        {
                            "id": "assistant-1",
                            "turn_id": "turn-1",
                            "role": "assistant",
                            "phase": "final_answer",
                            "content": "ThreadSnapshotFinalNeedle",
                        },
                        {
                            "id": "reasoning-1",
                            "role": "assistant",
                            "type": "reasoning",
                            "content": "PrivateReasoningNeedle",
                        },
                        {
                            "id": "tool-1",
                            "role": "tool",
                            "type": "tool_result",
                            "content": "PrivateToolNeedle",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        relative_snapshot = snapshot.relative_to(
            self.project_root
        ).as_posix()
        first_exit, first_events = _run_real(
            self.project_root,
            "thread",
            "import",
            "--thread-id",
            "thread-1",
            "--snapshot-file",
            relative_snapshot,
        )
        self.assertEqual(0, first_exit)
        first = first_events[-1]["result"]
        self.assertEqual("replaced", first["status"])
        self.assertEqual(2, first["records_indexed"])
        self.assertEqual(2, first["messages_excluded"])

        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT source_key, attributes_json FROM documents "
                "WHERE source = 'thread_export' ORDER BY source_key"
            ).fetchall()
        self.assertEqual(2, len(rows))
        assistant_key = next(
            str(row[0])
            for row in rows
            if json.loads(str(row[1]))["provenance"][
                "message_id"
            ]
            == "assistant-1"
        )

        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )
        self.addCleanup(store.close)
        self.assertTrue(_build_all_vectors(store)["complete"])
        for mode in ("lexical", "vector", "hybrid"):
            with self.subTest(mode=mode):
                result = store.search_many(
                    ("ThreadSnapshotFinalNeedle",),
                    {"user"},
                    current_houdini_version="21.0.440",
                    offset=0,
                    limit=10,
                    mode=mode,
                    source_kinds={"thread_export"},
                )[0]
                self.assertTrue(result["matches"])
        for private in ("PrivateReasoningNeedle", "PrivateToolNeedle"):
            self.assertEqual(
                [],
                store.search_many(
                    (private,),
                    {"user"},
                    current_houdini_version="21.0.440",
                    offset=0,
                    limit=10,
                    mode="lexical",
                    source_kinds={"thread_export"},
                )[0]["matches"],
            )
        store.close()

        snapshot.write_text(
            json.dumps(
                {
                    "schema": "hia-thread-snapshot/1",
                    "thread_id": "thread-1",
                    "messages": [
                        {
                            "id": "assistant-1",
                            "turn_id": "turn-1",
                            "role": "assistant",
                            "phase": "final_answer",
                            "content": "ThreadSnapshotUpdatedNeedle",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        update_exit, update_events = _run_real(
            self.project_root,
            "thread",
            "import",
            "--thread-id",
            "thread-1",
            "--snapshot-file",
            relative_snapshot,
        )
        self.assertEqual(0, update_exit)
        refresh = update_events[-1]["result"]["refresh"]
        self.assertEqual(1, refresh["documents_updated"])
        self.assertEqual(1, refresh["documents_removed"])
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            updated = connection.execute(
                "SELECT source_key FROM documents "
                "WHERE source = 'thread_export'"
            ).fetchall()
            private_chunks = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_fts "
                    "WHERE knowledge_fts MATCH 'PrivateReasoningNeedle OR "
                    "PrivateToolNeedle'"
                ).fetchone()[0]
            )
        self.assertEqual([(assistant_key,)], updated)
        self.assertEqual(0, private_chunks)

        remove_exit, remove_events = _run_real(
            self.project_root,
            "thread",
            "remove",
            "--thread-id",
            "thread-1",
        )
        self.assertEqual(0, remove_exit)
        self.assertEqual(
            1,
            remove_events[-1]["result"]["refresh"]["documents_removed"],
        )
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            counts = tuple(
                int(value)
                for value in connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM documents "
                    "WHERE source = 'thread_export'), "
                    "(SELECT COUNT(*) FROM chunks c JOIN documents d "
                    "ON d.id = c.document_id "
                    "WHERE d.source = 'thread_export'), "
                    "(SELECT COUNT(*) FROM chunk_vectors v JOIN chunks c "
                    "ON c.id = v.chunk_id JOIN documents d "
                    "ON d.id = c.document_id "
                    "WHERE d.source = 'thread_export')"
                ).fetchone()
            )
        self.assertEqual((0, 0, 0), counts)

    def test_thread_snapshot_rejects_external_and_mismatched_input(self) -> None:
        snapshot = self.project_root / "thread.json"
        snapshot.write_text(
            json.dumps(
                {
                    "schema": "hia-thread-snapshot/1",
                    "thread_id": "different-thread",
                    "messages": [],
                }
            ),
            encoding="utf-8",
        )

        external_exit, external_events = _run_real(
            self.project_root,
            "thread",
            "import",
            "--thread-id",
            "thread-1",
            "--snapshot-file",
            str(snapshot.resolve()),
        )
        self.assertEqual(2, external_exit)
        self.assertEqual(
            "UNSAFE_THREAD_SNAPSHOT",
            external_events[-1]["code"],
        )

        mismatch_exit, mismatch_events = _run_real(
            self.project_root,
            "thread",
            "import",
            "--thread-id",
            "thread-1",
            "--snapshot-file",
            "thread.json",
        )
        self.assertEqual(2, mismatch_exit)
        self.assertEqual(
            "INVALID_THREAD_SNAPSHOT",
            mismatch_events[-1]["code"],
        )

    def test_source_import_reports_a_resumable_refresh_failure(self) -> None:
        original = self.project_root / "selected-help.md"
        original.write_text("Selected local help.", encoding="utf-8")

        with mock.patch.object(
            LocalKnowledgeIndex,
            "refresh",
            side_effect=RuntimeError("synthetic refresh failure"),
        ):
            exit_code, events = _run(
                "sources",
                "import",
                "--path",
                str(original),
                store_type=RealIndexStore,
                project_root=self.project_root,
            )

        self.assertEqual(0, exit_code)
        result = events[-1]["result"]
        self.assertTrue(result["copied"])
        self.assertFalse(result["indexed"])
        self.assertEqual("refresh_failed", result["refresh"]["refresh_reason"])
        self.assertTrue(result["warnings"])
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / result["source"]["source_id"]
        )
        self.assertTrue(managed.is_file())
        self.assertEqual(original.read_bytes(), managed.read_bytes())

    def test_source_delete_restores_files_when_database_cleanup_fails(
        self,
    ) -> None:
        original = self.project_root / "selected-restore.md"
        original.write_text("Restore this managed source.", encoding="utf-8")
        import_exit, import_events = _run(
            "sources",
            "import",
            "--path",
            str(original),
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(0, import_exit)
        source_id = import_events[-1]["result"]["source"]["source_id"]
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / source_id
        )
        sidecar = Path(str(managed) + ".metadata.json")

        with mock.patch.object(
            LocalKnowledgeIndex,
            "_delete_document",
            side_effect=RuntimeError("synthetic delete failure"),
        ):
            delete_exit, delete_events = _run(
                "sources",
                "delete",
                "--source-id",
                source_id,
                store_type=RealIndexStore,
                project_root=self.project_root,
            )

        self.assertEqual(1, delete_exit)
        self.assertEqual("SOURCES_DELETE_FAILED", delete_events[-1]["code"])
        self.assertTrue(managed.is_file())
        self.assertTrue(sidecar.is_file())
        index = LocalKnowledgeIndex(self.project_root)
        with closing(index._connect(read_only=True)) as connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE source_key = ?",
                    (f"user:user_document:{source_id}",),
                ).fetchone()[0],
            )

    def test_source_delete_rejects_escape_reparse_and_missing_target(self) -> None:
        index = LocalKnowledgeIndex(self.project_root)
        sources = index.sources_root
        managed = sources / "managed.md"
        managed.write_text("Managed source", encoding="utf-8")
        outside = self.project_root / "outside.md"
        outside.write_text("Must survive", encoding="utf-8")

        escape_exit, escape_events = _run(
            "sources",
            "delete",
            "--source-id",
            "../outside.md",
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(2, escape_exit)
        self.assertEqual(
            "UNSAFE_MANAGED_SOURCE_PATH",
            escape_events[-1]["code"],
        )
        self.assertTrue(outside.exists())

        real_probe = cli._is_reparse_point  # noqa: SLF001
        with mock.patch.object(
            cli,
            "_is_reparse_point",
            side_effect=lambda path: (
                Path(path).name == "managed.md" or real_probe(Path(path))
            ),
        ):
            reparse_exit, reparse_events = _run(
                "sources",
                "delete",
                "--source-id",
                "managed.md",
                store_type=RealIndexStore,
                project_root=self.project_root,
            )
        self.assertEqual(2, reparse_exit)
        self.assertEqual(
            "UNSAFE_MANAGED_SOURCE_PATH",
            reparse_events[-1]["code"],
        )
        self.assertTrue(managed.exists())

        missing_exit, missing_events = _run(
            "sources",
            "delete",
            "--source-id",
            "missing.md",
            store_type=RealIndexStore,
            project_root=self.project_root,
        )
        self.assertEqual(3, missing_exit)
        self.assertEqual("SOURCE_NOT_FOUND", missing_events[-1]["code"])


if __name__ == "__main__":
    unittest.main()
