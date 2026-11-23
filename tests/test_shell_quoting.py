"""Plan values reach remote and local shells as data, never as commands.

Each test puts a value like ``1.31; touch MARKER`` (plus ``$(...)`` and
backtick forms) into one plan field, captures the script rodeo would send over
SSH, and runs it under a local bash where every external command is a no-op
that logs its argv. The marker must not exist and the value must arrive intact
as an argument. Values that land inside a quoted heredoc get a newline and the
terminator instead: rodeo must refuse them before anything runs.
"""
from __future__ import annotations

import dataclasses
import subprocess
import textwrap

import pytest

from rodeo.config import ConfigError
from rodeo.engine.rancher import RancherPhase
from rodeo.shellquote import heredoc, q

from .conftest import drain

PRELUDE = r"""
command_not_found_handle() {
  printf '%s\n' "$*" >> "$RODEO_TEST_LOG"
  case "$1" in curl) printf 200 ;; esac
  # A real command reads its stdin. A stub that does not leaves the writer of
  # `a | b` with no reader, so the writer dies of SIGPIPE and -e/pipefail end
  # the script (rc 141) before the probe runs: `read` is a builtin, so this
  # drains the pipe without looking anything up on the empty PATH.
  while IFS= read -r _; do :; done
  return 0
}
cd() { :; }
"""


def _payload(marker) -> str:
    m = str(marker)
    return f"1.31; /usr/bin/touch {m}; $(/usr/bin/touch {m}) `/usr/bin/touch {m}`"


def _run(script: str, tmp_path) -> str:
    """Run *script* with no real commands on PATH; return stdout plus the logged argv."""
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    log = tmp_path / "argv.log"
    r = subprocess.run(
        ["/bin/bash", "-c", PRELUDE + script],
        env={"PATH": str(empty), "HOME": str(tmp_path), "RODEO_TEST_LOG": str(log)},
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    return r.stdout + r.stderr + (log.read_text() if log.exists() else "")


def assert_inert(script: str, tmp_path, marker, payload: str, probe: str = "") -> None:
    """*probe* is appended to the script to print a value that never reaches argv."""
    seen = _run(script + probe, tmp_path)
    assert not marker.exists(), f"plan value ran as shell:\n{script}"
    assert payload in seen, f"the value never reached a command intact:\n{seen}"


class _Capture:
    """Stand-in for RemoteExecMixin._run: records each script, answers rc 0."""

    def __init__(self, stdout: str = ""):
        self.scripts: list[str] = []
        self.stdout = stdout

    def __call__(self, cmd, timeout=None, input=None):
        if input is not None:
            self.scripts.append(input)
        else:
            # _ssh_run: ssh joins the remote argv with spaces for the remote shell.
            host = next(i for i, a in enumerate(cmd) if str(a).startswith("root@"))
            self.scripts.append(" ".join(cmd[host + 1:]))
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


def _cfg() -> dict:
    return {
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9",
                    "gateway": "10.0.0.1", "dns_domain": "lab.example"},
        "credentials": {"harvester_admin_password": "x", "rancher_admin_password": "x"},
        "vms": {"rancher": {}},
    }


def _phase(cfg=None, stdout: str = "") -> tuple[RancherPhase, _Capture]:
    p = RancherPhase(cfg or _cfg())
    cap = _Capture(stdout)
    p._run = cap
    p._sleep = lambda s: True  # never wait in a poll loop
    return p, cap


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "MARKER"


def test_harness_catches_an_unquoted_value(tmp_path, marker):
    _run(f"set -euo pipefail\nhelm install --version {_payload(marker)}\n", tmp_path)
    assert marker.exists()


# ---------- rancher: cluster_setup ----------

def test_k3s_version(tmp_path, marker):
    cfg = _cfg()
    cfg["versions"] = {"k3s": _payload(marker)}
    p, cap = _phase(cfg)
    drain(p._install_k3s())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker),
                 probe='printf "%s\\n" "$INSTALL_K3S_VERSION"\n')


def test_cert_manager_version(tmp_path, marker):
    cfg = _cfg()
    cfg["versions"] = {"cert_manager": _payload(marker)}
    p, cap = _phase(cfg)
    drain(p._install_cert_manager())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_rancher_repo_name_and_url(tmp_path, marker):
    """suse-telco sets RANCHER_REPO_* per instance from telco.rancher_repo."""
    p, cap = _phase()
    p.RANCHER_REPO_NAME = _payload(marker)
    p.RANCHER_REPO_URL = _payload(marker)
    drain(p._install_cert_manager())
    drain(p._install_rancher())
    for script in cap.scripts:
        assert_inert(script, tmp_path, marker, _payload(marker))


# ---------- rancher: content ----------

def test_gitea_values(tmp_path, marker):
    p, cap = _phase()
    for attr in ("gitea_user", "gitea_password", "gitea_version", "alien_geeko_fleet_name",
                 "alien_geeko_fleet_repo", "eib_ip", "gitea_port"):
        setattr(p, attr, _payload(marker))
    drain(p._deploy_gitea())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_gitea_edge_node_values_cannot_close_the_heredoc(marker):
    p, cap = _phase()
    p.edge_nodes = [{"name": "edge1", "ip": "10.0.0.31",
                     "mac": f"52:54:00:00:00:01\nNM_EOF\n/usr/bin/touch {marker}"}]
    with pytest.raises(ValueError, match="NM_EOF"):
        drain(p._deploy_gitea())
    assert not cap.scripts and not marker.exists()


def test_gitea_edge_node_name_is_quoted(tmp_path, marker):
    p, cap = _phase()
    name = _payload(marker)
    p.edge_nodes = [{"name": name, "ip": "10.0.0.31", "mac": "52:54:00:00:00:01"}]
    drain(p._deploy_gitea())
    # The name is only a redirect target (never argv), so check the quoting itself.
    assert f'"$EIB_REPO"/{q(f"network-configs/{name}.yaml")} <<' in cap.scripts[0]
    _run(cap.scripts[0], tmp_path)
    assert not marker.exists()


def test_fleet_gitrepo_cannot_close_the_heredoc(marker):
    p, cap = _phase()
    p.alien_geeko_target_labels = {"site": f"x\n__GITREPO__\n/usr/bin/touch {marker}"}
    with pytest.raises(ValueError, match="__GITREPO__"):
        drain(p._create_alien_geeko_fleet())
    assert not cap.scripts


# ---------- rancher: downstream ----------

def test_downstream_preload_distro(tmp_path, marker, monkeypatch):
    p, cap = _phase(stdout="/var/tmp/rodeo-preload.tar\n")
    p.vm_ips = {"n1": "10.0.0.41"}
    monkeypatch.setattr(p, "_preload_script", lambda distro, version: "true")
    monkeypatch.setattr(p, "_copy_preload", lambda *a: subprocess.CompletedProcess([], 0, "", ""))
    p._preload_distro(_payload(marker), ["n1"])
    node = cap.scripts[1]
    assert node.startswith("test -d ")
    # `test` is a builtin; run it as an external command so its argv is logged.
    assert_inert("ls" + node[len("test"):], tmp_path, marker, _payload(marker))


def test_downstream_cluster_record_stays_in_its_heredoc(tmp_path, marker):
    p, cap = _phase()
    drain(p._apply_cluster_record(f"c1\n__MANIFEST__\n/usr/bin/touch {marker}", "v1"))
    _run(cap.scripts[0], tmp_path)
    assert not marker.exists()


# ---------- rancher: elemental ----------

def test_elemental_versions(tmp_path, marker):
    p, cap = _phase()
    p.elemental_crds_version = _payload(marker)
    p.elemental_op_version = _payload(marker)
    p.profile_type = "suse-virt"  # skip the suse-edge content steps
    drain(p._install_elemental())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_elemental_registrations_cannot_close_the_heredoc(marker):
    p, cap = _phase()
    p.elemental_reg_prefix = f"x\n__ELEMENTAL_MANIFEST__\n/usr/bin/touch {marker}"
    p.elemental_reg_count = 1
    with pytest.raises(ValueError, match="__ELEMENTAL_MANIFEST__"):
        drain(p._create_machine_registrations())
    assert not cap.scripts


# ---------- rancher: extensions ----------

def test_ui_extension_name_and_namespace(tmp_path, marker):
    p, cap = _phase()
    p._ui_extension_version(_payload(marker), _payload(marker))
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_extension_repo_name(tmp_path, marker):
    p, cap = _phase()
    drain(p._ensure_ext_repo(_payload(marker), "https://github.com/x/y", "main"))
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_extension_repo_url_cannot_close_the_heredoc(marker):
    p, cap = _phase()
    with pytest.raises(ValueError, match="__EXT_REPO__"):
        drain(p._ensure_ext_repo("r", f"https://x\n__EXT_REPO__\n/usr/bin/touch {marker}", "main"))
    assert not cap.scripts


# ---------- rancher: hauler ----------

def test_hauler_images_and_urls(tmp_path, marker):
    p, cap = _phase()
    for attr in ("eib_image", "alien_geeko_image", "elemental_op_version",
                 "leap_micro_iso_url", "leap_micro_raw_url",
                 "elemental_register_rpm_url", "elemental_system_agent_rpm_url"):
        setattr(p, attr, _payload(marker))
    drain(p._populate_hauler())
    script = cap.scripts[0].replace("HAULER=/usr/local/bin/hauler", "HAULER=hauler")
    assert_inert(script, tmp_path, marker, _payload(marker))


def test_hauler_mirror_ip_cannot_close_either_heredoc(marker):
    for term in ("K3S_REG", "EOF"):
        p, cap = _phase()
        p.eib_ip = f"10.0.0.20\n{term}\n/usr/bin/touch {marker}"
        with pytest.raises(ValueError, match=term):
            drain(p._populate_hauler())
        assert not cap.scripts


# ---------- suse-telco ----------

def _telco(monkeypatch, tmp_path, **cache):
    from rodeo.engine.telco import TelcoPhase
    from rodeo.profiles import get_profile

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = get_profile("suse-telco").default_cfg()
    cfg.update({"type": "suse-telco", "name": "suse-telco-test"})
    cfg["credentials"] = {"bmc_password": "b", "rancher_admin_password": "r"}
    cfg.setdefault("telco", {}).setdefault("image_cache", {}).update(cache)
    p = TelcoPhase(cfg)
    cap = _Capture(stdout="Ready\n")
    p._run = cap
    p._sleep = lambda s: True
    return p, cap


def test_telco_image_name_must_be_a_file_name(monkeypatch, tmp_path, marker):
    with pytest.raises(ConfigError, match="image_name"):
        _telco(monkeypatch, tmp_path, image_name=_payload(marker))


def test_telco_gitea_mirror(monkeypatch, tmp_path, marker):
    p, cap = _telco(monkeypatch, tmp_path)
    for attr in ("gitea_user", "gitea_password", "alien_geeko_fleet_name",
                 "alien_geeko_fleet_repo", "alien_geeko_fleet_branch", "gitea_port"):
        setattr(p, attr, _payload(marker))
    drain(p._install_gitea_mirror())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


def test_telco_mgmt_hostname(monkeypatch, tmp_path, marker):
    p, cap = _telco(monkeypatch, tmp_path)
    p.mgmt_hostname = _payload(marker)
    drain(p._wait_rke2_ready())
    script = cap.scripts[0].replace("test -", ": test -").replace("ln -sf", ": ln")
    assert_inert(script, tmp_path, marker, _payload(marker))


def test_telco_rancher_repo_from_plan(monkeypatch, tmp_path, marker):
    from rodeo.engine.telco import TelcoPhase
    from rodeo.profiles import get_profile

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = get_profile("suse-telco").default_cfg()
    cfg.update({"type": "suse-telco", "name": "t"})
    cfg["credentials"] = {"bmc_password": "b", "rancher_admin_password": "r"}
    cfg.setdefault("telco", {})["rancher_repo"] = {"name": _payload(marker), "url": _payload(marker)}
    p = TelcoPhase(cfg)
    assert p.RANCHER_REPO_NAME == _payload(marker)
    cap = _Capture()
    p._run = cap
    drain(p._install_cert_manager())
    assert_inert(cap.scripts[0], tmp_path, marker, _payload(marker))


# ---------- lab-in-a-box host commands ----------

def test_labinabox_setup_and_schema_commands(tmp_path, marker):
    from rodeo import labinabox_host as host

    setup = host.setup_command(f"/tmp/{_payload(marker)}/lab.json", 2)
    setup = setup.replace(q(host.LIAB_BIN / "setup_lab.py"), "setup_lab")
    assert_inert(setup, tmp_path, marker, _payload(marker))
    for cmd in host.schema_commands({_payload(marker)}).values():
        _run(cmd, tmp_path)
    assert not marker.exists()


# ---------- fleet ----------

def test_fleet_sync_lab_dir(tmp_path, marker):
    from rodeo.fleet.inventory import load_inventory
    from rodeo.fleet.sync import sync_script

    ws = tmp_path / "workshop.yaml"
    ws.write_text(textwrap.dedent("""
        name: demo
        lab:
          dir: /root/lab
          source: https://github.com/x/y
        hosts:
          - id: a
            ssh: 10.0.0.1
    """))
    inv = dataclasses.replace(load_inventory(ws), lab_dir=f"/root/{_payload(marker)}")
    # Make the "exists but is not a git repo" branch run: that echo used the raw value.
    script = sync_script(inv).replace("if [ -d", "if false && [ -d").replace(
        "elif [ -e", "elif true || [ -e")
    assert_inert(script, tmp_path, marker, _payload(marker))


# ---------- ssh destinations from the plan ----------

def test_cluster_kubeconfig_ssh_user_is_not_an_option(monkeypatch):
    from rodeo.engine import cluster as cluster_mod

    p = cluster_mod.ClusterPhase({"network": {"vip": "10.0.0.10"},
                                  "vms": {"harvester1": {"user": "-oProxyCommand=touch X"}}})
    p.harvester_user = "-oProxyCommand=touch X"
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "apiVersion: v1\nserver: https://127.0.0.1:6443\n", "")

    monkeypatch.setattr(cluster_mod.subprocess, "run", fake_run)
    p._sleep = lambda s: True
    drain(p._fetch_kubeconfig())
    argv = seen[0]
    dest = argv.index("-oProxyCommand=touch X@10.0.0.10")
    assert argv[dest - 1] == "--"


# ---------- helpers ----------

def test_q_quotes_non_strings():
    assert q(8080) == "8080"
    assert q("a b") == "'a b'"


def test_heredoc_refuses_only_a_full_terminator_line():
    assert heredoc("a: EOF\nEOF-ish\n", "EOF") == "a: EOF\nEOF-ish\n"
    with pytest.raises(ValueError):
        heredoc("a\nEOF\nb", "EOF")


# ---------- /etc/profile.d/rodeo.sh (sourced by every login shell) ----------

def test_env_file_values_are_data(tmp_path, marker, monkeypatch):
    from rodeo.engine.rancher import summary

    monkeypatch.setenv("HOME", str(tmp_path))
    written = {}

    class _NoEtc(type(summary.Path())):
        def write_text(self, content, *a, **k):
            if str(self).startswith("/etc/"):
                raise PermissionError(str(self))
            written["content"] = content
            return len(content)

    monkeypatch.setattr(summary, "Path", _NoEtc)
    import rodeo.paths as paths
    monkeypatch.setattr(paths, "rodeo_dir", lambda: _NoEtc(tmp_path))
    p, _ = _phase()
    p.rancher_password = _payload(marker)
    p.harvester_password = _payload(marker)
    p._write_env_file()
    assert_inert(written["content"], tmp_path, marker, _payload(marker),
                 probe='printf "%s\\n" "$RANCHER_ADMIN_PASSWORD" "$HARVESTER_ADMIN_PASSWORD"\n')
