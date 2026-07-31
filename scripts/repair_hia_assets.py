"""Install and verify the one supported local OCR/ASR asset profile.

This command intentionally owns no durable repair state.  Package managers,
the Hugging Face snapshot cache, and the resumable FFmpeg ``.part`` download
make the same command safe to rerun after a failure.  Every writable path is
kept below the selected project root.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


PROTOCOL = "hia-asset-repair-jsonl/1"
OCR_PACKAGES = ("rapidocr", "onnxruntime", "pypdfium2")
ASR_PACKAGES = ("faster-whisper",)
PACKAGE_MODULES = ("rapidocr", "onnxruntime", "pypdfium2", "faster_whisper")
ASR_PROFILE = "large-v3-turbo"
ASR_REPOSITORY = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
ASR_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
ASR_MODEL_FILE = "model.bin"
ASR_MODEL_FILE_SHA256 = (
    "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"
)
ASR_MODEL_RELATIVE = Path(".runtime") / "models" / "asr" / "faster-whisper"
ASSET_REPAIR_RUNTIME_RELATIVE = Path(".runtime") / "a"
ASR_REQUIRED_FILES = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
)
ASR_ESTIMATED_DOWNLOAD_BYTES = 1_750_000_000
ASR_ESTIMATED_DISK_BYTES = 2_000_000_000
FFMPEG_VERSION = "8.1.2"
FFMPEG_URL = (
    "https://www.gyan.dev/ffmpeg/builds/packages/"
    "ffmpeg-8.1.2-essentials_build.zip"
)
FFMPEG_SHA256 = (
    "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
)
FFMPEG_ARCHIVE_BYTES_ESTIMATE = 104_000_000
MAX_FFMPEG_ARCHIVE_BYTES = 192 * 1024 * 1024
DOWNLOAD_PROGRESS_BYTES = 8 * 1024 * 1024
FFMPEG_DOWNLOAD_ATTEMPTS = 4
ASR_MODEL_DOWNLOAD_ATTEMPTS = 256
PACKAGE_INSTALL_TIMEOUT_SECONDS = 10 * 60
PACKAGE_INSTALL_ATTEMPTS = 2
UV_HTTP_CONNECT_TIMEOUT_SECONDS = 15
UV_HTTP_TIMEOUT_SECONDS = 60
UV_HTTP_RETRIES = 1
MODEL_DOWNLOAD_ATTEMPTS = 2
HF_HUB_ETAG_TIMEOUT_SECONDS = 30
HF_HUB_DOWNLOAD_TIMEOUT_SECONDS = 120
HF_HUB_MAX_WORKERS = 1


class RepairError(RuntimeError):
    """A bounded, user-actionable repair failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JsonlEmitter:
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    def emit(self, event: str, **fields: Any) -> None:
        payload = {
            "protocol": PROTOCOL,
            "event": event,
            "action": "assets.repair",
            **fields,
        }
        self.stream.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        self.stream.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair the project-local HIA asset extraction profile."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--asr-model-path", default="")
    return parser


def _project_root(value: str) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except OSError as exc:
        raise RepairError(
            "PROJECT_ROOT_INVALID",
            "The project root does not exist.",
        ) from exc
    if not root.is_dir():
        raise RepairError(
            "PROJECT_ROOT_INVALID",
            "The project root is not a directory.",
        )
    required = (
        root / "scripts" / "hia-knowledge.ps1",
        root
        / "houdini_package"
        / "python_libs"
        / "hia_mcp_runtime"
        / "local_extractors.py",
    )
    if not all(path.is_file() for path in required):
        raise RepairError(
            "PROJECT_ROOT_INVALID",
            "The selected directory is not an HIA project root.",
        )
    return root


def _inside_project(path: Path, root: Path, *, runtime_only: bool = False) -> Path:
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(
            (root / ".runtime").resolve(strict=False)
            if runtime_only
            else root
        )
    except (OSError, ValueError) as exc:
        boundary = "project .runtime" if runtime_only else "project root"
        raise RepairError(
            "REPAIR_PATH_UNSAFE",
            f"Repair path must stay within the {boundary}.",
        ) from exc
    return resolved


def _model_path(value: str, root: Path) -> Path:
    if not value.strip():
        candidate = root / ASR_MODEL_RELATIVE
    else:
        raw = Path(value)
        candidate = raw if raw.is_absolute() else root / raw
    return _inside_project(candidate, root, runtime_only=True)


def _assert_managed_python(root: Path) -> None:
    expected = (root / ".venv" / "Scripts" / "python.exe").resolve(
        strict=False
    )
    actual = Path(sys.executable).resolve(strict=True)
    if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        raise RepairError(
            "MANAGED_PYTHON_REQUIRED",
            "assets repair must run with the managed project .venv Python.",
        )


def _repair_environment(root: Path) -> dict[str, str]:
    cache_root = root / ASSET_REPAIR_RUNTIME_RELATIVE
    temporary = cache_root / "t"
    for directory in (
        cache_root / "p",
        cache_root / "h",
        cache_root / "d",
        cache_root / "u",
        temporary,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "HIA_PROJECT_ROOT": str(root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PIP_REQUIRE_VIRTUALENV": "1",
            "PIP_CACHE_DIR": str(cache_root / "p"),
            "UV_CACHE_DIR": str(cache_root / "u"),
            "UV_HTTP_CONNECT_TIMEOUT": str(UV_HTTP_CONNECT_TIMEOUT_SECONDS),
            "UV_HTTP_TIMEOUT": str(UV_HTTP_TIMEOUT_SECONDS),
            "UV_HTTP_RETRIES": str(UV_HTTP_RETRIES),
            "UV_CONCURRENT_DOWNLOADS": "1",
            "UV_CONCURRENT_BUILDS": "1",
            "HF_HOME": str(cache_root / "h"),
            "HF_HUB_CACHE": str(cache_root / "h" / "hub"),
            "HF_HUB_ETAG_TIMEOUT": str(HF_HUB_ETAG_TIMEOUT_SECONDS),
            "HF_HUB_DOWNLOAD_TIMEOUT": str(
                HF_HUB_DOWNLOAD_TIMEOUT_SECONDS
            ),
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "XDG_CACHE_HOME": str(cache_root),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
        }
    )
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(name, None)
    return environment


def _run(
    arguments: Sequence[str],
    *,
    root: Path,
    environment: Mapping[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            cwd=root,
            env=dict(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepairError(
            "REPAIR_PROCESS_TIMEOUT",
            f"Repair child process timed out after {timeout} seconds.",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepairError(
            "REPAIR_PROCESS_FAILED",
            f"Repair child process could not complete: {exc}",
        ) from exc


def _bounded_process_error(result: subprocess.CompletedProcess[str]) -> str:
    value = (result.stderr or result.stdout or "").strip()
    if len(value) > 2000:
        value = value[-2000:]
    return value or f"child process exited with code {result.returncode}"


def _uv_executable(root: Path) -> Path:
    uv_root = _inside_project(
        root / ".runtime" / "toolchains" / "hia-embedding" / "uv",
        root,
        runtime_only=True,
    )
    if not uv_root.is_dir() or uv_root.is_symlink():
        raise RepairError(
            "UV_REQUIRED",
            "The project-local uv runtime is unavailable; run environment-repair.",
        )
    candidates = sorted(uv_root.glob("*/uv.exe"))
    ordinary = [
        _inside_project(path, root, runtime_only=True)
        for path in candidates
        if path.is_file()
        and not path.is_symlink()
        and not path.parent.is_symlink()
    ]
    if len(ordinary) != 1:
        raise RepairError(
            "UV_REQUIRED",
            "Expected exactly one verified project-local uv runtime; "
            "run environment-repair.",
        )
    return ordinary[0]


def install_packages(
    root: Path,
    environment: Mapping[str, str],
    emitter: JsonlEmitter,
) -> None:
    packages = (*OCR_PACKAGES, *ASR_PACKAGES)
    uv = _uv_executable(root)
    emitter.emit(
        "progress",
        stage="package",
        component="python_dependencies",
        status="running",
        packages=list(packages),
        target_relative_path=".venv/Lib/site-packages",
    )
    command = (
        str(uv),
        "--no-config",
        "--no-progress",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--default-index",
        "https://pypi.org/simple",
        *packages,
    )
    failure = ""
    for attempt in range(1, PACKAGE_INSTALL_ATTEMPTS + 1):
        try:
            result = _run(
                command,
                root=root,
                environment=environment,
                timeout=PACKAGE_INSTALL_TIMEOUT_SECONDS,
            )
            if not result.returncode:
                break
            failure = _bounded_process_error(result)
        except RepairError as exc:
            if exc.code != "REPAIR_PROCESS_TIMEOUT":
                raise
            failure = exc.message
        if attempt < PACKAGE_INSTALL_ATTEMPTS:
            emitter.emit(
                "progress",
                stage="package",
                component="python_dependencies",
                status="retrying",
                attempt=attempt + 1,
                attempts=PACKAGE_INSTALL_ATTEMPTS,
                cache_reused=True,
                message=failure[:500],
            )
    else:
        raise RepairError(
            "PACKAGE_INSTALL_FAILED",
            "Asset dependency installation failed after "
            f"{PACKAGE_INSTALL_ATTEMPTS} bounded attempts: {failure}",
        )
    emitter.emit(
        "progress",
        stage="package",
        component="python_dependencies",
        status="ready",
        packages=list(packages),
        target_relative_path=".venv/Lib/site-packages",
    )


def _download_file(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    maximum_bytes: int,
    emitter: JsonlEmitter,
    stage: str = "download",
    component: str = "ffmpeg",
    estimated_bytes: int = FFMPEG_ARCHIVE_BYTES_ESTIMATE,
    attempts: int = FFMPEG_DOWNLOAD_ATTEMPTS,
    error_code_prefix: str = "FFMPEG",
    artifact_name: str = "FFmpeg archive",
) -> None:
    if destination.is_file() and _sha256_file(destination) == expected_sha256:
        emitter.emit(
            "progress",
            stage=stage,
            component=component,
            status="ready",
            bytes=destination.stat().st_size,
            estimated_bytes=estimated_bytes,
            cached=True,
        )
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    downloaded = 0
    for attempt in range(1, attempts + 1):
        existing = partial.stat().st_size if partial.is_file() else 0
        request = urllib.request.Request(
            url,
            headers={"Range": f"bytes={existing}-"} if existing else {},
        )
        emitter.emit(
            "progress",
            stage=stage,
            component=component,
            status="running" if attempt == 1 else "retrying",
            attempt=attempt,
            attempts=attempts,
            bytes=existing,
            estimated_bytes=estimated_bytes,
            resumed=bool(existing),
        )
        try:
            response = urllib.request.urlopen(request, timeout=60)
            with response:
                status_code = int(response.getcode() or 0)
                if existing > 0 and status_code != 206:
                    raise urllib.error.URLError(
                        "server ignored the Range request; existing checkpoint "
                        "was preserved"
                    )
                append = existing > 0
                downloaded = existing if append else 0
                mode = "ab" if append else "wb"
                content_length = int(
                    response.headers.get("Content-Length", "0") or 0
                )
                expected_total = downloaded + content_length
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range:
                    total = content_range.rsplit("/", 1)[-1]
                    if total.isdigit():
                        expected_total = int(total)
                if expected_total > maximum_bytes:
                    raise RepairError(
                        f"{error_code_prefix}_DOWNLOAD_TOO_LARGE",
                        f"{artifact_name} exceeded its bounded size limit.",
                    )
                next_progress = downloaded + DOWNLOAD_PROGRESS_BYTES
                with partial.open(mode) as output:
                    while True:
                        part = response.read(1024 * 1024)
                        if not part:
                            break
                        downloaded += len(part)
                        if downloaded > maximum_bytes:
                            raise RepairError(
                                f"{error_code_prefix}_DOWNLOAD_TOO_LARGE",
                                f"{artifact_name} exceeded its bounded size limit.",
                            )
                        output.write(part)
                        if downloaded >= next_progress:
                            emitter.emit(
                                "progress",
                                stage=stage,
                                component=component,
                                status="running",
                                bytes=downloaded,
                                estimated_bytes=expected_total,
                                resumed=append,
                            )
                            next_progress = downloaded + DOWNLOAD_PROGRESS_BYTES
                if expected_total and downloaded < expected_total:
                    raise http.client.IncompleteRead(
                        b"",
                        expected_total - downloaded,
                    )
        except RepairError:
            raise
        except urllib.error.HTTPError as exc:
            if (
                exc.code == 416
                and existing
                and _sha256_file(partial) == expected_sha256
            ):
                break
            if attempt == attempts:
                raise RepairError(
                    f"{error_code_prefix}_DOWNLOAD_FAILED",
                    f"{artifact_name} download failed after "
                    f"{attempt} attempts: {exc}",
                ) from exc
            continue
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            if attempt == attempts:
                raise RepairError(
                    f"{error_code_prefix}_DOWNLOAD_FAILED",
                    f"{artifact_name} download failed after "
                    f"{attempt} attempts: {exc}",
                ) from exc
            continue
        digest = _sha256_file(partial)
        if digest == expected_sha256:
            break
        raise RepairError(
            f"{error_code_prefix}_CHECKSUM_MISMATCH",
            f"{artifact_name} checksum verification failed; the checkpoint "
            "was preserved for diagnosis.",
        )
    else:  # pragma: no cover - the bounded loop always breaks or raises.
        raise RepairError(
            f"{error_code_prefix}_DOWNLOAD_FAILED",
            f"{artifact_name} download did not complete.",
        )
    os.replace(partial, destination)
    emitter.emit(
        "progress",
        stage=stage,
        component=component,
        status="ready",
        bytes=destination.stat().st_size,
        estimated_bytes=estimated_bytes,
        cached=False,
    )


def install_ffmpeg(root: Path, emitter: JsonlEmitter) -> Path:
    target = root / ".runtime" / "dependencies" / "ffmpeg" / "bin"
    ffmpeg = target / "ffmpeg.exe"
    ffprobe = target / "ffprobe.exe"
    if ffmpeg.is_file() and ffprobe.is_file():
        emitter.emit(
            "progress",
            stage="verify",
            component="ffmpeg",
            status="ready",
            target_relative_path=target.relative_to(root).as_posix(),
            cached=True,
        )
        return ffmpeg
    downloads = root / ASSET_REPAIR_RUNTIME_RELATIVE / "d"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
    _download_file(
        FFMPEG_URL,
        archive,
        expected_sha256=FFMPEG_SHA256,
        maximum_bytes=MAX_FFMPEG_ARCHIVE_BYTES,
        emitter=emitter,
    )
    target.mkdir(parents=True, exist_ok=True)
    required = {"ffmpeg.exe": ffmpeg, "ffprobe.exe": ffprobe}
    try:
        with zipfile.ZipFile(archive) as package:
            members: dict[str, str] = {}
            for name in package.namelist():
                pure = PurePosixPath(name)
                if (
                    pure.name in required
                    and len(pure.parts) >= 2
                    and pure.parts[-2].casefold() == "bin"
                ):
                    members[pure.name] = name
            if set(members) != set(required):
                raise RepairError(
                    "FFMPEG_ARCHIVE_INVALID",
                    "FFmpeg archive does not contain the required executables.",
                )
            for name, destination in required.items():
                temporary = destination.with_suffix(".exe.tmp")
                with package.open(members[name]) as source:
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                os.replace(temporary, destination)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RepairError(
            "FFMPEG_ARCHIVE_INVALID",
            f"FFmpeg archive could not be installed: {exc}",
        ) from exc
    emitter.emit(
        "progress",
        stage="verify",
        component="ffmpeg",
        status="installed",
        target_relative_path=target.relative_to(root).as_posix(),
        cached=False,
    )
    return ffmpeg


def install_model(
    root: Path,
    model_path: Path,
    environment: Mapping[str, str],
    emitter: JsonlEmitter,
) -> None:
    missing = _missing_model_files(model_path)
    relative = model_path.relative_to(root).as_posix()
    if not missing:
        emitter.emit(
            "progress",
            stage="model",
            component="asr",
            status="ready",
            repository=ASR_REPOSITORY,
            revision=ASR_REVISION,
            profile=ASR_PROFILE,
            estimated_download_bytes=ASR_ESTIMATED_DOWNLOAD_BYTES,
            estimated_disk_bytes=ASR_ESTIMATED_DISK_BYTES,
            target_relative_path=relative,
            cached=True,
        )
        return
    emitter.emit(
        "progress",
        stage="model",
        component="asr",
        status="downloading",
        repository=ASR_REPOSITORY,
        revision=ASR_REVISION,
        profile=ASR_PROFILE,
        estimated_download_bytes=ASR_ESTIMATED_DOWNLOAD_BYTES,
        estimated_disk_bytes=ASR_ESTIMATED_DISK_BYTES,
        target_relative_path=relative,
        cached=False,
    )
    for name in (
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_HUB_ETAG_TIMEOUT",
        "HF_HUB_DOWNLOAD_TIMEOUT",
        "HF_HUB_DISABLE_PROGRESS_BARS",
        "HF_HUB_DISABLE_TELEMETRY",
        "HF_HUB_DISABLE_XET",
    ):
        value = environment.get(name)
        if value is not None:
            os.environ[name] = value
    try:
        from huggingface_hub import snapshot_download
    except (ImportError, ModuleNotFoundError) as exc:
        raise RepairError(
            "MODEL_DOWNLOADER_MISSING",
            "faster-whisper did not provide huggingface_hub.",
        ) from exc
    model_path.mkdir(parents=True, exist_ok=True)
    model_url = (
        f"https://huggingface.co/{ASR_REPOSITORY}/resolve/"
        f"{ASR_REVISION}/{ASR_MODEL_FILE}?download=true"
    )
    _download_file(
        model_url,
        model_path / ASR_MODEL_FILE,
        expected_sha256=ASR_MODEL_FILE_SHA256,
        maximum_bytes=ASR_ESTIMATED_DISK_BYTES,
        emitter=emitter,
        stage="model",
        component="asr",
        estimated_bytes=ASR_ESTIMATED_DOWNLOAD_BYTES,
        attempts=ASR_MODEL_DOWNLOAD_ATTEMPTS,
        error_code_prefix="MODEL",
        artifact_name="ASR model",
    )
    failure = ""
    for attempt in range(1, MODEL_DOWNLOAD_ATTEMPTS + 1):
        try:
            snapshot_download(
                repo_id=ASR_REPOSITORY,
                revision=ASR_REVISION,
                local_dir=str(model_path),
                cache_dir=environment["HF_HUB_CACHE"],
                etag_timeout=HF_HUB_ETAG_TIMEOUT_SECONDS,
                max_workers=HF_HUB_MAX_WORKERS,
                ignore_patterns=(ASR_MODEL_FILE,),
            )
            break
        except Exception as exc:
            failure = str(exc).strip()[:1000] or type(exc).__name__
            if attempt < MODEL_DOWNLOAD_ATTEMPTS:
                emitter.emit(
                    "progress",
                    stage="model",
                    component="asr",
                    status="retrying",
                    attempt=attempt + 1,
                    attempts=MODEL_DOWNLOAD_ATTEMPTS,
                    cache_reused=True,
                    message=failure,
                )
    else:
        raise RepairError(
            "MODEL_DOWNLOAD_FAILED",
            "ASR model download failed after "
            f"{MODEL_DOWNLOAD_ATTEMPTS} bounded attempts: {failure}",
        )
    missing = _missing_model_files(model_path)
    if missing:
        raise RepairError(
            "MODEL_VERIFY_FAILED",
            "ASR model is incomplete: " + ", ".join(missing),
        )
    emitter.emit(
        "progress",
        stage="model",
        component="asr",
        status="ready",
        repository=ASR_REPOSITORY,
        revision=ASR_REVISION,
        profile=ASR_PROFILE,
        estimated_download_bytes=ASR_ESTIMATED_DOWNLOAD_BYTES,
        estimated_disk_bytes=ASR_ESTIMATED_DISK_BYTES,
        target_relative_path=relative,
        cached=False,
    )


def verify_profile(
    root: Path,
    model_path: Path,
    ffmpeg: Path,
    environment: Mapping[str, str],
    emitter: JsonlEmitter,
) -> dict[str, Any]:
    emitter.emit(
        "progress",
        stage="verify",
        component="asset_profile",
        status="running",
    )
    missing_modules = [
        name for name in PACKAGE_MODULES if importlib.util.find_spec(name) is None
    ]
    if missing_modules:
        raise RepairError(
            "PACKAGE_VERIFY_FAILED",
            "Installed modules are unavailable: " + ", ".join(missing_modules),
        )
    missing_model = _missing_model_files(model_path)
    if missing_model:
        raise RepairError(
            "MODEL_VERIFY_FAILED",
            "ASR model is incomplete: " + ", ".join(missing_model),
        )
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    for executable in (ffmpeg, ffprobe):
        if not executable.is_file():
            raise RepairError(
                "FFMPEG_VERIFY_FAILED",
                f"Missing project-local executable: {executable.name}",
            )
        result = _run(
            (str(executable), "-version"),
            root=root,
            environment=environment,
            timeout=30,
        )
        if result.returncode:
            raise RepairError(
                "FFMPEG_VERIFY_FAILED",
                f"{executable.name} verification failed: "
                + _bounded_process_error(result),
            )
    result = {
        "packages": [*OCR_PACKAGES, *ASR_PACKAGES],
        "asr_profile": ASR_PROFILE,
        "asr_repository": ASR_REPOSITORY,
        "asr_revision": ASR_REVISION,
        "estimated_download_bytes": ASR_ESTIMATED_DOWNLOAD_BYTES,
        "estimated_disk_bytes": ASR_ESTIMATED_DISK_BYTES,
        "asr_model_path": model_path.relative_to(root).as_posix(),
        "ffmpeg_path": ffmpeg.relative_to(root).as_posix(),
        "ffprobe_path": ffprobe.relative_to(root).as_posix(),
        "ready": True,
    }
    emitter.emit(
        "progress",
        stage="verify",
        component="asset_profile",
        status="ready",
        result=result,
    )
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _missing_model_files(model_path: Path) -> list[str]:
    missing = [
        name for name in ASR_REQUIRED_FILES if not (model_path / name).is_file()
    ]
    if not any(
        candidate.is_file()
        for candidate in (
            model_path / "vocabulary.json",
            model_path / "vocabulary.txt",
        )
    ):
        missing.append("vocabulary.json|vocabulary.txt")
    return missing


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: Any = None,
) -> int:
    stream = stdout if stdout is not None else sys.stdout
    emitter = JsonlEmitter(stream)
    try:
        arguments = _parser().parse_args(argv)
        root = _project_root(arguments.project_root)
        _assert_managed_python(root)
        model_path = _model_path(arguments.asr_model_path, root)
        environment = _repair_environment(root)
        emitter.emit(
            "started",
            stage="package",
            profile="local-ocr-asr",
            asr_profile=ASR_PROFILE,
            asr_repository=ASR_REPOSITORY,
            asr_revision=ASR_REVISION,
            estimated_download_bytes=ASR_ESTIMATED_DOWNLOAD_BYTES,
            estimated_disk_bytes=ASR_ESTIMATED_DISK_BYTES,
        )
        install_packages(root, environment, emitter)
        ffmpeg = install_ffmpeg(root, emitter)
        install_model(root, model_path, environment, emitter)
        result = verify_profile(
            root,
            model_path,
            ffmpeg,
            environment,
            emitter,
        )
        emitter.emit("completed", stage="complete", result=result)
        return 0
    except RepairError as exc:
        emitter.emit(
            "error",
            stage="failed",
            error={
                "code": exc.code,
                "message": exc.message,
                "recoverable": True,
            },
        )
        return 1
    except Exception as exc:
        emitter.emit(
            "error",
            stage="failed",
            error={
                "code": "ASSET_REPAIR_FAILED",
                "message": str(exc)[:2000],
                "recoverable": True,
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
