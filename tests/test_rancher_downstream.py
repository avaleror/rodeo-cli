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
    # Nodes register concurrently, so order is not fixed.
    assert sorted(ip for ip, _ in registrations) == [
        "192.168.122.41", "192.168.122.42", "192.168.122.51", "192.168.122.52", "192.168.122.53",
    ]
    for _, script in registrations:
        assert "--etcd --controlplane --worker" in script
    assert any("--node-name rke2-ha3" in s for _, s in registrations)


def test_a_ready_cluster_registers_without_waiting_for_a_slower_one(tmp_path):
    """Live 2026-11-12: RKE2's command was ready ~35 s before K3s's and RKE2 is
    the slowest path; waiting for every command first cost that much."""
    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    rancher, node = remote.rancher, remote.node
    events: list[str] = []
    k3s_polls = {"n": 0}

    def _rancher(script, timeout=120):
        if "insecureNodeCommand" in script and "k3s-single" in script:
            k3s_polls["n"] += 1
            if k3s_polls["n"] < 3:  # K3s's command is not ready for two polls
                events.append("k3s not ready")
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        return rancher(script, timeout)

    import threading

    rke2_registered = threading.Event()

    def _node(ip, script, timeout=120):
        if "system-agent-install" in script:
            events.append(f"register {ip}")
            if ip == "192.168.122.42":
                rke2_registered.set()
        return node(ip, script, timeout)

    phase = _phase(cfg, remote)
    phase._ssh_script, phase._node_ssh_script = _rancher, _node
    # Each poll's sleep gives the RKE2 worker a chance to run (deterministic,
    # not timing-dependent); wait-for-all code never registers it here.
    phase._sleep = lambda s: (rke2_registered.wait(2), False)[1]
    assert _drain(phase.stream_downstream_clusters()) is True
    assert events.index("register 192.168.122.42") < len(events) - 1 - events[::-1].index("k3s not ready")


def test_downstream_phase_applies_every_cluster_before_any_node_registers(tmp_path):
    """CAPI's start-up wait is paid once for all clusters, not once per cluster."""
    cfg = _lab_cfg("rancher-test", tmp_path)
    events: list[str] = []
    remote = _Remote()
    rancher, node = remote.rancher, remote.node

    def _rancher(script, timeout=120):
        if "kubectl apply" in script:
            events.append("apply")
        return rancher(script, timeout)

    def _node(ip, script, timeout=120):
        if "system-agent-install" in script:
            events.append("register")
        return node(ip, script, timeout)

    phase = _phase(cfg, remote)
    phase._ssh_script, phase._node_ssh_script = _rancher, _node
    assert _drain(phase.stream_downstream_clusters()) is True
    assert events == ["apply", "apply", "register", "register"]


def test_downstream_phase_registers_k3s_and_rke2_nodes_at_the_same_time(tmp_path):
    """Both registrations must be in flight together: sequential code would
    leave the barrier waiting for a second party and time out."""
    import threading

    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    node = remote.node
    barrier = threading.Barrier(2, timeout=5)

    def _node(ip, script, timeout=120):
        if "system-agent-install" in script:
            barrier.wait()
        return node(ip, script, timeout)

    phase = _phase(cfg, remote)
    phase._node_ssh_script = _node
    assert _drain(phase.stream_downstream_clusters()) is True


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


# ---------- image pre-load ----------

def test_preload_copies_each_distros_tarball_before_any_node_registers(tmp_path):
    """Measured live 2026-11-12: rancher-agent (678 MB) plus the distro images
    were pulled on the node's critical path. The pre-load runs during the
    registration-command wait and must land before K3s/RKE2 start."""
    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    rancher, node = remote.rancher, remote.node
    events: list[str] = []

    def _rancher(script, timeout=120):
        if "rodeo-preload" in script:
            distro = "k3s" if "k3s-images.txt" in script else "rke2"
            return subprocess.CompletedProcess([], 0, stdout=f"/var/tmp/rodeo-preload-{distro}.tar\n", stderr="")
        return rancher(script, timeout)

    def _node(ip, script, timeout=120):
        if "system-agent-install" in script:
            events.append(f"register {ip}")
        if "test -d /var/lib/rancher" in script:  # fresh node: distro not installed yet
            return subprocess.CompletedProcess([], 1, stdout="", stderr="")
        return node(ip, script, timeout)

    def _copy(tarball, distro, ip):
        events.append(f"copy {distro} {ip}")
        assert tarball == f"/var/tmp/rodeo-preload-{distro}.tar"
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    phase = _phase(cfg, remote)
    phase._ssh_script, phase._node_ssh_script, phase._copy_preload = _rancher, _node, _copy
    assert _drain(phase.stream_downstream_clusters()) is True
    copies = [e for e in events if e.startswith("copy")]
    assert sorted(copies) == ["copy k3s 192.168.122.41", "copy rke2 192.168.122.42"]
    # Each node's own tarball lands before that node registers.
    for ip in ("192.168.122.41", "192.168.122.42"):
        copy = next(i for i, e in enumerate(events) if e.startswith("copy") and e.endswith(ip))
        assert copy < events.index(f"register {ip}")


def test_preload_failure_never_fails_the_deploy(tmp_path):
    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    rancher = remote.rancher

    def _rancher(script, timeout=120):
        if "rodeo-preload" in script:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="curl: (6) Could not resolve host")
        return rancher(script, timeout)

    phase = _phase(cfg, remote)
    phase._ssh_script = _rancher
    phase._copy_preload = lambda *a: pytest.fail("nothing to copy after a failed pre-load")
    gen = phase.stream_downstream_clusters()
    logs = []
    try:
        while True:
            ev = next(gen)
            logs.append(getattr(ev, "line", "") or getattr(ev, "text", "") or str(ev))
    except StopIteration as stop:
        assert stop.value is True
    assert any("pre-load skipped" in line for line in logs)
    assert len([s for _, s in remote.node_scripts if "system-agent-install" in s]) == 2


def test_preload_script_exports_what_the_node_will_ask_for(tmp_path):
    """Run the real script against stub kubectl/curl/k3s: names must carry
    Rancher's system-default-registry (a tarball under docker.io names is
    imported and then ignored), include rancher-agent at Rancher's version,
    and skip Calico add-ons a single node never runs."""
    import shutil

    if not shutil.which("bash"):
        pytest.skip("bash not available")
    cfg = _lab_cfg("rancher-test", tmp_path)
    phase = RancherPhase(cfg)
    script = phase._preload_script("rke2", "v1.36.4+rke2r1")
    assert "v1.36.4%2Brke2r1/rke2-images-core.linux-amd64.txt" in script

    bin_dir, out_dir = tmp_path / "bin", tmp_path / "out"
    bin_dir.mkdir()
    out_dir.mkdir()
    (bin_dir / "kubectl").write_text("#!/bin/sh\necho registry.rancher.com\n")
    (bin_dir / "curl").write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        "  *core*) printf 'docker.io/rancher/rke2-runtime:v1.36.4-rke2r1\\ndocker.io/rancher/mirrored-pause:3.10.2\\n' ;;\n"
        "  *calico*) printf 'docker.io/rancher/mirrored-calico-node:v3.32.1\\ndocker.io/rancher/mirrored-calico-whisker:v3.32.1\\n' ;;\n"
        "esac\n"
    )
    (bin_dir / "k3s").write_text(
        f"#!/bin/sh\necho \"$*\" >> {out_dir}/k3s.log\n"
        "case \"$*\" in *export*) for a; do case $a in *.part) : > \"$a\";; esac; done;; esac\n"
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    script = script.replace("/var/tmp/", f"{out_dir}/")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    export = next(ln for ln in (out_dir / "k3s.log").read_text().splitlines() if " export " in ln)
    images = sorted(a for a in export.split() if a.startswith("registry.") or a.startswith("docker."))
    assert images == [
        "registry.rancher.com/rancher/mirrored-calico-node:v3.32.1",
        "registry.rancher.com/rancher/mirrored-pause:3.10.2",
        f"registry.rancher.com/rancher/rancher-agent:v{phase.rancher_version}",
        "registry.rancher.com/rancher/rke2-runtime:v1.36.4-rke2r1",
    ]
    assert r.stdout.strip().endswith("rodeo-preload-rke2-v1.36.4+rke2r1.tar")


def test_downstream_nodes_get_iptables_and_register_after_cloud_init(tmp_path):
    """Live 2026-11-12: rke2-traefik stuck in ContainerCreating, portmap CNI
    'exec: iptables: executable file not found' on the Leap 16 cloud image."""
    tpl = Path(rodeo.__file__).parent / "data/ansible/roles/vms/templates/downstream-cloud-init-user-data.j2"
    assert "install --no-recommends iptables" in tpl.read_text()

    cfg = _lab_cfg("rancher-test", tmp_path)
    remote = _Remote()
    assert _drain(_phase(cfg, remote).stream_downstream_clusters()) is True
    for _, script in (x for x in remote.node_scripts if "system-agent-install" in x[1]):
        assert script.index("cloud-init status --wait") < script.index("system-agent-install")
