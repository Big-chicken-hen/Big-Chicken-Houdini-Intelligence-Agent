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

    def test_hython_license_failure_is_reported_without_becoming_timeout(self) -> None:
        houdini = self.make_houdini_install("Houdini 21.0.440")
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput 'Houdini 21.0.440' `
    -HoudiniExitCode 0 `
    -HythonOutput 'No licenses could be found to run this application. Please check for a valid license server host' `
    -HythonExitCode 3)
$checks | Where-Object id -eq 'houdini.hython_probe' | ConvertTo-Json -Compress
"""
        )
        check = json.loads(output)
        self.assertEqual("red", check["level"])
        self.assertIn("许可证", check["message"])
        self.assertIn("License Administrator", check["advice"])
        self.assertNotIn("超时", check["message"])

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

    def test_codex_login_guidance_is_project_local_and_contains_no_credentials(self) -> None:
        fake_root = self.sandbox / "portable login project"
        fake_root.mkdir()
        output = self.run_powershell(
            f"Get-HiaCodexLoginCommand -ProjectRoot {_ps_literal(fake_root)}"
        )
        self.assertIn(str(fake_root / ".runtime" / "codex-home"), output)
        self.assertIn(
            str(
                fake_root
                / ".runtime"
                / "toolchains"
                / "codex"
                / "0.144.3"
                / "codex.exe"
            ),
            output,
        )
        self.assertIn("login --device-auth", output)
        self.assertNotRegex(output.lower(), r"(bearer|refresh_token|sk-proj)")

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
        render_output = fake_root / "Final Output"
        self.run_powershell(
            f"""
Write-HiaLauncherSettings `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(houdini)} `
    -BridgePython {_ps_literal(bridge)} `
    -RenderOutputDir {_ps_literal(render_output)} `
    -McpBackend fxhoudini | Out-Null
"""
        )
        settings_path = fake_root / ".runtime" / "launcher" / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"houdini_exe", "bridge_python", "render_output_dir", "mcp_backend"},
            set(settings),
        )
        self.assertEqual(str(houdini), settings["houdini_exe"])
        self.assertEqual(str(bridge), settings["bridge_python"])
        self.assertEqual(str(render_output), settings["render_output_dir"])
        self.assertEqual("fxhoudini", settings["mcp_backend"])

        settings.pop("mcp_backend")
        settings.pop("render_output_dir")
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
        self.assertEqual("", payload["settings"]["render_output_dir"])
        self.assertEqual(str(houdini), payload["settings"]["houdini_exe"])
        self.assertEqual(
            ["hia_v2", "fxhoudini"],
            [choice["id"] for choice in payload["choices"]],
        )

    def test_render_output_directory_defaults_validates_and_creates_writable_local_path(self) -> None:
        fake_root = self.sandbox / "render-output-project"
        fake_root.mkdir()
        houdini_root = fake_root / "Houdini 22.0"
        houdini = houdini_root / "bin" / "houdini.exe"
        custom_output = fake_root / "deliverables" / "final"
        output = self.run_powershell(
            f"""
$root = {_ps_literal(fake_root)}
$houdini = {_ps_literal(houdini)}
$custom = {_ps_literal(custom_output)}
$default = Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path '' -HoudiniExe $houdini
$beforeCreate = Test-Path -LiteralPath $custom
$resolved = Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path $custom -HoudiniExe $houdini
$created = Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path $custom -HoudiniExe $houdini -Create
$outside = Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path {_ps_literal(self.sandbox / 'outside-render-output')} -HoudiniExe $houdini -Create
[IO.File]::WriteAllText((Join-Path $created 'keep.txt'), 'keep')
$createdAgain = Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path $custom -HoudiniExe $houdini -Create
function Get-RenderOutputError([string]$Value) {{
    try {{
        Resolve-HiaRenderOutputDirectory -ProjectRoot $root -Path $Value -HoudiniExe $houdini | Out-Null
        return ''
    }} catch {{
        return [string]$_.Exception.Message
    }}
}}
[pscustomobject]@{{
    default = $default
    default_exists = (Test-Path -LiteralPath $default)
    before_create = $beforeCreate
    resolved = $resolved
    created = $createdAgain
    keep = (Test-Path -LiteralPath (Join-Path $createdAgain 'keep.txt') -PathType Leaf)
    relative_error = (Get-RenderOutputError 'relative\output')
    device_error = (Get-RenderOutputError '\\.\C:\HIA-output')
    outside = $outside
    windows_error = (Get-RenderOutputError (Join-Path $env:SystemRoot 'HIA-output-test'))
    houdini_error = (Get-RenderOutputError (Join-Path {_ps_literal(houdini_root)} 'renders'))
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(str(fake_root / ".runtime" / "cache"), payload["default"])
        self.assertFalse(payload["default_exists"])
        self.assertFalse(payload["before_create"])
        self.assertEqual(str(custom_output), payload["resolved"])
        self.assertEqual(str(custom_output), payload["created"])
        self.assertEqual(str(self.sandbox / "outside-render-output"), payload["outside"])
        self.assertTrue(payload["keep"])
        for key in (
            "relative_error",
            "device_error",
            "windows_error",
            "houdini_error",
        ):
            self.assertTrue(payload[key], key)
        self.assertEqual([], list(custom_output.glob(".hia-write-probe-*.tmp")))

    def test_screenshot_cache_cleanup_deletes_only_confirmed_top_level_png_files(self) -> None:
        fake_root = self.sandbox / "screenshot-cleanup-project"
        (fake_root / "scripts").mkdir(parents=True)
        (fake_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (fake_root / "scripts" / "launch-houdini.ps1").write_text(
            "# lifecycle marker\n", encoding="utf-8"
        )
        screenshots = fake_root / ".runtime" / "cache" / "screenshots"
        screenshots.mkdir(parents=True)
        first = screenshots / "viewport-a.png"
        second = screenshots / "flipbook-b.PNG"
        first.write_bytes(b"abc")
        second.write_bytes(b"12345")
        keep_jpg = screenshots / "keep.jpg"
        keep_text = screenshots / "keep.txt"
        keep_jpg.write_bytes(b"jpg")
        keep_text.write_text("keep", encoding="utf-8")
        nested = screenshots / "nested"
        nested.mkdir()
        nested_png = nested / "inside.png"
        nested_png.write_bytes(b"nested")

        untouched_files = []
        for relative in (
            Path(".runtime/cache/previews/keep.png"),
            Path(".runtime/cache/tmp/keep.png"),
            Path(".runtime/attachments/keep.png"),
            Path(".runtime/diagnostics/keep.png"),
        ):
            path = fake_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"untouched")
            untouched_files.append(path)
        final_output = self.sandbox / "external-final-output" / "keep.png"
        final_output.parent.mkdir()
        final_output.write_bytes(b"final")
        outside_bait = self.sandbox / "screenshots" / "viewport-a.png"
        outside_bait.parent.mkdir()
        outside_bait.write_bytes(b"bait")

        output = self.run_powershell(
            f"""
$root = {_ps_literal(fake_root)}
$preview = Invoke-HiaScreenshotCacheCleanup -ProjectRoot $root
$previewPreserved = (
    (Test-Path -LiteralPath {_ps_literal(first)} -PathType Leaf) -and
    (Test-Path -LiteralPath {_ps_literal(second)} -PathType Leaf)
)
function Get-CleanupError([object]$CleanupPlan) {{
    try {{
        Invoke-HiaScreenshotCacheCleanup -ProjectRoot $root -Plan $CleanupPlan -Delete | Out-Null
        return ''
    }} catch {{
        return [string]$_.Exception.Message
    }}
}}
$mismatchPlan = [pscustomobject]@{{
    target_path = {_ps_literal(outside_bait.parent)}
    skipped_count = $preview.skipped_count
    candidates = $preview.candidates
}}
$escapePlan = [pscustomobject]@{{
    target_path = (Join-Path $preview.target_path '..\previews')
    skipped_count = $preview.skipped_count
    candidates = $preview.candidates
}}
$mismatchError = Get-CleanupError $mismatchPlan
$escapeError = Get-CleanupError $escapePlan
$failedPlansPreserved = (
    (Test-Path -LiteralPath {_ps_literal(first)} -PathType Leaf) -and
    (Test-Path -LiteralPath {_ps_literal(second)} -PathType Leaf) -and
    (Test-Path -LiteralPath {_ps_literal(outside_bait)} -PathType Leaf)
)
$result = Invoke-HiaScreenshotCacheCleanup -ProjectRoot $root -Plan $preview -Delete
try {{
    Invoke-HiaScreenshotCacheCleanup `
        -ProjectRoot ([System.IO.Path]::GetPathRoot($root)) | Out-Null
    $driveRootError = ''
}} catch {{
    $driveRootError = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    preview_target = $preview.target_path
    preview_exists = $preview.directory_exists
    preview_count = $preview.matched_count
    preview_bytes = $preview.matched_bytes
    preview_skipped = $preview.skipped_count
    preview_preserved = $previewPreserved
    mismatch_error = $mismatchError
    escape_error = $escapeError
    failed_plans_preserved = $failedPlansPreserved
    drive_root_error = $driveRootError
    deleted_count = $result.deleted_count
    deleted_bytes = $result.deleted_bytes
    skipped_count = $result.skipped_count
    failed_count = $result.failed_count
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(str(screenshots), payload["preview_target"])
        self.assertTrue(payload["preview_exists"])
        self.assertEqual(2, payload["preview_count"])
        self.assertEqual(8, payload["preview_bytes"])
        self.assertEqual(3, payload["preview_skipped"])
        self.assertTrue(payload["preview_preserved"])
        self.assertTrue(payload["mismatch_error"])
        self.assertTrue(payload["escape_error"])
        self.assertTrue(payload["failed_plans_preserved"])
        self.assertTrue(payload["drive_root_error"])
        self.assertEqual(2, payload["deleted_count"])
        self.assertEqual(8, payload["deleted_bytes"])
        self.assertEqual(3, payload["skipped_count"])
        self.assertEqual(0, payload["failed_count"])

        self.assertTrue(screenshots.is_dir())
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        for path in (
            keep_jpg,
            keep_text,
            nested_png,
            *untouched_files,
            final_output,
            outside_bait,
        ):
            self.assertTrue(path.is_file(), path)

    def test_screenshot_cache_cleanup_rejects_every_reparse_path_level(self) -> None:
        def write_project_markers(project: Path) -> None:
            (project / "scripts").mkdir(parents=True, exist_ok=True)
            (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (project / "scripts" / "launch-houdini.ps1").write_text(
                "# lifecycle marker\n", encoding="utf-8"
            )

        for level in ("project-root", "runtime", "cache", "screenshots"):
            with self.subTest(level=level):
                case_root = self.sandbox / f"reparse-{level}"
                case_root.mkdir()
                if level == "project-root":
                    target = case_root / "real-project"
                    write_project_markers(target)
                    marker = target / ".runtime" / "cache" / "screenshots" / "keep.png"
                    marker.parent.mkdir(parents=True)
                    project = case_root / "linked-project"
                    link = project
                else:
                    project = case_root / "project"
                    write_project_markers(project)
                    if level == "runtime":
                        target = case_root / "runtime-target"
                        marker = target / "cache" / "screenshots" / "keep.png"
                        link = project / ".runtime"
                    elif level == "cache":
                        (project / ".runtime").mkdir()
                        target = case_root / "cache-target"
                        marker = target / "screenshots" / "keep.png"
                        link = project / ".runtime" / "cache"
                    else:
                        (project / ".runtime" / "cache").mkdir(parents=True)
                        target = case_root / "screenshots-target"
                        marker = target / "keep.png"
                        link = project / ".runtime" / "cache" / "screenshots"
                    marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"keep")

                output = self.run_powershell(
                    f"""
$link = {_ps_literal(link)}
$target = {_ps_literal(target)}
$created = $false
try {{
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    $created = $true
    try {{
        Invoke-HiaScreenshotCacheCleanup -ProjectRoot {_ps_literal(project)} | Out-Null
        $errorText = ''
    }} catch {{
        $errorText = [string]$_.Exception.Message
    }}
    [pscustomobject]@{{
        error = $errorText
        marker_exists = (Test-Path -LiteralPath {_ps_literal(marker)} -PathType Leaf)
    }} | ConvertTo-Json -Compress
}} finally {{
    if ($created) {{
        $linkItem = Get-Item -LiteralPath $link -Force -ErrorAction Stop
        if (
            ([int]$linkItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -eq 0
        ) {{
            throw 'Test junction unexpectedly lost its reparse-point attribute.'
        }}
        [System.IO.Directory]::Delete($link, $false)
    }}
}}
"""
                )
                payload = json.loads(output)
                self.assertIn("reparse point", payload["error"])
                self.assertTrue(payload["marker_exists"])
                self.assertFalse(link.exists())
                self.assertTrue(marker.is_file())

    def test_screenshot_cache_cleanup_source_is_fail_closed_and_non_recursive(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8-sig")
        start = source.index("function Invoke-HiaScreenshotCacheCleanup")
        end = source.index("function Test-HiaLoopbackPorts", start)
        cleanup_source = source[start:end]

        for required in (
            "Get-HiaProjectRoot",
            "Join-Path $root '.runtime\\cache\\screenshots'",
            "[System.StringComparer]::OrdinalIgnoreCase.Equals",
            "GetFileSystemInfos()",
            "[System.IO.FileAttributes]::ReparsePoint",
            "[System.IO.File]::Delete",
            "'.png'",
            "last_write_utc_ticks",
        ):
            self.assertIn(required, cleanup_source)
        for forbidden in (
            "StartsWith",
            "Remove-Item",
            "-Recurse",
            "$HOME",
            "USERPROFILE",
            "HIA_RENDER_OUTPUT_DIR",
            "~",
            "'*'",
            '"*"',
        ):
            self.assertNotIn(forbidden, cleanup_source)
        self.assertIsNone(
            re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", cleanup_source)
        )

        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        handler_start = wpf_source.index("$cleanupScreenshotsButton.Add_Click")
        handler_end = wpf_source.index("$copyReportButton.Add_Click", handler_start)
        cleanup_handler = wpf_source[handler_start:handler_end]
        preview_call = cleanup_handler.index("Invoke-HiaScreenshotCacheCleanup")
        confirmation = cleanup_handler.index("[System.Windows.MessageBox]::Show")
        delete_call = cleanup_handler.index("-Delete", confirmation)
        self.assertLess(preview_call, confirmation)
        self.assertLess(confirmation, delete_call)
        for required in (
            "唯一允许目标",
            "匹配 PNG 文件",
            "总大小",
            "[System.Windows.MessageBoxButton]::YesNo",
            "[System.Windows.MessageBoxResult]::No",
            "未删除任何文件",
            "已删除 {0} 个",
            "跳过 {2} 个",
            "失败 {3} 个",
            "$cleanupScreenshotsButton.IsEnabled = -not $Busy",
        ):
            self.assertIn(required, wpf_source)

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
            "LayoutRoot": "System.Windows.Controls.Grid",
            "RightVisualRail": "System.Windows.Controls.Border",
            "RecoveryCard": "System.Windows.Controls.Border",
            "RecoveryCheckpointText": "System.Windows.Controls.TextBlock",
            "RecoverCheckpointOption": "System.Windows.Controls.RadioButton",
            "NormalLaunchOption": "System.Windows.Controls.RadioButton",
            "OverallStatusBadge": "System.Windows.Controls.Border",
            "OverallStatusDot": "System.Windows.Shapes.Ellipse",
            "OverallStatusText": "System.Windows.Controls.TextBlock",
            "McpBackendComboBox": "System.Windows.Controls.ComboBox",
            "HoudiniComboBox": "System.Windows.Controls.ComboBox",
            "BrowseHoudiniButton": "System.Windows.Controls.Button",
            "HoudiniPathText": "System.Windows.Controls.TextBlock",
            "BridgePythonComboBox": "System.Windows.Controls.ComboBox",
            "BrowseBridgeButton": "System.Windows.Controls.Button",
            "BridgePathText": "System.Windows.Controls.TextBlock",
            "RenderOutputTextBox": "System.Windows.Controls.TextBox",
            "BrowseRenderOutputButton": "System.Windows.Controls.Button",
            "PassCountText": "System.Windows.Controls.TextBlock",
            "WarningCountText": "System.Windows.Controls.TextBlock",
            "BlockedCountText": "System.Windows.Controls.TextBlock",
            "ChecksListBox": "System.Windows.Controls.ItemsControl",
            "EmptyStateBorder": "System.Windows.Controls.Border",
            "EmptyStateText": "System.Windows.Controls.TextBlock",
            "BusyPanel": "System.Windows.Controls.Border",
            "BusyProgressBar": "System.Windows.Controls.ProgressBar",
            "InlineStatusBorder": "System.Windows.Controls.Border",
            "InlineStatusText": "System.Windows.Controls.TextBlock",
            "ReportPathTextBox": "System.Windows.Controls.TextBox",
            "RescanButton": "System.Windows.Controls.Button",
            "RepairButton": "System.Windows.Controls.Button",
            "CleanupScreenshotsButton": "System.Windows.Controls.Button",
            "CopyReportButton": "System.Windows.Controls.Button",
            "LaunchButton": "System.Windows.Controls.Button",
        }
        names = ", ".join(f"'{name}'" for name in required_types)
        probe = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName PresentationFramework
[xml]$xaml = [IO.File]::ReadAllText({_ps_literal(XAML_PATH)})
$reader = [Xml.XmlNodeReader]::new($xaml)
try {{
    $window = [Windows.Markup.XamlReader]::Load($reader)
    $window.Content.Measure([Windows.Size]::new(640, 360))
    $window.Content.Arrange([Windows.Rect]::new(0, 0, 640, 360))
    $types = [ordered]@{{}}
    foreach ($name in @({names})) {{
        $control = $window.FindName($name)
        if ($null -eq $control) {{ throw "Missing control: $name" }}
        $types[$name] = $control.GetType().FullName
    }}
    $loadedWindow = $window
    $layoutRoot = $loadedWindow.FindName('LayoutRoot')
    $rightVisualRail = $loadedWindow.FindName('RightVisualRail')
    $wpfSource = [IO.File]::ReadAllText({_ps_literal(WPF_SCRIPT_PATH)})
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput(
        $wpfSource,
        [ref]$tokens,
        [ref]$errors
    )
    $responsiveFunction = @($ast.FindAll({{
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Update-ResponsiveLayout'
    }}, $true))[0]
    . ([scriptblock]::Create($responsiveFunction.Extent.Text))
    $script:compactLayout = $null
    $window = [pscustomobject]@{{ ActualWidth = 640.0 }}
    Update-ResponsiveLayout
    if (
        -not $script:compactLayout -or
        [Windows.Controls.Grid]::GetColumn($rightVisualRail) -ne 0 -or
        [Windows.Controls.Grid]::GetRow($rightVisualRail) -ne 7 -or
        [Windows.Controls.Grid]::GetRowSpan($rightVisualRail) -ne 1 -or
        $layoutRoot.ColumnDefinitions[2].Width.ToString() -ne '0'
    ) {{ throw 'Compact responsive layout did not activate.' }}
    $window.ActualWidth = 1120.0
    Update-ResponsiveLayout
    if (
        $script:compactLayout -or
        [Windows.Controls.Grid]::GetColumn($rightVisualRail) -ne 2 -or
        [Windows.Controls.Grid]::GetRow($rightVisualRail) -ne 0 -or
        [Windows.Controls.Grid]::GetRowSpan($rightVisualRail) -ne 8 -or
        $layoutRoot.ColumnDefinitions[0].Width.ToString() -ne '3*' -or
        $layoutRoot.ColumnDefinitions[2].Width.ToString() -ne '2*'
    ) {{ throw 'Wide responsive layout did not restore.' }}
    $window = $loadedWindow
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
        self.assertNotIn('x:Name="HiaLogoMark"', xaml_source)
        self.assertNotIn("小助手", combined)
        self.assertIn('x:Key="HoudiniOrangeBrush"', xaml_source)
        self.assertIn("<LinearGradientBrush", xaml_source)
        self.assertIn("<Path", xaml_source)
        self.assertIn("<Ellipse", xaml_source)
        self.assertIn("CornerRadius=", xaml_source)
        for text in (
            "最终渲染输出目录",
            "EXR",
            "图片",
            "视频",
            "USD",
            "导出",
            "模拟缓存",
            ".runtime/cache",
            "内部截图、预览和临时缓存目录不同",
        ):
            self.assertIn(text, xaml_source)
        for trigger in ("IsMouseOver", "IsPressed", "IsKeyboardFocused", "IsEnabled"):
            self.assertIn(trigger, xaml_source)

        forbidden = (
            "<DataGrid",
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
        self.assertEqual(0, xaml_source.count("<Image"))
        self.assertIn("CREATIVE WORKSPACE", xaml_source)
        self.assertIn('x:Name="RightVisualRail"', xaml_source)
        self.assertIn("Update-ResponsiveLayout", wpf_source)
        self.assertNotIn("[System.Windows.Media.Imaging.BitmapImage]::new()", wpf_source)
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
        self.assertGreater(contrast("#FFFFFF", resources["PickerHoverBrush"]), 7.0)
        self.assertGreater(contrast("#FFFFFF", resources["PickerSelectedBrush"]), 7.0)

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
            "TextElement.Foreground",
            "12,7,44,7",
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
            "TextElement.Foreground",
            "Opacity",
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
            self.assertIn("PickerTextBrush", template)
            self.assertIn("PickerSecondaryTextBrush", template)
        self.assertIn("version", templates["HoudiniPickerItemTemplate"])
        self.assertIn("StringFormat=Houdini {0}", templates["HoudiniPickerItemTemplate"])
        self.assertIn("source", templates["BridgePickerItemTemplate"])

        tab_indices = sorted(
            int(element.attrib["TabIndex"])
            for element in root.iter()
            if "TabIndex" in element.attrib
        )
        self.assertEqual(list(range(15)), tab_indices)
        self.assertLessEqual(int(root.attrib["MinWidth"]), 640)
        self.assertLessEqual(int(root.attrib["MinHeight"]), 360)
        self.assertEqual("Cycle", root.attrib["KeyboardNavigation.TabNavigation"])
        self.assertEqual("True", root.attrib["UseLayoutRounding"])
        self.assertEqual("True", root.attrib["SnapsToDevicePixels"])
        self.assertEqual("Ideal", root.attrib["TextOptions.TextFormattingMode"])
        self.assertIsNotNone(root.find(f"{presentation}ScrollViewer"))
        self.assertIsNotNone(next(root.iter(f"{presentation}WrapPanel"), None))
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("[System.Windows.SystemParameters]::WorkArea", wpf_source)
        self.assertIn("$window.Width = [Math]::Min", wpf_source)
        self.assertIn("$window.Height = [Math]::Min", wpf_source)
        self.assertIn("$script:compactLayout = $null", wpf_source)

        for event_binding in (
            "$rescanButton.Add_Click",
            "$mcpBackendCombo.Add_SelectionChanged",
            "$houdiniCombo.Add_SelectionChanged",
            "$bridgeCombo.Add_SelectionChanged",
            "$renderOutputTextBox.Add_TextChanged",
            "$browseHoudiniButton.Add_Click",
            "$browseBridgeButton.Add_Click",
            "$browseRenderOutputButton.Add_Click",
            "$repairButton.Add_Click",
            "$cleanupScreenshotsButton.Add_Click",
            "$copyReportButton.Add_Click",
            "$launchButton.Add_Click",
            "$window.Add_SizeChanged",
            "$window.Add_ContentRendered",
        ):
            self.assertEqual(1, wpf_source.count(event_binding), event_binding)

    def test_builtin_visual_and_recovery_ui_are_nonblocking_and_explicit(self) -> None:
        tree = ET.parse(XAML_PATH)
        root = tree.getroot()
        xaml_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
        named = {
            element.attrib.get(xaml_name): element
            for element in root.iter()
            if xaml_name in element.attrib
        }
        self.assertNotIn("OptionalArtworkPanel", named)
        self.assertNotIn("OptionalArtworkImage", named)
        xaml_source = XAML_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("CREATIVE WORKSPACE", xaml_source)
        self.assertNotIn("STEAM WINTER", xaml_source.upper())
        self.assertEqual("Collapsed", named["RecoveryCard"].attrib["Visibility"])
        self.assertEqual("True", named["RecoverCheckpointOption"].attrib["IsChecked"])
        self.assertNotIn("IsChecked", named["NormalLaunchOption"].attrib)
        self.assertEqual(
            named["RecoverCheckpointOption"].attrib["GroupName"],
            named["NormalLaunchOption"].attrib["GroupName"],
        )

        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        self.assertNotIn("Get-HiaLauncherArtworkPath", wpf_source)
        self.assertNotIn("steam-winter-sale", wpf_source)
        for required in (
            "Get-HiaRecoverableLauncherSession -ProjectRoot $projectRoot",
            "$recoverCheckpointOption.IsChecked = $true",
            "'RecoverySessionId'",
            "'RecoveryDecision'",
            "'RecoveryCheckpoint'",
            "Start-ExistingHoudiniLauncher @launchParameters",
        ):
            self.assertIn(required, wpf_source)
        for name in ("RecoverySessionId", "RecoveryCheckpoint", "RecoveryDecision"):
            self.assertIn(f"[string]${name} = ''", launcher_source)

    def test_recovery_discovery_selects_latest_safe_checkpoint_once(self) -> None:
        fake_root = self.sandbox / "recovery-project"
        sessions_root = fake_root / ".runtime" / "launcher-sessions"
        (fake_root / "scripts").mkdir(parents=True)
        sessions_root.mkdir(parents=True)
        (fake_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (fake_root / "scripts" / "launch-houdini.ps1").write_text(
            "# lifecycle marker\n", encoding="utf-8"
        )

        def write_session(
            session_id: str,
            *,
            state: str,
            checkpoint: str | None = None,
            timestamp: float = 1_700_000_000,
            exit_code: int | None = None,
            process_id: int | None = None,
        ) -> Path:
            session = sessions_root / session_id
            checkpoints = session / "checkpoints"
            checkpoints.mkdir(parents=True)
            manifest = {
                "schema_version": 1,
                "session_id": session_id,
                "state": state,
                "selected_houdini": str(fake_root / "fake" / "houdini.exe"),
                "hip_path": str(fake_root / "scene.hip"),
                "started_at_utc": "2026-07-20T01:00:00.0000000Z",
                "ended_at_utc": "2026-07-20T01:05:00Z" if exit_code is not None else None,
                "process_exit_code": exit_code,
                "houdini_process_id": process_id,
            }
            (session / "session.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            if checkpoint:
                checkpoint_path = checkpoints / checkpoint
                checkpoint_path.write_bytes(b"fake hip checkpoint")
                os.utime(checkpoint_path, (timestamp, timestamp))
                return checkpoint_path
            return checkpoints

        write_session("a" * 32, state="completed", checkpoint="complete.hip", exit_code=0)
        corrupt = sessions_root / ("b" * 32)
        (corrupt / "checkpoints").mkdir(parents=True)
        (corrupt / "session.json").write_text("{not-json", encoding="utf-8")
        (corrupt / "checkpoints" / "corrupt.hip").write_bytes(b"ignored")
        write_session("c" * 32, state="abnormal_exit", exit_code=9)
        write_session(
            "d" * 32,
            state="abnormal_exit",
            checkpoint="older.hip_bak1",
            timestamp=1_700_000_100,
            exit_code=7,
        )
        expected = write_session(
            "e" * 32,
            state="launch_failed",
            checkpoint="newest-recoverable.hiplc",
            timestamp=1_700_000_200,
        )
        write_session(
            "f" * 32,
            state="running",
            checkpoint="active-process.hipnc",
            timestamp=1_700_000_300,
            process_id=os.getpid(),
        )
        output = self.run_powershell(
            f"""
$items = @(Get-HiaRecoverableLauncherSession -ProjectRoot {_ps_literal(fake_root)})
[pscustomobject]@{{
    count = $items.Count
    session_id = if ($items.Count) {{ $items[0].session_id }} else {{ '' }}
    checkpoint_path = if ($items.Count) {{ $items[0].checkpoint_path }} else {{ '' }}
}} | ConvertTo-Json -Compress
"""
        )
        candidate = json.loads(output)
        self.assertEqual(1, candidate["count"])
        self.assertEqual("e" * 32, candidate["session_id"])
        self.assertEqual(str(expected), candidate["checkpoint_path"])
        session = sessions_root / ("e" * 32)
        manifest_path = session / "session.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(api_key="must-be-removed", unexpected="must-be-removed")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self.run_powershell(
            f"""
Set-HiaLauncherRecoveryDecision `
    -ProjectRoot {_ps_literal(fake_root)} `
    -SessionId '{'e' * 32}' `
    -Decision recover | Out-Null
$manifest = [System.IO.File]::ReadAllText({_ps_literal(manifest_path)}) | ConvertFrom-Json
[pscustomobject]@{{
    names = @($manifest.PSObject.Properties.Name)
    decision = $manifest.recovery_decision
    remaining = @(Get-HiaRecoverableLauncherSession -ProjectRoot {_ps_literal(fake_root)}).Count
}} | ConvertTo-Json -Depth 4 -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual("recover", result["decision"])
        self.assertEqual(1, result["remaining"])
        for forbidden in ("api_key", "unexpected"):
            self.assertNotIn(forbidden, result["names"])

    def test_current_session_crash_hip_is_pid_bound_and_copied_read_only(self) -> None:
        session = (
            self.sandbox
            / "portable-project"
            / ".runtime"
            / "launcher-sessions"
            / ("d" * 32)
        )
        temporary = session / "tmp"
        checkpoints = session / "checkpoints"
        temporary.mkdir(parents=True)
        checkpoints.mkdir()
        expected = temporary / "crash.asset.Developer_4321.hip"
        expected.write_bytes(b"crash hip remains unchanged")
        (temporary / "crash.asset.Developer_9999.hip").write_bytes(b"wrong pid")
        (temporary / "crash.asset.Developer_4321_log.txt").write_bytes(b"log")
        (temporary / "crash.empty.Developer_4321.hip").write_bytes(b"")
        nested = temporary / "nested"
        nested.mkdir()
        (nested / "crash.nested.Developer_4321.hip").write_bytes(b"nested")

        output = self.run_powershell(
            f"""
$file = Get-Item -LiteralPath {_ps_literal(expected)} -Force
$candidate = Get-HiaLatestLauncherCrashHip `
    -TempDirectory {_ps_literal(temporary)} `
    -HoudiniProcessId 4321 `
    -StartedAtUtcTicks ($file.LastWriteTimeUtc.Ticks - 1) `
    -EndedAtUtcTicks ($file.LastWriteTimeUtc.Ticks + 1)
$copy = Copy-HiaLauncherRecoveryHip `
    -SessionRoot {_ps_literal(session)} `
    -SourcePath $candidate.path `
    -Attempt 2
[pscustomobject]@{{
    candidate = $candidate.path
    copied = $copy.path
    source = $copy.source_path
}} | ConvertTo-Json -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual(str(expected), result["candidate"])
        self.assertEqual(str(expected), result["source"])
        copied = Path(result["copied"])
        self.assertEqual(session / "recovery", copied.parent)
        self.assertEqual(expected.read_bytes(), copied.read_bytes())
        self.assertEqual(b"crash hip remains unchanged", expected.read_bytes())

    def test_ai_checkpoint_sidecar_requires_the_exact_thread(self) -> None:
        goal_binding = "b" * 64
        checkpoints = (
            self.sandbox
            / "portable-project"
            / ".runtime"
            / "launcher-sessions"
            / ("c" * 32)
            / "checkpoints"
        )
        checkpoints.mkdir(parents=True)
        checkpoint = checkpoints / "stage-1.hip"
        checkpoint.write_bytes(b"stage checkpoint")
        (checkpoints / ".hia-stage-checkpoint.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "thread_id": "thread-exact",
                    "goal_binding": goal_binding,
                    "checkpoint_file": checkpoint.name,
                }
            ),
            encoding="utf-8",
        )

        output = self.run_powershell(
            f"""
$matching = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(checkpoints)} `
    -ThreadId 'thread-exact' `
    -GoalBinding '{goal_binding}'
$foreign = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(checkpoints)} `
    -ThreadId 'thread-other' `
    -GoalBinding '{goal_binding}'
$staleGoal = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(checkpoints)} `
    -ThreadId 'thread-exact' `
    -GoalBinding '{"c" * 64}'
[pscustomobject]@{{
    matching_path = $matching.path
    matching_thread = $matching.thread_id
    matching_goal = $matching.goal_binding
    foreign_missing = $null -eq $foreign
    stale_goal_missing = $null -eq $staleGoal
}} | ConvertTo-Json -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual(str(checkpoint), result["matching_path"])
        self.assertEqual("thread-exact", result["matching_thread"])
        self.assertEqual(goal_binding, result["matching_goal"])
        self.assertTrue(result["foreign_missing"])
        self.assertTrue(result["stale_goal_missing"])

        checkpoint.write_bytes(b"")
        output = self.run_powershell(
            f"""
$candidate = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(checkpoints)} `
    -ThreadId 'thread-exact' `
    -GoalBinding '{goal_binding}'
$null -eq $candidate
"""
        )
        self.assertEqual("True", output)

    def test_crash_recovery_policy_is_bounded_and_requires_focus_and_idle(self) -> None:
        output = self.run_powershell(
            """
$normal = Get-HiaCrashRecoveryDecision -ExitCode 0 -FocusVerified $false -ThreadIdle $false
$off = Get-HiaCrashRecoveryDecision -ExitCode 9 -FocusVerified $false -ThreadIdle $true
$busy = Get-HiaCrashRecoveryDecision -ExitCode 9 -FocusVerified $true -ThreadIdle $false
$recoveries = 0
$stops = 0
$lastReason = ''
foreach ($count in 1..4) {
    $decision = Get-HiaCrashRecoveryDecision `
        -ExitCode 9 `
        -FocusVerified $true `
        -ThreadIdle $true `
        -ConsecutiveCrashCount $count `
        -AutomaticRestartCount ([Math]::Min($count - 1, 3))
    if ($decision.recover) { $recoveries += 1 } else { $stops += 1 }
    $lastReason = $decision.reason
}
[pscustomobject]@{
    normal = $normal.reason
    off = $off.reason
    busy = $busy.reason
    recoveries = $recoveries
    stops = $stops
    final = $lastReason
} | ConvertTo-Json -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual("normal_exit", result["normal"])
        self.assertEqual("focus_not_verified", result["off"])
        self.assertEqual("thread_not_idle", result["busy"])
        self.assertEqual(3, result["recoveries"])
        self.assertEqual(1, result["stops"])
        self.assertEqual("bounded_limit", result["final"])

    def test_lifecycle_focus_gate_and_bounded_crash_recovery_are_explicit(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        self.assertIn("$exitDecision = Get-HiaCrashRecoveryDecision", source)
        self.assertIn("$maxConsecutiveCrashes = 3", source)
        self.assertIn("$maxAutomaticRestarts = 6", source)
        self.assertIn("$session.focus_mode -ne $true", source)
        self.assertIn("[string]$goal.status -ne 'active'", source)
        self.assertIn("-Path '/v1/interrupt'", source)
        self.assertIn("-Path '/v1/turn'", source)
        self.assertIn("Wait-FocusedThreadIdle", source)
        self.assertIn("Wait-FocusedRecoveryReady", source)
        self.assertIn("Test-RecoveryHipWithHython", source)
        self.assertIn("did not reset the crash counter", source)
        self.assertLess(
            source.index("$idleContext = Wait-FocusedThreadIdle"),
            source.index("$progressCopy = Copy-HiaLauncherRecoveryHip"),
        )
        probe = source.index("$progressCopy = Copy-HiaLauncherRecoveryHip")
        self.assertLess(
            source.index("Test-RecoveryHipWithHython", probe),
            source.index("$consecutiveCrashCount = 0", probe),
        )
        self.assertIn("-ThreadId $recoveryThreadId", source)
        self.assertIn("-GoalBinding $recoveryGoalBinding", source)
        self.assertIn("-ExpectedGoalBinding $recoveryGoalBinding", source)
        self.assertIn("Do not replay the old write or its arguments", source)
        self.assertIn("$attemptedRecoveryPrompts.Add", source)
        self.assertNotIn("Stop-Process", source)

    def test_crash_recovery_marker_is_scoped_to_one_pending_houdini_child(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        marker_names = (
            "HIA_CRASH_RECOVERY_THREAD_ID",
            "HIA_CRASH_RECOVERY_GOAL_BINDING",
            "HIA_CRASH_RECOVERY_PROMPT_ID",
        )
        base_environment = source[
            source.index("$houdiniEnvironment = @{") : source.index(
                "$stableCheckpoint = $null"
            )
        ]
        child_setup = source[
            source.index("$houdiniInfo = [System.Diagnostics.ProcessStartInfo]::new()") : source.index(
                "$houdiniProcess = [System.Diagnostics.Process]::new()"
            )
        ]

        for name in marker_names:
            self.assertNotIn(name, base_environment)
            self.assertIn(f"'{name}'", child_setup)
            self.assertEqual(2, source.count(f"'{name}'"))
        self.assertIn("if ($null -ne $pendingRecovery)", child_setup)
        self.assertIn(
            "'HIA_CRASH_RECOVERY_THREAD_ID' = [string]$pendingRecovery.thread_id",
            child_setup,
        )
        self.assertIn(
            "'HIA_CRASH_RECOVERY_GOAL_BINDING' = [string]$pendingRecovery.goal_binding",
            child_setup,
        )
        self.assertIn(
            "'HIA_CRASH_RECOVERY_PROMPT_ID' = [string]$pendingRecovery.prompt_id",
            child_setup,
        )
        self.assertLess(
            child_setup.index("Remove-ChildEnvironment"),
            child_setup.index("if ($null -ne $pendingRecovery)"),
        )

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
        self.assertEqual("BigChickenLauncher", properties["AssemblyName"])
        self.assertEqual("Big-Chicken Launcher", properties["Title"])
        self.assertEqual("Big-Chicken Launcher", properties["AssemblyTitle"])
        self.assertEqual("Big-Chicken Houdini Intelligence Agent", properties["Product"])
        self.assertEqual("Big-Chicken", properties["Company"])
        self.assertEqual("0.1.0-preview", properties["Version"])
        self.assertEqual("win-x64", properties["RuntimeIdentifier"])
        self.assertEqual("true", properties["SelfContained"])
        self.assertEqual("true", properties["PublishSingleFile"])
        self.assertEqual("false", properties["IncludeNativeLibrariesForSelfExtract"])
        self.assertEqual("false", properties["PublishTrimmed"])
        self.assertEqual([], project.findall(".//PackageReference"))

        self.assertEqual([], project.findall(".//Content"))

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
        self.assertNotIn(
            "steam-winter-sale",
            (EXE_PROJECT_PATH.read_text(encoding="utf-8") + WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")).lower(),
        )

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
            "CleanupScreenshotsButton",
            "CopyReportButton",
            "LaunchButton",
            "RenderOutputTextBox",
            "BrowseRenderOutputButton",
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
        self.assertIn("[AllowEmptyString()][string]$RenderOutputDir = ''", launcher_source)
        self.assertIn("'HIA_RENDER_OUTPUT_DIR'", launcher_source)
        self.assertNotIn("'-RenderOutputDir'", launcher_source)
        self.assertNotIn("$env:HIA_RENDER_OUTPUT_DIR =", combined)
        self.assertIn("Invoke-HiaScreenshotCacheCleanup", wpf_source)
        self.assertIn("Start-HiaCodexBootstrap", wpf_source)
        self.assertIn("[System.Diagnostics.ProcessStartInfo]::new()", wpf_source)
        self.assertIn("[System.Windows.Threading.DispatcherTimer]::new()", wpf_source)
        self.assertIn("Get-HiaCodexLoginCommand", wpf_source)
        self.assertIn("scripts\\bootstrap-runtime.ps1", wpf_source)
        self.assertNotIn("Invoke-WebRequest", wpf_source)
        self.assertIn("[System.Windows.MessageBoxButton]::YesNo", wpf_source)
        self.assertIn("[System.Windows.MessageBoxResult]::No", wpf_source)

    def test_lifecycle_uses_one_portable_project_cache_for_both_children(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("Join-Path $ResolvedRoot '.runtime\\cache'", source)
        for child in ("screenshots", "previews", "tmp"):
            self.assertIn(f"Join-Path $cacheRoot '{child}'", source)
        self.assertEqual(2, source.count("'HIA_CACHE_DIR' = $cacheRoot"))
        self.assertEqual(2, source.count("'HIA_RENDER_OUTPUT_DIR' = $renderOutputRoot"))
        self.assertIn("-Path ([string]$env:HIA_RENDER_OUTPUT_DIR)", source)
        self.assertIn("$renderOutputRoot = Resolve-HiaRenderOutputDirectory", source)
        self.assertNotIn("$renderOutputRoot = $cacheRoot", source)
        self.assertIn("'TEMP' = $sessionTemp", source)
        self.assertIn("'TMP' = $sessionTemp", source)
        self.assertIn("'HOUDINI_TEMP_DIR' = $sessionTemp", source)
        self.assertNotIn(r"E:\houdini-intelligence-agent", source)
        self.assertIn(".runtime/", gitignore.splitlines())

    def test_lifecycle_sessions_are_portable_redacted_and_recover_by_copy(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        for required in (
            'Join-Path $ResolvedRoot ".runtime\\launcher-sessions\\$sessionId"',
            "Join-Path $sessionRoot 'tmp'",
            "Join-Path $sessionRoot 'checkpoints'",
            "Join-Path $sessionRoot 'session.json'",
            "selected_houdini = $HoudiniExe",
            "hip_path = $knownHipPath",
            "started_at_utc = [DateTime]::UtcNow.ToString('o')",
            "ended_at_utc = $null",
            "process_exit_code = $null",
            "latest_checkpoint = $knownHipPath",
            "Write-LauncherSessionManifest -ManifestPath $sessionManifest",
            "Get-HiaLatestLauncherCheckpoint -CheckpointDirectory $sessionCheckpoints",
        ):
            self.assertIn(required, source)

        writer = source[
            source.index("function Write-LauncherSessionManifest") :
            source.index("function Get-HoudiniCandidatePaths")
        ]
        self.assertIn("ConvertTo-HiaRedactedJson", writer)
        for forbidden in (
            "HIA_BRIDGE_TOKEN",
            "HIA_SCENE_EXECUTOR_TOKEN",
            "FXHOUDINIMCP_TOKEN",
            "HIA_MCP_V2_TOKEN",
        ):
            self.assertNotIn(forbidden, writer)

        self.assertEqual(1, source.count("'HOUDINI_BACKUP_DIR' = $sessionCheckpoints"))
        bridge_environment = source[
            source.index("$bridgeEnvironment = @{") :
            source.index("$bridgeProcess = [System.Diagnostics.Process]::new()")
        ]
        houdini_environment = source[
            source.index("$houdiniEnvironment = @{") :
            source.index(
                "foreach ($entry in $houdiniBackendEnvironment.GetEnumerator())"
            )
        ]
        self.assertNotIn("HOUDINI_BACKUP_DIR", bridge_environment)
        self.assertIn("'HOUDINI_BACKUP_DIR' = $sessionCheckpoints", houdini_environment)

        self.assertIn("[AllowEmptyString()][string]$RecoverySessionId = ''", source)
        self.assertIn("[AllowEmptyString()][string]$RecoveryCheckpoint = ''", source)
        self.assertIn("[AllowEmptyString()][string]$RecoveryDecision = ''", source)
        self.assertIn("$sourceFile -isnot [System.IO.FileInfo]", source)
        self.assertIn("$sourceParent", source)
        self.assertIn("$sourceSessionCheckpoints", source)
        self.assertIn(
            "[System.IO.File]::Copy($recoverySourceCheckpoint, $knownHipPath, $false)",
            source,
        )
        self.assertNotIn("Copy-Item", source)
        self.assertNotIn("Move-Item", source)

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
