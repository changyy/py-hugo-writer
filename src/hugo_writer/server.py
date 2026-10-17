"""The local web app: one HTML page plus a small JSON API over the Site."""
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from . import images
from ._version import __version__
from .site import Conflict, SiteError, atomic_write, front_matter, get_featured, has_raw_html, version

# Every request that changes files must carry this header. A web page on another
# origin can't add a custom header without a CORS preflight, which we never grant,
# so random sites can't make the browser write into your blog.
WRITE_HEADER = 'X-Hugo-Writer'


class App(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, site, preview, port: int):
        self.site = site
        self.preview = preview
        super().__init__(('127.0.0.1', port), Handler)

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        return {f'127.0.0.1:{port}', f'localhost:{port}'}


class Handler(BaseHTTPRequestHandler):
    server: App
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # keep the terminal quiet; errors still go to log_error
        pass

    # -- plumbing ----------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _error(self, status: int, message: str):
        self._json({'error': message}, status)

    def _body(self) -> bytes:
        length = int(self.headers.get('Content-Length') or 0)
        if length > images.MAX_UPLOAD:
            raise SiteError('upload too large')
        return self.rfile.read(length)

    def _query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _guard(self, write: bool) -> bool:
        # Host check stops DNS-rebinding pages from talking to us under another name.
        if self.headers.get('Host') not in self.server.allowed_hosts:
            self._error(HTTPStatus.FORBIDDEN, 'bad host')
            return False
        if write and self.headers.get(WRITE_HEADER) != '1':
            self._error(HTTPStatus.FORBIDDEN, f'missing {WRITE_HEADER} header')
            return False
        return True

    def _dispatch(self, routes: dict, write: bool):
        if not self._guard(write):
            return
        path = urlsplit(self.path).path
        route = routes.get(path)
        if route is None and not write:
            route = Handler.static if path.startswith('/static/') else Handler.proxy
        if route is None:
            return self._error(HTTPStatus.NOT_FOUND, 'not found')
        try:
            route(self)
        except Conflict as e:
            self._error(HTTPStatus.CONFLICT, str(e))
        except SiteError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))

    def do_GET(self):
        self._dispatch(GET_ROUTES, write=False)

    def do_POST(self):
        self._dispatch(POST_ROUTES, write=True)

    def do_PUT(self):
        self._dispatch(PUT_ROUTES, write=True)

    # -- routes ------------------------------------------------------------------

    def index(self):
        html = resources.files('hugo_writer').joinpath('static/index.html').read_bytes()
        self._send(200, html, 'text/html; charset=utf-8')

    def static(self):
        # Files shipped inside the package (the editor's vendored JS/CSS).
        rel = urlsplit(self.path).path.removeprefix('/static/')
        node = resources.files('hugo_writer').joinpath('static')
        for part in rel.split('/'):
            if part in ('', '.', '..'):
                return self._error(HTTPStatus.NOT_FOUND, 'not found')
            node = node.joinpath(part)
        if not node.is_file():
            return self._error(HTTPStatus.NOT_FOUND, 'not found')
        ctype = mimetypes.guess_type(rel)[0] or 'application/octet-stream'
        self._send(200, node.read_bytes(), ctype + ('; charset=utf-8' if ctype.startswith(('text/', 'application/javascript')) else ''))

    def proxy(self):
        # Anything else is a site URL, an image in a post mostly. Images come straight from disk
        # (Hugo may not publish an unused or just-dropped one); the rest from the preview server.
        url_path = urlsplit(self.path).path
        try:
            file = self.server.site.image_file(urllib.parse.unquote(url_path))
        except SiteError:
            file = None
        if file is not None:
            ctype = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            return self._send(200, file.read_bytes(), ctype)
        preview = self.server.preview
        if preview is None:
            return self._error(HTTPStatus.NOT_FOUND, 'no preview server')
        try:
            with urllib.request.urlopen(preview.base + self.path, timeout=30) as res:
                self._send(res.status, res.read(), res.headers.get('Content-Type', 'application/octet-stream'))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(), e.headers.get('Content-Type', 'text/plain'))
        except OSError as e:
            self._error(HTTPStatus.BAD_GATEWAY, f'preview server: {e}')

    def info(self):
        site, preview = self.server.site, self.server.preview
        self._json({
            'root': str(site.root),
            'name': site.root.name,
            'version': __version__,
            'preview': preview.base if preview else None,
            'settings': vars(site.settings),
        })

    def list_posts(self):
        self._json({'posts': self.server.site.posts()})

    def get_post(self):
        rel = self._query().get('path', '')
        site = self.server.site
        text = site.read(rel)
        editable = site.resolve(rel).suffix.lower() in ('.md', '.markdown')
        front = self._front(text) if editable else {'featured': None, 'front_title': None, 'front_url': None}
        # url: the permalink right now, so a post whose url / slug changed elsewhere previews where it lives.
        self._json({'path': rel, 'text': text, 'editable': editable, 'has_html': editable and has_raw_html(text),
                    'version': version(text), 'url': site.url_of(rel), **front})

    def _front(self, text: str) -> dict:
        front = {}
        for name, key in (('featured', self.server.site.settings.featured_field), ('front_title', 'title'), ('front_url', 'url')):
            try:
                front[name] = get_featured(text, key)
            except (SiteError, ValueError):
                front[name] = None
        return front

    def set_featured(self):
        data, site = json.loads(self._body()), self.server.site
        text = site.set_featured(data.get('path', ''), data.get('image') or None)
        self._json({'text': text, 'version': version(text), 'featured': site.featured(data.get('path', ''))})

    def set_front(self):
        # {path, field: title | url | featured, value, version}
        data, site = json.loads(self._body()), self.server.site
        rel = data.get('path', '')
        text = site.set_front(rel, data.get('field', ''), data.get('value'), data.get('version'))
        self._json({'text': text, 'version': version(text), 'featured': site.featured(rel)})

    def save_post(self):
        # {path, text, version}: version is what the editor loaded; a different file on disk is a 409.
        data, site = json.loads(self._body()), self.server.site
        rel, text = data['path'], data['text']
        before = site.read(rel)
        site.write(rel, text, data.get('version'))
        res = {'ok': True, 'version': version(text)}
        if front_matter(before) != front_matter(text):     # url, slug, date … may move the page
            res.update(self._front(text), url=site.url_of(rel))
        self._json(res)

    def create_post(self):
        data = json.loads(self._body())
        site, preview = self.server.site, self.server.preview
        rel = site.create(data.get('title', ''), data.get('slug', ''))
        if preview:
            preview.nudge(site.root / rel)
        self._json({'path': rel})

    def draft(self):
        data = json.loads(self._body())
        text = self.server.site.set_draft(data['path'], bool(data['draft']), data.get('version'))
        self._json({'text': text, 'version': version(text)})

    def list_images(self):
        self._json({'images': self.server.site.images(self._query().get('post', ''))})

    def delete_image(self):
        data = json.loads(self._body())
        self.server.site.delete_image(data.get('post', ''), data.get('name', ''))
        self._json({'ok': True})

    def library(self):
        self._json({'images': self.server.site.library(self._query().get('post') or None)})

    def library_delete(self):
        self.server.site.delete_file(json.loads(self._body()).get('file', ''))
        self._json({'ok': True})

    def rename_image(self):
        # {post, name} for a post's image, or {file} from the library; new_name empty = {slug}-NN.
        data, site = json.loads(self._body()), self.server.site
        if data.get('file'):
            self._json(site.rename_file(data['file'], data.get('new_name', '')))
        else:
            self._json(site.rename_image(data.get('post', ''), data.get('name', ''), data.get('new_name', '')))

    def save_settings(self):
        site = self.server.site
        site.settings = site.settings.save(site.root, json.loads(self._body()))
        self._json({'settings': vars(site.settings)})

    def upload_image(self):
        q = self._query()
        site = self.server.site
        folder, prefix = site.image_target(q.get('post', ''))
        name = q.get('name') or 'image.png'
        s = site.settings
        # ?width= picks a smaller size for this image (the editor's image size menu), never a larger one.
        width = min(int(q.get('width') or 0) or s.image_max_width, s.image_max_width)
        data, ext, removed = images.process(self._body(), name, width, s.image_format, s.image_quality,
                                            strip=s.strip_metadata)
        folder.mkdir(parents=True, exist_ok=True)
        path = images.image_path(folder, s.image_name, site.image_slug(q.get('post', '')), name, ext)
        atomic_write(path, data)
        self._json({'url': prefix + path.name, 'file': path.relative_to(site.root).as_posix(), 'bytes': len(data),
                    'removed': removed})


GET_ROUTES = {'/': Handler.index, '/api/info': Handler.info, '/api/posts': Handler.list_posts,
              '/api/post': Handler.get_post, '/api/images': Handler.list_images, '/api/library': Handler.library}
POST_ROUTES = {'/api/post': Handler.create_post, '/api/draft': Handler.draft, '/api/image': Handler.upload_image,
               '/api/image/delete': Handler.delete_image, '/api/image/rename': Handler.rename_image,
               '/api/library/delete': Handler.library_delete, '/api/settings': Handler.save_settings,
               '/api/featured': Handler.set_featured, '/api/front': Handler.set_front}
PUT_ROUTES = {'/api/post': Handler.save_post}
