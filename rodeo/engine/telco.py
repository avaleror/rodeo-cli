"""TelcoPhase: SUSE Telco Cloud 3.7 management cluster on the mgmt VM.

Reuses RancherPhase for everything Rancher (Helm, cert-manager, Rancher Prime,
NodePort, /ping, admin password, server-url, CA sync) by pointing its remote
kubectl calls at the RKE2 kubeconfig. Adds what the Telco stack needs on top:
RKE2 instead of K3s, Metal3 (Ironic + baremetal-operator), the Rancher Turtles
providers chart, and the image cache that downstream Metal3MachineTemplates
download from (http://imagecache.local:8080).

Install steps follow the SUSE Telco Cloud 3.7 Metal3 quickstart, single-node
configuration: Ironic on the mgmt IP through a NodePort, no MetalLB, and
Ironic's shared volume on emptyDir (the chart's default when no size is set).
"""
from __future__ import annotations

import shlex
import threading
import time
from typing import Generator, Iterator

import yaml

from .rancher import RancherPhase
from .runner import DeployEvent, LogLine, ProgressUpdate

__all__ = ["TelcoPhase"]

# The nginx image the 3.7 Metal3 chart pins for its own media server.
_IMAGE_CACHE_IMAGE = "registry.suse.com/suse/nginx:1.21"
# Same directory the Metal3 chart's media server uses on the node.
_IMAGE_CACHE_DIR = "/opt/media"
_CHARTS = "oci://registry.suse.com/edge/charts"


class TelcoPhase(RancherPhase):
    """Boot mgmt and install the Telco Cloud management stack on it."""

    KUBECONFIG = "/etc/rancher/rke2/rke2.yaml"

    RKE2_TIMEOUT = 900       # RKE2 node Ready (15 min, includes image pulls)
    ROLLOUT_TIMEOUT = 900    # Metal3 / CAPI controllers Available (15 min)
    ROLLOUT_POLL = 15

    # Namespaces the providers chart populates (3.7 quickstart, 4.4.3).
    CAPI_NAMESPACES = (
        "cattle-capi-system",
        "capm3-system",
        "rke2-bootstrap-system",
        "rke2-control-plane-system",
    )

    def __init__(self, cfg: dict, stop: threading.Event | None = None) -> None:
        super().__init__(cfg, stop=stop)
        ver = cfg.get("versions", {})
        telco = cfg.get("telco", {})
        self.rke2_version = ver.get("rke2", "v1.36.3+rke2r1")
        self.metal3_version = ver.get("metal3", "")
        self.turtles_providers_version = ver.get("turtles_providers", "")
        self.mgmt_hostname = "mgmt"
        self.site_names = list(telco.get("sites", {}))
        cache = telco.get("image_cache", {})
        self.image_cache_port = int(cache.get("port", 8080))
        self.image_cache_hostname = cache.get("hostname", "imagecache.local")
        self.dns_domain = cfg.get("network", {}).get("dns_domain", "rodeo.lab")
        self.success = False

    # ---------- boot ----------

    def stream_boot(self) -> Iterator[DeployEvent]:
        """Start mgmt only. Site hosts stay off: Metal3 owns their power."""
        from .libvirt import LibvirtDriver

        try:
            with LibvirtDriver(self.libvirt_uri) as lv:
                yield LogLine("Ensuring libvirt network (virbr0) is up...")
                try:
                    lv.net_start("default")
                    lv.net_set_autostart("default", True)
                except Exception as exc:
                    yield LogLine(f"  ⚠  network start: {exc}")
                for name in self.site_names:
                    yield LogLine(f"  {name}: left powered off for Metal3.")
                info = lv.get_vm(self.mgmt_hostname)
                if info.state == "not found":
                    self.error = "mgmt domain not found, was the vms phase completed?"
                    yield LogLine(f"  ✗ {self.error}")
                    return
                if info.state == "running":
                    yield LogLine("  mgmt: already running.")
                else:
                    lv.start(self.mgmt_hostname)
                    yield LogLine("  mgmt: started (first boot runs combustion).")
        except Exception as exc:
            self.error = f"libvirt: {exc}"
            yield LogLine(f"  ✗ {self.error}")
            return

        yield LogLine(f"Waiting for mgmt SSH at {self.rancher_ip}...")
        if not (yield from self._wait_ssh()):
            self.error = f"mgmt SSH not reachable after {self.SSH_TIMEOUT // 60} min"
            yield LogLine(f"  ✗ {self.error}")
            return
        yield LogLine("  SSH is up.")
        self.success = True

    # ---------- management stack ----------

    def stream_mgmt(self) -> Iterator[DeployEvent]:
        """RKE2, Rancher Prime, Metal3, CAPI providers and the image cache."""
        steps = [
            (f"Waiting for mgmt SSH at {self.rancher_ip}", self._wait_ssh),
            (f"Installing RKE2 {self.rke2_version}", self._install_rke2),
            ("Waiting for the RKE2 node to be Ready", self._wait_rke2_ready),
            ("Installing Helm", self._install_helm),
            (f"Installing cert-manager {self.cert_mgr_version}", self._install_cert_manager),
            (f"Installing Rancher Prime {self.rancher_version} (may take 10+ min)", self._install_rancher),
            (f"Exposing Rancher on NodePort {self.nodeport}", self._expose_nodeport),
            (f"Waiting for Rancher /ping on {self.rancher_api}", self._wait_ping),
            ("Configuring Rancher admin password and server-url", self._configure_api),
            (f"Installing Metal3 {self.metal3_version}", self._install_metal3),
            (f"Installing the Rancher Turtles providers {self.turtles_providers_version}",
             self._install_capi_providers),
            (f"Starting the image cache on :{self.image_cache_port}", self._install_image_cache),
        ]
        for title, step in steps:
            yield LogLine(f"{title}...")
            if not (yield from step()):
                self.error = self.error or f"{title} failed"
                yield LogLine(f"  ✗ {self.error}")
                return
            yield LogLine("  done.")
        self.setup_done = True
        self.success = True

    # ---------- steps ----------

    def _script_step(self, script: str, timeout: int, error: str) -> Generator[DeployEvent, None, bool]:
        r = self._ssh_script(script, timeout=timeout)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = error
            return False
        return True

    def rke2_config(self) -> dict:
        return {
            "cni": "cilium",
            "write-kubeconfig-mode": "0600",
            "node-name": self.mgmt_hostname,
            "tls-san": [
                self.rancher_ip,
                self.mgmt_hostname,
                f"{self.mgmt_hostname}.{self.dns_domain}",
            ],
        }

    def _install_rke2(self) -> Generator[DeployEvent, None, bool]:
        # SL Micro is transactional: get.rke2.io picks the tarball method there and,
        # because /usr/local is its own subvolume, installs to /opt/rke2 with the
        # units in /etc/systemd/system. No transactional-update, no reboot.
        config = yaml.safe_dump(self.rke2_config(), default_flow_style=False, sort_keys=False)
        script = (
            "set -euo pipefail\n"
            "mkdir -p /etc/rancher/rke2\n"
            "cat > /etc/rancher/rke2/config.yaml <<'RODEO_RKE2_EOF'\n"
            f"{config}"
            "RODEO_RKE2_EOF\n"
            "if ! systemctl is-active --quiet rke2-server; then\n"
            f"  curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION={shlex.quote(self.rke2_version)} sh -\n"
            "  systemctl enable --now rke2-server.service\n"
            "fi\n"
        )
        return (yield from self._script_step(script, 900, "RKE2 install failed"))

    def _wait_rke2_ready(self) -> Generator[DeployEvent, None, bool]:
        # Plain `ssh root@mgmt kubectl ...` must work for the students (labs 01-03),
        # so link kubectl into PATH and give root a default kubeconfig.
        script = (
            "set -euo pipefail\n"
            f"test -s {self.KUBECONFIG}\n"
            "test -x /var/lib/rancher/rke2/bin/kubectl\n"
            "ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl\n"
            f"install -d -m 700 /root/.kube && install -m 600 {self.KUBECONFIG} /root/.kube/config\n"
            f"kubectl --kubeconfig={self.KUBECONFIG} get node {self.mgmt_hostname} --no-headers"
            " | awk '{print $2}'\n"
        )
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            r = self._ssh_script(script, timeout=30)
            if r.returncode == 0 and r.stdout.strip() == "Ready":
                yield ProgressUpdate("RKE2 node Ready", elapsed, self.RKE2_TIMEOUT)
                return True
            if elapsed >= self.RKE2_TIMEOUT:
                self.error = "RKE2 node never became Ready"
                return False
            yield ProgressUpdate("RKE2 node Ready", elapsed, self.RKE2_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.RKE2_TIMEOUT // 60}:00, waiting for RKE2...")
            if self._sleep(self.K3S_POLL):
                return False

    def metal3_values(self) -> dict:
        # Single-node configuration from the 3.7 quickstart (4.7.1): Ironic on the
        # mgmt IP via NodePort, so no MetalLB floating IP is needed.
        return {
            "global": {"ironicIP": self.rancher_ip},
            "metal3-ironic": {"service": {"type": "NodePort"}},
        }

    def _install_metal3(self) -> Generator[DeployEvent, None, bool]:
        values = yaml.safe_dump(self.metal3_values(), default_flow_style=False, sort_keys=False)
        script = (
            "set -euo pipefail\n"
            f"export KUBECONFIG={self.KUBECONFIG}\n"
            "cat > /root/metal3-values.yaml <<'RODEO_METAL3_EOF'\n"
            f"{values}"
            "RODEO_METAL3_EOF\n"
            f"helm upgrade --install metal3 {_CHARTS}/metal3"
            f" --version {shlex.quote(self.metal3_version)}"
            " --namespace metal3-system --create-namespace"
            " -f /root/metal3-values.yaml\n"
        )
        if not (yield from self._script_step(script, 600, "Metal3 install failed")):
            return False
        return (yield from self._wait_rollout(["metal3-system"], "Metal3"))

    def _install_capi_providers(self) -> Generator[DeployEvent, None, bool]:
        # Turtles itself ships inside Rancher (enabled by default since 2.13). The
        # providers chart adds CAPI core, CAPM3 and the RKE2 bootstrap and
        # control-plane providers through the CAPIProvider API.
        script = (
            "set -euo pipefail\n"
            f"export KUBECONFIG={self.KUBECONFIG}\n"
            f"helm upgrade --install rancher-turtles {_CHARTS}/rancher-turtles-providers"
            f" --version {shlex.quote(self.turtles_providers_version)}"
            " --namespace cattle-turtles-system --create-namespace\n"
        )
        if not (yield from self._script_step(script, 600, "Rancher Turtles providers install failed")):
            return False
        return (yield from self._wait_rollout(list(self.CAPI_NAMESPACES), "CAPI providers"))

    def image_cache_manifest(self) -> str:
        """nginx on mgmt:<port> serving /opt/media, the Metal3 media directory."""
        conf = (
            "worker_processes 1;\n"
            "pid /run/nginx.pid;\n"
            "error_log stderr;\n"
            "events {}\n"
            "http {\n"
            "  default_type application/octet-stream;\n"
            "  access_log /dev/stdout;\n"
            "  sendfile on;\n"
            "  client_body_temp_path /run/client_body;\n"
            "  proxy_temp_path /run/proxy;\n"
            "  fastcgi_temp_path /run/fastcgi;\n"
            "  uwsgi_temp_path /run/uwsgi;\n"
            "  scgi_temp_path /run/scgi;\n"
            "  server {\n"
            f"    listen {self.image_cache_port};\n"
            "    root /srv/www/htdocs;\n"
            "    autoindex on;\n"
            "  }\n"
            "}\n"
        )
        labels = {"app.kubernetes.io/name": "imagecache"}
        docs = [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "imagecache"}},
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "imagecache-nginx", "namespace": "imagecache"},
                "data": {"nginx.conf": conf},
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "imagecache", "namespace": "imagecache", "labels": labels},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": labels},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            # Host network: the site hosts reach it as
                            # imagecache.local:<port> without a NodePort or ingress.
                            "hostNetwork": True,
                            "securityContext": {"runAsUser": 499, "runAsNonRoot": True},
                            "containers": [{
                                "name": "nginx",
                                "image": _IMAGE_CACHE_IMAGE,
                                "args": ["nginx", "-g", "daemon off;", "-c", "/etc/rodeo-nginx/nginx.conf"],
                                "ports": [{"containerPort": self.image_cache_port, "name": "http"}],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "readinessProbe": {
                                    "tcpSocket": {"port": self.image_cache_port},
                                    "periodSeconds": 5,
                                },
                                "volumeMounts": [
                                    {"name": "media", "mountPath": "/srv/www/htdocs", "readOnly": True},
                                    {"name": "conf", "mountPath": "/etc/rodeo-nginx"},
                                    {"name": "run", "mountPath": "/run"},
                                ],
                            }],
                            "volumes": [
                                {"name": "media", "hostPath": {"path": _IMAGE_CACHE_DIR, "type": "DirectoryOrCreate"}},
                                {"name": "conf", "configMap": {"name": "imagecache-nginx"}},
                                {"name": "run", "emptyDir": {"sizeLimit": "10Mi"}},
                            ],
                        },
                    },
                },
            },
        ]
        return yaml.safe_dump_all(docs, default_flow_style=False, sort_keys=False)

    def _install_image_cache(self) -> Generator[DeployEvent, None, bool]:
        script = (
            "set -euo pipefail\n"
            f"export KUBECONFIG={self.KUBECONFIG}\n"
            f"install -d -m 755 {_IMAGE_CACHE_DIR}\n"
            "kubectl apply -f - <<'RODEO_IMAGECACHE_EOF'\n"
            f"{self.image_cache_manifest()}"
            "RODEO_IMAGECACHE_EOF\n"
            "kubectl -n imagecache rollout status deployment/imagecache --timeout=300s\n"
            f"curl -sf -o /dev/null http://127.0.0.1:{self.image_cache_port}/\n"
        )
        return (yield from self._script_step(script, 420, "image cache did not start"))

    def _wait_rollout(self, namespaces: list[str], label: str) -> Generator[DeployEvent, None, bool]:
        """Wait until every Deployment in the namespaces exists and is Available."""
        ns = " ".join(shlex.quote(n) for n in namespaces)
        script = (
            f"export KUBECONFIG={self.KUBECONFIG}\n"
            f"for ns in {ns}; do\n"
            '  n=$(kubectl -n "$ns" get deploy --no-headers 2>/dev/null | wc -l)\n'
            '  [ "$n" -gt 0 ] || { echo "$ns: no deployments yet"; exit 1; }\n'
            '  kubectl -n "$ns" wait --for=condition=Available deploy --all --timeout=5s >/dev/null 2>&1'
            ' || { echo "$ns: not Available yet"; exit 1; }\n'
            "done\n"
        )
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            r = self._ssh_script(script, timeout=60)
            if r.returncode == 0:
                yield ProgressUpdate(f"{label} Available", elapsed, self.ROLLOUT_TIMEOUT)
                return True
            if elapsed >= self.ROLLOUT_TIMEOUT:
                self.error = f"{label} not Available after {self.ROLLOUT_TIMEOUT // 60} min: {r.stdout.strip()}"
                return False
            yield ProgressUpdate(f"{label} Available", elapsed, self.ROLLOUT_TIMEOUT)
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d} / {self.ROLLOUT_TIMEOUT // 60}:00, {r.stdout.strip()}")
            if self._sleep(self.ROLLOUT_POLL):
                return False
