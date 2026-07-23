from __future__ import annotations

import io
import json
import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "hia_mcp_v2"))

from hia_mcp_v2.adapter import HiaMcpAdapter, MCP_PROTOCOL_VERSION  # noqa: E402
from hia_mcp_v2.stdio import (  # noqa: E402
    MAX_CALL_WORKERS,
    MAX_PENDING_CALLS,
    run_bytes,
    run_stdio,
)


def rpc(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_message() -> dict[str, Any]:
    return rpc(
        1,
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "stdio-queue-test", "version": "1"},
        },
    )


def call_message(request_id: int) -> dict[str, Any]:
    return rpc(request_id, "tools/call", {"name": "hia_context", "arguments": {}})


def encode_transcript(messages: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        for message in messages
    )


class RecordingTransport:
    def __init__(self, *, delay: float = 0.0, gated: bool = False) -> None:
        self.delay = delay
        self.release = threading.Event()
        if not gated:
            self.release.set()
        self._condition = threading.Condition()
        self.calls: list[int | str] = []
        self.queue_seconds: dict[int | str, float] = {}
        self.cancelled: list[int | str] = []
        self.active = 0
        self.peak_active = 0
        self.closed = False

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: int | str,
        cancellation: Any,
    ) -> Mapping[str, Any]:
        with self._condition:
            self.calls.append(request_id)
            self.queue_seconds[request_id] = cancellation.stdio_queue_seconds
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self._condition.notify_all()
        try:
            if not self.release.wait(5):
                raise RuntimeError("test transport gate timed out")
            if self.delay:
                time.sleep(self.delay)
            return {
                "ok": True,
                "result": {"tool": tool_name, "request_id": request_id},
                "warnings": [],
                "errors": [],
            }
        finally:
            with self._condition:
                self.active -= 1
                self._condition.notify_all()

    def wait_for_active(self, count: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self.active < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def cancel(self, request_id: int | str) -> None:
        self.cancelled.append(request_id)

    def close(self) -> None:
        self.closed = True


class FeedInput:
    def __init__(self) -> None:
        self._lines: queue.Queue[bytes] = queue.Queue()

    def send(self, message: dict[str, Any]) -> None:
        self._lines.put(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

    def close(self) -> None:
        self._lines.put(b"")

    def readline(self, _limit: int = -1) -> bytes:
        return self._lines.get(timeout=5)


class SynchronizedOutput:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._buffer = bytearray()

    def write(self, value: bytes) -> int:
        with self._condition:
            self._buffer.extend(value)
            self._condition.notify_all()
        return len(value)

    def flush(self) -> None:
        pass

    def messages(self) -> list[dict[str, Any]]:
        with self._condition:
            snapshot = bytes(self._buffer)
        return [json.loads(line) for line in snapshot.splitlines()]

    def wait_for_id(self, request_id: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if any(
                    json.loads(line).get("id") == request_id
                    for line in bytes(self._buffer).splitlines()
                ):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)


class FailingOutput:
    def __init__(self) -> None:
        self.flush_count = 0

    def write(self, value: bytes) -> int:
        return len(value)

    def flush(self) -> None:
        self.flush_count += 1
        if self.flush_count > 1:
            raise OSError("simulated closed output")


class LiveStdioSession:
    def __init__(self, transport: RecordingTransport) -> None:
        self.transport = transport
        self.source = FeedInput()
        self.output = SynchronizedOutput()
        self.diagnostics = io.StringIO()
        self.status: int | None = None
        self.thread = threading.Thread(target=self._run, name="hia-stdio-test-session")
        self.thread.start()
        self.source.send(initialize_message())
        self.source.send(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )

    def _run(self) -> None:
        self.status = run_stdio(
            HiaMcpAdapter(self.transport),
            input_stream=self.source,
            output_stream=self.output,
            diagnostic_stream=self.diagnostics,
        )

    def finish(self) -> None:
        self.source.close()
        self.thread.join(5)
        if self.thread.is_alive():
            self.transport.release.set()
            self.thread.join(5)
        if self.thread.is_alive():
            raise AssertionError("stdio session did not stop")


class HiaMcpV2StdioQueueTests(unittest.TestCase):
    def test_output_failure_does_not_kill_workers_or_hang_queue_drain(self) -> None:
        transport = RecordingTransport(delay=0.01)
        transcript = [initialize_message(), *(call_message(value) for value in range(10, 18))]
        diagnostics = io.StringIO()
        status: list[int] = []

        runner = threading.Thread(
            target=lambda: status.append(
                run_stdio(
                    HiaMcpAdapter(transport),
                    input_stream=io.BytesIO(encode_transcript(transcript)),
                    output_stream=FailingOutput(),
                    diagnostic_stream=diagnostics,
                )
            ),
            name="hia-failing-output-test",
            daemon=True,
        )
        runner.start()
        runner.join(3)

        self.assertFalse(runner.is_alive(), "output failure left stdio queue drain hanging")
        self.assertEqual([0], status)
        self.assertEqual(8, len(transport.calls))
        self.assertTrue(transport.closed)
        self.assertEqual("hia_mcp_v2: OUTPUT_WRITE_FAILED\n", diagnostics.getvalue())
        self.assertFalse(
            any(thread.name.startswith("hia-mcp-v2-call-") for thread in threading.enumerate())
        )

    def test_eof_waits_past_the_old_quarter_second_join_without_losing_response(self) -> None:
        transport = RecordingTransport(delay=0.35)
        transcript = [initialize_message(), call_message(10)]

        started = time.monotonic()
        status, output, diagnostics = run_bytes(
            encode_transcript(transcript),
            HiaMcpAdapter(transport),
        )
        elapsed = time.monotonic() - started

        messages = [json.loads(line) for line in output.splitlines()]
        response = next(message for message in messages if message.get("id") == 10)
        self.assertEqual(0, status)
        self.assertEqual("", diagnostics)
        self.assertNotIn("error", response)
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertTrue(transport.closed)
        self.assertFalse(
            any(thread.name.startswith("hia-mcp-v2-call-") for thread in threading.enumerate())
        )

    def test_burst_sixteen_calls_drains_at_eof_with_two_worker_peak(self) -> None:
        self.assertEqual(2, MAX_CALL_WORKERS)
        transport = RecordingTransport(delay=0.02)
        transcript = [
            initialize_message(),
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            *(call_message(request_id) for request_id in range(100, 116)),
        ]

        status, output, diagnostics = run_bytes(
            encode_transcript(transcript),
            HiaMcpAdapter(transport),
        )

        messages = [json.loads(line) for line in output.splitlines()]
        calls = [message for message in messages if message.get("id") in range(100, 116)]
        self.assertEqual(0, status)
        self.assertEqual("", diagnostics)
        self.assertEqual(16, len(calls))
        self.assertTrue(all("error" not in message for message in calls))
        self.assertEqual(16, len(transport.calls))
        self.assertLessEqual(transport.peak_active, MAX_CALL_WORKERS)
        self.assertGreater(transport.queue_seconds[102], 0.0)
        self.assertTrue(transport.closed)
        self.assertFalse(
            any(thread.name.startswith("hia-mcp-v2-call-") for thread in threading.enumerate())
        )

    def test_queued_cancellation_is_read_before_dispatch_and_ping_remains_responsive(self) -> None:
        transport = RecordingTransport(gated=True)
        session = LiveStdioSession(transport)
        self.addCleanup(transport.release.set)
        self.addCleanup(session.finish)
        session.source.send(call_message(10))
        session.source.send(call_message(11))
        self.assertTrue(transport.wait_for_active(2))

        session.source.send(call_message(12))
        session.source.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 12, "reason": "test"},
            }
        )
        session.source.send(rpc(2, "ping", {}))
        self.assertTrue(session.output.wait_for_id(2), "reader did not answer ping while calls blocked")

        transport.release.set()
        session.finish()
        cancelled = next(message for message in session.output.messages() if message.get("id") == 12)
        structured_error = cancelled["result"]["structuredContent"]["structured_error"]
        self.assertEqual(
            "CANCELLED_BEFORE_EXECUTION",
            structured_error["code"],
        )
        self.assertEqual("stdio_queue", structured_error["details"]["stage"])
        self.assertFalse(structured_error["details"]["request_submitted"])
        self.assertEqual(
            "not_submitted",
            structured_error["details"]["submission_state"],
        )
        self.assertFalse(structured_error["details"]["hom_may_still_execute"])
        self.assertNotIn(12, transport.calls)
        self.assertIn(12, transport.cancelled)

    def test_pending_capacity_rejects_only_the_request_beyond_the_real_queue(self) -> None:
        self.assertEqual(32, MAX_PENDING_CALLS)
        transport = RecordingTransport(gated=True)
        session = LiveStdioSession(transport)
        self.addCleanup(transport.release.set)
        self.addCleanup(session.finish)
        session.source.send(call_message(10))
        session.source.send(call_message(11))
        self.assertTrue(transport.wait_for_active(2))

        pending_ids = list(range(100, 100 + MAX_PENDING_CALLS))
        for request_id in pending_ids:
            session.source.send(call_message(request_id))
        overflow_id = 100 + MAX_PENDING_CALLS
        session.source.send(call_message(overflow_id))
        session.source.send(rpc(2, "ping", {}))
        self.assertTrue(session.output.wait_for_id(2))

        transport.release.set()
        session.finish()
        messages = session.output.messages()
        overflow = next(message for message in messages if message.get("id") == overflow_id)
        self.assertEqual("QUEUE_FULL", overflow["error"]["data"]["code"])
        self.assertEqual(
            {
                "pending_capacity": MAX_PENDING_CALLS,
                "pending_count": MAX_PENDING_CALLS,
                "active_worker_limit": MAX_CALL_WORKERS,
            },
            overflow["error"]["data"]["details"],
        )
        queue_full = [
            message
            for message in messages
            if message.get("error", {}).get("data", {}).get("code") == "QUEUE_FULL"
        ]
        self.assertEqual([overflow_id], [message["id"] for message in queue_full])
        self.assertEqual({10, 11, *pending_ids}, set(transport.calls))

    def test_duplicate_request_id_is_rejected_for_active_and_queued_calls(self) -> None:
        transport = RecordingTransport(gated=True)
        session = LiveStdioSession(transport)
        self.addCleanup(transport.release.set)
        self.addCleanup(session.finish)
        session.source.send(call_message(10))
        session.source.send(call_message(11))
        self.assertTrue(transport.wait_for_active(2))

        session.source.send(call_message(10))
        session.source.send(call_message(12))
        session.source.send(call_message(12))
        session.source.send(rpc(2, "ping", {}))
        self.assertTrue(session.output.wait_for_id(2))

        transport.release.set()
        session.finish()
        duplicate_errors = [
            message
            for message in session.output.messages()
            if message.get("error", {}).get("data", {}).get("code")
            == "DUPLICATE_REQUEST_ID"
        ]
        self.assertCountEqual([10, 12], [message["id"] for message in duplicate_errors])
        self.assertEqual(1, transport.calls.count(10))
        self.assertEqual(1, transport.calls.count(12))


if __name__ == "__main__":
    unittest.main()
