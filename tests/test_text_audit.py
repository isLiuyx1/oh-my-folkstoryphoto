from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "skills" / "oh-my-folkstoryphoto" / "scripts" / "text_audit.py"
SPEC = importlib.util.spec_from_file_location("text_audit", MODULE)
text_audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(text_audit)


class TextAuditTests(unittest.TestCase):
    def files(self, captions: list[str]) -> tuple[Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        story = root / "01-故事脚本.md"
        publication = root / "03-发布文件说明.md"
        storyboard = root / "02-AI生成分镜.md"
        story.write_text("马丁说：我先去看门。\n", encoding="utf-8")
        rows = "\n".join(f"| {index:02d} | {caption} |" for index, caption in enumerate(captions, 1))
        publication.write_text(f"| 图号 | 发布字幕 |\n|---|---|\n{rows}\n", encoding="utf-8")
        ai_rows = "\n".join(f"| {index:02d} | 证据 | {caption} |" for index, caption in enumerate(captions, 1))
        storyboard.write_text(f"| 图号 | 唯一证据 | 发布字幕 |\n|---|---|---|\n{ai_rows}\n", encoding="utf-8")
        return story, publication, storyboard

    def test_short_captions_and_cross_frame_pronouns_pass(self) -> None:
        story, publication, storyboard = self.files(["跑", "它进来了", "门关不上"])
        result = text_audit.audit_files(story, publication, storyboard)
        self.assertTrue(result["valid"])

    def test_empty_long_missing_and_desynchronized_captions_fail(self) -> None:
        story, publication, storyboard = self.files(["", "我" * 49])
        text = storyboard.read_text(encoding="utf-8").replace("我" * 49, "另一句")
        storyboard.write_text(text, encoding="utf-8")
        result = text_audit.audit_files(story, publication, storyboard)
        self.assertFalse(result["valid"])
        joined = " ".join(result["hard_errors"])
        self.assertIn("empty", joined)
        self.assertIn("exceeds", joined)
        self.assertIn("differs", joined)

    def test_user_reported_ai_patterns_raise_human_review_warnings(self) -> None:
        captions = [
            "科尔说岛上剩五名员工，仓库却每天准备六只水杯",
            "墙后每次呼气都会挤出人类音素",
        ]
        story, publication, storyboard = self.files(captions)
        result = text_audit.audit_files(story, publication, storyboard)
        warnings = " ".join(result["warnings"])
        self.assertIn("数量差", warnings)
        self.assertIn("技术词", warnings)
        self.assertIn("每次…都会", warnings)


if __name__ == "__main__":
    unittest.main()
