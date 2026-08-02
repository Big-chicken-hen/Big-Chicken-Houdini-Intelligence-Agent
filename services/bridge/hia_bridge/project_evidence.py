"""Deterministic provenance checks for project-team execution evidence.

This module verifies identities, completed tool-call records, capture artifacts,
and byte budgets.  It deliberately makes no visual, technical, or natural-
language quality judgement.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_HIA_SERVERS = frozenset({"hia_mcp_v2", "houdini_intelligence"})
_CAPTURE_TOOL = "hia_capture_viewport"
_SUCCESS_STATUSES = frozenset({"completed", "success", "succeeded"})
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_NAMED_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|cookie|credential|password|secret|"
    r"access[_-]?token|auth[_-]?token|bearer[_-]?token|id[_-]?token|"
    r"refresh[_-]?token|token)\s*[:=]\s*)([^\s,;]+)"
)
_QUERY_CREDENTIAL_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|token)=)[^&#\s]+"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b")


class EvidenceValidationError(ValueError):
    """A stable, credential-safe evidence validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedEvidence:
    item_id: str
    thread_id: str
    turn_id: str
    tool: str
    payload: Mapping[str, Any]
    artifact_paths: tuple[str, ...]
    evidence_bytes: int


@dataclass(frozen=True)
class EvidenceValidationResult:
    evidence: tuple[ValidatedEvidence, ...]
    added_evidence_bytes: int
    total_evidence_bytes: int


def redact_credentials(value: Any) -> Any:
    """Return a recursively credential-redacted JSON-like value."""

    if isinstance(value, str):
        text = _BEARER_PATTERN.sub(r"\1" + _REDACTED, value)
        text = _NAMED_CREDENTIAL_PATTERN.sub(r"\1" + _REDACTED, text)
        text = _QUERY_CREDENTIAL_PATTERN.sub(r"\1" + _REDACTED, text)
        return _OPENAI_KEY_PATTERN.sub(_REDACTED, text)
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = redact_credentials(key) if isinstance(key, str) else key
            redacted[safe_key] = (
                _REDACTED
                if isinstance(key, str) and _is_sensitive_key(key)
                else redact_credentials(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_credentials(item) for item in value)
    return value


def validate_evidence(
    *,
    references: Sequence[Mapping[str, Any]],
    tool_events: Sequence[Mapping[str, Any]],
    execution_thread_id: str,
    execution_turn_id: str,
    allowed_roots: Sequence[str | Path],
    existing_total_evidence_bytes: int,
    max_total_evidence_bytes: int,
) -> EvidenceValidationResult:
    """Validate evidence references against authoritative app-server events.

    A reference has an ``item_id``.  References to ``hia_capture_viewport``
    additionally require ``frame`` and may provide ``path``; when omitted, the
    capture must expose exactly one artifact for that frame.
    """

    _require_text(execution_thread_id, "execution_thread_id")
    _require_text(execution_turn_id, "execution_turn_id")
    _require_non_negative_int(
        existing_total_evidence_bytes, "existing_total_evidence_bytes"
    )
    _require_positive_int(max_total_evidence_bytes, "max_total_evidence_bytes")
    roots = _resolve_allowed_roots(allowed_roots)
    completed = _completed_items(tool_events)
    validated: list[ValidatedEvidence] = []
    seen_items: set[str] = set()
    counted_paths: set[Path] = set()
    added_bytes = 0

    for reference in references:
        if not isinstance(reference, Mapping):
            raise EvidenceValidationError(
                "INVALID_EVIDENCE_REFERENCE", "Evidence references must be objects"
            )
        item_id = _require_text(reference.get("item_id"), "item_id")
        event = completed.get(item_id)
        if event is None:
            raise EvidenceValidationError(
                "UNKNOWN_TOOL_ITEM", "Evidence references an unknown completed tool item"
            )
        params, item = event
        if params.get("threadId") != execution_thread_id:
            raise EvidenceValidationError(
                "WRONG_EXECUTION_THREAD",
                "Evidence does not belong to the current Execution Thread",
            )
        if params.get("turnId") != execution_turn_id:
            raise EvidenceValidationError(
                "STALE_EXECUTION_TURN",
                "Evidence does not belong to the current Execution Turn",
            )
        _validate_successful_hia_item(item)

        artifact_paths: tuple[str, ...] = ()
        artifact_bytes = 0
        if item.get("tool") == _CAPTURE_TOOL:
            frame = _require_frame(reference.get("frame"), "frame")
            requested_path = reference.get("path")
            if requested_path is not None:
                requested_path = _require_text(requested_path, "path")
            capture_path = _select_capture_path(item, frame, requested_path)
            resolved, file_size = _validate_capture_file(capture_path, roots)
            artifact_paths = (str(resolved),)
            if resolved not in counted_paths:
                counted_paths.add(resolved)
                artifact_bytes = file_size
        elif "frame" in reference or "path" in reference:
            raise EvidenceValidationError(
                "INVALID_EVIDENCE_REFERENCE",
                "Frame and path fields are reserved for viewport capture evidence",
            )

        if item_id in seen_items:
            continue
        seen_items.add(item_id)
        safe_payload = redact_credentials(item)
        payload_bytes = len(
            json.dumps(
                safe_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        item_bytes = payload_bytes + artifact_bytes
        added_bytes += item_bytes
        if existing_total_evidence_bytes + added_bytes > max_total_evidence_bytes:
            raise EvidenceValidationError(
                "EVIDENCE_BYTE_BUDGET_EXCEEDED",
                "Validated evidence would exceed the project evidence byte budget",
            )
        validated.append(
            ValidatedEvidence(
                item_id=item_id,
                thread_id=execution_thread_id,
                turn_id=execution_turn_id,
                tool=str(item["tool"]),
                payload=safe_payload,
                artifact_paths=artifact_paths,
                evidence_bytes=item_bytes,
            )
        )

    return EvidenceValidationResult(
        evidence=tuple(validated),
        added_evidence_bytes=added_bytes,
        total_evidence_bytes=existing_total_evidence_bytes + added_bytes,
    )


def _completed_items(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    completed: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        method = event.get("method")
        if event.get("type") == "codex_notification":
            pass
        elif method is None and event.get("type") == "item/completed":
            method = "item/completed"
        if method != "item/completed":
            continue
        params = event.get("params")
        if not isinstance(params, Mapping):
            continue
        item = params.get("item")
        if not isinstance(item, Mapping):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if item_id in completed:
            raise EvidenceValidationError(
                "DUPLICATE_TOOL_ITEM_ID",
                "The authoritative event set contains a duplicate completed tool item ID",
            )
        completed[item_id] = (params, item)
    return completed


def _validate_successful_hia_item(item: Mapping[str, Any]) -> None:
    if item.get("type") != "mcpToolCall":
        raise EvidenceValidationError(
            "NOT_HIA_TOOL_ITEM", "Evidence item is not an MCP tool call"
        )
    server = item.get("server")
    tool = item.get("tool")
    if server not in _HIA_SERVERS or not isinstance(tool, str) or not tool.startswith("hia_"):
        raise EvidenceValidationError(
            "NOT_HIA_TOOL_ITEM", "Evidence item is not a completed HIA tool call"
        )
    status = item.get("status")
    if not isinstance(status, str) or status.lower() not in _SUCCESS_STATUSES:
        raise EvidenceValidationError(
            "TOOL_ITEM_NOT_SUCCESSFUL", "Evidence tool item did not complete successfully"
        )
    if item.get("error") not in (None, {}, []):
        raise EvidenceValidationError(
            "TOOL_ITEM_NOT_SUCCESSFUL", "Evidence tool item contains an error"
        )
    result = item.get("result")
    if isinstance(result, Mapping):
        if result.get("isError") is True:
            raise EvidenceValidationError(
                "TOOL_ITEM_NOT_SUCCESSFUL", "Evidence tool result is marked as an error"
            )
        structured = result.get("structuredContent")
        if isinstance(structured, Mapping) and structured.get("ok") is not True:
            raise EvidenceValidationError(
                "TOOL_ITEM_NOT_SUCCESSFUL",
                "HIA structured evidence does not report successful completion",
            )


def _select_capture_path(
    item: Mapping[str, Any], frame: float, requested_path: str | None
) -> str:
    structured = _structured_content(item)
    payload = structured.get("result")
    if not isinstance(payload, Mapping):
        raise EvidenceValidationError(
            "INVALID_CAPTURE_EVIDENCE", "Viewport capture has no structured result"
        )
    candidates: list[tuple[float, str]] = []
    _append_capture_candidate(candidates, payload, fallback_path=None)
    sequence = payload.get("sequence")
    if isinstance(sequence, Mapping):
        records = sequence.get("frames")
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
            for record in records:
                if isinstance(record, Mapping):
                    _append_capture_candidate(candidates, record, fallback_path=None)
    matches = [
        path
        for actual_frame, path in candidates
        if math.isclose(actual_frame, frame, rel_tol=0.0, abs_tol=1.0e-6)
        and (requested_path is None or Path(path) == Path(requested_path))
    ]
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        raise EvidenceValidationError(
            "CAPTURE_FRAME_MISMATCH",
            "Viewport evidence must resolve to exactly one artifact at the specified frame",
        )
    return matches[0]


def _append_capture_candidate(
    candidates: list[tuple[float, str]],
    value: Mapping[str, Any],
    *,
    fallback_path: str | None,
) -> None:
    frames = [
        value.get("requested_frame"),
        value.get("actual_frame"),
        value.get("quality_frame"),
    ]
    numeric = [float(item) for item in frames if _is_frame(item)]
    if not numeric or any(
        not math.isclose(item, numeric[0], rel_tol=0.0, abs_tol=1.0e-6)
        for item in numeric[1:]
    ):
        return
    path = value.get("absolute_path") or value.get("evidence_path") or fallback_path
    if isinstance(path, str) and path:
        candidates.append((numeric[0], path))


def _structured_content(item: Mapping[str, Any]) -> Mapping[str, Any]:
    result = item.get("result")
    if not isinstance(result, Mapping):
        return {}
    structured = result.get("structuredContent")
    return structured if isinstance(structured, Mapping) else {}


def _validate_capture_file(path: str, roots: tuple[Path, ...]) -> tuple[Path, int]:
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise EvidenceValidationError(
            "CAPTURE_FILE_UNAVAILABLE", "Viewport evidence file is unavailable"
        ) from exc
    if not resolved.is_file() or not any(_is_relative_to(resolved, root) for root in roots):
        raise EvidenceValidationError(
            "CAPTURE_PATH_NOT_ALLOWED",
            "Viewport evidence path is outside the allowed artifact roots",
        )
    try:
        with resolved.open("rb") as stream:
            header = stream.read(16)
        size = resolved.stat().st_size
    except OSError as exc:
        raise EvidenceValidationError(
            "CAPTURE_FILE_UNAVAILABLE", "Viewport evidence file cannot be read"
        ) from exc
    detected = _image_type(header)
    suffix = resolved.suffix.lower()
    allowed_suffixes = {
        "png": {".png"},
        "jpeg": {".jpg", ".jpeg"},
        "webp": {".webp"},
    }
    if detected is None or suffix not in allowed_suffixes[detected]:
        raise EvidenceValidationError(
            "INVALID_CAPTURE_FILE_TYPE",
            "Viewport evidence must be a real PNG, JPEG, or WebP file",
        )
    return resolved, size


def _image_type(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def _resolve_allowed_roots(values: Sequence[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in values:
        try:
            root = Path(value).resolve(strict=True)
        except (OSError, RuntimeError, TypeError) as exc:
            raise EvidenceValidationError(
                "INVALID_ALLOWED_ROOT", "Allowed evidence roots must already exist"
            ) from exc
        if not root.is_dir():
            raise EvidenceValidationError(
                "INVALID_ALLOWED_ROOT", "Allowed evidence roots must be directories"
            )
        roots.append(root)
    if not roots:
        raise EvidenceValidationError(
            "INVALID_ALLOWED_ROOT", "At least one allowed evidence root is required"
        )
    return tuple(roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_sensitive_key(value: str) -> bool:
    normalized = "".join(character for character in value.lower() if character.isalnum())
    if normalized.endswith(
        (
            "apikey",
            "authorization",
            "cookie",
            "credential",
            "password",
            "secret",
            "accesstoken",
            "authtoken",
            "bearertoken",
            "idtoken",
            "refreshtoken",
            "sessiontoken",
        )
    ):
        return True
    words = [
        word.lower()
        for word in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
            value.replace("-", " ").replace("_", " "),
        )
    ]
    return any(word in _SENSITIVE_KEY_WORDS for word in words) or (
        "api" in words and "key" in words
    )


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceValidationError(
            "INVALID_EVIDENCE_INPUT", f"{name} must be a non-empty string"
        )
    return value


def _require_non_negative_int(value: Any, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceValidationError(
            "INVALID_EVIDENCE_INPUT", f"{name} must be a non-negative integer"
        )


def _require_positive_int(value: Any, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EvidenceValidationError(
            "INVALID_EVIDENCE_INPUT", f"{name} must be a positive integer"
        )


def _is_frame(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _require_frame(value: Any, name: str) -> float:
    if not _is_frame(value):
        raise EvidenceValidationError(
            "INVALID_EVIDENCE_INPUT", f"{name} must be a finite frame number"
        )
    return float(value)
