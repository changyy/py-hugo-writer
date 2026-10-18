#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-auth[requests]>=2"]
# ///
"""Page views per page from Google Analytics 4, merged into data/pageviews.json for Hugo templates.

    uv run ga4_pageviews.py --property 123456789 [--years 5] [--top 2000] [--output data/pageviews.json]
    uv run ga4_pageviews.py --csv exported.csv [--output data/pageviews.json]

Each run asks GA4 for the views of the last --years (the most viewed --top pages only, if given)
and merges them into the file: pages in the answer get the new count and today's date, pages
left out keep the count and date they had. So a small --top keeps the file short to fetch, and
old posts nobody reads any more keep their last known number.

--csv reads a report downloaded from the GA4 web UI instead, no Google sign-in needed: Reports >
Engagement > Pages and screens, dimension "Page path and screen class", the date range wanted,
then Share this report > Download file > Download CSV. Its counts are dated by the report's end date.

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
import csv
import json
import re
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, time
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


# The views column of a downloaded report, in the languages GA4 is used in most; else the second column.
VIEWS_HEADERS = {'views', '瀏覽', '瀏覽次數', '浏览', '浏览次数', '表示回数', '조회수', 'aufrufe', 'vues', 'visualizaciones'}


def read_csv(text: str) -> tuple[dict[str, int], datetime | None]:
    """A report downloaded from the GA4 web UI: views per page, and the report's end date."""
    end, lines = None, []
    for line in text.lstrip('\ufeff').splitlines():
        if line.startswith('#'):
            # "# End date: 20261017", "# 結束日期：20261017"; the start date comes first.
            if m := re.search(r'(\d{8})\s*$', line):
                end = datetime.strptime(m[1], '%Y%m%d')
        elif line.strip():
            lines.append(line)
    rows = list(csv.reader(lines))
    if not rows:
        raise Error('the CSV has no rows')
    header = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(header) if h in VIEWS_HEADERS), 1)
    views = {}
    for row in rows[1:]:
        if len(row) <= col or not row[0].startswith('/'):    # a total, or not a page
            continue
        try:
            n = int(float(row[col].replace(',', '').strip() or 0))
        except ValueError:
            raise Error(f'not a number of views: {row[col]!r} ({row[0]})')
        key = page_key(row[0])
        views[key] = views.get(key, 0) + n
    if not views and len(rows) > 1:
        raise Error('no page paths found; download the report with the "Page path" dimension')
    # Counted to the end of the report's last day.
    return views, end and datetime.combine(end.date(), time(23, 59, 59)).astimezone()


def merge(old: dict, views: dict[str, int], years: int | None, now: datetime, at: datetime | None = None) -> dict:
    """The new counts over the old file's: pages GA4 left out this time keep what they had.

    at: when the counts are from (a CSV's end date), now if not given. Fetching another --years
    than the file was made with starts it over; a CSV, whatever its dates, merges."""
    stamp = (at or now).isoformat(timespec='seconds')
    same = years is None or old.get('years', years) == years
    pages = dict(old.get('pages') or {}) if same else {}
    pages.update({key: {'views': n, 'at': stamp} for key, n in views.items()})
    pages = dict(sorted(pages.items(), key=lambda kv: (-kv[1]['views'], kv[0])))
    data = {'updated': now.isoformat(timespec='seconds'), 'years': years or old.get('years'), 'pages': pages}
    return {k: v for k, v in data.items() if v is not None}


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
    ap.add_argument('--csv', help='read a report downloaded from the GA4 web UI instead of asking GA4')
    args = ap.parse_args(argv)
    prop = (args.property or '').strip().removeprefix('properties/')
    try:
        if not args.csv and not prop.isdigit():
            raise Error('which GA4 property? pass --property 123456789 (Admin > Property details > Property ID)')
        if not 1 <= args.years <= 10:
            raise Error('--years must be between 1 and 10')
        out = Path(args.output)
        try:
            old = json.loads(out.read_text(encoding='utf-8')) if out.is_file() else {}
        except ValueError:
            raise Error(f'{out} is not JSON; move it away to start over')
        now = datetime.now().astimezone()
        if args.csv:
            views, at = read_csv(Path(args.csv).read_text(encoding='utf-8-sig'))
            data = merge(old, views, None, now, at)
        else:
            views = fetch(prop, args.token or access_token(args.credentials), args.years, args.top, args.host)
            data = merge(old, views, args.years, now)
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
    print(f'{sum(views.values()):,} views on {len(views):,} pages from ' + (args.csv or 'GA4')
          + (f', {kept:,} more kept from the last run' if kept else '') + f' -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
