from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
RUNTIME_TEST_ROOT = (
    REPOSITORY_ROOT / ".runtime" / "launcher-tests" / "embedding-installer"
)


def _load_installer() -> types.ModuleType:
    module_name = "_test_hia_embedding_installer_module"
    spec = importlib.util.spec_from_file_location(module_name, INSTALLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("installer helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


class EmbeddingInstallerTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RUNTIME_TEST_ROOT / uuid.uuid4().hex
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


if __name__ == "__main__":
    unittest.main()
