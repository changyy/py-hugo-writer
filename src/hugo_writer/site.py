"""A Hugo site on disk: list its pages, read and write posts, create new ones.

Listing goes through `hugo list all`, so Hugo itself parses every front matter
format (YAML, TOML, JSON) and computes permalinks; this module never has to.
"""
import csv
import io
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_FILES = tuple(f'{base}.{ext}' for base in ('hugo', 'config') for ext in ('toml', 'yaml', 'yml', 'json'))
EDITABLE_SUFFIXES = {'.md', '.markdown'}
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')
FRONT_MATTER = re.compile(r'\A(---|\+\+\+)[ \t]*\n(.*?)\n?^\1[ \t]*$', re.S | re.M)


class SiteError(Exception):
    """Something the user should see: a bad path, a hugo failure, an invalid slug."""


@dataclass
class Settings:
    """Per-site options, read from `.hugo-writer.toml` in the site root (all optional)."""

    section: str = 'posts'
    # Where a new post's page bundle goes, under content/<section>/.
    path: str = '{year}/{month}/{slug}'
    # Front matter `url` for new posts; empty to let Hugo's permalinks decide.
    url: str = ''
    image_max_width: int = 1024
    image_format: str = 'webp'
    image_quality: int = 82

    @classmethod
    def load(cls, root: Path) -> 'Settings':
        file = root / '.hugo-writer.toml'
        if not file.is_file():
            return cls()
        data = tomllib.loads(file.read_text(encoding='utf-8'))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Site:
    def __init__(self, root, hugo: str = 'hugo'):
        self.root = Path(root).resolve()
        if not any((self.root / name).is_file() for name in CONFIG_FILES) and not (self.root / 'config').is_dir():
            raise SiteError(f'{self.root} is not a Hugo site (no hugo.toml, hugo.yaml or config/ found)')
        self.content = self.root / 'content'
        self.hugo = hugo
        self.settings = Settings.load(self.root)

    # -- listing -----------------------------------------------------------------

    def posts(self) -> list[dict]:
        out = self._list()
        if out.returncode != 0:
            raise SiteError(out.stderr.strip() or 'hugo list all failed')
        posts = []
        for row in csv.DictReader(io.StringIO(out.stdout)):
            if row.get('kind') != 'page':
                continue
            posts.append({
                'path': row['path'],
                'title': row['title'],
                'date': row['date'],
                'draft': row['draft'] == 'true',
                'section': row['section'],
                'url': urlsplit(row['permalink']).path,
                'editable': Path(row['path']).suffix.lower() in EDITABLE_SUFFIXES,
            })
        posts.sort(key=lambda p: p['date'], reverse=True)
        return posts

    _list_flags = ['--noBuildLock']

    def _list(self) -> subprocess.CompletedProcess:
        # --noBuildLock keeps us from fighting a `hugo server` the user already runs,
        # but `hugo list` only accepts it in newer releases (not in 0.128, for one).
        run = lambda flags: subprocess.run([self.hugo, 'list', 'all', *flags], cwd=self.root,
                                           capture_output=True, text=True, encoding='utf-8')
        out = run(self._list_flags)
        if out.returncode != 0 and self._list_flags and 'unknown flag' in out.stderr:
            self._list_flags = []
            out = run([])
        return out

    # -- reading and writing -----------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """A site-relative path (as `hugo list` prints it) -> absolute path, kept inside content/."""
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.content):
            raise SiteError(f'not a content path: {rel}')
        return path

    def read(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise SiteError(f'no such post: {rel}')
        return path.read_text(encoding='utf-8')

    def write(self, rel: str, text: str) -> None:
        path = self.resolve(rel)
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            raise SiteError(f'only Markdown posts can be edited: {rel}')
        if not path.is_file():
            raise SiteError(f'no such post: {rel}')
        atomic_write(path, text.encode('utf-8'))

    def set_draft(self, rel: str, draft: bool) -> str:
        text = set_draft(self.read(rel), draft)
        self.write(rel, text)
        return text

    def create(self, title: str, slug: str, now: datetime | None = None) -> str:
        title = title.strip()
        if not title:
            raise SiteError('a title is required')
        if not SLUG.match(slug):
            raise SiteError('the slug may only use a-z, 0-9 and "-" (it becomes part of the URL)')
        now = (now or datetime.now()).astimezone()
        names = {'year': f'{now:%Y}', 'month': f'{now:%m}', 'day': f'{now:%d}', 'slug': slug}
        bundle = self.content / self.settings.section / self.settings.path.format(**names)
        page = bundle / 'index.md'
        if page.exists() or bundle.with_suffix('.md').exists():
            raise SiteError(f'a post already exists at {page.relative_to(self.root)}')
        lines = ['---', f'title: {quote(title)}', f'date: {now.isoformat(timespec="seconds")}', 'draft: true']
        if self.settings.url:
            lines.append(f'url: {self.settings.url.format(**names)}')
        lines += ['tags: []', '---', '', '']
        bundle.mkdir(parents=True, exist_ok=True)
        atomic_write(page, '\n'.join(lines).encode('utf-8'))
        return page.relative_to(self.root).as_posix()

    # -- images ------------------------------------------------------------------

    def image_target(self, rel: str) -> tuple[Path, str]:
        """Where images for a post are stored, and the Markdown link prefix for them.

        A page bundle (…/slug/index.md) keeps its images next to it; any other post
        gets static/images/<post path>/.
        """
        post = self.resolve(rel)
        if post.stem in ('index', '_index'):
            return post.parent, ''
        folder = self.root / 'static' / 'images' / post.relative_to(self.content).with_suffix('')
        return folder, '/images/' + folder.relative_to(self.root / 'static' / 'images').as_posix() + '/'


def quote(value: str) -> str:
    """A double-quoted scalar, valid in both YAML and TOML."""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def set_draft(text: str, draft: bool) -> str:
    """Set `draft` in a post's front matter, adding it (or the front matter) if missing."""
    value = 'true' if draft else 'false'
    if m := FRONT_MATTER.match(text):
        fence, body = m.group(1), m.group(2)
        line = f'draft: {value}' if fence == '---' else f'draft = {value}'
        existing = re.compile(r'^draft[ \t]*[:=].*$', re.M)
        body = existing.sub(line, body, count=1) if existing.search(body) else (body + '\n' + line if body else line)
        return text[:m.start(2)] + body + text[m.end(2):]
    if text.lstrip().startswith('{'):
        existing = re.compile(r'"draft"\s*:\s*(true|false)')
        if existing.search(text):
            return existing.sub(f'"draft": {value}', text, count=1)
        return re.sub(r'\{', '{\n  "draft": ' + value + ',', text, count=1)
    return f'---\ndraft: {value}\n---\n' + text


def atomic_write(path: Path, data: bytes) -> None:
    # Hugo's watcher may read the file mid-write; replace it in one step instead.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.hugo-writer-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        # mkstemp creates 0600; give the file the mode it had, or what umask would give a new one.
        if path.exists():
            mode = path.stat().st_mode & 0o777
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
