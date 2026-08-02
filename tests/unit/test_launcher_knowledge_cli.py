from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "hia_knowledge_cli.py"
WRAPPER_PATH = REPOSITORY_ROOT / "scripts" / "hia-knowledge.ps1"
INSTALLER_PATH = (
    REPOSITORY_ROOT / "scripts" / "launcher" / "Install-HiaEmbedding.ps1"
)
KNOWLEDGE_INDEX_PATH = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_mcp_runtime"
    / "knowledge_index.py"
)
KNOWLEDGE_INDEX_CLI_PATH = KNOWLEDGE_INDEX_PATH.with_name(
    "knowledge_index_cli.py"
)
KNOWLEDGE_ASSETS_PATH = KNOWLEDGE_INDEX_PATH.with_name("knowledge_assets.py")
LOCAL_EXTRACTORS_PATH = KNOWLEDGE_INDEX_PATH.with_name("local_extractors.py")
HYBRID_KNOWLEDGE_PATH = KNOWLEDGE_INDEX_PATH.with_name("hybrid_knowledge.py")
DETERMINISTIC_SOURCES_PATH = KNOWLEDGE_INDEX_PATH.with_name(
    "deterministic_sources.py"
)
EMBEDDING_CLIENT_PATH = KNOWLEDGE_INDEX_PATH.with_name(
    "embedding_client.py"
)
EMBEDDING_CONTRACT_PATH = (
    REPOSITORY_ROOT / "src" / "hia_core" / "embedding_contract.py"
)
TEST_RUN_ROOT = REPOSITORY_ROOT / ".runtime" / "test-runs"
TEST_RUN_ROOT.mkdir(parents=True, exist_ok=True)


def _powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise unittest.SkipTest("PowerShell is unavailable")


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class LauncherKnowledgeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_RUN_ROOT)
        self.temporary_root = Path(self._temporary.name)
        self.project_root = self.temporary_root / "moved knowledge project"
        self.project_root.mkdir()
        self._copy_project_runtime(self.project_root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _copy_project_runtime(root: Path, *, wrapper: bool = False) -> None:
        targets = (
            (
                KNOWLEDGE_INDEX_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "knowledge_index.py",
            ),
            (
                KNOWLEDGE_INDEX_CLI_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "knowledge_index_cli.py",
            ),
            (
                KNOWLEDGE_ASSETS_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "knowledge_assets.py",
            ),
            (
                LOCAL_EXTRACTORS_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "local_extractors.py",
            ),
            (
                HYBRID_KNOWLEDGE_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "hybrid_knowledge.py",
            ),
            (
                DETERMINISTIC_SOURCES_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "deterministic_sources.py",
            ),
            (
                EMBEDDING_CLIENT_PATH,
                root
                / "houdini_package"
                / "python_libs"
                / "hia_mcp_runtime"
                / "embedding_client.py",
            ),
            (
                EMBEDDING_CONTRACT_PATH,
                root / "src" / "hia_core" / "embedding_contract.py",
            ),
        )
        for source, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (
            root
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "__init__.py"
        ).write_text("", encoding="utf-8")
        if wrapper:
            wrapper_target = root / "scripts" / "hia-knowledge.ps1"
            helper_target = root / "scripts" / "launcher" / "hia_knowledge_cli.py"
            installer_target = (
                root
                / "scripts"
                / "launcher"
                / "Install-HiaEmbedding.ps1"
            )
            wrapper_target.parent.mkdir(parents=True, exist_ok=True)
            helper_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(WRAPPER_PATH, wrapper_target)
            shutil.copyfile(HELPER_PATH, helper_target)
            shutil.copyfile(INSTALLER_PATH, installer_target)

    def _run_helper(
        self,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], str]:
        child_environment = dict(os.environ)
        if environment:
            child_environment.update(environment)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(HELPER_PATH),
                "--project-root",
                str(self.project_root),
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            env=child_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        lines = [line for line in completed.stdout.splitlines() if line]
        self.assertEqual(
            1,
            len(lines),
            completed.stdout + completed.stderr,
        )
        return completed.returncode, json.loads(lines[0]), completed.stderr

    @staticmethod
    def _load_helper_module() -> object:
        name = "_test_launcher_hia_knowledge_cli"
        specification = importlib.util.spec_from_file_location(name, HELPER_PATH)
        assert specification is not None
        assert specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        specification.loader.exec_module(module)
        return module

    def _managed_environment_fixture(self, module: object) -> tuple[Path, Path]:
        contract = module._load_project_module(self.project_root, "contract")
        layout = contract.runtime_layout(self.project_root)
        venv = Path(layout["venv_root"])
        python = Path(layout["worker_python"])
        python.parent.mkdir(parents=True)
        python.write_bytes(b"test python placeholder")
        base_prefix = (
            self.project_root
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10.11-test"
        )
        base_prefix.mkdir(parents=True)
        marker = venv / module.VENV_MARKER_NAME
        marker.write_text(
            json.dumps(
                {
                    "schema": module.VENV_MARKER_SCHEMA,
                    "role": "hia-embedding",
                    "python_version": "3.10.11",
                }
            ),
            encoding="utf-8",
        )
        profile_id = contract.DEFAULT_EMBEDDING_PROFILE
        profile = contract.PROFILE_REGISTRY[profile_id]
        model = self.project_root / profile.model_directory
        model.mkdir(parents=True)
        (model / "config.json").write_text("{}", encoding="utf-8")
        (model / "model.safetensors").write_bytes(b"weights")
        (model / ".hia-embedding-model.json").write_text(
            json.dumps(
                {
                    "contract_version": contract.EMBEDDING_CONTRACT_VERSION,
                    "profile_id": profile_id,
                    "model_id": profile.model_id,
                    "revision": "test-revision",
                }
            ),
            encoding="utf-8",
        )
        return python, base_prefix

    def test_moved_root_status_is_project_relative_and_reports_missing(self) -> None:
        exit_code, payload, _stderr = self._run_helper("environment-status")

        self.assertEqual(0, exit_code)
        self.assertTrue(payload["ok"])
        self.assertEqual(str(self.project_root), payload["project_root"])
        result = payload["result"]
        self.assertEqual("missing", result["state"])
        self.assertTrue(
            Path(result["venv"]["path"]).is_relative_to(self.project_root)
        )
        self.assertTrue(
            Path(result["layout"]["worker_python"]).is_relative_to(
                self.project_root
            )
        )
        self.assertEqual("fts5", result["embedding_mode"])
        self.assertFalse(result["houdini_launch_blocked"])

    def test_external_base_prefix_is_not_portable_or_green(self) -> None:
        module = self._load_helper_module()
        python, _managed_base = self._managed_environment_fixture(module)
        venv = python.parents[1]
        probe = {
            "available": True,
            "executable": str(python),
            "version": "3.10.11",
            "bits": 64,
            "prefix": str(venv),
            "base_prefix": str(self.temporary_root / "external-python"),
            "pypdf_version": "6.14.2",
            "embedding_worker_version": "0.1.0",
            "torch_installed": True,
            "torch_version": "2.11.0",
            "torch_cuda_build": "12.8",
            "cuda_available": True,
            "gpu_name": "Test NVIDIA GPU",
        }
        with mock.patch.object(module, "_python_probe", return_value=probe):
            result = module._environment_status(self.project_root)

        self.assertEqual("repair_required", result["state"])
        self.assertFalse(result["venv"]["portable"])
        self.assertFalse(result["python"]["base_prefix_is_project_local"])
        self.assertEqual("fts5", result["embedding_mode"])
        self.assertEqual({}, result["embedding_runtime_environment"])

    def test_legacy_cuda_environment_is_reported_but_fails_closed(self) -> None:
        module = self._load_helper_module()
        python, _managed_base = self._managed_environment_fixture(module)
        contract = module._load_project_module(self.project_root, "contract")
        profile_id = contract.DEFAULT_EMBEDDING_PROFILE
        probe = {
            "available": True,
            "executable": str(python),
            "version": "3.10.11",
            "bits": 64,
            "prefix": str(python.parents[1]),
            "base_prefix": str(self.temporary_root / "legacy-external-python"),
            "pypdf_version": "",
            "embedding_worker_version": "0.1.0",
            "torch_installed": True,
            "torch_version": "2.11.0+cu128",
            "torch_cuda_build": "12.8",
            "cuda_available": True,
            "gpu_name": "NVIDIA GeForce RTX Test",
        }

        with mock.patch.object(module, "_python_probe", return_value=probe):
            result = module._environment_status(self.project_root)

        model = next(
            item
            for item in result["models"]["items"]
            if item["profile_id"] == profile_id
        )
        self.assertEqual("repair_required", result["state"])
        self.assertFalse(result["venv"]["portable"])
        self.assertFalse(result["python"]["base_prefix_is_project_local"])
        self.assertFalse(result["parser"]["installed"])
        self.assertTrue(result["torch"]["installed"])
        self.assertEqual("2.11.0+cu128", result["torch"]["version"])
        self.assertTrue(result["torch"]["cuda_available"])
        self.assertEqual("NVIDIA GeForce RTX Test", result["torch"]["gpu_name"])
        self.assertTrue(result["embedding_worker"]["installed"])
        self.assertTrue(model["installed"])
        self.assertEqual(profile_id, result["active_profile"])
        self.assertEqual("fts5", result["embedding_mode"])
        self.assertEqual("", result["active_device"])
        self.assertEqual({}, result["embedding_runtime_environment"])
        self.assertFalse(result["houdini_launch_blocked"])

    def test_aggregate_status_missing_database_is_zero_write(self) -> None:
        before_directories = tuple(
            sorted(
                path.relative_to(self.project_root).as_posix()
                for path in self.project_root.rglob("*")
                if path.is_dir()
            )
        )
        before_files = {
            path.relative_to(self.project_root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.project_root.rglob("*")
            if path.is_file()
        }

        exit_code, payload, stderr = self._run_helper("status")

        self.assertEqual(0, exit_code, stderr)
        result = payload["result"]
        self.assertEqual("not_initialized", result["index"]["status"])
        self.assertFalse(result["index"]["initialized"])
        self.assertFalse(result["index"]["available"])
        self.assertEqual(
            "KNOWLEDGE_INDEX_NOT_INITIALIZED",
            result["index"]["error"],
        )
        self.assertFalse(
            (
                self.project_root
                / ".runtime"
                / "knowledge"
                / "knowledge.sqlite3"
            ).exists()
        )
        after_directories = tuple(
            sorted(
                path.relative_to(self.project_root).as_posix()
                for path in self.project_root.rglob("*")
                if path.is_dir()
            )
        )
        after_files = {
            path.relative_to(self.project_root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_directories, after_directories)
        self.assertEqual(before_files, after_files)

    def test_aggregate_status_reads_partial_database_without_writes(self) -> None:
        knowledge_root = self.project_root / ".runtime" / "knowledge"
        sources_root = knowledge_root / "sources"
        sources_root.mkdir(parents=True)
        database = knowledge_root / "knowledge.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    source_group TEXT NOT NULL
                );
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL
                );
                CREATE TABLE chunk_vectors (
                    chunk_id INTEGER PRIMARY KEY
                );
                """
            )
            connection.execute(
                "INSERT INTO documents(id, source_group) VALUES (1, 'user')"
            )
            connection.executemany(
                "INSERT INTO chunks(id, document_id) VALUES (?, 1)",
                ((index,) for index in range(1, 31813)),
            )
            connection.executemany(
                "INSERT INTO chunk_vectors(chunk_id) VALUES (?)",
                ((index,) for index in range(1, 30988)),
            )
            connection.commit()
        finally:
            connection.close()
        before_bytes = database.read_bytes()
        before_mtime = database.stat().st_mtime_ns
        before_entries = tuple(
            sorted(
                path.relative_to(knowledge_root).as_posix()
                for path in knowledge_root.rglob("*")
            )
        )

        exit_code, payload, stderr = self._run_helper("status")

        self.assertEqual(0, exit_code, stderr)
        index = payload["result"]["index"]
        self.assertTrue(index["initialized"])
        self.assertTrue(index["available"])
        self.assertEqual("ready", index["status"])
        self.assertEqual(31812, index["total_chunks"])
        self.assertEqual(30987, index["vector_chunks"])
        self.assertEqual(825, index["pending_chunks"])
        self.assertTrue(index["partial"])
        self.assertFalse(index["complete"])
        self.assertEqual(before_bytes, database.read_bytes())
        self.assertEqual(before_mtime, database.stat().st_mtime_ns)
        self.assertEqual(
            before_entries,
            tuple(
                sorted(
                    path.relative_to(knowledge_root).as_posix()
                    for path in knowledge_root.rglob("*")
                )
            ),
        )

    def test_verified_cpu_and_cuda_probes_drive_the_same_runtime_environment(
        self,
    ) -> None:
        module = self._load_helper_module()
        python, managed_base = self._managed_environment_fixture(module)
        venv = python.parents[1]
        common = {
            "available": True,
            "executable": str(python),
            "version": "3.10.11",
            "bits": 64,
            "prefix": str(venv),
            "base_prefix": str(managed_base),
            "pypdf_version": "6.14.2",
            "embedding_worker_version": "0.1.0",
            "torch_installed": True,
            "torch_version": "2.11.0",
            "torch_cuda_build": "12.8",
        }
        cpu_probe = {
            **common,
            "cuda_available": False,
            "gpu_name": "",
        }
        cuda_probe = {
            **common,
            "cuda_available": True,
            "gpu_name": "Test NVIDIA GPU",
        }
        with mock.patch.object(module, "_python_probe", return_value=cpu_probe):
            cpu = module._environment_status(self.project_root)
        with mock.patch.object(module, "_python_probe", return_value=cuda_probe):
            cuda = module._environment_status(self.project_root)

        self.assertEqual("ready", cpu["state"])
        self.assertTrue(cpu["venv"]["portable"])
        self.assertEqual("cpu_embedding", cpu["embedding_mode"])
        self.assertEqual(
            "cpu",
            cpu["embedding_runtime_environment"]["HIA_EMBEDDING_DEVICE"],
        )
        self.assertEqual("ready", cuda["state"])
        self.assertEqual("cuda", cuda["embedding_mode"])
        self.assertEqual("Test NVIDIA GPU", cuda["torch"]["gpu_name"])
        self.assertEqual(
            "cuda",
            cuda["embedding_runtime_environment"]["HIA_EMBEDDING_DEVICE"],
        )

    def test_environment_paths_are_re_resolved_after_project_move(self) -> None:
        module = self._load_helper_module()
        python, managed_base = self._managed_environment_fixture(module)
        old_root = self.project_root
        relative_python = python.relative_to(old_root)
        relative_base = managed_base.relative_to(old_root)
        moved_root = self.temporary_root / "project after move"
        old_root_key = os.path.normcase(str(old_root))
        for cache_key in tuple(module._MODULE_CACHE):
            if cache_key[0] != old_root_key:
                continue
            cached_module = module._MODULE_CACHE.pop(cache_key)
            sys.modules.pop(cached_module.__name__, None)
        importlib.invalidate_caches()
        gc.collect()
        for attempt in range(3):
            try:
                old_root.rename(moved_root)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 2:
                    raise
                time.sleep(0.05)
        self.project_root = moved_root
        moved_python = moved_root / relative_python
        moved_base = moved_root / relative_base
        moved_module = self._load_helper_module()
        probe = {
            "available": True,
            "executable": str(moved_python),
            "version": "3.10.11",
            "bits": 64,
            "prefix": str(moved_python.parents[1]),
            "base_prefix": str(moved_base),
            "pypdf_version": "6.14.2",
            "embedding_worker_version": "0.1.0",
            "torch_installed": True,
            "torch_version": "2.11.0+cu128",
            "torch_cuda_build": "12.8",
            "cuda_available": True,
            "gpu_name": "NVIDIA GeForce RTX Test",
        }

        with mock.patch.object(
            moved_module,
            "_python_probe",
            return_value=probe,
        ):
            result = moved_module._environment_status(moved_root)

        serialized = json.dumps(result, ensure_ascii=False)
        selected = next(
            item
            for item in result["models"]["items"]
            if item["profile_id"] == result["active_profile"]
        )
        self.assertEqual("ready", result["state"])
        self.assertTrue(result["venv"]["portable"])
        self.assertEqual(str(moved_python), result["layout"]["worker_python"])
        self.assertEqual(str(moved_base), result["python"]["base_prefix"])
        self.assertTrue(Path(selected["path"]).is_relative_to(moved_root))
        self.assertNotIn(str(old_root), serialized)

    def test_settings_profile_device_and_explicit_environment_flow_to_cli(
        self,
    ) -> None:
        module = self._load_helper_module()
        python, managed_base = self._managed_environment_fixture(module)
        contract = module._load_project_module(self.project_root, "contract")
        public = contract.launcher_contract()
        profile = contract.PROFILE_REGISTRY[contract.DEFAULT_EMBEDDING_PROFILE]
        alternate_profile = next(
            value
            for value in contract.PROFILE_REGISTRY
            if value != contract.DEFAULT_EMBEDDING_PROFILE
        )
        settings_root = self.project_root / ".runtime" / "launcher"
        settings_root.mkdir(parents=True)
        (settings_root / "settings.json").write_text(
            json.dumps(
                {
                    public["settings"]["profile"]: alternate_profile,
                    public["settings"]["device"]: "cpu",
                }
            ),
            encoding="utf-8",
        )
        custom = self.project_root / ".runtime" / "models" / "custom-profile"
        custom.mkdir(parents=True)
        (custom / "config.json").write_text("{}", encoding="utf-8")
        (custom / "model.safetensors").write_bytes(b"weights")
        (custom / ".hia-embedding-model.json").write_text(
            json.dumps(
                {
                    "contract_version": contract.EMBEDDING_CONTRACT_VERSION,
                    "profile_id": profile.profile_id,
                    "model_id": profile.model_id,
                    "revision": "custom-revision",
                }
            ),
            encoding="utf-8",
        )
        probe = {
            "available": True,
            "executable": str(python),
            "version": "3.10.11",
            "bits": 64,
            "prefix": str(python.parents[1]),
            "base_prefix": str(managed_base),
            "pypdf_version": "6.14.2",
            "embedding_worker_version": "0.1.0",
            "torch_installed": True,
            "torch_version": "2.11.0",
            "torch_cuda_build": "12.8",
            "cuda_available": True,
            "gpu_name": "Test NVIDIA GPU",
        }
        relative_custom = custom.relative_to(self.project_root).as_posix()
        environment = {
            profile.model_dir_environment: relative_custom,
            contract.EMBEDDING_PROFILE_ENVIRONMENT: "",
            contract.EMBEDDING_DEVICE_ENVIRONMENT: "",
            "HTTPS_PROXY": "https://user:secret@example.invalid:8443",
            "PIP_INDEX_URL": "https://user:secret@index.invalid/simple",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(module, "_python_probe", return_value=probe),
        ):
            result = module._environment_status(self.project_root)
            child = module._safe_child_environment(
                self.project_root,
                result["layout"],
            )
        explicit_environment = {
            **environment,
            contract.EMBEDDING_PROFILE_ENVIRONMENT: profile.profile_id,
            contract.EMBEDDING_DEVICE_ENVIRONMENT: "cuda",
        }
        with (
            mock.patch.dict(os.environ, explicit_environment, clear=False),
            mock.patch.object(module, "_python_probe", return_value=probe),
        ):
            explicit = module._environment_status(self.project_root)

        selected = next(
            item
            for item in result["models"]["items"]
            if item["profile_id"] == profile.profile_id
        )
        self.assertEqual(str(custom), selected["path"])
        self.assertTrue(selected["configured_by_environment"])
        self.assertTrue(selected["installed"])
        self.assertEqual(alternate_profile, result["requested_profile"])
        self.assertEqual(profile.profile_id, result["active_profile"])
        self.assertTrue(result["profile_fallback"])
        self.assertEqual("settings", result["profile_source"])
        self.assertEqual("cpu", result["requested_device"])
        self.assertEqual("cpu", result["active_device"])
        self.assertEqual("settings", result["device_source"])
        self.assertEqual("cpu_embedding", result["embedding_mode"])
        self.assertEqual(
            "cpu",
            result["embedding_runtime_environment"][
                contract.EMBEDDING_DEVICE_ENVIRONMENT
            ],
        )
        self.assertEqual(profile.profile_id, explicit["requested_profile"])
        self.assertFalse(explicit["profile_fallback"])
        self.assertEqual("environment", explicit["profile_source"])
        self.assertEqual("cuda", explicit["requested_device"])
        self.assertEqual("cuda", explicit["active_device"])
        self.assertEqual("environment", explicit["device_source"])
        self.assertEqual("cuda", explicit["embedding_mode"])
        self.assertTrue(result["proxy"]["https_configured"])
        self.assertTrue(result["proxy"]["values_redacted"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn("HTTPS_PROXY", child)
        self.assertNotIn("PIP_INDEX_URL", child)
        self.assertGreaterEqual(result["storage"]["free_bytes"], 0)

    def test_import_list_delete_preserves_original(self) -> None:
        original = self.temporary_root / "original.md"
        original.write_text("# Managed source\n\nHoudini knowledge.", encoding="utf-8")

        import_exit, imported, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(original),
        )

        self.assertEqual(0, import_exit)
        import_result = imported["result"]
        self.assertEqual(1, import_result["imported"])
        source_id = import_result["items"][0]["source_id"]
        self.assertRegex(source_id, r"^original-[0-9a-f]{32}\.md$")
        managed = (
            self.project_root / ".runtime" / "knowledge" / "sources" / source_id
        )
        sidecar = Path(str(managed) + ".metadata.json")
        self.assertEqual(original.read_bytes(), managed.read_bytes())
        self.assertTrue(sidecar.is_file())

        list_exit, listed, _stderr = self._run_helper("list")
        self.assertEqual(0, list_exit)
        self.assertEqual(1, listed["result"]["total"])
        item = listed["result"]["items"][0]
        self.assertEqual(source_id, item["source_id"])
        self.assertEqual(str(original.resolve()), item["source"])
        self.assertEqual("md", item["format"])
        self.assertEqual("indexed", item["index_status"])

        delete_exit, deleted, _stderr = self._run_helper(
            "delete",
            "--source-id",
            source_id,
        )
        self.assertEqual(0, delete_exit)
        self.assertTrue(deleted["result"]["deleted"])
        self.assertFalse(deleted["result"]["original_deleted"])
        self.assertTrue(original.is_file())
        self.assertFalse(managed.exists())
        self.assertFalse(sidecar.exists())

    def test_media_import_is_sidecar_only_and_folder_contract_excludes_media(
        self,
    ) -> None:
        video = self.temporary_root / "large-video.mp4"
        with video.open("wb") as stream:
            stream.seek(6 * 1024 * 1024)
            stream.write(b"x")
        transcript = video.with_suffix(".vtt")
        transcript.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n"
            "LauncherMediaNeedle\n",
            encoding="utf-8",
        )

        exit_code, payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(video),
        )
        self.assertEqual(0, exit_code)
        result = payload["result"]
        self.assertIn(".mp4", result["supported_formats"])
        self.assertIn("vector", result)
        item = result["items"][0]
        self.assertEqual("user_transcript", item["source_kind"])
        self.assertFalse(item["media_copied"])
        self.assertTrue(item["transcript_managed"])
        managed = (
            self.project_root
            / ".runtime"
            / "knowledge"
            / "sources"
            / item["source_id"]
        )
        self.assertEqual(transcript.read_bytes(), managed.read_bytes())
        self.assertFalse(
            any(
                path.suffix.casefold() == ".mp4"
                for path in managed.parent.iterdir()
            )
        )

        status_exit, status, _stderr = self._run_helper("status")
        self.assertEqual(0, status_exit)
        contract = status["result"]["source_contract"]
        self.assertIn(".mp4", contract["supported_formats"])
        self.assertNotIn(".mp4", contract["folder_supported_formats"])
        self.assertEqual(
            "transcript_sidecar_only",
            contract["media_copy_policy"],
        )

        silent = self.temporary_root / "silent.mp4"
        silent.write_bytes(b"no-sidecar")
        missing_exit, missing, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(silent),
        )
        self.assertEqual(3, missing_exit)
        self.assertEqual("TRANSCRIPT_REQUIRED", missing["error"]["code"])

    def test_moved_project_thread_snapshot_import_and_remove(self) -> None:
        snapshot = (
            self.project_root
            / ".runtime"
            / "thread-snapshots"
            / "selected-thread.json"
        )
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            json.dumps(
                {
                    "schema": "hia-thread-snapshot/1",
                    "thread_id": "thread-moved",
                    "messages": [
                        {
                            "id": "answer-1",
                            "role": "assistant",
                            "phase": "final_answer",
                            "content": "MovedThreadNeedle",
                        },
                        {
                            "id": "tool-1",
                            "role": "tool",
                            "type": "tool_result",
                            "content": "ExcludedMovedToolNeedle",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        relative = snapshot.relative_to(self.project_root).as_posix()
        import_exit, imported, _stderr = self._run_helper(
            "thread-import",
            "--thread-id",
            "thread-moved",
            "--snapshot-file",
            relative,
        )
        self.assertEqual(0, import_exit)
        self.assertEqual("replaced", imported["result"]["status"])
        self.assertEqual(1, imported["result"]["records_indexed"])
        self.assertEqual(1, imported["result"]["messages_excluded"])

        remove_exit, removed, _stderr = self._run_helper(
            "thread-remove",
            "--thread-id",
            "thread-moved",
        )
        self.assertEqual(0, remove_exit)
        self.assertEqual("removed", removed["result"]["status"])
        self.assertEqual(
            1,
            removed["result"]["refresh"]["documents_removed"],
        )

    def test_source_storage_is_delegated_to_the_stable_core_cli(self) -> None:
        first = self.temporary_root / "first.txt"
        second = self.temporary_root / "renamed.txt"
        first.write_text("same content", encoding="utf-8")
        second.write_text("same content", encoding="utf-8")

        first_exit, first_payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(first),
        )
        second_exit, second_payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(second),
        )

        self.assertEqual(0, first_exit)
        self.assertEqual(0, second_exit)
        first_item = first_payload["result"]["items"][0]
        second_item = second_payload["result"]["items"][0]
        self.assertNotEqual(first_item["source_id"], second_item["source_id"])
        self.assertTrue(first_item["copied"])
        self.assertTrue(second_item["copied"])
        for item in (first_item, second_item):
            managed = (
                self.project_root
                / ".runtime"
                / "knowledge"
                / "sources"
                / item["source_id"]
            )
            sidecar = Path(str(managed) + ".metadata.json")
            self.assertEqual(b"same content", managed.read_bytes())
            self.assertEqual(
                "hia-managed-source/1",
                json.loads(sidecar.read_text(encoding="utf-8"))["schema"],
            )
        helper_source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"knowledge_index_cli.py"', helper_source)
        self.assertIn('("sources", source_command)', helper_source)
        self.assertIn("def _core_knowledge_action", helper_source)
        self.assertNotIn("os.link(", helper_source)
        self.assertNotIn("source.unlink(", helper_source)
        self.assertNotIn("SIDECAR_SCHEMA", helper_source)
        self.assertNotIn("def _copy_to_staging", helper_source)

    def test_formats_and_size_are_consumed_from_knowledge_index(self) -> None:
        helper_source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn("module.USER_TEXT_SUFFIXES", helper_source)
        self.assertIn("module.USER_OPTIONAL_SUFFIXES", helper_source)
        self.assertIn("module.MEDIA_SUFFIXES", helper_source)
        self.assertIn("module.MAX_DOCUMENT_BYTES", helper_source)
        self.assertIn("module.LocalKnowledgeIndex", helper_source)
        self.assertIn("def _core_source_action", helper_source)
        self.assertNotIn("def _refresh_user", helper_source)

        unsupported = self.temporary_root / "unsupported.docx"
        unsupported.write_bytes(b"not supported")
        unsupported_exit, unsupported_payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(unsupported),
        )
        self.assertEqual(2, unsupported_exit)
        self.assertEqual(
            "UNSUPPORTED_SOURCE_TYPE",
            unsupported_payload["error"]["code"],
        )

        too_large = self.temporary_root / "too-large.md"
        too_large.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        large_exit, large_payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(too_large),
        )
        self.assertEqual(2, large_exit)
        self.assertEqual("SOURCE_TOO_LARGE", large_payload["error"]["code"])

    def test_folder_import_accepts_only_real_backend_formats(self) -> None:
        folder = self.temporary_root / "folder"
        folder.mkdir()
        for suffix in (".md", ".txt", ".html", ".htm", ".srt", ".vtt"):
            (folder / f"source{suffix}").write_text(
                "Houdini local knowledge",
                encoding="utf-8",
            )
        (folder / "source.pdf").write_bytes(b"%PDF-1.4\ninvalid test pdf")
        (folder / "ignored.docx").write_bytes(b"ignored")
        (folder / "ignored.mp4").write_bytes(b"not recursively imported")

        exit_code, payload, _stderr = self._run_helper(
            "import-folder",
            "--path",
            str(folder),
        )

        self.assertEqual(0, exit_code)
        result = payload["result"]
        self.assertEqual(7, result["imported"])
        self.assertEqual(2, result["unsupported_files"])
        self.assertEqual(
            [".htm", ".html", ".md", ".pdf", ".srt", ".txt", ".vtt"],
            result["supported_formats"],
        )

    def test_user_site_fake_pypdf_does_not_participate(self) -> None:
        fake_site = self.temporary_root / "fake-user-site"
        package = fake_site / "pypdf"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            "raise RuntimeError('global user site was imported')\n",
            encoding="utf-8",
        )
        pdf = self.temporary_root / "source.pdf"
        pdf.write_bytes(b"%PDF-1.4\ninvalid")

        exit_code, payload, stderr = self._run_helper(
            "import-file",
            "--path",
            str(pdf),
            environment={
                "PYTHONPATH": str(fake_site),
                "PYTHONNOUSERSITE": "0",
            },
        )

        self.assertEqual(0, exit_code, stderr)
        warnings = payload["result"]["warnings"]
        self.assertTrue(
            any("pypdf support is not installed" in warning for warning in warnings),
            warnings,
        )
        self.assertNotIn("global user site was imported", stderr)

    def test_delete_rejects_escape_and_preserves_both_files(self) -> None:
        original = self.temporary_root / "original.md"
        sentinel = self.temporary_root / "sentinel.md"
        original.write_text("managed", encoding="utf-8")
        sentinel.write_text("keep", encoding="utf-8")
        exit_code, payload, _stderr = self._run_helper(
            "import-file",
            "--path",
            str(original),
        )
        self.assertEqual(0, exit_code)
        source_id = payload["result"]["items"][0]["source_id"]

        escape_exit, escape_payload, _stderr = self._run_helper(
            "delete",
            "--source-id",
            "../sentinel.md",
        )
        self.assertEqual(2, escape_exit)
        self.assertEqual(
            "UNSAFE_MANAGED_SOURCE_PATH",
            escape_payload["error"]["code"],
        )
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertTrue(original.is_file())
        self.assertTrue(
            (
                self.project_root
                / ".runtime"
                / "knowledge"
                / "sources"
                / source_id
            ).is_file()
        )

    def test_delete_rejects_reparse_source(self) -> None:
        sentinel = self.temporary_root / "sentinel.md"
        sentinel.write_text("keep", encoding="utf-8")
        sources = self.project_root / ".runtime" / "knowledge" / "sources"
        sources.mkdir(parents=True)
        link = sources / "linked.md"
        try:
            link.symlink_to(sentinel)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"file symlinks are unavailable: {exc}")
        reparse_exit, reparse_payload, _stderr = self._run_helper(
            "delete",
            "--source-id",
            "linked.md",
        )
        self.assertEqual(2, reparse_exit)
        self.assertEqual(
            "UNSAFE_MANAGED_SOURCE_PATH",
            reparse_payload["error"]["code"],
        )
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_wrapper_reports_missing_environment_without_global_python(self) -> None:
        wrapper_root = self.temporary_root / "wrapper moved root"
        wrapper_root.mkdir()
        self._copy_project_runtime(wrapper_root, wrapper=True)
        wrapper = wrapper_root / "scripts" / "hia-knowledge.ps1"

        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "environment-status",
            ],
            cwd=self.temporary_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual("missing", payload["result"]["state"])
        self.assertFalse(payload["result"]["python"]["available"])
        self.assertEqual(
            "qwen3-embedding-0.6b",
            payload["result"]["requested_profile"],
        )
        self.assertEqual(
            "installer_contract",
            payload["result"]["profile_source"],
        )
        self.assertEqual(
            str(wrapper_root / ".venv"),
            payload["result"]["venv"]["path"],
        )
        self.assertEqual(
            str(wrapper_root / ".venv" / "Scripts" / "python.exe"),
            payload["result"]["python"]["expected_path"],
        )
        self.assertEqual(
            "environment-install",
            payload["result"]["repair_actions"]["parser_only"],
        )
        self.assertNotIn(".runtime\\python\\python.exe", WRAPPER_PATH.read_text(
            encoding="utf-8"
        ))

    def test_wrapper_read_only_actions_do_not_create_runtime_scratch(self) -> None:
        wrapper_root = self.temporary_root / "read only wrapper root"
        wrapper_root.mkdir()
        self._copy_project_runtime(wrapper_root, wrapper=True)
        wrapper = wrapper_root / "scripts" / "hia-knowledge.ps1"
        runtime_root = wrapper_root / ".runtime"
        command_prefix = [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ]

        for action in ("status", "environment-status"):
            completed = subprocess.run(
                [*command_prefix, action],
                cwd=self.temporary_root,
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
            payload = json.loads(completed.stdout.strip())
            environment = (
                payload["result"]["environment"]
                if action == "status"
                else payload["result"]
            )
            self.assertEqual("missing", environment["state"])
            self.assertFalse(runtime_root.exists())

        read_only_list = subprocess.run(
            [*command_prefix, "list"],
            cwd=self.temporary_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertNotEqual(0, read_only_list.returncode)
        self.assertFalse(runtime_root.exists())

        mutating = subprocess.run(
            [*command_prefix, "rescan"],
            cwd=self.temporary_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertNotEqual(0, mutating.returncode)
        scratch = runtime_root / "cache" / "knowledge-cli"
        expected = (scratch, scratch / "home", scratch / "tmp")
        for directory in expected:
            self.assertTrue(directory.is_dir(), str(directory))
            self.assertTrue(directory.resolve().is_relative_to(wrapper_root))

        source = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertIn("-ReadOnly:(", source)
        self.assertIn(
            "$Action -in @('status', 'environment-status', 'list')",
            source,
        )
        self.assertIn("$assetCapabilitiesAction", source)

    def test_wrapper_blocks_nonportable_environment_before_actions(self) -> None:
        canonical = (
            self.project_root
            / ".venv"
            / "Scripts"
            / "python.exe"
        )
        ready = {
            "schema": "hia-knowledge-launcher-cli/1",
            "ok": True,
            "result": {
                "state": "ready",
                "venv": {"portable": True},
                "python": {
                    "path": str(canonical),
                    "portable": True,
                    "base_prefix_is_project_local": True,
                    "managed_marker": {"valid": True},
                },
                "embedding_runtime_environment": {},
            },
        }
        legacy = json.loads(json.dumps(ready))
        legacy["result"]["state"] = "repair_required"
        legacy["result"]["venv"]["portable"] = False
        legacy["result"]["python"]["portable"] = False
        legacy["result"]["python"]["base_prefix_is_project_local"] = False
        legacy["result"]["python"]["managed_marker"]["valid"] = False
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WRAPPER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'wrapper did not parse' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-HiaKnowledgeManagedEnvironment'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'managed environment gate is missing' }}
Invoke-Expression $definition[0].Extent.Text
$ready = {_ps_literal(json.dumps(ready))} | ConvertFrom-Json
$legacy = {_ps_literal(json.dumps(legacy))} | ConvertFrom-Json
$readyAccepted = $false
try {{
    [void](Assert-HiaKnowledgeManagedEnvironment `
        -Payload $ready `
        -CanonicalPython {_ps_literal(canonical)})
    $readyAccepted = $true
}} catch {{ }}
$legacyFailure = ''
try {{
    [void](Assert-HiaKnowledgeManagedEnvironment `
        -Payload $legacy `
        -CanonicalPython {_ps_literal(canonical)})
}} catch {{
    $legacyFailure = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    ready_accepted = $readyAccepted
    legacy_failure = $legacyFailure
}} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertTrue(payload["ready_accepted"])
        self.assertIn("ready, managed, portable", payload["legacy_failure"])
        source = WRAPPER_PATH.read_text(encoding="utf-8")
        gate = source.index("$verifiedEnvironment = Assert-HiaKnowledgeManagedEnvironment")
        runtime_environment = source.index(
            "$runtimeEnvironment = $verifiedEnvironment.embedding_runtime_environment"
        )
        self.assertLess(gate, source.index("$helperResult = Invoke-HiaKnowledgeProcess"))
        self.assertLess(gate, source.index("$indexResult = Invoke-HiaKnowledgeProcess"))
        self.assertLess(gate, runtime_environment)
        self.assertLess(
            runtime_environment,
            source.index("$helperResult = Invoke-HiaKnowledgeProcess"),
        )

    def test_wrapper_index_build_rescans_user_then_delegates_jsonl(self) -> None:
        source = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertIn("'index-status'", source)
        self.assertIn("'index-build'", source)
        self.assertIn("'rescan'", source)
        self.assertIn("'thread-import'", source)
        self.assertIn("'thread-remove'", source)
        self.assertIn("'--thread-id'", source)
        self.assertIn("'--snapshot-file'", source)
        self.assertIn("knowledge_index_cli.py", source)
        self.assertIn("'--format', 'json'", source)
        self.assertIn("'build', '--batch-size'", source)
        self.assertLess(
            source.index("$rescanResult = Invoke-HiaKnowledgeProcess"),
            source.index("$indexResult = Invoke-HiaKnowledgeProcess"),
        )
        self.assertIn("'PYTHONNOUSERSITE' = '1'", source)
        self.assertIn("'-I'", source)

    def test_clean_clone_explicit_profile_selects_full_embedding_install(self) -> None:
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(WRAPPER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'wrapper did not parse' }}
$definition = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-HiaKnowledgeEnvironmentInstallPlan'
}}, $true))
if ($definition.Count -ne 1) {{ throw 'install plan function is missing' }}
Invoke-Expression $definition[0].Extent.Text
$clean = [pscustomobject]@{{
    active_profile = ''
    requested_device = 'auto'
    torch = [pscustomobject]@{{ cuda_available = $false }}
    models = [pscustomobject]@{{
        items = @(
            [pscustomobject]@{{
                profile_id = 'qwen3-embedding-0.6b'
                installed = $false
                revision = ''
            }},
            [pscustomobject]@{{
                profile_id = 'qwen3-embedding-8b'
                installed = $false
                revision = ''
            }}
        )
    }}
}}
$explicit = Get-HiaKnowledgeEnvironmentInstallPlan `
    -Environment $clean `
    -ActionName 'environment-install' `
    -RequestedProfile 'qwen3-embedding-0.6b' `
    -RequestedDevice 'cuda'
$explicitEight = Get-HiaKnowledgeEnvironmentInstallPlan `
    -Environment $clean `
    -ActionName 'environment-install' `
    -RequestedProfile 'qwen3-embedding-8b'
$base = Get-HiaKnowledgeEnvironmentInstallPlan `
    -Environment $clean `
    -ActionName 'environment-install'
$invalidFailure = ''
try {{
    [void](Get-HiaKnowledgeEnvironmentInstallPlan `
        -Environment $clean `
        -ActionName 'environment-install' `
        -RequestedProfile 'not-a-real-profile')
}} catch {{
    $invalidFailure = [string]$_.Exception.Message
}}
[pscustomobject]@{{
    explicit = $explicit
    explicit_eight = $explicitEight
    base = $base
    invalid_failure = $invalidFailure
}} | ConvertTo-Json -Depth 6 -Compress
"""
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertFalse(payload["explicit"]["parser_only"])
        self.assertEqual(
            "qwen3-embedding-0.6b",
            payload["explicit"]["profile"],
        )
        self.assertEqual("main", payload["explicit"]["revision"])
        self.assertEqual("cuda", payload["explicit"]["device"])
        self.assertFalse(payload["explicit_eight"]["parser_only"])
        self.assertEqual(
            "qwen3-embedding-8b",
            payload["explicit_eight"]["profile"],
        )
        self.assertEqual("main", payload["explicit_eight"]["revision"])
        self.assertTrue(payload["base"]["parser_only"])
        self.assertEqual("", payload["base"]["profile"])
        self.assertIn(
            "Unsupported embedding profile",
            payload["invalid_failure"],
        )

    def test_parser_install_plan_is_project_managed_and_model_free(self) -> None:
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        parser_branch = source.index("if ($KnowledgeParserOnly)")
        full_branch = source.index(
            "$managedEnvironment = Invoke-HiaKnowledgeParserEnvironment",
            parser_branch,
        )
        parser_only = source[parser_branch:full_branch]
        torch_branch = source.index("$priorTorchProbe = Get-HiaEmbeddingTorchProbe")
        download_branch = source.index("$downloadResult = Invoke-HiaEmbeddingChildProcess")
        self.assertLess(parser_branch, torch_branch)
        self.assertLess(parser_branch, download_branch)
        self.assertIn("$parserResult = Invoke-HiaKnowledgeParserEnvironment", parser_only)
        self.assertIn("Write-Output ($parserResult | ConvertTo-Json -Compress)", parser_only)
        self.assertIn("\n    return", parser_only)
        for excluded in (
            "-DeferPublish",
            "$priorTorchProbe",
            "$workerInstallArguments",
            "$downloadResult",
            "$smokeResult",
        ):
            self.assertNotIn(excluded, parser_only)
        self.assertIn("$script:HiaKnowledgeParserRequirement = 'pypdf==6.14.2'", source)
        self.assertIn("$script:HiaKnowledgePythonVersion = '3.10.11'", source)
        self.assertIn("'python',\n            'install'", source)
        self.assertIn("'--install-dir', $pythonInstallRoot", source)
        self.assertIn("'--no-bin'", source)
        self.assertIn("'--no-registry'", source)
        self.assertIn("'--managed-python'", source)
        self.assertIn("'--no-python-downloads'", source)
        self.assertIn("'--relocatable'", source)
        self.assertIn("'--python', $workerPython", source)
        self.assertIn("'UV_PYTHON_INSTALL_DIR' = $PythonInstallRoot", source)
        self.assertIn("'UV_NO_MODIFY_PATH' = '1'", source)
        self.assertIn("'HOME' = $homeRoot", source)
        self.assertIn("$managedEnvironment = Invoke-HiaKnowledgeParserEnvironment", source)
        self.assertIn("-FilePath $managedPython", source)
        self.assertNotIn("BootstrapPython is required", source)
        self.assertNotIn("'-m', 'venv'", source)
        full_install = source[full_branch:]
        self.assertIn("-DeferPublish", full_install[: full_install.index("$transaction =")])
        prior_torch = source.index("$priorTorchProbe = Get-HiaEmbeddingTorchProbe")
        worker_install = source.index("$workerInstallArguments = @(", prior_torch)
        verified_torch = source.index("$verifiedTorch = Get-HiaEmbeddingTorchProbe")
        staging_runtime_validation = source.index(
            "The staged Bridge, MCP, parser, and embedding worker imports "
            "did not pass validation.",
            verified_torch,
        )
        model_download = source.index(
            "$downloadResult = Invoke-HiaEmbeddingChildProcess",
            staging_runtime_validation,
        )
        model_smoke = source.index(
            "$smokeResult = Invoke-HiaEmbeddingChildProcess",
            model_download,
        )
        publish = source.index(
            "$publish = Publish-HiaKnowledgeManagedVenv",
            model_smoke,
        )
        self.assertLess(full_branch, prior_torch)
        self.assertLess(prior_torch, worker_install)
        self.assertLess(worker_install, verified_torch)
        self.assertLess(verified_torch, staging_runtime_validation)
        self.assertLess(staging_runtime_validation, model_download)
        self.assertLess(model_download, model_smoke)
        self.assertLess(model_smoke, publish)
        self.assertNotIn("ForceTransactionalPublish", source)
        self.assertNotIn("AllowLegacyBackup", source)
        wrapper_source = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertIn("[string]$LogPath = ''", wrapper_source)
        self.assertIn(
            "$installArguments += @('-LogPath', $LogPath)",
            wrapper_source,
        )
        self.assertIn("installed_torch = $false", source)
        self.assertIn("installed_model = $false", source)

        managed_venv = source[
            source.index("function New-HiaKnowledgeManagedVenv"):
            source.index("function Publish-HiaKnowledgeManagedVenv")
        ]
        self.assertIn(
            "-VenvRoot ([string]$paths.staging)",
            managed_venv,
        )
        self.assertNotIn("[System.IO.Directory]::Move(", managed_venv)
        self.assertNotIn("Remove-Item", managed_venv)

        publish_venv = source[
            source.index("function Publish-HiaKnowledgeManagedVenv"):
            source.index("function Move-HiaKnowledgeStagingToFailed")
        ]
        root_backup = publish_venv.index(
            "[string]$paths.canonical,\n            [string]$paths.backup"
        )
        staged_publish = publish_venv.index(
            "[string]$paths.staging,\n            [string]$paths.canonical"
        )
        restore = publish_venv.index(
            "[System.IO.Directory]::Move(\n"
            "                [string]$paths.backup,\n"
            "                [string]$paths.canonical",
            staged_publish,
        )
        self.assertLess(root_backup, staged_publish)
        self.assertLess(staged_publish, restore)

        parser_environment = source[
            source.index("function Invoke-HiaKnowledgeParserEnvironment"):
            source.index("\ntry {\n$directorySeparators")
        ]
        unmarked_gate = parser_environment[
            parser_environment.index(
                "if (Test-Path -LiteralPath $venvRoot"
            ):
            parser_environment.index(
                "$removeEnvironment = @(Get-HiaKnowledgeRemoveEnvironment)"
            )
        ]
        self.assertIn(
            "unmarked or unsafe; refusing to repair or replace it",
            unmarked_gate,
        )
        self.assertNotIn("$RepairRequested", unmarked_gate)

    def test_managed_venv_publish_failure_restores_legacy_and_retry_succeeds(
        self,
    ) -> None:
        case_root = self.temporary_root / "venv rollback project"
        canonical_venv = case_root / ".venv"
        (canonical_venv / "Scripts").mkdir(parents=True)
        (canonical_venv / "Scripts" / "python.exe").write_text(
            "old root python",
            encoding="utf-8",
        )
        canonical_sentinel = canonical_venv / "old-root-environment.txt"
        canonical_sentinel.write_text("preserve root", encoding="utf-8")
        (canonical_venv / ".hia-managed-venv.json").write_text(
            json.dumps(
                {
                    "schema": "hia-managed-python-venv/1",
                    "role": "hia-embedding",
                    "install_id": "0" * 32,
                    "python_version": "3.10.11",
                }
            ),
            encoding="utf-8",
        )
        toolchain_root = (
            case_root
            / ".runtime"
            / "toolchains"
            / "hia-embedding"
        )
        legacy_venv = toolchain_root / "venv"
        legacy_venv.mkdir(parents=True)
        legacy_sentinel = legacy_venv / "legacy-cuda-worker.txt"
        legacy_sentinel.write_text("preserve", encoding="utf-8")
        managed_python = (
            case_root
            / ".runtime"
            / "toolchains"
            / "python"
            / "cpython-3.10.11-test"
            / "python.exe"
        )
        managed_python.parent.mkdir(parents=True)
        managed_python.write_bytes(b"managed python")
        python_install_root = managed_python.parents[1]
        uv_exe = (
            case_root
            / ".runtime"
            / "toolchains"
            / "uv"
            / "uv.exe"
        )
        uv_exe.parent.mkdir(parents=True)
        uv_exe.write_bytes(b"uv")
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(INSTALLER_PATH)},
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'installer did not parse' }}
$wanted = @(
    'Get-HiaKnowledgeTransactionPaths',
    'New-HiaKnowledgeManagedVenv',
    'Publish-HiaKnowledgeManagedVenv',
    'Undo-HiaKnowledgeManagedVenvPublication'
)
foreach ($name in $wanted) {{
    $definition = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }}, $true))
    if ($definition.Count -ne 1) {{ throw "managed venv function is missing: $name" }}
    Invoke-Expression $definition[0].Extent.Text
}}

$script:HiaManagedVenvMarkerName = '.hia-managed-venv.json'
$script:HiaManagedVenvMarkerSchema = 'hia-managed-python-venv/1'
$script:HiaKnowledgePythonVersion = '3.10.11'
function Assert-HiaKnowledgeProjectDirectory {{
    param(
        [string]$ProjectRoot,
        [string]$Path,
        [switch]$Create
    )
    if ($Create -and -not (Test-Path -LiteralPath $Path)) {{
        [void][System.IO.Directory]::CreateDirectory($Path)
    }}
    return [System.IO.Path]::GetFullPath($Path)
}}
function Assert-HiaKnowledgeManagedVenvTree {{
    param([string]$ProjectRoot, [string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {{
        throw 'managed venv tree is unavailable'
    }}
    return [System.IO.Path]::GetFullPath($Path)
}}
function Write-HiaEmbeddingInstallLog {{
    param([string]$Level, [string]$Message)
}}
function Invoke-HiaEmbeddingChildProcess {{
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment,
        [string]$WorkingDirectory
    )
    $target = [string]$Arguments[$Arguments.Count - 1]
    [void][System.IO.Directory]::CreateDirectory(
        (Join-Path $target 'Scripts')
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $target 'Scripts\\python.exe'),
        'staged python'
    )
    return [pscustomobject]@{{ exit_code = 0 }}
}}
function Assert-HiaEmbeddingProcessSucceeded {{
    param($Result, [string]$Operation)
}}
function Get-HiaKnowledgeManagedVenvMarker {{
    param([string]$ProjectRoot, [string]$VenvRoot)
    $marker = Join-Path $VenvRoot '.hia-managed-venv.json'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {{
        return $null
    }}
    $payload = [System.IO.File]::ReadAllText($marker) | ConvertFrom-Json
    if (
        [string]$payload.schema -ne $script:HiaManagedVenvMarkerSchema -or
        [string]$payload.role -ne 'hia-embedding'
    ) {{
        return $null
    }}
    return $payload
}}
function Test-HiaKnowledgeManagedVenv {{
    param(
        [string]$ProjectRoot,
        [string]$VenvRoot,
        [string]$PythonInstallRoot,
        [hashtable]$Environment,
        [string[]]$RemoveEnvironment
    )
    return $null -ne (
        Get-HiaKnowledgeManagedVenvMarker `
            -ProjectRoot $ProjectRoot `
            -VenvRoot $VenvRoot
    )
}}

$newTransaction = {{
    New-HiaKnowledgeManagedVenv `
        -ProjectRoot {_ps_literal(case_root)} `
        -VenvRoot {_ps_literal(canonical_venv)} `
        -TransactionRoot {_ps_literal(toolchain_root)} `
        -ManagedPython {_ps_literal(managed_python)} `
        -PythonInstallRoot {_ps_literal(python_install_root)} `
        -UvExe {_ps_literal(uv_exe)} `
        -Environment @{{}} `
        -RemoveEnvironment @('PIP_INDEX_URL')
}}
$firstTransaction = & $newTransaction
$firstPublish = Publish-HiaKnowledgeManagedVenv `
    -ProjectRoot {_ps_literal(case_root)} `
    -Transaction $firstTransaction
$firstFailure = ''
$rollback = $null
try {{
    throw 'injected published probe failure'
}} catch {{
    $firstFailure = [string]$_.Exception.Message
    $rollback = Undo-HiaKnowledgeManagedVenvPublication `
        -ProjectRoot {_ps_literal(case_root)} `
        -VenvRoot {_ps_literal(canonical_venv)} `
        -BackupRoot ([string]$firstPublish.backup_root) `
        -InstallId ([string]$firstTransaction.install_id)
}}
$rootRestored = (
    (Test-Path -LiteralPath {_ps_literal(canonical_sentinel)} -PathType Leaf) -and
    ([System.IO.File]::ReadAllText(
        {_ps_literal(canonical_sentinel)}
    ) -eq 'preserve root')
)
$legacyPreserved = (
    (Test-Path -LiteralPath {_ps_literal(legacy_sentinel)} -PathType Leaf) -and
    ([System.IO.File]::ReadAllText({_ps_literal(legacy_sentinel)}) -eq 'preserve')
)
$failedWorker = Join-Path (
    [string]$rollback.isolated_failed_venv
) 'Scripts\python.exe'
$firstBackupConsumed = -not (Test-Path -LiteralPath (
    [string]$firstTransaction.backup_root
))
$retryTransaction = & $newTransaction
$retryPublish = Publish-HiaKnowledgeManagedVenv `
    -ProjectRoot {_ps_literal(case_root)} `
    -Transaction $retryTransaction
[pscustomobject]@{{
    first_failure = $firstFailure
    root_restored = $rootRestored
    legacy_preserved = $legacyPreserved
    failed_isolated = (
        (Test-Path -LiteralPath $failedWorker -PathType Leaf) -and
        ([System.IO.File]::ReadAllText($failedWorker) -eq 'staged python')
    )
    first_backup_consumed = $firstBackupConsumed
    retry_succeeded = (
        (Test-Path -LiteralPath (
            Join-Path {_ps_literal(canonical_venv)} 'Scripts\python.exe'
        ) -PathType Leaf) -and
        -not [string]::IsNullOrWhiteSpace([string]$retryPublish.backup_root)
    )
    legacy_preserved_after_retry = (
        (Test-Path -LiteralPath {_ps_literal(legacy_sentinel)} -PathType Leaf) -and
        ([System.IO.File]::ReadAllText(
            {_ps_literal(legacy_sentinel)}
        ) -eq 'preserve')
    )
    staging_count = @(
        Get-ChildItem `
            -LiteralPath {_ps_literal(case_root / '.runtime' / 'v')} `
            -Directory `
            -Recurse `
            -ErrorAction SilentlyContinue |
            Where-Object {{ $_.Name -eq 's' }}
    ).Count
}} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertIn("injected published probe failure", payload["first_failure"])
        self.assertTrue(payload["root_restored"])
        self.assertTrue(payload["legacy_preserved"])
        self.assertTrue(payload["failed_isolated"])
        self.assertTrue(payload["first_backup_consumed"])
        self.assertTrue(payload["retry_succeeded"])
        self.assertTrue(payload["legacy_preserved_after_retry"])
        self.assertEqual(0, payload["staging_count"])

    def test_python_probe_covers_cpu_cuda_and_gpu_without_claiming_success(self) -> None:
        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"torch_installed": False', source)
        self.assertIn("torch.cuda.is_available()", source)
        self.assertIn("torch.cuda.get_device_name(0)", source)
        self.assertIn('"cuda_available": cuda_available', source)
        self.assertIn('"gpu_name": str(probe.get("gpu_name") or "")', source)
        self.assertIn('"embedding_mode": fallback', source)
        self.assertIn('"houdini_launch_blocked": False', source)

    def test_powershell_files_parse(self) -> None:
        script = f"""
$errors = $null
$tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    '{str(WRAPPER_PATH).replace("'", "''")}',
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'hia-knowledge.ps1 did not parse' }}
$errors = $null
$tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    '{str(INSTALLER_PATH).replace("'", "''")}',
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Install-HiaEmbedding.ps1 did not parse' }}
"""
        completed = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
