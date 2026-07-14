from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hia_core.codex_protocol import (  # noqa: E402
    CORE_APPROVAL_REQUESTS,
    CORE_CLIENT_NOTIFICATIONS,
    CORE_CLIENT_REQUESTS,
    CORE_SERVER_NOTIFICATIONS,
    REQUIRED_EXCLUSIONS,
    SCHEMA_DRAFT,
    SUPPORTED_CODEX_VERSION,
    ProtocolContractError,
    load_protocol_documents,
    validate_protocol_contract,
)


class CodexProtocolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory, cls.allowlist = load_protocol_documents(REPOSITORY_ROOT)
        cls.summary = validate_protocol_contract(REPOSITORY_ROOT)

    def allowed_methods(self, category: str) -> set[str]:
        return {
            entry["method"] for entry in self.allowlist["allowed"][category]
        }

    def test_offline_contract_validates(self) -> None:
        self.assertTrue(self.summary["ok"])
        self.assertEqual(SUPPORTED_CODEX_VERSION, self.summary["codex_cli_version"])
        self.assertEqual(SCHEMA_DRAFT, self.summary["schema_draft"])

    def test_generated_method_counts_are_frozen(self) -> None:
        self.assertEqual(
            {
                "client_requests": 87,
                "server_requests": 10,
                "server_notifications": 68,
                "client_notifications": 1,
            },
            self.summary["aggregate_method_counts"],
        )

    def test_response_schema_inventory_is_frozen(self) -> None:
        self.assertEqual(97, self.summary["response_schema_count"])
        self.assertEqual(97, len(self.inventory["response_schemas"]))

    def test_core_client_requests_are_exact(self) -> None:
        self.assertEqual(
            CORE_CLIENT_REQUESTS,
            self.allowed_methods("client_requests"),
        )

    def test_initialized_notification_is_exact(self) -> None:
        self.assertEqual(
            CORE_CLIENT_NOTIFICATIONS,
            self.allowed_methods("client_notifications"),
        )

    def test_approval_requests_are_exact(self) -> None:
        self.assertEqual(
            CORE_APPROVAL_REQUESTS,
            self.allowed_methods("server_requests"),
        )
        for entry in self.allowlist["allowed"]["server_requests"]:
            self.assertEqual("approval", entry["purpose"])
            self.assertTrue(entry["response_schema"].endswith("Response.json"))

    def test_stream_and_lifecycle_notifications_are_exact(self) -> None:
        self.assertEqual(
            CORE_SERVER_NOTIFICATIONS,
            self.allowed_methods("server_notifications"),
        )

    def test_policy_is_deny_by_default(self) -> None:
        self.assertEqual("deny-by-default", self.allowlist["policy"])

    def test_experimental_schema_and_methods_are_excluded(self) -> None:
        self.assertFalse(self.allowlist["schema_generation"]["experimental"])
        self.assertEqual(
            "--experimental",
            self.allowlist["schema_generation"]["forbidden_flag"],
        )
        excluded = set(
            self.allowlist["explicit_exclusions"]["experimental"]["methods"]
        )
        self.assertTrue(REQUIRED_EXCLUSIONS["experimental"].issubset(excluded))

    def test_dynamic_tools_are_excluded(self) -> None:
        excluded = set(
            self.allowlist["explicit_exclusions"]["dynamicTools"]["methods"]
        )
        self.assertEqual(REQUIRED_EXCLUSIONS["dynamicTools"], excluded)

    def test_process_api_is_excluded(self) -> None:
        excluded = set(
            self.allowlist["explicit_exclusions"]["processApi"]["methods"]
        )
        self.assertEqual(REQUIRED_EXCLUSIONS["processApi"], excluded)

    def test_thread_shell_command_is_excluded(self) -> None:
        excluded = set(
            self.allowlist["explicit_exclusions"]["threadShellCommand"]["methods"]
        )
        self.assertEqual(REQUIRED_EXCLUSIONS["threadShellCommand"], excluded)

    def test_websocket_is_excluded_and_stdio_is_only_transport(self) -> None:
        self.assertEqual(["stdio-jsonl"], self.allowlist["transport"]["allowed"])
        self.assertTrue(
            {"websocket", "ws", "wss"}.issubset(
                set(self.allowlist["transport"]["forbidden"])
            )
        )

    def test_upgrade_policy_blocks_breaking_core_changes(self) -> None:
        policy = self.allowlist["compatibility"]
        self.assertEqual("block-upgrade", policy["missing_allowlisted_method"])
        self.assertEqual(
            "block-upgrade-pending-review",
            policy["changed_allowlisted_schema"],
        )
        self.assertEqual("reject", policy["unknown_methods"])

    def test_contract_errors_are_json_serializable(self) -> None:
        error = ProtocolContractError("TEST", "test failure", "schema.json")
        encoded = json.dumps(error.to_dict())
        self.assertIn('"code": "TEST"', encoded)


if __name__ == "__main__":
    unittest.main()

