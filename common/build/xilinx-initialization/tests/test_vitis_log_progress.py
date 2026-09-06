#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


BUILD_DIR = Path(__file__).resolve().parents[1]
HELPER = BUILD_DIR / "vitis_log_progress.py"
sys.path.insert(0, str(BUILD_DIR))

from vitis_log_progress import classify_line  # noqa: E402


class VitisLogProgressTests(unittest.TestCase):
    def test_classifies_milestones_and_errors(self):
        self.assertEqual(
            classify_line("13:55:47 INFO  : SDT generated successfully"),
            (25, "hardware SDT generated"),
        )
        self.assertEqual(
            classify_line(
                "13:56:20 INFO  : Successfully created Domain "
                "/workspace/platform/psu_cortexr5_1/bsp"
            ),
            (40, "R5 core 1 domain created"),
        )
        self.assertEqual(
            classify_line("13:56:21 ERROR : platform build failed"),
            (None, "platform build failed"),
        )
        self.assertIsNone(classify_line("13:56:21 INFO  : ordinary internal detail"))

    def test_follows_only_new_records_and_emits_progress_events(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "vitis.log"
            old = b"12:00:00 INFO  : SDT generated successfully\n"
            log.write_bytes(old)
            ready = Path(directory) / "ready"
            event_read, event_write = os.pipe()
            environment = {**os.environ, "MNC_EVENT_FD": str(event_write)}
            process = subprocess.Popen(
                [
                    "python3",
                    str(HELPER),
                    "--log",
                    str(log),
                    "--stage",
                    "RPU",
                    "--offset",
                    str(len(old)),
                    "--poll",
                    "0.01",
                    "--ready-file",
                    str(ready),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                pass_fds=(event_write,),
            )
            os.close(event_write)
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "progress follower did not become ready")
            with log.open("ab") as stream:
                stream.write(
                    b"13:00:00 INFO  : Platform platform creation started.\n"
                    b"13:00:01 ERROR : Vitis server connection failed\n"
                )
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            events = os.read(event_read, 65536).decode()
            os.close(event_read)

            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn("hardware SDT generated", stdout)
            self.assertIn("[vitis] creating platform from XSA", stdout)
            self.assertIn("[vitis] Vitis server connection failed", stdout)
            self.assertIn(
                "MNC_EVENT\tprogress\tRPU\t20\tcreating platform from XSA",
                events,
            )


if __name__ == "__main__":
    unittest.main()
