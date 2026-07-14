from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
BRIDGE_CLIENT_PATH = PANEL_LIB_ROOT / "hia_panel" / "bridge_client.py"


class _BoundSignal:
    def __init__(self) -> None:
        self.callbacks: list[Any] = []
        self.connect_count = 0
        self.emissions: list[tuple[Any, ...]] = []

    def connect(self, callback: Any) -> None:
        self.connect_count += 1
        self.callbacks.append(callback)

    def emit(self, *arguments: Any) -> None:
        self.emissions.append(arguments)
        for callback in tuple(self.callbacks):
            callback(*arguments)


class _SignalDescriptor:
    def __init__(self, *_types: object) -> None:
        self._name = ""

    def __set_name__(self, _owner: type, name: str) -> None:
        self._name = f"_test_signal_{name}"

    def __get__(self, instance: object, _owner: type | None = None) -> Any:
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = _BoundSignal()
            instance.__dict__[self._name] = signal
        return signal


class _QObject:
    def __init__(self, parent: object | None = None) -> None:
        self.parent = parent


class _QUrl:
    def __init__(self, value: str) -> None:
        self.value = value


class _Timer(_QObject):
    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.timeout = _BoundSignal()
        self.stopped = False
        self.started_with: int | None = None

    def setSingleShot(self, _single_shot: bool) -> None:
        pass

    def start(self, timeout_ms: int) -> None:
        self.started_with = timeout_ms

    def stop(self) -> None:
        self.stopped = True


class _NetworkRequest:
    HttpStatusCodeAttribute = "http_status"

    class Attribute:
        HttpStatusCodeAttribute = "http_status"

    def __init__(self, url: _QUrl) -> None:
        self.url = url
        self.headers: dict[bytes, bytes] = {}
        self.transfer_timeout: int | None = None

    def setRawHeader(self, name: bytes, value: bytes) -> None:
        self.headers[name] = value

    def setTransferTimeout(self, timeout_ms: int) -> None:
        self.transfer_timeout = timeout_ms


class _Reply:
    def __init__(self, body: bytes = b'{"ok":true}') -> None:
        self.finished = _BoundSignal()
        self._properties: dict[str, object] = {}
        self._body = body
        self.delete_later_count = 0
        self.abort_count = 0

    def setProperty(self, name: str, value: object) -> None:
        self._properties[name] = value

    def property(self, name: str) -> object | None:
        return self._properties.get(name)

    def abort(self) -> None:
        self.abort_count += 1

    def readAll(self) -> bytes:
        return self._body

    def error(self) -> int:
        return 0

    def errorString(self) -> str:
        return ""

    def attribute(self, name: str) -> int | None:
        if name == "http_status":
            return 200
        return None

    def deleteLater(self) -> None:
        self.delete_later_count += 1


class _NetworkAccessManager(_QObject):
    instances: list["_NetworkAccessManager"] = []

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.finished = _BoundSignal()
        self.replies: list[_Reply] = []
        self.__class__.instances.append(self)

    def get(self, _request: _NetworkRequest) -> _Reply:
        reply = _Reply()
        self.replies.append(reply)
        return reply

    def post(self, _request: _NetworkRequest, _body: bytes) -> _Reply:
        reply = _Reply()
        self.replies.append(reply)
        return reply


def _load_bridge_client() -> tuple[type, _NetworkAccessManager]:
    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_network = types.ModuleType("PySide6.QtNetwork")
    qt_core.QObject = _QObject
    qt_core.QUrl = _QUrl
    qt_core.QTimer = _Timer
    qt_core.Signal = _SignalDescriptor
    qt_network.QNetworkAccessManager = _NetworkAccessManager
    qt_network.QNetworkRequest = _NetworkRequest
    qt_network.QNetworkReply = _Reply
    pyside.QtCore = qt_core
    pyside.QtNetwork = qt_network

    sys.path.insert(0, str(PANEL_LIB_ROOT))
    replacements = {
        "PySide6": pyside,
        "PySide6.QtCore": qt_core,
        "PySide6.QtNetwork": qt_network,
    }
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in replacements}
    module_name = "hia_panel._headless_bridge_client_reply"
    saved_module = sys.modules.get(module_name, missing)
    try:
        sys.modules.update(replacements)
        spec = importlib.util.spec_from_file_location(module_name, BRIDGE_CLIENT_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load hia_panel.bridge_client")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        client = module.BridgeClient("http://127.0.0.1:49152", "secret")
        return module.BridgeClient, client
    finally:
        if saved_module is missing:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = saved_module
        for name, original in saved.items():
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class BridgeClientReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        _NetworkAccessManager.instances.clear()

    def test_manager_finished_is_connected_once(self) -> None:
        _client_type, client = _load_bridge_client()

        self.assertEqual(client._manager.finished.connect_count, 1)
        client.get_health()
        client.get_session()
        self.assertEqual(client._manager.finished.connect_count, 1)

    def test_same_reply_is_processed_and_deleted_exactly_once(self) -> None:
        _client_type, client = _load_bridge_client()
        client.get_health()
        reply = client._manager.replies[-1]

        client._manager.finished.emit(reply)
        client._manager.finished.emit(reply)

        self.assertEqual(reply.delete_later_count, 1)
        self.assertEqual(len(client.healthReceived.emissions), 1)
        self.assertEqual(client.healthReceived.emissions[0][0], {"ok": True})
        self.assertEqual(reply.finished.connect_count, 1)

    def test_source_has_no_per_reply_finished_lambda(self) -> None:
        source = BRIDGE_CLIENT_PATH.read_text(encoding="utf-8")

        self.assertNotIn("reply.finished.connect(lambda", source)
        self.assertNotIn("current=reply", source)
        self.assertIn("self._manager.finished.connect(self._finished)", source)
        self.assertNotIn("eventFilter", source)


if __name__ == "__main__":
    unittest.main()
