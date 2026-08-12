#!/usr/bin/env python3
"""Schema-v5 capture-profile, prompt and image-review validation helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEVICE_PROFILES = {
    "当代中低端手机",
    "早期智能机/功能机",
    "2010年代存储卡消费级DV",
    "1990年代至2000年代磁带式家用摄像机",
    "卡片数码相机",
    "CCTV/固定监控",
    "运动相机/执法记录仪/行车记录仪",
    "胶片扫描/旧照片/档案翻拍",
}
CAPTURE_ROLES = {"primary", "secondary"}
DEVICE_VISIBILITY = {"不可见", "仅自然边缘/固定支架", "由另一采集源拍到"}
CALIBRATION_ROLES = {"普通基线", "最差拍摄条件", "首次重大异常", "无"}
REFERENCE_KINDS = {"identity", "prop", "location", "capture_style"}
CHECK_VALUES = {"pass", "fail", "na"}
CRITICAL_CHECKS = (
    "viewpoint_physics",
    "unplanned_recorder_absent",
    "capture_profile_match",
    "not_cinematic",
    "identity_match",
    "key_prop_match",
)
REQUIRED_CHECKS = CRITICAL_CHECKS + (
    "continuity_match",
    "defects_are_causal",
)
PROMPT_MAX_CHARACTERS = 260
PROMPT_MAX_BYTES = 900
POSITIVE_AESTHETIC_TERMS = (
    "电影感",
    "史诗",
    "英雄机位",
    "戏剧性光线",
    "戏剧性照明",
    "商业摄影",
    "概念图",
    "cinematic",
    "epic",
    "hero shot",
    "dramatic lighting",
    "commercial photography",
    "concept art",
    "masterpiece",
    "volumetric lighting",
    "hdr",
)
ANTI_CINEMATIC_CLAUSE = (
    "Capture realism: ordinary device recording, no cinematic lighting, shallow depth of "
    "field, rim light, teal-orange grading, hero angle, commercial HDR, game-render finish, "
    "or polished concept-art composition."
)
REFERENCE_SAFETY_CLAUSE = (
    "Reference safety: use inputs only for their declared identity, prop, location, or capture "
    "artifact role; never copy source composition, unrelated people, scenery, text, panels, or UI."
)


class AuthenticityError(ValueError):
    """Raised when a schema-v5 authenticity contract is violated."""


def realism_template() -> str:
    example = {
        "primary_capture_id": "phone-main",
        "captures": [
            {
                "id": "phone-main",
                "role": "primary",
                "device_profile": "当代中低端手机",
                "era": "故事发生当年，使用数年的中低端安卓手机",
                "owner": "待填写",
                "story_reason": "待填写为什么会持续拍摄",
                "original_ratio": "4:3或设备原生竖拍比例，最终小幅规范为4:5",
                "orientation_policy": "以竖拍为主；横向载体作为场景内实体被竖拍",
                "stable_state": "普通自动曝光、轻微数字锐化、无刻意景深",
                "constrained_state": "仅填写由遮挡、低照或数码变焦导致的变化",
                "failure_state": "仅填写由奔跑、跌落、进水或失焦导致的变化",
                "native_overlay_policy": "无；不得添加假相机HUD",
                "anchor_ids": ["phone-indoor-candid"],
            }
        ],
        "non_photographic_exceptions": [],
        "disclosure": "发布时标注内容由AI生成",
    }
    return (
        "# 真实性方案\n\n"
        "先选择一种主采集设备，最多增加两种有剧情来源的辅助设备。"
        "同一设备在稳定、受限、失控状态下的变化不算新增设备。\n\n"
        "请修改下方 JSON；确认前不得创建故事脚本。\n\n"
        "```json capture-profile\n"
        + json.dumps(example, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def _extract_json_block(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s+capture-profile\s*(\{.*?\})\s*```", text, re.S | re.I)
    if not match:
        raise AuthenticityError("真实性方案必须包含 ```json capture-profile 代码块")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AuthenticityError(f"真实性方案JSON无效: {exc}") from exc
    if not isinstance(payload, dict):
        raise AuthenticityError("真实性方案JSON必须是对象")
    return payload


def parse_realism_plan(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuthenticityError(f"真实性方案不存在: {path}")
    payload = _extract_json_block(path.read_text(encoding="utf-8"))
    captures = payload.get("captures")
    if not isinstance(captures, list) or not 1 <= len(captures) <= 3:
        raise AuthenticityError("真实性方案必须登记1种主设备，且总设备数不得超过3")
    ids: set[str] = set()
    primary: list[str] = []
    required_text = (
        "id", "role", "device_profile", "era", "owner", "story_reason",
        "original_ratio", "orientation_policy", "stable_state", "constrained_state",
        "failure_state", "native_overlay_policy",
    )
    for index, capture in enumerate(captures):
        if not isinstance(capture, dict):
            raise AuthenticityError(f"captures[{index}]必须是对象")
        for field in required_text:
            value = capture.get(field)
            if not isinstance(value, str) or not value.strip() or "待填写" in value:
                raise AuthenticityError(f"captures[{index}].{field}必须填写完整")
        capture_id = capture["id"].strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,31}", capture_id):
            raise AuthenticityError(f"captures[{index}].id必须是2–32位小写字母、数字或连字符")
        if capture_id in ids:
            raise AuthenticityError(f"采集配置ID重复: {capture_id}")
        ids.add(capture_id)
        if capture["role"] not in CAPTURE_ROLES:
            raise AuthenticityError(f"captures[{index}].role必须是primary或secondary")
        if capture["role"] == "primary":
            primary.append(capture_id)
        if capture["device_profile"] not in DEVICE_PROFILES:
            raise AuthenticityError(f"未知设备档案: {capture['device_profile']}")
        anchors = capture.get("anchor_ids", [])
        if not isinstance(anchors, list) or not all(isinstance(v, str) and v.strip() for v in anchors):
            raise AuthenticityError(f"captures[{index}].anchor_ids必须是字符串数组")
    if len(primary) != 1:
        raise AuthenticityError("真实性方案必须且只能有一个primary采集配置")
    if payload.get("primary_capture_id") != primary[0]:
        raise AuthenticityError("primary_capture_id必须指向唯一primary采集配置")
    exceptions = payload.get("non_photographic_exceptions", [])
    if not isinstance(exceptions, list):
        raise AuthenticityError("non_photographic_exceptions必须是数组")
    return payload


def capture_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in plan["captures"]}


def validate_storyboard_capture_rows(rows: list[dict[str, str]], plan: dict[str, Any]) -> list[int]:
    captures = capture_map(plan)
    counts = {key: 0 for key in captures}
    calibration: dict[str, int] = {}
    for row in rows:
        number = int(row["图号"])
        capture_id = row["采集配置ID"]
        if capture_id not in captures:
            raise AuthenticityError(f"AI分镜图{number}使用未批准采集配置: {capture_id}")
        counts[capture_id] += 1
        visibility = row["设备可见性"]
        if visibility not in DEVICE_VISIBILITY:
            raise AuthenticityError(f"AI分镜图{number}设备可见性无效: {visibility}")
        body = row["拍摄者入镜范围"]
        if visibility == "由另一采集源拍到" and "第二拍摄源:" not in body:
            raise AuthenticityError(f"AI分镜图{number}必须在拍摄者入镜范围中写明“第二拍摄源:配置ID”")
        if visibility != "由另一采集源拍到" and any(
            term in body for term in ("完整手机", "完整DV", "完整摄像机", "完整相机", "完整机身")
        ):
            raise AuthenticityError(f"AI分镜图{number}第一视角不得出现当前设备完整机身")
        role = row["校准角色"]
        if role not in CALIBRATION_ROLES:
            raise AuthenticityError(f"AI分镜图{number}校准角色无效: {role}")
        if role != "无":
            if role in calibration:
                raise AuthenticityError(f"校准角色重复: {role}")
            calibration[role] = number
    expected = CALIBRATION_ROLES - {"无"}
    if set(calibration) != expected:
        raise AuthenticityError("AI分镜必须各指定一张普通基线、最差拍摄条件、首次重大异常")
    primary = plan["primary_capture_id"]
    if counts[primary] * 2 < len(rows):
        raise AuthenticityError("主采集设备必须承担至少一半正式图")
    return [calibration[name] for name in ("普通基线", "最差拍摄条件", "首次重大异常")]


def validate_authored_prompt(prompt: str) -> None:
    encoded = prompt.encode("utf-8")
    if len(prompt) > PROMPT_MAX_CHARACTERS or len(encoded) > PROMPT_MAX_BYTES:
        raise AuthenticityError(
            f"v5 authored prompt超过预算: {len(prompt)}字符/{len(encoded)}字节；"
            f"上限{PROMPT_MAX_CHARACTERS}字符/{PROMPT_MAX_BYTES}字节"
        )
    lowered = prompt.lower()
    hits = [term for term in POSITIVE_AESTHETIC_TERMS if term in lowered]
    if hits:
        raise AuthenticityError("authored prompt含美学诱导词: " + ", ".join(hits))


def materialize_prompt(prompt: str, *, needs_reference_safety: bool) -> str:
    parts = [prompt.strip(), ANTI_CINEMATIC_CLAUSE]
    if needs_reference_safety:
        parts.append(REFERENCE_SAFETY_CLAUSE)
    return "\n".join(parts) + "\n"


def load_review(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthenticityError(f"无法读取真实性审查JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        raise AuthenticityError("真实性审查必须包含checks对象")
    checks = payload["checks"]
    for name in REQUIRED_CHECKS:
        value = checks.get(name)
        if value not in CHECK_VALUES:
            raise AuthenticityError(f"真实性审查checks.{name}必须是pass、fail或na")
        if name in ("viewpoint_physics", "unplanned_recorder_absent", "capture_profile_match", "not_cinematic") and value == "na":
            raise AuthenticityError(f"真实性审查checks.{name}不得为na")
    red_flags = payload.get("red_flags", [])
    if not isinstance(red_flags, list) or not all(isinstance(v, str) and v.strip() for v in red_flags):
        raise AuthenticityError("真实性审查red_flags必须是非空字符串数组")
    notes = payload.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        raise AuthenticityError("真实性审查notes必须填写")
    return payload


def hard_failures(review: dict[str, Any]) -> list[str]:
    labels = {
        "viewpoint_physics": "拍摄机位物理不成立",
        "unplanned_recorder_absent": "出现计划外拍摄设备",
        "capture_profile_match": "不符合已批准采集配置",
        "not_cinematic": "呈现电影剧照、游戏截图或商业摄影感",
        "identity_match": "人物身份与参考不一致",
        "key_prop_match": "关键道具结构与参考不一致",
        "continuity_match": "场景或连续性不成立",
        "defects_are_causal": "成像缺陷没有现场原因",
    }
    return [labels[name] for name in REQUIRED_CHECKS if review["checks"][name] == "fail"]
