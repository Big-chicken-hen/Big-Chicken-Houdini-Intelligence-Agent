"""Bounded standard-library HTTP workers for the Houdini Panel.

This module intentionally has no Qt dependency.  Worker threads communicate
with the UI-side client exclusively through a queue of plain Python mappings.
"""

from __future__ import annotations

import json
import math
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any


_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_CONTROL_WORKER_LIMIT = 2
_CONTROL_QUEUE_LIMIT = 64
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access_)?token|[?&]authorization)=([^&\s]*)"
)
_ALLOWED_SECRET_HEADERS = frozenset({"X-HIA-Executor-Token"})


class HttpTransport:
    """Execute authenticated loopback requests outside the Houdini UI thread."""

    def __init__(
        self,
        base_url: str,
        token: str,
        result_queue: queue.Queue[dict[str, Any]],
        *,
        control_worker_count: int = _CONTROL_WORKER_LIMIT,
        urlopen: Callable[..., Any] | None = None,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        if control_worker_count < 1 or control_worker_count > _CONTROL_WORKER_LIMIT:
            raise ValueError("control_worker_count must be between 1 and 2")
        if (
            not isinstance(token, str)
            or not token
            or "\r" in token
            or "\n" in token
        ):
            raise ValueError("Bridge token must be a non-empty string")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")

        self._base_url = _validate_loopback_base_url(base_url)
        self._token = token
        self._secret_lock = threading.Lock()
        self._secret_values: set[str] = {token}
        self._result_queue = result_queue
        # Redirects are disabled so an authenticated loopback request can never
        # carry its Authorization header to a second origin.
        self._urlopen = urlopen or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        ).open
        self._max_response_bytes = int(max_response_bytes)
        self._control_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=_CONTROL_QUEUE_LIMIT
        )
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._state_lock = threading.Lock()
        self._accepting = True
        self._threads: list[threading.Thread] = []

        event_thread = threading.Thread(
            target=self._worker,
            args=(self._event_queue,),
            name="HIA-Bridge-events",
            daemon=True,
        )
        self._threads.append(event_thread)
        for index in range(control_worker_count):
            self._threads.append(
                threading.Thread(
                    target=self._worker,
                    args=(self._control_queue,),
                    name=f"HIA-Bridge-control-{index + 1}",
                    daemon=True,
                )
            )
        for thread in self._threads:
            thread.start()

    @property
    def worker_threads(self) -> tuple[threading.Thread, ...]:
        """Return a read-only snapshot for bounded-shutdown diagnostics/tests."""

        return tuple(self._threads)

    def submit(
        self,
        *,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        context: str,
        request_id: str | None = None,
        generation: int = 0,
        timeout_ms: int,
        deadline_monotonic: float | None = None,
        event_request: bool = False,
        secret_headers: Mapping[str, str] | None = None,
        sensitive_values: Iterable[str] | None = None,
    ) -> str:
        """Queue one request and return its opaque correlation identifier."""

        normalized_method = method.upper()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError("Only GET and POST are supported")
        safe_path = _validate_relative_api_path(path)
        if timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")
        identifier = request_id or uuid.uuid4().hex
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        deadline = (
            time.monotonic() + (timeout_ms / 1000.0)
            if deadline_monotonic is None
            else float(deadline_monotonic)
        )
        if not math.isfinite(deadline):
            raise ValueError("deadline_monotonic must be finite")
        normalized_secret_headers: dict[str, str] = {}
        for name, value in (secret_headers or {}).items():
            if name not in _ALLOWED_SECRET_HEADERS:
                raise ValueError("Secret header is outside the fixed allowlist")
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 512
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("Secret header value is invalid")
            normalized_secret_headers[name] = value
        normalized_sensitive_values = tuple(sensitive_values or ())
        if len(normalized_sensitive_values) > 4:
            raise ValueError("Too many per-request sensitive values")
        for value in normalized_sensitive_values:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 512
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("Per-request sensitive value is invalid")

        record: dict[str, Any] = {
            "request_id": identifier,
            "generation": generation,
            "context": str(context),
            "method": normalized_method,
            "path": safe_path,
            "payload": dict(payload) if payload is not None else None,
            "timeout_ms": int(timeout_ms),
            "deadline_monotonic": deadline,
            "secret_headers": normalized_secret_headers,
            "sensitive_values": normalized_sensitive_values,
        }
        with self._secret_lock:
            if len(self._secret_values | set(normalized_secret_headers.values())) > 8:
                raise ValueError("Too many transport-level secret values")
            self._secret_values.update(normalized_secret_headers.values())
        with self._state_lock:
            if not self._accepting:
                raise RuntimeError("HTTP transport is closed")
            target = self._event_queue if event_request else self._control_queue
            target.put_nowait(record)
        return identifier

    def close(self, max_wait_seconds: float = 0.0) -> None:
        """Stop accepting work and wait only up to the caller's explicit bound.

        Work that has crossed the preflight boundary may finish, while queued
        work is rejected before any HTTP side effect.  Any request still
        blocked in the operating-system HTTP stack remains confined to a
        daemon thread and cannot make disposal exceed ``max_wait_seconds``.
        """

        wait_bound = max(0.0, float(max_wait_seconds))
        with self._state_lock:
            if self._accepting:
                self._accepting = False
        deadline = time.monotonic() + wait_bound
        for thread in self._threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

    def _worker(self, request_queue: queue.Queue[dict[str, Any]]) -> None:
        while True:
            try:
                record = request_queue.get(timeout=0.1)
            except queue.Empty:
                with self._state_lock:
                    if not self._accepting:
                        return
                continue
            try:
                result = self._preflight(record)
                if result is None:
                    result = self._execute(record)
                try:
                    self._result_queue.put_nowait(result)
                except queue.Full:
                    # The UI-side hard deadline remains authoritative.  A
                    # saturated consumer must never pin a daemon worker.
                    pass
            finally:
                request_queue.task_done()

    def _preflight(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Atomically claim a queued request before any HTTP side effect."""

        now = time.monotonic()
        with self._state_lock:
            accepting = self._accepting
            if not accepting:
                return self._terminal_result(
                    record,
                    error_kind="transport_closed",
                    error_message="Bridge transport closed before request started",
                )
            if now >= float(record["deadline_monotonic"]):
                return self._terminal_result(
                    record,
                    error_kind="timeout",
                    error_message="Bridge request timed out before it started",
                )
            # Releasing the lock after these checks is the execution boundary:
            # close() can suppress queued work, but cannot revoke an HTTP
            # request that has already crossed this boundary.
            record["started_monotonic"] = now
        return None

    def _execute(self, record: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        timeout_ms = int(record["timeout_ms"])
        deadline = float(record["deadline_monotonic"])
        timeout_seconds = min(
            timeout_ms / 1000.0,
            max(0.001, deadline - started),
        )
        raw = b""
        http_status: int | None = None
        error_kind: str | None = None
        error_message = ""
        sensitive_values = tuple(record.get("sensitive_values") or ())

        try:
            body = None
            headers = {
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "Cache-Control": "no-store",
            }
            headers.update(record.get("secret_headers") or {})
            if record["method"] == "POST":
                headers["Content-Type"] = "application/json"
                body = json.dumps(
                    record.get("payload") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            request = urllib.request.Request(
                self._base_url + record["path"],
                data=body,
                headers=headers,
                method=record["method"],
            )
            with self._urlopen(request, timeout=timeout_seconds) as response:
                http_status = _integer_or_none(getattr(response, "status", None))
                raw = self._read_bounded(response)
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError("Bridge request exceeded its deadline")
        except urllib.error.HTTPError as exc:
            http_status = _integer_or_none(getattr(exc, "code", None))
            error_kind = "http_error"
            try:
                raw = self._read_bounded(exc)
            except ResponseTooLargeError as size_exc:
                raw = b""
                error_kind = "response_too_large"
                error_message = str(size_exc)
            finally:
                try:
                    exc.close()
                except Exception:
                    pass
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                error_kind = "timeout"
                error_message = "Bridge request timed out"
            else:
                error_kind = "url_error"
                error_message = self._safe_error_text(reason, sensitive_values)
        except (TimeoutError, socket.timeout):
            error_kind = "timeout"
            error_message = "Bridge request timed out"
        except ResponseTooLargeError as exc:
            error_kind = "response_too_large"
            error_message = str(exc)
        except Exception as exc:  # Worker boundary: return data, never a traceback.
            error_kind = "transport_error"
            error_message = self._safe_error_text(exc, sensitive_values)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if (
            (elapsed_ms > timeout_ms or time.monotonic() >= deadline)
            and error_kind != "response_too_large"
        ):
            raw = b""
            error_kind = "timeout"
            error_message = "Bridge request timed out"
        raw = self._redact_body(raw, sensitive_values)
        return {
            "request_id": record["request_id"],
            "generation": record["generation"],
            "context": self._safe_error_text(record["context"], sensitive_values),
            "method": record["method"],
            "path": self._safe_error_text(record["path"], sensitive_values),
            "raw": raw,
            "http_status": http_status,
            "error_kind": error_kind,
            "error_message": self._safe_error_text(error_message, sensitive_values),
            "elapsed_ms": elapsed_ms,
        }

    def _terminal_result(
        self,
        record: dict[str, Any],
        *,
        error_kind: str,
        error_message: str,
    ) -> dict[str, Any]:
        sensitive_values = tuple(record.get("sensitive_values") or ())
        return {
            "request_id": record["request_id"],
            "generation": record["generation"],
            "context": self._safe_error_text(record["context"], sensitive_values),
            "method": record["method"],
            "path": self._safe_error_text(record["path"], sensitive_values),
            "raw": b"",
            "http_status": None,
            "error_kind": error_kind,
            "error_message": self._safe_error_text(error_message, sensitive_values),
            "elapsed_ms": 0,
        }

    def _read_bounded(self, response: Any) -> bytes:
        headers = getattr(response, "headers", None)
        content_length = headers.get("Content-Length") if headers is not None else None
        if content_length is not None:
            try:
                announced = int(content_length)
            except (TypeError, ValueError):
                announced = -1
            if announced > self._max_response_bytes:
                raise ResponseTooLargeError("Bridge response exceeded the size limit")
        raw = response.read(self._max_response_bytes + 1)
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        if len(raw) > self._max_response_bytes:
            raise ResponseTooLargeError("Bridge response exceeded the size limit")
        return raw

    def _redact_body(
        self,
        raw: bytes,
        sensitive_values: tuple[str, ...] = (),
    ) -> bytes:
        with self._secret_lock:
            secrets = tuple(self._secret_values) + sensitive_values
        for secret in secrets:
            secret_bytes = secret.encode("utf-8")
            if secret_bytes:
                raw = raw.replace(secret_bytes, b"<redacted>")
        return raw

    def _safe_error_text(
        self,
        value: object,
        sensitive_values: tuple[str, ...] = (),
    ) -> str:
        text = str(value)
        with self._secret_lock:
            secrets = tuple(self._secret_values) + sensitive_values
        for secret in secrets:
            text = text.replace(secret, "<redacted>")
        text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
        text = _QUERY_SECRET_PATTERN.sub(r"\1=<redacted>", text)
        return " ".join(text.splitlines()).strip()[:1000]


class ResponseTooLargeError(ValueError):
    """Raised when an untrusted Bridge response exceeds the fixed bound."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Convert redirects to HTTP errors instead of leaving loopback."""

    def redirect_request(
        self,
        request: Any,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _validate_loopback_base_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Bridge base URL is required")
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Bridge base URL must be http://127.0.0.1:<port>")
    return f"http://127.0.0.1:{port}"


def _validate_relative_api_path(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("Bridge API path must be absolute-path relative")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/v1/"):
        raise ValueError("Bridge API path must remain under /v1/")
    return value


def _integer_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
