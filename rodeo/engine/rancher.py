"""RancherPhase — Python port of the retired setup-rancher.sh deployer script."""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import tempfile
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
    CLUSTER_TIMEOUT = 1800   # cluster Active in Rancher (30 min)

    SSH_POLL     = 10
    K3S_POLL     = 10
    PING_POLL    = 10
    CLUSTER_POLL = 30

    def __init__(self, cfg: dict, stop: threading.Event | None = None) -> None:
        net  = cfg["network"]
        ver  = cfg.get("versions", {})
        cred = cfg.get("credentials", {})

        self.rancher_ip       = net.get("rancher_ip", "192.168.122.9")
        self.vip              = net["vip"]
        self.nodeport         = int(net.get("rancher_nodeport", 30002))
        self.dns_domain       = net.get("dns_domain", "aerogrid.com")
        self.gateway          = net.get("gateway", "192.168.122.1")
        real_harvester        = [n for n in cfg.get("vms", {}) if n != "rancher"]
        # Standalone = a Rancher-only lab (no Harvester to import). When the plan
        # has VMs but none of them are Harvester nodes, stop after configuring the
        # Rancher API and skip import / Harvester password / ISO eject.
        self.standalone       = bool(cfg.get("vms")) and not real_harvester
        self.harvester_nodes  = real_harvester or ["harvester1", "harvester2", "harvester3"]
        self.libvirt_uri      = cfg.get("libvirt", {}).get("uri", "qemu:///system")

        self.rancher_version  = ver.get("rancher", "2.13.1")
        self.k3s_version      = ver.get("k3s", "v1.31.4+k3s1")
        self.cert_mgr_version = ver.get("cert_manager", "v1.16.2")

        key = cfg.get("ssh", {}).get("identity_file")
        if not key:
            key = "/root/.ssh/id_ed25519" if os.geteuid() == 0 else str(Path.home() / ".ssh" / "id_ed25519")
        self.ssh_key = Path(key)
        self.admin_password = cred.get("lab_admin_password", cred.get("harvester_os_password", ""))

        self.rancher_api      = f"https://{self.rancher_ip}:{self.nodeport}"
        self.rancher_hostname = f"rancher.{self.rancher_ip}.sslip.io"

        self.success      = False
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

        yield LogLine(f"Installing Rancher Prime {self.rancher_version} (may take 10+ min)...")
        if not (yield from self._install_rancher()):
            return
        yield LogLine("  Rancher Prime installed.")

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

        if self.standalone:
            yield LogLine(
                f"\n  Rancher URL  : {self.rancher_api}  (NodePort)"
                "\n  Standalone Rancher lab — no Harvester cluster to import."
            )
            self.success = True
            return

        yield LogLine("Importing Harvester cluster into Rancher...")
        if not (yield from self._import_harvester()):
            return
        yield LogLine("  Harvester cluster import started.")

        yield LogLine("Setting Harvester dashboard admin password...")
        yield from self._set_harvester_password()

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

    # ---------- HTTP helpers ----------

    def _ssl_ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

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
        script = (
            "set -euo pipefail\n"
            f'export INSTALL_K3S_VERSION="{self.k3s_version}"\n'
            "curl -sfL https://get.k3s.io"
            " | sh -s - --write-kubeconfig-mode 644 --disable traefik --node-name rancher\n"
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
            "helm repo add rancher-prime https://charts.rancher.com/server-charts/prime\n"
            "helm repo add jetstack https://charts.jetstack.io\n"
            "helm repo update\n"
            f"kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/{v}/cert-manager.crds.yaml\n"
            f"helm install cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --version {v}\n"
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
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f'helm install rancher rancher-prime/rancher'
            f' --namespace cattle-system --create-namespace'
            f' --version "{self.rancher_version}"'
            f' --set hostname="{self.rancher_hostname}"'
            ' --set bootstrapPassword="admin"'
            ' --set replicas=1'
            ' --set ingress.tls.source=rancher'
            ' --wait --timeout 600s\n'
        )
        yield LogLine("  Running helm install rancher (up to 10 min)...")
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

    def _login(self, password: str) -> str:
        """Return a login token for admin@<password>, or '' on failure."""
        try:
            resp = self._http(
                "POST",
                "/v3-public/localProviders/local?action=login",
                {"username": "admin", "password": password},
            )
            return resp.get("token", "")
        except Exception:
            return ""

    def _configure_api(self) -> Generator[DeployEvent, None, bool]:
        # Idempotent: a previous run may have already changed the admin password,
        # so try the bootstrap password first, then the configured one.
        temp_token = self._login("admin")
        on_bootstrap = bool(temp_token)
        if not temp_token:
            temp_token = self._login(self.admin_password)

        if not temp_token:
            self.error = "Rancher login failed (tried bootstrap and configured passwords)"
            yield LogLine(f"  ✗ {self.error}")
            return False

        if on_bootstrap:
            try:
                self._http(
                    "POST",
                    "/v3/users?action=changepassword",
                    {"currentPassword": "admin", "newPassword": self.admin_password},
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
                {"value": self.rancher_api},
                token=self._api_token,
            )
        except Exception as exc:
            yield LogLine(f"  ⚠ server-url set: {exc}")

        try:
            pass_file = Path("/root/rancher-password")
            pass_file.write_text(self.admin_password)
            pass_file.chmod(0o600)
            yield LogLine("  Admin password saved to /root/rancher-password")
        except Exception:
            pass

        return True

    def _import_harvester(self) -> Generator[DeployEvent, None, bool]:
        try:
            resp = self._http(
                "POST",
                "/v3/clusters",
                {
                    "type": "cluster",
                    "name": "harvester",
                    "harvesterConfig": {},
                    "annotations": {
                        "field.cattle.io/description": "Harvester HCI cluster for SUSE Virt Rodeo"
                    },
                },
                token=self._api_token,
            )
            self._cluster_id = resp.get("id", "")
        except Exception as exc:
            self.error = f"Cluster create failed: {exc}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        yield LogLine(f"  Cluster record: {self._cluster_id}")

        try:
            resp = self._http(
                "GET",
                f"/v3/clusterregistrationtokens?clusterId={self._cluster_id}",
                token=self._api_token,
            )
            manifest_url = resp.get("data", [{}])[0].get("manifestUrl", "")
        except Exception as exc:
            self.error = f"Failed to get manifest URL: {exc}"
            yield LogLine(f"  ✗ {self.error}")
            return False

        if not manifest_url:
            self.error = "Manifest URL is empty"
            yield LogLine(f"  ✗ {self.error}")
            return False

        yield from self._patch_coredns()

        yield LogLine("  Applying import manifest to Harvester cluster...")
        r = self._run(
            ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH), "apply", "-f", manifest_url],
            timeout=60,
        )
        if r.returncode != 0:
            self.error = f"kubectl apply manifest failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Import manifest applied.")

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

        fd, tmp_path = tempfile.mkstemp(suffix=".json")
        try:
            os.close(fd)
            with open(tmp_path, "w") as f:
                json.dump(cm, f)
            r2 = self._run(
                ["kubectl", "--kubeconfig", str(KUBECONFIG_PATH), "apply", "-f", tmp_path],
                timeout=30,
            )
            if r2.returncode == 0:
                yield LogLine(f"  CoreDNS patched: {self.dns_domain} -> {dns_server}")
            else:
                yield LogLine(f"  ⚠ CoreDNS patch apply failed: {r2.stderr.strip()}")
        finally:
            os.unlink(tmp_path)

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
                        "newPassword": self.admin_password,
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
                    "password": self.admin_password,
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
