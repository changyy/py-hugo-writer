#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-auth[requests]>=2"]
# ///
"""Page views per page from Google Analytics 4, merged into data/pageviews.json for Hugo templates.

    uv run ga4_pageviews.py --property 123456789 [--years 5] [--top 2000] [--output data/pageviews.json]

Each run asks GA4 for the views of the last --years (the most viewed --top pages only, if given)
and merges them into the file: pages in the answer get the new count and today's date, pages
left out keep the count and date they had. So a small --top keeps the file short to fetch, and
old posts nobody reads any more keep their last known number.

    {"updated": "...", "years": 5,
     "pages": {"/2024/05/hello-markdown": {"views": 1245, "at": "2026-10-18T03:00:00+08:00"}, ...}}

Keys are paths as Hugo's .RelPermalink writes them (percent-encoded), lower case, without the
query, index.html, .html or a trailing slash: /a/b.html (Hugo) and /a/b (Cloudflare Pages drops
.html) are one page. hugo-writer and its pageviews.html partial look pages up that way:
https://github.com/changyy/py-hugo-writer/blob/main/docs/pageviews.md

Signing in, the first that is set:
  --token / GA4_ACCESS_TOKEN       an OAuth access token (`gcloud auth print-access-token`)
  --credentials FILE               a service account key file (JSON)
  GA4_CREDENTIALS_JSON             the key file's content (a CI secret)
  Application Default Credentials  GOOGLE_APPLICATION_CREDENTIALS, gcloud, workload identity
The account needs the Viewer role on the GA4 property (Admin > Property access management).
"""
import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
API = 'https://analyticsdata.googleapis.com/v1beta/properties/{}:runReport'
PAGE_SIZE = 100000
# What Go (and so Hugo) leaves unescaped in a path, besides letters, digits and -._~
PATH_SAFE = "/!$&'()*+,;=:@~"


class Error(Exception):
    pass


def access_token(credentials: str | None = None) -> str:
    import google.auth
    import google.auth.exceptions
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    info = os.environ.get('GA4_CREDENTIALS_JSON')
    if credentials:
        creds = service_account.Credentials.from_service_account_file(credentials, scopes=[SCOPE])
    elif info:
        creds = service_account.Credentials.from_service_account_info(json.loads(info), scopes=[SCOPE])
    else:
        try:
            creds, _ = google.auth.default(scopes=[SCOPE])
        except google.auth.exceptions.DefaultCredentialsError as e:
            raise Error(f'no Google credentials: {e}')
    creds.refresh(Request())
    return creds.token


def page_key(path: str) -> str:
    """One spelling per page: encoded like .RelPermalink, lower case, no query, index.html, .html or trailing slash."""
    path = quote(unquote(path.split('?', 1)[0].split('#', 1)[0]), safe=PATH_SAFE).lower()
    return path.removesuffix('index.html').removesuffix('.html').rstrip('/') or '/'


def fetch(property_id: str, token: str, years: int = 5, top: int = 0, host: str | None = None,
          opener=urllib.request.urlopen) -> dict[str, int]:
    """Views per page over the last `years`, the `top` most viewed pages only if top > 0."""
    body = {'dateRanges': [{'startDate': f'{years * 365}daysAgo', 'endDate': 'today'}],
            'dimensions': [{'name': 'pagePath'}], 'metrics': [{'name': 'screenPageViews'}],
            'orderBys': [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}]}
    if host:
        body['dimensionFilter'] = {'filter': {'fieldName': 'hostName', 'stringFilter': {'value': host}}}
    views, offset = {}, 0
    while True:
        limit = min(PAGE_SIZE, top - offset) if top else PAGE_SIZE
        req = urllib.request.Request(API.format(property_id),
                                     data=json.dumps({**body, 'limit': limit, 'offset': offset}).encode(),
                                     headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        try:
            with opener(req, timeout=60) as res:
                data = json.load(res)
        except urllib.error.HTTPError as e:
            try:
                message = json.load(e)['error']['message']
            except (ValueError, KeyError, TypeError):
                message = str(e)
            raise Error(f'GA4 property {property_id}: {message}')
        rows = data.get('rows', [])
        for row in rows:
            key = page_key(row['dimensionValues'][0]['value'])
            views[key] = views.get(key, 0) + int(row['metricValues'][0]['value'])
        offset += len(rows)
        if not rows or offset >= data.get('rowCount', 0) or (top and offset >= top):
            return views


def merge(old: dict, views: dict[str, int], years: int, now: datetime) -> dict:
    """The new counts over the old file's: pages GA4 left out this time keep what they had."""
    at = now.isoformat(timespec='seconds')
    pages = dict(old.get('pages') or {}) if old.get('years', years) == years else {}
    pages.update({key: {'views': n, 'at': at} for key, n in views.items()})
    pages = dict(sorted(pages.items(), key=lambda kv: (-kv[1]['views'], kv[0])))
    return {'updated': at, 'years': years, 'pages': pages}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    ap.add_argument('--property', default=os.environ.get('GA4_PROPERTY'),
                    help='GA4 property ID, the number in Admin > Property details (default: GA4_PROPERTY)')
    ap.add_argument('--years', type=int, default=5, help='count the views of the last N years (default: 5)')
    ap.add_argument('--top', type=int, default=0, help='fetch only the N most viewed pages (default: all)')
    ap.add_argument('--host', help='count only this host name (when the property covers other sites too)')
    ap.add_argument('--output', default='data/pageviews.json', help='file to merge into (default: data/pageviews.json)')
    ap.add_argument('--credentials', help='service account key file (JSON)')
    ap.add_argument('--token', default=os.environ.get('GA4_ACCESS_TOKEN'), help='OAuth access token (default: GA4_ACCESS_TOKEN)')
    args = ap.parse_args(argv)
    prop = (args.property or '').strip().removeprefix('properties/')
    try:
        if not prop.isdigit():
            raise Error('which GA4 property? pass --property 123456789 (Admin > Property details > Property ID)')
        if not 1 <= args.years <= 10:
            raise Error('--years must be between 1 and 10')
        out = Path(args.output)
        try:
            old = json.loads(out.read_text(encoding='utf-8')) if out.is_file() else {}
        except ValueError:
            raise Error(f'{out} is not JSON; move it away to start over')
        views = fetch(prop, args.token or access_token(args.credentials), args.years, args.top, args.host)
        data = merge(old, views, args.years, datetime.now().astimezone())
        out.parent.mkdir(parents=True, exist_ok=True)
        # Written whole or not at all: a build reading it never sees half a file.
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=out.parent, delete=False, suffix='.tmp') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write('\n')
        os.replace(f.name, out)
    except (Error, OSError) as e:
        print(f'ga4_pageviews: {e}', file=sys.stderr)
        return 1
    kept = len(data['pages']) - len(views)
    print(f'{sum(views.values()):,} views on {len(views):,} pages from GA4'
          + (f', {kept:,} more kept from the last run' if kept else '') + f' -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
