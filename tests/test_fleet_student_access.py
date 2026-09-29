"""provider.student_access + `rodeo fleet open-access` (claim portal F5.0)."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from rodeo.config import ConfigError
from rodeo.fleet import student_access as sa
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.status import HostStatusResult
from rodeo.providers.aws import MANAGED_SG_PORTS, AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from rodeo.secretgen import is_strong_password, random_password
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_aws_managed_sg import managed_ssh  # noqa: F401 (fixture)
from tests.test_claim_portal_baseline import _all_cidrs, _CountingEC2

OPERATOR = "203.0.113.9/32"
WORLD = "0.0.0.0/0"


def _inventory(tmp_path: Path, provider_extra: str = "", lab_extra: str = "") -> Path:
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            name: demo
            lab:
              dir: /root/lab
              profile: harvester
            {lab_extra}
            provider:
              type: aws
              region: eu-north-1
              instance_type: m8id.8xlarge
              subnet_id: subnet-1
            {provider_extra}
            hosts:
              - id: s1
                ssh: 198.51.100.1
              - id: s2
                ssh: 198.51.100.2
            """
        )
    )
    return path


# -- inventory ------------------------------------------------------------------


def test_student_access_defaults_to_operator(tmp_path):
    inv = load_inventory(_inventory(tmp_path))
    assert inv.student_access == "operator"
    assert "student_access" not in inv.provider  # nothing injected into provider cfg


@pytest.mark.parametrize("raw,expected", [("open", "open"), ("OPEN", "open"), ("operator", "operator")])
def test_student_access_accepts_known_values(tmp_path, raw, expected):
    inv = load_inventory(_inventory(tmp_path, f"  student_access: {raw}"))
    assert inv.student_access == expected


@pytest.mark.parametrize("raw", ["public", "yes", "''", "null"])
def test_student_access_unknown_value_fails_closed(tmp_path, raw):
    with pytest.raises(ConfigError, match="student_access"):
        load_inventory(_inventory(tmp_path, f"  student_access: {raw}"))


# -- password strength ----------------------------------------------------------


@pytest.mark.parametrize(
    "pw,strong",
    [
        ("Abcdefgh12345678", True),
        ("Abcdefgh1234567", False),  # 15 chars
        ("abcdefgh12345678", False),  # no upper
        ("ABCDEFGH12345678", False),  # no lower
        ("Abcdefghijklmnop", False),  # no digit
        ("", False),
    ],
)
def test_is_strong_password(pw, strong):
    assert is_strong_password(pw) is strong


def test_generated_passwords_are_always_strong():
    assert all(is_strong_password(random_password()) for _ in range(200))


def _run_check_script(home: Path, keys: list[str]) -> str:
    return subprocess.run(
        [sys.executable, "-c", sa.PASSWORD_CHECK_SCRIPT, *keys],
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    ).stdout


@pytest.mark.parametrize(
    "pw", ["Abcdefgh12345678", "Abcdefgh1234567", "abcdefgh12345678", "Zz9" * 6]
)
def test_remote_check_script_agrees_with_is_strong_password(tmp_path, pw):
    (tmp_path / ".rodeo").mkdir()
    (tmp_path / ".rodeo" / "secrets.yaml").write_text(
        f'harvester_admin_password: "{pw}"\nrancher_admin_password: \'{pw}\'\n'
    )
    out = _run_check_script(tmp_path, ["harvester_admin_password", "rancher_admin_password"])
    word = "strong" if is_strong_password(pw) else "weak"
    assert out.splitlines() == [
        f"harvester_admin_password={word}",
        f"rancher_admin_password={word}",
    ]
    assert pw not in out


def test_remote_check_script_reports_missing_key_and_file(tmp_path):
    assert _run_check_script(tmp_path, ["rancher_admin_password"]) == "rancher_admin_password=missing\n"
    (tmp_path / ".rodeo").mkdir()
    (tmp_path / ".rodeo" / "secrets.yaml").write_text('harvester_admin_password: "x"\n')
    assert _run_check_script(tmp_path, ["rancher_admin_password"]) == "rancher_admin_password=missing\n"


# -- AWS security group ---------------------------------------------------------


def _aws(fake) -> AwsHostProvider:
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")


def _provisioned(fake) -> str:
    return _aws(fake)._resolve_security_groups(fake, _provider(), workshop="demo")[0]


def test_open_adds_world_only_on_requested_ui_ports():
    fake = _FakeEC2WithSG()
    sg_id = _provisioned(fake)
    assert _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=(8443, 30002)) == sg_id
    assert _all_cidrs(fake, sg_id) == {
        22: {OPERATOR},
        8443: {OPERATOR, WORLD},
        30002: {OPERATOR, WORLD},
    }


def test_close_revokes_world_and_keeps_operator():
    fake = _FakeEC2WithSG()
    sg_id = _provisioned(fake)
    _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=(8443, 30002))
    _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=())
    assert _all_cidrs(fake, sg_id) == {p: {OPERATOR} for p in MANAGED_SG_PORTS}


def test_reprovision_closes_opened_ports():
    fake = _FakeEC2WithSG()
    sg_id = _provisioned(fake)
    _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=(8443,))
    _provisioned(fake)
    assert fake._cidrs_for(sg_id, 8443) == {OPERATOR}


def test_reopen_is_idempotent():
    fake = _CountingEC2()
    _provisioned(fake)
    _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=(8443, 30002))
    fake.authorize_calls = fake.revoke_calls = 0
    _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=(8443, 30002))
    assert (fake.authorize_calls, fake.revoke_calls) == (0, 0)


def test_open_refuses_byo_security_groups_without_touching_aws():
    fake = _CountingEC2()
    with pytest.raises(ConfigError, match="does not own"):
        _aws(fake).set_student_access(
            _provider(security_group_ids=["sg-byo"]), workshop="demo", open_ports=(8443,)
        )
    assert (fake.authorize_calls, fake.revoke_calls) == (0, 0)


@pytest.mark.parametrize("ports", [(22,), (8443, 22), (9999,)])
def test_open_refuses_ssh_and_unmanaged_ports(ports):
    fake = _FakeEC2WithSG()
    _provisioned(fake)
    with pytest.raises(ConfigError, match="cannot be opened"):
        _aws(fake).set_student_access(_provider(), workshop="demo", open_ports=ports)


def test_open_without_managed_sg_fails():
    with pytest.raises(ConfigError, match="fleet provision"):
        _aws(_FakeEC2WithSG()).set_student_access(_provider(), workshop="demo", open_ports=(8443,))


def test_single_host_provision_never_opens_even_with_student_access_in_config(
    managed_ssh, monkeypatch  # noqa: F811
):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)
    fake = _FakeEC2WithSG()
    _aws(fake).provision(
        ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False),
        _provider(student_access="open"),
    )
    sg_id = next(g["GroupId"] for g in fake.security_groups.values() if g["GroupName"] == "rodeo-demo")
    assert _all_cidrs(fake, sg_id) == {p: {OPERATOR} for p in MANAGED_SG_PORTS}


# -- readiness gate -------------------------------------------------------------


def _status(ok=True, complete=True):
    report = {"phases_complete": complete, "phases": {}} if ok else None
    return lambda inv, hosts, **k: [
        HostStatusResult(id=h.id, ok=ok, error=None if ok else "ssh refused", report=report)
        for h in hosts
    ]


def _remote(stdout: str, rc: int = 0):
    calls: list[list[str]] = []

    def fake(inv, host, argv, **k):
        calls.append(list(argv))
        return RemoteResult(host_id=host.id, rc=rc, stdout=stdout, stderr="")

    return fake, calls


def test_check_host_not_ready_while_phases_incomplete(tmp_path, monkeypatch):
    inv = load_inventory(_inventory(tmp_path))
    monkeypatch.setattr(sa, "fleet_status", _status(complete=False))
    fake, calls = _remote("")
    monkeypatch.setattr(sa, "run_remote", fake)
    r = sa._check_host(inv, inv.hosts[0], ["harvester_admin_password"], timeout=5)
    assert (r.ready, r.reason) == (False, "deploy not finished (phases incomplete)")
    assert calls == []  # never looks at passwords before the deploy is done


@pytest.mark.parametrize(
    "stdout,ready",
    [
        ("harvester_admin_password=strong\nrancher_admin_password=strong\n", True),
        ("harvester_admin_password=strong\nrancher_admin_password=weak\n", False),
        ("harvester_admin_password=strong\n", False),  # rancher unchecked
        ("harvester_admin_password=missing\nrancher_admin_password=strong\n", False),
        ("garbage\n", False),
    ],
)
def test_check_host_password_verdicts(tmp_path, monkeypatch, stdout, ready):
    inv = load_inventory(_inventory(tmp_path))
    monkeypatch.setattr(sa, "fleet_status", _status())
    fake, calls = _remote(stdout)
    monkeypatch.setattr(sa, "run_remote", fake)
    keys = ["harvester_admin_password", "rancher_admin_password"]
    r = sa._check_host(inv, inv.hosts[0], keys, timeout=5)
    assert r.ready is ready
    assert calls[0][:2] == ["python3", "-c"] and calls[0][3:] == keys


def test_check_host_script_failure_is_not_ready(tmp_path, monkeypatch):
    inv = load_inventory(_inventory(tmp_path))
    monkeypatch.setattr(sa, "fleet_status", _status())
    monkeypatch.setattr(sa, "run_remote", _remote("", rc=127)[0])
    r = sa._check_host(inv, inv.hosts[0], ["harvester_admin_password"], timeout=5)
    assert (r.ready, r.reason) == (False, "password check failed (exit 127)")


def test_student_ports_follow_components(tmp_path):
    assert sa.student_ports(load_inventory(_inventory(tmp_path))) == {
        "harvester": 8443,
        "rancher": 30002,
    }
    inv = load_inventory(_inventory(tmp_path, lab_extra="  components: [harvester]"))
    assert sa.student_ports(inv) == {"harvester": 8443}


# -- fleet_open_access ----------------------------------------------------------


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def set_student_access(self, config, *, workshop, open_ports):
        self.calls.append((workshop, tuple(open_ports)))
        return "sg-managed-0001"


@pytest.fixture
def fake_provider(monkeypatch):
    prov = _FakeProvider()
    monkeypatch.setattr(sa, "get_provider", lambda name: prov)
    return prov


def _ready(monkeypatch, not_ready: set[str] = frozenset()):
    monkeypatch.setattr(
        sa,
        "_check_host",
        lambda inv, h, keys, timeout: sa.HostReadiness(
            h.id, h.id not in not_ready, "deploy not finished" if h.id in not_ready else None
        ),
    )


def test_open_requires_student_access_open(tmp_path, fake_provider, monkeypatch):
    _ready(monkeypatch)
    inv = load_inventory(_inventory(tmp_path))
    with pytest.raises(ConfigError, match="student_access is not `open`"):
        sa.fleet_open_access(inv)
    assert fake_provider.calls == []


def test_open_refused_when_any_host_not_ready(tmp_path, fake_provider, monkeypatch):
    _ready(monkeypatch, not_ready={"s2"})
    inv = load_inventory(_inventory(tmp_path, "  student_access: open"))
    result = sa.fleet_open_access(inv)
    assert result.action == "refused"
    assert fake_provider.calls == []
    assert [(h.id, h.ready) for h in result.hosts] == [("s1", True), ("s2", False)]


def test_open_when_all_ready_opens_component_ports(tmp_path, fake_provider, monkeypatch):
    _ready(monkeypatch)
    inv = load_inventory(
        _inventory(tmp_path, "  student_access: open", lab_extra="  components: [harvester]")
    )
    result = sa.fleet_open_access(inv)
    assert result.action == "opened"
    assert fake_provider.calls == [("demo", (8443,))]


def test_close_works_without_student_access_and_skips_checks(tmp_path, fake_provider, monkeypatch):
    monkeypatch.setattr(sa, "_check_host", lambda *a, **k: pytest.fail("close must not check hosts"))
    inv = load_inventory(_inventory(tmp_path))
    result = sa.fleet_open_access(inv, close=True)
    assert result.action == "closed"
    assert fake_provider.calls == [("demo", ())]


def test_open_access_on_bare_metal_inventory_fails(tmp_path):
    path = tmp_path / "workshop.yaml"
    path.write_text("name: bm\nlab: {dir: /root/lab}\nhosts: [{id: s1, ssh: 10.0.0.1}]\n")
    with pytest.raises(ConfigError):
        sa.fleet_open_access(load_inventory(path), close=True)


# -- CLI ------------------------------------------------------------------------


def test_cli_refused_exits_1_with_reasons(tmp_path, fake_provider, monkeypatch):
    from rodeo.cli import cli

    _ready(monkeypatch, not_ready={"s1"})
    path = _inventory(tmp_path, "  student_access: open")
    res = CliRunner().invoke(cli, ["fleet", "open-access", "-f", str(path), "--output", "json"])
    assert res.exit_code == 1
    assert '"action": "refused"' in res.output
    assert fake_provider.calls == []


def test_cli_opened_json(tmp_path, fake_provider, monkeypatch):
    from rodeo.cli import cli

    _ready(monkeypatch)
    path = _inventory(tmp_path, "  student_access: open")
    res = CliRunner().invoke(cli, ["fleet", "open-access", "-f", str(path), "--output", "json"])
    assert res.exit_code == 0, res.output
    assert '"action": "opened"' in res.output
    assert '"ports": [\n    8443,\n    30002\n  ]' in res.output
