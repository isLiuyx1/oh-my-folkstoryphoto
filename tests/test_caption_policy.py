from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "skills"
    / "oh-my-folkstoryphoto"
    / "scripts"
    / "review_state.py"
)
sys.path.append(str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("review_state_caption_policy", MODULE_PATH)
review_state = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(review_state)


class PublicationCaptionPolicyTests(unittest.TestCase):
    def write_storyboard(self, caption: str, *, legacy: bool = False) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "02-AI生成分镜.md"
        if legacy:
            header = "| 图号 | 唯一证据 | 字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |"
            separator = "|---|---|---|---|---|---|---|---|---|---|---|"
            row = f"| 01 | 洞口出现 | {caption} | 手机 | 记录 | 门边 | 不看镜头 | 当代 | 轻微噪点 | 无 | 避免摆拍 |"
        else:
            header = "| 图号 | 唯一证据 | 画面原生文字 | 发布字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |"
            separator = "|---|---|---|---|---|---|---|---|---|---|---|---|"
            row = f"| 01 | 洞口出现 | 无 | {caption} | 手机 | 记录 | 门边 | 不看镜头 | 当代 | 轻微噪点 | 无 | 避免摆拍 |"
        path.write_text(
            "\n".join(("# AI生成分镜", "", header, separator, row)) + "\n",
            encoding="utf-8",
        )
        return path

    def test_concrete_source_style_captions_are_accepted(self) -> None:
        captions = (
            "我从小跟着外公跑山，他退休前一直负责水文勘探",
            "那天下午，一个外地人带着旧手稿来找外公",
            "工人在地基下面挖出一个测不到底的圆洞",
            "凌晨两点的监控里，有东西从封住的洞口爬了出来",
        )
        for caption in captions:
            with self.subTest(caption=caption):
                numbers, header = review_state.parse_production_storyboard(
                    self.write_storyboard(caption)
                )
                self.assertEqual(numbers, [1])
                self.assertEqual(header, review_state.PRODUCTION_STORYBOARD_COLUMNS)

    def test_short_caption_is_accepted_and_too_long_caption_is_rejected(self) -> None:
        numbers, _header = review_state.parse_production_storyboard(
            self.write_storyboard("跑")
        )
        self.assertEqual(numbers, [1])
        with self.assertRaisesRegex(review_state.StateError, "at most 48"):
            review_state.parse_production_storyboard(self.write_storyboard("我" * 49))

    def test_vague_suspense_placeholders_are_rejected(self) -> None:
        captions = (
            "情况越来越不对劲",
            "我看到了无法解释的东西",
            "接下来发生的事让我终生难忘",
            "这一幕太诡异了",
            "他们似乎隐瞒了什么",
        )
        for caption in captions:
            with self.subTest(caption=caption):
                with self.assertRaisesRegex(review_state.StateError, "publication caption is vague"):
                    review_state.parse_production_storyboard(
                        self.write_storyboard(caption)
                    )

    def test_legacy_v4_caption_remains_readable_without_new_validation(self) -> None:
        numbers, header = review_state.parse_production_storyboard(
            self.write_storyboard("短句", legacy=True)
        )
        self.assertEqual(numbers, [1])
        self.assertEqual(header, review_state.LEGACY_PRODUCTION_STORYBOARD_COLUMNS)

    def test_skill_documents_define_continuous_three_frame_test(self) -> None:
        skill = (ROOT / "skills" / "oh-my-folkstoryphoto" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        visual = (
            ROOT / "skills" / "oh-my-folkstoryphoto" / "references" / "visual-language.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "连续三图测试",
            "多数建议4–28字",
            "空洞悬念",
            "不超过48个可见字符",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill + visual)


if __name__ == "__main__":
    unittest.main()
