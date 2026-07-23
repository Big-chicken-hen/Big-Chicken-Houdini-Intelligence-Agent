from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath


_FORBIDDEN_COMPONENTS = {
    ".git",
    ".runtime",
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
    ".avi",
    ".bgeo",
    ".exr",
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
    ".vdb",
}
_FORBIDDEN_BASENAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "notice.steam-winter-sale.txt",
    "secrets.json",
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


def _content_violations(name: str, data: bytes) -> list[str]:
    if len(data) > _MAX_TEXT_SCAN_BYTES or b"\x00" in data:
        return []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(data):
            return [f"possible credential content: {_normalize_entry(name).as_posix()}"]
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


def inspect_release(package: Path) -> list[str]:
    if package.is_dir():
        entries: Iterable[tuple[str, bytes]] = _directory_entries(package)
    elif package.is_file() and package.suffix.lower() == ".zip":
        entries = _zip_entries(package)
    else:
        raise ValueError("release target must be a directory or .zip archive")

    violations: list[str] = []
    for name, data in entries:
        violations.extend(_path_violations(name))
        violations.extend(_content_violations(name, data))
    return sorted(set(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject private, generated, or unlicensed files in a public Big-Chicken Houdini Intelligence Agent release."
    )
    parser.add_argument("package", type=Path, help="Release staging directory or ZIP")
    args = parser.parse_args(argv)

    try:
        violations = inspect_release(args.package.resolve())
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
