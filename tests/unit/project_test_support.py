from __future__ import annotations

import sys
from typing import Any, Mapping


def server_transports() -> dict[str, dict[str, object]]:
    """Syntactically complete inert transports for fake-client unit tests."""

    return {
        "hia_mcp_v2": {
            "command": sys.executable,
            "args": ["-c", "raise SystemExit(0)"],
        },
        "houdini_intelligence": {
            "command": sys.executable,
            "args": ["-c", "raise SystemExit(0)"],
        },
    }


def observable_thread_response(
    params: Mapping[str, Any], thread_id: str
) -> dict[str, Any]:
    """Return the observable shape emitted by pinned Codex 0.144.3."""

    requested = params.get("sandbox")
    sandbox_type = "workspaceWrite" if requested == "workspace-write" else "readOnly"
    return {
        "thread": {
            "id": thread_id,
            "threadSource": params.get("threadSource"),
        },
        "sandbox": {"type": sandbox_type, "networkAccess": False},
        "approvalPolicy": params.get("approvalPolicy"),
        "model": params.get("model") or "gpt-test",
        "reasoningEffort": "high",
        "serviceTier": params.get("serviceTier"),
    }
