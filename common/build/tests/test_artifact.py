#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "artifact.py"
MAKE_PL = Path(__file__).resolve().parents[1] / "make_PL.sh"
MAKE_MCONF = Path(__file__).resolve().parents[1] / "make_mconf.sh"
MAKE_RPU = Path(__file__).resolve().parents[1] / "make_RPU.sh"


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

    def test_mconf_artifact_carries_both_openamp_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            for core in (0, 1):
                header_dir = payload / "openamp_gen" / f"psu_cortexr5_{core}"
                header_dir.mkdir(parents=True)
                (header_dir / "amd_platform_info.h").write_text(
                    f"#define R5_CORE {core}\n"
                )
            (payload / "yocto-conf" / "machine").mkdir(parents=True)
            (payload / "yocto-conf" / "machine" / "zudemo.conf").write_text(
                'MACHINE = "zudemo"\n'
            )
            (payload / "vivado_SDT_out").mkdir()
            (payload / "vivado_SDT_out" / "system-top.dts").write_text(
                "/dts-v1/;\n"
            )
            archive = root / "zudemo_mconf.tar.gz"
            output = root / "output"

            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "zudemo",
                "--payload-root", str(payload),
                "--output", str(archive),
            )
            self.run_helper(
                "extract",
                "--stage", "mconf",
                "--product", "zudemo",
                "--archive", str(archive),
                "--directory", str(output),
            )
            for core in (0, 1):
                header = (
                    output / "openamp_gen" / f"psu_cortexr5_{core}"
                    / "amd_platform_info.h"
                )
                self.assertEqual(header.read_text(), f"#define R5_CORE {core}\n")

    def test_mconf_generates_and_packages_openamp_headers(self):
        source = MAKE_MCONF.read_text()
        self.assertIn('HEADER_SCRIPT="${RPU_ROOT}/${RPU_HEADER_SCRIPT_REL}"', source)
        self.assertIn('install_machine_conf_payload "${STAGING}/generated-conf"', source)
        self.assertIn('OPENAMP_WORK="${RUNTIME_DIR}/openamp_gen"', source)
        self.assertIn('MACHINE="${MACHINE}" bash "${HEADER_SCRIPT}"', source)
        self.assertIn('"${OPENAMP_WORK}/psu_cortexr5_${core}/amd_platform_info.h"', source)
        self.assertIn('"${STAGING}/payload/openamp_gen/psu_cortexr5_${core}/"', source)
        for symbol in (
            "IPI_IRQ_VECT_ID",
            "POLL_BASE_ADDR",
            "IPI_CHN_BITMASK",
            "SHARED_MEM_PA",
            "SHARED_MEM_SIZE",
            "SHARED_BUF_OFFSET",
        ):
            self.assertIn(symbol, source)
        self.assertNotIn('lopper.log" "${STAGING}/payload', source)

    def test_rpu_stage_has_no_yocto_dependency(self):
        source = MAKE_RPU.read_text()
        for forbidden in (
            "source_yocto_sdk",
            "install_machine_conf_payload",
            "BITBAKE",
            "bitbake",
            "esw-conf-native",
            "GEN_MACHINECONF",
            "gen-machineconf",
            "HEADER_SCRIPT",
            "RPU_HEADER_SCRIPT_REL",
            "BOOTSTRAP_RPU_FILES",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn(
            'copy_tree_fresh "${STAGING}/mconf/openamp_gen" '
            '"${RUNTIME_DIR}/openamp_gen"',
            source,
        )
        self.assertLess(
            source.index('require_file "${STAGING}/mconf/openamp_gen/psu_cortexr5_0'),
            source.index('copy_tree_fresh "${STAGING}/mconf/openamp_gen"'),
        )
        self.assertIn('load_xilinx_environment "${VITIS}"', source)
        self.assertIn('--xsa "${XSA_PATH}"', source)

    def test_rpu_elf_only_reuses_platform_and_packages_both_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            rpu_root = workspace / "ZuBoardDemo_RPU"
            for component in ("platform", "R5c0", "R5c1"):
                (rpu_root / component).mkdir(parents=True)

            mconf_payload = root / "mconf-payload"
            for core in (0, 1):
                header_dir = (
                    mconf_payload / "openamp_gen" / f"psu_cortexr5_{core}"
                )
                header_dir.mkdir(parents=True)
                (header_dir / "amd_platform_info.h").write_text(
                    f"#define R5_CORE {core}\n"
                )
            mconf_artifact = root / "zudemo_mconf.tar.gz"
            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "zudemo",
                "--payload-root", str(mconf_payload),
                "--output", str(mconf_artifact),
            )

            tools = root / "tools"
            tools.mkdir()
            vitis_wrapper = tools / "vitis"
            vitis_wrapper.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$1" == "-s" ]]
script="$2"
shift 2
exec python3 "${script}" "$@"
"""
            )
            vitis_wrapper.chmod(0o755)
            (tools / "vitis.py").write_text(
                """import os
from pathlib import Path

workspace = None

class Component:
    def __init__(self, name):
        self.name = name

    def build(self):
        output = Path(workspace) / self.name / "build" / f"{self.name}.elf"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"mock {self.name} elf\\n")
        with open(os.environ["MOCK_VITIS_LOG"], "a") as stream:
            print(f"build:{self.name}", file=stream)
        return "success"

class Client:
    def set_workspace(self, path):
        global workspace
        workspace = path
        return "success"

    def get_component(self, name):
        return Component(name)

def create_client():
    return Client()

def dispose():
    pass
"""
            )
            readelf = tools / "readelf"
            readelf.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
case "$1" in
    -h)
        printf '  Class:                             ELF32\\n'
        printf '  Machine:                           ARM\\n'
        printf '  Entry point address:               0x0\\n'
        ;;
    -S) printf '  [ 1] .resource_table PROGBITS\\n' ;;
    *) exit 2 ;;
esac
"""
            )
            readelf.chmod(0o755)

            build_log = root / "vitis.log"
            env = os.environ.copy()
            env.update(
                VITIS=str(vitis_wrapper),
                PYTHONPATH=str(tools),
                MOCK_VITIS_LOG=str(build_log),
                PATH=f"{tools}:{env['PATH']}",
                XILINX_SETTINGS="/must/not/be/sourced/settings64.sh",
            )
            result = subprocess.run(
                [
                    "bash", str(MAKE_RPU),
                    "--workspace", str(workspace),
                    "--product", "zudemo",
                    "--mconf-artifact", str(mconf_artifact),
                    "--elf-only",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                build_log.read_text().splitlines(),
                ["build:R5c0", "build:R5c1"],
            )

            artifact = (
                workspace / "runtime-generated" / "bin_file"
                / "zudemo_rpu.tar.gz"
            )
            with tarfile.open(artifact, "r:gz") as archive:
                manifest = json.load(
                    archive.extractfile("monutchee-artifact-v1/manifest.json")
                )
            self.assertEqual(
                sorted(manifest["files"]),
                ["R5c0.elf", "R5c1.elf"],
            )
            self.assertEqual(manifest["metadata"]["build_mode"], "elf-only")
            self.assertNotIn("xsa_sha256", manifest["metadata"])

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

    def test_pl_stage_packages_mock_sdtgen_output_for_all_products(self):
        products = (
            ("zudemo", "ZuBoardDemo_PL.xsa"),
            ("kr260demo", "KR260Demo_PL.xsa"),
            ("msap1", "MSAP1_PL.xsa"),
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
