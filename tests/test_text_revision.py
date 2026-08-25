from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "oh-my-folkstoryphoto" / "scripts" / "review_state.py"
sys.path.append(str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("review_state_text_revision", MODULE_PATH)
review_state = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(review_state)


class TextRevisionTests(unittest.TestCase):
    def test_caption_mask_allows_only_caption_column_changes(self) -> None:
        before = """# AI分镜\n\n| 图号 | 唯一证据 | 发布字幕 | 拍摄者 |\n|---|---|---|---|\n| 01 | 门锁 | 原字幕 | 马丁 |\n"""
        caption_only = before.replace("原字幕", "跑")
        changed_evidence = before.replace("门锁", "窗户")
        self.assertEqual(
            review_state.mask_storyboard_captions(before),
            review_state.mask_storyboard_captions(caption_only),
        )
        self.assertNotEqual(
            review_state.mask_storyboard_captions(before),
            review_state.mask_storyboard_captions(changed_evidence),
        )

    def test_tree_hashes_detect_protected_asset_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "01.png"
            image.write_bytes(b"original")
            before = review_state.tree_hashes(root)
            image.write_bytes(b"changed")
            after = review_state.tree_hashes(root)
        self.assertNotEqual(before, after)

    def test_text_revision_phases_are_explicit(self) -> None:
        self.assertIn("text_revision_self_review", review_state.PHASES)
        self.assertIn("awaiting_text_revision_approval", review_state.PHASES)
        self.assertIn(
            "text_revision_self_review",
            review_state.LEGAL_TRANSITIONS["complete"],
        )


if __name__ == "__main__":
    unittest.main()
