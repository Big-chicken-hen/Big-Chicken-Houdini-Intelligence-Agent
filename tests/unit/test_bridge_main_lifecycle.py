from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge import main as bridge_main  # noqa: E402


BRIDGE_TOKEN = "bridge_" + "b" * 40
EXECUTOR_TOKEN = "executor_" + "e" * 40
BRIDGE_URL = "http://127.0.0.1:54321"


class _Client:
    process_id = 24680

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.overlays: list[dict[str, str]] = []

    def set_environment_overlay(self, values: dict[str, str]) -> None:
        self.order.append("environment_overlay")
        self.overlays.append(dict(values))


class _Session:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.start_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.order.append("session_start")
        self.start_count += 1

    def close(self) -> None:
        self.order.append("session_close")
        self.close_count += 1


class _Server:
    server_address = ("127.0.0.1", 54321)

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.close_count = 0

    def serve_forever(self, *, poll_interval: float) -> None:
        self.order.append("serve_forever")
        self.poll_interval = poll_interval

    def shutdown(self) -> None:
        self.order.append("shutdown")

    def server_close(self) -> None:
        self.order.append("server_close")
        self.close_count += 1


class BridgeMainLifecycleTests(unittest.TestCase):
    def _common_patches(
        self,
        session: _Session,
        client: _Client,
    ) -> tuple[contextlib.ExitStack, mock.Mock]:
        stack = contextlib.ExitStack()
        stack.enter_context(
            mock.patch.dict(
                os.environ,
                {
                    "HIA_BRIDGE_URL": BRIDGE_URL,
                    "HIA_BRIDGE_TOKEN": BRIDGE_TOKEN,
                    "HIA_SCENE_EXECUTOR_TOKEN": EXECUTOR_TOKEN,
                    "UNREVIEWED_API_KEY": "must_not_reach_codex_child",
                },
                clear=False,
            )
        )
        stack.enter_context(
            mock.patch.object(
                bridge_main,
                "_validated_paths",
                return_value=(
                    REPOSITORY_ROOT,
                    REPOSITORY_ROOT / "codex.exe",
                    REPOSITORY_ROOT,
                    REPOSITORY_ROOT,
                ),
            )
        )
        stack.enter_context(
            mock.patch.object(
                bridge_main.ProtocolPolicy,
                "from_project_root",
                return_value=SimpleNamespace(version="0.144.3"),
            )
        )
        client_constructor = stack.enter_context(
            mock.patch.object(
                bridge_main,
                "CodexStdioClient",
                return_value=client,
            )
        )
        stack.enter_context(
            mock.patch.object(bridge_main, "BridgeSession", return_value=session)
        )
        stack.enter_context(mock.patch.object(bridge_main.signal, "signal"))
        return stack, client_constructor

    def test_bridge_binds_and_injects_environment_before_codex_start(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        scene_queue = SimpleNamespace(shutdown=mock.Mock())
        server = _Server(order)
        stdout = io.StringIO()

        stack, client_constructor = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            return_value=registry,
        ), mock.patch.object(
            bridge_main,
            "SceneQueue",
            return_value=scene_queue,
        ), mock.patch.object(
            bridge_main,
            "BridgeApplication",
            return_value=object(),
        ), mock.patch.object(
            bridge_main,
            "LoopbackHTTPServer",
            side_effect=lambda *_args, **_kwargs: (
                order.append("bind") or server
            ),
        ) as server_constructor, contextlib.redirect_stdout(stdout):
            exit_code = bridge_main.run([])

        self.assertEqual(0, exit_code)
        self.assertEqual(1, session.start_count)
        self.assertEqual(1, session.close_count)
        self.assertEqual(1, server.close_count)
        scene_queue.shutdown.assert_called_once_with()
        self.assertLess(order.index("bind"), order.index("environment_overlay"))
        self.assertLess(
            order.index("environment_overlay"), order.index("session_start")
        )
        self.assertLess(order.index("session_start"), order.index("serve_forever"))

        self.assertEqual(
            [
                {
                    "HIA_BRIDGE_URL": "http://127.0.0.1:54321",
                    "HIA_BRIDGE_TOKEN": BRIDGE_TOKEN,
                }
            ],
            client.overlays,
        )

        command = client_constructor.call_args.args[0]
        child_environment = client_constructor.call_args.kwargs["environment"]
        self.assertEqual(str(REPOSITORY_ROOT / "codex.exe"), command[0])
        self.assertEqual("app-server", command[1])
        self.assertIn("--strict-config", command)
        overrides = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-c"
        ]
        self.assertEqual(2, len(overrides))
        override = overrides[0]
        self.assertTrue(
            override.startswith("mcp_servers.houdini_intelligence.command=\"")
        )
        self.assertIn(str(Path(sys.executable).resolve()).replace("\\", "\\\\"), override)
        self.assertEqual(
            "mcp_servers.houdini_intelligence.required=true",
            overrides[1],
        )
        self.assertNotIn(BRIDGE_TOKEN, repr(command))
        self.assertNotIn(EXECUTOR_TOKEN, repr(command))
        self.assertNotIn("HIA_BRIDGE_TOKEN", child_environment)
        self.assertNotIn("HIA_SCENE_EXECUTOR_TOKEN", child_environment)
        self.assertNotIn("HIA_BRIDGE_URL", child_environment)
        self.assertNotIn("UNREVIEWED_API_KEY", child_environment)
        self.assertNotIn("must_not_reach_codex_child", child_environment.values())
        server_constructor.assert_called_once_with(
            ("127.0.0.1", 54321),
            mock.ANY,
        )
        self.assertEqual(
            str(Path(sys.executable).resolve()),
            child_environment["HIA_EXPECTED_PYTHON_EXE"],
        )
        self.assertEqual(str(REPOSITORY_ROOT), child_environment["HIA_PROJECT_ROOT"])
        self.assertEqual("1", child_environment["PYTHONNOUSERSITE"])
        self.assertEqual(
            str(Path(sys.executable).resolve().parent).casefold(),
            child_environment["PATH"].split(os.pathsep)[0].casefold(),
        )
        python_paths = {
            item.replace("/", "\\").rstrip("\\").casefold()
            for item in child_environment["PYTHONPATH"].split(os.pathsep)
        }
        self.assertIn(
            str(REPOSITORY_ROOT / "services" / "houdini_mcp")
            .replace("/", "\\")
            .casefold(),
            python_paths,
        )
        self.assertIn(
            str(REPOSITORY_ROOT / "src").replace("/", "\\").casefold(),
            python_paths,
        )

        bootstrap = json.loads(stdout.getvalue())
        self.assertNotIn("url", bootstrap)
        self.assertNotIn("token", bootstrap)
        self.assertNotIn("executor_token", bootstrap["scene"])
        self.assertNotIn(BRIDGE_URL, stdout.getvalue())
        self.assertNotIn(BRIDGE_TOKEN, stdout.getvalue())
        self.assertNotIn(EXECUTOR_TOKEN, stdout.getvalue())

    def test_missing_or_equal_launch_credentials_fail_before_start(self) -> None:
        for bridge_token, executor_token in ((None, EXECUTOR_TOKEN), (BRIDGE_TOKEN, BRIDGE_TOKEN)):
            with self.subTest(bridge_token=bridge_token is not None):
                order: list[str] = []
                client = _Client(order)
                session = _Session(order)
                stack, _ = self._common_patches(session, client)
                environment = {
                    "HIA_BRIDGE_URL": BRIDGE_URL,
                    "HIA_SCENE_EXECUTOR_TOKEN": executor_token,
                }
                if bridge_token is not None:
                    environment["HIA_BRIDGE_TOKEN"] = bridge_token
                with stack, mock.patch.dict(
                    os.environ,
                    environment,
                    clear=True,
                ), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = bridge_main.run([])
                self.assertEqual(1, exit_code)
                self.assertEqual(0, session.start_count)

    def test_startup_error_redacts_both_credentials_and_bound_url(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        session.start = mock.Mock(
            side_effect=RuntimeError(
                "startup leaked "
                + BRIDGE_TOKEN
                + " "
                + EXECUTOR_TOKEN
                + " http://127.0.0.1:54321"
            )
        )
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        scene_queue = SimpleNamespace(shutdown=mock.Mock())
        server = _Server(order)
        stderr = io.StringIO()

        stack, _ = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            return_value=registry,
        ), mock.patch.object(
            bridge_main,
            "SceneQueue",
            return_value=scene_queue,
        ), mock.patch.object(
            bridge_main,
            "BridgeApplication",
            return_value=object(),
        ), mock.patch.object(
            bridge_main,
            "LoopbackHTTPServer",
            return_value=server,
        ), contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        encoded = stderr.getvalue()
        self.assertEqual(1, exit_code)
        self.assertNotIn(BRIDGE_TOKEN, encoded)
        self.assertNotIn(EXECUTOR_TOKEN, encoded)
        self.assertNotIn("http://127.0.0.1:54321", encoded)
        self.assertGreaterEqual(encoded.count("[REDACTED]"), 3)
        self.assertIn('"code": "BRIDGE_START_FAILED"', encoded)
        scene_queue.shutdown.assert_called_once_with()
        self.assertEqual(1, server.close_count)

    def test_bridge_url_is_required_and_strictly_loopback_origin_only(self) -> None:
        invalid_values = (
            None,
            "https://127.0.0.1:54321",
            "http://localhost:54321",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://user@127.0.0.1:54321",
            "http://127.0.0.1:54321/",
            "http://127.0.0.1:54321/path",
            "http://127.0.0.1:54321?query=1",
            "http://127.0.0.1:54321#fragment",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                environment = {
                    "HIA_BRIDGE_TOKEN": BRIDGE_TOKEN,
                    "HIA_SCENE_EXECUTOR_TOKEN": EXECUTOR_TOKEN,
                }
                if value is not None:
                    environment["HIA_BRIDGE_URL"] = value
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(bridge_main.BridgeError) as captured:
                        bridge_main._required_bridge_url()
                self.assertEqual(
                    "INVALID_LAUNCH_ENVIRONMENT",
                    captured.exception.code,
                )

        with mock.patch.dict(
            os.environ,
            {"HIA_BRIDGE_URL": "http://127.0.0.1:65535"},
            clear=True,
        ):
            self.assertEqual(
                ("http://127.0.0.1:65535", 65535),
                bridge_main._required_bridge_url(),
            )

    def test_bound_port_mismatch_fails_closed_without_start_or_retry(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        scene_queue = SimpleNamespace(shutdown=mock.Mock())
        server = _Server(order)
        server.server_address = ("127.0.0.1", 54322)
        stderr = io.StringIO()

        stack, _ = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            return_value=registry,
        ), mock.patch.object(
            bridge_main,
            "SceneQueue",
            return_value=scene_queue,
        ), mock.patch.object(
            bridge_main,
            "BridgeApplication",
            return_value=object(),
        ), mock.patch.object(
            bridge_main,
            "LoopbackHTTPServer",
            return_value=server,
        ) as server_constructor, contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        self.assertEqual(1, exit_code)
        self.assertEqual(0, session.start_count)
        self.assertEqual([], client.overlays)
        self.assertEqual(1, server_constructor.call_count)
        self.assertEqual(1, server.close_count)
        self.assertNotIn(BRIDGE_URL, stderr.getvalue())
        self.assertIn('"code": "BRIDGE_BIND_MISMATCH"', stderr.getvalue())

    def test_schema_initialization_failure_closes_unstarted_session(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        stderr = io.StringIO()
        stack, _ = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            side_effect=RuntimeError("schema failed"),
        ), contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        self.assertEqual(1, exit_code)
        self.assertEqual(0, session.start_count)
        self.assertEqual(1, session.close_count)
        self.assertIn('"code": "BRIDGE_START_FAILED"', stderr.getvalue())

    def test_server_bind_failure_closes_unstarted_session(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        scene_queue = SimpleNamespace(shutdown=mock.Mock())
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        stderr = io.StringIO()
        stack, _ = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            return_value=registry,
        ), mock.patch.object(
            bridge_main,
            "SceneQueue",
            return_value=scene_queue,
        ), mock.patch.object(
            bridge_main,
            "BridgeApplication",
            return_value=object(),
        ), mock.patch.object(
            bridge_main,
            "LoopbackHTTPServer",
            side_effect=OSError("bind failed"),
        ), contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        self.assertEqual(1, exit_code)
        self.assertEqual(0, session.start_count)
        self.assertEqual(1, session.close_count)
        scene_queue.shutdown.assert_called_once_with()
        self.assertIn("OSError: bind failed", stderr.getvalue())

    def test_scene_queue_initialization_failure_closes_unstarted_session(self) -> None:
        order: list[str] = []
        client = _Client(order)
        session = _Session(order)
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        stderr = io.StringIO()
        stack, _ = self._common_patches(session, client)
        with stack, mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            return_value=registry,
        ), mock.patch.object(
            bridge_main,
            "SceneQueue",
            side_effect=ValueError("queue failed"),
        ), contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        self.assertEqual(1, exit_code)
        self.assertEqual(0, session.start_count)
        self.assertEqual(1, session.close_count)
        self.assertIn("ValueError: queue failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
