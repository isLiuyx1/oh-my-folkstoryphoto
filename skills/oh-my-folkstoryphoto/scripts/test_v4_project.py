#!/usr/bin/env python3
"""Regression tests for the schema-v4 user-facing project workflow."""

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

import package_release
import review_state
import transport_guard


class V4ProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "story-project"
        review_state.init_project(self.project, schema_version=4)
        self.state = self.project / "08-系统文件" / "review-state.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_story(self) -> None:
        (self.project / "01-故事脚本.md").write_text(
            "# 故事脚本\n\n一个完整而可确认的故事。\n", encoding="utf-8"
        )

    def set_phase(self, phase: str) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = phase
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)

    def write_storyboards(self, count: int = 2, *, legacy_production: bool = False) -> None:
        public = self.project / "02-专业分镜表.md"
        rows = [
            "# 专业分镜表",
            "",
            "| 图号 | 画面拍什么 | 镜头怎么拍 | 人物在做什么 | 这张图要表达什么 |",
            "|---|---|---|---|---|",
        ]
        production = self.project / "07-制作资料" / "02-AI生成分镜.md"
        production.parent.mkdir(parents=True, exist_ok=True)
        if legacy_production:
            ai_rows = [
                "# AI生成分镜",
                "",
                "| 图号 | 唯一证据 | 字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        else:
            ai_rows = [
                "# AI生成分镜",
                "",
                "| 图号 | 唯一证据 | 画面原生文字 | 发布字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        for number in range(1, count + 1):
            rows.append(
                f"| {number:02d} | 场景{number} | 普通手机中景 | 主角继续行动 | 推进信息{number} |"
            )
            if legacy_production:
                ai_rows.append(
                    f"| {number:02d} | 证据{number} | 字幕{number} | 手机 | 记录 | 门边 | 不看镜头 | 当代 | 轻微噪点 | 上一镜 | 避免摆拍 |"
                )
            else:
                ai_rows.append(
                    f"| {number:02d} | 证据{number} | 无 | 我在现场发现了第{number}项具体线索 | 手机 | 记录 | 门边 | 不看镜头 | 当代 | 轻微噪点 | 上一镜 | 避免摆拍 |"
                )
        public.write_text("\n".join(rows) + "\n", encoding="utf-8")
        production.write_text("\n".join(ai_rows) + "\n", encoding="utf-8")

    def advance_to_scene(self, count: int = 2) -> None:
        self.write_story()
        self.set_phase("story_self_review")
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)
        self.write_storyboards(count)
        review_state.register_storyboard(self.state, count)
        self.set_phase("awaiting_storyboard_approval")
        review_state.approve_storyboard(self.state, True)
        self.set_phase("awaiting_reference_approval")
        review_state.approve_references(self.state, True)

    def assert_long_form_registration(self, count: int) -> None:
        self.write_story()
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)
        self.write_storyboards(count)
        result = review_state.register_storyboard(self.state, count)
        payload = review_state.load_json(self.state)
        self.assertEqual(result["planned_count"], count)
        self.assertEqual(payload["planned_count"], count)
        self.assertEqual(len(payload["images"]), count)
        self.assertEqual(payload["images"][-1]["number"], count)
        self.assertEqual(payload["artifacts"]["release_dir"], f"04-最终发布版-{count}图")

    def test_init_creates_only_current_user_file_and_system_state(self) -> None:
        self.assertEqual(
            sorted(path.name for path in self.project.iterdir()),
            ["01-故事脚本.md", "08-系统文件"],
        )
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["schema_version"], 4)
        self.assertIsNone(payload["planned_count"])
        self.assertEqual(payload["images"], [])
        self.assertEqual(review_state.validate_state(self.state)["project_dir"], str(self.project.resolve()))

    def test_three_approvals_hash_user_files_and_create_tasks_late(self) -> None:
        self.advance_to_scene(2)
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["phase"], "scene_self_review")
        self.assertEqual(payload["planned_count"], 2)
        self.assertEqual([item["number"] for item in payload["images"]], [1, 2])
        self.assertIn("story", payload["approvals"])
        self.assertIn("storyboard", payload["approvals"])
        self.assertIn("references", payload["approvals"])
        self.assertEqual(payload["artifacts"]["release_dir"], "04-最终发布版-2图")

    def test_30_frame_long_form_storyboard_registers_contiguously(self) -> None:
        self.assert_long_form_registration(30)

    def test_39_frame_long_form_storyboard_registers_contiguously(self) -> None:
        self.assert_long_form_registration(39)

    def test_public_storyboard_rejects_internal_or_extra_columns(self) -> None:
        self.write_story()
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)
        self.write_storyboards(1)
        public = self.project / "02-专业分镜表.md"
        public.write_text(
            public.read_text(encoding="utf-8").replace(
                "这张图要表达什么 |", "这张图要表达什么 | 字幕 |"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(review_state.StateError, "columns must be exactly"):
            review_state.register_storyboard(self.state, 1)

    def test_preferred_production_storyboard_separates_native_text_and_caption(self) -> None:
        self.write_storyboards(1)
        production = self.project / "07-制作资料" / "02-AI生成分镜.md"
        numbers, header = review_state.parse_production_storyboard(production)
        self.assertEqual(numbers, [1])
        self.assertEqual(header, review_state.PRODUCTION_STORYBOARD_COLUMNS)
        self.assertIn("画面原生文字", header)
        self.assertIn("发布字幕", header)

    def test_legacy_production_storyboard_remains_compatible(self) -> None:
        self.write_story()
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)
        self.write_storyboards(1, legacy_production=True)
        result = review_state.register_storyboard(self.state, 1)
        self.assertEqual(result["planned_count"], 1)

    def test_production_storyboard_rejects_unknown_or_incomplete_columns(self) -> None:
        self.write_story()
        self.set_phase("awaiting_story_approval")
        review_state.approve_story(self.state, True)
        self.write_storyboards(1)
        production = self.project / "07-制作资料" / "02-AI生成分镜.md"
        original = production.read_text(encoding="utf-8")
        production.write_text(
            original.replace("真实性风险 |", "真实性风险 | 未知字段 |", 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(review_state.StateError, "preferred v4.2 or legacy v4"):
            review_state.register_storyboard(self.state, 1)

        self.write_storyboards(1)
        production.write_text(
            production.read_text(encoding="utf-8").replace(
                "| 01 | 证据1 | 无 | 我在现场发现了第1项具体线索 |", "| 01 | 证据1 | | 我在现场发现了第1项具体线索 |"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(review_state.StateError, "empty production field"):
            review_state.register_storyboard(self.state, 1)

    def test_editing_approved_story_blocks_work_until_gate_reopens(self) -> None:
        self.advance_to_scene(1)
        story = self.project / "01-故事脚本.md"
        story.write_text(story.read_text(encoding="utf-8") + "修改。\n", encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "approved story file changed"):
            review_state.validate_state(self.state)
        result = review_state.reopen_gate(self.state, "story")
        self.assertEqual(result["phase"], "story_self_review")
        payload = review_state.load_json(self.state)
        self.assertIsNone(payload["planned_count"])
        self.assertEqual(payload["images"], [])
        self.assertTrue(Path(result["backup"]).is_file())

    def test_v4_transport_snapshots_use_numbered_system_directory(self) -> None:
        self.advance_to_scene(1)
        prompt = self.project / "prompt.txt"
        prompt.write_text("vertical 4:5 ordinary documentary frame", encoding="utf-8")
        result = transport_guard.preflight(
            argparse.Namespace(
                state=self.state,
                number=1,
                backend=transport_guard.BUILT_IN_BACKEND,
                route=None,
                model=None,
                prompt_file=prompt,
                reference=[],
                reference_role=[],
                repair_mode=None,
            )
        )
        request = self.project / "08-系统文件" / "01-生成请求" / "01.json"
        self.assertTrue(request.is_file())
        self.assertEqual(Path(result["request_file"]), request.resolve())
        self.assertFalse((self.project / "生成请求").exists())

    def test_v4_release_manifest_resolves_sources_from_project_root(self) -> None:
        self.advance_to_scene(1)
        source = self.project / "06-生成过程" / "01-原始生成图" / "01.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), (10, 20, 30)).save(source)
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        item.update(
            {
                "status": "pass",
                "candidate": str(source.relative_to(self.project)),
                "final_source": str(source.relative_to(self.project)),
            }
        )
        payload["phase"] = "final_self_review"
        for key in ("self_review", "acceptance"):
            path = self.project / payload["artifacts"][key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("ok", encoding="utf-8")
        manifest = self.project / payload["artifacts"]["release_manifest"]
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"images": [{"number": 1, "source": str(source.relative_to(self.project))}]}),
            encoding="utf-8",
        )
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)
        project_dir, _ = package_release.verify_state_for_packaging(self.state, manifest)
        loaded = package_release.load_manifest(manifest, project_dir)
        self.assertEqual(loaded[0]["source"], source.resolve())

    def test_v4_reports_default_to_numbered_production_directory(self) -> None:
        self.advance_to_scene(1)
        candidate = self.project / "06-生成过程" / "01-原始生成图" / "01.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), (10, 20, 30)).save(candidate)
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        item["candidate"] = str(candidate.relative_to(self.project))
        item["status"] = "review_pending"
        item["repair_recommendation"] = {
            "mode": "regenerate",
            "issues": ["人物直视镜头"],
            "notes": "改变拍摄行为",
        }
        payload["repair_policy"] = {
            "mode": "deferred_user_approved",
            "report_file": None,
            "report_generated_at": None,
            "approved_numbers": [],
            "approved_at": None,
        }
        review_state.atomic_write_json(self.state, payload)
        result = review_state.prepare_repair_report(self.state, None)
        expected = self.project / "07-制作资料" / "06-审查报告" / "02-返修报告.md"
        self.assertEqual(Path(result["report_file"]), expected.resolve())
        self.assertTrue(expected.is_file())
        self.assertFalse((self.project / "返修报告.md").exists())

    def test_v4_blocked_report_defaults_to_numbered_production_directory(self) -> None:
        self.advance_to_scene(1)
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        item["status"] = "transport_blocked"
        item["transport"].update(
            {
                "circuit_open": True,
                "consecutive_failures": 3,
                "last_error": "no candidate",
                "last_error_type": "no_candidate",
            }
        )
        review_state.atomic_write_json(self.state, payload)
        result = transport_guard.prepare_blocked_report(
            argparse.Namespace(state=self.state, output=None)
        )
        expected = self.project / "07-制作资料" / "06-审查报告" / "03-生成阻塞报告.md"
        self.assertEqual(Path(result["output"]), expected.resolve())
        self.assertTrue(expected.is_file())
        self.assertFalse((self.project / "生成阻塞报告.md").exists())


if __name__ == "__main__":
    unittest.main()
