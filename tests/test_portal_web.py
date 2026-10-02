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

    def claim(self, email="ana@x.io", pin="583920", name="Ana"):
        return self.post("/claim", email=email, pin=pin, name=name)

    def recover(self, email="ana@x.io", pin="583920"):
        return self.post("/recover", email=email, pin=pin)


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
    # board and claim form hidden again (this browser still offers its own lab)
    assert "Workshop code" in page and "Claimed by" not in page and "Get my lab</button>" not in page
    assert Browser(portal["port"]).get("/")[2].count("lab-01") == 0
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
    s, _, body = browser.post("/claim", csrf="aaaa", email="a@x.io", pin="583920", name="A")
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


# ---------------------------------------------------------------- getting back to your lab
def test_remembered_device_offers_continue_even_before_the_code(portal, browser):
    _, h, _ = browser.claim(name="Ana Lopez")
    link = h["Location"]
    page = browser.get("/")[2]
    assert "Welcome back, Ana Lopez" in page and f"href='{link}'" in page
    con = connect(portal["db"])
    claims.rotate_code(con)  # board access gone, the remembered lab is not
    con.close()
    page = browser.get("/")[2]
    assert "Workshop code" in page and f"href='{link}'" in page


def test_opening_a_personal_link_remembers_the_device(portal, browser):
    _, h, _ = browser.claim()
    phone = Browser(portal["port"])
    phone.get(h["Location"])
    assert "Continue to your lab" in phone.get("/")[2]


def test_forget_this_device(portal, browser):
    browser.claim()
    assert "Continue to your lab" in browser.get("/")[2]
    s, h, _ = browser.post("/forget")
    assert s == 303 and any(v.startswith("lab=;") and "Max-Age=0" in v
                            for v in h.get_all("Set-Cookie"))
    assert "Continue to your lab" not in browser.get("/")[2]


def test_recover_on_a_new_device_with_email_and_pin_only(portal, browser):
    _, h, _ = browser.claim(name="Ana")
    old = h["Location"]
    laptop = Browser(portal["port"])
    laptop.enter(portal["code"])
    s, h2, _ = laptop.recover()
    assert s == 303 and h2["Location"] != old
    assert "lab-01" in laptop.get(h2["Location"])[2]
    assert browser.get(old)[0] == 404  # the old link stops working
    assert "Continue to your lab" in laptop.get("/")[2]


def test_failed_recovery_opens_the_form_with_a_generic_error(portal, browser):
    browser.claim()
    other = Browser(portal["port"])
    other.enter(portal["code"])
    s, _, page = other.recover(pin="739273")
    assert s == 403 and "No lab matches that email and PIN" in page
    assert "<details class='card recover' open>" in page


def test_wrong_recovery_pins_count_as_failed_attempts(portal, browser):
    browser.claim()
    other = Browser(portal["port"])
    other.enter(portal["code"])
    for i in range(RATE_LIMIT):
        other.recover(email=f"x{i}@x.io", pin="739273")
    assert other.recover()[0] == 429


def test_claim_form_asks_for_a_six_digit_pin_and_refuses_obvious_ones(browser):
    page = browser.get("/")[2]
    assert "6-digit PIN" in page and 'pattern="[0-9]{6}"' in page
    s, _, page = browser.claim(pin="123456")
    assert s == 403 and "too easy to guess" in page


# ---------------------------------------------------------------- privacy notice
def test_privacy_page_is_public_and_lists_the_real_cookies(portal):
    from rodeo.portal.web import ACCESS_MAX_AGE, LAB_COOKIE_MAX_AGE, _span

    s, _, page = Browser(portal["port"]).get("/privacy")
    assert s == 200 and "What we keep" in page and "deleted when the workshop ends" in page
    for name in ("csrf", "access", "lab"):
        assert f"<code>{name}</code>" in page
    assert _span(ACCESS_MAX_AGE) == "24 hours" and _span(ACCESS_MAX_AGE) in page
    assert _span(LAB_COOKIE_MAX_AGE) == "3 days" and _span(LAB_COOKIE_MAX_AGE) in page
    assert "do not store personal data" not in page  # it would not be true


def test_the_privacy_page_matches_the_cookies_actually_set(portal, browser):
    """If someone changes a cookie, this fails until /privacy is updated too."""
    from rodeo.portal.web import ACCESS_MAX_AGE, LAB_COOKIE_MAX_AGE

    b = Browser(portal["port"])
    _, h, _ = b.get("/")
    csrf = [v for v in h.get_all("Set-Cookie") if v.startswith("csrf=")][0]
    assert "Max-Age" not in csrf and "Expires" not in csrf  # session cookie, as documented
    _, h, _ = b.enter(portal["code"])
    assert f"Max-Age={ACCESS_MAX_AGE}" in [v for v in h.get_all("Set-Cookie") if v.startswith("access=")][0]
    _, h, _ = b.claim()
    assert f"Max-Age={LAB_COOKIE_MAX_AGE}" in [v for v in h.get_all("Set-Cookie") if v.startswith("lab=")][0]
    names = {v.split("=", 1)[0] for v in h.get_all("Set-Cookie")} | {"csrf", "access"}
    assert names <= {"csrf", "access", "lab"}


def test_footer_privacy_line_on_every_page_and_note_under_claim_form(portal, browser):
    con = connect(portal["db"])
    tok = claims.rotate_admin_token(con)
    con.close()
    _, h, _ = browser.claim()
    pages = [Browser(portal["port"]).get("/")[2], browser.get("/")[2], browser.get(h["Location"])[2],
             _req(portal["port"], "GET", f"/admin/{tok}")[2], browser.get("/privacy")[2]]
    for page in pages:
        assert "Essential cookies only" in page and "href=/privacy" in page
    other = Browser(portal["port"])
    other.enter(portal["code"])
    assert "used only to assign your lab" in other.get("/")[2]


def test_logs_do_not_contain_ip_addresses(portal, browser, capsys):
    browser.claim()
    browser.get("/privacy")
    assert "127.0.0.1" not in capsys.readouterr().err
