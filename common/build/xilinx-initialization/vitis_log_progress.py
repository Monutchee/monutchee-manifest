#!/usr/bin/env python3

"""Follow new Vitis log records and surface concise RPU build progress."""

from __future__ import annotations

import argparse
import re
import signal
import threading
import time
from pathlib import Path

from build_events import emit


LOG_PREFIX = re.compile(r"^\d{2}:\d{2}:\d{2}\s+[A-Z]+\s+:\s*")
PHASES = (
    (re.compile(r"Platform .* creation started", re.IGNORECASE), 20,
     "creating platform from XSA"),
    (re.compile(r"SDT generated successfully", re.IGNORECASE), 25,
     "hardware SDT generated"),
    (re.compile(r"Successfully created Domain .*psu_cortexr5_0", re.IGNORECASE),
     30, "R5 core 0 domain created"),
    (re.compile(r"Successfully created Domain .*psu_cortexr5_1", re.IGNORECASE),
     40, "R5 core 1 domain created"),
    (re.compile(r"Successfully added libmetal", re.IGNORECASE), 43,
     "configuring libmetal"),
    (re.compile(r"Successfully added openamp", re.IGNORECASE), 47,
     "configuring OpenAMP"),
    (re.compile(r"Successfully added xilmailbox", re.IGNORECASE), 52,
     "configuring Xilinx mailbox"),
    (re.compile(r"Platform Build Finished successfully", re.IGNORECASE), 65,
     "platform build complete"),
    (re.compile(r"Initiating build for the R5c0 component", re.IGNORECASE), 75,
     "building R5 core 0 application"),
    (re.compile(r"Initiating build for the R5c1 component", re.IGNORECASE), 85,
     "building R5 core 1 application"),
)
ERROR = re.compile(r"\b(?:ERROR|FATAL|EXCEPTION|TRACEBACK|FAILED)\b", re.IGNORECASE)


def classify_line(line: str) -> tuple[int | None, str] | None:
    """Return a progress milestone or important diagnostic for one log line."""
    message = LOG_PREFIX.sub("", line.strip())
    if not message:
        return None
    for pattern, percent, description in PHASES:
        if pattern.search(message):
            return percent, description
    if ERROR.search(message):
        return None, message
    return None


class ProgressFollower:
    def __init__(self, path: Path, stage: str, offset: int, poll: float):
        self.path = path
        self.stage = stage
        self.position = max(0, offset)
        self.poll = poll
        self.pending = b""
        self.maximum_percent = 0
        self.stop = threading.Event()

    def process(self, line: str) -> None:
        update = classify_line(line)
        if update is None:
            return
        percent, message = update
        if percent is not None:
            self.maximum_percent = max(self.maximum_percent, percent)
            percent = self.maximum_percent
            emit("progress", self.stage, percent, message)
        print(f"[vitis] {message}", flush=True)

    def drain(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.position:
            self.position = 0
            self.pending = b""
        if size == self.position:
            return
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.position)
                data = stream.read()
                self.position = stream.tell()
        except OSError:
            return
        records = (self.pending + data).split(b"\n")
        self.pending = records.pop()
        for record in records:
            self.process(record.decode("utf-8", errors="replace"))

    def run(self) -> None:
        while not self.stop.wait(self.poll):
            self.drain()
        self.drain()
        if self.pending:
            self.process(self.pending.decode("utf-8", errors="replace"))
            self.pending = b""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--stage", default="RPU")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--poll", type=float, default=0.2)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    follower = ProgressFollower(args.log, args.stage, args.offset, args.poll)

    def request_stop(_signum, _frame) -> None:
        follower.stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if args.ready_file is not None:
        args.ready_file.touch()
    follower.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
