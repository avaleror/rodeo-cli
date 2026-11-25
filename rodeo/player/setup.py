"""Install the player on one lab host (``sudo rodeo player setup``).

Runs on the KVM host after ``rodeo up`` finished. Idempotent: re-running it
updates the track pin, the config and the units in place.

What it installs, all new names, nothing the existing lab uses:

- the ``student`` user, through the claim portal's own setup script and its
  proofs (no sudo, no privileged groups, cannot read the rodeo key), plus a
  copy of the Harvester kubeconfig the chapters tell the student to use;
- the track at a pinned commit, root-only (it holds the solve scripts);
- ``rodeo-player`` (this package, loopback), ``rodeo-player-ttyd`` (the
  browser terminal, as ``student``, loopback) and ``rodeo-player-caddy`` (the
  only public listener, three names, ``tls internal`` for this week's test);
- the lab's Rancher and Harvester passwords as track variables.

Functional-test shortcuts, to remove before students (plan WP2/WP3): the
player runs as root instead of a dedicated user plus a root helper; Caddy uses
its own CA instead of a wildcard certificate; there is no forward_auth, so the
security group must restrict 443 to the testers.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..fleet.portal import CADDY_SHA512, CADDY_VERSION, STUDENT_SETUP_SCRIPT, STUDENT_USER

TTYD_VERSION = "1.7.7"
TTYD_SHA256 = "8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55"  # ttyd.x86_64

TRACK_REPO = "https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo.git"
ETC = Path("/etc/rodeo-player")
VAR = Path("/var/lib/rodeo-player")
TRACK_DIR = VAR / "track"
STATE_DIR = VAR / "state"
CONFIG = ETC / "config.json"
CADDYFILE = ETC / "Caddyfile"
CADDY_DATA = Path("/var/lib/rodeo-player-caddy")
CA_ROOT = CADDY_DATA / "caddy" / "pki" / "authorities" / "local" / "root.crt"
ROOT_KUBECONFIG = Path("/root/.rodeo/harvester-kubeconfig")
STUDENT_GROUP = "rodeo-student"
PLAYER_PORT = 8680
TTYD_PORT = 7681

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_NAME_RE = re.compile(r"^(?=.{1,200}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_UPSTREAM_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}:\d{1,5}$")


@dataclass(frozen=True)
class SetupOptions:
    base: str                       # e.g. 13-61-184-227.sslip.io
    track_ref: str                  # full commit SHA of suse-virt-rodeo
    track_repo: str = TRACK_REPO
    harvester_upstream: str = "192.168.122.10:443"
    rancher_upstream: str = "192.168.122.9:30002"
    title: str = "SUSE Virtualization Rodeo"
    sandbox_id: str = ""

    def validate(self) -> None:
        if not _NAME_RE.match(self.base):
            raise ValueError(f"not a DNS name: {self.base!r}")
        if not _SHA_RE.match(self.track_ref):
            raise ValueError("--track-ref must be a full 40-character commit SHA (pin the track)")
        if not self.track_repo.startswith("https://"):
            raise ValueError("--track-repo must be an https:// git URL")
        for up in (self.harvester_upstream, self.rancher_upstream):
            if not _UPSTREAM_RE.match(up):
                raise ValueError(f"upstream must be IP:port, got {up!r}")

    @property
    def names(self) -> dict[str, str]:
        return {"player": self.base, "harvester": f"hv.{self.base}", "rancher": f"rancher.{self.base}"}


def sslip_base(public_ip: str) -> str:
    """``1.2.3.4`` -> ``1-2-3-4.sslip.io`` (resolves to the IP, any prefix too)."""
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", public_ip):
        raise ValueError(f"not an IPv4 address: {public_ip!r}")
    return public_ip.replace(".", "-") + ".sslip.io"


def aws_public_ip(timeout: float = 3.0) -> str:
    """The instance's public IPv4 from IMDSv2."""
    token_req = urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    token = urllib.request.urlopen(token_req, timeout=timeout).read().decode()
    ip_req = urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/public-ipv4",
        headers={"X-aws-ec2-metadata-token": token})
    return urllib.request.urlopen(ip_req, timeout=timeout).read().decode().strip()


# ---------------------------------------------------------------- rendered files

def caddyfile(opts: SetupOptions) -> str:
    n = opts.names

    def upstream(name: str, target: str) -> str:
        # The products serve their own self-signed certificates on the lab
        # network; the browser only ever sees Caddy's. X-Frame-Options is
        # dropped so the UIs can sit in the player's iframes.
        return f"""{name} {{
\ttls internal
\treverse_proxy https://{target} {{
\t\ttransport http {{
\t\t\ttls_insecure_skip_verify
\t\t}}
\t\theader_down -X-Frame-Options
\t}}
}}
"""

    return f"""{{
\tadmin off
\tskip_install_trust
}}

{n['player']} {{
\ttls internal
\thandle /terminal* {{
\t\treverse_proxy 127.0.0.1:{TTYD_PORT}
\t}}
\thandle {{
\t\treverse_proxy 127.0.0.1:{PLAYER_PORT}
\t}}
}}

{upstream(n['harvester'], opts.harvester_upstream)}
{upstream(n['rancher'], opts.rancher_upstream)}"""


def player_config(opts: SetupOptions) -> dict:
    n = opts.names
    return {
        "track_dir": str(TRACK_DIR),
        "state_dir": str(STATE_DIR),
        "title": opts.title,
        "listen": "127.0.0.1",
        "port": PLAYER_PORT,
        "sandbox_id": opts.sandbox_id,
        "tab_urls": {"8443": f"https://{n['harvester']}/", "30002": f"https://{n['rancher']}/"},
        "terminal_url": "/terminal/",
    }


def player_unit(rodeo_bin: str) -> str:
    return f"""[Unit]
Description=rodeo virt rodeo player (loopback)
After=network-online.target

[Service]
# Functional-test shortcut: root, because chapter scripts need the root
# kubeconfig. Before students: a dedicated user plus a root-only helper.
ExecStart={rodeo_bin} player serve --config {CONFIG}
Restart=on-failure
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
"""


def ttyd_unit() -> str:
    return f"""[Unit]
Description=rodeo virt rodeo player terminal (ttyd as {STUDENT_USER}, loopback)
After=network-online.target

[Service]
User={STUDENT_USER}
WorkingDirectory=~
Environment=HOME=/home/{STUDENT_USER} TERM=xterm-256color
ExecStart=/usr/local/bin/ttyd --interface lo --port {TTYD_PORT} --base-path /terminal --writable -t disableLeaveAlert=true /bin/bash -l
Restart=on-failure
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""


def caddy_unit() -> str:
    return f"""[Unit]
Description=rodeo virt rodeo player TLS (Caddy)
After=network-online.target rodeo-player.service

[Service]
User=rodeo-player-caddy
Group=rodeo-player-caddy
Environment=XDG_DATA_HOME={CADDY_DATA} XDG_CONFIG_HOME={CADDY_DATA}
ExecStart=/usr/local/bin/caddy run --config {CADDYFILE} --adapter caddyfile
Restart=on-failure
StateDirectory=rodeo-player-caddy
StateDirectoryMode=0700
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes

[Install]
WantedBy=multi-user.target
"""


def install_script(opts: SetupOptions, rodeo_bin: str) -> str:
    """Idempotent root script: binaries, track, kubeconfig, files, units."""
    q = shlex.quote
    caddy_url = (f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
                 f"caddy_{CADDY_VERSION}_linux_amd64.tar.gz")
    ttyd_url = f"https://github.com/tsl0922/ttyd/releases/download/{TTYD_VERSION}/ttyd.x86_64"
    files = {
        str(CADDYFILE): caddyfile(opts),
        str(CONFIG): json.dumps(player_config(opts), indent=2) + "\n",
        "/etc/systemd/system/rodeo-player.service": player_unit(rodeo_bin),
        "/etc/systemd/system/rodeo-player-ttyd.service": ttyd_unit(),
        "/etc/systemd/system/rodeo-player-caddy.service": caddy_unit(),
    }
    writes = "\n".join(
        f"cat > {q(path)} <<'RODEO_PLAYER_EOF'\n{body}RODEO_PLAYER_EOF" for path, body in files.items()
    )
    home = f"/home/{STUDENT_USER}"
    return f"""set -euo pipefail
[ "$(uname -m)" = x86_64 ] || {{ echo "player setup supports x86_64 hosts only" >&2; exit 2; }}
[ -s {ROOT_KUBECONFIG} ] || {{ echo "{ROOT_KUBECONFIG} missing: has rodeo up finished?" >&2; exit 2; }}
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
id rodeo-player-caddy >/dev/null 2>&1 || useradd --system --user-group --home-dir {CADDY_DATA} --shell /usr/sbin/nologin rodeo-player-caddy
install -d -m 700 {VAR} {STATE_DIR}
install -d -m 755 {ETC}

# Binaries, pinned and verified.
if [ "$(/usr/local/bin/caddy version 2>/dev/null | cut -d' ' -f1)" != "v{CADDY_VERSION}" ]; then
  curl -fsSL -o "$WORK/caddy.tgz" {q(caddy_url)}
  echo "{CADDY_SHA512}  $WORK/caddy.tgz" | sha512sum -c --quiet -
  tar -xzf "$WORK/caddy.tgz" -C "$WORK" caddy && install -m 755 "$WORK/caddy" /usr/local/bin/caddy
fi
if ! echo "{TTYD_SHA256}  /usr/local/bin/ttyd" | sha256sum -c --quiet - 2>/dev/null; then
  curl -fsSL -o "$WORK/ttyd" {q(ttyd_url)}
  echo "{TTYD_SHA256}  $WORK/ttyd" | sha256sum -c --quiet -
  install -m 755 "$WORK/ttyd" /usr/local/bin/ttyd
fi

# The track at a pinned commit, root-only: it holds the solve scripts.
command -v git >/dev/null || zypper -n -q in git-core
if [ ! -d {TRACK_DIR}/.git ]; then git init -q {TRACK_DIR}; fi
git -C {TRACK_DIR} fetch -q --depth 1 {q(opts.track_repo)} {q(opts.track_ref)}
git -C {TRACK_DIR} -c advice.detachedHead=false checkout -q --force FETCH_HEAD
[ "$(git -C {TRACK_DIR} rev-parse HEAD)" = {q(opts.track_ref)} ]
chmod 700 {TRACK_DIR}

# The kubeconfig chapter 1 tells the student to use (~/.rodeo/harvester-kubeconfig).
getent group {STUDENT_GROUP} >/dev/null || groupadd --system {STUDENT_GROUP}
usermod -a -G {STUDENT_GROUP} {STUDENT_USER}
install -d -m 750 -o {STUDENT_USER} -g {STUDENT_GROUP} {home}/.rodeo
install -m 640 -o root -g {STUDENT_GROUP} {ROOT_KUBECONFIG} {home}/.rodeo/harvester-kubeconfig
runuser -u {STUDENT_USER} -- test -r {home}/.rodeo/harvester-kubeconfig
if runuser -u {STUDENT_USER} -- test -r /root/.ssh/id_ed25519; then echo "student can read the rodeo key" >&2; exit 3; fi

{writes}
chmod 644 {CONFIG} {CADDYFILE}
/usr/local/bin/caddy validate --config {CADDYFILE} --adapter caddyfile >/dev/null 2>&1 \\
  || {{ /usr/local/bin/caddy validate --config {CADDYFILE} --adapter caddyfile; exit 1; }}

if systemctl is-active -q firewalld; then
  firewall-cmd -q --permanent --add-service=http --add-service=https && firewall-cmd -q --reload
fi
systemctl daemon-reload
systemctl enable -q rodeo-player rodeo-player-ttyd rodeo-player-caddy
systemctl restart rodeo-player rodeo-player-ttyd rodeo-player-caddy
for _ in $(seq 1 30); do
  curl -fsS -o /dev/null http://127.0.0.1:{PLAYER_PORT}/healthz && break
  sleep 1
done
curl -fsS -o /dev/null http://127.0.0.1:{PLAYER_PORT}/healthz
echo PLAYER_UP
"""


# ---------------------------------------------------------------- secrets

def _secrets_files(sudo_user: str | None) -> list[Path]:
    import pwd

    paths = []
    if sudo_user:
        try:
            paths.append(Path(pwd.getpwnam(sudo_user).pw_dir) / ".rodeo" / "secrets.yaml")
        except KeyError:
            pass
    paths.append(Path("/root/.rodeo/secrets.yaml"))
    return paths


def lab_passwords(sudo_user: str | None) -> dict[str, str]:
    """Rancher and Harvester admin passwords from the host's secrets.yaml."""
    import yaml

    for path in _secrets_files(sudo_user):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            out = {}
            if data.get("rancher_admin_password"):
                out["RANCHER_PASSWORD"] = str(data["rancher_admin_password"])
            if data.get("harvester_admin_password"):
                out["HARVESTER_PASSWORD"] = str(data["harvester_admin_password"])
            if out:
                return out
    return {}


# ---------------------------------------------------------------- run

def run_setup(opts: SetupOptions, *, sudo_user: str | None) -> dict:
    """Do the whole install. Returns URLs and the CA path, never a password."""
    opts.validate()
    rodeo_bin = shutil.which("rodeo") or "/usr/local/bin/rodeo"
    student = subprocess.run(["bash", "-c", STUDENT_SETUP_SCRIPT], input="", text=True,
                             capture_output=True, timeout=300)
    if student.returncode != 0 or "STUDENT_OK" not in student.stdout:
        raise RuntimeError(f"student user setup failed: {student.stderr.strip()[-400:]}")
    res = subprocess.run(["bash", "-c", install_script(opts, rodeo_bin)], text=True,
                         capture_output=True, timeout=900)
    if res.returncode != 0 or "PLAYER_UP" not in res.stdout:
        raise RuntimeError(f"player install failed (exit {res.returncode}): {res.stderr.strip()[-800:]}")

    from .runner import Runner

    runner = Runner(TRACK_DIR, STATE_DIR, sandbox_id=opts.sandbox_id)
    seeded = []
    for key, value in lab_passwords(sudo_user).items():
        runner.set_variable(key, value)
        seeded.append(key)
    n = opts.names
    return {
        "player": f"https://{n['player']}/",
        "harvester": f"https://{n['harvester']}/",
        "rancher": f"https://{n['rancher']}/",
        "ca_root": str(CA_ROOT),
        "variables_seeded": seeded,
        "chapters": len(runner.chapters),
    }
