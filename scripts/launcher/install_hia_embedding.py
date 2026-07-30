"""Install one contract-selected local embedding model.

The launcher invokes ``plan`` with a bootstrap Python, creates the dedicated
virtual environment, installs the embedding worker from its project source,
then invokes ``download`` with that environment's Python.  Importing this
module performs no filesystem writes, package installation, or download.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence


SUPPORTED_CONTRACT_VERSION = 1
MODEL_MANIFEST_NAME = ".hia-embedding-model.json"
STAGING_DIRECTORY_PREFIX = ".hia-embedding-staging-"
VENV_STAGING_DIRECTORY_PREFIX = ".venv-staging-"
VENV_MARKER_NAME = ".hia-managed-venv.json"
VENV_MARKER_SCHEMA = "hia-managed-python-venv/1"


class InstallerError(RuntimeError):
    """A safe, user-facing installer failure."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _project_root(raw_value: str) -> Path:
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        raise InstallerError("project root must be an absolute path")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("project root is unavailable") from exc
    if not root.is_dir():
        raise InstallerError("project root is not a directory")
    return root


def _load_contract(project_root: Path) -> ModuleType:
    contract_path = (
        project_root / "src" / "hia_core" / "embedding_contract.py"
    )
    try:
        resolved_contract = contract_path.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("embedding contract is unavailable") from exc
    if not _is_within(resolved_contract, project_root):
        raise InstallerError("embedding contract escapes the project root")

    module_name = "_hia_launcher_embedding_contract"
    spec = importlib.util.spec_from_file_location(module_name, resolved_contract)
    if spec is None or spec.loader is None:
        raise InstallerError("embedding contract could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise InstallerError("embedding contract could not be loaded") from exc
    return module


def _revision(raw_value: str | None) -> str:
    value = (raw_value or "main").strip()
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value) is None
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise InstallerError("model revision is invalid")
    return value


def _absolute_layout(
    project_root: Path,
    raw_layout: Mapping[str, Any],
) -> dict[str, str]:
    layout: dict[str, str] = {}
    for key, raw_path in raw_layout.items():
        if not isinstance(key, str) or not isinstance(raw_path, str):
            raise InstallerError("embedding runtime layout is invalid")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise InstallerError("embedding runtime layout is not absolute")
        resolved = candidate.resolve(strict=False)
        if not _is_within(resolved, project_root):
            raise InstallerError("embedding runtime layout escapes the project root")
        layout[key] = str(resolved)
    return layout


def _required_layout_path(layout: Mapping[str, str], key: str) -> Path:
    raw_value = layout.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise InstallerError(f"embedding runtime layout is missing {key}")
    return Path(raw_value)


def _child_environment(
    *,
    layout: Mapping[str, str],
    launcher_environment: Mapping[str, Any],
    profile: Any,
    profile_id: str,
    revision: str,
    model_dir: Path,
) -> tuple[dict[str, str], list[str], list[str]]:
    cache_root = _required_layout_path(layout, "cache_root")
    huggingface_cache = _required_layout_path(layout, "huggingface_cache")
    transformers_cache = _required_layout_path(layout, "transformers_cache")
    torch_cache = _required_layout_path(layout, "torch_cache")
    temp_root = _required_layout_path(layout, "temp_root")
    worker_python = _required_layout_path(layout, "worker_python")

    derived = {
        "pip": cache_root / "pip",
        "uv": cache_root / "uv",
        "home": cache_root / "home",
        "appdata_roaming": cache_root / "appdata" / "Roaming",
        "appdata_local": cache_root / "appdata" / "Local",
        "xdg": cache_root / "xdg",
        "python_user": cache_root / "python-user",
        "python_bytecode": cache_root / "python-bytecode",
        "torch_extensions": torch_cache / "extensions",
        "cuda": torch_cache / "cuda",
        "triton": torch_cache / "triton",
        "numba": cache_root / "numba",
        "matplotlib": cache_root / "matplotlib",
    }
    directories = {
        cache_root,
        huggingface_cache,
        transformers_cache,
        torch_cache,
        temp_root,
        _required_layout_path(layout, "toolchain_root"),
        _required_layout_path(layout, "models_root"),
        *derived.values(),
    }

    environment = {
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUSERBASE": str(derived["python_user"]),
        "PYTHONPYCACHEPREFIX": str(derived["python_bytecode"]),
        "PIP_CACHE_DIR": str(derived["pip"]),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PIP_REQUIRE_VIRTUALENV": "1",
        "UV_CACHE_DIR": str(derived["uv"]),
        "UV_NO_MODIFY_PATH": "1",
        "UV_NO_PROGRESS": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "HF_HOME": str(huggingface_cache),
        "HF_HUB_CACHE": str(huggingface_cache),
        "HUGGINGFACE_HUB_CACHE": str(huggingface_cache),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "0",
        "TRANSFORMERS_CACHE": str(transformers_cache),
        "TRANSFORMERS_OFFLINE": "0",
        "TORCH_HOME": str(torch_cache),
        "TORCH_EXTENSIONS_DIR": str(derived["torch_extensions"]),
        "CUDA_CACHE_PATH": str(derived["cuda"]),
        "TRITON_CACHE_DIR": str(derived["triton"]),
        "NUMBA_CACHE_DIR": str(derived["numba"]),
        "MPLCONFIGDIR": str(derived["matplotlib"]),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "TMPDIR": str(temp_root),
        "HOME": str(derived["home"]),
        "USERPROFILE": str(derived["home"]),
        "APPDATA": str(derived["appdata_roaming"]),
        "LOCALAPPDATA": str(derived["appdata_local"]),
        "XDG_CACHE_HOME": str(derived["xdg"]),
    }

    required_launcher_names = {
        "profile": profile_id,
        "python": str(worker_python),
    }
    for contract_key, value in required_launcher_names.items():
        environment_name = launcher_environment.get(contract_key)
        if not isinstance(environment_name, str) or not environment_name:
            raise InstallerError(
                f"embedding launcher environment is missing {contract_key}"
            )
        environment[environment_name] = value

    model_dir_environment = getattr(profile, "model_dir_environment", None)
    revision_environment = getattr(profile, "model_revision_environment", None)
    if (
        not isinstance(model_dir_environment, str)
        or not model_dir_environment
        or not isinstance(revision_environment, str)
        or not revision_environment
    ):
        raise InstallerError("embedding profile environment contract is invalid")
    environment[model_dir_environment] = str(model_dir)
    environment[revision_environment] = revision

    remove_environment = [
        "CONDA_PREFIX",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "PIP_CONFIG_FILE",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_INDEX_URL",
        "PIP_NO_INDEX",
        "PIP_PREFIX",
        "PIP_TARGET",
        "PIP_TRUSTED_HOST",
        "PIP_USER",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_CACHE_DIR",
        "UV_CONFIG_FILE",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_INSTALL_DIR",
        "UV_INDEX",
        "UV_INDEX_STRATEGY",
        "UV_INDEX_URL",
        "UV_INSECURE_HOST",
        "UV_NO_CONFIG",
        "UV_NO_INDEX",
        "UV_OFFLINE",
        "UV_PYTHON",
        "UV_PYTHON_BIN_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_TORCH_BACKEND",
        "UV_TOOL_BIN_DIR",
        "UV_UNMANAGED_INSTALL",
        "VIRTUAL_ENV",
    ]
    return (
        environment,
        sorted(str(path) for path in directories),
        remove_environment,
    )


def build_plan(
    *,
    project_root: Path,
    requested_profile: str | None,
    requested_revision: str | None,
) -> dict[str, Any]:
    contract = _load_contract(project_root)
    try:
        contract_version = contract.EMBEDDING_CONTRACT_VERSION
        registry = contract.PROFILE_REGISTRY
        default_profile = contract.DEFAULT_EMBEDDING_PROFILE
        launcher = contract.launcher_contract()
        raw_layout = contract.runtime_layout(project_root)
    except Exception as exc:
        raise InstallerError("embedding contract API is incomplete") from exc
    if (
        contract_version != SUPPORTED_CONTRACT_VERSION
        or launcher.get("contract_version") != contract_version
    ):
        raise InstallerError("unsupported embedding contract version")
    if not isinstance(registry, Mapping) or not registry:
        raise InstallerError("embedding profile registry is invalid")

    profile_id = (requested_profile or default_profile).strip()
    profile = registry.get(profile_id)
    if profile is None or getattr(profile, "profile_id", None) != profile_id:
        raise InstallerError("embedding profile is unsupported")
    revision = _revision(requested_revision)
    layout = _absolute_layout(project_root, raw_layout)

    model_relative = getattr(profile, "model_directory", None)
    model_id = getattr(profile, "model_id", None)
    repository_size_gb = getattr(profile, "repository_size_gb", None)
    if (
        not isinstance(model_relative, str)
        or not model_relative
        or not isinstance(model_id, str)
        or not model_id
        or not isinstance(repository_size_gb, (int, float))
        or repository_size_gb <= 0
    ):
        raise InstallerError("embedding profile contract is invalid")
    model_dir = (project_root / model_relative).resolve(strict=False)
    models_root = _required_layout_path(layout, "models_root")
    if not _is_within(model_dir, models_root):
        raise InstallerError("embedding model directory is outside models root")
    if str(model_dir) not in set(layout.values()):
        raise InstallerError("embedding model directory disagrees with runtime layout")

    launcher_profiles = launcher.get("profiles")
    launcher_environment = launcher.get("environment")
    launcher_worker = launcher.get("worker")
    if (
        not isinstance(launcher_profiles, Mapping)
        or not isinstance(launcher_environment, Mapping)
        or not isinstance(launcher_worker, Mapping)
    ):
        raise InstallerError("embedding launcher contract is invalid")
    serialized_profile = launcher_profiles.get(profile_id)
    if (
        not isinstance(serialized_profile, Mapping)
        or serialized_profile.get("model_id") != model_id
        or serialized_profile.get("model_directory") != model_relative
        or serialized_profile.get("repository_size_gb") != repository_size_gb
    ):
        raise InstallerError("embedding profile registry disagrees with launcher contract")

    worker_source = _required_layout_path(layout, "worker_source")
    worker_python = _required_layout_path(layout, "worker_python")
    venv_root = _required_layout_path(layout, "venv_root")
    if worker_python.parent.parent != venv_root:
        raise InstallerError("embedding worker Python disagrees with venv layout")
    if not worker_source.is_dir() or not (worker_source / "pyproject.toml").is_file():
        raise InstallerError("embedding worker source is unavailable")
    worker_distribution = launcher_worker.get("distribution")
    if not isinstance(worker_distribution, str) or not worker_distribution:
        raise InstallerError("embedding worker distribution is invalid")

    child_environment, directories, remove_environment = _child_environment(
        layout=layout,
        launcher_environment=launcher_environment,
        profile=profile,
        profile_id=profile_id,
        revision=revision,
        model_dir=model_dir,
    )
    return {
        "contract_version": contract_version,
        "profile_id": profile_id,
        "model_id": model_id,
        "revision": revision,
        "repository_size_gb": float(repository_size_gb),
        "model_dir": str(model_dir),
        "manifest_path": str(model_dir / MODEL_MANIFEST_NAME),
        "layout": layout,
        "worker_distribution": worker_distribution,
        "dimension": int(getattr(profile, "default_dimension")),
        "child_environment": child_environment,
        "required_directories": directories,
        "remove_environment": remove_environment,
    }


def smoke_selected_model(
    plan: Mapping[str, Any],
    *,
    staging_install_id: str,
    device: str,
) -> dict[str, Any]:
    _assert_download_environment(
        plan,
        staging_install_id=staging_install_id,
    )
    if device not in {"cpu", "cuda"}:
        raise InstallerError("embedding smoke device is invalid")
    model_dir = Path(str(plan["model_dir"]))
    expected_manifest = _manifest_payload(plan)
    if (
        not _is_ordinary_directory(model_dir)
        or not _read_matching_manifest(
            model_dir / MODEL_MANIFEST_NAME,
            expected_manifest,
        )
        or not _model_payload_is_complete(model_dir)
    ):
        raise InstallerError("embedding smoke model payload is incomplete")
    dimension = plan.get("dimension")
    if (
        isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or not 1 <= dimension <= 4096
    ):
        raise InstallerError("embedding smoke dimension is invalid")
    try:
        from hia_embedding_worker.worker import EmbeddingWorker
    except Exception as exc:
        raise InstallerError("embedding worker smoke entry is unavailable") from exc

    worker = EmbeddingWorker()
    initialized, _stop = worker.handle(
        {
            "id": "installer-smoke-init",
            "method": "init",
            "params": {
                "model_id": plan["model_id"],
                "model_dir": str(model_dir),
                "dim": dimension,
                "profile": plan["profile_id"],
                "model_revision": plan["revision"],
                "device": device,
            },
        }
    )
    if initialized.get("ok") is not True:
        raise InstallerError("embedding worker smoke initialization failed")
    encoded, _stop = worker.handle(
        {
            "id": "installer-smoke-embed",
            "method": "embed",
            "params": {
                "texts": ["HIA local embedding smoke"],
                "input_type": "document",
            },
        }
    )
    if encoded.get("ok") is not True:
        raise InstallerError("embedding worker smoke encode failed")
    result = encoded.get("result")
    if not isinstance(result, Mapping):
        raise InstallerError("embedding worker smoke result is invalid")
    vectors = result.get("vectors")
    if (
        result.get("profile") != plan["profile_id"]
        or result.get("model_id") != plan["model_id"]
        or result.get("model_revision") != plan["revision"]
        or result.get("device") != device
        or result.get("dim") != dimension
        or result.get("normalized") is not True
        or result.get("count") != 1
        or not isinstance(vectors, list)
        or len(vectors) != 1
        or not isinstance(vectors[0], list)
        or len(vectors[0]) != dimension
    ):
        raise InstallerError("embedding worker smoke contract mismatch")
    try:
        numeric = [float(value) for value in vectors[0]]
    except (TypeError, ValueError) as exc:
        raise InstallerError("embedding worker smoke vector is invalid") from exc
    if not all(math.isfinite(value) for value in numeric):
        raise InstallerError("embedding worker smoke vector is non-finite")
    norm = math.sqrt(sum(value * value for value in numeric))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-4:
        raise InstallerError("embedding worker smoke vector is not normalized")
    return {
        "status": "ready",
        **expected_manifest,
        "model_dir": str(model_dir),
        "dimension": dimension,
        "device": device,
        "norm": norm,
    }


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _validated_staging_worker(
    plan: Mapping[str, Any],
    install_id: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", install_id) is None:
        raise InstallerError("staged embedding venv install id is invalid")
    layout = plan.get("layout")
    if not isinstance(layout, Mapping):
        raise InstallerError("embedding runtime layout is invalid")
    toolchain_root = _required_layout_path(layout, "toolchain_root")
    try:
        resolved_toolchain = toolchain_root.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("embedding toolchain root is unavailable") from exc
    if not _is_ordinary_directory(resolved_toolchain):
        raise InstallerError("embedding toolchain root is unsafe")

    staging_root = toolchain_root / (
        f"{VENV_STAGING_DIRECTORY_PREFIX}{install_id}"
    )
    try:
        resolved_staging = staging_root.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("staged embedding venv is unavailable") from exc
    lexical_staging = Path(os.path.abspath(os.fspath(staging_root)))
    if (
        os.path.normcase(str(lexical_staging))
        != os.path.normcase(str(resolved_staging))
        or resolved_staging.parent != resolved_toolchain
        or not _is_ordinary_directory(resolved_staging)
    ):
        raise InstallerError("staged embedding venv is unsafe")

    marker_path = resolved_staging / VENV_MARKER_NAME
    if not _is_ordinary_file(marker_path):
        raise InstallerError("staged embedding venv marker is unavailable")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallerError("staged embedding venv marker is invalid") from exc
    if (
        not isinstance(marker, dict)
        or marker.get("schema") != VENV_MARKER_SCHEMA
        or marker.get("role") != "hia-embedding"
        or marker.get("install_id") != install_id
        or marker.get("python_version") != "3.10.11"
        or not isinstance(marker.get("managed_python"), str)
        or not marker["managed_python"]
        or Path(marker["managed_python"]).is_absolute()
        or ".." in Path(marker["managed_python"]).parts
    ):
        raise InstallerError("staged embedding venv marker does not match")

    worker_python = resolved_staging / "Scripts" / "python.exe"
    if not _is_ordinary_file(worker_python):
        raise InstallerError("staged embedding worker Python is unavailable")
    return worker_python


def _assert_download_environment(
    plan: Mapping[str, Any],
    *,
    staging_install_id: str | None = None,
) -> None:
    raw_environment = plan.get("child_environment")
    if not isinstance(raw_environment, Mapping):
        raise InstallerError("installer environment plan is invalid")
    canonical_worker = _required_layout_path(plan["layout"], "worker_python")
    worker_python = (
        _validated_staging_worker(plan, staging_install_id)
        if staging_install_id is not None
        else canonical_worker
    )
    saw_worker_environment = False
    for name, expected_value in raw_environment.items():
        selected_value = str(expected_value)
        if (
            Path(selected_value).is_absolute()
            and _same_path(Path(selected_value), canonical_worker)
        ):
            selected_value = str(worker_python)
            saw_worker_environment = True
        if os.environ.get(str(name)) != selected_value:
            raise InstallerError(
                f"installer child environment is not isolated: {name}"
            )
    if not saw_worker_environment:
        raise InstallerError("installer environment plan has no worker Python")

    if not _same_path(Path(sys.executable), worker_python):
        raise InstallerError("download must run in the dedicated embedding venv")
    try:
        importlib.metadata.distribution(str(plan["worker_distribution"]))
    except importlib.metadata.PackageNotFoundError as exc:
        raise InstallerError("embedding worker is not installed in its venv") from exc


def _manifest_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": plan["contract_version"],
        "profile_id": plan["profile_id"],
        "model_id": plan["model_id"],
        "revision": plan["revision"],
    }


def _is_ordinary_file(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return (
        path.is_file()
        and not path.is_symlink()
        and not attributes & 0x0400
    )


def _is_ordinary_directory(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return (
        path.is_dir()
        and not path.is_symlink()
        and not attributes & 0x0400
    )


def _read_matching_manifest(
    manifest_path: Path,
    expected: Mapping[str, Any],
) -> bool:
    if not _is_ordinary_file(manifest_path):
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallerError("embedding model manifest is invalid") from exc
    if not isinstance(payload, dict) or payload != dict(expected):
        raise InstallerError("embedding model manifest does not match the selection")
    return True


def _model_payload_is_complete(model_dir: Path) -> bool:
    return _is_ordinary_file(model_dir / "config.json") and any(
        _is_ordinary_file(path) for path in model_dir.glob("*.safetensors")
    )


def _downloaded_snapshot_payload_is_complete(
    snapshot_dir: Path,
    cache_root: Path,
) -> bool:
    try:
        resolved_cache_root = cache_root.resolve(strict=True)
    except OSError:
        return False

    def is_safe_snapshot_file(path: Path) -> bool:
        try:
            resolved_source = path.resolve(strict=True)
        except OSError:
            return False
        return (
            _is_within(resolved_source, resolved_cache_root)
            and _is_ordinary_file(resolved_source)
        )

    return is_safe_snapshot_file(snapshot_dir / "config.json") and any(
        is_safe_snapshot_file(path)
        for path in snapshot_dir.glob("*.safetensors")
    )


def _copy_snapshot_tree(
    snapshot_dir: Path,
    destination_root: Path,
    cache_root: Path,
) -> None:
    for source in sorted(snapshot_dir.rglob("*")):
        relative = source.relative_to(snapshot_dir)
        destination = destination_root / relative
        if source.is_dir() and not source.is_symlink():
            destination.mkdir(exist_ok=False)
            continue

        try:
            resolved_source = source.resolve(strict=True)
        except OSError as exc:
            raise InstallerError("downloaded embedding snapshot is invalid") from exc
        if not _is_within(resolved_source, cache_root):
            raise InstallerError("downloaded embedding snapshot escapes its cache")
        if not resolved_source.is_file():
            raise InstallerError("downloaded embedding snapshot contains invalid data")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(resolved_source, destination)
        except FileExistsError as exc:
            raise InstallerError(
                "embedding model materialization would overwrite a file"
            ) from exc
        except OSError:
            try:
                with resolved_source.open("rb") as source_stream:
                    with destination.open("xb") as destination_stream:
                        shutil.copyfileobj(
                            source_stream,
                            destination_stream,
                            length=1024 * 1024,
                        )
                shutil.copystat(
                    resolved_source,
                    destination,
                    follow_symlinks=True,
                )
            except FileExistsError as exc:
                raise InstallerError(
                    "embedding model materialization would overwrite a file"
                ) from exc
            except OSError as exc:
                raise InstallerError(
                    "embedding model could not be materialized"
                ) from exc


def _create_staging_directory(models_root: Path) -> Path:
    try:
        resolved_models_root = models_root.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("embedding models root is unavailable") from exc
    if not resolved_models_root.is_dir():
        raise InstallerError("embedding models root is not a directory")

    for _attempt in range(8):
        staging = resolved_models_root / (
            f"{STAGING_DIRECTORY_PREFIX}{uuid.uuid4().hex}"
        )
        try:
            staging.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        except OSError as exc:
            raise InstallerError(
                "embedding model staging directory could not be created"
            ) from exc
        return staging
    raise InstallerError("embedding model staging directory is unavailable")


def _validated_staging_directory(
    staging_dir: Path,
    models_root: Path,
) -> Path:
    try:
        resolved_models_root = models_root.resolve(strict=True)
        resolved_staging = staging_dir.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("embedding model staging directory is unavailable") from exc
    lexical_staging = Path(os.path.abspath(os.fspath(staging_dir)))
    if (
        os.path.normcase(str(lexical_staging))
        != os.path.normcase(str(resolved_staging))
        or resolved_staging.parent != resolved_models_root
        or re.fullmatch(
            re.escape(STAGING_DIRECTORY_PREFIX) + r"[0-9a-f]{32}",
            resolved_staging.name,
        )
        is None
        or not resolved_staging.is_dir()
    ):
        raise InstallerError("embedding model staging directory is unsafe")
    return resolved_staging


def _cleanup_staging_directory(
    staging_dir: Path,
    models_root: Path,
) -> None:
    if not staging_dir.exists():
        return
    validated = _validated_staging_directory(staging_dir, models_root)
    shutil.rmtree(validated)


def _materialize_snapshot(
    snapshot_dir: Path,
    model_dir: Path,
    cache_root: Path,
    models_root: Path,
    manifest_payload: Mapping[str, Any],
) -> None:
    try:
        resolved_models_root = models_root.resolve(strict=True)
    except OSError as exc:
        raise InstallerError("embedding models root is unavailable") from exc
    if model_dir.parent.resolve(strict=False) != resolved_models_root:
        raise InstallerError("embedding model directory is not a direct models child")
    if model_dir.exists():
        raise InstallerError("embedding model target already exists")

    staging_dir = _create_staging_directory(resolved_models_root)
    published = False
    try:
        _copy_snapshot_tree(snapshot_dir, staging_dir, cache_root)
        if not _model_payload_is_complete(staging_dir):
            raise InstallerError("materialized embedding model payload is incomplete")

        staging_manifest = staging_dir / MODEL_MANIFEST_NAME
        _write_staged_manifest(staging_manifest, manifest_payload)
        if (
            not _read_matching_manifest(staging_manifest, manifest_payload)
            or not _model_payload_is_complete(staging_dir)
        ):
            raise InstallerError("staged embedding model validation failed")

        validated_staging = _validated_staging_directory(
            staging_dir,
            resolved_models_root,
        )
        if model_dir.exists():
            raise InstallerError("embedding model target already exists")
        try:
            os.rename(validated_staging, model_dir)
        except FileExistsError as exc:
            raise InstallerError("embedding model target already exists") from exc
        except OSError as exc:
            raise InstallerError("embedding model could not be published") from exc
        published = True
    except BaseException:
        if not published:
            _cleanup_staging_directory(staging_dir, resolved_models_root)
        raise


def _write_staged_manifest(
    manifest_path: Path,
    payload: Mapping[str, Any],
) -> None:
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            manifest_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    except FileExistsError as exc:
        raise InstallerError("embedding model manifest already exists") from exc
    except OSError as exc:
        raise InstallerError("embedding model manifest could not be written") from exc


def download_selected_model(
    plan: Mapping[str, Any],
    *,
    staging_install_id: str | None = None,
) -> dict[str, Any]:
    _assert_download_environment(
        plan,
        staging_install_id=staging_install_id,
    )
    model_dir = Path(str(plan["model_dir"]))
    manifest_path = Path(str(plan["manifest_path"]))
    expected_manifest = _manifest_payload(plan)
    expected_manifest_path = model_dir / MODEL_MANIFEST_NAME
    if not _same_path(manifest_path, expected_manifest_path):
        raise InstallerError("embedding model manifest path is not canonical")

    if model_dir.exists():
        if (
            _is_ordinary_directory(model_dir)
            and _read_matching_manifest(manifest_path, expected_manifest)
            and _model_payload_is_complete(model_dir)
        ):
            return {
                "status": "already_installed",
                **expected_manifest,
                "model_dir": str(model_dir),
                "manifest_path": str(manifest_path),
            }
        raise InstallerError("embedding model target contains unverified existing data")

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        raise InstallerError(
            "huggingface_hub is unavailable in the embedding venv"
        ) from exc

    try:
        downloaded_path = snapshot_download(
            repo_id=str(plan["model_id"]),
            repo_type="model",
            revision=str(plan["revision"]),
            cache_dir=str(_required_layout_path(plan["layout"], "huggingface_cache")),
            token=False,
        )
    except Exception as exc:
        raise InstallerError("selected embedding model download failed") from exc
    downloaded_snapshot = Path(downloaded_path).resolve(strict=False)
    huggingface_cache = _required_layout_path(plan["layout"], "huggingface_cache")
    if not _is_within(downloaded_snapshot, huggingface_cache):
        raise InstallerError("embedding model download escaped its canonical cache")
    if (
        not downloaded_snapshot.is_dir()
        or not _downloaded_snapshot_payload_is_complete(
            downloaded_snapshot,
            huggingface_cache,
        )
    ):
        raise InstallerError("downloaded embedding model payload is incomplete")

    models_root = _required_layout_path(plan["layout"], "models_root")
    _materialize_snapshot(
        downloaded_snapshot,
        model_dir,
        huggingface_cache,
        models_root,
        expected_manifest,
    )
    return {
        "status": "installed",
        **expected_manifest,
        "model_dir": str(model_dir),
        "manifest_path": str(manifest_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install one project-local HIA embedding model."
    )
    parser.add_argument(
        "--action",
        choices=("plan", "download", "smoke"),
        required=True,
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--revision")
    parser.add_argument("--staging-install-id")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        plan = build_plan(
            project_root=_project_root(arguments.project_root),
            requested_profile=arguments.profile,
            requested_revision=arguments.revision,
        )
        if arguments.action == "plan":
            result = plan
        elif arguments.action == "download":
            result = download_selected_model(
                plan,
                staging_install_id=arguments.staging_install_id,
            )
        else:
            if not arguments.staging_install_id or not arguments.device:
                raise InstallerError(
                    "embedding smoke requires staging install id and device"
                )
            result = smoke_selected_model(
                plan,
                staging_install_id=arguments.staging_install_id,
                device=arguments.device,
            )
    except InstallerError as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
