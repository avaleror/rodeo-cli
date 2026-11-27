"""Install the player on one lab host (``sudo rodeo player setup``).

Runs on the KVM host after ``rodeo up`` finished. Idempotent: re-running it
updates the track pin, the config and the units in place.

What it installs, all new names, nothing the existing lab uses:

- the ``student`` user, through the claim portal's own setup script and its
  proofs (no sudo, no privileged groups, cannot read the rodeo key), plus a
  copy of the lab kubeconfig the chapters tell the student to use;
- the track at a pinned commit, root-only (it holds the solve scripts), when
  the profile has one; without it the player shows the UIs, a terminal, the
  credentials and the web guide link (``guide_url``), with the guide folded;
- ``rodeo-player`` (this package, loopback), ``rodeo-player-ttyd`` (the
  browser terminal, as ``student``, loopback) and ``rodeo-player-caddy`` (the
  only public listener: the player's name plus one per product UI);
- the lab's passwords as track variables, and the credentials the profile
  lists, resolved into a root-only file the player shows behind the login.

What the lab shows (title, track, product UIs, kubeconfig, variables) comes
from the lab profile's ``player.yaml`` (rodeo.player.profile).

Access: Caddy runs forward_auth to the player on every name, so only a
browser that opened the lab link gets in (see rodeo.player.auth). The link's
token also ends up in that browser's history and in Caddy's journal: treat it
like the lab password.

Names and TLS, two modes:

- test mode (no ``--domain``): ``<ip-dashed>.sslip.io``, ``<ui key>.`` under
  it (``hv.``, ``rancher.``), Caddy's own CA (``tls internal``);
- owned zone (``--domain`` + ``--label``): flat names ``<label>`` and
  ``<ui key>-<label>`` under the zone, and the event's
  wildcard certificate pushed by the laptop (``--tls-stdin``). It lands in
  /etc/rodeo-player/tls, root-only 0600; Caddy reads it through systemd
  ``LoadCredential``, so neither the Caddy user nor the student can open the
  key file. No ACME on the lab.

The player runs as root (chapter
scripts need the root kubeconfig and root's key to the Rancher VM) under the
systemd sandbox in player_unit().
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fleet.portal import CADDY_SHA512, CADDY_VERSION, STUDENT_SETUP_SCRIPT, STUDENT_USER
from .profile import PlayerProfile
from .profile import load as load_profile
from .runner import CHECK_TIMEOUT, check_timeout_ok

TTYD_VERSION = "1.7.7"
TTYD_SHA256 = "8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55"  # ttyd.x86_64

ETC = Path("/etc/rodeo-player")
VAR = Path("/var/lib/rodeo-player")
TRACK_DIR = VAR / "track"
STATE_DIR = VAR / "state"
CONFIG = ETC / "config.json"
AUTH_FILE = VAR / "auth.json"     # root-only: lab secret + access token
CREDENTIALS_FILE = VAR / "credentials.json"  # root-only: what the student logs in with
# Sorts after the student's blanket deny (zz-rodeo-student, fleet/portal.py):
# sudo applies the last matching rule.
STUDENT_SUDOERS = Path("/etc/sudoers.d/zzz-rodeo-player-student")
STUDENT_RODEO_DIR = Path("/usr/local/libexec/rodeo-player")   # the student's rodeo shim + root wrapper
STUDENT_ROOT_RODEO = STUDENT_RODEO_DIR / "rodeo-root"
STUDENT_PROFILE_D = Path("/etc/profile.d/rodeo-player-student.sh")
STUDENT_VM_KEY = VAR / "student-vm-key"      # root-only copy of the student's key to the lab VMs
CADDYFILE = ETC / "Caddyfile"
CADDY_DATA = Path("/var/lib/rodeo-player-caddy")
TLS_DIR = ETC / "tls"              # root 0700
TLS_CERT = TLS_DIR / "cert"         # root 0600, chain
TLS_KEY = TLS_DIR / "key"           # root 0600; Caddy gets it via LoadCredential
CA_ROOT = CADDY_DATA / "caddy" / "pki" / "authorities" / "local" / "root.crt"
# rodeo up writes the kubeconfig to the invoking user's ~/.rodeo: /root on a
# single-host lab, the SSH user's home (ec2-user) on a fleet host. The player
# keeps its own root-only copy, so it does not depend on which one it was.
PLAYER_KUBECONFIG = VAR / "lab-kubeconfig"
STUDENT_GROUP = "rodeo-student"
PLAYER_PORT = 8680
TTYD_PORT = 7681

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_NAME_RE = re.compile(r"^(?=.{1,200}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class SetupOptions:
    base: str                       # e.g. 13-61-184-227.sslip.io
    track_ref: str                  # full commit SHA of the track ("" = profile has none)
    profile: PlayerProfile = field(default_factory=load_profile)
    track_repo: str = ""            # empty = the profile's
    sandbox_id: str = ""
    check_timeout: int = CHECK_TIMEOUT  # seconds per check script
    # Owned-zone mode (flat names under one wildcard certificate):
    # base = <label>.<domain>, UIs = <key>-<label>.<domain> (hv-, rancher-).
    # Empty = the sslip.io test mode, UIs = <key>.<base>.
    domain: str = ""
    # Certificate chain + key (PEM). Empty = Caddy's own CA. Never in repr.
    tls_pem: str = field(default="", repr=False)

    @property
    def repo(self) -> str:
        return self.track_repo or self.profile.track_repo

    @property
    def title(self) -> str:
        return self.profile.title

    def validate(self) -> None:
        self.profile.validate()
        for name in self.names.values():
            if not _NAME_RE.match(name):
                raise ValueError(f"not a DNS name: {name!r}")
        if self.domain and not self.base.endswith("." + self.domain):
            raise ValueError(f"{self.base!r} is not under --domain {self.domain!r}")
        if self.tls_pem:
            split_pem(self.tls_pem)
        if self.repo:
            if not _SHA_RE.match(self.track_ref):
                raise ValueError("--track-ref must be a full 40-character commit SHA (pin the track)")
            if not self.repo.startswith("https://"):
                raise ValueError("--track-repo must be an https:// git URL")
        elif self.track_ref:
            raise ValueError(f"lab profile {self.profile.name} has no track: drop --track-ref")
        check_timeout_ok(self.check_timeout)

    @property
    def names(self) -> dict[str, str]:
        """``player`` and one name per product UI, keyed by the UI's key."""
        out = {"player": self.base}
        label = self.base.removesuffix(f".{self.domain}")
        for ui in self.profile.uis:
            out[ui.key] = f"{ui.key}-{label}.{self.domain}" if self.domain else f"{ui.key}.{self.base}"
        return out

    @property
    def tls_directive(self) -> str:
        # {$CREDENTIALS_DIRECTORY} is read when Caddy loads the file: systemd
        # sets it for the unit, the install script sets it for `caddy validate`.
        if self.tls_pem:
            return "tls {$CREDENTIALS_DIRECTORY}/cert {$CREDENTIALS_DIRECTORY}/key"
        return "tls internal"


_PEM_RE = re.compile(r"-----BEGIN ([A-Z ]+)-----\n.*?\n-----END \1-----\n?", re.DOTALL)


def split_pem(bundle: str) -> tuple[str, str]:
    """Certificate chain and private key out of one PEM bundle."""
    certs, keys = [], []
    for m in _PEM_RE.finditer(bundle.replace("\r\n", "\n")):
        block = m.group(0).rstrip("\n") + "\n"
        if m.group(1) == "CERTIFICATE":
            certs.append(block)
        elif m.group(1).endswith("PRIVATE KEY"):
            keys.append(block)
    if not certs or len(keys) != 1:
        raise ValueError("TLS bundle must hold the certificate chain and exactly one private key (PEM)")
    return "".join(certs), keys[0]


def domain_base(label: str, domain: str) -> str:
    """``k7q2mx`` + ``labs.example.com`` -> ``k7q2mx.labs.example.com``."""
    if not re.match(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$", label):
        raise ValueError(f"bad lab label {label!r} (lowercase letters, digits, dashes)")
    return f"{label}.{domain.strip('.').lower()}"


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

    def upstream(name: str, target: str, scheme: str = "https") -> str:
        # The products serve their own self-signed certificates on the lab
        # network; the browser only ever sees Caddy's. X-Frame-Options is
        # dropped so the UIs can sit in the player's iframes. Host is the
        # browser's: for an https upstream Caddy would send the upstream
        # address, and Rancher/Harvester build absolute API links from Host,
        # so the UI called https://192.168.122.10/v1/... and hung after login
        # (found live 2026-11-25).
        # Every request asks the player first (forward_auth), except the
        # cookie hop the player itself answers (see rodeo.player.auth).
        tls = ("\n\t\ttransport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}"
               if scheme == "https" else "")
        return f"""{name} {{
\t{opts.tls_directive}
\t@hop path /.rodeo/auth
\t@protected not path /.rodeo/auth
\tforward_auth @protected 127.0.0.1:{PLAYER_PORT} {{
\t\turi /_auth
\t}}
\thandle @hop {{
\t\treverse_proxy 127.0.0.1:{PLAYER_PORT}
\t}}
\thandle {{
\treverse_proxy {scheme}://{target} {{
\t\theader_up Host {{host}}
\t\theader_up X-Forwarded-Host {{host}}{tls}
\t\theader_down -X-Frame-Options
\t}}
\t}}
}}
"""

    uis = "\n".join(upstream(n[ui.key], ui.upstream, ui.scheme) for ui in opts.profile.uis)
    # disable_redirects: Caddy must not bind :80. The lab already serves
    # something there (nginx), and tls internal needs no HTTP challenge.
    return f"""{{
\tadmin off
\tskip_install_trust
\tauto_https disable_redirects
}}

{n['player']} {{
\t{opts.tls_directive}
\t@protected not path /enter /auth/hop /favicon.ico
\tforward_auth @protected 127.0.0.1:{PLAYER_PORT} {{
\t\turi /_auth
\t}}
\thandle /terminal* {{
\t\treverse_proxy 127.0.0.1:{TTYD_PORT}
\t}}
\thandle {{
\t\treverse_proxy 127.0.0.1:{PLAYER_PORT}
\t}}
}}

{uis}"""


def player_config(opts: SetupOptions) -> dict:
    n = opts.names
    return {
        "track_dir": str(TRACK_DIR),
        "state_dir": str(STATE_DIR),
        "title": opts.title,
        "listen": "127.0.0.1",
        "port": PLAYER_PORT,
        "sandbox_id": opts.sandbox_id,
        "tab_urls": {str(ui.tab_port): f"https://{n[ui.key]}/" for ui in opts.profile.uis},
        "check_timeout": opts.check_timeout,
        # Health probes go straight to the upstreams Caddy proxies, not the
        # public names (those sit behind the lab login).
        "probes": {**{str(ui.tab_port): f"{ui.scheme}://{ui.upstream}/"
                      for ui in opts.profile.uis if ui.lobby},
                   "terminal": f"http://127.0.0.1:{TTYD_PORT}/terminal/"},
        "terminal_url": "/terminal/",
        "track": bool(opts.repo),
        "guide_url": opts.profile.guide_url,
        # Without a track the tabs come from here: each UI, then the terminal.
        "lab_tabs": [*({"title": ui.label, "type": "service", "port": ui.tab_port} for ui in opts.profile.uis),
                     {"title": "Terminal", "type": "terminal"}],
        "credentials_file": str(CREDENTIALS_FILE),
        "auth_file": str(AUTH_FILE),
        "kubeconfig": str(PLAYER_KUBECONFIG) if opts.profile.kubeconfig else "",
        "hosts": dict(n),
    }


def player_unit(rodeo_bin: str) -> str:
    return f"""[Unit]
Description=rodeo lab player (loopback)
After=network-online.target

[Service]
# Root, because the chapter scripts need the root kubeconfig and root's SSH
# key to the Rancher VM. Sandboxed so a bug in the web process cannot change
# the host: everything read-only except root's home (kubectl cache, ssh
# known_hosts) and the player's own state; no capabilities, so uid 0 only
# reaches what root owns. Every chapter script was run under exactly these
# settings on a live lab (2026-11-25).
ExecStart={rodeo_bin} player serve --config {CONFIG}
Restart=on-failure
ProtectSystem=strict
ReadWritePaths={VAR} /root
PrivateTmp=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
"""


def ttyd_unit(opts: SetupOptions | None = None) -> str:
    libvirt = (" LIBVIRT_DEFAULT_URI=qemu:///system"
               if opts is not None and opts.profile.student_libvirt else "")
    # sudo needs new privileges: only labs whose guide runs rodeo commands as root.
    nnp = "no" if opts is not None and opts.profile.student_rodeo_commands else "yes"
    return f"""[Unit]
Description=rodeo lab player terminal (ttyd as {STUDENT_USER}, loopback)
After=network-online.target

[Service]
User={STUDENT_USER}
WorkingDirectory=~
Environment=HOME=/home/{STUDENT_USER} TERM=xterm-256color{libvirt}
ExecStart=/usr/local/bin/ttyd --interface lo --port {TTYD_PORT} --base-path /terminal --writable -t disableLeaveAlert=true /bin/bash -l
Restart=on-failure
NoNewPrivileges={nnp}

[Install]
WantedBy=multi-user.target
"""


def caddy_unit(opts: SetupOptions | None = None) -> str:
    # The key stays root-only on disk; systemd hands the unit a private copy
    # in $CREDENTIALS_DIRECTORY that only this service can read.
    creds = (f"LoadCredential=cert:{TLS_CERT}\nLoadCredential=key:{TLS_KEY}\n"
             if opts is not None and opts.tls_pem else "")
    return f"""[Unit]
Description=rodeo lab player TLS (Caddy)
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
{creds}
[Install]
WantedBy=multi-user.target
"""


def install_script(opts: SetupOptions, rodeo_bin: str, lab_user: str | None = None) -> str:
    """Idempotent root script: binaries, track, kubeconfig, files, units."""
    q = shlex.quote
    caddy_url = (f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
                 f"caddy_{CADDY_VERSION}_linux_amd64.tar.gz")
    ttyd_url = f"https://github.com/tsl0922/ttyd/releases/download/{TTYD_VERSION}/ttyd.x86_64"
    files = {
        str(CADDYFILE): caddyfile(opts),
        str(CONFIG): json.dumps(player_config(opts), indent=2) + "\n",
        "/etc/systemd/system/rodeo-player.service": player_unit(rodeo_bin),
        "/etc/systemd/system/rodeo-player-ttyd.service": ttyd_unit(opts),
        "/etc/systemd/system/rodeo-player-caddy.service": caddy_unit(opts),
    }
    writes = "\n".join(
        f"cat > {q(path)} <<'RODEO_PLAYER_EOF'\n{body}RODEO_PLAYER_EOF" for path, body in files.items()
    )
    home = f"/home/{STUDENT_USER}"
    kube = opts.profile.kubeconfig
    kube_check = (f'[ -s {PLAYER_KUBECONFIG} ] || {{ echo "{PLAYER_KUBECONFIG} missing: has rodeo up finished?" >&2; exit 2; }}'
                  if kube else "")
    kube_copy = f"""
# The kubeconfig the chapters tell the student to use (~/.rodeo/{kube}).
install -d -m 750 -o {STUDENT_USER} -g {STUDENT_GROUP} {home}/.rodeo
install -m 640 -o root -g {STUDENT_GROUP} {PLAYER_KUBECONFIG} {home}/.rodeo/{kube}
runuser -u {STUDENT_USER} -- test -r {home}/.rodeo/{kube}""" if kube else ""
    lab_key = opts.profile.student_ssh_key
    key_copy = f"""
# The lab VM key (the profile's student_ssh_key) as the student's default SSH
# key, so the assignment's bare `ssh sles@<vm>` works without naming a key.
# Never root's key: refuse if they were ever the same.
LABKEY={q(lab_key)}
if [ -f "$LABKEY" ]; then
  if [ -f /root/.ssh/id_ed25519 ] && cmp -s "$LABKEY" /root/.ssh/id_ed25519; then
    echo "the lab VM key is root's key; refusing to give it to {STUDENT_USER}" >&2; exit 3
  fi
  SG=$(id -gn {STUDENT_USER})
  install -d -m 700 -o {STUDENT_USER} -g "$SG" {home}/.ssh
  install -m 600 -o {STUDENT_USER} -g "$SG" "$LABKEY" {home}/.ssh/id_ed25519
  install -m 644 -o {STUDENT_USER} -g "$SG" "$LABKEY.pub" {home}/.ssh/id_ed25519.pub
fi
""" if lab_key else ""
    track = f"""# The track at a pinned commit, root-only: it holds the solve scripts.
command -v git >/dev/null || zypper -n -q in git-core
if [ ! -d {TRACK_DIR}/.git ]; then git init -q {TRACK_DIR}; fi
git -C {TRACK_DIR} fetch -q --depth 1 {q(opts.repo)} {q(opts.track_ref)}
git -C {TRACK_DIR} -c advice.detachedHead=false checkout -q --force FETCH_HEAD
[ "$(git -C {TRACK_DIR} rev-parse HEAD)" = {q(opts.track_ref)} ]
chmod 700 {TRACK_DIR}
""" if opts.repo else f"""# No track in this profile: an empty, root-only track directory (no chapters).
rm -rf {TRACK_DIR}
install -d -m 700 {TRACK_DIR}
"""
    libvirt = f"""
# The guide drives the lab VMs with virsh: the student joins libvirt (the
# system connection). Root-equivalent on this host, which is why fleets with
# such a profile need one root key per host (per_host_ssh_key).
usermod -a -G libvirt {STUDENT_USER}
runuser -u {STUDENT_USER} -- env LIBVIRT_DEFAULT_URI=qemu:///system virsh list --all >/dev/null \\
  || {{ echo "{STUDENT_USER} cannot use virsh on qemu:///system" >&2; exit 4; }}
""" if opts.profile.student_libvirt else ""
    commands = opts.profile.student_rodeo_commands
    allowed = "|".join(commands)
    # rodeo keeps the lab's state (trust, secrets) under the invoking user's
    # home: ec2-user on a fleet host, root on a host set up as root. The
    # wrapper runs as that same user's rodeo, never as the student's.
    owner = lab_user if lab_user and lab_user != STUDENT_USER and re.match(r"^[a-z_][a-z0-9_-]{0,31}$", lab_user) else ""
    as_owner = f"export SUDO_USER={owner}" if owner else "unset SUDO_USER"
    rule = ", ".join(f"{STUDENT_ROOT_RODEO} {c}, {STUDENT_ROOT_RODEO} {c} *" for c in commands)
    sudo = f"""
# The rodeo commands the guide runs on the host, as root, and nothing else.
# sudo would set SUDO_USER to the student, and rodeo would then look for the
# lab's state, trust and secrets in the student's home: the root wrapper points
# it at the user who deployed the lab, and the student's own `rodeo` sends
# exactly these subcommands to it.
install -d -m 755 {STUDENT_RODEO_DIR} {STUDENT_RODEO_DIR}/bin
cat > "$WORK/rodeo-root" <<'__ROOT__'
#!/bin/sh
case "$1" in {allowed}) ;; *) echo "rodeo $1 is not allowed here" >&2; exit 2 ;; esac
unset SUDO_UID SUDO_GID SUDO_COMMAND
{as_owner}
exec {rodeo_bin} "$@"
__ROOT__
cat > "$WORK/rodeo" <<'__SHIM__'
#!/bin/sh
case "$1" in {allowed}) exec sudo -n {STUDENT_ROOT_RODEO} "$@" ;; esac
exec {rodeo_bin} "$@"
__SHIM__
install -m 755 "$WORK/rodeo-root" {STUDENT_ROOT_RODEO}
install -m 755 "$WORK/rodeo" {STUDENT_RODEO_DIR}/bin/rodeo
cat > {STUDENT_PROFILE_D} <<'__PROFILE__'
if [ "$(id -un)" = {STUDENT_USER} ]; then PATH={STUDENT_RODEO_DIR}/bin:$PATH; fi
__PROFILE__
chmod 644 {STUDENT_PROFILE_D}
cat > "$WORK/sudoers" <<'__SUDOERS__'
{STUDENT_USER} ALL=(root) NOPASSWD: {rule}
__SUDOERS__
visudo -cqf "$WORK/sudoers"
install -m 440 "$WORK/sudoers" {STUDENT_SUDOERS}
""" if commands else f"""rm -f {STUDENT_SUDOERS} {STUDENT_PROFILE_D}
rm -rf {STUDENT_RODEO_DIR}
"""
    vm_ips = " ".join(q(ip) for ip in opts.profile.student_vm_access)
    vm_access = f"""
# The student's own key to the lab VMs (root there), never root's key to this
# host: made once here, installed on each VM with root's key.
VMKEY={STUDENT_VM_KEY}
[ -f "$VMKEY" ] || ssh-keygen -q -t ed25519 -N '' -C {STUDENT_USER}@lab-vms -f "$VMKEY"
chmod 600 "$VMKEY"
if [ -f /root/.ssh/id_ed25519 ] && cmp -s "$VMKEY" /root/.ssh/id_ed25519; then
  echo "the student VM key is root's key" >&2; exit 3
fi
SG=$(id -gn {STUDENT_USER})
install -d -m 700 -o {STUDENT_USER} -g "$SG" {home}/.ssh
install -m 600 -o {STUDENT_USER} -g "$SG" "$VMKEY" {home}/.ssh/id_ed25519
install -m 644 -o {STUDENT_USER} -g "$SG" "$VMKEY.pub" {home}/.ssh/id_ed25519.pub
PUB=$(cat "$VMKEY.pub")
CFG={home}/.ssh/config
: > "$CFG"
for ip in {vm_ips}; do
  printf 'Host %s\\n  User root\\n  StrictHostKeyChecking accept-new\\n' "$ip" >> "$CFG"
  if timeout 30 ssh -i /root/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=10 \\
       -o StrictHostKeyChecking=accept-new root@"$ip" \\
       "umask 077; mkdir -p ~/.ssh; grep -qxF '$PUB' ~/.ssh/authorized_keys 2>/dev/null || echo '$PUB' >> ~/.ssh/authorized_keys" \\
       </dev/null >/dev/null 2>&1; then
    echo "VM_ACCESS $ip ok"
  else
    echo "VM_ACCESS $ip unreachable"
  fi
done
chown {STUDENT_USER}:"$SG" "$CFG"; chmod 600 "$CFG"
""" if opts.profile.student_vm_access else ""
    return f"""set -euo pipefail
[ "$(uname -m)" = x86_64 ] || {{ echo "player setup supports x86_64 hosts only" >&2; exit 2; }}
{kube_check}
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

{track}
getent group {STUDENT_GROUP} >/dev/null || groupadd --system {STUDENT_GROUP}
usermod -a -G {STUDENT_GROUP} {STUDENT_USER}{kube_copy}
if runuser -u {STUDENT_USER} -- test -r /root/.ssh/id_ed25519; then echo "student can read the rodeo key" >&2; exit 3; fi
{key_copy}{libvirt}{vm_access}{sudo}
{writes}
chmod 644 {CONFIG} {CADDYFILE}
export CREDENTIALS_DIRECTORY={TLS_DIR}   # what systemd gives the unit (owned-zone TLS)
/usr/local/bin/caddy validate --config {CADDYFILE} --adapter caddyfile >/dev/null 2>&1 \\
  || {{ /usr/local/bin/caddy validate --config {CADDYFILE} --adapter caddyfile; exit 1; }}
unset CREDENTIALS_DIRECTORY

# Runtime + permanent, never --reload: a reload makes libvirt put its reject
# back in front of rodeo's DNAT accept and silently breaks the lab's own
# :8443/:30002 access (seen live 2026-11-25; rodeo's finalise re-asserts it).
if systemctl is-active -q firewalld; then
  firewall-cmd -q --add-service=https && firewall-cmd -q --permanent --add-service=https
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


def _last_lab(sudo_user: str | None) -> Path | None:
    """The lab ``rodeo up`` last ran (``~/.rodeo/last_lab`` of the deploying user, then root)."""
    import pwd

    homes = []
    if sudo_user:
        try:
            homes.append(Path(pwd.getpwnam(sudo_user).pw_dir))
        except KeyError:
            pass
    homes.append(Path("/root"))
    for home in homes:
        try:
            lab = Path((home / ".rodeo" / "last_lab").read_text().strip())
        except OSError:
            continue
        if lab.is_absolute() and lab.is_dir():
            return lab
    return None


def lab_secrets(sudo_user: str | None) -> dict[str, Any]:
    """What ``rodeo up`` resolved ``??keys`` from: the global secrets.yaml (the
    deploying user's over root's) and, over them, the lab's own
    ``.rodeo-secrets.yaml`` (suse-telco keeps mgmt_root_password there)."""
    import yaml

    from ..config import ConfigError, LAB_SECRETS_FILE, checked_lab_secrets
    from ..labfs import read_lab_file

    data: dict[str, Any] = {}
    for path in reversed(_secrets_files(sudo_user)):
        if path.is_file():
            data.update(yaml.safe_load(path.read_text()) or {})
    lab = _last_lab(sudo_user)
    if lab is not None:
        try:
            if checked_lab_secrets(lab) is not None:
                data.update(yaml.safe_load(read_lab_file(lab, LAB_SECRETS_FILE) or "") or {})
        except (ConfigError, OSError, yaml.YAMLError):
            pass
    return data


def lab_passwords(sudo_user: str | None, variables: dict[str, str] | None = None) -> dict[str, str]:
    """Track variables from the host's secrets.yaml (*variables*: TRACK_VAR -> secrets key;
    default: the virt-rodeo profile's)."""
    if variables is None:
        variables = load_profile().variables
    data = lab_secrets(sudo_user)
    return {var: str(data[key]) for var, key in variables.items() if data.get(key)}


def lab_credentials(sudo_user: str | None, profile: PlayerProfile) -> list[dict[str, str]]:
    """The profile's credentials with their passwords from secrets.yaml. A
    credential whose secret is missing is left out, never shown empty."""
    data = lab_secrets(sudo_user)
    return [{"label": c.label, "user": c.user, "password": str(data[c.secret])}
            for c in profile.credentials if data.get(c.secret)]


def write_credentials(creds: list[dict[str, str]], dest: Path = CREDENTIALS_FILE) -> None:
    """Root-only (0600) JSON the player reads and shows behind the lab login."""
    import os

    tmp = dest.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(creds, fh)
    os.replace(tmp, dest)
    dest.chmod(0o600)


# ---------------------------------------------------------------- run

def lab_kubeconfig(sudo_user: str | None, root_home: Path = Path("/root"),
                   name: str = "harvester-kubeconfig") -> Path:
    """The kubeconfig rodeo up wrote: the invoking user's ~/.rodeo, then root's."""
    import pwd

    homes = []
    if sudo_user:
        try:
            homes.append(Path(pwd.getpwnam(sudo_user).pw_dir))
        except KeyError:
            pass
    homes.append(root_home)
    for home in homes:
        path = home / ".rodeo" / name
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:  # e.g. an unreadable home: not this one
            continue
    raise RuntimeError(f"no ~/.rodeo/{name} for the invoking user or root: has rodeo up finished?")


def install_kubeconfig(src: Path, dest: Path = PLAYER_KUBECONFIG) -> None:
    import os

    data = src.read_bytes()
    fd = os.open(dest.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(dest.with_suffix(".tmp"), dest)
    dest.chmod(0o600)


ROOT_RODEO = Path("/root/.rodeo")


def ensure_root_kubeconfig(src: Path, dest: Path) -> bool:
    """Copy the lab kubeconfig to root's ~/.rodeo if root has none. True if copied."""
    if dest.exists() or src == dest:
        return False
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    install_kubeconfig(src, dest)
    return True


def install_tls(bundle: str, tls_dir: Path = TLS_DIR) -> None:
    """Write the pushed chain + key root-only (dir 0700, files 0600)."""
    import os

    chain, key = split_pem(bundle)
    tls_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    tls_dir.chmod(0o700)
    for name, body in (("cert", chain), ("key", key)):
        path = tls_dir / name
        tmp = tls_dir / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        os.replace(tmp, path)
        path.chmod(0o600)


def run_setup(opts: SetupOptions, *, sudo_user: str | None) -> dict:
    """Do the whole install. Returns URLs and the CA path, never a password."""
    opts.validate()
    rodeo_bin = shutil.which("rodeo") or "/usr/local/bin/rodeo"
    from . import auth

    # Before the units start: the player refuses to run without it.
    VAR.mkdir(mode=0o700, parents=True, exist_ok=True)
    kube = opts.profile.kubeconfig
    if kube:
        src = lab_kubeconfig(sudo_user, name=kube)
        install_kubeconfig(src)
        # Track scripts hard-code /root/.rodeo/<kubeconfig> (the layout the
        # track was written for). On a fleet host rodeo up wrote it for the SSH
        # user, so give root the same file. Never overwrite one root already has.
        ensure_root_kubeconfig(src, ROOT_RODEO / kube)
    if opts.tls_pem:
        install_tls(opts.tls_pem)
    access = auth.ensure(AUTH_FILE)
    write_credentials(lab_credentials(sudo_user, opts.profile))
    student = subprocess.run(["bash", "-c", STUDENT_SETUP_SCRIPT], input="", text=True,
                             capture_output=True, timeout=300)
    if student.returncode != 0 or "STUDENT_OK" not in student.stdout:
        raise RuntimeError(f"student user setup failed: {student.stderr.strip()[-400:]}")
    res = subprocess.run(["bash", "-c", install_script(opts, rodeo_bin, sudo_user)], text=True,
                         capture_output=True, timeout=900)
    if res.returncode != 0 or "PLAYER_UP" not in res.stdout:
        raise RuntimeError(f"player install failed (exit {res.returncode}): {res.stderr.strip()[-800:]}")

    from .runner import Runner

    runner = Runner(TRACK_DIR, STATE_DIR, sandbox_id=opts.sandbox_id, allow_empty=not opts.repo)
    seeded = []
    for key, value in lab_passwords(sudo_user, opts.profile.variables).items():
        runner.set_variable(key, value)
        seeded.append(key)
    n = opts.names
    return {
        "access_url": f"https://{n['player']}/enter?t={access.token}",
        "player": f"https://{n['player']}/",
        "profile": opts.profile.name,
        "uis": [{"key": ui.key, "label": ui.label, "url": f"https://{n[ui.key]}/"} for ui in opts.profile.uis],
        "ca_root": "" if opts.tls_pem else str(CA_ROOT),
        "variables_seeded": seeded,
        "chapters": len(runner.chapters),
        "credentials": len(opts.profile.credentials),
        "vm_access": {parts[1]: parts[2] for parts in (line.split() for line in res.stdout.splitlines())
                      if len(parts) == 3 and parts[0] == "VM_ACCESS"},
    }
