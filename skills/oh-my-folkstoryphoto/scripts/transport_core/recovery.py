"""Deterministic recovery policy and transaction identifiers."""

from __future__ import annotations

import hashlib
from typing import Any


def transaction_id(attempt_id: str, level: int) -> str:
    return hashlib.sha256(f"{attempt_id}:{level}".encode("utf-8")).hexdigest()[:24]


def recovery_spec(reference_count: int, level: int, repair_mode: str | None) -> dict[str, Any] | None:
    if reference_count not in {1, 2} or level not in {1, 2}:
        return None
    quality = 88 if level == 1 else 80
    max_edge = 1024 if level == 1 else 768
    if repair_mode == "edit":
        return {
            "operation": "edit_attachments_resize_compress",
            "max_edge": max_edge,
            "jpeg_quality": quality,
        }
    if reference_count == 2:
        return {
            "operation": "reference_board",
            "canvas_size": [1024, 1280] if level == 1 else [768, 960],
            "jpeg_quality": quality,
        }
    return {
        "operation": "resize_compress",
        "max_edge": max_edge,
        "jpeg_quality": quality,
        "crop": None,
    }

