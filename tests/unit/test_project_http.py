from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.http_server import BridgeApplication, LoopbackHTTPServer  # noqa: E402
from hia_bridge.project_registry import ProjectRegistry  # noqa: E402
from hia_bridge.project_service import ProjectTeamService, ProjectTeamSettings  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402


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
        self.project_team = ProjectTeamService(
            client=self.client,
            project_root=runtime,
            registry=ProjectRegistry(runtime / "registry.json"),
            settings=ProjectTeamSettings(runtime / "settings.json"),
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
            {"action": "append_guidance", "text": "keep scale", **common},
        )
        self.assertIn("project_team", guided)
        runtime = self.request(
            "POST",
            "/v1/project-team/actions",
            {
                "action": "set_role_runtime",
                "model": "gpt-next",
                "effort": "high",
                "service_tier": "priority",
                **common,
            },
        )
        role = runtime["project_team"]["projects"][0]["threads"][0]
        self.assertEqual("gpt-next", role["model"])


if __name__ == "__main__":
    unittest.main()
