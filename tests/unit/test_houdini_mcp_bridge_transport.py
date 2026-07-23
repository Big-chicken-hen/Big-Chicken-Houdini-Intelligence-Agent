from __future__ import annotations

import copy
import http.client
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "houdini_mcp"))

from hia_bridge.http_server import (  # noqa: E402
    BridgeApplication,
    BridgeRequestHandler,
    LoopbackHTTPServer,
)
from hia_bridge.scene_queue import B2_READ_ONLY_PROFILE  # noqa: E402
from hia_core.houdini_contract import (  # noqa: E402
    B2_READ_ONLY_TOOLS,
    SchemaRegistry,
)
from hia_houdini_mcp.adapter import (  # noqa: E402
    BridgeTransportError,
    CancellationHandoff,
)
from hia_houdini_mcp.bridge_transport import (  # noqa: E402
    BRIDGE_TOKEN_ENV,
    BRIDGE_URL_ENV,
    LoopbackBridgeTransport,
)


TOKEN = "b2c-test-token-0123456789-abcdef"
MANIFEST_DIGEST = "d" * 64


class FakeSocket:
    def __init__(self, abort: Any = None) -> None:
        self.timeouts: list[float] = []
        self._abort = abort
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def shutdown(self, _how: int) -> None:
        if callable(self._abort):
            self._abort()

    def close(self) -> None:
        self.closed = True
        if callable(self._abort):
            self._abort()


class FakeResponse:
    def __init__(
        self,
        status: int,
        payload: Any = None,
        *,
        raw: bytes | None = None,
        content_type: str = "application/json; charset=utf-8",
        content_length: int | None = None,
        include_content_length: bool = True,
        read_error: BaseException | None = None,
        entered_read: threading.Event | None = None,
        release_read: threading.Event | None = None,
    ) -> None:
        self.status = status
        self._raw = raw if raw is not None else json.dumps(
            payload, separators=(",", ":")
        ).encode("utf-8")
        self._offset = 0
        self._content_type = content_type
        self._content_length = (
            len(self._raw) if content_length is None else content_length
        )
        self._include_content_length = include_content_length
        self._read_error = read_error
        self._entered_read = entered_read
        self._release_read = release_read
        self.aborted = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def getheader(self, name: str, default: str | None = None) -> str | None:
        if name.casefold() == "content-type":
            return self._content_type
        if name.casefold() == "content-length":
            return (
                str(self._content_length)
                if self._include_content_length
                else default
            )
        return default

    def read(self, amount: int) -> bytes:
        if self._entered_read is not None:
            self._entered_read.set()
        if self._release_read is not None:
            self._release_read.wait(2.0)
        if self._read_error is not None:
            raise self._read_error
        value = self._raw[self._offset : self._offset + amount]
        self._offset += len(value)
        return value

    def abort(self) -> None:
        self.aborted.set()
        if self._release_read is not None:
            self._release_read.set()

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        self.abort()


class SlowDripResponse(FakeResponse):
    def read(self, amount: int) -> bytes:
        value = bytearray()
        while len(value) < amount and self._offset < len(self._raw):
            if self.aborted.is_set():
                raise OSError("aborted")
            time.sleep(0.02)
            value.append(self._raw[self._offset])
            self._offset += 1
        return bytes(value)


class UninterruptibleResponse(FakeResponse):
    def __init__(self, status: int, payload: Any, release: threading.Event) -> None:
        super().__init__(status, payload)
        self.release = release

    def read(self, amount: int) -> bytes:
        self.release.wait(2.0)
        return super().read(amount)

    def abort(self) -> None:
        self.aborted.set()


class BlockingCloseResponse(FakeResponse):
    """Keep close blocked to prove abort callers never wait for stream cleanup."""

    def __init__(self, status: int, payload: Any) -> None:
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        super().__init__(
            status,
            payload,
            entered_read=self.read_entered,
            release_read=self.read_release,
        )

    def close(self) -> None:
        self.close_entered.set()
        self.close_release.wait(2.0)
        super().close()


class NoResponseHeaders(FakeResponse):
    def __init__(self, status: int, payload: Any) -> None:
        super().__init__(status, payload)
        self.entered_headers = threading.Event()

    def wait_for_headers(self) -> None:
        self.entered_headers.set()
        self.aborted.wait(2.0)
        raise OSError("aborted before headers")


class FakeConnection:
    def __init__(
        self,
        factory: "ConnectionFactory",
        response: FakeResponse,
        *,
        connect_error: BaseException | None = None,
        clear_sock_on_getresponse: bool = False,
    ) -> None:
        self.factory = factory
        self.response = response
        self.connect_error = connect_error
        self.clear_sock_on_getresponse = clear_sock_on_getresponse
        self.sock: FakeSocket | None = None
        self.closed = False

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.sock = FakeSocket(self.response.abort)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.factory.requests.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": dict(headers or {}),
            }
        )

    def getresponse(self) -> FakeResponse:
        wait_for_headers = getattr(self.response, "wait_for_headers", None)
        if callable(wait_for_headers):
            wait_for_headers()
        if self.clear_sock_on_getresponse:
            self.sock = None
        return self.response

    def close(self) -> None:
        self.closed = True


class ConnectionFactory:
    def __init__(
        self,
        responses: list[FakeResponse],
        *,
        connect_errors: dict[int, BaseException] | None = None,
        clear_sock_on_getresponse: set[int] | None = None,
    ) -> None:
        self._responses = responses
        self._connect_errors = connect_errors or {}
        self._clear_sock_on_getresponse = clear_sock_on_getresponse or set()
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.connections: list[FakeConnection] = []
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, host: str, port: int, *, timeout: float) -> FakeConnection:
        with self._lock:
            index = len(self.connections)
            if index >= len(self._responses):
                raise AssertionError("Unexpected HTTP connection")
            connection = FakeConnection(
                self,
                self._responses[index],
                connect_error=self._connect_errors.get(index),
                clear_sock_on_getresponse=index in self._clear_sock_on_getresponse,
            )
            self.connections.append(connection)
            self.calls.append((host, port, timeout))
            return connection


def bridge_status(
    *,
    revision: int = 7,
    available: bool = True,
    schema_digest: str = MANIFEST_DIGEST,
) -> dict[str, Any]:
    return {
        "ok": True,
        "scene": {
            "available": available,
            "profile": "b2_read_only",
            "schema_version": "0.2.0",
            "schema_digest": schema_digest,
            "launch_id": "launch-test-1",
            "generation": 1,
            "attestation_digest": "a" * 64,
            "houdini_build": "21.0.440",
            "hip_session_id": "hip-session-live",
            "hip_fingerprint": "b" * 64,
            "scene_revision": revision,
            "catalog_digest": "c" * 64,
            "enabled_tools": list(B2_READ_ONLY_TOOLS),
            "allowed_node_types": [
                {
                    "context": context,
                    "requested_name": requested,
                    "resolved_name": resolved,
                    "available": True,
                }
                for context, requested, resolved in (
                    ("Object", "geo", "geo"),
                    ("Sop", "box", "box"),
                    ("Sop", "transform", "xform"),
                    ("Sop", "merge", "merge"),
                    ("Sop", "null", "null"),
                )
            ],
        },
    }


def prepared_scene_request(request_id: str = "scene-request-1") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "thread_id": "mcp-session-1",
        "turn_id": "rpc-request-1",
        "hip_session_id": "hip-session-live",
        "base_scene_revision": 7,
        "idempotency_key": "0123456789abcdef0123456789abcdef",
        "deadline_ms": 10_000,
        "permission_level": "scene_read",
        "include_graph_summaries": False,
    }


class LoopbackBridgeTransportTests(unittest.TestCase):
    def make_transport(
        self,
        factory: ConnectionFactory,
        **kwargs: Any,
    ) -> LoopbackBridgeTransport:
        return LoopbackBridgeTransport(
            "http://127.0.0.1:49152",
            TOKEN,
            manifest_digest=kwargs.pop("manifest_digest", MANIFEST_DIGEST),
            connection_factory=factory,
            **kwargs,
        )

    def test_origin_is_exact_loopback_http_and_environment_is_required(self) -> None:
        invalid = (
            "http://localhost:49152",
            "http://0.0.0.0:49152",
            "https://127.0.0.1:49152",
            "http://127.0.0.1",
            "http://127.0.0.1:49152/v1",
            "http://user@127.0.0.1:49152",
            "http://127.0.0.1:49152?token=secret",
        )
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(BridgeTransportError) as caught:
                    LoopbackBridgeTransport(
                        url, TOKEN, manifest_digest=MANIFEST_DIGEST
                    )
                self.assertEqual("BRIDGE_CONFIGURATION_INVALID", caught.exception.code)
        with self.assertRaises(BridgeTransportError) as caught:
            LoopbackBridgeTransport.from_environment(
                {}, manifest_digest=MANIFEST_DIGEST
            )
        self.assertEqual("BRIDGE_CONFIGURATION_MISSING", caught.exception.code)

        transport = LoopbackBridgeTransport.from_environment(
            {BRIDGE_URL_ENV: "http://127.0.0.1:49152", BRIDGE_TOKEN_ENV: TOKEN},
            manifest_digest=MANIFEST_DIGEST,
        )
        self.assertEqual("http://127.0.0.1:49152", transport.base_url)

    def test_real_http10_close_response_keeps_nonempty_body_readable(self) -> None:
        registry = SchemaRegistry.b2_read_only()

        class StaticReadOnlyQueue:
            profile = B2_READ_ONLY_PROFILE
            expected_schema_digest = registry.manifest_digest

            @staticmethod
            def live_capability_status() -> dict[str, Any]:
                return copy.deepcopy(
                    bridge_status(schema_digest=registry.manifest_digest)["scene"]
                )

        application = BridgeApplication(
            object(),
            object(),
            TOKEN,
            scene_queue=StaticReadOnlyQueue(),
            scene_registry=registry,
            scene_executor_token="executor-" + "e" * 32,
        )
        server = LoopbackHTTPServer(("127.0.0.1", 0), application)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        observations: list[tuple[int, bool, bool, str | None]] = []

        class InspectingConnection(http.client.HTTPConnection):
            def getresponse(self) -> http.client.HTTPResponse:
                response = super().getresponse()
                observations.append(
                    (
                        response.version,
                        response.will_close,
                        self.sock is None,
                        response.getheader("Connection"),
                    )
                )
                return response

        host, port = server.server_address
        transport = LoopbackBridgeTransport(
            f"http://{host}:{port}",
            TOKEN,
            manifest_digest=registry.manifest_digest,
            connection_factory=InspectingConnection,
        )
        try:
            prepared = transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-real-http10",
            )
            self.assertEqual("hip-session-live", prepared["hip_session_id"])
            self.assertEqual("HTTP/1.0", BridgeRequestHandler.protocol_version)
            self.assertEqual([(10, True, True, "close")], observations)
        finally:
            transport.close()
            server.shutdown()
            server.server_close()
            server_thread.join(1.0)
        self.assertFalse(server_thread.is_alive())

    def test_sockless_connection_after_headers_does_not_hide_response_body(self) -> None:
        response = FakeResponse(200, bridge_status())
        factory = ConnectionFactory(
            [response],
            clear_sock_on_getresponse={0},
        )
        prepared = self.make_transport(factory).prepare_arguments(
            "houdini_scene_info",
            {"include_graph_summaries": False},
            rpc_request_id="rpc-sockless-response",
        )
        self.assertEqual("hip-session-live", prepared["hip_session_id"])
        self.assertIsNone(factory.connections[0].sock)
        self.assertEqual(len(response._raw), response._offset)
        self.assertTrue(response.closed.is_set())
        self.assertEqual(1, response.close_calls)

    def test_graph_and_unknown_tools_are_rejected_before_any_connection(self) -> None:
        factory = ConnectionFactory([])
        transport = self.make_transport(factory)
        for name in (
            "houdini_graph_validate",
            "houdini_graph_apply",
            "houdini_graph_verify",
            "arbitrary_python",
        ):
            with self.subTest(tool=name):
                with self.assertRaises(BridgeTransportError) as caught:
                    transport.call_tool(
                        name,
                        prepared_scene_request(),
                        rpc_request_id=name,
                        cancellation_handoff=CancellationHandoff(),
                    )
                self.assertEqual("TOOL_NOT_ALLOWED", caught.exception.code)
        self.assertEqual([], factory.connections)

    def test_prepare_arguments_keeps_only_semantics_and_uses_live_status(self) -> None:
        factory = ConnectionFactory([FakeResponse(200, bridge_status(revision=19))])
        transport = self.make_transport(factory)
        prepared = transport.prepare_arguments(
            "houdini_scene_info",
            {
                "include_graph_summaries": True,
                "hip_session_id": "model-forged",
                "base_scene_revision": 999,
                "permission_level": "scene_write",
            },
            rpc_request_id="rpc-1",
        )
        self.assertEqual("hip-session-live", prepared["hip_session_id"])
        self.assertEqual(19, prepared["base_scene_revision"])
        self.assertEqual("scene_read", prepared["permission_level"])
        self.assertTrue(prepared["include_graph_summaries"])
        self.assertEqual(
            {
                "request_id",
                "thread_id",
                "turn_id",
                "hip_session_id",
                "base_scene_revision",
                "idempotency_key",
                "deadline_ms",
                "permission_level",
                "include_graph_summaries",
            },
            set(prepared),
        )
        request = factory.requests[0]
        self.assertEqual(("GET", "/v1/scene/status"), (request["method"], request["path"]))
        self.assertEqual(f"Bearer {TOKEN}", request["headers"]["Authorization"])

    def test_prepare_fails_closed_on_capability_mismatch(self) -> None:
        factory = ConnectionFactory(
            [FakeResponse(200, bridge_status(available=False))]
        )
        with self.assertRaises(BridgeTransportError) as caught:
            self.make_transport(factory).prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id=1,
            )
        self.assertEqual("CAPABILITY_MISMATCH", caught.exception.code)

    def test_status_evidence_is_exact_and_bound_to_local_manifest(self) -> None:
        malformed_payloads: list[tuple[dict[str, Any], str]] = []
        wrong_digest = bridge_status()
        wrong_digest["scene"]["schema_digest"] = "e" * 64
        malformed_payloads.append((wrong_digest, "CAPABILITY_MISMATCH"))
        extra_field = bridge_status()
        extra_field["scene"]["unexpected"] = True
        malformed_payloads.append((extra_field, "INVALID_BRIDGE_RESPONSE"))
        unavailable_type = bridge_status()
        unavailable_type["scene"]["allowed_node_types"][1]["available"] = False
        unavailable_type["scene"]["allowed_node_types"][1]["resolved_name"] = None
        malformed_payloads.append((unavailable_type, "INVALID_BRIDGE_RESPONSE"))
        wrong_resolution = bridge_status()
        wrong_resolution["scene"]["allowed_node_types"][2]["resolved_name"] = "transform"
        malformed_payloads.append((wrong_resolution, "INVALID_BRIDGE_RESPONSE"))
        missing_identity = bridge_status()
        del missing_identity["scene"]["launch_id"]
        malformed_payloads.append((missing_identity, "INVALID_BRIDGE_RESPONSE"))

        for payload, expected in malformed_payloads:
            with self.subTest(expected=expected, keys=tuple(payload["scene"])):
                transport = self.make_transport(
                    ConnectionFactory([FakeResponse(200, payload)])
                )
                with self.assertRaises(BridgeTransportError) as caught:
                    transport.prepare_arguments(
                        "houdini_scene_info",
                        {"include_graph_summaries": False},
                        rpc_request_id="rpc-status",
                    )
                self.assertEqual(expected, caught.exception.code)

    def test_prepare_node_types_preserves_only_the_allowed_semantic_list(self) -> None:
        factory = ConnectionFactory([FakeResponse(200, bridge_status())])
        transport = self.make_transport(factory)
        node_types = [
            {"context": "Object", "name": "geo"},
            {"context": "Sop", "name": "box"},
        ]
        prepared = transport.prepare_arguments(
            "houdini_node_type_info",
            {"node_types": node_types, "request_id": "model-controlled"},
            rpc_request_id=2,
        )
        self.assertEqual(node_types, prepared["node_types"])
        self.assertNotEqual("model-controlled", prepared["request_id"])
        self.assertNotIn("include_graph_summaries", prepared)

    def test_submit_polls_terminal_result_and_sends_bearer_each_time(self) -> None:
        result = {"ok": True, "request_id": "scene-request-1", "value": "bounded"}
        factory = ConnectionFactory(
            [
                FakeResponse(
                    202,
                    {"ok": True, "request_id": "scene-request-1", "terminal": False},
                ),
                FakeResponse(
                    200,
                    {
                        "ok": True,
                        "request_id": "scene-request-1",
                        "terminal": True,
                        "result": result,
                    },
                ),
            ]
        )
        transport = self.make_transport(factory)
        handoff = CancellationHandoff()
        actual = transport.call_tool(
            "houdini_scene_info",
            prepared_scene_request(),
            rpc_request_id="rpc-1",
            cancellation_handoff=handoff,
        )
        self.assertEqual(result, actual)
        self.assertTrue(handoff.submitted)
        self.assertEqual(
            ["/v1/scene/requests", "/v1/scene/requests/scene-request-1/result?wait_ms=250"],
            [request["path"] for request in factory.requests],
        )
        body = json.loads(factory.requests[0]["body"].decode("utf-8"))
        self.assertEqual("houdini_scene_info", body["tool_name"])
        self.assertEqual(prepared_scene_request(), body["arguments"])
        self.assertTrue(
            all(
                request["headers"]["Authorization"] == f"Bearer {TOKEN}"
                for request in factory.requests
            )
        )
        self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_bridge_rejection_timeout_disconnect_and_malformed_body_are_bounded(self) -> None:
        cases = (
            (
                ConnectionFactory(
                    [
                        FakeResponse(
                            403,
                            {
                                "ok": False,
                                "structured_error": {
                                    "code": "TOOL_NOT_ALLOWED",
                                    "message": f"remote {TOKEN}",
                                },
                            },
                        )
                    ]
                ),
                "INVALID_BRIDGE_RESPONSE",
            ),
            (
                ConnectionFactory(
                    [FakeResponse(200, raw=b"not-json", content_length=8)]
                ),
                "INVALID_BRIDGE_RESPONSE",
            ),
            (
                ConnectionFactory(
                    [FakeResponse(200, bridge_status())],
                    connect_errors={0: ConnectionRefusedError("secret endpoint")},
                ),
                "BRIDGE_DISCONNECTED",
            ),
            (
                ConnectionFactory(
                    [
                        FakeResponse(
                            200,
                            bridge_status(),
                            read_error=socket.timeout("secret timeout"),
                        )
                    ]
                ),
                "BRIDGE_DISCONNECTED",
            ),
        )
        for factory, expected in cases:
            with self.subTest(expected=expected):
                transport = self.make_transport(factory)
                with self.assertRaises(BridgeTransportError) as caught:
                    if expected == "INVALID_BRIDGE_RESPONSE" and factory.requests == []:
                        # The first two cases exercise different entry points but both
                        # must remain fixed-message and credential-safe.
                        if len(factory._responses) == 1 and factory._responses[0].status == 403:
                            transport.call_tool(
                                "houdini_scene_info",
                                prepared_scene_request(),
                                rpc_request_id="rpc-rejected",
                                cancellation_handoff=CancellationHandoff(),
                            )
                        else:
                            transport.prepare_arguments(
                                "houdini_scene_info",
                                {"include_graph_summaries": False},
                                rpc_request_id="rpc-malformed",
                            )
                    else:
                        transport.prepare_arguments(
                            "houdini_scene_info",
                            {"include_graph_summaries": False},
                            rpc_request_id="rpc-failure",
                        )
                self.assertEqual(expected, caught.exception.code)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn("secret", str(caught.exception))

    def test_safe_remote_rejection_code_is_preserved_but_message_is_not(self) -> None:
        factory = ConnectionFactory(
            [
                FakeResponse(
                    429,
                    {
                        "ok": False,
                        "structured_error": {
                            "code": "QUEUE_FULL",
                            "message": "remote implementation detail",
                        },
                    },
                )
            ]
        )
        with self.assertRaises(BridgeTransportError) as caught:
            self.make_transport(factory).call_tool(
                "houdini_scene_info",
                prepared_scene_request(),
                rpc_request_id="rpc-rejected",
                cancellation_handoff=CancellationHandoff(),
            )
        self.assertEqual("QUEUE_FULL", caught.exception.code)
        self.assertEqual({"http_status": 429}, caught.exception.details)
        self.assertNotIn("implementation detail", str(caught.exception))

    def test_oversized_response_is_rejected_before_read(self) -> None:
        response = FakeResponse(200, {}, content_length=129)
        factory = ConnectionFactory([response])
        transport = self.make_transport(factory, max_response_bytes=128)
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-large",
            )
        self.assertEqual("INVALID_BRIDGE_RESPONSE", caught.exception.code)
        self.assertTrue(response.closed.is_set())
        self.assertEqual(1, response.close_calls)

    def test_unannounced_oversized_response_is_rejected_while_reading(self) -> None:
        response = FakeResponse(
            200,
            raw=b"x" * 129,
            include_content_length=False,
        )
        transport = self.make_transport(
            ConnectionFactory([response]),
            max_response_bytes=128,
        )
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-unannounced-large",
            )
        self.assertEqual("INVALID_BRIDGE_RESPONSE", caught.exception.code)
        self.assertEqual(129, response._offset)
        self.assertTrue(response.closed.is_set())
        self.assertEqual(1, response.close_calls)

    def test_truncated_response_still_fails_closed(self) -> None:
        response = FakeResponse(200, raw=b"{}", content_length=3)
        transport = self.make_transport(ConnectionFactory([response]))
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-truncated",
            )
        self.assertEqual("INVALID_BRIDGE_RESPONSE", caught.exception.code)
        self.assertTrue(response.closed.is_set())
        self.assertEqual(1, response.close_calls)

    def test_deadline_after_headers_closes_detached_response_stream(self) -> None:
        response = BlockingCloseResponse(200, bridge_status())
        factory = ConnectionFactory(
            [response],
            clear_sock_on_getresponse={0},
        )
        transport = self.make_transport(
            factory,
            status_timeout=0.05,
            total_timeout=0.2,
            read_timeout=1.0,
        )
        started = time.monotonic()
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-detached-deadline",
            )
        elapsed = time.monotonic() - started
        self.assertEqual("DEADLINE_EXCEEDED", caught.exception.code)
        self.assertLess(elapsed, 0.3)
        self.assertTrue(response.read_entered.is_set())
        self.assertTrue(response.close_entered.wait(0.5))
        self.assertFalse(response.closed.is_set())
        response.close_release.set()
        self.assertTrue(response.closed.wait(0.5))
        self.assertTrue(response.aborted.is_set())
        self.assertEqual(1, response.close_calls)

    def test_cancel_during_status_body_closes_detached_response_stream(self) -> None:
        response = BlockingCloseResponse(200, bridge_status())
        factory = ConnectionFactory(
            [response],
            clear_sock_on_getresponse={0},
        )
        transport = self.make_transport(factory)
        handoff = CancellationHandoff()
        errors: list[BridgeTransportError] = []

        def invoke() -> None:
            try:
                transport.prepare_arguments(
                    "houdini_scene_info",
                    {"include_graph_summaries": False},
                    rpc_request_id="rpc-cancel-status",
                    cancellation_handoff=handoff,
                )
            except BridgeTransportError as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(response.read_entered.wait(0.5))
        started = time.monotonic()
        self.assertFalse(handoff.cancel())
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(response.close_entered.wait(0.5))
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(["CANCELLED"], [error.code for error in errors])
        self.assertFalse(response.closed.is_set())
        response.close_release.set()
        self.assertTrue(response.closed.wait(0.5))
        self.assertEqual(1, response.close_calls)

    def test_cancel_during_initial_submit_body_closes_response_and_notifies_bridge(
        self,
    ) -> None:
        response = BlockingCloseResponse(
            202,
            {"ok": True, "request_id": "scene-request-1", "terminal": False},
        )
        factory = ConnectionFactory(
            [response, FakeResponse(200, {"ok": True})],
            clear_sock_on_getresponse={0},
        )
        transport = self.make_transport(factory)
        handoff = CancellationHandoff()
        errors: list[BridgeTransportError] = []

        def invoke() -> None:
            try:
                transport.call_tool(
                    "houdini_scene_info",
                    prepared_scene_request(),
                    rpc_request_id="rpc-cancel-submit",
                    cancellation_handoff=handoff,
                )
            except BridgeTransportError as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(response.read_entered.wait(0.5))
        started = time.monotonic()
        self.assertFalse(handoff.cancel())
        transport.cancel("rpc-cancel-submit")
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(response.close_entered.wait(0.5))
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(["CANCELLED"], [error.code for error in errors])
        self.assertEqual(
            "/v1/scene/requests/scene-request-1/cancel",
            factory.requests[1]["path"],
        )
        self.assertEqual(2, len(factory.requests))
        self.assertFalse(response.closed.is_set())
        response.close_release.set()
        self.assertTrue(response.closed.wait(0.5))
        self.assertEqual(1, response.close_calls)

    def test_absolute_deadline_stops_a_continuously_active_slow_drip(self) -> None:
        factory = ConnectionFactory([SlowDripResponse(200, bridge_status())])
        transport = self.make_transport(
            factory,
            status_timeout=0.08,
            total_timeout=0.2,
            read_timeout=1.0,
        )
        started = time.monotonic()
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-drip",
            )
        elapsed = time.monotonic() - started
        self.assertEqual("DEADLINE_EXCEEDED", caught.exception.code)
        self.assertLess(elapsed, 0.3)
        self.assertTrue(factory.connections[0].closed)
        self.assertTrue(factory.connections[0].response.aborted.is_set())

    def test_absolute_deadline_interrupts_a_server_that_never_sends_headers(self) -> None:
        response = NoResponseHeaders(200, bridge_status())
        factory = ConnectionFactory([response])
        transport = self.make_transport(
            factory,
            status_timeout=0.05,
            total_timeout=0.2,
            read_timeout=1.0,
        )
        started = time.monotonic()
        with self.assertRaises(BridgeTransportError) as caught:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-no-headers",
            )
        elapsed = time.monotonic() - started
        self.assertEqual("DEADLINE_EXCEEDED", caught.exception.code)
        self.assertLess(elapsed, 0.3)
        self.assertTrue(response.entered_headers.is_set())
        self.assertTrue(response.aborted.is_set())

    def test_unresponsive_io_is_daemon_bounded_and_exhausts_no_more_slots(self) -> None:
        release = threading.Event()
        factory = ConnectionFactory(
            [
                UninterruptibleResponse(200, bridge_status(), release),
                FakeResponse(200, bridge_status()),
            ]
        )
        transport = self.make_transport(
            factory,
            status_timeout=0.05,
            total_timeout=0.2,
            read_timeout=1.0,
            max_io_workers=1,
        )
        with self.assertRaises(BridgeTransportError) as timed_out:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-hung",
            )
        self.assertEqual("DEADLINE_EXCEEDED", timed_out.exception.code)
        with self.assertRaises(BridgeTransportError) as saturated:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-no-more-workers",
            )
        self.assertEqual("BRIDGE_IO_SATURATED", saturated.exception.code)
        self.assertEqual(1, len(factory.connections))

        release.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                prepared = transport.prepare_arguments(
                    "houdini_scene_info",
                    {"include_graph_summaries": False},
                    rpc_request_id="rpc-recovered",
                )
            except BridgeTransportError as exc:
                if exc.code != "BRIDGE_IO_SATURATED":
                    raise
                time.sleep(0.01)
                continue
            break
        else:
            self.fail("The bounded I/O slot did not recover after worker release")
        self.assertEqual("hip-session-live", prepared["hip_session_id"])

    def test_close_is_idempotent_interrupts_active_io_and_rejects_new_work(self) -> None:
        response = BlockingCloseResponse(200, bridge_status())
        factory = ConnectionFactory(
            [response],
            clear_sock_on_getresponse={0},
        )
        transport = self.make_transport(
            factory,
            status_timeout=1.0,
            total_timeout=1.0,
        )
        errors: list[BridgeTransportError] = []

        def invoke() -> None:
            try:
                transport.prepare_arguments(
                    "houdini_scene_info",
                    {"include_graph_summaries": False},
                    rpc_request_id="rpc-close",
                )
            except BridgeTransportError as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(response.read_entered.wait(0.5))
        started = time.monotonic()
        transport.close()
        transport.close()
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(response.close_entered.wait(0.5))
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(["TRANSPORT_CLOSED"], [error.code for error in errors])
        self.assertFalse(response.closed.is_set())
        response.close_release.set()
        self.assertTrue(response.closed.wait(0.5))
        self.assertEqual(1, response.close_calls)
        deadline = time.monotonic() + 0.5
        while not factory.connections[0].closed and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(factory.connections[0].closed)
        with self.assertRaises(BridgeTransportError) as closed:
            transport.prepare_arguments(
                "houdini_scene_info",
                {"include_graph_summaries": False},
                rpc_request_id="rpc-after-close",
            )
        self.assertEqual("TRANSPORT_CLOSED", closed.exception.code)
        self.assertEqual(1, len(factory.connections))

    def test_cancel_posts_once_and_latched_handoff_stops_further_polling(self) -> None:
        poll_response = BlockingCloseResponse(
            202,
            {"ok": True, "request_id": "scene-request-1", "terminal": False},
        )
        factory = ConnectionFactory(
            [
                FakeResponse(
                    202,
                    {"ok": True, "request_id": "scene-request-1", "terminal": False},
                ),
                poll_response,
                FakeResponse(200, {"ok": True}),
            ],
            clear_sock_on_getresponse={1},
        )
        transport = self.make_transport(factory)
        handoff = CancellationHandoff()
        errors: list[BridgeTransportError] = []

        def invoke() -> None:
            try:
                transport.call_tool(
                    "houdini_scene_info",
                    prepared_scene_request(),
                    rpc_request_id="rpc-cancel",
                    cancellation_handoff=handoff,
                )
            except BridgeTransportError as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(poll_response.read_entered.wait(1.0))
        self.assertTrue(handoff.cancel())
        started = time.monotonic()
        transport.cancel("rpc-cancel")
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(poll_response.close_entered.wait(0.5))
        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(["CANCELLED"], [error.code for error in errors])
        self.assertEqual(
            "/v1/scene/requests/scene-request-1/cancel",
            factory.requests[2]["path"],
        )
        self.assertEqual(3, len(factory.requests))
        self.assertFalse(poll_response.closed.is_set())
        poll_response.close_release.set()
        self.assertTrue(poll_response.closed.wait(0.5))
        self.assertEqual(1, poll_response.close_calls)


if __name__ == "__main__":
    unittest.main()
