"""WP2: the player web app, against a loopback server (no cloud, no root)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rodeo.player.server import CSRF_HEADER, Player, PlayerConfig, load_config, make_server, plain

FRONT = """---
slug: {slug}
title: "<span id="a.{n}" lang="en" hist="vertrex-bank">Chapter {n}</span>"
tabs:
- title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  port: 8443
  protocol: https
- title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
---
Hello [[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]
[button label="Cluster Terminal" variant="success"](tab-1)
"""


def _sh(body: str) -> str:
    return "#!/bin/bash\n" + body + "\n"


@pytest.fixture
def track(tmp_path: Path) -> Path:
    root = tmp_path / "track"
    for n, name in enumerate(["01-one", "02-two", "03-three"], start=1):
        d = root / name
        d.mkdir(parents=True)
        (d / "assignment.md").write_text(FRONT.format(slug=name[3:], n=n))
        if n < 3:
            (d / "check-kvm-host").write_text(_sh(f'[ -f {tmp_path}/ok-{n} ] || {{ echo "FAIL: do chapter {n}"; exit 1; }}'))
            (d / "solve-kvm-host").write_text(_sh(f"touch {tmp_path}/ok-{n}"))
            (d / "setup-kvm-host").write_text(_sh("echo setup secret-password"))
    return root


@pytest.fixture
def served(track: Path, tmp_path: Path):
    cfg = PlayerConfig(
        track_dir=str(track), state_dir=str(tmp_path / "state"), port=0, home=str(tmp_path),
        kubeconfig=str(tmp_path / "kc"),
        tab_urls={"8443": "https://hv.lab.example/", "30002": "https://rancher.lab.example/"},
    )
    player = Player(cfg)
    player.runner.set_variable("RANCHER_PASSWORD", "<pw&>")
    server = make_server(player)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, player
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if r.headers.get_content_type() == "application/json" else body
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _post(url: str, data: dict | None = None, *, header: bool = True) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if header:
        headers[CSRF_HEADER] = "1"
    req = urllib.request.Request(url, data=json.dumps(data or {}).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _wait(base: str) -> dict:
    for _ in range(200):
        _, s = _get(base + "/api/state")
        if not s["busy"]:
            return s
        time.sleep(0.05)
    raise AssertionError("player stayed busy")


def test_page_and_static_assets(served):
    base, _ = served
    code, body = _get(base + "/")
    assert code == 200 and "<title>SUSE Virtualization Rodeo</title>" in body
    code, js = _get(base + "/static/marked.min.js")
    assert code == 200 and "marked v12.0.2" in js
    assert _get(base + "/static/../server.py")[0] == 404


def test_state_lists_chapters_with_plain_titles_and_proxy_tabs(served):
    base, _ = served
    _, s = _get(base + "/api/state")
    assert [c["title"] for c in s["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert s["current"] == 0 and s["entered"] is False
    assert [(t["type"], t["url"]) for t in s["tabs"]] == [
        ("service", "https://hv.lab.example/"),
        ("terminal", "/terminal/"),
        ("service", "https://rancher.lab.example/"),
    ]
    # Never the raw lab address.
    assert "192.168" not in json.dumps(s) and ":8443" not in json.dumps(s)


def test_chapter_body_escapes_variables_and_rewrites_buttons(served):
    base, _ = served
    _, view = _get(base + "/api/chapter/0")
    assert 'Hello <code class="rodeo-var" title="Click to copy">&lt;pw&amp;&gt;</code>' in view["body"]
    assert 'class="rodeo-tab-button" data-tab="1"' in view["body"]


def test_no_reading_ahead(served):
    base, _ = served
    assert _get(base + "/api/chapter/1")[0] == 404


def test_post_without_the_player_header_is_refused(served):
    base, _ = served
    code, _ = _post(base + "/api/enter", header=False)
    assert code == 403


def test_failing_check_shows_its_output_and_does_not_advance(served):
    base, _ = served
    assert _post(base + "/api/enter")[0] == 202
    _wait(base)
    assert _post(base + "/api/check")[0] == 202
    s = _wait(base)
    assert s["current"] == 0 and s["last"]["ok"] is False
    check = s["last"]["scripts"][0]
    assert check["kind"] == "check" and "FAIL: do chapter 1" in check["output"]


def test_setup_output_is_never_sent_to_the_browser(served):
    base, _ = served
    _post(base + "/api/enter")
    s = _wait(base)
    assert s["last"]["scripts"] == [{"kind": "setup", "exit": 0, "timed_out": False,
                                     "seconds": s["last"]["scripts"][0]["seconds"]}]
    assert "secret-password" not in json.dumps(s)


def test_passing_check_advances_and_skip_runs_solves(served, tmp_path):
    base, _ = served
    _post(base + "/api/enter")
    _wait(base)
    (tmp_path / "ok-1").touch()
    _post(base + "/api/check")
    s = _wait(base)
    assert s["current"] == 1 and s["completed"] == [0] and s["entered"] is True
    assert _post(base + "/api/skip", {"target": 2})[0] == 202
    s = _wait(base)
    assert s["current"] == 2 and (tmp_path / "ok-2").exists()
    assert s["chapters"][2]["check"] is False


def test_a_second_move_while_busy_is_refused(served, track):
    base, player = served
    (track / "01-one" / "setup-kvm-host").write_text(_sh("sleep 2"))
    assert _post(base + "/api/enter")[0] == 202
    assert _post(base + "/api/check")[0] == 409
    _wait(base)


def test_skip_needs_a_target(served):
    base, _ = served
    assert _post(base + "/api/skip", {})[0] == 400


def test_bad_move_is_reported_not_crashed(served):
    base, _ = served
    _post(base + "/api/check")  # chapter not entered yet
    s = _wait(base)
    assert s["last"]["ok"] is False and "setup has not passed" in s["last"]["message"]


def test_track_images_are_served_and_nothing_else(served, track):
    base, _ = served
    (track / "assets" / "logos").mkdir(parents=True)
    (track / "assets" / "logos" / "suse.svg").write_text("<svg/>")
    (track / "assets" / "notes.txt").write_text("not an image")
    code, body = _get(base + "/assets/logos/suse.svg")
    assert code == 200 and body == "<svg/>"
    assert _get(base + "/assets/notes.txt")[0] == 404
    assert _get(base + "/assets/..%2F01-one%2Fsolve-kvm-host")[0] == 404
    assert _get(base + "/assets/%2E%2E/01-one/assignment.md")[0] == 404


def test_refuses_to_bind_a_public_address(track, tmp_path):
    cfg = PlayerConfig(track_dir=str(track), state_dir=str(tmp_path / "s"), listen="0.0.0.0", port=0)
    with pytest.raises(ValueError, match="loopback only"):
        make_server(Player(cfg))


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"track_dir": "t", "state_dir": "s", "listen_public": True}))
    with pytest.raises(ValueError, match="unknown"):
        load_config(p)


def test_plain_strips_rmstory_spans():
    assert plain('<span lang="en">\U0001F3E6 Chapter 1: The &amp; Arrival</span>') == "\U0001F3E6 Chapter 1: The & Arrival"


def test_back_is_gone(served):
    base, _ = served
    assert _post(base + "/api/back", {"target": 0})[0] == 404


def test_state_reports_elapsed_seconds_while_busy(served, track):
    base, _ = served
    (track / "01-one" / "setup-kvm-host").write_text(_sh("sleep 2"))
    _post(base + "/api/enter")
    time.sleep(1.2)
    _, s = _get(base + "/api/state")
    assert s["busy"] == "enter" and s["busy_seconds"] >= 1
    s = _wait(base)
    assert s["busy_seconds"] == 0 and s["entered"] is True


def test_page_strips_scripts_and_handlers_from_assignments(served):
    base, _ = served
    _, body = _get(base + "/")
    assert "function clean(" in body and 'querySelectorAll("script' in body and "/^on/i" in body
