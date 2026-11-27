"""Player profiles: what the player shows comes from the lab profile's player.yaml."""
from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from rodeo.player import profile as p
from rodeo.player import setup as s

SHA = "a" * 40

EDGE = {
    "title": "SUSE Edge Rodeo",
    "track_repo": "https://github.com/example/edge-rodeo.git",
    "uis": [
        {"key": "rancher", "label": "SUSE Rancher Prime", "upstream": "192.168.122.9:443", "tab_port": 443},
        {"key": "gitea", "label": "Gitea", "upstream": "192.168.122.20:3000", "tab_port": 3000},
        {"key": "eib", "label": "Image builder", "upstream": "192.168.122.30:8443", "tab_port": 8443},
    ],
    "variables": {"RANCHER_PASSWORD": "??rancher_admin_password"},
}


@pytest.fixture
def examples(tmp_path):
    (tmp_path / "suse-edge").mkdir()
    (tmp_path / "suse-edge" / "player.yaml").write_text(yaml.safe_dump(EDGE))
    (tmp_path / "virt-rodeo").mkdir()
    (tmp_path / "virt-rodeo" / "player.yaml").write_text((p.EXAMPLES / "virt-rodeo" / "player.yaml").read_text())
    (tmp_path / "no-player").mkdir()
    return tmp_path


def test_shipped_virt_rodeo_profile_is_what_the_player_did_before():
    prof = p.load("virt-rodeo")
    assert prof.title == "SUSE Virtualization Rodeo"
    assert prof.track_repo == "https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo.git"
    assert [(u.key, u.upstream, u.tab_port) for u in prof.uis] == [
        ("hv", "192.168.122.10:443", 8443), ("rancher", "192.168.122.9:30002", 30002)]
    assert prof.kubeconfig == "harvester-kubeconfig"
    assert prof.variables == {"RANCHER_PASSWORD": "rancher_admin_password",
                              "HARVESTER_PASSWORD": "harvester_admin_password"}
    assert p.load(None) == prof


def test_a_profile_without_player_yaml_is_an_error_never_virt_rodeo(examples):
    with pytest.raises(p.ProfileError, match="has no player.yaml"):
        p.load("no-player", examples)
    assert not p.has_profile("no-player", examples) and p.has_profile("suse-edge", examples)
    assert p.load(None, examples).name == "virt-rodeo"  # hosts set up before profiles existed


def test_a_profile_without_a_track_shows_a_guide_and_credentials():
    prof = p.load("virt-workshop")
    assert prof.track_repo == "" and prof.guide_url.startswith("https://")
    assert [(c.label, c.user, c.secret) for c in prof.credentials] == [
        ("SUSE Virtualization (Harvester)", "admin", "harvester_admin_password"),
        ("SUSE Rancher Prime", "admin", "rancher_admin_password")]


@pytest.mark.parametrize("raw,match", [
    ({"credentials": [{"label": "R", "user": "admin", "password": "plain-text"}]}, "secrets.yaml key"),
    ({"credentials": [{"label": "", "user": "admin", "password": "??k"}]}, "credentials need"),
    ({"guide_url": "http://insecure.example/"}, "guide_url"),
    ({"uis": [{"key": "x", "upstream": "10.0.0.1:80", "tab_port": 80, "scheme": "ftp"}]}, "scheme"),
])
def test_new_fields_are_validated(raw, match):
    base = {"title": "T", "uis": [{"key": "x", "upstream": "10.0.0.1:443", "tab_port": 443}]}
    with pytest.raises(p.ProfileError, match=match):
        p.parse("t", {**base, **raw})


def test_http_ui_is_allowed():
    prof = p.parse("t", {"title": "T", "uis": [{"key": "app", "upstream": "192.168.122.21:30080",
                                                "tab_port": 30080, "scheme": "http"}]})
    assert prof.uis[0].scheme == "http"


def test_every_shipped_player_yaml_is_valid():
    found = sorted(p.EXAMPLES.glob(f"*/{p.FILE}"))
    assert found
    for path in found:
        p.load(path.parent.name)


@pytest.mark.parametrize("change,match", [
    ({"track_repo": "git@github.com:x/y.git"}, "https"),
    ({"uis": []}, "1 to"),
    ({"uis": [{"key": "player", "upstream": "1.2.3.4:443", "tab_port": 443}]}, "short lowercase"),
    ({"uis": [{"key": "Bad", "upstream": "1.2.3.4:443", "tab_port": 443}]}, "short lowercase"),
    ({"uis": [{"key": "a", "upstream": "evil.example:443", "tab_port": 443}]}, "IP:port"),
    ({"uis": [{"key": "a", "upstream": "1.2.3.4:443", "tab_port": 443},
              {"key": "a", "upstream": "1.2.3.5:443", "tab_port": 444}]}, "unique"),
    ({"uis": [{"key": "a", "upstream": "1.2.3.4:443", "tab_port": "x"}]}, "number"),
    ({"kubeconfig": "../../etc/shadow"}, "file name"),
    ({"student_ssh_key": "relative/key"}, "absolute"),
    ({"variables": {"bad var": "??k"}}, "TRACK_VAR"),
    ({"variables": {"RANCHER_PASSWORD": "hunter2"}}, "never the value"),
])
def test_bad_player_yaml_is_refused(change, match):
    with pytest.raises(p.ProfileError, match=match):
        p.parse("t", {**EDGE, **change})


def test_profile_name_cannot_walk_out_of_the_examples(examples):
    with pytest.raises(p.ProfileError):
        p.load("../etc", examples)


def test_upstream_override_only_for_known_uis():
    prof = p.load("virt-rodeo").with_upstreams({"hv": "10.0.0.5:443"})
    assert prof.ui("hv").upstream == "10.0.0.5:443" and prof.ui("rancher").upstream == "192.168.122.9:30002"
    with pytest.raises(p.ProfileError, match="no UI"):
        p.load("virt-rodeo").with_upstreams({"gitea": "10.0.0.5:443"})


def test_a_second_product_gets_its_own_names_tabs_and_proxies(examples):
    prof = p.load("suse-edge", examples)
    opts = s.SetupOptions(base=s.domain_base("k7q2mx9a", "rodeocli.com"), track_ref=SHA, profile=prof,
                          domain="rodeocli.com", tls_pem="")
    opts.validate()
    assert opts.names == {"player": "k7q2mx9a.rodeocli.com", "rancher": "rancher-k7q2mx9a.rodeocli.com",
                          "gitea": "gitea-k7q2mx9a.rodeocli.com", "eib": "eib-k7q2mx9a.rodeocli.com"}
    text = s.caddyfile(opts)
    assert set(re.findall(r"reverse_proxy (https://\S+)", text)) == {
        "https://192.168.122.9:443", "https://192.168.122.20:3000", "https://192.168.122.30:8443"}
    assert "hv-" not in text and "192.168.122.10" not in text
    assert text.count("forward_auth @protected") == 4  # the player and every UI
    cfg = s.player_config(opts)
    assert cfg["title"] == "SUSE Edge Rodeo"
    assert cfg["tab_urls"] == {"443": "https://rancher-k7q2mx9a.rodeocli.com/",
                               "3000": "https://gitea-k7q2mx9a.rodeocli.com/",
                               "8443": "https://eib-k7q2mx9a.rodeocli.com/"}
    assert cfg["hosts"] == opts.names
    assert cfg["kubeconfig"] == ""  # this profile names none
    from rodeo.player.server import PlayerConfig
    PlayerConfig(**cfg)
    # The fleet's DNS names are the same ones setup serves.
    from rodeo.fleet.dns import lab_names
    assert lab_names("rodeocli.com", "k7q2mx9a", tuple(u.key for u in prof.uis)) == opts.names


def test_install_script_skips_kubeconfig_and_key_when_the_profile_has_none(examples):
    opts = s.SetupOptions(base="1-2-3-4.sslip.io", track_ref=SHA, profile=p.load("suse-edge", examples))
    text = s.install_script(opts, "/usr/local/bin/rodeo")
    assert str(s.PLAYER_KUBECONFIG) not in text and "/home/student/.rodeo" not in text
    assert "LABKEY" not in text
    assert "fetch -q --depth 1 https://github.com/example/edge-rodeo.git" in text
    assert "student can read the rodeo key" in text  # that proof runs for every profile
    assert subprocess.run(["bash", "-n"], input=text, text=True).returncode == 0
    virt = s.install_script(s.SetupOptions(base="1-2-3-4.sslip.io", track_ref=SHA), "/usr/local/bin/rodeo")
    assert ".rodeo/harvester-kubeconfig" in virt and "LABKEY=/opt/suse-virt-workshop/lab-vm-key" in virt


def test_track_variables_follow_the_profile(tmp_path, monkeypatch):
    sec = tmp_path / "secrets.yaml"
    sec.write_text("rancher_admin_password: r-pw\nharvester_admin_password: h-pw\ngitea_password: g\n")
    monkeypatch.setattr(s, "_secrets_files", lambda user: [sec])
    assert s.lab_passwords(None, {"GITEA_PASSWORD": "gitea_password"}) == {"GITEA_PASSWORD": "g"}
    assert s.lab_passwords(None) == {"RANCHER_PASSWORD": "r-pw", "HARVESTER_PASSWORD": "h-pw"}


def test_fleet_names_the_profile_only_when_it_is_not_the_default(monkeypatch):
    from types import SimpleNamespace

    from rodeo.fleet import player as fp

    host = SimpleNamespace(id="s1", labels={})
    for lab_profile, expect in (("virt-rodeo", None), (None, None), ("suse-edge", "suse-edge")):
        monkeypatch.setattr("rodeo.player.profile.has_profile", lambda n, *a: n in ("virt-rodeo", "suse-edge"))
        inv = SimpleNamespace(player=None, lab_profile=lab_profile)
        argv = fp.setup_argv(inv, host, SHA)
        assert (argv[argv.index("--profile") + 1] if "--profile" in argv else None) == expect


def test_setup_command_refuses_an_unknown_profile(monkeypatch):
    from click.testing import CliRunner

    from rodeo.commands.player_cmd import player_cmd

    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = CliRunner().invoke(player_cmd, ["setup", "--track-ref", SHA, "--base", "1-2-3-4.sslip.io",
                                          "--profile", "nothing-here"])
    assert res.exit_code != 0 and "has no player.yaml" in res.output
    res = CliRunner().invoke(player_cmd, ["setup", "--track-ref", SHA, "--base", "1-2-3-4.sslip.io",
                                          "--upstream", "gitea=1.2.3.4:443"])
    assert res.exit_code != 0 and "no UI 'gitea'" in res.output


def test_player_test_accepts_expect_none(tmp_path, monkeypatch):
    """`--expect none` judges nothing, as its help says (it used to demand a file)."""
    from click.testing import CliRunner

    from rodeo.commands import player_cmd

    seen = {}

    def fake_track_test(runner, stop_on_failure, expect):
        seen["expect"] = expect
        return []

    class Cfg:
        track_dir = str(tmp_path)
        state_dir = str(tmp_path / "state")
        host = "kvm-host"
        sandbox_id = ""
        home = str(tmp_path)
        kubeconfig = ""
        check_timeout = 120

    monkeypatch.setattr("rodeo.player.server.load_config", lambda path: Cfg)
    monkeypatch.setattr("rodeo.player.runner.Runner", lambda *a, **k: object())
    monkeypatch.setattr("rodeo.player.runner.track_test", fake_track_test)
    conf = tmp_path / "config.json"
    conf.write_text("{}")
    r = CliRunner().invoke(player_cmd.test_cmd, ["--config", str(conf), "--expect", "none"])
    assert seen.get("expect") == {}, r.output
    r = CliRunner().invoke(player_cmd.test_cmd, ["--config", str(conf), "--expect",
                                                 str(tmp_path / "missing.yaml")])
    assert r.exit_code != 0 and "not a file" in r.output
