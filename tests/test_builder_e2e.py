"""Rodeo Builder end to end in a headless browser (Playwright + Chromium).

Builds the static page, serves it on 127.0.0.1 and drives it. Skipped where
Playwright or its Chromium is not installed, unless RB_E2E_REQUIRED is set
(CI's builder-e2e job), where that is a failure.
"""
from __future__ import annotations

import functools
import io
import os
import re
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
def site(tmp_path_factory, markdown_source, labinabox_checkout):
    out = tmp_path_factory.mktemp("site") / "builder"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "build-builder-static.py"), "--output", str(out),
                    "--lab-builder-url", "http://127.0.0.1:9/lab-builder/", "--no-fetch-sources",
                    "--labinabox", str(labinabox_checkout),
                    "--source", "virt-workshop=" + str(markdown_source)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/index.html".format(server.server_address[1])
    server.shutdown()


@pytest.fixture()
def live(labinabox_checkout, markdown_source):
    """The live `rodeo builder` server (save enabled) on a free port."""
    from rodeo.builder.api import Api
    from rodeo.builder.server import make_server

    api = Api(labinabox_checkout, "http://127.0.0.1:9/lab-builder/", "1.0.0",
              {"virt-workshop": markdown_source}, can_save=True)
    server = make_server(api, "127.0.0.1", 0, "0.0.0-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/".format(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture()
def page(request, monkeypatch):
    # conftest points HOME at a temp dir; Playwright finds its browsers under the
    # real user's ~/.cache/ms-playwright unless PLAYWRIGHT_BROWSERS_PATH says otherwise.
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(real_home / ".cache" / "ms-playwright"))
    url = request.getfixturevalue(getattr(request, "param", "site"))
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
        pg.goto(url)
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
                          "ride-on/story/stories/first-ride.yaml", "ride-on/README.md", "ride-on/builder.yaml"}
    assert yaml.safe_load(files["ride-on/builder.yaml"]) == {"base": "rancher"}
    plan = yaml.safe_load(files["ride-on/rodeo-plan.yaml"])
    assert plan["name"] == "ride-on" and plan["type"] == "rancher"
    assert plan["story"] == {"language": "en", "id": "first-ride"}
    assert yaml.safe_load(files["ride-on/story/stories/first-ride.yaml"]) == ["welcome.first-task"]
    assert "rodeo new ride-on --from-zip ride-on.zip" in files["ride-on/README.md"]
    assert page.locator("#saveBtn").is_hidden()


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
    assert yaml.safe_load(files["my-rodeo/builder.yaml"]) == {"base": "smlm-workshop"}


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
    page.click(".card")
    page.click(".engine >> text=lab-in-a-box")
    page.set_input_files("#enginePanel input[type=file]", str(lab))
    sync_api.expect(page.locator("#enginePanel")).to_contain_text("client_registration")
    files = _zip(page)
    assert yaml.safe_load(files["my-rodeo/rodeo-plan.yaml"])["lab_in_a_box"]["nodes"]["vm1"]["addons"] == \
        ["client_registration", "smlm"]
    assert "client_registration" in files["my-rodeo/lab.json"]
    page.set_input_files("#enginePanel input[type=file]", files=[{"name": "bad.json", "mimeType": "application/json",
                                                                  "buffer": b"{}"}])
    sync_api.expect(page.locator("#toast")).to_contain_text("Not a lab-in-a-box lab.json")


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
    assert yaml.safe_load(files["my-rodeo/builder.yaml"]) == {"base": "virt-workshop-aws"}
    assert files["my-rodeo/checks/check-the-arrival.sh"] == "#!/bin/bash\necho ok\n"
    assert "**Time:** 30 min" in files["my-rodeo/story/01-the-arrival.md"]


def test_plan_yaml_can_be_edited(page):
    page.click(".card")
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


def test_only_available_needs_are_offered_and_unknown_addons_refused(page):
    page.click("#newChapterBtn")
    offered = page.locator("#ncNeeds .chip").all_inner_texts()
    assert {"smlm", "client_registration", "pxe", "rke2", "rancher", "harvester"} <= set(offered)
    assert "argocd" not in offered and "neuvector" not in offered
    page.click("#ncCancel")
    page.click(".engine >> text=lab-in-a-box")
    page.fill("#enginePanel .addon-add input", "argocd")
    page.click("#enginePanel .addon-add button")
    assert "argocd is not a lab-in-a-box add-on" in page.locator("#toast").inner_text()
    assert page.locator("#enginePanel").get_by_text("Kubernetes clusters (in the lab-builder)").count() == 1


@pytest.fixture()
def bare_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("bare") / "builder"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "build-builder-static.py"), "--output", str(out),
                        "--no-fetch-sources", "--no-labinabox"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/index.html".format(server.server_address[1])
    server.shutdown()


@pytest.mark.parametrize("page", ["bare_site"], indirect=True)
def test_missing_catalogue_is_shown_not_hidden(page):
    page.click(".engine >> text=lab-in-a-box")
    alert = page.locator("#enginePanel .alert").inner_text()
    assert "catalogue unavailable" in alert and "--labinabox" in alert


@pytest.mark.parametrize("page", ["live"], indirect=True)
def test_live_server_saves_into_profiles(page):
    from rodeo.labseed import custom_profile_dir

    save = page.locator("#saveBtn")
    assert save.is_visible()
    page.fill("#name", "Saved Ride")
    page.press("#name", "Tab")
    page.click(".card >> text=Welcome to the rodeo")
    save.click()
    sync_api.expect(page.locator("#toastText")).to_have_text(re.compile("^Saved"))
    dest = custom_profile_dir("saved-ride")
    assert (dest / "definition.yaml").is_file() and (dest / "story" / "01-welcome.md").is_file()
    assert yaml.safe_load((dest / "rodeo-plan.yaml").read_text())["name"] == "saved-ride"
    assert "rodeo up --profile saved-ride" in page.locator("#toast").inner_text()
    page.evaluate("document.getElementById('toastText').textContent = ''")
    page.once("dialog", lambda d: d.accept())
    save.click()
    sync_api.expect(page.locator("#toastText")).to_have_text(re.compile("^Saved"))


def test_missing_addon_marks_work_needed_without_blocking(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Future feature")
    page.click("#ncNeeds .chip >> text=missing_addon")
    page.click("#newForm button[type=submit]")
    assert page.locator(".row .chip.todo").inner_text().lower() == "work needed"
    assert page.locator("#coverage").inner_text() == "lab covers every chapter · work needed in 1 chapter"
    page.click(".engine >> text=lab-in-a-box")
    assert "missing_addon" not in page.locator("#enginePanel").inner_text()
    files = _zip(page)
    assert "missing_addon" not in files["my-rodeo/rodeo-plan.yaml"]
    assert "## Work needed" in files["my-rodeo/README.md"] and "Future feature" in files["my-rodeo/README.md"]
    assert "needs: [missing_addon]" in files["my-rodeo/story/chapters.yaml"]


def _new_chapter(page, title, append=True):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", title)
    if not append:
        page.click("#ncAppend")
    page.click("#newForm button[type=submit]")


def _rows(page) -> list[str]:
    return page.locator(".row .row-title").all_inner_texts()


def test_editor_errors_show_above_the_editor(page):
    page.click(".card")
    page.click(".row-actions .btn >> text=spans")
    page.click("[data-mark=invariant]")
    toast = page.locator("#toast")
    sync_api.expect(toast).to_be_visible()
    assert toast.inner_text().startswith("Select text in the source first")
    box = toast.bounding_box()
    on_top = page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('#toast')",
                           [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
    assert on_top
    page.keyboard.press("Escape")
    assert page.locator("#editor").is_hidden()


def test_draft_is_offered_after_a_reload(page):
    page.fill("#name", "Draft Ride")
    page.press("#name", "Tab")
    page.click(".card")
    page.wait_for_timeout(900)
    page.reload()
    page.wait_for_selector(".engine")
    banner = page.locator("#draftBanner")
    sync_api.expect(banner).to_be_visible()
    assert '1 chapters in "draft-ride"' in banner.inner_text()
    assert page.locator(".row").count() == 0
    page.click("#draftRestore")
    assert banner.is_hidden() and page.locator(".row").count() == 1
    assert page.locator("#name").input_value() == "draft-ride"
    page.reload()
    page.wait_for_selector(".engine")
    page.click("#draftFresh")
    assert page.locator("#draftBanner").is_hidden() and page.locator(".row").count() == 0


def test_keyboard_tagging_and_tag_chip(page):
    page.click(".card")
    page.click(".row-actions .btn >> text=spans")
    source = page.locator("#edSource")
    start = source.input_value().index("Rancher Prime on K3s")
    source.evaluate("(ta, [s, e]) => { ta.focus(); ta.setSelectionRange(s, e); }", [start, start + len("Rancher Prime")])
    page.keyboard.press("Alt+KeyN")
    assert re.search(r'<span [^>]*>Rancher Prime</span>', source.input_value())
    assert "Alt+T / I / N tags" in page.locator("#edSel").inner_text()
    source.evaluate("(ta) => { ta.focus(); ta.setSelectionRange(0, 0); }")
    page.keyboard.press("Alt+KeyT")
    assert "Select some text first" in page.locator("#toastText").inner_text()
    at = source.input_value().index("K3s")
    source.evaluate("(ta, [s, e]) => { ta.setSelectionRange(s, e); ta.dispatchEvent(new MouseEvent('mouseup', "
                    "{ bubbles: true, clientX: 200, clientY: 200 })); }", [at, at + 3])
    chip = page.locator("#popover >> text=Tag selection")
    sync_api.expect(chip).to_be_visible()
    chip.click()
    assert page.locator("#popover h4").inner_text() == "Apply span"
    page.click("#popover .x-btn")
    assert page.locator("#popover").is_hidden()


def test_row_cursor_sets_the_insert_position(page):
    _new_chapter(page, "Alpha")
    _new_chapter(page, "Beta")
    _new_chapter(page, "Gamma", append=False)
    assert _rows(page) == ["Alpha", "Beta"]
    page.locator(".row .row-title >> text=Alpha").click()
    assert "Inserting after 01" in page.locator("#insertHint").inner_text()
    page.click(".card >> text=Gamma")
    assert _rows(page) == ["Alpha", "Gamma", "Beta"]
    assert page.locator(".row.cursor .row-title").inner_text() == "Gamma"


def test_remove_offers_undo_and_library_cards_toggle(page):
    card = page.locator(".card", has_text="Welcome to the rodeo")
    card.click()
    assert "added ✓" in card.inner_text()
    card.click()
    assert page.locator(".row").count() == 0
    page.click("#toastUndo")
    assert page.locator(".row").count() == 1
    page.click(".row-actions [aria-label^=Remove]")
    assert page.locator(".row").count() == 0
    page.click("#toastUndo")
    assert _rows(page) == ["Welcome to the rodeo"]


def test_review_lists_the_zip_and_warnings(page):
    page.click("#downloadBtn")
    assert page.locator("#toastText").inner_text() == "Add at least one chapter first."
    page.click(".card >> text=Welcome to the rodeo")
    _new_chapter(page, "Broken")
    page.click(".row-actions .btn >> nth=4")
    page.locator("#edSource").fill('# Broken\n\n<span lang="en">needs an id</span>\n')
    page.click("#edDone")
    assert "1 ⚠" in page.locator(".row", has_text="Broken").locator(".btn.warn").inner_text()
    assert page.locator("#warnBtn").inner_text().lower() == "1 ⚠ warning"
    review = page.locator("#reviewPanel")
    assert "1 span warning (same checks as rmstory validate)" in review.inner_text()
    listed = set(review.locator(".zip-files li").all_inner_texts())
    assert "rodeo new my-rodeo --from-zip my-rodeo.zip" in review.locator("pre").inner_text()
    files = _zip(page)
    assert listed == set(files)
    review.locator(".warn-row .btn", has_text="Fix").click()
    assert page.locator("#edChapter").inner_text() == "Broken"


def test_engine_banner_offers_the_workshops_engine(page):
    page.locator(".group", has_text="Virtualization workshop").locator(".card").first.click()
    banner = page.locator("#engineBanner")
    sync_api.expect(banner).to_be_visible()
    assert "run on suse-virt, not rancher" in banner.inner_text()
    banner.locator("button").click()
    assert page.locator(".engine.on .engine-name").inner_text() == "suse-virt"
    assert banner.is_hidden()


def test_engine_cards_move_with_arrow_keys(page):
    page.locator(".engine.on").focus()
    page.keyboard.press("ArrowRight")
    focused = page.evaluate("document.activeElement.querySelector('.engine-name').textContent")
    assert focused != "rancher"
    page.keyboard.press("Enter")
    assert page.locator(".engine.on .engine-name").inner_text() == focused
    assert page.locator("[data-tab=rodeo]").get_attribute("aria-selected") == "true"


@pytest.mark.parametrize("width", [1000, 600])
def test_narrow_layout_has_a_sticky_bar(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.click(".card")
    bar = page.locator("#stickyBar")
    sync_api.expect(bar).to_be_visible()
    assert "1 chapters" in bar.inner_text()
    assert page.locator("#chapterList").bounding_box()["height"] > 40
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
