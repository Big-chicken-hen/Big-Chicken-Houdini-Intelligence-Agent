from __future__ import annotations

import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_SOURCE = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_panel"
    / "b4b_panel.py"
)
PANEL_ASSET = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_panels"
    / "hia_b4b_stairs_acceptance.pypanel"
)
CONTROLLER_SOURCE = PANEL_SOURCE.with_name("b4b_acceptance.py")


class B4BPanelAssetTests(unittest.TestCase):
    def test_controller_interface_and_snapshot_shape_are_aligned(self) -> None:
        source = CONTROLLER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        controller = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "B4BAcceptanceController"
        )
        methods = {
            node.name: node
            for node in controller.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "__init__",
                "state",
                "report",
                "prepare",
                "apply_once",
                "verify_manual_undo",
            }.issubset(methods)
        )
        constructor = methods["__init__"]
        self.assertEqual(
            ["self", "hou_module"],
            [argument.arg for argument in constructor.args.args],
        )
        self.assertIn(
            "pyside_version",
            [argument.arg for argument in constructor.args.kwonlyargs],
        )
        self.assertEqual(
            ["confirmed"],
            [argument.arg for argument in methods["apply_once"].args.kwonlyargs],
        )
        for name in ("state", "report"):
            decorators = methods[name].decorator_list
            self.assertTrue(
                any(
                    isinstance(decorator, ast.Name)
                    and decorator.id == "property"
                    for decorator in decorators
                )
            )

        snapshot_keys: set[str] | None = None
        for node in ast.walk(constructor):
            if not isinstance(node, ast.AnnAssign):
                continue
            target = node.target
            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "_snapshot"
                and isinstance(node.value, ast.Dict)
            ):
                continue
            snapshot_keys = {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            break
        self.assertEqual(
            {
                "ok",
                "state",
                "message",
                "manual_undo_required",
                "baseline",
                "capability",
                "approval",
                "apply_result",
                "verification",
                "event_journal",
            },
            snapshot_keys,
        )

    def test_pypanel_is_the_only_live_hou_entry_and_injects_it(self) -> None:
        document = ET.parse(PANEL_ASSET)
        interface = document.getroot().find("interface")
        self.assertIsNotNone(interface)
        self.assertEqual("hia_b4b_stairs_acceptance", interface.attrib["name"])
        self.assertEqual(
            "HIA Gate B4B Stairs Acceptance", interface.attrib["label"]
        )
        self.assertIsNone(interface.find("includeInToolbarMenu"))

        embedded = interface.find("script").text
        tree = ast.parse(embedded)
        hou_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any(alias.name == "hou" for alias in node.names)
        ]
        self.assertEqual(1, len(hou_imports))
        self.assertIn("B4BStairsAcceptancePanel", embedded)
        self.assertIn('kwargs.get("paneTab")', embedded)
        self.assertIn("hou_module=hou", embedded)

    def test_panel_is_a_thin_controller_only_ui(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue({"json", "typing", "PySide6"}.issubset(imported_roots))
        self.assertTrue(
            {
                "hou",
                "threading",
                "socket",
                "urllib",
                "http",
                "subprocess",
                "requests",
            }.isdisjoint(imported_roots)
        )

        self.assertEqual(1, source.count("self._controller.prepare()"))
        self.assertEqual(
            1, source.count("self._controller.apply_once(confirmed=True)")
        )
        self.assertEqual(
            1, source.count("self._controller.verify_manual_undo()")
        )
        self.assertIn("_SHARED_CONTROLLER", source)
        self.assertIn("_APPLY_CONSUMED", source)
        self.assertIn("_APPLY_ACCEPTANCE_PASSED", source)
        self.assertIn("_MANUAL_UNDO_REQUIRED", source)
        self.assertIn("_UNDO_VERIFIED", source)
        self.assertIn("B4BAcceptanceController", source)
        self.assertEqual(1, source.count("_APPLY_CONSUMED = False"))
        self.assertEqual(1, source.count("_APPLY_CONSUMED = True"))
        self.assertNotIn("def closeEvent", source)

        for forbidden in (
            "BridgeClient",
            "QtNetwork",
            "QNetworkAccessManager",
            "QNetworkReply",
            "HIA_BRIDGE_",
            "os.environ",
            "getenv(",
            "hou.undos",
            "performUndo",
            "triggerUpdate",
            "save(",
            "render(",
            "cook(",
            "createDigitalAsset",
            "open(",
            "write_text(",
            "write_bytes(",
            "print(",
            "logging.",
            "QTimer",
        ):
            self.assertNotIn(forbidden, source)

    def test_constructor_never_runs_acceptance_actions(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        panel_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "B4BStairsAcceptancePanel"
        )
        constructor = next(
            node
            for node in panel_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(constructor)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            {"prepare", "apply_once", "verify_manual_undo"}.isdisjoint(
                called_attributes
            )
        )

    def test_ui_requires_review_then_one_apply_and_manual_undo(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        self.assertIn("/obj/HIA_Graph_stairs_demo", source)
        self.assertIn("60 秒", source)
        self.assertIn("QCheckBox", source)
        self.assertIn("完整核对上述 JSON", source)
        self.assertIn("Apply 一次（不可重试）", source)
        self.assertIn("Network View", source)
        self.assertIn("Scene View", source)
        self.assertIn("Houdini 主菜单 Edit > Undo", source)
        self.assertIn("在该视图中按 Ctrl+Z 一次", source)
        self.assertIn("不要在本 Panel 的文本区域内按 Ctrl+Z", source)
        self.assertIn("不要 Redo", source)
        self.assertIn("json.dumps", source)
        self.assertIn("ensure_ascii=False", source)
        self.assertIn("indent=2", source)

        apply_method = source[
            source.index("    def _apply_once") : source.index(
                "    @QtCore.Slot()\n    def _verify_manual_undo"
            )
        ]
        consumed_before_call = apply_method.index("_APPLY_CONSUMED = True")
        controller_call = apply_method.index(
            "self._controller.apply_once(confirmed=True)"
        )
        self.assertLess(consumed_before_call, controller_call)
        exception_handler = apply_method.index("except Exception:")
        self.assertLess(controller_call, exception_handler)
        self.assertNotIn("_APPLY_CONSUMED = False", apply_method)

    def test_apply_exception_uses_only_authoritative_controller_report(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        apply_method = source[
            source.index("    def _apply_once") : source.index(
                "    @QtCore.Slot()\n    def _verify_manual_undo"
            )
        ]

        self.assertIn(
            "snapshot = _authoritative_controller_report(self._controller)",
            apply_method,
        )
        self.assertNotIn('_safe_failure_snapshot("apply")', apply_method)
        self.assertIn('_unknown_write_state_snapshot("apply")', apply_method)
        self.assertIn("if not authoritative:", apply_method)

        unknown_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_unknown_write_state_snapshot"
        )
        returned = next(
            node.value
            for node in ast.walk(unknown_function)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        )
        fields = {
            key.value: value
            for key, value in zip(returned.keys, returned.values)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertIn("manual_undo_required", fields)
        self.assertIsInstance(fields["manual_undo_required"], ast.Constant)
        self.assertIsNone(fields["manual_undo_required"].value)
        self.assertIn("写入状态不确定", source)

    def test_non_dict_snapshots_are_normalized_before_field_access(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        show_method = source[
            source.index("    def _show_snapshot") : source.index(
                "    @QtCore.Slot()\n    def _prepare"
            )
        ]
        apply_method = source[
            source.index("    def _apply_once") : source.index(
                "    @QtCore.Slot()\n    def _verify_manual_undo"
            )
        ]
        verify_method = source[
            source.index("    def _verify_manual_undo") : source.index(
                "    @QtCore.Slot()\n    def _refresh_controls"
            )
        ]

        self.assertIn("-> dict[str, Any]", show_method)
        self.assertIn("return snapshot", show_method)
        self.assertIn("snapshot = self._show_snapshot(snapshot)", apply_method)
        self.assertIn("snapshot = self._show_snapshot(snapshot)", verify_method)
        self.assertLess(
            apply_method.index("snapshot = self._show_snapshot(snapshot)"),
            apply_method.index('snapshot.get("ok")'),
        )
        self.assertLess(
            verify_method.index("snapshot = self._show_snapshot(snapshot)"),
            verify_method.index('snapshot.get("manual_undo_required")'),
        )

    def test_retained_root_cleanup_is_driven_by_manual_undo_not_apply_success(self) -> None:
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        apply_method = source[
            source.index("    def _apply_once") : source.index(
                "    @QtCore.Slot()\n    def _verify_manual_undo"
            )
        ]
        verify_method = source[
            source.index("    def _verify_manual_undo") : source.index(
                "    @QtCore.Slot()\n    def _refresh_controls"
            )
        ]
        refresh_method = source[source.index("    def _refresh_controls") :]

        self.assertIn(
            '_MANUAL_UNDO_REQUIRED = snapshot.get("manual_undo_required") is True',
            apply_method,
        )
        self.assertIn("elif _MANUAL_UNDO_REQUIRED:", apply_method)
        self.assertIn("清理通过也不代表本次 Gate 验收成功", apply_method)
        self.assertIn("and _MANUAL_UNDO_REQUIRED", refresh_method)
        self.assertNotIn("_APPLY_ACCEPTANCE_PASSED", refresh_method)
        self.assertIn("本次 Gate 不能标记成功", verify_method)

    def test_formal_panel_and_production_entry_remain_unmodified_read_only(self) -> None:
        formal_panel = PANEL_SOURCE.with_name("panel.py").read_text(encoding="utf-8")
        formal_asset = PANEL_ASSET.with_name("houdini_intelligence.pypanel").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("b4b_panel", formal_panel)
        self.assertNotIn("B4BStairsAcceptancePanel", formal_panel)
        self.assertNotIn("b4b_panel", formal_asset)
        self.assertNotIn("B4BStairsAcceptancePanel", formal_asset)


if __name__ == "__main__":
    unittest.main()
