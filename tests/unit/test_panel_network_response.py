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
    def test_empty_qt_network_error_keeps_specific_transport_details(self) -> None:
        payload = normalize_bridge_response(
            b"",
            qt_error_code=1,
            error_string="Connection refused",
            http_status=None,
            context="turn_start",
            method="POST",
            path="/v1/turn",
        )
        error = payload["structured_error"]
        transport = error["details"]["transport"]
        self.assertEqual("NETWORK_ERROR", error["code"])
        self.assertIn("Connection refused", error["message"])
        self.assertEqual("turn_start", transport["context"])
        self.assertEqual(1, transport["qt_error_code"])
        self.assertEqual("Connection refused", transport["qt_error_string"])
        self.assertIsNone(transport["http_status"])
        rendered = format_bridge_error(payload)
        self.assertIn("[NETWORK_ERROR]", rendered)
        self.assertIn("POST /v1/turn", rendered)
        self.assertIn("Qt 1: Connection refused", rendered)
        self.assertNotEqual("Bridge request failed", rendered)

    def test_empty_http_response_is_not_treated_as_an_empty_error_object(self) -> None:
        payload = normalize_bridge_response(
            b"",
            qt_error_code=0,
            error_string="",
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
            qt_error_code=299,
            error_string="Conflict",
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
        self.assertEqual(299, error["details"]["transport"]["qt_error_code"])

    def test_success_response_is_returned_without_transport_noise(self) -> None:
        expected = {"ok": True, "thread_id": "thread-1"}
        actual = normalize_bridge_response(
            json.dumps(expected).encode("utf-8"),
            qt_error_code=0,
            error_string="",
            http_status=200,
            context="session_start",
            method="POST",
            path="/v1/session",
        )
        self.assertEqual(expected, actual)

    def test_transport_diagnostics_redact_bearer_and_query_secrets(self) -> None:
        signature = inspect.signature(normalize_bridge_response)
        self.assertNotIn("token", signature.parameters)
        payload = normalize_bridge_response(
            b"",
            qt_error_code=5,
            error_string="Bearer super-secret was rejected",
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


if __name__ == "__main__":
    unittest.main()
