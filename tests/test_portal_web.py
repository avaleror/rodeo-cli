"""Claim portal HTTP layer (F5.1): CSRF, headers, escaping, rate limit, logs."""
from __future__ import annotations

import http.client
import re
import threading
import urllib.parse

import pytest

from rodeo.portal import claims
from rodeo.portal.db import connect, migrate
from rodeo.portal.web import make_server


@pytest.fixture()
def portal(tmp_path, capsys):
    p = str(tmp_path / "portal.db")
    migrate(p)
    con = connect(p)
    claims.ensure_defaults(con, mode="both", title="Work<shop>")
    claims.import_labs(con, [{"id": "lab-01", "ready": True, "data": {
        "components": [{"label": "Harvester", "url": "https://1.2.3.4:8443",
                        "user": "admin", "password": "Pw<'\"&>1aA"}],
        "ssh": {"user": "student", "host": "1.2.3.4", "private_key": "PRIVATE-KEY"},
    }}])
    code = claims.settings(con)["code"]
    con.close()
    srv = make_server("127.0.0.1", 0, db_path=p, secure_cookie=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"port": srv.server_address[1], "code": code, "db": p}
    srv.shutdown()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read().decode(errors="replace")
    return r.status, dict(r.getheaders()), data


def _csrf(port):
    _, h, body = _req(port, "GET", "/")
    return re.search(r"name=csrf value='([^']+)'", body).group(1)


def _post_claim(port, code, email="ana@x.io", pin="1234", name="Ana", csrf=None, cookie=None):
    tok = csrf or _csrf(port)
    body = urllib.parse.urlencode({"csrf": tok, "code": code, "email": email, "pin": pin,
                                   "name": name})
    return _req(port, "POST", "/claim", body, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": f"csrf={cookie if cookie is not None else tok}",
    })


def test_claim_redirects_to_personal_page_with_escaped_values(portal):
    s, h, _ = _post_claim(portal["port"], portal["code"], name="<script>x</script>")
    assert s == 303 and h["Location"].startswith("/l/")
    s, h, page = _req(portal["port"], "GET", h["Location"])
    assert s == 200 and "lab-01" in page
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "Pw&lt;&#x27;&quot;&amp;&gt;1aA" in page


def test_security_headers_on_every_page(portal):
    _, h, _ = _req(portal["port"], "GET", "/")
    assert h["Cache-Control"] == "no-store"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]


def test_csrf_mismatch_rejected(portal):
    s, _, body = _post_claim(portal["port"], portal["code"], csrf="aaaa", cookie="bbbb")
    assert s == 400 and "expired" in body
    s, _, _ = _post_claim(portal["port"], portal["code"], csrf="aaaa", cookie="")
    assert s == 400


def test_key_download_only_with_valid_link(portal):
    _, h, _ = _post_claim(portal["port"], portal["code"])
    s, hh, body = _req(portal["port"], "GET", h["Location"] + "/key")
    assert s == 200 and body == "PRIVATE-KEY" and "attachment" in hh["Content-Disposition"]
    s, _, _ = _req(portal["port"], "GET", "/l/" + "A" * 32 + "/key")
    assert s == 404


def test_claim_rate_limited_per_ip(portal):
    statuses = [
        _post_claim(portal["port"], "WRONG-CODE", email=f"u{i}@x.io")[0] for i in range(12)
    ]
    assert statuses[:10] == [403] * 10 and 429 in statuses[10:]


def test_admin_view_needs_current_token(portal):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    _post_claim(portal["port"], portal["code"])
    s, _, page = _req(portal["port"], "GET", f"/admin/{tok}")
    assert s == 200 and "ana@x.io" in page and "Work&lt;shop&gt;" in page
    assert _req(portal["port"], "GET", "/admin/" + "B" * 32)[0] == 404


def test_logs_never_contain_tokens_or_emails(portal, capsys):
    _, h, _ = _post_claim(portal["port"], portal["code"], email="secret.person@x.io")
    token = h["Location"].split("/")[-1]
    _req(portal["port"], "GET", h["Location"])
    err = capsys.readouterr().err
    assert token not in err and "secret.person" not in err and portal["code"] not in err
    assert "GET /l/<token> 200" in err


def test_oversized_body_rejected(portal):
    s, _, _ = _req(portal["port"], "POST", "/claim", "x" * 5000,
                   {"Content-Type": "application/x-www-form-urlencoded"})
    assert s == 413


def test_public_board_shows_claimant_name_but_never_email_or_passwords(portal):
    _post_claim(portal["port"], portal["code"], email="ana.secret@x.io", name="Ana <Lopez>")
    s, _, page = _req(portal["port"], "GET", "/")
    assert s == 200
    assert "lab-01" in page and "claimed" in page and "Ana &lt;Lopez&gt;" in page
    assert "ana.secret" not in page and "Pw&lt;" not in page and "PRIVATE-KEY" not in page


def test_instructor_page_shows_every_lab_credential_behind_the_token(portal):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    s, _, page = _req(portal["port"], "GET", f"/admin/{tok}")
    assert s == 200 and "Pw&lt;&#x27;&quot;&amp;&gt;1aA" in page and "not claimed" in page
    s, h, key = _req(portal["port"], "GET", f"/admin/{tok}/key/lab-01")
    assert s == 200 and key == "PRIVATE-KEY" and "lab-01.key" in h["Content-Disposition"]
    assert _req(portal["port"], "GET", "/admin/" + "C" * 32 + "/key/lab-01")[0] == 404
    assert _req(portal["port"], "GET", f"/admin/{tok}/key/nope")[0] == 404


def test_admin_key_route_is_scrubbed_from_logs(portal, capsys):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    _req(portal["port"], "GET", f"/admin/{tok}/key/lab-01")
    assert tok not in capsys.readouterr().err
