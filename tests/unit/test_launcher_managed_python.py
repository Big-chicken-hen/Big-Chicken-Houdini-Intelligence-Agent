from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
from hia_core import embedding_contract


MODULE_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
LAUNCHER_PATH = REPOSITORY_ROOT / "scripts" / "hia-launcher.ps1"
LIFECYCLE_PATH = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
MANAGED_RELATIVE = Path(embedding_contract.EMBEDDING_PYTHON_WINDOWS)


def _ps_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class ManagedLauncherPythonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = shutil.which("powershell")
        if cls.powershell is None:
            raise unittest.SkipTest("Windows PowerShell is unavailable")
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="hia-managed-python-",
            dir=runtime_tmp,
        )
        cls.sandbox = Path(cls._temporary.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def run_powershell(self, body: str, *, env: dict[str, str] | None = None) -> str:
        script = (
            f"Import-Module -Force -DisableNameChecking {_ps_literal(MODULE_PATH)}\n"
            + body
        )
        completed = subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed.stdout.strip()

    def make_layout(
        self,
        name: str,
        *,
        include_system_site: bool = False,
        home_outside: bool = False,
    ) -> tuple[Path, dict[str, object]]:
        root = self.sandbox / name / "project"
        venv = root / embedding_contract.EMBEDDING_VENV_ROOT
        python = venv / "Scripts" / "python.exe"
        site_packages = venv / "Lib" / "site-packages"
        base = (
            self.sandbox / name / "external-python"
            if home_outside
            else root / ".runtime" / "toolchains" / "hia-embedding" / "python"
        )
        for directory in (
            python.parent,
            site_packages,
            base,
            base / "Lib",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fake managed python")
        (venv / "pyvenv.cfg").write_text(
            "\n".join(
                (
                    f"home = {base}",
                    "include-system-site-packages = "
                    + ("true" if include_system_site else "false"),
                    "version = 3.11.9",
                    "",
                )
            ),
            encoding="utf-8",
        )
        probe = {
            "python": "3.11.9",
            "executable": str(python),
            "prefix": str(venv),
            "base_prefix": str(base),
            "base_exec_prefix": str(base),
            "purelib": str(site_packages),
            "platlib": str(site_packages),
            "site_packages": [str(site_packages)],
            "sys_path": [str(base), str(base / "Lib"), str(site_packages)],
            "no_user_site": True,
            "enable_user_site": False,
        }
        return root, probe

    def get_state(self, root: Path, probe: dict[str, object]) -> dict[str, object]:
        output = self.run_powershell(
            "\n".join(
                (
                    f"$probe = {_ps_literal(json.dumps(probe))} | ConvertFrom-Json",
                    "$state = Get-HiaManagedBridgePythonState "
                    f"-ProjectRoot {_ps_literal(root)} -ProbePayloadOverride $probe",
                    "$state | ConvertTo-Json -Compress",
                )
            )
        )
        return json.loads(output.splitlines()[-1])

    def test_health_matrix_is_fail_closed(self) -> None:
        healthy_root, healthy_probe = self.make_layout("healthy")
        healthy = self.get_state(healthy_root, healthy_probe)
        self.assertTrue(healthy["healthy"], healthy)
        self.assertEqual(
            (healthy_root / MANAGED_RELATIVE).resolve(),
            Path(healthy["path"]).resolve(),
        )

        cases: list[tuple[str, dict[str, object]]] = []
        external_home_root, external_home_probe = self.make_layout(
            "external-home", home_outside=True
        )
        cases.append(("external home", self.get_state(external_home_root, external_home_probe)))

        system_site_root, system_site_probe = self.make_layout(
            "system-site", include_system_site=True
        )
        cases.append(("system site enabled", self.get_state(system_site_root, system_site_probe)))

        external_base_root, external_base_probe = self.make_layout("external-base")
        external_base = self.sandbox / "external-base-probe"
        external_base.mkdir()
        external_base_probe["base_prefix"] = str(external_base)
        cases.append(("external base", self.get_state(external_base_root, external_base_probe)))

        external_site_root, external_site_probe = self.make_layout("external-site")
        external_site = self.sandbox / "external-site-packages"
        external_site.mkdir()
        external_site_probe["site_packages"] = [str(external_site)]
        cases.append(("external site", self.get_state(external_site_root, external_site_probe)))

        user_site_root, user_site_probe = self.make_layout("user-site")
        user_site_probe["no_user_site"] = False
        user_site_probe["enable_user_site"] = True
        cases.append(("user site enabled", self.get_state(user_site_root, user_site_probe)))

        for label, result in cases:
            with self.subTest(case=label):
                self.assertFalse(result["healthy"], result)
                self.assertTrue(result["reason"])

    def test_reparse_in_managed_path_is_rejected(self) -> None:
        root = self.sandbox / "reparse" / "project"
        toolchain = root / ".runtime" / "toolchains" / "hia-embedding"
        target = root / ".runtime" / "managed-venv-target"
        python = target / "Scripts" / "python.exe"
        base = root / ".runtime" / "toolchains" / "hia-embedding" / "python"
        site_packages = target / "Lib" / "site-packages"
        for directory in (python.parent, base / "Lib", site_packages, toolchain):
            directory.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fake")
        (target / "pyvenv.cfg").write_text(
            f"home = {base}\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
        link = toolchain / "venv"
        create = subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"New-Item -ItemType Junction -Path {_ps_literal(link)} "
                f"-Target {_ps_literal(target)} | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if create.returncode != 0:
            self.skipTest(f"directory junction unavailable: {create.stderr.strip()}")
        probe = {
            "python": "3.11.9",
            "executable": str(link / "Scripts" / "python.exe"),
            "prefix": str(link),
            "base_prefix": str(base),
            "base_exec_prefix": str(base),
            "purelib": str(link / "Lib" / "site-packages"),
            "platlib": str(link / "Lib" / "site-packages"),
            "site_packages": [str(link / "Lib" / "site-packages")],
            "sys_path": [str(base), str(base / "Lib")],
            "no_user_site": True,
            "enable_user_site": False,
        }
        state = self.get_state(root, probe)
        self.assertFalse(state["healthy"], state)
        self.assertIn("reparse", state["reason"].lower())

    def test_expected_uv_python_alias_is_resolved_to_versioned_target(self) -> None:
        root = self.sandbox / "uv-alias" / "project"
        venv = root / embedding_contract.EMBEDDING_VENV_ROOT
        install_root = root / ".runtime" / "toolchains" / "python"
        target = install_root / "cpython-3.10.11-windows-x86_64-none"
        alias = install_root / "cpython-3.10-windows-x86_64-none"
        python = venv / "Scripts" / "python.exe"
        site_packages = venv / "Lib" / "site-packages"
        for directory in (
            python.parent,
            site_packages,
            target / "DLLs",
            target / "lib",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fake managed python")
        (target / "python.exe").write_bytes(b"fake base python")
        (target / "python310.zip").write_bytes(b"fake stdlib archive")
        (venv / "pyvenv.cfg").write_text(
            f"home = {alias}\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
        create = subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"New-Item -ItemType Junction -Path {_ps_literal(alias)} "
                f"-Target {_ps_literal(target)} | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if create.returncode != 0:
            self.skipTest(f"directory junction unavailable: {create.stderr.strip()}")
        probe = {
            "python": "3.10.11",
            "executable": str(python),
            "prefix": str(venv),
            "base_prefix": str(alias),
            "base_exec_prefix": str(alias),
            "purelib": str(site_packages),
            "platlib": str(site_packages),
            "site_packages": [str(venv), str(site_packages)],
            "sys_path": [
                str(alias / "python310.zip"),
                str(alias / "DLLs"),
                str(alias / "lib"),
                str(alias),
                str(venv),
                str(site_packages),
            ],
            "no_user_site": True,
            "enable_user_site": False,
        }

        state = self.get_state(root, probe)
        self.assertTrue(state["healthy"], state)

    def test_only_verified_managed_candidate_is_automatic(self) -> None:
        root, probe = self.make_layout("candidate-pollution")
        external = self.sandbox / "candidate-pollution" / "external" / "python.exe"
        legacy = self.sandbox / "candidate-pollution" / "legacy" / "python.exe"
        path_python = self.sandbox / "candidate-pollution" / "path" / "python.exe"
        for path in (external, legacy, path_python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"external")
        output = self.run_powershell(
            "\n".join(
                (
                    f"$env:HIA_BRIDGE_PYTHON = {_ps_literal(external)}",
                    f"$env:PATH = {_ps_literal(path_python.parent)} + ';' + $env:PATH",
                    f"$probe = {_ps_literal(json.dumps(probe))} | ConvertFrom-Json",
                    "$items = @(Get-HiaBridgePythonCandidates "
                    f"-ProjectRoot {_ps_literal(root)} "
                    f"-SavedPath {_ps_literal(legacy)} "
                    "-ManagedProbePayloadOverride $probe)",
                    "$items | ConvertTo-Json -Depth 5 -Compress",
                )
            )
        )
        candidates = json.loads(output.splitlines()[-1])
        if isinstance(candidates, dict):
            candidates = [candidates]
        automatic = [item for item in candidates if item["automatic"]]
        self.assertEqual(1, len(automatic), candidates)
        self.assertEqual(
            (root / MANAGED_RELATIVE).resolve(),
            Path(automatic[0]["path"]).resolve(),
        )
        sources = {item["source"] for item in candidates}
        self.assertIn("HIA_BRIDGE_PYTHON", sources)
        self.assertIn("settings", sources)
        self.assertIn("PATH", sources)
        for candidate in candidates:
            if Path(candidate["path"]).resolve() != (root / MANAGED_RELATIVE).resolve():
                self.assertTrue(candidate["advanced"])
                self.assertFalse(candidate["automatic"])

    def test_managed_setting_is_relative_and_re_resolves_after_move(self) -> None:
        first = self.sandbox / "portable-settings" / "first"
        second = self.sandbox / "portable-settings" / "second"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        first_python = first / MANAGED_RELATIVE
        first_python.parent.mkdir(parents=True)
        first_python.write_bytes(b"fake")
        self.run_powershell(
            "Write-HiaLauncherSettings "
            f"-ProjectRoot {_ps_literal(first)} "
            f"-HoudiniExe {_ps_literal(first / 'Houdini/bin/houdini.exe')} "
            f"-BridgePython {_ps_literal(first_python)} | Out-Null"
        )
        first_settings = first / ".runtime" / "launcher" / "settings.json"
        persisted = json.loads(first_settings.read_text(encoding="utf-8"))
        self.assertEqual("managed", persisted["bridge_python_mode"])
        self.assertEqual(
            embedding_contract.EMBEDDING_PYTHON_WINDOWS,
            persisted["bridge_python"],
        )
        self.assertFalse(Path(persisted["bridge_python"]).is_absolute())

        second_settings = second / ".runtime" / "launcher" / "settings.json"
        second_settings.parent.mkdir(parents=True)
        second_settings.write_bytes(first_settings.read_bytes())
        output = self.run_powershell(
            "$value = Read-HiaLauncherSettings "
            f"-ProjectRoot {_ps_literal(second)}; "
            "$value | ConvertTo-Json -Compress"
        )
        moved = json.loads(output.splitlines()[-1])
        self.assertEqual("managed", moved["bridge_python_mode"])
        self.assertEqual("", moved["bridge_python"])
        self.assertEqual(
            (second / MANAGED_RELATIVE).resolve(),
            Path(moved["bridge_python_managed"]).resolve(),
        )

    def test_legacy_absolute_setting_is_advanced_and_never_selected(self) -> None:
        root = self.sandbox / "legacy-settings" / "project"
        settings = root / ".runtime" / "launcher" / "settings.json"
        external = self.sandbox / "legacy-settings" / "outside" / "python.exe"
        settings.parent.mkdir(parents=True)
        external.parent.mkdir(parents=True)
        external.write_bytes(b"fake")
        settings.write_text(
            json.dumps(
                {
                    "houdini_exe": "",
                    "bridge_python": str(external),
                    "mcp_backend": "hia_v2",
                }
            ),
            encoding="utf-8",
        )
        output = self.run_powershell(
            "$value = Read-HiaLauncherSettings "
            f"-ProjectRoot {_ps_literal(root)}; "
            "$value | ConvertTo-Json -Compress"
        )
        loaded = json.loads(output.splitlines()[-1])
        self.assertEqual("external", loaded["bridge_python_mode"])
        self.assertEqual("", loaded["bridge_python"])
        self.assertEqual(external.resolve(), Path(loaded["bridge_python_advanced"]).resolve())

    def test_bootstrap_paths_match_embedding_contract(self) -> None:
        root = self.sandbox / "contract-consistency" / "project"
        root.mkdir(parents=True)
        expected = Path(
            embedding_contract.runtime_layout(root)["worker_python"]
        ).resolve()
        output = self.run_powershell(
            "Get-HiaManagedBridgePythonPath "
            f"-ProjectRoot {_ps_literal(root)}"
        )
        self.assertEqual(expected, Path(output.splitlines()[-1]).resolve())
        self.assertEqual(".venv", embedding_contract.EMBEDDING_VENV_ROOT)
        self.assertEqual(
            ".venv/Scripts/python.exe",
            embedding_contract.EMBEDDING_PYTHON_WINDOWS,
        )

        module = MODULE_PATH.read_text(encoding="utf-8-sig")
        wrapper = (
            REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("'.venv\\Scripts\\python.exe'", module)
        self.assertIn("'.venv\\Scripts\\python.exe'", wrapper)
        self.assertNotIn(
            "'.runtime\\toolchains\\hia-embedding\\venv\\Scripts\\python.exe'",
            module,
        )
        self.assertNotIn(
            "'.runtime\\toolchains\\hia-embedding\\venv\\Scripts\\python.exe'",
            wrapper,
        )

    def test_lifecycle_and_cli_auto_paths_do_not_consume_environment_or_path(self) -> None:
        lifecycle = LIFECYCLE_PATH.read_text(encoding="utf-8-sig")
        resolver_start = lifecycle.index("function Resolve-BridgePythonExecutable")
        resolver_end = lifecycle.index("$ResolvedRoot =", resolver_start)
        resolver = lifecycle[resolver_start:resolver_end]
        self.assertIn("Resolve-HiaManagedBridgePython", resolver)
        self.assertNotIn("HIA_BRIDGE_PYTHON", resolver)
        self.assertNotIn("Get-Command", resolver)
        self.assertNotIn(r".runtime\python\python.exe", resolver)

        launcher = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
        selection_start = launcher.index("$bridgeCandidates =")
        selection_end = launcher.index("$selectedBackend =", selection_start)
        selection = launcher[selection_start:selection_end]
        self.assertIn("automaticBridge", selection)
        self.assertNotIn("$Settings.bridge_python -and", selection)

        module = MODULE_PATH.read_text(encoding="utf-8-sig")
        plan_start = module.index("function New-HiaKnowledgeCliProcessPlan")
        plan_end = module.index(
            "function ConvertFrom-HiaKnowledgeIndexJsonLine",
            plan_start,
        )
        plan = module[plan_start:plan_end]
        self.assertIn("Resolve-HiaManagedBridgePython", plan)
        self.assertIn("-BridgePython $managedPython", plan)
        self.assertNotIn("-BridgePython $BridgePython", plan)


if __name__ == "__main__":
    unittest.main()
