"""ClusterPhase — Python port of the retired start-vms.sh deployer script."""
from __future__ import annotations

import os
import queue
import re
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
from ..paths import harvester_kubeconfig_path, rodeo_logs_dir
from ..ssh import ssh_opts


def _kubeconfig_path() -> Path:
    return harvester_kubeconfig_path()


# Legacy location used by instruqt-virtualization challenge scripts —
# kept as a symlink to the real file.
LEGACY_KUBECONFIG_PATH = Path("/tmp/harvester-kubeconfig")


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
        self.cfg      = cfg
        self.vip      = cfg["network"]["vip"]
        self.uri      = cfg.get("libvirt", {}).get("uri", "qemu:///system")
        key = cfg.get("ssh", {}).get("identity_file")
        if not key:
            key = "/root/.ssh/id_ed25519" if os.geteuid() == 0 else str(Path.home() / ".ssh" / "id_ed25519")
        self.ssh_key = Path(key)
        self.vm_names = list(cfg.get("vms", {}).keys())
        vms_cfg = cfg.get("vms", {})
        # Topology comes from the definition (via inventory), not hardcoded names:
        # start_order, which nodes are Harvester, how many must be Ready, and the
        # etcd join gap. Falls back to deriving from cfg["vms"] if inventory fails.
        self._load_topology()
        # Derive harvester SSH user from config (all harvester nodes share it).
        # Used for kubeconfig fetch over VIP. Defaults to "rancher" for Harvester OS.
        harvester_vm = self.harvester_nodes[0] if self.harvester_nodes else (self.vm_names[0] if self.vm_names else None)
        self.harvester_user = vms_cfg.get(harvester_vm, {}).get("user", "rancher") if harvester_vm else "rancher"
        self.success  = False
        self.error    = ""
        self._stop    = stop if stop is not None else threading.Event()
        self._background_rancher = None  # set if rancher setup runs during cluster wait

    def _load_topology(self) -> None:
        """Resolve start order, Harvester node set, Ready count, and etcd gap.

        Prefers the rendered inventory (definition.yaml) so 2-node, 3-node, or any
        topology works without hardcoded names. Falls back to cfg["vms"] ordering.
        """
        self.start_order = list(self.vm_names)
        self.harvester_nodes = [n for n in self.vm_names if n != "rancher"]
        self.ready_count = len(self.harvester_nodes) or 3
        self.etcd_gap = self.ETCD_JOIN_GAP
        try:
            from .. import inventory
            inv = inventory.build_inventory(self.cfg)
            if inv.get("start_order"):
                self.start_order = list(inv["start_order"])
            if inv.get("harvester_node_names"):
                self.harvester_nodes = list(inv["harvester_node_names"])
            if inv.get("harvester_ready_count"):
                self.ready_count = int(inv["harvester_ready_count"])
            if inv.get("etcd_join_gap_seconds") is not None:
                self.etcd_gap = int(inv["etcd_join_gap_seconds"])
        except Exception:
            pass  # keep the cfg-derived fallback

    def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake early on cancellation. Returns True if cancelled."""
        if self._stop.wait(seconds):
            self.error = "cancelled"
            return True
        return False

    def stream(self) -> Iterator[DeployEvent]:
        """Yield events. Check self.success after the generator is exhausted."""
        with LibvirtDriver(self.uri) as lv:
            yield LogLine("Ensuring libvirt default network (virbr0) is up...")
            try:
                lv.net_start("default")
                lv.net_set_autostart("default", True)
                yield LogLine("  virbr0 active — iPXE dnsmasq ready.")
            except Exception as exc:
                self.error = f"default network: {exc}"
                yield LogLine(f"  ✗  {self.error}")
                return

            order = self.start_order or self.vm_names
            if not order:
                self.error = "no VMs in topology"
                yield LogLine(f"  ✗  {self.error}")
                return
            harvester_set = set(self.harvester_nodes)

            # Bootstrap node first (first in start_order — a Harvester node).
            bootstrap = order[0]
            if not (yield from self._start_vm(lv, bootstrap)):
                return

            yield LogLine(f"Waiting for Harvester VIP {self.vip} (20-60 min expected)...")
            if not (yield from self._wait_vip()):
                self.error = f"Timed out after {self.VIP_TIMEOUT // 60} min waiting for VIP"
                return
            yield LogLine("  VIP responding — bootstrap node is up.")

            # Remaining nodes in order. Insert the etcd join gap before each
            # additional Harvester join node (not the first join, not non-Harvester
            # nodes like rancher) — same race-avoidance as the old h2→90s→h3 flow,
            # generalised to any node count.
            joins_started = 0
            for name in order[1:]:
                if name in harvester_set:
                    if joins_started >= 1 and self.etcd_gap > 0:
                        yield LogLine(
                            f"Waiting {self.etcd_gap}s before {name} (prevents etcd join race)..."
                        )
                        yield from self._countdown(self.etcd_gap, "etcd join gap")
                        if self._stop.is_set():
                            self.error = "cancelled"
                            return
                    if not (yield from self._start_vm(lv, name)):
                        return
                    joins_started += 1
                else:
                    if not (yield from self._start_vm(lv, name)):
                        return

            yield from self._log_vm_states(lv)

            yield LogLine(f"Fetching kubeconfig from {self.vip} via SSH...")
            if not (yield from self._fetch_kubeconfig()):
                self.error = f"Timed out after {self.KUBECONFIG_TIMEOUT // 60} min fetching kubeconfig"
                return
            yield LogLine(f"  Kubeconfig saved to {_kubeconfig_path()} (127.0.0.1 rewritten to VIP).")

            # If the topology has a Rancher VM, start K3s + Helm + Rancher Prime
            # in a background thread while we wait for all Harvester nodes Ready.
            # The Rancher VM boots from a cloud image and is reachable by SSH well
            # before the Harvester nodes finish their iPXE install, so the two
            # waits overlap naturally. Only the Harvester import (which writes the
            # cluster-registration-url to Harvester nodes) must happen after nodes Ready.
            drain: queue.Queue | None = None
            if "rancher" in self.vm_names:
                from .rancher import RancherPhase
                rp = RancherPhase(self.cfg, stop=self._stop)
                self._background_rancher = rp
                drain = queue.Queue()

                def _run_setup(rp: RancherPhase = rp, q: queue.Queue = drain) -> None:
                    try:
                        for ev in rp.stream_setup():
                            q.put(ev)
                    except Exception as exc:
                        q.put(LogLine(f"  ✗ rancher setup: {exc}"))
                    finally:
                        q.put(None)  # sentinel

                threading.Thread(target=_run_setup, daemon=True).start()
                yield LogLine("Rancher K3s + Helm setup starting in background...")

            yield LogLine(f"Waiting for {self.ready_count} Harvester nodes Ready (up to 90 min)...")
            if not (yield from self._wait_nodes_ready(drain=drain)):
                self.error = f"Timed out after {self.NODES_TIMEOUT // 60} min waiting for nodes"
                return

            # Drain any remaining rancher-setup events and wait for it to finish.
            # _wait_nodes_ready drains events non-blocking while polling, including
            # the None sentinel. If setup finished during the node wait, setup_done
            # is True and the queue is already empty — skip the blocking loop.
            RANCHER_DRAIN_TIMEOUT = 20 * 60  # 20 min max after nodes Ready
            if drain is not None and self._background_rancher is not None:
                if self._background_rancher.setup_done:
                    yield LogLine("Rancher setup completed during Harvester node install.")
                else:
                    yield LogLine("All Harvester nodes Ready — waiting for Rancher setup to finish...")
                    drain_deadline = time.monotonic() + RANCHER_DRAIN_TIMEOUT
                    while True:
                        remaining = drain_deadline - time.monotonic()
                        if remaining <= 0:
                            yield LogLine(
                                "  ⚠ Rancher background setup did not finish within "
                                f"{RANCHER_DRAIN_TIMEOUT // 60} min after nodes Ready "
                                "— will retry in the rancher phase."
                            )
                            self._background_rancher = None
                            break
                        try:
                            ev = drain.get(timeout=min(30.0, remaining))
                        except queue.Empty:
                            if self._stop.is_set():
                                self.error = "cancelled"
                                return
                            yield LogLine("  Still waiting for Rancher K3s/Helm setup...")
                            continue
                        if ev is None:
                            break
                        yield ev

                    if self._background_rancher is not None and not self._background_rancher.setup_done:
                        yield LogLine(
                            f"  ⚠ Rancher setup failed: {self._background_rancher.error} "
                            "— will retry in the rancher phase."
                        )
                        self._background_rancher = None

            yield LogLine(f"All {self.ready_count} Harvester nodes Ready. Cluster is up.")
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
        _last_vm_log = 0.0
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

            # Log VM states every 5 min so a stuck install is detectable from
            # the TUI or log file without needing to reconnect to a console.
            if elapsed - _last_vm_log >= 300:
                yield from self._vm_states_snapshot("waiting for VIP", elapsed, self.VIP_TIMEOUT)
                _last_vm_log = elapsed

            if self._sleep(self.VIP_POLL):
                return False

    # ---------- Kubeconfig fetch ----------

    def _fetch_kubeconfig(self) -> Generator[DeployEvent, None, bool]:
        cmd = [
            "ssh", "-i", str(self.ssh_key),
            *ssh_opts(),
            f"{self.harvester_user}@{self.vip}",
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
                # Replace whatever IP is in the server field (127.0.0.1, a node IP, etc.)
                # with the VIP so all kubectl ops always go through the cluster VIP.
                content = re.sub(
                    r'(server: https://)[^\s:]+',
                    rf'\g<1>{self.vip}',
                    result.stdout,
                )
                kubeconfig = _kubeconfig_path()
                kubeconfig.parent.mkdir(parents=True, exist_ok=True)
                kubeconfig.write_text(content)
                kubeconfig.chmod(0o600)
                try:
                    LEGACY_KUBECONFIG_PATH.unlink(missing_ok=True)
                    LEGACY_KUBECONFIG_PATH.symlink_to(kubeconfig)
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

    def _wait_nodes_ready(
        self, drain: "queue.Queue | None" = None
    ) -> Generator[DeployEvent, None, bool]:
        t0 = time.monotonic()
        last_log = -self.NODES_LOG_EVERY  # force a log on the first check
        drain_done = drain is None

        while True:
            elapsed = time.monotonic() - t0
            ready = self._count_ready_nodes()

            yield ProgressUpdate(
                "Waiting for nodes Ready",
                elapsed,
                self.NODES_TIMEOUT,
                detail=f"{ready}/{self.ready_count} nodes Ready",
            )

            if ready >= self.ready_count:
                return True

            if elapsed >= self.NODES_TIMEOUT:
                yield LogLine(f"  ✗ Only {ready}/{self.ready_count} nodes Ready after {self.NODES_TIMEOUT // 60} min")
                return False

            if elapsed - last_log >= self.NODES_LOG_EVERY:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(
                    f"  {m:02d}:{s:02d} / {self.NODES_TIMEOUT // 60}:00"
                    f" — {ready}/{self.ready_count} nodes Ready"
                )
                yield from self._vm_states_snapshot("waiting for nodes Ready", elapsed, self.NODES_TIMEOUT)
                last_log = elapsed

            # Drain background rancher-setup events without blocking.
            # Events are delayed by at most NODES_POLL seconds — acceptable for logs.
            if not drain_done and drain is not None:
                while True:
                    try:
                        ev = drain.get_nowait()
                    except queue.Empty:
                        break
                    if ev is None:
                        drain_done = True
                        break
                    yield ev

            if self._sleep(self.NODES_POLL):
                return False

    def _count_ready_nodes(self) -> int:
        try:
            result = subprocess.run(
                ["kubectl", "--kubeconfig", str(_kubeconfig_path()),
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

    def _vm_states_snapshot(self, phase: str, elapsed: float, total: float) -> Iterator[LogLine]:
        """Log VM states via virsh and write a heartbeat file.

        The heartbeat file lets someone who reconnects to a live lab see at a
        glance whether VMs are still running and how far the install had got,
        distinguishing a stuck install from an Instruqt timeout (which destroys
        the lab entirely — reconnect is impossible if the lab is gone).
        """
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        m, s = divmod(int(elapsed), 60)
        tm, ts = divmod(int(total), 60)
        header = f"{now}  {phase}  {m:02d}:{s:02d} / {tm:02d}:{ts:02d}"
        yield LogLine(f"  [{now}] VM states:")
        lines = [header, "  VM states:"]
        for name in self.vm_names:
            try:
                r = subprocess.run(
                    ["virsh", "domstate", name],
                    capture_output=True, text=True, timeout=5,
                )
                state = r.stdout.strip() or "unknown"
            except Exception:
                state = "unknown"
            yield LogLine(f"    {name:<14}  {state}")
            lines.append(f"    {name:<14}  {state}")

        lab_name = self.cfg.get("name", "rodeo")
        heartbeat = rodeo_logs_dir() / f"{lab_name}-heartbeat.txt"
        try:
            heartbeat.parent.mkdir(parents=True, exist_ok=True)
            heartbeat.write_text("\n".join(lines) + "\n")
        except Exception:
            pass

    def _log_vm_states(self, lv: LibvirtDriver) -> Iterator[DeployEvent]:
        try:
            yield LogLine("  VM states:")
            for vm in lv.list_vms(self.vm_names):
                yield LogLine(f"    {vm.name:<12}  {vm.state}")
        except Exception:
            pass
