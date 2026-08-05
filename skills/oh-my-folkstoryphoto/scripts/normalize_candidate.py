#!/usr/bin/env python3
"""Non-destructively normalize a portrait image-generation candidate to exact 4:5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


class CandidateError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(
    source: Path, output: Path, focal_y: float = 0.5, focal_x: float = 0.5
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise CandidateError(f"input does not exist: {source}")
    if output.exists():
        raise CandidateError(f"refusing to overwrite output: {output}")
    if not 0.0 <= focal_y <= 1.0:
        raise CandidateError("--focal-y must be between 0 and 1")
    if not 0.0 <= focal_x <= 1.0:
        raise CandidateError("--focal-x must be between 0 and 1")

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    if width >= height:
        raise CandidateError(
            f"input must already be portrait; got {width}x{height}. Regenerate vertically"
        )
    source_ratio = width / height
    target_ratio = 4 / 5
    if source_ratio > 0.82:
        raise CandidateError(
            f"portrait is materially wider than 4:5 ({width}x{height}); regenerate with a 4:5-safe composition"
        )

    crop_width = min(width, int(height * target_ratio))
    crop_width -= crop_width % 4
    if crop_width < 4:
        raise CandidateError("input is too small to normalize to exact 4:5")
    crop_height = crop_width * 5 // 4
    center_x = focal_x * width
    center_y = focal_y * height
    left = round(center_x - crop_width / 2)
    left = min(max(left, 0), width - crop_width)
    top = round(center_y - crop_height / 2)
    top = min(max(top, 0), height - crop_height)
    normalized = image.crop((left, top, left + crop_width, top + crop_height))

    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise CandidateError("output must use .png, .jpg, or .jpeg")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        if suffix == ".png":
            normalized.save(temporary, format="PNG", optimize=True)
        else:
            normalized.save(temporary, format="JPEG", quality=95, optimize=True)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "ok": True,
        "source": str(source),
        "output": str(output),
        "source_size": [width, height],
        "output_size": list(normalized.size),
        "crop_box": [left, top, left + crop_width, top + crop_height],
        "focal_x": focal_x,
        "focal_y": focal_y,
        "source_sha256": sha256(source),
        "output_sha256": sha256(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--focal-x", type=float, default=0.5)
    parser.add_argument("--focal-y", type=float, default=0.5)
    args = parser.parse_args()
    try:
        result = normalize(args.input, args.output, args.focal_y, args.focal_x)
    except (CandidateError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
