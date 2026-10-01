"""Exercise release selection and the real shell loader without vendor launches."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOLKIT = Path(__file__).resolve().parents[1]


class XilinxToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install_root = self.root / "SDKs with spaces"
        self.preset = self.root / "MncBuildPreset.yaml"
        self.preset.write_text("version: 1\nstages: {}\n")
        self.toolkit = self.root / ".monutchee-build"
        self.toolkit.mkdir()
        for name in ("libbuild.sh", "xilinx_toolchain.py", "preset.py"):
            shutil.copy2(TOOLKIT / name, self.toolkit / name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("XILINX_", "MNC_", "MONUTCHEE_"))
                    and key not in ("VIVADO", "VITIS", "SDTGEN", "XSDB")}
        self.env["PATH"] = "/usr/bin:/bin"
        self.invocations = self.root / "vendor-invocations"

    def sdk(self, version, legacy=False):
        directories = {}
        for component, names in (("Vivado", ("vivado", "sdtgen")), ("Vitis", ("vitis", "xsdb"))):
            directory = (self.install_root / component / version if legacy
                         else self.install_root / version / component)
            (directory / "bin").mkdir(parents=True)
            directories[component] = directory
            for name in names:
                tool = directory / "bin" / name
                tool.write_text(f"#!/bin/bash\nprintf '%s\\n' {name} >> '{self.invocations}'\nexit 99\n")
                tool.chmod(0o755)
        for directory in directories.values():
            (directory / "settings64.sh").write_text(
                f"export PATH='{directories['Vitis'] / 'bin'}:{directories['Vivado'] / 'bin'}':$PATH\n"
                f"export XILINX_VIVADO='{directories['Vivado']}'\n"
                f"export XILINX_VITIS='{directories['Vitis']}'\n"
                "export TEST_SETTINGS_SOURCED=yes\n")
        return directories

    def path_for(self, directories):
        return f"{directories['Vivado'] / 'bin'}:{directories['Vitis'] / 'bin'}:/usr/bin:/bin"

    def load(self, required="vitis"):
        script = '''set -Eeuo pipefail
source "$1/libbuild.sh"
WORKSPACE_ROOT="$2"
VITIS="${VITIS:-vitis}"
load_xilinx_environment "$3"
python3 - <<'PY'
import json, os
print(json.dumps({key: os.environ.get(key) for key in (
    'XILINX_VERSION', 'XILINX_ROOT', 'XILINX_VIVADO', 'XILINX_VITIS',
    'VIVADO', 'VITIS', 'SDTGEN', 'TEST_SETTINGS_SOURCED')}))
PY
'''
        return subprocess.run(["bash", "-c", script, "test", str(self.toolkit), str(self.root), required],
                              env=self.env, capture_output=True, text=True)

    def values(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.invocations.exists(), "Selection must not launch vendor tools")
        return json.loads(result.stdout.splitlines()[-1])

    def test_auto_uses_path_and_matching_rpu_sdk(self):
        directories = self.sdk("2026.1")
        self.sdk("2025.2")
        self.env["PATH"] = self.path_for(directories)
        values = self.values(self.load())
        self.assertEqual(values["XILINX_VERSION"], "2026.1")
        self.assertEqual(values["XILINX_VITIS"], str(directories["Vitis"]))
        self.assertEqual(values["VIVADO"], str(directories["Vivado"] / "bin/vivado"))
        self.assertIsNone(values["TEST_SETTINGS_SOURCED"])

    def test_auto_does_not_select_newest_installed_release(self):
        older = self.sdk("2025.2")
        self.sdk("2026.1")
        self.env["PATH"] = self.path_for(older)
        self.assertEqual(self.values(self.load())["XILINX_VERSION"], "2025.2")

    def test_pin_overrides_old_path_and_uses_component_settings(self):
        older = self.sdk("2025.2")
        newer = self.sdk("2026.1")
        self.env["PATH"] = self.path_for(older)
        self.preset.write_text(json.dumps({"version": 1, "xilinx": {
            "version": "2026.1", "install_root": str(self.install_root)}}))
        values = self.values(self.load())
        self.assertEqual(values["VITIS"], str(newer["Vitis"] / "bin/vitis"))
        self.assertEqual(values["XILINX_VITIS"], str(newer["Vitis"]))
        self.assertEqual(values["TEST_SETTINGS_SOURCED"], "yes")

    def test_environment_pin_overrides_preset(self):
        older = self.sdk("2025.2")
        newer = self.sdk("2026.1")
        self.env.update(PATH=self.path_for(older), XILINX_VERSION="2026.1", XILINX_ROOT=str(self.install_root))
        self.preset.write_text('version: 1\nxilinx:\n  version: "2025.2"\n')
        self.assertEqual(self.values(self.load())["XILINX_VIVADO"], str(newer["Vivado"]))

    def test_mixed_path_is_rejected_before_vendor_launch(self):
        older = self.sdk("2025.2")
        newer = self.sdk("2026.1")
        self.env["PATH"] = f"{newer['Vivado'] / 'bin'}:{older['Vitis'] / 'bin'}:/usr/bin:/bin"
        result = self.load()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mixed Xilinx installations", result.stderr)
        self.assertFalse(self.invocations.exists())

    def test_pin_rejects_conflicting_explicit_tool(self):
        older = self.sdk("2025.2")
        self.sdk("2026.1")
        self.env.update(XILINX_VERSION="2026.1", XILINX_ROOT=str(self.install_root),
                        VITIS=str(older["Vitis"] / "bin/vitis"))
        result = self.load(self.env["VITIS"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VITIS override selects 2025.2", result.stderr)

    def test_symlinks_and_older_directory_layout(self):
        directories = self.sdk("2025.2", legacy=True)
        links = self.root / "bin"
        links.mkdir()
        for component, names in (("Vivado", ("vivado", "sdtgen")), ("Vitis", ("vitis", "xsdb"))):
            for name in names:
                (links / name).symlink_to(directories[component] / "bin" / name)
        self.env["PATH"] = f"{links}:/usr/bin:/bin"
        values = self.values(self.load())
        self.assertEqual(values["XILINX_VERSION"], "2025.2")
        self.assertEqual(values["XILINX_VITIS"], str(directories["Vitis"]))

    def test_pin_recovers_missing_path(self):
        directories = self.sdk("2026.1")
        self.env.update(XILINX_VERSION="2026.1", XILINX_ROOT=str(self.install_root))
        self.assertEqual(self.values(self.load())["VITIS"], str(directories["Vitis"] / "bin/vitis"))

    def test_no_path_does_not_guess_an_installed_release(self):
        self.sdk("2026.1")
        result = self.load()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot detect the Xilinx release", result.stderr)

    def test_opaque_wrapper_is_kept_with_identifiable_sdk(self):
        directories = self.sdk("2026.1")
        self.env["PATH"] = self.path_for(directories)
        wrapper = self.root / "vitis-wrapper"
        wrapper.write_text("#!/bin/bash\nexit 99\n")
        wrapper.chmod(0o755)
        self.env["VITIS"] = str(wrapper)
        self.assertEqual(self.values(self.load(str(wrapper)))["VITIS"], str(wrapper))

    def test_explicit_settings_can_identify_a_missing_path(self):
        directories = self.sdk("2026.1")
        self.env["XILINX_SETTINGS"] = str(directories["Vitis"] / "settings64.sh")
        self.assertEqual(self.values(self.load())["XILINX_VERSION"], "2026.1")

    def test_missing_matching_tool_is_not_replaced_by_an_old_path_tool(self):
        newer = self.sdk("2026.1")
        older = self.sdk("2025.2")
        (newer["Vitis"] / "bin/vitis").unlink()
        self.env.update(PATH=self.path_for(older), XILINX_VERSION="2026.1",
                        XILINX_ROOT=str(self.install_root))
        result = self.load()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong to Xilinx 2026.1", result.stderr)
        self.assertFalse(self.invocations.exists())

    def test_repeated_load_does_not_source_an_unused_settings_override(self):
        directories = self.sdk("2026.1")
        self.env.update(PATH=self.path_for(directories),
                        XILINX_SETTINGS="/must/not/be/sourced/settings64.sh")
        result = subprocess.run(["bash", "-c", '''set -Eeuo pipefail
source "$1/libbuild.sh"
WORKSPACE_ROOT="$2"
load_xilinx_environment vivado
load_xilinx_environment sdtgen
printf '%s\\n' "$XILINX_VERSION"
''', "test", str(self.toolkit), str(self.root)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "2026.1")

    def test_bad_config_is_rejected(self):
        for xilinx in ({"version": 2026.1}, {"version": "latest"},
                       {"install_root": "relative/path"}, {"typo": "2026.1"}):
            with self.subTest(xilinx=xilinx):
                self.preset.write_text(json.dumps({"version": 1, "xilinx": xilinx}))
                result = subprocess.run(["python3", str(self.toolkit / "preset.py"), "validate",
                                         "--preset", str(self.preset)], env=self.env,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
