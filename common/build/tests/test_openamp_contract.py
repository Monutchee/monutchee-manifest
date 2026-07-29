import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


BUILD_DIR = Path(__file__).resolve().parents[1]
TOOL = BUILD_DIR / "openamp_contract.py"
CONTRACT = BUILD_DIR.parents[1] / "msap1" / "definition" / "openamp-contract.json"
CONTRACT_DIGEST = (
    "be0505ff0ca639072e84e6e71bfb62e54c97b150f7b2edd38ca2e19344413bd1"
)


class OpenAmpContractTests(unittest.TestCase):
    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOL), *arguments],
            check=False,
            text=True,
            capture_output=True,
        )

    def write_contract(self, directory: Path, value: object) -> Path:
        path = directory / "contract.json"
        path.write_text(json.dumps(value, indent=4), encoding="utf-8")
        return path

    def test_contract_digest_is_canonical(self):
        original = json.loads(CONTRACT.read_text(encoding="utf-8"))
        reordered = dict(reversed(tuple(original.items())))
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_contract(Path(directory), reordered)
            result = self.run_tool(
                "contract-digest", "--contract", str(path)
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), CONTRACT_DIGEST)

    def test_generated_headers_match_known_good_values(self):
        expected_hashes = {
            "r5c0": "e9cf4acf874b81d051f3a0b062e248a3042225d02353372904b32666f0d6acd6",
            "r5c1": "b5c9f445fc48a9434db8bbfb4f115471d1d333bc444910fc1ecb6cc87e5a70ce",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for core, expected in expected_hashes.items():
                output = root / core / "openamp_contract.h"
                result = self.run_tool(
                    "generate-header",
                    "--contract",
                    str(CONTRACT),
                    "--core",
                    core,
                    "--output",
                    str(output),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    hashlib.sha256(output.read_bytes()).hexdigest(), expected
                )
                content = output.read_text(encoding="utf-8")
                self.assertIn("MNC_OPENAMP_CONTRACT_H_", content)
                self.assertNotIn("amd_platform_info", content)

    def test_generated_domain_matches_known_good_value(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "openamp-domain.yaml"
            result = self.run_tool(
                "generate-domain",
                "--contract",
                str(CONTRACT),
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                "0ab666cb5a9f2501c550b8e9e4407a618ebaa57bc0cef0ceec3b5300ff18c883",
            )
            verify = self.run_tool(
                "verify-domain",
                "--contract",
                str(CONTRACT),
                "--domain",
                str(output),
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_rejects_overlapping_regions(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][1]["firmware"]["start"] = value["cores"][0]["firmware"][
            "start"
        ]
        self.assert_invalid(value, "overlap")

    def test_rejects_misaligned_regions(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][0]["rpmsg"]["vring0"]["start"] += 1
        self.assert_invalid(value, "aligned")

    def test_rejects_incomplete_core_set(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"].pop()
        self.assert_invalid(value, "exactly")

    def test_rejects_duplicate_mailboxes(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][1]["rpmsg"]["remote_ipi"] = 1
        value["cores"][1]["rpmsg"]["host_to_remote_mailbox"] = (
            "ipi_7_to_ipi_1"
        )
        value["cores"][1]["rpmsg"]["remote_to_host_mailbox"] = (
            "ipi_1_to_ipi_7"
        )
        self.assert_invalid(value, "mailbox")

    def test_rejects_duplicate_cpu_assignment(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][1]["cpu_cluster"] = value["cores"][0]["cpu_cluster"]
        self.assert_invalid(value, "cpu assignment")

    def test_rejects_duplicate_tcm_assignment(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][1]["firmware"]["tcm"][0] = value["cores"][0][
            "firmware"
        ]["tcm"][0]
        self.assert_invalid(value, "tcm assignment")

    def assert_invalid(self, value: object, message: str):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_contract(Path(directory), copy.deepcopy(value))
            result = self.run_tool(
                "validate-contract", "--contract", str(path)
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn(message, result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
