"""Harvester import into Rancher, CA fixes, dashboard password, CDROM eject."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Generator, Iterator

from ...paths import harvester_kubeconfig_path
from ..runner import DeployEvent, LogLine, ProgressUpdate


class HarvesterMixin:
    """Everything specific to managing the imported Harvester cluster."""

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

    def _harvester_login(self, password: str) -> tuple[str, str]:
        """Return (token, error) logging into the Harvester dashboard API at self.vip."""
        try:
            req = urllib.request.Request(
                f"https://{self.vip}/v3-public/localProviders/local?action=login",
                data=json.dumps({"username": "admin", "password": password}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=self._ssl_ctx(), timeout=30) as resp:
                token = json.loads(resp.read()).get("token", "")
            return token, ("" if token else "200 OK but no token in response")
        except Exception as exc:
            return "", str(exc)

    def _set_harvester_password(self) -> Iterator[DeployEvent]:
        """Move Harvester's dashboard admin off the admin/admin bootstrap and onto
        harvester_admin_password (secrets.yaml), polling until the API answers.

        Right after nodes go Ready (and, if auto-import ran, right after the
        cluster-registration-url import) the Harvester API server can still be
        mid-restart for a short window — a single immediate attempt is not
        reliable, especially on slower nested-KVM hosts like Instruqt.
        """
        # Try passwords in order: configured (secrets.yaml) first (succeeds on
        # re-runs), then the last password we know we set (handles a redeploy after
        # secrets.yaml was regenerated — the live password is still the old one),
        # then the admin/admin bootstrap default. Poll all of them until one responds.
        self.harvester_password_error = ""
        persisted_pw = self._read_persisted_password(self.HARVESTER_PW_FILE)
        candidates = [pw for pw in dict.fromkeys([self.harvester_password, persisted_pw, "admin"]) if pw]

        token = ""
        current_pw = ""
        last_errors: dict[str, str] = {}
        t0 = time.monotonic()
        while True:
            for pw in candidates:
                token, err = self._harvester_login(pw)
                if token:
                    current_pw = pw
                    break
                last_errors[pw] = err

            if token:
                break

            elapsed = time.monotonic() - t0
            if elapsed >= self.HARVESTER_PW_TIMEOUT:
                break
            m, s = divmod(int(elapsed), 60)
            errs = " | ".join(f"{pw[:8]}…: {e}" for pw, e in last_errors.items())
            yield LogLine(
                f"  {m:02d}:{s:02d} / {self.HARVESTER_PW_TIMEOUT // 60}:00 "
                f"— waiting for Harvester dashboard API — {errs}"
            )
            if self._sleep(self.HARVESTER_PW_POLL):
                self.harvester_password_error = "cancelled"
                return

        if not token:
            errs = " | ".join(f"{pw[:8]}…: {e}" for pw, e in last_errors.items())
            self.harvester_password_error = f"login failed after {self.HARVESTER_PW_TIMEOUT // 60} min — {errs}"
            yield LogLine(f"  ⚠ Harvester login failed after {self.HARVESTER_PW_TIMEOUT // 60} min — {errs}")
            return

        on_bootstrap = (current_pw != self.harvester_password)
        if on_bootstrap:
            try:
                req = urllib.request.Request(
                    f"https://{self.vip}/v3/users?action=changepassword",
                    data=json.dumps({
                        "currentPassword": current_pw,
                        "newPassword": self.harvester_password,
                    }).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, context=self._ssl_ctx(), timeout=30)
                yield LogLine("  Harvester admin password set.")
            except Exception as exc:
                self.harvester_password_error = f"password change failed: {exc}"
                yield LogLine(f"  ⚠ Harvester password change: {exc}")
                return
            # Re-login with the new password to fetch the API token below.
            token, err = self._harvester_login(self.harvester_password)
            if not token:
                self.harvester_password_error = f"re-login after password change failed: {err}"
                yield LogLine(f"  ⚠ Re-login after password change failed: {err}")
                return
        else:
            yield LogLine("  Harvester admin password already set — skipping change.")

        try:
            self.HARVESTER_PW_FILE.write_text(self.harvester_password)
            self.HARVESTER_PW_FILE.chmod(0o600)
            yield LogLine(f"  Admin password saved to {self.HARVESTER_PW_FILE}")
        except Exception:
            pass

        try:
            token_file = Path("/root/harvester-token")
            token_file.write_text(token)
            token_file.chmod(0o600)
            yield LogLine("  Harvester API token saved to /root/harvester-token")
        except Exception as exc:
            yield LogLine(f"  ⚠ Harvester token save: {exc}")

    def _eject_cdroms(self) -> Iterator[DeployEvent]:
        """Eject installer/config ISOs from Harvester VMs (best effort, respects cancellation).

        Prefers LibvirtDriver.eject_media (with cfg uri); falls back to virsh -c uri.
        Derives nodes from cfg (still name-based filter for now; see roadmap).
        """
        if self._stop.is_set():
            return

        # Prefer libvirt-python (no shell, uses configured URI)
        try:
            from ..libvirt import LibvirtDriver
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
