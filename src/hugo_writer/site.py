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
import time
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_FILES = tuple(f'{base}.{ext}' for base in ('hugo', 'config') for ext in ('toml', 'yaml', 'yml', 'json'))
EDITABLE_SUFFIXES = {'.md', '.markdown'}
IMAGE_SUFFIXES = {'.webp', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.avif'}
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')
# Raw HTML blocks the WYSIWYG editor would drop or mangle (embeds, tables, scripts, …).
RAW_HTML = re.compile(r'^[ \t]*<(iframe|object|embed|video|audio|script|style|table|div|form)\b', re.M | re.I)
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
    # File name for dropped images: {slug} (the post's), {n} (01, 02, …), {original} (the file's own
    # name). The default keeps camera / screenshot names, which carry dates and more, off the site.
    image_name: str = '{slug}-{n}'
    # Remove EXIF / XMP from images (GPS position, camera, time taken, …). False keeps EXIF.
    strip_metadata: bool = True

    @classmethod
    def load(cls, root: Path) -> 'Settings':
        file = root / '.hugo-writer.toml'
        if not file.is_file():
            return cls()
        data = tomllib.loads(file.read_text(encoding='utf-8'))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, root: Path, changes: dict) -> 'Settings':
        """Write changed options to .hugo-writer.toml, keeping its other lines and comments."""
        types = {f.name: type(getattr(self, f.name)) for f in fields(self)}
        values = {}
        for key, value in changes.items():
            if key not in types:
                raise SiteError(f'unknown setting: {key}')
            try:
                value = value if types[key] is not bool else (value in (True, 'true', '1', 1))
                values[key] = types[key](value)
            except (TypeError, ValueError):
                raise SiteError(f'{key} must be a {types[key].__name__}')
        if 'image_max_width' in values and not 16 <= values['image_max_width'] <= 8192:
            raise SiteError('image_max_width must be between 16 and 8192')
        if 'image_quality' in values and not 1 <= values['image_quality'] <= 100:
            raise SiteError('image_quality must be between 1 and 100')
        if 'image_format' in values and values['image_format'].lower() not in ('webp', 'jpeg', 'jpg', 'png'):
            raise SiteError('image_format must be webp, jpeg or png')
        if 'image_name' in values:
            try:
                values['image_name'].format(slug='s', n='01', original='o')
            except (KeyError, IndexError, ValueError):
                raise SiteError('image_name may only use {slug}, {n} and {original}')
        file = root / '.hugo-writer.toml'
        lines = file.read_text(encoding='utf-8').splitlines() if file.is_file() else []
        for key, value in values.items():
            literal = ('true' if value else 'false') if isinstance(value, bool) else (
                str(value) if isinstance(value, int) else quote(value))
            pattern = re.compile(rf'^\s*{key}\s*=')
            for i, line in enumerate(lines):
                if pattern.match(line):
                    lines[i] = f'{key} = {literal}'
                    break
            else:
                lines.append(f'{key} = {literal}')
        atomic_write(file, ('\n'.join(lines) + '\n').encode('utf-8'))
        return Settings.load(root)


class Site:
    def __init__(self, root, hugo: str = 'hugo'):
        self.root = Path(root).resolve()
        if not any((self.root / name).is_file() for name in CONFIG_FILES) and not (self.root / 'config').is_dir():
            raise SiteError(f'{self.root} is not a Hugo site (no hugo.toml, hugo.yaml or config/ found)')
        self.content = self.root / 'content'
        self.hugo = hugo
        self.settings = Settings.load(self.root)

    # -- listing -----------------------------------------------------------------

    def posts(self, max_age: float = 0) -> list[dict]:
        """Every page of the site via `hugo list all`; max_age > 0 may reuse a recent listing."""
        cached = getattr(self, '_posts', None)
        if max_age and cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
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
        self._posts = (time.monotonic(), posts)
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

    def image_slug(self, rel: str) -> str:
        """The post's slug: its bundle folder (…/slug/index.md) or its file name (…/slug.md)."""
        post = self.resolve(rel)
        return post.parent.name if post.stem in ('index', '_index') else post.stem

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


    def images(self, rel: str) -> list[dict]:
        """The images stored for a post (its bundle folder, or static/images/<post path>/)."""
        folder, prefix = self.image_target(rel)
        if not folder.is_dir():
            return []
        found = []
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
                st = f.stat()
                found.append({'name': f.name, 'url': prefix + f.name, 'bytes': st.st_size, 'modified': st.st_mtime})
        return found

    def delete_image(self, rel: str, name: str) -> None:
        folder, _ = self.image_target(rel)
        if not name or name != Path(name).name or name.startswith('.'):
            raise SiteError(f'not an image name: {name}')
        path = folder / name
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise SiteError(f'no such image: {name}')
        path.unlink()

    # -- the whole site's images -------------------------------------------------

    def library(self, only_post: str | None = None) -> list[dict]:
        """Every image of the site — in page bundles and in static/images/ — with the posts using it.

        A post uses an image when its text links to it: by file name from the bundle's own page,
        or by its published URL (/2026/10/slug/x.webp, /images/…) from any page.
        """
        posts = self.posts(max_age=5)
        owners = {}
        for p in posts:
            path = self.root / p['path']
            if path.stem in ('index', '_index'):
                owners[path.parent] = p
        texts = {}
        for p in posts:
            try:
                texts[p['path']] = (self.root / p['path']).read_text(encoding='utf-8', errors='replace')
            except OSError:
                texts[p['path']] = ''
        static = self.root / 'static' / 'images'
        files = [f for base in (self.content, static) if base.is_dir() for f in sorted(base.rglob('*'))
                 if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES]
        if only_post:
            folder, _ = self.image_target(only_post)
            files = [f for f in files if f.parent == folder]
        items = []
        for f in files:
            owner = owners.get(f.parent)
            if f.is_relative_to(static):
                url = '/images/' + f.relative_to(static).as_posix()
            elif owner:
                url = folder_of(owner['url']) + f.name
            else:
                url = None                                     # not a bundle: not published as a page file
            refs = []
            for p in posts:
                text = texts[p['path']]
                if (url and links_to(text, url)) or (owner is p and links_to(text, f.name)):
                    refs.append({'path': p['path'], 'title': p['title'], 'editable': p['editable']})
            st = f.stat()
            items.append({'file': f.relative_to(self.root).as_posix(), 'name': f.name, 'url': url,
                          'owner': {'path': owner['path'], 'title': owner['title']} if owner else None,
                          'bytes': st.st_size, 'modified': st.st_mtime, 'refs': refs})
        return items

    def image_file(self, url_path: str) -> Path | None:
        """The file on disk behind a site URL of an image, without asking Hugo.

        /images/… is static/images/…; /2026/10/slug/x.webp is x.webp in the bundle of the post
        published at /2026/10/slug.html (or /2026/10/slug/). Hugo itself only serves bundle files
        that a page links to (with publishResources = false), and only after a rebuild, so the
        editor reads them from disk: unused and just-dropped images show too.
        """
        if Path(url_path).suffix.lower() not in IMAGE_SUFFIXES:
            return None
        rel = url_path.lstrip('/')
        if rel.startswith('images/'):
            candidate = (self.root / 'static' / rel).resolve()
            return candidate if candidate.is_relative_to(self.root / 'static' / 'images') and candidate.is_file() else None
        folder, name = url_path.rsplit('/', 1)
        for p in self.posts(max_age=5):
            path = self.root / p['path']
            if path.stem in ('index', '_index') and folder_of(p['url']) == folder + '/':
                candidate = (path.parent / name).resolve()
                if candidate.parent == path.parent and candidate.is_file():
                    return candidate
        return None

    def delete_file(self, rel: str) -> None:
        """Delete an image by its site-relative path (from library())."""
        path = (self.root / rel).resolve()
        inside = path.is_relative_to(self.content) or path.is_relative_to(self.root / 'static' / 'images')
        if not inside or path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise SiteError(f'not an image of this site: {rel}')
        path.unlink()

    def rename_image(self, rel: str, name: str) -> dict:
        """Give a post's image the next {slug}-NN name and update every post linking to it.

        Returns the new name, the posts rewritten, and the ones that link to it but can't be
        edited here (HTML content), which the caller should point out.
        """
        folder, prefix = self.image_target(rel)
        old = folder / name
        if not name or name != Path(name).name or old.suffix.lower() not in IMAGE_SUFFIXES or not old.is_file():
            raise SiteError(f'no such image: {name}')
        from .images import image_path            # avoid a cycle at import time
        new = image_path(folder, '{slug}-{n}', self.image_slug(rel), name, old.suffix.lower())
        before = next(i for i in self.library(only_post=rel) if i['name'] == name)
        old.rename(new)
        updated, skipped = [], []
        for ref in before['refs']:
            if not ref['editable']:
                skipped.append(ref)
                continue
            path = self.root / ref['path']
            text = path.read_text(encoding='utf-8')
            if before['url']:
                text = replace_links(text, before['url'], folder_of_url_join(before['url'], new.name))
            if before['owner'] and before['owner']['path'] == ref['path']:
                text = replace_links(text, prefix + name, prefix + new.name)
            atomic_write(path, text.encode('utf-8'))
            updated.append(ref['path'])
        return {'name': new.name, 'updated': updated, 'skipped': skipped}


def folder_of(url: str) -> str:
    """Where Hugo publishes a bundle's files: /a/b/ for /a/b/, and /a/b/ for /a/b.html too."""
    if not url:
        return '/'
    return url if url.endswith('/') else re.sub(r'\.[^/.]*$', '', url) + '/'


def folder_of_url_join(url: str, name: str) -> str:
    return url[:url.rfind('/') + 1] + name


def _link_pattern(url: str) -> re.Pattern:
    # (url), (url#right), "url", =url … but not "x-url" or "url2". A bare file name must not follow
    # a "/" either: "/other-post/x.webp" is a different image than this bundle's "x.webp".
    before = r'[\s("\'=]' if '/' not in url else r'[\s("\'/=]'
    return re.compile(r'(^|' + before + ')' + re.escape(url) + r'(?=[)#\s"\'?]|$)', re.M)


def links_to(text: str, url: str) -> bool:
    return bool(_link_pattern(url).search(text))


def replace_links(text: str, old: str, new: str) -> str:
    return _link_pattern(old).sub(lambda m: m.group(1) + new, text)


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


def front_matter_end(text: str) -> int:
    """Where the front matter (YAML ---, TOML +++ or a JSON object) ends; 0 if there is none."""
    if m := FRONT_MATTER.match(text):
        return m.end()
    if not text.startswith('{'):
        return 0
    depth, in_string, escaped = 0, False, False
    for i, ch in enumerate(text):
        if in_string:
            escaped, in_string = (False, True) if escaped else (ch == '\\', ch != '"')
        elif ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i + 1
    return 0


def has_raw_html(text: str) -> bool:
    """Whether a Markdown post carries HTML blocks that only the source view keeps intact."""
    body = text[front_matter_end(text):]
    # Ignore fenced code: HTML shown as an example is not HTML on the page.
    body = re.sub(r'^(```|~~~).*?^\1', '', body, flags=re.M | re.S)
    return bool(RAW_HTML.search(body))


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
