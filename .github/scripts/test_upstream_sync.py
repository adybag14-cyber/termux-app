import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('upstream_sync', Path(__file__).with_name('upstream_sync.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.previous = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, self.previous)
        self.git('init', '--initial-branch=master')
        self.git('config', 'user.name', 'Sync fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.write('app.txt', 'base\n')
        self.write('.github/workflows/ci.yml', 'fork workflow\n')
        self.commit('base')
        self.base = sync.commit('HEAD')
        self.git('branch', 'upstream')

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name, text):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-m', message)

    def incoming(self):
        self.git('checkout', 'upstream')
        self.write('app.txt', 'new app code\n')
        self.write('.github/workflows/ci.yml', 'updated upstream workflow\n')
        self.write('.github/workflows/new.yml', 'new upstream workflow\n')
        self.commit('upstream')
        tip = sync.commit('HEAD')
        self.git('checkout', 'master')
        return tip

    def prepare(self):
        return sync.prepare('upstream', self.root / 'review')

    def test_real_merge_retains_workflows_and_carries_source(self):
        upstream = self.incoming()
        report = self.prepare()
        self.assertEqual(Path('app.txt').read_text(), 'new app code\n')
        self.assertEqual(Path('.github/workflows/ci.yml').read_text(), 'fork workflow\n')
        self.assertFalse(Path('.github/workflows/new.yml').exists())
        self.assertEqual(self.git('show', '-s', '--format=%P', 'HEAD').split(), [self.base, upstream])
        self.assertEqual(len(report['incoming_workflow_changes']), 2)
        self.assertIn('new upstream workflow', (self.root / 'review/upstream-workflows.patch').read_text())
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_workflow_conflict_keeps_fork_definition(self):
        self.incoming()
        self.write('.github/workflows/ci.yml', 'reviewed fork update\n')
        self.commit('fork workflow')
        self.prepare()
        self.assertEqual(Path('.github/workflows/ci.yml').read_text(), 'reviewed fork update\n')

    def test_upstream_workflow_deletion_is_retained(self):
        self.git('checkout', 'upstream')
        self.git('rm', '.github/workflows/ci.yml')
        self.commit('upstream removes workflow')
        self.git('checkout', 'master')
        self.prepare()
        self.assertTrue(Path('.github/workflows/ci.yml').is_file())

    def test_source_conflict_aborts_owned_merge(self):
        self.incoming()
        self.write('app.txt', 'different fork change\n')
        self.commit('fork source')
        before = sync.commit('HEAD')
        with self.assertRaisesRegex(RuntimeError, 'non-workflow conflicts'):
            self.prepare()
        self.assertEqual(sync.commit('HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(Path('app.txt').read_text(), 'different fork change\n')

    def test_dirty_checkout_is_preserved(self):
        self.write('app.txt', 'user edit\n')
        with self.assertRaisesRegex(RuntimeError, 'clean checkout'):
            self.prepare()
        self.assertEqual(Path('app.txt').read_text(), 'user edit\n')
        self.assertFalse((self.root / 'review').exists())

    def test_already_synced_does_not_create_commit(self):
        report = self.prepare()
        self.assertFalse(report['synced'])
        self.assertEqual(sync.commit('HEAD'), self.base)
        self.assertEqual(report['incoming_workflow_changes'], [])
        self.assertFalse((self.root / 'review/upstream-workflows.patch').exists())
        self.assertTrue(all(path.stat().st_size > 0 for path in (self.root / 'review').iterdir()))

    def test_app_only_merge_has_nonempty_release_evidence(self):
        self.git('checkout', 'upstream')
        self.write('app.txt', 'application-only update\n')
        self.commit('app update')
        self.git('checkout', 'master')
        report = self.prepare()
        self.assertTrue(report['synced'])
        self.assertEqual(report['incoming_workflow_changes'], [])
        self.assertEqual(Path('app.txt').read_text(), 'application-only update\n')
        files = list((self.root / 'review').iterdir())
        self.assertEqual([path.name for path in files], ['upstream-sync.json'])
        self.assertTrue(all(path.stat().st_size > 0 for path in files))

    def test_publish_preserves_concurrent_remote_update(self):
        remote = self.root / 'remote.git'
        self.git('clone', '--bare', '.', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.incoming()
        report = self.prepare()
        self.git('push', 'origin', 'upstream:refs/heads/upstream-fixture')
        subprocess.run(['git', '--git-dir', str(remote), 'update-ref', 'refs/heads/master',
                        sync.commit('upstream')], check=True, capture_output=True)
        with self.assertRaisesRegex(RuntimeError, 'Remote master advanced'):
            sync.publish(report)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/master').split()[0], sync.commit('upstream'))

    def test_publish_fast_forwards_only_prepared_commit(self):
        remote = self.root / 'remote.git'
        self.git('clone', '--bare', '.', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.incoming()
        report = self.prepare()
        sync.publish(report)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/master').split()[0], report['release_commit'])


if __name__ == '__main__':
    unittest.main()
