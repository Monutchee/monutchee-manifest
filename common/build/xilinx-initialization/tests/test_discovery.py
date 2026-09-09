"""Workspace discovery must not depend on a valid selected build target."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOOLKIT = Path(__file__).resolve().parents[1]


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.toolkit = self.root / '.monutchee-build'
        shutil.copytree(TOOLKIT, self.toolkit, ignore=shutil.ignore_patterns('tests', '__pycache__'))
        (self.toolkit / 'products').mkdir(exist_ok=True)
        (self.toolkit / 'products/test.conf').write_text(
            'PRODUCT=test\nMACHINE=board-a\nDEFAULT_BUILD_TARGET=board-a\nMNC_CHAIN="HLS PL RPU mconf yocto"\n')
        (self.toolkit / '.product').write_text('test\n')
        defs = self.toolkit / 'definitions/test'
        defs.mkdir(parents=True)
        (defs / 'targets.json').write_text(json.dumps({
            'board-a': {'supported': True, 'machine': 'machine-a'},
            'board-b': {'supported': True, 'machine': 'machine-b', 'supported_stages': ['HLS', 'PL']},
            'board-c': {'supported': False, 'reason': 'Carrier pending'},
        }))
        self.preset = self.root / 'MncBuildPreset.yaml'
        self.preset.write_text('build_target: board-c\n')
        self.env = dict(os.environ, MNC_NO_COMPLETION_INSTALL='1', PYTHONDONTWRITEBYTECODE='1')
        for key in ('MONUTCHEE_PRODUCT', 'MNC_BUILD_TARGET'):
            self.env.pop(key, None)

    def run_mnc(self, *args):
        return subprocess.run(['bash', str(self.toolkit/'mnc.sh'), *args],
            env=self.env, cwd=self.root, capture_output=True, text=True)

    def test_listing_works_with_unavailable_preset(self):
        result = self.run_mnc('list-build-target')
        self.assertEqual(result.returncode, 0, result.stderr)
        enabled, disabled = result.stdout.split('Unavailable targets')
        self.assertIn('board-a', enabled)
        self.assertIn('machine-b', enabled)
        self.assertNotIn('board-c', enabled)
        self.assertIn('board-c: Carrier pending', disabled)
        row = next(line for line in enabled.splitlines() if line.startswith('board-b'))
        self.assertIn('HLS PL', row)
        self.assertNotIn('RPU', row)
        self.assertFalse((self.root/'runtime-generated').exists())
        self.assertEqual(self.preset.read_text(), 'build_target: board-c\n')

    def test_help_lists_commands_even_with_malformed_preset(self):
        self.preset.write_text('invalid: [\n')
        result = self.run_mnc('help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ('list-build-target','PL summary','RPU elf-only','mconf status',
                        'yocto prepare-only','all status','deploy [jtag]','<stage> help'):
            self.assertIn(command, result.stdout)
        self.assertEqual(result.stdout, self.run_mnc('--help').stdout)
        self.assertFalse((self.root/'runtime-generated').exists())
        self.assertEqual(self.run_mnc('list-build-target').returncode, 0)

    def test_listing_rejects_extra_arguments(self):
        result = self.run_mnc('list-build-target','build')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('takes no arguments', result.stderr)
        self.assertFalse((self.root/'runtime-generated').exists())

    def test_completion_includes_new_commands(self):
        result = subprocess.run(['bash', '-c', '''source "$1"
COMP_WORDS=("$2" "")
COMP_CWORD=1
_mnc
printf '%s\\n' "${COMPREPLY[@]}"
''', 'bash', str(self.toolkit/'mnc-completion.bash'), str(self.toolkit/'mnc.sh')],
            env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ('help','list-build-target'):
            self.assertIn(command, result.stdout.splitlines())


if __name__ == '__main__':
    unittest.main()
