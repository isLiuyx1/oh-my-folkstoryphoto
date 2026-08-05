#!/usr/bin/env python3
"""Tests for deterministic two-source reference boards."""

from __future__ import annotations

import json
import tempfile
import unittest
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "skills" / "oh-my-folkstoryphoto" / "scripts"
sys.path.append(str(SCRIPTS_DIR))

import compose_reference_board as board


class ReferenceBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.first = self.root / "first.png"
        self.second = self.root / "second.jpg"
        Image.new("RGB", (900, 500), (120, 20, 30)).save(self.first)
        Image.new("RGB", (400, 900), (20, 80, 140)).save(self.second)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def compose(self, name: str = "board.jpg") -> dict[str, object]:
        return board.compose(
            [self.first, self.second],
            ["赵克明身份与服装", "双鱼玉佩结构"],
            [None, None],
            self.root / name,
            88,
        )

    def test_board_is_exact_4x5_and_sidecar_is_complete(self) -> None:
        result = self.compose()
        output = Path(str(result["output_path"]))
        sidecar = Path(str(result["sidecar"]))
        with Image.open(output) as image:
            self.assertEqual(image.size, (1024, 1280))
            self.assertEqual(image.mode, "RGB")
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["type"], board.BOARD_TYPE)
        self.assertEqual(len(manifest["sources"]), 2)
        self.assertEqual(manifest["output"]["sha256"], board.sha256(output))
        self.assertIn(manifest["layout"], {"stacked", "side-by-side"})
        self.assertEqual(manifest["gutter_pixels"], 16)

    def test_same_inputs_produce_same_image_hash(self) -> None:
        first = self.compose("one.jpg")
        second = self.compose("two.jpg")
        self.assertEqual(first["output"]["sha256"], second["output"]["sha256"])

    def test_low_bandwidth_board_preserves_lineage(self) -> None:
        result = board.compose(
            [self.first, self.second],
            ["first", "second"],
            [None, None],
            self.root / "low.jpg",
            80,
            (768, 960),
        )
        with Image.open(Path(str(result["output_path"]))) as image:
            self.assertEqual(image.size, (768, 960))
        manifest = json.loads(Path(str(result["sidecar"])).read_text(encoding="utf-8"))
        self.assertEqual(manifest["canvas_size"], [768, 960])
        self.assertEqual([entry["sha256"] for entry in manifest["sources"]], [
            board.sha256(self.first),
            board.sha256(self.second),
        ])

    def test_crop_bounds_are_checked(self) -> None:
        with self.assertRaisesRegex(board.BoardError, "exceeds"):
            board.compose(
                [self.first, self.second],
                ["first", "second"],
                [(0, 0, 901, 500), None],
                self.root / "bad.jpg",
                88,
            )

    def test_refuses_overwrite_missing_and_duplicate_sources(self) -> None:
        self.compose()
        with self.assertRaisesRegex(board.BoardError, "overwrite"):
            self.compose()
        with self.assertRaisesRegex(board.BoardError, "does not exist"):
            board.compose(
                [self.first, self.root / "missing.png"],
                ["first", "missing"],
                [None, None],
                self.root / "missing-board.jpg",
                88,
            )
        duplicate = self.root / "duplicate.png"
        duplicate.write_bytes(self.first.read_bytes())
        with self.assertRaisesRegex(board.BoardError, "duplicate source content"):
            board.compose(
                [self.first, duplicate],
                ["first", "duplicate"],
                [None, None],
                self.root / "duplicate-board.jpg",
                88,
            )

    def test_requires_exactly_two_sources_and_roles(self) -> None:
        with self.assertRaisesRegex(board.BoardError, "exactly two"):
            board.compose(
                [self.first, self.second, self.first],
                ["one", "two", "three"],
                [None, None, None],
                self.root / "three.jpg",
                88,
            )

    def test_corrupt_source_is_rejected(self) -> None:
        corrupt = self.root / "corrupt.png"
        corrupt.write_bytes(b"not-an-image")
        with self.assertRaisesRegex(board.BoardError, "not a readable image"):
            board.compose(
                [self.first, corrupt],
                ["first", "corrupt"],
                [None, None],
                self.root / "corrupt-board.jpg",
                88,
            )

    def test_exif_orientation_is_applied_before_layout(self) -> None:
        oriented = self.root / "oriented.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (20, 40), (1, 2, 3)).save(oriented, exif=exif)
        result = board.compose(
            [oriented, self.second],
            ["oriented", "second"],
            [None, None],
            self.root / "oriented-board.jpg",
            88,
        )
        self.assertEqual(result["sources"][0]["source_size"], [40, 20])


if __name__ == "__main__":
    unittest.main()
