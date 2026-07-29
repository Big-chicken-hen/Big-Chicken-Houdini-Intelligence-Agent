from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(
    0,
    str(REPOSITORY_ROOT / "houdini_package" / "python_libs"),
)

from hia_mcp_runtime.deterministic_sources import (  # noqa: E402
    SourceAdapterError,
    VERIFICATION,
    normalize_project_memory,
    normalize_selected_file,
    normalize_thread_export,
)


class HiaMcpV2DeterministicSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="hia-deterministic-sources-",
            dir=REPOSITORY_ROOT,
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_raw_text_and_html_are_normalized_without_codex(self) -> None:
        plain = self.write_text(
            "notes.md",
            "# Velocity workflow\n\nUse vorticity confinement.\n"
            "Bearer visible-token\napi_key=visible-key",
        )
        first = normalize_selected_file(plain, source_id="managed/notes.md")
        second = normalize_selected_file(plain, source_id="managed/notes.md")

        self.assertEqual("ready", first.status)
        self.assertEqual("user_document", first.records[0].source_kind)
        self.assertEqual(VERIFICATION, first.records[0].verification)
        self.assertIn("Velocity workflow", first.records[0].text)
        self.assertNotIn("visible-token", first.records[0].text)
        self.assertNotIn("visible-key", first.records[0].text)
        self.assertEqual(
            first.records[0].content_hash,
            second.records[0].content_hash,
        )
        self.assertEqual(
            first.records[0].source_key,
            second.records[0].source_key,
        )

        html = self.write_text(
            "guide.html",
            "<h1>Pyro</h1><script>do_not_index()</script>"
            "<p>Cache the final fields.</p>",
        )
        result = normalize_selected_file(html)
        self.assertEqual("ready", result.status)
        self.assertIn("Pyro", result.records[0].text)
        self.assertIn("Cache the final fields.", result.records[0].text)
        self.assertNotIn("do_not_index", result.records[0].text)

    def test_caption_file_is_a_user_transcript(self) -> None:
        captions = self.write_text(
            "lesson.vtt",
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
            "<v Speaker>Build a Vellum constraint.</v>\n\n"
            "00:00:02.000 --> 00:00:04.000\nCache the simulation.\n",
        )
        result = normalize_selected_file(captions)

        self.assertEqual("ready", result.status)
        record = result.records[0]
        self.assertEqual("user_transcript", record.source_kind)
        self.assertIn("Build a Vellum constraint.", record.text)
        self.assertIn("Cache the simulation.", record.text)
        self.assertNotIn("-->", record.text)

    def test_media_uses_existing_sidecar_or_requires_transcript(self) -> None:
        video = self.root / "tutorial.mp4"
        video.write_bytes(b"fixture-video")
        self.write_text(
            "tutorial.srt",
            "1\n00:00:00,000 --> 00:00:01,000\n"
            "Create the source volume.\n",
        )

        ready = normalize_selected_file(video, source_id="video/tutorial")
        self.assertEqual("ready", ready.status)
        self.assertEqual("user_transcript", ready.records[0].source_kind)
        self.assertEqual(
            "existing_sidecar",
            ready.records[0].provenance["extraction"],
        )
        self.assertIn("Create the source volume.", ready.records[0].text)

        no_transcript = self.root / "silent.mov"
        no_transcript.write_bytes(b"fixture-video-without-transcript")
        missing = normalize_selected_file(no_transcript)
        self.assertEqual("transcript_required", missing.status)
        self.assertEqual((), missing.records)
        self.assertEqual(
            "tool_unavailable",
            missing.details["embedded_probe_status"],
        )
        self.assertIn("No deterministic", missing.warnings[0])

    def test_thread_export_keeps_only_selected_public_final_text(self) -> None:
        messages = [
            {
                "id": "m1",
                "turn_id": "turn-1",
                "role": "user",
                "type": "message",
                "timestamp": "2026-07-28T01:00:00Z",
                "content": "Remember the cache path. password=hunter2",
            },
            {
                "id": "m2",
                "turn_id": "turn-1",
                "role": "assistant",
                "type": "reasoning",
                "is_final": True,
                "content": "private chain of thought",
            },
            {
                "id": "m3",
                "turn_id": "turn-1",
                "role": "tool",
                "type": "tool_result",
                "content": "raw tool output",
            },
            {
                "id": "m4",
                "turn_id": "turn-1",
                "role": "assistant",
                "phase": "final",
                "timestamp": "2026-07-28T01:01:00Z",
                "content": [
                    {"type": "output_text", "text": "Use a File Cache SOP."},
                    {"type": "tool_result", "text": "excluded tool payload"},
                ],
            },
            {
                "id": "m5",
                "role": "assistant",
                "channel": "commentary",
                "content": "intermediate update",
            },
            {
                "id": "m6",
                "role": "user",
                "visibility": "internal",
                "content": "internal event",
            },
            {
                "id": "m7",
                "turn_id": "turn-2",
                "role": "assistant",
                "phase": "final_answer",
                "content": "FinalAnswerNeedle is publicly visible.",
            },
        ]

        result = normalize_thread_export(
            "thread-123",
            messages,
            explicitly_selected=True,
        )

        self.assertEqual("ready", result.status)
        self.assertEqual(3, len(result.records))
        combined = "\n".join(record.text for record in result.records)
        self.assertIn("Remember the cache path.", combined)
        self.assertIn("Use a File Cache SOP.", combined)
        self.assertIn("FinalAnswerNeedle is publicly visible.", combined)
        self.assertNotIn("hunter2", combined)
        self.assertNotIn("chain of thought", combined)
        self.assertNotIn("tool payload", combined)
        self.assertNotIn("intermediate update", combined)
        self.assertEqual(
            {"user", "assistant"},
            {
                record.provenance["message_role"]
                for record in result.records
            },
        )
        self.assertTrue(
            all(
                record.provenance["thread_id"] == "thread-123"
                and record.verification == VERIFICATION
                for record in result.records
            )
        )
        self.assertEqual(
            {"turn-1", "turn-2"},
            {record.provenance["turn_id"] for record in result.records},
        )
        edited = normalize_thread_export(
            "thread-123",
            [
                {
                    **messages[0],
                    "timestamp": "2026-07-28T02:00:00Z",
                    "content": "Remember the revised cache path.",
                }
            ],
            explicitly_selected=True,
        )
        self.assertEqual(
            result.records[0].source_key,
            edited.records[0].source_key,
        )
        self.assertNotEqual(
            result.records[0].content_hash,
            edited.records[0].content_hash,
        )

    def test_thread_export_requires_explicit_selection(self) -> None:
        with self.assertRaises(SourceAdapterError):
            normalize_thread_export(
                "thread-123",
                [{"role": "user", "content": "visible"}],
                explicitly_selected=False,
            )

        empty = normalize_thread_export(
            "thread-123",
            [
                {
                    "role": "assistant",
                    "type": "reasoning",
                    "content": "not public final text",
                }
            ],
            explicitly_selected=True,
        )
        self.assertEqual("no_public_text", empty.status)
        self.assertEqual((), empty.records)

    def test_active_memory_preserves_original_and_inactive_removes_key(self) -> None:
        active = normalize_project_memory(
            {
                "stable_id": "mem_0123456789abcdef0123456789abcdef",
                "memory_type": "decision",
                "title": "Cache strategy",
                "body": "Write checkpoints only at explicit goal stages.",
                "tags": ["cache", "workflow"],
                "scope": "project",
                "status": "active",
                "summary": "Optional Codex summary.",
                "source_thread_id": "thread-123",
                "source_turn_id": "turn-7",
            }
        )

        self.assertEqual("ready", active.status)
        record = active.records[0]
        self.assertEqual("project_memory", record.source_kind)
        self.assertIn("Cache strategy", record.text)
        self.assertIn("Write checkpoints only", record.text)
        self.assertIn("Tags: cache, workflow", record.text)
        self.assertNotIn("Optional Codex summary.", record.text)
        self.assertEqual(
            "Optional Codex summary.",
            record.metadata["summary"],
        )
        self.assertEqual("thread-123", record.provenance["source_thread_id"])
        self.assertEqual("turn-7", record.provenance["source_turn_id"])

        for status in ("superseded", "deleted"):
            with self.subTest(status=status):
                inactive = normalize_project_memory(
                    {
                        "stable_id": "mem_0123456789abcdef0123456789abcdef",
                        "status": status,
                    }
                )
                self.assertEqual("inactive", inactive.status)
                self.assertEqual((), inactive.records)
                self.assertEqual(
                    (record.source_key,),
                    inactive.remove_source_keys,
                )

    def test_optional_pdf_extraction_uses_installed_reader_only(self) -> None:
        pdf = self.root / "notes.pdf"
        pdf.write_bytes(b"%PDF fixture")

        class FakePage:
            @staticmethod
            def extract_text() -> str:
                return "Deterministic PDF body"

        class FakeReader:
            def __init__(self, path: str) -> None:
                self.path = path
                self.pages = [FakePage()]

        fake_module = types.SimpleNamespace(PdfReader=FakeReader)
        with mock.patch.dict(sys.modules, {"pypdf": fake_module}):
            result = normalize_selected_file(pdf)

        self.assertEqual("ready", result.status)
        self.assertEqual("user_document", result.records[0].source_kind)
        self.assertEqual("Deterministic PDF body", result.records[0].text)

    def test_unsupported_source_is_reported_without_side_effects(self) -> None:
        source = self.root / "archive.bin"
        source.write_bytes(b"not a text source")
        before = sorted(path.name for path in self.root.iterdir())
        result = normalize_selected_file(source)
        after = sorted(path.name for path in self.root.iterdir())

        self.assertEqual("unsupported", result.status)
        self.assertEqual((), result.records)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
