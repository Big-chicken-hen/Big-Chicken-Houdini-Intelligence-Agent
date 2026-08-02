"""Finite real app-server Thread lifecycle smoke with project-local state only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402


def _codex_executable() -> Path:
    configured = os.environ.get("HIA_CODEX_EXE")
    candidate = (
        Path(configured)
        if configured
        else PROJECT_ROOT / ".runtime/toolchains/codex/0.144.3/codex.exe"
    ).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(
            "Set HIA_CODEX_EXE to a real Codex executable; tests/fakes are not accepted"
        )
    if "tests" in {part.lower() for part in candidate.parts}:
        raise ValueError("The smoke test refuses executables under tests")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    codex_exe = _codex_executable()
    run_id = uuid.uuid4().hex
    run_root = PROJECT_ROOT / ".runtime" / "smoke" / "codex-project-threads" / run_id
    codex_home = run_root / "codex-home"
    temp_directory = run_root / "tmp"
    codex_home.mkdir(parents=True, exist_ok=False)
    temp_directory.mkdir(parents=True, exist_ok=False)

    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "TEMP": str(temp_directory),
            "TMP": str(temp_directory),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    for name in (
        "CODEX_THREAD_ID",
        "HIA_BRIDGE_URL",
        "HIA_BRIDGE_TOKEN",
        "HIA_SCENE_EXECUTOR_TOKEN",
    ):
        environment.pop(name, None)

    version = subprocess.check_output(
        [str(codex_exe), "--version"],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        timeout=15,
    ).strip()
    policy = ProtocolPolicy.from_project_root(PROJECT_ROOT)
    client = CodexStdioClient(
        [
            str(codex_exe),
            "app-server",
            "--disable",
            "plugins",
            "--disable",
            "apps",
            "--disable",
            "remote_plugin",
            "--disable",
            "plugin_sharing",
        ],
        cwd=run_root,
        environment=environment,
        policy=policy,
        request_timeout=15.0,
    )
    thread_id: str | None = None
    initialize_result = None
    read_result = None
    delete_result = None
    delete_acknowledged = False
    cleanup_error: str | None = None
    try:
        client.start()
        initialize_result = client.initialize()
        started = client.request(
            "thread/start",
            {
                "cwd": str(run_root),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": "read-only",
                "ephemeral": False,
                "developerInstructions": (
                    "Protocol smoke only. Do not run a Turn, tool, command, or network request."
                ),
                "threadSource": f"hia-smoke/{run_id}",
                "config": {},
            },
        )
        thread = started.get("thread") if isinstance(started, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("real thread/start did not return a Thread identity")
        read_result = client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": False},
        )
        read_thread = (
            read_result.get("thread") if isinstance(read_result, dict) else None
        )
        if not isinstance(read_thread, dict) or read_thread.get("id") != thread_id:
            raise RuntimeError("real thread/read did not return the created Thread")
        delete_result = client.request("thread/delete", {"threadId": thread_id})
        delete_acknowledged = True
        thread_id = None
    finally:
        if thread_id is not None and client.is_running:
            try:
                client.request("thread/delete", {"threadId": thread_id})
            except Exception as exc:  # report exact owned-Thread cleanup failure
                cleanup_error = f"{type(exc).__name__}: {exc}"
        if client.is_running:
            try:
                residual = client.request(
                    "thread/list",
                    {
                        "cwd": [str(run_root)],
                        "archived": False,
                        "limit": 20,
                        "modelProviders": [],
                        "useStateDbOnly": True,
                        "sortKey": "recency_at",
                        "sortDirection": "desc",
                    },
                )
                entries = residual.get("data") if isinstance(residual, dict) else None
                if not isinstance(entries, list):
                    raise RuntimeError("cleanup thread/list response is malformed")
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    residual_id = entry.get("id")
                    residual_cwd = entry.get("cwd")
                    if (
                        isinstance(residual_id, str)
                        and residual_id
                        and isinstance(residual_cwd, str)
                        and Path(residual_cwd).resolve() == run_root.resolve()
                    ):
                        client.request("thread/delete", {"threadId": residual_id})
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                cleanup_error = (
                    f"{cleanup_error}; {detail}" if cleanup_error else detail
                )
        process_id = client.process_id
        client.close()

    initialize = initialize_result if isinstance(initialize_result, dict) else {}
    read_thread = read_result.get("thread") if isinstance(read_result, dict) else {}
    output = {
        "ok": cleanup_error is None and not client.is_running,
        "real_app_server": True,
        "executable": str(codex_exe),
        "executable_sha256": _sha256(codex_exe),
        "version": version,
        "process_id": process_id,
        "initialize": {
            "userAgent": initialize.get("userAgent"),
            "platformFamily": initialize.get("platformFamily"),
        },
        "thread": {
            "created_id": read_thread.get("id") if isinstance(read_thread, dict) else None,
            "read_turn_count": len(read_thread.get("turns", []))
            if isinstance(read_thread, dict) and isinstance(read_thread.get("turns"), list)
            else None,
            "delete_acknowledged": delete_acknowledged,
        },
        "process_reaped": not client.is_running,
        "cleanup_error": cleanup_error,
        "runtime_root": str(run_root),
        "methods_exercised": [
            "initialize",
            "initialized",
            "thread/start",
            "thread/read",
            "thread/delete",
            "stdin close/process reap",
        ],
    }
    result_path = run_root / "result.json"
    result_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
