#!/usr/bin/env python3
"""Render the schema-v5 three-image calibration review sheet."""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

import authenticity
import review_state


SKILL_DIR = Path(__file__).resolve().parent.parent
ANCHOR_DIR = SKILL_DIR / "assets" / "capture-style-anchors"
FONT_CANDIDATES = (
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def fit_image(path: Path, size: tuple[int, int], background: tuple[int, int, int]) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background)
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def wrap(value: str, width: int = 34) -> str:
    return "\n".join(textwrap.wrap(value, width=width, break_long_words=True) or [""])


def load_anchor_manifest() -> dict[str, dict[str, Any]]:
    payload = json.loads((ANCHOR_DIR / "manifest.json").read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["anchors"]}


def render(state_path: Path) -> Path:
    summary = review_state.validate_state(state_path)
    payload = review_state.load_json(state_path)
    if payload.get("schema_version") not in {5, 6} or payload.get("phase") != "calibration_self_review":
        raise review_state.StateError("calibration sheet requires schema v5/v6 calibration_self_review")
    project_dir = Path(summary["project_dir"])
    plan = authenticity.parse_realism_plan(review_state.artifact_path(project_dir, payload, "realism_plan"))
    captures = authenticity.capture_map(plan)
    rows = {
        int(row["图号"]): row
        for row in review_state.parse_v5_production_rows(
            review_state.artifact_path(project_dir, payload, "ai_storyboard")
        )
    }
    anchors = load_anchor_manifest()
    numbers = payload.get("calibration_numbers", [])
    if len(numbers) != 3:
        raise review_state.StateError("calibration sheet requires exactly three image numbers")

    width, header_h, row_h = 1500, 120, 520
    sheet = Image.new("RGB", (width, header_h + row_h * 3), (238, 238, 234))
    draw = ImageDraw.Draw(sheet)
    draw.text((42, 28), "真实性校准联系表", fill=(20, 20, 20), font=font(38))
    draw.text((42, 78), "候选图 / 采集质感锚点 / 配置与结构化审查", fill=(75, 75, 75), font=font(22))

    for index, number in enumerate(numbers):
        item = next(value for value in payload["images"] if value["number"] == number)
        if item.get("status") != "pass" or not item.get("candidate_versions"):
            raise review_state.StateError(f"calibration image {number} must pass before rendering the sheet")
        review = item["candidate_versions"][-1].get("review")
        if not isinstance(review, dict):
            raise review_state.StateError(f"calibration image {number} lacks structured review")
        row = rows[number]
        capture = captures[row["采集配置ID"]]
        y = header_h + index * row_h
        draw.rectangle((20, y + 10, width - 20, y + row_h - 10), fill=(250, 250, 247), outline=(180, 180, 175), width=2)

        source = review_state.resolve_project_path(project_dir, item["final_source"], "calibration.final_source")
        sheet.paste(fit_image(source, (300, 375), (30, 30, 30)), (40, y + 70))
        draw.text((40, y + 28), f"图{number:02d} · {row['校准角色']}", fill=(20, 20, 20), font=font(26))

        anchor_ids = capture.get("anchor_ids", [])
        anchor = anchors.get(anchor_ids[0]) if anchor_ids else None
        if anchor:
            anchor_path = ANCHOR_DIR / anchor["file"]
            sheet.paste(fit_image(anchor_path, (300, 375), (30, 30, 30)), (370, y + 70))
            draw.text((370, y + 28), f"锚点 · {anchor['id']}", fill=(20, 20, 20), font=font(23))
        else:
            draw.rectangle((370, y + 70, 670, y + 445), fill=(220, 220, 216))
            draw.text((425, y + 245), "无直接质感锚点", fill=(85, 85, 85), font=font(22))

        checks = review["checks"]
        failed = [name for name, value in checks.items() if value == "fail"]
        details = [
            f"采集配置：{row['采集配置ID']} / {capture['device_profile']}",
            f"拍摄者：{row['拍摄者']}；设备可见性：{row['设备可见性']}",
            f"受限机位：{row['受限机位']}",
            f"预期成像：{row['成像结果']}",
            "八项审查：" + ("全部通过/不适用" if not failed else "失败 " + ", ".join(failed)),
            f"审查说明：{review['notes']}",
        ]
        text = "\n\n".join(wrap(value) for value in details)
        draw.multiline_text((710, y + 45), text, fill=(35, 35, 35), font=font(22), spacing=8)

    output = review_state.artifact_path(project_dir, payload, "calibration_contact_sheet")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=92, subsampling=0)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    try:
        output = render(args.state.expanduser().resolve())
    except (review_state.StateError, authenticity.AuthenticityError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
