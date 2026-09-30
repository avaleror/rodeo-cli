"""Laptop-side claim portal orchestration (F5.2-F5.4).

The portal VM is passive: it never connects to lab hosts or cloud APIs. This
module pushes everything to it over SSH:

- ``provision_portal`` / ``deprovision_portal``: the VM itself (provider).
- ``portal_up``: copy ``rodeo/portal`` to the VM, systemd units, Caddy + TLS.
- ``portal_publish``: per lab host, read its UI passwords (and create the
  ``student`` SSH user when ``portal.student_ssh``), then upsert lab records.
- ``portal_admin``: every instructor command (status, invite, release, ...).

Secrets travel on stdin only (never argv) and never appear in error messages.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import shlex
import ssl
import subprocess
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..providers import get_provider
from .access import access_for_host
from .fanout import fanout
from .inventory import (
    PORTAL_HOST_ID,
    FleetHost,
    FleetInventory,
    PortalConfig,
    clear_portal,
    host_public_ip,
    merge_portal,
    require_provider,
)
from .ssh_exec import known_hosts_path, run_remote
from .student_access import SECRETS_PATH_SNIPPET, _PASSWORD_KEY, _check_host, student_ports

PORTAL_PKG_DIR = Path(__file__).resolve().parents[1] / "portal"
REMOTE_APP_DIR = "/opt/rodeo-portal"
REMOTE_DB = "/var/lib/rodeo-portal/portal.db"
PORTAL_USER = "rodeo-portal"
STUDENT_USER = "student"

# Caddy is not in the SLES 16 / Leap 16 repos; pinned release + SHA-512 from
# https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_checksums.txt
CADDY_VERSION = "2.11.4"
CADDY_SHA512 = (
    "8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff977"
    "7ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9"
)

_LABELS = {"harvester": "SUSE Virtualization (Harvester)", "rancher": "SUSE Rancher Prime"}


def require_portal(inventory: FleetInventory) -> PortalConfig:
    portal = inventory.portal
    if portal is None or not portal.enabled:
        raise ConfigError("no enabled portal: block in workshop.yaml (portal: {enabled: true})")
    return portal


def _portal_host(inventory: FleetInventory) -> FleetHost:
    portal = require_portal(inventory)
    if not portal.target:
        raise ConfigError(
            "the portal has no address yet: run `rodeo fleet provision` "
            "(or set portal.host for a bring-your-own portal machine)"
        )
    return FleetHost(id=PORTAL_HOST_ID, ssh=portal.target, public_ip=portal.public_ip)


def portal_url(inventory: FleetInventory) -> str:
    fqdn = require_portal(inventory).fqdn
    if not fqdn:
        raise ConfigError("portal has no public IP or hostname yet")
    return f"https://{fqdn}"


# ---------------------------------------------------------------- VM lifecycle
def provision_portal(inventory: FleetInventory, inventory_path: Path) -> Any:
    portal = require_portal(inventory)
    if portal.host:
        return None  # BYO machine: nothing to provision
    cfg = require_provider(inventory)
    provider = get_provider(str(cfg["type"]))
    if not hasattr(provider, "provision_portal"):
        raise ConfigError(f"provider {cfg['type']!r} cannot provision a portal VM yet")
    host = provider.provision_portal(
        cfg, workshop=inventory.name, ssh_user=inventory.ssh_user,
        instance_type=portal.instance_type,
    )
    merge_portal(inventory_path, ssh=host.ssh, public_ip=host.public_ip,
                 provider_id=host.provider_id)
    return host


def deprovision_portal(inventory: FleetInventory, inventory_path: Path) -> list[Any]:
    portal = inventory.portal
    if portal is None or portal.host:
        return []
    cfg = require_provider(inventory)
    provider = get_provider(str(cfg["type"]))
    if not hasattr(provider, "deprovision_portal"):
        return []
    results = provider.deprovision_portal(cfg, workshop=inventory.name)
    clear_portal(inventory_path)
    return results


# ---------------------------------------------------------------- install on the VM
def _package_b64() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(PORTAL_PKG_DIR.glob("*.py")):
            tar.add(f, arcname=f"rodeo_portal/{f.name}")
        for f in sorted((PORTAL_PKG_DIR / "static").glob("*")):  # logo, fonts, licences
            if f.is_file():
                tar.add(f, arcname=f"rodeo_portal/static/{f.name}")
    return base64.b64encode(buf.getvalue()).decode()


def _portal_unit() -> str:
    return f"""[Unit]
Description=rodeo claim portal
After=network-online.target

[Service]
User={PORTAL_USER}
Group={PORTAL_USER}
Environment=PYTHONPATH={REMOTE_APP_DIR} RODEO_PORTAL_DB={REMOTE_DB} PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m rodeo_portal serve --host 127.0.0.1 --port 8080
Restart=on-failure
StateDirectory=rodeo-portal
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
"""


def _caddy_unit() -> str:
    return """[Unit]
Description=rodeo claim portal TLS (Caddy)
After=network-online.target rodeo-portal.service

[Service]
User=rodeo-caddy
Group=rodeo-caddy
Environment=XDG_DATA_HOME=/var/lib/rodeo-caddy XDG_CONFIG_HOME=/var/lib/rodeo-caddy
ExecStart=/usr/local/bin/caddy run --config /etc/rodeo-portal/Caddyfile --adapter caddyfile
Restart=on-failure
StateDirectory=rodeo-caddy
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


def _caddyfile(fqdn: str) -> str:
    return f"""{{
\tadmin off
}}

{fqdn} {{
\tencode gzip
\theader Strict-Transport-Security "max-age=31536000"
\treverse_proxy 127.0.0.1:8080
}}
"""


def portal_up_script(fqdn: str, *, mode: str, title: str, code_letters: int = 4) -> str:
    """Idempotent install script run as root on the portal VM."""
    q = shlex.quote
    caddy_url = (
        f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
        f"caddy_{CADDY_VERSION}_linux_amd64.tar.gz"
    )
    admin = (
        f"runuser -u {PORTAL_USER} -- env PYTHONPATH={REMOTE_APP_DIR} RODEO_PORTAL_DB={REMOTE_DB} "
        "python3 -m rodeo_portal admin"
    )
    return f"""set -euo pipefail
command -v python3 >/dev/null || zypper -n in python3
id {PORTAL_USER} >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/rodeo-portal --shell /usr/sbin/nologin {PORTAL_USER}
id rodeo-caddy >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/rodeo-caddy --shell /usr/sbin/nologin rodeo-caddy
install -d -m 755 {REMOTE_APP_DIR} /etc/rodeo-portal
install -d -m 700 -o {PORTAL_USER} -g {PORTAL_USER} /var/lib/rodeo-portal
WORK=$(mktemp -d)  # private 0700 dir: no predictable /tmp names while running as root
trap 'rm -rf "$WORK"' EXIT
rm -rf {REMOTE_APP_DIR}/rodeo_portal
base64 -d > "$WORK/rodeo-portal.tgz" <<'PKG'
{_package_b64()}
PKG
tar -xzf "$WORK/rodeo-portal.tgz" -C {REMOTE_APP_DIR}
chown -R root:root {REMOTE_APP_DIR} && chmod -R a+rX {REMOTE_APP_DIR}
if [ "$(/usr/local/bin/caddy version 2>/dev/null | cut -d' ' -f1)" != "v{CADDY_VERSION}" ]; then
  curl -fsSL -o "$WORK/caddy.tgz" {q(caddy_url)}
  echo "{CADDY_SHA512}  $WORK/caddy.tgz" | sha512sum -c --quiet -
  tar -xzf "$WORK/caddy.tgz" -C "$WORK" caddy && install -m 755 "$WORK/caddy" /usr/local/bin/caddy
fi
cat > /etc/rodeo-portal/Caddyfile <<'CADDY'
{_caddyfile(fqdn)}CADDY
cat > /etc/systemd/system/rodeo-portal.service <<'UNIT'
{_portal_unit()}UNIT
cat > /etc/systemd/system/rodeo-caddy.service <<'UNIT'
{_caddy_unit()}UNIT
if systemctl is-active -q firewalld; then
  firewall-cmd -q --permanent --add-service=http --add-service=https && firewall-cmd -q --reload
fi
{admin} init --mode {q(mode)} --title {q(title)} --code-letters {int(code_letters)} >/dev/null
systemctl daemon-reload
systemctl enable -q rodeo-portal rodeo-caddy
systemctl restart rodeo-portal rodeo-caddy
echo PORTAL_UP
"""


def wait_https(url: str, *, timeout: float = 300.0) -> None:
    """Poll ``<url>/healthz`` with full TLS verification (Let's Encrypt must be real)."""
    ctx = ssl.create_default_context()
    deadline = time.monotonic() + timeout
    last = "no attempt"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/healthz", context=ctx, timeout=10) as r:
                if r.status == 200:
                    return
                last = f"HTTP {r.status}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
        time.sleep(5)
    raise ConfigError(f"portal not reachable over HTTPS at {url}: {last}")


def portal_up(inventory: FleetInventory, *, wait: bool = True) -> str:
    portal = require_portal(inventory)
    url = portal_url(inventory)
    script = portal_up_script(url.removeprefix("https://"), mode=portal.mode,
                              title=portal.title or inventory.name,
                              code_letters=portal.code_letters)
    res = run_remote(inventory, _portal_host(inventory), ["bash", "-s"], stdin=script,
                     timeout=600.0)
    if not res.ok or "PORTAL_UP" not in res.stdout:
        raise ConfigError(f"portal install failed (exit {res.rc}): {(res.stderr or res.stdout)[-600:]}")
    if wait:
        wait_https(url)
    return url


# ---------------------------------------------------------------- admin passthrough
def portal_admin(inventory: FleetInventory, args: list[str], *, stdin: str | None = None,
                 raw: bool = False) -> Any:
    cmd = ["runuser", "-u", PORTAL_USER, "--", "env", f"PYTHONPATH={REMOTE_APP_DIR}",
           f"RODEO_PORTAL_DB={REMOTE_DB}", "python3", "-m", "rodeo_portal", "admin", *args]
    res = run_remote(inventory, _portal_host(inventory), cmd, stdin=stdin, timeout=120.0)
    if raw:
        if not res.ok:
            raise ConfigError(f"portal admin {args[0]} failed (exit {res.rc})")
        return res.stdout
    try:
        out = json.loads(res.stdout.strip().splitlines()[-1]) if res.stdout.strip() else None
    except json.JSONDecodeError:
        out = None
    if isinstance(out, dict) and out.get("error"):
        raise ConfigError(str(out["error"]))
    if not res.ok or out is None:
        # stderr only: stdout may echo lab data
        raise ConfigError(f"portal admin {args[0]} failed (exit {res.rc}): {res.stderr.strip()[-300:]}")
    return out


def admin_link(inventory: FleetInventory) -> str:
    token = portal_admin(inventory, ["admin-token"])["token"]
    return f"{portal_url(inventory)}/admin/{token}"


# ---------------------------------------------------------------- student SSH (F5.4)
def student_keys_dir(workshop: str) -> Path:
    d = known_hosts_path(workshop).parent / "student-keys"  # same sanitised workshop dir
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def ensure_student_key(workshop: str, host_id: str) -> tuple[str, str]:
    """Per-lab ed25519 key pair, generated on the laptop; returns (private, public)."""
    priv = student_keys_dir(workshop) / host_id
    if not priv.is_file():
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{STUDENT_USER}@{workshop}-{host_id}",
             "-f", str(priv)],
            check=True, capture_output=True,
        )
    os.chmod(priv, 0o600)
    return priv.read_text(), Path(f"{priv}.pub").read_text().strip()


# Runs as root on the lab host. Creates an unprivileged user and then *proves* it
# is unprivileged: no sudo, no privileged group, cannot read /root (where the
# fleet-wide rodeo key lives, design R7). Any failed check aborts with exit 3.
STUDENT_SETUP_SCRIPT = f"""set -euo pipefail
U={STUDENT_USER}
PUB="$(cat)"
id "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"
for g in wheel sudo libvirt kvm qemu docker adm systemd-journal; do
  gpasswd -d "$U" "$g" >/dev/null 2>&1 || true
done
H=$(getent passwd "$U" | cut -d: -f6)
install -d -m 700 -o "$U" -g "$(id -gn "$U")" "$H/.ssh"
printf '%s\\n' "$PUB" > "$H/.ssh/authorized_keys"
chown "$U:$(id -gn "$U")" "$H/.ssh/authorized_keys"; chmod 600 "$H/.ssh/authorized_keys"
chmod 700 /root
# Explicit deny, sorted last so it wins over distro rules: SLES 16 grants every user
# "(ALL) ALL" with targetpw (safe only while root stays locked) and a NOPASSWD rule
# for cloudguestregistryauth. Validated with visudo before it is installed.
printf '%s ALL=(ALL) !ALL\n' "$U" > /etc/sudoers.d/.zz-rodeo-student.tmp
chmod 440 /etc/sudoers.d/.zz-rodeo-student.tmp
visudo -cqf /etc/sudoers.d/.zz-rodeo-student.tmp
mv -f /etc/sudoers.d/.zz-rodeo-student.tmp /etc/sudoers.d/zz-rodeo-student
# Ask sudo's own policy whether the student may run anything (runs nothing).
for c in /usr/bin/true /bin/bash /usr/bin/cloudguestregistryauth; do
  if sudo -n -l -U "$U" "$c" >/dev/null 2>&1; then echo "student may sudo $c" >&2; exit 3; fi
done
for g in wheel sudo libvirt kvm docker; do
  if id -nG "$U" | tr ' ' '\\n' | grep -qx "$g"; then echo "student in group $g" >&2; exit 3; fi
done
if runuser -u "$U" -- test -r /root/.ssh/id_ed25519; then echo "student can read the rodeo key" >&2; exit 3; fi
echo STUDENT_OK
"""


def ensure_student_user(inventory: FleetInventory, host: FleetHost) -> str:
    _, pub = ensure_student_key(inventory.name, host.id)
    res = run_remote(inventory, host, ["bash", "-c", STUDENT_SETUP_SCRIPT], stdin=pub,
                     timeout=120.0)
    if not res.ok or "STUDENT_OK" not in res.stdout:
        raise ConfigError(f"{host.id}: student user setup failed: {res.stderr.strip()[-300:]}")
    return STUDENT_USER


# ---------------------------------------------------------------- publish (F5.3)
# Prints the requested secrets.yaml values as one JSON object on stdout. Its
# output is parsed and never echoed, logged or put into an error message (R8).
READ_SECRETS_SCRIPT = SECRETS_PATH_SNIPPET + """
import json, os, sys
want = sys.argv[1:]
vals = {}
with open(_secrets_path()) as fh:
    for line in fh:
        key, sep, val = line.partition(":")
        if sep and key.strip() in want:
            vals[key.strip()] = val.strip().strip("\\"'")
print(json.dumps(vals))
"""


@dataclass(frozen=True)
class PublishRow:
    id: str
    ready: bool
    reason: str | None  # never contains a secret
    ssh: bool = False


def _lab_record(inventory: FleetInventory, host: FleetHost, *, timeout: float) -> tuple[dict[str, Any], PublishRow]:
    ports = student_ports(inventory)
    keys = [_PASSWORD_KEY[c] for c in ports]
    readiness = _check_host(inventory, host, keys, timeout=timeout)
    if not readiness.ready:
        return ({"id": host.id, "ready": False, "data": {}},
                PublishRow(host.id, False, readiness.reason))
    res = run_remote(inventory, host, ["python3", "-c", READ_SECRETS_SCRIPT, *keys], timeout=timeout)
    try:
        secrets = json.loads(res.stdout) if res.ok else None
    except json.JSONDecodeError:
        secrets = None
    if not isinstance(secrets, dict):
        return ({"id": host.id, "ready": False, "data": {}},
                PublishRow(host.id, False, f"could not read lab credentials (exit {res.rc})"))
    access = access_for_host(inventory, host)
    urls = {"harvester": access.harvester_url, "rancher": access.rancher_url}
    data: dict[str, Any] = {"components": [
        {"label": _LABELS[c], "url": urls[c], "user": "admin", "password": secrets.get(_PASSWORD_KEY[c], "")}
        for c in ports if urls.get(c)
    ]}
    ssh = False
    if require_portal(inventory).student_ssh:
        user = ensure_student_user(inventory, host)
        private, _ = ensure_student_key(inventory.name, host.id)
        data["ssh"] = {"user": user, "host": host_public_ip(host), "private_key": private}
        ssh = True
    return {"id": host.id, "ready": True, "data": data}, PublishRow(host.id, True, None, ssh)


def portal_publish(inventory: FleetInventory, *, concurrency: int = 8,
                   timeout: float = 120.0) -> list[PublishRow]:
    """Upsert every lab into the portal; not-ready labs are published as building."""
    require_portal(inventory)
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")

    def _work(h: FleetHost) -> tuple[dict[str, Any], PublishRow]:
        try:
            return _lab_record(inventory, h, timeout=timeout)
        except ConfigError as exc:
            return {"id": h.id, "ready": False, "data": {}}, PublishRow(h.id, False, str(exc))

    results = fanout(inventory.hosts, _work, concurrency=concurrency)
    records = [{**rec, "ord": i} for i, (rec, _) in enumerate(results)]
    portal_admin(inventory, ["import"], stdin=json.dumps(records))
    return [row for _, row in results]


# ---------------------------------------------------------------- deploy progress (watch)
# Union of every profile's phase order (suse_virt, suse_edge, rancher); `rodeo
# status` returns phases sorted by name, so the order is restored here.
PHASE_ORDER = ("kvm_host", "vms", "pxe_server", "boot", "cluster", "rancher", "elemental",
               "apply", "finalise", "custom_scripts")


def _ordered_phases(phases: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rank = {name: i for i, name in enumerate(PHASE_ORDER)}
    items = [(n, p) for n, p in phases.items() if isinstance(p, dict)]
    return sorted(items, key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))


def host_progress(status: Any, job_rec: Any | None) -> dict[str, Any]:
    """One lab's deploy progress from its ``rodeo status`` result and job record.

    Counts cacheable phases only: ``apply`` / ``custom_scripts`` re-run on every
    deploy and are never stored as completed. Contains no credential."""
    from ..service.status import phase_is_no_cache

    started = getattr(job_rec, "started_at", None) if job_rec else None
    state = getattr(job_rec, "state", None) if job_rec else None
    error = getattr(job_rec, "last_error", None) if job_rec else None
    if not status.ok or not status.report:
        return {"state": "failed" if state == "failed" else "unreachable", "done": 0, "total": 0,
                "phases": [], "current": None, "started_at": started, "finished_at": None,
                "error": (error or "host not reachable yet (still booting or installing rodeo?)")[:300]}
    report = status.report
    phases = _ordered_phases(report.get("phases") or {})
    cacheable = [(n, p) for n, p in phases if not phase_is_no_cache(n, p)]
    done = [n for n, p in cacheable if p.get("completed")]
    current = next((n for n, p in cacheable if not p.get("completed")), None)
    stamps = [str(p.get("timestamp")) for _, p in cacheable if p.get("timestamp")]
    complete = bool(cacheable) and current is None
    if complete:
        state = "ok"
    elif state not in ("failed", "running"):
        state = "running" if done else "pending"
    return {
        "state": state,
        "done": len(done),
        "total": len(cacheable),
        "current": current,
        "phases": [{"name": n, "done": bool(p.get("completed")), "at": p.get("timestamp")}
                   for n, p in cacheable],
        "started_at": started,
        "finished_at": max(stamps) if complete and stamps else None,
        "error": (str(error)[:300] if state == "failed" and error else None),
        "vip": bool(report.get("vip_reachable")),
    }


@dataclass(frozen=True)
class WatchTick:
    ready: list[str]
    building: list[str]
    failed: list[str]
    newly_published: list[str]
    done: bool


def portal_watch(
    inventory: FleetInventory,
    inventory_path: Path,
    *,
    interval: float = 60.0,
    concurrency: int = 8,
    timeout: float = 120.0,
    on_tick: Any = None,
    sleep: Any = time.sleep,
    max_ticks: int | None = None,
) -> WatchTick:
    """Push deploy progress to the portal every ``interval`` seconds and publish
    each lab the moment it is ready (so it becomes claimable). Returns when every
    lab is published ready, or after ``max_ticks``."""
    from .deploy import apply_status_to_job
    from .job import job_path_for, load_job, save_job
    from .status import fleet_status

    require_portal(inventory)
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")
    order = {h.id: i for i, h in enumerate(inventory.hosts)}
    job_file = job_path_for(inventory_path)
    published: set[str] = set()
    first = True
    ticks = 0
    while True:
        statuses = fleet_status(inventory, inventory.hosts, concurrency=concurrency, timeout=timeout)
        job = None
        if job_file.is_file():
            # Only reachable hosts update the job: early in a deploy `rodeo status`
            # fails while rodeo is still installing, which is not a failed deploy.
            job = apply_status_to_job(load_job(job_file), [st for st in statuses if st.ok])
            save_job(job, job_file)
        by_id = {st.id: st for st in statuses}
        progress = {
            h.id: host_progress(by_id[h.id], job.hosts.get(h.id) if job else None)
            for h in inventory.hosts
        }
        ready_now = [h for h in inventory.hosts
                     if progress[h.id]["state"] == "ok" and h.id not in published]
        records: list[dict[str, Any]] = []
        if ready_now:
            def _work(h: FleetHost) -> tuple[dict[str, Any], PublishRow]:
                try:
                    return _lab_record(inventory, h, timeout=timeout)
                except ConfigError as exc:
                    return {"id": h.id, "ready": False, "data": {}}, PublishRow(h.id, False, str(exc))

            for rec, row in fanout(ready_now, _work, concurrency=concurrency):
                if row.ready:
                    published.add(rec["id"])
                    records.append({**rec, "ord": order[rec["id"]]})
        if first:  # every lab appears on the portal (as building) from the first tick
            records += [{"id": h.id, "ready": False, "data": {}, "ord": order[h.id]}
                        for h in inventory.hosts
                        if h.id not in published and h.id not in {r["id"] for r in records}]
            first = False
        if records:
            portal_admin(inventory, ["import"], stdin=json.dumps(records))
        portal_admin(inventory, ["progress"], stdin=json.dumps(progress))
        ticks += 1
        tick = WatchTick(
            ready=sorted(published),
            building=[h.id for h in inventory.hosts
                      if h.id not in published and progress[h.id]["state"] != "failed"],
            failed=[h.id for h in inventory.hosts if progress[h.id]["state"] == "failed"],
            newly_published=[r["id"] for r in records if r.get("ready")],
            done=len(published) == len(inventory.hosts),
        )
        if on_tick:
            on_tick(tick, progress)
        if tick.done or (max_ticks is not None and ticks >= max_ticks):
            return tick
        sleep(interval)


# ---------------------------------------------------------------- roster invites
def read_roster(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as fh:
        rows = [
            {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(fh)
        ]
    if not rows or "email" not in rows[0]:
        raise ConfigError(f"{path}: roster CSV needs a header with at least name,email")
    return rows


def portal_invite(inventory: FleetInventory, inventory_path: Path, *, rotate: bool = False) -> tuple[Path, list[dict[str, Any]]]:
    """Reserve labs for the roster; write ``<workshop>-invites.csv`` (0600) for mail-merge."""
    portal = require_portal(inventory)
    if portal.roster is None:
        raise ConfigError("portal.roster is not set in workshop.yaml")
    results = portal_admin(inventory, ["invite", *(["--rotate"] if rotate else [])],
                           stdin=json.dumps(read_roster(portal.roster)))
    out = Path(inventory_path).resolve().parent / f"{inventory.name}-invites.csv"
    existing: dict[str, dict[str, str]] = {}
    if out.is_file():
        with open(out, newline="") as fh:
            existing = {r["email"]: r for r in csv.DictReader(fh)}
    base = portal_url(inventory)
    for r in results:
        prev = existing.get(r["email"], {})
        url = f"{base}/l/{r['token']}" if r.get("token") else prev.get("url", "")
        existing[r["email"]] = {"name": r["name"], "email": r["email"], "lab": r["lab"], "url": url}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "email", "lab", "url"])
        w.writeheader()
        w.writerows(existing.values())
    os.chmod(out, 0o600)
    return out, results
