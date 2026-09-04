"""SSH and HTTP primitives against the rancher and eib VMs."""
from __future__ import annotations

import json
import ssl
import subprocess
import time
import urllib.request
from typing import Generator

from ...ssh import ssh_opts
from ..runner import DeployEvent, LogLine, ProgressUpdate


class RemoteExecMixin:
    """Remote execution + HTTP plumbing shared by every RancherPhase concern."""

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
