#!/usr/bin/env python3
"""Create a smaller, non-destructive derivative of an approved reference image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


class ReferenceError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_crop(value: str) -> tuple[int, int, int, int]:
    try:
        left, top, right, bottom = (int(part) for part in value.split(","))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "crop must be four comma-separated integers: left,top,right,bottom"
        ) from exc
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise argparse.ArgumentTypeError("crop rectangle is invalid")
    return left, top, right, bottom


def optimize(
    source: Path,
    output: Path,
    crop: tuple[int, int, int, int] | None,
    max_edge: int,
    jpeg_quality: int,
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise ReferenceError(f"source does not exist: {source}")
    if output.exists():
        raise ReferenceError(f"refusing to overwrite output: {output}")
    if source == output:
        raise ReferenceError("source and output must be different")
    if output.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ReferenceError("output must use .jpg, .jpeg, or .png")
    if max_edge < 256 or max_edge > 1536:
        raise ReferenceError("max-edge must be between 256 and 1536")
    if jpeg_quality < 75 or jpeg_quality > 95:
        raise ReferenceError("jpeg-quality must be between 75 and 95")

    source_bytes = source.stat().st_size
    source_hash = sha256(source)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        source_size = image.size
        if crop is not None:
            left, top, right, bottom = crop
            if right > image.width or bottom > image.height:
                raise ReferenceError(
                    f"crop {crop} exceeds source dimensions {image.size}"
                )
            image = image.crop(crop)
        if max(image.size) > max_edge:
            scale = max_edge / max(image.size)
            resized = (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            )
            image = image.resize(resized, Image.Resampling.LANCZOS)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() in {".jpg", ".jpeg"}:
            image.save(
                output,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
            )
        else:
            image.save(output, format="PNG", optimize=True)

    result = {
        "ok": True,
        "source": str(source),
        "output": str(output),
        "crop": list(crop) if crop is not None else None,
        "source_size": list(source_size),
        "output_size": list(image.size),
        "source_bytes": source_bytes,
        "output_bytes": output.stat().st_size,
        "source_sha256": source_hash,
        "output_sha256": sha256(output),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--crop", type=parse_crop)
    parser.add_argument("--max-edge", type=int, default=1024)
    parser.add_argument("--jpeg-quality", type=int, default=88)
    args = parser.parse_args()
    try:
        result = optimize(
            args.input,
            args.output,
            args.crop,
            args.max_edge,
            args.jpeg_quality,
        )
    except (OSError, ReferenceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
