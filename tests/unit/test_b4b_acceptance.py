from __future__ import annotations

import ast
import copy
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
for path in (
    REPOSITORY_ROOT / "src",
    REPOSITORY_ROOT / "services" / "bridge",
    REPOSITORY_ROOT / "houdini_package" / "python_libs",
    REPOSITORY_ROOT / "tests" / "fakes",
):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from fake_hou import FakeHou
from fake_hou_b4b import FakeB4BReadFacade, FakeHouB4B
from hia_panel.b4b_acceptance import (
    APPROVAL_PRESENTED,
    FAILED,
    VERIFIED,
    WAIT_MANUAL_UNDO,
    B4BAcceptanceError,
    B4BAcceptanceController,
    _AcceptanceReadFacade,
    _ProcessApplyLatch,
)
from hia_panel.houdini_read_adapter import HoudiniReadAdapter


EXPECTED_DIGEST = "0a9cf0fd98882d8916dcdd9edda77655e93d9bde2857409581b0eb54f65290c4"
TARGET = "/obj/HIA_Graph_stairs_demo"
CONTROLLER_SOURCE = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_panel"
    / "b4b_acceptance.py"
)


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class B4BAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _live_b2_read_adapter() -> HoudiniReadAdapter:
        return HoudiniReadAdapter(
            FakeHou(),
            publisher_id="b4b-live-shape-test",
            python_version="3.11.9",
            pyside_version="6.8.0",
            fingerprint_key=b"fixed-b4b-live-shape-key-32b",
            strict_event_evidence=True,
        )

    def _controller(
        self,
        *,
        hou: FakeHouB4B | None = None,
        read: FakeB4BReadFacade | None = None,
        latch: _ProcessApplyLatch | None = None,
        clock: _Clock | None = None,
    ) -> tuple[B4BAcceptanceController, FakeHouB4B, FakeB4BReadFacade, _Clock]:
        hou = hou or FakeHouB4B()
        read = read or FakeB4BReadFacade(hou)
        clock = clock or _Clock()
        controller = B4BAcceptanceController(
            hou,
            pyside_version="6.8.0",
            read_adapter=read,
            process_latch=latch or _ProcessApplyLatch(),
            clock=clock,
        )
        return controller, hou, read, clock

    def test_complete_one_shot_queue_writer_and_manual_undo_flow(self) -> None:
        controller, hou, _read, _clock = self._controller()

        prepared = controller.prepare()
        self.assertTrue(prepared["ok"])
        self.assertEqual(APPROVAL_PRESENTED, prepared["state"])
        self.assertFalse(prepared["manual_undo_required"])
        self.assertEqual("/obj", prepared["baseline"]["current"])
        self.assertEqual(EXPECTED_DIGEST, prepared["approval"]["canonical_graph_digest"])
        self.assertEqual(TARGET, prepared["approval"]["target_path"])
        self.assertEqual(
            "b1_fake_only_local_approval_claim_envelope_not_live_evidence",
            prepared["capability"]["local_ledger_envelope"],
        )
        self.assertEqual(
            5,
            prepared["approval"]["side_effect_summary"]["node_count"],
        )
        self.assertEqual(
            0,
            prepared["approval"]["side_effect_summary"]["file_write_count"],
        )

        applied = controller.apply_once(confirmed=True)
        self.assertTrue(applied["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, applied["state"])
        self.assertTrue(applied["manual_undo_required"])
        self.assertEqual("completed", applied["apply_result"]["queue_ledger"]["state"])
        self.assertTrue(
            applied["apply_result"]["independent_verification"]["ok"]
        )
        self.assertEqual(1, hou.undo_group_count)
        self.assertEqual(1, hou.undo_group_commits)
        self.assertEqual(0, hou.manual_undo_calls)
        self.assertEqual(19, len(hou.mutation_log))
        self.assertTrue(
            all(
                mutation.path == TARGET or mutation.path.startswith(f"{TARGET}/")
                for mutation in hou.mutation_log
            )
        )
        self.assertTrue(
            all(mutation.thread_id == threading.get_ident() for mutation in hou.mutation_log)
        )

        before_undo = controller.verify_manual_undo()
        self.assertFalse(before_undo["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, before_undo["state"])
        self.assertTrue(before_undo["manual_undo_required"])
        self.assertEqual(0, hou.manual_undo_calls)

        hou.manual_undo()
        verified = controller.verify_manual_undo()
        self.assertTrue(verified["ok"])
        self.assertEqual(VERIFIED, verified["state"])
        self.assertFalse(verified["manual_undo_required"])
        self.assertTrue(verified["verification"]["cleanup_restored"])
        self.assertTrue(
            verified["verification"]["proofs"]["exact_root_deletion_observed"]
        )
        self.assertTrue(
            verified["verification"]["proofs"]["all_declared_paths_absent"]
        )
        self.assertEqual(("/", "/obj"), hou.registry_paths)

    def test_owned_sibling_flag_switch_cluster_commits_one_revision(self) -> None:
        controller, _hou, _read, _clock = self._controller(
            hou=FakeHouB4B(coupled_display_flag_events=True)
        )

        self.assertTrue(controller.prepare()["ok"])
        applied = controller.apply_once(confirmed=True)

        self.assertTrue(applied["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, applied["state"])
        self.assertTrue(applied["apply_result"]["adapter_result"]["ok"])
        self.assertEqual(
            1, applied["apply_result"]["adapter_result"]["scene_revision"]
        )
        self.assertTrue(
            applied["apply_result"]["independent_verification"]["ok"]
        )
        self.assertTrue(all(event["matched"] for event in applied["event_journal"]))

    def test_process_latch_and_controller_state_forbid_second_apply(self) -> None:
        latch = _ProcessApplyLatch()
        first, first_hou, _read, _clock = self._controller(latch=latch)
        self.assertEqual(APPROVAL_PRESENTED, first.prepare()["state"])
        self.assertTrue(first.apply_once(confirmed=True)["manual_undo_required"])
        mutation_count = len(first_hou.mutation_log)
        second_same = first.apply_once(confirmed=True)
        self.assertFalse(second_same["ok"])
        self.assertEqual(mutation_count, len(first_hou.mutation_log))

        second, second_hou, _read2, _clock2 = self._controller(latch=latch)
        self.assertEqual(APPROVAL_PRESENTED, second.prepare()["state"])
        rejected = second.apply_once(confirmed=True)
        self.assertFalse(rejected["ok"])
        self.assertEqual(FAILED, rejected["state"])
        self.assertEqual([], second_hou.mutation_log)
        self.assertIn("already consumed", rejected["message"])

    def test_unconfirmed_approval_is_denied_without_hom_mutation(self) -> None:
        controller, hou, _read, _clock = self._controller()
        controller.prepare()
        rejected = controller.apply_once(confirmed=False)
        self.assertFalse(rejected["ok"])
        self.assertEqual(FAILED, rejected["state"])
        self.assertFalse(rejected["manual_undo_required"])
        self.assertEqual([], hou.mutation_log)
        self.assertEqual(0, hou.undo_group_count)

    def test_prepare_requires_new_clean_empty_unselected_hip(self) -> None:
        scenarios = []

        dirty = FakeHouB4B()
        dirty.hipFile.dirty = True
        scenarios.append(dirty)

        not_new = FakeHouB4B()
        not_new.hipFile.new_file = False
        scenarios.append(not_new)

        occupied = FakeHouB4B()
        occupied.seed_preexisting_child("/obj", "geo", "User_Object")
        scenarios.append(occupied)

        selected = FakeHouB4B()
        selected.set_selection_for_test(selected.node("/obj"))
        scenarios.append(selected)

        for hou in scenarios:
            with self.subTest(index=scenarios.index(hou)):
                controller, _hou, _read, _clock = self._controller(hou=hou)
                result = controller.prepare()
                self.assertFalse(result["ok"])
                self.assertEqual(FAILED, result["state"])
                self.assertEqual([], hou.mutation_log)
                self.assertEqual(0, hou.undo_group_count)

    def test_preapply_rejects_revision_and_current_node_drift(self) -> None:
        controller, hou, read, _clock = self._controller()
        self.assertEqual(APPROVAL_PRESENTED, controller.prepare()["state"])
        read.drift_revision_for_test()
        rejected = controller.apply_once(confirmed=True)
        self.assertFalse(rejected["ok"])
        self.assertEqual([], hou.mutation_log)

        controller2, hou2, _read2, _clock2 = self._controller()
        self.assertEqual(APPROVAL_PRESENTED, controller2.prepare()["state"])
        hou2.set_current_for_test(None)
        rejected2 = controller2.apply_once(confirmed=True)
        self.assertFalse(rejected2["ok"])
        self.assertEqual([], hou2.mutation_log)

    def test_off_main_thread_prepare_fails_before_hom_access(self) -> None:
        controller, hou, _read, _clock = self._controller()
        results: list[dict[str, object]] = []

        worker = threading.Thread(target=lambda: results.append(controller.prepare()))
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(FAILED, results[0]["state"])
        self.assertEqual([], hou.call_log)
        self.assertEqual([], hou.mutation_log)

    def test_prepare_reports_only_fixed_safe_stage_for_unexpected_error(self) -> None:
        controller, hou, read, _clock = self._controller()
        with mock.patch.object(read, "start", side_effect=RuntimeError("secret")):
            result = controller.prepare()

        self.assertFalse(result["ok"])
        self.assertEqual("FAILED", result["state"])
        self.assertEqual(
            "The read-only B4B preflight failed at live_read",
            result["message"],
        )
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual([], hou.mutation_log)

    def test_prepare_reports_fixed_blank_baseline_substage(self) -> None:
        controller, hou, _read, _clock = self._controller()
        with mock.patch.object(
            hou, "selectedNodes", side_effect=RuntimeError("secret")
        ):
            result = controller.prepare()

        self.assertFalse(result["ok"])
        self.assertEqual("FAILED", result["state"])
        self.assertEqual(
            "The read-only blank baseline failed at selection", result["message"]
        )
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual([], hou.mutation_log)

    def test_prepare_accepts_positional_selected_nodes_compatibility(self) -> None:
        controller, hou, _read, _clock = self._controller()
        original = hou.selectedNodes

        def selected_nodes(*args: object, **kwargs: object) -> object:
            if kwargs:
                raise TypeError("keyword form unavailable")
            return original(include_hidden=bool(args[0]))

        with mock.patch.object(hou, "selectedNodes", side_effect=selected_nodes):
            result = controller.prepare()

        self.assertTrue(result["ok"])
        self.assertEqual(APPROVAL_PRESENTED, result["state"])
        self.assertEqual([], hou.mutation_log)

    def test_prepare_uses_existing_network_editor_when_pwd_is_unavailable(self) -> None:
        controller, hou, _read, _clock = self._controller()
        editor = mock.Mock()
        editor.pwd.return_value = hou.node("/obj")
        desktop = mock.Mock()
        desktop.paneTabOfType.return_value = editor
        hou.ui = mock.Mock()
        hou.ui.curDesktop.return_value = desktop
        hou.paneTabType = mock.Mock()
        hou.paneTabType.NetworkEditor = object()

        with mock.patch.object(hou, "pwd", side_effect=RuntimeError("unavailable")):
            result = controller.prepare()

        self.assertTrue(result["ok"])
        self.assertEqual(APPROVAL_PRESENTED, result["state"])
        self.assertEqual("/obj", result["baseline"]["current"])
        desktop.paneTabOfType.assert_called_once_with(hou.paneTabType.NetworkEditor)
        editor.pwd.assert_called_once_with()
        self.assertEqual([], hou.mutation_log)

    def test_strict_missing_event_retains_cleanup_path_and_never_retries(self) -> None:
        controller, hou, _read, _clock = self._controller()
        controller.prepare()
        hou.suppressed_event_operations.add("ParmTupleChanged")
        failed = controller.apply_once(confirmed=True)
        self.assertFalse(failed["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, failed["state"])
        self.assertTrue(failed["manual_undo_required"])
        self.assertIsNotNone(hou.node(TARGET))
        mutations = len(hou.mutation_log)

        retry = controller.apply_once(confirmed=True)
        self.assertFalse(retry["ok"])
        self.assertEqual(mutations, len(hou.mutation_log))

        hou.suppressed_event_operations.clear()
        hou.manual_undo()
        cleanup = controller.verify_manual_undo()
        self.assertFalse(cleanup["ok"])
        self.assertEqual(FAILED, cleanup["state"])
        self.assertFalse(cleanup["manual_undo_required"])
        self.assertTrue(cleanup["verification"]["cleanup_restored"])

    def test_user_data_without_hom_event_uses_exact_readback_evidence(self) -> None:
        controller, hou, read, _clock = self._controller()
        hou.suppressed_event_operations.add("CustomDataChanged")

        self.assertTrue(controller.prepare()["ok"])
        applied = controller.apply_once(confirmed=True)

        self.assertTrue(applied["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, applied["state"])
        evidence = read.last_owned_evidence()
        assert evidence is not None
        metadata_mutations = [
            mutation
            for mutation in evidence["mutations"]
            if mutation["operation"].startswith("set_user_data:")
        ]
        self.assertEqual(3, len(metadata_mutations))
        self.assertTrue(
            all(mutation["event_count"] == 0 for mutation in metadata_mutations)
        )
        self.assertTrue(
            all(
                mutation["exact_readback_proven"] is True
                for mutation in metadata_mutations
            )
        )

        hou.manual_undo()
        self.assertTrue(controller.verify_manual_undo()["ok"])

    def test_ledger_failure_after_actual_write_still_allows_manual_cleanup(self) -> None:
        controller, hou, _read, _clock = self._controller()
        controller.prepare()
        with mock.patch.object(
            controller._queue,
            "complete",
            side_effect=RuntimeError("injected ledger failure"),
        ):
            failed = controller.apply_once(confirmed=True)
        self.assertFalse(failed["ok"])
        self.assertEqual(WAIT_MANUAL_UNDO, failed["state"])
        self.assertTrue(failed["manual_undo_required"])
        self.assertIsNotNone(hou.node(TARGET))
        self.assertEqual(1, hou.undo_group_commits)
        hou.manual_undo()
        cleanup = controller.verify_manual_undo()
        self.assertTrue(cleanup["verification"]["cleanup_restored"])
        self.assertFalse(cleanup["manual_undo_required"])

    def test_manual_undo_requires_exact_parent_child_deletion_evidence(self) -> None:
        controller, hou, _read, _clock = self._controller()
        controller.prepare()
        self.assertTrue(controller.apply_once(confirmed=True)["ok"])
        hou.suppressed_event_operations.add("ChildDeleted")
        hou.manual_undo()
        rejected = controller.verify_manual_undo()
        self.assertFalse(rejected["ok"])
        self.assertFalse(
            rejected["verification"]["proofs"]["exact_root_deletion_observed"]
        )

    def test_manual_undo_accepts_dirty_new_hip_when_scene_is_restored(self) -> None:
        controller, hou, _read, _clock = self._controller()
        prepared = controller.prepare()
        self.assertFalse(prepared["baseline"]["dirty"])
        self.assertTrue(controller.apply_once(confirmed=True)["ok"])

        hou.manual_undo()
        hou.hipFile.dirty = True
        verified = controller.verify_manual_undo()

        self.assertTrue(verified["ok"])
        self.assertEqual(VERIFIED, verified["state"])
        self.assertTrue(verified["verification"]["cleanup_restored"])
        self.assertTrue(verified["verification"]["proofs"]["new_hip"])
        self.assertTrue(verified["verification"]["has_unsaved_changes"])

    def test_manual_undo_rejects_unknown_journal_source(self) -> None:
        controller, hou, _read, _clock = self._controller()
        controller.prepare()
        self.assertTrue(controller.apply_once(confirmed=True)["ok"])
        hou.event_source_overrides["ChildDeleted"] = hou.sentinel
        hou.manual_undo()
        rejected = controller.verify_manual_undo()
        self.assertFalse(rejected["ok"])
        self.assertFalse(
            rejected["verification"]["proofs"]["deletion_journal_scope_safe"]
        )

    def test_reports_are_json_serializable_and_never_expose_claim_credentials(self) -> None:
        controller, hou, _read, _clock = self._controller()
        snapshots = [controller.report, controller.prepare(), controller.apply_once(confirmed=True)]
        hou.manual_undo()
        snapshots.append(controller.verify_manual_undo())
        for snapshot in snapshots:
            serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            self.assertEqual(snapshot, json.loads(serialized))
            lowered = serialized.casefold()
            for forbidden in (
                "claim_token",
                "executor_token",
                "process_nonce",
                "authorization",
                "bearer ",
                "auth.json",
                "cookie",
            ):
                self.assertNotIn(forbidden, lowered)

    def test_fixture_and_approval_are_exact_not_asset_logic_in_writer(self) -> None:
        controller, _hou, _read, _clock = self._controller()
        prepared = controller.prepare()
        graph = prepared["approval"]["normalized_graph"]
        self.assertEqual("HIA_Graph_stairs_demo", graph["target"]["name_hint"])
        self.assertEqual(EXPECTED_DIGEST, prepared["approval"]["canonical_graph_digest"])
        self.assertEqual(
            EXPECTED_DIGEST,
            prepared["approval"]["approval_payload"]["canonical_graph_digest"],
        )
        self.assertEqual(5, len(graph["nodes"]))
        self.assertEqual(5, len(graph["connections"]))

    def test_promote_accepts_real_b2_nested_tuple_component_schema(self) -> None:
        promoted = _AcceptanceReadFacade(self._live_b2_read_adapter()).start()
        catalog = {
            (item["context"], item["requested_name"]): item
            for item in promoted["catalog"]
        }

        expected = {
            ("Sop", "box"): {"size": ("float", 3), "t": ("float", 3)},
            ("Sop", "transform"): {"t": ("float", 3)},
        }
        for key, expected_parameters in expected.items():
            parameters = {
                item["name"]: (item["items_type"], item["tuple_size"])
                for item in catalog[key]["parameters"]
            }
            self.assertEqual(expected_parameters, parameters)

    def test_promote_rejects_invalid_nested_tuple_component_schema(self) -> None:
        report = self._live_b2_read_adapter().start()

        def box_size(candidate: dict[str, object]) -> dict[str, object]:
            box = next(
                item
                for item in candidate["catalog"]
                if item["context"] == "Sop" and item["requested_name"] == "box"
            )
            return next(item for item in box["parameters"] if item["name"] == "size")

        def set_items_type_to_int(parameter: dict[str, object]) -> None:
            parameter["default_value"]["items_type"] = "int"

        def remove_items_type(parameter: dict[str, object]) -> None:
            parameter["default_value"].pop("items_type")

        def set_items_type_to_invalid_value(parameter: dict[str, object]) -> None:
            parameter["default_value"]["items_type"] = ["float"]

        def replace_default_with_non_mapping(parameter: dict[str, object]) -> None:
            parameter["default_value"] = ["tuple", "float"]

        def replace_default_type(parameter: dict[str, object]) -> None:
            parameter["default_value"]["type"] = "float"

        mutations = (
            set_items_type_to_int,
            remove_items_type,
            set_items_type_to_invalid_value,
            replace_default_with_non_mapping,
            replace_default_type,
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation.__name__):
                candidate = copy.deepcopy(report)
                mutation(box_size(candidate))
                with self.assertRaises(B4BAcceptanceError) as raised:
                    _AcceptanceReadFacade._promote(candidate)
                self.assertEqual("CAPABILITY_MISMATCH", raised.exception.code)

    def test_controller_source_has_no_live_import_network_or_automatic_undo(self) -> None:
        source = CONTROLLER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue(
            {
                "hou",
                "PySide6",
                "socket",
                "urllib",
                "http",
                "requests",
                "subprocess",
                "os",
            }.isdisjoint(imported_roots)
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            {
                "undo",
                "redo",
                "performUndo",
                "performRedo",
                "save",
                "cook",
                "render",
                "createDigitalAsset",
            }.isdisjoint(called_attributes)
        )
        self.assertIn("strict_json_loads", source)
        self.assertIn('"services" / "bridge"', source)
        self.assertIn("relative_to(_PROJECT_ROOT)", source)


if __name__ == "__main__":
    unittest.main()
