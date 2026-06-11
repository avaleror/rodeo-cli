"""Deploy pipeline runner — single source of truth for all phase execution."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from ..state import is_phase_done, mark_phase_done, mark_phase_failed, reset_from


# ---------- Events ----------

@dataclass
class DeployEvent:
    pass


@dataclass
class PhaseStarted(DeployEvent):
    phase: str


@dataclass
class PhaseSkipped(DeployEvent):
    phase: str
    reason: str = ""  # "done" | "before_start"


@dataclass
class PhaseDone(DeployEvent):
    phase: str
    elapsed: float


@dataclass
class PhaseFailed(DeployEvent):
    phase: str
    rc: int
    message: str = ""


@dataclass
class LogLine(DeployEvent):
    line: str


@dataclass
class ProgressUpdate(DeployEvent):
    step: str
    elapsed: float
    total: float
    detail: str = ""


@dataclass
class DeployComplete(DeployEvent):
    pass


# ---------- Runner ----------

class DeployRunner:
    """Run the five-phase deploy pipeline and stream typed events.

    Usage:
        runner = DeployRunner(cfg, root)
        for event in runner.run():
            ... handle event ...
    """

    def __init__(
        self,
        cfg: dict,
        root: Path,
        from_phase: str | None = None,
        install_collections: bool = True,
        force: bool = False,
        include_guarded: bool = False,
    ) -> None:
        self.cfg = cfg
        self.root = root
        self.from_phase = from_phase
        self.install_collections = install_collections
        self.force = force
        self.include_guarded = include_guarded
        self._plan_name = cfg.get("name", "default")
        self._proc: subprocess.Popen | None = None
        self._last_rc: int = 0
        self.stop = threading.Event()

    def terminate(self) -> None:
        """Stop the pipeline: signal poll loops and SIGTERM the subprocess group."""
        self.stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                self._proc.terminate()

    def run(self) -> Iterator[DeployEvent]:
        """Yield deploy events for all phases. Stops after first failure."""
        from ..profiles import get_profile
        profile = get_profile(self.cfg.get("type", "suse-virt"))

        if self.from_phase:
            reset_from(self.from_phase, self._plan_name, profile.phases)

        start_idx = (
            profile.phases.index(self.from_phase)
            if self.from_phase and self.from_phase in profile.phases
            else 0
        )

        # Install Ansible collections if any ansible phase will run
        first_ansible = next(
            (i for i, p in enumerate(profile.phases) if p in profile.ansible_phases),
            len(profile.phases),
        )
        if self.install_collections and start_idx <= first_ansible:
            req_file = self.root / "ansible" / "requirements.yml"
            if req_file.exists():
                yield LogLine("Installing Ansible collections...")
                result = subprocess.run(
                    ["ansible-galaxy", "collection", "install", "-r", str(req_file)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    yield LogLine(f"  ✗  ansible-galaxy failed: {result.stderr.strip()}")
                    yield PhaseFailed("setup", result.returncode, "collection install failed")
                    return
                yield LogLine("  ✓  collections installed")

        vars_file = self._write_vars_file()

        guard_active = (
            self.cfg.get("deployment_target", "baremetal") == "instruqt"
            and not self.include_guarded
        )

        for idx, phase in enumerate(profile.phases):
            if idx < start_idx:
                yield PhaseSkipped(phase, "before_start")
                continue
            if not self.force and is_phase_done(phase, self._plan_name):
                yield PhaseSkipped(phase, "done")
                continue
            if guard_active and phase in profile.guarded_phases:
                yield LogLine(
                    f"  ⚠  {phase} skipped: deployment_target is 'instruqt' and this "
                    "phase breaks Instruqt image save. Run with --finalise after the "
                    "image snapshot, or set deployment_target: baremetal."
                )
                yield PhaseSkipped(phase, "instruqt")
                continue
            if self.stop.is_set():
                mark_phase_failed(phase, "cancelled", self._plan_name)
                yield PhaseFailed(phase, 130, "cancelled")
                return

            yield PhaseStarted(phase)
            t0 = time.monotonic()

            try:
                yield from profile.run_phase(phase, self, vars_file)
            except Exception as exc:
                self._last_rc = 1
                yield LogLine(f"  ✗  {phase}: unexpected error: {exc}")

            elapsed = time.monotonic() - t0
            ok = self._last_rc == 0

            if ok:
                mark_phase_done(phase, self._plan_name)
                yield PhaseDone(phase, elapsed)
            else:
                mark_phase_failed(phase, f"{phase} exited {self._last_rc}", self._plan_name)
                yield PhaseFailed(phase, self._last_rc, f"{phase} failed")
                return

        yield DeployComplete()

    # ---------- Subprocess helpers ----------

    def _stream_subprocess(
        self, cmd: list[str], env: dict | None = None
    ) -> Iterator[DeployEvent]:
        """Launch a subprocess, stream stdout as LogLine events, set _last_rc."""
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        for raw in self._proc.stdout:  # type: ignore[union-attr]
            yield LogLine(raw.rstrip())
        self._proc.wait()
        self._last_rc = self._proc.returncode
        self._proc = None

    # ---------- Phase runners (public API for profiles) ----------

    def stream_ansible(self, tags: str, vars_file: Path) -> Iterator[DeployEvent]:
        inventory = self.root / self.cfg["ansible"]["inventory"]
        playbook = self.root / "ansible" / "playbook.yml"
        cmd = [
            "ansible-playbook",
            "-i", str(inventory),
            str(playbook),
            "--tags", tags,
            "-e", f"@{vars_file}",
        ]
        yield from self._stream_subprocess(cmd)

    def stream_cluster(self) -> Iterator[DeployEvent]:
        yield LogLine("Starting firewalld...")
        subprocess.run(["systemctl", "start", "firewalld"], capture_output=True)
        r = subprocess.run(["firewall-cmd", "--reload"], capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(f"  ⚠  firewall-cmd reload: {r.stderr.strip()}")

        from .cluster import ClusterPhase
        phase = ClusterPhase(self.cfg, stop=self.stop)
        yield from phase.stream()
        self._last_rc = 0 if phase.success else 1
        if phase.error:
            yield LogLine(f"  ✗  cluster: {phase.error}")

    def stream_rancher(self) -> Iterator[DeployEvent]:
        from .rancher import RancherPhase
        phase = RancherPhase(self.cfg, stop=self.stop)
        yield from phase.stream()
        self._last_rc = 0 if phase.success else 1
        if phase.error:
            yield LogLine(f"  ✗  rancher: {phase.error}")

    def stream_finalise(self) -> Iterator[DeployEvent]:
        vm_names = list(self.cfg.get("vms", {}).keys())
        successes = 0
        try:
            from .libvirt import LibvirtDriver
            with LibvirtDriver(self.cfg["libvirt"]["uri"]) as lv:
                for vm in vm_names:
                    try:
                        lv.set_autostart(vm, True)
                        yield LogLine(f"  autostart enabled: {vm}")
                        successes += 1
                    except Exception as exc:
                        yield LogLine(f"  ⚠  autostart {vm}: {exc}")
        except Exception as exc:
            yield LogLine(f"  ✗  libvirt: {exc}")
            self._last_rc = 1
            return

        if successes == 0:
            yield LogLine("  ✗  No VMs got autostart — aborting finalise")
            self._last_rc = 1
            return

        r = subprocess.run(
            ["systemctl", "enable", "libvirt-guests"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            yield LogLine(f"  ⚠  libvirt-guests enable: {r.stderr.strip()}")
        else:
            yield LogLine("  libvirt-guests enabled")

        self._last_rc = 0

    # ---------- Config helpers ----------

    def _write_vars_file(self) -> Path:
        """Write all plan values to a tempfile (mode 600) for Ansible -e @file.

        Keeps secrets off argv and wires resources/versions into Ansible.
        File is deleted on process exit via atexit.
        """
        creds = self.cfg.get("credentials", {})
        net = self.cfg.get("network", {})
        ver = self.cfg.get("versions", {})
        h_res = self.cfg.get("resources", {}).get("harvester", {})
        r_res = self.cfg.get("resources", {}).get("rancher", {})
        storage = self.cfg.get("storage", {})

        vars_data = {
            "network_mode":          net.get("mode", "nat"),
            "host_bridge":           net.get("host_bridge", "br0"),
            "harvester_vip":         net.get("vip", "192.168.122.10"),
            "rancher_ip":            net.get("rancher_ip", "192.168.122.9"),
            "dns_domain":            net.get("dns_domain", "aerogrid.com"),
            "libvirt_network_gateway": net.get("gateway", "192.168.122.1"),
            "harvester_os_password": creds.get("harvester_os_password", ""),
            "rancher_vm_password":   creds.get("harvester_os_password", ""),
            "harvester_version":     ver.get("harvester", "1.8.0"),
            "harvester_memory_mb":   h_res.get("memory_mib", 16384),
            "harvester_vcpu":        h_res.get("vcpu", 8),
            "harvester_disk_gb":     h_res.get("disk_gb", 270),
            "rancher_memory_mb":     r_res.get("memory_mib", 8192),
            "rancher_vcpu":          r_res.get("vcpu", 4),
            "rancher_disk_gb":       r_res.get("disk_gb", 60),
            "image_dir":             storage.get("image_dir", "/var/lib/libvirt/images"),
        }
        # Only override the role-default join token when the plan provides one.
        if creds.get("harvester_token"):
            vars_data["harvester_token"] = creds["harvester_token"]

        rodeo_dir = Path.home() / ".rodeo"
        rodeo_dir.mkdir(parents=True, exist_ok=True)
        # Sweep vars files left behind by a previous SIGKILL'd run.
        for stale in rodeo_dir.glob("rodeo-vars-*.yaml"):
            stale.unlink(missing_ok=True)
        fd, path_str = tempfile.mkstemp(
            prefix="rodeo-vars-", suffix=".yaml", dir=rodeo_dir
        )
        os.chmod(path_str, 0o600)
        os.close(fd)
        with open(path_str, "w") as f:
            yaml.dump(vars_data, f, default_flow_style=False)

        path = Path(path_str)
        atexit.register(lambda p=path: p.unlink(missing_ok=True))
        return path
