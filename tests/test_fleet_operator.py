"""rodeo fleet operator: the cloud host that runs fleet commands for the laptop."""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.fleet import operator as op
from rodeo.fleet.inventory import load_inventory
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from rodeo.ssh_key import load_lab_ssh_key
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_aws_managed_sg import managed_ssh  # noqa: F401 (fixture)


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)


def _aws(fake, ip="203.0.113.9"):
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: ip)


def _sg_rules(fake, sg_id):
    out: dict[int, set[str]] = {}
    for perm in fake.security_groups[sg_id]["IpPermissions"]:
        if perm["IpRanges"]:
            out.setdefault(perm["FromPort"], set()).update(r["CidrIp"] for r in perm["IpRanges"])
    return out


def _tags(inst):
    return {t["Key"]: t["Value"] for t in inst["Tags"]}


# ---------------------------------------------------------------- provider
def test_operator_host_is_small_ssh_from_the_laptop_only_and_expires(managed_ssh, no_wait):  # noqa: F811
    captured: dict = {}
    fake = _FakeEC2WithSG()
    orig = fake.run_instances

    def spy(**kw):
        if not kw.get("DryRun"):
            captured.update(kw)
        return orig(**kw)

    fake.run_instances = spy
    host = _aws(fake).provision_operator(_provider(), workshop="demo", ssh_user="root")
    assert captured["InstanceType"] == "t3.small" and "CpuOptions" not in captured
    tags = _tags(fake.instances[host.provider_id])
    assert tags["rodeo-role"] == "operator"
    assert "rodeo-expires-at" in tags  # the dead-man switch, like every rodeo instance
    sg = captured["NetworkInterfaces"][0]["Groups"][0]
    assert _sg_rules(fake, sg) == {22: {"203.0.113.9/32"}}  # nothing open to the world
    p = _aws(fake)
    identity = p.identity_tags(_provider(), load_lab_ssh_key("demo"))
    assert p._find_managed_sg(fake, vpc_id="vpc-1", identity=identity) is None  # not the lab SG


def test_operator_sg_follows_the_laptop_and_is_reused(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    a = _aws(fake).provision_operator(_provider(), workshop="demo", ssh_user="root")
    b = _aws(fake, ip="198.51.100.7").provision_operator(_provider(), workshop="demo", ssh_user="root")
    assert a.provider_id == b.provider_id and len(fake.instances) == 1
    sgs = [sid for sid, g in fake.security_groups.items()
           if {"Key": "rodeo-resource", "Value": "operator-security-group"} in g.get("Tags", [])]
    assert len(sgs) == 1 and _sg_rules(fake, sgs[0]) == {22: {"198.51.100.7/32"}}


def test_lab_teardown_leaves_the_operator_host_which_has_its_own(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="root", wait_ssh=False), _provider())
    oper = p.provision_operator(_provider(), workshop="demo", ssh_user="root")
    results = p.deprovision(ProvisionSpec(workshop="demo", host_ids=[], ssh_user="root", wait_ssh=False),
                            _provider())
    assert oper.provider_id not in fake.terminated
    assert any(r.id == "security-group" and r.detail == "deleted" for r in results)
    assert not any(r.id == "key-pair" for r in results)  # the operator still uses it
    out = p.deprovision_operator(_provider(), workshop="demo")
    assert oper.provider_id in fake.terminated
    assert [(r.id, r.detail) for r in out] == [
        ("operator", "terminating"), ("operator-security-group", "deleted"), ("key-pair", "deleted")]


def test_lab_sg_keeps_the_laptop_when_the_operator_runs(managed_ssh, no_wait, monkeypatch):  # noqa: F811
    fake = _FakeEC2WithSG()
    monkeypatch.setenv("RODEO_ALSO_ALLOW_IP", "81.2.69.142")
    p = _aws(fake, ip="203.0.113.50")  # the operator host is the caller
    p.provision(ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="root", wait_ssh=False), _provider())
    identity = p.identity_tags(_provider(), load_lab_ssh_key("demo"))
    sg = p._find_managed_sg(fake, vpc_id="vpc-1", identity=identity)
    assert _sg_rules(fake, sg)[22] == {"203.0.113.50/32", "81.2.69.142/32"}
    for bad in ("10.0.0.1", "198.51.100.7", "0.0.0.0", "81.2.69.0/24", "evil"):
        monkeypatch.setenv("RODEO_ALSO_ALLOW_IP", bad)
        from rodeo.providers.aws import _also_allowed
        assert _also_allowed() == set(), bad


# ---------------------------------------------------------------- push / pull / run
def _workshop(tmp_path: Path, player: bool = False) -> Path:
    d = tmp_path / "ws"
    d.mkdir()
    raw = {"name": "demo", "defaults": {"ssh_user": "ec2-user"}, "lab": {"dir": "/root/lab"},
           "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1"}, "hosts": []}
    if player:
        raw["player"] = {"domain": "labs.example.com"}
    (d / "workshop.yaml").write_text(yaml.safe_dump(raw))
    (d / "workshop.job.yaml").write_text("workshop: demo\n")
    (d / ".DS_Store").write_text("x")
    return d / "workshop.yaml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".rodeo" / "ssh" / "labs" / "demo").mkdir(parents=True)
    (h / ".rodeo" / "ssh" / "labs" / "demo" / "id_ed25519").write_text("PRIVATE")
    (h / ".rodeo" / "fleet" / "demo").mkdir(parents=True)
    (h / ".rodeo" / "fleet" / "demo" / "known_hosts").write_text("kh")
    (h / ".rodeo" / "fleet" / "demo" / "operator.yaml").write_text("ssh: 1.2.3.4\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr("rodeo.paths.invoking_home", lambda: h)
    return h


def test_push_sends_the_workshop_key_and_known_hosts_only(tmp_path, home):
    inv_path = _workshop(tmp_path)
    data = op.push_archive(load_inventory(inv_path), inv_path)
    names = set(tarfile.open(fileobj=io.BytesIO(data)).getnames())
    assert "rodeo-operator/demo/workshop.yaml" in names
    assert "rodeo-operator/demo/workshop.job.yaml" in names
    assert ".rodeo/ssh/labs/demo/id_ed25519" in names
    assert ".rodeo/fleet/demo/known_hosts" in names
    assert ".rodeo/fleet/demo/operator.yaml" not in names
    assert not any(n.endswith(".DS_Store") for n in names)


def test_push_adds_the_event_certificate_for_a_player_workshop(tmp_path, home):
    inv_path = _workshop(tmp_path, player=True)
    assert ".rodeo/certs/labs.example.com" in op.state_dirs(load_inventory(inv_path))


def _remote_tar(entries: dict[str, bytes], links: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(body), 0o644
            tar.addfile(info, io.BytesIO(body))
        for name in links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(info)
    return buf.getvalue()


def test_pull_writes_only_what_it_asked_for_and_backs_up_first(tmp_path, home, monkeypatch):
    inv_path = _workshop(tmp_path)
    inv = load_inventory(inv_path)
    payload = _remote_tar({
        "rodeo-operator/demo/workshop.yaml": b"name: demo\nhosts: [new]\n",
        ".rodeo/ssh/labs/demo/id_ed25519": b"PRIVATE",
        ".rodeo/ssh/labs/other/id_ed25519": b"NOT MINE",
        "../escape": b"x",
        "/etc/cron.d/evil": b"x",
    }, links=("rodeo-operator/demo/link",))
    monkeypatch.setattr(op, "_ssh", lambda *a, **k: subprocess.CompletedProcess([], 0, payload, b""))
    written = op.pull(inv, inv_path)
    assert inv_path.read_text() == "name: demo\nhosts: [new]\n"
    assert not (home / ".rodeo" / "ssh" / "labs" / "other").exists()
    assert not (tmp_path / "escape").exists() and not (inv_path.parent / "link").exists()
    assert any("backup of the local copy" in w for w in written)
    backup = next((home / ".rodeo" / "fleet" / "demo" / "backups").glob("*.tar.gz"))
    assert "ws/workshop.yaml" in tarfile.open(backup).getnames()
    assert oct((home / ".rodeo" / "ssh" / "labs" / "demo" / "id_ed25519").stat().st_mode & 0o777) == "0o600"


def test_run_starts_a_unit_with_the_pushed_workshop_and_no_prompts(tmp_path, home):
    inv_path = _workshop(tmp_path)
    inv = load_inventory(inv_path)
    script = op.run_command(inv, inv_path, ["up", "--track-ref", "a" * 40, "--images-bucket", "b; rm -rf /"],
                            laptop_ip="81.2.69.142")
    assert "systemd-run --unit rodeo-operator-run" in script
    assert "--setenv RODEO_ALSO_ALLOW_IP=81.2.69.142" in script
    assert "rodeo fleet up -f \"$HOME\"/rodeo-operator/demo/workshop.yaml" in script
    assert "'b; rm -rf /'" in script  # every argument quoted
    assert "is-active -q rodeo-operator-run" in script  # one run at a time
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0
    for bad in (["-f", "x.yaml", "up"], ["up", "--file=x"], ["operator", "down"], []):
        with pytest.raises(ConfigError):
            op.run_command(inv, inv_path, bad)
    with pytest.raises(ValueError):
        op.run_command(inv, inv_path, ["status"], laptop_ip="not-an-ip")


def test_status_reads_the_exit_file():
    assert op.status_text("running\n") == "running"
    assert op.status_text("0\n") == "done"
    assert op.status_text("3\n") == "failed (exit 3)"
    assert op.status_text("") == "none"


def test_login_sets_the_region_and_uses_remote_login(tmp_path, home):
    cmd = op.login_command(load_inventory(_workshop(tmp_path)))
    assert cmd.startswith("/usr/local/bin/aws configure set region eu-north-1 && /usr/local/bin/aws login --remote")


def test_bootstrap_installs_rodeo_and_a_signature_checked_aws_cli(tmp_path, home):
    script = op.bootstrap_script(load_inventory(_workshop(tmp_path)))
    assert "install_cli" in script and "OPERATOR_READY" in script
    assert "grep -q '^aws-cli/2'" in script  # the distro's v1 has no `aws login`
    assert "export RODEO_MODE=control-plane" in script and "import boto3" in script
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0


def test_operator_commands_are_registered():
    from click.testing import CliRunner

    from rodeo.commands.fleet_cmd import fleet_cmd

    out = CliRunner().invoke(fleet_cmd, ["operator", "--help"]).output
    for word in ("up", "login", "push", "run", "logs", "status", "pull", "down"):
        assert f"  {word} " in out


def test_operator_ignores_the_labs_instance_size_rules(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    cfg = {**_provider(), "instance_type": "m7i.large"}  # too small for a nested lab
    host = _aws(fake).provision_operator(cfg, workshop="demo", ssh_user="root")
    assert _aws(fake).deprovision_operator(cfg, workshop="demo")[0].provider_id == host.provider_id
