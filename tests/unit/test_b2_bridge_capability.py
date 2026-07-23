from __future__ import annotations

import copy
import hashlib
import hmac
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.http_server import (  # noqa: E402
    SCENE_EXECUTOR_HEADER,
    BridgeApplication,
    BridgeRequestHandler,
)
from hia_bridge.scene_queue import (  # noqa: E402
    B2_READ_ONLY_PROFILE,
    B2_READ_ONLY_TOOLS,
    FakeCapabilityAttestation,
    SceneQueue,
    SceneQueueError,
    _request_digest,
    normalize_live_capability_report,
)
from hia_core.houdini_contract import SchemaRegistry  # noqa: E402


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Session:
    def snapshot(self) -> dict[str, object]:
        return {}


def _catalog() -> list[dict[str, object]]:
    values = (
        ("Object", "geo", 0, 1),
        ("Sop", "box", 0, 1),
        ("Sop", "transform", 1, 1),
        ("Sop", "merge", 65535, 1),
        ("Sop", "null", 1, 1),
    )
    return [
        {
            "context": context,
            "requested_name": name,
            "resolved_name": "xform" if name == "transform" else name,
            "available": True,
            "creatable": False,
            "schema_source": "live_houdini_instance",
            "parameters": [],
            "input_count": inputs,
            "output_count": outputs,
        }
        for context, name, inputs, outputs in values
    ]


def _report(
    *,
    sequence: int = 1,
    session: str = "hip-session-a",
    revision: int = 7,
    available: bool = True,
    process_nonce: str = "process-nonce-a",
    publisher_suffix: str = "a" * 16,
    houdini_build: str = "21.0.440",
) -> dict[str, object]:
    publisher_id = (
        "panel-"
        + hashlib.sha256(process_nonce.encode("utf-8")).hexdigest()[:16]
        + "-"
        + publisher_suffix
    )
    report: dict[str, object] = {
        "available": available,
        "publisher_id": publisher_id,
        "observer_sequence": sequence,
        "houdini_build": houdini_build,
        "python_version": "3.11.9",
        "pyside_version": "6.8.2",
        "hip_session_id": session,
        "hip_fingerprint": "0" * 64,
        "scene_revision": revision,
        "session_observer_reliable": available,
        "revision_observer_reliable": available,
        "catalog": _catalog(),
    }
    key = hashlib.sha256(
        b"hia-b2-fingerprint\0" + process_nonce.encode("utf-8")
    ).digest()
    payload = "\x1f".join(
        (
            "hia-b2-safe-hip-fingerprint-v1",
            publisher_id,
            str(report["houdini_build"]),
            session,
            str(revision),
        )
    ).encode("utf-8")
    report["hip_fingerprint"] = hmac.new(
        key, payload, hashlib.sha256
    ).hexdigest()
    return report


class B2BridgeCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.registry = SchemaRegistry.b2_read_only()
        self.queue = SceneQueue(
            "launch-b2",
            4,
            expected_schema_digest=self.registry.manifest_digest,
            expected_catalog_digest=None,
            profile=B2_READ_ONLY_PROFILE,
            expected_process_nonce="process-nonce-a",
            live_capability_lease_seconds=0.5,
            clock=self.clock,
        )

    @staticmethod
    def _scene_arguments(
        *,
        session: str = "hip-session-a",
        revision: int = 7,
        request_id: str = "request-a",
    ) -> dict[str, object]:
        return {
            "request_id": request_id,
            "thread_id": "thread-a",
            "turn_id": "turn-a",
            "hip_session_id": session,
            "base_scene_revision": revision,
            "idempotency_key": "idempotency-key-a",
            "deadline_ms": 1000,
            "permission_level": "scene_read",
            "include_graph_summaries": False,
        }

    def assert_queue_error(self, code: str, callback: object) -> SceneQueueError:
        with self.assertRaises(SceneQueueError) as caught:
            callback()  # type: ignore[operator]
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_profile_is_exactly_two_read_tools_and_b1_fake_is_rejected(self) -> None:
        self.assertEqual(B2_READ_ONLY_TOOLS, self.queue.allowed_tools)
        for tool_name in (
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        ):
            self.assert_queue_error(
                "TOOL_NOT_ALLOWED",
                lambda name=tool_name: self.queue.build_request(
                    name, {}, self.clock() + 1
                ),
            )
        fake = FakeCapabilityAttestation(
            launch_id="launch-b2",
            generation=4,
            process_nonce="process-nonce-a",
            hip_session_id="hip-session-a",
            hip_fingerprint="a" * 64,
            scene_revision=7,
            catalog_digest="b" * 64,
            schema_digest=self.registry.manifest_digest,
        )
        self.assert_queue_error(
            "CAPABILITY_MISMATCH", lambda: self.queue.install_attestation(fake)
        )
        self.assert_queue_error(
            "TOOL_NOT_ALLOWED",
            lambda: self.queue.decide_approval(
                "request-a", "allow", "b" * 64, "launch-b2", 4
            ),
        )

    def test_bridge_injects_identity_and_recomputes_catalog_digest(self) -> None:
        report = _report()
        normalized, _, expected_catalog_digest = normalize_live_capability_report(
            report
        )
        attestation = self.queue.publish_live_capability(report)
        self.assertIsNotNone(attestation)
        assert attestation is not None
        self.assertEqual("b2_read_only", attestation.profile)
        self.assertEqual("launch-b2", attestation.launch_id)
        self.assertEqual(4, attestation.generation)
        self.assertEqual("process-nonce-a", attestation.process_nonce)
        self.assertEqual(self.registry.manifest_digest, attestation.schema_digest)
        self.assertEqual(expected_catalog_digest, attestation.catalog_digest)
        self.assertEqual(normalized["observer_sequence"], attestation.observer_sequence)
        for forbidden in (
            "launch_id",
            "generation",
            "process_nonce",
            "schema_digest",
            "catalog_digest",
            "attestation_digest",
        ):
            self.assertNotIn(forbidden, report)

        self.clock.value += 0.25
        renewed = self.queue.publish_live_capability(copy.deepcopy(report))
        self.assertEqual(attestation.digest, renewed.digest if renewed else None)
        self.assertGreater(self.queue.capability_lease_expires_at or 0, self.clock())

        changed_same_sequence = copy.deepcopy(report)
        changed_same_sequence["scene_revision"] = 8
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(changed_same_sequence),
        )
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(copy.deepcopy(report)),
        )
        self.assertIsNotNone(
            self.queue.publish_live_capability(_report(sequence=2, revision=8))
        )

    def test_process_nonce_binds_publisher_and_hip_fingerprint(self) -> None:
        first = self.queue.publish_live_capability(_report())
        self.assertIsNotNone(first)

        wrong_publisher = _report(sequence=2, publisher_suffix="b" * 16)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(wrong_publisher),
        )
        self.assertIsNotNone(self.queue.current_attestation_digest)

        wrong_fingerprint = _report(sequence=2, revision=8)
        wrong_fingerprint["hip_fingerprint"] = "f" * 64
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(wrong_fingerprint),
        )
        self.assertIsNone(self.queue.current_attestation_digest)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(_report(sequence=1)),
        )
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(_report(sequence=2, revision=8)),
        )
        self.assertIsNotNone(
            self.queue.publish_live_capability(_report(sequence=3, revision=8))
        )

    def test_b2_request_digest_is_explicitly_versioned_0_2_0(self) -> None:
        attestation = self.queue.publish_live_capability(_report())
        assert attestation is not None
        arguments = self._scene_arguments()
        request = self.queue.build_request(
            "houdini_scene_info", arguments, self.clock() + 1
        )

        expected = _request_digest(
            request.tool_name,
            request.arguments,
            request.absolute_deadline,
            request.launch_id,
            request.generation,
            request.attestation_digest,
            contract_version="0.2.0",
        )
        legacy = _request_digest(
            request.tool_name,
            request.arguments,
            request.absolute_deadline,
            request.launch_id,
            request.generation,
            request.attestation_digest,
            contract_version="0.1.0",
        )
        self.assertEqual("0.2.0", self.queue.contract_version)
        self.assertEqual(expected, request.request_digest)
        self.assertNotEqual(legacy, request.request_digest)

    def test_catalog_and_build_drift_revoke_the_prior_attestation(self) -> None:
        first = self.queue.publish_live_capability(_report())
        self.assertIsNotNone(first)

        catalog_drift = _report(sequence=2)
        catalog_drift["catalog"][1]["output_count"] = 2
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(catalog_drift),
        )
        self.assertIsNone(self.queue.current_attestation_digest)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(_report(sequence=1)),
        )

        restored = self.queue.publish_live_capability(_report(sequence=3))
        self.assertIsNotNone(restored)
        build_drift = _report(sequence=4, houdini_build="21.0.999")
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(build_drift),
        )
        self.assertIsNone(self.queue.current_attestation_digest)

    def test_newer_malformed_bound_report_revokes_but_wrong_publisher_cannot(self) -> None:
        first = self.queue.publish_live_capability(_report())
        assert first is not None
        request = self.queue.build_request(
            "houdini_scene_info", self._scene_arguments(), self.clock() + 1
        )
        self.queue.submit(request)

        wrong_publisher = _report(sequence=2, publisher_suffix="b" * 16)
        wrong_publisher["catalog"] = wrong_publisher["catalog"][:-1]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(wrong_publisher),
        )
        self.assertEqual(first.digest, self.queue.current_attestation_digest)

        malformed = _report(sequence=2)
        malformed["catalog"] = malformed["catalog"][:-1]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(malformed),
        )
        self.assertIsNone(self.queue.current_attestation_digest)
        terminal = self.queue.get_result("request-a")
        self.assertTrue(terminal.terminal)
        self.assertEqual(
            "CAPABILITY_MISMATCH", terminal.structured_error["code"]
        )

        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(_report(sequence=2)),
        )
        recovered = self.queue.publish_live_capability(_report(sequence=3))
        self.assertIsNotNone(recovered)

        self.clock.value += 0.51
        expired_malformed = _report(sequence=4)
        expired_malformed["catalog"] = expired_malformed["catalog"][:-1]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(expired_malformed),
        )
        takeover = self.queue.publish_live_capability(
            _report(
                sequence=1,
                session="hip-session-takeover",
                revision=0,
                publisher_suffix="b" * 16,
            )
        )
        self.assertIsNotNone(takeover)

    def test_authenticated_status_discovers_first_read_context_without_secrets(self) -> None:
        self.assert_queue_error(
            "HOUDINI_UNAVAILABLE", self.queue.live_capability_status
        )
        attestation = self.queue.publish_live_capability(_report())
        assert attestation is not None
        expires_at = self.queue.capability_lease_expires_at

        status = self.queue.live_capability_status()
        self.assertEqual(
            {
                "available",
                "profile",
                "schema_version",
                "schema_digest",
                "launch_id",
                "generation",
                "attestation_digest",
                "houdini_build",
                "hip_session_id",
                "hip_fingerprint",
                "scene_revision",
                "catalog_digest",
                "enabled_tools",
                "allowed_node_types",
            },
            set(status),
        )
        self.assertTrue(status["available"])
        self.assertEqual("hip-session-a", status["hip_session_id"])
        self.assertEqual(7, status["scene_revision"])
        self.assertEqual(
            ["houdini_scene_info", "houdini_node_type_info"],
            status["enabled_tools"],
        )
        self.assertEqual(
            [
                ("Object", "geo"),
                ("Sop", "box"),
                ("Sop", "transform"),
                ("Sop", "merge"),
                ("Sop", "null"),
            ],
            [
                (item["context"], item["requested_name"])
                for item in status["allowed_node_types"]
            ],
        )
        self.assertEqual("xform", status["allowed_node_types"][2]["resolved_name"])
        encoded = repr(status).casefold()
        for forbidden in (
            "process_nonce",
            "publisher_id",
            "observer_sequence",
            "executor_token",
            "parameters",
        ):
            self.assertNotIn(forbidden, encoded)

        self.clock.value += 0.25
        self.queue.live_capability_status()
        self.assertEqual(expires_at, self.queue.capability_lease_expires_at)
        self.clock.value += 0.26
        self.assert_queue_error(
            "HOUDINI_UNAVAILABLE", self.queue.live_capability_status
        )

    def test_missing_and_expired_lease_fail_houdini_unavailable(self) -> None:
        arguments = self._scene_arguments()
        self.assert_queue_error(
            "HOUDINI_UNAVAILABLE",
            lambda: self.queue.build_request(
                "houdini_scene_info", arguments, self.clock() + 1
            ),
        )

    def test_expired_panel_lease_allows_fresh_observer_session(self) -> None:
        old_report = _report()
        first = self.queue.publish_live_capability(old_report)
        self.assertIsNotNone(first)
        self.clock.value += 0.51
        self.assertIsNone(self.queue.current_attestation_digest)

        reopened = _report(
            sequence=1,
            session="hip-session-reopened",
            revision=0,
            publisher_suffix="b" * 16,
        )
        second = self.queue.publish_live_capability(reopened)
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual("hip-session-reopened", second.hip_session_id)
        self.assertEqual(1, second.observer_sequence)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(old_report),
        )
        arguments = self._scene_arguments(
            session="hip-session-reopened", revision=0
        )
        request = self.queue.build_request(
            "houdini_scene_info", arguments, self.clock() + 1
        )
        self.queue.submit(request)
        self.clock.value += 0.51
        self.assertIsNone(self.queue.current_attestation_digest)
        snapshot = self.queue.get_result("request-a")
        self.assertTrue(snapshot.terminal)
        self.assertEqual(
            "HOUDINI_UNAVAILABLE", snapshot.structured_error["code"]
        )
        self.assert_queue_error(
            "HOUDINI_UNAVAILABLE",
            lambda: self.queue.build_request(
                "houdini_scene_info", arguments, self.clock() + 1
            ),
        )

    def test_monotonic_revision_and_hip_replacement_invalidate_old_work(self) -> None:
        first = self.queue.publish_live_capability(_report())
        assert first is not None
        old_request = self.queue.build_request(
            "houdini_scene_info", self._scene_arguments(), self.clock() + 3
        )
        self.queue.submit(old_request)

        advanced = _report(sequence=2, revision=8)
        second = self.queue.publish_live_capability(advanced)
        assert second is not None
        self.assertNotEqual(first.digest, second.digest)
        old_snapshot = self.queue.get_result("request-a")
        self.assertEqual("CAPABILITY_MISMATCH", old_snapshot.structured_error["code"])

        regressed = _report(sequence=3, revision=7)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(regressed),
        )

        replacement = _report(sequence=4, session="hip-session-b", revision=0)
        third = self.queue.publish_live_capability(replacement)
        assert third is not None
        self.assertEqual("hip-session-b", third.hip_session_id)
        self.assertEqual(0, third.scene_revision)

        stale = _report(sequence=3)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(stale),
        )

        aba = _report(sequence=5, session="hip-session-a", revision=0)
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(aba),
        )
        self.assertIsNone(self.queue.current_attestation_digest)

        catalog_drift = _report(sequence=6, session="hip-session-c", revision=0)
        catalog_drift["catalog"][1]["output_count"] = 2
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(catalog_drift),
        )
        self.assertIsNone(self.queue.current_attestation_digest)

        build_drift = _report(
            sequence=7,
            session="hip-session-c",
            revision=0,
            houdini_build="21.0.999",
        )
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(build_drift),
        )

    def test_catalog_normalization_is_stable_and_unsafe_metadata_fails_closed(self) -> None:
        ordered = _report()
        reversed_report = _report()
        reversed_report["catalog"] = list(reversed(reversed_report["catalog"]))
        _, ordered_digest, ordered_catalog = normalize_live_capability_report(ordered)
        _, reversed_digest, reversed_catalog = normalize_live_capability_report(
            reversed_report
        )
        self.assertEqual(ordered_digest, reversed_digest)
        self.assertEqual(ordered_catalog, reversed_catalog)

        missing_type = _report()
        missing_type["catalog"] = missing_type["catalog"][:-1]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(missing_type),
        )

        unsafe = _report()
        unsafe["catalog"][1]["parameters"] = [
            {
                "name": "path",
                "label": "Path",
                "value_type": "string",
                "tuple_size": 1,
                "writable": False,
                "allows_expression": False,
                "default_value": {
                    "type": "string",
                    "value": "C:\\Users\\secret.hip",
                },
                "numeric_range": None,
            }
        ]
        error = self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(unsafe),
        )
        self.assertNotIn("secret.hip", error.message)

        excessive_outputs = _report()
        excessive_outputs["catalog"][1]["output_count"] = 65
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(excessive_outputs),
        )

        missing_numeric_range = _report()
        missing_numeric_range["catalog"][1]["parameters"] = [
            {
                "name": "size",
                "label": "Size",
                "value_type": "tuple",
                "tuple_size": 3,
                "writable": False,
                "allows_expression": False,
                "default_value": {
                    "type": "tuple",
                    "items_type": "float",
                    "value": [1.0, 1.0, 1.0],
                },
                "numeric_range": None,
            }
        ]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(missing_numeric_range),
        )

        oversized_string_default = _report()
        oversized_string_default["catalog"][1]["parameters"] = [
            {
                "name": "label",
                "label": "Label",
                "value_type": "string",
                "tuple_size": 1,
                "writable": False,
                "allows_expression": False,
                "default_value": {"type": "string", "value": "x" * 1025},
                "numeric_range": None,
            }
        ]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(oversized_string_default),
        )

        fractional_integer_range = _report()
        fractional_integer_range["catalog"][1]["parameters"] = [
            {
                "name": "divisions",
                "label": "Divisions",
                "value_type": "int",
                "tuple_size": 1,
                "writable": False,
                "allows_expression": False,
                "default_value": {"type": "int", "value": 1},
                "numeric_range": {
                    "min_value": 0.5,
                    "max_value": 10.5,
                    "min_is_strict": False,
                    "max_is_strict": False,
                },
            }
        ]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(fractional_integer_range),
        )

        namespaced_type = _report()
        namespaced_type["catalog"][1]["resolved_name"] = "box::2.0"
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(namespaced_type),
        )

        mismatched_canonical_type = _report()
        mismatched_canonical_type["catalog"][2]["resolved_name"] = "transform"
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(mismatched_canonical_type),
        )

        invalid_build = _report(houdini_build="Houdini:21")
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: self.queue.publish_live_capability(invalid_build),
        )

        unavailable = _report(available=False)
        unavailable["catalog"][1].update(
            {
                "available": False,
                "resolved_name": None,
                "parameters": [],
                "input_count": 0,
                "output_count": 0,
            }
        )
        normalized, _, _ = normalize_live_capability_report(unavailable)
        self.assertFalse(normalized["available"])

    def test_http_executor_role_and_capability_shape_are_fail_closed(self) -> None:
        bridge_token = "bridge-" + "b" * 32
        executor_token = "executor-" + "e" * 32
        application = BridgeApplication(
            _Session(),
            SimpleNamespace(),
            bridge_token,
            scene_queue=self.queue,
            scene_registry=self.registry,
            scene_executor_token=executor_token,
        )
        self.assertTrue(application.authorized("Bearer " + bridge_token))
        self.assertFalse(application.scene_executor_authorized(bridge_token))
        self.assertTrue(application.scene_executor_authorized(executor_token))
        self.assertTrue(
            application.requires_scene_executor_authorization(
                "POST", "/v1/scene/capabilities"
            )
        )
        self.assertTrue(
            application.requires_scene_executor_authorization(
                "GET", "/v1/scene/requests/next"
            )
        )
        self.assertTrue(
            application.requires_scene_executor_authorization(
                "POST", "/v1/scene/requests/request-a/result"
            )
        )
        self.assertFalse(
            application.requires_scene_executor_authorization(
                "POST", "/v1/scene/requests"
            )
        )
        self.assertFalse(
            application.requires_scene_executor_authorization(
                "GET", "/v1/scene/status"
            )
        )
        self.assertEqual("X-HIA-Executor-Token", SCENE_EXECUTOR_HEADER)
        with self.assertRaises(ValueError):
            BridgeApplication(
                _Session(),
                SimpleNamespace(),
                bridge_token,
                scene_queue=self.queue,
                scene_registry=self.registry,
                scene_executor_token=bridge_token,
            )

        handler = object.__new__(BridgeRequestHandler)
        handler.server = SimpleNamespace(application=application)
        payload, status = handler._handle_post(
            "/v1/scene/capabilities", {"report": _report()}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["available"])
        self.assertRegex(payload["attestation_digest"], r"^[a-f0-9]{64}$")
        self.assertNotIn("process_nonce", payload)
        status_payload, status_code = handler._handle_get(
            "/v1/scene/status", ""
        )
        self.assertEqual(200, status_code)
        self.assertEqual("hip-session-a", status_payload["scene"]["hip_session_id"])
        self.assertNotIn("process_nonce", repr(status_payload))

        malformed_report = _report(sequence=2)
        del malformed_report["catalog"]
        self.assert_queue_error(
            "CAPABILITY_MISMATCH",
            lambda: handler._handle_post(
                "/v1/scene/capabilities", {"report": malformed_report}
            ),
        )
        self.assertIsNone(self.queue.current_attestation_digest)

        injected = {"report": _report(), "expected_attestation_digest": "f" * 64}
        with self.assertRaises(Exception) as caught:
            handler._handle_post("/v1/scene/capabilities", injected)
        self.assertIn("field", str(caught.exception).casefold())

        self.assert_queue_error(
            "TOOL_NOT_ALLOWED",
            lambda: handler._handle_post(
                "/v1/scene/requests/request-a/approval",
                {
                    "decision": "allow",
                    "request_digest": "b" * 64,
                    "launch_id": "launch-b2",
                    "generation": 4,
                },
            ),
        )
        self.assert_queue_error(
            "TOOL_NOT_ALLOWED",
            lambda: handler._submit_scene_request(
                {"tool_name": "houdini_graph_validate", "arguments": {}}
            ),
        )

        captured: list[tuple[int, dict[str, object]]] = []
        request_handler = object.__new__(BridgeRequestHandler)
        request_handler.server = SimpleNamespace(application=application)
        request_handler.path = "/v1/scene/capabilities"
        request_handler.headers = {"Authorization": "Bearer " + bridge_token}
        request_handler._write_json = lambda status, value: captured.append(  # type: ignore[method-assign]
            (int(status), value)
        )
        request_handler._read_json_body = lambda **_kwargs: {}  # type: ignore[method-assign]
        request_handler._handle_post = lambda _path, _body: ({"ok": True}, 200)  # type: ignore[method-assign]
        request_handler._handle("POST")
        self.assertEqual(403, captured[-1][0])
        self.assertEqual(
            "SCENE_EXECUTOR_UNAUTHORIZED",
            captured[-1][1]["structured_error"]["code"],
        )
        self.assertNotIn(bridge_token, repr(captured))
        self.assertNotIn(executor_token, repr(captured))

        captured.clear()
        request_handler.headers[SCENE_EXECUTOR_HEADER] = executor_token
        request_handler._handle("POST")
        self.assertEqual((200, {"ok": True}), captured[-1])

        captured.clear()
        request_handler.path = "/v1/scene/status"
        request_handler.headers = {"Authorization": "Bearer " + bridge_token}
        request_handler._handle_get = lambda _path, _query: (  # type: ignore[method-assign]
            {"ok": True, "scene": {}},
            200,
        )
        request_handler._handle("GET")
        self.assertEqual((200, {"ok": True, "scene": {}}), captured[-1])

        captured.clear()
        request_handler.headers = {}
        request_handler._handle("GET")
        self.assertEqual(401, captured[-1][0])


if __name__ == "__main__":
    unittest.main()
