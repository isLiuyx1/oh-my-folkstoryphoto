#!/usr/bin/env python3
"""Validate, transition, and migrate folk-story photo review state."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


PHASES = {
    "drafting",
    "text_self_review",
    "awaiting_plan_approval",
    "reference_self_review",
    "awaiting_reference_approval",
    "scene_self_review",
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
FINAL_PHASES = {"final_self_review", "complete"}
REQUIRED_COMPLETE_ARTIFACTS = ("self_review", "acceptance", "release_manifest")
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
    "scene_self_review": {"repairing", "final_self_review", "needs_user"},
    "repairing": {"scene_self_review", "final_self_review", "needs_user"},
    "final_self_review": {"repairing", "complete", "needs_user"},
    "complete": set(),
    "needs_user": {
        "drafting",
        "text_self_review",
        "reference_self_review",
        "scene_self_review",
        "repairing",
        "final_self_review",
    },
}
APPROVAL_TRANSITIONS = {
    ("awaiting_plan_approval", "reference_self_review"),
    ("awaiting_reference_approval", "scene_self_review"),
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
        if status == "generating" and transport["circuit_open"]:
            raise StateError(
                f"{prefix}.generating cannot have transport.circuit_open"
            )
        normalized.append(
            {"number": number, "status": status, "final_source": final_source}
        )

    validate_numbering(normalized, planned_count)
    validate_final_requirements(
        phase, normalized, artifacts, project_dir, blocking_reasons
    )
    validate_manifest(project_dir, artifacts, normalized, phase)
    return summary(state_path, project_dir, phase, normalized, blocking_reasons, 2)


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
    raise StateError("schema_version must equal 1 or 2")


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
        "migrate", help="Migrate one explicitly selected unfinished v1 state to v2"
    )
    migrate.add_argument("--state", required=True, type=Path)
    migrate.add_argument("--to-version", type=int, choices=(2,), required=True)
    migrate.add_argument("--planned-count", type=int, required=True)
    migrate.add_argument(
        "--dry-run", action="store_true", help="Validate migration without writing"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state_path = args.state.expanduser().resolve()
        if args.command == "migrate":
            validate_v1(state_path, load_json(state_path))
            payload = migrate_v1(load_json(state_path), args.planned_count)
            if args.dry_run:
                temporary = state_path.parent / f".{state_path.name}.migration-dry-run"
                atomic_write_json(temporary, payload)
                try:
                    result = validate_v2(temporary, payload)
                finally:
                    temporary.unlink(missing_ok=True)
                result["dry_run"] = True
            else:
                atomic_write_json(state_path, payload)
                result = validate_state(state_path)
        else:
            result = validate_state(state_path)
            if args.command == "transition":
                payload = load_json(state_path)
                validate_transition(
                    payload["phase"], args.to, user_approved=args.user_approved
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
