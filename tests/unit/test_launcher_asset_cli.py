from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
CORE_CLI = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_mcp_runtime"
    / "knowledge_index_cli.py"
)
MODULE_PATH = (
    REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
)
WRAPPER_PATH = REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
REPAIR_HELPER = REPOSITORY_ROOT / "scripts" / "repair_hia_assets.py"
WPF_PATH = (
    REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Wpf.ps1"
)
XAML_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.xaml"


def _powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise unittest.SkipTest("PowerShell is unavailable")


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class LauncherAssetCliTests(unittest.TestCase):
    def run_powershell(self, script: str) -> str:
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "[Console]::OutputEncoding = "
                    "[System.Text.UTF8Encoding]::new($false)\n"
                    f"Import-Module {_ps_literal(MODULE_PATH)} -Force\n"
                    f"{script}"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        return completed.stdout.strip()

    def test_headless_assets_capabilities_emits_terminal_json(self) -> None:
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(CORE_CLI),
                "--project-root",
                str(REPOSITORY_ROOT),
                "--format",
                "json",
                "assets",
                "capabilities",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), completed.stdout)
        payload = json.loads(lines[0])
        self.assertEqual("completed", payload["event"])
        self.assertEqual("assets.capabilities", payload["action"])
        self.assertIn("extractors", payload["result"])
        self.assertIn("commands", payload["result"])

    def test_headless_powershell_wrapper_forwards_capabilities_json(self) -> None:
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WRAPPER_PATH),
                "assets",
                "capabilities",
                "-BootstrapPython",
                sys.executable,
                "-Json",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), completed.stdout)
        payload = json.loads(lines[0])
        self.assertEqual("completed", payload["event"])
        self.assertEqual("assets.capabilities", payload["action"])

    def test_asset_repair_rejects_non_managed_python_before_download(self) -> None:
        test_root = REPOSITORY_ROOT / ".runtime" / "test-runs"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "hia-knowledge.ps1").write_text(
                "# fixture\n",
                encoding="utf-8",
            )
            runtime = (
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
            )
            runtime.mkdir(parents=True)
            (runtime / "local_extractors.py").write_text(
                "# fixture\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(REPAIR_HELPER),
                    "--project-root",
                    str(root),
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(1, completed.returncode)
            lines = [
                line for line in completed.stdout.splitlines() if line.strip()
            ]
            self.assertEqual(1, len(lines), completed.stdout)
            payload = json.loads(lines[0])
            self.assertEqual("assets.repair", payload["action"])
            self.assertEqual("error", payload["event"])
            self.assertEqual(
                "MANAGED_PYTHON_REQUIRED",
                payload["error"]["code"],
            )
            self.assertFalse(
                (root / ".runtime" / "cache" / "assets-repair").exists()
            )

    def test_process_plan_forwards_the_complete_nested_cli(self) -> None:
        output = self.run_powershell(
            f"""
$plans = @(
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action capabilities
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action import -Path 'D:\\media\\long tutorial.mp4'
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action list -Offset 5 -Limit 25
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action status -AssetId 'asset-123'
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action resume -AssetId 'asset-123'
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action delete -AssetId 'asset-123'
    New-HiaAssetCliProcessPlan -ProjectRoot {_ps_literal(REPOSITORY_ROOT)} -Action repair
)
@($plans) | ConvertTo-Json -Depth 8 -Compress
"""
        )
        plans = json.loads(output)
        self.assertEqual(
            [
                "assets.capabilities",
                "assets.import",
                "assets.list",
                "assets.status",
                "assets.resume",
                "assets.delete",
                "assets.repair",
            ],
            [plan["action"] for plan in plans],
        )
        for plan, action in zip(
            plans,
            (
                "capabilities",
                "import",
                "list",
                "status",
                "resume",
                "delete",
                "repair",
            ),
            strict=True,
        ):
            arguments = plan["arguments"]
            self.assertIn("hia-knowledge.ps1", arguments[arguments.index("-File") + 1])
            nested = arguments.index("assets")
            self.assertEqual(action, arguments[nested + 1])
            self.assertEqual(str(REPOSITORY_ROOT), plan["working_directory"])
            expected_protocol = (
                "hia-asset-repair-jsonl/1"
                if action == "repair"
                else "hia-knowledge-index-jsonl/1"
            )
            self.assertEqual(expected_protocol, plan["protocol"])
        self.assertIn("-Path", plans[1]["arguments"])
        self.assertIn("-Offset", plans[2]["arguments"])
        self.assertIn("-Limit", plans[2]["arguments"])
        for plan in plans[3:6]:
            self.assertIn("-AssetId", plan["arguments"])

    def test_jsonl_parser_accepts_contract_and_normalizes_legacy_status(
        self,
    ) -> None:
        contract_asset = {
            "id": "asset-contract",
            "title": "Contract",
            "kind": "video",
            "stage": "vector",
            "status": "paused",
            "processed_units": 7,
            "total_units": 10,
            "fragments": 21,
            "lexical": {"status": "ready", "processed": 21, "total": 21},
            "vector": {"status": "partial", "processed": 13, "total": 21},
            "error": "",
            "recoverable": True,
        }
        legacy_asset = {
            "asset_id": "asset-legacy",
            "title": "Legacy",
            "status": "processing",
            "extractor": "builtin_text",
            "source_name": "notes.md",
            "source_suffix": ".md",
            "source_size_bytes": 100,
            "source_sha256": "a" * 64,
            "fragment_count": 4,
            "source_key_count": 4,
            "next_fragment": 4,
            "checkpoint_state": "processing",
            "documents_indexed": 4,
            "chunks_indexed": 5,
            "vectors_indexed": 3,
            "vector_pending_chunks": 2,
            "created_at": "2026-07-30T00:00:00Z",
            "updated_at": "2026-07-30T00:01:00Z",
        }
        contract_line = json.dumps(
            {
                "protocol": "hia-knowledge-index-jsonl/1",
                "event": "completed",
                "action": "assets.list",
                "result": {"items": [contract_asset], "total": 1},
            },
            ensure_ascii=False,
        )
        legacy_line = json.dumps(
            {
                "protocol": "hia-knowledge-index-jsonl/1",
                "event": "completed",
                "action": "assets.status",
                "result": legacy_asset,
                "asset": legacy_asset,
            },
            ensure_ascii=False,
        )
        repair_line = json.dumps(
            {
                "protocol": "hia-asset-repair-jsonl/1",
                "event": "started",
                "action": "assets.repair",
                "stage": "package",
                "profile": "local-ocr-asr",
            },
            ensure_ascii=False,
        )
        output = self.run_powershell(
            f"""
$contract = ConvertFrom-HiaAssetJsonLine -Line {_ps_literal(contract_line)}
$legacy = ConvertFrom-HiaAssetJsonLine -Line {_ps_literal(legacy_line)}
$repair = ConvertFrom-HiaAssetJsonLine -Line {_ps_literal(repair_line)}
[pscustomobject]@{{
    contract = $contract.result.items[0]
    legacy = $legacy.asset
    repair_event = $repair.event
    repair_action = $repair.action
}} | ConvertTo-Json -Depth 10 -Compress
"""
        )
        payload = json.loads(output)
        self.assertEqual(contract_asset, payload["contract"])
        legacy = payload["legacy"]
        self.assertEqual("asset-legacy", legacy["id"])
        self.assertEqual("md", legacy["kind"])
        self.assertEqual("running", legacy["status"])
        self.assertEqual(4, legacy["processed_units"])
        self.assertEqual(4, legacy["total_units"])
        self.assertEqual("ready", legacy["lexical"]["status"])
        self.assertEqual("partial", legacy["vector"]["status"])
        self.assertTrue(legacy["recoverable"])
        self.assertEqual("start", payload["repair_event"])
        self.assertEqual("assets.repair", payload["repair_action"])

    def test_wpf_file_filter_reads_the_cli_capability_maps(self) -> None:
        output = self.run_powershell(
            f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WPF_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'WPF script did not parse.' }}
foreach ($name in @(
    'Get-HiaAssetPropertyValue',
    'Get-HiaAssetCapabilityGroupLabel',
    'Get-HiaAssetCapabilitySummary',
    'Get-HiaAssetFileDialogFilter'
)) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "Missing function: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}
$script:knowledgeAssetCapabilities = @'
{{
  "formats": {{
    ".pdf": {{"adapter": "pdf", "status": "dependency_missing"}},
    ".mp4": {{"adapter": "media", "status": "available"}}
  }},
  "capabilities": {{
    "office": {{"formats": [".docx", ".pptx"]}},
    "legacy": {{"suffixes": [".txt"]}}
  }},
  "extractors": [
    {{"name": "stdlib_text", "status": "available", "formats": [".srt"]}},
    {{"name": "pypdf", "status": "dependency_missing", "formats": [".pdf"]}},
    {{"name": "rapidocr", "status": "dependency_missing", "formats": [".png"]}},
    {{"name": "faster_whisper", "status": "available", "formats": [".wav"]}}
  ]
}}
'@ | ConvertFrom-Json
[pscustomobject]@{{
    filter = Get-HiaAssetFileDialogFilter
    summary = Get-HiaAssetCapabilitySummary
}} | ConvertTo-Json -Compress
"""
        )
        payload = json.loads(output)
        for extension in (".pdf", ".mp4", ".docx", ".pptx", ".txt", ".srt"):
            self.assertIn(f"*{extension}", payload["filter"])
        self.assertEqual(
            "文档 部分 · OCR 待安装 · ASR 可用",
            payload["summary"],
        )

    def test_wrapper_and_wpf_use_streaming_cli_without_a_job_service(self) -> None:
        wrapper = WRAPPER_PATH.read_text(encoding="utf-8-sig")
        wpf = WPF_PATH.read_text(encoding="utf-8-sig")
        xaml = XAML_PATH.read_text(encoding="utf-8")
        repair_branch = wrapper[
            wrapper.index(
                "if ($Action -eq 'assets' -and $AssetAction -eq 'repair')"
            ):
            wrapper.index("if ($Action -eq 'assets')")
        ]
        asset_branch = wrapper[
            wrapper.index("if ($Action -eq 'assets')"):
            wrapper.index("if ($Action -eq 'index-build')")
        ]
        for marker in (
            "'capabilities'",
            "'import'",
            "'list'",
            "'status'",
            "'resume'",
            "'delete'",
            "'repair'",
            "Invoke-HiaKnowledgeStreamingProcess",
            "[Console]::Out.Flush()",
        ):
            self.assertIn(marker, wrapper)
        self.assertIn("$indexCliPath", asset_branch)
        self.assertIn("$assetRepairPath", repair_branch)
        self.assertIn("$repairArguments", repair_branch)
        self.assertIn("'assets.repair'", repair_branch)
        self.assertIn("Invoke-HiaKnowledgeStreamingProcess", repair_branch)
        self.assertNotIn("$helperPath", asset_branch)

        for marker in (
            "New-HiaAssetCliProcessPlan",
            "ConvertFrom-HiaAssetJsonLine",
            "$script:knowledgeAssetProcessTimer",
            "$script:knowledgeAssetPollTimer",
            "ReadLineAsync()",
            "Stop-HiaAssetProcess",
            "已完成 unit 和 manifest",
            "$script:knowledgeAssetProcess.Refresh()",
            "-not $script:knowledgeAssetProcess.HasExited",
            "$null -eq $script:knowledgeAssetCapabilities",
            "$succeeded",
            "Test-HiaAssetRepairAvailable",
            "Test-HiaAssetRepairNeeded",
            "Start-HiaAssetProcess -Action 'repair'",
            "[string]$Event.action -eq 'assets.repair'",
            "正在安装项目本地 OCR、ASR、模型与 FFmpeg",
            "Start-HiaAssetProcess -Action 'capabilities'",
        ):
            self.assertIn(marker, wpf)
        for forbidden in ("Bridge job", "asset service", "Start-Job"):
            self.assertNotIn(forbidden, wpf)
        for control in (
            "KnowledgeAssetsPanel",
            "KnowledgeAssetsSummaryText",
            "ImportKnowledgeAssetButton",
            "KnowledgeAssetsList",
            "KnowledgeAssetProgressText",
            "KnowledgeAssetActionButton",
            "DeleteKnowledgeAssetButton",
        ):
            self.assertIn(f'x:Name="{control}"', xaml)
        self.assertIn("资料加工", xaml)
        self.assertIn("暂停或继续所选资料加工", xaml)


if __name__ == "__main__":
    unittest.main()
