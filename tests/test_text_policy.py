from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "oh-my-folkstoryphoto"


class SceneNativeTextPolicyTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (SKILL / relative).read_text(encoding="utf-8")

    def test_prompt_template_allows_native_text_without_blanket_bans(self) -> None:
        visual = self.read("references/visual-language.md")
        self.assertNotIn("unlettered", visual)
        self.assertNotIn("Avoid: text", visual)
        self.assertNotIn("readable documents", visual)
        self.assertIn("Scene-native text", visual)
        self.assertIn("关键文字必须逐字核对", visual)
        self.assertIn("非关键环境文字可以自然出现", visual)

    def test_overlay_and_brand_policies_remain_explicit(self) -> None:
        visual = self.read("references/visual-language.md")
        skill = self.read("SKILL.md")
        for phrase in (
            "禁止发布字幕、标题、水印、平台UI浮层和假相机HUD",
            "真实品牌与Logo替换为虚构品牌",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)

    def test_review_policy_covers_required_text_and_forbidden_overlays(self) -> None:
        checklist = self.read("references/quality-checklist.md")
        for phrase in (
            "关键场景原生文字缺失、错字",
            "清晰可读的无意义乱码",
            "后期字幕",
            "平台 UI 浮层",
            "假相机 HUD",
            "真实品牌名或 Logo",
            "虚构品牌及场景内设备屏幕 UI 不因此失败",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, checklist)

    def test_publication_caption_is_separate_from_native_text(self) -> None:
        output = self.read("references/output-spec.md")
        self.assertIn("| 图号 | 唯一证据 | 画面原生文字 | 发布字幕 |", output)
        visual = self.read("references/visual-language.md")
        self.assertIn("发布字幕只进入发布说明，不进入生图提示词", visual)
        self.assertIn("发布字幕与场景原生文字是两个独立字段", visual)
        skill = self.read("SKILL.md")
        self.assertIn("无发布字幕叠图", skill)
        self.assertNotIn("4:5 无字图片", skill)


if __name__ == "__main__":
    unittest.main()
