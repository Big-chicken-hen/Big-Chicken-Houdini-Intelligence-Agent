from __future__ import annotations

import ast
import copy
import json
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "houdini_mcp"))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "tests" / "fakes"))

from fake_hou_write import (  # noqa: E402
    FakeConnection,
    FakeHouWrite,
    InjectedHouBaseException,
    MUTATION_PHASES,
    certified_write_catalog,
)
from hia_bridge.scene_queue import (  # noqa: E402
    FakeCapabilityAttestation,
    SceneQueue,
    SceneQueueError,
)
from hia_core.houdini_contract import (  # noqa: E402
    SchemaRegistry,
    canonical_json_sha256,
    graph_digest,
)
from hia_panel.houdini_write_adapter import (  # noqa: E402
    HoudiniWriteAdapter,
    HoudiniWriteAdapterError,
    WriteControlAbort,
    _ApprovedWriteBinding,
    _contains_identity,
    _identity_lookup,
    _same_identity_members,
)
from hia_houdini_mcp.adapter import HoudiniMCPAdapter  # noqa: E402


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _WriteReadState:
    """Small injected observer; HOM behavior itself remains in FakeHouWrite."""

    def __init__(
        self,
        catalog: list[dict[str, Any]],
        *,
        hip_session_id: str = "hip-session",
        hip_fingerprint: str = "a" * 64,
        scene_revision: int = 7,
        strict_event_evidence: bool = False,
    ) -> None:
        self.hip_session_id = hip_session_id
        self.hip_fingerprint = hip_fingerprint
        self.scene_revision = scene_revision
        self.catalog = self._live_catalog(catalog)
        self.available = True
        self._active: dict[str, Any] | None = None
        self.callback_sources_verified = 0
        self.refresh_calls = 0
        self.post_commit_refreshes = 0
        self._committed_needs_refresh = False
        self.strict_event_evidence = bool(strict_event_evidence)
        self._hou: FakeHouWrite | None = None
        self._last_owned_evidence: dict[str, Any] | None = None

    def bind_hou(self, fake: FakeHouWrite) -> None:
        self._hou = fake
        if self.strict_event_evidence:
            obj = fake.node("/obj")
            assert obj is not None
            obj.addEventCallback(
                tuple(vars(fake.nodeEventType).values()), self._on_node_event
            )
            self._assert_callback_registered(obj)

    def _assert_callback_registered(self, node: Any) -> None:
        assert self._hou is not None
        expected = tuple(vars(self._hou.nodeEventType).values())
        callbacks = node.eventCallbacks()
        registered = [
            event
            for event_types, callback in callbacks
            if callback == self._on_node_event
            for event in event_types
        ]
        if not all(any(item == seen for seen in registered) for item in expected):
            raise RuntimeError("strict fake observer readback failed")

    @staticmethod
    def _live_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return copy.deepcopy(catalog)

    def refresh(self) -> dict[str, Any]:
        self.refresh_calls += 1
        if self._committed_needs_refresh:
            self._committed_needs_refresh = False
            self.post_commit_refreshes += 1
        return self.capability_report()

    def capability_report(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "hip_session_id": self.hip_session_id,
            "hip_fingerprint": self.hip_fingerprint,
            "scene_revision": self.scene_revision,
            "catalog": copy.deepcopy(self.catalog),
        }

    def begin_owned_write(
        self,
        transaction_id: str,
        *,
        expected_hip_session_id: str,
        expected_scene_revision: int,
        expected_hip_fingerprint: str,
    ) -> object:
        if self._active is not None:
            raise RuntimeError("owned write already active")
        if expected_hip_session_id != self.hip_session_id:
            raise RuntimeError("session mismatch")
        if expected_scene_revision != self.scene_revision:
            raise RuntimeError("revision mismatch")
        if expected_hip_fingerprint != self.hip_fingerprint:
            raise RuntimeError("fingerprint mismatch")
        token = object()
        self._active = {
            "token": token,
            "transaction_id": transaction_id,
            "base": self.scene_revision,
            "base_fingerprint": self.hip_fingerprint,
            "events": 0,
            "mutation_expectation": None,
            "invalidated": False,
            "strict": self.strict_event_evidence,
            "events": [],
            "legacy_event_count": 0,
            "mutations": [],
            "observer_installations": [],
        }
        return token

    def begin_owned_mutation(
        self,
        token: object,
        *,
        expected_callback_source: object | None = None,
        operation: str | None = None,
        event_source_rules: Mapping[str, tuple[object, ...]] | None = None,
        allowed_child_subjects: tuple[object, ...] | None = None,
        required_event_types: tuple[str, ...] | None = None,
        allow_zero_events: bool = False,
    ) -> object:
        active = self._active
        if active is None or active["token"] is not token:
            raise RuntimeError("invalid owned write token")
        if (
            (expected_callback_source is None and event_source_rules is None)
            or active["mutation_expectation"] is not None
            or active["invalidated"]
        ):
            raise RuntimeError("invalid owned mutation expectation")
        marker = object()
        active["mutation_expectation"] = {
            "token": marker,
            "callback_source": expected_callback_source,
            "operation": operation,
            "event_source_rules": event_source_rules,
            "allowed_child_subjects": tuple(allowed_child_subjects or ()),
            "required_event_types": tuple(required_event_types or ()),
            "seen_event_types": set(),
            "seen_child_subjects": [],
            "event_count": 0,
            "allow_zero_events": bool(allow_zero_events),
        }
        return marker

    def finish_owned_mutation(
        self,
        token: object,
        expectation_token: object,
        *,
        expected_child_subjects: tuple[object, ...] | None = None,
        require_all_child_subjects: bool = False,
        exact_readback_proven: bool = False,
    ) -> int:
        active = self._active
        if (
            active is None
            or active["token"] is not token
            or active["mutation_expectation"] is None
            or active["mutation_expectation"]["token"] is not expectation_token
        ):
            raise RuntimeError("invalid owned mutation expectation")
        expectation = active["mutation_expectation"]
        active["mutation_expectation"] = None
        if active["strict"]:
            expected = tuple(expected_child_subjects or ())
            seen_subjects = tuple(expectation["seen_child_subjects"])
            allow_zero_events = expectation["allow_zero_events"] is True
            if (
                (expectation["event_count"] < 1 and not allow_zero_events)
                or (allow_zero_events and exact_readback_proven is not True)
                or (
                    expectation["required_event_types"]
                    and not any(
                        item in expectation["seen_event_types"]
                        for item in expectation["required_event_types"]
                    )
                )
                or (
                    expected
                    and any(
                        not any(value is subject for value in expected)
                        for subject in seen_subjects
                    )
                )
                or (
                    require_all_child_subjects
                    and any(
                        not any(value is subject for value in seen_subjects)
                        for subject in expected
                    )
                )
            ):
                active["invalidated"] = True
            mutation_record = {
                "operation": expectation["operation"],
                "event_count": expectation["event_count"],
                "event_types": sorted(expectation["seen_event_types"]),
                "no_op": False,
            }
            if allow_zero_events:
                mutation_record["exact_readback_proven"] = exact_readback_proven
            active["mutations"].append(mutation_record)
        if active["invalidated"]:
            raise RuntimeError("owned mutation callback source mismatch")
        return expectation["event_count"]

    def install_owned_node_observer(
        self, token: object, node: object
    ) -> dict[str, Any]:
        active = self._active
        if (
            active is None
            or active["token"] is not token
            or not active["strict"]
            or active["mutation_expectation"] is not None
        ):
            raise RuntimeError("strict observer installation is invalid")
        assert self._hou is not None
        node.addEventCallback(
            tuple(vars(self._hou.nodeEventType).values()), self._on_node_event
        )
        self._assert_callback_registered(node)
        record = {"path": node.path(), "session_id": node.sessionId()}
        active["observer_installations"].append(record)
        return copy.deepcopy(record)

    def record_owned_noop(self, token: object, *, operation: str) -> None:
        active = self._active
        if (
            active is None
            or active["token"] is not token
            or not active["strict"]
            or active["mutation_expectation"] is not None
        ):
            raise RuntimeError("strict no-op is invalid")
        active["mutations"].append(
            {
                "operation": operation,
                "event_count": 0,
                "event_types": [],
                "no_op": True,
            }
        )

    def finish_owned_write(self, token: object, *, outcome: str) -> dict[str, Any]:
        active = self._active
        if active is None or active["token"] is not token:
            raise RuntimeError("invalid owned write token")
        if active["mutation_expectation"] is not None or active["invalidated"]:
            raise RuntimeError("owned mutation expectation is still active")
        if outcome == "rolled_back":
            self.scene_revision = active["base"]
            self.hip_fingerprint = active["base_fingerprint"]
        else:
            self.scene_revision = active["base"] + 1
            self.hip_fingerprint = canonical_json_sha256(
                {
                    "previous": active["base_fingerprint"],
                    "scene_revision": self.scene_revision,
                }
            )
            if outcome == "committed":
                self._committed_needs_refresh = True
        if active["strict"]:
            self._last_owned_evidence = {
                "outcome": outcome,
                "events": copy.deepcopy(active["events"]),
                "mutations": copy.deepcopy(active["mutations"]),
                "observer_installations": copy.deepcopy(
                    active["observer_installations"]
                ),
            }
        self._active = None
        return self.capability_report()

    def last_owned_evidence(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._last_owned_evidence)

    def _on_node_event(self, **kwargs: Any) -> None:
        active = self._active
        if active is None:
            return
        expectation = active["mutation_expectation"]
        source = kwargs.get("node")
        event = kwargs.get("event_type")
        event_name = getattr(event, "name", None)
        subject = kwargs.get("child_node")
        rules = {} if expectation is None else expectation["event_source_rules"] or {}
        sources = rules.get(event_name, ())
        matched = bool(
            expectation is not None
            and any(item is source for item in sources)
        )
        allowed_subjects = (
            () if expectation is None else expectation["allowed_child_subjects"]
        )
        if matched and event_name in {"ChildCreated", "ChildDeleted", "ChildSwitched"}:
            matched = subject is not None
            if matched and allowed_subjects:
                matched = any(item is subject for item in allowed_subjects)
        active["events"].append(
            {
                "operation": None if expectation is None else expectation["operation"],
                "event_type": event_name,
                "source_path": source.path(),
                "child_path": None if subject is None else subject.path(),
                "matched": matched,
            }
        )
        if not matched:
            active["invalidated"] = True
            return
        expectation["event_count"] += 1
        expectation["seen_event_types"].add(event_name)
        if subject is not None:
            expectation["seen_child_subjects"].append(subject)

    def on_mutation(self, mutation: Any) -> None:
        if self._active is not None:
            expectation = self._active["mutation_expectation"]
            if (
                expectation is None
                or mutation.callback_source is not expectation["callback_source"]
            ):
                self._active["invalidated"] = True
            else:
                self._active["legacy_event_count"] += 1
                self.callback_sources_verified += 1
        else:
            self.scene_revision += 1
            self.hip_fingerprint = canonical_json_sha256(
                {
                    "previous": self.hip_fingerprint,
                    "scene_revision": self.scene_revision,
                }
            )


class _Guard:
    def __init__(self, reason: str | None = None, phase: str = "preflight") -> None:
        self.reason = reason
        self.phase = phase
        self._authority_lock = threading.RLock()

    def checkpoint(self, phase: str) -> None:
        with self._authority_lock:
            if self.reason is not None and phase == self.phase:
                raise WriteControlAbort(self.reason)

    def mutate(self, phase: str, operation: Any) -> Any:
        with self._authority_lock:
            self.checkpoint(phase)
            return operation()

    def finalize(self, operation: Any) -> Any:
        with self._authority_lock:
            self.checkpoint("commit")
            return operation()

    def contain(self, operation: Any) -> Any:
        """Run mandatory containment under the same non-cancellable lock."""

        with self._authority_lock:
            return operation()


class _ClaimAuthority:
    """Test-only one-shot identity capability owned by the fake controller."""

    def __init__(self, request: Any, claim: Any) -> None:
        self._request = request
        self._claim = claim
        self._execution_token: object | None = None
        self.consumed = False
        self.binding_consumed = False

    def issue_exact_claim(self, request: Any, claim: Any) -> object | None:
        if self.consumed or request is not self._request or claim is not self._claim:
            return None
        self.consumed = True
        self._execution_token = object()
        return self._execution_token

    def consume_binding(self, token: object) -> bool:
        if (
            self.binding_consumed
            or self._execution_token is None
            or token is not self._execution_token
        ):
            return False
        self.binding_consumed = True
        return True


class _NoopTransport:
    def call_tool(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise AssertionError("disabled graph tools must not reach transport")

    def cancel(self, _request_id: Any) -> None:
        return None


class HoudiniWriteAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SchemaRegistry()
        self.clock = _Clock()

    @staticmethod
    def _fixture(name: str) -> dict[str, Any]:
        path = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def _build(
        self,
        fixture: str = "stairs_graph.json",
        *,
        fake: FakeHouWrite | None = None,
        catalog: list[dict[str, Any]] | None = None,
        read: _WriteReadState | None = None,
        guard: _Guard | None = None,
        root_conflict: bool = False,
        strict_event_evidence: bool = False,
    ) -> dict[str, Any]:
        catalog = certified_write_catalog() if catalog is None else copy.deepcopy(catalog)
        if fake is None:
            fake = FakeHouWrite(catalog=catalog)
            read = (
                _WriteReadState(
                    catalog, strict_event_evidence=strict_event_evidence
                )
                if read is None
                else read
            )
            read.bind_hou(fake)
            if not strict_event_evidence:
                fake._mutation_callback = read.on_mutation
        elif read is None:
            read = _WriteReadState(
                catalog, strict_event_evidence=strict_event_evidence
            )
            read.bind_hou(fake)
            if not strict_event_evidence:
                fake._mutation_callback = read.on_mutation
        assert read is not None

        graph = self._fixture(fixture)
        if root_conflict:
            fake.seed_preexisting_child(
                "/obj", "geo", graph["target"]["name_hint"]
            )
            read.scene_revision = 7

        catalog_digest = canonical_json_sha256(catalog)
        attestation = FakeCapabilityAttestation(
            launch_id="launch-1",
            generation=3,
            process_nonce="fake-process",
            hip_session_id=read.hip_session_id,
            hip_fingerprint=read.hip_fingerprint,
            scene_revision=read.scene_revision,
            catalog_digest=catalog_digest,
            schema_digest=self.registry.manifest_digest,
        )
        queue = SceneQueue(
            "launch-1",
            3,
            expected_schema_digest=self.registry.manifest_digest,
            expected_catalog_digest=catalog_digest,
            clock=self.clock,
        )
        queue.install_attestation(attestation)
        arguments = {
            "request_id": "request-1",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "hip_session_id": read.hip_session_id,
            "expected_hip_fingerprint": read.hip_fingerprint,
            "base_scene_revision": read.scene_revision,
            "idempotency_key": "idempotency-key-0001",
            "deadline_ms": 10000,
            "permission_level": "scene_write",
            "graph": graph,
            "canonical_graph_digest": graph_digest(graph),
        }
        request = queue.build_request(
            "houdini_graph_apply", arguments, self.clock() + 10.0
        )
        queue.submit(request)
        presentation = queue.poll_next()
        assert presentation is not None
        queue.decide_approval(
            presentation.request_id,
            "allow",
            presentation.request_digest,
            "launch-1",
            3,
        )
        claim = queue.claim_next()
        assert claim is not None
        claim_authority = _ClaimAuthority(request, claim)
        obj = fake.node("/obj")
        obj_fingerprint = HoudiniWriteAdapter._obj_fingerprint(obj)
        binding = _ApprovedWriteBinding.from_scene_queue(
            request,
            claim,
            attestation=attestation,
            catalog=catalog,
            obj_fingerprint=obj_fingerprint,
            claim_authority=claim_authority,
        )
        adapter = HoudiniWriteAdapter(
            fake,
            read,
            capability_attestation=attestation,
            capability_catalog=catalog,
            main_thread_id=threading.get_ident(),
            clock=self.clock,
            schema_registry=self.registry,
            control_guard=guard or _Guard(),
            claim_authority=claim_authority,
            strict_event_evidence=strict_event_evidence,
        )
        return {
            "adapter": adapter,
            "attestation": attestation,
            "binding": binding,
            "catalog": catalog,
            "claim": claim,
            "claim_authority": claim_authority,
            "fake": fake,
            "graph": graph,
            "queue": queue,
            "read": read,
            "request": request,
        }

    def _assert_error(self, result: Mapping[str, Any], code: str) -> None:
        self.assertIs(result["ok"], False)
        self.assertEqual(code, result["structured_error"]["code"])
        round_tripped = json.loads(json.dumps(result, ensure_ascii=False))
        self.assertEqual(result, round_tripped)
        serialized = json.dumps(result, ensure_ascii=False).casefold()
        for marker in (
            "b4a-super-secret",
            "authorization: bearer",
            "bridge-token",
            "executor-credential",
            "auth.json",
        ):
            self.assertNotIn(marker, serialized)

    def _assert_frozen_error_output(
        self,
        state: Mapping[str, Any],
        result: Mapping[str, Any],
        code: str,
    ) -> None:
        self._assert_error(result, code)
        validated = self.registry.validate_output(
            "houdini_graph_apply", state["request"].arguments, result
        )
        self.assertEqual(result, validated)

    def _assert_sentinel_unchanged(
        self,
        fake: FakeHouWrite,
        before: Mapping[str, Any],
    ) -> None:
        self.assertEqual(before, fake.snapshot_node(fake.sentinel))

    def _assert_only_target_mutations(self, state: Mapping[str, Any]) -> None:
        target = f"/obj/{state['graph']['target']['name_hint']}"
        for mutation in state["fake"].mutation_log:
            self.assertTrue(
                mutation.path == target or mutation.path.startswith(f"{target}/"),
                f"mutation escaped approved target: {mutation}",
            )
            self.assertEqual(threading.get_ident(), mutation.thread_id)

    def test_preflight_requires_main_thread_before_any_hom_access(self) -> None:
        state = self._build()
        before = len(state["fake"].call_log)
        results: list[dict[str, Any]] = []

        def worker() -> None:
            results.append(state["adapter"].apply_prevalidated(state["binding"]))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self._assert_error(results[0], "MAIN_THREAD_REQUIRED")
        self.assertEqual(before, len(state["fake"].call_log))
        self.assertEqual([], state["fake"].mutation_log)
        self.assertEqual(0, state["fake"].undo_group_count)

    def test_fake_rejects_every_mutation_outside_an_active_undo_group(self) -> None:
        state = self._build()
        fake = state["fake"]
        sentinel_before = fake.snapshot_node(fake.sentinel)
        with self.assertRaisesRegex(AssertionError, "outside an active Undo group"):
            fake.sentinel.setDisplayFlag(True)
        self._assert_sentinel_unchanged(fake, sentinel_before)
        self.assertEqual([], fake.mutation_log)

    def test_undo_group_factory_failure_is_zero_mutation(self) -> None:
        fake = FakeHouWrite(undo_failure_point="group")
        state = self._build(fake=fake)
        sentinel_before = fake.snapshot_node(fake.sentinel)
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_frozen_error_output(state, result, "INTERNAL_ERROR")
        self.assertFalse(state["adapter"].frozen)
        self.assertEqual([], fake.mutation_log)
        self._assert_sentinel_unchanged(fake, sentinel_before)
        self.assertFalse(fake._undo_active)
        self.assertIsNone(state["read"]._active)

    def test_undo_enter_failure_is_indeterminate_and_freezes(self) -> None:
        for point in ("enter", "enter_after_active"):
            with self.subTest(point=point):
                fake = FakeHouWrite(undo_failure_point=point)
                state = self._build(fake=fake)
                sentinel_before = fake.snapshot_node(fake.sentinel)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_frozen_error_output(
                    state, result, "SCENE_STATE_INDETERMINATE"
                )
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual([], fake.mutation_log)
                self._assert_sentinel_unchanged(fake, sentinel_before)
                self.assertFalse(fake._undo_active)
                self.assertIsNone(state["read"]._active)

    def test_undo_group_exit_failure_is_indeterminate_and_freezes_writer(self) -> None:
        fake = FakeHouWrite(undo_failure_point="exit")
        state = self._build(fake=fake)
        sentinel_before = fake.snapshot_node(fake.sentinel)
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_frozen_error_output(
            state, result, "SCENE_STATE_INDETERMINATE"
        )
        self.assertTrue(state["adapter"].frozen)
        self.assertEqual(1, fake.undo_group_count)
        self.assertFalse(fake._undo_active)
        self._assert_sentinel_unchanged(fake, sentinel_before)
        self._assert_only_target_mutations(state)

    def test_normal_undo_exit_cannot_tamper_success_or_resurrect_rollback(self) -> None:
        committed = self._build()
        committed["fake"].undo_exit_tampers_success = True
        result = committed["adapter"].apply_prevalidated(
            committed["binding"]
        )
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(committed["adapter"].frozen)
        self.assertFalse(committed["fake"]._undo_active)
        self.assertEqual(1, committed["fake"].undo_group_commits)

        rolled_back = self._build()
        rolled_back["fake"].failure_phase = "set_parameters"
        rolled_back["fake"].undo_exit_resurrects_root = True
        rolled_back["adapter"]._phase_hook = rolled_back["fake"].phase_hook
        result = rolled_back["adapter"].apply_prevalidated(
            rolled_back["binding"]
        )
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(rolled_back["adapter"].frozen)
        self.assertFalse(rolled_back["fake"]._undo_active)
        root_path = f"/obj/{rolled_back['graph']['target']['name_hint']}"
        self.assertIn(root_path, rolled_back["fake"].registry_paths)
        self._assert_only_target_mutations(rolled_back)

    def test_create_node_failure_after_registration_is_contained_by_scope(self) -> None:
        root_fake = FakeHouWrite(create_node_failure_after_registration=1)
        root_state = self._build(fake=root_fake)
        sentinel_before = root_fake.snapshot_node(root_fake.sentinel)
        result = root_state["adapter"].apply_prevalidated(root_state["binding"])
        self._assert_frozen_error_output(
            root_state, result, "SCENE_STATE_INDETERMINATE"
        )
        self.assertTrue(root_state["adapter"].frozen)
        self.assertEqual([], root_fake.destroy_attempts)
        self.assertIn(
            f"/obj/{root_state['graph']['target']['name_hint']}",
            root_fake.registry_paths,
        )
        self._assert_sentinel_unchanged(root_fake, sentinel_before)
        self._assert_only_target_mutations(root_state)

        child_fake = FakeHouWrite(create_node_failure_after_registration=2)
        child_state = self._build(fake=child_fake)
        sentinel_before = child_fake.snapshot_node(child_fake.sentinel)
        result = child_state["adapter"].apply_prevalidated(child_state["binding"])
        self._assert_frozen_error_output(
            child_state, result, "SCENE_STATE_INDETERMINATE"
        )
        self.assertTrue(child_state["adapter"].frozen)
        self.assertEqual([], child_fake.destroy_attempts)
        self.assertIn(
            f"/obj/{child_state['graph']['target']['name_hint']}",
            child_fake.registry_paths,
        )
        self._assert_sentinel_unchanged(child_fake, sentinel_before)
        self._assert_only_target_mutations(child_state)

    def test_create_node_return_is_proven_before_any_followup_mutation(self) -> None:
        for mode in ("root_existing", "child_existing", "root_wrong_name"):
            with self.subTest(mode=mode):
                tamper_hooks = {"created_root_name"} if mode == "root_wrong_name" else set()
                fake = FakeHouWrite(tamper_hooks=tamper_hooks)
                state = self._build(fake=fake)
                sentinel_before = fake.snapshot_node(fake.sentinel)
                if mode == "root_existing":
                    fake.return_preexisting_root = True
                elif mode == "child_existing":
                    fake.return_preexisting_child = True

                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self._assert_sentinel_unchanged(fake, sentinel_before)
                self.assertFalse(
                    any(
                        mutation.path == "/obj/User_Sentinel"
                        for mutation in fake.mutation_log
                    )
                )
                if mode == "root_existing":
                    self.assertEqual([], fake.destroy_attempts)

    def test_stale_session_revision_and_hip_fingerprint_are_zero_mutation(self) -> None:
        cases = (
            ("hip_session_id", "hip-other", "HIP_SESSION_MISMATCH"),
            ("scene_revision", 8, "SCENE_CONFLICT"),
            ("hip_fingerprint", "b" * 64, "CAPABILITY_MISMATCH"),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                state = self._build()
                setattr(state["read"], field, value)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, code)
                self.assertEqual([], state["fake"].mutation_log)
                self.assertEqual(0, state["fake"].undo_group_count)

    def test_stale_obj_fingerprint_deadline_cancel_and_shutdown_are_zero_mutation(self) -> None:
        stale = self._build()
        stale["fake"].sentinel._resolved_type = "tampered_type"
        result = stale["adapter"].apply_prevalidated(stale["binding"])
        self._assert_error(result, "CAPABILITY_MISMATCH")
        self.assertEqual([], stale["fake"].mutation_log)

        expired = self._build()
        self.clock.value += 11.0
        result = expired["adapter"].apply_prevalidated(expired["binding"])
        self._assert_error(result, "DEADLINE_EXCEEDED")
        self.assertEqual([], expired["fake"].mutation_log)

        cancelled = self._build(guard=_Guard("cancel"))
        with self.assertRaises(WriteControlAbort) as cancel:
            cancelled["adapter"].apply_prevalidated(cancelled["binding"])
        self.assertEqual("cancel", cancel.exception.reason)
        self.assertEqual([], cancelled["fake"].mutation_log)

        shutdown = self._build(guard=_Guard("shutdown"))
        with self.assertRaises(WriteControlAbort) as stopped:
            shutdown["adapter"].apply_prevalidated(shutdown["binding"])
        self.assertEqual("shutdown", stopped.exception.reason)
        self.assertEqual([], shutdown["fake"].mutation_log)

    def test_invalid_transaction_clock_fails_before_hom_mutation(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf"), -1.0):
            with self.subTest(stage="entry", value=value):
                state = self._build()
                state["adapter"]._clock = lambda value=value: value
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "HOUDINI_UNAVAILABLE")
                self.assertEqual([], state["fake"].mutation_log)
                self.assertEqual(0, state["fake"].undo_group_count)

            with self.subTest(stage="locked_preflight", value=value):
                state = self._build()
                readings = iter((1000.0, value))
                state["adapter"]._clock = lambda: next(readings)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "HOUDINI_UNAVAILABLE")
                self.assertEqual([], state["fake"].mutation_log)
                self.assertEqual(0, state["fake"].undo_group_count)

        state = self._build()

        def broken_clock() -> float:
            raise RuntimeError("clock failure")

        state["adapter"]._clock = broken_clock
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "HOUDINI_UNAVAILABLE")
        self.assertEqual([], state["fake"].mutation_log)

    def test_final_commit_authority_linearizes_control_outcomes(self) -> None:
        rejected = self._build(guard=_Guard("cancel", phase="commit"))
        with self.assertRaises(WriteControlAbort) as stopped:
            rejected["adapter"].apply_prevalidated(rejected["binding"])
        self.assertTrue(stopped.exception.rollback_proven)
        self.assertEqual(7, rejected["read"].scene_revision)
        self.assertEqual(
            ("/", "/obj", "/obj/User_Sentinel"),
            rejected["fake"].registry_paths,
        )

        class _LateControlGuard(_Guard):
            def finalize(self, operation: Any) -> Any:
                with self._authority_lock:
                    self.checkpoint("commit")
                    result = operation()
                    # This outcome is later than the one commit authority point
                    # and therefore belongs to a subsequent transaction.
                    self.reason = "cancel"
                    self.phase = "commit"
                    return result

        committed = self._build(guard=_LateControlGuard())
        result = committed["adapter"].apply_prevalidated(committed["binding"])
        self.assertTrue(result["ok"])
        self.assertEqual(8, committed["read"].scene_revision)
        self.assertEqual(1, committed["fake"].undo_group_commits)

    def test_finalizer_must_invoke_and_return_the_exact_commit_boundary(self) -> None:
        class _SkippingGuard(_Guard):
            def finalize(self, operation: Any) -> Any:
                del operation
                with self._authority_lock:
                    return {"scene_revision": 8}

        skipped = self._build(guard=_SkippingGuard())
        result = skipped["adapter"].apply_prevalidated(skipped["binding"])
        self._assert_error(result, "HOUDINI_UNAVAILABLE")
        self.assertTrue(skipped["adapter"].frozen)
        self.assertFalse(skipped["fake"]._undo_active)
        self.assertIsNone(skipped["read"]._active)
        self.assertEqual(7, skipped["read"].scene_revision)
        self.assertEqual(
            ("/", "/obj", "/obj/User_Sentinel"),
            skipped["fake"].registry_paths,
        )

        class _ReplacingGuard(_Guard):
            def finalize(self, operation: Any) -> Any:
                with self._authority_lock:
                    return dict(operation())

        replaced = self._build(guard=_ReplacingGuard())
        result = replaced["adapter"].apply_prevalidated(replaced["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(replaced["adapter"].frozen)
        self.assertFalse(replaced["fake"]._undo_active)
        self.assertIsNone(replaced["read"]._active)
        self.assertEqual(8, replaced["read"].scene_revision)

        class _RepeatingGuard(_Guard):
            def finalize(self, operation: Any) -> Any:
                with self._authority_lock:
                    result = operation()
                    try:
                        operation()
                    except RuntimeError:
                        pass
                    return result

        repeated = self._build(guard=_RepeatingGuard())
        result = repeated["adapter"].apply_prevalidated(repeated["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(repeated["adapter"].frozen)
        self.assertFalse(repeated["fake"]._undo_active)
        self.assertIsNone(repeated["read"]._active)
        self.assertEqual(8, repeated["read"].scene_revision)
        self.assertEqual(1, repeated["fake"].undo_group_commits)
        self.assertEqual(0, repeated["fake"].undo_group_failures)
        self.assertEqual([], repeated["fake"].destroy_attempts)

    def test_missing_candidate_output_uses_unified_rollback_and_closes_undo(self) -> None:
        class _MissingSuccessOutputRegistry(SchemaRegistry):
            def validate_output(
                self,
                tool_name: str,
                arguments: Mapping[str, Any],
                output: Mapping[str, Any],
            ) -> dict[str, Any] | None:
                validated = super().validate_output(
                    tool_name, arguments, output
                )
                return None if validated.get("ok") is True else validated

        state = self._build()
        state["adapter"]._registry = _MissingSuccessOutputRegistry()
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "HOUDINI_UNAVAILABLE")
        self.assertTrue(state["adapter"].frozen)
        self.assertFalse(state["fake"]._undo_active)
        self.assertIsNone(state["read"]._active)
        self.assertEqual(
            ("/", "/obj", "/obj/User_Sentinel"),
            state["fake"].registry_paths,
        )

    def test_control_guard_must_invoke_each_mutation_boundary_exactly_once(self) -> None:
        class _SkippingMutationGuard(_Guard):
            def mutate(self, phase: str, operation: Any) -> Any:
                del phase, operation
                return object()

        skipped = self._build(guard=_SkippingMutationGuard())
        result = skipped["adapter"].apply_prevalidated(skipped["binding"])
        self._assert_error(result, "INTERNAL_ERROR")
        self.assertEqual([], skipped["fake"].mutation_log)
        self.assertEqual(0, skipped["fake"].undo_group_count)
        self.assertIsNone(skipped["read"]._active)

        class _RepeatingMutationGuard(_Guard):
            def mutate(self, phase: str, operation: Any) -> Any:
                with self._authority_lock:
                    self.checkpoint(phase)
                    result = operation()
                    try:
                        operation()
                    except RuntimeError:
                        pass
                    return result

        repeated = self._build(guard=_RepeatingMutationGuard())
        result = repeated["adapter"].apply_prevalidated(repeated["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(repeated["adapter"].frozen)
        self.assertEqual([], repeated["fake"].mutation_log)
        self.assertFalse(repeated["fake"]._undo_active)
        self.assertIsNone(repeated["read"]._active)

    def test_guard_callbacks_cannot_move_hom_work_off_main_thread(self) -> None:
        class _WorkerGuard(_Guard):
            def __init__(self, mode: str) -> None:
                super().__init__()
                self.mode = mode

            @staticmethod
            def _worker_call(operation: Any) -> Any:
                values: list[Any] = []
                failures: list[BaseException] = []

                def run() -> None:
                    try:
                        values.append(operation())
                    except BaseException as exc:
                        failures.append(exc)

                worker = threading.Thread(target=run)
                worker.start()
                worker.join(2)
                if worker.is_alive():
                    raise RuntimeError("test worker did not stop")
                if failures:
                    raise failures[0]
                return values[0]

            def mutate(self, phase: str, operation: Any) -> Any:
                if self.mode == "mutate":
                    return self._worker_call(operation)
                return super().mutate(phase, operation)

            def contain(self, operation: Any) -> Any:
                if self.mode == "contain":
                    return self._worker_call(operation)
                return super().contain(operation)

            def finalize(self, operation: Any) -> Any:
                if self.mode == "finalize":
                    return self._worker_call(operation)
                return super().finalize(operation)

        main_thread = threading.get_ident()
        for mode in ("mutate", "contain", "finalize"):
            with self.subTest(mode=mode):
                fake = FakeHouWrite(
                    failure_phase="create_nodes" if mode == "contain" else None
                )
                state = self._build(fake=fake, guard=_WorkerGuard(mode))
                if mode == "contain":
                    state["adapter"]._phase_hook = fake.phase_hook
                result = state["adapter"].apply_prevalidated(state["binding"])
                self.assertFalse(result["ok"])
                self.assertTrue(
                    all(call.thread_id == main_thread for call in fake.call_log)
                )
                self.assertTrue(
                    all(
                        mutation.thread_id == main_thread
                        for mutation in fake.mutation_log
                    )
                )

    def test_control_abort_at_every_transaction_phase_is_contained(self) -> None:
        for phase in MUTATION_PHASES:
            with self.subTest(phase=phase):
                state = self._build(guard=_Guard("cancel", phase=phase))
                sentinel_before = state["fake"].snapshot_node(
                    state["fake"].sentinel
                )
                with self.assertRaises(WriteControlAbort) as stopped:
                    state["adapter"].apply_prevalidated(state["binding"])
                self.assertTrue(stopped.exception.rollback_proven)
                self.assertEqual(7, state["read"].scene_revision)
                self.assertIsNone(state["read"]._active)
                self.assertEqual(
                    ("/", "/obj", "/obj/User_Sentinel"),
                    state["fake"].registry_paths,
                )
                self._assert_sentinel_unchanged(
                    state["fake"], sentinel_before
                )

    def test_missing_or_tampered_approval_binding_is_zero_mutation(self) -> None:
        state = self._build()
        with self.assertRaises(HoudiniWriteAdapterError) as missing:
            state["adapter"].apply_prevalidated(None)  # type: ignore[arg-type]
        self.assertEqual("APPROVAL_REQUIRED", missing.exception.code)
        tampered = replace(state["binding"], approval_binding_digest="f" * 64)
        result = state["adapter"].apply_prevalidated(tampered)
        self._assert_error(result, "APPROVAL_MISMATCH")
        self.assertEqual([], state["fake"].mutation_log)
        self.assertEqual(0, state["fake"].undo_group_count)

    def test_claim_authority_rejects_copied_claim_and_repeat_consumption(self) -> None:
        state = self._build()
        self.assertTrue(state["claim_authority"].consumed)
        obj_fingerprint = state["binding"].obj_fingerprint

        with self.assertRaises(HoudiniWriteAdapterError) as repeated:
            _ApprovedWriteBinding.from_scene_queue(
                state["request"],
                state["claim"],
                attestation=state["attestation"],
                catalog=state["catalog"],
                obj_fingerprint=obj_fingerprint,
                claim_authority=state["claim_authority"],
            )
        self.assertEqual("APPROVAL_REQUIRED", repeated.exception.code)

        copied_claim = replace(state["claim"])
        copied_authority = _ClaimAuthority(state["request"], state["claim"])
        with self.assertRaises(HoudiniWriteAdapterError) as copied:
            _ApprovedWriteBinding.from_scene_queue(
                state["request"],
                copied_claim,
                attestation=state["attestation"],
                catalog=state["catalog"],
                obj_fingerprint=obj_fingerprint,
                claim_authority=copied_authority,
            )
        self.assertEqual("APPROVAL_REQUIRED", copied.exception.code)
        self.assertFalse(copied_authority.consumed)

        cancelled_claim = replace(state["claim"], cancel_requested=True)
        cancelled_authority = _ClaimAuthority(
            state["request"], cancelled_claim
        )
        with self.assertRaises(HoudiniWriteAdapterError) as cancelled:
            _ApprovedWriteBinding.from_scene_queue(
                state["request"],
                cancelled_claim,
                attestation=state["attestation"],
                catalog=state["catalog"],
                obj_fingerprint=obj_fingerprint,
                claim_authority=cancelled_authority,
            )
        self.assertEqual("APPROVAL_MISMATCH", cancelled.exception.code)
        self.assertFalse(cancelled_authority.consumed)

    def test_approval_binding_is_one_shot_even_after_preflight_failure(self) -> None:
        state = self._build()
        state["read"].available = False
        first = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(first, "HOUDINI_UNAVAILABLE")
        state["read"].available = True
        second = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(second, "APPROVAL_REQUIRED")
        self.assertTrue(state["claim_authority"].binding_consumed)
        self.assertEqual([], state["fake"].mutation_log)
        self.assertEqual(0, state["fake"].undo_group_count)

    def test_target_conflict_missing_live_type_and_parameter_drift_are_zero_mutation(self) -> None:
        conflict = self._build(root_conflict=True)
        result = conflict["adapter"].apply_prevalidated(conflict["binding"])
        self._assert_error(result, "NAME_CONFLICT")
        self.assertEqual([], conflict["fake"].mutation_log)
        self.assertEqual(0, conflict["fake"].undo_group_count)

        missing = self._build()
        missing["read"].catalog[1]["available"] = False
        result = missing["adapter"].apply_prevalidated(missing["binding"])
        self._assert_error(result, "CAPABILITY_MISMATCH")
        self.assertEqual([], missing["fake"].mutation_log)

        drift = self._build()
        drift["read"].catalog[1]["parameters"][0]["tuple_size"] = 2
        result = drift["adapter"].apply_prevalidated(drift["binding"])
        self._assert_error(result, "CAPABILITY_MISMATCH")
        self.assertEqual([], drift["fake"].mutation_log)

        drift_cases = (
            ("category", "Other"),
            ("creatable", False),
            ("risk_level", "file_write"),
            ("parameter.items_type", "int"),
            ("parameter.writable", False),
            ("parameter.allows_expression", True),
        )
        for field, value in drift_cases:
            with self.subTest(live_catalog_field=field):
                state = self._build()
                if field.startswith("parameter."):
                    parameter_field = field.split(".", 1)[1]
                    state["read"].catalog[1]["parameters"][0][parameter_field] = value
                else:
                    state["read"].catalog[1][field] = value
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "CAPABILITY_MISMATCH")
                self.assertEqual([], state["fake"].mutation_log)

    def test_catalog_policy_rejects_unknown_and_high_risk_without_type_handlers(self) -> None:
        high_risk = certified_write_catalog()
        high_risk[1]["risk_level"] = "file_write"
        state = self._build(catalog=high_risk)
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "NODE_TYPE_NOT_ALLOWED")
        self.assertEqual([], state["fake"].mutation_log)

        source = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "houdini_write_adapter.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "ALLOWED_TYPES",
            "tabletop_box",
            "leg_front_left",
            "HIA_Table_",
            "OUT_STAIRS",
            '== "box"',
            '== "merge"',
        ):
            self.assertNotIn(forbidden, source)

        missing = certified_write_catalog()
        missing = [
            record for record in missing if record["requested_name"] != "box"
        ]
        missing_state = self._build(catalog=missing)
        missing_result = missing_state["adapter"].apply_prevalidated(
            missing_state["binding"]
        )
        self._assert_error(missing_result, "NODE_TYPE_NOT_ALLOWED")
        self.assertEqual([], missing_state["fake"].mutation_log)

    def test_catalog_resolved_names_change_without_transaction_engine_changes(self) -> None:
        catalog = certified_write_catalog()
        for index, record in enumerate(catalog):
            record["resolved_name"] = f"certified_type_{index}"
        state = self._build("stairs_graph.json", catalog=catalog)
        result = state["adapter"].apply_prevalidated(state["binding"])
        self.assertTrue(result["ok"])
        root_path = f"/obj/{state['graph']['target']['name_hint']}"
        root = state["fake"].node(root_path)
        self.assertEqual("certified_type_0", root.type().name())
        expected_by_canonical = {
            record["requested_name"]: record["resolved_name"]
            for record in catalog
        }
        for declaration in state["graph"]["nodes"]:
            live = state["fake"].node(
                f"{root_path}/{declaration['name_hint']}"
            )
            self.assertEqual(
                expected_by_canonical[declaration["type"]["name"]],
                live.type().name(),
            )

    def test_both_fixtures_use_one_generic_hom_translation_path(self) -> None:
        for fixture in ("table_graph.json", "stairs_graph.json"):
            with self.subTest(fixture=fixture):
                state = self._build(fixture)
                sentinel_before = state["fake"].snapshot_node(state["fake"].sentinel)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self.assertTrue(result["ok"])
                self.registry.validate_output(
                    "houdini_graph_apply", state["request"].arguments, result
                )
                self.assertEqual(1, state["fake"].undo_group_count)
                self.assertEqual(1, state["fake"].undo_group_commits)
                self.assertEqual(8, state["read"].scene_revision)
                self.assertEqual(1, state["read"].post_commit_refreshes)
                self.assertNotEqual(
                    state["request"].arguments["expected_hip_fingerprint"],
                    state["read"].hip_fingerprint,
                )
                self.assertEqual(
                    "HIA: Apply Graph", state["fake"].undo_labels[0]
                )
                created_roots = [
                    mutation
                    for mutation in state["fake"].mutation_log
                    if mutation.operation == "createNode"
                    and mutation.path.count("/") == 2
                ]
                self.assertEqual(1, len(created_roots))
                self.assertIn("/obj/User_Sentinel", state["fake"].registry_paths)
                self._assert_sentinel_unchanged(state["fake"], sentinel_before)
                self._assert_only_target_mutations(state)
                create_mutations = [
                    mutation
                    for mutation in state["fake"].mutation_log
                    if mutation.operation == "createNode"
                ]
                self.assertTrue(create_mutations)
                self.assertTrue(
                    all(mutation.detail[1] is False for mutation in create_mutations),
                    "root and every child must disable Houdini init scripts",
                )
                self.assertTrue(
                    all(mutation.detail[2] is True for mutation in create_mutations),
                    "root and every child must request the exact catalog-resolved type",
                )
                self.assertEqual(
                    len(state["fake"].mutation_log),
                    state["read"].callback_sources_verified,
                    "every adapter mutator must arm the exact callback-source identity",
                )

    def test_strict_stairs_apply_records_live_shaped_event_evidence(self) -> None:
        state = self._build(
            "stairs_graph.json", strict_event_evidence=True
        )

        result = state["adapter"].apply_prevalidated(state["binding"])
        evidence = state["read"].last_owned_evidence()

        self.assertTrue(result["ok"])
        assert evidence is not None
        self.assertEqual("committed", evidence["outcome"])
        self.assertEqual(6, len(evidence["observer_installations"]))
        operations = {item["operation"]: item for item in evidence["mutations"]}
        for required in (
            "create_root:root",
            "set_user_data:hia_ownership",
            "set_user_data:hia_transaction_id",
            "set_user_data:hia_graph_digest",
            "set_parameter:step_source:size",
            "connect:output:0",
            "set_flag:output:display",
            "set_flag:output:render",
        ):
            self.assertGreater(operations[required]["event_count"], 0)
        self.assertTrue(operations["set_flag:combine:display"]["no_op"])
        output_flag_events = {
            event["event_type"]
            for event in evidence["events"]
            if event["operation"] == "set_flag:output:display"
        }
        self.assertEqual(
            {"FlagChanged", "ChildSwitched"}, output_flag_events
        )
        self.assertTrue(all(event["matched"] for event in evidence["events"]))

    def test_strict_flag_evidence_accepts_owned_houdini_sibling_switch_cluster(
        self,
    ) -> None:
        fake = FakeHouWrite(
            catalog=certified_write_catalog(),
            coupled_display_flag_events=True,
        )
        state = self._build(
            "stairs_graph.json",
            fake=fake,
            strict_event_evidence=True,
        )

        result = state["adapter"].apply_prevalidated(state["binding"])
        evidence = state["read"].last_owned_evidence()

        self.assertTrue(result["ok"])
        assert evidence is not None
        events = [
            event
            for event in evidence["events"]
            if event["operation"] == "set_flag:combine:display"
        ]
        self.assertEqual(
            {"ChildSwitched", "FlagChanged"},
            {event["event_type"] for event in events},
        )
        self.assertIn(
            "/obj/HIA_Graph_stairs_demo/step_source",
            {event["child_path"] for event in events},
        )
        self.assertEqual(
            {
                "/obj/HIA_Graph_stairs_demo",
                "/obj/HIA_Graph_stairs_demo/combine_stairs",
                "/obj/HIA_Graph_stairs_demo/step_source",
            },
            {event["source_path"] for event in events},
        )
        self.assertTrue(all(event["matched"] for event in evidence["events"]))

    def test_strict_flag_evidence_requires_target_flag_event_and_readback(self) -> None:
        fake = FakeHouWrite(
            catalog=certified_write_catalog(),
            coupled_display_flag_events=True,
        )
        fake.suppressed_event_operations.add("FlagChanged")
        state = self._build(
            "stairs_graph.json",
            fake=fake,
            strict_event_evidence=True,
        )

        result = state["adapter"].apply_prevalidated(state["binding"])

        self.assertFalse(result["ok"])
        self.assertEqual(
            "SCENE_STATE_INDETERMINATE", result["structured_error"]["code"]
        )

    def test_strict_zero_wrong_late_event_and_new_observer_failure_fail_closed(
        self,
    ) -> None:
        cases = ("zero", "wrong_source", "late", "observer")
        for case in cases:
            with self.subTest(case=case):
                fake = FakeHouWrite(catalog=certified_write_catalog())
                if case == "zero":
                    fake.suppressed_event_operations.add("ParmTupleChanged")
                elif case == "wrong_source":
                    fake.event_source_overrides["CustomDataChanged"] = fake.sentinel
                elif case == "late":
                    fake.deferred_event_operations.add("ParmTupleChanged")
                else:
                    fake.reject_observer_paths.add(
                        "/obj/HIA_Graph_stairs_demo"
                    )
                state = self._build(
                    "stairs_graph.json",
                    fake=fake,
                    strict_event_evidence=True,
                )

                result = state["adapter"].apply_prevalidated(state["binding"])

                self.assertFalse(result["ok"])
                self.assertEqual(
                    "SCENE_STATE_INDETERMINATE",
                    result["structured_error"]["code"],
                )
                self.assertTrue(state["adapter"].frozen)
                self.assertIsNotNone(fake.node("/obj/HIA_Graph_stairs_demo"))
                self.assertEqual([], fake.destroy_attempts)
                if case == "late":
                    fake.flush_deferred_events()

    def test_strict_rollback_destroy_proves_all_child_deleted_subjects(self) -> None:
        fake = FakeHouWrite(
            catalog=certified_write_catalog(), failure_phase="postcondition"
        )
        state = self._build(
            "stairs_graph.json",
            fake=fake,
            strict_event_evidence=True,
        )
        state["adapter"]._phase_hook = fake.phase_hook

        result = state["adapter"].apply_prevalidated(state["binding"])
        evidence = state["read"].last_owned_evidence()

        self.assertFalse(result["ok"])
        self.assertIsNone(fake.node("/obj/HIA_Graph_stairs_demo"))
        self.assertEqual(1, len(fake.destroy_attempts))
        assert evidence is not None
        deletion_events = [
            event
            for event in evidence["events"]
            if event["operation"] == "rollback_destroy:root"
            and event["event_type"] == "ChildDeleted"
        ]
        self.assertEqual(6, len(deletion_events))
        self.assertEqual(
            {
                "/obj/HIA_Graph_stairs_demo",
                "/obj/HIA_Graph_stairs_demo/combine_stairs",
                "/obj/HIA_Graph_stairs_demo/landing",
                "/obj/HIA_Graph_stairs_demo/OUT_STAIRS",
                "/obj/HIA_Graph_stairs_demo/step_offset",
                "/obj/HIA_Graph_stairs_demo/step_source",
            },
            {event["child_path"] for event in deletion_events},
        )

    def test_resolved_type_parameters_connections_flags_and_scope_match_declaration(self) -> None:
        state = self._build("stairs_graph.json")
        result = state["adapter"].apply_prevalidated(state["binding"])
        self.assertTrue(result["ok"])
        fake = state["fake"]
        graph = state["graph"]
        root_path = f"/obj/{graph['target']['name_hint']}"
        root = fake.node(root_path)
        self.assertIsNotNone(root)
        by_name = {child.name(): child for child in root.children()}
        transform = next(
            node for node in graph["nodes"] if node["type"]["name"] == "transform"
        )
        self.assertEqual("xform", by_name[transform["name_hint"]].type().name())
        self.assertEqual((0.0, 0.25, 0.4), by_name[transform["name_hint"]].parmTuple("t").eval())
        expected_connections = len(graph["connections"])
        observed_connections = sum(
            len(child.inputConnections()) for child in root.children()
        )
        self.assertEqual(expected_connections, observed_connections)
        flagged = [
            child.name()
            for child in root.children()
            if child.isDisplayFlagSet() and child.isRenderFlagSet()
        ]
        expected_flagged = [
            node["name_hint"]
            for node in graph["nodes"]
            if node["flags"]["display"] and node["flags"]["render"]
        ]
        self.assertEqual(expected_flagged, flagged)
        self.assertTrue(
            all(
                connection.inputNode() in root.children()
                for child in root.children()
                for connection in child.inputConnections()
            )
        )
        create_types = [
            mutation.detail[0]
            for mutation in fake.mutation_log
            if mutation.operation == "createNode"
        ]
        self.assertIn("xform", create_types)

    def test_connections_are_applied_in_destination_input_order(self) -> None:
        state = self._build("stairs_graph.json")

        result = state["adapter"].apply_prevalidated(state["binding"])

        self.assertTrue(result["ok"])
        inputs_by_destination: dict[str, list[int]] = {}
        for mutation in state["fake"].mutation_log:
            if mutation.operation == "setInput":
                inputs_by_destination.setdefault(mutation.path, []).append(
                    mutation.detail[0]
                )
        self.assertTrue(inputs_by_destination)
        for input_indices in inputs_by_destination.values():
            self.assertEqual(sorted(input_indices), input_indices)

    def test_observed_state_tampering_fails_and_rolls_back_exact_root(self) -> None:
        for tamper in (
            "parameter",
            "connection",
            "connection_destination",
            "duplicate_connection",
            "display_flag",
            "render_flag",
            "cook_error",
            "root_error",
        ):
            with self.subTest(tamper=tamper):
                fake = FakeHouWrite(tamper_hooks={tamper})
                state = self._build(fake=fake)
                result = state["adapter"].apply_prevalidated(state["binding"])
                if tamper == "connection_destination":
                    self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                    self.assertTrue(state["adapter"].frozen)
                    self.assertEqual([], fake.destroy_attempts)
                else:
                    self._assert_error(result, "POSTCONDITION_FAILED")
                    self.assertEqual(("/", "/obj", "/obj/User_Sentinel"), fake.registry_paths)
                    self.assertFalse(state["adapter"].frozen)
                    self.assertEqual(7, state["read"].scene_revision)

    def test_observed_parameter_type_does_not_use_python_bool_numeric_equality(self) -> None:
        state = self._build("table_graph.json")

        def inject_bool_alias(phase: str) -> None:
            if phase != "set_flags_layout":
                return
            root_path = f"/obj/{state['graph']['target']['name_hint']}"
            root = state["fake"].node(root_path)
            for child in root.children():
                for name, value in child._parameter_values.items():
                    if not isinstance(value, tuple):
                        continue
                    items = list(value)
                    for index, item in enumerate(items):
                        if type(item) is float and item == 1.0:
                            items[index] = True
                            child._parameter_values[name] = tuple(items)
                            return
            self.fail("fixture has no float 1.0 value for strict-type test")

        state["adapter"]._phase_hook = inject_bool_alias
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "POSTCONDITION_FAILED")
        self.assertEqual(
            ("/", "/obj", "/obj/User_Sentinel"),
            state["fake"].registry_paths,
        )

    def test_equal_path_child_wrapper_cannot_replace_exact_identity(self) -> None:
        fake = FakeHouWrite(path_equality=True)
        state = self._build(fake=fake)

        def replace_one_child(phase: str) -> None:
            if phase != "set_flags_layout":
                return
            root_path = f"/obj/{state['graph']['target']['name_hint']}"
            root = fake.node(root_path)
            child = root.children()[0]
            callback = fake._mutation_callback
            fake._mutation_callback = None
            try:
                replacement = fake.replace_node_identity(child.path())
            finally:
                fake._mutation_callback = callback
            self.assertIsNot(child, replacement)
            self.assertEqual(child, replacement)

        state["adapter"]._phase_hook = replace_one_child
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(state["adapter"].frozen)
        self.assertEqual([], fake.destroy_attempts)

    def test_distinct_wrappers_for_one_hom_node_pass_scope_identity(self) -> None:
        fake = FakeHouWrite(path_equality=True)
        canonical = fake.sentinel
        duplicate = fake.duplicate_node_wrapper(canonical.path())

        self.assertIsNot(canonical, duplicate)
        self.assertEqual(canonical, duplicate)
        self.assertEqual(canonical.sessionId(), duplicate.sessionId())
        self.assertTrue(_same_identity_members((duplicate,), (canonical,)))
        self.assertEqual(
            "sentinel",
            _identity_lookup(((canonical, "sentinel"),), duplicate),
        )
        self.assertTrue(_contains_identity((canonical,), duplicate))

        wrong_session = fake.duplicate_node_wrapper(canonical.path())
        wrong_session._session_id += 1
        self.assertFalse(_same_identity_members((wrong_session,), (canonical,)))
        self.assertIsNone(
            _identity_lookup(((canonical, "sentinel"),), wrong_session)
        )
        self.assertFalse(_contains_identity((canonical,), wrong_session))

    def test_full_apply_accepts_fresh_lookup_wrappers(self) -> None:
        fake = FakeHouWrite(path_equality=True)
        canonical_lookup = fake.node

        def lookup_with_fresh_wrapper(path: str) -> Any:
            canonical = canonical_lookup(path)
            if canonical is None:
                return None
            return fake.duplicate_node_wrapper(path)

        fake.node = lookup_with_fresh_wrapper  # type: ignore[method-assign]
        state = self._build(fake=fake)

        result = state["adapter"].apply_prevalidated(state["binding"])

        self.assertTrue(result["ok"])
        self.assertEqual(1, fake.undo_group_commits)
        self.assertFalse(state["adapter"].frozen)

    def test_detached_root_cannot_pass_observed_scope_or_be_blindly_destroyed(self) -> None:
        state = self._build()

        def detach_root(phase: str) -> None:
            if phase != "set_flags_layout":
                return
            root_path = f"/obj/{state['graph']['target']['name_hint']}"
            root = state["fake"].node(root_path)
            obj = state["fake"].node("/obj")
            obj._children = [child for child in obj._children if child is not root]

        state["adapter"]._phase_hook = detach_root
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(state["adapter"].frozen)
        root_path = f"/obj/{state['graph']['target']['name_hint']}"
        self.assertIn(root_path, state["fake"].registry_paths)
        self.assertEqual([], state["fake"].destroy_attempts)

    def test_root_path_alias_cannot_pass_observed_scope_or_be_blindly_destroyed(self) -> None:
        state = self._build()

        def alias_root_path(phase: str) -> None:
            if phase != "set_flags_layout":
                return
            root_path = f"/obj/{state['graph']['target']['name_hint']}"
            state["fake"]._registry[root_path] = state["fake"].sentinel

        state["adapter"]._phase_hook = alias_root_path
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(state["adapter"].frozen)
        self.assertEqual([], state["fake"].destroy_attempts)
        root_path = f"/obj/{state['graph']['target']['name_hint']}"
        self.assertIs(state["fake"].node(root_path), state["fake"].sentinel)

    def test_parent_registry_and_preexisting_metadata_drift_never_commit(self) -> None:
        for mode in ("parent_registry", "sentinel_metadata"):
            with self.subTest(mode=mode):
                state = self._build()

                def tamper_parent(phase: str) -> None:
                    if phase != "set_flags_layout":
                        return
                    if mode == "parent_registry":
                        state["fake"]._registry["/obj"] = state["fake"].sentinel
                    else:
                        state["fake"].sentinel._name = "User_Sentinel_drift"

                state["adapter"]._phase_hook = tamper_parent
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual([], state["fake"].destroy_attempts)

    def test_child_path_alias_and_session_replacement_fail_exact_observation(self) -> None:
        for mode in ("path_alias", "session"):
            with self.subTest(mode=mode):
                state = self._build()

                def tamper_child(phase: str) -> None:
                    if phase != "set_flags_layout":
                        return
                    root_path = f"/obj/{state['graph']['target']['name_hint']}"
                    root = state["fake"].node(root_path)
                    child = root.children()[0]
                    if mode == "path_alias":
                        state["fake"]._registry[child.path()] = state["fake"].sentinel
                    else:
                        child._session_id += 10_000

                state["adapter"]._phase_hook = tamper_child
                result = state["adapter"].apply_prevalidated(state["binding"])
                if mode == "path_alias":
                    self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                    self.assertTrue(state["adapter"].frozen)
                    self.assertEqual([], state["fake"].destroy_attempts)
                else:
                    self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                    self.assertTrue(state["adapter"].frozen)
                    self.assertEqual([], state["fake"].destroy_attempts)

    def test_external_connection_cannot_pass_or_rollback_blindly(self) -> None:
        for direction in ("owned_to_user", "user_to_owned"):
            with self.subTest(direction=direction):
                state = self._build()

                def connect_to_sentinel(phase: str) -> None:
                    if phase != "set_flags_layout":
                        return
                    root_path = f"/obj/{state['graph']['target']['name_hint']}"
                    owned = state["fake"].node(root_path).children()[0]
                    if direction == "owned_to_user":
                        state["fake"].sentinel._inputs[0] = FakeConnection(
                            owned, state["fake"].sentinel, 0, 0
                        )
                    else:
                        owned._inputs[999] = FakeConnection(
                            state["fake"].sentinel, owned, 0, 999
                        )

                state["adapter"]._phase_hook = connect_to_sentinel
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual([], state["fake"].destroy_attempts)

    def test_ownership_and_extra_child_tampering_are_observed_not_request_echo(self) -> None:
        for mode in ("ownership", "extra"):
            with self.subTest(mode=mode):
                state = self._build()

                def hook(phase: str) -> None:
                    if phase != "set_flags_layout":
                        return
                    root_path = f"/obj/{state['graph']['target']['name_hint']}"
                    root = state["fake"].node(root_path)
                    if mode == "ownership":
                        root._user_data["hia_ownership"] = "tampered"
                    else:
                        callback = state["fake"]._mutation_callback
                        state["fake"]._mutation_callback = None
                        try:
                            state["fake"].inject_extra_child(root)
                        finally:
                            state["fake"]._mutation_callback = callback

                state["adapter"]._phase_hook = hook
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                root_path = f"/obj/{state['graph']['target']['name_hint']}"
                self.assertIn(root_path, state["fake"].registry_paths)
                self.assertEqual([], state["fake"].destroy_attempts)

    def test_every_transaction_phase_failure_rolls_back_and_preserves_sentinel(self) -> None:
        for phase in MUTATION_PHASES:
            with self.subTest(phase=phase):
                fake = FakeHouWrite(failure_phase=phase)
                state = self._build(fake=fake)
                state["adapter"]._phase_hook = fake.phase_hook
                before_session = fake.sentinel.sessionId()
                sentinel_before = fake.snapshot_node(fake.sentinel)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_frozen_error_output(state, result, "INTERNAL_ERROR")
                self.assertEqual(("/", "/obj", "/obj/User_Sentinel"), fake.registry_paths)
                self.assertEqual(before_session, fake.sentinel.sessionId())
                self._assert_sentinel_unchanged(fake, sentinel_before)
                self._assert_only_target_mutations(state)
                self.assertEqual(7, state["read"].scene_revision)
                self.assertEqual(1, fake.undo_group_count)

    def test_identity_replacement_and_rollback_exception_freeze_without_blind_delete(self) -> None:
        replaced = self._build()
        replacement_holder: list[Any] = []

        def replace_then_fail(phase: str) -> None:
            if phase == "create_nodes":
                path = f"/obj/{replaced['graph']['target']['name_hint']}"
                replacement_holder.append(replaced["fake"].replace_node_identity(path))
                raise RuntimeError("force rollback")

        replaced["adapter"]._phase_hook = replace_then_fail
        result = replaced["adapter"].apply_prevalidated(replaced["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(replaced["adapter"].frozen)
        self.assertIs(replacement_holder[0], replaced["fake"].node(replacement_holder[0].path()))
        mutation_count = len(replaced["fake"].mutation_log)
        again = replaced["adapter"].apply_prevalidated(replaced["binding"])
        self._assert_error(again, "SCENE_STATE_INDETERMINATE")
        self.assertEqual(mutation_count, len(replaced["fake"].mutation_log))

        broken = self._build()
        broken["fake"].destroy_raises = True
        broken["fake"].failure_phase = "create_nodes"
        broken["adapter"]._phase_hook = broken["fake"].phase_hook
        result = broken["adapter"].apply_prevalidated(broken["binding"])
        self._assert_frozen_error_output(
            broken, result, "SCENE_STATE_INDETERMINATE"
        )
        self.assertTrue(broken["adapter"].frozen)
        self.assertEqual(
            [f"/obj/{broken['graph']['target']['name_hint']}"],
            broken["fake"].destroy_attempts,
        )

    def test_rollback_requires_path_absence_and_parent_fingerprint_restoration(self) -> None:
        for mode in ("retained_registry", "fingerprint_drift"):
            with self.subTest(mode=mode):
                fake = FakeHouWrite(failure_phase="set_parameters")
                if mode == "retained_registry":
                    fake.destroy_retains_registry = True
                else:
                    fake.destroy_drifts_parent_fingerprint = True
                state = self._build(fake=fake)
                state["adapter"]._phase_hook = fake.phase_hook
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual(1, len(fake.destroy_attempts))

    def test_each_rollback_identity_mismatch_blocks_destroy_and_freezes(self) -> None:
        def tamper(root: Any, fake: FakeHouWrite, mode: str) -> None:
            if mode == "session":
                root._session_id += 1000
            elif mode == "parent":
                root._parent = fake.sentinel
            elif mode == "path":
                root._path = f"{root._path}_tampered"
            elif mode == "name":
                root._name = f"{root._name}_tampered"
            elif mode == "transaction":
                root._user_data["hia_transaction_id"] = "tampered"
            elif mode == "digest":
                root._user_data["hia_graph_digest"] = "0" * 64
            else:  # pragma: no cover - test table is closed below
                raise AssertionError(mode)

        for mode in ("session", "parent", "path", "name", "transaction", "digest"):
            with self.subTest(mode=mode):
                state = self._build()

                def fail_after_tamper(phase: str, *, mode: str = mode) -> None:
                    if phase != "create_nodes":
                        return
                    root_path = f"/obj/{state['graph']['target']['name_hint']}"
                    root = state["fake"].node(root_path)
                    tamper(root, state["fake"], mode)
                    raise RuntimeError("force exact rollback proof")

                state["adapter"]._phase_hook = fail_after_tamper
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_frozen_error_output(
                    state, result, "SCENE_STATE_INDETERMINATE"
                )
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual([], state["fake"].destroy_attempts)

    def test_error_output_is_schema_valid_json_safe_and_redacts_exception_secrets(self) -> None:
        state = self._build()

        def secret_failure(phase: str) -> None:
            if phase == "create_nodes":
                raise RuntimeError(
                    "Authorization: Bearer b4a-super-secret bridge-token "
                    "executor-credential auth.json"
                )

        state["adapter"]._phase_hook = secret_failure
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_frozen_error_output(state, result, "INTERNAL_ERROR")
        self.assertEqual(("/", "/obj", "/obj/User_Sentinel"), state["fake"].registry_paths)

    def test_keyboard_interrupt_and_system_exit_contain_then_reraise(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type.__name__):
                state = self._build()

                def hook(phase: str) -> None:
                    if phase == "create_nodes":
                        raise exception_type("injected")

                state["adapter"]._phase_hook = hook
                with self.assertRaises(exception_type):
                    state["adapter"].apply_prevalidated(state["binding"])
                self.assertEqual(("/", "/obj", "/obj/User_Sentinel"), state["fake"].registry_paths)
                self.assertEqual(7, state["read"].scene_revision)

    def test_post_commit_base_exception_freezes_before_reraise(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type.__name__):
                catalog = certified_write_catalog()

                class _PostCommitInterruptRead(_WriteReadState):
                    def __init__(self) -> None:
                        super().__init__(catalog)
                        self.raise_after_refresh = False

                    def refresh(self) -> dict[str, Any]:
                        report = super().refresh()
                        if self.post_commit_refreshes == 1:
                            self.raise_after_refresh = True
                        return report

                    def capability_report(self) -> dict[str, Any]:
                        if self.raise_after_refresh:
                            self.raise_after_refresh = False
                            raise exception_type("post-commit observation interrupted")
                        return super().capability_report()

                read = _PostCommitInterruptRead()
                fake = FakeHouWrite(catalog=catalog)
                fake._mutation_callback = read.on_mutation
                state = self._build(fake=fake, catalog=catalog, read=read)
                with self.assertRaises(exception_type):
                    state["adapter"].apply_prevalidated(state["binding"])
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual(8, read.scene_revision)
                self.assertEqual(1, fake.undo_group_commits)
                self.assertFalse(fake._undo_active)
                root_path = f"/obj/{state['graph']['target']['name_hint']}"
                self.assertIn(root_path, fake.registry_paths)

    def test_committed_report_base_exception_freezes_before_reraise(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type.__name__):
                catalog = certified_write_catalog()

                class _CriticalReport(dict[str, Any]):
                    def get(self, key: str, default: Any = None) -> Any:
                        if key == "hip_fingerprint":
                            raise exception_type(
                                "committed report access interrupted"
                            )
                        return super().get(key, default)

                class _CriticalPublicationRead(_WriteReadState):
                    def finish_owned_write(
                        self, token: object, *, outcome: str
                    ) -> dict[str, Any]:
                        report = super().finish_owned_write(
                            token, outcome=outcome
                        )
                        return _CriticalReport(report)

                read = _CriticalPublicationRead(catalog)
                fake = FakeHouWrite(catalog=catalog)
                fake._mutation_callback = read.on_mutation
                state = self._build(fake=fake, catalog=catalog, read=read)
                with self.assertRaises(exception_type):
                    state["adapter"].apply_prevalidated(state["binding"])
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual(8, read.scene_revision)
                self.assertEqual(1, fake.undo_group_commits)

    def test_failed_commit_refresh_is_not_retried(self) -> None:
        catalog = certified_write_catalog()

        class _OneFailedCommitRefresh(_WriteReadState):
            def __init__(self) -> None:
                super().__init__(catalog)
                self.failed_commit_refreshes = 0

            def refresh(self) -> dict[str, Any]:
                report = super().refresh()
                if self.post_commit_refreshes == 1:
                    self.failed_commit_refreshes += 1
                    raise RuntimeError("injected commit refresh failure")
                return report

        read = _OneFailedCommitRefresh()
        fake = FakeHouWrite(catalog=catalog)
        fake._mutation_callback = read.on_mutation
        state = self._build(fake=fake, catalog=catalog, read=read)
        result = state["adapter"].apply_prevalidated(state["binding"])
        self._assert_error(result, "SCENE_STATE_INDETERMINATE")
        self.assertTrue(state["adapter"].frozen)
        self.assertEqual(1, read.failed_commit_refreshes)

    def test_exact_commit_report_must_match_independent_capability_report(self) -> None:
        for mode in ("published", "available", "session", "catalog"):
            with self.subTest(mode=mode):
                catalog = certified_write_catalog()

                class _TamperedCommitReportRead(_WriteReadState):
                    def __init__(self) -> None:
                        super().__init__(catalog)
                        self.tampered = False

                    def finish_owned_write(
                        self, token: object, *, outcome: str
                    ) -> dict[str, Any]:
                        report = super().finish_owned_write(
                            token, outcome=outcome
                        )
                        if mode == "published":
                            report = copy.deepcopy(report)
                            report["available"] = False
                        return report

                    def refresh(self) -> dict[str, Any]:
                        report = super().refresh()
                        if (
                            mode != "published"
                            and self.post_commit_refreshes == 1
                            and not self.tampered
                        ):
                            self.tampered = True
                            report = copy.deepcopy(report)
                            if mode == "available":
                                report["available"] = False
                            elif mode == "session":
                                report["hip_session_id"] = "hip-other"
                            else:
                                report["catalog"] = []
                        return report

                read = _TamperedCommitReportRead()
                fake = FakeHouWrite(catalog=catalog)
                fake._mutation_callback = read.on_mutation
                state = self._build(fake=fake, catalog=catalog, read=read)
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual(8, read.scene_revision)
                self.assertEqual(1, fake.undo_group_commits)

    def test_rollback_publication_must_match_base_and_independent_report(self) -> None:
        for mode in ("available", "session", "catalog", "revision"):
            with self.subTest(mode=mode):
                catalog = certified_write_catalog()

                class _TamperedRollbackReportRead(_WriteReadState):
                    def finish_owned_write(
                        self, token: object, *, outcome: str
                    ) -> dict[str, Any]:
                        report = super().finish_owned_write(token, outcome=outcome)
                        if outcome != "rolled_back":
                            return report
                        if mode == "revision":
                            self.scene_revision += 1
                            self.hip_fingerprint = canonical_json_sha256(
                                {
                                    "previous": self.hip_fingerprint,
                                    "scene_revision": self.scene_revision,
                                }
                            )
                            return self.capability_report()
                        report = copy.deepcopy(report)
                        if mode == "available":
                            report["available"] = False
                        elif mode == "session":
                            report["hip_session_id"] = "hip-other"
                        else:
                            report["catalog"] = []
                        return report

                read = _TamperedRollbackReportRead(catalog)
                fake = FakeHouWrite(catalog=catalog, failure_phase="set_parameters")
                fake._mutation_callback = read.on_mutation
                state = self._build(fake=fake, catalog=catalog, read=read)
                state["adapter"]._phase_hook = fake.phase_hook
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)

    def test_indeterminate_publication_refreshes_retained_observer_scope(self) -> None:
        for mode in ("normal", "clears_then_raises", "stale_then_raises"):
            with self.subTest(mode=mode):
                catalog = certified_write_catalog()

                class _IndeterminateRefreshRead(_WriteReadState):
                    def __init__(self) -> None:
                        super().__init__(catalog)
                        self.awaiting_refresh = False
                        self.indeterminate_refreshes = 0

                    def finish_owned_write(
                        self, token: object, *, outcome: str
                    ) -> dict[str, Any]:
                        if outcome == "indeterminate":
                            self.awaiting_refresh = True
                            if mode == "stale_then_raises":
                                raise RuntimeError(
                                    "publication failed before state advance"
                                )
                        report = super().finish_owned_write(
                            token, outcome=outcome
                        )
                        if outcome == "indeterminate" and mode == "clears_then_raises":
                            raise RuntimeError(
                                "publication cleared state then failed"
                            )
                        return report

                    def refresh(self) -> dict[str, Any]:
                        report = super().refresh()
                        if self.awaiting_refresh:
                            self.awaiting_refresh = False
                            self.indeterminate_refreshes += 1
                        return report

                read = _IndeterminateRefreshRead()
                fake = FakeHouWrite(
                    catalog=catalog, failure_phase="set_parameters"
                )
                fake.destroy_raises = True
                fake._mutation_callback = read.on_mutation
                state = self._build(fake=fake, catalog=catalog, read=read)
                state["adapter"]._phase_hook = fake.phase_hook
                result = state["adapter"].apply_prevalidated(state["binding"])
                self._assert_error(result, "SCENE_STATE_INDETERMINATE")
                self.assertTrue(state["adapter"].frozen)
                self.assertEqual(1, read.indeterminate_refreshes)
                self.assertGreaterEqual(
                    result["scene_revision"],
                    state["request"].arguments["base_scene_revision"] + 1,
                )

    def test_internal_base_exception_is_contained_and_reraised(self) -> None:
        fake = FakeHouWrite(base_exception_phase="create_nodes")
        state = self._build(fake=fake)
        state["adapter"]._phase_hook = fake.phase_hook
        with self.assertRaises(InjectedHouBaseException):
            state["adapter"].apply_prevalidated(state["binding"])
        self.assertEqual(("/", "/obj", "/obj/User_Sentinel"), fake.registry_paths)

    def test_rollback_base_exception_is_contained_then_reraised(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type.__name__):
                fake = FakeHouWrite(failure_phase="set_parameters")
                state = self._build(fake=fake)
                fake.destroy_base_exception = exception_type(
                    "injected rollback boundary interruption"
                )
                state["adapter"]._phase_hook = fake.phase_hook
                with self.assertRaises(exception_type):
                    state["adapter"].apply_prevalidated(state["binding"])
                self.assertTrue(state["adapter"].frozen)
                self.assertFalse(fake._undo_active)
                self.assertIsNone(state["read"]._active)
                self.assertEqual(8, state["read"].scene_revision)

    def test_nonblocking_writer_defense_and_queue_idempotency_remain_separate(self) -> None:
        state = self._build()
        self.assertTrue(state["adapter"]._writer_lock.acquire(blocking=False))
        try:
            result = state["adapter"].apply_prevalidated(state["binding"])
        finally:
            state["adapter"]._writer_lock.release()
        self._assert_error(result, "WRITE_IN_PROGRESS")
        self.assertEqual([], state["fake"].mutation_log)
        self.assertFalse(hasattr(state["adapter"], "_idempotency"))
        with self.assertRaises(SceneQueueError) as conflict:
            changed = copy.deepcopy(state["request"].arguments)
            changed["turn_id"] = "turn-changed"
            state["queue"].build_request(
                "houdini_graph_apply", changed, self.clock() + 10.0
            )
        self.assertIn(conflict.exception.code, {"WRITE_IN_PROGRESS", "IDEMPOTENCY_CONFLICT"})

    def test_source_and_production_wiring_remain_dormant_and_deny_by_default(self) -> None:
        adapter_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "houdini_write_adapter.py"
        )
        tree = ast.parse(adapter_path.read_text(encoding="utf-8"))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")
        self.assertFalse(
            any(name == "hou" or name.startswith("hou.") for name in imported_modules),
            "both import hou and from hou import ... must remain impossible",
        )
        self.assertFalse(
            any(name.startswith("PySide") or name.startswith("Qt") for name in imported_modules)
        )
        self.assertTrue(
            {
                "os",
                "pathlib",
                "shutil",
                "tempfile",
                "subprocess",
                "multiprocessing",
                "socket",
            }.isdisjoint(imported_modules),
            "dormant adapter must not gain environment, filesystem, process, or socket imports",
        )
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        direct_calls = {
            node.func.id for node in calls if isinstance(node.func, ast.Name)
        }
        attribute_calls = {
            node.func.attr for node in calls if isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            {
                "exec",
                "eval",
                "compile",
                "open",
                "Thread",
                "Process",
                "Popen",
                "getenv",
            }.isdisjoint(direct_calls)
        )
        self.assertTrue(
            {
                "Thread",
                "Process",
                "Popen",
                "socket",
                "getenv",
                "putenv",
                "unsetenv",
                "read_text",
                "write_text",
                "read_bytes",
                "write_bytes",
                "mkdir",
                "unlink",
                "rename",
                "replace",
                "rmdir",
                "touch",
            }.isdisjoint(attribute_calls)
        )
        self.assertFalse(
            any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "main"
                for node in ast.walk(tree)
            )
        )
        self.assertFalse(
            any(isinstance(node, ast.Name) and node.id == "__name__" for node in ast.walk(tree)),
            "the dormant module must not expose an __main__ entry path",
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Attribute)
                and node.attr in {"environ", "environb"}
                for node in ast.walk(tree)
            )
        )

        production_files = [
            *list((REPOSITORY_ROOT / "services").rglob("*.py")),
            REPOSITORY_ROOT / "houdini_package" / "python_libs" / "hia_panel" / "panel.py",
            REPOSITORY_ROOT / "houdini_package" / "python_libs" / "hia_panel" / "__init__.py",
        ]
        self.assertTrue(
            all(
                "houdini_write_adapter" not in path.read_text(encoding="utf-8")
                for path in production_files
            )
        )
        mcp = HoudiniMCPAdapter.b2_read_only(
            _NoopTransport(), registry=SchemaRegistry.b2_read_only()
        )
        self.assertEqual(
            {"houdini_scene_info", "houdini_node_type_info"},
            set(mcp.tool_names),
        )
        mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "b4a-offline", "version": "1"},
                },
            }
        )
        mcp.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        for name in (
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        ):
            response = mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": name,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                }
            )
            self.assertEqual("TOOL_NOT_ALLOWED", response["error"]["data"]["code"])


if __name__ == "__main__":
    unittest.main()
