#!/usr/bin/env python3
"""Shared helpers for validating Vitis Python operation results."""

from __future__ import annotations


_SUCCESS_TEXT = frozenset({"0", "ok", "success", "succeeded", "true"})


def require_vitis_success(operation: str, status: object) -> None:
    """Raise when a Vitis operation reports a non-success status."""

    if status is None:
        return
    if isinstance(status, bool):
        success = status
    elif isinstance(status, int):
        success = status == 0
    else:
        success = str(status).strip().lower() in _SUCCESS_TEXT

    if not success:
        raise RuntimeError(f"{operation} failed (status {status!r})")
