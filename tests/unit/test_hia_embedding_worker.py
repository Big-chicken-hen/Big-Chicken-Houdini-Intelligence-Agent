from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).parents[2]
WORKER_ROOT = REPOSITORY_ROOT / "services" / "hia_mcp_v2" / "embedding_worker"
sys.path.insert(0, str(WORKER_ROOT))

from hia_embedding_worker.worker import (  # noqa: E402
    EmbeddingWorker,
    ModelProfile,
    SentenceTransformerBackend,
    run_stdio,
)


class FakeBackend:
    def __init__(
        self,
        dim: int,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.dim = dim
        self.failure = failure
        self.calls: list[tuple[list[str], str]] = []

    def encode(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]:
        self.calls.append((list(texts), input_type))
        if self.failure is not None:
            raise self.failure
        return [
            [0.0] * (self.dim - 1) + [float(index + 2)]
            for index, _text in enumerate(texts)
        ]


class FakeSentenceTransformer:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append((texts, dict(kwargs)))
        return [[3.0, 4.0] for _text in texts]


class EmbeddingWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hia-embedding-worker-",
            dir=runtime_tmp,
        )
        self.addCleanup(self.temporary.cleanup)
        self.model_dir = Path(self.temporary.name) / "model"
        self.model_dir.mkdir()

    def test_distribution_declares_isolated_embedding_dependencies(self) -> None:
        project = (WORKER_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.10"', project)
        self.assertIn('"sentence-transformers>=2.7.0"', project)
        self.assertIn('"transformers>=4.51.0"', project)

    def _init_request(
        self,
        request_id: str,
        *,
        model_id: str = "Qwen/Qwen3-Embedding-0.6B",
        dim: int = 2,
        profile: str = "qwen3-embedding-0.6b",
        model_revision: str = "local",
        device: str = "auto",
    ) -> dict[str, Any]:
        return {
            "id": request_id,
            "method": "init",
            "params": {
                "model_id": model_id,
                "model_dir": str(self.model_dir.resolve()),
                "dim": dim,
                "profile": profile,
                "model_revision": model_revision,
                "device": device,
            },
        }

    @staticmethod
    def _run(
        worker: EmbeddingWorker,
        requests: Sequence[dict[str, Any] | bytes],
    ) -> tuple[int, list[dict[str, Any]], bytes]:
        raw_lines: list[bytes] = []
        for request in requests:
            if isinstance(request, bytes):
                raw_lines.append(request.rstrip(b"\r\n") + b"\n")
            else:
                raw_lines.append(
                    json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
                )
        output = io.BytesIO()
        status = run_stdio(
            worker,
            input_stream=io.BytesIO(b"".join(raw_lines)),
            output_stream=output,
        )
        responses = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        return status, responses, output.getvalue()

    def test_init_health_and_shutdown_do_not_load_backend(self) -> None:
        factory_calls: list[ModelProfile] = []

        def factory(profile: ModelProfile) -> FakeBackend:
            factory_calls.append(profile)
            return FakeBackend(profile.dim)

        worker = EmbeddingWorker(backend_factory=factory)
        status, responses, _raw = self._run(
            worker,
            [
                self._init_request("init"),
                {"id": "health", "method": "health"},
                {"id": "shutdown", "method": "shutdown"},
                {
                    "id": "never",
                    "method": "embed",
                    "params": {"texts": ["ignored"], "input_type": "query"},
                },
            ],
        )

        self.assertEqual(0, status)
        self.assertEqual(
            ["init", "health", "shutdown"],
            [item["id"] for item in responses],
        )
        self.assertEqual([], factory_calls)
        self.assertFalse(responses[0]["result"]["loaded"])
        self.assertFalse(responses[1]["result"]["loaded"])
        self.assertTrue(responses[2]["result"]["shutdown"])

    def test_qwen_profiles_pass_through_metadata_and_support_dimension_4096(
        self,
    ) -> None:
        profiles = (
            ("Qwen/Qwen3-Embedding-0.6B", "qwen3-embedding-0.6b", 1024),
            ("Qwen/Qwen3-Embedding-8B", "qwen3-embedding-8b", 4096),
        )
        for model_id, profile_name, dimension in profiles:
            with self.subTest(model_id=model_id):
                backends: list[FakeBackend] = []

                def factory(profile: ModelProfile) -> FakeBackend:
                    backend = FakeBackend(profile.dim)
                    backends.append(backend)
                    return backend

                worker = EmbeddingWorker(backend_factory=factory)
                _status, responses, _raw = self._run(
                    worker,
                    [
                        self._init_request(
                            "init",
                            model_id=model_id,
                            dim=dimension,
                            profile=profile_name,
                        ),
                        {
                            "id": "embed",
                            "method": "embed",
                            "params": {
                                "texts": ["local embedding"],
                                "input_type": "document",
                            },
                        },
                        {"id": "shutdown", "method": "shutdown"},
                    ],
                )
                result = responses[1]["result"]
                self.assertEqual(model_id, result["model_id"])
                self.assertEqual(profile_name, result["profile"])
                self.assertEqual(dimension, result["dim"])
                self.assertEqual("local", result["model_revision"])
                self.assertEqual("auto", result["device"])
                self.assertTrue(result["normalized"])
                self.assertEqual(dimension, len(result["vectors"][0]))
                self.assertTrue(
                    all(
                        isinstance(value, float)
                        for value in result["vectors"][0]
                    )
                )
                self.assertAlmostEqual(
                    1.0,
                    math.sqrt(sum(value * value for value in result["vectors"][0])),
                )
                self.assertEqual(1, len(backends))

    def test_backend_load_is_lazy_offline_and_query_only_uses_prompt_name(self) -> None:
        fake_model = FakeSentenceTransformer()
        factory_calls: list[tuple[str, dict[str, Any]]] = []

        def model_factory(path: str, **kwargs: Any) -> FakeSentenceTransformer:
            factory_calls.append((path, dict(kwargs)))
            return fake_model

        profile = ModelProfile(
            model_id="Qwen/Qwen3-Embedding-8B",
            model_dir=self.model_dir.resolve(),
            dim=2,
            profile="qwen3-embedding-8b",
            model_revision="revision-1",
            device="cuda:0",
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            backend = SentenceTransformerBackend(profile, model_factory=model_factory)
            self.assertEqual([], factory_calls)

            backend.encode(["question"], input_type="query")
            backend.encode(["document"], input_type="document")

            self.assertEqual(1, len(factory_calls))
            self.assertTrue(factory_calls[0][1]["local_files_only"])
            self.assertFalse(factory_calls[0][1]["trust_remote_code"])
            self.assertEqual(2, factory_calls[0][1]["truncate_dim"])
            self.assertEqual("cuda:0", factory_calls[0][1]["device"])
            query_options = fake_model.calls[0][1]
            document_options = fake_model.calls[1][1]
            self.assertEqual("query", query_options["prompt_name"])
            self.assertNotIn("prompt_name", document_options)
            self.assertNotIn("prompt", document_options)
            self.assertTrue(query_options["normalize_embeddings"])
            self.assertEqual("1", os.environ["HF_HUB_OFFLINE"])
            self.assertEqual("1", os.environ["TRANSFORMERS_OFFLINE"])

    def test_qwen_mrl_dimension_is_applied_before_normalization(self) -> None:
        for dimension in (1024, 4096):
            with self.subTest(dimension=dimension):
                options_seen: list[dict[str, Any]] = []

                def model_factory(
                    _path: str,
                    **options: Any,
                ) -> FakeSentenceTransformer:
                    options_seen.append(dict(options))
                    return FakeSentenceTransformer()

                backend = SentenceTransformerBackend(
                    ModelProfile(
                        model_id="Qwen/Qwen3-Embedding-8B",
                        model_dir=self.model_dir.resolve(),
                        dim=dimension,
                        profile="qwen3-embedding-8b",
                    ),
                    model_factory=model_factory,
                )
                backend.encode(["query"], input_type="query")
                self.assertEqual(
                    dimension,
                    options_seen[0]["truncate_dim"],
                )

    def test_backend_is_created_once_for_persistent_embed_requests(self) -> None:
        backends: list[FakeBackend] = []

        def factory(profile: ModelProfile) -> FakeBackend:
            backend = FakeBackend(profile.dim)
            backends.append(backend)
            return backend

        _status, responses, _raw = self._run(
            EmbeddingWorker(backend_factory=factory),
            [
                self._init_request("init"),
                {
                    "id": "query",
                    "method": "embed",
                    "params": {"texts": ["q"], "input_type": "query"},
                },
                {
                    "id": "document",
                    "method": "embed",
                    "params": {"texts": ["d1", "d2"], "input_type": "document"},
                },
                {"id": "shutdown", "method": "shutdown"},
            ],
        )

        self.assertEqual(1, len(backends))
        self.assertEqual(
            [(["q"], "query"), (["d1", "d2"], "document")],
            backends[0].calls,
        )
        self.assertEqual("query", responses[1]["result"]["input_type"])
        self.assertEqual("document", responses[2]["result"]["input_type"])

    def test_profile_cannot_change_and_dimension_is_bounded(self) -> None:
        changed = self._init_request(
            "changed",
            model_id="Qwen/Qwen3-Embedding-8B",
            profile="qwen3-embedding-8b",
        )
        invalid_dim = self._init_request("too-wide", dim=4097)
        _status, responses, _raw = self._run(
            EmbeddingWorker(backend_factory=lambda profile: FakeBackend(profile.dim)),
            [
                self._init_request("first"),
                changed,
                invalid_dim,
                {"id": "shutdown", "method": "shutdown"},
            ],
        )

        self.assertTrue(responses[0]["ok"])
        self.assertEqual("ALREADY_INITIALIZED", responses[1]["error"]["code"])
        self.assertEqual("INVALID_PARAMS", responses[2]["error"]["code"])

    def test_invalid_model_directory_is_structured_and_does_not_load(self) -> None:
        factory_calls: list[ModelProfile] = []
        request = self._init_request("init")
        request["params"]["model_dir"] = "relative/model"
        _status, responses, _raw = self._run(
            EmbeddingWorker(
                backend_factory=lambda profile: (
                    factory_calls.append(profile) or FakeBackend(profile.dim)
                )
            ),
            [request, {"id": "shutdown", "method": "shutdown"}],
        )

        self.assertEqual("MODEL_DIRECTORY_INVALID", responses[0]["error"]["code"])
        self.assertEqual([], factory_calls)

    def test_invalid_json_and_backend_secret_are_redacted_without_killing_worker(
        self,
    ) -> None:
        secret = "Bearer SUPERSECRET-EMBEDDING-TOKEN"
        backend = FakeBackend(2, failure=RuntimeError(secret))
        _status, responses, raw = self._run(
            EmbeddingWorker(backend_factory=lambda _profile: backend),
            [
                b"{not json",
                self._init_request("init"),
                {
                    "id": "embed",
                    "method": "embed",
                    "params": {"texts": ["safe"], "input_type": "query"},
                },
                {"id": "health", "method": "health"},
                {"id": "shutdown", "method": "shutdown"},
            ],
        )

        self.assertEqual("INVALID_JSON", responses[0]["error"]["code"])
        self.assertEqual("EMBEDDING_FAILED", responses[2]["error"]["code"])
        self.assertTrue(responses[3]["ok"])
        self.assertNotIn(secret.encode("utf-8"), raw)
        self.assertNotIn(str(self.model_dir).encode("utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
