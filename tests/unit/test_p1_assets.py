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
            "$HIA_PROJECT_ROOT/houdini_package",
            package["path"],
        )
        encoded = json.dumps(package)
        self.assertNotIn(str(REPOSITORY_ROOT), encoded)
        self.assertNotIn("E:/houdini-intelligence-agent", encoded)
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
        self.assertEqual("Big-Chicken Houdini Intelligence Agent", interface.attrib["label"])
        embedded = interface.find("script").text
        tree = ast.parse(embedded)
        hou_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any(alias.name == "hou" for alias in node.names)
        ]
        self.assertEqual(1, len(hou_imports))
        self.assertIn("hou_module=hou", embedded)

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

    def test_hou_import_is_confined_to_the_hia_ui_runtime(self) -> None:
        package_root = REPOSITORY_ROOT / "houdini_package"
        hia_runtime = (
            package_root
            / "python_libs"
            / "hia_mcp_runtime"
            / "executor.py"
        )
        offenders: list[str] = []
        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name == "hou" for alias in node.names
                ):
                    offenders.append(str(path.relative_to(REPOSITORY_ROOT)))
                if isinstance(node, ast.ImportFrom) and node.module == "hou":
                    offenders.append(str(path.relative_to(REPOSITORY_ROOT)))
        self.assertEqual(
            [str(hia_runtime.relative_to(REPOSITORY_ROOT))],
            offenders,
        )

    def test_only_reviewed_houdini_runtimes_contain_direct_scene_calls(self) -> None:
        package_root = REPOSITORY_ROOT / "houdini_package"
        forbidden_attributes = {
            "createNode",
            "setInput",
            "setParms",
            "setParm",
            "destroy",
            "saveAndIncrementFileName",
            "cook",
            "render",
            "createDigitalAsset",
            "definition",
            "installFile",
            "uninstallFile",
            "reloadAllFiles",
        }
        dormant = (
            package_root
            / "python_libs"
            / "hia_panel"
            / "houdini_write_adapter.py"
        )
        local_acceptance = (
            package_root
            / "python_libs"
            / "hia_panel"
            / "b4b_acceptance.py"
        )
        hia_runtime = (
            package_root
            / "python_libs"
            / "hia_mcp_runtime"
            / "executor.py"
        )
        found: dict[Path, set[str]] = {}
        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and (
                        node.func.attr in forbidden_attributes
                        or (
                            node.func.attr == "save"
                            and isinstance(node.func.value, ast.Attribute)
                            and node.func.value.attr == "hipFile"
                        )
                    )
                ):
                    found.setdefault(path, set()).add(node.func.attr)
        self.assertEqual({dormant, hia_runtime}, set(found))
        self.assertEqual({"createNode", "setInput", "destroy"}, found[dormant])
        self.assertEqual({"cook"}, found[hia_runtime])

        for path in sorted(package_root.rglob("*.py")):
            if path in {dormant, local_acceptance}:
                continue
            self.assertNotIn(
                "houdini_write_adapter",
                path.read_text(encoding="utf-8"),
                str(path.relative_to(REPOSITORY_ROOT)),
            )

        acceptance_source = local_acceptance.read_text(encoding="utf-8")
        self.assertIn("from .houdini_write_adapter import", acceptance_source)
        self.assertNotIn("b4b_acceptance", (package_root / "python_libs" / "hia_panel" / "panel.py").read_text(encoding="utf-8"))
        self.assertNotIn("b4b_acceptance", (package_root / "python_panels" / "houdini_intelligence.pypanel").read_text(encoding="utf-8"))

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
        self.assertIn('method_text = str(method)', source)
        self.assertIn("协议提示：", source)
        self.assertIn("known-request-user-input", source)

    def test_panel_uses_expandable_qtextedit_with_local_send_shortcuts(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        composer_path = panel_path.with_name("composer.py")
        panel_source = panel_path.read_text(encoding="utf-8")
        composer_source = composer_path.read_text(encoding="utf-8")
        ast.parse(panel_source)
        ast.parse(composer_source)

        self.assertIn(
            "self.input_edit = ExpandableTextEdit(self)",
            panel_source,
        )
        self.assertNotIn(
            "self.input_edit = QtWidgets.QPlainTextEdit()",
            panel_source,
        )
        self.assertIn(
            "class ExpandableTextEdit(QtWidgets.QTextEdit):",
            composer_source,
        )
        self.assertIn('"Ctrl+Return"', composer_source)
        self.assertIn('"Ctrl+Enter"', composer_source)
        self.assertIn("QtGui.QShortcut(", composer_source)
        self.assertIn("QtGui.QKeySequence(sequence)", composer_source)
        self.assertIn(
            "shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)",
            composer_source,
        )
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
            "installEventFilter",
            "QInputMethodEvent",
            "focusProxy",
            "self.input_edit.viewport(",
            "self.input_edit.viewport()",
        ):
            self.assertNotIn(forbidden, panel_source)
            self.assertNotIn(forbidden, composer_source)

    def test_panel_conversation_view_is_native_pyside6(self) -> None:
        conversation_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "conversation_view.py"
        )
        source = conversation_path.read_text(encoding="utf-8")
        ast.parse(source)

        self.assertIn("from PySide6 import QtCore, QtGui, QtWidgets", source)
        self.assertIn("class ConversationView(QtWidgets.QWidget):", source)
        self.assertIn("QtWidgets.QTextBrowser", source)
        self.assertIn("self.setMarkdown(text)", source)
        self.assertIn('self.toggle_button.setText("展开详情")', source)
        self.assertIn('self.toggle_button.setText("收起详情"', source)
        self.assertNotIn("QtWebEngine", source)
        self.assertNotIn("QWebEngine", source)

    def test_panel_attachment_widgets_cover_file_clipboard_thumbnail_and_remove(self) -> None:
        panel_path = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        )
        composer_path = panel_path.with_name("composer.py")
        panel_source = panel_path.read_text(encoding="utf-8")
        composer_source = composer_path.read_text(encoding="utf-8")
        ast.parse(panel_source)
        ast.parse(composer_source)

        self.assertIn("dialog = QtWidgets.QFileDialog(", panel_source)
        self.assertIn("QtWidgets.QFileDialog.Option.DontUseNativeDialog", panel_source)
        self.assertIn("QtWidgets.QFileDialog.FileMode.ExistingFiles", panel_source)
        self.assertIn("QtCore.Qt.WindowModality.NonModal", panel_source)
        self.assertIn("dialog.show()", panel_source)
        self.assertNotIn("dialog.open()", panel_source)
        self.assertIn(
            "dialog.finished.connect(self._attachment_dialog_finished)",
            panel_source,
        )
        self.assertIn("self._attachment_dialog = None", panel_source)
        self.assertIn("dialog.deleteLater()", panel_source)
        self.assertNotIn("QtWidgets.QFileDialog.getOpenFileNames(", panel_source)
        for forbidden in (
            "ApplicationModal",
            "activateWindow",
            "raise_",
            "grabKeyboard",
            "grabMouse",
            "processEvents",
            "clearFocus(",
        ):
            self.assertNotIn(forbidden, panel_source)
        self.assertIn("self._attachment_store.copy_file(", panel_source)
        self.assertIn("self._attachment_store.clipboard_path(", panel_source)
        self.assertIn('image.save(path, "PNG")', panel_source)
        self.assertIn("def insertFromMimeData", composer_source)
        self.assertIn("source.hasImage()", composer_source)
        self.assertIn("QtGui.QPixmap(path)", composer_source)
        self.assertIn("class AttachmentStrip(QtWidgets.QWidget):", composer_source)
        self.assertIn("self.remove(attachment_path)", composer_source)

    def test_panel_has_no_manual_context_compaction_surface(self) -> None:
        panel_root = REPOSITORY_ROOT / "houdini_package" / "python_libs" / "hia_panel"
        bridge_root = REPOSITORY_ROOT / "services" / "bridge" / "hia_bridge"
        relevant_paths = (
            panel_root / "panel.py",
            panel_root / "conversation_view.py",
            panel_root / "bridge_client.py",
            panel_root / "http_transport.py",
            bridge_root / "http_server.py",
            bridge_root / "session.py",
            bridge_root / "codex_stdio.py",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in relevant_paths)

        for forbidden in (
            "压缩并继续",
            "thread/compact/start",
            '"/v1/compact"',
            "compact_button",
            "manual_compact",
            "local_summarizer",
        ):
            self.assertNotIn(forbidden, combined)

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
        self.assertIn("self._client.start_thread(", source)
        self.assertIn("model=self._selected_model_id()", source)
        self.assertIn("effort=self._selected_effort()", source)
        self.assertIn("service_tier=self._selected_service_tier()", source)
        self.assertIn('payload.get("models")', source)
        self.assertIn("supportedReasoningEfforts", source)
        self.assertIn("defaultReasoningEffort", source)
        self.assertIn("serviceTiers", source)
        self.assertIn("defaultServiceTier", source)

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
            'error_code == "NO_ACTIVE_TURN"'
        )
        generic_failure = source.index(
            'self._append_system(f"{context} 失败：{formatted_error}")'
        )
        self.assertLess(terminal_branch, generic_failure)
        self.assertIn('details.get("turn_active") is False', source[terminal_branch:generic_failure])
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
        self.assertIn("self._scene_request_active", client_source)
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
        self.assertIn("X-HIA-Executor-Token", transport_source)
        self.assertNotIn("from PySide6", transport_source)
        self.assertNotIn("print(", transport_source)
        self.assertNotIn("logging", transport_source)

        self.assertIn("error_kind", response_source)
        self.assertIn("request_id", response_source)
        self.assertIn("generation", response_source)
        self.assertNotIn("qt_error", response_source)

    def test_generic_launcher_is_version_neutral_and_preserves_discovery(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        self.assertTrue(launcher.is_file())
        source = launcher.read_text(encoding="utf-8")

        self.assertIsNone(re.search(r"\b(?:21|22)(?:\.\d+)*\b", source))
        self.assertNotIn(r"C:\Program Files\Side Effects Software", source)
        self.assertIn("[string]$BridgePython =", source)
        self.assertIn("[string]$HoudiniExe = ''", source)
        self.assertNotIn("$ExpectedRoot", source)
        self.assertIn("Resolve-ProjectCodexExecutable", source)
        self.assertIn("Join-Path $ResolvedRoot '.runtime\\codex-home'", source)
        self.assertIn("function Resolve-HoudiniExecutable", source)
        self.assertIn("if ($RequestedPath)", source)
        self.assertIn("$env:HFS", source)
        self.assertIn("Get-Command -Name 'houdini.exe' -All", source)
        self.assertIn("App Paths\\houdini.exe", source)
        self.assertIn("CurrentVersion\\Uninstall", source)
        self.assertIn(
            "$installProperty = $properties.PSObject.Properties['InstallLocation']",
            source,
        )
        self.assertIn(
            "Multiple Houdini executables were discovered. Pass -HoudiniExe",
            source,
        )

    def test_generic_launcher_preserves_executable_and_lifecycle_guards(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        source = launcher.read_text(encoding="utf-8")

        self.assertIn("ordinary absolute drive path", source)
        self.assertIn("$rawCandidatePath.Substring(2).Contains(':')", source)
        self.assertIn("Test-Path -LiteralPath $candidatePath -PathType Leaf", source)
        self.assertIn("Resolve-Path -LiteralPath $candidatePath", source)
        self.assertIn("$item.Name, 'houdini.exe'", source)
        self.assertIn("$item.VersionInfo.ProductVersion", source)
        self.assertIn("$item.VersionInfo.FileVersion", source)
        self.assertIn("if (-not $build)", source)
        self.assertIn("$productName = [string]$item.VersionInfo.ProductName", source)
        self.assertIn("$description = [string]$item.VersionInfo.FileDescription", source)
        self.assertIn("$companyName = [string]$item.VersionInfo.CompanyName", source)
        self.assertIn("function Test-HoudiniExecutableMetadata", source)
        self.assertIn("Test-HoudiniExecutableMetadata", source)
        self.assertIn("Side Effects Software(?: Inc\\.)?", source)
        self.assertIn("Houdini executable is a reparse point", source)
        self.assertIn("Houdini path traverses a reparse point", source)
        self.assertIn("$houdiniMetadata = Resolve-HoudiniExecutable", source)
        leaf_reparse_guard = source.index(
            'throw "Houdini executable is a reparse point: $resolvedPath"'
        )
        parent_walk = source.index("$current = $item.Directory")
        self.assertLess(leaf_reparse_guard, parent_walk)

        self.assertNotIn("setx", source.casefold())
        self.assertNotIn("--experimental", source)
        self.assertIn("function New-CryptographicToken", source)
        self.assertIn("function New-LoopbackBridgeUrl", source)
        self.assertIn("[System.Net.Sockets.TcpListener]::new(", source)
        self.assertIn("[System.Net.IPAddress]::Loopback", source)
        self.assertIn("$listener.Start()", source)
        self.assertIn("$listener.Stop()", source)
        self.assertIn("$bridgeUrl = New-LoopbackBridgeUrl", source)
        self.assertIn("RandomNumberGenerator]::Create()", source)
        self.assertIn("$bridgeToken = New-CryptographicToken", source)
        self.assertIn("$sceneExecutorToken = New-CryptographicToken", source)
        self.assertIn("'HIA_BRIDGE_TOKEN' = $bridgeToken", source)
        self.assertEqual(2, source.count("'HIA_BRIDGE_URL' = $bridgeUrl"))
        self.assertIn("HIA_SCENE_EXECUTOR_TOKEN", source)
        self.assertIn("'HIA_SCENE_EXECUTOR_TOKEN' = $sceneExecutorToken", source)
        self.assertIn("'HIA_EXPECTED_PYTHON_EXE' = $normalizedPython", source)
        self.assertIn("$bridgeBackendPythonPaths", source)
        self.assertIn("services\\hia_mcp_v2", source)
        self.assertIn("$bootstrap.PSObject.Properties['token']", source)
        self.assertIn("$bootstrap.PSObject.Properties['url']", source)
        self.assertNotIn("$bootstrap.url", source)
        self.assertNotIn("[string]$bootstrap.token", source)
        self.assertNotIn("[string]$bootstrap.scene.executor_token", source)
        self.assertIn("HIA_HOUDINI_PROCESS_NONCE", source)
        self.assertIn("HIA_HOUDINI_SCHEMA_DIGEST", source)
        self.assertEqual(1, source.count("/v1/shutdown"))
        self.assertIn('-Uri "$bridgeUrl/v1/shutdown"', source)
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
        force_wait = source.index("$bridgeProcess.WaitForExit(7000)")
        ownership_check = source.rindex(
            "$ownedBridge = Get-ExactOwnedBridgeProcess"
        )
        taskkill_call = source.rindex("& $taskkillExe @taskkillArguments")
        self.assertLess(force_wait, ownership_check)
        self.assertLess(ownership_check, taskkill_call)
        self.assertIn('-Filter "ProcessId = $ProcessId"', source)
        self.assertIn("-LauncherProcessId $launcherProcessId", source)
        self.assertIn("-BridgeExecutablePath $normalizedPython", source)
        self.assertIn(
            "$taskkillArguments = @('/PID', [string]$bridgePid, '/T', '/F')",
            source,
        )
        self.assertIn("$bridgeProcess.HasExited", source)
        self.assertNotIn("$bridgeProcess.Kill()", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("$_.Exception.Message", source)
        self.assertIsNone(re.search(r"(?m)^\s*'USERPROFILE'\s*=", source))
        self.assertIsNone(re.search(r"(?m)^\s*'HOME'\s*=", source))

    def test_launcher_derives_sibling_hython_and_exposes_it_to_bridge(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        source = launcher.read_text(encoding="utf-8")

        self.assertIn(
            "$houdiniBinDirectory = [System.IO.Path]::GetDirectoryName($HoudiniExe)",
            source,
        )
        self.assertIn(
            "$HythonExe = Join-Path $houdiniBinDirectory 'hython.exe'",
            source,
        )
        self.assertIn(
            "Test-Path -LiteralPath $HythonExe -PathType Leaf",
            source,
        )
        self.assertIn(
            "Selected Houdini installation is missing sibling hython.exe",
            source,
        )
        self.assertIn(
            "'PATH' = \"$pythonDirectory;$houdiniBinDirectory;$($env:PATH)\"",
            source,
        )

    def test_launcher_bridge_tree_ownership_guard_is_fail_closed(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        escaped_launcher = str(launcher).replace("'", "''")
        ownership_command = f"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '{escaped_launcher}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {{ throw 'Generic launcher did not parse' }}
$functions = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-ExactOwnedBridgeProcess'
}}, $true))
if ($functions.Count -ne 1) {{ throw 'Ownership guard was not uniquely defined' }}
Invoke-Expression $functions[0].Extent.Text

$script:Candidates = @()
function Get-CimInstance {{
    param($ClassName, $Filter, $ErrorAction)
    if ($ClassName -ne 'Win32_Process' -or $Filter -ne 'ProcessId = 43210') {{
        throw 'Ownership guard queried outside the exact PID'
    }}
    return @($script:Candidates)
}}

$expectedPath = 'D:\Python_3.10\python.exe'
$script:Candidates = @([pscustomobject]@{{
    ProcessId = 43210
    ParentProcessId = 12345
    ExecutablePath = $expectedPath
}})
$match = Get-ExactOwnedBridgeProcess `
    -ProcessId 43210 `
    -LauncherProcessId 12345 `
    -BridgeExecutablePath $expectedPath
if ($null -eq $match) {{ throw 'Exact owned Bridge was rejected' }}

$script:Candidates[0].ParentProcessId = 99999
if ($null -ne (Get-ExactOwnedBridgeProcess `
    -ProcessId 43210 `
    -LauncherProcessId 12345 `
    -BridgeExecutablePath $expectedPath)) {{
    throw 'Wrong-parent process was accepted'
}}
$script:Candidates[0].ParentProcessId = 12345
$script:Candidates[0].ExecutablePath = 'D:\Python_3.10\other.exe'
if ($null -ne (Get-ExactOwnedBridgeProcess `
    -ProcessId 43210 `
    -LauncherProcessId 12345 `
    -BridgeExecutablePath $expectedPath)) {{
    throw 'Wrong-executable process was accepted'
}}
$script:Candidates = @()
if ($null -ne (Get-ExactOwnedBridgeProcess `
    -ProcessId 43210 `
    -LauncherProcessId 12345 `
    -BridgeExecutablePath $expectedPath)) {{
    throw 'Missing process was accepted'
}}
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ownership_command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )

    def test_launcher_resolves_simulated_h21_and_h22_without_real_install(self) -> None:
        launcher = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        escaped_launcher = str(launcher).replace("'", "''")
        metadata_command = f"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '{escaped_launcher}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {{ throw 'Generic launcher did not parse' }}
$functionNames = @(
    'Get-HoudiniCandidatePaths',
    'Test-HoudiniExecutableMetadata',
    'Resolve-HoudiniExecutable'
)
foreach ($functionName in $functionNames) {{
    $functions = @($ast.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $functionName
    }}, $true))
    if ($functions.Count -ne 1) {{
        throw "Launcher function was not uniquely defined: $functionName"
    }}
    Invoke-Expression $functions[0].Extent.Text
}}

$script:SimulatedVersionInfo = $null
function Test-Path {{
    param([string]$LiteralPath, [string]$PathType)
    return $true
}}
function Resolve-Path {{
    param([string]$LiteralPath)
    return [pscustomobject]@{{
        Path = [System.IO.Path]::GetFullPath($LiteralPath)
    }}
}}
function Get-Item {{
    param([string]$LiteralPath, [switch]$Force)
    return [pscustomobject]@{{
        Name = [System.IO.Path]::GetFileName($LiteralPath)
        Attributes = [System.IO.FileAttributes]::Archive
        Directory = $null
        VersionInfo = $script:SimulatedVersionInfo
    }}
}}

$cases = @(
    [pscustomobject]@{{
        Label = 'H21 official company-only metadata'
        ProductName = ''
        FileDescription = ''
        CompanyName = 'Side Effects Software Inc.'
        ProductVersion = ''
        FileVersion = '21, 0, 0, 440'
        ExpectedPass = $true
        ExpectedBuild = '21, 0, 0, 440'
    }},
    [pscustomobject]@{{
        Label = 'H22 official company-only metadata'
        ProductName = ' '
        FileDescription = "`t"
        CompanyName = 'Side Effects Software Inc.'
        ProductVersion = '22, 0, 0, 100'
        FileVersion = ''
        ExpectedPass = $true
        ExpectedBuild = '22, 0, 0, 100'
    }},
    [pscustomobject]@{{
        Label = 'wrong company'
        ProductName = 'Houdini 22.0'
        FileDescription = 'Houdini'
        CompanyName = 'Example Software Inc.'
        ProductVersion = '22, 0, 0, 100'
        FileVersion = ''
        ExpectedPass = $false
        ExpectedBuild = $null
    }},
    [pscustomobject]@{{
        Label = 'description compatibility'
        ProductName = 'SideFX Application'
        FileDescription = 'Houdini'
        CompanyName = 'Side Effects Software Inc.'
        ProductVersion = '22.0.100'
        FileVersion = ''
        ExpectedPass = $true
        ExpectedBuild = '22.0.100'
    }},
    [pscustomobject]@{{
        Label = 'conflicting identity'
        ProductName = 'SideFX Application'
        FileDescription = '3D Software'
        CompanyName = 'Side Effects Software Inc.'
        ProductVersion = '22.0.100'
        FileVersion = ''
        ExpectedPass = $false
        ExpectedBuild = $null
    }},
    [pscustomobject]@{{
        Label = 'missing build'
        ProductName = ''
        FileDescription = ''
        CompanyName = 'Side Effects Software Inc.'
        ProductVersion = ''
        FileVersion = ''
        ExpectedPass = $false
        ExpectedBuild = $null
    }}
)
foreach ($case in $cases) {{
    $script:SimulatedVersionInfo = [pscustomobject]@{{
        ProductName = $case.ProductName
        FileDescription = $case.FileDescription
        CompanyName = $case.CompanyName
        ProductVersion = $case.ProductVersion
        FileVersion = $case.FileVersion
    }}
    $resolved = $null
    $passed = $true
    try {{
        $resolved = Resolve-HoudiniExecutable `
            -RequestedPath 'E:\simulated\houdini.exe'
    }} catch {{
        $passed = $false
    }}
    if ($passed -ne [bool]$case.ExpectedPass) {{
        throw "Unexpected resolver result for $($case.Label): $passed"
    }}
    if ($passed -and $resolved.Build -ne $case.ExpectedBuild) {{
        throw "Unexpected build for $($case.Label): $($resolved.Build)"
    }}
}}
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", metadata_command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_legacy_launcher_is_a_three_parameter_forwarding_wrapper(self) -> None:
        generic = REPOSITORY_ROOT / "scripts" / "launch-houdini.ps1"
        legacy = REPOSITORY_ROOT / "scripts" / "launch-houdini-21.0.440.ps1"
        self.assertTrue(generic.is_file())
        self.assertTrue(legacy.is_file())
        source = legacy.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 14)
        self.assertIn("[string]$BridgePython =", source)
        self.assertIn("[string]$HoudiniExe = ''", source)
        self.assertIn("[string]$McpBackend = 'hia_v2'", source)
        self.assertEqual(1, source.count("launch-houdini.ps1"))
        self.assertEqual(1, source.count("-BridgePython $BridgePython"))
        self.assertEqual(1, source.count("-HoudiniExe $HoudiniExe"))
        self.assertEqual(1, source.count("-McpBackend $McpBackend"))
        for duplicated_logic in (
            "function Resolve-HoudiniExecutable",
            "Get-Command -Name 'houdini.exe'",
            "ProcessStartInfo",
            "HIA_BRIDGE_URL",
            "/v1/shutdown",
        ):
            self.assertNotIn(duplicated_logic, source)

        for launcher in (generic, legacy):
            escaped_launcher = str(launcher).replace("'", "''")
            parse_command = (
                "[void][scriptblock]::Create([IO.File]::ReadAllText("
                f"'{escaped_launcher}'))"
            )
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", parse_command],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_b2_runtime_wiring_is_read_only_and_live_start_remains_manual(self) -> None:
        bridge_main = (
            REPOSITORY_ROOT / "services" / "bridge" / "hia_bridge" / "main.py"
        ).read_text(encoding="utf-8")
        mcp_stdio = (
            REPOSITORY_ROOT
            / "services"
            / "houdini_mcp"
            / "hia_houdini_mcp"
            / "stdio.py"
        ).read_text(encoding="utf-8")
        panel_source = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_panel"
            / "panel.py"
        ).read_text(encoding="utf-8")
        ast.parse(bridge_main)
        ast.parse(mcp_stdio)
        ast.parse(panel_source)

        self.assertIn("SchemaRegistry.b2_read_only", bridge_main)
        self.assertIn("B2_READ_ONLY_PROFILE", bridge_main)
        self.assertNotIn('"token": token', bridge_main)
        self.assertNotIn('"executor_token": scene_executor_token', bridge_main)
        self.assertIn('client.set_environment_overlay(', bridge_main)
        self.assertIn('"--strict-config"', bridge_main)
        self.assertIn('HIA_MCP_V2_SERVER_ID = "hia_mcp_v2"', bridge_main)
        self.assertIn(
            'FXHOUDINI_MCP_SERVER_ID = "houdini_intelligence"',
            bridge_main,
        )
        self.assertIn(
            'f"mcp_servers.{FXHOUDINI_MCP_SERVER_ID}.enabled=false"',
            bridge_main,
        )
        self.assertIn('f"{server}.required=true"', bridge_main)
        self.assertNotIn("import hou", bridge_main)
        self.assertNotIn("from hou", bridge_main)
        self.assertIn("HoudiniMCPAdapter.b2_read_only", mcp_stdio)
        self.assertNotIn("B2A_REAL_MCP_START_DISABLED", mcp_stdio)
        self.assertIn("LoopbackBridgeTransport.from_environment(", mcp_stdio)
        panel_tree = ast.parse(panel_source)
        build_ui = next(
            node
            for node in ast.walk(panel_tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_build_ui"
        )
        build_ui_source = ast.get_source_segment(panel_source, build_ui) or ""
        for obsolete_ui in (
            "Houdini 只读状态",
            "Catalog",
            "Schema",
            "可用类型",
            "5/5",
        ):
            self.assertNotIn(obsolete_ui, build_ui_source)
        for forbidden_button in (
            'QPushButton("Apply")',
            'QPushButton("应用")',
            'QPushButton("创建节点")',
            'QPushButton("执行图")',
        ):
            self.assertNotIn(forbidden_button, panel_source)

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
