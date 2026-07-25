from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hia_core import embedding_contract as contract  # noqa: E402


class EmbeddingContractTests(unittest.TestCase):
    def test_official_profiles_and_default_selection_are_exact(self) -> None:
        self.assertEqual(
            {"qwen3-embedding-0.6b", "qwen3-embedding-8b"},
            set(contract.PROFILE_REGISTRY),
        )
        self.assertEqual(
            "qwen3-embedding-0.6b",
            contract.DEFAULT_EMBEDDING_PROFILE,
        )
        self.assertEqual(
            "qwen3-embedding-0.6b",
            contract.FALLBACK_EMBEDDING_PROFILE,
        )

        small = contract.PROFILE_REGISTRY["qwen3-embedding-0.6b"]
        self.assertEqual("Qwen/Qwen3-Embedding-0.6B", small.model_id)
        self.assertEqual("Apache-2.0", small.license)
        self.assertEqual("0.6B", small.parameter_scale)
        self.assertEqual(1.21, small.repository_size_gb)
        self.assertEqual(1024, small.max_dimension)
        self.assertEqual(1024, small.default_dimension)
        self.assertEqual(32768, small.context_length)
        self.assertEqual(
            ".runtime/models/qwen3-embedding/qwen3-embedding-0.6b",
            small.model_directory,
        )
        self.assertEqual(
            "HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B",
            small.model_dir_environment,
        )
        self.assertEqual(
            "HIA_EMBEDDING_MODEL_REVISION_QWEN3_0_6B",
            small.model_revision_environment,
        )

        large = contract.PROFILE_REGISTRY["qwen3-embedding-8b"]
        self.assertEqual("Qwen/Qwen3-Embedding-8B", large.model_id)
        self.assertEqual("Apache-2.0", large.license)
        self.assertEqual("8B", large.parameter_scale)
        self.assertEqual(15.2, large.repository_size_gb)
        self.assertEqual(4096, large.max_dimension)
        self.assertEqual(1024, large.default_dimension)
        self.assertEqual(32768, large.context_length)
        self.assertEqual(
            ".runtime/models/qwen3-embedding/qwen3-embedding-8b",
            large.model_directory,
        )
        self.assertEqual(
            "HIA_EMBEDDING_MODEL_DIR_QWEN3_8B",
            large.model_dir_environment,
        )
        self.assertEqual(
            "HIA_EMBEDDING_MODEL_REVISION_QWEN3_8B",
            large.model_revision_environment,
        )

        for profile in (small, large):
            self.assertEqual(32, profile.min_mrl_dimension)
            self.assertTrue(profile.model_directory.startswith(".runtime/models/"))

    def test_launcher_settings_environment_status_and_fallback_are_exact(
        self,
    ) -> None:
        public = contract.launcher_contract()
        self.assertEqual(1, public["contract_version"])
        self.assertEqual(
            "qwen3-embedding-0.6b",
            public["default_profile"],
        )
        self.assertEqual(
            "qwen3-embedding-0.6b",
            public["fallback_profile"],
        )
        self.assertEqual(
            {
                "profile": "embedding_profile",
                "dimension": "embedding_dimension",
                "device": "embedding_device",
            },
            public["settings"],
        )
        self.assertEqual(
            {
                "profile": "HIA_EMBEDDING_PROFILE",
                "python": "HIA_EMBEDDING_PYTHON",
                "dimension": "HIA_EMBEDDING_DIM",
                "device": "HIA_EMBEDDING_DEVICE",
                "model_0_6b": "HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B",
                "model_8b": "HIA_EMBEDDING_MODEL_DIR_QWEN3_8B",
                "revision_0_6b": (
                    "HIA_EMBEDDING_MODEL_REVISION_QWEN3_0_6B"
                ),
                "revision_8b": "HIA_EMBEDDING_MODEL_REVISION_QWEN3_8B",
            },
            public["environment"],
        )
        self.assertEqual(
            list(contract.EMBEDDING_STATUS_VALUES),
            public["status_values"],
        )
        self.assertEqual(
            list(contract.EMBEDDING_PUBLIC_STATUS_FIELDS),
            public["status_fields"],
        )
        self.assertEqual(
            [
                "contract_version",
                "status",
                "installed",
                "ready",
                "degraded",
                "requested_profile",
                "active_profile",
                "model_id",
                "model_revision",
                "dim",
                "normalized",
                "initialized",
                "loaded",
                "fallback_reason",
                "repair",
            ],
            public["status_fields"],
        )
        self.assertEqual(
            ["qwen3-embedding-0.6b", "lexical"],
            public["selection"]["selected_8b_fallback"],
        )
        self.assertEqual(
            ["lexical"],
            public["selection"]["selected_0_6b_fallback"],
        )
        self.assertTrue(public["selection"]["single_loaded_model"])
        self.assertFalse(
            public["selection"]["download_on_import_or_search"]
        )
        json.dumps(public, allow_nan=False)

    def test_runtime_layout_is_exact_and_confined_to_project(self) -> None:
        root = REPOSITORY_ROOT.resolve()
        layout = contract.runtime_layout(root)

        expected_relative = {
            "toolchain_root": ".runtime/toolchains/hia-embedding",
            "venv_root": ".runtime/toolchains/hia-embedding/venv",
            "worker_python": (
                ".runtime/toolchains/hia-embedding/venv/Scripts/python.exe"
            ),
            "models_root": ".runtime/models/qwen3-embedding",
            "model_0_6b": (
                ".runtime/models/qwen3-embedding/qwen3-embedding-0.6b"
            ),
            "model_8b": (
                ".runtime/models/qwen3-embedding/qwen3-embedding-8b"
            ),
            "cache_root": ".runtime/cache/embedding",
            "huggingface_cache": ".runtime/cache/embedding/huggingface",
            "transformers_cache": ".runtime/cache/embedding/transformers",
            "torch_cache": ".runtime/cache/embedding/torch",
            "temp_root": ".runtime/cache/embedding/tmp",
            "knowledge_database": ".runtime/knowledge/knowledge.sqlite3",
            "worker_source": "services/hia_mcp_v2/embedding_worker",
        }
        self.assertEqual(set(expected_relative), set(layout))
        for key, relative in expected_relative.items():
            expected = str((root / Path(relative)).resolve())
            self.assertEqual(expected, layout[key], key)
            self.assertTrue(Path(layout[key]).is_relative_to(root), key)

        runtime_root = (root / ".runtime").resolve()
        for key in set(layout) - {"worker_source"}:
            self.assertTrue(
                Path(layout[key]).is_relative_to(runtime_root),
                key,
            )

    def test_worker_contract_is_project_source_and_never_downloads(self) -> None:
        public = contract.launcher_contract()
        self.assertEqual(
            {
                "distribution": "hia_embedding_worker",
                "module": "hia_embedding_worker",
                "entry_point": "hia_embedding_worker",
                "protocol": "hia-embedding-stdio/1",
                "source_directory": "services/hia_mcp_v2/embedding_worker",
                "python": (
                    ".runtime/toolchains/hia-embedding/venv/"
                    "Scripts/python.exe"
                ),
            },
            public["worker"],
        )
        source = (
            REPOSITORY_ROOT / "src" / "hia_core" / "embedding_contract.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "snapshot_download(",
            "from_pretrained(",
            "urlopen(",
            "requests.get(",
        ):
            self.assertNotIn(forbidden, source)

    def test_knowledge_index_cli_contract_is_stable_and_launcher_owned(
        self,
    ) -> None:
        value = contract.launcher_contract()["knowledge_index"]
        self.assertEqual(
            {
                "module": "hia_mcp_runtime.knowledge_index_cli",
                "protocol": "hia-knowledge-index-jsonl/1",
                "python_role": "bridge_python",
                "python_args": [
                    "-B",
                    "-m",
                    "hia_mcp_runtime.knowledge_index_cli",
                ],
                "status_args": [
                    "--project-root",
                    "{project_root}",
                    "status",
                ],
                "build_args": [
                    "--project-root",
                    "{project_root}",
                    "build",
                    "--batch-size",
                    "{batch_size}",
                ],
                "python_path": [
                    "houdini_package/python_libs",
                    "src",
                ],
                "commands": ["status", "build"],
                "default_batch_size": 32,
                "max_batch_size": 64,
                "events": [
                    "start",
                    "progress",
                    "completed",
                    "error",
                ],
                "index_status_fields": [
                    "available",
                    "status",
                    "requested_profile",
                    "active_profile",
                    "profile_id",
                    "model_id",
                    "model_revision",
                    "dim",
                    "normalized",
                    "degraded",
                    "fallback_reason",
                    "repair",
                    "complete",
                    "partial",
                    "vector_chunks",
                    "total_chunks",
                    "pending_chunks",
                    "chunks_indexed_this_call",
                    "last_batch_count",
                ],
                "exit_codes": {
                    "success": 0,
                    "runtime_error": 1,
                    "invalid_arguments": 2,
                    "interrupted": 130,
                },
                "resume": "missing_or_changed_chunks",
                "downloads_models": False,
            },
            value,
        )


if __name__ == "__main__":
    unittest.main()
