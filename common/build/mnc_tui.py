#!/usr/bin/env python3

"""Curses console and summary overlay for an ordinary mnc build child."""

from __future__ import annotations

import argparse
import codecs
import curses
import errno
import fcntl
import os
import pty
import re
import selectors
import signal
import struct
import sys
import termios
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field


ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
PL_PROGRESS_RE = re.compile(
    r"PL_BUILD_PROGRESS=(\S+)\s+(\S+)\s+\[[#.]+\]\s+(\d+)%\s*(.*)"
)


@dataclass
class Stage:
    name: str
    status: str = "PENDING"
    percent: int | None = 0
    detail: str = ""
    started: float | None = None
    finished: float | None = None
    summaries: list[str] = field(default_factory=list)

    def elapsed(self, now: float) -> int:
        if self.started is None:
            return 0
        return int((self.finished or now) - self.started)


class ConsoleBuffer:
    def __init__(self, limit: int = 10000):
        self.lines: deque[str] = deque(maxlen=limit)
        self.current = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, data: bytes) -> list[str]:
        completed: list[str] = []
        text = ANSI_RE.sub("", self.decoder.decode(data))
        for character in text:
            if character == "\r":
                self.current = ""
            elif character == "\n":
                self.lines.append(self.current)
                completed.append(self.current)
                self.current = ""
            elif character == "\b":
                self.current = self.current[:-1]
            elif character >= " " or character == "\t":
                self.current += character.expandtabs(8) if character == "\t" else character
        return completed

    def finish(self) -> None:
        tail = ANSI_RE.sub("", self.decoder.decode(b"", final=True))
        if tail:
            self.current += tail
        if self.current:
            self.lines.append(self.current)
            self.current = ""

    def snapshot(self) -> list[str]:
        values = list(self.lines)
        if self.current:
            values.append(self.current)
        return values


def format_elapsed(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def set_pty_size(fd: int, rows: int, columns: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    except OSError:
        pass


class Tui:
    def __init__(self, screen, mnc: str, arguments: list[str]):
        self.screen = screen
        self.mnc = mnc
        self.arguments = arguments
        self.console = ConsoleBuffer()
        self.stages: OrderedDict[str, Stage] = OrderedDict()
        self.summary_visible = True
        self.scroll = 0
        self.child_pid = 0
        self.child_status: int | None = None
        self.build_started = time.monotonic()
        self.build_finished: float | None = None
        self.event_partial = b""
        self.last_size = (-1, -1)

    def spawn(self) -> tuple[int, int]:
        event_read, event_write = os.pipe()
        set_nonblocking(event_write)
        os.set_inheritable(event_write, True)
        pid, master = pty.fork()
        if pid == 0:
            os.close(event_read)
            environment = os.environ.copy()
            environment["MNC_TUI_CHILD"] = "1"
            environment["MNC_EVENT_FD"] = str(event_write)
            os.execvpe("bash", ["bash", self.mnc, *self.arguments], environment)
        os.close(event_write)
        self.child_pid = pid
        set_nonblocking(master)
        set_nonblocking(event_read)
        return master, event_read

    def ensure_stage(self, name: str) -> Stage:
        if name not in self.stages:
            self.stages[name] = Stage(name)
        return self.stages[name]

    def handle_event(self, line: bytes) -> None:
        try:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            marker, kind, stage_name, percent_text, message = text.split("\t", 4)
        except ValueError:
            return
        if marker != "MNC_EVENT":
            return
        now = time.monotonic()
        if kind == "build_start":
            for name in message.split():
                self.ensure_stage(name)
            return
        if kind == "build_end":
            self.build_finished = now
            for pending in self.stages.values():
                if pending.status == "PENDING":
                    pending.status = "NOT-RUN"
                    pending.percent = 0
            return
        if not stage_name:
            return
        stage = self.ensure_stage(stage_name)
        if kind == "stage_start":
            stage.status = "RUNNING"
            stage.percent = 0
            stage.detail = message
            stage.started = now
        elif kind == "progress":
            stage.status = "RUNNING"
            stage.percent = int(percent_text) if percent_text.isdigit() else None
            stage.detail = message
            stage.started = stage.started or now
        elif kind == "stage_end":
            stage.status = "SUCCESS" if message == "success" else "FAILED"
            stage.percent = 100 if stage.status == "SUCCESS" else stage.percent
            stage.detail = message
            stage.finished = now
        elif kind == "summary":
            stage.summaries.append(message)
            stage.detail = message

    def feed_events(self, data: bytes) -> None:
        self.event_partial += data
        while b"\n" in self.event_partial:
            line, self.event_partial = self.event_partial.split(b"\n", 1)
            self.handle_event(line + b"\n")

    def inspect_console_line(self, line: str) -> None:
        match = PL_PROGRESS_RE.search(line)
        if not match:
            return
        stage = self.ensure_stage("PL")
        stage.status = "RUNNING"
        stage.percent = int(match.group(3))
        stage.detail = f"{match.group(1)}: {match.group(4)}"

    def draw_console(self, rows: int, columns: int) -> None:
        values = self.console.snapshot()
        usable = max(1, rows - 1)
        end = max(0, len(values) - self.scroll)
        start = max(0, end - usable)
        for y, value in enumerate(values[start:end]):
            try:
                self.screen.addnstr(y, 0, value, max(0, columns - 1))
            except curses.error:
                pass
        hint = "s summary  ↑/↓ scroll  End live  Ctrl-C cancel"
        if self.child_status is not None:
            hint = "build finished — Enter/q exits  s summary  ↑/↓ scroll"
        try:
            self.screen.addnstr(rows - 1, 0, hint.ljust(columns), columns - 1, curses.A_REVERSE)
        except curses.error:
            pass

    def progress_bar(self, stage: Stage, width: int, now: float) -> str:
        inner = max(5, width - 2)
        if stage.percent is None:
            position = int(now * 5) % max(1, inner)
            cells = ["."] * inner
            cells[position] = "#"
            return "[" + "".join(cells) + "]"
        filled = max(0, min(inner, stage.percent * inner // 100))
        return "[" + "#" * filled + "." * (inner - filled) + "]"

    def draw_summary(self, rows: int, columns: int) -> None:
        if not self.summary_visible:
            return
        width = min(64, max(44, columns // 2))
        height = min(rows - 2, max(7, len(self.stages) * 2 + 5))
        if columns < 72 or rows < 10 or width >= columns or height < 7:
            return
        begin_x = columns - width - 1
        window = curses.newwin(height, width, 1, begin_x)
        window.erase()
        window.box()
        now = self.build_finished or time.monotonic()
        title = " Build summary "
        try:
            window.addnstr(0, 2, title, width - 4, curses.A_BOLD)
            y = 1
            for stage in self.stages.values():
                elapsed = format_elapsed(stage.elapsed(now))
                headline = f"{stage.name:<8} {stage.status:<8} {elapsed}"
                window.addnstr(y, 2, headline, width - 4)
                y += 1
                if y >= height - 2:
                    break
                bar_width = min(24, width - 6)
                bar = self.progress_bar(stage, bar_width, now)
                percent = " --%" if stage.percent is None else f" {stage.percent:3d}%"
                window.addnstr(y, 2, bar + percent + " " + stage.detail, width - 4)
                y += 1
                if y >= height - 2:
                    break
            total = format_elapsed(int(now - self.build_started))
            window.addnstr(height - 2, 2, f"Total {total}   [s] hide", width - 4, curses.A_BOLD)
        except curses.error:
            pass
        window.noutrefresh()

    def handle_key(self, key: int, rows: int) -> bool:
        if key == -1:
            return False
        if key in (ord("s"), ord("S")):
            self.summary_visible = not self.summary_visible
        elif key == curses.KEY_UP:
            self.scroll += 1
        elif key == curses.KEY_DOWN:
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.scroll += max(1, rows - 2)
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - max(1, rows - 2))
        elif key == curses.KEY_END:
            self.scroll = 0
        elif key == 3 and self.child_status is None:
            try:
                os.killpg(self.child_pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        elif self.child_status is not None and key in (10, 13, ord("q"), ord("Q")):
            return True
        return False

    def run(self) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        self.screen.nodelay(True)
        self.screen.keypad(True)
        master, event_read = self.spawn()
        selector = selectors.DefaultSelector()
        selector.register(master, selectors.EVENT_READ, "console")
        selector.register(event_read, selectors.EVENT_READ, "event")

        while True:
            rows, columns = self.screen.getmaxyx()
            if (rows, columns) != self.last_size:
                set_pty_size(master, rows, columns)
                if self.child_pid:
                    try:
                        os.killpg(self.child_pid, signal.SIGWINCH)
                    except ProcessLookupError:
                        pass
                self.last_size = (rows, columns)

            for key, _ in selector.select(0.05):
                try:
                    data = os.read(key.fd, 65536)
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EIO):
                        raise
                    data = b""
                if data:
                    if key.data == "console":
                        for line in self.console.feed(data):
                            self.inspect_console_line(line)
                    else:
                        self.feed_events(data)
                else:
                    try:
                        selector.unregister(key.fd)
                    except KeyError:
                        pass

            if self.child_status is None:
                waited, status = os.waitpid(self.child_pid, os.WNOHANG)
                if waited:
                    exit_code = os.waitstatus_to_exitcode(status)
                    self.child_status = 128 - exit_code if exit_code < 0 else exit_code
                    self.build_finished = self.build_finished or time.monotonic()
                    for stage in self.stages.values():
                        if stage.status == "RUNNING":
                            stage.status = "INTERRUPTED" if self.child_status == 130 else "FAILED"
                            stage.finished = self.build_finished
                        elif stage.status == "PENDING":
                            stage.status = "NOT-RUN"
                    self.console.finish()

            self.screen.erase()
            self.draw_console(rows, columns)
            self.screen.noutrefresh()
            self.draw_summary(rows, columns)
            curses.doupdate()
            if self.child_status is not None and os.environ.get("MNC_TUI_TEST_AUTO_EXIT") == "1":
                break
            if self.handle_key(self.screen.getch(), rows):
                break
        os.close(master)
        os.close(event_read)
        return 0 if self.child_status is None else self.child_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--mnc")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.arguments[:1] == ["--"]:
        args.arguments = args.arguments[1:]
    return args


def main() -> int:
    args = parse_args()
    if args.check:
        curses.setupterm(fd=sys.stdout.fileno())
        return 0
    if not args.mnc:
        raise SystemExit("--mnc is required")
    return curses.wrapper(lambda screen: Tui(screen, args.mnc, args.arguments).run())


if __name__ == "__main__":
    raise SystemExit(main())
