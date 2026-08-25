#!/usr/bin/env python3
"""Render one numbered overview from schema-v6 original candidate paths."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps


def _tile(path: Path, size: tuple[int, int], number: int) -> Image.Image:
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            tile = Image.new("RGB", size, (28, 28, 28))
            tile.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
            return tile
    except Exception:
        tile = Image.new("RGB", size, (62, 62, 62))
        draw = ImageDraw.Draw(tile)
        draw.rectangle((8, 8, size[0] - 9, size[1] - 9), outline=(125, 125, 125), width=2)
        draw.text((size[0] // 2 - 18, size[1] // 2 - 8), f"{number:02d}", fill=(220, 220, 220))
        return tile


def render_overview(payload: dict[str, Any], project_dir: Path, output: Path) -> Path:
    images = sorted(payload.get("images", []), key=lambda item: item["number"])
    columns = 6 if len(images) >= 24 else max(1, min(5, math.ceil(math.sqrt(len(images)))))
    tile_size = (180, 225)
    label_h, gap = 28, 10
    rows = math.ceil(len(images) / columns)
    sheet = Image.new(
        "RGB",
        (columns * (tile_size[0] + gap) + gap, rows * (tile_size[1] + label_h + gap) + gap),
        (20, 20, 20),
    )
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(images):
        number = int(item["number"])
        candidate = project_dir / str(item.get("candidate") or "")
        x = gap + (index % columns) * (tile_size[0] + gap)
        y = gap + (index // columns) * (tile_size[1] + label_h + gap)
        sheet.paste(_tile(candidate, tile_size, number), (x, y))
        draw.text((x + 4, y + tile_size[1] + 5), f"{number:02d}", fill=(245, 245, 245))
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".overview-", suffix=".jpg", dir=output.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        sheet.save(temporary_path, format="JPEG", quality=90)
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output
