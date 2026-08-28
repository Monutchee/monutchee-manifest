#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import hashlib
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
MAKE_YOCTO = Path(__file__).resolve().parents[1] / "make_yocto.sh"
OPENAMP_CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "msap1"
    / "definition"
    / "openamp-contract.json"
)
OPENAMP_CONTRACT_TOOL = Path(__file__).resolve().parents[1] / "openamp_contract.py"


def canonical_openamp_contract_digest() -> str:
    """Use the same integer normalization as the installed build contract."""

    result = subprocess.run(
        [
            "python3",
            str(OPENAMP_CONTRACT_TOOL),
            "contract-digest",
            "--contract",
            str(OPENAMP_CONTRACT),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


class ArtifactTests(unittest.TestCase):
    def run_helper(self, *args: str, check: bool = True):
        return subprocess.run(
            ["python3", str(HELPER), *args],
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_hashed_artifact(
        self, root: Path, stage: str, output_base: Path
    ) -> Path:
        payload = root / f"{stage}-payload"
        payload.mkdir(exist_ok=True)
        (payload / "data.txt").write_text(f"{stage} payload\n")
        result = self.run_helper(
            "create",
            "--stage", stage,
            "--product", "msap1",
            "--payload-root", str(payload),
            "--output", str(output_base),
            "--hash-filename",
        )
        return Path(result.stdout.strip())

    def finalize_artifact(
        self,
        bin_dir: Path,
        stage: str,
        output_base: Path,
        published: Path,
    ) -> subprocess.CompletedProcess[str]:
        command = r'''
source "$1"
PRODUCT=msap1
BIN_FILE_DIR="$2"
RPU_DEPENDS_ON_MCONF=false
artifact_finalize_hashed "$3" "$4" "$5"
'''
        return subprocess.run(
            [
                "bash", "-c", command, "artifact-finalize",
                str(HELPER.parent / "libbuild.sh"),
                str(bin_dir),
                stage,
                str(output_base),
                str(published),
            ],
            check=False,
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

    def test_hash_named_artifact_uses_its_archive_sha256_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "data.txt").write_text("artifact payload\n")

            result = self.run_helper(
                "create",
                "--stage", "rpu",
                "--product", "msap1",
                "--payload-root", str(payload),
                "--output", str(root / "msap1_rpu.tar.gz"),
                "--hash-filename",
                "--metadata", "upstream_sha256=0123456789abcdef",
            )
            archive = Path(result.stdout.strip())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            self.assertEqual(archive.name, f"msap1_rpu_{digest[:6]}.tar.gz")
            self.assertFalse((root / "msap1_rpu.tar.gz").exists())
            self.run_helper(
                "verify",
                "--stage", "rpu",
                "--product", "msap1",
                "--archive", str(archive),
            )
            metadata = self.run_helper(
                "metadata",
                "--stage", "rpu",
                "--product", "msap1",
                "--archive", str(archive),
                "--key", "upstream_sha256",
            )
            self.assertEqual(metadata.stdout.strip(), "0123456789abcdef")

    def test_verify_rejects_incorrect_filename_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "data.txt").write_text("artifact payload\n")
            archive = root / "msap1_rpu_deadbe.tar.gz"
            self.run_helper(
                "create",
                "--stage", "rpu",
                "--product", "msap1",
                "--payload-root", str(payload),
                "--output", str(archive),
            )

            result = self.run_helper(
                "verify",
                "--stage", "rpu",
                "--product", "msap1",
                "--archive", str(archive),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filename hash", result.stderr)

    def test_select_uses_newest_matching_artifact_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "msap1_mconf_111111.tar.gz"
            newer = root / "msap1_mconf_222222.tar.gz"
            ignored = root / "msap1_rpu_333333.tar.gz"
            for path in (older, newer, ignored):
                path.write_bytes(b"test")
            os.utime(older, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

            result = self.run_helper(
                "select",
                "--directory", str(root),
                "--pattern", "msap1_mconf_*.tar.gz",
            )
            self.assertEqual(Path(result.stdout.strip()), newer)
            self.assertIn("2 files match", result.stderr)
            self.assertIn(f"using newest {newer.name}", result.stderr)

    def test_prune_removes_only_exact_artifact_family_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_base = root / "msap1_mconf.tar.gz"
            keep = root / "msap1_mconf_222222.tar.gz"
            removed = (
                output_base,
                root / "msap1_mconf_111111.tar.gz",
            )
            untouched = (
                root / "msap1_mconf_backup.tar.gz",
                root / "msap1_mconf_1234567.tar.gz",
                root / "msap1_mconf_ABCDEF.tar.gz",
                root / "msap1_rpu_333333.tar.gz",
                root / "MSAP1_PL.xsa",
                root / "R5c0.elf",
                root / "R5c1.elf",
            )
            for path in (*removed, keep, *untouched):
                path.write_bytes(b"test")

            result = self.run_helper(
                "prune",
                "--output-base", str(output_base),
                "--keep", str(keep),
            )

            self.assertEqual(
                set(result.stdout.splitlines()),
                {str(path) for path in removed},
            )
            self.assertTrue(keep.is_file())
            self.assertTrue(all(path.exists() for path in untouched))
            self.assertTrue(all(not path.exists() for path in removed))

    def test_finalize_prunes_own_stage_and_all_downstream_families(self):
        downstream = {
            "pl_sdtgen": ("mconf", "rpu", "yocto"),
            # MSAP1 RPU consumes the XSA and OpenAMP contract directly.
            "mconf": ("yocto",),
            "rpu": ("yocto",),
            "yocto": (),
        }
        stages = tuple(downstream)

        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                output_base = bin_dir / f"msap1_{stage}.tar.gz"
                published = self.create_hashed_artifact(root, stage, output_base)

                for candidate_stage in stages:
                    (bin_dir / f"msap1_{candidate_stage}.tar.gz").write_bytes(
                        b"legacy"
                    )
                    old_hash = (
                        "111111"
                        if published.name != f"msap1_{candidate_stage}_111111.tar.gz"
                        else "222222"
                    )
                    (
                        bin_dir
                        / f"msap1_{candidate_stage}_{old_hash}.tar.gz"
                    ).write_bytes(b"old")

                preserved = (
                    bin_dir / "MSAP1_PL.xsa",
                    bin_dir / "R5c0.elf",
                    bin_dir / "R5c1.elf",
                    bin_dir / "notes.tar.gz",
                )
                for path in preserved:
                    path.write_bytes(b"preserve")

                result = self.finalize_artifact(
                    bin_dir, stage, output_base, published
                )
                self.assertEqual(result.returncode, 0, result.stderr)

                for candidate_stage in stages:
                    family = list(
                        bin_dir.glob(f"msap1_{candidate_stage}*.tar.gz")
                    )
                    if candidate_stage == stage:
                        self.assertEqual(family, [published], candidate_stage)
                    elif candidate_stage in downstream[stage]:
                        self.assertEqual(family, [], candidate_stage)
                    else:
                        self.assertEqual(len(family), 2, candidate_stage)
                self.assertTrue(all(path.exists() for path in preserved))

    def test_finalize_custom_output_prunes_only_custom_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            export_dir = root / "exports"
            bin_dir.mkdir()
            export_dir.mkdir()

            for stage in ("pl_sdtgen", "mconf", "rpu", "yocto"):
                (bin_dir / f"msap1_{stage}_111111.tar.gz").write_bytes(
                    b"canonical"
                )

            output_base = export_dir / "diagnostic_pl.tar.gz"
            published = self.create_hashed_artifact(
                root, "pl_sdtgen", output_base
            )
            output_base.write_bytes(b"legacy")
            (export_dir / "diagnostic_pl_111111.tar.gz").write_bytes(b"old")

            result = self.finalize_artifact(
                bin_dir, "pl_sdtgen", output_base, published
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                list(export_dir.glob("diagnostic_pl*.tar.gz")),
                [published],
            )
            self.assertEqual(
                len(list(bin_dir.glob("msap1_*.tar.gz"))),
                4,
            )
            self.assertIn("canonical downstream artifacts were preserved", result.stdout)

    def test_finalize_verification_failure_preserves_previous_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            old_artifacts = tuple(
                bin_dir / f"msap1_{stage}_111111.tar.gz"
                for stage in ("pl_sdtgen", "mconf", "rpu", "yocto")
            )
            for path in old_artifacts:
                path.write_bytes(b"previous")
            invalid_new = bin_dir / "msap1_pl_sdtgen_abcdef.tar.gz"
            invalid_new.write_bytes(b"not an artifact")

            result = self.finalize_artifact(
                bin_dir,
                "pl_sdtgen",
                bin_dir / "msap1_pl_sdtgen.tar.gz",
                invalid_new,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(invalid_new.exists())
            self.assertTrue(all(path.exists() for path in old_artifacts))

    def test_workflow_uses_hash_named_outputs_and_latest_default_inputs(self):
        expected_inputs = {
            MAKE_MCONF: ("${PRODUCT}_pl_sdtgen_*.tar.gz",),
            MAKE_RPU: ("${PRODUCT}_mconf_*.tar.gz",),
            MAKE_YOCTO: (
                "${PRODUCT}_mconf_*.tar.gz",
                "${PRODUCT}_rpu_*.tar.gz",
            ),
        }
        for script in (MAKE_PL, MAKE_MCONF, MAKE_RPU, MAKE_YOCTO):
            with self.subTest(script=script.name):
                source = script.read_text()
                self.assertIn("artifact_create_hashed", source)
                self.assertIn("artifact_finalize_hashed", source)
                self.assertLess(
                    source.index("artifact_create_hashed"),
                    source.index("artifact_finalize_hashed"),
                )
                for pattern in expected_inputs.get(script, ()):
                    self.assertIn(
                        f'artifact_select_latest "{pattern}"',
                        source,
                    )
        self.assertIn(
            'artifact_metadata pl_sdtgen "${PL_SDTGEN_ARTIFACT}" xsa_sha256',
            MAKE_MCONF.read_text(),
        )
        self.assertIn(
            'artifact_metadata mconf "${MCONF_ARTIFACT}" xsa_sha256',
            MAKE_RPU.read_text(),
        )
        self.assertIn(
            'artifact_metadata rpu "${RPU_ARTIFACT}" mconf_sha256',
            MAKE_YOCTO.read_text(),
        )
        yocto_source = MAKE_YOCTO.read_text()
        self.assertLess(
            yocto_source.index('if [[ "${PREPARE_ONLY}" == true ]]'),
            yocto_source.index("artifact_finalize_hashed"),
        )

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
        self.assertIn(
            "-path '*/esw-conf-native/*/recipe-sysroot-native/usr/bin/lopper'",
            source,
        )
        self.assertIn('LOPPER_SYSROOT="${LOPPER_SYSROOT}"', source)
        self.assertIn(
            'OPENAMP_DTS_DIR="${YOCTO_BUILD_DIR}/conf/dts/${MACHINE}"',
            source,
        )
        self.assertIn('OPENAMP_OUT_ROOT="${OPENAMP_WORK}"', source)
        self.assertIn('bash "${HEADER_SCRIPT}"', source)
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

    def test_rpu_rejects_a_concurrent_vitis_workspace_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "applications" / "MSAP1_RPU").mkdir(parents=True)
            lock = workspace / "runtime-generated" / ".work" / "rpu-vitis.lock"
            lock.parent.mkdir(parents=True)
            owner = lock.with_suffix(".lock.owner")
            owner.write_text(
                "pid=4242\n"
                "started=2026-08-25T13:55:35Z\n"
                f"workspace={workspace}\n"
            )
            tools = root / "tools"
            tools.mkdir()
            vitis = tools / "vitis"
            vitis.write_text("#!/usr/bin/env bash\nexit 99\n")
            vitis.chmod(0o755)

            with lock.open("w") as lock_stream:
                fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = subprocess.run(
                    [
                        "bash",
                        str(MAKE_RPU),
                        "--workspace",
                        str(workspace),
                        "--product",
                        "msap1",
                    ],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={
                        **os.environ,
                        "VITIS": str(vitis),
                        "PATH": f"{tools}:{os.environ['PATH']}",
                        "XILINX_SETTINGS": "/must/not/be/sourced/settings64.sh",
                    },
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another RPU/Vitis build is already active", result.stderr)
            self.assertIn("pid=4242", result.stderr)
            self.assertIn("started=2026-08-25T13:55:35Z", result.stderr)

    def test_yocto_stage_overrides_nested_application_paths_after_local_conf(self):
        source = (
            Path(__file__).resolve().parents[1] / "make_yocto.sh"
        ).read_text()
        self.assertIn(
            'write_local_source_path "${APU_LOCAL_DIR_VARIABLE}" "${APU_ROOT}"',
            source,
        )
        self.assertIn(
            'write_local_source_path "${WEB_LOCAL_DIR_VARIABLE}" "${WEB_ROOT}"',
            source,
        )
        self.assertIn('--postread "${LOCAL_SOURCE_PATHS}"', source)
        self.assertNotIn("BB_ENV_PASSTHROUGH_ADDITIONS", source)

    def test_rpu_elf_only_reuses_platform_and_packages_both_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            rpu_root = workspace / "applications" / "ZuBoardDemo_RPU"
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
            xsa_sha256 = "1" * 64
            mconf_artifact = root / "zudemo_mconf.tar.gz"
            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "zudemo",
                "--payload-root", str(mconf_payload),
                "--output", str(mconf_artifact),
                "--metadata", f"xsa_sha256={xsa_sha256}",
            )
            mconf_sha256 = hashlib.sha256(mconf_artifact.read_bytes()).hexdigest()
            (
                rpu_root / "platform" / ".monutchee-provenance"
            ).write_text(
                "schema=monutchee-platform-provenance-v1\n"
                "product=zudemo\n"
                f"mconf_sha256={mconf_sha256}\n"
                f"xsa_sha256={xsa_sha256}\n"
                "xilinx_version=2025.2\n"
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
        core = self.name[-1]
        header = (
            Path(workspace).parent / "runtime-generated" / "openamp_gen"
            / f"psu_cortexr5_{core}" / "amd_platform_info.h"
        )
        if not header.is_file():
            raise RuntimeError(f"missing legacy-path OpenAMP header: {header}")
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
            self.assertFalse(
                (workspace / "applications" / "runtime-generated").exists()
            )
            self.assertEqual(
                build_log.read_text().splitlines(),
                ["build:R5c0", "build:R5c1"],
            )

            artifacts = list(
                (workspace / "runtime-generated" / "bin_file").glob(
                    "zudemo_rpu_*.tar.gz"
                )
            )
            self.assertEqual(len(artifacts), 1)
            artifact = artifacts[0]
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(artifact.name, f"zudemo_rpu_{digest[:6]}.tar.gz")
            with tarfile.open(artifact, "r:gz") as archive:
                manifest = json.load(
                    archive.extractfile("monutchee-artifact-v1/manifest.json")
                )
            self.assertEqual(
                sorted(manifest["files"]),
                ["R5c0.elf", "R5c1.elf"],
            )
            self.assertEqual(manifest["metadata"]["build_mode"], "elf-only")
            self.assertEqual(
                manifest["metadata"]["mconf_sha256"],
                mconf_sha256,
            )
            self.assertEqual(
                manifest["metadata"]["xsa_sha256"],
                xsa_sha256,
            )

    def test_msap1_rpu_elf_only_uses_contract_without_mconf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            rpu_root = workspace / "applications" / "MSAP1_RPU"
            for component in ("platform", "R5c0", "R5c1"):
                (rpu_root / component).mkdir(parents=True)
            contract = json.loads(OPENAMP_CONTRACT.read_text(encoding="utf-8"))
            for component, core in (("R5c0", "r5c0"), ("R5c1", "r5c1")):
                user_config = rpu_root / component / "src" / "UserConfig.cmake"
                user_config.parent.mkdir(parents=True)
                user_config.write_text(
                    "set(USER_COMPILE_DEFINITIONS MNC_OPENAMP_CONTRACT)\n"
                    f"set(USER_INCLUDE_DIRECTORIES openamp_contract/{core})\n"
                )
                core_contract = next(
                    item for item in contract["cores"] if item["id"] == core
                )
                firmware = core_contract["firmware"]
                (user_config.parent / "lscript.ld").write_text(
                    "MEMORY\n{\n"
                    "  psu_r5_ddr_0_memory_0 : ORIGIN = "
                    f"{firmware['start']}, LENGTH = {firmware['size']}\n"
                    "}\n"
                )
            helper_platform = (
                rpu_root
                / "libs/openamp-helper/machine/zynqmp_r5/platform_info.h"
            )
            helper_platform.parent.mkdir(parents=True)
            helper_platform.write_text(
                "#ifdef MNC_OPENAMP_CONTRACT\n"
                '#include "openamp_contract.h"\n'
                "#endif\n"
            )
            memory_gate = rpu_root / "scripts" / "verify_r5_memory.py"
            memory_gate.parent.mkdir(parents=True)
            memory_gate.write_text(
                "#!/usr/bin/env python3\n"
                "print('mock R5 memory gate: PASS')\n"
            )

            xsa = workspace / "runtime-generated" / "bin_file" / "MSAP1_PL.xsa"
            xsa.parent.mkdir(parents=True)
            xsa.write_bytes(b"mock XSA")
            xsa_sha256 = hashlib.sha256(xsa.read_bytes()).hexdigest()
            contract_sha256 = canonical_openamp_contract_digest()
            (rpu_root / "platform" / ".monutchee-provenance").write_text(
                "schema=monutchee-platform-provenance-v2\n"
                "product=msap1\n"
                f"xsa_sha256={xsa_sha256}\n"
                f"openamp_contract_sha256={contract_sha256}\n"
                "xilinx_version=2025.2\n"
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
                """from pathlib import Path

workspace = None

class Component:
    def __init__(self, name):
        self.name = name

    def build(self):
        core = self.name.lower()
        header = (
            Path(workspace).parent / "runtime-generated"
            / "openamp_contract" / core / "openamp_contract.h"
        )
        text = header.read_text()
        if "MNC_OPENAMP_CONTRACT_H_" not in text:
            raise RuntimeError(f"invalid OpenAMP contract header: {header}")
        output = Path(workspace) / self.name / "build" / f"{self.name}.elf"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"mock {self.name} elf\\n")
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

            env = os.environ.copy()
            env.update(
                VITIS=str(vitis_wrapper),
                PYTHONPATH=str(tools),
                PATH=f"{tools}:{env['PATH']}",
                XILINX_SETTINGS="/must/not/be/sourced/settings64.sh",
            )
            result = subprocess.run(
                [
                    "bash", str(MAKE_RPU),
                    "--workspace", str(workspace),
                    "--product", "msap1",
                    "--openamp-contract", str(OPENAMP_CONTRACT),
                    "--elf-only",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            artifacts = list(
                xsa.parent.glob("msap1_rpu_*.tar.gz")
            )
            self.assertEqual(len(artifacts), 1)
            with tarfile.open(artifacts[0], "r:gz") as archive:
                manifest = json.load(
                    archive.extractfile("monutchee-artifact-v1/manifest.json")
                )
            self.assertEqual(
                manifest["metadata"]["openamp_contract_sha256"],
                contract_sha256,
            )
            self.assertEqual(manifest["metadata"]["xsa_sha256"], xsa_sha256)
            self.assertNotIn("mconf_sha256", manifest["metadata"])

    def test_rpu_build_is_fail_closed_against_incompatible_sources_and_stale_elfs(
        self,
    ):
        source = MAKE_RPU.read_text()
        self.assertIn("verify_contract_rpu_sources", source)
        self.assertIn(
            "does not enable MNC_OPENAMP_CONTRACT",
            source,
        )
        self.assertIn(
            "still expects amd_platform_info.h",
            source,
        )
        self.assertIn(
            "submodule checkout does not match the commit pinned",
            source,
        )
        self.assertIn('flock -n "${RPU_LOCK_FD}"', source)
        self.assertIn('PYTHONUNBUFFERED=1 "${VITIS}"', source)
        self.assertIn('VITIS_PROGRESS_HELPER="${SCRIPT_DIR}/vitis_log_progress.py"', source)
        stale_cleanup = (
            'rm -f -- "${RPU_ROOT}/${core}/build/${core}.elf"'
        )
        self.assertIn(stale_cleanup, source)
        self.assertLess(
            source.index(stale_cleanup),
            source.index('if [[ "${ELF_ONLY}" == true ]]', source.index(stale_cleanup)),
        )
        self.assertLess(
            source.index('require_file "${ELF}" "${core} firmware"'),
            source.index('mv -f -- "${PLATFORM_RECEIPT_TMP}" "${PLATFORM_RECEIPT}"'),
        )

    def test_msap1_rpu_elf_only_rejects_contract_or_xsa_drift(self):
        contract_sha256 = canonical_openamp_contract_digest()
        for drift, expected in (
            ("contract", "OpenAMP contract changed"),
            ("xsa", "XSA changed"),
        ):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                rpu_root = workspace / "applications" / "MSAP1_RPU"
                (rpu_root / "platform").mkdir(parents=True)
                xsa = (
                    workspace / "runtime-generated" / "bin_file" / "MSAP1_PL.xsa"
                )
                xsa.parent.mkdir(parents=True)
                xsa.write_bytes(b"current XSA")
                current_xsa_sha256 = hashlib.sha256(xsa.read_bytes()).hexdigest()
                receipt_contract = (
                    "0" * 64 if drift == "contract" else contract_sha256
                )
                receipt_xsa = (
                    "0" * 64 if drift == "xsa" else current_xsa_sha256
                )
                (rpu_root / "platform" / ".monutchee-provenance").write_text(
                    "schema=monutchee-platform-provenance-v2\n"
                    "product=msap1\n"
                    f"xsa_sha256={receipt_xsa}\n"
                    f"openamp_contract_sha256={receipt_contract}\n"
                    "xilinx_version=2025.2\n"
                )
                tools = root / "tools"
                tools.mkdir()
                vitis = tools / "vitis"
                vitis.write_text("#!/usr/bin/env bash\nexit 99\n")
                vitis.chmod(0o755)
                result = subprocess.run(
                    [
                        "bash", str(MAKE_RPU),
                        "--workspace", str(workspace),
                        "--product", "msap1",
                        "--openamp-contract", str(OPENAMP_CONTRACT),
                        "--elf-only",
                    ],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={
                        **os.environ,
                        "VITIS": str(vitis),
                        "PATH": f"{tools}:{os.environ['PATH']}",
                        "XILINX_SETTINGS": "/must/not/be/sourced/settings64.sh",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_rpu_elf_only_requires_exact_mconf_platform_receipt(self):
        source = MAKE_RPU.read_text()
        self.assertIn(
            'PLATFORM_RECEIPT="${RPU_ROOT}/platform/.monutchee-provenance"',
            source,
        )
        self.assertIn(
            'PLATFORM_MCONF_SHA256="$(platform_receipt_value mconf_sha256)"',
            source,
        )
        self.assertIn(
            'if [[ "${PLATFORM_MCONF_SHA256}" != "${MCONF_SHA256}" ]]',
            source,
        )
        self.assertIn("run a full 'mnc RPU build'", source)
        self.assertIn(
            '--metadata "xsa_sha256=${XSA_SHA256}"',
            source,
        )
        self.assertIn(
            "printf 'mconf_sha256=%s\\n' \"${MCONF_SHA256}\"",
            source,
        )

    def test_rpu_elf_only_rejects_changed_mconf_before_vitis_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            platform = (
                workspace / "applications" / "ZuBoardDemo_RPU" / "platform"
            )
            platform.mkdir(parents=True)

            payload = root / "mconf-payload"
            payload.mkdir()
            xsa_sha256 = "2" * 64
            mconf_artifact = root / "zudemo_mconf.tar.gz"
            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "zudemo",
                "--payload-root", str(payload),
                "--output", str(mconf_artifact),
                "--metadata", f"xsa_sha256={xsa_sha256}",
            )
            (platform / ".monutchee-provenance").write_text(
                "schema=monutchee-platform-provenance-v1\n"
                "product=zudemo\n"
                f"mconf_sha256={'0' * 64}\n"
                f"xsa_sha256={xsa_sha256}\n"
                "xilinx_version=2025.2\n"
            )

            tools = root / "tools"
            tools.mkdir()
            vitis = tools / "vitis"
            vitis.write_text("#!/usr/bin/env bash\nexit 99\n")
            vitis.chmod(0o755)
            env = os.environ.copy()
            env.update(
                VITIS=str(vitis),
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
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Selected mconf artifact differs from the mconf used to build "
                "the existing Vitis platform",
                result.stderr,
            )

    def test_yocto_requires_rpu_mconf_and_xsa_lineage(self):
        source = MAKE_YOCTO.read_text()
        self.assertIn(
            'artifact_metadata rpu "${RPU_ARTIFACT}" mconf_sha256',
            source,
        )
        self.assertIn(
            'artifact_metadata rpu "${RPU_ARTIFACT}" xsa_sha256',
            source,
        )
        self.assertIn(
            'if [[ "${MCONF_XSA_SHA256}" != "${RPU_XSA_SHA256}" ]]',
            source,
        )
        self.assertIn(
            '--metadata "pl_sdtgen_sha256=${MCONF_PL_SDTGEN_SHA256}"',
            source,
        )

    def test_yocto_rejects_mismatched_rpu_xsa_before_preparing_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"

            mconf_payload = root / "mconf-payload"
            mconf_payload.mkdir()
            mconf_artifact = root / "zudemo_mconf.tar.gz"
            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "zudemo",
                "--payload-root", str(mconf_payload),
                "--output", str(mconf_artifact),
                "--metadata", f"xsa_sha256={'3' * 64}",
                "--metadata", f"pl_sdtgen_sha256={'4' * 64}",
            )
            mconf_sha256 = hashlib.sha256(mconf_artifact.read_bytes()).hexdigest()

            rpu_payload = root / "rpu-payload"
            rpu_payload.mkdir()
            rpu_artifact = root / "zudemo_rpu.tar.gz"
            self.run_helper(
                "create",
                "--stage", "rpu",
                "--product", "zudemo",
                "--payload-root", str(rpu_payload),
                "--output", str(rpu_artifact),
                "--metadata", f"mconf_sha256={mconf_sha256}",
                "--metadata", f"xsa_sha256={'5' * 64}",
            )

            result = subprocess.run(
                [
                    "bash", str(MAKE_YOCTO),
                    "--workspace", str(workspace),
                    "--product", "zudemo",
                    "--mconf-artifact", str(mconf_artifact),
                    "--rpu-artifact", str(rpu_artifact),
                    "--prepare-only",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "RPU artifact and mconf artifact were not built from the same "
                "XSA",
                result.stderr,
            )

    def test_msap1_yocto_rejects_mismatched_openamp_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            xsa_sha256 = "6" * 64

            mconf_payload = root / "mconf-payload"
            mconf_payload.mkdir()
            mconf_artifact = root / "msap1_mconf.tar.gz"
            self.run_helper(
                "create",
                "--stage", "mconf",
                "--product", "msap1",
                "--payload-root", str(mconf_payload),
                "--output", str(mconf_artifact),
                "--metadata", f"xsa_sha256={xsa_sha256}",
                "--metadata", f"pl_sdtgen_sha256={'7' * 64}",
                "--metadata", f"openamp_contract_sha256={'8' * 64}",
            )

            rpu_payload = root / "rpu-payload"
            rpu_payload.mkdir()
            rpu_artifact = root / "msap1_rpu.tar.gz"
            self.run_helper(
                "create",
                "--stage", "rpu",
                "--product", "msap1",
                "--payload-root", str(rpu_payload),
                "--output", str(rpu_artifact),
                "--metadata", f"xsa_sha256={xsa_sha256}",
                "--metadata", f"openamp_contract_sha256={'9' * 64}",
            )

            result = subprocess.run(
                [
                    "bash", str(MAKE_YOCTO),
                    "--workspace", str(workspace),
                    "--product", "msap1",
                    "--mconf-artifact", str(mconf_artifact),
                    "--rpu-artifact", str(rpu_artifact),
                    "--prepare-only",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "RPU and mconf artifacts use different OpenAMP contracts",
                result.stderr,
            )

    def test_pl_stage_resolves_one_xsa_path_for_both_directions(self):
        source = MAKE_PL.read_text()
        self.assertIn("--xsa FILE", source)
        self.assertIn('XSA_FILE="$(canonical_path "${XSA_FILE}")"', source)
        self.assertIn('XSA_FILE="${XSA_PATH}"', source)
        self.assertIn('require_file "${XSA_FILE}"', source)
        self.assertIn('"${SDTGEN}" -xsa "${XSA_FILE}"', source)
        # --gen-xsa writes and --sdtgen reads the same resolved path, so the
        # export target can never drift from the SDTGen input.
        self.assertIn('run_vivado_stage xsa export_xsa.tcl "${XSA_FILE}"', source)

    def test_pl_stage_scripts_are_one_tcl_script_per_stage(self):
        source = MAKE_PL.read_text()
        expected = (
            ("synth", "build_synth.tcl"),
            ("impl", "build_impl.tcl"),
            ("bitstream", "build_bitstream.tcl"),
        )
        for stage, script in expected:
            self.assertIn(f'run_vivado_stage {stage} {script} "${{JOBS}}"', source)
        # Stage order is the source order of the dispatch block.
        order = [
            source.index(f"run_vivado_stage {stage}")
            for stage, _ in (*expected, ("xsa", "export_xsa.tcl"))
        ]
        self.assertEqual(order, sorted(order))
        self.assertLess(
            source.index("run_vivado_stage xsa"),
            source.index('"${SDTGEN}" -xsa'),
        )

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
                        "--sdtgen",
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

                    outputs = list(bin_dir.glob(f"{product}_pl_sdtgen_*.tar.gz"))
                    self.assertEqual(len(outputs), 1)
                    output = outputs[0]
                    digest = hashlib.sha256(output.read_bytes()).hexdigest()
                    self.assertEqual(
                        output.name,
                        f"{product}_pl_sdtgen_{digest[:6]}.tar.gz",
                    )
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
                    "--sdtgen",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "SDTGEN": str(mock_sdtgen)},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bitstream-inclusive XSA exported from Vivado", result.stderr)

    def prepare_pl_compile_workspace(self, root: Path) -> dict:
        """Fake msap1 workspace, PL repository, vivado, sdtgen, and pgrep.

        pgrep is stubbed because the real one makes the Vivado-session guard
        depend on whatever the developer running the tests has open.
        """
        workspace = root / "workspace"
        script_dir = workspace / "applications/MSAP1_PL/SourceData/Script"
        script_dir.mkdir(parents=True)
        for name in (
            "build_bd.tcl",
            "build_synth.tcl",
            "build_impl.tcl",
            "build_bitstream.tcl",
            "export_xsa.tcl",
            "report_status.tcl",
            "report_summary.tcl",
        ):
            (script_dir / name).write_text(f"# stub {name}\n")
        project = workspace / "applications/MSAP1_PL/vivado_gen/MSAP1_PL.xpr"
        project.parent.mkdir(parents=True)
        project.write_text("stub project\n")

        log = root / "vivado-invocations.txt"
        mock_vivado = root / "vivado"
        mock_vivado.write_text(
            """#!/usr/bin/env bash
set -Eeuo pipefail
sourced=""
vivado_log=""
declare -a tclargs=()
while (($# > 0)); do
    case "$1" in
        -source) sourced="$2"; shift 2 ;;
        -log) vivado_log="$2"; shift 2 ;;
        -mode|-journal) shift 2 ;;
        -tclargs) shift; tclargs=("$@"); break ;;
        *) shift ;;
    esac
done
stage="$(basename -- "${sourced}")"
printf '%s %s\\n' "${stage}" "${tclargs[*]-}" >> "${MOCK_VIVADO_LOG}"
if [[ -n "${vivado_log}" ]]; then
    mkdir -p -- "$(dirname -- "${vivado_log}")"
    case "${stage}" in
        build_synth.tcl)
            printf '%s\\n' \
                'PL_BUILD_STATUS=synth_design Complete!' \
                'PL_BUILD_ELAPSED=00:00:03' \
                'PL_BUILD_UTIL_LUT=10/100 (10.00%)' \
                'PL_BUILD_TIMING_SYNTH_ESTIMATE_WNS=1.250' \
                > "${vivado_log}"
            ;;
        build_impl.tcl)
            printf '%s\\n' \
                'PL_BUILD_STATUS=route_design Complete!' \
                'PL_BUILD_ELAPSED=00:00:04' \
                'PL_BUILD_TIMING_WNS=0.500' \
                > "${vivado_log}"
            ;;
        build_bitstream.tcl)
            printf '%s\\n' \
                'PL_BUILD_STATUS=write_bitstream Complete!' \
                'PL_BUILD_ELAPSED=00:00:02' \
                'PL_BUILD_BITSTREAM_BYTES=1234' \
                'PL_BUILD_POWER_TOTAL_W=2.500' \
                > "${vivado_log}"
            ;;
        *) : > "${vivado_log}" ;;
    esac
fi
if [[ "${MOCK_VIVADO_FAIL:-}" == "${stage}" ]]; then
    printf 'mock vivado failure\\n' >&2
    exit 1
fi
if [[ "${stage}" == report_*.tcl ]]; then
    printf 'noise: board scan line\n'
    printf 'PL_REPORT_BEGIN\n'
    printf 'PL_STATUS_VERDICT=ok\n'
    printf 'PL_REPORT_END\n'
    printf 'noise: exiting vivado\n'
fi
if [[ "${stage}" == export_xsa.tcl ]]; then
    python3 -c 'import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("hw/hardware.hwh", "mock")' "${tclargs[0]}"
fi
"""
        )
        mock_vivado.chmod(0o755)

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
printf 'sdtgen %s\\n' "$(basename -- "${xsa}")" >> "${MOCK_VIVADO_LOG}"
prefix="$(basename -- "${xsa}" .xsa)"
mkdir -p -- "${output}"
printf '/dts-v1/;\\n' > "${output}/system-top.dts"
printf 'mock bitstream\\n' > "${output}/${prefix}.bit"
printf 'void psu_init(void) {}\\n' > "${output}/psu_init.c"
"""
        )
        mock_sdtgen.chmod(0o755)

        fake_bin = root / "bin"
        fake_bin.mkdir()
        pgrep = fake_bin / "pgrep"
        pgrep.write_text("#!/usr/bin/env bash\nexit \"${MOCK_PGREP_STATUS:-1}\"\n")
        pgrep.chmod(0o755)

        env = os.environ.copy()
        env.update(
            PATH=f"{fake_bin}{os.pathsep}{env['PATH']}",
            VIVADO=str(mock_vivado),
            SDTGEN=str(mock_sdtgen),
            XILINX_SETTINGS="/must/not/be/sourced/settings64.sh",
            MOCK_VIVADO_LOG=str(log),
        )
        return {"workspace": workspace, "log": log, "env": env}

    def run_make_pl(self, fixture: dict, *args: str, env_extra: dict | None = None):
        return subprocess.run(
            [
                "bash", str(MAKE_PL),
                "--workspace", str(fixture["workspace"]),
                "--product", "msap1",
                *args,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**fixture["env"], **(env_extra or {})},
        )

    def test_pl_default_runs_every_stage_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(fixture, "--jobs", "4")
            self.assertEqual(result.returncode, 0, result.stderr)

            xsa = fixture["workspace"] / "runtime-generated/bin_file/MSAP1_PL.xsa"
            self.assertEqual(
                fixture["log"].read_text().split("\n")[:6],
                [
                    "build_bd.tcl ",
                    "build_synth.tcl 4",
                    "build_impl.tcl 4",
                    "build_bitstream.tcl 4",
                    f"export_xsa.tcl {xsa}",
                    "sdtgen MSAP1_PL.xsa",
                ],
            )
            # Queries are diagnostics, not build steps: never implied.
            self.assertNotIn("report_status.tcl", fixture["log"].read_text())
            self.assertNotIn("report_summary.tcl", fixture["log"].read_text())
            outputs = list(
                (fixture["workspace"] / "runtime-generated/bin_file").glob(
                    "msap1_pl_sdtgen_*.tar.gz"
                )
            )
            self.assertEqual(len(outputs), 1)
            self.assertIn("PL synth=SUCCESS; wall=", result.stdout)
            self.assertIn("UTIL_LUT=10/100 (10.00%)", result.stdout)
            self.assertIn("PL impl=SUCCESS; wall=", result.stdout)
            self.assertIn("TIMING_WNS=0.500", result.stdout)
            self.assertIn("PL bitstream=SUCCESS; wall=", result.stdout)
            self.assertIn("BITSTREAM_BYTES=1234", result.stdout)
            self.assertIn("PL sdtgen=SUCCESS; wall=", result.stdout)

    def test_pl_stages_run_in_canonical_order_whatever_order_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--compile-bit",
                "--compile-synth",
                "--compile-impl",
                "--jobs", "2",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture["log"].read_text().split(),
                ["build_synth.tcl", "2", "build_impl.tcl", "2",
                 "build_bitstream.tcl", "2"],
            )

    def test_pl_single_stage_runs_only_that_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(fixture, "--compile-synth", "--jobs", "8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_synth.tcl 8\n")

    def test_pl_failing_stage_stops_the_chain_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--jobs", "4",
                env_extra={"MOCK_VIVADO_FAIL": "build_impl.tcl"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PL stage impl failed", result.stderr)
            self.assertIn("vivado_gen/logs/impl.log", result.stderr)
            self.assertIn("PL impl=FAILED; wall=", result.stdout)
            # Nothing after the failing stage may run.
            self.assertEqual(
                fixture["log"].read_text(),
                "build_bd.tcl \nbuild_synth.tcl 4\nbuild_impl.tcl 4\n",
            )

    def test_pl_gen_xsa_honours_an_explicit_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.prepare_pl_compile_workspace(root)
            target = root / "handoff"
            target.mkdir()
            xsa = target / "MSAP1_PL.xsa"
            result = self.run_make_pl(
                fixture, "--gen-xsa", "--sdtgen", "--xsa", str(xsa)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(xsa.is_file())
            self.assertEqual(
                fixture["log"].read_text().splitlines(),
                [f"export_xsa.tcl {xsa}", "sdtgen MSAP1_PL.xsa"],
            )

    def test_pl_compile_stages_refuse_a_live_vivado_session(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture, "--compile-synth", env_extra={"MOCK_PGREP_STATUS": "0"}
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Vivado session of this user is open", result.stderr)
            self.assertFalse(fixture["log"].exists())

            override = self.run_make_pl(
                fixture,
                "--compile-synth",
                "--ignore-vivado-session",
                env_extra={"MOCK_PGREP_STATUS": "0"},
            )
            self.assertEqual(override.returncode, 0, override.stderr)
            self.assertIn("build_synth.tcl", fixture["log"].read_text())

    def test_pl_sdtgen_stage_never_invokes_vivado(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            bin_dir = fixture["workspace"] / "runtime-generated/bin_file"
            bin_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(bin_dir / "MSAP1_PL.xsa", "w") as archive:
                archive.writestr("hw/hardware.hwh", "mock")

            result = self.run_make_pl(
                fixture,
                "--sdtgen",
                env_extra={
                    "VIVADO": "/must/not/be/called/vivado",
                    "MOCK_PGREP_STATUS": "0",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "sdtgen MSAP1_PL.xsa\n")

    def test_pl_build_bd_stage_runs_before_synthesis(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture, "--compile-synth", "--build-bd", "--jobs", "2"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture["log"].read_text().splitlines(),
                ["build_bd.tcl ", "build_synth.tcl 2"],
            )

    def test_pl_queries_run_while_a_vivado_session_is_open(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            # The queries open the project read-only, so a live GUI session
            # must not block them the way it blocks the build stages.
            result = self.run_make_pl(
                fixture,
                "--status",
                "--summary",
                env_extra={"MOCK_PGREP_STATUS": "0"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture["log"].read_text().splitlines(),
                ["report_status.tcl ", "report_summary.tcl "],
            )
            # --status also reports the handoff chain Vivado cannot see.
            self.assertIn("XSA: missing", result.stdout)
            self.assertIn("SDT artifact: none published", result.stdout)

    def test_pl_status_reports_a_stale_sdt_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            published = self.run_make_pl(fixture, "--gen-xsa", "--sdtgen")
            self.assertEqual(published.returncode, 0, published.stderr)

            current = self.run_make_pl(fixture, "--status")
            self.assertEqual(current.returncode, 0, current.stderr)
            self.assertIn("SDT artifact: built from the current XSA", current.stdout)

            xsa = fixture["workspace"] / "runtime-generated/bin_file/MSAP1_PL.xsa"
            with zipfile.ZipFile(xsa, "w") as archive:
                archive.writestr("hw/hardware.hwh", "changed")
            stale = self.run_make_pl(fixture, "--status")
            self.assertEqual(stale.returncode, 0, stale.stderr)
            self.assertIn("built from a different XSA", stale.stdout)

    def test_pl_report_lists_and_prints_without_vivado(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            reports = fixture["workspace"] / "applications/MSAP1_PL/vivado_gen/reports"
            reports.mkdir(parents=True)
            (reports / "impl_timing_summary.rpt").write_text("WNS 1.234\n")
            logs = fixture["workspace"] / "applications/MSAP1_PL/vivado_gen/logs"
            logs.mkdir(parents=True)
            (logs / "impl.log").write_text("mock stage log\n")

            no_vivado = {
                "VIVADO": "/must/not/be/called/vivado",
                "MOCK_PGREP_STATUS": "0",
            }
            index = self.run_make_pl(fixture, "--report", env_extra=no_vivado)
            self.assertEqual(index.returncode, 0, index.stderr)
            self.assertIn("impl_timing_summary.rpt", index.stdout)
            self.assertIn("impl.log", index.stdout)
            self.assertFalse(fixture["log"].exists())

            printed = self.run_make_pl(
                fixture, "--report", "impl_timing_summary", env_extra=no_vivado
            )
            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertIn("WNS 1.234", printed.stdout)

            # Stage logs are reachable by the same name.
            log_dump = self.run_make_pl(fixture, "--report=impl", env_extra=no_vivado)
            self.assertEqual(log_dump.returncode, 0, log_dump.stderr)
            self.assertIn("mock stage log", log_dump.stdout)

            missing = self.run_make_pl(
                fixture, "--report", "nope", env_extra=no_vivado
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("No such PL report: nope", missing.stderr)

            traversal = self.run_make_pl(
                fixture, "--report=../../AGENTS.md", env_extra=no_vivado
            )
            self.assertNotEqual(traversal.returncode, 0)
            self.assertIn("must not contain a path", traversal.stderr)

    def test_pl_rejects_an_invalid_job_count(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(fixture, "--compile-synth", "--jobs", "all")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid Vivado job count: all", result.stderr)

    def test_pl_sets_internal_threads_without_increasing_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--compile-impl",
                "--jobs", "1",
                "--threads", "16",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_impl.tcl 1\n")
            self.assertIn(
                "PL internal threads: 16 (--threads); jobs remains 1",
                result.stdout,
            )

    def test_pl_rejects_an_invalid_internal_thread_count(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture, "--compile-impl", "--threads", "auto"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Invalid Vivado internal thread count: auto", result.stderr
            )

    def test_pl_sizes_jobs_from_memory_when_none_is_given(self):
        """The default must come from the machine, not from a constant.

        Each concurrent Vivado run is a separate process holding gigabytes, so a
        core-count default overcommits memory on a design with many IP cores and
        the kernel answers by thrashing swap and OOM-killing the desktop. Only
        the memory term is pinned here: it is forced to bind by making one run
        cost more than any machine has, which makes the expected value 1 on every
        host the tests run on.
        """
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--compile-synth",
                env_extra={"PL_JOB_MEM_MB": "999999999"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_synth.tcl 1\n")
            self.assertIn("PL jobs: 1 (auto", result.stdout)
            # A build that is about to swap has to say so, because the symptom
            # otherwise looks like a hang rather than a misconfiguration.
            self.assertIn("short of one", result.stderr)

    def test_pl_auto_jobs_respects_the_cpu_term_and_the_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            # Memory is made non-binding (nothing reserved, 1 MB per run) so the
            # remaining two terms decide, and both are known here.
            result = self.run_make_pl(
                fixture,
                "--compile-synth",
                env_extra={
                    "PL_RESERVE_MEM_MB": "0",
                    "PL_JOB_MEM_MB": "1",
                    "PL_RESERVE_CPUS": "0",
                    "PL_MAX_JOBS": "4",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = min(os.cpu_count() or 1, 4)
            self.assertEqual(
                fixture["log"].read_text(), f"build_synth.tcl {expected}\n"
            )

    def test_pl_obeys_an_explicit_job_count_above_the_estimate_but_warns(self):
        """An explicit value wins: the caller may know what the estimate cannot.

        Silently substituting a different number would make the build command
        untrustworthy, so the only response is a warning.
        """
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--compile-synth",
                "--jobs", "12",
                env_extra={"PL_JOB_MEM_MB": "999999999"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_synth.tcl 12\n")
            self.assertIn("above the 1 this", result.stderr)

    def test_pl_accepts_auto_as_an_explicit_job_count(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture,
                "--compile-synth",
                "--jobs", "auto",
                env_extra={"PL_JOB_MEM_MB": "999999999"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_synth.tcl 1\n")

    def test_pl_env_job_count_still_overrides_the_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture, "--compile-synth", env_extra={"VIVADO_JOBS": "3"}
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture["log"].read_text(), "build_synth.tcl 3\n")

    def test_pl_rejects_an_invalid_sizing_override(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(
                fixture, "--compile-synth", env_extra={"PL_JOB_MEM_MB": "lots"}
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid PL_JOB_MEM_MB: lots", result.stderr)

    def test_pl_queries_do_not_report_a_job_count(self):
        """--status passes no -jobs, so it has no business printing one."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.prepare_pl_compile_workspace(Path(directory))
            result = self.run_make_pl(fixture, "--status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("PL jobs:", result.stdout)


if __name__ == "__main__":
    unittest.main()
