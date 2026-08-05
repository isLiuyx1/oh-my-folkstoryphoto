"""Phase-aware task selection and normalized backend events."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def eligible_job_type(phase: str) -> str | None:
    return {
        "reference_self_review": "reference",
        "scene_self_review": "scene",
        "repairing": "repair",
    }.get(phase)


def phase_jobs(payload: dict[str, Any]) -> Iterable[tuple[str, str | int, dict[str, Any]]]:
    kind = eligible_job_type(str(payload.get("phase")))
    if kind == "reference":
        for job in payload.get("reference_jobs", []):
            yield "reference", str(job.get("id")), job
    elif kind in {"scene", "repair"}:
        approved = set(payload.get("repair_policy", {}).get("approved_numbers", []))
        for item in payload.get("images", []):
            if kind == "repair" and item.get("number") not in approved:
                continue
            yield kind, int(item.get("number")), item


def backend_event(
    job_type: str,
    job_id: str | int,
    error_type: str,
    error_key: str,
    failed_at: str,
) -> dict[str, Any]:
    return {
        "job_type": job_type,
        "job_id": job_id,
        "error_type": error_type,
        "error_key": error_key,
        "failed_at": failed_at,
    }


def event_job_key(event: dict[str, Any]) -> str:
    if event.get("job_type") and event.get("job_id") is not None:
        return f"{event['job_type']}:{event['job_id']}"
    if event.get("job_key"):
        return str(event["job_key"])
    return f"scene:{event.get('image_number')}"
