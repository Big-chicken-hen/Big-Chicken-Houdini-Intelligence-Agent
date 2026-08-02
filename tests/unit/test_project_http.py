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
from hia_bridge.project_registry import ProjectRegistry  # noqa: E402
from hia_bridge.project_service import ProjectTeamService, ProjectTeamSettings  # noqa: E402
from hia_bridge.project_thread_factory import ProjectThreadFactory  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402
from tests.unit.project_test_support import server_transports  # noqa: E402


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
        self.workflow = _Workflow()
        self.project_team = ProjectTeamService(
            client=self.client,
            project_root=runtime,
            registry=ProjectRegistry(runtime / "registry.json"),
            settings=ProjectTeamSettings(runtime / "settings.json"),
            thread_factory=ProjectThreadFactory(
                self.client,
                runtime,
                "hia_mcp_v2",
                server_transports(),
            ),
            model_catalog=self.session.list_models,
            workflow=self.workflow,
        )
        application = BridgeApplication(
            self.session,
            self.events,
            self.TOKEN,
            project_team=self.project_team,
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
        with urlopen(
            Request(self.base_url + path, data=data, headers=headers, method=method),
            timeout=5,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_mode_and_single_route_are_explicit(self) -> None:
        snapshot = self.request("GET", "/v1/project-team")
        self.assertEqual("single", snapshot["project_team"]["settings"]["mode"])
        changed = self.request("POST", "/v1/project-team", {"mode": "team"})
        self.assertEqual("team", changed["project_team"]["settings"]["mode"])
        self.request("POST", "/v1/session", {"action": "start"})
        result = self.request(
            "POST",
            "/v1/turn",
            {"text": "ordinary", "team_override": "single"},
        )
        self.assertEqual("single", result["routing"])

    def test_team_route_returns_identity_ack_without_waiting_for_model_turn(self) -> None:
        result = self.request(
            "POST",
            "/v1/turn",
            {
                "text": "build a Houdini cabin",
                "team_override": "team",
                "model": "gpt-test",
            },
        )
        self.assertEqual("team", result["routing"])
        self.assertEqual("start_intake", result["pending_effect"])
        self.assertTrue(result["project_id"].startswith("project-"))
        self.assertEqual(1, len(result["project_team"]["projects"]))

    def test_project_role_cannot_bypass_workflow_through_ordinary_turn_route(self) -> None:
        started = self.request(
            "POST",
            "/v1/turn",
            {"text": "build scene", "team_override": "team"},
        )
        self.request(
            "POST",
            "/v1/session",
            {"action": "resume", "thread_id": started["root_thread_id"]},
        )

        with self.assertRaises(HTTPError) as raised:
            self.request(
                "POST",
                "/v1/turn",
                {"text": "bypass supervisor", "team_override": "single"},
            )
        self.assertEqual(409, raised.exception.code)
        blocked = json.loads(raised.exception.read().decode("utf-8"))
        error = blocked["structured_error"]
        self.assertEqual("PROJECT_ROLE_TURN_REQUIRES_WORKFLOW", error["code"])
        self.assertEqual(started["project_id"], error["details"]["project_id"])
        self.assertEqual("supervisor", error["details"]["role"])
        self.assertEqual(started["root_thread_id"], error["details"]["thread_id"])

        # The rejected ordinary Turn does not block the sanctioned guidance
        # route or leave a fake active Turn behind.
        guided = self.request(
            "POST",
            "/v1/project-team/actions",
            {
                "action": "append_guidance",
                "project_id": started["project_id"],
                "thread_id": started["root_thread_id"],
                "text": "preserve the roof silhouette",
            },
        )
        self.assertIn("project_team", guided)
        self.assertFalse(self.session.snapshot()["turn_active"])

    def test_role_actions_return_one_authoritative_snapshot(self) -> None:
        started = self.request(
            "POST",
            "/v1/turn",
            {"text": "build scene", "team_override": "team"},
        )
        common = {
            "project_id": started["project_id"],
            "thread_id": started["root_thread_id"],
        }
        guided = self.request(
            "POST",
            "/v1/project-team/actions",
            {
                "action": "append_guidance",
                "text": "replace the broad material scope",
                "requirement_delta": {
                    "add": [
                        {
                            "requirement_id": "REQ-simple-material",
                            "kind": "material",
                        }
                    ]
                },
                **common,
            },
        )
        self.assertIn("project_team", guided)
        record = self.project_team._registry.require(started["project_id"])
        self.assertEqual("REQ-simple-material", record.state.requirements[0].requirement_id)
        runtime = self.request(
            "POST",
            "/v1/project-team/actions",
            {
                "action": "set_role_runtime",
                "model": "fake-secondary-model",
                "effort": "high",
                "service_tier": None,
                **common,
            },
        )
        role = runtime["project_team"]["projects"][0]["threads"][0]
        self.assertEqual("fake-secondary-model", role["model"])
        with self.assertRaises(HTTPError) as invalid_runtime:
            self.request(
                "POST",
                "/v1/project-team/actions",
                {
                    "action": "set_role_runtime",
                    "model": "made-up-model",
                    "effort": "high",
                    "service_tier": None,
                    **common,
                },
            )
        self.assertEqual(400, invalid_runtime.exception.code)
        invalid_payload = json.loads(invalid_runtime.exception.read().decode("utf-8"))
        invalid_error = invalid_payload["structured_error"]
        self.assertEqual("PROJECT_RUNTIME_SELECTION_INVALID", invalid_error["code"])
        self.assertEqual("refresh_models", invalid_error["details"]["next_action"])
        stopped = self.request(
            "POST",
            "/v1/project-team/actions",
            {"action": "stop", "project_id": started["project_id"]},
        )
        self.assertIn("project_team", stopped)
        self.assertEqual([started["project_id"]], self.workflow.stopped)
        with self.assertRaises(HTTPError) as raised:
            self.request(
                "POST",
                "/v1/project-team/actions",
                {
                    "action": "append_guidance",
                    "project_id": started["project_id"],
                    "thread_id": started["root_thread_id"],
                    "text": "must not be falsely accepted",
                },
            )
        self.assertEqual(409, raised.exception.code)
        rejected = json.loads(raised.exception.read().decode("utf-8"))
        error = rejected["structured_error"]
        self.assertEqual("PROJECT_GUIDANCE_INACTIVE", error["code"])
        self.assertFalse(error["details"]["recoverable"])
        self.assertEqual("new_project", error["details"]["next_action"])


if __name__ == "__main__":
    unittest.main()
