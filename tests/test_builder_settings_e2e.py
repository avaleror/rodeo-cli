"""rodeo-cli Settings page end to end in a headless browser (Playwright + Chromium).

Lays the page out as rodeo/builder/settings/install.sh does (/rodeo-builder/settings/), serves
it on 127.0.0.1 and answers its API (/rodeo-builder/admin) in the browser from an in-memory
store. Skipped like test_builder_e2e.py where Playwright or Chromium is missing.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REQUIRED = bool(os.environ.get("RB_E2E_REQUIRED"))
if REQUIRED:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

BUILDER = Path(__file__).resolve().parent.parent / "rodeo" / "builder"
TYPES = [
    {"id": "scc", "label": "SCC", "fields": [
        {"name": "regcode", "label": "Registration code", "secret": True, "optional": False, "multiline": False},
        {"name": "email", "label": "Email", "secret": False, "optional": True, "multiline": False}],
     "keys": {"scc_regcode": "regcode"}},
    {"id": "generic", "label": "Generic", "fields": [], "keys": {}, "freeform": True},
]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    page_dir = root / "rodeo-builder" / "settings"
    page_dir.mkdir(parents=True)
    for name in ("style.css", "logic.js", "theme.js"):
        shutil.copy(BUILDER / "htdocs" / name, page_dir)
    for name in ("assets", "fonts"):
        shutil.copytree(BUILDER / "htdocs" / name, page_dir / name)
    for name in ("index.html", "settings.js"):
        shutil.copy(BUILDER / "settings" / "htdocs" / name, page_dir)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/rodeo-builder/settings/".format(server.server_address[1])
    server.shutdown()


class FakeAdmin:
    """The Settings API (/rodeo-builder/admin), answered from an in-memory store."""

    def __init__(self, logged_in=True, file_secrets=()):
        self.logged_in = logged_in
        self.file_secrets = [{"key": k} for k in file_secrets]
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def handle(self, route):
        req = route.request
        action = parse_qs(urlparse(req.url).query)["action"][0]
        if not self.logged_in:
            return route.fulfill(status=401, json={"error": "log in to manage credentials"})
        if action == "list":
            items = [{k: v for k, v in i.items() if k != "password"} for i in self.items.values()]
            return route.fulfill(json={"types": TYPES, "items": items, "file_secrets": self.file_secrets, "user": "alice"})
        body = req.post_data_json
        self.calls.append((action, body))
        if action == "add":
            item_id = "{:016x}".format(len(self.items) + 1)
            self.items[item_id] = {"id": item_id, "name": body["name"], "type": body["type"], "engine": "rodeo",
                                   "created": "2026-10-28T10:00:00Z", "used": None, "password": body["password"]}
            return route.fulfill(json={"item": {"id": item_id}})
        item = self.items.get(body["id"])
        if item is None:
            return route.fulfill(status=404, json={"error": "unknown credential"})
        if body["password"] != item["password"]:
            return route.fulfill(status=403, json={"error": "wrong password"})
        del self.items[body["id"]]
        return route.fulfill(json={"ok": True})


@pytest.fixture()
def browser_page(site, monkeypatch):
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(real_home / ".cache" / "ms-playwright"))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - any launch failure means no usable browser here
            if REQUIRED:
                raise
            pytest.skip("no Chromium for Playwright: {}".format(exc))
        pg = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error"
              and not msg.text.startswith("Failed to load resource") else None)

        def open_with(admin, query=""):
            pg.route(re.compile(r".*/rodeo-builder/admin\?.*"), admin.handle)
            pg.goto(site + query)
            return admin

        yield pg, open_with
        browser.close()
        assert errors == [], errors


def _values(page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('input, textarea')].map((i) => i.value)")


def test_add_list_and_delete_with_the_password(browser_page):
    page, open_with = browser_page
    admin = open_with(FakeAdmin(file_secrets=["harvester_token", "my_regcode"]))
    sync_api.expect(page.locator("#who")).to_have_text("logged in as alice")
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    assert page.locator(".cred-table thead th").all_inner_texts()[:6] == [
        "NAME", "APPLICATION", "ENGINE", "ENCRYPTED", "ADDED", "LAST USED"]
    sync_api.expect(page.locator("#fileSecretList .chip")).to_have_text(["harvester_token", "my_regcode"])

    page.click("#vaultAddBtn")
    page.select_option("#vaType", "scc")
    page.fill("#vaName", "my scc")
    page.fill("#vaf-regcode", "REG-SECRET-1")
    page.fill("#vaPass", "longpassword")
    page.fill("#vaPass2", "longpassword2")
    page.click("#vaultForm [type=submit]")
    sync_api.expect(page.locator("#vaResult")).to_contain_text("don't match")
    assert admin.calls == []
    page.fill("#vaPass2", "longpassword")
    page.click("#vaultForm [type=submit]")
    row = page.locator(".vault-item")
    sync_api.expect(row).to_contain_text("my scc")
    sync_api.expect(row).to_contain_text("SCC")
    sync_api.expect(row).to_contain_text("rodeo")
    assert admin.calls == [("add", {"name": "my scc", "type": "scc", "fields": {"regcode": "REG-SECRET-1"},
                                    "password": "longpassword"})]
    assert page.locator("#vaultForm").is_hidden()
    assert not any(v in ("REG-SECRET-1", "longpassword") for v in _values(page))

    page.click("button[aria-label='Delete my scc']")
    sync_api.expect(page.locator("#deletePrompt")).to_be_visible()
    page.click("#delSubmit")
    sync_api.expect(page.locator("#delError")).to_have_text("Type the password.")
    page.fill("#delPass", "wrong-password")
    page.click("#delSubmit")
    sync_api.expect(page.locator("#delError")).to_contain_text("Wrong password")
    sync_api.expect(page.locator(".vault-item")).to_have_count(1)
    page.keyboard.press("Escape")
    sync_api.expect(page.locator("#deletePrompt")).to_be_hidden()
    assert page.input_value("#delPass") == ""

    page.click("button[aria-label='Delete my scc']")
    page.fill("#delPass", "longpassword")
    page.click("#delSubmit")
    sync_api.expect(page.locator("#deletePrompt")).to_be_hidden()
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    assert admin.calls[-2:] == [("delete", {"id": "0000000000000001", "password": p}) for p in ("wrong-password", "longpassword")]
    assert not any(v in ("longpassword", "wrong-password") for v in _values(page))


def test_without_a_login_it_says_so(browser_page):
    page, open_with = browser_page
    open_with(FakeAdmin(logged_in=False))
    sync_api.expect(page.locator("#loadError")).to_have_text("Could not load the credentials: log in to manage credentials.")
    assert page.locator("#who").inner_text() == ""


def test_other_engines_credentials_are_listed_read_only(browser_page):
    page, open_with = browser_page
    admin = FakeAdmin()
    admin.items["liab-aws-tmm"] = {"id": "liab-aws-tmm", "name": "aws-tmm", "type": "generic", "engine": "lab-in-a-box",
                                   "created": "2026-10-12T09:09:00Z", "used": None, "password": "",
                                   "app": "aws", "encrypted": False}
    open_with(admin)
    row = page.locator(".vault-item")
    sync_api.expect(row).to_contain_text("aws-tmm")
    sync_api.expect(row).to_contain_text("lab-in-a-box")
    sync_api.expect(row).to_contain_text("read-only here")
    sync_api.expect(row.locator("td")).to_have_text(["aws", "lab-in-a-box", "no", re.compile(r"2026"), "never", "read-only here"])
    assert row.locator("button").count() == 0


def test_add_parameter_opens_the_form_with_that_type(browser_page):
    page, open_with = browser_page
    open_with(FakeAdmin(), "?add=scc")
    sync_api.expect(page.locator("#vaultForm")).to_be_visible()
    assert page.locator("#vaType").input_value() == "scc"
    assert page.locator("#vaFields label").first.text_content() == "Registration code"
    page.goto("about:blank")
    open_with(FakeAdmin(), "?add=no-such-type")
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    assert page.locator("#vaultForm").is_hidden()
