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
    P1_PASSIVE_SERVER_NOTIFICATIONS,
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

    def test_turn_steer_is_a_stable_core_request(self) -> None:
        entries = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["client_requests"]
        }
        self.assertEqual(
            {
                "method": "turn/steer",
                "params_definition": "TurnSteerParams",
                "response_schema": "v2/TurnSteerResponse.json",
            },
            entries["turn/steer"],
        )

        inventoried = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["client_requests"]["methods"]
        }["turn/steer"]
        self.assertEqual("TurnSteerParams", inventoried["params_definition"])
        self.assertFalse(inventoried["declared_experimental"])

        schema_root = REPOSITORY_ROOT / "schemas" / "codex-app-server" / "0.144.3"
        params = json.loads(
            (schema_root / "v2" / "TurnSteerParams.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"threadId", "expectedTurnId", "input"},
            set(params["required"]),
        )
        input_variants = params["definitions"]["UserInput"]["oneOf"]
        self.assertTrue(
            any(
                variant["properties"]["type"].get("enum") == ["text"]
                and "text_elements" in variant["properties"]
                for variant in input_variants
            )
        )
        self.assertTrue(
            any(
                variant["properties"]["type"].get("enum") == ["localImage"]
                and "path" in variant["properties"]
                for variant in input_variants
            )
        )

        response = json.loads(
            (schema_root / "v2" / "TurnSteerResponse.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(["turnId"], response["required"])

    def test_account_read_p1_extension_is_allowlisted(self) -> None:
        entries = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["client_requests"]
        }
        self.assertEqual(
            "v2/GetAccountResponse.json",
            entries["account/read"]["response_schema"],
        )

    def test_model_list_p1_extension_is_stable_and_read_only(self) -> None:
        entries = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["client_requests"]
        }
        model_list = entries["model/list"]
        self.assertEqual("ModelListParams", model_list["params_definition"])
        self.assertEqual(
            "v2/ModelListResponse.json",
            model_list["response_schema"],
        )
        self.assertEqual(
            "p1-read-only-model-catalog",
            model_list["purpose"],
        )

        inventoried = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["client_requests"]["methods"]
        }["model/list"]
        self.assertEqual("ModelListParams", inventoried["params_definition"])
        self.assertFalse(inventoried["declared_experimental"])
        for category in (
            "server_requests",
            "server_notifications",
            "client_notifications",
        ):
            self.assertNotIn("model/list", self.allowed_methods(category))

    def test_thread_history_methods_are_stable_and_strictly_directed(self) -> None:
        client_entries = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["client_requests"]
        }
        expected_requests = {
            "thread/list": (
                "ThreadListParams",
                "v2/ThreadListResponse.json",
            ),
            "thread/name/set": (
                "ThreadSetNameParams",
                "v2/ThreadSetNameResponse.json",
            ),
        }
        inventoried_requests = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["client_requests"]["methods"]
        }
        for method, (params_definition, response_schema) in expected_requests.items():
            with self.subTest(method=method):
                self.assertEqual(
                    params_definition,
                    client_entries[method]["params_definition"],
                )
                self.assertEqual(
                    response_schema,
                    client_entries[method]["response_schema"],
                )
                self.assertEqual("thread-history", client_entries[method]["purpose"])
                self.assertEqual(
                    params_definition,
                    inventoried_requests[method]["params_definition"],
                )
                self.assertFalse(
                    inventoried_requests[method]["declared_experimental"]
                )

        notification = "thread/name/updated"
        notification_entries = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["server_notifications"]
        }
        inventoried_notifications = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["server_notifications"]["methods"]
        }
        self.assertEqual(
            "ThreadNameUpdatedNotification",
            notification_entries[notification]["params_definition"],
        )
        self.assertEqual(
            "thread-history",
            notification_entries[notification]["purpose"],
        )
        self.assertEqual(
            "ThreadNameUpdatedNotification",
            inventoried_notifications[notification]["params_definition"],
        )
        self.assertFalse(
            inventoried_notifications[notification]["declared_experimental"]
        )

        for method in expected_requests:
            for category in (
                "server_requests",
                "server_notifications",
                "client_notifications",
            ):
                with self.subTest(method=method, category=category):
                    self.assertNotIn(method, self.allowed_methods(category))
        for category in (
            "client_requests",
            "server_requests",
            "client_notifications",
        ):
            with self.subTest(method=notification, category=category):
                self.assertNotIn(notification, self.allowed_methods(category))

    def test_native_thread_goal_methods_and_notifications_are_exact(self) -> None:
        requests = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["client_requests"]
        }
        expected_requests = {
            "thread/goal/get": (
                "ThreadGoalGetParams",
                "v2/ThreadGoalGetResponse.json",
            ),
            "thread/goal/set": (
                "ThreadGoalSetParams",
                "v2/ThreadGoalSetResponse.json",
            ),
            "thread/goal/clear": (
                "ThreadGoalClearParams",
                "v2/ThreadGoalClearResponse.json",
            ),
        }
        inventory_requests = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["client_requests"]["methods"]
        }
        for method, (params_definition, response_schema) in expected_requests.items():
            self.assertEqual(params_definition, requests[method]["params_definition"])
            self.assertEqual(response_schema, requests[method]["response_schema"])
            self.assertEqual("thread-goal", requests[method]["purpose"])
            self.assertFalse(inventory_requests[method]["declared_experimental"])

        notifications = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["server_notifications"]
        }
        expected_notifications = {
            "thread/goal/updated": "ThreadGoalUpdatedNotification",
            "thread/goal/cleared": "ThreadGoalClearedNotification",
        }
        inventory_notifications = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["server_notifications"]["methods"]
        }
        for method, params_definition in expected_notifications.items():
            self.assertEqual(
                params_definition, notifications[method]["params_definition"]
            )
            self.assertEqual("thread-goal", notifications[method]["purpose"])
            self.assertFalse(inventory_notifications[method]["declared_experimental"])

        schema_root = REPOSITORY_ROOT / "schemas" / "codex-app-server" / "0.144.3"
        set_params = json.loads(
            (schema_root / "v2" / "ThreadGoalSetParams.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(["threadId"], set_params["required"])
        self.assertEqual(
            {
                "active",
                "paused",
                "blocked",
                "usageLimited",
                "budgetLimited",
                "complete",
            },
            set(set_params["definitions"]["ThreadGoalStatus"]["enum"]),
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

    def test_automatic_compaction_is_receive_only(self) -> None:
        self.assertIn(
            "thread/compacted",
            self.allowed_methods("server_notifications"),
        )
        self.assertNotIn(
            "thread/compact/start",
            self.allowed_methods("client_requests"),
        )

    def test_request_user_input_remains_experimentally_excluded(self) -> None:
        method = "item/tool/requestUserInput"
        excluded = set(
            self.allowlist["explicit_exclusions"]["experimental"]["methods"]
        )
        inventoried = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["server_requests"]["methods"]
        }
        self.assertTrue(inventoried[method]["declared_experimental"])
        self.assertIn(method, REQUIRED_EXCLUSIONS["experimental"])
        self.assertIn(method, excluded)
        for category in (
            "client_requests",
            "server_requests",
            "server_notifications",
            "client_notifications",
        ):
            with self.subTest(category=category):
                self.assertNotIn(method, self.allowed_methods(category))

    def test_p1_passive_notifications_are_stable_and_receive_only(self) -> None:
        expected_definitions = {
            "account/rateLimits/updated": "AccountRateLimitsUpdatedNotification",
            "mcpServer/startupStatus/updated": "McpServerStatusUpdatedNotification",
            "remoteControl/status/changed": (
                "RemoteControlStatusChangedNotification"
            ),
            "skills/changed": "SkillsChangedNotification",
        }
        self.assertEqual(
            set(expected_definitions),
            set(P1_PASSIVE_SERVER_NOTIFICATIONS),
        )

        allowlisted = {
            entry["method"]: entry
            for entry in self.allowlist["allowed"]["server_notifications"]
        }
        inventoried = {
            entry["method"]: entry
            for entry in self.inventory["aggregates"]["server_notifications"]["methods"]
        }
        for method, params_definition in expected_definitions.items():
            self.assertEqual(
                params_definition,
                allowlisted[method]["params_definition"],
            )
            self.assertEqual(
                "passive-observation-only",
                allowlisted[method]["purpose"],
            )
            self.assertEqual(
                params_definition,
                inventoried[method]["params_definition"],
            )
            self.assertFalse(inventoried[method]["declared_experimental"])

        for category in (
            "client_requests",
            "server_requests",
            "client_notifications",
        ):
            self.assertTrue(
                P1_PASSIVE_SERVER_NOTIFICATIONS.isdisjoint(
                    self.allowed_methods(category)
                )
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
