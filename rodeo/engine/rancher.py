"""RancherPhase — Python port of the retired setup-rancher.sh deployer script."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import ssl
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Generator, Iterator

import yaml

from ..paths import harvester_kubeconfig_path
from .runner import DeployEvent, LogLine, ProgressUpdate
from ..ssh import ssh_opts


class RancherPhase:
    """Install K3s + Rancher Prime on the rancher VM and import the Harvester cluster."""

    SSH_TIMEOUT     = 300    # wait for rancher VM SSH (5 min)
    K3S_TIMEOUT     = 600    # K3s node Ready (10 min)
    PING_TIMEOUT    = 600    # Rancher /ping (10 min)
    LOGIN_TIMEOUT   = 600    # Rancher auth API ready after /ping (10 min)
    CLUSTER_TIMEOUT = 1800   # cluster Active in Rancher (30 min)

    SSH_POLL     = 10
    K3S_POLL     = 10
    PING_POLL    = 10
    LOGIN_POLL   = 10
    CLUSTER_POLL = 30

    def __init__(self, cfg: dict, stop: threading.Event | None = None) -> None:
        net  = cfg["network"]
        ver  = cfg.get("versions", {})
        cred = cfg.get("credentials", {})

        self.rancher_ip       = net.get("rancher_ip", "192.168.122.9")
        self.vip              = net.get("vip", "")   # empty for profiles without Harvester
        self.nodeport         = int(net.get("rancher_nodeport", 30002))
        self.dns_domain       = net.get("dns_domain", "aerogrid.com")
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
        self._api_token   = ""
        self._cluster_id  = ""
        self._stop        = stop if stop is not None else threading.Event()

    def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake early on cancellation. Returns True if cancelled."""
        if self._stop.wait(seconds):
            self.error = "cancelled"
            return True
        return False

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
            self.success = True
            return

        if self.harvester_auto_import:
            yield LogLine("Importing Harvester cluster into Rancher...")
            if not (yield from self._import_harvester()):
                return
            yield LogLine("  Harvester cluster import started.")

            yield LogLine("Setting Harvester dashboard admin password...")
            yield from self._set_harvester_password()
        else:
            yield LogLine("  Skipping auto-import — students will import Harvester into Rancher manually.")

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

    # ---------- SSH helpers ----------

    @staticmethod
    def _run(cmd: list[str], timeout: int, input: str | None = None) -> subprocess.CompletedProcess:
        """subprocess.run that converts timeouts/launch errors into a failed result."""
        try:
            return subprocess.run(
                cmd, input=input, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                cmd, returncode=124, stdout="", stderr=f"timed out after {timeout}s"
            )
        except OSError as exc:
            return subprocess.CompletedProcess(
                cmd, returncode=127, stdout="", stderr=str(exc)
            )

    def _ssh_run(self, *remote_cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return self._run(
            ["ssh", "-i", str(self.ssh_key), *ssh_opts(), f"root@{self.rancher_ip}", *remote_cmd],
            timeout=timeout,
        )

    def _ssh_script(self, script: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self._run(
            ["ssh", "-i", str(self.ssh_key), *ssh_opts(), f"root@{self.rancher_ip}", "bash", "-s"],
            timeout=timeout, input=script,
        )

    def _eib_ssh_script(self, script: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self._run(
            ["ssh", "-i", str(self.ssh_key), *ssh_opts(), f"root@{self.eib_ip}", "bash", "-s"],
            timeout=timeout, input=script,
        )

    # ---------- HTTP helpers ----------

    def _ssl_ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _detect_external_ip(self) -> str:
        """Return the external IP visible to the Rancher VM (= host's NAT egress IP)."""
        script = (
            "curl -sf --max-time 10 https://api.ipify.org"
            " || curl -sf --max-time 10 https://ifconfig.me"
            " || echo ''"
        )
        r = self._ssh_script(script, timeout=20)
        ip = r.stdout.strip()
        if ip and r.returncode == 0:
            return ip
        return self.rancher_ip  # fallback: use internal IP (no internet access)

    def _update_sslip_hostname(self) -> None:
        """Detect external IP and update rancher_hostname + rancher_api for letsEncrypt mode."""
        ext_ip = self._detect_external_ip()
        dashed = ext_ip.replace(".", "-")
        self.rancher_hostname = f"rancher.{dashed}.sslip.io"
        self.rancher_api = f"https://{self.rancher_hostname}"
        self.rancher_server_url = self.rancher_api

    def _http(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        token: str = "",
    ) -> dict:
        url = f"{self.rancher_api}{path}"
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, context=self._ssl_ctx(), timeout=30) as resp:
            raw = resp.read()
        # Some actions (e.g. changepassword) return 200 with an empty body.
        # Treat empty/non-JSON success responses as {} rather than raising.
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ---------- Phase sub-steps ----------

    def _wait_ssh(self) -> Generator[DeployEvent, None, bool]:
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            try:
                r = self._ssh_run("echo ok", timeout=15)
                if r.returncode == 0:
                    yield ProgressUpdate("Waiting for SSH", elapsed, self.SSH_TIMEOUT)
                    return True
            except Exception:
                pass

            if elapsed >= self.SSH_TIMEOUT:
                yield ProgressUpdate("Waiting for SSH", elapsed, self.SSH_TIMEOUT)
                return False

            yield ProgressUpdate("Waiting for SSH", elapsed, self.SSH_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.SSH_TIMEOUT // 60}:00 — SSH not ready yet...")
            if self._sleep(self.SSH_POLL):
                return False

    def _install_k3s(self) -> Generator[DeployEvent, None, bool]:
        # letsEncrypt uses Traefik ingress for HTTP01 ACME + TLS termination.
        # All other TLS sources (secret, self-signed) expose Rancher via NodePort
        # and don't need Traefik — disable it to keep the footprint small.
        disable_traefik = "" if self.tls_source == "letsEncrypt" else " --disable traefik"
        script = (
            "set -euo pipefail\n"
            f'export INSTALL_K3S_VERSION="{self.k3s_version}"\n'
            "curl -sfL https://get.k3s.io"
            f" | sh -s - --write-kubeconfig-mode 644{disable_traefik} --node-name rancher\n"
        )
        yield LogLine("  Running K3s installer (1-3 min)...")
        r = self._ssh_script(script, timeout=300)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "K3s install failed"
            return False
        return True

    def _wait_k3s_ready(self) -> Generator[DeployEvent, None, bool]:
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "kubectl get nodes --no-headers 2>/dev/null | awk '{print $2}' | head -1\n"
        )
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            try:
                r = self._ssh_script(script, timeout=30)
                if r.returncode == 0 and r.stdout.strip() == "Ready":
                    yield ProgressUpdate("K3s node Ready", elapsed, self.K3S_TIMEOUT)
                    return True
            except Exception:
                pass

            if elapsed >= self.K3S_TIMEOUT:
                yield ProgressUpdate("K3s node Ready", elapsed, self.K3S_TIMEOUT)
                return False

            yield ProgressUpdate("K3s node Ready", elapsed, self.K3S_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.K3S_TIMEOUT // 60}:00 — waiting for K3s node...")
            if self._sleep(self.K3S_POLL):
                return False

    def _install_helm(self) -> Generator[DeployEvent, None, bool]:
        script = (
            "set -euo pipefail\n"
            "curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash\n"
        )
        yield LogLine("  Running Helm installer...")
        r = self._ssh_script(script, timeout=120)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Helm install failed"
            return False
        return True

    def _install_cert_manager(self) -> Generator[DeployEvent, None, bool]:
        v = self.cert_mgr_version
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "helm repo add rancher-prime https://charts.rancher.com/server-charts/prime || true\n"
            "helm repo add jetstack https://charts.jetstack.io || true\n"
            "helm repo update\n"
            f"kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/{v}/cert-manager.crds.yaml\n"
            f"helm upgrade --install cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --version {v}\n"
            "kubectl -n cert-manager rollout status deployment/cert-manager --timeout=180s\n"
            "kubectl -n cert-manager rollout status deployment/cert-manager-webhook --timeout=180s\n"
        )
        yield LogLine("  Adding Helm repos and installing cert-manager (3-5 min)...")
        r = self._ssh_script(script, timeout=480)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "cert-manager install failed"
            return False
        return True

    def _rancher_helm_values(self) -> dict:
        """Helm values for rancher-prime; secrets go here so they never appear on argv."""
        values: dict = {
            "hostname": self.rancher_hostname,
            "bootstrapPassword": self.admin_password,
            "replicas": 1,
        }
        if self.tls_source == "letsEncrypt":
            values["ingress"] = {"tls": {"source": "letsEncrypt"}}
            values["letsEncrypt"] = {
                "email": self.letsencrypt_email,
                "environment": "production",
            }
        return values

    def _install_rancher(self) -> Generator[DeployEvent, None, bool]:
        # Write values via a quoted heredoc so bootstrapPassword (and email/hostname)
        # never land on the helm process argv or in shell word-splitting.
        values_yaml = yaml.safe_dump(
            self._rancher_helm_values(),
            default_flow_style=False,
            sort_keys=False,
        )
        remote_values = "/root/rancher-helm-values.yaml"
        marker = "RODEO_HELM_VALUES_EOF"
        # Fail closed if the password somehow contains the heredoc marker
        # (would truncate the values file). Practically impossible for random secrets.
        if marker in values_yaml:
            self.error = "Rancher Helm values contain blocked heredoc marker"
            yield LogLine(f"  ✗ {self.error}")
            return False
        version = shlex.quote(str(self.rancher_version))
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "umask 077\n"
            f"cat > {remote_values} <<'{marker}'\n"
            f"{values_yaml}"
            f"{marker}\n"
            f"chmod 600 {remote_values}\n"
            f"helm upgrade --install rancher rancher-prime/rancher"
            f" --namespace cattle-system --create-namespace"
            f" --version {version}"
            f" -f {remote_values}"
            " --wait --timeout 600s\n"
            f"rm -f {remote_values}\n"
        )
        yield LogLine("  Running helm upgrade --install rancher (up to 10 min)...")
        r = self._ssh_script(script, timeout=720)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Rancher Prime install failed"
            return False
        return True

    def _expose_nodeport(self) -> Generator[DeployEvent, None, bool]:
        patch = json.dumps({
            "spec": {
                "type": "NodePort",
                "ports": [{"port": 443, "nodePort": self.nodeport}],
            }
        })
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"kubectl -n cattle-system patch svc rancher --type strategic -p '{patch}'\n"
        )
        r = self._ssh_script(script, timeout=30)
        if r.returncode != 0:
            self.error = f"NodePort patch failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False
        return True

    def _wait_ping(self) -> Generator[DeployEvent, None, bool]:
        ctx = self._ssl_ctx()
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            try:
                resp = urllib.request.urlopen(
                    f"{self.rancher_api}/ping", timeout=5, context=ctx
                )
                if b"pong" in resp.read():
                    yield ProgressUpdate("Waiting for /ping", elapsed, self.PING_TIMEOUT)
                    return True
            except Exception:
                pass

            if elapsed >= self.PING_TIMEOUT:
                yield ProgressUpdate("Waiting for /ping", elapsed, self.PING_TIMEOUT)
                return False

            yield ProgressUpdate("Waiting for /ping", elapsed, self.PING_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.PING_TIMEOUT // 60}:00 — Rancher not responding yet...")
            if self._sleep(self.PING_POLL):
                return False

    def _get_bootstrap_password(self) -> str:
        """Read the real bootstrap password from cattle-system/bootstrap-secret.

        Rancher 2.14+ (and fresh installs after a K3s state wipe) may use a
        randomly generated password rather than the literal bootstrapPassword
        Helm value.  Reading the secret is the only reliable way to find it.
        Falls back to 'admin' when the secret is absent (older installs).
        """
        r = self._ssh_script(
            "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"
            " get secret bootstrap-secret -n cattle-system"
            " -o jsonpath='{.data.bootstrapPassword}' 2>/dev/null"
            " | base64 -d 2>/dev/null",
            timeout=15,
        )
        pw = r.stdout.strip() if r.returncode == 0 else ""
        return pw or "admin"

    def _login(self, password: str) -> tuple[str, str]:
        """Return (token, error). Token is '' on failure; error describes what happened."""
        try:
            resp = self._http(
                "POST",
                "/v3-public/localProviders/local?action=login",
                {"username": "admin", "password": password},
            )
            token = resp.get("token", "")
            return token, ("" if token else "200 OK but no token in response")
        except Exception as exc:
            return "", str(exc)

    def _clear_must_change_password(self) -> None:
        """Patch the admin User to clear mustChangePassword.

        Rancher 2.14+ sets mustChangePassword=true on fresh installs.
        When that flag is set the /v3-public login endpoint returns 401
        instead of a token, blocking every API call.  Clearing it via
        kubectl before the login loop lets the normal flow proceed.
        This is idempotent and safe to call on every deploy.
        """
        script = (
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "ADMIN=$(kubectl get users.management.cattle.io"
            " -o jsonpath='{.items[?(@.username==\"admin\")].metadata.name}'"
            " 2>/dev/null)\n"
            '[ -z "$ADMIN" ] && exit 0\n'
            'kubectl patch users.management.cattle.io "$ADMIN"'
            " --type=merge -p '{\"mustChangePassword\": false}' 2>/dev/null\n"
        )
        self._ssh_script(script, timeout=15)

    def _configure_api(self) -> Generator[DeployEvent, None, bool]:
        # Rancher 2.14+ sets mustChangePassword=true on fresh installs which
        # causes the login endpoint to return 401 until cleared.  Do it here
        # before the login loop so the rest of the flow is unaffected.
        self._clear_must_change_password()

        # Try passwords in order: configured (secrets.yaml) first (succeeds on re-runs),
        # then the value in bootstrap-secret (succeeds on fresh installs), then the
        # literal 'admin' fallback (handles old deployments where bootstrap was hardcoded).
        # 'on_bootstrap' means the password is not yet the configured one and must be set.
        bootstrap_pw = self._get_bootstrap_password()
        # dict.fromkeys preserves order and deduplicates (e.g. when bootstrap_pw == admin_pw)
        candidates = list(dict.fromkeys([self.admin_password, bootstrap_pw, "admin"]))

        temp_token = ""
        on_bootstrap = False
        t0 = time.monotonic()
        last_errors: dict[str, str] = {}
        while True:
            for pw in candidates:
                token, err = self._login(pw)
                if token:
                    temp_token = token
                    on_bootstrap = (pw != self.admin_password)
                    break
                last_errors[pw] = err

            if temp_token:
                break

            elapsed = time.monotonic() - t0
            if elapsed >= self.LOGIN_TIMEOUT:
                break
            yield ProgressUpdate("Waiting for Rancher auth API", elapsed, self.LOGIN_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            errs = " | ".join(f"{pw[:8]}…: {e}" for pw, e in last_errors.items())
            yield LogLine(f"  {m:02d}:{s:02d} / {self.LOGIN_TIMEOUT // 60}:00 — {errs}")
            if self._sleep(self.LOGIN_POLL):
                return False

        if not temp_token:
            errs = " | ".join(f"{pw[:8]}…: {e}" for pw, e in last_errors.items())
            self.error = f"Rancher login failed — {errs}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        if on_bootstrap:
            try:
                # Resolve admin user ID — required for the setpassword action.
                user_resp = self._http("GET", "/v3/users?me=true", token=temp_token)
                user_id = (user_resp.get("data") or [{}])[0].get("id", "")
                if not user_id:
                    raise ValueError("could not resolve admin user ID from /v3/users?me=true")
                # setpassword clears mustChangePassword automatically; changepassword
                # does not in Rancher 2.8+ and silently leaves the new password inactive.
                self._http(
                    "POST",
                    f"/v3/users/{user_id}?action=setpassword",
                    {"newPassword": self.admin_password},
                    token=temp_token,
                )
            except Exception as exc:
                self.error = f"Password change failed: {exc}"
                yield LogLine(f"  ✗ {self.error}")
                return False
        else:
            yield LogLine("  Admin password already set — skipping change.")

        try:
            resp = self._http(
                "POST",
                "/v3-public/localProviders/local?action=login",
                {"username": "admin", "password": self.admin_password},
            )
            self._api_token = resp.get("token", "")
        except Exception as exc:
            self.error = f"Re-login after password change failed: {exc}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        if not self._api_token:
            self.error = "Re-login returned no token"
            yield LogLine(f"  ✗ {self.error}")
            return False

        try:
            self._http(
                "PUT",
                "/v3/settings/server-url",
                {"value": self.rancher_server_url},
                token=self._api_token,
            )
        except Exception as exc:
            yield LogLine(f"  ⚠ server-url set: {exc}")

        # Sync cacerts with the actual serving CA.  Each Helm upgrade can rotate
        # tls-rancher-internal-ca while preserving the old cacerts Setting value,
        # causing cattle-cluster-agent to fail TLS verification on the next import.
        yield from self._sync_cacerts()

        try:
            pass_file = Path("/root/rancher-password")
            pass_file.write_text(self.admin_password)
            pass_file.chmod(0o600)
            yield LogLine("  Admin password saved to /root/rancher-password")
        except Exception:
            pass

        return True

    def _sync_cacerts(self) -> Iterator[DeployEvent]:
        """Ensure cacerts holds the CA that actually signs the served TLS chain.

        The cattle-cluster-agent verifies Rancher's TLS using the cacerts Setting.
        It must contain the exact CA the server presents on the wire, or the agent
        crashloops with "certificate signed by unknown authority (ECDSA
        verification failure)".

        Source of truth = the CA the server actually serves. We open a TLS
        connection to the port agents connect on (the Rancher NodePort) and take
        the issuer cert straight from the presented chain. This is deliberately
        NOT read from a K8s secret: on NodePort deployments the dynamiclistener
        serving CA differs from tls-rancher-ingress (same CN
        "dynamiclistener-ca@<serial>", different key), and syncing the ingress CA
        writes the WRONG cert — the agent then rejects the real chain. Pulling the
        CA from the live handshake is version- and topology-independent.
        """
        if self.standalone:
            return

        # Extract the issuer (2nd) cert from the chain served on the agent-facing
        # NodePort. `openssl s_client -showcerts` prints the full chain; the leaf
        # is cert 1 and its signing dynamiclistener-ca is cert 2.
        extract = (
            "set -euo pipefail\n"
            f"echo | openssl s_client -connect 127.0.0.1:{self.nodeport} -showcerts 2>/dev/null"
            " | awk '/BEGIN CERT/{c++} c==2'\n"
        )
        r = self._ssh_script(extract, timeout=20)
        served_ca = r.stdout.strip()
        if r.returncode != 0 or "BEGIN CERTIFICATE" not in served_ca:
            yield LogLine("  ⚠ cacerts sync: could not read served CA — skipping")
            return

        try:
            current = self._http("GET", "/v3/settings/cacerts", token=self._api_token)
            api_ca = (current.get("value") or "").strip()
        except Exception:
            api_ca = ""

        if api_ca == served_ca:
            return  # already in sync

        # cacerts is read-only via the REST API; patch the K8s resource directly.
        # Re-extract inside the same shell so the exact bytes are patched (piping
        # the PEM back through Python json.dumps preserves newlines safely).
        patch_script = (
            "set -euo pipefail\n"
            f"CA=$(echo | openssl s_client -connect 127.0.0.1:{self.nodeport} -showcerts 2>/dev/null"
            " | awk '/BEGIN CERT/{c++} c==2')\n"
            'VALUE=$(python3 -c "import sys,json; print(json.dumps(sys.stdin.read().rstrip()))" <<< "$CA")\n'
            "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"
            ' patch setting cacerts'
            ' --type=merge -p "{\\"value\\": $VALUE}" 2>&1\n'
        )
        r2 = self._ssh_script(patch_script, timeout=20)
        if r2.returncode == 0:
            yield LogLine("  cacerts synced with the served CA.")
        else:
            yield LogLine(f"  ⚠ cacerts sync: {r2.stderr.strip()[:120]}")

    def _import_harvester(self) -> Generator[DeployEvent, None, bool]:
        # Use the provisioning.cattle.io/v1 Cluster API — the documented import path
        # per https://docs.harvesterhci.io/v1.8/rancher/virtualization-management
        #
        # CATTLE_CA_CHECKSUM mismatch in Rancher 2.14.x is handled by _fix_cattle_ca_checksum
        # after the deployment appears on Harvester.  agentEnvVars does not propagate to
        # cattle-cluster-agent for imported clusters, so CATTLE_INSECURE_TLS is set there too.
        cluster_manifest = json.dumps({
            "apiVersion": "provisioning.cattle.io/v1",
            "kind": "Cluster",
            "metadata": {
                "name": "harvester",
                "namespace": "fleet-default",
                "labels": {"provider.cattle.io": "harvester"},
                "annotations": {
                    "field.cattle.io/description": "Harvester HCI cluster for SUSE Virt Rodeo",
                },
            },
            "spec": {
                "agentEnvVars": [
                    {"name": "CATTLE_INSECURE_TLS", "value": "true"},
                ],
            },
        })

        yield LogLine("  Creating Harvester cluster record (provisioning API)...")
        # Heredoc avoids all shell quoting issues when JSON passes over SSH.
        r = self._ssh_script(
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "kubectl apply -f - <<'__MANIFEST__'\n"
            f"{cluster_manifest}\n"
            "__MANIFEST__\n",
            timeout=30,
        )
        if r.returncode != 0:
            self.error = f"Cluster create failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        # Poll .status.clusterName — the c-m-xxxxx ID Rancher assigns.
        # 10 s poll interval (was 5 s) to reduce SSH connection pile-up.
        yield LogLine("  Waiting for cluster ID (up to 2 min)...")
        t0 = time.monotonic()
        cluster_id = ""
        _last_log = 0.0
        while time.monotonic() - t0 < 120:
            r = self._ssh_script(
                "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
                "kubectl get cluster.provisioning.cattle.io/harvester -n fleet-default "
                "-o jsonpath='{.status.clusterName}' 2>/dev/null\n",
                timeout=15,
            )
            value = r.stdout.strip()
            if r.returncode == 0 and value and value != "null":
                cluster_id = value
                break
            elapsed = time.monotonic() - t0
            if elapsed - _last_log >= 15:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(f"  {m:02d}:{s:02d} / 02:00 — waiting for cluster ID...")
                _last_log = elapsed
            if self._sleep(10):
                return False

        if not cluster_id:
            self.error = "Cluster ID not assigned after 120 s"
            yield LogLine(f"  ✗ {self.error}")
            return False

        self._cluster_id = cluster_id
        yield LogLine(f"  Cluster record: {cluster_id}")

        # Create the ClusterRegistrationToken in the cluster's namespace.
        token_manifest = json.dumps({
            "apiVersion": "management.cattle.io/v3",
            "kind": "ClusterRegistrationToken",
            "metadata": {"name": "default-token", "namespace": cluster_id},
            "spec": {"clusterName": cluster_id},
        })
        r = self._ssh_script(
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "kubectl apply -f - <<'__MANIFEST__'\n"
            f"{token_manifest}\n"
            "__MANIFEST__\n",
            timeout=30,
        )
        if r.returncode != 0:
            self.error = f"Registration token create failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        # Poll .status.manifestUrl — extended to 120 s; Rancher can be slow after
        # creating the cluster record, especially under load.
        yield LogLine("  Waiting for manifest URL (up to 2 min)...")
        manifest_url = ""
        t0 = time.monotonic()
        _last_log = 0.0
        while time.monotonic() - t0 < 120:
            r = self._ssh_script(
                "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
                f"kubectl get clusterregistrationtoken.management.cattle.io/default-token "
                f"-n {cluster_id} -o jsonpath='{{.status.manifestUrl}}' 2>/dev/null\n",
                timeout=15,
            )
            value = r.stdout.strip()
            if r.returncode == 0 and value and value != "null":
                manifest_url = value
                break
            elapsed = time.monotonic() - t0
            if elapsed - _last_log >= 15:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(f"  {m:02d}:{s:02d} / 02:00 — waiting for manifest URL...")
                _last_log = elapsed
            if self._sleep(10):
                return False

        if not manifest_url:
            self.error = "Manifest URL not available after 120 s"
            yield LogLine(f"  ✗ {self.error}")
            return False

        if not harvester_kubeconfig_path().exists():
            self.error = (
                f"Harvester kubeconfig not found at {harvester_kubeconfig_path()} — "
                "run the cluster phase first"
            )
            yield LogLine(f"  ✗ {self.error}")
            return False

        yield from self._patch_coredns()

        # Apply cluster-registration-url to Harvester — the native import mechanism.
        # Harvester's controller deploys cattle-cluster-agent from this setting.
        yield LogLine("  Registering Harvester with Rancher via cluster-registration-url...")
        setting_manifest = json.dumps({
            "apiVersion": "harvesterhci.io/v1beta1",
            "kind": "Setting",
            "metadata": {"name": "cluster-registration-url"},
            "value": manifest_url,
        })
        # Pass JSON via stdin — avoids all shell quoting issues (no bash -c / echo).
        r = self._run(
            ["kubectl", "--kubeconfig", str(harvester_kubeconfig_path()), "apply", "-f", "-"],
            timeout=30,
            input=setting_manifest,
        )
        if r.returncode != 0:
            self.error = f"cluster-registration-url apply failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Registration URL applied — Harvester will deploy the cluster agent.")
        yield from self._fix_cattle_ca_checksum()

        try:
            kube_dir = Path("/root/.kube")
            kube_dir.mkdir(parents=True, exist_ok=True)
            dest = kube_dir / "harvester.yaml"
            dest.write_text(harvester_kubeconfig_path().read_text())
            dest.chmod(0o600)
            yield LogLine(f"  Harvester kubeconfig saved to {dest}")
        except Exception as exc:
            yield LogLine(f"  ⚠ kubeconfig copy: {exc}")

        yield LogLine("  Waiting for cluster to go Active (up to 30 min)...")
        if not (yield from self._wait_cluster_active()):
            self.error = "Harvester cluster did not reach Active in Rancher"
            yield LogLine(f"  ✗ {self.error}")
            return False

        return True

    def _fix_cattle_ca_checksum(self) -> Iterator[DeployEvent]:
        """Patch cattle-cluster-agent to survive Rancher's off-by-one CATTLE_CA_CHECKSUM.

        The rancher-agent binary appends '\\n' to the downloaded cacerts PEM before
        computing its sha256 checksum.  Rancher generates CATTLE_CA_CHECKSUM as
        sha256(raw_value) — one byte shorter — so the comparison always fails.

        Two patches applied together solve this permanently:
        1. CATTLE_CA_CHECKSUM = sha256(raw_value + '\\n')  — correct value for the binary
        2. minReadySeconds: 300  — if Rancher reconciles with the wrong checksum, new pods
           crashloop instantly and never sustain 300 s of readiness, so maxUnavailable=0
           keeps the old correct-checksum pods running indefinitely.
        """
        if self.standalone:
            return

        try:
            resp = self._http("GET", "/v3/settings/cacerts", token=self._api_token)
            raw_value = resp.get("value") or ""
        except Exception:
            return
        if not raw_value.strip():
            return
        correct_checksum = hashlib.sha256((raw_value + "\n").encode()).hexdigest()
        yield LogLine(f"  Correct CATTLE_CA_CHECKSUM: {correct_checksum[:16]}...")

        yield LogLine("  Waiting for cattle-cluster-agent deployment (up to 90 s)...")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90:
            r = self._run(
                [
                    "kubectl", "--kubeconfig", str(harvester_kubeconfig_path()),
                    "get", "deployment", "cattle-cluster-agent",
                    "-n", "cattle-system", "--ignore-not-found",
                    "-o", "jsonpath={.metadata.name}",
                ],
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip() == "cattle-cluster-agent":
                break
            if self._sleep(5):
                return
        else:
            yield LogLine("  ⚠ cattle-cluster-agent deployment not found in 90 s — skipping patch.")
            return

        # Two separate patches:
        # 1. kubectl set env — updates only CATTLE_CA_CHECKSUM (merge by name, no image required)
        # 2. JSON patch — adds minReadySeconds:300 so crashlooping pods from Rancher's
        #    reconciliation (with wrong checksum) never sustain 300s of readiness and never
        #    trigger old-pod termination (maxUnavailable=0 keeps old pods alive indefinitely).
        #    minReadySeconds is absent from the import manifest so it survives reconciliation.
        r = self._run(
            [
                "kubectl", "--kubeconfig", str(harvester_kubeconfig_path()),
                "set", "env", "deployment/cattle-cluster-agent",
                "-n", "cattle-system",
                f"CATTLE_CA_CHECKSUM={correct_checksum}",
                "CATTLE_INSECURE_TLS=true",
            ],
            timeout=20,
        )
        if r.returncode != 0:
            yield LogLine(f"  ⚠ env patch failed: {r.stderr.strip()[:80]}")
            return

        r2 = self._run(
            [
                "kubectl", "--kubeconfig", str(harvester_kubeconfig_path()),
                "patch", "deployment", "cattle-cluster-agent",
                "-n", "cattle-system",
                "--type=json",
                "-p", '[{"op":"add","path":"/spec/minReadySeconds","value":300}]',
            ],
            timeout=20,
        )
        if r2.returncode == 0:
            yield LogLine("  Patched CATTLE_CA_CHECKSUM + minReadySeconds:300 — agent will connect.")
        else:
            yield LogLine(f"  ⚠ minReadySeconds patch failed: {r2.stderr.strip()[:80]}")

    def _patch_coredns(self) -> Iterator[DeployEvent]:
        dns_server = self.gateway
        cm_name = None
        for candidate in ("rke2-coredns-rke2-coredns", "coredns"):
            r = self._run(
                [
                    "kubectl", "--kubeconfig", str(harvester_kubeconfig_path()),
                    "get", "cm", candidate, "-n", "kube-system", "--ignore-not-found",
                ],
                timeout=30,
            )
            if r.returncode == 0 and candidate in r.stdout:
                cm_name = candidate
                break

        if not cm_name:
            yield LogLine("  ⚠ CoreDNS ConfigMap not found — pod DNS patch skipped")
            return

        r = self._run(
            [
                "kubectl", "--kubeconfig", str(harvester_kubeconfig_path()),
                "get", "cm", cm_name, "-n", "kube-system", "-o", "json",
            ],
            timeout=30,
        )
        if r.returncode != 0:
            yield LogLine("  ⚠ CoreDNS get failed — pod DNS patch skipped")
            return

        try:
            cm = json.loads(r.stdout)
            corefile = cm.get("data", {}).get("Corefile", "")
        except json.JSONDecodeError:
            yield LogLine("  ⚠ CoreDNS JSON parse error — pod DNS patch skipped")
            return

        if self.dns_domain in corefile:
            yield LogLine(f"  {self.dns_domain} zone already present — CoreDNS patch skipped")
            return

        zone = (
            f"\n{self.dns_domain}:53 {{\n"
            f"    errors\n"
            f"    forward . {dns_server}\n"
            f"    cache 30\n"
            f"}}\n"
        )
        cm["data"]["Corefile"] = corefile + zone

        r2 = self._run(
            ["kubectl", "--kubeconfig", str(harvester_kubeconfig_path()), "apply", "-f", "-"],
            timeout=30,
            input=json.dumps(cm),
        )
        if r2.returncode == 0:
            yield LogLine(f"  CoreDNS patched: {self.dns_domain} -> {dns_server}")
        else:
            yield LogLine(f"  ⚠ CoreDNS patch apply failed: {r2.stderr.strip()}")

    def _wait_cluster_active(self) -> Generator[DeployEvent, None, bool]:
        t0 = time.monotonic()
        state = "unknown"
        while True:
            elapsed = time.monotonic() - t0
            try:
                resp = self._http(
                    "GET",
                    f"/v3/clusters/{self._cluster_id}",
                    token=self._api_token,
                )
                state = resp.get("state", "unknown")
            except Exception:
                pass

            yield ProgressUpdate("Cluster Active", elapsed, self.CLUSTER_TIMEOUT, detail=state)

            if state == "active":
                return True

            if elapsed >= self.CLUSTER_TIMEOUT:
                return False

            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.CLUSTER_TIMEOUT // 60}:00 — cluster state: {state}")
            if self._sleep(self.CLUSTER_POLL):
                return False

    def _set_harvester_password(self) -> Iterator[DeployEvent]:
        ctx = self._ssl_ctx()
        bootstrap_token = ""
        try:
            req = urllib.request.Request(
                f"https://{self.vip}/v3-public/localProviders/local?action=login",
                data=json.dumps({"username": "admin", "password": "admin"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                bootstrap_token = json.loads(resp.read()).get("token", "")
        except Exception:
            pass

        if bootstrap_token:
            try:
                req = urllib.request.Request(
                    f"https://{self.vip}/v3/users?action=changepassword",
                    data=json.dumps({
                        "currentPassword": "admin",
                        "newPassword": self.harvester_password,
                    }).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {bootstrap_token}",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, context=ctx, timeout=30)
                yield LogLine("  Harvester admin password set.")
            except Exception as exc:
                yield LogLine(f"  ⚠ Harvester password change: {exc}")
        else:
            yield LogLine("  Bootstrap admin/admin returned no token — password may already be set.")

        try:
            req = urllib.request.Request(
                f"https://{self.vip}/v3-public/localProviders/local?action=login",
                data=json.dumps({
                    "username": "admin",
                    "password": self.harvester_password,
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                hv_token = json.loads(resp.read()).get("token", "")
            if hv_token:
                token_file = Path("/root/harvester-token")
                token_file.write_text(hv_token)
                token_file.chmod(0o600)
                yield LogLine("  Harvester API token saved to /root/harvester-token")
        except Exception as exc:
            yield LogLine(f"  ⚠ Harvester token fetch: {exc}")

    def _eject_cdroms(self) -> Iterator[DeployEvent]:
        """Eject installer/config ISOs from Harvester VMs (best effort, respects cancellation).

        Prefers LibvirtDriver.eject_media (with cfg uri); falls back to virsh -c uri.
        Derives nodes from cfg (still name-based filter for now; see roadmap).
        """
        if self._stop.is_set():
            return

        # Prefer libvirt-python (no shell, uses configured URI)
        try:
            from .libvirt import LibvirtDriver
            with LibvirtDriver(self.libvirt_uri) as lv:
                for node in self.harvester_nodes:
                    if self._stop.is_set():
                        return
                    for dev in ("sda", "sdb"):
                        if self._stop.is_set():
                            return
                        try:
                            lv.eject_media(node, dev)
                        except Exception:
                            pass  # method is already best-effort
                    yield LogLine(f"  {node}: CDROMs ejected")
            return
        except Exception as exc:
            yield LogLine(f"  ⚠ libvirt eject unavailable ({exc}) — falling back to virsh")

        # Fallback using virsh (honor non-default libvirt URI)
        virsh = ["virsh"]
        if self.libvirt_uri != "qemu:///system":
            virsh = ["virsh", "-c", self.libvirt_uri]
        for node in self.harvester_nodes:
            if self._stop.is_set():
                return
            for dev in ("sda", "sdb"):
                if self._stop.is_set():
                    return
                r = self._run(
                    virsh + ["change-media", node, dev, "--eject", "--live", "--config"],
                    timeout=30,
                )
                if r.returncode != 0:
                    stderr = r.stderr.lower()
                    if not any(x in stderr for x in ("no media", "not a cdrom", "no such file")):
                        yield LogLine(f"  ⚠ eject {node}:{dev} — {r.stderr.strip()}")
            yield LogLine(f"  {node}: CDROMs ejected")

    def _install_elemental(self) -> Generator[DeployEvent, None, bool]:
        """Install Elemental Operator CRDs + Operator, UI extension, and MachineRegistrations."""
        namespace = "cattle-elemental-system"
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"helm upgrade --install elemental-operator-crds"
            f" oci://registry.suse.com/rancher/elemental-operator-crds-chart"
            f" --version {self.elemental_crds_version}"
            f" --namespace {namespace} --create-namespace"
            f" --wait --timeout 3m\n"
            f"helm upgrade --install elemental-operator"
            f" oci://registry.suse.com/rancher/elemental-operator-chart"
            f" --version {self.elemental_op_version}"
            f" --namespace {namespace}"
            f" --wait --timeout 5m\n"
        )
        yield LogLine(
            f"Installing Elemental Operator {self.elemental_op_version} "
            "(CRDs + Operator, up to 8 min)..."
        )
        r = self._ssh_script(script, timeout=540)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Elemental Operator install failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Elemental Operator installed.")

        # UI extension, repos, and MachineRegistrations are suse-edge-specific.
        # Other profiles (e.g. future rancher-only) may use the Operator without the UI.
        if self.profile_type == "suse-edge":
            if not (yield from self._add_extension_repos()):
                return False
            if not (yield from self._create_machine_registrations()):
                return False
            if not (yield from self._populate_hauler()):
                return False
            if not (yield from self._deploy_gitea()):
                return False
            if not (yield from self._create_alien_geeko_fleet()):
                return False
        return True

    def _add_extension_repos(self) -> Generator[DeployEvent, None, bool]:
        """Create the Rancher and partner extension ClusterRepo resources and dismiss the setup banner.

        Mirrors what the Rancher UI does when you click "Add Rancher and SUSE Repositories"
        in the Extensions page. Creates two cluster-scoped ClusterRepo CRs:
          - rancher-ui-plugins  (rancher/ui-plugin-charts, Rancher Prime official)
          - partner-extensions  (rancher/partner-extensions, SUSE + partners)
        """
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "cat <<'__EXT_REPOS__' | kubectl apply -f -\n"
            "---\n"
            "apiVersion: catalog.cattle.io/v1\n"
            "kind: ClusterRepo\n"
            "metadata:\n"
            "  name: rancher-ui-plugins\n"
            "spec:\n"
            "  gitBranch: main\n"
            "  gitRepo: https://github.com/rancher/ui-plugin-charts\n"
            "---\n"
            "apiVersion: catalog.cattle.io/v1\n"
            "kind: ClusterRepo\n"
            "metadata:\n"
            "  name: partner-extensions\n"
            "spec:\n"
            "  gitBranch: main\n"
            "  gitRepo: https://github.com/rancher/partner-extensions\n"
            "__EXT_REPOS__\n"
            # Dismiss the 'Add Rancher and SUSE Repositories' banner.
            "kubectl patch setting display-add-extension-repos-banner"
            " --type=merge -p '{\"value\": \"true\"}' 2>/dev/null || true\n"
        )
        yield LogLine("Adding Rancher and SUSE extension repositories...")
        r = self._ssh_script(script, timeout=30)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Extension repository creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Extension repositories added.")
        return True

    # ---------- Rancher UI extensions (declarative reconcile) ----------

    def _reconcile_ui_extensions(self) -> Generator[DeployEvent, None, bool]:
        """Reconcile the UI extensions declared in the definition (rancher.ui_extensions).

        For each extension: ensure its ClusterRepo exists, force-reindex it so the
        pinned version is resolvable even from a stale cached index, then install it
        (or upgrade an older release in place) via the Rancher catalog action, and
        verify. Idempotent and non-fatal: a failure logs a warning and moves on so a
        slow chart pull or a transient error never breaks the deploy.
        """
        ns = "cattle-ui-plugin-system"
        for ext in self.ui_extensions:
            name = ext.get("name")
            version = str(ext.get("version", "")).strip()
            repo = ext.get("repo", {}) or {}
            repo_name = repo.get("name", "rancher")
            git_repo = repo.get("git_repo", "")
            git_branch = repo.get("git_branch", "main")
            if not name or not version:
                yield LogLine(f"  ⚠ skipping malformed ui_extension entry: {ext!r}")
                continue

            yield LogLine(f"Reconciling Rancher UI extension {name} -> {version}...")
            current = self._ui_extension_version(name, ns)
            if current == version:
                yield LogLine(f"  {name} already at {version}.")
                continue

            if not (yield from self._ensure_ext_repo(repo_name, git_repo, git_branch)):
                if self.error == "cancelled":
                    return False
                yield LogLine(f"  ⚠ {name}: could not prepare ClusterRepo {repo_name}; skipping.")
                continue

            action = "upgrade" if current else "install"
            if not self._catalog_chart_action(action, repo_name, name, version, ns):
                yield LogLine(f"  ⚠ {name}: catalog {action} request failed; skipping.")
                continue

            # The catalog action kicks off an async helm-operation; poll for the result.
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if self._ui_extension_version(name, ns) == version:
                    break
                if self._sleep(10):
                    return False
            final = self._ui_extension_version(name, ns)
            if final == version:
                yield LogLine(f"  {name} reconciled to {version}.")
            else:
                yield LogLine(
                    f"  ⚠ {name} not at {version} yet (is '{final or 'none'}'); "
                    "check the Rancher Extensions page."
                )
        return True

    def _ui_extension_version(self, name: str, ns: str) -> str:
        """Installed UIPlugin version, or '' if the extension is not present."""
        script = (
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"kubectl -n {ns} get uiplugins.catalog.cattle.io {name} "
            "-o jsonpath='{.spec.plugin.version}' 2>/dev/null || true\n"
        )
        return self._ssh_script(script, timeout=30).stdout.strip()

    def _ensure_ext_repo(
        self, repo_name: str, git_repo: str, git_branch: str
    ) -> Generator[DeployEvent, None, bool]:
        """Create the ClusterRepo if missing, then force a re-index."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        create_block = ""
        if git_repo:
            create_block = (
                f"if ! kubectl get clusterrepo {repo_name} >/dev/null 2>&1; then\n"
                "  cat <<'__EXT_REPO__' | kubectl apply -f -\n"
                "apiVersion: catalog.cattle.io/v1\n"
                "kind: ClusterRepo\n"
                "metadata:\n"
                f"  name: {repo_name}\n"
                "spec:\n"
                f"  gitRepo: {git_repo}\n"
                f"  gitBranch: {git_branch}\n"
                "__EXT_REPO__\n"
                "fi\n"
            )
        script = (
            "set -e\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"{create_block}"
            f"kubectl patch clusterrepo {repo_name} --type=merge "
            f"-p '{{\"spec\":{{\"forceUpdate\":\"{ts}\"}}}}'\n"
        )
        r = self._ssh_script(script, timeout=60)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            return False
        if self._sleep(15):  # let the catalog controller download the index
            return False
        return True

    def _catalog_chart_action(
        self, action: str, repo_name: str, chart: str, version: str, ns: str
    ) -> bool:
        """Drive the Rancher catalog install/upgrade action for one chart. True on success."""
        body = {
            "charts": [
                {
                    "chartName": chart,
                    "version": version,
                    "releaseName": chart,
                    "annotations": {
                        "catalog.cattle.io/ui-source-repo-type": "cluster",
                        "catalog.cattle.io/ui-source-repo": repo_name,
                    },
                    "values": {},
                }
            ],
            "namespace": ns,
            "wait": True,
            "timeout": "600s",
        }
        try:
            self._http(
                "POST",
                f"/v1/catalog.cattle.io.clusterrepos/{repo_name}?action={action}",
                data=body,
                token=self._api_token,
            )
            return True
        except Exception:
            return False

    def _create_machine_registrations(self) -> Generator[DeployEvent, None, bool]:
        """Create Elemental MachineRegistration CRs in fleet-default.

        Creates self.elemental_reg_count registrations named
        {prefix}-reg-1, {prefix}-reg-2, ... Each gets a distinct label
        so students can target specific registrations in their EIB image config.
        """
        if self.elemental_reg_count < 1:
            return True

        prefix = self.elemental_reg_prefix
        yield LogLine(
            f"Creating {self.elemental_reg_count} MachineRegistration(s) "
            f"({prefix}-reg-1 .. {prefix}-reg-{self.elemental_reg_count})..."
        )

        manifests = []
        for n in range(1, self.elemental_reg_count + 1):
            name = f"{prefix}-reg-{n}"
            manifests.append(
                f"apiVersion: elemental.cattle.io/v1beta1\n"
                f"kind: MachineRegistration\n"
                f"metadata:\n"
                f"  name: {name}\n"
                f"  namespace: fleet-default\n"
                f"spec:\n"
                # machineName uses SMBIOS fields — interpreted by Elemental at boot time,
                # not by the shell. The literal ${} must reach the cluster as-is.
                f"  machineName: '${{System Information/Manufacturer}}-${{System Information/UUID}}'\n"
                f"  machineInventoryLabels:\n"
                f"    manufacturer: '${{System Information/Manufacturer}}'\n"
                f"    productName: '${{System Information/Product Name}}'\n"
                f"    registration: '{name}'\n"
            )

        combined = "---\n" + "\n---\n".join(manifests)
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"cat <<'__ELEMENTAL_MANIFEST__' | kubectl apply -f -\n"
            f"{combined}\n"
            "__ELEMENTAL_MANIFEST__\n"
        )
        r = self._ssh_script(script, timeout=60)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "MachineRegistration creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  MachineRegistration(s) created. "
            f"Retrieve URL: kubectl get machineregistration {prefix}-reg-1 "
            f"-n fleet-default -o jsonpath='{{.status.registrationURL}}'"
        )
        return True

    def _populate_hauler(self) -> Generator[DeployEvent, None, bool]:
        """Populate the Hauler store on the eib VM with SUSE Edge artifacts.

        Runs after all Rancher/Elemental artifacts are fully downloaded so internet
        bandwidth is free. Downloads into /var/lib/hauler on the eib VM, then
        enables and starts the Hauler OCI registry (port 5000) and fileserver
        (port 8080) so participants can build EIB images fully offline.

        Also pre-stages the EIB image definition template at /home/eib-config/ with
        a placeholder for the MachineRegistration URL that participants fill in.
        """
        prefix = self.elemental_reg_prefix
        reg_name = f"{prefix}-reg-1"
        # Fixed lowercase names, not derived from the upstream URL: hauler's `store
        # add file` reference-name parser rejects uppercase (confirmed live —
        # "could not parse reference" with no name given; works once --name is
        # lowercase), and openSUSE's filenames ("openSUSE-Leap-Micro...") are
        # uppercase. Deterministic names also decouple us from upstream renames.
        iso_fname = "leap-micro-selfinstall.iso"
        raw_fname_dl = "leap-micro-default.raw.xz"
        # openSUSE ships the raw appliance .xz-compressed; EIB needs a plain .raw
        # baseImage, so it gets decompressed after staging (see the curl/xz block
        # below). raw_fname is the name EIB definitions actually reference.
        raw_fname = "leap-micro-default.raw"
        raw_decompress_cmd = f'xz -d -f "/home/eib-config/base-images/{raw_fname_dl}"\n'

        script = (
            "set -euo pipefail\n"
            "STORE=/var/lib/hauler\n"
            "HAULER=/usr/local/bin/hauler\n\n"
            # Mirror the EIB container image into Hauler so participants can run
            # EIB without internet access from the eib VM.
            f'$HAULER store add image "{self.eib_image}" --store $STORE\n'
            # Elemental register agent — EIB embeds this into the edge node image
            # so nodes can phone home to the Elemental Operator on first boot.
            # There is no standalone "elemental-register" image at registry.suse.com
            # (confirmed live: NAME_UNKNOWN) — the register binary ships inside the
            # elemental-operator image itself, same tag as the operator Deployment
            # (confirmed live: registry.suse.com/rancher/elemental-operator:1.9.0
            # pulls fine; this is the exact image already deployed by the elemental
            # phase's own Helm install a few steps earlier).
            f'$HAULER store add image "registry.suse.com/rancher/elemental-operator:{self.elemental_op_version}" --store $STORE\n'
            # Demo app image (Fleet-deployed to edge clusters, from cfg["alien_geeko"]["image"]);
            # edge nodes pull from Hauler via k3s registry mirror (docker.io → eib:5000).
            f'$HAULER store add image "{self.alien_geeko_image}" --store $STORE\n'
            # Leap Micro 6.2 SelfInstall ISO — EIB base for Elemental ISO builds (edge1/edge2).
            # Download via curl, not hauler's own HTTP client: opensuse.org's
            # redirector picks a rotating mirror, and at least one observed mirror
            # (pkg.adfinis-on-exoscale.ch) fails hauler's Go TLS client outright
            # ("tls: protocol version not supported") — curl (used everywhere else
            # in this codebase for large downloads) negotiates it fine. Then add the
            # already-downloaded local file with an explicit lowercase --name.
            f'curl -4 --http1.1 -fsSL --retry 5 --retry-delay 10 --retry-all-errors '
            f'-o "/tmp/{iso_fname}" "{self.leap_micro_iso_url}"\n'
            f'$HAULER store add file "/tmp/{iso_fname}" --name "{iso_fname}" --store $STORE\n'
            f'rm -f "/tmp/{iso_fname}"\n'
            # Leap Micro 6.2 Default RAW (.xz) — EIB base for standalone K3s/RKE2 builds (edge3/edge4)
            f'curl -4 --http1.1 -fsSL --retry 5 --retry-delay 10 --retry-all-errors '
            f'-o "/tmp/{raw_fname_dl}" "{self.leap_micro_raw_url}"\n'
            f'$HAULER store add file "/tmp/{raw_fname_dl}" --name "{raw_fname_dl}" --store $STORE\n'
            f'rm -f "/tmp/{raw_fname_dl}"\n\n'
            # Enable and start Hauler services (service units written by cloud-init)
            "systemctl daemon-reload\n"
            "systemctl enable --now hauler-registry.service hauler-fileserver.service\n\n"
            # enable --now returns once systemd has forked the unit, not once the
            # fileserver is actually bound and listening — curling immediately here
            # raced the startup and failed "Could not connect to server". Poll until
            # it answers (fileserver has no dedicated health path; a bare GET 404
            # still proves the socket is up) before staging the base images below.
            "for i in $(seq 1 30); do\n"
            '  curl -sS -o /dev/null "http://localhost:8080/" 2>/dev/null && break\n'
            "  sleep 1\n"
            "done\n\n"
            # Stage Leap Micro base images from Hauler fileserver into eib-config/base-images
            # so participants can reference them by filename in EIB definition files without
            # needing internet. The ISO is for Elemental builds; the RAW is for standalone builds.
            "mkdir -p /home/eib-config/scripts /home/eib-config/base-images /home/eib-output\n"
            f'curl -fsSL "http://localhost:8080/{iso_fname}" -o "/home/eib-config/base-images/{iso_fname}"\n'
            f'curl -fsSL "http://localhost:8080/{raw_fname_dl}" -o "/home/eib-config/base-images/{raw_fname_dl}"\n'
            f"{raw_decompress_cmd}\n"
            # k3s registry mirror script — EIB runs this during image build to embed
            # /etc/rancher/k3s/registries.yaml into the edge node OS so ALL container
            # pulls (docker.io, registry.suse.com, ghcr.io) go through the Hauler
            # registry at boot time, keeping edge nodes fully airgapped.
            f"cat > /home/eib-config/scripts/99-k3s-registries.sh << 'K3S_REG'\n"
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "mkdir -p /etc/rancher/k3s\n"
            "cat > /etc/rancher/k3s/registries.yaml << 'EOF'\n"
            "mirrors:\n"
            '  "docker.io":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            '  "registry.suse.com":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            '  "ghcr.io":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            "EOF\n"
            "K3S_REG\n"
            "chmod +x /home/eib-config/scripts/99-k3s-registries.sh\n\n"
            # Pre-stage EIB definition template for participants.
            # EIB 1.3.3 does NOT have a top-level elemental: key — Elemental registration
            # is configured via embeddedArtifacts (checked at build time from the Hauler store).
            f"cat > /home/eib-config/edge-definition.yaml << '__EIB_DEF__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n"
            "  imageType: raw\n"
            "  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: elemental-edge.raw\n\n"
            "operatingSystem:\n"
            "  kernelArgs:\n"
            "    - net.ifnames=0\n"
            "  scripts:\n"
            "    - 99-k3s-registries.sh\n\n"
            "embeddedArtifacts:\n"
            "  registries:\n"
            "    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__EIB_DEF__\n"
        )
        yield LogLine(
            f"Populating Hauler store on eib VM ({self.eib_ip}) "
            "with SUSE Edge artifacts (may take 15-30 min)..."
        )
        r = self._eib_ssh_script(script, timeout=2400)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Hauler store population failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            "  Hauler store populated. Registry: "
            f"http://{self.eib_ip}:5000  Fileserver: http://{self.eib_ip}:8080"
        )
        yield LogLine(
            f"  EIB definition template: /home/eib-config/edge-definition.yaml\n"
            f"  Set registration URL: kubectl get machineregistration {reg_name} "
            f"-n fleet-default -o jsonpath='{{{{.status.registrationURL}}}}'"
        )
        return True

    def _create_alien_geeko_fleet(self) -> Generator[DeployEvent, None, bool]:
        """Create a Fleet GitRepo for the demo app declared in cfg["alien_geeko"].

        Defaults to Alien-Geeko (https://github.com/SUSE-Technical-Marketing/Alien-Geeko), a
        Node.js CRT terminal web app showing Kubernetes cluster vitals, but every name/label here
        comes from self.alien_geeko_* (set from cfg["alien_geeko"] in __init__) so a rodeo-plan.yaml
        override can point this at a different demo app entirely.

        Participants label their edge cluster after Elemental registers + provisions it.
        The GitRepo is ready in advance so deployment kicks in the moment the label appears.
        """
        labels_yaml = "".join(
            f'          {k}: "{v}"\n' for k, v in self.alien_geeko_target_labels.items()
        )
        selector_yaml = ", ".join(
            f"{k}={v}" for k, v in self.alien_geeko_target_labels.items()
        )
        manifest = (
            "apiVersion: fleet.cattle.io/v1alpha1\n"
            "kind: GitRepo\n"
            "metadata:\n"
            f"  name: {self.alien_geeko_fleet_name}\n"
            f"  namespace: {self.alien_geeko_fleet_namespace}\n"
            "spec:\n"
            f"  repo: http://{self.eib_ip}:{self.gitea_port}/{self.gitea_user}/{self.alien_geeko_fleet_name}.git\n"
            "  branch: main\n"
            "  targets:\n"
            "    - name: x86-edge-clusters\n"
            "      clusterSelector:\n"
            "        matchLabels:\n"
            f"{labels_yaml}"
        )
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"cat <<'__GITREPO__' | kubectl apply -f -\n"
            f"{manifest}"
            "__GITREPO__\n"
        )
        yield LogLine(f"Creating Fleet GitRepo for {self.alien_geeko_fleet_name} demo app...")
        r = self._ssh_script(script, timeout=30)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Fleet GitRepo creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  Fleet GitRepo '{self.alien_geeko_fleet_name}' created in {self.alien_geeko_fleet_namespace}.\n"
            f"  To deploy: label an edge cluster with  {selector_yaml}\n"
            f"  Image served from Hauler: http://{self.eib_ip}:5000 (docker.io mirror)"
        )
        return True

    def _deploy_gitea(self) -> Generator[DeployEvent, None, bool]:
        """Deploy Gitea as a rootless Podman container on the EIB VM.

        Gitea runs on port 3000 alongside Hauler (port 5000/8080). The Alien-Geeko
        repo is mirrored from GitHub once at deploy time using Gitea's migration API
        (no git binary needed on the host). After deploy, Fleet syncs exclusively from
        local Gitea — no GitHub access needed during lab exercises.

        Credentials: admin_user from definition.yaml, password from secrets.yaml.
        """
        image = f"docker.io/gitea/gitea:{self.gitea_version}-rootless"
        gitea_url = f"http://localhost:{self.gitea_port}"
        # Same filenames _populate_hauler stages onto the eib VM — the .raw (not
        # .raw.xz) is what actually lands in base-images/ after decompression.
        iso_fname = self.leap_micro_iso_url.split("/")[-1]
        raw_fname_dl = self.leap_micro_raw_url.split("/")[-1]
        raw_fname = raw_fname_dl[:-3] if raw_fname_dl.endswith(".xz") else raw_fname_dl

        # NMState network-config, one file per edge node, generated from the
        # definition (name + IP + prefix + gateway + DNS). No node names or IPs
        # are hardcoded here — add/remove/renumber edge nodes in definition.yaml
        # and these regenerate to match.
        nmstate_blocks = ""
        for e in self.edge_nodes:
            nmstate_blocks += (
                f"cat > \"$EIB_REPO/network-configs/{e['name']}.yaml\" << 'NM_EOF'\n"
                "interfaces:\n  - name: eth0\n    type: ethernet\n    state: up\n"
                f"    ipv4:\n      address:\n        - ip: {e['ip']}\n          prefix-length: {self.net_prefix}\n"
                "      dhcp: false\n      enabled: true\n"
                "routes:\n  config:\n    - destination: 0.0.0.0/0\n"
                f"      next-hop-address: {self.gateway}\n      next-hop-interface: eth0\n"
                f"dns-resolver:\n  config:\n    servers:\n      - {self.dns_server}\n"
                "NM_EOF\n\n"
            )

        script = (
            "set -euo pipefail\n"
            f"GITEA_URL={gitea_url}\n"
            f"GITEA_USER={self.gitea_user}\n"
            f'GITEA_PASS="{self.gitea_password}"\n\n'
            # Start Gitea container (rootless, no SSH, SQLite backend).
            # --replace: a retry after a failure further down this same script
            # (e.g. the git-push step) leaves this container running under the
            # same name — confirmed live ("container name 'gitea' is already in
            # use"). gitea-data is a named volume, so replacing the container
            # keeps all prior state (users, repos) intact; every step below that
            # creates something already-created-by-a-prior-attempt tolerates that
            # for the same reason.
            f"podman run -d --name gitea --replace --restart=always \\\n"
            f"  -p {self.gitea_port}:{self.gitea_port} \\\n"
            "  -v gitea-data:/data \\\n"
            f'  -e GITEA__security__INSTALL_LOCK=true \\\n'
            f'  -e GITEA__server__ROOT_URL="http://{self.eib_ip}:{self.gitea_port}" \\\n'
            f"  -e GITEA__server__HTTP_PORT={self.gitea_port} \\\n"
            "  -e GITEA__server__DISABLE_SSH=true \\\n"
            f'  "{image}"\n\n'
            # Wait up to 60 s for the API to respond
            'echo "Waiting for Gitea..."\n'
            "for i in $(seq 1 30); do\n"
            '  curl -sf "$GITEA_URL/api/v1/version" >/dev/null 2>&1 && break\n'
            "  sleep 2\n"
            "done\n"
            'curl -sf "$GITEA_URL/api/v1/version" >/dev/null || '
            '{ echo "Gitea did not start in time"; exit 1; }\n\n'
            # Create admin user via the Gitea CLI inside the container. Tolerate
            # "already exists" (persisted in gitea-data from a prior attempt).
            "podman exec --user git gitea /usr/local/bin/gitea admin user create \\\n"
            '  --username "$GITEA_USER" --password "$GITEA_PASS" \\\n'
            "  --email gitea@aerogrid.local --admin --must-change-password=false \\\n"
            '  || echo "  (admin user already exists, continuing)"\n\n'
            # Generate API token for setup calls. write:repository alone covers
            # the /api/v1/repos/migrate call (alien-geeko) but NOT
            # /api/v1/user/repos (eib-config, further down) — confirmed live:
            # that endpoint 403s with "token does not have at least one of
            # required scope(s): [write:user]" without it.
            "TOKEN=$(curl -sf -X POST "
            '"$GITEA_URL/api/v1/users/$GITEA_USER/tokens" \\\n'
            '  -u "$GITEA_USER:$GITEA_PASS" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            "  -d '{\"name\":\"setup\",\"scopes\":[\"write:repository\",\"write:user\"]}' \\\n"
            "  | python3 -c "
            "\"import sys,json; print(json.load(sys.stdin)['sha1'])\")\n\n"
            # Mirror the demo app repo from GitHub via Gitea's migration API.
            # Gitea clones the repo internally — no git binary needed on the host.
            # This is the one internet call that happens at deploy time.
            # Tolerate a genuine 409 "already exists" (a prior attempt may have
            # migrated it successfully before failing at a later step) but fail
            # loud on anything else — see the eib-config creation below for why
            # a blanket `|| echo` is the wrong tool here.
            "RC=$(curl -s -o /tmp/alien-geeko-migrate.json -w '%{http_code}' "
            '-X POST "$GITEA_URL/api/v1/repos/migrate" \\\n'
            '  -H "Authorization: token $TOKEN" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            f'  -d \'{{"clone_addr":"{self.alien_geeko_fleet_repo}",'
            f'"repo_name":"{self.alien_geeko_fleet_name}","private":false,"mirror":false}}\')\n'
            'if [ "$RC" = "409" ]; then\n'
            f'  echo "  ({self.alien_geeko_fleet_name} repo already exists, continuing)"\n'
            'elif [ "$RC" != "200" ] && [ "$RC" != "201" ]; then\n'
            f'  echo "  {self.alien_geeko_fleet_name} migrate failed (HTTP $RC): $(cat /tmp/alien-geeko-migrate.json)"\n'
            "  exit 1\n"
            "fi\n\n"
            f'echo "  {self.alien_geeko_fleet_name}: http://{self.eib_ip}:{self.gitea_port}/$GITEA_USER/{self.alien_geeko_fleet_name}.git"\n\n'
            # ---- eib-config Gitea repo with EIB definition templates ----
            # Leap Micro 6.2's transactional-update model means `zypper install
            # git` silently no-ops (exit 0, "please use transactional-update to
            # update or modify the system", git still absent — confirmed live:
            # every subsequent `git` call below then failed "command not found",
            # aborting the whole script under set -e with no clear error surfaced
            # up top, since stdout/stderr get concatenated and reordered by the
            # time this method's caller prints them). Installing via
            # transactional-update needs a reboot mid-deploy, so run git in a
            # throwaway container instead — podman is already required and
            # working here for the Gitea container itself. Mounting $EIB_REPO at
            # the *same* path means every existing `git -C "$EIB_REPO" ...` call
            # below needs no changes.
            # --network host: the git push below targets http://localhost:3000/...
            # (Gitea on the host's own network namespace) — without this the
            # container gets its own network namespace and "localhost" would
            # resolve to itself, not the host, and the push would fail to connect.
            # No literal "git" before "$@": docker.io/alpine/git's own image
            # config sets ENTRYPOINT ["git"] (confirmed live via the registry
            # API) — passing "git" again here means the container actually runs
            # `git git -C ... init`, which fails ("'git' is not a git command").
            "git() {\n"
            '  podman run --rm --network host -v "$EIB_REPO:$EIB_REPO:Z" docker.io/alpine/git:latest "$@"\n'
            "}\n\n"
            # Tolerate a genuine 409 "already exists" (see the --replace note
            # above) but fail loud on anything else — a blanket `|| echo` here
            # previously masked a real 403 (missing write:user token scope,
            # fixed above) as if it were a harmless already-exists, so the
            # actual error only surfaced several steps later as a confusing
            # git-push 403 instead of at its real source.
            "RC=$(curl -s -o /tmp/eib-config-create.json -w '%{http_code}' "
            '-X POST "$GITEA_URL/api/v1/user/repos" \\\n'
            "  -H \"Authorization: token $TOKEN\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"name\":\"eib-config\",\"description\":\"AeroGrid EIB image definitions, network configs and combustion scripts\",\"private\":false,\"auto_init\":false}')\n"
            'if [ "$RC" = "409" ]; then\n'
            '  echo "  (eib-config repo already exists, continuing)"\n'
            'elif [ "$RC" != "200" ] && [ "$RC" != "201" ]; then\n'
            '  echo "  eib-config repo creation failed (HTTP $RC): $(cat /tmp/eib-config-create.json)"\n'
            "  exit 1\n"
            "fi\n\n"
            "EIB_REPO=/tmp/eib-config-repo\n"
            "rm -rf \"$EIB_REPO\"\n"
            "mkdir -p \"$EIB_REPO/network-configs\" \"$EIB_REPO/scripts\" \"$EIB_REPO/elemental\" \"$EIB_REPO/network\"\n\n"
            # .gitignore — keep build outputs and the transient network/ dir out of git
            "cat > \"$EIB_REPO/.gitignore\" << 'GITIGNORE_EOF'\n"
            "*.iso\n*.raw\n*.qcow2\nnetwork/\n.eib/\n"
            "GITIGNORE_EOF\n\n"
            # Copy the registry mirror script already written by _populate_hauler
            "cp /home/eib-config/scripts/99-k3s-registries.sh \"$EIB_REPO/scripts/\"\n\n"
            # Hostname combustion scripts (edge3 and edge4 standalone path)
            "cat > \"$EIB_REPO/scripts/10-hostname-edge3.sh\" << 'HNAME3_EOF'\n"
            "#!/bin/bash\nhostnamectl set-hostname edge3\nHNAME3_EOF\n"
            "chmod +x \"$EIB_REPO/scripts/10-hostname-edge3.sh\"\n\n"
            "cat > \"$EIB_REPO/scripts/10-hostname-edge4.sh\" << 'HNAME4_EOF'\n"
            "#!/bin/bash\nhostnamectl set-hostname edge4\nHNAME4_EOF\n"
            "chmod +x \"$EIB_REPO/scripts/10-hostname-edge4.sh\"\n\n"
            # NMState network config templates — one per edge node, generated
            # above from the definition (see nmstate_blocks).
            + nmstate_blocks +
            # Elemental registration config placeholder — filled in during Exercise 2
            "cat > \"$EIB_REPO/elemental/elemental_config.yaml\" << 'ELEM_EOF'\n"
            "# Filled in during Exercise 2, section 2.4.\n"
            "# On the eib VM, after cloning this repo:\n"
            f"#   REGURL=$(ssh root@{self.rancher_ip} \\\n"
            "#     \"kubectl get machineregistration suse-edge-reg-1 \\\n"
            "#      -n fleet-default -o jsonpath='{.status.registrationURL}'\")\n"
            "#   curl -k \"$REGURL\" > elemental/elemental_config.yaml\n"
            "ELEM_EOF\n\n"
            # EIB definition files — Elemental ISO path (edge1, edge2)
            "cat > \"$EIB_REPO/elemental-edge1-definition.yaml\" << '__DEF1__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n  imageType: iso\n  arch: x86_64\n"
            f"  baseImage: {iso_fname}\n"
            "  outputImageName: elemental-edge1.iso\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n  files:\n"
            "    - sourcePath: elemental/elemental_config.yaml\n"
            "      destinationPath: /oem/elemental.yaml\n\n"
            "embeddedArtifacts:\n  registries:\n    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__DEF1__\n\n"
            "cat > \"$EIB_REPO/elemental-edge2-definition.yaml\" << '__DEF2__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n  imageType: iso\n  arch: x86_64\n"
            f"  baseImage: {iso_fname}\n"
            "  outputImageName: elemental-edge2.iso\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n  files:\n"
            "    - sourcePath: elemental/elemental_config.yaml\n"
            "      destinationPath: /oem/elemental.yaml\n\n"
            "embeddedArtifacts:\n  registries:\n    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__DEF2__\n\n"
            # EIB definition files — standalone cluster RAW path (edge3 RKE2, edge4 K3s)
            "cat > \"$EIB_REPO/rke2-edge3-definition.yaml\" << '__DEF3__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n  imageType: raw\n  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: rke2-edge3.raw\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n  scripts:\n"
            "    - 10-hostname-edge3.sh\n    - 99-k3s-registries.sh\n\n"
            "kubernetes:\n  version: v1.35.3+rke2r3\n\n"
            "embeddedArtifacts:\n  registries:\n    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__DEF3__\n\n"
            "cat > \"$EIB_REPO/k3s-edge4-definition.yaml\" << '__DEF4__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n  imageType: raw\n  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: k3s-edge4.raw\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n  scripts:\n"
            "    - 10-hostname-edge4.sh\n    - 99-k3s-registries.sh\n\n"
            "kubernetes:\n  version: v1.35.5+k3s1\n\n"
            "embeddedArtifacts:\n  registries:\n    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__DEF4__\n\n"
            # Commit and push to local Gitea
            "git -C \"$EIB_REPO\" init\n"
            "git -C \"$EIB_REPO\" config user.email \"rodeo@aerogrid.local\"\n"
            "git -C \"$EIB_REPO\" config user.name \"AeroGrid Lab\"\n"
            "git -C \"$EIB_REPO\" add .\n"
            "git -C \"$EIB_REPO\" commit -m \"initial EIB config templates for AeroGrid edge nodes\"\n"
            f"git -C \"$EIB_REPO\" remote add origin \"http://$GITEA_USER:$GITEA_PASS@localhost:{self.gitea_port}/gitea/eib-config.git\"\n"
            "git -C \"$EIB_REPO\" push -u origin HEAD:main\n"
            "rm -rf \"$EIB_REPO\"\n\n"
            f'echo "  eib-config: http://{self.eib_ip}:{self.gitea_port}/$GITEA_USER/eib-config.git"\n'
        )
        yield LogLine(
            f"Deploying Gitea {self.gitea_version} on eib VM ({self.eib_ip}:{self.gitea_port})..."
        )
        r = self._eib_ssh_script(script, timeout=300)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Gitea deployment failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  Gitea ready. {self.alien_geeko_fleet_name} and eib-config repos initialised.\n"
            f"  Fleet GitRepo: http://{self.eib_ip}:{self.gitea_port}"
            f"/{self.gitea_user}/{self.alien_geeko_fleet_name}.git\n"
            f"  EIB workspace: http://{self.eib_ip}:{self.gitea_port}"
            f"/{self.gitea_user}/eib-config.git"
        )
        return True

    def _write_env_file(self) -> None:
        """Write /etc/profile.d/rodeo.sh so passwords and URLs are available as env vars.

        Loaded by every login shell on the Instruqt host — students can use
        $HARVESTER_ADMIN_PASSWORD, $RANCHER_ADMIN_PASSWORD, etc. in challenge scripts
        without having to parse the secrets file.
        """
        lines = ["# Rodeo lab credentials — generated by rodeo-cli, do not edit by hand"]
        if self.vip:
            lines += [
                f'export HARVESTER_VIP="{self.vip}"',
                f'export HARVESTER_URL="https://{self.vip}"',
                f'export HARVESTER_ADMIN_PASSWORD="{self.harvester_password}"',
            ]
        lines += [
            f'export RANCHER_URL="{self.rancher_api}"',
            f'export RANCHER_ADMIN_PASSWORD="{self.rancher_password}"',
        ]
        content = "\n".join(lines) + "\n"
        try:
            Path("/etc/profile.d/rodeo.sh").write_text(content)
        except Exception:
            # Non-root or read-only fs — write to ~/.rodeo/ as fallback
            from ..paths import rodeo_dir

            fallback = rodeo_dir() / "rodeo.env"
            try:
                fallback.write_text(content)
            except Exception:
                pass

    def _completion_summary(self) -> str:
        lines = [
            "\n",
            "  ┌─ Lab ready ──────────────────────────────────────────────┐\n",
            f"  │  Rancher URL    {self.rancher_api:<42}│\n",
        ]
        if self.vip:
            lines += [
                f"  │  Harvester URL  https://{self.vip:<38}│\n",
            ]
        lines += [
            "  │                                                          │\n",
            "  │  Username       admin                                    │\n",
        ]
        if self.vip:
            lines.append(f"  │  Harvester pw   {self.harvester_password:<42}│\n")
        lines += [
            f"  │  Rancher pw     {self.rancher_password:<42}│\n",
            "  │                                                          │\n",
            "  │  Passwords also in ~/.rodeo/secrets.yaml                 │\n",
            "  │  and $RANCHER_ADMIN_PASSWORD / $RANCHER_URL              │\n",
        ]
        if not self.vip:
            lines.append(
                "  │  Edge nodes: attach Elemental ISO + start VMs           │\n"
            )
        lines.append("  └──────────────────────────────────────────────────────────┘")
        return "".join(lines)
