from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import types
import unittest
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
MODULE_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
LAUNCHER_PATH = REPOSITORY_ROOT / "scripts" / "hia-launcher.ps1"
WPF_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Wpf.ps1"
XAML_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.xaml"
LIFECYCLE_PATH = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
EXE_PROJECT_ROOT = REPOSITORY_ROOT / "launcher" / "HoudiniIntelligenceLauncher"
EXE_PROJECT_PATH = EXE_PROJECT_ROOT / "HoudiniIntelligenceLauncher.csproj"
EXE_APP_PATH = EXE_PROJECT_ROOT / "App.xaml.cs"
EXE_ROOT_LOCATOR_PATH = EXE_PROJECT_ROOT / "ProjectRootLocator.cs"
EXE_BUILD_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "build-launcher.ps1"
UI_READY_PATHS = (
    REPOSITORY_ROOT / "houdini_package" / "python3.10libs" / "uiready.py",
    REPOSITORY_ROOT / "houdini_package" / "python3.11libs" / "uiready.py",
)


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class LauncherPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPOSITORY_ROOT
            / ".runtime"
            / "launcher-tests"
            / uuid.uuid4().hex
        )
        self.sandbox.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox)

    def run_powershell(self, body: str, *, timeout: int = 30) -> str:
        prefix = f"""
$ErrorActionPreference = 'Stop'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Import-Module -Force {_ps_literal(MODULE_PATH)}
"""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                prefix + body,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return completed.stdout.strip()

    def make_houdini_install(self, name: str, *, hython: bool = True) -> Path:
        bin_directory = self.sandbox / "Side Effects Software" / name / "bin"
        bin_directory.mkdir(parents=True, exist_ok=True)
        houdini = bin_directory / "houdini.exe"
        houdini.write_bytes(b"fake-houdini")
        if hython:
            (bin_directory / "hython.exe").write_bytes(b"fake-hython")
        return houdini

    def discover(self, common_root: Path) -> list[dict[str, object]]:
        output = self.run_powershell(
            f"""
$items = @(Get-HiaHoudiniCandidates `
    -CommonInstallRoots @({_ps_literal(common_root)}) `
    -SkipEnvironment -SkipPath -SkipRegistry)
ConvertTo-Json -InputObject $items -Depth 6 -Compress
"""
        )
        return json.loads(output)

    def test_discovers_single_houdini_21_install(self) -> None:
        expected = self.make_houdini_install("Houdini 21.0.440")
        candidates = self.discover(expected.parents[2])
        self.assertEqual(1, len(candidates))
        self.assertEqual("21.0.440", candidates[0]["version"])
        self.assertEqual(str(expected), candidates[0]["path"])

    def test_discovers_multiple_houdini_21_22_and_future_versions(self) -> None:
        root = self.sandbox / "Side Effects Software"
        for version in ("Houdini 21.0.440", "Houdini 22.0.100", "Houdini 23.5.7"):
            self.make_houdini_install(version)
        candidates = self.discover(root)
        self.assertEqual(
            {"21.0.440", "22.0.100", "23.5.7"},
            {item["version"] for item in candidates},
        )
        self.assertTrue(all(Path(item["path"]).is_absolute() for item in candidates))

    def test_explicit_houdini_path_does_not_silently_select_another_version(self) -> None:
        selected = self.make_houdini_install("Houdini 22.0.100")
        self.make_houdini_install("Houdini 21.0.440")
        output = self.run_powershell(
            f"""
$items = @(Get-HiaHoudiniCandidates -ExplicitPath {_ps_literal(selected)})
ConvertTo-Json -InputObject $items -Depth 6 -Compress
"""
        )
        candidates = json.loads(output)
        self.assertEqual([str(selected)], [item["path"] for item in candidates])
        self.assertIn("explicit", candidates[0]["sources"])

    def probe_consistency(
        self,
        houdini: Path,
        houdini_build: str,
        hython_build: str,
        reported_executable: Path,
    ) -> dict[str, str]:
        payload = {
            "build": hython_build,
            "python": "3.11",
            "executable": str(reported_executable),
            "hou_import": True,
        }
        marker_output = "__HIA_LAUNCHER_PROBE__" + json.dumps(payload)
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput {_ps_literal('Houdini ' + houdini_build)} `
    -HythonOutput {_ps_literal(marker_output)})
$levels = @{{}}
foreach ($check in $checks) {{ $levels[$check.id] = $check.level }}
ConvertTo-Json -InputObject $levels -Compress
"""
        )
        return json.loads(output)

    def test_missing_houdini_and_hython_are_blocking(self) -> None:
        missing = self.sandbox / "missing" / "bin" / "houdini.exe"
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency -HoudiniExe {_ps_literal(missing)} -HoudiniOutput '' -HythonOutput '')
$checks | ConvertTo-Json -Depth 5 -Compress
"""
        )
        checks = json.loads(output)
        self.assertEqual(
            {"houdini.executable", "houdini.hython"},
            {item["id"] for item in checks if item["level"] == "red"},
        )

    def test_missing_sibling_hython_is_blocking(self) -> None:
        houdini = self.make_houdini_install("Houdini 21.0.440", hython=False)
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency -HoudiniExe {_ps_literal(houdini)} -HoudiniOutput '' -HythonOutput '')
($checks | Where-Object id -eq 'houdini.hython').level
"""
        )
        self.assertEqual("red", output)

    def test_houdini_and_hython_build_mismatch_is_blocking(self) -> None:
        houdini = self.make_houdini_install("Houdini 22.0.100")
        levels = self.probe_consistency(
            houdini,
            "22.0.100",
            "22.0.101",
            houdini.with_name("hython.exe"),
        )
        self.assertEqual("red", levels["houdini.build_match"])

    def test_hython_python_executable_mismatch_is_blocking(self) -> None:
        houdini = self.make_houdini_install("Houdini 22.0.100")
        levels = self.probe_consistency(
            houdini,
            "22.0.100",
            "22.0.100",
            self.sandbox / "other-python.exe",
        )
        self.assertEqual("red", levels["houdini.python_match"])

    def test_missing_project_dependencies_and_unwritable_runtime_are_blocking(self) -> None:
        fake_root = self.sandbox / "moved-project"
        fake_root.mkdir()
        output = self.run_powershell(
            f"""
$result = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
    -BridgePython {_ps_literal(fake_root / 'missing' / 'python.exe')} `
    -ProbeOverrides @{{ runtime_writable = $false; loopback = $true }}
$selected = @($result.checks | Where-Object id -in @(
    'project.runtime_writable', 'bridge.python', 'codex.executable', 'hia_mcp_v2.runtime'
))
$selected | ConvertTo-Json -Depth 5 -Compress
"""
        )
        checks = {item["id"]: item["level"] for item in json.loads(output)}
        self.assertEqual(
            {
                "project.runtime_writable": "red",
                "bridge.python": "red",
                "codex.executable": "red",
                "hia_mcp_v2.runtime": "red",
            },
            checks,
        )

    def test_safe_repair_makes_moved_project_configs_relative(self) -> None:
        fake_root = self.sandbox / "another-drive-style-root" / "project"
        (fake_root / ".codex").mkdir(parents=True)
        package_directory = fake_root / "houdini_package" / "packages"
        package_directory.mkdir(parents=True)
        (fake_root / ".codex" / "config.toml").write_text(
            """[mcp_servers.houdini_intelligence]
command = 'Z:\\old-location\\.runtime\\fxhoudinimcp\\1.3.0\\venv\\Scripts\\python.exe'
cwd = 'Z:\\old-location'
""",
            encoding="utf-8",
        )
        (package_directory / "houdini_intelligence.json").write_text(
            json.dumps(
                {
                    "enable": True,
                    "env": [
                        {"HIA_PROJECT_ROOT": "Z:/old-location"},
                        {
                            "PYTHONPATH": {
                                "method": "prepend",
                                "value": "Z:/old-location/src",
                            }
                        },
                    ],
                    "path": "Z:/old-location/houdini_package",
                }
            ),
            encoding="utf-8",
        )
        self.run_powershell(
            f"Repair-HiaSafeProject -ProjectRoot {_ps_literal(fake_root)} | Out-Null"
        )
        config = (fake_root / ".codex" / "config.toml").read_text(encoding="utf-8")
        package = json.loads(
            (package_directory / "houdini_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("command = '.runtime\\fxhoudinimcp", config)
        self.assertIn("cwd = '.'", config)
        self.assertNotIn("Z:\\old-location", config)
        self.assertEqual("$HIA_PROJECT_ROOT/houdini_package", package["path"])
        self.assertNotIn("Z:/old-location", json.dumps(package))
        self.assertTrue((fake_root / ".runtime" / "launcher").is_dir())

    def test_report_json_redacts_credentials(self) -> None:
        output = self.run_powershell(
            """
$value = [pscustomobject]@{
    token = 'token-value-must-not-survive'
    cookie = 'cookie-value-must-not-survive'
    api_key = 'sk-abcdefghijklmnop'
    note = 'Authorization: Bearer bearer-value-must-not-survive'
}
ConvertTo-HiaRedactedJson -Value $value -Compress
"""
        )
        self.assertNotIn("token-value-must-not-survive", output)
        self.assertNotIn("cookie-value-must-not-survive", output)
        self.assertNotIn("sk-abcdefghijklmnop", output)
        self.assertNotIn("bearer-value-must-not-survive", output)
        self.assertIn("[REDACTED]", output)

    def test_check_only_json_runs_without_starting_houdini_gui(self) -> None:
        missing_houdini = self.sandbox / "never-started" / "houdini.exe"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Mta",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(LAUNCHER_PATH),
                "-CheckOnly",
                "-Json",
                "-HoudiniExe",
                str(missing_houdini),
                "-BridgePython",
                sys.executable,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=45,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("red", result["overall"])
        self.assertEqual("hia_v2", result["mcp_backend"])
        self.assertEqual(str(missing_houdini), result["selected_houdini"])
        checks = {check["id"]: check for check in result["checks"]}
        self.assertEqual("green", checks["project.codex_config_required"]["level"])
        self.assertTrue(Path(result["report"]["json_path"]).is_file())
        self.assertTrue(Path(result["report"]["log_path"]).is_file())

    def test_preflight_flags_required_project_mcp_as_recovery_blocker(self) -> None:
        fake_root = self.sandbox / "required-project-mcp"
        config_directory = fake_root / ".codex"
        config_directory.mkdir(parents=True)
        (config_directory / "config.toml").write_text(
            """[mcp_servers.houdini_intelligence]
command = '.runtime\\fxhoudinimcp\\1.3.0\\venv\\Scripts\\python.exe'
cwd = '.'
enabled = true
required = true
""",
            encoding="utf-8",
        )
        output = self.run_powershell(
            f"""
$result = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
    -BridgePython {_ps_literal(fake_root / 'missing' / 'python.exe')} `
    -ProbeOverrides @{{ runtime_writable = $false; loopback = $true }}
$check = $result.checks | Where-Object id -eq 'project.codex_config_required'
$check | ConvertTo-Json -Compress
"""
        )
        check = json.loads(output)
        self.assertEqual("red", check["level"])
        self.assertIn("required=true", check["message"])
        self.assertIn("任务恢复", check["message"])

    def test_settings_store_backend_with_paths_and_default_legacy_settings_to_hia_v2(self) -> None:
        fake_root = self.sandbox / "settings-project"
        fake_root.mkdir()
        houdini = fake_root / "Houdini" / "bin" / "houdini.exe"
        bridge = fake_root / "Python" / "python.exe"
        self.run_powershell(
            f"""
Write-HiaLauncherSettings `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(houdini)} `
    -BridgePython {_ps_literal(bridge)} `
    -McpBackend fxhoudini | Out-Null
"""
        )
        settings_path = fake_root / ".runtime" / "launcher" / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual({"houdini_exe", "bridge_python", "mcp_backend"}, set(settings))
        self.assertEqual(str(houdini), settings["houdini_exe"])
        self.assertEqual(str(bridge), settings["bridge_python"])
        self.assertEqual("fxhoudini", settings["mcp_backend"])

        settings.pop("mcp_backend")
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        output = self.run_powershell(
            f"""
$settings = Read-HiaLauncherSettings -ProjectRoot {_ps_literal(fake_root)}
$choices = @(Get-HiaMcpBackendChoices)
[pscustomobject]@{{ settings = $settings; choices = $choices }} | ConvertTo-Json -Depth 5 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("hia_v2", payload["settings"]["mcp_backend"])
        self.assertEqual(str(houdini), payload["settings"]["houdini_exe"])
        self.assertEqual(
            ["hia_v2", "fxhoudini"],
            [choice["id"] for choice in payload["choices"]],
        )

    def test_preflight_checks_only_the_selected_mcp_backend(self) -> None:
        fake_root = self.sandbox / "backend-preflight"
        fake_root.mkdir()
        output = self.run_powershell(
            f"""
$overrides = @{{ runtime_writable = $true; loopback = $true }}
$hiaResult = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -McpBackend hia_v2 `
    -ProbeOverrides $overrides
$fxResult = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -McpBackend fxhoudini `
    -ProbeOverrides $overrides
[pscustomobject]@{{
    hia = @($hiaResult.checks | ForEach-Object id)
    fx = @($fxResult.checks | ForEach-Object id)
}} | ConvertTo-Json -Depth 5 -Compress
"""
        )
        payload = json.loads(output)
        self.assertIn("hia_mcp_v2.runtime", payload["hia"])
        self.assertNotIn("fxhoudinimcp.runtime", payload["hia"])
        self.assertIn("fxhoudinimcp.runtime", payload["fx"])
        self.assertNotIn("hia_mcp_v2.runtime", payload["fx"])

    def test_wpf_xaml_loads_and_exposes_required_controls(self) -> None:
        ET.parse(XAML_PATH)
        required_types = {
            "HiaLogoMark": "System.Windows.Controls.Grid",
            "OverallStatusBadge": "System.Windows.Controls.Border",
            "McpBackendComboBox": "System.Windows.Controls.ComboBox",
            "HoudiniComboBox": "System.Windows.Controls.ComboBox",
            "HoudiniPathText": "System.Windows.Controls.TextBlock",
            "BridgePythonComboBox": "System.Windows.Controls.ComboBox",
            "BridgePathText": "System.Windows.Controls.TextBlock",
            "PassCountText": "System.Windows.Controls.TextBlock",
            "WarningCountText": "System.Windows.Controls.TextBlock",
            "BlockedCountText": "System.Windows.Controls.TextBlock",
            "ChecksListBox": "System.Windows.Controls.ItemsControl",
            "BusyProgressBar": "System.Windows.Controls.ProgressBar",
            "InlineStatusText": "System.Windows.Controls.TextBlock",
            "ReportPathTextBox": "System.Windows.Controls.TextBox",
            "RescanButton": "System.Windows.Controls.Button",
            "RepairButton": "System.Windows.Controls.Button",
            "CopyReportButton": "System.Windows.Controls.Button",
            "LaunchButton": "System.Windows.Controls.Button",
        }
        names = ", ".join(f"'{name}'" for name in required_types)
        probe = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName PresentationFramework
[xml]$xaml = [IO.File]::ReadAllText({_ps_literal(XAML_PATH)})
$reader = [Xml.XmlNodeReader]::new($xaml)
try {{
    $window = [Windows.Markup.XamlReader]::Load($reader)
    $types = [ordered]@{{}}
    foreach ($name in @({names})) {{
        $control = $window.FindName($name)
        if ($null -eq $control) {{ throw "Missing control: $name" }}
        $types[$name] = $control.GetType().FullName
    }}
    $types | ConvertTo-Json -Compress
}} finally {{
    $reader.Close()
}}
"""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Sta",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                probe,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        self.assertEqual(required_types, json.loads(completed.stdout))

    def test_wpf_xaml_is_self_contained_static_and_uses_standard_chrome(self) -> None:
        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        combined = xaml_source + wpf_source + launcher_source

        self.assertIn('FontFamily="Segoe UI"', xaml_source)
        self.assertRegex(xaml_source, r'MinWidth="\d+"')
        self.assertRegex(xaml_source, r'MinHeight="\d+"')
        self.assertIn('x:Name="BusyProgressBar"', xaml_source)
        self.assertIn('IsIndeterminate="True"', xaml_source)
        self.assertGreaterEqual(xaml_source.count('TextTrimming="CharacterEllipsis"'), 2)
        self.assertGreaterEqual(xaml_source.count("ToolTip="), 3)
        self.assertIn('x:Name="HiaLogoMark"', xaml_source)
        self.assertIn('x:Key="AccentPinkBrush"', xaml_source)
        self.assertIn("<LinearGradientBrush", xaml_source)
        self.assertIn("<Path", xaml_source)
        self.assertIn("<Ellipse", xaml_source)
        self.assertIn("CornerRadius=", xaml_source)
        for trigger in ("IsMouseOver", "IsPressed", "IsKeyboardFocused", "IsEnabled"):
            self.assertIn(trigger, xaml_source)

        forbidden = (
            "<DataGrid",
            "<Image",
            "BitmapImage",
            "<MediaElement",
            "<WebBrowser",
            "ResourceDictionary Source=",
            "clr-namespace:",
            "assembly=",
            "pack://",
            "file://",
            "Storyboard",
            "BeginStoryboard",
            "EventTrigger",
            "Animation",
            'WindowStyle="None"',
            'AllowsTransparency="True"',
            "WindowChrome",
            "DragMove",
            "x:Class=",
        )
        for text in forbidden:
            self.assertNotIn(text, combined)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'>\s])[a-z]:\\", combined))
        self.assertNotIn(r"\\", xaml_source)
        uri_set = set(re.findall(r"(?:https?|file|pack)://[^\"\s<]+", xaml_source))
        self.assertEqual(
            {
                "http://schemas.microsoft.com/winfx/2006/xaml/presentation",
                "http://schemas.microsoft.com/winfx/2006/xaml",
            },
            uri_set,
        )

    def test_wpf_runtime_pickers_have_explicit_high_contrast_templates(self) -> None:
        tree = ET.parse(XAML_PATH)
        root = tree.getroot()
        presentation = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
        xaml_key = "{http://schemas.microsoft.com/winfx/2006/xaml}Key"
        xaml_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"

        resources = {
            element.attrib[xaml_key]: element.attrib.get("Color", "")
            for element in root.iter(f"{presentation}SolidColorBrush")
            if xaml_key in element.attrib
        }

        def channel(value: int) -> float:
            normalized = value / 255.0
            return (
                normalized / 12.92
                if normalized <= 0.04045
                else ((normalized + 0.055) / 1.055) ** 2.4
            )

        def luminance(color: str) -> float:
            rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
            return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])

        def contrast(first: str, second: str) -> float:
            lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
            return (lighter + 0.05) / (darker + 0.05)

        self.assertGreater(
            contrast(resources["PickerTextBrush"], resources["PickerBackgroundBrush"]),
            7.0,
        )
        self.assertGreater(
            contrast(
                resources["PickerSecondaryTextBrush"],
                resources["PickerBackgroundBrush"],
            ),
            4.5,
        )
        self.assertGreater(
            contrast(
                resources["PickerDisabledTextBrush"],
                resources["PickerDisabledBackgroundBrush"],
            ),
            4.5,
        )

        styles = {
            element.attrib.get(xaml_key): ET.tostring(element, encoding="unicode")
            for element in root.iter(f"{presentation}Style")
            if xaml_key in element.attrib
        }
        combo_style = styles["DarkPickerComboBoxStyle"]
        item_style = styles["DarkPickerComboBoxItemStyle"]
        for required in (
            "ControlTemplate",
            "PART_Popup",
            "PickerPopupBrush",
            "Background",
            "Foreground",
            "BorderBrush",
            "IsMouseOver",
            "IsKeyboardFocusWithin",
            "IsDropDownOpen",
            "IsEnabled",
        ):
            self.assertIn(required, combo_style)
        for required in (
            "ControlTemplate",
            "Background",
            "Foreground",
            "BorderBrush",
            "IsHighlighted",
            "IsSelected",
            "IsKeyboardFocusWithin",
            "IsEnabled",
        ):
            self.assertIn(required, item_style)

        combo_expectations = {
            "McpBackendComboBox": "BackendPickerItemTemplate",
            "HoudiniComboBox": "HoudiniPickerItemTemplate",
            "BridgePythonComboBox": "BridgePickerItemTemplate",
        }
        combo_boxes = {
            element.attrib.get(xaml_name): element
            for element in root.iter(f"{presentation}ComboBox")
        }
        for name, template in combo_expectations.items():
            combo = combo_boxes[name]
            self.assertEqual(
                "{StaticResource DarkPickerComboBoxStyle}", combo.attrib["Style"]
            )
            self.assertEqual(
                f"{{StaticResource {template}}}", combo.attrib["ItemTemplate"]
            )
            self.assertNotIn("DisplayMemberPath", combo.attrib)

        templates = {
            element.attrib.get(xaml_key): ET.tostring(element, encoding="unicode")
            for element in root.iter(f"{presentation}DataTemplate")
            if xaml_key in element.attrib
        }
        for template_name in ("HoudiniPickerItemTemplate", "BridgePickerItemTemplate"):
            template = templates[template_name]
            self.assertGreaterEqual(template.count("TextBlock"), 2)
            self.assertIn("path", template)
            self.assertIn("ToolTip", template)
            self.assertIn("CharacterEllipsis", template)
        self.assertIn("version", templates["HoudiniPickerItemTemplate"])
        self.assertIn("source", templates["BridgePickerItemTemplate"])

    def test_exe_project_root_locator_works_after_project_move(self) -> None:
        fake_root = self.sandbox / "moved-launcher-project"
        nested_launcher = fake_root / ".runtime" / "dist" / "launcher"
        (fake_root / "scripts").mkdir(parents=True)
        nested_launcher.mkdir(parents=True)
        (fake_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (fake_root / "scripts" / "hia-launcher.ps1").write_text(
            "# launcher marker\n", encoding="utf-8"
        )
        (fake_root / "scripts" / "launch-houdini.ps1").write_text(
            "# lifecycle marker\n", encoding="utf-8"
        )
        compiler_temp = self.sandbox / "csharp-compiler-temp"
        output = self.run_powershell(
            f"""
[System.IO.Directory]::CreateDirectory({_ps_literal(compiler_temp)}) | Out-Null
$env:TEMP = {_ps_literal(compiler_temp)}
$env:TMP = {_ps_literal(compiler_temp)}
$source = [System.IO.File]::ReadAllText({_ps_literal(EXE_ROOT_LOCATOR_PATH)})
Add-Type -TypeDefinition $source -Language CSharp
[HoudiniIntelligenceLauncher.ProjectRootLocator]::Find({_ps_literal(nested_launcher)})
"""
        )
        self.assertEqual(str(fake_root), output)

        locator_source = EXE_ROOT_LOCATOR_PATH.read_text(encoding="utf-8")
        app_source = EXE_APP_PATH.read_text(encoding="utf-8")
        self.assertIn("AppContext.BaseDirectory", app_source)
        self.assertIn("DirectoryInfo", locator_source)
        self.assertIn("current.Parent", locator_source)
        self.assertNotIn("Environment.CurrentDirectory", app_source + locator_source)
        self.assertIsNone(
            re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", app_source + locator_source)
        )

    def test_exe_project_and_build_script_are_portable_and_self_contained(self) -> None:
        project = ET.parse(EXE_PROJECT_PATH).getroot()
        properties = {
            element.tag: (element.text or "").strip()
            for group in project.findall("PropertyGroup")
            for element in group
        }
        self.assertEqual("WinExe", properties["OutputType"])
        self.assertEqual("net8.0-windows", properties["TargetFramework"])
        self.assertEqual("true", properties["UseWPF"])
        self.assertEqual("HoudiniIntelligenceLauncher", properties["AssemblyName"])
        self.assertEqual("win-x64", properties["RuntimeIdentifier"])
        self.assertEqual("true", properties["SelfContained"])
        self.assertEqual("true", properties["PublishSingleFile"])
        self.assertEqual("false", properties["IncludeNativeLibrariesForSelfExtract"])
        self.assertEqual("false", properties["PublishTrimmed"])
        self.assertEqual([], project.findall(".//PackageReference"))

        build_source = EXE_BUILD_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        app_source = EXE_APP_PATH.read_text(encoding="utf-8")
        locator_source = EXE_ROOT_LOCATOR_PATH.read_text(encoding="utf-8")
        combined = build_source + app_source + locator_source
        for required in (
            "$PSScriptRoot",
            ".runtime",
            "DOTNET_CLI_HOME",
            "NUGET_PACKAGES",
            "NUGET_HTTP_CACHE_PATH",
            "NUGET_PLUGINS_CACHE_PATH",
            "$env:TEMP",
            "$env:TMP",
            "BaseOutputPath",
            "BaseIntermediateOutputPath",
            "MSBuildProjectExtensionsPath",
            "dist\\launcher",
            "https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/8.0/releases.json",
            "https://builds.dotnet.microsoft.com/",
            "Get-FileHash",
            "--continue-at",
            "Expand-Archive",
            "win-x64",
            "--self-contained",
            "--smoke-test",
        ):
            self.assertIn(required, build_source)
        self.assertIn('Path.Combine(projectRoot, "scripts", "hia-launcher.ps1")', app_source)
        self.assertIn("ArgumentList.Add", app_source)
        self.assertNotIn("Remove-Item", build_source)
        self.assertNotIn("SetEnvironmentVariable", combined)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", combined))

        launcher_assets = [
            path
            for root_path in (EXE_PROJECT_ROOT, XAML_PATH.parent)
            for path in root_path.rglob("*")
            if path.suffix.lower()
            in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".mp4"}
        ]
        self.assertEqual([], launcher_assets)

    def test_cli_modes_do_not_load_wpf_assets(self) -> None:
        fake_root = self.sandbox / "portable-cli-no-ui"
        launcher_directory = fake_root / "scripts" / "launcher"
        launcher_directory.mkdir(parents=True)
        shutil.copy2(LAUNCHER_PATH, fake_root / "scripts" / "hia-launcher.ps1")
        shutil.copy2(MODULE_PATH, launcher_directory / "HiaLauncher.Core.psm1")
        (fake_root / "scripts" / "launch-houdini.ps1").write_text(
            "# lifecycle marker\n", encoding="utf-8"
        )
        (fake_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        self.assertFalse((launcher_directory / "HiaLauncher.Wpf.ps1").exists())
        self.assertFalse((launcher_directory / "HiaLauncher.xaml").exists())

        missing_houdini = fake_root / "missing" / "houdini.exe"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Mta",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(fake_root / "scripts" / "hia-launcher.ps1"),
                "-CheckOnly",
                "-Json",
                "-HoudiniExe",
                str(missing_houdini),
                "-BridgePython",
                sys.executable,
            ],
            cwd=fake_root,
            capture_output=True,
            encoding="utf-8",
            timeout=45,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual("red", json.loads(completed.stdout)["overall"])

        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        main_guard = launcher_source.index(
            "if ($CheckOnly -or $Json) {", launcher_source.index("$inputs =")
        )
        gui_entry = launcher_source.index("$wpfUiPath")
        self.assertLess(main_guard, gui_entry)
        self.assertIn("exit 0", launcher_source[main_guard:gui_entry])
        self.assertNotIn("PresentationFramework", launcher_source)

    def test_launcher_is_portable_and_exposes_cli_and_gui_controls(self) -> None:
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        module_source = MODULE_PATH.read_text(encoding="utf-8-sig")
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        lifecycle_source = (
            REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        ).read_text(encoding="utf-8")
        combined = (
            launcher_source
            + module_source
            + wpf_source
            + xaml_source
            + lifecycle_source
        )
        self.assertNotIn(r"E:\houdini-intelligence-agent", combined)
        self.assertIn("[switch]$CheckOnly", launcher_source)
        self.assertIn("[switch]$Json", launcher_source)
        self.assertIn("HiaLauncher.Wpf.ps1", launcher_source)
        self.assertIn("HiaLauncher.xaml", wpf_source)
        for control_name in (
            "McpBackendComboBox",
            "RescanButton",
            "RepairButton",
            "CopyReportButton",
            "LaunchButton",
        ):
            self.assertIn(f'x:Name="{control_name}"', xaml_source)
        self.assertEqual(
            1,
            launcher_source.count(
                "Join-Path $projectRoot 'scripts\\launch-houdini.ps1'"
            ),
        )
        self.assertNotIn("System.Windows.Forms", combined)
        self.assertNotIn("System.Drawing", combined)
        self.assertNotIn("HIA_BRIDGE_TOKEN", launcher_source + wpf_source)
        self.assertNotIn("HIA_BRIDGE_URL", launcher_source + wpf_source)

    def test_lifecycle_uses_one_portable_project_cache_for_both_children(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("Join-Path $ResolvedRoot '.runtime\\cache'", source)
        for child in ("screenshots", "previews", "tmp"):
            self.assertIn(f"Join-Path $cacheRoot '{child}'", source)
        self.assertEqual(2, source.count("'HIA_CACHE_DIR' = $cacheRoot"))
        self.assertIn("'TEMP' = $sessionTemp", source)
        self.assertIn("'TMP' = $sessionTemp", source)
        self.assertIn("'HOUDINI_TEMP_DIR' = $sessionTemp", source)
        self.assertNotIn(r"E:\houdini-intelligence-agent", source)
        self.assertIn(".runtime/", gitignore.splitlines())

    def test_lifecycle_selects_mutually_exclusive_backend_paths_and_environment(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "[ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2'",
            source,
        )
        self.assertIn("'--mcp-backend'", source)
        self.assertIn("'HIA_MCP_BACKEND' = $McpBackend", source)
        self.assertEqual(2, source.count("Remove-ChildEnvironment -StartInfo"))

        hia_start = source.index("if ($McpBackend -eq 'hia_v2')")
        fallback_start = source.index("} else {", hia_start)
        backend_end = source.index("$bridgeProcessPythonPath", fallback_start)
        hia_branch = source[hia_start:fallback_start]
        fallback_branch = source[fallback_start:backend_end]
        self.assertIn("services\\hia_mcp_v2", hia_branch)
        self.assertIn(".runtime\\hia-mcp-v2", hia_branch)
        self.assertIn("'HIA_MCP_V2_HOST' = '127.0.0.1'", hia_branch)
        self.assertIn("'HIA_MCP_V2_ROUTE' = '/hia-mcp-v2/v1/execute'", hia_branch)
        self.assertIn("'HIA_MCP_V2_AUTOSTART'", hia_branch)
        self.assertNotIn("fxhoudinimcp", hia_branch.casefold())
        self.assertNotIn("FXHOUDINIMCP_", hia_branch)

        self.assertIn(".runtime\\fxhoudinimcp\\1.3.0", fallback_branch)
        self.assertIn("'FXHOUDINIMCP_AUTOSTART' = '1'", fallback_branch)
        self.assertIn("'FXHOUDINIMCP_TOKEN' = $houdiniMcpToken", fallback_branch)
        self.assertNotIn("HIA_MCP_V2_", fallback_branch)

    def test_uiready_starts_only_the_selected_backend_and_checks_hia_readiness(self) -> None:
        sources = [path.read_text(encoding="utf-8") for path in UI_READY_PATHS]
        self.assertEqual(sources[0], sources[1])
        self.assertIn('if _backend == "hia_v2"', sources[0])
        self.assertIn('elif _backend == "fxhoudini"', sources[0])

        hia_calls: list[dict[str, object]] = []
        fake_hia = types.ModuleType("hia_mcp_runtime")

        class _AliveThread:
            @staticmethod
            def is_alive() -> bool:
                return True

        def start_runtime_server(**kwargs: object) -> object:
            hia_calls.append(dict(kwargs))
            return types.SimpleNamespace(
                host="127.0.0.1",
                port=45123,
                route="/hia-mcp-v2/v1/execute",
                thread=_AliveThread(),
                stop=lambda: None,
            )

        fake_hia.start_runtime_server = start_runtime_server  # type: ignore[attr-defined]
        forbidden_fx = types.ModuleType("fxhoudinimcp_server")
        forbidden_fx.startup = types.SimpleNamespace(  # type: ignore[attr-defined]
            ensure_running=lambda: self.fail("FX fallback started in the HIA branch")
        )
        runtime_directory = REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2"
        hia_environment = {
            "HIA_MCP_BACKEND": "hia_v2",
            "HIA_MCP_V2_AUTOSTART": "1",
            "HIA_PROJECT_ROOT": str(REPOSITORY_ROOT),
            "HIA_MCP_V2_RUNTIME_DIR": str(runtime_directory),
            "HIA_MCP_V2_HOST": "127.0.0.1",
            "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
            "HIA_MCP_V2_PORT": "45123",
            "HIA_MCP_V2_TOKEN": "T" * 48,
        }
        with (
            mock.patch.dict(os.environ, hia_environment, clear=True),
            mock.patch.dict(
                sys.modules,
                {"hia_mcp_runtime": fake_hia, "fxhoudinimcp_server": forbidden_fx},
            ),
        ):
            namespace = runpy.run_path(str(UI_READY_PATHS[0]))
        self.assertEqual(1, len(hia_calls))
        self.assertEqual(45123, hia_calls[0]["port"])
        self.assertEqual("T" * 48, hia_calls[0]["token"])
        self.assertIn("_hia_mcp_v2_session", namespace)

        fx_calls: list[str] = []
        fake_fx = types.ModuleType("fxhoudinimcp_server")
        fake_fx.startup = types.SimpleNamespace(  # type: ignore[attr-defined]
            ensure_running=lambda: fx_calls.append("started")
        )
        forbidden_hia = types.ModuleType("hia_mcp_runtime")
        forbidden_hia.start_runtime_server = (  # type: ignore[attr-defined]
            lambda **_: self.fail("HIA runtime started in the fallback branch")
        )
        with (
            mock.patch.dict(
                os.environ,
                {
                    "HIA_MCP_BACKEND": "fxhoudini",
                    "FXHOUDINIMCP_AUTOSTART": "1",
                },
                clear=True,
            ),
            mock.patch.dict(
                sys.modules,
                {"hia_mcp_runtime": forbidden_hia, "fxhoudinimcp_server": fake_fx},
            ),
        ):
            runpy.run_path(str(UI_READY_PATHS[0]))
        self.assertEqual(["started"], fx_calls)


if __name__ == "__main__":
    unittest.main()
