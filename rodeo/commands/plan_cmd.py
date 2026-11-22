"""rodeo plan — read-only diff of desired (plan YAML) vs actual (host) state."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import click
from rich.console import Console

from ..config import ConfigError, load_config, validate_config
from ..drift import DriftReport, collect_drift, inspect_host
from ..engine.libvirt import LibvirtDriver, names_safe_to_clean
from ..inventory import plan_vm_rows
from ..labimport import SECRET_KEY_RE
from ..profiles import get_profile
from ..state import load_state
from ._options import config_options

console = Console()

#: Version of the ``--output json`` object. Bump on a breaking change.
PLAN_JSON_SCHEMA = 1


@click.command("plan")
@config_options
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="json prints one object (no credentials, no secret values) for scripts and MCP.",
)
def plan_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    output_fmt: str = "text",
) -> None:
    """Show what `rodeo deploy` would do, without changing anything."""
    as_json = output_fmt.lower() == "json"
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    # Read-only: no secret placeholder is resolved, so nothing printed below
    # (versions, storage paths, ...) can carry a secret or run a command.
    try:
        cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    except ConfigError as exc:
        if as_json:
            click.echo(json.dumps({"schema": PLAN_JSON_SCHEMA, "error": scrub(str(exc), {})}, indent=2))
        else:
            console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(2)
    # The diff is still shown when the config would fail at deploy; the exit
    # code (2) tells scripts and MCP clients that deploy would refuse it.
    config_error: ConfigError | None = None
    try:
        validate_config(cfg, check=True)
    except ConfigError as exc:
        config_error = exc
        if not as_json:
            console.print(f"\n[yellow]⚠  Config issue (deploy will refuse): {exc}[/yellow]")
    profile = get_profile(cfg.get("type", "suse-virt"))

    actual = _inspect_host(cfg)
    report = collect_drift(cfg, actual=actual)
    if as_json:
        data = plan_data(cfg, profile, report, config_error)
        click.echo(json.dumps(scrub(data, cfg), indent=2))
        if config_error is not None:
            raise SystemExit(2)
        return

    if actual is None:
        console.print("\n[yellow]⚠  libvirt not reachable on host — showing desired state only.[/yellow]")
    _print_foreign_vms(cfg)
    create, change, ok = _print_vms(report)
    _print_network(report)
    downloads = _print_storage(cfg)
    # A VM create/change the diff above just reported means the "vms" phase's
    # cached "done" state no longer reflects the host — flag that instead of
    # printing a plain checkmark that contradicts the diff a few lines up.
    pending = _print_phases(cfg, profile, vms_drift=report.vms_plan_drift)

    console.print()
    if create or change or downloads or pending:
        parts = []
        if create:
            parts.append(f"[green]{create} to create[/green]")
        if change:
            parts.append(f"[yellow]{change} to change (recreate via rodeo clean + deploy)[/yellow]")
        if ok:
            parts.append(f"{ok} unchanged")
        if downloads:
            parts.append(f"{downloads} download(s)")
        console.print(f"  Plan: {', '.join(parts)} — {pending} phase(s) pending.")
        console.print("  Run [bold]rodeo deploy[/bold] to apply.\n")
    else:
        console.print("  [bold green]Nothing to do — deployment matches the plan.[/bold green]\n")
    if config_error is not None:
        raise SystemExit(2)


# --- JSON output ------------------------------------------------------------

def plan_data(cfg: dict, profile, report: DriftReport, config_error: ConfigError | None) -> dict:
    """The plan as one JSON-able object. Built from the same drift report,
    storage and phase checks as the text output; holds no credentials."""
    creates = [
        {"name": c.name, "desired": c.desired, "ip": c.ip}
        for c in report.vm_changes if c.kind == "create"
    ]
    changes = [
        {"name": c.name, "kind": c.kind, "desired": c.desired, "ip": c.ip,
         "actual_state": c.actual_state, "from": c.from_value, "to": c.to_value}
        for c in report.vm_changes if c.kind != "create"
    ]
    unchanged = [
        {"name": name, "desired": desired, "ip": ip, "state": state}
        for name, desired, ip, state in report.vm_ok
    ]
    if not report.reachable:
        net_state = "desired"
    elif report.net_active:
        net_state = "active"
    else:
        net_state = "define"
    image_dir, storage = _storage_rows(cfg)
    phases = _phase_rows(cfg, profile, vms_drift=report.vms_plan_drift)
    pending = [p["name"] for p in phases if p["state"] in ("pending", "failed")]
    downloads = [s for s in storage if not s["present"]]
    return {
        "schema": PLAN_JSON_SCHEMA,
        "name": cfg.get("name", "default"),
        "type": cfg.get("type", "suse-virt"),
        "host_reachable": report.reachable,
        "creates": creates,
        "changes": changes,
        "unchanged": unchanged,
        "foreign_vms": [{"name": n, "owner": o} for n, o in _foreign_vms(cfg)],
        "network": {"name": "default", "bridge": "virbr0", "state": net_state},
        "storage": {"image_dir": image_dir, "downloads": downloads},
        "phases": phases,
        "phases_pending": pending,
        "summary": {
            "create": report.create_count,
            "change": report.change_count,
            "unchanged": report.ok_count,
            "downloads": len(downloads),
            "phases_pending": len(pending),
        },
        "config_error": str(config_error) if config_error is not None else None,
    }


# ":/+": pathlib folds "//" into "/" in paths built from a URL-like value.
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*:/+)[^/@\s]+@")
_MASK = "***"
#: Shorter values are not masked: they would blank out ordinary words.
_MIN_SECRET_LEN = 4


def _plan_secret_values(cfg: Any, under_credentials: bool = False) -> set[str]:
    """Literal values the plan itself holds under credential keys (``credentials.*``
    or a key such as ``password``/``token``). Nothing resolves on this read path,
    so these literals are the only secret values the config can carry."""
    out: set[str] = set()
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            secretish = under_credentials or k == "credentials" or bool(SECRET_KEY_RE.search(str(k)))
            if isinstance(v, (dict, list)):
                out |= _plan_secret_values(v, secretish)
            elif secretish and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                out.add(str(v))
    elif isinstance(cfg, list):
        for v in cfg:
            out |= _plan_secret_values(v, under_credentials)
    return {v for v in out if len(v) >= _MIN_SECRET_LEN}


def scrub(obj: Any, cfg: dict) -> Any:
    """Mask credential values from ``cfg`` and URL userinfo in every string of ``obj``."""
    secrets = sorted(_plan_secret_values(cfg), key=len, reverse=True)

    def _str(s: str) -> str:
        s = _URL_USERINFO.sub(r"\1" + _MASK + "@", s)
        for value in secrets:
            s = s.replace(value, _MASK)
        return s

    def _walk(o: Any) -> Any:
        if isinstance(o, str):
            return _str(o)
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        return o

    return _walk(obj)


# --- Text output ------------------------------------------------------------

def _foreign_vms(cfg: dict) -> list[tuple[str, str]]:
    """Planned names already stamped for a different plan: (name, owner)."""
    planned = [name for name, _ in plan_vm_rows(cfg)]
    if not planned:
        return []
    try:
        with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
            xml_by_name = {name: lv.domain_xml(name) for name in planned}
    except Exception:
        return []
    _, foreign = names_safe_to_clean(planned, xml_by_name, cfg.get("name", "default"))
    return list(foreign)


def _print_foreign_vms(cfg: dict) -> None:
    """Warn when a planned name is already stamped for a different plan."""
    for name, owner in _foreign_vms(cfg):
        console.print(
            f"[yellow]⚠  {name} exists and is owned by plan {owner}. "
            "rodeo clean will skip it.[/yellow]"
        )


def _inspect_host(cfg: dict) -> dict | None:
    """Shim so tests can monkeypatch plan_cmd._inspect_host (delegates to drift)."""
    return inspect_host(cfg)


def _print_vms(report: DriftReport) -> tuple[int, int, int]:
    console.print("\n[bold]  Virtual machines[/bold]")
    for c in report.vm_changes:
        line = f"    {c.name:<12} {c.desired:<22} {c.ip:<16}"
        if c.kind == "create":
            console.print(f"{line} [green]+ create[/green]")
        elif c.kind == "memory":
            console.print(
                f"{line} [yellow]~ memory {c.from_value} → {c.to_value} MiB[/yellow]"
            )
        elif c.kind == "vcpu":
            console.print(f"{line} [yellow]~ vcpu {c.from_value} → {c.to_value}[/yellow]")
    for name, desired, ip, state in report.vm_ok:
        line = f"    {name:<12} {desired:<22} {ip:<16}"
        if state == "desired":
            console.print(f"{line} [dim]desired[/dim]")
        else:
            console.print(f"{line} [dim]✓ {state}[/dim]")
    return report.create_count, report.change_count, report.ok_count


def _print_network(report: DriftReport) -> None:
    console.print("\n[bold]  Network[/bold]")
    if not report.reachable:
        console.print("    default (virbr0)  [dim]desired[/dim]")
    elif report.net_active:
        console.print("    default (virbr0)  [dim]✓ active[/dim]")
    else:
        console.print(
            "    default (virbr0)  [green]+ define/start[/green] [dim](vms/cluster phases)[/dim]"
        )


def _storage_rows(cfg: dict) -> tuple[str, list[dict]]:
    """(image_dir, rows) for the image and PXE artifacts a deploy needs.
    Each row: ``artifact``, ``section`` (storage|pxe), ``action``, ``present``."""
    image_dir = Path(cfg["storage"]["image_dir"])
    version = cfg.get("versions", {}).get("harvester", "?")
    pxe_root = Path("/srv/harvester-pxe")
    artifacts = [f"harvester-v{version}-amd64.iso"]
    artifacts += [f"{name}-vda.qcow2" for name in cfg.get("vms", {})]
    pxe_artifacts = [
        ("ipxe.efi", Path("/var/lib/libvirt/dnsmasq/ipxe.efi")),
        (f"harvester-v{version}-vmlinuz-amd64", pxe_root / "harvester" / f"harvester-v{version}-vmlinuz-amd64"),
        (f"harvester-v{version}-initrd-amd64", pxe_root / "harvester" / f"harvester-v{version}-initrd-amd64"),
        (f"harvester-v{version}-rootfs-amd64.squashfs", pxe_root / "harvester" / f"harvester-v{version}-rootfs-amd64.squashfs"),
    ]
    rows = [
        {"artifact": a, "section": "storage",
         "action": "download" if a.endswith(".iso") else "create",
         "present": (image_dir / a).exists()}
        for a in artifacts
    ]
    rows += [
        {"artifact": label, "section": "pxe", "action": "provision", "present": path.exists()}
        for label, path in pxe_artifacts
    ]
    return str(image_dir), rows


def _print_storage(cfg: dict) -> int:
    image_dir, rows = _storage_rows(cfg)
    console.print(f"\n[bold]  Storage[/bold] [dim]({image_dir})[/dim]")
    downloads = 0
    pxe_header = False
    for row in rows:
        if row["section"] == "pxe" and not pxe_header:
            console.print("\n[bold]  PXE boot[/bold] [dim](pxe_server phase)[/dim]")
            pxe_header = True
        artifact = row["artifact"]
        if row["present"]:
            console.print(f"    {artifact:<34} [dim]✓ present[/dim]")
        else:
            console.print(f"    {artifact:<34} [green]+ {row['action']}[/green]")
            downloads += 1
    return downloads


def _phase_rows(cfg: dict, profile, vms_drift: bool = False) -> list[dict]:
    """One ``{name, state}`` per profile phase; state is done, drift, guarded,
    pending or failed (pending with a recorded error)."""
    plan_name = cfg.get("name", "default")
    state = load_state(plan_name).get("phases", {})
    guard = cfg.get("deployment_target") == "instruqt"
    rows = []
    for phase in profile.phases:
        info = state.get(phase, {})
        if info.get("completed") and phase == "vms" and vms_drift:
            st = "drift"
        elif info.get("completed"):
            st = "done"
        elif guard and phase in profile.guarded_phases:
            st = "guarded"
        else:
            st = "failed" if info.get("last_error") else "pending"
        rows.append({"name": phase, "state": st})
    return rows


def _print_phases(cfg: dict, profile, vms_drift: bool = False) -> int:
    console.print("\n[bold]  Phases[/bold]")
    pending = 0
    for row in _phase_rows(cfg, profile, vms_drift=vms_drift):
        phase, st = row["name"], row["state"]
        if st == "drift":
            console.print(
                f"    [yellow]~[/yellow]  {phase:<10} "
                f"[yellow]done, but drift detected (see Virtual machines above)[/yellow]"
            )
        elif st == "done":
            console.print(f"    [green]✓[/green]  {phase:<10} [dim]done[/dim]")
        elif st == "guarded":
            console.print(
                f"    [yellow]⊘[/yellow]  {phase:<10} [dim]guarded (instruqt) — needs --finalise[/dim]"
            )
        else:
            marker = "[red]✗[/red]" if st == "failed" else "[dim]○[/dim]"
            console.print(f"    {marker}  {phase:<10} [dim]pending[/dim]")
            pending += 1
    return pending
