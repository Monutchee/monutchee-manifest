#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


MANIFEST_ROOT = Path(__file__).resolve().parents[3]
LIBBUILD = MANIFEST_ROOT / "common" / "build" / "libbuild.sh"
SETUP_WORKSPACE = MANIFEST_ROOT / "common" / "setupWorkspace"


class ProductProfileTests(unittest.TestCase):
    def run_setup_with_fake_repo(self, directory, product, *components):
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir(exist_ok=True)
        repo_log = root / "repo.log"
        fake_repo = bin_dir / "repo"
        fake_repo.write_text(
            """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\t%s\\n' "$PWD" "$*" >> "$REPO_CALL_LOG"
if [[ "${1:-}" == "init" ]]; then
    mkdir -p .repo
fi
"""
        )
        fake_repo.chmod(0o755)
        fake_git = bin_dir / "git"
        fake_git.write_text(
            """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\t%s\\n' "$PWD" "$*" >> "$GIT_CALL_LOG"
"""
        )
        fake_git.chmod(0o755)
        workspace = root / "workspace"
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["REPO_CALL_LOG"] = str(repo_log)
        env["GIT_CALL_LOG"] = str(root / "git.log")
        result = subprocess.run(
            [
                "bash",
                str(SETUP_WORKSPACE),
                "--product",
                product,
                "--workspace",
                str(workspace),
                *components,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        calls = repo_log.read_text().splitlines() if repo_log.exists() else []
        return result, workspace, calls

    def test_msap1_profile_values(self):
        with tempfile.TemporaryDirectory() as directory:
            command = r'''
source "$1"
WORKSPACE_ROOT="$2"
load_product_profile msap1
printf '%s\n' \
    "$PRODUCT" "$PROJECT_PREFIX" "$PL_REPO_DIR" "$PL_XSA_BASENAME" \
    "$SDT_MODE" "$SDT_VALUE_REL" "$RPU_REPO_DIR" "$MACHINE" \
    "$MCONF_TEMPLATE_REL" "$MCONF_DOMAIN_REL" "$DEFAULT_IMAGE_TARGET"
'''
            result = subprocess.run(
                ["bash", "-c", command, "profile-test", str(LIBBUILD), directory],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "msap1",
                    "MSAP1",
                    "MSAP1_PL",
                    "MSAP1_PL.xsa",
                    "board_dts",
                    "zynqmp-smk-k26-reva",
                    "MSAP1_RPU",
                    "msap1",
                    "yocto-build/sources/meta-monutchee/meta-msap1/conf/machineyaml/msap1-sdt.yaml",
                    "yocto-build/sources/meta-monutchee/meta-zynqmp-addon/recipes-bsp/domainyaml/openamp-overlay-zynqmp-v2026_1.yaml",
                    "msap1-image",
                ],
            )

    def test_msap1_setup_installs_build_wrappers_without_component_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            result = subprocess.run(
                [
                    "bash", str(SETUP_WORKSPACE),
                    "--product", "msap1",
                    "--workspace", str(workspace),
                    "scripts",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((workspace / "yocto-build" / ".mncos-product").exists())
            self.assertTrue((workspace / ".monutchee-build/products/msap1.conf").is_file())
            for name in ("make_PL.sh", "make_mconf.sh", "make_RPU.sh", "make_yocto.sh"):
                wrapper = workspace / name
                self.assertTrue(wrapper.is_file(), name)
                self.assertIn("--product msap1", wrapper.read_text())

    def test_product_manifests_separate_workspace_and_yocto_projects(self):
        expected_projects = {
            "zudemo": ["ZuBoardDemo_APU", "ZuBoardDemo_RPU", "ZuBoardDemo_PL"],
            "kr260demo": ["KR260Demo_APU", "KR260Demo_RPU", "KR260Demo_PL"],
            "msap1": ["MSAP1_APU", "MSAP1_RPU", "MSAP1_PL", "MSAP1_WEB"],
        }
        old_names = {
            "zudemo": "zudemo.xml",
            "kr260demo": "kr260demo.xml",
            "msap1": "msap1.xml",
        }

        for product, project_names in expected_projects.items():
            with self.subTest(product=product):
                product_dir = MANIFEST_ROOT / product
                main_root = ET.parse(product_dir / "main.xml").getroot()
                actual = [node.attrib["name"] for node in main_root.findall("project")]
                self.assertEqual(actual, project_names)
                self.assertTrue((product_dir / "yocto.xml").is_file())
                ET.parse(product_dir / "yocto.xml")
                self.assertFalse((product_dir / old_names[product]).exists())

    def test_all_initializes_root_then_independent_yocto_workspace(self):
        for product in ("zudemo", "kr260demo", "msap1"):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                result, workspace, calls = self.run_setup_with_fake_repo(
                    directory, product, "all"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    calls,
                    [
                        f"{workspace}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m {product}/main.xml",
                        f"{workspace}\tsync --fetch-submodules",
                        f"{workspace / 'yocto-build'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m {product}/yocto.xml",
                        f"{workspace / 'yocto-build'}\tsync",
                    ],
                )

    def test_msap1_web_selector_and_non_msap1_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            result, workspace, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "web"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1], f"{workspace}\tsync --fetch-submodules MSAP1_WEB")

        with tempfile.TemporaryDirectory() as directory:
            result, _, calls = self.run_setup_with_fake_repo(
                directory, "kr260demo", "web"
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not define a WEB repository", result.stderr)
            self.assertEqual(calls, [])

    def test_initialized_workspace_can_be_synchronized_again(self):
        with tempfile.TemporaryDirectory() as directory:
            first, _, _ = self.run_setup_with_fake_repo(directory, "msap1", "all")
            self.assertEqual(first.returncode, 0, first.stderr)
            second, _, calls = self.run_setup_with_fake_repo(directory, "msap1", "all")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(calls), 8)

    def test_focused_apu_sync_completes_pinned_submodules(self):
        with tempfile.TemporaryDirectory() as directory:
            result, workspace, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "apu"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1], f"{workspace}\tsync --fetch-submodules MSAP1_APU")
            git_calls = (Path(directory) / "git.log").read_text().splitlines()
            self.assertTrue(
                git_calls[-1].endswith(
                    f"\t-C {workspace / 'MSAP1_APU'} submodule update --init --recursive"
                )
            )

    def test_legacy_component_checkout_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            (workspace / "MSAP1_APU").mkdir(parents=True)
            result, _, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "apu"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("has no root .repo", result.stderr)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
