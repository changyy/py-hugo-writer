"""A Hugo site on disk: list its pages, read and write posts, create new ones.

Listing goes through `hugo list all`, so Hugo itself parses every front matter
format (YAML, TOML, JSON) and computes permalinks; this module never has to.
"""
import csv
import hashlib
import io
import json
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


class Conflict(SiteError):
    """The post changed on disk (another editor, an AI tool, git) since the editor opened it."""


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
    # Front matter key for the post's featured image: "images" is Hugo's own (its Open Graph and
    # Twitter templates read it), "cover.image" is PaperMod's; a dot means a nested key.
    featured_field: str = 'images'
    # A post without a featured image gets the first image dropped into it ("first"), or none.
    featured_default: str = 'first'
    # The site's GA4 property, as in the address of a GA4 report (a123456789p987654321, or p987654321),
    # for the editor's link to the Pages and screens report; empty for none.
    ga4_property: str = ''

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
        if 'featured_field' in values:
            _featured_keys(values['featured_field'])
        if 'ga4_property' in values:
            values['ga4_property'] = ga4_property(values['ga4_property'])
        if 'featured_default' in values and values['featured_default'] not in ('first', 'none'):
            raise SiteError('featured_default must be first or none')
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
        listed_at = time.time()
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
        self._posts = (time.monotonic(), posts, listed_at)
        return posts

    def url_of(self, rel: str) -> str:
        """Where Hugo publishes the post now: re-lists the site only if the file changed since the last listing."""
        cached = getattr(self, '_posts', None)
        fresh = cached and self.resolve(rel).stat().st_mtime < cached[2]
        posts = cached[1] if fresh else self.posts()
        return next((p['url'] for p in posts if p['path'] == rel), '')

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

    def check(self, rel: str, expect: str | None) -> None:
        """Raise Conflict if the post is no longer the version the editor loaded (None: don't check)."""
        if expect and version(self.read(rel)) != expect:
            raise Conflict('the post changed on disk since it was opened')

    def write(self, rel: str, text: str, expect: str | None = None) -> None:
        self.check(rel, expect)
        path = self.resolve(rel)
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            raise SiteError(f'only Markdown posts can be edited: {rel}')
        if not path.is_file():
            raise SiteError(f'no such post: {rel}')
        atomic_write(path, text.encode('utf-8'))

    def set_draft(self, rel: str, draft: bool, expect: str | None = None) -> str:
        self.check(rel, expect)
        text = set_draft(self.read(rel), draft)
        self.write(rel, text)
        return text

    def featured(self, rel: str) -> str | None:
        return get_featured(self.read(rel), self.settings.featured_field)

    def set_featured(self, rel: str, image: str | None) -> str:
        """Set (or with None, remove) the post's featured image in its front matter."""
        return self.set_front(rel, 'featured', image)

    def set_front(self, rel: str, field: str, value: str | list[str] | None, expect: str | None = None) -> str:
        """Set one of the post's settings in its front matter: title, url, featured (image) or tags.

        An empty url or featured removes the key (Hugo's permalinks / no image); a title can't be empty;
        tags is a list (empty: tags: []), each tag trimmed and kept once (Hugo ignores case).
        """
        if field == 'tags':
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise SiteError('tags must be a list of strings')
            tags, seen = [], set()
            for tag in (' '.join(v.split()) for v in value):
                if tag and tag.lower() not in seen:
                    seen.add(tag.lower())
                    tags.append(tag)
            self.check(rel, expect)
            text = set_featured(self.read(rel), 'tags', tags)
            self.write(rel, text)
            return text
        if not isinstance(value, (str, type(None))):
            raise SiteError(f'{field} must be a string')
        value = (value or '').strip() or None
        if field == 'title':
            if not value:
                raise SiteError('a title is required')
            key = 'title'
        elif field == 'url':
            if value and not value.startswith('/'):
                value = '/' + value
            if value and not re.fullmatch(r'/[^\s"\'<>]*', value):
                raise SiteError('a URL path has no spaces or quotes, e.g. /2026/10/my-post.html')
            key = 'url'
        elif field == 'featured':
            key = self.settings.featured_field
        else:
            raise SiteError(f'unknown post setting: {field}')
        self.check(rel, expect)
        text = set_featured(self.read(rel), key, value)
        self.write(rel, text)
        return text

    def tags(self) -> list[dict]:
        """Every tag on the site with the number of posts using it, the most used first."""
        counts, names = {}, {}
        for p in self.posts(max_age=5):
            try:
                text = (self.root / p['path']).read_text(encoding='utf-8', errors='replace')
                tags = get_tags(text)
            except (OSError, SiteError, ValueError):     # broken front matter: Hugo reports it, not us
                continue
            for tag in tags:
                key = tag.lower()                      # Hugo puts "Go" and "go" on one page
                counts[key] = counts.get(key, 0) + 1
                names.setdefault(key, tag)             # spelled as in the newest post
        return sorted(({'name': names[k], 'count': n} for k, n in counts.items()),
                      key=lambda t: (-t['count'], t['name'].lower()))

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

    def library(self, only_post: str | None = None, only_folder: Path | None = None) -> list[dict]:
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
            only_folder, _ = self.image_target(only_post)
        if only_folder is not None:
            files = [f for f in files if f.parent == only_folder]
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

    def rename_image(self, rel: str, name: str, new_name: str = '') -> dict:
        """Rename one of a post's images (see rename_file)."""
        folder, _ = self.image_target(rel)
        if not name or name != Path(name).name:
            raise SiteError(f'no such image: {name}')
        return self.rename_file((folder / name).relative_to(self.root).as_posix(), new_name)

    def rename_file(self, file: str, new_name: str = '') -> dict:
        """Rename an image and update every Markdown post linking to it.

        new_name is made URL-safe and keeps the file's extension; empty picks the next
        {slug}-NN. Returns the new name, the posts rewritten, and the ones that link to the image
        but can't be edited here (HTML content), which the caller should point out.
        """
        from .images import image_path, safe_stem           # avoid a cycle at import time
        old = (self.root / file).resolve()
        static = self.root / 'static' / 'images'
        inside = old.is_relative_to(self.content) or old.is_relative_to(static)
        if not inside or old.suffix.lower() not in IMAGE_SUFFIXES or not old.is_file():
            raise SiteError(f'no such image: {file}')
        before = next((i for i in self.library(only_folder=old.parent) if i['name'] == old.name), None)
        if before is None:
            raise SiteError(f'no such image: {file}')
        ext = old.suffix.lower()
        if new_name.strip():
            stem = safe_stem(new_name.strip() + '.x')
            if stem.lower().endswith(ext):                   # "photo.webp" typed in full
                stem = stem[:-len(ext)]
            new = old.parent / f'{stem}{ext}'
            if new == old:
                return {'name': old.name, 'updated': [], 'skipped': []}
            if new.exists():
                raise SiteError(f'{new.name} already exists')
        else:
            slug = self.image_slug(before['owner']['path']) if before['owner'] else old.parent.name
            new = image_path(old.parent, '{slug}-{n}', slug, old.name, ext)
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
                text = replace_links(text, old.name, new.name)
            atomic_write(path, text.encode('utf-8'))
            updated.append(ref['path'])
        return {'name': new.name, 'file': new.relative_to(self.root).as_posix(), 'updated': updated, 'skipped': skipped}

def ga4_property(text: str) -> str:
    """a…p… from a GA4 address (or as is), p… from a property ID; '' clears it."""
    text = text.strip()
    if not text:
        return ''
    if m := re.search(r'(?<![\w])(a\d+p\d+)(?![\w])', text):
        return m[1]
    if m := re.fullmatch(r'(?:properties/|p)?(\d+)', text):
        return 'p' + m[1]
    raise SiteError('ga4_property: paste the address of a GA4 report, or the property ID')


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


# -- featured image in the front matter --------------------------------------------
# A key like "images" (a list; the first entry counts) or a nested "cover.image" (a value), in
# YAML, TOML or JSON front matter. YAML and TOML are edited line by line, so the rest of the front
# matter, comments included, stays as it was.

def _featured_keys(field: str) -> list[str]:
    keys = [k for k in field.split('.') if k]
    if not 1 <= len(keys) <= 2 or not all(re.fullmatch(r'[A-Za-z_][\w-]*', k) for k in keys):
        raise SiteError(f'featured_field must be a key or parent.key, not {field!r}')
    return keys


def _yaml_block(lines: list[str], key: str, start: int = 0, end: int | None = None, indent: str = '') -> tuple[int, int] | None:
    """[first, last) lines of `key:` at this indent and the lines that belong to it."""
    end = len(lines) if end is None else end
    head = re.compile(rf'^{re.escape(indent)}{re.escape(key)}\s*:')
    for i in range(start, end):
        if head.match(lines[i]):
            j = i + 1
            while j < end and lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip()) > len(indent)
                                                   or (not indent and lines[j].lstrip().startswith('- '))):
                j += 1
            return i, j
    return None


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return re.sub(r'\\(.)', r'\1', raw[1:-1])
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    return raw


def _yaml_value(raw: str) -> str | list[str]:
    """An inline YAML value: a scalar, or a flow list like [a, "b, c"]."""
    raw = raw.strip()
    if not (raw.startswith('[') and raw.endswith(']')):
        return _unquote(raw)
    items = re.findall(r'\s*("(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[^,]+)', raw[1:-1])
    return [v for v in (_unquote(i) for i in items) if v]


def get_value(text: str, field: str) -> str | list[str] | None:
    """A front matter value at key or parent.key: a string, a list of strings, or None."""
    keys = _featured_keys(field)
    end = front_matter_end(text)
    if not end:
        return None
    front = text[:end]
    if front.startswith('{') or FRONT_MATTER.match(text).group(1) == '+++':
        value = json.loads(front) if front.startswith('{') else tomllib.loads(FRONT_MATTER.match(text).group(2))
        for k in keys:
            value = value.get(k) if isinstance(value, dict) else None
        if isinstance(value, list):                     # tags = [2024, "go"]: numbers are tags too
            return [str(v) for v in value if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
        return value if isinstance(value, str) else None
    lines = FRONT_MATTER.match(text).group(2).split('\n')
    block = _yaml_block(lines, keys[0])
    if not block:
        return None
    if len(keys) == 2:
        child = _yaml_block(lines, keys[1], block[0] + 1, block[1], indent=_child_indent(lines, block))
        if not child:
            return None
        block = child
    first, last = block
    inline = lines[first].split(':', 1)[1].strip()
    if inline:
        return _yaml_value(inline)
    return [v for v in (_unquote(l.strip()[2:]) for l in lines[first + 1:last] if l.strip().startswith('- ')) if v]


def get_featured(text: str, field: str) -> str | None:
    value = get_value(text, field)
    value = value[0] if isinstance(value, list) and value else value
    return value if isinstance(value, str) and value else None


def get_tags(text: str) -> list[str]:
    value = get_value(text, 'tags')
    return value if isinstance(value, list) else [value] if value else []


def _child_indent(lines, block) -> str:
    for line in lines[block[0] + 1:block[1]]:
        if line.strip():
            return line[:len(line) - len(line.lstrip())]
    return '  '


def set_featured(text: str, field: str, image: str | list[str] | None) -> str:
    """Set a front matter value (None removes it); a string under `images` is written as a list."""
    keys = _featured_keys(field)
    if isinstance(image, str) and keys[-1] == 'images':
        image = [image]
    end = front_matter_end(text)
    if not end:
        if image is None:
            return text
        text = '---\n---\n' + text
        end = front_matter_end(text)
    front = text[:end]
    if front.startswith('{'):
        data = json.loads(front)
        target = data
        for k in keys[:-1]:
            target = target.setdefault(k, {}) if image is not None else target.get(k, {})
        if image is None:
            target.pop(keys[-1], None)
            if len(keys) == 2 and keys[0] in data and not data[keys[0]]:
                del data[keys[0]]
        else:
            target[keys[-1]] = image
        return json.dumps(data, ensure_ascii=False, indent=2) + text[end:]
    m = FRONT_MATTER.match(text)
    fence, body = m.group(1), m.group(2)
    lines = body.split('\n') if body else []
    if isinstance(image, list):
        value = '[' + ', '.join(quote(i) for i in image) + ']'
    else:
        value = quote(image) if image is not None else None
    if fence == '+++':
        lines = _set_toml(lines, keys, value)
    else:
        lines = _set_yaml(lines, keys, value, image if isinstance(image, list) else None)
    new_body = '\n'.join(lines)
    return text[:m.start(2)] + new_body + text[m.end(2):]


def _set_yaml(lines, keys, value, items=None):
    block = _yaml_block(lines, keys[0])
    if len(keys) == 1:
        new = [f'{keys[0]}: {value}'] if value is not None else []
        if block:
            # A list written one "- item" per line stays that way.
            dashes = [l for l in lines[block[0] + 1:block[1]] if l.lstrip().startswith('- ')]
            if items and dashes and not lines[block[0]].split(':', 1)[1].strip():
                indent = dashes[0][:len(dashes[0]) - len(dashes[0].lstrip())]
                new = [f'{keys[0]}:'] + [f'{indent}- {quote(i)}' for i in items]
            del lines[block[0]:block[1]]
            at = block[0]
        else:
            at = len(lines)
        lines[at:at] = new
        return lines
    if block and lines[block[0]].split(':', 1)[1].strip():
        raise SiteError(f'{keys[0]} is written inline in the front matter; change it in the source view')
    if not block:
        if value is not None:
            lines += [f'{keys[0]}:', f'  {keys[1]}: {value}']
        return lines
    indent = _child_indent(lines, block)
    child = _yaml_block(lines, keys[1], block[0] + 1, block[1], indent=indent)
    if child:
        del lines[child[0]:child[1]]
    if value is not None:
        lines.insert(child[0] if child else block[0] + 1, f'{indent}{keys[1]}: {value}')
    else:
        rest = _yaml_block(lines, keys[0])
        if rest and rest[1] - rest[0] == 1:                 # parent left with no children
            del lines[rest[0]]
    return lines


def _set_toml(lines, keys, value):
    tables = [i for i, l in enumerate(lines) if re.match(r'^\s*\[', l)]
    top_end = tables[0] if tables else len(lines)
    if len(keys) == 1:
        pattern, section = re.compile(rf'^\s*{re.escape(keys[0])}\s*='), (0, top_end)
    else:
        dotted = re.compile(rf'^\s*{re.escape(keys[0])}\.{re.escape(keys[1])}\s*=')
        hit = next((i for i in range(top_end) if dotted.match(lines[i])), None)
        if hit is not None:
            if value is None:
                del lines[hit]
            else:
                lines[hit] = f'{keys[0]}.{keys[1]} = {value}'
            return lines
        head = next((i for i in tables if re.match(rf'^\s*\[{re.escape(keys[0])}\]\s*$', lines[i])), None)
        if head is None:
            if value is not None:
                lines += ['', f'[{keys[0]}]', f'{keys[1]} = {value}']
            return lines
        stop = next((i for i in tables if i > head), len(lines))
        pattern, section = re.compile(rf'^\s*{re.escape(keys[1])}\s*='), (head + 1, stop)
    hit = next((i for i in range(*section) if pattern.match(lines[i])), None)
    name = keys[-1]
    if hit is not None:
        if value is None:
            del lines[hit]
        else:
            lines[hit] = f'{name} = {value}'
    elif value is not None:
        lines.insert(section[1], f'{name} = {value}')
    if len(keys) == 2 and value is None:
        head = section[0] - 1
        stop = next((i for i, l in enumerate(lines) if i > head and re.match(r'^\s*\[', l)), len(lines))
        if not any(l.strip() for l in lines[head + 1:stop]):
            del lines[head:stop]
            while lines and not lines[-1].strip():
                lines.pop()
    return lines


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


def version(text: str) -> str:
    """A post's version for the editor: by content, so rewriting the same bytes (a touch) isn't a change."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def front_matter(text: str) -> str:
    return text[:front_matter_end(text)]


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
