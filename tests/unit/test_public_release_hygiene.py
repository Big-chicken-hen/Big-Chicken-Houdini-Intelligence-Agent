from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "check-public-release.py"
SPEC = importlib.util.spec_from_file_location("check_public_release", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PublicReleaseHygieneTests(unittest.TestCase):
    def _write_zip(self, entries: dict[str, bytes]) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        archive = Path(temporary.name) / "release.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name, content in entries.items():
                package.writestr(name, content)
        return archive

    def test_clean_release_with_optional_root_passes(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/README.md": b"# Big-Chicken Houdini Intelligence Agent\n",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/LICENSE": b"license\n",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/src/hia_core/__init__.py": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/assets/launcher/original-artwork.png": b"\x89PNG",
            }
        )
        self.assertEqual([], CHECKER.inspect_release(archive))
        self.assertEqual(0, CHECKER.main([str(archive)]))

    def test_private_runtime_outputs_and_historical_docs_are_rejected(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/.runtime/codex-home/auth.json": b"{}",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/tests/unit/test_example.py": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/docs/P2-V-GATE-B2C.md": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/docs/TEST-REPORT.md": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/cache/screenshots/view.png": b"\x89PNG",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/example.hip": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/render/final.exr": b"",
            }
        )
        violations = CHECKER.inspect_release(archive)
        encoded = "\n".join(violations).lower()
        for expected in (
            ".runtime",
            "tests",
            "historical gate document",
            "internal test report",
            "screenshots",
            "example.hip",
            "final.exr",
        ):
            self.assertIn(expected, encoded)

    def test_unlicensed_artwork_and_credentials_are_rejected(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/assets/launcher/steam-winter-sale.png": b"\x89PNG",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/NOTICE.steam-winter-sale.txt": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/config/.env.local": b"MODE=test",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/config/public.txt": (
                    b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
                ),
            }
        )
        violations = CHECKER.inspect_release(archive)
        encoded = "\n".join(violations).lower()
        self.assertIn("unlicensed launcher artwork", encoded)
        self.assertIn("notice.steam-winter-sale.txt", encoded)
        self.assertIn(".env.local", encoded)
        self.assertIn("possible credential content", encoded)

    def test_nonportable_archive_paths_are_rejected(self) -> None:
        for name in ("/absolute/file.txt", "../outside.txt", "C:/private/file.txt"):
            with self.subTest(name=name):
                self.assertTrue(CHECKER._path_violations(name))

    def test_historical_houdini_panels_are_rejected(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/houdini_package/python_panels/"
                "hia_b4b_stairs_acceptance.pypanel": b"<xml/>",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/houdini_package/python_libs/hia_panel/"
                "ime_diagnostic.py": b"",
            }
        )
        violations = "\n".join(CHECKER.inspect_release(archive))
        self.assertIn("non-production Houdini runtime file", violations)


if __name__ == "__main__":
    unittest.main()
