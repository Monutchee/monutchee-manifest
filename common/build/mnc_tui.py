#!/usr/bin/env python3

"""Curses console with build and system overlays for an ordinary mnc child."""

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


@dataclass
class ResourceUsage:
    cpu_percent: float | None = None
    memory_used_kib: int = 0
    memory_total_kib: int = 0
    swap_used_kib: int = 0
    swap_total_kib: int = 0


class ResourceMonitor:
    """Low-overhead aggregate CPU and memory sampling from Linux procfs."""

    def __init__(
        self,
        stat_path: str = "/proc/stat",
        meminfo_path: str = "/proc/meminfo",
        sample_interval: float = 0.5,
    ):
        self.stat_path = stat_path
        self.meminfo_path = meminfo_path
        self.sample_interval = sample_interval
        self.last_sample: float | None = None
        self.previous_cpu: tuple[int, int] | None = None
        self.usage = ResourceUsage()

    def _read_cpu(self) -> tuple[int, int]:
        with open(self.stat_path, encoding="utf-8") as stream:
            fields = stream.readline().split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            raise ValueError("aggregate CPU counters are unavailable")
        counters = [int(value) for value in fields[1:9]]
        total = sum(counters)
        idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
        return total, idle

    def _read_memory(self) -> tuple[int, int, int, int]:
        values: dict[str, int] = {}
        with open(self.meminfo_path, encoding="utf-8") as stream:
            for line in stream:
                key, separator, remainder = line.partition(":")
                if not separator:
                    continue
                fields = remainder.split()
                if fields:
                    values[key] = int(fields[0])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable")
        if available is None:
            available = (
                values.get("MemFree", 0)
                + values.get("Buffers", 0)
                + values.get("Cached", 0)
                + values.get("SReclaimable", 0)
                - values.get("Shmem", 0)
            )
        swap_total = values.get("SwapTotal", 0)
        swap_free = values.get("SwapFree", 0)
        return (
            max(0, total - available),
            total,
            max(0, swap_total - swap_free),
            swap_total,
        )

    def sample(self, now: float | None = None, force: bool = False) -> ResourceUsage:
        sampled_at = time.monotonic() if now is None else now
        if (
            not force
            and self.last_sample is not None
            and sampled_at - self.last_sample < self.sample_interval
        ):
            return self.usage
        self.last_sample = sampled_at

        try:
            total, idle = self._read_cpu()
            if self.previous_cpu is not None:
                total_delta = total - self.previous_cpu[0]
                idle_delta = idle - self.previous_cpu[1]
                if total_delta > 0:
                    self.usage.cpu_percent = max(
                        0.0,
                        min(100.0, 100.0 * (total_delta - idle_delta) / total_delta),
                    )
            self.previous_cpu = total, idle
        except (OSError, ValueError):
            pass

        try:
            (
                self.usage.memory_used_kib,
                self.usage.memory_total_kib,
                self.usage.swap_used_kib,
                self.usage.swap_total_kib,
            ) = self._read_memory()
        except (OSError, ValueError):
            pass
        return self.usage


class ConsoleBuffer:
    def __init__(self, limit: int = 10000):
        self.lines: deque[str] = deque(maxlen=limit)
        self.current = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        # A PTY normally translates each child newline to CRLF.  Defer
        # interpreting CR until the following character so CRLF remains a
        # line ending while a standalone CR still replaces a progress line.
        self.pending_cr = False

    def _consume(self, text: str) -> list[str]:
        completed: list[str] = []
        for character in text:
            if self.pending_cr:
                self.pending_cr = False
                if character == "\n":
                    self.lines.append(self.current)
                    completed.append(self.current)
                    self.current = ""
                    continue
                self.current = ""

            if character == "\r":
                self.pending_cr = True
            elif character == "\n":
                self.lines.append(self.current)
                completed.append(self.current)
                self.current = ""
            elif character == "\b":
                self.current = self.current[:-1]
            elif character >= " " or character == "\t":
                self.current += character.expandtabs(8) if character == "\t" else character
        return completed

    def feed(self, data: bytes) -> list[str]:
        text = ANSI_RE.sub("", self.decoder.decode(data))
        return self._consume(text)

    def finish(self) -> None:
        tail = ANSI_RE.sub("", self.decoder.decode(b"", final=True))
        if tail:
            self._consume(tail)
        self.pending_cr = False
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


def format_kib(value: int) -> str:
    amount = float(max(0, value))
    suffix = "K"
    for next_suffix in ("M", "G", "T", "P"):
        if amount < 1024:
            break
        amount /= 1024
        suffix = next_suffix
    if amount >= 100:
        return f"{amount:.0f}{suffix}"
    if amount >= 10:
        return f"{amount:.1f}{suffix}"
    return f"{amount:.2f}{suffix}"


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
        self.resources = ResourceMonitor()
        self.stages: OrderedDict[str, Stage] = OrderedDict()
        self.summary_visible = True
        self.resources_visible = True
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
        hint = "s build  r system  ↑/↓ scroll  End live  Ctrl-C cancel"
        if self.child_status is not None:
            hint = "build finished — Enter/q exits  s build  r system  ↑/↓ scroll"
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

    def draw_summary(self, rows: int, columns: int) -> tuple[int, int, int, int] | None:
        if not self.summary_visible:
            return None
        width = min(64, max(44, columns // 2))
        height = min(rows - 2, max(7, len(self.stages) * 2 + 5))
        if columns < 72 or rows < 10 or width >= columns or height < 7:
            return None
        begin_y = 1
        begin_x = columns - width - 1
        window = curses.newwin(height, width, begin_y, begin_x)
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
        return begin_y, begin_x, height, width

    @staticmethod
    def resource_bar(percent: float | None, width: int) -> str:
        inner = max(5, width - 2)
        if percent is None:
            return "[" + "?" + "." * (inner - 1) + "]"
        filled = max(0, min(inner, int(round(percent * inner / 100))))
        return "[" + "#" * filled + "." * (inner - filled) + "]"

    def resource_line(
        self,
        label: str,
        percent: float | None,
        value: str,
        width: int,
    ) -> str:
        bar_width = max(7, width - len(label) - len(value) - 2)
        return f"{label}{self.resource_bar(percent, bar_width)} {value}"

    def draw_resources(
        self,
        rows: int,
        columns: int,
        summary_geometry: tuple[int, int, int, int] | None,
    ) -> None:
        if not self.resources_visible:
            return
        width = min(64, max(44, columns // 2))
        height = 6
        begin_y = 1
        if summary_geometry is not None:
            begin_y = summary_geometry[0] + summary_geometry[2] + 1
        if (
            columns < 72
            or rows < 8
            or width >= columns
            or begin_y + height > rows - 1
        ):
            return
        begin_x = columns - width - 1
        window = curses.newwin(height, width, begin_y, begin_x)
        window.erase()
        window.box()
        usage = self.resources.usage
        memory_percent = (
            100.0 * usage.memory_used_kib / usage.memory_total_kib
            if usage.memory_total_kib
            else 0.0
        )
        swap_percent = (
            100.0 * usage.swap_used_kib / usage.swap_total_kib
            if usage.swap_total_kib
            else 0.0
        )
        cpu_value = (
            " --.-%" if usage.cpu_percent is None else f"{usage.cpu_percent:5.1f}%"
        )
        memory_value = (
            f"{format_kib(usage.memory_used_kib)}/{format_kib(usage.memory_total_kib)}"
        )
        swap_value = (
            f"{format_kib(usage.swap_used_kib)}/{format_kib(usage.swap_total_kib)}"
        )
        content_width = width - 4
        try:
            window.addnstr(0, 2, " System resources ", width - 4, curses.A_BOLD)
            window.addnstr(
                1,
                2,
                self.resource_line("CPU ", usage.cpu_percent, cpu_value, content_width),
                content_width,
            )
            window.addnstr(
                2,
                2,
                self.resource_line("Mem ", memory_percent, memory_value, content_width),
                content_width,
            )
            window.addnstr(
                3,
                2,
                self.resource_line("Swp ", swap_percent, swap_value, content_width),
                content_width,
            )
            window.addnstr(4, 2, "[r] hide", content_width, curses.A_BOLD)
        except curses.error:
            pass
        window.noutrefresh()

    def handle_key(self, key: int, rows: int) -> bool:
        if key == -1:
            return False
        if key in (ord("s"), ord("S")):
            self.summary_visible = not self.summary_visible
        elif key in (ord("r"), ord("R")):
            self.resources_visible = not self.resources_visible
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
            self.resources.sample()
            self.draw_console(rows, columns)
            self.screen.noutrefresh()
            summary_geometry = self.draw_summary(rows, columns)
            self.draw_resources(rows, columns, summary_geometry)
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
