import shutil
import subprocess

import pytest

from hugo_writer import git

from .conftest import BUNDLE, DRAFT

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')


def run(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
                   check=True, capture_output=True)


def test_a_site_outside_git(site_dir, monkeypatch):
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(site_dir.parent))
    assert git.status(site_dir) == {'repo': False}


@needs_git
def test_uncommitted_files_and_the_last_commit(site_dir):
    run(site_dir, 'init', '-q', '-b', 'main')
    got = git.status(site_dir)
    assert got['repo'] and got['branch'] == 'main' and got['last_commit'] is None
    assert got['dirty'] == len(got['files']) and {'path': BUNDLE, 'status': '??'} in got['files']

    run(site_dir, 'add', '.')
    run(site_dir, 'commit', '-q', '-m', 'first')
    got = git.status(site_dir)
    assert got['dirty'] == 0 and got['files'] == []
    assert got['last_commit']['subject'] == 'first' and got['last_commit']['time']

    (site_dir / DRAFT).write_text('changed', encoding='utf-8')
    assert git.status(site_dir)['files'] == [{'path': DRAFT, 'status': 'M'}]


@needs_git
def test_a_site_inside_a_larger_repository(tmp_path, site_dir):
    # site_dir is tmp_path/demo-site: paths are given relative to the site, and only its files count.
    run(tmp_path, 'init', '-q')
    (tmp_path / 'other.txt').write_text('x')
    (site_dir / DRAFT).write_text('changed', encoding='utf-8')
    paths = [f['path'] for f in git.status(site_dir)['files']]
    assert DRAFT in paths and 'other.txt' not in paths and not any(p.startswith('demo-site/') for p in paths)
