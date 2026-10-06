"""Rancher-provisioned downstream K3s/RKE2 clusters on lab VMs.

Rancher creates a custom cluster record per entry in ``downstream_clusters``;
each node then runs that cluster's registration command (rancher-system-agent),
and Rancher installs K3s or RKE2 on it. Every node gets all three roles
(etcd, control plane, worker), so a 3-node cluster is a 3-member etcd HA.
"""
from __future__ import annotations

import json
import shlex
import time
from typing import Generator

from ...ssh import ssh_opts
from ..runner import DeployEvent, LogLine, ProgressUpdate

_K3S_KUBECONFIG = "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
_DISTROS = ("k3s", "rke2")


class DownstreamMixin:
    """Create downstream clusters in Rancher and register their nodes."""

    DOWNSTREAM_TIMEOUT = 2700  # every cluster Ready (45 min; 3-node RKE2 joins one by one)
    DOWNSTREAM_POLL = 20
    REGISTRATION_TIMEOUT = 900  # cluster ID, CAPI cluster, registration command (15 min)

    def _downstream_version(self, distro: str) -> str:
        return self.k3s_downstream_version if distro == "k3s" else self.rke2_downstream_version

    def _node_ssh_script(self, ip: str, script: str, timeout: int = 120):
        return self._run(
            ["ssh", "-i", str(self.ssh_key), *ssh_opts(), f"root@{ip}", "bash", "-s"],
            timeout=timeout, input=script,
        )

    def _validate_downstream(self) -> str:
        """Return an error message, or '' when every cluster is well formed."""
        seen: set[str] = set()
        for cluster in self.downstream_clusters:
            name = cluster.get("name", "")
            distro = cluster.get("distro", "")
            nodes = cluster.get("nodes") or []
            if not name:
                return "a downstream cluster has no name"
            if distro not in _DISTROS:
                return f"downstream cluster '{name}': distro must be one of {list(_DISTROS)}, got {distro!r}"
            if not nodes:
                return f"downstream cluster '{name}' has no nodes"
            for node in nodes:
                if not self.vm_ips.get(node):
                    return f"downstream cluster '{name}': node '{node}' is not a lab VM with an IP"
                if node in seen:
                    return f"node '{node}' is in more than one downstream cluster"
                seen.add(node)
        return ""

    def stream_downstream_clusters(self) -> Generator[DeployEvent, None, bool]:
        """Create every downstream cluster, register its nodes, wait until all are Ready."""
        if not self.downstream_clusters:
            yield LogLine("No downstream clusters in this topology.")
            return True

        err = self._validate_downstream()
        if err:
            self.error = err
            yield LogLine(f"  ✗ {err}")
            return False

        for cluster in self.downstream_clusters:
            name, distro = cluster["name"], cluster["distro"]
            version = self._downstream_version(distro)
            yield LogLine(f"Creating {distro.upper()} cluster '{name}' ({version}) in Rancher...")
            command = yield from self._create_downstream_cluster(name, version)
            if not command:
                return False
            for node in cluster["nodes"]:
                if not (yield from self._register_downstream_node(name, node, command)):
                    return False

        return (yield from self._wait_downstream_ready())

    def _create_downstream_cluster(self, name: str, version: str) -> Generator[DeployEvent, None, str]:
        """Apply the cluster record and return its registration command ('' on failure)."""
        manifest = json.dumps({
            "apiVersion": "provisioning.cattle.io/v1",
            "kind": "Cluster",
            "metadata": {
                "name": name,
                "namespace": "fleet-default",
                "annotations": {"field.cattle.io/description": "rodeo downstream cluster"},
            },
            # An empty rkeConfig makes this a custom cluster: Rancher provisions
            # K3s or RKE2 (picked by the version suffix) on registered nodes.
            "spec": {"kubernetesVersion": version, "rkeConfig": {}},
        })
        r = self._ssh_script(
            "set -euo pipefail\n"
            + _K3S_KUBECONFIG
            + "kubectl apply -f - <<'__MANIFEST__'\n"
            f"{manifest}\n"
            "__MANIFEST__\n",
            timeout=30,
        )
        if r.returncode != 0:
            self.error = f"cluster '{name}' create failed: {r.stderr.strip()}"
            yield LogLine(f"  ✗ {self.error}")
            return ""

        # Rancher assigns the c-m-xxxxx ID, then creates default-token in that
        # namespace. insecureNodeCommand skips the CA checksum: Rancher's cert
        # is self-signed in this lab and the agent pins the CA it downloads.
        # Two Rancher 2.15 behaviours, both found live:
        #  - the commands carry a literal {token}; the token itself is in the
        #    Secret named by status.tokenSecretName (key "token"). Substituted
        #    here, on the Rancher VM, so it never reaches the log.
        #  - CAPI (cattle-capi-system) starts minutes after Rancher answers
        #    /ping. Until its Cluster object exists, every node registration
        #    gets "500 machine not found by request", so wait for it first.
        name_q = shlex.quote(name)
        query = (
            _K3S_KUBECONFIG
            + f"id=$(kubectl get clusters.provisioning.cattle.io {name_q} -n fleet-default"
            " -o jsonpath='{.status.clusterName}' 2>/dev/null)\n"
            '[ -n "$id" ] || exit 0\n'
            f"kubectl get clusters.cluster.x-k8s.io {name_q} -n fleet-default >/dev/null 2>&1 || exit 0\n"
            'crt="clusterregistrationtokens.management.cattle.io/default-token"\n'
            'cmd=$(kubectl get "$crt" -n "$id" -o jsonpath=\'{.status.insecureNodeCommand}\' 2>/dev/null)\n'
            'case "$cmd" in\n'
            "  *'{token}'*)\n"
            '    sec=$(kubectl get "$crt" -n "$id" -o jsonpath=\'{.status.tokenSecretName}\' 2>/dev/null)\n'
            '    [ -n "$sec" ] || exit 0\n'
            '    tok=$(kubectl get secret "$sec" -n "$id" -o jsonpath=\'{.data.token}\' 2>/dev/null | base64 -d)\n'
            '    [ -n "$tok" ] || exit 0\n'
            '    cmd=${cmd//\\{token\\}/$tok} ;;\n'
            "esac\n"
            'printf \'%s\\n\' "$cmd"\n'
        )
        t0 = time.monotonic()
        last_log = 0.0
        while True:
            r = self._ssh_script(query, timeout=20)
            command = r.stdout.strip()
            if r.returncode == 0 and command.startswith("curl"):
                yield LogLine("  Cluster record ready.")
                return command
            elapsed = time.monotonic() - t0
            if elapsed >= self.REGISTRATION_TIMEOUT:
                self.error = f"cluster '{name}': no registration command after {self.REGISTRATION_TIMEOUT // 60} min"
                yield LogLine(f"  ✗ {self.error}")
                return ""
            if elapsed - last_log >= 30:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(f"  {m:02d}:{s:02d}  waiting for the registration command...")
                last_log = elapsed
            if self._sleep(10):
                return ""

    def _register_downstream_node(self, cluster: str, node: str, command: str) -> Generator[DeployEvent, None, bool]:
        ip = self.vm_ips[node]
        yield LogLine(f"  {node} ({ip}): waiting for SSH...")
        t0 = time.monotonic()
        while self._node_ssh_script(ip, "true", timeout=15).returncode != 0:
            if time.monotonic() - t0 >= self.SSH_TIMEOUT:
                self.error = f"{node}: SSH not reachable after {self.SSH_TIMEOUT // 60} min"
                yield LogLine(f"  ✗ {self.error}")
                return False
            if self._sleep(self.SSH_POLL):
                return False

        # Re-runs converge: a node whose agent is already running is registered.
        r = self._node_ssh_script(ip, "systemctl is-active --quiet rancher-system-agent", timeout=15)
        if r.returncode == 0:
            yield LogLine(f"  {node}: already registered.")
            return True

        roles = "--etcd --controlplane --worker"
        script = (
            "set -euo pipefail\n"
            f"{command} {roles} --node-name {shlex.quote(node)}\n"
        )
        yield LogLine(f"  {node}: registering with '{cluster}' (etcd, control plane, worker)...")
        # The command carries the cluster token: it travels on stdin, never argv or the log.
        r = self._node_ssh_script(ip, script, timeout=300)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
            for line in tail:
                yield LogLine(f"    {line}")
            self.error = f"{node}: registration with '{cluster}' failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(f"  {node}: agent installed.")
        return True

    def _wait_downstream_ready(self) -> Generator[DeployEvent, None, bool]:
        """Wait until every cluster is Ready with all of its nodes.

        The provisioning Cluster's status.ready turns true as soon as the
        first control plane node is up (seen live: rke2-ha "ready" with 1 of
        3 nodes), so it is not enough on its own. A cluster counts once its
        CAPI Machines are all Running, one per declared node, and Rancher's
        management cluster reports Ready=True.
        """
        expected = {c["name"]: len(c["nodes"]) for c in self.downstream_clusters}
        names = list(expected)
        yield LogLine(
            f"Waiting for {len(names)} cluster(s) to be Ready with all nodes "
            f"(up to {self.DOWNSTREAM_TIMEOUT // 60} min)..."
        )
        # One line per cluster: name=<provisioning ready>|<running machines>|<machines>|<mgmt Ready>
        query = (
            _K3S_KUBECONFIG
            + "for c in " + " ".join(shlex.quote(n) for n in names) + "; do\n"
            '  ready=$(kubectl get clusters.provisioning.cattle.io "$c" -n fleet-default'
            " -o jsonpath='{.status.ready}' 2>/dev/null)\n"
            '  id=$(kubectl get clusters.provisioning.cattle.io "$c" -n fleet-default'
            " -o jsonpath='{.status.clusterName}' 2>/dev/null)\n"
            '  phases=$(kubectl get machines.cluster.x-k8s.io -n fleet-default'
            ' -l "cluster.x-k8s.io/cluster-name=$c"'
            " -o jsonpath='{range .items[*]}{.status.phase}{\"\\n\"}{end}' 2>/dev/null)\n"
            '  total=$(printf \'%s\\n\' "$phases" | grep -c . || true)\n'
            '  running=$(printf \'%s\\n\' "$phases" | grep -cx Running || true)\n'
            '  mgmt=""\n'
            '  [ -n "$id" ] && mgmt=$(kubectl get clusters.management.cattle.io "$id"'
            " -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null)\n"
            '  echo "$c=$ready|$running|$total|$mgmt"\n'
            "done\n"
        )
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            r = self._ssh_script(query, timeout=60)
            state: dict[str, tuple[str, int, int, str]] = {}
            for line in r.stdout.splitlines():
                if "=" not in line:
                    continue
                name, _, rest = line.partition("=")
                parts = rest.split("|")
                if len(parts) != 4:
                    continue
                try:
                    state[name] = (parts[0], int(parts[1] or 0), int(parts[2] or 0), parts[3])
                except ValueError:
                    continue

            def _done(n: str) -> bool:
                st = state.get(n)
                return bool(st) and st[0] == "true" and st[1] == st[2] == expected[n] and st[3] == "True"

            ready = [n for n in names if _done(n)]
            yield ProgressUpdate(
                "Downstream clusters", elapsed, self.DOWNSTREAM_TIMEOUT,
                detail=f"{len(ready)}/{len(names)} ready",
            )
            if len(ready) == len(names):
                for n in names:
                    yield LogLine(f"  {n}: Ready ({expected[n]}/{expected[n]} nodes)")
                return True

            def _status(n: str) -> str:
                st = state.get(n)
                return f"{n} ({st[1]}/{expected[n]} nodes)" if st else n

            pending = ", ".join(_status(n) for n in names if n not in ready)
            if elapsed >= self.DOWNSTREAM_TIMEOUT:
                self.error = f"downstream cluster(s) not Ready after {self.DOWNSTREAM_TIMEOUT // 60} min: {pending}"
                yield LogLine(f"  ✗ {self.error}")
                return False
            m, s = divmod(int(elapsed), 60)
            yield LogLine(f"  {m:02d}:{s:02d}  provisioning: {pending}")
            if self._sleep(self.DOWNSTREAM_POLL):
                return False
