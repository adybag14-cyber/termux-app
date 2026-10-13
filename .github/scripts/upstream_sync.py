"""Merge upstream source while retaining this fork's reviewed workflow definitions."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

WORKFLOWS = '.github/workflows'


def git(*args, check=True):
    result = subprocess.run(['git', *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8')
    if check and result.returncode:
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.strip()}')
    return result


def commit(ref):
    value = git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').stdout.strip()
    if not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('Expected a SHA-1 commit identity')
    return value


def prepare(upstream_ref, report_dir):
    report_dir = Path(report_dir).resolve()
    repo = Path(git('rev-parse', '--show-toplevel').stdout.strip()).resolve()
    if report_dir == repo or repo in report_dir.parents:
        raise ValueError('Write review evidence outside the working tree')
    if git('status', '--porcelain', '--untracked-files=all').stdout:
        raise RuntimeError('Upstream sync requires a clean checkout')
    if git('rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0:
        raise RuntimeError('An existing merge must be resolved by its owner')
    before, upstream = commit('HEAD'), commit(upstream_ref)
    base = git('merge-base', before, upstream).stdout.strip()
    changed = git('diff', '--name-only', '-z', base, upstream, '--', WORKFLOWS).stdout
    changed = sorted(name for name in changed.split('\0') if name)
    report_dir.mkdir(parents=True, exist_ok=False)
    # Keep the complete incoming workflow diff for review; do not apply it with
    # the built-in Actions token, whose permissions exclude workflow updates.
    patch = git('diff', '--binary', base, upstream, '--', WORKFLOWS).stdout
    # GitHub release assets must contain at least one byte. The JSON record
    # represents the no-change case; do not publish an empty patch asset.
    if patch:
        (report_dir / 'upstream-workflows.patch').write_text(patch, encoding='utf-8')
    if git('merge-base', '--is-ancestor', upstream, before, check=False).returncode != 0:
        try:
            merged = git('merge', '--no-commit', '--no-ff', upstream, check=False)
            conflicts = git('diff', '--name-only', '--diff-filter=U', '-z').stdout.split('\0')
            outside = [name for name in conflicts if name and not name.startswith(WORKFLOWS + '/')]
            if outside or (merged.returncode and not any(conflicts)):
                raise RuntimeError('Source merge failed; refusing to resolve non-workflow conflicts: ' + ', '.join(outside))
            git('restore', '--source=' + before, '--staged', '--worktree', '--', WORKFLOWS)
            if git('diff', '--cached', '--name-only', before, '--', WORKFLOWS).stdout:
                raise RuntimeError('Fork workflow definitions changed during source sync')
            git('commit', '-m', 'sync: merge upstream source; retain reviewed fork workflows')
        except BaseException:
            if git('rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0:
                git('merge', '--abort')
            raise
    release = commit('HEAD')
    if git('diff', '--name-only', before, release, '--', WORKFLOWS).stdout:
        raise RuntimeError('Prepared commit changed a workflow definition')
    git('merge-base', '--is-ancestor', upstream, release)
    report = dict(before_sha=before, upstream_sha=upstream, release_commit=release,
                  synced=release != before, workflow_policy='retain_reviewed_fork_definitions',
                  incoming_workflow_changes=changed)
    (report_dir / 'upstream-sync.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def publish(report):
    before, release = report['before_sha'], report['release_commit']
    if any(not re.fullmatch('[0-9a-f]{40}', sha) for sha in (before, release)):
        raise ValueError('Invalid prepared commit')
    if commit('HEAD') != release:
        raise RuntimeError('HEAD changed after the build was prepared')
    if git('diff', '--name-only', before, release, '--', WORKFLOWS).stdout:
        raise RuntimeError('Prepared push would change workflow definitions')
    remote = git('ls-remote', '--exit-code', 'origin', 'refs/heads/master').stdout.split()
    if not remote or remote[0] != before:
        raise RuntimeError('Remote master advanced; preserve it and rerun from a fresh checkout')
    if release != before:
        # A normal push also rejects a non-fast-forward race after ls-remote.
        git('push', 'origin', release + ':refs/heads/master')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--upstream-ref', default='refs/remotes/upstream/master')
    prepare_parser.add_argument('--report-dir', required=True)
    publish_parser = sub.add_parser('publish')
    publish_parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if args.action == 'publish':
        publish(json.loads(Path(args.report).read_text(encoding='utf-8')))
        return
    report = prepare(args.upstream_ref, args.report_dir)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key in ('upstream_sha', 'release_commit', 'before_sha', 'synced'):
                value = str(report[key]).lower() if isinstance(report[key], bool) else report[key]
                output.write(f'{key}={value}\n')
    if report['incoming_workflow_changes']:
        print('::warning::Upstream workflow changes were retained as review evidence; fork workflow definitions are unchanged.')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
