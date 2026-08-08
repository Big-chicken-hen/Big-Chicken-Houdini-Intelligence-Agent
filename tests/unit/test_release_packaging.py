from __future__ import annotations

import json
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
BOOTSTRAP_PATH = REPOSITORY_ROOT / "scripts" / "bootstrap-runtime.ps1"
BUILD_RELEASE_PATH = REPOSITORY_ROOT / "scripts" / "build-release.ps1"
PUBLIC_CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "check-public-release.py"
KNOWLEDGE_PACK_PATH = REPOSITORY_ROOT / "knowledge" / "sidefx-official"
README_PATH = REPOSITORY_ROOT / "README.md"
INSTALLATION_PATH = REPOSITORY_ROOT / "docs" / "INSTALLATION.md"
TEST_REPORT_PATH = REPOSITORY_ROOT / "docs" / "TEST-REPORT.md"
GITIGNORE_PATH = REPOSITORY_ROOT / ".gitignore"
HIA_MCP_V2_PATH = REPOSITORY_ROOT / "docs" / "HIA-MCP-V2.md"
XAML_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.xaml"
WPF_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Wpf.ps1"
CORE_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
LAUNCHER_PATH = REPOSITORY_ROOT / "scripts" / "hia-launcher.ps1"
CS_PROJECT_PATH = (
    REPOSITORY_ROOT
    / "launcher"
    / "HoudiniIntelligenceLauncher"
    / "HoudiniIntelligenceLauncher.csproj"
)


class ReleasePackagingTests(unittest.TestCase):
    def test_knowledge_cli_documentation_uses_project_managed_venv(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        hia_mcp_v2 = HIA_MCP_V2_PATH.read_text(encoding="utf-8")
        managed_command = (
            r".\.venv\Scripts\python.exe -B "
            r".\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py"
        )
        install_command = (
            r"powershell -File .\scripts\hia-knowledge.ps1 environment-install"
        )

        self.assertEqual(1, readme.count(managed_command))
        self.assertEqual(13, hia_mcp_v2.count(managed_command))
        for source in (readme, hia_mcp_v2):
            self.assertIn(install_command, source)
            self.assertNotIn(
                "\npython -B "
                r".\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py",
                source,
            )

    def test_release_powershell_scripts_parse(self) -> None:
        for path in (BOOTSTRAP_PATH, BUILD_RELEASE_PATH):
            command = (
                "$errors=$null; "
                "[Management.Automation.Language.Parser]::ParseFile("
                f"'{str(path).replace(chr(39), chr(39) * 2)}',"
                "[ref]$null,[ref]$errors)|Out-Null; "
                "if($errors.Count){$errors|ForEach-Object Message; exit 1}"
            )
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_codex_bootstrap_is_pinned_and_project_local(self) -> None:
        source = BOOTSTRAP_PATH.read_text(encoding="utf-8-sig")
        for required in (
            "rust-v0.144.3/codex-x86_64-pc-windows-msvc.exe.zip",
            "5490114D8684B30F91E6E6F7B1238B2544FA3B957E42C9836AA959E8F563C01F",
            "E5DCC9F9B08102C58596AF85345F689A69FD53A87D8D408BDC0FCDAF99FCF6E3",
            "9806824E11AACFC2FC41C5AEC9413CB64F755CAFD982F49170FA4F659500444A",
            "7EFA768607D8E3F3FBF8F018C7A3454695FAE718984345125BAB387A863F089F",
            "6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3",
            "Get-AuthenticodeSignature",
            r"downloads\codex\$version",
            r"toolchains\codex\$version",
            r"tmp\codex-bootstrap",
            r"scripts\hia-knowledge.ps1 environment-install",
            "a global or PATH Python is not used by the normal setup",
            "No global PATH, registry, Houdini installation, or user configuration was changed.",
        ):
            self.assertIn(required, source)
        self.assertNotIn("git config --global", source.lower())
        self.assertNotIn("setx", source.lower())
        self.assertNotIn("Remove-Item", source)
        self.assertNotIn("Get-Command -Name 'python.exe'", source)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", source))

    def test_release_builder_uses_a_strict_allowlist_and_fresh_launcher_files(self) -> None:
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        for required in (
            "$releaseFileAllowlist",
            "$releaseDirectoryAllowlist",
            "$releaseBuildInputFiles",
            "$releaseSourceRequiredFiles",
            "$releaseDenyPatterns",
            ".agents/skills/houdini-visual-research/references/build-brief-and-review.md",
            "scripts/bootstrap-runtime.ps1",
            "scripts/hia-cache.ps1",
            "scripts/hia-knowledge.ps1",
            "scripts/launcher/Install-HiaEmbedding.ps1",
            "scripts/launcher/hia_knowledge_cli.py",
            "scripts/launcher/install_hia_embedding.py",
            "services/bridge/hia_bridge/knowledge_cli.py",
            "contracts/codex-app-server/0.144.3/",
            "knowledge/sidefx-official/",
            "schemas/codex-app-server/0.144.3/",
            "services/hia_mcp_v2/",
            "src/hia_core/__init__.py",
            "src/hia_core/codex_protocol.py",
            "src/hia_core/embedding_contract.py",
            "src/hia_core/houdini_contract.py",
            "src/hia_core/path_policy.py",
            "houdini_package/python_panels/houdini_intelligence.pypanel",
            "houdini_package/python_libs/hia_mcp_runtime/deterministic_sources.py",
            "houdini_package/python_libs/hia_mcp_runtime/embedding_client.py",
            "houdini_package/python_libs/hia_mcp_runtime/hybrid_knowledge.py",
            "houdini_package/python_libs/hia_mcp_runtime/knowledge_index.py",
            "houdini_package/python_libs/hia_mcp_runtime/knowledge_index_cli.py",
            "houdini_package/python_libs/hia_panel/panel.py",
            "houdini_package/python_libs/hia_panel/task_insights.py",
            'Big-Chicken-Houdini-Intelligence-Agent-v$Version-win-x64',
            "BigChickenLauncher.exe",
            "D3DCompiler_47_cor3.dll",
            "PenImc_cor3.dll",
            "PresentationNative_cor3.dll",
            "vcruntime140_cor3.dll",
            "wpfgfx_cor3.dll",
            "check-public-release.py",
            'SHA256SUMS-v$Version.txt',
            "CreateEntryFromFile",
            "licenses\\dotnet",
            "ThirdPartyNotices.txt",
            "assets/launcher/launcher-hero.png",
            "launcher/HoudiniIntelligenceLauncher/App.xaml",
            "launcher/HoudiniIntelligenceLauncher/App.xaml.cs",
            "launcher/HoudiniIntelligenceLauncher/HoudiniIntelligenceLauncher.csproj",
            "launcher/HoudiniIntelligenceLauncher/ProjectRootLocator.cs",
        ):
            self.assertIn(required, source)
        self.assertNotIn("'houdini_package/',", source)
        self.assertNotIn("'src/hia_core/',", source)
        self.assertNotIn("src/hia_core/vending_machine.py", source)
        for excluded in (
            "hia_b4b_stairs_acceptance.pypanel",
            "b4b_acceptance.py",
            "b4b_panel.py",
            "hia_ime_diagnostic.pypanel",
            "ime_diagnostic.py",
            "houdini_write_adapter.py",
        ):
            self.assertNotIn(excluded, source)
        for forbidden in (
            "'(^|/)tests?(/|$)'",
            "'(^|/)assets(/|$)'",
            "'(^|/)\\.runtime(/|$)'",
            "'(^|/)\\.venv(/|$)'",
            "TEST-REPORT",
            "steam-winter-sale",
            "auth|credentials|secrets?|token",
            r"\.(hip|hiplc|hipnc|abc|fbx|usd|usda|usdc|exr|png",
        ):
            self.assertIn(forbidden, source)
        self.assertIn("& $launcherBuildScript", source)
        self.assertIn("$launcherDist = $packageRoot", source)
        self.assertIn("$buildArguments = @{ OutputDirectory = $launcherDist }", source)
        self.assertNotIn("Join-Path $runtimeRoot 'dist\\launcher'", source)
        self.assertIn(
            "& $releasePython -I -B $publicReleaseChecker "
            "--source-tree $projectRoot",
            source,
        )
        self.assertIn(
            "& $releasePython -I -B $publicReleaseChecker",
            source,
        )
        self.assertIn("--forbid-text $projectRoot", source)
        self.assertLess(
            source.index("--source-tree $projectRoot"),
            source.index("& $launcherBuildScript"),
        )
        self.assertLess(
            source.index("--source-tree $projectRoot"),
            source.index("[System.IO.FileMode]::CreateNew"),
        )
        self.assertNotIn("Copy-Item", source)
        self.assertNotIn("Remove-Item", source)
        self.assertNotIn("Get-Command -Name 'python.exe'", source)
        self.assertIn(
            '$checksumsPath = Join-Path $releaseRoot '
            '"SHA256SUMS-v$Version.txt"',
            source,
        )
        self.assertNotIn(
            "$checksumsPath = Join-Path $releaseRoot 'SHA256SUMS.txt'",
            source,
        )
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", source))
        allowlist_source = source[
            source.index("$releaseFileAllowlist")
            : source.index("$releaseDenyPatterns")
        ]
        for python_version in ("3.10", "3.11", "3.13"):
            self.assertIn(
                f"'houdini_package/python{python_version}libs/uiready.py'",
                allowlist_source,
            )
        self.assertNotIn("PROJECT_TEAM_LIVE_ACCEPTANCE", allowlist_source)
        self.assertNotIn("'.runtime", allowlist_source)
        self.assertNotIn('".runtime', allowlist_source)
        self.assertNotIn("'.venv", allowlist_source)
        self.assertNotIn('".venv', allowlist_source)
        self.assertLess(
            source.index(
                "if ($normalized -match $managedVenvDenyPattern) { return $false }"
            ),
            source.index("if ($releaseFileAllowlist -contains $normalized)"),
        )
        self.assertLess(
            source.index("if ($releaseFileAllowlist -contains $normalized)"),
            source.index("foreach ($pattern in $releaseDenyPatterns)"),
        )

    def test_release_allowlist_covers_exact_current_knowledge_cards(self) -> None:
        manifest = json.loads(
            (KNOWLEDGE_PACK_PATH / "manifest.json").read_text(encoding="utf-8")
        )
        manifest_cards = {
            str(item["path"])
            for item in manifest["sources"]
        }
        disk_cards = {
            path.relative_to(KNOWLEDGE_PACK_PATH).as_posix()
            for path in (KNOWLEDGE_PACK_PATH / "cards").rglob("*.md")
            if path.is_file() and not path.is_symlink()
        }

        self.assertEqual(disk_cards, manifest_cards)
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        directory_allowlist = source[
            source.index("$releaseDirectoryAllowlist")
            : source.index("$releaseDenyPatterns")
        ]
        self.assertIn("'knowledge/sidefx-official/'", directory_allowlist)

    def test_release_excludes_removed_project_team_panel_modules(self) -> None:
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        file_allowlist = source[
            source.index("$releaseFileAllowlist"):
            source.index("$releaseDirectoryAllowlist")
        ]
        removed = (
            "houdini_package/python_libs/hia_panel/project_team.py",
            "houdini_package/python_libs/hia_panel/project_team_controller.py",
            "houdini_package/python_libs/hia_panel/project_team_view.py",
        )
        for relative_path in removed:
            self.assertFalse((REPOSITORY_ROOT / relative_path).exists())
            self.assertNotIn(f"'{relative_path}'", file_allowlist)

    def test_release_preflight_is_read_only_and_uses_canonical_venv(
        self,
    ) -> None:
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        preflight = source[
            source.index("function Invoke-ReleaseSourcePreflight"):
            source.index("function Copy-ReleaseFile")
        ]
        self.assertIn(
            "Join-Path $projectRoot '.venv\\Scripts\\python.exe'",
            preflight,
        )
        self.assertIn("git -C $projectRoot ls-files --", preflight)
        self.assertIn(
            "git -C $projectRoot ls-files --others --exclude-standard --",
            preflight,
        )
        self.assertIn(
            "Required release source is not tracked by git",
            preflight,
        )
        self.assertIn("Untracked release source would be omitted", preflight)
        self.assertIn("[switch]$PreflightOnly", source)
        self.assertLess(
            source.index("$releasePython = Invoke-ReleaseSourcePreflight"),
            source.index(
                "[System.IO.Directory]::CreateDirectory($directory)"
            ),
        )
        self.assertLess(
            source.index("if ($PreflightOnly)"),
            source.index("& $launcherBuildScript"),
        )

    def test_current_release_source_preflight_passes_without_building(
        self,
    ) -> None:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BUILD_RELEASE_PATH),
                "-PreflightOnly",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        self.assertIn("[release-preflight]", completed.stdout)
        self.assertNotIn("[release] archive:", completed.stdout)

    def test_release_docs_distinguish_historical_artifacts_and_counts(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        installation = INSTALLATION_PATH.read_text(encoding="utf-8")
        report = TEST_REPORT_PATH.read_text(encoding="utf-8")

        self.assertIn("Published historical Preview ZIP", readme)
        self.assertIn("$ReleaseVersion = '<approved-preview-version>'", readme)
        self.assertNotIn("-Version 0.1.1-preview", readme)
        self.assertIn("historical 2026-07-24 snapshot", installation)
        self.assertIn("各节中的“当前”", report)
        self.assertIn("不能作为最新发行候选", report)
        self.assertIn("当前权威环境是项目根 `<project-root>/.venv`", report)
        self.assertIn("`hia-knowledge.ps1 list`", report)
        self.assertNotIn("`hia-knowledge.ps1 sources list`", report)
        self.assertIn("尚缺 33 张卡", report)

    def test_cold_start_and_gui_cli_parity_contracts_are_documented(
        self,
    ) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        installation = INSTALLATION_PATH.read_text(encoding="utf-8")
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        wpf = WPF_PATH.read_text(encoding="utf-8-sig")
        combined_docs = re.sub(r"\s+", " ", readme + "\n" + installation)

        for required in (
            "With only Houdini installed",
            "project-local Codex runtime is already verified and logged in",
            "## GUI and command-line parity",
            "hia-launcher.ps1 -CheckOnly -Json",
            "hia-launcher.ps1 -PrintCodexLoginCommand -Json",
            "scripts\\launch-houdini.ps1",
            "hia-knowledge.ps1 environment-install",
            "hia-cache.ps1 -Action <list\\|clear>",
            "build-release.ps1 -PreflightOnly",
            "There is intentionally no separate background Houdini stop service",
        ):
            self.assertIn(required, combined_docs)
        self.assertIn("[switch]$PrintCodexLoginCommand", launcher)
        self.assertIn(
            "Get-HiaCodexLoginCommand -ProjectRoot $projectRoot",
            launcher,
        )
        for shared_entry in (
            "Invoke-PreflightAndReport",
            "Start-ExistingHoudiniLauncher",
            "scripts\\bootstrap-runtime.ps1",
            "'hia-knowledge.ps1'",
            "'hia-cache.ps1'",
            "Install-HiaEmbedding.ps1",
        ):
            self.assertIn(shared_entry, launcher + "\n" + wpf)
        self.assertNotIn(r"E:\houdini-intelligence-agent", launcher + wpf)

    def test_gitignore_covers_private_and_large_generated_outputs(self) -> None:
        ignored = set(GITIGNORE_PATH.read_text(encoding="utf-8").splitlines())
        for required in (
            "auth.json",
            "credentials.json",
            "secrets.json",
            "token.json",
            "models/",
            "*.abc",
            "*.fbx",
            "*.usd",
            "*.usda",
            "*.usdc",
        ):
            self.assertIn(required, ignored)
        self.assertNotIn("*.png", ignored)

    def test_release_excludes_asset_only_core_examples(self) -> None:
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        allowlist_source = source[
            source.index("$releaseFileAllowlist")
            : source.index("$releaseDenyPatterns")
        ]
        self.assertNotIn("'src/hia_core/'", allowlist_source)
        self.assertNotIn("vending_machine.py", allowlist_source)
        self.assertIn("'src/hia_core/houdini_contract.py'", allowlist_source)
        self.assertIn("'src/hia_core/embedding_contract.py'", allowlist_source)

    def test_release_launcher_build_uses_a_project_runtime_output_override(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "build-launcher.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("[AllowEmptyString()][string]$OutputDirectory = ''", source)
        self.assertIn("[System.IO.Path]::GetFullPath($OutputDirectory)", source)
        self.assertIn("Launcher output must stay under the project runtime directory", source)

    def test_launcher_bundles_project_artwork_without_third_party_names(self) -> None:
        combined = "\n".join(
            (
                XAML_PATH.read_text(encoding="utf-8-sig"),
                WPF_PATH.read_text(encoding="utf-8-sig"),
                CORE_PATH.read_text(encoding="utf-8-sig"),
                CS_PROJECT_PATH.read_text(encoding="utf-8-sig"),
            )
        )
        self.assertNotIn("steam-winter-sale", combined.lower())
        self.assertNotIn("sakurakouji-luna", combined.lower())
        self.assertIn("Initialize-HiaOptionalArtwork", combined)
        self.assertIn("launcher-hero.png", combined)
        self.assertIn("'assets'", combined)
        self.assertTrue(
            (REPOSITORY_ROOT / "assets" / "launcher" / "launcher-hero.png").is_file()
        )
        self.assertIn("BIG-CHICKEN", combined)
        project = ET.parse(CS_PROJECT_PATH).getroot()
        self.assertEqual([], project.findall(".//Content"))

    def test_public_release_checker_exists_and_is_invoked_without_shell_wrapping(self) -> None:
        self.assertTrue(PUBLIC_CHECKER_PATH.is_file())
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        self.assertNotIn("powershell.exe", source.lower())
        self.assertNotIn("cmd.exe", source.lower())

    def test_readme_documents_the_release_builder_and_outputs(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        for required in (
            r".\scripts\build-release.ps1",
            r".runtime\release",
            "SHA256SUMS-v<version>.txt",
            "check-public-release.py",
            "0.1.1-preview",
        ):
            self.assertIn(required, readme)


if __name__ == "__main__":
    unittest.main()
