from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG_PATH = REPOSITORY_ROOT / ".codex" / "config.toml"
PINNED_CODEX_VERSION = "0.144.3"
SERVER_NAME = "houdini_intelligence"

CODEX_0_144_3_STDIO_SERVER_KEYS = frozenset(
    {
        "command",
        "args",
        "cwd",
        "enabled",
        "required",
        "startup_timeout_sec",
        "tool_timeout_sec",
        "env_vars",
    }
)
EXPECTED_ENV_VARS = [
    "PATH",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "TEMP",
    "TMP",
    "HIA_PROJECT_ROOT",
    "HIA_CACHE_DIR",
    "HIA_EXPECTED_PYTHON_EXE",
    "HOUDINI_HOST",
    "HOUDINI_PORT",
    "FXHOUDINIMCP_TOKEN",
]


def _parse_scalar(value: str) -> object:
    if value.startswith("'") and value.endswith("'"):
        if "'" in value[1:-1]:
            raise ValueError("embedded quote is outside the reviewed literal grammar")
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    if value.startswith("[") and value.endswith("]"):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) for item in parsed
        ):
            raise ValueError("inline arrays may contain strings only")
        return parsed
    raise ValueError(f"unsupported value in closed config grammar: {value!r}")


def _parse_closed_server(source: str) -> dict[str, object]:
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    expected_header = f"[mcp_servers.{SERVER_NAME}]"
    headers = [
        line
        for line in lines
        if re.fullmatch(r"\[[A-Za-z0-9_.-]+\]", line)
    ]
    if not lines or lines[0] != expected_header or headers != [expected_header]:
        raise ValueError("configuration must contain exactly one reviewed server table")

    server: dict[str, object] = {}
    index = 1
    while index < len(lines):
        match = re.fullmatch(r"([a-z_]+)\s*=\s*(.*)", lines[index])
        if match is None:
            raise ValueError(f"unsupported config line: {lines[index]!r}")
        key, value = match.groups()
        if key in server:
            raise ValueError(f"duplicate key: {key}")

        if value == "[":
            items: list[str] = []
            index += 1
            while index < len(lines) and lines[index] != "]":
                item_match = re.fullmatch(r"'([^']*)',", lines[index])
                if item_match is None:
                    raise ValueError(
                        f"unsupported multiline array item: {lines[index]!r}"
                    )
                items.append(item_match.group(1))
                index += 1
            if index >= len(lines) or lines[index] != "]":
                raise ValueError(f"unterminated array for {key}")
            server[key] = items
        else:
            server[key] = _parse_scalar(value)
        index += 1
    return server


def _load_config() -> tuple[str, dict[str, object]]:
    source = CONFIG_PATH.read_text(encoding="utf-8")
    return source, _parse_closed_server(source)


class B2CCodexConfigTests(unittest.TestCase):
    def test_pinned_codex_stdio_server_shape_is_closed(self) -> None:
        self.assertEqual("0.144.3", PINNED_CODEX_VERSION)
        _, server = _load_config()
        self.assertEqual(CODEX_0_144_3_STDIO_SERVER_KEYS, set(server))
        self.assertEqual(
            r".runtime\fxhoudinimcp\1.3.0\venv\Scripts\python.exe",
            server["command"],
        )
        self.assertEqual(["-B", "-m", "fxhoudinimcp"], server["args"])
        self.assertEqual(".", server["cwd"])
        self.assertIs(server["enabled"], True)
        self.assertIs(server["required"], False)
        self.assertEqual(15, server["startup_timeout_sec"])
        self.assertEqual(65, server["tool_timeout_sec"])

    def test_ordinary_project_session_keeps_houdini_mcp_optional(self) -> None:
        _, server = _load_config()
        self.assertIs(server["enabled"], True)
        self.assertIs(server["required"], False)

    def test_environment_is_name_only_and_contains_no_executor_credential(self) -> None:
        source, server = _load_config()
        self.assertEqual(EXPECTED_ENV_VARS, server["env_vars"])
        self.assertNotIn("env", server)
        self.assertNotIn("HIA_SCENE_EXECUTOR_TOKEN", server["env_vars"])

        lowered = source.casefold()
        self.assertNotIn("http://", lowered)
        self.assertNotIn("https://", lowered)
        self.assertNotIn("bearer ", lowered)
        self.assertNotRegex(source, r"(?i)(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}")
        self.assertEqual(1, source.count("FXHOUDINIMCP_TOKEN"))

    def test_upstream_tool_surface_is_not_filtered(self) -> None:
        _, server = _load_config()
        self.assertNotIn("enabled_tools", server)
        self.assertNotIn("disabled_tools", server)

    def test_no_network_or_open_world_server_keys_are_present(self) -> None:
        source, server = _load_config()
        forbidden_keys = {
            "url",
            "http_headers",
            "env_http_headers",
            "bearer_token_env_var",
            "oauth_resource",
            "scopes",
            "approval_mode",
        }
        self.assertTrue(forbidden_keys.isdisjoint(server))
        self.assertNotRegex(source, r"(?im)^\s*(?:url|env|http_headers)\s*=")

    def test_config_contains_one_literal_server_table_and_no_extra_table(self) -> None:
        source, _ = _load_config()
        table_headers = re.findall(r"(?m)^\s*\[([^]]+)]\s*$", source)
        self.assertEqual([f"mcp_servers.{SERVER_NAME}"], table_headers)


if __name__ == "__main__":
    unittest.main()
