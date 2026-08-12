#!/usr/bin/env python3
"""Regression tests for review-state v2/v3 and transport recovery behavior."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = (
    HERE
    if (HERE / "review_state.py").is_file()
    else HERE.parent / "skills" / "oh-my-folkstoryphoto" / "scripts"
)
sys.path.append(str(SCRIPTS_DIR))

import review_state
import package_release
import compose_reference_board
import subscription_image_bridge
import transport_guard


class TransportWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.state = self.project / "review-state.json"
        self.prompt = self.project / "prompt.txt"
        self.prompt.write_text(
            "vertical 4:5 same approved prompt\n"
            + transport_guard.REFERENCE_BOARD_SAFETY_CLAUSE,
            encoding="utf-8",
        )
        self.reference = self.project / "reference.png"
        Image.new("RGB", (32, 24), (12, 34, 56)).save(self.reference)
        self.second_reference = self.project / "second-reference.png"
        Image.new("RGB", (24, 32), (98, 76, 54)).save(self.second_reference)
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

    def upgrade_v3(self) -> None:
        payload = review_state.migrate_v2(review_state.load_json(self.state))
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)

    def preflight_args(
        self,
        number: int,
        *,
        backend: str = transport_guard.BUILT_IN_BACKEND,
        model: str | None = None,
        prompt: Path | None = None,
        references: list[Path] | None = None,
        repair_mode: str | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            state=self.state,
            number=number,
            backend=backend,
            route=None,
            model=model,
            prompt_file=prompt or self.prompt,
            reference=references if references is not None else [self.reference],
            repair_mode=repair_mode,
        )

    def failure_args(self, number: int) -> argparse.Namespace:
        return argparse.Namespace(
            state=self.state,
            number=number,
            error_type="network_error",
            message="network error",
            elapsed_seconds=1.0,
        )

    def clear_cooldown(self, number: int) -> None:
        payload = review_state.load_json(self.state)
        payload["images"][number - 1]["transport"]["next_eligible_at"] = None
        review_state.atomic_write_json(self.state, payload)

    def authorize_board_policy(self) -> dict[str, object]:
        return transport_guard.authorize_reference_board_policy(
            argparse.Namespace(
                state=self.state,
                timeout_seconds=480,
                user_approved=True,
            )
        )

    def make_board(self, name: str = "reference-board.jpg") -> Path:
        output = self.project / name
        compose_reference_board.compose(
            [self.reference, self.second_reference],
            ["first identity anchor", "second object anchor"],
            [None, None],
            output,
            88,
        )
        return output

    def test_three_failures_open_only_scene_circuit(self) -> None:
        for expected in (1, 2, 3):
            transport_guard.preflight(self.preflight_args(1))
            result = transport_guard.record_failure(self.failure_args(1))
            self.assertEqual(result["consecutive_failures"], expected)
            self.clear_cooldown(1)
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

    def test_same_error_on_second_scene_records_warning_without_blocking_backend(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
            self.clear_cooldown(1)
        transport_guard.preflight(self.preflight_args(2))
        result = transport_guard.record_failure(self.failure_args(2))
        self.assertFalse(result["backend_circuit_open"])
        self.assertTrue(result["backend_health_warning"])
        transport_guard.preflight(self.preflight_args(3))

    def test_approved_resume_grants_exactly_one_probe(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
            self.clear_cooldown(1)
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
            state=self.state,
            number=1,
            candidate=self.candidate,
            elapsed_seconds=1.0,
        )
        result = transport_guard.record_success(args)
        self.assertEqual(result["status"], "review_pending")
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["repair_count"], 0)
        self.assertEqual(
            payload["images"][0]["candidate"], "原始生成图/01.png"
        )

    def test_preflight_requires_unambiguous_vertical_4x5(self) -> None:
        missing = self.project / "missing-aspect.txt"
        missing.write_text("ordinary documentary photo", encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "vertical 4:5"):
            transport_guard.preflight(self.preflight_args(1, prompt=missing))

        conflicting = self.project / "conflicting-aspect.txt"
        conflicting.write_text(
            "vertical 4:5 documentary photo, landscape 3:2", encoding="utf-8"
        )
        with self.assertRaisesRegex(review_state.StateError, "conflicting"):
            transport_guard.preflight(self.preflight_args(1, prompt=conflicting))

    def test_record_success_rejects_non_4x5_candidate(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        landscape = self.project / "原始生成图" / "landscape.png"
        Image.new("RGB", (60, 40), (1, 2, 3)).save(landscape)
        with self.assertRaisesRegex(review_state.StateError, "exact vertical 4:5"):
            transport_guard.record_success(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    candidate=landscape,
                    elapsed_seconds=1.0,
                )
            )

    def test_user_approved_aspect_invalidation_archives_and_reopens(self) -> None:
        payload = review_state.load_json(self.state)
        wrong = self.project / "原始生成图" / "wrong.png"
        Image.new("RGB", (60, 40), (9, 8, 7)).save(wrong)
        item = payload["images"][0]
        item["status"] = "pass"
        item["candidate"] = "原始生成图/wrong.png"
        item["final_source"] = "原始生成图/wrong.png"
        review_state.atomic_write_json(self.state, payload)

        result = transport_guard.invalidate_candidate_aspect(
            argparse.Namespace(
                state=self.state,
                number=1,
                archive=Path("返修记录/01-v1-错误画幅.png"),
                user_approved=True,
            )
        )

        payload = review_state.load_json(self.state)
        self.assertEqual(result["old_size"], [60, 40])
        self.assertEqual(payload["images"][0]["status"], "pending")
        self.assertIsNone(payload["images"][0]["candidate"])
        self.assertFalse(wrong.exists())
        self.assertTrue((self.project / result["archived_candidate"]).is_file())

    def test_input_drift_and_corrupt_reference_are_rejected(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        changed_prompt = self.project / "changed.txt"
        changed_prompt.write_text("vertical 4:5 changed prompt", encoding="utf-8")
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
            self.clear_cooldown(1)
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

    def test_subscription_bridge_requires_authorization_and_preserves_inputs(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        bridge = self.preflight_args(
            1,
            backend=transport_guard.SUBSCRIPTION_BRIDGE_BACKEND,
            model="gpt-image-2",
        )
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(bridge)
        transport_guard.authorize_fallback(
            argparse.Namespace(
                state=self.state,
                backend=transport_guard.SUBSCRIPTION_BRIDGE_BACKEND,
                model="gpt-image-2",
                user_approved=True,
            )
        )
        result = transport_guard.preflight(bridge)
        self.assertFalse(result["input_differences"]["prompt_changed"])
        self.assertFalse(result["input_differences"]["references_changed"])
        self.assertIn("codex_subscription_bridge", result["request_file"])

    def test_subscription_bridge_uses_isolated_output_and_attached_reference(self) -> None:
        fake_codex = self.project / "codex"
        fake_codex.write_text("fake", encoding="utf-8")
        output = self.project / "bridge-output.png"
        log = self.project / "bridge.log"

        def fake_run(command, **kwargs):
            workdir = Path(command[command.index("-C") + 1])
            self.assertIn("-i", command)
            self.assertIn("<exact-scene-specification>", kwargs["input"])
            Image.new("RGB", (64, 80), (4, 5, 6)).save(workdir / "out.png")
            return argparse.Namespace(returncode=0)

        with mock.patch.object(
            subscription_image_bridge.subprocess, "run", side_effect=fake_run
        ):
            result = subscription_image_bridge.run_bridge(
                argparse.Namespace(
                    prompt_file=self.prompt,
                    output=output,
                    log=log,
                    reference=[self.reference],
                    size="1024x1280",
                    timeout_seconds=480,
                    codex=str(fake_codex),
                )
            )
        self.assertTrue(output.is_file())
        self.assertEqual(result["backend"], "codex_subscription_bridge")

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

    def test_interrupted_regular_attempt_returns_to_pending(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        result = transport_guard.recover_interrupted(
            argparse.Namespace(
                state=self.state,
                number=1,
                confirm_no_candidate=True,
                reason="turn_interrupted",
            )
        )
        self.assertEqual(result["status"], "pending")
        payload = review_state.load_json(self.state)
        transport = payload["images"][0]["transport"]
        self.assertEqual(transport["consecutive_failures"], 0)
        self.assertFalse(transport["probe_in_flight"])
        self.assertEqual(transport["attempt_history"][-1]["outcome"], "interrupted")

    def test_interrupted_probe_restores_scene_circuit(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
            self.clear_cooldown(1)
        transport_guard.resume_probe(
            argparse.Namespace(state=self.state, number=1, user_approved=True)
        )
        transport_guard.preflight(self.preflight_args(1))
        result = transport_guard.recover_interrupted(
            argparse.Namespace(
                state=self.state,
                number=1,
                confirm_no_candidate=True,
                reason="user_abort",
            )
        )
        self.assertEqual(result["status"], "transport_blocked")
        self.assertTrue(result["scene_circuit_open"])
        self.assertEqual(result["consecutive_failures"], 3)

    def test_only_one_generation_can_be_in_flight(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(self.preflight_args(2))

    def test_cooldown_blocks_immediate_retry(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        result = transport_guard.record_failure(self.failure_args(1))
        self.assertIsNotNone(result["cooldown_until"])
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(self.preflight_args(1))
        self.clear_cooldown(1)
        transport_guard.preflight(self.preflight_args(1))

    def test_old_backend_failure_is_outside_rolling_window(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        payload = review_state.load_json(self.state)
        backend = payload["transport_backends"][transport_guard.BUILT_IN_BACKEND]
        backend["failure_window"][0]["failed_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        review_state.atomic_write_json(self.state, payload)
        transport_guard.preflight(self.preflight_args(2))
        result = transport_guard.record_failure(self.failure_args(2))
        self.assertFalse(result["backend_circuit_open"])
        self.assertEqual(result["affected_images"], [2])

    def test_success_clears_backend_failure_window(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        transport_guard.preflight(self.preflight_args(2))
        transport_guard.record_success(
            argparse.Namespace(
                state=self.state,
                number=2,
                candidate=self.candidate,
                elapsed_seconds=1.0,
            )
        )
        transport_guard.preflight(self.preflight_args(3))
        result = transport_guard.record_failure(self.failure_args(3))
        self.assertFalse(result["backend_circuit_open"])
        self.assertEqual(result["affected_images"], [3])

    def test_legacy_v2_generating_probe_can_be_recovered(self) -> None:
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        item["status"] = "generating"
        item["transport"]["attempts_total"] = 4
        item["transport"]["consecutive_failures"] = 3
        item["transport"]["probe_in_flight"] = True
        payload["transport_backends"][transport_guard.BUILT_IN_BACKEND] = {
            "circuit_open": True,
            "reason": "network_error",
            "error_key": "legacy",
            "affected_images": [1, 2],
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        review_state.atomic_write_json(self.state, payload)
        review_state.validate_state(self.state)
        result = transport_guard.recover_interrupted(
            argparse.Namespace(
                state=self.state,
                number=1,
                confirm_no_candidate=True,
                reason="tool_timeout",
            )
        )
        self.assertEqual(result["status"], "transport_blocked")
        self.assertEqual(result["attempts_total"], 4)
        self.assertEqual(result["consecutive_failures"], 3)

    def test_batch_auto_rolls_after_three_successes(self) -> None:
        self.write_v2(4)
        transport_guard.batch_start(argparse.Namespace(state=self.state))
        for number in (1, 2, 3):
            candidate = self.project / "原始生成图" / f"{number:02d}.png"
            Image.new("RGB", (40, 50), (number, 80, 90)).save(candidate)
            transport_guard.preflight(self.preflight_args(number))
            result = transport_guard.record_success(
                argparse.Namespace(
                    state=self.state,
                    number=number,
                    candidate=candidate,
                    elapsed_seconds=1.0,
                )
            )
        self.assertEqual(result["batch_status"], "stopped")
        self.assertEqual(result["batch_success_count"], 3)
        next_attempt = transport_guard.preflight(self.preflight_args(4))
        self.assertTrue(next_attempt["batch_auto_started"])

    def test_materialized_retry_prompt_is_byte_exact(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        output = self.project / "retry-prompt.txt"
        result = transport_guard.materialize_prompt(
            argparse.Namespace(state=self.state, number=1, output=output)
        )
        self.assertEqual(output.read_bytes(), self.prompt.read_bytes())
        self.assertEqual(
            result["prompt_sha256"],
            transport_guard.sha256_bytes(self.prompt.read_bytes()),
        )
        with self.assertRaises(review_state.StateError):
            transport_guard.materialize_prompt(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    output=output,
                    repair_mode=False,
                )
            )

    def test_content_repair_success_preserves_original_candidate(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_success(
            argparse.Namespace(
                state=self.state,
                number=1,
                candidate=self.candidate,
                elapsed_seconds=1.0,
            )
        )
        repair_prompt = self.project / "repair-prompt.txt"
        repair_prompt.write_text("vertical 4:5; remove only the extra person", encoding="utf-8")
        repair = self.project / "返修记录" / "01-v2.png"
        repair.parent.mkdir()
        Image.new("RGB", (40, 50), (90, 80, 70)).save(repair)
        payload = review_state.load_json(self.state)
        payload["phase"] = "repairing"
        review_state.atomic_write_json(self.state, payload)
        transport_guard.preflight(
            self.preflight_args(
                1,
                prompt=repair_prompt,
                references=[self.candidate, self.reference],
                repair_mode="edit",
            )
        )
        result = transport_guard.record_success(
            argparse.Namespace(
                state=self.state,
                number=1,
                candidate=repair,
                elapsed_seconds=1.0,
            )
        )
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertEqual(item["candidate"], "原始生成图/01.png")
        self.assertEqual(item["repair_count"], 1)
        self.assertEqual(item["repair_mode"], "edit")
        self.assertEqual(item["repair_file"], "返修记录/01-v2.png")
        self.assertEqual(result["repair_mode"], "edit")
        review_state.mark_pass(self.state, 1, "repaired image passed", ["off-center"])
        passed = review_state.load_json(self.state)["images"][0]
        self.assertEqual(passed["status"], "pass")
        self.assertEqual(passed["final_source"], "返修记录/01-v2.png")

    def test_content_repair_transport_failure_restores_review_pending(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_success(
            argparse.Namespace(
                state=self.state,
                number=1,
                candidate=self.candidate,
                elapsed_seconds=1.0,
            )
        )
        repair_prompt = self.project / "repair-prompt.txt"
        repair_prompt.write_text("vertical 4:5; remove only the extra person", encoding="utf-8")
        payload = review_state.load_json(self.state)
        payload["phase"] = "repairing"
        review_state.atomic_write_json(self.state, payload)
        transport_guard.preflight(
            self.preflight_args(
                1,
                prompt=repair_prompt,
                references=[self.candidate, self.reference],
                repair_mode="edit",
            )
        )
        transport_guard.record_failure(self.failure_args(1))
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertEqual(item["status"], "review_pending")
        self.assertEqual(item["repair_count"], 0)
        self.assertEqual(item["candidate"], "原始生成图/01.png")

    def test_interrupted_content_repair_preserves_original_candidate(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_success(
            argparse.Namespace(
                state=self.state,
                number=1,
                candidate=self.candidate,
                elapsed_seconds=1.0,
            )
        )
        repair_prompt = self.project / "repair-prompt.txt"
        repair_prompt.write_text("vertical 4:5; remove only the extra person", encoding="utf-8")
        payload = review_state.load_json(self.state)
        payload["phase"] = "repairing"
        review_state.atomic_write_json(self.state, payload)
        transport_guard.preflight(
            self.preflight_args(
                1,
                prompt=repair_prompt,
                references=[self.candidate, self.reference],
                repair_mode="edit",
            )
        )
        result = transport_guard.recover_interrupted(
            argparse.Namespace(
                state=self.state,
                number=1,
                confirm_no_candidate=True,
                reason="tool_timeout",
            )
        )
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertEqual(result["status"], "review_pending")
        self.assertEqual(item["candidate"], "原始生成图/01.png")
        self.assertEqual(item["repair_count"], 0)
        self.assertIsNone(item["repair_file"])
        superseded = transport_guard.supersede_repair(
            argparse.Namespace(
                state=self.state,
                number=1,
                reason="reduce redundant references after hard timeout",
                user_approved=True,
            )
        )
        self.assertTrue(
            (self.project / superseded["archived_request"]).is_file()
        )
        self.assertFalse(
            transport_guard.repair_request_path(self.project, 1).exists()
        )

    def test_new_request_defaults_to_two_references(self) -> None:
        references = []
        for index in range(3):
            path = self.project / f"reference-{index}.png"
            Image.new("RGB", (16, 16), (index, 2, 3)).save(path)
            references.append(path)
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(
                self.preflight_args(1, references=references)
            )
        args = self.preflight_args(1, references=references)
        args.allow_high_reference_count = True
        result = transport_guard.preflight(args)
        self.assertEqual(result["reference_summary"]["count"], 3)

    def test_user_approved_request_revision_archives_and_resets_scene_failures(self) -> None:
        transport_guard.preflight(self.preflight_args(1))
        transport_guard.record_failure(self.failure_args(1))
        revised_prompt = self.project / "revised-prompt.txt"
        revised_prompt.write_text("vertical 4:5 approved lower-latency prompt", encoding="utf-8")
        result = transport_guard.revise_request(
            argparse.Namespace(
                state=self.state,
                number=1,
                prompt_file=revised_prompt,
                reference=[self.reference],
                reason="replace redundant references with one coherent master",
                user_approved=True,
                allow_high_reference_count=False,
            )
        )
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertTrue((self.project / result["archived_request"]).is_file())
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["transport"]["consecutive_failures"], 0)
        self.assertIsNone(item["transport"]["next_eligible_at"])
        self.assertEqual(item["transport"]["attempts_total"], 1)

    def test_preflight_reports_prompt_budget_and_reference_roles(self) -> None:
        args = self.preflight_args(1)
        args.reference_role = ["Zhao and Lin identity, clothing, and vehicle"]
        result = transport_guard.preflight(args)
        self.assertEqual(result["reference_roles"], args.reference_role)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["prompt_summary"]["latency_risk"], "normal")
        snapshot = review_state.load_json(Path(result["request_file"]))
        self.assertEqual(snapshot["reference_roles"], args.reference_role)

    def test_two_reference_original_uses_480_second_budget(self) -> None:
        args = self.preflight_args(
            1, references=[self.reference, self.second_reference]
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        result = transport_guard.preflight(args)
        self.assertEqual(result["runtime_budget_seconds"], 480)
        self.assertEqual(result["reference_summary"]["physical_attachment_count"], 2)
        self.assertEqual(result["reference_summary"]["logical_source_count"], 2)
        self.assertEqual(result["reference_summary"]["latency_risk"], "elevated")

    def test_authorized_two_reference_request_requires_board_safe_prompt(self) -> None:
        self.authorize_board_policy()
        unsafe = self.project / "unsafe-two-reference.txt"
        unsafe.write_text("vertical 4:5 documentary frame", encoding="utf-8")
        args = self.preflight_args(
            1,
            prompt=unsafe,
            references=[self.reference, self.second_reference],
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        with self.assertRaisesRegex(review_state.StateError, "safety clause"):
            transport_guard.preflight(args)

    def test_479_second_timeout_cannot_stage_reference_board(self) -> None:
        self.authorize_board_policy()
        args = self.preflight_args(
            1, references=[self.reference, self.second_reference]
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        transport_guard.preflight(args)
        transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="timeout",
                message="hard timeout",
                elapsed_seconds=479.0,
            )
        )
        board = self.make_board()
        with self.assertRaisesRegex(review_state.StateError, "shorter than 480"):
            transport_guard.stage_reference_board_fallback(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    reference_board=board,
                    confirm_no_candidate=True,
                )
            )

    def test_480_second_timeout_stages_board_without_prompt_or_repair_drift(self) -> None:
        args = self.preflight_args(
            1, references=[self.reference, self.second_reference]
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        initial = transport_guard.preflight(args)
        failed = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="timeout",
                message="two-reference hard timeout",
                elapsed_seconds=480.0,
            )
        )
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        request = review_state.load_json(transport_guard.request_path(self.project, 1))
        self.assertTrue(failed["retry_ready"])
        self.assertEqual(failed["auto_recovery"]["level"], 1)
        self.assertEqual(request["prompt_sha256"], initial["prompt_sha256"])
        self.assertEqual(len(request["references"]), 1)
        self.assertIn("reference_board", request["references"][0])
        self.assertEqual(item["repair_count"], 0)
        self.assertEqual(item["transport"]["consecutive_failures"], 1)
        self.assertIsNone(item["transport"]["next_eligible_at"])
        board = Path(request["references"][0]["path"])
        retry = self.preflight_args(1, references=[board])
        retry.reference_role = request["reference_roles"]
        resumed = transport_guard.preflight(retry)
        self.assertEqual(resumed["runtime_budget_seconds"], 600)
        self.assertTrue(resumed["reference_summary"]["contains_reference_board"])
        second = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="timeout",
                message="reference board also timed out",
                elapsed_seconds=600.0,
            )
        )
        self.assertEqual(second["auto_recovery"]["level"], 2)
        low_request = review_state.load_json(transport_guard.request_path(self.project, 1))
        low_board = Path(low_request["references"][0]["path"])
        with Image.open(low_board) as image:
            self.assertEqual(image.size, (768, 960))
        low_retry = self.preflight_args(1, references=[low_board])
        low_retry.reference_role = low_request["reference_roles"]
        transport_guard.preflight(low_retry)
        third = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="no_candidate",
                message="low board returned no candidate",
                elapsed_seconds=600.0,
            )
        )
        self.assertTrue(third["scene_circuit_open"])
        self.assertIsNone(third["auto_recovery"])
        transport_guard.preflight(self.preflight_args(2))

    def test_reference_jobs_use_same_auto_recovery_and_do_not_block_peers(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        for reference_id in ("hero", "location"):
            review_state.register_reference_job(
                self.state, reference_id, "character" if reference_id == "hero" else "location", "角色参考"
            )
        args = argparse.Namespace(
            state=self.state,
            reference_id="hero",
            prompt_file=self.prompt,
            reference=[self.reference, self.second_reference],
            reference_role=["hero identity", "fixed clothing"],
        )
        transport_guard.reference_preflight(args)
        first = transport_guard.record_reference_failure(
            argparse.Namespace(
                state=self.state,
                reference_id="hero",
                error_type="timeout",
                message="timeout",
                elapsed_seconds=480.0,
            )
        )
        self.assertTrue(first["retry_ready"])
        request = review_state.load_json(
            transport_guard.reference_request_path(self.project, "hero")
        )
        self.assertEqual(request["auto_recovery"]["level"], 1)
        peer = transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state,
                reference_id="location",
                prompt_file=self.prompt,
                reference=[],
                reference_role=[],
            )
        )
        self.assertEqual(peer["reference_id"], "location")
        reference_candidate = self.project / "角色参考" / "location.png"
        Image.new("RGB", (50, 50), (7, 8, 9)).save(reference_candidate)
        transport_guard.record_reference_success(
            argparse.Namespace(
                state=self.state,
                reference_id="location",
                candidate=reference_candidate,
            )
        )
        passed = review_state.mark_reference_pass(
            self.state, "location", "location layout approved"
        )
        self.assertEqual(passed["status"], "pass")

    def test_generation_phase_guards_cannot_bypass_approvals(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "awaiting_reference_approval"
        review_state.atomic_write_json(self.state, payload)
        with self.assertRaisesRegex(review_state.StateError, "scene_self_review"):
            transport_guard.preflight(self.preflight_args(1))

    def test_blocked_report_waits_until_other_jobs_finish(self) -> None:
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1))
            transport_guard.record_failure(self.failure_args(1))
            self.clear_cooldown(1)
        report_path = self.project / "生成阻塞报告.md"
        with self.assertRaisesRegex(review_state.StateError, "finish first"):
            transport_guard.prepare_blocked_report(
                argparse.Namespace(state=self.state, output=report_path)
            )
        payload = review_state.load_json(self.state)
        for number in (2, 3):
            candidate = self.project / "原始生成图" / f"{number:02d}.png"
            Image.new("RGB", (40, 50), (number, 3, 4)).save(candidate)
            item = payload["images"][number - 1]
            item["candidate"] = str(candidate.relative_to(self.project))
            item["final_source"] = str(candidate.relative_to(self.project))
            item["status"] = "pass"
        review_state.atomic_write_json(self.state, payload)
        result = transport_guard.prepare_blocked_report(
            argparse.Namespace(state=self.state, output=report_path)
        )
        self.assertEqual(result["blocked_images"], [1])
        self.assertIn("正式分镜 | 1", report_path.read_text(encoding="utf-8"))

    def test_board_fallback_requires_authorization_and_untampered_lineage(self) -> None:
        payload = review_state.load_json(self.state)
        payload["images"][0]["transport"]["auto_recovery_level"] = 2
        review_state.atomic_write_json(self.state, payload)
        args = self.preflight_args(
            1, references=[self.reference, self.second_reference]
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        transport_guard.preflight(args)
        transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="timeout",
                message="two-reference hard timeout",
                elapsed_seconds=480.0,
            )
        )
        payload = review_state.load_json(self.state)
        payload["transport_batch"]["status"] = "stopped"
        payload["transport_batch"]["stopped_reason"] = "legacy_manual_fallback"
        review_state.atomic_write_json(self.state, payload)
        board = self.make_board()
        with self.assertRaisesRegex(review_state.StateError, "not authorized"):
            transport_guard.stage_reference_board_fallback(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    reference_board=board,
                    confirm_no_candidate=True,
                )
            )
        self.authorize_board_policy()
        sidecar = compose_reference_board.sidecar_path(board)
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        manifest["sources"][0]["sha256"] = "0" * 64
        sidecar.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(review_state.StateError, "hash no longer matches"):
            transport_guard.stage_reference_board_fallback(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    reference_board=board,
                    confirm_no_candidate=True,
                )
            )

    def test_board_fallback_rejects_existing_candidate(self) -> None:
        self.authorize_board_policy()
        args = self.preflight_args(
            1, references=[self.reference, self.second_reference]
        )
        args.reference_role = ["first identity anchor", "second object anchor"]
        transport_guard.preflight(args)
        transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="timeout",
                message="two-reference hard timeout",
                elapsed_seconds=480.0,
            )
        )
        payload = review_state.load_json(self.state)
        payload["images"][0]["candidate"] = "原始生成图/01.png"
        payload["images"][0]["status"] = "review_pending"
        review_state.atomic_write_json(self.state, payload)
        board = self.make_board()
        with self.assertRaisesRegex(review_state.StateError, "candidate already exists"):
            transport_guard.stage_reference_board_fallback(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    reference_board=board,
                    confirm_no_candidate=True,
                )
            )

    def test_reference_role_count_must_match_reference_count(self) -> None:
        args = self.preflight_args(1)
        args.reference_role = ["first", "second"]
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(args)

    def test_deferred_repairs_require_all_originals_report_and_user_approval(self) -> None:
        payload = review_state.load_json(self.state)
        payload["repair_policy"] = {
            "mode": "deferred_user_approved",
            "report_file": None,
            "report_generated_at": None,
            "approved_numbers": [],
            "approved_at": None,
        }
        for number, item in enumerate(payload["images"], start=1):
            candidate = self.project / "原始生成图" / f"{number:02d}.png"
            Image.new("RGB", (40, 50), (number, 8, 9)).save(candidate)
            item["candidate"] = str(candidate.relative_to(self.project))
            item["status"] = "review_pending"
        review_state.atomic_write_json(self.state, payload)
        review_state.queue_repair(
            self.state,
            1,
            "edit",
            ["extra person"],
            "remove only the extra person",
            [],
        )
        review_state.mark_pass(self.state, 2, "original passed", [])
        review_state.mark_pass(self.state, 3, "original passed", [])
        report = review_state.prepare_repair_report(
            self.state, Path("返修报告.md")
        )
        self.assertEqual(report["phase"], "awaiting_repair_approval")
        self.assertTrue((self.project / "返修报告.md").is_file())
        repair_prompt = self.project / "repair-deferred.txt"
        repair_prompt.write_text("vertical 4:5; remove only the extra person", encoding="utf-8")
        with self.assertRaises(review_state.StateError):
            transport_guard.preflight(
                self.preflight_args(
                    1,
                    prompt=repair_prompt,
                    references=[self.project / "原始生成图" / "01.png"],
                    repair_mode="edit",
                )
            )
        review_state.authorize_repairs(self.state, [1], True)
        result = transport_guard.preflight(
            self.preflight_args(
                1,
                prompt=repair_prompt,
                references=[self.project / "原始生成图" / "01.png"],
                repair_mode="edit",
            )
        )
        self.assertEqual(result["repair_mode"], "edit")

    def test_v3_derivative_failure_releases_active_attempt_and_blocks_only_item(self) -> None:
        self.upgrade_v3()
        transport_guard.preflight(self.preflight_args(1))
        self.reference.unlink()
        result = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="no_candidate",
                message="no candidate",
                elapsed_seconds=999.0,
            )
        )
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        self.assertEqual(item["status"], "transport_blocked")
        self.assertIsNone(item["transport"]["active_attempt"])
        self.assertEqual(item["transport"]["recovery"]["state"], "failed")
        self.assertTrue(result["recovery_error"])
        transport_guard.preflight(self.preflight_args(2, references=[]))

    def test_v3_reference_phase_status_is_phase_aware(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        review_state.register_reference_job(
            self.state, "hero", "character", "角色参考"
        )
        status = transport_guard.batch_status(argparse.Namespace(state=self.state))
        self.assertEqual(status["eligible_job_type"], "reference")
        self.assertEqual(status["ready"], [])
        self.assertEqual(status["reference_ready"], ["hero"])
        self.assertEqual(status["next_runnable"], {"job_type": "reference", "job_id": "hero"})

    def test_v3_interrupted_reference_attempt_can_be_recovered(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        review_state.register_reference_job(self.state, "hero", "character", "角色参考")
        transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state,
                reference_id="hero",
                prompt_file=self.prompt,
                reference=[],
                reference_role=[],
            )
        )
        result = transport_guard.recover_interrupted(
            argparse.Namespace(
                state=self.state,
                number=None,
                reference_id="hero",
                reason="turn_interrupted",
                confirm_no_candidate=True,
            )
        )
        self.assertEqual(result["job_type"], "reference")
        self.assertEqual(result["status"], "pending")
        status = transport_guard.batch_status(argparse.Namespace(state=self.state))
        self.assertEqual(status["reference_ready"], ["hero"])

    def test_v3_edit_recovery_compresses_target_and_support_separately(self) -> None:
        self.write_v2(1, phase="repairing")
        payload = review_state.load_json(self.state)
        payload["repair_policy"] = {
            "mode": "deferred_user_approved",
            "report_file": "返修报告.md",
            "report_generated_at": "2026-01-01T00:00:00+00:00",
            "approved_numbers": [1],
            "approved_at": "2026-01-01T00:00:00+00:00",
        }
        payload["images"][0]["candidate"] = "原始生成图/01.png"
        payload["images"][0]["status"] = "review_pending"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        repair_prompt = self.project / "repair-v3.txt"
        repair_prompt.write_text("vertical 4:5 repair one local defect", encoding="utf-8")
        args = self.preflight_args(
            1,
            prompt=repair_prompt,
            references=[self.candidate, self.reference],
            repair_mode="edit",
        )
        args.reference_role = ["edit target", "identity support"]
        transport_guard.preflight(args)
        result = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state,
                number=1,
                error_type="no_candidate",
                message="no candidate",
                elapsed_seconds=1.0,
            )
        )
        recovery = result["auto_recovery"]
        self.assertEqual(recovery["operation"], "edit_attachments_resize_compress")
        self.assertEqual(len(recovery["next_references"]), 2)
        self.assertEqual(recovery["next_reference_roles"], ["edit target", "identity support"])
        request = review_state.load_json(transport_guard.repair_request_path(self.project, 1))
        self.assertEqual(len(request["references"]), 2)
        self.assertFalse(any(entry.get("reference_board") for entry in request["references"]))

    def test_v3_timeout_uses_persisted_clock_not_reported_elapsed(self) -> None:
        self.upgrade_v3()
        transport_guard.preflight(self.preflight_args(1, references=[]))
        with self.assertRaisesRegex(review_state.StateError, "persisted runtime budget"):
            transport_guard.record_failure(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    error_type="timeout",
                    message="claimed timeout",
                    elapsed_seconds=9999.0,
                )
            )
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["status"], "generating")

    def test_v3_reference_candidate_must_stay_in_registered_output_dir(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        review_state.register_reference_job(self.state, "hero", "character", "角色参考")
        transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state,
                reference_id="hero",
                prompt_file=self.prompt,
                reference=[],
                reference_role=[],
            )
        )
        wrong = self.project / "wrong-reference.png"
        Image.new("RGB", (50, 50), (1, 2, 3)).save(wrong)
        with self.assertRaisesRegex(review_state.StateError, "registered output_dir"):
            transport_guard.record_reference_success(
                argparse.Namespace(state=self.state, reference_id="hero", candidate=wrong)
            )

    def test_v3_reference_review_regenerates_once_then_needs_user(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        review_state.register_reference_job(self.state, "hero", "character", "角色参考")
        pre_args = argparse.Namespace(
            state=self.state,
            reference_id="hero",
            prompt_file=self.prompt,
            reference=[],
            reference_role=[],
        )
        transport_guard.reference_preflight(pre_args)
        first = self.project / "角色参考" / "hero-v1.png"
        Image.new("RGB", (50, 50), (4, 5, 6)).save(first)
        transport_guard.record_reference_success(
            argparse.Namespace(state=self.state, reference_id="hero", candidate=first)
        )
        first_review = review_state.record_reference_review(
            self.state, "hero", "fail", ["fixed clothing drift"], "regenerate once"
        )
        self.assertEqual(first_review["status"], "pending")
        corrected_prompt = self.project / "corrected-reference.txt"
        transport_guard.materialize_reference_prompt(
            argparse.Namespace(state=self.state, reference_id="hero", output=corrected_prompt)
        )
        pre_args.prompt_file = corrected_prompt
        transport_guard.reference_preflight(pre_args)
        second = self.project / "角色参考" / "hero-v2.png"
        Image.new("RGB", (50, 50), (7, 8, 9)).save(second)
        transport_guard.record_reference_success(
            argparse.Namespace(state=self.state, reference_id="hero", candidate=second)
        )
        second_review = review_state.record_reference_review(
            self.state, "hero", "fail", ["identity still unstable"], "needs user"
        )
        self.assertEqual(second_review["status"], "needs_user")
        state = review_state.load_json(self.state)
        self.assertEqual(len(state["reference_jobs"][0]["candidate_versions"]), 2)

    def test_v3_zero_reference_blocks_after_three_without_recovery(self) -> None:
        self.upgrade_v3()
        for _ in range(3):
            transport_guard.preflight(self.preflight_args(1, references=[]))
            transport_guard.record_failure(
                argparse.Namespace(
                    state=self.state,
                    number=1,
                    error_type="no_candidate",
                    message="no candidate",
                    elapsed_seconds=1.0,
                )
            )
            self.clear_cooldown(1)
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["images"][0]["status"], "transport_blocked")
        self.assertEqual(payload["images"][0]["transport"]["auto_recovery_level"], 0)

    def test_v3_single_reference_runs_both_recovery_levels_from_origin(self) -> None:
        self.upgrade_v3()
        large_reference = self.project / "large-reference.png"
        Image.new("RGB", (1600, 1200), (11, 22, 33)).save(large_reference)
        args = self.preflight_args(1, references=[large_reference])
        args.reference_role = ["hero identity"]
        transport_guard.preflight(args)
        first = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state, number=1, error_type="no_candidate",
                message="no candidate", elapsed_seconds=1.0,
            )
        )
        first_path = Path(first["auto_recovery"]["next_references"][0]["path"])
        with Image.open(first_path) as image:
            self.assertEqual(max(image.size), 1024)
        args.reference = [first_path]
        transport_guard.preflight(args)
        second = transport_guard.record_failure(
            argparse.Namespace(
                state=self.state, number=1, error_type="no_candidate",
                message="no candidate", elapsed_seconds=1.0,
            )
        )
        second_path = Path(second["auto_recovery"]["next_references"][0]["path"])
        with Image.open(second_path) as image:
            self.assertEqual(max(image.size), 768)
        history = review_state.load_json(self.state)["images"][0]["transport"]["auto_recovery_history"]
        self.assertEqual(history[0]["origin_references"][0]["sha256"], history[1]["origin_references"][0]["sha256"])

    def test_v3_mixed_backend_events_warn_without_key_error(self) -> None:
        self.upgrade_v3()
        transport_guard.preflight(self.preflight_args(1, references=[]))
        transport_guard.record_failure(
            argparse.Namespace(
                state=self.state, number=1, error_type="network_error",
                message="network error", elapsed_seconds=1.0,
            )
        )
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        review_state.register_reference_job(self.state, "hero", "character", "角色参考")
        transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state, reference_id="hero", prompt_file=self.prompt,
                reference=[], reference_role=[],
            )
        )
        result = transport_guard.record_reference_failure(
            argparse.Namespace(
                state=self.state, reference_id="hero", error_type="network_error",
                message="network error", elapsed_seconds=1.0,
            )
        )
        self.assertTrue(result["backend_health_warning"])
        events = review_state.load_json(self.state)["transport_backends"][transport_guard.BUILT_IN_BACKEND]["failure_window"]
        self.assertEqual({event["job_type"] for event in events}, {"scene", "reference"})

    def test_v3_batch_rolls_after_fifteen_minutes(self) -> None:
        self.upgrade_v3()
        payload = review_state.load_json(self.state)
        payload["transport_batch"] = transport_guard.new_batch_record()
        old_id = payload["transport_batch"]["batch_id"]
        payload["transport_batch"]["started_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=901)
        ).isoformat()
        review_state.atomic_write_json(self.state, payload)
        result = transport_guard.preflight(self.preflight_args(1, references=[]))
        self.assertTrue(result["batch_auto_started"])
        self.assertNotEqual(result["batch_id"], old_id)

    def test_v2_to_v3_cli_dry_run_and_backup(self) -> None:
        before = self.state.read_bytes()
        with mock.patch(
            "sys.argv",
            ["review_state.py", "migrate", "--state", str(self.state), "--to-version", "3", "--dry-run"],
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(review_state.main(), 0)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(list(self.project.glob("review-state.v2.*.json")))
        with mock.patch(
            "sys.argv",
            ["review_state.py", "migrate", "--state", str(self.state), "--to-version", "3"],
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(review_state.main(), 0)
        self.assertEqual(review_state.load_json(self.state)["schema_version"], 3)
        self.assertEqual(len(list(self.project.glob("review-state.v2.*.json"))), 1)

    def test_v3_reference_derivative_failure_does_not_lock_peer(self) -> None:
        payload = review_state.load_json(self.state)
        payload["phase"] = "reference_self_review"
        review_state.atomic_write_json(self.state, payload)
        self.upgrade_v3()
        for reference_id in ("hero", "location"):
            review_state.register_reference_job(self.state, reference_id, "character", "角色参考")
        transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state, reference_id="hero", prompt_file=self.prompt,
                reference=[self.reference], reference_role=["hero identity"],
            )
        )
        self.reference.unlink()
        result = transport_guard.record_reference_failure(
            argparse.Namespace(
                state=self.state, reference_id="hero", error_type="no_candidate",
                message="no candidate", elapsed_seconds=1.0,
            )
        )
        self.assertTrue(result["recovery_error"])
        payload = review_state.load_json(self.state)
        self.assertEqual(payload["reference_jobs"][0]["status"], "transport_blocked")
        self.assertIsNone(payload["reference_jobs"][0]["transport"]["active_attempt"])
        peer = transport_guard.reference_preflight(
            argparse.Namespace(
                state=self.state, reference_id="location", prompt_file=self.prompt,
                reference=[], reference_role=[],
            )
        )
        self.assertEqual(peer["reference_id"], "location")

    def test_v3_batch_status_reconciles_committed_recovery_transaction(self) -> None:
        self.upgrade_v3()
        payload = review_state.load_json(self.state)
        item = payload["images"][0]
        request_file = self.project / "生成请求" / "01.json"
        request_file.parent.mkdir(parents=True, exist_ok=True)
        request = transport_guard.create_request(
            1,
            transport_guard.BUILT_IN_BACKEND,
            transport_guard.DEFAULT_BUILT_IN_ROUTE,
            None,
            self.prompt.read_text(encoding="utf-8"),
            transport_guard.sha256_file(self.prompt),
            [],
            [],
        )
        request["recovery_transaction_id"] = "tx-committed"
        request["auto_recovery"] = {"level": 1}
        review_state.atomic_write_json(request_file, request)
        item["status"] = "pending"
        item["transport"]["request_file"] = "生成请求/01.json"
        item["transport"]["recovery"] = {
            "level": 0,
            "state": "staging",
            "transaction": {
                "transaction_id": "tx-committed",
                "next_level": 1,
                "prior_status": "pending",
            },
            "last_error": None,
        }
        review_state.atomic_write_json(self.state, payload)
        status = transport_guard.batch_status(argparse.Namespace(state=self.state))
        self.assertTrue(status["reconciled_recoveries"][0]["committed"])
        item = review_state.load_json(self.state)["images"][0]
        self.assertEqual(item["transport"]["recovery"]["state"], "ready")
        self.assertEqual(item["transport"]["auto_recovery_level"], 1)

    def test_v3_rejects_legacy_manual_recovery_interfaces(self) -> None:
        self.upgrade_v3()
        with self.assertRaisesRegex(review_state.StateError, "schema v2 compatibility"):
            transport_guard.resume_probe(
                argparse.Namespace(state=self.state, number=1, user_approved=True)
            )

    def test_v3_final_state_is_accepted_by_packaging_guard(self) -> None:
        self.write_v2(1)
        self.upgrade_v3()
        manifest = self.project / "release-manifest.json"
        manifest.write_text(
            json.dumps(
                {"images": [{"number": 1, "source": "原始生成图/01.png"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        payload = review_state.load_json(self.state)
        payload["phase"] = "final_self_review"
        payload["artifacts"]["release_manifest"] = "release-manifest.json"
        payload["images"][0].update(
            {
                "status": "pass",
                "candidate": "原始生成图/01.png",
                "final_source": "原始生成图/01.png",
            }
        )
        review_state.atomic_write_json(self.state, payload)
        package_release.verify_state_for_packaging(self.state, manifest.resolve())


if __name__ == "__main__":
    unittest.main()
