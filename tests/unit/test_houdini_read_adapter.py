from __future__ import annotations

import ast
import json
import sys
import threading
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).parents[2]
SRC = REPOSITORY_ROOT / "src"
PANEL_LIB = REPOSITORY_ROOT / "houdini_package" / "python_libs"
FAKES = REPOSITORY_ROOT / "tests" / "fakes"
for entry in (SRC, PANEL_LIB, FAKES):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from fake_hou import FakeHou  # noqa: E402
from hia_core.houdini_contract import SchemaRegistry  # noqa: E402
from hia_panel.houdini_read_adapter import (  # noqa: E402
    HoudiniReadAdapter,
    HoudiniReadAdapterError,
    _same_houdini_node,
)


class HoudiniReadAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = SchemaRegistry.b2_read_only()

    def _adapter(
        self, fake: FakeHou, *, strict_event_evidence: bool = False
    ) -> HoudiniReadAdapter:
        return HoudiniReadAdapter(
            fake,
            publisher_id="panel-publisher-1",
            python_version="3.11.9",
            pyside_version="6.5.3",
            fingerprint_key=b"fixed-test-fingerprint-key-32b",
            strict_event_evidence=strict_event_evidence,
        )

    @staticmethod
    def _arguments(
        report: dict[str, Any],
        *,
        node_types: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "request_id": "request-1",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "hip_session_id": report["hip_session_id"],
            "base_scene_revision": report["scene_revision"],
            "idempotency_key": "idempotency-key-0001",
            "deadline_ms": 5000,
            "permission_level": "scene_read",
        }
        if node_types is None:
            arguments["include_graph_summaries"] = True
        else:
            arguments["node_types"] = node_types
        return arguments

    @staticmethod
    def _begin_owned_write(
        adapter: HoudiniReadAdapter,
        report: dict[str, Any],
        *,
        transaction_id: str = "transaction-1",
    ) -> object:
        return adapter.begin_owned_write(
            transaction_id,
            expected_hip_session_id=report["hip_session_id"],
            expected_scene_revision=report["scene_revision"],
            expected_hip_fingerprint=report["hip_fingerprint"],
        )

    def test_module_has_no_top_level_houdini_import_or_forbidden_calls(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "houdini_read_adapter.py"
        )
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("hou", imported)
        for forbidden in (
            "QtNetwork",
            "QNetworkReply",
            "createNode(",
            "setParm(",
            "setInput(",
            "destroy(",
            "hipFile.save",
            ".render(",
            ".cook(",
            ".definition(",
            "createDigitalAsset(",
            "installFile(",
            "hscript(",
            "exec(",
            "eval(",
        ):
            self.assertNotIn(forbidden, source)

    def test_start_publishes_exact_bounded_capability_and_five_type_catalog(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        report = adapter.start()

        self.assertEqual(
            {
                "available",
                "publisher_id",
                "houdini_build",
                "python_version",
                "pyside_version",
                "hip_session_id",
                "hip_fingerprint",
                "scene_revision",
                "observer_sequence",
                "session_observer_reliable",
                "revision_observer_reliable",
                "catalog",
            },
            set(report),
        )
        self.assertTrue(report["available"])
        self.assertTrue(report["session_observer_reliable"])
        self.assertTrue(report["revision_observer_reliable"])
        self.assertGreater(report["observer_sequence"], 0)
        self.assertRegex(report["hip_fingerprint"], r"^[a-f0-9]{64}$")
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
                for item in report["catalog"]
            ],
        )
        self.assertEqual(
            ["geo", "box", "xform", "merge", "null"],
            [item["resolved_name"] for item in report["catalog"]],
        )
        for node_type in report["catalog"]:
            self.assertFalse(node_type["creatable"])
            self.assertTrue(node_type["available"])
            for parameter in node_type["parameters"]:
                self.assertFalse(parameter["writable"])
                self.assertFalse(parameter["allows_expression"])
                self.assertEqual(
                    {
                        "min_value",
                        "max_value",
                        "min_is_strict",
                        "max_is_strict",
                    },
                    set(parameter["numeric_range"]),
                )

        encoded = json.dumps(report, sort_keys=True)
        for forbidden in (
            "launch_id",
            "generation",
            "process_nonce",
            "schema_digest",
            "attestation_digest",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(fake.calls)
        self.assertEqual(
            {threading.get_ident()}, {call.thread_id for call in fake.calls}
        )

    def test_unchanged_refresh_reuses_sequence_and_identical_report(self) -> None:
        adapter = self._adapter(FakeHou())
        first = adapter.start()
        second = adapter.refresh()
        third = adapter.capability_report()

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_worker_execute_returns_main_thread_error_before_hom_access(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        report = adapter.start()
        arguments = self._arguments(report)
        calls_before = len(fake.calls)
        results: list[dict[str, Any]] = []

        worker = threading.Thread(
            target=lambda: results.append(
                adapter.execute("houdini_scene_info", arguments)
            )
        )
        worker.start()
        worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(calls_before, len(fake.calls))
        self.assertEqual(
            "MAIN_THREAD_REQUIRED", results[0]["structured_error"]["code"]
        )
        self.registry.validate_output("houdini_scene_info", arguments, results[0])

    def test_worker_malformed_request_still_never_touches_hom(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        adapter.start()
        calls_before = len(fake.calls)
        results: list[dict[str, Any]] = []

        def invoke() -> None:
            results.append(adapter.execute("houdini_scene_info", {}))

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(2)

        self.assertEqual(calls_before, len(fake.calls))
        self.assertEqual(
            "MAIN_THREAD_REQUIRED", results[0]["structured_error"]["code"]
        )

    def test_worker_start_fails_before_hom_access(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        failures: list[HoudiniReadAdapterError] = []

        def invoke() -> None:
            try:
                adapter.start()
            except HoudiniReadAdapterError as exc:
                failures.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(2)

        self.assertEqual([], fake.calls)
        self.assertEqual("MAIN_THREAD_REQUIRED", failures[0].code)

    def test_manual_change_advances_revision_sequence_and_fingerprint(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()

        fake.trigger_manual_change()
        after = adapter.capability_report()

        self.assertEqual(before["hip_session_id"], after["hip_session_id"])
        self.assertEqual(before["scene_revision"] + 1, after["scene_revision"])
        self.assertGreater(after["observer_sequence"], before["observer_sequence"])
        self.assertNotEqual(before["hip_fingerprint"], after["hip_fingerprint"])

    def test_owned_write_coalesces_commit_rollback_and_indeterminate(self) -> None:
        cases = (
            ("committed", 1, False),
            ("rolled_back", 0, True),
            ("indeterminate", 1, False),
        )
        for outcome, expected_delta, fingerprint_restored in cases:
            with self.subTest(outcome=outcome):
                fake = FakeHou()
                adapter = self._adapter(fake)
                before = adapter.start()
                token = self._begin_owned_write(adapter, before)

                during = adapter.capability_report()
                self.assertFalse(during["available"])
                self.assertEqual(before["scene_revision"], during["scene_revision"])

                expectation = adapter.begin_owned_mutation(
                    token,
                    expected_callback_source=fake.obj,
                )
                fake.trigger_manual_change()
                fake.trigger_manual_change()
                self.assertEqual(
                    2,
                    adapter.finish_owned_mutation(token, expectation),
                )
                coalesced = adapter.capability_report()
                self.assertEqual(
                    before["scene_revision"], coalesced["scene_revision"]
                )
                self.assertEqual(
                    before["observer_sequence"], coalesced["observer_sequence"]
                )

                after = adapter.finish_owned_write(token, outcome=outcome)
                self.assertTrue(after["available"])
                self.assertEqual(
                    before["scene_revision"] + expected_delta,
                    after["scene_revision"],
                )
                self.assertEqual(
                    before["observer_sequence"] + 1,
                    after["observer_sequence"],
                )
                self.assertEqual(
                    fingerprint_restored,
                    before["hip_fingerprint"] == after["hip_fingerprint"],
                )

    def test_strict_observer_preflight_requires_event_callbacks_readback(self) -> None:
        fake = FakeHou(hide_node_event_callbacks=True)
        adapter = self._adapter(fake, strict_event_evidence=True)

        report = adapter.start()

        self.assertFalse(report["available"])
        self.assertFalse(report["revision_observer_reliable"])
        self.assertGreater(fake.obj.callback_count, 0)

    def test_strict_owned_mutation_requires_typed_event_and_exact_subject(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            operation="create_root:fixture",
            event_source_rules={"ChildCreated": (fake.obj,)},
            required_event_types=("ChildCreated",),
        )

        graph = fake.add_hia_graph("HIA_Graph_fixture", "a" * 64)
        count = adapter.finish_owned_mutation(
            token,
            expectation,
            expected_child_subjects=(graph,),
            require_all_child_subjects=True,
        )
        registration = adapter.install_owned_node_observer(token, graph)
        after = adapter.finish_owned_write(token, outcome="committed")
        evidence = adapter.last_owned_evidence()

        self.assertEqual(1, count)
        self.assertEqual(graph.path(), registration["path"])
        self.assertEqual(before["scene_revision"] + 1, after["scene_revision"])
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual("committed", evidence["outcome"])
        self.assertEqual("ChildCreated", evidence["events"][0]["event_type"])
        self.assertEqual(graph.path(), evidence["events"][0]["child_path"])

        boundary = len(adapter.event_journal_snapshot())
        graph.emit(fake.nodeEventType.BeingDeleted)
        fake.obj.emit(fake.nodeEventType.ChildDeleted, child_node=graph)
        undo_events = adapter.event_journal_snapshot()[boundary:]
        self.assertEqual(["BeingDeleted", "ChildDeleted"], [
            item["event_type"] for item in undo_events
        ])
        self.assertEqual("/obj", undo_events[-1]["source_path"])
        self.assertEqual(graph.path(), undo_events[-1]["child_path"])
        self.assertFalse(undo_events[-1]["matched"])
        self.assertEqual("committed", adapter.last_owned_evidence()["outcome"])

    def test_strict_events_and_observers_accept_equivalent_fresh_hom_wrappers(
        self,
    ) -> None:
        fake = FakeHou(return_fresh_node_wrappers=True)
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()

        self.assertTrue(before["available"])
        self.assertTrue(adapter.refresh()["revision_observer_reliable"])
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            operation="create_root:fresh_wrapper",
            event_source_rules={"ChildCreated": (fake.obj,)},
            required_event_types=("ChildCreated",),
        )
        graph = fake.add_hia_graph(
            "HIA_Graph_fresh_wrapper",
            "b" * 64,
            notify=False,
        )
        source_wrapper = fake.node_wrapper("/obj")
        subject_wrapper = fake.node_wrapper(graph.path())

        self.assertIsNot(source_wrapper, fake.obj)
        self.assertIsNot(subject_wrapper, graph)
        self.assertTrue(_same_houdini_node(source_wrapper, fake.obj))
        self.assertTrue(_same_houdini_node(subject_wrapper, graph))
        fake.obj.emit(
            fake.nodeEventType.ChildCreated,
            callback_source=source_wrapper,
            child_node=subject_wrapper,
        )

        self.assertEqual(
            1,
            adapter.finish_owned_mutation(
                token,
                expectation,
                expected_child_subjects=(graph,),
                require_all_child_subjects=True,
            ),
        )
        registration = adapter.install_owned_node_observer(token, graph)
        result = adapter.finish_owned_write(token, outcome="committed")

        self.assertEqual(graph.path(), registration["path"])
        self.assertTrue(result["available"])
        self.assertTrue(result["revision_observer_reliable"])

    def test_same_path_and_session_wrapper_is_rejected_without_strict_equality(
        self,
    ) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()
        unequal_wrapper = fake.node_wrapper("/obj", equivalent=False)

        self.assertEqual(fake.obj.path(), unequal_wrapper.path())
        self.assertEqual(fake.obj.sessionId(), unequal_wrapper.sessionId())
        self.assertFalse(fake.obj == unequal_wrapper)
        self.assertFalse(_same_houdini_node(fake.obj, unequal_wrapper))

        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            operation="set_parameter:unequal_wrapper",
            event_source_rules={"ParmTupleChanged": (fake.obj,)},
            required_event_types=("ParmTupleChanged",),
        )
        fake.obj.emit(
            fake.nodeEventType.ParmTupleChanged,
            callback_source=unequal_wrapper,
        )

        with self.assertRaises(HoudiniReadAdapterError) as raised:
            adapter.finish_owned_mutation(token, expectation)
        self.assertEqual("SCENE_CONFLICT", raised.exception.code)
        with self.assertRaises(HoudiniReadAdapterError):
            adapter.finish_owned_write(token, outcome="indeterminate")

        fresh = FakeHou()
        fresh_adapter = self._adapter(fresh, strict_event_evidence=True)
        self.assertTrue(fresh_adapter.start()["revision_observer_reliable"])
        fresh.return_fresh_node_wrappers = True
        fresh.node_wrappers_equivalent = False
        self.assertFalse(fresh_adapter.refresh()["revision_observer_reliable"])

    def test_houdini_node_equivalence_fails_closed_when_comparison_raises(
        self,
    ) -> None:
        class ExplodingEquality:
            def __eq__(self, _other: object) -> bool:
                raise RuntimeError("comparison unavailable")

        self.assertFalse(_same_houdini_node(FakeHou().obj, ExplodingEquality()))

    def test_strict_owned_mutation_rejects_zero_wrong_and_off_main_events(self) -> None:
        cases = ("zero", "wrong", "off_main")
        for case in cases:
            with self.subTest(case=case):
                fake = FakeHou()
                adapter = self._adapter(fake, strict_event_evidence=True)
                before = adapter.start()
                token = self._begin_owned_write(adapter, before)
                expectation = adapter.begin_owned_mutation(
                    token,
                    operation=f"set_parameter:{case}",
                    event_source_rules={"ParmTupleChanged": (fake.obj,)},
                    required_event_types=("ParmTupleChanged",),
                )
                if case == "wrong":
                    fake.obj.emit(fake.nodeEventType.FlagChanged)
                elif case == "off_main":
                    worker = threading.Thread(
                        target=lambda: fake.obj.emit(
                            fake.nodeEventType.ParmTupleChanged
                        )
                    )
                    worker.start()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                with self.assertRaises(HoudiniReadAdapterError):
                    adapter.finish_owned_mutation(token, expectation)
                with self.assertRaises(HoudiniReadAdapterError):
                    adapter.finish_owned_write(token, outcome="indeterminate")

    def test_fixed_user_data_mutation_allows_zero_events_only_with_exact_readback(
        self,
    ) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            operation="set_user_data:hia_ownership",
            event_source_rules={
                "CustomDataChanged": (fake.obj,),
                "AppearanceChanged": (fake.obj,),
            },
            required_event_types=(),
            allow_zero_events=True,
        )

        self.assertEqual(
            0,
            adapter.finish_owned_mutation(
                token,
                expectation,
                exact_readback_proven=True,
            ),
        )
        adapter.finish_owned_write(token, outcome="rolled_back")
        evidence = adapter.last_owned_evidence()

        assert evidence is not None
        self.assertEqual(
            [
                {
                    "operation": "set_user_data:hia_ownership",
                    "event_count": 0,
                    "event_types": [],
                    "no_op": False,
                    "exact_readback_proven": True,
                }
            ],
            evidence["mutations"],
        )

        unauthorized_hou = FakeHou()
        unauthorized = self._adapter(
            unauthorized_hou, strict_event_evidence=True
        )
        unauthorized_report = unauthorized.start()
        unauthorized_token = self._begin_owned_write(
            unauthorized, unauthorized_report
        )
        with self.assertRaises(HoudiniReadAdapterError) as raised:
            unauthorized.begin_owned_mutation(
                unauthorized_token,
                operation="set_parameter:unsafe_silent_policy",
                event_source_rules={"ParmTupleChanged": (unauthorized_hou.obj,)},
                allow_zero_events=True,
            )
        self.assertEqual("INVALID_ARGUMENT", raised.exception.code)

        missing_hou = FakeHou()
        missing = self._adapter(missing_hou, strict_event_evidence=True)
        missing_report = missing.start()
        missing_token = self._begin_owned_write(missing, missing_report)
        missing_expectation = missing.begin_owned_mutation(
            missing_token,
            operation="set_user_data:hia_graph_digest",
            event_source_rules={
                "CustomDataChanged": (missing_hou.obj,),
                "AppearanceChanged": (missing_hou.obj,),
            },
            required_event_types=(),
            allow_zero_events=True,
        )
        with self.assertRaises(HoudiniReadAdapterError) as missing_error:
            missing.finish_owned_mutation(missing_token, missing_expectation)
        self.assertEqual("SCENE_CONFLICT", missing_error.exception.code)

    def test_strict_noop_is_recorded_without_fabricating_event(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)

        adapter.record_owned_noop(token, operation="set_flag:node:display")
        adapter.finish_owned_write(token, outcome="rolled_back")
        evidence = adapter.last_owned_evidence()

        assert evidence is not None
        self.assertEqual(0, evidence["event_count"])
        self.assertEqual(
            [{
                "operation": "set_flag:node:display",
                "event_count": 0,
                "event_types": [],
                "no_op": True,
            }],
            evidence["mutations"],
        )

    def test_strict_event_journal_is_bounded_and_overflow_fails_closed(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake, strict_event_evidence=True)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            operation="set_parameter:bounded",
            event_source_rules={"ParmTupleChanged": (fake.obj,)},
            required_event_types=("ParmTupleChanged",),
        )

        for _index in range(513):
            fake.obj.emit(fake.nodeEventType.ParmTupleChanged)

        with self.assertRaises(HoudiniReadAdapterError):
            adapter.finish_owned_mutation(token, expectation)
        self.assertEqual(512, len(adapter.event_journal_snapshot()))
        self.assertFalse(adapter.capability_report()["revision_observer_reliable"])

    def test_owned_write_unmarked_existing_node_event_is_external(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)

        fake.trigger_manual_change()
        observed = adapter.capability_report()

        self.assertEqual(before["scene_revision"] + 1, observed["scene_revision"])
        self.assertEqual(
            before["observer_sequence"] + 1,
            observed["observer_sequence"],
        )
        with self.assertRaises(HoudiniReadAdapterError) as raised:
            adapter.finish_owned_write(token, outcome="indeterminate")
        self.assertEqual("SCENE_CONFLICT", raised.exception.code)
        after = adapter.capability_report()
        self.assertTrue(after["available"])
        self.assertEqual(observed["scene_revision"], after["scene_revision"])

    def test_owned_mutation_only_coalesces_the_same_underlying_callback_node(self) -> None:
        fake = FakeHou()
        other = fake.add_hia_graph(
            "HIA_Graph_existing",
            "a" * 64,
            notify=False,
        )
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            expected_callback_source=fake.obj,
        )

        fake.trigger_manual_change(other.path())
        observed = adapter.capability_report()

        self.assertEqual(before["scene_revision"] + 1, observed["scene_revision"])
        with self.assertRaises(HoudiniReadAdapterError) as raised:
            adapter.finish_owned_mutation(token, expectation)
        self.assertEqual("SCENE_CONFLICT", raised.exception.code)
        with self.assertRaises(HoudiniReadAdapterError) as finish_failure:
            adapter.finish_owned_write(token, outcome="indeterminate")
        self.assertEqual("SCENE_CONFLICT", finish_failure.exception.code)

    def test_owned_mutation_missing_callback_source_is_external(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            expected_callback_source=fake.obj,
        )

        adapter._on_node_event(event_type=fake.nodeEventType.ParmTupleChanged)

        with self.assertRaises(HoudiniReadAdapterError) as raised:
            adapter.finish_owned_mutation(token, expectation)
        self.assertEqual("SCENE_CONFLICT", raised.exception.code)
        with self.assertRaises(HoudiniReadAdapterError):
            adapter.finish_owned_write(token, outcome="indeterminate")

    def test_owned_mutation_expectations_are_opaque_serial_and_main_thread_only(
        self,
    ) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()

        with self.assertRaises(HoudiniReadAdapterError) as inactive:
            adapter.begin_owned_mutation(
                object(),
                expected_callback_source=fake.obj,
            )
        self.assertEqual("INVALID_ARGUMENT", inactive.exception.code)

        token = self._begin_owned_write(adapter, before)
        expectation = adapter.begin_owned_mutation(
            token,
            expected_callback_source=fake.obj,
        )
        self.assertIs(type(expectation), object)
        with self.assertRaises(HoudiniReadAdapterError) as nested:
            adapter.begin_owned_mutation(
                token,
                expected_callback_source=fake.obj,
            )
        self.assertEqual("SCENE_CONFLICT", nested.exception.code)
        with self.assertRaises(HoudiniReadAdapterError) as forged:
            adapter.finish_owned_mutation(token, object())
        self.assertEqual("INVALID_ARGUMENT", forged.exception.code)

        failures: list[HoudiniReadAdapterError] = []

        def worker_finish() -> None:
            try:
                adapter.finish_owned_mutation(token, expectation)
            except HoudiniReadAdapterError as exc:
                failures.append(exc)

        worker = threading.Thread(target=worker_finish)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual("MAIN_THREAD_REQUIRED", failures[0].code)

        self.assertEqual(0, adapter.finish_owned_mutation(token, expectation))
        with self.assertRaises(HoudiniReadAdapterError) as replayed:
            adapter.finish_owned_mutation(token, expectation)
        self.assertEqual("INVALID_ARGUMENT", replayed.exception.code)
        adapter.finish_owned_write(token, outcome="rolled_back")

    def test_owned_write_snapshot_and_opaque_token_fail_closed(self) -> None:
        adapter = self._adapter(FakeHou())
        before = adapter.start()

        stale_cases = (
            ({"expected_hip_session_id": "stale-session"}, "HIP_SESSION_MISMATCH"),
            (
                {"expected_scene_revision": before["scene_revision"] + 1},
                "SCENE_CONFLICT",
            ),
            ({"expected_hip_fingerprint": "f" * 64}, "SCENE_CONFLICT"),
        )
        for replacement, expected_code in stale_cases:
            with self.subTest(replacement=replacement):
                arguments = {
                    "expected_hip_session_id": before["hip_session_id"],
                    "expected_scene_revision": before["scene_revision"],
                    "expected_hip_fingerprint": before["hip_fingerprint"],
                    **replacement,
                }
                with self.assertRaises(HoudiniReadAdapterError) as raised:
                    adapter.begin_owned_write("transaction-stale", **arguments)
                self.assertEqual(expected_code, raised.exception.code)
                self.assertEqual(before, adapter.capability_report())

        token = self._begin_owned_write(adapter, before)
        self.assertIs(type(token), object)
        with self.assertRaises(HoudiniReadAdapterError) as forged:
            adapter.finish_owned_write(object(), outcome="committed")
        self.assertEqual("INVALID_ARGUMENT", forged.exception.code)
        with self.assertRaises(HoudiniReadAdapterError) as nested:
            self._begin_owned_write(
                adapter,
                before,
                transaction_id="transaction-nested",
            )
        self.assertEqual("SCENE_CONFLICT", nested.exception.code)

        restored = adapter.finish_owned_write(token, outcome="rolled_back")
        self.assertEqual(before, restored)
        with self.assertRaises(HoudiniReadAdapterError) as replayed:
            adapter.finish_owned_write(token, outcome="rolled_back")
        self.assertEqual("INVALID_ARGUMENT", replayed.exception.code)

    def test_owned_write_begin_and_finish_require_the_main_thread(self) -> None:
        adapter = self._adapter(FakeHou())
        before = adapter.start()
        begin_failures: list[HoudiniReadAdapterError] = []

        def begin_worker() -> None:
            try:
                self._begin_owned_write(adapter, before)
            except HoudiniReadAdapterError as exc:
                begin_failures.append(exc)

        worker = threading.Thread(target=begin_worker)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual("MAIN_THREAD_REQUIRED", begin_failures[0].code)
        self.assertEqual(before, adapter.capability_report())

        token = self._begin_owned_write(adapter, before)
        finish_failures: list[HoudiniReadAdapterError] = []

        def finish_worker() -> None:
            try:
                adapter.finish_owned_write(token, outcome="rolled_back")
            except HoudiniReadAdapterError as exc:
                finish_failures.append(exc)

        worker = threading.Thread(target=finish_worker)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual("MAIN_THREAD_REQUIRED", finish_failures[0].code)
        self.assertEqual(
            before,
            adapter.finish_owned_write(token, outcome="rolled_back"),
        )

    def test_owned_write_hip_events_invalidate_without_rewriting_observation(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)

        fake.trigger_save()
        observed_save = adapter.capability_report()
        with self.assertRaises(HoudiniReadAdapterError) as save_failure:
            adapter.finish_owned_write(token, outcome="rolled_back")
        self.assertEqual("SCENE_CONFLICT", save_failure.exception.code)
        after_save = adapter.capability_report()
        expected_after_save = dict(observed_save)
        expected_after_save["available"] = True
        self.assertEqual(expected_after_save, after_save)
        self.assertEqual(before["scene_revision"] + 1, after_save["scene_revision"])

        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)
        fake.trigger_load()
        changed_session = adapter.capability_report()
        with self.assertRaises(HoudiniReadAdapterError) as load_failure:
            adapter.finish_owned_write(token, outcome="rolled_back")
        self.assertEqual("HIP_SESSION_MISMATCH", load_failure.exception.code)
        self.assertEqual(changed_session, adapter.capability_report())
        self.assertNotEqual(
            before["hip_session_id"], changed_session["hip_session_id"]
        )
        self.assertEqual(0, changed_session["scene_revision"])

    def test_owned_write_off_main_callback_invalidates_sticky_fail_closed(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()
        token = self._begin_owned_write(adapter, before)

        worker = threading.Thread(target=fake.trigger_manual_change)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())

        failed = adapter.capability_report()
        self.assertFalse(failed["available"])
        self.assertFalse(failed["revision_observer_reliable"])
        with self.assertRaises(HoudiniReadAdapterError) as raised:
            adapter.finish_owned_write(token, outcome="indeterminate")
        self.assertEqual("HOUDINI_UNAVAILABLE", raised.exception.code)
        self.assertFalse(adapter.capability_report()["available"])

    def test_save_advances_snapshot_without_replacing_session_or_leaking_path(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        initial = adapter.start()
        fake.trigger_manual_change()
        dirty = adapter.capability_report()

        fake.trigger_save()
        saved = adapter.capability_report()

        self.assertEqual(initial["hip_session_id"], saved["hip_session_id"])
        self.assertEqual(dirty["scene_revision"] + 1, saved["scene_revision"])
        self.assertNotEqual(dirty["hip_fingerprint"], saved["hip_fingerprint"])
        self.assertNotIn("hidden", json.dumps(saved).casefold())

    def test_hip_lifecycle_replaces_session_and_reinstalls_observers(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        before = adapter.start()

        fake.trigger_load()
        changed = adapter.capability_report()
        self.assertNotEqual(before["hip_session_id"], changed["hip_session_id"])
        self.assertNotEqual(before["hip_fingerprint"], changed["hip_fingerprint"])
        self.assertEqual(0, changed["scene_revision"])
        self.assertFalse(changed["revision_observer_reliable"])

        refreshed = adapter.refresh()
        self.assertTrue(refreshed["available"])
        fake.trigger_manual_change()
        self.assertEqual(1, adapter.capability_report()["scene_revision"])

    def test_node_observer_registration_failure_never_becomes_fail_open(self) -> None:
        fake = FakeHou(reject_node_observers=True)
        adapter = self._adapter(fake)

        first = adapter.start()
        second = adapter.refresh()
        self.assertFalse(first["available"])
        self.assertFalse(second["available"])
        self.assertFalse(second["revision_observer_reliable"])

        fake.reject_node_observers = False
        recovered = adapter.refresh()
        self.assertTrue(recovered["available"])
        self.assertTrue(recovered["revision_observer_reliable"])

    def test_same_path_new_node_session_gets_a_fresh_observer(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        adapter.start()
        old = fake.obj

        replacement = fake.replace_node_instance("/obj")
        fake.obj = replacement
        report = adapter.refresh()

        self.assertTrue(report["available"])
        self.assertEqual(0, old.callback_count)
        self.assertEqual(1, replacement.callback_count)
        replacement.emit(fake.nodeEventType.ParmTupleChanged)
        self.assertEqual(1, adapter.capability_report()["scene_revision"])

    def test_off_main_observer_delivery_is_sticky_fail_closed(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        adapter.start()

        worker = threading.Thread(target=fake.trigger_manual_change)
        worker.start()
        worker.join(2)

        self.assertFalse(worker.is_alive())
        failed = adapter.capability_report()
        self.assertFalse(failed["available"])
        self.assertFalse(failed["revision_observer_reliable"])
        self.assertFalse(adapter.refresh()["available"])

    def test_unreliable_observer_and_live_catalog_conflicts_fail_closed(self) -> None:
        cases = (
            FakeHou(missing_node_event="InputRewired"),
            FakeHou(reject_hip_observer=True),
            FakeHou(missing_node_types=(("Sop", "transform"),)),
            FakeHou(missing_node_types=(("Sop", "merge"),)),
            FakeHou(parameter_conflict=("Sop", "box", "size")),
        )
        for fake in cases:
            with self.subTest(fake=fake):
                adapter = self._adapter(fake)
                report = adapter.start()
                self.assertFalse(report["available"])
                result = adapter.execute(
                    "houdini_scene_info", self._arguments(report)
                )
                self.assertEqual(
                    "HOUDINI_UNAVAILABLE", result["structured_error"]["code"]
                )

        unsafe_build = self._adapter(FakeHou(build="21.0.440\nunsafe")).start()
        self.assertFalse(unsafe_build["available"])

    def test_scene_info_is_bounded_owned_only_and_never_reports_user_path(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        adapter.start()
        digest = "a" * 64
        fake.add_hia_graph("HIA_Graph_fixture", digest)
        current = adapter.capability_report()
        result = adapter.execute(
            "houdini_scene_info", self._arguments(current)
        )

        self.assertTrue(result["ok"])
        self.registry.validate_output(
            "houdini_scene_info", self._arguments(current), result
        )
        scene = result["result"]
        self.assertEqual("21.0.440", scene["houdini_build"])
        self.assertEqual(["Object", "Sop"], scene["enabled_contexts"])
        self.assertEqual(1, len(scene["hia_graphs"]))
        self.assertEqual("/obj/HIA_Graph_fixture", scene["hia_graphs"][0]["root_path"])
        self.assertEqual("unknown", scene["hia_graphs"][0]["cook_state"])
        encoded = json.dumps(result)
        self.assertNotIn("C:\\", encoded)
        self.assertNotIn("AppData", encoded)
        self.assertNotIn("selected", encoded.casefold())

    def test_node_type_info_returns_only_requested_safe_metadata(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        report = adapter.start()
        arguments = self._arguments(
            report,
            node_types=[
                {"context": "Sop", "name": "box"},
                {"context": "Sop", "name": "transform"},
                {"context": "Sop", "name": "merge"},
            ],
        )
        result = adapter.execute("houdini_node_type_info", arguments)

        self.assertTrue(result["ok"])
        self.registry.validate_output("houdini_node_type_info", arguments, result)
        node_types = result["result"]["node_types"]
        self.assertEqual(
            ["box", "transform", "merge"],
            [item["requested_name"] for item in node_types],
        )
        self.assertEqual(
            ["box", "xform", "merge"],
            [item["resolved_name"] for item in node_types],
        )
        self.assertEqual(["size", "t"], [item["name"] for item in node_types[0]["parameters"]])
        self.assertEqual(9999, node_types[2]["input_count"])
        encoded = json.dumps(node_types).casefold()
        for forbidden in ("callback", "help", "definition", "file"):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(all(not item["creatable"] for item in node_types))
        self.assertTrue(
            all(
                not parameter["writable"] and not parameter["allows_expression"]
                for item in node_types
                for parameter in item["parameters"]
            )
        )

        live_name_query = self._arguments(
            report,
            node_types=[{"context": "Sop", "name": "xform"}],
        )
        rejected = adapter.execute("houdini_node_type_info", live_name_query)
        self.assertEqual(
            "NODE_TYPE_NOT_ALLOWED", rejected["structured_error"]["code"]
        )

    def test_stale_session_revision_deadline_and_unlisted_tools_fail_closed(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        report = adapter.start()
        arguments = self._arguments(report)

        graph_result = adapter.execute("houdini_graph_validate", {})
        self.assertEqual("TOOL_NOT_ALLOWED", graph_result["structured_error"]["code"])

        calls_before_deadline = len(fake.calls)
        expired = adapter.execute(
            "houdini_scene_info", arguments, absolute_deadline=0.0
        )
        self.assertEqual("DEADLINE_EXCEEDED", expired["structured_error"]["code"])
        self.assertEqual(calls_before_deadline, len(fake.calls))

        fake.trigger_manual_change()
        stale_revision = adapter.execute("houdini_scene_info", arguments)
        self.assertEqual("SCENE_CONFLICT", stale_revision["structured_error"]["code"])

        current = adapter.capability_report()
        stale_session_arguments = self._arguments(current)
        fake.trigger_clear()
        stale_session = adapter.execute("houdini_scene_info", stale_session_arguments)
        self.assertEqual(
            "HIP_SESSION_MISMATCH", stale_session["structured_error"]["code"]
        )

    def test_dispose_is_local_idempotent_and_removes_owned_callbacks(self) -> None:
        fake = FakeHou()
        adapter = self._adapter(fake)
        adapter.start()
        self.assertGreater(fake.observer_callback_count, 0)

        adapter.dispose()
        adapter.dispose()

        self.assertEqual(0, fake.observer_callback_count)
        self.assertFalse(adapter.capability_report()["available"])


if __name__ == "__main__":
    unittest.main()
