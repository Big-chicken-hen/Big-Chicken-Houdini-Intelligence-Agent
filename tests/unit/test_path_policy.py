from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hia_core.path_policy import (  # noqa: E402
    PROJECT_ROOT,
    PathPolicyError,
    validate_project_subpath,
)


class PathPolicyTests(unittest.TestCase):
    def assert_rejected(self, raw_path: str, expected_code: str) -> PathPolicyError:
        with self.assertRaises(PathPolicyError) as raised:
            validate_project_subpath(raw_path)
        self.assertEqual(expected_code, raised.exception.code)
        json.dumps(raised.exception.to_dict())
        return raised.exception

    def test_accepts_relative_ordinary_child(self) -> None:
        result = validate_project_subpath(r"docs\design.md")
        self.assertEqual(PROJECT_ROOT / "docs" / "design.md", result)

    def test_accepts_absolute_child_case_insensitively(self) -> None:
        candidate = str(PROJECT_ROOT / "docs" / "design.md").swapcase()
        result = validate_project_subpath(candidate)
        self.assertEqual(
            candidate.casefold(),
            str(result).casefold(),
        )

    def test_rejects_empty_path(self) -> None:
        self.assert_rejected("   ", "EMPTY_PATH")

    def test_accepts_project_root_on_another_local_drive(self) -> None:
        result = validate_project_subpath(
            r"docs\design.md",
            project_root=r"C:\portable\houdini-intelligence-agent",
        )
        self.assertEqual(
            Path(r"C:\portable\houdini-intelligence-agent\docs\design.md"),
            result,
        )

    def test_rejects_appdata_case_insensitively(self) -> None:
        self.assert_rejected(r"cache\AppData\file.bin", "APPDATA_FORBIDDEN")

    def test_rejects_drive_root(self) -> None:
        self.assert_rejected(f"{PROJECT_ROOT.drive}\\", "DRIVE_ROOT")

    def test_rejects_unc_path(self) -> None:
        self.assert_rejected(r"\\server\share\asset.bgeo", "UNC_OR_DEVICE_PATH")

    def test_rejects_device_path(self) -> None:
        self.assert_rejected(
            rf"\\?\{PROJECT_ROOT}\asset.bgeo",
            "UNC_OR_DEVICE_PATH",
        )

    def test_rejects_alternate_data_stream(self) -> None:
        self.assert_rejected(r"docs\design.md:secret", "ADS_FORBIDDEN")

    def test_rejects_parent_escape(self) -> None:
        self.assert_rejected(r"..\outside.txt", "OUTSIDE_PROJECT")

    def test_rejects_absolute_outside_path(self) -> None:
        self.assert_rejected(str(PROJECT_ROOT.parent / "outside" / "file.txt"), "OUTSIDE_PROJECT")

    def test_rejects_project_root_itself(self) -> None:
        self.assert_rejected(str(PROJECT_ROOT), "PROJECT_ROOT_FORBIDDEN")

    def test_rejects_existing_reparse_component(self) -> None:
        target = PROJECT_ROOT / "linked" / "file.txt"

        def is_reparse(path: Path) -> bool:
            return str(path).casefold() == str(target.parent).casefold()

        with mock.patch("hia_core.path_policy._is_reparse_point", side_effect=is_reparse):
            self.assert_rejected(str(target), "REPARSE_POINT")

    def test_rejects_reparse_project_root(self) -> None:
        with mock.patch("hia_core.path_policy._is_reparse_point", return_value=True):
            self.assert_rejected(r"docs\design.md", "REPARSE_POINT")


if __name__ == "__main__":
    unittest.main()
