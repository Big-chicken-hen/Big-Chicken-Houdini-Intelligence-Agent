from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
MODULE_PATH = REPOSITORY_ROOT / "scripts" / "launcher" / "HiaLauncher.Core.psm1"
LIFECYCLE_PATH = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
GOAL_BINDING = "b" * 64


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class HiaHipLocalRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / "tests"
        )
        self.root = Path(self._temporary.name)
        self.session_id = "c" * 32
        self.session = (
            self.root
            / "portable-project"
            / ".runtime"
            / "launcher-sessions"
            / self.session_id
        )
        self.session_checkpoints = self.session / "checkpoints"
        self.session_checkpoints.mkdir(parents=True)
        self.hip_parent = self.root / "user-scenes"
        self.hip_parent.mkdir()
        self.source_hip = self.hip_parent / "asset.hip"
        self.source_hip.write_bytes(b"saved hip")
        self.external_checkpoints = self.hip_parent / ".hia" / "checkpoints"
        self.external_checkpoints.mkdir(parents=True)
        self.checkpoint = self.external_checkpoints / "asset_bak1.hip"
        self.checkpoint.write_bytes(b"checkpoint")
        self.payload = {
            "version": 2,
            "launcher_session_id": self.session_id,
            "thread_id": "thread-exact",
            "goal_binding": GOAL_BINDING,
            "storage_scope": "hip",
            "source_hip_path": str(self.source_hip.resolve()),
            "checkpoint_file": self.checkpoint.name,
        }
        self.write_markers()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def write_markers(
        self,
        *,
        pointer: dict[str, object] | None = None,
        sidecar: dict[str, object] | None = None,
    ) -> None:
        encoded_pointer = json.dumps(pointer or self.payload)
        encoded_sidecar = json.dumps(sidecar or self.payload)
        (self.session_checkpoints / ".hia-stage-checkpoint.json").write_text(
            encoded_pointer,
            encoding="utf-8",
        )
        (self.external_checkpoints / ".hia-stage-checkpoint.json").write_text(
            encoded_sidecar,
            encoding="utf-8",
        )

    def run_powershell(self, body: str) -> str:
        prefix = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Import-Module -Force {_ps_literal(MODULE_PATH)}
"""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                prefix + body,
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
        return completed.stdout.strip()

    def test_discovers_and_copies_bound_hip_local_checkpoint(self) -> None:
        output = self.run_powershell(
            f"""
$candidate = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(self.session_checkpoints)} `
    -ThreadId 'thread-exact' `
    -GoalBinding '{GOAL_BINDING}'
$copy = Copy-HiaLauncherRecoveryHip `
    -SessionRoot {_ps_literal(self.session)} `
    -SourcePath $candidate.path `
    -Attempt 1 `
    -ThreadId 'thread-exact' `
    -GoalBinding '{GOAL_BINDING}'
[pscustomobject]@{{
    path = $candidate.path
    scope = $candidate.storage_scope
    session_id = $candidate.launcher_session_id
    source_hip = $candidate.source_hip_path
    copied = $copy.path
}} | ConvertTo-Json -Compress
"""
        )
        result = json.loads(output)
        self.assertEqual(str(self.checkpoint), result["path"])
        self.assertEqual("hip", result["scope"])
        self.assertEqual(self.session_id, result["session_id"])
        self.assertEqual(str(self.source_hip), result["source_hip"])
        copied = Path(result["copied"])
        self.assertEqual(self.session / "recovery", copied.parent)
        self.assertEqual(self.checkpoint.read_bytes(), copied.read_bytes())

    def test_rejects_path_escape_marker_tamper_and_old_session(self) -> None:
        cases = []
        escaped = dict(self.payload, checkpoint_file="../escape.hip")
        cases.append((escaped, escaped))
        mismatched_sidecar = dict(self.payload, goal_binding="d" * 64)
        cases.append((self.payload, mismatched_sidecar))
        old_session = dict(self.payload, launcher_session_id="e" * 32)
        cases.append((old_session, old_session))
        wrong_source = dict(
            self.payload,
            source_hip_path=str((self.root / "other.hip").resolve()),
        )
        cases.append((wrong_source, wrong_source))

        for pointer, sidecar in cases:
            with self.subTest(pointer=pointer, sidecar=sidecar):
                self.write_markers(pointer=pointer, sidecar=sidecar)
                output = self.run_powershell(
                    f"""
$candidate = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(self.session_checkpoints)}
$null -eq $candidate
"""
                )
                self.assertEqual("True", output)

    def test_copy_rejects_thread_goal_tamper_after_discovery(self) -> None:
        discovered = self.run_powershell(
            f"""
$candidate = Get-HiaLatestLauncherCheckpoint `
    -CheckpointDirectory {_ps_literal(self.session_checkpoints)} `
    -ThreadId 'thread-exact' `
    -GoalBinding '{GOAL_BINDING}'
$candidate.path
"""
        )
        self.assertEqual(str(self.checkpoint), discovered)
        tampered = dict(
            self.payload,
            thread_id="thread-other",
            goal_binding="d" * 64,
        )
        self.write_markers(pointer=tampered, sidecar=tampered)
        refused = self.run_powershell(
            f"""
try {{
    Copy-HiaLauncherRecoveryHip `
        -SessionRoot {_ps_literal(self.session)} `
        -SourcePath {_ps_literal(self.checkpoint)} `
        -Attempt 1 `
        -ThreadId 'thread-exact' `
        -GoalBinding '{GOAL_BINDING}' | Out-Null
    $false
}} catch {{
    $true
}}
"""
        )
        self.assertEqual("True", refused)

    def test_lifecycle_validates_selected_session_pointer_before_copy(self) -> None:
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        recovery_block = source[
            source.index("if ($RecoveryDecision -eq 'recover') {") :
            source.index("$sessionState = [ordered]@{")
        ]
        self.assertIn(
            "$validatedRecoveryCheckpoint = Get-HiaLatestLauncherCheckpoint",
            recovery_block,
        )
        self.assertIn(
            "Recovery checkpoint is not bound to the selected launcher session.",
            recovery_block,
        )
        self.assertNotIn(
            "-Path $RecoveryCheckpoint `\n        -Root $ResolvedRoot",
            recovery_block,
        )

    def test_repository_ignores_hip_local_control_directory(self) -> None:
        ignored = subprocess.run(
            ["git", "check-ignore", ".hia/checkpoints/example.hip"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, ignored.returncode, msg=ignored.stderr)


if __name__ == "__main__":
    unittest.main()
