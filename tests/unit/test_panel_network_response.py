from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_panel.network_response import (  # noqa: E402
    format_bridge_error,
    normalize_bridge_response,
)


class PanelNetworkResponseTests(unittest.TestCase):
    def test_empty_network_error_keeps_specific_transport_details(self) -> None:
        payload = normalize_bridge_response(
            b"",
            error_kind="url_error",
            error_message="Connection refused",
            http_status=None,
            context="turn_start",
            method="POST",
            path="/v1/turn",
            request_id="request-1",
            generation=4,
        )
        error = payload["structured_error"]
        transport = error["details"]["transport"]
        self.assertEqual("NETWORK_ERROR", error["code"])
        self.assertIn("Connection refused", error["message"])
        self.assertEqual("turn_start", transport["context"])
        self.assertEqual("url_error", transport["error_kind"])
        self.assertEqual("Connection refused", transport["error_message"])
        self.assertIsNone(transport["http_status"])
        self.assertEqual("request-1", transport["request_id"])
        self.assertEqual(4, transport["generation"])
        rendered = format_bridge_error(payload)
        self.assertIn("[NETWORK_ERROR]", rendered)
        self.assertIn("POST /v1/turn", rendered)
        self.assertIn("url_error: Connection refused", rendered)
        self.assertNotEqual("Bridge request failed", rendered)

    def test_empty_http_response_is_not_treated_as_an_empty_error_object(self) -> None:
        payload = normalize_bridge_response(
            b"",
            http_status=200,
            context="session",
            method="GET",
            path="/v1/session",
        )
        self.assertEqual(
            "EMPTY_BRIDGE_RESPONSE",
            payload["structured_error"]["code"],
        )
        self.assertEqual(
            200,
            payload["structured_error"]["details"]["transport"]["http_status"],
        )

    def test_existing_bridge_error_is_preserved_and_transport_is_added(self) -> None:
        raw_error = {
            "ok": False,
            "structured_error": {
                "code": "TURN_ALREADY_ACTIVE",
                "message": "The selected Thread already has an active Turn",
                "details": {"thread_id": "thread-1"},
            },
        }
        payload = normalize_bridge_response(
            json.dumps(raw_error).encode("utf-8"),
            error_kind="http_error",
            error_message="Conflict",
            http_status=409,
            context="turn_start",
            method="POST",
            path="/v1/turn",
        )
        error = payload["structured_error"]
        self.assertEqual("TURN_ALREADY_ACTIVE", error["code"])
        self.assertEqual(raw_error["structured_error"]["message"], error["message"])
        self.assertEqual("thread-1", error["details"]["thread_id"])
        self.assertEqual(409, error["details"]["transport"]["http_status"])
        self.assertEqual(
            "http_error",
            error["details"]["transport"]["error_kind"],
        )

    def test_success_response_is_returned_without_transport_noise(self) -> None:
        expected = {"ok": True, "thread_id": "thread-1"}
        actual = normalize_bridge_response(
            json.dumps(expected).encode("utf-8"),
            http_status=200,
            context="session_start",
            method="POST",
            path="/v1/session",
        )
        self.assertEqual(expected, actual)

    def test_transport_diagnostics_redact_bearer_and_query_secrets(self) -> None:
        signature = inspect.signature(normalize_bridge_response)
        self.assertNotIn("token", signature.parameters)
        self.assertNotIn("qt_error_code", signature.parameters)
        self.assertNotIn("error_string", signature.parameters)
        payload = normalize_bridge_response(
            b"",
            error_kind="url_error",
            error_message="Bearer super-secret was rejected",
            http_status=401,
            context="health",
            method="GET",
            path="/v1/health?token=super-secret",
        )
        encoded = json.dumps(payload)
        rendered = format_bridge_error(payload)
        self.assertNotIn("super-secret", encoded)
        self.assertNotIn("super-secret", rendered)
        self.assertIn("<redacted>", encoded)

    def test_timeout_has_a_distinct_safe_error(self) -> None:
        payload = normalize_bridge_response(
            b"",
            error_kind="timeout",
            error_message="Bridge request timed out",
            http_status=None,
            context="events",
            method="GET",
            path="/v1/events?after=0&timeout=15",
        )
        self.assertEqual(
            "NETWORK_TIMEOUT",
            payload["structured_error"]["code"],
        )

    def test_oversized_response_is_not_parsed(self) -> None:
        payload = normalize_bridge_response(
            b"",
            error_kind="response_too_large",
            error_message="Bridge response exceeded the size limit",
            http_status=200,
            context="events",
            method="GET",
            path="/v1/events?after=0&timeout=15",
        )
        self.assertEqual(
            "INVALID_BRIDGE_RESPONSE",
            payload["structured_error"]["code"],
        )

    def test_malicious_or_ambiguous_json_is_rejected(self) -> None:
        malicious_inputs = (
            b"\xff\xfe",
            b"[]",
            b'{"ok":true,"ok":false}',
            b'{"ok":true,"value":NaN}',
        )
        for raw in malicious_inputs:
            with self.subTest(raw=raw):
                payload = normalize_bridge_response(
                    raw,
                    http_status=200,
                    context="health",
                    method="GET",
                    path="/v1/health",
                )
                self.assertEqual(
                    "INVALID_BRIDGE_RESPONSE",
                    payload["structured_error"]["code"],
                )

    def test_untrusted_success_strings_are_redacted_recursively(self) -> None:
        raw = json.dumps(
            {
                "ok": True,
                "nested": {
                    "message": "Bearer nested-secret",
                    "url": "/v1/health?token=query-secret",
                },
            }
        ).encode("utf-8")
        payload = normalize_bridge_response(
            raw,
            http_status=200,
            context="health",
            method="GET",
            path="/v1/health",
        )
        encoded = json.dumps(payload)
        self.assertNotIn("nested-secret", encoded)
        self.assertNotIn("query-secret", encoded)
        self.assertIn("<redacted>", encoded)


if __name__ == "__main__":
    unittest.main()
