"""Stable launcher/runtime contract for optional local Qwen embeddings.

This module is intentionally standard-library-only and performs no I/O.  The
launcher owns installation and selection; HIA MCP consumes the resulting
environment and reports the public status fields declared here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


EMBEDDING_CONTRACT_VERSION = 1
EMBEDDING_PROFILE_SETTING_KEY = "embedding_profile"
EMBEDDING_DIMENSION_SETTING_KEY = "embedding_dimension"
EMBEDDING_DEVICE_SETTING_KEY = "embedding_device"
DEFAULT_EMBEDDING_PROFILE = "qwen3-embedding-0.6b"
FALLBACK_EMBEDDING_PROFILE = "qwen3-embedding-0.6b"

EMBEDDING_PROFILE_ENVIRONMENT = "HIA_EMBEDDING_PROFILE"
EMBEDDING_PYTHON_ENVIRONMENT = "HIA_EMBEDDING_PYTHON"
EMBEDDING_DIMENSION_ENVIRONMENT = "HIA_EMBEDDING_DIM"
EMBEDDING_DEVICE_ENVIRONMENT = "HIA_EMBEDDING_DEVICE"
MODEL_DIR_0_6B_ENVIRONMENT = "HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B"
MODEL_DIR_8B_ENVIRONMENT = "HIA_EMBEDDING_MODEL_DIR_QWEN3_8B"
MODEL_REVISION_0_6B_ENVIRONMENT = (
    "HIA_EMBEDDING_MODEL_REVISION_QWEN3_0_6B"
)
MODEL_REVISION_8B_ENVIRONMENT = "HIA_EMBEDDING_MODEL_REVISION_QWEN3_8B"

WORKER_DISTRIBUTION = "hia_embedding_worker"
WORKER_MODULE = "hia_embedding_worker"
WORKER_CONSOLE_ENTRY_POINT = "hia_embedding_worker"
WORKER_SOURCE_DIRECTORY = "services/hia_mcp_v2/embedding_worker"
WORKER_PROTOCOL = "hia-embedding-stdio/1"

KNOWLEDGE_INDEX_CLI_MODULE = "hia_mcp_runtime.knowledge_index_cli"
KNOWLEDGE_INDEX_CLI_PROTOCOL = "hia-knowledge-index-jsonl/1"
KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE = 32
KNOWLEDGE_INDEX_MAX_BATCH_SIZE = 64
KNOWLEDGE_INDEX_CLI_EVENTS = (
    "start",
    "progress",
    "completed",
    "error",
)
KNOWLEDGE_INDEX_STATUS_FIELDS = (
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
)

EMBEDDING_TOOLCHAIN_ROOT = ".runtime/toolchains/hia-embedding"
EMBEDDING_VENV_ROOT = f"{EMBEDDING_TOOLCHAIN_ROOT}/venv"
EMBEDDING_PYTHON_WINDOWS = f"{EMBEDDING_VENV_ROOT}/Scripts/python.exe"
EMBEDDING_MODELS_ROOT = ".runtime/models/qwen3-embedding"
EMBEDDING_CACHE_ROOT = ".runtime/cache/embedding"
HUGGINGFACE_CACHE_ROOT = f"{EMBEDDING_CACHE_ROOT}/huggingface"
TRANSFORMERS_CACHE_ROOT = f"{EMBEDDING_CACHE_ROOT}/transformers"
TORCH_CACHE_ROOT = f"{EMBEDDING_CACHE_ROOT}/torch"
EMBEDDING_TEMP_ROOT = f"{EMBEDDING_CACHE_ROOT}/tmp"
KNOWLEDGE_DATABASE = ".runtime/knowledge/knowledge.sqlite3"

EMBEDDING_STATUS_VALUES = (
    "disabled",
    "missing",
    "installed",
    "configured",
    "loading",
    "ready",
    "degraded",
    "error",
)
EMBEDDING_PUBLIC_STATUS_FIELDS = (
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
)


@dataclass(frozen=True)
class EmbeddingProfileContract:
    profile_id: str
    label: str
    model_id: str
    license: str
    parameter_scale: str
    repository_size_gb: float
    weight_format: str
    max_dimension: int
    default_dimension: int
    min_mrl_dimension: int
    context_length: int
    multilingual: str
    model_directory: str
    model_dir_environment: str
    model_revision_environment: str


PROFILE_REGISTRY: Mapping[str, EmbeddingProfileContract] = {
    "qwen3-embedding-0.6b": EmbeddingProfileContract(
        profile_id="qwen3-embedding-0.6b",
        label="Qwen3 Embedding 0.6B",
        model_id="Qwen/Qwen3-Embedding-0.6B",
        license="Apache-2.0",
        parameter_scale="0.6B",
        repository_size_gb=1.21,
        weight_format="BF16",
        max_dimension=1024,
        default_dimension=1024,
        min_mrl_dimension=32,
        context_length=32768,
        multilingual="100+ languages",
        model_directory=(
            f"{EMBEDDING_MODELS_ROOT}/qwen3-embedding-0.6b"
        ),
        model_dir_environment=MODEL_DIR_0_6B_ENVIRONMENT,
        model_revision_environment=MODEL_REVISION_0_6B_ENVIRONMENT,
    ),
    "qwen3-embedding-8b": EmbeddingProfileContract(
        profile_id="qwen3-embedding-8b",
        label="Qwen3 Embedding 8B",
        model_id="Qwen/Qwen3-Embedding-8B",
        license="Apache-2.0",
        parameter_scale="8B",
        repository_size_gb=15.2,
        weight_format="BF16 sharded",
        max_dimension=4096,
        default_dimension=1024,
        min_mrl_dimension=32,
        context_length=32768,
        multilingual="100+ languages",
        model_directory=f"{EMBEDDING_MODELS_ROOT}/qwen3-embedding-8b",
        model_dir_environment=MODEL_DIR_8B_ENVIRONMENT,
        model_revision_environment=MODEL_REVISION_8B_ENVIRONMENT,
    ),
}


def runtime_layout(project_root: str | Path) -> dict[str, str]:
    """Return exact absolute paths without creating or validating them."""

    root = Path(project_root).resolve()

    def absolute(relative: str) -> str:
        return str((root / Path(relative)).resolve())

    return {
        "toolchain_root": absolute(EMBEDDING_TOOLCHAIN_ROOT),
        "venv_root": absolute(EMBEDDING_VENV_ROOT),
        "worker_python": absolute(EMBEDDING_PYTHON_WINDOWS),
        "models_root": absolute(EMBEDDING_MODELS_ROOT),
        "model_0_6b": absolute(
            PROFILE_REGISTRY[
                "qwen3-embedding-0.6b"
            ].model_directory
        ),
        "model_8b": absolute(
            PROFILE_REGISTRY["qwen3-embedding-8b"].model_directory
        ),
        "cache_root": absolute(EMBEDDING_CACHE_ROOT),
        "huggingface_cache": absolute(HUGGINGFACE_CACHE_ROOT),
        "transformers_cache": absolute(TRANSFORMERS_CACHE_ROOT),
        "torch_cache": absolute(TORCH_CACHE_ROOT),
        "temp_root": absolute(EMBEDDING_TEMP_ROOT),
        "knowledge_database": absolute(KNOWLEDGE_DATABASE),
        "worker_source": absolute(WORKER_SOURCE_DIRECTORY),
    }


def launcher_contract() -> dict[str, Any]:
    """Return the JSON-safe contract consumed by launcher integration."""

    return {
        "contract_version": EMBEDDING_CONTRACT_VERSION,
        "settings": {
            "profile": EMBEDDING_PROFILE_SETTING_KEY,
            "dimension": EMBEDDING_DIMENSION_SETTING_KEY,
            "device": EMBEDDING_DEVICE_SETTING_KEY,
        },
        "default_profile": DEFAULT_EMBEDDING_PROFILE,
        "fallback_profile": FALLBACK_EMBEDDING_PROFILE,
        "profiles": {
            key: asdict(value) for key, value in PROFILE_REGISTRY.items()
        },
        "worker": {
            "distribution": WORKER_DISTRIBUTION,
            "module": WORKER_MODULE,
            "entry_point": WORKER_CONSOLE_ENTRY_POINT,
            "protocol": WORKER_PROTOCOL,
            "source_directory": WORKER_SOURCE_DIRECTORY,
            "python": EMBEDDING_PYTHON_WINDOWS,
        },
        "knowledge_index": {
            "module": KNOWLEDGE_INDEX_CLI_MODULE,
            "protocol": KNOWLEDGE_INDEX_CLI_PROTOCOL,
            "python_role": "bridge_python",
            "python_args": [
                "-B",
                "-m",
                KNOWLEDGE_INDEX_CLI_MODULE,
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
            "default_batch_size": KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE,
            "max_batch_size": KNOWLEDGE_INDEX_MAX_BATCH_SIZE,
            "events": list(KNOWLEDGE_INDEX_CLI_EVENTS),
            "index_status_fields": list(KNOWLEDGE_INDEX_STATUS_FIELDS),
            "exit_codes": {
                "success": 0,
                "runtime_error": 1,
                "invalid_arguments": 2,
                "interrupted": 130,
            },
            "resume": "missing_or_changed_chunks",
            "downloads_models": False,
        },
        "environment": {
            "profile": EMBEDDING_PROFILE_ENVIRONMENT,
            "python": EMBEDDING_PYTHON_ENVIRONMENT,
            "dimension": EMBEDDING_DIMENSION_ENVIRONMENT,
            "device": EMBEDDING_DEVICE_ENVIRONMENT,
            "model_0_6b": MODEL_DIR_0_6B_ENVIRONMENT,
            "model_8b": MODEL_DIR_8B_ENVIRONMENT,
            "revision_0_6b": MODEL_REVISION_0_6B_ENVIRONMENT,
            "revision_8b": MODEL_REVISION_8B_ENVIRONMENT,
        },
        "status_values": list(EMBEDDING_STATUS_VALUES),
        "status_fields": list(EMBEDDING_PUBLIC_STATUS_FIELDS),
        "selection": {
            "single_loaded_model": True,
            "selected_8b_fallback": [
                "qwen3-embedding-0.6b",
                "lexical",
            ],
            "selected_0_6b_fallback": ["lexical"],
            "download_on_import_or_search": False,
        },
        "repair_actions": {
            "missing": "install",
            "damaged": "repair",
            "runtime": "repair_toolchain",
        },
    }


__all__ = [
    "DEFAULT_EMBEDDING_PROFILE",
    "EMBEDDING_CONTRACT_VERSION",
    "EMBEDDING_DEVICE_ENVIRONMENT",
    "EMBEDDING_DIMENSION_ENVIRONMENT",
    "EMBEDDING_PROFILE_ENVIRONMENT",
    "EMBEDDING_PYTHON_ENVIRONMENT",
    "EMBEDDING_PUBLIC_STATUS_FIELDS",
    "EMBEDDING_STATUS_VALUES",
    "EmbeddingProfileContract",
    "FALLBACK_EMBEDDING_PROFILE",
    "KNOWLEDGE_INDEX_CLI_EVENTS",
    "KNOWLEDGE_INDEX_CLI_MODULE",
    "KNOWLEDGE_INDEX_CLI_PROTOCOL",
    "KNOWLEDGE_INDEX_DEFAULT_BATCH_SIZE",
    "KNOWLEDGE_INDEX_MAX_BATCH_SIZE",
    "KNOWLEDGE_INDEX_STATUS_FIELDS",
    "MODEL_DIR_0_6B_ENVIRONMENT",
    "MODEL_DIR_8B_ENVIRONMENT",
    "MODEL_REVISION_0_6B_ENVIRONMENT",
    "MODEL_REVISION_8B_ENVIRONMENT",
    "PROFILE_REGISTRY",
    "WORKER_MODULE",
    "launcher_contract",
    "runtime_layout",
]
