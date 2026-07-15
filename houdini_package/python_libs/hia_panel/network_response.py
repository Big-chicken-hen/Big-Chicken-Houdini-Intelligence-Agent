"""Pure response normalization for the asynchronous Bridge HTTP client."""

from __future__ import annotations

import json
import math
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
    error_kind: str | None = None,
    error_message: str = "",
    http_status: int | None,
    context: str,
    method: str,
    path: str,
    request_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """Decode one bounded Bridge response and retain safe transport context.

    Authentication material is deliberately absent from this API.  Callers
    must never pass request headers or a Bearer token to this function.
    """

    transport = _transport_details(
        error_kind=error_kind,
        error_message=error_message,
        http_status=http_status,
        context=context,
        method=method,
        path=path,
        request_id=request_id,
        generation=generation,
    )
    network_failed = error_kind in {
        "timeout",
        "url_error",
        "transport_error",
        "transport_closed",
    }
    response_too_large = error_kind == "response_too_large"
    http_failed = http_status is not None and http_status >= 400

    payload: dict[str, Any] | None = None
    invalid_code: str | None = None
    invalid_message: str | None = None
    if response_too_large:
        invalid_code = "INVALID_BRIDGE_RESPONSE"
        invalid_message = "Bridge response exceeded the size limit"
    elif not raw:
        if error_kind == "timeout":
            invalid_code = "NETWORK_TIMEOUT"
            invalid_message = "Bridge network request timed out"
        elif network_failed:
            invalid_code = "NETWORK_ERROR"
            safe_message = _transport_text(error_message)
            invalid_message = (
                "Bridge network request failed: " + safe_message
                if safe_message
                else "Bridge network request failed"
            )
        elif http_failed:
            invalid_code = "BRIDGE_HTTP_ERROR"
            invalid_message = f"Bridge returned HTTP {http_status}"
        else:
            invalid_code = "EMPTY_BRIDGE_RESPONSE"
            invalid_message = "Bridge returned an empty response"
    else:
        try:
            decoded = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            if isinstance(decoded, dict):
                payload = _sanitize_mapping(decoded)
            else:
                invalid_code = "INVALID_BRIDGE_RESPONSE"
                invalid_message = "Bridge response root must be a JSON object"
        except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
            invalid_code = "INVALID_BRIDGE_RESPONSE"
            invalid_message = "Bridge returned invalid JSON"

    if payload is not None:
        existing_error = payload.get("structured_error")
        if isinstance(existing_error, Mapping):
            normalized = dict(payload)
            normalized["ok"] = False
            structured_error = dict(existing_error)
            details = structured_error.get("details")
            if isinstance(details, Mapping):
                merged_details = dict(details)
            else:
                merged_details = {}
            merged_details["transport"] = transport
            structured_error["details"] = merged_details
            normalized["structured_error"] = structured_error
            return normalized
        if payload.get("ok") is True and not network_failed and not http_failed:
            return payload
        if error_kind == "timeout":
            invalid_code = "NETWORK_TIMEOUT"
            invalid_message = "Bridge network request timed out"
        elif network_failed:
            invalid_code = "NETWORK_ERROR"
            safe_message = _transport_text(error_message)
            invalid_message = (
                "Bridge network request failed: " + safe_message
                if safe_message
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
    error_kind = _display_text(transport.get("error_kind"))
    error_detail = _display_text(transport.get("error_message"))
    if error_kind:
        diagnostic = error_kind
        if error_detail:
            diagnostic += f": {error_detail}"
        diagnostics.append(diagnostic)
    suffix = f" ({', '.join(diagnostics)})" if diagnostics else ""
    return f"[{code}] {message}{suffix}"


def _transport_details(
    *,
    error_kind: str | None,
    error_message: str,
    http_status: int | None,
    context: str,
    method: str,
    path: str,
    request_id: str | None,
    generation: int | None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "context": _transport_text(context),
        "method": _transport_text(method),
        "path": _transport_text(path),
        "http_status": http_status,
        "error_kind": _transport_text(error_kind or ""),
        "error_message": _transport_text(error_message),
    }
    if request_id is not None:
        details["request_id"] = _transport_text(request_id)
    if generation is not None:
        details["generation"] = generation
    return details


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _transport_text(str(key)): _sanitize_value(item)
        for key, item in value.items()
    }


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _transport_text(value)
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


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
