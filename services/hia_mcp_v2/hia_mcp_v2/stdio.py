"""Bounded UTF-8 JSONL stdio runner for HIA MCP V2."""

from __future__ import annotations

import io
import json
import queue
import sys
import threading
import time
from typing import Any, BinaryIO, TextIO

from .adapter import HiaMcpAdapter
from .tools import TOOL_BY_NAME
from .transport import LoopbackTransport, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES


MAX_JSON_DEPTH = 32
MAX_READ_CALL_WORKERS = 2
MAX_WRITE_CALL_WORKERS = 1
MAX_CALL_WORKERS = MAX_READ_CALL_WORKERS + MAX_WRITE_CALL_WORKERS
MAX_PENDING_READ_CALLS = 32
MAX_PENDING_WRITE_CALLS = 8
MAX_PENDING_CALLS = MAX_PENDING_READ_CALLS + MAX_PENDING_WRITE_CALLS
SHUTDOWN_DRAIN_SECONDS = 0.5
SHUTDOWN_CANCEL_SECONDS = 0.25
_WORKER_STOP = object()


class ProtocolFrameError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number {value} is not valid JSON")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


def decode_frame(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.rstrip(b"\r\n")
    if len(line) > MAX_REQUEST_BYTES:
        raise ProtocolFrameError("FRAME_TOO_LARGE")
    if not line.strip():
        return None
    try:
        value = json.loads(
            line.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolFrameError("INVALID_JSONL_FRAME") from exc
    if not isinstance(value, dict):
        raise ProtocolFrameError("INVALID_JSONL_FRAME")
    try:
        if _json_depth(value) > MAX_JSON_DEPTH:
            raise ProtocolFrameError("INVALID_JSONL_FRAME")
    except RecursionError as exc:
        raise ProtocolFrameError("INVALID_JSONL_FRAME") from exc
    return value


def _encoded_response(message: dict[str, Any]) -> bytes:
    raw = json.dumps(
        message,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(raw) <= MAX_RESPONSE_BYTES:
        return raw
    request_id = message.get("id")
    fallback = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32000,
            "message": "The MCP response exceeds the byte limit",
            "data": {"code": "RESPONSE_TOO_LARGE", "limit_bytes": MAX_RESPONSE_BYTES},
        },
    }
    return json.dumps(fallback, separators=(",", ":")).encode("utf-8") + b"\n"


def run_stdio(
    adapter: HiaMcpAdapter,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    diagnostic_stream: TextIO | None = None,
) -> int:
    source = input_stream or sys.stdin.buffer
    destination = output_stream or sys.stdout.buffer
    diagnostics = diagnostic_stream or sys.stderr
    write_lock = threading.Lock()
    diagnostic_lock = threading.Lock()
    call_lock = threading.Lock()
    pending_reads: queue.Queue[object] = queue.Queue(
        maxsize=MAX_PENDING_READ_CALLS
    )
    pending_writes: queue.Queue[object] = queue.Queue(
        maxsize=MAX_PENDING_WRITE_CALLS
    )
    in_flight_ids: set[int | str] = set()
    drained = threading.Event()
    drained.set()
    workers_stop = threading.Event()
    closed = threading.Event()
    output_failed = threading.Event()

    def write_diagnostic(code: str) -> None:
        with diagnostic_lock:
            try:
                diagnostics.write(f"hia_mcp_v2: {code}\n")
                diagnostics.flush()
            except Exception:
                pass

    def write(message: dict[str, Any]) -> None:
        with write_lock:
            if closed.is_set() or output_failed.is_set():
                return
            try:
                destination.write(_encoded_response(message))
                destination.flush()
            except Exception:
                output_failed.set()
                write_diagnostic("OUTPUT_WRITE_FAILED")

    def call_worker(pending_calls: queue.Queue[object]) -> None:
        while True:
            try:
                item = pending_calls.get(timeout=0.1)
            except queue.Empty:
                if workers_stop.is_set():
                    return
                continue
            try:
                if item is _WORKER_STOP:
                    return
                message, request_id, queued_at = item
                queue_seconds = max(0.0, time.monotonic() - queued_at)
                response = adapter.handle_message(
                    message,
                    stdio_queue_seconds=queue_seconds,
                )
                if response is not None:
                    write(response)
            finally:
                if item is not _WORKER_STOP:
                    with call_lock:
                        in_flight_ids.discard(request_id)
                        if not in_flight_ids:
                            drained.set()
                pending_calls.task_done()

    read_workers = [
        threading.Thread(
            target=call_worker,
            args=(pending_reads,),
            name=f"hia-mcp-v2-read-{index + 1}",
            daemon=True,
        )
        for index in range(MAX_READ_CALL_WORKERS)
    ]
    write_workers = [
        threading.Thread(
            target=call_worker,
            args=(pending_writes,),
            name=f"hia-mcp-v2-write-{index + 1}",
            daemon=True,
        )
        for index in range(MAX_WRITE_CALL_WORKERS)
    ]
    workers = [*read_workers, *write_workers]
    for worker in workers:
        worker.start()

    status = 0
    try:
        while True:
            raw = source.readline(MAX_REQUEST_BYTES + 2)
            if raw == b"":
                break
            try:
                message = decode_frame(raw)
            except ProtocolFrameError as exc:
                code = str(exc)
                write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error", "data": {"code": code}},
                    }
                )
                write_diagnostic(code)
                status = 1
                break
            if message is None:
                continue
            request_id = message.get("id")
            is_call = (
                message.get("method") == "tools/call"
                and HiaMcpAdapter._valid_id(request_id)
                and isinstance(message.get("params"), dict)
            )
            if is_call:
                duplicate = False
                queue_full = False
                pending_count = 0
                params = message.get("params", {})
                tool_name = params.get("name") if isinstance(params, dict) else None
                tool_spec = TOOL_BY_NAME.get(tool_name) if isinstance(tool_name, str) else None
                lane = "write" if tool_spec is not None and not tool_spec.read_only else "read"
                pending_calls = pending_writes if lane == "write" else pending_reads
                pending_capacity = (
                    MAX_PENDING_WRITE_CALLS
                    if lane == "write"
                    else MAX_PENDING_READ_CALLS
                )
                worker_limit = (
                    MAX_WRITE_CALL_WORKERS
                    if lane == "write"
                    else MAX_READ_CALL_WORKERS
                )
                with call_lock:
                    if request_id in in_flight_ids:
                        duplicate = True
                    else:
                        try:
                            pending_calls.put_nowait(
                                (message, request_id, time.monotonic())
                            )
                        except queue.Full:
                            queue_full = True
                            pending_count = pending_calls.qsize()
                        else:
                            in_flight_ids.add(request_id)
                            drained.clear()
                if duplicate:
                    write(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32602,
                                "message": "A tool call with this request id is already queued or active",
                                "data": {
                                    "code": "DUPLICATE_REQUEST_ID",
                                    "details": {},
                                },
                            },
                        }
                    )
                elif queue_full:
                    write(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32000,
                                "message": "The HIA MCP V2 pending call queue is full",
                                "data": {
                                    "code": "QUEUE_FULL",
                                    "details": {
                                        "lane": lane,
                                        "pending_capacity": pending_capacity,
                                        "pending_count": pending_count,
                                        "active_worker_limit": worker_limit,
                                    },
                                },
                            },
                        }
                    )
                continue
            response = adapter.handle_message(message)
            if response is not None:
                write(response)
    finally:
        # A normal finite transcript gets a short graceful drain. If the client
        # disappears while a transport call is blocked, cancellation is latched
        # for queued/active ids and shutdown remains bounded. This never kills an
        # already-entered HOM call in Houdini's UI thread.
        shutdown_started = time.monotonic()
        if not drained.wait(SHUTDOWN_DRAIN_SECONDS):
            with call_lock:
                outstanding = list(in_flight_ids)
            for request_id in outstanding:
                adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {
                            "requestId": request_id,
                            "reason": "stdio input closed",
                        },
                    }
                )
            adapter.shutdown()
            drained.wait(SHUTDOWN_CANCEL_SECONDS)
        else:
            adapter.shutdown()
        workers_stop.set()
        for pending_calls, lane_workers in (
            (pending_reads, read_workers),
            (pending_writes, write_workers),
        ):
            for _ in lane_workers:
                try:
                    pending_calls.put_nowait(_WORKER_STOP)
                except queue.Full:
                    break
        remaining_join = max(
            0.0,
            SHUTDOWN_DRAIN_SECONDS
            + SHUTDOWN_CANCEL_SECONDS
            - (time.monotonic() - shutdown_started),
        )
        join_deadline = time.monotonic() + remaining_join
        for worker in workers:
            worker.join(timeout=max(0.0, join_deadline - time.monotonic()))
        closed.set()
    return status


def serve_from_environment() -> int:
    try:
        transport = LoopbackTransport.from_environment()
    except ValueError as exc:
        sys.stderr.write(f"hia_mcp_v2: CONFIGURATION_ERROR: {exc}\n")
        return 2
    return run_stdio(HiaMcpAdapter(transport))


def run_bytes(payload: bytes, adapter: HiaMcpAdapter) -> tuple[int, bytes, str]:
    """Test helper that runs a finite stdio transcript without subprocesses."""

    output = io.BytesIO()
    diagnostics = io.StringIO()
    status = run_stdio(
        adapter,
        input_stream=io.BytesIO(payload),
        output_stream=output,
        diagnostic_stream=diagnostics,
    )
    return status, output.getvalue(), diagnostics.getvalue()
