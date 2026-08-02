from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_artifacts import ProjectArtifactStore


class ProjectArtifactStoreTests(unittest.TestCase):
    def test_named_and_effect_artifacts_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifacts.json"
            store = ProjectArtifactStore(path)
            store.put_named("project-1", "plan", {"stages": [{"stage_id": "s1"}]})
            store.put_effect("project-1", "effect-1", "request_plan", {"ok": True})
            reopened = ProjectArtifactStore(path)
            project = reopened.project("project-1")
        self.assertEqual("s1", project["plan"]["stages"][0]["stage_id"])
        self.assertEqual("request_plan", project["effects"]["effect-1"]["kind"])

    def test_effect_receipt_is_idempotent_but_cannot_change_content(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectArtifactStore(Path(directory) / "artifacts.json")
            store.put_effect("project-1", "effect-1", "kind", {"value": 1})
            store.put_effect("project-1", "effect-1", "kind", {"value": 1})
            with self.assertRaisesRegex(ValueError, "reused"):
                store.put_effect("project-1", "effect-1", "kind", {"value": 2})

    def test_artifact_values_must_be_json(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectArtifactStore(Path(directory) / "artifacts.json")
            with self.assertRaisesRegex(ValueError, "JSON"):
                store.put_named("project-1", "bad", {"value": object()})


if __name__ == "__main__":
    unittest.main()
