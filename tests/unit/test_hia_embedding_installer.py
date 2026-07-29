from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
INSTALLER_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "launcher"
    / "install_hia_embedding.py"
)
POWERSHELL_INSTALLER_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "launcher"
    / "Install-HiaEmbedding.ps1"
)
RUNTIME_TEST_ROOT = REPOSITORY_ROOT


def _load_installer() -> types.ModuleType:
    module_name = "_test_hia_embedding_installer_module"
    spec = importlib.util.spec_from_file_location(module_name, INSTALLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("installer helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


installer = _load_installer()


class EmbeddingInstallerTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            RUNTIME_TEST_ROOT
            / f".hia-embedding-installer-test-{uuid.uuid4().hex}"
        )
        self.models_root = self.sandbox / "models"
        self.cache_root = self.sandbox / "cache" / "huggingface"
        self.snapshot = (
            self.cache_root
            / "models--Qwen--test"
            / "snapshots"
            / "revision"
        )
        self.model_dir = self.models_root / "selected-model"
        self.models_root.mkdir(parents=True)
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "config.json").write_text(
            '{"model_type":"test"}\n',
            encoding="utf-8",
        )
        (self.snapshot / "model.safetensors").write_bytes(b"safe")
        self.plan = {
            "contract_version": 1,
            "profile_id": "selected-profile",
            "model_id": "Qwen/Selected-Model",
            "revision": "main",
            "model_dir": str(self.model_dir),
            "manifest_path": str(
                self.model_dir / installer.MODEL_MANIFEST_NAME
            ),
            "layout": {
                "huggingface_cache": str(self.cache_root),
                "models_root": str(self.models_root),
            },
            "child_environment": {},
            "worker_distribution": "test-worker",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox)

    def _hub_module(self, download: mock.Mock) -> types.ModuleType:
        module = types.ModuleType("huggingface_hub")
        module.snapshot_download = download
        return module

    def _staging_paths(self) -> list[Path]:
        return list(
            self.models_root.glob(
                f"{installer.STAGING_DIRECTORY_PREFIX}*"
            )
        )

    def _link_snapshot_payload(
        self,
        config_target: Path,
        weight_target: Path,
    ) -> None:
        config_link = self.snapshot / "config.json"
        weight_link = self.snapshot / "model.safetensors"
        config_link.unlink()
        weight_link.unlink()
        try:
            config_link.symlink_to(
                os.path.relpath(config_target, self.snapshot)
            )
            weight_link.symlink_to(
                os.path.relpath(weight_target, self.snapshot)
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest(
                    "Windows file symlink creation requires elevated privilege"
                )
            raise

    def test_failed_materialization_leaves_canonical_clean_and_retry_succeeds(
        self,
    ) -> None:
        download = mock.Mock(return_value=str(self.snapshot))

        def fail_mid_copy(
            _snapshot: Path,
            staging: Path,
            _cache: Path,
        ) -> None:
            (staging / "partial.bin").write_bytes(b"partial")
            raise installer.InstallerError("injected materialization failure")

        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
            mock.patch.object(
                installer,
                "_copy_snapshot_tree",
                side_effect=fail_mid_copy,
            ),
        ):
            with self.assertRaisesRegex(
                installer.InstallerError,
                "injected materialization failure",
            ):
                installer.download_selected_model(self.plan)

        self.assertFalse(self.model_dir.exists())
        self.assertEqual([], self._staging_paths())

        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
        ):
            result = installer.download_selected_model(self.plan)

        self.assertEqual("installed", result["status"])
        self.assertTrue((self.model_dir / "config.json").is_file())
        self.assertTrue((self.model_dir / "model.safetensors").is_file())
        manifest = json.loads(
            (
                self.model_dir / installer.MODEL_MANIFEST_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "contract_version": 1,
                "profile_id": "selected-profile",
                "model_id": "Qwen/Selected-Model",
                "revision": "main",
            },
            manifest,
        )
        self.assertEqual([], self._staging_paths())

    def test_unknown_nonempty_target_is_never_overwritten(self) -> None:
        self.model_dir.mkdir()
        user_file = self.model_dir / "unknown.bin"
        user_file.write_bytes(b"keep")
        download = mock.Mock(return_value=str(self.snapshot))

        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
        ):
            with self.assertRaisesRegex(
                installer.InstallerError,
                "unverified existing data",
            ):
                installer.download_selected_model(self.plan)

        download.assert_not_called()
        self.assertEqual(b"keep", user_file.read_bytes())
        self.assertEqual([user_file], list(self.model_dir.iterdir()))
        self.assertEqual([], self._staging_paths())

    def test_verified_existing_model_is_reused_without_download(self) -> None:
        first_download = mock.Mock(return_value=str(self.snapshot))
        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(first_download)},
            ),
        ):
            installed = installer.download_selected_model(self.plan)
        self.assertEqual("installed", installed["status"])

        unexpected_download = mock.Mock(
            side_effect=AssertionError("verified model must not download again")
        )
        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(unexpected_download)},
            ),
        ):
            reused = installer.download_selected_model(self.plan)

        self.assertEqual("already_installed", reused["status"])
        unexpected_download.assert_not_called()
        self.assertEqual([], self._staging_paths())

    def test_unsupported_hardlinks_fall_back_to_staged_copy(self) -> None:
        download = mock.Mock(return_value=str(self.snapshot))
        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
            mock.patch.object(
                installer.os,
                "link",
                side_effect=OSError("hardlinks unsupported"),
            ),
        ):
            result = installer.download_selected_model(self.plan)

        self.assertEqual("installed", result["status"])
        self.assertEqual(
            b"safe",
            (self.model_dir / "model.safetensors").read_bytes(),
        )
        self.assertTrue(
            (self.model_dir / installer.MODEL_MANIFEST_NAME).is_file()
        )
        self.assertEqual([], self._staging_paths())

    def test_huggingface_snapshot_symlinks_within_cache_are_materialized(
        self,
    ) -> None:
        blobs = self.cache_root / "blobs"
        blobs.mkdir()
        config_blob = blobs / "config-blob"
        weight_blob = blobs / "weight-blob"
        config_blob.write_text('{"model_type":"test"}\n', encoding="utf-8")
        weight_blob.write_bytes(b"linked-safe")
        self._link_snapshot_payload(config_blob, weight_blob)
        download = mock.Mock(return_value=str(self.snapshot))

        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
        ):
            result = installer.download_selected_model(self.plan)

        self.assertEqual("installed", result["status"])
        installed_config = self.model_dir / "config.json"
        installed_weight = self.model_dir / "model.safetensors"
        self.assertFalse(installed_config.is_symlink())
        self.assertFalse(installed_weight.is_symlink())
        self.assertEqual(b"linked-safe", installed_weight.read_bytes())
        self.assertEqual([], self._staging_paths())

    def test_huggingface_snapshot_symlink_escaping_cache_is_rejected(
        self,
    ) -> None:
        blobs = self.cache_root / "blobs"
        blobs.mkdir()
        config_blob = blobs / "config-blob"
        escaped_weight = self.sandbox / "outside-weight-blob"
        config_blob.write_text('{"model_type":"test"}\n', encoding="utf-8")
        escaped_weight.write_bytes(b"outside")
        self._link_snapshot_payload(config_blob, escaped_weight)
        download = mock.Mock(return_value=str(self.snapshot))

        with (
            mock.patch.object(
                installer,
                "_assert_download_environment",
            ),
            mock.patch.dict(
                sys.modules,
                {"huggingface_hub": self._hub_module(download)},
            ),
        ):
            with self.assertRaisesRegex(
                installer.InstallerError,
                "downloaded embedding model payload is incomplete",
            ):
                installer.download_selected_model(self.plan)

        self.assertFalse(self.model_dir.exists())
        self.assertEqual([], self._staging_paths())

    def test_download_staging_worker_requires_exact_transaction_marker(
        self,
    ) -> None:
        install_id = uuid.uuid4().hex
        toolchain = self.sandbox / "toolchains" / "hia-embedding"
        staging = toolchain / f".venv-staging-{install_id}"
        worker = staging / "Scripts" / "python.exe"
        worker.parent.mkdir(parents=True)
        worker.write_bytes(b"staged python")
        marker = {
            "schema": "hia-managed-python-venv/1",
            "role": "hia-embedding",
            "install_id": install_id,
            "python_version": "3.10.11",
            "managed_python": (
                ".runtime/toolchains/python/pinned/python.exe"
            ),
        }
        (staging / installer.VENV_MARKER_NAME).write_text(
            json.dumps(marker),
            encoding="utf-8",
        )
        plan = {
            "layout": {
                "toolchain_root": str(toolchain),
                "worker_python": str(
                    self.sandbox / ".venv" / "Scripts" / "python.exe"
                ),
            },
        }

        self.assertEqual(
            worker.resolve(),
            installer._validated_staging_worker(plan, install_id),
        )

        for label, changed in (
            ("wrong id", {**marker, "install_id": "f" * 32}),
            ("wrong role", {**marker, "role": "other"}),
            ("absolute base", {**marker, "managed_python": "C:\\python.exe"}),
        ):
            with self.subTest(case=label):
                (staging / installer.VENV_MARKER_NAME).write_text(
                    json.dumps(changed),
                    encoding="utf-8",
                )
                with self.assertRaises(installer.InstallerError):
                    installer._validated_staging_worker(plan, install_id)

    def test_publish_validation_rollback_preserves_backup_and_isolates_new(
        self,
    ) -> None:
        project_root = self.sandbox / "publish-rollback"
        toolchain = (
            project_root / ".runtime" / "toolchains" / "hia-embedding"
        )
        canonical = project_root / ".venv"
        install_id = uuid.uuid4().hex
        backup = toolchain / f".venv-backup-{install_id}"
        canonical.mkdir(parents=True)
        backup.mkdir(parents=True)
        marker = {
            "schema": "hia-managed-python-venv/1",
            "role": "hia-embedding",
            "install_id": install_id,
            "python_version": "3.10.11",
            "managed_python": (
                ".runtime/toolchains/python/test/python.exe"
            ),
        }
        (canonical / ".hia-managed-venv.json").write_text(
            json.dumps(marker),
            encoding="utf-8",
        )
        (canonical / "failed-new.txt").write_text("new", encoding="utf-8")
        (backup / "prior-working.txt").write_text(
            "prior",
            encoding="utf-8",
        )
        outside = self.sandbox / "outside.txt"
        outside.write_text("outside", encoding="utf-8")

        script = f"""
$ErrorActionPreference = 'Stop'
$installerPath = {_ps_literal(POWERSHELL_INSTALLER_PATH)}
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $installerPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'installer AST failed' }}
$wanted = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'Assert-HiaKnowledgeProjectDirectory',
    'Get-HiaKnowledgeManagedVenvMarker',
    'Assert-HiaKnowledgeManagedVenvTree',
    'Get-HiaKnowledgeTransactionPaths',
    'Copy-HiaKnowledgeManagedVenvTree',
    'Undo-HiaKnowledgeManagedVenvPublication'
)
foreach ($name in $wanted) {{
    $node = $ast.Find(
        {{
            param($candidate)
            $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $candidate.Name -eq $name
        }},
        $true
    )
    if ($null -eq $node) {{ throw "missing function $name" }}
    Invoke-Expression $node.Extent.Text
}}
$script:HiaManagedVenvMarkerName = '.hia-managed-venv.json'
$script:HiaManagedVenvMarkerSchema = 'hia-managed-python-venv/1'
$script:HiaKnowledgePythonVersion = '3.10.11'
function Write-HiaEmbeddingInstallLog {{ param($Level, $Message) }}
$rollback = Undo-HiaKnowledgeManagedVenvPublication `
    -ProjectRoot {_ps_literal(project_root)} `
    -VenvRoot {_ps_literal(canonical)} `
    -BackupRoot {_ps_literal(backup)} `
    -InstallId {_ps_literal(install_id)}
[pscustomobject]@{{
    restored = Test-Path -LiteralPath (
        Join-Path {_ps_literal(canonical)} 'prior-working.txt'
    ) -PathType Leaf
    backup_preserved = Test-Path -LiteralPath (
        Join-Path {_ps_literal(backup)} 'prior-working.txt'
    ) -PathType Leaf
    isolated = Test-Path -LiteralPath (
        Join-Path ([string]$rollback.isolated_failed_venv) 'failed-new.txt'
    ) -PathType Leaf
    outside_unchanged = (
        [System.IO.File]::ReadAllText({_ps_literal(outside)}) -eq 'outside'
    )
}} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["restored"], result)
        self.assertTrue(result["backup_preserved"], result)
        self.assertTrue(result["isolated"], result)
        self.assertTrue(result["outside_unchanged"], result)

    def test_installer_owned_cleanup_uses_exact_project_local_allowlist(
        self,
    ) -> None:
        project_root = self.sandbox / "cleanup-project"
        install_id = uuid.uuid4().hex
        toolchain = (
            project_root / ".runtime" / "toolchains" / "hia-embedding"
        )
        staging = toolchain / f".venv-staging-{install_id}"
        (staging / "nested").mkdir(parents=True)
        (staging / "nested" / "package.bin").write_bytes(b"managed")
        canonical = project_root / ".venv"
        canonical.mkdir()
        (canonical / "keep.txt").write_text("canonical", encoding="utf-8")
        models = project_root / ".runtime" / "models"
        knowledge = project_root / ".runtime" / "knowledge"
        cache = project_root / ".runtime" / "cache" / "embedding" / "uv"
        for protected in (models, knowledge, cache):
            protected.mkdir(parents=True)
            (protected / "keep.txt").write_text("keep", encoding="utf-8")
        hip = project_root / "scene.hip"
        hip.write_bytes(b"hip")
        outside = self.sandbox / "outside-cleanup"
        outside.mkdir()
        (outside / "keep.txt").write_text("outside", encoding="utf-8")
        denied = [
            "",
            "relative",
            "$HOME\\unsafe",
            "~\\unsafe",
            "USERPROFILE\\unsafe",
            str(outside),
            str(project_root),
            str(canonical),
            str(models),
            str(knowledge),
            str(cache),
        ]

        script = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(POWERSHELL_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'installer AST failed' }}
$wanted = @(
    'Assert-HiaKnowledgeProjectDirectory',
    'Assert-HiaKnowledgeManagedVenvTree',
    'Get-HiaKnowledgeTransactionPaths',
    'Assert-HiaKnowledgeCleanupTarget',
    'Remove-HiaKnowledgeInstallerOwnedDirectory'
)
foreach ($name in $wanted) {{
    $node = $ast.Find(
        {{
            param($candidate)
            $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $candidate.Name -eq $name
        }},
        $true
    )
    Invoke-Expression $node.Extent.Text
}}
$removed = Remove-HiaKnowledgeInstallerOwnedDirectory `
    -ProjectRoot {_ps_literal(project_root)} `
    -Path {_ps_literal(staging)} `
    -Kind 'staging' `
    -InstallId {_ps_literal(install_id)}
$denied = ConvertFrom-Json -InputObject {_ps_literal(json.dumps(denied))}
$rejected = 0
foreach ($candidate in @($denied)) {{
    try {{
        Assert-HiaKnowledgeCleanupTarget `
            -ProjectRoot {_ps_literal(project_root)} `
            -Path ([string]$candidate) `
            -Kind 'staging' `
            -InstallId {_ps_literal(install_id)} | Out-Null
    }} catch {{
        $rejected += 1
    }}
}}
[pscustomobject]@{{
    removed = [bool]$removed
    rejected = $rejected
    denied_count = @($denied).Count
}} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["removed"], result)
        self.assertEqual(result["denied_count"], result["rejected"])
        self.assertFalse(staging.exists())
        self.assertEqual(
            "canonical",
            (canonical / "keep.txt").read_text(encoding="utf-8"),
        )
        for protected in (models, knowledge, cache):
            self.assertEqual(
                "keep",
                (protected / "keep.txt").read_text(encoding="utf-8"),
            )
        self.assertEqual(b"hip", hip.read_bytes())
        self.assertEqual(
            "outside",
            (outside / "keep.txt").read_text(encoding="utf-8"),
        )

    def test_cleanup_rejects_reparse_ancestor_without_writes(self) -> None:
        project_root = self.sandbox / "reparse-cleanup-project"
        project_root.mkdir()
        outside_runtime = self.sandbox / "outside-runtime"
        install_id = uuid.uuid4().hex
        staging = (
            outside_runtime
            / "toolchains"
            / "hia-embedding"
            / f".venv-staging-{install_id}"
        )
        staging.mkdir(parents=True)
        sentinel = staging / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        created = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "New-Item -ItemType Junction "
                    f"-Path {_ps_literal(project_root / '.runtime')} "
                    f"-Target {_ps_literal(outside_runtime)} | Out-Null"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if created.returncode != 0:
            self.skipTest("Windows junction creation is unavailable")
        script = f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(POWERSHELL_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
$wanted = @(
    'Assert-HiaKnowledgeProjectDirectory',
    'Assert-HiaKnowledgeManagedVenvTree',
    'Get-HiaKnowledgeTransactionPaths',
    'Assert-HiaKnowledgeCleanupTarget',
    'Remove-HiaKnowledgeInstallerOwnedDirectory'
)
foreach ($name in $wanted) {{
    $node = $ast.Find(
        {{
            param($candidate)
            $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $candidate.Name -eq $name
        }},
        $true
    )
    Invoke-Expression $node.Extent.Text
}}
$rejected = $false
try {{
    Remove-HiaKnowledgeInstallerOwnedDirectory `
        -ProjectRoot {_ps_literal(project_root)} `
        -Path {_ps_literal(project_root / '.runtime' / 'toolchains' / 'hia-embedding' / f'.venv-staging-{install_id}')} `
        -Kind 'staging' `
        -InstallId {_ps_literal(install_id)} | Out-Null
}} catch {{
    $rejected = $true
}}
$rejected | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(json.loads(completed.stdout))
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_legacy_candidate_failure_happens_before_backup_cleanup(
        self,
    ) -> None:
        project_root = self.sandbox / "candidate-first"
        install_id = uuid.uuid4().hex
        toolchain = (
            project_root / ".runtime" / "toolchains" / "hia-embedding"
        )
        legacy = toolchain / "venv"
        (legacy / "Scripts").mkdir(parents=True)
        (legacy / "Scripts" / "python.exe").write_bytes(b"legacy")
        (legacy / "pyvenv.cfg").write_text(
            "home = Z:\\python\nversion = 3.10.11\n",
            encoding="utf-8",
        )
        backup = toolchain / f".venv-backup-{install_id}"
        backup.mkdir()
        sentinel = backup / "keep.txt"
        sentinel.write_text("backup", encoding="utf-8")
        transaction = {
            "install_id": install_id,
            "staging_root": str(
                toolchain / f".venv-staging-{install_id}"
            ),
            "backup_root": str(backup),
            "failed_root": str(toolchain / f".venv-failed-{install_id}"),
            "legacy_root": str(legacy),
        }
        script = f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(POWERSHELL_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
$node = $ast.Find(
    {{
        param($candidate)
        $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $candidate.Name -eq 'Complete-HiaKnowledgeManagedVenvTransaction'
    }},
    $true
)
Invoke-Expression $node.Extent.Text
$script:HiaKnowledgePythonVersion = '3.10.11'
$script:RemoveCalled = $false
function Get-HiaKnowledgeManagedVenvMarker {{ return $null }}
function Test-HiaEmbeddingOrdinaryFile {{ return $true }}
function Write-HiaEmbeddingInstallLog {{ param($Level, $Message) }}
function Assert-HiaKnowledgeCleanupTarget {{
    throw 'injected candidate validation failure'
}}
function Remove-HiaKnowledgeInstallerOwnedDirectory {{
    $script:RemoveCalled = $true
}}
$transaction = ConvertFrom-Json -InputObject {_ps_literal(json.dumps(transaction))}
$failed = $false
try {{
    Complete-HiaKnowledgeManagedVenvTransaction `
        -ProjectRoot {_ps_literal(project_root)} `
        -Transaction $transaction `
        -IncludeVerifiedLegacyCandidate | Out-Null
}} catch {{
    $failed = $_.Exception.Message -eq 'injected candidate validation failure'
}}
[pscustomobject]@{{
    failed = $failed
    remove_called = $script:RemoveCalled
    backup_exists = Test-Path -LiteralPath {_ps_literal(sentinel)} -PathType Leaf
}} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["failed"], result)
        self.assertFalse(result["remove_called"], result)
        self.assertTrue(result["backup_exists"], result)


class EmbeddingInstallerFlowFailureTests(unittest.TestCase):
    _FLOW_STUB = r"""
$script:HiaFlowFailureStage = '__FAILURE_STAGE__'
$script:HiaFlowTorchProbeCount = 0

function Add-HiaFlowCall {
    param([Parameter(Mandatory = $true)][string]$Name)
    [System.IO.File]::AppendAllText(
        (Join-Path $ProjectRoot 'flow-child-calls.txt'),
        ($Name + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-HiaProjectLocalUv {
    param(
        [string]$ProjectRoot,
        [string]$ToolchainRoot,
        [string]$CacheRoot,
        [string]$TempRoot,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment
    )
    return [pscustomobject]@{
        exe = Join-Path $ProjectRoot '.runtime\toolchains\uv\uv.exe'
        version = '0.11.29'
        cache_root = Join-Path $CacheRoot 'uv'
    }
}

function Get-HiaKnowledgeManagedPython {
    param(
        [string]$ProjectRoot,
        [string]$PythonInstallRoot,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment
    )
    return Join-Path $PythonInstallRoot 'cpython-3.10.11-flow\python.exe'
}

function Test-HiaKnowledgeManagedVenv {
    param(
        [string]$ProjectRoot,
        [string]$VenvRoot,
        [string]$PythonInstallRoot,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment,
        [switch]$RequireParser,
        [switch]$RequireWorker
    )
    if (
        $script:HiaFlowFailureStage -eq 'partial-root' -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath($VenvRoot).TrimEnd('\'),
            [System.IO.Path]::GetFullPath(
                (Join-Path $ProjectRoot '.venv')
            ).TrimEnd('\')
        ) -and
        -not $RequireParser -and
        -not $RequireWorker
    ) {
        return $false
    }
    if (
        $RequireParser -and
        $script:HiaFlowFailureStage -eq 'pypdf' -and
        $VenvRoot -match '\\.venv-staging-'
    ) {
        Add-HiaFlowCall -Name 'staging-pypdf-verify-failed'
        return $false
    }
    if ($RequireWorker) {
        $runtimeVerifyName = if ($VenvRoot -match '\\.venv-staging-') {
            'staging-runtime-verify'
        } else {
            'published-runtime-verify'
        }
        Add-HiaFlowCall -Name $runtimeVerifyName
        if (
            $script:HiaFlowFailureStage -eq 'publish-validation' -and
            $VenvRoot -notmatch '\\.venv-staging-'
        ) {
            return $false
        }
    }
    return $true
}

function Get-HiaKnowledgePythonProbe {
    param(
        [string]$PythonExe,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment,
        [string]$WorkingDirectory
    )
    Add-HiaFlowCall -Name 'pypdf-verify'
    return [pscustomobject]@{
        executable = $PythonExe
        version = '3.10.11'
        bits = 64
        prefix = Join-Path $ProjectRoot '.venv'
        base_prefix = Join-Path $ProjectRoot (
            '.runtime\toolchains\python\cpython-3.10.11-flow'
        )
        no_user_site = $true
        bridge_import = $true
        mcp_import = $true
        worker_import = $true
        pypdf_version = if (
            $script:HiaFlowFailureStage -eq 'pypdf'
        ) {
            ''
        } else {
            '6.14.2'
        }
    }
}

function Get-HiaEmbeddingTorchProbe {
    param(
        [string]$PythonExe,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment,
        [string]$WorkingDirectory
    )
    Add-HiaFlowCall -Name ('torch-path:' + [string]$PythonExe)
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $null
    }
    $script:HiaFlowTorchProbeCount += 1
    Add-HiaFlowCall -Name (
        'torch-worker-verify-{0}' -f $script:HiaFlowTorchProbeCount
    )
    $installed = -not (
        $script:HiaFlowFailureStage -eq 'torch-worker' -and
        $PythonExe -match '\\.venv-staging-'
    )
    $cuda = (
        $installed -and
        $script:HiaFlowFailureStage -eq 'legacy-cuda-auto'
    )
    return [pscustomobject]@{
        installed = $installed
        torch_version = if ($installed) {
            if ($cuda) { '2.11.0+cu128' } else { '2.11.0+cpu' }
        } else { '' }
        torch_cuda_build = if ($cuda) { '12.8' } else { '' }
        cuda_available = $cuda
        device_name = if ($cuda) { 'Flow GPU' } else { '' }
    }
}

function Test-HiaNvidiaGpuAvailable {
    return $false
}

function Invoke-HiaEmbeddingChildProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{},
        [string[]]$RemoveEnvironment = @(),
        [string]$WorkingDirectory,
        [int]$TimeoutSeconds = 0
    )
    if (
        $Arguments -contains 'python' -and
        $Arguments -contains 'install' -and
        $Arguments -contains '--install-dir'
    ) {
        Add-HiaFlowCall -Name 'managed-python-install'
        return [pscustomobject]@{
            exit_code = 0
            stdout = ''
            stderr = ''
        }
    }
    if ($Arguments -contains 'venv') {
        Add-HiaFlowCall -Name 'staging-venv-create'
        $target = [string]$Arguments[$Arguments.Count - 1]
        [System.IO.Directory]::CreateDirectory(
            (Join-Path $target 'Scripts')
        ) | Out-Null
        [System.IO.File]::WriteAllText(
            (Join-Path $target 'Scripts\python.exe'),
            'flow python'
        )
        [System.IO.File]::WriteAllText(
            (Join-Path $target 'new-install.txt'),
            $script:HiaFlowFailureStage
        )
        return [pscustomobject]@{
            exit_code = 0
            stdout = ''
            stderr = ''
        }
    }
    if ($Arguments -contains 'pypdf==6.14.2') {
        Add-HiaFlowCall -Name 'pypdf-install'
        return [pscustomobject]@{
            exit_code = 0
            stdout = ''
            stderr = ''
        }
    }
    if (
        $Arguments -contains '--action' -and
        $Arguments -contains 'plan'
    ) {
        Add-HiaFlowCall -Name 'embedding-plan'
        $toolchain = Join-Path $ProjectRoot (
            '.runtime\toolchains\hia-embedding'
        )
        $cache = Join-Path $ProjectRoot '.runtime\cache\embedding'
        $models = Join-Path $ProjectRoot '.runtime\models'
        $venv = Join-Path $ProjectRoot '.venv'
        $plan = [ordered]@{
            child_environment = [ordered]@{
                PYTHONNOUSERSITE = '1'
                HIA_EMBEDDING_PYTHON = Join-Path $venv 'Scripts\python.exe'
            }
            remove_environment = @('PYTHONHOME', 'PYTHONPATH')
            required_directories = @($cache, $models)
            layout = [ordered]@{
                toolchain_root = $toolchain
                cache_root = $cache
                temp_root = Join-Path $cache 'tmp'
                venv_root = $venv
                legacy_venv_root = Join-Path $toolchain 'venv'
                worker_python = Join-Path $venv 'Scripts\python.exe'
                worker_source = 'flow-worker'
            }
            profile_id = 'flow-profile'
            model_id = 'Qwen/Flow-Model'
            model_dir = Join-Path $models 'flow-profile'
            dimension = 1024
            revision = 'main'
            worker_distribution = 'flow-worker'
        }
        return [pscustomobject]@{
            exit_code = 0
            stdout = $plan | ConvertTo-Json -Depth 8 -Compress
            stderr = ''
        }
    }
    if ($Arguments -contains 'flow-worker') {
        Add-HiaFlowCall -Name 'worker-install'
        return [pscustomobject]@{
            exit_code = 0
            stdout = ''
            stderr = ''
        }
    }
    if (
        $Arguments -contains '--action' -and
        $Arguments -contains 'download'
    ) {
        Add-HiaFlowCall -Name 'model-download'
        $output = if ($script:HiaFlowFailureStage -eq 'model') {
            '{invalid-model-json'
        } else {
            [ordered]@{
                status = 'already_installed'
                profile_id = 'flow-profile'
                model_id = 'Qwen/Flow-Model'
                model_dir = Join-Path $ProjectRoot (
                    '.runtime\models\flow-profile'
                )
            } | ConvertTo-Json -Compress
        }
        return [pscustomobject]@{
            exit_code = 0
            stdout = $output
            stderr = ''
        }
    }
    if (
        $Arguments -contains '--action' -and
        $Arguments -contains 'smoke'
    ) {
        Add-HiaFlowCall -Name 'model-smoke'
        $output = if ($script:HiaFlowFailureStage -eq 'smoke') {
            '{invalid-smoke-json'
        } else {
            [ordered]@{
                status = 'ready'
                profile_id = 'flow-profile'
                model_id = 'Qwen/Flow-Model'
                revision = 'main'
                model_dir = Join-Path $ProjectRoot (
                    '.runtime\models\flow-profile'
                )
                dimension = 1024
                device = if (
                    $script:HiaFlowFailureStage -eq 'legacy-cuda-auto'
                ) { 'cuda' } else { 'cpu' }
                norm = 1.0
            } | ConvertTo-Json -Compress
        }
        return [pscustomobject]@{
            exit_code = 0
            stdout = $output
            stderr = ''
        }
    }
    throw 'Flow test reached an unexpected child-process boundary.'
}
"""

    def setUp(self) -> None:
        self.sandbox = (
            REPOSITORY_ROOT
            / f".hia-installer-flow-test-{uuid.uuid4().hex}"
        )
        self.sandbox.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox)

    @staticmethod
    def _write_managed_marker(path: Path, install_id: str) -> bytes:
        payload = {
            "schema": "hia-managed-python-venv/1",
            "role": "hia-embedding",
            "install_id": install_id,
            "python_version": "3.10.11",
            "managed_python": (
                ".runtime/toolchains/python/"
                "cpython-3.10.11-flow/python.exe"
            ),
        }
        encoded = (
            json.dumps(payload, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        (path / ".hia-managed-venv.json").write_bytes(encoded)
        return encoded

    def _run_instrumented_flow(
        self,
        *,
        name: str,
        stage: str = "success",
        canonical: str = "missing",
        legacy: str = "missing",
        parser_only: bool = False,
        repair: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, list[str]]:
        project_root = self.sandbox / name
        project_root.mkdir(parents=True)
        canonical_root = project_root / ".venv"
        toolchain = (
            project_root / ".runtime" / "toolchains" / "hia-embedding"
        )
        if canonical == "reparse":
            junction_target = (
                project_root / ".runtime" / "unsafe-root-venv"
            )
            junction_target.mkdir(parents=True)
            self._write_managed_marker(junction_target, "a" * 32)
            created = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "New-Item -ItemType Junction "
                        f"-Path {_ps_literal(canonical_root)} "
                        f"-Target {_ps_literal(junction_target)} | Out-Null"
                    ),
                ],
                cwd=project_root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if created.returncode != 0:
                self.skipTest("Windows junction creation is unavailable")
        elif canonical != "missing":
            canonical_root.mkdir()
            if canonical != "unmarked":
                self._write_managed_marker(canonical_root, "a" * 32)
            (canonical_root / "old-environment.txt").write_text(
                canonical,
                encoding="utf-8",
            )
        legacy_root = toolchain / "venv"
        if legacy != "missing":
            (legacy_root / "Scripts").mkdir(parents=True)
            if legacy == "marked":
                self._write_managed_marker(legacy_root, "b" * 32)
            elif legacy == "shape":
                (legacy_root / "pyvenv.cfg").write_text(
                    "home = Z:\\old-python\nversion = 3.10.11\n",
                    encoding="utf-8",
                )
            (legacy_root / "Scripts" / "python.exe").write_bytes(
                b"legacy python"
            )
            (legacy_root / "legacy-sentinel.txt").write_text(
                legacy,
                encoding="utf-8",
            )

        managed_python = (
            project_root
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10.11-flow"
            / "python.exe"
        )
        managed_python.parent.mkdir(parents=True)
        managed_python.write_bytes(b"flow managed python")
        uv_exe = (
            project_root
            / ".runtime"
            / "toolchains"
            / "uv"
            / "uv.exe"
        )
        uv_exe.parent.mkdir(parents=True)
        uv_exe.write_bytes(b"flow uv")
        helper = (
            project_root
            / "scripts"
            / "launcher"
            / "install_hia_embedding.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("# flow boundary\n", encoding="utf-8")

        source = POWERSHELL_INSTALLER_PATH.read_text(encoding="utf-8")
        insertion = "\ntry {\n$directorySeparators"
        self.assertEqual(1, source.count(insertion))
        instrumented = source.replace(
            insertion,
            "\n" + self._FLOW_STUB.replace("__FAILURE_STAGE__", stage)
            + insertion,
            1,
        )
        flow_script = self.sandbox / f"Install-{name}-Flow.ps1"
        flow_script.write_text(instrumented, encoding="utf-8")
        arguments = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(flow_script),
            "-ProjectRoot",
            str(project_root),
            "-Profile",
            "flow-profile",
            "-Device",
            "auto" if stage == "legacy-cuda-auto" else "cpu",
        ]
        if parser_only:
            arguments.append("-KnowledgeParserOnly")
        if repair:
            arguments.append("-Repair")
        completed = subprocess.run(
            arguments,
            cwd=project_root,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "UV_OFFLINE": "1",
            },
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        calls_path = project_root / "flow-child-calls.txt"
        calls = (
            calls_path.read_text(encoding="utf-8").splitlines()
            if calls_path.is_file()
            else []
        )
        return completed, canonical_root, toolchain, calls

    def test_fresh_full_install_validates_staging_before_single_publish(
        self,
    ) -> None:
        completed, canonical, toolchain, calls = self._run_instrumented_flow(
            name="fresh-success",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertEqual(str(canonical), result["venv_root"])
        self.assertEqual(
            str(
                canonical.parent
                / ".runtime"
                / "cache"
                / "embedding"
                / "uv"
            ),
            result["uv_cache_cleanup_candidate"],
        )
        self.assertTrue((canonical / ".hia-managed-venv.json").is_file())
        self.assertEqual([], list(toolchain.glob(".venv-staging-*")))
        self.assertEqual([], list(toolchain.glob(".venv-backup-*")))
        self.assertEqual([], list(toolchain.glob(".venv-failed-*")))
        first_model = calls.index("model-download")
        second_model = calls.index("model-download", first_model + 1)
        smoke = calls.index("model-smoke")
        self.assertLess(calls.index("pypdf-install"), calls.index("worker-install"))
        self.assertLess(calls.index("worker-install"), calls.index("staging-runtime-verify"))
        self.assertLess(calls.index("staging-runtime-verify"), first_model)
        self.assertLess(first_model, smoke)
        self.assertLess(smoke, calls.index("published-runtime-verify"))
        self.assertLess(calls.index("published-runtime-verify"), second_model)

    def test_legacy_only_reports_explicit_cleanup_candidate_after_migration(
        self,
    ) -> None:
        completed, canonical, toolchain, _calls = self._run_instrumented_flow(
            name="legacy-shape-success",
            legacy="shape",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertTrue(canonical.is_dir())
        self.assertTrue((toolchain / "venv").is_dir())
        self.assertEqual(
            str(toolchain / "venv"),
            result["legacy_cleanup_candidate"],
        )

    def test_unknown_legacy_directory_is_preserved_after_success(self) -> None:
        completed, canonical, toolchain, _calls = self._run_instrumented_flow(
            name="legacy-unknown-success",
            legacy="unknown",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertTrue(canonical.is_dir())
        self.assertTrue((toolchain / "venv" / "legacy-sentinel.txt").is_file())
        self.assertEqual("", result["legacy_cleanup_candidate"])

    def test_auto_uses_only_safe_exact_legacy_torch_probe(self) -> None:
        completed, _canonical, toolchain, calls = self._run_instrumented_flow(
            name="legacy-cuda-auto",
            stage="legacy-cuda-auto",
            legacy="shape",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertEqual("cuda", result["resolved_device"])
        legacy_python = str(toolchain / "venv" / "Scripts" / "python.exe")
        self.assertEqual(
            f"torch-path:{legacy_python}",
            next(call for call in calls if call.startswith("torch-path:")),
        )

    def test_healthy_root_venv_parser_install_is_idempotent(self) -> None:
        completed, canonical, toolchain, calls = self._run_instrumented_flow(
            name="healthy-idempotent",
            canonical="healthy",
            parser_only=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            "healthy",
            (canonical / "old-environment.txt").read_text(encoding="utf-8"),
        )
        self.assertNotIn("staging-venv-create", calls)
        self.assertEqual([], list(toolchain.glob(".venv-*")))

    def test_unmarked_and_partial_root_venv_fail_closed(self) -> None:
        cases = (
            ("unmarked", "unmarked", "success", "refusing", True),
            ("partial", "partial", "partial-root", "refusing", False),
            ("reparse", "reparse", "success", "reparse", False),
        )
        for name, canonical, stage, diagnostic, repair in cases:
            with self.subTest(case=name):
                completed, root_venv, toolchain, calls = (
                    self._run_instrumented_flow(
                        name=f"unsafe-{name}",
                        stage=stage,
                        canonical=canonical,
                        repair=repair,
                    )
                )
                self.assertEqual(1, completed.returncode, completed.stderr)
                self.assertIn(diagnostic, completed.stderr.lower())
                self.assertTrue(root_venv.is_dir())
                self.assertNotIn("staging-venv-create", calls)
                self.assertEqual([], list(toolchain.glob(".venv-staging-*")))

    def test_post_publish_validation_failure_restores_and_preserves_backup(
        self,
    ) -> None:
        completed, canonical, toolchain, calls = self._run_instrumented_flow(
            name="publish-validation-failure",
            stage="publish-validation",
            canonical="healthy",
        )

        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertIn("published root .venv failed", completed.stderr)
        self.assertEqual(
            "healthy",
            (canonical / "old-environment.txt").read_text(encoding="utf-8"),
        )
        backups = list(toolchain.glob(".venv-backup-*"))
        failed = list(toolchain.glob(".venv-failed-*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(1, len(failed))
        self.assertEqual(
            "healthy",
            (backups[0] / "old-environment.txt").read_text(encoding="utf-8"),
        )
        self.assertIn("published-runtime-verify", calls)

    def _run_failure(
        self,
        stage: str,
        diagnostic: str,
        required_calls: tuple[str, ...],
    ) -> None:
        self.assertIn(stage, {"pypdf", "torch-worker", "model", "smoke"})
        project_root = self.sandbox / f"{stage}-project"
        canonical = project_root / ".venv"
        canonical.mkdir(parents=True)
        old_install_id = "a" * 32
        old_marker = {
            "schema": "hia-managed-python-venv/1",
            "role": "hia-embedding",
            "install_id": old_install_id,
            "python_version": "3.10.11",
            "managed_python": (
                ".runtime/toolchains/python/"
                "cpython-3.10.11-flow/python.exe"
            ),
        }
        marker_path = canonical / ".hia-managed-venv.json"
        marker_bytes = (
            json.dumps(old_marker, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        marker_path.write_bytes(marker_bytes)
        old_sentinel = canonical / "old-environment.txt"
        old_sentinel.write_text("prior working venv", encoding="utf-8")

        managed_python = (
            project_root
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10.11-flow"
            / "python.exe"
        )
        managed_python.parent.mkdir(parents=True)
        managed_python.write_bytes(b"flow managed python")
        uv_exe = (
            project_root
            / ".runtime"
            / "toolchains"
            / "uv"
            / "uv.exe"
        )
        uv_exe.parent.mkdir(parents=True)
        uv_exe.write_bytes(b"flow uv")
        helper = (
            project_root
            / "scripts"
            / "launcher"
            / "install_hia_embedding.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("# flow boundary\n", encoding="utf-8")

        source = POWERSHELL_INSTALLER_PATH.read_text(encoding="utf-8")
        insertion = "\ntry {\n$directorySeparators"
        self.assertEqual(1, source.count(insertion))
        stub = self._FLOW_STUB.replace("__FAILURE_STAGE__", stage)
        instrumented = source.replace(
            insertion,
            "\n" + stub + insertion,
            1,
        )
        flow_script = self.sandbox / f"Install-{stage}-Flow.ps1"
        flow_script.write_text(instrumented, encoding="utf-8")

        environment = os.environ.copy()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "UV_OFFLINE": "1",
            }
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(flow_script),
                "-ProjectRoot",
                str(project_root),
                "-Profile",
                "flow-profile",
                "-Device",
                "cpu",
            ],
            cwd=project_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertIn("Embedding installation failed:", completed.stderr)
        self.assertIn(diagnostic, completed.stderr)
        logs = list(
            (project_root / ".runtime" / "launcher").glob(
                "embedding-install-*.log"
            )
        )
        self.assertEqual(1, len(logs))
        self.assertIn(diagnostic, logs[0].read_text(encoding="utf-8"))

        self.assertTrue(canonical.is_dir())
        self.assertFalse(canonical.is_symlink())
        self.assertEqual(marker_bytes, marker_path.read_bytes())
        self.assertEqual(
            "prior working venv",
            old_sentinel.read_text(encoding="utf-8"),
        )
        self.assertFalse((canonical / "new-install.txt").exists())

        toolchain = (
            project_root / ".runtime" / "toolchains" / "hia-embedding"
        )
        isolated = list(toolchain.glob(".venv-failed-*"))
        self.assertEqual(1, len(isolated))
        isolated_marker = json.loads(
            (
                isolated[0] / ".hia-managed-venv.json"
            ).read_text(encoding="utf-8")
        )
        new_install_id = isolated_marker["install_id"]
        self.assertNotEqual(old_install_id, new_install_id)
        self.assertEqual(f".venv-failed-{new_install_id}", isolated[0].name)
        self.assertEqual(
            stage,
            (isolated[0] / "new-install.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual([], list(toolchain.glob(".venv-backup-*")))
        self.assertEqual([], list(toolchain.glob(".venv-staging-*")))

        calls = (
            project_root / "flow-child-calls.txt"
        ).read_text(encoding="utf-8").splitlines()
        for required in required_calls:
            self.assertIn(required, calls)

    def test_full_flow_pypdf_validation_failure_restores_previous_venv(
        self,
    ) -> None:
        self._run_failure(
            "pypdf",
            (
                "The staged project-local PDF parser environment did not "
                "pass validation."
            ),
            (
                "staging-venv-create",
                "pypdf-install",
                "staging-pypdf-verify-failed",
            ),
        )

    def test_full_flow_torch_worker_validation_failure_restores_previous_venv(
        self,
    ) -> None:
        self._run_failure(
            "torch-worker",
            (
                "PyTorch is unavailable in the project-local embedding "
                "environment."
            ),
            (
                "staging-venv-create",
                "worker-install",
                "torch-worker-verify-1",
            ),
        )

    def test_full_flow_model_validation_failure_restores_previous_venv(
        self,
    ) -> None:
        self._run_failure(
            "model",
            "Selected embedding model installation returned invalid JSON.",
            (
                "staging-venv-create",
                "worker-install",
                "model-download",
            ),
        )

    def test_full_flow_smoke_failure_never_publishes_staging(self) -> None:
        self._run_failure(
            "smoke",
            "Staged embedding model encode smoke returned invalid JSON.",
            (
                "staging-venv-create",
                "worker-install",
                "model-download",
                "model-smoke",
            ),
        )


class ManagedPythonUvAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.powershell = shutil.which("powershell.exe")
        if self.powershell is None:
            self.skipTest("Windows PowerShell is unavailable")
        self.sandbox = (
            REPOSITORY_ROOT
            / f".hia-managed-python-alias-test-{uuid.uuid4().hex}"
        )
        self.sandbox.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox)

    def _create_junction(self, link: Path, target: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "New-Item -ItemType Junction "
                    f"-Path {_ps_literal(link)} "
                    f"-Target {_ps_literal(target)} | Out-Null"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if completed.returncode != 0:
            self.skipTest(
                "Windows directory junctions are unavailable: "
                + completed.stderr.strip()
            )

    def _versioned_python(self, project_root: Path) -> Path:
        candidate = (
            project_root
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10.11-windows-x86_64-none"
            / "python.exe"
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"managed python")
        return candidate

    def _resolve_managed_python(
        self,
        project_root: Path,
    ) -> dict[str, object]:
        install_root = (
            project_root / ".runtime" / "toolchains" / "python"
        )
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(POWERSHELL_INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'installer AST failed' }}
$wanted = @(
    'Test-HiaEmbeddingOrdinaryFile',
    'Assert-HiaKnowledgeProjectDirectory',
    'Get-HiaKnowledgeManagedPython'
)
foreach ($name in $wanted) {{
    $node = $ast.Find(
        {{
            param($candidate)
            $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $candidate.Name -eq $name
        }},
        $true
    )
    if ($null -eq $node) {{ throw "missing function $name" }}
    Invoke-Expression $node.Extent.Text
}}
$script:HiaKnowledgePythonVersion = '3.10.11'
$script:HiaManagedPythonProbePaths = @()
function Get-HiaKnowledgePythonProbe {{
    param(
        [string]$PythonExe,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment,
        [string]$WorkingDirectory
    )
    $script:HiaManagedPythonProbePaths += [string]$PythonExe
    return [pscustomobject]@{{
        version = '3.10.11'
        bits = 64
    }}
}}
$result = [ordered]@{{
    ok = $false
    path = ''
    error = ''
    probe_paths = @()
}}
try {{
    $result.path = Get-HiaKnowledgeManagedPython `
        -ProjectRoot {_ps_literal(project_root)} `
        -PythonInstallRoot {_ps_literal(install_root)} `
        -Environment @{{}} `
        -RemoveEnvironment @('PYTHONHOME')
    $result.ok = $true
}} catch {{
    $result.error = [string]$_.Exception.Message
}}
$result.probe_paths = @($script:HiaManagedPythonProbePaths)
$result | ConvertTo-Json -Compress
"""
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
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )
        return json.loads(completed.stdout.strip())

    def test_plain_versioned_managed_python_directory_still_succeeds(
        self,
    ) -> None:
        project_root = self.sandbox / "plain" / "project"
        expected = self._versioned_python(project_root)

        result = self._resolve_managed_python(project_root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(expected.resolve(), Path(str(result["path"])).resolve())
        self.assertEqual(
            [str(expected.resolve())],
            [str(Path(path).resolve()) for path in result["probe_paths"]],
        )

    def test_safe_uv_same_root_alias_is_skipped_for_versioned_directory(
        self,
    ) -> None:
        project_root = self.sandbox / "safe-alias" / "project"
        expected = self._versioned_python(project_root)
        alias = (
            expected.parent.parent
            / "cpython-3.10-windows-x86_64-none"
        )
        self._create_junction(alias, expected.parent)

        result = self._resolve_managed_python(project_root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(expected.resolve(), Path(str(result["path"])).resolve())
        self.assertNotIn(
            "cpython-3.10-windows-x86_64-none",
            str(result["path"]),
        )
        self.assertEqual(
            [str(expected.resolve())],
            [str(Path(path).resolve()) for path in result["probe_paths"]],
        )

    def test_unsafe_managed_python_junctions_fail_closed(self) -> None:
        cases: list[tuple[str, Path]] = []

        outside_project = self.sandbox / "outside-target" / "project"
        self._versioned_python(outside_project)
        outside_target = (
            self.sandbox
            / "outside-target"
            / "external"
            / "cpython-3.10.11-windows-x86_64-none"
        )
        outside_target.mkdir(parents=True)
        outside_alias = (
            outside_project
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10-windows-x86_64-none"
        )
        self._create_junction(outside_alias, outside_target)
        cases.append(("outside target", outside_project))

        missing_project = self.sandbox / "missing-target" / "project"
        missing_install = (
            missing_project / ".runtime" / "toolchains" / "python"
        )
        missing_install.mkdir(parents=True)
        missing_target = (
            missing_install / "cpython-3.10.11-windows-x86_64-none"
        )
        missing_target.mkdir()
        missing_alias = (
            missing_install / "cpython-3.10-windows-x86_64-none"
        )
        self._create_junction(missing_alias, missing_target)
        missing_target.rmdir()
        cases.append(("missing target", missing_project))

        chain_project = self.sandbox / "target-chain" / "project"
        install_root = (
            chain_project / ".runtime" / "toolchains" / "python"
        )
        payload = install_root / "payload"
        payload.mkdir(parents=True)
        (payload / "python.exe").write_bytes(b"managed python")
        versioned_link = (
            install_root / "cpython-3.10.11-windows-x86_64-none"
        )
        alias_link = install_root / "cpython-3.10-windows-x86_64-none"
        self._create_junction(versioned_link, payload)
        self._create_junction(alias_link, versioned_link)
        cases.append(("target reparse chain", chain_project))

        wrong_target_project = self.sandbox / "wrong-target" / "project"
        wrong_install = (
            wrong_target_project / ".runtime" / "toolchains" / "python"
        )
        wrong_target = (
            wrong_install / "cpython-3.10.12-windows-x86_64-none"
        )
        wrong_target.mkdir(parents=True)
        (wrong_target / "python.exe").write_bytes(b"managed python")
        wrong_alias = (
            wrong_install / "cpython-3.10-windows-x86_64-none"
        )
        self._create_junction(wrong_alias, wrong_target)
        cases.append(("wrong target name", wrong_target_project))

        unknown_project = self.sandbox / "unknown-link" / "project"
        unknown_target = self._versioned_python(unknown_project).parent
        unknown_link = (
            unknown_target.parent / "unexpected-python-junction"
        )
        self._create_junction(unknown_link, unknown_target)
        cases.append(("unknown junction", unknown_project))

        for label, project_root in cases:
            with self.subTest(case=label):
                result = self._resolve_managed_python(project_root)
                self.assertFalse(result["ok"], result)
                self.assertIn(
                    "unsafe reparse point",
                    str(result["error"]).lower(),
                )


if __name__ == "__main__":
    unittest.main()
