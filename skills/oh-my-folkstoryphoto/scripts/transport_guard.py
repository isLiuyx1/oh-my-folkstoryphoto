#!/usr/bin/env python3
"""Persist image-generation preflight, retry, circuit, and fallback state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
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
    load_json,
    resolve_project_dir,
    validate_state,
)


BUILT_IN_BACKEND = "built_in_imagegen"
FALLBACK_BACKEND = "cli_api"
DEFAULT_BUILT_IN_ROUTE = "chatgpt.com/backend-api/codex/images"
TRANSPORT_ERRORS = {"network_error", "timeout", "no_candidate"}
SCENE_FAILURE_LIMIT = 3
BACKEND_SCENE_THRESHOLD = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    }


def require_v2(state_path: Path) -> tuple[dict[str, Any], Path]:
    validate_state(state_path)
    payload = load_json(state_path)
    if payload.get("schema_version") != 2:
        raise StateError(
            "transport_guard requires schema_version 2; migrate the selected "
            "unfinished project first"
        )
    project_dir = resolve_project_dir(state_path, payload.get("project_dir"))
    return payload, project_dir


def image_item(payload: dict[str, Any], number: int) -> dict[str, Any]:
    for item in payload["images"]:
        if item.get("number") == number:
            return item
    raise StateError(f"image {number} is not present in planned images")


def request_path(project_dir: Path, number: int) -> Path:
    return project_dir / "生成请求" / f"{number:02d}.json"


def fallback_request_path(project_dir: Path, number: int) -> Path:
    return project_dir / "生成请求" / f"{number:02d}-fallback-{FALLBACK_BACKEND}.json"


def normalize_references(raw_paths: list[Path]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for index, raw_path in enumerate(raw_paths):
        path = raw_path.expanduser().resolve()
        if path in seen:
            raise StateError(f"duplicate reference path: {path}")
        seen.add(path)
        normalized.append(validate_image(path, f"references[{index}]"))
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
        "references": references,
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
    return backends.setdefault(
        backend,
        {
            "circuit_open": False,
            "reason": None,
            "error_key": None,
            "affected_images": [],
            "opened_at": None,
        },
    )


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, project_dir = require_v2(state_path)
    item = image_item(payload, args.number)
    transport = item["transport"]
    prompt, prompt_hash = prompt_payload(args.prompt_file)
    references = normalize_references(args.reference)
    route = args.route or (
        DEFAULT_BUILT_IN_ROUTE if args.backend == BUILT_IN_BACKEND else FALLBACK_BACKEND
    )
    model = args.model

    if item["status"] in {"pass", "review_pending", "needs_user"}:
        raise StateError(
            f"image {args.number} status {item['status']} is not eligible for generation"
        )
    if item["status"] == "generating":
        raise StateError(f"image {args.number} already has a generation in flight")

    same_backend = transport.get("backend") == args.backend
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

    base_path = request_path(project_dir, args.number)
    current = create_request(
        args.number,
        args.backend,
        route,
        model,
        prompt,
        prompt_hash,
        references,
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
        request_file = fallback_request_path(project_dir, args.number)
        if request_file.exists():
            existing = load_json(request_file)
            if request_core(existing) != request_core(current):
                raise StateError("fallback request drift detected")
        else:
            atomic_write_json(request_file, current)

    previous_backend = transport.get("backend")
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
    transport.update(
        {
            "backend": args.backend,
            "route": route,
            "attempts_total": transport["attempts_total"] + 1,
            "prompt_sha256": prompt_hash,
            "reference_sha256": reference_fingerprints(references),
            "probe_granted": False,
            "probe_in_flight": is_probe,
            "circuit_open": False,
            "request_file": str(request_file.relative_to(project_dir)),
            "model": model,
            "input_differences": differences,
        }
    )
    item["status"] = "generating"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "preflight",
        "image_number": args.number,
        "backend": args.backend,
        "attempt_number": transport["attempts_total"],
        "probe": is_probe,
        "request_file": str(request_file),
        "prompt_sha256": prompt_hash,
        "references": reference_fingerprints(references),
        "input_differences": differences,
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


def record_failure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
    item = image_item(payload, args.number)
    if item["status"] != "generating":
        raise StateError(
            f"image {args.number} must be generating before recording a failure"
        )
    transport = item["transport"]
    if args.error_type not in TRANSPORT_ERRORS:
        raise StateError(f"unsupported transport error type: {args.error_type}")
    detailed, backend_key = calculate_fingerprints(transport, args.error_type)
    was_probe = transport.get("probe_in_flight", False)
    transport.update(
        {
            "consecutive_failures": transport["consecutive_failures"] + 1,
            "last_error": args.message,
            "last_error_type": args.error_type,
            "error_fingerprint": detailed,
            "backend_error_key": backend_key,
            "last_failure_at": now_iso(),
            "probe_in_flight": False,
        }
    )
    scene_blocked = was_probe or (
        transport["consecutive_failures"] >= SCENE_FAILURE_LIMIT
    )
    transport["circuit_open"] = scene_blocked
    item["status"] = "transport_blocked" if scene_blocked else "pending"

    backend = transport["backend"]
    affected = sorted(
        candidate["number"]
        for candidate in payload["images"]
        if candidate["transport"].get("backend") == backend
        and candidate["transport"].get("backend_error_key") == backend_key
        and candidate["transport"].get("last_error_type") == args.error_type
    )
    backend_record = backend_state(payload, backend)
    backend_blocked = len(set(affected)) >= BACKEND_SCENE_THRESHOLD
    if backend_blocked:
        backend_record.update(
            {
                "circuit_open": True,
                "reason": args.error_type,
                "error_key": backend_key,
                "affected_images": affected,
                "opened_at": now_iso(),
            }
        )

    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-failure",
        "image_number": args.number,
        "consecutive_failures": transport["consecutive_failures"],
        "scene_circuit_open": scene_blocked,
        "backend_circuit_open": backend_blocked,
        "affected_images": affected,
        "error_fingerprint": detailed,
    }


def resume_probe(args: argparse.Namespace) -> dict[str, Any]:
    if not args.user_approved:
        raise StateError("resume-probe requires explicit --user-approved")
    state_path = args.state.expanduser().resolve()
    payload, _ = require_v2(state_path)
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
    try:
        candidate_relative = str(candidate.relative_to(project_dir))
    except ValueError as exc:
        raise StateError("candidate must be copied into the project directory") from exc

    transport = item["transport"]
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
            "last_success_at": now_iso(),
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
        }
    )
    item["candidate"] = candidate_relative
    item["status"] = "review_pending"
    atomic_write_json(state_path, payload)
    validate_state(state_path)
    return {
        "command": "record-success",
        "image_number": args.number,
        "status": "review_pending",
        "candidate": candidate_details,
        "backend_circuit_open": False,
    }


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
        choices=(BUILT_IN_BACKEND, FALLBACK_BACKEND),
        default=BUILT_IN_BACKEND,
    )
    pre.add_argument("--route")
    pre.add_argument("--model")
    pre.add_argument("--prompt-file", required=True, type=Path)
    pre.add_argument("--reference", action="append", default=[], type=Path)

    failure = subparsers.add_parser(
        "record-failure", help="Record one transport failure"
    )
    failure.add_argument("--state", required=True, type=Path)
    failure.add_argument("--number", required=True, type=int)
    failure.add_argument("--error-type", required=True, choices=sorted(TRANSPORT_ERRORS))
    failure.add_argument("--message", required=True)

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

    fallback = subparsers.add_parser(
        "authorize-fallback", help="Authorize one project-level CLI/API fallback"
    )
    fallback.add_argument("--state", required=True, type=Path)
    fallback.add_argument("--backend", choices=(FALLBACK_BACKEND,), required=True)
    fallback.add_argument("--model", required=True)
    fallback.add_argument("--user-approved", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        number = getattr(args, "number", None)
        if number is not None and number < 1:
            raise StateError("--number must be a positive integer")
        handlers = {
            "preflight": preflight,
            "record-failure": record_failure,
            "resume-probe": resume_probe,
            "record-success": record_success,
            "authorize-fallback": authorize_fallback,
        }
        result = handlers[args.command](args)
    except (StateError, UnicodeDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
