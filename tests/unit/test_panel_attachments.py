from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_panel.attachment_store import AttachmentStore  # noqa: E402


class PanelAttachmentStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run_root = (
            REPOSITORY_ROOT
            / ".runtime"
            / "tmp"
            / "test-panel-attachments"
            / uuid.uuid4().hex
        )
        cls.project_root = cls.run_root / "project"
        cls.source_root = cls.run_root / "sources"
        cls.source_root.mkdir(parents=True)
        cls.store = AttachmentStore(cls.project_root)

    def _source(self, name: str, content: bytes = b"test-image") -> Path:
        path = self.source_root / f"{uuid.uuid4().hex}-{name}"
        path.write_bytes(content)
        return path

    def test_copy_file_accepts_supported_extensions_in_thread_directory(self) -> None:
        expected_directory = (
            self.project_root / ".runtime" / "attachments" / "thread-123"
        ).resolve()

        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".JPEG"):
            with self.subTest(suffix=suffix):
                source = self._source(f"reference{suffix}", suffix.encode("ascii"))
                copied = Path(self.store.copy_file("thread-123", source))
                self.assertEqual(expected_directory, copied.parent)
                self.assertEqual(suffix.lower(), copied.suffix)
                self.assertEqual(suffix.encode("ascii"), copied.read_bytes())

    def test_copy_file_uses_unique_names_without_overwriting(self) -> None:
        first = Path(self.store.copy_file("thread-unique", self._source("same.png", b"a")))
        second = Path(
            self.store.copy_file("thread-unique", self._source("same.png", b"b"))
        )

        self.assertNotEqual(first, second)
        self.assertEqual(b"a", first.read_bytes())
        self.assertEqual(b"b", second.read_bytes())

    def test_clipboard_paths_are_unique_png_paths_in_thread_directory(self) -> None:
        first = Path(self.store.clipboard_path("thread-clipboard"))
        second = Path(self.store.new_clipboard_path("thread-clipboard"))
        expected_directory = (
            self.project_root / ".runtime" / "attachments" / "thread-clipboard"
        ).resolve()

        self.assertEqual(expected_directory, first.parent)
        self.assertEqual(expected_directory, second.parent)
        self.assertEqual(".png", first.suffix)
        self.assertEqual(".png", second.suffix)
        self.assertNotEqual(first, second)

    def test_rejects_unsupported_extension(self) -> None:
        source = self._source("reference.gif")
        with self.assertRaises(ValueError):
            self.store.copy_file("thread-1", source)

    def test_rejects_thread_id_path_traversal_and_unsafe_names(self) -> None:
        unsafe_ids = (
            "",
            ".",
            "..",
            "../outside",
            r"..\outside",
            "nested/thread",
            r"nested\thread",
            r"E:\outside",
            "CON",
        )
        for thread_id in unsafe_ids:
            with self.subTest(thread_id=thread_id):
                with self.assertRaises(ValueError):
                    self.store.clipboard_path(thread_id)


if __name__ == "__main__":
    unittest.main()
