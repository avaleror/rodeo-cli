"""rodeo player setup: rendered files and the install script (no root, no cloud)."""
from __future__ import annotations

import json
import re
import subprocess

import pytest
from click.testing import CliRunner

from rodeo.player import setup as s

SHA = "77022fd" + "0" * 33


def _opts(**kw) -> s.SetupOptions:
    return s.SetupOptions(base="13-61-184-227.sslip.io", track_ref=SHA, **kw)


def test_sslip_base():
    assert s.sslip_base("13.61.184.227") == "13-61-184-227.sslip.io"
    with pytest.raises(ValueError):
        s.sslip_base("example.com")


@pytest.mark.parametrize("kw,match", [
    ({"track_ref": "main"}, "40-character"),
    ({"base": "Bad_Name"}, "DNS name"),
    ({"track_repo": "git@github.com:x/y.git"}, "https"),
    ({"harvester_upstream": "evil.example:443"}, "IP:port"),
])
def test_validation_refuses_bad_input(kw, match):
    base = {"base": "13-61-184-227.sslip.io", "track_ref": SHA}
    with pytest.raises(ValueError, match=match):
        s.SetupOptions(**{**base, **kw}).validate()


def test_caddyfile_proxies_only_the_player_ttyd_and_the_two_uis():
    text = s.caddyfile(_opts())
    upstreams = re.findall(r"reverse_proxy (\S+)", text)
    assert set(upstreams) == {f"127.0.0.1:{s.TTYD_PORT}", f"127.0.0.1:{s.PLAYER_PORT}",
                              "https://192.168.122.10:443", "https://192.168.122.9:30002"}
    # Every site asks the player before proxying anything.
    sites = re.split(r"\n(?=\S+ \{\n)", text.split("}\n", 1)[1].strip())
    assert len(sites) == 3
    for site in sites:
        assert re.search(r"forward_auth @protected 127\.0\.0\.1:%d \{\n\t\turi /_auth" % s.PLAYER_PORT, site), site
    assert "@protected not path /enter /auth/hop /favicon.ico" in text
    assert text.count("@protected not path /.rodeo/auth") == 2
    assert "13-61-184-227.sslip.io {" in text
    assert "hv.13-61-184-227.sslip.io {" in text and "rancher.13-61-184-227.sslip.io {" in text
    assert text.count("tls internal") == 3
    assert "admin off" in text and "skip_install_trust" in text
    # The lab already listens on :80 (nginx); Caddy must not try to.
    assert "auto_https disable_redirects" in text
    # The UIs build absolute API links from Host: it must be the browser's.
    assert text.count("header_up Host {host}") == 2
    assert "{http." not in text and "{query" not in text  # no request-chosen upstream


def test_player_config_points_tabs_at_proxy_names():
    cfg = s.player_config(_opts())
    assert cfg["listen"] == "127.0.0.1"
    assert cfg["tab_urls"] == {"8443": "https://hv.13-61-184-227.sslip.io/",
                               "30002": "https://rancher.13-61-184-227.sslip.io/"}
    assert cfg["terminal_url"] == "/terminal/"
    # The server accepts exactly these keys.
    from rodeo.player.server import PlayerConfig
    PlayerConfig(**cfg)


def test_ttyd_runs_as_student_on_loopback_with_a_fixed_shell():
    unit = s.ttyd_unit()
    assert "User=student" in unit
    assert "--interface lo" in unit and f"--port {s.TTYD_PORT}" in unit
    assert re.search(r"ExecStart=\S+ttyd .* /bin/bash -l$", unit, re.M)
    assert "--url-arg" not in unit  # the browser cannot pass arguments


def test_caddy_unit_is_unprivileged():
    unit = s.caddy_unit()
    assert "User=rodeo-player-caddy" in unit
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in unit and "NoNewPrivileges=yes" in unit


def test_install_script_verifies_pins_and_protects_the_track():
    text = s.install_script(_opts(), "/usr/local/bin/rodeo")
    assert f"echo \"{s.TTYD_SHA256}  $WORK/ttyd\" | sha256sum -c" in text
    assert "sha512sum -c" in text
    assert f"fetch -q --depth 1 https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo.git {SHA}" in text
    assert f'[ "$(git -C {s.TRACK_DIR} rev-parse HEAD)" = {SHA} ]' in text
    assert f"chmod 700 {s.TRACK_DIR}" in text
    assert "student can read the rodeo key" in text
    assert "install -m 640 -o root -g rodeo-student /root/.rodeo/harvester-kubeconfig" in text
    assert text.rstrip().endswith("echo PLAYER_UP")
    # A firewalld reload breaks the lab's DNAT access to :8443/:30002.
    assert not any("firewall-cmd" in ln and "--reload" in ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "firewall-cmd -q --add-service=https && firewall-cmd -q --permanent --add-service=https" in text
    assert subprocess.run(["bash", "-n"], input=text, text=True).returncode == 0


def test_lab_passwords_reads_secrets_without_printing(tmp_path, monkeypatch):
    sec = tmp_path / "secrets.yaml"
    sec.write_text("rancher_admin_password: r-pw\nharvester_admin_password: h-pw\nother: x\n")
    monkeypatch.setattr(s, "_secrets_files", lambda user: [tmp_path / "missing.yaml", sec])
    assert s.lab_passwords("ec2-user") == {"RANCHER_PASSWORD": "r-pw", "HARVESTER_PASSWORD": "h-pw"}


def test_setup_needs_root(monkeypatch):
    from rodeo.commands.player_cmd import player_cmd

    monkeypatch.setattr("os.geteuid", lambda: 1000)
    res = CliRunner().invoke(player_cmd, ["setup", "--track-ref", SHA, "--base", "a.example.com"])
    assert res.exit_code != 0 and "sudo" in res.output


def test_player_is_a_registered_command():
    from rodeo.cli import cli

    assert "player" in cli.commands
    assert set(cli.commands["player"].commands) == {"serve", "setup", "test", "link"}


def test_config_is_valid_json():
    json.loads(json.dumps(s.player_config(_opts())))


def test_player_unit_is_sandboxed_but_can_run_chapters():
    unit = s.player_unit("/usr/local/bin/rodeo")
    for line in ("ProtectSystem=strict", "NoNewPrivileges=yes", "CapabilityBoundingSet=",
                 "PrivateTmp=yes", "ProtectKernelModules=yes", "RestrictSUIDSGID=yes"):
        assert line in unit.splitlines(), line
    # Chapters need root's home (kubectl cache, ssh known_hosts) and the player state.
    assert f"ReadWritePaths={s.VAR} /root" in unit


def test_student_gets_the_lab_vm_key_never_roots():
    text = s.install_script(_opts(), "/usr/local/bin/rodeo")
    assert 'install -m 600 -o student -g "$SG" "$LABKEY" /home/student/.ssh/id_ed25519' in text
    assert 'cmp -s "$LABKEY" /root/.ssh/id_ed25519' in text
    assert "/root/.ssh/id_ed25519 /home/student" not in text
