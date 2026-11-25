"""The player web app for one lab: assignment on the left, tabs on the right.

Standard library only, like the claim portal. It binds loopback; Caddy is the
only public listener. Moves (enter, check, next, skip) can run for minutes, so
they run on one background worker and the page polls ``/api/state``.

What the student sees from scripts: the check output only, as on Instruqt.
Setup, solve and cleanup results are reported as exit codes, never output,
because those scripts print passwords.

Config is one JSON file (see :func:`load_config`).
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .runner import MoveResult, Runner, RunnerError, ScriptResult

STATIC_DIR = Path(__file__).resolve().parents[1] / "data" / "player" / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CSRF_HEADER = "X-Rodeo-Player"
_TAG = re.compile(r"<[^>]+>")
_STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


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


def load_config(path: Path | str) -> PlayerConfig:
    data = json.loads(Path(path).read_text())
    known = set(PlayerConfig.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown player config keys: {sorted(unknown)}")
    return PlayerConfig(**data)


def plain(text: str) -> str:
    """Chapter titles carry rmstory spans; the sidebar wants text."""
    return html.unescape(_TAG.sub("", text)).strip()


class Player:
    """The runner plus one background worker and its last result."""

    def __init__(self, cfg: PlayerConfig, runner: Runner | None = None) -> None:
        self.cfg = cfg
        self.runner = runner or Runner(
            cfg.track_dir, cfg.state_dir, host=cfg.host, sandbox_id=cfg.sandbox_id,
            home=cfg.home, kubeconfig=cfg.kubeconfig,
        )
        self._lock = threading.Lock()
        self._busy: str | None = None
        self._last: dict[str, Any] | None = None

    # -- moves --------------------------------------------------------------

    def start(self, action: str, target: int | None = None) -> bool:
        """Start a move on the worker; False when one is already running."""
        with self._lock:
            if self._busy:
                return False
            self._busy = action
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
            elif action == "back":
                res = self.runner.back(int(target))  # type: ignore[arg-type]
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
            self._busy = None

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
        r = self.runner
        return max([r.current, *r.completed])

    def tabs(self, index: int) -> list[dict[str, Any]]:
        out = []
        for i, t in enumerate(self.runner.chapter(index).tabs):
            if t.type == "terminal":
                url = self.cfg.terminal_url
            elif t.type == "service" and t.port is not None:
                url = self.cfg.tab_urls.get(str(t.port), "")
            else:
                url = ""
            out.append({"index": i, "title": t.title, "type": t.type, "url": url})
        return out

    def state(self) -> dict[str, Any]:
        r = self.runner
        with self._lock:
            busy, last = self._busy, self._last
        return {
            "title": self.cfg.title,
            "chapters": [{"index": c.index, "title": plain(c.title), "check": c.has("check")}
                         for c in r.chapters],
            "current": r.current,
            "entered": r.entered,
            "completed": r.completed,
            "readable": self.readable(),
            "busy": busy,
            "last": last,
            "tabs": self.tabs(r.current),
        }

    def chapter_view(self, index: int) -> dict[str, Any]:
        if not 0 <= index <= self.readable():
            raise IndexError(index)
        ch = self.runner.chapter(index)
        return {"index": index, "title": plain(ch.title), "body": self.runner.render(index),
                "tabs": self.tabs(index)}


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

    # -- GET ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, page(self.player.cfg.title).encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(200, self.player.state())
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
        elif path == "/healthz":
            self._send(200, b"ok\n", "text/plain")
        else:
            self._error(404, "not found")

    # -- POST ---------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        # A custom header forces a CORS preflight, which this server never
        # answers, so another site cannot make a student's browser press Check.
        if self.headers.get(CSRF_HEADER) != "1":
            self._error(403, "missing player header")
            return
        action = self.path.split("?", 1)[0].removeprefix("/api/")
        if action not in {"enter", "check", "next", "skip", "back"}:
            self._error(404, "not found")
            return
        target = None
        if action in {"skip", "back"}:
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
    return (STATIC_DIR / "player.html").read_text().replace("{{TITLE}}", html.escape(title))
