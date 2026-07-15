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
    def test_houdini_package_contains_no_qt_network_transport(self) -> None:
        package_root = REPOSITORY_ROOT / "houdini_package"
        forbidden = (
            "QtNetwork",
            "QNetworkAccessManager",
            "QNetworkReply",
            "reply.finished",
        )
        inspected = []
        for path in sorted(package_root.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".pypanel", ".json"}:
                continue
            source = path.read_text(encoding="utf-8")
            inspected.append(path)
            for token in forbidden:
                self.assertNotIn(token, source, f"{token} remains in {path}")
        self.assertTrue(inspected)

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

    def test_offline_ime_diagnostic_panel_is_stock_and_content_blind(self) -> None:
        source_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "ime_diagnostic.py"
        )
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_panels"
            / "hia_ime_diagnostic.pypanel"
        )
        source = source_path.read_text(encoding="utf-8")
        ast.parse(source)
        document = ET.parse(panel_path)
        interface = document.getroot().find("interface")
        self.assertIsNotNone(interface)
        self.assertEqual("hia_ime_diagnostic", interface.attrib["name"])
        self.assertEqual("HIA IME Diagnostic (Offline)", interface.attrib["label"])
        ast.parse(interface.find("script").text)

        self.assertEqual(1, source.count("QtWidgets.QLineEdit()"))
        self.assertEqual(1, source.count("QtWidgets.QTextEdit()"))
        self.assertEqual(1, source.count("QtWidgets.QPlainTextEdit()"))
        self.assertEqual(1, source.count("QtCore.QTimer(self)"))
        for forbidden in (
            "setAttribute(",
            "setInputMethodHints(",
            "setFocusPolicy(",
            "eventFilter",
            "keyPressEvent",
            "inputMethodEvent",
            "focusProxy",
            "viewport",
            "QInputMethodEvent",
            "BridgeClient",
            "urllib",
            "http.client",
            "QtNetwork",
            ".text(",
            "toPlainText(",
            "selectedText(",
            "displayText(",
            "toHtml(",
            "toMarkdown(",
            ".document(",
        ):
            self.assertNotIn(forbidden, source)

        allowed_record_fields = {
            "focusWidget",
            "QLineEdit",
            "QTextEdit",
            "QPlainTextEdit",
            "hasFocus",
            "WA_InputMethodEnabled",
            "inputMethodHints",
            "inputMethod().isVisible",
        }
        tree = ast.parse(source)
        recorded_fields = {
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertEqual(allowed_record_fields, recorded_fields)

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

    def test_panel_uses_stock_qtextedit_without_input_method_overrides(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("self.input_edit = QtWidgets.QTextEdit()", source)
        self.assertNotIn("self.input_edit = QtWidgets.QPlainTextEdit()", source)
        for forbidden in (
            "self.input_edit.setAttribute(",
            "self.input_edit.setInputMethodHints(",
            "self.input_edit.setFocusPolicy(",
            "WA_InputMethodEnabled",
            "ImhNone",
            "StrongFocus",
            "def keyPressEvent",
            "def eventFilter",
            "def inputMethodEvent",
            "QInputMethodEvent",
            "focusProxy",
            "self.input_edit.viewport(",
            "self.input_edit.viewport()",
            "setFocus(",
        ):
            self.assertNotIn(forbidden, source)

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

    def test_bridge_client_uses_stdlib_workers_and_main_thread_queue_drain(self) -> None:
        client_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "bridge_client.py"
        )
        transport_path = client_path.with_name("http_transport.py")
        response_path = client_path.with_name("network_response.py")
        client_source = client_path.read_text(encoding="utf-8")
        transport_source = transport_path.read_text(encoding="utf-8")
        response_source = response_path.read_text(encoding="utf-8")
        ast.parse(client_source)
        ast.parse(transport_source)
        ast.parse(response_source)

        self.assertIn("from PySide6 import QtCore", client_source)
        self.assertIn("queue.Queue", client_source)
        self.assertIn("threading.Lock()", client_source)
        self.assertIn("QtCore.QTimer(self)", client_source)
        self.assertEqual(1, client_source.count("QtCore.QTimer(self)"))
        self.assertIn("self._drain_timer.timeout.connect(self._drain_results)", client_source)
        self.assertIn("self._expire_deadlines()", client_source)
        self.assertIn("self._latest_request_by_context", client_source)
        self.assertIn("self._generation", client_source)
        self.assertIn("self._event_request_active", client_source)
        self.assertIn("_RECONCILIATION_TIMEOUT_MS = 5_000", client_source)
        self.assertIn("_EVENT_POLL_TIMEOUT_MS = 20_000", client_source)
        self.assertIn("_DEFAULT_REQUEST_TIMEOUT_MS = 15_000", client_source)
        self.assertIn("def dispose(self)", client_source)
        self.assertNotIn("def shutdown(self)", client_source)
        self.assertNotIn("/v1/shutdown", client_source)
        self.assertNotIn("allow_during_close", client_source)

        self.assertIn("import urllib.request", transport_source)
        self.assertIn("threading.Thread(", transport_source)
        self.assertIn("daemon=True", transport_source)
        self.assertIn('name="HIA-Bridge-events"', transport_source)
        self.assertIn('name=f"HIA-Bridge-control-', transport_source)
        self.assertIn("self._result_queue.put_nowait(result)", transport_source)
        self.assertIn("Bearer ", transport_source)
        self.assertIn("http://127.0.0.1:<port>", transport_source)
        self.assertNotIn("from PySide6", transport_source)
        self.assertNotIn("print(", transport_source)
        self.assertNotIn("logging", transport_source)

        self.assertIn("error_kind", response_source)
        self.assertIn("request_id", response_source)
        self.assertIn("generation", response_source)
        self.assertNotIn("qt_error", response_source)

    def test_launcher_is_syntactically_valid_and_nonpersistent(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini-21.0.440.ps1"
        source = launcher.read_text(encoding="utf-8")
        self.assertIn(
            r"C:\Program Files\Side Effects Software\Houdini 21.0.440\bin\houdini.exe",
            source,
        )
        self.assertNotIn("setx", source.casefold())
        self.assertNotIn("--experimental", source)
        self.assertEqual(1, source.count("/v1/shutdown"))
        self.assertLess(
            source.index("$houdiniProcess.WaitForExit()"),
            source.index("/v1/shutdown"),
        )
        self.assertIn("$houdiniStarted = $false", source)
        self.assertIn("$houdiniExited = $false", source)
        self.assertIn("$houdiniStarted = $true", source)
        self.assertIn("$houdiniExited = $houdiniProcess.HasExited", source)
        self.assertIn("$bridgeCleanupAllowed = (-not $houdiniStarted) -or (", source)
        shutdown_guard = source.rindex(
            "if ($bridgeCleanupAllowed -and $bridgeStarted",
            0,
            source.index("/v1/shutdown"),
        )
        self.assertLess(shutdown_guard, source.index("/v1/shutdown"))
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

    def test_panel_close_has_only_local_lifecycle_ownership(self) -> None:
        panel_source = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        ).read_text(encoding="utf-8")
        client_source = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "bridge_client.py"
        ).read_text(encoding="utf-8")

        self.assertIn("client.dispose()", panel_source)
        self.assertIn("self._client = None", panel_source)
        self.assertIn("self._polling_enabled = False", panel_source)
        self.assertNotIn("/v1/shutdown", panel_source)
        self.assertNotIn(".shutdown()", panel_source)
        self.assertNotIn("/v1/shutdown", client_source)
        self.assertNotIn("def shutdown", client_source)


if __name__ == "__main__":
    unittest.main()
