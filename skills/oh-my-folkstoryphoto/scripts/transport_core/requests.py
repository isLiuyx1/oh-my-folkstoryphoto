"""Small request-snapshot helpers kept separate from the CLI facade."""

from __future__ import annotations

from pathlib import Path


def transaction_path(project_dir: Path, transaction_id: str) -> Path:
    return project_dir / "生成请求" / "恢复事务" / f"{transaction_id}.json"


def temporary_derivative_path(final_path: Path, transaction_id: str) -> Path:
    return final_path.with_name(f".{final_path.stem}-{transaction_id}{final_path.suffix}")

