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
from .transport import LoopbackTransport, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES


MAX_JSON_DEPTH = 32
MAX_CALL_WORKERS = 2
MAX_PENDING_CALLS = 32
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
    pending_calls: queue.Queue[object] = queue.Queue(maxsize=MAX_PENDING_CALLS)
    in_flight_ids: set[int | str] = set()
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

    def call_worker() -> None:
        while True:
            item = pending_calls.get()
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
                pending_calls.task_done()

    workers = [
        threading.Thread(
            target=call_worker,
            name=f"hia-mcp-v2-call-{index + 1}",
            daemon=True,
        )
        for index in range(MAX_CALL_WORKERS)
    ]
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
                                        "pending_capacity": MAX_PENDING_CALLS,
                                        "pending_count": pending_count,
                                        "active_worker_limit": MAX_CALL_WORKERS,
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
        # Accepted calls are drained before shutdown so finite stdio transcripts
        # receive one response per request. The two long-lived workers also keep
        # thread creation bounded during bursts.
        pending_calls.join()
        for _ in workers:
            pending_calls.put(_WORKER_STOP)
        for worker in workers:
            worker.join()
        closed.set()
        adapter.shutdown()
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
