"""scripts/ga4_pageviews.py: GA4 answers merged into data/pageviews.json; the demo site's partial reading it."""
import importlib.util
import io
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .conftest import HUGO, needs_hugo

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'ga4_pageviews.py'
spec = importlib.util.spec_from_file_location('ga4_pageviews', SCRIPT)
ga4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ga4)

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=timezone(timedelta(hours=8)))


def fake_ga4(rows, sent):
    """An opener answering runReport from rows [(path, views)], paged by the request's limit / offset."""
    def opener(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        page = rows[body['offset']:body['offset'] + body['limit']]
        return io.BytesIO(json.dumps({'rowCount': len(rows), 'rows': [
            {'dimensionValues': [{'value': p}], 'metricValues': [{'value': str(n)}]} for p, n in page]}).encode())
    return opener


def test_paths_are_keyed_like_rel_permalink():
    # Cloudflare Pages serves /x.html at /x: one page either way.
    assert ga4.page_key('/2024/05/hello.html?utm_source=x') == ga4.page_key('/2024/05/hello') == '/2024/05/hello'
    assert ga4.page_key('/2024/05/Hello.HTML') == '/2024/05/hello'
    assert ga4.page_key('/posts/Hello/') == ga4.page_key('/posts/hello/index.html') == '/posts/hello'
    assert ga4.page_key('/posts/中文-測試/') == ga4.page_key('/posts/%E4%B8%AD%E6%96%87-%E6%B8%AC%E8%A9%A6') \
        == '/posts/%e4%b8%ad%e6%96%87-%e6%b8%ac%e8%a9%a6'
    assert ga4.page_key('/') == '/'


def test_fetch_pages_through_and_stops_at_top(monkeypatch):
    monkeypatch.setattr(ga4, 'PAGE_SIZE', 2)
    rows = [('/a/', 9), ('/a', 1), ('/b/', 5), ('/c/', 3), ('/d/', 2)]
    sent = []
    assert ga4.fetch('1', 't', top=3, opener=fake_ga4(rows, sent)) == {'/a': 10, '/b': 5}
    assert [(b['offset'], b['limit']) for b in sent] == [(0, 2), (2, 1)]
    assert sent[0]['dateRanges'][0]['startDate'] == '1825daysAgo'
    sent.clear()
    assert len(ga4.fetch('1', 't', opener=fake_ga4(rows, sent))) == 4 and len(sent) == 3


def test_pages_left_out_keep_their_last_count():
    first = ga4.merge({}, {'/a': 10, '/old': 4}, 5, NOW - timedelta(days=40))
    second = ga4.merge(first, {'/a': 12, '/b': 7}, 5, NOW)
    assert second['pages'] == {'/a': {'views': 12, 'at': NOW.isoformat()}, '/b': {'views': 7, 'at': NOW.isoformat()},
                               '/old': first['pages']['/old']}
    assert list(second['pages']) == ['/a', '/b', '/old'] and second['updated'] == NOW.isoformat()
    # Another window counts differently: start over.
    assert list(ga4.merge(second, {'/b': 1}, 3, NOW)['pages']) == ['/b']


@needs_hugo
def test_the_partial_shows_views_and_how_old_they_are(site_dir):
    ago = datetime.now().astimezone() - timedelta(days=40)
    (site_dir / 'data' / 'pageviews.json').write_text(json.dumps({'pages': {
        '/2024/05/hello-markdown': {'views': 1245, 'at': ago.isoformat(timespec='seconds')},
        '/2024/07/with-embed': {'views': 3, 'at': ago.isoformat(timespec='seconds')}}}))
    with (site_dir / 'hugo.toml').open('a') as f:
        f.write("\n[params.pageviews]\n  min = 10\n")
    subprocess.run([HUGO, '--quiet', '--destination', 'public'], cwd=site_dir, check=True, capture_output=True)
    pages = {p.relative_to(site_dir / 'public').as_posix(): p.read_text() for p in (site_dir / 'public').rglob('*.html')}
    hello = next(html for path, html in pages.items() if 'hello-markdown' in path and 'pageviews' in html)
    assert '1,245 views (as of <time' in hello and '>1mo ago</time>)' in hello
    assert not any('class="pageviews"' in html for path, html in pages.items() if 'with-embed' in path)  # below min


def test_hugo_writer_reads_the_keys_the_script_writes():
    from hugo_writer.pageviews import page_key
    for path in ('/', '/a/b.html', '/a/b/', '/a/b/index.html?x=1', '/A/中文 測試/', "/a/(b)+c!/"):
        assert page_key(path) == ga4.page_key(path)


# As the GA4 web UI downloads it (zh-TW UI): comments with the dates, CRLF, extra columns.
REPORT = ('# ----------------------------------------\r\n# 網頁和畫面: 網頁路徑與畫面類別\r\n# 資源：blog.example\r\n'
          '# \r\n# 所有使用者\r\n# 開始日期：20260720\r\n# 結束日期：20261017\r\n'
          '網頁路徑與畫面類別,瀏覽,活躍使用者,每位活躍使用者觀看數\r\n'
          '/,50,26,1.92\r\n/2026/10/hello,8,3,2.6\r\n/2026/10/hello.html,2,1,2\r\n"/2026/05/big","1,234",9,1\r\n')


def test_reads_a_report_downloaded_from_ga4():
    views, at = ga4.read_csv('﻿' + REPORT)
    assert views == {'/': 50, '/2026/10/hello': 10, '/2026/05/big': 1234}
    assert at.date().isoformat() == '2026-10-17' and (at.hour, at.minute) == (23, 59)
    # English UI, a totals row, no dates.
    views, at = ga4.read_csv('Page path and screen class,Views,Active users\nGrand total,99,9\n/a/,3,1\n')
    assert views == {'/a': 3} and at is None


def test_a_report_merges_and_is_dated_by_its_end_date(tmp_path):
    out = tmp_path / 'data' / 'pageviews.json'
    report = tmp_path / 'report.csv'
    report.write_text(REPORT, encoding='utf-8')
    out.parent.mkdir()
    out.write_text(json.dumps({'years': 5, 'pages': {'/old': {'views': 7, 'at': '2026-01-01T00:00:00+08:00'}}}))
    assert ga4.main(['--csv', str(report), '--output', str(out)]) == 0
    data = json.loads(out.read_text())
    assert data['pages']['/old']['views'] == 7 and data['years'] == 5
    assert data['pages']['/2026/10/hello'] == {'views': 10, 'at': data['pages']['/']['at']}
    assert data['pages']['/']['at'].startswith('2026-10-17T23:59:59')


def test_hugo_writer_reads_reports_and_merges_as_the_script_does():
    from hugo_writer import pageviews
    assert pageviews.read_csv(REPORT) == ga4.read_csv(REPORT)
    old = {'years': 5, 'pages': {'/old': {'views': 7, 'at': '2026-01-01T00:00:00+08:00'}}}
    views, at = ga4.read_csv(REPORT)
    assert pageviews.merge(old, views, None, NOW, at) == ga4.merge(old, views, None, NOW, at)
    assert pageviews.merge(old, views, 3, NOW) == ga4.merge(old, views, 3, NOW)
