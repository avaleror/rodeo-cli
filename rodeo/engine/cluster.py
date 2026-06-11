"""ClusterPhase — Python port of deployer/lib/start-vms.sh."""
from __future__ import annotations

import ssl
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Iterator

from .libvirt import LibvirtDriver
from .runner import DeployEvent, LogLine, ProgressUpdate

KUBECONFIG_PATH = Path("/tmp/harvester-kubeconfig")

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
    "-o", "LogLevel=ERROR",
]


class ClusterPhase:
    """Start Harvester + Rancher VMs and wait for the cluster to be ready.

    Replaces deployer/lib/start-vms.sh. Uses LibvirtDriver instead of virsh.
    """

    VIP_TIMEOUT        = 3600   # Harvester install: 20-60 min in nested KVM
    KUBECONFIG_TIMEOUT = 1800   # SSH for rke2.yaml lags a few min after VIP responds
    NODES_TIMEOUT      = 5400   # 3 nodes Ready: up to 90 min
    VIP_POLL           = 30
    SSH_POLL           = 15
    NODES_POLL         = 20
    NODES_LOG_EVERY    = 120    # log node count at most once per 2 min
    ETCD_JOIN_GAP      = 90     # gap between harvester2 and harvester3 prevents etcd join race

    def __init__(self, cfg: dict) -> None:
        self.vip     = cfg["network"]["vip"]
        self.uri     = cfg.get("libvirt", {}).get("uri", "qemu:///system")
        self.ssh_key = Path(cfg.get("ssh", {}).get("identity_file", "/root/.ssh/id_ed25519"))
        self.success = False
        self.error   = ""

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

    def _start_vm(self, lv: LibvirtDriver, name: str) -> Iterator[DeployEvent]:
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

    def _wait_vip(self) -> Iterator[DeployEvent]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            try:
                urllib.request.urlopen(
                    f"https://{self.vip}", timeout=5, context=ctx
                )
                yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
                return True
            except Exception:
                pass

            if elapsed >= self.VIP_TIMEOUT:
                yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
                return False

            yield ProgressUpdate("Waiting for VIP", elapsed, self.VIP_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.VIP_TIMEOUT // 60}:00 — polling {self.vip}...")
            time.sleep(self.VIP_POLL)

    # ---------- Kubeconfig fetch ----------

    def _fetch_kubeconfig(self) -> Iterator[DeployEvent]:
        cmd = [
            "ssh", "-i", str(self.ssh_key),
            *_SSH_OPTS,
            f"rancher@{self.vip}",
            "sudo cat /etc/rancher/rke2/rke2.yaml",
        ]

        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and result.stdout.strip():
                content = result.stdout.replace("127.0.0.1", self.vip)
                KUBECONFIG_PATH.write_text(content)
                KUBECONFIG_PATH.chmod(0o600)
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
            time.sleep(self.SSH_POLL)

    # ---------- Nodes ready ----------

    def _wait_nodes_ready(self) -> Iterator[DeployEvent]:
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

            time.sleep(self.NODES_POLL)

    def _count_ready_nodes(self) -> int:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH),
             "get", "nodes", "--no-headers"],
            capture_output=True, text=True,
        )
        return sum(
            1 for line in result.stdout.splitlines()
            if len(line.split()) >= 2 and line.split()[1] == "Ready"
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
            time.sleep(min(5.0, remaining))

    def _log_vm_states(self, lv: LibvirtDriver) -> Iterator[DeployEvent]:
        try:
            yield LogLine("  VM states:")
            for vm in lv.list_vms():
                yield LogLine(f"    {vm.name:<12}  {vm.state}")
        except Exception:
            pass
