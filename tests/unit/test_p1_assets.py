from __future__ import annotations

import ast
import json
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]


class P1AssetTests(unittest.TestCase):
    def test_houdini_package_is_project_local(self) -> None:
        package_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "packages"
            / "houdini_intelligence.json"
        )
        package = json.loads(package_path.read_text(encoding="utf-8"))
        self.assertTrue(package["enable"])
        self.assertEqual(
            "E:/houdini-intelligence-agent/houdini_package",
            package["path"],
        )
        encoded = json.dumps(package)
        self.assertNotIn("AppData", encoded)
        self.assertNotIn("WindowsApps", encoded)

    def test_python_panel_xml_and_embedded_script_parse(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_panels"
            / "houdini_intelligence.pypanel"
        )
        document = ET.parse(panel_path)
        interface = document.getroot().find("interface")
        self.assertIsNotNone(interface)
        self.assertEqual("houdini_intelligence", interface.attrib["name"])
        self.assertEqual("Houdini Intelligence", interface.attrib["label"])
        ast.parse(interface.find("script").text)

    def test_panel_python_has_no_houdini_scene_calls(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertNotIn("import hou", source)
        self.assertNotIn("hou.", source)
        self.assertIn("PySide6", source)

    def test_panel_displays_ignored_notification_method(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('method = event.get("method")', source)
        self.assertIn('method_text = f" method={method}"', source)
        self.assertIn("协议警告", source)

    def test_panel_uses_native_qt_input_method_without_event_interception(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("QtCore.Qt.WidgetAttribute.WA_InputMethodEnabled", source)
        self.assertIn("QtCore.Qt.InputMethodHint.ImhNone", source)
        self.assertIn("QtCore.Qt.FocusPolicy.StrongFocus", source)
        self.assertNotIn("def keyPressEvent", source)
        self.assertNotIn("def eventFilter", source)
        self.assertNotIn("QInputMethodEvent", source)
        self.assertNotIn("setFocus(", source)

    def test_panel_model_selectors_are_catalog_driven_and_forward_parameters(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("self._client.get_models()", source)
        self.assertIn("self._client.start_thread(model=self._selected_model_id())", source)
        self.assertIn("model=self._selected_model_id()", source)
        self.assertIn("effort=self._selected_effort()", source)
        self.assertIn('payload.get("models")', source)
        self.assertIn("supportedReasoningEfforts", source)
        self.assertIn("defaultReasoningEffort", source)

    def test_panel_wires_authoritative_terminal_and_bounded_reconciliation(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        terminal_branch = source.index(
            'error_code == "NO_ACTIVE_TURN" and details.get("turn_active") is False'
        )
        generic_failure = source.index(
            'self._append_system(f"{context} 失败：{format_bridge_error(payload)}")'
        )
        self.assertLess(terminal_branch, generic_failure)
        self.assertIn("reconcile_no_active_error", source)
        self.assertIn("claim_reconciliation", source)
        self.assertIn("_SESSION_RECONCILE_CONTEXT_PREFIX", source)
        self.assertIn("_PASSIVE_STATUS_NOTIFICATIONS", source)

    def test_bridge_client_captures_safe_qt_error_context(self) -> None:
        client_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "bridge_client.py"
        )
        source = client_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("reply.errorString()", source)
        self.assertIn("reply.error()", source)
        self.assertIn("HttpStatusCodeAttribute", source)
        self.assertIn('reply.property("hia_context")', source)
        self.assertIn("normalize_bridge_response", source)
        self.assertIn("request.setTransferTimeout(transfer_timeout)", source)
        self.assertIn("_RECONCILIATION_TIMEOUT_MS = 5_000", source)
        self.assertIn("deadline.timeout.connect(reply.abort)", source)
        self.assertIn("deadline.start(transfer_timeout)", source)

    def test_launcher_is_syntactically_valid_and_nonpersistent(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini-21.0.440.ps1"
        source = launcher.read_text(encoding="utf-8")
        self.assertIn(
            r"C:\Program Files\Side Effects Software\Houdini 21.0.440\bin\houdini.exe",
            source,
        )
        self.assertNotIn("setx", source.casefold())
        self.assertNotIn("--experimental", source)
        self.assertIsNone(re.search(r"(?m)^\s*'USERPROFILE'\s*=", source))
        self.assertIsNone(re.search(r"(?m)^\s*'HOME'\s*=", source))
        escaped_launcher = str(launcher).replace("'", "''")
        parse_command = (
            "[void][scriptblock]::Create([IO.File]::ReadAllText("
            f"'{escaped_launcher}'))"
        )
        command = ["powershell", "-NoProfile", "-Command", parse_command]
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
