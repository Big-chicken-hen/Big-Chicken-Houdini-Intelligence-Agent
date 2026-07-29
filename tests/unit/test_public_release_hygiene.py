from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "check-public-release.py"
KNOWLEDGE_PACK_PATH = REPOSITORY_ROOT / "knowledge" / "sidefx-official"
COMMUNITY_KNOWLEDGE_PACK_PATH = (
    REPOSITORY_ROOT / "knowledge" / "community-tutorials"
)
SPEC = importlib.util.spec_from_file_location("check_public_release", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PublicReleaseHygieneTests(unittest.TestCase):
    def _write_zip(self, entries: dict[str, bytes]) -> Path:
        temporary = tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT)
        self.addCleanup(temporary.cleanup)
        archive = Path(temporary.name) / "release.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name, content in entries.items():
                package.writestr(name, content)
        return archive

    def _write_git_knowledge_fixture(
        self,
        *,
        leave_untracked: str | None = None,
    ) -> Path:
        temporary = tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manifest = {
            "schema_version": 1,
            "sources": [
                {"path": "cards/first.md"},
                {"path": "cards/second.md"},
            ],
        }
        pack_names = ("sidefx-official", "community-tutorials")
        for pack_name in pack_names:
            pack = root / "knowledge" / pack_name
            cards = pack / "cards"
            cards.mkdir(parents=True)
            (pack / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (pack / "coverage.json").write_text("{}", encoding="utf-8")
            (pack / "sources.json").write_text("{}", encoding="utf-8")
            (cards / "first.md").write_text("# First\n", encoding="utf-8")
            (cards / "second.md").write_text("# Second\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            capture_output=True,
        )
        relative_paths = [
            f"knowledge/{pack_name}/{relative}"
            for pack_name in pack_names
            for relative in (
                "manifest.json",
                "coverage.json",
                "sources.json",
                "cards/first.md",
                "cards/second.md",
            )
        ]
        tracked_paths = [
            path for path in relative_paths if path != leave_untracked
        ]
        subprocess.run(
            ["git", "-C", str(root), "add", "--", *tracked_paths],
            check=True,
            capture_output=True,
        )
        return root

    def test_clean_release_with_optional_root_passes(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/README.md": b"# Big-Chicken Houdini Intelligence Agent\n",
                f"{root}/LICENSE": b"license\n",
                f"{root}/src/hia_core/__init__.py": b"",
                f"{root}/assets/launcher/original-artwork.png": b"\x89PNG",
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [{"path": "cards/workflow.md"}],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/coverage.json": b"{}",
                f"{root}/knowledge/sidefx-official/sources.json": b"{}",
                f"{root}/knowledge/sidefx-official/cards/workflow.md": (
                    b"# Original workflow card\n"
                ),
            }
        )
        self.assertEqual([], CHECKER.inspect_release(archive))
        self.assertEqual(0, CHECKER.main([str(archive)]))

    def test_knowledge_manifest_requires_every_declared_card(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [
                            {"path": "cards/present.md"},
                            {"path": "cards/missing.md"},
                        ],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/coverage.json": b"{}",
                f"{root}/knowledge/sidefx-official/sources.json": b"{}",
                f"{root}/knowledge/sidefx-official/cards/present.md": b"present",
            }
        )

        violations = "\n".join(CHECKER.inspect_release(archive))

        self.assertIn(
            "built-in knowledge card is missing or not an ordinary file: "
            "cards/missing.md",
            violations,
        )

    def test_knowledge_archive_rejects_undeclared_card(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [{"path": "cards/declared.md"}],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/coverage.json": b"{}",
                f"{root}/knowledge/sidefx-official/sources.json": b"{}",
                f"{root}/knowledge/sidefx-official/cards/declared.md": b"declared",
                f"{root}/knowledge/sidefx-official/cards/extra.md": b"extra",
            }
        )

        violations = "\n".join(CHECKER.inspect_release(archive))

        self.assertIn(
            "built-in knowledge archive has an undeclared card: cards/extra.md",
            violations,
        )

    def test_current_knowledge_pack_disk_manifest_and_archive_cards_match(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
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
        packaged_paths = manifest_cards | {
            "manifest.json",
            "coverage.json",
            "sources.json",
        }
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/{relative_path}": (
                    KNOWLEDGE_PACK_PATH / relative_path
                ).read_bytes()
                for relative_path in packaged_paths
            }
        )
        with zipfile.ZipFile(archive) as package:
            archive_cards = {
                Path(name).relative_to(
                    root, "knowledge", "sidefx-official"
                ).as_posix()
                for name in package.namelist()
                if "/knowledge/sidefx-official/cards/" in name
                and name.lower().endswith(".md")
            }

        self.assertEqual(disk_cards, manifest_cards)
        self.assertEqual(manifest_cards, archive_cards)
        self.assertEqual([], CHECKER.inspect_release(archive))

    def test_current_git_preflight_reports_exact_real_tracking_gap(self) -> None:
        expected: set[str] = set()
        for pack_path in (
            KNOWLEDGE_PACK_PATH,
            COMMUNITY_KNOWLEDGE_PACK_PATH,
        ):
            relative_root = pack_path.relative_to(REPOSITORY_ROOT).as_posix()
            manifest = json.loads(
                (pack_path / "manifest.json").read_text(encoding="utf-8")
            )
            expected.update(
                f"{relative_root}/{name}"
                for name in ("manifest.json", "coverage.json", "sources.json")
            )
            expected.update(
                f"{relative_root}/{item['path']}"
                for item in manifest["sources"]
            )
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "ls-files",
                "-z",
                "--",
                "knowledge/sidefx-official",
                "knowledge/community-tutorials",
            ],
            check=True,
            capture_output=True,
        )
        tracked = {
            value.decode("utf-8")
            for value in completed.stdout.split(b"\0")
            if value
        }
        expected_missing = expected - tracked

        violations = CHECKER.inspect_git_knowledge_source(REPOSITORY_ROOT)
        actual_missing = {
            violation.rsplit(": ", 1)[1]
            for violation in violations
            if "not tracked by git:" in violation
        }

        self.assertEqual(expected_missing, actual_missing)
        if not expected_missing:
            self.assertEqual([], violations)

    def test_git_preflight_accepts_complete_tracked_pack(self) -> None:
        root = self._write_git_knowledge_fixture()

        self.assertEqual([], CHECKER.inspect_git_knowledge_source(root))

    def test_git_preflight_rejects_untracked_manifest_card(self) -> None:
        missing = "knowledge/sidefx-official/cards/second.md"
        root = self._write_git_knowledge_fixture(leave_untracked=missing)

        violations = CHECKER.inspect_git_knowledge_source(root)

        self.assertIn(
            f"built-in knowledge manifest card is not tracked by git: {missing}",
            violations,
        )

    def test_git_preflight_rejects_untracked_required_metadata(self) -> None:
        for basename in ("manifest.json", "coverage.json", "sources.json"):
            with self.subTest(basename=basename):
                missing = f"knowledge/sidefx-official/{basename}"
                root = self._write_git_knowledge_fixture(
                    leave_untracked=missing
                )

                violations = CHECKER.inspect_git_knowledge_source(root)

                self.assertIn(
                    "built-in knowledge metadata is not tracked by git: "
                    f"{missing}",
                    violations,
                )

    def test_git_preflight_rejects_untracked_community_manifest_card(
        self,
    ) -> None:
        missing = "knowledge/community-tutorials/cards/second.md"
        root = self._write_git_knowledge_fixture(leave_untracked=missing)

        violations = CHECKER.inspect_git_knowledge_source(root)

        self.assertIn(
            f"built-in knowledge manifest card is not tracked by git: {missing}",
            violations,
        )

    def test_git_preflight_rejects_untracked_community_metadata(self) -> None:
        for basename in ("manifest.json", "coverage.json", "sources.json"):
            with self.subTest(basename=basename):
                missing = f"knowledge/community-tutorials/{basename}"
                root = self._write_git_knowledge_fixture(
                    leave_untracked=missing
                )

                violations = CHECKER.inspect_git_knowledge_source(root)

                self.assertIn(
                    "built-in knowledge metadata is not tracked by git: "
                    f"{missing}",
                    violations,
                )

    def test_git_preflight_rejects_disk_card_outside_manifest(self) -> None:
        root = self._write_git_knowledge_fixture()
        extra = (
            root
            / "knowledge"
            / "sidefx-official"
            / "cards"
            / "undeclared.md"
        )
        extra.write_text("# Undeclared\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(root), "add", "--", str(extra)],
            check=True,
            capture_output=True,
        )

        violations = CHECKER.inspect_git_knowledge_source(root)

        self.assertIn(
            "built-in knowledge disk has an undeclared card: "
            "cards/undeclared.md",
            violations,
        )

    def test_knowledge_manifest_rejects_escaping_card_path(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [{"path": "../outside.md"}],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/coverage.json": b"{}",
                f"{root}/knowledge/sidefx-official/sources.json": b"{}",
                f"{root}/knowledge/outside.md": b"outside",
            }
        )

        violations = "\n".join(CHECKER.inspect_release(archive))

        self.assertIn("unsafe built-in knowledge card path", violations)
        self.assertIn("../outside.md", violations)

    def test_knowledge_manifest_rejects_nonordinary_card(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [{"path": "cards/link.md"}],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/coverage.json": b"{}",
                f"{root}/knowledge/sidefx-official/sources.json": b"{}",
            }
        )
        with zipfile.ZipFile(archive, "a") as package:
            link = zipfile.ZipInfo(
                f"{root}/knowledge/sidefx-official/cards/link.md"
            )
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(link, b"target.md")

        violations = "\n".join(CHECKER.inspect_release(archive))

        self.assertIn(
            "built-in knowledge card is missing or not an ordinary file: "
            "cards/link.md",
            violations,
        )

    def test_knowledge_metadata_must_be_complete_ordinary_utf8_json(self) -> None:
        root = "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview"
        archive = self._write_zip(
            {
                f"{root}/knowledge/sidefx-official/manifest.json": json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [{"path": "cards/workflow.md"}],
                    }
                ).encode("utf-8"),
                f"{root}/knowledge/sidefx-official/sources.json": b"{invalid",
                f"{root}/knowledge/sidefx-official/cards/workflow.md": b"card",
            }
        )
        with zipfile.ZipFile(archive, "a") as package:
            link = zipfile.ZipInfo(
                f"{root}/knowledge/sidefx-official/coverage.json"
            )
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(link, b"coverage-target.json")

        violations = "\n".join(CHECKER.inspect_release(archive))

        self.assertIn(
            "metadata is missing or not ordinary: "
            "knowledge/sidefx-official/coverage.json",
            violations,
        )
        self.assertIn(
            "metadata is invalid UTF-8 JSON: "
            "knowledge/sidefx-official/sources.json",
            violations,
        )

    def test_private_runtime_outputs_and_historical_docs_are_rejected(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/.runtime/codex-home/auth.json": b"{}",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/.runtime/models/"
                "qwen3-embedding/qwen3-embedding-8b/model.safetensors": b"weights",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/.venv/"
                "pyvenv.cfg": b"home=project-local",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/.runtime/knowledge/"
                "knowledge.sqlite3": b"SQLite format 3\x00",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/tests/unit/test_example.py": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/docs/P2-V-GATE-B2C.md": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/docs/TEST-REPORT.md": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/cache/screenshots/view.png": b"\x89PNG",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/example.hip": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/cache/export.usdc": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/cache/export.abc": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/cache/export.fbx": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/render/final.exr": b"",
            }
        )
        violations = CHECKER.inspect_release(archive)
        encoded = "\n".join(violations).lower()
        for expected in (
            ".runtime",
            ".venv",
            "model.safetensors",
            "pyvenv.cfg",
            "knowledge.sqlite3",
            "tests",
            "historical gate document",
            "internal test report",
            "screenshots",
            "example.hip",
            "export.usdc",
            "export.abc",
            "export.fbx",
            "final.exr",
        ):
            self.assertIn(expected, encoded)

    def test_top_level_managed_venv_is_rejected_from_archive(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/"
                ".venv/Scripts/python.exe": b"MZ",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/"
                ".venv/Lib/site-packages/pypdf/__init__.py": b"",
            }
        )

        violations = "\n".join(CHECKER.inspect_release(archive)).lower()

        self.assertIn("forbidden directory .venv", violations)
        self.assertIn(".venv/scripts/python.exe", violations)
        self.assertIn(".venv/lib/site-packages", violations)

    def test_unlicensed_artwork_and_credentials_are_rejected(self) -> None:
        archive = self._write_zip(
            {
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/assets/launcher/steam-winter-sale.png": b"\x89PNG",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/NOTICE.steam-winter-sale.txt": b"",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/config/.env.local": b"MODE=test",
                "Big-Chicken-Houdini-Intelligence-Agent-v0.1.0-preview/config/token.json": b"{}",
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
        self.assertIn("token.json", encoded)
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
