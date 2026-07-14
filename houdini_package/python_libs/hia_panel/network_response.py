"""Pure response normalization for the asynchronous Qt Bridge client."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any


_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access_)?token|[?&]authorization)=([^&\s]*)"
)


def normalize_bridge_response(
    raw: bytes,
    *,
    qt_error_code: int | None,
    error_string: str,
    http_status: int | None,
    context: str,
    method: str,
    path: str,
) -> dict[str, Any]:
    """Decode a Bridge response and retain safe Qt transport diagnostics.

    Authentication material is deliberately absent from the API.  Callers must
    never pass request headers or a Bearer token to this function.
    """

    transport = _transport_details(
        qt_error_code=qt_error_code,
        error_string=error_string,
        http_status=http_status,
        context=context,
        method=method,
        path=path,
    )
    network_failed = qt_error_code not in {None, 0}
    http_failed = http_status is not None and http_status >= 400

    payload: dict[str, Any] | None = None
    invalid_code: str | None = None
    invalid_message: str | None = None
    if not raw:
        invalid_code = "NETWORK_ERROR" if network_failed else "EMPTY_BRIDGE_RESPONSE"
        if network_failed and transport["qt_error_string"]:
            invalid_message = (
                "Bridge network request failed: " + transport["qt_error_string"]
            )
        elif network_failed:
            invalid_message = "Bridge network request failed"
        else:
            invalid_message = "Bridge returned an empty response"
    else:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            invalid_code = "INVALID_BRIDGE_RESPONSE"
            invalid_message = "Bridge returned invalid JSON"
        else:
            if isinstance(decoded, dict):
                payload = decoded
            else:
                invalid_code = "INVALID_BRIDGE_RESPONSE"
                invalid_message = "Bridge response root must be a JSON object"

    if payload is not None:
        existing_error = payload.get("structured_error")
        if isinstance(existing_error, Mapping):
            normalized = copy.deepcopy(payload)
            normalized["ok"] = False
            structured_error = dict(copy.deepcopy(existing_error))
            details = structured_error.get("details")
            if isinstance(details, Mapping):
                merged_details = dict(copy.deepcopy(details))
            else:
                merged_details = {}
            merged_details["transport"] = transport
            structured_error["details"] = merged_details
            normalized["structured_error"] = structured_error
            return normalized
        if payload.get("ok") is True and not network_failed and not http_failed:
            return payload
        if network_failed:
            invalid_code = "NETWORK_ERROR"
            invalid_message = (
                "Bridge network request failed: " + transport["qt_error_string"]
                if transport["qt_error_string"]
                else "Bridge network request failed"
            )
        elif http_failed:
            invalid_code = "BRIDGE_HTTP_ERROR"
            invalid_message = f"Bridge returned HTTP {http_status}"
        else:
            invalid_code = "BRIDGE_REQUEST_FAILED"
            invalid_message = "Bridge reported an unsuccessful request"

    return {
        "ok": False,
        "structured_error": {
            "code": invalid_code or "BRIDGE_REQUEST_FAILED",
            "message": invalid_message or "Bridge request failed",
            "details": {"transport": transport},
        },
    }


def format_bridge_error(
    payload: Mapping[str, Any],
    *,
    default_message: str = "Bridge request failed",
) -> str:
    """Render only the safe, useful subset of a structured Bridge error."""

    structured_error = payload.get("structured_error")
    if not isinstance(structured_error, Mapping):
        return _display_text(default_message)

    code = _display_text(structured_error.get("code") or "BRIDGE_REQUEST_FAILED")
    message = _display_text(structured_error.get("message") or default_message)
    details = structured_error.get("details")
    transport = details.get("transport") if isinstance(details, Mapping) else None
    if not isinstance(transport, Mapping):
        return f"[{code}] {message}"

    diagnostics: list[str] = []
    context = _display_text(transport.get("context"))
    if context:
        diagnostics.append(f"context={context}")
    method = _display_text(transport.get("method"))
    path = _display_text(transport.get("path"))
    if method or path:
        diagnostics.append(" ".join(part for part in (method, path) if part))
    http_status = transport.get("http_status")
    if http_status is not None:
        diagnostics.append(f"HTTP {http_status}")
    qt_error_code = transport.get("qt_error_code")
    qt_error_string = _display_text(transport.get("qt_error_string"))
    if qt_error_code is not None:
        qt_detail = f"Qt {qt_error_code}"
        if qt_error_string:
            qt_detail += f": {qt_error_string}"
        diagnostics.append(qt_detail)
    elif qt_error_string:
        diagnostics.append(f"Qt: {qt_error_string}")

    suffix = f" ({', '.join(diagnostics)})" if diagnostics else ""
    return f"[{code}] {message}{suffix}"


def _transport_details(
    *,
    qt_error_code: int | None,
    error_string: str,
    http_status: int | None,
    context: str,
    method: str,
    path: str,
) -> dict[str, Any]:
    return {
        "context": _transport_text(context),
        "method": _transport_text(method),
        "path": _transport_text(path),
        "qt_error_code": qt_error_code,
        "qt_error_string": _transport_text(error_string),
        "http_status": http_status,
    }


def _transport_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    redacted = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\1=<redacted>", redacted)
    return redacted


def _display_text(value: object) -> str:
    if value is None:
        return ""
    text = _transport_text(str(value))
    text = " ".join(text.splitlines()).strip()
    return text[:1000]
