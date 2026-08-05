#!/usr/bin/env python3
"""Generate one image through a fresh ChatGPT-authenticated Codex CLI task."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class BridgeError(RuntimeError):
    """Raised when the subscription bridge cannot return a valid candidate."""


def image_header_is_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 16:
        return False
    header = path.read_bytes()[:16]
    return header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff")


def build_worker_prompt(prompt: str, reference_names: list[str], size: str) -> str:
    references = (
        "\n".join(
            f"- Attached image {index}: required reference {name}; preserve only the "
            "identity, clothing, object, or spatial facts assigned to it by the scene prompt."
            for index, name in enumerate(reference_names, start=1)
        )
        if reference_names
        else "- No reference images are attached."
    )
    return f"""You are a one-shot image-generation worker. Call the image_generation tool exactly once.

The attached images, if any, are required references in this order:
{references}

Pass the scene specification below to the image-generation tool without summarizing,
rewriting, weakening, or adding narrative elements. Use the attached images as image
references, not merely as prose inspiration. Request an output size of {size}.

<exact-scene-specification>
{prompt}
</exact-scene-specification>

After the tool returns, save or copy the generated candidate to ./out.png inside the
current working directory. Do not fabricate an image with code. Do not return an image
from another task or cache. Reply with only out.png after the file exists.
"""


def run_bridge(args: argparse.Namespace) -> dict[str, object]:
    prompt_path = args.prompt_file.expanduser().resolve()
    if not prompt_path.is_file():
        raise BridgeError(f"prompt file does not exist: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise BridgeError("prompt file is empty")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise BridgeError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.log.expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    references = [path.expanduser().resolve() for path in args.reference]
    if len(references) > 2:
        raise BridgeError("subscription bridge accepts at most two references")
    for path in references:
        if not image_header_is_valid(path):
            raise BridgeError(f"reference is not a readable PNG/JPEG: {path}")
    codex = Path(args.codex).expanduser().resolve() if args.codex else Path(
        shutil.which("codex") or ""
    )
    if not codex.is_file():
        raise BridgeError("codex CLI is not installed or not on PATH")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="folkstory-subscription-bridge-") as raw_dir:
        workdir = Path(raw_dir)
        copied_references: list[Path] = []
        for index, source in enumerate(references, start=1):
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg"} else ".png"
            target = workdir / f"reference-{index:02d}{suffix}"
            shutil.copy2(source, target)
            copied_references.append(target)
        worker_prompt = build_worker_prompt(
            prompt, [path.name for path in copied_references], args.size
        )
        command = [
            str(codex),
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--ignore-user-config",
            "--enable",
            "image_generation",
            "-c",
            'model_reasoning_effort="low"',
            "-s",
            "workspace-write",
            "-C",
            str(workdir),
            "--json",
        ]
        for reference in copied_references:
            command.extend(["-i", str(reference)])
        command.append("-")
        try:
            with log_path.open("w", encoding="utf-8") as log_handle:
                completed = subprocess.run(
                    command,
                    input=worker_prompt,
                    text=True,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    timeout=args.timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(
                f"subscription bridge exceeded {args.timeout_seconds} seconds; log: {log_path}"
            ) from exc
        candidates = [
            path
            for path in workdir.glob("*.png")
            if path.name != "out.png" and not path.name.startswith("reference-")
        ]
        candidate = workdir / "out.png"
        if not image_header_is_valid(candidate) and candidates:
            candidate = max(candidates, key=lambda path: path.stat().st_mtime)
        if completed.returncode != 0 or not image_header_is_valid(candidate):
            raise BridgeError(
                f"codex exec returned {completed.returncode} without a valid candidate; log: {log_path}"
            )
        staged = output.with_name(f".{output.name}.partial")
        shutil.copy2(candidate, staged)
        os.replace(staged, output)
    return {
        "ok": True,
        "backend": "codex_subscription_bridge",
        "model": "gpt-image-2",
        "output": str(output),
        "log": str(log_path),
        "references": [str(path) for path in references],
        "size": args.size,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--reference", action="append", default=[], type=Path)
    parser.add_argument("--size", default="1024x1280")
    parser.add_argument("--timeout-seconds", type=int, default=480)
    parser.add_argument("--codex")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout_seconds < 60 or args.timeout_seconds > 600:
        print(json.dumps({"ok": False, "error": "timeout must be 60-600 seconds"}))
        return 2
    try:
        result = run_bridge(args)
    except (BridgeError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
