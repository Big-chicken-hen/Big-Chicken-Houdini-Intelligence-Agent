"""Deterministic normalization for explicitly selected local knowledge sources.

This module performs no indexing, model work, directory watching, or network
access.  Callers explicitly provide one file, one exported thread, or one
project-memory row and receive bounded, redacted records ready for the existing
SQLite/FTS/vector ingestion path.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_THREAD_MESSAGES = 10_000
MAX_MESSAGE_CHARS = 262_144
VERIFICATION = "user_supplied_unverified"
TEXT_SUFFIXES = frozenset({".txt", ".md", ".html", ".htm", ".srt", ".vtt"})
OPTIONAL_SUFFIXES = frozenset({".pdf"})
CAPTION_SUFFIXES = frozenset({".srt", ".vtt"})
MEDIA_SUFFIXES = frozenset(
    {
        ".aac",
        ".avi",
        ".flac",
        ".m4a",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".wav",
        ".webm",
    }
)
INACTIVE_MEMORY_STATUSES = frozenset({"deleted", "superseded"})
_INTERNAL_MESSAGE_TYPES = frozenset(
    {
        "analysis",
        "commentary",
        "event",
        "function_call",
        "function_result",
        "internal",
        "reasoning",
        "tool",
        "tool_call",
        "tool_output",
        "tool_result",
    }
)
_FINAL_MARKERS = frozenset(
    {
        "assistant_final",
        "completed",
        "final",
        "final_answer",
        "final_message",
    }
)


class SourceAdapterError(ValueError):
    """Raised when an explicitly supplied source has an invalid shape."""


@dataclass(frozen=True)
class NormalizedSource:
    source_kind: str
    source_key: str
    title: str
    text: str
    verification: str
    content_hash: str
    provenance: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_key": self.source_key,
            "title": self.title,
            "text": self.text,
            "verification": self.verification,
            "content_hash": self.content_hash,
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SourceBatch:
    status: str
    records: tuple[NormalizedSource, ...] = ()
    remove_source_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": [record.as_dict() for record in self.records],
            "remove_source_keys": list(self.remove_source_keys),
            "warnings": list(self.warnings),
            "details": dict(self.details or {}),
        }


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        folded = tag.casefold()
        if folded in {"script", "style"}:
            self.hidden_depth += 1
        elif not self.hidden_depth and folded in {
            "br",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "p",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if folded in {"script", "style"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and folded in {
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "p",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def normalize_selected_file(
    path: str | Path,
    *,
    source_id: str = "",
    source_kind: str = "",
) -> SourceBatch:
    """Normalize one user-selected ordinary file without writing anywhere."""

    selected = Path(path)
    suffix = selected.suffix.casefold()
    declared_kind = str(source_kind or "").strip()
    if declared_kind not in {"", "user_document", "user_transcript"}:
        raise SourceAdapterError("source_kind is unsupported")
    if suffix in MEDIA_SUFFIXES:
        if declared_kind and declared_kind != "user_transcript":
            raise SourceAdapterError("media can only produce user_transcript")
        return _normalize_media(selected, source_id=source_id)
    if suffix not in TEXT_SUFFIXES | OPTIONAL_SUFFIXES:
        return SourceBatch(
            status="unsupported",
            warnings=(f"Unsupported explicit source type: {suffix or '<none>'}",),
            details={"suffix": suffix},
        )
    if (
        declared_kind == "user_transcript"
        and suffix not in CAPTION_SUFFIXES | {".txt"}
    ):
        raise SourceAdapterError(
            "user_transcript requires TXT, SRT, or VTT text"
        )
    if declared_kind == "user_document" and suffix in CAPTION_SUFFIXES:
        raise SourceAdapterError("caption files are user_transcript sources")
    return _normalize_text_file(
        selected,
        source_id=source_id,
        source_kind=declared_kind,
    )


def find_media_sidecar(path: str | Path) -> Path | None:
    """Return one existing deterministic transcript for a selected media file."""

    selected = Path(path)
    if selected.suffix.casefold() not in MEDIA_SUFFIXES:
        return None
    resolved, error = _ordinary_file(
        selected,
        enforce_size_limit=False,
    )
    return None if error else _media_sidecar(resolved)


def normalize_thread_export(
    thread_id: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    explicitly_selected: bool,
) -> SourceBatch:
    """Normalize only public user text and assistant final text from one task."""

    if not explicitly_selected:
        raise SourceAdapterError("Thread import requires an explicit selection")
    normalized_thread_id = _identifier(thread_id, "thread_id")
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise SourceAdapterError("messages must be an ordered message array")
    if len(messages) > MAX_THREAD_MESSAGES:
        raise SourceAdapterError("thread export exceeds the message limit")

    records: list[NormalizedSource] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or not _is_public_message(message):
            continue
        role = str(message.get("role") or "").casefold()
        text = _normalize_text(_redact_text(_public_message_text(message)))
        if not text:
            continue
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS]
        turn_id = _optional_identifier(message.get("turn_id"))
        message_id = _optional_identifier(message.get("id"))
        timestamp = _bounded_text(message.get("timestamp"), 128)
        message_identity = message_id or f"{turn_id}:{role}:{index}"
        key_material = "\0".join(
            (normalized_thread_id, role, message_identity)
        )
        source_key = f"thread_export:{_sha256(key_material)[:32]}"
        title_part = turn_id or message_id or str(index + 1)
        records.append(
            _record(
                source_kind="thread_export",
                source_key=source_key,
                title=f"Thread {normalized_thread_id} · {role} · {title_part}",
                text=text,
                provenance={
                    "thread_id": normalized_thread_id,
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "message_role": role,
                    "timestamp": timestamp,
                    "explicitly_selected": True,
                },
                metadata={"message_index": index},
            )
        )
    return SourceBatch(
        status="ready" if records else "no_public_text",
        records=tuple(records),
        details={
            "thread_id": normalized_thread_id,
            "messages_received": len(messages),
            "records_emitted": len(records),
        },
    )


def normalize_project_memory(memory: Mapping[str, Any]) -> SourceBatch:
    """Normalize one explicit memory row or describe its deterministic removal."""

    if not isinstance(memory, Mapping):
        raise SourceAdapterError("memory must be an object")
    stable_id = _identifier(
        memory.get("stable_id") or memory.get("id"),
        "stable_id",
    )
    source_key = f"memory:{stable_id}"
    status = str(memory.get("status") or "active").strip().casefold()
    if status in INACTIVE_MEMORY_STATUSES:
        return SourceBatch(
            status="inactive",
            remove_source_keys=(source_key,),
            details={"stable_id": stable_id, "memory_status": status},
        )
    if status != "active":
        raise SourceAdapterError("memory status must be active, superseded, or deleted")

    title = _normalize_text(_redact_text(str(memory.get("title") or "")))
    body = _normalize_text(_redact_text(str(memory.get("body") or "")))
    if not title or not body:
        raise SourceAdapterError("active memory requires title and body")
    tags = _tags(memory.get("tags", ()))
    text = f"{title}\n\n{body}"
    if tags:
        text += f"\n\nTags: {', '.join(tags)}"
    source_thread_id = _optional_identifier(memory.get("source_thread_id"))
    source_turn_id = _optional_identifier(memory.get("source_turn_id"))
    summary = _normalize_text(_redact_text(str(memory.get("summary") or "")))
    return SourceBatch(
        status="ready",
        records=(
            _record(
                source_kind="project_memory",
                source_key=source_key,
                title=title,
                text=text,
                provenance={
                    "stable_id": stable_id,
                    "status": "active",
                    "source_thread_id": source_thread_id,
                    "source_turn_id": source_turn_id,
                },
                metadata={
                    "memory_type": _bounded_text(
                        memory.get("memory_type"),
                        64,
                    ),
                    "scope": _bounded_text(memory.get("scope") or "project", 256),
                    "tags": tags,
                    "summary": summary,
                    "created_at": _bounded_text(memory.get("created_at"), 128),
                    "updated_at": _bounded_text(memory.get("updated_at"), 128),
                },
            ),
        ),
    )


def _normalize_media(path: Path, *, source_id: str) -> SourceBatch:
    resolved, path_error = _ordinary_file(
        path,
        enforce_size_limit=False,
    )
    if path_error:
        return SourceBatch(status="unavailable", warnings=(path_error,))
    transcript = find_media_sidecar(resolved)
    details = {
        "media_path": _redact_text(str(resolved)),
        "media_suffix": resolved.suffix.casefold(),
        "media_bytes": resolved.stat().st_size,
        "embedded_probe_status": "tool_unavailable",
    }
    if transcript is None:
        return SourceBatch(
            status="transcript_required",
            warnings=(
                "No deterministic SRT, VTT, or TXT transcript sidecar was found; "
                "no project ffprobe/ffmpeg extractor is configured",
            ),
            details=details,
        )
    extracted = _read_text(transcript)
    if extracted[0] is None:
        return SourceBatch(status="unavailable", warnings=(extracted[1],))
    text = extracted[0]
    if transcript.suffix.casefold() in CAPTION_SUFFIXES:
        text = _captions_to_text(text)
    text = _normalize_text(_redact_text(text))
    if not text:
        return SourceBatch(
            status="transcript_required",
            warnings=("The selected media transcript contained no usable text",),
            details=details,
        )
    source_key = _path_source_key(
        "user_transcript",
        resolved,
        source_id or transcript.name,
    )
    details["transcript_path"] = _redact_text(str(transcript))
    return SourceBatch(
        status="ready",
        records=(
            _record(
                source_kind="user_transcript",
                source_key=source_key,
                title=resolved.stem,
                text=text,
                provenance={
                    "selected_media": _redact_text(str(resolved)),
                    "transcript_sidecar": _redact_text(str(transcript)),
                    "transcript_format": transcript.suffix.casefold().lstrip("."),
                    "extraction": "existing_sidecar",
                },
                metadata={
                    "media_suffix": resolved.suffix.casefold(),
                    "transcript_required": False,
                },
            ),
        ),
        details=details,
    )


def _normalize_text_file(
    path: Path,
    *,
    source_id: str,
    source_kind: str = "",
) -> SourceBatch:
    resolved, path_error = _ordinary_file(path)
    if path_error:
        return SourceBatch(status="unavailable", warnings=(path_error,))
    suffix = resolved.suffix.casefold()
    if suffix == ".pdf":
        text, error = _read_optional_pdf(resolved)
    else:
        text, error = _read_text(resolved)
    if text is None:
        return SourceBatch(status="extractor_unavailable", warnings=(error,))
    if suffix in {".html", ".htm"}:
        text = _html_to_text(text)
    elif suffix in CAPTION_SUFFIXES:
        text = _captions_to_text(text)
    text = _normalize_text(_redact_text(text))
    if not text:
        return SourceBatch(
            status="unavailable",
            warnings=("The explicit source contained no usable text",),
        )
    effective_source_kind = (
        source_kind
        or (
            "user_transcript"
            if suffix in CAPTION_SUFFIXES
            else "user_document"
        )
    )
    return SourceBatch(
        status="ready",
        records=(
            _record(
                source_kind=effective_source_kind,
                source_key=_path_source_key(
                    effective_source_kind,
                    resolved,
                    source_id,
                ),
                title=resolved.stem,
                text=text,
                provenance={
                    "selected_path": _redact_text(str(resolved)),
                    "format": suffix.lstrip("."),
                    "explicitly_selected": True,
                },
                metadata={"source_bytes": resolved.stat().st_size},
            ),
        ),
    )


def _record(
    *,
    source_kind: str,
    source_key: str,
    title: str,
    text: str,
    provenance: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> NormalizedSource:
    return NormalizedSource(
        source_kind=source_kind,
        source_key=source_key,
        title=title,
        text=text,
        verification=VERIFICATION,
        content_hash=_sha256(text),
        provenance=dict(provenance),
        metadata=dict(metadata),
    )


def _ordinary_file(
    path: Path,
    *,
    enforce_size_limit: bool = True,
) -> tuple[Path, str]:
    try:
        if not os.path.lexists(path):
            return path, f"Explicit source was not found: {_redact_text(str(path))}"
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            return path, "Explicit source cannot be a symlink or reparse point"
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            return path, "Explicit source is not an ordinary file"
        if (
            enforce_size_limit
            and resolved.stat().st_size > MAX_SOURCE_BYTES
        ):
            return (
                path,
                f"Explicit source exceeds the {MAX_SOURCE_BYTES} byte limit",
            )
        return resolved, ""
    except OSError as exc:
        return path, f"Explicit source could not be inspected ({exc})"


def _read_text(path: Path) -> tuple[str | None, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"Explicit source could not be read ({exc})"
    if len(raw) > MAX_SOURCE_BYTES:
        return None, f"Explicit source exceeds the {MAX_SOURCE_BYTES} byte limit"
    return raw.decode("utf-8-sig", errors="replace"), ""


def _read_optional_pdf(path: Path) -> tuple[str | None, str]:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return None, "PDF was skipped because optional pypdf support is not installed"
    try:
        reader = PdfReader(str(path))
        return "\n\n".join(str(page.extract_text() or "") for page in reader.pages), ""
    except Exception as exc:
        return None, f"Optional PDF extraction failed ({exc})"


def _media_sidecar(media: Path) -> Path | None:
    candidates: list[Path] = []
    for suffix in (".srt", ".vtt", ".txt"):
        candidates.extend((media.with_suffix(suffix), Path(str(media) + suffix)))
    for candidate in candidates:
        if candidate == media:
            continue
        resolved, error = _ordinary_file(candidate)
        if not error:
            return resolved
    return None


def _is_public_message(message: Mapping[str, Any]) -> bool:
    role = str(message.get("role") or "").strip().casefold()
    if role not in {"user", "assistant"}:
        return False
    visibility = str(message.get("visibility") or "public").casefold()
    if visibility in {"hidden", "internal", "private"}:
        return False
    markers = {
        str(message.get(name) or "").strip().casefold()
        for name in ("type", "kind", "phase", "status", "channel")
    }
    if markers & _INTERNAL_MESSAGE_TYPES:
        return False
    if role == "user":
        return True
    return bool(
        message.get("is_final") is True
        or markers & _FINAL_MARKERS
    )


def _public_message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(_public_text_parts(message.get("content")))


def _public_text_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        block_type = str(value.get("type") or "text").casefold()
        if block_type in _INTERNAL_MESSAGE_TYPES:
            return []
        candidate = value.get("text", value.get("content", ""))
        if isinstance(candidate, Mapping):
            candidate = candidate.get("value", "")
        return _public_text_parts(candidate)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            parts.extend(_public_text_parts(item))
        return parts
    return []


def _html_to_text(text: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return text
    return "".join(parser.parts)


def _captions_to_text(text: str) -> str:
    output: list[str] = []
    timestamp = re.compile(
        r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.casefold() == "webvtt"
            or stripped.isdecimal()
            or timestamp.match(stripped)
            or stripped.startswith(("NOTE", "REGION", "STYLE"))
        ):
            continue
        cleaned = re.sub(r"<[^>]+>", "", stripped).strip()
        if cleaned:
            output.append(cleaned)
    return "\n".join(output)


def _normalize_text(text: str) -> str:
    value = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", value)
    ]
    return "\n\n".join(part for part in paragraphs if part)


def _redact_text(value: str) -> str:
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", str(value))
    text = re.sub(
        r"(?i)(token|secret|password|api[_-]?key)(\s*[:=]\s*)"
        r"[^\s,;\"']+",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:sk-(?:proj-)?|gh[pousr]_)[A-Za-z0-9_-]{12,}\b",
        "[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)[A-Z]:\\Users\\[^\\\s]+",
        r"%USERPROFILE%",
        text,
    )
    for name, secret in os.environ.items():
        if (
            re.search(r"(?i)(token|secret|password|api.?key)", name)
            and len(secret) >= 4
        ):
            text = text.replace(secret, "[REDACTED]")
    return text


def _identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text) is None
    ):
        raise SourceAdapterError(f"{field_name} is invalid")
    return text


def _optional_identifier(value: Any) -> str:
    text = str(value or "").strip()
    return _identifier(text, "identifier") if text else ""


def _bounded_text(value: Any, maximum: int) -> str:
    return _redact_text(str(value or "").strip())[:maximum]


def _tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence) or len(value) > 32:
        raise SourceAdapterError("memory tags must be an array of at most 32 items")
    output: list[str] = []
    for item in value:
        tag = _normalize_text(_redact_text(str(item)))
        if not tag or len(tag) > 128:
            raise SourceAdapterError("memory tag is invalid")
        if tag not in output:
            output.append(tag)
    return tuple(output)


def _path_source_key(source_kind: str, path: Path, source_id: str) -> str:
    identity = str(source_id or "").strip()
    if identity:
        if len(identity) > 512:
            raise SourceAdapterError("source_id is too long")
        normalized = identity.replace("\\", "/").strip("/")
        if (
            not normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise SourceAdapterError("source_id must be a relative stable identifier")
        return f"user:{source_kind}:{normalized}"
    return f"user:{source_kind}:sha256-{_sha256(str(path).casefold())[:32]}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "CAPTION_SUFFIXES",
    "find_media_sidecar",
    "MEDIA_SUFFIXES",
    "NormalizedSource",
    "OPTIONAL_SUFFIXES",
    "SourceAdapterError",
    "SourceBatch",
    "TEXT_SUFFIXES",
    "VERIFICATION",
    "normalize_project_memory",
    "normalize_selected_file",
    "normalize_thread_export",
]
