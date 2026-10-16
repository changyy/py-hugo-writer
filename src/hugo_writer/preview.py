"""A private `hugo server` for the preview pane: drafts and future posts included, rendered in memory."""
import collections
import socket
import subprocess
import threading
import time


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class HugoPreview:
    def __init__(self, site, port: int | None = None):
        self.site = site
        self.port = port or free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.proc: subprocess.Popen | None = None
        self.log = collections.deque(maxlen=40)

    def start(self, timeout: float = 120) -> 'HugoPreview':
        cmd = [self.site.hugo, 'server', '--buildDrafts', '--buildFuture', '--renderToMemory',
               # The user may already run their own `hugo server` in this site; don't fight over the lock.
               '--noBuildLock', '--bind', '127.0.0.1', '--port', str(self.port), '--baseURL', 'http://127.0.0.1']
        self.proc = subprocess.Popen(cmd, cwd=self.site.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace')
        threading.Thread(target=self._drain, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError('hugo server exited:\n' + '\n'.join(self.log))
            try:
                with socket.create_connection(('127.0.0.1', self.port), timeout=0.5):
                    return self
            except OSError:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError(f'hugo server did not start within {timeout:.0f}s:\n' + '\n'.join(self.log))

    def nudge(self, path, delay: float = 1.0):
        """Rewrite a just-created file once hugo watches its new folders.

        A post in a brand-new folder is written before hugo has added that folder to its
        watch list; hugo sees the event but renders nothing until the file changes again.
        """
        def rewrite():
            try:
                path.write_bytes(path.read_bytes())
            except OSError:
                pass
        timer = threading.Timer(delay, rewrite)
        timer.daemon = True
        timer.start()

    def _drain(self):
        for line in self.proc.stdout:
            self.log.append(line.rstrip())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
