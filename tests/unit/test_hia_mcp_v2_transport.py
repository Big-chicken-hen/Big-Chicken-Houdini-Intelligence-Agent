from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "hia_mcp_v2"))
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_mcp_runtime.http_server import EXECUTE_ROUTE, WIRE_PROTOCOL, start_runtime_server  # noqa: E402
from hia_mcp_v2.errors import TransportError  # noqa: E402
from hia_mcp_v2.transport import (  # noqa: E402
    MAX_REQUEST_BYTES,
    CancellationToken,
    LoopbackTransport,
    TransportConfig,
)


class FakeExecutor:
    def __init__(self, *, delay: float = 0.0, response_size: int = 0) -> None:
        self.scene_revision = 9
        self.delay = delay
        self.response_size = response_size
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments)))
        if self.delay:
            time.sleep(self.delay)
        result = {
            "ok": True,
            "result": {"tool": tool_name, "blob": "x" * self.response_size},
            "warnings": [],
            "errors": [],
        }
        if tool_name == "hia_execute_hom":
            result["phase_timings"] = {"runtime_hom_seconds": 0.02}
        return result


def raw_request(port: int, token: str | None) -> urllib.request.Request:
    body = json.dumps(
        {"protocol": WIRE_PROTOCOL, "id": 1, "tool": "hia_context", "arguments": {}}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(
        f"http://127.0.0.1:{port}{EXECUTE_ROUTE}",
        data=body,
        headers=headers,
        method="POST",
    )


class HiaMcpV2TransportTests(unittest.TestCase):
    def test_python_module_entrypoint_runs_real_stdio_initialize_list_and_call(self) -> None:
        token = "M" * 48
        session = start_runtime_server(
            executor=FakeExecutor(),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "subprocess-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "hia_context", "arguments": {}},
            },
        ]
        payload = b"".join(json.dumps(item).encode("utf-8") + b"\n" for item in messages)
        environment = os.environ.copy()
        environment.update(session.environment())
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "services" / "hia_mcp_v2")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "hia_mcp_v2"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        responses = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines())}
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", errors="replace"))
        self.assertEqual("hia_mcp_v2", responses[1]["result"]["serverInfo"]["name"])
        self.assertGreaterEqual(len(responses[2]["result"]["tools"]), 12)
        self.assertFalse(responses[3]["result"]["isError"])

    def test_loopback_bearer_transport_dispatches_one_call(self) -> None:
        token = "T" * 48
        executor = FakeExecutor()
        session = start_runtime_server(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(TransportConfig("127.0.0.1", session.port, token))
        cancellation = CancellationToken(stdio_queue_seconds=0.025)
        result = transport.call(
            "hia_execute_hom",
            {"script": "pass"},
            request_id=5,
            cancellation=cancellation,
        )
        self.assertTrue(result["ok"])
        self.assertEqual([("hia_execute_hom", {"script": "pass"})], executor.calls)
        timings = result["phase_timings"]
        self.assertEqual(0.02, timings["runtime_hom_seconds"])
        self.assertEqual(0.025, timings["stdio_queue_seconds"])
        for key in (
            "request_serialization_seconds",
            "runtime_wait_seconds",
            "runtime_serialization_seconds",
            "response_read_seconds",
            "response_decode_seconds",
            "total_seconds",
        ):
            self.assertGreaterEqual(timings[key], 0.0)
        self.assertTrue(cancellation.accepted)
        self.assertEqual(".runtime\\hia-mcp-v2", str(session.runtime_directory.relative_to(REPOSITORY_ROOT)))

    def test_missing_and_wrong_tokens_return_401_and_403(self) -> None:
        token = "U" * 48
        session = start_runtime_server(
            executor=FakeExecutor(),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        for supplied, expected in ((None, 401), ("W" * 48, 403)):
            with self.subTest(expected=expected):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(raw_request(session.port, supplied), timeout=2)
                self.assertEqual(expected, raised.exception.code)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertIn(payload["error"]["code"], {"UNAUTHORIZED", "FORBIDDEN"})

    def test_success_access_log_is_suppressed_but_non_2xx_remains_structured(self) -> None:
        token = "L" * 48
        session = start_runtime_server(
            executor=FakeExecutor(),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)

        success_log = io.StringIO()
        with contextlib.redirect_stderr(success_log):
            with urllib.request.urlopen(raw_request(session.port, token), timeout=2) as response:
                success_payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(success_payload["ok"])
        self.assertEqual("", success_log.getvalue())

        error_log = io.StringIO()
        with contextlib.redirect_stderr(error_log):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(raw_request(session.port, "W" * 48), timeout=2)
            error_payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual("FORBIDDEN", error_payload["error"]["code"])
        self.assertIn(" 403 ", error_log.getvalue())

    def test_non_loopback_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TransportConfig("localhost", 45123, "A" * 48)
        with self.assertRaises(ValueError):
            TransportConfig("0.0.0.0", 45123, "A" * 48)

    def test_request_size_is_bounded_before_network_submission(self) -> None:
        transport = LoopbackTransport(TransportConfig("127.0.0.1", 45123, "A" * 48))
        with self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "x" * MAX_REQUEST_BYTES},
                request_id=1,
                cancellation=CancellationToken(),
            )
        self.assertEqual("REQUEST_TOO_LARGE", raised.exception.code)

    def test_execute_budget_expired_in_stdio_queue_never_submits(self) -> None:
        transport = LoopbackTransport(
            TransportConfig("127.0.0.1", 9, "Q" * 48, timeout_seconds=2)
        )
        cancellation = CancellationToken(stdio_queue_seconds=1.25)

        with self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass", "timeout_seconds": 1},
                request_id=11,
                cancellation=cancellation,
            )

        self.assertEqual("TIMEOUT_BEFORE_EXECUTION", raised.exception.code)
        self.assertEqual("stdio_queue", raised.exception.details["stage"])
        self.assertEqual("not_submitted", raised.exception.details["submission_state"])
        self.assertFalse(raised.exception.details["request_submitted"])
        self.assertFalse(raised.exception.details["hom_may_still_execute"])
        self.assertFalse(cancellation.accepted)

    def test_timeout_before_response_reports_unknown_submission_state(self) -> None:
        token = "V" * 48
        session = start_runtime_server(
            executor=FakeExecutor(delay=0.25),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(
            TransportConfig("127.0.0.1", session.port, token, timeout_seconds=0.1)
        )
        with self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass"},
                request_id=2,
                cancellation=CancellationToken(),
            )
        self.assertEqual("TIMEOUT", raised.exception.code)
        self.assertEqual("runtime_request_outcome_unknown", raised.exception.details["stage"])
        self.assertEqual("unknown", raised.exception.details["submission_state"])
        self.assertIsNone(raised.exception.details["request_submitted"])
        self.assertTrue(raised.exception.details["hom_may_still_execute"])
        self.assertFalse(raised.exception.details["automatic_retry_safe"])
        self.assertFalse(raised.exception.details["interruptible_after_submission"])
        self.assertIn("do not automatically retry", raised.exception.message.casefold())

    def test_urlopen_timeout_cannot_claim_runtime_acceptance(self) -> None:
        transport = LoopbackTransport(
            TransportConfig("127.0.0.1", 45123, "A" * 48, timeout_seconds=0.1)
        )
        cancellation = CancellationToken()

        with mock.patch(
            "hia_mcp_v2.transport.urllib.request.urlopen",
            side_effect=socket.timeout("connect timed out"),
        ), self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass"},
                request_id=12,
                cancellation=cancellation,
            )

        self.assertEqual("unknown", raised.exception.details["submission_state"])
        self.assertIsNone(raised.exception.details["request_submitted"])
        self.assertTrue(raised.exception.details["hom_may_still_execute"])
        self.assertFalse(cancellation.accepted)

    def test_response_read_timeout_is_accepted_but_not_retry_safe(self) -> None:
        class ReadTimeoutResponse:
            headers: dict[str, str] = {}

            def __enter__(self) -> "ReadTimeoutResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                raise socket.timeout("response read timed out")

        transport = LoopbackTransport(
            TransportConfig("127.0.0.1", 45123, "A" * 48, timeout_seconds=0.1)
        )
        cancellation = CancellationToken()

        with mock.patch(
            "hia_mcp_v2.transport.urllib.request.urlopen",
            return_value=ReadTimeoutResponse(),
        ), self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass"},
                request_id=13,
                cancellation=cancellation,
            )

        self.assertEqual("accepted", raised.exception.details["submission_state"])
        self.assertTrue(raised.exception.details["request_submitted"])
        self.assertFalse(raised.exception.details["hom_may_still_execute"])
        self.assertFalse(raised.exception.details["automatic_retry_safe"])
        self.assertTrue(cancellation.accepted)

    def test_oversized_runtime_response_is_a_stable_error(self) -> None:
        token = "Z" * 48
        session = start_runtime_server(
            executor=FakeExecutor(response_size=4_300_000),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(TransportConfig("127.0.0.1", session.port, token))
        with self.assertRaises(TransportError) as raised:
            transport.call("hia_context", {}, request_id=3, cancellation=CancellationToken())
        self.assertEqual("RESPONSE_TOO_LARGE", raised.exception.code)

    def test_two_sessions_use_independent_random_ports_and_tokens(self) -> None:
        first = start_runtime_server(executor=FakeExecutor(), project_root=REPOSITORY_ROOT)
        second = start_runtime_server(executor=FakeExecutor(), project_root=REPOSITORY_ROOT)
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        first_env = first.environment()
        second_env = second.environment()
        self.assertNotEqual(first.port, second.port)
        self.assertNotEqual(first_env["HIA_MCP_V2_TOKEN"], second_env["HIA_MCP_V2_TOKEN"])
        self.assertEqual(EXECUTE_ROUTE, first_env["HIA_MCP_V2_ROUTE"])
        self.assertTrue(all(name.startswith("HIA_MCP_V2_") for name in first_env))


if __name__ == "__main__":
    unittest.main()
