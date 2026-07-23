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
HOUDINI_MCP_TOKEN = "houdini_" + "m" * 40
HOUDINI_MCP_PORT = "58123"
HIA_MCP_V2_TOKEN = "hia_v2_" + "v" * 40
HIA_MCP_V2_PORT = "58124"
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
                    "HIA_HOUDINI_MCP_PORT": HOUDINI_MCP_PORT,
                    "FXHOUDINIMCP_TOKEN": HOUDINI_MCP_TOKEN,
                    "HIA_MCP_V2_HOST": "127.0.0.1",
                    "HIA_MCP_V2_PORT": HIA_MCP_V2_PORT,
                    "HIA_MCP_V2_TOKEN": HIA_MCP_V2_TOKEN,
                    "HIA_MCP_V2_ROUTE": "/hia-mcp-v2/v1/execute",
                    "HIA_MCP_V2_RUNTIME_DIR": str(
                        REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2"
                    ),
                    "HIA_CACHE_DIR": str(
                        REPOSITORY_ROOT / ".runtime" / "cache"
                    ),
                    "HIA_RENDER_OUTPUT_DIR": str(
                        REPOSITORY_ROOT / ".runtime" / "cache"
                    ),
                    "HIA_FOCUS_STATE_PATH": str(
                        REPOSITORY_ROOT / ".runtime" / "bridge" / "focus-mode.json"
                    ),
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
        original_is_file = Path.is_file
        original_is_dir = Path.is_dir
        expected_fx_python = (
            REPOSITORY_ROOT / bridge_main.FXHOUDINI_MCP_PYTHON_RELATIVE_PATH
        ).resolve()
        expected_fx_source = (
            REPOSITORY_ROOT / bridge_main.FXHOUDINI_MCP_SOURCE_RELATIVE_PATH
        ).resolve()

        def is_file(path: Path) -> bool:
            if path.resolve() == expected_fx_python:
                return True
            return original_is_file(path)

        def is_dir(path: Path) -> bool:
            if path.resolve() == expected_fx_source:
                return True
            return original_is_dir(path)

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
        ) as server_constructor, mock.patch.object(
            Path,
            "is_file",
            new=is_file,
        ), mock.patch.object(
            Path,
            "is_dir",
            new=is_dir,
        ), contextlib.redirect_stdout(stdout):
            exit_code = bridge_main.run(["--mcp-backend", "fxhoudini"])

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
        self.assertEqual(
            [
                "mcp_servers.houdini_intelligence.command="
                + bridge_main._toml_basic_string(
                    str(
                        REPOSITORY_ROOT
                        / bridge_main.FXHOUDINI_MCP_PYTHON_RELATIVE_PATH
                    )
                ),
                "mcp_servers.houdini_intelligence.required=true",
                'mcp_servers.houdini_intelligence.default_tools_approval_mode="approve"',
                'model_provider="hia_chatgpt_http"',
                'model_providers.hia_chatgpt_http.name="HIA ChatGPT HTTP"',
                "model_providers.hia_chatgpt_http.base_url="
                '"https://chatgpt.com/backend-api/codex"',
                'model_providers.hia_chatgpt_http.wire_api="responses"',
                "model_providers.hia_chatgpt_http.requires_openai_auth=true",
                "model_providers.hia_chatgpt_http.supports_websockets=false",
            ],
            overrides,
        )
        self.assertNotIn(BRIDGE_TOKEN, repr(command))
        self.assertNotIn(EXECUTOR_TOKEN, repr(command))
        self.assertNotIn(HOUDINI_MCP_TOKEN, repr(command))
        self.assertNotIn("HIA_BRIDGE_TOKEN", child_environment)
        self.assertNotIn("HIA_SCENE_EXECUTOR_TOKEN", child_environment)
        self.assertNotIn("HIA_BRIDGE_URL", child_environment)
        self.assertNotIn("UNREVIEWED_API_KEY", child_environment)
        self.assertNotIn("must_not_reach_codex_child", child_environment.values())
        self.assertEqual("127.0.0.1", child_environment["HOUDINI_HOST"])
        self.assertEqual(HOUDINI_MCP_PORT, child_environment["HOUDINI_PORT"])
        self.assertEqual(
            HOUDINI_MCP_TOKEN,
            child_environment["FXHOUDINIMCP_TOKEN"],
        )
        server_constructor.assert_called_once_with(
            ("127.0.0.1", 54321),
            mock.ANY,
        )
        self.assertEqual(
            str(Path(sys.executable).resolve()),
            child_environment["HIA_EXPECTED_PYTHON_EXE"],
        )
        self.assertEqual(str(REPOSITORY_ROOT), child_environment["HIA_PROJECT_ROOT"])
        self.assertEqual(
            str(REPOSITORY_ROOT / ".runtime" / "cache"),
            child_environment["HIA_CACHE_DIR"],
        )
        self.assertEqual(
            str(REPOSITORY_ROOT / ".runtime" / "cache"),
            child_environment["HIA_RENDER_OUTPUT_DIR"],
        )
        for relative in ("screenshots", "previews", "tmp"):
            self.assertTrue(
                (REPOSITORY_ROOT / ".runtime" / "cache" / relative).is_dir()
            )
        self.assertEqual("1", child_environment["PYTHONNOUSERSITE"])
        self.assertEqual(
            str(
                (
                    REPOSITORY_ROOT
                    / bridge_main.FXHOUDINI_MCP_PYTHON_RELATIVE_PATH
                ).parent
            ).casefold(),
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
        self.assertIn(
            str(REPOSITORY_ROOT / bridge_main.FXHOUDINI_MCP_SOURCE_RELATIVE_PATH)
            .replace("/", "\\")
            .casefold(),
            python_paths,
        )

        bootstrap = json.loads(stdout.getvalue())
        self.assertEqual("fxhoudini", bootstrap["mcp_backend"])
        self.assertNotIn("url", bootstrap)
        self.assertNotIn("token", bootstrap)
        self.assertNotIn("executor_token", bootstrap["scene"])
        self.assertNotIn(BRIDGE_URL, stdout.getvalue())
        self.assertNotIn(BRIDGE_TOKEN, stdout.getvalue())
        self.assertNotIn(EXECUTOR_TOKEN, stdout.getvalue())

    def test_hia_v2_is_default_and_exclusively_registered_with_owned_environment(
        self,
    ) -> None:
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
        ) as application_constructor, mock.patch.object(
            bridge_main,
            "LoopbackHTTPServer",
            return_value=server,
        ), contextlib.redirect_stdout(stdout):
            exit_code = bridge_main.run([])

        self.assertEqual(0, exit_code)
        command = client_constructor.call_args.args[0]
        child_environment = client_constructor.call_args.kwargs["environment"]
        overrides = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-c"
        ]
        hia_server = "mcp_servers.hia_mcp_v2"
        self.assertEqual(str(REPOSITORY_ROOT / "codex.exe"), command[0])
        self.assertEqual(1, command.count("--strict-config"))
        self.assertIn("mcp_servers.houdini_intelligence.enabled=false", overrides)
        self.assertIn(
            f"{hia_server}.command="
            + bridge_main._toml_basic_string(str(Path(sys.executable).resolve())),
            overrides,
        )
        self.assertIn(
            f'{hia_server}.args=["-B", "-m", "hia_mcp_v2"]',
            overrides,
        )
        self.assertIn(f"{hia_server}.required=true", overrides)
        self.assertIn(
            f'{hia_server}.default_tools_approval_mode="approve"',
            overrides,
        )
        self.assertFalse(any("fxhoudinimcp" in item.casefold() for item in overrides))

        self.assertEqual("127.0.0.1", child_environment["HIA_MCP_V2_HOST"])
        self.assertEqual(HIA_MCP_V2_PORT, child_environment["HIA_MCP_V2_PORT"])
        self.assertEqual(HIA_MCP_V2_TOKEN, child_environment["HIA_MCP_V2_TOKEN"])
        self.assertEqual(
            "/hia-mcp-v2/v1/execute",
            child_environment["HIA_MCP_V2_ROUTE"],
        )
        self.assertEqual(
            str(REPOSITORY_ROOT / ".runtime" / "hia-mcp-v2"),
            child_environment["HIA_MCP_V2_RUNTIME_DIR"],
        )
        self.assertEqual(
            str(REPOSITORY_ROOT / ".runtime" / "cache"),
            child_environment["HIA_CACHE_DIR"],
        )
        self.assertEqual(
            str(REPOSITORY_ROOT / ".runtime" / "cache"),
            child_environment["HIA_RENDER_OUTPUT_DIR"],
        )
        self.assertIn("HIA_CACHE_DIR", bridge_main.HIA_MCP_V2_CHILD_ENVIRONMENT)
        self.assertIn(
            "HIA_RENDER_OUTPUT_DIR",
            bridge_main.HIA_MCP_V2_CHILD_ENVIRONMENT,
        )
        self.assertIn(
            f'{hia_server}.env_vars='
            + json.dumps(list(bridge_main.HIA_MCP_V2_CHILD_ENVIRONMENT)),
            overrides,
        )
        for forbidden in ("HOUDINI_HOST", "HOUDINI_PORT", "FXHOUDINIMCP_TOKEN"):
            self.assertNotIn(forbidden, child_environment)
        python_paths = {
            item.replace("/", "\\").rstrip("\\").casefold()
            for item in child_environment["PYTHONPATH"].split(os.pathsep)
        }
        self.assertIn(
            str(REPOSITORY_ROOT / "services" / "hia_mcp_v2")
            .replace("/", "\\")
            .casefold(),
            python_paths,
        )
        self.assertNotIn(
            str(REPOSITORY_ROOT / bridge_main.FXHOUDINI_MCP_SOURCE_RELATIVE_PATH)
            .replace("/", "\\")
            .casefold(),
            python_paths,
        )
        self.assertEqual(
            "hia_v2",
            application_constructor.call_args.kwargs["houdini_mcp_backend"],
        )
        self.assertEqual("hia_v2", json.loads(stdout.getvalue())["mcp_backend"])

    def test_http_provider_command_is_escaped_process_local_and_secret_free(self) -> None:
        mcp_python = 'E:\\runtime\\quoted "python"\\python.exe'
        with mock.patch.object(
            bridge_main,
            "_toml_basic_string",
            wraps=bridge_main._toml_basic_string,
        ) as encoder:
            command = bridge_main._codex_app_server_command(
                REPOSITORY_ROOT / "codex.exe",
                mcp_python,
            )

        self.assertEqual("app-server", command[1])
        self.assertEqual(1, command.count("--strict-config"))
        overrides = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-c"
        ]
        self.assertEqual(9, len(overrides))
        self.assertEqual(
            1,
            overrides.count(
                'mcp_servers.houdini_intelligence.default_tools_approval_mode="approve"'
            ),
        )
        self.assertEqual(
            "mcp_servers.houdini_intelligence.command="
            + json.dumps(mcp_python, ensure_ascii=True),
            overrides[0],
        )
        self.assertEqual(
            [
                mcp_python,
                "approve",
                "hia_chatgpt_http",
                "HIA ChatGPT HTTP",
                "https://chatgpt.com/backend-api/codex",
                "responses",
            ],
            [call.args[0] for call in encoder.call_args_list],
        )
        provider_overrides = overrides[3:]
        self.assertEqual(6, len(provider_overrides))
        self.assertEqual(
            {
                'model_provider="hia_chatgpt_http"',
                'model_providers.hia_chatgpt_http.name="HIA ChatGPT HTTP"',
                "model_providers.hia_chatgpt_http.base_url="
                '"https://chatgpt.com/backend-api/codex"',
                'model_providers.hia_chatgpt_http.wire_api="responses"',
                "model_providers.hia_chatgpt_http.requires_openai_auth=true",
                "model_providers.hia_chatgpt_http.supports_websockets=false",
            },
            set(provider_overrides),
        )
        command_text = "\n".join(command).casefold()
        for forbidden in (
            "responses_websockets",
            "stream_max_retries",
            "bearer ",
            "cookie",
            "access_token",
            "refresh_token",
            "api_key",
            "password",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, command_text)

    def test_cache_directory_is_portable_and_rejects_path_escape(self) -> None:
        portable_root = REPOSITORY_ROOT.parent / "portable-hia-project"
        expected = (portable_root / ".runtime" / "cache").resolve()

        self.assertEqual(expected, bridge_main._cache_directory(portable_root, None))
        self.assertEqual(
            expected,
            bridge_main._cache_directory(portable_root, str(expected)),
        )
        with self.assertRaises(bridge_main.BridgeError) as captured:
            bridge_main._cache_directory(
                portable_root,
                str(portable_root.parent / "escaped-cache"),
            )
        self.assertEqual("INVALID_CACHE_DIR", captured.exception.code)
        source = Path(bridge_main.__file__).read_text(encoding="utf-8")
        self.assertNotIn(r"E:\houdini-intelligence-agent", source)

    def test_http_provider_command_does_not_target_persistent_config(self) -> None:
        project_config = REPOSITORY_ROOT / ".codex" / "config.toml"
        project_config_before = project_config.read_bytes()
        runtime_config = (
            REPOSITORY_ROOT / ".runtime" / "codex-home" / "config.toml"
        )
        runtime_before = (
            runtime_config.read_bytes() if runtime_config.is_file() else None
        )

        command = bridge_main._codex_app_server_command(
            REPOSITORY_ROOT / "codex.exe",
            str(REPOSITORY_ROOT / "python.exe"),
        )

        self.assertEqual(project_config_before, project_config.read_bytes())
        self.assertEqual(
            runtime_before,
            runtime_config.read_bytes() if runtime_config.is_file() else None,
        )
        command_text = "\n".join(command).replace("\\", "/").casefold()
        self.assertNotIn(".codex/config.toml", command_text)
        self.assertNotIn(".runtime/codex-home/config.toml", command_text)

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
