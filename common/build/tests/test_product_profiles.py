#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


MANIFEST_ROOT = Path(__file__).resolve().parents[3]
LIBBUILD = MANIFEST_ROOT / "common" / "build" / "libbuild.sh"
SETUP_WORKSPACE = MANIFEST_ROOT / "common" / "setupWorkspace"


class ProductProfileTests(unittest.TestCase):
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
                    "yocto-build/sources/meta-kria/conf/machineyaml/k26-smk-kr-sdt.yaml",
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


if __name__ == "__main__":
    unittest.main()
