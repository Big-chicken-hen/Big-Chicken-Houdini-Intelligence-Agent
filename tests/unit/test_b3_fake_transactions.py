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
from typing import Any, Iterator


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "tests" / "fakes"))

from fake_b3_harness import GateB3OfflineHarness  # noqa: E402
from fake_scene_executor import (  # noqa: E402
    MUTATION_BOUNDARIES,
    ROLLBACK_TAMPER_POINTS,
    UNEXPECTED_FAILURE_POINTS,
    FakeSceneExecutor,
)
from hia_bridge.scene_queue import SceneQueueError  # noqa: E402
from hia_core.houdini_contract import (  # noqa: E402
    ContractError,
    SchemaRegistry,
    canonical_json_sha256,
)


class _Clock:
    def __init__(self, value: float = 10_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _walk_leaves(value: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk_leaves(value[key], path + (key,))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_leaves(item, path + (index,))
        return
    yield path


def _replace_leaf(value: Any, path: tuple[Any, ...]) -> Any:
    changed = copy.deepcopy(value)
    parent = changed
    for component in path[:-1]:
        parent = parent[component]
    key = path[-1]
    leaf = parent[key]
    if isinstance(leaf, bool):
        replacement = not leaf
    elif isinstance(leaf, int):
        replacement = leaf + 1
    elif isinstance(leaf, float):
        replacement = leaf + 0.125
    elif isinstance(leaf, str):
        replacement = leaf + "_changed"
    elif leaf is None:
        replacement = "changed"
    else:  # pragma: no cover - the closed approval payload has JSON scalars only
        raise AssertionError(f"unsupported approval leaf: {type(leaf).__name__}")
    parent[key] = replacement
    return changed


class GateB3FakeTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SchemaRegistry()
        self.executor = FakeSceneExecutor(
            hip_session_id="b3-hip-session",
            hip_fingerprint="ab" * 32,
        )
        self.harness = GateB3OfflineHarness(
            self.executor,
            registry=self.registry,
        )

    @staticmethod
    def _fixture(name: str) -> dict[str, Any]:
        path = REPOSITORY_ROOT / "tests" / "fixtures" / "p2_v" / name
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _arguments(
        executor: FakeSceneExecutor,
        tool_name: str,
        suffix: str,
        *,
        graph: dict[str, Any] | None = None,
        digest: str | None = None,
        root_path: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "request_id": f"request-{suffix}",
            "thread_id": f"thread-{suffix}",
            "turn_id": f"turn-{suffix}",
            "hip_session_id": executor.hip_session_id,
            "base_scene_revision": executor.scene_revision,
            "idempotency_key": f"idempotency-{suffix}-0001",
            "deadline_ms": 30_000,
            "permission_level": (
                "scene_write" if tool_name == "houdini_graph_apply" else "scene_read"
            ),
        }
        if tool_name in {
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
        }:
            arguments["expected_hip_fingerprint"] = executor.hip_fingerprint
        if graph is not None:
            arguments["graph"] = copy.deepcopy(graph)
        if tool_name == "houdini_graph_apply":
            arguments["canonical_graph_digest"] = digest
        if tool_name == "houdini_graph_verify":
            arguments["root_path"] = root_path
            arguments["expected_graph_digest"] = digest
        return arguments

    def _validate(
        self,
        harness: GateB3OfflineHarness,
        executor: FakeSceneExecutor,
        graph: dict[str, Any],
        suffix: str,
    ) -> dict[str, Any]:
        snapshot = harness.run_read(
            "houdini_graph_validate",
            self._arguments(
                executor,
                "houdini_graph_validate",
                f"{suffix}-validate",
                graph=graph,
            ),
        )
        self.assertEqual("completed", snapshot.state)
        self.assertIsNotNone(snapshot.result)
        assert snapshot.result is not None
        self.assertTrue(snapshot.result["ok"])
        return snapshot.result["result"]

    def _present_apply(
        self,
        harness: GateB3OfflineHarness,
        executor: FakeSceneExecutor,
        graph: dict[str, Any],
        suffix: str,
        *,
        absolute_deadline: float | None = None,
    ) -> tuple[Any, Any, dict[str, Any]]:
        validation = self._validate(harness, executor, graph, suffix)
        arguments = self._arguments(
            executor,
            "houdini_graph_apply",
            f"{suffix}-apply",
            graph=validation["normalized_graph"],
            digest=validation["canonical_graph_digest"],
        )
        request, _, presentation = harness.present_apply(
            arguments,
            absolute_deadline=absolute_deadline,
        )
        return request, presentation, validation

    def _verify(
        self,
        harness: GateB3OfflineHarness,
        executor: FakeSceneExecutor,
        root_path: str,
        digest: str,
        suffix: str,
    ) -> Any:
        return harness.run_read(
            "houdini_graph_verify",
            self._arguments(
                executor,
                "houdini_graph_verify",
                f"{suffix}-verify",
                root_path=root_path,
                digest=digest,
            ),
        )

    def _claim_apply(
        self,
        harness: GateB3OfflineHarness,
        presentation: Any,
    ) -> Any:
        decision = harness.decide(presentation, "allow")
        self.assertEqual("queued", decision.state)
        work = harness.poll()
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual("execute", work.kind)
        return work

    def _start_execution(
        self,
        harness: GateB3OfflineHarness,
        work: Any,
    ) -> tuple[threading.Thread, list[Any], list[BaseException]]:
        results: list[Any] = []
        errors: list[BaseException] = []

        def run() -> None:
            try:
                results.append(harness.execute_work(work))
            except BaseException as exc:  # asserted by each caller
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        return worker, results, errors

    def test_validate_is_an_absolute_zero_scene_change(self) -> None:
        before = (
            self.executor.scene_content_digest,
            self.executor.sentinel_digest,
            self.executor.scene_revision,
            self.executor.hip_fingerprint,
            self.executor.undo_depth,
            copy.deepcopy(self.executor.observed_scene),
        )
        validation = self._validate(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "zero-change",
        )
        self.assertTrue(validation["valid"])
        self.assertFalse(validation["scene_mutated"])
        after = (
            self.executor.scene_content_digest,
            self.executor.sentinel_digest,
            self.executor.scene_revision,
            self.executor.hip_fingerprint,
            self.executor.undo_depth,
            self.executor.observed_scene,
        )
        self.assertEqual(before, after)

    def test_both_general_fixtures_run_the_same_approved_transaction_and_undo(self) -> None:
        observed_shapes: list[tuple[int, int]] = []
        for index, fixture_name in enumerate(
            ("table_graph.json", "stairs_graph.json"), start=1
        ):
            with self.subTest(fixture=fixture_name):
                executor = FakeSceneExecutor(
                    hip_session_id=f"fixture-session-{index}",
                    hip_fingerprint=(f"{index:02x}" * 32),
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                before_content = executor.scene_content_digest
                before_sentinel = executor.sentinel_digest
                before_fingerprint = executor.hip_fingerprint
                before_revision = executor.scene_revision
                request, presentation, validation = self._present_apply(
                    harness,
                    executor,
                    self._fixture(fixture_name),
                    f"fixture-{index}",
                )
                self.assertEqual(
                    request.approval_binding_digest,
                    presentation.approval_binding_digest,
                )
                applied = harness.allow_and_execute(presentation)
                self.assertEqual("completed", applied.state)
                self.assertIsNotNone(applied.result)
                assert applied.result is not None
                self.assertTrue(applied.result["ok"])
                result = applied.result["result"]
                self.assertEqual(before_revision + 1, executor.scene_revision)
                self.assertEqual(1, executor.undo_depth)
                self.assertEqual(1, executor.internal_postcondition_count)
                self.assertEqual(tuple(MUTATION_BOUNDARIES), executor.last_transaction_trace)
                self.assertEqual(before_sentinel, executor.sentinel_digest)

                verified = self._verify(
                    harness,
                    executor,
                    result["root_path"],
                    validation["canonical_graph_digest"],
                    f"fixture-{index}",
                )
                self.assertIsNotNone(verified.result)
                assert verified.result is not None
                self.assertTrue(verified.result["ok"])
                self.assertTrue(verified.result["result"]["valid"])
                observed_shapes.append(
                    (
                        len(verified.result["result"]["nodes"]),
                        len(verified.result["result"]["connections"]),
                    )
                )

                undo = executor.simulate_undo()
                self.assertEqual("OK", undo["result_code"])
                self.assertEqual(0, executor.undo_depth)
                self.assertEqual({}, executor.graphs)
                self.assertEqual(before_content, executor.scene_content_digest)
                self.assertEqual(before_sentinel, executor.sentinel_digest)
                self.assertEqual(before_fingerprint, executor.hip_fingerprint)
                self.assertEqual(before_revision + 2, executor.scene_revision)
        self.assertNotEqual(observed_shapes[0], observed_shapes[1])

    def test_approval_allow_deny_dismiss_expiry_disconnect_reuse_and_mismatch(self) -> None:
        graph = self._fixture("stairs_graph.json")

        request, presentation, _ = self._present_apply(
            self.harness, self.executor, graph, "allow"
        )
        before = self.executor.scene_content_digest
        applied = self.harness.allow_and_execute(presentation)
        self.assertTrue(applied.result["ok"])
        self.assertNotEqual(before, self.executor.scene_content_digest)
        execution_count = self.executor.apply_execution_count
        replay = self.harness.replay(request)
        self.assertTrue(replay.replayed)
        self.assertEqual(execution_count, self.executor.apply_execution_count)

        for decision in ("deny", "dismiss", "disconnect"):
            with self.subTest(decision=decision):
                executor = FakeSceneExecutor(
                    hip_session_id=f"decision-{decision}",
                    hip_fingerprint="cd" * 32,
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, candidate, _ = self._present_apply(
                    harness, executor, graph, decision
                )
                unchanged = executor.scene_content_digest
                if decision == "deny":
                    terminal = harness.decide(candidate, "deny")
                    self.assertEqual("denied", terminal.state)
                elif decision == "dismiss":
                    terminal = harness.dismiss(candidate)
                    self.assertEqual("cancelled", terminal.state)
                else:
                    harness.disconnect_panel()
                    terminal = harness.queue.get_result(candidate.request_id)
                    self.assertEqual("shutdown", terminal.state)
                self.assertEqual(unchanged, executor.scene_content_digest)
                self.assertEqual(0, executor.apply_execution_count)

        clock = _Clock()
        executor = FakeSceneExecutor(
            hip_session_id="decision-expiry", hip_fingerprint="de" * 32
        )
        harness = GateB3OfflineHarness(
            executor,
            registry=self.registry,
            clock=clock,
        )
        _, expiring, _ = self._present_apply(
            harness,
            executor,
            graph,
            "expiry",
            absolute_deadline=clock() + 1.0,
        )
        unchanged = executor.scene_content_digest
        clock.advance(1.1)
        expired = harness.queue.get_result(expiring.request_id)
        self.assertEqual("expired", expired.state)
        self.assertEqual(unchanged, executor.scene_content_digest)

        executor = FakeSceneExecutor(
            hip_session_id="decision-mismatch", hip_fingerprint="ef" * 32
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry)
        _, mismatched, _ = self._present_apply(
            harness, executor, graph, "mismatch"
        )
        with self.assertRaises(SceneQueueError) as caught:
            harness.decide(mismatched, "allow", request_digest="f" * 64)
        self.assertEqual("APPROVAL_DIGEST_MISMATCH", caught.exception.code)
        self.assertEqual(0, executor.apply_execution_count)
        self.assertEqual({}, executor.graphs)

    def test_every_approval_payload_leaf_changes_its_digest(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "approval-leaves",
        )
        payload = presentation.approval_payload
        self.assertIsNotNone(payload)
        assert payload is not None
        original = canonical_json_sha256(payload)
        self.assertEqual(original, presentation.approval_binding_digest)
        paths = list(_walk_leaves(payload))
        self.assertGreater(len(paths), 40)
        for path in paths:
            with self.subTest(path=path):
                changed = _replace_leaf(payload, path)
                self.assertNotEqual(original, canonical_json_sha256(changed))
                tampered_presentation = replace(
                    presentation,
                    approval_payload=changed,
                )
                with self.assertRaises(SceneQueueError) as caught:
                    self.harness.decide(tampered_presentation, "allow")
                self.assertEqual(
                    "APPROVAL_DIGEST_MISMATCH",
                    caught.exception.code,
                )
                self.assertEqual(0, self.executor.apply_execution_count)
                self.assertIsNone(self.harness.poll())
        allowed = self.harness.decide(presentation, "allow")
        self.assertEqual("queued", allowed.state)

    def test_forged_claim_token_and_digest_cannot_bypass_scene_approval(self) -> None:
        validation = self._validate(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "forged-claim",
        )
        arguments = self._arguments(
            self.executor,
            "houdini_graph_apply",
            "forged-claim-apply",
            graph=validation["normalized_graph"],
            digest=validation["canonical_graph_digest"],
        )
        forged = SimpleNamespace(
            tool_name="houdini_graph_apply",
            arguments=arguments,
            request_digest="0" * 64,
            executor_token="attacker-made-token",
        )
        before = self.executor.scene_content_digest
        with self.assertRaises(ValueError):
            self.executor._execute_authorized_claim(
                forged,
                authority=object(),
            )
        direct = self.executor.execute("houdini_graph_apply", arguments)
        self.assertFalse(direct["ok"])
        self.assertEqual(
            "APPROVAL_REQUIRED",
            direct["structured_error"]["code"],
        )
        self.assertEqual(before, self.executor.scene_content_digest)
        self.assertEqual(0, self.executor.apply_execution_count)

        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("table_graph.json"),
            "forged-harness-claim",
        )
        forged_before_allow = replace(
            presentation,
            kind="execute",
            executor_token="attacker-made-token",
        )
        with self.assertRaises(ValueError):
            self.harness.execute_work(forged_before_allow)
        self.assertEqual(before, self.executor.scene_content_digest)

        self.harness.decide(presentation, "allow")
        genuine_work = self.harness.poll()
        self.assertIsNotNone(genuine_work)
        assert genuine_work is not None
        forged_after_allow = replace(
            genuine_work,
            executor_token="attacker-made-token",
        )
        with self.assertRaises(ValueError):
            self.harness.execute_work(forged_after_allow)
        self.assertEqual(before, self.executor.scene_content_digest)
        self.assertEqual(0, self.executor.apply_execution_count)

        original_thread_id = genuine_work.arguments["thread_id"]
        genuine_work.arguments["thread_id"] = "tampered-after-claim"
        with self.assertRaises(ValueError):
            self.harness.execute_work(genuine_work)
        self.assertEqual(before, self.executor.scene_content_digest)
        self.assertEqual(0, self.executor.apply_execution_count)
        genuine_work.arguments["thread_id"] = original_thread_id

        completed = self.harness.execute_work(genuine_work)
        self.assertIsNotNone(completed.result)
        assert completed.result is not None
        self.assertTrue(completed.result["ok"])
        self.assertEqual(1, self.executor.apply_execution_count)

    def test_two_concurrent_writers_are_serialized_by_the_fake_scene(self) -> None:
        executor = FakeSceneExecutor(
            hip_session_id="writer-session", hip_fingerprint="12" * 32
        )
        first_harness = GateB3OfflineHarness(executor, registry=self.registry)
        second_harness = GateB3OfflineHarness(executor, registry=self.registry)
        _, first_presentation, _ = self._present_apply(
            first_harness,
            executor,
            self._fixture("table_graph.json"),
            "writer-one",
        )
        _, second_presentation, _ = self._present_apply(
            second_harness,
            executor,
            self._fixture("stairs_graph.json"),
            "writer-two",
        )
        first_harness.decide(first_presentation, "allow")
        second_harness.decide(second_presentation, "allow")
        first_work = first_harness.poll()
        second_work = second_harness.poll()
        self.assertIsNotNone(first_work)
        self.assertIsNotNone(second_work)
        assert first_work is not None and second_work is not None

        entered = threading.Event()
        release = threading.Event()
        original_apply = executor._graph_apply

        def blocking_apply(
            arguments: Any,
            *,
            approved_claim_digest: str,
            execution_guard: Any | None = None,
        ) -> Any:
            entered.set()
            if not release.wait(2.0):
                raise AssertionError("concurrent writer test timed out")
            return original_apply(
                arguments,
                approved_claim_digest=approved_claim_digest,
                execution_guard=execution_guard,
            )

        executor._graph_apply = blocking_apply  # type: ignore[method-assign]
        first_results: list[Any] = []
        worker = threading.Thread(
            target=lambda: first_results.append(first_harness.execute_work(first_work))
        )
        worker.start()
        self.assertTrue(entered.wait(2.0))
        second = second_harness.execute_work(second_work)
        release.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(first_results))
        self.assertTrue(first_results[0].result["ok"])
        self.assertFalse(second.result["ok"])
        self.assertEqual(
            "WRITE_IN_PROGRESS",
            second.result["structured_error"]["code"],
        )
        self.assertEqual(1, executor.apply_execution_count)
        self.assertEqual(1, len(executor.graphs))

    def test_simulated_undo_cannot_race_an_active_apply(self) -> None:
        first_request, first_presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("table_graph.json"),
            "undo-race-first",
        )
        del first_request
        first = self.harness.allow_and_execute(first_presentation)
        self.assertIsNotNone(first.result)
        assert first.result is not None
        self.assertTrue(first.result["ok"])
        self.assertEqual(1, self.executor.undo_depth)

        _, second_presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "undo-race-second",
        )
        self.harness.decide(second_presentation, "allow")
        second_work = self.harness.poll()
        self.assertIsNotNone(second_work)
        assert second_work is not None

        entered = threading.Event()
        release = threading.Event()
        original_reach_boundary = self.executor._reach_boundary

        def blocking_boundary(
            boundary: str,
            trace: list[str],
            *,
            execution_guard: Any | None = None,
        ) -> None:
            if boundary == "create_root":
                entered.set()
                if not release.wait(2.0):
                    raise AssertionError("apply/Undo race test timed out")
            original_reach_boundary(
                boundary,
                trace,
                execution_guard=execution_guard,
            )

        self.executor._reach_boundary = blocking_boundary  # type: ignore[method-assign]
        results: list[Any] = []
        errors: list[BaseException] = []

        def execute_second() -> None:
            try:
                results.append(self.harness.execute_work(second_work))
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        worker = threading.Thread(target=execute_second)
        worker.start()
        self.assertTrue(entered.wait(2.0))
        with self.assertRaises(ContractError) as caught:
            self.executor.simulate_undo()
        self.assertEqual("WRITE_IN_PROGRESS", caught.exception.code)

        release.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        self.assertIsNotNone(results[0].result)
        self.assertTrue(results[0].result["ok"])
        self.assertEqual(2, len(self.executor.graphs))
        self.assertEqual(2, self.executor.undo_depth)

    def test_stale_session_revision_and_fingerprint_fail_before_write(self) -> None:
        cases = (
            ("hip_session_id", "other-session", "HIP_SESSION_MISMATCH"),
            ("base_scene_revision", 1, "SCENE_CONFLICT"),
            ("expected_hip_fingerprint", "f" * 64, "CAPABILITY_MISMATCH"),
        )
        for field, value, expected_code in cases:
            with self.subTest(field=field):
                executor = FakeSceneExecutor(
                    hip_session_id=f"stale-{field}", hip_fingerprint="23" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                validation = self._validate(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"stale-{field}",
                )
                arguments = self._arguments(
                    executor,
                    "houdini_graph_apply",
                    f"stale-{field}-apply",
                    graph=validation["normalized_graph"],
                    digest=validation["canonical_graph_digest"],
                )
                arguments[field] = value
                before = executor.scene_content_digest
                with self.assertRaises(SceneQueueError) as caught:
                    harness.submit("houdini_graph_apply", arguments)
                self.assertEqual(expected_code, caught.exception.code)
                self.assertEqual(before, executor.scene_content_digest)
                self.assertEqual(0, executor.apply_execution_count)

    def test_exact_idempotent_replay_never_executes_twice_and_change_conflicts(self) -> None:
        request, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "idempotency",
        )
        completed = self.harness.allow_and_execute(presentation)
        self.assertTrue(completed.result["ok"])
        self.assertEqual(1, self.executor.apply_execution_count)
        replay = self.harness.replay(request)
        self.assertTrue(replay.replayed)
        self.assertEqual(completed.result, replay.result)
        self.assertEqual(1, self.executor.apply_execution_count)

        changed = copy.deepcopy(request.arguments)
        changed["turn_id"] = "turn-idempotency-changed"
        with self.assertRaises(SceneQueueError) as caught:
            self.harness.submit("houdini_graph_apply", changed)
        self.assertEqual("IDEMPOTENCY_CONFLICT", caught.exception.code)
        self.assertEqual(1, self.executor.apply_execution_count)

    def test_deadline_cancel_and_shutdown_leave_no_late_write(self) -> None:
        graph = self._fixture("stairs_graph.json")

        clock = _Clock()
        deadline_executor = FakeSceneExecutor(
            hip_session_id="deadline-session", hip_fingerprint="34" * 32
        )
        deadline_harness = GateB3OfflineHarness(
            deadline_executor, registry=self.registry, clock=clock
        )
        _, deadline_presentation, _ = self._present_apply(
            deadline_harness,
            deadline_executor,
            graph,
            "deadline",
            absolute_deadline=clock() + 1.0,
        )
        deadline_harness.decide(deadline_presentation, "allow")
        before = deadline_executor.scene_content_digest
        clock.advance(1.1)
        self.assertIsNone(deadline_harness.poll())
        self.assertEqual(
            "expired",
            deadline_harness.queue.get_result(deadline_presentation.request_id).state,
        )
        self.assertEqual(before, deadline_executor.scene_content_digest)

        cancel_executor = FakeSceneExecutor(
            hip_session_id="cancel-session", hip_fingerprint="45" * 32
        )
        cancel_harness = GateB3OfflineHarness(cancel_executor, registry=self.registry)
        _, cancel_presentation, _ = self._present_apply(
            cancel_harness, cancel_executor, graph, "cancel"
        )
        cancel_harness.decide(cancel_presentation, "allow")
        cancelled = cancel_harness.queue.cancel(cancel_presentation.request_id)
        self.assertEqual("cancelled", cancelled.state)
        self.assertIsNone(cancel_harness.poll())
        self.assertEqual(0, cancel_executor.apply_execution_count)
        self.assertEqual({}, cancel_executor.graphs)

        shutdown_executor = FakeSceneExecutor(
            hip_session_id="shutdown-session", hip_fingerprint="56" * 32
        )
        shutdown_harness = GateB3OfflineHarness(
            shutdown_executor, registry=self.registry
        )
        _, shutdown_presentation, _ = self._present_apply(
            shutdown_harness, shutdown_executor, graph, "shutdown"
        )
        shutdown_harness.decide(shutdown_presentation, "allow")
        claimed = shutdown_harness.poll()
        self.assertIsNotNone(claimed)
        assert claimed is not None
        unchanged = shutdown_executor.scene_content_digest
        shutdown_harness.disconnect_panel()
        terminal = shutdown_harness.execute_work(claimed)
        self.assertEqual("shutdown", terminal.state)
        self.assertEqual(unchanged, shutdown_executor.scene_content_digest)
        self.assertEqual(0, shutdown_executor.apply_execution_count)

    def test_each_mutation_boundary_rolls_back_only_owned_state(self) -> None:
        for index, boundary in enumerate(MUTATION_BOUNDARIES):
            with self.subTest(boundary=boundary):
                executor = FakeSceneExecutor(
                    hip_session_id=f"boundary-{index}", hip_fingerprint="67" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                request, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"boundary-{index}",
                )
                del request
                before = (
                    executor.scene_content_digest,
                    executor.sentinel_digest,
                    executor.scene_revision,
                    executor.hip_fingerprint,
                )
                executor.inject_failure_once(boundary)
                failed = harness.allow_and_execute(presentation)
                self.assertEqual("completed", failed.state)
                self.assertIsNotNone(failed.result)
                assert failed.result is not None
                self.assertFalse(failed.result["ok"])
                self.assertEqual(
                    "INTERNAL_ERROR",
                    failed.result["structured_error"]["code"],
                )
                self.registry.validate_output(
                    "houdini_graph_apply",
                    presentation.arguments,
                    failed.result,
                )
                self.assertEqual(
                    before,
                    (
                        executor.scene_content_digest,
                        executor.sentinel_digest,
                        executor.scene_revision,
                        executor.hip_fingerprint,
                    ),
                )
                self.assertEqual({}, executor.graphs)
                self.assertEqual(0, executor.undo_depth)
                self.assertEqual(boundary, executor.last_transaction_trace[-1])

    def test_rollback_failure_freezes_later_writes(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "rollback-failure",
        )
        sentinel = self.executor.sentinel_digest
        self.executor.inject_failure_once("create_nodes", rollback_failure=True)
        failed = self.harness.allow_and_execute(presentation)
        self.assertFalse(failed.result["ok"])
        self.assertEqual(
            "SCENE_STATE_INDETERMINATE",
            failed.result["structured_error"]["code"],
        )
        self.assertTrue(self.executor.writes_indeterminate)
        self.assertEqual(sentinel, self.executor.sentinel_digest)
        self.assertEqual(0, self.executor.undo_depth)

        validation = self._validate(
            self.harness,
            self.executor,
            self._fixture("table_graph.json"),
            "frozen-next",
        )
        arguments = self._arguments(
            self.executor,
            "houdini_graph_apply",
            "frozen-next-apply",
            graph=validation["normalized_graph"],
            digest=validation["canonical_graph_digest"],
        )
        with self.assertRaises(SceneQueueError) as caught:
            self.harness.submit("houdini_graph_apply", arguments)
        self.assertEqual("SCENE_STATE_INDETERMINATE", caught.exception.code)

    def test_internal_postcondition_tamper_rolls_back_before_commit(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "postcondition",
        )
        before = self.executor.scene_content_digest
        sentinel = self.executor.sentinel_digest
        self.executor.inject_postcondition_tamper_once()
        failed = self.harness.allow_and_execute(presentation)
        self.assertFalse(failed.result["ok"])
        self.assertEqual(
            "POSTCONDITION_FAILED",
            failed.result["structured_error"]["code"],
        )
        self.assertEqual(1, self.executor.internal_postcondition_count)
        self.assertEqual(before, self.executor.scene_content_digest)
        self.assertEqual(sentinel, self.executor.sentinel_digest)
        self.assertEqual(0, self.executor.undo_depth)

    def test_observed_state_tamper_is_detected_by_external_verify(self) -> None:
        graph = self._fixture("stairs_graph.json")
        _, presentation, validation = self._present_apply(
            self.harness,
            self.executor,
            graph,
            "observed-tamper",
        )
        applied = self.harness.allow_and_execute(presentation)
        root_path = applied.result["result"]["root_path"]
        parameterized = next(node for node in graph["nodes"] if node["parameters"])
        parameter = parameterized["parameters"][0]
        typed = copy.deepcopy(parameter["value"])
        typed["value"] = [9.0 for _ in typed["value"]]
        self.executor.tamper_observed_parameter(
            root_path,
            parameterized["id"],
            parameter["name"],
            typed,
        )
        verified = self._verify(
            self.harness,
            self.executor,
            root_path,
            validation["canonical_graph_digest"],
            "observed-tamper",
        )
        self.assertIsNotNone(verified.result)
        assert verified.result is not None
        self.assertTrue(verified.result["ok"])
        self.assertFalse(verified.result["result"]["valid"])
        self.assertFalse(verified.result["result"]["digest_matches"])

    def test_unexpected_runtime_errors_always_rollback_and_are_sanitized(self) -> None:
        for index, point in enumerate(UNEXPECTED_FAILURE_POINTS):
            with self.subTest(point=point):
                executor = FakeSceneExecutor(
                    hip_session_id=f"unexpected-{index}", hip_fingerprint="71" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"unexpected-{index}",
                )
                before = (
                    executor.scene_content_digest,
                    executor.sentinel_digest,
                    executor.scene_revision,
                    executor.hip_fingerprint,
                )
                executor.inject_unexpected_failure_once(point)
                failed = harness.allow_and_execute(presentation)
                self.assertEqual("completed", failed.state)
                self.assertIsNotNone(failed.result)
                assert failed.result is not None
                self.assertFalse(failed.result["ok"])
                self.assertEqual(
                    "INTERNAL_ERROR",
                    failed.result["structured_error"]["code"],
                )
                self.registry.validate_output(
                    "houdini_graph_apply", presentation.arguments, failed.result
                )
                self.assertEqual(
                    before,
                    (
                        executor.scene_content_digest,
                        executor.sentinel_digest,
                        executor.scene_revision,
                        executor.hip_fingerprint,
                    ),
                )
                self.assertEqual({}, executor.graphs)
                self.assertEqual(0, executor.undo_depth)
                self.assertEqual(
                    {"/obj/HIA_Graph_UserSentinel"},
                    set(executor.observed_scene["roots"]),
                )
                encoded = json.dumps(
                    {"result": failed.result, "audit": executor.audit_records},
                    allow_nan=False,
                    sort_keys=True,
                ).casefold()
                for forbidden in (
                    "deterministic fake unexpected",
                    "runtimeerror",
                    "traceback",
                    "c:\\users\\",
                    "authorization",
                    "refresh_token",
                ):
                    self.assertNotIn(forbidden, encoded)

    def test_base_exceptions_rollback_then_rethrow_without_being_swallowed(self) -> None:
        cases = (
            ("publish_root", KeyboardInterrupt),
            ("create_nodes", SystemExit),
            ("commit", KeyboardInterrupt),
        )
        for index, (point, exception_type) in enumerate(cases):
            with self.subTest(point=point, exception=exception_type.__name__):
                executor = FakeSceneExecutor(
                    hip_session_id=f"base-{index}", hip_fingerprint="72" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"base-{index}",
                )
                work = self._claim_apply(harness, presentation)
                before = executor.scene_content_digest
                sentinel = executor.sentinel_digest
                executor.inject_base_exception_once(point, exception_type)
                with self.assertRaises(exception_type):
                    harness.execute_work(work)
                self.assertEqual(before, executor.scene_content_digest)
                self.assertEqual(sentinel, executor.sentinel_digest)
                self.assertEqual({}, executor.graphs)
                self.assertEqual(0, executor.undo_depth)
                self.assertFalse(executor.writes_indeterminate)
                self.assertIsNone(executor._active_transaction_id)

    def test_base_exception_with_unprovable_rollback_freezes_then_rethrows(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "base-freeze",
        )
        work = self._claim_apply(self.harness, presentation)
        sentinel = self.executor.sentinel_digest
        self.executor.inject_base_exception_once("create_nodes", KeyboardInterrupt)
        self.executor.inject_rollback_tamper_once("identity")
        with self.assertRaises(KeyboardInterrupt):
            self.harness.execute_work(work)
        self.assertTrue(self.executor.writes_indeterminate)
        self.assertEqual(sentinel, self.executor.sentinel_digest)
        self.assertEqual(2, len(self.executor.observed_scene["roots"]))
        self.assertIsNone(self.executor._active_transaction_id)

    def test_rollback_exception_returns_indeterminate_and_freezes_writes(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "rollback-exception",
        )
        sentinel = self.executor.sentinel_digest
        self.executor.inject_failure_once("create_nodes")
        self.executor.inject_rollback_exception_once()
        failed = self.harness.allow_and_execute(presentation)
        self.assertEqual("completed", failed.state)
        self.assertEqual(
            "SCENE_STATE_INDETERMINATE",
            failed.result["structured_error"]["code"],
        )
        self.assertTrue(self.executor.writes_indeterminate)
        self.assertEqual(sentinel, self.executor.sentinel_digest)
        self.assertGreater(len(self.executor.observed_scene["roots"]), 1)
        self.assertNotIn("traceback", json.dumps(failed.result).casefold())

    def test_rollback_requires_exact_root_path_target_identity_and_transaction(self) -> None:
        for index, point in enumerate(ROLLBACK_TAMPER_POINTS):
            with self.subTest(point=point):
                executor = FakeSceneExecutor(
                    hip_session_id=f"proof-{index}", hip_fingerprint="73" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"proof-{index}",
                )
                sentinel = executor.sentinel_digest
                executor.inject_failure_once("set_parameters")
                executor.inject_rollback_tamper_once(point)
                failed = harness.allow_and_execute(presentation)
                self.assertEqual(
                    "SCENE_STATE_INDETERMINATE",
                    failed.result["structured_error"]["code"],
                )
                self.assertTrue(executor.writes_indeterminate)
                self.assertEqual(sentinel, executor.sentinel_digest)
                self.assertEqual(2, len(executor.observed_scene["roots"]))
                self.assertEqual(0, executor.undo_depth)

    def test_cancel_cannot_hide_an_indeterminate_rollback(self) -> None:
        executor = FakeSceneExecutor(
            hip_session_id="cancel-indeterminate",
            hip_fingerprint="7f" * 32,
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry)
        _, presentation, _ = self._present_apply(
            harness,
            executor,
            self._fixture("stairs_graph.json"),
            "cancel-indeterminate",
        )
        work = self._claim_apply(harness, presentation)
        executor.inject_failure_once("set_parameters", rollback_failure=True)
        original_execute = executor._execute_authorized_claim

        def execute_then_cancel(*args: Any, **kwargs: Any) -> dict[str, Any]:
            output = original_execute(*args, **kwargs)
            requested = harness.cancel(work.request_id)
            self.assertTrue(requested.cancel_requested)
            return output

        executor._execute_authorized_claim = (  # type: ignore[method-assign]
            execute_then_cancel
        )
        failed = harness.execute_work(work)

        self.assertEqual("indeterminate", failed.state)
        self.assertEqual(
            "SCENE_STATE_INDETERMINATE",
            failed.structured_error["code"],
        )
        self.assertTrue(executor.writes_indeterminate)

    def test_post_claim_cancel_deadline_and_shutdown_are_transactional(self) -> None:
        graph = self._fixture("stairs_graph.json")
        for index, boundary in enumerate(("create_root", "set_parameters")):
            with self.subTest(control="cancel", boundary=boundary):
                executor = FakeSceneExecutor(
                    hip_session_id=f"cancel-live-{index}", hip_fingerprint="74" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness, executor, graph, f"cancel-live-{index}"
                )
                work = self._claim_apply(harness, presentation)
                before = executor.scene_content_digest
                entered, release = threading.Event(), threading.Event()
                executor.pause_at_boundary_once(boundary, entered, release)
                worker, results, errors = self._start_execution(harness, work)
                self.assertTrue(entered.wait(2.0))
                requested = harness.cancel(work.request_id)
                self.assertTrue(requested.cancel_requested)
                release.set()
                worker.join(3.0)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], errors)
                self.assertEqual("cancelled", results[0].state)
                self.assertEqual("CANCELLED", results[0].structured_error["code"])
                self.assertEqual(before, executor.scene_content_digest)
                self.assertEqual({}, executor.graphs)

        clock = _Clock()
        executor = FakeSceneExecutor(
            hip_session_id="deadline-live", hip_fingerprint="75" * 32
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry, clock=clock)
        _, presentation, _ = self._present_apply(
            harness,
            executor,
            graph,
            "deadline-live",
            absolute_deadline=clock() + 1.0,
        )
        work = self._claim_apply(harness, presentation)
        before = executor.scene_content_digest
        entered, release = threading.Event(), threading.Event()
        executor.pause_at_boundary_once("postcondition", entered, release)
        worker, results, errors = self._start_execution(harness, work)
        self.assertTrue(entered.wait(2.0))
        clock.advance(1.1)
        self.assertEqual("claimed", harness.queue.get_result(work.request_id).state)
        concurrent_read = self._arguments(
            executor, "houdini_scene_info", "deadline-concurrent-read"
        )
        concurrent_read["include_graph_summaries"] = False
        _, queued_read = harness.submit(
            "houdini_scene_info",
            concurrent_read,
            absolute_deadline=clock() + 5.0,
        )
        self.assertEqual("queued", queued_read.state)
        read_work = harness.poll()
        self.assertIsNotNone(read_work)
        self.assertEqual("houdini_scene_info", read_work.tool_name)
        self.assertEqual("claimed", harness.queue.execution_snapshot(work.request_id).state)
        release.set()
        worker.join(3.0)
        self.assertEqual([], errors)
        self.assertEqual("expired", results[0].state)
        self.assertEqual("DEADLINE_EXCEEDED", results[0].structured_error["code"])
        self.assertEqual(before, executor.scene_content_digest)
        self.assertEqual({}, executor.graphs)

        executor = FakeSceneExecutor(
            hip_session_id="shutdown-live", hip_fingerprint="76" * 32
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry)
        _, presentation, _ = self._present_apply(
            harness, executor, graph, "shutdown-live"
        )
        work = self._claim_apply(harness, presentation)
        before = executor.scene_content_digest
        entered, release = threading.Event(), threading.Event()
        executor.pause_at_boundary_once("before_commit", entered, release)
        worker, results, errors = self._start_execution(harness, work)
        self.assertTrue(entered.wait(2.0))
        harness.disconnect_panel()
        pending_shutdown = harness.queue.execution_snapshot(work.request_id)
        self.assertEqual("claimed", pending_shutdown.state)
        self.assertFalse(pending_shutdown.terminal)
        self.assertEqual(
            "claimed", harness.queue.get_result(work.request_id).state
        )
        release.set()
        worker.join(3.0)
        self.assertEqual([], errors)
        self.assertEqual("shutdown", results[0].state)
        self.assertEqual(before, executor.scene_content_digest)
        self.assertEqual({}, executor.graphs)

    def test_cancel_between_boundaries_wins_before_the_next_mutation(self) -> None:
        cases = (
            ("before_create_root", ()),
            ("before_create_nodes", ("create_root",)),
        )
        for index, (pause_point, expected_trace) in enumerate(cases):
            with self.subTest(pause_point=pause_point):
                executor = FakeSceneExecutor(
                    hip_session_id=f"cancel-between-boundaries-{index}",
                    hip_fingerprint="7d" * 32,
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"cancel-between-boundaries-{index}",
                )
                work = self._claim_apply(harness, presentation)
                before = executor.scene_content_digest
                entered, release = threading.Event(), threading.Event()
                executor.pause_at_boundary_once(pause_point, entered, release)
                worker, results, errors = self._start_execution(harness, work)
                self.assertTrue(entered.wait(2.0))

                requested = harness.cancel(work.request_id)
                self.assertTrue(requested.cancel_requested)
                release.set()
                worker.join(3.0)

                self.assertFalse(worker.is_alive())
                self.assertEqual([], errors)
                self.assertEqual("cancelled", results[0].state)
                self.assertEqual("CANCELLED", results[0].structured_error["code"])
                self.assertEqual(expected_trace, executor.last_transaction_trace)
                self.assertFalse(executor.writes_indeterminate)
                self.assertEqual(before, executor.scene_content_digest)
                self.assertEqual({}, executor.graphs)

    def test_deadline_after_final_authority_point_cannot_false_cancel_commit(self) -> None:
        clock = _Clock()
        executor = FakeSceneExecutor(
            hip_session_id="deadline-commit-wins", hip_fingerprint="7c" * 32
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry, clock=clock)
        _, presentation, _ = self._present_apply(
            harness,
            executor,
            self._fixture("stairs_graph.json"),
            "deadline-commit-wins",
            absolute_deadline=clock() + 1.0,
        )
        original_commit = executor._commit_transaction

        def commit_then_advance(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = original_commit(*args, **kwargs)
            clock.advance(2.0)
            return result

        executor._commit_transaction = commit_then_advance  # type: ignore[method-assign]
        completed = harness.allow_and_execute(presentation)
        self.assertEqual("completed", completed.state)
        self.assertTrue(completed.result["ok"])
        self.assertEqual(1, len(executor.graphs))
        self.assertEqual(1, executor.undo_depth)

    def test_cancel_and_final_commit_race_has_one_authoritative_outcome(self) -> None:
        outcomes: set[str] = set()
        for index in range(12):
            executor = FakeSceneExecutor(
                hip_session_id=f"commit-race-{index}", hip_fingerprint="77" * 32
            )
            harness = GateB3OfflineHarness(executor, registry=self.registry)
            _, presentation, _ = self._present_apply(
                harness,
                executor,
                self._fixture("stairs_graph.json"),
                f"commit-race-{index}",
            )
            work = self._claim_apply(harness, presentation)
            entered, release = threading.Event(), threading.Event()
            executor.pause_at_boundary_once("before_commit", entered, release)
            worker, results, errors = self._start_execution(harness, work)
            self.assertTrue(entered.wait(2.0))
            gate = threading.Barrier(2)
            cancel_results: list[Any] = []

            def cancel_now() -> None:
                gate.wait()
                cancel_results.append(harness.cancel(work.request_id))

            cancel_worker = threading.Thread(target=cancel_now)
            cancel_worker.start()
            release.set()
            gate.wait()
            worker.join(3.0)
            cancel_worker.join(3.0)
            self.assertEqual([], errors)
            self.assertEqual(1, len(results))
            self.assertEqual(1, len(cancel_results))
            terminal = harness.queue.execution_snapshot(work.request_id)
            outcomes.add(terminal.state)
            if terminal.state == "completed":
                self.assertTrue(terminal.result["ok"])
                self.assertEqual(1, len(executor.graphs))
            else:
                self.assertEqual("cancelled", terminal.state)
                self.assertEqual("CANCELLED", terminal.structured_error["code"])
                self.assertEqual({}, executor.graphs)
            self.assertEqual(1 if terminal.state == "completed" else 0, executor.undo_depth)
        self.assertTrue(outcomes <= {"completed", "cancelled"})

    def test_readers_never_observe_partial_state_at_any_mutation_boundary(self) -> None:
        for index, boundary in enumerate(MUTATION_BOUNDARIES):
            with self.subTest(boundary=boundary):
                executor = FakeSceneExecutor(
                    hip_session_id=f"reader-{index}", hip_fingerprint="78" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, _ = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"reader-{index}",
                )
                work = self._claim_apply(harness, presentation)
                read_arguments = self._arguments(
                    executor, "houdini_scene_info", f"reader-info-{index}"
                )
                read_arguments["include_graph_summaries"] = True
                entered, release = threading.Event(), threading.Event()
                executor.pause_at_boundary_once(boundary, entered, release)
                writer, write_results, write_errors = self._start_execution(harness, work)
                self.assertTrue(entered.wait(2.0))
                read_results: list[Any] = []
                reader = threading.Thread(
                    target=lambda: read_results.append(
                        executor.execute("houdini_scene_info", read_arguments)
                    )
                )
                reader.start()
                time.sleep(0.02)
                self.assertTrue(reader.is_alive())
                release.set()
                writer.join(3.0)
                reader.join(3.0)
                self.assertEqual([], write_errors)
                self.assertTrue(write_results[0].result["ok"])
                self.assertEqual(1, len(read_results))
                self.assertFalse(read_results[0]["ok"])
                self.assertEqual(
                    "SCENE_CONFLICT",
                    read_results[0]["structured_error"]["code"],
                )
                self.assertEqual(1, len(executor.graphs))

    def test_verify_blocks_at_postcondition_and_commit_until_stable(self) -> None:
        for index, boundary in enumerate(("postcondition", "commit")):
            with self.subTest(boundary=boundary):
                executor = FakeSceneExecutor(
                    hip_session_id=f"verify-reader-{index}",
                    hip_fingerprint="7b" * 32,
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, validation = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"verify-reader-{index}",
                )
                work = self._claim_apply(harness, presentation)
                root_path = "/obj/" + validation["normalized_graph"]["target"][
                    "name_hint"
                ]
                verify_arguments = self._arguments(
                    executor,
                    "houdini_graph_verify",
                    f"verify-reader-{index}-read",
                    root_path=root_path,
                    digest=validation["canonical_graph_digest"],
                )
                entered, release = threading.Event(), threading.Event()
                executor.pause_at_boundary_once(boundary, entered, release)
                writer, write_results, write_errors = self._start_execution(harness, work)
                self.assertTrue(entered.wait(2.0))
                verify_results: list[Any] = []
                reader = threading.Thread(
                    target=lambda: verify_results.append(
                        executor.execute("houdini_graph_verify", verify_arguments)
                    )
                )
                reader.start()
                time.sleep(0.02)
                self.assertTrue(reader.is_alive())
                release.set()
                writer.join(3.0)
                reader.join(3.0)
                self.assertEqual([], write_errors)
                self.assertTrue(write_results[0].result["ok"])
                self.assertEqual(1, len(verify_results))
                self.assertFalse(verify_results[0]["ok"])
                self.assertEqual(
                    "SCENE_CONFLICT",
                    verify_results[0]["structured_error"]["code"],
                )
                self.assertEqual(1, len(executor.graphs))

    def test_failed_transaction_and_undo_readers_see_only_stable_snapshots(self) -> None:
        executor = FakeSceneExecutor(
            hip_session_id="reader-rollback", hip_fingerprint="79" * 32
        )
        harness = GateB3OfflineHarness(executor, registry=self.registry)
        _, presentation, _ = self._present_apply(
            harness,
            executor,
            self._fixture("stairs_graph.json"),
            "reader-rollback",
        )
        work = self._claim_apply(harness, presentation)
        read_arguments = self._arguments(
            executor, "houdini_scene_info", "reader-rollback-info"
        )
        read_arguments["include_graph_summaries"] = True
        entered, release = threading.Event(), threading.Event()
        executor.pause_at_boundary_once("set_parameters", entered, release)
        executor.inject_failure_once("connect_nodes")
        writer, write_results, write_errors = self._start_execution(harness, work)
        self.assertTrue(entered.wait(2.0))
        read_results: list[Any] = []
        reader = threading.Thread(
            target=lambda: read_results.append(
                executor.execute("houdini_scene_info", read_arguments)
            )
        )
        reader.start()
        time.sleep(0.02)
        self.assertTrue(reader.is_alive())
        release.set()
        writer.join(3.0)
        reader.join(3.0)
        self.assertEqual([], write_errors)
        self.assertFalse(write_results[0].result["ok"])
        self.assertTrue(read_results[0]["ok"])
        self.assertEqual([], read_results[0]["result"]["hia_graphs"])

        _, presentation, _ = self._present_apply(
            harness,
            executor,
            self._fixture("stairs_graph.json"),
            "reader-undo-apply",
        )
        applied = harness.allow_and_execute(presentation)
        self.assertTrue(applied.result["ok"])
        undo_read = self._arguments(
            executor, "houdini_scene_info", "reader-undo-info"
        )
        undo_read["include_graph_summaries"] = True
        undo_entered, undo_release = threading.Event(), threading.Event()
        executor.pause_simulated_undo_once(undo_entered, undo_release)
        undo_results: list[Any] = []
        undo_worker = threading.Thread(
            target=lambda: undo_results.append(executor.simulate_undo())
        )
        undo_worker.start()
        self.assertTrue(undo_entered.wait(2.0))
        post_undo_reads: list[Any] = []
        undo_reader = threading.Thread(
            target=lambda: post_undo_reads.append(
                executor.execute("houdini_scene_info", undo_read)
            )
        )
        undo_reader.start()
        time.sleep(0.02)
        self.assertTrue(undo_reader.is_alive())
        undo_release.set()
        undo_worker.join(3.0)
        undo_reader.join(3.0)
        self.assertEqual("OK", undo_results[0]["result_code"])
        self.assertFalse(post_undo_reads[0]["ok"])
        self.assertEqual(
            "SCENE_CONFLICT",
            post_undo_reads[0]["structured_error"]["code"],
        )
        self.assertEqual({}, executor.graphs)

    def test_attestation_refresh_waits_for_one_stable_undo_snapshot(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "attestation-undo",
        )
        applied = self.harness.allow_and_execute(presentation)
        self.assertTrue(applied.result["ok"])
        entered, release = threading.Event(), threading.Event()
        self.executor.pause_simulated_undo_once(entered, release)
        undo_results: list[Any] = []
        undo_worker = threading.Thread(
            target=lambda: undo_results.append(self.executor.simulate_undo())
        )
        undo_worker.start()
        self.assertTrue(entered.wait(2.0))
        refresh_results: list[str] = []
        refresh_worker = threading.Thread(
            target=lambda: refresh_results.append(self.harness.refresh_attestation())
        )
        refresh_worker.start()
        time.sleep(0.02)
        self.assertTrue(refresh_worker.is_alive())
        release.set()
        undo_worker.join(3.0)
        refresh_worker.join(3.0)
        self.assertEqual("OK", undo_results[0]["result_code"])
        self.assertEqual(1, len(refresh_results))
        stable = self.executor.capability_snapshot()
        expected = self.harness._attestation(stable).digest
        self.assertEqual(expected, refresh_results[0])
        self.assertEqual(expected, self.harness.queue.current_attestation_digest)

    def test_verify_checks_are_derived_from_each_observed_dimension(self) -> None:
        scenarios = (
            ("parameters", "parameters"),
            ("connections", "connections"),
            ("flags", "flags"),
            ("ownership", "ownership"),
            ("transaction", "ownership"),
            ("duplicate_identity", "nodes"),
            ("empty_root_identity", "nodes"),
            ("node_key", "nodes"),
            ("cook", "cook"),
        )
        for index, (scenario, expected_check) in enumerate(scenarios):
            with self.subTest(scenario=scenario):
                executor = FakeSceneExecutor(
                    hip_session_id=f"verify-{index}", hip_fingerprint="7a" * 32
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                graph = self._fixture("stairs_graph.json")
                _, presentation, validation = self._present_apply(
                    harness, executor, graph, f"verify-{index}"
                )
                applied = harness.allow_and_execute(presentation)
                root_path = applied.result["result"]["root_path"]
                if scenario == "parameters":
                    executor.tamper_observed_parameter(
                        root_path,
                        "step_source",
                        "size",
                        {
                            "type": "tuple",
                            "items_type": "float",
                            "value": [2.0, 0.25, 0.4],
                        },
                    )
                elif scenario == "connections":
                    changed = copy.deepcopy(graph["connections"])
                    changed[0]["source"]["node"] = "landing"
                    executor.tamper_observed_connections(root_path, changed)
                elif scenario == "flags":
                    executor.tamper_observed_flags(
                        root_path, "output", {"display": False, "render": False}
                    )
                    executor.tamper_observed_flags(
                        root_path, "combine", {"display": True, "render": True}
                    )
                elif scenario == "ownership":
                    executor.tamper_observed_ownership(root_path, "user_owned")
                elif scenario == "transaction":
                    executor.tamper_observed_transaction(root_path, None)
                elif scenario == "duplicate_identity":
                    identity = executor.observed_scene["roots"][root_path]["nodes"][
                        "step_source"
                    ]["object_identity"]
                    executor.tamper_observed_object_identity(
                        root_path, "landing", identity
                    )
                elif scenario == "empty_root_identity":
                    executor.tamper_observed_object_identity(root_path, None, "")
                elif scenario == "node_key":
                    executor.tamper_observed_node_key(
                        root_path, "landing", "tampered_mapping_key"
                    )
                else:
                    executor.tamper_observed_cook_state(
                        root_path, "output", "error"
                    )
                verified = self._verify(
                    harness,
                    executor,
                    root_path,
                    validation["canonical_graph_digest"],
                    f"verify-{index}",
                )
                self.assertIsNotNone(verified.result)
                assert verified.result is not None
                self.assertTrue(verified.result["ok"])
                result = verified.result["result"]
                checks = {item["name"]: item for item in result["checks"]}
                self.assertEqual(set(checks), {
                    "session", "revision", "target", "ownership", "nodes",
                    "parameters", "connections", "flags", "cook", "graph_digest",
                })
                self.assertFalse(checks[expected_check]["passed"])
                if scenario in {"transaction", "empty_root_identity"}:
                    self.assertFalse(checks["ownership"]["passed"])
                self.assertFalse(result["valid"])
                self.registry.validate_output(
                    "houdini_graph_verify",
                    self._arguments(
                        executor,
                        "houdini_graph_verify",
                        f"verify-{index}-verify",
                        root_path=root_path,
                        digest=validation["canonical_graph_digest"],
                    ),
                    verified.result,
                )

    def test_verify_rejects_missing_or_unknown_cook_state_schema_safely(self) -> None:
        for index, scenario in enumerate(("missing", "unknown")):
            with self.subTest(scenario=scenario):
                executor = FakeSceneExecutor(
                    hip_session_id=f"verify-cook-{index}",
                    hip_fingerprint="7e" * 32,
                )
                harness = GateB3OfflineHarness(executor, registry=self.registry)
                _, presentation, validation = self._present_apply(
                    harness,
                    executor,
                    self._fixture("stairs_graph.json"),
                    f"verify-cook-{index}",
                )
                applied = harness.allow_and_execute(presentation)
                self.assertIsNotNone(applied.result)
                assert applied.result is not None
                root_path = applied.result["result"]["root_path"]
                if scenario == "missing":
                    executor.remove_observed_cook_state(root_path, "output")
                else:
                    executor.tamper_observed_cook_state(
                        root_path, "output", "schema-invalid-state"
                    )

                arguments = self._arguments(
                    executor,
                    "houdini_graph_verify",
                    f"verify-cook-{index}",
                    root_path=root_path,
                    digest=validation["canonical_graph_digest"],
                )
                verified = harness.run_read("houdini_graph_verify", arguments)
                self.assertIsNotNone(verified.result)
                assert verified.result is not None
                self.assertFalse(verified.result["ok"])
                self.assertEqual(
                    "VERIFY_FAILED",
                    verified.result["structured_error"]["code"],
                )
                self.registry.validate_output(
                    "houdini_graph_verify", arguments, verified.result
                )

    def test_failure_results_and_audit_are_json_schema_safe_and_secret_free(self) -> None:
        _, presentation, _ = self._present_apply(
            self.harness,
            self.executor,
            self._fixture("stairs_graph.json"),
            "safe-error",
        )
        self.executor.inject_failure_once("set_parameters")
        failed = self.harness.allow_and_execute(presentation)
        self.assertIsNotNone(failed.result)
        assert failed.result is not None
        checked = self.registry.validate_output(
            "houdini_graph_apply",
            presentation.arguments,
            failed.result,
        )
        self.assertEqual(failed.result, checked)
        encoded_result = json.dumps(checked, allow_nan=False, sort_keys=True)
        encoded_audit = json.dumps(
            self.executor.audit_records,
            allow_nan=False,
            sort_keys=True,
        )
        self.assertNotIn("created_nodes", encoded_result)
        self.assertNotIn("changed_nodes", encoded_result)
        for clear_correlation in (
            presentation.arguments["request_id"],
            presentation.arguments["thread_id"],
            presentation.arguments["turn_id"],
            presentation.arguments["idempotency_key"],
        ):
            self.assertNotIn(clear_correlation, encoded_audit)
        combined = (encoded_result + encoded_audit).casefold()
        for forbidden in (
            "bearer ",
            "cookie",
            "authorization",
            "api_key",
            "refresh_token",
            "traceback",
            "c:\\users\\",
            "$home",
            "raw chat",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
