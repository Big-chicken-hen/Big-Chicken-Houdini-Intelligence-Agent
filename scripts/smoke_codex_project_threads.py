"""Fork one existing terminal smoke Thread with the pinned real app-server.

The source Thread is read-only and preserved.  Only the fork created by this
process is deleted.
"""

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
    source_thread_id = os.environ.get("HIA_SMOKE_SOURCE_THREAD_ID", "").strip()
    model = os.environ.get("HIA_SMOKE_MODEL", "").strip()
    reasoning_effort = os.environ.get("HIA_SMOKE_REASONING_EFFORT", "").strip()
    service_tier = os.environ.get("HIA_SMOKE_SERVICE_TIER", "default").strip()
    if not source_thread_id or not model or not service_tier:
        raise ValueError(
            "HIA_SMOKE_SOURCE_THREAD_ID, HIA_SMOKE_MODEL, and "
            "HIA_SMOKE_SERVICE_TIER must identify one existing terminal smoke Thread"
        )
    run_id = uuid.uuid4().hex
    run_root = PROJECT_ROOT / ".runtime" / "smoke" / "codex-project-threads" / run_id
    codex_home = PROJECT_ROOT / ".runtime" / "codex-home"
    temp_directory = run_root / "tmp"
    codex_home.mkdir(parents=True, exist_ok=True)
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
        request_timeout=45.0,
    )
    owned_thread_ids: set[str] = set()
    initialize_result = None
    read_result = None
    fork_result = None
    completed_turn: dict[str, object] | None = None
    new_delete_acknowledged = False
    cleanup_error: str | None = None

    try:
        client.start()
        initialize_result = client.initialize()
        resumed = client.request(
            "thread/resume",
            {
                "threadId": source_thread_id,
                "cwd": str(run_root),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "developerInstructions": (
                    "Protocol smoke only. Never call tools or request approval."
                ),
                "model": model,
                "reasoningEffort": reasoning_effort or None,
                "serviceTier": service_tier,
                "config": {},
            },
        )
        if (
            not isinstance(resumed, dict)
            or resumed.get("approvalPolicy") != "never"
            or resumed.get("model") != model
            or resumed.get("reasoningEffort") != (reasoning_effort or None)
            or resumed.get("serviceTier") != service_tier
            or resumed.get("sandbox")
            != {"type": "readOnly", "networkAccess": False}
        ):
            profile = {
                key: resumed.get(key) if isinstance(resumed, dict) else None
                for key in (
                    "model",
                    "reasoningEffort",
                    "serviceTier",
                    "approvalPolicy",
                    "sandbox",
                )
            }
            raise RuntimeError(
                "real thread/resume did not apply the smoke profile: "
                + json.dumps(profile, ensure_ascii=False, sort_keys=True)
            )
        read_result = client.request(
            "thread/read",
            {"threadId": source_thread_id, "includeTurns": True},
        )
        read_thread = (
            read_result.get("thread") if isinstance(read_result, dict) else None
        )
        if (
            not isinstance(read_thread, dict)
            or read_thread.get("id") != source_thread_id
        ):
            raise RuntimeError("real thread/read did not return the source Thread")
        turns = read_thread.get("turns") if isinstance(read_thread, dict) else None
        if isinstance(turns, list):
            completed_turn = next(
                (
                    candidate
                    for candidate in reversed(turns)
                    if isinstance(candidate, dict)
                    and candidate.get("status")
                    in {"completed", "interrupted", "failed"}
                ),
                None,
            )
        if completed_turn is None:
            raise RuntimeError("source smoke Thread has no terminal Turn")
        turn_id = completed_turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("source smoke Thread terminal Turn has no identity")
        thread_id = source_thread_id
        source = read_thread.get("threadSource")
        fork_result = client.request(
            "thread/fork",
            {
                "threadId": thread_id,
                "lastTurnId": turn_id,
                "cwd": str(run_root),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "ephemeral": False,
                "developerInstructions": (
                    "Protocol smoke only. Never call tools or request approval."
                ),
                "threadSource": source,
                "model": model,
                "reasoningEffort": reasoning_effort or None,
                "serviceTier": service_tier,
                "config": {},
            },
        )
        forked_thread = (
            fork_result.get("thread") if isinstance(fork_result, dict) else None
        )
        forked_id = (
            forked_thread.get("id") if isinstance(forked_thread, dict) else None
        )
        if (
            not isinstance(forked_id, str)
            or not forked_id
            or forked_id == thread_id
            or forked_thread.get("forkedFromId") != thread_id
            or forked_thread.get("threadSource") != source
            or fork_result.get("approvalPolicy") != "never"
            or fork_result.get("model") != model
            or fork_result.get("reasoningEffort") != (reasoning_effort or None)
            or fork_result.get("serviceTier") != service_tier
            or fork_result.get("sandbox")
            != {"type": "readOnly", "networkAccess": False}
        ):
            raise RuntimeError("real thread/fork did not preserve direct profile fields")
        owned_thread_ids.add(forked_id)
        active_thread_id = forked_id
        rebound = client.request(
            "thread/read",
            {"threadId": active_thread_id, "includeTurns": False},
        )
        rebound_thread = (
            rebound.get("thread") if isinstance(rebound, dict) else None
        )
        if (
            not isinstance(rebound_thread, dict)
            or rebound_thread.get("id") != active_thread_id
            or rebound_thread.get("forkedFromId") != thread_id
        ):
            raise RuntimeError("real forked Thread identity readback failed")
        client.request("thread/delete", {"threadId": active_thread_id})
        owned_thread_ids.discard(active_thread_id)
        new_delete_acknowledged = True
    finally:
        if client.is_running:
            for owned_thread_id in tuple(owned_thread_ids):
                try:
                    client.request("thread/delete", {"threadId": owned_thread_id})
                    owned_thread_ids.discard(owned_thread_id)
                except Exception as exc:  # report exact owned-Thread cleanup failure
                    detail = f"{type(exc).__name__}: {exc}"
                    cleanup_error = (
                        f"{cleanup_error}; {detail}" if cleanup_error else detail
                    )
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
    forked_thread = fork_result.get("thread") if isinstance(fork_result, dict) else {}
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
            "source_id": read_thread.get("id") if isinstance(read_thread, dict) else None,
            "forked_id": forked_thread.get("id")
            if isinstance(forked_thread, dict)
            else None,
            "forked_from_id": forked_thread.get("forkedFromId")
            if isinstance(forked_thread, dict)
            else None,
            "read_turn_count": len(read_thread.get("turns", []))
            if isinstance(read_thread, dict) and isinstance(read_thread.get("turns"), list)
            else None,
            "source_preserved": True,
            "new_delete_acknowledged": new_delete_acknowledged,
            "terminal_turn_id": completed_turn.get("id")
            if isinstance(completed_turn, dict)
            else None,
        },
        "process_reaped": not client.is_running,
        "cleanup_error": cleanup_error,
        "runtime_root": str(run_root),
        "methods_exercised": [
            "initialize",
            "initialized",
            "thread/resume",
            "thread/read",
            "thread/fork",
            "identity readback",
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
