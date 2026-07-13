#!/usr/bin/env python3

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "artifact.py"
MAKE_PL = Path(__file__).resolve().parents[1] / "make_PL.sh"


class ArtifactTests(unittest.TestCase):
    def run_helper(self, *args: str, check: bool = True):
        return subprocess.run(
            ["python3", str(HELPER), *args],
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_round_trip_and_stage_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "vivado_SDT_out").mkdir()
            (payload / "vivado_SDT_out" / "system-top.dts").write_text("/dts-v1/;\n")
            archive = root / "zudemo_pl_sdtgen.tar.gz"
            output = root / "output"

            self.run_helper(
                "create",
                "--stage", "pl_sdtgen",
                "--product", "zudemo",
                "--payload-root", str(payload),
                "--output", str(archive),
                "--metadata", "source=test",
            )
            self.run_helper(
                "extract",
                "--stage", "pl_sdtgen",
                "--product", "zudemo",
                "--archive", str(archive),
                "--directory", str(output),
            )
            self.assertEqual(
                (output / "vivado_SDT_out" / "system-top.dts").read_text(),
                "/dts-v1/;\n",
            )

            wrong_product = self.run_helper(
                "verify",
                "--stage", "pl_sdtgen",
                "--product", "kr260demo",
                "--archive", str(archive),
                check=False,
            )
            self.assertNotEqual(wrong_product.returncode, 0)
            self.assertIn("expected kr260demo", wrong_product.stderr)

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                data = b"bad"
                info = tarfile.TarInfo("monutchee-artifact-v1/payload/../../escape")
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))

            result = self.run_helper(
                "verify",
                "--stage", "rpu",
                "--product", "zudemo",
                "--archive", str(archive),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe archive member", result.stderr)

    def test_pl_stage_only_consumes_xsa_and_runs_sdtgen(self):
        source = MAKE_PL.read_text()
        for forbidden in (
            "export_xsa.tcl",
            "write_hw_platform",
            'VIVADO="',
            '"${VIVADO}"',
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("--xsa FILE", source)
        self.assertIn('XSA_INPUT="$(canonical_path "${XSA_INPUT}")"', source)
        self.assertIn('XSA_INPUT="${XSA_PATH}"', source)
        self.assertIn('require_file "${XSA_INPUT}"', source)
        self.assertIn('"${SDTGEN}" -xsa "${XSA_INPUT}"', source)

    def test_pl_stage_packages_mock_sdtgen_output_for_both_products(self):
        products = (
            ("zudemo", "ZuBoardDemo_PL.xsa"),
            ("kr260demo", "KR260Demo_PL.xsa"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock_sdtgen = root / "sdtgen"
            mock_sdtgen.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
xsa=""
output=""
while (($# > 0)); do
    case "$1" in
        -xsa) xsa="$2"; shift 2 ;;
        -dir) output="$2"; shift 2 ;;
        *) shift ;;
    esac
done
prefix="$(basename -- "${xsa}" .xsa)"
mkdir -p -- "${output}"
printf '/dts-v1/;\\n' > "${output}/system-top.dts"
printf 'mock bitstream\\n' > "${output}/${prefix}.bit"
printf 'void psu_init(void) {}\\n' > "${output}/psu_init.c"
"""
            )
            mock_sdtgen.chmod(0o755)

            for product, xsa_name in products:
                with self.subTest(product=product):
                    workspace = root / product
                    bin_dir = workspace / "runtime-generated" / "bin_file"
                    bin_dir.mkdir(parents=True)
                    if product == "kr260demo":
                        xsa = workspace / "manual-export" / xsa_name
                        xsa.parent.mkdir(parents=True)
                    else:
                        xsa = bin_dir / xsa_name
                    with zipfile.ZipFile(xsa, "w") as archive:
                        archive.writestr("hw/hardware.hwh", "mock")

                    if product == "zudemo":
                        dts = workspace / (
                            "yocto-build/sources/meta-monutchee/meta-zuboard/"
                            "recipes-bsp/device-tree/files/zub1cg.dtsi"
                        )
                        dts.parent.mkdir(parents=True)
                        dts.write_text("/dts-v1/;\n")

                    env = os.environ.copy()
                    env.update(
                        SDTGEN=str(mock_sdtgen),
                        VIVADO="/must/not/be/called/vivado",
                        XILINX_SETTINGS="/must/not/be/sourced/settings64.sh",
                    )
                    command = [
                        "bash", str(MAKE_PL),
                        "--workspace", str(workspace),
                        "--product", product,
                    ]
                    if product == "kr260demo":
                        command.extend(("--xsa", str(xsa)))
                    result = subprocess.run(
                        command,
                        check=False,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

                    output = bin_dir / f"{product}_pl_sdtgen.tar.gz"
                    with tarfile.open(output, "r:gz") as archive:
                        names = archive.getnames()
                    self.assertTrue(
                        any(name.endswith("/payload/vivado_SDT_out/system-top.dts")
                            for name in names)
                    )
                    self.assertFalse(any(name.endswith(".xsa") for name in names))

    def test_pl_stage_reports_missing_user_exported_xsa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock_sdtgen = root / "sdtgen"
            mock_sdtgen.write_text("#!/usr/bin/env bash\nexit 99\n")
            mock_sdtgen.chmod(0o755)
            result = subprocess.run(
                [
                    "bash", str(MAKE_PL),
                    "--workspace", str(root / "workspace"),
                    "--product", "zudemo",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "SDTGEN": str(mock_sdtgen)},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bitstream-inclusive XSA exported from Vivado", result.stderr)


if __name__ == "__main__":
    unittest.main()
