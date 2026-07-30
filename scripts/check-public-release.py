from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath


_FORBIDDEN_COMPONENTS = {
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    ".vs",
    "attachments",
    "checkpoints",
    "jobs",
    "logs",
    "previews",
    "quarantine",
    "renders",
    "screenshots",
    "tests",
}
_FORBIDDEN_SUFFIXES = {
    ".abc",
    ".avi",
    ".bgeo",
    ".exr",
    ".fbx",
    ".hip",
    ".hiplc",
    ".hipnc",
    ".key",
    ".mov",
    ".mp4",
    ".p12",
    ".pem",
    ".pfx",
    ".rat",
    ".sim",
    ".usd",
    ".usda",
    ".usdc",
    ".vdb",
}
_FORBIDDEN_BASENAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "notice.steam-winter-sale.txt",
    "secrets.json",
    "token.json",
}
_FORBIDDEN_RUNTIME_PATHS = {
    "houdini_package/python_libs/hia_panel/b4b_acceptance.py",
    "houdini_package/python_libs/hia_panel/b4b_panel.py",
    "houdini_package/python_libs/hia_panel/houdini_write_adapter.py",
    "houdini_package/python_libs/hia_panel/ime_diagnostic.py",
    "houdini_package/python_panels/hia_b4b_stairs_acceptance.pypanel",
    "houdini_package/python_panels/hia_ime_diagnostic.pypanel",
}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]{16,}", re.I),
    re.compile(rb'"refresh_token"\s*:\s*"[^"]{8,}"', re.I),
)
_MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024
_KNOWLEDGE_PACK_ROOT = PurePosixPath("knowledge/sidefx-official")
_KNOWLEDGE_MANIFEST = _KNOWLEDGE_PACK_ROOT / "manifest.json"
_COMMUNITY_KNOWLEDGE_PACK_ROOT = PurePosixPath(
    "knowledge/community-tutorials"
)
_KNOWLEDGE_METADATA = ("manifest.json", "coverage.json", "sources.json")


def _normalize_entry(name: str) -> PurePosixPath:
    return PurePosixPath(name.replace("\\", "/"))


def _strip_optional_root(path: PurePosixPath) -> PurePosixPath:
    parts = path.parts
    if len(parts) > 1 and parts[0].lower().startswith(("hia-", "big-chicken-")):
        return PurePosixPath(*parts[1:])
    return path


def _path_violations(name: str) -> list[str]:
    portable_name = name.replace("\\", "/")
    path = _normalize_entry(name)
    if not path.parts or path == PurePosixPath("."):
        return []
    violations: list[str] = []
    if (
        portable_name.startswith("/")
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", portable_name)
    ):
        violations.append(f"unsafe archive path: {name}")
        return violations

    relative = _strip_optional_root(path)
    lower_parts = tuple(part.lower() for part in relative.parts)
    lower_path = relative.as_posix().lower()
    basename = lower_parts[-1]

    blocked_components = sorted(set(lower_parts) & _FORBIDDEN_COMPONENTS)
    if blocked_components:
        violations.append(
            f"forbidden directory {blocked_components[0]}: {relative.as_posix()}"
        )
    if basename in _FORBIDDEN_BASENAMES or basename.startswith(".env."):
        violations.append(f"credential or private config: {relative.as_posix()}")
    if any(lower_path.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES):
        violations.append(f"forbidden project output: {relative.as_posix()}")
    if lower_path == "assets/launcher/steam-winter-sale.png":
        violations.append(f"unlicensed launcher artwork: {relative.as_posix()}")
    if lower_path == "assets/launcher/notice.md":
        violations.append(f"unverified artwork notice: {relative.as_posix()}")
    if lower_path == "docs/test-report.md":
        violations.append(f"internal test report: {relative.as_posix()}")
    if lower_path in _FORBIDDEN_RUNTIME_PATHS:
        violations.append(f"non-production Houdini runtime file: {relative.as_posix()}")
    if (
        len(lower_parts) >= 2
        and lower_parts[0] == "docs"
        and (
            re.match(r"^p[0-9]+-", basename, re.I)
            or "gate" in basename
        )
    ):
        violations.append(f"historical gate document: {relative.as_posix()}")
    return violations


def _content_violations(
    name: str,
    data: bytes,
    forbidden_text: Iterable[str] = (),
) -> list[str]:
    if len(data) > _MAX_TEXT_SCAN_BYTES or b"\x00" in data:
        return []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(data):
            return [f"possible credential content: {_normalize_entry(name).as_posix()}"]
    lowered = data.lower()
    for value in forbidden_text:
        normalized = str(value).strip()
        if not normalized:
            continue
        variants = {
            normalized,
            normalized.replace("\\", "/"),
            normalized.replace("/", "\\"),
        }
        if any(
            variant.encode("utf-8", errors="ignore").lower() in lowered
            for variant in variants
            if variant
        ):
            return [
                "embedded local checkout path: "
                f"{_normalize_entry(name).as_posix()}"
            ]
    return []


def _directory_entries(root: Path) -> Iterator[tuple[str, bytes]]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            yield path.relative_to(root).as_posix(), b""
            continue
        if path.is_file():
            data = path.read_bytes()
            yield path.relative_to(root).as_posix(), data


def _zip_entries(archive: Path) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            if info.is_dir():
                continue
            data = b""
            if info.file_size <= _MAX_TEXT_SCAN_BYTES:
                data = package.read(info)
            yield info.filename, data


def _relative_entry_name(name: str) -> str:
    return _strip_optional_root(_normalize_entry(name)).as_posix()


def _safe_card_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if (
        value != path.as_posix()
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", value)
    ):
        return None
    return path


def _ordinary_zip_entry(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return not info.is_dir() and (
        info.create_system != 3
        or not unix_mode
        or stat.S_ISREG(unix_mode)
    )


def _manifest_card_paths(
    manifest: object,
    manifest_name: str,
) -> tuple[set[str], list[str]]:
    sources = manifest.get("sources") if isinstance(manifest, Mapping) else None
    if not isinstance(sources, list) or not sources:
        return set(), [
            f"built-in knowledge manifest sources are invalid: {manifest_name}"
        ]

    cards: set[str] = set()
    violations: list[str] = []
    for index, source in enumerate(sources):
        raw_path = source.get("path") if isinstance(source, Mapping) else None
        card_path = _safe_card_path(raw_path)
        if (
            card_path is None
            or len(card_path.parts) < 2
            or card_path.parts[0] != "cards"
            or card_path.suffix.lower() != ".md"
        ):
            violations.append(
                "unsafe built-in knowledge card path at "
                f"sources[{index}]: {raw_path!r}"
            )
            continue
        cards.add(card_path.as_posix())
    return cards, violations


def _knowledge_pack_violations_for_root(
    archive: zipfile.ZipFile,
    entries: Mapping[str, zipfile.ZipInfo],
    pack_root: PurePosixPath,
) -> list[str]:
    metadata: dict[str, object] = {}
    violations: list[str] = []
    for basename in _KNOWLEDGE_METADATA:
        name = (pack_root / basename).as_posix()
        info = entries.get(name)
        if info is None:
            violations.append(
                f"built-in knowledge metadata is missing or not ordinary: {name}"
            )
            continue
        try:
            if info.file_size > _MAX_TEXT_SCAN_BYTES:
                raise ValueError
            metadata[basename] = json.loads(archive.read(info).decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            violations.append(
                f"built-in knowledge metadata is invalid UTF-8 JSON: {name}"
            )
    if violations:
        return violations
    manifest_name = (pack_root / "manifest.json").as_posix()
    manifest_cards, manifest_violations = _manifest_card_paths(
        metadata["manifest.json"],
        manifest_name,
    )
    violations.extend(manifest_violations)

    archive_cards = {
        PurePosixPath(name).relative_to(pack_root).as_posix()
        for name in entries
        if PurePosixPath(name).is_relative_to(pack_root / "cards")
        and PurePosixPath(name).suffix.lower() == ".md"
    }
    for card_path in sorted(manifest_cards - archive_cards):
        violations.append(
            "built-in knowledge card is missing or not an ordinary file: "
            f"{card_path}"
        )
    for card_path in sorted(archive_cards - manifest_cards):
        violations.append(
            "built-in knowledge archive has an undeclared card: "
            f"{card_path}"
        )
    return violations


def _knowledge_pack_violations(archive_path: Path) -> list[str]:
    with zipfile.ZipFile(archive_path) as archive:
        entries = {
            _relative_entry_name(info.filename): info
            for info in archive.infolist()
            if _ordinary_zip_entry(info)
        }
        roots = [_KNOWLEDGE_PACK_ROOT]
        if any(
            PurePosixPath(name).is_relative_to(
                _COMMUNITY_KNOWLEDGE_PACK_ROOT
            )
            for name in entries
        ):
            roots.append(_COMMUNITY_KNOWLEDGE_PACK_ROOT)
        return [
            violation
            for root in roots
            for violation in _knowledge_pack_violations_for_root(
                archive,
                entries,
                root,
            )
        ]


def _ordinary_source_file(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(status, "st_file_attributes", 0)
    return (
        stat.S_ISREG(status.st_mode)
        and not path.is_symlink()
        and not (reparse_flag and attributes & reparse_flag)
    )


def inspect_knowledge_source(
    source_root: Path,
    tracked_files: Iterable[str],
    *,
    knowledge_pack_root: PurePosixPath = _KNOWLEDGE_PACK_ROOT,
) -> list[str]:
    pack_root = source_root / knowledge_pack_root.as_posix()
    tracked = {
        PurePosixPath(str(path).replace("\\", "/")).as_posix()
        for path in tracked_files
    }
    violations: list[str] = []
    metadata: dict[str, object] = {}
    for basename in _KNOWLEDGE_METADATA:
        relative = (knowledge_pack_root / basename).as_posix()
        path = source_root / relative
        if not _ordinary_source_file(path):
            violations.append(
                f"built-in knowledge metadata is missing or not ordinary: {relative}"
            )
            continue
        if relative not in tracked:
            violations.append(
                f"built-in knowledge metadata is not tracked by git: {relative}"
            )
        try:
            if path.stat().st_size > _MAX_TEXT_SCAN_BYTES:
                raise ValueError
            metadata[basename] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            violations.append(
                f"built-in knowledge metadata is invalid UTF-8 JSON: {relative}"
            )

    if "manifest.json" not in metadata:
        return sorted(set(violations))
    manifest_cards, manifest_violations = _manifest_card_paths(
        metadata["manifest.json"],
        (knowledge_pack_root / "manifest.json").as_posix(),
    )
    violations.extend(manifest_violations)

    cards_root = pack_root / "cards"
    disk_cards: set[str] = set()
    if cards_root.is_dir():
        for path in cards_root.rglob("*.md"):
            relative = path.relative_to(pack_root).as_posix()
            if _ordinary_source_file(path):
                disk_cards.add(relative)
            else:
                violations.append(
                    f"built-in knowledge card is not an ordinary file: {relative}"
                )

    for card_path in sorted(manifest_cards - disk_cards):
        violations.append(
            f"built-in knowledge manifest card is missing from disk: {card_path}"
        )
    for card_path in sorted(disk_cards - manifest_cards):
        violations.append(
            f"built-in knowledge disk has an undeclared card: {card_path}"
        )
    for card_path in sorted(manifest_cards):
        relative = (knowledge_pack_root / card_path).as_posix()
        if relative not in tracked:
            violations.append(
                f"built-in knowledge manifest card is not tracked by git: {relative}"
            )
    return sorted(set(violations))


def inspect_git_knowledge_source(source_root: Path) -> list[str]:
    knowledge_pack_roots = (
        _KNOWLEDGE_PACK_ROOT,
        _COMMUNITY_KNOWLEDGE_PACK_ROOT,
    )
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "ls-files",
            "-z",
            "--",
            *(root.as_posix() for root in knowledge_pack_roots),
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"git ls-files failed for built-in knowledge: {detail}")
    tracked = [
        value.decode("utf-8", errors="surrogateescape")
        for value in completed.stdout.split(b"\0")
        if value
    ]
    return sorted(
        {
            violation
            for knowledge_pack_root in knowledge_pack_roots
            for violation in inspect_knowledge_source(
                source_root,
                tracked,
                knowledge_pack_root=knowledge_pack_root,
            )
        }
    )


def inspect_release(
    package: Path,
    *,
    forbidden_text: Iterable[str] = (),
) -> list[str]:
    if package.is_dir():
        entries: Iterable[tuple[str, bytes]] = _directory_entries(package)
        knowledge_violations: list[str] = []
    elif package.is_file() and package.suffix.lower() == ".zip":
        entries = _zip_entries(package)
        knowledge_violations = _knowledge_pack_violations(package)
    else:
        raise ValueError("release target must be a directory or .zip archive")

    violations: list[str] = []
    for name, data in entries:
        violations.extend(_path_violations(name))
        violations.extend(_content_violations(name, data, forbidden_text))
    violations.extend(knowledge_violations)
    return sorted(set(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject private, generated, or unlicensed files in a public Big-Chicken Houdini Intelligence Agent release."
    )
    parser.add_argument(
        "package",
        nargs="?",
        type=Path,
        help="Release staging directory or ZIP",
    )
    parser.add_argument(
        "--source-tree",
        type=Path,
        help="Validate the built-in knowledge pack against real git ls-files",
    )
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="Reject an exact local path or text value from release contents",
    )
    args = parser.parse_args(argv)
    if (args.package is None) == (args.source_tree is None):
        parser.error("provide exactly one package or --source-tree")

    try:
        if args.source_tree is not None:
            violations = inspect_git_knowledge_source(args.source_tree.resolve())
        else:
            violations = inspect_release(
                args.package.resolve(),
                forbidden_text=args.forbid_text,
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"release hygiene check failed: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("release hygiene check rejected the package:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("release hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
