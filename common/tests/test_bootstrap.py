"""Exercise the exact sh -s entry point with local Git remotes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMMON = Path(__file__).resolve().parents[1]
BOOTSTRAP = (COMMON / 'bootstrap').read_text()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bootstrap-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        for key in tuple(self.env):
            if key.startswith(('MONUTCHEE_', 'MANIFEST_', 'GIT_')):
                self.env.pop(key)
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        TMPDIR=str(self.root), GIT_TERMINAL_PROMPT='0')
        self.shared = self.root / 'shared-remote'
        self.project = self.root / 'project-remote'
        self.workspace = self.root / 'workspace with spaces'
        (self.shared / 'common/build').mkdir(parents=True)
        shutil.copy2(COMMON / 'setupWorkspace', self.shared / 'common/setupWorkspace')
        shutil.copytree(COMMON / 'build/xilinx-initialization',
                        self.shared / 'common/build/xilinx-initialization',
                        ignore=shutil.ignore_patterns('__pycache__', 'tests'))
        self.project.mkdir()
        (self.project / 'product.conf').write_text(
            'PRODUCT=sample\nVENDOR=xilinx\nPROJECT_PREFIX=Sample\n'
            'APU_REPO_DIR=Sample_APU\nRPU_REPO_DIR=Sample_RPU\nPL_REPO_DIR=Sample_PL\n'
            'MNC_CHAIN="PL mconf RPU yocto"\n')
        for repo in (self.shared, self.project):
            self.git('init', '-b', 'main', repo)
            self.git('-C', repo, 'add', '.')
            self.git('-C', repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                     'commit', '-m', 'fixture')

    def git(self, *args):
        subprocess.run(['git', *map(str, args)], check=True, capture_output=True, env=self.env)

    def run_bootstrap(self, *args):
        # stdin matches curl ... | sh -s -- ...; no prior shared checkout exists.
        return subprocess.run(['sh', '-s', '--', *map(str, args)], input=BOOTSTRAP,
                              cwd=self.root, capture_output=True, text=True, env=self.env)

    def arguments(self):
        return ['--shared-url', self.shared.as_uri(), '--project', 'sample',
                '--manifest-url', self.project.as_uri(), '--workspace', self.workspace]

    def assert_cleaned(self):
        self.assertEqual(list(self.root.glob('monutchee-bootstrap.*')), [])

    def test_piped_bootstrap_installs_workspace_and_refreshes_from_remote(self):
        result = self.run_bootstrap(*self.arguments(), 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        toolkit = self.workspace / '.monutchee-build'
        self.assertEqual((toolkit / '.product').read_text(), 'sample\n')
        self.assertTrue((self.workspace / 'mnc').is_file())
        self.assert_cleaned()
        (toolkit / 'make_PL.sh').write_text('stale toolkit')
        (self.workspace / 'MncBuildPreset.yaml').write_text('user settings\n')
        (self.workspace / '.monutchee-workspace').write_text('sample\n')
        result = self.run_bootstrap(*self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual((toolkit / 'make_PL.sh').read_text(), 'stale toolkit')
        self.assertEqual((self.workspace / 'MncBuildPreset.yaml').read_text(), 'user settings\n')
        self.assert_cleaned()

    def test_private_project_access_error_is_preserved_and_sources_cleaned(self):
        result = self.run_bootstrap(*self.arguments(), '--manifest-url', self.root / 'denied.git', 'scripts')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('access rights/SSH credentials', result.stderr)
        self.assertFalse(self.workspace.exists())
        self.assert_cleaned()

    def test_missing_shared_branch_has_actionable_error(self):
        result = self.run_bootstrap(*self.arguments(), '--shared-branch', 'missing', 'scripts')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot fetch shared tools', result.stderr)
        self.assert_cleaned()

    def test_shared_and_project_branches_are_independent(self):
        self.git('-C', self.shared, 'branch', 'tools-branch')
        self.git('-C', self.project, 'switch', '-c', 'project-branch')
        profile = self.project / 'product.conf'
        profile.write_text(profile.read_text() + 'BRANCH_MARKER=selected\n')
        self.git('-C', self.project, 'add', 'product.conf')
        self.git('-C', self.project, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-m', 'branch profile')
        result = self.run_bootstrap(*self.arguments(), '--shared-branch', 'tools-branch',
                                    '--branch', 'project-branch', 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BRANCH_MARKER=selected',
                      (self.workspace / '.monutchee-build/products/sample.conf').read_text())
        self.assert_cleaned()

    def test_help_and_invalid_options_do_not_fetch(self):
        for args, status in ((('--help',), 0), (('--project',), 1),
                             (('--unknown',), 1), (('--shared-branch',), 1)):
            with self.subTest(args=args):
                result = self.run_bootstrap(*args)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertNotIn('Fetching shared', result.stdout)
                self.assert_cleaned()


if __name__ == '__main__':
    unittest.main()
