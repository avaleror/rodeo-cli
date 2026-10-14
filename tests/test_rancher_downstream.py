"""rancher / rancher-test profiles: Rancher-provisioned K3s and RKE2 clusters."""
from __future__ import annotations

import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

import rodeo
from rodeo import inventory, preflight
from rodeo.config import load_config
from rodeo.engine.rancher import RancherPhase
from rodeo.engine.runner import DeployRunner
from rodeo.labseed import PROFILE_EXAMPLE, seed_lab
from rodeo.profiles import get_profile, list_profile_types
from rodeo.providers.instance_catalog import catalog_for_profile
from rodeo.secretgen import plan_secret_keys

_TEMPLATES = Path(rodeo.__file__).parent / "data" / "ansible" / "roles" / "vms" / "templates"

FULL_CLUSTERS = {
    "k3s-single": ("k3s", ["k3s"]),
    "rke2-single": ("rke2", ["rke2"]),
    "rke2-ha": ("rke2", ["rke2-ha1", "rke2-ha2", "rke2-ha3"]),
}


def _lab_cfg(profile: str, tmp_path: Path) -> dict:
    """Seed the bundled example and load it the way `rodeo up` does."""
    lab = seed_lab(profile, tmp_path / "labs" / profile)
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    rodeo_dir = Path.home() / ".rodeo"
    rodeo_dir.mkdir(exist_ok=True)
    (rodeo_dir / "secrets.yaml").write_text(
        yaml.safe_dump({k: f"Secret-{k}-123" for k in plan_secret_keys(plan)})
    )
    return load_config("rodeo-plan.yaml", config_dir=str(lab))


# ---------- profiles ----------

def test_both_profiles_are_registered_and_seedable():
    assert {"rancher", "rancher-test"} <= set(list_profile_types())
    assert PROFILE_EXAMPLE["rancher"] == "rancher-lab-config"
    assert PROFILE_EXAMPLE["rancher-test"] == "rancher-test"


def test_rancher_profile_has_three_downstream_clusters():
    cfg = get_profile("rancher").default_cfg()
    assert list(cfg["vms"]) == ["rancher", "k3s", "rke2", "rke2-ha1", "rke2-ha2", "rke2-ha3"]
    got = {c["name"]: (c["distro"], c["nodes"]) for c in cfg["downstream_clusters"]}
    assert got == FULL_CLUSTERS


def test_rancher_test_profile_has_two_single_node_clusters():
    cfg = get_profile("rancher-test").default_cfg()
    assert list(cfg["vms"]) == ["rancher", "k3s", "rke2"]
    got = {c["name"]: (c["distro"], c["nodes"]) for c in cfg["downstream_clusters"]}
    assert got == {k: v for k, v in FULL_CLUSTERS.items() if k != "rke2-ha"}


@pytest.mark.parametrize("profile", ["rancher", "rancher-test"])
def test_versions_are_latest_rancher_supported(profile):
    ver = get_profile(profile).default_cfg()["versions"]
    assert ver["rancher"] == "2.15.2"
    assert ver["cert_manager"] == "v1.21.2"
    # Rancher 2.15.2 supports Kubernetes up to v1.36 for K3s/RKE2.
    assert ver["k3s"] == ver["downstream_k3s"] == "v1.36.4+k3s1"
    assert ver["downstream_rke2"] == "v1.36.4+rke2r1"


def test_minimal_sizing_per_node_flavor():
    res = get_profile("rancher").default_cfg()["resources"]
    assert res["k3s-node"] == {"memory_mib": 2048, "vcpu": 2, "disk_gb": 20}
    assert res["rke2-node"] == {"memory_mib": 4096, "vcpu": 2, "disk_gb": 30}


def test_profiles_without_downstream_clusters_get_no_key():
    for name in ("suse-virt", "suse-edge"):
        assert "downstream_clusters" not in get_profile(name).default_cfg()


# ---------- downstream nodes are never Harvester nodes ----------

@pytest.mark.parametrize("profile", ["rancher", "rancher-test"])
def test_downstream_nodes_are_not_harvester_nodes(profile, tmp_path):
    cfg = _lab_cfg(profile, tmp_path)
    assert inventory.harvester_vm_names(cfg) == []
    assert RancherPhase(cfg).standalone is True


def test_harvester_names_unchanged_without_downstream_clusters():
    cfg = {"vms": {"harvester1": {}, "rancher": {}, "eib": {}, "edge1": {}, "k3s": {}}}
    assert inventory.harvester_vm_names(cfg) == ["harvester1", "k3s"]


@pytest.mark.parametrize("profile", ["rancher", "rancher-test"])
def test_vars_file_needs_no_harvester_token_and_sizes_downstream_flavors(profile, tmp_path):
    cfg = _lab_cfg(profile, tmp_path)
    assert "harvester_token" not in cfg.get("credentials", {})
    data = yaml.safe_load(DeployRunner(cfg, tmp_path)._write_vars_file().read_text())
    assert data["libvirt_flavors"]["k3s-node"]["memory_mib"] == 2048
    assert data["libvirt_flavors"]["rke2-node"]["memory_mib"] == 4096
    flavors = {n["name"]: n["flavor"] for n in data["vm_nodes"]}
    assert flavors["k3s"] == "k3s-node" and flavors["rke2"] == "rke2-node"


def test_preflight_counts_downstream_nodes(tmp_path):
    cfg = _lab_cfg("rancher", tmp_path)
    need_mib, need_gb = preflight._resource_needs(cfg)
    assert need_mib == 8192 + 2048 + 4 * 4096
    assert need_gb == 60 + 20 + 4 * 30 + 20


def test_aws_catalog_has_both_profiles():
    assert catalog_for_profile("rancher")["recommended"].instance_type == "m8id.4xlarge"
    assert catalog_for_profile("rancher-test")["budget"].instance_type == "m7i.2xlarge"


# ---------- Ansible templates ----------

def _env() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(_TEMPLATES)), trim_blocks=True)


def test_downstream_vm_xml_attaches_its_own_cloud_init_seed(tmp_path):
    cfg = _lab_cfg("rancher", tmp_path)
    data = yaml.safe_load(DeployRunner(cfg, tmp_path)._write_vars_file().read_text())
    node = next(n for n in data["vm_nodes"] if n["name"] == "rke2-ha2")
    xml = _env().get_template("vm.xml.j2").render(
        item=node, serial_log_dir="/var/log", libvirt_network_name="default", **data
    )
    assert "<memory unit='MiB'>4096</memory>" in xml
    assert "rke2-ha2-cloud-init.iso" in xml
    assert "rancher-cloud-init.iso" not in xml
    assert "pflash" not in xml


def test_downstream_cloud_init_renders_valid_yaml():
    node = {"name": "k3s", "hostname": "k3s", "ip": "192.168.122.41", "mgmt_mac": "02:00:00:0D:6A:41"}
    env = _env()
    ctx = {
        "item": node, "ssh_public_key": "ssh-ed25519 KEY", "rancher_vm_password": "pw",
        "node_prefix": 24, "libvirt_network_gateway": "192.168.122.1",
    }
    user = yaml.safe_load(env.get_template("downstream-cloud-init-user-data.j2").render(**ctx))
    net = yaml.safe_load(env.get_template("downstream-cloud-init-network-config.j2").render(**ctx))
    meta = yaml.safe_load(env.get_template("downstream-cloud-init-meta-data.j2").render(**ctx))
    assert user["hostname"] == "k3s"
    assert user["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 KEY"]
    assert net["ethernets"]["mgmt"]["match"]["macaddress"] == "02:00:00:0d:6a:41"
    assert net["ethernets"]["mgmt"]["addresses"] == ["192.168.122.41/24"]
    assert meta["local-hostname"] == "k3s"


# ---------- the downstream phase ----------

class _Remote:
    """Stands in for the Rancher VM (kubectl) and the downstream nodes (SSH)."""

    def __init__(self, agent_active: set[str] | None = None, ready: bool = True):
        self.agent_active = agent_active or set()
        self.ready = ready
        self.rancher_scripts: list[str] = []
        self.node_scripts: list[tuple[str, str]] = []

    def rancher(self, script, timeout=120):
        self.rancher_scripts.append(script)
        if "insecureNodeCommand" in script:
            out = "curl --insecure -fL https://rancher/system-agent-install.sh | sudo sh -s - --token TOK"
        elif "status.ready" in script:
            names = script.split("for c in ", 1)[1].split(";", 1)[0].split()
            lines = []
            for n in names:
                total = len(FULL_CLUSTERS[n][1])
                if self.ready:
                    lines.append(f"{n}=true|{total}|{total}|True")
                else:
                    # What Rancher reports live while a 3-node cluster joins:
                    # status.ready true, but only the first node is Running.
                    lines.append(f"{n}=true|1|{total}|Unknown")
            out = "\n".join(lines)
        else:
            out = ""
        return subprocess.CompletedProcess([], 0, stdout=out, stderr="")

    def node(self, ip, script, timeout=120):
        self.node_scripts.append((ip, script))
        rc = 0
        if "is-active" in script:
            rc = 0 if ip in self.agent_active else 3
        return subprocess.CompletedProcess([], rc, stdout="", stderr="")


def _phase(cfg, remote: _Remote) -> RancherPhase:
    phase = RancherPhase(cfg)
    phase._ssh_script = remote.rancher
    phase._node_ssh_script = remote.node
    phase._sleep = lambda s: False
    return phase


def _drain(gen):
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def test_downstream_phase_creates_clusters_and_registers_every_node(tmp_path):
    cfg = _lab_cfg("rancher", tmp_path)
    remote = _Remote()
    phase = _phase(cfg, remote)
    assert _drain(phase.stream_downstream_clusters()) is True

    applied = "\n".join(s for s in remote.rancher_scripts if "kubectl apply" in s)
    assert '"kubernetesVersion": "v1.36.4+k3s1"' in applied
    assert applied.count('"kubernetesVersion": "v1.36.4+rke2r1"') == 2
    assert '"rkeConfig": {}' in applied

    registrations = [(ip, s) for ip, s in remote.node_scripts if "system-agent-install" in s]
    assert [ip for ip, _ in registrations] == [
        "192.168.122.41", "192.168.122.42", "192.168.122.51", "192.168.122.52", "192.168.122.53",
    ]
    for _, script in registrations:
        assert "--etcd --controlplane --worker" in script
    assert "--node-name rke2-ha3" in registrations[-1][1]


def test_downstream_phase_skips_nodes_already_registered(tmp_path):
    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote(agent_active={"192.168.122.41"})
    assert _drain(_phase(cfg, remote).stream_downstream_clusters()) is True
    registered = [ip for ip, s in remote.node_scripts if "system-agent-install" in s]
    assert registered == ["192.168.122.42"]


def test_downstream_phase_waits_for_every_node_not_just_status_ready(tmp_path):
    cfg = _lab_cfg("rancher", tmp_path)
    phase = _phase(cfg, _Remote(ready=False))
    phase.DOWNSTREAM_TIMEOUT = 0
    assert _drain(phase.stream_downstream_clusters()) is False
    assert "rke2-ha (1/3 nodes)" in phase.error


def test_downstream_phase_fails_when_clusters_never_get_ready(tmp_path):
    cfg = _lab_cfg("rancher-test", tmp_path)
    phase = _phase(cfg, _Remote(ready=False))
    phase.DOWNSTREAM_TIMEOUT = 0
    assert _drain(phase.stream_downstream_clusters()) is False
    assert "not Ready" in phase.error


def test_downstream_phase_rejects_unknown_node_and_distro(tmp_path):
    cfg = _lab_cfg("rancher-test", tmp_path)
    cfg["downstream_clusters"] = [{"name": "x", "distro": "k3s", "nodes": ["ghost"]}]
    phase = _phase(cfg, _Remote())
    assert _drain(phase.stream_downstream_clusters()) is False
    assert "ghost" in phase.error

    cfg["downstream_clusters"] = [{"name": "x", "distro": "rke1", "nodes": ["k3s"]}]
    phase = _phase(cfg, _Remote())
    assert _drain(phase.stream_downstream_clusters()) is False
    assert "distro" in phase.error


def test_downstream_phase_is_a_no_op_without_clusters():
    cfg = get_profile("suse-edge").default_cfg()
    cfg["network"] = {}
    assert _drain(RancherPhase(cfg).stream_downstream_clusters()) is True


def test_success_screen_lists_the_clusters():
    profile = get_profile("rancher")
    lines = profile.success_next_steps(profile.default_cfg())
    assert any("k3s-single, rke2-single, rke2-ha" in line for line in lines)
    assert any("rodeo ssh rancher" in line for line in lines)


def test_registration_query_resolves_token_placeholder_and_waits_for_capi(tmp_path):
    """Rancher 2.15 puts a literal {token} in the CRT commands (token lives in
    status.tokenSecretName) and starts CAPI after /ping; both found live."""
    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    assert _drain(_phase(cfg, remote).stream_downstream_clusters()) is True
    query = next(s for s in remote.rancher_scripts if "insecureNodeCommand" in s)
    assert "clusters.cluster.x-k8s.io" in query
    assert "tokenSecretName" in query
    assert "${cmd//\\{token\\}/$tok}" in query
