"""Status must report provenance honestly and never trigger mutable builds."""
import argparse
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLKIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT))
from artifact import create, sha256_file
from stage_status import Snapshot


class StageStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'runtime-generated/test-board/bin_file'
        self.bin.mkdir(parents=True)
        self.xsa = self.bin / 'test.xsa'
        self.xsa.write_bytes(b'xsa')
        self.args = argparse.Namespace(stage='rpu', product='test', target='test-board',
            machine='test-machine', bin_dir=self.bin, xsa=self.xsa,
            contract=None, yocto_build=self.root / 'yocto-build/build-test-machine')

    def artifact(self, stage, metadata=None, payload=None):
        source = self.root / (stage + '-payload')
        source.mkdir(exist_ok=True)
        for name, content in (payload or {'file': b'data'}).items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        meta = dict(build_target='test-board', machine='test-machine',
                    xsa_sha256=sha256_file(self.xsa))
        meta.update(metadata or {})
        path = self.bin / f'test_{stage}_000000.tar.gz'
        with contextlib.redirect_stdout(io.StringIO()):
            create(argparse.Namespace(payload_root=str(source), output=str(path),
                product='test', stage=stage, metadata=[f'{k}={v}' for k,v in meta.items()],
                hash_filename=False))
        return path

    def report(self, stage):
        self.args.stage = stage
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            Snapshot(self.args).run()
        return out.getvalue()

    def test_missing_artifacts_are_a_report_not_a_build(self):
        for stage in ('rpu','mconf','yocto'):
            self.assertIn('artifact missing', self.report(stage))
        self.assertFalse(self.args.yocto_build.exists())

    def test_compatible_rpu_then_changed_xsa_and_local_elf(self):
        dep = self.artifact('mconf')
        self.artifact('rpu', {'mconf_sha256': sha256_file(dep)},
                      {'R5c0.elf': b'core0', 'R5c1.elf': b'core1'})
        for core in (0,1):
            (self.bin / f'R5c{core}.elf').write_bytes(f'core{core}'.encode())
        self.assertIn('recorded inputs match', self.report('rpu'))
        self.xsa.write_bytes(b'new xsa')
        (self.bin/'R5c0.elf').write_bytes(b'new elf')
        result = self.report('rpu')
        self.assertIn('RPU_STATUS_XSA=mismatch', result)
        self.assertIn('differs from packaged output', result)

    def test_wrong_target_and_corrupt_manifest_never_report_compatible(self):
        path = self.artifact('rpu', {'build_target':'other-board'})
        self.assertIn('build_target mismatch', self.report('rpu'))
        path.write_bytes(b'not a tar')
        self.assertIn('artifact invalid', self.report('rpu'))

    def test_mconf_checks_installed_config_and_sdt_digest(self):
        dep = self.artifact('pl_sdtgen')
        self.artifact('mconf', {'pl_sdtgen_sha256': sha256_file(dep)},
                      {'yocto-conf/machine/test-machine.conf': b'conf'})
        self.assertIn('1 missing', self.report('mconf'))
        dest = self.args.yocto_build/'conf/machine/test-machine.conf'
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b'conf')
        self.assertIn('recorded inputs match', self.report('mconf'))
        dest.write_bytes(b'changed')
        self.assertIn('1 changed', self.report('mconf'))

    def test_yocto_detects_replaced_upstream_artifact(self):
        rpu, mconf = self.artifact('rpu'), self.artifact('mconf')
        self.artifact('yocto', {'rpu_sha256':sha256_file(rpu),
                               'mconf_sha256':sha256_file(mconf), 'image_target':'test-image'})
        self.assertIn('recorded inputs match', self.report('yocto'))
        self.artifact('rpu', {'build_mode':'new-build'})
        result = self.report('yocto')
        self.assertIn('YOCTO_STATUS_RPU_INPUT=mismatch', result)
        self.assertNotIn('recorded inputs match', result)


class StatusDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.toolkit = self.root / '.monutchee-build'
        shutil.copytree(TOOLKIT, self.toolkit, ignore=shutil.ignore_patterns('tests','__pycache__'))
        (self.toolkit/'products').mkdir(exist_ok=True)
        (self.toolkit/'products/test.conf').write_text('''MACHINE=test-machine
APU_REPO_DIR=APU
RPU_REPO_DIR=RPU
PL_REPO_DIR=PL
PL_XSA_BASENAME=test.xsa
MNC_CHAIN="HLS PL RPU mconf yocto"
''')
        (self.toolkit/'.product').write_text('test\n')
        self.env = dict(os.environ, MNC_SKIP_COMPLETION_INSTALL='1', PYTHONDONTWRITEBYTECODE='1')
        for name in ('MNC_BUILD_TARGET','MNC_BUILD_MACHINE','MNC_SUPPORTED_STAGES','MONUTCHEE_PRODUCT'):
            self.env.pop(name,None)
        self.stub_log = self.root/'stages.log'
        self.env['STATUS_TEST_LOG'] = str(self.stub_log)
        # Only PL launches Vivado; replace it with a read-only-query recording stub.
        (self.toolkit/'make_PL.sh').write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$STATUS_TEST_LOG"
exit "${STATUS_TEST_PL_EXIT:-0}"
''')

    def run_mnc(self, *args):
        return subprocess.run(['bash',str(self.toolkit/'mnc.sh'),*args],
                              cwd=self.root,env=self.env,capture_output=True,text=True)

    def test_chain_queries_all_stages_without_creating_runtime(self):
        result=self.run_mnc('all','status')
        self.assertEqual(result.returncode,0,result.stderr)
        for name in ('HLS','RPU','MCONF','YOCTO'):
            self.assertIn(name+'_STATUS_VERDICT=',result.stdout)
        self.assertIn('--status',self.stub_log.read_text())
        self.assertFalse((self.root/'runtime-generated').exists())
        self.assertFalse((self.root/'yocto-build').exists())

    def test_chain_continues_after_query_failure_and_supports_range(self):
        self.env['STATUS_TEST_PL_EXIT']='9'
        result=self.run_mnc('--from','PL','--to','mconf','all','status')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('PL_STATUS_ERROR=query failed',result.stdout)
        self.assertIn('MCONF_STATUS_VERDICT=',result.stdout)
        self.assertNotIn('YOCTO_STATUS',result.stdout)
        self.assertNotIn('HLS_STATUS',result.stdout)

    def test_status_works_while_build_lock_is_held(self):
        directory=self.root/'runtime-generated/.work'
        directory.mkdir(parents=True)
        with (directory/'build.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.run_mnc('RPU','status')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'runtime-generated/bin_file').exists())

    def test_status_rejects_build_options_and_dry_run_runs_no_stage(self):
        result=self.run_mnc('RPU','status','--elf-only')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Unsupported status option',result.stderr)
        result=self.run_mnc('--dry-run','all','status')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.stub_log.exists())
        self.assertFalse((self.root/'runtime-generated').exists())

    def test_unsupported_target_reports_without_bypassing_build_gate(self):
        p=self.toolkit/'products/test.conf'
        p.write_text(p.read_text()+'MNC_SUPPORTED_STAGES="HLS PL"\nMNC_BUILD_TARGET=test-board\n')
        for args in [('RPU','status'),('all','status')]:
            result=self.run_mnc(*args)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('unsupported for this target',result.stdout)
        result=self.run_mnc('--cli','--dry-run','RPU','build')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('does not yet support RPU',result.stderr)


if __name__ == '__main__':
    unittest.main()
