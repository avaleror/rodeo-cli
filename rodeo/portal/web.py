"""HTTP front end: stdlib ThreadingHTTPServer on 127.0.0.1 behind Caddy (TLS).

Routes: ``/`` claim form, ``POST /claim``, ``/l/<token>`` lab card, ``/l/<token>/key``
student SSH key, ``/admin/<token>`` read-only instructor view, ``/healthz``.
Access logs carry method, route template and status only: never tokens, emails,
PINs or passwords.
"""
from __future__ import annotations

import hmac
import html
import json
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from . import claims
from .db import connect

MAX_BODY = 4096
RATE_LIMIT = 10  # POST /claim and bad links, per client IP per window
RATE_WINDOW = 60.0

_CSS = """
:root{--bg:#f4f6f5;--card:#fff;--ink:#14201a;--mute:#56675e;--acc:#0c7a4f;--acc-ink:#fff;--line:#dce4df;--err:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#0e1411;--card:#16201b;--ink:#e5eee9;--mute:#9bb0a5;--acc:#30ba78;--acc-ink:#07130d;--line:#28362f;--err:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:720px;margin:0 auto;padding:40px 16px}main.wide{max-width:1000px}
h1{font-size:1.7rem;margin:0 0 6px;letter-spacing:-.01em}.sub{color:var(--mute);margin:0 0 28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:16px}
.card h2{margin:0 0 14px;font-size:1.1rem}label{display:block;font-weight:600;margin:14px 0 6px}
input{width:100%;font:inherit;padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink)}
.hint{color:var(--mute);font-size:.88rem;margin-top:4px}
.btn{display:inline-block;margin-top:20px;font:inherit;font-weight:600;padding:11px 20px;border:0;border-radius:9px;background:var(--acc);color:var(--acc-ink);cursor:pointer;text-decoration:none}
.err{color:var(--err);font-weight:600;margin:0 0 12px}
dl{display:grid;grid-template-columns:100px 1fr;gap:8px 12px;margin:0}dt{color:var(--mute)}dd{margin:0;overflow-wrap:anywhere}
code,pre{font:14px ui-monospace,SFMono-Regular,Menlo,monospace}code{background:var(--bg);padding:2px 6px;border-radius:6px}
pre{background:var(--bg);padding:12px;border-radius:9px;overflow-x:auto;margin:10px 0 0}
a{color:var(--acc)}.cp{font:inherit;font-size:.78rem;margin-left:8px;padding:2px 8px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;cursor:pointer}
.note{color:var(--mute);font-size:.9rem}.pill{display:inline-block;font-size:.8rem;padding:2px 10px;border-radius:99px;background:var(--acc);color:var(--acc-ink);font-weight:600;vertical-align:middle}
.tbl{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.95rem}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600}.st-claimed{color:var(--acc);font-weight:600}.st-building{color:var(--mute)}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}.stat{flex:1;min-width:120px}.stat b{display:block;font-size:1.6rem}
.bar{display:inline-block;width:120px;height:8px;border-radius:99px;background:var(--line);vertical-align:middle;margin-right:8px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--acc)}
.sub-row td{border-bottom:1px solid var(--line);padding-top:0;white-space:normal}tr:has(+ .sub-row) td{border-bottom:0}
.ph-done{color:var(--acc)}.ph-todo{color:var(--mute)}.st-ok{color:var(--acc);font-weight:600}.st-failed,.st-unreachable{color:var(--err);font-weight:600}
"""
_JS = (
    "document.querySelectorAll('.cp').forEach(function(b){b.addEventListener('click',function(){"
    "navigator.clipboard.writeText(b.dataset.v);b.textContent='copied'})});"
)


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str, *, wide: bool = False, refresh: int | None = None) -> bytes:
    meta = f"<meta http-equiv=refresh content={refresh}>" if refresh else ""
    cls = " class=wide" if wide else ""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"{meta}<title>{_e(title)}</title><link rel=stylesheet href=/s.css></head>"
        f"<body><main{cls}>{body}</main><script src=/s.js></script></body></html>"
    ).encode()


def _copyable(value: str) -> str:
    return f"<code>{_e(value)}</code><button class=cp type=button data-v='{_e(value)}'>copy</button>"


def claim_form(title: str, csrf: str, *, mode: str, is_open: bool, err: str = "",
               email: str = "", name: str = "", rows: list[dict[str, Any]] | None = None) -> bytes:
    heading = f"<h1>{_e(title or 'Workshop labs')}</h1>"
    labs = board(rows or [])
    if mode == "roster":
        return _page(title, heading + "<p class=sub>Open the personal link your instructor "
                     "sent you to get your lab.</p>" + labs)
    if not is_open:
        return _page(title, heading + "<p class=sub>Claiming is closed. Ask your instructor.</p>"
                     + labs)
    msg = f"<p class=err role=alert>{_e(err)}</p>" if err else ""
    return _page(title, f"""{heading}
<p class=sub>Claim your personal lab with the event code your instructor shows.</p>
<form class=card method=post action=/claim>{msg}
<input type=hidden name=csrf value='{_e(csrf)}'>
<label for=code>Event code</label><input id=code name=code required maxlength=12 autocomplete=off autocapitalize=characters placeholder="ABCD-2345">
<label for=name>Your name</label><input id=name name=name required maxlength=80 autocomplete=name value='{_e(name)}'>
<label for=email>Email</label><input id=email name=email type=email required maxlength=254 autocomplete=email value='{_e(email)}'>
<label for=pin>4-digit PIN</label><input id=pin name=pin required inputmode=numeric pattern="[0-9]{{4}}" maxlength=4 autocomplete=off>
<div class=hint>Choose any 4 digits. If you lose your lab link, the same email and PIN bring it back.</div>
<button class=btn type=submit>Get my lab</button></form>{labs}""")


def _lab_cards(lab_id: str, data: dict[str, Any], key_href: str, *, ssh_title: str) -> str:
    """Credential cards for one lab (student page and instructor page share them)."""
    cards = ""
    for c in data.get("components") or []:
        cards += (
            f"<div class=card><h2>{_e(c.get('label', ''))}</h2><dl>"
            f"<dt>URL</dt><dd><a href='{_e(c['url'])}' target=_blank rel='noopener noreferrer'>{_e(c['url'])}</a></dd>"
            f"<dt>User</dt><dd>{_copyable(c.get('user', 'admin'))}</dd>"
            f"<dt>Password</dt><dd>{_copyable(c.get('password', ''))}</dd></dl></div>"
        )
    ssh = data.get("ssh")
    if ssh:
        key = f"{lab_id}.key"
        cmd = f"ssh -i {key} {ssh['user']}@{ssh['host']}"
        cards += (
            f"<div class=card><h2>{_e(ssh_title)}</h2><dl>"
            f"<dt>User</dt><dd>{_copyable(ssh['user'])}</dd><dt>Host</dt><dd>{_copyable(ssh['host'])}</dd></dl>"
            f"<p><a class=btn href='{_e(key_href)}' download='{_e(key)}'>Download SSH key</a></p>"
            "<p class=note>Then, in the folder where you saved it:</p>"
            f"<pre>chmod 600 {_e(key)}\n{_e(cmd)}</pre></div>"
        )
    return cards


def board(rows: list[dict[str, Any]]) -> str:
    """Public claim board: lab, state and the claimant's *name* (never email)."""
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{_e(r['lab'])}</td><td class=st-{_e(r['state'])}>{_e(r['state'])}</td>"
        f"<td>{_e(r['name']) if r['state'] == 'claimed' else ''}</td></tr>"
        for r in rows
    )
    free = sum(1 for r in rows if r["state"] == "free")
    return (f"<h2 style='margin:28px 0 10px'>Labs <span class=note>{free} of {len(rows)} free</span></h2>"
            "<div class='card tbl'><table><thead><tr><th>Lab</th><th>State</th>"
            f"<th>Claimed by</th></tr></thead><tbody>{body}</tbody></table></div>")


def lab_page(row: Any, token: str) -> bytes:
    lab_id = row["id"]
    who = row["name"] or row["email"]
    head = (f"<h1>Your lab <span class=pill>{_e(lab_id)}</span></h1>"
            f"<p class=sub>Assigned to {_e(who)}. Bookmark this page: it is your personal "
            "link. Do not share it.</p>")
    if not row["ready"]:
        return _page(f"Lab {lab_id}", head + "<div class=card><h2>Still building</h2>"
                     "<p>Your lab is not ready yet. This page refreshes every minute.</p></div>",
                     refresh=60)
    cards = _lab_cards(lab_id, json.loads(row["data"] or "{}"), f"/l/{token}/key",
                       ssh_title="SSH to your lab host")
    return _page(f"Lab {lab_id}", head + cards + "<p class=note>The lab web UIs use "
                 "self-signed certificates. Accept the browser warning to continue.</p>")


STALE_AFTER = 180  # seconds without a progress push before the page warns


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min" if m else f"{seconds} s"


_STATE_LABEL = {"ok": "ready", "running": "deploying", "failed": "failed",
                "unreachable": "unreachable", "pending": "waiting"}


def progress_section(progress: dict[str, dict[str, Any]], progress_at: str | None,
                     order: list[str], *, now: datetime | None = None) -> str:
    """Deploy progress per lab, as pushed by ``rodeo fleet portal publish --watch``."""
    if not progress:
        return ""
    now = now or datetime.now(timezone.utc)
    pushed = _parse_ts(progress_at)
    age = (now - pushed).total_seconds() if pushed else None
    if age is None or age > STALE_AFTER:
        fresh = (f"<p class=err role=alert>No update for {_dur(age)}: is "
                 "<code>rodeo fleet portal publish --watch</code> still running?</p>"
                 if age is not None else "")
    else:
        fresh = f"<p class=note>Updated {_dur(age)} ago.</p>"
    ids = [i for i in order if i in progress] + sorted(set(progress) - set(order))
    body = ""
    for lab_id in ids:
        p = progress[lab_id]
        done, total = int(p.get("done") or 0), int(p.get("total") or 0)
        pct = int(100 * done / total) if total else 0
        state = str(p.get("state") or "pending")
        start = _parse_ts(p.get("started_at"))
        end = _parse_ts(p.get("finished_at")) if state == "ok" else None
        elapsed = _dur(((end or now) - start).total_seconds()) if start else ""
        phases = " &middot; ".join(
            f"<span class={'ph-done' if ph.get('done') else 'ph-todo'}>{_e(ph.get('name', ''))}</span>"
            for ph in p.get("phases") or []
        )
        err = f"<div class=err>{_e(str(p['error'])[:300])}</div>" if p.get("error") else ""
        current = "" if state == "ok" else _e(p.get("current") or "")
        body += (
            f"<tr><td><a href='#lab-{_e(lab_id)}'>{_e(lab_id)}</a></td>"
            f"<td><div class=bar><span style='width:{pct}%'></span></div>"
            f"<span class=note>{done}/{total}</span></td>"
            f"<td>{current}</td><td>{elapsed}</td>"
            f"<td class=st-{_e(state)}>{_e(_STATE_LABEL.get(state, state))}</td></tr>"
            f"<tr class=sub-row><td></td><td colspan=4><span class=note>{phases}</span>{err}</td></tr>"
        )
    return ("<h2 style='margin:28px 0 10px'>Deployment</h2>" + fresh +
            "<div class='card tbl'><table><thead><tr><th>Lab</th><th>Progress</th>"
            "<th>Current phase</th><th>Elapsed</th><th>State</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def admin_page(s: dict[str, Any], rows: list[dict[str, Any]], labs: dict[str, dict[str, Any]],
               token: str, *, progress: dict[str, dict[str, Any]] | None = None,
               progress_at: str | None = None) -> bytes:
    """Instructor-only (secret link): who has which lab plus every lab's credentials."""
    n = len(rows)
    claimed = sum(1 for r in rows if r["state"] == "claimed")
    free = sum(1 for r in rows if r["state"] == "free")
    stats = "".join(
        f"<div class='card stat'><span class=note>{label}</span><b>{value}</b></div>"
        for label, value in (("Labs", n), ("Claimed", claimed), ("Free", free),
                             ("Event code", _e(s["code"]) if s["mode"] != "roster" else "n/a"),
                             ("Claiming", "open" if s["open"] else "closed"))
    )
    body = "".join(
        f"<tr><td><a href='#lab-{_e(r['lab'])}'>{_e(r['lab'])}</a></td>"
        f"<td class=st-{_e(r['state'])}>{_e(r['state'])}</td>"
        f"<td>{_e(r['name'])}</td><td>{_e(r['email'])}</td><td>{_e(r['source'])}</td>"
        f"<td>{_e(r['claimed_at'])}</td><td>{_e(r['opened_at'])}</td></tr>"
        for r in rows
    )
    details = ""
    for r in rows:
        data = labs.get(r["lab"]) or {}
        who = f"{_e(r['name'])} &lt;{_e(r['email'])}&gt;" if r["email"] else "not claimed"
        host = (data.get("ssh") or {}).get("host") or data.get("host") or ""
        inner = (_lab_cards(r["lab"], data, f"/admin/{token}/key/{r['lab']}",
                            ssh_title="SSH as the student user")
                 if data.get("components") or data.get("ssh")
                 else "<p class=note>Not published yet (still building).</p>")
        details += (
            f"<details class=card id='lab-{_e(r['lab'])}'><summary><b>{_e(r['lab'])}</b> "
            f"<span class=st-{_e(r['state'])}>{_e(r['state'])}</span> "
            f"<span class=note>{who}{' &middot; ' + _e(host) if host else ''}</span></summary>"
            f"<div style='margin-top:14px'>{inner}</div></details>"
        )
    return _page(
        "Instructor view",
        f"<h1>{_e(s['title'] or 'Workshop')}: instructor view</h1><p class=sub>Instructors "
        "only: this page shows every lab's credentials. Do not project it. Changes go through "
        "<code>rodeo fleet portal</code>; <code>rodeo fleet portal admin-link</code> revokes "
        "this link.</p>"
        f"<div class=stats>{stats}</div><div class='card tbl'><table><thead><tr><th>Lab</th>"
        "<th>State</th><th>Name</th><th>Email</th><th>Via</th><th>Claimed (UTC)</th>"
        f"<th>First opened</th></tr></thead><tbody>{body}</tbody></table></div>"
        + progress_section(progress or {}, progress_at, [r["lab"] for r in rows])
        + f"<h2 style='margin:28px 0 10px'>Lab details</h2>{details}",
        wide=True,
        # Follow the deploy live; stop refreshing once every lab is ready so open
        # detail panels (passwords) do not collapse under the instructor.
        refresh=30 if any(r["state"] == "building" for r in rows) else None,
    )


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record one attempt; True when ``key`` is over the limit."""
        t = time.monotonic()
        with self._lock:
            recent = [x for x in self._hits.get(key, []) if t - x < self.window]
            recent.append(t)
            self._hits[key] = recent
            if len(self._hits) > 10000:  # bound memory under a scan
                self._hits = {k: v for k, v in self._hits.items() if t - v[-1] < self.window}
            return len(recent) > self.limit


def _route(path: str) -> str:
    path = re.sub(r"^/l/[^/]+", "/l/<token>", path)
    return re.sub(r"^/admin/[^/]+", "/admin/<token>", path)


class Handler(BaseHTTPRequestHandler):
    server_version = "rodeo-portal"
    sys_version = ""
    db_path: str | None = None
    limiter = RateLimiter()
    secure_cookie = True

    def log_message(self, fmt: str, *args: Any) -> None:
        status = args[1] if len(args) > 1 else "-"
        sys.stderr.write(f"{self.command} {_route(self.path.split('?')[0])} {status}\n")

    def _ip(self) -> str:
        # Caddy appends the real client address as the last X-Forwarded-For hop.
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[-1].strip() if fwd else self.client_address[0]

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        for k, v in (
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy",
             "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"),
            *(extra or []),
        ):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _csrf(self) -> str:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["csrf"].value if "csrf" in c else ""

    def _form(self, code: int = 200, **kw: Any) -> None:
        tok = self._csrf() or secrets.token_urlsafe(16)
        secure = "; Secure" if self.secure_cookie else ""
        con = connect(self.db_path)
        try:
            s = claims.settings(con)
            rows = claims.status_rows(con)
        finally:
            con.close()
        self._send(code, claim_form(s["title"], tok, mode=s["mode"], is_open=s["open"],
                                    rows=rows, **kw),
                   extra=[("Set-Cookie", f"csrf={tok}; Path=/; HttpOnly; SameSite=Strict{secure}")])

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            return self._form()
        if path == "/s.css":
            return self._send(200, _CSS.encode(), "text/css; charset=utf-8")
        if path == "/s.js":
            return self._send(200, _JS.encode(), "application/javascript")
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        m = re.fullmatch(r"/l/([^/]+)(/key)?", path)
        if m:
            con = connect(self.db_path)
            try:
                row = claims.lab_for_token(con, m.group(1))
            finally:
                con.close()
            if row is None:
                if self.limiter.hit(self._ip()):
                    return self._send(429, _page("Slow down", "<h1>Too many attempts</h1>"))
                return self._send(404, _page("Link not valid", "<h1>Link not valid</h1><p class=sub>"
                                             "This link was released or never existed. "
                                             "<a href=/>Claim a lab</a>.</p>"))
            if m.group(2):
                key = (json.loads(row["data"] or "{}").get("ssh") or {}).get("private_key")
                if not key or not row["ready"]:
                    return self._send(404, b"no key", "text/plain")
                return self._send(200, key.encode(), "application/octet-stream", extra=[
                    ("Content-Disposition", f'attachment; filename="{row["id"]}.key"')])
            return self._send(200, lab_page(row, m.group(1)))
        m = re.fullmatch(r"/admin/([^/]+)(?:/key/([A-Za-z0-9_.-]+))?", path)
        if m:
            con = connect(self.db_path)
            try:
                if not claims.is_admin_token(con, m.group(1)):
                    if self.limiter.hit(self._ip()):
                        return self._send(429, _page("Slow down", "<h1>Too many attempts</h1>"))
                    return self._send(404, _page("Not found", "<h1>Not found</h1>"))
                labs = claims.lab_data(con)
                if m.group(2):
                    key = (labs.get(m.group(2), {}).get("ssh") or {}).get("private_key")
                    if not key:
                        return self._send(404, b"no key", "text/plain")
                    return self._send(200, key.encode(), "application/octet-stream", extra=[
                        ("Content-Disposition", f'attachment; filename="{m.group(2)}.key"')])
                prog, prog_at = claims.progress(con)
                return self._send(200, admin_page(claims.settings(con), claims.status_rows(con),
                                                  labs, m.group(1), progress=prog,
                                                  progress_at=prog_at))
            finally:
                con.close()
        self._send(404, _page("Not found", "<h1>Not found</h1>"))

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/claim":
            return self._send(404, b"", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = MAX_BODY + 1
        if n > MAX_BODY or n < 0:
            return self._send(413, b"too large", "text/plain")
        raw = self.rfile.read(n).decode(errors="replace")
        f = {k: v[0] for k, v in parse_qs(raw).items()}
        email = f.get("email", "").strip()[:254]
        name = f.get("name", "").strip()[:80]
        cookie = self._csrf()
        if not cookie or not hmac.compare_digest(cookie, f.get("csrf", "")):
            return self._form(400, err="Your session expired. Please submit again.",
                              email=email, name=name)
        if self.limiter.hit(self._ip()):
            return self._form(429, err="Too many attempts. Wait a minute and try again.",
                              email=email, name=name)
        con = connect(self.db_path)
        try:
            token, _ = claims.claim_open(con, code=f.get("code", ""), email=email,
                                         name=name, pin=f.get("pin", ""))
        except claims.ClaimError as exc:
            return self._form(403, err=str(exc), email=email, name=name)
        finally:
            con.close()
        self.send_response(303)
        for k, v in (("Location", f"/l/{token}"), ("Cache-Control", "no-store"),
                     ("Referrer-Policy", "no-referrer"), ("Content-Length", "0")):
            self.send_header(k, v)
        self.end_headers()


def make_server(host: str, port: int, *, db_path: str | None = None,
                secure_cookie: bool = True) -> ThreadingHTTPServer:
    handler = type("PortalHandler", (Handler,), {
        "db_path": db_path, "limiter": RateLimiter(), "secure_cookie": secure_cookie,
    })
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv
