#!/usr/bin/env python3
"""Validate, transition, and migrate folk-story photo review state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASES = {
    "drafting",
    "text_self_review",
    "awaiting_plan_approval",
    "reference_self_review",
    "awaiting_reference_approval",
    "scene_self_review",
    "awaiting_repair_approval",
    "repairing",
    "final_self_review",
    "complete",
    "needs_user",
}
V1_VERDICTS = {"pending", "pass", "fail", "needs_user"}
V2_STATUSES = {
    "pending",
    "generating",
    "transport_blocked",
    "review_pending",
    "pass",
    "needs_user",
}
SUPPORTED_SCHEMAS = {1, 2, 3}
FINAL_PHASES = {"final_self_review", "complete"}
REQUIRED_COMPLETE_ARTIFACTS = ("self_review", "acceptance", "release_manifest")
REFERENCE_BOARD_TIMEOUT_SECONDS = 480
LEGAL_TRANSITIONS = {
    "drafting": {"text_self_review", "needs_user"},
    "text_self_review": {"awaiting_plan_approval", "needs_user"},
    "awaiting_plan_approval": {"drafting", "reference_self_review", "needs_user"},
    "reference_self_review": {"awaiting_reference_approval", "needs_user"},
    "awaiting_reference_approval": {
        "reference_self_review",
        "scene_self_review",
        "needs_user",
    },
    "scene_self_review": {
        "awaiting_repair_approval",
        "final_self_review",
        "needs_user",
    },
    "awaiting_repair_approval": {"repairing", "scene_self_review", "needs_user"},
    "repairing": {"awaiting_repair_approval", "final_self_review", "needs_user"},
    "final_self_review": {"repairing", "complete", "needs_user"},
    "complete": set(),
    "needs_user": {
        "drafting",
        "text_self_review",
        "reference_self_review",
        "scene_self_review",
        "awaiting_repair_approval",
        "repairing",
        "final_self_review",
    },
}
APPROVAL_TRANSITIONS = {
    ("awaiting_plan_approval", "reference_self_review"),
    ("awaiting_reference_approval", "scene_self_review"),
    ("awaiting_repair_approval", "repairing"),
}


class StateError(ValueError):
    """Raised when review state violates the public contract."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise StateError(f"unable to read {path}: {exc}") from exc


def resolve_project_dir(state_path: Path, raw_project_dir: Any) -> Path:
    if not isinstance(raw_project_dir, str) or not raw_project_dir.strip():
        raise StateError("project_dir must be a non-empty path string")
    project_dir = Path(raw_project_dir).expanduser()
    if not project_dir.is_absolute():
        project_dir = state_path.parent / project_dir
    return project_dir.resolve()


def resolve_project_path(project_dir: Path, raw_path: Any, field: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise StateError(f"{field} must be a non-empty path string")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve()


def require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise StateError(f"{field} must be an array of non-empty strings")
    return value


def require_nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StateError(f"{field} must be a non-negative integer")
    return value


def validate_transition(current: str, target: str, user_approved: bool = False) -> None:
    if current not in PHASES or target not in PHASES:
        raise StateError("transition phases must be valid review phases")
    if target not in LEGAL_TRANSITIONS[current]:
        raise StateError(f"illegal phase transition: {current} -> {target}")
    if (current, target) in APPROVAL_TRANSITIONS and not user_approved:
        raise StateError(f"{current} -> {target} requires explicit --user-approved")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".json", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=path.suffix or ".txt", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_release_manifest(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, list):
        raise StateError("release manifest must contain an images array")
    if not all(isinstance(item, dict) for item in images):
        raise StateError("release manifest images must be objects")
    return images


def validate_common(
    state_path: Path, payload: dict[str, Any]
) -> tuple[str, Path, dict[str, Any], list[dict[str, Any]], list[str]]:
    phase = payload.get("phase")
    if phase not in PHASES:
        raise StateError(f"phase must be one of {sorted(PHASES)}")
    if payload.get("max_repairs_per_item") != 1:
        raise StateError("max_repairs_per_item must equal 1")

    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    if not project_dir.is_dir():
        raise StateError(f"project_dir does not exist: {project_dir}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise StateError("artifacts must be an object")
    images = payload.get("images")
    if not isinstance(images, list):
        raise StateError("images must be an array")
    blocking_reasons = require_string_list(
        payload.get("blocking_reasons", []), "blocking_reasons"
    )
    return phase, project_dir, artifacts, images, blocking_reasons


def validate_content_fields(
    item: dict[str, Any], prefix: str, status: str
) -> tuple[int, list[str], list[str]]:
    hard_failures = require_string_list(
        item.get("hard_failures", []), f"{prefix}.hard_failures"
    )
    red_flags = require_string_list(
        item.get("photo_red_flags", []), f"{prefix}.photo_red_flags"
    )
    repair_count = require_nonnegative_int(
        item.get("repair_count"), f"{prefix}.repair_count"
    )
    if repair_count not in (0, 1):
        raise StateError(f"{prefix}.repair_count must be 0 or 1")
    if status == "pass" and hard_failures:
        raise StateError(f"{prefix} cannot pass with hard failures")
    if status == "pass" and len(red_flags) >= 3:
        raise StateError(f"{prefix} cannot pass with three or more photo red flags")
    if status == "needs_user" and repair_count != 1:
        raise StateError(f"{prefix}.needs_user requires repair_count 1")
    repair_mode = item.get("repair_mode")
    if repair_mode not in (None, "edit", "regenerate"):
        raise StateError(f"{prefix}.repair_mode must be edit, regenerate or null")
    if repair_count == 1 and repair_mode is None:
        raise StateError(f"{prefix}.repair_mode is required after a repair")
    return repair_count, hard_failures, red_flags


def validate_candidate_files(
    project_dir: Path,
    item: dict[str, Any],
    prefix: str,
    status: str,
    repair_count: int,
    *,
    candidate_required: bool,
) -> Path | None:
    candidate_raw = item.get("candidate")
    candidate: Path | None = None
    if candidate_raw is not None:
        candidate = resolve_project_path(project_dir, candidate_raw, f"{prefix}.candidate")
        if not candidate.is_file():
            raise StateError(f"{prefix}.candidate does not exist: {candidate}")
    elif candidate_required:
        raise StateError(f"{prefix}.candidate is required for status {status}")

    final_source_raw = item.get("final_source")
    final_source: Path | None = None
    if status == "pass":
        final_source = resolve_project_path(
            project_dir, final_source_raw, f"{prefix}.final_source"
        )
        if not final_source.is_file():
            raise StateError(f"{prefix}.final_source does not exist: {final_source}")
    elif final_source_raw is not None:
        raise StateError(f"{prefix}.final_source must be null unless status is pass")

    repair_file_raw = item.get("repair_file")
    if repair_count == 1:
        repair_file = resolve_project_path(
            project_dir, repair_file_raw, f"{prefix}.repair_file"
        )
        if not repair_file.is_file():
            raise StateError(f"{prefix}.repair_file does not exist: {repair_file}")
    elif repair_file_raw is not None:
        raise StateError(f"{prefix}.repair_file requires repair_count 1")
    return final_source


def validate_transport(transport: Any, prefix: str) -> None:
    if not isinstance(transport, dict):
        raise StateError(f"{prefix}.transport must be an object")
    backend = transport.get("backend")
    if not isinstance(backend, str) or not backend:
        raise StateError(f"{prefix}.transport.backend must be a non-empty string")
    route = transport.get("route")
    if route is not None and (not isinstance(route, str) or not route):
        raise StateError(f"{prefix}.transport.route must be null or a non-empty string")
    for field in ("attempts_total", "consecutive_failures"):
        require_nonnegative_int(transport.get(field), f"{prefix}.transport.{field}")
    for field in ("circuit_open", "probe_granted", "probe_in_flight"):
        if not isinstance(transport.get(field), bool):
            raise StateError(f"{prefix}.transport.{field} must be boolean")
    for field in (
        "last_error",
        "last_error_type",
        "error_fingerprint",
        "backend_error_key",
        "prompt_sha256",
    ):
        value = transport.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise StateError(f"{prefix}.transport.{field} must be null or a string")
    references = transport.get("reference_sha256")
    if not isinstance(references, list):
        raise StateError(f"{prefix}.transport.reference_sha256 must be an array")
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}] must be an object"
            )
        if not isinstance(reference.get("path"), str) or not reference["path"]:
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}].path is required"
            )
        digest = reference.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise StateError(
                f"{prefix}.transport.reference_sha256[{index}].sha256 is invalid"
            )
    active_attempt = transport.get("active_attempt")
    if active_attempt is not None:
        if not isinstance(active_attempt, dict):
            raise StateError(f"{prefix}.transport.active_attempt must be an object or null")
        for field in ("attempt_id", "started_at", "request_fingerprint"):
            value = active_attempt.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(
                    f"{prefix}.transport.active_attempt.{field} is required"
                )
        if not isinstance(active_attempt.get("probe"), bool):
            raise StateError(
                f"{prefix}.transport.active_attempt.probe must be boolean"
            )
    history = transport.get("attempt_history", [])
    if not isinstance(history, list) or not all(
        isinstance(entry, dict) for entry in history
    ):
        raise StateError(f"{prefix}.transport.attempt_history must be an array")
    next_eligible = transport.get("next_eligible_at")
    if next_eligible is not None and (
        not isinstance(next_eligible, str) or not next_eligible
    ):
        raise StateError(
            f"{prefix}.transport.next_eligible_at must be null or a timestamp"
        )


def validate_v3_transport(transport: dict[str, Any], prefix: str) -> None:
    recovery = transport.get("recovery")
    if not isinstance(recovery, dict):
        raise StateError(f"{prefix}.transport.recovery must be an object in schema v3")
    if recovery.get("level") not in (0, 1, 2):
        raise StateError(f"{prefix}.transport.recovery.level must be 0, 1 or 2")
    if recovery.get("state") not in {"idle", "staging", "ready", "failed"}:
        raise StateError(
            f"{prefix}.transport.recovery.state must be idle, staging, ready or failed"
        )
    transaction = recovery.get("transaction")
    if transaction is not None and not isinstance(transaction, dict):
        raise StateError(f"{prefix}.transport.recovery.transaction must be an object or null")
    if recovery.get("state") == "staging" and not transaction:
        raise StateError(f"{prefix}.transport.recovery.staging requires a transaction")


def validate_manifest(
    project_dir: Path,
    artifacts: dict[str, Any],
    normalized: list[dict[str, Any]],
    phase: str,
) -> None:
    raw_manifest = artifacts.get("release_manifest")
    if not raw_manifest:
        if phase in FINAL_PHASES:
            raise StateError("final phases require artifacts.release_manifest")
        return
    manifest_path = resolve_project_path(
        project_dir, raw_manifest, "artifacts.release_manifest"
    )
    if not manifest_path.exists():
        if phase in FINAL_PHASES:
            raise StateError(f"release manifest does not exist: {manifest_path}")
        return

    release_images = load_release_manifest(manifest_path)
    pass_sources = {
        item["number"]: item["final_source"]
        for item in normalized
        if item["status"] == "pass"
    }
    manifest_numbers: list[int] = []
    for index, release_item in enumerate(release_images):
        number = release_item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(
                f"release_manifest.images[{index}].number must be a positive integer"
            )
        source = resolve_project_path(
            project_dir,
            release_item.get("source"),
            f"release_manifest.images[{index}].source",
        )
        if not source.is_file():
            raise StateError(f"release manifest source does not exist: {source}")
        if number in manifest_numbers:
            raise StateError(f"release manifest contains duplicate image {number}")
        manifest_numbers.append(number)
        if number not in pass_sources:
            raise StateError(f"release manifest image {number} is not marked pass")
        if source != pass_sources[number]:
            raise StateError(
                f"release manifest image {number} does not match final_source"
            )

    if phase in FINAL_PHASES:
        expected = [item["number"] for item in normalized]
        if sorted(manifest_numbers) != expected:
            raise StateError(
                "final release manifest must include every planned image exactly once"
            )


def validate_v1(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        prefix = f"images[{index}]"
        if not isinstance(item, dict):
            raise StateError(f"{prefix} must be an object")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(f"{prefix}.number must be a positive integer")
        verdict = item.get("verdict")
        if verdict not in V1_VERDICTS:
            raise StateError(f"{prefix}.verdict must be one of {sorted(V1_VERDICTS)}")
        repair_count, _, _ = validate_content_fields(item, prefix, verdict)
        final_source = validate_candidate_files(
            project_dir,
            item,
            prefix,
            verdict,
            repair_count,
            candidate_required=True,
        )
        normalized.append(
            {"number": number, "status": verdict, "final_source": final_source}
        )
    validate_numbering(normalized, len(normalized))
    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 1)


def validate_v2(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    phase, project_dir, artifacts, images, blocking_reasons = validate_common(
        state_path, payload
    )
    planned_count = payload.get("planned_count")
    if (
        not isinstance(planned_count, int)
        or isinstance(planned_count, bool)
        or planned_count < 1
    ):
        raise StateError("planned_count must be a positive integer")

    backends = payload.get("transport_backends", {})
    if not isinstance(backends, dict):
        raise StateError("transport_backends must be an object")
    for name, backend in backends.items():
        if not isinstance(name, str) or not name or not isinstance(backend, dict):
            raise StateError("transport_backends entries must be named objects")
        if not isinstance(backend.get("circuit_open"), bool):
            raise StateError(
                f"transport_backends.{name}.circuit_open must be boolean"
            )
        affected = backend.get("affected_images", [])
        if not isinstance(affected, list) or not all(
            isinstance(number, int) and not isinstance(number, bool) and number > 0
            for number in affected
        ):
            raise StateError(
                f"transport_backends.{name}.affected_images must contain image numbers"
            )
        failure_window = backend.get("failure_window", [])
        if not isinstance(failure_window, list) or not all(
            isinstance(event, dict) for event in failure_window
        ):
            raise StateError(
                f"transport_backends.{name}.failure_window must be an array"
            )

    batch = payload.get("transport_batch")
    if batch is not None:
        if not isinstance(batch, dict):
            raise StateError("transport_batch must be an object")
        if batch.get("status") not in {"active", "stopped"}:
            raise StateError("transport_batch.status must be active or stopped")
        for field in ("success_count", "attempt_count"):
            require_nonnegative_int(batch.get(field), f"transport_batch.{field}")

    authorizations = payload.get("fallback_authorizations", {})
    if not isinstance(authorizations, dict):
        raise StateError("fallback_authorizations must be an object")
    for name, authorization in authorizations.items():
        if not isinstance(name, str) or not name or not isinstance(authorization, dict):
            raise StateError("fallback_authorizations entries must be named objects")
        if authorization.get("authorized") is not True:
            raise StateError(
                f"fallback_authorizations.{name}.authorized must be true"
            )
        if not isinstance(authorization.get("model"), str) or not authorization["model"]:
            raise StateError(f"fallback_authorizations.{name}.model is required")

    board_policy = payload.get("reference_board_policy")
    if board_policy is not None:
        if not isinstance(board_policy, dict):
            raise StateError("reference_board_policy must be an object")
        if board_policy.get("authorized") is not True:
            raise StateError("reference_board_policy.authorized must be true")
        if board_policy.get("timeout_seconds") != REFERENCE_BOARD_TIMEOUT_SECONDS:
            raise StateError(
                "reference_board_policy.timeout_seconds must equal 480"
            )
        if board_policy.get("source_count") != 2:
            raise StateError("reference_board_policy.source_count must equal 2")
        if board_policy.get("original_generation_only") is not True:
            raise StateError(
                "reference_board_policy.original_generation_only must be true"
            )
        for field in ("authorized_at", "snapshot_file"):
            value = board_policy.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(f"reference_board_policy.{field} is required")

    board_fallbacks = payload.get("reference_board_fallbacks", [])
    if not isinstance(board_fallbacks, list) or not all(
        isinstance(record, dict) for record in board_fallbacks
    ):
        raise StateError("reference_board_fallbacks must be an array")
    for index, record in enumerate(board_fallbacks):
        number = record.get("image_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(
                f"reference_board_fallbacks[{index}].image_number must be positive"
            )
        for field in ("staged_at", "archived_request", "request_file", "board"):
            value = record.get(field)
            if not isinstance(value, str) or not value:
                raise StateError(
                    f"reference_board_fallbacks[{index}].{field} is required"
                )

    repair_policy = payload.get("repair_policy")
    if repair_policy is not None:
        if not isinstance(repair_policy, dict):
            raise StateError("repair_policy must be an object")
        if repair_policy.get("mode") != "deferred_user_approved":
            raise StateError(
                "repair_policy.mode must equal deferred_user_approved"
            )
        approved_numbers = repair_policy.get("approved_numbers", [])
        if not isinstance(approved_numbers, list) or not all(
            isinstance(number, int)
            and not isinstance(number, bool)
            and number > 0
            for number in approved_numbers
        ):
            raise StateError("repair_policy.approved_numbers must contain image numbers")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        prefix = f"images[{index}]"
        if not isinstance(item, dict):
            raise StateError(f"{prefix} must be an object")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise StateError(f"{prefix}.number must be a positive integer")
        status = item.get("status")
        if status not in V2_STATUSES:
            raise StateError(f"{prefix}.status must be one of {sorted(V2_STATUSES)}")
        repair_count, _, _ = validate_content_fields(item, prefix, status)
        recommendation = item.get("repair_recommendation")
        if recommendation is not None:
            if not isinstance(recommendation, dict):
                raise StateError(f"{prefix}.repair_recommendation must be an object")
            if recommendation.get("mode") not in {"edit", "regenerate"}:
                raise StateError(
                    f"{prefix}.repair_recommendation.mode must be edit or regenerate"
                )
            require_string_list(
                recommendation.get("issues", []),
                f"{prefix}.repair_recommendation.issues",
            )
        final_source = validate_candidate_files(
            project_dir,
            item,
            prefix,
            status,
            repair_count,
            candidate_required=status in {"review_pending", "pass", "needs_user"},
        )
        validate_transport(item.get("transport"), prefix)
        transport = item["transport"]
        if status == "transport_blocked" and not transport["circuit_open"]:
            raise StateError(
                f"{prefix}.transport_blocked requires transport.circuit_open"
            )
        if (
            status == "generating"
            and transport["circuit_open"]
            and not transport["probe_in_flight"]
        ):
            raise StateError(
                f"{prefix}.generating may keep a circuit open only for a probe"
            )
        if status == "generating" and transport.get("active_attempt") is None:
            # Legacy schema-v2 attempts are accepted so recover-interrupted can
            # safely close a turn that was aborted before attempt metadata existed.
            pass
        if status != "generating" and transport.get("active_attempt") is not None:
            raise StateError(
                f"{prefix}.transport.active_attempt requires generating status"
            )
        normalized.append(
            {"number": number, "status": status, "final_source": final_source}
        )

    reference_jobs = payload.get("reference_jobs", [])
    if not isinstance(reference_jobs, list):
        raise StateError("reference_jobs must be an array")
    seen_reference_ids: set[str] = set()
    for index, job in enumerate(reference_jobs):
        prefix = f"reference_jobs[{index}]"
        if not isinstance(job, dict):
            raise StateError(f"{prefix} must be an object")
        reference_id = job.get("id")
        if (
            not isinstance(reference_id, str)
            or not reference_id
            or not all(character.isalnum() or character in "-_" for character in reference_id)
        ):
            raise StateError(f"{prefix}.id must be a non-empty slug")
        if reference_id in seen_reference_ids:
            raise StateError(f"duplicate reference job id: {reference_id}")
        seen_reference_ids.add(reference_id)
        if job.get("kind") not in {"character", "location", "prop", "vehicle", "wonder"}:
            raise StateError(f"{prefix}.kind is invalid")
        status = job.get("status")
        if status not in V2_STATUSES:
            raise StateError(f"{prefix}.status must be one of {sorted(V2_STATUSES)}")
        candidate = job.get("candidate")
        if status in {"review_pending", "pass", "needs_user"} and not candidate:
            raise StateError(f"{prefix}.{status} requires a candidate")
        if candidate:
            resolve_project_path(project_dir, candidate, f"{prefix}.candidate")
            if not (project_dir / candidate).is_file():
                raise StateError(f"{prefix}.candidate does not exist: {candidate}")
        validate_transport(job.get("transport"), prefix)

    validate_numbering(normalized, planned_count)
    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 2)


def validate_v3(state_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = validate_v2(state_path, payload)
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    for name, backend in payload.get("transport_backends", {}).items():
        for index, event in enumerate(backend.get("failure_window", [])):
            prefix = f"transport_backends.{name}.failure_window[{index}]"
            if event.get("job_type") not in {"scene", "reference"}:
                raise StateError(f"{prefix}.job_type must be scene or reference")
            job_id = event.get("job_id")
            if not isinstance(job_id, (str, int)) or isinstance(job_id, bool):
                raise StateError(f"{prefix}.job_id must be a string or integer")
            for field in ("error_type", "error_key", "failed_at"):
                if not isinstance(event.get(field), str) or not event[field]:
                    raise StateError(f"{prefix}.{field} is required")
    for index, item in enumerate(payload["images"]):
        validate_v3_transport(item["transport"], f"images[{index}]")
    for index, job in enumerate(payload.get("reference_jobs", [])):
        prefix = f"reference_jobs[{index}]"
        validate_v3_transport(job["transport"], prefix)
        output_dir = resolve_project_path(project_dir, job.get("output_dir"), f"{prefix}.output_dir")
        try:
            output_dir.relative_to(project_dir)
        except ValueError as exc:
            raise StateError(f"{prefix}.output_dir must stay inside project_dir") from exc
        if not isinstance(job.get("candidate_versions"), list):
            raise StateError(f"{prefix}.candidate_versions must be an array")
        repair_count = require_nonnegative_int(
            job.get("content_repair_count"), f"{prefix}.content_repair_count"
        )
        if repair_count not in (0, 1):
            raise StateError(f"{prefix}.content_repair_count must be 0 or 1")
        require_string_list(job.get("review_issues", []), f"{prefix}.review_issues")
        approved = job.get("approved_candidate")
        if job.get("status") == "pass":
            if not approved or approved != job.get("candidate"):
                raise StateError(f"{prefix}.pass requires approved_candidate to equal candidate")
        elif approved is not None:
            raise StateError(f"{prefix}.approved_candidate requires pass status")
    result["schema_version"] = 3
    return result


def validate_numbering(normalized: list[dict[str, Any]], planned_count: int) -> None:
    numbers = sorted(item["number"] for item in normalized)
    expected = list(range(1, planned_count + 1))
    if numbers != expected:
        raise StateError(
            f"image numbers must match planned_count and be contiguous; got {numbers}"
        )


def validate_final_requirements(
    phase: str,
    normalized: list[dict[str, Any]],
    artifacts: dict[str, Any],
    project_dir: Path,
    blocking_reasons: list[str],
) -> None:
    if phase in FINAL_PHASES:
        non_passing = [
            item["number"] for item in normalized if item["status"] != "pass"
        ]
        if non_passing:
            raise StateError(
                f"{phase} requires every image to pass; failing numbers: {non_passing}"
            )
    if phase == "complete":
        for key in REQUIRED_COMPLETE_ARTIFACTS:
            raw_path = artifacts.get(key)
            path = resolve_project_path(project_dir, raw_path, f"artifacts.{key}")
            if not path.is_file():
                raise StateError(f"complete state requires artifacts.{key}: {path}")
        if blocking_reasons:
            raise StateError("complete state cannot contain blocking_reasons")
    if phase == "needs_user" and not blocking_reasons:
        raise StateError("needs_user requires at least one blocking reason")


def summary(
    state_path: Path,
    project_dir: Path,
    phase: str,
    normalized: list[dict[str, Any]],
    blocking_reasons: list[str],
    schema_version: int,
) -> dict[str, Any]:
    return {
        "state_file": str(state_path.resolve()),
        "project_dir": str(project_dir),
        "schema_version": schema_version,
        "phase": phase,
        "image_count": len(normalized),
        "passing_count": sum(item["status"] == "pass" for item in normalized),
        "transport_blocked_count": sum(
            item["status"] == "transport_blocked" for item in normalized
        ),
        "blocking_count": len(blocking_reasons),
    }


def validate_state(state_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    if not isinstance(payload, dict):
        raise StateError("review state must contain a JSON object")
    version = payload.get("schema_version")
    if version == 1:
        return validate_v1(state_path, payload)
    if version == 2:
        return validate_v2(state_path, payload)
    if version == 3:
        return validate_v3(state_path, payload)
    raise StateError("schema_version must equal 1, 2 or 3")


def empty_transport() -> dict[str, Any]:
    return {
        "backend": "built_in_imagegen",
        "route": None,
        "attempts_total": 0,
        "consecutive_failures": 0,
        "last_error": None,
        "last_error_type": None,
        "error_fingerprint": None,
        "backend_error_key": None,
        "prompt_sha256": None,
        "reference_sha256": [],
        "circuit_open": False,
        "probe_granted": False,
        "probe_in_flight": False,
        "active_attempt": None,
        "attempt_history": [],
        "next_eligible_at": None,
        "reference_summary": {"count": 0, "total_bytes": 0, "images": []},
        "auto_recovery_level": 0,
        "auto_recovery_history": [],
        "backend_health_warning": False,
        "recovery": {
            "level": 0,
            "state": "idle",
            "transaction": None,
            "last_error": None,
        },
    }


def require_writable_state(payload: dict[str, Any], command: str) -> int:
    version = payload.get("schema_version")
    if version not in {2, 3}:
        raise StateError(f"{command} requires schema_version 2 or 3")
    if version == 2 and payload.get("phase") == "complete":
        raise StateError("completed schema v2 projects are read-only")
    return int(version)


def register_reference_job(
    state_path: Path, reference_id: str, kind: str, output_dir: str
) -> dict[str, Any]:
    payload = load_json(state_path)
    version = require_writable_state(payload, "register-reference-job")
    if payload.get("phase") != "reference_self_review":
        raise StateError("reference jobs may only be registered in reference_self_review")
    if not reference_id or not all(
        character.isalnum() or character in "-_" for character in reference_id
    ):
        raise StateError("reference-id must be a non-empty slug")
    jobs = payload.setdefault("reference_jobs", [])
    if any(job.get("id") == reference_id for job in jobs):
        raise StateError(f"reference job already exists: {reference_id}")
    if kind not in {"character", "location", "prop", "vehicle", "wonder"}:
        raise StateError("unsupported reference job kind")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    target_dir = resolve_project_path(project_dir, output_dir, "output_dir")
    target_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": reference_id,
        "kind": kind,
        "output_dir": str(target_dir.relative_to(project_dir)),
        "status": "pending",
        "candidate": None,
        "notes": "",
        "transport": empty_transport(),
    }
    if version == 3:
        job.update(
            {
                "candidate_versions": [],
                "content_repair_count": 0,
                "review_issues": [],
                "approved_candidate": None,
            }
        )
    jobs.append(job)
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "register-reference-job", **job}


def mark_reference_pass(
    state_path: Path, reference_id: str, notes: str
) -> dict[str, Any]:
    payload = load_json(state_path)
    version = require_writable_state(payload, "mark-reference-pass")
    if version == 3:
        result = record_reference_review(
            state_path, reference_id, "pass", [], notes
        )
        result["command"] = "mark-reference-pass"
        return result
    job = next(
        (
            candidate
            for candidate in payload.get("reference_jobs", [])
            if candidate.get("id") == reference_id
        ),
        None,
    )
    if job is None:
        raise StateError(f"reference job is not registered: {reference_id}")
    if job.get("status") != "review_pending" or not job.get("candidate"):
        raise StateError("reference job must have a review_pending candidate")
    job["status"] = "pass"
    job["notes"] = notes
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "mark-reference-pass",
        "reference_id": reference_id,
        "status": "pass",
        "candidate": job["candidate"],
    }


def record_reference_review(
    state_path: Path,
    reference_id: str,
    verdict: str,
    issues: list[str],
    notes: str,
) -> dict[str, Any]:
    payload = load_json(state_path)
    if payload.get("schema_version") != 3:
        raise StateError("record-reference-review requires schema_version 3")
    if payload.get("phase") != "reference_self_review":
        raise StateError("reference review requires phase reference_self_review")
    job = next(
        (item for item in payload.get("reference_jobs", []) if item.get("id") == reference_id),
        None,
    )
    if job is None:
        raise StateError(f"reference job is not registered: {reference_id}")
    if job.get("status") != "review_pending" or not job.get("candidate"):
        raise StateError("reference job must have a review_pending candidate")
    candidate = str(job["candidate"])
    versions = job.setdefault("candidate_versions", [])
    version_number = int(job.get("content_repair_count", 0)) + 1
    existing = next(
        (entry for entry in versions if entry.get("candidate") == candidate), None
    )
    version_entry = existing or {"version": version_number, "candidate": candidate}
    version_entry.update(
        {
            "review": verdict,
            "issues": list(issues),
            "notes": notes,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if existing is None:
        versions.append(version_entry)
    if verdict == "pass":
        if issues:
            raise StateError("passing reference review cannot include issues")
        job.update(
            {
                "status": "pass",
                "approved_candidate": candidate,
                "review_issues": [],
                "notes": notes,
            }
        )
    elif verdict == "fail":
        if not issues:
            raise StateError("failed reference review requires at least one issue")
        job["review_issues"] = list(issues)
        job["approved_candidate"] = None
        job["notes"] = notes
        if int(job.get("content_repair_count", 0)) >= 1:
            job["status"] = "needs_user"
        else:
            project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
            request_path = project_dir / "生成请求" / f"reference-{reference_id}.json"
            current = load_json(request_path)
            origin = current.get("content_repair_origin") or current
            origin_recovery = origin.get("auto_recovery_origin", {})
            references = origin_recovery.get("references") or origin.get("references", [])
            roles = origin_recovery.get("reference_roles") or origin.get("reference_roles", [])
            correction = "\n\nReference content correction (version 2):\n" + "\n".join(
                f"- {issue}" for issue in issues
            )
            prompt = str(origin.get("prompt", "")) + correction
            revised = copy.deepcopy(origin)
            revised.update(
                {
                    "prompt": prompt,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "references": copy.deepcopy(references),
                    "reference_roles": list(roles),
                    "content_repair_origin": copy.deepcopy(origin),
                    "content_repair": {
                        "version": 2,
                        "issues": list(issues),
                        "staged_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            revised.pop("auto_recovery", None)
            revised.pop("auto_recovery_origin", None)
            archive_index = 1
            while True:
                archive = request_path.with_name(
                    f"{request_path.stem}-content-v{archive_index}{request_path.suffix}"
                )
                if not archive.exists():
                    break
                archive_index += 1
            atomic_write_json(archive, current)
            atomic_write_json(request_path, revised)
            job.update(
                {
                    "status": "pending",
                    "candidate": None,
                    "content_repair_count": 1,
                }
            )
            transport = job["transport"]
            transport.update(
                {
                    "prompt_sha256": revised["prompt_sha256"],
                    "reference_sha256": [
                        {"path": entry.get("path"), "sha256": entry.get("sha256")}
                        for entry in references
                    ],
                    "auto_recovery_level": 0,
                    "auto_recovery_history": [],
                    "consecutive_failures": 0,
                    "last_error": None,
                    "last_error_type": None,
                    "circuit_open": False,
                    "next_eligible_at": None,
                    "recovery": {
                        "level": 0,
                        "state": "idle",
                        "transaction": None,
                        "last_error": None,
                    },
                }
            )
    else:
        raise StateError("reference review verdict must be pass or fail")
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-reference-review",
        "reference_id": reference_id,
        "verdict": verdict,
        "status": job["status"],
        "content_repair_count": job.get("content_repair_count", 0),
    }


def migrate_v1(payload: dict[str, Any], planned_count: int) -> dict[str, Any]:
    existing = payload.get("images")
    if not isinstance(existing, list):
        raise StateError("images must be an array")
    if planned_count < len(existing):
        raise StateError("planned_count cannot be smaller than existing image count")
    migrated = copy.deepcopy(payload)
    migrated["schema_version"] = 2
    migrated["planned_count"] = planned_count
    migrated["transport_backends"] = {}
    migrated["fallback_authorizations"] = {}
    images: list[dict[str, Any]] = []
    status_map = {
        "pending": "pending",
        "pass": "pass",
        "fail": "review_pending",
        "needs_user": "needs_user",
    }
    by_number = {item.get("number"): item for item in existing if isinstance(item, dict)}
    for number in range(1, planned_count + 1):
        if number in by_number:
            item = copy.deepcopy(by_number[number])
            verdict = item.pop("verdict", "pending")
            item["status"] = status_map.get(verdict, "pending")
            item["transport"] = empty_transport()
            if item.get("candidate") is None and item["status"] == "review_pending":
                item["status"] = "pending"
            images.append(item)
        else:
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
                    "transport": empty_transport(),
                }
            )
    migrated["images"] = images
    return migrated


def _migrate_transport_v3(raw: Any) -> dict[str, Any]:
    transport = copy.deepcopy(raw) if isinstance(raw, dict) else empty_transport()
    defaults = empty_transport()
    for key, value in defaults.items():
        transport.setdefault(key, copy.deepcopy(value))
    level = int(transport.get("auto_recovery_level", 0))
    recovery = transport.setdefault("recovery", {})
    recovery.setdefault("level", level)
    recovery.setdefault("state", "ready" if level else "idle")
    recovery.setdefault("transaction", None)
    recovery.setdefault("last_error", None)
    return transport


def migrate_v2(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 2:
        raise StateError("schema v3 migration requires a schema v2 state")
    if payload.get("phase") == "complete":
        raise StateError("completed schema v2 projects remain read-only and are not migrated")
    migrated = copy.deepcopy(payload)
    migrated["schema_version"] = 3
    migrated.setdefault("reference_jobs", [])
    migrated.setdefault("transport_batch", None)
    migrated.setdefault("reference_board_policy", None)
    migrated.setdefault("reference_board_fallbacks", [])
    for item in migrated.get("images", []):
        item["transport"] = _migrate_transport_v3(item.get("transport"))
    for job in migrated.get("reference_jobs", []):
        job["transport"] = _migrate_transport_v3(job.get("transport"))
        versions = job.setdefault("candidate_versions", [])
        candidate = job.get("candidate")
        if candidate and not versions:
            versions.append({"version": 1, "candidate": candidate, "review": None})
        job.setdefault("content_repair_count", 0)
        job.setdefault("review_issues", [])
        job.setdefault(
            "approved_candidate", candidate if job.get("status") == "pass" else None
        )
    for backend in migrated.setdefault("transport_backends", {}).values():
        normalized = []
        for event in backend.get("failure_window", []):
            if "job_type" in event and "job_id" in event:
                normalized.append(event)
            elif event.get("job_key"):
                raw_key = str(event["job_key"])
                job_type, _, job_id = raw_key.partition(":")
                normalized.append(
                    {**event, "job_type": job_type or "reference", "job_id": job_id}
                )
            else:
                normalized.append(
                    {**event, "job_type": "scene", "job_id": event.get("image_number")}
                )
            normalized[-1].pop("job_key", None)
            normalized[-1].pop("image_number", None)
        backend["failure_window"] = normalized
        backend.setdefault("circuit_open", False)
        backend.setdefault("affected_images", [])
    return migrated


def mark_pass(state_path: Path, number: int, notes: str, red_flags: list[str]) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "mark-pass")
    item = next(
        (candidate for candidate in payload["images"] if candidate.get("number") == number),
        None,
    )
    if item is None:
        raise StateError(f"image {number} is not present in planned images")
    if item.get("status") != "review_pending":
        raise StateError(f"image {number} must be review_pending before mark-pass")
    if len(red_flags) >= 3:
        raise StateError("mark-pass accepts fewer than three photo red flags")
    final_source = (
        item.get("repair_file")
        if item.get("repair_count") == 1
        else item.get("candidate")
    )
    if not final_source:
        raise StateError(f"image {number} has no passing source")
    item.update(
        {
            "status": "pass",
            "hard_failures": [],
            "photo_red_flags": red_flags,
            "final_source": final_source,
            "notes": notes,
        }
    )
    atomic_write_json(state_path, payload)
    return validate_state(state_path)


def queue_repair(
    state_path: Path,
    number: int,
    mode: str,
    issues: list[str],
    notes: str,
    red_flags: list[str],
) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "queue-repair")
    if payload.get("repair_policy", {}).get("mode") != "deferred_user_approved":
        raise StateError("queue-repair requires deferred_user_approved repair_policy")
    item = next(
        (candidate for candidate in payload["images"] if candidate.get("number") == number),
        None,
    )
    if item is None or item.get("status") != "review_pending":
        raise StateError(f"image {number} must be review_pending before queue-repair")
    if item.get("repair_count") != 0 or not item.get("candidate"):
        raise StateError(f"image {number} must have an unmodified original candidate")
    if not issues:
        raise StateError("queue-repair requires at least one issue")
    item["hard_failures"] = list(issues)
    item["photo_red_flags"] = list(red_flags)
    item["notes"] = notes
    item["repair_recommendation"] = {
        "mode": mode,
        "issues": list(issues),
        "notes": notes,
    }
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "queue-repair",
        "image_number": number,
        "mode": mode,
        "issues": issues,
    }


def prepare_repair_report(state_path: Path, output: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    require_writable_state(payload, "prepare-repair-report")
    if payload.get("repair_policy", {}).get("mode") != "deferred_user_approved":
        raise StateError(
            "prepare-repair-report requires deferred_user_approved repair_policy"
        )
    if payload.get("phase") != "scene_self_review":
        raise StateError("repair report can only be prepared after scene self-review")
    missing_originals = [
        item["number"] for item in payload["images"] if not item.get("candidate")
    ]
    if missing_originals:
        raise StateError(
            f"all original candidates are required before repair reporting: {missing_originals}"
        )
    unreviewed = [
        item["number"]
        for item in payload["images"]
        if item.get("status") == "review_pending"
        and item.get("repair_recommendation") is None
    ]
    if unreviewed:
        raise StateError(f"images still need content review: {unreviewed}")
    queued = [
        item for item in payload["images"] if item.get("repair_recommendation")
    ]
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    report_path = output.expanduser()
    if not report_path.is_absolute():
        report_path = project_dir / report_path
    report_path = report_path.resolve()
    try:
        report_relative = str(report_path.relative_to(project_dir))
    except ValueError as exc:
        raise StateError("repair report must be written inside the project") from exc
    lines = [
        "# 返修报告",
        "",
        "全部正式原图已经生成并完成首轮内容审查。以下项目等待用户确认后统一返修。",
        "",
        "| 图号 | 建议方式 | 问题 | 审查说明 |",
        "|---|---|---|---|",
    ]
    for item in queued:
        rec = item["repair_recommendation"]
        issues = "；".join(rec["issues"]).replace("|", "｜")
        notes = rec.get("notes", "").replace("|", "｜")
        lines.append(f"| {item['number']:02d} | {rec['mode']} | {issues} | {notes} |")
    if not queued:
        lines.append("| — | — | 无需返修 | 全部原图通过 |")
    lines.extend(
        [
            "",
            "## 用户确认",
            "",
            "返修不会自动开始。用户可批准全部或指定图号；未获批准的图片保持原图和审查结论不变。",
            "",
        ]
    )
    atomic_write_text(report_path, "\n".join(lines))
    policy = payload["repair_policy"]
    policy.update(
        {
            "report_file": report_relative,
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            "approved_numbers": [],
            "approved_at": None,
        }
    )
    payload.setdefault("artifacts", {})["repair_report"] = report_relative
    payload["phase"] = (
        "awaiting_repair_approval" if queued else "final_self_review"
    )
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "prepare-repair-report",
        "report_file": str(report_path),
        "queued_numbers": [item["number"] for item in queued],
        "phase": payload["phase"],
    }


def authorize_repairs(
    state_path: Path, numbers: list[int], user_approved: bool
) -> dict[str, Any]:
    if not user_approved:
        raise StateError("authorize-repairs requires explicit --user-approved")
    payload = load_json(state_path)
    require_writable_state(payload, "authorize-repairs")
    if payload.get("phase") != "awaiting_repair_approval":
        raise StateError("repairs may only be authorized after the repair report")
    queued = {
        item["number"]
        for item in payload["images"]
        if item.get("repair_recommendation")
    }
    selected = set(numbers) if numbers else queued
    if not selected or not selected.issubset(queued):
        raise StateError("authorized repair numbers must be present in the report")
    policy = payload.get("repair_policy", {})
    policy["approved_numbers"] = sorted(selected)
    policy["approved_at"] = datetime.now(timezone.utc).isoformat()
    payload["phase"] = "repairing"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "authorize-repairs",
        "approved_numbers": sorted(selected),
        "phase": "repairing",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate a review-state file")
    validate.add_argument("--state", required=True, type=Path)
    transition = subparsers.add_parser(
        "transition", help="Move a valid state file to the next legal phase"
    )
    transition.add_argument("--state", required=True, type=Path)
    transition.add_argument("--to", required=True, choices=sorted(PHASES))
    transition.add_argument(
        "--user-approved",
        action="store_true",
        help="Confirm explicit user approval when crossing an approval gate.",
    )
    migrate = subparsers.add_parser(
        "migrate", help="Migrate one explicitly selected unfinished state"
    )
    migrate.add_argument("--state", required=True, type=Path)
    migrate.add_argument("--to-version", type=int, choices=(2, 3), required=True)
    migrate.add_argument("--planned-count", type=int)
    migrate.add_argument(
        "--dry-run", action="store_true", help="Validate migration without writing"
    )
    passed = subparsers.add_parser(
        "mark-pass", help="Mark one reviewed original or repaired image as passing"
    )
    passed.add_argument("--state", required=True, type=Path)
    passed.add_argument("--number", required=True, type=int)
    passed.add_argument("--notes", required=True)
    passed.add_argument("--red-flag", action="append", default=[])
    queued = subparsers.add_parser(
        "queue-repair", help="Record one original-image issue for deferred repair"
    )
    queued.add_argument("--state", required=True, type=Path)
    queued.add_argument("--number", required=True, type=int)
    queued.add_argument("--repair-mode", required=True, choices=("edit", "regenerate"))
    queued.add_argument("--issue", action="append", required=True)
    queued.add_argument("--notes", required=True)
    queued.add_argument("--red-flag", action="append", default=[])
    report = subparsers.add_parser(
        "prepare-repair-report",
        help="Write the repair report after every original candidate is reviewed",
    )
    report.add_argument("--state", required=True, type=Path)
    report.add_argument("--output", required=True, type=Path)
    approve = subparsers.add_parser(
        "authorize-repairs",
        help="Authorize report-listed repairs after explicit user approval",
    )
    approve.add_argument("--state", required=True, type=Path)
    approve.add_argument("--number", action="append", default=[], type=int)
    approve.add_argument("--user-approved", action="store_true")
    register = subparsers.add_parser(
        "register-reference-job",
        help="Register one reference asset for transport-safe generation",
    )
    register.add_argument("--state", required=True, type=Path)
    register.add_argument("--reference-id", required=True)
    register.add_argument(
        "--kind",
        required=True,
        choices=("character", "location", "prop", "vehicle", "wonder"),
    )
    register.add_argument("--output-dir", required=True)
    reference_pass = subparsers.add_parser(
        "mark-reference-pass",
        help="Mark one reviewed reference asset as approved",
    )
    reference_pass.add_argument("--state", required=True, type=Path)
    reference_pass.add_argument("--reference-id", required=True)
    reference_pass.add_argument("--notes", required=True)
    reference_review = subparsers.add_parser(
        "record-reference-review",
        help="Record pass/fail review and stage the one allowed reference correction",
    )
    reference_review.add_argument("--state", required=True, type=Path)
    reference_review.add_argument("--reference-id", required=True)
    reference_review.add_argument("--verdict", required=True, choices=("pass", "fail"))
    reference_review.add_argument("--issues-file", type=Path)
    reference_review.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state_path = args.state.expanduser().resolve()
        if args.command == "migrate":
            source = load_json(state_path)
            if args.to_version == 2:
                if args.planned_count is None:
                    raise StateError("v1 to v2 migration requires --planned-count")
                validate_v1(state_path, source)
                payload = migrate_v1(source, args.planned_count)
                validator = validate_v2
            else:
                validate_v2(state_path, source)
                payload = migrate_v2(source)
                validator = validate_v3
            if args.dry_run:
                temporary = state_path.parent / f".{state_path.name}.migration-dry-run"
                atomic_write_json(temporary, payload)
                try:
                    result = validator(temporary, payload)
                finally:
                    temporary.unlink(missing_ok=True)
                result["dry_run"] = True
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = state_path.with_name(
                    f"{state_path.stem}.v{source.get('schema_version')}.{stamp}{state_path.suffix}"
                )
                shutil.copy2(state_path, backup)
                atomic_write_json(state_path, payload)
                result = validate_state(state_path)
                result["backup"] = str(backup)
        elif args.command == "mark-pass":
            result = mark_pass(
                state_path, args.number, args.notes, args.red_flag
            )
        elif args.command == "queue-repair":
            result = queue_repair(
                state_path,
                args.number,
                args.repair_mode,
                args.issue,
                args.notes,
                args.red_flag,
            )
        elif args.command == "prepare-repair-report":
            result = prepare_repair_report(state_path, args.output)
        elif args.command == "authorize-repairs":
            result = authorize_repairs(
                state_path, args.number, args.user_approved
            )
        elif args.command == "register-reference-job":
            result = register_reference_job(
                state_path, args.reference_id, args.kind, args.output_dir
            )
        elif args.command == "mark-reference-pass":
            result = mark_reference_pass(
                state_path, args.reference_id, args.notes
            )
        elif args.command == "record-reference-review":
            issues: list[str] = []
            if args.issues_file:
                raw = load_json(args.issues_file.expanduser().resolve())
                issues = raw.get("issues", []) if isinstance(raw, dict) else raw
                require_string_list(issues, "issues-file")
            result = record_reference_review(
                state_path, args.reference_id, args.verdict, issues, args.notes
            )
        else:
            result = validate_state(state_path)
            if args.command == "transition":
                payload = load_json(state_path)
                require_writable_state(payload, "transition")
                validate_transition(
                    payload["phase"], args.to, user_approved=args.user_approved
                )
                if args.to == "awaiting_reference_approval":
                    unfinished = [
                        job.get("id")
                        for job in payload.get("reference_jobs", [])
                        if job.get("status") != "pass"
                    ]
                    if unfinished:
                        raise StateError(
                            "all registered reference jobs must pass before reference approval: "
                            + ", ".join(str(value) for value in unfinished)
                        )
                payload["phase"] = args.to
                atomic_write_json(state_path, payload)
                result = validate_state(state_path)
    except StateError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
