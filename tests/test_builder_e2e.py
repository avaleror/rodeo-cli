"""Rodeo Builder end to end in a headless browser (Playwright + Chromium).

Builds the static page, serves it on 127.0.0.1 and drives it. Skipped where
Playwright or its Chromium is not installed, unless RB_E2E_REQUIRED is set
(CI's builder-e2e job), where that is a failure.
"""
from __future__ import annotations

import functools
import io
import os
import subprocess
import sys
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REQUIRED = bool(os.environ.get("RB_E2E_REQUIRED"))
if REQUIRED:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def site(tmp_path_factory, markdown_source):
    out = tmp_path_factory.mktemp("site") / "builder"
    subprocess.run([sys.executable, str(REPO / "scripts" / "build-builder-static.py"), "--output", str(out),
                    "--lab-builder-url", "http://127.0.0.1:9/lab-builder/", "--no-fetch-sources",
                    "--source", "virt-workshop=" + str(markdown_source)], check=True, capture_output=True)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/index.html".format(server.server_address[1])
    server.shutdown()


@pytest.fixture()
def page(site, monkeypatch):
    # conftest points HOME at a temp dir; Playwright finds its browsers under the
    # real user's ~/.cache/ms-playwright unless PLAYWRIGHT_BROWSERS_PATH says otherwise.
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
        context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 900})
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.on("console", lambda msg: errors.append(msg.text)
              if msg.type == "error" and "fonts.g" not in (msg.location or {}).get("url", "") else None)
        pg.route("https://fonts.googleapis.com/**", lambda route: route.abort())
        pg.goto(site)
        pg.wait_for_selector(".engine")
        yield pg
        browser.close()
        assert errors == [], errors


def _zip(page) -> dict[str, str]:
    with page.expect_download() as info:
        page.click("#downloadBtn")
    data = Path(info.value.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return {n: zf.read(n).decode() for n in zf.namelist()}


def test_loads_engines_library_and_previews(page):
    assert page.locator(".engine").count() == 4
    assert page.locator(".engine.on .engine-name").inner_text() == "rancher"
    assert "First ride" in page.locator("#library").inner_text()
    assert page.locator("#libCount").inner_text() == "3"
    assert "provides" in page.locator("#enginePanel").inner_text().lower()
    page.click("[data-tab=plan]")
    assert "name: my-rodeo" in page.locator("#planEditor").input_value()


def test_compose_native_rodeo_and_download(page):
    page.fill("#name", "Ride On")
    page.press("#name", "Tab")
    page.click(".card")                                   # Welcome to the rodeo
    assert page.locator(".row").count() == 1
    assert page.locator("#coverage").inner_text() == "lab covers every chapter"
    page.click("#variants .chip >> text=first-ride")
    files = _zip(page)
    assert set(files) == {"ride-on/rodeo-plan.yaml", "ride-on/story/01-welcome.md", "ride-on/story/chapters.yaml",
                          "ride-on/story/stories/first-ride.yaml", "ride-on/README.md"}
    plan = yaml.safe_load(files["ride-on/rodeo-plan.yaml"])
    assert plan["name"] == "ride-on" and plan["type"] == "rancher"
    assert plan["story"] == {"language": "en", "id": "first-ride"}
    assert yaml.safe_load(files["ride-on/story/stories/first-ride.yaml"]) == ["welcome.first-task"]
    assert "rodeo new ride-on --from rancher" in files["ride-on/README.md"]


def test_drag_and_drop_reorders(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Fleet GitOps")
    page.click("#ncNeeds .chip >> text=fleet")
    page.click("#newForm button[type=submit]")
    page.locator(".card", has_text="Welcome").drag_to(page.locator(".row").first)
    titles = page.locator(".row .row-title").all_inner_texts()
    assert titles == ["Welcome to the rodeo", "Fleet GitOps"]
    page.locator(".row").nth(1).focus()
    page.keyboard.press("Alt+ArrowUp")
    assert page.locator(".row .row-title").all_inner_texts() == ["Fleet GitOps", "Welcome to the rodeo"]


def test_labinabox_engine_adds_needed_addons_and_definition(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Manage clients")
    page.click("#ncNeeds .chip >> text=smlm")
    page.click("#ncCheck")
    page.click("#newForm button[type=submit]")
    page.click(".engine >> text=lab-in-a-box")
    assert "lab missing: smlm" in page.locator("#coverage").inner_text()
    page.click(".chip.need >> text=+ smlm")
    assert page.locator("#coverage").inner_text() == "lab covers every chapter"
    href = page.locator("#enginePanel a").get_attribute("href")
    assert href.endswith("/lab-builder/?addons=smlm")
    files = _zip(page)
    plan = yaml.safe_load(files["my-rodeo/rodeo-plan.yaml"])
    assert plan["type"] == "lab-in-a-box" and plan["lab_in_a_box"]["nodes"]["vm1"]["addons"] == ["smlm"]
    assert "my-rodeo/definition.yaml" in files and "my-rodeo/checks/check-manage-clients.sh" in files
    page.click(".seg button >> text=Import existing lab")
    page.click(".imports .engine >> text=smlm-workshop")
    files = _zip(page)
    assert yaml.safe_load(files["my-rodeo/rodeo-plan.yaml"])["lab_in_a_box"]["variant"] == "image"
    assert "my-rodeo/definition.yaml" not in files
    assert "rodeo new my-rodeo --from smlm-workshop" in files["my-rodeo/README.md"]


def test_story_editor_tags_a_selection(page):
    page.click(".card")
    page.click(".row-actions .btn >> text=spans")
    assert not page.locator("#editor").is_hidden()
    source = page.locator("#edSource")
    text = source.input_value()
    start = text.index("Rancher Prime on K3s")
    source.evaluate("(ta, [s, e]) => { ta.focus(); ta.setSelectionRange(s, e); }", [start, start + len("Rancher Prime")])
    page.click("[data-mark=invariant]")
    body = source.input_value()
    assert '<span no>Rancher Prime</span>' in body
    assert "0 warnings" in page.locator("#edSummary").inner_text()
    page.click("[data-edtab=stories]")
    assert "welcome.first-task" in page.locator("#storiesView").inner_text()
    page.click("#edDone")
    assert page.locator("#editor").is_hidden()


def test_theme_toggle(page):
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.click("#themeBtn")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    assert page.locator("#logo").get_attribute("src").endswith("horseshoe-mark-light.svg")


def test_upload_lab_json_brings_its_addons(page, tmp_path):
    lab = tmp_path / "lab.json"
    lab.write_text('{"common": {}, "nodes": {"a.lab": {"addons": ["smlm", {"client_registration": {}}]}}}')
    page.click(".engine >> text=lab-in-a-box")
    page.set_input_files("#enginePanel input[type=file]", str(lab))
    assert "client_registration" in page.locator("#enginePanel").inner_text()
    files = _zip(page)
    assert yaml.safe_load(files["my-rodeo/rodeo-plan.yaml"])["lab_in_a_box"]["nodes"]["vm1"]["addons"] == \
        ["client_registration", "smlm"]
    assert "client_registration" in files["my-rodeo/lab.json"]
    page.set_input_files("#enginePanel input[type=file]", files=[{"name": "bad.json", "mimeType": "application/json",
                                                                  "buffer": b"{}"}])
    assert "Not a lab-in-a-box lab.json" in page.locator("#toast").inner_text()


def test_selected_engine_is_marked(page):
    selected = page.locator(".engine.on")
    assert selected.count() == 1
    assert selected.locator(".engine-name").inner_text() == "rancher"
    assert selected.locator(".selected").inner_text().lower() == "selected"
    assert page.locator(".engine .selected").count() == 1
    assert "rancher" in page.locator("#engineNow").inner_text()
    page.click(".engine >> text=suse-virt")
    assert page.locator(".engine.on .engine-name").inner_text() == "suse-virt"
    assert page.locator(".engine.on").get_attribute("aria-checked") == "true"


def test_source_group_switches_engine_and_base(page):
    library = page.locator("#library").inner_text()
    assert "Virtualization workshop" in library and "Exercise 1: The Arrival" in library
    group = page.locator(".group", has_text="Virtualization workshop")
    group.get_by_role("button", name="use", exact=True).click()
    assert page.locator(".engine.on .engine-name").inner_text() == "suse-virt"
    assert "virt-workshop-aws" in page.locator("#engineNow").inner_text()
    assert group.get_by_role("button", name="use", exact=True).count() == 0
    group.locator(".card", has_text="The Arrival").click()
    assert page.locator("#coverage").inner_text() == "lab covers every chapter"
    files = _zip(page)
    assert "rodeo new my-rodeo --from virt-workshop-aws" in files["my-rodeo/README.md"]
    assert files["my-rodeo/checks/check-the-arrival.sh"] == "#!/bin/bash\necho ok\n"
    assert "**Time:** 30 min" in files["my-rodeo/story/01-the-arrival.md"]


def test_plan_yaml_can_be_edited(page):
    page.click("[data-tab=plan]")
    editor = page.locator("#planEditor")
    assert "generated" in page.locator("#planState").inner_text()
    editor.fill(editor.input_value() + "\n# hand edit\nextra_key: 42\n")
    assert "edited by hand" in page.locator("#planState").inner_text()
    assert not page.locator("#planReset").is_hidden()
    editor.fill(editor.input_value().replace("name: my-rodeo", "name: Edited Name"))
    page.click("[data-tab=rodeo]")
    assert page.locator("#name").input_value() == "edited-name"
    page.select_option("#target", "instruqt")
    files = _zip(page)
    plan = yaml.safe_load(files["edited-name/rodeo-plan.yaml"])
    assert plan["extra_key"] == 42 and plan["name"] == "edited-name" and plan["deployment_target"] == "instruqt"
    page.once("dialog", lambda d: d.dismiss())
    page.click(".engine >> text=suse-edge")
    assert page.locator(".engine.on .engine-name").inner_text() == "rancher"
    page.click("[data-tab=plan]")
    page.click("#planReset")
    assert "extra_key" not in page.locator("#planEditor").input_value()
    assert page.locator("#planReset").is_hidden()
