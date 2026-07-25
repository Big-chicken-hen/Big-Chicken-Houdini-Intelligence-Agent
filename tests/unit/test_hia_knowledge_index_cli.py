from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
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
)
from hia_mcp_runtime import knowledge_index_cli as cli  # noqa: E402


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

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.index = self

    @classmethod
    def reset(cls, *, total: int, vector: int = 0) -> None:
        cls.total = total
        cls.vector = vector
        cls.build_calls = 0
        cls.interrupt_on_call = None
        cls.close_calls = 0
        cls.refresh_calls = 0

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
        if source_groups != {"project"} or not force:
            raise AssertionError("unexpected source refresh")
        if snapshot != {"houdini_version": "21.0.000"}:
            raise AssertionError("unexpected source snapshot")
        return {"refreshed": True}, []

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

    def close(self) -> None:
        type(self).close_calls += 1


def _run(*arguments: str) -> tuple[int, list[dict[str, Any]]]:
    output = io.StringIO()
    with mock.patch.object(cli, "HybridKnowledgeStore", FakeStore):
        exit_code = cli.main(
            (
                "--project-root",
                str(REPOSITORY_ROOT),
                *arguments,
            ),
            stdout=output,
        )
    events = [json.loads(line) for line in output.getvalue().splitlines() if line]
    return exit_code, events


class KnowledgeIndexCliTests(unittest.TestCase):
    def test_module_starts_with_contract_python_path_shape(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(RUNTIME_PACKAGE_ROOT), str(SOURCE_ROOT))
        )

        completed = subprocess.run(
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

        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        self.assertIn("{status,build}", completed.stdout)

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
        self.assertEqual(0, FakeStore.refresh_calls)
        self.assertEqual(1, FakeStore.close_calls)

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


if __name__ == "__main__":
    unittest.main()
