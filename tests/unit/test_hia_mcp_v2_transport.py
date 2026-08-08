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

LAUNCHER_SESSION_ID = "1" * 32
OTHER_LAUNCHER_SESSION_ID = "2" * 32
FAKE_EXECUTOR_PATH = Path(__file__).resolve()


class FakeExecutor:
    def __init__(
        self,
        *,
        delay: float = 0.0,
        response_size: int = 0,
        hip_file: Any | None = None,
    ) -> None:
        self.scene_revision = 9
        self.delay = delay
        self.response_size = response_size
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._hou = type("FakeHou", (), {"hipFile": hip_file})()

    @staticmethod
    def _run_on_main_thread(callback: Any) -> Any:
        return callback()

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
            result.update(
                {
                    "stdout": "",
                    "revision": self.scene_revision,
                    "dirty": False,
                    "elapsed_seconds": 0.02,
                    "script_sha256": "0" * 64,
                    "scene_change_status": "unchanged",
                }
            )
        return result


class FakeHipFile:
    def __init__(self, path: str = "", *, is_new: bool = True) -> None:
        self.current_path = path
        self.is_new = is_new

    def isNewFile(self) -> bool:
        return self.is_new

    def path(self) -> str:
        return self.current_path


def start_test_runtime(**kwargs: Any) -> Any:
    kwargs.setdefault("launcher_session_id", LAUNCHER_SESSION_ID)
    kwargs.setdefault("expected_executor_path", FAKE_EXECUTOR_PATH)
    return start_runtime_server(**kwargs)


def transport_config(
    port: int,
    token: str,
    *,
    timeout_seconds: float = 60.0,
) -> TransportConfig:
    return TransportConfig(
        "127.0.0.1",
        port,
        token,
        LAUNCHER_SESSION_ID,
        str(FAKE_EXECUTOR_PATH),
        timeout_seconds=timeout_seconds,
    )


def expected_runtime() -> dict[str, Any]:
    return {
        "identity_version": 1,
        "launcher_session_id": LAUNCHER_SESSION_ID,
        "houdini_pid": os.getpid(),
        "executor_module_path": str(FAKE_EXECUTOR_PATH),
    }


def expected_identity(
    *,
    hip_path: str | None = None,
    hip_state: str = "unavailable",
    scene_revision: int = 0,
) -> dict[str, Any]:
    source_mtime_ns = FAKE_EXECUTOR_PATH.stat().st_mtime_ns
    return {
        **expected_runtime(),
        "hip_path": hip_path,
        "hip_state": hip_state,
        "scene_revision": scene_revision,
        "executor_loaded_mtime_ns": source_mtime_ns,
        "executor_disk_mtime_ns": source_mtime_ns,
        "executor_source_status": "current",
    }


def latch_transport(transport: LoopbackTransport) -> None:
    transport._accept_identity(  # noqa: SLF001 - focused transport contract test
        expected_identity()
    )


def raw_request(port: int, token: str | None) -> urllib.request.Request:
    body = json.dumps(
        {
            "protocol": WIRE_PROTOCOL,
            "id": 1,
            "tool": "hia_context",
            "arguments": {},
            "expected_runtime": expected_runtime(),
        }
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
        session = start_test_runtime(
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
        session = start_test_runtime(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(transport_config(session.port, token))
        cancellation = CancellationToken(stdio_queue_seconds=0.025)
        result = transport.call(
            "hia_execute_hom",
            {"script": "pass"},
            request_id=5,
            cancellation=cancellation,
        )
        self.assertTrue(result["ok"])
        self.assertEqual([("hia_execute_hom", {"script": "pass"})], executor.calls)
        self.assertEqual(
            {
                "ok",
                "result",
                "stdout",
                "warnings",
                "errors",
                "revision",
                "dirty",
                "elapsed_seconds",
                "script_sha256",
                "scene_change_status",
            },
            set(result),
        )
        self.assertEqual(0.02, result["elapsed_seconds"])
        self.assertTrue(cancellation.accepted)
        self.assertEqual(".runtime\\hia-mcp-v2", str(session.runtime_directory.relative_to(REPOSITORY_ROOT)))

    def test_health_and_execute_report_identity_while_hip_and_revision_may_change(
        self,
    ) -> None:
        token = "I" * 48
        hip_file = FakeHipFile()
        executor = FakeExecutor(hip_file=hip_file)
        session = start_test_runtime(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(transport_config(session.port, token))

        initial = transport.health()
        self.assertEqual(
            expected_identity(hip_state="unsaved", scene_revision=9),
            initial,
        )

        saved_path = str(REPOSITORY_ROOT / "different-scene.hip")
        hip_file.current_path = saved_path
        hip_file.is_new = False
        executor.scene_revision = 10
        result = transport.call(
            "hia_context",
            {},
            request_id=21,
            cancellation=CancellationToken(),
        )

        self.assertEqual(saved_path, result["runtime_identity"]["hip_path"])
        self.assertEqual("saved", result["runtime_identity"]["hip_state"])
        self.assertEqual(10, result["runtime_identity"]["scene_revision"])
        self.assertEqual([("hia_context", {})], executor.calls)

    def test_latched_process_mismatch_is_rejected_before_dispatch(self) -> None:
        token = "J" * 48
        executor = FakeExecutor()
        session = start_test_runtime(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(transport_config(session.port, token))
        transport._accept_identity(  # noqa: SLF001 - simulate a replaced process
            {
                **expected_identity(),
                "houdini_pid": os.getpid() + 1,
            }
        )

        with self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass"},
                request_id=22,
                cancellation=CancellationToken(),
            )
        self.assertEqual("HOUDINI_SESSION_CHANGED", raised.exception.code)
        self.assertFalse(raised.exception.details["request_submitted"])
        self.assertTrue(raised.exception.details["restart_required"])
        self.assertEqual([], executor.calls)

        inspected = transport.call(
            "hia_inspect",
            {"paths": ["/obj"]},
            request_id=24,
            cancellation=CancellationToken(),
        )
        self.assertTrue(inspected["restart_required"])
        self.assertEqual(
            "HOUDINI_SESSION_CHANGED",
            inspected["identity_warning"]["code"],
        )
        self.assertEqual(
            [("hia_inspect", {"paths": ["/obj"]})],
            executor.calls,
        )

    def test_changed_executor_source_is_reported_stale_before_dispatch(
        self,
    ) -> None:
        token = "S" * 48
        executor = FakeExecutor()
        session = start_test_runtime(
            executor=executor,
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(transport_config(session.port, token))
        transport.health()
        session.server.executor_loaded_mtime_ns += 1

        with self.assertRaises(TransportError) as raised:
            transport.call(
                "hia_execute_hom",
                {"script": "pass"},
                request_id=25,
                cancellation=CancellationToken(),
            )

        self.assertEqual("STALE_HOUDINI_RUNTIME", raised.exception.code)
        self.assertEqual(
            "stale",
            raised.exception.details["runtime_identity"][
                "executor_source_status"
            ],
        )
        self.assertFalse(raised.exception.details["request_submitted"])
        self.assertEqual([], executor.calls)

        inspected = transport.call(
            "hia_inspect",
            {"paths": ["/obj"]},
            request_id=26,
            cancellation=CancellationToken(),
        )
        self.assertTrue(inspected["restart_required"])
        self.assertEqual(
            "STALE_HOUDINI_RUNTIME",
            inspected["identity_warning"]["code"],
        )

    def test_old_runtime_health_contract_requires_restart(self) -> None:
        transport = LoopbackTransport(transport_config(45123, "K" * 48))
        legacy_payload = {
            "protocol": WIRE_PROTOCOL,
            "ok": True,
            "result": {"server_id": "hia_mcp_v2", "scene_revision": 7},
        }

        with mock.patch(
            "hia_mcp_v2.transport.urllib.request.urlopen",
            return_value=io.BytesIO(json.dumps(legacy_payload).encode("utf-8")),
        ), self.assertRaises(TransportError) as raised:
            transport.health()

        self.assertEqual("STALE_HOUDINI_RUNTIME", raised.exception.code)
        self.assertTrue(raised.exception.details["restart_required"])

    def test_health_latch_rejects_same_endpoint_with_a_new_process(self) -> None:
        transport = LoopbackTransport(transport_config(45123, "N" * 48))

        def health_payload(houdini_pid: int) -> io.BytesIO:
            return io.BytesIO(
                json.dumps(
                    {
                        "protocol": WIRE_PROTOCOL,
                        "ok": True,
                        "result": {
                            "server_id": "hia_mcp_v2",
                            "scene_revision": 0,
                            "runtime_identity": {
                                **expected_identity(hip_state="unsaved"),
                                "houdini_pid": houdini_pid,
                            },
                        },
                    }
                ).encode("utf-8")
            )

        with mock.patch(
            "hia_mcp_v2.transport.urllib.request.urlopen",
            side_effect=[
                health_payload(1234),
                health_payload(5678),
            ],
        ):
            self.assertEqual(1234, transport.health()["houdini_pid"])
            with self.assertRaises(TransportError) as raised:
                transport.health()

        self.assertEqual("HOUDINI_SESSION_CHANGED", raised.exception.code)
        self.assertFalse(raised.exception.details["request_submitted"])

    def test_health_rejects_wrong_launcher_session_or_executor_source(self) -> None:
        cases = (
            (
                {"launcher_session_id": OTHER_LAUNCHER_SESSION_ID},
                "HOUDINI_SESSION_CHANGED",
            ),
            (
                {
                    "executor_module_path": str(
                        REPOSITORY_ROOT / "other" / "executor.py"
                    )
                },
                "HOUDINI_RUNTIME_SOURCE_CHANGED",
            ),
        )
        for replacement, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                identity = {
                    **expected_identity(hip_state="unsaved"),
                    **replacement,
                }
                payload = {
                    "protocol": WIRE_PROTOCOL,
                    "ok": True,
                    "result": {
                        "server_id": "hia_mcp_v2",
                        "scene_revision": 0,
                        "runtime_identity": identity,
                    },
                }
                transport = LoopbackTransport(
                    transport_config(45123, "P" * 48)
                )
                with mock.patch(
                    "hia_mcp_v2.transport.urllib.request.urlopen",
                    return_value=io.BytesIO(
                        json.dumps(payload).encode("utf-8")
                    ),
                ), self.assertRaises(TransportError) as raised:
                    transport.health()
                self.assertEqual(expected_code, raised.exception.code)
                self.assertTrue(raised.exception.details["restart_required"])

    def test_missing_and_wrong_tokens_return_401_and_403(self) -> None:
        token = "U" * 48
        session = start_test_runtime(
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
        session = start_test_runtime(
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
            TransportConfig(
                "localhost",
                45123,
                "A" * 48,
                LAUNCHER_SESSION_ID,
                str(FAKE_EXECUTOR_PATH),
            )
        with self.assertRaises(ValueError):
            TransportConfig(
                "0.0.0.0",
                45123,
                "A" * 48,
                LAUNCHER_SESSION_ID,
                str(FAKE_EXECUTOR_PATH),
            )

    def test_request_size_is_bounded_before_network_submission(self) -> None:
        transport = LoopbackTransport(transport_config(45123, "A" * 48))
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
            transport_config(9, "Q" * 48, timeout_seconds=2)
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
        session = start_test_runtime(
            executor=FakeExecutor(delay=0.25),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(
            transport_config(session.port, token, timeout_seconds=0.1)
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
        self.assertFalse(raised.exception.details["interruptible_after_submission"])
        self.assertIn("do not automatically retry", raised.exception.message.casefold())

    def test_urlopen_timeout_cannot_claim_runtime_acceptance(self) -> None:
        transport = LoopbackTransport(
            transport_config(45123, "A" * 48, timeout_seconds=0.1)
        )
        latch_transport(transport)
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

    def test_response_read_timeout_reports_runtime_acceptance(self) -> None:
        class ReadTimeoutResponse:
            headers: dict[str, str] = {}

            def __enter__(self) -> "ReadTimeoutResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                raise socket.timeout("response read timed out")

        transport = LoopbackTransport(
            transport_config(45123, "A" * 48, timeout_seconds=0.1)
        )
        latch_transport(transport)
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
        self.assertTrue(cancellation.accepted)

    def test_oversized_runtime_response_is_a_stable_error(self) -> None:
        token = "Z" * 48
        session = start_test_runtime(
            executor=FakeExecutor(response_size=4_300_000),
            project_root=REPOSITORY_ROOT,
            token=token,
            port=0,
        )
        self.addCleanup(session.stop)
        transport = LoopbackTransport(transport_config(session.port, token))
        with self.assertRaises(TransportError) as raised:
            transport.call("hia_context", {}, request_id=3, cancellation=CancellationToken())
        self.assertEqual("RESPONSE_TOO_LARGE", raised.exception.code)

    def test_two_sessions_use_independent_random_ports_and_tokens(self) -> None:
        first = start_test_runtime(
            executor=FakeExecutor(),
            project_root=REPOSITORY_ROOT,
        )
        second = start_runtime_server(
            executor=FakeExecutor(),
            project_root=REPOSITORY_ROOT,
            launcher_session_id=OTHER_LAUNCHER_SESSION_ID,
            expected_executor_path=FAKE_EXECUTOR_PATH,
        )
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        first_env = first.environment()
        second_env = second.environment()
        self.assertNotEqual(first.port, second.port)
        self.assertNotEqual(first_env["HIA_MCP_V2_TOKEN"], second_env["HIA_MCP_V2_TOKEN"])
        self.assertNotEqual(
            first_env["HIA_LAUNCHER_SESSION_ID"],
            second_env["HIA_LAUNCHER_SESSION_ID"],
        )
        self.assertEqual(EXECUTE_ROUTE, first_env["HIA_MCP_V2_ROUTE"])
        self.assertTrue(
            all(
                name.startswith("HIA_MCP_V2_")
                or name == "HIA_LAUNCHER_SESSION_ID"
                for name in first_env
            )
        )


if __name__ == "__main__":
    unittest.main()
