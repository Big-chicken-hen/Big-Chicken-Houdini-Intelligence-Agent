from __future__ import annotations

import importlib.util
import io
import json
import hashlib
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
REPAIR_PATH = REPOSITORY_ROOT / "scripts" / "repair_hia_assets.py"
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
TEST_RUN_ROOT = REPOSITORY_ROOT / ".runtime" / "test-runs"
TEST_RUN_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

SPEC = importlib.util.spec_from_file_location("repair_hia_assets", REPAIR_PATH)
assert SPEC is not None and SPEC.loader is not None
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)

from hia_mcp_runtime.knowledge_assets import KnowledgeAssetManager  # noqa: E402


class AssetRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_RUN_ROOT)
        self.root = Path(self._temporary.name)
        for path in (
            self.root / "scripts" / "hia-knowledge.ps1",
            self.root
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "local_extractors.py",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# marker\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_single_profile_contract_is_large_v3_turbo(self) -> None:
        capabilities = KnowledgeAssetManager.capabilities(
            project_root=self.root
        )

        self.assertTrue(capabilities["model_installation"])
        self.assertTrue(
            capabilities["external_extraction"]["model_installation"]
        )
        repair_contract = capabilities["repair"]
        self.assertEqual("large-v3-turbo", repair_contract["asr_profile"])
        self.assertEqual(
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            repair_contract["asr_repository"],
        )
        self.assertGreaterEqual(
            repair_contract["estimated_download_bytes"],
            1_500_000_000,
        )
        self.assertLessEqual(
            repair_contract["estimated_download_bytes"],
            2_000_000_000,
        )
        self.assertEqual("int8", repair_contract["cpu_compute_type"])
        for extractor in capabilities["extractors"]:
            self.assertEqual(
                r".\scripts\hia-knowledge.ps1 assets repair",
                extractor["action"],
            )
            self.assertIn("target_relative_path", extractor)
            self.assertIsInstance(extractor["estimated_bytes"], int)
            self.assertIsInstance(extractor["installed"], bool)
            self.assertIsInstance(extractor["ready"], bool)
        media = next(
            row
            for row in capabilities["extractors"]
            if row["name"] == "media_asr"
        )
        self.assertEqual(
            ".runtime/models/asr/faster-whisper",
            media["target_relative_path"],
        )
        self.assertGreaterEqual(media["estimated_bytes"], 1_500_000_000)

    def test_package_install_uses_only_the_one_ocr_and_asr_stack(self) -> None:
        emitter = repair.JsonlEmitter(io.StringIO())
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        uv = (
            self.root
            / ".runtime"
            / "toolchains"
            / "hia-embedding"
            / "uv"
            / "0.11.29"
            / "uv.exe"
        )
        with (
            mock.patch.object(repair, "_uv_executable", return_value=uv),
            mock.patch.object(
                repair,
                "_run",
                return_value=completed,
            ) as run,
        ):
            repair.install_packages(self.root, {}, emitter)

        arguments = run.call_args.args[0]
        self.assertEqual(str(uv), arguments[0])
        self.assertIn("--python", arguments)
        self.assertEqual(
            sys.executable,
            arguments[arguments.index("--python") + 1],
        )
        self.assertNotIn("pip", arguments[:1])
        self.assertEqual(
            (
                "rapidocr",
                "onnxruntime",
                "pypdfium2",
                "faster-whisper",
            ),
            tuple(arguments[-4:]),
        )
        self.assertNotIn("whisper", arguments[-4:])
        self.assertNotIn("torch", arguments[-4:])

    def test_repair_process_paths_use_the_short_project_runtime_root(self) -> None:
        hostile = {
            "PYTHONHOME": r"Z:\hostile-python",
            "VIRTUAL_ENV": r"Z:\hostile-venv",
            "PIP_CONFIG_FILE": r"Z:\hostile-pip.ini",
            "PIP_INDEX_URL": "https://invalid.example.invalid/simple",
            "UV_CONFIG_FILE": r"Z:\hostile-uv.toml",
            "UV_INDEX_URL": "https://invalid.example.invalid/simple",
            "UV_OFFLINE": "1",
            "HF_TOKEN": "must-not-reach-child",
            "HF_HUB_OFFLINE": "1",
        }
        with mock.patch.dict(repair.os.environ, hostile, clear=False):
            environment = repair._repair_environment(self.root)
            repair._activate_repair_environment(environment)
            self.assertNotIn("HF_HUB_OFFLINE", repair.os.environ)
            self.assertNotIn("UV_CONFIG_FILE", repair.os.environ)
        short_root = self.root / ".runtime" / "a"

        self.assertEqual(str(short_root / "t"), environment["TEMP"])
        self.assertEqual(environment["TEMP"], environment["TMP"])
        self.assertEqual(environment["TEMP"], environment["TMPDIR"])
        self.assertEqual(str(short_root / "u"), environment["UV_CACHE_DIR"])
        self.assertEqual(str(short_root / "h"), environment["HF_HOME"])
        self.assertEqual(
            str(short_root / "h" / "hub"),
            environment["HF_HUB_CACHE"],
        )
        self.assertEqual(
            str(repair.HF_HUB_ETAG_TIMEOUT_SECONDS),
            environment["HF_HUB_ETAG_TIMEOUT"],
        )
        self.assertEqual(
            str(repair.HF_HUB_DOWNLOAD_TIMEOUT_SECONDS),
            environment["HF_HUB_DOWNLOAD_TIMEOUT"],
        )
        self.assertEqual("1", environment["HF_HUB_DISABLE_XET"])
        self.assertEqual(
            str(repair.UV_HTTP_CONNECT_TIMEOUT_SECONDS),
            environment["UV_HTTP_CONNECT_TIMEOUT"],
        )
        self.assertEqual(
            str(repair.UV_HTTP_TIMEOUT_SECONDS),
            environment["UV_HTTP_TIMEOUT"],
        )
        self.assertEqual(
            str(repair.UV_HTTP_RETRIES),
            environment["UV_HTTP_RETRIES"],
        )
        self.assertEqual("1", environment["UV_CONCURRENT_DOWNLOADS"])
        self.assertEqual("1", environment["UV_CONCURRENT_BUILDS"])
        for name in (
            "TEMP",
            "TMP",
            "TMPDIR",
            "UV_CACHE_DIR",
            "HF_HOME",
            "PIP_CACHE_DIR",
        ):
            self.assertTrue(Path(environment[name]).is_relative_to(self.root))
        for name in (
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "PIP_CONFIG_FILE",
            "PIP_INDEX_URL",
            "UV_CONFIG_FILE",
            "UV_INDEX_URL",
            "UV_OFFLINE",
            "HF_TOKEN",
            "HF_HUB_OFFLINE",
        ):
            self.assertNotIn(name, environment)

    def test_package_install_retries_once_with_the_same_uv_cache(self) -> None:
        output = io.StringIO()
        emitter = repair.JsonlEmitter(output)
        uv = self.root / ".runtime" / "toolchains" / "uv.exe"
        timed_out = repair.RepairError(
            "REPAIR_PROCESS_TIMEOUT",
            "Repair child process timed out after 600 seconds.",
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="installed",
            stderr="",
        )
        with (
            mock.patch.object(repair, "_uv_executable", return_value=uv),
            mock.patch.object(
                repair,
                "_run",
                side_effect=(timed_out, completed),
            ) as run,
        ):
            repair.install_packages(self.root, {}, emitter)

        self.assertEqual(2, run.call_count)
        self.assertEqual(run.call_args_list[0].args, run.call_args_list[1].args)
        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        retry = next(row for row in events if row["status"] == "retrying")
        self.assertEqual(2, retry["attempt"])
        self.assertEqual(2, retry["attempts"])
        self.assertTrue(retry["cache_reused"])

    def test_model_download_uses_official_mapping_and_is_rerunnable(
        self,
    ) -> None:
        output = io.StringIO()
        emitter = repair.JsonlEmitter(output)
        model_path = self.root / repair.ASR_MODEL_RELATIVE
        calls: list[dict[str, object]] = []

        def snapshot_download(**kwargs: object) -> str:
            calls.append(dict(kwargs))
            model_path.mkdir(parents=True, exist_ok=True)
            for name in (*repair.ASR_REQUIRED_FILES, "vocabulary.json"):
                (model_path / name).write_bytes(b"fixture")
            return str(model_path)

        fake_hub = types.SimpleNamespace(snapshot_download=snapshot_download)
        environment = repair._repair_environment(self.root)
        with (
            mock.patch.dict(sys.modules, {"huggingface_hub": fake_hub}),
            mock.patch.object(repair, "_download_file") as download,
        ):
            repair.install_model(
                self.root,
                model_path,
                environment,
                emitter,
            )
            repair.install_model(
                self.root,
                model_path,
                environment,
                emitter,
            )

        self.assertEqual(1, len(calls))
        self.assertEqual(repair.ASR_REPOSITORY, calls[0]["repo_id"])
        self.assertEqual(repair.ASR_REVISION, calls[0]["revision"])
        self.assertEqual(str(model_path), calls[0]["local_dir"])
        self.assertEqual(
            environment["HF_HUB_CACHE"],
            calls[0]["cache_dir"],
        )
        self.assertEqual(
            repair.HF_HUB_ETAG_TIMEOUT_SECONDS,
            calls[0]["etag_timeout"],
        )
        self.assertEqual(
            repair.HF_HUB_MAX_WORKERS,
            calls[0]["max_workers"],
        )
        self.assertEqual(
            (repair.ASR_MODEL_FILE,),
            calls[0]["ignore_patterns"],
        )
        self.assertEqual(1, download.call_count)
        self.assertEqual(
            repair.ASR_MODEL_FILE_SHA256,
            download.call_args.kwargs["expected_sha256"],
        )
        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual("downloading", events[0]["status"])
        self.assertEqual("ready", events[-1]["status"])
        self.assertTrue(events[-1]["cached"])

    def test_model_download_retries_once_with_the_same_hf_cache(self) -> None:
        output = io.StringIO()
        emitter = repair.JsonlEmitter(output)
        model_path = self.root / repair.ASR_MODEL_RELATIVE
        environment = repair._repair_environment(self.root)
        calls: list[dict[str, object]] = []

        def snapshot_download(**kwargs: object) -> str:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                raise TimeoutError("fixture model timeout")
            model_path.mkdir(parents=True, exist_ok=True)
            for name in (*repair.ASR_REQUIRED_FILES, "vocabulary.json"):
                (model_path / name).write_bytes(b"fixture")
            return str(model_path)

        fake_hub = types.SimpleNamespace(snapshot_download=snapshot_download)
        with (
            mock.patch.dict(sys.modules, {"huggingface_hub": fake_hub}),
            mock.patch.object(repair, "_download_file"),
        ):
            repair.install_model(
                self.root,
                model_path,
                environment,
                emitter,
            )

        self.assertEqual(2, len(calls))
        self.assertEqual(
            calls[0]["cache_dir"],
            calls[1]["cache_dir"],
        )
        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        retry = next(row for row in events if row["status"] == "retrying")
        self.assertEqual(2, retry["attempt"])
        self.assertEqual(2, retry["attempts"])
        self.assertTrue(retry["cache_reused"])
        self.assertIn("fixture model timeout", retry["message"])

    def test_model_download_stops_after_two_bounded_failures(self) -> None:
        model_path = self.root / repair.ASR_MODEL_RELATIVE
        environment = repair._repair_environment(self.root)
        snapshot_download = mock.Mock(
            side_effect=TimeoutError("stable fixture timeout")
        )
        fake_hub = types.SimpleNamespace(snapshot_download=snapshot_download)

        with (
            mock.patch.dict(sys.modules, {"huggingface_hub": fake_hub}),
            mock.patch.object(repair, "_download_file"),
            self.assertRaises(repair.RepairError) as caught,
        ):
            repair.install_model(
                self.root,
                model_path,
                environment,
                repair.JsonlEmitter(io.StringIO()),
            )

        self.assertEqual("MODEL_DOWNLOAD_FAILED", caught.exception.code)
        self.assertIn("2 bounded attempts", caught.exception.message)
        self.assertIn("stable fixture timeout", caught.exception.message)
        self.assertEqual(2, snapshot_download.call_count)

    def test_ffmpeg_install_extracts_only_bounded_runtime_executables(
        self,
    ) -> None:
        def fake_download(
            _url: str,
            destination: Path,
            **_kwargs: object,
        ) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr(
                    "ffmpeg-fixture/bin/ffmpeg.exe",
                    b"ffmpeg",
                )
                archive.writestr(
                    "ffmpeg-fixture/bin/ffprobe.exe",
                    b"ffprobe",
                )
                archive.writestr(
                    "../../outside-project.txt",
                    b"must not extract",
                )

        with mock.patch.object(
            repair,
            "_download_file",
            side_effect=fake_download,
        ):
            ffmpeg = repair.install_ffmpeg(
                self.root,
                repair.JsonlEmitter(io.StringIO()),
            )

        self.assertEqual(
            (
                self.root
                / ".runtime"
                / "dependencies"
                / "ffmpeg"
                / "bin"
                / "ffmpeg.exe"
            ),
            ffmpeg,
        )
        self.assertEqual(b"ffmpeg", ffmpeg.read_bytes())
        self.assertEqual(b"ffprobe", ffmpeg.with_name("ffprobe.exe").read_bytes())
        self.assertFalse((self.root / "outside-project.txt").exists())

    def test_ffmpeg_download_resumes_an_early_ended_response(self) -> None:
        payload = b"abcdef"
        destination = self.root / ".runtime" / "a" / "d" / "ffmpeg.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        requests: list[object] = []

        class Response(io.BytesIO):
            def __init__(
                self,
                data: bytes,
                *,
                status: int,
                headers: dict[str, str],
            ) -> None:
                super().__init__(data)
                self.status = status
                self.headers = headers

            def getcode(self) -> int:
                return self.status

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                self.close()

        responses = (
            Response(
                payload[:3],
                status=200,
                headers={"Content-Length": str(len(payload))},
            ),
            Response(
                payload[3:],
                status=206,
                headers={
                    "Content-Length": "3",
                    "Content-Range": "bytes 3-5/6",
                },
            ),
        )

        def urlopen(request: object, **_kwargs: object) -> Response:
            requests.append(request)
            return responses[len(requests) - 1]

        output = io.StringIO()
        with mock.patch.object(
            repair.urllib.request,
            "urlopen",
            side_effect=urlopen,
        ):
            repair._download_file(
                "https://example.invalid/ffmpeg.zip",
                destination,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                maximum_bytes=1024,
                emitter=repair.JsonlEmitter(output),
            )

        self.assertEqual(payload, destination.read_bytes())
        self.assertEqual(2, len(requests))
        self.assertIsNone(requests[0].get_header("Range"))
        self.assertEqual("bytes=3-", requests[1].get_header("Range"))
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(any(row["status"] == "retrying" for row in events))

    def test_range_downgrade_never_overwrites_an_existing_checkpoint(
        self,
    ) -> None:
        destination = self.root / ".runtime" / "a" / "d" / "model.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.write_bytes(b"preserved checkpoint")

        class Response(io.BytesIO):
            headers = {"Content-Length": "16"}

            def getcode(self) -> int:
                return 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                self.close()

        with (
            mock.patch.object(
                repair.urllib.request,
                "urlopen",
                return_value=Response(b"replacement data"),
            ),
            self.assertRaises(repair.RepairError) as caught,
        ):
            repair._download_file(
                "https://example.invalid/model.bin",
                destination,
                expected_sha256="0" * 64,
                maximum_bytes=1024,
                emitter=repair.JsonlEmitter(io.StringIO()),
                attempts=1,
                error_code_prefix="MODEL",
                artifact_name="ASR model",
            )

        self.assertEqual("MODEL_DOWNLOAD_FAILED", caught.exception.code)
        self.assertEqual(b"preserved checkpoint", partial.read_bytes())

    def test_model_override_must_stay_below_project_runtime(self) -> None:
        with self.assertRaises(repair.RepairError) as captured:
            repair._model_path("../outside-model", self.root)
        self.assertEqual("REPAIR_PATH_UNSAFE", captured.exception.code)

    def test_main_streams_all_repair_stages_and_terminal_result(self) -> None:
        output = io.StringIO()
        ffmpeg = (
            self.root
            / ".runtime"
            / "dependencies"
            / "ffmpeg"
            / "bin"
            / "ffmpeg.exe"
        )

        def package(
            _root: Path,
            _environment: object,
            emitter: repair.JsonlEmitter,
        ) -> None:
            emitter.emit(
                "progress",
                stage="package",
                component="python_dependencies",
                status="ready",
            )

        def model(
            _root: Path,
            _model_path: Path,
            _environment: object,
            emitter: repair.JsonlEmitter,
        ) -> None:
            emitter.emit(
                "progress",
                stage="model",
                component="asr",
                status="ready",
            )

        def ffmpeg_install(
            _root: Path,
            emitter: repair.JsonlEmitter,
        ) -> Path:
            emitter.emit(
                "progress",
                stage="download",
                component="ffmpeg",
                status="ready",
            )
            return ffmpeg

        def verify(
            _root: Path,
            _model_path: Path,
            _ffmpeg: Path,
            _environment: object,
            emitter: repair.JsonlEmitter,
        ) -> dict[str, object]:
            result = {"ready": True}
            emitter.emit(
                "progress",
                stage="verify",
                component="asset_profile",
                status="ready",
            )
            return result

        environment_before = dict(repair.os.environ)
        with (
            mock.patch.object(repair, "_assert_managed_python"),
            mock.patch.object(
                repair,
                "install_packages",
                side_effect=package,
            ),
            mock.patch.object(repair, "install_model", side_effect=model),
            mock.patch.object(
                repair,
                "install_ffmpeg",
                side_effect=ffmpeg_install,
            ),
            mock.patch.object(
                repair,
                "verify_profile",
                side_effect=verify,
            ),
        ):
            exit_code = repair.main(
                ("--project-root", str(self.root)),
                stdout=output,
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(environment_before, dict(repair.os.environ))
        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual("started", events[0]["event"])
        self.assertEqual("completed", events[-1]["event"])
        self.assertEqual(
            ["package", "download", "model", "verify"],
            [
                event["stage"]
                for event in events
                if event["event"] == "progress"
            ],
        )
        self.assertTrue(events[-1]["result"]["ready"])

    def test_main_preserves_ffmpeg_when_model_download_fails(self) -> None:
        output = io.StringIO()
        call_order: list[str] = []
        ffmpeg = (
            self.root
            / ".runtime"
            / "dependencies"
            / "ffmpeg"
            / "bin"
            / "ffmpeg.exe"
        )

        def package(
            _root: Path,
            _environment: object,
            _emitter: repair.JsonlEmitter,
        ) -> None:
            call_order.append("package")

        def ffmpeg_install(
            _root: Path,
            _emitter: repair.JsonlEmitter,
        ) -> Path:
            call_order.append("ffmpeg")
            ffmpeg.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.write_bytes(b"fixture-ffmpeg")
            return ffmpeg

        def model(
            _root: Path,
            _model_path: Path,
            _environment: object,
            _emitter: repair.JsonlEmitter,
        ) -> None:
            call_order.append("model")
            raise repair.RepairError(
                "MODEL_DOWNLOAD_FAILED",
                "stable fixture model failure",
            )

        environment_before = dict(repair.os.environ)
        with (
            mock.patch.object(repair, "_assert_managed_python"),
            mock.patch.object(
                repair,
                "install_packages",
                side_effect=package,
            ),
            mock.patch.object(
                repair,
                "install_ffmpeg",
                side_effect=ffmpeg_install,
            ),
            mock.patch.object(
                repair,
                "install_model",
                side_effect=model,
            ),
            mock.patch.object(repair, "verify_profile") as verify,
        ):
            exit_code = repair.main(
                ("--project-root", str(self.root)),
                stdout=output,
            )

        self.assertEqual(1, exit_code)
        self.assertEqual(environment_before, dict(repair.os.environ))
        self.assertEqual(["package", "ffmpeg", "model"], call_order)
        self.assertTrue(ffmpeg.is_file())
        verify.assert_not_called()
        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual("error", events[-1]["event"])
        self.assertEqual(
            "MODEL_DOWNLOAD_FAILED",
            events[-1]["error"]["code"],
        )


if __name__ == "__main__":
    unittest.main()
