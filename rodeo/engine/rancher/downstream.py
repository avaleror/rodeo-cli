"""Rancher-provisioned downstream K3s/RKE2 clusters on lab VMs.

Rancher creates a custom cluster record per entry in ``downstream_clusters``;
each node then runs that cluster's registration command (rancher-system-agent),
and Rancher installs K3s or RKE2 on it. Every node gets all three roles
(etcd, control plane, worker), so a 3-node cluster is a 3-member etcd HA.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Generator

from ...ssh import ssh_opts
from ..runner import DeployEvent, LogLine, ProgressUpdate

_K3S_KUBECONFIG = "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
_DISTROS = ("k3s", "rke2")

# Image pre-load: the upstream image list for the exact version, fetched on the
# Rancher VM. RKE2 gets core + Calico (Rancher's default CNI), minus Calico
# add-ons a single-node cluster never runs.
_IMAGE_LISTS = {
    "k3s": ["https://github.com/k3s-io/k3s/releases/download/{v}/k3s-images.txt"],
    "rke2": [
        "https://github.com/rancher/rke2/releases/download/{v}/rke2-images-core.linux-amd64.txt",
        "https://github.com/rancher/rke2/releases/download/{v}/rke2-images-calico.linux-amd64.txt",
    ],
}
_UNUSED_IMAGES = r"calico-(ctl|apiserver|envoy|goldmane|whisker)"
# K3s and RKE2 import every tarball in here when they first start.
_AGENT_IMAGE_DIR = {"k3s": "/var/lib/rancher/k3s/agent/images", "rke2": "/var/lib/rancher/rke2/agent/images"}


class DownstreamMixin:
    """Create downstream clusters in Rancher and register their nodes."""

    DOWNSTREAM_TIMEOUT = 2700  # every cluster Ready (45 min; 3-node RKE2 joins one by one)
    DOWNSTREAM_POLL = 5  # one SSH round trip; at 20 s up to 20 s were lost after Ready
    REGISTRATION_POLL = 5
    REGISTRATION_TIMEOUT = 900  # cluster ID, CAPI cluster, registration command (15 min)
    PRELOAD_GRACE = 180  # extra wait for the image pre-load once registration could start

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

        # Every cluster at once, and nothing waits on a cluster it does not
        # need: all records are applied, each distro's images are pre-loaded
        # in parallel, and each cluster's nodes register as soon as *its*
        # command is ready and *its* distro's images have landed. Measured
        # live: RKE2's command came ~35 s before K3s's, and RKE2 is the
        # slowest path, so a shared "wait for all" cost that much.
        for cluster in self.downstream_clusters:
            name, distro = cluster["name"], cluster["distro"]
            version = self._downstream_version(distro)
            yield LogLine(f"Creating {distro.upper()} cluster '{name}' ({version}) in Rancher...")
            if not (yield from self._apply_cluster_record(name, version)):
                return False

        nodes_by_distro: dict[str, list[str]] = {}
        for c in self.downstream_clusters:
            nodes_by_distro.setdefault(c["distro"], []).extend(c["nodes"])
        n_nodes = sum(len(v) for v in nodes_by_distro.values())
        pool = ThreadPoolExecutor(max_workers=len(nodes_by_distro) + n_nodes)
        try:
            preloads = {d: pool.submit(self._preload_distro, d, nodes)
                        for d, nodes in nodes_by_distro.items()}
            ok = yield from self._register_as_ready(pool, preloads)
            for d in sorted(preloads):
                if preloads[d].done() and not preloads[d].cancelled():
                    try:
                        for line in preloads[d].result():
                            yield LogLine(line)
                    except Exception as exc:  # never fatal: nodes can always pull
                        yield LogLine(f"  {d}: image pre-load failed ({exc}); nodes pulled instead.")
        finally:
            pool.shutdown(wait=False)
        if not ok:
            return False
        return (yield from self._wait_downstream_ready())

    def _register_as_ready(self, pool, preloads) -> Generator[DeployEvent, None, bool]:
        """Poll every cluster's registration command; register its nodes the moment it is ready."""
        pending = {c["name"]: c for c in self.downstream_clusters}
        jobs: list[tuple[str, str, object]] = []
        t0 = time.monotonic()
        last_log = 0.0
        while pending:
            for name in list(pending):
                r = self._ssh_script(self._registration_query(name), timeout=20)
                command = r.stdout.strip()
                if r.returncode != 0 or not command.startswith("curl"):
                    continue
                cluster = pending.pop(name)
                yield LogLine(f"  {name}: cluster record ready, registering {', '.join(cluster['nodes'])}...")
                for node in cluster["nodes"]:
                    jobs.append((name, node, pool.submit(
                        self._register_after_preload, preloads[cluster["distro"]], name, node, command,
                    )))
            if not pending:
                break
            elapsed = time.monotonic() - t0
            if elapsed >= self.REGISTRATION_TIMEOUT:
                self.error = (f"no registration command after {self.REGISTRATION_TIMEOUT // 60} min: "
                              f"{', '.join(pending)}")
                yield LogLine(f"  ✗ {self.error}")
                return False
            if elapsed - last_log >= 30:
                m, s = divmod(int(elapsed), 60)
                yield LogLine(f"  {m:02d}:{s:02d}  waiting for the registration command ({', '.join(pending)})...")
                last_log = elapsed
            if self._sleep(self.REGISTRATION_POLL):
                return False

        ok = True
        for _, _, fut in jobs:
            node_ok, lines = fut.result()
            for line in lines:
                yield LogLine(line)
            ok = ok and node_ok
        return ok

    def _register_after_preload(self, preload, cluster: str, node: str, command: str) -> tuple[bool, list[str]]:
        """Worker: let this node's images land (bounded), then register it."""
        notes: list[str] = []
        try:
            preload.result(timeout=self.PRELOAD_GRACE)
        except FutureTimeout:
            notes.append(f"  {node}: image pre-load still running; registering anyway, it will pull.")
        except Exception:  # reported once by the caller; never fatal
            pass
        ok, lines = self._register_node_sync(cluster, node, command)
        return ok, notes + lines

    # ---------- image pre-load ----------

    def _preload_script(self, distro: str, version: str) -> str:
        """Pull a distro's images on the Rancher VM and export them as one tarball.

        Names are rewritten to Rancher's system-default-registry, which is what
        the downstream node asks for (registry.rancher.com on Rancher Prime);
        a tarball under another name would be imported and then ignored.
        """
        urls = " ".join(shlex.quote(u.format(v=version.replace("+", "%2B"))) for u in _IMAGE_LISTS[distro])
        out = f"/var/tmp/rodeo-preload-{distro}-{version}.tar"
        agent = f"docker.io/rancher/rancher-agent:v{str(self.rancher_version).lstrip('v')}"
        return (
            "set -euo pipefail\n"
            + _K3S_KUBECONFIG
            + f"out={shlex.quote(out)}\n"
            '[ -s "$out" ] && { echo "$out"; exit 0; }\n'
            "reg=$(kubectl get settings.management.cattle.io system-default-registry"
            " -o jsonpath='{.value}' 2>/dev/null || true)\n"
            "imgs=$( { for u in " + urls + '; do curl -sfL "$u"; done; '
            f"echo {shlex.quote(agent)}; }} | grep -vE {shlex.quote(_UNUSED_IMAGES)}"
            ' | sed "s#^docker.io/#${reg:-docker.io}/#" | sort -u)\n'
            '[ -n "$imgs" ] || { echo "no image list" >&2; exit 1; }\n'
            'printf \'%s\\n\' $imgs | xargs -P 6 -I{} k3s ctr -n rodeo-preload images pull'
            " --platform linux/amd64 {} >/dev/null\n"
            'k3s ctr -n rodeo-preload images export --platform linux/amd64 "$out.part" $imgs\n'
            'mv "$out.part" "$out"\n'
            'echo "$out"\n'
        )

    def _copy_preload(self, tarball: str, distro: str, ip: str) -> subprocess.CompletedProcess:
        """Stream a tarball from the Rancher VM into a node's image import dir."""
        dest = _AGENT_IMAGE_DIR[distro]
        key = str(self.ssh_key)
        src = subprocess.Popen(
            ["ssh", "-i", key, *ssh_opts(), f"root@{self.rancher_ip}", "cat", shlex.quote(tarball)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            # .part is not an image extension, so K3s/RKE2 never import a half-written file.
            return subprocess.run(
                ["ssh", "-i", key, *ssh_opts(), f"root@{ip}",
                 f"mkdir -p {dest} && cat > {dest}/rodeo-preload.tar.part"
                 f" && mv {dest}/rodeo-preload.tar.part {dest}/rodeo-preload.tar"],
                stdin=src.stdout, capture_output=True, text=True, timeout=600,
            )
        finally:
            if src.stdout:
                src.stdout.close()
            src.wait(timeout=30)

    def _preload_distro(self, distro: str, nodes: list[str]) -> list[str]:
        """Pre-load one distro's images onto its nodes; returns log lines and never raises."""
        t0 = time.monotonic()
        r = self._ssh_script(self._preload_script(distro, self._downstream_version(distro)), timeout=900)
        if r.returncode != 0 or not r.stdout.strip():
            err = (r.stderr or "").strip().splitlines()[-1:] or [f"exit {r.returncode}"]
            return [f"  {distro}: image pre-load skipped ({err[0]})"]
        tarball = r.stdout.strip().splitlines()[-1]
        lines: list[str] = []
        for node in nodes:
            ip = self.vm_ips[node]
            # A node already running its distro imported its images long ago.
            if self._node_ssh_script(ip, f"test -d /var/lib/rancher/{distro}/server", timeout=15).returncode == 0:
                continue
            if self._copy_preload(tarball, distro, ip).returncode != 0:
                lines.append(f"  {node}: image pre-load copy failed; it will pull instead")
        lines.insert(0, f"  {distro}: images pre-loaded in {int(time.monotonic() - t0)}s.")
        return lines

    def _apply_cluster_record(self, name: str, version: str) -> Generator[DeployEvent, None, bool]:
        """Apply one provisioning Cluster record (a custom cluster)."""
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
            return False
        return True

    @staticmethod
    def _registration_query(name: str) -> str:
        """Shell that prints the cluster's node command once it is usable, else nothing.

        Rancher assigns the c-m-xxxxx ID, then creates default-token in that
        namespace. insecureNodeCommand skips the CA checksum: Rancher's cert
        is self-signed in this lab and the agent pins the CA it downloads.
        Two Rancher 2.15 behaviours, both found live:
         - the commands carry a literal {token}; the token itself is in the
           Secret named by status.tokenSecretName (key "token"). Substituted
           here, on the Rancher VM, so it never reaches the log.
         - CAPI (cattle-capi-system) starts minutes after Rancher answers
           /ping. Until its Cluster object exists, every node registration
           gets "500 machine not found by request", so wait for it first.
        """
        name_q = shlex.quote(name)
        return (
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

    def _register_node_sync(self, cluster: str, node: str, command: str) -> tuple[bool, list[str]]:
        """Register one node; runs in a worker thread, so it returns its log lines."""
        ip = self.vm_ips[node]
        lines = [f"  {node} ({ip}): waiting for SSH..."]
        t0 = time.monotonic()
        while self._node_ssh_script(ip, "true", timeout=15).returncode != 0:
            if time.monotonic() - t0 >= self.SSH_TIMEOUT:
                self.error = f"{node}: SSH not reachable after {self.SSH_TIMEOUT // 60} min"
                return False, lines + [f"  ✗ {self.error}"]
            if self._sleep(self.SSH_POLL):
                return False, lines

        # Re-runs converge: a node whose agent is already running is registered.
        r = self._node_ssh_script(ip, "systemctl is-active --quiet rancher-system-agent", timeout=15)
        if r.returncode == 0:
            return True, lines + [f"  {node}: already registered."]

        roles = "--etcd --controlplane --worker"
        script = (
            "set -euo pipefail\n"
            # The node's cloud-init installs what K3s/RKE2 need (iptables);
            # registering before it finishes races it.
            "cloud-init status --wait >/dev/null 2>&1 || true\n"
            f"{command} {roles} --node-name {shlex.quote(node)}\n"
        )
        lines.append(f"  {node}: registering with '{cluster}' (etcd, control plane, worker)...")
        # The command carries the cluster token: it travels on stdin, never argv or the log.
        r = self._node_ssh_script(ip, script, timeout=300)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
            lines += [f"    {line}" for line in tail]
            self.error = f"{node}: registration with '{cluster}' failed"
            return False, lines + [f"  ✗ {self.error}"]
        return True, lines + [f"  {node}: agent installed."]

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
