#!/usr/bin/env python3
"""Compose exactly two approved references into one auditable 4:5 board."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


BOARD_SCHEMA_VERSION = 1
BOARD_TYPE = "oh-my-folkstoryphoto-reference-board"
CANVAS_SIZE = (1024, 1280)
ALLOWED_CANVAS_SIZES = {(1024, 1280), (768, 960)}
GUTTER = 16
BACKGROUND = (128, 128, 128)


class BoardError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_path(output: Path) -> Path:
    return output.with_name(f"{output.name}.json")


def parse_crop(value: str) -> tuple[int, int, int, int] | None:
    if value.strip().lower() == "full":
        return None
    try:
        left, top, right, bottom = (int(part) for part in value.split(","))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "source crop must be 'full' or left,top,right,bottom"
        ) from exc
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise argparse.ArgumentTypeError("source crop rectangle is invalid")
    return left, top, right, bottom


def load_source(
    path: Path, role: str, crop: tuple[int, int, int, int] | None
) -> tuple[Image.Image, dict[str, object]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise BoardError(f"source does not exist: {resolved}")
    if not role.strip():
        raise BoardError("each source role must be non-empty")
    try:
        with Image.open(resolved) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, ValueError) as exc:
        raise BoardError(f"source is not a readable image: {resolved}: {exc}") from exc
    source_size = image.size
    if crop is not None:
        left, top, right, bottom = crop
        if right > image.width or bottom > image.height:
            raise BoardError(f"crop {crop} exceeds source dimensions {image.size}")
        image = image.crop(crop)
    details: dict[str, object] = {
        "path": str(resolved),
        "role": role.strip(),
        "crop": list(crop) if crop is not None else None,
        "source_size": list(source_size),
        "cropped_size": list(image.size),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    return image, details


def cell_boxes(
    layout: str, canvas_size: tuple[int, int] = CANVAS_SIZE
) -> list[tuple[int, int, int, int]]:
    width, height = canvas_size
    if layout == "stacked":
        cell_height = (height - GUTTER) // 2
        return [
            (0, 0, width, cell_height),
            (0, cell_height + GUTTER, width, height),
        ]
    cell_width = (width - GUTTER) // 2
    return [
        (0, 0, cell_width, height),
        (cell_width + GUTTER, 0, width, height),
    ]


def occupied_area(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    cell_width = box[2] - box[0]
    cell_height = box[3] - box[1]
    scale = min(cell_width / image.width, cell_height / image.height)
    return image.width * scale * image.height * scale


def choose_layout(
    images: list[Image.Image], canvas_size: tuple[int, int] = CANVAS_SIZE
) -> str:
    scores = {}
    for layout in ("stacked", "side-by-side"):
        scores[layout] = sum(
            occupied_area(image, box)
            for image, box in zip(images, cell_boxes(layout, canvas_size), strict=True)
        )
    return "stacked" if scores["stacked"] >= scores["side-by-side"] else "side-by-side"


def fit_inside(
    image: Image.Image, box: tuple[int, int, int, int]
) -> tuple[Image.Image, tuple[int, int]]:
    cell_width = box[2] - box[0]
    cell_height = box[3] - box[1]
    scale = min(cell_width / image.width, cell_height / image.height)
    size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    resized = image.resize(size, Image.Resampling.LANCZOS)
    position = (
        box[0] + (cell_width - size[0]) // 2,
        box[1] + (cell_height - size[1]) // 2,
    )
    return resized, position


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def compose(
    sources: list[Path],
    roles: list[str],
    crops: list[tuple[int, int, int, int] | None],
    output: Path,
    jpeg_quality: int,
    canvas_size: tuple[int, int] = CANVAS_SIZE,
) -> dict[str, object]:
    if len(sources) != 2 or len(roles) != 2 or len(crops) != 2:
        raise BoardError("reference board requires exactly two sources, roles, and crops")
    output = output.expanduser().resolve()
    sidecar = sidecar_path(output)
    if output.suffix.lower() not in {".jpg", ".jpeg"}:
        raise BoardError("reference board output must use .jpg or .jpeg")
    if output.exists() or sidecar.exists():
        raise BoardError(f"refusing to overwrite output or sidecar: {output}")
    if not 75 <= jpeg_quality <= 95:
        raise BoardError("jpeg-quality must be between 75 and 95")
    if canvas_size not in ALLOWED_CANVAS_SIZES:
        raise BoardError(
            "canvas-size must be 1024x1280 or 768x960"
        )

    loaded = [
        load_source(source, role, crop)
        for source, role, crop in zip(sources, roles, crops, strict=True)
    ]
    images = [entry[0] for entry in loaded]
    details = [entry[1] for entry in loaded]
    resolved_sources = [Path(str(entry["path"])) for entry in details]
    if resolved_sources[0] == resolved_sources[1]:
        raise BoardError("duplicate source path is not allowed")
    if details[0]["sha256"] == details[1]["sha256"]:
        raise BoardError("duplicate source content is not allowed")
    if output in resolved_sources:
        raise BoardError("output must be different from both sources")

    layout = choose_layout(images, canvas_size)
    boxes = cell_boxes(layout, canvas_size)
    canvas = Image.new("RGB", canvas_size, BACKGROUND)
    for index, (image, box) in enumerate(zip(images, boxes, strict=True)):
        fitted, position = fit_inside(image, box)
        canvas.paste(fitted, position)
        details[index]["panel_index"] = index + 1
        details[index]["panel_position"] = "top" if layout == "stacked" and index == 0 else (
            "bottom" if layout == "stacked" else "left" if index == 0 else "right"
        )
        details[index]["cell_box"] = list(box)
        details[index]["rendered_box"] = [
            position[0],
            position[1],
            position[0] + fitted.width,
            position[1] + fitted.height,
        ]

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        canvas.save(
            temporary,
            format="JPEG",
            quality=jpeg_quality,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
        temporary.replace(output)
        manifest: dict[str, object] = {
            "schema_version": BOARD_SCHEMA_VERSION,
            "type": BOARD_TYPE,
            "layout": layout,
            "canvas_size": list(canvas_size),
            "gutter_pixels": GUTTER,
            "background_rgb": list(BACKGROUND),
            "output": {
                "path": str(output),
                "format": "JPEG",
                "width": canvas_size[0],
                "height": canvas_size[1],
                "bytes": output.stat().st_size,
                "sha256": sha256(output),
            },
            "sources": details,
        }
        atomic_write_json(sidecar, manifest)
    except Exception:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        **manifest,
        "output_path": str(output),
        "sidecar": str(sidecar),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--source-role", action="append", required=True)
    parser.add_argument(
        "--source-crop",
        action="append",
        type=parse_crop,
        help="Repeat twice; use 'full' or left,top,right,bottom",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--jpeg-quality", type=int, default=88)
    parser.add_argument(
        "--canvas-size",
        choices=("1024x1280", "768x960"),
        default="1024x1280",
    )
    args = parser.parse_args()
    crops = args.source_crop if args.source_crop is not None else [None, None]
    try:
        result = compose(
            args.source,
            args.source_role,
            crops,
            args.output,
            args.jpeg_quality,
            tuple(int(value) for value in args.canvas_size.split("x")),
        )
    except (BoardError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
