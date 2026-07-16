"""Strict UTF-8 JSONL runner for the offline Houdini MCP adapter."""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, BinaryIO, TextIO

from hia_core.houdini_contract import strict_json_loads

from .adapter import BridgeTransport, HoudiniMCPAdapter


MAX_JSONL_BYTES = 262_144
MAX_JSON_DEPTH = 32
MAX_CALL_WORKERS = 2
SHUTDOWN_GRACE_SECONDS = 0.25


class ProtocolSessionError(ValueError):
    """A malformed frame that terminates the current stdio session."""


def json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((json_depth(item) for item in value), default=0)
    return 0


def decode_jsonl_frame(raw_line: bytes) -> dict[str, Any] | None:
    """Decode one bounded strict JSONL frame or return ``None`` for blank lines."""

    line = raw_line.rstrip(b"\r\n")
    if len(line) > MAX_JSONL_BYTES:
        raise ProtocolSessionError("JSONL frame exceeds the byte limit")
    if not line.strip():
        return None
    try:
        text = line.decode("utf-8", errors="strict")
        value = strict_json_loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolSessionError("JSONL frame is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolSessionError("JSON-RPC frame root must be an object")
    try:
        depth = json_depth(value)
    except RecursionError as exc:
        raise ProtocolSessionError("JSON-RPC frame exceeds the nesting limit") from exc
    if depth > MAX_JSON_DEPTH:
        raise ProtocolSessionError("JSON-RPC frame exceeds the nesting limit")
    return value


def _encode_message(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _write_diagnostic(stream: TextIO, code: str) -> None:
    # Codes are fixed by this module; raw frames, arguments, and tokens are never
    # copied to the diagnostic channel.
    stream.write(f"hia-houdini-mcp: {code}\n")
    stream.flush()


def run_stdio(
    adapter: HoudiniMCPAdapter,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    diagnostic_stream: TextIO | None = None,
) -> int:
    """Run one finite stdio session and return zero at clean EOF.

    A malformed, duplicate-key, non-finite, oversized, or over-deep frame emits
    one JSON-RPC parse error, records only a fixed diagnostic code, and ends the
    session without processing later frames.
    """

    source = input_stream or sys.stdin.buffer
    destination = output_stream or sys.stdout.buffer
    diagnostics = diagnostic_stream or sys.stderr
    write_lock = threading.Lock()
    worker_lock = threading.Lock()
    closed = threading.Event()
    workers: dict[int | str, threading.Thread] = {}

    def write_response(message: dict[str, Any]) -> None:
        if closed.is_set():
            return
        encoded = _encode_message(message)
        with write_lock:
            if closed.is_set():
                return
            destination.write(encoded)
            destination.flush()

    def write_terminal_response(message: dict[str, Any]) -> None:
        """Write the final protocol error and close output to worker results."""

        encoded = _encode_message(message)
        with write_lock:
            closed.set()
            destination.write(encoded)
            destination.flush()

    def finish_worker(request_id: int | str) -> None:
        with worker_lock:
            workers.pop(request_id, None)

    def handle_call(message: dict[str, Any], request_id: int | str) -> None:
        try:
            if closed.is_set():
                return
            response = adapter.handle_message(message)
            if response is not None:
                write_response(response)
        finally:
            finish_worker(request_id)

    def begin_call(message: dict[str, Any]) -> bool:
        request_id = message.get("id")
        with worker_lock:
            if request_id in workers or len(workers) >= MAX_CALL_WORKERS:
                return False
            worker = threading.Thread(
                target=handle_call,
                args=(message, request_id),
                name=f"hia-mcp-call-{request_id}",
                daemon=True,
            )
            workers[request_id] = worker
            worker.start()
        return True

    def close_workers() -> None:
        closed.set()
        with worker_lock:
            active = list(workers.items())

        def cancel_for_shutdown(request_id: int | str) -> None:
            try:
                adapter.cancel_request(request_id)
            except Exception:
                _write_diagnostic(diagnostics, "SHUTDOWN_CANCELLATION_FAILED")

        for request_id, _ in active:
            threading.Thread(
                target=cancel_for_shutdown,
                args=(request_id,),
                name=f"hia-mcp-cancel-{request_id}",
                daemon=True,
            ).start()
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        for _, worker in active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)

    status = 0
    try:
        while True:
            raw_line = source.readline(MAX_JSONL_BYTES + 2)
            if raw_line == b"":
                break
            if len(raw_line) > MAX_JSONL_BYTES and not raw_line.endswith((b"\n", b"\r")):
                write_terminal_response(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                            "data": {"code": "FRAME_TOO_LARGE"},
                        },
                    }
                )
                _write_diagnostic(diagnostics, "FRAME_TOO_LARGE")
                status = 1
                break
            try:
                message = decode_jsonl_frame(raw_line)
            except (ProtocolSessionError, RecursionError) as exc:
                stable_code = (
                    "FRAME_TOO_LARGE"
                    if "byte limit" in str(exc)
                    else "INVALID_JSONL_FRAME"
                )
                write_terminal_response(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                            "data": {"code": stable_code},
                        },
                    }
                )
                _write_diagnostic(diagnostics, stable_code)
                status = 1
                break
            if message is None:
                continue
            request_id = message.get("id")
            async_request_id = (
                isinstance(request_id, int) and not isinstance(request_id, bool)
            ) or (isinstance(request_id, str) and bool(request_id))
            params = message.get("params")
            valid_call_shape = (
                message.get("jsonrpc") == "2.0"
                and set(message) <= {"jsonrpc", "id", "method", "params"}
                and isinstance(params, dict)
                and set(params) == {"name", "arguments"}
                and params.get("name") in adapter.tool_names
                and isinstance(params.get("arguments"), dict)
            )
            if (
                message.get("method") == "tools/call"
                and "id" in message
                and async_request_id
                and valid_call_shape
            ):
                if not begin_call(message):
                    write_response(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32000,
                                "message": "The MCP call worker limit is reached",
                                "data": {"code": "QUEUE_FULL"},
                            },
                        }
                    )
                continue
            response = adapter.handle_message(message)
            if response is not None:
                write_response(response)
    finally:
        close_workers()
    return status


def serve(
    transport: BridgeTransport,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    diagnostic_stream: TextIO | None = None,
) -> int:
    """Compose an injected transport with the strict runner."""

    diagnostics = diagnostic_stream or sys.stderr
    adapter = HoudiniMCPAdapter(
        transport,
        diagnostic_sink=lambda code: _write_diagnostic(diagnostics, code),
    )
    return run_stdio(
        adapter,
        input_stream=input_stream,
        output_stream=output_stream,
        diagnostic_stream=diagnostics,
    )


def main() -> int:
    """Refuse standalone startup while B1 has no approved live transport."""

    _write_diagnostic(sys.stderr, "B1_LIVE_TRANSPORT_DISABLED")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
