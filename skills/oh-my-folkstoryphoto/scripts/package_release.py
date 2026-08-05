#!/usr/bin/env python3
"""Create numbered 4:5 PNGs and a contact sheet without modifying sources."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from review_state import (
    StateError,
    load_json,
    resolve_project_dir,
    resolve_project_path,
    validate_state,
)

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError as exc:
    raise SystemExit(
        "Pillow is required. In Codex Desktop, load workspace dependencies and "
        "run this script with the bundled Python runtime."
    ) from exc


DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350
DEFAULT_COLUMNS = 5
DEFAULT_OVERVIEW = "最终发布总览.jpg"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--state",
        type=Path,
        help=(
            "For schema v2/v3 projects, require a valid final_self_review or complete "
            "state whose release manifest matches --manifest."
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--columns", type=int, default=DEFAULT_COLUMNS)
    parser.add_argument("--overview-name", default=DEFAULT_OVERVIEW)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace numbered release files and the overview only.",
    )
    return parser.parse_args()


def verify_state_for_packaging(state_path: Path, manifest_path: Path) -> None:
    summary = validate_state(state_path)
    payload = load_json(state_path)
    if payload.get("schema_version") not in {2, 3}:
        raise ValueError("--state packaging guard requires schema_version 2 or 3")
    if summary["phase"] not in {"final_self_review", "complete"}:
        raise ValueError(
            "--state must be in final_self_review or complete before packaging"
        )
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    raw_manifest = payload.get("artifacts", {}).get("release_manifest")
    state_manifest = resolve_project_path(
        project_dir, raw_manifest, "artifacts.release_manifest"
    )
    if state_manifest != manifest_path:
        raise ValueError("--manifest does not match artifacts.release_manifest")


def validate_focal(value: Any, field: str, index: int) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"images[{index}].{field} must be a number from 0 to 1.")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"images[{index}].{field} must be from 0 to 1.")
    return result


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"Manifest does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, list) or not images:
        raise ValueError("Manifest must contain a non-empty 'images' array.")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            raise ValueError(f"images[{index}] must be an object.")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError(f"images[{index}].number must be a positive integer.")
        raw_source = item.get("source")
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise ValueError(f"images[{index}].source must be a path string.")
        source = Path(raw_source).expanduser()
        if not source.is_absolute():
            source = (path.parent / source).resolve()
        if not source.is_file():
            raise ValueError(f"Source image does not exist: {source}")
        normalized.append(
            {
                "number": number,
                "source": source,
                "focal_x": validate_focal(
                    item.get("focal_x", 0.5), "focal_x", index
                ),
                "focal_y": validate_focal(
                    item.get("focal_y", 0.5), "focal_y", index
                ),
            }
        )

    normalized.sort(key=lambda item: item["number"])
    numbers = [item["number"] for item in normalized]
    expected = list(range(1, len(normalized) + 1))
    if numbers != expected:
        raise ValueError(
            f"Image numbers must be unique and contiguous from 1; got {numbers}."
        )
    return normalized


def crop_and_resize(
    image: Image.Image,
    width: int,
    height: int,
    focal_x: float,
    focal_y: float,
) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    source_width, source_height = image.size
    target_ratio = width / height
    source_ratio = source_width / source_height

    if source_ratio > target_ratio:
        crop_height = source_height
        crop_width = max(1, round(crop_height * target_ratio))
        center_x = focal_x * source_width
        left = round(center_x - crop_width / 2)
        left = min(max(left, 0), source_width - crop_width)
        top = 0
    else:
        crop_width = source_width
        crop_height = max(1, round(crop_width / target_ratio))
        center_y = focal_y * source_height
        top = round(center_y - crop_height / 2)
        top = min(max(top, 0), source_height - crop_height)
        left = 0

    cropped = image.crop((left, top, left + crop_width, top + crop_height))
    return cropped.resize((width, height), Image.Resampling.LANCZOS)


def atomic_save(image: Image.Image, destination: Path, **kwargs: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=destination.suffix or ".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        image.save(temporary_path, **kwargs)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_contact_sheet(
    release_paths: list[Path], columns: int, overview_path: Path
) -> None:
    thumb_width, thumb_height = 216, 270
    margin, label_height = 12, 28
    rows = math.ceil(len(release_paths) / columns)
    sheet = Image.new(
        "RGB",
        (
            columns * (thumb_width + margin) + margin,
            rows * (thumb_height + label_height + margin) + margin,
        ),
        (30, 30, 30),
    )
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(release_paths):
        with Image.open(path) as source:
            thumb = source.convert("RGB").resize(
                (thumb_width, thumb_height), Image.Resampling.LANCZOS
            )
        x = margin + (index % columns) * (thumb_width + margin)
        y = margin + (index // columns) * (thumb_height + label_height + margin)
        sheet.paste(thumb, (x, y))
        draw.text((x, y + thumb_height + 4), path.stem, fill=(240, 240, 240))
    atomic_save(sheet, overview_path, quality=92)


def main() -> int:
    args = parse_args()
    if args.width < 1 or args.height < 1 or args.columns < 1:
        raise SystemExit("--width, --height and --columns must be positive.")
    if Path(args.overview_name).name != args.overview_name:
        raise SystemExit("--overview-name must be a filename, not a path.")
    try:
        manifest_path = args.manifest.expanduser().resolve()
        if args.state is not None:
            verify_state_for_packaging(
                args.state.expanduser().resolve(), manifest_path
            )
        images = load_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, StateError, ValueError) as exc:
        raise SystemExit(f"Invalid manifest: {exc}") from exc

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    release_paths = [
        output_dir / f"{item['number']:02d}.png" for item in images
    ]
    overview_path = output_dir / args.overview_name
    existing = [
        path for path in [*release_paths, overview_path] if path.exists()
    ]
    if existing and not args.overwrite:
        raise SystemExit(
            "Refusing to overwrite existing release files. Use --overwrite:\n"
            + "\n".join(str(path) for path in existing[:10])
        )

    for item, destination in zip(images, release_paths):
        with Image.open(item["source"]) as source:
            result = crop_and_resize(
                source,
                args.width,
                args.height,
                item["focal_x"],
                item["focal_y"],
            )
        atomic_save(result, destination, compress_level=6)
    create_contact_sheet(release_paths, args.columns, overview_path)

    for path in release_paths:
        with Image.open(path) as image:
            if image.size != (args.width, args.height):
                raise RuntimeError(f"Unexpected output dimensions for {path}")
    print(
        json.dumps(
            {
                "count": len(release_paths),
                "width": args.width,
                "height": args.height,
                "output_dir": str(output_dir),
                "overview": str(overview_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
