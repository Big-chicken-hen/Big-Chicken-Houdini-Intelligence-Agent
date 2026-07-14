"""Asynchronous QtNetwork client for the authenticated local Bridge."""

from __future__ import annotations

import json
from typing import Any

from PySide6 import QtCore, QtNetwork

from .network_response import normalize_bridge_response


_RECONCILIATION_TIMEOUT_MS = 5_000
_EVENT_POLL_TIMEOUT_MS = 20_000
_DEFAULT_REQUEST_TIMEOUT_MS = 15_000


class BridgeClient(QtCore.QObject):
    healthReceived = QtCore.Signal(dict)
    sessionReceived = QtCore.Signal(dict)
    eventsReceived = QtCore.Signal(dict)
    actionCompleted = QtCore.Signal(str, dict)
    requestFailed = QtCore.Signal(str, dict)

    def __init__(self, base_url: str, token: str, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._token = token.encode("utf-8")
        self._manager = QtNetwork.QNetworkAccessManager(self)
        self._manager.finished.connect(self._finished)
        self._event_request_active = False

    def get_health(self) -> None:
        self._request("GET", "/v1/health", context="health")

    def get_session(self, *, context: str = "session") -> None:
        """Read Bridge session state with a caller-supplied correlation context."""

        self._request("GET", "/v1/session", context=context)

    def get_models(self) -> None:
        """Read the Bridge-sanitized stable Codex model catalog."""

        self._request("GET", "/v1/models", context="models")

    def start_thread(self, *, model: str | None = None) -> None:
        payload: dict[str, Any] = {"action": "start"}
        if model is not None:
            payload["model"] = model
        self._request(
            "POST",
            "/v1/session",
            payload,
            context="session_start",
        )

    def resume_thread(self, thread_id: str) -> None:
        self._request(
            "POST",
            "/v1/session",
            {"action": "resume", "thread_id": thread_id},
            context="session_resume",
        )

    def start_turn(
        self,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        context: str = "turn_start",
    ) -> None:
        payload: dict[str, Any] = {"text": text}
        if model is not None:
            payload["model"] = model
        if effort is not None:
            payload["effort"] = effort
        self._request(
            "POST",
            "/v1/turn",
            payload,
            context=context,
        )

    def interrupt(self, *, context: str = "interrupt") -> None:
        self._request("POST", "/v1/interrupt", {}, context=context)

    def resolve_approval(self, request_id: Any, decision: str) -> None:
        self._request(
            "POST",
            "/v1/approval",
            {"request_id": request_id, "decision": decision},
            context=f"approval_{decision}",
        )

    def poll_events(self, after: int, timeout: int = 15) -> None:
        if self._event_request_active:
            return
        self._event_request_active = True
        path = f"/v1/events?after={int(after)}&timeout={int(timeout)}"
        self._request("GET", path, context="events")

    def shutdown(self) -> None:
        self._request("POST", "/v1/shutdown", {}, context="shutdown")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        context: str,
    ) -> None:
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(self._base_url + path))
        request.setRawHeader(b"Authorization", b"Bearer " + self._token)
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Cache-Control", b"no-store")
        if context.startswith("session_reconcile:"):
            transfer_timeout = _RECONCILIATION_TIMEOUT_MS
        elif context == "events":
            transfer_timeout = _EVENT_POLL_TIMEOUT_MS
        else:
            transfer_timeout = _DEFAULT_REQUEST_TIMEOUT_MS
        request.setTransferTimeout(transfer_timeout)
        if method == "GET":
            reply = self._manager.get(request)
        else:
            request.setRawHeader(b"Content-Type", b"application/json")
            encoded = json.dumps(
                payload or {},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            reply = self._manager.post(request, encoded)
        reply.setProperty("hia_context", context)
        reply.setProperty("hia_method", method)
        reply.setProperty("hia_path", path)
        deadline = QtCore.QTimer(reply)
        deadline.setSingleShot(True)
        deadline.timeout.connect(reply.abort)
        reply.finished.connect(deadline.stop)
        deadline.start(transfer_timeout)

    def _finished(self, reply: QtNetwork.QNetworkReply) -> None:
        if bool(reply.property("hia_finished_processed")):
            return
        reply.setProperty("hia_finished_processed", True)
        context = str(reply.property("hia_context"))
        method = str(reply.property("hia_method"))
        path = str(reply.property("hia_path"))
        if context == "events":
            self._event_request_active = False
        raw = bytes(reply.readAll())
        qt_error_code = self._integer_value(reply.error())
        status_attribute = getattr(
            QtNetwork.QNetworkRequest,
            "HttpStatusCodeAttribute",
            None,
        )
        if status_attribute is None:
            status_attribute = QtNetwork.QNetworkRequest.Attribute.HttpStatusCodeAttribute
        http_status = self._integer_value(reply.attribute(status_attribute))
        payload = normalize_bridge_response(
            raw,
            qt_error_code=qt_error_code,
            error_string=reply.errorString(),
            http_status=http_status,
            context=context,
            method=method,
            path=path,
        )
        if not payload.get("ok", False):
            self.requestFailed.emit(context, payload)
        elif context == "health":
            self.healthReceived.emit(payload)
        elif context == "session":
            self.sessionReceived.emit(payload)
        elif context == "events":
            self.eventsReceived.emit(payload)
        else:
            self.actionCompleted.emit(context, payload)
        reply.deleteLater()

    @staticmethod
    def _integer_value(value: Any) -> int | None:
        if value is None:
            return None
        raw_value = getattr(value, "value", value)
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None
