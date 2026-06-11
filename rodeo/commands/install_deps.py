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
_ZYPPER_PACKAGES = [
    "jq", "curl", "tmux", "xorriso", "qemu-tools",
    "qemu-ovmf-x86_64", "firewalld", "python3-firewall",
    "python3-libvirt-python",
]
_APT_PACKAGES = [
    "qemu-kvm", "libvirt-daemon-system", "libvirt-clients",
    "xorriso", "tmux", "python3-libvirt", "jq", "curl", "firewalld",
]
_DNF_PACKAGES = [
    "qemu-kvm", "libvirt", "virt-install", "xorriso",
    "tmux", "python3-libvirt", "jq", "curl", "firewalld",
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


@click.command("install-deps")
@click.option("--skip-ansible", is_flag=True, help="Skip ansible-core pip install.")
def install_deps_cmd(skip_ansible: bool) -> None:
    """Install system packages and tools required to run rodeo deploy."""
    if os.geteuid() != 0:
        console.print("[red]Must run as root: sudo rodeo install-deps[/red]")
        raise SystemExit(1)

    distro = _detect_distro()
    console.print(f"\n[bold cyan]Installing packages for: {distro}[/bold cyan]")

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

    console.print("\n[bold green]✓  Dependencies installed.[/bold green]")
    console.print("Next step: [bold]rodeo init[/bold]")
