#!/usr/bin/env python3
"""Safely sync the canonical workspace source into a local Codex install."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_NAME = "oh-my-folkstoryphoto"
REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCE = REPO_ROOT / "skills" / PLUGIN_NAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("skill", "plugin"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--target-root",
        type=Path,
        help="Override install root for testing or a custom Codex home.",
    )
    return parser.parse_args()


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def resolve_paths(mode: str, target_root: Path | None) -> tuple[Path, Path, Path | None]:
    if mode == "skill":
        root = (
            target_root.expanduser().resolve()
            if target_root
            else Path.home() / ".codex" / "skills"
        )
        return SKILL_SOURCE, root / PLUGIN_NAME, None
    root = (
        target_root.expanduser().resolve()
        if target_root
        else Path.home() / ".agents" / "plugins"
    )
    return REPO_ROOT, root / "plugins" / PLUGIN_NAME, root / "marketplace.json"


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".DS_Store", ".git"}
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def build_marketplace(existing: Any) -> dict[str, Any]:
    if existing is None:
        payload: dict[str, Any] = {
            "name": "personal",
            "interface": {"displayName": "Personal"},
            "plugins": [],
        }
    elif isinstance(existing, dict):
        payload = existing
    else:
        raise ValueError("marketplace.json must contain a JSON object")

    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("marketplace.json plugins must be an array")
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Creativity",
    }
    payload["plugins"] = [
        item
        for item in plugins
        if not isinstance(item, dict) or item.get("name") != PLUGIN_NAME
    ] + [entry]
    payload.setdefault("name", "personal")
    payload.setdefault("interface", {"displayName": "Personal"})
    return payload


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


def sync_tree(source: Path, destination: Path) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}-staging-{timestamp()}"
    shutil.copytree(source, staging, ignore=copy_ignore)
    backup: Path | None = None
    try:
        if destination.exists():
            backup = destination.parent / f"{destination.name}.backup-{timestamp()}"
            destination.replace(backup)
        staging.replace(destination)
    except Exception:
        if backup and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return backup


def main() -> int:
    args = parse_args()
    source, destination, marketplace = resolve_paths(args.mode, args.target_root)
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")

    plan = {
        "mode": args.mode,
        "source": str(source),
        "destination": str(destination),
        "destination_exists": destination.exists(),
        "marketplace": str(marketplace) if marketplace else None,
        "action": "dry-run" if args.dry_run else "sync",
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    backup = sync_tree(source, destination)
    marketplace_backup: Path | None = None
    if marketplace:
        existing = None
        if marketplace.exists():
            existing = json.loads(marketplace.read_text(encoding="utf-8"))
            marketplace_backup = marketplace.with_name(
                f"{marketplace.name}.backup-{timestamp()}"
            )
            shutil.copy2(marketplace, marketplace_backup)
        atomic_write_json(marketplace, build_marketplace(existing))

    print(
        json.dumps(
            {
                "installed": str(destination),
                "backup": str(backup) if backup else None,
                "marketplace_backup": (
                    str(marketplace_backup) if marketplace_backup else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
