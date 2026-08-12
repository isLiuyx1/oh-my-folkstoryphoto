#!/usr/bin/env python3
"""Portable authenticity benchmark and hard-failure contract tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import authenticity


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "authenticity-regressions.json"
ANCHORS = HERE.parent / "assets" / "capture-style-anchors"


class AuthenticityRegressionTest(unittest.TestCase):
    def test_benchmarks_are_portable_and_obey_hard_failure_contract(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 8)
        for case in cases:
            with self.subTest(case=case["id"]):
                visual_asset = case.get("visual_asset")
                if visual_asset:
                    self.assertTrue((ANCHORS / visual_asset).is_file())
                invalid = set(case["failed_checks"]) - set(authenticity.REQUIRED_CHECKS)
                self.assertFalse(invalid)
                if case["expected"] == "fail":
                    self.assertTrue(case["failed_checks"])
                    self.assertTrue(
                        set(case["failed_checks"]) & set(authenticity.CRITICAL_CHECKS)
                    )
                else:
                    self.assertEqual(case["expected"], "pass")
                    self.assertEqual(case["failed_checks"], [])


if __name__ == "__main__":
    unittest.main()
