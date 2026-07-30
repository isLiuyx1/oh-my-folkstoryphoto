#!/usr/bin/env python3
"""Codex Stop hook for unfinished folk-story photo-carousel self-review."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


POINTER_NAME = ".oh-my-folkstoryphoto-review.json"
SAFE_PHASES = {
    "awaiting_plan_approval",
    "awaiting_reference_approval",
    "complete",
    "needs_user",
}
BLOCKING_PHASES = {
    "drafting",
    "text_self_review",
    "reference_self_review",
    "scene_self_review",
    "repairing",
    "final_self_review",
}


def emit(value: dict[str, Any] | None = None) -> None:
    if value:
        print(json.dumps(value, ensure_ascii=False))


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def resolve_state_file(cwd: Path, pointer: dict[str, Any]) -> Path:
    raw_path = pointer.get("state_file")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("pointer state_file must be a non-empty path string")
    state_file = Path(raw_path).expanduser()
    if not state_file.is_absolute():
        state_file = cwd / state_file
    return state_file.resolve()


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        emit()
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "Stop":
        emit()
        return 0
    if event.get("stop_hook_active") is True:
        emit()
        return 0

    raw_cwd = event.get("cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd:
        emit()
        return 0
    cwd = Path(raw_cwd).expanduser().resolve()
    pointer_path = cwd / POINTER_NAME
    if not pointer_path.is_file():
        emit()
        return 0

    try:
        pointer = load_object(pointer_path)
        state_path = resolve_state_file(cwd, pointer)
        state = load_object(state_path)
        phase = state.get("phase")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        emit(
            {
                "decision": "block",
                "reason": (
                    "民间故事图文存在活动自审指针，但状态不可读取："
                    f"{exc}。请修复或将项目明确标记为 needs_user 后再结束。"
                ),
            }
        )
        return 0

    if phase in SAFE_PHASES:
        emit()
        return 0
    if phase in BLOCKING_PHASES:
        emit(
            {
                "decision": "block",
                "reason": (
                    f"民间故事图文项目仍处于 {phase}。请读取 {state_path}，"
                    "完成当前自审/返修并更新状态；不得绕过用户确认，也不得超过一次自动返修。"
                ),
            }
        )
        return 0

    emit(
        {
            "decision": "block",
            "reason": (
                f"民间故事图文审查状态包含未知阶段 {phase!r}：{state_path}。"
                "请修正为合法阶段或标记 needs_user。"
            ),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
