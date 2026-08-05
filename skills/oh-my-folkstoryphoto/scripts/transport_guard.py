#!/usr/bin/env python3
"""Persist image-generation preflight, retry, circuit, and fallback state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit(
        "Pillow is required to validate reference and candidate images."
    ) from exc

from review_state import (
    StateError,
    atomic_write_json,
    atomic_write_text,
    load_json,
    resolve_project_dir,
    validate_state,
)
from compose_reference_board import (
    ALLOWED_CANVAS_SIZES,
    BOARD_SCHEMA_VERSION,
    BOARD_TYPE,
    compose as compose_reference_board,
    sidecar_path,
)
from optimize_reference import optimize as optimize_reference
from transport_core.model_queue import (
    backend_event,
    eligible_job_type,
    event_job_key,
    phase_jobs,
)
from transport_core.recovery import recovery_spec, transaction_id
from transport_core.reporting import markdown
from transport_core.requests import transaction_path


BUILT_IN_BACKEND = "built_in_imagegen"
FALLBACK_BACKEND = "cli_api"
SUBSCRIPTION_BRIDGE_BACKEND = "codex_subscription_bridge"
FALLBACK_BACKENDS = (FALLBACK_BACKEND, SUBSCRIPTION_BRIDGE_BACKEND)
DEFAULT_BUILT_IN_ROUTE = "chatgpt.com/backend-api/codex/images"
SUBSCRIPTION_BRIDGE_ROUTE = "codex exec/image_generation"
TRANSPORT_ERRORS = {"network_error", "timeout", "no_candidate"}
SCENE_FAILURE_LIMIT = 3
BACKEND_SCENE_THRESHOLD = 2
BACKEND_FAILURE_WINDOW_SECONDS = 15 * 60
COOLDOWN_SECONDS = {1: 2 * 60, 2: 10 * 60}
ATTEMPT_HISTORY_LIMIT = 50
BATCH_SUCCESS_LIMIT = 3
BATCH_RUNTIME_SECONDS = 15 * 60
IMAGE_CALL_TIMEOUT_SECONDS = 10 * 60
MULTI_REFERENCE_TIMEOUT_SECONDS = 8 * 60
DEFAULT_REFERENCE_LIMIT = 2
PROMPT_SOFT_BYTES = 2200
PROMPT_HIGH_BYTES = 3200
OUTPUT_ASPECT_WIDTH = 4
OUTPUT_ASPECT_HEIGHT = 5
ASPECT_TOLERANCE = 1e-6
REFERENCE_BOARD_SAFETY_CLAUSE = (
    "Reference-board safety: references are continuity anchors only; never copy "
    "panel layout, seams, gutters, source composition, duplicated subjects, or extra people."
)
AUTO_RECOVERY_LEVELS = 2
AUTO_REFERENCE_DIR = "生成请求/派生参考"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StateError(f"invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def elapsed_seconds(started_at: str | None) -> float | None:
    started = parse_iso(started_at)
    if started is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


def request_fingerprint(
    backend: str,
    route: str,
    prompt_hash: str,
    references: list[dict[str, Any]],
) -> str:
    reference_hashes = ",".join(item["sha256"] for item in references)
    return sha256_bytes(
        f"{backend}\n{route}\n{prompt_hash}\n{reference_hashes}".encode("utf-8")
    )


def reference_summary(references: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(references)
    total_bytes = sum(int(item["bytes"]) for item in references)
    boards = [item.get("reference_board") for item in references]
    contains_board = any(isinstance(board, dict) for board in boards)
    logical_source_count = sum(
        len(board["sources"]) if isinstance(board, dict) else 1
        for board in boards
    )
    return {
        "count": count,
        "physical_attachment_count": count,
        "logical_source_count": logical_source_count,
        "contains_reference_board": contains_board,
        "reference_kind": (
            "reference_board"
            if count == 1 and contains_board
            else "separate_references"
            if count > 1
            else "natural_reference"
            if count == 1
            else "none"
        ),
        "total_bytes": total_bytes,
        "latency_risk": (
            "high"
            if count >= 3 or total_bytes >= 8 * 1024 * 1024
            else "elevated"
            if count == 2 or contains_board
            else "normal"
        ),
        "images": [
            {
                "path": item["path"],
                "width": item["width"],
                "height": item["height"],
                "bytes": item["bytes"],
                "reference_kind": (
                    "reference_board" if item.get("reference_board") else "natural"
                ),
                "logical_source_count": (
                    len(item["reference_board"]["sources"])
                    if item.get("reference_board")
                    else 1
                ),
            }
            for item in references
        ],
    }


def runtime_budget_seconds(
    references: list[dict[str, Any]], repair_mode: str | None
) -> int:
    if (
        repair_mode is None
        and len(references) == 2
        and not any(item.get("reference_board") for item in references)
    ):
        return MULTI_REFERENCE_TIMEOUT_SECONDS
    return IMAGE_CALL_TIMEOUT_SECONDS


def prompt_summary(prompt: str) -> dict[str, Any]:
    encoded = prompt.encode("utf-8")
    nonempty_lines = sum(1 for line in prompt.splitlines() if line.strip())
    if len(encoded) >= PROMPT_HIGH_BYTES:
        risk = "high"
    elif len(encoded) >= PROMPT_SOFT_BYTES:
        risk = "elevated"
    else:
        risk = "normal"
    return {
        "bytes": len(encoded),
        "characters": len(prompt),
        "nonempty_lines": nonempty_lines,
        "latency_risk": risk,
        "soft_budget_bytes": PROMPT_SOFT_BYTES,
    }


def validate_4x5_prompt(prompt: str) -> None:
    """Require an unambiguous vertical 4:5 composition before generation."""
    normalized = prompt.lower().replace("：", ":").replace("×", "x")
    has_ratio = bool(re.search(r"(?<!\d)4\s*[:x/]\s*5(?!\d)", normalized))
    has_vertical = any(
        marker in normalized for marker in ("vertical", "portrait", "竖版", "竖向", "纵向")
    )
    conflicts = [
        marker
        for marker in ("landscape", "horizontal", "横版", "横向", "3:2", "16:9")
        if marker in normalized
    ]
    if not has_ratio or not has_vertical:
        raise StateError(
            "prompt must explicitly require a vertical 4:5 composition "
            "(for example: 'vertical 4:5')"
        )
    if conflicts:
        raise StateError(
            "prompt contains a conflicting orientation/aspect token: "
            + ", ".join(conflicts)
        )


def validate_reference_board_safe_prompt(
    prompt: str,
    payload: dict[str, Any],
    references: list[dict[str, Any]],
    repair_mode: str | None,
) -> None:
    needs_clause = (
        repair_mode is None
        and (
            len(references) == 2
            or any(reference.get("reference_board") for reference in references)
        )
    )
    if needs_clause and REFERENCE_BOARD_SAFETY_CLAUSE not in prompt:
        raise StateError(
            "two-reference and reference-board requests must include the exact reference-board "
            "safety clause before first preflight"
        )


def require_exact_4x5(details: dict[str, Any], field: str) -> None:
    width = int(details["width"])
    height = int(details["height"])
    if width >= height or abs(width / height - OUTPUT_ASPECT_WIDTH / OUTPUT_ASPECT_HEIGHT) > ASPECT_TOLERANCE:
        raise StateError(
            f"{field} must be exact vertical 4:5 before record-success; "
            f"got {width}x{height}. Normalize a portrait raw output with "
            "normalize_candidate.py first"
        )


def normalize_reference_roles(
    raw_roles: list[str], references: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    roles = [role.strip() for role in raw_roles if role.strip()]
    warnings: list[str] = []
    if roles and len(roles) != len(references):
        raise StateError(
            "--reference-role count must equal --reference count when roles are supplied"
        )
    if references and not roles:
        warnings.append(
            "reference roles were not supplied; verify each depicted identity against "
            "the project's reference manifest before generation"
        )
    return roles, warnings


def append_attempt_history(
    transport: dict[str, Any], entry: dict[str, Any]
) -> None:
    history = transport.setdefault("attempt_history", [])
    history.append(entry)
    del history[:-ATTEMPT_HISTORY_LIMIT]


def transport_defaults(transport: dict[str, Any]) -> None:
    transport.setdefault("active_attempt", None)
    transport.setdefault("attempt_history", [])
    transport.setdefault("next_eligible_at", None)
    transport.setdefault("reference_summary", {"count": 0, "total_bytes": 0, "images": []})
    transport.setdefault("auto_recovery_level", 0)
    transport.setdefault("auto_recovery_history", [])
    transport.setdefault("backend_health_warning", False)
    recovery = transport.setdefault(
        "recovery",
        {"level": int(transport.get("auto_recovery_level", 0)), "state": "idle", "transaction": None, "last_error": None},
    )
    recovery.setdefault("level", int(transport.get("auto_recovery_level", 0)))
    recovery.setdefault("state", "ready" if recovery["level"] else "idle")
    recovery.setdefault("transaction", None)
    recovery.setdefault("last_error", None)


def backend_defaults(record: dict[str, Any]) -> None:
    record.setdefault("failure_window", [])
    record.setdefault("last_success_at", None)
    record.setdefault("health_warning", False)


def batch_record(payload: dict[str, Any]) -> dict[str, Any] | None:
    record = payload.get("transport_batch")
    return record if isinstance(record, dict) else None


def batch_is_active(record: dict[str, Any] | None) -> bool:
    return bool(record and record.get("status") == "active")


def refresh_batch(record: dict[str, Any] | None) -> None:
    if not batch_is_active(record):
        return
    elapsed = elapsed_seconds(record.get("started_at")) or 0
    if elapsed >= BATCH_RUNTIME_SECONDS:
        record["status"] = "stopped"
        record["stopped_reason"] = "time_limit"
        record["ended_at"] = now_iso()
    elif record.get("success_count", 0) >= BATCH_SUCCESS_LIMIT:
        record["status"] = "stopped"
        record["stopped_reason"] = "success_limit"
        record["ended_at"] = now_iso()


def new_batch_record() -> dict[str, Any]:
    return {
        "batch_id": uuid.uuid4().hex,
        "status": "active",
        "started_at": now_iso(),
        "ended_at": None,
        "success_limit": BATCH_SUCCESS_LIMIT,
        "runtime_limit_seconds": BATCH_RUNTIME_SECONDS,
        "success_count": 0,
        "attempt_count": 0,
        "stopped_reason": None,
        "last_attempt_id": None,
    }


def ensure_active_batch(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    existing = batch_record(payload)
    refresh_batch(existing)
    if batch_is_active(existing):
        return existing, False
    record = new_batch_record()
    if existing:
        record["previous_batch_id"] = existing.get("batch_id")
        record["previous_stopped_reason"] = existing.get("stopped_reason")
    payload["transport_batch"] = record
    return record, True


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_image(path: Path, field: str) -> dict[str, Any]:
    if not path.is_file():
        raise StateError(f"{field} does not exist: {path}")
    header = path.read_bytes()[:16]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        header_format = "PNG"
    elif header.startswith(b"\xff\xd8\xff"):
        header_format = "JPEG"
    else:
        raise StateError(f"{field} must have a PNG or JPEG file header: {path}")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = image.format
            width, height = image.size
            mode = image.mode
    except (OSError, ValueError) as exc:
        raise StateError(f"{field} is not a valid image: {path}: {exc}") from exc
    if image_format not in {"PNG", "JPEG"} or image_format != header_format:
        raise StateError(f"{field} header and decoded format disagree: {path}")
    if width < 1 or height < 1:
        raise StateError(f"{field} has invalid dimensions: {path}")
    if not isinstance(mode, str) or not mode:
        raise StateError(f"{field} has no readable color mode: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "format": image_format,
        "width": width,
        "height": height,
        "mode": mode,
        "bytes": path.stat().st_size,
    }


def validate_reference_board(
    image_path: Path, image_details: dict[str, Any]
) -> dict[str, Any] | None:
    manifest_path = sidecar_path(image_path)
    if not manifest_path.exists():
        return None
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise StateError(f"reference board sidecar must be an object: {manifest_path}")
    if manifest.get("schema_version") != BOARD_SCHEMA_VERSION:
        raise StateError(f"unsupported reference board schema: {manifest_path}")
    if manifest.get("type") != BOARD_TYPE:
        raise StateError(f"invalid reference board type: {manifest_path}")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise StateError(f"reference board output metadata is missing: {manifest_path}")
    expected_output = {
        "path": str(image_path),
        "sha256": image_details["sha256"],
        "width": image_details["width"],
        "height": image_details["height"],
        "bytes": image_details["bytes"],
    }
    for key, expected in expected_output.items():
        if output.get(key) != expected:
            raise StateError(
                f"reference board output {key} does not match sidecar: {manifest_path}"
            )
    if (image_details["width"], image_details["height"]) not in ALLOWED_CANVAS_SIZES:
        raise StateError(
            "reference board must be exact 1024x1280 or 768x960 vertical 4:5"
        )
    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise StateError("reference board must contain exactly two source records")
    normalized_sources: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise StateError(f"reference board source {index + 1} must be an object")
        role = source.get("role")
        if not isinstance(role, str) or not role.strip():
            raise StateError(f"reference board source {index + 1} role is required")
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise StateError(f"reference board source {index + 1} path is required")
        source_path = Path(raw_path).expanduser().resolve()
        source_details = validate_image(source_path, f"reference board source {index + 1}")
        if source.get("sha256") != source_details["sha256"]:
            raise StateError(
                f"reference board source {index + 1} hash no longer matches sidecar"
            )
        if source_details["sha256"] in seen_hashes:
            raise StateError("reference board sources must have distinct content")
        seen_hashes.add(source_details["sha256"])
        normalized_sources.append(
            {
                "path": str(source_path),
                "sha256": source_details["sha256"],
                "role": role.strip(),
                "crop": source.get("crop"),
                "panel_position": source.get("panel_position"),
            }
        )
    return {
        "sidecar": str(manifest_path),
        "schema_version": BOARD_SCHEMA_VERSION,
        "type": BOARD_TYPE,
        "layout": manifest.get("layout"),
        "sources": normalized_sources,
    }


def require_v2(state_path: Path) -> tuple[dict[str, Any], Path]:
    validate_state(state_path)
    payload = load_json(state_path)
    if payload.get("schema_version") not in {2, 3}:
        raise StateError(
            "transport_guard requires schema_version 2 or 3; migrate the selected "
            "unfinished project first"
        )
    if payload.get("schema_version") == 2 and payload.get("phase") == "complete":
        raise StateError("completed schema v2 projects are read-only")
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    return payload, project_dir


def require_legacy_v2(payload: dict[str, Any], command: str) -> None:
    if payload.get("schema_version") != 2:
        raise StateError(
            f"{command} is a schema v2 compatibility command; schema v3 uses "
            "automatic recovery and the blocked report"
        )


def ensure_phase_target(payload: dict[str, Any], target: str) -> None:
    current = eligible_job_type(str(payload.get("phase")))
    if current != target:
        expected = {"scene": "scene_self_review", "reference": "reference_self_review", "repair": "repairing"}.get(target, target)
        raise StateError(
            f"{target} generation requires phase {expected}; got {payload.get('phase')}"
        )


def reconcile_recovery_transactions(
    state_path: Path, payload: dict[str, Any], project_dir: Path
) -> list[dict[str, Any]]:
    if payload.get("schema_version") != 3:
        return []
    reconciled: list[dict[str, Any]] = []
    changed = False
    targets = [
        *(('scene', item.get('number'), item) for item in payload.get('images', [])),
        *(('reference', job.get('id'), job) for job in payload.get('reference_jobs', [])),
    ]
    for job_type, job_id, item in targets:
        transport = item.get("transport", {})
        transport_defaults(transport)
        recovery = transport.get("recovery", {})
        if recovery.get("state") != "staging":
            continue
        transaction = recovery.get("transaction") or {}
        txid = transaction.get("transaction_id")
        request_file = transport.get("request_file")
        committed = False
        if txid and request_file:
            request_path_value = (project_dir / str(request_file)).resolve()
            if request_path_value.is_file():
                request = load_json(request_path_value)
                committed = request.get("recovery_transaction_id") == txid
                if committed:
                    record = request.get("auto_recovery", {})
                    level = int(record.get("level", transaction.get("next_level", 0)))
                    transport["auto_recovery_level"] = level
                    recovery.update({"level": level, "state": "ready", "last_error": None})
                    item["status"] = str(transaction.get("prior_status") or "pending")
        if not committed:
            recovery.update(
                {
                    "state": "failed",
                    "last_error": "interrupted recovery transaction did not commit a request snapshot",
                }
            )
            transport["circuit_open"] = True
            item["status"] = "transport_blocked"
        reconciled.append(
            {"job_type": job_type, "job_id": job_id, "transaction_id": txid, "committed": committed}
        )
        changed = True
    if changed:
        atomic_write_json(state_path, payload)
        validate_state(state_path)
    return reconciled


def image_item(payload: dict[str, Any], number: int) -> dict[str, Any]:
    for item in payload["images"]:
        if item.get("number") == number:
            return item
    raise StateError(f"image {number} is not present in planned images")


def reference_job(payload: dict[str, Any], reference_id: str) -> dict[str, Any]:
    for job in payload.get("reference_jobs", []):
        if job.get("id") == reference_id:
            return job
    raise StateError(f"reference job {reference_id} is not registered")


def request_path(project_dir: Path, number: int) -> Path:
    return project_dir / "生成请求" / f"{number:02d}.json"


def fallback_request_path(project_dir: Path, number: int, backend: str) -> Path:
    return project_dir / "生成请求" / f"{number:02d}-fallback-{backend}.json"


def repair_request_path(project_dir: Path, number: int) -> Path:
    return project_dir / "生成请求" / f"{number:02d}-repair.json"


def reference_request_path(project_dir: Path, reference_id: str) -> Path:
    return project_dir / "生成请求" / f"reference-{reference_id}.json"


def normalize_references(raw_paths: list[Path]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for index, raw_path in enumerate(raw_paths):
        path = raw_path.expanduser().resolve()
        if path in seen:
            raise StateError(f"duplicate reference path: {path}")
        seen.add(path)
        details = validate_image(path, f"references[{index}]")
        board = validate_reference_board(path, details)
        if board is not None:
            details["reference_board"] = board
        normalized.append(details)
    return normalized


def prompt_payload(prompt_file: Path) -> tuple[str, str]:
    path = prompt_file.expanduser().resolve()
    if not path.is_file():
        raise StateError(f"prompt file does not exist: {path}")
    prompt = path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise StateError("prompt file must contain a non-empty prompt")
    return prompt, sha256_bytes(prompt.encode("utf-8"))


def reference_fingerprints(references: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"path": item["path"], "sha256": item["sha256"]} for item in references
    ]


def request_core(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_sha256": request["prompt_sha256"],
        "reference_sha256": reference_fingerprints(request["references"]),
    }


def create_request(
    number: int,
    backend: str,
    route: str,
    model: str | None,
    prompt: str,
    prompt_hash: str,
    references: list[dict[str, Any]],
    reference_roles: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "image_number": number,
        "created_at": now_iso(),
        "backend": backend,
        "route": route,
        "model": model,
        "prompt": prompt,
        "prompt_sha256": prompt_hash,
        "prompt_summary": prompt_summary(prompt),
        "references": references,
        "reference_roles": reference_roles or [],
    }


def changed_inputs(
    base: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    base_core = request_core(base)
    current_core = request_core(current)
    return {
        "prompt_changed": (
            base_core["prompt_sha256"] != current_core["prompt_sha256"]
        ),
        "references_changed": (
            base_core["reference_sha256"] != current_core["reference_sha256"]
        ),
        "base_prompt_sha256": base_core["prompt_sha256"],
        "current_prompt_sha256": current_core["prompt_sha256"],
        "base_reference_sha256": base_core["reference_sha256"],
        "current_reference_sha256": current_core["reference_sha256"],
    }


def backend_state(payload: dict[str, Any], backend: str) -> dict[str, Any]:
    backends = payload.setdefault("transport_backends", {})
    record = backends.setdefault(
        backend,
        {
            "circuit_open": False,
            "reason": None,
            "error_key": None,
            "affected_images": [],
            "opened_at": None,
            "failure_window": [],
            "last_success_at": None,
        },
    )
    backend_defaults(record)
    return record


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    reconcile_recovery_transactions(state_path, payload, project_dir)
    item = image_item(payload, args.number)
    transport = item["transport"]
    transport_defaults(transport)
    in_flight = [
        candidate["number"]
        for candidate in payload["images"]
        if candidate.get("status") == "generating"
    ]
    in_flight.extend(
        f"reference:{candidate.get('id')}"
        for candidate in payload.get("reference_jobs", [])
        if candidate.get("status") == "generating"
    )
    if in_flight:
        raise StateError(
            f"only one generation may run at a time; in flight: {in_flight}. "
            "Record its result or recover-interrupted first"
        )
    repair_mode = getattr(args, "repair_mode", None)
    ensure_phase_target(payload, "repair" if repair_mode else "scene")
    batch, batch_auto_started = ensure_active_batch(payload)
    prompt, prompt_hash = prompt_payload(args.prompt_file)
    validate_4x5_prompt(prompt)
    references = normalize_references(args.reference)
    reference_roles, reference_role_warnings = normalize_reference_roles(
        getattr(args, "reference_role", []), references
    )
    if (
        len(references) > DEFAULT_REFERENCE_LIMIT
        and not getattr(args, "allow_high_reference_count", False)
    ):
        raise StateError(
            f"new requests may use at most {DEFAULT_REFERENCE_LIMIT} references by "
            "default; use --allow-high-reference-count only after explicit user approval"
        )
    default_route = {
        BUILT_IN_BACKEND: DEFAULT_BUILT_IN_ROUTE,
        FALLBACK_BACKEND: FALLBACK_BACKEND,
        SUBSCRIPTION_BRIDGE_BACKEND: SUBSCRIPTION_BRIDGE_ROUTE,
    }[args.backend]
    route = args.route or default_route
    model = args.model
    validate_reference_board_safe_prompt(
        prompt, payload, references, repair_mode
    )

    if repair_mode:
        if item["status"] != "review_pending":
            raise StateError(
                f"image {args.number} must be review_pending before a content repair"
            )
        if item.get("repair_count") != 0:
            raise StateError(
                f"image {args.number} has already used its one content repair"
            )
        if not item.get("candidate"):
            raise StateError(
                f"image {args.number} content repair requires an original candidate"
            )
        policy = payload.get("repair_policy")
        if policy and policy.get("mode") == "deferred_user_approved":
            approved = policy.get("approved_numbers", [])
            if payload.get("phase") != "repairing" or args.number not in approved:
                raise StateError(
                    f"image {args.number} repair is not authorized by the user-approved "
                    "repair report"
                )
            if not all(candidate.get("candidate") for candidate in payload["images"]):
                raise StateError(
                    "all original candidates must exist before deferred repairs begin"
                )
    elif item["status"] in {"pass", "review_pending", "needs_user"}:
        raise StateError(
            f"image {args.number} status {item['status']} is not eligible for generation"
        )
    same_backend = transport.get("backend") == args.backend
    next_eligible = parse_iso(transport.get("next_eligible_at"))
    if (
        same_backend
        and next_eligible
        and datetime.now(timezone.utc) < next_eligible
    ):
        raise StateError(
            f"image {args.number} is cooling down until "
            f"{next_eligible.isoformat()}"
        )
    backend_circuit = backend_state(payload, args.backend)
    is_probe = bool(transport.get("probe_granted")) and same_backend
    if transport.get("circuit_open") and same_backend and not is_probe:
        raise StateError(
            f"image {args.number} circuit is open; run resume-probe --user-approved"
        )
    if backend_circuit.get("circuit_open") and not is_probe:
        raise StateError(
            f"backend {args.backend} circuit is open; only an approved probe may run"
        )

    base_path = (
        repair_request_path(project_dir, args.number)
        if repair_mode
        else request_path(project_dir, args.number)
    )
    current = create_request(
        args.number,
        args.backend,
        route,
        model,
        prompt,
        prompt_hash,
        references,
        reference_roles,
    )
    if args.backend == BUILT_IN_BACKEND:
        if model is not None:
            raise StateError("built_in_imagegen preflight must not specify --model")
        if base_path.exists():
            base = load_json(base_path)
            differences = changed_inputs(base, current)
            if differences["prompt_changed"] or differences["references_changed"]:
                raise StateError(
                    "built-in retry input drift detected; keep the approved prompt "
                    "and required references unchanged"
                )
            board_fallback = base.get("reference_board_fallback")
            if isinstance(board_fallback, dict):
                if repair_mode is not None:
                    raise StateError("reference board fallback is not allowed for repairs")
                if not batch_is_active(batch):
                    raise StateError(
                        "reference board fallback must start in a new active batch"
                    )
                if batch.get("batch_id") == board_fallback.get("staged_batch_id"):
                    raise StateError(
                        "reference board fallback cannot run in the batch that timed out"
                    )
        else:
            atomic_write_json(base_path, current)
        request_file = base_path
        differences = changed_inputs(current, current)
    else:
        authorization = payload.get("fallback_authorizations", {}).get(args.backend)
        if not authorization or authorization.get("authorized") is not True:
            raise StateError(
                f"fallback backend {args.backend} requires authorize-fallback "
                "--user-approved"
            )
        if not model:
            raise StateError("fallback preflight requires --model")
        if authorization.get("model") != model:
            raise StateError(
                f"fallback model must match authorized model {authorization.get('model')}"
            )
        if not base_path.exists():
            raise StateError(
                "fallback requires an existing built-in request snapshot as baseline"
            )
        base = load_json(base_path)
        differences = changed_inputs(base, current)
        current["differences_from_built_in"] = differences
        request_file = fallback_request_path(project_dir, args.number, args.backend)
        if request_file.exists():
            existing = load_json(request_file)
            if request_core(existing) != request_core(current):
                raise StateError("fallback request drift detected")
        else:
            atomic_write_json(request_file, current)

    previous_backend = transport.get("backend")
    prior_status = item["status"]
    prior_scene_circuit_open = bool(transport.get("circuit_open"))
    prior_backend_circuit = {
        key: backend_circuit.get(key)
        for key in (
            "circuit_open",
            "reason",
            "error_key",
            "affected_images",
            "opened_at",
        )
    }
    if previous_backend != args.backend:
        transport.update(
            {
                "consecutive_failures": 0,
                "last_error": None,
                "last_error_type": None,
                "error_fingerprint": None,
                "backend_error_key": None,
                "circuit_open": False,
                "probe_granted": False,
                "probe_in_flight": False,
            }
        )
        prior_scene_circuit_open = False
    started_at = now_iso()
    attempt_id = uuid.uuid4().hex
    fingerprint = request_fingerprint(
        args.backend, route, prompt_hash, references
    )
    summary = reference_summary(references)
    text_summary = prompt_summary(prompt)
    runtime_budget = runtime_budget_seconds(references, repair_mode)
    transport.update(
        {
            "backend": args.backend,
            "route": route,
            "attempts_total": transport["attempts_total"] + 1,
            "prompt_sha256": prompt_hash,
            "reference_sha256": reference_fingerprints(references),
            "probe_granted": False,
            "probe_in_flight": is_probe,
            "circuit_open": prior_scene_circuit_open if is_probe else False,
            "request_file": str(request_file.relative_to(project_dir)),
            "model": model,
            "input_differences": differences,
            "reference_summary": summary,
            "prompt_summary": text_summary,
            "next_eligible_at": None,
            "active_attempt": {
                "attempt_id": attempt_id,
                "started_at": started_at,
                "probe": is_probe,
                "prior_status": prior_status,
                "prior_scene_circuit_open": prior_scene_circuit_open,
                "prior_backend_circuit": prior_backend_circuit,
                "request_fingerprint": fingerprint,
                "reference_summary": summary,
                "prompt_summary": text_summary,
                "repair_mode": repair_mode,
                "runtime_budget_seconds": runtime_budget,
            },
        }
    )
    item["status"] = "generating"
    if batch_is_active(batch):
        batch["attempt_count"] = batch.get("attempt_count", 0) + 1
        batch["last_attempt_id"] = attempt_id
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "preflight",
        "image_number": args.number,
        "backend": args.backend,
        "attempt_number": transport["attempts_total"],
        "attempt_id": attempt_id,
        "probe": is_probe,
        "repair_mode": repair_mode,
        "request_file": str(request_file),
        "prompt_sha256": prompt_hash,
        "references": reference_fingerprints(references),
        "reference_summary": summary,
        "prompt_summary": text_summary,
        "reference_roles": reference_roles,
        "warnings": reference_role_warnings,
        "runtime_budget_seconds": runtime_budget,
        "input_differences": differences,
        "batch_auto_started": batch_auto_started,
        "batch_id": batch.get("batch_id"),
    }


def calculate_fingerprints(
    transport: dict[str, Any], error_type: str
) -> tuple[str, str]:
    route = transport.get("route") or ""
    prompt_hash = transport.get("prompt_sha256") or ""
    reference_hashes = ",".join(
        reference["sha256"] for reference in transport.get("reference_sha256", [])
    )
    detailed = sha256_bytes(
        f"{route}\n{error_type}\n{prompt_hash}\n{reference_hashes}".encode("utf-8")
    )
    backend_key = sha256_bytes(f"{route}\n{error_type}".encode("utf-8"))
    return detailed, backend_key


def _auto_recovery_origin(request: dict[str, Any]) -> dict[str, Any]:
    existing = request.get("auto_recovery_origin")
    if isinstance(existing, dict):
        return existing
    references = request.get("references") or []
    roles = request.get("reference_roles") or []
    return {
        "references": json.loads(json.dumps(references)),
        "reference_roles": [
            roles[index] if index < len(roles) else f"reference {index + 1} continuity anchor"
            for index in range(len(references))
        ],
    }


def request_origin_reference_count(project_dir: Path, transport: dict[str, Any]) -> int:
    raw = transport.get("request_file")
    if not isinstance(raw, str) or not raw:
        return 0
    path = (project_dir / raw).resolve()
    if not path.is_file():
        return 0
    request = load_json(path)
    origin = request.get("auto_recovery_origin")
    references = origin.get("references", []) if isinstance(origin, dict) else request.get("references", [])
    return len(references) if isinstance(references, list) else 0


def _derived_reference_path(
    project_dir: Path,
    number: int,
    repair_mode: str | None,
    level: int,
    transaction: str | None = None,
    attachment_index: int | None = None,
) -> Path:
    directory = project_dir / AUTO_REFERENCE_DIR
    kind = "repair" if repair_mode else "original"
    suffix = f"-r{attachment_index}" if attachment_index is not None else ""
    transaction_suffix = f"-{transaction}" if transaction else ""
    stem = f"{number:02d}-{kind}-auto-l{level}{suffix}{transaction_suffix}"
    candidate = directory / f"{stem}.jpg"
    version = 2
    while candidate.exists() or sidecar_path(candidate).exists():
        candidate = directory / f"{stem}-v{version}.jpg"
        version += 1
    return candidate


def stage_automatic_reference_recovery(
    payload: dict[str, Any],
    project_dir: Path,
    item: dict[str, Any],
    transport: dict[str, Any],
    error_type: str,
    active_attempt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if error_type not in {"timeout", "no_candidate"}:
        return None
    current_level = int(transport.get("auto_recovery_level", 0))
    if current_level >= AUTO_RECOVERY_LEVELS:
        return None
    raw_request_file = transport.get("request_file")
    if not isinstance(raw_request_file, str) or not raw_request_file:
        return None
    current_path = (project_dir / raw_request_file).resolve()
    if not current_path.is_file():
        return None
    current = load_json(current_path)
    origin = _auto_recovery_origin(current)
    origin_refs = origin.get("references") or []
    origin_roles = origin.get("reference_roles") or []
    if len(origin_refs) not in {1, 2}:
        return None
    source_paths = [Path(reference["path"]).expanduser().resolve() for reference in origin_refs]
    next_level = current_level + 1
    active_attempt = active_attempt or transport.get("active_attempt") or {}
    repair_mode = active_attempt.get("repair_mode")
    spec = recovery_spec(len(source_paths), next_level, repair_mode)
    if spec is None:
        return None
    attempt_id = str(active_attempt.get("attempt_id") or "unknown")
    txid = transaction_id(attempt_id, next_level)
    journal = transaction_path(project_dir, txid)
    journal_record = {
        "schema_version": 1,
        "transaction_id": txid,
        "job_type": "repair" if repair_mode else "scene",
        "job_id": item["number"],
        "attempt_id": attempt_id,
        "level": next_level,
        "state": "staging",
        "started_at": now_iso(),
    }
    atomic_write_json(journal, journal_record)
    outputs: list[Path] = []
    temporary_outputs: list[Path] = []
    derivatives: list[dict[str, Any]] = []
    try:
        if spec["operation"] == "reference_board":
            output = _derived_reference_path(
                project_dir, int(item["number"]), repair_mode, next_level, txid
            )
            temporary = output.with_name(f".{output.stem}-{txid}{output.suffix}")
            output.parent.mkdir(parents=True, exist_ok=True)
            derivative = compose_reference_board(
                source_paths,
                [str(role) for role in origin_roles],
                [None, None],
                temporary,
                int(spec["jpeg_quality"]),
                tuple(spec["canvas_size"]),
            )
            os.replace(temporary, output)
            os.replace(sidecar_path(temporary), sidecar_path(output))
            moved_sidecar = load_json(sidecar_path(output))
            moved_sidecar["output"]["path"] = str(output)
            atomic_write_json(sidecar_path(output), moved_sidecar)
            outputs.append(output)
            derivatives.append(derivative)
            references = normalize_references([output])
            roles = [
                "single reference board; continuity anchors only: "
                + "; ".join(str(role) for role in origin_roles)
            ]
        else:
            selected_sources = source_paths if repair_mode == "edit" else source_paths[:1]
            for index, source in enumerate(selected_sources, 1):
                output = _derived_reference_path(
                    project_dir,
                    int(item["number"]),
                    repair_mode,
                    next_level,
                    txid,
                    index if repair_mode == "edit" else None,
                )
                temporary = output.with_name(f".{output.stem}-{txid}{output.suffix}")
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary_outputs.append(temporary)
                derivative = optimize_reference(
                    source,
                    temporary,
                    None,
                    int(spec["max_edge"]),
                    int(spec["jpeg_quality"]),
                )
                os.replace(temporary, output)
                outputs.append(output)
                derivatives.append(derivative)
            references = normalize_references(outputs)
            roles = [str(role) for role in origin_roles[: len(outputs)]]
        operation = str(spec["operation"])
        parameters = {key: value for key, value in spec.items() if key != "operation"}

        revised = create_request(
            int(item["number"]), current["backend"], current["route"], current.get("model"),
            current["prompt"], current["prompt_sha256"], references, roles,
        )
        revised["auto_recovery_origin"] = origin
        revised["recovery_transaction_id"] = txid
        revised["auto_recovery"] = {
            "level": next_level, "operation": operation, "trigger": error_type,
            "parameters": parameters, "derivatives": derivatives, "staged_at": now_iso(),
        }
        archive = next_versioned_path(current_path.parent, f"{current_path.stem}-auto-superseded")
        atomic_write_json(archive, current)
        atomic_write_json(current_path, revised)
    except Exception:
        for temporary in temporary_outputs:
            temporary.unlink(missing_ok=True)
            sidecar_path(temporary).unlink(missing_ok=True)
        for output in outputs:
            output.unlink(missing_ok=True)
            sidecar_path(output).unlink(missing_ok=True)
        journal_record.update({"state": "failed", "ended_at": now_iso()})
        atomic_write_json(journal, journal_record)
        raise
    record = {
        "transaction_id": txid,
        "level": next_level,
        "operation": operation,
        "trigger": error_type,
        "parameters": parameters,
        "request_file": str(current_path.relative_to(project_dir)),
        "archived_request": str(archive.relative_to(project_dir)),
        "derived_reference": str(outputs[0].relative_to(project_dir)),
        "derived_references": [str(path.relative_to(project_dir)) for path in outputs],
        "derived_sha256": references[0]["sha256"],
        "derived_sha256_all": [entry["sha256"] for entry in references],
        "origin_references": origin_refs,
        "next_references": reference_fingerprints(references),
        "next_reference_roles": roles,
        "staged_at": now_iso(),
    }
    transport["auto_recovery_level"] = next_level
    transport.setdefault("auto_recovery_history", []).append(record)
    transport["prompt_sha256"] = revised["prompt_sha256"]
    transport["reference_sha256"] = reference_fingerprints(references)
    transport["reference_summary"] = reference_summary(references)
    transport["input_differences"] = changed_inputs(current, revised)
    transport["recovery"] = {
        "level": next_level,
        "state": "ready",
        "transaction": {"transaction_id": txid, "journal": str(journal.relative_to(project_dir))},
        "last_error": None,
    }
    journal_record.update({"state": "committed", "ended_at": now_iso(), "request_file": str(current_path.relative_to(project_dir))})
    atomic_write_json(journal, journal_record)
    return record


def record_failure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    item = image_item(payload, args.number)
    if item["status"] != "generating":
        raise StateError(
            f"image {args.number} must be generating before recording a failure"
        )
    transport = item["transport"]
    transport_defaults(transport)
    if args.error_type not in TRANSPORT_ERRORS:
        raise StateError(f"unsupported transport error type: {args.error_type}")
    detailed, backend_key = calculate_fingerprints(transport, args.error_type)
    was_probe = transport.get("probe_in_flight", False)
    active = transport.get("active_attempt") or {}
    ended_at = now_iso()
    supplied_elapsed = getattr(args, "elapsed_seconds", None)
    duration = elapsed_seconds(active.get("started_at"))
    if payload.get("schema_version") == 2 and supplied_elapsed is not None:
        duration = supplied_elapsed
    budget = int(active.get("runtime_budget_seconds", IMAGE_CALL_TIMEOUT_SECONDS))
    if (
        payload.get("schema_version") == 3
        and args.error_type == "timeout"
        and (duration is None or duration < budget)
    ):
        raise StateError(
            f"timeout cannot be recorded before the persisted runtime budget; "
            f"actual={duration}, budget={budget}"
        )
    failures = transport["consecutive_failures"] + 1
    can_recover = (
        args.error_type in {"timeout", "no_candidate"}
        and int(transport.get("auto_recovery_level", 0)) < AUTO_RECOVERY_LEVELS
        and request_origin_reference_count(project_dir, transport) in {1, 2}
    )
    cooldown = None if can_recover else COOLDOWN_SECONDS.get(failures)
    transport.update(
        {
            "consecutive_failures": failures,
            "last_error": args.message,
            "last_error_type": args.error_type,
            "error_fingerprint": detailed,
            "backend_error_key": backend_key,
            "last_failure_at": ended_at,
            "probe_in_flight": False,
            "active_attempt": None,
            "reported_elapsed_seconds": supplied_elapsed,
            "next_eligible_at": (
                (datetime.now(timezone.utc) + timedelta(seconds=cooldown)).isoformat()
                if cooldown and not was_probe
                else None
            ),
        }
    )
    append_attempt_history(
        transport,
        {
            "attempt_id": active.get("attempt_id"),
            "started_at": active.get("started_at"),
            "ended_at": ended_at,
            "outcome": "transport_failure",
            "error_type": args.error_type,
            "message": args.message,
            "elapsed_seconds": duration,
            "reported_elapsed_seconds": supplied_elapsed,
            "probe": was_probe,
            "request_fingerprint": active.get("request_fingerprint"),
        },
    )
    scene_blocked = was_probe or (
        transport["consecutive_failures"] >= SCENE_FAILURE_LIMIT
        and not can_recover
    )
    transport["circuit_open"] = scene_blocked
    prior_status = active.get("prior_status")
    item["status"] = (
        "transport_blocked"
        if scene_blocked
        else "review_pending"
        if prior_status == "review_pending"
        else "pending"
    )

    backend = transport["backend"]
    backend_record = backend_state(payload, backend)
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=BACKEND_FAILURE_WINDOW_SECONDS
    )
    window = [
        event
        for event in backend_record.get("failure_window", [])
        if parse_iso(event.get("failed_at"))
        and parse_iso(event.get("failed_at")) >= cutoff
    ]
    event = backend_event("scene", args.number, args.error_type, backend_key, ended_at)
    if payload.get("schema_version") == 2:
        event = {**event, "image_number": args.number}
    window.append(event)
    backend_record["failure_window"] = window[-ATTEMPT_HISTORY_LIMIT:]
    affected_jobs = sorted(
        {
            event_job_key(record)
            for record in window
            if record.get("error_key") == backend_key
            and record.get("error_type") == args.error_type
        }
    )
    affected = sorted(
        int(key.split(":", 1)[1])
        for key in affected_jobs
        if key.startswith("scene:") and key.split(":", 1)[1].isdigit()
    )
    window_warning = len(set(affected_jobs)) >= BACKEND_SCENE_THRESHOLD
    if window_warning:
        backend_record.update(
            {
                "circuit_open": False,
                "reason": args.error_type,
                "error_key": backend_key,
                "affected_images": affected,
                "affected_jobs": affected_jobs,
                "opened_at": now_iso(),
                "health_warning": True,
            }
        )
    backend_blocked = False
    transport["backend_health_warning"] = window_warning
    if can_recover:
        next_level = int(transport.get("auto_recovery_level", 0)) + 1
        txid = transaction_id(str(active.get("attempt_id") or "unknown"), next_level)
        transport["recovery"] = {
            "level": int(transport.get("auto_recovery_level", 0)),
            "state": "staging",
            "transaction": {"transaction_id": txid, "attempt_id": active.get("attempt_id"), "next_level": next_level},
            "last_error": None,
        }
        transport["recovery"]["transaction"].update(
            {"prior_status": active.get("prior_status"), "repair_mode": active.get("repair_mode")}
        )
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    auto_recovery = None
    recovery_error = None
    if can_recover:
        try:
            auto_recovery = stage_automatic_reference_recovery(
                payload, project_dir, item, transport, args.error_type, active
            )
            item["status"] = "review_pending" if active.get("prior_status") == "review_pending" else "pending"
            atomic_write_json(state_path, payload)
            validate_state(state_path)
        except (StateError, OSError, ValueError) as exc:
            recovery_error = str(exc)
            transport["circuit_open"] = True
            transport["recovery"] = {
                "level": int(transport.get("auto_recovery_level", 0)),
                "state": "failed",
                "transaction": transport.get("recovery", {}).get("transaction"),
                "last_error": recovery_error,
            }
            item["status"] = "transport_blocked"
            scene_blocked = True
            atomic_write_json(state_path, payload)
            validate_state(state_path)
    return {
        "command": "record-failure",
        "image_number": args.number,
        "consecutive_failures": transport["consecutive_failures"],
        "scene_circuit_open": scene_blocked,
        "backend_circuit_open": backend_blocked,
        "affected_images": affected,
        "affected_jobs": affected_jobs,
        "error_fingerprint": detailed,
        "cooldown_until": transport.get("next_eligible_at"),
        "batch_stopped": False,
        "auto_recovery": auto_recovery,
        "retry_ready": bool(auto_recovery),
        "recovery_error": recovery_error,
        "backend_health_warning": window_warning,
    }


def resume_probe(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError("resume-probe requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
    require_legacy_v2(payload, "resume-probe")
    item = image_item(payload, args.number)
    transport = item["transport"]
    backend = backend_state(payload, transport["backend"])
    if item["status"] != "transport_blocked" and not backend.get("circuit_open"):
        raise StateError(
            "resume-probe is only valid for a scene or backend with an open circuit"
        )
    if transport.get("probe_granted") or transport.get("probe_in_flight"):
        raise StateError("an approved probe is already available or in flight")
    transport["probe_granted"] = True
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "resume-probe",
        "image_number": args.number,
        "probe_granted": True,
        "attempts_allowed": 1,
    }


def record_success(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    item = image_item(payload, args.number)
    if item["status"] != "generating":
        raise StateError(
            f"image {args.number} must be generating before recording success"
        )
    candidate = args.candidate.expanduser().resolve()
    candidate_details = validate_image(candidate, "candidate")
    require_exact_4x5(candidate_details, "candidate")
    try:
        candidate_relative = str(candidate.relative_to(project_dir))
    except ValueError as exc:
        raise StateError("candidate must be copied into the project directory") from exc

    transport = item["transport"]
    transport_defaults(transport)
    active = transport.get("active_attempt") or {}
    ended_at = now_iso()
    supplied_elapsed = getattr(args, "elapsed_seconds", None)
    duration = (
        supplied_elapsed
        if supplied_elapsed is not None
        else elapsed_seconds(active.get("started_at"))
    )
    append_attempt_history(
        transport,
        {
            "attempt_id": active.get("attempt_id"),
            "started_at": active.get("started_at"),
            "ended_at": ended_at,
            "outcome": "success",
            "error_type": None,
            "message": None,
            "elapsed_seconds": duration,
            "probe": bool(transport.get("probe_in_flight")),
            "request_fingerprint": active.get("request_fingerprint"),
            "candidate": candidate_relative,
        },
    )
    transport.update(
        {
            "consecutive_failures": 0,
            "last_error": None,
            "last_error_type": None,
            "error_fingerprint": None,
            "backend_error_key": None,
            "circuit_open": False,
            "probe_granted": False,
            "probe_in_flight": False,
            "last_success_at": ended_at,
            "next_eligible_at": None,
            "active_attempt": None,
        }
    )
    backend_record = backend_state(payload, transport["backend"])
    backend_record.update(
        {
            "circuit_open": False,
            "reason": None,
            "error_key": None,
            "affected_images": [],
            "opened_at": None,
            "failure_window": [],
            "last_success_at": ended_at,
            "health_warning": False,
        }
    )
    repair_mode = active.get("repair_mode")
    if repair_mode:
        item["repair_count"] = 1
        item["repair_mode"] = repair_mode
        item["repair_file"] = candidate_relative
    else:
        item["candidate"] = candidate_relative
    item["status"] = "review_pending"
    batch = batch_record(payload)
    if batch_is_active(batch):
        batch["success_count"] = batch.get("success_count", 0) + 1
        refresh_batch(batch)
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-success",
        "image_number": args.number,
        "status": "review_pending",
        "candidate": candidate_details,
        "repair_mode": repair_mode,
        "backend_circuit_open": False,
        "batch_status": batch.get("status") if batch else None,
        "batch_success_count": batch.get("success_count") if batch else None,
    }


def invalidate_candidate_aspect(args: argparse.Namespace) -> dict[str, Any]:
    """Archive a previously accepted wrong-aspect candidate and reopen the scene."""
    if not args.user_approved:
        raise StateError("invalidate-candidate-aspect requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    item = image_item(payload, args.number)
    if item.get("status") not in {"pass", "review_pending"}:
        raise StateError(
            f"image {args.number} must be pass or review_pending before aspect invalidation"
        )
    candidate_raw = item.get("candidate")
    if not candidate_raw:
        raise StateError(f"image {args.number} has no original candidate")
    candidate = (project_dir / candidate_raw).resolve()
    try:
        candidate.relative_to(project_dir)
    except ValueError as exc:
        raise StateError("candidate must be inside the project directory") from exc
    details = validate_image(candidate, "candidate")
    try:
        require_exact_4x5(details, "candidate")
    except StateError:
        pass
    else:
        raise StateError("candidate is already exact vertical 4:5")

    archive = args.archive.expanduser()
    if not archive.is_absolute():
        archive = project_dir / archive
    archive = archive.resolve()
    try:
        archive.relative_to(project_dir)
    except ValueError as exc:
        raise StateError("archive must be inside the project directory") from exc
    if archive.exists():
        raise StateError(f"refusing to overwrite archive: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)

    original_item = json.loads(json.dumps(item))
    candidate.rename(archive)
    try:
        transport = item["transport"]
        transport_defaults(transport)
        append_attempt_history(
            transport,
            {
                "attempt_id": None,
                "started_at": None,
                "ended_at": now_iso(),
                "outcome": "aspect_invalidated",
                "error_type": None,
                "message": f"accepted candidate had wrong aspect {details['width']}x{details['height']}",
                "elapsed_seconds": None,
                "probe": False,
                "request_fingerprint": None,
                "candidate": str(archive.relative_to(project_dir)),
            },
        )
        item.update(
            {
                "status": "pending",
                "candidate": None,
                "hard_failures": [],
                "photo_red_flags": [],
                "repair_count": 0,
                "repair_mode": None,
                "repair_file": None,
                "final_source": None,
                "notes": (
                    f"Previous candidate archived after user-confirmed aspect failure: "
                    f"{archive.relative_to(project_dir)} ({details['width']}x{details['height']})"
                ),
            }
        )
        item.pop("repair_recommendation", None)
        atomic_write_json(state_path, payload)
        validate_state(state_path)
    except Exception:
        item.clear()
        item.update(original_item)
        archive.rename(candidate)
        raise
    return {
        "ok": True,
        "command": "invalidate-candidate-aspect",
        "image_number": args.number,
        "old_size": [details["width"], details["height"]],
        "archived_candidate": str(archive.relative_to(project_dir)),
        "status": "pending",
    }


def recover_interrupted(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_no_candidate:
        raise StateError(
            "recover-interrupted requires --confirm-no-candidate after checking "
            "the generated output and project candidate directories"
        )
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
    reference_id = getattr(args, "reference_id", None)
    number = getattr(args, "number", None)
    if reference_id:
        item = reference_job(payload, reference_id)
        job_type = "reference"
        job_id: str | int = reference_id
    elif number is not None:
        item = image_item(payload, number)
        job_type = "scene"
        job_id = number
    else:
        raise StateError("recover-interrupted requires --number or --reference-id")
    if item["status"] != "generating":
        raise StateError(
            f"{job_type} {job_id} is not generating; no interrupted attempt to recover"
        )
    transport = item["transport"]
    transport_defaults(transport)
    active = transport.get("active_attempt")
    repair_in_flight = bool(active and active.get("repair_mode"))
    if item.get("candidate") is not None and not repair_in_flight:
        raise StateError("cannot recover an interrupted original attempt with a candidate")
    if repair_in_flight and item.get("repair_file") is not None:
        raise StateError("cannot recover an interrupted repair after a repair file exists")
    was_probe = bool(
        transport.get("probe_in_flight")
        or (active and active.get("probe"))
    )
    prior_status = (
        active.get("prior_status")
        if active
        else ("transport_blocked" if was_probe else "pending")
    )
    prior_scene_circuit = (
        bool(active.get("prior_scene_circuit_open"))
        if active
        else was_probe or transport.get("consecutive_failures", 0) >= SCENE_FAILURE_LIMIT
    )
    if was_probe:
        prior_status = "transport_blocked"
        prior_scene_circuit = True
    ended_at = now_iso()
    append_attempt_history(
        transport,
        {
            "attempt_id": active.get("attempt_id") if active else None,
            "started_at": active.get("started_at") if active else None,
            "ended_at": ended_at,
            "outcome": "interrupted",
            "error_type": None,
            "message": args.reason,
            "elapsed_seconds": (
                elapsed_seconds(active.get("started_at")) if active else None
            ),
            "probe": was_probe,
            "request_fingerprint": (
                active.get("request_fingerprint") if active else None
            ),
        },
    )
    transport.update(
        {
            "active_attempt": None,
            "probe_granted": False,
            "probe_in_flight": False,
            "circuit_open": prior_scene_circuit,
            "next_eligible_at": None,
            "last_interrupted_at": ended_at,
            "last_interruption_reason": args.reason,
        }
    )
    item["status"] = prior_status
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "recover-interrupted",
        "job_type": job_type,
        "job_id": job_id,
        "image_number": number,
        "reference_id": reference_id,
        "status": item["status"],
        "scene_circuit_open": transport["circuit_open"],
        "backend_circuit_open": backend_state(
            payload, transport["backend"]
        ).get("circuit_open"),
        "consecutive_failures": transport["consecutive_failures"],
        "attempts_total": transport["attempts_total"],
    }


def batch_start(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
    in_flight = [
        item["number"]
        for item in payload["images"]
        if item.get("status") == "generating"
    ]
    if in_flight:
        raise StateError(
            f"cannot start a batch while generation is in flight: {in_flight}"
        )
    existing = batch_record(payload)
    refresh_batch(existing)
    if batch_is_active(existing):
        raise StateError(
            f"batch {existing.get('batch_id')} is already active"
        )
    record = {
        "batch_id": uuid.uuid4().hex,
        "status": "active",
        "started_at": now_iso(),
        "ended_at": None,
        "success_limit": BATCH_SUCCESS_LIMIT,
        "runtime_limit_seconds": BATCH_RUNTIME_SECONDS,
        "success_count": 0,
        "attempt_count": 0,
        "stopped_reason": None,
        "last_attempt_id": None,
    }
    payload["transport_batch"] = record
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "batch-start", **record}


def batch_status(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    reconciled = reconcile_recovery_transactions(state_path, payload, project_dir)
    now = datetime.now(timezone.utc)
    batch = batch_record(payload)
    refresh_batch(batch)
    in_flight: list[str] = []
    in_flight_details: list[dict[str, Any]] = []
    ready: list[int] = []
    cooling: list[dict[str, Any]] = []
    blocked: list[int] = []
    board_fallbacks: list[dict[str, Any]] = []
    reference_ready: list[str] = []
    reference_cooling: list[dict[str, Any]] = []
    reference_blocked: list[str] = []
    recoveries: list[dict[str, Any]] = []
    for job_type, job_id, item in [
        *(('scene', entry['number'], entry) for entry in payload.get('images', [])),
        *(('reference', entry['id'], entry) for entry in payload.get('reference_jobs', [])),
    ]:
        transport = item["transport"]
        transport_defaults(transport)
        recovery = transport.get("recovery", {})
        if recovery.get("state") != "idle" or recovery.get("level"):
            recoveries.append(
                {"job_type": job_type, "job_id": job_id, **recovery}
            )
        if item.get("status") == "generating":
            active = transport.get("active_attempt") or {}
            duration = elapsed_seconds(active.get("started_at"))
            budget = active.get("runtime_budget_seconds", IMAGE_CALL_TIMEOUT_SECONDS)
            key = f"{job_type}:{job_id}"
            in_flight.append(key)
            in_flight_details.append(
                {
                    "job_type": job_type,
                    "job_id": job_id,
                    "attempt_id": active.get("attempt_id"),
                    "elapsed_seconds": duration,
                    "runtime_budget_seconds": budget,
                    "overdue": bool(duration is not None and duration >= budget),
                    "repair_mode": active.get("repair_mode"),
                    "reference_summary": active.get("reference_summary"),
                }
            )
    for job_type, job_id, item in phase_jobs(payload):
        transport = item["transport"]
        status = item.get("status")
        if status == "transport_blocked":
            if job_type == "reference":
                reference_blocked.append(str(job_id))
            else:
                blocked.append(int(job_id))
            continue
        runnable_status = "review_pending" if job_type == "repair" else "pending"
        if status != runnable_status:
            continue
        next_eligible = parse_iso(transport.get("next_eligible_at"))
        cooling_record = {"job_type": job_type, "job_id": job_id, "next_eligible_at": next_eligible.isoformat()} if next_eligible and now < next_eligible else None
        if cooling_record:
            if job_type == "reference":
                reference_cooling.append({"reference_id": str(job_id), "next_eligible_at": cooling_record["next_eligible_at"]})
            else:
                cooling.append({"image_number": int(job_id), "next_eligible_at": cooling_record["next_eligible_at"]})
        elif not transport.get("circuit_open") and not backend_state(payload, transport["backend"]).get("circuit_open"):
            if job_type == "reference":
                reference_ready.append(str(job_id))
            else:
                ready.append(int(job_id))
        if payload.get("schema_version") == 2 and job_type == "scene" and status in {"pending", "transport_blocked"}:
            board_status = reference_board_fallback_status(payload, project_dir, item)
            if board_status["eligible"]:
                board_fallbacks.append(board_status)
    open_backends = sorted(
        name
        for name, record in payload.get("transport_backends", {}).items()
        if record.get("circuit_open")
    )
    backend_health_warnings = {
        name: {
            "reason": record.get("reason"),
            "affected_images": record.get("affected_images", []),
            "affected_jobs": record.get("affected_jobs", []),
        }
        for name, record in payload.get("transport_backends", {}).items()
        if record.get("health_warning")
    }
    return {
        "command": "batch-status",
        "phase": payload.get("phase"),
        "eligible_job_type": eligible_job_type(str(payload.get("phase"))),
        "batch": batch,
        "in_flight": in_flight,
        "in_flight_details": in_flight_details,
        "ready": ready,
        "cooling": cooling,
        "transport_blocked": blocked,
        "reference_ready": reference_ready,
        "reference_cooling": reference_cooling,
        "reference_transport_blocked": reference_blocked,
        "reference_board_fallbacks": board_fallbacks,
        "recoveries": recoveries,
        "reconciled_recoveries": reconciled,
        "next_runnable": (
            {"job_type": "reference", "job_id": reference_ready[0]}
            if reference_ready
            else {"job_type": eligible_job_type(str(payload.get("phase"))), "job_id": ready[0]}
            if ready
            else None
        ),
        "open_backends": open_backends,
        "backend_health_warnings": backend_health_warnings,
        "needs_user_probe_approval": bool(payload.get("schema_version") == 2 and (open_backends or blocked)),
        "next_action": (
            "recover-interrupted or record the in-flight result"
            if in_flight
            else "compose and stage the eligible reference board fallback"
            if board_fallbacks
            else "show the repair report and request explicit user approval"
            if payload.get("phase") == "awaiting_repair_approval"
            else "generate the first ready reference job"
            if payload.get("phase") == "reference_self_review" and reference_ready
            else "generate the first ready image"
            if ready
            else "phase complete: prepare or update the blocked report"
            if blocked or reference_blocked
            else "no generation task is runnable in the current phase"
        ),
    }


def materialize_prompt(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    _, project_dir = require_v2(state_path)
    snapshot_path = (
        repair_request_path(project_dir, args.number)
        if getattr(args, "repair_mode", False)
        else request_path(project_dir, args.number)
    )
    snapshot = load_json(snapshot_path)
    prompt = snapshot.get("prompt")
    prompt_hash = snapshot.get("prompt_sha256")
    if not isinstance(prompt, str) or not prompt:
        raise StateError(f"request snapshot has no prompt: {snapshot_path}")
    actual_hash = sha256_bytes(prompt.encode("utf-8"))
    if prompt_hash != actual_hash:
        raise StateError(
            f"request snapshot prompt hash is invalid: {snapshot_path}"
        )
    output = args.output.expanduser().resolve()
    if output.exists():
        raise StateError(f"materialized prompt output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(prompt.encode("utf-8"))
    return {
        "command": "materialize-prompt",
        "image_number": args.number,
        "output": str(output),
        "bytes": len(prompt.encode("utf-8")),
        "prompt_sha256": actual_hash,
    }


def next_versioned_path(directory: Path, stem: str, suffix: str = ".json") -> Path:
    version = 1
    while True:
        candidate = directory / f"{stem}-v{version}{suffix}"
        if not candidate.exists():
            return candidate
        version += 1


def authorize_reference_board_policy(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError(
            "authorize-reference-board-policy requires explicit --user-approved"
        )
    if args.timeout_seconds != MULTI_REFERENCE_TIMEOUT_SECONDS:
        raise StateError(
            f"reference board policy timeout must be {MULTI_REFERENCE_TIMEOUT_SECONDS} seconds"
        )
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    require_legacy_v2(payload, "authorize-reference-board-policy")
    existing = payload.get("reference_board_policy")
    if isinstance(existing, dict) and existing.get("authorized") is True:
        if existing.get("timeout_seconds") != args.timeout_seconds:
            raise StateError("reference board policy is already authorized with another timeout")
        return {
            "command": "authorize-reference-board-policy",
            **existing,
            "already_authorized": True,
        }
    snapshot_dir = project_dir / "生成请求"
    snapshot = next_versioned_path(
        snapshot_dir, "review-state-before-reference-board-policy"
    )
    atomic_write_json(snapshot, payload)
    authorization = {
        "authorized": True,
        "authorized_at": now_iso(),
        "timeout_seconds": args.timeout_seconds,
        "source_count": 2,
        "original_generation_only": True,
        "snapshot_file": str(snapshot.relative_to(project_dir)),
    }
    payload["reference_board_policy"] = authorization
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "authorize-reference-board-policy",
        **authorization,
        "already_authorized": False,
    }


def reference_board_fallback_status(
    payload: dict[str, Any], project_dir: Path, item: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    policy = payload.get("reference_board_policy")
    if not isinstance(policy, dict) or policy.get("authorized") is not True:
        reasons.append("project policy is not authorized")
        threshold = MULTI_REFERENCE_TIMEOUT_SECONDS
    else:
        threshold = policy.get("timeout_seconds", MULTI_REFERENCE_TIMEOUT_SECONDS)
    if item.get("candidate") is not None:
        reasons.append("candidate already exists")
    if item.get("status") not in {"pending", "transport_blocked"}:
        reasons.append(f"status {item.get('status')} is not eligible")
    transport = item.get("transport") or {}
    if transport.get("active_attempt") is not None:
        reasons.append("attempt is still active")
    history = transport.get("attempt_history") or []
    latest = history[-1] if history else None
    if not isinstance(latest, dict):
        reasons.append("no completed attempt is recorded")
    else:
        if latest.get("outcome") != "transport_failure":
            reasons.append("latest attempt is not a transport failure")
        if latest.get("error_type") != "timeout":
            reasons.append("latest failure is not a timeout")
        elapsed = latest.get("elapsed_seconds")
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
            reasons.append("latest timeout has no elapsed duration")
        elif elapsed < threshold:
            reasons.append(f"latest timeout is shorter than {threshold} seconds")
    current_path = request_path(project_dir, int(item["number"]))
    request: dict[str, Any] | None = None
    if not current_path.is_file():
        reasons.append("original request snapshot is missing")
    else:
        request = load_json(current_path)
        references = request.get("references")
        if not isinstance(references, list) or len(references) != 2:
            reasons.append("request does not contain exactly two references")
        elif any(reference.get("reference_board") for reference in references):
            reasons.append("request already uses a reference board")
        if request.get("reference_board_fallback") is not None:
            reasons.append("request has already used the reference board fallback")
        if isinstance(latest, dict) and latest.get("request_fingerprint"):
            try:
                expected = request_fingerprint(
                    request["backend"],
                    request["route"],
                    request["prompt_sha256"],
                    references,
                )
            except (KeyError, TypeError):
                reasons.append("request snapshot is incomplete")
            else:
                if latest.get("request_fingerprint") != expected:
                    reasons.append("latest timeout belongs to another request fingerprint")
    return {
        "image_number": item.get("number"),
        "eligible": not reasons,
        "timeout_seconds": threshold,
        "reasons": reasons,
        "request_file": str(current_path),
    }


def stage_reference_board_fallback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_no_candidate:
        raise StateError(
            "stage-reference-board-fallback requires --confirm-no-candidate"
        )
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    require_legacy_v2(payload, "stage-reference-board-fallback")
    item = image_item(payload, args.number)
    eligibility = reference_board_fallback_status(payload, project_dir, item)
    if not eligibility["eligible"]:
        raise StateError(
            "reference board fallback is not eligible: "
            + "; ".join(eligibility["reasons"])
        )
    batch = batch_record(payload)
    if batch_is_active(batch):
        raise StateError(
            "reference board fallback may only be staged after the timed-out batch stops"
        )
    board_path = args.reference_board.expanduser().resolve()
    board_references = normalize_references([board_path])
    board_manifest = board_references[0].get("reference_board")
    if not isinstance(board_manifest, dict):
        raise StateError("--reference-board must have a valid reference board sidecar")

    current_path = request_path(project_dir, args.number)
    old = load_json(current_path)
    validate_reference_board_safe_prompt(
        old["prompt"], payload, board_references, None
    )
    old_references = old["references"]
    old_fingerprints = reference_fingerprints(old_references)
    board_sources = [
        {"path": source["path"], "sha256": source["sha256"]}
        for source in board_manifest["sources"]
    ]
    if board_sources != old_fingerprints:
        raise StateError(
            "reference board sources must match the timed-out request in the same order"
        )
    old_roles = old.get("reference_roles") or []
    board_roles = [source["role"] for source in board_manifest["sources"]]
    if old_roles and board_roles != old_roles:
        raise StateError(
            "reference board source roles must match the timed-out request roles"
        )
    combined_role = "单文件参考板（仅连续性锚点，禁止复制分栏、间隔或源构图）：" + "；".join(
        board_roles
    )
    revised = create_request(
        args.number,
        old["backend"],
        old["route"],
        old.get("model"),
        old["prompt"],
        old["prompt_sha256"],
        board_references,
        [combined_role],
    )
    staged_at = now_iso()
    revised["reference_board_fallback"] = {
        "staged_at": staged_at,
        "staged_batch_id": batch.get("batch_id") if batch else None,
        "source_reference_sha256": old_fingerprints,
        "board_sha256": board_references[0]["sha256"],
        "timeout_seconds": eligibility["timeout_seconds"],
    }
    archive = next_versioned_path(
        current_path.parent, f"{args.number:02d}-reference-board-superseded"
    )
    atomic_write_json(archive, old)
    atomic_write_json(current_path, revised)

    transport = item["transport"]
    transport_defaults(transport)
    summary = reference_summary(board_references)
    differences = changed_inputs(old, revised)
    transport.update(
        {
            "prompt_sha256": old["prompt_sha256"],
            "reference_sha256": reference_fingerprints(board_references),
            "request_file": str(current_path.relative_to(project_dir)),
            "input_differences": differences,
            "reference_summary": summary,
            "reference_board_fallback_staged_at": staged_at,
        }
    )
    record = {
        "image_number": args.number,
        "staged_at": staged_at,
        "archived_request": str(archive.relative_to(project_dir)),
        "request_file": str(current_path.relative_to(project_dir)),
        "board": str(board_path),
        "board_sha256": board_references[0]["sha256"],
        "source_reference_sha256": old_fingerprints,
        "prompt_sha256": old["prompt_sha256"],
        "cooldown_until": transport.get("next_eligible_at"),
        "staged_batch_id": batch.get("batch_id") if batch else None,
    }
    payload.setdefault("reference_board_fallbacks", []).append(record)
    try:
        atomic_write_json(state_path, payload)
        validate_state(state_path)
    except Exception:
        atomic_write_json(current_path, old)
        archive.unlink(missing_ok=True)
        raise
    return {"command": "stage-reference-board-fallback", **record}


def supersede_repair(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError("supersede-repair requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    require_legacy_v2(payload, "supersede-repair")
    item = image_item(payload, args.number)
    if item.get("status") != "review_pending" or item.get("repair_count") != 0:
        raise StateError(
            "supersede-repair requires a review_pending image with unused repair"
        )
    transport = item["transport"]
    transport_defaults(transport)
    history = transport.get("attempt_history", [])
    if not history or history[-1].get("outcome") not in {
        "interrupted",
        "transport_failure",
    }:
        raise StateError(
            "supersede-repair requires a recorded interrupted or failed repair attempt"
        )
    current = repair_request_path(project_dir, args.number)
    if not current.is_file():
        raise StateError(f"repair request snapshot does not exist: {current}")
    snapshot = load_json(current)
    version = 1
    while True:
        archived = current.with_name(
            f"{args.number:02d}-repair-v{version}-superseded.json"
        )
        if not archived.exists():
            break
        version += 1
    current.replace(archived)
    authorization = {
        "image_number": args.number,
        "approved_at": now_iso(),
        "reason": args.reason,
        "archived_request": str(archived.relative_to(project_dir)),
        "old_prompt_sha256": snapshot.get("prompt_sha256"),
        "old_reference_sha256": reference_fingerprints(
            snapshot.get("references", [])
        ),
    }
    payload.setdefault("repair_request_supersessions", []).append(authorization)
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "supersede-repair", **authorization}


def revise_request(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError("revise-request requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    require_legacy_v2(payload, "revise-request")
    item = image_item(payload, args.number)
    if item.get("status") not in {"pending", "transport_blocked"}:
        raise StateError(
            "revise-request requires a pending or transport_blocked original request"
        )
    if item.get("candidate") is not None:
        raise StateError("revise-request is only allowed when no candidate exists")
    transport = item["transport"]
    transport_defaults(transport)
    if transport.get("active_attempt") is not None:
        raise StateError("revise-request cannot run while an attempt is active")
    history = transport.get("attempt_history", [])
    if not history or history[-1].get("outcome") not in {
        "interrupted",
        "transport_failure",
        "aspect_invalidated",
    }:
        raise StateError(
            "revise-request requires a recorded interrupted, failed, or aspect-invalidated attempt"
        )
    current_path = request_path(project_dir, args.number)
    if not current_path.is_file():
        raise StateError(f"request snapshot does not exist: {current_path}")
    prompt, prompt_hash = prompt_payload(args.prompt_file)
    validate_4x5_prompt(prompt)
    references = normalize_references(args.reference)
    reference_roles, _ = normalize_reference_roles(
        getattr(args, "reference_role", []), references
    )
    if (
        len(references) > DEFAULT_REFERENCE_LIMIT
        and not args.allow_high_reference_count
    ):
        raise StateError(
            f"revised requests may use at most {DEFAULT_REFERENCE_LIMIT} references "
            "without explicit high-count approval"
        )
    old = load_json(current_path)
    revised = create_request(
        args.number,
        BUILT_IN_BACKEND,
        DEFAULT_BUILT_IN_ROUTE,
        None,
        prompt,
        prompt_hash,
        references,
        reference_roles,
    )
    differences = changed_inputs(old, revised)
    if not differences["prompt_changed"] and not differences["references_changed"]:
        raise StateError("revised request must change the prompt or references")
    version = 1
    while True:
        archived = current_path.with_name(
            f"{args.number:02d}-v{version}-superseded.json"
        )
        if not archived.exists():
            break
        version += 1
    atomic_write_json(archived, old)
    atomic_write_json(current_path, revised)
    authorization = {
        "image_number": args.number,
        "approved_at": now_iso(),
        "reason": args.reason,
        "archived_request": str(archived.relative_to(project_dir)),
        "request_file": str(current_path.relative_to(project_dir)),
        "old_prompt_sha256": old.get("prompt_sha256"),
        "new_prompt_sha256": prompt_hash,
        "old_reference_sha256": reference_fingerprints(old.get("references", [])),
        "new_reference_sha256": reference_fingerprints(references),
    }
    payload.setdefault("request_supersessions", []).append(authorization)
    transport.update(
        {
            "backend": BUILT_IN_BACKEND,
            "route": DEFAULT_BUILT_IN_ROUTE,
            "consecutive_failures": 0,
            "last_error": None,
            "last_error_type": None,
            "error_fingerprint": None,
            "backend_error_key": None,
            "prompt_sha256": prompt_hash,
            "reference_sha256": reference_fingerprints(references),
            "circuit_open": False,
            "probe_granted": False,
            "probe_in_flight": False,
            "request_file": str(current_path.relative_to(project_dir)),
            "input_differences": differences,
            "reference_summary": reference_summary(references),
            "next_eligible_at": None,
            "active_attempt": None,
        }
    )
    item["status"] = "pending"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {"command": "revise-request", **authorization}


def authorize_fallback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError("authorize-fallback requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
    authorizations = payload.setdefault("fallback_authorizations", {})
    authorizations[args.backend] = {
        "authorized": True,
        "authorized_at": now_iso(),
        "model": args.model,
    }
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "authorize-fallback",
        "backend": args.backend,
        "model": args.model,
        "authorized": True,
    }


def _reference_request(
    reference_id: str,
    prompt: str,
    prompt_hash: str,
    references: list[dict[str, Any]],
    roles: list[str],
) -> dict[str, Any]:
    request = create_request(
        0,
        BUILT_IN_BACKEND,
        DEFAULT_BUILT_IN_ROUTE,
        None,
        prompt,
        prompt_hash,
        references,
        roles,
    )
    request.pop("image_number", None)
    request["reference_id"] = reference_id
    request["job_type"] = "reference"
    return request


def reference_preflight(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    reconcile_recovery_transactions(state_path, payload, project_dir)
    ensure_phase_target(payload, "reference")
    job = reference_job(payload, args.reference_id)
    if job.get("status") != "pending":
        raise StateError(
            f"reference job {args.reference_id} status {job.get('status')} is not eligible"
        )
    in_flight_images = [
        item["number"] for item in payload["images"] if item.get("status") == "generating"
    ]
    in_flight_references = [
        item["id"]
        for item in payload.get("reference_jobs", [])
        if item.get("status") == "generating"
    ]
    if in_flight_images or in_flight_references:
        raise StateError(
            f"only one generation may run at a time; images={in_flight_images}, "
            f"references={in_flight_references}"
        )
    batch, batch_auto_started = ensure_active_batch(payload)
    prompt, prompt_hash = prompt_payload(args.prompt_file)
    references = normalize_references(args.reference)
    roles, warnings = normalize_reference_roles(args.reference_role, references)
    if len(references) > DEFAULT_REFERENCE_LIMIT:
        raise StateError("reference jobs accept at most two input references")
    validate_reference_board_safe_prompt(prompt, payload, references, None)
    transport = job["transport"]
    transport_defaults(transport)
    next_eligible = parse_iso(transport.get("next_eligible_at"))
    if next_eligible and datetime.now(timezone.utc) < next_eligible:
        raise StateError(
            f"reference job {args.reference_id} is cooling down until {next_eligible.isoformat()}"
        )
    request_file = reference_request_path(project_dir, args.reference_id)
    current = _reference_request(args.reference_id, prompt, prompt_hash, references, roles)
    if request_file.exists():
        existing = load_json(request_file)
        differences = changed_inputs(existing, current)
        if differences["prompt_changed"] or differences["references_changed"]:
            raise StateError("reference retry input drift detected")
    else:
        atomic_write_json(request_file, current)
        differences = changed_inputs(current, current)
    summary = reference_summary(references)
    attempt_id = uuid.uuid4().hex
    started_at = now_iso()
    transport.update(
        {
            "backend": BUILT_IN_BACKEND,
            "route": DEFAULT_BUILT_IN_ROUTE,
            "attempts_total": transport.get("attempts_total", 0) + 1,
            "prompt_sha256": prompt_hash,
            "reference_sha256": reference_fingerprints(references),
            "request_file": str(request_file.relative_to(project_dir)),
            "reference_summary": summary,
            "prompt_summary": prompt_summary(prompt),
            "next_eligible_at": None,
            "active_attempt": {
                "attempt_id": attempt_id,
                "started_at": started_at,
                "probe": False,
                "prior_status": job["status"],
                "request_fingerprint": request_fingerprint(
                    BUILT_IN_BACKEND, DEFAULT_BUILT_IN_ROUTE, prompt_hash, references
                ),
                "reference_summary": summary,
                "runtime_budget_seconds": runtime_budget_seconds(references, None),
                "job_type": "reference",
            },
        }
    )
    job["status"] = "generating"
    batch["attempt_count"] = batch.get("attempt_count", 0) + 1
    batch["last_attempt_id"] = attempt_id
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "reference-preflight",
        "reference_id": args.reference_id,
        "attempt_id": attempt_id,
        "request_file": str(request_file),
        "references": reference_fingerprints(references),
        "reference_summary": summary,
        "reference_roles": roles,
        "warnings": warnings,
        "runtime_budget_seconds": runtime_budget_seconds(references, None),
        "batch_auto_started": batch_auto_started,
        "batch_id": batch["batch_id"],
        "input_differences": differences,
    }


def stage_automatic_reference_job_recovery(
    project_dir: Path,
    job: dict[str, Any],
    transport: dict[str, Any],
    error_type: str,
    active_attempt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if error_type not in {"timeout", "no_candidate"}:
        return None
    level = int(transport.get("auto_recovery_level", 0))
    if level >= AUTO_RECOVERY_LEVELS:
        return None
    current_path = project_dir / str(transport.get("request_file", ""))
    if not current_path.is_file():
        return None
    current = load_json(current_path)
    origin = _auto_recovery_origin(current)
    origin_refs = origin.get("references") or []
    origin_roles = origin.get("reference_roles") or []
    if len(origin_refs) not in {1, 2}:
        return None
    next_level = level + 1
    active_attempt = active_attempt or transport.get("active_attempt") or {}
    txid = transaction_id(str(active_attempt.get("attempt_id") or "unknown"), next_level)
    journal = transaction_path(project_dir, txid)
    atomic_write_json(
        journal,
        {
            "schema_version": 1,
            "transaction_id": txid,
            "job_type": "reference",
            "job_id": job["id"],
            "attempt_id": active_attempt.get("attempt_id"),
            "level": next_level,
            "state": "staging",
            "started_at": now_iso(),
        },
    )
    output_dir = project_dir / AUTO_REFERENCE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"reference-{job['id']}-auto-l{next_level}-{txid}.jpg"
    temporary = output.with_name(f".{output.stem}-{txid}{output.suffix}")
    sources = [Path(entry["path"]).expanduser().resolve() for entry in origin_refs]
    quality = 88 if next_level == 1 else 80
    try:
        if len(sources) == 2:
            canvas = (1024, 1280) if next_level == 1 else (768, 960)
            derivative = compose_reference_board(
                sources,
                [str(role) for role in origin_roles],
                [None, None],
                temporary,
                quality,
                canvas,
            )
            os.replace(temporary, output)
            os.replace(sidecar_path(temporary), sidecar_path(output))
            moved_sidecar = load_json(sidecar_path(output))
            moved_sidecar["output"]["path"] = str(output)
            atomic_write_json(sidecar_path(output), moved_sidecar)
            references = normalize_references([output])
            roles = ["single reference board: " + "; ".join(str(role) for role in origin_roles)]
            operation = "reference_board"
            parameters = {"canvas_size": list(canvas), "jpeg_quality": quality}
        else:
            max_edge = 1024 if next_level == 1 else 768
            derivative = optimize_reference(sources[0], temporary, None, max_edge, quality)
            os.replace(temporary, output)
            references = normalize_references([output])
            roles = [str(origin_roles[0])]
            operation = "resize_compress"
            parameters = {"max_edge": max_edge, "jpeg_quality": quality, "crop": None}
    except Exception:
        temporary.unlink(missing_ok=True)
        sidecar_path(temporary).unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        sidecar_path(output).unlink(missing_ok=True)
        atomic_write_json(
            journal,
            {
                "schema_version": 1,
                "transaction_id": txid,
                "job_type": "reference",
                "job_id": job["id"],
                "attempt_id": active_attempt.get("attempt_id"),
                "level": next_level,
                "state": "failed",
                "ended_at": now_iso(),
            },
        )
        raise
    revised = _reference_request(
        job["id"], current["prompt"], current["prompt_sha256"], references, roles
    )
    revised["auto_recovery_origin"] = origin
    revised["recovery_transaction_id"] = txid
    revised["auto_recovery"] = {
        "level": next_level,
        "operation": operation,
        "trigger": error_type,
        "parameters": parameters,
        "derivative": derivative,
        "staged_at": now_iso(),
    }
    archive = next_versioned_path(current_path.parent, f"{current_path.stem}-auto-superseded")
    try:
        atomic_write_json(archive, current)
        atomic_write_json(current_path, revised)
    except Exception:
        output.unlink(missing_ok=True)
        sidecar_path(output).unlink(missing_ok=True)
        atomic_write_json(
            journal,
            {
                "schema_version": 1,
                "transaction_id": txid,
                "job_type": "reference",
                "job_id": job["id"],
                "attempt_id": active_attempt.get("attempt_id"),
                "level": next_level,
                "state": "failed",
                "ended_at": now_iso(),
            },
        )
        raise
    record = {
        "transaction_id": txid,
        "level": next_level,
        "operation": operation,
        "trigger": error_type,
        "parameters": parameters,
        "archived_request": str(archive.relative_to(project_dir)),
        "derived_reference": str(output.relative_to(project_dir)),
        "derived_sha256": references[0]["sha256"],
        "origin_references": origin_refs,
        "next_references": reference_fingerprints(references),
        "next_reference_roles": roles,
        "staged_at": now_iso(),
    }
    transport["auto_recovery_level"] = next_level
    transport.setdefault("auto_recovery_history", []).append(record)
    transport["reference_sha256"] = reference_fingerprints(references)
    transport["reference_summary"] = reference_summary(references)
    transport["recovery"] = {
        "level": next_level,
        "state": "ready",
        "transaction": {"transaction_id": txid, "journal": str(journal.relative_to(project_dir))},
        "last_error": None,
    }
    atomic_write_json(
        journal,
        {
            "schema_version": 1,
            "transaction_id": txid,
            "job_type": "reference",
            "job_id": job["id"],
            "attempt_id": active_attempt.get("attempt_id"),
            "level": next_level,
            "state": "committed",
            "ended_at": now_iso(),
            "request_file": str(current_path.relative_to(project_dir)),
        },
    )
    return record


def record_reference_failure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    job = reference_job(payload, args.reference_id)
    if job.get("status") != "generating":
        raise StateError("reference job must be generating before recording failure")
    transport = job["transport"]
    transport_defaults(transport)
    active = transport.get("active_attempt") or {}
    failures = transport.get("consecutive_failures", 0) + 1
    ended_at = now_iso()
    supplied_elapsed = getattr(args, "elapsed_seconds", None)
    duration = elapsed_seconds(active.get("started_at"))
    if payload.get("schema_version") == 2 and supplied_elapsed is not None:
        duration = supplied_elapsed
    budget = int(active.get("runtime_budget_seconds", IMAGE_CALL_TIMEOUT_SECONDS))
    if payload.get("schema_version") == 3 and args.error_type == "timeout" and (
        duration is None or duration < budget
    ):
        raise StateError(
            f"timeout cannot be recorded before the persisted runtime budget; actual={duration}, budget={budget}"
        )
    can_recover = (
        args.error_type in {"timeout", "no_candidate"}
        and int(transport.get("auto_recovery_level", 0)) < AUTO_RECOVERY_LEVELS
        and request_origin_reference_count(project_dir, transport) in {1, 2}
    )
    blocked = failures >= SCENE_FAILURE_LIMIT and not can_recover
    append_attempt_history(
        transport,
        {
            "attempt_id": active.get("attempt_id"),
            "started_at": active.get("started_at"),
            "ended_at": ended_at,
            "outcome": "transport_failure",
            "error_type": args.error_type,
            "message": args.message,
            "elapsed_seconds": duration,
            "reported_elapsed_seconds": supplied_elapsed,
            "probe": False,
            "request_fingerprint": active.get("request_fingerprint"),
        },
    )
    cooldown = None if can_recover else COOLDOWN_SECONDS.get(failures)
    transport.update(
        {
            "consecutive_failures": failures,
            "last_error": args.message,
            "last_error_type": args.error_type,
            "last_failure_at": ended_at,
            "active_attempt": None,
            "reported_elapsed_seconds": supplied_elapsed,
            "circuit_open": blocked,
            "next_eligible_at": (
                (datetime.now(timezone.utc) + timedelta(seconds=cooldown)).isoformat()
                if cooldown
                else None
            ),
        }
    )
    _, backend_key = calculate_fingerprints(transport, args.error_type)
    backend = backend_state(payload, BUILT_IN_BACKEND)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=BACKEND_FAILURE_WINDOW_SECONDS)
    window = [
        event
        for event in backend.get("failure_window", [])
        if parse_iso(event.get("failed_at"))
        and parse_iso(event.get("failed_at")) >= cutoff
    ]
    event = backend_event("reference", args.reference_id, args.error_type, backend_key, ended_at)
    if payload.get("schema_version") == 2:
        event["job_key"] = f"reference:{args.reference_id}"
    window.append(event)
    backend["failure_window"] = window[-ATTEMPT_HISTORY_LIMIT:]
    affected_jobs = sorted(
        {
            event_job_key(event)
            for event in window
            if event.get("error_key") == backend_key
        }
    )
    if len(affected_jobs) >= BACKEND_SCENE_THRESHOLD:
        backend.update(
            {
                "health_warning": True,
                "reason": args.error_type,
                "error_key": backend_key,
                "affected_jobs": affected_jobs,
                "circuit_open": False,
            }
        )
    job["status"] = "transport_blocked" if blocked else "pending"
    if can_recover:
        next_level = int(transport.get("auto_recovery_level", 0)) + 1
        transport["recovery"] = {
            "level": int(transport.get("auto_recovery_level", 0)),
            "state": "staging",
            "transaction": {
                "transaction_id": transaction_id(str(active.get("attempt_id") or "unknown"), next_level),
                "attempt_id": active.get("attempt_id"),
                "next_level": next_level,
                "prior_status": active.get("prior_status"),
            },
            "last_error": None,
        }
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    recovery = None
    recovery_error = None
    if can_recover:
        try:
            recovery = stage_automatic_reference_job_recovery(
                project_dir, job, transport, args.error_type, active
            )
            job["status"] = "pending"
            atomic_write_json(state_path, payload)
            validate_state(state_path)
        except (StateError, OSError, ValueError) as exc:
            recovery_error = str(exc)
            job["status"] = "transport_blocked"
            transport["circuit_open"] = True
            transport["recovery"] = {
                "level": int(transport.get("auto_recovery_level", 0)),
                "state": "failed",
                "transaction": transport.get("recovery", {}).get("transaction"),
                "last_error": recovery_error,
            }
            atomic_write_json(state_path, payload)
            validate_state(state_path)
    return {
        "command": "record-reference-failure",
        "reference_id": args.reference_id,
        "status": job["status"],
        "auto_recovery": recovery,
        "retry_ready": bool(recovery),
        "recovery_error": recovery_error,
        "cooldown_until": transport.get("next_eligible_at"),
        "backend_health_warning": bool(backend.get("health_warning")),
    }


def record_reference_success(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    job = reference_job(payload, args.reference_id)
    if job.get("status") != "generating":
        raise StateError("reference job must be generating before recording success")
    candidate = args.candidate.expanduser().resolve()
    details = validate_image(candidate, "reference candidate")
    output_dir = (project_dir / str(job.get("output_dir", ""))).resolve()
    try:
        relative = str(candidate.relative_to(project_dir))
    except ValueError as exc:
        raise StateError("reference candidate must be inside the project") from exc
    try:
        candidate.relative_to(output_dir)
    except ValueError as exc:
        raise StateError(
            f"reference candidate must be inside its registered output_dir: {output_dir}"
        ) from exc
    transport = job["transport"]
    active = transport.get("active_attempt") or {}
    append_attempt_history(
        transport,
        {
            "attempt_id": active.get("attempt_id"),
            "started_at": active.get("started_at"),
            "ended_at": now_iso(),
            "outcome": "success",
            "candidate": relative,
            "probe": False,
            "request_fingerprint": active.get("request_fingerprint"),
        },
    )
    transport.update(
        {
            "consecutive_failures": 0,
            "last_error": None,
            "last_error_type": None,
            "circuit_open": False,
            "active_attempt": None,
            "next_eligible_at": None,
        }
    )
    backend = backend_state(payload, BUILT_IN_BACKEND)
    backend.update(
        {
            "failure_window": [],
            "health_warning": False,
            "reason": None,
            "error_key": None,
            "affected_jobs": [],
            "circuit_open": False,
            "last_success_at": now_iso(),
        }
    )
    job["candidate"] = relative
    job["status"] = "review_pending"
    if payload.get("schema_version") == 3:
        versions = job.setdefault("candidate_versions", [])
        if not any(entry.get("candidate") == relative for entry in versions):
            versions.append(
                {
                    "version": int(job.get("content_repair_count", 0)) + 1,
                    "candidate": relative,
                    "review": None,
                    "recorded_at": now_iso(),
                }
            )
        job["approved_candidate"] = None
    batch = batch_record(payload)
    if batch_is_active(batch):
        batch["success_count"] = batch.get("success_count", 0) + 1
        refresh_batch(batch)
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-reference-success",
        "reference_id": args.reference_id,
        "status": job["status"],
        "candidate": details,
    }


def materialize_reference_prompt(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    _, project_dir = require_v2(state_path)
    snapshot = load_json(reference_request_path(project_dir, args.reference_id))
    prompt = snapshot.get("prompt")
    expected = snapshot.get("prompt_sha256")
    if not isinstance(prompt, str) or sha256_bytes(prompt.encode("utf-8")) != expected:
        raise StateError("reference request prompt hash is invalid")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise StateError(f"materialized prompt output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(prompt.encode("utf-8"))
    return {
        "command": "materialize-reference-prompt",
        "reference_id": args.reference_id,
        "output": str(output),
        "prompt_sha256": expected,
    }


def prepare_blocked_report(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    active = []
    for job_type, job_id, item in phase_jobs(payload):
        runnable_statuses = {"generating", "review_pending" if job_type == "repair" else "pending"}
        if item.get("status") in runnable_statuses:
            active.append(f"{job_type} {job_id}")
    if active:
        raise StateError(
            "blocked report requires all runnable generation jobs to finish first: "
            + ", ".join(active)
        )
    blocked_images = [
        item for item in payload["images"] if item.get("status") == "transport_blocked"
    ]
    blocked_references = [
        job
        for job in payload.get("reference_jobs", [])
        if job.get("status") == "transport_blocked"
    ]
    if not blocked_images and not blocked_references:
        raise StateError("there are no transport-blocked jobs to report")
    output = args.output.expanduser().resolve()
    try:
        output.relative_to(project_dir)
    except ValueError as exc:
        raise StateError("blocked report must be written inside the project") from exc
    lines = [
        "# 生成阻塞报告",
        "",
        f"- 生成时间：{now_iso()}",
        "- 说明：以下任务已耗尽自动参考降级阶梯；其他可运行任务已继续完成。",
        "",
        f"- 当前阶段：{payload.get('phase')}",
        "",
        "| 类型 | 标识 | 恢复等级 | 最后错误 |",
        "|---|---|---:|---|",
    ]
    blocked_entries = [
        *(('正式分镜', str(item['number']), item) for item in blocked_images),
        *(('参考图', str(job['id']), job) for job in blocked_references),
    ]
    for kind, identifier, item in blocked_entries:
        transport = item["transport"]
        lines.append(
            f"| {kind} | {identifier} | {transport.get('auto_recovery_level', 0)} | "
            f"{markdown(transport.get('last_error') or '未返回候选')} |"
        )
    lines.append("")
    for kind, identifier, item in blocked_entries:
        transport = item["transport"]
        history = transport.get("auto_recovery_history", [])
        attempts = transport.get("attempt_history", [])
        lines.extend(
            [
                f"## {kind} {identifier}",
                "",
                f"- 恢复等级：{transport.get('auto_recovery_level', 0)}",
                f"- 最后错误：{markdown(transport.get('last_error') or '未返回候选')}",
                f"- 当前请求：{markdown(transport.get('request_file'))}",
                f"- 恢复状态：{markdown(transport.get('recovery', {}).get('state'))}",
                "",
                "### 输入版本",
                "",
                "| 档位 | 事务 | 操作与参数 | 来源角色 | 来源哈希 | 派生文件与哈希 | 触发原因 |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        if not history:
            source_hashes = "、".join(entry.get("sha256", "") for entry in transport.get("reference_sha256", [])) or "零参考"
            lines.append(f"| 0 | — | 原始输入 | — | {markdown(source_hashes)} | — | — |")
        for record in history:
            roles = "；".join(str(value) for value in record.get("next_reference_roles", []))
            origins = "、".join(str(value.get("sha256", "")) for value in record.get("origin_references", []))
            derived_paths = record.get("derived_references") or [record.get("derived_reference")]
            derived_hashes = record.get("derived_sha256_all") or [record.get("derived_sha256")]
            derived = "；".join(f"{path} ({digest})" for path, digest in zip(derived_paths, derived_hashes))
            lines.append(
                f"| {record.get('level')} | {markdown(record.get('transaction_id'))} | "
                f"{markdown(record.get('operation'))} {markdown(record.get('parameters'))} | "
                f"{markdown(roles)} | {markdown(origins)} | {markdown(derived)} | {markdown(record.get('trigger'))} |"
            )
        lines.extend(
            [
                "",
                "### 失败历史",
                "",
                "| 开始 | 结束 | 实际耗时 | 报告耗时 | 类型 | 消息 | 请求指纹 |",
                "|---|---|---:|---:|---|---|---|",
            ]
        )
        for attempt in attempts:
            if attempt.get("outcome") not in {"transport_failure", "interrupted"}:
                continue
            lines.append(
                f"| {markdown(attempt.get('started_at'))} | {markdown(attempt.get('ended_at'))} | "
                f"{markdown(attempt.get('elapsed_seconds'))} | {markdown(attempt.get('reported_elapsed_seconds'))} | "
                f"{markdown(attempt.get('error_type') or attempt.get('outcome'))} | "
                f"{markdown(attempt.get('message'))} | {markdown(attempt.get('request_fingerprint'))} |"
            )
        lines.extend(["", "- 建议：核对来源文件、身份锚点和服务状态后，再由人工决定是否重开。", ""])
    lines.append("")
    atomic_write_text(output, "\n".join(lines))
    payload.setdefault("artifacts", {})["blocked_report"] = str(output.relative_to(project_dir))
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "prepare-blocked-report",
        "output": str(output),
        "blocked_images": [item["number"] for item in blocked_images],
        "blocked_references": [job["id"] for job in blocked_references],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pre = subparsers.add_parser(
        "preflight", help="Validate and persist one stable generation request"
    )
    pre.add_argument("--state", required=True, type=Path)
    pre.add_argument("--number", required=True, type=int)
    pre.add_argument(
        "--backend",
        choices=(BUILT_IN_BACKEND, *FALLBACK_BACKENDS),
        default=BUILT_IN_BACKEND,
    )
    pre.add_argument("--route")
    pre.add_argument("--model")
    pre.add_argument("--prompt-file", required=True, type=Path)
    pre.add_argument("--reference", action="append", default=[], type=Path)
    pre.add_argument(
        "--reference-role",
        action="append",
        default=[],
        help="Human-readable identity/purpose for the matching --reference",
    )
    pre.add_argument("--repair-mode", choices=("edit", "regenerate"))
    pre.add_argument(
        "--allow-high-reference-count",
        action="store_true",
        help="Allow more than two references after explicit user approval",
    )

    failure = subparsers.add_parser(
        "record-failure", help="Record one transport failure"
    )
    failure.add_argument("--state", required=True, type=Path)
    failure.add_argument("--number", required=True, type=int)
    failure.add_argument("--error-type", required=True, choices=sorted(TRANSPORT_ERRORS))
    failure.add_argument("--message", required=True)
    failure.add_argument("--elapsed-seconds", type=float)

    probe = subparsers.add_parser(
        "resume-probe", help="Grant one probe after a scene or backend circuit opens"
    )
    probe.add_argument("--state", required=True, type=Path)
    probe.add_argument("--number", required=True, type=int)
    probe.add_argument("--user-approved", action="store_true")

    success = subparsers.add_parser(
        "record-success", help="Record a returned candidate and enter content review"
    )
    success.add_argument("--state", required=True, type=Path)
    success.add_argument("--number", required=True, type=int)
    success.add_argument("--candidate", required=True, type=Path)
    success.add_argument("--elapsed-seconds", type=float)

    invalid_aspect = subparsers.add_parser(
        "invalidate-candidate-aspect",
        help="Archive an accepted non-4:5 candidate and reopen the scene",
    )
    invalid_aspect.add_argument("--state", required=True, type=Path)
    invalid_aspect.add_argument("--number", required=True, type=int)
    invalid_aspect.add_argument("--archive", required=True, type=Path)
    invalid_aspect.add_argument("--user-approved", action="store_true")

    interrupted = subparsers.add_parser(
        "recover-interrupted",
        help="Close an interrupted in-flight attempt after confirming no candidate",
    )
    interrupted.add_argument("--state", required=True, type=Path)
    interrupted_target = interrupted.add_mutually_exclusive_group(required=True)
    interrupted_target.add_argument("--number", type=int)
    interrupted_target.add_argument("--reference-id")
    interrupted.add_argument("--confirm-no-candidate", action="store_true")
    interrupted.add_argument(
        "--reason",
        required=True,
        choices=("user_abort", "turn_interrupted", "tool_timeout"),
    )

    start = subparsers.add_parser(
        "batch-start", help="Start one bounded serial generation batch"
    )
    start.add_argument("--state", required=True, type=Path)

    status = subparsers.add_parser(
        "batch-status", help="Report safe next actions for the generation queue"
    )
    status.add_argument("--state", required=True, type=Path)

    materialize = subparsers.add_parser(
        "materialize-prompt",
        help="Write a byte-exact prompt file from an approved request snapshot",
    )
    materialize.add_argument("--state", required=True, type=Path)
    materialize.add_argument("--number", required=True, type=int)
    materialize.add_argument("--output", required=True, type=Path)
    materialize.add_argument("--repair-mode", action="store_true")

    supersede = subparsers.add_parser(
        "supersede-repair",
        help="Archive a failed repair snapshot before an approved input revision",
    )
    supersede.add_argument("--state", required=True, type=Path)
    supersede.add_argument("--number", required=True, type=int)
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--user-approved", action="store_true")

    revise = subparsers.add_parser(
        "revise-request",
        help="Archive and replace a failed original request after user-approved input optimization",
    )
    revise.add_argument("--state", required=True, type=Path)
    revise.add_argument("--number", required=True, type=int)
    revise.add_argument("--prompt-file", required=True, type=Path)
    revise.add_argument("--reference", action="append", default=[], type=Path)
    revise.add_argument("--reference-role", action="append", default=[])
    revise.add_argument("--reason", required=True)
    revise.add_argument("--user-approved", action="store_true")
    revise.add_argument("--allow-high-reference-count", action="store_true")

    board_policy = subparsers.add_parser(
        "authorize-reference-board-policy",
        help="Authorize the two-reference timeout to one-board fallback policy",
    )
    board_policy.add_argument("--state", required=True, type=Path)
    board_policy.add_argument(
        "--timeout-seconds", required=True, type=int
    )
    board_policy.add_argument("--user-approved", action="store_true")

    board_stage = subparsers.add_parser(
        "stage-reference-board-fallback",
        help="Replace an eligible timed-out two-reference request with one board",
    )
    board_stage.add_argument("--state", required=True, type=Path)
    board_stage.add_argument("--number", required=True, type=int)
    board_stage.add_argument("--reference-board", required=True, type=Path)
    board_stage.add_argument("--confirm-no-candidate", action="store_true")

    fallback = subparsers.add_parser(
        "authorize-fallback", help="Authorize one project-level CLI/API fallback"
    )
    fallback.add_argument("--state", required=True, type=Path)
    fallback.add_argument("--backend", choices=FALLBACK_BACKENDS, required=True)
    fallback.add_argument("--model", required=True)
    fallback.add_argument("--user-approved", action="store_true")

    reference_pre = subparsers.add_parser(
        "reference-preflight",
        help="Validate and persist one registered reference-asset request",
    )
    reference_pre.add_argument("--state", required=True, type=Path)
    reference_pre.add_argument("--reference-id", required=True)
    reference_pre.add_argument("--prompt-file", required=True, type=Path)
    reference_pre.add_argument("--reference", action="append", default=[], type=Path)
    reference_pre.add_argument("--reference-role", action="append", default=[])

    reference_failure = subparsers.add_parser(
        "record-reference-failure",
        help="Record a reference-asset transport failure and stage automatic recovery",
    )
    reference_failure.add_argument("--state", required=True, type=Path)
    reference_failure.add_argument("--reference-id", required=True)
    reference_failure.add_argument(
        "--error-type", required=True, choices=sorted(TRANSPORT_ERRORS)
    )
    reference_failure.add_argument("--message", required=True)
    reference_failure.add_argument("--elapsed-seconds", type=float)

    reference_success = subparsers.add_parser(
        "record-reference-success",
        help="Record a returned reference asset and enter reference review",
    )
    reference_success.add_argument("--state", required=True, type=Path)
    reference_success.add_argument("--reference-id", required=True)
    reference_success.add_argument("--candidate", required=True, type=Path)

    reference_materialize = subparsers.add_parser(
        "materialize-reference-prompt",
        help="Write a byte-exact prompt from a reference request snapshot",
    )
    reference_materialize.add_argument("--state", required=True, type=Path)
    reference_materialize.add_argument("--reference-id", required=True)
    reference_materialize.add_argument("--output", required=True, type=Path)
    blocked_report = subparsers.add_parser(
        "prepare-blocked-report",
        help="Write one report after every runnable generation job has finished",
    )
    blocked_report.add_argument("--state", required=True, type=Path)
    blocked_report.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        number = getattr(args, "number", None)
        if number is not None and number < 1:
            raise StateError("--number must be a positive integer")
        duration = getattr(args, "elapsed_seconds", None)
        if duration is not None and duration < 0:
            raise StateError("--elapsed-seconds must be non-negative")
        handlers = {
            "preflight": preflight,
            "record-failure": record_failure,
            "resume-probe": resume_probe,
            "record-success": record_success,
            "invalidate-candidate-aspect": invalidate_candidate_aspect,
            "recover-interrupted": recover_interrupted,
            "batch-start": batch_start,
            "batch-status": batch_status,
            "materialize-prompt": materialize_prompt,
            "supersede-repair": supersede_repair,
            "revise-request": revise_request,
            "authorize-reference-board-policy": authorize_reference_board_policy,
            "stage-reference-board-fallback": stage_reference_board_fallback,
            "authorize-fallback": authorize_fallback,
            "reference-preflight": reference_preflight,
            "record-reference-failure": record_reference_failure,
            "record-reference-success": record_reference_success,
            "materialize-reference-prompt": materialize_reference_prompt,
            "prepare-blocked-report": prepare_blocked_report,
        }
        result = handlers[args.command](args)
    except (StateError, UnicodeDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
