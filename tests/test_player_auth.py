"""Player login: access link, forward_auth, the per-host cookie hop."""
from __future__ import annotations

import http.client
import json
import stat
import threading
import urllib.parse
from pathlib import Path

import pytest

from rodeo.player import auth
from rodeo.player.server import Player, PlayerConfig, make_server

P, HV, RA = "lab.example", "hv.lab.example", "rancher.lab.example"


def test_ensure_is_root_only_and_stable(tmp_path):
    f = tmp_path / "auth.json"
    a = auth.ensure(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert auth.ensure(f).token == a.token
    assert auth.rotate(f).token != a.token


def test_cookies_are_bound_to_host_and_expire():
    a = auth.Auth(secret=b"k" * 32, token="t")
    c = a.cookie(HV, now=1000)
    assert a.cookie_ok(HV, c, now=1001)
    assert not a.cookie_ok(RA, c, now=1001)          # another name
    assert not a.cookie_ok(HV, c, now=1000 + auth.SESSION_SECONDS + 1)
    other_lab = auth.Auth(secret=b"x" * 32, token="t")
    assert not other_lab.cookie_ok(HV, c, now=1001)  # another lab
    assert not a.ticket_ok(HV, c, now=1001)          # a cookie is not a ticket
    assert not a.cookie_ok(HV, "garbage")


@pytest.fixture
def lab(tmp_path: Path):
    track = tmp_path / "track" / "01-one"
    track.mkdir(parents=True)
    (track / "assignment.md").write_text("---\nslug: one\ntitle: One\n---\nHi\n")
    a = auth.ensure(tmp_path / "auth.json")
    cfg = PlayerConfig(track_dir=str(track.parent), state_dir=str(tmp_path / "state"), port=0,
                       auth_file=str(tmp_path / "auth.json"), hosts={"player": P, "hv": HV, "rancher": RA})
    server = make_server(Player(cfg))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], a
    server.shutdown()
    server.server_close()


def _req(port, path, host, cookie=None, method="GET", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host, **(headers or {})}
    if cookie:
        h["Cookie"] = f"{auth.COOKIE}={cookie}"
    c.request(method, path, headers=h)
    r = c.getresponse()
    body = r.read()
    return r.status, dict(r.getheaders()), body


def _set_cookie(headers) -> str:
    raw = headers.get("Set-Cookie", "")
    assert "HttpOnly" in raw and "Secure" in raw and "SameSite=Lax" in raw
    return raw.split(";", 1)[0].split("=", 1)[1]


def test_everything_is_locked_without_the_link(lab):
    port, _ = lab
    assert _req(port, "/", P)[0] == 401
    assert _req(port, "/api/state", P)[0] == 401
    assert _req(port, "/_auth", P)[0] == 401
    assert _req(port, "/healthz", P)[0] == 200


def test_wrong_token_is_refused(lab):
    port, _ = lab
    status, headers, _ = _req(port, "/enter?t=nope", P)
    assert status == 401 and "Set-Cookie" not in headers


def test_link_signs_in_the_player(lab):
    port, a = lab
    status, headers, _ = _req(port, f"/enter?t={a.token}", P)
    assert status == 302 and headers["Location"] == "/"
    cookie = _set_cookie(headers)
    assert _req(port, "/api/state", P, cookie)[0] == 200
    assert _req(port, "/_auth", P, cookie, headers={"X-Forwarded-Host": P})[0] == 200


def test_ui_names_get_their_own_cookie_through_the_hop(lab):
    port, a = lab
    player_cookie = _set_cookie(_req(port, f"/enter?t={a.token}", P)[1])
    # 1. Caddy asks about hv. with no cookie: send the browser to the player's hop.
    status, h, _ = _req(port, "/_auth", HV, headers={"X-Forwarded-Host": HV, "X-Forwarded-Uri": "/dashboard/x"})
    assert status == 302
    hop = urllib.parse.urlsplit(h["Location"])
    assert hop.netloc == P and hop.path == "/auth/hop"
    # 2. The player, seeing its own cookie, hands out a ticket for hv. only.
    status, h, _ = _req(port, f"{hop.path}?{hop.query}", P, player_cookie)
    assert status == 302
    back = urllib.parse.urlsplit(h["Location"])
    assert back.netloc == HV and back.path == "/.rodeo/auth"
    # 3. hv. trades the ticket for its own cookie and returns to the page asked for.
    status, h, _ = _req(port, f"{back.path}?{back.query}", HV)
    assert status == 302 and h["Location"] == "/dashboard/x"
    hv_cookie = _set_cookie(h)
    assert _req(port, "/_auth", HV, hv_cookie, headers={"X-Forwarded-Host": HV})[0] == 200
    # That cookie is useless on the other UI name.
    assert _req(port, "/_auth", RA, hv_cookie, headers={"X-Forwarded-Host": RA})[0] == 302


def test_hop_needs_the_player_cookie_and_a_known_name(lab):
    port, a = lab
    assert _req(port, "/auth/hop?to=hv&r=%2F", P)[0] == 401
    player_cookie = _set_cookie(_req(port, f"/enter?t={a.token}", P)[1])
    assert _req(port, "/auth/hop?to=evil.example&r=%2F", P, player_cookie)[0] == 401


def test_ticket_for_one_name_does_not_work_on_another(lab):
    port, a = lab
    t = a.ticket(HV)
    assert _req(port, f"/.rodeo/auth?ticket={t}&r=%2F", RA)[0] == 401


def test_return_path_cannot_leave_the_site(lab):
    port, a = lab
    t = a.ticket(HV)
    _, h, _ = _req(port, f"/.rodeo/auth?ticket={t}&r=%2F%2Fevil.example%2F", HV)
    assert h["Location"] == "/"


def test_post_needs_the_cookie(lab):
    port, _ = lab
    status, _, body = _req(port, "/api/check", P, method="POST", headers={"X-Rodeo-Player": "1"})
    assert status == 401 and json.loads(body)["error"]


def test_redirects_never_leak_the_ticket_in_referer(lab):
    port, a = lab
    player_cookie = _set_cookie(_req(port, f"/enter?t={a.token}", P)[1])
    _, h, _ = _req(port, "/auth/hop?to=hv&r=%2F", P, player_cookie)
    assert h["Referrer-Policy"] == "no-referrer"
