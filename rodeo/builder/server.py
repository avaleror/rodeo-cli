"""The live Rodeo Builder: serves rodeo/builder/htdocs/ and the builder Api.

The same page the static build publishes, with live answers instead of
embedded ones, plus what only a server can do (save a rodeo straight into
``~/.rodeo/profiles/``). Standard library only.

    GET  /                   index.html (version filled in, write token in a meta tag)
    GET  /<file>             a file from htdocs/
    GET  /api?action=<name>  Api.dispatch(name, "GET")
    POST /api?action=save    Api.dispatch("save", "POST", body); needs the
                             X-Rodeo-Builder-Token header from the page's meta tag

Every request must name an allowed Host (loopback names by default), which
stops DNS-rebinding pages from reaching the server; the per-run token stops
other sites from posting to it.
"""
from __future__ import annotations

import hmac
import html
import json
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import Api

HTDOCS = Path(__file__).resolve().parent / "htdocs"
TOKEN_HEADER = "X-Rodeo-Builder-Token"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY = 48 * 1024 * 1024
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}


def csp(lab_builder_url: str) -> str:
    """Content-Security-Policy for the page: own files, Google Fonts, the lab-builder frame."""
    origin = urlparse(lab_builder_url)
    frame = "{}://{}".format(origin.scheme, origin.netloc) if origin.scheme in ("http", "https") and origin.netloc else "'none'"
    return ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "frame-src {}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'".format(frame))


def page(version: str, token: str) -> bytes:
    """index.html with the version and the write token filled in."""
    text = (HTDOCS / "index.html").read_text().replace("__RODEOVERSION__", html.escape(version))
    meta = '  <meta name="rodeo-builder-token" content="{}">\n'.format(html.escape(token))
    at = text.index("</head>")
    return (text[:at] + meta + text[at:]).encode("utf-8")


def host_allowed(header: str | None, allowed: tuple[str, ...]) -> bool:
    """True when the Host header's name (port stripped) is one of *allowed*."""
    if not header:
        return False
    name = header.strip().lower()
    if name.startswith("["):
        name = name[1:name.find("]")] if "]" in name else name
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name in allowed


def make_handler(api: Api, version: str, allowed_hosts: tuple[str, ...], lab_builder_url: str,
                 token: str) -> type[BaseHTTPRequestHandler]:
    index = page(version, token)
    policy = csp(lab_builder_url)

    class Handler(BaseHTTPRequestHandler):
        server_version = "rodeo-builder"
        sys_version = ""

        def _send(self, status: int, data: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", policy)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status: int, obj: dict[str, Any]) -> None:
            self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if host_allowed(self.headers.get("Host"), allowed_hosts):
                return True
            self._json(403, {"error": "host not allowed"})
            return False

        def do_GET(self) -> None:  # noqa: N802 - http.server's method name
            if not self._host_ok():
                return
            url = urlparse(self.path)
            if url.path == "/api":
                action = (parse_qs(url.query).get("action") or [""])[0]
                status, obj = api.dispatch(action, "GET", parse_qs(url.query))
                self._json(status, obj)
                return
            if url.path in ("/", "/index.html"):
                self._send(200, index, MIME[".html"])
                return
            full = (HTDOCS / url.path.lstrip("/")).resolve()
            if HTDOCS not in full.parents or not full.is_file():
                self._json(404, {"error": "not found"})
                return
            self._send(200, full.read_bytes(), MIME.get(full.suffix, "application/octet-stream"))

        def do_POST(self) -> None:  # noqa: N802 - http.server's method name
            if not self._host_ok():
                return
            url = urlparse(self.path)
            if url.path != "/api":
                self._json(404, {"error": "not found"})
                return
            if not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), token):
                self._json(403, {"error": "missing or wrong {} header".format(TOKEN_HEADER)})
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                self._json(415, {"error": "send application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._json(413, {"error": "request body too large"})
                return
            action = (parse_qs(url.query).get("action") or [""])[0]
            status, obj = api.dispatch(action, "POST", parse_qs(url.query), self.rfile.read(length))
            self._json(status, obj)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def make_server(api: Api, host: str = "127.0.0.1", port: int = 8678, version: str = "",
                allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS, lab_builder_url: str = "",
                token: str | None = None) -> ThreadingHTTPServer:
    """A ready (not yet serving) builder server; ``server_address`` has the bound port."""
    handler = make_handler(api, version, allowed_hosts, lab_builder_url or api.lab_builder_url,
                           token or secrets.token_urlsafe(32))
    cls = _Server6 if ":" in host else ThreadingHTTPServer
    server = cls((host, port), handler)
    server.daemon_threads = True
    return server


class _Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6
