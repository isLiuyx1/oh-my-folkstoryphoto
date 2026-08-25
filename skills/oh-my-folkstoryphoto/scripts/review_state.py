#!/usr/bin/env python3
"""Validate, transition, and migrate folk-story photo review state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authenticity


PHASES = {
    "realism_self_review",
    "awaiting_realism_approval",
    "drafting",
    "story_self_review",
    "awaiting_story_approval",
    "plan_self_review",
    "awaiting_storyboard_approval",
    "text_self_review",
    "awaiting_plan_approval",
    "reference_self_review",
    "awaiting_reference_approval",
    "calibration_self_review",
    "awaiting_calibration_approval",
    "scene_generation",
    "awaiting_first_review_decision",
    "scene_self_review",
    "awaiting_repair_approval",
    "repairing",
    "final_self_review",
    "text_revision_self_review",
    "awaiting_text_revision_approval",
    "complete",
    "needs_user",
}
V1_VERDICTS = {"pending", "pass", "fail", "needs_user"}
V2_STATUSES = {
    "pending",
    "generating",
    "transport_blocked",
    "candidate_ready",
    "review_pending",
    "pass",
    "needs_user",
}
SUPPORTED_SCHEMAS = {1, 2, 3, 4, 5, 6}
FINAL_PHASES = {"final_self_review", "complete"}
REQUIRED_COMPLETE_ARTIFACTS = ("self_review", "acceptance", "release_manifest")
REFERENCE_BOARD_TIMEOUT_SECONDS = 480
LEGAL_TRANSITIONS = {
    "realism_self_review": {"awaiting_realism_approval", "needs_user"},
    "awaiting_realism_approval": {"realism_self_review", "drafting", "needs_user"},
    "drafting": {"story_self_review", "text_self_review", "needs_user"},
    "story_self_review": {"awaiting_story_approval", "needs_user"},
    "awaiting_story_approval": {"story_self_review", "plan_self_review", "needs_user"},
    "plan_self_review": {"awaiting_storyboard_approval", "story_self_review", "needs_user"},
    "awaiting_storyboard_approval": {"plan_self_review", "reference_self_review", "story_self_review", "needs_user"},
    "text_self_review": {"awaiting_plan_approval", "needs_user"},
    "awaiting_plan_approval": {"drafting", "reference_self_review", "needs_user"},
    "reference_self_review": {"awaiting_reference_approval", "needs_user"},
    "awaiting_reference_approval": {
        "reference_self_review",
        "scene_self_review",
        "calibration_self_review",
        "needs_user",
    },
    "calibration_self_review": {"awaiting_calibration_approval", "reference_self_review", "needs_user"},
    "awaiting_calibration_approval": {"calibration_self_review", "scene_self_review", "scene_generation", "needs_user"},
    "scene_generation": {"awaiting_first_review_decision", "needs_user"},
    "awaiting_first_review_decision": {"scene_self_review", "final_self_review", "needs_user"},
    "scene_self_review": {
        "awaiting_repair_approval",
        "final_self_review",
        "needs_user",
    },
    "awaiting_repair_approval": {"repairing", "scene_self_review", "needs_user"},
    "repairing": {"awaiting_repair_approval", "final_self_review", "needs_user"},
    "final_self_review": {"repairing", "complete", "needs_user"},
    "text_revision_self_review": {"awaiting_text_revision_approval"},
    "awaiting_text_revision_approval": {"text_revision_self_review", "complete"},
    "complete": {"text_revision_self_review"},
    "needs_user": {
        "realism_self_review",
        "drafting",
        "text_self_review",
        "reference_self_review",
        "calibration_self_review",
        "awaiting_calibration_approval",
        "scene_self_review",
        "awaiting_repair_approval",
        "repairing",
        "final_self_review",
    },
}
APPROVAL_TRANSITIONS = {
    ("awaiting_realism_approval", "drafting"),
    ("awaiting_story_approval", "plan_self_review"),
    ("awaiting_storyboard_approval", "reference_self_review"),
    ("awaiting_plan_approval", "reference_self_review"),
    ("awaiting_reference_approval", "scene_self_review"),
    ("awaiting_reference_approval", "calibration_self_review"),
    ("awaiting_calibration_approval", "scene_self_review"),
    ("awaiting_repair_approval", "repairing"),
}

V4_ARTIFACTS = {
    "story": "01-故事脚本.md",
    "storyboard": "02-专业分镜表.md",
    "publication": "03-发布文件说明.md",
    "release_dir": "04-最终发布版-N图",
    "references_dir": "05-参考素材",
    "character_references": "05-参考素材/01-角色参考",
    "location_masters": "05-参考素材/02-地点母版",
    "prop_references": "05-参考素材/03-物件与设备参考",
    "process_dir": "06-生成过程",
    "originals_dir": "06-生成过程/01-原始生成图",
    "repairs_dir": "06-生成过程/02-返修记录",
    "overview_dir": "06-生成过程/03-当前总览",
    "production_dir": "07-制作资料",
    "creative_plan": "07-制作资料/01-创作方案.md",
    "ai_storyboard": "07-制作资料/02-AI生成分镜.md",
    "visual_settings": "07-制作资料/03-角色与视觉设定.md",
    "prompts": "07-制作资料/04-出图提示词.md",
    "reference_notes": "07-制作资料/05-参考图说明.md",
    "reports_dir": "07-制作资料/06-审查报告",
    "self_review": "07-制作资料/06-审查报告/01-自审记录.md",
    "repair_report": "07-制作资料/06-审查报告/02-返修报告.md",
    "blocked_report": "07-制作资料/06-审查报告/03-生成阻塞报告.md",
    "acceptance": "07-制作资料/06-审查报告/04-验收记录.md",
    "system_dir": "08-系统文件",
    "release_manifest": "08-系统文件/release-manifest.json",
    "requests_dir": "08-系统文件/01-生成请求",
    "backups_dir": "08-系统文件/02-状态备份",
}

V5_ARTIFACTS = {
    **V4_ARTIFACTS,
    "realism_plan": "00-真实性方案.md",
    "calibration_dir": "06-生成过程/00-真实性校准",
    "calibration_contact_sheet": "06-生成过程/00-真实性校准/真实性校准联系表.jpg",
    "authenticity_reviews_dir": "08-系统文件/03-真实性审查",
}

V6_ARTIFACTS = {
    **V5_ARTIFACTS,
    "originals_overview": "06-生成过程/03-当前总览/首轮原图总览.jpg",
}

PUBLIC_STORYBOARD_COLUMNS = (
    "图号",
    "画面拍什么",
    "镜头怎么拍",
    "人物在做什么",
    "这张图要表达什么",
)
LEGACY_PRODUCTION_STORYBOARD_COLUMNS = (
    "图号",
    "唯一证据",
    "字幕",
    "拍摄来源",
    "拍摄原因",
    "受限机位",
    "人物意识",
    "设备/年代",
    "成像结果",
    "连续性引用",
    "真实性风险",
)
PRODUCTION_STORYBOARD_COLUMNS = (
    "图号",
    "唯一证据",
    "画面原生文字",
    "发布字幕",
    "拍摄来源",
    "拍摄原因",
    "受限机位",
    "人物意识",
    "设备/年代",
    "成像结果",
    "连续性引用",
    "真实性风险",
)
V5_PRODUCTION_STORYBOARD_COLUMNS = (
    "图号",
    "唯一证据",
    "画面原生文字",
    "发布字幕",
    "采集配置ID",
    "拍摄者",
    "拍摄原因",
    "受限机位",
    "拍摄者入镜范围",
    "设备可见性",
    "人物意识",
    "成像结果",
    "连续性引用",
    "校准角色",
    "真实性风险",
)
MAX_PUBLICATION_CAPTION_CHARS = 48
VAGUE_PUBLICATION_CAPTION_PATTERNS = (
    re.compile(r"^(?:我|我们)?(?:看见|看到|发现)(?:了)?(?:一个|一些)?(?:奇怪|诡异|可怕|无法解释)的?(?:东西|景象|事情)$"),
    re.compile(r"^(?:事情|情况|一切)(?:开始|变得|越来越)?(?:不对劲|奇怪|诡异|可怕)(?:了)?$"),
    re.compile(r"^(?:接下来|后来)(?:发生的)?(?:事|事情).*(?:终生难忘|无法解释|不敢相信)$"),
    re.compile(r"^(?:这|那)(?:一幕|件事|个东西).*(?:诡异|奇怪|可怕)(?:了)?$"),
    re.compile(r"^他们似乎隐瞒了什么$"),
)


class StateError(ValueError):
    """Raised when review state violates the public contract."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise StateError(f"unable to read {path}: {exc}") from exc


def resolve_project_dir(state_path: Path, raw_project_dir: Any) -> Path:
    if not isinstance(raw_project_dir, str) or not raw_project_dir.strip():
        raise StateError("project_dir must be a non-empty path string")
    project_dir = Path(raw_project_dir).expanduser()
    if not project_dir.is_absolute():
        project_dir = state_path.parent / project_dir
    return project_dir.resolve()


def resolve_project_path(project_dir: Path, raw_path: Any, field: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise StateError(f"{field} must be a non-empty path string")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve()


def require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise StateError(f"{field} must be an array of non-empty strings")
    return value


def require_nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StateError(f"{field} must be a non-negative integer")
    return value


def validate_transition(current: str, target: str, user_approved: bool = False) -> None:
    if current not in PHASES or target not in PHASES:
        raise StateError("transition phases must be valid review phases")
    if target not in LEGAL_TRANSITIONS[current]:
        raise StateError(f"illegal phase transition: {current} -> {target}")
    if (current, target) in APPROVAL_TRANSITIONS and not user_approved:
        raise StateError(f"{current} -> {target} requires explicit --user-approved")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".json", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=path.suffix or ".txt", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_relative(payload: dict[str, Any], key: str, legacy: str | None = None) -> str:
    value = payload.get("artifacts", {}).get(key)
    if isinstance(value, str) and value.strip():
        return value
    if payload.get("schema_version") == 4 and key in V4_ARTIFACTS:
        return V4_ARTIFACTS[key]
    if payload.get("schema_version") == 5 and key in V5_ARTIFACTS:
        return V5_ARTIFACTS[key]
    if payload.get("schema_version") == 6 and key in V6_ARTIFACTS:
        return V6_ARTIFACTS[key]
    if legacy is not None:
        return legacy
    raise StateError(f"artifacts.{key} is required")


def artifact_path(
    project_dir: Path,
    payload: dict[str, Any],
    key: str,
    legacy: str | None = None,
) -> Path:
    return resolve_project_path(
        project_dir, artifact_relative(payload, key, legacy), f"artifacts.{key}"
    )


def requests_root(project_dir: Path) -> Path:
    v4_state = project_dir / V4_ARTIFACTS["system_dir"] / "review-state.json"
    if v4_state.is_file():
        payload = load_json(v4_state)
        if payload.get("schema_version") in {4, 5, 6}:
            return artifact_path(project_dir, payload, "requests_dir")
    return project_dir / "生成请求"


def approval_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StateError(f"approval file does not exist: {path}")
    content = path.read_bytes()
    if not content:
        raise StateError(f"approval file is empty: {path}")
    if path.suffix.lower() in {".md", ".txt"} and not content.decode("utf-8").strip():
        raise StateError(f"approval file is empty: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_approval_hash(project_dir: Path, record: Any, field: str) -> None:
    if not isinstance(record, dict):
        raise StateError(f"approvals.{field} must be an object")
    path = resolve_project_path(project_dir, record.get("path"), f"approvals.{field}.path")
    digest = record.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise StateError(f"approvals.{field}.sha256 is invalid")
    if not path.is_file() or sha256_file(path) != digest:
        raise StateError(
            f"approved {field} file changed; reopen the corresponding review gate"
        )


def parse_storyboard(path: Path) -> list[int]:
    if not path.is_file():
        raise StateError(f"professional storyboard does not exist: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    table_rows = [line for line in lines if line.strip().startswith("|")]
    if len(table_rows) < 3:
        raise StateError("professional storyboard must contain a Markdown table")
    header = tuple(cell.strip() for cell in table_rows[0].strip().strip("|").split("|"))
    if header != PUBLIC_STORYBOARD_COLUMNS:
        raise StateError(
            "professional storyboard columns must be exactly: "
            + " | ".join(PUBLIC_STORYBOARD_COLUMNS)
        )
    numbers: list[int] = []
    for row in table_rows[2:]:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) != len(PUBLIC_STORYBOARD_COLUMNS):
            raise StateError("every professional storyboard row must contain exactly five cells")
        try:
            number = int(cells[0])
        except ValueError as exc:
            raise StateError(f"invalid storyboard image number: {cells[0]}") from exc
        if any(not value for value in cells[1:]):
            raise StateError(f"storyboard image {number} contains an empty user-facing field")
        numbers.append(number)
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise StateError(f"storyboard image numbers must be contiguous from 1; got {numbers}")
    return numbers


def validate_publication_caption(caption: str, number: int) -> None:
    visible = re.sub(r"\s+", "", caption)
    normalized = re.sub(r"[，。！？!?、；;：:\s…]+$", "", caption.strip())
    if any(pattern.fullmatch(normalized) for pattern in VAGUE_PUBLICATION_CAPTION_PATTERNS):
        raise StateError(
            f"AI storyboard image {number} publication caption is vague; name a concrete "
            "subject or object and the event shown by this frame"
        )
    length = len(visible)
    if length == 0:
        raise StateError(f"AI storyboard image {number} publication caption must not be empty")
    if length > MAX_PUBLICATION_CAPTION_CHARS:
        raise StateError(
            f"AI storyboard image {number} publication caption must contain at most "
            f"{MAX_PUBLICATION_CAPTION_CHARS} visible characters"
        )


def parse_production_storyboard(path: Path) -> tuple[list[int], tuple[str, ...]]:
    if not path.is_file():
        raise StateError(f"AI production storyboard does not exist: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    table_rows = [line for line in lines if line.strip().startswith("|")]
    if len(table_rows) < 3:
        raise StateError("AI production storyboard must contain a Markdown table")
    header = tuple(cell.strip() for cell in table_rows[0].strip().strip("|").split("|"))
    allowed = {
        PRODUCTION_STORYBOARD_COLUMNS,
        LEGACY_PRODUCTION_STORYBOARD_COLUMNS,
        V5_PRODUCTION_STORYBOARD_COLUMNS,
    }
    if header not in allowed:
        raise StateError(
            "AI production storyboard columns must match the v5, preferred v4.2 or legacy v4 format"
        )
    numbers: list[int] = []
    caption_index = (
        header.index("发布字幕")
        if header in {PRODUCTION_STORYBOARD_COLUMNS, V5_PRODUCTION_STORYBOARD_COLUMNS}
        else None
    )
    for row in table_rows[2:]:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) != len(header):
            raise StateError(
                f"every AI production storyboard row must contain exactly {len(header)} cells"
            )
        try:
            number = int(cells[0])
        except ValueError as exc:
            raise StateError(f"invalid AI storyboard image number: {cells[0]}") from exc
        if any(not value for value in cells[1:]):
            raise StateError(f"AI storyboard image {number} contains an empty production field")
        if caption_index is not None:
            validate_publication_caption(cells[caption_index], number)
        numbers.append(number)
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise StateError(
            f"AI production storyboard image numbers must be contiguous from 1; got {numbers}"
        )
    return numbers, header


def parse_v5_production_rows(path: Path) -> list[dict[str, str]]:
    numbers, header = parse_production_storyboard(path)
    if header != V5_PRODUCTION_STORYBOARD_COLUMNS:
        raise StateError("schema v5 requires the exact v5 AI production storyboard columns")
    table_rows = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("|")
    ]
    rows: list[dict[str, str]] = []
    for number, raw in zip(numbers, table_rows[2:], strict=True):
        cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
        row = dict(zip(header, cells, strict=True))
        row["图号"] = str(number)
        rows.append(row)
    return rows


def load_release_manifest(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, list):
        raise StateError("release manifest must contain an images array")
    if not all(isinstance(item, dict) for item in images):
        raise StateError("release manifest images must be objects")
    return images


def validate_common(
    state_path: Path, payload: dict[str, Any]
) -> tuple[str, Path, dict[str, Any], list[dict[str, Any]], list[str]]:
    phase = payload.get("phase")
    if phase not in PHASES:
        raise StateError(f"phase must be one of {sorted(PHASES)}")
    if payload.get("max_repairs_per_item") != 1:
        raise StateError("max_repairs_per_item must equal 1")

    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    if not project_dir.is_dir():
        raise StateError(f"project_dir does not exist: {project_dir}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise StateError("artifacts must be an object")
    images = payload.get("images")
    if not isinstance(images, list):
        raise StateError("images must be an array")
    blocking_reasons = require_string_list(
        payload.get("blocking_reasons", []), "blocking_reasons"
    )
    return phase, project_dir, artifacts, images, blocking_reasons


def validate_content_fields(
    item: dict[str, Any], prefix: str, status: str
) -> tuple[int, list[str], list[str]]:
    hard_failures = require_string_list(
        item.get("hard_failures", []), f"{prefix}.hard_failures"
    )
    red_flags = require_string_list(
        item.get("photo_red_flags", []), f"{prefix}.photo_red_flags"
    )
    repair_count = require_nonnegative_int(
        item.get("repair_count"), f"{prefix}.repair_count"
    )
    if repair_count not in (0, 1):
        raise StateError(f"{prefix}.repair_count must be 0 or 1")
    if status == "pass" and hard_failures:
        raise StateError(f"{prefix} cannot pass with hard failures")
    if status == "pass" and len(red_flags) >= 3:
        raise StateError(f"{prefix} cannot pass with three or more photo red flags")
    if status == "needs_user" and repair_count != 1:
        raise StateError(f"{prefix}.needs_user requires repair_count 1")
    repair_mode = item.get("repair_mode")
    if repair_mode not in (None, "edit", "regenerate"):
        raise StateError(f"{prefix}.repair_mode must be edit, regenerate or null")
    if repair_count == 1 and repair_mode is None:
        raise StateError(f"{prefix}.repair_mode is required after a repair")
    return repair_count, hard_failures, red_flags


def validate_candidate_files(
    project_dir: Path,
    item: dict[str, Any],
    prefix: str,
    status: str,
    repair_count: int,
    *,
    candidate_required: bool,
    allow_missing: bool = False,
) -> Path | None:
    candidate_raw = item.get("candidate")
    candidate: Path | None = None
    if candidate_raw is not None:
        candidate = resolve_project_path(project_dir, candidate_raw, f"{prefix}.candidate")
        if not allow_missing and not candidate.is_file():
            raise StateError(f"{prefix}.candidate does not exist: {candidate}")
    elif candidate_required:
        raise StateError(f"{prefix}.candidate is required for status {status}")

    final_source_raw = item.get("final_source")
    final_source: Path | None = None
    if status == "pass":
        final_source = resolve_project_path(
            project_dir, final_source_raw, f"{prefix}.final_source"
        )
        if not allow_missing and not final_source.is_file():
            raise StateError(f"{prefix}.final_source does not exist: {final_source}")
    elif final_source_raw is not None:
        raise StateError(f"{prefix}.final_source must be null unless status is pass")

    repair_file_raw = item.get("repair_file")
    if repair_count == 1:
        repair_file = resolve_project_path(
            project_dir, repair_file_raw, f"{prefix}.repair_file"
        )
        if not allow_missing and not repair_file.is_file():
            raise StateError(f"{prefix}.repair_file does not exist: {repair_file}")
    elif repair_file_raw is not None:
        raise StateError(f"{prefix}.repair_file requires repair_count 1")
    return final_source


def validate_transport(transport: Any, prefix: str) -> None:
    if not isinstance(transport, dict):
        raise StateError(f"{prefix}.transport must be an object")
    backend = transport.get("backend")
    if not isinstance(backend, str) or not backend:
        raise StateError(f"{prefix}.transport.backend must be a non-empty string")
    route = transport.get("route")
    if route is not None and (not isinstance(route, str) or not route):
        raise StateError(f"{prefix}.transport.route must be null or a non-empty string")
    for field in ("attempts_total", "consecutive_failures"):
        require_nonnegative_int(transport.get(field), f"{prefix}.transport.{field}")
    for field in ("circuit_open", "probe_granted", "probe_in_flight"):
        if not isinstance(transport.get(field), bool):
            raise StateError(f"{prefix}.transport.{field} must be boolean")
    for field in (
        "last_error",
        "last_error_type",
        "error_fingerprint",
        "backend_error_key",
        "prompt_sha256",
    ):
        value = transport.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise StateError(f"{prefix}.transport.{field} must be null or a string")
    references = transport.get("reference_sha256")
    if not isinstance(references, list):
        raise StateError(f"{prefix}.transport.reference_sha256 must be an array")
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}] must be an object"
            )
        if not isinstance(reference.get("path"), str) or not reference["path"]:
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}].path is required"
            )
        digest = reference.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}].sha256 is invalid"
            )
    active_attempt = transport.get("active_attempt")
    if active_attempt is not None:
        if not isinstance(active_attempt, dict):
            raise StateError(f"{prefix}.transport.active_attempt must be an object or null")
        for field in ("attempt_id", "started_at", "request_fingerprint"):
            value = active_attempt.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(
                    f"{prefix}.transport.active_attempt.{field} is required"
                )
        if not isinstance(active_attempt.get("probe"), bool):
            raise StateError(
                f"{prefix}.transport.active_attempt.probe must be boolean"
            )
    history = transport.get("attempt_history", [])
    if not isinstance(history, list) or not all(
        isinstance(entry, dict) for entry in history
    ):
        raise StateError(f"{prefix}.transport.attempt_history must be an array")
    next_eligible = transport.get("next_eligible_at")
    if next_eligible is not None and (
        not isinstance(next_eligible, str) or not next_eligible
    ):
        raise StateError(
            f"{prefix}.transport.next_eligible_at must be null or a timestamp"
        )


def validate_v3_transport(transport: dict[str, Any], prefix: str) -> None:
    recovery = transport.get("recovery")
    if not isinstance(recovery, dict):
        raise StateError(f"{prefix}.transport.recovery must be an object in schema v3")
    if recovery.get("level") not in (0, 1, 2):
        raise StateError(f"{prefix}.transport.recovery.level must be 0, 1 or 2")
    if recovery.get("state") not in {"idle", "staging", "ready", "failed"}:
        raise StateError(
            f"{prefix}.transport.recovery.state must be idle, staging, ready or failed"
        )
    transaction = recovery.get("transaction")
    if transaction is not None and not isinstance(transaction, dict):
        raise StateError(f"{prefix}.transport.recovery.transaction must be an object or null")
    if recovery.get("state") == "staging" and not transaction:
        raise StateError(f"{prefix}.transport.recovery.staging requires a transaction")


def validate_manifest(
    project_dir: Path,
    artifacts: dict[str, Any],
    normalized: list[dict[str, Any]],
    phase: str,
) -> None:
    raw_manifest = artifacts.get("release_manifest")
    if not raw_manifest:
        if phase in FINAL_PHASES:
            raise StateError("final phases require artifacts.release_manifest")
        return
    manifest_path = resolve_project_path(
        project_dir, raw_manifest, "artifacts.release_manifest"
    )
    if not manifest_path.exists():
        if phase in FINAL_PHASES:
            raise StateError(f"release manifest does not exist: {manifest_path}")
        return

    release_images = load_release_manifest(manifest_path)
    pass_sources = {
        item["number"]: item["final_source"]
        for item in normalized
        if item["status"] == "pass"
    }
    manifest_numbers: list[int] = []
    for index, release_item in enumerate(release_images):
        number = release_item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(
                f"release_manifest.images[{index}].number must be a positive integer"
            )
        source = resolve_project_path(
            project_dir,
            release_item.get("source"),
            f"release_manifest.images[{index}].source",
        )
        if not source.is_file():
            raise StateError(f"release manifest source does not exist: {source}")
        if number in manifest_numbers:
            raise StateError(f"release manifest contains duplicate image {number}")
        manifest_numbers.append(number)
        if number not in pass_sources:
            raise StateError(f"release manifest image {number} is not marked pass")
        if source != pass_sources[number]:
            raise StateError(
                f"release manifest image {number} does not match final_source"
            )

    if phase in FINAL_PHASES:
        expected = [item["number"] for item in normalized]
        if sorted(manifest_numbers) != expected:
            raise StateError(
                "final release manifest must include every planned image exactly once"
            )


def validate_v1(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        prefix = f"images[{index}]"
        if not isinstance(item, dict):
            raise StateError(f"{prefix} must be an object")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(f"{prefix}.number must be a positive integer")
        verdict = item.get("verdict")
        if verdict not in V1_VERDICTS:
            raise StateError(f"{prefix}.verdict must be one of {sorted(V1_VERDICTS)}")
        repair_count, _, _ = validate_content_fields(item, prefix, verdict)
        final_source = validate_candidate_files(
            project_dir,
            item,
            prefix,
            verdict,
            repair_count,
            candidate_required=True,
        )
        normalized.append(
            {"number": number, "status": verdict, "final_source": final_source}
        )
    validate_numbering(normalized, len(normalized))
    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 1)


def validate_v2(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    planned_count = payload.get("planned_count")
    if (
        not isinstance(planned_count, int)
        or isinstance(planned_count, bool)
        or planned_count < 1
    ):
        raise StateError("planned_count must be a positive integer")

    backends = payload.get("transport_backends", {})
    if not isinstance(backends, dict):
        raise StateError("transport_backends must be an object")
    for name, backend in backends.items():
        if not isinstance(name, str) or not name or not isinstance(backend, dict):
            raise StateError("transport_backends entries must be named objects")
        if not isinstance(backend.get("circuit_open"), bool):
            raise StateError(
                f"transport_backends.{name}.circuit_open must be boolean"
            )
        affected = backend.get("affected_images", [])
        if not isinstance(affected, list) or not all(
            isinstance(number, int) and not isinstance(number, bool) and number > 0
            for number in affected
        ):
            raise StateError(
                f"transport_backends.{name}.affected_images must contain image numbers"
            )
        failure_window = backend.get("failure_window", [])
        if not isinstance(failure_window, list) or not all(
            isinstance(event, dict) for event in failure_window
        ):
            raise StateError(
                f"transport_backends.{name}.failure_window must be an array"
            )

    batch = payload.get("transport_batch")
    if batch is not None:
        if not isinstance(batch, dict):
            raise StateError("transport_batch must be an object")
        if batch.get("status") not in {"active", "stopped"}:
            raise StateError("transport_batch.status must be active or stopped")
        for field in ("success_count", "attempt_count"):
            require_nonnegative_int(batch.get(field), f"transport_batch.{field}")

    authorizations = payload.get("fallback_authorizations", {})
    if not isinstance(authorizations, dict):
        raise StateError("fallback_authorizations must be an object")
    for name, authorization in authorizations.items():
        if not isinstance(name, str) or not name or not isinstance(authorization, dict):
            raise StateError("fallback_authorizations entries must be named objects")
        if authorization.get("authorized") is not True:
            raise StateError(
                f"fallback_authorizations.{name}.authorized must be true"
            )
        if not isinstance(authorization.get("model"), str) or not authorization["model"]:
            raise StateError(f"fallback_authorizations.{name}.model is required")

    board_policy = payload.get("reference_board_policy")
    if board_policy is not None:
        if not isinstance(board_policy, dict):
            raise StateError("reference_board_policy must be an object")
        if board_policy.get("authorized") is not True:
            raise StateError("reference_board_policy.authorized must be true")
        if board_policy.get("timeout_seconds") != REFERENCE_BOARD_TIMEOUT_SECONDS:
            raise StateError(
                "reference_board_policy.timeout_seconds must equal 480"
            )
        if board_policy.get("source_count") != 2:
            raise StateError("reference_board_policy.source_count must equal 2")
        if board_policy.get("original_generation_only") is not True:
            raise StateError(
                "reference_board_policy.original_generation_only must be true"
            )
        for field in ("authorized_at", "snapshot_file"):
            value = board_policy.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(f"reference_board_policy.{field} is required")

    board_fallbacks = payload.get("reference_board_fallbacks", [])
    if not isinstance(board_fallbacks, list) or not all(
        isinstance(record, dict) for record in board_fallbacks
    ):
        raise StateError("reference_board_fallbacks must be an array")
    for index, record in enumerate(board_fallbacks):
        number = record.get("image_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(
                f"reference_board_fallbacks[{index}].image_number must be positive"
            )
        for field in ("staged_at", "archived_request", "request_file", "board"):
            value = record.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(
                    f"reference_board_fallbacks[{index}].{field} is required"
                )

    repair_policy = payload.get("repair_policy")
    if repair_policy is not None:
        if not isinstance(repair_policy, dict):
            raise StateError("repair_policy must be an object")
        if repair_policy.get("mode") != "deferred_user_approved":
            raise StateError(
                "repair_policy.mode must equal deferred_user_approved"
            )
        approved_numbers = repair_policy.get("approved_numbers", [])
        if not isinstance(approved_numbers, list) or not all(
            isinstance(number, int)
            and not isinstance(number, bool)
            and number > 0
            for number in approved_numbers
        ):
            raise StateError("repair_policy.approved_numbers must contain image numbers")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        prefix = f"images[{index}]"
        if not isinstance(item, dict):
            raise StateError(f"{prefix} must be an object")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(f"{prefix}.number must be a positive integer")
        status = item.get("status")
        if status not in V2_STATUSES:
            raise StateError(f"{prefix}.status must be one of {sorted(V2_STATUSES)}")
        repair_count, _, _ = validate_content_fields(item, prefix, status)
        recommendation = item.get("repair_recommendation")
        if recommendation is not None:
            if not isinstance(recommendation, dict):
                raise StateError(f"{prefix}.repair_recommendation must be an object")
            if recommendation.get("mode") not in {"edit", "regenerate"}:
                raise StateError(
                    f"{prefix}.repair_recommendation.mode must be edit or regenerate"
                )
            require_string_list(
                recommendation.get("issues", []),
                f"{prefix}.repair_recommendation.issues",
            )
        final_source = validate_candidate_files(
            project_dir,
            item,
            prefix,
            status,
            repair_count,
            candidate_required=status in {"review_pending", "pass", "needs_user"},
            allow_missing=bool(payload.get("_v6_relaxed_files")),
        )
        validate_transport(item.get("transport"), prefix)
        transport = item["transport"]
        if status == "transport_blocked" and not transport["circuit_open"]:
            raise StateError(
                f"{prefix}.transport_blocked requires transport.circuit_open"
            )
        if (
            status == "generating"
            and transport["circuit_open"]
            and not transport["probe_in_flight"]
        ):
            raise StateError(
                f"{prefix}.generating may keep a circuit open only for a probe"
            )
        if status == "generating" and transport.get("active_attempt") is None:
            # Legacy schema-v2 attempts are accepted so recover-interrupted can
            # safely close a turn that was aborted before attempt metadata existed.
            pass
        if status != "generating" and transport.get("active_attempt") is not None:
            raise StateError(
                f"{prefix}.transport.active_attempt requires generating status"
            )
        normalized.append(
            {"number": number, "status": status, "final_source": final_source}
        )

    reference_jobs = payload.get("reference_jobs", [])
    if not isinstance(reference_jobs, list):
        raise StateError("reference_jobs must be an array")
    seen_reference_ids: set[str] = set()
    for index, job in enumerate(reference_jobs):
        prefix = f"reference_jobs[{index}]"
        if not isinstance(job, dict):
            raise StateError(f"{prefix} must be an object")
        reference_id = job.get("id")
        if (
            not isinstance(reference_id, str)
            or not reference_id
            or not all(character.isalnum() or character in "-_" for character in reference_id)
        ):
            raise StateError(f"{prefix}.id must be a non-empty slug")
        if reference_id in seen_reference_ids:
            raise StateError(f"duplicate reference job id: {reference_id}")
        seen_reference_ids.add(reference_id)
        if job.get("kind") not in {"character", "location", "prop", "vehicle", "wonder"}:
            raise StateError(f"{prefix}.kind is invalid")
        status = job.get("status")
        if status not in V2_STATUSES:
            raise StateError(f"{prefix}.status must be one of {sorted(V2_STATUSES)}")
        candidate = job.get("candidate")
        if status in {"review_pending", "pass", "needs_user"} and not candidate:
            raise StateError(f"{prefix}.{status} requires a candidate")
        if candidate:
            resolve_project_path(project_dir, candidate, f"{prefix}.candidate")
            if not (project_dir / candidate).is_file():
                raise StateError(f"{prefix}.candidate does not exist: {candidate}")
        validate_transport(job.get("transport"), prefix)

    validate_numbering(normalized, planned_count)
    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 2)


def validate_v3(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = validate_v2(state_path, payload)
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    for name, backend in payload.get("transport_backends", {}).items():
        for index, event in enumerate(backend.get("failure_window", [])):
            prefix = f"transport_backends.{name}.failure_window[{index}]"
            if event.get("job_type") not in {"scene", "reference"}:
                raise StateError(f"{prefix}.job_type must be scene or reference")
            job_id = event.get("job_id")
            if not isinstance(job_id, (str, int)) or isinstance(job_id, bool):
                raise StateError(f"{prefix}.job_id must be a string or integer")
            for field in ("error_type", "error_key", "failed_at"):
                if not isinstance(event.get(field), str) or not event[field]:
                    raise StateError(f"{prefix}.{field} is required")
    for index, item in enumerate(payload["images"]):
        validate_v3_transport(item["transport"], f"images[{index}]")
    for index, job in enumerate(payload.get("reference_jobs", [])):
        prefix = f"reference_jobs[{index}]"
        validate_v3_transport(job["transport"], prefix)
        output_dir = resolve_project_path(project_dir, job.get("output_dir"), f"{prefix}.output_dir")
        try:
            output_dir.relative_to(project_dir)
        except ValueError as exc:
            raise StateError(f"{prefix}.output_dir must stay inside project_dir") from exc
        if not isinstance(job.get("candidate_versions"), list):
            raise StateError(f"{prefix}.candidate_versions must be an array")
        repair_count = require_nonnegative_int(
            job.get("content_repair_count"), f"{prefix}.content_repair_count"
        )
        if repair_count not in (0, 1):
            raise StateError(f"{prefix}.content_repair_count must be 0 or 1")
        require_string_list(job.get("review_issues", []), f"{prefix}.review_issues")
        approved = job.get("approved_candidate")
        if job.get("status") == "pass":
            if not approved or approved != job.get("candidate"):
                raise StateError(f"{prefix}.pass requires approved_candidate to equal candidate")
        elif approved is not None:
            raise StateError(f"{prefix}.approved_candidate requires pass status")
    result["schema_version"] = 3
    return result


def validate_v4(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    expected_state = project_dir / V4_ARTIFACTS["system_dir"] / "review-state.json"
    if state_path.resolve() != expected_state.resolve():
        raise StateError(f"schema v4 state must be stored at {expected_state}")
    for key, default in V4_ARTIFACTS.items():
        value = artifacts.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StateError(f"schema v4 requires artifacts.{key}")
        if key != "release_dir" and value != default:
            raise StateError(f"schema v4 artifacts.{key} must equal {default}")

    allowed_root_documents = {
        V4_ARTIFACTS["story"], V4_ARTIFACTS["storyboard"], V4_ARTIFACTS["publication"]
    }
    loose_documents = [
        path.name for path in project_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
        and path.name not in allowed_root_documents
    ]
    if loose_documents:
        raise StateError(
            "schema v4 root contains unclassified Markdown/JSON files: "
            + ", ".join(sorted(loose_documents))
        )

    planned_count = payload.get("planned_count")
    if planned_count is None:
        if images:
            raise StateError("schema v4 story stage must not create image tasks before the storyboard")
        if payload.get("reference_jobs", []):
            raise StateError("schema v4 story stage must not create reference jobs before the storyboard")
        normalized: list[dict[str, Any]] = []
    else:
        if not isinstance(planned_count, int) or isinstance(planned_count, bool) or planned_count < 1:
            raise StateError("planned_count must be null before planning or a positive integer")
        compatibility = copy.deepcopy(payload)
        compatibility["schema_version"] = 3
        result = validate_v3(state_path, compatibility)
        normalized = [
            {
                "number": item["number"],
                "status": item["status"],
                "final_source": resolve_project_path(project_dir, item["final_source"], "final_source")
                if item.get("final_source") else None,
            }
            for item in payload["images"]
        ]
        result["schema_version"] = 4
    expected_release_dir = (
        V4_ARTIFACTS["release_dir"]
        if planned_count is None
        else f"04-最终发布版-{planned_count}图"
    )
    if artifacts.get("release_dir") != expected_release_dir:
        raise StateError(f"schema v4 artifacts.release_dir must equal {expected_release_dir}")

    approvals = payload.get("approvals")
    if not isinstance(approvals, dict):
        raise StateError("schema v4 approvals must be an object")
    story_required = phase not in {"drafting", "story_self_review", "awaiting_story_approval", "needs_user"}
    storyboard_required = phase in {
        "reference_self_review", "awaiting_reference_approval", "scene_self_review",
        "awaiting_repair_approval", "repairing", "final_self_review", "complete",
    }
    references_required = phase in {
        "scene_self_review", "awaiting_repair_approval", "repairing",
        "final_self_review", "complete",
    }
    if story_required:
        validate_approval_hash(project_dir, approvals.get("story"), "story")
    if storyboard_required:
        storyboard = approvals.get("storyboard")
        if not isinstance(storyboard, dict):
            raise StateError("approvals.storyboard must be an object")
        validate_approval_hash(project_dir, storyboard.get("public"), "storyboard.public")
        validate_approval_hash(project_dir, storyboard.get("production"), "storyboard.production")
        numbers = parse_storyboard(artifact_path(project_dir, payload, "storyboard"))
        if planned_count != len(numbers):
            raise StateError("approved professional storyboard count must equal planned_count")
    if references_required:
        references = approvals.get("references")
        if not isinstance(references, dict) or not isinstance(references.get("assets"), list):
            raise StateError("formal generation requires approvals.references.assets")
        for index, record in enumerate(references["assets"]):
            validate_approval_hash(project_dir, record, f"references.assets[{index}]")

    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 4)


def validate_v5(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    if not payload.get("_v6_allow_unreviewed_pass") and any(
        item.get("status") == "candidate_ready" for item in images
    ):
        raise StateError("schema v5 does not support candidate_ready")
    expected_state = project_dir / V5_ARTIFACTS["system_dir"] / "review-state.json"
    if state_path.resolve() != expected_state.resolve():
        raise StateError(f"schema v5 state must be stored at {expected_state}")
    for key, default in V5_ARTIFACTS.items():
        value = artifacts.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StateError(f"schema v5 requires artifacts.{key}")
        if key != "release_dir" and value != default:
            raise StateError(f"schema v5 artifacts.{key} must equal {default}")

    allowed_root_documents = {
        V5_ARTIFACTS["realism_plan"], V5_ARTIFACTS["story"],
        V5_ARTIFACTS["storyboard"], V5_ARTIFACTS["publication"],
    }
    loose_documents = [
        path.name for path in project_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
        and path.name not in allowed_root_documents
    ]
    if loose_documents:
        raise StateError(
            "schema v5 root contains unclassified Markdown/JSON files: "
            + ", ".join(sorted(loose_documents))
        )

    approvals = payload.get("approvals")
    if not isinstance(approvals, dict):
        raise StateError("schema v5 approvals must be an object")
    if phase == "awaiting_realism_approval":
        try:
            authenticity.parse_realism_plan(artifact_path(project_dir, payload, "realism_plan"))
        except authenticity.AuthenticityError as exc:
            raise StateError(str(exc)) from exc
    realism_required = phase not in {"realism_self_review", "awaiting_realism_approval", "needs_user"}
    if realism_required:
        validate_approval_hash(project_dir, approvals.get("realism"), "realism")
        try:
            authenticity.parse_realism_plan(artifact_path(project_dir, payload, "realism_plan"))
        except authenticity.AuthenticityError as exc:
            raise StateError(str(exc)) from exc
    if phase in {"realism_self_review", "awaiting_realism_approval"}:
        if (project_dir / V5_ARTIFACTS["story"]).exists():
            raise StateError("schema v5 must not create the story before realism approval")

    planned_count = payload.get("planned_count")
    if planned_count is None:
        if images or payload.get("reference_jobs", []):
            raise StateError("schema v5 must not create image/reference tasks before storyboard registration")
        normalized: list[dict[str, Any]] = []
    else:
        if not isinstance(planned_count, int) or isinstance(planned_count, bool) or planned_count < 1:
            raise StateError("planned_count must be null or a positive integer")
        compatibility = copy.deepcopy(payload)
        compatibility["schema_version"] = 3
        validate_v3(state_path, compatibility)
        normalized = [
            {
                "number": item["number"],
                "status": item["status"],
                "final_source": resolve_project_path(project_dir, item["final_source"], "final_source")
                if item.get("final_source") else None,
            }
            for item in images
        ]
        for index, item in enumerate(images):
            versions = item.get("candidate_versions")
            if not isinstance(versions, list):
                raise StateError(f"images[{index}].candidate_versions must be an array")
            if item.get("status") == "pass" and not payload.get("_v6_allow_unreviewed_pass"):
                if not versions or not isinstance(versions[-1].get("review"), dict):
                    raise StateError(f"images[{index}].pass requires a structured current-version review")
                failures = authenticity.hard_failures(versions[-1]["review"])
                if failures:
                    raise StateError(f"images[{index}] cannot pass a failed authenticity review")

    expected_release_dir = (
        V5_ARTIFACTS["release_dir"] if planned_count is None
        else f"04-最终发布版-{planned_count}图"
    )
    if artifacts.get("release_dir") != expected_release_dir:
        raise StateError(f"schema v5 artifacts.release_dir must equal {expected_release_dir}")

    story_required = phase not in {
        "realism_self_review", "awaiting_realism_approval", "drafting",
        "story_self_review", "awaiting_story_approval", "needs_user",
    }
    storyboard_required = phase in {
        "reference_self_review", "awaiting_reference_approval", "calibration_self_review",
        "awaiting_calibration_approval", "scene_self_review", "awaiting_repair_approval",
        "repairing", "final_self_review", "complete",
    }
    references_required = phase in {
        "calibration_self_review", "awaiting_calibration_approval", "scene_self_review",
        "awaiting_repair_approval", "repairing", "final_self_review", "complete",
    }
    calibration_required = phase in {
        "scene_self_review", "awaiting_repair_approval", "repairing", "final_self_review", "complete",
    }
    if story_required:
        validate_approval_hash(project_dir, approvals.get("story"), "story")
    if storyboard_required:
        storyboard = approvals.get("storyboard")
        if not isinstance(storyboard, dict):
            raise StateError("approvals.storyboard must be an object")
        validate_approval_hash(project_dir, storyboard.get("public"), "storyboard.public")
        validate_approval_hash(project_dir, storyboard.get("production"), "storyboard.production")
        rows = parse_v5_production_rows(artifact_path(project_dir, payload, "ai_storyboard"))
        plan = authenticity.parse_realism_plan(artifact_path(project_dir, payload, "realism_plan"))
        calibration_numbers = authenticity.validate_storyboard_capture_rows(rows, plan)
        if payload.get("calibration_numbers") != calibration_numbers:
            raise StateError("calibration_numbers must match the three storyboard calibration roles")
    if references_required:
        references = approvals.get("references")
        if not isinstance(references, dict) or not isinstance(references.get("assets"), list):
            raise StateError("calibration/formal generation requires approved references")
        for index, record in enumerate(references["assets"]):
            validate_approval_hash(project_dir, record, f"references.assets[{index}]")
    if phase == "awaiting_calibration_approval":
        submission = payload.get("calibration_submission")
        if not isinstance(submission, dict):
            raise StateError("awaiting calibration approval requires calibration_submission")
        validate_approval_hash(project_dir, submission.get("contact_sheet"), "calibration_submission.contact_sheet")
    if calibration_required:
        calibration = approvals.get("calibration")
        if not isinstance(calibration, dict):
            raise StateError("formal generation requires approvals.calibration")
        validate_approval_hash(project_dir, calibration.get("contact_sheet"), "calibration.contact_sheet")
        records = calibration.get("images")
        if not isinstance(records, list) or len(records) != 3:
            raise StateError("approvals.calibration.images must contain three records")
        for index, record in enumerate(records):
            validate_approval_hash(project_dir, record, f"calibration.images[{index}]")

    validate_final_requirements(phase, normalized, artifacts, project_dir, blocking_reasons)
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 5)


TEXT_REVISION_KEYS = ("story", "publication", "ai_storyboard")


def json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def text_revision_preservation(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts", {})
    release_manifest = artifact_path(project_dir, payload, "release_manifest")
    release_dir = resolve_project_path(project_dir, artifacts.get("release_dir"), "artifacts.release_dir")
    references_dir = artifact_path(project_dir, payload, "references_dir")
    return {
        "images_state_sha256": json_digest(payload.get("images", [])),
        "reference_jobs_sha256": json_digest(payload.get("reference_jobs", [])),
        "release_manifest_sha256": sha256_file(release_manifest),
        "release_files": tree_hashes(release_dir),
        "reference_files": tree_hashes(references_dir),
    }


def mask_storyboard_captions(text: str) -> str:
    lines: list[str] = []
    header: tuple[str, ...] | None = None
    caption_index: int | None = None
    table_row_index = 0
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if table_row_index == 0:
                header = tuple(cells)
                caption_index = header.index("发布字幕") if "发布字幕" in header else None
            elif table_row_index >= 2 and caption_index is not None and len(cells) == len(header or ()):
                cells[caption_index] = "<PUBLICATION_CAPTION>"
                newline = "\n" if line.endswith("\n") else ""
                line = "| " + " | ".join(cells) + " |" + newline
            table_row_index += 1
        lines.append(line)
    return "".join(lines)


def validate_active_text_revision(state_path: Path, payload: dict[str, Any]) -> None:
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    revision = payload.get("text_revision")
    if not isinstance(revision, dict) or revision.get("status") != "active":
        raise StateError("text revision phase requires an active text_revision record")
    backup_dir = resolve_project_path(project_dir, revision.get("backup_dir"), "text_revision.backup_dir")
    base = revision.get("base")
    if not isinstance(base, dict):
        raise StateError("text_revision.base must be an object")
    for key in TEXT_REVISION_KEYS:
        record = base.get(key)
        if not isinstance(record, dict):
            raise StateError(f"text_revision.base.{key} must be an object")
        backup = backup_dir / record.get("backup_name", "")
        if not backup.is_file() or sha256_file(backup) != record.get("sha256"):
            raise StateError(f"text revision backup is missing or changed: {key}")
    current = text_revision_preservation(project_dir, payload)
    if current != revision.get("preservation"):
        raise StateError("text revision changed protected image, reference, manifest, or release data")
    base_storyboard = backup_dir / base["ai_storyboard"]["backup_name"]
    current_storyboard = artifact_path(project_dir, payload, "ai_storyboard")
    if mask_storyboard_captions(base_storyboard.read_text(encoding="utf-8")) != mask_storyboard_captions(
        current_storyboard.read_text(encoding="utf-8")
    ):
        raise StateError("text revision may change only the publication-caption column in the AI storyboard")
    if payload.get("phase") == "awaiting_text_revision_approval":
        draft = revision.get("draft")
        if not isinstance(draft, dict):
            raise StateError("awaiting text revision approval requires draft hashes")
        for key in TEXT_REVISION_KEYS:
            path = artifact_path(project_dir, payload, key)
            if draft.get(key, {}).get("sha256") != sha256_file(path):
                raise StateError("submitted text changed; return to text_revision_self_review")


def validate_v6(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate schema v6 while preserving the v5 authenticity contract."""
    compatibility = copy.deepcopy(payload)
    compatibility["schema_version"] = 5
    compatibility["_v6_allow_unreviewed_pass"] = True
    compatibility["_v6_relaxed_files"] = True
    for item in compatibility.get("images", []):
        if item.get("status") == "candidate_ready":
            item["status"] = "review_pending"
    if compatibility.get("phase") in {
        "scene_generation", "awaiting_first_review_decision", "final_self_review"
    }:
        compatibility["phase"] = "scene_self_review"
    if compatibility.get("phase") in {"text_revision_self_review", "awaiting_text_revision_approval"}:
        project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
        compatibility["phase"] = "complete"
        compatibility.setdefault("approvals", {})["story"] = approval_record(
            artifact_path(project_dir, payload, "story")
        )
        compatibility.setdefault("approvals", {}).setdefault("storyboard", {})["production"] = approval_record(
            artifact_path(project_dir, payload, "ai_storyboard")
        )
    result = validate_v5(state_path, compatibility)
    if payload.get("phase") in {"text_revision_self_review", "awaiting_text_revision_approval"}:
        validate_active_text_revision(state_path, payload)
    if payload.get("phase") in FINAL_PHASES:
        non_passing = [
            item["number"] for item in payload.get("images", [])
            if item.get("status") != "pass"
        ]
        if non_passing:
            raise StateError(
                f"{payload.get('phase')} requires every image to pass; failing numbers: {non_passing}"
            )
    if payload.get("phase") == "complete":
        project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
        validate_manifest(project_dir, payload.get("artifacts", {}), [
            {
                "number": item["number"],
                "status": item["status"],
                "final_source": resolve_project_path(
                    project_dir, item.get("final_source"), "final_source"
                ) if item.get("final_source") else None,
            }
            for item in payload.get("images", [])
        ], "complete")
    result["schema_version"] = 6
    result["phase"] = payload.get("phase")
    return result


def validate_numbering(normalized: list[dict[str, Any]], planned_count: int) -> None:
    numbers = sorted(item["number"] for item in normalized)
    expected = list(range(1, planned_count + 1))
    if numbers != expected:
        raise StateError(
            f"image numbers must match planned_count and be contiguous; got {numbers}"
        )


def validate_final_requirements(
    phase: str,
    normalized: list[dict[str, Any]],
    artifacts: dict[str, Any],
    project_dir: Path,
    blocking_reasons: list[str],
) -> None:
    if phase in FINAL_PHASES:
        non_passing = [
            item["number"] for item in normalized if item["status"] != "pass"
        ]
        if non_passing:
            raise StateError(
                f"{phase} requires every image to pass; failing numbers: {non_passing}"
            )
    if phase == "complete":
        for key in REQUIRED_COMPLETE_ARTIFACTS:
            raw_path = artifacts.get(key)
            path = resolve_project_path(project_dir, raw_path, f"artifacts.{key}")
            if not path.is_file():
                raise StateError(f"complete state requires artifacts.{key}: {path}")
        if blocking_reasons:
            raise StateError("complete state cannot contain blocking_reasons")
    if phase == "needs_user" and not blocking_reasons:
        raise StateError("needs_user requires at least one blocking reason")


def summary(
    state_path: Path,
    project_dir: Path,
    phase: str,
    normalized: list[dict[str, Any]],
    blocking_reasons: list[str],
    schema_version: int,
) -> dict[str, Any]:
    return {
        "state_file": str(state_path.resolve()),
        "project_dir": str(project_dir),
        "schema_version": schema_version,
        "phase": phase,
        "image_count": len(normalized),
        "passing_count": sum(item["status"] == "pass" for item in normalized),
        "transport_blocked_count": sum(
            item["status"] == "transport_blocked" for item in normalized
        ),
        "blocking_count": len(blocking_reasons),
    }


def validate_state(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if not isinstance(payload, dict):
        raise StateError("review state must contain a JSON object")
    version = payload.get("schema_version")
    if version == 1:
        return validate_v1(state_path, payload)
    if version == 2:
        return validate_v2(state_path, payload)
    if version == 3:
        return validate_v3(state_path, payload)
    if version == 4:
        return validate_v4(state_path, payload)
    if version == 5:
        return validate_v5(state_path, payload)
    if version == 6:
        return validate_v6(state_path, payload)
    raise StateError("schema_version must equal 1, 2, 3, 4, 5 or 6")


def empty_transport() -> dict[str, Any]:
    return {
        "backend": "built_in_imagegen",
        "route": None,
        "attempts_total": 0,
        "consecutive_failures": 0,
        "last_error": None,
        "last_error_type": None,
        "error_fingerprint": None,
        "backend_error_key": None,
        "prompt_sha256": None,
        "reference_sha256": [],
        "circuit_open": False,
        "probe_granted": False,
        "probe_in_flight": False,
        "active_attempt": None,
        "attempt_history": [],
        "next_eligible_at": None,
        "reference_summary": {"count": 0, "total_bytes": 0, "images": []},
        "auto_recovery_level": 0,
        "auto_recovery_history": [],
        "backend_health_warning": False,
        "recovery": {
            "level": 0,
            "state": "idle",
            "transaction": None,
            "last_error": None,
        },
    }


def empty_image(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "status": "pending",
        "candidate": None,
        "hard_failures": [],
        "photo_red_flags": [],
        "repair_count": 0,
        "repair_mode": None,
        "repair_file": None,
        "final_source": None,
        "notes": "",
        "transport": empty_transport(),
    }


def empty_image_v5(number: int) -> dict[str, Any]:
    item = empty_image(number)
    item["candidate_versions"] = []
    return item


def init_project(project_dir: Path, schema_version: int = 5) -> dict[str, Any]:
    if schema_version not in {4, 5, 6}:
        raise StateError("init-project schema_version must be 4, 5 or 6")
    project_dir = project_dir.expanduser().resolve()
    if project_dir.exists() and any(project_dir.iterdir()):
        raise StateError(f"project directory is not empty: {project_dir}")
    project_dir.mkdir(parents=True, exist_ok=True)
    artifacts = V6_ARTIFACTS if schema_version == 6 else V5_ARTIFACTS if schema_version == 5 else V4_ARTIFACTS
    story = project_dir / artifacts["story"]
    realism = project_dir / artifacts.get("realism_plan", "00-真实性方案.md")
    system_dir = project_dir / artifacts["system_dir"]
    system_dir.mkdir(parents=True, exist_ok=True)
    if schema_version == 4 and not story.exists():
        atomic_write_text(story, "# 故事脚本\n")
    if schema_version in {5, 6}:
        atomic_write_text(realism, authenticity.realism_template())
    state_path = system_dir / "review-state.json"
    payload = {
        "schema_version": schema_version,
        "project_dir": "..",
        "phase": "realism_self_review" if schema_version in {5, 6} else "drafting",
        "planned_count": None,
        "max_repairs_per_item": 1,
        "repair_policy": {
            "mode": "deferred_user_approved",
            "approved_numbers": [],
        },
        "artifacts": copy.deepcopy(artifacts),
        "approvals": {},
        "images": [],
        "reference_jobs": [],
        "transport_backends": {},
        "transport_batch": None,
        "fallback_authorizations": {},
        "reference_board_policy": None,
        "reference_board_fallbacks": [],
        "calibration_numbers": [],
        "calibration_round": 0,
        "calibration_submission": None,
        "blocking_reasons": [],
    }
    atomic_write_json(state_path, payload)
    result = validate_state(state_path)
    response = {"command": "init-project", **result}
    if schema_version in {5, 6}:
        response["realism_file"] = str(realism)
    else:
        response["story_file"] = str(story)
    return response


def approve_realism(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-realism requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") not in {5, 6}:
        raise StateError("approve-realism is only available for schema v5/v6")
    if payload.get("phase") != "awaiting_realism_approval":
        raise StateError("realism may only be approved in awaiting_realism_approval")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    realism = artifact_path(project_dir, payload, "realism_plan")
    try:
        authenticity.parse_realism_plan(realism)
    except authenticity.AuthenticityError as exc:
        raise StateError(str(exc)) from exc
    record = approval_record(realism)
    record["path"] = str(realism.relative_to(project_dir))
    payload.setdefault("approvals", {})["realism"] = record
    story = artifact_path(project_dir, payload, "story")
    if not story.exists():
        atomic_write_text(story, "# 故事脚本\n")
    payload["phase"] = "drafting"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "approve-realism", "phase": payload["phase"], "approval": record, "story_file": str(story)}


def approve_story(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-story requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") not in {4, 5, 6}:
        raise StateError("approve-story is only available for schema v4/v5/v6")
    if payload.get("phase") != "awaiting_story_approval":
        raise StateError("story may only be approved in awaiting_story_approval")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    story = artifact_path(project_dir, payload, "story")
    record = approval_record(story)
    record["path"] = str(story.relative_to(project_dir))
    payload.setdefault("approvals", {})["story"] = record
    payload["phase"] = "plan_self_review"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "approve-story", "phase": payload["phase"], "approval": record}


def register_storyboard(state_path: Path, planned_count: int) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") not in {4, 5, 6}:
        raise StateError("register-storyboard is only available for schema v4/v5")
    if payload.get("phase") != "plan_self_review":
        raise StateError("storyboard may only be registered in plan_self_review")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    validate_approval_hash(project_dir, payload.get("approvals", {}).get("story"), "story")
    storyboard = artifact_path(project_dir, payload, "storyboard")
    numbers = parse_storyboard(storyboard)
    if planned_count != len(numbers):
        raise StateError(
            f"planned_count {planned_count} does not match professional storyboard rows {len(numbers)}"
        )
    production = artifact_path(project_dir, payload, "ai_storyboard")
    production_numbers, header = parse_production_storyboard(production)
    if production_numbers != numbers:
        raise StateError("AI production storyboard must contain the same ordered image numbers")
    payload["planned_count"] = planned_count
    if payload.get("schema_version") in {5, 6}:
        if header != V5_PRODUCTION_STORYBOARD_COLUMNS:
            raise StateError("schema v5 requires the exact v5 AI production storyboard")
        try:
            plan = authenticity.parse_realism_plan(artifact_path(project_dir, payload, "realism_plan"))
            rows = parse_v5_production_rows(production)
            payload["calibration_numbers"] = authenticity.validate_storyboard_capture_rows(rows, plan)
        except authenticity.AuthenticityError as exc:
            raise StateError(str(exc)) from exc
        payload["images"] = [empty_image_v5(number) for number in numbers]
    else:
        payload["images"] = [empty_image(number) for number in numbers]
    payload["artifacts"]["release_dir"] = f"04-最终发布版-{planned_count}图"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "register-storyboard",
        "planned_count": planned_count,
        "phase": payload["phase"],
    }


def approve_storyboard(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-storyboard requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") not in {4, 5, 6}:
        raise StateError("approve-storyboard is only available for schema v4/v5")
    if payload.get("phase") != "awaiting_storyboard_approval":
        raise StateError("storyboard may only be approved in awaiting_storyboard_approval")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    records: dict[str, Any] = {}
    for label, key in (("public", "storyboard"), ("production", "ai_storyboard")):
        path = artifact_path(project_dir, payload, key)
        record = approval_record(path)
        record["path"] = str(path.relative_to(project_dir))
        records[label] = record
    numbers = parse_storyboard(artifact_path(project_dir, payload, "storyboard"))
    if payload.get("planned_count") != len(numbers):
        raise StateError("professional storyboard changed after registration; register it again")
    production_numbers, _header = parse_production_storyboard(
        artifact_path(project_dir, payload, "ai_storyboard")
    )
    if production_numbers != numbers:
        raise StateError("AI production storyboard changed after registration; register it again")
    payload.setdefault("approvals", {})["storyboard"] = records
    payload["phase"] = "reference_self_review"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "approve-storyboard", "phase": payload["phase"], "approval": records}


def approve_references(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-references requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") not in {4, 5, 6}:
        raise StateError("approve-references is only available for schema v4/v5")
    if payload.get("phase") != "awaiting_reference_approval":
        raise StateError("references may only be approved in awaiting_reference_approval")
    unfinished = [
        job.get("id") for job in payload.get("reference_jobs", [])
        if job.get("status") != "pass"
    ]
    if unfinished:
        raise StateError(
            "all registered reference jobs must pass before reference approval: "
            + ", ".join(str(value) for value in unfinished)
        )
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    assets = []
    for job in payload.get("reference_jobs", []):
        candidate = resolve_project_path(
            project_dir, job.get("approved_candidate"),
            f"reference_jobs.{job.get('id')}.approved_candidate",
        )
        record = approval_record(candidate)
        record["path"] = str(candidate.relative_to(project_dir))
        record["reference_id"] = job.get("id")
        assets.append(record)
    payload.setdefault("approvals", {})["references"] = {
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
    }
    payload["phase"] = (
        "calibration_self_review" if payload.get("schema_version") in {5, 6}
        else "scene_self_review"
    )
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "approve-references",
        "phase": payload["phase"],
        "approved_reference_ids": [item["reference_id"] for item in assets],
    }


def submit_calibration(state_path: Path, contact_sheet: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") not in {5, 6}:
        raise StateError("submit-calibration is only available for schema v5/v6")
    if payload.get("phase") != "calibration_self_review":
        raise StateError("calibration may only be submitted in calibration_self_review")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    numbers = payload.get("calibration_numbers", [])
    if len(numbers) != 3:
        raise StateError("calibration requires exactly three storyboard images")
    items = [next(item for item in payload["images"] if item["number"] == number) for number in numbers]
    if any(item.get("status") != "pass" for item in items):
        raise StateError("all three calibration images must pass structured review before submission")
    contact_sheet = contact_sheet.expanduser().resolve()
    expected = artifact_path(project_dir, payload, "calibration_contact_sheet")
    if contact_sheet != expected or not contact_sheet.is_file():
        raise StateError(f"calibration contact sheet must exist at {expected}")
    sheet_record = approval_record(contact_sheet)
    sheet_record["path"] = str(contact_sheet.relative_to(project_dir))
    image_records = []
    for item in items:
        source = resolve_project_path(project_dir, item.get("final_source"), "calibration.final_source")
        record = approval_record(source)
        record["path"] = str(source.relative_to(project_dir))
        record["number"] = item["number"]
        image_records.append(record)
    payload["calibration_submission"] = {
        "contact_sheet": sheet_record,
        "images": image_records,
        "round": payload.get("calibration_round", 0),
    }
    payload["phase"] = "awaiting_calibration_approval"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "submit-calibration", "phase": payload["phase"], "numbers": numbers}


def approve_calibration(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-calibration requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") not in {5, 6}:
        raise StateError("approve-calibration is only available for schema v5/v6")
    if payload.get("phase") != "awaiting_calibration_approval":
        raise StateError("calibration may only be approved in awaiting_calibration_approval")
    submission = payload.get("calibration_submission")
    if not isinstance(submission, dict):
        raise StateError("calibration submission is missing")
    payload.setdefault("approvals", {})["calibration"] = copy.deepcopy(submission)
    payload["phase"] = "scene_generation" if payload.get("schema_version") == 6 else "scene_self_review"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "approve-calibration",
        "phase": payload["phase"],
        "approved_numbers": payload.get("calibration_numbers", []),
    }


def start_text_revision(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") != 6 or payload.get("phase") != "complete":
        raise StateError("start-text-revision requires a completed schema-v6 project")
    validate_state(state_path)
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = artifact_path(project_dir, payload, "backups_dir") / f"text-revision-{stamp}"
    if backup_dir.exists():
        raise StateError(f"text revision backup already exists: {backup_dir}")
    backup_dir.mkdir(parents=True)
    base: dict[str, Any] = {}
    for key in TEXT_REVISION_KEYS:
        source = artifact_path(project_dir, payload, key)
        backup_name = source.name
        shutil.copy2(source, backup_dir / backup_name)
        base[key] = {
            "path": str(source.relative_to(project_dir)),
            "backup_name": backup_name,
            "sha256": sha256_file(source),
        }
    shutil.copy2(state_path, backup_dir / "review-state.json")
    payload["text_revision"] = {
        "status": "active",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backup_dir": str(backup_dir.relative_to(project_dir)),
        "base": base,
        "preservation": text_revision_preservation(project_dir, payload),
        "draft": None,
    }
    payload["phase"] = "text_revision_self_review"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "start-text-revision", "phase": payload["phase"], "backup": str(backup_dir)}


def submit_text_revision(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") != 6 or payload.get("phase") != "text_revision_self_review":
        raise StateError("submit-text-revision requires text_revision_self_review")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    try:
        import text_audit
        audit = text_audit.audit_files(
            artifact_path(project_dir, payload, "story"),
            artifact_path(project_dir, payload, "publication"),
            artifact_path(project_dir, payload, "ai_storyboard"),
        )
    except (OSError, ValueError) as exc:
        raise StateError(str(exc)) from exc
    if audit["hard_errors"]:
        raise StateError("text audit hard errors: " + "; ".join(audit["hard_errors"]))
    validate_active_text_revision(state_path, payload)
    revision = payload["text_revision"]
    revision["draft"] = {
        key: {
            "path": artifact_relative(payload, key),
            "sha256": sha256_file(artifact_path(project_dir, payload, key)),
        }
        for key in TEXT_REVISION_KEYS
    }
    revision["audit"] = audit
    revision["submitted_at"] = datetime.now(timezone.utc).isoformat()
    payload["phase"] = "awaiting_text_revision_approval"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "submit-text-revision",
        "phase": payload["phase"],
        "warning_count": len(audit["warnings"]),
    }


def approve_text_revision(state_path: Path, user_approved: bool) -> dict[str, Any]:
    if not user_approved:
        raise StateError("approve-text-revision requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") != 6 or payload.get("phase") != "awaiting_text_revision_approval":
        raise StateError("approve-text-revision requires awaiting_text_revision_approval")
    validate_state(state_path)
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    revision = payload["text_revision"]
    approved_at = datetime.now(timezone.utc).isoformat()
    approved = copy.deepcopy(revision["draft"])
    for record in approved.values():
        record["approved_at"] = approved_at
    payload.setdefault("approvals", {})["story"] = approved["story"]
    payload.setdefault("approvals", {}).setdefault("storyboard", {})["production"] = approved["ai_storyboard"]
    payload.setdefault("approvals", {})["text_revision"] = {
        "base": copy.deepcopy(revision["base"]),
        "approved": approved,
        "approved_at": approved_at,
    }
    history = payload.setdefault("text_revision_history", [])
    finished = copy.deepcopy(revision)
    finished.update({"status": "approved", "approved_at": approved_at})
    history.append(finished)
    payload["text_revision"] = None
    payload["phase"] = "complete"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "approve-text-revision",
        "phase": "complete",
        "approved": approved,
    }


def revert_text_revision(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") != 6 or payload.get("phase") not in {
        "text_revision_self_review", "awaiting_text_revision_approval"
    }:
        raise StateError("revert-text-revision requires an active text revision")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    revision = payload.get("text_revision", {})
    backup_dir = resolve_project_path(project_dir, revision.get("backup_dir"), "text_revision.backup_dir")
    base = revision.get("base", {})
    for key in TEXT_REVISION_KEYS:
        record = base.get(key, {})
        shutil.copy2(backup_dir / record.get("backup_name", ""), artifact_path(project_dir, payload, key))
    original_state = load_json(backup_dir / "review-state.json")
    atomic_write_json(state_path, original_state)
    validate_state(state_path)
    return {"command": "revert-text-revision", "phase": "complete", "backup": str(backup_dir)}


def reopen_gate(state_path: Path, gate: str) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") not in {4, 5, 6}:
        raise StateError("reopen-gate is only available for schema v4/v5")
    allowed_gates = {"story", "storyboard"} | (
        {"realism", "calibration"} if payload.get("schema_version") in {5, 6} else set()
    )
    if gate not in allowed_gates:
        raise StateError("invalid gate for this schema")
    active = [
        str(item.get("number"))
        for item in payload.get("images", [])
        if item.get("transport", {}).get("active_attempt") is not None
    ] + [
        str(job.get("id"))
        for job in payload.get("reference_jobs", [])
        if job.get("transport", {}).get("active_attempt") is not None
    ]
    if active:
        raise StateError("cannot reopen an approval gate while generation is active: " + ", ".join(active))
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    backup_dir = artifact_path(project_dir, payload, "backups_dir")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"review-state-before-reopen-{gate}-{stamp}.json"
    atomic_write_json(backup, payload)
    approvals = payload.setdefault("approvals", {})
    if gate == "realism":
        if "realism" not in approvals:
            raise StateError("realism gate is not currently approved")
        story_path = artifact_path(project_dir, payload, "story")
        if story_path.is_file():
            archived_story = backup_dir / f"story-before-reopen-realism-{stamp}.md"
            shutil.copy2(story_path, archived_story)
            story_path.unlink()
        payload["approvals"] = {}
        payload["phase"] = "realism_self_review"
        payload["planned_count"] = None
        payload.setdefault("artifacts", {})["release_dir"] = "04-最终发布版-N图"
        payload["images"] = []
        payload["reference_jobs"] = []
        payload["transport_batch"] = None
        payload["transport_backends"] = {}
        payload["calibration_numbers"] = []
        payload["calibration_round"] = 0
        payload["calibration_submission"] = None
        payload["blocking_reasons"] = []
        atomic_write_json(state_path, payload)
        validate_state(state_path)
        return {"command": "reopen-gate", "gate": gate, "phase": payload["phase"], "backup": str(backup)}
    if gate == "calibration":
        if payload.get("phase") not in {"calibration_self_review", "awaiting_calibration_approval"}:
            raise StateError(
                "calibration may only be reopened after a failed self-review or while awaiting approval"
            )
        if payload.get("calibration_round", 0) >= 1:
            payload["phase"] = "needs_user"
            payload.setdefault("blocking_reasons", []).append("真实性校准第二次仍未获批准，需要用户调整方案")
            atomic_write_json(state_path, payload)
            validate_state(state_path)
            return {"command": "reopen-gate", "gate": gate, "phase": "needs_user", "backup": str(backup)}
        payload["calibration_round"] = 1
        payload["calibration_submission"] = None
        approvals.pop("calibration", None)
        numbers = set(payload.get("calibration_numbers", []))
        requests_dir = artifact_path(project_dir, payload, "requests_dir")
        for number in numbers:
            request = requests_dir / f"{number:02d}.json"
            if request.is_file():
                archived_request = backup_dir / f"calibration-round0-{number:02d}-request-{stamp}.json"
                shutil.copy2(request, archived_request)
                request.unlink()
        payload["images"] = [
            empty_image_v5(item["number"]) if item["number"] in numbers else item
            for item in payload["images"]
        ]
        payload["phase"] = "calibration_self_review"
        atomic_write_json(state_path, payload)
        validate_state(state_path)
        return {"command": "reopen-gate", "gate": gate, "phase": payload["phase"], "backup": str(backup)}
    approvals.pop("storyboard", None)
    approvals.pop("references", None)
    if gate == "story":
        approvals.pop("story", None)
        payload["phase"] = "story_self_review"
    else:
        payload["phase"] = "plan_self_review"
    payload["planned_count"] = None
    payload["images"] = []
    payload["reference_jobs"] = []
    payload["transport_batch"] = None
    payload["transport_backends"] = {}
    payload["artifacts"]["release_dir"] = (
        V5_ARTIFACTS["release_dir"] if payload.get("schema_version") in {5, 6}
        else V4_ARTIFACTS["release_dir"]
    )
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "reopen-gate",
        "gate": gate,
        "phase": payload["phase"],
        "backup": str(backup),
    }


def require_writable_state(payload: dict[str, Any], command: str) -> int:
    version = payload.get("schema_version")
    if version not in {2, 3, 4, 5, 6}:
        raise StateError(f"{command} requires schema_version 2, 3, 4, 5 or 6")
    if version == 2 and payload.get("phase") == "complete":
        raise StateError("completed schema v2 projects are read-only")
    return int(version)


def register_reference_job(
    state_path: Path, reference_id: str, kind: str, output_dir: str
) -> dict[str, Any]:
    payload = load_json(state_path)
    version = require_writable_state(payload, "register-reference-job")
    if payload.get("phase") != "reference_self_review":
        raise StateError("reference jobs may only be registered in reference_self_review")
    if not reference_id or not all(
        character.isalnum() or character in "-_" for character in reference_id
    ):
        raise StateError("reference-id must be a non-empty slug")
    jobs = payload.setdefault("reference_jobs", [])
    if any(job.get("id") == reference_id for job in jobs):
        raise StateError(f"reference job already exists: {reference_id}")
    if kind not in {"character", "location", "prop", "vehicle", "wonder"}:
        raise StateError("unsupported reference job kind")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    target_dir = resolve_project_path(project_dir, output_dir, "output_dir")
    target_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": reference_id,
        "kind": kind,
        "output_dir": str(target_dir.relative_to(project_dir)),
        "status": "pending",
        "candidate": None,
        "notes": "",
        "transport": empty_transport(),
    }
    if version in {3, 4, 5, 6}:
        job.update(
            {
                "candidate_versions": [],
                "content_repair_count": 0,
                "review_issues": [],
                "approved_candidate": None,
            }
        )
    jobs.append(job)
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "register-reference-job", **job}


def mark_reference_pass(
    state_path: Path, reference_id: str, notes: str
) -> dict[str, Any]:
    payload = load_json(state_path)
    version = require_writable_state(payload, "mark-reference-pass")
    if version in {3, 4, 5, 6}:
        result = record_reference_review(
            state_path, reference_id, "pass", [], notes
        )
        result["command"] = "mark-reference-pass"
        return result
    job = next(
        (
            candidate
            for candidate in payload.get("reference_jobs", [])
            if candidate.get("id") == reference_id
        ),
        None,
    )
    if job is None:
        raise StateError(f"reference job is not registered: {reference_id}")
    if job.get("status") != "review_pending" or not job.get("candidate"):
        raise StateError("reference job must have a review_pending candidate")
    job["status"] = "pass"
    job["notes"] = notes
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "mark-reference-pass",
        "reference_id": reference_id,
        "status": "pass",
        "candidate": job["candidate"],
    }


def record_reference_review(
    state_path: Path,
    reference_id: str,
    verdict: str,
    issues: list[str],
    notes: str,
) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") not in {3, 4, 5, 6}:
        raise StateError("record-reference-review requires schema_version 3, 4 or 5")
    if payload.get("phase") != "reference_self_review":
        raise StateError("reference review requires phase reference_self_review")
    job = next(
        (item for item in payload.get("reference_jobs", []) if item.get("id") == reference_id),
        None,
    )
    if job is None:
        raise StateError(f"reference job is not registered: {reference_id}")
    if job.get("status") != "review_pending" or not job.get("candidate"):
        raise StateError("reference job must have a review_pending candidate")
    candidate = str(job["candidate"])
    versions = job.setdefault("candidate_versions", [])
    version_number = int(job.get("content_repair_count", 0)) + 1
    existing = next(
        (entry for entry in versions if entry.get("candidate") == candidate), None
    )
    version_entry = existing or {"version": version_number, "candidate": candidate}
    version_entry.update(
        {
            "review": verdict,
            "issues": list(issues),
            "notes": notes,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if existing is None:
        versions.append(version_entry)
    if verdict == "pass":
        if issues:
            raise StateError("passing reference review cannot include issues")
        job.update(
            {
                "status": "pass",
                "approved_candidate": candidate,
                "review_issues": [],
                "notes": notes,
            }
        )
    elif verdict == "fail":
        if not issues:
            raise StateError("failed reference review requires at least one issue")
        job["review_issues"] = list(issues)
        job["approved_candidate"] = None
        job["notes"] = notes
        if int(job.get("content_repair_count", 0)) >= 1:
            job["status"] = "needs_user"
        else:
            project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
            request_path = requests_root(project_dir) / f"reference-{reference_id}.json"
            current = load_json(request_path)
            origin = current.get("content_repair_origin") or current
            origin_recovery = origin.get("auto_recovery_origin", {})
            references = origin_recovery.get("references") or origin.get("references", [])
            roles = origin_recovery.get("reference_roles") or origin.get("reference_roles", [])
            correction = "\n\nReference content correction (version 2):\n" + "\n".join(
                f"- {issue}" for issue in issues
            )
            prompt = str(origin.get("prompt", "")) + correction
            revised = copy.deepcopy(origin)
            revised.update(
                {
                    "prompt": prompt,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "references": copy.deepcopy(references),
                    "reference_roles": list(roles),
                    "content_repair_origin": copy.deepcopy(origin),
                    "content_repair": {
                        "version": 2,
                        "issues": list(issues),
                        "staged_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            revised.pop("auto_recovery", None)
            revised.pop("auto_recovery_origin", None)
            archive_index = 1
            while True:
                archive = request_path.with_name(
                    f"{request_path.stem}-content-v{archive_index}{request_path.suffix}"
                )
                if not archive.exists():
                    break
                archive_index += 1
            atomic_write_json(archive, current)
            atomic_write_json(request_path, revised)
            job.update(
                {
                    "status": "pending",
                    "candidate": None,
                    "content_repair_count": 1,
                }
            )
            transport = job["transport"]
            transport.update(
                {
                    "prompt_sha256": revised["prompt_sha256"],
                    "reference_sha256": [
                        {"path": entry.get("path"), "sha256": entry.get("sha256")}
                        for entry in references
                    ],
                    "auto_recovery_level": 0,
                    "auto_recovery_history": [],
                    "consecutive_failures": 0,
                    "last_error": None,
                    "last_error_type": None,
                    "circuit_open": False,
                    "next_eligible_at": None,
                    "recovery": {
                        "level": 0,
                        "state": "idle",
                        "transaction": None,
                        "last_error": None,
                    },
                }
            )
    else:
        raise StateError("reference review verdict must be pass or fail")
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-reference-review",
        "reference_id": reference_id,
        "verdict": verdict,
        "status": job["status"],
        "content_repair_count": job.get("content_repair_count", 0),
    }


def migrate_v1(payload: dict[str, Any], planned_count: int) -> dict[str, Any]:
    existing = payload.get("images")
    if not isinstance(existing, list):
        raise StateError("images must be an array")
    if planned_count < len(existing):
        raise StateError("planned_count cannot be smaller than existing image count")
    migrated = copy.deepcopy(payload)
    migrated["schema_version"] = 2
    migrated["planned_count"] = planned_count
    migrated["transport_backends"] = {}
    migrated["fallback_authorizations"] = {}
    images: list[dict[str, Any]] = []
    status_map = {
        "pending": "pending",
        "pass": "pass",
        "fail": "review_pending",
        "needs_user": "needs_user",
    }
    by_number = {item.get("number"): item for item in existing if isinstance(item, dict)}
    for number in range(1, planned_count + 1):
        if number in by_number:
            item = copy.deepcopy(by_number[number])
            verdict = item.pop("verdict", "pending")
            item["status"] = status_map.get(verdict, "pending")
            item["transport"] = empty_transport()
            if item.get("candidate") is None and item["status"] == "review_pending":
                item["status"] = "pending"
            images.append(item)
        else:
            images.append(
                {
                    "number": number,
                    "status": "pending",
                    "candidate": None,
                    "hard_failures": [],
                    "photo_red_flags": [],
                    "repair_count": 0,
                    "repair_mode": None,
                    "repair_file": None,
                    "final_source": None,
                    "notes": "",
                    "transport": empty_transport(),
                }
            )
    migrated["images"] = images
    return migrated


def _migrate_transport_v3(raw: Any) -> dict[str, Any]:
    transport = copy.deepcopy(raw) if isinstance(raw, dict) else empty_transport()
    defaults = empty_transport()
    for key, value in defaults.items():
        transport.setdefault(key, copy.deepcopy(value))
    level = int(transport.get("auto_recovery_level", 0))
    recovery = transport.setdefault("recovery", {})
    recovery.setdefault("level", level)
    recovery.setdefault("state", "ready" if level else "idle")
    recovery.setdefault("transaction", None)
    recovery.setdefault("last_error", None)
    return transport


def migrate_v2(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 2:
        raise StateError("schema v3 migration requires a schema v2 state")
    if payload.get("phase") == "complete":
        raise StateError("completed schema v2 projects remain read-only and are not migrated")
    migrated = copy.deepcopy(payload)
    migrated["schema_version"] = 3
    migrated.setdefault("reference_jobs", [])
    migrated.setdefault("transport_batch", None)
    migrated.setdefault("reference_board_policy", None)
    migrated.setdefault("reference_board_fallbacks", [])
    for item in migrated.get("images", []):
        item["transport"] = _migrate_transport_v3(item.get("transport"))
    for job in migrated.get("reference_jobs", []):
        job["transport"] = _migrate_transport_v3(job.get("transport"))
        versions = job.setdefault("candidate_versions", [])
        candidate = job.get("candidate")
        if candidate and not versions:
            versions.append({"version": 1, "candidate": candidate, "review": None})
        job.setdefault("content_repair_count", 0)
        job.setdefault("review_issues", [])
        job.setdefault(
            "approved_candidate", candidate if job.get("status") == "pass" else None
        )
    for backend in migrated.setdefault("transport_backends", {}).values():
        normalized = []
        for event in backend.get("failure_window", []):
            if "job_type" in event and "job_id" in event:
                normalized.append(event)
            elif event.get("job_key"):
                raw_key = str(event["job_key"])
                job_type, _, job_id = raw_key.partition(":")
                normalized.append(
                    {**event, "job_type": job_type or "reference", "job_id": job_id}
                )
            else:
                normalized.append(
                    {**event, "job_type": "scene", "job_id": event.get("image_number")}
                )
            normalized[-1].pop("job_key", None)
            normalized[-1].pop("image_number", None)
        backend["failure_window"] = normalized
        backend.setdefault("circuit_open", False)
        backend.setdefault("affected_images", [])
    return migrated


def mark_pass(
    state_path: Path,
    number: int,
    notes: str,
    red_flags: list[str],
    review_file: Path | None = None,
) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "mark-pass")
    item = next(
        (candidate for candidate in payload["images"] if candidate.get("number") == number),
        None,
    )
    if item is None:
        raise StateError(f"image {number} is not present in planned images")
    if item.get("status") != "review_pending":
        raise StateError(f"image {number} must be review_pending before mark-pass")
    if payload.get("schema_version") in {5, 6}:
        if review_file is None:
            raise StateError("schema v5 mark-pass requires --review-file")
        project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
        review_path = review_file.expanduser().resolve()
        reviews_root = artifact_path(project_dir, payload, "authenticity_reviews_dir")
        try:
            review_path.relative_to(reviews_root)
        except ValueError as exc:
            raise StateError(f"review file must stay inside {reviews_root}") from exc
        try:
            review = authenticity.load_review(review_path)
        except authenticity.AuthenticityError as exc:
            raise StateError(str(exc)) from exc
        failures = authenticity.hard_failures(review)
        versions = item.get("candidate_versions", [])
        if not versions:
            raise StateError(f"image {number} has no candidate version to review")
        current_source = item.get("repair_file") if item.get("repair_count") == 1 else item.get("candidate")
        if versions[-1].get("candidate") != current_source:
            raise StateError(f"image {number} current candidate version does not match passing source")
        record = approval_record(review_path)
        record["path"] = str(review_path.relative_to(project_dir))
        versions[-1]["review"] = copy.deepcopy(review)
        versions[-1]["review_record"] = record
        item["photo_red_flags"] = list(review.get("red_flags", []))
        item["notes"] = review["notes"]
        if failures:
            item["hard_failures"] = failures
            if item.get("repair_count") == 1:
                item["status"] = "needs_user"
                reason = f"图{number:02d}返修后仍有硬失败: " + "；".join(failures)
                if reason not in payload.setdefault("blocking_reasons", []):
                    payload["blocking_reasons"].append(reason)
                payload["phase"] = "needs_user"
            elif (
                payload.get("schema_version") in {5, 6}
                and payload.get("phase") == "calibration_self_review"
                and payload.get("calibration_round", 0) >= 1
                and number in payload.get("calibration_numbers", [])
            ):
                item["repair_count"] = 1
                item["repair_mode"] = "regenerate"
                item["repair_file"] = item.get("candidate")
                item["status"] = "needs_user"
                reason = f"图{number:02d}第二轮真实性校准仍有硬失败: " + "；".join(failures)
                if reason not in payload.setdefault("blocking_reasons", []):
                    payload["blocking_reasons"].append(reason)
                payload["phase"] = "needs_user"
            atomic_write_json(state_path, payload)
            validate_state(state_path)
            return {
                "command": "mark-pass",
                "image_number": number,
                "status": item["status"],
                "hard_failures": failures,
            }
        if item.get("hard_failures"):
            raise StateError(
                f"image {number} current candidate retains hard failures; record a new candidate version before passing"
            )
        red_flags = list(review.get("red_flags", []))
        notes = review["notes"]
    if len(red_flags) >= 3:
        raise StateError("mark-pass accepts fewer than three photo red flags")
    final_source = (
        item.get("repair_file")
        if item.get("repair_count") == 1
        else item.get("candidate")
    )
    if not final_source:
        raise StateError(f"image {number} has no passing source")
    item.update(
        {
            "status": "pass",
            "photo_red_flags": red_flags,
            "final_source": final_source,
            "notes": notes,
        }
    )
    atomic_write_json(state_path, payload)
    return validate_state(state_path)


def queue_repair(
    state_path: Path,
    number: int,
    mode: str,
    issues: list[str],
    notes: str,
    red_flags: list[str],
) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "queue-repair")
    if payload.get("repair_policy", {}).get("mode") != "deferred_user_approved":
        raise StateError("queue-repair requires deferred_user_approved repair_policy")
    item = next(
        (candidate for candidate in payload["images"] if candidate.get("number") == number),
        None,
    )
    if item is None or item.get("status") != "review_pending":
        raise StateError(f"image {number} must be review_pending before queue-repair")
    if item.get("repair_count") != 0 or not item.get("candidate"):
        raise StateError(f"image {number} must have an unmodified original candidate")
    if not issues:
        raise StateError("queue-repair requires at least one issue")
    item["hard_failures"] = list(issues)
    item["photo_red_flags"] = list(red_flags)
    item["notes"] = notes
    item["repair_recommendation"] = {
        "mode": mode,
        "issues": list(issues),
        "notes": notes,
    }
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "queue-repair",
        "image_number": number,
        "mode": mode,
        "issues": issues,
    }


def prepare_repair_report(state_path: Path, output: Path | None) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "prepare-repair-report")
    if payload.get("repair_policy", {}).get("mode") != "deferred_user_approved":
        raise StateError(
            "prepare-repair-report requires deferred_user_approved repair_policy"
        )
    if payload.get("phase") != "scene_self_review":
        raise StateError("repair report can only be prepared after scene self-review")
    missing_originals = [
        item["number"] for item in payload["images"] if not item.get("candidate")
    ]
    if missing_originals:
        raise StateError(
            f"all original candidates are required before repair reporting: {missing_originals}"
        )
    unreviewed = [
        item["number"]
        for item in payload["images"]
        if item.get("status") == "review_pending"
        and item.get("repair_recommendation") is None
    ]
    if unreviewed:
        raise StateError(f"images still need content review: {unreviewed}")
    queued = [
        item for item in payload["images"] if item.get("repair_recommendation")
    ]
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    report_path = output.expanduser() if output else artifact_path(
        project_dir, payload, "repair_report", "返修报告.md"
    )
    if not report_path.is_absolute():
        report_path = project_dir / report_path
    report_path = report_path.resolve()
    try:
        report_relative = str(report_path.relative_to(project_dir))
    except ValueError as exc:
        raise StateError("repair report must be written inside the project") from exc
    lines = [
        "# 返修报告",
        "",
        "全部正式原图已经生成并完成首轮内容审查。以下项目等待用户确认后统一返修。",
        "",
        "| 图号 | 建议方式 | 问题 | 审查说明 |",
        "|---|---|---|---|",
    ]
    for item in queued:
        rec = item["repair_recommendation"]
        issues = "；".join(rec["issues"]).replace("|", "｜")
        notes = rec.get("notes", "").replace("|", "｜")
        lines.append(f"| {item['number']:02d} | {rec['mode']} | {issues} | {notes} |")
    if not queued:
        lines.append("| — | — | 无需返修 | 全部原图通过 |")
    lines.extend(
        [
            "",
            "## 用户确认",
            "",
            "返修不会自动开始。用户可批准全部或指定图号；未获批准的图片保持原图和审查结论不变。",
            "",
        ]
    )
    atomic_write_text(report_path, "\n".join(lines))
    policy = payload["repair_policy"]
    policy.update(
        {
            "report_file": report_relative,
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            "approved_numbers": [],
            "approved_at": None,
        }
    )
    payload.setdefault("artifacts", {})["repair_report"] = report_relative
    payload["phase"] = (
        "awaiting_repair_approval" if queued else "final_self_review"
    )
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "prepare-repair-report",
        "report_file": str(report_path),
        "queued_numbers": [item["number"] for item in queued],
        "phase": payload["phase"],
    }


def authorize_repairs(
    state_path: Path, numbers: list[int], user_approved: bool
) -> dict[str, Any]:
    if not user_approved:
        raise StateError("authorize-repairs requires explicit --user-approved")
    payload = load_json(state_path)
    require_writable_state(payload, "authorize-repairs")
    if payload.get("phase") != "awaiting_repair_approval":
        raise StateError("repairs may only be authorized after the repair report")
    queued = {
        item["number"]
        for item in payload["images"]
        if item.get("repair_recommendation")
    }
    selected = set(numbers) if numbers else queued
    if not selected or not selected.issubset(queued):
        raise StateError("authorized repair numbers must be present in the report")
    policy = payload.get("repair_policy", {})
    policy["approved_numbers"] = sorted(selected)
    policy["approved_at"] = datetime.now(timezone.utc).isoformat()
    payload["phase"] = "repairing"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "authorize-repairs",
        "approved_numbers": sorted(selected),
        "phase": "repairing",
    }


def submit_originals_overview(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") != 6:
        raise StateError("submit-originals-overview is only available for schema v6")
    if payload.get("phase") != "scene_generation":
        raise StateError("originals overview may only be submitted in scene_generation")
    missing = [item["number"] for item in payload["images"] if not item.get("candidate")]
    if missing:
        raise StateError(f"all original candidate paths are required before overview: {missing}")
    unfinished = [
        item["number"] for item in payload["images"]
        if item.get("status") not in {"pass", "candidate_ready"}
    ]
    if unfinished:
        raise StateError(f"all original generations must finish before overview: {unfinished}")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    output = artifact_path(project_dir, payload, "originals_overview")
    from originals_overview import render_overview
    render_overview(payload, project_dir, output)
    payload["phase"] = "awaiting_first_review_decision"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "submit-originals-overview",
        "phase": payload["phase"],
        "overview": str(output),
    }


def choose_first_review(
    state_path: Path, mode: str, numbers: list[int], user_approved: bool
) -> dict[str, Any]:
    if not user_approved:
        raise StateError("choose-first-review requires explicit --user-approved")
    payload = load_json(state_path)
    if payload.get("schema_version") != 6:
        raise StateError("choose-first-review is only available for schema v6")
    if payload.get("phase") != "awaiting_first_review_decision":
        raise StateError("first review may only be chosen after originals overview")
    calibration = set(payload.get("calibration_numbers", []))
    originals = {item["number"] for item in payload["images"] if item["number"] not in calibration}
    selected = set(numbers)
    if mode == "selected":
        if not selected or not selected.issubset(originals):
            raise StateError("selected review numbers must be non-calibration originals")
    elif selected:
        raise StateError("--number is only valid with --mode selected")
    review_numbers = originals if mode == "full" else selected if mode == "selected" else set()
    for item in payload["images"]:
        if item["number"] in calibration:
            continue
        if item.get("status") != "candidate_ready" or not item.get("candidate"):
            raise StateError(f"image {item['number']} is not ready for first-review choice")
        if item["number"] in review_numbers:
            item["status"] = "review_pending"
        else:
            item["status"] = "pass"
            item["final_source"] = item["candidate"]
            item["hard_failures"] = []
            item["photo_red_flags"] = []
            item["notes"] = ""
    payload["phase"] = "scene_self_review" if review_numbers else "final_self_review"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "choose-first-review",
        "mode": mode,
        "review_numbers": sorted(review_numbers),
        "phase": payload["phase"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser(
        "init-project", help="Create one clean schema-v6 project and its realism-stage state"
    )
    initialize.add_argument("--project-dir", required=True, type=Path)
    initialize.add_argument("--schema-version", type=int, choices=(4, 5, 6), default=6)
    validate = subparsers.add_parser("validate", help="Validate a review-state file")
    validate.add_argument("--state", required=True, type=Path)
    transition = subparsers.add_parser(
        "transition", help="Move a valid state file to the next legal phase"
    )
    transition.add_argument("--state", required=True, type=Path)
    transition.add_argument("--to", required=True, choices=sorted(PHASES))
    transition.add_argument(
        "--user-approved",
        action="store_true",
        help="Confirm explicit user approval when crossing an approval gate.",
    )
    realism_approval = subparsers.add_parser(
        "approve-realism", help="Approve the schema-v5 realism plan and create the story file"
    )
    realism_approval.add_argument("--state", required=True, type=Path)
    realism_approval.add_argument("--user-approved", action="store_true")
    story_approval = subparsers.add_parser(
        "approve-story", help="Approve the current user-edited story and open planning"
    )
    story_approval.add_argument("--state", required=True, type=Path)
    story_approval.add_argument("--user-approved", action="store_true")
    storyboard_register = subparsers.add_parser(
        "register-storyboard", help="Validate both storyboards and create image tasks"
    )
    storyboard_register.add_argument("--state", required=True, type=Path)
    storyboard_register.add_argument("--planned-count", required=True, type=int)
    storyboard_approval = subparsers.add_parser(
        "approve-storyboard", help="Approve public and AI production storyboards"
    )
    storyboard_approval.add_argument("--state", required=True, type=Path)
    storyboard_approval.add_argument("--user-approved", action="store_true")
    reference_approval = subparsers.add_parser(
        "approve-references", help="Approve all passing reference assets for formal generation"
    )
    reference_approval.add_argument("--state", required=True, type=Path)
    reference_approval.add_argument("--user-approved", action="store_true")
    calibration_submit = subparsers.add_parser(
        "submit-calibration", help="Submit three passing calibration images and their contact sheet"
    )
    calibration_submit.add_argument("--state", required=True, type=Path)
    calibration_submit.add_argument("--contact-sheet", required=True, type=Path)
    calibration_approval = subparsers.add_parser(
        "approve-calibration", help="Approve the three schema-v5 calibration images"
    )
    calibration_approval.add_argument("--state", required=True, type=Path)
    calibration_approval.add_argument("--user-approved", action="store_true")
    text_revision_start = subparsers.add_parser(
        "start-text-revision", help="Back up and open text-only revision for a completed v6 project"
    )
    text_revision_start.add_argument("--state", required=True, type=Path)
    text_revision_submit = subparsers.add_parser(
        "submit-text-revision", help="Audit and submit story/caption-only changes for user approval"
    )
    text_revision_submit.add_argument("--state", required=True, type=Path)
    text_revision_approve = subparsers.add_parser(
        "approve-text-revision", help="Approve submitted text-only changes and return to complete"
    )
    text_revision_approve.add_argument("--state", required=True, type=Path)
    text_revision_approve.add_argument("--user-approved", action="store_true")
    text_revision_revert = subparsers.add_parser(
        "revert-text-revision", help="Restore the pre-revision text and state backup"
    )
    text_revision_revert.add_argument("--state", required=True, type=Path)
    reopen = subparsers.add_parser(
        "reopen-gate", help="Invalidate downstream approvals after editing an approved file"
    )
    reopen.add_argument("--state", required=True, type=Path)
    reopen.add_argument(
        "--gate", required=True,
        choices=("realism", "story", "storyboard", "calibration"),
    )
    migrate = subparsers.add_parser(
        "migrate", help="Migrate one explicitly selected unfinished state"
    )
    migrate.add_argument("--state", required=True, type=Path)
    migrate.add_argument("--to-version", type=int, choices=(2, 3, 6), required=True)
    migrate.add_argument("--planned-count", type=int)
    migrate.add_argument(
        "--reset-first-review",
        action="store_true",
        help="For an explicitly selected v5 project, discard active non-calibration first-review decisions and return to scene_generation.",
    )
    migrate.add_argument(
        "--dry-run", action="store_true", help="Validate migration without writing"
    )
    passed = subparsers.add_parser(
        "mark-pass", help="Mark one reviewed original or repaired image as passing"
    )
    passed.add_argument("--state", required=True, type=Path)
    passed.add_argument("--number", required=True, type=int)
    passed.add_argument("--notes", required=True)
    passed.add_argument("--red-flag", action="append", default=[])
    passed.add_argument("--review-file", type=Path)
    queued = subparsers.add_parser(
        "queue-repair", help="Record one original-image issue for deferred repair"
    )
    queued.add_argument("--state", required=True, type=Path)
    queued.add_argument("--number", required=True, type=int)
    queued.add_argument("--repair-mode", required=True, choices=("edit", "regenerate"))
    queued.add_argument("--issue", action="append", required=True)
    queued.add_argument("--notes", required=True)
    queued.add_argument("--red-flag", action="append", default=[])
    report = subparsers.add_parser(
        "prepare-repair-report",
        help="Write the repair report after every original candidate is reviewed",
    )
    report.add_argument("--state", required=True, type=Path)
    report.add_argument(
        "--output", type=Path,
        help="Report path; schema v4 defaults to artifacts.repair_report.",
    )
    approve = subparsers.add_parser(
        "authorize-repairs",
        help="Authorize report-listed repairs after explicit user approval",
    )
    approve.add_argument("--state", required=True, type=Path)
    approve.add_argument("--number", action="append", default=[], type=int)
    approve.add_argument("--user-approved", action="store_true")
    overview = subparsers.add_parser(
        "submit-originals-overview",
        help="Render the numbered first-generation overview and await the user's review choice",
    )
    overview.add_argument("--state", required=True, type=Path)
    first_review = subparsers.add_parser(
        "choose-first-review",
        help="Choose full, selected, or skipped first-round content review",
    )
    first_review.add_argument("--state", required=True, type=Path)
    first_review.add_argument("--mode", required=True, choices=("full", "selected", "skip"))
    first_review.add_argument("--number", action="append", default=[], type=int)
    first_review.add_argument("--user-approved", action="store_true")
    register = subparsers.add_parser(
        "register-reference-job",
        help="Register one reference asset for transport-safe generation",
    )
    register.add_argument("--state", required=True, type=Path)
    register.add_argument("--reference-id", required=True)
    register.add_argument(
        "--kind",
        required=True,
        choices=("character", "location", "prop", "vehicle", "wonder"),
    )
    register.add_argument("--output-dir", required=True)
    reference_pass = subparsers.add_parser(
        "mark-reference-pass",
        help="Mark one reviewed reference asset as approved",
    )
    reference_pass.add_argument("--state", required=True, type=Path)
    reference_pass.add_argument("--reference-id", required=True)
    reference_pass.add_argument("--notes", required=True)
    reference_review = subparsers.add_parser(
        "record-reference-review",
        help="Record pass/fail review and stage the one allowed reference correction",
    )
    reference_review.add_argument("--state", required=True, type=Path)
    reference_review.add_argument("--reference-id", required=True)
    reference_review.add_argument("--verdict", required=True, choices=("pass", "fail"))
    reference_review.add_argument("--issues-file", type=Path)
    reference_review.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "init-project":
            result = init_project(args.project_dir, args.schema_version)
            print(json.dumps({"valid": True, **result}, ensure_ascii=False, indent=2))
            return 0
        state_path = args.state.expanduser().resolve()
        if args.command == "migrate":
            source = load_json(state_path)
            if args.to_version == 6:
                if source.get("schema_version") != 5 or source.get("phase") == "complete":
                    raise StateError("v5 to v6 migration requires one unfinished schema-v5 project")
                validate_v5(state_path, source)
                payload = copy.deepcopy(source)
                payload["schema_version"] = 6
                payload.setdefault("artifacts", {})["originals_overview"] = V6_ARTIFACTS["originals_overview"]
                if args.reset_first_review:
                    calibration = set(payload.get("calibration_numbers", []))
                    missing = [
                        item["number"] for item in payload.get("images", [])
                        if item.get("number") not in calibration and not item.get("candidate")
                    ]
                    if missing:
                        raise StateError(
                            f"reset-first-review requires every non-calibration original candidate: {missing}"
                        )
                    for item in payload.get("images", []):
                        if item.get("number") in calibration:
                            continue
                        item["status"] = "candidate_ready"
                        item["hard_failures"] = []
                        item["photo_red_flags"] = []
                        item["repair_count"] = 0
                        item["repair_mode"] = None
                        item["repair_file"] = None
                        item["final_source"] = None
                        item["notes"] = ""
                        item.pop("repair_recommendation", None)
                        versions = item.get("candidate_versions", [])
                        if versions:
                            versions[-1]["review"] = None
                            versions[-1]["review_record"] = None
                    policy = payload.setdefault("repair_policy", {})
                    policy.update(
                        {
                            "report_file": None,
                            "report_generated_at": None,
                            "approved_numbers": [],
                            "approved_at": None,
                        }
                    )
                    payload["blocking_reasons"] = []
                    payload["phase"] = "scene_generation"
                reviewed_or_later = source.get("phase") in {
                    "awaiting_repair_approval", "repairing", "final_self_review", "needs_user"
                }
                if not args.reset_first_review and not reviewed_or_later and source.get("phase") == "scene_self_review":
                    calibration = set(source.get("calibration_numbers", []))
                    has_review = any(
                        version.get("review")
                        for item in source.get("images", [])
                        if item.get("number") not in calibration
                        for version in item.get("candidate_versions", [])
                    )
                    if not has_review:
                        for item in payload.get("images", []):
                            if item.get("candidate") and item.get("number") not in calibration:
                                item["status"] = "candidate_ready"
                                item["final_source"] = None
                        payload["phase"] = "scene_generation"
                validator = validate_v6
            elif args.to_version == 2:
                if args.planned_count is None:
                    raise StateError("v1 to v2 migration requires --planned-count")
                validate_v1(state_path, source)
                payload = migrate_v1(source, args.planned_count)
                validator = validate_v2
            else:
                validate_v2(state_path, source)
                payload = migrate_v2(source)
                validator = validate_v3
            if args.dry_run:
                result = validator(state_path, payload)
                result["dry_run"] = True
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = state_path.with_name(
                    f"{state_path.stem}.v{source.get('schema_version')}.{stamp}{state_path.suffix}"
                )
                shutil.copy2(state_path, backup)
                atomic_write_json(state_path, payload)
                result = validate_state(state_path)
                result["backup"] = str(backup)
        elif args.command == "approve-realism":
            result = approve_realism(state_path, args.user_approved)
        elif args.command == "approve-story":
            result = approve_story(state_path, args.user_approved)
        elif args.command == "register-storyboard":
            result = register_storyboard(state_path, args.planned_count)
        elif args.command == "approve-storyboard":
            result = approve_storyboard(state_path, args.user_approved)
        elif args.command == "approve-references":
            result = approve_references(state_path, args.user_approved)
        elif args.command == "submit-calibration":
            result = submit_calibration(state_path, args.contact_sheet)
        elif args.command == "approve-calibration":
            result = approve_calibration(state_path, args.user_approved)
        elif args.command == "start-text-revision":
            result = start_text_revision(state_path)
        elif args.command == "submit-text-revision":
            result = submit_text_revision(state_path)
        elif args.command == "approve-text-revision":
            result = approve_text_revision(state_path, args.user_approved)
        elif args.command == "revert-text-revision":
            result = revert_text_revision(state_path)
        elif args.command == "reopen-gate":
            result = reopen_gate(state_path, args.gate)
        elif args.command == "mark-pass":
            result = mark_pass(
                state_path, args.number, args.notes, args.red_flag, args.review_file
            )
        elif args.command == "queue-repair":
            result = queue_repair(
                state_path,
                args.number,
                args.repair_mode,
                args.issue,
                args.notes,
                args.red_flag,
            )
        elif args.command == "prepare-repair-report":
            result = prepare_repair_report(state_path, args.output)
        elif args.command == "authorize-repairs":
            result = authorize_repairs(
                state_path, args.number, args.user_approved
            )
        elif args.command == "submit-originals-overview":
            result = submit_originals_overview(state_path)
        elif args.command == "choose-first-review":
            result = choose_first_review(
                state_path, args.mode, args.number, args.user_approved
            )
        elif args.command == "register-reference-job":
            result = register_reference_job(
                state_path, args.reference_id, args.kind, args.output_dir
            )
        elif args.command == "mark-reference-pass":
            result = mark_reference_pass(
                state_path, args.reference_id, args.notes
            )
        elif args.command == "record-reference-review":
            issues: list[str] = []
            if args.issues_file:
                raw = load_json(args.issues_file.expanduser().resolve())
                issues = raw.get("issues", []) if isinstance(raw, dict) else raw
                require_string_list(issues, "issues-file")
            result = record_reference_review(
                state_path, args.reference_id, args.verdict, issues, args.notes
            )
        else:
            result = validate_state(state_path)
            if args.command == "transition":
                payload = load_json(state_path)
                require_writable_state(payload, "transition")
                if payload.get("schema_version") in {4, 5, 6} and (
                    payload.get("phase"), args.to
                ) in {
                    ("awaiting_realism_approval", "drafting"),
                    ("awaiting_story_approval", "plan_self_review"),
                    ("awaiting_storyboard_approval", "reference_self_review"),
                    ("awaiting_reference_approval", "scene_self_review"),
                    ("awaiting_reference_approval", "calibration_self_review"),
                    ("awaiting_calibration_approval", "scene_self_review"),
                }:
                    raise StateError(
                        "schema v4/v5 uses dedicated approval commands "
                        "to hash approved files"
                    )
                validate_transition(
                    payload["phase"], args.to, user_approved=args.user_approved
                )
                if args.to == "awaiting_reference_approval":
                    unfinished = [
                        job.get("id")
                        for job in payload.get("reference_jobs", [])
                        if job.get("status") != "pass"
                    ]
                    if unfinished:
                        raise StateError(
                            "all registered reference jobs must pass before reference approval: "
                            + ", ".join(str(value) for value in unfinished)
                        )
                prior = copy.deepcopy(payload)
                payload["phase"] = args.to
                atomic_write_json(state_path, payload)
                try:
                    result = validate_state(state_path)
                except Exception:
                    atomic_write_json(state_path, prior)
                    raise
    except StateError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
