"""lab-in-a-box platform: host helpers, lab.json rendering, phases, smlm-workshop profile."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from rodeo import labinabox_host as host
from rodeo.config import ConfigError, load_config
from rodeo.engine import labinabox_phase as phase
from rodeo.engine.runner import DeployRunner, LogLine
from rodeo.labinabox import available_addons, build_lab_json, unresolved_placeholders
from rodeo.labseed import PROFILE_EXAMPLE, example_dir, seed_lab
from rodeo.preflight import _resource_needs
from rodeo.profiles import get_profile

SMLM = example_dir("smlm-workshop")
SHA = "a" * 64
_SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "labinabox_schema" / "schema.json").read_text())


def _smlm_cfg(tmp_path: Path, secrets: dict | None = None) -> dict:
    lab = tmp_path / "lab"
    shutil.copytree(SMLM, lab)
    rodeo = tmp_path / ".rodeo"
    rodeo.mkdir(exist_ok=True)
    (rodeo / "secrets.yaml").write_text(yaml.safe_dump(secrets or {}))
    return load_config(lab / "rodeo-plan.yaml")


FULL_SECRETS = {
    "sles15sp5_image_url": "file:///srv/images/sp5.qcow2",
    "sles15sp5_image_sha256": "c" * 64,
    "sles15sp6_image_url": "https://mirror.example/sp6.qcow2",
    "sles15sp6_image_sha256": "d" * 64,
    "universal_pwd": "Universal123",
    "smlm_admin_password": "Admin123abc",
    "smlm_image_admin_pass": "Baked123abc",
    "smlm_image_url": "https://images.example/smlm.qcow2",
    "smlm_image_sha256": SHA,
}


@pytest.fixture(autouse=True)
def _root_ssh_in_tmp(tmp_path, monkeypatch):
    """Point the phase's /root/.ssh key paths into tmp_path."""
    ssh = tmp_path / "root-ssh"
    monkeypatch.setattr(phase, "_ROOT_KEY", ssh / "id_rsa")
    monkeypatch.setattr(phase, "_ROOT_ED25519_KEY", ssh / "id_ed25519")
    monkeypatch.setattr(phase, "_AUTHORIZED_KEYS", ssh / "authorized_keys")


# ── labinabox_host: pure helpers ───────────────────────────────────────────

def test_source_defaults_and_validation():
    assert host.source({}) == (host.LIAB_REPO, host.LIAB_REF)
    assert host.source({"lab_in_a_box": {"source": {"ref": "dev"}}})[1] == "dev"
    with pytest.raises(ConfigError):
        host.source({"lab_in_a_box": {"source": {"ref": "../etc"}}})
    with pytest.raises(ConfigError):
        host.source({"lab_in_a_box": {"source": {"ref": "main; rm -rf /"}}})


_LS_REMOTE = (
    "aaa\trefs/tags/1.9.2\n"
    "bbb\trefs/tags/1.10.0\n"
    "ccc\trefs/tags/1.9.4\n"
    "ddd\trefs/tags/nightly\n"
    "eee\trefs/tags/2.0.0-rc1\n"
)


def test_latest_tag_is_the_highest_version_not_the_last_string():
    assert host.latest_tag(_LS_REMOTE) == "1.10.0"
    assert host.latest_tag("aaa\trefs/tags/v1.2\nbbb\trefs/tags/v1.11\n") == "v1.11"
    assert host.latest_tag("aaa\trefs/tags/nightly\n") is None


def test_default_ref_is_latest_and_resolves_to_a_tag():
    assert host.source({})[1] == host.LIAB_LATEST
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return _Completed(stdout=_LS_REMOTE)

    assert host.resolve_ref("https://x/lab-in-a-box", "latest", run=run) == "1.10.0"
    assert calls == [["git", "ls-remote", "--tags", "--refs", "https://x/lab-in-a-box"]]
    assert host.resolve_ref("https://x/lab-in-a-box", "1.9.2", run=run) == "1.9.2"
    assert len(calls) == 1


def test_resolve_latest_fails_clearly():
    with pytest.raises(ConfigError, match="source.ref"):
        host.resolve_ref("https://x", "latest", run=lambda *a, **k: _Completed(128, stderr="unreachable"))
    with pytest.raises(ConfigError, match="no version tags"):
        host.resolve_ref("https://x", "latest", run=lambda *a, **k: _Completed(stdout="a\trefs/tags/x\n"))


def test_source_env_overrides_beat_the_plan(monkeypatch):
    plan = {"lab_in_a_box": {"source": {"repo": "https://example/plan", "ref": "plan-ref"}}}
    monkeypatch.setenv("RODEO_LABINABOX_REPO", "https://github.com/fork/lab-in-a-box")
    assert host.source(plan) == ("https://github.com/fork/lab-in-a-box", "plan-ref")
    monkeypatch.setenv("RODEO_LABINABOX_REF", "feat/x")
    assert host.source(plan) == ("https://github.com/fork/lab-in-a-box", "feat/x")
    assert host.source({})[1] == "feat/x"
    monkeypatch.setenv("RODEO_LABINABOX_REF", "--upload-pack=x")
    with pytest.raises(ConfigError):
        host.source({})


def test_fetch_commands_are_shallow_ipv4_and_pinned(tmp_path):
    cmds = host.fetch_commands("https://example/repo", "abc123", tmp_path)
    fetch = next(c for c in cmds if "fetch" in c)
    assert "--ipv4" in fetch and "--depth" in fetch and fetch[-2:] == ["https://example/repo", "abc123"]
    assert cmds[-1][-1] == "FETCH_HEAD"


def test_install_command_is_scripts_only(tmp_path):
    cmd = host.install_command(tmp_path, "python3.13")
    assert "_webui_mode=off" in cmd and "_backup=off" in cmd
    assert "_python_bin=python3.13" in cmd
    assert cmd[-1] == str(tmp_path / "install_automation_node_scripts.sh")


def test_lab_creation_cfg_is_marked_and_credential_free():
    text = host.render_lab_creation_cfg("192.168.122.1", "ssh-rsa AAAA root@host\n")
    assert host.cfg_is_rodeo_managed(text)
    assert "REMOTE_HOST=192.168.122.1" in text
    assert "VIRT_SRV='qemu:///system'" in text
    assert "ROOT_SSH_KEY='ssh-rsa AAAA root@host'" in text
    for forbidden in ("regcode", "ROOT_PWD_HASH", "rancher_initial_pwd", "PASS"):
        assert forbidden not in text
    assert not host.cfg_is_rodeo_managed("REMOTE_HOST=nuc1\n")


def test_dns_and_network_xml():
    lab = {"nodes": {"smlm.rodeo.lab": {"myip": "192.168.122.20", "mymac": "02:00:00:5A:00:20"}}}
    assert host.dns_host_entries(lab) == [("192.168.122.20", ["smlm.rodeo.lab", "smlm"])]
    cmd = host.net_update_command("default", "192.168.122.20", ["smlm.rodeo.lab", "smlm"])
    assert cmd[:6] == ["virsh", "-c", "qemu:///system", "net-update", "default", "add-last"]
    assert "<hostname>smlm.rodeo.lab</hostname>" in cmd[7]
    xml = host.render_network_xml(
        {"name": "default", "bridge": "virbr0", "cidr": "192.168.122.0/24",
         "gateway": "192.168.122.1", "domain": "rodeo.lab"}, lab)
    assert "<host ip='192.168.122.20'><hostname>smlm.rodeo.lab</hostname>" in xml
    assert "<host mac='02:00:00:5A:00:20' name='smlm' ip='192.168.122.20'/>" in xml
    assert "netmask='255.255.255.0'" in xml


def test_base_images_validation():
    ok = {"lab_in_a_box": {"images": [
        {"name": "a.qcow2", "url": "https://x/a.qcow2", "sha256": SHA.upper()},
        {"name": "b.img", "url": "https://x/b.img", "sha256_url": "https://x/SUMS"},
    ]}}
    images = host.base_images(ok)
    assert images[0]["sha256"] == SHA and images[1]["sha256_url"] == "https://x/SUMS"
    bad = [
        {"name": "../a", "url": "https://x", "sha256": SHA},
        {"name": "a", "url": "ftp://x", "sha256": SHA},
        {"name": "a", "url": "https://x", "sha256": "nothex"},
        {"name": "a", "url": "https://x"},
        {"name": "a", "url": "??smlm_image_url", "sha256": SHA},
    ]
    for entry in bad:
        with pytest.raises(ConfigError):
            host.base_images({"lab_in_a_box": {"images": [entry]}})


def test_unresolved_image_secret_names_the_secret():
    with pytest.raises(ConfigError, match=r"\?\?smlm_image_url"):
        host.base_images({"lab_in_a_box": {"images": [
            {"name": "a", "url": "??smlm_image_url", "sha256": SHA}]}})


def test_forward_port_commands_resolve_lab_nodes():
    lab = {"nodes": {"smlm.rodeo.lab": {"myip": "192.168.122.20"}}}
    cmds = host.forward_port_commands(
        [{"name": "web", "host_port": 443, "guest_port": 443, "target": "smlm"}], lab)
    assert cmds[1][-1] == "--add-forward-port=port=443:proto=tcp:toport=443:toaddr=192.168.122.20"
    with pytest.raises(ConfigError, match="not a lab node"):
        host.forward_port_commands([{"name": "x", "host_port": 1, "guest_port": 1, "target": "nope"}], lab)


def test_local_override(monkeypatch, tmp_path):
    monkeypatch.delenv(host.LIAB_PATH_ENV, raising=False)
    assert host.local_override() is None
    monkeypatch.setenv(host.LIAB_PATH_ENV, str(tmp_path))
    assert host.local_override() == tmp_path.resolve()


# ── labinabox.py translation ───────────────────────────────────────────────

def test_available_addons_reads_checkout(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("install_smlm.py", "install_rancher", "setup_lab.py"):
        (scripts / name).write_text("")
    assert available_addons(tmp_path) == {"smlm", "rancher"}


def test_unresolved_placeholders_paths():
    lab = {"smlm": {"a": "??x", "list": [{"b": "??y"}], "ok": "fine"}}
    assert unresolved_placeholders(lab) == ["smlm.a", "smlm.list[0].b"]


def test_smlm_workshop_lab_json(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    lab, warnings = build_lab_json(cfg)
    assert warnings == []  # every node sets its own ISO_IMAGE
    assert unresolved_placeholders(lab) == []
    assert len(lab["nodes"]) == 8
    smlm = lab["nodes"]["smlm.rodeo.lab"]
    assert smlm["myip"] == "192.168.122.20" and smlm["addons"] == ["smlm"]
    assert smlm["VM_MEM"] == "16384" and smlm["VM_DSK"] == "300"
    centos = lab["nodes"]["centos7.rodeo.lab"]
    assert centos["config_method"] == "virt_customize" and centos["VM_BOOT"] == "uefi=off"
    assert lab["nodes"]["zzcentos7.rodeo.lab"]["VM_DSK_BUS"] == "sata"  # YAML anchor survived
    assert lab["nodes"]["ubuntu2404lts.rodeo.lab"]["ssh_pwauth"] == "true"
    assert lab["common"]["VM_ROOT_PASS"] == "Universal123"
    assert smlm["ISO_URL"] == "https://images.example/smlm.qcow2" and smlm["ISO_SHA256"] == SHA
    assert centos["ISO_SHA256"].startswith("284aab2b")
    assert lab["nodes"]["sles15.rodeo.lab"]["ISO_URL"] == "file:///srv/images/sp5.qcow2"
    assert lab["nodes"]["zzsles15c.rodeo.lab"]["ISO_SHA256"] == "d" * 64
    ubuntu = lab["nodes"]["ubuntu2404lts.rodeo.lab"]
    assert ubuntu["ISO_SHA256_URL"].endswith("/SHA256SUMS") and "ISO_SHA256" not in ubuntu
    section = lab["smlm"]
    assert section["smlm_preinstalled"] == "true"
    assert section["smlm_image_admin_pass"] == "Baked123abc"
    assert section["smlm_admin_pass"] == "Admin123abc"
    assert [k["smlm_activation_key"] for k in section["smlm_activation_keys"]] == [
        "sles15sp5", "sles15sp6", "ubuntu2404", "liberty7ltss"]
    assert "kclusters" not in lab
    # zz* clients arrive pre-registered under the exercises' system names.
    regs = {name.split(".")[0]: node["addons"][0]["client_registration"]
            for name, node in lab["nodes"].items() if node.get("addons") and name != "smlm.rodeo.lab"}
    assert {k: v["client_registration_profile_name"] for k, v in regs.items()} == {
        "zzcentos7": "airco-dh4a-prod", "zzsles15a": "at-ct-pro",
        "zzsles15b": "at-ft-pro", "zzsles15c": "at-ct-qa"}
    assert regs["zzcentos7"]["client_registration_activation_key"] == "1-liberty7ltss"
    assert lab["nodes"]["zzsles15b.rodeo.lab"]["ISO_IMAGE"].startswith("SLES15-SP6")  # merge key kept the base
    assert lab["client_registration"]["client_registration_server"] == "smlm.rodeo.lab"
    assert lab["client_registration"]["client_registration_admin_pass"] == "Admin123abc"
    groups = {g["name"]: g.get("systems", []) for g in section["smlm_system_groups"]}
    assert groups["airtrain"] == ["at-ft-pro", "at-ct-pro", "at-ct-qa"]
    json.loads(json.dumps(lab))


def test_smlm_workshop_without_operator_secrets_stays_unresolved(tmp_path):
    cfg = _smlm_cfg(tmp_path, {"universal_pwd": "u", "smlm_admin_password": "a"})
    lab, _ = build_lab_json(cfg)
    unresolved = unresolved_placeholders(lab)
    assert "smlm.smlm_image_admin_pass" in unresolved
    assert "nodes.smlm.rodeo.lab.ISO_URL" in unresolved
    assert "nodes.sles15.rodeo.lab.ISO_SHA256" in unresolved


def test_common_image_source_for_nodes_without_their_own(tmp_path):
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    (lab_dir / "rodeo-plan.yaml").write_text(yaml.safe_dump({
        "type": "lab-in-a-box", "name": "one",
        "lab_in_a_box": {"iso_image": "leap.qcow2", "images": [
            {"name": "leap.qcow2", "url": "https://x/leap.qcow2", "sha256_url": "https://x/leap.sha256"}]}}))
    lab, warnings = build_lab_json(load_config(lab_dir / "rodeo-plan.yaml"))
    assert lab["common"]["ISO_URL"] == "https://x/leap.qcow2"
    assert lab["common"]["ISO_SHA256_URL"] == "https://x/leap.sha256"
    assert "ISO_URL" not in lab["nodes"]["vm1.rodeo.lab"]
    assert warnings == []


# ── profile + registration ─────────────────────────────────────────────────

def test_profile_registered_and_example_listed():
    profile = get_profile("lab-in-a-box")
    assert profile.phases == ["kvm_host", "labinabox_host", "labinabox", "custom_scripts"]
    assert profile.ansible_phases == frozenset(["kvm_host"])
    assert PROFILE_EXAMPLE["smlm-workshop"] == "smlm-workshop"


def test_smlm_cfg_vms_flavors_and_sizing(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    assert cfg["vms"]["smlm"] == {
        "ip": "192.168.122.20", "user": "root", "mac": "02:00:00:5A:00:20", "flavor": "smlm",
        "domain": "smlm.rodeo.lab"}
    need_mib, need_gb = _resource_needs(cfg)
    assert need_mib == 16384 + 2 * 1024 + 4 * 2048 + 2048
    assert need_gb == 300 + 2 * 20 + 4 * 30 + 20 + 20


def test_vars_file_needs_no_harvester_secrets(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    assert DeployRunner(cfg, tmp_path)._write_vars_file().exists()


def test_seed_smlm_workshop_keeps_bake_kit_and_secret_refs(tmp_path):
    lab = seed_lab("smlm-workshop", tmp_path / "my-smlm")
    assert (lab / "custom/scripts/10-smlm-config.sh").stat().st_mode & 0o111
    for rel in ("definition.yaml", "README.md", "bake/rodeo-plan.yaml", "bake/generalise.sh",
                "bake/export-image.sh", "bake/definition.yaml"):
        assert (lab / rel).is_file(), rel
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["name"] == "my-smlm"
    assert plan["lab_in_a_box"]["sections"]["smlm"]["smlm_admin_pass"] == "??smlm_admin_password"
    assert plan["lab_in_a_box"]["nodes"]["zzsles15c"]["ISO_IMAGE"].startswith("SLES15-SP6")


def test_bake_plan_loads_with_only_the_smlm_node(tmp_path):
    lab = seed_lab("smlm-workshop", tmp_path / "lab")
    cfg = load_config(lab / "bake" / "rodeo-plan.yaml")
    assert list(cfg["vms"]) == ["smlm"]
    lab_json, _ = build_lab_json(cfg)
    node = lab_json["nodes"]["smlm.rodeo.lab"]
    assert node["myip"] == "192.168.122.20" and node["mymac"] == "02:00:00:5A:00:20"
    channels = lab_json["smlm"]["smlm_channels"]
    assert "centos7-x86_64" in channels and "sle15-sp6-installer-updates-x86_64" in channels


# ── phases ──────────────────────────────────────────────────────────────────

class _Runner:
    """Minimal DeployRunner stand-in recording streamed commands."""

    def __init__(self, cfg: dict, root: Path, rc: int = 0) -> None:
        self.cfg = cfg
        self.root = root
        self._last_rc = 0
        self.rc = rc
        self.streamed: list[list[str]] = []
        self.firewalld = 0

    def _stream_subprocess(self, cmd, env=None):
        self.streamed.append(list(cmd))
        self._last_rc = self.rc
        yield LogLine("ok")

    def _start_firewalld(self):
        self.firewalld += 1
        yield LogLine("firewalld")


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_stream_labinabox_writes_private_lab_json_and_runs_setup(tmp_path, monkeypatch):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    runner = _Runner(cfg, tmp_path)
    calls = []
    root_ed = tmp_path / "root_id_ed25519"
    root_ed.write_text("PRIVATE")
    root_ed.with_suffix(".pub").write_text("ssh-ed25519 AAAAroot root@host\n")
    monkeypatch.setattr(phase, "_ROOT_ED25519_KEY", root_ed)
    keys = tmp_path / ".rodeo" / "ssh"
    keys.mkdir(parents=True)
    (keys / "id_ed25519").write_text("PRIVATE")
    (keys / "id_ed25519.pub").write_text("ssh-ed25519 AAAArodeo rodeo-managed\n")

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["openssl", "passwd"]:
            assert kw["input"] == "Universal123"
            return _Completed(stdout="$6$salt$hash\n")
        return _Completed()

    monkeypatch.setattr(phase.subprocess, "run", fake_run)
    list(phase.stream_labinabox(runner))

    assert runner._last_rc == 0
    path = Path(cfg["config_dir"]) / phase.LAB_JSON_RELPATH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lab = json.loads(path.read_text())
    assert lab["common"]["ROOT_PWD_HASH"] == "$6$salt$hash"
    assert runner.streamed == [[str(host.LIAB_BIN / "setup_lab.py"), "--keep", "--parallel=4", str(path)]]
    ssh_calls = [c for c in calls if c[0] == "ssh"]
    assert len(ssh_calls) == 8 and ssh_calls[0][-2] == "root@192.168.122.20"
    assert "ssh-ed25519 AAAArodeo rodeo-managed" in ssh_calls[0][-1]
    assert "ssh-ed25519 AAAAroot root@host" in ssh_calls[0][-1]


def test_stream_labinabox_refuses_unresolved_secrets(tmp_path, monkeypatch):
    cfg = _smlm_cfg(tmp_path, {"universal_pwd": "u", "smlm_admin_password": "a"})
    runner = _Runner(cfg, tmp_path)
    monkeypatch.setattr(phase.subprocess, "run", lambda *a, **k: _Completed())
    lines = [e.line for e in phase.stream_labinabox(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("smlm.smlm_image_admin_pass" in line for line in lines)
    assert not (Path(cfg["config_dir"]) / phase.LAB_JSON_RELPATH).exists()
    assert runner.streamed == []


@pytest.fixture
def host_sandbox(tmp_path, monkeypatch):
    """Point every host path the host phase touches into tmp_path."""
    root = tmp_path / "sys"
    (root / "ssh").mkdir(parents=True)
    checkout = tmp_path / "liab"
    checkout.mkdir()
    (checkout / "install_automation_node_scripts.sh").write_text("")
    monkeypatch.setenv(host.LIAB_PATH_ENV, str(checkout))
    monkeypatch.setattr(host, "LAB_CREATION_CFG", root / "lab_creation.cfg")
    monkeypatch.setattr(host, "NAMED_ZONE_DIR", root / "named")
    monkeypatch.setattr(phase, "ETC_HOSTS", root / "hosts")
    monkeypatch.setattr(phase, "_ROOT_KEY", root / "ssh" / "id_rsa")
    monkeypatch.setattr(phase, "_AUTHORIZED_KEYS", root / "ssh" / "authorized_keys")
    monkeypatch.setattr(phase.shutil, "which",
                        lambda name: None if name in ("python3.11", "python3.12") else "/usr/bin/" + name)
    (root / "ssh" / "id_rsa").write_text("PRIVATE")
    (root / "ssh" / "id_rsa.pub").write_text("ssh-rsa AAAAhost root@host\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(host, "LIAB_BIN", bin_dir)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        text = " ".join(cmd)
        if "lab_schema" in text:
            return _Completed(stdout=_schema_output(text))
        return _Completed()

    monkeypatch.setattr(phase.subprocess, "run", fake_run)
    return root, calls


def _schema_output(cmd: str) -> str:
    """What lab-in-a-box's lab_schema prints, rebuilt from the test snapshot."""
    fields = lambda names: [{"name": n} for n in names]  # noqa: E731
    if "--base" in cmd:
        return json.dumps({"sections": {k: {"fields": fields(_SCHEMA[k])} for k in ("common", "nodes", "kclusters")}})
    addon = cmd.split("install_")[-1].split()[0]
    return json.dumps({"fields": fields(_SCHEMA["addons"][addon])})


def test_host_phase_prepares_host(tmp_path, host_sandbox, monkeypatch):
    root, calls = host_sandbox
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg.pop("workshop")

    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]

    assert runner._last_rc == 0, lines
    assert runner.streamed[0][-1].endswith("install_automation_node_scripts.sh")
    assert "_python_bin=python3.13" in runner.streamed[0]
    cfg_file = host.LAB_CREATION_CFG
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
    assert "REMOTE_HOST=192.168.122.1" in cfg_file.read_text()
    assert "ssh-rsa AAAAhost" in (root / "ssh" / "authorized_keys").read_text()
    assert (root / "named").is_dir()
    assert runner.firewalld == 1
    assert ["firewall-cmd", "--permanent", "--zone=public",
            "--add-forward-port=port=443:proto=tcp:toport=443:toaddr=192.168.122.20"] in calls
    # Images are lab-in-a-box's job: rodeo only asks for Last-Modified (age check).
    assert not any(c[0] == "curl" and "-o" in c for c in calls)


def test_host_phase_fetches_the_resolved_latest_release(tmp_path, host_sandbox, monkeypatch):
    monkeypatch.delenv(host.LIAB_PATH_ENV)
    monkeypatch.setattr(host, "resolve_ref", lambda repo, ref: "1.10.0")
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"].pop("source", None)
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert "lab-in-a-box latest release: 1.10.0" in lines
    fetch = next(c for c in runner.streamed if "fetch" in c)
    assert fetch[-1] == "1.10.0"
    assert str(host.checkout_dir("1.10.0")) in fetch


def test_host_phase_stops_when_latest_cannot_be_resolved(tmp_path, host_sandbox, monkeypatch):
    monkeypatch.delenv(host.LIAB_PATH_ENV)

    def fail(repo, ref):
        raise ConfigError("cannot resolve lab-in-a-box 'latest'")

    monkeypatch.setattr(host, "resolve_ref", fail)
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"].pop("source", None)
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("cannot resolve lab-in-a-box 'latest'" in line for line in lines)
    assert not any("fetch" in c for c in runner.streamed)


def test_host_phase_refuses_foreign_lab_creation_cfg(tmp_path, host_sandbox):
    root, _ = host_sandbox
    host.LAB_CREATION_CFG.write_text("REMOTE_HOST=nuc1.mydemo.lab\n")
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"]["target"] = {"mode": "managed"}
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("not written by rodeo" in line for line in lines)
    assert host.LAB_CREATION_CFG.read_text() == "REMOTE_HOST=nuc1.mydemo.lab\n"


# ── clean ───────────────────────────────────────────────────────────────────

def test_clean_runs_destroy_lab_for_deployed_lab(tmp_path, monkeypatch):
    from rodeo.commands import clean

    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    lab, _ = build_lab_json(cfg)
    lab_json = Path(cfg["config_dir"]) / phase.LAB_JSON_RELPATH
    lab_json.parent.mkdir()
    lab_json.write_text(json.dumps(lab))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "destroy_lab.py").write_text("")
    monkeypatch.setattr(host, "LIAB_BIN", bin_dir)
    monkeypatch.setattr(host, "LAB_CREATION_CFG", tmp_path / "lab_creation.cfg")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n" + host.hosts_block(cfg["name"], lab) + host.hosts_block("other", lab))
    monkeypatch.setattr(phase, "ETC_HOSTS", hosts)
    calls = []
    monkeypatch.setattr(clean.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Completed())
    clean._destroy_labinabox_lab(cfg)
    assert calls[0] == [str(bin_dir / "destroy_lab.py"), str(lab_json)]
    assert ["firewall-cmd", "--permanent", "--zone=public",
            "--remove-forward-port=port=443:proto=tcp:toport=443:toaddr=192.168.122.20"] in calls
    assert not any("net-destroy" in c for c in calls)          # instance 0 shares `default`
    assert f"LAB-IN-A-BOX {cfg['name']}" not in hosts.read_text()
    assert "LAB-IN-A-BOX other" in hosts.read_text()             # other labs' blocks stay
    assert not lab_json.exists()

    calls.clear()
    clean._destroy_labinabox_lab(cfg)                            # nothing deployed any more
    assert calls == []


def test_subprocess_module_is_the_real_one():
    # Guard for the monkeypatching above: the phase uses subprocess.run directly.
    assert phase.subprocess is subprocess


# ── day-2 commands: libvirt domains are FQDNs ──────────────────────────────

class _RecordingLV:
    """LibvirtDriver stand-in: domains exist under their FQDN only."""

    def __init__(self, domains):
        self.running = dict(domains)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, uri):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def list_all_domain_names(self):
        return list(self.running)

    def is_running(self, name):
        return self.running.get(name, False)

    def start(self, name):
        self.calls.append(("start", name))
        if name not in self.running:
            raise RuntimeError(f"Domain not found: {name}")
        self.running[name] = True

    def shutdown(self, name):
        self.calls.append(("shutdown", name))
        if name in self.running:
            self.running[name] = False

    def list_vms(self, names):
        from rodeo.engine.libvirt import VMInfo

        return [VMInfo(name=n, state=("running" if self.running.get(n) else "shut off")
                       if n in self.running else "not found") for n in names]


def test_domain_name():
    from rodeo.engine.libvirt import domain_name

    cfg = {"vms": {"smlm": {"domain": "smlm.rodeo.lab"}, "rancher": {"ip": "x"}}}
    assert domain_name(cfg, "smlm") == "smlm.rodeo.lab"
    assert domain_name(cfg, "rancher") == "rancher"
    assert domain_name(None, "harvester1") == "harvester1"


def test_status_reports_short_names_for_fqdn_domains(tmp_path, monkeypatch):
    from rodeo.service.status import status_report

    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    lv = _RecordingLV({"smlm.rodeo.lab": True, "centos7.rodeo.lab": False})
    monkeypatch.setattr("rodeo.engine.libvirt.LibvirtDriver", lv)
    vms = {v["name"]: v["state"] for v in status_report(cfg)["vms"]}
    assert vms["smlm"] == "running"
    assert vms["centos7"] == "shut off"
    assert vms["ubuntu2404lts"] == "not found"


def test_start_and_stop_use_fqdn_domains(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import rodeo.commands.start_cmd as start_mod
    import rodeo.commands.stop_cmd as stop_mod

    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    plan = Path(cfg["config_dir"]) / "rodeo-plan.yaml"
    domains = {f"{n}.rodeo.lab": False for n in cfg["vms"]}
    lv = _RecordingLV(domains)
    for mod in (start_mod, stop_mod):
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod, "LibvirtDriver", lv)
    monkeypatch.setattr(start_mod, "_start_host_services", lambda components: None, raising=False)
    monkeypatch.setattr(stop_mod, "_stop_host_services", lambda components: None, raising=False)
    monkeypatch.setattr(start_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(stop_mod.time, "sleep", lambda s: None)

    result = CliRunner().invoke(start_mod.start_cmd, ["--yes", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert ("start", "smlm.rodeo.lab") in lv.calls
    assert all(name.endswith(".rodeo.lab") for _, name in lv.calls)
    assert "not defined on this host" not in result.output

    lv.calls.clear()
    result = CliRunner().invoke(stop_mod.stop_cmd, ["--yes", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert ("shutdown", "smlm.rodeo.lab") in lv.calls


# ── lab.json fields vs. lab-in-a-box's schema (tests/fixtures/labinabox_schema) ──



def test_smlm_workshop_lab_json_only_uses_known_lab_in_a_box_fields(tmp_path):
    from rodeo.labinabox import unknown_fields

    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    lab, _ = build_lab_json(cfg)
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"  # added by the labinabox phase at deploy
    assert unknown_fields(lab, _SCHEMA) == []


def test_smlm_workshop_lab_json_has_every_required_lab_in_a_box_field(tmp_path):
    lab, _ = build_lab_json(_smlm_cfg(tmp_path, FULL_SECRETS))
    every_node_has_image = all(n.get("ISO_IMAGE") for n in lab["nodes"].values())
    missing = [f"common.{k}" for k in _SCHEMA["required"]["common"]
               if k not in lab["common"] and not (k == "ISO_IMAGE" and every_node_has_image)]
    missing += [f"nodes.{n}.{k}" for n, v in lab["nodes"].items()
                for k in _SCHEMA["required"]["nodes"] if k not in v]
    assert missing == []


def test_common_sizing_is_the_largest_node_value(tmp_path):
    lab, _ = build_lab_json(_smlm_cfg(tmp_path, FULL_SECRETS))
    for key in ("VM_MEM", "VM_CPU", "VM_DSK"):
        assert lab["common"][key] == str(max(int(n[key]) for n in lab["nodes"].values()))
    smlm = next(v for k, v in lab["nodes"].items() if k.startswith("smlm."))
    assert lab["common"]["VM_MEM"] == smlm["VM_MEM"] == "16384"


def test_unknown_fields_reports_what_an_older_lab_in_a_box_lacks():
    from rodeo.labinabox import unknown_fields

    old = {"common": ["ISO_IMAGE"], "nodes": ["myip", "addons"], "kclusters": [],
           "addons": {"smlm": ["smlm_admin_pass"]}}
    lab = {"common": {"ISO_IMAGE": "x", "ISO_URL": "u"},
           "nodes": {"a": {"myip": "1", "ssh_pwauth": "true",
                           "addons": ["smlm", {"client_registration": {"x": 1}}]}},
           "smlm": {"smlm_admin_pass": "p", "smlm_preinstalled": "true"}}
    assert unknown_fields(lab, old) == [
        "common.ISO_URL", "nodes.a.ssh_pwauth", "nodes.a.addons.client_registration",
        "smlm.smlm_preinstalled"]



def test_image_age_days(tmp_path, monkeypatch):
    image = tmp_path / "img.qcow2"
    image.write_bytes(b"x")
    now = image.stat().st_mtime + 10 * 86400
    assert round(host.image_age_days(f"file://{image}", now=now)) == 10
    assert host.image_age_days(f"file://{tmp_path}/missing", now=now) is None

    headers = ("HTTP/1.1 302 Found\nLast-Modified: Mon, 01 Jan 2026 00:00:00 GMT\n\n"
               "HTTP/1.1 200 OK\nLast-Modified: Thu, 01 Jan 2026 00:00:00 GMT\n")
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: _Completed(stdout=headers))
    jan2 = 1767312000.0  # 2026-01-02T00:00:00Z
    assert round(host.image_age_days("https://x/img", now=jan2)) == 1  # last hop wins
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: _Completed(stdout="HTTP/1.1 200 OK\n"))
    assert host.image_age_days("https://x/img", now=jan2) is None


def test_max_age_days_validation():
    with pytest.raises(ConfigError, match="max_age_days"):
        host.base_images({"lab_in_a_box": {"images": [
            {"name": "a", "url": "https://x", "sha256": SHA, "max_age_days": 0}]}})


# ── §10: existing lab-in-a-box hosts ───────────────────────────────────────

def test_target_validation():
    assert host.target({}) == {"mode": "auto", "host": None, "ssh_user": "root", "libvirt_uri": None}
    for bad in ({"mode": "x"}, {"host": "a;b"}, {"host": "a", "mode": "managed"}, {"ssh_user": "Root!"},
                {"libvirt_uri": "qemu+ssh://root@h/system; rm -rf /"}, {"libvirt_uri": "http://x"}):
        with pytest.raises(ConfigError):
            host.target({"lab_in_a_box": {"target": bad}})


def test_effective_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "LAB_CREATION_CFG", tmp_path / "lab_creation.cfg")
    monkeypatch.setattr(host, "LIAB_BIN", tmp_path)
    assert host.effective_mode({}) == "managed"                      # nothing here yet
    host.LAB_CREATION_CFG.write_text(host.render_lab_creation_cfg("192.168.122.1", "k"))
    (tmp_path / "setup_lab.py").write_text("")
    assert host.effective_mode({}) == "managed"                      # rodeo's own install
    host.LAB_CREATION_CFG.write_text("REMOTE_HOST=nuc1\n")
    assert host.effective_mode({}) == "existing"                     # someone else's
    assert host.effective_mode({"lab_in_a_box": {"target": {"mode": "managed"}}}) == "managed"
    assert host.effective_mode({"lab_in_a_box": {"target": {"host": "auto1"}}}) == "existing"


def test_remote_commands_keep_secrets_off_argv_and_check_ownership():
    upload = host.remote_upload_command("smlm-2")
    assert "umask 077" in upload and "cat > .rodeo-labs/smlm-2/lab.json.tmp" in upload
    assert "'rodeo lab smlm-2' > .rodeo-labs/smlm-2/owner" in upload
    destroy = host.remote_destroy_command("smlm-2")
    assert destroy.startswith('test "$(cat .rodeo-labs/smlm-2/owner 2>/dev/null)" = \'rodeo lab smlm-2\' && ')
    assert "destroy_lab.py .rodeo-labs/smlm-2/lab.json" in destroy
    with pytest.raises(ConfigError):
        host.remote_lab_dir("../etc")
    assert host.setup_command("x/lab.json", 4).endswith("setup_lab.py --keep --parallel=4 x/lab.json")


def test_addons_used():
    lab = {"nodes": {"a": {"addons": ["smlm", {"client_registration": {}}]}},
           "kclusters": {"c": {"addons": ["rancher"]}}, "common": {}, "longhorn": {}}
    assert host.addons_used(lab) == {"smlm", "client_registration", "rancher", "longhorn"}


def test_existing_local_host_is_left_alone(tmp_path, host_sandbox):
    root, calls = host_sandbox
    host.LAB_CREATION_CFG.write_text("REMOTE_HOST=nuc1\n")
    (host.LIAB_BIN / "setup_lab.py").write_text("")
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg.pop("workshop")
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 0, lines
    assert runner.streamed == []                           # no fetch/install
    assert host.LAB_CREATION_CFG.read_text() == "REMOTE_HOST=nuc1\n"
    assert runner.firewalld == 0
    assert not any(c[0] in ("virsh", "firewall-cmd", "ssh-keygen") for c in calls)
    assert any("existing lab-in-a-box on this host" in line for line in lines)
    assert any("fields match" in line for line in lines)


def test_field_check_refuses_older_lab_in_a_box(tmp_path, host_sandbox, monkeypatch):
    root, calls = host_sandbox
    host.LAB_CREATION_CFG.write_text("REMOTE_HOST=nuc1\n")
    (host.LIAB_BIN / "setup_lab.py").write_text("")
    old_smlm = [f for f in _SCHEMA["addons"]["smlm"] if f != "smlm_preinstalled"]

    def old_schema(cmd, **kw):
        text = " ".join(cmd)
        if "lab_schema" not in text:
            return _Completed()
        out = _schema_output(text)
        if "install_smlm" in text:
            out = json.dumps({"fields": [{"name": n} for n in old_smlm]})
        return _Completed(stdout=out)

    monkeypatch.setattr(phase.subprocess, "run", old_schema)
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("smlm.smlm_preinstalled" in line and "newer lab-in-a-box" in line for line in lines)


def test_remote_host_setup_runs_over_ssh(tmp_path, monkeypatch):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"]["target"] = {"host": "automation.example.lab"}
    keys = tmp_path / ".rodeo" / "ssh"
    keys.mkdir(parents=True)
    (keys / "id_ed25519").write_text("PRIVATE")
    (keys / "id_ed25519.pub").write_text("ssh-ed25519 AAAArodeo\n")
    runner = _Runner(cfg, tmp_path)
    uploads = []

    def fake_run(cmd, **kw):
        if cmd[:2] == ["openssl", "passwd"]:
            return _Completed(stdout="$6$h\n")
        uploads.append((cmd, kw.get("input")))
        return _Completed()

    monkeypatch.setattr(phase.subprocess, "run", fake_run)
    list(phase.stream_labinabox(runner))
    assert runner._last_rc == 0
    (cmd, data), = uploads                                  # one upload, no key pushes
    assert cmd[-2] == "root@automation.example.lab" and "cat > .rodeo-labs/" in cmd[-1]
    assert "Universal123" in data and "Universal123" not in " ".join(cmd)   # secrets via stdin only
    assert runner.streamed[-1][-2] == "root@automation.example.lab"
    assert runner.streamed[-1][-1].endswith(f"--keep --parallel=4 .rodeo-labs/{cfg['name']}/lab.json")


def test_rodeo_ssh_jumps_through_remote_host(tmp_path):
    from rodeo.ssh_targets import build_ssh_target

    cfg = {"vms": {"smlm": {"ip": "10.1.0.20", "user": "root"}},
           "lab_in_a_box": {"target": {"host": "automation.example.lab"}}}
    t = build_ssh_target("smlm", cfg=cfg, key=str(tmp_path / "k"))
    assert (t.host, t.jump_host, t.jump_user) == ("10.1.0.20", "automation.example.lab", "root")


def test_clean_on_remote_host_checks_ownership(tmp_path, monkeypatch):
    from rodeo.commands import clean

    keys = tmp_path / ".rodeo" / "ssh"
    keys.mkdir(parents=True)
    (keys / "id_ed25519").write_text("PRIVATE")
    (keys / "id_ed25519.pub").write_text("ssh-ed25519 AAAArodeo\n")
    calls = []
    monkeypatch.setattr(clean.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Completed())
    clean._destroy_labinabox_lab({"name": "smlm-2", "config_dir": str(tmp_path),
                                  "lab_in_a_box": {"target": {"host": "auto1", "ssh_user": "lab"}}})
    (cmd,) = calls
    assert cmd[-2] == "lab@auto1"
    assert cmd[-1] == host.remote_destroy_command("smlm-2")


# ── §11: variants (image / scratch), mirrors, no Instruqt in the lab ────────

SCRATCH_SECRETS = {**FULL_SECRETS, "scc_regcode": "REG", "scc_mirror_user": "u", "scc_mirror_password": "p",
                   "sles15sp7_image_url": "file:///srv/sles15sp7.qcow2", "sles15sp7_image_sha256": "e" * 64}


def test_scratch_variant_builds_smlm_on_sles15sp7(tmp_path):
    from rodeo.labinabox import unknown_fields

    cfg = _smlm_cfg(tmp_path, SCRATCH_SECRETS)
    cfg["lab_in_a_box"]["variant"] = "scratch"
    lab, _ = build_lab_json(cfg)
    smlm = lab["nodes"]["smlm.rodeo.lab"]
    assert smlm["ISO_IMAGE"] == "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2"
    assert smlm["ISO_URL"] == "file:///srv/sles15sp7.qcow2" and smlm["addons"] == ["smlm"]
    assert smlm["config_method"] == "cloud-init"
    section = lab["smlm"]
    assert "smlm_preinstalled" not in section and "smlm_image_admin_pass" not in section
    assert "smlm_byos" not in section
    assert section["smlm_scc_regcode"] == "REG" and section["smlm_scc_password"] == "p"
    assert "centos7-x86_64" in section["smlm_channels"]
    assert section["smlm_activation_keys"][0]["smlm_activation_key"] == "sles15sp5"   # kept from base
    assert not any(n.get("ISO_IMAGE") == "smlm-workshop-server.qcow2" for n in lab["nodes"].values())
    assert unresolved_placeholders(lab) == []
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"
    assert unknown_fields(lab, _SCHEMA) == []


def test_variant_secrets_only_for_the_selected_variant(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    plan = yaml.safe_load((SMLM / "rodeo-plan.yaml").read_text())
    _, missing = ensure_plan_secrets(plan, tmp_path / "s.yaml")
    assert "smlm_image_url" in missing and "scc_regcode" not in missing
    plan["lab_in_a_box"]["variant"] = "scratch"
    _, missing = ensure_plan_secrets(plan, tmp_path / "s2.yaml")
    assert "scc_regcode" in missing and "sles15sp7_image_sha256" in missing
    assert "smlm_image_url" not in missing and "smlm_image_admin_pass" not in missing


def test_variant_merge_rules():
    from rodeo.labinabox import effective_overlay

    cfg = {"lab_in_a_box": {
        "images": [{"name": "a", "url": "https://a"}, {"name": "b", "url": "https://b"}],
        "sections": {"x": {"keep": 1, "drop": 2}},
        "variant": "v",
        "variants": {"v": {"notice": "n", "operator_secrets": ["k"],
                           "images": [{"name": "a", "remove": True}, {"name": "b", "sha256": "s"},
                                      {"name": "c", "url": "https://c"}],
                           "sections": {"x": {"drop": None, "new": 3}}}}}}
    out = effective_overlay(cfg)
    assert out["images"] == [{"name": "b", "url": "https://b", "sha256": "s"}, {"name": "c", "url": "https://c"}]
    assert out["sections"] == {"x": {"keep": 1, "new": 3}}
    assert "variants" not in out and "notice" not in out
    cfg["lab_in_a_box"]["variant"] = "nope"
    with pytest.raises(ConfigError, match="not one of"):
        effective_overlay(cfg)


def test_image_mirror_list(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"]["images"][1]["url"].append("https://mirror.example/c7.qcow2")
    lab, _ = build_lab_json(cfg)
    assert lab["nodes"]["centos7.rodeo.lab"]["ISO_URL"] == (
        "https://cloud.centos.org/centos/7/images/CentOS-7-x86_64-GenericCloud-2211.qcow2 "
        "https://mirror.example/c7.qcow2")
    cfg["lab_in_a_box"]["images"][1]["url"].append("ftp://bad")
    with pytest.raises(ConfigError, match="mirror"):
        host.base_images(cfg)


def test_no_instruqt_in_the_lab_itself(tmp_path):
    for variant in ("image", "scratch"):
        cfg = _smlm_cfg(tmp_path / variant, SCRATCH_SECRETS)
        cfg["lab_in_a_box"]["variant"] = variant
        lab, _ = build_lab_json(cfg)
        assert "instruqt" not in json.dumps(lab).lower(), variant
    definition = (SMLM / "definition.yaml").read_text()
    body = definition.split("apiVersion", 1)[1]           # past the provenance comment
    assert "instruqt" not in body.lower()


def test_managed_host_names_missing_tools(tmp_path, host_sandbox, monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", lambda name: None if name in ("virt-install", "rsync") else "/x")
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("rsync, virt-install" in line for line in lines)
    assert runner.streamed == []


def test_bake_plan_only_uses_known_fields(tmp_path):
    from rodeo.labinabox import unknown_fields

    lab_dir = seed_lab("smlm-workshop", tmp_path / "lab")
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump(SCRATCH_SECRETS))
    lab, _ = build_lab_json(load_config(lab_dir / "bake" / "rodeo-plan.yaml"))
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"
    assert unknown_fields(lab, _SCHEMA) == []


# ── §12: several instances of one lab on a host ────────────────────────────

def _instance_cfg(tmp_path: Path, n: int, secrets: dict | None = None) -> dict:
    lab = tmp_path / f"lab{n}"
    shutil.copytree(SMLM, lab)
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    plan["instance"] = n
    plan["name"] = f"smlm-{n}"
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump(plan))
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump(secrets or FULL_SECRETS))
    return load_config(lab / "rodeo-plan.yaml")


def test_instance_helpers():
    from rodeo import instances

    assert instances.shift_ip("192.168.122.20", 3) == "192.168.125.20"
    assert instances.shift_cidr("192.168.122.0/24", 3) == "192.168.125.0/24"
    assert instances.shift_mac("02:00:00:5A:00:20", 3) == "02:00:03:5A:00:20"
    with pytest.raises(ConfigError):
        instances.shift_ip("192.168.250.1", 10)
    with pytest.raises(ConfigError):
        instances.instance_number({"instance": "2"})
    topo = {"network": {"name": "default", "bridge": "virbr0", "cidr": "192.168.122.0/24"}}
    assert instances.apply_to_topology(topo, 0) is topo


def test_two_instances_do_not_overlap(tmp_path):
    a, b = _instance_cfg(tmp_path, 1), _instance_cfg(tmp_path, 2)
    lab_a, _ = build_lab_json(a)
    lab_b, _ = build_lab_json(b)
    assert set(lab_a["nodes"]).isdisjoint(lab_b["nodes"])                        # libvirt domains
    assert "smlm.i1.rodeo.lab" in lab_a["nodes"] and "smlm.i2.rodeo.lab" in lab_b["nodes"]
    ips = lambda lab: {n["myip"] for n in lab["nodes"].values()}  # noqa: E731
    macs = lambda lab: {n["mymac"] for n in lab["nodes"].values()}  # noqa: E731
    assert ips(lab_a).isdisjoint(ips(lab_b)) and macs(lab_a).isdisjoint(macs(lab_b))
    assert lab_a["common"]["mygw"] == "192.168.123.1" and lab_b["common"]["mydomain"] == "i2.rodeo.lab"
    assert {n["BRIDGE"] for n in lab_a["nodes"].values()} == {"rbr1"}
    from rodeo.inventory import build_inventory

    na, nb = build_inventory(a)["libvirt_network"], build_inventory(b)["libvirt_network"]
    assert (na["name"], nb["name"]) == ("rodeo-i1", "rodeo-i2")
    ports = lambda cfg: [s["host_port"] for s in build_inventory(cfg)["_raw_topology"]["exposed_services"]]  # noqa: E731
    assert (ports(a), ports(b)) == ([1443], [2443])


def test_instance_vms_and_node_refs_follow_the_instance(tmp_path):
    cfg = _instance_cfg(tmp_path, 1)
    assert cfg["vms"]["smlm"]["ip"] == "192.168.123.20"
    assert cfg["vms"]["smlm"]["domain"] == "smlm.i1.rodeo.lab"
    assert cfg["network"]["dns_domain"] == "i1.rodeo.lab"
    lab, _ = build_lab_json(cfg)
    reg = lab["client_registration"]
    assert reg["client_registration_server"] == "smlm.rodeo.lab"            # the image's own name
    assert reg["client_registration_server_node"] == "smlm.i1.rodeo.lab"
    assert reg["client_registration_server_ip"] == "192.168.123.20"


def test_unknown_node_ref_is_an_error(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg["lab_in_a_box"]["sections"]["client_registration"]["client_registration_server_ip"] = "${node_ip:nope}"
    with pytest.raises(ConfigError, match="no lab node named 'nope'"):
        build_lab_json(cfg)


def test_dns_aliases_reach_libvirt_dns():
    lab = {"nodes": {"smlm.i1.rodeo.lab": {"myip": "192.168.123.20"}}}
    aliases = {"smlm": ["smlm.rodeo.lab"]}
    assert host.dns_host_entries(lab, aliases) == [
        ("192.168.123.20", ["smlm.i1.rodeo.lab", "smlm", "smlm.rodeo.lab"])]
    xml = host.render_network_xml({"name": "rodeo-i1", "bridge": "rbr1", "cidr": "192.168.123.0/24",
                                   "gateway": "192.168.123.1", "domain": "i1.rodeo.lab"}, lab, aliases)
    assert "<hostname>smlm.rodeo.lab</hostname>" in xml and "<bridge name='rbr1'" in xml
    assert "smlm.rodeo.lab" not in host.hosts_block("smlm-1", lab)       # host side: per-instance names only


def test_hosts_block_replacement():
    lab = {"nodes": {"a.lab": {"myip": "10.0.0.1"}}}
    text = "127.0.0.1 localhost\n" + host.hosts_block("one", lab) + host.hosts_block("two", lab)
    new = host.replace_hosts_block(text, "one", host.hosts_block("one", {"nodes": {"b.lab": {"myip": "10.0.0.2"}}}))
    assert new.count("BEGIN RODEO LAB-IN-A-BOX one") == 1 and "10.0.0.2  b.lab  b" in new
    assert "BEGIN RODEO LAB-IN-A-BOX two" in new and new.startswith("127.0.0.1 localhost\n")
    removed = host.replace_hosts_block(new, "one", None)
    assert "LAB-IN-A-BOX one" not in removed and "LAB-IN-A-BOX two" in removed


def test_teardown_removes_instance_network_only_when_its_own():
    lab = {"nodes": {"smlm.i1.lab": {"myip": "192.168.123.20"}}}
    svc = [{"name": "w", "host_port": 1443, "guest_port": 443, "target": "smlm"}]
    cmds = host.teardown_commands(lab, svc, "rodeo-i1")
    assert cmds[0][-1] == "--remove-port=1443/tcp"
    assert ["virsh", "-c", "qemu:///system", "net-undefine", "rodeo-i1"] in cmds
    assert not any("net-undefine" in c for c in host.teardown_commands(lab, svc, None))


def test_generated_secrets_are_per_lab(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    plan = yaml.safe_load((SMLM / "rodeo-plan.yaml").read_text())
    global_file = tmp_path / "secrets.yaml"
    global_file.write_text(yaml.safe_dump({"smlm_image_url": "https://x"}))
    ensure_plan_secrets(plan, global_file, tmp_path / "a" / ".rodeo-secrets.yaml")
    ensure_plan_secrets(plan, global_file, tmp_path / "b" / ".rodeo-secrets.yaml")
    a = yaml.safe_load((tmp_path / "a" / ".rodeo-secrets.yaml").read_text())
    b = yaml.safe_load((tmp_path / "b" / ".rodeo-secrets.yaml").read_text())
    assert a["universal_pwd"] != b["universal_pwd"]
    assert "smlm_image_url" not in a                        # operator secrets stay global


def test_lab_secrets_override_global(tmp_path):
    cfg = _instance_cfg(tmp_path, 1)
    lab_dir = Path(cfg["config_dir"])
    (lab_dir / ".rodeo-secrets.yaml").write_text(yaml.safe_dump({"universal_pwd": "OnlyThisLab1"}))
    cfg = load_config(lab_dir / "rodeo-plan.yaml")
    lab, _ = build_lab_json(cfg)
    assert lab["common"]["VM_ROOT_PASS"] == "OnlyThisLab1"


def test_host_lock_is_reentrant_across_runs(tmp_path):
    with phase._host_lock():
        pass
    with phase._host_lock():
        pass
    assert (tmp_path / ".rodeo" / "labinabox-host.lock").exists()


def test_instances_new_and_list(tmp_path):
    from click.testing import CliRunner

    from rodeo.commands.instances_cmd import instances_cmd

    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump(FULL_SECRETS))
    base = tmp_path / "labs"
    r = CliRunner().invoke(instances_cmd, ["new", "smlm-workshop", "--count", "2", "--dir", str(base)])
    assert r.exit_code == 0, r.output
    assert yaml.safe_load((base / "smlm-workshop-2" / "rodeo-plan.yaml").read_text())["instance"] == 2
    r = CliRunner().invoke(instances_cmd, ["list", "--dir", str(base)])
    assert r.exit_code == 0, r.output
    assert "rodeo-i1" in r.output and "rodeo-i2" in r.output and "2443" in r.output
    r = CliRunner().invoke(instances_cmd, ["new", "rancher", "--count", "1", "--dir", str(base)])
    assert r.exit_code != 0 and "only lab-in-a-box" in r.output


def test_target_libvirt_uri_drives_day2_commands(tmp_path):
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    plan = Path(cfg["config_dir"]) / "rodeo-plan.yaml"
    data = yaml.safe_load(plan.read_text())
    data["lab_in_a_box"]["target"] = {"host": "auto1", "libvirt_uri": "qemu+ssh://root@kvm1/system?keyfile=/k"}
    plan.write_text(yaml.safe_dump(data))
    assert load_config(plan)["libvirt"]["uri"] == "qemu+ssh://root@kvm1/system?keyfile=/k"


def test_capacity_helpers():
    out = ("HOST kvm1\nTotal:     33554432 KiB\nHOST kvm2\nTotal:     8388608 KiB\n"
           "HOST kvm3\nTotal: unknown\n")
    free = host.parse_capacity(out)
    assert free == {"kvm1": 32768, "kvm2": 8192, "kvm3": None}
    lab = {"common": {"VM_MEM": "1024"}, "nodes": {"a": {"VM_MEM": "16384"}, "b": {}}}
    assert host.lab_memory_mib(lab) == 17408
    assert host.capacity_warnings(lab, free) == ["could not read free memory on kvm3"]
    big = {"common": {}, "nodes": {"a": {"VM_MEM": "40960"}}}
    warnings = host.capacity_warnings(big, free)
    assert any("largest VM needs 40 GiB" in w and "32 GiB free" in w for w in warnings)
    assert host.capacity_warnings(lab, {}) == ["could not read any hypervisor's free memory"]
    many = {"common": {}, "nodes": {f"n{i}": {"VM_MEM": "16384"} for i in range(3)}}
    assert any("48 GiB" in w and "40 GiB free in total" in w for w in host.capacity_warnings(many, free))

def test_existing_host_warns_when_hypervisor_is_short(tmp_path, host_sandbox, monkeypatch):
    root, calls = host_sandbox
    host.LAB_CREATION_CFG.write_text("REMOTE_HOST=nuc1\n")
    (host.LIAB_BIN / "setup_lab.py").write_text("")

    def run(cmd, **kw):
        text = " ".join(cmd)
        if "freecell" in text:
            return _Completed(stdout="HOST nuc1\nTotal:     8388608 KiB\n")   # 8 GiB free
        if "lab_schema" in text:
            return _Completed(stdout=_schema_output(text))
        return _Completed()

    monkeypatch.setattr(phase.subprocess, "run", run)
    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    cfg.pop("workshop")
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 0                                           # a warning, not a failure
    assert any("needs 28 GiB" in line and "8 GiB free in total" in line for line in lines)


def _seed_instances(tmp_path, count=2):
    from click.testing import CliRunner

    from rodeo.commands.instances_cmd import instances_cmd

    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump(FULL_SECRETS))
    base = tmp_path / "labs"
    CliRunner().invoke(instances_cmd, ["new", "smlm-workshop", "--count", str(count), "--dir", str(base)])
    return base


def test_instances_up_deploys_each_and_stops_on_failure(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import rodeo.commands.instances_cmd as mod

    base = _seed_instances(tmp_path, 3)
    asked = []
    monkeypatch.setattr("rodeo.commands.up_cmd._ensure_plan_secrets", lambda p, assume_yes: asked.append(p))
    runs = []
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: runs.append(cmd) or _Completed(returncode=1 if "smlm-workshop-2" in cmd[-1] else 0))
    r = CliRunner().invoke(mod.instances_cmd, ["up", "--dir", str(base)])
    assert r.exit_code != 0 and "smlm-workshop-2" in r.output
    assert len(asked) == 3                                   # secrets checked for all, up front
    assert [c[-1].rsplit("/", 1)[-1] for c in runs] == ["smlm-workshop-1", "smlm-workshop-2"]
    assert runs[0][1:4] == ["up", "--yes", "--no-tmux"]
    runs.clear()
    r = CliRunner().invoke(mod.instances_cmd, ["up", "--dir", str(base), "--keep-going"])
    assert len(runs) == 3


def test_instances_clean_named(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import rodeo.commands.instances_cmd as mod

    base = _seed_instances(tmp_path)
    runs = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or _Completed())
    r = CliRunner().invoke(mod.instances_cmd, ["clean", "smlm-workshop-2", "--dir", str(base), "--yes"])
    assert r.exit_code == 0, r.output
    assert len(runs) == 1 and runs[0][1:3] == ["clean", "--yes"] and runs[0][4].endswith("smlm-workshop-2")
    r = CliRunner().invoke(mod.instances_cmd, ["clean", "nope", "--dir", str(base), "--yes"])
    assert r.exit_code != 0 and "no instance lab named nope" in r.output


def test_instances_list_reports_room(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import rodeo.commands.instances_cmd as mod

    base = _seed_instances(tmp_path)
    monkeypatch.setattr("rodeo.preflight._read_avail_mib", lambda: 100 * 1024)
    monkeypatch.setattr("rodeo.preflight._free_gib", lambda d: 2000)
    r = CliRunner().invoke(mod.instances_cmd, ["list", "--dir", str(base)])
    assert r.exit_code == 0, r.output
    assert "Room for about 3 more instance(s)" in r.output      # 100 GiB / 28 GiB each


# ── optional: VMs in a cloud account (lab_in_a_box.cloud) ───────────────────

CLOUD_PLAN = {
    "type": "lab-in-a-box", "name": "cloudlab",
    "lab_in_a_box": {
        "iso_image": "ami-0123456789abcdef0",
        "cloud": {"cloudtype": "aws", "account": "aws-lab",
                  "settings": {"AWS_REGION": "eu-north-1", "AWS_ACCESS_KEY_ID": "??aws_access_key_id",
                               "AWS_SECRET_ACCESS_KEY": "??aws_secret_access_key"}},
        "images": [{"name": "ami-0123456789abcdef0", "url": "https://x/i", "sha256": SHA}],
    },
}


def _cloud_cfg(tmp_path, plan=None):
    lab = tmp_path / "cloudlab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump(plan or CLOUD_PLAN))
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(
        yaml.safe_dump({"aws_access_key_id": "AKIAX", "aws_secret_access_key": "s3cr3t"}))
    return load_config(lab / "rodeo-plan.yaml")


def test_cloud_validation():
    ok = host.cloud({"lab_in_a_box": {"cloud": {"cloudtype": "hetzner", "account": "h1"}}})
    assert ok == {"cloudtype": "hetzner", "account": "h1", "settings": {}}
    for bad in ({"cloudtype": "vmware", "account": "a"}, {"cloudtype": "aws", "account": "../a"},
                {"cloudtype": "aws", "account": "a", "settings": {"lower": "x"}}):
        with pytest.raises(ConfigError):
            host.cloud({"lab_in_a_box": {"cloud": bad}})
    assert host.cloud({}) is None


def test_cloud_lab_json(tmp_path):
    from rodeo.labinabox import unknown_fields

    lab, _ = build_lab_json(_cloud_cfg(tmp_path))
    node = lab["nodes"]["vm1.rodeo.lab"]
    assert not {"myip", "mymac", "BRIDGE"} & set(node)
    assert lab["common"]["cloud_account"] == "aws-lab"
    assert lab["common"]["ISO_IMAGE"] == "ami-0123456789abcdef0"
    assert "ISO_URL" not in lab["common"] and not {"mygw", "mydns", "mymask"} & set(lab["common"])
    assert "s3cr3t" not in json.dumps(lab)                          # credentials never in lab.json
    assert unknown_fields(lab, _SCHEMA) == []


def test_cloud_credentials_are_operator_secrets(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    generated, missing = ensure_plan_secrets(CLOUD_PLAN, tmp_path / "s.yaml")
    assert generated == [] and missing == ["aws_access_key_id", "aws_secret_access_key"]


def test_cloud_credentials_file(tmp_path, host_sandbox, monkeypatch):
    root, calls = host_sandbox
    monkeypatch.setattr(host, "CREDENTIALS_DIR", root / "credentials")
    cfg = _cloud_cfg(tmp_path)
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 0, lines
    creds = root / "credentials" / "aws-lab.yaml"
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    body = yaml.safe_load(creds.read_text())
    assert body == {"cloudtype": "aws", "unencrypted": True, "AWS_REGION": "eu-north-1",
                    "AWS_ACCESS_KEY_ID": "AKIAX", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}
    assert not any(c[0] in ("virsh", "firewall-cmd") for c in calls)     # no hypervisor setup
    assert runner.firewalld == 0 and not (root / "hosts").exists()
    assert "LAB_HOSTS_FILE=/etc/hosts" in host.LAB_CREATION_CFG.read_text()

    creds.write_text("cloudtype: aws\n")                                 # someone else's file now
    runner = _Runner(cfg, tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1 and any("wasn't written by rodeo" in line for line in lines)


def test_cloud_needs_no_hypervisor_tools(tmp_path, host_sandbox, monkeypatch):
    root, _ = host_sandbox
    monkeypatch.setattr(host, "CREDENTIALS_DIR", root / "credentials")
    monkeypatch.setattr(phase.shutil, "which",
                        lambda n: None if n in ("virsh", "virt-install", "qemu-img", "aws", "python3.11",
                                                "python3.12") else "/x")
    runner = _Runner(_cloud_cfg(tmp_path), tmp_path)
    lines = [e.line for e in phase.stream_labinabox_host(runner) if isinstance(e, LogLine)]
    assert runner._last_rc == 1
    assert any("lacks aws " in line for line in lines)              # only the provider CLI is missing


def test_cloud_addresses_learned_and_used(tmp_path, monkeypatch):
    cfg = _cloud_cfg(tmp_path)
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n13.48.1.2  vm1.rodeo.lab vm1  # lab-in-a-box\n"
                     "13.48.9.9  other.lab other  # lab-in-a-box\n")
    monkeypatch.setattr(phase, "ETC_HOSTS", hosts)
    monkeypatch.setattr(phase.subprocess, "run", lambda *a, **k: _Completed(stdout="$6$h\n"))
    runner = _Runner(cfg, tmp_path)
    list(phase.stream_labinabox(runner))
    assert json.loads((Path(cfg["plan_dir"]) / phase.NODES_RELPATH).read_text()) == {"vm1": "13.48.1.2"}
    assert load_config(Path(cfg["plan_dir"]) / "rodeo-plan.yaml")["vms"]["vm1"]["ip"] == "13.48.1.2"


def test_hosts_file_ips():
    text = "10.0.0.1  a.lab a  # lab-in-a-box\n10.0.0.2 b.lab b\n# 10.0.0.3  c.lab c  # lab-in-a-box\n"
    assert host.hosts_file_ips(text, ["a.lab", "b.lab", "c.lab"]) == {"a": "10.0.0.1"}


def test_cloud_skips_kvm_preflight(tmp_path, monkeypatch):
    from rodeo import preflight

    cfg = _cloud_cfg(tmp_path)
    monkeypatch.setattr(preflight, "_nested_enabled", lambda: pytest.fail("no KVM check for cloud VMs"))
    preflight.run_preflight(cfg, tmp_path)


def test_lab_secrets_found_next_to_a_plan_only_lab(tmp_path):
    cfg = _cloud_cfg(tmp_path)
    (Path(cfg["plan_dir"]) / ".rodeo-secrets.yaml").write_text(yaml.safe_dump({"aws_access_key_id": "LABKEY"}))
    lab, _ = build_lab_json(load_config(Path(cfg["plan_dir"]) / "rodeo-plan.yaml"))
    assert load_config(Path(cfg["plan_dir"]) / "rodeo-plan.yaml")["lab_in_a_box"]["cloud"]["settings"][
        "AWS_ACCESS_KEY_ID"] == "LABKEY"


def test_capacity_command_asks_each_hypervisor(tmp_path):
    cfg = tmp_path / "lab_creation.cfg"
    cfg.write_text("REMOTE_HOST=kvm1\nVIRT_SRV='qemu+ssh://root@kvm1/system?keyfile=/k'\nKVM_HOSTS='kvm1 kvm2'\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "virsh").write_text('#!/bin/sh\necho "uri=$2" >> "$LOG"\necho "Total:    1048576 KiB"\n')
    (fake / "virsh").chmod(0o755)
    log = tmp_path / "log"
    script = host.CAPACITY_COMMAND.replace("/etc/lab_creation.cfg", str(cfg))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{fake}:/usr/bin:/bin", "LOG": str(log)})
    assert host.parse_capacity(r.stdout) == {"kvm1": 1024, "kvm2": 1024}
    assert log.read_text().splitlines() == ["uri=qemu+ssh://root@kvm1/system?keyfile=/k",
                                            "uri=qemu+ssh://root@kvm2/system?keyfile=.ssh/id_rsa"]


def test_bake_and_scratch_sync_the_same_channels():
    """The bake plan lists every channel flat; the scratch variant lists the base
    channels and gets the children from the activation keys. Keep them equal."""
    plan = yaml.safe_load((SMLM / "rodeo-plan.yaml").read_text())
    bake = yaml.safe_load((SMLM / "bake" / "rodeo-plan.yaml").read_text())
    keys = plan["lab_in_a_box"]["sections"]["smlm"]["smlm_activation_keys"]
    scratch = set(plan["lab_in_a_box"]["variants"]["scratch"]["sections"]["smlm"]["smlm_channels"])
    scratch |= {c for k in keys for c in k["smlm_activation_key_child_channels"].split()}
    assert set(bake["lab_in_a_box"]["sections"]["smlm"]["smlm_channels"]) == scratch


def test_doctor_reports_image_age_in_a_lab_dir(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rodeo.commands import doctor_cmd as doc

    cfg = _smlm_cfg(tmp_path, FULL_SECRETS)
    monkeypatch.chdir(cfg["config_dir"])
    monkeypatch.setattr(host, "image_age_days", lambda url: 60.0)
    r = CliRunner().invoke(doc.doctor_cmd, [])
    assert r.exit_code == 0, r.output
    assert "smlm-workshop-server.qcow2: 60 days old — over max_age_days 45" in r.output


def test_cloud_exposed_services_open_ports_on_every_provider(tmp_path):
    from rodeo.labinabox import unknown_fields

    lab_dir = tmp_path / "lab"
    shutil.copytree(SMLM, lab_dir)
    plan = yaml.safe_load((lab_dir / "rodeo-plan.yaml").read_text())
    plan["lab_in_a_box"]["cloud"] = {"cloudtype": "aws", "account": "aws-lab",
                                     "settings": {"AWS_REGION": "eu-north-1"}}
    (lab_dir / "rodeo-plan.yaml").write_text(yaml.safe_dump(plan))
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump(FULL_SECRETS))
    cfg = load_config(lab_dir / "rodeo-plan.yaml")
    lab, warnings = build_lab_json(cfg)
    assert lab["nodes"]["smlm.rodeo.lab"]["open_ports"] == ["443"]
    assert "open_ports" not in lab["nodes"]["centos7.rodeo.lab"]
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"
    assert unknown_fields(lab, _SCHEMA) == []

    for cloudtype in ("gcp", "hetzner", "alibaba", "scaleway", "upcloud", "ovhcloud", "exoscale"):
        cfg["lab_in_a_box"]["cloud"]["cloudtype"] = cloudtype
        lab, warnings = build_lab_json(cfg)
        assert lab["nodes"]["smlm.rodeo.lab"]["open_ports"] == ["443"], cloudtype
        assert not any("open" in w for w in warnings), cloudtype


# ── cloud labs: status/start/stop through lab-in-a-box's vm_power.py ────────

def test_power_argv_local_and_remote(tmp_path):
    cfg = _cloud_cfg(tmp_path)
    local = host.power_argv(cfg, "status", ["vm1.rodeo.lab"])
    assert local == [str(host.LIAB_BIN / "vm_power.py"),
                     str(Path(cfg["plan_dir"]) / ".labinabox" / "lab.json"), "status", "vm1.rodeo.lab"]
    cfg["lab_in_a_box"]["target"] = {"host": "auto1"}
    remote = host.power_argv(cfg, "stop", ["vm1.rodeo.lab"], identity="/k")
    assert remote[-2] == "root@auto1"
    assert remote[-1] == f"{host.LIAB_BIN / 'vm_power.py'} .rodeo-labs/cloudlab/lab.json stop vm1.rodeo.lab"
    with pytest.raises(ConfigError):
        host.power_argv(cfg, "reboot", [])


def test_cloud_power_maps_states(tmp_path, monkeypatch):
    cfg = _cloud_cfg(tmp_path)
    monkeypatch.setattr(host.subprocess, "run",
                        lambda *a, **k: _Completed(stdout='{"vm1.rodeo.lab": "running"}'))
    assert host.cloud_power(cfg, "status", ["vm1"]) == {"vm1": "running"}
    monkeypatch.setattr(host.subprocess, "run",
                        lambda *a, **k: _Completed(returncode=127, stderr="vm_power.py: not found"))
    assert host.cloud_power(cfg, "status", ["vm1"]) == {"vm1": "error: vm_power.py: not found"}


def test_status_report_for_a_cloud_lab(tmp_path, monkeypatch):
    from rodeo.service.status import status_report

    cfg = _cloud_cfg(tmp_path)
    monkeypatch.setattr(host, "cloud_power", lambda c, action, names: {n: "stopped" for n in names})
    monkeypatch.setattr("rodeo.engine.libvirt.LibvirtDriver", lambda uri: pytest.fail("no libvirt for cloud VMs"))
    report = status_report(cfg)
    assert report["vms"] == [{"name": "vm1", "state": "stopped", "autostart": False}]
    assert "libvirt_error" not in report


def test_cloud_restart_waits_for_stopped():
    from rich.console import Console

    from rodeo.commands import _cloud_vms

    seq = iter(["stopping", "stopping", "stopped"])
    actions = []

    def fake(cfg, action, names):
        actions.append(action)
        if action == "status":
            return {n: next(seq) for n in names}
        return {n: "pending" if action == "start" else "stopping" for n in names}

    import rodeo.labinabox_host as h
    orig = h.cloud_power
    h.cloud_power = fake
    try:
        slept = []
        ok = _cloud_vms.power({}, "restart", ["vm1"], Console(file=open("/dev/null", "w")), sleep=slept.append)
    finally:
        h.cloud_power = orig
    assert ok and actions == ["stop", "status", "status", "status", "start"] and len(slept) == 2


def test_start_and_stop_commands_for_a_cloud_lab(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import rodeo.commands.start_cmd as start_mod
    import rodeo.commands.stop_cmd as stop_mod

    cfg = _cloud_cfg(tmp_path)
    plan = Path(cfg["plan_dir"]) / "rodeo-plan.yaml"
    calls = []
    monkeypatch.setattr(host, "cloud_power", lambda c, action, names: calls.append(action) or
                        {n: "pending" if action == "start" else "stopping" for n in names})
    for mod in (start_mod, stop_mod):
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod, "LibvirtDriver", lambda uri: pytest.fail("no libvirt for cloud VMs"))
    r = CliRunner().invoke(start_mod.start_cmd, ["--yes", "--config", str(plan)])
    assert r.exit_code == 0, r.output
    assert "vm1: pending" in r.output
    r = CliRunner().invoke(stop_mod.stop_cmd, ["--yes", "--config", str(plan)])
    assert r.exit_code == 0, r.output
    assert calls == ["start", "stop"]


# ── SLES 15 SP7 bake + aws variants ───────────────────────────────────────────────

AWS_SECRETS = {**FULL_SECRETS, "aws_access_key_id": "AKIAX", "aws_secret_access_key": "s3cr3t",
               "aws_security_group_id": "sg-1", "smlm_image_ami": "ami-0smlm", "centos7_ami": "ami-0c7",
               "sles15sp5_ami": "ami-0sp5", "sles15sp6_ami": "ami-0sp6", "ubuntu2404_ami": "ami-0ub"}


def test_workshop_aws_variant(tmp_path):
    from rodeo.labinabox import unknown_fields

    cfg = _smlm_cfg(tmp_path, AWS_SECRETS)
    cfg["lab_in_a_box"]["variant"] = "aws"
    lab, warnings = build_lab_json(cfg)
    assert lab["common"]["cloud_account"] == "smlm-workshop"
    smlm = lab["nodes"]["smlm.rodeo.lab"]
    assert smlm["ISO_IMAGE"] == "ami-0smlm" and smlm["config_method"] == "cloud-init"
    assert smlm["open_ports"] == ["443"] and "ISO_URL" not in smlm
    c7 = lab["nodes"]["zzcentos7.rodeo.lab"]
    assert c7["ISO_IMAGE"] == "ami-0c7" and c7["config_method"] == "cloud-init"
    assert not {"VM_BOOT", "VM_DSK_BUS", "VM_NET_MODEL"} & set(c7)
    assert c7["addons"][0]["client_registration"]["client_registration_profile_name"] == "airco-dh4a-prod"
    assert lab["nodes"]["zzsles15b.rodeo.lab"]["ISO_IMAGE"] == "ami-0sp6"
    assert unresolved_placeholders(lab) == []
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"
    assert unknown_fields(lab, _SCHEMA) == []


def test_aws_variant_asks_only_for_aws_values(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    plan = yaml.safe_load((SMLM / "rodeo-plan.yaml").read_text())
    plan["lab_in_a_box"]["variant"] = "aws"
    _, missing = ensure_plan_secrets(plan, tmp_path / "s.yaml")
    assert {"aws_access_key_id", "smlm_image_ami", "ubuntu2404_ami"} <= set(missing)
    assert not {"smlm_image_url", "smlm_image_sha256", "sles15sp5_image_url"} & set(missing)  # qcow2s unused


def test_bake_starts_from_sles15sp7(tmp_path):
    from rodeo.labinabox import unknown_fields

    lab_dir = seed_lab("smlm-workshop", tmp_path / "lab")
    (tmp_path / ".rodeo").mkdir(exist_ok=True)
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump({**SCRATCH_SECRETS, **AWS_SECRETS,
                                                                        "sles15sp7_ami": "ami-0sp7"}))
    plan = lab_dir / "bake" / "rodeo-plan.yaml"
    lab, _ = build_lab_json(load_config(plan))
    node = lab["nodes"]["smlm.rodeo.lab"]
    assert node["ISO_IMAGE"] == "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2"
    assert node["config_method"] == "cloud-init" and "smlm_byos" not in lab["smlm"]
    data = yaml.safe_load(plan.read_text())
    data["lab_in_a_box"]["variant"] = "aws"
    plan.write_text(yaml.safe_dump(data))
    lab, _ = build_lab_json(load_config(plan))
    node = lab["nodes"]["smlm.rodeo.lab"]
    assert node["ISO_IMAGE"] == "ami-0sp7" and lab["common"]["cloud_account"] == "smlm-bake"
    lab["common"]["ROOT_PWD_HASH"] = "$6$x"
    assert unknown_fields(lab, _SCHEMA) == []


def test_workshop_boots_the_baked_server_with_cloud_init(tmp_path):
    lab, _ = build_lab_json(_smlm_cfg(tmp_path, FULL_SECRETS))
    smlm = lab["nodes"]["smlm.rodeo.lab"]
    assert smlm["ISO_IMAGE"] == "smlm-workshop-server.qcow2" and smlm["config_method"] == "cloud-init"


def test_bake_scripts_are_valid_bash():
    for script in ("generalise.sh", "export-image.sh", "export-ami.sh"):
        subprocess.run(["bash", "-n", str(SMLM / "bake" / script)], check=True)
