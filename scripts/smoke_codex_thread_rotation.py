"""Run ThreadRotationService against the pinned real app-server.

The script forks one existing repository-local smoke Thread to create a disposable
source without modifying that fixture.  Its three compaction notifications are
synthetic service inputs, so this smoke validates the real service
fork/read/rebind/delete path but deliberately does not claim that automatic
compaction was produced or observed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.thread_rotation import (  # noqa: E402
    ThreadRotationAdapters,
    ThreadRotationProfile,
    ThreadRotationService,
)


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


def _create_disposable_source(
    client: CodexStdioClient,
    fixture_id: str,
    run_root: Path,
    requested_model: str | None,
    service_tier: str,
) -> tuple[str, str, str, dict[str, object]]:
    read = client.request(
        "thread/read", {"threadId": fixture_id, "includeTurns": True}
    )
    thread = read.get("thread") if isinstance(read, dict) else None
    turns = thread.get("turns") if isinstance(thread, dict) else None
    terminal = next(
        (
            turn
            for turn in reversed(turns if isinstance(turns, list) else [])
            if isinstance(turn, dict)
            and turn.get("status") in {"completed", "interrupted", "failed"}
        ),
        None,
    )
    terminal_turn_id = terminal.get("id") if isinstance(terminal, dict) else None
    thread_source = thread.get("threadSource") if isinstance(thread, dict) else None
    if not isinstance(terminal_turn_id, str) or not terminal_turn_id:
        raise RuntimeError("the app-server smoke fixture has no terminal Turn")
    if not isinstance(thread_source, str) or not thread_source:
        raise RuntimeError("the app-server smoke fixture has no native source")
    fork_params: dict[str, object] = {
        "threadId": fixture_id,
        "lastTurnId": terminal_turn_id,
        "cwd": str(run_root),
        "approvalPolicy": "never",
        "developerInstructions": "Run one disposable Thread rotation smoke.",
        "ephemeral": False,
        "threadSource": thread_source,
        "serviceTier": service_tier,
        "sandbox": "workspace-write",
        "config": {},
    }
    if requested_model is not None:
        fork_params["model"] = requested_model
    result = client.request("thread/fork", fork_params)
    forked = result.get("thread") if isinstance(result, dict) else None
    source_thread_id = forked.get("id") if isinstance(forked, dict) else None
    if (
        not isinstance(source_thread_id, str)
        or not source_thread_id
        or forked.get("forkedFromId") != fixture_id
    ):
        raise RuntimeError("fixture fork did not create one disposable source")
    return fixture_id, source_thread_id, terminal_turn_id, dict(result)


def main() -> int:
    codex_exe = _codex_executable()
    fixture_thread_id = os.environ.get("HIA_SMOKE_FIXTURE_THREAD_ID", "").strip()
    if not fixture_thread_id:
        raise ValueError("HIA_SMOKE_FIXTURE_THREAD_ID is required")
    requested_model = os.environ.get("HIA_SMOKE_MODEL", "").strip() or None
    service_tier = os.environ.get("HIA_SMOKE_SERVICE_TIER", "default").strip()
    if not service_tier:
        raise ValueError("HIA_SMOKE_SERVICE_TIER is required")

    run_id = uuid.uuid4().hex
    run_root = PROJECT_ROOT / ".runtime" / "smoke" / "thread-rotation" / run_id
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
        policy=ProtocolPolicy.from_project_root(PROJECT_ROOT),
        request_timeout=45.0,
    )
    rotation: ThreadRotationService | None = None
    rotation_events: list[dict[str, object]] = []
    owned_thread_ids: set[str] = set()
    cleanup_errors: list[str] = []
    source_thread_id: str | None = None
    replacement_thread_id: str | None = None
    terminal_turn_id: str | None = None
    initialize_result: object = None

    try:
        client.start()
        initialize_result = client.initialize()
        (
            fixture_thread_id,
            source_thread_id,
            terminal_turn_id,
            start_result,
        ) = _create_disposable_source(
            client,
            fixture_thread_id,
            run_root,
            requested_model,
            service_tier,
        )
        started_thread = start_result.get("thread")
        model = start_result.get("model") if isinstance(start_result, dict) else None
        sandbox = start_result.get("sandbox") if isinstance(start_result, dict) else None
        if not isinstance(model, str) or not model:
            raise RuntimeError("real thread/start did not return an effective model")
        if not isinstance(sandbox, dict):
            raise RuntimeError("real thread/start did not return an effective sandbox")
        thread_source = (
            started_thread.get("threadSource")
            if isinstance(started_thread, dict)
            else None
        )
        if not isinstance(thread_source, str) or not thread_source:
            raise RuntimeError("real thread/start did not return a Thread source")
        owned_thread_ids.add(source_thread_id)
        client.request(
            "thread/goal/set",
            {
                "threadId": source_thread_id,
                "objective": "Verify native Thread rotation readback",
                "status": "paused",
                "tokenBudget": 2_000,
            },
        )

        profile = ThreadRotationProfile(
            cwd=str(run_root),
            developer_instructions="Run one disposable Thread rotation smoke.",
            ephemeral=False,
            thread_source=thread_source,
            model=model,
            reasoning_effort=(
                start_result.get("reasoningEffort")
                if isinstance(start_result, dict)
                else None
            ),
            service_tier=(
                start_result.get("serviceTier")
                if isinstance(start_result, dict)
                else None
            ),
            fork_sandbox="workspace-write",
            response_sandbox=dict(sandbox),
            config={},
        )
        authority = {"thread_id": source_thread_id}

        def expected_profile(thread_id: str, _last_turn_id: str):
            return profile if authority["thread_id"] == thread_id else None

        def rebind(old_thread_id: str, new_thread_id: str) -> None:
            if authority["thread_id"] != old_thread_id:
                raise RuntimeError("smoke authority changed before rebind")
            authority["thread_id"] = new_thread_id

        def publish(payload: object) -> None:
            if isinstance(payload, dict):
                rotation_events.append(dict(payload))

        rotation = ThreadRotationService(
            client,
            ThreadRotationAdapters(
                expected_profile=expected_profile,
                is_idle=lambda thread_id: authority["thread_id"] == thread_id,
                rebind=rebind,
                publish=publish,
            ),
        )
        client.add_notification_observer(rotation.observe)
        source_read = client.request(
            "thread/read", {"threadId": source_thread_id, "includeTurns": True}
        )
        source_native = (
            source_read.get("thread") if isinstance(source_read, dict) else None
        )
        source_turns = (
            source_native.get("turns") if isinstance(source_native, dict) else None
        )
        source_readback = {
            "id": source_native.get("id") if isinstance(source_native, dict) else None,
            "threadSource": (
                source_native.get("threadSource")
                if isinstance(source_native, dict)
                else None
            ),
            "forkedFromId": (
                source_native.get("forkedFromId")
                if isinstance(source_native, dict)
                else None
            ),
            "turns": [
                {
                    "id": turn.get("id"),
                    "status": turn.get("status"),
                    "items_is_list": isinstance(turn.get("items"), list),
                    "itemsView": turn.get("itemsView"),
                }
                for turn in (source_turns if isinstance(source_turns, list) else [])
                if isinstance(turn, dict)
            ],
        }
        for turn_id, item_id in (
            ("synthetic-compaction-turn-1", "synthetic-compaction-item-1"),
            ("synthetic-compaction-turn-2", "synthetic-compaction-item-2"),
            (terminal_turn_id, "synthetic-compaction-item-3"),
        ):
            rotation.observe(
                "item/completed",
                {
                    "threadId": source_thread_id,
                    "turnId": turn_id,
                    "item": {"id": item_id, "type": "contextCompaction"},
                },
            )

        rotation.observe(
            "thread/status/changed",
            {
                "threadId": source_thread_id,
                "status": {"type": "idle"},
            },
        )
        rotation_deadline = time.monotonic() + 60.0
        while (
            not any(
                event.get("type") in {"thread_rotated", "thread_rotation_failed"}
                for event in rotation_events
            )
            and time.monotonic() < rotation_deadline
        ):
            time.sleep(0.05)
        replacement_thread_id = authority["thread_id"]
        if replacement_thread_id == source_thread_id:
            raise RuntimeError(
                "ThreadRotationService failed: "
                f"profile_source={thread_source!r}, "
                f"readback={source_readback!r}, events={rotation_events!r}"
            )
        owned_thread_ids.add(replacement_thread_id)
        rotated = next(
            (
                event
                for event in rotation_events
                if event.get("type") == "thread_rotated"
            ),
            None,
        )
        if (
            rotated is None
            or rotated.get("oldThreadId") != source_thread_id
            or rotated.get("newThreadId") != replacement_thread_id
            or rotated.get("old_thread_orphaned") is not False
        ):
            raise RuntimeError("ThreadRotationService did not publish one clean result")
        owned_thread_ids.discard(source_thread_id)
        goal_result = client.request(
            "thread/goal/get", {"threadId": replacement_thread_id}
        )
        goal = goal_result.get("goal") if isinstance(goal_result, dict) else None
        if not isinstance(goal, dict) or (
            goal.get("objective"), goal.get("status"), goal.get("tokenBudget")
        ) != ("Verify native Thread rotation readback", "paused", 2_000):
            raise RuntimeError("replacement Thread Goal did not survive rotation")

        client.request("thread/delete", {"threadId": replacement_thread_id})
        owned_thread_ids.discard(replacement_thread_id)
    finally:
        if rotation is not None:
            rotation.close()
        if client.is_running:
            for thread_id in tuple(owned_thread_ids):
                try:
                    client.request("thread/delete", {"threadId": thread_id})
                    owned_thread_ids.discard(thread_id)
                except Exception as exc:
                    cleanup_errors.append(f"{thread_id}: {type(exc).__name__}: {exc}")
        process_id = client.process_id
        client.close()

    initialize = initialize_result if isinstance(initialize_result, dict) else {}
    output = {
        "ok": not cleanup_errors and not client.is_running,
        "real_app_server": True,
        "thread_rotation_service_path_verified": True,
        "automatic_compaction_verified": False,
        "automatic_compaction_note": (
            "The service received three synthetic contextCompaction notifications; "
            "no real automatic compaction was produced or claimed."
        ),
        "embedded_houdini_verified": False,
        "executable": str(codex_exe),
        "executable_sha256": _sha256(codex_exe),
        "version": version,
        "process_id": process_id,
        "initialize": {
            "userAgent": initialize.get("userAgent"),
            "platformFamily": initialize.get("platformFamily"),
        },
        "thread": {
            "source_id": source_thread_id,
            "replacement_id": replacement_thread_id,
            "fixture_id": fixture_thread_id,
            "terminal_turn_id": terminal_turn_id,
            "goal_preserved": True,
            "replacement_deleted": replacement_thread_id not in owned_thread_ids,
        },
        "rotation_events": rotation_events,
        "cleanup_errors": cleanup_errors,
        "process_reaped": not client.is_running,
        "runtime_root": str(run_root),
        "methods_exercised": [
            "thread/read fixture",
            "thread/fork fixture",
            "thread/goal/set paused",
            "ThreadRotationService.observe",
            "thread/status/changed idle",
            "thread/read(includeTurns=true)",
            "thread/goal/get",
            "thread/fork",
            "thread/goal/set",
            "atomic local rebind",
            "thread/delete",
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
