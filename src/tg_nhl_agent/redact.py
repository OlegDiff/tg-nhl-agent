from __future__ import annotations


def mask_secret(value: str | None, keep_last: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep_last:
        return "<masked>"
    return "<masked>..." + value[-keep_last:]
