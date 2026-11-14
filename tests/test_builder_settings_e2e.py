"""rodeo-cli Settings page end to end in a headless browser (Playwright + Chromium).

The page as `rodeo builder` serves it (/rodeo-builder/settings/ on the builder's own address),
against the real API and a vault under the test's HOME; the login is alice:alicepass, answered
by the browser. Skipped like test_builder_e2e.py where Playwright or Chromium is missing.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import pytest

from rodeo import labinabox_host
from rodeo.builder import login, vault
from rodeo.builder.api import Api
from rodeo.builder.server import make_server

REQUIRED = bool(os.environ.get("RB_E2E_REQUIRED"))
if REQUIRED:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

LOGIN = {"username": "alice", "password": "alicepass"}


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """A login accepting alice:alicepass and an empty lab-in-a-box credential store."""
    monkeypatch.setattr(login, "verify", lambda user, password: (user, password) == ("alice", "alicepass"))
    engine_store = tmp_path / "engine-credentials"
    engine_store.mkdir()
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", engine_store)
    return engine_store


def _serve(labinabox_checkout, need_login):
    api = Api(labinabox_checkout, "http://127.0.0.1:9/lab-builder/", "1.0.0", can_save=True,
              can_deploy=True, need_login=need_login)
    server = make_server(api, "127.0.0.1", 0, "0.0.0-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:{}/rodeo-builder/settings/".format(server.server_address[1])


@pytest.fixture()
def browser_page(stores, labinabox_checkout, monkeypatch):
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(real_home / ".cache" / "ms-playwright"))
    servers = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - any launch failure means no usable browser here
            if REQUIRED:
                raise
            pytest.skip("no Chromium for Playwright: {}".format(exc))
        errors: list[str] = []
        pages = []

        def open_page(need_login=False, logged_in=True, query=""):
            server, url = _serve(labinabox_checkout, need_login)
            servers.append(server)
            context = browser.new_context(viewport={"width": 1440, "height": 900},
                                          http_credentials=LOGIN if logged_in else None)
            pg = context.new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error"
                  and not msg.text.startswith("Failed to load resource") else None)
            pg.goto(url + query)
            pages.append(pg)
            return pg

        yield open_page
        browser.close()
        for server in servers:
            server.shutdown()
            server.server_close()
        assert errors == [], errors


def _values(page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('input, textarea')].map((i) => i.value)")


def test_add_list_and_delete_with_the_password(browser_page, tmp_path):
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text("harvester_token: t\nmy_regcode: r\n")
    page = browser_page()
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    assert page.locator(".cred-table thead th").all_inner_texts()[:6] == [
        "NAME", "APPLICATION", "ENGINE", "ENCRYPTED", "ADDED", "LAST USED"]
    sync_api.expect(page.locator("#fileSecretList .chip")).to_have_text(["harvester_token", "my_regcode"])
    assert page.locator("a.btn[href='/']").inner_text() == "Builder"

    page.click("#vaultAddBtn")
    page.select_option("#vaType", "scc")
    page.fill("#vaName", "my scc")
    page.fill("#vaf-regcode", "REG-SECRET-1")
    page.fill("#vaPass", "longpassword")
    page.fill("#vaPass2", "longpassword2")
    page.click("#vaultForm [type=submit]")
    sync_api.expect(page.locator("#vaResult")).to_contain_text("don't match")
    assert vault.list_items() == []
    page.fill("#vaPass2", "longpassword")
    page.click("#vaultForm [type=submit]")
    row = page.locator(".vault-item")
    sync_api.expect(row).to_contain_text("my scc")
    sync_api.expect(row).to_contain_text("SCC")
    sync_api.expect(row).to_contain_text("rodeo")
    stored = vault.list_items()
    assert [(i["name"], i["type"]) for i in stored] == [("my scc", "scc")]
    assert vault.open_item(stored[0]["id"], "longpassword") == {"regcode": "REG-SECRET-1", "email": ""}
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
    assert vault.list_items() == []
    assert not any(v in ("longpassword", "wrong-password") for v in _values(page))


def test_an_exposed_builder_lists_after_the_login_and_says_so_without_one(browser_page):
    page = browser_page(need_login=True)
    sync_api.expect(page.locator("#who")).to_have_text("logged in as alice")
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    page = browser_page(need_login=True, logged_in=False)
    sync_api.expect(page.locator("#loadError")).to_contain_text("Could not load the credentials: log in first")
    assert page.locator("#who").inner_text() == ""


def test_other_engines_credentials_are_listed_read_only(browser_page, stores):
    (stores / "aws-tmm.yaml").write_text("unencrypted: true\nAWS_ACCESS_KEY_ID: AKIA\nAWS_SECRET_ACCESS_KEY: s\n")
    page = browser_page()
    row = page.locator(".vault-item")
    sync_api.expect(row).to_contain_text("aws-tmm")
    sync_api.expect(row).to_contain_text("lab-in-a-box")
    sync_api.expect(row).to_contain_text("read-only here")
    sync_api.expect(row.locator("td")).to_have_text(["aws", "lab-in-a-box", "no", re.compile(r"\d"), "never", "read-only here"])
    assert row.locator("button").count() == 0


def test_add_parameter_opens_the_form_with_that_type(browser_page):
    page = browser_page(query="?add=scc")
    sync_api.expect(page.locator("#vaultForm")).to_be_visible()
    assert page.locator("#vaType").input_value() == "scc"
    assert page.locator("#vaFields label").first.text_content() == "Registration code"
    page = browser_page(query="?add=no-such-type")
    sync_api.expect(page.locator("#vaultList")).to_contain_text("No stored credentials yet.")
    assert page.locator("#vaultForm").is_hidden()
