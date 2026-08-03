"""Real app-server smoke for atomic five-role project creation and isolation."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.project_contracts import (  # noqa: E402
    ProjectState,
    Role,
    authoritative_task_identity,
)
from hia_bridge.project_permissions import permission_profile  # noqa: E402
from hia_bridge.project_thread_factory import ProjectThreadFactory  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from smoke_codex_project_threads import _codex_executable, _sha256  # noqa: E402


class RecordingClient:
    """Record only requests acknowledged by the real app-server."""

    def __init__(self, client: CodexStdioClient) -> None:
        self.client = client
        self.accepted_starts: list[dict[str, Any]] = []

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        result = self.client.request(method, dict(params))
        if method == "thread/start":
            thread = result.get("thread") if isinstance(result, dict) else None
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            self.accepted_starts.append(
                {
                    "thread_id": thread_id,
                    "requested_sandbox": params.get("sandbox"),
                    "effective_sandbox": (
                        result.get("sandbox") if isinstance(result, dict) else None
                    ),
                    "requested_approvalPolicy": params.get("approvalPolicy"),
                    "effective_approvalPolicy": (
                        result.get("approvalPolicy")
                        if isinstance(result, dict)
                        else None
                    ),
                    "config": dict(params.get("config", {})),
                    "threadSource": params.get("threadSource"),
                }
            )
        return result


def _write_inert_mcp_config(run_root: Path) -> None:
    """Provide valid empty transports for effective-inventory inspection."""

    config_directory = run_root / ".codex"
    config_directory.mkdir(parents=True, exist_ok=False)
    python_literal = json.dumps(sys.executable)
    exit_literal = json.dumps("raise SystemExit(0)")
    config_directory.joinpath("config.toml").write_text(
        (
            "[mcp_servers.hia_mcp_v2]\n"
            f"command = {python_literal}\n"
            f"args = ['-c', {exit_literal}]\n"
            "enabled = false\nrequired = false\n\n"
            "[mcp_servers.houdini_intelligence]\n"
            f"command = {python_literal}\n"
            f"args = ['-c', {exit_literal}]\n"
            "enabled = false\nrequired = false\n"
        ),
        encoding="utf-8",
    )


def _list_run_threads(client: CodexStdioClient, run_root: Path) -> list[dict[str, Any]]:
    result = client.request(
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
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("thread/list response is malformed")
    return [item for item in data if isinstance(item, dict)]


def main() -> int:
    codex_exe = _codex_executable()
    run_id = uuid.uuid4().hex
    run_root = (
        PROJECT_ROOT / ".runtime" / "smoke" / "codex-project-provisioning" / run_id
    )
    codex_home = run_root / "codex-home"
    temp_directory = run_root / "tmp"
    codex_home.mkdir(parents=True, exist_ok=False)
    temp_directory.mkdir(parents=True, exist_ok=False)
    _write_inert_mcp_config(run_root)

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
        "HOUDINI_HOST",
        "HOUDINI_PORT",
        "FXHOUDINIMCP_TOKEN",
    ):
        environment.pop(name, None)

    version = subprocess.check_output(
        [str(codex_exe), "--version"],
        cwd=run_root,
        env=environment,
        text=True,
        timeout=15,
    ).strip()
    production_policy = ProtocolPolicy.from_project_root(PROJECT_ROOT)
    smoke_policy = replace(
        production_policy,
        client_requests=(
            production_policy.client_requests | {"mcpServerStatus/list"}
        ),
    )
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
        # Schema-pinned read-only status inspection is smoke-only; production
        # retains its narrower frozen allowlist.
        policy=smoke_policy,
        request_timeout=15.0,
    )
    recorder = RecordingClient(client)
    created_ids: list[str] = []
    read_ids: list[str] = []
    cleanup_error: str | None = None
    initialize_result: Any = None
    final_role_ids: dict[str, str] = {}
    permission_records: list[dict[str, Any]] = []
    try:
        client.start()
        initialize_result = client.initialize()
        task_id, task_digest = authoritative_task_identity(
            "real app-server project provisioning smoke"
        )
        project_id = f"smoke-project-{run_id}"
        state = ProjectState(
            project_id=project_id,
            authoritative_task_id=task_id,
            authoritative_task_sha256=task_digest,
        )
        inert_server = PROJECT_ROOT / "scripts" / "smoke_inert_mcp.py"
        transports = {
            server_id: {
                "command": sys.executable,
                "args": ["-B", str(inert_server)],
                "cwd": str(run_root),
                "startup_timeout_sec": 10,
                "tool_timeout_sec": 10,
                "default_tools_approval_mode": "approve",
            }
            for server_id in ("hia_mcp_v2", "houdini_intelligence")
        }
        factory = ProjectThreadFactory(
            recorder,
            run_root,
            "hia_mcp_v2",
            transports,
        )
        state = factory.create_all_roles(state)
        created_ids = [state.roles[role].thread_id for role in Role]
        if set(state.roles) != set(Role) or len(recorder.accepted_starts) != 5:
            raise RuntimeError("atomic project creation did not create exactly five roles")

        accepted_by_source = {
            item["threadSource"]: item for item in recorder.accepted_starts
        }
        for role in Role:
            binding = state.roles[role]
            expected = permission_profile(role, "hia_mcp_v2", transports)
            source = f"hia-project/{project_id}/{role.value}"
            accepted = accepted_by_source.get(source)
            if accepted is None or accepted.get("thread_id") != binding.thread_id:
                raise RuntimeError(f"missing acknowledged thread/start for {role.value}")
            matched = (
                accepted.get("requested_sandbox") == expected.sandbox
                and accepted.get("requested_approvalPolicy")
                == expected.approval_policy
                and accepted.get("config") == dict(expected.config)
            )
            if not matched:
                raise RuntimeError(f"permission profile drift for {role.value}")
            permission_records.append(
                {
                    "role": role.value,
                    "thread_id": binding.thread_id,
                    "threadSource": source,
                    "requested_sandbox": accepted["requested_sandbox"],
                    "effective_sandbox": accepted["effective_sandbox"],
                    "requested_approvalPolicy": accepted[
                        "requested_approvalPolicy"
                    ],
                    "effective_approvalPolicy": accepted[
                        "effective_approvalPolicy"
                    ],
                    "config": accepted["config"],
                    "production_profile_match": True,
                }
            )
            read = client.request(
                "thread/read",
                {"threadId": binding.thread_id, "includeTurns": False},
            )
            thread = read.get("thread") if isinstance(read, dict) else None
            if not isinstance(thread, dict) or thread.get("id") != binding.thread_id:
                raise RuntimeError(f"thread/read identity mismatch for {role.value}")
            status_result = client.request(
                "mcpServerStatus/list",
                {
                    "threadId": binding.thread_id,
                    "detail": "toolsAndAuthOnly",
                },
            )
            status_data = (
                status_result.get("data")
                if isinstance(status_result, dict)
                else None
            )
            if not isinstance(status_data, list):
                raise RuntimeError(f"MCP status is not observable for {role.value}")
            observed_servers = {
                item.get("name"): {
                    "tools": sorted((item.get("tools") or {}).keys()),
                    "initialized": isinstance(item.get("serverInfo"), dict),
                }
                for item in status_data
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            expected_servers = {
                "hia_mcp_v2": {
                    "tools": [],
                    "initialized": role is Role.EXECUTION,
                },
                "houdini_intelligence": {
                    "tools": [],
                    "initialized": False,
                },
            }
            if observed_servers != expected_servers:
                raise RuntimeError(
                    f"effective MCP inventory drift for {role.value}: "
                    f"{observed_servers!r}"
                )
            permission_records[-1]["effective_mcp_inventory"] = observed_servers
            read_ids.append(binding.thread_id)
            final_role_ids[role.value] = binding.thread_id
    finally:
        if client.is_running:
            try:
                for thread_id in reversed(tuple(dict.fromkeys(created_ids))):
                    client.request("thread/delete", {"threadId": thread_id})
                residual_by_id = {
                    item.get("id"): item
                    for item in _list_run_threads(client, run_root)
                    if isinstance(item.get("id"), str)
                    and isinstance(item.get("cwd"), str)
                    and Path(item["cwd"]).resolve() == run_root.resolve()
                }
                for thread_id in created_ids:
                    residual_by_id.pop(thread_id, None)
                for thread_id in tuple(residual_by_id):
                    client.request("thread/delete", {"threadId": thread_id})
                if _list_run_threads(client, run_root):
                    raise RuntimeError("owned smoke Threads remain after cleanup")
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
        process_id = client.process_id
        client.close()

    initialize = initialize_result if isinstance(initialize_result, dict) else {}
    output = {
        "ok": (
            cleanup_error is None
            and not client.is_running
            and len(final_role_ids) == 5
            and set(read_ids) == set(final_role_ids.values())
        ),
        "real_app_server": True,
        "executable": str(codex_exe),
        "executable_sha256": _sha256(codex_exe),
        "version": version,
        "process_id": process_id,
        "initialize": {
            "userAgent": initialize.get("userAgent"),
            "platformFamily": initialize.get("platformFamily"),
        },
        "atomic_role_creation": {
            "role_thread_ids": final_role_ids,
            "created_count": len(final_role_ids),
        },
        "permission_isolation": permission_records,
        "read_back_thread_ids": read_ids,
        "all_five_deleted": cleanup_error is None,
        "process_reaped": not client.is_running,
        "cleanup_error": cleanup_error,
        "runtime_root": str(run_root),
        "turns_started": 0,
        "houdini_connected": False,
        "mcp_transport": "protocol-compliant empty smoke server",
    }
    (run_root / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
