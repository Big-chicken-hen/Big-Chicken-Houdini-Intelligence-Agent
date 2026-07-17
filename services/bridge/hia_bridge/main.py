"""Bridge process entry point."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import signal
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence

from hia_core.houdini_contract import B2_SCHEMA_VERSION, SchemaRegistry
from hia_core.path_policy import PROJECT_ROOT, PathPolicyError, validate_project_subpath

from .codex_stdio import CodexStdioClient
from .errors import BridgeError
from .events import EventBuffer
from .http_server import BridgeApplication, LoopbackHTTPServer
from .protocol import ProtocolPolicy
from .scene_queue import B2_READ_ONLY_PROFILE, SceneQueue
from .session import BridgeSession


PINNED_CODEX_RELATIVE_PATH = Path(
    ".runtime/toolchains/codex/0.144.3/codex.exe"
)
CODEX_HOME_RELATIVE_PATH = Path(".runtime/codex-home")
_LAUNCH_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_BRIDGE_URL_PATTERN = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})$"
)
_CODEX_CHILD_ENVIRONMENT_ALLOWLIST = (
    "ALL_PROXY",
    "COMSPEC",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TZ",
    "WINDIR",
)


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


def _required_launch_secret(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or _LAUNCH_SECRET_PATTERN.fullmatch(value) is None:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            f"Required launch credential is missing or invalid: {name}",
        )
    return value


def _required_bridge_url() -> tuple[str, int]:
    value = os.environ.get("HIA_BRIDGE_URL")
    if not isinstance(value, str):
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Required loopback Bridge URL is missing",
        )
    match = _BRIDGE_URL_PATTERN.fullmatch(value)
    if match is None:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Bridge URL must be an exact credential-free IPv4 loopback origin",
        )
    port = int(match.group(1))
    if not 1 <= port <= 65_535:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Bridge URL port is outside the valid range",
        )
    return value, port


def _prepend_environment_path(
    environment: dict[str, str],
    name: str,
    entries: Sequence[Path],
) -> None:
    existing = environment.get(name, "")
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in [*(str(entry) for entry in entries), *existing.split(os.pathsep)]:
        if not raw:
            continue
        key = raw.replace("/", "\\").rstrip("\\").casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(raw)
    environment[name] = os.pathsep.join(ordered)


def _allowlisted_child_environment(
    source: Mapping[str, str],
) -> dict[str, str]:
    """Copy only reviewed OS/network values into the owned Codex child."""

    by_casefold = {name.casefold(): value for name, value in source.items()}
    environment: dict[str, str] = {}
    for name in _CODEX_CHILD_ENVIRONMENT_ALLOWLIST:
        value = by_casefold.get(name.casefold())
        if isinstance(value, str) and "\x00" not in value:
            environment[name] = value
    return environment


def _redact_value(value: object, sensitive_values: Sequence[str]) -> object:
    if isinstance(value, str):
        redacted = value
        for sensitive in sorted(
            (item for item in sensitive_values if item),
            key=len,
            reverse=True,
        ):
            redacted = redacted.replace(sensitive, "[REDACTED]")
        return redacted
    if isinstance(value, dict):
        return {
            (
                _redact_value(key, sensitive_values)
                if isinstance(key, str)
                else key
            ): _redact_value(item, sensitive_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, sensitive_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, sensitive_values) for item in value)
    return value


def _toml_basic_string(value: str) -> str:
    if not isinstance(value, str) or not value or any(
        ord(character) < 0x20 for character in value
    ):
        raise BridgeError(
            "INVALID_CODEX_EXECUTABLE",
            "The MCP Python executable cannot be represented safely in TOML",
        )
    # JSON basic strings are a strict, safely escaped subset of TOML basic
    # strings for an ordinary Windows executable path.
    return json.dumps(value, ensure_ascii=True)


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
    session: BridgeSession | None = None
    server: LoopbackHTTPServer | None = None
    scene_queue: SceneQueue | None = None
    sensitive_values: list[str] = []
    try:
        project_root, codex_exe, codex_home, temp_directory = _validated_paths(args)
        codex_home.mkdir(parents=True, exist_ok=True)
        temp_directory.mkdir(parents=True, exist_ok=True)

        token = _required_launch_secret("HIA_BRIDGE_TOKEN")
        sensitive_values.append(token)
        scene_executor_token = _required_launch_secret(
            "HIA_SCENE_EXECUTOR_TOKEN"
        )
        sensitive_values.append(scene_executor_token)
        requested_bridge_url, requested_bridge_port = _required_bridge_url()
        sensitive_values.append(requested_bridge_url)
        if hmac.compare_digest(token, scene_executor_token):
            raise BridgeError(
                "INVALID_LAUNCH_ENVIRONMENT",
                "Bridge and scene executor credentials must be independent",
            )

        policy = ProtocolPolicy.from_project_root(project_root)
        child_environment = _allowlisted_child_environment(os.environ)
        resolved_python = str(Path(sys.executable).resolve())
        _prepend_environment_path(
            child_environment,
            "PATH",
            (Path(resolved_python).parent,),
        )
        _prepend_environment_path(
            child_environment,
            "PYTHONPATH",
            (
                project_root / "services" / "houdini_mcp",
                project_root / "src",
            ),
        )
        child_environment.update(
            {
                "CODEX_HOME": str(codex_home),
                "TEMP": str(temp_directory),
                "TMP": str(temp_directory),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "HIA_PROJECT_ROOT": str(project_root),
                "HIA_EXPECTED_PYTHON_EXE": resolved_python,
            }
        )
        events = EventBuffer()
        client = CodexStdioClient(
            [
                str(codex_exe),
                "app-server",
                "--strict-config",
                "-c",
                "mcp_servers.houdini_intelligence.command="
                + _toml_basic_string(resolved_python),
                "-c",
                "mcp_servers.houdini_intelligence.required=true",
            ],
            cwd=project_root,
            environment=child_environment,
            policy=policy,
            request_timeout=45.0,
        )
        session = BridgeSession(project_root, client, events)
        scene_launch_id = f"launch-{secrets.token_hex(16)}"
        scene_generation = 1
        houdini_process_nonce = f"houdini-{secrets.token_hex(16)}"
        scene_registry = SchemaRegistry.b2_read_only(
            project_root / "schemas" / "houdini-mcp" / B2_SCHEMA_VERSION
        )
        scene_queue = SceneQueue(
            scene_launch_id,
            scene_generation,
            expected_schema_digest=scene_registry.manifest_digest,
            expected_catalog_digest=None,
            profile=B2_READ_ONLY_PROFILE,
            expected_process_nonce=houdini_process_nonce,
        )
        application = BridgeApplication(
            session,
            events,
            token,
            scene_queue=scene_queue,
            scene_registry=scene_registry,
            scene_executor_token=scene_executor_token,
        )
        server = LoopbackHTTPServer(
            ("127.0.0.1", requested_bridge_port),
            application,
        )

        host, port = server.server_address
        bridge_url = f"http://{host}:{port}"
        if bridge_url != requested_bridge_url:
            raise BridgeError(
                "BRIDGE_BIND_MISMATCH",
                "Bridge did not bind the exact launch-scoped loopback origin",
            )
        client.set_environment_overlay(
            {
                "HIA_BRIDGE_URL": bridge_url,
                "HIA_BRIDGE_TOKEN": token,
            }
        )
        session.start()

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

        bootstrap = {
            "ok": True,
            "bridge_pid": os.getpid(),
            "codex_pid": client.process_id,
            "codex_version": policy.version,
            "transport": "stdio-jsonl",
            "scene": {
                "profile": "p2-v-b2-read-only",
                "launch_id": scene_launch_id,
                "generation": scene_generation,
                "process_nonce": houdini_process_nonce,
                "schema_version": scene_registry.schema_version,
                "schema_digest": scene_registry.manifest_digest,
            },
        }
        print(json.dumps(bootstrap, ensure_ascii=False, separators=(",", ":")), flush=True)
        server.serve_forever(poll_interval=0.25)
        return 0
    except (BridgeError, PathPolicyError) as exc:
        if hasattr(exc, "to_dict"):
            payload = exc.to_dict()
        else:
            payload = {
                "ok": False,
                "structured_error": {"code": "PATH_POLICY_ERROR", "message": str(exc)},
            }
        print(
            json.dumps(
                _redact_value(payload, sensitive_values),
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    except Exception as exc:
        payload = {
            "ok": False,
            "structured_error": {
                "code": "BRIDGE_START_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
            },
        }
        print(
            json.dumps(
                _redact_value(payload, sensitive_values),
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        try:
            if server is not None:
                server.server_close()
        finally:
            try:
                if scene_queue is not None:
                    scene_queue.shutdown()
            finally:
                if session is not None:
                    session.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
