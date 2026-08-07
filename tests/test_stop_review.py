from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop_review.py"


class StopReviewTests(unittest.TestCase):
    def invoke(self, cwd: Path, phase: str | None, *, active: bool = False):
        if phase is not None:
            project = cwd / "project"
            project.mkdir(exist_ok=True)
            state = project / "review-state.json"
            state.write_text(json.dumps({"phase": phase}), encoding="utf-8")
            (cwd / ".oh-my-folkstoryphoto-review.json").write_text(
                json.dumps({"state_file": "project/review-state.json"}),
                encoding="utf-8",
            )
        payload = {
            "session_id": "thread-test",
            "cwd": str(cwd),
            "hook_event_name": "Stop",
            "stop_hook_active": active,
            "last_assistant_message": "done",
        }
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def test_no_active_project_does_not_interfere(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(Path(directory), None), {})

    def test_waiting_for_user_allows_stop(self):
        for phase in (
            "awaiting_story_approval",
            "awaiting_storyboard_approval",
            "awaiting_plan_approval",
            "awaiting_reference_approval",
            "awaiting_repair_approval",
            "needs_user",
            "complete",
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(self.invoke(Path(directory), phase), {})

    def test_unfinished_review_continues(self):
        for phase in ("story_self_review", "plan_self_review", "scene_self_review"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                output = self.invoke(Path(directory), phase)
                self.assertEqual(output["decision"], "block")
                self.assertIn(phase, output["reason"])

    def test_second_stop_does_not_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.invoke(Path(directory), "repairing", active=True)
        self.assertEqual(output, {})

    def test_invalid_pointer_continues_once(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            (cwd / ".oh-my-folkstoryphoto-review.json").write_text(
                "{bad json", encoding="utf-8"
            )
            output = self.invoke(cwd, None)
        self.assertEqual(output["decision"], "block")
        self.assertIn("不可读取", output["reason"])


if __name__ == "__main__":
    unittest.main()
