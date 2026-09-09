"""Exercise the real HLS shell option forwarding without launching vendor tools."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOLKIT = Path(__file__).resolve().parents[1]


class HlsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(TOOLKIT / 'make_HLS.sh', self.root / 'make_HLS.sh')
        (self.root / 'build_hls_components.py').touch()
        (self.root / 'vitis-comp.json').write_text('{}')
        (self.root / 'libbuild.sh').write_text('''
default_workspace_root() { printf '%s' "$HLS_CLI_ROOT"; }
canonical_path() { printf '%s' "$1"; }
load_product_profile() {
    HLS_WORKSPACE="$HLS_CLI_ROOT"
    PL_ROOT="$HLS_CLI_ROOT"
    RUNTIME_DIR="$HLS_CLI_ROOT/runtime"
}
acquire_workspace_build_lock() { :; }
load_xilinx_environment() { :; }
require_command() { command -v "$1" >/dev/null; }
require_dir() { test -d "$1"; }
require_file() { test -f "$1"; }
log() { printf '%s\\n' "$*"; }
warn() { printf '%s\\n' "$*" >&2; }
die() { warn "$*"; exit 1; }
build_progress() { :; }
build_summary() { log "$*"; }
''')
        self.vitis = self.root / 'vitis-stub'
        self.vitis.write_text('#!/bin/bash\nprintf "VITIS_ARG=%s\\n" "$@"\nexit "${HLS_CLI_EXIT:-0}"\n')
        self.vitis.chmod(0o755)
        self.env = dict(os.environ, HLS_CLI_ROOT=str(self.root), VITIS=str(self.vitis),
                        MNC_BUILD_TARGET='', XILINX_VITIS_DATA_DIR=str(self.root / 'data'))

    def run_stage(self, *args):
        return subprocess.run(['bash', str(self.root / 'make_HLS.sh'), *args],
                              env=self.env, text=True, capture_output=True)

    def test_defaults_and_explicit_cosim(self):
        for options, expected, summary in [
            ((), '--skip-cosim', 'skipped'),
            (('--cosim',), '--cosim', 'enabled'),
            (('--cosim', '--skip-cosim'), '--skip-cosim', 'skipped'),
            (('--skip-cosim', '--cosim'), '--cosim', 'enabled'),
        ]:
            with self.subTest(options=options):
                result = self.run_stage(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = [line.removeprefix('VITIS_ARG=') for line in result.stdout.splitlines()
                        if line.startswith('VITIS_ARG=')]
                self.assertEqual(args[-1], expected)
                self.assertNotIn('--skip-csim', args)
                self.assertIn(f'cosim={summary}', result.stdout)

    def test_component_csim_controls_and_failure(self):
        self.env['HLS_CLI_EXIT'] = '7'
        result = self.run_stage('--cosim', '--component', 'Example', '--skip-csim')
        self.assertEqual(result.returncode, 7)
        self.assertIn('VITIS_ARG=Example', result.stdout)
        self.assertIn('VITIS_ARG=--skip-csim', result.stdout)
        self.assertNotIn('HLS IP repository is current', result.stdout)


if __name__ == '__main__':
    unittest.main()
