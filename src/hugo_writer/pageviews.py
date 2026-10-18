"""data/pageviews.json: each page's views, for the post list. scripts/ga4_pageviews.py writes it
from the GA4 API or a report downloaded from GA4; the editor takes such a report too (merge_csv).
Keys, the report format and merging are the same as in that script, which stays standalone."""
import csv
import json
import re
from datetime import datetime, time
from pathlib import Path
from urllib.parse import quote, unquote

from .site import SiteError, atomic_write

FILE = 'data/pageviews.json'
# What Go (and so Hugo) leaves unescaped in a path, besides letters, digits and -._~
PATH_SAFE = "/!$&'()*+,;=:@~"
# The views column of a downloaded report, in the languages GA4 is used in most; else the second column.
VIEWS_HEADERS = {'views', '瀏覽', '瀏覽次數', '浏览', '浏览次数', '表示回数', '조회수', 'aufrufe', 'vues', 'visualizaciones'}


def page_key(path: str) -> str:
    """The file's key for a URL path; the same as scripts/ga4_pageviews.py and the pageviews.html partial."""
    path = quote(unquote(path.split('?', 1)[0].split('#', 1)[0]), safe=PATH_SAFE).lower()
    return path.removesuffix('index.html').removesuffix('.html').rstrip('/') or '/'


def load(root: Path) -> dict | None:
    """{updated, pages: {key: {views, at}}}; None without the file, {error} when it can't be read."""
    file = root / FILE
    if not file.is_file():
        return None
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
        pages = data['pages']
        if not isinstance(pages, dict):
            raise TypeError
    except (ValueError, KeyError, TypeError, OSError) as e:
        return {'error': f'{FILE}: {e.__class__.__name__}: {e}'}
    return {**data, 'pages': pages}


def views_of(data: dict | None, url: str) -> dict | None:
    page = (data or {}).get('pages', {}).get(page_key(url)) if url else None
    return page if isinstance(page, dict) and isinstance(page.get('views'), (int, float)) else None


def read_csv(text: str) -> tuple[dict[str, int], datetime | None]:
    """A report downloaded from the GA4 web UI: views per page, and the report's end date."""
    end, lines = None, []
    for line in text.lstrip('﻿').splitlines():
        if line.startswith('#'):
            # "# End date: 20261017", "# 結束日期：20261017"; the start date comes first.
            if m := re.search(r'(\d{8})\s*$', line):
                end = datetime.strptime(m[1], '%Y%m%d')
        elif line.strip():
            lines.append(line)
    rows = list(csv.reader(lines))
    if not rows:
        raise SiteError('the CSV has no rows')
    header = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(header) if h in VIEWS_HEADERS), 1)
    views = {}
    for row in rows[1:]:
        if len(row) <= col or not row[0].startswith('/'):    # a total, or not a page
            continue
        try:
            n = int(float(row[col].replace(',', '').strip() or 0))
        except ValueError:
            raise SiteError(f'not a number of views: {row[col]!r} ({row[0]})')
        key = page_key(row[0])
        views[key] = views.get(key, 0) + n
    if not views:
        raise SiteError('no page paths in the CSV; download the "Pages and screens" report by "Page path"')
    # Counted to the end of the report's last day.
    return views, end and datetime.combine(end.date(), time(23, 59, 59)).astimezone()


def merge(old: dict, views: dict[str, int], years: int | None, now: datetime, at: datetime | None = None) -> dict:
    """The new counts over the old file's: pages left out this time keep what they had (as the script)."""
    stamp = (at or now).isoformat(timespec='seconds')
    same = years is None or old.get('years', years) == years
    pages = dict(old.get('pages') or {}) if same else {}
    pages.update({key: {'views': n, 'at': stamp} for key, n in views.items()})
    pages = dict(sorted(pages.items(), key=lambda kv: (-kv[1]['views'], kv[0])))
    data = {'updated': now.isoformat(timespec='seconds'), 'years': years or old.get('years'), 'pages': pages}
    return {k: v for k, v in data.items() if v is not None}


def templates(root: Path) -> dict:
    """Where the site (or its theme) has the pageviews-top.html partial, and which templates call it."""
    partial, used = None, []
    for layouts in [root / 'layouts', *sorted((root / 'themes').glob('*/layouts'))]:
        for file in sorted(layouts.rglob('*.html')) if layouts.is_dir() else []:
            rel = file.relative_to(root).as_posix()
            if file.name == 'pageviews-top.html' and file.parent.name in ('partials', '_partials'):
                partial = partial or rel
            elif 'pageviews-top.html' in file.read_text(encoding='utf-8', errors='replace'):
                used.append(rel)
    return {'partial': partial, 'used_in': used}


def touch(root: Path) -> None:
    """Rewrite data/pageviews.json as it is, so a running hugo server rebuilds (a setting changed)."""
    file = root / FILE
    if file.is_file():
        file.write_bytes(file.read_bytes())


def merge_csv(root: Path, text: str) -> dict:
    """Merge a downloaded report into the site's data/pageviews.json; what was read and what was kept."""
    views, at = read_csv(text)
    old = load(root) or {}
    if 'error' in old:
        raise SiteError(f'{old["error"]}; move it away to start over')
    data = merge(old, views, None, datetime.now().astimezone(), at)
    (root / FILE).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(root / FILE, (json.dumps(data, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
    return {'pages': len(views), 'views': sum(views.values()), 'kept': len(data['pages']) - len(views),
            'at': at and at.isoformat(timespec='seconds'), 'file': FILE}
