import importlib.util
import io
import json
import os
import shutil
import fcntl
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest

TOOLKIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT))
from build_target import resolve
from prepare_vitis_workspace import prepare


class BuildTargets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.preset = self.root / 'MncBuildPreset.yaml'
        self.preset.write_text('version: 1\nstages: {}\n')
        self.definitions = self.root / 'targets.json'
        self.profile = dict(supported=True, machine='meter-board-a',
                            pl_project='vivado_gen/meter-board-a/METER.xpr',
                            pl_create_script='projects/meter/board-a/system_project.tcl',
                            part='test-part', sdt_mode='board_dts', sdt_value='board-a',
                            mconf_template='yocto-build/sources/meta-meter/conf/machineyaml/board-a.yaml',
                            openamp_contract='definitions/meter/openamp-contract.json')
        self.definitions.write_text(json.dumps({
            'meter-board-a': self.profile,
            'meter-board-b': dict(self.profile, machine='meter-board-b'),
            'meter-future': dict(supported=False, reason='No carrier design'),
        }))

    def test_default_and_frozen_selection(self):
        values, defaulted = resolve(self.definitions, self.preset, 'meter-board-a')
        self.assertTrue(defaulted)
        self.assertEqual(values['MACHINE'], 'meter-board-a')
        self.preset.write_text('version: 1\nbuild_target: meter-board-b\n')
        values, defaulted = resolve(self.definitions, self.preset, 'meter-board-a')
        self.assertFalse(defaulted)
        self.assertEqual(values['MNC_BUILD_TARGET'], 'meter-board-b')
        frozen, _ = resolve(self.definitions, self.preset, 'meter-board-a', 'meter-board-a')
        self.assertEqual(frozen['MACHINE'], 'meter-board-a')

    def test_unknown_unavailable_and_unsafe_target(self):
        for target in ['other', 'meter-future', '../escape', True, None]:
            with self.subTest(target=target):
                self.preset.write_text(json.dumps(dict(version=1, build_target=target)))
                with self.assertRaises(ValueError):
                    resolve(self.definitions, self.preset, 'meter-board-a')
        self.profile['pl_project'] = '../outside.xpr'
        self.definitions.write_text(json.dumps({'meter-board-a': self.profile}))
        self.preset.write_text('version: 1\n')
        with self.assertRaises(ValueError):
            resolve(self.definitions, self.preset, 'meter-board-a')

    def artifact(self, *args, target='meter-board-a'):
        env = dict(os.environ, MNC_BUILD_TARGET=target, MNC_BUILD_MACHINE=target)
        return subprocess.run([sys.executable, str(TOOLKIT / 'artifact.py'), *args],
                              env=env, capture_output=True, text=True)

    def workspace(self):
        toolkit = self.root / '.monutchee-build'
        shutil.copytree(TOOLKIT, toolkit, ignore=shutil.ignore_patterns('tests', '__pycache__'))
        (toolkit / 'products').mkdir(exist_ok=True)
        (toolkit / 'products/meter.conf').write_text('''PRODUCT=meter
DEFAULT_BUILD_TARGET=meter-board-a
APU_REPO_DIR=APU
RPU_REPO_DIR=RPU
PL_REPO_DIR=PL
PL_XSA_BASENAME=METER.xsa
MNC_CHAIN="HLS PL"
''')
        (toolkit / '.product').write_text('meter\n')
        (toolkit / 'definitions/meter').mkdir(parents=True)
        shutil.copyfile(self.definitions, toolkit / 'definitions/meter/targets.json')
        for stage in ('HLS', 'PL'):
            (toolkit / f'make_{stage}.sh').write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "${MNC_BUILD_TARGET}" >> "${TEST_TARGET_LOG}"
printf 'version: 1\\nbuild_target: meter-board-b\\n' > "${TEST_PRESET}"
''')
        return toolkit

    def test_chain_freezes_target_even_when_preset_changes(self):
        toolkit = self.workspace()
        log = self.root / 'targets-built'
        env = dict(os.environ, TEST_TARGET_LOG=str(log), TEST_PRESET=str(self.preset))
        result = subprocess.run(['bash', str(toolkit / 'mnc.sh'), '--cli', 'all', 'build'],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['meter-board-a', 'meter-board-a'])
        reports = list((self.root / 'runtime-generated/meter-board-a/buildLog').glob('*.log'))
        self.assertEqual(len(reports), 1)
        self.assertFalse((self.root / 'runtime-generated/meter-board-b').exists())

    def test_lock_serializes_different_targets(self):
        toolkit = self.workspace()
        lock = self.root / 'runtime-generated/.work/build.lock'
        lock.parent.mkdir(parents=True)
        with lock.open('w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.preset.write_text('version: 1\nbuild_target: meter-board-b\n')
            result = subprocess.run(['bash', str(toolkit / 'mnc.sh'), '--cli', 'all', 'build'],
                                    cwd=self.root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Another build/deployment', result.stderr)

    def test_each_target_resolves_distinct_build_and_runtime_paths(self):
        toolkit = self.workspace()
        paths = []
        for target in ['meter-board-a', 'meter-board-b']:
            self.preset.write_text(f'version: 1\nbuild_target: {target}\n')
            result = subprocess.run(['bash', '-c',
                'source "$1/libbuild.sh"; WORKSPACE_ROOT="$2"; load_product_profile meter; '
                'printf "%s\\n" "$RUNTIME_DIR" "$YOCTO_BUILD_DIR" "$RPU_WORKSPACE" "$HLS_WORKSPACE"',
                'test', str(toolkit), str(self.root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = result.stdout.splitlines()
            self.assertEqual(selected[1], str(self.root / f'yocto-build/build-{target}'))
            self.assertTrue(all(target in value for value in selected))
            paths.append(set(selected))
        self.assertFalse(paths[0] & paths[1])

    def test_artifacts_reject_other_targets_and_legacy_before_extraction(self):
        payload = self.root / 'payload'
        payload.mkdir()
        (payload / 'firmware.elf').write_text('firmware-a')
        archive = self.root / 'rpu.tar.gz'
        result = self.artifact('create', '--product', 'meter', '--stage', 'rpu',
                               '--payload-root', str(payload), '--output', str(archive))
        self.assertEqual(result.returncode, 0, result.stderr)
        for target, expected in [('meter-board-a', 0), ('meter-board-b', 1)]:
            dest = self.root / target
            result = self.artifact('extract', '--product', 'meter', '--stage', 'rpu',
                                   '--archive', str(archive), '--directory', str(dest), target=target)
            self.assertEqual(result.returncode == 0, expected == 0, result.stderr)
            self.assertEqual(dest.exists(), expected == 0)
        result = self.artifact('create', '--product', 'meter', '--stage', 'rpu',
                               '--payload-root', str(payload), '--output', str(archive), target='')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.artifact('verify', '--product', 'meter', '--stage', 'rpu', '--archive', str(archive))
        self.assertNotEqual(result.returncode, 0)

    def test_sdk_context_selects_machine_only_after_mconf_and_is_idempotent(self):
        toolkit = self.workspace()
        conf = self.root / 'yocto-build/build-meter-board-a/conf'
        conf.mkdir(parents=True)
        (conf / 'local.conf').write_text('MACHINE ??= "qemuarm64"\n')

        def context():
            result = subprocess.run(['bash', '-c',
                'set -e; source "$1/libbuild.sh"; WORKSPACE_ROOT="$2"; '
                'load_product_profile meter; write_yocto_target_context',
                'test', str(toolkit), str(self.root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return (conf / 'mnc-target.conf').read_text()

        self.assertNotIn('MACHINE =', context())
        (conf / 'machine').mkdir()
        (conf / 'machine/meter-board-a.conf').write_text('# generated by mconf\n')
        text = context()
        self.assertIn('MACHINE = "meter-board-a"', text)
        self.assertIn('MNC_BUILD_TARGET = "meter-board-a"', text)
        self.assertIn('${TOPDIR}/../../runtime-generated/${MNC_BUILD_TARGET}', text)
        self.assertEqual(context(), text)
        self.assertEqual((conf / 'local.conf').read_text().count('require conf/mnc-target.conf'), 1)

    def test_vitis_inputs_are_private_and_removed_sources_do_not_linger(self):
        source = self.root / 'source'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        (source / '.gitignore').write_text('build/\n')
        (source / 'app.cpp').write_text('first')
        (source / 'build').mkdir()
        (source / 'build/stale.elf').write_text('stale')
        a, b = self.root / 'a', self.root / 'b'
        prepare(source, a)
        prepare(source, b)
        self.assertFalse((a / 'build').exists())
        (a / 'app.cpp').unlink()
        (a / 'app.cpp').symlink_to(source / 'app.cpp')
        prepare(source, a)
        self.assertFalse((a / 'app.cpp').is_symlink())
        (a / 'app.cpp').write_text('tool modified input')
        (a / 'build').mkdir()
        (a / 'build/app.elf').write_text('built')
        self.assertEqual((source / 'app.cpp').read_text(), 'first')
        self.assertEqual((b / 'app.cpp').read_text(), 'first')
        (source / 'app.cpp').unlink()
        (source / 'new.cpp').write_text('second')
        prepare(source, a)
        self.assertFalse((a / 'app.cpp').exists())
        self.assertTrue((a / 'build/app.elf').exists())
        self.assertEqual((a / 'new.cpp').read_text(), 'second')

    def test_station_checks_machine_before_invoking_client(self):
        toolkit = self.workspace()
        with (toolkit / 'products/meter.conf').open('a') as profile:
            profile.write('JTAG_ARTIFACT_NAME=station.tar.gz\n')
        marker = self.root / 'client-called'
        # A local stub records invocation; this test makes no network requests.
        (toolkit / 'station_client.py').write_text(
            'import os\nfrom pathlib import Path\nPath(os.environ["TEST_CLIENT_MARKER"]).touch()\n')
        archive = self.root / 'station.tar.gz'
        for product, machine, accepted in [
            ('meter', 'meter-board-b', False),
            ('other', 'meter-board-a', False),
            ('meter', 'meter-board-a', True),
        ]:
            data = json.dumps(dict(artifact=dict(product=product, machine=machine))).encode()
            with tarfile.open(archive, 'w:gz') as tar:
                info = tarfile.TarInfo('manifest.json')
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            result = subprocess.run([
                'bash', str(toolkit / 'make_deploy.sh'), '--workspace', str(self.root),
                '--product', 'meter', '--type', 'jtag', '--artifact', str(archive),
                '--station-url', 'http://127.0.0.1:9',
                '--xilinx-hw-server-url', 'tcp:127.0.0.1:3121', '--tftp-server-ip', '127.0.0.1',
            ], env=dict(os.environ, TEST_CLIENT_MARKER=str(marker)), capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, accepted, result.stderr)
            self.assertEqual(marker.exists(), accepted)


if __name__ == '__main__':
    unittest.main()
