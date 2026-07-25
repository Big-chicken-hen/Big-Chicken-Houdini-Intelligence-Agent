"""Standard-library client for the project-local embedding worker.

The Houdini process never imports an ML runtime.  This module starts one
pre-installed worker with an isolated Python interpreter, communicates over
bounded JSONL stdio, and validates every returned vector before exposing it to
the lexical knowledge index.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from hia_core.embedding_contract import (
    DEFAULT_EMBEDDING_PROFILE,
    EMBEDDING_CONTRACT_VERSION,
    EMBEDDING_DEVICE_ENVIRONMENT,
    EMBEDDING_DIMENSION_ENVIRONMENT,
    EMBEDDING_PROFILE_ENVIRONMENT,
    EMBEDDING_PYTHON_ENVIRONMENT,
    FALLBACK_EMBEDDING_PROFILE,
    MODEL_DIR_0_6B_ENVIRONMENT,
    MODEL_DIR_8B_ENVIRONMENT,
    MODEL_REVISION_0_6B_ENVIRONMENT,
    MODEL_REVISION_8B_ENVIRONMENT,
    PROFILE_REGISTRY,
    WORKER_MODULE,
    EmbeddingProfileContract,
    runtime_layout,
)

DEFAULT_PROFILE = DEFAULT_EMBEDDING_PROFILE
PROFILE_ENVIRONMENT = EMBEDDING_PROFILE_ENVIRONMENT
PYTHON_ENVIRONMENT = EMBEDDING_PYTHON_ENVIRONMENT
DIMENSION_ENVIRONMENT = EMBEDDING_DIMENSION_ENVIRONMENT
DEVICE_ENVIRONMENT = EMBEDDING_DEVICE_ENVIRONMENT
EmbeddingProfile = EmbeddingProfileContract
PROFILES = PROFILE_REGISTRY

MAX_BATCH_TEXTS = 64
MAX_TEXT_CHARACTERS = 32 * 1024
MAX_BATCH_CHARACTERS = 256 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_CHARACTERS = 64 * 1024 * 1024
MAX_STDERR_CHARACTERS = 8192
DEFAULT_TIMEOUT_SECONDS = 60.0
SHUTDOWN_GRACE_SECONDS = 1.0
TERMINATE_GRACE_SECONDS = 1.0
NORMALIZATION_TOLERANCE = 0.005

_EMBEDDING_ENVIRONMENT_NAMES = frozenset(
    {
        PROFILE_ENVIRONMENT,
        PYTHON_ENVIRONMENT,
        DIMENSION_ENVIRONMENT,
        DEVICE_ENVIRONMENT,
        MODEL_DIR_0_6B_ENVIRONMENT,
        MODEL_DIR_8B_ENVIRONMENT,
        MODEL_REVISION_0_6B_ENVIRONMENT,
        MODEL_REVISION_8B_ENVIRONMENT,
        "HIA_EMBEDDING_MODEL_DIR",
        "HIA_EMBEDDING_MODEL_ID",
    }
)
_FALLBACK_PROFILE = FALLBACK_EMBEDDING_PROFILE
_FALLBACK_ERROR_CODES = frozenset(
    {
        "EMBEDDING_TIMEOUT",
        "EMBEDDING_FAILED",
        "EMBEDDING_WORKER_CLOSED",
        "EMBEDDING_WORKER_START_FAILED",
        "MODEL_DIRECTORY_INVALID",
        "MODEL_LOAD_FAILED",
        "MODEL_RUNTIME_UNAVAILABLE",
    }
)
_STDOUT_EOF = object()


class EmbeddingClientError(RuntimeError):
    """Stable worker/client error suitable for lexical fallback."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

class EmbeddingConfigurationError(EmbeddingClientError):
    """Raised when a partially configured runtime is unsafe or unusable."""


@dataclass(frozen=True)
class EmbeddingBatch:
    """One validated document/query embedding batch."""

    document_vectors: tuple[tuple[float, ...], ...]
    query_vectors: tuple[tuple[float, ...], ...]
    model_id: str
    model_revision: str
    profile_id: str
    dim: int
    normalized: bool
    requested_profile: str
    status: str
    fallback_reason: str | None
    repair: Mapping[str, Any] | None


class EmbeddingClient:
    """One persistent, serialized JSONL connection to an embedding worker."""

    def __init__(
        self,
        *,
        project_root: Path,
        python_path: Path,
        requested_profile: EmbeddingProfile,
        active_profile: EmbeddingProfile,
        model_directories: Mapping[str, Path | None],
        model_revisions: Mapping[str, str],
        requested_dim: int,
        active_dim: int,
        device: str,
        timeout_seconds: float,
        fallback_reason: str | None = None,
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        self._project_root = project_root
        self._python_path = python_path
        self._requested_profile = requested_profile
        self._active_profile = active_profile
        self._model_directories = dict(model_directories)
        self._model_revisions = dict(model_revisions)
        self._requested_dim = requested_dim
        self._active_dim = active_dim
        self._device = device
        self._timeout_seconds = timeout_seconds
        self._fallback_reason = fallback_reason
        self._repair = dict(repair) if repair else None
        self._fallback_attempted = active_profile.profile_id != requested_profile.profile_id

        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[object] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lock = threading.Lock()
        self._stderr_parts: deque[str] = deque()
        self._stderr_characters = 0
        self._next_request_id = 1
        self._closed = False
        self._initialized = False
        self._loaded = False
        self._status = (
            "degraded"
            if active_profile.profile_id != requested_profile.profile_id
            else "configured"
        )

    @classmethod
    def from_environment(
        cls,
        project_root: str | os.PathLike[str],
        *,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> EmbeddingClient | None:
        """Build a client without starting it; no embedding variables means off."""

        values = dict(os.environ if environ is None else environ)
        if not any(name in values for name in _EMBEDDING_ENVIRONMENT_NAMES):
            return None
        if "HIA_EMBEDDING_MODEL_DIR" in values or "HIA_EMBEDDING_MODEL_ID" in values:
            raise EmbeddingConfigurationError(
                "EMBEDDING_CONFIGURATION_INVALID",
                "Use one supported HIA embedding profile and its profile-specific model directory",
                {
                    "profile_environment": PROFILE_ENVIRONMENT,
                    "supported_profiles": sorted(PROFILES),
                },
            )
        try:
            timeout_value = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise EmbeddingConfigurationError(
                "EMBEDDING_CONFIGURATION_INVALID",
                "The embedding worker timeout must be positive",
            ) from exc
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_value)
            or timeout_value <= 0.0
        ):
            raise EmbeddingConfigurationError(
                "EMBEDDING_CONFIGURATION_INVALID",
                "The embedding worker timeout must be positive",
            )

        root = Path(project_root).resolve()
        layout = runtime_layout(root)
        python_path = _required_runtime_file(
            values.get(PYTHON_ENVIRONMENT),
            field=PYTHON_ENVIRONMENT,
            allowed_root=Path(layout["toolchain_root"]),
        )

        raw_profile = values.get(PROFILE_ENVIRONMENT, DEFAULT_PROFILE).strip()
        requested_profile = PROFILES.get(raw_profile)
        if requested_profile is None:
            raise EmbeddingConfigurationError(
                "EMBEDDING_PROFILE_UNSUPPORTED",
                "The requested embedding profile is unsupported",
                {
                    "requested_profile": raw_profile,
                    "supported_profiles": sorted(PROFILES),
                },
            )
        requested_dim = _dimension(
            values.get(DIMENSION_ENVIRONMENT),
            requested_profile,
        )
        device = _device(values.get(DEVICE_ENVIRONMENT))

        models_root = Path(layout["models_root"])
        model_directories: dict[str, Path | None] = {}
        model_revisions: dict[str, str] = {}
        configured_model_paths: dict[str, bool] = {}
        for profile in PROFILES.values():
            raw_path = values.get(profile.model_dir_environment)
            configured_model_paths[profile.profile_id] = raw_path is not None
            model_directories[profile.profile_id] = _optional_runtime_directory(
                raw_path,
                field=profile.model_dir_environment,
                allowed_root=models_root,
            )
            model_revisions[profile.profile_id] = _model_revision(
                values.get(profile.model_revision_environment),
                field=profile.model_revision_environment,
            )

        active_profile = requested_profile
        active_dim = requested_dim
        fallback_reason: str | None = None
        repair: Mapping[str, Any] | None = None
        if model_directories[requested_profile.profile_id] is None:
            if (
                requested_profile.profile_id == "qwen3-embedding-8b"
                and model_directories[_FALLBACK_PROFILE] is not None
            ):
                active_profile = PROFILES[_FALLBACK_PROFILE]
                active_dim = min(
                    requested_dim,
                    active_profile.max_dimension,
                )
                fallback_reason = "REQUESTED_MODEL_UNAVAILABLE"
                repair = _repair_details(requested_profile)
            else:
                raise EmbeddingConfigurationError(
                    "EMBEDDING_MODEL_UNAVAILABLE",
                    "The selected local embedding model is unavailable",
                    {
                        "requested_profile": requested_profile.profile_id,
                        "model_dir_environment": (
                            requested_profile.model_dir_environment
                        ),
                        "configured": configured_model_paths[
                            requested_profile.profile_id
                        ],
                    },
                )

        return cls(
            project_root=root,
            python_path=python_path,
            requested_profile=requested_profile,
            active_profile=active_profile,
            model_directories=model_directories,
            model_revisions=model_revisions,
            requested_dim=requested_dim,
            active_dim=active_dim,
            device=device,
            timeout_seconds=timeout_value,
            fallback_reason=fallback_reason,
            repair=repair,
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "contract_version": EMBEDDING_CONTRACT_VERSION,
                "status": self._status,
                "installed": (
                    self._python_path.is_file()
                    and self._model_directories.get(
                        self._active_profile.profile_id
                    )
                    is not None
                ),
                "ready": self._loaded and not self._closed,
                "degraded": bool(self._fallback_reason),
                "requested_profile": self._requested_profile.profile_id,
                "active_profile": self._active_profile.profile_id,
                "model_id": self._active_model_id(),
                "model_revision": self._active_revision(),
                "dim": self._active_dim,
                "normalized": True,
                "initialized": self._initialized,
                "loaded": self._loaded,
                "fallback_reason": self._fallback_reason,
                "repair": dict(self._repair) if self._repair else None,
            }

    def encode(
        self,
        *,
        documents: Sequence[str],
        queries: Sequence[str],
    ) -> EmbeddingBatch:
        document_values, query_values = _validated_texts(documents, queries)
        with self._lock:
            if self._closed:
                raise EmbeddingClientError(
                    "EMBEDDING_CLIENT_CLOSED",
                    "The embedding client is closed",
                )
            if not document_values and not query_values:
                return self._batch((), ())
            try:
                document_vectors, query_vectors = self._encode_current(
                    document_values,
                    query_values,
                )
            except EmbeddingClientError as exc:
                self._stop_process(graceful=False)
                if self._may_fallback(exc):
                    self._activate_fallback(exc)
                    try:
                        document_vectors, query_vectors = self._encode_current(
                            document_values,
                            query_values,
                        )
                    except EmbeddingClientError as fallback_error:
                        self._status = "error"
                        self._stop_process(graceful=False)
                        raise fallback_error
                else:
                    self._status = "error"
                    raise
            self._status = "degraded" if self._fallback_reason else "ready"
            self._loaded = True
            return self._batch(document_vectors, query_vectors)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._stop_process(graceful=True)
            self._closed = True
            self._status = "disabled"

    def _batch(
        self,
        document_vectors: Sequence[Sequence[float]],
        query_vectors: Sequence[Sequence[float]],
    ) -> EmbeddingBatch:
        return EmbeddingBatch(
            document_vectors=tuple(tuple(vector) for vector in document_vectors),
            query_vectors=tuple(tuple(vector) for vector in query_vectors),
            model_id=self._active_model_id(),
            model_revision=self._active_revision(),
            profile_id=self._active_profile.profile_id,
            dim=self._active_dim,
            normalized=True,
            requested_profile=self._requested_profile.profile_id,
            status="degraded" if self._fallback_reason else self._status,
            fallback_reason=self._fallback_reason,
            repair=dict(self._repair) if self._repair else None,
        )

    def _encode_current(
        self,
        documents: list[str],
        queries: list[str],
    ) -> tuple[tuple[tuple[float, ...], ...], tuple[tuple[float, ...], ...]]:
        self._ensure_started()
        document_vectors: tuple[tuple[float, ...], ...] = ()
        query_vectors: tuple[tuple[float, ...], ...] = ()
        if documents:
            document_vectors = self._embed_group(documents, "document")
        if queries:
            query_vectors = self._embed_group(queries, "query")
        return document_vectors, query_vectors

    def _ensure_started(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            return
        self._stop_process(graceful=False)
        model_dir = self._model_directories.get(
            self._active_profile.profile_id
        )
        if model_dir is None:
            raise EmbeddingClientError(
                "EMBEDDING_MODEL_UNAVAILABLE",
                "The active local embedding model is unavailable",
            )

        command = [
            str(self._python_path),
            "-I",
            "-m",
            WORKER_MODULE,
        ]
        popen_arguments: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(self._project_root),
            "env": self._worker_environment(),
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            creation_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if creation_flag:
                popen_arguments["creationflags"] = creation_flag
        try:
            process = subprocess.Popen(command, **popen_arguments)
        except (OSError, ValueError) as exc:
            raise EmbeddingClientError(
                "EMBEDDING_WORKER_START_FAILED",
                "The local embedding worker could not be started",
                {"reason_type": type(exc).__name__},
            ) from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.terminate()
            raise EmbeddingClientError(
                "EMBEDDING_WORKER_START_FAILED",
                "The local embedding worker pipes are unavailable",
            )

        self._process = process
        self._stdout_queue = queue.Queue(maxsize=8)
        self._stderr_parts.clear()
        self._stderr_characters = 0
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout, self._stdout_queue),
            name="hia-embedding-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="hia-embedding-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            result = self._exchange(
                "init",
                {
                    "model_dir": str(model_dir),
                    "model_id": self._active_profile.model_id,
                    "model_revision": self._active_revision(),
                    "dim": self._active_dim,
                    "profile": self._active_profile.profile_id,
                    "device": self._device,
                },
            )
            self._validate_worker_metadata(result)
            self._initialized = True
        except Exception:
            self._stop_process(graceful=False)
            raise

    def _embed_group(
        self,
        texts: list[str],
        input_type: str,
    ) -> tuple[tuple[float, ...], ...]:
        result = self._exchange(
            "embed",
            {"texts": texts, "input_type": input_type},
        )
        self._validate_worker_metadata(result)
        if result.get("input_type") != input_type:
            raise EmbeddingClientError(
                "EMBEDDING_PROTOCOL_ERROR",
                "The embedding worker returned the wrong input type",
            )
        if result.get("count") != len(texts):
            raise EmbeddingClientError(
                "EMBEDDING_COUNT_MISMATCH",
                "The embedding worker returned the wrong number of vectors",
                {"expected_count": len(texts), "actual_count": result.get("count")},
            )
        if result.get("normalized") is not True:
            raise EmbeddingClientError(
                "EMBEDDING_NOT_NORMALIZED",
                "The embedding worker did not return normalized vectors",
            )
        return _validated_vectors(
            result.get("vectors"),
            expected_count=len(texts),
            expected_dim=self._active_dim,
        )

    def _validate_worker_metadata(self, result: Mapping[str, Any]) -> None:
        expected = self._active_profile
        if (
            result.get("model_id") != expected.model_id
            or result.get("profile") != expected.profile_id
            or result.get("dim") != self._active_dim
            or result.get("model_revision") != self._active_revision()
            or result.get("device") != self._device
        ):
            raise EmbeddingClientError(
                "EMBEDDING_PROFILE_MISMATCH",
                "The embedding worker profile does not match the client configuration",
                {
                    "expected_profile": expected.profile_id,
                    "expected_dim": self._active_dim,
                },
            )

    def _exchange(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        process = self._process
        responses = self._stdout_queue
        if (
            process is None
            or process.stdin is None
            or responses is None
            or process.poll() is not None
        ):
            raise EmbeddingClientError(
                "EMBEDDING_WORKER_CLOSED",
                "The local embedding worker is not running",
                {"stderr": self._stderr_summary()},
            )
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = json.dumps(
            {"id": request_id, "method": method, "params": dict(params)},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise EmbeddingClientError(
                "EMBEDDING_REQUEST_TOO_LARGE",
                "The embedding request exceeds the byte limit",
                {"limit_bytes": MAX_REQUEST_BYTES},
            )
        try:
            process.stdin.write(payload + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise EmbeddingClientError(
                "EMBEDDING_WORKER_CLOSED",
                "The local embedding worker closed its input",
                {"stderr": self._stderr_summary()},
            ) from exc

        deadline = time.monotonic() + self._timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EmbeddingClientError(
                    "EMBEDDING_TIMEOUT",
                    "The local embedding worker did not respond before the timeout",
                    {"timeout_seconds": self._timeout_seconds},
                )
            try:
                item = responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise EmbeddingClientError(
                    "EMBEDDING_TIMEOUT",
                    "The local embedding worker did not respond before the timeout",
                    {"timeout_seconds": self._timeout_seconds},
                ) from exc
            if item is _STDOUT_EOF:
                raise EmbeddingClientError(
                    "EMBEDDING_WORKER_CLOSED",
                    "The local embedding worker exited before responding",
                    {"stderr": self._stderr_summary()},
                )
            if isinstance(item, EmbeddingClientError):
                raise item
            if not isinstance(item, str):
                raise EmbeddingClientError(
                    "EMBEDDING_PROTOCOL_ERROR",
                    "The embedding worker returned an invalid response",
                )
            try:
                response = json.loads(item)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise EmbeddingClientError(
                    "EMBEDDING_PROTOCOL_ERROR",
                    "The embedding worker returned invalid JSON",
                ) from exc
            if not isinstance(response, Mapping):
                raise EmbeddingClientError(
                    "EMBEDDING_PROTOCOL_ERROR",
                    "The embedding worker response must be an object",
                )
            if response.get("id") != request_id:
                raise EmbeddingClientError(
                    "EMBEDDING_PROTOCOL_ERROR",
                    "The embedding worker response id is out of sequence",
                )
            if response.get("ok") is not True:
                error = response.get("error")
                if not isinstance(error, Mapping):
                    raise EmbeddingClientError(
                        "EMBEDDING_PROTOCOL_ERROR",
                        "The embedding worker returned an invalid error",
                    )
                code = str(error.get("code") or "EMBEDDING_WORKER_ERROR")
                message = str(
                    error.get("message")
                    or "The local embedding worker rejected the request"
                )
                details = error.get("details")
                raise EmbeddingClientError(
                    code,
                    message,
                    details if isinstance(details, Mapping) else None,
                )
            result = response.get("result")
            if not isinstance(result, Mapping):
                raise EmbeddingClientError(
                    "EMBEDDING_PROTOCOL_ERROR",
                    "The embedding worker result must be an object",
                )
            return result

    def _may_fallback(self, error: EmbeddingClientError) -> bool:
        return (
            not self._fallback_attempted
            and self._requested_profile.profile_id == "qwen3-embedding-8b"
            and self._active_profile.profile_id == "qwen3-embedding-8b"
            and self._model_directories.get(_FALLBACK_PROFILE) is not None
            and error.code in _FALLBACK_ERROR_CODES
        )

    def _activate_fallback(self, error: EmbeddingClientError) -> None:
        self._fallback_attempted = True
        self._active_profile = PROFILES[_FALLBACK_PROFILE]
        self._active_dim = min(
            self._requested_dim,
            self._active_profile.max_dimension,
        )
        self._fallback_reason = error.code
        self._repair = _repair_details(self._requested_profile)
        self._status = "degraded"

    def _active_revision(self) -> str:
        return self._model_revisions[self._active_profile.profile_id]

    def _active_model_id(self) -> str:
        return (
            f"{self._active_profile.model_id}@{self._active_revision()}"
        )

    def _worker_environment(self) -> dict[str, str]:
        inherited = os.environ
        environment: dict[str, str] = {}
        for name in (
            "COMSPEC",
            "NUMBER_OF_PROCESSORS",
            "OS",
            "PATH",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_IDENTIFIER",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "WINDIR",
        ):
            value = inherited.get(name)
            if value:
                environment[name] = value

        layout = runtime_layout(self._project_root)
        cache_root = Path(layout["cache_root"])
        home_root = cache_root / "home"
        temp_root = Path(layout["temp_root"])
        hf_root = Path(layout["huggingface_cache"])
        transformers_root = Path(layout["transformers_cache"])
        torch_root = Path(layout["torch_cache"])
        torch_inductor_root = torch_root / "inductor"
        for path in (
            cache_root,
            home_root,
            temp_root,
            hf_root,
            transformers_root,
            torch_root,
            torch_inductor_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        environment.update(
            {
                "APPDATA": str(home_root / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home_root / "AppData" / "Local"),
                "HOME": str(home_root),
                "USERPROFILE": str(home_root),
                "TEMP": str(temp_root),
                "TMP": str(temp_root),
                "TMPDIR": str(temp_root),
                "XDG_CACHE_HOME": str(cache_root),
                "HF_HOME": str(hf_root),
                "HUGGINGFACE_HUB_CACHE": str(hf_root / "hub"),
                "TRANSFORMERS_CACHE": str(transformers_root),
                "SENTENCE_TRANSFORMERS_HOME": str(transformers_root),
                "TORCH_HOME": str(torch_root),
                "TORCHINDUCTOR_CACHE_DIR": str(torch_inductor_root),
                "CUDA_CACHE_PATH": str(cache_root / "cuda"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
            }
        )
        for path in (
            environment["APPDATA"],
            environment["LOCALAPPDATA"],
            environment["HUGGINGFACE_HUB_CACHE"],
            environment["TRANSFORMERS_CACHE"],
            environment["CUDA_CACHE_PATH"],
        ):
            Path(path).mkdir(parents=True, exist_ok=True)
        return environment

    @staticmethod
    def _read_stdout(
        stream: TextIO,
        destination: queue.Queue[object],
    ) -> None:
        try:
            while True:
                line = stream.readline(MAX_RESPONSE_CHARACTERS + 1)
                if line == "":
                    _queue_item(destination, _STDOUT_EOF)
                    return
                if len(line) > MAX_RESPONSE_CHARACTERS:
                    _queue_item(
                        destination,
                        EmbeddingClientError(
                            "EMBEDDING_RESPONSE_TOO_LARGE",
                            "The embedding worker response exceeds the character limit",
                            {"limit_characters": MAX_RESPONSE_CHARACTERS},
                        ),
                    )
                    return
                if line.strip():
                    _queue_item(destination, line)
        except Exception:
            _queue_item(destination, _STDOUT_EOF)

    def _read_stderr(self, stream: TextIO) -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    return
                bounded = line[-MAX_STDERR_CHARACTERS:]
                with self._stderr_lock:
                    self._stderr_parts.append(bounded)
                    self._stderr_characters += len(bounded)
                    while (
                        self._stderr_parts
                        and self._stderr_characters > MAX_STDERR_CHARACTERS
                    ):
                        removed = self._stderr_parts.popleft()
                        self._stderr_characters -= len(removed)
        except Exception:
            return

    def _stderr_summary(self) -> str:
        with self._stderr_lock:
            return "".join(self._stderr_parts)[-MAX_STDERR_CHARACTERS:]

    def _stop_process(self, *, graceful: bool) -> None:
        process = self._process
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread
        self._process = None
        self._stdout_queue = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._initialized = False
        self._loaded = False
        if process is None:
            return
        if graceful and process.poll() is None and process.stdin is not None:
            request_id = self._next_request_id
            self._next_request_id += 1
            try:
                payload = json.dumps(
                    {
                        "id": request_id,
                        "method": "shutdown",
                        "params": {},
                    },
                    separators=(",", ":"),
                )
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in (stdout_thread, stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=TERMINATE_GRACE_SECONDS)


def _required_runtime_file(
    raw_value: str | None,
    *,
    field: str,
    allowed_root: Path,
) -> Path:
    if raw_value is None or not raw_value.strip():
        raise EmbeddingConfigurationError(
            "EMBEDDING_CONFIGURATION_INCOMPLETE",
            f"{field} is required when local embeddings are configured",
            {"field": field},
        )
    path = _runtime_path(
        raw_value,
        field=field,
        allowed_root=allowed_root,
    )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EmbeddingConfigurationError(
            "EMBEDDING_RUNTIME_UNAVAILABLE",
            f"{field} does not exist",
            {"field": field},
        ) from exc
    if not resolved.is_file():
        raise EmbeddingConfigurationError(
            "EMBEDDING_RUNTIME_UNAVAILABLE",
            f"{field} must identify a file",
            {"field": field},
        )
    return resolved


def _optional_runtime_directory(
    raw_value: str | None,
    *,
    field: str,
    allowed_root: Path,
) -> Path | None:
    if raw_value is None:
        return None
    if not raw_value.strip():
        raise EmbeddingConfigurationError(
            "EMBEDDING_CONFIGURATION_INVALID",
            f"{field} must not be empty",
            {"field": field},
        )
    path = _runtime_path(
        raw_value,
        field=field,
        allowed_root=allowed_root,
    )
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir():
        raise EmbeddingConfigurationError(
            "EMBEDDING_MODEL_UNAVAILABLE",
            f"{field} must identify a directory",
            {"field": field},
        )
    return resolved


def _runtime_path(
    raw_value: str,
    *,
    field: str,
    allowed_root: Path,
) -> Path:
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        raise EmbeddingConfigurationError(
            "EMBEDDING_PATH_INVALID",
            f"{field} must be an absolute path",
            {"field": field},
        )
    root = allowed_root.resolve()
    resolved = candidate.resolve()
    if not _is_within(resolved, root):
        raise EmbeddingConfigurationError(
            "EMBEDDING_PATH_OUTSIDE_RUNTIME",
            f"{field} must stay under the project runtime",
            {"field": field},
        )
    return resolved


def _dimension(
    raw_value: str | None,
    profile: EmbeddingProfile,
) -> int:
    if raw_value is None:
        return profile.default_dimension
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise EmbeddingConfigurationError(
            "EMBEDDING_DIMENSION_INVALID",
            "HIA_EMBEDDING_DIM must be an integer",
        ) from exc
    if not profile.min_mrl_dimension <= value <= profile.max_dimension:
        raise EmbeddingConfigurationError(
            "EMBEDDING_DIMENSION_INVALID",
            "The embedding dimension is outside the selected profile's MRL range",
            {
                "minimum": profile.min_mrl_dimension,
                "maximum": profile.max_dimension,
                "profile": profile.profile_id,
            },
        )
    return value


def _model_revision(raw_value: str | None, *, field: str) -> str:
    value = "local" if raw_value is None else raw_value.strip()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value)
        or ".." in value
        or "//" in value
        or value.endswith("/")
    ):
        raise EmbeddingConfigurationError(
            "EMBEDDING_MODEL_REVISION_INVALID",
            f"{field} is invalid",
            {"field": field},
        )
    return value


def _device(raw_value: str | None) -> str:
    value = "auto" if raw_value is None else raw_value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
        raise EmbeddingConfigurationError(
            "EMBEDDING_DEVICE_INVALID",
            "HIA_EMBEDDING_DEVICE is invalid",
        )
    return value


def _validated_texts(
    documents: Sequence[str],
    queries: Sequence[str],
) -> tuple[list[str], list[str]]:
    if isinstance(documents, (str, bytes)) or not isinstance(
        documents, Sequence
    ):
        raise EmbeddingClientError(
            "EMBEDDING_ARGUMENTS_INVALID",
            "documents must be a sequence of strings",
        )
    if isinstance(queries, (str, bytes)) or not isinstance(queries, Sequence):
        raise EmbeddingClientError(
            "EMBEDDING_ARGUMENTS_INVALID",
            "queries must be a sequence of strings",
        )
    document_values = list(documents)
    query_values = list(queries)
    if len(document_values) + len(query_values) > MAX_BATCH_TEXTS:
        raise EmbeddingClientError(
            "EMBEDDING_BATCH_TOO_LARGE",
            "The embedding batch contains too many texts",
            {"maximum": MAX_BATCH_TEXTS},
        )
    total_characters = 0
    for value in [*document_values, *query_values]:
        if not isinstance(value, str) or not value.strip():
            raise EmbeddingClientError(
                "EMBEDDING_ARGUMENTS_INVALID",
                "Embedding texts must be non-empty strings",
            )
        if len(value) > MAX_TEXT_CHARACTERS:
            raise EmbeddingClientError(
                "EMBEDDING_TEXT_TOO_LARGE",
                "An embedding text exceeds the character limit",
                {"maximum": MAX_TEXT_CHARACTERS},
            )
        total_characters += len(value)
    if total_characters > MAX_BATCH_CHARACTERS:
        raise EmbeddingClientError(
            "EMBEDDING_BATCH_TOO_LARGE",
            "The embedding batch exceeds the character limit",
            {"maximum": MAX_BATCH_CHARACTERS},
        )
    return document_values, query_values


def _validated_vectors(
    raw_vectors: Any,
    *,
    expected_count: int,
    expected_dim: int,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(raw_vectors, list) or len(raw_vectors) != expected_count:
        raise EmbeddingClientError(
            "EMBEDDING_COUNT_MISMATCH",
            "The embedding worker returned an invalid vector batch",
            {
                "expected_count": expected_count,
                "actual_count": (
                    len(raw_vectors) if isinstance(raw_vectors, list) else None
                ),
            },
        )
    vectors: list[tuple[float, ...]] = []
    for raw_vector in raw_vectors:
        if not isinstance(raw_vector, list) or len(raw_vector) != expected_dim:
            raise EmbeddingClientError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "The embedding worker returned a vector with the wrong dimension",
                {
                    "expected_dim": expected_dim,
                    "actual_dim": (
                        len(raw_vector) if isinstance(raw_vector, list) else None
                    ),
                },
            )
        vector: list[float] = []
        for raw_value in raw_vector:
            if isinstance(raw_value, bool) or not isinstance(
                raw_value, (int, float)
            ):
                raise EmbeddingClientError(
                    "EMBEDDING_VECTOR_INVALID",
                    "The embedding worker returned a non-numeric vector",
                )
            value = float(raw_value)
            if not math.isfinite(value):
                raise EmbeddingClientError(
                    "EMBEDDING_VECTOR_INVALID",
                    "The embedding worker returned a non-finite vector",
                )
            vector.append(value)
        norm = math.sqrt(sum(value * value for value in vector))
        if (
            not math.isfinite(norm)
            or abs(norm - 1.0) > NORMALIZATION_TOLERANCE
        ):
            raise EmbeddingClientError(
                "EMBEDDING_NOT_NORMALIZED",
                "The embedding worker returned a non-normalized vector",
                {"norm": norm},
            )
        vectors.append(tuple(vector))
    return tuple(vectors)


def _repair_details(profile: EmbeddingProfile) -> dict[str, Any]:
    return {
        "profile": profile.profile_id,
        "model_id": profile.model_id,
        "model_dir_environment": profile.model_dir_environment,
        "model_revision_environment": profile.model_revision_environment,
        "action": "Install or repair the requested local model through the launcher",
        "downloads_performed": False,
    }


def _queue_item(destination: queue.Queue[object], value: object) -> None:
    try:
        destination.put_nowait(value)
    except queue.Full:
        return


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


__all__ = [
    "DEFAULT_PROFILE",
    "DIMENSION_ENVIRONMENT",
    "DEVICE_ENVIRONMENT",
    "EMBEDDING_CONTRACT_VERSION",
    "EmbeddingBatch",
    "EmbeddingClient",
    "EmbeddingClientError",
    "EmbeddingConfigurationError",
    "EmbeddingProfile",
    "MODEL_DIR_0_6B_ENVIRONMENT",
    "MODEL_DIR_8B_ENVIRONMENT",
    "MODEL_REVISION_0_6B_ENVIRONMENT",
    "MODEL_REVISION_8B_ENVIRONMENT",
    "PROFILE_ENVIRONMENT",
    "PROFILES",
    "PYTHON_ENVIRONMENT",
]
