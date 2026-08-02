"""Protocol-compliant empty MCP server used only by real app-server smoke tests."""

from __future__ import annotations

import json
import sys


def _reply(request: dict) -> dict | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        params = request.get("params")
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "hia-project-smoke-empty", "version": "1"},
            },
        }
    if method == "tools/list":
        result = {"tools": []}
    elif method in {"resources/list", "prompts/list"}:
        result = {method.split("/", 1)[0]: []}
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for raw in sys.stdin.buffer:
        try:
            request = json.loads(raw.decode("utf-8"))
            response = _reply(request) if isinstance(request, dict) else None
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except (UnicodeError, ValueError):
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
