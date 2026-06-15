"""Host readiness checks and profile-fit detection.

Two audiences share this module:

- ``rodeo deploy --check`` and ``rodeo up`` call :func:`run_preflight` with a loaded
  plan to confirm the host can build *that* lab (RAM/disk sized from the plan).
- ``rodeo doctor`` and ``rodeo up`` (before a lab exists) call :func:`detect_host` +
  :func:`recommend_profile` to inspect the machine and suggest the largest lab that fits.

Detection uses only the stdlib (``/proc``, ``os``, ``shutil``) so it adds no deps and
degrades gracefully when a value cannot be read.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from rich.console import Console

console = Console()

DEFAULT_IMAGE_DIR = "/var/lib/libvirt/images"

CORE_TOOLS = ("ansible-playbook", "ansible-galaxy", "kubectl")
OPTIONAL_TOOLS = ("virsh", "ssh")
# Python modules the deploy needs: libvirt-python (LibvirtDriver) and lxml
# (community.libvirt Ansible modules in the vms phase). Missing lxml only surfaces
# ~20 min into vms, so we check up front.
CORE_PY_MODULES = ("libvirt", "lxml")

# Beginner-facing profiles, smallest first, with the RAM each realistically needs.
# `up`/`doctor` recommend the largest profile whose need fits available RAM.
PROFILE_SIZING = [
    {"name": "rancher", "ram_gib": 10, "label": "Rancher Prime on K3s (1 VM, no Harvester)"},
    {"name": "test", "ram_gib": 36, "label": "2-node Harvester (no Rancher)"},
    {"name": "harvester-ha", "ram_gib": 52, "label": "3-node Harvester, no Rancher (etcd HA)"},
    {"name": "harvester", "ram_gib": 60, "label": "3-node Harvester + Rancher Prime"},
]


def _read_meminfo() -> tuple[int, int]:
    """Return (total_mib, available_mib) from /proc/meminfo, or (0, 0)."""
    total = avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    return total, avail


def _read_avail_mib() -> int:
    """Available RAM in MiB (kept for the plan-sized RAM check)."""
    return _read_meminfo()[1]


def _nested_enabled() -> bool:
    for p in (
        "/sys/module/kvm_intel/parameters/nested",
        "/sys/module/kvm_amd/parameters/nested",
    ):
        try:
            if Path(p).read_text().strip() in ("1", "Y"):
                return True
        except OSError:
            pass
    return False


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_pkg_mgr() -> str:
    """Best-effort package manager: zypper | apt | dnf | unknown."""
    for tool, mgr in (("zypper", "zypper"), ("apt-get", "apt"), ("dnf", "dnf")):
        if shutil.which(tool):
            return mgr
    return "unknown"


def _free_gib(image_dir: str) -> int:
    try:
        return shutil.disk_usage(image_dir).free // (1024 ** 3)
    except OSError:
        return -1


def detect_host(image_dir: str = DEFAULT_IMAGE_DIR) -> dict:
    """Inspect the machine. All fields degrade gracefully when unreadable."""
    total_mib, avail_mib = _read_meminfo()
    return {
        "is_root": os.geteuid() == 0,
        "pkg_mgr": detect_pkg_mgr(),
        "has_kvm": Path("/dev/kvm").exists(),
        "nested": _nested_enabled(),
        "ram_total_gib": total_mib // 1024 if total_mib else 0,
        "ram_avail_gib": avail_mib // 1024 if avail_mib else 0,
        "cpus": os.cpu_count() or 0,
        "image_dir": image_dir,
        "disk_free_gib": _free_gib(image_dir),
        "core_tools": {t: shutil.which(t) is not None for t in CORE_TOOLS},
        "optional_tools": {t: shutil.which(t) is not None for t in OPTIONAL_TOOLS},
        "py_modules": {m: _module_present(m) for m in CORE_PY_MODULES},
    }


def missing_core_tools(host: dict) -> list[str]:
    missing = [t for t, ok in host["core_tools"].items() if not ok]
    missing += [f"python3-{m}" for m, ok in host.get("py_modules", {}).items() if not ok and m == "lxml"]
    return missing


def recommend_profile(host: dict) -> tuple[str, bool]:
    """Return (profile_name, fits). Largest profile whose RAM need is met.

    When nothing fits, returns the smallest profile with ``fits=False`` so the caller
    can warn honestly instead of silently starting a deploy that will fail.
    """
    avail = host.get("ram_avail_gib") or host.get("ram_total_gib") or 0
    best = None
    for tier in PROFILE_SIZING:
        if avail >= tier["ram_gib"]:
            best = tier["name"]
    if best is not None:
        return best, True
    return PROFILE_SIZING[0]["name"], False


def profile_label(name: str) -> str:
    for tier in PROFILE_SIZING:
        if tier["name"] == name:
            return tier["label"]
    return name


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _print_checks(title: str, checks: list[tuple[str, bool, str, bool]]) -> bool:
    """Print a check list. ``checks`` items are (label, ok, detail, optional).

    Optional failures render as warnings and do not flip the overall result.
    """
    console.print(f"\n[bold]{title}[/bold]\n")
    all_ok = True
    for label, ok, detail, optional in checks:
        if ok:
            console.print(f"  [green]✓[/green]  {label}")
        elif optional:
            console.print(f"  [yellow]⚠[/yellow]  {label}  [dim]{detail}[/dim]")
        else:
            console.print(f"  [red]✗[/red]  {label}  [dim]{detail}[/dim]")
            all_ok = False
    console.print()
    return all_ok


def run_preflight(cfg: dict, root: Path) -> bool:
    """Plan-sized preflight for ``deploy --check`` / ``up``. Prints results, returns ok.

    Core tools (ansible, kubectl) and host basics are hard requirements; virsh/ssh are
    warnings only (day-2 + fallbacks, libvirt-python is the primary path).
    """
    res = cfg.get("resources", {})
    storage = cfg.get("storage", {})
    image_dir = storage.get("image_dir", DEFAULT_IMAGE_DIR)

    checks: list[tuple[str, bool, str, bool]] = []
    checks.append(("root", os.geteuid() == 0, "not running as root — some phases require root", False))
    checks.append(("/dev/kvm", Path("/dev/kvm").exists(), "/dev/kvm not found — is KVM enabled?", False))
    checks.append(("nested virt", _nested_enabled(), "nested virtualization not enabled in kvm module", False))

    avail_mib = _read_avail_mib()
    need_mib = (
        res.get("harvester", {}).get("memory_mib", 16384) * 3
        + res.get("rancher", {}).get("memory_mib", 8192)
    )
    if avail_mib > 0:
        checks.append(("RAM", avail_mib >= need_mib,
                       f"need {need_mib // 1024} GB, have {avail_mib // 1024} GB available", False))
    else:
        checks.append(("RAM", True, "could not read /proc/meminfo", False))

    need_gb = (
        res.get("harvester", {}).get("disk_gb", 270) * 3
        + res.get("rancher", {}).get("disk_gb", 60)
        + 30
    )
    free_gb = _free_gib(image_dir)
    if free_gb >= 0:
        checks.append(("disk", free_gb >= need_gb,
                       f"need ~{need_gb} GB, have {free_gb} GB free in {image_dir}", False))
    else:
        checks.append(("disk", True, f"cannot stat {image_dir}", False))

    for tool in CORE_TOOLS:
        checks.append((tool, shutil.which(tool) is not None, f"{tool} not found in PATH", False))
    for mod in CORE_PY_MODULES:
        checks.append((
            f"python: {mod}", _module_present(mod),
            f"Python module '{mod}' not importable — run: sudo rodeo install-deps", False,
        ))
    for tool in OPTIONAL_TOOLS:
        checks.append((tool, shutil.which(tool) is not None,
                       f"{tool} not found in PATH (needed for 'attach', 'ssh', and some fallbacks)", True))

    title = f"Preflight — {cfg.get('name', 'rodeo')}"
    all_ok = _print_checks(title, checks)
    if all_ok:
        console.print("[bold green]All checks passed.[/bold green]\n")
    else:
        console.print("[bold red]One or more checks failed.[/bold red]\n")
    return all_ok
