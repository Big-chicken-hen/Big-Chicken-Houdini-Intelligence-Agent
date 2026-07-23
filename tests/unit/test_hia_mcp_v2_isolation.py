from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "services" / "hia_mcp_v2"
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
UPSTREAM_ROOT = REPOSITORY_ROOT / ".runtime" / "fxhoudinimcp" / "1.3.0" / "source"
sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
from hia_mcp_v2.tools import TOOL_NAMES  # noqa: E402
from hia_mcp_v2.transport import (  # noqa: E402
    ENV_PREFIX,
    EXECUTE_ROUTE,
    RUNTIME_DIRECTORY,
    SERVER_ID,
    TransportConfig,
)


def upstream_tool_names() -> set[str]:
    names: set[str] = set()
    tools_root = UPSTREAM_ROOT / "python" / "fxhoudinimcp" / "tools"
    for path in tools_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr != "tool":
                    continue
                if isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "mcp":
                    names.add(node.name)
    return names


class HiaMcpV2IsolationTests(unittest.TestCase):
    @unittest.skipUnless(
        UPSTREAM_ROOT.is_dir(),
        "optional FXHoudiniMCP audit fixture is not installed",
    )
    def test_captured_upstream_179_tool_names_have_zero_intersection(self) -> None:
        upstream = upstream_tool_names()
        self.assertEqual(179, len(upstream))
        self.assertEqual(set(), set(TOOL_NAMES) & upstream)

    def test_distribution_import_entrypoint_server_and_tool_names_are_independent(self) -> None:
        pyproject = (SERVICE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "hia_mcp_v2"', pyproject)
        self.assertIn('hia_mcp_v2 = "hia_mcp_v2.__main__:main"', pyproject)
        self.assertEqual("hia_mcp_v2", SERVER_ID)
        self.assertTrue(all(name.startswith("hia_") for name in TOOL_NAMES))

    def test_environment_route_and_runtime_directory_are_private_to_v2(self) -> None:
        self.assertEqual("HIA_MCP_V2_", ENV_PREFIX)
        self.assertEqual("/hia-mcp-v2/v1/execute", EXECUTE_ROUTE)
        self.assertEqual(".runtime/hia-mcp-v2", RUNTIME_DIRECTORY)
        config = TransportConfig.from_environment(
            {
                "HIA_MCP_V2_PORT": "45123",
                "HIA_MCP_V2_TOKEN": "A" * 40,
                "FXHOUDINIMCP_PORT": "8100",
                "FXHOUDINIMCP_TOKEN": "B" * 40,
            }
        )
        self.assertEqual(45123, config.port)
        self.assertEqual("A" * 40, config.token)

    def test_production_packages_do_not_import_or_read_upstream_namespaces(self) -> None:
        product_roots = [SERVICE_ROOT / "hia_mcp_v2", RUNTIME_PACKAGE_ROOT / "hia_mcp_runtime"]
        forbidden = (
            "import fxhoudinimcp",
            "from fxhoudinimcp",
            "fxhoudinimcp_server",
            "FXHOUDINIMCP_",
        )
        for root in product_roots:
            for path in root.glob("*.py"):
                source = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    self.assertNotIn(marker, source, f"{marker} leaked into {path}")

    def test_runtime_implements_every_nonlocal_tool_without_a_node_allowlist(self) -> None:
        local_tools = {"hia_search_capabilities"}
        self.assertEqual(set(TOOL_NAMES) - local_tools, set(HoudiniExecutor.TOOL_NAMES))
        source = (RUNTIME_PACKAGE_ROOT / "hia_mcp_runtime" / "executor.py").read_text(encoding="utf-8")
        self.assertNotIn("ALLOWED_TYPES", source)
        self.assertNotIn("NODE_TYPE_ALLOWLIST", source)
        self.assertNotIn("hia_create_node", source)
        self.assertNotIn("hia_set_parameter", source)


if __name__ == "__main__":
    unittest.main()
