from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_attachments import (
    finalize_project_attachments,
    inspect_project_draft,
    resolve_project_attachment_paths,
)
from services.bridge.hia_bridge.project_contracts import authoritative_task_identity


class ProjectAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.draft_id = "draft-one"
        self.draft = (
            self.root
            / ".runtime"
            / "project-attachments"
            / "drafts"
            / self.draft_id
        )
        self.draft.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_image_only_project_has_stable_content_identity_and_final_owner(self) -> None:
        source = self.draft / "reference.png"
        source.write_bytes(b"image-only-project")
        candidates = inspect_project_draft(
            self.root,
            self.draft_id,
            (str(source),),
        )
        task_id, digest = authoritative_task_identity(
            "",
            tuple(item.sha256 for item in candidates),
        )
        references = finalize_project_attachments(
            self.root,
            "project-one",
            candidates,
        )
        paths = resolve_project_attachment_paths(
            self.root,
            "project-one",
            references,
        )
        self.assertTrue(task_id.startswith("task-"))
        self.assertEqual(64, len(digest))
        self.assertEqual(1, len(paths))
        self.assertEqual(
            self.root / ".runtime" / "project-attachments" / "project-one",
            Path(paths[0]).parent,
        )
        self.assertFalse(source.exists())

    def test_changed_draft_fails_before_any_final_move(self) -> None:
        source = self.draft / "reference.webp"
        source.write_bytes(b"before")
        candidates = inspect_project_draft(
            self.root,
            self.draft_id,
            (str(source),),
        )
        source.write_bytes(b"after")
        with self.assertRaisesRegex(ValueError, "changed"):
            finalize_project_attachments(self.root, "project-two", candidates)
        self.assertTrue(source.exists())
        self.assertFalse(
            (self.root / ".runtime" / "project-attachments" / "project-two").exists()
        )

    def test_paths_outside_exact_project_draft_are_rejected(self) -> None:
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside")
        with self.assertRaisesRegex(ValueError, "escapes"):
            inspect_project_draft(
                self.root,
                self.draft_id,
                (str(outside),),
            )


if __name__ == "__main__":
    unittest.main()
