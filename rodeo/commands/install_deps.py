"""rodeo install-deps — provision system packages for the rodeo."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import click
from rich.console import Console

console = Console()

_ZYPPER_PATTERNS = ["kvm_server", "kvm_tools"]
# python3-lxml is required by the community.libvirt Ansible modules (virt_net etc.)
# in the vms phase. On SUSE the generic name resolves via capabilities to the
# versioned package (e.g. python313-lxml).
# guestfs-tools provides virt-customize, used to inject cloud-init into the
# Rancher VM image (Leap 16 base images ship without it). See roles/vms images.yml.
_ZYPPER_PACKAGES = [
    "jq", "curl", "tmux", "xorriso", "qemu-tools",
    "qemu-ovmf-x86_64", "firewalld", "python3-firewall",
    "python3-libvirt-python", "python3-lxml", "guestfs-tools",
]
_APT_PACKAGES = [
    "qemu-kvm", "libvirt-daemon-system", "libvirt-clients",
    "xorriso", "tmux", "python3-libvirt", "python3-lxml", "libguestfs-tools",
    "jq", "curl", "firewalld",
]
_DNF_PACKAGES = [
    "qemu-kvm", "libvirt", "virt-install", "xorriso",
    "tmux", "python3-libvirt", "python3-lxml", "guestfs-tools",
    "jq", "curl", "firewalld",
]
_K8S_CHANNEL = "v1.36"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)


def _detect_distro() -> str:
    try:
        text = Path("/etc/os-release").read_text()
        ltext = text.lower()
        if "suse" in ltext or "opensuse" in ltext:
            return "suse"
        if "ubuntu" in ltext or "debian" in ltext:
            return "debian"
        if "fedora" in ltext or "rhel" in ltext or "centos" in ltext:
            return "fedora"
    except FileNotFoundError:
        pass
    for tool, distro in [("zypper", "suse"), ("apt-get", "debian"), ("dnf", "fedora")]:
        if shutil.which(tool):
            return distro
    return "unknown"


def _install_suse() -> None:
    console.print("[bold]  Installing zypper patterns...[/bold]")
    _run(["zypper", "--non-interactive", "install", "-t", "pattern"] + _ZYPPER_PATTERNS)
    console.print("[bold]  Installing zypper packages...[/bold]")
    _run(["zypper", "--non-interactive", "install"] + _ZYPPER_PACKAGES)
    # Ensure the libvirt Python binding is present.
    # On SLES/SUSE the package is python3-libvirt-python (suppress "no provider" if already satisfied or variant).
    try:
        _run(["zypper", "--non-interactive", "install", "python3-libvirt-python"], check=False)
    except Exception:
        pass
    # Start libvirt daemons (modular virtqemud on SLES 16+) so the socket exists for `plan` and `status`
    # to inspect current host state. This is needed before any deploy.
    # Note: some units may not exist on modular SLES; ignore failures (suppress stderr to avoid noise).
    try:
        for svc in ("virtqemud", "virtnetworkd", "virtstoraged"):
            _run(["systemctl", "enable", "--now", svc], check=False, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    console.print("[bold]  Adding Kubernetes repo for kubectl...[/bold]")
    try:
        repo_url = f"https://pkgs.k8s.io/core:/stable:/{_K8S_CHANNEL}/rpm/"
        _run(["zypper", "--non-interactive", "addrepo", "--gpgcheck-strict", repo_url, "kubernetes"], check=False)
        _run(["zypper", "--non-interactive", "--gpg-auto-import-keys", "refresh"])
        _run(["zypper", "--non-interactive", "install", "kubectl"])
    except subprocess.CalledProcessError:
        console.print("[yellow]  ⚠  kubectl install failed — add the Kubernetes zypper repo manually[/yellow]")


def _install_debian() -> None:
    _run(["apt-get", "update", "-q"])
    _run(["apt-get", "install", "-y"] + _APT_PACKAGES)
    _install_kubectl_apt()


def _install_kubectl_apt() -> None:
    try:
        keyring = "/etc/apt/keyrings/kubernetes-apt-keyring.gpg"
        os.makedirs("/etc/apt/keyrings", exist_ok=True)
        urllib.request.urlretrieve(
            f"https://pkgs.k8s.io/core:/stable:/{_K8S_CHANNEL}/deb/Release.key",
            "/tmp/k8s-release.key",
        )
        _run(["gpg", "--dearmor", "-o", keyring, "/tmp/k8s-release.key"])
        with open("/etc/apt/sources.list.d/kubernetes.list", "w") as f:
            f.write(
                f"deb [signed-by={keyring}] "
                f"https://pkgs.k8s.io/core:/stable:/{_K8S_CHANNEL}/deb/ /\n"
            )
        _run(["apt-get", "update", "-q"])
        _run(["apt-get", "install", "-y", "kubectl"])
    except Exception as exc:
        console.print(f"[yellow]  ⚠  kubectl install failed: {exc}[/yellow]")


def _install_fedora() -> None:
    _run(["dnf", "install", "-y"] + _DNF_PACKAGES)
    try:
        with open("/etc/yum.repos.d/kubernetes.repo", "w") as f:
            f.write(
                "[kubernetes]\n"
                "name=Kubernetes\n"
                f"baseurl=https://pkgs.k8s.io/core:/stable:/{_K8S_CHANNEL}/rpm/\n"
                "enabled=1\ngpgcheck=1\n"
                f"gpgkey=https://pkgs.k8s.io/core:/stable:/{_K8S_CHANNEL}/rpm/repodata/repomd.xml.key\n"
            )
        _run(["dnf", "install", "-y", "kubectl"])
    except Exception as exc:
        console.print(f"[yellow]  ⚠  kubectl install failed: {exc}[/yellow]")


_ANSIBLE_DISTRO_PKG = {
    "suse":   ["zypper", "--non-interactive", "install", "ansible-core"],
    "debian": ["apt-get", "install", "-y", "ansible-core"],
    "fedora": ["dnf", "install", "-y", "ansible-core"],
}


def _install_ansible(distro: str) -> None:
    """Prefer the distro package (plays nice with PEP 668); fall back to pip."""
    if shutil.which("ansible-playbook"):
        console.print("[bold]  ansible-playbook already present — skipping install.[/bold]")
        _install_ansible_collections()
        return

    pkg_cmd = _ANSIBLE_DISTRO_PKG.get(distro)
    installed = False
    if pkg_cmd:
        console.print(f"[bold]  Installing ansible-core via {pkg_cmd[0]}...[/bold]")
        installed = _run(pkg_cmd, check=False).returncode == 0 and \
            shutil.which("ansible-playbook") is not None

    if not installed:
        console.print("[bold]  Installing ansible-core via pip...[/bold]")
        _run([sys.executable, "-m", "pip", "install", "--quiet", "ansible-core>=2.16"])

    _install_ansible_collections()


def _install_ansible_collections() -> None:
    console.print("[bold]  Installing Ansible collections...[/bold]")
    req_file = Path(__file__).parent.parent / "data" / "ansible" / "requirements.yml"
    if not req_file.exists():
        console.print("[yellow]  ⚠  requirements.yml not found, skipping collection install[/yellow]")
        return
    r = _run(["ansible-galaxy", "collection", "install", "-r", str(req_file)], check=False)
    if r.returncode != 0:
        console.print(
            "[red]  ✗  ansible-galaxy collection install failed — "
            "rodeo deploy will retry, but check network access to galaxy.ansible.com[/red]"
        )


def _ensure_invoking_user_in_libvirt_group() -> None:
    """Add the real (non-root) user to the ``libvirt`` group.

    rodeo's privileged deploy phases self-escalate via sudo (see privilege.py),
    but read-only commands (``status``, ``plan``) only need to open the libvirt
    socket — they should never require root. Without ``libvirt`` group
    membership, a non-root ``qemu:///system`` connection fails outright
    ("no polkit agent available"), forcing every user to reach for sudo just to
    check status. Most distros ship a default polkit rule granting
    ``libvirt`` group members that access, so group membership alone is
    normally enough.

    Uses ``SUDO_USER`` (set by the ``sudo rodeo install-deps`` invocation this
    command already requires) rather than any hardcoded name, so this benefits
    whoever actually runs it. No-op when genuinely root with no invoking user
    (nothing to grant access to) or when the host has no ``libvirt`` group.
    """
    user = os.environ.get("SUDO_USER")
    if not user or user == "root":
        return
    if subprocess.run(["getent", "group", "libvirt"], capture_output=True).returncode != 0:
        console.print("[yellow]  ⚠  no 'libvirt' group on this host — skipping unprivileged libvirt access setup[/yellow]")
        return
    current_groups = subprocess.run(
        ["id", "-nG", user], capture_output=True, text=True
    ).stdout.split()
    if "libvirt" in current_groups:
        console.print(f"[green]  ✓  {user} already in the libvirt group[/green]")
        return
    r = _run(["usermod", "-aG", "libvirt", user], check=False)
    if r.returncode == 0:
        console.print(
            f"[green]  ✓  Added {user} to the libvirt group[/green] "
            "[dim](log out/in, or run `newgrp libvirt`, for it to take effect — "
            "then 'rodeo status'/'rodeo plan' work without sudo)[/dim]"
        )
    else:
        console.print(
            f"[yellow]  ⚠  Could not add {user} to the libvirt group (usermod exit {r.returncode}) "
            "— read-only commands will need sudo[/yellow]"
        )


@click.command("install-deps")
@click.option("--skip-ansible", is_flag=True, help="Skip ansible-core pip install.")
@click.option("--link", is_flag=True, help="Create/update /usr/local/bin/rodeo symlink to this invocation (so plain 'rodeo' and 'sudo rodeo' work without exports or full paths).")
@click.option("--force-link", is_flag=True, help="Force overwrite an existing /usr/local/bin/rodeo symlink.")
def install_deps_cmd(skip_ansible: bool, link: bool, force_link: bool) -> None:
    """Install system packages and tools required to run rodeo deploy."""
    if os.geteuid() != 0:
        console.print("[red]Must run as root: sudo rodeo install-deps[/red]")
        raise SystemExit(1)

    distro = _detect_distro()
    console.print(f"\n[bold cyan]Installing packages for: {distro}[/bold cyan]")

    try:
        if distro == "suse":
            _install_suse()
        elif distro == "debian":
            _install_debian()
        elif distro == "fedora":
            _install_fedora()
        else:
            console.print("[red]Cannot detect distro. Install packages manually.[/red]")
            raise SystemExit(1)

        if not skip_ansible:
            _install_ansible(distro)
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(str(c) for c in exc.cmd[:4])
        console.print(
            f"\n[red]✗  Package installation failed (exit {exc.returncode}): {cmd} ...[/red]\n"
            "Check network access and repository configuration, then re-run."
        )
        raise SystemExit(exc.returncode or 1)

    console.print("\n[bold green]✓  Dependencies installed.[/bold green]")

    _ensure_invoking_user_in_libvirt_group()

    # Verify critical Python bindings (especially for SLES where libvirt-python
    # is a system package that venvs need --system-site-packages to see).
    try:
        import libvirt  # noqa: F401
        console.print("[green]  libvirt-python binding importable.[/green]")
    except ImportError:
        console.print(
            "[yellow]  ⚠  libvirt-python binding not importable in this Python. "
            "If using a venv, recreate it with --system-site-packages.[/yellow]"
        )

    # Make rodeo feel like a normal system binary: create a stable symlink in /usr/local/bin.
    # This is the main win for the "first phase" friction (no more export RODEO=long/path every shell,
    # and 'sudo rodeo' becomes possible without remembering the venv location).
    # Call with --link (or --force-link) on the *first* sudo $RODEO install-deps.
    # The symlink points at the venv's rodeo script (its shebang keeps the correct python + site-packages).
    # Re-run --force-link if you move/ recreate the venv later.
    rodeo_bin = os.path.realpath(sys.argv[0]) if sys.argv[0] else None
    target = Path("/usr/local/bin/rodeo")
    if (link or force_link) and rodeo_bin and Path(rodeo_bin).exists():
        try:
            cur = os.path.realpath(str(target)) if (target.exists() or target.is_symlink()) else None
            desired = os.path.realpath(rodeo_bin)
            if target.exists() or target.is_symlink():
                if force_link:
                    target.unlink(missing_ok=True)
                    target.symlink_to(rodeo_bin)
                    console.print(f"[green]✓  /usr/local/bin/rodeo -> {rodeo_bin} (overwritten)[/green]")
                elif cur == desired:
                    console.print("[green]✓  /usr/local/bin/rodeo already points here[/green]")
                else:
                    console.print("[yellow]⚠  /usr/local/bin/rodeo exists and points elsewhere — use --force-link to replace[/yellow]")
            else:
                target.symlink_to(rodeo_bin)
                console.print(f"[green]✓  /usr/local/bin/rodeo -> {rodeo_bin}[/green]")
            console.print("[dim]  Now 'rodeo' (in PATH) and 'sudo rodeo' should work. If sudo restricts PATH, use: sudo env PATH=/usr/local/bin:$PATH rodeo ...[/dim]")
        except Exception as exc:
            console.print(f"[yellow]⚠  Could not create /usr/local/bin/rodeo link: {exc}[/yellow]")
    elif rodeo_bin:
        # Always give the one-liner so users know the easy path even if they didn't pass --link this time.
        console.print("\n[bold]Make 'rodeo' a normal command (recommended, run once):[/bold]")
        console.print(f"  sudo {rodeo_bin} install-deps --link")
        console.print("  # (or sudo ln -s " + rodeo_bin + " /usr/local/bin/rodeo )")
        console.print("  # After this, drop the 'export RODEO=...' and long paths in future shells.")

    console.print("Next step: [bold]rodeo init[/bold]  (or `rodeo init --example harvester-lab-config /path/to/lab` for the 2-node test variant)")
    console.print("[dim]After --link above you can usually just use 'rodeo' and 'sudo -E rodeo ...'[/dim]")
