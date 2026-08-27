"""K3s + Helm + cert-manager + Rancher Prime install and API configuration."""
from __future__ import annotations

import json
import shlex
import time
import urllib.request
from typing import Generator, Iterator

import yaml

from ..runner import DeployEvent, LogLine, ProgressUpdate


class ClusterSetupMixin:
    """Bring up the management cluster and configure the Rancher API."""

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

    def _clear_first_login(self) -> None:
        """Patch the first-login Setting to false.

        Rancher's dashboard shows the "create your password" wizard whenever
        this Setting is true. It's normally cleared as a side effect of the
        bootstrap admin completing the setpassword action — but when the
        configured password already matches bootstrapPassword (the common
        case here, since bootstrapPassword is seeded from secrets.yaml),
        login succeeds immediately, setpassword is never called, and the
        wizard stays stuck on even though the credentials already work.
        Idempotent and safe to call on every deploy.
        """
        script = (
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "kubectl patch settings.management.cattle.io first-login"
            " --type=merge -p '{\"value\":\"false\"}' 2>/dev/null\n"
        )
        self._ssh_script(script, timeout=15)

    def _configure_api(self) -> Generator[DeployEvent, None, bool]:
        # Rancher 2.14+ sets mustChangePassword=true on fresh installs which
        # causes the login endpoint to return 401 until cleared.  Do it here
        # before the login loop so the rest of the flow is unaffected.
        self._clear_must_change_password()

        # Try passwords in order: configured (secrets.yaml) first (succeeds on re-runs),
        # then the last password we know we set (handles a redeploy after secrets.yaml
        # was regenerated — the live password is still the old one), then the value in
        # bootstrap-secret (succeeds on fresh installs), then the literal 'admin'
        # fallback (handles old deployments where bootstrap was hardcoded).
        # 'on_bootstrap' means the password is not yet the configured one and must be set.
        persisted_pw = self._read_persisted_password(self.RANCHER_PW_FILE)
        bootstrap_pw = self._get_bootstrap_password()
        # dict.fromkeys preserves order and deduplicates (e.g. when bootstrap_pw == admin_pw)
        candidates = [pw for pw in dict.fromkeys([self.admin_password, persisted_pw, bootstrap_pw, "admin"]) if pw]

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

        self._clear_first_login()

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
            self.RANCHER_PW_FILE.write_text(self.admin_password)
            self.RANCHER_PW_FILE.chmod(0o600)
            yield LogLine(f"  Admin password saved to {self.RANCHER_PW_FILE}")
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
