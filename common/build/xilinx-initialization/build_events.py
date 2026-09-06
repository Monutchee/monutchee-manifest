#!/usr/bin/env python3

"""Best-effort progress events shared by Vitis-hosted Python builders."""

from __future__ import annotations

import os


def emit(kind: str, stage: str, percent: int | None, message: str) -> None:
    value = os.environ.get("MNC_EVENT_FD", "")
    if not value.isdigit():
        return
    fields = (kind, stage, "" if percent is None else str(percent), message)
    line = "MNC_EVENT\t" + "\t".join(
        field.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        for field in fields
    ) + "\n"
    try:
        os.write(int(value), line.encode("utf-8", errors="replace"))
    except OSError:
        pass


def progress(stage: str, completed: int, total: int, message: str) -> None:
    percent = None if total <= 0 else max(0, min(100, completed * 100 // total))
    emit("progress", stage, percent, message)
