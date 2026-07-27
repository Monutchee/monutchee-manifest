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
elif [[ "${1:-}" == "start" ]]; then
    for project in "${@:3}"; do
        [[ "${project}" == -* ]] && continue
        mkdir -p "${project}"
        touch "${project}/.git"
    done
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
    "$MCONF_TEMPLATE_REL" "$MCONF_DOMAIN_REL" "$DEFAULT_IMAGE_TARGET" \
    "$APU_ROOT" "$RPU_ROOT" "$PL_ROOT" "$WEB_ROOT" \
    "$APU_LOCAL_DIR_VARIABLE" "$WEB_LOCAL_DIR_VARIABLE"
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
                    f"{directory}/applications/MSAP1_APU",
                    f"{directory}/applications/MSAP1_RPU",
                    f"{directory}/applications/MSAP1_PL",
                    f"{directory}/applications/MSAP1_WEB",
                    "MSAP1_APU_APP_LOCAL_DIR",
                    "MSAP1_WEB_LOCAL_DIR",
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
            self.assertIn(
                "Workspace setup completed successfully.",
                result.stdout,
            )
            self.assertNotIn(
                "Workspace script update completed successfully.",
                result.stdout,
            )
            self.assertFalse((workspace / "yocto-build" / ".mncos-product").exists())
            self.assertTrue((workspace / ".monutchee-build/products/msap1.conf").is_file())
            for name in ("make_PL.sh", "make_mconf.sh", "make_RPU.sh", "make_yocto.sh"):
                wrapper = workspace / name
                self.assertTrue(wrapper.is_file(), name)
                self.assertIn("--product msap1", wrapper.read_text())
            self.assertFalse((workspace / "updateBuildScripts.sh").exists())
            launcher = (workspace / "openTmux").read_text()
            syntax = subprocess.run(
                ["bash", "-n", str(workspace / "openTmux")],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            self.assertIn(
                'open_window root "${WORKSPACE_ROOT}"',
                launcher,
            )
            self.assertLess(
                launcher.index('open_window root "${WORKSPACE_ROOT}"'),
                launcher.index(
                    'open_window yocto "${WORKSPACE_ROOT}/${YOCTO_DIR}/sources/meta-monutchee"'
                ),
            )
            self.assertIn(
                'open_window yocto "${WORKSPACE_ROOT}/${YOCTO_DIR}/sources/meta-monutchee"',
                launcher,
            )
            self.assertIn(
                '-v -l "${YOCTO_BOTTOM_HEIGHT}"',
                launcher,
            )
            self.assertIn(
                "failed to split the yocto window; using one pane",
                launcher,
            )
            self.assertIn(
                'tmux send-keys -t "${YOCTO_SDK_PANE}" \'source ./setupSDK\' C-m',
                launcher,
            )
            self.assertIn(
                'tmux select-window -t "${SESSION}:root"',
                launcher,
            )

    def test_scripts_only_update_does_not_recreate_tmux_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            (workspace / "updateBuildScripts.sh").write_text("legacy updater\n")
            env = os.environ.copy()
            env["MONUTCHEE_SCRIPTS_ONLY_UPDATE"] = "true"
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
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "Workspace script update completed successfully.",
                result.stdout,
            )
            self.assertNotIn(
                "Workspace setup completed successfully.",
                result.stdout,
            )
            self.assertFalse((workspace / "openTmux").exists())
            self.assertFalse((workspace / "updateBuildScripts.sh").exists())

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
                applications_root = ET.parse(
                    product_dir / "applications.xml"
                ).getroot()
                actual = [
                    node.attrib["name"]
                    for node in applications_root.findall("project")
                ]
                self.assertEqual(actual, project_names)
                self.assertTrue((product_dir / "yocto.xml").is_file())
                ET.parse(product_dir / "yocto.xml")
                self.assertFalse((product_dir / "main.xml").exists())
                self.assertFalse((product_dir / old_names[product]).exists())

    def test_all_initializes_independent_applications_and_yocto_clients(self):
        expected_projects = {
            "zudemo": ["ZuBoardDemo_APU", "ZuBoardDemo_RPU", "ZuBoardDemo_PL"],
            "kr260demo": ["KR260Demo_APU", "KR260Demo_RPU", "KR260Demo_PL"],
            "msap1": ["MSAP1_APU", "MSAP1_RPU", "MSAP1_PL", "MSAP1_WEB"],
        }
        for product in ("zudemo", "kr260demo", "msap1"):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                result, workspace, calls = self.run_setup_with_fake_repo(
                    directory, product, "all"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    calls,
                    [
                        f"{workspace / 'applications'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m {product}/applications.xml",
                        f"{workspace / 'applications'}\tsync --fetch-submodules",
                        f"{workspace / 'applications'}\tstart main {' '.join(expected_projects[product])}",
                        f"{workspace / 'yocto-build'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m {product}/yocto.xml",
                        f"{workspace / 'yocto-build'}\tsync",
                        f"{workspace / 'yocto-build'}\tstart main sources/meta-monutchee",
                    ],
                )
                self.assertTrue((workspace / "applications/.repo").is_dir())
                self.assertTrue((workspace / "yocto-build/.repo").is_dir())
                self.assertTrue((workspace / "runtime-generated").is_dir())
                self.assertEqual(
                    (workspace / ".monutchee-workspace").read_text().strip(),
                    product,
                )
                self.assertFalse((workspace / ".repo").exists())

    def test_no_component_performs_full_setup_in_empty_workspace(self):
        for product in ("zudemo", "kr260demo", "msap1"):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                result, workspace, calls = self.run_setup_with_fake_repo(
                    directory, product
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("performing the complete setup", result.stdout)
                self.assertIn(
                    "Workspace setup completed successfully.",
                    result.stdout,
                )
                self.assertNotIn(
                    "Workspace script update completed successfully.",
                    result.stdout,
                )
                self.assertEqual(len(calls), 6)
                self.assertTrue((workspace / "applications/.repo").is_dir())
                self.assertTrue((workspace / "yocto-build/.repo").is_dir())
                self.assertTrue((workspace / ".monutchee-build").is_dir())
                self.assertEqual(
                    (workspace / ".monutchee-workspace").read_text().strip(),
                    product,
                )

    def test_no_component_refreshes_only_scripts_in_initialized_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            first, workspace, first_calls = self.run_setup_with_fake_repo(
                directory, "msap1"
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(len(first_calls), 6)

            installed_rpu = workspace / ".monutchee-build/make_RPU.sh"
            installed_rpu.write_text("outdated\n")
            launcher = workspace / "openTmux"
            launcher.write_text("preserve launcher\n")

            second, _, all_calls = self.run_setup_with_fake_repo(
                directory, "msap1"
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("refreshing build scripts and guidance only", second.stdout)
            self.assertIn(
                "Workspace script update completed successfully.",
                second.stdout,
            )
            self.assertNotIn(
                "Workspace setup completed successfully.",
                second.stdout,
            )
            self.assertEqual(all_calls, first_calls)
            self.assertEqual(
                installed_rpu.read_bytes(),
                (MANIFEST_ROOT / "common/build/make_RPU.sh").read_bytes(),
            )
            self.assertEqual(launcher.read_text(), "preserve launcher\n")

    def test_automatic_mode_rejects_a_different_product_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            (workspace / ".monutchee-workspace").write_text("zudemo\n")
            result, _, calls = self.run_setup_with_fake_repo(
                directory, "msap1"
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("workspace belongs to product zudemo", result.stderr)
            self.assertEqual(calls, [])

    def test_branch_option_selects_manifest_branch_during_automatic_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            repo_log = root / "repo.log"
            fake_repo = bin_dir / "repo"
            fake_repo.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\t%s\\n' "$PWD" "$*" >> "$REPO_CALL_LOG"
if [[ "${1:-}" == "init" ]]; then
    mkdir -p .repo
elif [[ "${1:-}" == "start" ]]; then
    for project in "${@:3}"; do
        [[ "${project}" == -* ]] && continue
        mkdir -p "${project}"
        touch "${project}/.git"
    done
fi
"""
            )
            fake_repo.chmod(0o755)
            fake_git = bin_dir / "git"
            fake_git.write_text("#!/usr/bin/env bash\nexit 0\n")
            fake_git.chmod(0o755)
            workspace = root / "workspace"
            env = os.environ.copy()
            env.update(
                PATH=f"{bin_dir}:{env['PATH']}",
                REPO_CALL_LOG=str(repo_log),
            )
            result = subprocess.run(
                [
                    "bash", str(SETUP_WORKSPACE),
                    "--product", "msap1",
                    "--workspace", str(workspace),
                    "--branch", "feat/add_hex_on_artifact",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = repo_log.read_text().splitlines()
            self.assertIn(
                "-b feat/add_hex_on_artifact -m msap1/applications.xml",
                calls[0],
            )
            self.assertIn(
                "-b feat/add_hex_on_artifact -m msap1/yocto.xml",
                calls[3],
            )

    def test_product_bootstraps_are_posix_and_forward_selected_branch(self):
        for product in ("zudemo", "kr260demo", "msap1"):
            wrapper = MANIFEST_ROOT / product / "setupWorkspace"
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                syntax = subprocess.run(
                    ["sh", "-n", str(wrapper)],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(syntax.returncode, 0, syntax.stderr)

                root = Path(directory)
                tools = root / "tools"
                tools.mkdir()
                setup_log = root / "setup.log"
                curl_log = root / "curl.log"
                mock_setup = root / "shared-setupWorkspace"
                mock_setup.write_text(
                    """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' \
    "${MANIFEST_BRANCH}" \
    "${MANIFEST_RAW_BASE_URL}" \
    "$*" > "${MOCK_SETUP_LOG}"
"""
                )
                curl = tools / "curl"
                curl.write_text(
                    """#!/usr/bin/env bash
set -Eeuo pipefail
url=""
output=""
while (($# > 0)); do
    case "$1" in
        -o) output="$2"; shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "${url}" > "${MOCK_CURL_LOG}"
cp -- "${MOCK_SETUP_SOURCE}" "${output}"
"""
                )
                curl.chmod(0o755)
                env = os.environ.copy()
                env.update(
                    PATH=f"{tools}:{env['PATH']}",
                    MOCK_SETUP_LOG=str(setup_log),
                    MOCK_CURL_LOG=str(curl_log),
                    MOCK_SETUP_SOURCE=str(mock_setup),
                )
                branch = "feat/add_hex_on_artifact"
                result = subprocess.run(
                    ["sh", "-s", "--", "--branch", branch],
                    input=wrapper.read_text(),
                    cwd=root,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                raw_base = (
                    "https://raw.githubusercontent.com/Monutchee/"
                    f"monutchee-manifest/{branch}"
                )
                self.assertEqual(
                    curl_log.read_text().strip(),
                    f"{raw_base}/common/setupWorkspace",
                )
                self.assertEqual(
                    setup_log.read_text().splitlines(),
                    [
                        branch,
                        raw_base,
                        f"--product {product} --branch {branch}",
                    ],
                )

    def test_msap1_web_selector_and_non_msap1_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            result, workspace, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "web"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls[-1],
                f"{workspace / 'applications'}\tstart main MSAP1_WEB",
            )

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
            self.assertEqual(len(calls), 10)
            self.assertEqual(
                calls[-4:],
                [
                    f"{Path(directory) / 'workspace/applications'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m msap1/applications.xml",
                    f"{Path(directory) / 'workspace/applications'}\tsync --fetch-submodules",
                    f"{Path(directory) / 'workspace/yocto-build'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m msap1/yocto.xml",
                    f"{Path(directory) / 'workspace/yocto-build'}\tsync",
                ],
            )

    def test_yocto_selector_does_not_initialize_applications_client(self):
        with tempfile.TemporaryDirectory() as directory:
            result, workspace, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "yocto"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls,
                [
                    f"{workspace / 'yocto-build'}\tinit -u https://github.com/Monutchee/monutchee-manifest.git -b main -m msap1/yocto.xml",
                    f"{workspace / 'yocto-build'}\tsync",
                    f"{workspace / 'yocto-build'}\tstart main sources/meta-monutchee",
                ],
            )
            self.assertFalse((workspace / "applications/.repo").exists())
            self.assertTrue((workspace / "yocto-build/.mncos-product").is_file())

    def test_focused_apu_sync_completes_pinned_submodules(self):
        with tempfile.TemporaryDirectory() as directory:
            result, workspace, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "apu"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls[-2:],
                [
                    f"{workspace / 'applications'}\tsync --fetch-submodules MSAP1_APU",
                    f"{workspace / 'applications'}\tstart main MSAP1_APU",
                ],
            )
            git_calls = (Path(directory) / "git.log").read_text().splitlines()
            self.assertTrue(
                git_calls[-1].endswith(
                    f"\t-C {workspace / 'applications/MSAP1_APU'} submodule update --init --recursive"
                )
            )

    def test_unmanaged_application_checkout_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            (workspace / "applications/MSAP1_APU").mkdir(parents=True)
            result, _, calls = self.run_setup_with_fake_repo(
                directory, "msap1", "apu"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("applications has no .repo", result.stderr)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
