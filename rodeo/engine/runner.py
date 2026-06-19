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


# ---------- Networking helpers ----------

def _detect_ext_iface() -> str:
    """Return the interface that holds the default route (the external NIC)."""
    try:
        r = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if "dev" in parts:
                return parts[parts.index("dev") + 1]
    except Exception:
        pass
    return "eth0"


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


# Known Harvester ISO checksums (releases.rancher.com). When the plan pins a
# version not listed here, the checksum is passed empty so get_url skips
# verification instead of failing against the 1.8.0 role default.
_HARVESTER_ISO_CHECKSUMS = {
    "1.8.0": "sha512:dcbe2b2ba47e1f15854eb054f0cf5a5efe711db7aa86c4a4e50410e0f12aa5481085f99b85e62e89ddb53b95b61dc859b8568152f986be7d4168fd6b8ead026a",
}


# ---------- Runner ----------

class DeployRunner:
    """Run the deploy pipeline and stream typed events.

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
        ansible_verbose: int = 0,
    ) -> None:
        self.cfg = cfg
        self.root = root
        self.from_phase = from_phase
        self.install_collections = install_collections
        self.force = force
        self.include_guarded = include_guarded
        self.ansible_verbose = ansible_verbose
        self._plan_name = cfg.get("name", "default")
        self._proc: subprocess.Popen | None = None
        self._last_rc: int = 0
        self.stop = threading.Event()
        self._background_rancher = None  # set by stream_cluster when setup ran in background

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

        version = self.cfg.get("versions", {}).get("harvester", "1.8.0")
        if version not in _HARVESTER_ISO_CHECKSUMS:
            yield LogLine(
                f"  ⚠  No known ISO checksum for Harvester {version} — "
                "the download will not be verified."
            )

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

    @property
    def _log_file(self) -> Path:
        log_dir = Path.home() / ".rodeo" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{self._plan_name}.log"

    def _stream_subprocess(
        self, cmd: list[str], env: dict | None = None
    ) -> Iterator[DeployEvent]:
        """Launch a subprocess, stream stdout as LogLine events, set _last_rc."""
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        with open(self._log_file, "a", errors="replace") as lf:
            lf.write(f"\n--- {' '.join(cmd)} ---\n")
            for raw in self._proc.stdout:  # type: ignore[union-attr]
                lf.write(raw)
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
        if self.ansible_verbose > 0:
            cmd.append("-" + "v" * min(self.ansible_verbose, 4))
        yield from self._stream_subprocess(cmd)

    def _start_firewalld(self) -> Iterator[DeployEvent]:
        """Unmask, start, and target-aware configure firewalld.

        kvm_host masks firewalld during the Ansible phase to protect Instruqt's
        external NIC from zone reassignment. This method unmaskes it, starts it,
        and applies target-specific hardening before the first DNAT'd packet arrives.
        """
        target = self.cfg.get("deployment_target", "baremetal")
        yield LogLine(f"Starting firewalld (target: {target})...")
        subprocess.run(["systemctl", "unmask", "firewalld"], capture_output=True)
        subprocess.run(["systemctl", "start", "firewalld"], capture_output=True)

        if target == "instruqt":
            # On Instruqt (GCP), cloud-init doesn't assign a firewalld zone to the
            # external NIC. Without an explicit assignment the NIC falls into the
            # default zone (usually public), but we pin it to be safe — the DNAT
            # port_forward rules are in the public zone and only apply to interfaces
            # that are in it.
            ext = _detect_ext_iface()
            yield LogLine(f"  Instruqt: pinning {ext} to public zone...")
            subprocess.run(
                ["firewall-cmd", "--zone=public", f"--change-interface={ext}", "--permanent"],
                capture_output=True,
            )

        r = subprocess.run(["firewall-cmd", "--reload"], capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(f"  ⚠  firewall-cmd reload: {r.stderr.strip()}")

        # Re-trigger the libvirt hook so `ct status dnat accept` lands in guest_input
        # now that firewalld's DNAT rules are actually active. The hook also fires on
        # VM plug-in events, but this covers the gap between firewalld start and the
        # first VM boot.
        hook = Path("/etc/libvirt/hooks/network")
        if hook.exists():
            subprocess.run([str(hook), "default", "started"], capture_output=True)

        if target == "instruqt":
            net = self.cfg.get("network", {})
            ui_port = net.get("harvester_ui_port", 8443)
            rport = net.get("rancher_nodeport", 30002)
            yield LogLine(
                f"  Instruqt: host ports {ui_port} (Harvester) and {rport} (Rancher) "
                "are forwarded. Make sure both are declared as services in the "
                "Instruqt track configuration so GCP opens them."
            )

    def stream_cluster(self) -> Iterator[DeployEvent]:
        yield from self._start_firewalld()

        from .cluster import ClusterPhase
        phase = ClusterPhase(self.cfg, stop=self.stop)
        yield from phase.stream()
        self._last_rc = 0 if phase.success else 1
        if phase.error:
            yield LogLine(f"  ✗  cluster: {phase.error}")
        # Pass the background RancherPhase to stream_rancher if setup completed.
        if phase._background_rancher is not None and phase._background_rancher.setup_done:
            self._background_rancher = phase._background_rancher

    def stream_boot(self) -> Iterator[DeployEvent]:
        """Bring up host network + start the lab VMs (for non-Harvester profiles).

        The suse-virt pipeline starts VMs inside ClusterPhase (which also waits on the
        Harvester VIP, etcd join, and nodes Ready). Profiles without a Harvester cluster
        (e.g. rancher) use this lighter step instead: start firewalld, activate the
        libvirt network, and boot the defined VMs so the next phase can SSH to them.
        """
        yield from self._start_firewalld()

        uri = self.cfg.get("libvirt", {}).get("uri", "qemu:///system")
        vm_names = list(self.cfg.get("vms", {}).keys())
        try:
            from .libvirt import LibvirtDriver
            with LibvirtDriver(uri) as lv:
                yield LogLine("Ensuring libvirt network (virbr0) is up...")
                try:
                    lv.net_start("default")
                    lv.net_set_autostart("default", True)
                    yield LogLine("  virbr0 active.")
                except Exception as exc:
                    yield LogLine(f"  ⚠  network start: {exc}")
                for name in vm_names:
                    info = lv.get_vm(name)
                    if info.state == "not found":
                        yield LogLine(f"  ✗ {name}: domain not found — was the vms phase completed?")
                        self._last_rc = 1
                        return
                    if info.state == "running":
                        yield LogLine(f"  {name}: already running.")
                    else:
                        lv.start(name)
                        yield LogLine(f"  {name}: started.")
        except Exception as exc:
            yield LogLine(f"  ✗  libvirt: {exc}")
            self._last_rc = 1
            return
        self._last_rc = 0

    def stream_rancher(self) -> Iterator[DeployEvent]:
        from .rancher import RancherPhase
        background = self._background_rancher
        if background is not None and background.setup_done:
            # K3s + Helm + Rancher Prime already completed during the cluster phase wait.
            # Set passwords + eject ISOs only — Harvester import is a lab exercise.
            yield LogLine("Rancher K3s/Helm already complete — setting passwords and ejecting ISOs...")
            phase = background
            yield LogLine("Setting Harvester dashboard admin password...")
            yield from phase._set_harvester_password()
            yield LogLine("Ejecting installer ISOs from Harvester VMs...")
            yield from phase._eject_cdroms()
            phase._write_env_file()
            yield LogLine(phase._completion_summary())
            phase.success = True
        else:
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

        Keeps secrets off argv and wires resources/versions/network into Ansible.
        For the Harvester/SUSE Virtualization rodeo, vm_nodes (full with MACs, UUIDs,
        per-node interfaces/cables) now come from the centralized definition file
        (rodeo/data/platforms/suse-virt/definition.yaml) via the inventory renderer.
        host_prep (sysctls, selinux, ovmf, network rules) also from definition (Phase 1 EIB plan).
        Explicit values in the definition are used (matching previous defaults exactly).
        Generation happens for omitted fields. File deleted on exit via atexit.
        """
        creds = self.cfg.get("credentials", {})
        net = self.cfg.get("network", {})
        ver = self.cfg.get("versions", {})
        h_res = self.cfg.get("resources", {}).get("harvester", {})
        r_res = self.cfg.get("resources", {}).get("rancher", {})
        storage = self.cfg.get("storage", {})

        version = ver.get("harvester", "1.8.0")
        vars_data = {
            "network_mode":          net.get("mode", "nat"),
            "host_bridge":           net.get("host_bridge", "br0"),
            "harvester_vip":         net.get("vip", "192.168.122.10"),
            "rancher_ip":            net.get("rancher_ip", "192.168.122.9"),
            "lab_dns_domain":        net.get("dns_domain", "aerogrid.com"),
            "libvirt_network_gateway": net.get("gateway", "192.168.122.1"),
            "harvester_os_password": creds.get("harvester_os_password", ""),
            "rancher_vm_password":   creds.get("harvester_os_password", ""),
            "harvester_version":     version,
            "harvester_iso_checksum": _HARVESTER_ISO_CHECKSUMS.get(version, ""),
            # Nested structure matches what roles/vms actually consumes
            # (vm.xml.j2, images.yml) — flat per-flavor keys are not read.
            "libvirt_flavors": {
                "harvester": {
                    "memory_mib": h_res.get("memory_mib", 16384),
                    "vcpu":       h_res.get("vcpu", 8),
                    "disk_gb":    h_res.get("disk_gb", 270),
                },
                "rancher": {
                    "memory_mib": r_res.get("memory_mib", 8192),
                    "vcpu":       r_res.get("vcpu", 4),
                    "disk_gb":    r_res.get("disk_gb", 60),
                },
            },
            "image_dir":             storage.get("image_dir", "/var/lib/libvirt/images"),
            "libvirt_pool_name":     storage.get("libvirt_pool_name", "default"),
            "libvirt_pool_path":     storage.get("libvirt_pool_path", storage.get("image_dir", "/var/lib/libvirt/images")),
            # New: support for specifying the disk device on multi-disk hosts.
            # Comes from the definition file's storage section. Roles can use it
            # to prepare the right disk for the lab (format, mount to mount_point, etc.).
            "libvirt_storage_device": storage.get("device", ""),
            "libvirt_storage_fs_type": storage.get("fs_type", "xfs"),
            "libvirt_storage_mount_point": storage.get("mount_point", storage.get("image_dir", "/var/lib/libvirt/images")),
        }
        # Only override the role-default join token when the plan provides one.
        if creds.get("harvester_token"):
            vars_data["harvester_token"] = creds["harvester_token"]

        # Wire the full vm_nodes (with MACs, UUIDs, interfaces, etc.) from the centralized
        # definition (rodeo/data/profiles/suse-virt/topology.yaml via inventory.py).
        # This makes the definition the source for both Python side and Ansible provisioning
        # for the Harvester/SUSE Virtualization rodeo. Values match the previous role defaults
        # exactly for this explicit definition, so deployment behavior is unchanged.
        # If fields are omitted in the definition, the renderer generates them.
        try:
            from .. import inventory
            inv = inventory.build_inventory(self.cfg)
            vm_nodes = inv.get("vm_nodes")
            if vm_nodes:
                vars_data["vm_nodes"] = vm_nodes
            # host_prep comes from definition via inventory for the Harvester recipe (EIB plan Phase 1).
            # Emitted whole + a few flat keys so kvm_host tasks (sysctls, selinux, libvirt ovmf) and
            # future storage prep can consume without requiring large role refactors yet.
            # Values match the expectations declared in definition.yaml host_prep.
            host_prep = inv.get("host_prep", {})
            if host_prep:
                vars_data["host_prep"] = host_prep
                vars_data["host_prep_sysctls"] = host_prep.get("sysctls", [])
                vars_data["selinux_mode"] = host_prep.get("selinux_mode", "")
                lp = host_prep.get("libvirt", {})
                vars_data["libvirt_ovmf_code"] = lp.get("ovmf_code", "")
                vars_data["libvirt_ovmf_vars_template"] = lp.get("ovmf_vars_template", "")
        except Exception:
            # Fall back to role defaults (the old behavior). The definition load is resilient.
            pass

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
