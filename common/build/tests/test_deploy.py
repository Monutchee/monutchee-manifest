#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


MAKE_DEPLOY = Path(__file__).resolve().parents[1] / "make_deploy.sh"


class DeployTests(unittest.TestCase):
    def fixture(self, root: Path) -> dict[str, Path | dict[str, str]]:
        workspace = root / "workspace"
        tftp = workspace / "yocto-build/build/export/tftpboot"
        tftp.mkdir(parents=True)
        (tftp / "load-jtag-image.tcl").write_text("# mock loader\n")

        invocation = root / "xsdb-invocation.txt"
        xsdb = root / "xsdb"
        xsdb.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s|%s\\n" "$PWD" "$*" > "$MOCK_XSDB_LOG"\n'
        )
        xsdb.chmod(0o755)
        environment = os.environ.copy()
        environment.update(XSDB=str(xsdb), MOCK_XSDB_LOG=str(invocation))
        return {
            "workspace": workspace,
            "tftp": tftp,
            "invocation": invocation,
            "environment": environment,
        }

    def run_deploy(self, fixture: dict, *arguments: str):
        return subprocess.run(
            [
                "bash", str(MAKE_DEPLOY),
                "--workspace", str(fixture["workspace"]),
                "--product", "msap1",
                *arguments,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=fixture["environment"],
        )

    def test_jtag_deploy_runs_xsdb_from_the_tftp_export(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            result = self.run_deploy(
                fixture,
                "--type", "jtag",
                "--xilinx-hw-server-ip", "172.30.19.20",
                "--tftp-machine-ip", "172.30.19.19",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture["invocation"].read_text().strip(),
                f"{fixture['tftp']}|./load-jtag-image.tcl 172.30.19.20 172.30.19.19",
            )
            self.assertIn("JTAG deployment completed", result.stdout)
            self.assertFalse(
                (fixture["workspace"] / "runtime-generated/buildLog").exists()
            )

    def test_jtag_alias_and_ipv4_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            valid = self.run_deploy(
                fixture,
                "--jtag",
                "--xilinx-hw-server-ip=10.0.0.2",
                "--tftp-machine-ip=10.0.0.3",
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)

            invalid = self.run_deploy(
                fixture,
                "--jtag",
                "--xilinx-hw-server-ip", "10.0.0.999",
                "--tftp-machine-ip", "10.0.0.3",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("not an IPv4 address", invalid.stderr)

    def test_rejects_missing_or_unknown_deploy_type(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            missing = self.run_deploy(fixture)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("No deploy type configured", missing.stderr)

            unsupported = self.run_deploy(fixture, "--type", "network")
            self.assertNotEqual(unsupported.returncode, 0)
            self.assertIn("only jtag", unsupported.stderr)


if __name__ == "__main__":
    unittest.main()
