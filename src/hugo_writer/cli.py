"""`hugo-writer [SITE]`: start the editor for a Hugo site and open it in the browser."""
import argparse
import shutil
import signal
import sys
import webbrowser

from ._version import __version__
from .preview import HugoPreview
from .server import App
from .site import Site, SiteError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='hugo-writer', description=__doc__.split(':', 1)[1].strip())
    ap.add_argument('site', nargs='?', default='.', help='Hugo site directory (default: current directory)')
    ap.add_argument('--port', type=int, default=8765, help='editor port (default: 8765, the next free one if taken)')
    ap.add_argument('--hugo', default='hugo', help='hugo executable (default: hugo on PATH)')
    ap.add_argument('--no-preview', action='store_true', help="don't start the hugo server preview")
    ap.add_argument('--no-browser', action='store_true', help="don't open the browser")
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = ap.parse_args(argv)

    if shutil.which(args.hugo) is None:
        print(f'hugo-writer: cannot find "{args.hugo}"; install Hugo (https://gohugo.io/installation/) '
              'or pass --hugo /path/to/hugo', file=sys.stderr)
        return 1
    try:
        site = Site(args.site, args.hugo)
    except SiteError as e:
        print(f'hugo-writer: {e}', file=sys.stderr)
        return 1

    # `kill` (SIGTERM) should clean up like Ctrl+C does, or the preview hugo server is left running.
    signal.signal(signal.SIGTERM, _interrupt)
    preview = app = None
    try:
        if not args.no_preview:
            print('Starting hugo server for the preview…', flush=True)
            try:
                preview = HugoPreview(site).start()
            except RuntimeError as e:
                print(f'hugo-writer: {e}', file=sys.stderr)
                return 1

        for port in range(args.port, args.port + 20):
            try:
                app = App(site, preview, port)
                break
            except OSError:
                continue
        if app is None:
            print(f'hugo-writer: ports {args.port}-{args.port + 19} are all in use', file=sys.stderr)
            return 1

        url = f'http://127.0.0.1:{app.server_address[1]}/'
        print(f'hugo-writer {__version__}: {site.root}')
        print(f'  editor   {url}')
        if preview:
            print(f'  preview  {preview.base}/')
        print('Press Ctrl+C to stop.', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        app.serve_forever()
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl+C shouldn't cut cleanup short either
    finally:
        if app:
            app.server_close()
        if preview:
            preview.stop()
    return 0


def _interrupt(signum, frame):
    # Ignore repeats while cleaning up: `uv run` / `uvx` forward the signal they get, so a
    # `kill` aimed at both can arrive twice and would cut preview.stop() short.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


if __name__ == '__main__':
    sys.exit(main())
