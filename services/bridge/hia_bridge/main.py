"""Bridge process entry point."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import threading
from pathlib import Path
from typing import Sequence

from hia_core.path_policy import PROJECT_ROOT, PathPolicyError, validate_project_subpath

from .codex_stdio import CodexStdioClient
from .errors import BridgeError
from .events import EventBuffer
from .http_server import BridgeApplication, LoopbackHTTPServer
from .protocol import ProtocolPolicy
from .session import BridgeSession


PINNED_CODEX_RELATIVE_PATH = Path(
    ".runtime/toolchains/codex/0.144.3/codex.exe"
)
CODEX_HOME_RELATIVE_PATH = Path(".runtime/codex-home")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Houdini Intelligence local Bridge")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--codex-exe",
        default=str(PROJECT_ROOT / PINNED_CODEX_RELATIVE_PATH),
    )
    parser.add_argument(
        "--codex-home",
        default=str(PROJECT_ROOT / CODEX_HOME_RELATIVE_PATH),
    )
    return parser


def _same_windows_path(left: Path, right: Path) -> bool:
    return str(left).replace("/", "\\").rstrip("\\").casefold() == str(
        right
    ).replace("/", "\\").rstrip("\\").casefold()


def _validated_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    project_root = Path(args.project_root)
    if not _same_windows_path(project_root, PROJECT_ROOT):
        raise BridgeError(
            "INVALID_PROJECT_ROOT",
            f"Project root must be exactly {PROJECT_ROOT}",
        )
    codex_exe = validate_project_subpath(args.codex_exe, project_root=project_root)
    expected_codex = project_root / PINNED_CODEX_RELATIVE_PATH
    if not _same_windows_path(codex_exe, expected_codex):
        raise BridgeError(
            "INVALID_CODEX_EXECUTABLE",
            f"Codex executable must be {expected_codex}",
        )
    if not codex_exe.is_file():
        raise BridgeError(
            "CODEX_EXECUTABLE_MISSING",
            f"Pinned Codex executable does not exist: {codex_exe}",
        )
    codex_home = validate_project_subpath(args.codex_home, project_root=project_root)
    expected_home = project_root / CODEX_HOME_RELATIVE_PATH
    if not _same_windows_path(codex_home, expected_home):
        raise BridgeError(
            "INVALID_CODEX_HOME",
            f"CODEX_HOME must be {expected_home}",
        )
    temp_directory = validate_project_subpath(
        codex_home / "tmp",
        project_root=project_root,
    )
    return project_root, codex_exe, codex_home, temp_directory


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project_root, codex_exe, codex_home, temp_directory = _validated_paths(args)
        codex_home.mkdir(parents=True, exist_ok=True)
        temp_directory.mkdir(parents=True, exist_ok=True)

        policy = ProtocolPolicy.from_project_root(project_root)
        child_environment = os.environ.copy()
        child_environment.update(
            {
                "CODEX_HOME": str(codex_home),
                "TEMP": str(temp_directory),
                "TMP": str(temp_directory),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        events = EventBuffer()
        client = CodexStdioClient(
            [str(codex_exe), "app-server"],
            cwd=project_root,
            environment=child_environment,
            policy=policy,
            request_timeout=45.0,
        )
        session = BridgeSession(project_root, client, events)
        session.start()

        token = secrets.token_urlsafe(32)
        application = BridgeApplication(session, events, token)
        server = LoopbackHTTPServer(("127.0.0.1", 0), application)

        def request_shutdown(*_: object) -> None:
            threading.Thread(
                target=server.shutdown,
                name="hia-signal-shutdown",
                daemon=True,
            ).start()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                signal.signal(signal_value, request_shutdown)

        host, port = server.server_address
        bootstrap = {
            "ok": True,
            "url": f"http://{host}:{port}",
            "token": token,
            "bridge_pid": os.getpid(),
            "codex_pid": client.process_id,
            "codex_version": policy.version,
            "transport": "stdio-jsonl",
        }
        print(json.dumps(bootstrap, ensure_ascii=False, separators=(",", ":")), flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
            session.close()
        return 0
    except (BridgeError, PathPolicyError) as exc:
        if hasattr(exc, "to_dict"):
            payload = exc.to_dict()
        else:
            payload = {
                "ok": False,
                "structured_error": {"code": "PATH_POLICY_ERROR", "message": str(exc)},
            }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        payload = {
            "ok": False,
            "structured_error": {
                "code": "BRIDGE_START_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
            },
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
