from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
CACHE_CLI = REPOSITORY_ROOT / "scripts" / "hia-cache.ps1"
RUNTIME_TEST_ROOT = (
    REPOSITORY_ROOT / ".runtime" / "launcher-tests" / "cache-cli"
)


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class LauncherCacheCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RUNTIME_TEST_ROOT / uuid.uuid4().hex
        self.project = self.sandbox / "project"
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(CACHE_CLI, scripts / CACHE_CLI.name)
        self.cli = scripts / CACHE_CLI.name

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def run_cli(
        self,
        action: str,
        *,
        categories: str | None = None,
        snapshot_hash: str | None = None,
        expected_code: int = 0,
        script: Path | None = None,
    ) -> dict[str, object]:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script or self.cli),
            "-Action",
            action,
        ]
        if categories is not None:
            command.extend(["-Category", categories])
        if snapshot_hash is not None:
            command.extend(["-SnapshotHash", snapshot_hash])
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            expected_code,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        output = completed.stdout.strip()
        self.assertTrue(output, completed.stderr)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            self.fail(
                f"cache CLI did not return one JSON document: {output!r}; {exc}"
            )

    def write_cache_file(self, relative: str, content: bytes) -> Path:
        path = self.project / ".runtime" / "cache" / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def stabilize_cache_directory_metadata(self, root: Path) -> None:
        """Remove NTFS lazy directory-timestamp noise from snapshot tests."""
        stable_ns = 1_700_000_000_000_000_000
        directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            os.utime(directory, ns=(stable_ns, stable_ns))

    def make_junction(self, link: Path, target: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "New-Item -ItemType Junction -Path "
                    f"{_ps_literal(link)} -Target {_ps_literal(target)} "
                    "| Out-Null"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest(
                "Junction creation is unavailable: " + completed.stderr
            )

    def test_list_and_clear_csv_categories_preserve_every_boundary(self) -> None:
        screenshot = self.write_cache_file(
            "screenshots/nested/shot.png", b"shot"
        )
        preview = self.write_cache_file("previews/card.png", b"preview")
        untouched_tmp = self.write_cache_file("tmp/session.tmp", b"tmp")
        untouched_dotnet = self.write_cache_file("dotnet/nuget/pkg", b"pkg")
        cache_root_file = self.write_cache_file("final-render.exr", b"render")
        renders = self.write_cache_file("renders/keep.exr", b"render-dir")
        research = self.write_cache_file("research/keep.txt", b"research")

        protected: list[Path] = []
        for relative in (
            ".venv/Lib/site-packages/torch/__init__.py",
            ".runtime/knowledge/knowledge.sqlite3",
            ".runtime/models/model.bin",
            ".runtime/toolchains/python.exe",
            ".runtime/toolchains/hia-embedding/venv/legacy.txt",
            ".runtime/attachments/input.png",
            ".runtime/launcher-sessions/checkpoints/turn.json",
            ".runtime/diagnostics/report.json",
            "workfile.hip",
        ):
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"protected")
            protected.append(path)

        outside_sentinel = self.sandbox / "outside-sentinel.txt"
        outside_sentinel.write_bytes(b"outside")
        adjacent = self.sandbox / "adjacent" / ".runtime" / "cache"
        adjacent.mkdir(parents=True)
        adjacent_sentinel = adjacent / "screenshots.png"
        adjacent_sentinel.write_bytes(b"adjacent")

        self.stabilize_cache_directory_metadata(
            self.project / ".runtime" / "cache"
        )

        preview_payload = self.run_cli(
            "list", categories="previews,screenshots"
        )
        self.assertTrue(preview_payload["ok"])
        self.assertEqual(
            ["screenshots", "previews"],
            preview_payload["selected_categories"],
        )
        self.assertEqual(11, preview_payload["total_bytes"])
        self.assertRegex(
            str(preview_payload["snapshot_hash"]), r"\A[0-9a-f]{64}\Z"
        )
        category_payloads = {
            item["id"]: item for item in preview_payload["categories"]
        }
        self.assertEqual(4, category_payloads["screenshots"]["bytes"])
        self.assertEqual(7, category_payloads["previews"]["bytes"])
        self.assertTrue(
            all(
                item["category_root_preserved"]
                for item in preview_payload["categories"]
            )
        )

        cleared = self.run_cli(
            "clear",
            categories="screenshots,previews",
            snapshot_hash=str(preview_payload["snapshot_hash"]),
        )
        self.assertTrue(cleared["ok"])
        self.assertEqual(11, cleared["freed_bytes"])
        self.assertEqual(0, cleared["failed_count"])
        self.assertFalse(screenshot.exists())
        self.assertFalse(preview.exists())
        self.assertTrue(
            (self.project / ".runtime/cache/screenshots").is_dir()
        )
        self.assertTrue((self.project / ".runtime/cache/previews").is_dir())

        for path in (
            untouched_tmp,
            untouched_dotnet,
            cache_root_file,
            renders,
            research,
            *protected,
            outside_sentinel,
            adjacent_sentinel,
        ):
            self.assertTrue(path.is_file(), path)

    def test_stale_combined_snapshot_causes_zero_writes(self) -> None:
        screenshot = self.write_cache_file("screenshots/a.png", b"a")
        preview = self.write_cache_file("previews/b.png", b"b")
        listed = self.run_cli(
            "list", categories="screenshots,previews"
        )
        preview.write_bytes(b"changed-after-preview")

        rejected = self.run_cli(
            "clear",
            categories="screenshots,previews",
            snapshot_hash=str(listed["snapshot_hash"]),
            expected_code=4,
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual("CACHE_SNAPSHOT_STALE", rejected["error"]["code"])
        self.assertTrue(screenshot.is_file())
        self.assertEqual(b"changed-after-preview", preview.read_bytes())

    def test_houdini_scene_in_category_blocks_the_entire_selected_batch(
        self,
    ) -> None:
        safe_preview = self.write_cache_file("previews/card.png", b"preview")
        hip = self.write_cache_file("tmp/recovery.hip", b"hip")
        hiplc = self.write_cache_file("tmp/recovery.HIPLC", b"hiplc")
        hipnc = self.write_cache_file("tmp/recovery.hipnc", b"hipnc")

        listed = self.run_cli("list", categories="previews,tmp")
        self.assertEqual(["tmp"], listed["blocked_categories"])
        tmp_category = next(
            item for item in listed["categories"] if item["id"] == "tmp"
        )
        self.assertTrue(
            any(
                "Houdini scene file" in reason
                for reason in tmp_category["block_reasons"]
            )
        )

        rejected = self.run_cli(
            "clear",
            categories="previews,tmp",
            snapshot_hash=str(listed["snapshot_hash"]),
            expected_code=3,
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual("CACHE_CATEGORY_BLOCKED", rejected["error"]["code"])
        for path in (safe_preview, hip, hiplc, hipnc):
            self.assertTrue(path.is_file(), path)

    def test_unknown_or_path_escape_category_is_rejected_without_writes(
        self,
    ) -> None:
        screenshot = self.write_cache_file("screenshots/a.png", b"keep")
        knowledge = self.project / ".runtime" / "knowledge" / "keep.txt"
        knowledge.parent.mkdir(parents=True)
        knowledge.write_bytes(b"knowledge")

        rejected = self.run_cli(
            "clear",
            categories=r"screenshots,..\knowledge",
            snapshot_hash="0" * 64,
            expected_code=2,
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual("CACHE_COMMAND_ERROR", rejected["error"]["code"])
        self.assertIn("Unsupported cache category", rejected["error"]["message"])
        self.assertTrue(screenshot.is_file())
        self.assertTrue(knowledge.is_file())

    def test_embedding_runtime_excludes_downloads(self) -> None:
        runtime_cache = self.write_cache_file(
            "embedding/torch/compiled.bin", b"runtime"
        )
        uv_install_cache = self.write_cache_file(
            "embedding/uv/archive-v0/package.whl", b"uv-cache"
        )
        download_cache = self.write_cache_file(
            "embedding/huggingface/blobs/model.bin", b"download"
        )
        protected: list[Path] = []
        for relative in (
            ".venv/Lib/site-packages/torch/__init__.py",
            ".runtime/toolchains/hia-embedding/venv/legacy.txt",
            ".runtime/models/qwen/model.safetensors",
            ".runtime/knowledge/knowledge.sqlite3",
            "recovery.hipnc",
        ):
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"protected")
            protected.append(path)
        self.stabilize_cache_directory_metadata(
            self.project / ".runtime" / "cache" / "embedding"
        )
        listed = self.run_cli("list", categories="embedding-runtime")
        self.assertTrue(listed["ok"])
        self.assertEqual(15, listed["total_bytes"])
        category = listed["categories"][0]
        self.assertEqual(
            str(
                self.project
                / ".runtime"
                / "cache"
                / "embedding"
                / "huggingface"
            ),
            category["excluded_path"],
        )

        cleared = self.run_cli(
            "clear",
            categories="embedding-runtime",
            snapshot_hash=str(listed["snapshot_hash"]),
        )
        self.assertTrue(cleared["ok"])
        self.assertFalse(runtime_cache.exists())
        self.assertFalse(uv_install_cache.exists())
        self.assertTrue(download_cache.is_file())
        for path in protected:
            self.assertTrue(path.is_file(), path)
        self.assertTrue(
            (self.project / ".runtime/cache/embedding").is_dir()
        )
        self.assertTrue(
            (self.project / ".runtime/cache/embedding/huggingface").is_dir()
        )

    def test_embedding_download_reparse_is_blocked_and_never_followed(
        self,
    ) -> None:
        safe_selected = self.write_cache_file(
            "screenshots/must-also-remain.png", b"safe"
        )
        downloads = (
            self.project / ".runtime" / "cache" / "embedding" / "huggingface"
        )
        downloads.mkdir(parents=True)
        outside = self.sandbox / "outside-download-data"
        sentinel = outside / "keep.bin"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"outside")
        self.make_junction(downloads / "models--linked", outside)

        listed = self.run_cli(
            "list", categories="screenshots,embedding-downloads"
        )
        self.assertTrue(listed["ok"])
        self.assertEqual(["embedding-downloads"], listed["blocked_categories"])
        downloads_payload = next(
            item
            for item in listed["categories"]
            if item["id"] == "embedding-downloads"
        )
        self.assertTrue(downloads_payload["blocked"])
        self.assertTrue(
            any(
                "reparse point" in reason
                for reason in downloads_payload["block_reasons"]
            )
        )

        rejected = self.run_cli(
            "clear",
            categories="screenshots,embedding-downloads",
            snapshot_hash=str(listed["snapshot_hash"]),
            expected_code=3,
        )
        self.assertEqual(
            "CACHE_CATEGORY_BLOCKED", rejected["error"]["code"]
        )
        self.assertTrue(safe_selected.is_file())
        self.assertTrue(sentinel.is_file())

    def test_reparse_at_each_target_level_and_internal_entry_fails_closed(
        self,
    ) -> None:
        for level in ("runtime", "cache", "screenshots", "internal"):
            with self.subTest(level=level):
                case = self.sandbox / f"reparse-{level}"
                project = case / "project"
                scripts = project / "scripts"
                scripts.mkdir(parents=True)
                shutil.copy2(CACHE_CLI, scripts / CACHE_CLI.name)
                cli = scripts / CACHE_CLI.name
                target = case / f"{level}-target"

                if level == "runtime":
                    link = project / ".runtime"
                    marker = target / "cache" / "screenshots" / "keep.png"
                elif level == "cache":
                    (project / ".runtime").mkdir()
                    link = project / ".runtime" / "cache"
                    marker = target / "screenshots" / "keep.png"
                elif level == "screenshots":
                    (project / ".runtime" / "cache").mkdir(parents=True)
                    link = project / ".runtime" / "cache" / "screenshots"
                    marker = target / "keep.png"
                else:
                    cache = project / ".runtime" / "cache" / "screenshots"
                    cache.mkdir(parents=True)
                    link = cache / "linked"
                    marker = target / "keep.png"

                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"sentinel")
                self.make_junction(link, target)
                listed = self.run_cli(
                    "list", categories="screenshots", script=cli
                )
                self.assertTrue(listed["ok"])
                self.assertEqual(["screenshots"], listed["blocked_categories"])
                rejected = self.run_cli(
                    "clear",
                    categories="screenshots",
                    snapshot_hash=str(listed["snapshot_hash"]),
                    expected_code=3,
                    script=cli,
                )
                self.assertEqual(
                    "CACHE_CATEGORY_BLOCKED", rejected["error"]["code"]
                )
                self.assertEqual(b"sentinel", marker.read_bytes())

    def test_project_root_reparse_is_rejected_before_cache_inspection(
        self,
    ) -> None:
        case = self.sandbox / "reparse-project-root"
        real_project = case / "real-project"
        scripts = real_project / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(CACHE_CLI, scripts / CACHE_CLI.name)
        marker = (
            real_project
            / ".runtime"
            / "cache"
            / "screenshots"
            / "keep.png"
        )
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b"sentinel")
        linked_project = case / "linked-project"
        self.make_junction(linked_project, real_project)

        rejected = self.run_cli(
            "list",
            categories="screenshots",
            expected_code=2,
            script=linked_project / "scripts" / CACHE_CLI.name,
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual("CACHE_COMMAND_ERROR", rejected["error"]["code"])
        self.assertIn("reparse point", rejected["error"]["message"])
        self.assertEqual(b"sentinel", marker.read_bytes())

    def test_active_embedding_installer_lock_blocks_both_categories(self) -> None:
        self.write_cache_file("embedding/torch/cache.bin", b"runtime")
        self.write_cache_file(
            "embedding/huggingface/blobs/cache.bin", b"download"
        )
        launcher = self.project / ".runtime" / "launcher"
        launcher.mkdir(parents=True)
        lock_path = launcher / "embedding-install.lock"
        lock_path.write_text("{}\n", encoding="utf-8")
        command = f"""
$stream = [System.IO.FileStream]::new(
    {_ps_literal(lock_path)},
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::Read
)
& {_ps_literal(self.cli)} `
    -Action list `
    -Category @('embedding-runtime', 'embedding-downloads')
"""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(
            ["embedding-runtime", "embedding-downloads"],
            payload["blocked_categories"],
        )
        for category in payload["categories"]:
            self.assertTrue(category["blocked"])
            self.assertTrue(
                any(
                    "installer lock is active" in reason
                    for reason in category["block_reasons"]
                )
            )

    def test_source_and_ast_enforce_non_recursive_project_relative_contract(
        self,
    ) -> None:
        source = CACHE_CLI.read_text(encoding="utf-8-sig")
        for required in (
            "$PSCommandPath",
            "GetFullPath",
            "GetFileSystemInfos()",
            "[System.IO.FileAttributes]::ReparsePoint",
            "[System.IO.File]::Delete",
            "[System.IO.Directory]::Delete($path, $false)",
            "hia-cache-json/1",
            "CACHE_SNAPSHOT_STALE",
            "CACHE_CATEGORY_BLOCKED",
            "embedding-install.lock",
            "excluded_child = 'huggingface'",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "Remove-Item",
            "-Recurse",
            "Get-ChildItem",
            "USERPROFILE",
            "$HOME",
            '"~"',
            "'~'",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(re.search(r"(?i)(?:^|[\"'\s])[a-z]:[\\/]", source))
        self.assertNotRegex(source, r"Directory\]::Delete\([^,\n]+,\s*\$true")

        command = f"""
$tokens = $null
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    {_ps_literal(CACHE_CLI)},
    [ref]$tokens,
    [ref]$errors
)
@($errors | ForEach-Object {{ $_.Message }}) |
    ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        errors = json.loads(completed.stdout.strip() or "[]")
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
