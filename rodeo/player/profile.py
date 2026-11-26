"""What the rodeo player shows for one lab: its ``player.yaml``.

Each lab profile that can run in the player ships a ``player.yaml`` next to
its ``rodeo-plan.yaml`` (``rodeo/data/examples/<profile>/player.yaml``)::

    title: SUSE Virtualization Rodeo
    track_repo: https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo.git  # optional
    guide_url: https://.../workshop-guide/  # optional: web guide when there is no track
    kubeconfig: harvester-kubeconfig      # file rodeo up writes in ~/.rodeo (optional)
    student_ssh_key: /opt/.../lab-vm-key  # student's default SSH key (optional)
    uis:                                  # product UIs, each behind its own name
      - key: hv                           # name prefix: hv-<lab>.<zone>
        label: SUSE Virtualization
        upstream: 192.168.122.10:443      # on the lab network
        scheme: https                     # https (default) or http
        tab_port: 8443                    # the port the track's tabs use
    student_libvirt: false                # student may use virsh (qemu:///system); root-equivalent
    student_vm_access: [192.168.122.9]    # lab VMs where the student's own key logs in as root
    credentials:                          # shown to the student in the player
      - label: SUSE Virtualization
        user: admin
        password: ??harvester_admin_password
    variables:                            # track variable: ??secrets.yaml key
      HARVESTER_PASSWORD: ??harvester_admin_password

Instructions come from the track or, without one, from ``guide_url``; with
neither the player shows the UIs, a terminal and the credentials, with the
guide folded. ``load(name)`` for a profile with no ``player.yaml`` is an
error: it never borrows another product's UIs. ``load(None)`` is virt-rodeo,
for hosts set up before profiles existed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXAMPLES = Path(__file__).resolve().parents[1] / "data" / "examples"
DEFAULT_PROFILE = "virt-rodeo"
FILE = "player.yaml"
MAX_UIS = 8
MAX_CREDENTIALS = 8

_KEY_RE = re.compile(r"^[a-z][a-z0-9]{0,14}$")
_UPSTREAM_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}:\d{1,5}$")
_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_RESERVED = {"player", "www", "terminal"}
_TEXT_RE = re.compile(r"^[^\x00-\x1f<>]{1,80}$")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerUI:
    key: str        # host key and name prefix
    label: str      # what people read (portal, setup output)
    upstream: str   # IP:port on the lab network
    tab_port: int   # the track tab's port, e.g. 8443
    scheme: str = "https"  # how the product serves it: https (self-signed is fine) or http


@dataclass(frozen=True)
class PlayerCredential:
    label: str      # e.g. "SUSE Rancher Prime"
    user: str
    secret: str     # secrets.yaml key of the password (never the value)


@dataclass(frozen=True)
class PlayerProfile:
    name: str
    title: str
    track_repo: str
    uis: tuple[PlayerUI, ...]
    kubeconfig: str = ""
    student_ssh_key: str = ""
    variables: dict[str, str] = field(default_factory=dict)  # TRACK_VAR -> secrets.yaml key
    guide_url: str = ""
    credentials: tuple[PlayerCredential, ...] = ()
    # Labs whose guide drives VMs from the host (virsh, ssh root@<vm>). libvirt
    # access is root-equivalent on the host: fleets need per_host_ssh_key.
    student_libvirt: bool = False
    student_vm_access: tuple[str, ...] = ()

    def ui(self, key: str) -> PlayerUI | None:
        return next((u for u in self.uis if u.key == key), None)

    def with_upstreams(self, overrides: dict[str, str]) -> PlayerProfile:
        """The same profile with some UIs pointed elsewhere (``--upstream``)."""
        unknown = set(overrides) - {u.key for u in self.uis}
        if unknown:
            raise ProfileError(f"no UI {sorted(unknown)[0]!r} in profile {self.name} "
                               f"(it has: {', '.join(u.key for u in self.uis)})")
        uis = tuple(PlayerUI(u.key, u.label, overrides.get(u.key, u.upstream), u.tab_port, u.scheme)
                    for u in self.uis)
        out = PlayerProfile(self.name, self.title, self.track_repo, uis, self.kubeconfig,
                            self.student_ssh_key, dict(self.variables), self.guide_url, self.credentials,
                            self.student_libvirt, self.student_vm_access)
        out.validate()
        return out

    def validate(self) -> None:
        where = f"{self.name}/{FILE}"
        if not self.title.strip():
            raise ProfileError(f"{where}: title is empty")
        if self.track_repo and not self.track_repo.startswith("https://"):
            raise ProfileError(f"{where}: track_repo must be an https:// git URL")
        if self.guide_url and not re.match(r"^https://[^\s<>\"']+$", self.guide_url):
            raise ProfileError(f"{where}: guide_url must be an https:// address")
        if len(self.credentials) > MAX_CREDENTIALS:
            raise ProfileError(f"{where}: at most {MAX_CREDENTIALS} credentials")
        for ip in self.student_vm_access:
            if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                raise ProfileError(f"{where}: student_vm_access lists lab VM addresses, got {ip!r}")
        if self.student_vm_access and self.student_ssh_key:
            raise ProfileError(f"{where}: student_ssh_key and student_vm_access both set the student's "
                               "default key: pick one")
        for c in self.credentials:
            if not _TEXT_RE.match(c.label) or not _TEXT_RE.match(c.user) or not _FILE_RE.match(c.secret):
                raise ProfileError(f"{where}: credentials need a label, a user and password: ??<secrets key>")
        if not 1 <= len(self.uis) <= MAX_UIS:
            raise ProfileError(f"{where}: uis needs 1 to {MAX_UIS} entries")
        keys, ports = set(), set()
        for u in self.uis:
            if not _KEY_RE.match(u.key) or u.key in _RESERVED:
                raise ProfileError(f"{where}: UI key {u.key!r} must be a short lowercase name "
                                   f"(not {', '.join(sorted(_RESERVED))})")
            if not _UPSTREAM_RE.match(u.upstream):
                raise ProfileError(f"{where}: upstream must be IP:port, got {u.upstream!r}")
            if u.scheme not in ("https", "http"):
                raise ProfileError(f"{where}: UI {u.key} scheme must be https or http, got {u.scheme!r}")
            if not 1 <= u.tab_port <= 65535:
                raise ProfileError(f"{where}: tab_port {u.tab_port} is not a port")
            if u.key in keys or u.tab_port in ports:
                raise ProfileError(f"{where}: UI keys and tab ports must be unique ({u.key}, {u.tab_port})")
            keys.add(u.key)
            ports.add(u.tab_port)
        if self.kubeconfig and not _FILE_RE.match(self.kubeconfig):
            raise ProfileError(f"{where}: kubeconfig is a file name under ~/.rodeo, got {self.kubeconfig!r}")
        if self.student_ssh_key and not self.student_ssh_key.startswith("/"):
            raise ProfileError(f"{where}: student_ssh_key must be an absolute path")
        for var, secret in self.variables.items():
            if not _VAR_RE.match(var) or not _FILE_RE.match(secret):
                raise ProfileError(f"{where}: variables maps TRACK_VAR: secrets_key, got {var!r}: {secret!r}")


def _secret_ref(name: str, var: str, value: Any) -> str:
    """``??rancher_admin_password`` -> ``rancher_admin_password``."""
    text = str(value or "")
    if not text.startswith("??"):
        raise ProfileError(f"{name}/{FILE}: {var} must be ??<secrets.yaml key>, "
                           "never the value itself")
    return text[2:]


def parse(name: str, raw: Any) -> PlayerProfile:
    if not isinstance(raw, dict):
        raise ProfileError(f"{name}/{FILE} must be a mapping")
    uis = []
    for u in raw.get("uis") or []:
        if not isinstance(u, dict):
            raise ProfileError(f"{name}/{FILE}: each UI is a mapping")
        try:
            port = int(u.get("tab_port"))
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"{name}/{FILE}: tab_port must be a number") from exc
        uis.append(PlayerUI(str(u.get("key") or ""), str(u.get("label") or u.get("key") or ""),
                            str(u.get("upstream") or ""), port, str(u.get("scheme") or "https")))
    creds = []
    for i, c in enumerate(raw.get("credentials") or []):
        if not isinstance(c, dict):
            raise ProfileError(f"{name}/{FILE}: each credential is a mapping")
        creds.append(PlayerCredential(str(c.get("label") or ""), str(c.get("user") or ""),
                                      _secret_ref(name, f"credentials[{i}].password", c.get("password"))))
    variables = raw.get("variables") or {}
    if not isinstance(variables, dict):
        raise ProfileError(f"{name}/{FILE}: variables must be a mapping")
    profile = PlayerProfile(
        name=name,
        title=str(raw.get("title") or ""),
        track_repo=str(raw.get("track_repo") or ""),
        uis=tuple(uis),
        kubeconfig=str(raw.get("kubeconfig") or ""),
        student_ssh_key=str(raw.get("student_ssh_key") or ""),
        variables={str(k): _secret_ref(name, f"variables.{k}", v) for k, v in variables.items()},
        guide_url=str(raw.get("guide_url") or ""),
        credentials=tuple(creds),
        student_libvirt=bool(raw.get("student_libvirt", False)),
        student_vm_access=tuple(str(x) for x in (raw.get("student_vm_access") or [])),
    )
    profile.validate()
    return profile


def has_profile(name: str | None, examples: Path = EXAMPLES) -> bool:
    return bool(name) and (examples / str(name) / FILE).is_file()


def load(name: str | None = None, examples: Path = EXAMPLES) -> PlayerProfile:
    """The player profile of lab profile *name* (virt-rodeo for None, the
    hosts set up before profiles existed). A named profile without a
    player.yaml is an error: it never gets another product's UIs."""
    import yaml

    if name and not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", name):
        raise ProfileError(f"not a profile name: {name!r}")
    if name and not has_profile(name, examples):
        raise ProfileError(f"lab profile {name!r} has no {FILE}: the student UI does not know "
                           "what to show for it")
    chosen = name or DEFAULT_PROFILE
    path = examples / chosen / FILE
    return parse(chosen, yaml.safe_load(path.read_text()))
