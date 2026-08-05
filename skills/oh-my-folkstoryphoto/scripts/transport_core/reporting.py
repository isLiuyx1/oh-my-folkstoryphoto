"""Formatting helpers for transport reports."""

from __future__ import annotations


def markdown(value: object) -> str:
    return str(value if value is not None else "—").replace("|", "｜").replace("\n", " ")

