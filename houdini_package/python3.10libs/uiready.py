"""Start the project-local authenticated FXHoudini gateway after Houdini UI init."""

import os


if os.environ.get("FXHOUDINIMCP_AUTOSTART", "1") == "1":
    try:
        from fxhoudinimcp_server import startup

        startup.ensure_running()
    except Exception as exc:
        print(f"[HIA] Real-time Houdini MCP unavailable: {exc}")
