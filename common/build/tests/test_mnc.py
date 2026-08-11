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


BUILD_DIR = Path(__file__).resolve().parents[1]
MNC = BUILD_DIR / "mnc.sh"
LIBBUILD = BUILD_DIR / "libbuild.sh"

STAGES = ("HLS", "PL", "RPU", "mconf", "yocto")

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
        (workspace / "runtime-generated" / "bin_file").mkdir(parents=True)

        toolkit.joinpath("mnc.sh").write_text(MNC.read_text())
        toolkit.joinpath("libbuild.sh").write_text(LIBBUILD.read_text())
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
