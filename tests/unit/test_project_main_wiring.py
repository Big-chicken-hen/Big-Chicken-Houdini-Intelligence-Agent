from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.main import _build_project_runtime
from services.bridge.hia_bridge.project_app_server import ProjectRoleClient
from services.bridge.hia_bridge.project_effects import ProjectRoleExecutor
from services.bridge.hia_bridge.project_runner import ProjectRunner
from services.bridge.hia_bridge.project_service import ProjectTeamService
from services.bridge.hia_bridge.project_workflow import ProjectWorkflowHost
from tests.unit.project_test_support import server_transports


class _Client:
    is_running = True

    def request(self, method, params):
        raise AssertionError(f"unexpected RPC during composition: {method}")


def _catalog():
    return {"models": []}


class ProjectMainWiringTests(unittest.TestCase):
    def test_runtime_composes_only_required_single_path_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = EventBuffer()
            runtime = _build_project_runtime(
                client=_Client(),
                events=events,
                project_root=root,
                selected_backend="hia_mcp_v2",
                server_transports=server_transports(),
                allowed_evidence_roots=(root,),
                model_catalog=_catalog,
            )
            try:
                self.assertIsInstance(runtime.service, ProjectTeamService)
                self.assertIsInstance(runtime.workflow, ProjectWorkflowHost)
                self.assertIsInstance(runtime.runner, ProjectRunner)
                self.assertIsInstance(runtime.client, ProjectRoleClient)
                self.assertFalse(hasattr(runtime, "artifact_store"))
                self.assertFalse(hasattr(runtime, "transfer"))
                self.assertFalse(hasattr(runtime, "recovery"))
            finally:
                self.assertTrue(runtime.close())

    def test_all_project_executors_share_one_scene_write_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = _build_project_runtime(
                client=_Client(),
                events=EventBuffer(),
                project_root=root,
                selected_backend="hia_mcp_v2",
                server_transports=server_transports(),
                allowed_evidence_roots=(root,),
                model_catalog=_catalog,
            )
            try:
                first = runtime.workflow._executor_factory("project-a")
                second = runtime.workflow._executor_factory("project-b")
                self.assertIsInstance(first, ProjectRoleExecutor)
                self.assertIs(first._scene_write_lock, second._scene_write_lock)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
