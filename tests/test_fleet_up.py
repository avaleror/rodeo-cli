"""`rodeo fleet up`: a whole workshop in one command (rodeo/commands/fleet_up_cmd.py)."""
from __future__ import annotations


import pytest
import yaml
from click.testing import CliRunner

from rodeo.commands import fleet_cmd, fleet_portal_cmd
from rodeo.commands import fleet_up_cmd as up
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.status import HostStatusResult


def _subnet(provider, *, instance_type=""):
    assert provider["region"] and instance_type
    return "subnet-default"


def test_build_inventory_is_a_valid_workshop(tmp_path):
    data = up.build_inventory(profile="virt-workshop-aws", count=3, default_subnet=_subnet,
                              guide_url="https://example.test/guide/")
    path = tmp_path / "workshop.yaml"
    path.write_text(yaml.safe_dump(data))
    inv = load_inventory(path)
    assert inv.name.startswith("virt-workshop-aws-") and inv.lab_profile == "virt-workshop-aws"
    assert inv.lab_dir == up.LAB_DIR and inv.deploy_concurrency == 3
    assert data["provider"] == {"type": "aws", "count": 3, "region": "eu-north-1", "student_access": "open",
                                "ttl_hours": 6, "instance_tier": "recommended", "subnet_id": "subnet-default"}
    assert inv.portal.enabled and inv.portal.title == "virt-workshop-aws workshop"
    assert data["portal"]["guide_url"] == "https://example.test/guide/"


def test_build_inventory_keeps_what_is_given(monkeypatch):
    monkeypatch.setenv("RODEO_AWS_REGION", "eu-central-1")
    data = up.build_inventory(profile="harvester", count=20, name="emea", instance_type="m8id.8xlarge",
                              subnet_id="subnet-1", ttl_hours=10, portal=False,
                              default_subnet=lambda *a, **k: pytest.fail("subnet was given"))
    assert data["name"] == "emea" and "portal" not in data
    assert data["lab"]["concurrency"] == 8
    assert (data["provider"]["region"], data["provider"]["instance_type"], data["provider"]["ttl_hours"]) == \
        ("eu-central-1", "m8id.8xlarge", 10)
    assert "instance_tier" not in data["provider"]


def _ok(hid, done=True):
    return HostStatusResult(hid, True, None, {"phases": {"kvm_host": {"completed": done}}})


def _inventory_file(tmp_path, portal=True):
    data = up.build_inventory(profile="harvester", count=2, name="demo", subnet_id="subnet-1", portal=portal)
    data["hosts"] = [{"id": "s1", "ssh": "198.51.100.1"}, {"id": "s2", "ssh": "198.51.100.2"}]
    path = tmp_path / "workshop.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_wait_for_labs_polls_until_every_lab_is_complete(tmp_path, monkeypatch):
    path = _inventory_file(tmp_path)
    rounds = iter([[_ok("s1"), _ok("s2", done=False)], [_ok("s1"), _ok("s2")]])
    monkeypatch.setattr("rodeo.fleet.status.fleet_status", lambda *a, **k: next(rounds))
    slept, lines = [], []
    outcome = up.wait_for_labs(load_inventory(path), path, minutes=5, echo=lines.append, sleep=slept.append)
    assert outcome == ("ok", []) and slept == [up.POLL_SECONDS]
    assert "ready 1/2" in lines[0] and "ready 2/2" in lines[1]


def test_wait_for_labs_gives_up_after_the_deadline(tmp_path, monkeypatch):
    path = _inventory_file(tmp_path)
    monkeypatch.setattr("rodeo.fleet.status.fleet_status", lambda *a, **k: [_ok("s1"), _ok("s2", done=False)])
    ticks = iter([0, 0, 400])
    outcome = up.wait_for_labs(load_inventory(path), path, minutes=5, echo=lambda _: None,
                               sleep=lambda _: None, clock=lambda: next(ticks))
    assert outcome == ("timeout", ["s2"])


@pytest.fixture()
def steps(monkeypatch):
    """Every invoked fleet command records its name instead of running."""
    seen: list[str] = []
    for module, names in ((fleet_cmd, ("fleet_provision_cmd", "fleet_deploy_cmd", "fleet_open_access_cmd",
                                       "fleet_access_cmd")),
                          (fleet_portal_cmd, ("portal_up_cmd", "portal_publish_cmd", "portal_info_cmd"))):
        for name in names:
            monkeypatch.setattr(module, name, lambda _n=name, **kw: seen.append(_n))
    monkeypatch.setattr(up, "wait_for_labs", lambda *a, **k: seen.append("wait") or ("ok", []))
    return seen


def test_up_runs_the_instructor_flow_in_order(tmp_path, steps):
    path = _inventory_file(tmp_path)
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(path)])
    assert r.exit_code == 0, r.output
    assert steps == ["fleet_provision_cmd", "fleet_deploy_cmd", "portal_up_cmd", "wait",
                     "fleet_open_access_cmd", "portal_publish_cmd", "portal_info_cmd"]
    assert "fleet deprovision -f" in r.output


def test_up_without_portal_prints_the_access_sheet(tmp_path, steps):
    path = _inventory_file(tmp_path, portal=False)
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(path)])
    assert r.exit_code == 0, r.output
    assert steps == ["fleet_provision_cmd", "fleet_deploy_cmd", "wait", "fleet_open_access_cmd",
                     "fleet_access_cmd"]


def test_up_writes_the_inventory_from_flags(tmp_path, steps, monkeypatch):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider.default_subnet", lambda self, p, **k: "subnet-d")
    path = tmp_path / "virt" / "workshop.yaml"
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(path), "--profile", "virt-workshop-aws",
                                                 "--count", "3", "--name", "virt-3"])
    assert r.exit_code == 0, r.output
    inv = load_inventory(path)
    assert (inv.name, inv.provider["count"], inv.provider["subnet_id"]) == ("virt-3", 3, "subnet-d")
    assert steps[0] == "fleet_provision_cmd"


def test_up_stops_and_says_how_to_resume_when_a_lab_fails(tmp_path, steps, monkeypatch):
    monkeypatch.setattr(up, "wait_for_labs", lambda *a, **k: ("failed", ["s2"]))
    path = _inventory_file(tmp_path)
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(path)])
    assert r.exit_code == 1
    assert "s2: failed" in r.output and "--failed-only" in r.output
    assert "fleet_open_access_cmd" not in steps and "portal_publish_cmd" not in steps


def test_up_refuses_flags_for_an_existing_inventory(tmp_path, steps):
    path = _inventory_file(tmp_path)
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(path), "--count", "5"])
    assert r.exit_code == 2 and "already exists" in r.output and steps == []


def test_up_needs_profile_and_count_for_a_new_inventory(tmp_path, steps):
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(tmp_path / "nope.yaml"),
                                                 "--profile", "virt-workshop-aws"])
    assert r.exit_code == 2 and "--profile and --count" in r.output and steps == []
