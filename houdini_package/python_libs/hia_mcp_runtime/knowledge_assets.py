"""Resumable ingestion of long local assets into the shared knowledge index.

File parsing is owned exclusively by :mod:`local_extractors`.  This module
copies explicitly selected files under ``.runtime/knowledge/assets``, maps each
returned fragment to one existing ``NormalizedSource``/document, commits
bounded batches through the existing SQLite/FTS/vector path, and persists the
extractor's opaque checkpoint only after those fragment batches commit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from .deterministic_sources import (
        CAPTION_SUFFIXES,
        NormalizedSource,
        VERIFICATION,
    )
    from .knowledge_index import MAX_DOCUMENT_BYTES
    from .local_extractors import (
        ExtractionConfig,
        ExtractionResult,
        LocalAssetExtractor,
        MEDIA_SUFFIXES,
    )
else:
    from hia_mcp_runtime.deterministic_sources import (
        CAPTION_SUFFIXES,
        NormalizedSource,
        VERIFICATION,
    )
    from hia_mcp_runtime.knowledge_index import MAX_DOCUMENT_BYTES
    from hia_mcp_runtime.local_extractors import (
        ExtractionConfig,
        ExtractionResult,
        LocalAssetExtractor,
        MEDIA_SUFFIXES,
    )


ASSET_MANIFEST_SCHEMA = "hia-knowledge-asset/1"
ASSET_CHECKPOINT_SCHEMA = "hia-knowledge-asset-checkpoint/1"
ASSET_COLLECTION = "asset"
ASSET_ROOT_RELATIVE = Path(".runtime") / "knowledge" / "assets"
DEFAULT_ASSET_BATCH_SIZE = 8
MAX_ASSET_BATCH_SIZE = 64
MAX_ASSET_BYTES = 512 * 1024 * 1024 * 1024
MAX_ASSET_ID_CHARS = 128
MAX_FRAGMENT_ID_CHARS = 256
MAX_LOCATOR_CHARS = 1024
MAX_TITLE_CHARS = 512
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
ASSET_REPAIR_ACTION = r".\scripts\hia-knowledge.ps1 assets repair"
ASR_MODEL_RELATIVE = ".runtime/models/asr/faster-whisper"
FFMPEG_RELATIVE = ".runtime/dependencies/ffmpeg/bin/ffmpeg.exe"
_EXTRACTOR_REPAIR_TARGETS: dict[str, tuple[str, int]] = {
    "stdlib_text": (
        "houdini_package/python_libs/hia_mcp_runtime/local_extractors.py",
        0,
    ),
    "stdlib_csv": (
        "houdini_package/python_libs/hia_mcp_runtime/local_extractors.py",
        0,
    ),
    "stdlib_docx": (
        "houdini_package/python_libs/hia_mcp_runtime/local_extractors.py",
        0,
    ),
    "stdlib_pptx": (
        "houdini_package/python_libs/hia_mcp_runtime/local_extractors.py",
        0,
    ),
    "stdlib_xlsx": (
        "houdini_package/python_libs/hia_mcp_runtime/local_extractors.py",
        0,
    ),
    "pypdf": (".venv/Lib/site-packages/pypdf", 10_000_000),
    "image_ocr": (".venv/Lib/site-packages/rapidocr", 420_000_000),
    "media_asr": (ASR_MODEL_RELATIVE, 2_000_000_000),
}


class KnowledgeAssetError(RuntimeError):
    """Raised when an asset cannot be safely or deterministically processed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool = False,
        not_found: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.not_found = not_found


ProgressCallback = Callable[[Mapping[str, Any]], None]


class KnowledgeAssetManager:
    """Own asset artifacts while reusing one existing knowledge database."""

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        *,
        store: Any,
        extractor: Any | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.store = store
        self.index = store.index
        self.asset_root = self.project_root / ASSET_ROOT_RELATIVE
        self.extractor = extractor or LocalAssetExtractor(
            ExtractionConfig(project_root=self.project_root)
        )

    @staticmethod
    def capabilities(
        *,
        project_root: str | os.PathLike[str] | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Expose the actual local extractor inventory used by import/resume."""

        selected = extractor or LocalAssetExtractor(
            ExtractionConfig(project_root=project_root)
        )
        raw = selected.capabilities()
        if not isinstance(raw, Mapping):
            raise KnowledgeAssetError(
                "ASSET_CAPABILITIES_INVALID",
                "Local extractor capabilities must be an object",
            )
        capability_rows = raw.get("capabilities")
        rows = capability_rows if isinstance(capability_rows, Mapping) else {}
        formats_value = raw.get("formats")
        formats = (
            dict(formats_value) if isinstance(formats_value, Mapping) else {}
        )
        resolved_root = ExtractionConfig(
            project_root=project_root
        ).resolved_project_root
        extractors: list[dict[str, Any]] = []
        for name, value in sorted(rows.items()):
            details = dict(value) if isinstance(value, Mapping) else {}
            adapter_name = str(details.get("name") or name)
            suffixes = details.get("formats")
            if not isinstance(suffixes, list):
                suffixes = details.get("suffixes")
            target, estimated_bytes = _EXTRACTOR_REPAIR_TARGETS.get(
                adapter_name,
                (
                    "houdini_package/python_libs/hia_mcp_runtime/"
                    "local_extractors.py",
                    0,
                ),
            )
            status = str(details.get("status") or "unknown")
            extractors.append(
                {
                    **details,
                    "name": adapter_name,
                    "suffixes": (
                        sorted(str(item) for item in suffixes)
                        if isinstance(suffixes, (list, tuple, set, frozenset))
                        else []
                    ),
                    "action": ASSET_REPAIR_ACTION,
                    "target_relative_path": target,
                    "estimated_bytes": estimated_bytes,
                    "installed": _extractor_installed(
                        resolved_root,
                        adapter_name,
                    ),
                    "ready": status in {"available", "ready"},
                }
            )
        return {
            "schema": ASSET_MANIFEST_SCHEMA,
            "checkpoint_schema": ASSET_CHECKPOINT_SCHEMA,
            "database": "shared .runtime/knowledge/knowledge.sqlite3",
            "asset_root": ASSET_ROOT_RELATIVE.as_posix(),
            "fragment_mapping": {
                "required": ["asset_id", "fragment_id", "text", "locator"],
                "optional": ["metadata", "source_kind", "title"],
                "source_kinds": ["user_document", "user_transcript"],
            },
            "extractors": extractors,
            "formats": formats,
            "external_extraction": {
                "supported": True,
                "model_installation": True,
                "suffixes_requiring_injected_extractor": sorted(
                    suffix
                    for suffix, value in formats.items()
                    if not isinstance(value, Mapping)
                    or str(value.get("status") or "")
                    not in {"available", "ready"}
                ),
            },
            "local_only": bool(raw.get("local_only", True)),
            "network_access": bool(raw.get("network_access", False)),
            "model_installation": True,
            "repair": {
                "action": ASSET_REPAIR_ACTION,
                "profile": "local-ocr-asr",
                "asr_profile": "large-v3-turbo",
                "asr_repository": (
                    "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
                ),
                "asr_model_target_relative_path": ASR_MODEL_RELATIVE,
                "ffmpeg_target_relative_path": FFMPEG_RELATIVE,
                "estimated_download_bytes": 1_750_000_000,
                "estimated_disk_bytes": 2_000_000_000,
                "cpu_compute_type": "int8",
                "advanced_device_override": "cuda",
                "rerunnable": True,
            },
            "commands": [
                "assets import",
                "assets list",
                "assets status",
                "assets resume",
                "assets delete",
                "assets capabilities",
                "assets repair",
            ],
        }

    def import_asset(
        self,
        path: str | os.PathLike[str],
        *,
        asset_id: str = "",
        title: str = "",
        batch_size: int = DEFAULT_ASSET_BATCH_SIZE,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        source = _ordinary_source(Path(path))
        if source.stat().st_size > MAX_ASSET_BYTES:
            raise KnowledgeAssetError(
                "ASSET_TOO_LARGE",
                f"Assets are limited to {MAX_ASSET_BYTES} bytes",
            )
        stable_id = _asset_id(asset_id or f"asset-{uuid.uuid4().hex}")
        selected_title = _bounded_text(title or source.stem, MAX_TITLE_CHARS)
        if not selected_title:
            raise KnowledgeAssetError(
                "ASSET_TITLE_INVALID",
                "Asset title must contain visible text",
            )
        bounded_batch = _batch_size(batch_size)
        root = self._asset_directory(stable_id, create_root=True)
        if os.path.lexists(root):
            raise KnowledgeAssetError(
                "ASSET_ALREADY_EXISTS",
                f"Asset {stable_id} already exists; use assets resume",
                recoverable=True,
            )
        root.mkdir()
        managed_source = root / f"source{source.suffix.casefold()}"
        try:
            shutil.copyfile(source, managed_source)
            source_sha256 = _hash_file(managed_source)
            manifest = {
                "schema": ASSET_MANIFEST_SCHEMA,
                "asset_id": stable_id,
                "title": selected_title,
                "kind": source.suffix.casefold().lstrip(".") or "asset",
                "status": "processing",
                "source_name": source.name,
                "source_suffix": source.suffix.casefold(),
                "source_path": managed_source.relative_to(
                    self.project_root
                ).as_posix(),
                "source_sha256": source_sha256,
                "source_size_bytes": managed_source.stat().st_size,
                "extractor": "local_extractors",
                "source_keys": [],
                "fragments": [],
                "error": "",
                "recoverable": True,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            checkpoint = {
                "schema": ASSET_CHECKPOINT_SCHEMA,
                "asset_id": stable_id,
                "source_sha256": source_sha256,
                "state": "processing",
                "next_fragment": 0,
                "source_keys": [],
                "extractor_checkpoint": None,
                "updated_at": _utc_now(),
            }
            _write_json_atomic(root / "manifest.json", manifest)
            _write_json_atomic(root / "checkpoint.json", checkpoint)
        except BaseException:
            try:
                shutil.rmtree(root)
            except OSError:
                pass
            raise
        _emit_progress(
            progress,
            stage="copied",
            asset_id=stable_id,
            source_size_bytes=int(manifest["source_size_bytes"]),
        )
        return self._process_unit(
            stable_id,
            batch_size=bounded_batch,
            progress=progress,
        )

    def resume_asset(
        self,
        asset_id: str,
        *,
        batch_size: int = DEFAULT_ASSET_BATCH_SIZE,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        stable_id = _asset_id(asset_id)
        _root, manifest, checkpoint = self._asset_state(stable_id)
        if str(checkpoint.get("source_sha256") or "") != str(
            manifest.get("source_sha256") or ""
        ):
            raise KnowledgeAssetError(
                "ASSET_CHECKPOINT_INVALID",
                "Asset checkpoint does not match its copied source",
            )
        bounded_batch = _batch_size(batch_size)
        if str(checkpoint.get("state") or "") == "complete":
            self._sync_asset_vectors(
                stable_id,
                batch_size=bounded_batch,
                progress=progress,
            )
            return self.status_asset(stable_id)
        return self._process_unit(
            stable_id,
            batch_size=bounded_batch,
            progress=progress,
        )

    def list_assets(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        bounded_offset, bounded_limit = _pagination(offset, limit)
        root = self._ensure_asset_root(create=False)
        if root is None:
            return {
                "items": [],
                "total": 0,
                "offset": bounded_offset,
                "limit": bounded_limit,
            }
        asset_ids = sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and not _is_reparse_point(path)
            and _IDENTIFIER_PATTERN.fullmatch(path.name)
        )
        return {
            "items": [
                self.status_asset(value)
                for value in asset_ids[
                    bounded_offset : bounded_offset + bounded_limit
                ]
            ],
            "total": len(asset_ids),
            "offset": bounded_offset,
            "limit": bounded_limit,
        }

    def status_asset(self, asset_id: str) -> dict[str, Any]:
        stable_id = _asset_id(asset_id)
        _root, manifest, checkpoint = self._asset_state(stable_id)
        source_keys = _source_keys(manifest, checkpoint)
        counts = self._database_counts(source_keys)
        fragments = manifest.get("fragments")
        fragment_count = len(fragments) if isinstance(fragments, list) else 0
        processed_units, total_units = _extractor_progress(
            checkpoint.get("extractor_checkpoint"),
            fragment_count,
            str(checkpoint.get("state") or ""),
        )
        return {
            "asset_id": stable_id,
            "title": str(manifest.get("title") or ""),
            "kind": str(manifest.get("kind") or "asset"),
            "status": str(manifest.get("status") or "unknown"),
            "extractor": str(manifest.get("extractor") or ""),
            "source_name": str(manifest.get("source_name") or ""),
            "source_suffix": str(manifest.get("source_suffix") or ""),
            "source_size_bytes": int(manifest.get("source_size_bytes") or 0),
            "source_sha256": str(manifest.get("source_sha256") or ""),
            "fragment_count": fragment_count,
            "source_key_count": len(source_keys),
            "next_fragment": int(checkpoint.get("next_fragment") or 0),
            "processed_units": processed_units,
            "total_units": total_units,
            "checkpoint_state": str(checkpoint.get("state") or ""),
            "documents_indexed": counts["documents"],
            "chunks_indexed": counts["chunks"],
            "vectors_indexed": counts["vectors"],
            "vector_pending_chunks": max(
                0,
                counts["chunks"] - counts["vectors"],
            ),
            "error": str(manifest.get("error") or ""),
            "recoverable": bool(manifest.get("recoverable", False)),
            "created_at": str(manifest.get("created_at") or ""),
            "updated_at": str(manifest.get("updated_at") or ""),
        }

    def delete_asset(self, asset_id: str) -> dict[str, Any]:
        stable_id = _asset_id(asset_id)
        root, manifest, checkpoint = self._asset_state(stable_id)
        source_keys = _source_keys(manifest, checkpoint)
        counts = self._database_counts(source_keys)
        tombstone = root.with_name(f".delete-{stable_id}-{uuid.uuid4().hex}.tmp")
        os.rename(root, tombstone)
        try:
            with self.index._connect() as connection:  # noqa: SLF001
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if source_keys:
                        placeholders = ",".join("?" for _value in source_keys)
                        rows = connection.execute(
                            "SELECT id FROM documents "
                            "WHERE collection = ? "
                            f"AND source_key IN ({placeholders})",
                            [ASSET_COLLECTION, *source_keys],
                        ).fetchall()
                        for row in rows:
                            self.index._delete_document(  # noqa: SLF001
                                connection,
                                int(row[0]),
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except BaseException:
            if os.path.lexists(tombstone) and not os.path.lexists(root):
                os.rename(tombstone, root)
            raise
        warnings: list[str] = []
        try:
            shutil.rmtree(tombstone)
        except OSError as exc:
            warnings.append(_bounded_text(str(exc), 1024))
        return {
            "asset_id": stable_id,
            "deleted": True,
            "documents_deleted": counts["documents"],
            "chunks_deleted": counts["chunks"],
            "vectors_deleted": counts["vectors"],
            "source_keys_deleted": len(source_keys),
            "warnings": warnings,
        }

    def _process_unit(
        self,
        asset_id: str,
        *,
        batch_size: int,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        root, manifest, checkpoint = self._asset_state(asset_id)
        source = self._managed_source(root, manifest)
        if _hash_file(source) != str(manifest.get("source_sha256") or ""):
            raise KnowledgeAssetError(
                "ASSET_SOURCE_CHANGED",
                "The copied asset no longer matches its manifest",
            )
        raw_checkpoint = checkpoint.get("extractor_checkpoint")
        extractor_checkpoint = (
            dict(raw_checkpoint) if isinstance(raw_checkpoint, Mapping) else None
        )
        result = self.extractor.extract(
            source,
            asset_id=asset_id,
            checkpoint=extractor_checkpoint,
        )
        if not isinstance(result, ExtractionResult):
            raise KnowledgeAssetError(
                "ASSET_EXTRACTOR_INVALID",
                "Local extractor returned an invalid result",
                recoverable=True,
            )
        if result.status not in {"ready", "partial"}:
            message = (
                "; ".join(str(value) for value in result.warnings if str(value))
                or f"Local extractor reported {result.status}"
            )
            manifest["status"] = str(result.status or "failed")
            manifest["error"] = _bounded_text(message, 2048)
            manifest["recoverable"] = True
            manifest["updated_at"] = _utc_now()
            checkpoint["state"] = str(result.status or "failed")
            checkpoint["updated_at"] = _utc_now()
            _write_json_atomic(root / "manifest.json", manifest)
            _write_json_atomic(root / "checkpoint.json", checkpoint)
            raise KnowledgeAssetError(
                "ASSET_EXTRACTION_INCOMPLETE",
                manifest["error"],
                recoverable=True,
            )

        known_fragments = {
            str(value.get("fragment_id") or ""): int(
                value.get("ordinal") or 0
            )
            for value in manifest.get("fragments", ())
            if isinstance(value, Mapping)
        }
        batch: list[NormalizedSource] = []
        descriptors: list[dict[str, Any]] = []
        source_suffix = str(manifest.get("source_suffix") or "")
        for raw in result.fragments:
            fragment_id = _fragment_id(
                raw.get("fragment_id") if isinstance(raw, Mapping) else ""
            )
            ordinal = known_fragments.get(
                fragment_id,
                len(known_fragments),
            )
            record, descriptor = normalize_asset_fragment(
                asset_id,
                raw,
                ordinal=ordinal,
                asset_title=str(manifest.get("title") or ""),
                managed_source_path=str(manifest.get("source_path") or ""),
                source_sha256=str(manifest.get("source_sha256") or ""),
                extractor_name=str(
                    result.details.get("adapter")
                    if isinstance(result.details, Mapping)
                    else ""
                )
                or "local_extractors",
                source_suffix=source_suffix,
            )
            batch.append(record)
            descriptors.append(descriptor)
            known_fragments.setdefault(fragment_id, ordinal)
            if len(batch) >= batch_size:
                self._commit_batch(
                    root,
                    manifest,
                    checkpoint,
                    batch,
                    descriptors,
                    progress=progress,
                )
                batch = []
                descriptors = []
        if batch:
            self._commit_batch(
                root,
                manifest,
                checkpoint,
                batch,
                descriptors,
                progress=progress,
            )

        # The extractor checkpoint advances only after every fragment returned
        # by this unit has committed to SQLite and vector synchronization has
        # been attempted.  Re-running after a crash is therefore an idempotent
        # replay of source_key-stable documents.
        next_extractor_checkpoint = (
            dict(result.checkpoint)
            if isinstance(result.checkpoint, Mapping)
            else None
        )
        complete = result.status == "ready" and (
            next_extractor_checkpoint is None
            or bool(next_extractor_checkpoint.get("complete", True))
        )
        adapter = (
            str(result.details.get("adapter") or "")
            if isinstance(result.details, Mapping)
            else ""
        )
        if adapter:
            manifest["extractor"] = _bounded_text(adapter, 128)
        manifest["status"] = "complete" if complete else "partial"
        manifest["error"] = ""
        manifest["recoverable"] = not complete
        manifest["updated_at"] = _utc_now()
        checkpoint["state"] = "complete" if complete else "partial"
        checkpoint["next_fragment"] = len(manifest["fragments"])
        checkpoint["source_keys"] = list(manifest["source_keys"])
        checkpoint["extractor_checkpoint"] = next_extractor_checkpoint
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(root / "manifest.json", manifest)
        _write_json_atomic(root / "checkpoint.json", checkpoint)
        _emit_progress(
            progress,
            stage="complete" if complete else "partial",
            asset_id=asset_id,
            fragments_committed=len(manifest["fragments"]),
            next_fragment=int(checkpoint["next_fragment"]),
            recoverable=not complete,
        )
        return self.status_asset(asset_id)

    def _commit_batch(
        self,
        root: Path,
        manifest: dict[str, Any],
        checkpoint: dict[str, Any],
        records: Sequence[NormalizedSource],
        descriptors: Sequence[Mapping[str, Any]],
        *,
        progress: ProgressCallback | None,
    ) -> None:
        refresh = self.index.refresh_explicit_records(
            records,
            collection=ASSET_COLLECTION,
        )
        document_ids = tuple(
            int(value) for value in refresh.get("document_ids", ())
        )
        known_keys = set(str(value) for value in manifest["source_keys"])
        known_fragments = {
            str(value.get("fragment_id") or "")
            for value in manifest["fragments"]
            if isinstance(value, Mapping)
        }
        for record, raw_descriptor in zip(records, descriptors):
            if record.source_key not in known_keys:
                manifest["source_keys"].append(record.source_key)
                known_keys.add(record.source_key)
            descriptor = dict(raw_descriptor)
            fragment_id = str(descriptor.get("fragment_id") or "")
            if fragment_id not in known_fragments:
                manifest["fragments"].append(descriptor)
                known_fragments.add(fragment_id)
        checkpoint["source_keys"] = list(manifest["source_keys"])
        checkpoint["next_fragment"] = len(manifest["fragments"])
        manifest["updated_at"] = _utc_now()
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(root / "manifest.json", manifest)
        # Do not advance ``extractor_checkpoint`` here; it still identifies the
        # beginning of the current extractor unit until all its batches commit.
        _write_json_atomic(root / "checkpoint.json", checkpoint)
        # Persist deterministic source ownership immediately after the lexical
        # transaction.  If vectorization is interrupted, exact delete and an
        # idempotent resume still know every already-committed document while
        # the opaque extractor checkpoint remains at the start of this unit.
        vector = (
            self.store.vectorize_documents(document_ids)
            if document_ids
            else self.store.status()
        )
        _emit_progress(
            progress,
            stage="indexed",
            asset_id=str(manifest["asset_id"]),
            batch_fragments=len(records),
            fragments_committed=len(manifest["fragments"]),
            next_fragment=int(checkpoint["next_fragment"]),
            refresh=dict(refresh),
            vector=dict(vector),
        )

    def _sync_asset_vectors(
        self,
        asset_id: str,
        *,
        batch_size: int,
        progress: ProgressCallback | None,
    ) -> None:
        _root, manifest, checkpoint = self._asset_state(asset_id)
        source_keys = _source_keys(manifest, checkpoint)
        if not source_keys:
            return
        placeholders = ",".join("?" for _value in source_keys)
        with self.index._connect(read_only=True) as connection:  # noqa: SLF001
            document_ids = tuple(
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM documents "
                    "WHERE collection = ? "
                    f"AND source_key IN ({placeholders}) ORDER BY id",
                    [ASSET_COLLECTION, *source_keys],
                ).fetchall()
            )
        for start in range(0, len(document_ids), batch_size):
            selected = document_ids[start : start + batch_size]
            vector = self.store.vectorize_documents(selected)
            _emit_progress(
                progress,
                stage="vector",
                asset_id=asset_id,
                documents_processed=min(
                    len(document_ids),
                    start + len(selected),
                ),
                documents_total=len(document_ids),
                vector=dict(vector),
            )

    def _asset_directory(
        self,
        asset_id: str,
        *,
        create_root: bool = False,
    ) -> Path:
        root = self._ensure_asset_root(create=create_root)
        if root is None:
            return self.asset_root / asset_id
        candidate = root / asset_id
        if os.path.lexists(candidate) and _is_reparse_point(candidate):
            raise KnowledgeAssetError(
                "ASSET_PATH_UNSAFE",
                "Asset directory cannot be a symlink or reparse point",
            )
        return candidate

    def _ensure_asset_root(self, *, create: bool) -> Path | None:
        current = self.project_root
        for part in ASSET_ROOT_RELATIVE.parts:
            current = current / part
            if os.path.lexists(current):
                if _is_reparse_point(current):
                    raise KnowledgeAssetError(
                        "ASSET_PATH_UNSAFE",
                        "Asset runtime path contains a reparse point",
                    )
                if not current.is_dir():
                    raise KnowledgeAssetError(
                        "ASSET_PATH_UNSAFE",
                        "Asset runtime path is not a directory",
                    )
            elif create:
                current.mkdir()
            else:
                return None
        try:
            current.resolve(strict=True).relative_to(self.project_root)
        except (OSError, ValueError) as exc:
            raise KnowledgeAssetError(
                "ASSET_PATH_UNSAFE",
                "Asset runtime path escaped the project root",
            ) from exc
        return current

    def _asset_state(
        self,
        asset_id: str,
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        root = self._asset_directory(asset_id)
        if not root.is_dir():
            raise KnowledgeAssetError(
                "ASSET_NOT_FOUND",
                f"Asset {asset_id} was not found",
                not_found=True,
            )
        manifest = _read_json(root / "manifest.json", "ASSET_MANIFEST_INVALID")
        checkpoint = _read_json(
            root / "checkpoint.json",
            "ASSET_CHECKPOINT_INVALID",
        )
        if (
            manifest.get("schema") != ASSET_MANIFEST_SCHEMA
            or checkpoint.get("schema") != ASSET_CHECKPOINT_SCHEMA
            or manifest.get("asset_id") != asset_id
            or checkpoint.get("asset_id") != asset_id
        ):
            raise KnowledgeAssetError(
                "ASSET_MANIFEST_INVALID",
                "Asset manifest or checkpoint identity is invalid",
            )
        return root, manifest, checkpoint

    def _managed_source(
        self,
        root: Path,
        manifest: Mapping[str, Any],
    ) -> Path:
        relative = Path(str(manifest.get("source_path") or ""))
        candidate = self.project_root / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise KnowledgeAssetError(
                "ASSET_PATH_UNSAFE",
                "Copied asset source escaped its managed directory",
            ) from exc
        value = os.lstat(candidate)
        if not stat.S_ISREG(value.st_mode) or _is_reparse_point(candidate):
            raise KnowledgeAssetError(
                "ASSET_PATH_UNSAFE",
                "Copied asset source is not an ordinary file",
            )
        return resolved

    def _database_counts(self, source_keys: Sequence[str]) -> dict[str, int]:
        if not source_keys:
            return {"documents": 0, "chunks": 0, "vectors": 0}
        placeholders = ",".join("?" for _value in source_keys)
        with self.index._connect(read_only=True) as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT COUNT(DISTINCT d.id), COUNT(DISTINCT c.id), "
                "COUNT(DISTINCT cv.chunk_id) "
                "FROM documents d "
                "LEFT JOIN chunks c ON c.document_id = d.id "
                "LEFT JOIN chunk_vectors cv ON cv.chunk_id = c.id "
                "WHERE d.collection = ? "
                f"AND d.source_key IN ({placeholders})",
                [ASSET_COLLECTION, *source_keys],
            ).fetchone()
        return {
            "documents": int(row[0] or 0),
            "chunks": int(row[1] or 0),
            "vectors": int(row[2] or 0),
        }


def normalize_asset_fragment(
    asset_id: str,
    fragment: Mapping[str, Any],
    *,
    ordinal: int,
    asset_title: str,
    managed_source_path: str,
    source_sha256: str,
    extractor_name: str,
    source_suffix: str = "",
) -> tuple[NormalizedSource, dict[str, Any]]:
    """Map one local-extractor fragment to exactly one document record."""

    stable_id = _asset_id(asset_id)
    if not isinstance(fragment, Mapping):
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            "Extractor fragments must be mappings",
            recoverable=True,
        )
    declared_asset_id = str(fragment.get("asset_id") or stable_id)
    if declared_asset_id != stable_id:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            "Extractor fragment belongs to a different asset",
        )
    fragment_id = _fragment_id(fragment.get("fragment_id"))
    if isinstance(ordinal, bool) or int(ordinal) < 0:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            "Fragment ordinal must be non-negative",
        )
    text = _normalize_text(str(fragment.get("text") or ""))
    if not text:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            f"Fragment {fragment_id} contains no usable text",
            recoverable=True,
        )
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_TOO_LARGE",
            f"Fragment {fragment_id} exceeds {MAX_DOCUMENT_BYTES} bytes",
            recoverable=True,
        )
    locator = _locator_text(fragment.get("locator"))
    if not locator:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            f"Fragment {fragment_id} locator is empty",
        )
    metadata_value = fragment.get("metadata")
    metadata = (
        dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
    )
    source_kind = str(fragment.get("source_kind") or "")
    if not source_kind:
        source_kind = (
            "user_transcript"
            if str(source_suffix).casefold()
            in MEDIA_SUFFIXES | CAPTION_SUFFIXES
            else "user_document"
        )
    if source_kind not in {"user_document", "user_transcript"}:
        raise KnowledgeAssetError(
            "ASSET_FRAGMENT_INVALID",
            "Asset fragments must use an existing user source kind",
        )
    title = _bounded_text(
        fragment.get("title") or f"{asset_title} [{int(ordinal) + 1}]",
        MAX_TITLE_CHARS,
    )
    identity = {
        "asset_id": stable_id,
        "fragment_id": fragment_id,
        "locator": locator,
    }
    metadata.update(
        {
            **identity,
            "asset_title": _bounded_text(asset_title, MAX_TITLE_CHARS),
            "fragment_ordinal": int(ordinal),
            "extractor": _bounded_text(extractor_name, 128),
        }
    )
    provenance = {
        **identity,
        "managed_source_path": _bounded_text(
            managed_source_path,
            MAX_LOCATOR_CHARS,
        ),
        "source_sha256": _bounded_text(source_sha256, 128),
        "extractor": _bounded_text(extractor_name, 128),
    }
    source_key = f"user:{source_kind}:asset:{stable_id}:{fragment_id}"
    record = NormalizedSource(
        source_kind=source_kind,
        source_key=source_key,
        title=title,
        text=text,
        verification=VERIFICATION,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        provenance=provenance,
        metadata=metadata,
    )
    return record, {
        "fragment_id": fragment_id,
        "source_key": source_key,
        "locator": locator,
        "ordinal": int(ordinal),
        "content_hash": record.content_hash,
    }


def _ordinary_source(path: Path) -> Path:
    try:
        value = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise KnowledgeAssetError(
            "ASSET_SOURCE_NOT_FOUND",
            "The selected asset source was not found",
            not_found=True,
        ) from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or _is_reparse_point(path)
        or not resolved.is_file()
    ):
        raise KnowledgeAssetError(
            "ASSET_SOURCE_INVALID",
            "The selected asset source must be an ordinary file",
        )
    return resolved


def _extractor_installed(project_root: Path, adapter_name: str) -> bool:
    """Return bounded on-disk install state without importing heavy modules."""

    if adapter_name.startswith("stdlib_"):
        return True
    if adapter_name == "pypdf":
        return (
            project_root / ".venv" / "Lib" / "site-packages" / "pypdf"
        ).is_dir()
    if adapter_name == "image_ocr":
        site_packages = project_root / ".venv" / "Lib" / "site-packages"
        return all(
            (site_packages / name).is_dir()
            for name in ("rapidocr", "onnxruntime", "pypdfium2")
        )
    if adapter_name == "media_asr":
        site_packages = project_root / ".venv" / "Lib" / "site-packages"
        return all(
            (
                (site_packages / "faster_whisper").is_dir(),
                (project_root / ASR_MODEL_RELATIVE).is_dir(),
                (project_root / FFMPEG_RELATIVE).is_file(),
            )
        )
    target, _estimated_bytes = _EXTRACTOR_REPAIR_TARGETS.get(
        adapter_name,
        ("", 0),
    )
    return bool(target and (project_root / target).exists())


def _extractor_progress(
    raw_checkpoint: Any,
    fragment_count: int,
    state: str,
) -> tuple[int, int]:
    checkpoint = raw_checkpoint if isinstance(raw_checkpoint, Mapping) else {}
    if "duration_seconds" in checkpoint:
        processed = max(
            0,
            int(float(checkpoint.get("next_start_seconds") or 0)),
        )
        total = max(
            processed,
            int(float(checkpoint.get("duration_seconds") or 0)),
        )
        return processed, total
    processed = fragment_count
    total = fragment_count
    if state not in {"complete", "ready"}:
        total = max(processed, processed + 1)
    return processed, total


def _source_keys(
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> list[str]:
    values: list[str] = []
    for container in (manifest, checkpoint):
        raw = container.get("source_keys")
        if isinstance(raw, list):
            values.extend(str(value) for value in raw if str(value))
    return list(dict.fromkeys(values))


def _asset_id(value: Any) -> str:
    return _identifier(value, "asset_id", MAX_ASSET_ID_CHARS)


def _fragment_id(value: Any) -> str:
    return _identifier(value, "fragment_id", MAX_FRAGMENT_ID_CHARS)


def _identifier(value: Any, name: str, maximum: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or _IDENTIFIER_PATTERN.fullmatch(text) is None
    ):
        raise KnowledgeAssetError(
            f"{name.upper()}_INVALID",
            (
                f"{name} must be 1-{maximum} characters using letters, "
                "numbers, dot, underscore, colon, or hyphen"
            ),
        )
    return text


def _locator_text(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    elif isinstance(value, (list, tuple)):
        raw = json.dumps(
            list(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        raw = str(value or "")
    return _bounded_text(raw, MAX_LOCATOR_CHARS)


def _batch_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_ASSET_BATCH_SIZE
    ):
        raise KnowledgeAssetError(
            "ASSET_BATCH_SIZE_INVALID",
            f"batch_size must be between 1 and {MAX_ASSET_BATCH_SIZE}",
        )
    return value


def _pagination(offset: int, limit: int) -> tuple[int, int]:
    if (
        isinstance(offset, bool)
        or isinstance(limit, bool)
        or not isinstance(offset, int)
        or not isinstance(limit, int)
        or offset < 0
        or not 1 <= limit <= 500
    ):
        raise KnowledgeAssetError(
            "ASSET_PAGINATION_INVALID",
            "offset must be non-negative and limit must be 1-500",
        )
    return offset, limit


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeAssetError(
            code,
            f"{path.name} is missing or invalid",
        ) from exc
    if not isinstance(value, Mapping):
        raise KnowledgeAssetError(code, f"{path.name} must contain an object")
    return dict(value)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(value, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & reparse_flag)


def _normalize_text(text: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in text.replace("\x00", "").replace("\r\n", "\n").replace(
            "\r",
            "\n",
        ).split("\n")
    ).strip()


def _bounded_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _emit_progress(
    callback: ProgressCallback | None,
    **fields: Any,
) -> None:
    if callback is not None:
        callback(dict(fields))


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
