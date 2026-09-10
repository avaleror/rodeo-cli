"""Rancher UI extension repositories and declarative extension reconcile."""
from __future__ import annotations

import time
from typing import Generator

from ..runner import DeployEvent, LogLine


class UiExtensionsMixin:
    """ClusterRepos + UI extension version reconcile via the catalog API."""

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

        # Wait for the catalog controller to actually finish downloading the index —
        # a fixed short sleep isn't reliable for a repo's first-ever sync against a
        # real GitHub-hosted chart index, and a premature install action against an
        # unindexed repo fails silently (empty index, no chart found). Poll the
        # ClusterRepo's own downloadTime instead of guessing a fixed wait.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self._ext_repo_downloaded(repo_name, ts):
                return True
            if self._sleep(5):
                return False
        yield LogLine(f"  ⚠ {repo_name}: index still downloading after 90s; proceeding anyway.")
        return True

    def _ext_repo_downloaded(self, repo_name: str, since: str) -> bool:
        """True once the ClusterRepo's index has downloaded at/after `since` (UTC 'Z' timestamps sort lexicographically)."""
        script = (
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"kubectl get clusterrepo {repo_name} -o "
            "jsonpath='{.status.downloadTime}' 2>/dev/null || true\n"
        )
        downloaded = self._ssh_script(script, timeout=20).stdout.strip()
        return bool(downloaded) and downloaded >= since

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
