from __future__ import annotations

import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
BOOTSTRAP_PATH = REPOSITORY_ROOT / "scripts" / "bootstrap-runtime.ps1"
BUILD_RELEASE_PATH = REPOSITORY_ROOT / "scripts" / "build-release.ps1"
PUBLIC_CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "check-public-release.py"
README_PATH = REPOSITORY_ROOT / "README.md"
XAML_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.xaml"
WPF_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Wpf.ps1"
CORE_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
CS_PROJECT_PATH = (
    REPOSITORY_ROOT
    / "launcher"
    / "HoudiniIntelligenceLauncher"
    / "HoudiniIntelligenceLauncher.csproj"
)


class ReleasePackagingTests(unittest.TestCase):
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
            "Python 3.10+",
            "No global PATH, registry, Houdini installation, or user configuration was changed.",
        ):
            self.assertIn(required, source)
        self.assertNotIn("git config --global", source.lower())
        self.assertNotIn("setx", source.lower())
        self.assertNotIn("Remove-Item", source)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", source))

    def test_release_builder_uses_a_strict_allowlist_and_fresh_launcher_files(self) -> None:
        source = BUILD_RELEASE_PATH.read_text(encoding="utf-8-sig")
        for required in (
            "$releaseFileAllowlist",
            "$releaseDirectoryAllowlist",
            "$releaseDenyPatterns",
            "scripts/bootstrap-runtime.ps1",
            "contracts/codex-app-server/0.144.3/",
            "schemas/codex-app-server/0.144.3/",
            "services/hia_mcp_v2/",
            "houdini_package/python_panels/houdini_intelligence.pypanel",
            "houdini_package/python_libs/hia_panel/panel.py",
            'Big-Chicken-Houdini-Intelligence-Agent-v$Version-win-x64',
            "BigChickenLauncher.exe",
            "D3DCompiler_47_cor3.dll",
            "PenImc_cor3.dll",
            "PresentationNative_cor3.dll",
            "vcruntime140_cor3.dll",
            "wpfgfx_cor3.dll",
            "check-public-release.py",
            "SHA256SUMS.txt",
            "CreateEntryFromFile",
            "licenses\\dotnet",
            "ThirdPartyNotices.txt",
        ):
            self.assertIn(required, source)
        self.assertNotIn("'houdini_package/',", source)
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
            "TEST-REPORT",
            "steam-winter-sale",
            r"\.(hip|hiplc|hipnc|exr|png",
        ):
            self.assertIn(forbidden, source)
        self.assertIn("& $launcherBuildScript", source)
        self.assertIn("$launcherDist = $packageRoot", source)
        self.assertIn("$buildArguments = @{ OutputDirectory = $launcherDist }", source)
        self.assertNotIn("Join-Path $runtimeRoot 'dist\\launcher'", source)
        self.assertIn("& $python.Source -B $publicReleaseChecker $archivePath", source)
        self.assertNotIn("Copy-Item", source)
        self.assertNotIn("Remove-Item", source)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", source))

    def test_release_launcher_build_uses_a_project_runtime_output_override(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "build-launcher.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("[AllowEmptyString()][string]$OutputDirectory = ''", source)
        self.assertIn("[System.IO.Path]::GetFullPath($OutputDirectory)", source)
        self.assertIn("Launcher output must stay under the project runtime directory", source)

    def test_launcher_has_no_seasonal_artwork_runtime_dependency(self) -> None:
        combined = "\n".join(
            (
                XAML_PATH.read_text(encoding="utf-8-sig"),
                WPF_PATH.read_text(encoding="utf-8-sig"),
                CORE_PATH.read_text(encoding="utf-8-sig"),
                CS_PROJECT_PATH.read_text(encoding="utf-8-sig"),
            )
        )
        self.assertNotIn("steam-winter-sale", combined.lower())
        self.assertNotIn("OptionalArtwork", combined)
        self.assertNotIn("Get-HiaLauncherArtworkPath", combined)
        self.assertIn("CREATIVE WORKSPACE", combined)
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
            "SHA256SUMS.txt",
            "check-public-release.py",
            "0.1.0-preview",
        ):
            self.assertIn(required, readme)


if __name__ == "__main__":
    unittest.main()
