"""Small standard-library JSON-RPC/MCP adapter for HIA MCP V2."""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from typing import Any, Mapping

from .errors import HiaMcpError, InputError, TransportError
from .tools import CAPABILITY_MATRIX, TOOL_BY_NAME, TOOL_NAMES, descriptors, validate_input
from .transport import CancellationToken, HoudiniTransport, SERVER_ID


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.1.0"
_REQUEST_METHODS = {"initialize", "ping", "tools/list", "tools/call"}
_NOTIFICATIONS = {"notifications/initialized", "notifications/cancelled"}


class HiaMcpAdapter:
    def __init__(self, transport: HoudiniTransport) -> None:
        self._transport = transport
        self._lock = threading.RLock()
        self._initialize_seen = False
        self._initialized = False
        self._closed = False
        self._active: dict[int | str, CancellationToken] = {}
        self._cancelled: OrderedDict[int | str, None] = OrderedDict()
        self._descriptors = descriptors()

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(TOOL_NAMES)

    def handle_message(
        self,
        message: Mapping[str, Any],
        *,
        stdio_queue_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        if not isinstance(message, Mapping):
            return self._rpc_error(None, -32600, "Invalid Request", "INVALID_REQUEST")
        has_id = "id" in message
        request_id = message.get("id") if has_id else None
        if not self._valid_envelope(message, has_id=has_id):
            return self._rpc_error(request_id if self._valid_id(request_id) else None, -32600, "Invalid Request", "INVALID_REQUEST") if has_id else None
        method = message["method"]
        params = message.get("params")
        if not has_id:
            self._notification(method, params)
            return None
        if method not in _REQUEST_METHODS:
            return self._rpc_error(request_id, -32601, "Method not found", "METHOD_NOT_FOUND")
        try:
            result = self._request(
                request_id,
                method,
                params,
                stdio_queue_seconds=stdio_queue_seconds,
            )
        except InputError as exc:
            return self._rpc_error(request_id, -32602, exc.message, exc.code, exc.details)
        except HiaMcpError as exc:
            return self._rpc_error(request_id, -32000, exc.message, exc.code, exc.details)
        except Exception:
            return self._rpc_error(request_id, -32603, "Internal error", "INTERNAL_ERROR")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active.items())
        for request_id, token in active:
            token.cancel()
            try:
                self._transport.cancel(request_id)
            except Exception:
                pass
        try:
            self._transport.close()
        except Exception:
            pass

    def _request(
        self,
        request_id: int | str,
        method: str,
        params: Any,
        *,
        stdio_queue_seconds: float = 0.0,
    ) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            if params not in (None, {}):
                raise InputError("INVALID_PARAMS", "ping does not accept parameters")
            return {}
        self._require_initialized()
        if method == "tools/list":
            if params not in (None, {}) and not (
                isinstance(params, Mapping)
                and set(params) <= {"cursor", "_meta"}
                and (
                    "cursor" not in params
                    or isinstance(params.get("cursor"), str)
                )
                and (
                    "_meta" not in params
                    or isinstance(params.get("_meta"), Mapping)
                )
            ):
                raise InputError("INVALID_PARAMS", "tools/list parameters are invalid")
            return {"tools": copy.deepcopy(self._descriptors)}
        return self._call_tool(
            request_id,
            params,
            stdio_queue_seconds=stdio_queue_seconds,
        )

    def _initialize(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise InputError("INVALID_PARAMS", "initialize parameters must be an object")
        protocol = params.get("protocolVersion")
        if protocol != MCP_PROTOCOL_VERSION:
            raise InputError(
                "UNSUPPORTED_PROTOCOL",
                "HIA MCP V2 requires MCP protocol 2025-06-18",
                {"supported": MCP_PROTOCOL_VERSION},
            )
        with self._lock:
            if self._closed:
                raise HiaMcpError("SERVER_CLOSED", "The HIA MCP V2 server is closed")
            if self._initialize_seen:
                raise InputError("ALREADY_INITIALIZED", "initialize may be called only once")
            self._initialize_seen = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_ID, "version": SERVER_VERSION},
            "instructions": (
                "HIA MCP V2 is Codex's live Houdini perception, knowledge, execution, and validation layer. "
                "Batch related read queries and reuse their results; discover installed node types dynamically, "
                "and prefer one hia_execute_hom batch for complex edits."
            ),
        }

    def _call_tool(
        self,
        request_id: int | str,
        params: Any,
        *,
        stdio_queue_seconds: float = 0.0,
    ) -> dict[str, Any]:
        if not isinstance(params, Mapping) or set(params) - {"name", "arguments", "_meta"}:
            raise InputError("INVALID_PARAMS", "tools/call parameters are invalid")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in TOOL_BY_NAME:
            raise InputError("TOOL_NOT_FOUND", "Unknown HIA MCP V2 tool")
        if not isinstance(arguments, Mapping):
            raise InputError("INVALID_ARGUMENTS", "Tool arguments must be an object")
        validate_input(name, arguments)
        if name == "hia_search_capabilities":
            payload = self._search_capabilities(arguments)
            return self._tool_result(payload)
        token = CancellationToken(stdio_queue_seconds=stdio_queue_seconds)
        with self._lock:
            if request_id in self._cancelled:
                self._cancelled.pop(request_id, None)
                return self._tool_error(
                    "CANCELLED_BEFORE_EXECUTION",
                    "The call was cancelled before Houdini execution began",
                    {
                        "stage": "stdio_queue",
                        "stdio_queue_seconds": token.stdio_queue_seconds,
                        "submission_state": "not_submitted",
                        "request_submitted": False,
                        "hom_may_still_execute": False,
                        "automatic_retry_safe": True,
                        "interruptible_after_submission": False,
                    },
                )
            if request_id in self._active:
                raise InputError("DUPLICATE_REQUEST_ID", "A tool call with this request id is already active")
            self._active[request_id] = token
        try:
            payload = self._transport.call(
                name,
                dict(arguments),
                request_id=request_id,
                cancellation=token,
            )
            if not isinstance(payload, Mapping):
                raise TransportError("INVALID_RESPONSE", "The transport result must be an object")
            return self._tool_result(dict(payload))
        except TransportError as exc:
            return self._tool_error(exc.code, exc.message, exc.details)
        finally:
            with self._lock:
                self._active.pop(request_id, None)

    def _search_capabilities(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).casefold()
        domain = str(arguments.get("domain", "")).casefold()
        offset = int(arguments.get("offset", 0))
        limit = int(arguments.get("limit", 50))
        matches = []
        for item in CAPABILITY_MATRIX:
            haystack = json.dumps(item, ensure_ascii=False).casefold()
            if domain and domain not in str(item["domain"]).casefold():
                continue
            if query and query not in haystack:
                continue
            matches.append(copy.deepcopy(item))
        page = matches[offset : offset + limit]
        return {
            "ok": True,
            "result": {"capabilities": page, "total": len(matches), "offset": offset, "limit": limit},
            "warnings": [],
        }

    def _tool_result(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        image = value.pop("image", None)
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            }
        ]
        if isinstance(image, Mapping):
            data = image.get("data_base64")
            mime_type = image.get("mime_type", "image/png")
            if isinstance(data, str) and data:
                content.append({"type": "image", "data": data, "mimeType": str(mime_type)})
        is_error = value.get("ok") is False
        result = {"content": content, "structuredContent": value, "isError": is_error}
        return result

    def _tool_error(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": False,
            "result": None,
            "warnings": [],
            "errors": [{"code": code, "message": message, "details": dict(details or {})}],
            "structured_error": {"code": code, "message": message, "details": dict(details or {})},
        }
        return self._tool_result(payload)

    def _notification(self, method: str, params: Any) -> None:
        if method == "notifications/initialized":
            with self._lock:
                if self._initialize_seen:
                    self._initialized = True
            return
        if method == "notifications/cancelled" and isinstance(params, Mapping):
            request_id = params.get("requestId")
            if not self._valid_id(request_id):
                return
            with self._lock:
                token = self._active.get(request_id)
                if token is None:
                    self._cancelled[request_id] = None
                    self._cancelled.move_to_end(request_id)
                    while len(self._cancelled) > 256:
                        self._cancelled.popitem(last=False)
                else:
                    token.cancel()
            try:
                self._transport.cancel(request_id)
            except Exception:
                pass

    def _require_initialized(self) -> None:
        with self._lock:
            if not self._initialize_seen:
                raise InputError("NOT_INITIALIZED", "initialize must be called first")
            # Some MCP clients send tools/list before the initialized notification.
            # Initialization response is sufficient; the notification remains accepted.

    @classmethod
    def _valid_envelope(cls, message: Mapping[str, Any], *, has_id: bool) -> bool:
        if set(message) - {"jsonrpc", "id", "method", "params"}:
            return False
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return False
        if has_id and not cls._valid_id(message.get("id")):
            return False
        if "params" in message and not isinstance(message.get("params"), Mapping):
            return False
        return True

    @staticmethod
    def _valid_id(value: Any) -> bool:
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, str) and bool(value))

    @staticmethod
    def _rpc_error(
        request_id: Any,
        rpc_code: int,
        message: str,
        stable_code: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": rpc_code,
                "message": message,
                "data": {"code": stable_code, "details": dict(details or {})},
            },
        }
