"""Phases of the lab-in-a-box platform (rodeo/profiles/labinabox.py).

labinabox_host  prepares this KVM host to run lab-in-a-box against itself:
                pinned checkout + install, /etc/lab_creation.cfg, root SSH to
                self, libvirt network + DNS for the lab nodes, and the workshop
                track (if the plan names one). Base images are downloaded by
                lab-in-a-box itself (ISO_URL, see rodeo/labinabox.py).
labinabox       renders lab.json from the plan and runs setup_lab.py --keep,
                which creates the VMs and runs every addon (e.g. install_smlm).

Both are idempotent: re-running converges instead of rebuilding.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from .. import labinabox_host as host
from .. import workshop
from ..config import ConfigError
from ..inventory import build_inventory
from ..labinabox import build_lab_json, effective_overlay, unresolved_placeholders

if TYPE_CHECKING:
    from .runner import DeployEvent, DeployRunner

# Commands the managed host path needs: always, and when it is also the hypervisor.
_AUTOMATION_TOOLS = ("bash", "git", "curl", "ssh", "ssh-keygen", "openssl", "rsync")
_HYPERVISOR_TOOLS = ("virsh", "qemu-img", "virt-install")
# Interpreters lab-in-a-box's Python 3.10+ code runs on, most preferred first.
_PYTHONS = ("python3.11", "python3.13", "python3.12")
# lab-in-a-box reads /root/.ssh/id_rsa.pub itself (cloud-init ROOT_SSH_KEY), so RSA it is.
_ROOT_KEY = Path("/root/.ssh/id_rsa")
# The key `rodeo ssh <vm>` uses as root (rodeo/ssh_targets.py:_HOST_ROOT_SSH_KEY).
_ROOT_ED25519_KEY = Path("/root/.ssh/id_ed25519")
_AUTHORIZED_KEYS = Path("/root/.ssh/authorized_keys")
ETC_HOSTS = Path("/etc/hosts")
# The rendered lab.json (resolved secrets, 0600), relative to the lab dir.
LAB_JSON_RELPATH = Path(".labinabox") / "lab.json"
# Cloud VMs' addresses, learned after setup_lab.py ({short name: ip}).
NODES_RELPATH = Path(".labinabox") / "nodes.json"


def lab_dir(runner: "DeployRunner") -> Path:
    cfg = runner.cfg
    return Path(cfg.get("config_dir") or cfg.get("plan_dir") or runner.root)


def lab_json_path(runner: "DeployRunner") -> Path:
    return lab_dir(runner) / LAB_JSON_RELPATH


def _run(runner: "DeployRunner", cmd: list[str], env: dict | None = None) -> Iterator["DeployEvent"]:
    """Stream one command; the caller checks runner._last_rc."""
    yield from runner._stream_subprocess(cmd, env=env)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Lines of setup_lab.py output kept to summarise a failed run.
_SETUP_TAIL = 400
_SUMMARY_MAX = 40


def failure_summary(lines: list[str]) -> list[str]:
    """What to repeat after a failed setup_lab.py run: its LAB SUMMARY block
    (without colours and rules), else its last ERROR/FAILED lines."""
    plain = [_ANSI.sub("", line).rstrip() for line in lines]
    starts = [i for i, line in enumerate(plain) if line.strip() == "LAB SUMMARY"]
    if starts:
        picked = [line for line in plain[starts[-1] + 1:] if line.strip() and set(line.strip()) != {"═"}]
    else:
        picked = [line.strip() for line in plain if "ERROR" in line or "FAILED" in line]
    return picked[-_SUMMARY_MAX:]


def _run_setup(runner: "DeployRunner", cmd: list[str]) -> Iterator["DeployEvent"]:
    """Stream setup_lab.py; when it fails, repeat its summary after the output."""
    from .runner import LogLine

    tail: deque[str] = deque(maxlen=_SETUP_TAIL)
    for event in _run(runner, cmd):
        if isinstance(event, LogLine):
            tail.append(event.line)
        yield event
    if runner._last_rc != 0:
        summary = failure_summary(list(tail))
        yield LogLine(f"  ✗  setup_lab.py failed (exit {runner._last_rc})" + (":" if summary else ""))
        for line in summary:
            yield LogLine(f"     {line}")


def stream_labinabox_host(runner: "DeployRunner") -> Iterator["DeployEvent"]:
    from .runner import LogLine

    cfg = runner.cfg
    try:
        lab, warnings = build_lab_json(cfg)
        host.base_images(cfg)  # validates: clear error for unresolved/invalid sources
        spec = workshop.workshop_spec(cfg)
        tgt = host.target(cfg)
        mode = host.effective_mode(cfg)
    except ConfigError as exc:
        yield LogLine(f"  ✗  {exc}")
        runner._last_rc = 1
        return
    for warning in warnings:
        yield LogLine(f"  ⚠  {warning}")
    from ..labinabox import variant_extras

    notice = variant_extras(cfg)["notice"]
    if notice:
        yield LogLine(f"  ⚠  {notice}")

    if mode == "managed":
        # One host, several lab instances: their shared setup steps (install,
        # lab_creation.cfg, /etc/hosts, firewall) must not interleave.
        with _host_lock():
            yield from _stream_managed_host(runner, lab)
    else:
        yield from _stream_existing_host(runner, tgt, lab)
    if runner._last_rc != 0:
        return
    yield from _check_fields(runner, tgt if tgt["host"] else None, lab)
    if runner._last_rc != 0:
        return

    # 5. Base images: lab-in-a-box's setup_lab.py downloads and verifies them
    #    (ISO_URL in lab.json). Here only: warn about stale pre-built images.
    for name, age, max_age in host.image_ages(cfg):
        if age is not None and age > max_age:
            yield LogLine(f"  ⚠  {name} is {int(age)} days old (max_age_days: {max_age}) "
                          "— time to rebuild it (see bake/README.md)")

    # 6. Workshop track: optional content, never a build input — problems only warn.
    if spec is not None:
        yield from _stream_workshop(runner, spec, lab)

    runner._last_rc = 0


def _stream_workshop(runner: "DeployRunner", spec: dict, lab: dict) -> Iterator["DeployEvent"]:
    """Fetch the track into <lab>/workshop, record the commit, render the guide."""
    from .runner import LogLine

    dest = workshop.checkout_path(lab_dir(runner))
    yield LogLine(f"Fetching workshop track '{spec['track']}' ({spec['ref'] or spec['branch']})...")
    for cmd in workshop.track_fetch_commands(spec, dest):
        yield from _run(runner, cmd)
        if runner._last_rc != 0:
            yield LogLine("  ⚠  workshop track fetch failed — the lab itself is unaffected")
            return
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = head.stdout.strip()
    lock = lab_dir(runner) / ".labinabox" / "workshop.lock"
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock.write_text(workshop.lock_text(spec, commit))
    track_dir = dest / "tracks" / spec["track"]
    try:
        track = workshop.parse_track(track_dir)
        guide = workshop.render_guide(track_dir, spec, lab_dir(runner) / "workshop-guide")
    except (ConfigError, OSError) as exc:
        yield LogLine(f"  ⚠  workshop track: {exc}")
        return
    yield LogLine(f"  ✓  {track['title']} @ {commit[:12]}: {len(guide)} challenge(s) rendered to workshop-guide/")
    if spec["pdf"]:
        yield from _stream_guide_pdf(runner, track_dir)
    for warning in workshop.topology_warnings(track, spec, list(lab.get("nodes", {}))):
        yield LogLine(f"  ⚠  {warning}")


@contextlib.contextmanager
def _host_lock() -> Iterator[None]:
    from ..paths import rodeo_dir

    path = rodeo_dir() / "labinabox-host.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _stream_guide_pdf(runner: "DeployRunner", track_dir: Path) -> Iterator["DeployEvent"]:
    """workshop-guide.pdf via the track's own build tool — best effort, warnings only."""
    import sys

    from .runner import LogLine

    base = lab_dir(runner)
    tool = workshop.prepare_pdf_build(track_dir, base / "workshop-guide", base / ".labinabox" / "pdf-build")
    if tool is None:
        yield LogLine("  ⚠  workshop.pdf: the track has no tools/build_pdf.py")
        return
    out = base / "workshop-guide.pdf"
    r = subprocess.run([sys.executable, str(tool), str(out)], capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
        yield LogLine(f"  ⚠  workshop.pdf: build_pdf.py failed ({tail[0][:160]}) — needs pandoc, "
                      "Chromium and python pyyaml/pillow/pypdf")
        return
    out.chmod(0o600)  # carries the lab's own values, passwords included
    yield LogLine(f"  ✓  workshop guide PDF: {out}")


def _stream_managed_host(runner: "DeployRunner", lab: dict) -> Iterator["DeployEvent"]:
    """Install lab-in-a-box here and make this host its own hypervisor + automation node."""
    from .runner import LogLine

    cfg = runner.cfg
    cloud_spec = host.cloud(cfg)
    tools = _AUTOMATION_TOOLS + ((host.CLOUD_CLIS[cloud_spec["cloudtype"]],)
                                 if cloud_spec and cloud_spec["cloudtype"] in host.CLOUD_CLIS else ())
    if not cloud_spec:
        tools += _HYPERVISOR_TOOLS
    elif cloud_spec["cloudtype"] == "aws":
        from ..awscli import ensure_on_path

        ensure_on_path()
    missing = [tool for tool in tools if not shutil.which(tool)]
    if missing:
        yield LogLine(
            f"  ✗  this host lacks {', '.join(missing)} — needed to run lab-in-a-box here "
            "(install them, or use an existing lab-in-a-box host: lab_in_a_box.target)"
        )
        runner._last_rc = 1
        return
    # 1. lab-in-a-box checkout (source ref) and install.
    checkout = host.local_override()
    if checkout is not None:
        yield LogLine(f"Using local lab-in-a-box checkout {checkout} ({host.LIAB_PATH_ENV})")
    else:
        repo, ref = host.source(cfg)
        if ref == host.LIAB_LATEST:
            try:
                ref = host.resolve_ref(repo, ref)
            except ConfigError as exc:
                yield LogLine(f"  ✗  {exc}")
                runner._last_rc = 1
                return
            yield LogLine(f"lab-in-a-box latest release: {ref}")
        checkout = host.checkout_dir(ref)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        yield LogLine(f"Fetching lab-in-a-box {ref} from {repo}...")
        for cmd in host.fetch_commands(repo, ref, checkout):
            yield from _run(runner, cmd)
            if runner._last_rc != 0:
                yield LogLine(f"  ✗  {' '.join(cmd[:4])} … exited {runner._last_rc}")
                return
    if not (checkout / "install_automation_node_scripts.sh").is_file():
        yield LogLine(f"  ✗  {checkout} is not a lab-in-a-box checkout")
        runner._last_rc = 1
        return

    python_bin = next((p for p in _PYTHONS if shutil.which(p)), None)
    if python_bin is None:
        yield LogLine(f"  ✗  lab-in-a-box needs one of {', '.join(_PYTHONS)} on PATH")
        runner._last_rc = 1
        return
    yield LogLine(f"Installing lab-in-a-box scripts (interpreter: {python_bin})...")
    yield from _run(runner, host.install_command(checkout, python_bin))
    if runner._last_rc != 0:
        yield LogLine("  ✗  lab-in-a-box install failed")
        return

    # 2. Root SSH to this host: lab-in-a-box reaches REMOTE_HOST (= this host)
    #    and every VM over `ssh root@…` with root's default key.
    if not _ROOT_KEY.exists():
        _ROOT_KEY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "rsa", "-b", "4096", "-N", "", "-f", str(_ROOT_KEY)],
            check=True,
        )
    pubkey = _ROOT_KEY.with_suffix(".pub").read_text().strip()
    authorized = _AUTHORIZED_KEYS.read_text() if _AUTHORIZED_KEYS.exists() else ""
    if pubkey not in authorized:
        with _AUTHORIZED_KEYS.open("a") as fh:
            fh.write(("" if authorized.endswith("\n") or not authorized else "\n") + pubkey + "\n")
        _AUTHORIZED_KEYS.chmod(0o600)

    # 3. /etc/lab_creation.cfg — never clobber one rodeo didn't write.
    inv = build_inventory(cfg)
    net = inv.get("libvirt_network", {})
    gateway = net.get("gateway") or cfg.get("network", {}).get("gateway", "192.168.122.1")
    if host.LAB_CREATION_CFG.exists() and not host.cfg_is_rodeo_managed(host.LAB_CREATION_CFG.read_text()):
        yield LogLine(
            f"  ✗  {host.LAB_CREATION_CFG} exists and was not written by rodeo — this host "
            "already runs lab-in-a-box on its own. Use it as is (lab_in_a_box.target.mode: "
            "existing, or auto), or move that file away to let rodeo manage it."
        )
        runner._last_rc = 1
        return
    fd = os.open(host.LAB_CREATION_CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(host.render_lab_creation_cfg(gateway, pubkey))
    host.NAMED_ZONE_DIR.mkdir(parents=True, exist_ok=True)

    if cloud_spec:
        # Cloud VMs: no local network, DNS, firewall or hosts block to set up —
        # only the account lab-in-a-box creates them in.
        yield from _write_cloud_credentials(runner, cloud_spec)
        return

    # 4. libvirt network + DNS for every lab node (served by libvirt's dnsmasq).
    uri = cfg.get("libvirt", {}).get("uri", "qemu:///system")
    net_name = net.get("name", "default")
    aliases = effective_overlay(cfg).get("dns_aliases") or {}
    exists = subprocess.run(
        ["virsh", "-c", uri, "net-info", net_name], capture_output=True, text=True,
    ).returncode == 0
    if not exists:
        yield LogLine(f"Defining libvirt network '{net_name}'...")
        xml = lab_dir(runner) / ".labinabox" / "network.xml"
        xml.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        xml.write_text(host.render_network_xml(net, lab, aliases))
        yield from _run(runner, ["virsh", "-c", uri, "net-define", str(xml)])
        if runner._last_rc != 0:
            return
    for cmd in (["net-start", net_name], ["net-autostart", net_name]):
        subprocess.run(["virsh", "-c", uri, *cmd], capture_output=True, text=True)
    if exists:
        for ip, names in host.dns_host_entries(lab, aliases):
            r = subprocess.run(host.net_update_command(net_name, ip, names, uri), capture_output=True, text=True)
            if r.returncode != 0 and "exist" not in (r.stderr + r.stdout).lower():
                yield LogLine(f"  ⚠  DNS entry {names[0]} → {ip}: {r.stderr.strip()}")
    yield LogLine(f"  ✓  network '{net_name}' with DNS for {len(lab.get('nodes', {}))} node(s)")
    hosts = ETC_HOSTS
    plan = cfg.get("name", "rodeo")
    hosts.write_text(host.replace_hosts_block(hosts.read_text() if hosts.exists() else "", plan,
                                              host.hosts_block(plan, lab)))
    try:
        forwards = host.forward_port_commands(
            (inv.get("_raw_topology") or {}).get("exposed_services") or [], lab)
    except (ConfigError, KeyError, ValueError) as exc:
        yield LogLine(f"  ✗  {exc}")
        runner._last_rc = 1
        return
    # kvm_host leaves firewalld masked; start it first (same step the rancher
    # profile's boot phase takes), then add the exposed_services forwards.
    yield from runner._start_firewalld()
    for cmd in forwards:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(f"  ✗  {' '.join(cmd[3:])}: {r.stderr.strip()}")
            runner._last_rc = 1
            return
    if forwards:
        subprocess.run(["firewall-cmd", "--reload"], capture_output=True, text=True)
        yield LogLine(f"  ✓  {len(forwards) // 2} exposed service(s) forwarded from this host")

    runner._last_rc = 0


def _write_cloud_credentials(runner: "DeployRunner", spec: dict) -> Iterator["DeployEvent"]:
    """lab-in-a-box credential file for spec's account (0600) — or reuse an existing one."""
    from .runner import LogLine

    path = host.credentials_path(spec["account"])
    if not spec["settings"]:
        if not path.exists() and not any(host.CREDENTIALS_DIR.glob(f"{spec['account']}.*")):
            yield LogLine(f"  ✗  no cloud account '{spec['account']}' in {host.CREDENTIALS_DIR} — "
                          "add lab_in_a_box.cloud.settings, or create it with setup_credentials.py")
            runner._last_rc = 1
            return
        yield LogLine(f"  ✓  using the existing cloud account '{spec['account']}'")
        runner._last_rc = 0
        return
    if path.exists() and not host.credentials_are_rodeo_managed(path):
        yield LogLine(f"  ✗  {path} exists and wasn't written by rodeo — pick another "
                      "lab_in_a_box.cloud.account, or drop `settings` to use that one")
        runner._last_rc = 1
        return
    host.CREDENTIALS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(host.render_credentials(spec))
    yield LogLine(f"  ✓  cloud account '{spec['account']}' ({spec['cloudtype']}) — VMs are created there")
    runner._last_rc = 0


def _stream_existing_host(runner: "DeployRunner", tgt: dict, lab: dict) -> Iterator["DeployEvent"]:
    """Use a lab-in-a-box someone already set up: touch nothing of its own."""
    from .runner import LogLine

    where = tgt["host"] or "this host"
    yield LogLine(f"Using the existing lab-in-a-box on {where} — its install, network, "
                  "firewall and keys are left as they are")
    bridges = {n.get("BRIDGE") for n in lab.get("nodes", {}).values()}
    if "virbr0" in bridges:
        yield LogLine("  ⚠  nodes attach to virbr0 (rodeo's NAT default) — an existing lab-in-a-box "
                      "setup usually has its own bridge: set network.bridge/cidr/gateway in "
                      "definition.yaml and mydns via lab_in_a_box.sections.common")
    if tgt["host"]:
        from ..ssh_key import ensure_rodeo_ssh_key

        key = ensure_rodeo_ssh_key()
        r = subprocess.run(host.remote_ssh(tgt, str(key)) + ["test -x " + str(host.LIAB_BIN / "setup_lab.py")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(
                f"  ✗  cannot use lab-in-a-box on {tgt['ssh_user']}@{tgt['host']} — add rodeo's key "
                f"({key.with_suffix('.pub')}) to its authorized_keys and check that setup_lab.py is "
                f"installed ({r.stderr.strip()[:160]})"
            )
            runner._last_rc = 1
            return

    cloud_spec = host.cloud(runner.cfg)
    if cloud_spec:
        # Cloud VMs: capacity is the cloud account's business; credentials are that host's.
        if cloud_spec["settings"]:
            yield LogLine(f"  ⚠  lab_in_a_box.cloud.settings ignored: the existing lab-in-a-box uses its own "
                          f"'{cloud_spec['account']}' account file")
        runner._last_rc = 0
        return

    # Capacity: warn only — setup_lab.py --keep may reuse VMs that already exist.
    if tgt["host"]:
        from ..ssh_key import ensure_rodeo_ssh_key

        argv = host.remote_ssh(tgt, str(ensure_rodeo_ssh_key())) + [host.CAPACITY_COMMAND]
    else:
        argv = ["bash", "-c", host.CAPACITY_COMMAND]
    free = host.parse_capacity(subprocess.run(argv, capture_output=True, text=True).stdout or "")
    warnings = host.capacity_warnings(lab, free)
    for warning in warnings:
        yield LogLine(f"  ⚠  {warning} (fine if some of these VMs already exist)")
    if not warnings:
        yield LogLine("  ✓  hypervisor memory free: " + ", ".join(
            f"{h} {m // 1024} GiB" for h, m in free.items() if m is not None)
            + f"; lab needs {host.lab_memory_mib(lab) // 1024} GiB")
    runner._last_rc = 0


def _check_fields(runner: "DeployRunner", remote: dict | None, lab: dict) -> Iterator["DeployEvent"]:
    """Refuse a lab.json the installed lab-in-a-box doesn't understand, before building anything."""
    from ..labinabox import unknown_fields
    from .runner import LogLine

    outputs = {}
    for name, cmd in host.schema_commands(host.addons_used(lab)).items():
        if remote:
            from ..ssh_key import ensure_rodeo_ssh_key

            argv = host.remote_ssh(remote, str(ensure_rodeo_ssh_key())) + [cmd]
        else:
            argv = shlex.split(cmd)
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            what = "base lab schema" if name == "_base" else f"addon '{name}'"
            yield LogLine(f"  ✗  the installed lab-in-a-box has no {what} ({r.stderr.strip()[:160]})")
            runner._last_rc = 1
            return
        outputs[name] = r.stdout
    try:
        unknown = unknown_fields(lab, host.schema_from_outputs(outputs))
    except (ValueError, KeyError) as exc:
        yield LogLine(f"  ✗  could not read the installed lab-in-a-box schema: {exc}")
        runner._last_rc = 1
        return
    if unknown:
        yield LogLine(
            "  ✗  the installed lab-in-a-box doesn't know these lab.json fields: " + ", ".join(unknown)
            + " — it needs a newer lab-in-a-box (for a managed host: bump lab_in_a_box.source.ref)"
        )
        runner._last_rc = 1
        return
    yield LogLine("  ✓  lab.json fields match the installed lab-in-a-box")
    runner._last_rc = 0


def stream_labinabox(runner: "DeployRunner") -> Iterator["DeployEvent"]:
    from .runner import LogLine

    cfg = runner.cfg
    try:
        lab, _ = build_lab_json(cfg)
    except ConfigError as exc:
        yield LogLine(f"  ✗  {exc}")
        runner._last_rc = 1
        return
    unresolved = unresolved_placeholders(lab)
    if unresolved:
        yield LogLine(
            "  ✗  unresolved secrets in the lab-in-a-box definition: " + ", ".join(unresolved)
            + " — add them to ~/.rodeo/secrets.yaml"
        )
        runner._last_rc = 1
        return

    root_password = (lab.get("common") or {}).get("VM_ROOT_PASS")
    if root_password:
        # cloud-init/Ignition nodes take a crypt hash; virt_customize takes VM_ROOT_PASS.
        r = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=root_password,
                           capture_output=True, text=True)
        if r.returncode != 0:
            yield LogLine(f"  ✗  could not hash lab_in_a_box.root_password: {r.stderr.strip()}")
            runner._last_rc = 1
            return
        lab["common"]["ROOT_PWD_HASH"] = r.stdout.strip()

    # lab.json carries resolved secrets: owner-only, inside the lab dir, never in git.
    path = lab_json_path(runner)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(lab, fh, indent=2)
        fh.write("\n")

    parallel = effective_overlay(cfg).get("parallel")
    tgt = host.target(cfg)
    if tgt["host"]:
        yield from _stream_remote_setup(runner, tgt, path, parallel)
        return
    yield LogLine(f"Running lab-in-a-box setup_lab.py for {len(lab.get('nodes', {}))} node(s)...")
    yield from _run_setup(runner, shlex.split(host.setup_command(str(path), parallel)))
    if runner._last_rc != 0:
        return

    if host.cloud(cfg):
        # Cloud VMs got their addresses at creation; lab-in-a-box wrote them into
        # LAB_HOSTS_FILE. Keep them for rodeo ssh / the success screen.
        ips = host.hosts_file_ips(ETC_HOSTS.read_text() if ETC_HOSTS.exists() else "", list(lab["nodes"]))
        (lab_dir(runner) / NODES_RELPATH).write_text(json.dumps(ips, indent=1) + "\n")
        missing = sorted({f.split(".")[0] for f in lab["nodes"]} - set(ips))
        yield LogLine(f"  ✓  cloud VM addresses: {', '.join(f'{k} {v}' for k, v in sorted(ips.items())) or 'none'}")
        if missing:
            yield LogLine(f"  ⚠  no address known for: {', '.join(missing)}")

    # lab-in-a-box trusts root's id_rsa on every VM. `rodeo ssh <vm>` logs in with
    # the host's /root/.ssh/id_ed25519 (as root) or rodeo's managed key
    # (otherwise) — add both. Best effort: a node that refuses is reported.
    from ..ssh_key import ensure_rodeo_ssh_key, rodeo_ssh_public_key_path

    if not _ROOT_ED25519_KEY.exists() and host.effective_mode(cfg) == "managed":
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(_ROOT_ED25519_KEY)],
                       check=False)
    ensure_rodeo_ssh_key()
    pubkeys = [p.read_text().strip() for p in (rodeo_ssh_public_key_path(),
                                               _ROOT_ED25519_KEY.with_suffix(".pub")) if p.exists()]
    add_key = "mkdir -p ~/.ssh && chmod 700 ~/.ssh" + "".join(
        f" && (grep -qxF {shlex.quote(k)} ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo {shlex.quote(k)} >> ~/.ssh/authorized_keys)" for k in pubkeys)
    for fqdn, node in lab.get("nodes", {}).items():
        r = subprocess.run(
            ["ssh", "-i", str(_ROOT_KEY), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=10", f"root@{node.get('myip') or fqdn}", add_key],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            yield LogLine(f"  ⚠  {fqdn}: could not add rodeo's SSH keys ({r.stderr.strip()[:120]})")
    runner._last_rc = 0


def _stream_remote_setup(runner: "DeployRunner", tgt: dict, path: Path, parallel) -> Iterator["DeployEvent"]:
    """Copy lab.json to the remote lab-in-a-box host (over stdin) and run setup_lab.py there.

    The VMs live on that host's network: rodeo doesn't push its own SSH keys to
    them — `rodeo ssh <vm>` jumps through the host instead.
    """
    from ..ssh_key import ensure_rodeo_ssh_key
    from .runner import LogLine

    plan = runner.cfg.get("name", "rodeo")
    ssh = host.remote_ssh(tgt, str(ensure_rodeo_ssh_key()))
    try:
        upload = host.remote_upload_command(plan)
    except ConfigError as exc:
        yield LogLine(f"  ✗  {exc}")
        runner._last_rc = 1
        return
    r = subprocess.run(ssh + [upload], input=path.read_text(), capture_output=True, text=True)
    if r.returncode != 0:
        yield LogLine(f"  ✗  could not copy lab.json to {tgt['host']}: {r.stderr.strip()[:160]}")
        runner._last_rc = 1
        return
    remote_json = f"{host.remote_lab_dir(plan)}/lab.json"
    yield LogLine(f"Running lab-in-a-box setup_lab.py on {tgt['host']} ({remote_json})...")
    yield from _run_setup(runner, ssh + [host.setup_command(remote_json, parallel)])
