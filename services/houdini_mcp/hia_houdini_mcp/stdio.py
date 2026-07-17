"""Strict UTF-8 JSONL runner for the offline Houdini MCP adapter."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Mapping, TextIO

from hia_core.houdini_contract import SchemaRegistry, strict_json_loads

from .adapter import BridgeTransport, BridgeTransportError, HoudiniMCPAdapter
from .bridge_transport import LoopbackBridgeTransport


MAX_JSONL_BYTES = 262_144
MAX_JSON_DEPTH = 32
MAX_CALL_WORKERS = 2
SHUTDOWN_GRACE_SECONDS = 0.5
PROJECT_ROOT_ENV = "HIA_PROJECT_ROOT"
EXPECTED_PYTHON_ENV = "HIA_EXPECTED_PYTHON_EXE"


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
                name="hia-mcp-call",
                daemon=True,
            )
            workers[request_id] = worker
            worker.start()
        return True

    def close_workers() -> None:
        closed.set()
        with worker_lock:
            active = list(workers.items())
        shutdown_worker = threading.Thread(
            target=adapter.shutdown,
            name="hia-mcp-shutdown",
            daemon=True,
        )
        shutdown_worker.start()
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        shutdown_worker.join(max(0.0, deadline - time.monotonic()))
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
                and {"name", "arguments"} <= set(params)
                and set(params) <= {"name", "arguments", "_meta"}
                and params.get("name") in adapter.tool_names
                and isinstance(params.get("arguments"), dict)
                and ("_meta" not in params or isinstance(params.get("_meta"), dict))
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
    registry: SchemaRegistry | None = None,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    diagnostic_stream: TextIO | None = None,
) -> int:
    """Compose an injected transport with the strict runner."""

    diagnostics = diagnostic_stream or sys.stderr
    selected_registry = registry or SchemaRegistry.b2_read_only()
    adapter = HoudiniMCPAdapter.b2_read_only(
        transport,
        registry=selected_registry,
        diagnostic_sink=lambda code: _write_diagnostic(diagnostics, code),
    )
    return run_stdio(
        adapter,
        input_stream=input_stream,
        output_stream=output_stream,
        diagnostic_stream=diagnostics,
    )


def _runtime_identity_matches(environment: Mapping[str, str] | None = None) -> bool:
    """Verify inherited project/Python identity without returning path details."""

    source = os.environ if environment is None else environment
    expected_root = source.get(PROJECT_ROOT_ENV)
    expected_python = source.get(EXPECTED_PYTHON_ENV)
    if not isinstance(expected_root, str) or not isinstance(expected_python, str):
        return False

    def canonical(value: str | os.PathLike[str]) -> str | None:
        try:
            return os.path.normcase(str(Path(value).resolve(strict=True)))
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    module_root = canonical(Path(__file__).parents[3])
    inherited_root = canonical(expected_root)
    working_root = canonical(Path.cwd())
    inherited_python = canonical(expected_python)
    active_python = canonical(sys.executable)
    return (
        module_root is not None
        and inherited_root == module_root
        and working_root == module_root
        and inherited_python is not None
        and inherited_python == active_python
    )


def main() -> int:
    """Run the approved B2C loopback transport from inherited environment."""

    if not _runtime_identity_matches():
        _write_diagnostic(sys.stderr, "B2C_RUNTIME_IDENTITY_INVALID")
        return 2
    try:
        registry = SchemaRegistry.b2_read_only()
    except Exception:
        _write_diagnostic(sys.stderr, "B2C_FROZEN_SCHEMA_INVALID")
        return 2
    try:
        transport = LoopbackBridgeTransport.from_environment(
            manifest_digest=registry.manifest_digest
        )
    except BridgeTransportError as exc:
        code = (
            "B2C_BRIDGE_CONFIGURATION_MISSING"
            if exc.code == "BRIDGE_CONFIGURATION_MISSING"
            else "B2C_BRIDGE_CONFIGURATION_INVALID"
        )
        _write_diagnostic(sys.stderr, code)
        return 2
    return serve(transport, registry=registry)


if __name__ == "__main__":
    raise SystemExit(main())
