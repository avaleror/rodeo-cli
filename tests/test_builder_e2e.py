"""Rodeo Builder end to end in a headless browser (Playwright + Chromium).

Builds the static page, serves it on 127.0.0.1 and drives it. Skipped where
Playwright or its Chromium is not installed, unless RB_E2E_REQUIRED is set
(CI's builder-e2e job), where that is a failure.
"""
from __future__ import annotations

import functools
import json
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
def live_local_lab_builder(labinabox_checkout, markdown_source):
    """The live server linking to the lab-builder it started itself (``http://{host}:<port>/``)."""
    from rodeo.builder.api import Api
    from rodeo.builder.server import make_server

    api = Api(labinabox_checkout, "http://{host}:9/lab-builder/", "1.0.0", {"virt-workshop": markdown_source}, can_save=True)
    server = make_server(api, "127.0.0.1", 0, "0.0.0-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/".format(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture()
def live_deploy(labinabox_checkout, markdown_source, tmp_path, monkeypatch):
    """The live server allowed to deploy, with a fake rodeo that runs three phases."""
    from rodeo.builder import deploywork
    from rodeo.builder.api import Api
    from rodeo.builder.server import make_server

    fake = tmp_path / "fake-rodeo"
    fake.write_text("#!{}\nimport sys, time\nprint('rodeo', *sys.argv[1:], flush=True)\n"
                    "for i in range(3):\n    print('phase', i, flush=True)\n    time.sleep(0.3)\n".format(sys.executable))
    fake.chmod(0o755)
    monkeypatch.setattr(deploywork, "find_rodeo_bin", lambda: str(fake))
    monkeypatch.setattr(deploywork, "capability", lambda run=None: {"available": True, "reason": "", "as": "root"})
    api = Api(labinabox_checkout, "http://127.0.0.1:9/lab-builder/", "1.0.0", {"virt-workshop": markdown_source},
              can_save=True, can_deploy=True)
    server = make_server(api, "127.0.0.1", 0, "0.0.0-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/".format(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture()
def live_github(labinabox_checkout, markdown_source):
    """The live server listening on loopback only, so Publish uses the host's GitHub login."""
    from rodeo.builder.api import Api
    from rodeo.builder.server import make_server

    api = Api(labinabox_checkout, "http://127.0.0.1:9/lab-builder/", "1.0.0",
              {"virt-workshop": markdown_source}, can_save=True, can_publish=True)
    server = make_server(api, "127.0.0.1", 0, "0.0.0-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/".format(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture()
def live_rmstory(fake_rmstory, live):
    """The live server with a fake rmstory on its PATH (engines deepl and ollama)."""
    return live


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
        pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector(".engine")
        _open_library(pg)
        yield pg
        browser.close()
        assert errors == [], errors


def _open_library(page) -> None:
    page.wait_for_selector(".group-head")
    while page.locator(".group-head[aria-expanded=false]").count():
        page.locator(".group-head[aria-expanded=false]").first.click()


def _add_need(page, text: str) -> None:
    page.fill("#ncNeedInput", text)
    page.press("#ncNeedInput", "Enter")


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
    _add_need(page, "fleet")
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
    _add_need(page, "smlm")
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
    button = page.locator(".row-actions .btn").first
    assert button.inner_text().lower() == "edit" and "spans" in button.get_attribute("title")
    button.click()
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


def test_story_editor_undo_and_cancel(page):
    page.click(".card")
    page.locator(".row-actions .btn").first.click()
    title = page.locator(".lib-title").bounding_box()
    assert page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('#editor')",
                         [title["x"] + 10, title["y"] + 10])
    source = page.locator("#edSource")
    original = source.input_value()
    start = original.index("Rancher Prime on K3s")
    source.evaluate("(ta, [s, e]) => { ta.focus(); ta.setSelectionRange(s, e); }", [start, start + len("Rancher Prime")])
    page.click("[data-mark=invariant]")
    tagged = source.input_value()
    assert "<span no>Rancher Prime</span>" in tagged
    source.focus()
    page.keyboard.press("Control+z")
    assert source.input_value() == original
    page.keyboard.press("Control+Shift+z")
    assert source.input_value() == tagged
    source.evaluate("ta => ta.setSelectionRange(ta.value.length, ta.value.length)")
    page.keyboard.type("more")
    assert source.input_value() == tagged + "more"
    page.keyboard.press("Control+z")
    assert source.input_value() == tagged
    page.once("dialog", lambda d: d.accept())
    page.click("#edCancel")
    assert page.locator("#editor").is_hidden()
    page.locator(".row-actions .btn").first.click()
    assert source.input_value() == original


def _edit_body(page, body: str):
    page.click(".card")
    page.locator(".row-actions .btn").first.click()
    source = page.locator("#edSource")
    source.fill(body)
    return source


_SELECT_IN_PREVIEW = """([sel, whole]) => {
  const node = document.querySelector('#edPreview ' + sel), r = document.createRange();
  if (whole) r.selectNodeContents(node);
  else { const t = node.querySelector('.seg').firstChild; r.setStart(t, 0); r.setEnd(t, t.length); }
  getSelection().removeAllRanges();
  getSelection().addRange(r);
  document.querySelector('#edPreview').dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: 200, clientY: 200 }));
}"""


def test_story_preview_renders_markdown(page):
    _edit_body(page, "---\nslug: demo\n---\n# Big title\n\nSome *soft* and **strong** text with `kubectl get pods`, "
                     '<span lang="en" id="demo.tagged">a tagged span</span>.\n\n- first item\n- second item\n\n'
                     "1. one\n2. two\n\n> quoted\n\n```bash\necho hi\n```\n\n---\n\n"
                     "[docs](https://example.com) ![logo](https://example.com/x.png) [bad](javascript:alert(1))\n")
    pv = page.locator("#edPreview")
    assert pv.get_attribute("data-format") == "markdown"
    assert "slug: demo" in pv.locator("pre.fm").inner_text()
    assert pv.locator("h1").inner_text() == "Big title"
    assert pv.locator("p em").inner_text() == "soft"
    assert pv.locator("p strong").inner_text() == "strong"
    assert pv.locator("p > code").inner_text() == "kubectl get pods"
    assert pv.locator("ul > li").all_inner_texts() == ["first item", "second item"]
    assert pv.locator("ol > li").all_inner_texts() == ["one", "two"]
    assert pv.locator("blockquote").inner_text() == "quoted"
    assert pv.locator("pre code").inner_text() == "echo hi"
    assert pv.locator("hr").count() == 1
    span = pv.locator(".sp.k-translatable")
    assert span.locator(".badge").inner_text() == "demo.tagged" and "a tagged span" in span.inner_text()
    link = pv.locator("a", has_text="docs")
    assert link.get_attribute("href") == "https://example.com"
    assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener"
    assert pv.locator("img").count() == 0
    assert pv.locator("a.md-img").get_attribute("href") == "https://example.com/x.png"
    assert pv.locator("a", has_text="bad").get_attribute("href") is None
    text = pv.inner_text()
    assert "**" not in text and "# Big" not in text and "`" not in text and "<span" not in text


def test_story_preview_renders_only_safe_html(page):
    _edit_body(page, '<h2>Hello</h2>\n<p onclick="window.pwned=1">Para <b>bold</b> <span no>inv</span> &amp; '
                     '<a href="https://example.com" onclick="window.pwned=2">link</a> '
                     '<a href="javascript:window.pwned=3">bad</a></p>\n<script>window.pwned=4</script>'
                     '<iframe src="https://example.com"></iframe><style>body { display: none }</style>'
                     '<img src="https://example.com/x.png" alt="pic" onerror="window.pwned=5">\n'
                     "<ul><li>one<li>two</ul>")
    pv = page.locator("#edPreview")
    assert pv.get_attribute("data-format") == "html"
    assert pv.locator("h2").inner_text() == "Hello"
    assert pv.locator("p b").inner_text() == "bold"
    assert pv.locator("p .sp.k-invariant").inner_text() == "inv"
    assert "Para bold inv & link bad" in pv.locator("p").inner_text()
    assert pv.locator("ul > li").all_inner_texts() == ["one", "two"]
    assert pv.locator("script, style, iframe, img, [onclick], [onerror]").count() == 0
    assert "pwned" not in pv.inner_text()
    link = pv.locator("a", has_text="link")
    assert link.get_attribute("href") == "https://example.com" and link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener"
    assert pv.locator("a", has_text="bad").get_attribute("href") is None
    assert pv.locator("a.md-img").inner_text() == "pic"
    pv.locator("p b").click()
    assert page.evaluate("window.pwned") is None


def test_story_preview_selection_tags_the_source(page):
    source = _edit_body(page, "## Heading here\n\nSome **bold words** and `code`.\n")
    page.evaluate(_SELECT_IN_PREVIEW, ["strong", False])
    page.click("#popover >> text=Tag selection")
    page.click("#popover >> text=Invariant")
    assert source.input_value() == "## Heading here\n\nSome **<span no>bold words</span>** and `code`.\n"
    assert page.locator("#edPreview strong .sp.k-invariant").inner_text() == "bold words"
    page.evaluate(_SELECT_IN_PREVIEW, ["h2", True])
    page.click("#popover >> text=Tag selection")
    page.click("#popover >> text=Translatable")
    assert re.match(r'## <span lang="en" id="[^"]+">Heading here</span>\n', source.input_value())
    page.evaluate(_SELECT_IN_PREVIEW, ["p", True])
    page.click("#popover >> text=Tag selection")
    page.click("#popover >> text=No-lang")
    assert re.search(r'\n\n<span lang="nolang"[^>]*>Some \*\*<span no>bold words</span>\*\* and `code`\.</span>\n$',
                     source.input_value())
    source.fill("Setext title\n=====\n\n> [!NOTE]\n> Callout body.\n")
    pv = page.locator("#edPreview")
    assert pv.locator("h1").inner_text() == "Setext title"
    callout = pv.locator("blockquote.callout.callout-note")
    assert callout.locator(".callout-title").text_content() == "Note"
    assert "[!NOTE]" not in pv.inner_text() and "===" not in pv.inner_text()
    page.evaluate(_SELECT_IN_PREVIEW, ["h1", True])
    page.click("#popover >> text=Tag selection")
    page.click("#popover >> text=Translatable")
    assert re.match(r'<span lang="en" id="[^"]+">Setext title</span>\n=====\n', source.input_value())
    page.evaluate(_SELECT_IN_PREVIEW, ["blockquote", True])
    page.click("#popover >> text=Tag selection")
    page.click("#popover >> text=Invariant")
    assert source.input_value().endswith("\n\n> [!NOTE]\n> <span no>Callout body.</span>\n")
    assert pv.locator("blockquote.callout .sp.k-invariant").inner_text() == "Callout body."


def test_story_editor_panes_scroll_together(page):
    page.set_viewport_size({"width": 1440, "height": 700})
    page.click(".card")
    page.locator(".row-actions .btn").first.click()
    source, preview = page.locator("#edSource"), page.locator("#edPreview")
    source.evaluate("ta => { ta.value += '\\n\\nMore text.'.repeat(80); ta.dispatchEvent(new Event('input')); }")
    source.evaluate("ta => { ta.scrollTop = ta.scrollHeight; }")
    page.wait_for_function("() => { const p = document.querySelector('#edPreview'); return p.scrollTop > 0 && "
                           "p.scrollTop >= p.scrollHeight - p.clientHeight - 2; }")
    box = source.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 700
    preview.evaluate("p => { p.scrollTop = 0; }")
    page.wait_for_function("() => document.querySelector('#edSource').scrollTop <= 1")


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
    assert page.locator(".engine[aria-checked=true]").count() == 1
    assert selected.locator(".engine-title").inner_text() == "Rancher Prime" and "VMs" not in selected.inner_text()
    assert "rancher" in page.locator("#engineNow").inner_text()
    page.click(".engine >> text=suse-virt")
    assert page.locator(".engine.on .engine-name").inner_text() == "suse-virt"
    assert page.locator(".engine.on").get_attribute("aria-checked") == "true"


def test_source_group_switches_engine_and_base(page):
    page.click(".card >> text=Welcome to the rodeo")
    group = page.locator(".group", has_text="Virtualization workshop")
    group.get_by_role("button", name="use", exact=True).click()
    assert page.locator(".engine.on .engine-name").inner_text() == "suse-virt"
    assert "virt-workshop-aws" in page.locator("#engineNow").inner_text()
    assert _rows(page)[0] == "Exercise 1: The Arrival" and "Welcome to the rodeo" not in _rows(page)
    assert len(_rows(page)) == group.locator(".card").count()
    page.click("#toastUndo")
    assert _rows(page) == ["Welcome to the rodeo"]
    assert page.locator(".engine.on .engine-name").inner_text() == "rancher"
    group.get_by_role("button", name="use", exact=True).click()
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
    page.focus("#ncNeedInput")
    offered = page.locator("#ncNeedList li").all_inner_texts()
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


def test_new_chapter_needs_autocomplete_and_new_tags(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Quantum lab")
    page.fill("#ncNeedInput", "flee")
    assert page.locator("#ncNeedList li").all_inner_texts()[0] == "fleet"
    page.press("#ncNeedInput", "Enter")
    page.fill("#ncNeedInput", "Quantum Lab")
    options = page.locator("#ncNeedList li").all_inner_texts()
    assert options == ["Add \u201cquantum_lab\u201d as a new need"]
    page.press("#ncNeedInput", "Enter")
    assert [c.split()[0] for c in page.locator("#ncNeeds .chip").all_inner_texts()] == ["fleet", "quantum_lab"]
    assert "todo" in page.locator("#ncNeeds .chip", has_text="quantum_lab").get_attribute("class")
    page.focus("#ncNeedInput")
    page.keyboard.press("Backspace")
    page.keyboard.press("Backspace")
    assert page.locator("#ncNeeds .chip").count() == 0
    _add_need(page, "fleet")
    _add_need(page, "quantum lab")
    page.keyboard.press("Escape")
    assert page.locator("#ncNeedList").is_hidden() and page.locator("#newModal").is_visible()
    page.click("#newForm button[type=submit]")
    assert page.locator(".row .chip.todo").inner_text() == "quantum_lab"
    assert page.locator("#coverage").inner_text() == "lab covers every chapter · work needed in 1 chapter"
    readme = _zip(page)["my-rodeo/README.md"]
    assert "## Work needed" in readme and "- Quantum lab (`quantum-lab`): `quantum_lab`" in readme


def test_missing_addon_marks_work_needed_without_blocking(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Future feature")
    _add_need(page, "missing_addon")
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
    page.click(".row-actions .btn >> text=Edit")
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
    page.click(".row-actions .btn >> text=Edit")
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


def test_library_info_filters_and_details(page):
    page.reload()
    page.wait_for_selector(".group-head")
    virt = page.locator(".group", has_text="Virtualization workshop")
    assert page.locator(".group", has_text="First ride").locator(".card").count() == 0
    virt.get_by_role("button", name="use", exact=True).click()
    assert virt.locator(".card").count() == 0 and page.locator(".row").count() > 0
    page.click("#toastUndo")
    assert page.locator(".row").count() == 0
    virt.locator(".info-btn").click()
    info = virt.locator(".group-info")
    sync_api.expect(info).to_be_visible()
    assert "suse-virt" in info.inner_text() and "virt-workshop-aws" in info.inner_text()
    chip = info.locator(".chip.missing", has_text="kubevirt")
    assert chip.inner_text() == "kubevirt" and "rancher lab engine doesn't provide kubevirt" in chip.get_attribute("title")
    page.keyboard.press("Escape")
    assert page.locator(".group-info.open").count() == 0
    page.mouse.move(700, 500)
    sync_api.expect(info).to_be_hidden()
    page.locator(".group", has_text="First ride").locator(".group-head").click()
    card = page.locator(".card", has_text="Welcome to the rodeo")
    assert card.locator(".card-meta").count() == 0 and "10 min" in card.get_attribute("title")
    page.click("#detailsBtn")
    assert "10 min" in card.locator(".card-meta").inner_text()
    page.click("#filterBtn")
    page.check("#filterMenu input[value=engine]")
    sync_api.expect(virt).to_have_count(0)
    assert page.locator("#filterCount").inner_text() == "1"
    page.uncheck("#filterMenu input[value=engine]")
    page.check("#filterMenu input[value=added]")
    page.keyboard.press("Escape")
    assert page.locator("#filterMenu").is_hidden()
    card.click()
    sync_api.expect(card).to_have_count(0)
    assert page.locator(".row").count() == 1


def test_remove_offers_undo_and_library_cards_toggle(page):
    card = page.locator(".card", has_text="Welcome to the rodeo")
    card.click()
    assert "added" in card.get_attribute("class") and "click to remove" in card.get_attribute("title")
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


def test_engine_info_and_resizable_columns(page):
    card = page.locator(".engine-card", has=page.locator(".engine-name", has_text="suse-virt"))
    card.locator(".info-btn").click()
    info = card.locator(".engine-info")
    sync_api.expect(info).to_be_visible()
    assert "4 VMs" in info.inner_text() and "longhorn" in info.inner_text()
    assert page.locator(".engine.on .engine-name").inner_text() == "rancher"
    page.keyboard.press("Escape")
    page.mouse.move(700, 800)
    sync_api.expect(info).to_be_hidden()
    left = page.locator(".col.left")
    width = left.bounding_box()["width"]
    sep = page.locator("#resizeLeft").bounding_box()
    page.mouse.move(sep["x"] + 1, 400)
    page.mouse.down()
    page.mouse.move(sep["x"] + 101, 400, steps=5)
    page.mouse.up()
    assert abs(left.bounding_box()["width"] - (width + 100)) <= 2
    page.locator("#resizeRight").focus()
    right = page.locator(".col.right").bounding_box()["width"]
    page.keyboard.press("ArrowLeft")
    assert page.locator(".col.right").bounding_box()["width"] == right + 16
    page.reload()
    page.wait_for_selector(".engine")
    assert abs(page.locator(".col.left").bounding_box()["width"] - (width + 100)) <= 2
    sep = page.locator("#resizeLeft").bounding_box()
    page.mouse.dblclick(sep["x"] + 1, 400)
    assert abs(page.locator(".col.left").bounding_box()["width"] - width) <= 2


@pytest.mark.parametrize("page", ["site", "live"], indirect=True)
def test_header_fits_a_phone(page):
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    brand = page.locator(".brand").bounding_box()
    buttons = [b.bounding_box() for b in page.locator(".hdr-right .btn").all() if b.is_visible()]
    assert buttons and all(brand["x"] + brand["width"] <= b["x"] for b in buttons)
    assert all(b["x"] + b["width"] <= 390 for b in buttons)
    sync_api.expect(page.locator("#stickyDownload")).to_be_visible()


@pytest.mark.parametrize("width", [1000, 600])
def test_narrow_layout_has_a_sticky_bar(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.click(".card")
    bar = page.locator("#stickyBar")
    sync_api.expect(bar).to_be_visible()
    assert "1 chapters" in bar.inner_text()
    assert page.locator("#chapterList").bounding_box()["height"] > 40
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def _open_translations(page):
    page.click(".card >> text=Welcome to the rodeo")
    page.click(".row-actions .btn >> text=Edit")
    page.click("[data-edtab=translations]")


def test_static_translations_are_written_by_hand(page):
    _open_translations(page)
    view = page.locator("#translationsView")
    assert page.locator("#trRun").count() == 0 and "rodeo builder" in view.inner_text()
    assert view.locator("thead th").all_inner_texts() == ["id", "de", "es", "fr"]
    page.locator("tr", has_text="welcome.login").locator(".tr-cell").first.click()
    assert '<span id="welcome.rancher-url"/>' in page.locator(".tr-src").inner_text()
    page.fill("#trText", "Öffne den Browser")
    page.click(".tr-actions .btn >> text=Save")
    sync_api.expect(page.locator("#toastText")).to_contain_text('Keep <span id="welcome.rancher-url"/>')
    page.locator("tr", has_text="welcome.intro").locator(".tr-cell").first.click()
    page.fill("#trText", "Aufsitzen.")
    page.click(".tr-actions .btn >> text=Save")
    sync_api.expect(page.locator("tr", has_text="welcome.intro").locator(".tr-cell.st-reviewed")).to_have_count(1)
    page.click("#edDone")
    with page.expect_download() as dl:
        page.click("#downloadBtn")
    with zipfile.ZipFile(dl.value.path()) as zf:
        row = json.loads(zf.read("my-rodeo/story/strings/de/welcome.intro/@default/content.json"))
        assert "Translations in `story/strings/`: `de`" in zf.read("my-rodeo/README.md").decode()
    assert row["content"] == "Aufsitzen." and row["status"] == "reviewed" and row["source_checksum"] is None


@pytest.mark.parametrize("page", ["live_rmstory"], indirect=True)
def test_live_translate_fills_the_missing_rows(page):
    _open_translations(page)
    page.select_option("#translationsView select[aria-label='Language to fill']", "es")
    page.click("#trRun")
    sync_api.expect(page.locator("#toastText")).to_contain_text("es translations filled as draft with deepl")
    drafts = page.locator("#translationsView .tr-cell.st-draft")
    assert drafts.count() == 2
    drafts.first.click()
    assert page.locator("#trText").input_value().startswith("[es] ")


@pytest.mark.parametrize("page", ["live_rmstory"], indirect=True)
def test_live_review_checks_with_rmstory(page):
    page.click(".card >> text=Welcome to the rodeo")
    page.click("#rmCheckBtn")
    sync_api.expect(page.locator(".rm-check")).to_contain_text("tagged span(s)")


@pytest.mark.parametrize("page", ["live_rmstory"], indirect=True)
def test_live_new_chapter_is_drafted_with_an_engine(page):
    page.click("#newChapterBtn")
    assert page.locator("#ncDraft").is_visible()
    assert page.locator("#ncEngine option").all_inner_texts() == ["ollama"]
    page.fill("#ncTitle", "Open the UI")
    page.fill("#ncBrief", "Log in to Rancher")
    page.click("#ncDraftBtn")
    sync_api.expect(page.locator("#toastText")).to_have_text(re.compile("^Draft ready"))
    body = page.locator("#ncBody").input_value()
    assert body.startswith("# Open the UI") and '<span lang="en" id="new-chapter.1">Log in to Rancher</span>' in body
    page.click("#newForm button[type=submit]")
    sync_api.expect(page.locator("#chapterList")).to_contain_text("Open the UI")


def test_static_new_chapter_has_no_draft(page):
    page.click("#newChapterBtn")
    assert page.locator("#ncDraft").is_hidden()


def _route_github(page, calls):
    files = {"labs/01-intro/assignment.md": "---\ntitle: Intro to the lab\n---\n\nRead this.\n",
             "labs/01-intro/check-workstation": "#!/bin/sh\nexit 0\n",
             "labs/02-deploy/assignment.md": "---\ntitle: Deploy\n---\n\nDeploy it.\n", "README.md": "# Acme\n"}

    def handle(route):
        url = route.request.url
        calls.append(url)
        if url == "https://api.github.com/repos/acme/labs":
            return route.fulfill(json={"default_branch": "main"})
        if url == "https://api.github.com/repos/acme/labs/git/trees/main?recursive=1":
            return route.fulfill(json={"tree": [{"path": p, "type": "blob"} for p in files], "truncated": False})
        prefix = "https://raw.githubusercontent.com/acme/labs/main/"
        if url.startswith(prefix) and url[len(prefix):] in files:
            return route.fulfill(body=files[url[len(prefix):]])
        return route.fulfill(status=404, body="{}")

    page.route(re.compile(r"^https://(api\.github\.com|raw\.githubusercontent\.com)/.*"), handle)


def test_static_imports_a_github_workshop(page):
    calls = []
    _route_github(page, calls)
    page.click("#importBtn")
    assert "github.com" in page.locator("#imNote").inner_text()
    page.fill("#imUrl", "https://github.com/acme/labs/tree/main/labs")
    page.click("#imSubmit")
    sync_api.expect(page.locator("#toastText")).to_have_text("Imported 2 chapters (instruqt) from labs")
    assert page.locator("#importModal").is_hidden()
    group = page.locator(".group", has_text="github.com/acme/labs/tree/main/labs")
    assert group.locator(".card-title").all_inner_texts() == ["Intro to the lab", "Deploy"]
    group.locator(".card", has_text="Intro to the lab").click()
    sync_api.expect(page.locator("#chapterList")).to_contain_text("Intro to the lab")
    assert not any("api.github.com/repos/acme/labs/contents" in c for c in calls)
    group.locator(".group-actions .btn", has_text="×").click()
    sync_api.expect(page.locator(".group", has_text="acme/labs")).to_have_count(0)
    page.click("#toastUndo")
    sync_api.expect(page.locator(".group", has_text="acme/labs")).to_have_count(1)


def test_static_import_says_why_it_failed(page):
    page.click("#importBtn")
    page.fill("#imUrl", "https://gitlab.com/acme/labs")
    page.click("#imSubmit")
    sync_api.expect(page.locator("#toastText")).to_contain_text("imports from github.com only")
    assert page.locator("#importModal").is_visible()


@pytest.mark.parametrize("page", ["live"], indirect=True)
def test_live_imports_through_the_server(page, monkeypatch, tmp_path):
    from rodeo.builder import imports

    (tmp_path / "ws" / "story").mkdir(parents=True)
    (tmp_path / "ws" / "story" / "01-hello.md").write_text('# Hello there\n<span lang="en" id="hello.1">Hi</span>\n')
    seen = []

    def fake(url):
        seen.append(url)
        return imports.read_group({"repo": "https://git.example.com/team/ws", "ref": "", "path": "ws"}, tmp_path)

    monkeypatch.setattr(imports, "import_source", fake)
    page.click("#importBtn")
    assert "any" in page.locator("#imNote").inner_text() or "public https" in page.locator("#imNote").inner_text()
    page.fill("#imUrl", "https://git.example.com/team/ws")
    page.click("#imSubmit")
    sync_api.expect(page.locator("#toastText")).to_have_text("Imported 1 chapter (rmstory) from ws")
    assert seen == ["https://git.example.com/team/ws"]
    sync_api.expect(page.locator(".group", has_text="git.example.com/team/ws")).to_contain_text("Hello there")


@pytest.mark.parametrize("page", ["live_rmstory"], indirect=True)
def test_live_new_story_retells_the_chapters(page):
    page.click(".card >> text=Welcome to the rodeo")
    assert page.locator("#newStory").is_visible()
    assert page.locator("#nsEngine option").all_inner_texts() == ["ollama"]
    assert page.locator("#nsParts").inner_text() == "Retells 1 story span; keeps 2 exercise spans and 2 fixed spans word for word."
    page.fill("#nsTheme", "A space heist")
    page.click("#nsBtn")
    sync_api.expect(page.locator("#toastText")).to_have_text(re.compile(r"^New story: 1 chapter retold"))
    group = page.locator(".group", has_text="retold from")
    sync_api.expect(group).to_contain_text("A space heist")
    page.click("#t-story")
    files = page.locator("#fileList").inner_text()
    assert "a-space-heist" in files
    page.locator("#fileList li", has_text="a-space-heist").first.click()
    preview = page.locator("#filePreview").inner_text()
    assert '<span lang="en" id="a-space-heist.welcome.first-task" hist="first-ride">A space heist</span>' in preview
    assert "Saddle up." in preview and 'id="welcome.login"' in preview and "<!--rodeo-keep" not in preview
    page.click("#toastUndo")
    page.click("#t-story")
    assert "a-space-heist" not in page.locator("#fileList").inner_text()


@pytest.mark.parametrize("page", ["live_rmstory"], indirect=True)
def test_live_new_story_needs_story_spans(page):
    page.click("#newChapterBtn")
    page.fill("#ncTitle", "Only steps")
    page.fill("#ncBody", '# Only steps\n<span lang="en" id="only-steps.1">Run it</span>\n')
    page.click("#newForm button[type=submit]")
    assert page.locator("#nsBtn").is_disabled()
    assert page.locator("#nsParts").inner_text().startswith("Nothing to retell yet")


def test_static_has_no_new_story(page):
    assert page.locator("#newStory").is_hidden()


def test_static_publish_links_use_the_browsers_github_login(page):
    page.click("#publishBtn")
    assert page.locator("#pubStatic").is_visible() and page.locator("#pubSubmit").is_hidden()
    assert "Type your GitHub account" in page.locator("#pubSteps").inner_text()
    page.fill("#pubUser", "rider")
    links = page.locator("#pubSteps a")
    name = page.locator("#name").input_value()
    assert links.nth(0).get_attribute("href").startswith("https://github.com/new?name=" + name + "&")
    assert links.nth(1).get_attribute("href") == "https://github.com/rider/{}/upload/main".format(name)
    assert links.nth(2).get_attribute("href").startswith("https://github.com/avaleror/rodeo-cli/new/main?filename=rodeo%2Fdata%2Fcatalog.d%2F")
    page.keyboard.press("Escape")
    assert page.locator("#publishModal").is_hidden()


@pytest.mark.parametrize("page", ["live"], indirect=True)
def test_exposed_live_server_publishes_through_github_pages(page):
    page.click("#publishBtn")
    sync_api.expect(page.locator("#pubStatic")).to_be_visible()


@pytest.mark.parametrize("page", ["live_github"], indirect=True)
def test_live_publish_with_the_hosts_github_login(page, monkeypatch):
    from rodeo.builder import publish

    sent = []
    monkeypatch.setattr(publish, "status", lambda: {"logged_in": True, "login": "rider", "source": "gh", "gh": True,
                                                   "error": "", "device": {}, "catalog": "avaleror/rodeo-cli"})

    def fake(req):
        sent.append(req)
        return {"repo": "https://github.com/rider/" + req["name"], "login": "rider",
                "pull_request": "https://github.com/avaleror/rodeo-cli/pull/7"}

    monkeypatch.setattr(publish, "publish", fake)
    page.click(".card >> text=Welcome to the rodeo")
    page.click("#publishBtn")
    sync_api.expect(page.locator("#pubAccount")).to_have_text("Publishing as rider (GitHub login from gh).")
    page.fill("#pubName", "my-ride")
    page.click("#pubSubmit")
    sync_api.expect(page.locator("#pubResult")).to_contain_text("Published https://github.com/rider/my-ride")
    assert page.locator("#pubResult a").nth(1).get_attribute("href") == "https://github.com/avaleror/rodeo-cli/pull/7"
    req = sent[0]
    assert req["name"] == "my-ride" and req["update"] is False and req["languages"][0] == "en"
    assert {"rodeo-plan.yaml", "README.md"} <= {f["path"] for f in req["files"]}
    assert page.locator("#name").input_value() == "my-ride"


@pytest.mark.parametrize("page", ["live_github"], indirect=True)
def test_live_publish_offers_the_catalog_link_when_the_pr_fails(page, monkeypatch):
    from rodeo.builder import publish

    monkeypatch.setattr(publish, "status", lambda: {"logged_in": True, "login": "rider", "source": "GH_TOKEN", "gh": False,
                                                   "error": "", "device": {}, "catalog": "rider/rodeo-cli"})
    monkeypatch.setattr(publish, "publish", lambda req: {"repo": "https://github.com/rider/" + req["name"], "login": "rider",
                                                         "pull_request": "", "catalog_error": "GitHub 403"})
    page.click("#publishBtn")
    sync_api.expect(page.locator("#pubSubmit")).to_be_visible()
    page.click("#pubSubmit")
    sync_api.expect(page.locator("#pubResult")).to_contain_text("The catalog pull request failed (GitHub 403)")
    href = page.locator("#pubResult a", has_text="add it to the catalog").get_attribute("href")
    assert href.startswith("https://github.com/rider/rodeo-cli/new/main?filename=rodeo%2Fdata%2Fcatalog.d%2F")


@pytest.mark.parametrize("page", ["live_github"], indirect=True)
def test_live_publish_asks_for_a_github_login(page, monkeypatch):
    from rodeo.builder import publish

    logged = {"in": False, "started": False}
    monkeypatch.setattr(publish, "status", lambda: {
        "logged_in": logged["in"], "login": "rider" if logged["in"] else "", "source": "gh", "gh": True, "error": "",
        "catalog": "avaleror/rodeo-cli", "device": {"pending": logged["started"] and not logged["in"], "code": "AB12-CD34",
                                                    "url": publish.DEVICE_URL} if logged["started"] else {}})

    def start():
        logged["started"] = True
        return {"pending": True, "code": "AB12-CD34", "url": publish.DEVICE_URL, "error": ""}

    monkeypatch.setattr(publish, "start_login", start)
    page.click("#publishBtn")
    sync_api.expect(page.locator("#pubLogin")).to_be_visible()
    assert page.locator("#pubSubmit").is_hidden()
    page.context.route("https://github.com/**", lambda route: route.fulfill(body="device page"))
    with page.context.expect_page() as popup:
        page.click("#pubLoginBtn")
    popup.value.wait_for_load_state()
    assert popup.value.url == publish.DEVICE_URL
    popup.value.close()
    sync_api.expect(page.locator("#pubDevice")).to_contain_text("AB12-CD34")
    logged["in"] = True
    sync_api.expect(page.locator("#pubAccount")).to_have_text("Publishing as rider (GitHub login from gh).", timeout=10_000)
    assert page.locator("#pubSubmit").is_visible()


@pytest.mark.parametrize("page", ["live_github"], indirect=True)
def test_live_publish_without_gh_says_how_to_log_in(page, monkeypatch):
    from rodeo.builder import publish

    monkeypatch.setattr(publish, "status", lambda: {"logged_in": False, "login": "", "source": "", "gh": False,
                                                   "error": "", "device": {}, "catalog": "avaleror/rodeo-cli"})
    page.click("#publishBtn")
    sync_api.expect(page.locator("#pubDevice")).to_contain_text("Install the GitHub CLI (gh)")
    assert page.locator("#pubLoginBtn").is_hidden()


@pytest.mark.parametrize("page", ["live_local_lab_builder"], indirect=True)
def test_live_links_and_embeds_its_own_lab_builder_on_the_pages_host(page):
    page.click(".engine >> text=lab-in-a-box")
    href = page.locator("#enginePanel a", has_text="Open lab-builder").get_attribute("href")
    assert href == "http://127.0.0.1:9/lab-builder/"
    page.click(".seg button >> text=Embed here")
    page.click("#enginePanel button >> text=Open embedded lab-builder")
    src = page.locator("#embedFrame").get_attribute("src")
    assert src.startswith("http://127.0.0.1:9/lab-builder/?embed=1&origin=http%3A%2F%2F127.0.0.1%3A")


@pytest.mark.parametrize("page", ["live_local_lab_builder"], indirect=True)
def test_live_takes_the_lab_its_own_lab_builder_hands_back(page):
    handback = ("<script>parent.postMessage({type: 'labinabox:lab', lab: {nodes: {n1: {addons: ['smlm']}}}}, '*')"
                "</script>")
    page.route("http://127.0.0.1:9/**", lambda route: route.fulfill(body=handback, content_type="text/html"))
    page.click(".engine >> text=lab-in-a-box")
    page.click(".seg button >> text=Embed here")
    page.click("#enginePanel button >> text=Open embedded lab-builder")
    sync_api.expect(page.locator("#toastText")).to_have_text("Lab from the lab-builder: smlm")
    assert page.locator("#embedOverlay").is_hidden()
def _zip_of(page, selector) -> tuple[str, dict[str, str]]:
    with page.expect_download() as info:
        page.click(selector)
    with zipfile.ZipFile(io.BytesIO(Path(info.value.path()).read_bytes())) as zf:
        return info.value.suggested_filename, {n: zf.read(n).decode() for n in zf.namelist()}


def test_infrastructure_and_instructions_download_apart(page):
    assert page.locator("#partInfra").is_visible() and page.locator("#partInstructions").is_visible()
    page.click(".card >> text=Welcome to the rodeo")
    name, infra = _zip_of(page, "#dlInfra")
    assert name == "my-rodeo-infrastructure.zip"
    assert "my-rodeo-infrastructure/rodeo-plan.yaml" in infra and not any("/story/" in n for n in infra)
    assert "not part of this download" in infra["my-rodeo-infrastructure/README.md"]
    name, instr = _zip_of(page, "#dlInstructions")
    assert name == "my-rodeo-instructions.zip"
    assert sorted(n.split("/", 1)[1] for n in instr) == ["README.md", "story/01-welcome.md", "story/chapters.yaml",
                                                         "story/stories/first-ride.yaml"]
    full = _zip(page)
    assert "my-rodeo/story/01-welcome.md" in full and "my-rodeo/rodeo-plan.yaml" in full


def test_instructions_repository_goes_into_the_plan(page):
    page.fill("#contentUrl", "github.com/acme/labs")
    page.locator("#contentUrl").dispatch_event("change")
    sync_api.expect(page.locator("#contentNote")).to_contain_text("https://")
    page.fill("#contentUrl", "https://github.com/acme/labs/tree/v2/story")
    page.locator("#contentUrl").dispatch_event("change")
    sync_api.expect(page.locator("#contentNote")).to_contain_text("The plan gets content: https://github.com/acme/labs @ v2 /story")
    page.click(".card >> text=Welcome to the rodeo")
    _, infra = _zip_of(page, "#dlInfra")
    plan = yaml.safe_load(infra["my-rodeo-infrastructure/rodeo-plan.yaml"])
    assert plan["content"] == {"repo": "https://github.com/acme/labs", "ref": "v2", "path": "story", "format": "rmstory"}
    assert "rodeo content apply" in infra["my-rodeo-infrastructure/README.md"]


@pytest.mark.parametrize("page", ["site", "live"], indirect=True)
def test_page_loads_its_own_fonts_and_nothing_from_other_hosts(page):
    origin = page.evaluate("location.origin")
    seen = []
    page.on("request", lambda req: seen.append(req.url))
    page.reload()
    page.wait_for_selector(".engine")
    page.evaluate("document.fonts.ready.then(() => true)")
    assert page.evaluate("document.fonts.check('700 16px Syne') && document.fonts.check('16px \"Space Mono\"')")
    loaded = page.evaluate("[...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family)")
    assert {"Syne", "Space Mono"} <= {f.strip('"') for f in loaded}
    assert seen and all(u.startswith(origin) or u.startswith("data:") for u in seen), [u for u in seen if not u.startswith(origin)]


def test_static_deploy_is_greyed_out_with_why(page):
    assert page.locator("#deployBtn").get_attribute("aria-disabled") == "true"
    assert "Only available in rodeo builder, the dynamic version" in page.locator("#deployWrap").get_attribute("title")
    page.click("#deployBtn", force=True)
    sync_api.expect(page.locator("#toastText")).to_contain_text("Only available in rodeo builder")
    assert page.locator("#deployModal").is_hidden()


@pytest.mark.parametrize("page", ["live"], indirect=True)
def test_exposed_live_server_does_not_deploy(page):
    sync_api.expect(page.locator("#deployWrap")).to_have_attribute("title", re.compile("listens on this machine only"))
    assert page.locator("#deployBtn").get_attribute("aria-disabled") == "true"


@pytest.mark.parametrize("page", ["live_deploy"], indirect=True)
def test_live_deploys_without_saving_and_says_so(page, monkeypatch):
    from rodeo.builder import deploywork

    real = deploywork.ensure_plan_secrets
    monkeypatch.setattr(deploywork, "ensure_plan_secrets", lambda plan, lab_path=None: (real(plan, lab_path=lab_path)[0], ["scc_regcode"]))
    page.click(".card >> text=Welcome to the rodeo")
    sync_api.expect(page.locator("#deployBtn")).to_have_attribute("aria-disabled", "false")
    page.click("#deployBtn")
    sync_api.expect(page.locator("#deployModal .unsaved")).to_contain_text("This rodeo is not saved.")
    assert "my-rodeo-unsaved-" in page.locator("#dpWhere").inner_text()
    page.click("#dpSubmit")
    sync_api.expect(page.locator("#dpSecretFields input[name=scc_regcode]")).to_be_visible()
    page.fill("#dpSecretFields input[name=scc_regcode]", "REG-123")
    page.click("#dpSubmit")
    sync_api.expect(page.locator("#deployModal")).to_be_hidden()
    banner = page.locator("#deployText")
    sync_api.expect(banner).to_contain_text("Deploying an unsaved rodeo")
    sync_api.expect(banner).to_contain_text("This rodeo is not saved")
    sync_api.expect(banner).to_contain_text("Deployed from an unsaved rodeo", timeout=15_000)
    page.click("#deployLogBtn")
    sync_api.expect(page.locator("#deployLog")).to_contain_text("phase 2")
    assert "rodeo up --yes --no-tmux --dir" in page.locator("#deployLog").inner_text()
    assert "REG-123" in deploywork.secrets_path().read_text()
    page.click("#deploySave")
    sync_api.expect(banner).to_contain_text("saved as the profile my-rodeo")
    assert page.locator("#deploySave").is_hidden()
