#!/usr/bin/env python3
"""Read-only structural and human-writing audit for story-carousel text."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


MAX_CAPTION_CHARS = 48
FORMULA_PATTERNS = {
    "连续‘不是…而是…’式解释": re.compile(r"不是.{0,24}而是"),
    "‘每次…都会…’式句子": re.compile(r"每次.{0,24}都会"),
    "事后总结式措辞": re.compile(r"(这证明|这意味着|结果表明|由此可见|这才是)"),
    "形式化连接词": re.compile(r"(值得注意的是|不难发现|与此同时|在此基础上|总而言之)"),
}
TECHNICAL_TERMS = ("音素", "匹配结果", "神经反射", "体细胞对照组")


def visible_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def markdown_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            block.append(lines[index])
            index += 1
        if len(block) >= 3:
            header = [cell.strip() for cell in block[0].strip().strip("|").split("|")]
            rows = [
                [cell.strip() for cell in raw.strip().strip("|").split("|")]
                for raw in block[2:]
            ]
            tables.append((header, rows))
    for header, rows in tables:
        if "图号" in header and "发布字幕" in header:
            return header, rows
    raise ValueError(f"no 图号/发布字幕 Markdown table found in {path}")


def parse_captions(path: Path) -> dict[int, str]:
    header, rows = markdown_rows(path)
    number_index = header.index("图号")
    caption_index = header.index("发布字幕")
    captions: dict[int, str] = {}
    for row in rows:
        if len(row) != len(header):
            raise ValueError(f"table row has {len(row)} cells; expected {len(header)} in {path}")
        raw_number = re.sub(r"\D", "", row[number_index])
        if not raw_number:
            raise ValueError(f"invalid image number {row[number_index]!r} in {path}")
        number = int(raw_number)
        if number in captions:
            raise ValueError(f"duplicate image number {number} in {path}")
        captions[number] = row[caption_index].strip()
    return captions


def warn_formula(text: str, where: str, warnings: list[str]) -> None:
    for label, pattern in FORMULA_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            warnings.append(f"{where}: {label}（{len(matches)}处），请人工判断是否像现场说话")


def audit_files(story: Path, publication: Path, storyboard: Path) -> dict[str, Any]:
    hard_errors: list[str] = []
    warnings: list[str] = []
    for label, path in (("story", story), ("publication", publication), ("storyboard", storyboard)):
        if not path.is_file():
            hard_errors.append(f"{label} file does not exist: {path}")
    if hard_errors:
        return {"valid": False, "caption_count": 0, "hard_errors": hard_errors, "warnings": warnings}
    try:
        publication_captions = parse_captions(publication)
        storyboard_captions = parse_captions(storyboard)
    except (OSError, UnicodeError, ValueError) as exc:
        return {"valid": False, "caption_count": 0, "hard_errors": [str(exc)], "warnings": warnings}
    numbers = sorted(publication_captions)
    if numbers != list(range(1, len(numbers) + 1)):
        hard_errors.append(f"publication image numbers must be contiguous from 1; got {numbers}")
    if sorted(storyboard_captions) != numbers:
        hard_errors.append("publication and AI storyboard image numbers differ")
    for number in numbers:
        caption = publication_captions[number]
        if not caption.strip():
            hard_errors.append(f"caption {number:02d} is empty")
        if visible_length(caption) > MAX_CAPTION_CHARS:
            hard_errors.append(f"caption {number:02d} exceeds {MAX_CAPTION_CHARS} visible characters")
        if storyboard_captions.get(number) != caption:
            hard_errors.append(f"caption {number:02d} differs between publication and AI storyboard")

    captions = [publication_captions[number] for number in numbers]
    lengths = [visible_length(value) for value in captions]
    if lengths and max(lengths) - min(lengths) <= 6:
        warnings.append("字幕句长过于整齐，请打破统一节奏")
    for index in range(max(0, len(lengths) - 2)):
        trio = lengths[index:index + 3]
        if max(trio) - min(trio) <= 2 and min(trio) >= 12:
            warnings.append(f"字幕 {index + 1:02d}–{index + 3:02d} 句长过于整齐")
    openings = Counter(re.sub(r"[\W_]+", "", caption)[:4] for caption in captions if caption)
    for opening, count in openings.items():
        if opening and count >= 3:
            warnings.append(f"字幕重复以“{opening}”开头 {count} 次")

    joined_captions = "\n".join(captions)
    story_text = story.read_text(encoding="utf-8")
    warn_formula(joined_captions, "captions", warnings)
    warn_formula(story_text, "story", warnings)
    for number, caption in publication_captions.items():
        for term in TECHNICAL_TERMS:
            if term in caption:
                warnings.append(
                    f"字幕 {number:02d} 使用技术词“{term}”；确认说话者当时具有这一知识来源"
                )
    clue_pattern = re.compile(r"(?:五|六|5|6).{0,12}(?:却|少|多|剩|只有).{0,12}(?:五|六|5|6)|(?:五|六|5|6)名.{0,20}(?:五|六|5|6)(?:只|个|杯)")
    for number, caption in publication_captions.items():
        if clue_pattern.search(caption):
            warnings.append(f"字幕 {number:02d} 强调数量差；请确认后文回收，否则降为生活细节")
    return {
        "valid": not hard_errors,
        "caption_count": len(captions),
        "hard_errors": hard_errors,
        "warnings": list(dict.fromkeys(warnings)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", required=True, type=Path)
    parser.add_argument("--publication", required=True, type=Path)
    parser.add_argument("--storyboard", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit_files(args.story.expanduser().resolve(), args.publication.expanduser().resolve(), args.storyboard.expanduser().resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        args.json_out.expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
