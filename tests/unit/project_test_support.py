from __future__ import annotations

import sys


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
