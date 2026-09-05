#!/usr/bin/env python3

"""Expose quiet gen-machineconf Tinfoil task counts to the mnc summary."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import runpy
import sys

import build_events


@contextmanager
def report_task_progress(tinfoil_class, runqueue):
    """Attach the public Tinfoil callback without changing vendor source files."""
    original = tinfoil_class.build_targets
    stage = os.environ.get("MNC_STAGE_NAME", "mconf")

    @wraps(original)
    def build_targets(self, targets, task=None, handle_events=True,
                      extra_events=None, event_callback=None):
        if not handle_events:
            return original(self, targets, task, handle_events,
                            extra_events, event_callback)

        def callback(event):
            # Setscene has a separate total and may interleave with normal
            # tasks. Do not mix its counters into the main task percentage.
            if isinstance(event, runqueue.runQueueTaskStarted):
                stats = event.stats
                current = stats.completed + stats.active + stats.failed + 1
                if 0 < current <= stats.total:
                    build_events.emit(
                        "progress", stage, min(99, current * 100 // stats.total),
                        f"BitBake tasks {current}/{stats.total}",
                    )
            # Preserve all existing handling, especially task failures and
            # cancellation. Returning true would consume the event in Tinfoil.
            return event_callback(event) if event_callback else False

        label = targets if isinstance(targets, str) else " ".join(targets)
        build_events.emit("progress", stage, None, f"BitBake: {label}")
        try:
            return original(self, targets, task, handle_events, extra_events, callback)
        finally:
            build_events.emit("progress", stage, None, "generating machine configuration")

    tinfoil_class.build_targets = build_targets
    try:
        yield
    finally:
        tinfoil_class.build_targets = original


def main() -> None:
    script = Path(sys.argv[1]).resolve()
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))

    import bb.runqueue
    import bb.tinfoil

    with report_task_progress(bb.tinfoil.Tinfoil, bb.runqueue):
        runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
