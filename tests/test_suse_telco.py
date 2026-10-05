"""suse-telco registration: plan renders, catalog does not fall back to Harvester."""
from __future__ import annotations

import re

import yaml
from click.testing import CliRunner

from rodeo.commands.plan_cmd import plan_cmd
from rodeo.inventory import build_inventory
from rodeo.labseed import PROFILE_EXAMPLE, seed_lab
from rodeo.profiles import get_profile
from rodeo.providers.instance_catalog import catalog_for_profile

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(output: str) -> str:
    return " ".join(_ANSI.sub("", output).split())


def test_profile_phase_order_and_edge_unchanged():
    telco = get_profile("suse-telco")
    assert telco.phases == [
        "kvm_host",
        "vms",
        "bmc",
        "boot",
        "mgmt",
        "images",
        "enroll",
        "finalise",
        "custom_scripts",
    ]
    assert telco.ansible_phases == frozenset(["kvm_host", "vms", "bmc"])
    assert "images" in telco.no_cache_phases
    assert "enroll" in telco.no_cache_phases
    edge = get_profile("suse-edge")
    assert edge.phases == [
        "kvm_host",
        "vms",
        "boot",
        "rancher",
        "elemental",
        "apply",
        "finalise",
        "custom_scripts",
    ]


def test_definition_topology():
    inv = build_inventory({"type": "suse-telco", "name": "suse-telco-test"})
    assert [n["name"] for n in inv["vm_nodes"]] == ["mgmt", "site-co", "site-ran"]
    assert [n["flavor"] for n in inv["vm_nodes"]] == ["mgmt", "site", "site"]
    net = inv["libvirt_network"]
    assert net["name"] == "default"
    assert net["bridge"] == "virbr0"
    assert net["cidr"] == "192.168.122.0/24"
    assert net["gateway"] == "192.168.122.1"
    assert {h["name"]: h["ip"] for h in net["dhcp_hosts"]} == {
        "mgmt": "192.168.122.10",
        "site-co": "192.168.122.21",
        "site-ran": "192.168.122.32",
    }


def test_plan_renders_telco_topology(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-telco\nname: suse-telco-test\n")
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "mgmt" in out
    assert "site-co" in out
    assert "site-ran" in out
    assert "16384 MiB / 8 vcpu" in out
    assert "8192 MiB / 4 vcpu" in out
    for phase in ("kvm_host", "vms", "bmc", "boot", "mgmt", "images", "enroll", "finalise", "custom_scripts"):
        assert phase in out
    assert "elemental pending" not in out
    assert "pxe_server pending" not in out


def test_aws_catalog_does_not_fall_back_to_harvester():
    telco = catalog_for_profile("suse-telco")
    harvester = catalog_for_profile("harvester")
    assert telco["recommended"].instance_type == "m8id.4xlarge"
    assert telco["performance"].instance_type == "m7i.metal-24xl"
    assert harvester["recommended"].instance_type == "m8id.8xlarge"
    assert telco is not harvester


def test_example_is_registered_and_seeds(tmp_path):
    assert PROFILE_EXAMPLE["suse-telco"] == "suse-telco"
    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["type"] == "suse-telco"
    assert plan["deployment_target"] == "baremetal"
    assert plan["network"]["dns_domain"] == "northline.telco"
    assert plan["network"]["gateway"] == "192.168.122.1"
    assert plan["rancher_tls"]["source"] == "rancher"
    assert plan["resources"]["mgmt"]["memory_mib"] == 16384
    assert plan["resources"]["site"]["vcpu"] == 4


def test_versions_match_telco_37_release_notes():
    v = get_profile("suse-telco").versions
    assert v["rancher"] == "2.15.1"
    assert v["rke2"] == "v1.36.3+rke2r1"
    assert v["cert_manager"] == "v1.20.1"  # cert-manager release tags carry the v
    assert v["turtles_providers"] == "307.0.8+up0.27.0"
    assert v["metal3"] == "307.0.31+up0.16.0"
    assert v["kubevirt"] == "307.0.3+up0.8.0"
    assert v["cdi"] == "307.0.3+up0.8.0"
    assert v["eib"] == "1.3.4"
    assert v["kiwi_builder"] == "10.2.29.1"


def test_example_plan_matches_profile_versions(tmp_path):
    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    profile = get_profile("suse-telco").versions
    for key, value in plan["versions"].items():
        assert profile[key] == value, key


def test_scc_code_is_operator_secret(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    secrets = tmp_path / "secrets.yaml"
    generated, missing = ensure_plan_secrets(plan, path=secrets)
    assert "scc_registration_code" in missing
    assert "scc_registration_code" not in generated


def test_site_ran_vip_is_not_a_node_address():
    inv = build_inventory({"type": "suse-telco", "name": "suse-telco-test"})
    node_ips = {n["ip"] for n in inv["vm_nodes"]}
    sites = get_profile("suse-telco").extra_cfg()["telco"]["sites"]
    assert sites["site-co"]["control_plane_endpoint"] == "192.168.122.21"
    assert sites["site-ran"]["control_plane_endpoint"] == "192.168.122.22"
    assert "192.168.122.22" not in node_ips
    assert sites["site-ran"]["namespace"] == "northline"


def test_network_template_skips_empty_vip():
    from pathlib import Path

    import jinja2

    import rodeo

    tpl = Path(rodeo.__file__).parent / "data/ansible/roles/vms/templates/network.xml.j2"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl.parent)))
    base = {"lab_dns_domain": "northline.telco", "libvirt_network_gateway": "192.168.122.1", "vm_nodes": []}
    assert "<host ip=''>" not in env.get_template(tpl.name).render(harvester_vip="", **base)
    assert "virtualization.northline.telco" in env.get_template(tpl.name).render(
        harvester_vip="192.168.122.10", **base
    )


# --- bmc phase (sushy-tools) ---

_SITE_UUIDS = {
    "a1000002-0f00-4000-8000-000000000021",
    "a1000003-0f00-4000-8000-000000000032",
}
_MGMT_UUID = "a1000001-0f00-4000-8000-000000000010"


def _telco_cfg() -> dict:
    cfg = get_profile("suse-telco").default_cfg()
    cfg.update({"type": "suse-telco", "name": "suse-telco-test"})
    cfg["credentials"] = {"bmc_password": "Bmc-Secret-123"}
    return cfg


def test_bmc_phase_runs_after_vms_through_ansible():
    telco = get_profile("suse-telco")
    assert telco.phases.index("bmc") == telco.phases.index("vms") + 1
    assert telco.ansible_phases == frozenset(["kvm_host", "vms", "bmc"])
    assert "bmc" in telco.no_cache_phases


def test_bmc_vars_expose_only_site_hosts():
    v = get_profile("suse-telco").ansible_vars(_telco_cfg())
    assert v["bmc_listen_ip"] == "192.168.122.1"
    assert v["bmc_port"] == 8000
    assert v["bmc_password"] == "Bmc-Secret-123"
    assert set(v["bmc_allowed_instances"]) == _SITE_UUIDS
    assert _MGMT_UUID not in v["bmc_allowed_instances"]
    assert "@sha256:" in v["bmc_image"]  # never a moving tag


def test_runner_vars_file_carries_bmc_vars(tmp_path, monkeypatch):
    from rodeo.engine.runner import DeployRunner

    monkeypatch.setenv("HOME", str(tmp_path))
    data = yaml.safe_load(DeployRunner(_telco_cfg(), tmp_path)._write_vars_file().read_text())
    assert data["bmc_password"] == "Bmc-Secret-123"
    assert set(data["bmc_allowed_instances"]) == _SITE_UUIDS
    assert data["rancher_ip"] == "192.168.122.10"


def test_other_profiles_get_no_bmc_vars():
    assert get_profile("suse-edge").ansible_vars({}) == {}


def test_bmc_vars_are_consumed_by_the_role():
    from pathlib import Path

    import rodeo

    role = Path(rodeo.__file__).parent / "data/ansible/roles/bmc"
    text = "\n".join(
        p.read_text() for p in role.rglob("*") if p.is_file() and "defaults" not in p.parts
    )
    for key in get_profile("suse-telco").ansible_vars(_telco_cfg()):
        if key.startswith("bmc_"):
            assert key in text, key
    playbook = (role.parent.parent / "playbook.yml").read_text()
    assert "role: bmc" in playbook


def test_sushy_config_renders_listen_ip_and_allow_list():
    import json
    from pathlib import Path

    import jinja2

    import rodeo

    tpl_dir = Path(rodeo.__file__).parent / "data/ansible/roles/bmc/templates"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_dir)))
    env.filters["to_json"] = json.dumps
    v = get_profile("suse-telco").ansible_vars(_telco_cfg())
    conf = env.get_template("sushy-emulator.conf.j2").render(
        bmc_libvirt_uri="qemu:///system", **v
    )
    ns: dict = {}
    exec(conf, ns)  # sushy reads this file as Python
    assert ns["SUSHY_EMULATOR_LISTEN_IP"] == "192.168.122.1"
    assert ns["SUSHY_EMULATOR_LISTEN_PORT"] == 8000
    assert set(ns["SUSHY_EMULATOR_ALLOWED_INSTANCES"]) == _SITE_UUIDS
    assert ns["SUSHY_EMULATOR_AUTH_FILE"] == "/etc/sushy/htpasswd"
    unit = env.get_template("rodeo-sushy.container.j2").render(
        bmc_service="rodeo-sushy", bmc_dir="/etc/rodeo/sushy", **v
    )
    assert f"Image={v['bmc_image']}" in unit
    assert "Network=host" in unit
    assert "--config /etc/sushy/sushy-emulator.conf" in unit


def test_example_plan_generates_bmc_password(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    generated, missing = ensure_plan_secrets(plan, path=tmp_path / "secrets.yaml")
    assert "bmc_password" in generated
    assert "bmc_password" not in missing


# --- vms phase ---

def _vms_role_vars(tmp_path, monkeypatch) -> dict:
    """Role defaults + group_vars + the vars file the runner writes, as Ansible layers them."""
    from pathlib import Path

    import rodeo
    from rodeo.engine.runner import DeployRunner

    monkeypatch.setenv("HOME", str(tmp_path))
    ansible = Path(rodeo.__file__).parent / "data/ansible"
    cfg = _telco_cfg()
    cfg["credentials"]["mgmt_root_password"] = "Mgmt-Secret-123"
    data: dict = {}
    data.update(yaml.safe_load((ansible / "roles/vms/defaults/main.yml").read_text()))
    data.update(yaml.safe_load((ansible / "group_vars/all.yml").read_text()))
    data.update(yaml.safe_load(DeployRunner(cfg, tmp_path)._write_vars_file().read_text()))
    data["ssh_public_key"] = "ssh-ed25519 AAAAtest rodeo"
    return data


def _render(template: str, **ctx) -> str:
    from pathlib import Path

    import jinja2

    import rodeo

    tpl_dir = Path(rodeo.__file__).parent / "data/ansible/roles/vms/templates"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_dir)), undefined=jinja2.StrictUndefined)
    return env.get_template(template).render(**ctx)


def test_vms_runs_through_ansible():
    assert "vms" in get_profile("suse-telco").ansible_phases


def test_vm_xml_mgmt_boots_base_disk_with_combustion_seed(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    v = _vms_role_vars(tmp_path, monkeypatch)
    node = next(n for n in v["vm_nodes"] if n["name"] == "mgmt")
    dom = ET.fromstring(_render("vm.xml.j2", item=node, **v))
    assert dom.find("os").get("firmware") == "efi"
    assert dom.find("os/loader") is None  # auto-selection, no pinned loader
    assert dom.find("memory").text == "16384"
    assert dom.find("vcpu").text == "8"
    sources = [d.find("source").get("file") for d in dom.findall("devices/disk")]
    assert sources == [
        "/var/lib/libvirt/images/mgmt-vda.qcow2",
        "/var/lib/libvirt/images/mgmt-combustion.iso",
    ]
    assert "rancher-cloud-init" not in ET.tostring(dom).decode()
    assert dom.find("devices/interface/mac").get("address") == "02:00:00:0F:62:10"


def test_vm_xml_site_has_blank_disk_and_no_cdrom(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    v = _vms_role_vars(tmp_path, monkeypatch)
    for name, mac in (("site-co", "02:00:00:0F:62:21"), ("site-ran", "02:00:00:0F:62:32")):
        node = next(n for n in v["vm_nodes"] if n["name"] == name)
        dom = ET.fromstring(_render("vm.xml.j2", item=node, **v))
        assert dom.find("os").get("firmware") == "efi"
        assert dom.find("os/firmware/feature[@name='secure-boot']").get("enabled") == "no"
        assert dom.find("os").get("machine") is None
        assert dom.find("os/type").get("machine") == "q35"  # sushy adds a SATA CD-ROM on q35
        disks = dom.findall("devices/disk")
        assert [d.get("device") for d in disks] == ["disk"]
        assert disks[0].find("source").get("file") == f"/var/lib/libvirt/images/{name}-vda.qcow2"
        assert dom.find("uuid").text == node["uuid"]  # the Redfish system id
        assert dom.find("cpu").get("mode") == "host-passthrough"  # nested virt for lab 07
        assert dom.find("memory").text == "8192"
        assert dom.find("devices/interface/mac").get("address") == mac


def test_other_flavors_keep_their_vm_xml(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    v = _vms_role_vars(tmp_path, monkeypatch)
    v["libvirt_flavors"]["rancher"] = {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60}
    node = {"name": "rancher", "uuid": "u", "flavor": "rancher", "mgmt_mac": "02:00:00:00:00:09"}
    dom = ET.fromstring(_render("vm.xml.j2", item=node, **v))
    assert dom.find("os").get("firmware") is None
    assert dom.findall("devices/disk")[1].find("source").get("file").endswith("rancher-cloud-init.iso")


def test_combustion_script_is_valid_bash(tmp_path, monkeypatch):
    import subprocess

    v = _vms_role_vars(tmp_path, monkeypatch)
    node = next(n for n in v["vm_nodes"] if n["name"] == "mgmt")
    script = _render("telco-combustion-script.j2", item=node, root_password_hash="$6$x$y", **v)
    assert "combustion: network" not in script
    assert "echo 'mgmt' > /etc/hostname" in script
    assert "hostnamectl set-hostname" not in script
    assert "ssh-ed25519 AAAAtest rodeo" in script
    assert "growfs /" in script
    path = tmp_path / "script"
    path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0


def test_imagecache_resolves_to_mgmt(tmp_path, monkeypatch):
    v = _vms_role_vars(tmp_path, monkeypatch)
    assert v["lab_extra_dns_hosts"] == [{"ip": "192.168.122.10", "hostname": "imagecache.local"}]
    net = _render("network.xml.j2", **v)
    assert "<host ip='192.168.122.10'>\n      <hostname>imagecache.local</hostname>" in net
    assert "<host ip=''>" not in net


def test_vms_vars_base_image_and_flavors(tmp_path, monkeypatch):
    v = _vms_role_vars(tmp_path, monkeypatch)
    assert v["telco_base_image"]["path"] == (
        "/var/lib/libvirt/images/SL-Micro.x86_64-6.2-Base-GM.raw.xz"
    )
    assert v["libvirt_flavors"]["mgmt"] == {"memory_mib": 16384, "vcpu": 8, "disk_gb": 80}
    assert v["libvirt_flavors"]["site"] == {"memory_mib": 8192, "vcpu": 4, "disk_gb": 40}
    assert v["mgmt_root_password"] == "Mgmt-Secret-123"


def test_telco_vars_are_consumed_by_ansible(tmp_path, monkeypatch):
    from pathlib import Path

    import rodeo

    ansible = Path(rodeo.__file__).parent / "data/ansible"
    text = "\n".join(
        p.read_text() for p in ansible.rglob("*") if p.is_file() and "defaults" not in p.parts
    )
    for key in get_profile("suse-telco").ansible_vars(_telco_cfg()):
        assert key in text, key


def test_example_plan_generates_mgmt_password(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    generated, _ = ensure_plan_secrets(plan, path=tmp_path / "secrets.yaml")
    assert "mgmt_root_password" in generated
    assert plan["telco"]["base_image"]["path"].endswith("SL-Micro.x86_64-6.2-Base-GM.raw.xz")


# --- boot + mgmt phases (TelcoPhase) ---

import subprocess as _sp  # noqa: E402


def _telco_phase(monkeypatch, tmp_path):
    from rodeo.engine.telco import TelcoPhase

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _telco_cfg()
    cfg["credentials"]["rancher_admin_password"] = "Rancher-Secret-123"
    return TelcoPhase(cfg)


def test_rke2_kubeconfig_only_for_telco():
    from rodeo.engine.rancher import RancherPhase
    from rodeo.engine.telco import TelcoPhase

    assert RancherPhase.KUBECONFIG == "/etc/rancher/k3s/k3s.yaml"
    assert TelcoPhase.KUBECONFIG == "/etc/rancher/rke2/rke2.yaml"


def test_telco_phase_targets_mgmt(monkeypatch, tmp_path):
    p = _telco_phase(monkeypatch, tmp_path)
    assert p.rancher_ip == "192.168.122.10"
    assert p.rancher_api == "https://192.168.122.10:30002"
    assert p.rancher_version == "2.15.1"
    assert p.rke2_version == "v1.36.3+rke2r1"
    assert p.site_names == ["site-co", "site-ran"]
    cfg = p.rke2_config()
    assert cfg["cni"] == "cilium"
    assert "192.168.122.10" in cfg["tls-san"]


def test_metal3_values_follow_single_node_quickstart(monkeypatch, tmp_path):
    p = _telco_phase(monkeypatch, tmp_path)
    assert p.metal3_values() == {
        "global": {"ironicIP": "192.168.122.10"},
        "metal3-ironic": {"service": {"type": "NodePort"}},
    }


def test_image_cache_manifest(monkeypatch, tmp_path):
    p = _telco_phase(monkeypatch, tmp_path)
    docs = list(yaml.safe_load_all(p.image_cache_manifest()))
    dep = next(d for d in docs if d["kind"] == "Deployment")
    pod = dep["spec"]["template"]["spec"]
    assert pod["hostNetwork"] is True
    c = pod["containers"][0]
    assert c["image"] == "registry.suse.com/suse/nginx:1.21"
    assert c["ports"][0]["containerPort"] == 8080
    media = next(v for v in pod["volumes"] if v["name"] == "media")
    assert media["hostPath"]["path"] == "/opt/media"
    conf = next(d for d in docs if d["kind"] == "ConfigMap")["data"]["nginx.conf"]
    assert "listen 8080;" in conf


def _run_mgmt(p, fail_on: str | None = None):
    scripts: list[str] = []

    def fake_run(cmd, timeout, input=None):
        scripts.append(input or " ".join(cmd))
        if fail_on and input and fail_on in input:
            return _sp.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        out = "Ready\n" if input and "get node" in input else "ok\n"
        return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")

    def http_step_ok():  # /ping and the Rancher API need a live server
        return True
        yield

    p._run = fake_run
    p._wait_ping = http_step_ok
    p._configure_api = http_step_ok
    events = list(p.stream_mgmt())
    return scripts, events


def test_mgmt_phase_runs_every_step_on_rke2(monkeypatch, tmp_path):
    p = _telco_phase(monkeypatch, tmp_path)
    scripts, _ = _run_mgmt(p)
    assert p.success, p.error
    joined = "\n".join(scripts)
    assert "k3s/k3s.yaml" not in joined
    assert "get.k3s.io" not in joined
    assert "INSTALL_RKE2_VERSION=v1.36.3+rke2r1" in joined
    order = [
        "get.rke2.io",
        "cert-manager",
        "rancher-prime/rancher",
        "patch svc rancher",
        "edge/charts/metal3 --version 307.0.31+up0.16.0",
        "rancher-turtles-providers --version 307.0.8+up0.27.0",
        "deployment/imagecache",
    ]
    positions = [next(i for i, s in enumerate(scripts) if marker in s) for marker in order]
    assert positions == sorted(positions)
    assert all(
        "KUBECONFIG=/etc/rancher/rke2/rke2.yaml" in s or "kubeconfig=/etc/rancher/rke2/rke2.yaml" in s
        for s in scripts
        if "kubectl" in s and "ln -sf" not in s
    )


def test_mgmt_phase_stops_at_first_failure(monkeypatch, tmp_path):
    p = _telco_phase(monkeypatch, tmp_path)
    scripts, _ = _run_mgmt(p, fail_on="edge/charts/metal3")
    assert not p.success
    assert p.error == "Metal3 install failed"
    assert not any("rancher-turtles-providers" in s for s in scripts)


def test_boot_starts_mgmt_only(monkeypatch, tmp_path):
    from rodeo.engine import libvirt as lvmod

    started: list[str] = []

    class FakeLV:
        def __init__(self, uri): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def net_start(self, name): ...
        def net_set_autostart(self, name, on): ...
        def get_vm(self, name):
            return type("I", (), {"state": "shut off"})()
        def start(self, name): started.append(name)

    monkeypatch.setattr(lvmod, "LibvirtDriver", FakeLV)
    p = _telco_phase(monkeypatch, tmp_path)
    p._run = lambda cmd, timeout, input=None: _sp.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    list(p.stream_boot())
    assert started == ["mgmt"]
    assert p.success


def test_run_phase_routes_boot_and_mgmt_to_telco(monkeypatch, tmp_path):
    from rodeo.engine import telco as telco_mod

    calls: list[str] = []

    class FakePhase:
        def __init__(self, cfg, stop=None):
            self.success = True
        def stream_boot(self):
            calls.append("boot")
            yield from ()
        def stream_mgmt(self):
            calls.append("mgmt")
            yield from ()

    monkeypatch.setattr(telco_mod, "TelcoPhase", FakePhase)

    class FakeRunner:
        cfg = _telco_cfg()
        stop = None
        _last_rc = None
        def _start_firewalld(self):
            calls.append("firewalld")
            yield from ()

    r = FakeRunner()
    prof = get_profile("suse-telco")
    list(prof.run_phase("boot", r, None))
    list(prof.run_phase("mgmt", r, None))
    assert calls == ["firewalld", "boot", "mgmt"]
    assert r._last_rc == 0


# --- images + enroll phases ---

def _phase_with_base(monkeypatch, tmp_path, scc="SCC-TEST-CODE"):
    from rodeo.engine.telco import TelcoPhase

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _telco_cfg()
    cfg["storage"] = {"image_dir": str(tmp_path / "images")}
    cfg["credentials"].update({
        "rancher_admin_password": "Rancher-Secret-123",  # gitleaks:allow (fake fixture)
        "mgmt_root_password": "Mgmt-Secret-123",  # gitleaks:allow (fake fixture)
        "scc_registration_code": scc,
    })
    cfg["ssh"] = {"identity_file": str(tmp_path / "id_ed25519")}
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAAtest rodeo\n")
    (tmp_path / "images").mkdir(exist_ok=True)
    base = tmp_path / "images" / "SL-Micro.x86_64-6.2-Base-GM.raw"
    if not base.exists():
        base.write_bytes(b"base")
    return TelcoPhase(cfg)


def test_eib_definition_matches_37_docs(monkeypatch, tmp_path):
    p = _phase_with_base(monkeypatch, tmp_path)
    d = p.eib_definition("$6$hash", "ssh-ed25519 KEY")
    assert d["apiVersion"] == "1.3"
    assert d["image"] == {
        "imageType": "raw",
        "arch": "x86_64",
        "baseImage": "SL-Micro.x86_64-6.2-Base-GM.raw",
        "outputImageName": "eibimage-downstream-cluster.raw",
    }
    os_ = d["operatingSystem"]
    assert os_["kernelArgs"] == ["ignition.platform.id=openstack"]
    assert os_["systemd"]["disable"] == [
        "rebootmgr", "transactional-update.timer", "transactional-update-cleanup.timer",
        "fstrim", "time-sync.target",
    ]
    assert os_["packages"] == {"packageList": ["jq"], "sccRegistrationCode": "SCC-TEST-CODE"}
    assert os_["users"][0]["sshKeys"] == ["ssh-ed25519 KEY"]


def test_growfs_scripts_match_docs():
    from pathlib import Path

    import rodeo
    from rodeo.engine.telco import _GROWFS_SCRIPT

    body = _GROWFS_SCRIPT.split("growfs() {", 1)[1]
    combustion = (
        Path(rodeo.__file__).parent / "data/ansible/roles/vms/templates/telco-combustion-script.j2"
    ).read_text()
    for line in body.splitlines():
        if line.strip() and not line.strip().startswith("#"):
            assert line.strip() in combustion, line


class _Recorder:
    def __init__(self, tmp_path, have_digest=""):
        self.cmds: list = []
        self.tmp_path = tmp_path
        self.have_digest = have_digest

    def run(self, cmd, timeout, input=None):
        self.cmds.append((cmd, input))
        if cmd[:2] == ["openssl", "passwd"]:
            return _sp.CompletedProcess(cmd, 0, stdout="$6$salt$hash\n", stderr="")
        if "cat /opt/media/" in " ".join(cmd):
            out = f"{self.have_digest}  eibimage-downstream-cluster.raw\n" if self.have_digest else ""
            return _sp.CompletedProcess(cmd, 0 if out else 1, stdout=out, stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")


def _fake_eib(phase, builds):
    def stream_local(cmd, timeout):
        builds.append(cmd)
        defn = phase.eib_dir / "downstream-cluster-config.yaml"
        assert defn.is_file() and oct(defn.stat().st_mode & 0o777) == "0o600"
        assert "SCC-TEST-CODE" in defn.read_text()
        (phase.eib_dir / phase.image_name).write_bytes(b"built-image")
        return 0
        yield

    phase._stream_local = stream_local


def test_images_builds_once_then_reuses(monkeypatch, tmp_path):
    p = _phase_with_base(monkeypatch, tmp_path)
    rec = _Recorder(tmp_path)
    p._run = rec.run
    builds: list = []
    _fake_eib(p, builds)
    list(p.stream_images())
    assert p.success, p.error
    assert builds and builds[0][:4] == ["podman", "run", "--rm", "--privileged"]
    assert "registry.suse.com/edge/3.7/edge-image-builder:1.3.4" in builds[0]
    assert not (p.eib_dir / "downstream-cluster-config.yaml").exists()  # SCC code removed
    assert (p.eib_dir / "base-images" / "SL-Micro.x86_64-6.2-Base-GM.raw").is_file()
    assert (p.eib_dir / "custom/scripts/01-fix-growfs.sh").is_file()
    publish = next(i for c, i in rec.cmds if i and "sha256sum -c" in i)
    assert "eibimage-downstream-cluster.raw.sha256" in publish
    assert any(c[0] == "scp" for c, _ in rec.cmds)

    p2 = _phase_with_base(monkeypatch, tmp_path)
    p2._run = _Recorder(tmp_path).run
    builds2: list = []
    _fake_eib(p2, builds2)
    list(p2.stream_images())
    assert p2.success and builds2 == []  # same inputs: no rebuild


def test_images_skips_copy_when_mgmt_has_it(monkeypatch, tmp_path):
    import hashlib

    p = _phase_with_base(monkeypatch, tmp_path)
    digest = hashlib.sha256(b"built-image").hexdigest()
    rec = _Recorder(tmp_path, have_digest=digest)
    p._run = rec.run
    _fake_eib(p, [])
    list(p.stream_images())
    assert p.success
    assert not any(c[0] == "scp" for c, _ in rec.cmds)


def test_images_requires_scc_code(monkeypatch, tmp_path):
    p = _phase_with_base(monkeypatch, tmp_path, scc="")
    list(p.stream_images())
    assert not p.success
    assert "scc_registration_code" in p.error


def test_baremetalhosts_match_workshop_selectors(monkeypatch, tmp_path):
    p = _phase_with_base(monkeypatch, tmp_path)
    docs = list(yaml.safe_load_all(p.baremetalhost_manifest()))
    kinds = [(d["kind"], d["metadata"]["name"]) for d in docs]
    assert ("Namespace", "northline") in kinds
    assert kinds.index(("Namespace", "northline")) < kinds.index(("BareMetalHost", "site-ran"))
    bmh = {d["metadata"]["name"]: d for d in docs if d["kind"] == "BareMetalHost"}
    co, ran = bmh["site-co"], bmh["site-ran"]
    # manifests/downstream-single.yaml hostSelector
    assert co["metadata"]["namespace"] == "default"
    assert co["metadata"]["labels"] == {"cluster-role": "control-plane", "site": "site-co"}
    # manifests/site-ran.yaml hostSelector
    assert ran["metadata"]["namespace"] == "northline"
    assert ran["metadata"]["labels"] == {
        "cluster-role": "control-plane", "deploy-region": "northline", "cluster-type": "site-ran",
    }
    assert co["spec"]["bootMACAddress"] == "02:00:00:0F:62:21"
    assert ran["spec"]["bootMACAddress"] == "02:00:00:0F:62:32"
    assert co["spec"]["bmc"]["address"] == (
        "redfish-virtualmedia://192.168.122.1:8000/redfish/v1/Systems/"
        "a1000002-0f00-4000-8000-000000000021"
    )
    assert co["spec"]["bmc"]["disableCertificateVerification"] is True
    assert co["spec"]["online"] is True
    assert co["spec"]["rootDeviceHints"] == {"deviceName": "/dev/vda"}
    secret = next(d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "site-co-bmc-credentials")
    assert secret["stringData"] == {"username": "admin", "password": "Bmc-Secret-123"}


def test_enroll_waits_for_available_and_reports_errors(monkeypatch, tmp_path):
    p = _phase_with_base(monkeypatch, tmp_path)
    answers = iter([
        "site-co registering <none>\nsite-ran registering Failed to get power state\n",
        "site-co inspecting <none>\nsite-ran inspecting <none>\n",
        "site-co available <none>\nsite-ran available <none>\n",
    ])

    def run(cmd, timeout, input=None):
        if input and "get bmh" in input:
            return _sp.CompletedProcess(cmd, 0, stdout=next(answers), stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    p._run = run
    p._sleep = lambda s: False
    lines = [getattr(e, "text", getattr(e, "line", str(e))) for e in p.stream_enroll()]
    assert p.success, p.error
    assert any("Failed to get power state" in str(x) for x in lines)


def test_run_phase_routes_images_and_enroll(monkeypatch):
    from rodeo.engine import telco as telco_mod

    calls: list[str] = []

    class FakePhase:
        def __init__(self, cfg, stop=None):
            self.success = True

        def stream_images(self):
            calls.append("images")
            yield from ()

        def stream_enroll(self):
            calls.append("enroll")
            yield from ()

    monkeypatch.setattr(telco_mod, "TelcoPhase", FakePhase)

    class FakeRunner:
        cfg = _telco_cfg()
        stop = None
        _last_rc = None

    r = FakeRunner()
    prof = get_profile("suse-telco")
    list(prof.run_phase("images", r, None))
    list(prof.run_phase("enroll", r, None))
    assert calls == ["images", "enroll"]
    assert r._last_rc == 0
