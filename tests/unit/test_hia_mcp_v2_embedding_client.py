from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(
    0,
    str(REPOSITORY_ROOT / "houdini_package" / "python_libs"),
)

from hia_mcp_runtime import embedding_client  # noqa: E402
from hia_mcp_runtime.embedding_client import (  # noqa: E402
    DEVICE_ENVIRONMENT,
    DIMENSION_ENVIRONMENT,
    MODEL_DIR_0_6B_ENVIRONMENT,
    MODEL_DIR_8B_ENVIRONMENT,
    PROFILE_ENVIRONMENT,
    PYTHON_ENVIRONMENT,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingConfigurationError,
)
from hia_core.embedding_contract import (  # noqa: E402
    EMBEDDING_PUBLIC_STATUS_FIELDS,
    MODEL_REVISION_0_6B_ENVIRONMENT,
    MODEL_REVISION_8B_ENVIRONMENT,
    runtime_layout,
)


class HiaMcpV2EmbeddingClientTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=runtime_tmp)
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name) / "embedding-client-project"
        self.project_root.mkdir()

        layout = runtime_layout(self.project_root)
        self.python_path = Path(layout["worker_python"])
        self.python_path.parent.mkdir(parents=True)
        self.python_path.write_bytes(b"test placeholder")
        self.model_0_6b = Path(layout["model_0_6b"])
        self.model_8b = Path(layout["model_8b"])
        self.model_0_6b.mkdir(parents=True)
        self.model_8b.mkdir(parents=True)
        self.worker_script = (
            self.project_root / ".runtime" / "tmp" / "fake_embedding_worker.py"
        )
        self.start_count_path = (
            self.project_root / ".runtime" / "tmp" / "worker-start-count.txt"
        )
        self.shutdown_path = (
            self.project_root / ".runtime" / "tmp" / "worker-shutdown.txt"
        )
        self.processes: list[subprocess.Popen[str]] = []
        self.commands: list[list[str]] = []
        self.child_cwds: list[str] = []
        self.child_environments: list[dict[str, str]] = []
        self.addCleanup(self._stop_processes)

    def _environment(
        self,
        *,
        profile: str = "qwen3-embedding-0.6b",
        include_0_6b: bool = True,
        include_8b: bool = False,
        dim: int | None = 32,
    ) -> dict[str, str]:
        values = {
            PYTHON_ENVIRONMENT: str(self.python_path),
            PROFILE_ENVIRONMENT: profile,
            DEVICE_ENVIRONMENT: "cpu",
        }
        if dim is not None:
            values[DIMENSION_ENVIRONMENT] = str(dim)
        if include_0_6b:
            values[MODEL_DIR_0_6B_ENVIRONMENT] = str(self.model_0_6b)
            values[MODEL_REVISION_0_6B_ENVIRONMENT] = "revision-0-6b"
        if include_8b:
            values[MODEL_DIR_8B_ENVIRONMENT] = str(self.model_8b)
            values[MODEL_REVISION_8B_ENVIRONMENT] = "revision-8b"
        return values

    def _write_worker(self, mode: str = "valid") -> None:
        script = f"""
import json
import math
import os
import pathlib
import sys
import time

MODE = {mode!r}
START_COUNT = pathlib.Path({str(self.start_count_path)!r})
SHUTDOWN = pathlib.Path({str(self.shutdown_path)!r})
try:
    count = int(START_COUNT.read_text(encoding="utf-8"))
except Exception:
    count = 0
START_COUNT.parent.mkdir(parents=True, exist_ok=True)
START_COUNT.write_text(str(count + 1), encoding="utf-8")
profile = None

def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    request = json.loads(line)
    request_id = request["id"]
    method = request["method"]
    params = request.get("params", {{}})
    if method == "init":
        profile = dict(params)
        send({{
            "id": request_id,
            "ok": True,
            "result": {{
                "model_id": profile["model_id"],
                "profile": profile["profile"],
                "dim": profile["dim"],
                "model_revision": profile["model_revision"],
                "device": profile["device"],
                "initialized": True,
                "loaded": False
            }}
        }})
    elif method == "embed":
        forbidden = [
            name for name in (
                "OPENAI_API_KEY",
                "HF_TOKEN",
                "HUGGINGFACE_HUB_TOKEN",
                "HIA_MCP_V2_TOKEN",
                "AWS_SECRET_ACCESS_KEY"
            )
            if name in os.environ
        ]
        if forbidden:
            send({{
                "id": request_id,
                "ok": False,
                "error": {{
                    "code": "CREDENTIAL_LEAK",
                    "message": "credential environment leaked"
                }}
            }})
            continue
        if MODE in ("fail_8b", "fail_8b_embedding") and profile["profile"] == "qwen3-embedding-8b":
            code = "EMBEDDING_FAILED" if MODE == "fail_8b_embedding" else "MODEL_LOAD_FAILED"
            send({{
                "id": request_id,
                "ok": False,
                "error": {{
                    "code": code,
                    "message": "fake 8B load failed"
                }}
            }})
            continue
        if MODE == "timeout":
            time.sleep(10)
            continue
        texts = params["texts"]
        dim = int(profile["dim"])
        actual_dim = dim - 1 if MODE == "bad_dim" else dim
        vectors = []
        for _text in texts:
            vector = [0.0] * actual_dim
            if vector:
                vector[0] = 1.0
            if MODE == "not_normalized" and vector:
                vector[0] = 0.5
            if MODE == "nonfinite" and vector:
                vector[0] = float("nan")
            vectors.append(vector)
        reported_count = len(vectors) + 1 if MODE == "bad_count" else len(vectors)
        send({{
            "id": request_id,
            "ok": True,
            "result": {{
                "model_id": profile["model_id"],
                "profile": profile["profile"],
                "dim": profile["dim"],
                "model_revision": profile["model_revision"],
                "device": profile["device"],
                "normalized": True,
                "input_type": params["input_type"],
                "count": reported_count,
                "vectors": vectors
            }}
        }})
    elif method == "shutdown":
        SHUTDOWN.write_text("shutdown", encoding="utf-8")
        send({{
            "id": request_id,
            "ok": True,
            "result": {{"shutdown": True}}
        }})
        break
"""
        self.worker_script.parent.mkdir(parents=True, exist_ok=True)
        self.worker_script.write_text(script, encoding="utf-8")

    def _popen_patch(self) -> mock._patch:
        real_popen = subprocess.Popen

        def launch(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.Popen[str]:
            self.commands.append(list(command))
            self.child_cwds.append(str(kwargs["cwd"]))
            self.child_environments.append(dict(kwargs["env"]))
            process = real_popen(
                [sys.executable, "-u", str(self.worker_script)],
                **kwargs,
            )
            self.processes.append(process)
            return process

        return mock.patch.object(
            embedding_client.subprocess,
            "Popen",
            side_effect=launch,
        )

    def _stop_processes(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def test_completely_missing_environment_returns_none(self) -> None:
        self.assertIsNone(
            EmbeddingClient.from_environment(
                self.project_root,
                environ={},
            )
        )
        self.assertEqual([], self.commands)

    def test_partial_or_legacy_configuration_is_rejected(self) -> None:
        with self.assertRaises(EmbeddingConfigurationError) as partial:
            EmbeddingClient.from_environment(
                self.project_root,
                environ={PYTHON_ENVIRONMENT: str(self.python_path)},
            )
        self.assertEqual("EMBEDDING_MODEL_UNAVAILABLE", partial.exception.code)

        with self.assertRaises(EmbeddingConfigurationError) as legacy:
            EmbeddingClient.from_environment(
                self.project_root,
                environ={
                    PYTHON_ENVIRONMENT: str(self.python_path),
                    "HIA_EMBEDDING_MODEL_DIR": str(self.model_0_6b),
                },
            )
        self.assertEqual(
            "EMBEDDING_CONFIGURATION_INVALID",
            legacy.exception.code,
        )

        invalid_revision = self._environment()
        invalid_revision[MODEL_REVISION_0_6B_ENVIRONMENT] = "../escape"
        with self.assertRaises(EmbeddingConfigurationError) as revision:
            EmbeddingClient.from_environment(
                self.project_root,
                environ=invalid_revision,
            )
        self.assertEqual(
            "EMBEDDING_MODEL_REVISION_INVALID",
            revision.exception.code,
        )

    def test_python_and_model_paths_cannot_escape_project_runtime(self) -> None:
        outside_python = self.project_root / "outside-python.exe"
        outside_python.write_bytes(b"outside")
        environment = self._environment()
        environment[PYTHON_ENVIRONMENT] = str(outside_python)
        with self.assertRaises(EmbeddingConfigurationError) as python_error:
            EmbeddingClient.from_environment(
                self.project_root,
                environ=environment,
            )
        self.assertEqual(
            "EMBEDDING_PATH_OUTSIDE_RUNTIME",
            python_error.exception.code,
        )

        outside_model = self.project_root / "outside-model"
        outside_model.mkdir()
        environment = self._environment()
        environment[MODEL_DIR_0_6B_ENVIRONMENT] = str(outside_model)
        with self.assertRaises(EmbeddingConfigurationError) as model_error:
            EmbeddingClient.from_environment(
                self.project_root,
                environ=environment,
            )
        self.assertEqual(
            "EMBEDDING_PATH_OUTSIDE_RUNTIME",
            model_error.exception.code,
        )

    def test_one_persistent_worker_encodes_documents_and_queries_in_one_call(
        self,
    ) -> None:
        self._write_worker()
        environment = self._environment()
        environment.update(
            {
                "OPENAI_API_KEY": "secret",
                "HF_TOKEN": "secret",
                "HIA_MCP_V2_TOKEN": "secret",
                "AWS_SECRET_ACCESS_KEY": "secret",
            }
        )
        with mock.patch.dict(os.environ, environment, clear=False):
            client = EmbeddingClient.from_environment(self.project_root)
            self.assertIsNotNone(client)
            assert client is not None
            configured_status = client.status()
            self.assertEqual(
                set(EMBEDDING_PUBLIC_STATUS_FIELDS),
                set(configured_status),
            )
            self.assertFalse(configured_status["ready"])
            self.assertFalse(configured_status["initialized"])
            self.assertFalse(configured_status["loaded"])
            with self._popen_patch():
                first = client.encode(
                    documents=["document one", "document two"],
                    queries=["query one"],
                )
                second = client.encode(
                    documents=["document three"],
                    queries=[],
                )
                client.close()

        self.assertEqual(2, len(first.document_vectors))
        self.assertEqual(1, len(first.query_vectors))
        self.assertEqual(32, len(first.document_vectors[0]))
        self.assertEqual(
            "Qwen/Qwen3-Embedding-0.6B@revision-0-6b",
            first.model_id,
        )
        self.assertEqual("revision-0-6b", first.model_revision)
        self.assertEqual("qwen3-embedding-0.6b", first.profile_id)
        self.assertEqual("ready", first.status)
        self.assertEqual(1, len(second.document_vectors))
        self.assertEqual(1, len(self.commands))
        self.assertEqual(
            [
                str(self.python_path.resolve()),
                "-I",
                "-m",
                "hia_embedding_worker",
            ],
            self.commands[0],
        )
        self.assertEqual(
            str(self.project_root.resolve()),
            str(Path(self.child_cwds[0]).resolve()),
        )
        self.assertEqual("1", self.start_count_path.read_text(encoding="utf-8"))
        self.assertTrue(self.shutdown_path.is_file())

        child_environment = self.child_environments[0]
        for forbidden in (
            "OPENAI_API_KEY",
            "HF_TOKEN",
            "HUGGINGFACE_HUB_TOKEN",
            "HIA_MCP_V2_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "LOGNAME",
            "USER",
            "LNAME",
            "USERNAME",
        ):
            self.assertNotIn(forbidden, child_environment)
        self.assertEqual("1", child_environment["HF_HUB_OFFLINE"])
        self.assertEqual("1", child_environment["TRANSFORMERS_OFFLINE"])
        for name in (
            "TEMP",
            "TMP",
            "HOME",
            "HF_HOME",
            "TRANSFORMERS_CACHE",
            "TORCH_HOME",
            "TORCHINDUCTOR_CACHE_DIR",
        ):
            self.assertTrue(
                Path(child_environment[name])
                .resolve()
                .is_relative_to(self.project_root.resolve()),
                name,
            )
        self.assertTrue(
            Path(child_environment["TORCHINDUCTOR_CACHE_DIR"]).is_dir()
        )
        self.assertEqual(
            (
                self.project_root
                / ".runtime"
                / "cache"
                / "embedding"
                / "torch"
                / "inductor"
            ).resolve(),
            Path(
                child_environment["TORCHINDUCTOR_CACHE_DIR"]
            ).resolve(),
        )

        process = self.processes[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin is None or process.stdin.closed)
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)
        self.assertEqual("disabled", client.status()["status"])
        self.assertFalse(client.status()["ready"])
        self.assertFalse(client.status()["initialized"])
        self.assertFalse(client.status()["loaded"])

    def test_8b_load_failure_falls_back_once_to_installed_0_6b(self) -> None:
        self._write_worker("fail_8b_embedding")
        environment = self._environment(
            profile="qwen3-embedding-8b",
            include_0_6b=True,
            include_8b=True,
            dim=None,
        )
        with mock.patch.dict(os.environ, environment, clear=False):
            client = EmbeddingClient.from_environment(self.project_root)
            assert client is not None
            with self._popen_patch():
                batch = client.encode(documents=["fallback"], queries=["query"])
                client.close()

        self.assertEqual(2, len(self.commands))
        self.assertEqual("qwen3-embedding-8b", batch.requested_profile)
        self.assertEqual("qwen3-embedding-0.6b", batch.profile_id)
        self.assertEqual(
            "Qwen/Qwen3-Embedding-0.6B@revision-0-6b",
            batch.model_id,
        )
        self.assertEqual("revision-0-6b", batch.model_revision)
        self.assertEqual(1024, batch.dim)
        self.assertEqual("degraded", batch.status)
        self.assertEqual("EMBEDDING_FAILED", batch.fallback_reason)
        self.assertEqual(
            MODEL_DIR_8B_ENVIRONMENT,
            batch.repair["model_dir_environment"],
        )
        self.assertEqual("2", self.start_count_path.read_text(encoding="utf-8"))

    def test_missing_preferred_8b_uses_installed_0_6b_without_download(
        self,
    ) -> None:
        environment = self._environment(
            profile="qwen3-embedding-8b",
            include_0_6b=True,
            include_8b=False,
            dim=None,
        )
        client = EmbeddingClient.from_environment(
            self.project_root,
            environ=environment,
        )
        assert client is not None
        status = client.status()
        self.assertEqual("degraded", status["status"])
        self.assertEqual("qwen3-embedding-8b", status["requested_profile"])
        self.assertEqual("qwen3-embedding-0.6b", status["active_profile"])
        self.assertEqual(1024, status["dim"])
        self.assertEqual(
            "REQUESTED_MODEL_UNAVAILABLE",
            status["fallback_reason"],
        )
        self.assertFalse(status["repair"]["downloads_performed"])
        client.close()
        self.assertEqual([], self.commands)

    def test_8b_profile_uses_contract_default_and_mrl_bounds(self) -> None:
        environment = self._environment(
            profile="qwen3-embedding-8b",
            include_0_6b=False,
            include_8b=True,
            dim=None,
        )
        client = EmbeddingClient.from_environment(
            self.project_root,
            environ=environment,
        )
        assert client is not None
        status = client.status()
        self.assertEqual("qwen3-embedding-8b", status["active_profile"])
        self.assertEqual(1024, status["dim"])
        self.assertEqual(
            "Qwen/Qwen3-Embedding-8B@revision-8b",
            status["model_id"],
        )
        client.close()

        environment[DIMENSION_ENVIRONMENT] = "4096"
        explicit = EmbeddingClient.from_environment(
            self.project_root,
            environ=environment,
        )
        assert explicit is not None
        self.assertEqual(4096, explicit.status()["dim"])
        explicit.close()

        environment[DIMENSION_ENVIRONMENT] = "4097"
        with self.assertRaises(EmbeddingConfigurationError) as dimension:
            EmbeddingClient.from_environment(
                self.project_root,
                environ=environment,
            )
        self.assertEqual(
            "EMBEDDING_DIMENSION_INVALID",
            dimension.exception.code,
        )

    def test_invalid_result_count_dimension_finiteness_and_norm_are_rejected(
        self,
    ) -> None:
        cases = {
            "bad_count": "EMBEDDING_COUNT_MISMATCH",
            "bad_dim": "EMBEDDING_DIMENSION_MISMATCH",
            "nonfinite": "EMBEDDING_VECTOR_INVALID",
            "not_normalized": "EMBEDDING_NOT_NORMALIZED",
        }
        for mode, expected_code in cases.items():
            with self.subTest(mode=mode):
                self._write_worker(mode)
                environment = self._environment()
                with mock.patch.dict(os.environ, environment, clear=False):
                    client = EmbeddingClient.from_environment(
                        self.project_root
                    )
                    assert client is not None
                    with self._popen_patch():
                        with self.assertRaises(EmbeddingClientError) as caught:
                            client.encode(
                                documents=["invalid"],
                                queries=[],
                            )
                        client.close()
                self.assertEqual(expected_code, caught.exception.code)

    def test_timeout_terminates_worker_and_close_is_idempotent(self) -> None:
        self._write_worker("timeout")
        environment = self._environment()
        with mock.patch.dict(os.environ, environment, clear=False):
            client = EmbeddingClient.from_environment(
                self.project_root,
                timeout_seconds=0.1,
            )
            assert client is not None
            started = time.monotonic()
            with self._popen_patch():
                with self.assertRaises(EmbeddingClientError) as caught:
                    client.encode(documents=["slow"], queries=[])
                client.close()
                client.close()
            elapsed = time.monotonic() - started

        self.assertEqual("EMBEDDING_TIMEOUT", caught.exception.code)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(1, len(self.processes))
        self.assertIsNotNone(self.processes[0].poll())


if __name__ == "__main__":
    unittest.main()
