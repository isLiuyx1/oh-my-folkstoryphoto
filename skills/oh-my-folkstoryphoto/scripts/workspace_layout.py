#!/usr/bin/env python3
"""Create and safely organize a numbered folk-story production workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_state


ONGOING_DIR = "01-进行中项目"
COMPLETED_DIR = "02-已完成作品"
SHOWCASE_DIR = "03-代表作品"
PLANNING_DIR = "04-创作管理"
REFERENCE_DIR = "05-参考素材"
GUIDES_DIR = "06-规范与模板"
TOOLS_DIR = "07-技能与工具"
TEMP_DIR = "08-临时文件"
NUMBERED_DIRS = (
    ONGOING_DIR,
    COMPLETED_DIR,
    SHOWCASE_DIR,
    PLANNING_DIR,
    REFERENCE_DIR,
    GUIDES_DIR,
    TOOLS_DIR,
    TEMP_DIR,
)
ACTIVE_POINTER = ".oh-my-folkstoryphoto-review.json"
BACKUP_SUBDIR = Path(TEMP_DIR) / "02-整理备份"
IGNORED_PARTS = {
    ".git",
    ".ruff_cache",
    "__pycache__",
    ".workspace-organizer-backups",
    "oh-my-folkstoryphoto",
}
TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".log",
    ".csv",
    ".tsv",
    ".html",
    ".sh",
}


class WorkspaceError(ValueError):
    """Raised when workspace organization cannot be performed safely."""


@dataclass(frozen=True)
class Move:
    source: Path
    destination: Path
    kind: str
    phase: str | None = None


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(state_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"无法读取状态文件 {state_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkspaceError(f"状态文件必须是 JSON 对象: {state_path}")
    return payload


def state_project_root(state_path: Path, payload: dict[str, Any]) -> Path:
    raw = payload.get("project_dir")
    if not isinstance(raw, str) or not raw.strip():
        raise WorkspaceError(f"project_dir 无效: {state_path}")
    project = Path(raw).expanduser()
    if not project.is_absolute():
        project = state_path.parent / project
    return project.resolve()


def has_active_lock(payload: dict[str, Any]) -> bool:
    for collection in ("images", "reference_jobs"):
        items = payload.get(collection, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            transport = item.get("transport", {})
            if item.get("status") == "generating":
                return True
            if isinstance(transport, dict) and transport.get("active_attempt") is not None:
                return True
    return False


def ignored(path: Path, workspace: Path) -> bool:
    relative = path.relative_to(workspace)
    return (
        any(part in IGNORED_PARTS for part in relative.parts)
        or (relative.parts and relative.parts[0] == TOOLS_DIR)
        or (
            relative.parts
            and relative.parts[0] == TEMP_DIR
            and "02-整理备份" in relative.parts
        )
    )


def find_state_files(workspace: Path) -> list[Path]:
    result: list[Path] = []
    for path in workspace.rglob("review-state.json"):
        if not ignored(path, workspace) and path.is_file():
            result.append(path)
    return sorted(result)


def discover_state_projects(workspace: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    projects: dict[Path, tuple[Path, dict[str, Any]]] = {}
    for state_path in find_state_files(workspace):
        payload = load_state(state_path)
        root = state_project_root(state_path, payload)
        if not is_relative_to(root, workspace):
            continue
        if not root.is_dir():
            raise WorkspaceError(f"状态记录的项目目录不存在: {root}")
        existing = projects.get(root)
        if existing and existing[0] != state_path:
            raise WorkspaceError(f"同一项目发现多个状态文件: {root}")
        projects[root] = (state_path, payload)
    return [(root, state, payload) for root, (state, payload) in sorted(projects.items())]


def looks_like_legacy_complete(project: Path) -> bool:
    has_release = any(
        child.is_dir() and child.name.startswith("最终发布版-")
        for child in project.iterdir()
    )
    return has_release and (project / "验收记录.md").is_file()


def make_move(source: Path, destination: Path, kind: str, phase: str | None = None) -> Move:
    return Move(source.resolve(), destination.resolve(), kind, phase)


def build_plan(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise WorkspaceError(f"工作区不存在: {workspace}")

    moves: list[Move] = []
    blockers: list[str] = []
    state_projects = discover_state_projects(workspace)
    project_roots = {root for root, _state, _payload in state_projects}

    for root, state_path, payload in state_projects:
        if has_active_lock(payload):
            blockers.append(f"项目仍有活动生图锁: {root}")
            continue
        phase = str(payload.get("phase", "unknown"))
        category = COMPLETED_DIR if phase == "complete" else ONGOING_DIR
        destination = workspace / category / root.name
        if root.parent == destination.parent:
            continue
        moves.append(make_move(root, destination, "state_project", phase))

    legacy_archive = workspace / "作品归档"
    if legacy_archive.is_dir():
        for child in sorted(legacy_archive.iterdir()):
            if not child.is_dir() or child.resolve() in project_roots:
                continue
            if looks_like_legacy_complete(child):
                moves.append(
                    make_move(
                        child,
                        workspace / COMPLETED_DIR / child.name,
                        "legacy_complete_project",
                        "complete_without_state",
                    )
                )
            elif child.name not in {".DS_Store"}:
                blockers.append(f"旧归档项目无法判定是否完成: {child}")

    fixed_moves = (
        (workspace / "代表作", workspace / SHOWCASE_DIR, "showcase"),
        (
            workspace / "创作计划.xlsx",
            workspace / PLANNING_DIR / "01-创作计划.xlsx",
            "planning_file",
        ),
        (
            workspace / "参考图文视频",
            workspace / REFERENCE_DIR / "01-参考图文视频",
            "reference_library",
        ),
        (
            workspace / "系列制作规范.md",
            workspace / GUIDES_DIR / "01-系列制作规范.md",
            "guide_file",
        ),
        (
            workspace / "oh-my-folkstoryphoto",
            workspace / TEMP_DIR / "01-旧版技能源码-oh-my-folkstoryphoto",
            "legacy_skill_source",
        ),
        (
            workspace / "_render_pure_voiceover",
            workspace / TEMP_DIR / "03-历史临时输出" / "_render_pure_voiceover",
            "temporary_output",
        ),
    )
    for source, destination, kind in fixed_moves:
        if source.exists():
            moves.append(make_move(source, destination, kind))

    unique_sources: set[Path] = set()
    unique_destinations: set[Path] = set()
    for move in moves:
        if move.source in unique_sources:
            blockers.append(f"重复移动来源: {move.source}")
        if move.destination in unique_destinations or move.destination.exists():
            blockers.append(f"移动目标已存在: {move.destination}")
        unique_sources.add(move.source)
        unique_destinations.add(move.destination)

    return {
        "workspace": str(workspace),
        "numbered_directories": list(NUMBERED_DIRS),
        "moves": [
            {
                "source": str(move.source),
                "destination": str(move.destination),
                "kind": move.kind,
                "phase": move.phase,
            }
            for move in moves
        ],
        "state_project_count": len(state_projects),
        "blockers": blockers,
    }


def moves_from_plan(plan: dict[str, Any]) -> list[Move]:
    return [
        Move(
            Path(item["source"]),
            Path(item["destination"]),
            str(item["kind"]),
            item.get("phase"),
        )
        for item in plan["moves"]
    ]


def replacement_pairs(workspace: Path, moves: Iterable[Move]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for move in moves:
        pairs.append((str(move.source), str(move.destination)))
        try:
            old_relative = move.source.relative_to(workspace).as_posix()
            new_relative = move.destination.relative_to(workspace).as_posix()
        except ValueError:
            continue
        if move.source.is_dir():
            pairs.append((old_relative + "/", new_relative + "/"))
        else:
            pairs.append((old_relative, new_relative))
    return sorted(set(pairs), key=lambda pair: len(pair[0]), reverse=True)


def iter_text_files(workspace: Path) -> Iterable[Path]:
    for path in workspace.rglob("*"):
        if not path.is_file() or ignored(path, workspace):
            continue
        if path.name == ACTIVE_POINTER or path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def changed_text_files(
    workspace: Path, pairs: list[tuple[str, str]]
) -> list[tuple[Path, str, str]]:
    replacements = dict(pairs)
    pattern = re.compile("|".join(re.escape(old) for old, _new in pairs)) if pairs else None

    def replace_paths(value: str) -> str:
        if pattern is None:
            return value
        return pattern.sub(lambda match: replacements[match.group(0)], value)

    changes: list[tuple[Path, str, str]] = []
    for path in iter_text_files(workspace):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        revised = replace_paths(original)
        if revised == original and path.suffix.lower() == ".json":
            try:
                payload = json.loads(original)
            except json.JSONDecodeError:
                payload = None

            def rewrite_value(value: Any) -> Any:
                if isinstance(value, str):
                    return replace_paths(value)
                if isinstance(value, list):
                    return [rewrite_value(item) for item in value]
                if isinstance(value, dict):
                    return {key: rewrite_value(item) for key, item in value.items()}
                return value

            if payload is not None:
                rewritten_payload = rewrite_value(payload)
                if rewritten_payload != payload:
                    revised = (
                        json.dumps(rewritten_payload, ensure_ascii=False, indent=2) + "\n"
                    )
        if revised != original:
            changes.append((path, original, revised))
    return changes


def current_path_after_moves(path: Path, moves: list[Move]) -> Path:
    matching = [move for move in moves if is_relative_to(path, move.source)]
    if not matching:
        return path
    move = max(matching, key=lambda item: len(item.source.parts))
    return move.destination / path.relative_to(move.source)


def backup_changes(
    workspace: Path,
    backup_dir: Path,
    changes: list[tuple[Path, str, str]],
    plan: dict[str, Any],
) -> None:
    files_root = backup_dir / "原始文本"
    for path, original, _revised in changes:
        relative = path.relative_to(workspace)
        target = files_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original, encoding="utf-8")
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "整理计划.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate_result(
    workspace: Path,
    expected_project_count: int,
    old_absolute_prefixes: list[str],
) -> dict[str, Any]:
    state_files = find_state_files(workspace)
    if len(state_files) != expected_project_count:
        raise WorkspaceError(
            f"状态项目数量变化: 预期 {expected_project_count}, 实际 {len(state_files)}"
        )
    for state_path in state_files:
        payload = load_state(state_path)
        root = state_project_root(state_path, payload)
        if not root.is_dir():
            raise WorkspaceError(f"迁移后 project_dir 不存在: {root}")
        if has_active_lock(payload):
            raise WorkspaceError(f"迁移后发现活动生图锁: {state_path}")

    pointer_path = workspace / ACTIVE_POINTER
    pointer_target: str | None = None
    if pointer_path.exists():
        pointer = load_state(pointer_path)
        raw_target = pointer.get("state_file")
        if not isinstance(raw_target, str):
            raise WorkspaceError("活动项目指针缺少 state_file")
        target = (workspace / raw_target).resolve()
        if not target.is_file():
            raise WorkspaceError(f"活动项目指针失效: {target}")
        pointer_target = str(target)

    stale: list[str] = []
    for path in iter_text_files(workspace):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(prefix in content for prefix in old_absolute_prefixes):
            stale.append(str(path))
    if stale:
        raise WorkspaceError(f"仍有旧绝对路径残留: {stale[:5]}")
    return {
        "state_project_count": len(state_files),
        "pointer_target": pointer_target,
        "old_absolute_path_residue": 0,
    }


def apply_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("blockers"):
        raise WorkspaceError("存在阻断项，拒绝整理: " + "; ".join(plan["blockers"]))
    workspace = Path(plan["workspace"]).resolve()
    moves = moves_from_plan(plan)
    pairs = replacement_pairs(workspace, moves)
    changes = changed_text_files(workspace, pairs)
    backup_dir = workspace / BACKUP_SUBDIR / utc_stamp()
    backup_changes(workspace, backup_dir, changes, plan)
    completed_moves: list[Move] = []
    written_paths: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for name in NUMBERED_DIRS:
            directory = workspace / name
            if not directory.exists():
                directory.mkdir(parents=True)
                created_dirs.append(directory)
        for move in moves:
            move.destination.parent.mkdir(parents=True, exist_ok=True)
            move.source.replace(move.destination)
            completed_moves.append(move)
        for old_path, _original, revised in changes:
            target = current_path_after_moves(old_path, moves)
            if not target.is_file():
                raise WorkspaceError(f"待改写文件在移动后不存在: {target}")
            atomic_write_text(target, revised)
            written_paths.append(target)

        legacy_archive = workspace / "作品归档"
        if legacy_archive.is_dir():
            ds_store = legacy_archive / ".DS_Store"
            ds_store.unlink(missing_ok=True)
            if not any(legacy_archive.iterdir()):
                legacy_archive.rmdir()

        validation = validate_result(
            workspace,
            int(plan["state_project_count"]),
            [str(move.source) for move in moves],
        )
    except Exception:
        for move in reversed(completed_moves):
            if move.destination.exists() and not move.source.exists():
                move.source.parent.mkdir(parents=True, exist_ok=True)
                move.destination.replace(move.source)
        files_root = backup_dir / "原始文本"
        if files_root.is_dir():
            for backup in files_root.rglob("*"):
                if backup.is_file():
                    target = workspace / backup.relative_to(files_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise

    return {
        "applied": True,
        "workspace": str(workspace),
        "backup": str(backup_dir),
        "moves_completed": len(completed_moves),
        "text_files_rewritten": len(written_paths),
        "validation": validation,
    }


def safe_project_name(raw: str) -> str:
    name = raw.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise WorkspaceError("project-name 必须是不含路径分隔符的非空名称")
    return name


def versioned_project_dir(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate
    version = 2
    while True:
        candidate = parent / f"{name}-v{version}"
        if not candidate.exists():
            return candidate
        version += 1


def init_workspace_project(workspace: Path, project_name: str) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    ongoing = workspace / ONGOING_DIR
    ongoing.mkdir(parents=True, exist_ok=True)
    project_dir = versioned_project_dir(ongoing, safe_project_name(project_name))
    result = review_state.init_project(project_dir, schema_version=6)
    state_path = project_dir / "08-系统文件" / "review-state.json"
    pointer = {"state_file": state_path.relative_to(workspace).as_posix()}
    atomic_write_text(
        workspace / ACTIVE_POINTER,
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "command": "init-project",
        "workspace": str(workspace),
        "project_dir": str(project_dir),
        "state_file": str(state_path),
        "realism_file": result["realism_file"],
        "active_pointer": str(workspace / ACTIVE_POINTER),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    organize = subparsers.add_parser(
        "organize", help="Preview or apply the numbered workspace layout"
    )
    organize.add_argument("--workspace", required=True, type=Path)
    organize.add_argument(
        "--apply",
        action="store_true",
        help="Apply the plan; without this flag the command is a dry run.",
    )
    initialize = subparsers.add_parser(
        "init-project", help="Create a schema-v5 project under 01-进行中项目"
    )
    initialize.add_argument("--workspace", required=True, type=Path)
    initialize.add_argument("--project-name", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "init-project":
            result = init_workspace_project(args.workspace, args.project_name)
        else:
            plan = build_plan(args.workspace)
            result = apply_plan(plan) if args.apply else {"dry_run": True, **plan}
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0
    except (WorkspaceError, review_state.StateError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
