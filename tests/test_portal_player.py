"""Claim portal + rodeo player: the "Open your lab" card (on for every workshop fleet)."""
from __future__ import annotations

import json

import yaml

from rodeo.fleet import portal as fp
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.portal.web import _lab_cards

LINK = "https://13-61-184-227.sslip.io/enter?t=AbCdEfGhIjKlMnOpQrStUvWx"


def test_card_shows_only_for_a_well_formed_link():
    html = _lab_cards("lab-1", {"player_url": LINK})
    assert "Open your lab" in html and LINK in html
    for bad in ("javascript:alert(1)", "https://evil.example/", "http://x.example/enter?t=" + "a" * 20,
                LINK + "'><script>"):
        assert "Open your lab" not in _lab_cards("lab-1", {"player_url": bad})


def test_lab_without_a_player_renders_as_before():
    data = {"components": [{"label": "H", "url": "https://1.2.3.4:8443", "user": "admin", "password": "p"}]}
    assert _lab_cards("lab-1", data) == _lab_cards(
        "lab-1", {**data, "player_url": ""})


def _inv(tmp_path, player: bool):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "virt-rodeo"},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m8id.8xlarge", "student_access": "open"},
        "portal": {"enabled": True, "ssh": "10.9.9.9", "public_ip": "10.9.9.9"},
        "hosts": [{"id": "student-01", "ssh": "10.0.0.1", "public_ip": "10.0.0.1"}],
    }
    if not player:
        data["player"] = False  # the fleet opted out of the student UI
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def _publish(tmp_path, monkeypatch, *, player: bool, link_out: str = LINK + "\n"):
    inv = _inv(tmp_path, player)
    calls: list = []
    pushed: dict = {}

    class R:
        ready, reason = True, None

    monkeypatch.setattr(fp, "_check_host", lambda *a, **k: R())

    def fake_remote(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        calls.append(argv)
        if host.id == "portal":
            pushed["stdin"] = stdin
            return RemoteResult(host.id, 0, '{"imported": 1}\n', "")
        if argv[:2] == ["python3", "-c"]:
            return RemoteResult(host.id, 0, json.dumps({"harvester_admin_password": "x" * 16,
                                                        "rancher_admin_password": "x" * 16}), "")
        if argv == ["bash", "-c", fp.PLAYER_LINK_SCRIPT]:
            return RemoteResult(host.id, 0, link_out, "")
        raise AssertionError(argv)

    monkeypatch.setattr(fp, "run_remote", fake_remote)
    monkeypatch.setattr("rodeo.paths.rodeo_dir", lambda: tmp_path / "rodeo")
    fp.portal_publish(inv)
    return json.loads(pushed["stdin"])[0]["data"], calls


def test_inventory_reads_the_opt_out(tmp_path):
    from rodeo.fleet.inventory import runs_player

    assert runs_player(_inv(tmp_path, True)) is True
    assert runs_player(_inv(tmp_path, False)) is False


def test_publish_without_the_player_never_probes_it(tmp_path, monkeypatch):
    data, calls = _publish(tmp_path, monkeypatch, player=False)
    assert "player_url" not in data
    assert ["bash", "-c", fp.PLAYER_LINK_SCRIPT] not in calls


def test_publish_with_the_player_adds_the_link_and_drops_the_raw_lab(tmp_path, monkeypatch):
    data, _ = _publish(tmp_path, monkeypatch, player=True)
    assert data["player_url"] == LINK
    # No UI URLs, no admin passwords: the player is the only way in.
    assert "components" not in data
    assert "x" * 16 not in json.dumps(data)


def test_publish_without_the_player_keeps_the_cards(tmp_path, monkeypatch):
    data, _ = _publish(tmp_path, monkeypatch, player=False)
    assert data["components"] and "player_url" not in data


def test_card_page_with_a_player_shows_no_credentials():
    data = {"player_url": LINK,
            "components": [{"label": "H", "url": "https://1.2.3.4:8443", "user": "admin", "password": "pw-secret"}]}
    html = _lab_cards("lab-1", data)
    assert "Open your lab" in html and "pw-secret" not in html and ":8443" not in html


def test_lab_without_its_player_is_not_ready_and_leaks_nothing(tmp_path, monkeypatch):
    # Never fall back to raw credentials on a fleet that promised the player.
    assert _publish(tmp_path, monkeypatch, player=True, link_out="")[0] == {}
    assert _publish(tmp_path, monkeypatch, player=True, link_out="oops\n")[0] == {}
