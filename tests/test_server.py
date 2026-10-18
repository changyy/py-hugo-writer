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
    for icon, ctype in (('favicon.ico', 'image/'), ('favicon-32.png', 'image/png'), ('icon-512.png', 'image/png')):
        status, got, _ = request(app, 'GET', f'/static/{icon}')
        assert status == 200 and got.startswith(ctype)


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


def test_pages_need_the_preview(app):
    # Images come from disk (see below); pages only from a preview server, and there is none here.
    assert request(app, 'GET', '/2024/05/hello-markdown.html')[0] == 404


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


def test_image_list_and_delete_api(app):
    _, _, data = request(app, 'GET', f'/api/images?post={BUNDLE}')
    assert [i['name'] for i in json.loads(data)['images']] == ['sample.webp']
    body = json.dumps({'post': BUNDLE, 'name': 'sample.webp'})
    assert request(app, 'POST', '/api/image/delete', body, {'Content-Type': 'application/json'})[0] == 403
    assert request(app, 'POST', '/api/image/delete', body, WRITE)[0] == 200
    assert request(app, 'POST', '/api/image/delete', json.dumps({'post': BUNDLE, 'name': 'index.md'}), WRITE)[0] == 400
    _, _, data = request(app, 'GET', f'/api/images?post={BUNDLE}')
    assert json.loads(data)['images'] == []


@needs_hugo
def test_library_rename_and_settings_api(app):
    _, _, data = request(app, 'GET', '/api/library')
    (sample,) = [i for i in json.loads(data)['images'] if i['name'] == 'sample.webp']
    assert [r['path'] for r in sample['refs']] == [BUNDLE]
    _, _, data = request(app, 'POST', '/api/image/rename', json.dumps({'post': BUNDLE, 'name': 'sample.webp'}), WRITE)
    assert json.loads(data)['name'] == 'hello-markdown-01.webp'
    assert 'hello-markdown-01.webp' in app.site.read(BUNDLE)
    status, _, data = request(app, 'POST', '/api/settings', json.dumps({'image_max_width': 640}), WRITE)
    assert status == 200 and json.loads(data)['settings']['image_max_width'] == 640 == app.site.settings.image_max_width
    assert request(app, 'POST', '/api/settings', json.dumps({'image_quality': 500}), WRITE)[0] == 400
    file = 'content/posts/2024/05/hello-markdown/hello-markdown-01.webp'
    assert request(app, 'POST', '/api/library/delete', json.dumps({'file': file}), WRITE)[0] == 200
    assert request(app, 'POST', '/api/library/delete', json.dumps({'file': 'hugo.toml'}), WRITE)[0] == 400


@needs_hugo
def test_images_are_served_from_disk_even_when_hugo_would_not(site):
    # publishResources = false (as in the demo site): Hugo does not publish an image no page links to.
    folder = site.root / 'content/posts/2024/05/hello-markdown'
    (folder / 'unused.png').write_bytes(png(8, 8))
    app = serve(site)                       # no preview server at all
    try:
        status, ctype, data = request(app, 'GET', '/2024/05/hello-markdown/unused.png')
        assert status == 200 and ctype == 'image/png' and data == (folder / 'unused.png').read_bytes()
        assert request(app, 'GET', '/2024/05/hello-markdown/missing.png')[0] == 404
        assert request(app, 'GET', '/2024/05/hello-markdown/../../../../hugo.toml')[0] == 404
    finally:
        app.shutdown()
        app.server_close()


@needs_hugo
def test_renaming_twice_is_an_error_not_a_crash(app):
    body = json.dumps({'post': BUNDLE, 'name': 'sample.webp', 'new_name': 'chosen'})
    status, _, data = request(app, 'POST', '/api/image/rename', body, WRITE)
    assert status == 200 and json.loads(data)['name'] == 'chosen.webp'
    status, _, data = request(app, 'POST', '/api/image/rename', body, WRITE)   # the file is gone now
    assert status == 400 and 'no such image' in json.loads(data)['error']
    file = json.dumps({'file': 'content/posts/2024/05/hello-markdown/chosen.webp', 'new_name': ''})
    assert json.loads(request(app, 'POST', '/api/image/rename', file, WRITE)[2])['name'] == 'hello-markdown-01.webp'


def test_featured_api(app):
    _, _, data = request(app, 'GET', f'/api/post?path={BUNDLE}')
    assert json.loads(data)['featured'] is None
    status, _, data = request(app, 'POST', '/api/featured', json.dumps({'path': BUNDLE, 'image': 'sample.webp'}), WRITE)
    res = json.loads(data)
    assert status == 200 and res['featured'] == 'sample.webp' and 'images: ["sample.webp"]' in res['text']
    _, _, data = request(app, 'POST', '/api/featured', json.dumps({'path': BUNDLE, 'image': None}), WRITE)
    assert json.loads(data)['featured'] is None and 'images:' not in app.site.read(BUNDLE)
    assert request(app, 'POST', '/api/settings', json.dumps({'featured_field': 'a.b.c'}), WRITE)[0] == 400


@needs_hugo
def test_tags_api(app):
    _, _, data = request(app, 'GET', f'/api/post?path={BUNDLE}')
    post = json.loads(data)
    assert post['tags'] == ['demo']
    body = json.dumps({'path': BUNDLE, 'field': 'tags', 'value': ['demo', '筆記'], 'version': post['version']})
    status, _, data = request(app, 'POST', '/api/front', body, WRITE)
    res = json.loads(data)
    assert status == 200 and res['tags'] == ['demo', '筆記'] and 'tags: ["demo", "筆記"]' in res['text']
    _, _, data = request(app, 'GET', '/api/tags')
    assert {'name': '筆記', 'count': 1} in json.loads(data)['tags']
    bad = json.dumps({'path': BUNDLE, 'field': 'tags', 'value': 'demo'})
    assert request(app, 'POST', '/api/front', bad, WRITE)[0] == 400


@needs_hugo
def test_post_versions_and_conflicts(app):
    _, _, data = request(app, 'GET', f'/api/post?path={BUNDLE}')
    post = json.loads(data)
    assert post['url'] == '/2024/05/hello-markdown.html' and post['version']
    body = post['text'] + 'more\n'
    status, _, data = request(app, 'PUT', '/api/post', json.dumps({'path': BUNDLE, 'text': body, 'version': post['version']}), WRITE)
    saved = json.loads(data)
    assert status == 200 and saved['version'] != post['version'] and 'url' not in saved   # body only: no re-listing
    # A save that changes the front matter reports where the page lives now.
    moved = body.replace('url: /2024/05/hello-markdown.html', 'slug: renamed')
    _, _, data = request(app, 'PUT', '/api/post', json.dumps({'path': BUNDLE, 'text': moved, 'version': saved['version']}), WRITE)
    saved = json.loads(data)
    assert saved['url'] == '/posts/2024/05/renamed/' and saved['front_url'] is None
    # Changed on disk by someone else: every write with the old version is a 409, the file is kept.
    app.site.resolve(BUNDLE).write_text('changed elsewhere\n')
    for method, path, extra in (('PUT', '/api/post', {'text': 'mine'}), ('POST', '/api/draft', {'draft': True}),
                                ('POST', '/api/front', {'field': 'title', 'value': 'x'})):
        body = json.dumps({'path': BUNDLE, 'version': saved['version'], **extra})
        assert request(app, method, path, body, WRITE)[0] == 409
    assert app.site.read(BUNDLE) == 'changed elsewhere\n'


def test_reports_git_status_and_the_post_mtime(app):
    status, _, body = request(app, 'GET', '/api/git')
    assert status == 200 and 'repo' in json.loads(body)
    post = json.loads(request(app, 'GET', f'/api/post?path={BUNDLE}')[2])
    assert post['mtime'] == app.site.resolve(BUNDLE).stat().st_mtime

