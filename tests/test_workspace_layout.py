from __future__ import annotations

import importlib.util
import json
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
    / "workspace_layout.py"
)
SPEC = importlib.util.spec_from_file_location("workspace_layout", MODULE_PATH)
workspace_layout = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = workspace_layout
SPEC.loader.exec_module(workspace_layout)


class WorkspaceLayoutTests(unittest.TestCase):
    def make_state(self, project: Path, phase: str, schema: int = 3) -> Path:
        project.mkdir(parents=True)
        state = {
            "schema_version": schema,
            "project_dir": str(project),
            "phase": phase,
            "max_repairs_per_item": 1,
            "artifacts": {},
            "images": [],
            "reference_jobs": [],
            "blocking_reasons": [],
        }
        path = project / "review-state.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return path

    def test_apply_classifies_projects_and_rewrites_cross_project_paths(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        active = workspace / "活动项目"
        archived = workspace / "作品归档" / "01-完成项目"
        active_state = self.make_state(active, "repairing")
        archived_state = self.make_state(archived, "complete")
        request = active / "生成请求" / "01.json"
        request.parent.mkdir()
        request.write_text(
            json.dumps(
                {"self": str(active / "01.png"), "other": str(archived / "02.png")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tool_doc = workspace / workspace_layout.TOOLS_DIR / "helper" / "README.md"
        tool_doc.parent.mkdir(parents=True)
        tool_doc.write_text("skills/活动项目/ 只是示例，不是项目路径。\n", encoding="utf-8")
        old_skill = workspace / "oh-my-folkstoryphoto"
        old_skill.mkdir()
        (old_skill / "README.md").write_text(
            "skills/oh-my-folkstoryphoto/\n", encoding="utf-8"
        )
        (workspace / workspace_layout.ACTIVE_POINTER).write_text(
            json.dumps({"state_file": str(active_state.relative_to(workspace))}),
            encoding="utf-8",
        )

        plan = workspace_layout.build_plan(workspace)
        self.assertFalse(plan["blockers"])
        result = workspace_layout.apply_plan(plan)

        new_active = workspace / workspace_layout.ONGOING_DIR / active.name
        new_complete = workspace / workspace_layout.COMPLETED_DIR / archived.name
        self.assertTrue((new_active / "review-state.json").is_file())
        self.assertTrue((new_complete / "review-state.json").is_file())
        revised = (new_active / "生成请求" / "01.json").read_text(encoding="utf-8")
        self.assertIn(str(new_active), revised)
        self.assertIn(str(new_complete), revised)
        self.assertNotIn(str(active), revised)
        pointer = json.loads(
            (workspace / workspace_layout.ACTIVE_POINTER).read_text(encoding="utf-8")
        )
        self.assertEqual(
            pointer["state_file"],
            f"{workspace_layout.ONGOING_DIR}/活动项目/review-state.json",
        )
        self.assertTrue(Path(result["backup"]).is_dir())
        self.assertEqual(
            tool_doc.read_text(encoding="utf-8"),
            "skills/活动项目/ 只是示例，不是项目路径。\n",
        )
        moved_old_skill = (
            workspace
            / workspace_layout.TEMP_DIR
            / "01-旧版技能源码-oh-my-folkstoryphoto"
            / "README.md"
        )
        self.assertEqual(
            moved_old_skill.read_text(encoding="utf-8"),
            "skills/oh-my-folkstoryphoto/\n",
        )
        self.assertFalse(active_state.exists())
        self.assertFalse(archived_state.exists())

    def test_active_attempt_blocks_all_moves(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        project = workspace / "活动项目"
        state_path = self.make_state(project, "scene_self_review")
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["images"] = [
            {
                "status": "generating",
                "transport": {"active_attempt": {"attempt_id": "a1"}},
            }
        ]
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        plan = workspace_layout.build_plan(workspace)
        self.assertRegex(" ".join(plan["blockers"]), "活动生图锁")
        with self.assertRaises(workspace_layout.WorkspaceError):
            workspace_layout.apply_plan(plan)
        self.assertTrue(project.is_dir())

    def test_legacy_project_requires_release_and_acceptance(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        unknown = workspace / "作品归档" / "旧项目"
        unknown.mkdir(parents=True)
        plan = workspace_layout.build_plan(workspace)
        self.assertRegex(" ".join(plan["blockers"]), "无法判定")

    def test_init_project_uses_ongoing_directory_and_versions_name(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        first = workspace_layout.init_workspace_project(workspace, "山中怪谈")
        second = workspace_layout.init_workspace_project(workspace, "山中怪谈")
        self.assertEqual(Path(first["project_dir"]).name, "山中怪谈")
        self.assertEqual(Path(second["project_dir"]).name, "山中怪谈-v2")
        self.assertTrue(Path(second["realism_file"]).is_file())
        self.assertFalse((Path(second["project_dir"]) / "01-故事脚本.md").exists())
        pointer = json.loads(
            (workspace / workspace_layout.ACTIVE_POINTER).read_text(encoding="utf-8")
        )
        self.assertEqual(
            pointer["state_file"],
            f"{workspace_layout.ONGOING_DIR}/山中怪谈-v2/08-系统文件/review-state.json",
        )


if __name__ == "__main__":
    unittest.main()
