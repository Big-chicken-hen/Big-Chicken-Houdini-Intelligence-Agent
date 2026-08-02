from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_evidence import (
    EvidenceValidationError,
    redact_credentials,
    validate_evidence,
)


THREAD_ID = "thread-execution"
TURN_ID = "turn-current"


def _event(
    item_id: str,
    *,
    thread_id: str = THREAD_ID,
    turn_id: str = TURN_ID,
    tool: str = "hia_validate",
    status: str = "completed",
    structured: dict | None = None,
    server: str = "hia_mcp_v2",
) -> dict:
    return {
        "type": "codex_notification",
        "method": "item/completed",
        "params": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {
                "id": item_id,
                "type": "mcpToolCall",
                "server": server,
                "tool": tool,
                "status": status,
                "arguments": {},
                "result": {
                    "content": [],
                    "structuredContent": structured
                    if structured is not None
                    else {"ok": True, "result": {"valid": True}},
                },
            },
        },
    }


def _validate(references, events, root: Path, **overrides):
    arguments = {
        "references": references,
        "tool_events": events,
        "execution_thread_id": THREAD_ID,
        "execution_turn_id": TURN_ID,
        "allowed_roots": [root],
        "existing_total_evidence_bytes": 0,
        "max_total_evidence_bytes": 1_000_000,
    }
    arguments.update(overrides)
    return validate_evidence(**arguments)


class ProjectEvidenceTests(unittest.TestCase):
    def test_accepts_real_successful_item_from_current_execution_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _validate(
                [{"item_id": "tool-real"}],
                [_event("tool-real")],
                Path(directory),
            )
        self.assertEqual(("tool-real",), tuple(item.item_id for item in result.evidence))
        self.assertGreater(result.added_evidence_bytes, 0)

    def test_rejects_fake_item_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvidenceValidationError, "unknown") as raised:
                _validate(
                    [{"item_id": "tool-fake"}],
                    [_event("tool-real")],
                    Path(directory),
                )
        self.assertEqual("UNKNOWN_TOOL_ITEM", raised.exception.code)

    def test_rejects_old_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EvidenceValidationError) as raised:
                _validate(
                    [{"item_id": "tool-old"}],
                    [_event("tool-old", turn_id="turn-old")],
                    Path(directory),
                )
        self.assertEqual("STALE_EXECUTION_TURN", raised.exception.code)

    def test_rejects_wrong_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EvidenceValidationError) as raised:
                _validate(
                    [{"item_id": "tool-wrong-thread"}],
                    [_event("tool-wrong-thread", thread_id="thread-review")],
                    Path(directory),
                )
        self.assertEqual("WRONG_EXECUTION_THREAD", raised.exception.code)

    def test_rejects_started_failed_and_unsuccessful_structured_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = _event("started")
            started["method"] = "item/started"
            cases = (
                ("started", [started], "UNKNOWN_TOOL_ITEM"),
                ("failed", [_event("failed", status="failed")], "TOOL_ITEM_NOT_SUCCESSFUL"),
                (
                    "not-ok",
                    [_event("not-ok", structured={"ok": False, "errors": []})],
                    "TOOL_ITEM_NOT_SUCCESSFUL",
                ),
            )
            for item_id, events, code in cases:
                with self.subTest(item_id=item_id):
                    with self.assertRaises(EvidenceValidationError) as raised:
                        _validate([{"item_id": item_id}], events, root)
                    self.assertEqual(code, raised.exception.code)

    def test_capture_requires_tool_frame_allowed_path_and_real_header(self) -> None:
        signatures = {
            "capture.png": b"\x89PNG\r\n\x1a\n" + b"x" * 20,
            "capture.jpg": b"\xff\xd8\xff\xe0" + b"x" * 20,
            "capture.webp": b"RIFF\x10\x00\x00\x00WEBP" + b"x" * 20,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (name, content) in enumerate(signatures.items(), 1):
                path = root / name
                path.write_bytes(content)
                frame = float(index)
                event = _event(
                    f"capture-{index}",
                    tool="hia_capture_viewport",
                    structured={
                        "ok": True,
                        "result": {
                            "absolute_path": str(path),
                            "requested_frame": frame,
                            "actual_frame": frame,
                            "quality_frame": frame,
                            "mode": "viewport",
                            "width": 640,
                            "height": 360,
                            "source_state": {
                                "camera": {"path": "/obj/review_cam"},
                                "viewport": {"name": "persp1"},
                            },
                        },
                    },
                )
                result = _validate(
                    [{"item_id": f"capture-{index}", "frame": frame}],
                    [event],
                    root,
                )
                self.assertEqual(str(path.resolve()), result.evidence[0].artifact_paths[0])
                self.assertEqual(frame, result.evidence[0].capture_frame)
                self.assertEqual(
                    {
                        "mode": "viewport",
                        "width": 640,
                        "height": 360,
                        "camera": {"path": "/obj/review_cam"},
                        "viewport": {"name": "persp1"},
                    },
                    result.evidence[0].capture_view,
                )

    def test_capture_rejects_wrong_tool_frame_path_and_fake_header(self) -> None:
        with tempfile.TemporaryDirectory() as allowed_directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(allowed_directory)
            good = root / "good.png"
            good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
            fake = root / "fake.png"
            fake.write_bytes(b"this is not an image")
            outside = Path(outside_directory) / "outside.png"
            outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)

            def capture(item_id: str, path: Path):
                return _event(
                    item_id,
                    tool="hia_capture_viewport",
                    structured={
                        "ok": True,
                        "result": {
                            "absolute_path": str(path),
                            "requested_frame": 12,
                            "actual_frame": 12,
                            "quality_frame": 12,
                        },
                    },
                )

            cases = (
                (
                    [{"item_id": "wrong-tool", "frame": 12}],
                    [_event("wrong-tool")],
                    "INVALID_EVIDENCE_REFERENCE",
                ),
                (
                    [{"item_id": "wrong-frame", "frame": 13}],
                    [capture("wrong-frame", good)],
                    "CAPTURE_FRAME_MISMATCH",
                ),
                (
                    [{"item_id": "outside", "frame": 12}],
                    [capture("outside", outside)],
                    "CAPTURE_PATH_NOT_ALLOWED",
                ),
                (
                    [{"item_id": "fake-header", "frame": 12}],
                    [capture("fake-header", fake)],
                    "INVALID_CAPTURE_FILE_TYPE",
                ),
            )
            for references, events, code in cases:
                with self.subTest(code=code):
                    with self.assertRaises(EvidenceValidationError) as raised:
                        _validate(references, events, root)
                    self.assertEqual(code, raised.exception.code)

    def test_credentials_are_redacted_recursively(self) -> None:
        value = {
            "authorization": "Bearer top-secret",
            "sessionToken": "camel-secret",
            "nested": [
                {"api_key": "sk-proj-super-secret-value"},
                "url=https://example.test/?token=query-secret",
                ("password=plain-secret",),
            ],
        }
        redacted = redact_credentials(value)
        encoded = json.dumps(redacted)
        for secret in (
            "top-secret",
            "super-secret-value",
            "query-secret",
            "plain-secret",
            "camel-secret",
        ):
            self.assertNotIn(secret, encoded)
        self.assertGreaterEqual(encoded.count("[REDACTED]"), 4)

    def test_validated_payload_is_redacted_before_return(self) -> None:
        event = _event("secret-tool")
        event["params"]["item"]["result"]["structuredContent"]["nested"] = {
            "refresh_token": "refresh-secret",
            "message": "Authorization: Bearer bearer-secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = _validate(
                [{"item_id": "secret-tool"}], [event], Path(directory)
            )
        encoded = json.dumps(result.evidence[0].payload)
        self.assertNotIn("refresh-secret", encoded)
        self.assertNotIn("bearer-secret", encoded)

    def test_total_evidence_byte_budget_includes_payload_and_unique_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "capture.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 256)
            event = _event(
                "capture-budget",
                tool="hia_capture_viewport",
                structured={
                    "ok": True,
                    "result": {
                        "absolute_path": str(image),
                        "requested_frame": 1,
                        "actual_frame": 1,
                        "quality_frame": 1,
                    },
                },
            )
            generous = _validate(
                [
                    {"item_id": "capture-budget", "frame": 1},
                    {"item_id": "capture-budget", "frame": 1},
                ],
                [event],
                root,
            )
            self.assertEqual(1, len(generous.evidence))
            self.assertGreaterEqual(generous.added_evidence_bytes, image.stat().st_size)
            with self.assertRaises(EvidenceValidationError) as raised:
                _validate(
                    [{"item_id": "capture-budget", "frame": 1}],
                    [event],
                    root,
                    existing_total_evidence_bytes=10,
                    max_total_evidence_bytes=generous.added_evidence_bytes,
                )
            self.assertEqual("EVIDENCE_BYTE_BUDGET_EXCEEDED", raised.exception.code)

    def test_duplicate_completed_item_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EvidenceValidationError) as raised:
                _validate(
                    [{"item_id": "duplicate"}],
                    [_event("duplicate"), _event("duplicate")],
                    Path(directory),
                )
        self.assertEqual("DUPLICATE_TOOL_ITEM_ID", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
