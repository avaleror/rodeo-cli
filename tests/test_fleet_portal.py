"""Claim portal laptop side (F5.2-F5.4): portal VM lifecycle, inventory, publish."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.fleet import portal as fp
from rodeo.fleet.inventory import STUDENT_SSH_PORT, load_inventory
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.ssh_key import load_lab_ssh_key
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_aws_managed_sg import managed_ssh  # noqa: F401 (fixture)


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)


def _aws(fake):
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")


def _sg_rules(fake, sg_id):
    out: dict[int, set[str]] = {}
    for perm in fake.security_groups[sg_id]["IpPermissions"]:
        out.setdefault(perm["FromPort"], set()).update(r["CidrIp"] for r in perm["IpRanges"])
    return out


def _tags(inst):
    return {t["Key"]: t["Value"] for t in inst["Tags"]}


# ---------------------------------------------------------------- AWS portal VM
def test_portal_vm_gets_own_sg_small_type_and_no_rodeo_key(managed_ssh, no_wait, monkeypatch):  # noqa: F811
    planted: list[str] = []
    monkeypatch.setattr("rodeo.providers.aws.plant_rodeo_ssh_key", lambda inv, fh: planted.append(fh.id))
    captured: dict = {}
    fake = _FakeEC2WithSG()
    orig = fake.run_instances

    def spy(**kw):
        if not kw.get("DryRun"):
            captured.update(kw)
        return orig(**kw)

    fake.run_instances = spy
    host = _aws(fake).provision_portal(_provider(), workshop="demo", ssh_user="root")
    assert planted == [], "the portal must never receive the fleet-wide rodeo private key"
    assert captured["InstanceType"] == "t3.small"
    assert "CpuOptions" not in captured
    inst = fake.instances[host.provider_id]
    assert _tags(inst)["rodeo-role"] == "portal" and _tags(inst)["rodeo-host-id"] == "portal"
    sg = captured["NetworkInterfaces"][0]["Groups"][0]
    assert _sg_rules(fake, sg) == {22: {"203.0.113.9/32"}, 80: {"0.0.0.0/0"}, 443: {"0.0.0.0/0"}}
    # the lab SG lookup must never return the portal SG (R4)
    p = _aws(fake)
    identity = p.identity_tags(_provider(), load_lab_ssh_key("demo"))
    assert p._find_managed_sg(fake, vpc_id="vpc-1", identity=identity) is None


def test_portal_provision_is_idempotent(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    a = _aws(fake).provision_portal(_provider(), workshop="demo", ssh_user="root")
    b = _aws(fake).provision_portal(_provider(), workshop="demo", ssh_user="root")
    assert a.provider_id == b.provider_id and len(fake.instances) == 1


def test_lab_deprovision_never_terminates_portal_and_still_deletes_lab_sg(managed_ssh, no_wait):  # noqa: F811
    """R1: the portal must neither be killed by a lab deprovision nor keep the lab SG alive."""
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="root", wait_ssh=False), _provider())
    portal = p.provision_portal(_provider(), workshop="demo", ssh_user="root")
    results = p.deprovision(ProvisionSpec(workshop="demo", host_ids=[], ssh_user="root", wait_ssh=False), _provider())
    assert portal.provider_id not in fake.terminated
    assert any(r.id == "security-group" and r.detail == "deleted" for r in results)
    out = p.deprovision_portal(_provider(), workshop="demo")
    assert portal.provider_id in fake.terminated
    # The lab key stays while the portal lives (it was launched with it) and
    # goes with it: portal SG deleted, then the lab key pair.
    assert not any(r.id == "key-pair" for r in results)
    assert [(r.id, r.detail) for r in out] == [
        ("portal", "terminating"), ("portal-security-group", "deleted"), ("key-pair", "deleted"),
    ]


def test_student_access_never_opens_ssh(managed_ssh, no_wait):  # noqa: F811
    """Students come in through the player only: neither :22 (the rodeo key logs
    in there) nor the old student SSH port ever opens to the internet."""
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="root", wait_ssh=False), _provider())
    for ports in ((22, 8443), (STUDENT_SSH_PORT, 443)):
        with pytest.raises(ConfigError, match="cannot be opened"):
            p.set_student_access(_provider(), workshop="demo", open_ports=ports)


def test_open_access_closes_ssh_rules_left_by_older_releases(managed_ssh, no_wait):  # noqa: F811
    """Older releases opened :22 (v0.18-0.19) or the student SSH port to the world.
    Running open-access with this release takes both away."""
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="root", wait_ssh=False), _provider())
    sg = next(g["GroupId"] for g in fake.security_groups.values()
              if g["GroupName"].startswith("rodeo-demo-") and not g["GroupName"].endswith("-portal"))
    for port in (22, STUDENT_SSH_PORT):
        fake.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": port, "ToPort": port, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    p.set_student_access(_provider(), workshop="demo", open_ports=(443,))
    rules = _sg_rules(fake, sg)
    assert rules[22] == {"203.0.113.9/32"}
    assert not rules.get(STUDENT_SSH_PORT)
    assert rules[443] == {"203.0.113.9/32", "0.0.0.0/0"}


# ---------------------------------------------------------------- inventory
def _write(tmp_path, portal: dict, student_access="open", roster: str | None = None, player=None) -> Path:
    if roster is not None:
        (tmp_path / "students.csv").write_text(roster)
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "harvester"},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m8id.8xlarge", "student_access": student_access},
        "hosts": [], "portal": portal,
    }
    if player is not None:
        data["player"] = player
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_inventory_portal_defaults_and_fqdn(tmp_path):
    inv = load_inventory(_write(tmp_path, {"enabled": True}))
    assert inv.portal.mode == "both"
    assert inv.portal.fqdn is None
    fp_ = tmp_path / "workshop.yaml"
    from rodeo.fleet.inventory import merge_portal

    merge_portal(fp_, ssh="1.2.3.4", public_ip="1.2.3.4", provider_id="i-1")
    inv = load_inventory(fp_)
    assert inv.portal.fqdn == "portal-1-2-3-4.sslip.io" and inv.portal.provider_id == "i-1"
    assert inv.hosts == []  # portal never joins hosts[] (R6)


@pytest.mark.parametrize("portal,access,roster,msg", [
    ({"mode": "weird"}, "open", None, "portal.mode"),
    ({"mode": "roster"}, "open", None, "needs portal.roster"),
    ({"roster": "missing.csv"}, "open", None, "not found"),
    ({"enabled": True}, "operator", None, "student_access: open"),
])
def test_inventory_portal_validation_fails_closed(tmp_path, portal, access, roster, msg):
    with pytest.raises(ConfigError, match=msg):
        load_inventory(_write(tmp_path, portal, student_access=access, roster=roster))


def test_portal_host_id_is_reserved(tmp_path):
    p = _write(tmp_path, {"enabled": True})
    data = yaml.safe_load(p.read_text())
    data["hosts"] = [{"id": "portal", "ssh": "1.2.3.4"}]
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="reserved"):
        load_inventory(p)


# ---------------------------------------------------------------- publish
SECRET = "Sup3rSecretPassw0rdXY"  # gitleaks:allow (fake fixture for the leak tests)


def _publish_env(tmp_path, monkeypatch, *, ready: bool, secrets_ok=True, player=False):
    # player=False: a fleet without the student UI publishes URLs and passwords.
    p = _write(tmp_path, {"enabled": True}, player=None if player else False)
    data = yaml.safe_load(p.read_text())
    data["hosts"] = [{"id": "student-01", "ssh": "10.0.0.1", "public_ip": "10.0.0.1"}]
    data["portal"].update({"ssh": "10.9.9.9", "public_ip": "10.9.9.9"})
    p.write_text(yaml.safe_dump(data))
    inv = load_inventory(p)
    pushed: dict = {}

    class R:
        def __init__(self, ok, reason=None):
            self.ready, self.reason = ok, reason

    monkeypatch.setattr(fp, "_check_host", lambda *a, **k: R(ready, None if ready else "deploy not finished"))

    def fake_remote(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        if host.id == "portal":
            pushed["argv"], pushed["stdin"] = argv, stdin
            return RemoteResult(host.id, 0, '{"imported": 1}\n', "")
        if argv[:2] == ["python3", "-c"]:
            out = json.dumps({"harvester_admin_password": SECRET, "rancher_admin_password": SECRET})
            return RemoteResult(host.id, 0 if secrets_ok else 1, out if secrets_ok else SECRET, "boom")
        if argv[:2] == ["bash", "-c"]:  # the player link probe
            return RemoteResult(host.id, 0, "https://lab1.example.org/enter?t=" + "T" * 32 + "\n", "")
        raise AssertionError(argv)

    monkeypatch.setattr(fp, "run_remote", fake_remote)
    monkeypatch.setattr("rodeo.paths.rodeo_dir", lambda: tmp_path / "rodeo")
    return inv, pushed


def test_publish_ready_lab_carries_urls_and_passwords_via_stdin(tmp_path, monkeypatch):
    inv, pushed = _publish_env(tmp_path, monkeypatch, ready=True)
    rows = fp.portal_publish(inv)
    assert rows[0].ready and rows[0].reason is None
    assert SECRET not in " ".join(pushed["argv"]), "secrets must never travel on argv"
    lab = json.loads(pushed["stdin"])[0]
    urls = {c["url"] for c in lab["data"]["components"]}
    assert urls == {"https://10.0.0.1:8443", "https://10.0.0.1:30002"}
    assert "ssh" not in lab["data"]


def test_publish_not_ready_lab_is_building_without_credentials(tmp_path, monkeypatch):
    inv, pushed = _publish_env(tmp_path, monkeypatch, ready=False)
    rows = fp.portal_publish(inv)
    assert not rows[0].ready and "deploy not finished" in rows[0].reason
    assert json.loads(pushed["stdin"]) == [{"id": "student-01", "ready": False, "data": {}, "ord": 0}]


def test_publish_error_never_leaks_secrets(tmp_path, monkeypatch):
    """R8: a failing secrets read must not surface its stdout anywhere."""
    inv, pushed = _publish_env(tmp_path, monkeypatch, ready=True, secrets_ok=False)
    rows = fp.portal_publish(inv)
    assert not rows[0].ready and SECRET not in (rows[0].reason or "")
    assert SECRET not in pushed["stdin"]


def test_publish_with_the_player_gives_only_the_link(tmp_path, monkeypatch):
    inv, pushed = _publish_env(tmp_path, monkeypatch, ready=True, player=True)
    rows = fp.portal_publish(inv)
    lab = json.loads(pushed["stdin"])[0]
    assert rows[0].ready
    assert lab["data"] == {"player_url": "https://lab1.example.org/enter?t=" + "T" * 32}
    assert SECRET not in pushed["stdin"], "with the player, no UI password reaches the portal"


def test_student_setup_script_proves_the_user_is_unprivileged():
    s = fp.STUDENT_SETUP_SCRIPT
    assert "sudo -n -l -U" in s and "exit 3" in s
    # SLES 16 lets every user sudo with root's password (targetpw): deny explicitly,
    # last, validated, and check real commands instead of grepping "may run".
    assert "ALL=(ALL) !ALL" in s and "/etc/sudoers.d/zz-rodeo-student" in s
    assert "visudo -cqf" in s and "/usr/bin/cloudguestregistryauth" in s
    assert 'grep -q "may run"' not in s
    assert "test -r /root/.ssh/id_ed25519" in s and "chmod 700 /root" in s
    for g in ("wheel", "libvirt", "docker"):
        assert g in s


def test_student_setup_script_gives_no_ssh_and_removes_the_old_student_sshd():
    s = fp.STUDENT_SETUP_SCRIPT
    assert "rm -f \"$H/.ssh/authorized_keys\"" in s  # nobody logs in as the student over SSH
    assert "systemctl disable -q --now rodeo-student-sshd" in s
    assert "--remove-port=2222/tcp" in s
    assert "ExecStart" not in s and "AllowUsers" not in s  # no sshd is set up any more
    # The main sshd on :22 is never touched.
    assert "/etc/ssh/sshd_config" not in s.replace("/etc/ssh/rodeo-student-sshd_config", "")


# ---------------------------------------------------------------- invite + install
def test_invite_writes_private_csv_and_keeps_old_links(tmp_path, monkeypatch):
    p = _write(tmp_path, {"enabled": True, "mode": "roster", "roster": "students.csv"},
               roster="name,email\nAna,ana@x.io\nBo,bo@x.io\n")
    data = yaml.safe_load(p.read_text())
    data["portal"].update({"ssh": "1.2.3.4", "public_ip": "1.2.3.4"})
    p.write_text(yaml.safe_dump(data))
    inv = load_inventory(p)
    answers = iter([
        [{"email": "ana@x.io", "name": "Ana", "lab": "s1", "token": "T" * 32, "status": "invited"},
         {"email": "bo@x.io", "name": "Bo", "lab": "s2", "token": "U" * 32, "status": "invited"}],
        [{"email": "ana@x.io", "name": "Ana", "lab": "s1", "token": None, "status": "existing"},
         {"email": "bo@x.io", "name": "Bo", "lab": "s2", "token": None, "status": "existing"}],
    ])
    monkeypatch.setattr(fp, "portal_admin", lambda *a, **k: next(answers))
    out, _ = fp.portal_invite(inv, p)
    fp.portal_invite(inv, p)
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["url"] == "https://portal-1-2-3-4.sslip.io/l/" + "T" * 32


def test_portal_up_script_pins_caddy_and_ships_the_package():
    s = fp.portal_up_script("portal-1-2-3-4.sslip.io", mode="both", title="T'x")
    assert fp.CADDY_SHA512 in s and "sha512sum -c" in s
    assert "rodeo_portal" in s and "ProtectSystem=strict" in s
    assert "portal-1-2-3-4.sslip.io {" in s
    assert "--title 'T'\"'\"'x'" in s  # shell-quoted
    assert "id_ed25519" not in s  # no key material ever goes to the portal



# ---------------------------------------------------------------- security review fixes
@pytest.mark.parametrize("hid", ["../../etc", "a b", "x" * 64, "-lead", 'q"'])
def test_unsafe_host_ids_fail_closed(tmp_path, hid):
    p = _write(tmp_path, {"enabled": True})
    data = yaml.safe_load(p.read_text())
    data["hosts"] = [{"id": hid, "ssh": "1.2.3.4"}]
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="must be letters"):
        load_inventory(p)


@pytest.mark.parametrize("name", ["labs.example.com\n}", "not a host", "x", "a..b.com"])
def test_invalid_portal_hostname_fails_closed(tmp_path, name):
    with pytest.raises(ConfigError, match="hostname"):
        load_inventory(_write(tmp_path, {"enabled": True, "hostname": name}))


@pytest.mark.parametrize("letters,ok", [(4, True), (8, True), (3, False), (9, False), ("x", False)])
def test_code_letters_range(tmp_path, letters, ok):
    p = _write(tmp_path, {"enabled": True, "code_letters": letters})
    if ok:
        assert load_inventory(p).portal.code_letters == letters
    else:
        with pytest.raises(ConfigError, match="code_letters"):
            load_inventory(p)


def test_code_letters_default_is_six(tmp_path):
    assert load_inventory(_write(tmp_path, {"enabled": True})).portal.code_letters == 6


def test_portal_up_script_uses_private_temp_dir_and_code_letters():
    s = fp.portal_up_script("portal-1-2-3-4.sslip.io", mode="open", title="T", code_letters=6)
    assert "mktemp -d" in s and "/tmp/" not in s
    assert "--code-letters 6" in s


# ---------------------------------------------------------------- live bug 2026-09-30
def test_secrets_path_follows_sudo_user_like_invoking_home(tmp_path, monkeypatch):
    """Under `sudo -n -H` HOME is /root but rodeo keeps state in SUDO_USER's home
    (rodeo.paths.invoking_home). The remote scripts must look in the same place."""
    import collections
    import sys
    import types

    from rodeo.fleet.student_access import SECRETS_PATH_SNIPPET

    ns: dict = {}
    exec(SECRETS_PATH_SNIPPET, ns)  # noqa: S102 - the exact code shipped to hosts
    P = collections.namedtuple("P", "pw_dir")
    fake_pwd = types.SimpleNamespace(
        getpwnam=lambda n: P("/home/ec2-user") if n == "ec2-user" else (_ for _ in ()).throw(KeyError(n))
    )
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUDO_USER", "ec2-user")
    assert ns["_secrets_path"]() == "/home/ec2-user/.rodeo/secrets.yaml"
    monkeypatch.setenv("SUDO_USER", "no-such-user")
    assert ns["_secrets_path"]() == f"{tmp_path}/.rodeo/secrets.yaml"
    monkeypatch.delenv("SUDO_USER")
    assert ns["_secrets_path"]() == f"{tmp_path}/.rodeo/secrets.yaml"


@pytest.mark.parametrize("which", ["check", "read"])
def test_remote_secret_scripts_still_run(tmp_path, which):
    import subprocess
    import sys

    from rodeo.fleet.student_access import PASSWORD_CHECK_SCRIPT

    (tmp_path / ".rodeo").mkdir()
    (tmp_path / ".rodeo" / "secrets.yaml").write_text('harvester_admin_password: "Str0ngEnoughPassw0rdX"\n')  # gitleaks:allow (fake)
    script = PASSWORD_CHECK_SCRIPT if which == "check" else fp.READ_SECRETS_SCRIPT
    out = subprocess.run([sys.executable, "-c", script, "harvester_admin_password"],
                         capture_output=True, text=True, check=True,
                         env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}).stdout
    assert out.strip() == ("harvester_admin_password=strong" if which == "check"
                           else '{"harvester_admin_password": "Str0ngEnoughPassw0rdX"}')  # gitleaks:allow


def test_package_ships_brand_assets_and_font_licences():
    import base64
    import io
    import tarfile

    names = tarfile.open(fileobj=io.BytesIO(base64.b64decode(fp._package_b64()))).getnames()
    for n in ("suse-logo.png", "open-sans.woff2", "source-sans-pro-400.woff2",
              "OFL-OpenSans.txt", "OFL-SourceSansPro.txt"):
        assert f"rodeo_portal/static/{n}" in names


@pytest.mark.parametrize("url,ok", [
    ("https://avaleror.github.io/suse-virt-workshop/", True), ("/guide/", True), ("", True),
    ("javascript:alert(1)", False), ("http://x.io", False), ("//evil.example", False),
])
def test_guide_url_validation(tmp_path, url, ok):
    p = _write(tmp_path, {"enabled": True, "guide_url": url})
    if ok:
        assert load_inventory(p).portal.guide_url == url
    else:
        with pytest.raises(ConfigError, match="guide_url"):
            load_inventory(p)


def test_portal_up_passes_guide_url_quoted():
    s = fp.portal_up_script("p.sslip.io", mode="open", title="T",
                            guide_url="https://avaleror.github.io/suse-virt-workshop/")
    assert "--guide-url https://avaleror.github.io/suse-virt-workshop/" in s
    assert "--guide-url ''" in fp.portal_up_script("p.sslip.io", mode="open", title="T")


def test_publish_command_prints_its_table(tmp_path, monkeypatch):
    """Found live 2026-11-26: the table still read a student SSH column."""
    from click.testing import CliRunner

    from rodeo.commands.fleet_portal_cmd import portal_publish_cmd

    inv, _ = _publish_env(tmp_path, monkeypatch, ready=True, player=True)
    monkeypatch.setattr("rodeo.commands.fleet_portal_cmd.load_inventory", lambda p: inv)
    res = CliRunner().invoke(portal_publish_cmd, ["-f", str(tmp_path / "workshop.yaml")])
    assert res.exit_code == 0, res.output
    assert "student-01" in res.output and "ready" in res.output and "ssh" not in res.output.lower()
