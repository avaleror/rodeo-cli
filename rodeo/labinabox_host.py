"""Host-side helpers for deploying through lab-in-a-box.

lab-in-a-box normally runs on a separate automation VM that reaches the
hypervisor over SSH. On a rodeo host both roles are the same machine: rodeo
installs lab-in-a-box's scripts locally and points its REMOTE_HOST at the
libvirt gateway address (the host itself, as the guests see it), so every
``ssh root@REMOTE_HOST`` / rsync lab-in-a-box does lands back on this host.

Nearly everything here is pure (commands and file contents are returned, not
run) so the phase in rodeo/engine/labinabox_phase.py stays a thin executor and
the logic is unit-testable without root or libvirt. image_age_days() is the one
exception: it asks the image's server for its Last-Modified date.
"""
from __future__ import annotations

import email.utils
import ipaddress
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from .config import ConfigError
from .paths import rodeo_dir

# Upstream lab-in-a-box and the ref rodeo deploys by default. Precedence:
# RODEO_LABINABOX_REPO / RODEO_LABINABOX_REF, then the plan's
# lab_in_a_box.source.repo / .ref, then these defaults. RODEO_LABINABOX_PATH
# (a local checkout) replaces the fetch altogether.
LIAB_REPO = "https://github.com/SUSE-Technical-Marketing/lab-in-a-box"
LIAB_REF = "13800a34889a14bedc334307c5aaba8af547a81e"
LIAB_REPO_ENV = "RODEO_LABINABOX_REPO"
LIAB_REF_ENV = "RODEO_LABINABOX_REF"
LIAB_PATH_ENV = "RODEO_LABINABOX_PATH"

# Where lab-in-a-box's installer puts the orchestration scripts.
LIAB_BIN = Path("/usr/local/bin")
LAB_CREATION_CFG = Path("/etc/lab_creation.cfg")
# DNSService writes zone files here unconditionally; rodeo serves lab DNS from
# libvirt's dnsmasq instead, but the directory must exist for those writes.
NAMED_ZONE_DIR = Path("/var/lib/named")

_CFG_MARKER = "# Managed by rodeo (lab-in-a-box platform) — regenerated on every deploy."
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def source(cfg: dict) -> tuple[str, str]:
    """(repo, ref) to deploy: environment overrides, then the plan's
    lab_in_a_box.source block, then LIAB_REPO / LIAB_REF."""
    src = (cfg.get("lab_in_a_box") or {}).get("source") or {}
    repo = os.environ.get(LIAB_REPO_ENV, "").strip() or str(src.get("repo") or LIAB_REPO)
    ref = os.environ.get(LIAB_REF_ENV, "").strip() or str(src.get("ref") or LIAB_REF)
    if not _REF_RE.match(ref) or ".." in ref or ref.startswith("-"):
        raise ConfigError(f"lab-in-a-box ref '{ref}' is not a valid git ref")
    return repo, ref


def checkout_dir(ref: str) -> Path:
    """Where rodeo keeps the checkout of one lab-in-a-box ref."""
    return rodeo_dir() / "vendor" / "lab-in-a-box" / ref.replace("/", "_")


def local_override() -> Path | None:
    """A developer's local lab-in-a-box checkout, if RODEO_LABINABOX_PATH is set."""
    value = os.environ.get(LIAB_PATH_ENV)
    return Path(value).expanduser().resolve() if value else None


def fetch_commands(repo: str, ref: str, dest: Path) -> list[list[str]]:
    """git commands that check out exactly `ref` (branch, tag or SHA) into dest.

    Shallow fetch of the single ref, IPv4 only (Instruqt hosts have no IPv6
    route while GitHub publishes AAAA records).
    """
    git = ["git", "-C", str(dest)]
    return [
        ["git", "init", "-q", str(dest)],
        git + ["fetch", "--ipv4", "-q", "--depth", "1", repo, ref],
        git + ["checkout", "-q", "--force", "FETCH_HEAD"],
    ]


def install_command(checkout: Path, python_bin: str) -> list[str]:
    """Run lab-in-a-box's own installer: scripts only, no web UI, no backup tarball."""
    return [
        "env",
        "_webui_mode=off",
        "_backup=off",
        f"_python_bin={python_bin}",
        f"_scripts_path={checkout}",
        "bash",
        str(checkout / "install_automation_node_scripts.sh"),
    ]


def render_lab_creation_cfg(remote_host: str, root_pubkey: str) -> str:
    """/etc/lab_creation.cfg for a colocated hypervisor + automation node.

    Holds no credentials: VMs get the host root key (ROOT_SSH_KEY); SCC and
    addon secrets travel in lab.json, resolved from ~/.rodeo/secrets.yaml.
    """
    return "\n".join([
        _CFG_MARKER,
        f"ROOT_SSH_KEY={shlex.quote(root_pubkey.strip())}",
        f"REMOTE_HOST={shlex.quote(remote_host)}",
        "VIRT_SRV='qemu:///system'",
        f"mysource={shlex.quote(remote_host)}",
        # No BIND here: lab-in-a-box also writes each VM's name into /etc/hosts
        # (incl. cloud VMs, whose IP is only known once they exist).
        "LAB_HOSTS_FILE=/etc/hosts",
        "",
    ])


def cfg_is_rodeo_managed(text: str) -> bool:
    """True if /etc/lab_creation.cfg was written by rodeo (safe to overwrite)."""
    return text.startswith(_CFG_MARKER)


def dns_host_entries(lab: dict, aliases: dict | None = None) -> list[tuple[str, list[str]]]:
    """(ip, [fqdn, short, *aliases]) per lab.json node, for libvirt's dnsmasq.

    `aliases` (lab_in_a_box.dns_aliases: {short name: [extra names]}) lets a node
    answer to a fixed name in every lab instance — e.g. the name a pre-built
    server image was installed with.
    """
    entries = []
    for fqdn, node in (lab.get("nodes") or {}).items():
        ip = node.get("myip")
        if ip:
            short = fqdn.split(".")[0]
            names = [fqdn, short]
            names += [a for a in (aliases or {}).get(short, []) if a not in names]
            entries.append((ip, names))
    return entries


def hosts_block(plan: str, lab: dict) -> str:
    """This lab's /etc/hosts block (lab-in-a-box reaches every VM by FQDN)."""
    lines = [f"# BEGIN RODEO LAB-IN-A-BOX {plan}"]
    lines += [f"{ip}  {'  '.join(names)}" for ip, names in dns_host_entries(lab)]
    lines.append(f"# END RODEO LAB-IN-A-BOX {plan}")
    return "\n".join(lines) + "\n"


def replace_hosts_block(text: str, plan: str, block: str | None) -> str:
    """/etc/hosts text with this lab's block replaced (or removed when block is None)."""
    begin, end = f"# BEGIN RODEO LAB-IN-A-BOX {plan}", f"# END RODEO LAB-IN-A-BOX {plan}"
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == begin:
            skipping = True
            continue
        if skipping:
            skipping = line.strip() != end
            continue
        out.append(line)
    result = "".join(out)
    if block:
        result += ("" if not result or result.endswith("\n") else "\n") + block
    return result


def teardown_commands(lab: dict, exposed_services: list[dict], net_name: str | None,
                      uri: str = "qemu:///system") -> list[list[str]]:
    """What `rodeo clean` removes for one managed lab: its port forwards, and —
    when it has its own libvirt network (an instance > 0) — that network."""
    cmds = [c[:3] + [c[3].replace("--add-", "--remove-", 1)]
            for c in forward_port_commands(exposed_services, lab)]
    if net_name:
        cmds += [["virsh", "-c", uri, "net-destroy", net_name], ["virsh", "-c", uri, "net-undefine", net_name]]
    return cmds


def dns_host_xml(ip: str, names: list[str]) -> str:
    hostnames = "".join(f"<hostname>{n}</hostname>" for n in names)
    return f"<host ip='{ip}'>{hostnames}</host>"


def net_update_command(network: str, ip: str, names: list[str], uri: str = "qemu:///system") -> list[str]:
    """Add one DNS host record to a running libvirt network, live and persistent."""
    return [
        "virsh", "-c", uri, "net-update", network, "add-last", "dns-host",
        dns_host_xml(ip, names), "--live", "--config",
    ]


def base_images(cfg: dict) -> list[dict]:
    """Validated lab_in_a_box.images entries: {name, url, sha256, sha256_url, max_age_days}.

    Each is a base qcow2/ISO lab-in-a-box copies VM disks from (a node's
    ISO_IMAGE); rodeo/labinabox.py turns it into that node's ISO_URL /
    ISO_SHA256 / ISO_SHA256_URL and lab-in-a-box downloads it. Verification is
    mandatory: a pinned `sha256`, or for images republished under a fixed name,
    the `sha256_url` the publisher serves next to it.
    """
    from .labinabox import effective_overlay

    out = []
    for entry in effective_overlay(cfg).get("images") or []:
        name = str(entry.get("name", ""))
        urls = entry.get("url") if isinstance(entry.get("url"), list) else [entry.get("url", "")]
        urls = [str(u) for u in urls]
        url = urls[0] if urls else ""
        sha = str(entry.get("sha256", "")).lower()
        sha_url = str(entry.get("sha256_url", ""))
        unresolved = [v for v in (*urls, sha, sha_url) if v.startswith("??")]
        if unresolved:
            raise ConfigError(
                f"lab_in_a_box.images[{name}]: secret {unresolved[0]} is not set — "
                "add it to ~/.rodeo/secrets.yaml (rodeo up asks for it)"
            )
        if not _IMAGE_NAME_RE.match(name):
            raise ConfigError(f"lab_in_a_box.images: '{name}' is not a plain file name")
        if not urls or not all(u.startswith(("https://", "http://", "file://")) for u in urls):
            raise ConfigError(f"lab_in_a_box.images[{name}]: url (or each mirror in a url list) "
                              "must be http(s):// or file://")
        if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ConfigError(f"lab_in_a_box.images[{name}]: sha256 must be a 64-character hex digest")
        if not sha and not sha_url.startswith("https://"):
            raise ConfigError(f"lab_in_a_box.images[{name}]: set sha256, or sha256_url (https://)")
        max_age = entry.get("max_age_days")
        if max_age is not None and (not isinstance(max_age, int) or max_age < 1):
            raise ConfigError(f"lab_in_a_box.images[{name}]: max_age_days must be a positive integer")
        out.append({"name": name, "url": url, "sha256": sha, "sha256_url": sha_url, "max_age_days": max_age})
    return out


def image_age_days(url: str, now: float | None = None) -> float | None:
    """Age of a published image (HTTP Last-Modified, or a file:// file's mtime), None if unknown."""
    now = time.time() if now is None else now
    if url.startswith("file://"):
        try:
            return (now - Path(url[len("file://"):]).stat().st_mtime) / 86400
        except OSError:
            return None
    r = subprocess.run(["curl", "-4", "-sIL", "--max-time", "20", url], capture_output=True, text=True)
    stamp = None
    for line in r.stdout.splitlines():
        if line.lower().startswith("last-modified:"):
            stamp = line.split(":", 1)[1].strip()  # the last redirect hop wins
    if not stamp:
        return None
    try:
        return (now - email.utils.parsedate_to_datetime(stamp).timestamp()) / 86400
    except (TypeError, ValueError):
        return None


def render_network_xml(net: dict, lab: dict, aliases: dict | None = None) -> str:
    """libvirt NAT network with a DNS + static DHCP entry for every lab node.

    Same shape as the vms role's network.xml.j2; used when the network does not
    exist yet (lab-in-a-box attaches VMs to the bridge but never defines it).
    """
    cidr = ipaddress.ip_network(net["cidr"], strict=False)
    hosts = [
        f"    {dns_host_xml(ip, names)}"
        for ip, names in dns_host_entries(lab, aliases)
    ]
    dhcp = [
        f"      <host mac='{node['mymac']}' name='{fqdn.split('.')[0]}' ip='{node['myip']}'/>"
        for fqdn, node in (lab.get("nodes") or {}).items()
        if node.get("mymac") and node.get("myip")
    ]
    range_ = net.get("dhcp_range") or {}
    range_line = (
        [f"      <range start='{range_['start']}' end='{range_['end']}'/>"]
        if range_.get("start") and range_.get("end") else []
    )
    return "\n".join([
        "<network>",
        f"  <name>{net.get('name', 'default')}</name>",
        "  <forward mode='nat'>",
        "    <nat>",
        "      <port start='1024' end='65535'/>",
        "    </nat>",
        "  </forward>",
        f"  <bridge name='{net.get('bridge', 'virbr0')}' stp='on' delay='0'/>",
        f"  <domain name='{net['domain']}' localOnly='yes'/>",
        "  <dns>",
        *hosts,
        "  </dns>",
        f"  <ip address='{net['gateway']}' netmask='{cidr.netmask}'>",
        "    <dhcp>",
        *range_line,
        *dhcp,
        "    </dhcp>",
        "  </ip>",
        "</network>",
        "",
    ])


def forward_port_commands(exposed_services: list[dict], lab: dict) -> list[list[str]]:
    """firewalld rules exposing each exposed_services entry on the host.

    kvm_host only forwards the fixed Rancher/Harvester ports; here `target` is
    any lab node's short name, resolved to its lab.json IP. Permanent rules,
    applied by the firewalld reload that follows.
    """
    ips = {fqdn.split(".")[0]: node.get("myip") for fqdn, node in (lab.get("nodes") or {}).items()}
    cmds = []
    for svc in exposed_services:
        target = str(svc.get("target", ""))
        ip = ips.get(target)
        if not ip:
            raise ConfigError(
                f"exposed_services '{svc.get('name', '?')}': target '{target}' is not a lab node"
            )
        proto = svc.get("proto", "tcp")
        port, toport = int(svc["host_port"]), int(svc["guest_port"])
        base = ["firewall-cmd", "--permanent", "--zone=public"]
        cmds.append(base + [f"--add-port={port}/{proto}"])
        cmds.append(base + [f"--add-forward-port=port={port}:proto={proto}:toport={toport}:toaddr={ip}"])
    return cmds

# ── Existing lab-in-a-box hosts (lab_in_a_box.target) ──────────────────────
#
#   target:
#     mode: auto        # auto (default) | managed | existing
#     host: automation.example.lab   # a remote lab-in-a-box automation node
#     ssh_user: root
#     libvirt_uri: qemu+ssh://root@kvm1.example.lab/system   # where its VMs run
#                  (for rodeo status/start/stop/restart; default: this host)
#
# managed:  rodeo installs lab-in-a-box on this host and owns its config.
# existing: a lab-in-a-box that someone already set up — this host (auto-detected
#           from a /etc/lab_creation.cfg rodeo didn't write) or `host` over SSH.
#           rodeo then leaves its install, network, firewall and keys alone and
#           only hands it lab.json.

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")
_PLAN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def target(cfg: dict) -> dict:
    """Validated lab_in_a_box.target: {mode, host, ssh_user}."""
    spec = (cfg.get("lab_in_a_box") or {}).get("target") or {}
    mode = str(spec.get("mode") or "auto")
    host = str(spec.get("host") or "")
    user = str(spec.get("ssh_user") or "root")
    if mode not in ("auto", "managed", "existing"):
        raise ConfigError(f"lab_in_a_box.target.mode '{mode}': use auto, managed or existing")
    if host and not _HOST_RE.match(host):
        raise ConfigError(f"lab_in_a_box.target.host '{host}' is not a plain host name or IP")
    if host and mode == "managed":
        raise ConfigError("lab_in_a_box.target.host needs mode existing (or auto): rodeo only "
                          "installs lab-in-a-box on the host it runs on")
    if not _USER_RE.match(user):
        raise ConfigError(f"lab_in_a_box.target.ssh_user '{user}' is not a valid user name")
    uri = str(spec.get("libvirt_uri") or "")
    if uri and not re.fullmatch(r"qemu(\+ssh|\+tcp|\+tls)?://[^\s'\"]*/system(\?[^\s'\"]*)?", uri):
        raise ConfigError(f"lab_in_a_box.target.libvirt_uri '{uri}' is not a qemu:// libvirt URI")
    return {"mode": mode, "host": host or None, "ssh_user": user, "libvirt_uri": uri or None}


def effective_mode(cfg: dict) -> str:
    """'managed' or 'existing' — auto resolves against this host's own setup."""
    spec = target(cfg)
    if spec["host"]:
        return "existing"
    if spec["mode"] != "auto":
        return spec["mode"]
    foreign_cfg = LAB_CREATION_CFG.exists() and not cfg_is_rodeo_managed(LAB_CREATION_CFG.read_text())
    return "existing" if foreign_cfg and (LIAB_BIN / "setup_lab.py").exists() else "managed"


def remote_lab_dir(plan: str) -> str:
    """Per-lab directory on a remote lab-in-a-box host (relative to the SSH user's home)."""
    if not _PLAN_RE.match(plan):
        raise ConfigError(f"plan name '{plan}' can't be used as a directory name on the remote host")
    return f".rodeo-labs/{plan}"


def owner_marker(plan: str) -> str:
    """Written next to the remote lab.json; `rodeo clean` destroys only a lab carrying it."""
    return f"rodeo lab {plan}"


def remote_ssh(spec: dict, identity: str) -> list[str]:
    return [
        "ssh", "-i", identity, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15", f"{spec['ssh_user']}@{spec['host']}",
    ]


def remote_upload_command(plan: str) -> str:
    """Store lab.json (read from stdin — never in argv) privately, plus the owner marker."""
    d = remote_lab_dir(plan)
    return (f"umask 077 && mkdir -p {d} && cat > {d}/lab.json.tmp && mv {d}/lab.json.tmp {d}/lab.json"
            f" && printf '%s\\n' {shlex.quote(owner_marker(plan))} > {d}/owner")


def setup_command(lab_json: str, parallel: int | None = None) -> str:
    cmd = f"{LIAB_BIN / 'setup_lab.py'} --keep"
    if parallel:
        cmd += f" --parallel={int(parallel)}"
    return f"{cmd} {lab_json}"


def remote_destroy_command(plan: str) -> str:
    """destroy_lab.py for this lab only — refuses unless the owner marker matches."""
    d = remote_lab_dir(plan)
    return (f"test \"$(cat {d}/owner 2>/dev/null)\" = {shlex.quote(owner_marker(plan))}"
            f" && {LIAB_BIN / 'destroy_lab.py'} {d}/lab.json && rm -rf {d}")


def addons_used(lab: dict) -> set[str]:
    """Addon names lab.json asks for: node addons, kcluster addons, addon sections."""
    names = {k for k in lab if k not in ("nodes", "common", "kclusters")}
    for node in (lab.get("nodes") or {}).values():
        for entry in node.get("addons") or []:
            names.update(entry if isinstance(entry, dict) else [entry])
    for cluster in (lab.get("kclusters") or {}).values():
        names.update(cluster.get("addons") or [])
    return names


def schema_commands(addons: set[str]) -> dict[str, str]:
    """Shell commands printing the installed lab-in-a-box schema (base + each addon)."""
    cmds = {"_base": f"{LIAB_BIN / 'lab_schema'} --base json"}
    for addon in sorted(addons):
        cmds[addon] = f"{LIAB_BIN / 'lab_schema'} {LIAB_BIN / ('install_' + addon)} json"
    return cmds


def schema_from_outputs(outputs: dict[str, str]) -> dict:
    """Same shape as tests/fixtures/labinabox_schema/schema.json, from schema_commands' output."""
    base = json.loads(outputs["_base"])["sections"]
    return {
        "common": [f["name"] for f in base["common"]["fields"]],
        "nodes": [f["name"] for f in base["nodes"]["fields"]],
        "kclusters": [f["name"] for f in base.get("kclusters", {}).get("fields", [])],
        "addons": {name: [f["name"] for f in json.loads(out)["fields"]]
                   for name, out in outputs.items() if name != "_base"},
    }


# Free memory on every hypervisor an existing lab-in-a-box drives: KVM_HOSTS, else
# REMOTE_HOST, else this machine — reached the way lab-in-a-box itself does
# (VIRT_SRV for REMOTE_HOST, qemu+ssh://root@<host> for the others). Sourcing
# lab_creation.cfg only sets shell variables; nothing of it is printed.
CAPACITY_COMMAND = r"""
. /etc/lab_creation.cfg 2>/dev/null
hosts="${KVM_HOSTS:-${REMOTE_HOST:-localhost}}"
for h in $hosts; do
  if [ "$h" = "localhost" ]; then uri="${VIRT_SRV:-qemu:///system}"
  elif [ "$h" = "$REMOTE_HOST" ] && [ -n "$VIRT_SRV" ]; then uri="$VIRT_SRV"
  else uri="qemu+ssh://root@$h/system?keyfile=.ssh/id_rsa"; fi
  echo "HOST $h"
  virsh -c "$uri" freecell --all 2>/dev/null | grep '^Total:' || echo "Total: unknown"
done
"""


def parse_capacity(output: str) -> dict[str, int | None]:
    """{hypervisor: MiB free (None if unreadable)} from CAPACITY_COMMAND's output."""
    free: dict[str, int | None] = {}
    current = None
    for line in output.splitlines():
        if line.startswith("HOST "):
            current = line.split(None, 1)[1].strip()
            free[current] = None
        elif current and line.startswith("Total:"):
            match = re.search(r"(\d+)\s*KiB", line)
            free[current] = int(match.group(1)) // 1024 if match else None
    return free


def capacity_warnings(lab: dict, free: dict[str, int | None]) -> list[str]:
    """Why the lab may not fit (empty: it fits). Warnings only — --keep may reuse VMs."""
    common = lab.get("common") or {}
    sizes = [int(n.get("VM_MEM") or common.get("VM_MEM") or 0) for n in (lab.get("nodes") or {}).values()]
    known = {h: m for h, m in free.items() if m is not None}
    out = [f"could not read free memory on {h}" for h, m in free.items() if m is None]
    if not known:
        return out or ["could not read any hypervisor's free memory"]
    need, total = sum(sizes), sum(known.values())
    if need > total:
        out.append(f"the lab needs {need // 1024} GiB of VM memory, the hypervisor(s) have "
                   f"{total // 1024} GiB free in total")
    if sizes and max(sizes) > max(known.values()):
        out.append(f"the largest VM needs {max(sizes) // 1024} GiB, the roomiest hypervisor has "
                   f"{max(known.values()) // 1024} GiB free")
    return out


def lab_memory_mib(lab: dict) -> int:
    common = lab.get("common") or {}
    return sum(int(n.get("VM_MEM") or common.get("VM_MEM") or 0) for n in (lab.get("nodes") or {}).values())


# ── Cloud VMs (lab_in_a_box.cloud) ─────────────────────────────────────────
#
#   cloud:
#     cloudtype: aws            # a lab-in-a-box compute backend
#     account: aws-lab          # its credential file / cloud_account name
#     settings:                 # that backend's keys (see lab-in-a-box's README);
#       AWS_REGION: eu-north-1  #   ??secrets are always operator secrets
#       AWS_ACCESS_KEY_ID: "??aws_access_key_id"
#       AWS_SECRET_ACCESS_KEY: "??aws_secret_access_key"
#
# lab-in-a-box creates every node in that account instead of on a KVM host, so
# rodeo's host needs no nested KVM: it only runs lab-in-a-box. Omit `settings`
# to use an account file that already exists on the lab-in-a-box host.

CLOUD_TYPES = ("aws", "gcp", "hetzner", "alibaba", "scaleway", "upcloud", "ovhcloud", "exoscale")
# CLIs lab-in-a-box drives for these backends (the others talk REST directly).
CLOUD_CLIS = {"aws": "aws", "gcp": "gcloud", "alibaba": "aliyun", "exoscale": "exo"}
CREDENTIALS_DIR = Path("/etc/lab_creation/credentials")
_CREDS_MARKER = "# Managed by rodeo (lab-in-a-box platform) — removed by `rodeo clean`."
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SETTING_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def cloud(cfg: dict) -> dict | None:
    """Validated lab_in_a_box.cloud (selected variant applied), or None."""
    from .labinabox import effective_overlay

    spec = effective_overlay(cfg).get("cloud")
    if not spec:
        return None
    cloudtype = str(spec.get("cloudtype") or "")
    account = str(spec.get("account") or "")
    settings = spec.get("settings") or {}
    if cloudtype not in CLOUD_TYPES:
        raise ConfigError(f"lab_in_a_box.cloud.cloudtype '{cloudtype}': one of {', '.join(CLOUD_TYPES)}")
    if not _ACCOUNT_RE.match(account):
        raise ConfigError(f"lab_in_a_box.cloud.account '{account}' is not a plain name")
    if not isinstance(settings, dict) or not all(_SETTING_RE.match(str(k)) for k in settings):
        raise ConfigError("lab_in_a_box.cloud.settings must map UPPER_CASE backend keys to values")
    return {"cloudtype": cloudtype, "account": account,
            "settings": {str(k): str(v) for k, v in settings.items()}}


def cloud_secret_keys(plan: dict) -> set[str]:
    """??keys used in cloud.settings — cloud credentials are never generated."""
    from .labinabox import effective_overlay
    from .secretgen import plan_secret_keys

    try:
        spec = effective_overlay(plan).get("cloud") or {}
    except ConfigError:
        return set()
    return plan_secret_keys(spec.get("settings") or {})


def credentials_path(account: str) -> Path:
    return CREDENTIALS_DIR / f"{account}.yaml"


def render_credentials(spec: dict) -> str:
    """lab-in-a-box credential file for this account (plaintext, 0600, root-only)."""
    body = {"cloudtype": spec["cloudtype"], "unencrypted": True, **spec["settings"]}
    return _CREDS_MARKER + "\n" + json.dumps(body, indent=1) + "\n"   # JSON is valid YAML


def credentials_are_rodeo_managed(path: Path) -> bool:
    try:
        return path.read_text().startswith(_CREDS_MARKER)
    except OSError:
        return False


def hosts_file_ips(text: str, fqdns: list[str]) -> dict[str, str]:
    """{short name: ip} for lab nodes, from lab-in-a-box's LAB_HOSTS_FILE lines."""
    wanted = {f: f.split(".")[0] for f in fqdns}
    found = {}
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and line.rstrip().endswith("# lab-in-a-box") and parts[1] in wanted:
            found[wanted[parts[1]]] = parts[0]
    return found


def image_ages(cfg: dict) -> list[tuple[str, float | None, int]]:
    """(name, age in days or None, max_age_days) for each image with a max_age_days."""
    return [(img["name"], image_age_days(img["url"]), img["max_age_days"])
            for img in base_images(cfg) if img["max_age_days"]]


def power_argv(cfg: dict, action: str, fqdns: list[str], identity: str | None = None) -> list[str]:
    """lab-in-a-box's vm_power.py for this lab — here, or on its remote host."""
    if action not in ("status", "start", "stop"):
        raise ConfigError(f"unknown power action {action!r}")
    spec = target(cfg)
    tool = str(LIAB_BIN / "vm_power.py")
    if spec["host"]:
        lab_json = f"{remote_lab_dir(cfg.get('name', 'rodeo'))}/lab.json"
        return remote_ssh(spec, identity or "") + [" ".join([tool, lab_json, action, *fqdns])]
    base = Path(cfg.get("config_dir") or cfg.get("plan_dir") or ".")
    return [tool, str(base / ".labinabox" / "lab.json"), action, *fqdns]


def cloud_power(cfg: dict, action: str, vm_names: list[str]) -> dict[str, str]:
    """{vm short name: state} for a cloud lab, via lab-in-a-box's vm_power.py."""
    identity = None
    if target(cfg)["host"]:
        from .ssh_key import ensure_rodeo_ssh_key

        identity = str(ensure_rodeo_ssh_key())
    vms = cfg.get("vms") or {}
    fqdns = [(vms.get(n) or {}).get("domain") or n for n in vm_names]
    r = subprocess.run(power_argv(cfg, action, fqdns, identity), capture_output=True, text=True, timeout=600)
    try:
        states = json.loads(r.stdout)
    except ValueError:
        detail = (r.stderr or r.stdout).strip()[:200] or f"exit {r.returncode}"
        return {n: f"error: {detail}" for n in vm_names}
    return {n: str(states.get(f, "unknown")) for n, f in zip(vm_names, fqdns)}
