"""The player web app for one lab: assignment on the left, tabs on the right.

Standard library only, like the claim portal. It binds loopback; Caddy is the
only public listener. Moves (enter, check, next, skip) can run for minutes, so
they run on one background worker and the page polls ``/api/state``.

What the student sees from scripts: the check output only.
Setup, solve and cleanup results are reported as exit codes, never output,
because those scripts print passwords.

Config is one JSON file (see :func:`load_config`).
"""
from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .auth import COOKIE, SESSION_SECONDS, Auth
from .auth import load as load_auth
from .outline import chapter_name, outline
from .runner import CHECK_TIMEOUT, MoveResult, Runner, RunnerError, ScriptResult, Tab, check_timeout_ok

STATIC_DIR = Path(__file__).resolve().parents[1] / "data" / "player" / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CSRF_HEADER = "X-Rodeo-Player"
_TAG = re.compile(r"<[^>]+>")
LOCKED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Lab locked</title>
<style>:root{--bg:#eef2f0;--card:#fff;--ink:#0c322c;--ink2:#4b635c;--go:#30ba78;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#061815;--card:#0b2420;--ink:#e3efea;--ink2:#9ab5ac}}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;box-sizing:border-box;
background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:30rem;background:var(--card);border-radius:12px;padding:28px 32px;border-top:4px solid var(--go)}
h1{font-size:22px;margin:0 0 8px}p{margin:0;color:var(--ink2)}</style></head>
<body><main><h1>This lab needs its link</h1><p>Open the lab link you were given, from the workshop portal or
your instructor. It signs this browser in for 24 hours.</p></main></body></html>"""
TOO_MANY_PAGE = (LOCKED_PAGE.replace("<title>Lab locked</title>", "<title>Too many tries</title>")
                 .replace("<h1>This lab needs its link</h1>", "<h1>Too many tries</h1>")
                 .replace("""<p>Open the lab link you were given, from the workshop portal or
your instructor. It signs this browser in for 24 hours.</p>""",
                          """<p>This lab saw too many wrong lab links from your network. Wait a few
minutes, then open the lab link you were given again.</p>"""))


class LoginLimiter:
    """Wrong lab links per source address, in a sliding window.

    The token is 192 random bits, so this is not what keeps a lab safe; it
    stops a bot hammering ``/enter`` from loading the lab host. Each lab is
    one student's host, so a shared event network only counts that
    student's own mistakes. A good link clears the address.
    """

    def __init__(self, limit: int = 20, window: float = 600.0, max_sources: int = 10_000) -> None:
        self.limit, self.window, self.max_sources = limit, window, max_sources
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, source: str, now: float) -> list[float]:
        kept = [t for t in self._fails.get(source, []) if now - t < self.window]
        if kept:
            self._fails[source] = kept
        else:
            self._fails.pop(source, None)
        return kept

    def blocked(self, source: str, now: float | None = None) -> bool:
        with self._lock:
            return len(self._recent(source, time.time() if now is None else now)) >= self.limit

    def failed(self, source: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            recent = self._recent(source, now)
            if not recent and len(self._fails) >= self.max_sources:
                # Memory stays bounded: drop expired sources, then the stalest.
                for s in list(self._fails):
                    self._recent(s, now)
                if len(self._fails) >= self.max_sources:
                    self._fails.pop(min(self._fails, key=lambda s: self._fails[s][-1]))
            self._fails[source] = [*recent, now]

    def cleared(self, source: str) -> None:
        with self._lock:
            self._fails.pop(source, None)



_STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


@dataclass
class PlayerConfig:
    track_dir: str
    state_dir: str
    title: str = "SUSE Virtualization Rodeo"
    listen: str = "127.0.0.1"
    port: int = 8680
    host: str = "kvm-host"
    home: str = "/root"
    kubeconfig: str = "/root/.rodeo/harvester-kubeconfig"
    sandbox_id: str = ""
    # Track tab port -> URL the browser opens for it (the proxy name, never
    # the raw lab address). Keys are strings because JSON keys are.
    tab_urls: dict[str, str] = field(default_factory=dict)
    terminal_url: str = "/terminal/"
    # Access control (rodeo.player.auth). Empty auth_file = no login, for
    # local runs and tests; ``rodeo player setup`` always sets it.
    auth_file: str = ""
    # Public names: {"player": base, "hv": "hv.<base>", "rancher": "rancher.<base>"}.
    hosts: dict[str, str] = field(default_factory=dict)
    # Seconds one check script may run before it is stopped and counted as failed.
    check_timeout: int = CHECK_TIMEOUT
    # Where the host checks that each lab tab answers: tab port (as a string)
    # or "terminal" -> a URL on the lab network or loopback, never the public
    # names (those need the student's cookie). Empty: no lab health shown.
    probes: dict[str, str] = field(default_factory=dict)
    # False for a lab profile without a track: no chapters, the UIs and a terminal.
    track: bool = True
    # Web guide for a lab without a track (the profile's guide_url), or "".
    guide_url: str = ""
    # Root-only JSON [{label, user, password}] written by setup; "" = none.
    credentials_file: str = ""
    # The lab's tabs when there is no track: [{title, type, port}] (service
    # tabs by tab port, plus the terminal). With a track, each chapter's own.
    lab_tabs: list[dict[str, Any]] = field(default_factory=list)


def load_config(path: Path | str) -> PlayerConfig:
    data = json.loads(Path(path).read_text())
    known = set(PlayerConfig.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown player config keys: {sorted(unknown)}")
    cfg = PlayerConfig(**data)
    check_timeout_ok(cfg.check_timeout)
    return cfg


HEALTH_TTL = 5.0      # seconds a health answer is reused, however many pages poll
PROBE_TIMEOUT = 3.0


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when ``url`` answers HTTP below 500. Lab certificates are self-signed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "rodeo-player-health"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:  # noqa: S310 (fixed lab URLs)
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (OSError, ValueError):
        return False


def plain(text: str) -> str:
    """Chapter titles carry rmstory spans; the sidebar wants text."""
    return html.unescape(_TAG.sub("", text)).strip()


class Player:
    """The runner plus one background worker and its last result."""

    def __init__(self, cfg: PlayerConfig, runner: Runner | None = None) -> None:
        self.cfg = cfg
        self.auth: Auth | None = load_auth(cfg.auth_file) if cfg.auth_file else None
        if self.auth and not cfg.hosts.get("player"):
            raise ValueError("auth_file needs hosts.player in the player config")
        self.runner = runner or Runner(
            cfg.track_dir, cfg.state_dir, host=cfg.host, sandbox_id=cfg.sandbox_id,
            home=cfg.home, kubeconfig=cfg.kubeconfig, check_timeout=cfg.check_timeout,
            allow_empty=not cfg.track,
        )
        self.logins = LoginLimiter()
        self._lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._health: dict[str, Any] | None = None
        self._health_at = 0.0
        self._busy: str | None = None
        self._last: dict[str, Any] | None = None
        self._started: float | None = None
        # The runner's position, copied under the lock when no move is
        # running. The worker changes the runner mid-skip; pages read this
        # copy, so they never see a half-updated chapter index.
        self._pos = self._position()

    def _position(self) -> dict[str, Any]:
        r = self.runner
        return {"current": r.current, "entered": r.entered, "completed": r.completed,
                "skipped": r.skipped}

    # -- moves --------------------------------------------------------------

    def start(self, action: str, target: int | None = None) -> bool:
        """Start a move on the worker; False when one is already running."""
        with self._lock:
            if self._busy:
                return False
            self._busy = action
            self._started = time.time()
        threading.Thread(target=self._run, args=(action, target), daemon=True).start()
        return True

    def _run(self, action: str, target: int | None) -> None:
        try:
            if action == "enter":
                res = self.runner.enter()
            elif action == "check":
                res = self.runner.check()
            elif action == "next":
                res = self.runner.next()
            elif action == "skip":
                res = self.runner.skip(int(target))  # type: ignore[arg-type]
            else:
                raise RunnerError(f"unknown action {action}")
            last = self._summary(action, res)
        except (RunnerError, ValueError, IndexError) as exc:
            last = {"action": action, "ok": False, "message": str(exc), "scripts": []}
        except Exception as exc:  # keep the worker alive; the page shows it
            last = {"action": action, "ok": False, "message": f"internal error: {exc.__class__.__name__}",
                    "scripts": []}
        with self._lock:
            self._last = {**last, "finished": time.time()}
            self._pos = self._position()
            self._busy = None
            self._started = None

    @staticmethod
    def _script(r: ScriptResult) -> dict[str, Any]:
        out = {"kind": r.kind, "exit": r.exit_code, "timed_out": r.timed_out,
               "seconds": round(r.duration, 1)}
        if r.kind == "check":
            out["output"] = r.output
        return out

    def _summary(self, action: str, res: MoveResult) -> dict[str, Any]:
        return {"action": action, "ok": res.ok, "message": res.message,
                "scripts": [self._script(r) for r in res.results]}

    # -- views --------------------------------------------------------------

    def readable(self) -> int:
        """Highest chapter index the student may read (no reading ahead)."""
        with self._lock:
            pos = self._pos
        return max([pos["current"], *pos["completed"]])

    def _tab_list(self, index: int) -> list[Tab]:
        if self.runner.chapters:
            return list(self.runner.chapter(index).tabs)
        return [Tab(title=str(t.get("title") or ""), type=str(t.get("type") or "service"),
                    port=t.get("port")) for t in self.cfg.lab_tabs]

    def tabs(self, index: int) -> list[dict[str, Any]]:
        out = []
        for i, t in enumerate(self._tab_list(index)):
            if t.type == "terminal":
                url = self.cfg.terminal_url
            elif t.type == "service" and t.port is not None:
                url = self.cfg.tab_urls.get(str(t.port), "")
            else:
                url = ""
            out.append({"index": i, "title": t.title, "type": t.type, "url": url})
        return out

    def health(self) -> dict[str, Any]:
        """Does each tab of the current chapter answer, and is its setup done?

        One probe round per HEALTH_TTL whatever the number of open pages; the
        lock also keeps two rounds from running at once.
        """
        with self._health_lock:
            if self._health is not None and time.monotonic() - self._health_at < HEALTH_TTL:
                return self._health
            with self._lock:
                pos, busy, last = dict(self._pos), self._busy, self._last
            tabs = self._tab_list(pos["current"])
            keys = ["terminal" if t.type == "terminal" else str(t.port) for t in tabs]
            urls = [self.cfg.probes.get(k, "") for k in keys]
            todo = [u for u in urls if u]
            results: dict[str, bool] = {}
            if todo:
                with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                    results = dict(zip(todo, pool.map(probe, todo)))
            items = [{"name": t.title, "ok": results.get(u) if u else None} for t, u in zip(tabs, urls)]
            if pos["entered"] or not self.runner.chapters:
                setup = "done"
            elif busy == "enter":
                setup = "running"
            elif last and last.get("action") == "enter" and not last.get("ok"):
                setup = "failed"
            else:
                setup = "waiting"
            checked = [i["ok"] for i in items if i["ok"] is not None]
            self._health = {"checked": bool(checked), "ready": bool(checked) and all(checked),
                            "items": items, "setup": setup, "chapter": pos["current"]}
            self._health_at = time.monotonic()
            return self._health

    def state(self) -> dict[str, Any]:
        r = self.runner
        with self._lock:
            busy, last, pos, started = self._busy, self._last, dict(self._pos), self._started
        return {
            "title": self.cfg.title,
            "chapters": [self._chapter_summary(c) for c in r.chapters],
            "current": pos["current"],
            "entered": pos["entered"],
            "completed": pos["completed"],
            "skipped": pos["skipped"],
            "readable": max([pos["current"], *pos["completed"]]),
            "busy": busy,
            "busy_seconds": round(time.time() - started) if busy and started else 0,
            "last": last,
            "tabs": self.tabs(pos["current"]),
            "guide_url": self.cfg.guide_url,
            "credentials": self.has_credentials(),
        }

    def credentials(self) -> list[dict[str, str]]:
        """What the student logs in to the lab UIs with (behind the lab login)."""
        if not self.cfg.credentials_file:
            return []
        try:
            data = json.loads(Path(self.cfg.credentials_file).read_text())
        except (OSError, ValueError):
            return []
        return [{k: str(c.get(k, "")) for k in ("label", "user", "password")}
                for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    def has_credentials(self) -> bool:
        return bool(self.credentials())

    def chapter_view(self, index: int) -> dict[str, Any]:
        if not 0 <= index <= self.readable():
            raise IndexError(index)
        ch = self.runner.chapter(index)
        body = self.runner.render(index, urls=self.cfg.tab_urls)
        return {**self._chapter_summary(ch), "body": body, "tabs": self.tabs(index),
                "teaser": plain(ch.teaser), **outline(body)}

    @staticmethod
    def _chapter_summary(ch: Any) -> dict[str, Any]:
        number, name = chapter_name(ch.title)
        return {"index": ch.index, "title": plain(ch.title), "check": ch.has("check"),
                "number": number, "name": name, "difficulty": ch.difficulty}


class Handler(BaseHTTPRequestHandler):
    player: Player  # set on the subclass made by make_server
    server_version = "rodeo-player"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # requests only, no bodies
        pass

    # -- helpers ------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, *, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- access -------------------------------------------------------------

    def _host(self) -> str:
        """The public name asked for (Caddy passes it through)."""
        h = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        return h.split(",")[0].strip().split(":")[0].lower()

    def _source(self) -> str:
        """The student's address. Caddy, the only public listener, sets
        X-Forwarded-For to the client it saw (it trusts no proxy before it),
        so the last entry is that client."""
        xff = self.headers.get("X-Forwarded-For") or ""
        return xff.split(",")[-1].strip() or self.client_address[0]

    def _cookie(self) -> str:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except CookieError:
            return ""
        return jar[COOKIE].value if COOKIE in jar else ""

    def _signed_in(self, host: str) -> bool:
        auth = self.player.auth
        return auth is None or auth.cookie_ok(host, self._cookie())

    def _redirect(self, location: str, cookie_for: str | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if cookie_for and self.player.auth:
            self.send_header("Set-Cookie", f"{COOKIE}={self.player.auth.cookie(cookie_for)}; "
                             f"Max-Age={SESSION_SECONDS}; Path=/; Secure; HttpOnly; SameSite=Lax")
        self.send_header("Cache-Control", "no-store")
        # The hop puts a 60 s ticket in the query string: never leak it.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _locked(self) -> None:
        self._send(401, LOCKED_PAGE.encode(), "text/html; charset=utf-8")

    @staticmethod
    def _safe_return(r: str) -> str:
        """A same-host path to go back to after a hop, never another site."""
        return r if r.startswith("/") and not r.startswith("//") and "\\" not in r else "/"

    def _ui_label(self, host: str) -> str | None:
        hosts = self.player.cfg.hosts
        return next((k for k, name in hosts.items() if k != "player" and name == host), None)

    def _access(self, path: str, query: dict[str, list[str]]) -> bool:
        """Handle the login routes. True when the request was answered here."""
        auth, hosts = self.player.auth, self.player.cfg.hosts
        if auth is None:
            return False
        host = self._host()
        player = hosts["player"]
        if path == "/enter" and host == player:
            source, logins = self._source(), self.player.logins
            if logins.blocked(source):
                self._send(429, TOO_MANY_PAGE.encode(), "text/html; charset=utf-8")
            elif auth.token_ok((query.get("t") or [""])[0]):
                logins.cleared(source)
                self._redirect("/", cookie_for=player)
            else:
                logins.failed(source)
                self._locked()
            return True
        if path == "/_auth":  # Caddy's forward_auth for every request, every name
            if auth.cookie_ok(host, self._cookie()):
                self._send(200, b"ok", "text/plain")
            elif self._ui_label(host):
                ret = urllib.parse.quote(self._safe_return(self.headers.get("X-Forwarded-Uri") or "/"), safe="")
                self._redirect(f"https://{player}/auth/hop?to={self._ui_label(host)}&r={ret}")
            else:
                self._locked()
            return True
        if path == "/auth/hop" and host == player:
            to = (query.get("to") or [""])[0]
            target = hosts.get(to) if to and to != "player" else None
            if not target or not auth.cookie_ok(player, self._cookie()):
                self._locked()
                return True
            ret = urllib.parse.quote(self._safe_return((query.get("r") or ["/"])[0]), safe="")
            self._redirect(f"https://{target}/.rodeo/auth?ticket={auth.ticket(target)}&r={ret}")
            return True
        if path == "/.rodeo/auth" and self._ui_label(host):
            if auth.ticket_ok(host, (query.get("ticket") or [""])[0]):
                self._redirect(self._safe_return((query.get("r") or ["/"])[0]), cookie_for=host)
            else:
                self._locked()
            return True
        # Defence in depth: the player's own routes need the cookie even if a
        # request reached it without going through Caddy's forward_auth.
        if path != "/healthz" and not auth.cookie_ok(player, self._cookie()):
            self._locked()
            return True
        return False

    # -- GET ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path, _, qs = self.path.partition("?")
        if self._access(path, urllib.parse.parse_qs(qs)):
            return
        if path == "/":
            self._send(200, page(self.player.cfg.title).encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(200, self.player.state())
        elif path == "/api/health":
            self._json(200, self.player.health())
        elif path == "/api/credentials":
            self._json(200, {"credentials": self.player.credentials()})
        elif path.startswith("/api/chapter/"):
            try:
                self._json(200, self.player.chapter_view(int(path.rsplit("/", 1)[1])))
            except (ValueError, IndexError):
                self._error(404, "chapter not available")
        elif path.startswith("/static/"):
            name = path[len("/static/"):]
            f = STATIC_DIR / name
            if "/" in name or name.startswith(".") or not f.is_file():
                self._error(404, "not found")
                return
            self._send(200, f.read_bytes(), _STATIC_TYPES.get(f.suffix, "application/octet-stream"),
                       cache=True)
        elif path.startswith("/assets/"):
            self._asset(path[len("/assets/"):])
        elif path == "/healthz":
            self._send(200, b"ok\n", "text/plain")
        else:
            self._error(404, "not found")

    def _asset(self, rel: str) -> None:
        """Track images: the assignments say ``../assets/x``, which is ``/assets/x`` here.

        Only files under the track's ``assets/`` directory, only image types.
        """
        root = (Path(self.player.cfg.track_dir) / "assets").resolve()
        target = (root / urllib.parse.unquote(rel)).resolve()
        ctype = mimetypes.guess_type(target.name)[0] or ""
        if root not in target.parents or not target.is_file() or not ctype.startswith("image/"):
            self._error(404, "not found")
            return
        self._send(200, target.read_bytes(), ctype, cache=True)

    # -- POST ---------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        # A custom header forces a CORS preflight, which this server never
        # answers, so another site cannot make a student's browser press Check.
        if self.headers.get(CSRF_HEADER) != "1":
            self._error(403, "missing player header")
            return
        if self.player.auth and not self._signed_in(self.player.cfg.hosts["player"]):
            self._error(401, "open the lab link first")
            return
        action = self.path.split("?", 1)[0].removeprefix("/api/")
        # No "back": past chapters are read with GET /api/chapter/<n>, which
        # never moves the runner, so a crafted POST cannot rewind a solved lab.
        if action not in {"enter", "check", "next", "skip"}:
            self._error(404, "not found")
            return
        if not self.player.runner.chapters:
            self._error(HTTPStatus.CONFLICT, "this lab has no chapters")
            return
        target = None
        if action == "skip":
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1024)
                target = int(json.loads(self.rfile.read(length) or b"{}")["target"])
            except (ValueError, KeyError, TypeError):
                self._error(400, "target chapter missing")
                return
        if not self.player.start(action, target):
            self._error(HTTPStatus.CONFLICT, "a step is already running")
            return
        self._json(202, {"started": action})


def make_server(player: Player, *, allow_public: bool = False) -> ThreadingHTTPServer:
    """Bind the player. Refuses anything but loopback unless a test says so."""
    if player.cfg.listen not in LOOPBACK and not allow_public:
        raise ValueError(f"the player binds loopback only, not {player.cfg.listen!r}; Caddy is the public listener")
    handler = type("BoundHandler", (Handler,), {"player": player})
    server = ThreadingHTTPServer((player.cfg.listen, player.cfg.port), handler)
    server.daemon_threads = True
    return server


def serve(cfg: PlayerConfig) -> None:
    server = make_server(Player(cfg))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def page(title: str) -> str:
    # The script is served with a long cache; its content hash in the URL makes
    # a browser fetch the new one after an upgrade.
    version = hashlib.sha256((STATIC_DIR / "player.js").read_bytes()).hexdigest()[:12]
    return ((STATIC_DIR / "player.html").read_text()
            .replace("{{TITLE}}", html.escape(title)).replace("{{ASSET_V}}", version))
