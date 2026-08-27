"""RancherPhase — Rancher/K3s deploy orchestration, one concern per module.

Split from the original monolithic rancher.py; the class composition below
preserves the single public surface, so `from rodeo.engine.rancher import
RancherPhase` works exactly as before. Concerns:

  remote.py         SSH/HTTP primitives (RemoteExecMixin)
  cluster_setup.py  K3s + Helm + cert-manager + Rancher install + API config
  harvester.py      Harvester import, CA fixes, dashboard password, CDROM eject
  elemental.py      Elemental operator + MachineRegistrations
  extensions.py     Rancher UI extension repos + declarative reconcile
  hauler.py         Hauler store population (airgap artifacts)
  content.py        Lab content seeding (Gitea + demo-app Fleet repo)
  summary.py        env file + completion banner
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterator

# Re-exported stdlib modules: tests and tooling patch these through this module
# (e.g. rancher.time.monotonic, rancher.subprocess.run, rancher.urllib.request.urlopen).
import hashlib  # noqa: F401
import json  # noqa: F401
import shlex  # noqa: F401
import ssl  # noqa: F401
import subprocess  # noqa: F401
import time  # noqa: F401
import urllib.request  # noqa: F401

import yaml  # noqa: F401

from ..runner import DeployEvent, LogLine
from .cluster_setup import ClusterSetupMixin
from .content import LabContentMixin
from .elemental import ElementalMixin
from .extensions import UiExtensionsMixin
from .harvester import HarvesterMixin
from .hauler import HaulerMixin
from .remote import RemoteExecMixin
from .summary import SummaryMixin

__all__ = ["RancherPhase"]


class RancherPhase(
    RemoteExecMixin,
    ClusterSetupMixin,
    HarvesterMixin,
    ElementalMixin,
    UiExtensionsMixin,
    HaulerMixin,
    LabContentMixin,
    SummaryMixin,
):
    """Install K3s + Rancher Prime on the rancher VM and import the Harvester cluster."""

    SSH_TIMEOUT     = 300    # wait for rancher VM SSH (5 min)
    K3S_TIMEOUT     = 600    # K3s node Ready (10 min)
    PING_TIMEOUT    = 600    # Rancher /ping (10 min)
    LOGIN_TIMEOUT   = 600    # Rancher auth API ready after /ping (10 min)
    CLUSTER_TIMEOUT = 1800   # cluster Active in Rancher (30 min)
    HARVESTER_PW_TIMEOUT = 300   # Harvester dashboard API ready for password set (5 min)

    SSH_POLL     = 10
    K3S_POLL     = 10
    PING_POLL    = 10
    LOGIN_POLL   = 10
    CLUSTER_POLL = 30
    HARVESTER_PW_POLL = 10

    # Last password each side actually accepted. Read as an extra login candidate
    # (so a redeploy after secrets.yaml is regenerated can still authenticate with
    # the previous password to set the new one) and rewritten on every successful
    # change so it always reflects live state.
    RANCHER_PW_FILE   = Path("/root/rancher-password")
    HARVESTER_PW_FILE = Path("/root/harvester-password")

    def __init__(self, cfg: dict, stop: threading.Event | None = None) -> None:
        net  = cfg["network"]
        ver  = cfg.get("versions", {})
        cred = cfg.get("credentials", {})

        self.rancher_ip       = net.get("rancher_ip", "192.168.122.9")
        self.vip              = net.get("vip", "")   # empty for profiles without Harvester
        self.nodeport         = int(net.get("rancher_nodeport", 30002))
        self.dns_domain       = net.get("dns_domain", "rodeo.lab")
        self.gateway          = net.get("gateway", "192.168.122.1")

        # Standalone = a Rancher-only lab (no Harvester cluster to manage).
        # Use harvester_node_names from the definition when present — it's the
        # authoritative list. Fall back to "everything that isn't rancher" for
        # profiles that predate the definition file (e.g. old suse-virt plans).
        _harvester_names = cfg.get("harvester_node_names")
        if _harvester_names is not None:
            real_harvester = [n for n in cfg.get("vms", {}) if n in set(_harvester_names)]
        else:
            real_harvester = [n for n in cfg.get("vms", {}) if n != "rancher"]
        self.standalone       = bool(cfg.get("vms")) and not real_harvester
        self.harvester_nodes  = real_harvester or ["harvester1", "harvester2", "harvester3"]
        self.libvirt_uri      = cfg.get("libvirt", {}).get("uri", "qemu:///system")

        self.rancher_version         = ver.get("rancher", "2.14.1")
        self.k3s_version             = ver.get("k3s", "v1.35.3+k3s1")
        self.cert_mgr_version        = ver.get("cert_manager", "v1.20.1")
        self.elemental_crds_version  = ver.get("elemental_operator_crds", "1.9.0")
        self.elemental_op_version    = ver.get("elemental_operator", "1.9.0")

        # Rancher Prime UI extensions to reconcile after import (declarative, from
        # the definition's rancher.ui_extensions). Empty for profiles that declare none.
        self.ui_extensions = cfg.get("rancher_ui_extensions", []) or []

        self.profile_type = cfg.get("type", "")
        # Default OFF: the rodeo/workshop model is that students import Harvester
        # into Rancher themselves as a lab exercise. A plan opts in explicitly
        # with `harvester_auto_import: true` when a fully-wired cluster is wanted.
        self.harvester_auto_import = cfg.get("harvester_auto_import", False)

        eib_vm = cfg.get("vms", {}).get("eib", {})
        self.eib_ip      = eib_vm.get("ip", "192.168.122.20")

        # Edge nodes — derived from the definition, never hardcoded. Names come
        # from edge_node_names (authoritative) or any VM whose name starts with
        # "edge". IP, network prefix and gateway all flow through from the
        # definition so changing the CIDR, gateway or a node's IP needs no code
        # change here — the EIB network-configs regenerate to match.
        _edge_names = cfg.get("edge_node_names") or [
            n for n in cfg.get("vms", {}) if n.startswith("edge")
        ]
        self.edge_nodes = [
            {"name": n, "ip": cfg.get("vms", {}).get(n, {}).get("ip", "")}
            for n in _edge_names
        ]
        try:
            self.net_prefix = int(str(net.get("cidr", "192.168.122.0/24")).split("/")[1])
        except (ValueError, IndexError):
            self.net_prefix = 24
        # DNS resolver for edge nodes; falls back to the gateway (dnsmasq on the
        # libvirt network answers there) when the definition doesn't set one.
        self.dns_server = net.get("dns_server", self.gateway)
        self.image_dir   = cfg.get("storage", {}).get("image_dir", "/var/lib/libvirt/images")
        eib_def          = cfg.get("eib", {})
        self.eib_image   = eib_def.get("container_image", "registry.suse.com/edge/3.6/edge-image-builder:1.3.3.1")
        self.hauler_version = cfg.get("versions", {}).get("hauler", "1.2.2")
        # download.suse.com's SL Micro path is stale (redirects to a marketing page,
        # not the file — confirmed live: hauler happily "added" the 302 redirect
        # chain's tiny HTML body as if it were the real multi-GB image, only
        # failing checksum verification later when actually served). Using
        # openSUSE Leap Micro 6.2 instead — freely downloadable, no SCC/registration
        # gate, confirmed live via HEAD request (real Content-Length, not a redirect
        # to a webpage).
        _leap_micro_iso_default = "https://download.opensuse.org/distribution/leap-micro/6.2/appliances/iso/openSUSE-Leap-Micro.x86_64-Default-SelfInstall.iso"
        _leap_micro_raw_default = "https://download.opensuse.org/distribution/leap-micro/6.2/appliances/openSUSE-Leap-Micro.x86_64-Default.raw.xz"
        self.leap_micro_iso_url = eib_def.get("leap_micro_iso_url", _leap_micro_iso_default)
        self.leap_micro_raw_url = eib_def.get("leap_micro_raw_url", _leap_micro_raw_default)

        el_cfg = cfg.get("elemental", {})
        _plan_name = cfg.get("name", "suse-edge").lower().replace("_", "-")
        self.elemental_reg_count    = int(el_cfg.get("registrations", 1))
        self.elemental_reg_prefix   = el_cfg.get("registration_prefix") or _plan_name

        # TLS mode: 'rancher' = Rancher self-signed cert + NodePort (default)
        #           'letsEncrypt' = Let's Encrypt cert via Traefik ingress + sslip.io hostname
        tls_cfg = cfg.get("rancher_tls", {})
        self.tls_source        = tls_cfg.get("source", "rancher")
        self.letsencrypt_email = tls_cfg.get("email", "admin@example.com")

        key = cfg.get("ssh", {}).get("identity_file")
        if not key:
            key = "/root/.ssh/id_ed25519" if os.geteuid() == 0 else str(Path.home() / ".ssh" / "id_ed25519")
        self.ssh_key = Path(key)
        # Prefer the explicit per-service keys; fall back to lab_admin_password for
        # older secrets files that predate the split.
        _fallback = cred.get("lab_admin_password", cred.get("harvester_os_password", ""))
        self.rancher_password   = cred.get("rancher_admin_password", _fallback)
        self.harvester_password = cred.get("harvester_admin_password", _fallback)
        self.admin_password     = self.rancher_password  # alias used by _configure_api

        gitea_cfg = cfg.get("gitea", {})
        self.gitea_port     = int(gitea_cfg.get("port", 3000))
        self.gitea_user     = gitea_cfg.get("admin_user", "gitea")
        self.gitea_version  = cfg.get("versions", {}).get("gitea", "1.22")
        self.gitea_password = cred.get("gitea_admin_password", "gitea-lab")
        _ag = cfg.get("alien_geeko", {})
        self.alien_geeko_fleet_repo = _ag.get(
            "fleet_repo", "https://github.com/SUSE-Technical-Marketing/Alien-Geeko.git"
        )
        self.alien_geeko_image = _ag.get("image", "docker.io/avaleror/alien-geeko:latest")
        self.alien_geeko_fleet_name = _ag.get("fleet_name", "alien-geeko")
        self.alien_geeko_fleet_namespace = _ag.get("fleet_namespace", "fleet-default")
        self.alien_geeko_target_labels = _ag.get(
            "target_labels", {"demo": "true", "edge-type": "x86-cluster"}
        )

        # For letsEncrypt mode, rancher_hostname and rancher_api are updated at
        # install time once the external IP is known (_update_sslip_hostname).
        self.rancher_hostname = f"rancher.{self.rancher_ip.replace('.', '-')}.sslip.io"
        if self.tls_source == "letsEncrypt":
            self.rancher_api = f"https://{self.rancher_hostname}"
            # rancher_server_url == rancher_api for letsEncrypt (both use the public hostname)
            self.rancher_server_url = self.rancher_api
        else:
            # rancher_api uses the raw VM IP — no DNS required; _ssl_ctx() already skips
            # cert verification for rodeo-cli's own API calls.
            self.rancher_api = f"https://{self.rancher_ip}:{self.nodeport}"
            # rancher_server_url uses the sslip.io hostname — this is what cattle-cluster-agent
            # reads from Rancher's server-url setting. The cert SAN is the sslip.io hostname,
            # not the raw IP, so the agent's TLS hostname verification passes.
            self.rancher_server_url = f"https://{self.rancher_hostname}:{self.nodeport}"

        self.success      = False
        self.setup_done   = False  # True after K3s+Helm+Rancher ping complete
        self.error        = ""
        # _set_harvester_password() is intentionally non-fatal for the deploy
        # pipeline (self.error/self.success are untouched on failure — cluster
        # import still counts as a success). This flag lets standalone callers
        # (e.g. the set-password command) still detect and report a failure.
        self.harvester_password_error = ""
        self._api_token   = ""
        self._cluster_id  = ""
        self._stop        = stop if stop is not None else threading.Event()

    def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake early on cancellation. Returns True if cancelled."""
        if self._stop.wait(seconds):
            self.error = "cancelled"
            return True
        return False

    @staticmethod
    def _read_persisted_password(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def stream(self) -> Iterator[DeployEvent]:
        """Yield events. Check self.success after exhaustion."""
        yield from self.stream_setup()
        if not self.setup_done:
            return
        yield from self.stream_import()

    def stream_setup(self) -> Iterator[DeployEvent]:
        """K3s + Helm + cert-manager + Rancher Prime + API config.

        Can run concurrently with the Harvester node-ready wait in ClusterPhase
        because it only touches the Rancher VM — no Harvester dependency.
        Sets self.setup_done = True on success.
        """
        yield LogLine(f"Waiting for rancher VM SSH at {self.rancher_ip}...")
        if not (yield from self._wait_ssh()):
            self.error = f"SSH not reachable after {self.SSH_TIMEOUT // 60} min"
            return
        yield LogLine("  SSH is up.")

        yield LogLine(f"Installing K3s {self.k3s_version}...")
        if not (yield from self._install_k3s()):
            return
        yield LogLine("  K3s installed.")

        yield LogLine("Waiting for K3s node Ready...")
        if not (yield from self._wait_k3s_ready()):
            self.error = "K3s node never became Ready"
            return
        yield LogLine("  K3s node Ready.")

        yield LogLine("Installing Helm...")
        if not (yield from self._install_helm()):
            return
        yield LogLine("  Helm installed.")

        yield LogLine(f"Installing cert-manager {self.cert_mgr_version}...")
        if not (yield from self._install_cert_manager()):
            return
        yield LogLine("  cert-manager installed.")

        if self.tls_source == "letsEncrypt":
            yield LogLine("Detecting external IP for sslip.io hostname...")
            self._update_sslip_hostname()
            yield LogLine(f"  Rancher hostname: {self.rancher_hostname}")

        yield LogLine(f"Installing Rancher Prime {self.rancher_version} (may take 10+ min)...")
        if not (yield from self._install_rancher()):
            return
        yield LogLine("  Rancher Prime installed.")

        if self.tls_source != "letsEncrypt":
            yield LogLine(f"Exposing Rancher on NodePort {self.nodeport}...")
            if not (yield from self._expose_nodeport()):
                return
            yield LogLine("  NodePort configured.")

        yield LogLine(f"Waiting for Rancher /ping on {self.rancher_api}...")
        if not (yield from self._wait_ping()):
            self.error = f"Rancher did not respond after {self.PING_TIMEOUT // 60} min"
            return
        yield LogLine("  Rancher is up.")

        yield LogLine("Configuring Rancher admin password and server-url...")
        if not (yield from self._configure_api()):
            return
        yield LogLine("  Rancher API configured.")

        self.setup_done = True

    def stream_import(self) -> Iterator[DeployEvent]:
        """UI Extension + Harvester cluster import + password + CDROM eject.

        Requires all Harvester nodes Ready and the Harvester kubeconfig to exist.
        Requires setup_done (self._api_token must be set by stream_setup).
        """
        if self.standalone:
            yield LogLine(
                f"\n  Rancher URL  : {self.rancher_api}  (NodePort)"
                "\n  Standalone Rancher lab — no Harvester cluster to import."
            )
            # Reconcile declared Rancher UI extensions (e.g. suse-edge's OS Manager /
            # Elemental extension). Non-fatal: warnings only, never fails the phase.
            # Standalone labs (rancher, suse-edge) never reach the non-standalone
            # branch below, so this must be handled here too, not just once.
            if self.ui_extensions:
                if not (yield from self._reconcile_ui_extensions()):
                    return
            self.success = True
            return

        if self.harvester_auto_import:
            yield LogLine("Importing Harvester cluster into Rancher...")
            if not (yield from self._import_harvester()):
                return
            yield LogLine("  Harvester cluster import started.")
        else:
            yield LogLine("  Skipping auto-import — students will import Harvester into Rancher manually.")

        # Independent of auto-import: the Harvester dashboard itself is always up
        # once nodes are Ready, so always move it off the admin/admin bootstrap
        # and onto the secrets.yaml password — otherwise the success screen shows
        # a password that was never actually applied.
        yield LogLine("Setting Harvester dashboard admin password...")
        yield from self._set_harvester_password()

        yield LogLine("Ejecting installer ISOs from Harvester VMs...")
        yield from self._eject_cdroms()

        # Reconcile declared Rancher UI extensions (e.g. the Harvester extension) to
        # their pinned versions. Non-fatal: warnings only, never fails the phase.
        if self.ui_extensions:
            if not (yield from self._reconcile_ui_extensions()):
                return

        yield LogLine(
            f"\n  Rancher URL  : {self.rancher_api}  (NodePort)"
            f"\n  Cluster ID   : {self._cluster_id}"
        )
        self.success = True
