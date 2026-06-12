"""ClusterPhase — Python port of the retired start-vms.sh deployer script."""
from __future__ import annotations

import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator, Iterator

from .libvirt import LibvirtDriver
from .runner import DeployEvent, LogLine, ProgressUpdate

KUBECONFIG_PATH = Path.home() / ".rodeo" / "harvester-kubeconfig"
# Legacy location used by instruqt-virtualization challenge scripts —
# kept as a symlink to the real file.
LEGACY_KUBECONFIG_PATH = Path("/tmp/harvester-kubeconfig")

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
    "-o", "LogLevel=ERROR",
]


class ClusterPhase:
    """Start Harvester + Rancher VMs and wait for the cluster to be ready.

    Replaces the retired start-vms.sh. Uses LibvirtDriver instead of virsh.
    """

    VIP_TIMEOUT        = 3600   # Harvester install: 20-60 min in nested KVM
    KUBECONFIG_TIMEOUT = 1800   # SSH for rke2.yaml lags a few min after VIP responds
    NODES_TIMEOUT      = 5400   # 3 nodes Ready: up to 90 min
    VIP_POLL           = 30
    SSH_POLL           = 15
    NODES_POLL         = 20
    NODES_LOG_EVERY    = 120    # log node count at most once per 2 min
    ETCD_JOIN_GAP      = 90     # gap between harvester2 and harvester3 prevents etcd join race

    def __init__(self, cfg: dict, stop: threading.Event | None = None) -> None:
        self.vip      = cfg["network"]["vip"]
        self.uri      = cfg.get("libvirt", {}).get("uri", "qemu:///system")
        self.ssh_key  = Path(cfg.get("ssh", {}).get("identity_file", "/root/.ssh/id_ed25519"))
        self.vm_names = list(cfg.get("vms", {}).keys())
        self.success  = False
        self.error    = ""
        self._stop    = stop if stop is not None else threading.Event()

    def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake early on cancellation. Returns True if cancelled."""
        if self._stop.wait(seconds):
            self.error = "cancelled"
            return True
        return False

    def stream(self) -> Iterator[DeployEvent]:
        """Yield events. Check self.success after the generator is exhausted."""
        with LibvirtDriver(self.uri) as lv:

            if not (yield from self._start_vm(lv, "harvester1")):
                return

            yield LogLine(f"Waiting for Harvester VIP {self.vip} (20-60 min expected)...")
            if not (yield from self._wait_vip()):
                self.error = f"Timed out after {self.VIP_TIMEOUT // 60} min waiting for VIP"
                return
            yield LogLine("  VIP responding — bootstrap node is up.")

            if not (yield from self._start_vm(lv, "harvester2")):
                return

            yield LogLine(
                f"Waiting {self.ETCD_JOIN_GAP}s before harvester3 "
                "(prevents etcd join race)..."
            )
            yield from self._countdown(self.ETCD_JOIN_GAP, "etcd join gap")
            if self._stop.is_set():
                self.error = "cancelled"
                return

            if not (yield from self._start_vm(lv, "harvester3")):
                return
            if not (yield from self._start_vm(lv, "rancher")):
                return

            yield from self._log_vm_states(lv)

            yield LogLine(f"Fetching kubeconfig from {self.vip} via SSH...")
            if not (yield from self._fetch_kubeconfig()):
                self.error = f"Timed out after {self.KUBECONFIG_TIMEOUT // 60} min fetching kubeconfig"
                return
            yield LogLine(f"  Kubeconfig saved to {KUBECONFIG_PATH} (127.0.0.1 rewritten to VIP).")

            yield LogLine("Waiting for 3 Harvester nodes Ready (up to 90 min)...")
            if not (yield from self._wait_nodes_ready()):
                self.error = f"Timed out after {self.NODES_TIMEOUT // 60} min waiting for nodes"
                return

            yield LogLine("All 3 Harvester nodes Ready. Cluster is up.")
            self.success = True

    # ---------- VM start ----------

    def _start_vm(
        self, lv: LibvirtDriver, name: str
    ) -> Generator[DeployEvent, None, bool]:
        yield LogLine(f"Starting {name}...")
        try:
            info = lv.get_vm(name)
            if info.state == "not found":
                yield LogLine(f"  ✗ {name}: domain not found — was the vms phase completed?")
                self.error = f"{name} not found"
                return False
            if info.state == "running":
                yield LogLine(f"  {name}: already running.")
            else:
                lv.start(name)
                yield LogLine(f"  {name}: started (was {info.state}).")
            return True
        except Exception as exc:
            yield LogLine(f"  ✗ {name}: {exc}")
            self.error = str(exc)
            return False

    # ---------- VIP poll ----------

    def _wait_vip(self) -> Generator[DeployEvent, None, bool]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            up = False
            try:
                urllib.request.urlopen(
                    f"https://{self.vip}", timeout=5, context=ctx
                )
                up = True
            except urllib.error.HTTPError:
                # Any HTTP status means TCP+TLS+HTTP all answered — the VIP is up.
                up = True
            except Exception:
                pass

            if up:
                yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
                return True

            if elapsed >= self.VIP_TIMEOUT:
                yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
                return False

            yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.VIP_TIMEOUT // 60}:00 — polling {self.vip}...")
            if self._sleep(self.VIP_POLL):
                return False

    # ---------- Kubeconfig fetch ----------

    def _fetch_kubeconfig(self) -> Generator[DeployEvent, None, bool]:
        cmd = [
            "ssh", "-i", str(self.ssh_key),
            *_SSH_OPTS,
            f"rancher@{self.vip}",
            "sudo cat /etc/rancher/rke2/rke2.yaml",
        ]

        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                result = subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr="")

            if result.returncode == 0 and result.stdout.strip():
                content = result.stdout.replace("127.0.0.1", self.vip)
                KUBECONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                KUBECONFIG_PATH.write_text(content)
                KUBECONFIG_PATH.chmod(0o600)
                try:
                    LEGACY_KUBECONFIG_PATH.unlink(missing_ok=True)
                    LEGACY_KUBECONFIG_PATH.symlink_to(KUBECONFIG_PATH)
                except OSError:
                    pass  # compat link only — not worth failing the phase
                yield ProgressUpdate("Fetching kubeconfig", elapsed, self.KUBECONFIG_TIMEOUT)
                return True

            if elapsed >= self.KUBECONFIG_TIMEOUT:
                yield ProgressUpdate("Fetching kubeconfig", elapsed, self.KUBECONFIG_TIMEOUT)
                return False

            yield ProgressUpdate("Fetching kubeconfig", elapsed, self.KUBECONFIG_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(
                f"  {m:02d}:{s:02d} / {self.KUBECONFIG_TIMEOUT // 60}:00"
                " — SSH not ready yet..."
            )
            if self._sleep(self.SSH_POLL):
                return False

    # ---------- Nodes ready ----------

    def _wait_nodes_ready(self) -> Generator[DeployEvent, None, bool]:
        t0 = time.monotonic()
        last_log = -self.NODES_LOG_EVERY  # force a log on the first check

        while True:
            elapsed = time.monotonic() - t0
            ready = self._count_ready_nodes()

            yield ProgressUpdate(
                "Waiting for nodes Ready",
                elapsed,
                self.NODES_TIMEOUT,
                detail=f"{ready}/3 nodes Ready",
            )

            if ready >= 3:
                return True

            if elapsed >= self.NODES_TIMEOUT:
                yield LogLine(f"  ✗ Only {ready}/3 nodes Ready after {self.NODES_TIMEOUT // 60} min")
                return False

            if elapsed - last_log >= self.NODES_LOG_EVERY:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(
                    f"  {m:02d}:{s:02d} / {self.NODES_TIMEOUT // 60}:00"
                    f" — {ready}/3 nodes Ready"
                )
                last_log = elapsed

            if self._sleep(self.NODES_POLL):
                return False

    def _count_ready_nodes(self) -> int:
        try:
            result = subprocess.run(
                ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH),
                 "get", "nodes", "--no-headers"],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 0
        # Status column can be "Ready" or "Ready,SchedulingDisabled".
        return sum(
            1 for line in result.stdout.splitlines()
            if len(line.split()) >= 2 and line.split()[1].split(",")[0] == "Ready"
        )

    # ---------- Helpers ----------

    def _countdown(self, seconds: int, label: str) -> Iterator[DeployEvent]:
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            remaining = seconds - elapsed
            if remaining <= 0:
                yield ProgressUpdate(label, float(seconds), float(seconds))
                return
            yield ProgressUpdate(label, elapsed, float(seconds))
            if self._sleep(min(5.0, remaining)):
                return

    def _log_vm_states(self, lv: LibvirtDriver) -> Iterator[DeployEvent]:
        try:
            yield LogLine("  VM states:")
            for vm in lv.list_vms(self.vm_names):
                yield LogLine(f"    {vm.name:<12}  {vm.state}")
        except Exception:
            pass
