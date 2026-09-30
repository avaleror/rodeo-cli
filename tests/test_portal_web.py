"""Claim portal HTTP layer: code gate, CSRF, headers, escaping, rate limit, logs."""
from __future__ import annotations

import http.client
import re
import threading
import urllib.parse

import pytest

from rodeo.portal import claims
from rodeo.portal.db import connect, migrate
from rodeo.portal.web import RATE_LIMIT, make_server


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
    }}, {"id": "lab-02", "ready": True, "data": {"components": []}}])
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
    return r.status, r.headers, data


def _cookies(headers) -> dict[str, str]:
    out = {}
    for v in headers.get_all("Set-Cookie") or []:
        k, _, rest = v.partition("=")
        out[k] = rest.split(";", 1)[0]
    return out


class Browser:
    """Minimal cookie-keeping client."""

    def __init__(self, port):
        self.port, self.jar = port, {}

    def get(self, path):
        s, h, body = _req(self.port, "GET", path, headers=self._hdr())
        self.jar.update(_cookies(h))
        return s, h, body

    def post(self, path, **form):
        if "csrf" not in form:
            if "csrf" not in self.jar:
                self.get("/")
            form["csrf"] = self.jar["csrf"]
        s, h, body = _req(self.port, "POST", path, urllib.parse.urlencode(form),
                          {"Content-Type": "application/x-www-form-urlencoded", **self._hdr()})
        self.jar.update(_cookies(h))
        return s, h, body

    def _hdr(self):
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.jar.items())} if self.jar else {}

    def enter(self, code):
        return self.post("/enter", code=code)

    def claim(self, email="ana@x.io", pin="1234", name="Ana"):
        return self.post("/claim", email=email, pin=pin, name=name)


@pytest.fixture()
def browser(portal):
    b = Browser(portal["port"])
    assert b.enter(portal["code"])[0] == 303
    return b


# ---------------------------------------------------------------- code gate
def test_code_format():
    code = claims.new_code()
    assert re.fullmatch(r"RODEO-[A-HJ-NP-Z]{4}-\d{8}", code), code
    assert re.fullmatch(r"RODEO-[A-Z]{6}-\d{8}", claims.new_code(6))


def test_gate_hides_board_and_claim_form_until_code_entered(portal):
    b = Browser(portal["port"])
    b.get("/")
    s, _, page = b.get("/")
    assert s == 200 and "Workshop code" in page
    assert "lab-01" not in page and "Get my lab" not in page


def test_code_is_forgiving_about_case_spaces_and_dashes(portal):
    b = Browser(portal["port"])
    sloppy = portal["code"].lower().replace("-", " ")
    assert b.enter(sloppy)[0] == 303
    s, _, page = b.get("/")
    assert "Get my lab" in page and "lab-01" in page


def test_wrong_code_refused_and_claim_without_access_bounces(portal):
    b = Browser(portal["port"])
    s, _, page = b.enter("RODEO-AAAA-20000101")
    assert s == 403 and "not valid" in page
    s, h, _ = b.claim()
    assert s == 303 and h["Location"] == "/"


def test_rotating_the_code_ends_board_sessions_but_not_lab_links(portal, browser):
    _, h, _ = browser.claim()
    link = h["Location"]
    con = connect(portal["db"])
    claims.rotate_code(con)
    con.close()
    _, _, page = browser.get("/")
    assert "Workshop code" in page and "lab-01" not in page
    assert browser.get(link)[0] == 200


# ---------------------------------------------------------------- claiming
def test_claim_redirects_to_personal_page_with_escaped_values(browser):
    s, h, _ = browser.claim(name="<script>x</script>")
    assert s == 303 and h["Location"].startswith("/l/")
    s, _, page = browser.get(h["Location"])
    assert s == 200 and "lab-01" in page
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "Pw&lt;&#x27;&quot;&amp;&gt;1aA" in page


def test_security_headers_on_every_page(portal):
    s, h, _ = _req(portal["port"], "GET", "/")
    assert h["Cache-Control"] == "no-store"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]


def test_access_cookie_is_httponly_samesite(portal):
    b = Browser(portal["port"])
    b.get("/")
    _, h, _ = b.enter(portal["code"])
    access = [v for v in h.get_all("Set-Cookie") if v.startswith("access=")][0]
    assert "HttpOnly" in access and "SameSite=Strict" in access and "Max-Age=" in access


def test_csrf_mismatch_rejected(portal, browser):
    s, _, body = browser.post("/claim", csrf="aaaa", email="a@x.io", pin="1234", name="A")
    assert s == 400 and "expired" in body
    s, _, _ = Browser(portal["port"]).post("/enter", csrf="aaaa", code=portal["code"])
    assert s == 400


def test_key_download_only_with_valid_link(browser):
    _, h, _ = browser.claim()
    s, hh, body = browser.get(h["Location"] + "/key")
    assert s == 200 and body == "PRIVATE-KEY" and "attachment" in hh["Content-Disposition"]
    assert browser.get("/l/" + "A" * 32 + "/key")[0] == 404


# ---------------------------------------------------------------- rate limit (security review #1)
def test_a_whole_classroom_behind_one_ip_can_claim(portal):
    """Successful attempts never count: 40 students from one NAT address."""
    con = connect(portal["db"])
    claims.import_labs(con, [{"id": f"lab-{i:02d}", "ready": True, "data": {}} for i in range(40)])
    con.close()
    for i in range(40):
        b = Browser(portal["port"])
        assert b.enter(portal["code"])[0] == 303
        assert b.claim(email=f"s{i}@x.io")[0] == 303, i


def test_failed_guesses_are_rate_limited_per_ip(portal):
    b = Browser(portal["port"])
    statuses = [b.enter(f"RODEO-ZZZZ-{i:08d}")[0] for i in range(RATE_LIMIT + 2)]
    assert statuses[:RATE_LIMIT] == [403] * RATE_LIMIT and statuses[-1] == 429
    assert b.enter(portal["code"])[0] == 429  # even the right code, until the window passes


def test_typos_in_name_or_email_do_not_count(portal, browser):
    for _ in range(RATE_LIMIT + 5):
        assert browser.claim(email="not-an-email")[0] == 403
    assert browser.claim()[0] == 303


# ---------------------------------------------------------------- board and instructor page
def test_board_shows_claimant_name_but_never_email_or_passwords(browser):
    browser.claim(email="ana.secret@x.io", name="Ana <Lopez>")
    s, _, page = browser.get("/")
    assert s == 200
    assert "lab-01" in page and "claimed" in page and "Ana &lt;Lopez&gt;" in page
    assert "ana.secret" not in page and "Pw&lt;" not in page and "PRIVATE-KEY" not in page


def test_admin_view_needs_current_token(portal, browser):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    browser.claim()
    s, _, page = _req(portal["port"], "GET", f"/admin/{tok}")
    assert s == 200 and "ana@x.io" in page and "Work&lt;shop&gt;" in page
    assert _req(portal["port"], "GET", "/admin/" + "B" * 32)[0] == 404


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


def test_no_page_uses_inline_styles_blocked_by_our_csp(portal, browser):
    """Security review #6: CSP default-src 'self' makes browsers drop style=."""
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    claims.set_progress(con, {"lab-01": {"state": "running", "done": 2, "total": 6}})
    con.close()
    _, h, _ = browser.claim()
    pages = [browser.get("/")[2], browser.get(h["Location"])[2],
             _req(portal["port"], "GET", f"/admin/{tok}")[2], Browser(portal["port"]).get("/")[2]]
    for page in pages:
        assert not re.search(r"\sstyle\s*=", page)
    assert "<progress max=6 value=2>" in pages[2]


# ---------------------------------------------------------------- logs and limits
def test_logs_never_contain_tokens_emails_or_the_code(portal, browser, capsys):
    _, h, _ = browser.claim(email="secret.person@x.io")
    token = h["Location"].split("/")[-1]
    browser.get(h["Location"])
    err = capsys.readouterr().err
    assert token not in err and "secret.person" not in err and portal["code"] not in err
    assert "GET /l/<token> 200" in err


def test_admin_key_route_is_scrubbed_from_logs(portal, capsys):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    _req(portal["port"], "GET", f"/admin/{tok}/key/lab-01")
    assert tok not in capsys.readouterr().err


def test_oversized_body_rejected(portal):
    s, _, _ = _req(portal["port"], "POST", "/claim", "x" * 5000,
                   {"Content-Type": "application/x-www-form-urlencoded"})
    assert s == 413


# ---------------------------------------------------------------- SUSE branding assets
def test_brand_assets_served_from_self_with_types_and_cache(portal):
    for name, ctype in (("suse-logo.png", "image/png"), ("open-sans.woff2", "font/woff2"),
                        ("source-sans-pro-700.woff2", "font/woff2")):
        c = http.client.HTTPConnection("127.0.0.1", portal["port"], timeout=10)
        c.request("GET", f"/static/{name}")
        r = c.getresponse()
        body = r.read()
        assert r.status == 200 and r.headers["Content-Type"] == ctype and len(body) > 1000
        assert "max-age" in r.headers["Cache-Control"]


@pytest.mark.parametrize("path", ["/static/../web.py", "/static/OFL-OpenSans.txt",
                                  "/static/", "/static/%2e%2e/db.py"])
def test_static_route_is_an_allow_list(portal, path):
    assert _req(portal["port"], "GET", path)[0] == 404


def test_pages_reference_only_local_assets(portal, browser):
    page = browser.get("/")[2]
    assert "/static/suse-logo.png" in page and "fonts.googleapis" not in page
    css = _req(portal["port"], "GET", "/s.css")[2]
    assert "https://" not in css and "#00A651" in css


# ---------------------------------------------------------------- workshop guide link
GUIDE = "https://avaleror.github.io/suse-virt-workshop/"


def _set_guide(portal, url):
    con = connect(portal["db"])
    claims.ensure_defaults(con, guide_url=url)
    con.close()


def test_guide_link_on_every_student_page(portal, browser):
    _set_guide(portal, GUIDE)
    _, h, _ = browser.claim()
    lab = browser.get(h["Location"])[2]
    assert "Workshop guide" in lab and f"href='{GUIDE}'" in lab
    assert "rel='noopener noreferrer'" in lab
    assert f"href='{GUIDE}'" in browser.get("/")[2]  # claim page / board
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    assert GUIDE in _req(portal["port"], "GET", f"/admin/{tok}")[2]


def test_guide_link_before_the_lab_is_ready(portal):
    _set_guide(portal, "/guide/")
    con = connect(portal["db"])
    claims.import_labs(con, [{"id": "lab-01", "ready": False, "data": {}},
                             {"id": "lab-02", "ready": True, "data": {}}])
    con.close()
    b = Browser(portal["port"])
    b.enter(portal["code"])
    _, h, _ = b.claim()
    page = b.get(h["Location"])[2]
    assert "href='/guide/'" in page and "target=_blank" not in page.split("href='/guide/'")[1][:40]


def test_no_guide_configured_means_no_link(portal, browser):
    _, h, _ = browser.claim()
    assert "Workshop guide" not in browser.get(h["Location"])[2]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://plain.example", "guide",
                                 "https://x.io/\"onmouseover=", "//evil.example"])
def test_unsafe_guide_urls_rejected(portal, bad):
    con = connect(portal["db"])
    with pytest.raises(claims.ClaimError):
        claims.ensure_defaults(con, guide_url=bad)
    con.close()
