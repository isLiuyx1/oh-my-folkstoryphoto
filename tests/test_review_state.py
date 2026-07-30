from __future__ import annotations

import importlib.util
import json
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
SPEC = importlib.util.spec_from_file_location("review_state", MODULE_PATH)
review_state = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(review_state)


class ReviewStateTests(unittest.TestCase):
    def make_project(self, phase: str = "final_self_review"):
        temporary = tempfile.TemporaryDirectory()
        project = Path(temporary.name)
        (project / "原始生成图").mkdir()
        (project / "原始生成图" / "01.png").write_bytes(b"image-one")
        (project / "原始生成图" / "02.png").write_bytes(b"image-two")
        (project / "自审记录.md").write_text("# review\n", encoding="utf-8")
        (project / "验收记录.md").write_text("# acceptance\n", encoding="utf-8")
        manifest = {
            "images": [
                {"number": 1, "source": "原始生成图/01.png"},
                {"number": 2, "source": "原始生成图/02.png"},
            ]
        }
        (project / "release-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        state = {
            "schema_version": 1,
            "project_dir": ".",
            "phase": phase,
            "max_repairs_per_item": 1,
            "artifacts": {
                "self_review": "自审记录.md",
                "acceptance": "验收记录.md",
                "release_manifest": "release-manifest.json",
            },
            "images": [
                {
                    "number": 1,
                    "candidate": "原始生成图/01.png",
                    "verdict": "pass",
                    "hard_failures": [],
                    "photo_red_flags": ["commercial_sharpness"],
                    "repair_count": 0,
                    "repair_mode": None,
                    "repair_file": None,
                    "final_source": "原始生成图/01.png",
                    "notes": "",
                },
                {
                    "number": 2,
                    "candidate": "原始生成图/02.png",
                    "verdict": "pass",
                    "hard_failures": [],
                    "photo_red_flags": [],
                    "repair_count": 0,
                    "repair_mode": None,
                    "repair_file": None,
                    "final_source": "原始生成图/02.png",
                    "notes": "",
                },
            ],
            "blocking_reasons": [],
        }
        state_path = project / "review-state.json"
        state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        return temporary, project, state_path, state

    def test_valid_final_review(self):
        temporary, _project, state_path, _state = self.make_project()
        self.addCleanup(temporary.cleanup)
        summary = review_state.validate_state(state_path)
        self.assertEqual(summary["passing_count"], 2)
        self.assertEqual(summary["phase"], "final_self_review")

    def test_complete_requires_all_artifacts(self):
        temporary, project, state_path, _state = self.make_project("complete")
        self.addCleanup(temporary.cleanup)
        (project / "验收记录.md").unlink()
        with self.assertRaisesRegex(review_state.StateError, "acceptance"):
            review_state.validate_state(state_path)

    def test_three_red_flags_cannot_pass(self):
        temporary, _project, state_path, state = self.make_project()
        self.addCleanup(temporary.cleanup)
        state["images"][0]["photo_red_flags"] = [
            "centered_subject",
            "direct_gaze",
            "clean_edges",
        ]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "three or more"):
            review_state.validate_state(state_path)

    def test_failed_image_cannot_enter_manifest(self):
        temporary, _project, state_path, state = self.make_project("repairing")
        self.addCleanup(temporary.cleanup)
        state["images"][0]["verdict"] = "fail"
        state["images"][0]["final_source"] = None
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "not marked pass"):
            review_state.validate_state(state_path)

    def test_repair_budget_is_exactly_one(self):
        temporary, _project, state_path, state = self.make_project()
        self.addCleanup(temporary.cleanup)
        state["images"][0]["repair_count"] = 2
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "must be 0 or 1"):
            review_state.validate_state(state_path)

    def test_needs_user_requires_reason(self):
        temporary, project, state_path, state = self.make_project("needs_user")
        self.addCleanup(temporary.cleanup)
        (project / "release-manifest.json").unlink()
        state["images"][0]["verdict"] = "needs_user"
        state["images"][0]["repair_count"] = 1
        state["images"][0]["repair_mode"] = "regenerate"
        state["images"][0]["final_source"] = None
        repair = Path(temporary.name) / "原始生成图" / "01-v2.png"
        repair.write_bytes(b"repair")
        state["images"][0]["repair_file"] = "原始生成图/01-v2.png"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "blocking reason"):
            review_state.validate_state(state_path)

    def test_practice_fixture_encodes_editorial_failure(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "authenticity-cases.json").read_text(
                encoding="utf-8"
            )
        )
        old = fixture["old_editorial_portrait"]
        candid = fixture["candid_doorway_snapshot"]
        self.assertGreaterEqual(len(old["photo_red_flags"]), 3)
        self.assertLess(len(candid["photo_red_flags"]), 3)
        self.assertEqual(old["expected"], "fail")
        self.assertEqual(candid["expected"], "pass")

    def test_all_declared_transitions_are_legal(self):
        for current, targets in review_state.LEGAL_TRANSITIONS.items():
            for target in targets:
                approved = (current, target) in review_state.APPROVAL_TRANSITIONS
                with self.subTest(current=current, target=target):
                    review_state.validate_transition(
                        current, target, user_approved=approved
                    )

    def test_undeclared_transition_is_rejected(self):
        with self.assertRaisesRegex(review_state.StateError, "illegal"):
            review_state.validate_transition("drafting", "complete")

    def test_approval_gate_requires_explicit_flag(self):
        with self.assertRaisesRegex(review_state.StateError, "user-approved"):
            review_state.validate_transition(
                "awaiting_plan_approval", "reference_self_review"
            )

    def test_transition_command_updates_atomically(self):
        temporary, _project, state_path, state = self.make_project(
            "awaiting_plan_approval"
        )
        self.addCleanup(temporary.cleanup)
        state["artifacts"]["release_manifest"] = "not-created-yet.json"
        state["images"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result = review_state.validate_state(state_path)
        self.assertEqual(result["phase"], "awaiting_plan_approval")
        review_state.validate_transition(
            "awaiting_plan_approval",
            "reference_self_review",
            user_approved=True,
        )
        payload = review_state.load_json(state_path)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(state_path, payload)
        self.assertEqual(
            review_state.validate_state(state_path)["phase"],
            "reference_self_review",
        )


if __name__ == "__main__":
    unittest.main()
