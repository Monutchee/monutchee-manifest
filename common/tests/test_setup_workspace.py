"""Project discovery and vendor boundary tests; no external network or hardware."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project-setup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / "shared"
        (self.shared / "common").mkdir(parents=True)
        shutil.copy2(SOURCE / "setupWorkspace", self.shared / "common/setupWorkspace")
        (self.shared / "projects").mkdir()
        self.script = self.shared / "common/setupWorkspace"
        self.env = os.environ.copy()
        for key in tuple(self.env):
            if key.startswith(('MONUTCHEE_', 'MANIFEST_', 'GIT_')):
                self.env.pop(key)
        self.env['GIT_CONFIG_GLOBAL'] = os.devnull
        self.env['GIT_CONFIG_SYSTEM'] = os.devnull
        self.env['GIT_TERMINAL_PROMPT'] = '0'
        self.backend = self.shared / "common/build/example-initialization"
        self.backend.mkdir(parents=True)
        (self.backend / "workspace.sh").write_text(
            'initialize_workspace() { printf "vendor=%s project=%s args=%s\\n" "$VENDOR" "$MONUTCHEE_PRODUCT" "$*"; }\n')

    def run_setup(self, *args):
        return subprocess.run(['bash', str(self.script), *map(str, args)],
                              capture_output=True, text=True, env=self.env)

    def project(self, parent=None, name='sample', vendor='example'):
        project = (parent or self.root) / f'{name}-manifest'
        project.mkdir(parents=True)
        (project / 'product.conf').write_text(f'PRODUCT="{name}"\nVENDOR="{vendor}"\n')
        return project

    def test_list_does_not_execute_profiles_and_deduplicates_symlinks(self):
        project = self.project()
        marker = self.root / 'executed'
        with (project / 'product.conf').open('a') as f:
            f.write(f'touch "{marker}"\n')
        (self.shared / 'projects/sample-manifest').symlink_to(project)
        result = self.run_setup('--list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f'sample\t{project}'])
        self.assertFalse(marker.exists())

    def test_selects_sibling_and_dispatches_arbitrary_vendor(self):
        self.project()
        result = self.run_setup('--project', 'sample', 'flash')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vendor=example project=sample args=flash', result.stdout)

    def test_explicit_directory_handles_spaces(self):
        project = self.project(self.root / 'directory with spaces')
        result = self.run_setup('--manifest-dir', project, 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_name_requires_explicit_directory(self):
        self.project()
        self.project(self.shared / 'projects')
        result = self.run_setup('--project', 'sample')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Multiple matching', result.stderr)

    def test_missing_project_does_not_fetch_implicitly(self):
        result = self.run_setup('--project', 'missing')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No project selected/found', result.stderr)
        self.assertEqual(list((self.shared / 'projects').iterdir()), [])

    def test_unknown_vendor_fails_before_workspace_creation(self):
        project = self.project(vendor='unimplemented')
        result = self.run_setup('--manifest-dir', project, '--workspace', self.root / 'workspace')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported vendor', result.stderr)
        self.assertFalse((self.root / 'workspace').exists())

    def test_profile_identity_must_match_selection(self):
        project = self.project()
        (project / 'product.conf').write_text('PRODUCT=another\nVENDOR=example\n')
        result = self.run_setup('--project', 'sample')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match', result.stderr)

    def test_invalid_identifiers_and_missing_arguments(self):
        for args in (('--project', '../escape'), ('--project',), ('--branch',), ('--bad',)):
            with self.subTest(args=args):
                result = self.run_setup(*args)
                self.assertNotEqual(result.returncode, 0)

    def git(self, *args):
        subprocess.run(['git', *map(str, args)], check=True, capture_output=True, env=self.env)

    def test_fetches_explicit_project_from_local_remote(self):
        # Keep the remote outside discovery, and use real Git to verify cloning.
        project = self.project(self.root / 'remote-sources')
        self.git('init', '-b', 'main', project)
        self.git('-C', project, 'add', 'product.conf')
        self.git('-C', project, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-m', 'fixture')
        result = self.run_setup('--project', 'sample', '--fetch', '--manifest-url', project, 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.shared / 'projects/sample-manifest/.git').is_dir())
        result = self.run_setup('--project', 'sample', '--check-access')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_access_failure_is_clear_and_leaves_no_project(self):
        result = self.run_setup('--project', 'sample', '--fetch', '--manifest-url', self.root / 'unavailable.git')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('access rights/SSH credentials', result.stderr)
        self.assertEqual(list((self.shared / 'projects').iterdir()), [])

    def test_clone_failure_cleans_staging_directory(self):
        tools = self.root / 'tools'
        tools.mkdir()
        git = tools / 'git'
        git.write_text('#!/bin/sh\nif [ "$1" = clone ]; then exit 1; fi\nexit 0\n')
        git.chmod(0o755)
        self.env['PATH'] = f"{tools}:{self.env['PATH']}"
        result = self.run_setup('--project', 'sample', '--fetch', '--manifest-url', 'unavailable')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Project clone failed', result.stderr)
        self.assertEqual(list((self.shared / 'projects').iterdir()), [])

    def test_xilinx_installs_only_selected_profile_and_preserves_user_settings(self):
        shutil.copytree(SOURCE / 'build/xilinx-initialization',
                        self.shared / 'common/build/xilinx-initialization',
                        ignore=shutil.ignore_patterns('__pycache__'))
        project = self.project(vendor='xilinx')
        with (project / 'product.conf').open('a') as f:
            f.write('PROJECT_PREFIX=Sample\nAPU_REPO_DIR=Sample_APU\nRPU_REPO_DIR=Sample_RPU\n'
                    'PL_REPO_DIR=Sample_PL\nMNC_CHAIN="PL mconf RPU yocto"\n')
        (project / 'definition').mkdir()
        (project / 'definition/resource.txt').write_text('project resource')
        workspace = self.root / 'workspace'
        result = self.run_setup('--project', 'sample', '--workspace', workspace, 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        toolkit = workspace / '.monutchee-build'
        self.assertEqual([p.name for p in (toolkit / 'products').iterdir()], ['sample.conf'])
        self.assertEqual((toolkit / 'definitions/sample/resource.txt').read_text(), 'project resource')
        self.assertFalse((toolkit / 'tests').exists())
        self.assertFalse((toolkit / 'workspace.sh').exists())
        self.assertTrue((toolkit / 'vitis_hls_client.py').is_file())
        self.assertTrue((workspace / 'mnc').is_symlink())
        (workspace / 'MncBuildPreset.yaml').write_text('user settings\n')
        (workspace / '.monutchee-workspace').write_text('sample\n')
        result = self.run_setup('--project', 'sample', '--workspace', workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((workspace / 'MncBuildPreset.yaml').read_text(), 'user settings\n')


if __name__ == '__main__':
    unittest.main()
