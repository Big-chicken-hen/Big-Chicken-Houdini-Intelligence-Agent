"""Finite real smoke test: initialize, initialized, account/read, then stop."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402


def main() -> int:
    codex_exe = PROJECT_ROOT / ".runtime/toolchains/codex/0.144.3/codex.exe"
    codex_home = PROJECT_ROOT / ".runtime/codex-home"
    temp_directory = codex_home / "tmp"
    codex_home.mkdir(parents=True, exist_ok=True)
    temp_directory.mkdir(parents=True, exist_ok=True)

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
    policy = ProtocolPolicy.from_project_root(PROJECT_ROOT)
    client = CodexStdioClient(
        [str(codex_exe), "app-server"],
        cwd=PROJECT_ROOT,
        environment=environment,
        policy=policy,
        request_timeout=45.0,
    )
    initialize_result = None
    account_result = None
    try:
        client.start()
        initialize_result = client.initialize()
        account_result = client.request("account/read", {"refreshToken": False})
    finally:
        client.close()

    account = account_result.get("account") if isinstance(account_result, dict) else None
    output = {
        "ok": True,
        "initialize": {
            "userAgent": initialize_result.get("userAgent")
            if isinstance(initialize_result, dict)
            else None,
            "platformFamily": initialize_result.get("platformFamily")
            if isinstance(initialize_result, dict)
            else None,
        },
        "account": {
            "requiresOpenaiAuth": account_result.get("requiresOpenaiAuth")
            if isinstance(account_result, dict)
            else None,
            "present": isinstance(account, dict),
            "type": account.get("type") if isinstance(account, dict) else None,
        },
        "process_reaped": not client.is_running,
        "methods_exercised": ["initialize", "initialized", "account/read"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["process_reaped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
