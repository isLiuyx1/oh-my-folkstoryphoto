#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "skills" / "oh-my-folkstoryphoto" / "scripts"
sys.path.append(str(SCRIPTS_DIR))

import normalize_candidate


class NormalizeCandidateTest(unittest.TestCase):
    def test_crops_portrait_to_exact_4x5_without_overwriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "portrait.png"
            output = root / "normalized.png"
            Image.new("RGB", (1024, 1536), (20, 40, 60)).save(source)
            source_hash = normalize_candidate.sha256(source)

            result = normalize_candidate.normalize(source, output, 0.5)

            self.assertEqual(result["output_size"], [1024, 1280])
            with Image.open(output) as image:
                self.assertEqual(image.size, (1024, 1280))
            self.assertEqual(normalize_candidate.sha256(source), source_hash)

    def test_rejects_landscape_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            landscape = root / "landscape.png"
            output = root / "normalized.png"
            Image.new("RGB", (1536, 1024), (1, 2, 3)).save(landscape)
            with self.assertRaises(normalize_candidate.CandidateError):
                normalize_candidate.normalize(landscape, output)

            portrait = root / "portrait.png"
            Image.new("RGB", (40, 50), (1, 2, 3)).save(portrait)
            Image.new("RGB", (40, 50), (4, 5, 6)).save(output)
            with self.assertRaises(normalize_candidate.CandidateError):
                normalize_candidate.normalize(portrait, output)

    def test_near_4x5_odd_dimensions_become_mathematically_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "near.png"
            output = root / "exact.png"
            Image.new("RGB", (1122, 1402), (7, 8, 9)).save(source)

            result = normalize_candidate.normalize(source, output)

            self.assertEqual(result["output_size"], [1120, 1400])
            self.assertEqual(result["crop_box"], [1, 1, 1121, 1401])


if __name__ == "__main__":
    unittest.main()
