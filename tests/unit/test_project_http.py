from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.http_server import BridgeApplication, LoopbackHTTPServer  # noqa: E402
from hia_bridge.project_effects import CompletedTurn  # noqa: E402
from hia_bridge.project_registry import ProjectRegistry  # noqa: E402
from hia_bridge.project_runner import ProjectRunner  # noqa: E402
from hia_bridge.project_service import ProjectTeamService, ProjectTeamSettings  # noqa: E402
from hia_bridge.project_thread_factory import ProjectThreadFactory  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402
from tests.unit.project_test_support import observable_thread_response, server_transports  # noqa: E402


class _Workflow:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start(self, project_id):
        self.started.append(project_id)
        return True

    def stop(self, project_id):
        self.stopped.append(project_id)
        return False

    def resume(self, project_id):
        return True


class _ProjectClient:
    def request(self, method, params):
        if method == "thread/start":
            role = params["threadSource"].rsplit("/", 1)[-1]
            return observable_thread_response(params, f"native-project-{role}")
        if method == "turn/start":
            return {"turn": {"id": "guidance-turn"}}
        raise AssertionError(f"unexpected project RPC: {method}")

    def wait_for_turn(self, thread_id, turn_id, timeout_seconds):
        return CompletedTurn(
            thread_id,
            turn_id,
            "completed",
            {"schema": "hia-project-guidance-recorded/1", "revision": 1},
        )

    def has_active_thread(self, thread_id):
        return False


class ProjectHTTPTests(unittest.TestCase):
    TOKEN = "project-http-token-with-at-least-thirty-two-chars"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        runtime = Path(self.temp.name)
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.client = CodexStdioClient(
            [sys.executable, "-B", str(ROOT / "tests" / "fakes" / "fake_app_server.py")],
            cwd=ROOT,
            environment=environment,
            policy=ProtocolPolicy.from_project_root(ROOT),
            request_timeout=5.0,
        )
        self.events = EventBuffer()
        self.session = BridgeSession(ROOT, self.client, self.events)
        self.session.start()
        self.role_client = _ProjectClient()
        self.registry = ProjectRegistry(runtime / "registry.json")
        self.runner = ProjectRunner(self.registry)
        self.workflow = _Workflow()
        self.project_team = ProjectTeamService(
            client=self.role_client,
            project_root=runtime,
            registry=self.registry,
            settings=ProjectTeamSettings(runtime / "settings.json"),
            thread_factory=ProjectThreadFactory(
                self.role_client, runtime, "hia_mcp_v2", server_transports()
            ),
            runner=self.runner,
            model_catalog=self.session.list_models,
            workflow=self.workflow,
        )
        application = BridgeApplication(
            self.session, self.events, self.TOKEN, project_team=self.project_team
        )
        self.server = LoopbackHTTPServer(("127.0.0.1", 0), application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.session.close()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.TOKEN}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        with urlopen(Request(self.base_url + path, data=data, headers=headers, method=method), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def start_team(self) -> dict:
        return self.request(
            "POST",
            "/v1/project-team/start",
            {"text": "build a Houdini cabin", "model": "gpt-test"},
        )

    def test_mode_and_single_route_are_explicit(self) -> None:
        snapshot = self.request("GET", "/v1/project-team")
        self.assertEqual("single", snapshot["project_team"]["settings"]["mode"])
        changed = self.request("POST", "/v1/project-team", {"mode": "team"})
        self.assertEqual("team", changed["project_team"]["settings"]["mode"])
        self.request("POST", "/v1/session", {"action": "start"})
        result = self.request("POST", "/v1/turn", {"text": "ordinary"})
        self.assertTrue(result["ok"])
        self.assertNotIn("routing", result)

    def test_team_route_returns_five_role_project_without_goal_fields(self) -> None:
        result = self.start_team()
        self.assertEqual("start_supervisor", result["next_action"])
        self.assertTrue(result["project_id"].startswith("project-"))
        project = result["project_team"]["projects"][0]
        self.assertEqual(5, len(project["threads"]))
        self.assertNotIn("goal_thread_id", json.dumps(result))

    def test_image_only_project_start_may_omit_text_field(self) -> None:
        draft_id = "http-image-only"
        draft = (
            Path(self.temp.name)
            / ".runtime"
            / "project-attachments"
            / "drafts"
            / draft_id
        )
        draft.mkdir(parents=True)
        image = draft / "reference.png"
        image.write_bytes(b"project-reference")

        result = self.request(
            "POST",
            "/v1/project-team/start",
            {
                "attachment_draft_id": draft_id,
                "local_image_paths": [str(image)],
            },
        )

        project = result["project_team"]["projects"][0]
        self.assertEqual(5, len(project["threads"]))
        self.assertFalse(image.exists())

    def test_project_delete_action_is_not_exposed(self) -> None:
        started = self.start_team()
        with self.assertRaises(HTTPError) as raised:
            self.request(
                "POST",
                "/v1/project-team/actions",
                {"action": "delete", "project_id": started["project_id"]},
            )
        self.assertEqual(400, raised.exception.code)
        body = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual("INVALID_PROJECT_ACTION", body["structured_error"]["code"])
        self.assertIsNotNone(self.registry.get(started["project_id"]))

    def test_stop_returns_one_fresh_snapshot(self) -> None:
        started = self.start_team()
        result = self.request(
            "POST",
            "/v1/project-team/actions",
            {"action": "stop", "project_id": started["project_id"]},
        )
        self.assertIn("project_team", result)
        self.assertEqual([started["project_id"]], self.workflow.stopped)


if __name__ == "__main__":
    unittest.main()
