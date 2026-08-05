from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "oh-my-folkstoryphoto"
    / "scripts"
    / "package_release.py"
)
SCRIPTS_DIR = SCRIPT.parent
sys.path.append(str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("package_release", SCRIPT)
package_release = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(package_release)


class PackageReleaseTests(unittest.TestCase):
    def make_manifest(self, directory: Path, numbers=(1, 2)) -> Path:
        images = []
        for number in numbers:
            source = directory / f"source-{number}.png"
            Image.new("RGB", (800, 600), (number * 50, 20, 30)).save(source)
            images.append(
                {
                    "number": number,
                    "source": source.name,
                    "focal_x": 0.25,
                    "focal_y": 0.5,
                }
            )
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"images": images}), encoding="utf-8")
        return manifest

    def run_script(self, manifest: Path, output: Path, *extra: str):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(output),
                "--width",
                "108",
                "--height",
                "135",
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_packages_relative_sources_and_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            output = root / "release"
            result = self.run_script(manifest, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "最终发布总览.jpg").is_file())
            for number in (1, 2):
                with Image.open(output / f"{number:02d}.png") as image:
                    self.assertEqual(image.size, (108, 135))

    def test_refuses_overwrite_without_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            output = root / "release"
            self.assertEqual(self.run_script(manifest, output).returncode, 0)
            second = self.run_script(manifest, output)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("Refusing to overwrite", second.stderr)
            third = self.run_script(manifest, output, "--overwrite")
            self.assertEqual(third.returncode, 0, third.stderr)

    def test_rejects_missing_or_duplicate_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root, numbers=(1, 1))
            with self.assertRaisesRegex(ValueError, "unique and contiguous"):
                package_release.load_manifest(manifest)

    def test_focal_crop_protects_requested_side(self):
        image = Image.new("RGB", (400, 200), "blue")
        for x in range(100):
            for y in range(200):
                image.putpixel((x, y), (255, 0, 0))
        left = package_release.crop_and_resize(image, 100, 100, 0.0, 0.5)
        center = package_release.crop_and_resize(image, 100, 100, 0.5, 0.5)
        self.assertGreater(left.getpixel((10, 50))[0], 200)
        self.assertLess(center.getpixel((10, 50))[0], 100)


if __name__ == "__main__":
    unittest.main()
