from __future__ import annotations

import copy
import json
import sys
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "tests" / "fakes"))

from hia_bridge.scene_queue import (  # noqa: E402
    ALLOWED_TOOLS,
    FakeCapabilityAttestation,
    SceneQueue,
    SceneQueueError,
    SceneRequest,
)
from hia_bridge.http_server import BridgeApplication, BridgeRequestHandler  # noqa: E402
from fake_b3_harness import GateB3OfflineHarness  # noqa: E402
from fake_scene_executor import FAKE_CATALOG_DIGEST, FakeSceneExecutor  # noqa: E402
from hia_core.houdini_contract import SchemaRegistry, graph_digest  # noqa: E402


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SceneQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.registry = SchemaRegistry()
        self.graph = self._fixture("stairs_graph.json")
        self.attestation = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id="hip-session",
            hip_fingerprint="a" * 64,
            scene_revision=7,
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )
        self.queue = self._new_queue()
        self.queue.install_attestation(self.attestation)

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        path = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def _new_queue(self, **kwargs: object) -> SceneQueue:
        options: dict[str, object] = {"clock": self.clock}
        options.update(kwargs)
        return SceneQueue(
            "launch-1",
            3,
            expected_schema_digest=self.registry.manifest_digest,
            expected_catalog_digest=FAKE_CATALOG_DIGEST,
            **options,
        )

    def _arguments(
        self,
        request_id: str,
        idempotency_key: str,
        *,
        write: bool = False,
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "request_id": request_id,
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "hip_session_id": "hip-session",
            "base_scene_revision": 7,
            "idempotency_key": idempotency_key,
            "deadline_ms": 10000,
            "permission_level": "scene_write" if write else "scene_read",
        }
        if write:
            arguments["expected_hip_fingerprint"] = "a" * 64
        return arguments

    def _request(
        self,
        request_id: str,
        idempotency_key: str,
        *,
        tool_name: str = "houdini_scene_info",
        write: bool = False,
        deadline_offset: float = 10.0,
    ) -> SceneRequest:
        arguments = self._arguments(request_id, idempotency_key, write=write)
        if tool_name in {
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        }:
            arguments["expected_hip_fingerprint"] = "a" * 64
        if tool_name in {"houdini_graph_validate", "houdini_graph_apply"}:
            arguments["graph"] = copy.deepcopy(self.graph)
        if tool_name == "houdini_graph_apply":
            arguments["canonical_graph_digest"] = graph_digest(self.graph)
        if tool_name == "houdini_graph_verify":
            arguments["root_path"] = "/obj/HIA_Graph_stairs_demo"
            arguments["expected_graph_digest"] = graph_digest(self.graph)
        return self.queue.build_request(
            tool_name,
            arguments,
            self.clock() + deadline_offset,
        )

    @staticmethod
    def _apply_success_result(request: SceneRequest) -> dict[str, object]:
        assert request.approval_binding_digest is not None
        return {
            "ok": True,
            "result": {
                "canonical_graph_digest": request.arguments[
                    "canonical_graph_digest"
                ],
                "approval_binding_digest": request.approval_binding_digest,
            },
        }

    def _approve(self, request: SceneRequest) -> None:
        presentation = self.queue.poll_next()
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertEqual(presentation.kind, "approval_required")
        self.assertNotIn("executor_token", presentation.to_dict())
        self.assertEqual(
            presentation.approval_binding_digest,
            request.approval_binding_digest,
        )
        self.assertEqual(presentation.approval_payload, request.approval_payload)
        self.assertEqual(
            presentation.approval_payload["nodes"],
            request.arguments["graph"]["nodes"],
        )
        self.queue.decide_approval(
            presentation.request_id,
            "allow",
            presentation.request_digest,
            "launch-1",
            3,
        )

    def test_allowlist_is_exact_and_missing_attestation_fails_closed(self) -> None:
        self.assertEqual(
            ALLOWED_TOOLS,
            {
                "houdini_scene_info",
                "houdini_node_type_info",
                "houdini_graph_validate",
                "houdini_graph_apply",
                "houdini_graph_verify",
            },
        )
        empty = self._new_queue()
        with self.assertRaises(SceneQueueError) as caught:
            empty.build_request(
                "houdini_scene_info",
                self._arguments("req-1", "idem-key-0000001"),
                self.clock() + 1,
            )
        self.assertEqual(caught.exception.code, "HOUDINI_UNAVAILABLE")

    def test_current_attestation_is_read_only_and_mismatch_is_rejected(self) -> None:
        self.assertEqual(self.queue.current_attestation_digest, self.attestation.digest)
        request = SceneRequest.build(
            "houdini_scene_info",
            self._arguments("req-1", "idem-key-0000001"),
            self.clock() + 10,
            "launch-1",
            3,
            "d" * 64,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(request)
        self.assertEqual(caught.exception.code, "CAPABILITY_MISMATCH")

        stale_arguments = self._arguments("validate-1", "validate-idem-0001")
        stale_arguments["expected_hip_fingerprint"] = "d" * 64
        stale = self.queue.build_request(
            "houdini_graph_validate", stale_arguments, self.clock() + 10
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(stale)
        self.assertEqual(caught.exception.code, "CAPABILITY_MISMATCH")

        uppercase_arguments = self._arguments(
            "validate-2", "validate-idem-0002"
        )
        uppercase_arguments["expected_hip_fingerprint"] = "A" * 64
        uppercase = self.queue.build_request(
            "houdini_graph_validate", uppercase_arguments, self.clock() + 10
        )
        self.assertEqual(self.queue.submit(uppercase).state, "queued")

    def test_attestation_must_match_reviewed_schema_and_catalog(self) -> None:
        wrong_catalog = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id="hip-session",
            hip_fingerprint="a" * 64,
            scene_revision=7,
            catalog_digest="b" * 64,
            schema_digest=self.registry.manifest_digest,
        )
        queue = self._new_queue()
        with self.assertRaises(SceneQueueError) as caught:
            queue.install_attestation(wrong_catalog)
        self.assertEqual(caught.exception.code, "CAPABILITY_MISMATCH")

        same_revision_new_fingerprint = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id="hip-session",
            hip_fingerprint="d" * 64,
            scene_revision=7,
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.replace_attestation(
                same_revision_new_fingerprint, self.attestation.digest
            )
        self.assertEqual(caught.exception.code, "SCENE_CONFLICT")

    def test_attestation_replacement_is_cas_monotonic_and_invalidates_old_work(self) -> None:
        terminal_request = self._request("done-1", "done-idem-000001")
        self.queue.submit(terminal_request)
        terminal_work = self.queue.poll_next()
        assert terminal_work is not None and terminal_work.executor_token is not None
        self.queue.complete(
            terminal_work.request_id, terminal_work.executor_token, {"ok": True}
        )
        self.queue.submit(self._request("pending-1", "pending-idem-001"))

        advanced = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id="hip-session",
            hip_fingerprint="d" * 64,
            scene_revision=8,
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.replace_attestation(advanced, "f" * 64)
        self.assertEqual(caught.exception.code, "CAPABILITY_MISMATCH")
        self.assertEqual(self.queue.get_result("pending-1").state, "queued")

        regressed = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id="hip-session",
            hip_fingerprint="d" * 64,
            scene_revision=6,
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.replace_attestation(regressed, self.attestation.digest)
        self.assertEqual(caught.exception.code, "SCENE_CONFLICT")

        self.assertEqual(
            self.queue.replace_attestation(advanced, self.attestation.digest),
            advanced.digest,
        )
        invalidated = self.queue.get_result("pending-1")
        self.assertTrue(invalidated.terminal)
        self.assertEqual(
            invalidated.structured_error["code"], "CAPABILITY_MISMATCH"
        )
        replay = self.queue.submit(terminal_request)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.result, {"ok": True})

        next_arguments = self._arguments("next-1", "next-idem-000001")
        next_arguments["base_scene_revision"] = 8
        next_request = self.queue.build_request(
            "houdini_scene_info", next_arguments, self.clock() + 10
        )
        self.assertEqual(self.queue.submit(next_request).state, "queued")

    def test_unknown_tool_and_permission_upgrade_are_denied(self) -> None:
        unknown = self.queue.build_request(
            "houdini_python_exec",
            self._arguments("req-1", "idem-key-0000001"),
            self.clock() + 10,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(unknown)
        self.assertEqual(caught.exception.code, "TOOL_NOT_ALLOWED")

        arguments = self._arguments("req-2", "idem-key-0000002")
        arguments["permission_level"] = "scene_write"
        upgraded = self.queue.build_request(
            "houdini_scene_info", arguments, self.clock() + 10
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(upgraded)
        self.assertEqual(caught.exception.code, "PERMISSION_MISMATCH")

    def test_read_claim_complete_and_terminal_replay_are_exactly_once(self) -> None:
        request = self._request("req-1", "idem-key-0000001")
        submitted = self.queue.submit(request)
        self.assertEqual(submitted.state, "queued")
        self.assertNotIn("executor_token", submitted.to_dict())

        work = self.queue.poll_next()
        self.assertIsNotNone(work)
        assert work is not None and work.executor_token is not None
        self.assertEqual(work.kind, "execute")
        result = {"ok": True, "request_id": "req-1"}
        completed = self.queue.complete("req-1", work.executor_token, result)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.result, result)

        replay = self.queue.submit(request)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.result, result)
        replay.result["ok"] = False
        self.assertTrue(self.queue.get_result("req-1").result["ok"])
        self.assertIsNone(self.queue.poll_next())

        repeat_complete = self.queue.complete("req-1", work.executor_token, result)
        self.assertTrue(repeat_complete.replayed)
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.complete(
                "req-1", work.executor_token, {"ok": False, "changed": True}
            )
        self.assertEqual(caught.exception.code, "RESULT_CONFLICT")

        self.clock.advance(20)
        rebuilt = self.queue.build_request(
            "houdini_scene_info",
            self._arguments("req-1", "idem-key-0000001"),
            self.clock() + 999,
        )
        self.assertEqual(rebuilt.absolute_deadline, request.absolute_deadline)
        self.assertEqual(rebuilt.attestation_digest, request.attestation_digest)
        late_replay = self.queue.submit(rebuilt)
        self.assertTrue(late_replay.replayed)
        self.assertEqual(late_replay.result, result)

    def test_validate_is_read_only_and_never_enters_approval_or_write_lock(self) -> None:
        first = self._request(
            "validate-1",
            "validate-idem-0001",
            tool_name="houdini_graph_validate",
        )
        second = self._request(
            "validate-2",
            "validate-idem-0002",
            tool_name="houdini_graph_validate",
        )
        self.assertEqual(self.queue.submit(first).state, "queued")
        self.assertEqual(self.queue.submit(second).state, "queued")

        first_work = self.queue.poll_next()
        second_work = self.queue.poll_next()
        self.assertIsNotNone(first_work)
        self.assertIsNotNone(second_work)
        assert first_work is not None and second_work is not None
        self.assertEqual(first_work.kind, "execute")
        self.assertEqual(second_work.kind, "execute")
        self.assertIsNotNone(first_work.executor_token)
        self.assertIsNotNone(second_work.executor_token)

        with self.assertRaises(SceneQueueError) as caught:
            self.queue.decide_approval(
                first.arguments["request_id"],
                "allow",
                first.request_digest,
                "launch-1",
                3,
            )
        self.assertEqual(caught.exception.code, "APPROVAL_NOT_REQUIRED")

    def test_apply_requires_presentation_exact_approval_and_one_use(self) -> None:
        request = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
        )
        tampered_binding = replace(
            request, approval_binding_digest="f" * 64
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(tampered_binding)
        self.assertEqual(caught.exception.code, "REQUEST_DIGEST_MISMATCH")
        self.assertEqual(self.queue.submit(request).state, "awaiting_approval")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.decide_approval(
                "apply-1", "allow", request.request_digest, "launch-1", 3
            )
        self.assertEqual(caught.exception.code, "APPROVAL_NOT_PRESENTED")

        presentation = self.queue.poll_next()
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertEqual(presentation.kind, "approval_required")
        self.assertNotIn("executor_token", presentation.to_dict())
        self.assertIsNone(self.queue.poll_next())
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.decide_approval(
                "apply-1", "allow", "f" * 64, "launch-1", 3
            )
        self.assertEqual(caught.exception.code, "APPROVAL_DIGEST_MISMATCH")

        allowed = self.queue.decide_approval(
            "apply-1", "allow", request.request_digest, "launch-1", 3
        )
        self.assertEqual(allowed.state, "queued")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.decide_approval(
                "apply-1", "allow", request.request_digest, "launch-1", 3
            )
        self.assertEqual(caught.exception.code, "APPROVAL_ALREADY_RESOLVED")

        work = self.queue.poll_next()
        self.assertIsNotNone(work)
        assert work is not None and work.executor_token is not None
        self.assertEqual(work.kind, "execute")
        self.assertIsNone(self.queue.poll_next())
        wrong_result = self._apply_success_result(request)
        wrong_result["result"]["approval_binding_digest"] = "f" * 64
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.complete("apply-1", work.executor_token, wrong_result)
        self.assertEqual(caught.exception.code, "APPROVAL_MISMATCH")
        self.queue.complete(
            "apply-1", work.executor_token, self._apply_success_result(request)
        )

    def test_second_apply_is_rejected_before_approval_consumption(self) -> None:
        first = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
        )
        second = self._request(
            "apply-2",
            "apply-idem-00002",
            tool_name="houdini_graph_apply",
            write=True,
        )
        self.assertEqual(self.queue.submit(first).state, "awaiting_approval")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(second)
        self.assertEqual(caught.exception.code, "WRITE_IN_PROGRESS")
        self.assertEqual(caught.exception.status, 409)
        self._approve(first)

        work_one = self.queue.poll_next()
        self.assertIsNotNone(work_one)
        assert work_one is not None and work_one.executor_token is not None
        self.assertIsNone(self.queue.poll_next())
        self.queue.complete(
            work_one.request_id,
            work_one.executor_token,
            self._apply_success_result(first),
        )
        self.assertEqual(self.queue.submit(second).state, "awaiting_approval")

    def test_read_only_work_is_not_blocked_by_an_active_apply_lock(self) -> None:
        write = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
        )
        self.queue.submit(write)
        self._approve(write)
        write_work = self.queue.poll_next()
        self.assertIsNotNone(write_work)
        assert write_work is not None and write_work.executor_token is not None

        read = self._request(
            "validate-1",
            "validate-idem-0001",
            tool_name="houdini_graph_validate",
        )
        self.queue.submit(read)
        read_work = self.queue.poll_next()
        self.assertIsNotNone(read_work)
        assert read_work is not None
        self.assertEqual("houdini_graph_validate", read_work.tool_name)
        self.assertEqual("execute", read_work.kind)

    def test_parallel_pollers_do_not_claim_one_request_twice(self) -> None:
        self.queue.submit(self._request("req-1", "idem-key-0000001"))
        barrier = threading.Barrier(3)
        results: list[object] = []

        def poll() -> None:
            barrier.wait()
            results.append(self.queue.poll_next())

        threads = [threading.Thread(target=poll), threading.Thread(target=poll)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_denial_is_terminal_and_exact_replay_does_not_queue(self) -> None:
        request = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
        )
        self.queue.submit(request)
        presentation = self.queue.poll_next()
        assert presentation is not None
        denied = self.queue.decide_approval(
            "apply-1", "deny", presentation.request_digest, "launch-1", 3
        )
        self.assertEqual(denied.state, "denied")
        self.assertEqual(denied.structured_error["code"], "APPROVAL_DENIED")
        self.assertTrue(self.queue.submit(request).replayed)
        self.assertIsNone(self.queue.poll_next())

    def test_approval_and_absolute_request_deadlines_do_not_reset(self) -> None:
        request = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
            deadline_offset=120,
        )
        self.queue.submit(request)
        self._approve(request)
        self.clock.advance(60.1)
        self.assertIsNone(self.queue.poll_next())
        expired = self.queue.get_result("apply-1")
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.structured_error["code"], "APPROVAL_EXPIRED")

        read = self._request(
            "read-1", "read-idem-000001", deadline_offset=2
        )
        self.queue.submit(read)
        self.clock.advance(2)
        self.assertEqual(self.queue.get_result("read-1").state, "expired")

    def test_claimed_apply_deadline_is_indeterminate_until_trusted_reconcile(self) -> None:
        request = self._request(
            "apply-1",
            "apply-idem-00001",
            tool_name="houdini_graph_apply",
            write=True,
            deadline_offset=2,
        )
        self.queue.submit(request)
        self._approve(request)
        work = self.queue.poll_next()
        assert work is not None and work.executor_token is not None
        claimed_read = self._request(
            "read-claimed-before-indeterminate", "read-claimed-idem-0001"
        )
        queued_read = self._request(
            "read-queued-before-indeterminate", "read-queued-idem-00001"
        )
        self.queue.submit(claimed_read)
        self.queue.submit(queued_read)
        claimed_read_work = self.queue.poll_next()
        assert (
            claimed_read_work is not None
            and claimed_read_work.executor_token is not None
        )
        self.clock.advance(2)
        snapshot = self.queue.get_result("apply-1")
        self.assertEqual(snapshot.state, "indeterminate")
        self.assertTrue(snapshot.cancel_requested)
        self.assertEqual(
            snapshot.structured_error["code"], "SCENE_STATE_INDETERMINATE"
        )
        for request_id in (
            "read-claimed-before-indeterminate",
            "read-queued-before-indeterminate",
        ):
            stale_read = self.queue.get_result(request_id)
            self.assertEqual(stale_read.state, "indeterminate")
            self.assertEqual(
                stale_read.structured_error["code"],
                "SCENE_STATE_INDETERMINATE",
            )

        second = self._request(
            "apply-2",
            "apply-idem-00002",
            tool_name="houdini_graph_apply",
            write=True,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(second)
        self.assertEqual(
            caught.exception.code, "SCENE_STATE_INDETERMINATE"
        )

        read = self._request("read-after-indeterminate", "read-idem-000001")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(read)
        self.assertEqual(
            caught.exception.code, "SCENE_STATE_INDETERMINATE"
        )

        self.assertEqual(
            self.queue.replace_attestation(
                self.attestation, self.attestation.digest
            ),
            self.attestation.digest,
        )
        self.assertEqual(self.queue.submit(read).state, "queued")
        self.assertEqual(self.queue.submit(second).state, "awaiting_approval")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.complete(
                "apply-1",
                work.executor_token,
                self._apply_success_result(request),
            )
        self.assertEqual(caught.exception.code, "RESULT_CONFLICT")

    def test_idempotency_conflict_and_capacity_are_fail_closed(self) -> None:
        first = self._request("req-1", "shared-idem-0001")
        second = self._request("req-2", "shared-idem-0001")
        self.queue.submit(first)
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(second)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

        small = self._new_queue(capacity=2)
        small.install_attestation(self.attestation)
        for number in (1, 2):
            small.submit(
                small.build_request(
                    "houdini_scene_info",
                    self._arguments(f"small-{number}", f"small-idem-0000{number}"),
                    self.clock() + 10,
                )
            )
        with self.assertRaises(SceneQueueError) as caught:
            small.submit(
                small.build_request(
                    "houdini_scene_info",
                    self._arguments("small-3", "small-idem-00003"),
                    self.clock() + 10,
                )
            )
        self.assertEqual(caught.exception.code, "QUEUE_FULL")
        self.assertEqual(caught.exception.details, {"capacity": 2})

    def test_cancel_before_and_after_claim_is_exactly_once(self) -> None:
        self.queue.submit(self._request("req-1", "idem-key-0000001"))
        cancelled = self.queue.cancel("req-1")
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(cancelled.structured_error["code"], "CANCELLED")
        self.assertTrue(self.queue.cancel("req-1").replayed)

        self.queue.submit(self._request("req-2", "idem-key-0000002"))
        work = self.queue.poll_next()
        assert work is not None and work.executor_token is not None
        requested = self.queue.cancel("req-2")
        self.assertEqual(requested.state, "claimed")
        self.assertTrue(requested.cancel_requested)
        self.assertTrue(self.queue.cancel("req-2").cancel_requested)
        completed = self.queue.complete(
            "req-2", work.executor_token, {"ok": False, "cancelled": True}
        )
        self.assertEqual(completed.state, "completed")

    def test_shutdown_resolves_active_records_once_and_rejects_new_work(self) -> None:
        first = self._request("req-1", "idem-key-0000001")
        second = self._request("req-2", "idem-key-0000002")
        self.queue.submit(first)
        self.queue.submit(second)
        self.queue.poll_next()
        self.queue.shutdown()
        self.queue.shutdown()
        for request_id in ("req-1", "req-2"):
            snapshot = self.queue.get_result(request_id)
            self.assertEqual(snapshot.state, "shutdown")
            self.assertEqual(snapshot.structured_error["code"], "SHUTTING_DOWN")
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.submit(first)
        self.assertEqual(caught.exception.code, "SHUTTING_DOWN")
        self.assertIsNone(self.queue.poll_next())

    def test_terminal_retention_is_bounded(self) -> None:
        queue = self._new_queue(capacity=1, terminal_retention=2)
        queue.install_attestation(self.attestation)
        retained_requests = []
        for number in range(2):
            request = queue.build_request(
                "houdini_scene_info",
                self._arguments(
                    f"retained-{number}", f"retained-idem-{number:04d}"
                ),
                self.clock() + 10,
            )
            queue.submit(request)
            retained_requests.append(request)
            work = queue.poll_next()
            assert work is not None and work.executor_token is not None
            queue.complete(work.request_id, work.executor_token, {"ok": True})
        third = queue.build_request(
            "houdini_scene_info",
            self._arguments("retained-2", "retained-idem-0002"),
            self.clock() + 10,
        )
        with self.assertRaises(SceneQueueError) as caught:
            queue.submit(third)
        self.assertEqual(caught.exception.code, "QUEUE_FULL")
        self.assertEqual(
            caught.exception.details, {"terminal_retention": 2}
        )
        self.assertTrue(queue.submit(retained_requests[0]).replayed)
        self.assertEqual(queue.get_result("retained-0").state, "completed")
        self.assertEqual(queue.get_result("retained-1").state, "completed")

    def test_structured_errors_and_snapshots_never_expose_executor_token(self) -> None:
        self.queue.submit(self._request("req-1", "idem-key-0000001"))
        snapshot_json = json.dumps(self.queue.get_result("req-1").to_dict())
        self.assertNotIn("token", snapshot_json.lower())
        with self.assertRaises(SceneQueueError) as caught:
            self.queue.get_result("missing")
        error_json = json.dumps(caught.exception.to_dict())
        self.assertNotIn("token", error_json.lower())
        self.assertEqual(
            set(caught.exception.to_dict()), {"code", "status", "message", "details"}
        )


class BridgeSceneHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SchemaRegistry()
        self.graph = self._fixture("stairs_graph.json")
        self.queue = SceneQueue(
            "http-launch",
            1,
            expected_schema_digest=self.registry.manifest_digest,
            expected_catalog_digest=FAKE_CATALOG_DIGEST,
        )
        self.attestation = FakeCapabilityAttestation(
            launch_id="http-launch",
            generation=1,
            process_nonce="http-fake-process",
            hip_session_id="http-hip-session",
            hip_fingerprint="a" * 64,
            scene_revision=4,
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )
        self.queue.install_attestation(self.attestation)
        application = BridgeApplication(
            object(),
            object(),
            "t" * 64,
            scene_queue=self.queue,
            scene_registry=self.registry,
        )
        self.server = SimpleNamespace(
            application=application,
            shutdown=mock.Mock(),
        )
        self.handler = object.__new__(BridgeRequestHandler)
        self.handler.server = self.server

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        path = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def _apply_arguments(self, suffix: str) -> dict[str, object]:
        return {
            "request_id": f"http-request-{suffix}",
            "thread_id": "http-thread",
            "turn_id": f"http-turn-{suffix}",
            "hip_session_id": self.attestation.hip_session_id,
            "expected_hip_fingerprint": self.attestation.hip_fingerprint,
            "base_scene_revision": self.attestation.scene_revision,
            "idempotency_key": f"http-idempotency-{suffix}-0001",
            "deadline_ms": 10_000,
            "permission_level": "scene_write",
            "graph": copy.deepcopy(self.graph),
            "canonical_graph_digest": graph_digest(self.graph),
        }

    def test_terminal_queue_error_is_shaped_as_contract_result(self) -> None:
        arguments = self._apply_arguments("denied")
        request = self.queue.build_request(
            "houdini_graph_apply", arguments, time.monotonic() + 10
        )
        self.queue.submit(request)
        presentation = self.queue.poll_next()
        assert presentation is not None
        snapshot = self.queue.decide_approval(
            presentation.request_id,
            "deny",
            presentation.request_digest,
            "http-launch",
            1,
        )
        self.assertIsNone(snapshot.result)
        payload = self.handler._terminal_scene_payload(snapshot)
        self.assertFalse(payload["result"]["ok"])
        self.assertEqual(
            payload["result"]["structured_error"]["code"],
            "APPROVAL_DENIED",
        )

    def test_http_retry_reuses_deadline_and_second_apply_is_409(self) -> None:
        first_body = {
            "tool_name": "houdini_graph_apply",
            "arguments": self._apply_arguments("first"),
        }
        first_payload, first_status = self.handler._submit_scene_request(first_body)
        self.assertEqual(first_status, 202)
        presentation = self.queue.poll_next()
        assert presentation is not None
        self.queue.decide_approval(
            presentation.request_id,
            "deny",
            presentation.request_digest,
            "http-launch",
            1,
        )
        replay_payload, replay_status = self.handler._submit_scene_request(first_body)
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay_payload["replayed"])
        self.assertEqual(
            replay_payload["result"]["structured_error"]["code"],
            "APPROVAL_DENIED",
        )

        active_body = {
            "tool_name": "houdini_graph_apply",
            "arguments": self._apply_arguments("active"),
        }
        _, active_status = self.handler._submit_scene_request(active_body)
        self.assertEqual(active_status, 202)
        second_body = {
            "tool_name": "houdini_graph_apply",
            "arguments": self._apply_arguments("second"),
        }
        blocked_payload, blocked_status = self.handler._submit_scene_request(
            second_body
        )
        self.assertEqual(blocked_status, 409)
        self.assertFalse(blocked_payload["ok"])
        self.assertEqual(
            blocked_payload["result"]["structured_error"]["code"],
            "WRITE_IN_PROGRESS",
        )

    def test_shutdown_closes_scene_queue_before_http_server(self) -> None:
        request = self.queue.build_request(
            "houdini_scene_info",
            {
                "request_id": "http-read-request",
                "thread_id": "http-thread",
                "turn_id": "http-turn-read",
                "hip_session_id": self.attestation.hip_session_id,
                "base_scene_revision": self.attestation.scene_revision,
                "idempotency_key": "http-read-idempotency-0001",
                "deadline_ms": 10_000,
                "permission_level": "scene_read",
                "include_graph_summaries": False,
            },
            time.monotonic() + 10,
        )
        self.queue.submit(request)
        with mock.patch("hia_bridge.http_server.threading.Thread") as thread_type:
            payload, status = self.handler._handle_post("/v1/shutdown", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "shutting_down")
        self.assertTrue(self.queue.is_shutdown)
        self.assertEqual(self.queue.get_result(request.arguments["request_id"]).state, "shutdown")
        thread_type.assert_called_once()
        thread_type.return_value.start.assert_called_once_with()

    def test_bridge_application_rejects_schema_digest_drift(self) -> None:
        wrong_queue = SceneQueue(
            "http-launch",
            1,
            expected_schema_digest="f" * 64,
            expected_catalog_digest=FAKE_CATALOG_DIGEST,
        )
        with self.assertRaises(ValueError):
            BridgeApplication(
                object(),
                object(),
                "t" * 64,
                scene_queue=wrong_queue,
                scene_registry=self.registry,
            )


class FakeGeneralGraphExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SchemaRegistry()
        self.executor = FakeSceneExecutor(
            hip_session_id="fixture-session",
            hip_fingerprint="ab" * 32,
        )
        self.harness = GateB3OfflineHarness(
            self.executor,
            registry=self.registry,
        )

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        path = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def _request(
        self,
        tool_name: str,
        *,
        suffix: str,
        graph: dict[str, object] | None = None,
        digest: str | None = None,
        root_path: str | None = None,
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "request_id": f"request-{suffix}",
            "thread_id": "thread-fixture",
            "turn_id": f"turn-{suffix}",
            "hip_session_id": self.executor.hip_session_id,
            "base_scene_revision": self.executor.scene_revision,
            "idempotency_key": f"idempotency-{suffix}-0001",
            "deadline_ms": 1000,
            "permission_level": (
                "scene_write" if tool_name == "houdini_graph_apply" else "scene_read"
            ),
        }
        if tool_name in {
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        }:
            arguments["expected_hip_fingerprint"] = self.executor.hip_fingerprint
        if graph is not None:
            arguments["graph"] = graph
        if tool_name == "houdini_graph_apply":
            arguments["canonical_graph_digest"] = digest
        if tool_name == "houdini_graph_verify":
            arguments["root_path"] = root_path
            arguments["expected_graph_digest"] = digest
        return self.registry.validate_input(tool_name, arguments)

    def _execute_checked(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        if tool_name == "houdini_graph_apply":
            _, _, presentation = self.harness.present_apply(arguments)
            snapshot = self.harness.allow_and_execute(presentation)
            assert snapshot.result is not None
            return snapshot.result
        output = self.executor.execute(tool_name, arguments)
        return self.registry.validate_output(tool_name, arguments, output)

    def _validate_apply_verify(
        self, fixture_name: str, suffix: str
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        graph = self._fixture(fixture_name)
        before = (
            self.executor.scene_revision,
            self.executor.hip_fingerprint,
            json.dumps(self.executor.graphs, sort_keys=True),
        )
        validate_request = self._request(
            "houdini_graph_validate", suffix=f"{suffix}-validate", graph=graph
        )
        validated = self._execute_checked("houdini_graph_validate", validate_request)
        self.assertTrue(validated["ok"])
        self.assertFalse(validated["result"]["scene_mutated"])
        self.assertEqual(before[0], self.executor.scene_revision)
        self.assertEqual(before[1], self.executor.hip_fingerprint)
        self.assertEqual(before[2], json.dumps(self.executor.graphs, sort_keys=True))
        digest = validated["result"]["canonical_graph_digest"]
        self.assertEqual(digest, graph_digest(validated["result"]["normalized_graph"]))

        apply_request = self._request(
            "houdini_graph_apply",
            suffix=f"{suffix}-apply",
            graph=validated["result"]["normalized_graph"],
            digest=digest,
        )
        applied = self._execute_checked("houdini_graph_apply", apply_request)
        self.assertTrue(applied["ok"])
        self.assertEqual(digest, applied["result"]["canonical_graph_digest"])
        self.assertEqual(apply_request["base_scene_revision"] + 1, self.executor.scene_revision)

        root_path = applied["result"]["root_path"]
        verify_request = self._request(
            "houdini_graph_verify",
            suffix=f"{suffix}-verify",
            digest=digest,
            root_path=root_path,
        )
        verified = self._execute_checked("houdini_graph_verify", verify_request)
        self.assertTrue(verified["ok"])
        self.assertTrue(verified["result"]["valid"])
        self.assertTrue(verified["result"]["digest_matches"])
        return validated, applied, verified

    def test_both_fixtures_share_validate_apply_verify_path(self) -> None:
        first = self._validate_apply_verify("table_graph.json", "first")
        second = self._validate_apply_verify("stairs_graph.json", "second")
        self.assertNotEqual(
            first[0]["result"]["summary"]["node_count"],
            second[0]["result"]["summary"]["node_count"],
        )
        self.assertNotEqual(
            first[0]["result"]["canonical_graph_digest"],
            second[0]["result"]["canonical_graph_digest"],
        )
        self.assertEqual(
            len(first[2]["result"]["nodes"]),
            first[0]["result"]["summary"]["node_count"],
        )
        self.assertEqual(
            len(second[2]["result"]["nodes"]),
            second[0]["result"]["summary"]["node_count"],
        )
        self.assertFalse(hasattr(self.executor, "_graph_apply_table"))
        self.assertFalse(hasattr(self.executor, "_graph_apply_stairs"))

    def test_apply_requires_matching_current_validation_digest(self) -> None:
        graph = self._fixture("stairs_graph.json")
        digest = graph_digest(graph)
        request = self._request(
            "houdini_graph_apply",
            suffix="unvalidated-apply",
            graph=graph,
            digest=digest,
        )
        result = self._execute_checked("houdini_graph_apply", request)
        self.assertFalse(result["ok"])
        self.assertEqual("APPROVAL_MISMATCH", result["structured_error"]["code"])
        self.assertEqual({}, self.executor.graphs)
        self.assertEqual(0, self.executor.scene_revision)

    def test_fake_catalog_rejects_unknown_parameter_and_type_mismatch(self) -> None:
        unknown = self._fixture("stairs_graph.json")
        unknown["nodes"][3]["parameters"].append(
            {"name": "unknown", "value": {"type": "float", "value": 1.0}}
        )
        request = self._request(
            "houdini_graph_validate", suffix="unknown-parameter", graph=unknown
        )
        output = self._execute_checked("houdini_graph_validate", request)
        self.assertFalse(output["ok"])
        self.assertEqual("PARAMETER_NOT_ALLOWED", output["structured_error"]["code"])

        wrong_type = self._fixture("stairs_graph.json")
        wrong_type["nodes"][0]["parameters"][0]["value"] = {
            "type": "tuple",
            "items_type": "int",
            "value": [1, 1, 1],
        }
        request = self._request(
            "houdini_graph_validate", suffix="wrong-type", graph=wrong_type
        )
        output = self._execute_checked("houdini_graph_validate", request)
        self.assertFalse(output["ok"])
        self.assertEqual("PARAMETER_TYPE_MISMATCH", output["structured_error"]["code"])
        self.assertEqual({}, self.executor.graphs)
        self.assertEqual(0, self.executor.scene_revision)

    def test_fake_catalog_rejects_live_port_mismatch(self) -> None:
        invalid_port = self._fixture("stairs_graph.json")
        invalid_port["connections"][0]["source"]["output"] = 1
        request = self._request(
            "houdini_graph_validate", suffix="invalid-port", graph=invalid_port
        )
        output = self._execute_checked("houdini_graph_validate", request)
        self.assertFalse(output["ok"])
        self.assertEqual("TOPOLOGY_NOT_ALLOWED", output["structured_error"]["code"])
        self.assertEqual({}, self.executor.graphs)

    def test_uppercase_digest_and_stored_graph_tamper_are_verified_live(self) -> None:
        graph = self._fixture("stairs_graph.json")
        validate_request = self._request(
            "houdini_graph_validate", suffix="uppercase-validate", graph=graph
        )
        validate_request["expected_hip_fingerprint"] = (
            self.executor.hip_fingerprint.upper()
        )
        validated = self._execute_checked(
            "houdini_graph_validate", validate_request
        )
        digest = validated["result"]["canonical_graph_digest"]
        apply_request = self._request(
            "houdini_graph_apply",
            suffix="uppercase-apply",
            graph=validated["result"]["normalized_graph"],
            digest=digest.upper(),
        )
        apply_request["expected_hip_fingerprint"] = (
            self.executor.hip_fingerprint.upper()
        )
        applied = self._execute_checked("houdini_graph_apply", apply_request)
        self.assertTrue(applied["ok"])
        root_path = applied["result"]["root_path"]

        verify_request = self._request(
            "houdini_graph_verify",
            suffix="uppercase-verify",
            root_path=root_path,
            digest=digest.upper(),
        )
        verify_request["expected_hip_fingerprint"] = (
            self.executor.hip_fingerprint.upper()
        )
        verified = self._execute_checked("houdini_graph_verify", verify_request)
        self.assertTrue(verified["result"]["valid"])

        self.executor.tamper_observed_parameter(
            root_path,
            "landing",
            "size",
            {"type": "tuple", "items_type": "float", "value": [9.0, 1.0, 1.0]},
        )
        tampered_request = self._request(
            "houdini_graph_verify",
            suffix="tampered-verify",
            root_path=root_path,
            digest=digest,
        )
        tampered = self._execute_checked(
            "houdini_graph_verify", tampered_request
        )
        self.assertFalse(tampered["result"]["valid"])
        self.assertFalse(tampered["result"]["digest_matches"])
        self.assertNotEqual(
            digest, tampered["result"]["observed_graph_digest"]
        )

    def test_queue_requires_explicit_trusted_attestation_advance_after_apply(self) -> None:
        queue = self.harness.queue
        graph = self._fixture("stairs_graph.json")

        validate_arguments = self._request(
            "houdini_graph_validate", suffix="queue-validate", graph=graph
        )
        validate_request, _ = self.harness.submit(
            "houdini_graph_validate",
            validate_arguments,
            absolute_deadline=time.monotonic() + 10,
        )
        validate_work = self.harness.poll()
        assert validate_work is not None and validate_work.executor_token is not None
        validate_output = self._execute_checked(
            "houdini_graph_validate", validate_work.arguments
        )
        queue.complete(
            validate_work.request_id, validate_work.executor_token, validate_output
        )
        digest = validate_output["result"]["canonical_graph_digest"]

        apply_arguments = self._request(
            "houdini_graph_apply",
            suffix="queue-apply",
            graph=validate_output["result"]["normalized_graph"],
            digest=digest,
        )
        apply_request, _ = self.harness.submit(
            "houdini_graph_apply",
            apply_arguments,
            absolute_deadline=time.monotonic() + 10,
        )
        presentation = self.harness.poll()
        assert presentation is not None
        self.assertEqual("approval_required", presentation.kind)
        self.harness.decide(presentation, "allow")
        apply_work = self.harness.poll()
        assert apply_work is not None and apply_work.executor_token is not None
        completed_apply = self.harness.execute_work(
            apply_work,
            refresh_attestation=False,
        )
        assert completed_apply.result is not None
        apply_output = completed_apply.result

        verify_arguments = self._request(
            "houdini_graph_verify",
            suffix="queue-verify",
            root_path=apply_output["result"]["root_path"],
            digest=digest,
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.harness.submit(
                "houdini_graph_verify",
                verify_arguments,
                absolute_deadline=time.monotonic() + 10,
            )
        self.assertEqual("SCENE_CONFLICT", caught.exception.code)

        self.harness.refresh_attestation()
        verify_request, _ = self.harness.submit(
            "houdini_graph_verify",
            verify_arguments,
            absolute_deadline=time.monotonic() + 10,
        )
        verify_work = self.harness.poll()
        assert verify_work is not None and verify_work.executor_token is not None
        verify_output = self._execute_checked("houdini_graph_verify", verify_work.arguments)
        completed = queue.complete(
            verify_work.request_id, verify_work.executor_token, verify_output
        )
        self.assertTrue(completed.result["result"]["valid"])


if __name__ == "__main__":
    unittest.main()
