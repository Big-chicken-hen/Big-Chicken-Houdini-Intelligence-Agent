"""Start exactly one selected project-local MCP runtime after Houdini UI init."""

import os
from pathlib import Path


_backend = os.environ.get("HIA_MCP_BACKEND", "")

if _backend == "hia_v2" and os.environ.get("HIA_MCP_V2_AUTOSTART", "0") == "1":
    try:
        from hia_mcp_runtime import start_runtime_server

        _project_root = Path(os.environ["HIA_PROJECT_ROOT"]).resolve()
        _expected_runtime = (_project_root / ".runtime" / "hia-mcp-v2").resolve()
        _configured_runtime = Path(os.environ["HIA_MCP_V2_RUNTIME_DIR"]).resolve()
        if _configured_runtime != _expected_runtime:
            raise RuntimeError("HIA MCP V2 runtime directory does not match the project")
        if os.environ.get("HIA_MCP_V2_HOST") != "127.0.0.1":
            raise RuntimeError("HIA MCP V2 host must be 127.0.0.1")
        if os.environ.get("HIA_MCP_V2_ROUTE") != "/hia-mcp-v2/v1/execute":
            raise RuntimeError("HIA MCP V2 route is invalid")
        _requested_port = int(os.environ["HIA_MCP_V2_PORT"])
        _hia_mcp_v2_session = start_runtime_server(
            project_root=_project_root,
            token=os.environ["HIA_MCP_V2_TOKEN"],
            port=_requested_port,
        )
        if (
            _hia_mcp_v2_session.host != "127.0.0.1"
            or _hia_mcp_v2_session.port != _requested_port
            or _hia_mcp_v2_session.route != "/hia-mcp-v2/v1/execute"
            or not _hia_mcp_v2_session.thread.is_alive()
        ):
            _hia_mcp_v2_session.stop()
            raise RuntimeError("HIA MCP V2 runtime did not become ready")
        print(f"[HIA] HIA MCP V2 ready on 127.0.0.1:{_requested_port}")
    except Exception as exc:
        print(f"[HIA] HIA MCP V2 unavailable: {exc}")
elif _backend == "fxhoudini" and os.environ.get("FXHOUDINIMCP_AUTOSTART", "0") == "1":
    try:
        from fxhoudinimcp_server import startup

        startup.ensure_running()
    except Exception as exc:
        print(f"[HIA] FXHoudiniMCP fallback unavailable: {exc}")
