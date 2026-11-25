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
    assert f"install -m 640 -o root -g rodeo-student {s.PLAYER_KUBECONFIG}" in text
    assert s.player_config(_opts())["kubeconfig"] == str(s.PLAYER_KUBECONFIG)
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


# ---------------------------------------------------------------- owned zone

PEM = ("-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
       "-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
       "-----BEGIN EC PRIVATE KEY-----\nKKK\n-----END EC PRIVATE KEY-----\n")  # gitleaks:allow (fake test key)


def _zone_opts(**kw) -> s.SetupOptions:
    return s.SetupOptions(base=s.domain_base("k7q2mx9a", "rodeocli.com"), track_ref=SHA,
                          domain="rodeocli.com", tls_pem=PEM, **kw)


def test_owned_zone_uses_flat_names_and_the_pushed_cert():
    opts = _zone_opts()
    opts.validate()
    assert opts.names == {"player": "k7q2mx9a.rodeocli.com", "harvester": "hv-k7q2mx9a.rodeocli.com",
                          "rancher": "rancher-k7q2mx9a.rodeocli.com"}
    text = s.caddyfile(opts)
    assert "tls internal" not in text
    assert text.count("tls {$CREDENTIALS_DIRECTORY}/cert {$CREDENTIALS_DIRECTORY}/key") == 3
    assert "KKK" not in text and "KKK" not in repr(opts)
    cfg = s.player_config(opts)
    assert cfg["tab_urls"]["8443"] == "https://hv-k7q2mx9a.rodeocli.com/"
    unit = s.caddy_unit(opts)
    assert f"LoadCredential=key:{s.TLS_KEY}" in unit and f"LoadCredential=cert:{s.TLS_CERT}" in unit
    assert "LoadCredential" not in s.caddy_unit(_opts())
    script = s.install_script(opts, "/usr/local/bin/rodeo")
    assert f"export CREDENTIALS_DIRECTORY={s.TLS_DIR}" in script and "KKK" not in script
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0


def test_install_tls_is_root_only(tmp_path):
    d = tmp_path / "tls"
    s.install_tls(PEM, d)
    assert (d / "cert").read_text().count("BEGIN CERTIFICATE") == 2
    assert "KKK" in (d / "key").read_text() and "CERTIFICATE" not in (d / "key").read_text()
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert {oct((d / n).stat().st_mode & 0o777) for n in ("cert", "key")} == {"0o600"}


_CERT = "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
_KEY = "-----BEGIN EC PRIVATE KEY-----\nKKK\n-----END EC PRIVATE KEY-----\n"  # gitleaks:allow (fake test key)


@pytest.mark.parametrize("pem", ["junk", _CERT, _KEY, _CERT + _KEY + _KEY])
def test_bad_tls_bundles_are_refused(pem):
    with pytest.raises(ValueError, match="TLS bundle"):
        s.split_pem(pem)


def test_label_and_domain_must_be_clean():
    with pytest.raises(ValueError):
        s.domain_base("Bad_Label", "rodeocli.com")
    with pytest.raises(ValueError, match="not under"):
        s.SetupOptions(base="x.other.com", track_ref=SHA, domain="rodeocli.com").validate()


def test_setup_cli_domain_needs_label_and_cert(monkeypatch):
    from rodeo.cli import cli

    monkeypatch.setattr("os.geteuid", lambda: 0)
    r = CliRunner().invoke(cli, ["player", "setup", "--track-ref", SHA, "--domain", "rodeocli.com"])
    assert r.exit_code != 0 and "--label" in r.output
    r = CliRunner().invoke(cli, ["player", "setup", "--track-ref", SHA, "--domain", "rodeocli.com",
                                 "--label", "k7q2mx9a"])
    assert r.exit_code != 0 and "--tls-stdin" in r.output


def test_lab_kubeconfig_prefers_the_invoking_users_home(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace

    fleet_home = tmp_path / "ec2-user"
    (fleet_home / ".rodeo").mkdir(parents=True)
    (fleet_home / ".rodeo" / "harvester-kubeconfig").write_text("apiVersion: v1\n")
    monkeypatch.setattr(pwd, "getpwnam", lambda u: SimpleNamespace(pw_dir=str(fleet_home)))
    root_home = tmp_path / "root"
    assert s.lab_kubeconfig("ec2-user", root_home) == fleet_home / ".rodeo" / "harvester-kubeconfig"
    monkeypatch.setattr(pwd, "getpwnam", lambda u: SimpleNamespace(pw_dir=str(tmp_path / "nobody")))
    with pytest.raises(RuntimeError, match="has rodeo up finished"):
        s.lab_kubeconfig("ec2-user", root_home)
    (root_home / ".rodeo").mkdir(parents=True)
    (root_home / ".rodeo" / "harvester-kubeconfig").write_text("root\n")
    assert s.lab_kubeconfig("ec2-user", root_home) == root_home / ".rodeo" / "harvester-kubeconfig"
    dest = tmp_path / "copy"
    s.install_kubeconfig(fleet_home / ".rodeo" / "harvester-kubeconfig", dest)
    assert dest.read_text() == "apiVersion: v1\n" and oct(dest.stat().st_mode & 0o777) == "0o600"


def test_root_gets_the_track_kubeconfig_only_when_missing(tmp_path):
    src = tmp_path / "ec2" / "kc"
    src.parent.mkdir()
    src.write_text("lab\n")
    dest = tmp_path / "root" / ".rodeo" / "harvester-kubeconfig"
    assert s.ensure_root_kubeconfig(src, dest) is True
    assert dest.read_text() == "lab\n" and oct(dest.stat().st_mode & 0o777) == "0o600"
    assert oct(dest.parent.stat().st_mode & 0o777) == "0o700"
    dest.write_text("root's own\n")
    assert s.ensure_root_kubeconfig(src, dest) is False
    assert dest.read_text() == "root's own\n"
    assert s.ensure_root_kubeconfig(dest, dest) is False
