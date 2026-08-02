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
EMBEDDING_CONTRACT_PATH = REPOSITORY_ROOT / "src" / "hia_core" / "embedding_contract.py"
EMBEDDING_INSTALLER_PATH = (
    REPOSITORY_ROOT / "scripts" / "launcher" / "Install-HiaEmbedding.ps1"
)
EMBEDDING_INSTALLER_HELPER_PATH = (
    REPOSITORY_ROOT / "scripts" / "launcher" / "install_hia_embedding.py"
)
EXE_PROJECT_ROOT = REPOSITORY_ROOT / "launcher" / "HoudiniIntelligenceLauncher"
EXE_PROJECT_PATH = EXE_PROJECT_ROOT / "HoudiniIntelligenceLauncher.csproj"
EXE_APP_PATH = EXE_PROJECT_ROOT / "App.xaml.cs"
EXE_ROOT_LOCATOR_PATH = EXE_PROJECT_ROOT / "ProjectRootLocator.cs"
EXE_BUILD_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "build-launcher.ps1"
UI_READY_PATHS = (
    REPOSITORY_ROOT / "houdini_package" / "python3.10libs" / "uiready.py",
    REPOSITORY_ROOT / "houdini_package" / "python3.11libs" / "uiready.py",
    REPOSITORY_ROOT / "houdini_package" / "python3.13libs" / "uiready.py",
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

    def test_process_argument_converters_preserve_empty_string(self) -> None:
        probe = self.sandbox / "empty argument probe.ps1"
        probe.write_text(
            """
param([AllowEmptyString()][string]$Profile = 'missing')
if ($Profile.Length -eq 0) { 'EMPTY' } else { "VALUE:$Profile" }
""".strip(),
            encoding="utf-8-sig",
        )
        wrapper = REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(wrapper)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'hia-knowledge.ps1 did not parse.' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'ConvertTo-HiaKnowledgeProcessArgument'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'wrapper converter is unavailable.' }}
. ([scriptblock]::Create($definition[0].Extent.Text))

function Invoke-EmptyArgumentProbe([string]$Encoded) {{
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Get-Process -Id $PID).Path
    $info.Arguments = (
        '-NoProfile -ExecutionPolicy Bypass -File ' +
        (ConvertTo-HiaProcessArgument -Value {_ps_literal(probe)}) +
        ' -Profile ' + $Encoded
    )
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) {{ throw 'probe did not start.' }}
    $stdout = $process.StandardOutput.ReadToEnd().Trim()
    $stderr = $process.StandardError.ReadToEnd().Trim()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) {{ throw "probe failed: $stderr" }}
    return $stdout
}}

$core = ConvertTo-HiaProcessArgument -Value ''
$wrapperValue = ConvertTo-HiaKnowledgeProcessArgument -Value ''
[pscustomobject]@{{
    core_encoded = $core
    wrapper_encoded = $wrapperValue
    core_received = Invoke-EmptyArgumentProbe -Encoded $core
    wrapper_received = Invoke-EmptyArgumentProbe -Encoded $wrapperValue
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual('""', payload["core_encoded"])
        self.assertEqual('""', payload["wrapper_encoded"])
        self.assertEqual("EMPTY", payload["core_received"])
        self.assertEqual("EMPTY", payload["wrapper_received"])

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

    def test_process_grace_waits_for_the_same_process_and_reports_elapsed_time(
        self,
    ) -> None:
        probe = self.sandbox / "slow-probe.ps1"
        probe.write_text(
            "Start-Sleep -Milliseconds 1400\nWrite-Output 'READY'\n",
            encoding="utf-8-sig",
        )
        output = self.run_powershell(
            f"""
$result = Invoke-HiaProcess `
    -FilePath (Get-Process -Id $PID).Path `
    -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', {_ps_literal(probe)}) `
    -TimeoutSeconds 1 `
    -GraceTimeoutSeconds 2
$result | ConvertTo-Json -Compress
""",
            timeout=10,
        )
        result = json.loads(output)
        self.assertTrue(result["started"])
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["completed_after_grace"])
        self.assertEqual(0, result["exit_code"])
        self.assertEqual("READY", result["stdout"].strip())
        self.assertGreaterEqual(result["elapsed_ms"], 1000)
        self.assertLess(result["elapsed_ms"], 3500)

    def test_process_exceeding_primary_and_grace_windows_stays_timed_out(
        self,
    ) -> None:
        probe = self.sandbox / "timeout-probe.ps1"
        probe.write_text("Start-Sleep -Seconds 5\n", encoding="utf-8-sig")
        output = self.run_powershell(
            f"""
$result = Invoke-HiaProcess `
    -FilePath (Get-Process -Id $PID).Path `
    -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', {_ps_literal(probe)}) `
    -TimeoutSeconds 1 `
    -GraceTimeoutSeconds 1
$result | ConvertTo-Json -Compress
""",
            timeout=10,
        )
        result = json.loads(output)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["completed_after_grace"])
        self.assertGreaterEqual(result["elapsed_ms"], 1800)
        self.assertLess(result["elapsed_ms"], 3500)

    def test_process_can_remove_pythonpath_from_child_without_changing_parent(
        self,
    ) -> None:
        probe = self.sandbox / "environment-probe.ps1"
        probe.write_text(
            """
$value = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
if ($null -eq $value) { 'REMOVED' } else { "VALUE:$value" }
""".strip(),
            encoding="utf-8-sig",
        )
        output = self.run_powershell(
            f"""
$original = $env:PYTHONPATH
try {{
    $env:PYTHONPATH = 'external-probe-path'
    $result = Invoke-HiaProcess `
        -FilePath (Get-Process -Id $PID).Path `
        -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', {_ps_literal(probe)}) `
        -RemoveEnvironmentVariables @('PYTHONPATH')
    [pscustomobject]@{{
        child = [string]$result.stdout.Trim()
        parent = [string]$env:PYTHONPATH
    }} | ConvertTo-Json -Compress
}} finally {{
    $env:PYTHONPATH = $original
}}
"""
        )
        result = json.loads(output)
        self.assertEqual("REMOVED", result["child"])
        self.assertEqual("external-probe-path", result["parent"])

    def test_hython_cold_start_completed_in_grace_is_a_timed_yellow_warning(
        self,
    ) -> None:
        houdini = self.make_houdini_install("Houdini 21.0.440")
        payload = {
            "build": "21.0.440",
            "python": "3.11",
            "executable": str(houdini.with_name("hython.exe")),
            "hou_import": True,
        }
        marker_output = "__HIA_LAUNCHER_PROBE__" + json.dumps(payload)
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput 'Houdini 21.0.440' `
    -HythonOutput {_ps_literal(marker_output)} `
    -HythonCompletedAfterGrace `
    -HythonElapsedMilliseconds 13500)
$checks | Where-Object id -eq 'houdini.hython_probe' | ConvertTo-Json -Compress
"""
        )
        check = json.loads(output)
        self.assertEqual("yellow", check["level"])
        self.assertIn("13.5 秒", check["message"])
        self.assertIn("import hou 已验证", check["message"])
        self.assertIn("无需修复", check["advice"])

    def test_hython_final_timeout_remains_red_without_claiming_install_damage(
        self,
    ) -> None:
        houdini = self.make_houdini_install("Houdini 21.0.440")
        output = self.run_powershell(
            f"""
$checks = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput 'Houdini 21.0.440' `
    -HoudiniExitCode 0 `
    -HythonOutput '' `
    -HythonExitCode -1 `
    -HythonTimedOut `
    -HythonElapsedMilliseconds 24012)
$checks | Where-Object id -eq 'houdini.hython_probe' | ConvertTo-Json -Compress
"""
        )
        check = json.loads(output)
        self.assertEqual("red", check["level"])
        self.assertIn("24.0 秒", check["message"])
        self.assertIn("Houdini build 21.0.440 可读取", check["message"])
        self.assertIn("尚未确认 import hou", check["message"])
        self.assertIn("重负载结束后重新扫描", check["advice"])
        self.assertNotIn("修复所选 Houdini 安装", check["advice"])

    def test_hython_nonzero_or_missing_marker_remains_red(self) -> None:
        houdini = self.make_houdini_install("Houdini 21.0.440")
        output = self.run_powershell(
            f"""
$nonzero = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput 'Houdini 21.0.440' `
    -HythonOutput 'probe failed' `
    -HythonExitCode 7) | Where-Object id -eq 'houdini.hython_probe'
$missingMarker = @(Test-HiaHoudiniProbeConsistency `
    -HoudiniExe {_ps_literal(houdini)} `
    -HoudiniOutput 'Houdini 21.0.440' `
    -HythonOutput '{{"hou_import":true}}' `
    -HythonExitCode 0) | Where-Object id -eq 'houdini.hython_probe'
@($nonzero, $missingMarker) | ConvertTo-Json -Compress
"""
        )
        checks = json.loads(output)
        self.assertEqual(["red", "red"], [check["level"] for check in checks])
        self.assertIn("退出码为 7", checks[0]["message"])
        self.assertIn("未返回有效探针数据", checks[1]["message"])

    def test_hython_probe_alone_gets_bounded_grace_and_clean_pythonpath(
        self,
    ) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8-sig")
        function_source = source[
            source.index("function Invoke-HiaHoudiniChecks"):
            source.index("function Get-HiaPinnedCodexExecutable")
        ]
        self.assertIn("-GraceTimeoutSeconds $hythonGraceSeconds", function_source)
        self.assertIn(
            "-RemoveEnvironmentVariables @('PYTHONPATH')",
            function_source,
        )
        self.assertIn(",flush=True)", function_source)
        self.assertIn(
            "[Math]::Min($TimeoutSeconds, 30 - $TimeoutSeconds)",
            function_source,
        )
        self.assertEqual(1, function_source.count("-GraceTimeoutSeconds"))
        self.assertEqual(1, function_source.count("-FilePath $hythonExe"))
        self.assertLess(
            function_source.index("Invoke-HiaProcess -FilePath $HoudiniExe"),
            function_source.index("-GraceTimeoutSeconds $hythonGraceSeconds"),
        )

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

    def test_hia_preflight_requires_every_supported_ui_ready_hook(self) -> None:
        fake_root = self.sandbox / "versioned-ui-ready-project"
        required_files = (
            fake_root / "services" / "hia_mcp_v2" / "hia_mcp_v2" / "__main__.py",
            fake_root
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "http_server.py",
            *(fake_root / path.relative_to(REPOSITORY_ROOT) for path in UI_READY_PATHS),
        )
        for path in required_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test fixture\n", encoding="utf-8")

        def hia_level() -> str:
            output = self.run_powershell(
                f"""
$result = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
    -BridgePython {_ps_literal(fake_root / 'missing' / 'python.exe')} `
    -ProbeOverrides @{{ runtime_writable = $true; loopback = $true }}
($result.checks | Where-Object id -eq 'hia_mcp_v2.runtime').level
"""
            )
            return output.strip()

        self.assertEqual("green", hia_level())
        (fake_root / "houdini_package" / "python3.13libs" / "uiready.py").unlink()
        self.assertEqual("red", hia_level())

    def test_explicit_advanced_bridge_override_accepts_python_org_install(
        self,
    ) -> None:
        fake_root = self.sandbox / "bridge-project"
        fake_root.mkdir()
        bridge = (
            fake_root
            / "Users"
            / "TestUser"
            / "AppData"
            / "Local"
            / "Programs"
            / "Python"
            / "Python311"
            / "python.exe"
        )
        bridge.parent.mkdir(parents=True)
        bridge.write_bytes(b"fake-python")
        marker = "__HIA_LAUNCHER_PROBE__" + json.dumps(
            {
                "python": "3.11",
                "executable": str(bridge),
                "imports": ["hia_bridge", "hia_core", "hia_mcp_v2"],
            },
            separators=(",", ":"),
        )
        output = self.run_powershell(
            f"""
$bridgeProbe = [pscustomobject]@{{
    started = $true
    timed_out = $false
    exit_code = 0
    stdout = {_ps_literal(marker)}
    stderr = ''
    error = ''
}}
$placeholderCandidate = [pscustomobject]@{{
    path = {_ps_literal(fake_root / 'missing' / 'houdini.exe')}
    version = ''
    display = ''
    exists = $false
    sources = @()
}}
$result = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
    -BridgePython {_ps_literal(bridge)} `
    -Candidates @($placeholderCandidate) `
    -ProbeOverrides @{{ runtime_writable = $true; loopback = $true; bridge = $bridgeProbe }}
$result.checks | Where-Object id -eq 'bridge.python' | ConvertTo-Json -Compress
"""
        )
        check = json.loads(output)
        self.assertEqual("green", check["level"])

    def test_bridge_python_rejects_unsafe_paths_and_failed_probe(self) -> None:
        fake_root = self.sandbox / "bridge-policy-project"
        fake_root.mkdir()
        windows_apps = (
            fake_root
            / "Users"
            / "TestUser"
            / "AppData"
            / "Local"
            / "Microsoft"
            / "WindowsApps"
            / "python.exe"
        )
        ordinary_appdata = (
            fake_root
            / "Users"
            / "TestUser"
            / "AppData"
            / "Local"
            / "Temp"
            / "python.exe"
        )
        normal_python = fake_root / "toolchain" / "python.exe"
        for path in (windows_apps, ordinary_appdata, normal_python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-python")

        unsafe_paths = (
            windows_apps,
            ordinary_appdata,
            Path(r"\\server\share\python.exe"),
            Path(r"relative\python.exe"),
            Path(str(normal_python) + ":stream"),
            fake_root / "missing" / "python.exe",
        )
        successful_probe = "__HIA_LAUNCHER_PROBE__" + json.dumps(
            {"python": "3.11", "executable": str(normal_python)},
            separators=(",", ":"),
        )
        powershell_paths = ", ".join(_ps_literal(path) for path in unsafe_paths)
        output = self.run_powershell(
            f"""
$bridgeProbe = [pscustomobject]@{{
    started = $true
    timed_out = $false
    exit_code = 0
    stdout = {_ps_literal(successful_probe)}
    stderr = ''
    error = ''
}}
$placeholderCandidate = [pscustomobject]@{{
    path = {_ps_literal(fake_root / 'missing' / 'houdini.exe')}
    version = ''
    display = ''
    exists = $false
    sources = @()
}}
$levels = @()
foreach ($candidate in @({powershell_paths})) {{
    $result = Invoke-HiaPreflight `
        -ProjectRoot {_ps_literal(fake_root)} `
        -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
        -BridgePython $candidate `
        -Candidates @($placeholderCandidate) `
        -ProbeOverrides @{{ runtime_writable = $true; loopback = $true; bridge = $bridgeProbe }}
    $check = @($result.checks | Where-Object id -eq 'bridge.python')[0]
    $levels += $check.level
}}
$failedProbe = [pscustomobject]@{{
    started = $true
    timed_out = $false
    exit_code = 1
    stdout = ''
    stderr = 'probe failed'
    error = ''
}}
$failedResult = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -HoudiniExe {_ps_literal(fake_root / 'missing' / 'houdini.exe')} `
    -BridgePython {_ps_literal(normal_python)} `
    -Candidates @($placeholderCandidate) `
    -ProbeOverrides @{{ runtime_writable = $true; loopback = $true; bridge = $failedProbe }}
$failedCheck = @($failedResult.checks | Where-Object id -eq 'bridge.python')[0]
[pscustomobject]@{{ unsafe = $levels; failed_probe = $failedCheck.level }} |
    ConvertTo-Json -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual(["red"] * len(unsafe_paths), result["unsafe"])
        self.assertEqual("red", result["failed_probe"])

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
    UV_INDEX_URL = 'https://private-index.example/simple'
    HTTPS_PROXY = 'https://proxy-user:proxy-pass@proxy.example:8443/path'
    note = @'
Authorization: Basic dXNlcjpwYXNz
Authorization: Bearer bearer-value-must-not-survive
https://url-user:url-password@example.test/simple
https://token-only-core@example.test/simple
PIP_EXTRA_INDEX_URL=https://pip-secret.example/simple
UV_INDEX=https://first-index.example/simple https://second-index.example/simple
HTTP_PROXY=http://plain-proxy.example:8080
NO_PROXY=localhost,internal.example
next-line-visible
'@
}
ConvertTo-HiaRedactedJson -Value $value -Compress
"""
        )
        payload = json.loads(output)
        self.assertNotIn("token-value-must-not-survive", output)
        self.assertNotIn("cookie-value-must-not-survive", output)
        self.assertNotIn("sk-abcdefghijklmnop", output)
        self.assertNotIn("bearer-value-must-not-survive", output)
        self.assertNotIn("dXNlcjpwYXNz", output)
        self.assertNotIn("url-user", output)
        self.assertNotIn("url-password", output)
        self.assertNotIn("token-only-core", output)
        self.assertIn("https://[REDACTED]@example.test/simple", output)
        self.assertNotIn("private-index.example", output)
        self.assertNotIn("pip-secret.example", output)
        self.assertNotIn("first-index.example", output)
        self.assertNotIn("second-index.example", output)
        self.assertNotIn("proxy.example", output)
        self.assertNotIn("plain-proxy.example", output)
        self.assertNotIn("internal.example", output)
        self.assertIn("next-line-visible", payload["note"])
        self.assertIn("[REDACTED]", output)

    def test_core_text_redacts_token_only_url_userinfo(self) -> None:
        output = self.run_powershell(
            """
ConvertTo-HiaRedactedText `
    -Text 'https://token-only@example.test/simple'
"""
        )
        self.assertEqual(
            "https://[REDACTED]@example.test/simple",
            output,
        )

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

    def test_launcher_settings_and_install_logs_reject_reparse_parents(
        self,
    ) -> None:
        for level in ("runtime", "launcher"):
            with self.subTest(level=level):
                case_root = self.sandbox / f"launcher-storage-{level}"
                project = case_root / "project"
                lure = case_root / "lure"
                project.mkdir(parents=True)
                lure.mkdir()
                marker = lure / "keep.txt"
                marker.write_text("keep", encoding="utf-8")
                if level == "runtime":
                    link = project / ".runtime"
                    target = lure
                else:
                    (project / ".runtime").mkdir()
                    link = project / ".runtime" / "launcher"
                    target = lure
                expected_log = (
                    project
                    / ".runtime"
                    / "launcher"
                    / "embedding-install-junction.log"
                )
                output = self.run_powershell(
                    f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(EMBEDDING_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse.' }}
$requiredFunctions = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'Resolve-HiaEmbeddingLauncherDirectory',
    'Resolve-HiaEmbeddingInstallLogPath'
)
foreach ($name in $requiredFunctions) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "missing function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}

$link = {_ps_literal(link)}
$created = $false
try {{
    New-Item -ItemType Junction -Path $link -Target {_ps_literal(target)} | Out-Null
    $created = $true
    $settingsFailure = ''
    try {{
        Write-HiaLauncherSettings `
            -ProjectRoot {_ps_literal(project)} `
            -HoudiniExe {_ps_literal(project / 'Houdini' / 'houdini.exe')} `
            -BridgePython {_ps_literal(project / 'Python' / 'python.exe')} | Out-Null
    }} catch {{
        $settingsFailure = [string]$_.Exception.Message
    }}
    $logFailure = ''
    try {{
        [void](Resolve-HiaEmbeddingInstallLogPath `
            -ProjectRoot {_ps_literal(project)} `
            -RequestedPath {_ps_literal(expected_log)})
    }} catch {{
        $logFailure = [string]$_.Exception.Message
    }}
    [pscustomobject]@{{
        settings_failure = $settingsFailure
        log_failure = $logFailure
        marker = [System.IO.File]::ReadAllText({_ps_literal(marker)})
        lure_settings = Test-Path `
            -LiteralPath (Join-Path {_ps_literal(lure)} 'settings.json') `
            -PathType Leaf
        lure_log = Test-Path `
            -LiteralPath (Join-Path {_ps_literal(lure)} 'embedding-install-junction.log') `
            -PathType Leaf
    }} | ConvertTo-Json -Compress
}} finally {{
    if ($created) {{
        $item = Get-Item -LiteralPath $link -Force -ErrorAction Stop
        if (
            ([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -eq 0
        ) {{
            throw 'Test junction unexpectedly lost its reparse-point attribute.'
        }}
        [System.IO.Directory]::Delete($link, $false)
    }}
}}
"""
                )
                payload = json.loads(output)
                self.assertIn("reparse", payload["settings_failure"].lower())
                self.assertIn("reparse", payload["log_failure"].lower())
                self.assertEqual("keep", payload["marker"])
                self.assertFalse(payload["lure_settings"])
                self.assertFalse(payload["lure_log"])

    def test_launcher_settings_and_install_logs_reject_file_symlinks(
        self,
    ) -> None:
        project = self.sandbox / "launcher-storage-file-link"
        launcher = project / ".runtime" / "launcher"
        launcher.mkdir(parents=True)
        lure_settings = self.sandbox / "outside-settings.json"
        lure_log = self.sandbox / "outside-install.log"
        lure_settings.write_text('{"keep":"settings"}\n', encoding="utf-8")
        lure_log.write_text("keep-log\n", encoding="utf-8")
        settings_link = launcher / "settings.json"
        log_link = launcher / "embedding-install-linked.log"
        try:
            settings_link.symlink_to(lure_settings)
            log_link.symlink_to(lure_log)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest(
                    "Windows file symlink creation requires elevated privilege"
                )
            raise

        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(EMBEDDING_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse.' }}
$requiredFunctions = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'Resolve-HiaEmbeddingLauncherDirectory',
    'Resolve-HiaEmbeddingInstallLogPath'
)
foreach ($name in $requiredFunctions) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "missing function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}

$readFailure = ''
try {{
    [void](Read-HiaLauncherSettings -ProjectRoot {_ps_literal(project)})
}} catch {{
    $readFailure = [string]$_.Exception.Message
}}
$writeFailure = ''
try {{
    Write-HiaLauncherSettings `
        -ProjectRoot {_ps_literal(project)} `
        -HoudiniExe {_ps_literal(project / 'Houdini' / 'houdini.exe')} `
        -BridgePython {_ps_literal(project / 'Python' / 'python.exe')} | Out-Null
}} catch {{
    $writeFailure = [string]$_.Exception.Message
}}
$logFailure = ''
try {{
    [void](Resolve-HiaEmbeddingInstallLogPath `
        -ProjectRoot {_ps_literal(project)} `
        -RequestedPath {_ps_literal(log_link)})
}} catch {{
    $logFailure = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    read_failure = $readFailure
    write_failure = $writeFailure
    log_failure = $logFailure
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertIn("ordinary file", payload["read_failure"])
        self.assertIn("ordinary file", payload["write_failure"])
        self.assertIn("ordinary file", payload["log_failure"])
        self.assertEqual(
            '{"keep":"settings"}\n',
            lure_settings.read_text(encoding="utf-8"),
        )
        self.assertEqual("keep-log\n", lure_log.read_text(encoding="utf-8"))

    def test_embedding_contract_drives_choices_default_sizes_and_portable_setting(self) -> None:
        fake_root = self.sandbox / "embedding-settings"
        fake_root.mkdir()
        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$default = [string]$data.contract.default_profile
$alternate = @($data.profiles.PSObject.Properties.Name | Where-Object {{ $_ -ne $default }})[0]
$choices = @(Get-HiaEmbeddingProfileChoices -EmbeddingData $data)
Write-HiaEmbeddingPreference `
    -ProjectRoot {_ps_literal(fake_root)} `
    -EmbeddingData $data `
    -EmbeddingProfile $alternate `
    -EmbeddingDevice 'cuda' | Out-Null
$settings = Read-HiaLauncherSettings -ProjectRoot {_ps_literal(fake_root)}
$key = [string]$data.contract.settings.profile
$deviceKey = [string]$data.contract.settings.device
[pscustomobject]@{{
    default = $default
    alternate = $alternate
    selected = [string]$settings.PSObject.Properties[$key].Value
    device = [string]$settings.PSObject.Properties[$deviceKey].Value
    profile_count = @($data.profiles.PSObject.Properties).Count
    choices = $choices
    setting_key = $key
}} | ConvertTo-Json -Depth 8 -Compress
"""
        )
        payload = json.loads(output)
        contract = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))
        public = contract["launcher_contract"]()
        self.assertEqual(public["default_profile"], payload["default"])
        self.assertEqual(2, payload["profile_count"])
        self.assertEqual(payload["alternate"], payload["selected"])
        self.assertEqual("cuda", payload["device"])
        self.assertEqual(public["settings"]["profile"], payload["setting_key"])
        self.assertEqual(
            set(public["profiles"]),
            {choice["id"] for choice in payload["choices"]},
        )
        details = " ".join(choice["detail"] for choice in payload["choices"])
        self.assertIn("1.21 GB", details)
        self.assertIn("15.2 GB", details)
        self.assertIn("默认 / 轻量", details)
        self.assertIn("高质量 / 资源占用高", details)
        settings_path = fake_root / ".runtime" / "launcher" / "settings.json"
        self.assertTrue(settings_path.is_file())
        self.assertFalse((self.sandbox / "AppData").exists())

    def test_embedding_device_choices_are_bounded_and_cuda_repair_is_project_local(self) -> None:
        output = self.run_powershell(
            """
[pscustomobject]@{
    empty = Resolve-HiaEmbeddingDevice -Device ''
    auto = Resolve-HiaEmbeddingDevice -Device 'auto'
    cuda = Resolve-HiaEmbeddingDevice -Device 'CUDA'
    cpu = Resolve-HiaEmbeddingDevice -Device 'cpu'
} | ConvertTo-Json -Compress
"""
        )
        self.assertEqual(
            {
                "empty": "auto",
                "auto": "auto",
                "cuda": "cuda",
                "cpu": "cpu",
            },
            json.loads(output),
        )
        installer_source = EMBEDDING_INSTALLER_PATH.read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("[ValidateSet('auto', 'cuda', 'cpu')]", installer_source)
        self.assertIn(
            "https://astral.sh/uv/0.11.29/install.ps1",
            installer_source,
        )
        self.assertIn("UV_UNMANAGED_INSTALL", installer_source)
        self.assertIn("UV_CACHE_DIR", installer_source)
        self.assertIn("--torch-backend=cu128", installer_source)
        self.assertIn("'--python', $workerPython", installer_source)
        self.assertEqual(3, installer_source.count("'--default-index'"))
        self.assertIn("https://pypi.org/simple", installer_source)
        self.assertEqual(
            2,
            installer_source.count("-FilePath ([string]$uv.exe)"),
        )
        self.assertNotIn("https://download.pytorch.org", installer_source)
        self.assertNotIn("--index-url", installer_source)
        self.assertNotIn("'-m',\n            'pip'", installer_source)
        uv_ready = installer_source.index("$uv = Get-HiaProjectLocalUv")
        cuda_torch = installer_source.index(
            "$cudaTorchResult = Invoke-HiaEmbeddingChildProcess",
            uv_ready,
        )
        worker_install = installer_source.index(
            "$workerInstallResult = Invoke-HiaEmbeddingChildProcess",
            cuda_torch,
        )
        self.assertLess(uv_ready, cuda_torch)
        self.assertLess(cuda_torch, worker_install)
        self.assertIn("torch.cuda.is_available()", installer_source)
        self.assertIn("device_name", installer_source)
        self.assertIn("project-local embedding environment", installer_source)
        self.assertNotIn("--user", installer_source)

    def test_embedding_installer_accepts_verified_cuda_without_nvidia_smi(self) -> None:
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(EMBEDDING_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse.' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Resolve-HiaEmbeddingInstallDevice'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'CUDA device decision helper is missing.' }}
Invoke-Expression $definition[0].Extent.Text
$cudaTorch = [pscustomobject]@{{ cuda_available = $true }}
$cpuTorch = [pscustomobject]@{{ cuda_available = $false }}
$failure = ''
try {{
    [void](Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice cuda `
        -TorchProbe $cpuTorch `
        -NvidiaAvailable $false)
}} catch {{
    $failure = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    explicit_cuda = Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice cuda `
        -TorchProbe $cudaTorch `
        -NvidiaAvailable $false
    automatic_cuda = Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice auto `
        -TorchProbe $cudaTorch `
        -NvidiaAvailable $false
    repair_cuda = Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice cuda `
        -TorchProbe $cpuTorch `
        -NvidiaAvailable $true
    automatic_cpu = Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice auto `
        -TorchProbe $cpuTorch `
        -NvidiaAvailable $false
    automatic_hardware_only = Resolve-HiaEmbeddingInstallDevice `
        -RequestedDevice auto `
        -TorchProbe $cpuTorch `
        -NvidiaAvailable $true
    failure = $failure
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("cuda", payload["explicit_cuda"])
        self.assertEqual("cuda", payload["automatic_cuda"])
        self.assertEqual("cuda", payload["repair_cuda"])
        self.assertEqual("cpu", payload["automatic_cpu"])
        self.assertEqual("cpu", payload["automatic_hardware_only"])
        self.assertIn("neither CUDA-enabled PyTorch", payload["failure"])

        installer_source = EMBEDDING_INSTALLER_PATH.read_text(encoding="utf-8-sig")
        decision_call = installer_source.index(
            "$resolvedDevice = Resolve-HiaEmbeddingInstallDevice"
        )
        cuda_install = installer_source.index(
            "$cudaTorchResult = Invoke-HiaEmbeddingChildProcess",
            decision_call,
        )
        self.assertIn(
            "-not $priorTorchCudaAvailable",
            installer_source[decision_call:cuda_install],
        )
        self.assertIn(
            "$resolvedDevice -eq 'cuda' -and -not [bool]$verifiedTorch.cuda_available",
            installer_source,
        )

    def test_embedding_install_uses_project_log_and_gui_surfaces_safe_reason(
        self,
    ) -> None:
        fake_root = self.sandbox / "embedding-install-log"
        fake_root.mkdir()
        expected_log = (
            fake_root
            / ".runtime"
            / "launcher"
            / "embedding-install-test.log"
        )
        outside_log = fake_root / "outside.log"
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(EMBEDDING_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse.' }}
$requiredFunctions = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'ConvertTo-HiaEmbeddingSafeLogText',
    'Resolve-HiaEmbeddingLauncherDirectory',
    'Resolve-HiaEmbeddingInstallLogPath'
)
foreach ($name in $requiredFunctions) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "missing function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}
$inside = Resolve-HiaEmbeddingInstallLogPath `
    -ProjectRoot {_ps_literal(fake_root)} `
    -RequestedPath {_ps_literal(expected_log)}
$outsideFailure = ''
try {{
    [void](Resolve-HiaEmbeddingInstallLogPath `
        -ProjectRoot {_ps_literal(fake_root)} `
        -RequestedPath {_ps_literal(outside_log)})
}} catch {{
    $outsideFailure = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    inside = $inside
    inside_exists = Test-Path -LiteralPath $inside -PathType Leaf
    outside_exists = Test-Path -LiteralPath {_ps_literal(outside_log)}
    outside_failure = $outsideFailure
    safe = ConvertTo-HiaEmbeddingSafeLogText `
        -Text @'
Authorization: Basic dXNlcjpwYXNz
Authorization: Bearer abcdefghi
https://url-user:url-password@example.test/simple
https://token-only-installer@example.test/simple
UV_INDEX_URL=https://uv-secret.example/simple
PIP_EXTRA_INDEX_URL=https://pip-secret.example/simple
UV_INDEX=https://first-index.example/simple https://second-index.example/simple
HTTPS_PROXY=https://proxy-user:proxy-pass@proxy.example:8443/path
NO_PROXY=localhost,internal.example
next-line-visible
sk-abcdefghijk
'@
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(expected_log, Path(payload["inside"]))
        self.assertTrue(payload["inside_exists"])
        self.assertFalse(payload["outside_exists"])
        self.assertIn("project-local launcher log", payload["outside_failure"])
        self.assertNotIn("secret", payload["safe"])
        self.assertNotIn("abcdefghi", payload["safe"])
        self.assertNotIn("dXNlcjpwYXNz", payload["safe"])
        self.assertNotIn("url-user", payload["safe"])
        self.assertNotIn("url-password", payload["safe"])
        self.assertNotIn("token-only-installer", payload["safe"])
        self.assertIn(
            "https://[REDACTED]@example.test/simple",
            payload["safe"],
        )
        self.assertNotIn("uv-secret.example", payload["safe"])
        self.assertNotIn("pip-secret.example", payload["safe"])
        self.assertNotIn("first-index.example", payload["safe"])
        self.assertNotIn("second-index.example", payload["safe"])
        self.assertNotIn("proxy.example", payload["safe"])
        self.assertNotIn("internal.example", payload["safe"])
        self.assertIn("next-line-visible", payload["safe"])
        self.assertIn("[REDACTED]", payload["safe"])

        installer_source = EMBEDDING_INSTALLER_PATH.read_text(
            encoding="utf-8-sig"
        )
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        for required in (
            "[string]$LogPath = ''",
            "Write-HiaEmbeddingInstallLog",
            "[System.IO.FileMode]::CreateNew",
            "ConvertTo-HiaEmbeddingSafeLogText",
        ):
            self.assertIn(required, installer_source)
        for required in (
            "New-HiaEmbeddingInstallLogPath",
            "Get-HiaEmbeddingInstallFailureSummary",
            "'-LogPath', $script:embeddingInstallLogPath",
            "ConvertTo-HiaRedactedJson",
            "这次安装没完成",
            "安装日志",
        ):
            self.assertIn(required, wpf_source)
        self.assertNotIn(
            "请在 PowerShell 中运行 scripts\\launcher\\Install-HiaEmbedding.ps1",
            wpf_source,
        )
        self.assertEqual(1, wpf_source.count("母鸡啄米中"))
        complete = wpf_source[
            wpf_source.index("function Complete-HiaEmbeddingInstall"):
            wpf_source.index("$script:embeddingTimer.Add_Tick")
        ]
        self.assertLess(
            complete.index("Invoke-GuiScan"),
            complete.index("$runtimeReady"),
        )
        self.assertIn(
            "if ($exitCode -eq 0 -and $runtimeReady)",
            complete,
        )
        self.assertIn("$script:preflightFailed", complete)
        self.assertIn(
            "$script:lastReportPath = $validatedInstallLog",
            complete,
        )

    def test_embedding_install_exit_zero_requires_green_runtime_refresh(
        self,
    ) -> None:
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WPF_SCRIPT_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'HiaLauncher.Wpf.ps1 did not parse.' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Test-HiaEmbeddingRuntimeReady'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'runtime verification helper is missing' }}
Invoke-Expression $definition[0].Extent.Text

function New-TestResult([object[]]$Checks) {{
    return [pscustomobject]@{{ checks = $Checks }}
}}
$green = New-TestResult @(
    [pscustomobject]@{{ id = 'embedding.runtime'; level = 'green' }}
)
$yellow = New-TestResult @(
    [pscustomobject]@{{ id = 'embedding.runtime'; level = 'yellow' }}
)
$red = New-TestResult @(
    [pscustomobject]@{{ id = 'embedding.runtime'; level = 'red' }}
)
$missing = New-TestResult @(
    [pscustomobject]@{{ id = 'other'; level = 'green' }}
)
$duplicate = New-TestResult @(
    [pscustomobject]@{{ id = 'embedding.runtime'; level = 'green' }},
    [pscustomobject]@{{ id = 'embedding.runtime'; level = 'green' }}
)
[pscustomobject]@{{
    green = Test-HiaEmbeddingRuntimeReady -Result $green
    yellow = Test-HiaEmbeddingRuntimeReady -Result $yellow
    red = Test-HiaEmbeddingRuntimeReady -Result $red
    missing = Test-HiaEmbeddingRuntimeReady -Result $missing
    duplicate = Test-HiaEmbeddingRuntimeReady -Result $duplicate
    null_result = Test-HiaEmbeddingRuntimeReady -Result $null
}} | ConvertTo-Json -Compress
"""
        )
        self.assertEqual(
            {
                "green": True,
                "yellow": False,
                "red": False,
                "missing": False,
                "duplicate": False,
                "null_result": False,
            },
            json.loads(output),
        )

    def test_gui_environment_repair_actions_cover_parser_only_and_existing_model(
        self,
    ) -> None:
        base = {
            "state": "ready",
            "venv": {"portable": True},
            "python": {"managed_marker": {"valid": True}},
            "uv": {"available": True},
            "parser": {"installed": True},
            "torch": {"installed": False},
            "embedding_worker": {"installed": False},
            "models": {
                "items": [
                    {
                        "profile_id": "qwen3-embedding-0.6b",
                        "installed": False,
                    }
                ]
            },
            "embedding_mode": "fts5",
        }
        missing = json.loads(json.dumps(base))
        missing["state"] = "missing"
        missing_with_model = json.loads(json.dumps(missing))
        missing_with_model["models"]["items"][0]["installed"] = True
        legacy = json.loads(json.dumps(base))
        legacy["state"] = "repair_required"
        legacy["venv"]["portable"] = False
        legacy["python"]["managed_marker"]["valid"] = False
        legacy["parser"]["installed"] = False
        legacy["torch"] = {
            "installed": True,
            "cuda_available": True,
        }
        legacy["embedding_worker"]["installed"] = True
        legacy_with_model = json.loads(json.dumps(legacy))
        legacy_with_model["models"]["items"][0]["installed"] = True
        repair_required_with_model = json.loads(json.dumps(base))
        repair_required_with_model["state"] = "repair_required"
        repair_required_with_model["parser"]["installed"] = False
        repair_required_with_model["models"]["items"][0]["installed"] = True
        unsafe = json.loads(json.dumps(base))
        unsafe["state"] = "unsafe"
        existing_model = json.loads(json.dumps(base))
        existing_model["models"]["items"][0]["installed"] = True
        ready_model = json.loads(json.dumps(existing_model))
        ready_model["torch"]["installed"] = True
        ready_model["embedding_worker"]["installed"] = True

        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WPF_SCRIPT_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'HiaLauncher.Wpf.ps1 did not parse.' }}
foreach ($name in @(
    'Get-HiaKnowledgeEnvironmentAction',
    'Get-HiaKnowledgeEnvironmentReason',
    'Get-HiaKnowledgeEnvironmentStageFromLog'
)) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "missing helper: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}
$missing = {_ps_literal(json.dumps(missing))} | ConvertFrom-Json
$missingWithModel = {_ps_literal(json.dumps(missing_with_model))} | ConvertFrom-Json
$legacy = {_ps_literal(json.dumps(legacy))} | ConvertFrom-Json
$legacyWithModel = {_ps_literal(json.dumps(legacy_with_model))} | ConvertFrom-Json
$repairWithModel = {_ps_literal(json.dumps(repair_required_with_model))} | ConvertFrom-Json
$unsafe = {_ps_literal(json.dumps(unsafe))} | ConvertFrom-Json
$existing = {_ps_literal(json.dumps(existing_model))} | ConvertFrom-Json
$readyModel = {_ps_literal(json.dumps(ready_model))} | ConvertFrom-Json
$fts = {_ps_literal(json.dumps(base))} | ConvertFrom-Json
$profile = 'qwen3-embedding-0.6b'
[pscustomobject]@{{
    missing_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $missing -SelectedProfile $profile
    missing_model_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $missingWithModel -SelectedProfile $profile
    legacy_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $legacy -SelectedProfile $profile
    legacy_model_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $legacyWithModel -SelectedProfile $profile
    repair_model_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $repairWithModel -SelectedProfile $profile
    unsafe_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $unsafe -SelectedProfile $profile
    existing_model_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $existing -SelectedProfile $profile
    ready_model_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $readyModel -SelectedProfile $profile
    fts_action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $fts -SelectedProfile $profile
    legacy_reason = Get-HiaKnowledgeEnvironmentReason `
        -Environment $legacy -SelectedProfile $profile
    legacy_model_reason = Get-HiaKnowledgeEnvironmentReason `
        -Environment $legacyWithModel -SelectedProfile $profile
    existing_model_reason = Get-HiaKnowledgeEnvironmentReason `
        -Environment $existing -SelectedProfile $profile
    parser_stage = Get-HiaKnowledgeEnvironmentStageFromLog `
        -LogText 'Installing and validating pypdf'
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("environment-install", payload["missing_action"])
        self.assertEqual(
            "environment-install-embedding",
            payload["missing_model_action"],
        )
        self.assertEqual("environment-repair", payload["legacy_action"])
        self.assertEqual(
            "environment-repair-embedding",
            payload["legacy_model_action"],
        )
        self.assertEqual(
            "environment-repair-embedding",
            payload["repair_model_action"],
        )
        self.assertEqual("environment-repair", payload["unsafe_action"])
        self.assertEqual(
            "environment-repair-embedding",
            payload["existing_model_action"],
        )
        self.assertEqual("", payload["ready_model_action"])
        self.assertEqual("", payload["fts_action"])
        self.assertIn("项目外 Python", payload["legacy_reason"])
        self.assertIn("受管标记", payload["legacy_reason"])
        self.assertIn("一次修复", payload["legacy_model_reason"])
        self.assertIn("复用现有模型和项目缓存", payload["legacy_model_reason"])
        self.assertIn("PyTorch", payload["existing_model_reason"])
        self.assertIn("复用模型和项目缓存", payload["existing_model_reason"])
        self.assertIn("pypdf", payload["parser_stage"])

    def test_gui_knowledge_environment_repair_is_async_logged_and_retryable(
        self,
    ) -> None:
        wpf = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        wrapper = (
            REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
        ).read_text(encoding="utf-8-sig")
        xaml = XAML_PATH.read_text(encoding="utf-8")

        start = wpf[
            wpf.index("function Start-HiaKnowledgeEnvironmentRepair"):
            wpf.index("function Update-PathSummaries")
        ]
        complete = wpf[
            wpf.index("function Complete-HiaKnowledgeEnvironmentRepair"):
            wpf.index("$script:knowledgeEnvironmentTimer.Add_Tick")
        ]
        dedicated_click = wpf[
            wpf.index("$repairKnowledgeEnvironmentButton.Add_Click"):
            wpf.index("$importKnowledgeFileButton.Add_Click")
        ]
        repair_click = wpf[
            wpf.index("$repairButton.Add_Click"):
            wpf.index("$launchButton.Add_Click")
        ]

        self.assertIn("Get-HiaEmbeddingInstallLockInfo", start)
        self.assertIn("New-HiaEmbeddingInstallLogPath", start)
        self.assertIn(
            "Join-Path $projectRoot 'scripts\\hia-knowledge.ps1'",
            start,
        )
        self.assertIn("'-LogPath', $environmentLogPath", start)
        self.assertIn("$selectedProfile = Get-ComboEmbeddingProfile", start)
        self.assertIn(
            "[string]::IsNullOrWhiteSpace($selectedProfile)",
            start,
        )
        self.assertIn(
            "$arguments += @('-Profile', $selectedProfile)",
            start,
        )
        self.assertIn("'-Device', (Get-ComboEmbeddingDevice)", start)
        self.assertIn("'environment-install'", start)
        self.assertIn("'environment-repair'", start)
        self.assertNotIn("Start-HiaEmbeddingInstall", start)
        self.assertNotIn("Install-HiaEmbedding.ps1", start)
        self.assertIn("ReadToEndAsync()", start)
        self.assertIn("$script:knowledgeEnvironmentTimer.Start()", start)
        self.assertNotIn("WaitForExit", start)
        self.assertIn("Get-HiaEmbeddingInstallFailureSummary", complete)
        self.assertIn("$script:knowledgeEnvironmentLastFailure", complete)
        self.assertIn("$knowledgeEnvironmentLogExpander.IsExpanded = $true", complete)
        self.assertIn("可直接重试", complete)
        self.assertIn("Refresh-HiaKnowledgeDisplay -Quiet", complete)
        self.assertIn("Invoke-GuiScan", complete)
        self.assertIn(
            "$completedAction -like 'environment-*-embedding'",
            complete,
        )
        self.assertIn(
            "$nextAction -like 'environment-*-embedding'",
            complete,
        )
        self.assertLess(
            complete.index("$nextAction -like 'environment-*-embedding'"),
            complete.index(
                "项目本地 Python、uv、pypdf、PyTorch 与 "
                "embedding worker 已完成验证"
            ),
        )
        self.assertNotIn("Start-HiaKnowledgeIndexProcess -Action 'build'", start)
        self.assertNotIn(
            "Start-HiaKnowledgeIndexProcess -Action 'build'",
            complete,
        )
        for released_state in (
            "$script:knowledgeEnvironmentProcess = $null",
            "$script:knowledgeEnvironmentOutputTask = $null",
            "$script:knowledgeEnvironmentErrorTask = $null",
        ):
            self.assertIn(released_state, complete)
        self.assertIn(
            "Start-HiaKnowledgeEnvironmentRepair",
            dedicated_click,
        )
        self.assertIn("Get-HiaKnowledgeEnvironmentAction", repair_click)
        self.assertLess(
            repair_click.index("Test-CurrentRedCheck -Id 'codex.executable'"),
            repair_click.index("Get-HiaKnowledgeEnvironmentAction"),
        )
        self.assertIn("[string]$LogPath = ''", wrapper)
        self.assertIn("$installArguments += @('-LogPath', $LogPath)", wrapper)
        for control in (
            "KnowledgeEnvironmentReasonText",
            "KnowledgeEnvironmentProgressBar",
            "KnowledgeEnvironmentStageText",
            "KnowledgeEnvironmentLogExpander",
            "KnowledgeEnvironmentLogPathText",
            "KnowledgeEnvironmentLogTextBox",
        ):
            self.assertIn(f'x:Name="{control}"', xaml)
        self.assertIn("正在准备项目本地工具链", xaml)

    def test_embedding_install_lock_blocks_second_launcher_and_releases(
        self,
    ) -> None:
        fake_root = self.sandbox / "embedding-install-lock"
        fake_root.mkdir()
        log_one = (
            fake_root
            / ".runtime"
            / "launcher"
            / "embedding-install-owner.log"
        )
        log_two = (
            fake_root
            / ".runtime"
            / "launcher"
            / "embedding-install-next.log"
        )
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(EMBEDDING_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse.' }}
$requiredFunctions = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'Resolve-HiaEmbeddingLauncherDirectory',
    'Resolve-HiaEmbeddingInstallLogPath',
    'Enter-HiaEmbeddingInstallLock'
)
foreach ($name in $requiredFunctions) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "missing function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}

$logOne = Resolve-HiaEmbeddingInstallLogPath `
    -ProjectRoot {_ps_literal(fake_root)} `
    -RequestedPath {_ps_literal(log_one)}
$logTwo = Resolve-HiaEmbeddingInstallLogPath `
    -ProjectRoot {_ps_literal(fake_root)} `
    -RequestedPath {_ps_literal(log_two)}
$lockPath = Join-Path {_ps_literal(fake_root)} `
    '.runtime\\launcher\\embedding-install.lock'
$first = Enter-HiaEmbeddingInstallLock `
    -ProjectRoot {_ps_literal(fake_root)} `
    -InstallLogPath $logOne
try {{
    $active = Get-HiaEmbeddingInstallLockInfo `
        -ProjectRoot {_ps_literal(fake_root)}
    $secondFailure = ''
    try {{
        $unexpected = Enter-HiaEmbeddingInstallLock `
            -ProjectRoot {_ps_literal(fake_root)} `
            -InstallLogPath $logTwo
        $unexpected.Dispose()
    }} catch {{
        $secondFailure = [string]$_.Exception.Message
    }}
}} finally {{
    $first.Dispose()
}}
$inactive = Get-HiaEmbeddingInstallLockInfo `
    -ProjectRoot {_ps_literal(fake_root)}
$third = Enter-HiaEmbeddingInstallLock `
    -ProjectRoot {_ps_literal(fake_root)} `
    -InstallLogPath $logTwo
$third.Dispose()
$lockItem = Get-Item -LiteralPath $lockPath -Force
$lockPayload = [System.IO.File]::ReadAllText($lockPath) | ConvertFrom-Json
[pscustomobject]@{{
    active = [bool]$active.active
    active_log = [string]$active.log_path
    second_failure = $secondFailure
    inactive_after_release = $null -eq $inactive
    lock_is_file = $lockItem -is [System.IO.FileInfo]
    lock_is_reparse = (
        ([int]$lockItem.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    )
    latest_log = [string]$lockPayload.log_path
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertTrue(payload["active"])
        self.assertTrue(payload["inactive_after_release"])
        self.assertTrue(payload["lock_is_file"])
        self.assertFalse(payload["lock_is_reparse"])
        self.assertIn("already running", payload["second_failure"])
        self.assertEqual(log_one, Path(payload["active_log"]))
        self.assertIn(str(Path(payload["active_log"])), payload["second_failure"])
        self.assertEqual(log_two, Path(payload["latest_log"]))
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        start = wpf_source[
            wpf_source.index("function Start-HiaEmbeddingInstall"):
            wpf_source.index("function Update-HiaKnowledgeIndexFromEvent")
        ]
        self.assertIn("Get-HiaEmbeddingInstallLockInfo", start)
        self.assertIn("Show-HiaEmbeddingInstallAlreadyRunning", start)
        self.assertIn("已有知识向量安装正在运行", wpf_source)
        self.assertLess(
            start.index("Get-HiaEmbeddingInstallLockInfo"),
            start.index("New-HiaEmbeddingInstallLogPath"),
        )
        self.assertLess(
            start.index("Get-HiaEmbeddingInstallLockInfo"),
            start.index("$script:embeddingProcess.Start()"),
        )

    def test_embedding_device_is_threaded_through_preflight_and_cpu_torch_is_not_cuda_ready(
        self,
    ) -> None:
        fake_root = self.sandbox / "device-aware-preflight"
        fake_root.mkdir()
        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$profile = [string]$data.contract.default_profile
$deviceKey = [string]$data.contract.settings.device
$cpuOnly = [pscustomobject]@{{
    worker_ready = $true
    installed_profiles = @($profile)
    probe_passed = $true
    probe_message = '独立 worker 可用，但当前 PyTorch 仅能使用 CPU。'
    torch_version = 'test'
    torch_cuda_build = ''
    cuda_available = $false
    device_name = ''
}}
$cudaReady = [pscustomobject]@{{
    worker_ready = $true
    installed_profiles = @($profile)
    probe_passed = $true
    probe_message = '独立 worker 可用；CUDA 已就绪：Test GPU。'
    torch_version = 'test'
    torch_cuda_build = 'test'
    cuda_available = $true
    device_name = 'Test GPU'
}}
$auto = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -EmbeddingDevice auto `
    -ProbeOverride $cpuOnly
$cpu = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -EmbeddingDevice cpu `
    -ProbeOverride $cpuOnly
$cudaCpuOnly = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -EmbeddingDevice cuda `
    -ProbeOverride $cpuOnly
$cuda = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -EmbeddingDevice cuda `
    -ProbeOverride $cudaReady
$preflight = Invoke-HiaPreflight `
    -ProjectRoot {_ps_literal(fake_root)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -EmbeddingDevice cuda `
    -ProbeOverrides @{{
        embedding = $cpuOnly
        runtime_writable = $true
        loopback = $true
    }}
$preflightEmbedding = @(
    $preflight.checks | Where-Object id -eq 'embedding.runtime'
)[0]
[pscustomobject]@{{
    auto = $auto
    cpu = $cpu
    cuda_cpu_only = $cudaCpuOnly
    cuda = $cuda
    preflight_check = $preflightEmbedding
    preflight_device = [string]$preflight.PSObject.Properties[$deviceKey].Value
}} | ConvertTo-Json -Depth 8 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("green", payload["auto"]["level"])
        self.assertEqual("green", payload["cpu"]["level"])
        self.assertEqual("yellow", payload["cuda_cpu_only"]["level"])
        self.assertIn(
            "torch.cuda.is_available()=false",
            payload["cuda_cpu_only"]["message"],
        )
        self.assertIn("安装/修复", payload["cuda_cpu_only"]["advice"])
        self.assertIn("Houdini 仍可启动", payload["cuda_cpu_only"]["advice"])
        self.assertEqual("green", payload["cuda"]["level"])
        self.assertEqual("yellow", payload["preflight_check"]["level"])
        self.assertEqual("cuda", payload["preflight_device"])

        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        report_function = launcher_source[
            launcher_source.index("function Invoke-PreflightAndReport"):
            launcher_source.index("function Write-ConsoleSummary")
        ]
        self.assertIn("$SelectedEmbeddingDevice", report_function)
        self.assertIn(
            "-EmbeddingDevice $SelectedEmbeddingDevice",
            report_function,
        )
        check_only_call = launcher_source[
            launcher_source.index(
                "if ($CheckOnly -or $Json) {",
                launcher_source.index("$inputs ="),
            ):
            launcher_source.index("$wpfUiPath =")
        ]
        self.assertIn(
            "-SelectedEmbeddingDevice $inputs.embedding_device",
            check_only_call,
        )

        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        self.assertEqual(
            2,
            wpf_source.count(
                "-SelectedEmbeddingDevice $selectedEmbeddingDevice"
            ),
        )
        busy_function = wpf_source[
            wpf_source.index("function Set-BusyState"):
            wpf_source.index("function Test-CurrentRedCheck")
        ]
        self.assertIn(
            "$embeddingDeviceCombo.IsEnabled = "
            "(-not $Busy -and $null -ne $script:embeddingData)",
            busy_function,
        )

    def test_embedding_preflight_is_nonblocking_and_explains_fallback_states(self) -> None:
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8")
        core_source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "[ValidateRange(2, 60)][int]$ProbeTimeoutSeconds = 12",
            launcher_source,
        )
        self.assertIn(
            "-TimeoutSeconds ([Math]::Max(30, $TimeoutSeconds))",
            core_source,
        )

        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$default = [string]$data.contract.default_profile
$alternate = @($data.profiles.PSObject.Properties.Name | Where-Object {{ $_ -ne $default }})[0]
$missing = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $default `
    -ProbeOverride ([pscustomobject]@{{
        worker_ready = $false
        installed_profiles = @()
        probe_passed = $false
        probe_message = 'missing'
    }})
$fallback = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $alternate `
    -ProbeOverride ([pscustomobject]@{{
        worker_ready = $true
        installed_profiles = @($default)
        probe_passed = $true
        probe_message = 'ready'
    }})
$ready = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $alternate `
    -ProbeOverride ([pscustomobject]@{{
        worker_ready = $true
        installed_profiles = @($alternate)
        probe_passed = $true
        probe_message = '独立 worker 与必要 import 可用；预检不会加载模型。'
    }})
$broken = Get-HiaEmbeddingCheckResult `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -EmbeddingData $data `
    -EmbeddingProfile $default `
    -ProbeOverride ([pscustomobject]@{{
        worker_ready = $true
        installed_profiles = @($default)
        probe_passed = $false
        probe_message = 'import failed'
    }})
[pscustomobject]@{{
    missing = $missing
    fallback = $fallback
    ready = $ready
    broken = $broken
    missing_overall = Get-HiaOverallLevel -Checks @($missing)
}} | ConvertTo-Json -Depth 8 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("yellow", payload["missing"]["level"])
        self.assertEqual("yellow", payload["missing_overall"])
        self.assertIn("FTS5", payload["missing"]["message"])
        self.assertIn("Houdini 仍可启动", payload["missing"]["advice"])
        self.assertEqual("yellow", payload["fallback"]["level"])
        self.assertIn("降级", payload["fallback"]["message"])
        self.assertIn("FTS5", payload["fallback"]["message"])
        self.assertEqual("green", payload["ready"]["level"])
        self.assertIn("预检不会加载模型", payload["ready"]["message"])
        self.assertEqual("yellow", payload["broken"]["level"])
        self.assertIn("import failed", payload["broken"]["message"])

    def test_knowledge_index_process_plan_uses_bridge_contract_and_project_paths(
        self,
    ) -> None:
        fake_root = self.sandbox / "knowledge-index-plan"
        contract_target = fake_root / "src" / "hia_core" / "embedding_contract.py"
        contract_target.parent.mkdir(parents=True)
        shutil.copy2(EMBEDDING_CONTRACT_PATH, contract_target)
        (fake_root / "houdini_package" / "python_libs").mkdir(parents=True)
        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(fake_root)} `
    -PythonExe {_ps_literal(sys.executable)}
$profile = [string]$data.contract.default_profile
$status = New-HiaKnowledgeIndexProcessPlan `
    -ProjectRoot {_ps_literal(fake_root)} `
    -BridgePython {_ps_literal(sys.executable)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -Action status
$build = New-HiaKnowledgeIndexProcessPlan `
    -ProjectRoot {_ps_literal(fake_root)} `
    -BridgePython {_ps_literal(sys.executable)} `
    -EmbeddingData $data `
    -EmbeddingProfile $profile `
    -Action build
[pscustomobject]@{{
    status = $status
    build = $build
}} | ConvertTo-Json -Depth 12 -Compress
"""
        )
        payload = json.loads(output)
        public = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))["launcher_contract"]()
        module = public["knowledge_index"]["module"]
        root = str(fake_root)
        base_arguments = ["-B", "-m", module, "--project-root", root]
        self.assertEqual(
            [*base_arguments, "status"],
            payload["status"]["arguments"],
        )
        self.assertEqual(
            [*base_arguments, "build", "--batch-size", "32"],
            payload["build"]["arguments"],
        )
        self.assertEqual(Path(sys.executable), Path(payload["status"]["file_path"]))
        self.assertEqual(root, payload["status"]["working_directory"])
        self.assertEqual(
            os.pathsep.join(
                (
                    str(fake_root / "houdini_package" / "python_libs"),
                    str(fake_root / "src"),
                )
            ),
            payload["status"]["environment"]["PYTHONPATH"],
        )
        environment = payload["status"]["environment"]
        self.assertEqual(root, environment["HIA_PROJECT_ROOT"])
        self.assertEqual("utf-8", environment["PYTHONIOENCODING"])
        self.assertNotIn(public["environment"]["python"], environment)
        for profile in public["profiles"].values():
            self.assertNotIn(profile["model_dir_environment"], environment)
            self.assertNotIn(profile["model_revision_environment"], environment)
        self.assertEqual(
            set(public["environment"].values()),
            set(payload["status"]["clear_environment_names"]),
        )

    def test_knowledge_index_jsonl_parser_handles_progress_completion_and_error(
        self,
    ) -> None:
        public = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))["launcher_contract"]()
        protocol = public["knowledge_index"]["protocol"]
        base_index = {
            "available": True,
            "profile_id": public["default_profile"],
            "model_id": public["profiles"][public["default_profile"]]["model_id"],
            "dim": 1024,
            "total_chunks": 5,
            "vector_chunks": 3,
            "pending_chunks": 2,
            "complete": False,
            "last_batch_count": 2,
            "chunks_indexed_this_call": 2,
        }
        progress = {
            "protocol": protocol,
            "event": "progress",
            "action": "build",
            "batch_number": 2,
            "indexed_this_batch": 2,
            "index": base_index,
        }
        completed_index = {**base_index}
        completed_index.update(
            vector_chunks=5,
            pending_chunks=0,
            complete=True,
        )
        completed = {
            "protocol": protocol,
            "event": "completed",
            "action": "build",
            "batches": 3,
            "index": completed_index,
        }
        error = {
            "protocol": protocol,
            "event": "error",
            "action": "build",
            "code": "VECTOR_INDEX_NO_PROGRESS",
            "message": "no progress",
            "recoverable": True,
            "index": base_index,
        }
        early_error = {
            "protocol": protocol,
            "event": "error",
            "action": "build",
            "code": "BUILD_FAILED",
            "message": "source validation failed",
            "recoverable": True,
            "index": {},
        }
        invalid_protocol = {**progress, "protocol": "wrong/1"}
        missing_index = {key: value for key, value in progress.items() if key != "index"}
        negative_index = {**base_index, "pending_chunks": -1}
        negative = {**progress, "index": negative_index}
        active_profile_index = {**base_index}
        active_profile_index["active_profile"] = active_profile_index.pop(
            "profile_id"
        )
        active_profile_only = {
            **progress,
            "index": active_profile_index,
        }
        lines = [
            progress,
            completed,
            error,
            active_profile_only,
            early_error,
        ]
        invalid_lines = [invalid_protocol, missing_index, negative]
        ps_lines = ",\n".join(
            _ps_literal(json.dumps(item, ensure_ascii=False))
            for item in lines
        )
        ps_invalid = ",\n".join(
            _ps_literal(json.dumps(item, ensure_ascii=False))
            for item in invalid_lines
        )
        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$events = @(
    @({ps_lines}) | ForEach-Object {{
        ConvertFrom-HiaKnowledgeIndexJsonLine -Line $_ -EmbeddingData $data
    }}
)
$rejected = @(
    @({ps_invalid}) | ForEach-Object {{
        try {{
            [void](ConvertFrom-HiaKnowledgeIndexJsonLine -Line $_ -EmbeddingData $data)
            'accepted'
        }} catch {{
            [string]$_.Exception.Message
        }}
    }}
)
[pscustomobject]@{{
    events = $events
    rejected = $rejected
}} | ConvertTo-Json -Depth 12 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(
            ["progress", "completed", "error", "progress", "error"],
            [event["event"] for event in payload["events"]],
        )
        self.assertEqual(3, payload["events"][0]["index"]["vector_chunks"])
        self.assertTrue(payload["events"][1]["index"]["complete"])
        self.assertEqual(
            public["default_profile"],
            payload["events"][3]["index"]["profile_id"],
        )
        self.assertEqual(
            "VECTOR_INDEX_NO_PROGRESS",
            payload["events"][2]["code"],
        )
        self.assertEqual("BUILD_FAILED", payload["events"][4]["code"])
        self.assertTrue(all(message != "accepted" for message in payload["rejected"]))

    def test_embedding_preflight_rejects_reparse_ancestors_before_worker_probe(self) -> None:
        fake_root = self.sandbox / "embedding-reparse-project"
        lure_root = self.sandbox / "embedding-reparse-lure"
        fake_root.mkdir()
        lure_root.mkdir()
        output = self.run_powershell(
            f"""
$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$baseRoot = {_ps_literal(fake_root)}
$lure = {_ps_literal(lure_root)}
$workerRelative = [string]$data.contract.worker.python

$workerRoot = Join-Path $baseRoot 'worker-case'
$workerRuntime = Join-Path $workerRoot '.runtime'
[System.IO.Directory]::CreateDirectory($workerRuntime) | Out-Null
$workerPath = Join-Path $workerRoot $workerRelative
$lureWorker = Join-Path $lure 'toolchains'
[System.IO.Directory]::CreateDirectory($lureWorker) | Out-Null
New-Item -ItemType Junction -Path (Join-Path $workerRuntime 'toolchains') -Target $lureWorker | Out-Null
$data.layout.worker_python = $workerPath
$data.layout.models_root = Join-Path $workerRuntime 'models\\qwen3-embedding'
$data.layout.cache_root = Join-Path $workerRuntime 'cache\\embedding'
$data.layout.huggingface_cache = Join-Path $workerRuntime 'cache\\embedding\\huggingface'
$data.layout.transformers_cache = Join-Path $workerRuntime 'cache\\embedding\\transformers'
$data.layout.torch_cache = Join-Path $workerRuntime 'cache\\embedding\\torch'
$data.layout.temp_root = Join-Path $workerRuntime 'cache\\embedding\\tmp'
$workerState = Get-HiaEmbeddingRuntimeState `
    -ProjectRoot $workerRoot `
    -EmbeddingData $data

$cacheRoot = Join-Path $baseRoot 'cache-case'
$cacheRuntime = Join-Path $cacheRoot '.runtime'
$cacheWorker = Join-Path $cacheRoot $workerRelative
[System.IO.Directory]::CreateDirectory(
    [System.IO.Path]::GetDirectoryName($cacheWorker)
) | Out-Null
[System.IO.File]::WriteAllBytes($cacheWorker, [byte[]](1))
$lureCache = Join-Path $lure 'cache'
[System.IO.Directory]::CreateDirectory($lureCache) | Out-Null
New-Item -ItemType Junction -Path (Join-Path $cacheRuntime 'cache') -Target $lureCache | Out-Null
$data.layout.worker_python = $cacheWorker
$data.layout.models_root = Join-Path $cacheRuntime 'models\\qwen3-embedding'
$data.layout.cache_root = Join-Path $cacheRuntime 'cache\\embedding'
$data.layout.huggingface_cache = Join-Path $cacheRuntime 'cache\\embedding\\huggingface'
$data.layout.transformers_cache = Join-Path $cacheRuntime 'cache\\embedding\\transformers'
$data.layout.torch_cache = Join-Path $cacheRuntime 'cache\\embedding\\torch'
$data.layout.temp_root = Join-Path $cacheRuntime 'cache\\embedding\\tmp'
$cacheState = Get-HiaEmbeddingRuntimeState `
    -ProjectRoot $cacheRoot `
    -EmbeddingData $data
[pscustomobject]@{{
    worker_ready = [bool]$workerState.worker_ready
    cache_worker_ready = [bool]$cacheState.worker_ready
    cache_probe_passed = [bool]$cacheState.probe_passed
    cache_message = [string]$cacheState.probe_message
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertFalse(payload["worker_ready"])
        self.assertTrue(payload["cache_worker_ready"])
        self.assertFalse(payload["cache_probe_passed"])
        self.assertIn("缓存路径不在普通项目目录内", payload["cache_message"])

    def test_embedding_installer_plan_is_contract_selected_and_project_local(self) -> None:
        installer = runpy.run_path(str(EMBEDDING_INSTALLER_HELPER_PATH))
        contract = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))
        public = contract["launcher_contract"]()
        profiles = list(public["profiles"])
        self.assertEqual(2, len(profiles))

        default_plan = installer["build_plan"](
            project_root=REPOSITORY_ROOT,
            requested_profile=None,
            requested_revision=None,
        )
        alternate = next(
            profile
            for profile in profiles
            if profile != public["default_profile"]
        )
        alternate_plan = installer["build_plan"](
            project_root=REPOSITORY_ROOT,
            requested_profile=alternate,
            requested_revision=None,
        )
        self.assertEqual(public["default_profile"], default_plan["profile_id"])
        self.assertEqual(alternate, alternate_plan["profile_id"])
        self.assertNotEqual(default_plan["model_dir"], alternate_plan["model_dir"])
        for plan in (default_plan, alternate_plan):
            self.assertEqual(
                public["profiles"][plan["profile_id"]]["repository_size_gb"],
                plan["repository_size_gb"],
            )
            self.assertTrue(
                Path(plan["model_dir"]).is_relative_to(REPOSITORY_ROOT / ".runtime")
            )
            self.assertEqual(
                REPOSITORY_ROOT / ".venv" / "Scripts" / "python.exe",
                Path(plan["layout"]["worker_python"]),
            )
            for directory in plan["required_directories"]:
                self.assertTrue(Path(directory).is_relative_to(REPOSITORY_ROOT))
            uv_cache = Path(plan["child_environment"]["UV_CACHE_DIR"])
            self.assertTrue(uv_cache.is_relative_to(REPOSITORY_ROOT / ".runtime"))
            self.assertIn(str(uv_cache), plan["required_directories"])
            self.assertEqual(
                "never",
                plan["child_environment"]["UV_PYTHON_DOWNLOADS"],
            )
            self.assertTrue(
                {
                    "UV_INDEX",
                    "UV_DEFAULT_INDEX",
                    "UV_EXTRA_INDEX_URL",
                    "UV_INDEX_URL",
                    "UV_NO_INDEX",
                    "UV_OFFLINE",
                    "PIP_INDEX_URL",
                    "PIP_EXTRA_INDEX_URL",
                    "PIP_NO_INDEX",
                }.issubset(set(plan["remove_environment"]))
            )

    def test_embedding_launcher_sources_consume_contract_without_copied_model_values(self) -> None:
        contract = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))
        public = contract["launcher_contract"]()
        source_paths = (
            MODULE_PATH,
            LAUNCHER_PATH,
            WPF_SCRIPT_PATH,
            XAML_PATH,
            LIFECYCLE_PATH,
            EMBEDDING_INSTALLER_PATH,
            EMBEDDING_INSTALLER_HELPER_PATH,
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8-sig") for path in source_paths
        )
        for profile_id, profile in public["profiles"].items():
            self.assertNotIn(profile_id, combined)
            self.assertNotIn(profile["model_id"], combined)
            self.assertNotIn(profile["model_directory"], combined)
        for environment_name in public["environment"].values():
            self.assertNotIn(environment_name, combined)
        self.assertIn("launcher_contract", combined)
        self.assertIn("PROFILE_REGISTRY", combined)
        self.assertIn("runtime_layout", combined)
        self.assertIn("default_dimension", LIFECYCLE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("EmbeddingDimension", combined)
        self.assertNotIn("reranker", combined.lower())
        self.assertNotIn("quantization", combined.lower())
        helper_source = EMBEDDING_INSTALLER_HELPER_PATH.read_text(encoding="utf-8")
        installer_source = EMBEDDING_INSTALLER_PATH.read_text(
            encoding="utf-8-sig"
        )
        self.assertGreater(
            helper_source.index("from huggingface_hub import snapshot_download"),
            helper_source.index("def download_selected_model"),
        )
        for required in (
            "Selected model source: https://huggingface.co/{0}",
            "official model files are about {3} GB",
            "Interrupted downloads reuse the project-local Hugging Face cache",
        ):
            self.assertIn(required, installer_source)

    def test_lifecycle_injects_only_complete_contract_embedding_installations(self) -> None:
        fake_root = self.sandbox / "embedding-lifecycle"
        fake_root.mkdir()
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(LIFECYCLE_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'launch-houdini.ps1 did not parse' }}
$requiredFunctions = @(
    'Assert-OrdinaryProjectPath',
    'Test-EmbeddingEnvironmentName',
    'Get-EmbeddingModelInstallation',
    'New-EmbeddingChildEnvironment'
)
$definitions = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in $requiredFunctions
}}, $true))
foreach ($name in $requiredFunctions) {{
    $definition = @($definitions | Where-Object Name -eq $name)
    if ($definition.Count -ne 1) {{ throw "missing lifecycle function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}

$data = Get-HiaEmbeddingContractData `
    -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} `
    -PythonExe {_ps_literal(sys.executable)}
$root = {_ps_literal(fake_root)}
$default = [string]$data.contract.default_profile
$alternate = @($data.contract.profiles.PSObject.Properties.Name | Where-Object {{ $_ -ne $default }})[0]
$defaultProfile = $data.contract.profiles.PSObject.Properties[$default].Value
$alternateProfile = $data.contract.profiles.PSObject.Properties[$alternate].Value
$workerPython = Join-Path $root ([string]$data.contract.worker.python)
$layoutMap = [ordered]@{{ worker_python = $workerPython }}
$index = 0
foreach ($profileProperty in @($data.contract.profiles.PSObject.Properties)) {{
    $layoutMap["model_$index"] = Join-Path $root ([string]$profileProperty.Value.model_directory)
    $index += 1
}}
$payload = [pscustomobject]@{{
    contract = $data.contract
    layout = [pscustomobject]$layoutMap
}}

function Add-TestModel([object]$Profile, [bool]$Complete) {{
    $directory = Join-Path $root ([string]$Profile.model_directory)
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $directory 'config.json'), '{{}}')
    [System.IO.File]::WriteAllBytes((Join-Path $directory 'model.safetensors'), [byte[]](1, 2, 3))
    if ($Complete) {{
        $manifest = [ordered]@{{
            contract_version = [int]$data.contract.contract_version
            profile_id = [string]$Profile.profile_id
            model_id = [string]$Profile.model_id
            revision = 'main'
        }}
        [System.IO.File]::WriteAllText(
            (Join-Path $directory '.hia-embedding-model.json'),
            ($manifest | ConvertTo-Json)
        )
    }}
}}

$empty = New-EmbeddingChildEnvironment -Payload $payload -RequestedProfile $alternate -Root $root
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($workerPython)) | Out-Null
[System.IO.File]::WriteAllBytes($workerPython, [byte[]](1))
Add-TestModel -Profile $alternateProfile -Complete $false
$half = New-EmbeddingChildEnvironment -Payload $payload -RequestedProfile $alternate -Root $root
Add-TestModel -Profile $defaultProfile -Complete $true
$fallback = New-EmbeddingChildEnvironment -Payload $payload -RequestedProfile $alternate -Root $root
$alternateDirectory = Join-Path $root ([string]$alternateProfile.model_directory)
$alternateManifest = [ordered]@{{
    contract_version = [int]$data.contract.contract_version
    profile_id = [string]$alternateProfile.profile_id
    model_id = [string]$alternateProfile.model_id
    revision = 'main'
}}
[System.IO.File]::WriteAllText(
    (Join-Path $alternateDirectory '.hia-embedding-model.json'),
    ($alternateManifest | ConvertTo-Json)
)
$full = New-EmbeddingChildEnvironment -Payload $payload -RequestedProfile $alternate -RequestedDevice cpu -Root $root
$environment = $data.contract.environment
[pscustomobject]@{{
    empty_keys = @($empty.values.Keys)
    empty_has_python = $empty.values.ContainsKey([string]$environment.python)
    half_has_python = $half.values.ContainsKey([string]$environment.python)
    half_has_alternate = $half.values.ContainsKey([string]$alternateProfile.model_dir_environment)
    fallback_has_default = $fallback.values.ContainsKey([string]$defaultProfile.model_dir_environment)
    fallback_has_alternate = $fallback.values.ContainsKey([string]$alternateProfile.model_dir_environment)
    full_has_alternate = $full.values.ContainsKey([string]$alternateProfile.model_dir_environment)
    full_requested = [string]$full.requested_profile
    full_dimension = [int]$full.values[[string]$environment.dimension]
    expected_dimension = [int]$alternateProfile.default_dimension
    full_device = [string]$full.values[[string]$environment.device]
    cleared_environment_count = @($full.environment_names).Count
}} | ConvertTo-Json -Depth 8 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(3, len(payload["empty_keys"]))
        self.assertFalse(payload["empty_has_python"])
        self.assertTrue(payload["half_has_python"])
        self.assertFalse(payload["half_has_alternate"])
        self.assertTrue(payload["fallback_has_default"])
        self.assertFalse(payload["fallback_has_alternate"])
        self.assertTrue(payload["full_has_alternate"])
        contract = runpy.run_path(str(EMBEDDING_CONTRACT_PATH))["launcher_contract"]()
        self.assertEqual(
            next(
                profile
                for profile in contract["profiles"]
                if profile != contract["default_profile"]
            ),
            payload["full_requested"],
        )
        self.assertEqual(payload["expected_dimension"], payload["full_dimension"])
        self.assertEqual("cpu", payload["full_device"])
        self.assertEqual(
            len(set(contract["environment"].values())),
            payload["cleared_environment_count"],
        )

    def test_lifecycle_bootstrap_builds_only_installed_pending_knowledge(
        self,
    ) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        call = source.index(
            "Invoke-HiaKnowledgeBootstrapOnLaunch `",
            source.index("$bridgePythonPath ="),
        )
        self.assertLess(source.index("$embeddingSelection ="), call)
        self.assertLess(call, source.index("$bridgeToken =", call))
        self.assertIn("'hia_mcp_runtime.knowledge_index_cli'", source)

        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(LIFECYCLE_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'launch-houdini.ps1 did not parse' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Invoke-HiaKnowledgeBootstrapOnLaunch'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'knowledge bootstrap helper is unavailable' }}
. ([scriptblock]::Create($definition[0].Extent.Text))

$script:responses = [System.Collections.Generic.Queue[object]]::new()
$script:calls = [System.Collections.Generic.List[string]]::new()
function Add-FakeResponse([string]$Action, [int]$Pending, [bool]$Installed) {{
    $payload = [ordered]@{{
        event = 'completed'
        action = $Action
        index = @{{ pending_chunks = $Pending }}
        installation = @{{ installed = $Installed }}
    }}
    $script:responses.Enqueue([pscustomobject]@{{
        timed_out = $false
        exit_code = 0
        stdout = ($payload | ConvertTo-Json -Depth 5 -Compress)
    }})
}}
function Invoke-HiaProcess {{
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironmentVariables,
        [string]$WorkingDirectory
    )
    $action = @($Arguments | Where-Object {{ $_ -in @('bootstrap', 'build') }})[-1]
    $script:calls.Add($action)
    return $script:responses.Dequeue()
}}
$common = @{{
    Python = 'X:\\project\\.venv\\Scripts\\python.exe'
    Root = 'X:\\project'
    PythonPathEntries = @('X:\\project\\houdini_package\\python_libs', 'X:\\project\\src')
    EmbeddingEnvironment = @{{ HIA_EMBEDDING_PROFILE = 'qwen3-embedding-0.6b' }}
    EmbeddingEnvironmentNames = @('HIA_EMBEDDING_PROFILE')
}}

Add-FakeResponse bootstrap 2 $true
Add-FakeResponse build 0 $true
Invoke-HiaKnowledgeBootstrapOnLaunch @common
$pending = ($script:calls -join ',')

$script:calls.Clear()
Add-FakeResponse bootstrap 0 $true
Invoke-HiaKnowledgeBootstrapOnLaunch @common
$complete = ($script:calls -join ',')

$script:calls.Clear()
Add-FakeResponse bootstrap 2 $false
Invoke-HiaKnowledgeBootstrapOnLaunch @common
$lexicalOnly = ($script:calls -join ',')

[pscustomobject]@{{
    pending = $pending
    complete = $complete
    lexical_only = $lexicalOnly
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("bootstrap,build", payload["pending"])
        self.assertEqual("bootstrap", payload["complete"])
        self.assertEqual("bootstrap", payload["lexical_only"])

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
    previews_error = (Get-RenderOutputError (Join-Path $root '.runtime\cache\previews\final'))
    tmp_error = (Get-RenderOutputError (Join-Path $root '.runtime\cache\tmp'))
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
            "previews_error",
            "tmp_error",
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
        helper_start = wpf_source.index("function Invoke-HiaCacheCleanup")
        helper_end = wpf_source.index(
            "$cleanupScreenshotsButton.Add_Click",
            helper_start,
        )
        cleanup_helper = wpf_source[helper_start:helper_end]
        handler_end = wpf_source.index("$openReportButton.Add_Click", helper_end)
        cleanup_handler = wpf_source[helper_end:handler_end]
        preview_call = cleanup_helper.index("-Arguments @('-Action', 'list'")
        confirmation = cleanup_helper.index("[System.Windows.MessageBox]::Show")
        delete_call = cleanup_helper.index("'-Action', 'clear'", confirmation)
        self.assertLess(preview_call, confirmation)
        self.assertLess(confirmation, delete_call)
        self.assertIn("Invoke-HiaCacheCleanup -CategoryIds @(", cleanup_handler)
        for required in (
            "hia-cache.ps1",
            "'-Category', $categoryCsv",
            "'-SnapshotHash', [string]$preview.snapshot_hash",
            "预计释放",
            "项目资料、模型、工具链、附件、会话检查点、HIP 与最终输出不会被清理",
            "[System.Windows.MessageBoxButton]::YesNo",
            "[System.Windows.MessageBoxResult]::No",
            "未删除任何文件",
            "缓存清理完成",
            "Update-HiaCacheActions",
        ):
            self.assertIn(required, wpf_source)
        for forbidden in (
            "Invoke-HiaScreenshotCacheCleanup",
            "[System.IO.File]::Delete",
            "Remove-Item",
        ):
            self.assertNotIn(forbidden, cleanup_helper + cleanup_handler)

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

    def test_wpf_knowledge_index_is_manual_async_resumable_and_nonblocking(
        self,
    ) -> None:
        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        module_source = MODULE_PATH.read_text(encoding="utf-8-sig")
        for required in (
            'x:Name="KnowledgeIndexPanel"',
            'x:Name="KnowledgeIndexModelText"',
            'x:Name="KnowledgeIndexCountText"',
            'x:Name="KnowledgeIndexProgressBar"',
            'x:Name="KnowledgeIndexStatusText"',
            'x:Name="KnowledgeIndexActionButton"',
            "本地知识索引",
            "构建/继续索引",
        ):
            self.assertIn(required, xaml_source)

        for required in (
            "New-HiaKnowledgeCliProcessPlan",
            "ConvertFrom-HiaKnowledgeIndexJsonLine",
            "ReadLineAsync()",
            "ReadToEndAsync()",
            "StandardOutputEncoding",
            "clear_environment_names",
            "本次构建已取消；已提交批次保留",
            "FTS5 lexical 检索可继续工作，Houdini 仍可启动",
            "索引已完成",
            "$script:knowledgeIndexTimer.Add_Tick",
        ):
            self.assertIn(required, wpf_source)
        self.assertIn("PYTHONPATH", module_source)
        self.assertIn("default_batch_size", module_source)
        self.assertIn("Test-HiaEmbeddingModelInstall", module_source)

        handler_start = wpf_source.index("$knowledgeIndexActionButton.Add_Click")
        handler_end = wpf_source.index(
            "$mcpBackendCombo.Add_SelectionChanged",
            handler_start,
        )
        action_handler = wpf_source[handler_start:handler_end]
        self.assertIn("Stop-HiaKnowledgeIndexProcess", action_handler)
        self.assertIn(
            "Start-HiaKnowledgeIndexProcess -Action 'build'",
            action_handler,
        )
        self.assertEqual(
            1,
            wpf_source.count("Start-HiaKnowledgeIndexProcess -Action 'build'"),
        )

        start_function_start = wpf_source.index(
            "function Start-HiaKnowledgeIndexProcess"
        )
        start_function = wpf_source[
            start_function_start:
            wpf_source.index(
                "function Stop-HiaKnowledgeIndexProcess {",
                start_function_start,
            )
        ]
        self.assertNotIn("Set-BusyState", start_function)
        self.assertNotIn("Remove-Item", start_function)
        self.assertNotIn("$processEnvironment = if", start_function)
        self.assertIn(
            "$processEnvironment = $startInfo.EnvironmentVariables",
            start_function,
        )
        self.assertIn("$knowledgeIndexFailure = $_", start_function)
        self.assertIn(
            "$failureDetail = [string]$knowledgeIndexFailure.Exception.Message",
            start_function,
        )
        self.assertIn("-Message $failureMessage", start_function)
        self.assertIn("$startMessage = if ($Action -eq 'build')", start_function)
        self.assertIn(
            "Set-HiaKnowledgeIndexDisplay -Message $startMessage",
            start_function,
        )
        self.assertNotIn("Set-HiaKnowledgeIndexDisplay -Message (", start_function)
        tree_stop_function = wpf_source[
            wpf_source.index("function Stop-HiaKnowledgeIndexProcessTree"):
            wpf_source.index("function Complete-HiaKnowledgeIndexProcess")
        ]
        for required in (
            "$Process.Id",
            "Join-Path $env:SystemRoot 'System32\\taskkill.exe'",
            '"/PID $processId /T /F"',
            "UseShellExecute = $false",
            "CreateNoWindow = $true",
            "WaitForExit(5000)",
            "$terminator.ExitCode -ne 0",
        ):
            self.assertIn(required, tree_stop_function)
        self.assertNotIn("/IM", tree_stop_function.upper())
        self.assertNotIn(".Kill()", wpf_source)
        self.assertIn("Stop-HiaKnowledgeIndexProcessTree", start_function)
        complete_function = wpf_source[
            wpf_source.index("function Complete-HiaKnowledgeIndexProcess"):
            wpf_source.index("function Read-HiaKnowledgeIndexOutput")
        ]
        self.assertIn(
            "Stop-HiaKnowledgeIndexProcessTree -Process $process",
            complete_function,
        )
        cancelled = complete_function[complete_function.index("if ($cancelled)"):]
        self.assertIn("Start-HiaKnowledgeIndexProcess -Action 'status'", cancelled)
        stop_function = wpf_source[
            wpf_source.index("function Stop-HiaKnowledgeIndexProcess {"):
            wpf_source.index("function Get-PathIndex")
        ]
        self.assertIn("Stop-HiaKnowledgeIndexProcessTree", stop_function)
        read_function = wpf_source[
            wpf_source.index("function Read-HiaKnowledgeIndexOutput"):
            wpf_source.index("function Test-HiaKnowledgeIndexProcessComplete")
        ]
        self.assertIn("Stop-HiaKnowledgeIndexProcessTree", read_function)
        timer_handler = wpf_source[
            wpf_source.index("$script:knowledgeIndexTimer.Add_Tick"):
            wpf_source.index("function Start-HiaKnowledgeIndexProcess")
        ]
        self.assertIn("Stop-HiaKnowledgeIndexProcessTree", timer_handler)
        closed_handler = wpf_source[wpf_source.index("$window.Add_Closed"):]
        self.assertIn("Stop-HiaKnowledgeIndexProcessTree", closed_handler)
        self.assertNotIn("knowledge.sqlite3", wpf_source)
        self.assertNotIn("Invoke-WebRequest", start_function)
        self.assertNotIn("ShowDialog", start_function)

    def test_wpf_xaml_loads_and_exposes_required_controls(self) -> None:
        ET.parse(XAML_PATH)
        required_types = {
            "LayoutRoot": "System.Windows.Controls.Grid",
            "CustomTitleBar": "System.Windows.Controls.Border",
            "MinimizeWindowButton": "System.Windows.Controls.Button",
            "MaximizeWindowButton": "System.Windows.Controls.Button",
            "MaximizeWindowGlyph": "System.Windows.Controls.TextBlock",
            "CloseWindowButton": "System.Windows.Controls.Button",
            "NavigationColumn": "System.Windows.Controls.ColumnDefinition",
            "NavigationRail": "System.Windows.Controls.Border",
            "SidebarPanel": "System.Windows.Controls.Grid",
            "MainShell": "System.Windows.Controls.Grid",
            "RightVisualRail": "System.Windows.Controls.Border",
            "MainScrollViewer": "System.Windows.Controls.ScrollViewer",
            "MainWorkspacePanel": "System.Windows.Controls.Border",
            "PageHost": "System.Windows.Controls.Grid",
            "OverviewNavButton": "System.Windows.Controls.RadioButton",
            "EnvironmentNavButton": "System.Windows.Controls.RadioButton",
            "PreflightNavButton": "System.Windows.Controls.RadioButton",
            "ReportsSettingsNavButton": "System.Windows.Controls.RadioButton",
            "OverviewPage": "System.Windows.Controls.Grid",
            "EnvironmentPage": "System.Windows.Controls.Grid",
            "PreflightPage": "System.Windows.Controls.Grid",
            "ReportsSettingsPage": "System.Windows.Controls.Grid",
            "PageTitleText": "System.Windows.Controls.TextBlock",
            "PageSubtitleText": "System.Windows.Controls.TextBlock",
            "OverviewOverallHeadline": "System.Windows.Controls.TextBlock",
            "OverviewOverallDetail": "System.Windows.Controls.TextBlock",
            "OptionalArtworkPanel": "System.Windows.Controls.Border",
            "OptionalArtworkImage": "System.Windows.Controls.Image",
            "OverviewHoudiniDot": "System.Windows.Shapes.Ellipse",
            "OverviewHoudiniValueText": "System.Windows.Controls.TextBlock",
            "OverviewHoudiniDetailText": "System.Windows.Controls.TextBlock",
            "OverviewMcpDot": "System.Windows.Shapes.Ellipse",
            "OverviewMcpValueText": "System.Windows.Controls.TextBlock",
            "OverviewMcpDetailText": "System.Windows.Controls.TextBlock",
            "OverviewCodexDot": "System.Windows.Shapes.Ellipse",
            "OverviewCodexValueText": "System.Windows.Controls.TextBlock",
            "OverviewCodexDetailText": "System.Windows.Controls.TextBlock",
            "OverviewEmbeddingDot": "System.Windows.Shapes.Ellipse",
            "OverviewEmbeddingValueText": "System.Windows.Controls.TextBlock",
            "OverviewEmbeddingDetailText": "System.Windows.Controls.TextBlock",
            "RecoveryCard": "System.Windows.Controls.Border",
            "RecoveryCheckpointText": "System.Windows.Controls.TextBlock",
            "RecoverCheckpointOption": "System.Windows.Controls.RadioButton",
            "NormalLaunchOption": "System.Windows.Controls.RadioButton",
            "OverviewQuickActionsPanel": "System.Windows.Controls.Border",
            "QuickRescanButton": "System.Windows.Controls.Button",
            "QuickRepairButton": "System.Windows.Controls.Button",
            "QuickCleanupScreenshotsButton": "System.Windows.Controls.Button",
            "QuickOpenReportButton": "System.Windows.Controls.Button",
            "QuickCopyReportButton": "System.Windows.Controls.Button",
            "OverallStatusBadge": "System.Windows.Controls.Border",
            "OverallStatusDot": "System.Windows.Shapes.Ellipse",
            "OverallStatusText": "System.Windows.Controls.TextBlock",
            "McpBackendComboBox": "System.Windows.Controls.ComboBox",
            "EmbeddingProfileComboBox": "System.Windows.Controls.ComboBox",
            "EmbeddingDeviceComboBox": "System.Windows.Controls.ComboBox",
            "HoudiniComboBox": "System.Windows.Controls.ComboBox",
            "BrowseHoudiniButton": "System.Windows.Controls.Button",
            "HoudiniPathText": "System.Windows.Controls.TextBlock",
            "BridgePythonComboBox": "System.Windows.Controls.ComboBox",
            "BrowseBridgeButton": "System.Windows.Controls.Button",
            "BridgePathText": "System.Windows.Controls.TextBlock",
            "RenderOutputTextBox": "System.Windows.Controls.TextBox",
            "BrowseRenderOutputButton": "System.Windows.Controls.Button",
            "EnvironmentKnowledgeRuntimeText": "System.Windows.Controls.TextBlock",
            "EnvironmentKnowledgeRuntimePathText": "System.Windows.Controls.TextBlock",
            "EnvironmentActivationCommandText": "System.Windows.Controls.TextBlock",
            "CopyActivationCommandButton": "System.Windows.Controls.Button",
            "KnowledgeEnvironmentStatusText": "System.Windows.Controls.TextBlock",
            "KnowledgeEnvironmentPathText": "System.Windows.Controls.TextBlock",
            "RepairKnowledgeEnvironmentButton": "System.Windows.Controls.Button",
            "KnowledgeEnvironmentReasonText": "System.Windows.Controls.TextBlock",
            "KnowledgeEnvironmentProgressPanel": "System.Windows.Controls.Grid",
            "KnowledgeEnvironmentProgressBar": "System.Windows.Controls.ProgressBar",
            "KnowledgeEnvironmentStageText": "System.Windows.Controls.TextBlock",
            "KnowledgeEnvironmentLogExpander": "System.Windows.Controls.Expander",
            "KnowledgeEnvironmentLogPathText": "System.Windows.Controls.TextBlock",
            "KnowledgeEnvironmentLogTextBox": "System.Windows.Controls.TextBox",
            "KnowledgeSourcesSummaryText": "System.Windows.Controls.TextBlock",
            "ImportKnowledgeFileButton": "System.Windows.Controls.Button",
            "ImportKnowledgeFolderButton": "System.Windows.Controls.Button",
            "RefreshKnowledgeSourcesButton": "System.Windows.Controls.Button",
            "KnowledgeSourcesList": "System.Windows.Controls.ListBox",
            "DeleteKnowledgeSourceButton": "System.Windows.Controls.Button",
            "RescanKnowledgeSourcesButton": "System.Windows.Controls.Button",
            "KnowledgeIndexPanel": "System.Windows.Controls.Border",
            "KnowledgeIndexModelText": "System.Windows.Controls.TextBlock",
            "KnowledgeIndexCountText": "System.Windows.Controls.TextBlock",
            "KnowledgeIndexProgressBar": "System.Windows.Controls.ProgressBar",
            "KnowledgeIndexStatusText": "System.Windows.Controls.TextBlock",
            "KnowledgeIndexActionButton": "System.Windows.Controls.Button",
            "CacheSummaryText": "System.Windows.Controls.TextBlock",
            "RefreshCacheButton": "System.Windows.Controls.Button",
            "CacheCategoriesList": "System.Windows.Controls.ListBox",
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
            "OpenReportButton": "System.Windows.Controls.Button",
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
    $launchButton = $window.FindName('LaunchButton')
    # Logical viewports cover the default 100%, 125%, 150%, and the
    # minimum-size 200% DPI scenarios without opening a real window.
    foreach ($size in @(
        [Windows.Size]::new(1180, 820),
        [Windows.Size]::new(944, 656),
        [Windows.Size]::new(820, 600),
        [Windows.Size]::new(787, 547),
        [Windows.Size]::new(640, 480)
    )) {{
        $window.Content.Measure($size)
        $window.Content.Arrange([Windows.Rect]::new(0, 0, $size.Width, $size.Height))
        $window.Content.UpdateLayout()
        $launchPosition = $launchButton.TranslatePoint(
            [Windows.Point]::new(0, 0),
            $window.Content
        )
        if (
            $launchButton.ActualWidth -le 0 -or
            $launchButton.ActualHeight -le 0 -or
            $launchPosition.Y -lt 0 -or
            ($launchPosition.Y + $launchButton.ActualHeight) -gt $size.Height
        ) {{ throw "Launch button is unreachable at $($size.Width)x$($size.Height)." }}
    }}
    $types = [ordered]@{{}}
    foreach ($name in @({names})) {{
        $control = $window.FindName($name)
        if ($null -eq $control) {{ throw "Missing control: $name" }}
        $types[$name] = $control.GetType().FullName
    }}
    $recoveryTransform = $window.FindName('RecoveryCard').RenderTransform
    $quickActionsTransform = (
        $window.FindName('OverviewQuickActionsPanel').RenderTransform
    )
    $knowledgeIndexTransform = $window.FindName('KnowledgeIndexPanel').RenderTransform
    if (
        $recoveryTransform -isnot [Windows.Media.ScaleTransform] -or
        $quickActionsTransform -isnot [Windows.Media.ScaleTransform] -or
        $knowledgeIndexTransform -isnot [Windows.Media.ScaleTransform] -or
        [object]::ReferenceEquals($recoveryTransform, $quickActionsTransform) -or
        [object]::ReferenceEquals($recoveryTransform, $knowledgeIndexTransform) -or
        [object]::ReferenceEquals($quickActionsTransform, $knowledgeIndexTransform)
    ) {{
        throw 'Hover cards must own independent layout-neutral transforms.'
    }}
    $loadedWindow = $window
    $layoutRoot = $loadedWindow.FindName('LayoutRoot')
    $navigationColumn = $loadedWindow.FindName('NavigationColumn')
    $sidebarPanel = $loadedWindow.FindName('SidebarPanel')
    $rightVisualRail = $loadedWindow.FindName('RightVisualRail')
    $wpfSource = [IO.File]::ReadAllText({_ps_literal(WPF_SCRIPT_PATH)})
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput(
        $wpfSource,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {{ throw 'HiaLauncher.Wpf.ps1 did not parse.' }}
    $requiredFunctions = @(
        'Set-HiaLauncherPage',
        'Update-ResponsiveLayout'
    )
    $definitions = @($ast.FindAll({{
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $requiredFunctions
    }}, $true))
    foreach ($name in $requiredFunctions) {{
        $definition = @($definitions | Where-Object Name -eq $name)
        if ($definition.Count -ne 1) {{ throw "Missing WPF function: $name" }}
        . ([scriptblock]::Create($definition[0].Extent.Text))
    }}

    $overviewPage = $loadedWindow.FindName('OverviewPage')
    $environmentPage = $loadedWindow.FindName('EnvironmentPage')
    $preflightPage = $loadedWindow.FindName('PreflightPage')
    $reportsSettingsPage = $loadedWindow.FindName('ReportsSettingsPage')
    $overviewNavButton = $loadedWindow.FindName('OverviewNavButton')
    $environmentNavButton = $loadedWindow.FindName('EnvironmentNavButton')
    $preflightNavButton = $loadedWindow.FindName('PreflightNavButton')
    $reportsSettingsNavButton = $loadedWindow.FindName('ReportsSettingsNavButton')
    $pageTitleText = $loadedWindow.FindName('PageTitleText')
    $pageSubtitleText = $loadedWindow.FindName('PageSubtitleText')
    $mainScrollViewer = $loadedWindow.FindName('MainScrollViewer')
    foreach ($page in @('overview', 'environment', 'preflight', 'reports')) {{
        Set-HiaLauncherPage -Page $page
        $visiblePages = @(
            @(
                $overviewPage,
                $environmentPage,
                $preflightPage,
                $reportsSettingsPage
            ) | Where-Object Visibility -eq ([System.Windows.Visibility]::Visible)
        )
        $checkedNavigation = @(
            @(
                $overviewNavButton,
                $environmentNavButton,
                $preflightNavButton,
                $reportsSettingsNavButton
            ) | Where-Object {{ $_.IsChecked -eq $true }}
        )
        if ($visiblePages.Count -ne 1 -or $checkedNavigation.Count -ne 1) {{
            throw "Page navigation did not select exactly one page: $page"
        }}
    }}

    $script:compactLayout = $null
    $window = [pscustomobject]@{{ ActualWidth = 640.0 }}
    Update-ResponsiveLayout
    if (
        -not $script:compactLayout -or
        $navigationColumn.Width.Value -ne 190 -or
        $sidebarPanel.Margin.Left -ne 14 -or
        $sidebarPanel.Margin.Top -ne 18 -or
        $sidebarPanel.Margin.Right -ne 14 -or
        $sidebarPanel.Margin.Bottom -ne 14 -or
        $rightVisualRail.Width -ne 160 -or
        $rightVisualRail.Margin.Left -ne 0 -or
        $rightVisualRail.Margin.Top -ne 12 -or
        $rightVisualRail.Margin.Right -ne 0 -or
        $rightVisualRail.Margin.Bottom -ne 0
    ) {{ throw 'Compact responsive layout did not activate.' }}
    $window.ActualWidth = 1180.0
    Update-ResponsiveLayout
    if (
        $script:compactLayout -or
        $navigationColumn.Width.Value -ne 212 -or
        $sidebarPanel.Margin.Left -ne 18 -or
        $sidebarPanel.Margin.Top -ne 22 -or
        $sidebarPanel.Margin.Right -ne 18 -or
        $sidebarPanel.Margin.Bottom -ne 18 -or
        $rightVisualRail.Width -ne 174 -or
        $rightVisualRail.Margin.Left -ne 0 -or
        $rightVisualRail.Margin.Top -ne 16 -or
        $rightVisualRail.Margin.Right -ne 0 -or
        $rightVisualRail.Margin.Bottom -ne 0
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

    def test_wpf_xaml_is_self_contained_and_uses_safe_dark_window_chrome(self) -> None:
        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        combined = xaml_source + wpf_source + launcher_source

        self.assertIn('FontFamily="Segoe UI"', xaml_source)
        self.assertRegex(xaml_source, r'MinWidth="\d+"')
        self.assertRegex(xaml_source, r'MinHeight="\d+"')
        self.assertIn('WindowStyle="None"', xaml_source)
        self.assertIn('AllowsTransparency="False"', xaml_source)
        self.assertIn('Background="#090C18"', xaml_source)
        self.assertIn('x:Name="CustomTitleBar"', xaml_source)
        self.assertIn('Background="{StaticResource TitleBarGradientBrush}"', xaml_source)
        self.assertIn('x:Name="MinimizeWindowButton"', xaml_source)
        self.assertIn('x:Name="MaximizeWindowButton"', xaml_source)
        self.assertIn('x:Name="CloseWindowButton"', xaml_source)
        self.assertIn("[System.Windows.Shell.WindowChrome]::new()", wpf_source)
        self.assertIn("$windowChrome.CaptionHeight = 42", wpf_source)
        self.assertIn("$windowChrome.ResizeBorderThickness", wpf_source)
        self.assertIn("$windowChrome.GlassFrameThickness", wpf_source)
        self.assertIn("$windowChrome.UseAeroCaptionButtons = $false", wpf_source)
        self.assertIn("SetIsHitTestVisibleInChrome", wpf_source)
        for command in (
            "MinimizeWindow",
            "MaximizeWindow",
            "RestoreWindow",
            "CloseWindow",
        ):
            self.assertIn(f"[System.Windows.SystemCommands]::{command}", wpf_source)
        self.assertIn("$window.Add_StateChanged", wpf_source)
        self.assertIn('x:Name="BusyProgressBar"', xaml_source)
        self.assertIn('IsIndeterminate="True"', xaml_source)
        self.assertIn('Title="Big-Chicken Houdini Intelligence Agent"', xaml_source)
        self.assertIn('Text="BIG-CHICKEN"', xaml_source)
        self.assertIn('Text="Houdini Intelligence Agent"', xaml_source)
        self.assertIn(
            'AutomationProperties.Name="Big-Chicken Houdini Intelligence Agent"',
            xaml_source,
        )
        self.assertNotIn("Big-Chicken 插件", combined)
        self.assertGreaterEqual(xaml_source.count('TextTrimming="CharacterEllipsis"'), 2)
        self.assertGreaterEqual(xaml_source.count("ToolTip="), 3)
        self.assertNotIn('x:Name="HiaLogoMark"', xaml_source)
        self.assertNotIn("小助手", combined)
        self.assertIn('x:Key="AccentGradientBrush"', xaml_source)
        self.assertIn("<LinearGradientBrush", xaml_source)
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
            "<ImageBrush",
            "<WebBrowser",
            "ResourceDictionary Source=",
            "clr-namespace:",
            "assembly=",
            "pack://",
            "file://",
            'AllowsTransparency="True"',
            "DragMove",
            "x:Class=",
            ".jpg",
            ".jpeg",
            ".webp",
            ".svg",
        )
        for text in forbidden:
            self.assertNotIn(text, combined)
        self.assertEqual(1, len(re.findall(r"<Image(?:\s|>)", xaml_source)))
        self.assertNotIn('Source="', ET.tostring(
            next(
                element
                for element in ET.parse(XAML_PATH).getroot().iter()
                if element.attrib.get(
                    "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
                ) == "OptionalArtworkImage"
            ),
            encoding="unicode",
        ))
        for brand_token in (
            'x:Name="BrandTitleText"',
            'x:Name="BrandSubtitleText"',
            'x:Name="MainScrollViewer"',
            'x:Name="MainWorkspacePanel"',
        ):
            self.assertIn(brand_token, xaml_source)
        for rejected_visual in (
            'x:Name="BrandNodeMotif"',
            'x:Name="AssistantAvatarSlot"',
            'x:Name="SystemNodePathPrimary"',
            'x:Name="SystemNodePathAccent"',
            'Text="DISCOVER"',
            'Text="VERIFY"',
            'Text="PREPARE"',
            'Text="LAUNCH"',
            'Text="HIA"',
            'Text="LOCAL"',
        ):
            self.assertNotIn(rejected_visual, xaml_source)
        self.assertIn('x:Name="RightVisualRail"', xaml_source)
        self.assertIn("Update-ResponsiveLayout", wpf_source)
        self.assertIn("[System.Windows.Media.Imaging.BitmapImage]::new()", wpf_source)
        self.assertEqual(1, wpf_source.count("launcher-hero.png"))
        self.assertIn("Initialize-HiaOptionalArtwork", wpf_source)
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

    def test_wpf_workspace_layout_keeps_footer_fixed_and_scrollbars_dark(self) -> None:
        tree = ET.parse(XAML_PATH)
        root = tree.getroot()
        presentation = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
        xaml_key = "{http://schemas.microsoft.com/winfx/2006/xaml}Key"
        xaml_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
        named = {
            element.attrib.get(xaml_name): element
            for element in root.iter()
            if xaml_name in element.attrib
        }

        body = [
            child
            for child in root
            if child.tag != f"{presentation}Window.Resources"
        ]
        self.assertEqual(1, len(body))
        layout_root = body[0]
        self.assertEqual(f"{presentation}Grid", layout_root.tag)
        self.assertEqual("LayoutRoot", layout_root.attrib[xaml_name])
        columns = layout_root.find(f"{presentation}Grid.ColumnDefinitions")
        rows = layout_root.find(f"{presentation}Grid.RowDefinitions")
        self.assertIsNotNone(columns)
        self.assertIsNotNone(rows)
        self.assertEqual(
            ["42", "*"],
            [row.attrib["Height"] for row in list(rows)],
        )
        self.assertEqual(
            ["212", "*"],
            [column.attrib["Width"] for column in list(columns)],
        )
        self.assertEqual("212", named["NavigationColumn"].attrib["Width"])

        title_bar = named["CustomTitleBar"]
        navigation_rail = named["NavigationRail"]
        main_shell = named["MainShell"]
        self.assertEqual("0", title_bar.attrib["Grid.Row"])
        self.assertEqual("2", title_bar.attrib["Grid.ColumnSpan"])
        self.assertEqual("42", title_bar.attrib["Height"])
        self.assertEqual(
            "{StaticResource TitleBarGradientBrush}",
            title_bar.attrib["Background"],
        )
        self.assertEqual("1", navigation_rail.attrib["Grid.Row"])
        self.assertEqual("0", navigation_rail.attrib["Grid.Column"])
        self.assertEqual("1", main_shell.attrib["Grid.Row"])
        self.assertEqual("1", main_shell.attrib["Grid.Column"])
        main_rows = main_shell.find(f"{presentation}Grid.RowDefinitions")
        self.assertIsNotNone(main_rows)
        self.assertEqual(
            ["Auto", "Auto", "*", "Auto"],
            [row.attrib["Height"] for row in list(main_rows)],
        )

        self.assertEqual("2", named["MainScrollViewer"].attrib["Grid.Row"])
        self.assertEqual(
            "{StaticResource ModernScrollViewerStyle}",
            named["MainScrollViewer"].attrib["Style"],
        )
        self.assertEqual("Transparent", named["MainWorkspacePanel"].attrib["Background"])
        self.assertEqual("0", named["MainWorkspacePanel"].attrib["BorderThickness"])

        page_names = (
            "OverviewPage",
            "EnvironmentPage",
            "PreflightPage",
            "ReportsSettingsPage",
        )
        page_host = named["PageHost"]
        self.assertEqual(
            set(page_names),
            {
                child.attrib[xaml_name]
                for child in list(page_host)
                if xaml_name in child.attrib
            },
        )
        self.assertTrue(
            all(named[name].tag == f"{presentation}Grid" for name in page_names)
        )
        self.assertEqual(
            ["Visible", "Collapsed", "Collapsed", "Collapsed"],
            [
                named[name].attrib.get("Visibility", "Visible")
                for name in page_names
            ],
        )

        navigation_names = (
            "OverviewNavButton",
            "EnvironmentNavButton",
            "PreflightNavButton",
            "ReportsSettingsNavButton",
        )
        navigation_items = [named[name] for name in navigation_names]
        self.assertTrue(
            all(
                item.tag == f"{presentation}RadioButton"
                for item in navigation_items
            )
        )
        self.assertEqual(
            {"LauncherNavigation"},
            {item.attrib["GroupName"] for item in navigation_items},
        )
        self.assertEqual("True", navigation_items[0].attrib["IsChecked"])
        self.assertTrue(
            all("IsChecked" not in item.attrib for item in navigation_items[1:])
        )
        self.assertTrue(
            all("AutomationProperties.Name" in item.attrib for item in navigation_items)
        )

        parent_map = {
            child: parent for parent in root.iter() for child in list(parent)
        }
        cursor = named["LaunchButton"]
        ancestor_tags = []
        while parent_map[cursor] is not main_shell:
            cursor = parent_map[cursor]
            ancestor_tags.append(cursor.tag)
        self.assertEqual("FooterBar", cursor.attrib[xaml_name])
        self.assertEqual("3", cursor.attrib["Grid.Row"])
        self.assertNotIn(f"{presentation}ScrollViewer", ancestor_tags)

        right_visual = named["RightVisualRail"]
        self.assertEqual("3", right_visual.attrib["Grid.Row"])
        self.assertEqual("174", right_visual.attrib["Width"])
        self.assertNotIn("Grid.Column", right_visual.attrib)
        self.assertNotIn("Grid.RowSpan", right_visual.attrib)
        right_visual_source = ET.tostring(right_visual, encoding="unicode")
        self.assertNotIn("Canvas", right_visual_source)
        self.assertNotIn("Viewbox", right_visual_source)

        scroll_viewers = list(root.iter(f"{presentation}ScrollViewer"))
        self.assertGreaterEqual(len(scroll_viewers), 3)
        for scroll_viewer in scroll_viewers:
            self.assertEqual(
                "{StaticResource ModernScrollViewerStyle}",
                scroll_viewer.attrib["Style"],
            )

        styles = list(root.iter(f"{presentation}Style"))
        scrollbar_style = next(
            style
            for style in styles
            if style.attrib.get("TargetType") == "{x:Type ScrollBar}"
            and xaml_key not in style.attrib
        )
        scrollbar_source = ET.tostring(scrollbar_style, encoding="unicode")
        for required in (
            "ControlTemplate",
            "PART_Track",
            "ModernThumbStyle",
            'Background" Value="Transparent"',
            'BorderThickness" Value="0"',
        ):
            self.assertIn(required, scrollbar_source)
        self.assertGreaterEqual(scrollbar_source.count('Opacity="0"'), 2)
        for forbidden in ("White", "#FFFFFF", "SystemColors", "LineUpCommand"):
            self.assertNotIn(forbidden, scrollbar_source)

        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        self.assertLessEqual(
            xaml_source.count('Style="{StaticResource SectionCardStyle}"'),
            1,
        )

    def test_wpf_subtle_hover_motion_is_scoped_and_layout_neutral(self) -> None:
        tree = ET.parse(XAML_PATH)
        root = tree.getroot()
        presentation = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
        xaml_key = "{http://schemas.microsoft.com/winfx/2006/xaml}Key"
        xaml_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
        named = {
            element.attrib.get(xaml_name): element
            for element in root.iter()
            if xaml_name in element.attrib
        }
        styles = {
            element.attrib.get(xaml_key): element
            for element in root.iter(f"{presentation}Style")
            if xaml_key in element.attrib
        }
        xaml_source = XAML_PATH.read_text(encoding="utf-8")

        hover_style = styles["SubtleInteractiveCardStyle"]
        hover_source = ET.tostring(hover_style, encoding="unicode")
        self.assertEqual("Border", hover_style.attrib["TargetType"])
        self.assertIn("RenderTransformOrigin", hover_source)
        self.assertNotIn("LayoutTransform", hover_source)
        self.assertNotIn('<Setter Property="RenderTransform"', hover_source)
        self.assertIn("ScaleTransform.ScaleX", hover_source)
        self.assertIn("ScaleTransform.ScaleY", hover_source)
        self.assertNotIn("TranslateTransform", hover_source)
        self.assertNotIn("RotateTransform", hover_source)

        event_triggers = list(hover_style.iter(f"{presentation}EventTrigger"))
        self.assertEqual(
            {"MouseEnter", "MouseLeave"},
            {trigger.attrib["RoutedEvent"] for trigger in event_triggers},
        )
        animations = list(hover_style.iter(f"{presentation}DoubleAnimation"))
        self.assertEqual(4, len(animations))
        self.assertEqual(
            {"0:0:0.14", "0:0:0.16"},
            {animation.attrib["Duration"] for animation in animations},
        )
        self.assertEqual(
            {"1", "1.006"},
            {animation.attrib["To"] for animation in animations},
        )
        self.assertEqual(
            {
                "(UIElement.RenderTransform).(ScaleTransform.ScaleX)",
                "(UIElement.RenderTransform).(ScaleTransform.ScaleY)",
            },
            {
                animation.attrib["Storyboard.TargetProperty"]
                for animation in animations
            },
        )
        self.assertTrue(
            all(
                abs(float(animation.attrib["To"]) - 1.0) <= 0.0061
                for animation in animations
            )
        )
        self.assertTrue(
            all("RepeatBehavior" not in animation.attrib for animation in animations)
        )
        self.assertTrue(
            all("AutoReverse" not in animation.attrib for animation in animations)
        )
        self.assertEqual(2, xaml_source.count("<EventTrigger"))
        self.assertEqual(2, xaml_source.count("<BeginStoryboard"))
        self.assertEqual(4, xaml_source.count("<DoubleAnimation"))
        for forbidden in (
            "RepeatBehavior",
            "AutoReverse",
            "Forever",
            "BounceEase",
            "ElasticEase",
        ):
            self.assertNotIn(forbidden, xaml_source)
        for button_style in ("ActionButtonStyle", "PrimaryButtonStyle"):
            button_source = ET.tostring(styles[button_style], encoding="unicode")
            self.assertNotIn("Storyboard", button_source)
            self.assertNotIn("Animation", button_source)

        animated_cards = {
            name
            for name, element in named.items()
            if element.attrib.get("Style")
            == "{StaticResource SubtleInteractiveCardStyle}"
        }
        self.assertEqual(
            {
                "RecoveryCard",
                "OverviewQuickActionsPanel",
                "KnowledgeIndexPanel",
            },
            animated_cards,
        )
        for name in animated_cards:
            card_source = ET.tostring(named[name], encoding="unicode")
            self.assertIn("ScaleTransform", card_source)
            self.assertIn('ScaleX="1"', card_source)
            self.assertIn('ScaleY="1"', card_source)
        self.assertEqual(
            "{StaticResource PrimaryButtonStyle}",
            named["LaunchButton"].attrib["Style"],
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
        self.assertGreater(
            contrast(
                resources["DisabledPrimaryTextBrush"],
                resources["DisabledPrimaryBackgroundBrush"],
            ),
            4.5,
        )
        self.assertGreater(
            contrast(resources["TextPrimaryBrush"], "#151A2A"),
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
        primary_button_style = styles["PrimaryButtonStyle"]
        tooltip_style = styles["DarkToolTipStyle"]
        navigation_style = styles["NavigationButtonStyle"]
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
        for required in (
            "IsEnabled",
            "DisabledPrimaryBackgroundBrush",
            "DisabledPrimaryBorderBrush",
            "DisabledPrimaryTextBrush",
        ):
            self.assertIn(required, primary_button_style)
        for required in (
            "ControlTemplate",
            "Background",
            "Foreground",
            "BorderBrush",
            "Padding",
            "MaxWidth",
            "Placement",
            "TextWrapping",
        ):
            self.assertIn(required, tooltip_style)
        for required in (
            "ControlTemplate",
            "IsMouseOver",
            "IsChecked",
            "IsKeyboardFocused",
            "IsEnabled",
            "SelectedRail",
            "NavDot",
        ):
            self.assertIn(required, navigation_style)
        implicit_tooltip_styles = [
            style
            for style in root.iter(f"{presentation}Style")
            if style.attrib.get("TargetType") == "{x:Type ToolTip}"
            and xaml_key not in style.attrib
        ]
        self.assertEqual(1, len(implicit_tooltip_styles))
        self.assertEqual(
            "{StaticResource DarkToolTipStyle}",
            implicit_tooltip_styles[0].attrib["BasedOn"],
        )
        self.assertNotIn('Opacity" Value="0.42"', styles["ActionButtonStyle"])

        combo_expectations = {
            "McpBackendComboBox": "BackendPickerItemTemplate",
            "EmbeddingProfileComboBox": "EmbeddingProfilePickerItemTemplate",
            "EmbeddingDeviceComboBox": "BackendPickerItemTemplate",
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
        embedding_template = templates["EmbeddingProfilePickerItemTemplate"]
        self.assertGreaterEqual(embedding_template.count("TextBlock"), 2)
        self.assertIn("display", embedding_template)
        self.assertIn("detail", embedding_template)
        self.assertIn("tooltip", embedding_template)
        self.assertIn("PickerTextBrush", embedding_template)
        self.assertIn("PickerSecondaryTextBrush", embedding_template)

        tab_indices = sorted(
            int(element.attrib["TabIndex"])
            for element in root.iter()
            if "TabIndex" in element.attrib
        )
        self.assertEqual(list(range(39)), tab_indices)
        expected_tab_order = {
            "OverviewNavButton": 0,
            "EnvironmentNavButton": 1,
            "PreflightNavButton": 2,
            "ReportsSettingsNavButton": 3,
            "RecoverCheckpointOption": 4,
            "NormalLaunchOption": 5,
            "QuickRescanButton": 6,
            "QuickRepairButton": 7,
            "QuickCleanupScreenshotsButton": 8,
            "QuickOpenReportButton": 9,
            "QuickCopyReportButton": 10,
            "McpBackendComboBox": 11,
            "EmbeddingProfileComboBox": 12,
            "EmbeddingDeviceComboBox": 13,
            "HoudiniComboBox": 14,
            "BrowseHoudiniButton": 15,
            "BridgePythonComboBox": 16,
            "BrowseBridgeButton": 17,
            "RenderOutputTextBox": 18,
            "BrowseRenderOutputButton": 19,
            "CopyActivationCommandButton": 20,
            "RepairKnowledgeEnvironmentButton": 21,
            "KnowledgeEnvironmentLogExpander": 22,
            "ImportKnowledgeFileButton": 23,
            "ImportKnowledgeFolderButton": 24,
            "RefreshKnowledgeSourcesButton": 25,
            "KnowledgeSourcesList": 26,
            "DeleteKnowledgeSourceButton": 27,
            "RescanKnowledgeSourcesButton": 28,
            "KnowledgeIndexActionButton": 29,
            "RefreshCacheButton": 30,
            "CacheCategoriesList": 31,
            "CleanupScreenshotsButton": 32,
            "ReportPathTextBox": 33,
            "RescanButton": 34,
            "RepairButton": 35,
            "OpenReportButton": 36,
            "CopyReportButton": 37,
            "LaunchButton": 38,
        }
        named_tab_order = {
            element.attrib[xaml_name]: int(element.attrib["TabIndex"])
            for element in root.iter()
            if xaml_name in element.attrib and "TabIndex" in element.attrib
        }
        self.assertEqual(expected_tab_order, named_tab_order)
        self.assertEqual(
            len(named_tab_order),
            len(set(named_tab_order.values())),
        )
        self.assertLessEqual(int(root.attrib["MinWidth"]), 640)
        self.assertLessEqual(int(root.attrib["MinHeight"]), 480)
        self.assertEqual("Cycle", root.attrib["KeyboardNavigation.TabNavigation"])
        self.assertEqual("True", root.attrib["UseLayoutRounding"])
        self.assertEqual("True", root.attrib["SnapsToDevicePixels"])
        self.assertEqual("Ideal", root.attrib["TextOptions.TextFormattingMode"])
        self.assertIsNotNone(next(root.iter(f"{presentation}ScrollViewer"), None))
        self.assertIsNotNone(next(root.iter(f"{presentation}WrapPanel"), None))
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("[System.Windows.SystemParameters]::WorkArea", wpf_source)
        self.assertIn("$window.Width = [Math]::Min", wpf_source)
        self.assertIn("$window.Height = [Math]::Min", wpf_source)
        self.assertIn("$script:compactLayout = $null", wpf_source)

        named = {
            element.attrib.get(xaml_name): element
            for element in root.iter()
            if xaml_name in element.attrib
        }
        for name in (
            "MinimizeWindowButton",
            "MaximizeWindowButton",
            "CloseWindowButton",
        ):
            button = named[name]
            self.assertEqual(
                "{StaticResource "
                + (
                    "CloseCaptionButtonStyle"
                    if name == "CloseWindowButton"
                    else "CaptionButtonStyle"
                )
                + "}",
                button.attrib["Style"],
            )
            self.assertIn("AutomationProperties.Name", button.attrib)

        caption_style_source = styles["CaptionButtonStyle"]
        self.assertIn('Property="Focusable" Value="False"', caption_style_source)
        self.assertIn('Property="IsTabStop" Value="False"', caption_style_source)

        self.assertEqual("True", named["LaunchButton"].attrib["IsDefault"])
        self.assertEqual(
            "{StaticResource PrimaryButtonStyle}",
            named["LaunchButton"].attrib["Style"],
        )
        primary_buttons = [
            element.attrib.get(xaml_name)
            for element in root.iter(f"{presentation}Button")
            if element.attrib.get("Style")
            == "{StaticResource PrimaryButtonStyle}"
        ]
        self.assertEqual(["LaunchButton"], primary_buttons)
        for secondary_name in (
            "KnowledgeIndexActionButton",
            "QuickRescanButton",
            "QuickRepairButton",
            "QuickCleanupScreenshotsButton",
            "QuickOpenReportButton",
            "QuickCopyReportButton",
            "RescanButton",
            "RepairButton",
            "CleanupScreenshotsButton",
            "OpenReportButton",
            "CopyReportButton",
        ):
            self.assertEqual(
                "{StaticResource ActionButtonStyle}",
                named[secondary_name].attrib["Style"],
            )

        for event_binding in (
            "$minimizeWindowButton.Add_Click",
            "$maximizeWindowButton.Add_Click",
            "$closeWindowButton.Add_Click",
            "$window.Add_StateChanged",
            "$overviewNavButton.Add_Click",
            "$environmentNavButton.Add_Click",
            "$preflightNavButton.Add_Click",
            "$reportsSettingsNavButton.Add_Click",
            "$rescanButton.Add_Click",
            "$knowledgeIndexActionButton.Add_Click",
            "$mcpBackendCombo.Add_SelectionChanged",
            "$houdiniCombo.Add_SelectionChanged",
            "$bridgeCombo.Add_SelectionChanged",
            "$renderOutputTextBox.Add_TextChanged",
            "$browseHoudiniButton.Add_Click",
            "$browseBridgeButton.Add_Click",
            "$browseRenderOutputButton.Add_Click",
            "$repairButton.Add_Click",
            "$cleanupScreenshotsButton.Add_Click",
            "$openReportButton.Add_Click",
            "$copyReportButton.Add_Click",
            "$copyActivationCommandButton.Add_Click",
            "$quickRescanButton.Add_Click",
            "$quickRepairButton.Add_Click",
            "$quickCleanupScreenshotsButton.Add_Click",
            "$quickOpenReportButton.Add_Click",
            "$quickCopyReportButton.Add_Click",
            "$launchButton.Add_Click",
            "$window.Add_SizeChanged",
            "$window.Add_ContentRendered",
        ):
            self.assertEqual(1, wpf_source.count(event_binding), event_binding)
        self.assertEqual(
            1,
            wpf_source.count("function Set-HiaLauncherPage"),
        )

    def test_overview_quick_actions_reuse_handlers_and_show_canonical_venv(self) -> None:
        xaml_source = XAML_PATH.read_text(encoding="utf-8")
        wpf_source = WPF_SCRIPT_PATH.read_text(encoding="utf-8-sig")
        core_source = MODULE_PATH.read_text(encoding="utf-8-sig")
        managed_environment_message = (
            "HIA Python 环境位于项目根目录 .venv；"
            "受管 CPython、uv、模型与缓存位于 .runtime。"
        )
        self.assertGreaterEqual(wpf_source.count(managed_environment_message), 3)
        self.assertEqual(2, core_source.count(managed_environment_message))
        self.assertNotIn(
            ".runtime/toolchains/hia-embedding/venv",
            xaml_source + wpf_source,
        )
        self.assertIn(r".\.venv\Scripts\Activate.ps1", xaml_source)
        self.assertIn("$script:embeddingData.layout.venv_root", wpf_source)
        self.assertIn("$script:embeddingData.layout.activation_script", wpf_source)
        self.assertIn("Join-Path $projectRoot '.venv'", wpf_source)
        self.assertIn(
            "Join-Path $projectRoot '.venv\\Scripts\\Activate.ps1'",
            wpf_source,
        )

        quick_rescan = wpf_source[
            wpf_source.index("$quickRescanButton.Add_Click"):
            wpf_source.index("$quickRepairButton.Add_Click")
        ]
        quick_repair = wpf_source[
            wpf_source.index("$quickRepairButton.Add_Click"):
            wpf_source.index("$quickCleanupScreenshotsButton.Add_Click")
        ]
        quick_cleanup = wpf_source[
            wpf_source.index("$quickCleanupScreenshotsButton.Add_Click"):
            wpf_source.index("$quickOpenReportButton.Add_Click")
        ]
        quick_open = wpf_source[
            wpf_source.index("$quickOpenReportButton.Add_Click"):
            wpf_source.index("$quickCopyReportButton.Add_Click")
        ]
        quick_copy = wpf_source[
            wpf_source.index("$quickCopyReportButton.Add_Click"):
            wpf_source.index("$launchButton.Add_Click")
        ]
        self.assertIn("$rescanButton.RaiseEvent", quick_rescan)
        self.assertIn("$repairButton.RaiseEvent", quick_repair)
        self.assertIn("Invoke-HiaCacheCleanup -CategoryIds @('screenshots')", quick_cleanup)
        self.assertIn("$openReportButton.RaiseEvent", quick_open)
        self.assertIn("$copyReportButton.RaiseEvent", quick_copy)
        self.assertEqual(1, wpf_source.count("function Invoke-HiaCacheCleanup"))
        self.assertIn(
            "Invoke-HiaCacheCleanup -CategoryIds @(",
            wpf_source[
                wpf_source.index("$cleanupScreenshotsButton.Add_Click"):
                wpf_source.index("$openReportButton.Add_Click")
            ],
        )

        update_repair = wpf_source[
            wpf_source.index("function Update-RepairButton"):
            wpf_source.index("function New-CheckView")
        ]
        self.assertIn("'环境无需修复'", update_repair)
        self.assertIn("$quickRepairButton.IsEnabled = $repairButton.IsEnabled", update_repair)
        report_actions = wpf_source[
            wpf_source.index("function Update-HiaReportActions"):
            wpf_source.index("function Initialize-HiaOptionalArtwork")
        ]
        for control in (
            "$openReportButton",
            "$copyReportButton",
            "$quickOpenReportButton",
            "$quickCopyReportButton",
        ):
            self.assertIn(f"{control}.IsEnabled = $enabled", report_actions)
        busy_state = wpf_source[
            wpf_source.index("function Set-BusyState"):
            wpf_source.index("function Test-CurrentRedCheck")
        ]
        self.assertIn("$quickRescanButton.IsEnabled = -not $Busy", busy_state)
        self.assertIn("$copyActivationCommandButton.IsEnabled = -not $Busy", busy_state)
        self.assertIn("Update-HiaReportActions", busy_state)

    def test_quick_action_disabled_states_are_derived_from_shared_state(self) -> None:
        output = self.run_powershell(
            f"""
Add-Type -AssemblyName PresentationFramework
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WPF_SCRIPT_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'WPF script did not parse.' }}
$required = @(
    'Get-HiaKnowledgeEnvironmentAction',
    'Test-CurrentRedCheck',
    'Test-CurrentNonGreenCheck',
    'Update-RepairButton',
    'Test-HiaLatestReportAvailable',
    'Update-HiaReportActions',
    'Update-HiaCacheActions'
)
$definitions = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -in $required
}}, $true))
foreach ($name in $required) {{
    $definition = @($definitions | Where-Object Name -eq $name)
    if ($definition.Count -ne 1) {{ throw "Missing function: $name" }}
    . ([scriptblock]::Create($definition[0].Extent.Text))
}}
function Get-ComboEmbeddingProfile {{ return '' }}
$repairButton = [System.Windows.Controls.Button]::new()
$quickRepairButton = [System.Windows.Controls.Button]::new()
$openReportButton = [System.Windows.Controls.Button]::new()
$copyReportButton = [System.Windows.Controls.Button]::new()
$quickOpenReportButton = [System.Windows.Controls.Button]::new()
$quickCopyReportButton = [System.Windows.Controls.Button]::new()
$cleanupScreenshotsButton = [System.Windows.Controls.Button]::new()
$quickCleanupScreenshotsButton = [System.Windows.Controls.Button]::new()
$cacheCategoriesList = [System.Windows.Controls.ListBox]::new()
$script:isBusy = $false
$script:preflightFailed = $false
$script:knowledgeEnvironmentLastFailure = ''
$script:embeddingData = $null
$script:lastReportPath = ''
$script:cachePreview = $null
$script:knowledgeEnvironmentStatus = [pscustomobject]@{{
    state = 'ready'
    models = [pscustomobject]@{{ items = @() }}
    torch = [pscustomobject]@{{ installed = $false }}
    embedding_worker = [pscustomobject]@{{ installed = $false }}
}}
$script:currentResult = [pscustomobject]@{{
    overall = 'green'
    checks = @()
}}
Update-RepairButton
Update-HiaReportActions
Update-HiaCacheActions
[pscustomobject]@{{
    repair_label = [string]$repairButton.Content
    repair_enabled = [bool]$repairButton.IsEnabled
    quick_repair_label = [string]$quickRepairButton.Content
    quick_repair_enabled = [bool]$quickRepairButton.IsEnabled
    open_enabled = [bool]$openReportButton.IsEnabled
    copy_enabled = [bool]$copyReportButton.IsEnabled
    quick_open_enabled = [bool]$quickOpenReportButton.IsEnabled
    quick_copy_enabled = [bool]$quickCopyReportButton.IsEnabled
    cleanup_enabled = [bool]$cleanupScreenshotsButton.IsEnabled
    quick_cleanup_enabled = [bool]$quickCleanupScreenshotsButton.IsEnabled
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual("环境无需修复", payload["repair_label"])
        self.assertEqual(payload["repair_label"], payload["quick_repair_label"])
        for key, value in payload.items():
            if key.endswith("_enabled"):
                self.assertFalse(value, key)

    def test_optional_artwork_and_recovery_ui_are_nonblocking_and_explicit(self) -> None:
        tree = ET.parse(XAML_PATH)
        root = tree.getroot()
        xaml_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
        named = {
            element.attrib.get(xaml_name): element
            for element in root.iter()
            if xaml_name in element.attrib
        }
        self.assertIn("OptionalArtworkPanel", named)
        self.assertIn("OptionalArtworkImage", named)
        self.assertEqual("Collapsed", named["OptionalArtworkPanel"].attrib["Visibility"])
        self.assertEqual("False", named["OptionalArtworkPanel"].attrib["IsHitTestVisible"])
        self.assertEqual(
            "UniformToFill",
            named["OptionalArtworkImage"].attrib["Stretch"],
        )
        self.assertNotIn("Source", named["OptionalArtworkImage"].attrib)
        xaml_source = XAML_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("BIG-CHICKEN", xaml_source)
        self.assertIn("Houdini Intelligence Agent", xaml_source)
        self.assertEqual(
            "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}TextBlock",
            named["BrandTitleText"].tag,
        )
        self.assertEqual(
            "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}TextBlock",
            named["BrandSubtitleText"].tag,
        )
        self.assertNotIn("BrandNodeMotif", named)
        self.assertNotIn("AssistantAvatarSlot", named)
        header_source = ET.tostring(named["MainShellHeader"], encoding="unicode")
        overview_source = ET.tostring(named["OverviewPage"], encoding="unicode")
        right_rail_source = ET.tostring(named["RightVisualRail"], encoding="unicode")
        for abstract_visual in ("Canvas", "Path", "Viewbox"):
            self.assertNotIn(abstract_visual, header_source)
            self.assertNotIn(abstract_visual, overview_source)
            self.assertNotIn(abstract_visual, right_rail_source)
        for forbidden_brand_element in (
            "ChickenHead",
            "ChickenComb",
            "ChickenBeak",
            "ChickenEye",
            "Mascot",
            "CharacterArtwork",
            "BrandNodeMotif",
            "AssistantAvatarSlot",
        ):
            self.assertNotIn(forbidden_brand_element, xaml_source)
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
        artwork_function = wpf_source[
            wpf_source.index("function Initialize-HiaOptionalArtwork"):
            wpf_source.index("function Set-OverallState")
        ]
        for required in (
            "$projectRoot",
            "'assets'",
            "'launcher'",
            "'launcher-hero.png'",
            "BitmapCacheOption]::OnLoad",
            "BitmapCreateOptions]::IgnoreImageCache",
            "FileAttributes]::ReparsePoint",
            "$optionalArtworkPanel.Visibility = [System.Windows.Visibility]::Visible",
            "$optionalArtworkPanel.Visibility = [System.Windows.Visibility]::Collapsed",
        ):
            self.assertIn(required, artwork_function)
        self.assertIn("try {", artwork_function)
        self.assertIn("} catch {", artwork_function)
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
        self.assertEqual("0.1.1-preview", properties["Version"])
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
            in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".gif",
                ".bmp",
                ".ico",
                ".svg",
                ".mp4",
            }
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

        login = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Mta",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(fake_root / "scripts" / "hia-launcher.ps1"),
                "-PrintCodexLoginCommand",
                "-Json",
            ],
            cwd=fake_root,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, login.returncode, login.stderr)
        login_payload = json.loads(login.stdout)
        self.assertEqual("hia-launcher-cli/1", login_payload["schema"])
        self.assertEqual("codex-login-command", login_payload["action"])
        self.assertIn("login --device-auth", login_payload["command"])

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
        self.assertIn("[switch]$PrintCodexLoginCommand", launcher_source)
        self.assertIn("HiaLauncher.Wpf.ps1", launcher_source)
        self.assertIn("HiaLauncher.xaml", wpf_source)
        for control_name in (
            "McpBackendComboBox",
            "EmbeddingProfileComboBox",
            "RescanButton",
            "RepairButton",
            "CleanupScreenshotsButton",
            "OpenReportButton",
            "CopyReportButton",
            "QuickRescanButton",
            "QuickRepairButton",
            "QuickCleanupScreenshotsButton",
            "QuickOpenReportButton",
            "QuickCopyReportButton",
            "LaunchButton",
            "RenderOutputTextBox",
            "BrowseRenderOutputButton",
            "KnowledgeIndexPanel",
            "KnowledgeIndexActionButton",
            "KnowledgeEnvironmentStatusText",
            "EnvironmentActivationCommandText",
            "CopyActivationCommandButton",
            "RepairKnowledgeEnvironmentButton",
            "KnowledgeEnvironmentReasonText",
            "KnowledgeEnvironmentProgressBar",
            "KnowledgeEnvironmentStageText",
            "KnowledgeEnvironmentLogExpander",
            "KnowledgeEnvironmentLogPathText",
            "KnowledgeEnvironmentLogTextBox",
            "ImportKnowledgeFileButton",
            "ImportKnowledgeFolderButton",
            "KnowledgeSourcesList",
            "DeleteKnowledgeSourceButton",
            "CacheCategoriesList",
            "RefreshCacheButton",
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
        self.assertIn("[AllowEmptyString()][string]$EmbeddingProfile = ''", launcher_source)
        self.assertIn("'HIA_RENDER_OUTPUT_DIR'", launcher_source)
        self.assertNotIn("'-RenderOutputDir'", launcher_source)
        self.assertNotIn("$env:HIA_RENDER_OUTPUT_DIR =", combined)
        self.assertIn("Invoke-HiaLauncherJsonCli", wpf_source)
        self.assertIn("'hia-cache.ps1'", wpf_source)
        self.assertIn("'hia-knowledge.ps1'", wpf_source)
        self.assertNotIn("Invoke-HiaScreenshotCacheCleanup", wpf_source)
        environment_completion = wpf_source[
            wpf_source.index("function Complete-HiaKnowledgeEnvironmentRepair"):
            wpf_source.index("$script:knowledgeEnvironmentTimer.Add_Tick")
        ]
        self.assertIn(
            "[string]$script:knowledgeEnvironmentStatus.state -ne 'ready'",
            environment_completion,
        )
        self.assertLess(
            environment_completion.index(
                "[string]$script:knowledgeEnvironmentStatus.state -ne 'ready'"
            ),
            environment_completion.index(
                "项目本地 Python、uv 与文档解析环境已完成验证"
            ),
        )
        self.assertIn("Start-HiaCodexBootstrap", wpf_source)
        self.assertIn("Start-HiaEmbeddingInstall", wpf_source)
        self.assertIn("Write-HiaEmbeddingPreference", wpf_source)
        self.assertIn("FTS5 无需重建", wpf_source)
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
        self.assertIn("$sourceSessionCheckpoints", source)
        self.assertIn("$validatedRecoveryCheckpoint", source)
        self.assertIn(
            "Recovery checkpoint is not bound to the selected launcher session.",
            source,
        )
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
        self.assertIn("'HIA_MCP_V2_EXECUTOR_PATH' = $hiaMcpExecutorSource", hia_branch)
        self.assertIn("'HIA_LAUNCHER_SESSION_ID' = $sessionId", hia_branch)
        self.assertIn("'HIA_MCP_V2_AUTOSTART'", hia_branch)
        self.assertNotIn("fxhoudinimcp", hia_branch.casefold())
        self.assertNotIn("FXHOUDINIMCP_", hia_branch)

        self.assertIn(".runtime\\fxhoudinimcp\\1.3.0", fallback_branch)
        self.assertIn("'FXHOUDINIMCP_AUTOSTART' = '1'", fallback_branch)
        self.assertIn("'FXHOUDINIMCP_TOKEN' = $houdiniMcpToken", fallback_branch)
        self.assertNotIn("HIA_MCP_V2_", fallback_branch)

    def test_uiready_starts_only_the_selected_backend_and_checks_hia_readiness(self) -> None:
        sources = [path.read_text(encoding="utf-8") for path in UI_READY_PATHS]
        self.assertTrue(sources)
        self.assertEqual(1, len(set(sources)))
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
        executor_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "executor.py"
        )
        hia_environment = {
            "HIA_MCP_BACKEND": "hia_v2",
            "HIA_MCP_V2_AUTOSTART": "1",
            "HIA_PROJECT_ROOT": str(REPOSITORY_ROOT),
            "HIA_MCP_V2_RUNTIME_DIR": str(runtime_directory),
            "HIA_MCP_V2_HOST": "127.0.0.1",
            "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
            "HIA_MCP_V2_PORT": "45123",
            "HIA_MCP_V2_TOKEN": "T" * 48,
            "HIA_MCP_V2_EXECUTOR_PATH": str(executor_path),
            "HIA_LAUNCHER_SESSION_ID": "1" * 32,
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
        self.assertEqual("1" * 32, hia_calls[0]["launcher_session_id"])
        self.assertEqual(
            executor_path.resolve(),
            hia_calls[0]["expected_executor_path"],
        )
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
