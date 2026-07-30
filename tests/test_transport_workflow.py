#!/usr/bin/env python3
"""Regression tests for review-state v2 and transport circuit behavior."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "skills" / "oh-my-folkstoryphoto" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import review_state
import package_release
import transport_guard


class TransportWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.state = self.project / "review-state.json"
        self.prompt = self.project / "prompt.txt"
        self.prompt.write_text("same approved prompt", encoding="utf-8")
        self.reference = self.project / "reference.png"
        Image.new("RGB", (32, 24), (12, 34, 56)).save(self.reference)
        self.candidate = self.project / "原始生成图" / "01.png"
        self.candidate.parent.mkdir()
        Image.new("RGB", (40, 50), (70, 80, 90)).save(self.candidate)
        self.write_v2(3)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_v2(self, planned_count: int, phase: str = "scene_self_review") -> None:
        images = []
        for number in range(1, planned_count + 1):
            images.append(
                {
                    "number": number,
                    "status": "pending",
                    "candidate": None,
                    "hard_failures": [],
                    "photo_red_flags": [],
                    "repair_count": 0,
                    "repair_mode": None,
                    "repair_file": None,
                    "final_source": None,
                    "notes": "",
                    "transport": review_state.empty_transport(),
                }
            )
        payload = {
            "schema_version": 2,
            "project_dir": ".",
            "phase": phase,
            "planned_count": planned_count,
            "max_repairs_per_item": 1,
            "artifacts": {
                "self_review": "自审记录.md",
                "acceptance": "验收记录.md",
                "release_manifest": "release-manifest.json",
            },
            "images": images,
            "transport_backends": {},
            "fallback_authorizations": {},
            "blocking_reasons": [],
        }
        review_state.atomic_write_json(self.state, payload)

    def preflight_args(
        self,
        number: int,
        *,
        backend: str = transport_guard.BUILT_IN_BACKEND,
        model: str | None = None,
        prompt: Path | None = None,
        references: list[Path] | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            state=self.state,
            number=number,
            backend=backend,
            route=None,
            model=model,
            prompt_file=prompt or self.prompt,
            reference=references if references is not None else [self.reference],
        )

    def failure_args(self, number: int) -> argparse.Namespace:
        return argparse.Namespace(
            state=self.state,
            number=number,
            error_type="network_error",
            message="network error",
        )

    def test_three_failures_open_only_scene_circuit(self) -> None:
        for expected in (1, 2, 3):
            transport_guard.preflight(self.preflight_args(1))
            result = transport_guard.record_failure(self.failure_args(1))
            self.assertEqual(result["consecutive_failures"], expected)
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["status"], "transport_blocked")
        self.assertEqual(payload["images"][0]["repair_count"], 0)
        self.assertEqual(payload["images"][1]["status"], "pending")
        self.assertFalse(
            payload["transport_backends"][transport_guard.BUILT_IN_BACKEND][
                "circuit_open"
            ]
        )
        transport_guard.preflight(self.preflight_args(2))

    def test_same_error_on_second_scene_opens_backend_circuit(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
        transport_guard.preflight(self.preflight_args(2))
        result = transport_guard.record_failure(self.failure_args(2))
        self.assertTrue(result["backend_circuit_open"])
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(self.preflight_args(3))

    def test_approved_resume_grants_exactly_one_probe(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
        probe = argparse.Namespace(state=self.state, number=1, user_approved=True)
        transport_guard.resume_probe(probe)
        result = transport_guard.preflight(self.preflight_args(1))
        self.assertTrue(result["probe"])
        transport_guard.record_failure(self.failure_args(1))
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(self.preflight_args(1))
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["transport"]["attempts_total"], 4)

    def test_success_enters_content_review_and_resets_transport(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        args = argparse.Namespace(
            state=self.state, number=1, candidate=self.candidate
        )
        result = transport_guard.record_success(args)
        self.assertEqual(result["status"], "review_pending")
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["repair_count"], 0)
        self.assertEqual(
            payload["images"][0]["candidate"], "原始生成图/01.png"
        )

    def test_input_drift_and_corrupt_reference_are_rejected(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        changed_prompt = self.project / "changed.txt"
        changed_prompt.write_text("changed prompt", encoding="utf-8")
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(
                self.preflight_args(1, prompt=changed_prompt)
            )
        corrupt = self.project / "corrupt.png"
        corrupt.write_bytes(b"not a png")
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(
                self.preflight_args(2, references=[corrupt])
            )

    def test_fallback_requires_authorization_and_records_differences(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        fallback = self.preflight_args(
            1, backend=transport_guard.FALLBACK_BACKEND, model="test-model"
        )
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(fallback)
        authorization = argparse.Namespace(
            state=self.state,
            backend=transport_guard.FALLBACK_BACKEND,
            model="test-model",
            user_approved=True,
        )
        transport_guard.authorize_fallback(authorization)
        result = transport_guard.preflight(fallback)
        self.assertFalse(result["input_differences"]["prompt_changed"])
        self.assertTrue(Path(result["request_file"]).is_file())

    def test_authorized_fallback_bypasses_only_old_backend_circuit(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
        authorization = argparse.Namespace(
            state=self.state,
            backend=transport_guard.FALLBACK_BACKEND,
            model="test-model",
            user_approved=True,
        )
        transport_guard.authorize_fallback(authorization)
        result = transport_guard.preflight(
            self.preflight_args(
                1,
                backend=transport_guard.FALLBACK_BACKEND,
                model="test-model",
            )
        )
        self.assertEqual(result["backend"], transport_guard.FALLBACK_BACKEND)
        payload = review_state.load_json(self.state)
        self.assertEqual(
            payload["images"][0]["transport"]["consecutive_failures"], 0
        )
        self.assertEqual(payload["images"][0]["transport"]["attempts_total"], 4)

    def test_final_phase_rejects_missing_images(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "final_self_review"
        review_state.atomic_write_json(self.state, payload)
        with self.assertRaises(review_state.StateError):
            review_state.validate_state(self.state)

    def test_partial_manifest_valid_during_work_but_packaging_refuses(self) -> None:
        payload = review_state.load_json(self.state)
        payload["images"][0]["status"] = "pass"
        payload["images"][0]["candidate"] = "原始生成图/01.png"
        payload["images"][0]["final_source"] = "原始生成图/01.png"
        manifest = self.project / "release-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "images": [
                        {
                            "number": 1,
                            "source": "原始生成图/01.png",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)
        with self.assertRaises(ValueError):
            package_release.verify_state_for_packaging(self.state, manifest)

    def test_v1_migration_dry_shape_and_pending_slots(self) -> None:
        existing = self.project / "existing.png"
        Image.new("RGB", (20, 20), (1, 2, 3)).save(existing)
        v1 = {
            "schema_version": 1,
            "project_dir": ".",
            "phase": "scene_self_review",
            "max_repairs_per_item": 1,
            "artifacts": {},
            "images": [
                {
                    "number": 1,
                    "candidate": "existing.png",
                    "verdict": "pass",
                    "hard_failures": [],
                    "photo_red_flags": [],
                    "repair_count": 0,
                    "repair_mode": None,
                    "repair_file": None,
                    "final_source": "existing.png",
                }
            ],
            "blocking_reasons": [],
        }
        migrated = review_state.migrate_v1(v1, 3)
        migrated_path = self.project / "migrated.json"
        review_state.atomic_write_json(migrated_path, migrated)
        summary = review_state.validate_state(migrated_path)
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual([item["status"] for item in migrated["images"]], [
            "pass",
            "pending",
            "pending",
        ])


if __name__ == "__main__":
    unittest.main()
