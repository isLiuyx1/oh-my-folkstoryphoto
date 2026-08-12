#!/usr/bin/env python3
"""Regression tests for schema-v5 capture realism and calibration gates."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = (
    HERE
    if (HERE / "review_state.py").is_file()
    else HERE.parent / "skills" / "oh-my-folkstoryphoto" / "scripts"
)
sys.path.append(str(SCRIPTS_DIR))

import authenticity
import calibration_sheet
import review_state
import transport_guard


class V5AuthenticityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "v5-project"
        review_state.init_project(self.project)
        self.state = self.project / "08-系统文件" / "review-state.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def set_phase(self, phase: str) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = phase
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)

    def fill_realism(self, *, captures: int = 1) -> None:
        path = self.project / "00-真实性方案.md"
        payload = authenticity._extract_json_block(path.read_text(encoding="utf-8"))
        main = payload["captures"][0]
        main["owner"] = "主角"
        main["story_reason"] = "主角日常记录工作，异常发生后继续保存证据"
        for index in range(1, captures):
            extra = dict(main)
            extra.update(
                {
                    "id": f"camera-{index}",
                    "role": "secondary",
                    "device_profile": "卡片数码相机",
                    "owner": f"同伴{index}",
                    "story_reason": "出发前已交代用于记录沿途资料",
                    "anchor_ids": [],
                }
            )
            payload["captures"].append(extra)
        text = "# 真实性方案\n\n```json capture-profile\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
        path.write_text(text, encoding="utf-8")

    def approve_realism_and_story(self) -> None:
        self.fill_realism()
        self.set_phase("awaiting_realism_approval")
        review_state.approve_realism(self.state, True)
        story = self.project / "01-故事脚本.md"
        story.write_text("# 故事脚本\n\n一个完整故事。\n", encoding="utf-8")
        self.set_phase("story_self_review")
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)

    def write_storyboards(self, count: int = 4, *, contradiction: bool = False) -> None:
        public = self.project / "02-专业分镜表.md"
        public.parent.mkdir(parents=True, exist_ok=True)
        public_rows = [
            "# 专业分镜表", "",
            "| 图号 | 画面拍什么 | 镜头怎么拍 | 人物在做什么 | 这张图要表达什么 |",
            "|---|---|---|---|---|",
        ]
        production = self.project / "07-制作资料" / "02-AI生成分镜.md"
        production.parent.mkdir(parents=True, exist_ok=True)
        ai_rows = [
            "# AI生成分镜", "",
            "| " + " | ".join(review_state.V5_PRODUCTION_STORYBOARD_COLUMNS) + " |",
            "|" + "---|" * len(review_state.V5_PRODUCTION_STORYBOARD_COLUMNS),
        ]
        roles = ["普通基线", "最差拍摄条件", "首次重大异常"] + ["无"] * max(0, count - 3)
        for number in range(1, count + 1):
            public_rows.append(f"| {number:02d} | 场景{number} | 普通手机受限机位 | 主角行动 | 证据{number} |")
            body = "完整DV机身" if contradiction and number == 1 else "只露拍摄者鞋尖"
            cells = [
                f"{number:02d}", f"证据{number}", "无", f"我在现场发现了第{number}项具体线索",
                "phone-main", "主角", "持续记录", "门框后方", body, "不可见",
                "不看镜头", "轻微数字锐化", "承接上一镜", roles[number - 1], "避免摆拍",
            ]
            ai_rows.append("| " + " | ".join(cells) + " |")
        public.write_text("\n".join(public_rows) + "\n", encoding="utf-8")
        production.write_text("\n".join(ai_rows) + "\n", encoding="utf-8")

    def advance_to_calibration(self, count: int = 4) -> None:
        self.approve_realism_and_story()
        self.write_storyboards(count)
        review_state.register_storyboard(self.state, count)
        self.set_phase("awaiting_storyboard_approval")
        review_state.approve_storyboard(self.state, True)
        self.set_phase("awaiting_reference_approval")
        review_state.approve_references(self.state, True)

    def preflight(self, number: int, prompt: str = "竖版4:5，门框后普通手机记录主角翻找纸箱，顶灯过曝。") -> dict:
        path = self.project / f"prompt-{number}.txt"
        path.write_text(prompt, encoding="utf-8")
        return transport_guard.preflight(
            argparse.Namespace(
                state=self.state, number=number, backend=transport_guard.BUILT_IN_BACKEND,
                route=None, model=None, prompt_file=path, reference=[], reference_role=[],
                reference_kind=[], capture_id="phone-main", device_visibility="不可见",
                repair_mode=None, allow_high_reference_count=False,
            )
        )

    def generate_and_review(self, number: int, *, fail: str | None = None) -> Path:
        self.preflight(number)
        candidate = self.project / "06-生成过程" / "01-原始生成图" / f"{number:02d}.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 500), (30 + number, 40, 50)).save(candidate)
        transport_guard.record_success(
            argparse.Namespace(state=self.state, number=number, candidate=candidate, elapsed_seconds=1.0)
        )
        checks = {name: "pass" for name in authenticity.REQUIRED_CHECKS}
        checks["identity_match"] = "na"
        checks["key_prop_match"] = "na"
        if fail:
            checks[fail] = "fail"
        review = self.project / "08-系统文件" / "03-真实性审查" / f"{number:02d}.json"
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(
            json.dumps({"checks": checks, "red_flags": [], "notes": "逐项检查完成"}, ensure_ascii=False),
            encoding="utf-8",
        )
        review_state.mark_pass(self.state, number, "ignored", [], review)
        return candidate

    def test_init_requires_realism_before_story(self) -> None:
        self.assertEqual(sorted(path.name for path in self.project.iterdir()), ["00-真实性方案.md", "08-系统文件"])
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["schema_version"], 5)
        self.assertEqual(payload["phase"], "realism_self_review")
        self.assertFalse((self.project / "01-故事脚本.md").exists())

    def test_realism_rejects_more_than_primary_plus_two_secondary(self) -> None:
        self.fill_realism(captures=4)
        with self.assertRaisesRegex(authenticity.AuthenticityError, "不得超过3"):
            authenticity.parse_realism_plan(self.project / "00-真实性方案.md")

    def test_invalid_realism_cannot_enter_user_approval_gate(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "awaiting_realism_approval"
        review_state.atomic_write_json(self.state, payload)
        with self.assertRaisesRegex(review_state.StateError, "必须填写完整"):
            review_state.validate_state(self.state)

    def test_failed_transition_can_be_rolled_back_without_leaving_invalid_phase(self) -> None:
        payload = review_state.load_json(self.state)
        prior = json.loads(json.dumps(payload))
        payload["phase"] = "awaiting_realism_approval"
        review_state.atomic_write_json(self.state, payload)
        try:
            with self.assertRaises(review_state.StateError):
                review_state.validate_state(self.state)
        finally:
            review_state.atomic_write_json(self.state, prior)
        self.assertEqual(review_state.load_json(self.state)["phase"], "realism_self_review")

    def test_storyboard_rejects_first_person_device_body_contradiction(self) -> None:
        self.approve_realism_and_story()
        self.write_storyboards(4, contradiction=True)
        with self.assertRaisesRegex(review_state.StateError, "完整机身"):
            review_state.register_storyboard(self.state, 4)

    def test_prompt_budget_and_aesthetic_terms_are_hard_failures(self) -> None:
        self.advance_to_calibration()
        with self.assertRaisesRegex(review_state.StateError, "美学诱导词"):
            self.preflight(1, "竖版4:5，电影感英雄机位记录异常。")
        with self.assertRaisesRegex(review_state.StateError, "超过预算"):
            self.preflight(1, "竖版4:5，" + "普通记录" * 80)

    def test_only_registered_calibration_images_can_generate(self) -> None:
        self.advance_to_calibration(4)
        with self.assertRaisesRegex(review_state.StateError, "only the three"):
            self.preflight(4)

    def test_preflight_persists_authored_prompt_and_injects_fixed_safety(self) -> None:
        self.advance_to_calibration()
        authored = "竖版4:5，门框后记录主角核对纸箱，顶灯局部过曝。"
        self.preflight(1, authored)
        request = review_state.load_json(self.project / "08-系统文件" / "01-生成请求" / "01.json")
        self.assertEqual(request["authored_prompt"], authored)
        self.assertEqual(request["capture_id"], "phone-main")
        self.assertEqual(request["device_visibility"], "不可见")
        self.assertIn(authenticity.ANTI_CINEMATIC_CLAUSE, request["prompt"])
        self.assertNotEqual(request["prompt"], authored)

    def test_single_cinematic_failure_blocks_pass(self) -> None:
        self.advance_to_calibration()
        self.generate_and_review(1, fail="not_cinematic")
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertEqual(item["status"], "review_pending")
        self.assertIn("电影剧照", item["hard_failures"][0])
        self.assertIsNone(item["final_source"])

    def test_three_calibration_images_become_formal_passes_after_approval(self) -> None:
        self.advance_to_calibration()
        for number in (1, 2, 3):
            self.generate_and_review(number)
        sheet = calibration_sheet.render(self.state)
        self.assertTrue(sheet.is_file())
        with Image.open(sheet) as rendered:
            self.assertEqual(rendered.size, (1500, 1680))
        review_state.submit_calibration(self.state, sheet)
        review_state.approve_calibration(self.state, True)
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["phase"], "scene_self_review")
        self.assertEqual([item["status"] for item in payload["images"][:3]], ["pass", "pass", "pass"])
        self.assertIn("calibration", payload["approvals"])

    def test_failed_repair_enters_needs_user_and_preserves_version_history(self) -> None:
        self.advance_to_calibration()
        for number in (1, 2, 3):
            self.generate_and_review(number)
        sheet = self.project / "06-生成过程" / "00-真实性校准" / "真实性校准联系表.jpg"
        sheet.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 500), (80, 80, 80)).save(sheet)
        review_state.submit_calibration(self.state, sheet)
        review_state.approve_calibration(self.state, True)

        self.generate_and_review(4, fail="not_cinematic")
        review_state.queue_repair(self.state, 4, "regenerate", ["电影化"], "整图重生", [])
        review_state.prepare_repair_report(self.state, None)
        review_state.authorize_repairs(self.state, [4], True)
        prompt = self.project / "prompt-4.txt"
        transport_guard.preflight(
            argparse.Namespace(
                state=self.state, number=4, backend=transport_guard.BUILT_IN_BACKEND,
                route=None, model=None, prompt_file=prompt, reference=[], reference_role=[],
                reference_kind=[], capture_id="phone-main", device_visibility="不可见",
                repair_mode="regenerate", allow_high_reference_count=False,
            )
        )
        repaired = self.project / "06-生成过程" / "02-返修记录" / "04-v2.png"
        repaired.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 500), (90, 20, 20)).save(repaired)
        transport_guard.record_success(
            argparse.Namespace(state=self.state, number=4, candidate=repaired, elapsed_seconds=1.0)
        )
        checks = {name: "pass" for name in authenticity.REQUIRED_CHECKS}
        checks["identity_match"] = "na"
        checks["key_prop_match"] = "na"
        checks["not_cinematic"] = "fail"
        review = self.project / "08-系统文件" / "03-真实性审查" / "04-v2.json"
        review.write_text(
            json.dumps({"checks": checks, "red_flags": [], "notes": "返修仍像电影剧照"}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = review_state.mark_pass(self.state, 4, "ignored", [], review)
        payload = review_state.load_json(self.state)
        self.assertEqual(result["status"], "needs_user")
        self.assertEqual(payload["phase"], "needs_user")
        self.assertEqual(len(payload["images"][3]["candidate_versions"]), 2)
        self.assertTrue(payload["images"][3]["candidate_versions"][0]["review"])
        self.assertTrue(payload["images"][3]["candidate_versions"][1]["review"])


if __name__ == "__main__":
    unittest.main()
