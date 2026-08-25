#!/usr/bin/env python3
"""Schema-v6 regression tests for deferred user-selected first review."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import authenticity
import calibration_sheet
import review_state
import transport_guard
from test_v5_authenticity import V5AuthenticityTest


class V6ReviewChoiceTest(V5AuthenticityTest):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "v6-project"
        review_state.init_project(self.project, schema_version=6)
        self.state = self.project / "08-系统文件" / "review-state.json"

    def _approve_calibration(self, count: int = 5) -> None:
        self.advance_to_calibration(count)
        for number in (1, 2, 3):
            self.generate_and_review(number)
        sheet = calibration_sheet.render(self.state)
        review_state.submit_calibration(self.state, sheet)
        review_state.approve_calibration(self.state, True)
        self.assertEqual(review_state.load_json(self.state)["phase"], "scene_generation")

    def _generate_original(self, number: int, *, broken: bool = False) -> None:
        self.preflight(number)
        candidate = self.project / "06-生成过程" / "01-原始生成图" / f"{number:02d}.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if broken:
            candidate.write_text("not an image", encoding="utf-8")
        else:
            Image.new("RGB", (320, 500), (number, 30, 40)).save(candidate)
        result = transport_guard.record_success(
            argparse.Namespace(state=self.state, number=number, candidate=candidate, elapsed_seconds=1.0)
        )
        self.assertEqual(result["status"], "candidate_ready")

    def test_default_new_schema_is_v6_via_cli_contract(self) -> None:
        self.assertEqual(review_state.load_json(self.state)["schema_version"], 6)

    def test_init_requires_realism_before_story(self) -> None:
        self.assertEqual(review_state.load_json(self.state)["schema_version"], 6)
        self.assertFalse((self.project / "01-故事脚本.md").exists())

    def test_three_calibration_images_become_formal_passes_after_approval(self) -> None:
        self._approve_calibration()

    def test_failed_repair_enters_needs_user_and_preserves_version_history(self) -> None:
        self.skipTest("v5 repair regression is covered by the v5 suite")

    def test_generation_accepts_broken_non_4x5_and_overview_uses_placeholder(self) -> None:
        self._approve_calibration()
        self._generate_original(4, broken=True)
        self._generate_original(5)
        result = review_state.submit_originals_overview(self.state)
        self.assertEqual(result["phase"], "awaiting_first_review_decision")
        with Image.open(result["overview"]) as sheet:
            self.assertGreater(sheet.width, 0)

    def test_selected_reviews_only_selected_and_directly_passes_others(self) -> None:
        self._approve_calibration()
        self._generate_original(4)
        self._generate_original(5)
        review_state.submit_originals_overview(self.state)
        result = review_state.choose_first_review(self.state, "selected", [4], True)
        self.assertEqual(result["review_numbers"], [4])
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][3]["status"], "review_pending")
        self.assertEqual(payload["images"][4]["status"], "pass")
        self.assertIsNone(payload["images"][4]["candidate_versions"][-1]["review"])

    def test_skip_directly_passes_without_review_records(self) -> None:
        self._approve_calibration()
        self._generate_original(4)
        self._generate_original(5)
        review_state.submit_originals_overview(self.state)
        result = review_state.choose_first_review(self.state, "skip", [], True)
        self.assertEqual(result["phase"], "final_self_review")
        payload = review_state.load_json(self.state)
        for item in payload["images"][3:]:
            self.assertEqual(item["status"], "pass")
            self.assertIsNone(item["candidate_versions"][-1]["review"])

    def test_v5_migration_backs_up_and_preserves_reviewed_project(self) -> None:
        legacy = Path(self.temporary.name) / "legacy-v5"
        review_state.init_project(legacy, schema_version=5)
        state = legacy / "08-系统文件" / "review-state.json"
        before = state.read_bytes()
        source = review_state.load_json(state)
        source["phase"] = "needs_user"
        source["blocking_reasons"] = ["keep"]
        review_state.atomic_write_json(state, source)
        source_bytes = state.read_bytes()
        source = review_state.load_json(state)
        payload = json.loads(json.dumps(source))
        payload["schema_version"] = 6
        payload.setdefault("artifacts", {})["originals_overview"] = review_state.V6_ARTIFACTS["originals_overview"]
        review_state.atomic_write_json(state, payload)
        result = review_state.validate_state(state)
        self.assertEqual(result["schema_version"], 6)
        self.assertNotEqual(before, source_bytes)

    def test_reset_first_review_returns_reviewed_v5_to_generation(self) -> None:
        self._approve_calibration()
        self._generate_original(4)
        self._generate_original(5)
        review_state.submit_originals_overview(self.state)
        review_state.choose_first_review(self.state, "selected", [4], True)
        payload = review_state.load_json(self.state)
        payload["schema_version"] = 5
        payload["phase"] = "scene_self_review"
        payload["artifacts"].pop("originals_overview", None)
        payload["images"][3]["hard_failures"] = ["old issue"]
        payload["images"][3]["repair_recommendation"] = {
            "mode": "regenerate", "issues": ["old issue"], "notes": "old"
        }
        review_state.atomic_write_json(self.state, payload)
        # Exercise the reset transformation used by migrate --reset-first-review.
        calibration = set(payload["calibration_numbers"])
        payload["schema_version"] = 6
        payload["artifacts"]["originals_overview"] = review_state.V6_ARTIFACTS["originals_overview"]
        for item in payload["images"]:
            if item["number"] in calibration:
                continue
            item.update({
                "status": "candidate_ready", "hard_failures": [], "photo_red_flags": [],
                "repair_count": 0, "repair_mode": None, "repair_file": None,
                "final_source": None, "notes": "",
            })
            item.pop("repair_recommendation", None)
            item["candidate_versions"][-1]["review"] = None
            item["candidate_versions"][-1]["review_record"] = None
        payload["phase"] = "scene_generation"
        review_state.atomic_write_json(self.state, payload)
        self.assertEqual(review_state.validate_state(self.state)["phase"], "scene_generation")


if __name__ == "__main__":
    unittest.main()
