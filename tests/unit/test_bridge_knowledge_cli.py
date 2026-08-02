from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.knowledge_cli import (  # noqa: E402
    KnowledgeCliError,
    KnowledgeCliRunner,
    _remove_thread_snapshot,
)


class _Completed:
    def __init__(
        self,
        payload: dict[str, Any],
        *,
        returncode: int = 0,
    ) -> None:
        self.returncode = returncode
        self.stdout = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        self.stderr = b""


class _RunFactory:
    def __init__(self, responses: list[_Completed] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> _Completed:
        self.calls.append((list(command), dict(kwargs)))
        if self.responses:
            return self.responses.pop(0)
        return _Completed({"ok": True, "result": {}})


class _Process:
    def __init__(self, stdout: Any, payload: dict[str, Any]) -> None:
        self.pid = 4312
        self.returncode: int | None = None
        stdout.write(
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        stdout.flush()
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout: float) -> int:
        del timeout
        return int(self.returncode or 0)


class _PopenFactory:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.processes: list[_Process] = []

    def __call__(self, command: list[str], **kwargs: Any) -> _Process:
        self.calls.append((list(command), dict(kwargs)))
        process = _Process(kwargs["stdout"], self.payload)
        self.processes.append(process)
        return process


def _thread_projection(thread_id: str = "thread-selected") -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "result": {
            "thread": {
                "id": thread_id,
                "turns": [
                    {
                        "id": "turn-public-1",
                        "items": [
                            {
                                "id": "item-user-1",
                                "type": "userMessage",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "PublicUserNeedle",
                                    },
                                    {
                                        "type": "localImage",
                                        "path": r"E:\private\image.png",
                                    },
                                ],
                            },
                            {
                                "id": "item-final-1",
                                "type": "agentMessage",
                                "text": "PublicFinalNeedle",
                            },
                            {
                                "id": "tool-private-1",
                                "type": "commandExecution",
                                "aggregatedOutput": "PrivateToolNeedle",
                            },
                        ],
                    }
                ],
            }
        },
    }


class BridgeKnowledgeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = REPOSITORY_ROOT / ".runtime"
        runtime_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="knowledge-bridge-test-",
            dir=runtime_root,
        )
        self.project_root = Path(self.temporary.name)
        scripts = self.project_root / "scripts"
        scripts.mkdir()
        source = REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
        (scripts / "hia-knowledge.ps1").write_bytes(source.read_bytes())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        run_factory: Any,
        popen_factory: Any | None = None,
    ) -> KnowledgeCliRunner:
        runner = KnowledgeCliRunner(
            self.project_root,
            run_factory=run_factory,
            popen_factory=popen_factory or _PopenFactory({}),
        )
        runner._powershell = lambda: Path(r"C:\Windows\System32\powershell.exe")
        return runner

    def test_status_keeps_environment_index_and_builtin_pack_distinct(
        self,
    ) -> None:
        status = _Completed(
            {
                "ok": True,
                "action": "status",
                "result": {
                    "environment": {
                        "state": "ready",
                        "embedding_mode": "cpu_embedding",
                        "active_profile": "cpu",
                    },
                    "index": {
                        "available": True,
                        "document_count": 14,
                        "user_document_count": 2,
                        "chunk_count": 40,
                        "vector_count": 20,
                        "vector_pending": 20,
                    },
                    "sources": {
                        "items": [
                            {
                                "source_id": "guide-a1.pdf",
                                "name": "guide.pdf",
                                "source": r"D:\docs\guide.pdf",
                                "managed_path": ".runtime/knowledge/sources/guide-a1.pdf",
                                "format": "pdf",
                                "size_bytes": 120,
                                "index_status": "indexed",
                            }
                        ],
                        "total": 1,
                    },
                },
            }
        )
        index_status = _Completed(
            {
                "event": "completed",
                "action": "status",
                "index": {
                    "available": True,
                    "complete": False,
                    "total_chunks": 40,
                    "vector_chunks": 20,
                    "pending_chunks": 20,
                    "corpus": {
                        "builtin_pack": {
                            "installed": True,
                            "pack_id": "sidefx-official-workflows-v1",
                            "pack_version": "1",
                            "cards": 10,
                            "complete": True,
                        },
                        "user_documents": 2,
                    },
                },
            }
        )
        run = _RunFactory([status, index_status])
        runner = self._runner(run_factory=run)

        result = runner.handle({"action": "status"})

        self.assertEqual("ready", result["environment"]["state"])
        self.assertFalse(result["index"]["complete"])
        self.assertEqual(14, result["index"]["document_count"])
        self.assertEqual(2, result["index"]["user_document_count"])
        self.assertEqual(20, result["index"]["vector_pending"])
        self.assertEqual(
            {
                "installed": True,
                "pack_id": "sidefx-official-workflows-v1",
                "version": "1",
                "card_count": 10,
                "complete": True,
            },
            result["built_in"],
        )
        self.assertEqual(1, result["sources"]["total"])
        self.assertEqual("guide-a1.pdf", result["sources"]["items"][0]["source_id"])
        self.assertEqual(
            ["status", "index-status"],
            [
                command[command.index("-Action") + 1]
                for command, _kwargs in run.calls
            ],
        )
        for command, kwargs in run.calls:
            self.assertEqual(
                str(self.project_root / "scripts" / "hia-knowledge.ps1"),
                command[command.index("-File") + 1],
            )
            self.assertEqual(str(self.project_root), kwargs["cwd"])
            self.assertNotIn("HIA_BRIDGE_TOKEN", kwargs["env"])

    def test_missing_environment_does_not_claim_index_or_pack_complete(
        self,
    ) -> None:
        run = _RunFactory(
            [
                _Completed(
                    {
                        "ok": True,
                        "action": "status",
                        "result": {
                            "environment": {
                                "state": "missing",
                                "embedding_mode": "fts5",
                            },
                            "index": {"available": False},
                            "sources": {"items": [], "total": None},
                        },
                    }
                )
            ]
        )
        runner = self._runner(run_factory=run)

        result = runner.handle({"action": "status"})

        self.assertEqual("missing", result["environment"]["state"])
        self.assertIsNone(result["index"]["complete"])
        self.assertIsNone(result["built_in"]["card_count"])
        self.assertIsNone(result["sources"]["total"])
        self.assertEqual(1, len(run.calls))

    def test_missing_embedding_observation_stays_unavailable(self) -> None:
        run = _RunFactory(
            [
                _Completed(
                    {
                        "ok": True,
                        "action": "status",
                        "result": {
                            "environment": {},
                            "index": {"available": False},
                            "sources": {"items": [], "total": None},
                        },
                    }
                )
            ]
        )
        runner = self._runner(run_factory=run)

        result = runner.handle({"action": "status"})

        self.assertEqual("missing", result["environment"]["state"])
        self.assertEqual("unavailable", result["environment"]["embedding_mode"])
        self.assertNotIn("fallback_non_blocking", result["environment"])

    def test_repair_required_uses_public_pack_metadata_without_claiming_install(
        self,
    ) -> None:
        manifest = (
            self.project_root / "knowledge" / "sidefx-official" / "manifest.json"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "pack_id": "sidefx-official-workflows-v2",
                    "pack_version": "2.0.0",
                    "sources": [{"id": "one"}, {"id": "two"}],
                }
            ),
            encoding="utf-8",
        )
        run = _RunFactory(
            [
                _Completed(
                    {
                        "ok": True,
                        "action": "status",
                        "result": {
                            "environment": {
                                "state": "repair_required",
                                "embedding_mode": "fts5",
                            },
                            "index": {"available": True},
                            "sources": {"items": [], "total": 0},
                        },
                    }
                )
            ]
        )
        runner = self._runner(run_factory=run)

        result = runner.handle({"action": "status"})

        self.assertEqual(
            {
                "installed": None,
                "pack_id": "sidefx-official-workflows-v2",
                "version": "2.0.0",
                "card_count": 2,
                "complete": None,
            },
            result["built_in"],
        )
        self.assertIsNone(result["index"]["complete"])
        self.assertEqual(1, len(run.calls))

    def test_rebuild_is_one_cancellable_project_local_job(self) -> None:
        popen = _PopenFactory(
            {
                "event": "completed",
                "action": "build",
                "index": {
                    "available": True,
                    "complete": True,
                    "total_chunks": 8,
                    "vector_chunks": 8,
                    "pending_chunks": 0,
                },
            }
        )
        run = _RunFactory()
        runner = self._runner(run_factory=run, popen_factory=popen)

        started = runner.handle(
            {"action": "start", "operation": "rebuild"}
        )
        job_id = started["job"]["job_id"]
        self.assertTrue(started["job"]["cancellable"])
        self.assertEqual("running", started["job"]["state"])
        command, kwargs = popen.calls[0]
        self.assertEqual("index-build", command[command.index("-Action") + 1])
        self.assertEqual(str(self.project_root), kwargs["cwd"])
        self.assertTrue(
            Path(started["job"]["log_path"]).is_relative_to(self.project_root)
        )

        cancelled = runner.handle({"action": "cancel", "job_id": job_id})

        self.assertEqual("cancelled", cancelled["job"]["state"])
        self.assertEqual(1, popen.processes[0].terminate_calls)
        with self.assertRaises(KnowledgeCliError) as active:
            runner.handle(
                {"action": "cancel", "job_id": "b" * 32}
            )
        self.assertEqual("KNOWLEDGE_JOB_NOT_FOUND", active.exception.code)

    def test_repair_is_background_but_has_no_fake_cancel_contract(self) -> None:
        popen = _PopenFactory(
            {
                "status": "repaired",
                "mode": "knowledge-parser-only",
                "log_path": str(
                    self.project_root
                    / ".runtime"
                    / "launcher"
                    / "embedding-install-test.log"
                ),
            }
        )
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=popen,
        )
        started = runner.handle(
            {"action": "start", "operation": "repair"}
        )

        self.assertFalse(started["job"]["cancellable"])
        command, _kwargs = popen.calls[0]
        self.assertEqual(
            "environment-repair",
            command[command.index("-Action") + 1],
        )
        self.assertNotIn("-KnowledgeParserOnly", command)
        status = runner.handle({"action": "status"})
        self.assertEqual(
            started["job"]["job_id"],
            status["job"]["job_id"],
        )
        self.assertEqual("running", status["job"]["state"])
        with self.assertRaises(KnowledgeCliError) as raised:
            runner.handle(
                {
                    "action": "cancel",
                    "job_id": started["job"]["job_id"],
                }
            )
        self.assertEqual("KNOWLEDGE_JOB_NOT_CANCELLABLE", raised.exception.code)
        self.assertIsNone(popen.processes[0].returncode)

    def test_import_and_delete_reject_ambiguous_or_escaping_arguments(
        self,
    ) -> None:
        popen = _PopenFactory({"ok": True, "result": {}})
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=popen,
        )
        with self.assertRaises(KnowledgeCliError):
            runner.handle(
                {
                    "action": "start",
                    "operation": "import_files",
                    "paths": ["relative.txt"],
                }
            )
        with self.assertRaises(KnowledgeCliError):
            runner.handle(
                {
                    "action": "start",
                    "operation": "delete",
                    "source_id": "../original.pdf",
                }
            )
        with self.assertRaises(KnowledgeCliError):
            runner.handle(
                {
                    "action": "start",
                    "operation": "delete",
                    "source_id": "guide.pdf",
                    "unexpected": True,
                }
            )
        self.assertEqual([], popen.calls)

    def test_file_import_passes_an_absolute_path_without_touching_original(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="knowledge-panel-test-",
            dir=self.project_root,
        ) as temporary:
            original = Path(temporary) / "guide.md"
            original.write_text("# Guide", encoding="utf-8")
            popen = _PopenFactory(
                {
                    "ok": True,
                    "result": {"imported": 1, "already_imported": 0},
                }
            )
            runner = self._runner(
                run_factory=_RunFactory(),
                popen_factory=popen,
            )

            runner.handle(
                {
                    "action": "start",
                    "operation": "import_files",
                    "paths": [str(original)],
                }
            )

            command, _kwargs = popen.calls[0]
            self.assertEqual("import-file", command[command.index("-Action") + 1])
            self.assertIn(str(original.resolve()), command)
            self.assertEqual("# Guide", original.read_text(encoding="utf-8"))

    def test_thread_import_uses_one_bounded_public_snapshot_and_stable_ids(
        self,
    ) -> None:
        popen = _PopenFactory(
            {
                "ok": True,
                "result": {
                    "status": "replaced",
                    "thread_id": "thread-selected",
                    "messages_received": 2,
                    "records_indexed": 2,
                    "messages_excluded": 0,
                },
            }
        )
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=popen,
        )
        reader_calls: list[str] = []

        started = runner.handle(
            {
                "action": "start",
                "operation": "import_thread",
                "thread_id": "thread-selected",
            },
            thread_reader=lambda thread_id: (
                reader_calls.append(thread_id)
                or _thread_projection(thread_id)
            ),
        )

        self.assertEqual(["thread-selected"], reader_calls)
        command, _kwargs = popen.calls[0]
        self.assertEqual(
            "thread-import",
            command[command.index("-Action") + 1],
        )
        self.assertEqual(
            "thread-selected",
            command[command.index("-ThreadId") + 1],
        )
        snapshot_relative = command[command.index("-SnapshotFile") + 1]
        self.assertTrue(snapshot_relative.startswith(".runtime/cache/knowledge-cli/"))
        snapshot_path = self.project_root / Path(snapshot_relative)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema", "thread_id", "messages"},
            set(snapshot),
        )
        self.assertEqual(
            ["user", "assistant"],
            [message["role"] for message in snapshot["messages"]],
        )
        self.assertEqual(
            ["item-user-1", "item-final-1"],
            [message["id"] for message in snapshot["messages"]],
        )
        self.assertTrue(snapshot["messages"][1]["phase"] == "final_answer")
        serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn("PublicUserNeedle", serialized_snapshot)
        self.assertIn("PublicFinalNeedle", serialized_snapshot)
        self.assertNotIn("PrivateToolNeedle", serialized_snapshot)
        self.assertNotIn(r"E:\private\image.png", serialized_snapshot)
        self.assertNotIn("PublicUserNeedle", " ".join(command))
        self.assertNotIn(
            "PublicUserNeedle",
            json.dumps(started, ensure_ascii=False),
        )
        self.assertNotIn(
            "PublicUserNeedle",
            Path(started["job"]["log_path"]).read_text(encoding="utf-8"),
        )

        popen.processes[0].returncode = 0
        completed = runner.handle(
            {
                "action": "job_status",
                "job_id": started["job"]["job_id"],
            }
        )
        self.assertEqual("completed", completed["job"]["state"])
        self.assertEqual(
            "thread-selected",
            completed["job"]["result"]["thread_id"],
        )
        self.assertEqual(2, completed["job"]["result"]["records_indexed"])
        self.assertFalse(snapshot_path.exists())
        self.assertNotIn(
            "PublicFinalNeedle",
            json.dumps(completed, ensure_ascii=False),
        )

    def test_thread_snapshot_cleanup_runs_after_failure_cancel_and_close(
        self,
    ) -> None:
        cases = ("failed", "cancelled", "closed")
        for case in cases:
            with self.subTest(case=case):
                popen = _PopenFactory(
                    {
                        "ok": False,
                        "error": {
                            "code": "THREAD_IMPORT_FAILED",
                            "message": "bounded failure",
                        },
                    }
                )
                runner = self._runner(
                    run_factory=_RunFactory(),
                    popen_factory=popen,
                )
                started = runner.handle(
                    {
                        "action": "start",
                        "operation": "import_thread",
                        "thread_id": f"thread-{case}",
                    },
                    thread_reader=lambda thread_id: _thread_projection(
                        thread_id
                    ),
                )
                command, _kwargs = popen.calls[0]
                snapshot_path = self.project_root / Path(
                    command[command.index("-SnapshotFile") + 1]
                )
                self.assertTrue(snapshot_path.is_file())
                if case == "failed":
                    popen.processes[0].returncode = 3
                    result = runner.handle(
                        {
                            "action": "job_status",
                            "job_id": started["job"]["job_id"],
                        }
                    )
                    self.assertEqual("failed", result["job"]["state"])
                elif case == "cancelled":
                    result = runner.handle(
                        {
                            "action": "cancel",
                            "job_id": started["job"]["job_id"],
                        }
                    )
                    self.assertEqual("cancelled", result["job"]["state"])
                else:
                    runner.close()
                    self.assertEqual("cancelled", runner._job["state"])
                self.assertFalse(snapshot_path.exists())

    def test_thread_remove_never_reads_or_materializes_a_snapshot(self) -> None:
        popen = _PopenFactory(
            {
                "ok": True,
                "result": {
                    "status": "removed",
                    "thread_id": "thread-selected",
                },
            }
        )
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=popen,
        )

        started = runner.handle(
            {
                "action": "start",
                "operation": "remove_thread",
                "thread_id": "thread-selected",
            },
            thread_reader=lambda _thread_id: self.fail(
                "remove_thread must not read transcript text"
            ),
        )
        command, _kwargs = popen.calls[0]
        self.assertEqual(
            "thread-remove",
            command[command.index("-Action") + 1],
        )
        self.assertNotIn("-SnapshotFile", command)
        popen.processes[0].returncode = 0
        completed = runner.handle(
            {
                "action": "job_status",
                "job_id": started["job"]["job_id"],
            }
        )
        self.assertEqual("removed", completed["job"]["result"]["status"])
        self.assertEqual(
            [],
            list(
                (
                    self.project_root
                    / ".runtime"
                    / "cache"
                    / "knowledge-cli"
                ).glob("thread-snapshot-*.json")
            ),
        )

    def test_thread_snapshot_limits_reparse_and_cleanup_failure_fail_closed(
        self,
    ) -> None:
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=_PopenFactory({"ok": True, "result": {}}),
        )
        oversized = _thread_projection("thread-large")
        oversized_items = oversized["result"]["thread"]["turns"][0]["items"]
        oversized_items[:] = [
            {
                "id": f"item-{index}",
                "type": "agentMessage",
                "text": "x" * 262_144,
            }
            for index in range(17)
        ]
        with self.assertRaises(KnowledgeCliError) as too_large:
            runner.handle(
                {
                    "action": "start",
                    "operation": "import_thread",
                    "thread_id": "thread-large",
                },
                thread_reader=lambda _thread_id: oversized,
            )
        self.assertEqual("THREAD_SNAPSHOT_TOO_LARGE", too_large.exception.code)

        import hia_bridge.knowledge_cli as knowledge_cli_module

        original_reparse = knowledge_cli_module._is_reparse_point
        with mock.patch.object(
            knowledge_cli_module,
            "_is_reparse_point",
            side_effect=lambda path: (
                Path(path).name == "knowledge-cli"
                or original_reparse(Path(path))
            ),
        ):
            with self.assertRaises(KnowledgeCliError) as unsafe:
                runner.handle(
                    {
                        "action": "start",
                        "operation": "import_thread",
                        "thread_id": "thread-unsafe",
                    },
                    thread_reader=lambda thread_id: _thread_projection(
                        thread_id
                    ),
                )
        self.assertEqual("UNSAFE_KNOWLEDGE_LOG_PATH", unsafe.exception.code)

        popen = _PopenFactory(
            {
                "ok": True,
                "result": {
                    "status": "replaced",
                    "thread_id": "thread-cleanup-warning",
                },
            }
        )
        runner = self._runner(
            run_factory=_RunFactory(),
            popen_factory=popen,
        )
        started = runner.handle(
            {
                "action": "start",
                "operation": "import_thread",
                "thread_id": "thread-cleanup-warning",
            },
            thread_reader=lambda thread_id: _thread_projection(thread_id),
        )
        command, _kwargs = popen.calls[0]
        snapshot_path = self.project_root / Path(
            command[command.index("-SnapshotFile") + 1]
        )
        original_unlink = Path.unlink

        def fail_snapshot_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
            if path == snapshot_path:
                raise PermissionError("simulated cleanup denial")
            original_unlink(path, *args, **kwargs)

        popen.processes[0].returncode = 0
        with mock.patch.object(Path, "unlink", new=fail_snapshot_unlink):
            completed = runner.handle(
                {
                    "action": "job_status",
                    "job_id": started["job"]["job_id"],
                }
            )
        warning = completed["job"]["warnings"][0]
        self.assertIn(
            snapshot_path.relative_to(self.project_root).as_posix(),
            warning,
        )
        self.assertNotIn("PublicUserNeedle", warning)
        self.assertTrue(snapshot_path.is_file())

        outside = (
            self.project_root.parent
            / f"outside-thread-snapshot-{os.getpid()}.json"
        )
        outside.write_text("outside sentinel", encoding="utf-8")
        try:
            escape_warning = _remove_thread_snapshot(
                self.project_root,
                outside,
            )
            self.assertIn("[outside project]", escape_warning)
            self.assertEqual(
                "outside sentinel",
                outside.read_text(encoding="utf-8"),
            )
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
