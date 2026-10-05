"""HTTP front end: stdlib ThreadingHTTPServer on 127.0.0.1 behind Caddy (TLS).

Routes: ``/`` workshop-code gate, then claim form + lab board; ``POST /enter`` (code),
``POST /claim``, ``/l/<token>`` lab card, ``/l/<token>/key`` student SSH key,
``/admin/<token>`` read-only instructor view, ``/healthz``.
Pages never use inline ``style=``: the CSP (``default-src 'self'``) would block it.
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
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import claims
from .db import connect

MAX_BODY = 4096
# Only *failed* attempts count (wrong code or PIN, unknown link, bad admin token):
# a whole classroom behind one NAT address claims freely.
RATE_LIMIT = 30
RATE_WINDOW = 600.0
# Wrong workshop codes from *all* addresses together, so many addresses can't walk
# the code space (Cursor review on #53). Past it, only addresses that already sent
# CODE_FAILS_WHILE_CAPPED wrong codes wait; fresh ones (real students) still get in,
# so nobody can close the gate for the class by flooding it (Cursor review on #77).
CODE_FAIL_LIMIT_ALL = 200
CODE_FAILS_WHILE_CAPPED = 3
ACCESS_MAX_AGE = 24 * 3600
# "Remember this device": the student's personal link token in an HttpOnly cookie, so
# reopening the portal offers "Continue to your lab" with nothing to type.
LAB_COOKIE_MAX_AGE = 3 * 24 * 3600

# SUSE branding, matching SUSE-Technical-Marketing/suse-virt-storylane: SUSE green on
# white, Source Sans Pro headings, Open Sans body. Fonts and logo are served by the
# portal itself (static/): the CSP allows only 'self', and third-party font hosts
# would learn every student's IP address.
_CSS = """
@font-face{font-family:'Source Sans Pro';font-style:normal;font-weight:400;font-display:swap;src:url(/static/source-sans-pro-400.woff2) format('woff2')}
@font-face{font-family:'Source Sans Pro';font-style:normal;font-weight:600;font-display:swap;src:url(/static/source-sans-pro-600.woff2) format('woff2')}
@font-face{font-family:'Source Sans Pro';font-style:normal;font-weight:700;font-display:swap;src:url(/static/source-sans-pro-700.woff2) format('woff2')}
@font-face{font-family:'Open Sans';font-style:normal;font-weight:300 800;font-display:swap;src:url(/static/open-sans.woff2) format('woff2')}
:root{--bg:#FFFFFF;--surface:#F7F8FA;--surface-hi:#EEF0F3;--green:#00A651;--green-ink:#00783C;--green-alt:#73BA25;
--green-dim:rgba(0,166,81,.08);--green-border:rgba(0,166,81,.22);--text:#1B1C1E;--gray:#6B7280;--border:#E4E6EA;--err:#C0392B;
--font-head:'Source Sans Pro',system-ui,sans-serif;--font-body:'Open Sans',system-ui,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 var(--font-body);-webkit-font-smoothing:antialiased}
a{color:var(--green-ink);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:760px;margin:0 auto;padding:0 20px}.wide .wrap{max-width:1100px}
nav{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);box-shadow:0 1px 4px rgba(0,0,0,.06)}
.nav-inner{height:64px;display:flex;align-items:center;justify-content:space-between;gap:16px}
.logo{display:flex;align-items:center;gap:12px;min-width:0}.logo:hover{text-decoration:none}
.logo-img{height:26px;width:auto;display:block}.logo-divider{width:1px;height:22px;background:var(--border);flex-shrink:0}
.logo-product{color:var(--text);font:600 15px var(--font-head);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.logo-img,.logo-divider{flex-shrink:0}
.nav-tag{font:700 11px var(--font-head);letter-spacing:.1em;text-transform:uppercase;color:var(--gray);white-space:nowrap}
.hero{padding:48px 0 36px;border-bottom:1px solid var(--border);background:linear-gradient(170deg,#EBF8F1 0%,#FFFFFF 55%)}
.badge{display:inline-block;background:var(--green-dim);border:1px solid var(--green-border);color:var(--green-ink);font:700 12px var(--font-head);letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:999px;margin-bottom:16px}
h1{font:700 clamp(28px,4vw,40px)/1.15 var(--font-head);letter-spacing:-.01em;margin:0 0 10px}
.hero-sub{color:var(--gray);font-size:17px;margin:0;max-width:620px}
main{padding:36px 0 56px}
h2{font:700 19px var(--font-head);margin:0 0 14px}.h2x{margin:36px 0 14px;font-size:22px}
.card{background:var(--surface);border:1px solid var(--border);border-top:3px solid var(--green);border-radius:6px;padding:24px;margin-bottom:18px}
label{display:block;font:600 15px var(--font-head);margin:16px 0 6px}label:first-of-type{margin-top:0}
input{width:100%;font:inherit;padding:11px 12px;border:1px solid var(--border);border-radius:4px;background:#fff;color:var(--text)}
input:focus{outline:2px solid var(--green-border);border-color:var(--green)}
.hint{color:var(--gray);font-size:13px;margin-top:6px}
.btn{display:inline-flex;align-items:center;gap:8px;margin-top:22px;padding:11px 26px;border:0;border-radius:4px;background:var(--green-ink);color:#fff;font:600 15px var(--font-head);cursor:pointer;text-decoration:none;transition:background .15s}
.btn:hover{background:#006532;text-decoration:none}
.err{color:var(--err);font-weight:600;margin:0 0 14px}
dl{display:grid;grid-template-columns:110px 1fr;gap:10px 14px;margin:0}dt{color:var(--gray);font:600 14px var(--font-head)}dd{margin:0;overflow-wrap:anywhere}
code,pre{font:14px var(--mono)}code{background:#fff;border:1px solid var(--border);padding:2px 6px;border-radius:4px}
pre{background:#fff;border:1px solid var(--border);padding:12px;border-radius:4px;overflow-x:auto;margin:10px 0 0}
.cp{font:600 12px var(--font-head);margin-left:8px;padding:3px 10px;border:1px solid var(--green-border);background:var(--green-dim);color:var(--green-ink);border-radius:999px;cursor:pointer}
.note{color:var(--gray);font-size:14px}
.pill{display:inline-block;font:700 15px var(--font-head);padding:3px 12px;border-radius:999px;background:var(--green-ink);color:#fff;vertical-align:middle}
.tbl{overflow-x:auto;padding:8px 24px}table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;padding:10px 8px;color:var(--gray);font:700 12px var(--font-head);letter-spacing:.06em;text-transform:uppercase;border-bottom:2px solid var(--border);white-space:nowrap}
td{text-align:left;padding:11px 8px;border-bottom:1px solid var(--border);white-space:nowrap}tbody tr:last-child td{border-bottom:0}
.st{display:inline-block;font:700 11px var(--font-head);letter-spacing:.06em;text-transform:uppercase;padding:2px 10px;border-radius:999px}
.st-claimed,.st-ok{background:var(--green-ink);color:#fff}.st-free{background:var(--green-dim);color:var(--green-ink);border:1px solid var(--green-border)}
.st-building,.st-pending,.st-running{background:var(--surface-hi);color:var(--gray)}.st-failed,.st-unreachable{background:#FDECEA;color:var(--err)}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}.stat{flex:1;min-width:120px;margin:0}
.stat b{display:block;font:700 34px/1.1 var(--font-head);color:var(--green-ink);margin-top:4px}.stat .note{font:700 11px var(--font-head);letter-spacing:.1em;text-transform:uppercase}
.stat-code{flex:2;min-width:240px}.stat-code b{font:600 18px var(--mono);color:var(--text);white-space:nowrap;padding-top:10px}
progress{width:120px;height:8px;vertical-align:middle;margin-right:8px;accent-color:var(--green)}
.sub-row td{padding-top:0;white-space:normal}tr:has(+ .sub-row) td{border-bottom:0}
.ph-done{color:var(--green-ink)}.ph-todo{color:var(--gray)}.mt{margin-top:16px}
.guide{background:var(--green-dim);border-color:var(--green-border);border-top-color:var(--green)}.guide .btn{margin-top:14px}
.guide-inline{margin:0 0 18px}.guide-inline .btn{margin-top:0}
.forget{margin:6px 0 0}.linkbtn{background:none;border:0;padding:0;color:var(--gray);font:inherit;font-size:13px;text-decoration:underline;cursor:pointer}
details.recover summary{font-weight:600;cursor:pointer}details.recover[open] summary{margin-bottom:14px}
details.card summary{cursor:pointer;font-family:var(--font-head);font-size:16px}details.card summary b{margin-right:8px}
footer{border-top:1px solid var(--border);background:var(--surface);color:var(--gray);font-size:13px;padding:22px 0}
.footer-inner{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.footer-logo{height:20px;width:auto;opacity:.75}
.footer-privacy{max-width:560px;text-align:right;line-height:1.5}.footer-privacy a{color:var(--gray);text-decoration:underline}
.privacy-note{color:var(--gray);font-size:13px;margin:14px 0 0}.privacy-note a{color:var(--gray);text-decoration:underline}
.privacy h2{margin-top:6px}.privacy p,.privacy ul{margin:0 0 12px}.privacy li{margin-bottom:6px}.privacy .card .tbl{padding:0}
.privacy td{white-space:normal;vertical-align:top}.privacy td:last-child{white-space:nowrap}
@media (max-width:640px){.footer-privacy{text-align:left}.nav-tag{display:none}.logo-img{height:22px}.logo-product{font-size:14px}pre{white-space:pre-wrap;word-break:break-all}dl{grid-template-columns:1fr;gap:2px}dd{margin-bottom:10px}.card{padding:18px}.tbl{padding:4px 12px}.hero{padding:32px 0 24px}}
"""
_JS = (
    "document.querySelectorAll('.cp').forEach(function(b){b.addEventListener('click',function(){"
    "navigator.clipboard.writeText(b.dataset.v);b.textContent='copied'})});"
)
# Served from rodeo/portal/static (copied to the VM with the code). Licences for the
# fonts (SIL OFL 1.1) ship alongside them and are not served.
STATIC_FILES = {
    "suse-logo.png": "image/png",
    "open-sans.woff2": "font/woff2",
    "source-sans-pro-400.woff2": "font/woff2",
    "source-sans-pro-600.woff2": "font/woff2",
    "source-sans-pro-700.woff2": "font/woff2",
}
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str, *, heading: str = "", sub: str = "", badge: str = "",
          product: str = "", tag: str = "", wide: bool = False,
          refresh: int | None = None) -> bytes:
    """SUSE-branded shell: sticky top bar, green hero, content, footer.

    ``heading`` is trusted HTML (callers escape); every other text is escaped here."""
    meta = f"<meta http-equiv=refresh content={refresh}>" if refresh else ""
    cls = " class=wide" if wide else ""
    hero = ""
    if heading:
        hero = ("<header class=hero><div class=wrap>"
                + (f"<span class=badge>{_e(badge)}</span>" if badge else "")
                + f"<h1>{heading}</h1>"
                + (f"<p class=hero-sub>{_e(sub)}</p>" if sub else "")
                + "</div></header>")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"{meta}<title>{_e(title)}</title><link rel=stylesheet href=/s.css></head>"
        f"<body{cls}><nav><div class='wrap nav-inner'><a class=logo href=/>"
        "<img class=logo-img src=/static/suse-logo.png alt=SUSE><span class=logo-divider></span>"
        f"<span class=logo-product>{_e(product or 'Hands-on labs')}</span></a>"
        + (f"<span class=nav-tag>{_e(tag)}</span>" if tag else "")
        + f"</div></nav>{hero}<main><div class=wrap>{body}</div></main>"
        "<footer><div class='wrap footer-inner'><img class=footer-logo src=/static/suse-logo.png alt=SUSE>"
        "<span class=footer-privacy>Privacy: we only keep your name and email for this workshop, "
        "deleted when it ends. Essential cookies only. <a href=/privacy>Details</a></span>"
        "</div></footer>"
        "<script src=/s.js></script></body></html>"
    ).encode()


def _guide(url: str, *, compact: bool = False) -> str:
    """Workshop guide link: a card on the student's lab page, a button elsewhere.
    The URL was validated (https:// or /path) when it was configured."""
    if not url:
        return ""
    ext = url.startswith("https://")
    target = " target=_blank rel='noopener noreferrer'" if ext else ""
    btn = f"<a class=btn href='{_e(url)}'{target}>Open the workshop guide</a>"
    if compact:
        return f"<p class=guide-inline>{btn}</p>"
    return ("<div class='card guide'><h2>Workshop guide</h2><p class=note>Step-by-step "
            "exercises for this lab. Keep it open next to this page.</p>" + btn + "</div>")


def _copyable(value: str) -> str:
    return f"<code>{_e(value)}</code><button class=cp type=button data-v='{_e(value)}'>copy</button>"


def _span(seconds: float) -> str:
    """Human duration for the privacy page, from the constants the code uses."""
    seconds = int(seconds)
    if seconds > 86400 and seconds % 86400 == 0:
        n, unit = seconds // 86400, "day"
    elif seconds % 3600 == 0:
        n, unit = seconds // 3600, "hour"
    else:
        n, unit = max(1, seconds // 60), "minute"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def privacy_page(title: str) -> bytes:
    """Plain-language privacy notice. Keep it true to the code: every duration below
    comes from the constants that set the cookies and the failure limit."""
    cookies = (
        ("csrf", "Protects the forms on this site from forged submissions.",
         "Until you close the browser"),
        ("access", "Remembers that you entered the workshop code.", _span(ACCESS_MAX_AGE)),
        ("lab", "Remembers this device so “Continue to your lab” can take you back. "
                "“Forget this device” removes it.", _span(LAB_COOKIE_MAX_AGE)),
    )
    rows = "".join(f"<tr><td><code>{n}</code></td><td>{_e(why)}</td><td>{_e(life)}</td></tr>"
                   for n, why, life in cookies)
    body = f"""<div class='privacy'>
<div class=card><h2>What we keep</h2>
<ul><li><b>Your name and email</b>, to assign your lab and to give it back to you if you lose the page.</li>
<li><b>Your PIN</b>, stored only as a one-way hash: nobody can read it, including the instructor.</li>
<li><b>When you claimed and first opened your lab.</b></li></ul>
<p>This is kept only on this workshop's own server. Your instructor can see which lab you have, with your name and email, to help you during the workshop, and can export that list (for example for attendance).</p></div>
<div class=card><h2>How long</h2>
<p>Everything above is deleted when the workshop ends and this server is shut down.</p></div>
<div class=card><h2>What we do not do</h2>
<ul><li>No tracking, analytics or advertising, and nothing is shared with third parties. Even the fonts and logo are served by this server.</li>
<li>Your network address is not stored. To stop password guessing, the addresses of failed attempts are kept in memory for at most {_e(_span(RATE_WINDOW))} and never written to disk.</li></ul></div>
<div class=card><h2>Cookies</h2>
<p>Only these three, all needed for the portal to work. None of them tracks you.</p>
<div class=tbl><table><thead><tr><th>Name</th><th>Purpose</th><th>Lasts</th></tr></thead><tbody>{rows}</tbody></table></div></div>
<div class=card><h2>Questions</h2><p>Ask your instructor.</p></div>
<p><a class=btn href=/>Back to the portal</a></p></div>"""
    return _page("Privacy", body, heading="Privacy", badge="Your data",
                 sub="What this lab portal keeps, why, and for how long.", product=title)


def _forget_form(csrf: str) -> str:
    return (f"<form method=post action=/forget class=forget><input type=hidden name=csrf value='{_e(csrf)}'>"
            "<button class=linkbtn type=submit>Not you? Forget this device</button></form>")


def _continue(mine: Any, csrf: str) -> str:
    """Card for a browser that already holds a lab (remembered device)."""
    if mine is None:
        return ""
    return (f"<div class='card guide'><h2>Welcome back, {_e(mine['name'] or 'student')}</h2>"
            f"<p class=note>This device remembers your lab <b>{_e(mine['id'])}</b>.</p>"
            f"<p class=guide-inline><a class=btn href='/l/{_e(mine['token'])}'>Continue to your lab</a></p>"
            + _forget_form(csrf) + "</div>")


def _recover_form(csrf: str, *, email: str = "", err: str = "") -> str:
    """Lost link on a new device: email + PIN, nothing else."""
    msg = f"<p class=err role=alert>{_e(err)}</p>" if err else ""
    opened = " open" if err else ""
    return (f"<details class='card recover'{opened}><summary>Already have a lab? Get it back</summary>{msg}"
            f"<form method=post action=/recover><input type=hidden name=csrf value='{_e(csrf)}'>"
            f"<label for=r-email>Email</label><input id=r-email name=email type=email required maxlength=254 autocomplete=email value='{_e(email)}'>"
            "<label for=r-pin>Your 6-digit PIN</label><input id=r-pin name=pin required inputmode=numeric pattern='[0-9]{6}' minlength=6 maxlength=6 autocomplete=off>"
            "<div class=hint>Locked out or forgot your PIN? Ask your instructor.</div>"
            "<button class=btn type=submit>Get my lab back</button></form></details>")


def gate_page(title: str, csrf: str, *, err: str = "", mine: Any = None) -> bytes:
    """First page: only the workshop code. Nothing about labs or people before it
    (except, on a remembered device, a way back to that student's own lab)."""
    msg = f"<p class=err role=alert>{_e(err)}</p>" if err else ""
    return _page(title or "Hands-on labs", _continue(mine, csrf) + f"""<form class=card method=post action=/enter>{msg}
<input type=hidden name=csrf value='{_e(csrf)}'>
<label for=code>Workshop code</label><input id=code name=code required maxlength=40 autocomplete=off autocapitalize=characters spellcheck=false placeholder="RODEO-ABCD-20260930">
<button class=btn type=submit>Continue</button></form>""",
                 heading=_e(title or "Hands-on labs"), badge="Hands-on lab",
                 sub="Enter the workshop code your instructor gives you.", product=title)


def claim_form(title: str, csrf: str, *, mode: str, is_open: bool, err: str = "",
               email: str = "", name: str = "", rows: list[dict[str, Any]] | None = None,
               guide_url: str = "", mine: Any = None, recover_err: str = "") -> bytes:
    labs = _guide(guide_url, compact=True) + board(rows or [])
    back = _continue(mine, csrf)
    shell = {"heading": _e(title or "Hands-on labs"), "badge": "Hands-on lab", "product": title}
    if mode == "roster":
        return _page(title or "Hands-on labs", back + labs,
                     sub="Open the personal link your instructor sent you to get your lab.", **shell)
    recover = _recover_form(csrf, email=email if recover_err else "", err=recover_err)
    if not is_open:
        return _page(title or "Hands-on labs", back + recover + labs,
                     sub="Claiming is closed. Ask your instructor.", **shell)
    msg = f"<p class=err role=alert>{_e(err)}</p>" if err else ""
    return _page(title or "Hands-on labs", back + f"""<form class=card method=post action=/claim>{msg}
<input type=hidden name=csrf value='{_e(csrf)}'>
<label for=name>Your name</label><input id=name name=name required maxlength=80 autocomplete=name value='{_e(name)}'>
<label for=email>Email</label><input id=email name=email type=email required maxlength=254 autocomplete=email value='{_e(email)}'>
<label for=pin>6-digit PIN</label><input id=pin name=pin required inputmode=numeric pattern="[0-9]{{6}}" minlength=6 maxlength=6 autocomplete=off>
<div class=hint>Choose 6 digits that are not obvious (not 123456 or 111111). On another device, your email and this PIN get your lab back.</div>
<button class=btn type=submit>Get my lab</button>
<p class=privacy-note>Your name and email are used only to assign your lab and are deleted after the workshop. <a href=/privacy>Privacy details</a></p></form>{recover}{labs}""",
                 sub="Claim your personal lab: it is yours for the whole workshop.", **shell)


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
        # Student SSH has its own port (not 22); records published before it had none.
        raw_port = str(ssh.get("port") or "")
        port = int(raw_port) if raw_port.isdigit() else 22
        port_opt = f"-p {port} " if port != 22 else ""
        cmd = f"ssh {port_opt}-i {key} {ssh['user']}@{ssh['host']}"
        cards += (
            f"<div class=card><h2>{_e(ssh_title)}</h2><dl>"
            f"<dt>User</dt><dd>{_copyable(ssh['user'])}</dd><dt>Host</dt><dd>{_copyable(ssh['host'])}</dd>"
            f"<dt>Port</dt><dd>{_copyable(str(port))}</dd></dl>"
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
        f"<tr><td>{_e(r['lab'])}</td><td><span class='st st-{_e(r['state'])}'>{_e(r['state'])}</span></td>"
        f"<td>{_e(r['name']) if r['state'] == 'claimed' else ''}</td></tr>"
        for r in rows
    )
    free = sum(1 for r in rows if r["state"] == "free")
    return (f"<h2 class=h2x>Labs <span class=note>{free} of {len(rows)} free</span></h2>"
            "<div class='card tbl'><table><thead><tr><th>Lab</th><th>State</th>"
            f"<th>Claimed by</th></tr></thead><tbody>{body}</tbody></table></div>")


def lab_page(row: Any, token: str, *, title: str = "", guide_url: str = "",
             csrf: str = "") -> bytes:
    lab_id = row["id"]
    who = row["name"] or row["email"]
    shell = {"heading": f"Your lab <span class=pill>{_e(lab_id)}</span>", "badge": "Your lab",
             "product": title, "tag": "Personal link",
             "sub": f"Assigned to {who}. Bookmark this page: it is your personal link. "
                    "Do not share it."}
    guide = _guide(guide_url)
    if not row["ready"]:
        return _page(f"Lab {lab_id}", "<div class=card><h2>Still building</h2>"
                     "<p>Your lab is not ready yet. This page refreshes every minute.</p></div>"
                     + guide, refresh=60, **shell)
    cards = guide + _lab_cards(lab_id, json.loads(row["data"] or "{}"), f"/l/{token}/key",
                       ssh_title="SSH to your lab host")
    forget = _forget_form(csrf) if csrf else ""
    return _page(f"Lab {lab_id}", cards + "<p class=note>The lab web UIs use self-signed "
                 "certificates. Accept the browser warning to continue.</p>" + forget, **shell)


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
            f"<td><progress max={max(total, 1)} value={min(done, max(total, 1))}></progress>"
            f"<span class=note>{done}/{total}</span></td>"
            f"<td>{current}</td><td>{elapsed}</td>"
            f"<td><span class='st st-{_e(state)}'>{_e(_STATE_LABEL.get(state, state))}</span></td></tr>"
            f"<tr class=sub-row><td></td><td colspan=4><span class=note>{phases}</span>{err}</td></tr>"
        )
    return ("<h2 class=h2x>Deployment</h2>" + fresh +
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
        f"<div class='card stat{extra}'><span class=note>{label}</span><b>{value}</b></div>"
        for label, value, extra in (("Labs", n, ""), ("Claimed", claimed, ""), ("Free", free, ""),
                                    ("Workshop code", _e(s["code"]), " stat-code"),
                                    ("Claiming", "open" if s["open"] else "closed", ""))
    )
    body = "".join(
        f"<tr><td><a href='#lab-{_e(r['lab'])}'>{_e(r['lab'])}</a></td>"
        f"<td><span class='st st-{_e(r['state'])}'>{_e(r['state'])}</span></td>"
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
            f"<span class='st st-{_e(r['state'])}'>{_e(r['state'])}</span> "
            f"<span class=note>{who}{' &middot; ' + _e(host) if host else ''}</span></summary>"
            f"<div class=mt>{inner}</div></details>"
        )
    guide = (f"<p class=note>Workshop guide shown to students: <a href='{_e(s['guide_url'])}' "
             f"target=_blank rel='noopener noreferrer'>{_e(s['guide_url'])}</a></p>"
             if s.get("guide_url") else
             "<p class=note>No workshop guide link configured (portal.guide_url).</p>")
    return _page(
        "Instructor view",
        f"<div class=stats>{stats}</div>{guide}<div class='card tbl'><table><thead><tr><th>Lab</th>"
        "<th>State</th><th>Name</th><th>Email</th><th>Via</th><th>Claimed (UTC)</th>"
        f"<th>First opened</th></tr></thead><tbody>{body}</tbody></table></div>"
        + progress_section(progress or {}, progress_at, [r["lab"] for r in rows])
        + f"<h2 class=h2x>Lab details</h2>{details}",
        heading=f"{_e(s['title'] or 'Workshop')}: instructor view", badge="Instructors only",
        sub="This page shows every lab's credentials. Do not project it. Changes go through "
            "rodeo fleet portal; rodeo fleet portal admin-link revokes this link.",
        product=s["title"], tag="Instructor", wide=True,
        # Follow the deploy live; stop refreshing once every lab is ready so open
        # detail panels (passwords) do not collapse under the instructor.
        refresh=30 if any(r["state"] == "building" for r in rows) else None,
    )


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, t: float) -> list[float]:
        return [x for x in self._hits.get(key, []) if t - x < self.window]

    def over(self, key: str) -> bool:
        """True when ``key`` already used up its failures (checked before work)."""
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.limit

    def count(self, key: str) -> int:
        """Failures ``key`` has in the current window."""
        with self._lock:
            return len(self._recent(key, time.monotonic()))

    def fail(self, key: str) -> None:
        """Record one failed attempt for ``key``."""
        t = time.monotonic()
        with self._lock:
            self._hits[key] = [*self._recent(key, t), t]
            if len(self._hits) > 10000:  # bound memory under a scan
                self._hits = {k: v for k, v in self._hits.items() if t - v[-1] < self.window}


def _route(path: str) -> str:
    path = re.sub(r"^/l/[^/]+", "/l/<token>", path)
    return re.sub(r"^/admin/[^/]+", "/admin/<token>", path)


class Handler(BaseHTTPRequestHandler):
    server_version = "rodeo-portal"
    sys_version = ""
    db_path: str | None = None
    limiter = RateLimiter()
    code_limiter = RateLimiter(limit=CODE_FAIL_LIMIT_ALL)
    secure_cookie = True

    def log_message(self, fmt: str, *args: Any) -> None:
        status = args[1] if len(args) > 1 else "-"
        sys.stderr.write(f"{self.command} {_route(self.path.split('?')[0])} {status}\n")

    def _ip(self) -> str:
        # Caddy appends the real client address as the last X-Forwarded-For hop.
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[-1].strip() if fwd else self.client_address[0]

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra: list[tuple[str, str]] | None = None, cache: str = "no-store") -> None:
        self.send_response(code)
        for k, v in (
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", cache),
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

    def _cookie(self, name: str) -> str:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[name].value if name in c else ""

    def _csrf(self) -> str:
        return self._cookie("csrf")

    def _flags(self) -> str:
        return "; HttpOnly; SameSite=Strict" + ("; Secure" if self.secure_cookie else "")

    def _slow_down(self) -> None:
        self._send(429, _page("Slow down", "", heading="Too many attempts",
                              sub="Wait a few minutes and try again."))

    def _codes_paused(self) -> None:
        self._send(429, _page("Slow down", "", heading="Too many wrong codes",
                              sub="Too many wrong workshop codes from your network. Wait a few "
                                  "minutes and try again, or ask your instructor."))

    def _mine(self, con: Any) -> Any:
        """The lab this browser remembers (lab cookie), as a dict, or None."""
        token = self._cookie("lab")
        row = claims.lab_for_token(con, token) if token else None
        return None if row is None else {"id": row["id"], "name": row["name"], "token": token}

    def _lab_cookie(self, token: str, *, forget: bool = False) -> str:
        age = 0 if forget else LAB_COOKIE_MAX_AGE
        return f"lab={'' if forget else token}; Path=/; Max-Age={age}{self._flags()}"

    def _front(self, code: int = 200, *, gate_err: str = "", **kw: Any) -> None:
        """Code gate, or (with a valid access cookie) claim form + lab board. A
        remembered lab shows "Continue to your lab" on both."""
        tok = self._csrf() or secrets.token_urlsafe(16)
        con = connect(self.db_path)
        try:
            s = claims.settings(con)
            allowed = claims.has_access(con, self._cookie("access"))
            rows = claims.status_rows(con) if allowed else []
            mine = self._mine(con)
        finally:
            con.close()
        body = (claim_form(s["title"], tok, mode=s["mode"], is_open=s["open"], rows=rows,
                           guide_url=s["guide_url"], mine=mine, **kw)
                if allowed else gate_page(s["title"], tok, err=gate_err, mine=mine))
        self._send(code, body, extra=[("Set-Cookie", f"csrf={tok}; Path=/{self._flags()}")])

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        headers = [("Location", location), ("Cache-Control", "no-store"),
                   ("Referrer-Policy", "no-referrer"), ("Content-Length", "0")]
        if cookie:
            headers.append(("Set-Cookie", cookie))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            return self._front()
        if path == "/s.css":
            return self._send(200, _CSS.encode(), "text/css; charset=utf-8")
        if path == "/s.js":
            return self._send(200, _JS.encode(), "application/javascript")
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if path == "/privacy":
            con = connect(self.db_path)
            try:
                title = claims.settings(con)["title"]
            finally:
                con.close()
            return self._send(200, privacy_page(title))
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name in STATIC_FILES:  # fixed allow-list: no path handling at all
                return self._send(200, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name],
                                  cache="public, max-age=86400")
            return self._send(404, b"", "text/plain")
        m = re.fullmatch(r"/l/([^/]+)(/key)?", path)
        if m:
            if self.limiter.over(self._ip()):
                return self._slow_down()
            con = connect(self.db_path)
            try:
                row = claims.lab_for_token(con, m.group(1))
            finally:
                con.close()
            if row is None:
                self.limiter.fail(self._ip())
                return self._send(404, _page("Link not valid", "<p><a class=btn href=/>Claim a lab</a></p>",
                                             heading="Link not valid",
                                             sub="This link was released or never existed."))
            if m.group(2):
                key = (json.loads(row["data"] or "{}").get("ssh") or {}).get("private_key")
                if not key or not row["ready"]:
                    return self._send(404, b"no key", "text/plain")
                return self._send(200, key.encode(), "application/octet-stream", extra=[
                    ("Content-Disposition", f'attachment; filename="{row["id"]}.key"')])
            con = connect(self.db_path)
            try:
                st = claims.settings(con)
            finally:
                con.close()
            csrf = self._csrf() or secrets.token_urlsafe(16)
            return self._send(200, lab_page(row, m.group(1), title=st["title"],
                                            guide_url=st["guide_url"], csrf=csrf),
                              extra=[("Set-Cookie", self._lab_cookie(m.group(1))),
                                     ("Set-Cookie", f"csrf={csrf}; Path=/{self._flags()}")])
        m = re.fullmatch(r"/admin/([^/]+)(?:/key/([A-Za-z0-9_.-]+))?", path)
        if m:
            if self.limiter.over(self._ip()):
                return self._slow_down()
            con = connect(self.db_path)
            try:
                if not claims.is_admin_token(con, m.group(1)):
                    self.limiter.fail(self._ip())
                    return self._send(404, _page("Not found", "", heading="Not found"))
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
        self._send(404, _page("Not found", "", heading="Not found"))

    def _read_form(self) -> dict[str, str] | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = MAX_BODY + 1
        if n > MAX_BODY or n < 0:
            self._send(413, b"too large", "text/plain")
            return None
        raw = self.rfile.read(n).decode(errors="replace")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/enter", "/claim", "/recover", "/forget"):
            return self._send(404, b"", "text/plain")
        f = self._read_form()
        if f is None:
            return
        email = f.get("email", "").strip()[:254]
        name = f.get("name", "").strip()[:80]
        cookie = self._csrf()
        if not cookie or not hmac.compare_digest(cookie, f.get("csrf", "")):
            return self._front(400, gate_err="Your session expired. Please submit again.",
                               err="Your session expired. Please submit again.",
                               email=email, name=name)
        if self.path == "/forget":
            return self._redirect("/", self._lab_cookie("", forget=True))
        if self.limiter.over(self._ip()):
            return self._slow_down()
        con = connect(self.db_path)
        try:
            if self.path == "/enter":
                if (self.code_limiter.over("*")
                        and self.limiter.count(self._ip()) >= CODE_FAILS_WHILE_CAPPED):
                    con.close()
                    return self._codes_paused()
                if not claims.code_matches(f.get("code", ""), claims.settings(con)["code"]):
                    self.limiter.fail(self._ip())
                    self.code_limiter.fail("*")
                    con.close()
                    return self._front(403, gate_err="That workshop code is not valid.")
                access = claims.access_token(con)
                return self._redirect("/", f"access={access}; Path=/; Max-Age={ACCESS_MAX_AGE}"
                                           f"{self._flags()}")
            if not claims.has_access(con, self._cookie("access")):
                return self._redirect("/")  # code rotated or never entered
            current = claims.settings(con)["code"] or ""
            try:
                if self.path == "/recover":
                    token, _ = claims.recover(con, code=current, email=email, pin=f.get("pin", ""))
                else:
                    token, _ = claims.claim_open(con, code=current, email=email, name=name,
                                                 pin=f.get("pin", ""))
            except claims.ClaimError as exc:
                if isinstance(exc, claims.GuessError):
                    self.limiter.fail(self._ip())
                con.close()
                if self.path == "/recover":
                    return self._front(403, recover_err=str(exc), email=email)
                return self._front(403, err=str(exc), email=email, name=name)
        finally:
            con.close()
        self._redirect(f"/l/{token}", self._lab_cookie(token))


def make_server(host: str, port: int, *, db_path: str | None = None,
                secure_cookie: bool = True) -> ThreadingHTTPServer:
    handler = type("PortalHandler", (Handler,), {
        "db_path": db_path, "limiter": RateLimiter(),
        "code_limiter": RateLimiter(limit=CODE_FAIL_LIMIT_ALL), "secure_cookie": secure_cookie,
    })
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv
