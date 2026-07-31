"""Deterministic, local-only extraction for user-selected documents and media.

The module is deliberately independent from the knowledge-index CLI.  Importing
it uses only the Python standard library.  PDF, OCR, and ASR dependencies are
loaded lazily, and their absence is represented as an actionable capability
state instead of an import-time failure.

No adapter summarizes content, calls a network service, or invokes Codex.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import importlib.util
import json
import os
import posixpath
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from xml.etree import ElementTree


TEXT_SUFFIXES = frozenset({".txt", ".md", ".html", ".htm", ".srt", ".vtt"})
OFFICE_SUFFIXES = frozenset({".docx", ".pptx", ".xlsx", ".csv"})
PDF_SUFFIXES = frozenset({".pdf"})
IMAGE_SUFFIXES = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)
AUDIO_SUFFIXES = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})
VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES
SUPPORTED_SUFFIXES = (
    TEXT_SUFFIXES
    | OFFICE_SUFFIXES
    | PDF_SUFFIXES
    | IMAGE_SUFFIXES
    | MEDIA_SUFFIXES
)

PROJECT_VENV_RELATIVE = ".venv"
PYPDF_PACKAGE_RELATIVE = ".venv/Lib/site-packages/pypdf"
RAPIDOCR_PACKAGE_RELATIVE = ".venv/Lib/site-packages/rapidocr"
RAPIDOCR_MODEL_RELATIVE = ".venv/Lib/site-packages/rapidocr/models"
ONNXRUNTIME_PACKAGE_RELATIVE = ".venv/Lib/site-packages/onnxruntime"
PDFIUM_PACKAGE_RELATIVE = ".venv/Lib/site-packages/pypdfium2"
FASTER_WHISPER_PACKAGE_RELATIVE = ".venv/Lib/site-packages/faster_whisper"
CTRANSLATE2_PACKAGE_RELATIVE = ".venv/Lib/site-packages/ctranslate2"
FFMPEG_EXECUTABLE_RELATIVE = ".runtime/dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE_EXECUTABLE_RELATIVE = ".runtime/dependencies/ffmpeg/bin/ffprobe.exe"
ASR_MODEL_RELATIVE = ".runtime/models/asr/faster-whisper"
ASR_MODEL_REQUIRED_FILES = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
)
EXTRACTOR_TEMP_RELATIVE = ".runtime/tmp/extractors"
EXTRACTOR_OUTPUT_RELATIVE = ".runtime/cache/extractors"
KNOWLEDGE_ENVIRONMENT_REPAIR_ACTION = (
    r".\scripts\hia-knowledge.ps1 environment-repair"
)
ASSET_REPAIR_ACTION = r".\scripts\hia-knowledge.ps1 assets repair"
EXTRACTION_CHECKPOINT_SCHEMA = "hia-local-extractor-checkpoint/1"
EXTRACTION_STATUS_READY = "ready"
EXTRACTION_STATUS_PARTIAL = "partial"
EXTRACTION_STATUS_FAILED = "failed"
MEDIA_SLICE_OVERLAP_SECONDS = 2.0
EXTRACTION_STATUSES = frozenset(
    {
        EXTRACTION_STATUS_READY,
        EXTRACTION_STATUS_PARTIAL,
        EXTRACTION_STATUS_FAILED,
    }
)

_ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_CAPTION_TIMING = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})(?:\s+.*)?$"
)


class ExtractionError(RuntimeError):
    """Raised for an invalid caller request or a deterministic adapter failure."""


@dataclass(frozen=True)
class ExtractionConfig:
    """Limits and local dependency configuration shared by all adapters."""

    project_root: str | os.PathLike[str] | None = None
    max_source_bytes: int = 128 * 1024 * 1024
    max_media_source_bytes: int = 512 * 1024 * 1024 * 1024
    max_archive_member_bytes: int = 64 * 1024 * 1024
    max_archive_total_bytes: int = 256 * 1024 * 1024
    max_fragment_chars: int = 24_000
    media_slice_seconds: float = 15 * 60.0
    max_media_slices_per_call: int = 1
    asr_model_path: str | os.PathLike[str] | None = None
    asr_language: str | None = None
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    ffmpeg_path: str | os.PathLike[str] | None = None
    ffprobe_path: str | os.PathLike[str] | None = None

    def __post_init__(self) -> None:
        if self.max_source_bytes <= 0:
            raise ValueError("max_source_bytes must be positive")
        if self.max_media_source_bytes <= 0:
            raise ValueError("max_media_source_bytes must be positive")
        if self.max_archive_member_bytes <= 0:
            raise ValueError("max_archive_member_bytes must be positive")
        if self.max_archive_total_bytes <= 0:
            raise ValueError("max_archive_total_bytes must be positive")
        if self.max_fragment_chars < 256:
            raise ValueError("max_fragment_chars must be at least 256")
        if self.media_slice_seconds <= 0:
            raise ValueError("media_slice_seconds must be positive")
        if self.max_media_slices_per_call <= 0:
            raise ValueError("max_media_slices_per_call must be positive")

    @property
    def resolved_project_root(self) -> Path:
        if self.project_root is not None:
            return Path(self.project_root).resolve()
        environment_root = os.environ.get("HIA_PROJECT_ROOT", "").strip()
        if environment_root:
            return Path(environment_root).resolve()
        return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ExtractionResult:
    """One bounded extraction unit.

    ``ready`` is terminal and may contain zero or more fragments.  ``partial``
    contains committed-unit candidates plus the only checkpoint a manager may
    persist for the next call.  ``failed`` is non-advancing: callers must keep
    their previously committed checkpoint and must not commit returned work.
    """

    status: str
    fragments: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    checkpoint: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in EXTRACTION_STATUSES:
            raise ValueError(f"Unsupported extraction status: {self.status}")
        if self.status == EXTRACTION_STATUS_PARTIAL and self.checkpoint is None:
            raise ValueError("partial extraction requires a checkpoint")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fragments": [
                {
                    "asset_id": fragment["asset_id"],
                    "fragment_id": fragment["fragment_id"],
                    "text": fragment["text"],
                    "locator": dict(fragment["locator"]),
                    "metadata": dict(fragment["metadata"]),
                }
                for fragment in self.fragments
            ],
            "warnings": list(self.warnings),
            "details": dict(self.details),
            "checkpoint": (
                dict(self.checkpoint) if self.checkpoint is not None else None
            ),
        }


class OcrBackend(Protocol):
    """Interface for a deterministic, local image OCR implementation."""

    name: str

    def capability(self) -> Mapping[str, Any]:
        """Return an actionable availability report."""

    def extract_text(self, path: Path) -> str:
        """Return source text without summarization."""

    def extract_image(self, image: Any) -> str:
        """Extract one in-memory page image without using system temp."""


class AsrBackend(Protocol):
    """Interface for deterministic, local, time-sliced media transcription."""

    name: str

    def capability(self) -> Mapping[str, Any]:
        """Return an actionable availability report."""

    def duration_seconds(self, path: Path) -> float:
        """Return media duration in seconds."""

    def transcribe_slice(
        self,
        path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> Iterable[Mapping[str, Any]]:
        """Yield source-relative transcript segments within the requested slice."""


class ExtractorAdapter(Protocol):
    """Small adapter contract used by :class:`LocalAssetExtractor`."""

    name: str
    suffixes: frozenset[str]

    def capability(self) -> Mapping[str, Any]:
        """Return an actionable availability report."""

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        """Extract one explicitly selected ordinary file."""


class _HTMLTextParser(HTMLParser):
    _BLOCK_TAGS = frozenset(
        {
            "blockquote",
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
            "pre",
            "section",
            "table",
            "td",
            "th",
            "tr",
        }
    )
    _HIDDEN_TAGS = frozenset({"script", "style", "template"})

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
        if folded in self._HIDDEN_TAGS:
            self.hidden_depth += 1
        elif not self.hidden_depth and folded in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if folded in self._HIDDEN_TAGS:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and folded in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


class PlainTextAdapter:
    """TXT, Markdown, HTML, SRT, and WebVTT extraction using stdlib only."""

    name = "stdlib_text"
    suffixes = TEXT_SUFFIXES

    def capability(self) -> Mapping[str, Any]:
        return _available_capability(
            self.name,
            self.suffixes,
            "Python standard library",
        )

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        text, encoding = _read_text_file(path, config)
        suffix = path.suffix.casefold()
        if suffix in {".srt", ".vtt"}:
            return _extract_captions(path, asset_id, text, encoding, config)
        if suffix in {".html", ".htm"}:
            parser = _HTMLTextParser()
            try:
                parser.feed(text)
                parser.close()
            except Exception as exc:
                return _failure(self.name, path, f"HTML parsing failed: {exc}")
            text = "".join(parser.parts)
        return _text_result(
            path,
            asset_id,
            text,
            config,
            locator={"type": "text"},
            metadata={"adapter": self.name, "encoding": encoding},
        )


class CsvAdapter:
    """CSV rows extracted deterministically with the standard csv module."""

    name = "stdlib_csv"
    suffixes = frozenset({".csv"})

    def capability(self) -> Mapping[str, Any]:
        return _available_capability(
            self.name,
            self.suffixes,
            "Python standard library",
        )

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        text, encoding = _read_text_file(path, config)
        fragments: list[dict[str, Any]] = []
        try:
            rows = csv.reader(text.splitlines())
            for row_number, row in enumerate(rows, start=1):
                row_text = "\t".join(_normalize_text(cell) for cell in row).strip()
                if not row_text:
                    continue
                fragments.extend(
                    _fragments_for_text(
                        asset_id,
                        row_text,
                        {"type": "row", "row": row_number},
                        {
                            "adapter": self.name,
                            "format": "csv",
                            "source_name": path.name,
                            "encoding": encoding,
                            "columns": len(row),
                        },
                        config.max_fragment_chars,
                    )
                )
        except csv.Error as exc:
            return _failure(self.name, path, f"CSV parsing failed: {exc}")
        return _result_from_fragments(self.name, path, fragments)


class DocxAdapter:
    """DOCX paragraph extraction directly from the OOXML package."""

    name = "stdlib_docx"
    suffixes = frozenset({".docx"})

    def capability(self) -> Mapping[str, Any]:
        return _available_capability(
            self.name,
            self.suffixes,
            "Python standard-library ZIP and XML parsers",
        )

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        try:
            with _open_ooxml(path, config) as archive:
                document = _read_archive_member(
                    archive,
                    "word/document.xml",
                    config,
                )
            root = ElementTree.fromstring(document)
        except (ExtractionError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
            return _failure(self.name, path, f"DOCX extraction failed: {exc}")

        word_namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        fragments: list[dict[str, Any]] = []
        paragraph_number = 0
        for paragraph in root.iter(f"{word_namespace}p"):
            paragraph_number += 1
            parts: list[str] = []
            for element in paragraph.iter():
                if element.tag == f"{word_namespace}t":
                    parts.append(element.text or "")
                elif element.tag == f"{word_namespace}tab":
                    parts.append("\t")
                elif element.tag in {
                    f"{word_namespace}br",
                    f"{word_namespace}cr",
                }:
                    parts.append("\n")
            text = _normalize_text("".join(parts))
            if not text:
                continue
            fragments.extend(
                _fragments_for_text(
                    asset_id,
                    text,
                    {"type": "paragraph", "paragraph": paragraph_number},
                    {
                        "adapter": self.name,
                        "format": "docx",
                        "source_name": path.name,
                    },
                    config.max_fragment_chars,
                )
            )
        return _result_from_fragments(self.name, path, fragments)


class PptxAdapter:
    """PPTX text extraction with one semantic fragment group per slide."""

    name = "stdlib_pptx"
    suffixes = frozenset({".pptx"})
    _SLIDE_NAME = re.compile(r"^ppt/slides/slide(\d+)\.xml$")

    def capability(self) -> Mapping[str, Any]:
        return _available_capability(
            self.name,
            self.suffixes,
            "Python standard-library ZIP and XML parsers",
        )

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        fragments: list[dict[str, Any]] = []
        drawing_namespace = (
            "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        )
        try:
            with _open_ooxml(path, config) as archive:
                slides = _pptx_slides(archive, config, self._SLIDE_NAME)
                for slide_number, member in enumerate(slides, start=1):
                    root = ElementTree.fromstring(
                        _read_archive_member(archive, member, config)
                    )
                    paragraphs: list[str] = []
                    for paragraph in root.iter(f"{drawing_namespace}p"):
                        runs = [
                            element.text or ""
                            for element in paragraph.iter(f"{drawing_namespace}t")
                        ]
                        text = _normalize_text("".join(runs))
                        if text:
                            paragraphs.append(text)
                    text = "\n".join(paragraphs)
                    if not text:
                        continue
                    fragments.extend(
                        _fragments_for_text(
                            asset_id,
                            text,
                            {"type": "slide", "slide": slide_number},
                            {
                                "adapter": self.name,
                                "format": "pptx",
                                "source_name": path.name,
                            },
                            config.max_fragment_chars,
                        )
                    )
        except (
            ExtractionError,
            ElementTree.ParseError,
            zipfile.BadZipFile,
        ) as exc:
            return _failure(self.name, path, f"PPTX extraction failed: {exc}")
        return _result_from_fragments(self.name, path, fragments)


class XlsxAdapter:
    """XLSX row extraction directly from worksheet OOXML."""

    name = "stdlib_xlsx"
    suffixes = frozenset({".xlsx"})

    def capability(self) -> Mapping[str, Any]:
        return _available_capability(
            self.name,
            self.suffixes,
            "Python standard-library ZIP and XML parsers",
        )

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        try:
            with _open_ooxml(path, config) as archive:
                shared_strings = _xlsx_shared_strings(archive, config)
                sheets = _xlsx_sheets(archive, config)
                fragments = _xlsx_fragments(
                    archive,
                    sheets,
                    shared_strings,
                    path,
                    asset_id,
                    config,
                )
        except (
            ExtractionError,
            ElementTree.ParseError,
            ValueError,
            zipfile.BadZipFile,
        ) as exc:
            return _failure(self.name, path, f"XLSX extraction failed: {exc}")
        return _result_from_fragments(self.name, path, fragments)


class PypdfAdapter:
    """Optional PDF adapter emitting page-addressable fragments."""

    name = "pypdf"
    suffixes = PDF_SUFFIXES

    def __init__(
        self,
        config: ExtractionConfig | None = None,
        ocr_backend: OcrBackend | None = None,
    ) -> None:
        self.config = config or ExtractionConfig()
        self.ocr_backend = ocr_backend

    def capability(self) -> Mapping[str, Any]:
        if _module_available("pypdf"):
            report = _available_capability(
                self.name,
                self.suffixes,
                "pypdf text extraction with optional page-image OCR",
            )
        else:
            report = _missing_capability(
                self.name,
                self.suffixes,
                "Python package 'pypdf' is not installed.",
                "Use Launcher Knowledge Environment repair, or run "
                f"{KNOWLEDGE_ENVIRONMENT_REPAIR_ACTION}.",
                dependencies=("pypdf",),
            )
        return {
            **report,
            "page_ocr": _pdf_page_ocr_capability(
                self.config,
                self.ocr_backend,
            ),
        }

    def _page_text(
        self,
        path: Path,
        page_number: int,
        page: Any,
        config: ExtractionConfig,
    ) -> tuple[str, str, tuple[str, ...]]:
        warnings: list[str] = []
        try:
            text = str(page.extract_text() or "")
        except Exception as exc:
            text = ""
            warnings.append(
                f"PDF page {page_number} text extraction failed: {exc}"
            )
        if _normalize_text(text):
            return text, "pdf_text", tuple(warnings)

        page_ocr = _pdf_page_ocr_capability(config, self.ocr_backend)
        if page_ocr["status"] != "available" or self.ocr_backend is None:
            warnings.append(
                f"PDF page {page_number} has no embedded text; page OCR is "
                f"{page_ocr['status']}: {page_ocr['message']} "
                f"Repair: {page_ocr['action']}"
            )
            return "", "unavailable", tuple(warnings)
        try:
            image = _render_pdf_page(
                path,
                page_number,
            )
            text = self.ocr_backend.extract_image(image)
        except Exception as exc:
            warnings.append(f"PDF page {page_number} OCR failed: {exc}")
            return "", "failed", tuple(warnings)
        return text, "rapidocr", tuple(warnings)

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        capability = self.capability()
        if capability["status"] != "available":
            return _dependency_result(capability, path)
        try:
            pypdf = importlib.import_module("pypdf")
            reader = pypdf.PdfReader(str(path))
            fragments: list[dict[str, Any]] = []
            warnings: list[str] = []
            for page_number, page in enumerate(reader.pages, start=1):
                text, extraction, page_warnings = self._page_text(
                    path,
                    page_number,
                    page,
                    config,
                )
                warnings.extend(page_warnings)
                fragments.extend(
                    _fragments_for_text(
                        asset_id,
                        text,
                        {"type": "page", "page": page_number},
                        {
                            "adapter": self.name,
                            "format": "pdf",
                            "source_name": path.name,
                            "extraction": extraction,
                        },
                        config.max_fragment_chars,
                    )
                )
        except Exception as exc:
            return _failure(self.name, path, f"PDF extraction failed: {exc}")
        result = _result_from_fragments(
            self.name,
            path,
            fragments,
            warnings=warnings,
        )
        return ExtractionResult(
            status=result.status,
            fragments=result.fragments,
            warnings=result.warnings,
            details={**result.details, "pages": len(reader.pages)},
        )


class RapidOcrBackend:
    """Single local OCR backend using wheel-bundled PP-OCRv6 small models."""

    name = "rapidocr"
    _MODEL_FILES = (
        "PP-OCRv6_det_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "PP-OCRv6_rec_small.onnx",
    )

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config
        self._engine: Any = None

    def capability(self) -> Mapping[str, Any]:
        missing = [
            module
            for module in ("rapidocr", "onnxruntime")
            if not _module_available(module)
        ]
        if missing:
            return _missing_capability(
                self.name,
                IMAGE_SUFFIXES,
                "Local OCR Python dependencies are missing: "
                + ", ".join(missing),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                dependencies=tuple(missing),
            )
        package_roots = {
            module: _module_root(module)
            for module in ("rapidocr", "onnxruntime")
        }
        project_venv = (
            self.config.resolved_project_root / PROJECT_VENV_RELATIVE
        )
        outside_venv = tuple(
            module
            for module, root in package_roots.items()
            if root is None or not _is_within(root, project_venv)
        )
        if outside_venv:
            return _missing_capability(
                self.name,
                IMAGE_SUFFIXES,
                "OCR packages are outside the project .venv: "
                + ", ".join(outside_venv),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                status="invalid_configuration",
                dependencies=outside_venv,
            )
        package_root = package_roots["rapidocr"]
        if package_root is None:
            raise AssertionError("RapidOCR package root was not resolved")
        model_root = package_root / "models"
        missing_models = tuple(
            name for name in self._MODEL_FILES if not (model_root / name).is_file()
        )
        if missing_models:
            return _missing_capability(
                self.name,
                IMAGE_SUFFIXES,
                "RapidOCR wheel models are missing: " + ", ".join(missing_models),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                dependencies=missing_models,
            )
        return {
            **_available_capability(
                self.name,
                IMAGE_SUFFIXES,
                "RapidOCR + ONNX Runtime with wheel-bundled PP-OCRv6 small",
            ),
            "package_path": str(package_root),
            "model_path": str(model_root),
            "languages": ["Chinese", "English"],
            "model_family": "PP-OCRv6 small",
            "network_downloads": False,
            "quality_limits": [
                "Accuracy depends on resolution, contrast, deskew, and document "
                "layout.",
                "Handwriting, dense equations, diagrams, and complex reading "
                "order are not guaranteed.",
                "OCR returns evidence text only; it does not summarize or infer.",
            ],
        }

    def extract_text(self, path: Path) -> str:
        return self.extract_image(str(path))

    def extract_image(self, image: Any) -> str:
        capability = self.capability()
        if capability["status"] != "available":
            raise ExtractionError(str(capability["message"]))
        if self._engine is None:
            rapidocr = importlib.import_module("rapidocr")
            self._engine = rapidocr.RapidOCR()
        try:
            result = self._engine(image)
            texts = getattr(result, "txts", ()) or ()
        except Exception as exc:
            raise ExtractionError(f"Local RapidOCR failed: {exc}") from exc
        return "\n".join(
            text for value in texts if (text := _normalize_text(str(value)))
        )


class ImageOcrAdapter:
    """Image adapter backed by an injected or default local OCR backend."""

    name = "image_ocr"
    suffixes = IMAGE_SUFFIXES

    def __init__(self, backend: OcrBackend) -> None:
        self.backend = backend

    def capability(self) -> Mapping[str, Any]:
        report = dict(self.backend.capability())
        return {
            **report,
            "name": self.name,
            "backend": self.backend.name,
            "formats": sorted(self.suffixes),
        }

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        _reject_checkpoint(checkpoint)
        capability = self.capability()
        if capability.get("status") != "available":
            return _dependency_result(capability, path)
        try:
            text = self.backend.extract_text(path)
        except Exception as exc:
            return _failure(self.name, path, f"Image OCR failed: {exc}")
        return _text_result(
            path,
            asset_id,
            text,
            config,
            locator={"type": "image"},
            metadata={
                "adapter": self.name,
                "backend": self.backend.name,
                "languages": ["Chinese", "English"],
            },
        )


class FasterWhisperBackend:
    """Optional local faster-whisper backend with FFmpeg pipe slicing."""

    name = "faster_whisper"

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config
        self._model: Any = None

    def capability(self) -> Mapping[str, Any]:
        missing = [
            module
            for module in ("faster_whisper", "ctranslate2", "numpy")
            if not _module_available(module)
        ]
        if missing:
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "Local ASR Python dependencies are missing: "
                + ", ".join(missing),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                dependencies=tuple(missing),
            )
        project_venv = (
            self.config.resolved_project_root / PROJECT_VENV_RELATIVE
        )
        outside_venv = tuple(
            module
            for module in ("faster_whisper", "ctranslate2", "numpy")
            if (
                (root := _module_root(module)) is None
                or not _is_within(root, project_venv)
            )
        )
        if outside_venv:
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "ASR packages are outside the project .venv: "
                + ", ".join(outside_venv),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                status="invalid_configuration",
                dependencies=outside_venv,
            )
        model_path = _configured_path(
            self.config.asr_model_path or ASR_MODEL_RELATIVE,
            self.config.resolved_project_root,
        )
        if model_path is None or not model_path.is_dir():
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "A local faster-whisper model directory is not configured.",
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                status="not_configured",
                dependencies=("local faster-whisper model",),
            )
        missing_model_files = [
            name
            for name in ASR_MODEL_REQUIRED_FILES
            if not (model_path / name).is_file()
        ]
        if not any(
            (model_path / name).is_file()
            for name in ("vocabulary.json", "vocabulary.txt")
        ):
            missing_model_files.append("vocabulary.json|vocabulary.txt")
        if missing_model_files:
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "The local faster-whisper model is incomplete: "
                + ", ".join(missing_model_files),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION} to resume the model download.",
                status="not_configured",
                dependencies=tuple(missing_model_files),
            )
        runtime_root = self.config.resolved_project_root / ".runtime"
        if not _is_within(model_path, runtime_root):
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "The ASR model is outside the project .runtime directory.",
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}; clear an unsupported custom "
                "asr_model_path to select the managed project model.",
                status="invalid_configuration",
                dependencies=("project-runtime faster-whisper model",),
            )
        ffmpeg = _configured_executable(
            self.config.ffmpeg_path,
            self.config.resolved_project_root,
            (
                FFMPEG_EXECUTABLE_RELATIVE,
                ".runtime/dependencies/ffmpeg/bin/ffmpeg",
            ),
        )
        ffprobe = _configured_executable(
            self.config.ffprobe_path,
            self.config.resolved_project_root,
            (
                FFPROBE_EXECUTABLE_RELATIVE,
                ".runtime/dependencies/ffmpeg/bin/ffprobe",
            ),
        )
        if ffmpeg is None or ffprobe is None:
            missing_tools = []
            if ffmpeg is None:
                missing_tools.append("ffmpeg")
            if ffprobe is None:
                missing_tools.append("ffprobe")
            return _missing_capability(
                self.name,
                MEDIA_SUFFIXES,
                "Project-local media tools are missing: "
                + ", ".join(missing_tools),
                "Use Launcher Knowledge Assets repair, or run "
                f"{ASSET_REPAIR_ACTION}.",
                status="not_configured",
                dependencies=tuple(missing_tools),
            )
        return {
            **_available_capability(
                self.name,
                MEDIA_SUFFIXES,
                "faster-whisper with a local model and project-local FFmpeg",
            ),
            "model_path": str(model_path),
            "ffmpeg_path": str(ffmpeg),
            "ffprobe_path": str(ffprobe),
            "device": self.config.asr_device,
            "compute_type": self.config.asr_compute_type,
            "checkpoint_schema": EXTRACTION_CHECKPOINT_SCHEMA,
            "slice_seconds": self.config.media_slice_seconds,
            "slice_overlap_seconds": MEDIA_SLICE_OVERLAP_SECONDS,
            "beam_size": 5,
            "cpu_threads": "ctranslate2_default",
            "network_downloads": False,
            "bounded_decode_reason": (
                "PyAV bundles FFmpeg, but standard decode_audio decodes the "
                "whole asset. Project-runtime ffmpeg/ffprobe decode only one "
                "overlapped checkpoint slice to bound hour-scale memory."
            ),
        }

    def duration_seconds(self, path: Path) -> float:
        capability = self.capability()
        if capability["status"] != "available":
            raise ExtractionError(str(capability["message"]))
        command = [
            str(capability["ffprobe_path"]),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        completed = _run_local_process(command, text_output=True)
        try:
            payload = json.loads(completed.stdout)
            duration = float(payload["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExtractionError(
                "ffprobe did not return a valid media duration"
            ) from exc
        if duration <= 0:
            raise ExtractionError("ffprobe returned a non-positive duration")
        return duration

    def transcribe_slice(
        self,
        path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> Iterable[Mapping[str, Any]]:
        capability = self.capability()
        if capability["status"] != "available":
            raise ExtractionError(str(capability["message"]))
        duration = max(0.0, end_seconds - start_seconds)
        command = [
            str(capability["ffmpeg_path"]),
            "-v",
            "error",
            "-ss",
            _decimal_seconds(start_seconds),
            "-t",
            _decimal_seconds(duration),
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ]
        completed = _run_local_process(command, text_output=False)
        numpy = importlib.import_module("numpy")
        audio = numpy.frombuffer(completed.stdout, dtype=numpy.float32)
        if not audio.size:
            return ()

        if self._model is None:
            faster_whisper = importlib.import_module("faster_whisper")
            self._model = faster_whisper.WhisperModel(
                str(capability["model_path"]),
                device=self.config.asr_device,
                compute_type=self.config.asr_compute_type,
                cpu_threads=0,
                num_workers=1,
                local_files_only=True,
            )
        segments, _info = self._model.transcribe(
            audio,
            language=self.config.asr_language,
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        output: list[dict[str, Any]] = []
        for segment in segments:
            output.append(
                {
                    "text": str(segment.text or ""),
                    "start_seconds": start_seconds + float(segment.start),
                    "end_seconds": start_seconds + float(segment.end),
                    "metadata": {
                        "average_log_probability": getattr(
                            segment,
                            "avg_logprob",
                            None,
                        )
                    },
                }
            )
        return output


class MediaAsrAdapter:
    """Checkpointed media extraction processing bounded time slices per call."""

    name = "media_asr"
    suffixes = MEDIA_SUFFIXES

    def __init__(self, backend: AsrBackend) -> None:
        self.backend = backend

    def capability(self) -> Mapping[str, Any]:
        report = dict(self.backend.capability())
        return {
            **report,
            "name": self.name,
            "backend": self.backend.name,
            "formats": sorted(self.suffixes),
        }

    def extract(
        self,
        path: Path,
        asset_id: str,
        config: ExtractionConfig,
        checkpoint: Mapping[str, Any] | None,
    ) -> ExtractionResult:
        capability = self.capability()
        if capability.get("status") != "available":
            return _dependency_result(capability, path)
        fingerprint = _source_fingerprint(path)
        try:
            duration = float(self.backend.duration_seconds(path))
        except Exception as exc:
            return _failure(self.name, path, f"Media duration probe failed: {exc}")
        if duration <= 0:
            return _failure(
                self.name,
                path,
                "Media duration probe returned a non-positive duration",
            )

        try:
            state = _media_checkpoint(
                checkpoint,
                asset_id,
                fingerprint,
                duration,
            )
        except ExtractionError as exc:
            return ExtractionResult(
                status=EXTRACTION_STATUS_FAILED,
                warnings=(str(exc),),
                details={
                    "adapter": self.name,
                    "source_name": path.name,
                    "failure_reason": "invalid_checkpoint",
                },
            )

        start = float(state["next_start_seconds"])
        slice_index = int(state["next_slice_index"])
        if bool(state["complete"]) or start >= duration:
            complete_checkpoint = {
                **state,
                "complete": True,
                "next_start_seconds": _round_seconds(duration),
            }
            return ExtractionResult(
                status=EXTRACTION_STATUS_READY,
                details={
                    "adapter": self.name,
                    "source_name": path.name,
                    "duration_seconds": _round_seconds(duration),
                    "slices_processed": 0,
                },
                checkpoint=complete_checkpoint,
            )

        fragments: list[dict[str, Any]] = []
        warnings: list[str] = []
        slices_processed = 0
        while (
            start < duration
            and slices_processed < config.max_media_slices_per_call
        ):
            end = min(duration, start + config.media_slice_seconds)
            decode_start = max(0.0, start - MEDIA_SLICE_OVERLAP_SECONDS)
            decode_end = min(
                duration,
                end + MEDIA_SLICE_OVERLAP_SECONDS,
            )
            try:
                segments = tuple(
                    self.backend.transcribe_slice(
                        path,
                        decode_start,
                        decode_end,
                    )
                )
            except Exception as exc:
                return ExtractionResult(
                    status=EXTRACTION_STATUS_FAILED,
                    fragments=(),
                    warnings=tuple(
                        [
                            *warnings,
                            "Media ASR failed for "
                            f"{_decimal_seconds(start)}-"
                            f"{_decimal_seconds(end)} seconds: {exc}",
                        ]
                    ),
                    details={
                        "adapter": self.name,
                        "source_name": path.name,
                        "duration_seconds": _round_seconds(duration),
                        "slices_processed": 0,
                        "failure_reason": "asr_slice_failed",
                        "discarded_fragments": len(fragments),
                    },
                    checkpoint=state,
                )
            for segment_index, segment in enumerate(segments, start=1):
                text = _normalize_text(str(segment.get("text") or ""))
                if not text:
                    continue
                decoded_segment_start = _clamp_seconds(
                    segment.get("start_seconds"),
                    decode_start,
                    decode_end,
                    decode_start,
                )
                decoded_segment_end = _clamp_seconds(
                    segment.get("end_seconds"),
                    decoded_segment_start,
                    decode_end,
                    decode_end,
                )
                midpoint = (
                    decoded_segment_start + decoded_segment_end
                ) / 2.0
                if midpoint < start or (
                    midpoint >= end and end < duration
                ):
                    continue
                segment_start = max(start, decoded_segment_start)
                segment_end = min(end, decoded_segment_end)
                metadata = segment.get("metadata")
                segment_metadata = (
                    dict(metadata) if isinstance(metadata, Mapping) else {}
                )
                fragments.extend(
                    _fragments_for_text(
                        asset_id,
                        text,
                        {
                            "type": "time_range",
                            "start_seconds": _round_seconds(segment_start),
                            "end_seconds": _round_seconds(segment_end),
                            "slice_index": slice_index,
                            "segment": segment_index,
                        },
                        {
                            "adapter": self.name,
                            "backend": self.backend.name,
                            "format": path.suffix.casefold().lstrip("."),
                            "source_name": path.name,
                            "slice_start_seconds": _round_seconds(start),
                            "slice_end_seconds": _round_seconds(end),
                            "decode_start_seconds": _round_seconds(decode_start),
                            "decode_end_seconds": _round_seconds(decode_end),
                            **segment_metadata,
                        },
                        config.max_fragment_chars,
                    )
                )
            start = end
            slice_index += 1
            slices_processed += 1

        complete = start >= duration
        next_checkpoint = {
            "schema": EXTRACTION_CHECKPOINT_SCHEMA,
            "version": 1,
            "asset_id": asset_id,
            "source_fingerprint": fingerprint,
            "duration_seconds": _round_seconds(duration),
            "next_start_seconds": _round_seconds(
                duration if complete else start
            ),
            "next_slice_index": slice_index,
            "complete": complete,
        }
        return ExtractionResult(
            status=(
                EXTRACTION_STATUS_READY
                if complete
                else EXTRACTION_STATUS_PARTIAL
            ),
            fragments=tuple(fragments),
            warnings=tuple(warnings),
            details={
                "adapter": self.name,
                "source_name": path.name,
                "duration_seconds": _round_seconds(duration),
                "slices_processed": slices_processed,
            },
            checkpoint=next_checkpoint,
        )


class LocalAssetExtractor:
    """Dispatch explicitly selected files to deterministic local adapters."""

    name = "local_asset"

    def __init__(
        self,
        config: ExtractionConfig | None = None,
        *,
        adapters: Sequence[ExtractorAdapter] | None = None,
        ocr_backend: OcrBackend | None = None,
        asr_backend: AsrBackend | None = None,
    ) -> None:
        self.config = config or ExtractionConfig()
        if adapters is None:
            effective_ocr = ocr_backend or RapidOcrBackend(self.config)
            effective_asr = asr_backend or FasterWhisperBackend(self.config)
            adapters = (
                PlainTextAdapter(),
                CsvAdapter(),
                DocxAdapter(),
                PptxAdapter(),
                XlsxAdapter(),
                PypdfAdapter(self.config, effective_ocr),
                ImageOcrAdapter(effective_ocr),
                MediaAsrAdapter(effective_asr),
            )
        suffixes: dict[str, ExtractorAdapter] = {}
        for adapter in adapters:
            for suffix in adapter.suffixes:
                folded = str(suffix).casefold()
                if folded in suffixes:
                    raise ValueError(
                        f"Duplicate extractor for {folded}: "
                        f"{suffixes[folded].name} and {adapter.name}"
                    )
                suffixes[folded] = adapter
        self._adapters = tuple(adapters)
        self._suffixes = suffixes

    def supports(self, path: str | os.PathLike[str]) -> bool:
        """Return whether a registered adapter owns the selected suffix."""

        return Path(path).suffix.casefold() in self._suffixes

    def capabilities(self) -> dict[str, Any]:
        """Report native and optional format support without loading heavy code."""

        capabilities: dict[str, dict[str, Any]] = {}
        formats: dict[str, dict[str, Any]] = {}
        for adapter in self._adapters:
            report = dict(adapter.capability())
            report.setdefault("name", adapter.name)
            report.setdefault("formats", sorted(adapter.suffixes))
            capabilities[adapter.name] = report
            for suffix in adapter.suffixes:
                formats[suffix] = {
                    "adapter": adapter.name,
                    "status": str(report.get("status") or "unknown"),
                    "action": str(report.get("action") or ""),
                }
        return {
            "local_only": True,
            "summarization": False,
            "network_access": False,
            "capabilities": capabilities,
            "formats": dict(sorted(formats.items())),
        }

    def extract(
        self,
        path: str | os.PathLike[str],
        *,
        asset_id: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        """Extract one ordinary local file without writing any artifacts."""

        selected = Path(path)
        try:
            resolved = _ordinary_file(selected, self.config)
        except ExtractionError as exc:
            return ExtractionResult(
                status=EXTRACTION_STATUS_FAILED,
                warnings=(str(exc),),
                details={
                    "source_name": selected.name,
                    "failure_reason": "unavailable",
                },
            )
        suffix = resolved.suffix.casefold()
        adapter = self._suffixes.get(suffix)
        if adapter is None:
            return ExtractionResult(
                status=EXTRACTION_STATUS_FAILED,
                warnings=(f"Unsupported local asset type: {suffix or '<none>'}",),
                details={
                    "source_name": resolved.name,
                    "suffix": suffix,
                    "supported_suffixes": sorted(self._suffixes),
                    "failure_reason": "unsupported",
                },
            )
        try:
            stable_asset_id = _asset_id(resolved, asset_id)
            result = adapter.extract(
                resolved,
                stable_asset_id,
                self.config,
                checkpoint,
            )
            _validate_fragments(result.fragments)
            return result
        except ExtractionError as exc:
            return ExtractionResult(
                status="failed",
                warnings=(str(exc),),
                details={
                    "adapter": adapter.name,
                    "source_name": resolved.name,
                    "failure_reason": "invalid_request",
                },
            )
        except Exception as exc:
            return ExtractionResult(
                status="failed",
                warnings=(
                    f"{adapter.name} extraction failed: "
                    f"{type(exc).__name__}: {exc}",
                ),
                details={
                    "adapter": adapter.name,
                    "source_name": resolved.name,
                    "failure_reason": "unexpected_error",
                },
            )


def capability_report(
    config: ExtractionConfig | None = None,
    *,
    ocr_backend: OcrBackend | None = None,
    asr_backend: AsrBackend | None = None,
) -> dict[str, Any]:
    """Convenience function for UI, service, and preflight callers."""

    return LocalAssetExtractor(
        config,
        ocr_backend=ocr_backend,
        asr_backend=asr_backend,
    ).capabilities()


def dependency_contract(
    config: ExtractionConfig | None = None,
    *,
    ocr_backend: OcrBackend | None = None,
    asr_backend: AsrBackend | None = None,
) -> dict[str, Any]:
    """Return the stable Launcher/core install and integration contract."""

    effective = config or ExtractionConfig()
    report = capability_report(
        effective,
        ocr_backend=ocr_backend,
        asr_backend=asr_backend,
    )
    capabilities = report["capabilities"]
    pdf_capability = dict(capabilities["pypdf"])
    ocr_capability = dict(capabilities["image_ocr"])
    asr_capability = dict(capabilities["media_asr"])
    return {
        "schema": "hia-local-extractor-dependencies/1",
        "installer_owner": "Launcher",
        "runtime_installs_dependencies": False,
        "network_downloads": False,
        "python_environment": PROJECT_VENV_RELATIVE,
        "runtime_paths": {
            "temporary": EXTRACTOR_TEMP_RELATIVE,
            "output": EXTRACTOR_OUTPUT_RELATIVE,
        },
        "backends": {
            "pdf_text": {
                "adapter": "pypdf",
                "packages": [
                    {
                        "distribution": "pypdf",
                        "module": "pypdf",
                        "relative_path": PYPDF_PACKAGE_RELATIVE,
                    }
                ],
                "capability": pdf_capability,
                "repair_action": str(pdf_capability.get("action") or ""),
            },
            "ocr": {
                "backend": "rapidocr",
                "packages": [
                    {
                        "distribution": "rapidocr",
                        "module": "rapidocr",
                        "relative_path": RAPIDOCR_PACKAGE_RELATIVE,
                    },
                    {
                        "distribution": "onnxruntime",
                        "module": "onnxruntime",
                        "relative_path": ONNXRUNTIME_PACKAGE_RELATIVE,
                    },
                    {
                        "distribution": "pypdfium2",
                        "module": "pypdfium2",
                        "relative_path": PDFIUM_PACKAGE_RELATIVE,
                    },
                ],
                "install_command": ASSET_REPAIR_ACTION,
                "model_family": "PP-OCRv6 small",
                "model_directory": RAPIDOCR_MODEL_RELATIVE,
                "languages": ["Chinese", "English"],
                "pdf_pages": {
                    "renderer": "pypdfium2",
                    "dpi": 300,
                    "in_memory": True,
                },
                "capability": ocr_capability,
                "pdf_page_capability": dict(
                    pdf_capability.get("page_ocr") or {}
                ),
                "repair_action": str(ocr_capability.get("action") or ""),
                "quality_limits": [
                    "Low resolution, blur, skew, unusual fonts, handwriting, "
                    "equations, diagrams, and complex reading order can reduce "
                    "accuracy.",
                    "The backend emits source evidence only and performs no "
                    "summary or inference.",
                ],
            },
            "asr": {
                "backend": "faster_whisper",
                "packages": [
                    {
                        "distribution": "faster-whisper",
                        "module": "faster_whisper",
                        "relative_path": FASTER_WHISPER_PACKAGE_RELATIVE,
                    },
                    {
                        "distribution": "ctranslate2",
                        "module": "ctranslate2",
                        "relative_path": CTRANSLATE2_PACKAGE_RELATIVE,
                    },
                ],
                "install_command": ASSET_REPAIR_ACTION,
                "executables": {
                    "ffmpeg": FFMPEG_EXECUTABLE_RELATIVE,
                    "ffprobe": FFPROBE_EXECUTABLE_RELATIVE,
                },
                "model_directory": ASR_MODEL_RELATIVE,
                "defaults": {
                    "device": "cpu",
                    "compute_type": "int8",
                    "slice_seconds": effective.media_slice_seconds,
                    "slice_overlap_seconds": MEDIA_SLICE_OVERLAP_SECONDS,
                    "max_slices_per_call": effective.max_media_slices_per_call,
                    "beam_size": 5,
                    "cpu_threads": "ctranslate2_default",
                    "num_workers": 1,
                },
                "bounded_decode_reason": (
                    "faster-whisper's PyAV dependency bundles FFmpeg, but its "
                    "standard decode_audio path decodes a whole asset. The "
                    "project-runtime ffmpeg/ffprobe pair decodes one checkpointed "
                    "time slice to bound memory for hour-scale media."
                ),
                "capability": asr_capability,
                "repair_action": str(asr_capability.get("action") or ""),
            },
        },
        "manager_integration": {
            "extractor": "LocalAssetExtractor",
            "single_file_parser": True,
            "call": (
                "extract(path, asset_id=asset_id, checkpoint=checkpoint)"
            ),
            "result_fields": [
                "status",
                "fragments",
                "warnings",
                "details",
                "checkpoint",
            ],
            "fragment_fields": [
                "asset_id",
                "fragment_id",
                "text",
                "locator",
                "metadata",
            ],
            "statuses": {
                EXTRACTION_STATUS_READY: (
                    "terminal; commit fragments and terminal checkpoint together"
                ),
                EXTRACTION_STATUS_PARTIAL: (
                    "non-terminal; atomically commit fragments and checkpoint, "
                    "then call again"
                ),
                EXTRACTION_STATUS_FAILED: (
                    "non-advancing; commit nothing and retain the previously "
                    "persisted checkpoint"
                ),
            },
            "checkpoint_schema": EXTRACTION_CHECKPOINT_SCHEMA,
            "checkpoint_fields": [
                "schema",
                "version",
                "asset_id",
                "source_fingerprint",
                "duration_seconds",
                "next_start_seconds",
                "next_slice_index",
                "complete",
            ],
            "persistence_owner": "KnowledgeAssetManager",
            "database_owner": "KnowledgeAssetManager",
        },
    }


def _read_text_file(
    path: Path,
    config: ExtractionConfig,
) -> tuple[str, str]:
    raw = _read_bounded(path, config.max_source_bytes)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replacement"


def _extract_captions(
    path: Path,
    asset_id: str,
    text: str,
    encoding: str,
    config: ExtractionConfig,
) -> ExtractionResult:
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    fragments: list[dict[str, Any]] = []
    cue_number = 0
    index = 0
    while index < len(blocks):
        block = blocks[index].strip()
        index += 1
        if not block:
            continue
        lines = block.splitlines()
        marker = lines[0].strip().casefold()
        if marker == "webvtt":
            lines = lines[1:]
            if not lines:
                continue
        if marker.startswith(("note", "region", "style")):
            continue
        timing_index = next(
            (
                line_index
                for line_index, line in enumerate(lines[:2])
                if _CAPTION_TIMING.match(line)
            ),
            -1,
        )
        if timing_index < 0:
            continue
        match = _CAPTION_TIMING.match(lines[timing_index])
        if match is None:
            continue
        cue_text = _caption_text("\n".join(lines[timing_index + 1 :]))
        if not cue_text:
            continue
        cue_number += 1
        start = _caption_seconds(match.group("start"))
        end = _caption_seconds(match.group("end"))
        fragments.extend(
            _fragments_for_text(
                asset_id,
                cue_text,
                {
                    "type": "time_range",
                    "start_seconds": _round_seconds(start),
                    "end_seconds": _round_seconds(end),
                    "cue": cue_number,
                },
                {
                    "adapter": "stdlib_text",
                    "format": path.suffix.casefold().lstrip("."),
                    "source_name": path.name,
                    "encoding": encoding,
                },
                config.max_fragment_chars,
            )
        )
    if not fragments and _normalize_text(text):
        return ExtractionResult(
            status="failed",
            warnings=("Caption file contained no valid timed cues.",),
            details={"adapter": "stdlib_text", "source_name": path.name},
        )
    return _result_from_fragments("stdlib_text", path, fragments)


def _caption_text(value: str) -> str:
    parser = _HTMLTextParser()
    try:
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    except Exception:
        value = re.sub(r"<[^>]+>", "", value)
    return _normalize_text(value)


def _caption_seconds(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    try:
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60.0 + float(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)
    except ValueError as exc:
        raise ExtractionError(f"Invalid caption timestamp: {value}") from exc
    raise ExtractionError(f"Invalid caption timestamp: {value}")


def _pdf_page_ocr_capability(
    config: ExtractionConfig,
    backend: OcrBackend | None,
) -> dict[str, Any]:
    if backend is None or not callable(
        getattr(backend, "extract_image", None)
    ):
        return _missing_capability(
            "pdf_page_ocr",
            PDF_SUFFIXES,
            "No local OCR backend accepts PDF page images.",
            "Use Launcher Knowledge Assets repair, or run "
            f"{ASSET_REPAIR_ACTION}.",
            status="not_configured",
            dependencies=("RapidOCR backend",),
        )
    backend_report = dict(backend.capability())
    if backend_report.get("status") != "available":
        return {
            **_missing_capability(
                "pdf_page_ocr",
                PDF_SUFFIXES,
                "The configured RapidOCR backend is unavailable: "
                + str(backend_report.get("message") or ""),
                str(
                    backend_report.get("action")
                    or (
                        "Use Launcher Knowledge Assets repair, or run "
                        f"{ASSET_REPAIR_ACTION}."
                    )
                ),
                status=str(
                    backend_report.get("status") or "dependency_missing"
                ),
                dependencies=tuple(
                    str(value)
                    for value in backend_report.get("dependencies", ())
                ),
            ),
            "ocr": backend_report,
        }
    if not _module_available("pypdfium2"):
        return _missing_capability(
            "pdf_page_ocr",
            PDF_SUFFIXES,
            "The pypdfium2 page renderer is missing from the project .venv.",
            "Use Launcher Knowledge Assets repair, or run "
            f"{ASSET_REPAIR_ACTION}.",
            dependencies=("pypdfium2",),
        )
    package_root = _module_root("pypdfium2")
    if package_root is None or not _is_within(
        package_root,
        config.resolved_project_root / PROJECT_VENV_RELATIVE,
    ):
        return _missing_capability(
            "pdf_page_ocr",
            PDF_SUFFIXES,
            "pypdfium2 is not installed in the project .venv.",
            "Use Launcher Knowledge Assets repair, or run "
            f"{ASSET_REPAIR_ACTION}.",
            status="invalid_configuration",
            dependencies=("project .venv pypdfium2",),
        )
    return {
        **_available_capability(
            "pdf_page_ocr",
            PDF_SUFFIXES,
            "pypdfium2 page rendering passed in memory to RapidOCR",
        ),
        "pypdfium2_path": str(package_root),
        "ocr": backend_report,
        "raster_dpi": 300,
        "temporary_files": False,
    }


def _render_pdf_page(
    path: Path,
    page_number: int,
) -> Any:
    pdfium = importlib.import_module("pypdfium2")
    document = pdfium.PdfDocument(str(path))
    page = None
    bitmap = None
    try:
        page = document[page_number - 1]
        render_page = getattr(page, "render")
        bitmap = render_page(
            scale=300.0 / 72.0,
        )
        image = bitmap.to_numpy().copy()
    finally:
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()
        document.close()
    return image


def _open_ooxml(
    path: Path,
    config: ExtractionConfig,
) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExtractionError(f"Invalid OOXML package: {exc}") from exc
    total = sum(info.file_size for info in archive.infolist())
    if total > config.max_archive_total_bytes:
        archive.close()
        raise ExtractionError(
            "OOXML expanded content exceeds the configured archive limit"
        )
    oversized = next(
        (
            info.filename
            for info in archive.infolist()
            if info.file_size > config.max_archive_member_bytes
        ),
        "",
    )
    if oversized:
        archive.close()
        raise ExtractionError(
            f"OOXML member exceeds the configured limit: {oversized}"
        )
    return archive


def _read_archive_member(
    archive: zipfile.ZipFile,
    name: str,
    config: ExtractionConfig,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ExtractionError(f"OOXML member is missing: {name}") from exc
    if info.file_size > config.max_archive_member_bytes:
        raise ExtractionError(f"OOXML member exceeds the limit: {name}")
    return archive.read(info)


def _xlsx_shared_strings(
    archive: zipfile.ZipFile,
    config: ExtractionConfig,
) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = ElementTree.fromstring(
        _read_archive_member(archive, "xl/sharedStrings.xml", config)
    )
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return tuple(
        "".join(text.text or "" for text in item.iter(f"{namespace}t"))
        for item in root.iter(f"{namespace}si")
    )


def _pptx_slides(
    archive: zipfile.ZipFile,
    config: ExtractionConfig,
    slide_name: re.Pattern[str],
) -> tuple[str, ...]:
    presentation_member = "ppt/presentation.xml"
    relationships_member = "ppt/_rels/presentation.xml.rels"
    names = frozenset(archive.namelist())
    if presentation_member in names and relationships_member in names:
        presentation_namespace = (
            "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        )
        document_relation_namespace = (
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        )
        package_relation_namespace = (
            "{http://schemas.openxmlformats.org/package/2006/relationships}"
        )
        presentation = ElementTree.fromstring(
            _read_archive_member(archive, presentation_member, config)
        )
        relationships = ElementTree.fromstring(
            _read_archive_member(archive, relationships_member, config)
        )
        targets = {
            str(relation.get("Id") or ""): str(relation.get("Target") or "")
            for relation in relationships.iter(
                f"{package_relation_namespace}Relationship"
            )
        }
        ordered: list[str] = []
        for slide in presentation.iter(f"{presentation_namespace}sldId"):
            relation_id = str(
                slide.get(f"{document_relation_namespace}id") or ""
            )
            target = targets.get(relation_id, "")
            if not target:
                continue
            member = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("ppt", target))
            )
            if not member.startswith("ppt/slides/") or member not in names:
                raise ExtractionError(
                    "Slide relationship escaped the PPTX slides directory"
                )
            ordered.append(member)
        if ordered:
            return tuple(ordered)
    numbered: list[tuple[int, str]] = []
    for name in archive.namelist():
        match = slide_name.fullmatch(name)
        if match is not None:
            numbered.append((int(match.group(1)), name))
    return tuple(
        name for _number, name in sorted(numbered, key=lambda item: item[0])
    )


def _xlsx_sheets(
    archive: zipfile.ZipFile,
    config: ExtractionConfig,
) -> tuple[tuple[str, str], ...]:
    main_namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    relation_namespace = (
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    )
    package_relation_namespace = (
        "{http://schemas.openxmlformats.org/package/2006/relationships}"
    )
    workbook = ElementTree.fromstring(
        _read_archive_member(archive, "xl/workbook.xml", config)
    )
    relationships = ElementTree.fromstring(
        _read_archive_member(
            archive,
            "xl/_rels/workbook.xml.rels",
            config,
        )
    )
    targets = {
        str(relation.get("Id") or ""): str(relation.get("Target") or "")
        for relation in relationships.iter(
            f"{package_relation_namespace}Relationship"
        )
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.iter(f"{main_namespace}sheet"):
        name = str(sheet.get("name") or "Sheet")
        relation_id = str(sheet.get(f"{relation_namespace}id") or "")
        target = targets.get(relation_id, "")
        if not target:
            continue
        if target.startswith("/"):
            member = target.lstrip("/")
        else:
            member = posixpath.normpath(posixpath.join("xl", target))
        if not member.startswith("xl/"):
            raise ExtractionError("Worksheet relationship escaped the XLSX root")
        sheets.append((name, member))
    return tuple(sheets)


def _xlsx_fragments(
    archive: zipfile.ZipFile,
    sheets: Sequence[tuple[str, str]],
    shared_strings: Sequence[str],
    path: Path,
    asset_id: str,
    config: ExtractionConfig,
) -> list[dict[str, Any]]:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    fragments: list[dict[str, Any]] = []
    for sheet_name, member in sheets:
        root = ElementTree.fromstring(
            _read_archive_member(archive, member, config)
        )
        for fallback_row, row in enumerate(
            root.iter(f"{namespace}row"),
            start=1,
        ):
            row_number = int(row.get("r") or fallback_row)
            fields: list[str] = []
            for cell in row.findall(f"{namespace}c"):
                reference = str(cell.get("r") or "")
                cell_type = str(cell.get("t") or "")
                value_element = cell.find(f"{namespace}v")
                value = value_element.text if value_element is not None else ""
                if cell_type == "s" and value:
                    index = int(value)
                    if index < 0 or index >= len(shared_strings):
                        raise ExtractionError(
                            f"Shared string index is invalid: {index}"
                        )
                    value = shared_strings[index]
                elif cell_type == "inlineStr":
                    value = "".join(
                        text.text or ""
                        for text in cell.iter(f"{namespace}t")
                    )
                elif cell_type == "b":
                    value = "TRUE" if value == "1" else "FALSE"
                value = _normalize_text(value or "")
                if value:
                    fields.append(f"{reference or '?'}={value}")
            row_text = "\t".join(fields)
            if not row_text:
                continue
            fragments.extend(
                _fragments_for_text(
                    asset_id,
                    row_text,
                    {
                        "type": "sheet_row",
                        "sheet": sheet_name,
                        "row": row_number,
                    },
                    {
                        "adapter": "stdlib_xlsx",
                        "format": "xlsx",
                        "source_name": path.name,
                        "cells": len(fields),
                    },
                    config.max_fragment_chars,
                )
            )
    return fragments


def _text_result(
    path: Path,
    asset_id: str,
    text: str,
    config: ExtractionConfig,
    *,
    locator: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> ExtractionResult:
    fragments = _fragments_for_text(
        asset_id,
        text,
        locator,
        {
            **metadata,
            "format": path.suffix.casefold().lstrip("."),
            "source_name": path.name,
        },
        config.max_fragment_chars,
    )
    return _result_from_fragments(str(metadata["adapter"]), path, fragments)


def _fragments_for_text(
    asset_id: str,
    text: str,
    locator: Mapping[str, Any],
    metadata: Mapping[str, Any],
    maximum: int,
) -> list[dict[str, Any]]:
    chunks = _split_text(text, maximum)
    total = len(chunks)
    fragments: list[dict[str, Any]] = []
    for part, chunk in enumerate(chunks, start=1):
        part_locator = dict(locator)
        part_metadata = dict(metadata)
        if total > 1:
            part_locator["part"] = part
            part_metadata["parts"] = total
        locator_json = json.dumps(
            part_locator,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = hashlib.sha256(locator_json.encode("utf-8")).hexdigest()[:20]
        fragments.append(
            {
                "asset_id": asset_id,
                "fragment_id": f"{asset_id}:{identity}",
                "text": chunk,
                "locator": part_locator,
                "metadata": part_metadata,
            }
        )
    return fragments


def _split_text(value: str, maximum: int) -> tuple[str, ...]:
    text = _normalize_text(value)
    if not text:
        return ()
    chunks: list[str] = []
    remaining = text
    while len(remaining) > maximum:
        minimum = maximum // 2
        boundary = max(
            remaining.rfind("\n\n", minimum, maximum + 1),
            remaining.rfind("\n", minimum, maximum + 1),
            remaining.rfind(" ", minimum, maximum + 1),
        )
        if boundary < minimum:
            boundary = maximum
        chunk = remaining[:boundary].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def _normalize_text(value: str) -> str:
    text = (
        str(value)
        .replace("\x00", " ")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines = [re.sub(r" +", " ", line).strip() for line in text.splitlines()]
    output: list[str] = []
    blank = False
    for line in lines:
        if line:
            output.append(line)
            blank = False
        elif output and not blank:
            output.append("")
            blank = True
    return "\n".join(output).strip()


def _result_from_fragments(
    adapter: str,
    path: Path,
    fragments: Sequence[dict[str, Any]],
    *,
    warnings: Sequence[str] = (),
) -> ExtractionResult:
    return ExtractionResult(
        status=EXTRACTION_STATUS_READY,
        fragments=tuple(fragments),
        warnings=tuple(warnings),
        details={
            "adapter": adapter,
            "source_name": path.name,
            "fragments": len(fragments),
        },
    )


def _failure(adapter: str, path: Path, message: str) -> ExtractionResult:
    return ExtractionResult(
        status=EXTRACTION_STATUS_FAILED,
        warnings=(message,),
        details={
            "adapter": adapter,
            "source_name": path.name,
            "failure_reason": "adapter_failed",
        },
    )


def _dependency_result(
    capability: Mapping[str, Any],
    path: Path,
) -> ExtractionResult:
    return ExtractionResult(
        status=EXTRACTION_STATUS_FAILED,
        warnings=(
            str(capability.get("message") or "Extractor dependency is missing."),
            str(capability.get("action") or "Configure the local dependency."),
        ),
        details={
            "adapter": str(capability.get("name") or ""),
            "source_name": path.name,
            "capability": dict(capability),
            "failure_reason": str(
                capability.get("status") or "dependency_missing"
            ),
        },
    )


def _available_capability(
    name: str,
    suffixes: Iterable[str],
    implementation: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "available",
        "formats": sorted(suffixes),
        "implementation": implementation,
        "dependencies": [],
        "message": "Ready for deterministic local extraction.",
        "action": "",
        "local_only": True,
    }


def _missing_capability(
    name: str,
    suffixes: Iterable[str],
    message: str,
    action: str,
    *,
    status: str = "dependency_missing",
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "formats": sorted(suffixes),
        "implementation": "",
        "dependencies": list(dependencies),
        "message": message,
        "action": action,
        "local_only": True,
    }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _module_root(name: str) -> Path | None:
    try:
        specification = importlib.util.find_spec(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None
    if specification is None:
        return None
    locations = specification.submodule_search_locations
    if locations:
        try:
            return Path(next(iter(locations))).resolve()
        except (OSError, StopIteration):
            return None
    if specification.origin:
        try:
            return Path(specification.origin).resolve().parent
        except OSError:
            return None
    return None


def _configured_executable(
    configured: str | os.PathLike[str] | None,
    project_root: Path,
    candidates: Sequence[str],
) -> Path | None:
    paths: list[Path] = []
    if configured is not None and str(configured).strip():
        configured_path = Path(configured)
        paths.append(
            configured_path
            if configured_path.is_absolute()
            else project_root / configured_path
        )
    paths.extend(project_root / relative for relative in candidates)
    for candidate in paths:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and _is_within(
            resolved,
            project_root / ".runtime",
        ):
            return resolved
    return None


def _configured_path(
    configured: str | os.PathLike[str] | None,
    project_root: Path,
) -> Path | None:
    if configured is None or not str(configured).strip():
        return None
    path = Path(configured)
    try:
        return (
            path.resolve()
            if path.is_absolute()
            else (project_root / path).resolve()
        )
    except OSError:
        return None


def _ordinary_file(path: Path, config: ExtractionConfig) -> Path:
    try:
        if not os.path.lexists(path):
            raise ExtractionError(f"Local asset was not found: {path}")
        if path.is_symlink():
            raise ExtractionError("Local asset cannot be a symbolic link")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ExtractionError("Local asset is not an ordinary file")
        size = resolved.stat().st_size
    except ExtractionError:
        raise
    except OSError as exc:
        raise ExtractionError(f"Local asset could not be inspected: {exc}") from exc
    maximum = (
        config.max_media_source_bytes
        if resolved.suffix.casefold() in MEDIA_SUFFIXES
        else config.max_source_bytes
    )
    if size > maximum:
        raise ExtractionError(
            f"Local asset exceeds the {maximum} byte limit"
        )
    return resolved


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
        if size > maximum:
            raise ExtractionError(f"Source exceeds the {maximum} byte limit")
        with path.open("rb") as stream:
            value = stream.read(maximum + 1)
    except ExtractionError:
        raise
    except OSError as exc:
        raise ExtractionError(f"Source could not be read: {exc}") from exc
    if len(value) > maximum:
        raise ExtractionError(f"Source exceeds the {maximum} byte limit")
    return value


def _asset_id(path: Path, supplied: str | None) -> str:
    if supplied is not None:
        value = str(supplied).strip()
        if _ASSET_ID_PATTERN.fullmatch(value) is None:
            raise ExtractionError(
                "asset_id must be 1-256 characters using letters, digits, "
                "period, underscore, colon, or hyphen"
            )
        return value
    identity = hashlib.sha256(
        str(path).casefold().encode("utf-8")
    ).hexdigest()[:24]
    return f"asset-{identity}"


def _validate_fragments(fragments: Sequence[Mapping[str, Any]]) -> None:
    expected = {"asset_id", "fragment_id", "text", "locator", "metadata"}
    identities: set[str] = set()
    for fragment in fragments:
        if set(fragment) != expected:
            raise ExtractionError(
                "Adapter returned an invalid fragment field set"
            )
        if not str(fragment["asset_id"]) or not str(fragment["fragment_id"]):
            raise ExtractionError("Adapter returned an empty fragment identity")
        if not _normalize_text(str(fragment["text"])):
            raise ExtractionError("Adapter returned an empty fragment")
        if not isinstance(fragment["locator"], Mapping):
            raise ExtractionError("Adapter returned an invalid locator")
        if not isinstance(fragment["metadata"], Mapping):
            raise ExtractionError("Adapter returned invalid metadata")
        identity = str(fragment["fragment_id"])
        if identity in identities:
            raise ExtractionError("Adapter returned duplicate fragment ids")
        identities.add(identity)


def _reject_checkpoint(checkpoint: Mapping[str, Any] | None) -> None:
    if checkpoint is not None:
        raise ExtractionError(
            "checkpoint is supported only for audio and video extraction"
        )


def _source_fingerprint(path: Path) -> str:
    stat_result = path.stat()
    digest = hashlib.sha256()
    digest.update(str(path).casefold().encode("utf-8"))
    digest.update(str(stat_result.st_size).encode("ascii"))
    digest.update(str(stat_result.st_mtime_ns).encode("ascii"))
    sample_size = 64 * 1024
    with path.open("rb") as stream:
        digest.update(stream.read(sample_size))
        if stat_result.st_size > sample_size:
            stream.seek(max(0, stat_result.st_size - sample_size))
            digest.update(stream.read(sample_size))
    return digest.hexdigest()


def _media_checkpoint(
    checkpoint: Mapping[str, Any] | None,
    asset_id: str,
    fingerprint: str,
    duration: float,
) -> dict[str, Any]:
    if checkpoint is None:
        return {
            "schema": EXTRACTION_CHECKPOINT_SCHEMA,
            "version": 1,
            "asset_id": asset_id,
            "source_fingerprint": fingerprint,
            "duration_seconds": _round_seconds(duration),
            "next_start_seconds": 0.0,
            "next_slice_index": 0,
            "complete": False,
        }
    if not isinstance(checkpoint, Mapping):
        raise ExtractionError("Media checkpoint must be an object")
    if str(checkpoint.get("schema") or "") != EXTRACTION_CHECKPOINT_SCHEMA:
        raise ExtractionError("Media checkpoint schema is unsupported")
    if int(checkpoint.get("version", 0)) != 1:
        raise ExtractionError("Media checkpoint version is unsupported")
    if str(checkpoint.get("asset_id") or "") != asset_id:
        raise ExtractionError("Media checkpoint belongs to another asset")
    if str(checkpoint.get("source_fingerprint") or "") != fingerprint:
        raise ExtractionError(
            "Media changed after the checkpoint was created"
        )
    try:
        start = float(checkpoint.get("next_start_seconds", 0.0))
        slice_index = int(checkpoint.get("next_slice_index", 0))
    except (TypeError, ValueError) as exc:
        raise ExtractionError("Media checkpoint fields are invalid") from exc
    if start < 0 or start > duration + 0.001 or slice_index < 0:
        raise ExtractionError("Media checkpoint boundary is out of range")
    return {
        "schema": EXTRACTION_CHECKPOINT_SCHEMA,
        "version": 1,
        "asset_id": asset_id,
        "source_fingerprint": fingerprint,
        "duration_seconds": _round_seconds(duration),
        "next_start_seconds": _round_seconds(min(start, duration)),
        "next_slice_index": slice_index,
        "complete": bool(checkpoint.get("complete", False)),
    }


def _clamp_seconds(
    value: Any,
    minimum: float,
    maximum: float,
    default: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _round_seconds(value: float) -> float:
    return round(float(value), 6)


def _decimal_seconds(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _run_local_process(
    command: Sequence[str],
    *,
    text_output: bool,
) -> subprocess.CompletedProcess[Any]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=text_output,
            timeout=5 * 60,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(
            f"Local process timed out: {Path(command[0]).name}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = _normalize_text(str(stderr or ""))[:500]
        raise ExtractionError(
            f"Local process failed: {Path(command[0]).name}"
            + (f" ({detail})" if detail else "")
        ) from exc
    except OSError as exc:
        raise ExtractionError(
            f"Local process could not start: {Path(command[0]).name} ({exc})"
        ) from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


__all__ = [
    "AsrBackend",
    "AUDIO_SUFFIXES",
    "capability_report",
    "CsvAdapter",
    "dependency_contract",
    "DocxAdapter",
    "EXTRACTION_CHECKPOINT_SCHEMA",
    "ExtractionConfig",
    "ExtractionError",
    "ExtractionResult",
    "EXTRACTION_STATUS_FAILED",
    "EXTRACTION_STATUS_PARTIAL",
    "EXTRACTION_STATUS_READY",
    "EXTRACTION_STATUSES",
    "ExtractorAdapter",
    "FasterWhisperBackend",
    "IMAGE_SUFFIXES",
    "ImageOcrAdapter",
    "LocalAssetExtractor",
    "MEDIA_SLICE_OVERLAP_SECONDS",
    "MEDIA_SUFFIXES",
    "OFFICE_SUFFIXES",
    "OcrBackend",
    "PDF_SUFFIXES",
    "PlainTextAdapter",
    "PptxAdapter",
    "PypdfAdapter",
    "RapidOcrBackend",
    "SUPPORTED_SUFFIXES",
    "TEXT_SUFFIXES",
    "VIDEO_SUFFIXES",
    "XlsxAdapter",
]
