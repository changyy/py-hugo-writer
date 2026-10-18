"""data/pageviews.json, written by scripts/ga4_pageviews.py: each page's views, for the post list."""
import json
from pathlib import Path
from urllib.parse import quote, unquote

FILE = 'data/pageviews.json'
# What Go (and so Hugo) leaves unescaped in a path, besides letters, digits and -._~
PATH_SAFE = "/!$&'()*+,;=:@~"


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
    return {'updated': data.get('updated'), 'pages': pages}


def views_of(data: dict | None, url: str) -> dict | None:
    page = (data or {}).get('pages', {}).get(page_key(url)) if url else None
    return page if isinstance(page, dict) and isinstance(page.get('views'), (int, float)) else None
