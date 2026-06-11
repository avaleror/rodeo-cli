"""Deploy pipeline runner — single source of truth for all phase execution."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from ..state import PHASES, is_phase_done, mark_phase_done, mark_phase_failed, reset_from


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
    ) -> None:
        self.cfg = cfg
        self.root = root
        self.from_phase = from_phase
        self.install_collections = install_collections
        self.force = force
        self._proc: subprocess.Popen | None = None
        self._last_rc: int = 0

    def terminate(self) -> None:
        """Send SIGTERM to the current subprocess process group."""
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                self._proc.terminate()

    def run(self) -> Iterator[DeployEvent]:
        """Yield deploy events for all phases. Stops after first failure."""
        if self.from_phase:
            reset_from(self.from_phase)

        start_idx = (
            PHASES.index(self.from_phase)
            if self.from_phase and self.from_phase in PHASES
            else 0
        )

        # Install Ansible collections before any Ansible phase
        if self.install_collections and start_idx <= PHASES.index("vms"):
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

        for idx, phase in enumerate(PHASES):
            if idx < start_idx:
                yield PhaseSkipped(phase, "before_start")
                continue
            if not self.force and is_phase_done(phase):
                yield PhaseSkipped(phase, "done")
                continue

            yield PhaseStarted(phase)
            t0 = time.monotonic()

            if phase in ("kvm_host", "vms"):
                yield from self._stream_ansible(phase, vars_file)
            elif phase == "cluster":
                yield from self._stream_cluster()
            elif phase == "rancher":
                yield from self._stream_rancher()
            elif phase == "finalise":
                yield from self._stream_finalise()
            else:
                self._last_rc = 0

            elapsed = time.monotonic() - t0
            ok = self._last_rc == 0

            if ok:
                mark_phase_done(phase)
                yield PhaseDone(phase, elapsed)
            else:
                mark_phase_failed(phase, f"{phase} exited {self._last_rc}")
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

    # ---------- Phase runners ----------

    def _stream_ansible(self, tags: str, vars_file: Path) -> Iterator[DeployEvent]:
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

    def _stream_cluster(self) -> Iterator[DeployEvent]:
        yield LogLine("Starting firewalld...")
        subprocess.run(["systemctl", "start", "firewalld"], capture_output=True)
        r = subprocess.run(["firewall-cmd", "--reload"], capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(f"  ⚠  firewall-cmd reload: {r.stderr.strip()}")

        start_script = self.root / "deployer" / "lib" / "start-vms.sh"
        if not start_script.exists():
            yield LogLine(f"  ✗  {start_script} not found")
            self._last_rc = 1
            return

        env = {**os.environ, "HARVESTER_VIP": self.cfg["network"]["vip"]}
        yield from self._stream_subprocess([str(start_script)], env=env)

    def _stream_rancher(self) -> Iterator[DeployEvent]:
        script = self.root / "deployer" / "lib" / "setup-rancher.sh"
        if not script.exists():
            yield LogLine(f"  ✗  {script} not found")
            self._last_rc = 1
            return

        creds = self.cfg.get("credentials", {})
        net = self.cfg["network"]
        ver = self.cfg.get("versions", {})
        env = {
            **os.environ,
            "RANCHER_VM_IP":         net.get("rancher_ip", "192.168.122.9"),
            "RANCHER_VERSION":       ver.get("rancher", "2.13.1"),
            "K3S_VERSION":           ver.get("k3s", "v1.31.4+k3s1"),
            "HARVESTER_VIP":         net.get("vip", "192.168.122.10"),
            "HARVESTER_OS_PASSWORD": creds.get("harvester_os_password", ""),
            "CERT_MANAGER_VERSION":  ver.get("cert_manager", "v1.16.2"),
            "LAB_ADMIN_PASSWORD":    creds.get(
                "lab_admin_password", creds.get("harvester_os_password", "")
            ),
        }
        yield from self._stream_subprocess([str(script)], env=env)

    def _stream_finalise(self) -> Iterator[DeployEvent]:
        successes = 0
        try:
            from .libvirt import LibvirtDriver, RODEO_VMS
            with LibvirtDriver(self.cfg["libvirt"]["uri"]) as lv:
                for vm in RODEO_VMS:
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

        rodeo_dir = Path.home() / ".rodeo"
        rodeo_dir.mkdir(parents=True, exist_ok=True)
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
