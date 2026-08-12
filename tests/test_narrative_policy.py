from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "oh-my-folkstoryphoto"


class LongFormNarrativePolicyTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (SKILL / relative).read_text(encoding="utf-8")

    def test_default_range_is_30_to_39_everywhere(self) -> None:
        documents = [
            ROOT / "README.md",
            SKILL / "SKILL.md",
            SKILL / "references" / "workflow.md",
            SKILL / "references" / "visual-language.md",
            SKILL / "references" / "quality-checklist.md",
        ]
        for document in documents:
            content = document.read_text(encoding="utf-8")
            with self.subTest(document=document.name):
                self.assertIn("30–39", content)
                self.assertNotRegex(content, r"24[–-]27")

    def test_workflow_contains_all_eight_beats_and_density_guards(self) -> None:
        workflow = self.read("references/workflow.md")
        for beat in (
            "人物锚点",
            "可信来源",
            "异常入口",
            "现实验证",
            "交叉印证",
            "阻力门槛",
            "亲历升级",
            "证据余波",
        ):
            with self.subTest(beat=beat):
                self.assertIn(beat, workflow)
        self.assertIn("每 3–5 图必须出现一次", workflow)
        self.assertIn("删图测试", workflow)
        self.assertIn("至少四类证据载体", workflow)

    def test_caption_provenance_and_visual_guards_are_explicit(self) -> None:
        visual = self.read("references/visual-language.md")
        checklist = self.read("references/quality-checklist.md")
        for phrase in (
            "建议 12–36 个汉字、硬范围 8–48 个可见字符",
            "不机械复述画面",
            "形成者、时间、获取途径和保留原因",
            "不得以横图加上下黑边",
            "电影剧照、游戏画面或专业恐怖海报",
            "不得只以贴脸惊吓结束",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, visual)
        self.assertIn("最后 2–4 图包含结果或余波", checklist)

    def test_reference_study_separates_learning_from_copying(self) -> None:
        workflow = self.read("references/workflow.md")
        self.assertIn("参考作品拆解", workflow)
        self.assertIn("只转换抽象方法", workflow)
        self.assertIn("不得复刻参考作品的角色、地点、道具、冲突或怪物设计", workflow)

    def test_narrative_failure_fixtures_cover_reference_weaknesses(self) -> None:
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "narrative-cases.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(fixture["traceable_evidence_relay"]["expected"], "pass")
        for case in (
            "letterboxed_surveillance",
            "repeated_hole_views",
            "untraceable_cctv",
            "cinematic_drift",
            "jump_scare_only_ending",
        ):
            with self.subTest(case=case):
                self.assertEqual(fixture[case]["expected"], "fail")
                self.assertTrue(fixture[case]["failures"])


if __name__ == "__main__":
    unittest.main()
