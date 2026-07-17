from __future__ import annotations

import email.message
import io
import json
import queue
import socket
import threading
import time
import unittest
import urllib.error
from collections.abc import Iterable
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"

import sys

sys.path.insert(0, str(PANEL_LIB_ROOT))

from hia_panel.http_transport import HttpTransport  # noqa: E402


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_length: str | None = None,
    ) -> None:
        self.status = status
        self.headers: dict[str, str] = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_arguments: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def _submit(
    transport: HttpTransport,
    *,
    request_id: str,
    method: str = "GET",
    path: str = "/v1/health",
    context: str = "health",
    payload: dict[str, Any] | None = None,
    event_request: bool = False,
    timeout_ms: int = 1_000,
    deadline_monotonic: float | None = None,
    secret_headers: dict[str, str] | None = None,
    sensitive_values: Iterable[str] | None = None,
) -> str:
    return transport.submit(
        method=method,
        path=path,
        payload=payload,
        context=context,
        request_id=request_id,
        generation=7,
        timeout_ms=timeout_ms,
        deadline_monotonic=deadline_monotonic,
        event_request=event_request,
        secret_headers=secret_headers,
        sensitive_values=sensitive_values,
    )


class HttpTransportTests(unittest.TestCase):
    def test_executor_header_is_allowlisted_forwarded_and_always_redacted(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        observed: dict[str, Any] = {}
        executor_secret = "executor-secret-value"

        def urlopen(request: Any, *, timeout: float) -> _Response:
            del timeout
            observed["executor"] = request.get_header("X-hia-executor-token")
            return _Response(
                json.dumps(
                    {"ok": False, "echo": executor_secret},
                    separators=(",", ":"),
                ).encode("utf-8")
            )

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "bearer-secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(transport.close)
        _submit(
            transport,
            request_id="executor-header",
            secret_headers={"X-HIA-Executor-Token": executor_secret},
        )

        result = results.get(timeout=2.0)
        self.assertEqual(executor_secret, observed["executor"])
        self.assertNotIn(executor_secret.encode("utf-8"), result["raw"])
        self.assertIn(b"<redacted>", result["raw"])
        with self.assertRaisesRegex(ValueError, "allowlist"):
            _submit(
                transport,
                request_id="bad-secret-header",
                secret_headers={"X-Other-Secret": "not-allowed"},
            )

    def test_per_request_claim_token_is_redacted_without_becoming_a_header(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        observed_headers: dict[str, str] = {}
        claim = "opaque-one-request-claim"

        def urlopen(request: Any, *, timeout: float) -> _Response:
            del timeout
            observed_headers.update(dict(request.header_items()))
            return _Response(claim.encode("utf-8"))

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "bearer-secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(transport.close)
        _submit(
            transport,
            request_id="claim-redaction",
            sensitive_values=(claim,),
        )

        result = results.get(timeout=2.0)
        self.assertNotIn(claim, observed_headers.values())
        self.assertEqual(b"<redacted>", result["raw"])
        self.assertNotIn(claim, transport._secret_values)

    def test_success_uses_loopback_bearer_and_returns_plain_dict(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        observed: dict[str, Any] = {}

        def urlopen(request: Any, *, timeout: float) -> _Response:
            observed["url"] = request.full_url
            observed["authorization"] = request.get_header("Authorization")
            observed["timeout"] = timeout
            return _Response(b'{"ok":true}', status=200)

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "test-secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(transport.close)

        identifier = _submit(transport, request_id="request-success")
        result = results.get(timeout=2.0)

        self.assertEqual("request-success", identifier)
        self.assertEqual("http://127.0.0.1:49152/v1/health", observed["url"])
        self.assertEqual("Bearer test-secret", observed["authorization"])
        self.assertEqual(1.0, observed["timeout"])
        self.assertIs(type(result), dict)
        self.assertEqual(b'{"ok":true}', result["raw"])
        self.assertEqual(200, result["http_status"])
        self.assertIsNone(result["error_kind"])
        self.assertEqual("request-success", result["request_id"])
        self.assertEqual(7, result["generation"])
        self.assertTrue(all(thread.daemon for thread in transport.worker_threads))
        self.assertEqual(1, sum("-events" in thread.name for thread in transport.worker_threads))
        self.assertEqual(2, sum("-control-" in thread.name for thread in transport.worker_threads))

    def test_http_error_preserves_safe_structured_body_and_status(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        body = b'{"ok":false,"structured_error":{"code":"CONFLICT"}}'

        def urlopen(_request: Any, *, timeout: float) -> Any:
            del timeout
            headers = email.message.Message()
            headers["Content-Type"] = "application/json"
            raise urllib.error.HTTPError(
                "http://127.0.0.1:49152/v1/turn",
                409,
                "Conflict",
                headers,
                io.BytesIO(body),
            )

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(transport.close)
        _submit(
            transport,
            request_id="request-http-error",
            path="/v1/turn",
            context="turn_start",
        )

        result = results.get(timeout=2.0)
        self.assertEqual("http_error", result["error_kind"])
        self.assertEqual(409, result["http_status"])
        self.assertEqual(body, result["raw"])

    def test_url_error_and_timeout_are_distinct(self) -> None:
        cases = (
            (
                urllib.error.URLError("connection refused"),
                "url_error",
                "connection refused",
            ),
            (urllib.error.URLError(socket.timeout("late")), "timeout", "timed out"),
            (TimeoutError("late"), "timeout", "timed out"),
        )
        for index, (raised, expected_kind, expected_text) in enumerate(cases):
            with self.subTest(expected_kind=expected_kind, raised=type(raised).__name__):
                results: queue.Queue[dict[str, Any]] = queue.Queue()

                def urlopen(_request: Any, *, timeout: float) -> Any:
                    del timeout
                    raise raised

                transport = HttpTransport(
                    "http://127.0.0.1:49152",
                    "secret",
                    results,
                    urlopen=urlopen,
                    control_worker_count=1,
                )
                try:
                    _submit(transport, request_id=f"request-error-{index}")
                    result = results.get(timeout=2.0)
                finally:
                    transport.close()
                self.assertEqual(expected_kind, result["error_kind"])
                self.assertIn(expected_text, result["error_message"].lower())

    def test_empty_malicious_and_oversized_responses_are_bounded(self) -> None:
        cases = (
            (b"", 32, None, b""),
            (b"\xff\xfe<script>", 32, None, b"\xff\xfe<script>"),
            (b"x" * 33, 32, "response_too_large", b""),
        )
        for index, (body, limit, expected_error, expected_raw) in enumerate(cases):
            with self.subTest(index=index):
                results: queue.Queue[dict[str, Any]] = queue.Queue()

                def urlopen(_request: Any, *, timeout: float) -> _Response:
                    del timeout
                    return _Response(body)

                transport = HttpTransport(
                    "http://127.0.0.1:49152",
                    "secret",
                    results,
                    urlopen=urlopen,
                    max_response_bytes=limit,
                    control_worker_count=1,
                )
                try:
                    _submit(transport, request_id=f"request-body-{index}")
                    result = results.get(timeout=2.0)
                finally:
                    transport.close()
                self.assertEqual(expected_error, result["error_kind"])
                self.assertEqual(expected_raw, result["raw"])

    def test_announced_oversized_response_is_rejected_before_read(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()

        class NeverReadResponse(_Response):
            def read(self, size: int = -1) -> bytes:
                raise AssertionError(f"read unexpectedly called with {size}")

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=lambda _request, timeout: NeverReadResponse(
                b"ignored",
                content_length="33",
            ),
            max_response_bytes=32,
            control_worker_count=1,
        )
        self.addCleanup(transport.close)
        _submit(transport, request_id="request-announced-large")

        result = results.get(timeout=2.0)
        self.assertEqual("response_too_large", result["error_kind"])
        self.assertEqual(b"", result["raw"])

    def test_event_long_poll_runs_alongside_two_control_workers(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        event_started = threading.Event()
        release_event = threading.Event()
        controls_started = threading.Event()
        release_controls = threading.Event()
        lock = threading.Lock()
        active_controls = 0
        maximum_controls = 0

        def urlopen(request: Any, *, timeout: float) -> _Response:
            nonlocal active_controls, maximum_controls
            del timeout
            if "/v1/events?" in request.full_url:
                event_started.set()
                release_event.wait(2.0)
                return _Response(b'{"ok":true,"events":[]}')
            with lock:
                active_controls += 1
                maximum_controls = max(maximum_controls, active_controls)
                if active_controls == 2:
                    controls_started.set()
            try:
                release_controls.wait(2.0)
                return _Response(b'{"ok":true}')
            finally:
                with lock:
                    active_controls -= 1

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(release_event.set)
        self.addCleanup(release_controls.set)
        self.addCleanup(transport.close)

        _submit(
            transport,
            request_id="event-1",
            path="/v1/events?after=0&timeout=15",
            context="events",
            event_request=True,
        )
        self.assertTrue(event_started.wait(1.0))
        _submit(transport, request_id="control-1")
        _submit(transport, request_id="control-2")
        self.assertTrue(controls_started.wait(1.0))
        self.assertEqual(2, maximum_controls)

        release_controls.set()
        completed = {results.get(timeout=2.0)["request_id"] for _ in range(2)}
        self.assertEqual({"control-1", "control-2"}, completed)
        self.assertFalse(release_event.is_set())

        release_event.set()
        self.assertEqual("event-1", results.get(timeout=2.0)["request_id"])

    def test_expired_queued_post_never_reaches_urlopen(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        both_workers_started = threading.Event()
        release_workers = threading.Event()
        lock = threading.Lock()
        opened: list[str] = []
        blocker_count = 0

        def urlopen(request: Any, *, timeout: float) -> _Response:
            nonlocal blocker_count
            del timeout
            label = str(json.loads(request.data.decode("utf-8"))["label"])
            with lock:
                opened.append(label)
                if label.startswith("blocker-"):
                    blocker_count += 1
                    if blocker_count == 2:
                        both_workers_started.set()
            if label.startswith("blocker-"):
                release_workers.wait(2.0)
            return _Response(b'{"ok":true}')

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(release_workers.set)
        self.addCleanup(transport.close)
        generous_deadline = time.monotonic() + 5.0
        for index in (1, 2):
            _submit(
                transport,
                request_id=f"blocker-{index}",
                method="POST",
                path="/v1/turn",
                context=f"blocker-{index}",
                payload={"label": f"blocker-{index}"},
                timeout_ms=5_000,
                deadline_monotonic=generous_deadline,
            )
        self.assertTrue(both_workers_started.wait(1.0))

        expired_deadline = time.monotonic() + 0.05
        _submit(
            transport,
            request_id="expired-post",
            method="POST",
            path="/v1/turn",
            context="expired-post",
            payload={"label": "expired-post"},
            timeout_ms=5_000,
            deadline_monotonic=expired_deadline,
        )
        threading.Event().wait(max(0.0, expired_deadline - time.monotonic()) + 0.03)
        release_workers.set()

        completed = {
            result["request_id"]: result
            for result in (results.get(timeout=2.0) for _ in range(3))
        }
        self.assertNotIn("expired-post", opened)
        self.assertEqual("timeout", completed["expired-post"]["error_kind"])

    def test_close_suppresses_queued_post_before_urlopen(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        both_workers_started = threading.Event()
        release_workers = threading.Event()
        lock = threading.Lock()
        opened: list[str] = []
        blocker_count = 0

        def urlopen(request: Any, *, timeout: float) -> _Response:
            nonlocal blocker_count
            del timeout
            label = str(json.loads(request.data.decode("utf-8"))["label"])
            with lock:
                opened.append(label)
                if label.startswith("blocker-"):
                    blocker_count += 1
                    if blocker_count == 2:
                        both_workers_started.set()
            if label.startswith("blocker-"):
                release_workers.wait(2.0)
            return _Response(b'{"ok":true}')

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=urlopen,
        )
        self.addCleanup(release_workers.set)
        self.addCleanup(transport.close)
        deadline = time.monotonic() + 5.0
        for index in (1, 2):
            _submit(
                transport,
                request_id=f"blocker-{index}",
                method="POST",
                path="/v1/turn",
                context=f"blocker-{index}",
                payload={"label": f"blocker-{index}"},
                timeout_ms=5_000,
                deadline_monotonic=deadline,
            )
        self.assertTrue(both_workers_started.wait(1.0))
        _submit(
            transport,
            request_id="queued-post",
            method="POST",
            path="/v1/turn",
            context="queued-post",
            payload={"label": "queued-post"},
            timeout_ms=5_000,
            deadline_monotonic=deadline,
        )

        transport.close(max_wait_seconds=0.0)
        release_workers.set()
        transport.close(max_wait_seconds=1.0)

        completed = {
            result["request_id"]: result
            for result in (results.get(timeout=2.0) for _ in range(3))
        }
        self.assertNotIn("queued-post", opened)
        self.assertEqual(
            "transport_closed",
            completed["queued-post"]["error_kind"],
        )

    def test_close_is_bounded_and_rejects_new_work(self) -> None:
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        entered = threading.Event()
        release = threading.Event()

        def urlopen(_request: Any, *, timeout: float) -> _Response:
            del timeout
            entered.set()
            release.wait(2.0)
            return _Response(b'{"ok":true}')

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            "secret",
            results,
            urlopen=urlopen,
            control_worker_count=1,
        )
        self.addCleanup(release.set)
        self.addCleanup(transport.close)
        _submit(transport, request_id="blocked")
        self.assertTrue(entered.wait(1.0))

        started = time.monotonic()
        transport.close(max_wait_seconds=0.05)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.30)
        self.assertTrue(all(thread.daemon for thread in transport.worker_threads))
        with self.assertRaisesRegex(RuntimeError, "closed"):
            _submit(transport, request_id="after-close")

    def test_token_is_redacted_from_error_and_untrusted_body(self) -> None:
        secret = "bearer-token-very-secret"
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        call_count = 0

        def urlopen(_request: Any, *, timeout: float) -> _Response:
            nonlocal call_count
            del timeout
            call_count += 1
            if call_count == 1:
                raise urllib.error.URLError(
                    f"Bearer {secret} rejected at /v1/health?token={secret}"
                )
            return _Response(
                json.dumps({"ok": False, "echo": secret}).encode("utf-8")
            )

        transport = HttpTransport(
            "http://127.0.0.1:49152",
            secret,
            results,
            urlopen=urlopen,
            control_worker_count=1,
        )
        self.addCleanup(transport.close)
        _submit(transport, request_id="redact-error")
        _submit(transport, request_id="redact-body")

        encoded_results = repr([results.get(timeout=2.0) for _ in range(2)])
        self.assertNotIn(secret, encoded_results)
        self.assertIn("<redacted>", encoded_results)

    def test_only_authenticated_ipv4_loopback_base_url_is_accepted(self) -> None:
        invalid_urls = (
            "http://localhost:49152",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:70000",
            "https://127.0.0.1:49152",
            "http://127.0.0.1:49152/other",
            "http://user@127.0.0.1:49152",
        )
        for value in invalid_urls:
            with self.subTest(value=value), self.assertRaises(ValueError):
                HttpTransport(value, "secret", queue.Queue())

    def test_control_worker_count_cannot_exceed_two(self) -> None:
        for worker_count in (0, 3):
            with self.subTest(worker_count=worker_count), self.assertRaises(ValueError):
                HttpTransport(
                    "http://127.0.0.1:49152",
                    "secret",
                    queue.Queue(),
                    control_worker_count=worker_count,
                )


if __name__ == "__main__":
    unittest.main()
