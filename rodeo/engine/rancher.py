"""RancherPhase — Python port of the retired setup-rancher.sh deployer script."""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Generator, Iterator

from .cluster import KUBECONFIG_PATH
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
        self.ui_ext_version          = ver.get("harvester_ui_extension", "1.7.1")
        self.elemental_crds_version  = ver.get("elemental_operator_crds", "1.9.0")
        self.elemental_op_version    = ver.get("elemental_operator", "1.9.0")
        self.elemental_ui_version    = ver.get("elemental_ui_extension", "3.0.2-rc.2")

        self.profile_type = cfg.get("type", "")
        self.harvester_auto_import = cfg.get("harvester_auto_import", True)

        eib_vm = cfg.get("vms", {}).get("eib", {})
        self.eib_ip      = eib_vm.get("ip", "192.168.122.20")
        self.image_dir   = cfg.get("storage", {}).get("image_dir", "/var/lib/libvirt/images")
        eib_def          = cfg.get("eib", {})
        self.eib_image   = eib_def.get("container_image", "registry.suse.com/edge/3.6/edge-image-builder:1.3.3.1")
        self.hauler_version = cfg.get("versions", {}).get("hauler", "1.2.2")
        _hauler_leap_url = "https://download.opensuse.org/distribution/leap-micro/6.2/appliances/openSUSE-Leap-Micro.x86_64-Default-qcow.qcow2"
        self.hauler_base_url = eib_def.get("hauler_base_url", _hauler_leap_url)

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

        Requires all Harvester nodes Ready and KUBECONFIG_PATH to exist.
        Requires setup_done (self._api_token must be set by stream_setup).
        """
        if self.standalone:
            yield LogLine(
                f"\n  Rancher URL  : {self.rancher_api}  (NodePort)"
                "\n  Standalone Rancher lab — no Harvester cluster to import."
            )
            self.success = True
            return

        yield LogLine("Installing Harvester UI Extension...")
        yield from self._install_ui_extension()

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

    def _install_rancher(self) -> Generator[DeployEvent, None, bool]:
        if self.tls_source == "letsEncrypt":
            tls_flags = (
                f' --set ingress.tls.source=letsEncrypt'
                f' --set letsEncrypt.email="{self.letsencrypt_email}"'
                f' --set letsEncrypt.environment=production'
            )
        else:
            tls_flags = ""
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f'helm upgrade --install rancher rancher-prime/rancher'
            f' --namespace cattle-system --create-namespace'
            f' --version "{self.rancher_version}"'
            f' --set hostname="{self.rancher_hostname}"'
            f'{tls_flags}'
            ' --set bootstrapPassword="admin"'
            ' --set replicas=1'
            ' --wait --timeout 600s\n'
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

    def _install_ui_extension(self) -> Iterator[DeployEvent]:
        # Create the UIPlugin resource directly via the Rancher Steve API — no Helm,
        # no SSH, no repo fetch. Endpoint URLs match the values.yaml from the official
        # harvester-ui-extension chart (gh-pages branch, versioned path).
        gh_base = (
            "https://raw.githubusercontent.com/harvester/harvester-ui-extension"
            f"/gh-pages/extensions/harvester/{self.ui_ext_version}"
        )
        uiplugin = {
            "apiVersion": "catalog.cattle.io/v1",
            "kind": "UIPlugin",
            "metadata": {
                "name": "harvester",
                "namespace": "cattle-ui-plugin-system",
            },
            "spec": {
                "plugin": {
                    "name": "harvester",
                    "version": self.ui_ext_version,
                    "endpoint": gh_base,
                    "compressedEndpoint": f"{gh_base}.tgz",
                    "noCache": False,
                    "noAuth": False,
                    "metadata": {
                        "catalog.cattle.io/display-name": "Harvester",
                        "catalog.cattle.io/kube-version": ">= 1.16.0-0",
                        "catalog.cattle.io/rancher-version": ">= 2.14.0-0",
                        "catalog.cattle.io/ui-extensions-version": ">= 3.0.0 < 4.0.0",
                    },
                }
            },
        }
        try:
            self._http(
                "POST", "/v1/catalog.cattle.io.uiplugins",
                data=uiplugin, token=self._api_token,
            )
            yield LogLine(
                f"  Harvester UI Extension {self.ui_ext_version} registered via Rancher API "
                "(Virtualization tab appears after the UI reloads)."
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 409:
                yield LogLine(f"  Harvester UI Extension {self.ui_ext_version} already present.")
            else:
                yield LogLine(
                    f"  ⚠ Harvester UI Extension API call failed ({exc}) — "
                    "install manually: Extensions > Available > Harvester > Install"
                )

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

    def _configure_api(self) -> Generator[DeployEvent, None, bool]:
        # /ping comes up before the auth API is ready — wait for a working login.
        # Idempotent: try bootstrap password first (fresh install), then the
        # configured lab password (password already set on a previous run / upgrade).
        temp_token = ""
        on_bootstrap = False
        t0 = time.monotonic()
        err_bootstrap = err_configured = ""
        while True:
            temp_token, err_bootstrap = self._login("admin")
            if temp_token:
                on_bootstrap = True
                break
            temp_token, err_configured = self._login(self.admin_password)
            if temp_token:
                break
            elapsed = time.monotonic() - t0
            if elapsed >= self.LOGIN_TIMEOUT:
                break
            yield ProgressUpdate("Waiting for Rancher auth API", elapsed, self.LOGIN_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(
                f"  {m:02d}:{s:02d} / {self.LOGIN_TIMEOUT // 60}:00"
                f" — bootstrap: {err_bootstrap} | configured: {err_configured}"
            )
            if self._sleep(self.LOGIN_POLL):
                return False

        if not temp_token:
            self.error = (
                f"Rancher login failed — bootstrap: {err_bootstrap} | configured: {err_configured}"
            )
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
        """Ensure the cacerts Setting matches the actual serving CA from K3s.

        Rancher's Helm upgrade rotates tls-rancher-internal-ca but preserves the
        existing cacerts Setting.  The mismatch causes cattle-cluster-agent to
        fail TLS verification (ECDSA failure: old CA in manifest vs new CA on
        the wire).  Fix: patch the Setting directly via kubectl when they diverge.
        """
        if self.standalone:
            return

        r = self._ssh_script(
            "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"
            " get secret tls-rancher-internal-ca -n cattle-system"
            " -o jsonpath='{.data.tls\\.crt}' 2>/dev/null | base64 -d",
            timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return  # nothing to sync (self-signed cert or secret absent)

        serving_ca = r.stdout.strip()

        try:
            current = self._http("GET", "/v3/settings/cacerts", token=self._api_token)
            api_ca = (current.get("value") or "").strip()
        except Exception:
            return

        if api_ca == serving_ca:
            return  # already in sync

        # cacerts is read-only via the REST API; patch the K8s resource directly.
        patch_script = (
            "set -euo pipefail\n"
            "CA=$(kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"
            " get secret tls-rancher-internal-ca -n cattle-system"
            " -o jsonpath='{.data.tls\\.crt}' | base64 -d)\n"
            "VALUE=$(python3 -c \"import sys,json; print(json.dumps(sys.stdin.read().rstrip()))\" <<< \"$CA\")\n"
            "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"
            " patch setting cacerts"
            " --type=merge -p \"{\\\"value\\\": $VALUE}\" 2>&1\n"
        )
        r2 = self._ssh_script(patch_script, timeout=20)
        if r2.returncode == 0:
            yield LogLine("  cacerts synced with serving CA.")
        else:
            yield LogLine(f"  ⚠ cacerts sync: {r2.stderr.strip()[:120]}")

    def _import_harvester(self) -> Generator[DeployEvent, None, bool]:
        # Use the provisioning.cattle.io/v1 Cluster API — the documented import path
        # per https://docs.harvesterhci.io/v1.8/rancher/virtualization-management
        # agentEnvVars is included for future Rancher versions; in 2.14.x it does not
        # propagate to cattle-cluster-agent for imported (generic) clusters. TLS is
        # handled correctly because server-url uses the sslip.io hostname that matches
        # the Rancher TLS cert's CN/SAN — no bypass needed.
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

        if not KUBECONFIG_PATH.exists():
            self.error = (
                f"Harvester kubeconfig not found at {KUBECONFIG_PATH} — "
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
            ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH), "apply", "-f", "-"],
            timeout=30,
            input=setting_manifest,
        )
        if r.returncode != 0:
            self.error = f"cluster-registration-url apply failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Registration URL applied — Harvester will deploy the cluster agent.")

        try:
            kube_dir = Path("/root/.kube")
            kube_dir.mkdir(parents=True, exist_ok=True)
            dest = kube_dir / "harvester.yaml"
            dest.write_text(KUBECONFIG_PATH.read_text())
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

    def _patch_coredns(self) -> Iterator[DeployEvent]:
        dns_server = self.gateway
        cm_name = None
        for candidate in ("rke2-coredns-rke2-coredns", "coredns"):
            r = self._run(
                [
                    "kubectl", "--kubeconfig", str(KUBECONFIG_PATH),
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
                "kubectl", "--kubeconfig", str(KUBECONFIG_PATH),
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
            ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH), "apply", "-f", "-"],
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
            if not (yield from self._install_elemental_ui()):
                return False
            if not (yield from self._create_machine_registrations()):
                return False
            if not (yield from self._populate_hauler()):
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

    def _install_elemental_ui(self) -> Generator[DeployEvent, None, bool]:
        """Install the Elemental UI extension into Rancher via Helm."""
        # Install directly from the chart tarball URL: the rancher/elemental-ui
        # gh-pages Helm index only publishes up to 1.2.0; 3.x builds are in the
        # assets/ directory but not listed in the index. Direct URL avoids the
        # repo + version lookup entirely and works for any version including RCs.
        chart_url = (
            f"https://raw.githubusercontent.com/rancher/elemental-ui"
            f"/gh-pages/assets/elemental/elemental-{self.elemental_ui_version}.tgz"
        )
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"helm upgrade --install elemental-ui '{chart_url}'"
            f" --namespace cattle-ui-plugin-system --create-namespace"
            f" --wait --timeout 2m\n"
        )
        yield LogLine(f"Installing Elemental UI extension {self.elemental_ui_version}...")
        r = self._ssh_script(script, timeout=180)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip() and "elphelming" not in line.lower():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Elemental UI extension install failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Elemental UI extension installed.")
        return True

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

        script = (
            "set -euo pipefail\n"
            "STORE=/var/lib/hauler\n"
            "HAULER=/usr/local/bin/hauler\n\n"
            # Mirror the EIB container image into Hauler so participants can run
            # EIB without internet access from the eib VM.
            f'$HAULER store add image "{self.eib_image}" --store $STORE\n'
            # Elemental register agent — EIB embeds this into the edge node image
            # so nodes can phone home to the Elemental Operator on first boot.
            f'$HAULER store add image "registry.suse.com/rancher/elemental-register:{self.elemental_op_version}" --store $STORE\n'
            # Alien-Geeko demo app image — Fleet deploys this to edge clusters;
            # edge nodes pull from Hauler via k3s registry mirror (docker.io → eib:5000).
            '$HAULER store add image "docker.io/avaleror/alien-geeko:latest" --store $STORE\n'
            # openSUSE Leap Micro 6.2 base image — EIB input; served on port 8080
            # so participants can download it with: curl http://localhost:8080/<filename>
            f'$HAULER store add file "{self.hauler_base_url}" --store $STORE\n\n'
            # Enable and start Hauler services (service units written by cloud-init)
            "systemctl daemon-reload\n"
            "systemctl enable --now hauler-registry.service hauler-fileserver.service\n\n"
            # Pre-stage EIB assets for participants: definition template + k3s registry mirror script
            "mkdir -p /home/eib-config/scripts /home/eib-config/base-images /home/eib-output\n"
            # qemu-img is needed to convert the QCOW2 base image to RAW before EIB can use it.
            # EIB's modify-raw-image.sh calls guestfish with --format=raw, so it needs a true RAW input.
            "zypper install -y qemu-tools 2>&1 | tail -3\n"
            # Download the base QCOW2 from the Hauler fileserver (already running on port 8080)
            # and convert it to RAW — EIB 1.3.x requires a raw disk image as input.
            f"QCOW=/home/eib-config/base-images/openSUSE-Leap-Micro.x86_64-Default-qcow.qcow2\n"
            f"RAW=/home/eib-config/base-images/openSUSE-Leap-Micro.x86_64-Default.raw\n"
            f'curl -fsSL "http://localhost:8080/openSUSE-Leap-Micro.x86_64-Default-qcow.qcow2" -o "$QCOW"\n'
            'qemu-img convert -f qcow2 -O raw "$QCOW" "$RAW"\n'
            'rm -f "$QCOW"\n\n'
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
            "  baseImage: openSUSE-Leap-Micro.x86_64-Default.raw\n"
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
        """Create a Fleet GitRepo for the Alien-Geeko demo app.

        Alien-Geeko (https://github.com/SUSE-Technical-Marketing/Alien-Geeko) is a
        Node.js CRT terminal web app showing Kubernetes cluster vitals. Fleet deploys
        it to any downstream cluster labelled demo=true + edge-type=x86-cluster.

        Participants label their edge cluster after Elemental registers + provisions it.
        The GitRepo is ready in advance so deployment kicks in the moment the label appears.
        """
        manifest = (
            "apiVersion: fleet.cattle.io/v1alpha1\n"
            "kind: GitRepo\n"
            "metadata:\n"
            "  name: alien-geeko\n"
            "  namespace: fleet-default\n"
            "spec:\n"
            "  repo: https://github.com/SUSE-Technical-Marketing/Alien-Geeko.git\n"
            "  branch: main\n"
            "  targets:\n"
            "    - name: x86-edge-clusters\n"
            "      clusterSelector:\n"
            "        matchLabels:\n"
            '          demo: "true"\n'
            "          edge-type: x86-cluster\n"
        )
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"cat <<'__GITREPO__' | kubectl apply -f -\n"
            f"{manifest}"
            "__GITREPO__\n"
        )
        yield LogLine("Creating Fleet GitRepo for Alien-Geeko demo app...")
        r = self._ssh_script(script, timeout=30)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Fleet GitRepo creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            "  Fleet GitRepo 'alien-geeko' created in fleet-default.\n"
            "  To deploy: label an edge cluster with  demo=true  edge-type=x86-cluster\n"
            "  Image served from Hauler: http://192.168.122.20:5000 (docker.io mirror)"
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
            fallback = Path.home() / ".rodeo" / "rodeo.env"
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
