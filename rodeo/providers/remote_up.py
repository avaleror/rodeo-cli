"""Single-host AWS acquire + remote ``rodeo up`` (deployment_target: aws)."""
from __future__ import annotations

import base64
import io
import re
import shlex
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import ConfigError
from ..fleet.inventory import FleetHost, FleetInventory
from ..fleet.ssh_exec import run_remote
from ..install_source import (
    DEFAULT_INSTALL_URL,
    bootstrap_fragment,
    default_install_url,
    resolve_install_source,
)
from ..paths import rodeo_secrets_path, rodeo_state_dir
from ..ssh_key import resolve_ssh_identity
from . import deadman
from .base import SINGLE_HOST_ID, ProvisionSpec, ProvisionedHost
from .registry import get_provider

# install_url / ref resolution is shared with fleet deploy — see install_source.
_DEFAULT_INSTALL = DEFAULT_INSTALL_URL
_DEFAULT_LAB_DIR = "/root/rodeo-lab"
# What the host needs to treat the lab as existing (up_cmd's lab_ready) rather
# than seed a fresh one from the bundled example.
_PLAN_FILES = ("rodeo-plan.yaml", "definition.yaml")
# The laptop's values (global ~/.rodeo/secrets.yaml + the lab's own
# .rodeo-secrets.yaml) for the secrets the plan references travel in the
# same tar under this name and become the
# host's ~/.rodeo/secrets.yaml, so the host deploys with the passwords the
# operator already has locally instead of generating its own.
_SHIPPED_SECRETS = ".rodeo-shipped-secrets.yaml"


def on_ec2(*, timeout: float = 0.4) -> bool:
    """True when running on an EC2 instance (IMDSv2, IMDSv1, or DMI fallback)."""
    if _on_ec2_imds(timeout=timeout):
        return True
    return _on_ec2_dmi()


def _on_ec2_imds(*, timeout: float) -> bool:
    """Probe instance metadata; prefer IMDSv2 token, fall back to IMDSv1."""
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        with urllib.request.urlopen(token_req, timeout=timeout) as token_resp:
            token = token_resp.read().decode("utf-8", errors="replace").strip()
        if token:
            meta_req = urllib.request.Request(
                "http://169.254.169.254/latest/meta-data/",
                headers={"X-aws-ec2-metadata-token": token},
            )
            with urllib.request.urlopen(meta_req, timeout=timeout) as resp:
                return int(getattr(resp, "status", 200) or 200) < 400
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass
    try:
        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200) < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _on_ec2_dmi() -> bool:
    """Best-effort EC2 detection via DMI / sysfs when IMDS is unreachable."""
    for path in (
        Path("/sys/devices/virtual/dmi/id/product_uuid"),
        Path("/sys/hypervisor/uuid"),
    ):
        try:
            val = path.read_text().strip().lower()
        except OSError:
            continue
        if val.startswith("ec2"):
            return True
    for path in (
        Path("/sys/devices/virtual/dmi/id/bios_vendor"),
        Path("/sys/devices/virtual/dmi/id/sys_vendor"),
    ):
        try:
            val = path.read_text().strip().lower()
        except OSError:
            continue
        if "amazon" in val:
            return True
    return False


def on_gce(*, timeout: float = 0.4) -> bool:
    """True when running on a Compute Engine VM (DMI first, metadata server fallback)."""
    try:
        if "Google Compute Engine" in Path("/sys/class/dmi/id/product_name").read_text():
            return True
    except OSError:
        pass
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/id",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.headers.get("Metadata-Flavor") == "Google"
    except (urllib.error.URLError, OSError, ValueError):
        return False


# deployment_target values that acquire a cloud VM and run the lab on it.
CLOUD_TARGETS = ("aws", "gcp")


def on_cloud_host(target: str) -> bool:
    """True when this process runs on the VM that *target* provisions (no recursion)."""
    target = (target or "").strip().lower()
    if target == "aws":
        return on_ec2()
    if target == "gcp":
        return on_gce()
    return False


def _cloud_target(cfg: dict[str, Any]) -> str:
    """The plan's cloud target (aws or gcp): the --target the host runs and its log's name."""
    target = str(cfg.get("deployment_target") or "").strip().lower()
    return target if target in CLOUD_TARGETS else "aws"


def default_ssh_user(provider_type: str) -> str:
    """The login the provider's default image gives rodeo."""
    return "rodeo" if (provider_type or "").lower() == "gcp" else "ec2-user"


def aws_host_state_path(plan_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in plan_name)
    return rodeo_state_dir() / f"{safe}-aws-host.yaml"


def save_aws_host_state(plan_name: str, host: ProvisionedHost) -> Path:
    path = aws_host_state_path(plan_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "plan": plan_name,
                "host_id": host.id,
                "ssh": host.ssh,
                "public_ip": host.public_ip,
                "provider_id": host.provider_id,
                "labels": dict(host.labels),
            },
            default_flow_style=False,
            sort_keys=False,
        )
    )
    return path


def load_aws_host_state(plan_name: str) -> dict[str, Any] | None:
    path = aws_host_state_path(plan_name)
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else None


def clear_aws_host_state(plan_name: str) -> None:
    path = aws_host_state_path(plan_name)
    if path.is_file():
        path.unlink()


def _provider_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    provider = cfg.get("provider")
    if not isinstance(provider, dict):
        # deployment_target: remote has no provider: (nothing is provisioned);
        # the paths it shares with aws only read optional keys from it.
        if str(cfg.get("deployment_target") or "").strip().lower() == "remote":
            provider = {}
        else:
            raise ConfigError("deployment_target: aws requires provider:")
    out = dict(provider)
    target = str(cfg.get("deployment_target") or "").strip().lower()
    default_type = target if target in CLOUD_TARGETS else "aws"
    out["type"] = str(out.get("type") or default_type).strip().lower()
    # Only the fleet inventory loader may lift the dead-man switch.
    out.pop(deadman.ALLOW_OFF_KEY, None)
    return out


def provision_primary(
    cfg: dict[str, Any],
    *,
    get_provider_fn: Callable[[str], Any] | None = None,
) -> ProvisionedHost:
    """Create or reuse the single EC2 host for this plan."""
    provider_cfg = _provider_cfg(cfg)
    plan_name = str(cfg.get("name") or "rodeo")
    identity = resolve_ssh_identity(workshop=plan_name)
    provider_cfg = dict(provider_cfg)
    provider_cfg["identity_file"] = identity
    ssh_user = str(provider_cfg.get("ssh_user") or default_ssh_user(provider_cfg["type"]))
    spec = ProvisionSpec(
        workshop=plan_name,
        host_ids=[SINGLE_HOST_ID],
        ssh_user=ssh_user,
        identity_file=identity,
        wait_ssh=True,
        extra_labels={"scope": "single-host"},
    )
    getter = get_provider_fn or get_provider
    provider = getter(str(provider_cfg["type"]))
    hosts = provider.provision(spec, provider_cfg)
    if not hosts:
        raise ConfigError(f"{provider_cfg['type']} provision returned no hosts")
    host = hosts[0]
    save_aws_host_state(plan_name, host)
    return host


def _fleet_inventory_for(
    cfg: dict[str, Any],
    host: ProvisionedHost,
    *,
    ssh_user: str | None = None,
    identity_file: str | None = None,
    lab_dir: str | None = None,
) -> tuple[FleetInventory, FleetHost]:
    """The one-host fleet inventory a remote ``rodeo up`` runs over.

    *ssh_user*, *identity_file* and *lab_dir* override the ``provider:`` block for a host
    that is not provisioned by a provider (``deployment_target: remote``)."""
    provider_cfg = _provider_cfg(cfg)
    plan_name = str(cfg.get("name") or "rodeo")
    identity = identity_file or resolve_ssh_identity(workshop=plan_name)
    ssh_user = str(ssh_user or provider_cfg.get("ssh_user") or default_ssh_user(provider_cfg["type"]))
    inv = FleetInventory(
        name=plan_name,
        lab_dir=str(lab_dir or provider_cfg.get("lab_dir") or _DEFAULT_LAB_DIR),
        defaults={
            "ssh_user": ssh_user,
            "identity_file": identity,
        },
        hosts=[],
        install_url=str(provider_cfg.get("install_url") or default_install_url()),
    )
    fh = FleetHost(
        id=host.id,
        ssh=host.ssh,
        public_ip=host.public_ip,
        labels=dict(host.labels),
    )
    return inv, fh


def _log_base(name: str) -> str:
    """The file-name stem for a run's log, pid and exit files: ``<stem>-up.log`` and friends.

    Only the stem is interpolated into the remote shell script, so anything unusual in a
    target name is flattened to a safe token.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "remote"


def remote_up_script(
    *,
    lab_dir: str,
    profile: str | None,
    install_url: str = _DEFAULT_INSTALL,
    ref: str | None = None,
    ship_plan: bool = False,
    detach: bool = False,
    target: str = "aws",
    log_name: str | None = None,
) -> str:
    """Bootstrap + remote ``rodeo up --target aws`` (phases still run as baremetal).

    ``--target aws``, not ``--target baremetal``. The remote detects it is on
    EC2 via IMDS, so up_cmd takes the local-deploy path (no recursion) while
    keeping ``deployment_target: aws`` in the plan — which is what makes
    apply_host_context raise disk_gb to the aws floor and set
    ``storage.backend: nvme`` so the kvm_host phase mounts the instance store.
    Phase behaviour is still baremetal, via up_cmd's local_target.

    Passing baremetal here discarded all of that: the remote seeded a plain
    baremetal lab with disk_gb 250 on the 10 GB root EBS, and preflight failed
    with "need ~520 GB, have 7 GB free" while a 3.4 TB NVMe sat unmounted.

    *ref* pins the rodeo-cli the host runs. It also forces the bootstrap to
    run even when ``rodeo`` is already installed, which is the only way local
    commits reach a host that was provisioned before them.

    *target* is the ``--target`` the remote runs (and names its log unless *log_name* says
    otherwise). ``deployment_target: remote`` sends ``baremetal``: the host is a plain KVM host,
    and sending ``remote`` would have it ship the lab on to itself.

    *ship_plan*: stdin carries the local lab's plan files (base64 tar, see
    pack_plan_files), unpacked into the lab dir first, before the bootstrap
    can read stdin. The host then finds an existing lab and deploys the
    operator's plan, not the bundled example (where edition, versions and
    resources edited locally were silently lost).

    If the tar carries the laptop's secrets they replace the host's
    ~/.rodeo/secrets.yaml (resolved like paths.invoking_home: SUDO_USER's home
    when set). Live-caught 2026-11-11: the host generated its own password, so
    the one in the operator's ~/.rodeo/secrets.yaml did not log in to Rancher.

    *detach*: start ``rodeo up`` and return instead of following its log. The
    detached job records itself (see _detached_job) and the script prints one
    ``AWS_UP_DETACHED`` line with its pid. A deploy that is already running
    prints an ``AWS_UP_RUNNING`` line before exit 75.
    """
    lab = shlex.quote(lab_dir)
    remote_target = shlex.quote(target)
    base = _log_base(log_name or target)
    unpack = ""
    if ship_plan:
        shipped = shlex.quote(f"{lab_dir}/{_SHIPPED_SECRETS}")
        unpack = (
            f"mkdir -p {lab}; (umask 077; base64 -d | tar -C {lab} --no-same-owner -xf -); "
            f"if [ -f {shipped} ]; then "
            'rh="$HOME"; if [ -n "${SUDO_USER:-}" ]; then '
            'rh="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; rh="${rh:-$HOME}"; fi; '
            'mkdir -p "$rh/.rodeo"; '
            f'install -m 600 {shipped} "$rh/.rodeo/secrets.yaml"; rm -f {shipped}; fi; '
        )
    profile_bits = ""
    if profile:
        profile_bits = f"--profile {shlex.quote(profile)} "
    up = f"rodeo up --yes --no-tmux {profile_bits}--dir {lab} --target {remote_target}"
    running = (
        "printf 'AWS_UP_RUNNING pid=%s log=%s\\n' \"$rpid\" \"$log\"; " if detach else ""
    )
    return (
        "set -euo pipefail; "
        f"{unpack}"
        f"{bootstrap_fragment(install_url=install_url, ref=ref)}; "
        f"mkdir -p {lab}; "
        # The log directory does not exist on a fresh host: rodeo creates
        # ~/.rodeo/logs on first run, but that run *is* the one being logged,
        # so it must exist before rodeo starts. $HOME is /root here
        # (sudo -n bash -lc).
        'mkdir -p "$HOME/.rodeo/logs"; '
        f'log="$HOME/.rodeo/logs/{base}-up.log"; '
        f'pidf="$HOME/.rodeo/logs/{base}-up.pid"; '
        f'exitf="$HOME/.rodeo/logs/{base}-up.exit"; '
        # The job creates the log by redirecting into it, but _followed_job's
        # tail can open it first and exit ("no files remaining"), losing the
        # deploy's output for the whole session. Creating it here is a no-op
        # for an existing log, so the offset below still skips earlier runs.
        ': >>"$log"; '
        # rodeo outlives the SSH session (laptop asleep, Ctrl-C, run_remote
        # timeout), so a re-run must not start a second deploy racing the first
        # one on the same lab. A pid file, not flock: a lock fd would be
        # inherited by whatever the deploy leaves running, like the tee pipe.
        # Only the first field is the pid: a detached run adds its start time.
        'rpid=""; [ -s "$pidf" ] && read -r rpid _ <"$pidf" || true; '
        'if [ -n "$rpid" ] && kill -0 "$rpid" 2>/dev/null '
        '&& grep -qa rodeo "/proc/$rpid/cmdline" 2>/dev/null; then '
        'echo "rodeo up is already running on this host (pid $rpid),'
        ' started by an earlier session. Follow it with:'
        ' sudo tail -f $log" >&2; '
        f"{running}"
        'exit 75; fi; '
        # The laptop only ships a lab it trusts (load_config secrets="all"), so
        # the host trusts that same copy; an older rodeo without --yes ignores it.
        f"rodeo trust --yes --config-dir {lab} </dev/null >/dev/null 2>&1 || true; "
        # A previous detached run's exit code must not describe this run.
        'rm -f "$exitf"; '
        f"{_detached_job(up, base) if detach else _followed_job(up)}"
    )


def _followed_job(up: str) -> str:
    """Run *up* in the background and follow its log until it ends (human path)."""
    return (
        'off=$(( $(stat -c %s "$log" 2>/dev/null || echo 0) + 1 )); '
        "set +e; "
        # rodeo writes to the log, not to the SSH channel, and the laptop gets
        # this run's part of it through tail --pid, which ends when rodeo ends.
        # Piping into tee kept the session open for as long as any process the
        # deploy left behind still held that pipe: live-caught 2026-11-18, the
        # host finished at 19:15 and the laptop hung until the connection was
        # reset at 20:42, so a healthy lab came back as exit 1.
        f"{up} "
        '</dev/null >>"$log" 2>&1 & rp=$!; '
        'echo "$rp" >"$pidf"; '
        'tail -c "+$off" --pid="$rp" -f "$log"; '
        'wait "$rp"; ec=$?; rm -f "$pidf"; set -e; '
        'echo AWS_UP_EXIT:$ec; '
        "exit \"$ec\""
    )


def _detached_job(up: str, base: str = "aws") -> str:
    """Start *up* as a job that records its own pid and outcome, then return.

    The job, not this shell, writes ``<base>-up.pid`` (its pid and start time,
    field 22 of its /proc stat) before rodeo starts, so an instant failure
    can't race us, and writes rodeo's exit code to ``<base>-up.exit`` before it
    removes the pid file. It keeps today's redirections: nothing holds the SSH
    channel, so the session ends as soon as this script does.
    """
    job = (
        f'pidf="$HOME/.rodeo/logs/{base}-up.pid"; exitf="$HOME/.rodeo/logs/{base}-up.exit"; '
        # $$ is this job's shell. /proc/self would be sed's own process. Field
        # 22 counted after the ") " that closes the command name, which may
        # itself contain spaces: field 3 there is the first, so 22 is the 20th.
        'st=$(sed "s/^.*) //" /proc/$$/stat | cut -d " " -f 20); '
        'echo "$$ $st" >"$pidf"; '
        f'{up} </dev/null >>"$HOME/.rodeo/logs/{base}-up.log" 2>&1; '
        'ec=$?; echo "$ec" >"$exitf.tmp" && mv -f "$exitf.tmp" "$exitf"; '
        'rm -f "$pidf"; exit "$ec"'
    )
    return (
        f'sh -c {shlex.quote(job)} </dev/null >>"$log" 2>&1 & rp=$!; '
        # Wait (5 s at most) for the job's own record, or its exit file if it
        # already finished.
        'i=0; while [ "$i" -lt 50 ] && [ ! -s "$pidf" ] && [ ! -s "$exitf" ]; do '
        'sleep 0.1; i=$((i + 1)); done; '
        'st=""; [ -s "$pidf" ] && read -r _ st <"$pidf" || true; '
        "printf 'AWS_UP_DETACHED pid=%s start=%s log=%s\\n' \"$rp\" \"$st\" \"$log\"; "
        "exit 0"
    )


def local_secrets(lab: Path) -> dict[str, Any]:
    """The secrets a local deploy of *lab* would use: global, then the lab's own."""
    from ..config import LAB_SECRETS_FILE, checked_lab_secrets
    from ..labfs import read_lab_file

    merged: dict[str, Any] = {}
    texts = []
    if rodeo_secrets_path().is_file():
        texts.append((rodeo_secrets_path(), rodeo_secrets_path().read_text()))
    # The lab's file is checked here too (no symlink, no hard link), not only by
    # an earlier load_config, and read without following links.
    if checked_lab_secrets(lab) is not None:
        texts.append((lab / LAB_SECRETS_FILE, read_lab_file(lab, LAB_SECRETS_FILE) or ""))
    for path, text in texts:
        try:
            merged.update(yaml.safe_load(text) or {})
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: invalid YAML: {exc}")
    return merged


def referenced_secret_keys(lab: Path) -> set[str]:
    """The plain ``??key`` placeholders *lab*'s plan uses, as the host will load it.

    Same walker as secretgen.ensure_plan_secrets (plan_secret_keys, with the
    selected lab-in-a-box variant applied), run over the plan as load_config
    renders it, so Jinja in the plan does not hide a placeholder.
    """
    from ..config import load_config
    from ..labinabox import apply_variant
    from ..secretgen import plan_secret_keys

    plan = lab / "rodeo-plan.yaml"
    if not plan.is_file():
        return set()
    cfg = load_config(plan, config_dir=lab, secrets="off")
    return plan_secret_keys(apply_variant(cfg))


def shipped_secrets(lab: Path) -> dict[str, Any]:
    """The laptop's values for only the secrets *lab*'s plan references.

    The host never sees unrelated keys from ~/.rodeo/secrets.yaml (an SCC
    regcode, another lab's passwords, cloud keys).
    """
    keys = referenced_secret_keys(lab)
    return {k: v for k, v in local_secrets(lab).items() if k in keys}


def pack_plan_files(lab: Path, *, with_secrets: bool = False) -> str:
    """Base64 tar of the lab's plan files (root-owned, 0600) for the remote.

    *with_secrets* adds the laptop's values for the secrets the plan
    references (see shipped_secrets) so laptop and host share one set of
    passwords for them.
    """
    buf = io.BytesIO()

    def _add(name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), 0o600
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in _PLAN_FILES:
            path = lab / name
            if path.is_file():
                _add(name, path.read_bytes())
        if with_secrets:
            secrets = shipped_secrets(lab)
            if secrets:
                _add(_SHIPPED_SECRETS, yaml.safe_dump(secrets, sort_keys=True).encode())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def run_remote_up(
    cfg: dict[str, Any],
    host: ProvisionedHost,
    *,
    profile: str | None = None,
    ref: str | None = None,
    timeout: float = 7200.0,
    plan_dir: Path | None = None,
    target: str = "aws",
    log_name: str | None = None,
    ssh_user: str | None = None,
    identity_file: str | None = None,
    lab_dir: str | None = None,
    detach: bool = False,
) -> dict[str, Any] | None:
    """SSH to the provisioned host and run ``rodeo up`` as baremetal.

    With *plan_dir* the host deploys that lab's plan (see remote_up_script). *target*,
    *log_name*, *ssh_user*, *identity_file* and *lab_dir* serve a host that no provider
    created (``deployment_target: remote``); their defaults are the aws path unchanged.
    With *detach* it returns once the host has started the deploy:
    ``{"state": "started", "pid", "start", "log", "host"}``, or
    ``{"state": "already_running", ...}`` (raised as AlreadyRunning, exit 75).
    """
    inv, fh = _fleet_inventory_for(
        cfg, host, ssh_user=ssh_user, identity_file=identity_file, lab_dir=lab_dir,
    )
    provider_cfg = _provider_cfg(cfg)
    lab_dir = str(lab_dir or provider_cfg.get("lab_dir") or _DEFAULT_LAB_DIR)
    install_url, resolved_ref = resolve_install_source(provider_cfg, ref=ref)
    script = remote_up_script(
        lab_dir=lab_dir,
        profile=profile,
        install_url=install_url,
        ref=resolved_ref,
        ship_plan=plan_dir is not None,
        detach=detach,
        target=target,
        log_name=log_name,
    )
    extra: dict[str, Any] = {}
    if plan_dir is not None:
        extra["stdin"] = pack_plan_files(plan_dir, with_secrets=True)
    result = run_remote(
        inv,
        fh,
        ["sudo", "-n", "bash", "-lc", script],
        timeout=timeout,
        **extra,
    )
    if detach:
        if result.rc == 75 and "AWS_UP_RUNNING " in (result.stdout or ""):
            info = _marker_fields(result.stdout, "AWS_UP_RUNNING")
            raise AlreadyRunning({"state": "already_running", **info, "host": host.id})
        if result.ok and "AWS_UP_DETACHED " in (result.stdout or ""):
            info = _marker_fields(result.stdout, "AWS_UP_DETACHED")
            return {"state": "started", **info, "host": host.id}
    if not result.ok:
        # Prefer stdout: the remote streams rodeo's own log there, while stderr
        # often holds only ssh noise ("Permanently added … to known hosts"),
        # which would otherwise be reported as the failure.
        parts = [p.strip() for p in (result.stdout, result.stderr) if (p or "").strip()]
        msg = "\n".join(parts) if parts else f"exit {result.rc}"
        msg = msg[-1500:]
        if result.rc in (124, 255):
            # Timeout or a dropped connection: rodeo does not die with the
            # session, so the deploy may well still be running on the host.
            raise ConfigError(
                f"lost the session to {host.public_ip} (exit {result.rc}): {msg}\n"
                "If the deploy had started, it keeps running on the host. Follow it with "
                f"`sudo tail -f /root/.rodeo/logs/{_log_base(log_name or target)}-up.log` "
                "there; re-running "
                "rodeo up refuses to start a second deploy while it runs."
            )
        who = "the AMI's login user" if target == "aws" else f"{inv.defaults['ssh_user']} on the host"
        raise ConfigError(
            f"remote rodeo up failed on {host.public_ip} "
            f"(exit {result.rc}): {msg}\n"
            f"If the output is empty or mentions sudo, check that {who} "
            "has passwordless sudo; otherwise the message above is "
            "the real failure."
        )
    out = result.stdout or ""
    if "AWS_UP_EXIT:" in out:
        for line in reversed(out.splitlines()):
            if line.startswith("AWS_UP_EXIT:"):
                code = line.split(":", 1)[1].strip()
                if code != "0":
                    raise ConfigError(
                        f"remote rodeo up exited {code} on {host.public_ip} "
                        f"(see ~/.rodeo/logs/{_log_base(log_name or target)}-up.log on the host)"
                    )
                break
    if detach:
        raise ConfigError(
            f"remote rodeo up on {host.public_ip} did not report a pid: "
            f"{(out or result.stderr or '').strip()[-500:]}"
        )
    return None


class AlreadyRunning(ConfigError):
    """A detached ``rodeo up`` found a deploy already running on the host (exit 75)."""

    def __init__(self, info: dict[str, Any]):
        super().__init__(f"rodeo up is already running on the host (pid {info.get('pid')})")
        self.info = info


def _marker_fields(stdout: str, marker: str) -> dict[str, Any]:
    """The key=value fields of the last *marker* line; pid as an int when it is one."""
    line = next(ln for ln in reversed(stdout.splitlines()) if ln.startswith(marker + " "))
    fields: dict[str, Any] = {}
    for part in line[len(marker) + 1:].split(" "):
        key, _, value = part.partition("=")
        if key:
            fields[key] = value
    if str(fields.get("pid", "")).isdigit():
        fields["pid"] = int(fields["pid"])
    return fields


#: Most log lines ``remote_up_status`` returns.
MAX_STATUS_LINES = 500

_STATUS_TEMPLATE = (
    'd="$HOME/.rodeo/logs"; log="$d/@BASE@-up.log"; pidf="$d/@BASE@-up.pid"; exitf="$d/@BASE@-up.exit"; '
    'state=none; pid=""; code=""; '
    # The exit file first: the job writes it before it removes its pid file.
    'if [ -s "$exitf" ]; then state=exited; read -r code _ <"$exitf" || true; '
    'elif [ -s "$pidf" ]; then read -r pid st <"$pidf" || true; state=gone; '
    'if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then '
    # A recorded start time must match, or the pid was reused. A followed
    # (non-detached) run records no start time: then it must still be rodeo.
    'if [ -n "${st:-}" ]; then '
    '[ "$(sed "s/^.*) //" "/proc/$pid/stat" | cut -d " " -f 20)" = "$st" ] && state=running; '
    'elif grep -qa rodeo "/proc/$pid/cmdline" 2>/dev/null; then state=running; fi; '
    'fi; fi; '
    'printf "AWS_UP_STATUS state=%s pid=%s exit=%s log=%s\\n" "$state" "$pid" "$code" "$log"; '
    'echo AWS_UP_LOG; [ -f "$log" ] && tail -n "$1" "$log"; exit 0'
)


def _status_script(base: str = "aws") -> str:
    """The remote shell that reports one target's deploy state and the tail of its log."""
    return _STATUS_TEMPLATE.replace("@BASE@", _log_base(base))


def remote_up_status(
    cfg: dict[str, Any],
    *,
    lines: int = 40,
    timeout: float = 60.0,
    host: ProvisionedHost | None = None,
    log_name: str | None = None,
    ssh_user: str | None = None,
    identity_file: str | None = None,
) -> dict[str, Any]:
    """State of the last remote ``rodeo up`` on this plan's host, plus its log tail.

    ``state`` is ``running``, ``exited`` (with ``exit``), ``gone`` (a pid file
    whose process is dead or reused: killed, or the host rebooted) or ``none``.

    Without *host* the AWS host this plan provisioned is read from local state. A caller
    that already knows the host passes it with *log_name*, *ssh_user* and *identity_file*
    (``deployment_target: remote``, whose host no provider created).
    """
    if host is None:
        plan_name = str(cfg.get("name") or "rodeo")
        saved = load_aws_host_state(plan_name)
        if not saved or not saved.get("public_ip"):
            raise ConfigError(
                f"no cloud host recorded for plan '{plan_name}' on this machine "
                f"(rodeo up --target {_cloud_target(cfg)} records it)"
            )
        host = ProvisionedHost(
            id=str(saved.get("host_id") or SINGLE_HOST_ID),
            ssh=str(saved.get("ssh") or saved["public_ip"]),
            public_ip=str(saved["public_ip"]),
            labels=dict(saved.get("labels") or {}),
            provider_id=saved.get("provider_id"),
        )
    n = max(1, min(int(lines), MAX_STATUS_LINES))
    inv, fh = _fleet_inventory_for(
        cfg, host, ssh_user=ssh_user, identity_file=identity_file,
    )
    result = run_remote(
        inv, fh, ["bash", "-c", _status_script(log_name or _cloud_target(cfg)), "status", str(n)],
        timeout=timeout,
    )
    out = result.stdout or ""
    if not result.ok or "AWS_UP_STATUS " not in out:
        raise ConfigError(
            f"could not read the deploy state on {host.public_ip} (exit {result.rc}): "
            f"{(result.stderr or out).strip()[-500:]}"
        )
    info = _marker_fields(out, "AWS_UP_STATUS")
    for key in ("pid", "exit"):
        value = info.get(key, "")
        info[key] = int(value) if str(value).lstrip("-").isdigit() else None
    tail = out.split("AWS_UP_LOG\n", 1)[1] if "AWS_UP_LOG\n" in out else ""
    return {**info, "host": host.id, "ip": host.public_ip, "lines": tail.splitlines()}


def destroy_primary(
    cfg: dict[str, Any],
    *,
    get_provider_fn: Callable[[str], Any] | None = None,
) -> list[Any]:
    """Terminate the ownership-tagged single-host instance for this plan."""
    provider_cfg = _provider_cfg(cfg)
    plan_name = str(cfg.get("name") or "rodeo")
    identity = resolve_ssh_identity(workshop=plan_name)
    ssh_user = str(provider_cfg.get("ssh_user") or default_ssh_user(provider_cfg["type"]))
    saved = load_aws_host_state(plan_name) or {}
    spec = ProvisionSpec(
        workshop=plan_name,
        host_ids=[SINGLE_HOST_ID],
        ssh_user=ssh_user,
        identity_file=identity,
        wait_ssh=False,
        known_instance_ids=[str(saved["provider_id"])] if saved.get("provider_id") else [],
    )
    getter = get_provider_fn or get_provider
    provider = getter(str(provider_cfg["type"]))
    results = provider.deprovision(spec, provider_cfg)
    clear_aws_host_state(plan_name)
    return results


def execute_aws_up(
    cfg: dict[str, Any],
    *,
    profile: str | None = None,
    ref: str | None = None,
    get_provider_fn: Callable[[str], Any] | None = None,
    remote_timeout: float = 7200.0,
    plan_dir: Path | None = None,
) -> ProvisionedHost:
    """Provision (or reuse) primary EC2 host, then remote-run ``rodeo up``."""
    host, _ = _provision_and_run(
        cfg, profile=profile, ref=ref, get_provider_fn=get_provider_fn,
        remote_timeout=remote_timeout, plan_dir=plan_dir, detach=False,
    )
    return host


def execute_aws_up_detached(
    cfg: dict[str, Any],
    *,
    profile: str | None = None,
    ref: str | None = None,
    get_provider_fn: Callable[[str], Any] | None = None,
    remote_timeout: float = 7200.0,
    plan_dir: Path | None = None,
) -> tuple[ProvisionedHost, dict[str, Any]]:
    """Like execute_aws_up, but return once the host has started ``rodeo up``.

    Returns the host and run_remote_up's record (pid, start, log, host).
    Raises AlreadyRunning when a deploy is already running there.
    """
    host, info = _provision_and_run(
        cfg, profile=profile, ref=ref, get_provider_fn=get_provider_fn,
        remote_timeout=remote_timeout, plan_dir=plan_dir, detach=True,
    )
    assert info is not None
    return host, info


def _provision_and_run(
    cfg: dict[str, Any],
    *,
    profile: str | None,
    ref: str | None,
    get_provider_fn: Callable[[str], Any] | None,
    remote_timeout: float,
    plan_dir: Path | None,
    detach: bool,
) -> tuple[ProvisionedHost, dict[str, Any] | None]:
    # Resolve the bootstrap source *before* provisioning: a typo'd ref must
    # cost nothing. Validating it at first use would reject it only after an
    # i7i is already running and billing.
    resolve_install_source(_provider_cfg(cfg), ref=ref)
    host = provision_primary(cfg, get_provider_fn=get_provider_fn)
    # The host runs `rodeo up --target <aws|gcp>`: it detects that it *is* that cloud's
    # VM (on_cloud_host), deploys locally, and keeps the cloud's host shaping.
    info = run_remote_up(
        cfg, host, profile=profile, ref=ref, timeout=remote_timeout, plan_dir=plan_dir,
        detach=detach, target=_cloud_target(cfg),
    )
    return host, info
