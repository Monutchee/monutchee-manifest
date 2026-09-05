import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("release_reports", Path(__file__).resolve().parents[1] / "release_reports.py")
REPORTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORTS)


class ReleaseReportsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.build = self.workspace / "yocto-build/build"
        self.output = self.workspace / "delivery/metadata"
        self.reports = self.build / "tmp/deploy/images/msap1/msap1-image-msap1.rootfs.mncos-reports"
        self.reports.mkdir(parents=True)
        (self.workspace / "applications").mkdir()
        self.repository = self.workspace / "yocto-build/sources/meta-monutchee"
        self.repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        subprocess.run(["git", "-C", str(self.repository), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        for name in ("image.cve.json", "image.spdx.tar.zst", "image.manifest", "kernel.config"):
            (self.reports / name).write_text("fixture")
        (self.reports / "build.json").write_text(json.dumps({"image": "msap1-image", "machine": "msap1"}))

    def collect(self):
        REPORTS.collect(self.workspace, self.build, "msap1-image", "msap1", self.output)

    def test_collects_reports_and_records_dirty_source_without_contents(self):
        (self.repository / "uncommitted.txt").write_text("local source content")
        self.collect()
        source = json.loads((self.output / "source-revisions.json").read_text())["repositories"][0]
        self.assertTrue(source["dirty"])
        self.assertEqual(len(source["revision"]), 40)
        self.assertNotIn("local source content", json.dumps(source))
        self.assertTrue((self.output / "reports/kernel.config").is_file())

    def test_missing_report_prevents_delivery(self):
        (self.reports / "image.cve.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing MNCOS release report"):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_wrong_product_reports_are_rejected(self):
        (self.reports / "build.json").write_text('{"image": "zudemo-image", "machine": "zudemo"}')
        with self.assertRaisesRegex(ValueError, "do not match"):
            self.collect()


if __name__ == "__main__":
    unittest.main()
