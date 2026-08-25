#!/usr/bin/env python3
"""Synchronize the AI storyboard publication-caption column from publication notes."""

from __future__ import annotations

import argparse
from pathlib import Path

import text_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", required=True, type=Path)
    parser.add_argument("--storyboard", required=True, type=Path)
    args = parser.parse_args()
    captions = text_audit.parse_captions(args.publication)
    path = args.storyboard
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    header: list[str] | None = None
    caption_index: int | None = None
    table_index = 0
    changed = 0
    output: list[str] = []
    for line in lines:
        if line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if table_index == 0:
                header = cells
                caption_index = cells.index("发布字幕")
            elif table_index >= 2 and header and caption_index is not None:
                number = int(cells[header.index("图号")])
                if number in captions:
                    cells[caption_index] = captions[number]
                    line = "| " + " | ".join(cells) + " |\n"
                    changed += 1
            table_index += 1
        output.append(line)
    if changed != len(captions):
        raise SystemExit(f"changed {changed} rows but expected {len(captions)}")
    path.write_text("".join(output), encoding="utf-8")
    print(f"synchronized {changed} captions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
