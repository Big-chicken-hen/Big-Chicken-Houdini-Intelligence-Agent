from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge import main as bridge_main  # noqa: E402


class _Client:
    process_id = 24680


class _Session:
    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.start_count += 1

    def close(self) -> None:
        self.close_count += 1


class BridgeMainLifecycleTests(unittest.TestCase):
    def _common_patches(self, session: _Session) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
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
        stack.enter_context(
            mock.patch.object(bridge_main, "CodexStdioClient", return_value=_Client())
        )
        stack.enter_context(
            mock.patch.object(bridge_main, "BridgeSession", return_value=session)
        )
        return stack

    def test_schema_initialization_failure_closes_started_session(self) -> None:
        session = _Session()
        stderr = io.StringIO()
        with self._common_patches(session), mock.patch.object(
            bridge_main.SchemaRegistry,
            "b2_read_only",
            side_effect=RuntimeError("schema failed"),
        ), contextlib.redirect_stderr(stderr):
            exit_code = bridge_main.run([])

        self.assertEqual(1, exit_code)
        self.assertEqual(1, session.start_count)
        self.assertEqual(1, session.close_count)
        self.assertIn('"code": "BRIDGE_START_FAILED"', stderr.getvalue())

    def test_server_bind_failure_closes_started_session(self) -> None:
        session = _Session()
        scene_queue = SimpleNamespace(shutdown=mock.Mock())
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        stderr = io.StringIO()
        with self._common_patches(session), mock.patch.object(
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
        self.assertEqual(1, session.start_count)
        self.assertEqual(1, session.close_count)
        scene_queue.shutdown.assert_called_once_with()
        self.assertIn("OSError: bind failed", stderr.getvalue())

    def test_scene_queue_initialization_failure_closes_started_session(self) -> None:
        session = _Session()
        registry = SimpleNamespace(
            manifest_digest="a" * 64,
            schema_version="0.2.0",
        )
        stderr = io.StringIO()
        with self._common_patches(session), mock.patch.object(
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
        self.assertEqual(1, session.start_count)
        self.assertEqual(1, session.close_count)
        self.assertIn("ValueError: queue failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
