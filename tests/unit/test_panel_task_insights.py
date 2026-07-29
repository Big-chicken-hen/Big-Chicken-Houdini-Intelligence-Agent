from __future__ import annotations

import unittest

from houdini_package.python_libs.hia_panel.task_insights import (
    bounded_public_text,
    normalize_build_brief,
    normalize_context_pack_summary,
    normalize_reviews,
    normalize_stage_plan,
)


class PanelTaskInsightTests(unittest.TestCase):
    def test_build_brief_keeps_only_explicit_public_fields(self) -> None:
        brief = normalize_build_brief(
            {
                "title": "木屋短蓝图",
                "summary": "先结构后材质",
                "constraints": ["保持可编辑", {"raw": "ignored"}],
                "outputs": ["当前场景节点网络"],
                "reasoning": "不得显示",
            }
        )

        self.assertEqual("木屋短蓝图", brief["title"])
        self.assertEqual(("保持可编辑",), brief["constraints"])
        self.assertEqual(("当前场景节点网络",), brief["deliverables"])
        self.assertNotIn("reasoning", brief)
        self.assertIsNone(normalize_build_brief({"reasoning": "only hidden"}))

    def test_stage_and_review_models_are_bounded_and_skip_raw_payloads(self) -> None:
        stages = normalize_stage_plan(
            [
                {
                    "step": "建立主体",
                    "status": "inProgress",
                    "details": "公开阶段说明",
                    "raw": {"huge": "ignored"},
                },
                {"status": "pending"},
            ]
        )
        reviews = normalize_reviews(
            {
                "domain": "spatial",
                "severity": "warning",
                "path": "/obj/HIA_Result",
                "evidence": "有明显穿插",
                "suggested_next_action": "调整受影响区域",
                "reasoning": "不得显示",
            }
        )

        self.assertEqual(
            [
                {
                    "title": "建立主体",
                    "status": "inProgress",
                    "summary": "公开阶段说明",
                }
            ],
            stages,
        )
        self.assertEqual("spatial", reviews[0]["domain"])
        self.assertEqual("/obj/HIA_Result", reviews[0]["object_path"])
        self.assertNotIn("reasoning", reviews[0])

    def test_context_pack_is_metadata_only_and_text_is_display_safe(self) -> None:
        summary = normalize_context_pack_summary(
            {
                "title": "本地资料",
                "source": "项目知识",
                "itemCount": 4,
                "chunks": ["不得带入 UI"],
            }
        )

        self.assertEqual(
            {"title": "本地资料", "source": "项目知识", "count": 4},
            summary,
        )
        self.assertTrue(
            bounded_public_text("a\x00b-more", 3).startswith("a b…")
        )
        secret_text = bounded_public_text(
            "Bearer token-value Cookie=session-value api_key=api-value",
            200,
        )
        self.assertIn("[REDACTED]", secret_text)
        self.assertNotIn("token-value", secret_text)
        self.assertNotIn("session-value", secret_text)
        self.assertNotIn("api-value", secret_text)
        self.assertIsNone(normalize_context_pack_summary({"chunks": ["only"]}))

    def test_public_models_enforce_small_display_bounds(self) -> None:
        brief = normalize_build_brief({"summary": "x" * 5_000})
        stages = normalize_stage_plan(
            [
                {"step": f"阶段 {index}", "status": "pending"}
                for index in range(40)
            ]
        )
        reviews = normalize_reviews(
            [
                {"domain": f"domain-{index}", "evidence": "e" * 2_000}
                for index in range(30)
            ]
        )

        self.assertLess(len(brief["summary"]), 2_050)
        self.assertEqual(32, len(stages))
        self.assertEqual(24, len(reviews))
        self.assertLess(len(reviews[0]["evidence"]), 1_250)


if __name__ == "__main__":
    unittest.main()
