"""Load and merge rodeo-plan.yaml + ~/.rodeo/secrets.yaml.

Plan files may use Jinja templating with a `parameters:` block:

    parameters:
      memory: 16384
    resources:
      harvester:
        memory_mib: {{ memory }}

Value precedence: base defaults < profile defaults < plan file
< --paramfile (deep-merged like terraform tfvars) < -P key=value
(dotted paths, e.g. -P resources.harvester.memory_mib=20480).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """User-facing configuration problem (bad YAML, bad values, bad params)."""


# Base defaults that apply to every rodeo type.
# Profile-specific defaults (vms, resources, versions) are merged from the profile.
_BASE_DEFAULTS: dict[str, Any] = {
    "type": "suse-virt",
    "name": "suse-virt-rodeo",
    "deployment_target": "baremetal",  # instruqt | baremetal
    "network": {
        "mode": "nat",
        "vip": "192.168.122.10",
        "rancher_ip": "192.168.122.9",
        "gateway": "192.168.122.1",
        "dns_domain": "aerogrid.com",
    },
    "storage": {"image_dir": "/var/lib/libvirt/images"},
    "libvirt": {"uri": "qemu:///system"},
    "ansible": {
        "path": None,
        "inventory": "deployer/inventory.local",
    },
    "credentials": {},
}

_SECRETS_PATH = Path.home() / ".rodeo" / "secrets.yaml"

# Markers that identify a lab directory, so commands can be run from anywhere
# inside it without passing --config-dir (like git/terraform finding their root).
_LAB_MARKERS = ("rodeo-plan.yaml", "definition.yaml")


def _invoking_home() -> Path:
    """Real user's home even under plain ``sudo`` (uses SUDO_USER)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    return Path.home()


def _resolve_secrets_path() -> Path:
    """Secrets file to read, resolved live so HOME/SUDO_USER are honored.

    Order: the invoking user's ~/.rodeo/secrets.yaml (correct under plain ``sudo``,
    where HOME is /root but SUDO_USER points back at the user), then the module
    constant (which tests monkeypatch). Lets ``rodeo up`` write secrets as the user
    and have the escalated deploy still find them — no ``sudo -E`` needed.
    """
    live = _invoking_home() / ".rodeo" / "secrets.yaml"
    if live.exists():
        return live
    if _SECRETS_PATH.exists():
        return _SECRETS_PATH
    return live


def find_lab_dir(start: str | Path | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) for a lab dir, or None.

    A lab dir contains rodeo-plan.yaml or definition.yaml. Lets the user run
    `rodeo deploy` / `plan` / `up` from anywhere inside their lab.
    """
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if any((d / m).exists() for m in _LAB_MARKERS):
            return d
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _parse_param(entry: str) -> tuple[str, Any]:
    """Parse one -P KEY=VALUE entry; VALUE is YAML-coerced (16 -> int, true -> bool)."""
    if "=" not in entry:
        raise ConfigError(f"-P expects KEY=VALUE, got '{entry}'")
    key, _, raw = entry.partition("=")
    key = key.strip()
    if not key:
        raise ConfigError(f"-P expects KEY=VALUE, got '{entry}'")
    try:
        value = yaml.safe_load(raw) if raw != "" else ""
    except yaml.YAMLError:
        value = raw
    return key, value


def _set_path(cfg: dict, dotted: str, value: Any) -> None:
    """Set cfg['a']['b']['c'] = value for dotted 'a.b.c', creating dicts as needed."""
    node = cfg
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _extract_parameters_block(text: str) -> dict:
    """Parse the top-level `parameters:` block from raw plan text.

    Done textually because the rest of the file may contain Jinja that
    breaks a plain YAML parse. Parameter defaults themselves must be
    literal YAML (no templating).
    """
    match = re.search(r"^parameters:[ \t]*\n((?:[ \t]+.*\n?|\n)*)", text, re.M)
    if not match:
        return {}
    try:
        block = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in parameters block: {exc}")
    if not isinstance(block, dict):
        raise ConfigError("The parameters block must be a mapping")
    return block


def _render_plan(text: str, source: str, context: dict) -> dict:
    """Jinja-render plan text (when templated) and YAML-parse it."""
    if "{{" in text or "{%" in text:
        try:
            import jinja2
            env = jinja2.Environment(undefined=jinja2.StrictUndefined)
            text = env.from_string(text).render(**context)
        except jinja2.UndefinedError as exc:
            raise ConfigError(
                f"{source}: undefined template parameter: {exc.message}\n"
                "Define it in the parameters block, --paramfile, or -P key=value."
            )
        except jinja2.TemplateSyntaxError as exc:
            raise ConfigError(f"{source} line {exc.lineno}: template syntax error: {exc.message}")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: invalid YAML: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: top level must be a mapping")
    data.pop("parameters", None)
    return data


def _resolve_secret_value(value: str, secrets: dict) -> str:
    """Resolve one ??placeholder. On failure the literal is kept so
    validate_config() fails closed with a clear message.

    Supported forms:
      ??key                  -> ~/.rodeo/secrets.yaml lookup
      ??env:NAME             -> environment variable
      ??file:/path           -> first line of a file (e.g. a mounted secret)
      ??cmd:some command     -> stdout of a shell command (pass, op, vault...)
    """
    spec = value[2:]
    if spec.startswith("env:"):
        return os.environ.get(spec[4:]) or value
    if spec.startswith("file:"):
        try:
            content = Path(spec[5:]).read_text().strip()
            return content or value
        except OSError:
            return value
    if spec.startswith("cmd:"):
        try:
            r = subprocess.run(
                spec[4:], shell=True, capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return value
    return secrets.get(spec, value)


def _resolve_secrets(cfg: dict, secrets: dict) -> dict:
    """Replace ??placeholders throughout the config."""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("??"):
            return _resolve_secret_value(obj, secrets)
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj
    return _walk(cfg)


def load_config(
    plan_path: str | Path = "rodeo-plan.yaml",
    params: tuple[str, ...] = (),
    paramfile: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict:
    """Load config, with optional --config-dir for definition + artifacts (EIB model).

    If config_dir provided and contains rodeo-plan.yaml, and plan_path is default,
    the dir's plan is used. definition.yaml in dir (if present) is preferred by inventory.
    """
    plan_path = Path(plan_path)

    # Auto-detect the lab dir when neither an explicit plan nor --config-dir was
    # given, so commands work from anywhere inside a lab without --config-dir .
    if config_dir is None and plan_path == Path("rodeo-plan.yaml") and not plan_path.exists():
        detected = find_lab_dir()
        if detected is not None:
            config_dir = str(detected)

    if config_dir is not None:
        cdir = Path(config_dir)
        if (plan_path == Path("rodeo-plan.yaml") or not plan_path.exists()):
            candidate = cdir / "rodeo-plan.yaml"
            if candidate.exists():
                plan_path = candidate

    paramfile_data: dict = {}
    if paramfile is not None:
        pf = Path(paramfile)
        if not pf.exists():
            raise ConfigError(f"Param file not found: {pf}")
        try:
            paramfile_data = yaml.safe_load(pf.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{pf}: invalid YAML: {exc}")
        if not isinstance(paramfile_data, dict):
            raise ConfigError(f"{pf}: top level must be a mapping")

    cli_params = [_parse_param(p) for p in params]

    plan: dict = {}
    if plan_path.exists():
        text = plan_path.read_text()
        # Template context: parameters block < paramfile scalars < -P entries
        context = _extract_parameters_block(text)
        context.update(
            {k: v for k, v in paramfile_data.items() if not isinstance(v, dict)}
        )
        context.update({k: v for k, v in cli_params if "." not in k})
        plan = _render_plan(text, str(plan_path), context)

    # Determine type early so profile defaults can be merged before plan overrides.
    type_name = plan.get("type", _BASE_DEFAULTS["type"])
    try:
        from .profiles import get_profile
        profile_defaults = get_profile(type_name).default_cfg(config_dir=config_dir)
    except (ImportError, ValueError):
        profile_defaults = {}

    cfg = _deep_merge(_BASE_DEFAULTS, profile_defaults)
    cfg = _deep_merge(cfg, plan)
    cfg = _deep_merge(cfg, paramfile_data)
    for key, value in cli_params:
        _set_path(cfg, key, value)

    secrets: dict = {}
    secrets_path = _resolve_secrets_path()
    if secrets_path.exists():
        try:
            secrets = yaml.safe_load(secrets_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{secrets_path}: invalid YAML: {exc}")

    cfg = _resolve_secrets(cfg, secrets)

    env_path = os.environ.get("RODEO_ANSIBLE_PATH")
    if env_path:
        cfg["ansible"]["path"] = env_path

    if config_dir is not None:
        cfg["config_dir"] = str(Path(config_dir).resolve())

    return cfg


def validate_config(cfg: dict) -> None:
    """Raise ConfigError on unresolved secrets, empty credentials, or bad values."""
    creds = cfg.get("credentials", {})
    unresolved = [k for k, v in creds.items() if isinstance(v, str) and v.startswith("??")]
    if unresolved:
        raise ConfigError(
            f"Secrets not resolved: {', '.join(unresolved)}\n"
            "For ??key: edit ~/.rodeo/secrets.yaml or run: rodeo init\n"
            "For ??env:/??file:/??cmd:: the source returned nothing — "
            "check the variable, file, or command."
        )
    empty = [
        k for k, v in creds.items()
        if v is None or (isinstance(v, str) and (not v.strip() or v == "CHANGE_ME"))
    ]
    if empty:
        raise ConfigError(
            f"Credentials are empty: {', '.join(empty)}\n"
            "An empty password would be baked into the VM install config.\n"
            "Set values in rodeo-plan.yaml (??key) and ~/.rodeo/secrets.yaml, or run: rodeo init"
        )
    target = cfg.get("deployment_target", "baremetal")
    if target not in ("instruqt", "baremetal"):
        raise ConfigError(
            f"Invalid deployment_target '{target}' — use 'instruqt' or 'baremetal'."
        )

    # Resource sanity — catch -P typos before they fail deep inside libvirt.
    for flavor, spec in cfg.get("resources", {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"resources.{flavor} must be a mapping")
        for field in ("memory_mib", "vcpu", "disk_gb"):
            value = spec.get(field)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ConfigError(
                    f"resources.{flavor}.{field} must be a positive integer, got {value!r}"
                )

    # Network consistency — a VIP colliding with a node IP only surfaces
    # 40+ minutes into the deploy as a kube-vip failure.
    net = cfg.get("network", {})
    vms = cfg.get("vms", {})
    node_ips = {
        name: spec.get("ip")
        for name, spec in vms.items()
        if isinstance(spec, dict) and spec.get("ip")
    }
    vip = net.get("vip")
    if vip and vip in node_ips.values():
        owner = next(n for n, ip in node_ips.items() if ip == vip)
        raise ConfigError(
            f"network.vip ({vip}) collides with the IP of VM '{owner}'.\n"
            "The VIP must be a free address — kube-vip floats it between nodes."
        )
    rancher_ip = net.get("rancher_ip")
    vm_rancher_ip = node_ips.get("rancher")
    if rancher_ip and vm_rancher_ip and rancher_ip != vm_rancher_ip:
        raise ConfigError(
            f"network.rancher_ip ({rancher_ip}) does not match "
            f"vms.rancher.ip ({vm_rancher_ip}) — update both together."
        )


_BUNDLED_DATA = Path(__file__).parent / "data"


def find_ansible_root(cfg: dict) -> Path | None:
    """Return the directory containing ansible/playbook.yml and deployer/.

    Search order:
      1. cfg['ansible']['path'] or RODEO_ANSIBLE_PATH env
      2. Bundled data shipped with rodeo-cli
      3. Current working directory
      4. ~/instruqt-virtualization (dev checkout)
    """
    candidates = [
        cfg["ansible"].get("path"),
        os.environ.get("RODEO_ANSIBLE_PATH"),
        str(_BUNDLED_DATA),
        ".",
        str(Path.home() / "instruqt-virtualization"),
    ]
    for c in candidates:
        if c is None:
            continue
        p = Path(c)
        if (p / "ansible" / "playbook.yml").exists():
            return p
    return None
