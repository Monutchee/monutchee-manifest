#!/usr/bin/env python3

"""Grammar and dispatch tests for the mnc build command.

Each test builds a throwaway workspace whose stage scripts are recording stubs,
so what is asserted is exactly the argument vector mnc hands to a stage.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.build.mnc_tui import (
    ConsoleBuffer,
    ResourceMonitor,
    Tui,
    format_elapsed,
    format_kib,
)


BUILD_DIR = Path(__file__).resolve().parents[1]
MNC = BUILD_DIR / "mnc.sh"
LIBBUILD = BUILD_DIR / "libbuild.sh"
PRESET = BUILD_DIR / "preset.py"
TUI = BUILD_DIR / "mnc_tui.py"
PRESET_TEMPLATE = BUILD_DIR / "templates" / "MncBuildPreset.yaml"

STAGES = ("HLS", "PL", "RPU", "mconf", "yocto", "deploy")

# Records its own argv, minus the --workspace/--product pair mnc always adds.
STUB_STAGE = """#!/usr/bin/env bash
set -Eeuo pipefail
declare -a passed=()
while (($# > 0)); do
    case "$1" in
        --workspace|--product) shift 2 ;;
        *) passed+=("$1"); shift ;;
    esac
done
printf '%s\\n' "STAGE_NAME ${passed[*]-}" >> "${MNC_TEST_LOG}"
exit "${MNC_TEST_EXIT:-0}"
"""


class MncTests(unittest.TestCase):
    def workspace(self, root: Path, product: str = "msap1") -> Path:
        """A minimal workspace: toolkit, product marker, stub stages, symlink."""
        workspace = root / "workspace"
        toolkit = workspace / ".monutchee-build"
        (toolkit / "products").mkdir(parents=True)
        (toolkit / "templates").mkdir(parents=True)
        (workspace / "runtime-generated" / "bin_file").mkdir(parents=True)

        toolkit.joinpath("mnc.sh").write_text(MNC.read_text())
        # setupWorkspace chmod +x's it, and "./mnc" needs that.
        toolkit.joinpath("mnc.sh").chmod(0o755)
        toolkit.joinpath("libbuild.sh").write_text(LIBBUILD.read_text())
        toolkit.joinpath("preset.py").write_text(PRESET.read_text())
        toolkit.joinpath("mnc_tui.py").write_text(TUI.read_text())
        toolkit.joinpath("templates", "MncBuildPreset.yaml").write_text(
            PRESET_TEMPLATE.read_text()
        )
        for name in ("msap1", "zudemo", "kr260demo"):
            source = BUILD_DIR / "products" / f"{name}.conf"
            toolkit.joinpath("products", f"{name}.conf").write_text(
                source.read_text()
            )
        toolkit.joinpath(".product").write_text(f"{product}\n")

        for stage in STAGES:
            script = toolkit / f"make_{stage}.sh"
            script.write_text(STUB_STAGE.replace("STAGE_NAME", stage))
            script.chmod(0o755)

        (workspace / "mnc").symlink_to(".monutchee-build/mnc.sh")
        return workspace

    def run_mnc(self, workspace: Path, *arguments: str, exit_code: str = "0"):
        log = workspace / "invocations.txt"
        environment = {
            key: value for key, value in os.environ.items()
            if key != "MONUTCHEE_PRODUCT"
        }
        environment.update(MNC_TEST_LOG=str(log), MNC_TEST_EXIT=exit_code)
        result = subprocess.run(
            ["bash", str(workspace / "mnc"), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workspace,
            env=environment,
        )
        result.invocations = (
            log.read_text().splitlines() if log.exists() else []
        )
        return result

    def assertDispatch(self, workspace: Path, arguments: str, expected: str):
        result = self.run_mnc(workspace, *arguments.split())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.invocations, [expected], arguments)

    def test_dispatch_grammar(self):
        """The command's whole surface, one case per documented behaviour."""
        cases = {
            # build is the bare stage invocation
            "HLS build": "HLS ",
            "PL build": "PL ",
            # arguments after the command pass through untouched
            "PL build --sdtgen": "PL --sdtgen",
            "PL build --jobs 4 --compile-synth": "PL --jobs 4 --compile-synth",
            # any other command becomes --<command>
            "PL sdtgen": "PL --sdtgen",
            "PL status": "PL --status",
            "PL report impl_timing_summary": "PL --report impl_timing_summary",
            "RPU elf-only": "RPU --elf-only",
            "yocto prepare-only": "yocto --prepare-only",
            # --args is an explicit separator and is dropped
            "PL build --args --sdtgen": "PL --sdtgen",
            "PL --args --help": "PL --help",
            # a command that is itself an option is passthrough
            "PL --status": "PL --status",
            # "--" is never mnc's, so make_yocto.sh still gets its BitBake args
            "yocto build -- -c cleanall msap1-image":
                "yocto -- -c cleanall msap1-image",
            "yocto build --args -- -c cleanall": "yocto -- -c cleanall",
            # target matching is case-insensitive in both directions
            "pl build": "PL ",
            "hls build": "HLS ",
            "YOCTO build": "yocto ",
            "MCONF build": "mconf ",
        }
        with tempfile.TemporaryDirectory() as directory:
            for arguments, expected in cases.items():
                workspace = self.workspace(Path(directory) / arguments.replace(" ", "_"))
                with self.subTest(arguments=arguments):
                    self.assertDispatch(workspace, arguments, expected)

    def test_chain_runs_every_stage_in_dependency_order(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "all", "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.invocations,
                ["HLS ", "PL ", "RPU ", "mconf ", "yocto "],
            )
            self.assertIn("Chain complete", result.stdout)
            reports = list(
                (workspace / "runtime-generated/buildLog").glob("build_*.log")
            )
            self.assertEqual(len(reports), 1)
            report = reports[0].read_text()
            self.assertIn("Build summary:", report)
            self.assertIn("yocto    SUCCESS", report)
            self.assertIn("Total build time", report)

    def test_stage_summary_wraps_each_metric_onto_an_indented_line(self):
        """Long PL records keep routed utilization visible in the console."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            stage = workspace / ".monutchee-build/make_PL.sh"
            stage.write_text(
                "#!/usr/bin/env bash\n"
                "set -Eeuo pipefail\n"
                'source "$(dirname -- "$0")/libbuild.sh"\n'
                "build_summary 'PL synth=SUCCESS; wall=00:06:26; "
                "UTIL_LUT=93725/117120 (80.02%); "
                "UTIL_CLB=N/A (physical CLB occupancy requires implementation)'\n"
                "build_summary 'PL impl=SUCCESS; wall=00:16:38; "
                "UTIL_LUT=88552/117120 (75.61%); "
                "UTIL_CLB=14609/14640 (99.79%); "
                "TIMING_WNS=0.299199'\n"
            )
            stage.chmod(0o755)

            result = self.run_mnc(workspace, "PL", "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            final_summary = result.stdout.rsplit("Build summary:", 1)[-1]
            self.assertIn("\n      PL synth=SUCCESS\n", final_summary)
            self.assertIn("\n        wall=00:06:26\n", final_summary)
            self.assertIn(
                "\n        UTIL_CLB=N/A "
                "(physical CLB occupancy requires implementation)\n",
                final_summary,
            )
            self.assertIn("\n      PL impl=SUCCESS\n", final_summary)
            self.assertIn(
                "\n        UTIL_CLB=14609/14640 (99.79%)\n",
                final_summary,
            )
            self.assertIn("\n        TIMING_WNS=0.299199\n", final_summary)

    def test_preset_applies_to_pl_and_command_line_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            preset = workspace / "MncBuildPreset.yaml"
            preset.write_text(
                "version: 1\nstages:\n  PL:\n"
                "    jobs: 1\n    threads: 16\n"
            )

            chain = self.run_mnc(workspace, "all", "build")
            self.assertEqual(chain.returncode, 0, chain.stderr)
            self.assertIn("PL --jobs 1 --threads 16", chain.invocations)

            (workspace / "invocations.txt").unlink()
            direct = self.run_mnc(
                workspace, "PL", "build", "--jobs", "4", "--compile-synth"
            )
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertEqual(
                direct.invocations,
                ["PL --jobs 1 --threads 16 --jobs 4 --compile-synth"],
            )

    def test_station_deploy_preset_and_legacy_keys_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            preset = workspace / "MncBuildPreset.yaml"
            preset.write_text(
                "version: 1\nstages:\n  deploy:\n"
                "    type: jtag\n"
                "    station_url: http://127.0.0.1:8042\n"
                "    xilinx_hw_server_url: tcp:hw-server.local:3121\n"
                "    tftp_server_ip: 192.0.2.10\n"
                "    board_ip: 192.0.2.20\n"
            )
            result = self.run_mnc(workspace, "deploy")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.invocations,
                [
                    "deploy --type jtag --station-url http://127.0.0.1:8042 "
                    "--xilinx-hw-server-url tcp:hw-server.local:3121 "
                    "--tftp-server-ip 192.0.2.10 --board-ip 192.0.2.20"
                ],
            )

            (workspace / "invocations.txt").unlink()
            preset.write_text(
                "version: 1\nstages:\n  deploy:\n"
                "    type: jtag\n"
                "    xilinx_hw_server_ip: 192.0.2.30\n"
                "    tftp_machine_ip: 192.0.2.31\n"
            )
            legacy = self.run_mnc(workspace, "deploy")
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(
                legacy.invocations,
                [
                    "deploy --type jtag --xilinx-hw-server-ip 192.0.2.30 "
                    "--tftp-machine-ip 192.0.2.31"
                ],
            )

    def test_deploy_preset_rejects_ambiguous_and_unsafe_station_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            preset = workspace / "MncBuildPreset.yaml"
            preset.write_text(
                "version: 1\nstages:\n  deploy:\n"
                "    type: jtag\n"
                "    station_url: ftp://station.invalid\n"
                "    xilinx_hw_server_ip: 192.0.2.30\n"
                "    tftp_machine_ip: 192.0.2.31\n"
            )
            result = self.run_mnc(workspace, "deploy")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("http(s) URL", result.stderr)
            self.assertEqual(result.invocations, [])

            preset.write_text(
                "version: 1\nstages:\n  deploy:\n"
                "    type: jtag\n"
                "    xilinx_hw_server_url: tcp:host:3121\n"
                "    xilinx_hw_server_ip: 192.0.2.30\n"
                "    tftp_server_ip: 192.0.2.31\n"
            )
            ambiguous = self.run_mnc(workspace, "deploy")
            self.assertNotEqual(ambiguous.returncode, 0)
            self.assertIn("must not set both", ambiguous.stderr)
            self.assertEqual(ambiguous.invocations, [])

    def test_missing_preset_is_created_and_invalid_preset_stops_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            created = self.run_mnc(workspace, "PL", "build")
            self.assertEqual(created.returncode, 0, created.stderr)
            preset = workspace / "MncBuildPreset.yaml"
            self.assertTrue(preset.is_file())
            self.assertIn("jobs: null", preset.read_text())
            self.assertIn("threads: null", preset.read_text())

            (workspace / "invocations.txt").unlink()
            preset.write_text("version: 1\nstages:\n  PL:\n    jobs: 0\n")
            rejected = self.run_mnc(workspace, "PL", "build")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("positive integer", rejected.stderr)
            self.assertEqual(rejected.invocations, [])

            preset.write_text(
                "version: 1\nstages:\n  PL:\n"
                "    jobs: 1\n    threads: 0\n"
            )
            rejected = self.run_mnc(workspace, "PL", "build")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("stages.PL.threads", rejected.stderr)
            self.assertEqual(rejected.invocations, [])

    def test_tui_falls_back_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "--tui", "PL", "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("continuing with the normal build", result.stderr)
            self.assertEqual(result.invocations, ["PL "])

    def test_default_build_runs_in_tui_in_a_pseudo_terminal_and_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.workspace(root)
            environment = {
                key: value for key, value in os.environ.items()
                if key != "MONUTCHEE_PRODUCT"
            }
            environment.update(
                TERM="xterm-256color",
                MNC_TEST_LOG=str(workspace / "invocations.txt"),
                MNC_TEST_EXIT="0",
                MNC_NO_COMPLETION_INSTALL="1",
                MNC_TUI_TEST_AUTO_EXIT="1",
                HOME=str(root),
            )
            result = subprocess.run(
                ["script", "-qec", "./mnc PL build", "/dev/null"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=workspace,
                env=environment,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                (workspace / "invocations.txt").read_text().splitlines(),
                ["PL "],
            )
            self.assertEqual(
                len(list((workspace / "runtime-generated/buildLog").glob("build_*.log"))),
                1,
            )

    def test_cli_forces_original_mode_in_a_pseudo_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.workspace(root)
            environment = {
                key: value for key, value in os.environ.items()
                if key != "MONUTCHEE_PRODUCT"
            }
            environment.update(
                TERM="xterm-256color",
                MNC_TEST_LOG=str(workspace / "invocations.txt"),
                MNC_TEST_EXIT="0",
                MNC_NO_COMPLETION_INSTALL="1",
                HOME=str(root),
            )
            result = subprocess.run(
                ["script", "-qec", "./mnc --cli PL build", "/dev/null"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=workspace,
                env=environment,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("\x1b[", result.stdout)
            self.assertEqual(
                (workspace / "invocations.txt").read_text().splitlines(),
                ["PL "],
            )

    def test_default_noninteractive_build_uses_cli_without_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "PL", "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("interactive terminal", result.stderr)
            self.assertEqual(result.invocations, ["PL "])

    def test_tui_and_cli_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "--tui", "--cli", "PL", "build")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot be used together", result.stderr)
            self.assertEqual(result.invocations, [])

    def test_deploy_uses_preset_and_never_creates_a_build_report(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "deploy")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.invocations,
                [
                    "deploy --type jtag --station-url http://127.0.0.1:8042 "
                    "--xilinx-hw-server-url tcp:172.30.19.20:3121 "
                    "--tftp-server-ip 172.30.19.19"
                ],
            )
            self.assertFalse(
                (workspace / "runtime-generated/buildLog").exists()
            )

    def test_deploy_jtag_command_line_values_override_the_preset(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(
                workspace,
                "deploy", "jtag",
                "--xilinx-hw-server-ip", "10.0.0.2",
                "--tftp-machine-ip", "10.0.0.3",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.invocations,
                [
                    "deploy --type jtag --station-url http://127.0.0.1:8042 "
                    "--xilinx-hw-server-url tcp:172.30.19.20:3121 "
                    "--tftp-server-ip 172.30.19.19 --type jtag "
                    "--xilinx-hw-server-ip 10.0.0.2 --tftp-machine-ip 10.0.0.3"
                ],
            )

    def test_chain_requires_an_explicit_command(self):
        """"mnc all" is the most expensive thing here; one word must not start it."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "all")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No command given for all", result.stderr)
            self.assertEqual(result.invocations, [])

    def test_options_taking_a_value_reject_a_missing_or_empty_one(self):
        """A failing "shift 2" under set -e would exit with no diagnostic."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            for arguments in (
                ("--from",), ("--to",), ("--from=",), ("--to=",),
                ("--from", "", "all", "build"),
                ("--dry-run", "--from"),
            ):
                with self.subTest(arguments=arguments):
                    result = self.run_mnc(workspace, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("needs a TARGET", result.stderr)
                    self.assertEqual(result.invocations, [])

    def test_chain_stops_at_the_first_failure_and_names_the_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "all", "build", exit_code="3")
            self.assertNotEqual(result.returncode, 0)
            # Only the first stage runs; the rest must not.
            self.assertEqual(result.invocations, ["HLS "])
            self.assertIn("mnc HLS build failed", result.stderr)
            self.assertIn("mnc --from HLS all build", result.stderr)
            report = next(
                (workspace / "runtime-generated/buildLog").glob("build_*.log")
            ).read_text()
            self.assertIn("HLS      FAILED", report)
            self.assertIn("PL       NOT-RUN", report)
            self.assertIn("Resume command", report)

    def test_chain_resume_window(self):
        with tempfile.TemporaryDirectory() as directory:
            for arguments, expected in {
                ("--from", "RPU", "all", "build"): ["RPU ", "mconf ", "yocto "],
                ("--to", "PL", "all", "build"): ["HLS ", "PL "],
                ("--from", "PL", "--to", "RPU", "all", "build"): ["PL ", "RPU "],
                # case-insensitive, like target matching
                ("--from", "rpu", "all", "build"): ["RPU ", "mconf ", "yocto "],
            }.items():
                workspace = self.workspace(Path(directory) / "_".join(arguments))
                with self.subTest(arguments=arguments):
                    result = self.run_mnc(workspace, *arguments)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.invocations, expected)

    def test_chain_refuses_stage_arguments(self):
        """A flag one stage defines would kill another, so the chain takes none."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "all", "build", "--jobs", "4")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("takes no stage arguments", result.stderr)
            self.assertEqual(result.invocations, [])

    def test_chain_refuses_non_build_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "all", "status")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("only supports the build command", result.stderr)
            self.assertEqual(result.invocations, [])

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            for arguments in (("all", "build"), ("PL", "build", "--sdtgen")):
                with self.subTest(arguments=arguments):
                    result = self.run_mnc(workspace, "--dry-run", *arguments)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("would run", result.stdout)
                    self.assertEqual(result.invocations, [])

    def test_stage_exit_status_is_passed_through(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            result = self.run_mnc(workspace, "PL", "build", exit_code="7")
            self.assertEqual(result.returncode, 7)

    def test_targets_are_discovered_not_hardcoded(self):
        """A new make_<name>.sh is usable through mnc with no change to mnc."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            extra = workspace / ".monutchee-build/make_DOC.sh"
            extra.write_text(STUB_STAGE.replace("STAGE_NAME", "DOC"))
            extra.chmod(0o755)

            listing = self.run_mnc(workspace, "--list")
            self.assertIn("DOC", listing.stdout)
            self.assertDispatch(workspace, "DOC build --draft", "DOC --draft")

    def test_rejects_unknown_targets_and_missing_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            for arguments, expected in (
                (("APU", "build"), "Unknown target 'APU'"),
                ((), "No target given"),
                (("PL",), "No command given for PL"),
                (("--bogus", "PL", "build"), "Unknown mnc option: --bogus"),
                (("--from", "PL", "PL", "build"),
                 "--from and --to apply to the 'all' target only"),
                (("--from", "nope", "all", "build"),
                 "--from nope is not in the chain"),
            ):
                with self.subTest(arguments=arguments):
                    result = self.run_mnc(workspace, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertEqual(result.invocations, [])

    def test_product_comes_from_the_workspace_marker(self):
        """No product is baked into the root command, unlike the old wrappers."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory), product="zudemo")
            listing = self.run_mnc(workspace, "--list")
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertIn("Product:   zudemo", listing.stdout)

            # The workspace's own marker outranks the toolkit's.
            (workspace / ".monutchee-workspace").write_text("kr260demo\n")
            listing = self.run_mnc(workspace, "--list")
            self.assertIn("Product:   kr260demo", listing.stdout)

    def test_passes_workspace_and_product_to_every_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            # A stub that records the pair mnc adds, rather than stripping it.
            (workspace / ".monutchee-build/make_PL.sh").write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >> "${MNC_TEST_LOG}"\n'
            )
            result = self.run_mnc(workspace, "PL", "build", "--sdtgen")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.invocations,
                [f"--workspace {workspace} --product msap1 --sdtgen"],
            )

    def test_symlink_invocation_finds_the_toolkit_from_any_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            elsewhere = Path(directory) / "elsewhere"
            elsewhere.mkdir()
            result = subprocess.run(
                ["bash", str(workspace / "mnc"), "--list"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=elsewhere,
                env={key: value for key, value in os.environ.items()
                     if key != "MONUTCHEE_PRODUCT"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Workspace: {workspace}", result.stdout)

    def test_help_is_available_and_documents_the_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(Path(directory))
            for flag in ("-h", "--help"):
                result = self.run_mnc(workspace, flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("mnc [OPTIONS] <target> <command>", result.stdout)
                self.assertIn("--args", result.stdout)
                self.assertEqual(result.invocations, [])
                # The order differs per product, so help must not assert one.
                for stage in ("HLS -> PL", "PL -> RPU", "mconf -> yocto"):
                    self.assertNotIn(stage, result.stdout)


class CompletionTests(unittest.TestCase):
    """The TAB completion, driven exactly as a shell drives it.

    Registration needs an interactive shell, so these exercise the completion
    function directly, in bash and in zsh under the ksh emulation bashcompinit
    uses (which makes COMP_WORDS 0-indexed as the function expects).
    """

    COMPLETION = BUILD_DIR / "mnc-completion.bash"

    def complete(self, shell: str, workspace: Path, *words: str) -> list[str]:
        emulate = "emulate -L ksh\n" if shell == "zsh" else ""
        script = (
            f'source "{self.COMPLETION}" >/dev/null 2>&1\n'
            f"{emulate}"
            f'COMP_WORDS=({" ".join(f'"{w}"' for w in words)} "")\n'
            f"COMP_CWORD={len(words)}\n"
            "COMPREPLY=()\n"
            "_mnc\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n'
        )
        result = subprocess.run(
            [shell, "-c", script],
            check=False, text=True, cwd=workspace,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line for line in result.stdout.split("\n") if line]

    def test_completes_the_same_in_bash_and_zsh(self):
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            command = str(workspace / "mnc")
            for words, expected in (
                ((command,), {"HLS", "PL", "RPU", "mconf", "yocto", "deploy", "all",
                              "--list", "--dry-run", "--tui", "--cli", "--from", "--to"}),
                ((command, "all"), {"build", "help"}),
                ((command, "deploy"), {"jtag", "build", "help"}),
                ((command, "--from"), {"HLS", "PL", "RPU", "mconf", "yocto"}),
            ):
                for shell in ("bash", "zsh"):
                    with self.subTest(words=words, shell=shell):
                        offered = set(self.complete(shell, workspace, *words))
                        self.assertTrue(
                            expected <= offered,
                            f"{expected - offered} missing from {offered}",
                        )

    def test_stage_commands_come_from_the_stage_script(self):
        """A stage option is completable as a command with no change here."""
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            stage = workspace / ".monutchee-build/make_PL.sh"
            stage.write_text(
                stage.read_text().replace(
                    "        *) passed+=(\"$1\"); shift ;;",
                    "        --brand-new-option) shift ;;\n"
                    "        *) passed+=(\"$1\"); shift ;;",
                )
            )
            offered = self.complete("bash", workspace, str(workspace / "mnc"), "PL")
            self.assertIn("brand-new-option", offered)
            self.assertIn("build", offered)

    def test_never_offers_options_mnc_already_injects(self):
        """mnc always passes --workspace/--product; a valueless duplicate would
        make the stage script's "shift 2" fail with no diagnostic."""
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            command = str(workspace / "mnc")
            for words in ((command, "PL"), (command, "PL", "build")):
                with self.subTest(words=words):
                    offered = self.complete("bash", workspace, *words)
                    for forbidden in ("workspace", "--workspace",
                                      "product", "--product"):
                        self.assertNotIn(forbidden, offered)

    def run_on_a_tty(self, workspace: Path, home: Path, shell: str,
                     *arguments: str, env_extra: dict | None = None):
        """mnc only edits an rc file for a human at a terminal, so give it one."""
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("MONUTCHEE_PRODUCT", "MNC_NO_COMPLETION_INSTALL")
        }
        environment.update(HOME=str(home), SHELL=shell, **(env_extra or {}))
        return subprocess.run(
            ["script", "-qec",
             " ".join(["./mnc", *arguments]), "/dev/null"],
            check=False, text=True, cwd=workspace,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=environment,
        )

    def test_first_run_on_a_terminal_installs_completion_once(self):
        helper = MncTests()
        for shell, rc_name in (("/usr/bin/zsh", ".zshrc"), ("/bin/bash", ".bashrc")):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = helper.workspace(root)
                (workspace / ".monutchee-build/mnc-completion.bash").write_text(
                    self.COMPLETION.read_text()
                )
                home = root / "home"
                home.mkdir()
                rc = home / rc_name
                rc.write_text("export EXISTING=1\n")

                with self.subTest(shell=shell):
                    first = self.run_on_a_tty(workspace, home, shell, "--list")
                    self.assertIn("TAB completion added", first.stdout)

                    body = rc.read_text()
                    # Pre-existing content is kept, and the hook is guarded so a
                    # deleted workspace cannot break shell startup.
                    self.assertIn("export EXISTING=1", body)
                    self.assertIn("mnc-completion.bash", body)
                    self.assertRegex(body, r"if \[ -f .* \]; then source .*; fi")

                    # Idempotent.
                    second = self.run_on_a_tty(workspace, home, shell, "--list")
                    self.assertNotIn("TAB completion added", second.stdout)
                    self.assertEqual(rc.read_text().count("mnc-completion.bash"), 2)

    def test_never_edits_an_rc_file_without_a_terminal(self):
        """A pipeline or CI run must not touch the developer's shell config."""
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = helper.workspace(root)
            (workspace / ".monutchee-build/mnc-completion.bash").write_text(
                self.COMPLETION.read_text()
            )
            home = root / "home"
            home.mkdir()
            rc = home / ".zshrc"
            rc.write_text("")

            environment = {
                key: value for key, value in os.environ.items()
                if key != "MONUTCHEE_PRODUCT"
            }
            environment.update(HOME=str(home), SHELL="/usr/bin/zsh")
            result = subprocess.run(
                ["bash", str(workspace / "mnc"), "--list"],
                check=False, text=True, cwd=workspace,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(rc.read_text(), "")

    def test_completion_install_can_be_declined(self):
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = helper.workspace(root)
            (workspace / ".monutchee-build/mnc-completion.bash").write_text(
                self.COMPLETION.read_text()
            )
            home = root / "home"
            home.mkdir()
            rc = home / ".zshrc"
            rc.write_text("")
            self.run_on_a_tty(
                workspace, home, "/usr/bin/zsh", "--list",
                env_extra={"MNC_NO_COMPLETION_INSTALL": "1"},
            )
            self.assertEqual(rc.read_text(), "")

    def test_sourcing_mnc_registers_completion_and_runs_nothing(self):
        """The user's own idea: apply completion in the current shell.

        Sourcing runs in the caller's shell, so it can register completion --
        but then the rest of the script must not run, or "set -Eeuo pipefail"
        would persist in an interactive shell, die() would exit it, and a stage
        would replace it with exec.
        """
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            (workspace / ".monutchee-build/mnc-completion.bash").write_text(
                self.COMPLETION.read_text()
            )
            log = workspace / "invocations.txt"

            # zsh needs its completion system before bashcompinit can work.
            preludes = {
                "bash": "",
                "zsh": "autoload -Uz compinit && "
                       f"compinit -u -d {workspace}/zcd >/dev/null 2>&1; ",
            }
            for shell, prelude in preludes.items():
                with self.subTest(shell=shell):
                    result = subprocess.run(
                        [shell, "-c", prelude
                         + "source ./mnc; "
                           "complete -p mnc; "
                           'case $- in *e*) echo SET_E_LEAKED;; esac; '
                           "false; echo SHELL_SURVIVED"],
                        check=False, text=True, cwd=workspace,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    self.assertIn("-F _mnc mnc", result.stdout, result.stderr)
                    self.assertIn("SHELL_SURVIVED", result.stdout)
                    self.assertNotIn("SET_E_LEAKED", result.stdout)
                    # Sourcing must never run a build stage.
                    self.assertFalse(log.exists(), "sourcing ran a stage")

    def test_executing_still_dispatches_after_the_source_check(self):
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            helper.assertDispatch(workspace, "PL build --sdtgen", "PL --sdtgen")

    def test_completion_option_prints_a_sourceable_script(self):
        """"eval $(mnc --completion)" must see the script and nothing else."""
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            (workspace / ".monutchee-build/mnc-completion.bash").write_text(
                self.COMPLETION.read_text()
            )
            result = helper.run_mnc(workspace, "--completion")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("[monutchee]", result.stdout)
            self.assertIn("_mnc()", result.stdout)

            # It really registers when evaluated.
            registered = subprocess.run(
                ["bash", "-c",
                 f'eval "$(bash {workspace}/mnc --completion)" && complete -p mnc'],
                check=False, text=True, cwd=workspace,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            self.assertIn("-F _mnc mnc", registered.stdout)

    def test_case_insensitive_target_resolves_to_its_script(self):
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            command = str(workspace / "mnc")
            self.assertEqual(
                sorted(self.complete("bash", workspace, command, "pl")),
                sorted(self.complete("bash", workspace, command, "PL")),
            )

    def test_unknown_target_offers_nothing(self):
        helper = MncTests()
        with tempfile.TemporaryDirectory() as directory:
            workspace = helper.workspace(Path(directory))
            self.assertEqual(
                self.complete("bash", workspace, str(workspace / "mnc"), "APU"),
                [],
            )


class TuiStateTests(unittest.TestCase):
    def test_console_normalizes_ansi_and_carriage_return_updates(self):
        console = ConsoleBuffer()
        completed = console.feed(b"plain\n\x1b[31mold\x1b[0m\rnew\n")
        self.assertEqual(completed, ["plain", "new"])
        self.assertEqual(console.snapshot(), ["plain", "new"])

    def test_console_preserves_pty_crlf_lines_across_chunks(self):
        console = ConsoleBuffer()
        first = console.feed(b"Loading cache...done.\r\nBitBake running\r")
        second = console.feed(b"\n")
        self.assertEqual(first, ["Loading cache...done."])
        self.assertEqual(second, ["BitBake running"])
        self.assertEqual(
            console.snapshot(),
            ["Loading cache...done.", "BitBake running"],
        )

    def test_resource_monitor_reports_aggregate_cpu_memory_and_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stat = root / "stat"
            meminfo = root / "meminfo"
            stat.write_text("cpu  100 0 50 850 0 0 0 0 0 0\n")
            meminfo.write_text(
                "MemTotal:       32768 kB\n"
                "MemAvailable:    8192 kB\n"
                "SwapTotal:       8192 kB\n"
                "SwapFree:        6144 kB\n"
            )
            monitor = ResourceMonitor(str(stat), str(meminfo))
            first = monitor.sample(now=1.0, force=True)
            self.assertIsNone(first.cpu_percent)

            stat.write_text("cpu  150 0 70 880 0 0 0 0 0 0\n")
            usage = monitor.sample(now=2.0, force=True)
            self.assertAlmostEqual(usage.cpu_percent, 70.0)
            self.assertEqual(usage.memory_used_kib, 24576)
            self.assertEqual(usage.memory_total_kib, 32768)
            self.assertEqual(usage.swap_used_kib, 2048)
            self.assertEqual(usage.swap_total_kib, 8192)

    def test_resource_pane_has_an_independent_toggle(self):
        tui = Tui(None, "/mnc", [])
        self.assertTrue(tui.summary_visible)
        self.assertTrue(tui.resources_visible)
        tui.handle_key(ord("r"), 24)
        self.assertTrue(tui.summary_visible)
        self.assertFalse(tui.resources_visible)
        tui.handle_key(ord("R"), 24)
        self.assertTrue(tui.resources_visible)

    def test_resource_pane_is_aligned_below_build_summary(self):
        tui = Tui(None, "/mnc", [])
        window = mock.Mock()
        with mock.patch(
            "common.build.mnc_tui.curses.newwin", return_value=window
        ) as newwin:
            summary = tui.draw_summary(40, 160)
            tui.draw_resources(40, 160, summary)
        summary_call, resources_call = newwin.call_args_list
        summary_height, summary_width, summary_y, summary_x = summary_call.args
        resource_height, resource_width, resource_y, resource_x = resources_call.args
        self.assertEqual(resource_y, summary_y + summary_height + 1)
        self.assertEqual(resource_x, summary_x)
        self.assertEqual(summary_width, 72)
        self.assertEqual(resource_width, summary_width)
        self.assertEqual(resource_height, 6)

    def test_wide_summary_preserves_full_long_stage_detail(self):
        tui = Tui(None, "/mnc", [])
        stage = tui.ensure_stage("HLS")
        stage.status = "RUNNING"
        stage.percent = None
        stage.detail = "SlidingOneCycleRmsEngine: CO_SIMULATION"
        window = mock.Mock()
        with mock.patch(
            "common.build.mnc_tui.curses.newwin", return_value=window
        ):
            tui.draw_summary(20, 160)

        detail_call = next(
            call for call in window.addnstr.call_args_list
            if stage.detail in call.args[2]
        )
        self.assertTrue(detail_call.args[2].endswith("CO_SIMULATION"))
        self.assertLessEqual(len(detail_call.args[2]), detail_call.args[3])

    def test_resource_size_formatting(self):
        self.assertEqual(format_kib(32768), "32.0M")
        self.assertEqual(format_kib(32 * 1024 * 1024), "32.0G")

    def test_events_drive_stage_lifecycle_and_pl_progress(self):
        tui = Tui(None, "/mnc", [])
        tui.handle_event(b"MNC_EVENT\tbuild_start\t\t\tHLS PL RPU\n")
        tui.handle_event(b"MNC_EVENT\tstage_start\tPL\t0\tstarting\n")
        tui.inspect_console_line(
            "PL_BUILD_PROGRESS=synth_1 00:00:05 [#####.....] 50% Running"
        )
        self.assertEqual(list(tui.stages), ["HLS", "PL", "RPU"])
        self.assertEqual(tui.stages["PL"].status, "RUNNING")
        self.assertEqual(tui.stages["PL"].percent, 50)
        self.assertIn("synth_1", tui.stages["PL"].detail)

        tui.handle_event(b"MNC_EVENT\tstage_end\tPL\t100\tsuccess\n")
        self.assertEqual(tui.stages["PL"].status, "SUCCESS")
        self.assertEqual(tui.stages["PL"].percent, 100)

    def test_elapsed_format(self):
        self.assertEqual(format_elapsed(3661), "01:01:01")

    def test_preset_parser_explains_the_pyyaml_dependency(self):
        result = subprocess.run(
            [
                "python3", "-S", str(PRESET), "validate",
                "--preset", str(PRESET_TEMPLATE), "--known-stage", "PL",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PyYAML is required", result.stderr)


class ChainOrderTests(unittest.TestCase):
    """The per-product chain order, and the artifact-pruning hazard it avoids."""

    def profile(self, product: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in (BUILD_DIR / "products" / f"{product}.conf").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key] = value.strip('"')
        return values

    def chain(self, product: str) -> list[str]:
        chain = self.profile(product).get("MNC_CHAIN", "")
        self.assertTrue(chain, f"{product} declares no MNC_CHAIN")
        return chain.split()

    def test_every_product_declares_a_chain_of_installed_stages(self):
        for product in ("msap1", "zudemo", "kr260demo"):
            with self.subTest(product=product):
                for stage in self.chain(product):
                    self.assertTrue(
                        (BUILD_DIR / f"make_{stage}.sh").is_file(),
                        f"{product} chain names {stage}, but make_{stage}.sh"
                        " is not installed",
                    )

    def test_mconf_precedes_rpu_whenever_rpu_consumes_mconf(self):
        """Otherwise publishing mconf prunes the rpu artifact just built.

        artifact_finalize_hashed treats rpu as downstream of mconf when
        RPU_DEPENDS_ON_MCONF is true (its default), so a chain that runs RPU
        first would have the mconf stage delete that RPU artifact.
        """
        for product in ("msap1", "zudemo", "kr260demo"):
            with self.subTest(product=product):
                chain = self.chain(product)
                depends = self.profile(product).get(
                    "RPU_DEPENDS_ON_MCONF", "true"
                )
                self.assertIn("RPU", chain)
                self.assertIn("mconf", chain)
                if depends == "true":
                    self.assertLess(
                        chain.index("mconf"), chain.index("RPU"),
                        f"{product} has RPU_DEPENDS_ON_MCONF={depends}, so"
                        " mconf must run before RPU",
                    )

    def test_chain_ends_at_yocto_and_starts_with_a_hardware_stage(self):
        for product in ("msap1", "zudemo", "kr260demo"):
            with self.subTest(product=product):
                chain = self.chain(product)
                self.assertEqual(chain[-1], "yocto")
                self.assertIn(chain[0], ("HLS", "PL"))
                self.assertLess(chain.index("PL"), chain.index("yocto"))

    def test_mnc_refuses_to_guess_an_undeclared_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = MncTests()
            workspace = helper.workspace(Path(directory))
            profile = workspace / ".monutchee-build/products/msap1.conf"
            profile.write_text(
                "\n".join(
                    line for line in profile.read_text().splitlines()
                    if not line.startswith("MNC_CHAIN=")
                )
                + "\n"
            )
            for arguments in (
                ("all", "build"),
                ("--from", "PL", "all", "build"),
                ("--to", "PL", "all", "build"),
            ):
                with self.subTest(arguments=arguments):
                    result = helper.run_mnc(workspace, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("declares no MNC_CHAIN", result.stderr)
                    # One message: a die() that cannot stop mnc used to let it
                    # continue into raw bash errors.
                    self.assertNotIn("unbound variable", result.stderr)
                    self.assertNotIn("bad array subscript", result.stderr)
                    self.assertEqual(result.invocations, [])


class VscodeTemplateTests(unittest.TestCase):
    TEMPLATE = BUILD_DIR / "templates" / "vscode-settings.json"

    def render(self, prefix: str, web: bool) -> dict:
        text = self.TEMPLATE.read_text().replace("@PROJECT_PREFIX@", prefix)
        if not web:
            text = "\n".join(
                line for line in text.splitlines() if '_WEB"' not in line
            )
        return json.loads(text)

    def test_renders_to_strict_json_for_every_product(self):
        for prefix, web in (
            ("MSAP1", True), ("ZuBoardDemo", False), ("KR260Demo", False),
        ):
            with self.subTest(prefix=prefix):
                settings = self.render(prefix, web)
                self.assertEqual(
                    settings["cmake.sourceDirectory"],
                    "${workspaceFolder}/applications/" + f"{prefix}_APU",
                )
                repositories = settings["git.scanRepositories"]
                for suffix in ("APU", "RPU", "PL"):
                    self.assertIn(f"applications/{prefix}_{suffix}", repositories)
                self.assertEqual(
                    f"applications/{prefix}_WEB" in repositories, web
                )
                self.assertIn(
                    "yocto-build/sources/meta-monutchee", repositories
                )

    def test_carries_no_placeholder_after_rendering(self):
        self.assertNotIn("@PROJECT_PREFIX@", json.dumps(self.render("MSAP1", True)))

    def test_excludes_the_yocto_build_tree(self):
        settings = self.render("MSAP1", True)
        for key in ("files.exclude", "search.exclude"):
            self.assertTrue(settings[key]["yocto-build/build/**"])


if __name__ == "__main__":
    unittest.main()
