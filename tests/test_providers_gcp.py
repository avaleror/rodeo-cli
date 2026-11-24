"""GCP host provider: catalog, instance body, provision/deprovision over a fake API."""
from __future__ import annotations

import pytest

from rodeo.config import ConfigError
from rodeo.providers import gcp
from rodeo.providers.base import ProvisionSpec
from rodeo.providers.gcp import GcpHostProvider
from rodeo.providers.instance_catalog import (
    AWS_PROFILE_TIERS,
    GCP_PROFILE_TIERS,
    resolve_instance_type,
)


class FakeApi:
    """In-memory stand-in for GceApi."""

    project = "proj"

    def __init__(self, *, zones=("europe-west4-a", "europe-west4-b"), offered=None,
                 full_zones=()):
        self.zones = list(zones)
        self.offered = offered  # None = every zone offers every type
        self.full_zones = set(full_zones)
        self.instances: dict[str, dict] = {}
        self.firewalls: dict[str, dict] = {}
        self.networks: list[tuple] = []
        self.inserts: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def zones_in_region(self, region):
        return [z for z in self.zones if z.startswith(region)]

    def offers(self, zone, machine_type):
        return self.offered is None or zone in self.offered

    def image_from_family(self, project, family):
        return f"projects/{project}/global/images/family/{family}"

    def _view(self, inst):
        keys = ("name", "zone", "status", "id", "labels", "public_ip", "metadata", "tags", "created")
        return {k: inst.get(k) for k in keys}

    def find_instances(self, label_filter):
        return [self._view(i) for i in self.instances.values()]

    def get_instance(self, zone, name):
        inst = self.instances.get(name)
        return self._view(inst) if inst and inst["zone"] == zone else None

    def insert_instance(self, zone, body):
        self.inserts.append((zone, body))
        if zone in self.full_zones:
            raise RuntimeError("ZONE_RESOURCE_POOL_EXHAUSTED: no capacity")
        meta = {i["key"]: i["value"] for i in body["metadata"]["items"]}
        self.instances[body["name"]] = {
            "name": body["name"], "zone": zone, "status": "RUNNING", "id": "1",
            "labels": dict(body["labels"]), "public_ip": "203.0.113.7", "metadata": meta,
            "tags": list(body["tags"]["items"]), "created": "2026-11-24T10:00:00+00:00",
        }

    def set_labels(self, zone, name, labels):
        self.instances[name]["labels"] = {**self.instances[name]["labels"], **labels}

    def start_instance(self, zone, name):
        self.instances[name]["status"] = "RUNNING"

    def delete_instance(self, zone, name):
        self.deleted.append(name)
        del self.instances[name]

    def ensure_network(self, network, region, subnet, cidr):
        self.networks.append((network, region, subnet, cidr))

    def get_firewall(self, name):
        return self.firewalls.get(name)

    def upsert_firewall(self, name, network, ports, source_ranges, tag, exists,
                        description="rodeo-cli lab access (managed by rodeo)"):
        self.firewalls[name] = {"name": name, "source_ranges": sorted(source_ranges),
                                "ports": ports, "network": network, "tag": tag,
                                "description": description}

    def list_firewalls(self):
        return [{"name": f["name"], "description": f.get("description", "")}
                for f in self.firewalls.values()]

    def delete_firewall(self, name):
        del self.firewalls[name]


def _provider(api):
    return GcpHostProvider(api=api, sleep=lambda _s: None, caller_ip_fn=lambda: "198.51.100.9")


def _spec(**kw):
    return ProvisionSpec(workshop=kw.pop("workshop", "My_Lab"), host_ids=["primary"],
                         ssh_user="rodeo", wait_ssh=False, **kw)


def _fw(api, lab="rodeo-my-lab-"):
    """The one firewall rule of a lab (named rodeo-<lab>-<deployment> since issue #175)."""
    names = [n for n in api.firewalls if n.startswith(lab)]
    assert len(names) == 1, names
    return api.firewalls[names[0]]


def _inst_names(api, lab="rodeo-my-lab-"):
    return [n for n in api.instances if n.startswith(lab)]


@pytest.fixture
def ssh_dir(tmp_path, monkeypatch):
    d = tmp_path / "ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: d)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: d)
    return d


# ---------- names, catalog, machine types ----------

def test_names_are_valid_gce_labels_and_resources():
    assert gcp.label_value("My_Lab.2026") == "my_lab-2026"
    assert gcp.resource_name("9 Lab") == "r-9-lab"
    assert len(gcp.instance_name("x" * 80, "primary")) <= 63


def test_every_profile_has_the_same_tiers_on_gcp_as_on_aws():
    assert set(GCP_PROFILE_TIERS) == set(AWS_PROFILE_TIERS)
    for profile, tiers in AWS_PROFILE_TIERS.items():
        assert set(GCP_PROFILE_TIERS[profile]) == set(tiers), profile


def test_every_gcp_pick_can_run_nested_kvm():
    for tiers in GCP_PROFILE_TIERS.values():
        for offer in tiers.values():
            GcpHostProvider.check_machine_type(offer.instance_type)


def test_catalog_is_per_provider():
    assert resolve_instance_type(profile="rancher-test", provider="gcp")[0] == "c4-standard-16-lssd"
    assert resolve_instance_type(profile="rancher-test")[0] == "m8id.4xlarge"


@pytest.mark.parametrize("itype", ["c4d-standard-32", "n2d-standard-32", "c4a-standard-32",
                                   "e2-standard-16"])
def test_amd_arm_and_e2_are_refused(itype):
    with pytest.raises(ConfigError, match="cannot run nested KVM"):
        GcpHostProvider.check_machine_type(itype)


def test_small_shapes_are_refused():
    with pytest.raises(ConfigError, match="too small"):
        GcpHostProvider.check_machine_type("n4-standard-4")


def test_validate_needs_type_gcp_and_a_size():
    p = _provider(FakeApi())
    with pytest.raises(ConfigError, match="type: gcp"):
        p.validate({"type": "aws", "instance_tier": "recommended"})
    with pytest.raises(ConfigError, match="instance_type or provider.instance_tier"):
        p.validate({"type": "gcp"})
    with pytest.raises(ConfigError, match="not in provider.region"):
        p.validate({"type": "gcp", "instance_tier": "budget", "region": "europe-west4",
                    "zone": "us-central1-a"})


# ---------- instance body ----------

def _body(**cfg):
    base = {"instance_type": "c4-standard-16-lssd"}
    base.update(cfg)
    return _provider(FakeApi()).instance_body(
        base, project="proj", workshop="My_Lab", host_id="primary", zone="europe-west4-b",
        image="img", ssh_user="rodeo", public_key="ssh-ed25519 AAAA lab\n",
        extra_labels={"scope": "single-host"},
    )


def test_body_enables_nested_virt_and_the_deadman_delete():
    body = _body()
    assert body["advanced_machine_features"] == {"enable_nested_virtualization": True}
    assert body["scheduling"]["instance_termination_action"] == "DELETE"
    assert 6 * 3600 - 60 <= body["scheduling"]["max_run_duration"]["seconds"] <= 6 * 3600
    assert body["labels"]["rodeo-expires-at"].isdigit()


def test_ttl_off_means_no_max_run_duration():
    from rodeo.providers.deadman import ALLOW_OFF_KEY

    body = _body(ttl_hours="off", **{ALLOW_OFF_KEY: True})
    assert "max_run_duration" not in body["scheduling"]
    assert body["labels"]["rodeo-expires-at"] == "never"


def test_ownership_labels_cannot_be_overridden():
    with pytest.raises(ConfigError, match="reserved"):
        _body(labels={"managed-by": "someone", "rodeo-workshop": "other"})
    body = _provider(FakeApi()).instance_body(
        {"instance_type": "c4-standard-16-lssd"}, project="proj", workshop="My_Lab",
        host_id="primary", zone="europe-west4-b", image="img", ssh_user="rodeo",
        public_key="ssh-ed25519 AAAA lab\n",
        extra_labels={"managed-by": "someone", "rodeo-workshop": "other", "scope": "single-host"},
    )
    assert body["labels"]["managed-by"] == "rodeo"
    assert body["labels"]["rodeo-workshop"] == "my_lab"
    assert body["labels"]["scope"] == "single-host"


def test_body_carries_key_tag_network_and_boot_script():
    body = _body()
    meta = {i["key"]: i["value"] for i in body["metadata"]["items"]}
    assert meta["ssh-keys"] == "rodeo:ssh-ed25519 AAAA lab"
    assert meta["rodeo-workshop"] == "My_Lab"
    assert "zypper" in meta["startup-script"]
    assert body["tags"]["items"] == ["rodeo-my-lab"]
    nic = body["network_interfaces"][0]
    assert nic["subnetwork"] == "projects/proj/regions/europe-west4/subnetworks/rodeo-europe-west4"
    assert nic["access_configs"][0]["type_"] == "ONE_TO_ONE_NAT"


def test_boot_disk_follows_series_and_local_ssd():
    lssd = _body()["disks"][0]["initialize_params"]
    assert lssd["disk_type"].endswith("hyperdisk-balanced") and lssd["disk_size_gb"] == 100
    n2 = _body(instance_type="n2-standard-32", boot_disk_gb=700)["disks"][0]["initialize_params"]
    assert n2["disk_type"].endswith("pd-balanced") and n2["disk_size_gb"] == 700


def test_body_is_a_valid_compute_instance():
    compute_v1 = pytest.importorskip("google.cloud.compute_v1")
    inst = compute_v1.Instance(_body())
    assert inst.advanced_machine_features.enable_nested_virtualization is True
    assert inst.network_interfaces[0].access_configs[0].type_ == "ONE_TO_ONE_NAT"


# ---------- provision / deprovision ----------

def test_provision_creates_network_firewall_and_instance(ssh_dir):
    api = FakeApi()
    hosts = _provider(api).provision(_spec(), {"type": "gcp", "instance_tier": "recommended",
                                               "region": "europe-west4", "lab_profile": "rancher-test"})
    assert hosts[0].public_ip == "203.0.113.7"
    assert hosts[0].labels["provision_action"] == "create"
    assert api.networks == [("rodeo-europe-west4", "europe-west4", "rodeo-europe-west4", "10.90.0.0/20")]
    fw = _fw(api)
    assert fw["source_ranges"] == ["198.51.100.9/32"] and fw["ports"] == (22, 8443, 30002)
    zone, body = api.inserts[0]
    assert zone == "europe-west4-a" and body["machine_type"].endswith("c4-standard-16-lssd")


def test_provision_moves_to_the_next_zone_without_capacity(ssh_dir):
    api = FakeApi(full_zones={"europe-west4-a"})
    _provider(api).provision(_spec(), {"type": "gcp", "instance_type": "c4-standard-8-lssd",
                                       "region": "europe-west4"})
    assert [z for z, _ in api.inserts] == ["europe-west4-a", "europe-west4-b"]


def test_provision_skips_zones_that_do_not_offer_the_type(ssh_dir):
    api = FakeApi(offered={"europe-west4-b"})
    _provider(api).provision(_spec(), {"type": "gcp", "instance_type": "c4-standard-8-lssd",
                                       "region": "europe-west4"})
    assert [z for z, _ in api.inserts] == ["europe-west4-b"]


def test_provision_reuses_a_live_instance_and_keeps_its_key(ssh_dir):
    api = FakeApi()
    p = _provider(api)
    cfg = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}
    p.provision(_spec(), cfg)
    again = p.provision(_spec(), cfg)
    assert again[0].labels["provision_action"] == "reuse"
    assert len(api.inserts) == 1


def test_a_lab_whose_key_is_elsewhere_is_left_alone(ssh_dir):
    api = FakeApi()
    cfg = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}
    _provider(api).provision(_spec(), cfg)
    import shutil

    first = _inst_names(api)
    shutil.rmtree(ssh_dir)
    # Same lab name from another machine (issue #175): a second deployment, side by side.
    _provider(api).provision(_spec(), cfg)
    assert len(_inst_names(api)) == 2 and set(first) <= set(_inst_names(api))
    assert len([n for n in api.firewalls if n.startswith("rodeo-my-lab-")]) == 2


def test_byo_network_is_not_created(ssh_dir):
    api = FakeApi()
    _provider(api).provision(_spec(), {"type": "gcp", "instance_type": "c4-standard-8-lssd",
                                       "region": "europe-west4", "network": "corp",
                                       "subnetwork": "corp-sub"})
    assert api.networks == []
    assert _fw(api)["network"] == "corp"


def test_firewall_follows_the_callers_ip(ssh_dir):
    api = FakeApi()
    cfg = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}
    _provider(api).provision(_spec(), cfg)
    moved = GcpHostProvider(api=api, sleep=lambda _s: None, caller_ip_fn=lambda: "192.0.2.1")
    moved.provision(_spec(), cfg)
    assert _fw(api)["source_ranges"] == ["192.0.2.1/32"]


def test_deprovision_deletes_only_this_workshop_then_the_firewall(ssh_dir):
    api = FakeApi()
    p = _provider(api)
    cfg = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}
    p.provision(_spec(), cfg)
    p.provision(_spec(workshop="other"), cfg)
    mine = _inst_names(api)
    results = p.deprovision(_spec(), cfg)
    assert api.deleted == mine and len(mine) == 1
    assert not [n for n in api.firewalls if n.startswith("rodeo-my-lab-")]
    assert [n for n in api.firewalls if n.startswith("rodeo-other-")]
    assert {r.id for r in results} == {"primary", "firewall"}
    assert all(r.ok for r in results)


def test_deprovision_ignores_a_workshop_whose_name_sanitises_the_same(ssh_dir):
    api = FakeApi()
    p = _provider(api)
    cfg = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}
    p.provision(_spec(workshop="my-lab"), cfg)
    name = _inst_names(api)[0]
    api.instances[name]["metadata"]["rodeo-workshop"] = "my-lab"
    # My_Lab has no key here and nothing recorded: refused, nothing deleted.
    with pytest.raises(ConfigError, match="cloud ls"):
        p.deprovision(_spec(workshop="My_Lab"), cfg)
    p.deprovision(_spec(workshop="My_Lab", known_instance_ids=[name]), cfg)
    assert api.deleted == []


# ---------- host side ----------

def test_gcp_target_shapes_the_host_like_aws():
    from rodeo.host_context import apply_host_context

    cfg, notes = apply_host_context({"deployment_target": "gcp", "resources": {}}, host_facts={})
    assert cfg["storage"]["backend"] == "nvme"
    assert any("gcp/nvme" in n for n in notes)


def test_preflight_counts_every_gce_local_ssd(tmp_path):
    from rodeo.preflight import _pending_nvme_pool_gib

    sys_block, by_id = tmp_path / "block", tmp_path / "by-id"
    by_id.mkdir()
    for i, name in enumerate(["nvme0n1", "nvme1n1", "nvme2n1"]):
        (sys_block / name).mkdir(parents=True)
        (sys_block / name / "size").write_text(str(375 * 1024**3 // 512))
        if name != "nvme0n1":
            (by_id / f"google-local-nvme-ssd-{i}").symlink_to(tmp_path / name)
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/nvme0n1p3 / btrfs rw 0 0\n")
    got = _pending_nvme_pool_gib({"backend": "nvme"}, sys_block=sys_block, proc_mounts=mounts,
                                 by_id=by_id)
    assert got == 750


def test_cloud_targets_run_the_matching_target_on_the_host():
    from rodeo.providers.remote_up import _cloud_target, default_ssh_user

    assert _cloud_target({"deployment_target": "gcp"}) == "gcp"
    assert _cloud_target({"deployment_target": "aws"}) == "aws"
    assert default_ssh_user("gcp") == "rodeo" and default_ssh_user("aws") == "ec2-user"


def test_provision_waits_for_the_image_repositories(ssh_dir, monkeypatch):
    """SLES PAYG registers its repos after sshd answers; the installer needs them."""
    calls = []

    class Result:
        def __init__(self, ok):
            self.ok = ok

    def fake_run(inv, fh, cmd, **kw):
        calls.append(cmd)
        return Result(len(calls) >= 3)

    monkeypatch.setattr(gcp, "run_remote", fake_run)
    p = _provider(FakeApi())
    monkeypatch.setattr(p, "_wait_ssh", lambda *a, **k: None)
    monkeypatch.setattr(gcp, "plant_rodeo_ssh_key", lambda *a, **k: None)
    spec = ProvisionSpec(workshop="lab", host_ids=["primary"], ssh_user="rodeo", wait_ssh=True)
    p.provision(spec, {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"})
    assert len(calls) == 3 and "zypper" in calls[0][-1]


def test_body_attaches_a_service_account_for_payg_registration():
    """Without one the identity token 404s and SLES PAYG cannot register its repos."""
    sa = _body()["service_accounts"]
    assert sa == [{"email": "default", "scopes": ["https://www.googleapis.com/auth/userinfo.email"]}]
