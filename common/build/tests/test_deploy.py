#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MAKE_DEPLOY = Path(__file__).resolve().parents[1] / "make_deploy.sh"


class MockStationHandler(BaseHTTPRequestHandler):
    artifact_id = "a" * 64
    job_id = "job-1"
    events = [
        {"sequence": 1, "time": "2026-08-30T00:00:00Z", "level": "info", "message": "Job queued"},
        {"sequence": 2, "time": "2026-08-30T00:00:01Z", "level": "info", "message": "Job completed successfully"},
    ]

    def log_message(self, _format, *_arguments):
        pass

    def do_GET(self):
        self.server.authorization.append(self.headers.get("Authorization"))
        parsed = urlsplit(self.path)
        if parsed.path == "/api/v1/health":
            self.send_json(200, {"status": "ok", "version": "test", "apiVersion": "v1"})
        elif parsed.path == "/api/v1/capabilities":
            self.send_json(200, {"xsdb": {"available": True}})
        elif parsed.path == f"/api/v1/jobs/{self.job_id}/events":
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            self.send_json(
                200, {"events": [event for event in self.events if event["sequence"] > after]}
            )
        elif parsed.path == f"/api/v1/jobs/{self.job_id}":
            self.send_json(
                200,
                {
                    "id": self.job_id,
                    "state": "succeeded",
                    "eventCount": len(self.events),
                },
            )
        else:
            self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        self.server.authorization.append(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/api/v1/artifacts":
            self.server.upload = body
            self.send_json(
                201,
                {
                    "id": self.artifact_id,
                    "manifest": {"artifact": {"name": "msap1-jtag-image"}},
                },
            )
        elif self.path == "/api/v1/jobs":
            self.server.job_request = json.loads(body)
            self.send_json(201, {"id": self.job_id, "state": "queued"})
        else:
            self.send_json(404, {"error": {"message": "not found"}})

    def send_json(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DeployTests(unittest.TestCase):
    def fixture(self, root: Path) -> dict[str, Path | dict[str, str]]:
        workspace = root / "workspace"
        tftp = workspace / "yocto-build/build/export/tftpboot"
        tftp.mkdir(parents=True)
        (tftp / "load-jtag-image.tcl").write_text("# mock loader\n")
        provision = workspace / "yocto-build/build/export/provision-image"
        provision.mkdir(parents=True)
        artifact = provision / "msap1-jtag-image.tar.gz"
        artifact.write_bytes(b"mock-station-artifact")

        invocation = root / "xsdb-invocation.txt"
        xsdb = root / "xsdb"
        xsdb.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s|%s\\n" "$PWD" "$*" > "$MOCK_XSDB_LOG"\n'
        )
        xsdb.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("MNC_STATION_TOKEN", None)
        environment.pop("MNC_STATION_TOKEN_FILE", None)
        environment.update(XSDB=str(xsdb), MOCK_XSDB_LOG=str(invocation))
        station = ThreadingHTTPServer(("127.0.0.1", 0), MockStationHandler)
        station.upload = b""
        station.job_request = None
        station.authorization = []
        thread = threading.Thread(target=station.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(station.shutdown)
        self.addCleanup(station.server_close)
        return {
            "workspace": workspace,
            "tftp": tftp,
            "artifact": artifact,
            "invocation": invocation,
            "environment": environment,
            "station": station,
            "station_url": f"http://127.0.0.1:{station.server_port}",
        }

    def run_deploy(self, fixture: dict, *arguments: str, product: str = "msap1"):
        return subprocess.run(
            [
                "bash", str(MAKE_DEPLOY),
                "--workspace", str(fixture["workspace"]),
                "--product", product,
                *arguments,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=fixture["environment"],
        )

    def test_jtag_deploy_uploads_artifact_and_follows_station_job(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            result = self.run_deploy(
                fixture,
                "--type", "jtag",
                "--station-url", fixture["station_url"],
                "--xilinx-hw-server-url", "tcp:172.30.19.20:3121",
                "--tftp-server-ip", "172.30.19.19",
                "--board-ip", "172.30.19.21",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                b"mock-station-artifact", fixture["station"].upload
            )
            self.assertEqual(
                fixture["station"].job_request,
                {
                    "artifactId": "a" * 64,
                    "hwServerUrl": "tcp:172.30.19.20:3121",
                    "tftpServerIp": "172.30.19.19",
                    "boardIp": "172.30.19.21",
                },
            )
            self.assertIn("artifact verified", result.stdout)
            self.assertIn("Job completed successfully", result.stdout)
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
                "--station-url", fixture["station_url"],
                "--xilinx-hw-server-ip=10.0.0.2",
                "--tftp-machine-ip=10.0.0.3",
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(
                fixture["station"].job_request["hwServerUrl"],
                "tcp:10.0.0.2:3121",
            )

            overridden = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--xilinx-hw-server-ip", "10.0.0.2",
                "--xilinx-hw-server-url", "tcp:new-hw-server.local:3121",
                "--tftp-server-ip", "10.0.0.3",
            )
            self.assertEqual(overridden.returncode, 0, overridden.stderr)
            self.assertEqual(
                fixture["station"].job_request["hwServerUrl"],
                "tcp:new-hw-server.local:3121",
            )

            invalid = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--xilinx-hw-server-ip", "10.0.0.999",
                "--tftp-machine-ip", "10.0.0.3",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("not an IPv4 address", invalid.stderr)

    def test_station_token_file_authenticates_without_exposing_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            token = "0123456789abcdef0123456789abcdef0123456789abcdef"
            token_file = Path(directory) / "station-token"
            token_file.write_text(token + "\n")
            token_file.chmod(0o600)
            result = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--station-token-file", str(token_file),
                "--xilinx-hw-server-url", "tcp:172.30.19.20:3121",
                "--tftp-server-ip", "172.30.19.19",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(fixture["station"].authorization)
            self.assertEqual(
                set(fixture["station"].authorization),
                {f"Bearer {token}"},
            )
            self.assertNotIn(token, result.stdout)
            self.assertNotIn(token, result.stderr)

    def test_station_token_environment_authenticates_without_exposing_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            token = "fedcba9876543210fedcba9876543210"
            fixture["environment"]["MNC_STATION_TOKEN"] = token
            result = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--xilinx-hw-server-url", "tcp:172.30.19.20:3121",
                "--tftp-server-ip", "172.30.19.19",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                set(fixture["station"].authorization),
                {f"Bearer {token}"},
            )
            self.assertNotIn(token, result.stdout)
            self.assertNotIn(token, result.stderr)

    def test_station_token_sources_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            missing = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--station-token-file", str(Path(directory) / "missing"),
                "--xilinx-hw-server-url", "tcp:172.30.19.20:3121",
                "--tftp-server-ip", "172.30.19.19",
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("not a regular file", missing.stderr)

            token_file = Path(directory) / "station-token"
            token_file.write_text("0123456789abcdef\n")
            fixture["environment"]["MNC_STATION_TOKEN"] = "fedcba9876543210"
            conflict = self.run_deploy(
                fixture,
                "--jtag",
                "--station-url", fixture["station_url"],
                "--station-token-file", str(token_file),
                "--xilinx-hw-server-url", "tcp:172.30.19.20:3121",
                "--tftp-server-ip", "172.30.19.19",
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("either MNC_STATION_TOKEN", conflict.stderr)

    def test_product_without_station_artifact_keeps_legacy_xsdb_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            result = self.run_deploy(
                fixture,
                "--jtag",
                "--xilinx-hw-server-ip", "10.0.0.2",
                "--tftp-machine-ip", "10.0.0.3",
                product="zudemo",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture["invocation"].read_text().strip(),
                f"{fixture['tftp']}|./load-jtag-image.tcl 10.0.0.2 10.0.0.3",
            )
            self.assertIn("legacy direct XSDB flow", result.stderr)

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
