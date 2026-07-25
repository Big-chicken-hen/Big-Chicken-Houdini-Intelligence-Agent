"""Bounded persistent JSONL worker for local text embeddings.

The module deliberately does not import an ML runtime or load a model at import
time.  A trusted parent process supplies one immutable local model profile with
``init``; the model is loaded only when the first ``embed`` request arrives.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Protocol, Sequence


MAX_REQUEST_BYTES = 1024 * 1024
MAX_BATCH_TEXTS = 64
MAX_TEXT_CHARACTERS = 32 * 1024
MAX_BATCH_CHARACTERS = 256 * 1024
MAX_EMBEDDING_DIMENSION = 4096


@dataclass(frozen=True)
class WorkerError(Exception):
    """Stable protocol error that never contains backend exception text."""

    code: str
    message: str
    details: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ModelProfile:
    """One immutable model selection for the lifetime of a worker process."""

    model_id: str
    model_dir: Path
    dim: int
    profile: str
    model_revision: str = "local"
    device: str = "auto"

    def public_metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "dim": self.dim,
            "profile": self.profile,
            "model_revision": self.model_revision,
            "device": self.device,
        }


class EmbeddingBackend(Protocol):
    def encode(
        self,
        texts: Sequence[str],
        *,
        input_type: str,
    ) -> Any: ...


BackendFactory = Callable[[ModelProfile], EmbeddingBackend]
ModelFactory = Callable[..., Any]


class SentenceTransformerBackend:
    """Lazy, offline-only adapter around ``sentence_transformers``."""

    def __init__(
        self,
        model_profile: ModelProfile,
        *,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._profile = model_profile
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if (
            not self._profile.model_dir.is_absolute()
            or not self._profile.model_dir.is_dir()
        ):
            raise WorkerError(
                "MODEL_DIRECTORY_INVALID",
                "The configured local model directory is unavailable",
            )

        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        model_factory = self._model_factory
        if model_factory is None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:
                raise WorkerError(
                    "MODEL_RUNTIME_UNAVAILABLE",
                    "The local embedding runtime is unavailable",
                ) from exc
            model_factory = SentenceTransformer

        try:
            options: dict[str, Any] = {
                "local_files_only": True,
                "trust_remote_code": False,
                "truncate_dim": self._profile.dim,
            }
            if self._profile.device != "auto":
                options["device"] = self._profile.device
            self._model = model_factory(
                str(self._profile.model_dir),
                **options,
            )
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerError(
                "MODEL_LOAD_FAILED",
                "The configured local embedding model could not be loaded",
            ) from exc
        return self._model

    def encode(
        self,
        texts: Sequence[str],
        *,
        input_type: str,
    ) -> Any:
        model = self._load_model()
        options: dict[str, Any] = {
            "normalize_embeddings": True,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        }
        if input_type == "query":
            options["prompt_name"] = "query"
        try:
            return model.encode(list(texts), **options)
        except Exception as exc:
            raise WorkerError(
                "EMBEDDING_FAILED",
                "The local embedding model could not encode the request",
            ) from exc


def _default_backend_factory(profile: ModelProfile) -> EmbeddingBackend:
    return SentenceTransformerBackend(profile)


def _clean_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise WorkerError("INVALID_PARAMS", f"{field} must be a string")
    result = value.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}", result) is None:
        raise WorkerError("INVALID_PARAMS", f"{field} is invalid")
    return result


def _model_profile(params: Mapping[str, Any]) -> ModelProfile:
    model_id = _clean_identifier(params.get("model_id"), field="model_id")
    profile = _clean_identifier(params.get("profile"), field="profile")
    model_revision = _clean_identifier(
        params.get("model_revision", "local"),
        field="model_revision",
    )
    raw_device = params.get("device", "auto")
    if (
        not isinstance(raw_device, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", raw_device.strip()) is None
    ):
        raise WorkerError("INVALID_PARAMS", "device is invalid")
    device = raw_device.strip()
    raw_model_dir = params.get("model_dir")
    if not isinstance(raw_model_dir, str) or not raw_model_dir.strip():
        raise WorkerError("INVALID_PARAMS", "model_dir must be an absolute path")
    candidate = Path(raw_model_dir)
    if not candidate.is_absolute():
        raise WorkerError(
            "MODEL_DIRECTORY_INVALID",
            "The configured local model directory must be absolute",
        )
    try:
        model_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise WorkerError(
            "MODEL_DIRECTORY_INVALID",
            "The configured local model directory is unavailable",
        ) from exc
    if not model_dir.is_dir():
        raise WorkerError(
            "MODEL_DIRECTORY_INVALID",
            "The configured local model directory is unavailable",
        )

    dim = params.get("dim")
    if (
        isinstance(dim, bool)
        or not isinstance(dim, int)
        or dim < 1
        or dim > MAX_EMBEDDING_DIMENSION
    ):
        raise WorkerError(
            "INVALID_PARAMS",
            f"dim must be an integer from 1 through {MAX_EMBEDDING_DIMENSION}",
        )
    return ModelProfile(
        model_id=model_id,
        model_dir=model_dir,
        dim=dim,
        profile=profile,
        model_revision=model_revision,
        device=device,
    )


def _texts(params: Mapping[str, Any]) -> tuple[list[str], str]:
    raw_texts = params.get("texts")
    if (
        not isinstance(raw_texts, list)
        or not raw_texts
        or len(raw_texts) > MAX_BATCH_TEXTS
    ):
        raise WorkerError(
            "INVALID_PARAMS",
            f"texts must contain from 1 through {MAX_BATCH_TEXTS} strings",
        )
    texts: list[str] = []
    total = 0
    for value in raw_texts:
        if not isinstance(value, str) or not value.strip():
            raise WorkerError(
                "INVALID_PARAMS",
                "texts must contain non-empty strings",
            )
        if len(value) > MAX_TEXT_CHARACTERS:
            raise WorkerError("INVALID_PARAMS", "A text input is too large")
        total += len(value)
        if total > MAX_BATCH_CHARACTERS:
            raise WorkerError("INVALID_PARAMS", "The text batch is too large")
        texts.append(value)

    input_type = params.get("input_type")
    if input_type not in {"query", "document"}:
        raise WorkerError(
            "INVALID_PARAMS",
            "input_type must be query or document",
        )
    return texts, input_type


def _normalized_vectors(
    raw_vectors: Any,
    *,
    expected_count: int,
    expected_dim: int,
) -> list[list[float]]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    if not isinstance(raw_vectors, (list, tuple)) or len(raw_vectors) != expected_count:
        raise WorkerError(
            "INVALID_EMBEDDING_RESULT",
            "The embedding backend returned an invalid batch",
        )

    vectors: list[list[float]] = []
    for raw_vector in raw_vectors:
        if hasattr(raw_vector, "tolist"):
            raw_vector = raw_vector.tolist()
        if not isinstance(raw_vector, (list, tuple)):
            raise WorkerError(
                "INVALID_EMBEDDING_RESULT",
                "The embedding backend returned an invalid vector",
            )
        if len(raw_vector) != expected_dim:
            raise WorkerError(
                "DIMENSION_MISMATCH",
                "The embedding dimension does not match the initialized profile",
                {"expected_dim": expected_dim, "actual_dim": len(raw_vector)},
            )
        vector: list[float] = []
        for value in raw_vector:
            if isinstance(value, bool):
                raise WorkerError(
                    "INVALID_EMBEDDING_RESULT",
                    "The embedding backend returned a non-numeric vector",
                )
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise WorkerError(
                    "INVALID_EMBEDDING_RESULT",
                    "The embedding backend returned a non-numeric vector",
                ) from exc
            if not math.isfinite(number):
                raise WorkerError(
                    "INVALID_EMBEDDING_RESULT",
                    "The embedding backend returned a non-finite vector",
                )
            vector.append(number)
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0.0:
            raise WorkerError(
                "INVALID_EMBEDDING_RESULT",
                "The embedding backend returned a zero-length vector",
            )
        vectors.append([float(value / norm) for value in vector])
    return vectors


class EmbeddingWorker:
    """State machine for one trusted profile and many embedding requests."""

    def __init__(self, *, backend_factory: BackendFactory | None = None) -> None:
        self._backend_factory = backend_factory or _default_backend_factory
        self._profile: ModelProfile | None = None
        self._backend: EmbeddingBackend | None = None
        self._loaded = False

    @property
    def initialized(self) -> bool:
        return self._profile is not None

    @property
    def loaded(self) -> bool:
        return self._loaded

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = _model_profile(params)
        if self._profile is not None and requested != self._profile:
            raise WorkerError(
                "ALREADY_INITIALIZED",
                "This worker process is already bound to another model profile",
            )
        self._profile = requested
        return {
            **requested.public_metadata(),
            "initialized": True,
            "loaded": self.loaded,
        }

    def _health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "initialized": self.initialized,
            "loaded": self.loaded,
        }
        if self._profile is not None:
            result.update(self._profile.public_metadata())
        return result

    def _embed(self, params: Mapping[str, Any]) -> dict[str, Any]:
        profile = self._profile
        if profile is None:
            raise WorkerError(
                "NOT_INITIALIZED",
                "Initialize one local model profile before embedding",
            )
        texts, input_type = _texts(params)
        if self._backend is None:
            try:
                self._backend = self._backend_factory(profile)
            except WorkerError:
                raise
            except Exception as exc:
                raise WorkerError(
                    "MODEL_LOAD_FAILED",
                    "The configured local embedding model could not be loaded",
                ) from exc
        try:
            raw_vectors = self._backend.encode(texts, input_type=input_type)
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerError(
                "EMBEDDING_FAILED",
                "The local embedding model could not encode the request",
            ) from exc
        self._loaded = True
        vectors = _normalized_vectors(
            raw_vectors,
            expected_count=len(texts),
            expected_dim=profile.dim,
        )
        return {
            **profile.public_metadata(),
            "normalized": True,
            "input_type": input_type,
            "count": len(vectors),
            "vectors": vectors,
        }

    def handle(self, request: Any) -> tuple[dict[str, Any], bool]:
        request_id: int | str | None = None
        try:
            if not isinstance(request, dict):
                raise WorkerError("INVALID_REQUEST", "The request must be an object")
            candidate_id = request.get("id")
            if (
                isinstance(candidate_id, bool)
                or not isinstance(candidate_id, (int, str))
                or (
                    isinstance(candidate_id, str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", candidate_id)
                    is None
                )
            ):
                raise WorkerError("INVALID_REQUEST", "The request id is invalid")
            request_id = candidate_id
            method = request.get("method")
            if not isinstance(method, str):
                raise WorkerError("INVALID_REQUEST", "The request method is invalid")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise WorkerError("INVALID_PARAMS", "params must be an object")

            if method == "init":
                result = self._initialize(params)
            elif method == "health":
                result = self._health()
            elif method == "embed":
                result = self._embed(params)
            elif method == "shutdown":
                result = {"shutdown": True}
            else:
                raise WorkerError(
                    "METHOD_NOT_FOUND",
                    "The requested method is unsupported",
                )
            should_stop = method == "shutdown"
            return {"id": request_id, "ok": True, "result": result}, should_stop
        except WorkerError as exc:
            error: dict[str, Any] = {
                "code": exc.code,
                "message": exc.message,
            }
            if exc.details:
                error["details"] = dict(exc.details)
            return {"id": request_id, "ok": False, "error": error}, False
        except Exception:
            return {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The embedding worker failed internally",
                },
            }, False


def _protocol_error(code: str, message: str) -> dict[str, Any]:
    return {
        "id": None,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _write_response(destination: BinaryIO, response: Mapping[str, Any]) -> None:
    payload = json.dumps(
        response,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    destination.write(payload + b"\n")
    destination.flush()


def _discard_line_remainder(source: BinaryIO) -> None:
    while True:
        fragment = source.readline(MAX_REQUEST_BYTES + 2)
        if fragment == b"" or fragment.endswith(b"\n"):
            return


def run_stdio(
    worker: EmbeddingWorker | None = None,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> int:
    """Run until trusted shutdown or EOF, writing JSON only to stdout."""

    service = worker or EmbeddingWorker()
    source = input_stream or sys.stdin.buffer
    destination = output_stream or sys.stdout.buffer
    while True:
        raw_line = source.readline(MAX_REQUEST_BYTES + 2)
        if raw_line == b"":
            return 0
        if len(raw_line) > MAX_REQUEST_BYTES + 1:
            if not raw_line.endswith(b"\n"):
                _discard_line_remainder(source)
            try:
                _write_response(
                    destination,
                    _protocol_error(
                        "REQUEST_TOO_LARGE",
                        "The JSONL request exceeds the byte limit",
                    ),
                )
            except Exception:
                return 1
            continue
        if not raw_line.strip():
            continue
        try:
            request = json.loads(
                raw_line.decode("utf-8", errors="strict"),
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            response = _protocol_error("INVALID_JSON", "The JSONL request is invalid")
            stop = False
        else:
            response, stop = service.handle(request)
        try:
            _write_response(destination, response)
        except Exception:
            return 1
        if stop:
            return 0


def main() -> int:
    return run_stdio()
