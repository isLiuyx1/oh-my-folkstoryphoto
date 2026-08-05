#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import optimize_reference


class OptimizeReferenceTest(unittest.TestCase):
    def test_creates_bounded_non_destructive_derivative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "derived.jpg"
            Image.new("RGB", (1600, 1200), (40, 80, 120)).save(source)
            original_hash = optimize_reference.sha256(source)

            result = optimize_reference.optimize(
                source, output, (100, 100, 1300, 1100), 800, 88
            )

            self.assertEqual(result["output_size"], [800, 667])
            self.assertTrue(output.is_file())
            self.assertEqual(optimize_reference.sha256(source), original_hash)
            self.assertLess(output.stat().st_size, source.stat().st_size)

    def test_rejects_overwrite_and_out_of_bounds_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGB", (32, 24), (1, 2, 3)).save(source)
            with self.assertRaises(optimize_reference.ReferenceError):
                optimize_reference.optimize(source, source, None, 512, 88)
            with self.assertRaises(optimize_reference.ReferenceError):
                optimize_reference.optimize(
                    source, root / "out.jpg", (0, 0, 33, 24), 512, 88
                )


if __name__ == "__main__":
    unittest.main()
