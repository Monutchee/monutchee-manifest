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
    "b433a1164f76b2156486b34ac66f521cab7e0874076ef0d37adba74d717de89f"
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

    def test_hex_and_decimal_integer_spellings_have_the_same_digest(self):
        hexadecimal = json.loads(CONTRACT.read_text(encoding="utf-8"))

        def decimalize(value: object) -> object:
            if isinstance(value, str) and value.lower().startswith("0x"):
                return int(value, 16)
            if isinstance(value, list):
                return [decimalize(item) for item in value]
            if isinstance(value, dict):
                return {key: decimalize(item) for key, item in value.items()}
            return value

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hex_path = self.write_contract(root, hexadecimal)
            hex_digest = self.run_tool(
                "contract-digest", "--contract", str(hex_path)
            )
            decimal_path = root / "decimal-contract.json"
            decimal_path.write_text(
                json.dumps(decimalize(hexadecimal), indent=4), encoding="utf-8"
            )
            decimal_digest = self.run_tool(
                "contract-digest", "--contract", str(decimal_path)
            )

        self.assertEqual(hex_digest.returncode, 0, hex_digest.stderr)
        self.assertEqual(decimal_digest.returncode, 0, decimal_digest.stderr)
        self.assertEqual(hex_digest.stdout, decimal_digest.stdout)

    def test_generated_headers_match_known_good_values(self):
        expected_hashes = {
            "r5c0": "fddaac648ec2b25175c9cb6a8872bcd620232d1f5f06042ebdd0489d1a2ba83d",
            "r5c1": "4cbdf6bdb5a7ca5f5f227b3bcff9b66b922e6f74c8e8a74aec66049d2a0d9fe2",
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
                "d4e931556f30307de5d38170e68d09ce81f1c518fcdcdc42cd28062fb5073922",
            )
            verify = self.run_tool(
                "verify-domain",
                "--contract",
                str(CONTRACT),
                "--domain",
                str(output),
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_linker_window_must_match_firmware_region(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        firmware = contract["cores"][1]["firmware"]
        with tempfile.TemporaryDirectory() as directory:
            linker = Path(directory) / "lscript.ld"
            linker.write_text(
                "MEMORY { psu_r5_ddr_0_memory_0 : "
                f"ORIGIN = {firmware['start']}, LENGTH = {firmware['size']} }}\n",
                encoding="utf-8",
            )
            valid = self.run_tool(
                "verify-linker", "--contract", str(CONTRACT),
                "--core", "r5c1", "--linker", str(linker)
            )
            linker.write_text(
                "MEMORY { psu_r5_ddr_0_memory_0 : "
                f"ORIGIN = {firmware['start']}, LENGTH = 0x400000 }}\n",
                encoding="utf-8",
            )
            stale = self.run_tool(
                "verify-linker", "--contract", str(CONTRACT),
                "--core", "r5c1", "--linker", str(linker)
            )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(stale.returncode, 2)
        self.assertIn("does not match", stale.stderr)

    def test_rejects_overlapping_regions(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["cores"][1]["firmware"]["start"] = value["cores"][0]["firmware"][
            "start"
        ]
        self.assert_invalid(value, "overlap")

    def test_rejects_misaligned_regions(self):
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        start = value["cores"][0]["rpmsg"]["vring0"]["start"]
        value["cores"][0]["rpmsg"]["vring0"]["start"] = int(start, 0) + 1
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
