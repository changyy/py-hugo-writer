import http.client
import json
import threading
import time

import pytest

from hugo_writer.preview import HugoPreview
from hugo_writer.server import App

from .conftest import BUNDLE, needs_hugo
from .test_images import png

WRITE = {'X-Hugo-Writer': '1', 'Content-Type': 'application/json'}


def serve(site, preview=None):
    app = App(site, preview, 0)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    return app


@pytest.fixture
def app(site):
    app = serve(site)
    yield app
    app.shutdown()
    app.server_close()


def request(app, method, path, body=None, headers=None, host=None):
    port = app.server_address[1]
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    conn.request(method, path, body=body, headers={'Host': host or f'127.0.0.1:{port}', **(headers or {})})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, res.getheader('Content-Type'), data


def test_serves_the_editor_and_its_vendored_files(app):
    status, _, html = request(app, 'GET', '/')
    assert status == 200 and b'hugo-writer' in html
    status, ctype, js = request(app, 'GET', '/static/vendor/toastui-editor-3.2.2/toastui-editor-all.min.js')
    assert status == 200 and 'javascript' in ctype and b'@toast-ui/editor' in js
    assert request(app, 'GET', '/static/../server.py')[0] == 404


def test_rejects_other_hosts(app):
    assert request(app, 'GET', '/api/info', host='evil.example')[0] == 403


def test_writes_need_the_custom_header(app):
    body = json.dumps({'path': BUNDLE, 'text': 'x'})
    assert request(app, 'PUT', '/api/post', body, {'Content-Type': 'application/json'})[0] == 403
    assert request(app, 'PUT', '/api/post', body, WRITE)[0] == 200
    assert app.site.read(BUNDLE) == 'x'


def test_bad_paths_are_client_errors(app):
    status, _, data = request(app, 'GET', '/api/post?path=../hugo.toml')
    assert status == 400 and 'error' in json.loads(data)


def test_image_upload_lands_next_to_a_bundle_post(app):
    _, _, data = request(app, 'POST', '/api/post', json.dumps({'title': 'Pics', 'slug': 'pics'}), WRITE)
    rel = json.loads(data)['path']
    status, _, data = request(app, 'POST', f'/api/image?post={rel}&name=Shot.PNG', png(2000, 1000),
                              {'X-Hugo-Writer': '1'})
    res = json.loads(data)
    # Named after the post, not the uploaded file (screenshot names carry dates and more).
    assert status == 200 and res['url'] == 'pics-01.webp'
    assert (app.site.root / res['file']).parent == (app.site.root / rel).parent
    _, _, data = request(app, 'POST', f'/api/image?post={rel}&name=Other.jpg', png(10, 10), {'X-Hugo-Writer': '1'})
    assert json.loads(data)['url'] == 'pics-02.webp'


def test_site_urls_need_the_preview(app):
    assert request(app, 'GET', '/2024/05/hello-markdown/sample.webp')[0] == 404


@needs_hugo
def test_preview_renders_posts_and_the_editor_proxies_their_images(site):
    preview = HugoPreview(site).start()
    app = serve(site, preview)
    try:
        status, _, html = request(app, 'GET', '/2024/05/hello-markdown.html')
        assert status == 200 and b'Hello, Markdown' in html
        status, ctype, _ = request(app, 'GET', '/2024/05/hello-markdown/sample.webp')
        assert status == 200 and ctype == 'image/webp'

        # A new draft shows up in the preview once hugo notices the file.
        _, _, data = request(app, 'POST', '/api/post', json.dumps({'title': 'Fresh', 'slug': 'fresh'}), WRITE)
        for _ in range(60):
            status, _, html = request(app, 'GET', '/' + time.strftime('%Y/%m') + '/fresh.html')
            if status == 200:
                break
            time.sleep(0.5)
        assert status == 200 and b'Fresh' in html
    finally:
        app.shutdown()
        app.server_close()
        preview.stop()


def test_posts_with_html_blocks_are_flagged(app):
    _, _, data = request(app, 'GET', '/api/post?path=content/posts/2024/07/with-embed.md')
    assert json.loads(data)['has_html'] is True
    _, _, data = request(app, 'GET', f'/api/post?path={BUNDLE}')
    assert json.loads(data)['has_html'] is False


def test_image_width_picks_a_smaller_size_only(app):
    from PIL import Image
    import io
    post = BUNDLE
    for asked, got in ((256, 256), (0, 1024), (5000, 1024)):
        _, _, data = request(app, 'POST', f'/api/image?post={post}&name=a.png&width={asked}', png(2000, 1000),
                             {'X-Hugo-Writer': '1'})
        saved = app.site.root / json.loads(data)['file']
        assert Image.open(io.BytesIO(saved.read_bytes())).width == got
