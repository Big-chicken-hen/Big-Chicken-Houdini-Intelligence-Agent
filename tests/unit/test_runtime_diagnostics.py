from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
if str(PANEL_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(PANEL_LIB_ROOT))

from hia_panel.runtime_diagnostics import RuntimeDiagnosticWriter  # noqa: E402


class RuntimeDiagnosticWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix="runtime-diagnostics-",
            dir=runtime_tmp,
        )
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name) / "project"
        self.project_root.mkdir()
        self.moment = datetime(2026, 7, 18, 5, 30, 45, tzinfo=timezone.utc)

    def writer(self) -> RuntimeDiagnosticWriter:
        return RuntimeDiagnosticWriter(
            self.project_root,
            clock=lambda: self.moment,
        )

    def test_initial_report_is_project_local_and_contains_required_fields(self) -> None:
        writer = self.writer()
        snapshot = {
            "status": "failed",
            "houdini_build": "21.0.440",
            "python_version": "3.11.7",
            "plugin_or_git_commit": "5b09bf8",
            "thread_id": "thread-example",
            "turn_id": "turn-example",
            "model": "codex-test",
            "effort": "high",
            "user_goal": "修改当前 Houdini 场景",
            "expected": "生成可编辑节点网络",
            "actual": "执行未完成",
            "stage": "execute_python",
            "tool_order": ["read_nodes", "execute_python"],
            "error_code": "HOM_FAILED",
            "error_text": "hou.OperationFailed: bad parameter",
            "traceback": "Traceback: hou.OperationFailed",
            "retries": 1,
            "recovery": "未恢复",
            "nodes": ["/obj/HIA_Result"],
            "selection": ["/obj/source"],
            "scene_revision": 9,
            "dirty": True,
            "scene_modified": True,
            "root_path": "/obj/HIA_Result",
            "manual_check": "等待真实 GUI 验收",
            "undo": "未验证",
            "attachments": [
                r"E:\private\references\front.png",
                "/private/references/side.webp",
            ],
            "reproduction": "在当前场景重试同一请求",
            "workaround": "无",
            "impact": "当前资产未完成",
            "next_step": "检查真实 HOM 错误",
            "hypotheses": ["待验证参数兼容性"],
        }
        occurrence = {
            "status": "failed",
            "stage": "execute_python",
            "tool": "execute_python",
            "error_code": "HOM_FAILED",
            "error_text": "hou.OperationFailed: bad parameter",
            "retry_count": 1,
        }

        path = Path(
            writer.record(
                "thread-example:turn-example",
                snapshot=snapshot,
                occurrence=occurrence,
                slug="hom-failure",
            )
        )

        diagnostics_root = (self.project_root / ".runtime" / "diagnostics").resolve()
        self.assertEqual(diagnostics_root, path.parent)
        self.assertRegex(path.name, r"^20260718-053045-hom-failure\.md$")
        self.assertEqual(str(path), writer.path_for("thread-example:turn-example"))

        content = path.read_text(encoding="utf-8")
        for label in (
            "时间",
            "状态",
            "Houdini build",
            "Python",
            "插件或 Git commit",
            "Thread",
            "Turn",
            "Model",
            "Effort",
            "用户目标摘要",
            "预期结果",
            "实际结果",
            "阶段",
            "工具及顺序",
            "错误代码",
            "错误文本",
            "Traceback（已脱敏）",
            "警告",
            "重试",
            "恢复情况",
            "节点",
            "当前选择",
            "场景版本",
            "未保存",
            "场景已修改",
            "根路径",
            "人工检查",
            "Undo",
            "附件",
            "复现步骤",
            "变通方法",
            "影响",
            "下一步",
            "待验证假设",
        ):
            self.assertIn(f"- {label}：", content)
        self.assertIn("front.png", content)
        self.assertIn("side.webp", content)
        self.assertNotIn(r"E:\private\references", content)
        self.assertNotIn("/private/references", content)

    def test_same_turn_appends_updates_to_one_file(self) -> None:
        writer = self.writer()
        first = writer.record(
            "thread-1:turn-1",
            snapshot={"status": "failed", "error_text": "first failure"},
            occurrence={"stage": "mcp", "error_text": "first failure"},
        )
        second = writer.record(
            "thread-1:turn-1",
            snapshot={
                "status": "failed",
                "turn_id": "turn-1",
                "model": "codex-current",
                "scene_revision": 12,
                "actual": "retry remained incomplete",
            },
            occurrence={
                "stage": "recovery",
                "error_text": "second failure",
                "manual": True,
            },
        )

        self.assertEqual(first, second)
        files = list((self.project_root / ".runtime" / "diagnostics").glob("*.md"))
        self.assertEqual([Path(first)], files)
        content = Path(first).read_text(encoding="utf-8")
        self.assertEqual(1, content.count("# Big-Chicken Houdini Intelligence Agent 问题报告"))
        self.assertEqual(1, content.count("## 更新 2"))
        self.assertIn("first failure", content)
        self.assertIn("second failure", content)
        self.assertIn("- 手动记录：是", content)
        self.assertIn("### 当前快照更新", content)
        self.assertIn("- Turn：turn-1", content)
        self.assertIn("- Model：codex-current", content)
        self.assertIn("- 场景版本：12", content)
        self.assertIn("- 实际结果：retry remained incomplete", content)

    def test_new_writer_reuses_existing_report_for_the_same_thread_and_turn(self) -> None:
        first_writer = self.writer()
        first = first_writer.record(
            "draft-key",
            snapshot={"thread_id": "thread-1", "turn_id": "尚未确认"},
            occurrence={"stage": "attachment", "error_text": "copy failed"},
        )
        first_writer.record(
            "draft-key",
            snapshot={"thread_id": "thread-1", "turn_id": "turn-1"},
            occurrence={"stage": "turn", "error_text": "turn failed"},
        )

        second_writer = self.writer()
        second = second_writer.record(
            "thread-1:turn-1",
            snapshot={"thread_id": "thread-1", "turn_id": "turn-1"},
            occurrence={"stage": "manual", "manual": True},
        )

        self.assertEqual(first, second)
        files = list((self.project_root / ".runtime" / "diagnostics").glob("*.md"))
        self.assertEqual([Path(first)], files)
        content = Path(first).read_text(encoding="utf-8")
        self.assertIn("## 更新 3", content)
        self.assertIn("- 手动记录：是", content)

    def test_different_turns_get_two_exclusively_created_files(self) -> None:
        writer = self.writer()
        first = writer.record(
            "thread-1:turn-1",
            snapshot={},
            occurrence={},
        )
        second = writer.record(
            "thread-1:turn-2",
            snapshot={},
            occurrence={},
        )

        self.assertNotEqual(first, second)
        self.assertEqual(
            2,
            len(list((self.project_root / ".runtime" / "diagnostics").glob("*.md"))),
        )
        content = Path(first).read_text(encoding="utf-8")
        self.assertIn("不可用", content)
        self.assertIn("未提供", content)

    def test_recursive_redaction_covers_credentials_bearer_and_query_values(self) -> None:
        secrets = {
            "bearer-secret",
            "access-secret",
            "authorization-secret",
            "cookie-secret",
            "api-secret",
            "password-secret",
            "plain-secret",
            "refresh-secret",
            "login-secret",
            "nested-token-secret",
            "occurrence-secret",
            "basic-secret",
            "url-password",
            "sk-proj-api-key-secret",
            "sk-proj-naked-secret",
        }
        snapshot = {
            "status": "failed",
            "error_text": (
                "Bearer bearer-secret "
                "https://loopback.invalid/api?access_token=access-secret"
                "&authorization=authorization-secret "
                "cookie=cookie-secret api_key=api-secret "
                "password=password-secret secret=plain-secret "
                "refresh_token=refresh-secret "
                "login_credential=login-secret"
                " Authorization: Basic basic-secret"
                " https://user:url-password@example.invalid/api"
                " API key: sk-proj-api-key-secret"
                " naked sk-proj-naked-secret"
            ),
            "tools": [
                {
                    "tool": "execute_python",
                    "authorization": "nested-token-secret",
                }
            ],
            "recovery": {
                "cookie": "cookie-secret",
                "nested": {"access_token": "nested-token-secret"},
            },
        }
        occurrence = {
            "stage": "bridge",
            "error_text": "Authorization: Bearer occurrence-secret",
            "recovery": {"refreshToken": "refresh-secret"},
        }

        path = Path(
            self.writer().record(
                "thread-redaction:turn-redaction",
                snapshot=snapshot,
                occurrence=occurrence,
            )
        )
        content = path.read_text(encoding="utf-8")

        for secret in secrets:
            self.assertNotIn(secret, content)
        self.assertGreaterEqual(content.count("[REDACTED]"), 8)
        self.assertNotIn("Bearer ", content)

    def test_environment_project_root_is_used_when_argument_is_omitted(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HIA_PROJECT_ROOT": str(self.project_root)},
            clear=False,
        ):
            writer = RuntimeDiagnosticWriter(clock=lambda: self.moment)
            path = Path(
                writer.record(
                    "thread-env:turn-env",
                    snapshot={"attachments": [r"C:\private\image.png"]},
                    occurrence={"stage": "manual"},
                )
            )

        self.assertEqual(
            (self.project_root / ".runtime" / "diagnostics").resolve(),
            path.parent,
        )
        content = path.read_text(encoding="utf-8")
        self.assertIn("image.png", content)
        self.assertNotIn(r"C:\private", content)


if __name__ == "__main__":
    unittest.main()
