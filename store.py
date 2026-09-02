"""
Local filesystem artifact store + signed-URL HTTP server for the
share-artifact plugin.

No git, no repo, no push. An artifact is published by writing it straight
to local disk under the plugin's profile-scoped data dir and computing an
HMAC signature over its id. The signature - not a login wall - is what
makes the URL "shareable": anyone holding the full URL (id + sign) can
view it, nobody can enumerate or guess other artifacts' ids.

Serving is a small stdlib ThreadingHTTPServer bound to 127.0.0.1, fronted
by whatever public reverse proxy already terminates TLS for this Hermes
instance (Caddy/nginx) via a single added route:

    handle /share/artifact/* {
        reverse_proxy 127.0.0.1:<port>
    }

URL shape: https://<host>/share/artifact/<id>/<sign>/<optional/sub/path>
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import logging
import mimetypes
import re
import shutil
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[0-9a-f]{10,32}$")
_SIGN_RE = re.compile(r"^[0-9a-f]{16,64}$")

_MARKDOWN_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown.min.css">
<style>
  body {{ margin:0; background:#0d1117; }}
  .markdown-body {{ box-sizing:border-box; max-width:900px; margin:0 auto; padding:2.5rem 1.5rem; }}
</style>
</head>
<body class="markdown-body">
<article id="content"></article>
<script id="src" type="text/markdown">{escaped}</script>
<script>
  document.getElementById('content').innerHTML =
    marked.parse(document.getElementById('src').textContent);
</script>
</body></html>
"""


class PublishError(RuntimeError):
    pass


class ArtifactStore:
    """Owns the artifacts/ directory and _meta.json bookkeeping for one plugin."""

    def __init__(self, root: Path, secret: str):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._secret = secret.encode("utf-8")

    def sign(self, artifact_id: str) -> str:
        return hmac.new(self._secret, artifact_id.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    def verify(self, artifact_id: str, sign: str) -> bool:
        return hmac.compare_digest(self.sign(artifact_id), sign)

    def dir_for(self, artifact_id: str) -> Path:
        return self.root / artifact_id

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def publish(
        self,
        *,
        path: Optional[Path] = None,
        html: Optional[str] = None,
        title: str = "",
        entrypoint: str = "index.html",
    ) -> dict:
        if path is None and html is None:
            raise PublishError("either path or html must be given")
        if path is not None and not path.exists():
            raise PublishError(f"path does not exist: {path}")

        artifact_id = self._new_id()
        dest = self.dir_for(artifact_id)
        dest.mkdir(parents=True, exist_ok=False)
        try:
            served_entrypoint = _write_payload(dest, path, html, entrypoint)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

        meta = {
            "id": artifact_id,
            "title": title or artifact_id,
            "entrypoint": served_entrypoint,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (dest / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {**meta, "sign": self.sign(artifact_id)}

    def delete(self, artifact_id: str) -> bool:
        if not _ID_RE.match(artifact_id):
            raise PublishError(f"invalid artifact id: {artifact_id!r}")
        dest = self.dir_for(artifact_id)
        if not dest.exists():
            return False
        shutil.rmtree(dest)
        return True

    def read_meta(self, artifact_id: str) -> Optional[dict]:
        meta_path = self.dir_for(artifact_id) / "_meta.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def render_markdown_html(markdown: str, title: str) -> str:
    escaped = markdown.replace("</script>", "<\\/script>")
    return _MARKDOWN_TEMPLATE.format(title=title or "Report", escaped=escaped)


def _write_payload(dest: Path, path: Optional[Path], html: Optional[str], entrypoint_name: str) -> str:
    if html is not None:
        (dest / entrypoint_name).write_text(html, encoding="utf-8")
        return entrypoint_name

    assert path is not None
    if path.is_dir():
        for child in path.iterdir():
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        if (dest / entrypoint_name).exists():
            return entrypoint_name
        if (dest / "index.html").exists():
            return "index.html"
        raise PublishError(
            f"directory has no '{entrypoint_name}' or 'index.html' at its root; "
            "pass entrypoint= to pick the file to serve"
        )

    shutil.copy2(path, dest / path.name)
    if path.suffix.lower() in (".html", ".htm"):
        if path.name != "index.html":
            shutil.copy2(path, dest / "index.html")
        return "index.html"
    wrapper = _wrap_single_file(path.name)
    (dest / "index.html").write_text(wrapper, encoding="utf-8")
    return "index.html"


def _wrap_single_file(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        body = f'<img src="./{filename}" style="max-width:100%">'
    elif ext == ".md":
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<script src='https://cdn.jsdelivr.net/npm/marked/marked.min.js'></script></head>"
            "<body><article id='content'></article><script>"
            f"fetch('./{filename}').then(r=>r.text()).then(t=>{{"
            "document.getElementById('content').innerHTML = marked.parse(t);});"
            "</script></body></html>"
        )
    else:
        body = f'<iframe src="./{filename}" style="width:100%;height:100vh;border:0"></iframe>'
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{filename}</title></head><body style='margin:0'>{body}</body></html>"


# ---------------------------------------------------------------------------
# HTTP server: signed-URL static file serving, bound to 127.0.0.1 only.
# The public reverse proxy (Caddy/nginx) terminates TLS and forwards
# /share/artifact/* here - this process never talks TLS or touches the
# public interface directly.
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^/share/artifact/([0-9a-f]{10,32})/([0-9a-f]{16,64})(/.*)?$")


def _safe_join(root: Path, rest: str) -> Optional[Path]:
    rest = unquote(rest or "").lstrip("/")
    if not rest:
        return None
    candidate = (root / rest).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


class _Handler(http.server.BaseHTTPRequestHandler):
    store: ArtifactStore = None  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.debug("share-artifact http: " + fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve()

    def _serve(self) -> None:
        m = _URL_RE.match(self.path.split("?", 1)[0])
        if not m:
            self._send(404, b"Not found")
            return
        artifact_id, sign, rest = m.group(1), m.group(2), m.group(3) or ""

        if not self.store.verify(artifact_id, sign):
            self._send(403, b"Invalid or expired link")
            return

        meta = self.store.read_meta(artifact_id)
        if meta is None:
            self._send(404, b"Artifact not found")
            return

        artifact_dir = self.store.dir_for(artifact_id)
        if rest and rest != "/":
            target = _safe_join(artifact_dir, rest)
            if target is None or not target.is_file():
                self._send(404, b"Not found")
                return
        else:
            target = artifact_dir / meta.get("entrypoint", "index.html")
            if not target.is_file():
                self._send(404, b"Artifact has no entrypoint")
                return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError:
            self._send(404, b"Not found")
            return
        self._send(200, data, content_type)


class ArtifactServer:
    """Owns the daemon ThreadingHTTPServer lifecycle. Idempotent start/stop."""

    def __init__(self, store: ArtifactStore, host: str = "127.0.0.1", port: int = 8791):
        self.store = store
        self.host = host
        self.port = port
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        """Start the server. Returns False (no-op) if already running or the
        port is already bound by a previous instance of this same server
        (e.g. a prior, not-yet-torn-down plugin generation)."""
        with self._lock:
            if self._httpd is not None:
                return True
            handler = type("_BoundHandler", (_Handler,), {"store": self.store})
            try:
                httpd = http.server.ThreadingHTTPServer((self.host, self.port), handler)
            except OSError as e:
                logger.warning(
                    "share-artifact: could not bind %s:%s (%s) - "
                    "server may already be running from a prior plugin generation",
                    self.host, self.port, e,
                )
                return False
            httpd.daemon_threads = True
            thread = threading.Thread(
                target=httpd.serve_forever, name="share-artifact-http", daemon=True,
            )
            thread.start()
            self._httpd = httpd
            self._thread = thread
            logger.info("share-artifact: serving on %s:%s", self.host, self.port)
            return True

    def stop(self) -> None:
        with self._lock:
            if self._httpd is None:
                return
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False
