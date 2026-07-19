from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
BRIDGE_ROOT = REPOSITORY_ROOT / "services" / "bridge"
HIA_SERVICE_ROOT = REPOSITORY_ROOT / "services" / "hia_mcp_v2"
HOUDINI_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
UPSTREAM_ROOT = (
    REPOSITORY_ROOT
    / ".runtime"
    / "fxhoudinimcp"
    / "1.3.0"
    / "source"
    / "python"
    / "fxhoudinimcp"
    / "tools"
)

for path in (
    REPOSITORY_ROOT / "src",
    BRIDGE_ROOT,
    HIA_SERVICE_ROOT,
    HOUDINI_LIB_ROOT,
):
    sys.path.insert(0, str(path))

from hia_bridge import main as bridge_main  # noqa: E402
from hia_mcp_runtime.executor import HoudiniExecutor  # noqa: E402
from hia_mcp_runtime.http_server import (  # noqa: E402
    EXECUTE_ROUTE as RUNTIME_EXECUTE_ROUTE,
    HEALTH_ROUTE as RUNTIME_HEALTH_ROUTE,
    WIRE_PROTOCOL as RUNTIME_PROTOCOL,
    start_runtime_server,
)
from hia_mcp_v2.adapter import (  # noqa: E402
    HiaMcpAdapter,
    MCP_PROTOCOL_VERSION,
)
from hia_mcp_v2.tools import TOOL_NAMES  # noqa: E402
from hia_mcp_v2.transport import (  # noqa: E402
    CancellationToken,
    LoopbackTransport,
    TransportConfig,
)


def _powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _powershell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _config_overrides(command: list[str]) -> list[str]:
    return [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-c"
    ]


def _upstream_tool_names() -> set[str]:
    names: set[str] = set()
    for path in UPSTREAM_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if (
                    isinstance(function, ast.Attribute)
                    and function.attr == "tool"
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "mcp"
                ):
                    names.add(node.name)
    return names


class _NoopTransport:
    def call(self, *args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("tools/list must not dispatch to Houdini")

    def cancel(self, request_id: int | str) -> None:
        return None

    def close(self) -> None:
        return None


def _hia_stdio_tool_names() -> set[str]:
    adapter = HiaMcpAdapter(_NoopTransport())
    try:
        initialized = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "production-integration-test", "version": "1"},
                },
            }
        )
        assert initialized is not None
        assert initialized["result"]["serverInfo"]["name"] == "hia_mcp_v2"
        response = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert response is not None
        return {tool["name"] for tool in response["result"]["tools"]}
    finally:
        adapter.shutdown()


class LauncherBackendIntegrationTests(unittest.TestCase):
    def test_settings_default_persistence_and_choices_are_project_local(self) -> None:
        powershell = _powershell_executable()
        if powershell is None:
            self.skipTest("PowerShell is unavailable")

        runtime_root = REPOSITORY_ROOT / ".runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        module = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
        with tempfile.TemporaryDirectory(
            prefix="hia-mcp-v2-launcher-test-",
            dir=runtime_root,
        ) as temporary_root:
            temporary_path = Path(temporary_root)
            fake_houdini = temporary_path / "Houdini 21" / "bin" / "houdini.exe"
            fake_python = temporary_path / "Bridge Python" / "python.exe"
            script = "\n".join(
                (
                    f"Import-Module -Force -DisableNameChecking {_powershell_quote(module)}",
                    f"$root = {_powershell_quote(temporary_path)}",
                    "$before = Read-HiaLauncherSettings -ProjectRoot $root",
                    "$choices = @(Get-HiaMcpBackendChoices)",
                    "$path = Write-HiaLauncherSettings -ProjectRoot $root "
                    f"-HoudiniExe {_powershell_quote(fake_houdini)} "
                    f"-BridgePython {_powershell_quote(fake_python)} "
                    "-McpBackend fxhoudini",
                    "$after = Read-HiaLauncherSettings -ProjectRoot $root",
                    "[ordered]@{before=$before; choices=$choices; path=$path; after=$after} "
                    "| ConvertTo-Json -Depth 8 -Compress",
                )
            )
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            result = json.loads(lines[-1])

            self.assertEqual("hia_v2", result["before"]["mcp_backend"])
            self.assertEqual(
                ["hia_v2", "fxhoudini"],
                [choice["id"] for choice in result["choices"]],
            )
            self.assertIn("推荐", result["choices"][0]["display"])
            self.assertIn("兼容回退", result["choices"][1]["display"])
            self.assertEqual("fxhoudini", result["after"]["mcp_backend"])

            expected_settings = temporary_path / ".runtime" / "launcher" / "settings.json"
            self.assertEqual(expected_settings.resolve(), Path(result["path"]).resolve())
            persisted = json.loads(expected_settings.read_text(encoding="utf-8-sig"))
            self.assertEqual(
                {"houdini_exe", "bridge_python", "render_output_dir", "mcp_backend"},
                set(persisted),
            )
            self.assertEqual("fxhoudini", persisted["mcp_backend"])
            self.assertEqual("", persisted["render_output_dir"])

    def test_wpf_has_high_contrast_picker_templates_and_passes_one_backend(self) -> None:
        xaml_path = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.xaml"
        root = ET.parse(xaml_path).getroot()
        name_attribute = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
        key_attribute = "{http://schemas.microsoft.com/winfx/2006/xaml}Key"
        pickers = [
            element
            for element in root.iter()
            if element.tag.endswith("ComboBox")
            and element.attrib.get(name_attribute) == "McpBackendComboBox"
        ]
        self.assertEqual(1, len(pickers))
        self.assertEqual(
            "{StaticResource BackendPickerItemTemplate}",
            pickers[0].attrib.get("ItemTemplate"),
        )
        self.assertEqual(
            "{StaticResource DarkPickerComboBoxStyle}",
            pickers[0].attrib.get("Style"),
        )
        self.assertNotIn("DisplayMemberPath", pickers[0].attrib)
        self.assertEqual("False", pickers[0].attrib.get("IsEditable"))

        houdini_picker = next(
            element
            for element in root.iter()
            if element.tag.endswith("ComboBox")
            and element.attrib.get(name_attribute) == "HoudiniComboBox"
        )
        self.assertEqual(
            "{StaticResource HoudiniPickerItemTemplate}",
            houdini_picker.attrib.get("ItemTemplate"),
        )
        self.assertNotIn("DisplayMemberPath", houdini_picker.attrib)

        resources = {
            element.attrib[key_attribute]: ET.tostring(element, encoding="unicode")
            for element in root.iter()
            if key_attribute in element.attrib
        }
        houdini_template = resources["HoudiniPickerItemTemplate"]
        for expected in (
            "Binding version",
            "Binding path",
            "ToolTip",
            "TextTrimming=\"CharacterEllipsis\"",
            "PickerSecondaryTextBrush",
        ):
            self.assertIn(expected, houdini_template)

        picker_style = (
            resources["DarkPickerComboBoxStyle"]
            + resources["DarkPickerComboBoxItemStyle"]
        )
        for expected in (
            "PickerBackgroundBrush",
            "PickerTextBrush",
            "PickerHoverBrush",
            "PickerSelectedBrush",
            "PickerDisabledTextBrush",
        ):
            self.assertIn(expected, picker_style)

        wpf_source = (
            REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Wpf.ps1"
        ).read_text(encoding="utf-8-sig")
        entry_source = (REPOSITORY_ROOT / "scripts" / "hia-launcher.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("Get-HiaMcpBackendChoices", wpf_source)
        self.assertIn("$selectedBackend = Get-ComboBackend", wpf_source)
        self.assertIn("-McpBackend $selectedBackend", wpf_source)
        self.assertIn("'-McpBackend', $SelectedBackend", entry_source)

    def test_lifecycle_branches_have_disjoint_runtime_namespaces(self) -> None:
        source = (REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1").read_text(
            encoding="utf-8-sig"
        )
        branch_start = source.index("if ($McpBackend -eq 'hia_v2') {")
        branch_end = source.index("$bridgeProcessPythonPath", branch_start)
        hia_block, fallback_block = source[branch_start:branch_end].split("} else {", 1)

        self.assertIn("HIA_MCP_V2_HOST", hia_block)
        self.assertIn("HIA_MCP_V2_TOKEN", hia_block)
        self.assertIn("/hia-mcp-v2/v1/execute", hia_block)
        self.assertIn(".runtime\\hia-mcp-v2", hia_block)
        self.assertIn("$bridgeBackendPythonPaths = @($hiaMcpServicePath)", hia_block)
        self.assertNotIn("FXHOUDINIMCP_", hia_block)
        self.assertNotIn(".runtime\\fxhoudinimcp", hia_block)
        self.assertNotIn("fxMcpSourcePath", hia_block)

        self.assertIn("FXHOUDINIMCP_TOKEN", fallback_block)
        self.assertIn(".runtime\\fxhoudinimcp\\1.3.0", fallback_block)
        self.assertIn(
            "$houdiniBackendPythonPaths = @($fxHoudiniServerPath)",
            fallback_block,
        )
        self.assertNotIn("HIA_MCP_V2_", fallback_block)
        self.assertNotIn(".runtime\\hia-mcp-v2", fallback_block)
        self.assertNotIn("hiaMcpServicePath", fallback_block)

        self.assertIn("[ValidateSet('hia_v2', 'fxhoudini')]", source)
        self.assertIn("[string]$McpBackend = 'hia_v2'", source)
        self.assertIn("'--mcp-backend'", source)


class BridgeBackendIntegrationTests(unittest.TestCase):
    def test_hia_strict_config_registers_only_owned_required_server(self) -> None:
        bridge_python = str(Path(sys.executable).resolve())
        self.assertTrue(Path(bridge_python).is_absolute())
        command = bridge_main._codex_app_server_command(
            REPOSITORY_ROOT / "codex.exe",
            bridge_python,
            backend="hia_v2",
            project_root=REPOSITORY_ROOT,
        )
        overrides = _config_overrides(command)
        server = "mcp_servers.hia_mcp_v2"

        self.assertEqual(1, command.count("--strict-config"))
        self.assertIn("mcp_servers.houdini_intelligence.enabled=false", overrides)
        self.assertIn(
            f"{server}.command=" + bridge_main._toml_basic_string(bridge_python),
            overrides,
        )
        self.assertIn(f'{server}.args=["-B", "-m", "hia_mcp_v2"]', overrides)
        self.assertIn(f"{server}.enabled=true", overrides)
        self.assertIn(f"{server}.required=true", overrides)
        self.assertIn(f'{server}.default_tools_approval_mode="approve"', overrides)

        env_override = next(value for value in overrides if value.startswith(f"{server}.env_vars="))
        env_names = json.loads(env_override.split("=", 1)[1])
        self.assertIn("HIA_MCP_V2_TOKEN", env_names)
        self.assertIn("HIA_MCP_V2_ROUTE", env_names)
        self.assertFalse(any(name.startswith("FXHOUDINIMCP_") for name in env_names))
        self.assertFalse(any("fxhoudinimcp" in value.casefold() for value in overrides))

    def test_fallback_strict_config_registers_no_hia_server(self) -> None:
        fx_python = (
            REPOSITORY_ROOT
            / ".runtime"
            / "fxhoudinimcp"
            / "1.3.0"
            / "venv"
            / "Scripts"
            / "python.exe"
        ).resolve()
        self.assertTrue(fx_python.is_absolute())
        command = bridge_main._codex_app_server_command(
            REPOSITORY_ROOT / "codex.exe",
            str(fx_python),
            backend="fxhoudini",
            project_root=REPOSITORY_ROOT,
        )
        overrides = _config_overrides(command)
        self.assertEqual(1, command.count("--strict-config"))
        self.assertIn(
            "mcp_servers.houdini_intelligence.command="
            + bridge_main._toml_basic_string(str(fx_python)),
            overrides,
        )
        self.assertIn("mcp_servers.houdini_intelligence.required=true", overrides)
        self.assertIn(
            'mcp_servers.houdini_intelligence.default_tools_approval_mode="approve"',
            overrides,
        )
        self.assertFalse(any("hia_mcp_v2" in value for value in overrides))

    def test_hia_environment_contract_ignores_upstream_values(self) -> None:
        token = "v2_" + "x" * 40
        expected_runtime = REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2"
        environment = {
            "HIA_MCP_V2_HOST": "127.0.0.1",
            "HIA_MCP_V2_PORT": "45123",
            "HIA_MCP_V2_TOKEN": token,
            "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
            "HIA_MCP_V2_RUNTIME_DIR": str(expected_runtime),
            "FXHOUDINIMCP_PORT": "8999",
            "FXHOUDINIMCP_TOKEN": "upstream_" + "z" * 40,
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            result = bridge_main._required_hia_mcp_v2_environment(REPOSITORY_ROOT)

        self.assertEqual(
            {
                "HIA_MCP_V2_HOST",
                "HIA_MCP_V2_PORT",
                "HIA_MCP_V2_TOKEN",
                "HIA_MCP_V2_ROUTE",
                "HIA_MCP_V2_RUNTIME_DIR",
            },
            set(result),
        )
        self.assertEqual("45123", result["HIA_MCP_V2_PORT"])
        self.assertEqual(token, result["HIA_MCP_V2_TOKEN"])

    @unittest.skipUnless(
        UPSTREAM_ROOT.is_dir(),
        "optional FXHoudiniMCP audit fixture is not installed",
    )
    def test_selected_tools_lists_are_mutually_exclusive(self) -> None:
        hia_tools = _hia_stdio_tool_names()
        upstream_tools = _upstream_tool_names()

        self.assertEqual(set(TOOL_NAMES), hia_tools)
        self.assertTrue(hia_tools)
        self.assertTrue(all(name.startswith("hia_") for name in hia_tools))
        self.assertEqual(179, len(upstream_tools))
        self.assertEqual(set(), hia_tools & upstream_tools)
        self.assertIn("create_node", upstream_tools)
        self.assertIn("set_parameter", upstream_tools)
        self.assertNotIn("create_node", hia_tools)
        self.assertNotIn("set_parameter", hia_tools)
        self.assertFalse(any(name.startswith("hia_") for name in upstream_tools))

    def test_ordinary_project_config_keeps_optional_fallback_only(self) -> None:
        source = (REPOSITORY_ROOT / ".codex" / "config.toml").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("[mcp_servers.houdini_intelligence]", source)
        self.assertIn("required = false", source)
        self.assertIn("'-m', 'fxhoudinimcp'", source)
        self.assertNotIn("[mcp_servers.hia_mcp_v2]", source)


class HoudiniRuntimeIntegrationTests(unittest.TestCase):
    def test_uiready_starts_exactly_the_selected_backend(self) -> None:
        for python_version in ("python3.10libs", "python3.11libs"):
            path = REPOSITORY_ROOT / "houdini_package" / python_version / "uiready.py"
            with self.subTest(python_version=python_version, backend="hia_v2"):
                hia_start = mock.Mock()
                fx_start = mock.Mock()
                fake_session = SimpleNamespace(
                    host="127.0.0.1",
                    port=45124,
                    route="/hia-mcp-v2/v1/execute",
                    thread=SimpleNamespace(is_alive=lambda: True),
                    stop=mock.Mock(),
                )
                hia_start.return_value = fake_session
                hia_module = types.ModuleType("hia_mcp_runtime")
                hia_module.start_runtime_server = hia_start
                fx_module = types.ModuleType("fxhoudinimcp_server")
                fx_module.startup = SimpleNamespace(ensure_running=fx_start)
                environment = {
                    "HIA_MCP_BACKEND": "hia_v2",
                    "HIA_MCP_V2_AUTOSTART": "1",
                    "HIA_PROJECT_ROOT": str(REPOSITORY_ROOT),
                    "HIA_MCP_V2_RUNTIME_DIR": str(
                        REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2"
                    ),
                    "HIA_MCP_V2_HOST": "127.0.0.1",
                    "HIA_MCP_V2_PORT": "45124",
                    "HIA_MCP_V2_TOKEN": "uiready_" + "a" * 40,
                    "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
                    "FXHOUDINIMCP_AUTOSTART": "1",
                }
                with mock.patch.dict(os.environ, environment, clear=True), mock.patch.dict(
                    sys.modules,
                    {
                        "hia_mcp_runtime": hia_module,
                        "fxhoudinimcp_server": fx_module,
                    },
                    clear=False,
                ), contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(path), run_name=f"__uiready_hia_{python_version}__")
                hia_start.assert_called_once_with(
                    project_root=REPOSITORY_ROOT.resolve(),
                    token="uiready_" + "a" * 40,
                    port=45124,
                )
                fx_start.assert_not_called()

            with self.subTest(python_version=python_version, backend="fxhoudini"):
                hia_start = mock.Mock()
                fx_start = mock.Mock()
                hia_module = types.ModuleType("hia_mcp_runtime")
                hia_module.start_runtime_server = hia_start
                fx_module = types.ModuleType("fxhoudinimcp_server")
                fx_module.startup = SimpleNamespace(ensure_running=fx_start)
                with mock.patch.dict(
                    os.environ,
                    {
                        "HIA_MCP_BACKEND": "fxhoudini",
                        "FXHOUDINIMCP_AUTOSTART": "1",
                        "HIA_MCP_V2_AUTOSTART": "1",
                    },
                    clear=True,
                ), mock.patch.dict(
                    sys.modules,
                    {
                        "hia_mcp_runtime": hia_module,
                        "fxhoudinimcp_server": fx_module,
                    },
                    clear=False,
                ), contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(path), run_name=f"__uiready_fx_{python_version}__")
                fx_start.assert_called_once_with()
                hia_start.assert_not_called()

    def test_runtime_health_and_tool_call_use_fake_ui_main_thread_dispatch(self) -> None:
        class FakeHipFile:
            def path(self) -> str:
                return str(REPOSITORY_ROOT / "integration-test.hip")

            def hasUnsavedChanges(self) -> bool:
                return False

        class FakeHou:
            hipFile = FakeHipFile()

            def applicationVersionString(self) -> str:
                return "21.0.440"

            def frame(self) -> float:
                return 24.0

            def fps(self) -> float:
                return 24.0

            def frameRange(self) -> tuple[float, float]:
                return (1.0, 240.0)

            def playbarRange(self) -> tuple[float, float]:
                return (1.0, 120.0)

            def selectedNodes(self) -> tuple[object, ...]:
                return ()

            def nodeTypeCategories(self) -> dict[str, object]:
                return {"Sop": object(), "Lop": object()}

            def isUIAvailable(self) -> bool:
                return True

        dispatch_count = 0

        def fake_ui_dispatch(callback: object) -> object:
            nonlocal dispatch_count
            dispatch_count += 1
            return callback()  # type: ignore[operator]

        executor = HoudiniExecutor(
            hou_module=FakeHou(),
            main_thread_runner=fake_ui_dispatch,
            project_root=REPOSITORY_ROOT,
        )
        token = "runtime_" + "r" * 40
        session = start_runtime_server(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        transport = LoopbackTransport(
            TransportConfig(
                host="127.0.0.1",
                port=session.port,
                token=token,
                route=RUNTIME_EXECUTE_ROUTE,
                timeout_seconds=3,
            )
        )
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{session.port}{RUNTIME_HEALTH_ROUTE}",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.assertEqual(RUNTIME_PROTOCOL, health["protocol"])
            self.assertTrue(health["ok"])
            self.assertEqual("hia_mcp_v2", health["result"]["server_id"])
            self.assertEqual(0, dispatch_count)

            result = transport.call(
                "hia_context",
                {},
                request_id="production-integration",
                cancellation=CancellationToken(),
            )
            self.assertTrue(result["ok"])
            self.assertEqual("21.0.440", result["result"]["houdini_build"])
            self.assertEqual(["Lop", "Sop"], result["result"]["available_contexts"])
            self.assertEqual(1, dispatch_count)
            self.assertEqual("127.0.0.1", session.host)
            self.assertEqual(REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2", session.runtime_directory)
        finally:
            transport.close()
            session.stop()


if __name__ == "__main__":
    unittest.main()
