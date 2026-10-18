"""Whether the site is kept in git, what is not committed yet, and the last commit."""
import subprocess
from pathlib import Path

# The editor lists this many uncommitted files at most; the count is always the full one.
MAX_FILES = 50


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=10)


def status(root: Path) -> dict:
    """{repo: False} outside git (or without git); else the uncommitted files under root and the last commit."""
    try:
        top = _git(root, 'rev-parse', '--show-toplevel', '--show-prefix')
    except (OSError, subprocess.TimeoutExpired):
        return {'repo': False}
    if top.returncode != 0:
        return {'repo': False}
    prefix = top.stdout.splitlines()[1] if len(top.stdout.splitlines()) > 1 else ''
    branch = _git(root, 'branch', '--show-current').stdout.strip()
    # Only the site's own files count when it sits in a larger repository; paths come back
    # relative to the repository, so they are made relative to the site.
    out = _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.')
    files, entries = [], out.stdout.split('\0')
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in 'RC':            # a rename or copy is followed by its old path
            i += 1
        files.append({'path': path.removeprefix(prefix), 'status': code.strip() or code})
    last = _git(root, 'log', '-1', '--format=%H%x00%cI%x00%s', '--', '.')
    commit = None
    if last.returncode == 0 and last.stdout.strip():
        sha, when, subject = last.stdout.strip().split('\0', 2)
        commit = {'hash': sha[:7], 'time': when, 'subject': subject}
    return {'repo': True, 'branch': branch, 'dirty': len(files), 'files': files[:MAX_FILES], 'last_commit': commit}
