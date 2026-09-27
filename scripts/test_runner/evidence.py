"""Bounded subprocess queries and secret-free input evidence."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
from datetime import datetime, timezone

VERSION = '1'
POLICY = dict(startup=30., collection=60., slow=60., test=120., gap=30.,
              final=30., suite=600., term=10., reap=5., poll=.2)


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, path)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], timeout=10)


def inputs(root):
    names = set(git(root, 'ls-files', '-z').decode().split('\0'))
    names.update(git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'))
    # Include ignored settings/fixtures too, except explicit generated artifacts.
    names.update(git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').decode().split('\0'))
    entries = {}
    for name in sorted(names - {''}):
        if (any(part in {'.test-runs', '__pycache__', '.pytest_cache', '.git', '.venv', 'venv'} for part in Path(name).parts)
                or name.startswith(('logs/', '.superpowers/', '.claude/worktrees/', 'data/gimpo/'))):
            continue
        path = root / name
        try:
            info = path.lstat()
            entry = {'mode': stat.S_IMODE(info.st_mode)}
            if path.is_symlink():
                entry['target'] = os.readlink(path)
                entry['external'] = not path.resolve().is_relative_to(root.resolve())
                if path.is_file():
                    entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_file():
                entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                entry['kind'] = 'non-file'
            entries[name] = entry
        except FileNotFoundError:
            entries[name] = {'deleted': True}
    return {'files': entries, 'fingerprint': digest(entries)}


def environment():
    packages = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())
    browsers = []
    for base in (Path.home() / 'Library/Caches/ms-playwright', Path.home() / '.cache/ms-playwright'):
        if base.exists():
            browsers.extend(p.name for p in base.iterdir() if p.is_dir())
    values = {k: os.environ[k] for k in ('LANG', 'LC_ALL', 'TZ', 'PYTHONHASHSEED', 'RUN_DEFAULT_TIMEOUT_ACCEPTANCE',
                                                'TELEGRAM_ALARM_ENABLED', 'FLASK_DEBUG', 'WERKZEUG_RUN_MAIN',
                                                'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONWARNINGS', 'PYTHONOPTIMIZE') if k in os.environ}
    return dict(python=sys.executable, version=sys.version, platform=platform.platform(),
                architecture=platform.machine(), packages=packages, browsers=sorted(browsers),
                environment=values, runner_version=VERSION, policy_version=VERSION)
