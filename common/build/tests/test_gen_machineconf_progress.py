#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BUILD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD_DIR))

from gen_machineconf_progress import report_task_progress


class TaskStarted:
    def __init__(self, completed, active, failed, total, noexec=False):
        self.stats = SimpleNamespace(
            completed=completed, active=active, failed=failed, total=total,
        )
        self.noexec = noexec


class MachineconfProgressTests(unittest.TestCase):
    def test_tinfoil_callback_preserves_handling_and_reports_each_invocation(self):
        ordinary = object()
        events = [
            ordinary, TaskStarted(6700, 74, 0, 9927),
            TaskStarted(9926, 0, 0, 9927, noexec=True),
            TaskStarted(0, 0, 0, 0),
        ]
        callback = mock.Mock(return_value=False)

        class Tinfoil:
            def build_targets(self, targets, task=None, handle_events=True,
                              extra_events=None, event_callback=None):
                self.arguments = targets, task, handle_events, extra_events
                for event in events:
                    self.handled.append(event_callback(event))
                return False  # A failed build must still report failure.

        original = Tinfoil.build_targets
        tinfoil = Tinfoil()
        tinfoil.handled = []
        with mock.patch.dict(os.environ, {"MNC_STAGE_NAME": "mconf"}), mock.patch(
            "gen_machineconf_progress.build_events.emit"
        ) as emit:
            with report_task_progress(Tinfoil, SimpleNamespace(runQueueTaskStarted=TaskStarted)):
                self.assertFalse(tinfoil.build_targets(
                    "kconfig-frontends-native", "addto_recipe_sysroot",
                    extra_events=["custom.event"], event_callback=callback,
                ))
                self.assertEqual(tinfoil.arguments, (
                    "kconfig-frontends-native", "addto_recipe_sysroot", True, ["custom.event"],
                ))
                events[:] = [TaskStarted(9, 0, 0, 200)]
                self.assertFalse(tinfoil.build_targets(["esw-conf-native"]))

        self.assertIs(Tinfoil.build_targets, original)
        self.assertEqual(callback.call_count, 4)
        self.assertEqual(tinfoil.handled, [False] * 5)
        self.assertEqual(emit.call_args_list, [
            mock.call("progress", "mconf", None, "BitBake: kconfig-frontends-native"),
            mock.call("progress", "mconf", 68, "BitBake tasks 6775/9927"),
            mock.call("progress", "mconf", 99, "BitBake tasks 9927/9927"),
            mock.call("progress", "mconf", None, "generating machine configuration"),
            mock.call("progress", "mconf", None, "BitBake: esw-conf-native"),
            mock.call("progress", "mconf", 5, "BitBake tasks 10/200"),
            mock.call("progress", "mconf", None, "generating machine configuration"),
        ])

    def test_async_build_and_callback_consumption_are_preserved(self):
        event = object()

        class Tinfoil:
            def build_targets(self, targets, task=None, handle_events=True,
                              extra_events=None, event_callback=None):
                return event_callback(event) if handle_events else "async result"

        with mock.patch("gen_machineconf_progress.build_events.emit") as emit:
            with report_task_progress(Tinfoil, SimpleNamespace(runQueueTaskStarted=TaskStarted)):
                self.assertEqual(Tinfoil().build_targets("recipe", handle_events=False), "async result")
                emit.assert_not_called()
                self.assertTrue(Tinfoil().build_targets("recipe", event_callback=lambda _: True))

    def test_interrupt_propagates_and_restores_original_method(self):
        class Tinfoil:
            def build_targets(self, *args):
                raise KeyboardInterrupt

        original = Tinfoil.build_targets
        with mock.patch("gen_machineconf_progress.build_events.emit"):
            with self.assertRaises(KeyboardInterrupt):
                with report_task_progress(Tinfoil, SimpleNamespace(runQueueTaskStarted=TaskStarted)):
                    Tinfoil().build_targets("recipe")
        self.assertIs(Tinfoil.build_targets, original)

    def test_launcher_preserves_generator_arguments_exit_status_and_event_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bb = root / "bb"
            bb.mkdir()
            (bb / "__init__.py").write_text("")
            (bb / "runqueue.py").write_text("class runQueueTaskStarted: pass\n")
            (bb / "tinfoil.py").write_text(
                "from types import SimpleNamespace\n"
                "from bb.runqueue import runQueueTaskStarted\n"
                "class Tinfoil:\n"
                "    def build_targets(self, targets, task, handle_events, extra_events, event_callback):\n"
                "        event = runQueueTaskStarted()\n"
                "        event.stats = SimpleNamespace(completed=49, active=0, failed=0, total=100)\n"
                "        assert event_callback(event) is False\n"
                "        return False\n"
            )
            script = root / "gen-machineconf"
            script.write_text(
                "import sys\n"
                "from bb.tinfoil import Tinfoil\n"
                "assert sys.argv[1:] == ['parse-sdt', '--machine-name', 'msap1']\n"
                "assert not Tinfoil().build_targets('kconfig-frontends-native')\n"
                "print('generator output preserved')\n"
                "sys.exit(7)\n"
            )
            event_read, event_write = os.pipe()
            try:
                result = subprocess.run(
                    [sys.executable, str(BUILD_DIR / "gen_machineconf_progress.py"),
                     str(script), "parse-sdt", "--machine-name", "msap1"],
                    env={**os.environ, "MNC_EVENT_FD": str(event_write), "MNC_STAGE_NAME": "mconf"},
                    pass_fds=(event_write,), capture_output=True, text=True, timeout=10,
                )
            finally:
                os.close(event_write)
            with os.fdopen(event_read) as stream:
                output = stream.read()
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(result.stdout, "generator output preserved\n")
            self.assertIn("MNC_EVENT\tprogress\tmconf\t50\tBitBake tasks 50/100\n", output)


if __name__ == "__main__":
    unittest.main()
