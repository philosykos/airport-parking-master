#!/usr/bin/env python3
"""Package committed, dirty and untracked changes against tested input hashes."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from scripts.test_runner.evidence import atomic, git, inputs


def package(root, run_id, base):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id) or base.startswith('-'):
        raise ValueError('invalid run id or base')
    run = root / '.test-runs' / run_id
    manifest = json.loads((run / 'manifest.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    before = inputs(root)
    if (not result.get('inputs_unchanged') or result['fingerprint'] != manifest['fingerprint']
            or before['fingerprint'] != manifest['inputs']['fingerprint']):
        raise ValueError('review inputs do not match tested snapshot')
    base_sha = git(root, 'rev-parse', '--verify', base + '^{commit}').decode().strip()
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    committed = git(root, 'diff', '--binary', base_sha, head, '--')
    dirty = git(root, 'diff', '--binary', 'HEAD', '--')
    names = git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    names = [name for name in names if name and name in before['files']]
    untracked = bytearray()
    for name in names:
        diff = subprocess.run(['git', 'diff', '--no-index', '--binary', '--', '/dev/null', name],
                              cwd=root, capture_output=True, timeout=10)
        if diff.returncode not in (0, 1):
            raise RuntimeError(diff.stderr.decode(errors='replace'))
        untracked.extend(diff.stdout)
    if inputs(root)['fingerprint'] != before['fingerprint']:
        raise ValueError('inputs changed while preparing review')
    output = run / 'review'
    output.mkdir(exist_ok=True)
    for name, data in (('committed.patch', committed), ('worktree.patch', dirty), ('untracked.patch', untracked)):
        (output / name).write_bytes(data)
    atomic(output / 'snapshot.json', dict(base=base_sha, head=head, run_id=run_id,
           fingerprint=result['fingerprint'], input_fingerprint=before['fingerprint'],
           files=before['files'], untracked=names, outcome=result['outcome'],
           acceptance_review_required=True))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--base', required=True, help='BASE recorded immediately before task dispatch')
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
        print(package(root, args.run_id, args.base))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
