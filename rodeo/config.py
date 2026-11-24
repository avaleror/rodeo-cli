"""Load and merge rodeo-plan.yaml + ~/.rodeo/secrets.yaml.

Plan files may use Jinja templating with a `parameters:` block:

    parameters:
      memory: 16384
    resources:
      harvester:
        memory_mib: {{ memory }}

Value precedence: base defaults < profile defaults < plan file
< --paramfile (deep-merged override file) < -P key=value
(dotted paths, e.g. -P resources.harvester.memory_mib=20480).
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import ipaddress
import os
import re
import stat
import struct
import subprocess
import warnings
from pathlib import Path
from typing import Any, Literal

import yaml

from .paths import rodeo_last_lab_file, rodeo_secrets_path, rodeo_trusted_labs_path
from .redact import register_secret, register_secrets


class ConfigError(ValueError):
    """User-facing configuration problem (bad YAML, bad values, bad params)."""


class UntrustedLab(ConfigError):
    """A write command met a lab that `rodeo trust` has not approved as it is now."""


# Base defaults that apply to every rodeo type.
# Profile-specific defaults (vms, resources, versions) are merged from the profile.
_BASE_DEFAULTS: dict[str, Any] = {
    "type": "suse-virt",
    "name": "suse-virt-rodeo",
    "deployment_target": "baremetal",  # instruqt | baremetal | aws | remote
    "network": {
        "mode": "nat",
        "vip": "192.168.122.10",
        "rancher_ip": "192.168.122.9",
        "gateway": "192.168.122.1",
        "dns_domain": "rodeo.lab",
    },
    "storage": {"image_dir": "/var/lib/libvirt/images"},
    # disk_cache / disk_io: unset by default — DeployRunner picks
    # none/native (baremetal) or writeback/threads (instruqt). Override
    # via plan or -P libvirt.disk_cache=... / libvirt.disk_io=...
    "libvirt": {"uri": "qemu:///system"},
    "ansible": {
        "path": None,
        "inventory": "deployer/inventory.local",
    },
    "credentials": {},
}

# Markers that identify a lab directory, so commands can be run from anywhere
# inside it without passing --config-dir (auto-detected from the working directory).
_LAB_MARKERS = ("rodeo-plan.yaml", "definition.yaml")
# Per-lab secrets (0600, next to rodeo-plan.yaml) — override ~/.rodeo/secrets.yaml.
LAB_SECRETS_FILE = ".rodeo-secrets.yaml"


def _resolve_secrets_path() -> Path:
    """Secrets file to read, resolved live so HOME/SUDO_USER are honored."""
    return rodeo_secrets_path()


def _record_lab_dir(lab_dir: Path) -> None:
    """Persist the last successfully used lab dir so commands work from anywhere."""
    try:
        last_lab = rodeo_last_lab_file()
        last_lab.parent.mkdir(parents=True, exist_ok=True)
        last_lab.write_text(str(lab_dir))
    except OSError:
        pass


def find_lab_dir(start: str | Path | None = None, *, use_last_lab: bool = True) -> Path | None:
    """Walk up from ``start`` (default cwd) for a lab dir, or None.

    A lab dir contains rodeo-plan.yaml or definition.yaml. Lets the user run
    `rodeo deploy` / `plan` / `up` from anywhere inside their lab.

    Falls back to ~/.rodeo/last_lab when the walk-up finds nothing, so commands
    like `rodeo ssh eib` work from any directory after the first deploy.
    ``use_last_lab=False`` skips that fallback (`rodeo up --profile NAME`).
    """
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if any((d / m).exists() for m in _LAB_MARKERS):
            return d
    if not use_last_lab:
        return None
    # Fall back to the last successfully loaded lab dir.
    try:
        last = Path(rodeo_last_lab_file().read_text().strip())
        if last.is_dir() and any((last / m).exists() for m in _LAB_MARKERS):
            return last
    except OSError:
        pass
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


def sandboxed_jinja_env():
    """The one Jinja environment for text from a lab, plan or workshop repo.

    Sandboxed, so a template can't reach Python internals
    ({{ ''.__class__.__mro__ ... }}); the sandbox's range() also raises
    OverflowError above jinja2.sandbox.MAX_RANGE. tests/test_config.py fails
    if a plain jinja2.Environment comes back anywhere in rodeo/.
    """
    import jinja2
    from jinja2.sandbox import SandboxedEnvironment

    return SandboxedEnvironment(undefined=jinja2.StrictUndefined)


def _render_plan(text: str, source: str, context: dict) -> dict:
    """Jinja-render plan text (when templated) and YAML-parse it."""
    if "{{" in text or "{%" in text:
        try:
            import jinja2

            text = sandboxed_jinja_env().from_string(text).render(**context)
        except jinja2.UndefinedError as exc:
            raise ConfigError(
                f"{source}: undefined template parameter: {exc.message}\n"
                "Define it in the parameters block, --paramfile, or -P key=value."
            )
        except jinja2.exceptions.SecurityError as exc:
            raise ConfigError(f"{source}: template blocked by the sandbox: {exc}")
        except jinja2.TemplateSyntaxError as exc:
            raise ConfigError(f"{source} line {exc.lineno}: template syntax error: {exc.message}")
        except OverflowError as exc:
            raise ConfigError(f"{source}: template range too large: {exc}")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: invalid YAML: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: top level must be a mapping")
    data.pop("parameters", None)
    return data


# --- Secrets and lab trust ------------------------------------------------------
#
# load_config(secrets=MODE) picks which ??placeholders resolve:
#   "off"  : nothing (the default; read commands and day-2 power commands).
#   "keys" : ??key only, from the lab's .rodeo-secrets.yaml, plus the global
#            ~/.rodeo/secrets.yaml when the lab is trusted (deploy --check,
#            story render).
#   "all"  : every form (??key, ??env:, ??file:, ??cmd:), and only in a trusted
#            lab; an untrusted lab is a ConfigError (write commands).
#
# Trust is per lab, not per string: a lab already runs its own code as root on
# deploy (custom/scripts, content repo scripts), so approving one ??cmd: string
# protects little. `rodeo trust` records a sha256 manifest of the lab directory
# (everything but the state rodeo writes there); any change makes the lab
# untrusted again. A lab is trusted at creation only when rodeo wrote every file
# in it from a bundled (or already trusted) profile. -P and --paramfile may only
# set allowlisted data keys with checked values, never `??` or templates.
SecretsMode = Literal["off", "keys", "all"]
_SECRETS_MODES = ("off", "keys", "all")
_SOURCE_PREFIXES = ("??env:", "??cmd:", "??file:")
# Set by load_config after -P/--paramfile are merged (so it can't be forged).
LAB_TRUSTED_KEY = "_lab_trusted"
UNTRUSTED_LAB = (
    "This lab is not trusted: review it and run rodeo trust\n"
    "(the lab directory is new, or a file in it changed since the last approval)."
)


def is_secret_source(value: Any) -> bool:
    """??env:, ??cmd: and ??file: (everything but a plain ??key)."""
    return isinstance(value, str) and value.startswith(_SOURCE_PREFIXES)


def collect_placeholders(cfg: Any) -> set[str]:
    """Every string starting with ?? anywhere in the config."""
    if isinstance(cfg, str):
        return {cfg} if cfg.startswith("??") else set()
    if isinstance(cfg, dict):
        return set().union(*(collect_placeholders(v) for v in cfg.values()))
    if isinstance(cfg, list):
        return set().union(*(collect_placeholders(v) for v in cfg))
    return set()


_INJECTION_MARKERS = ("??", "{{", "{%", "\n", "\r")


def _reject_injected(value: Any, where: str) -> None:
    """-P / --paramfile values may not carry placeholders, templates or newlines.

    They come from the command line or an MCP client; secret sources and Jinja
    belong in rodeo-plan.yaml, which `rodeo trust` covers.
    """
    if isinstance(value, str):
        if any(m in value for m in _INJECTION_MARKERS):
            raise ConfigError(
                f"{where}: values passed with -P or --paramfile may not contain "
                "'??', '{{', '{%' or a newline.\n"
                "Put secret placeholders and templates in rodeo-plan.yaml itself."
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            _reject_injected(k, where)
            _reject_injected(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _reject_injected(v, f"{where}[{i}]")


# Written by rodeo itself into a lab directory, so not part of what is trusted
# (one list; tests/test_config.py, test_content.py, test_runner.py and
# test_story.py check it holds across a deploy, a content apply and a story
# render). Top-level names and prefixes, nested relative paths, and names
# skipped at any depth.
_UNHASHED_TOP = frozenset({
    ".rodeo", ".labinabox", LAB_SECRETS_FILE, ".rodeo-shipped-secrets.yaml",
    "rodeo-secrets.env", "logs", "events",
    "content", "content.lock", "guide",                         # content.py
    "workshop", "workshop-guide", "workshop-guide.pdf",         # labinabox_phase.py
    ".builder-deploy.log",                                      # builder/deploywork.py
    ".claude",  # agent-setup and Claude Code itself; rodeo never runs anything from it
})
_UNHASHED_TOP_PREFIXES = (".content.", ".workshop.")  # temporary checkouts
_UNHASHED_NESTED = frozenset({"story/strings"})  # rmstory's translation store (story.py)
_UNHASHED_ANY = frozenset({".git", "__pycache__"})


def _unhashed(rel: str) -> bool:
    """True for a lab-relative path (or anything under it) that trust skips."""
    parts = rel.split("/")
    if parts[0] in _UNHASHED_TOP or parts[0].startswith(_UNHASHED_TOP_PREFIXES):
        return True
    if len(parts) == 1 and parts[0].endswith(".log"):
        return True
    if any(p in _UNHASHED_ANY for p in parts):
        return True
    return any(rel == n or rel.startswith(n + "/") for n in _UNHASHED_NESTED)


def _walk_lab(lab: Path) -> tuple[list[Path], list[Path]]:
    """(regular files, symlinks) in the lab that trust covers, each sorted."""
    files: list[Path] = []
    links: list[Path] = []
    for root, dirs, names in os.walk(lab):
        here = Path(root)
        keep = []
        for d in dirs:
            p = here / d
            if _unhashed(str(p.relative_to(lab))):
                continue
            (links if p.is_symlink() else keep).append(p if p.is_symlink() else d)
        dirs[:] = sorted(keep)
        for f in names:
            p = here / f
            if _unhashed(str(p.relative_to(lab))):
                continue
            (links if p.is_symlink() else files).append(p)
    return sorted(files), sorted(links)


def _hashed_files(lab: Path) -> list[Path]:
    files, links = _walk_lab(lab)
    return sorted(files + links)


def lab_problems(lab_dir: str | Path) -> list[str]:
    """Why a lab can't be trusted as it is: symlinks that leave it or point into
    the paths rodeo writes (their content would escape the hash)."""
    lab = Path(os.path.realpath(lab_dir))
    problems = []
    files, links = _walk_lab(lab)
    for path in files:
        if not stat.S_ISREG(path.lstat().st_mode):
            problems.append(f"{path.relative_to(lab)} is not a regular file (FIFO, socket or device)")
    for link in links:
        rel = str(link.relative_to(lab))
        target = Path(os.path.realpath(link))
        try:
            trel = str(target.relative_to(lab))
        except ValueError:
            problems.append(f"{rel} is a symlink to {target}, outside the lab")
            continue
        if _unhashed(trel):
            problems.append(f"{rel} is a symlink into {trel}, which trust does not cover")
        elif not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
            # A directory link (custom/scripts -> ..) would let later, unhashed
            # files such as *.log run as custom scripts.
            problems.append(f"{rel} is a symlink to a directory or special file: only links "
                            "to regular files inside the lab are allowed")
    # The paths rodeo writes are left out of the hash, so a symlink there (say
    # content.lock -> /etc/something) is invisible to it but redirects a root
    # write. No symlink at all is allowed in them.
    for root, dirs, names in os.walk(lab):
        here = Path(root)
        for name in dirs + names:
            p = here / name
            rel = str(p.relative_to(lab))
            if p.is_symlink() and _unhashed(rel) and not rel.startswith(".git/") and "/.git/" not in rel:
                problems.append(f"{rel} is a symlink in a path rodeo writes: remove it")
    return problems


def _record(h: Any, rel: str, kind: bytes, content: bytes) -> None:
    """One manifest entry with explicit lengths, so no file can fake another's."""
    path = rel.encode()
    h.update(struct.pack(">Q", len(path)) + path + kind
             + struct.pack(">Q", len(content)) + hashlib.sha256(content).digest())


def lab_fingerprint(lab_dir: str | Path) -> str:
    """sha256 over a manifest of the lab directory: every file except the state
    rodeo writes (rodeo-plan.yaml, definition.yaml, custom/, manifests/, helm/,
    certs/, story/, per-host manifests...).

    Each entry is (len(path), path, type, len(content), sha256(content)). A
    symlink records its target path inside the lab, plus the target's content
    when it is a file; lab_problems() rejects links that leave the lab. Content
    repos fetched by URL are covered only through the URL and ref in the plan.
    """
    lab = Path(os.path.realpath(lab_dir))
    h = hashlib.sha256()
    files, links = _walk_lab(lab)
    # Never open a FIFO or device (it would hang or read forever): such a lab
    # has lab_problems() and can't be trusted anyway.
    files = [p for p in files if stat.S_ISREG(p.lstat().st_mode)]
    for path in sorted(files + links):
        rel = str(path.relative_to(lab))
        if path.is_symlink():
            target = Path(os.path.realpath(path))
            try:
                trel = str(target.relative_to(lab))
            except ValueError:
                trel = str(target)
            content = target.read_bytes() if target.is_file() and stat.S_ISREG(target.stat().st_mode) else b""
            _record(h, rel, b"L", trel.encode() + b"\0" + hashlib.sha256(content).digest())
        else:
            kind = b"X" if path.lstat().st_mode & 0o111 else b"F"  # chmod +x untrusts
            _record(h, rel, kind, path.read_bytes())
    return h.hexdigest()


def has_hashed_files(lab_dir: str | Path) -> bool:
    """True when the directory already holds anything trust would cover."""
    lab = Path(lab_dir)
    return lab.is_dir() and bool(_hashed_files(lab))


# --- -P / --paramfile ------------------------------------------------------------
# The only keys -P and --paramfile may set (an MCP client passes them), each with
# a value check: allowlisted values still reach Ansible shell tasks
# (versions.harvester ends up in a download URL and a file name). Anything else
# (ansible paths, repos, refs, checksums, scripts, story engines, SSH keys, lab
# directories...) belongs in rodeo-plan.yaml, which `rodeo trust` covers.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_IP_CHARS_RE = re.compile(r"^[0-9A-Fa-f:.]+(/[0-9]{1,3})?$")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_PARAM_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]*$")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _pos_int(v: Any) -> bool:
    return _is_int(v) and v > 0


_MAX_TTL_HOURS = 168  # the dead-man switch cap (providers/deadman.py)


def _ttl_hours(v: Any) -> bool:
    """A finite TTL in (0, 168]. Not "off" and not .inf: an override must never
    disable the dead-man switch."""
    import math

    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) \
        and 0 < v <= _MAX_TTL_HOURS


def _matches(rx: re.Pattern) -> Any:
    return lambda v: (isinstance(v, str) or _is_int(v) or isinstance(v, float)) \
        and not isinstance(v, bool) and str(v).isascii() and bool(rx.fullmatch(str(v)))


def _version_value(v: Any) -> bool:
    return _matches(_VERSION_RE)(v)


def _net_value(v: Any) -> bool:
    """An IP, a CIDR or a host name, written canonically. No IPv6 scope id:
    ipaddress accepts "fe80::1%<anything>", which then lands in a shell script."""
    return _canonical_net(v) is not None


def _canonical_net(v: Any) -> str | None:
    """The canonical form of an IP, CIDR or host name, or None when it is not one.
    IPv6 compares case-insensitively; an all-hex word (db, cafe) is a host name."""
    if not isinstance(v, str) or not v.isascii():
        return None
    if _IP_CHARS_RE.fullmatch(v):
        low = v.lower()
        for parse in (ipaddress.ip_address, ipaddress.ip_interface):
            try:
                canon = str(parse(v))
            except ValueError:
                continue
            if canon == low:
                return canon
        try:
            canon = str(ipaddress.ip_network(v, strict=False))
            if canon == low:
                return canon
        except ValueError:
            pass
    if re.fullmatch(r"[0-9.]+", v):
        return None  # looks like an IPv4 address but isn't a canonical one
    return v if _HOSTNAME_RE.fullmatch(v) else None


_FACT_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9 .,:!?()/@+=_-]{0,254}[A-Za-z0-9.!?)])?$")
_FACT_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/%+-]*)?(\?[A-Za-z0-9._~=%+-]*)?$")


def _fact_value(v: Any) -> bool:
    """content.facts only fill [[ Instruqt-Var ]] tags in the rendered guide
    (content.render_text): never a shell. Plain sentences or http(s) URLs; no
    quotes, `$`, backticks, `;`, `|`, `&`, `<`, `>`, `%` (outside a URL) or `\\`."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return False
    text = str(v)
    if not text.isascii() or len(text) > 256:
        return False
    if text.startswith(("http://", "https://")):
        from urllib.parse import urlsplit

        parts = urlsplit(text)
        return bool(_FACT_URL_RE.fullmatch(text)) and parts.scheme in ("http", "https") and bool(parts.netloc)
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:[^ ]", text):
        return False  # another URL scheme (javascript:, ftp://, file:)
    return bool(_FACT_RE.fullmatch(text))


# (dotted pattern, value check); "*" is exactly one segment.
PARAM_RULES: tuple[tuple[str, Any], ...] = (
    ("name", _matches(_NAME_RE)),
    ("type", _matches(_NAME_RE)),
    ("deployment_target", _matches(_NAME_RE)),
    ("instance", _is_int),
    ("resources.*.*", _pos_int),
    ("versions.*", _version_value),
    ("network.*", _net_value),
    ("libvirt.disk_cache", _matches(_NAME_RE)),
    ("libvirt.disk_io", _matches(_NAME_RE)),
    ("rancher_tls.email", _matches(_EMAIL_RE)),
    ("elemental.registrations", _is_int),
    ("lab_in_a_box.variant", _matches(_NAME_RE)),
    ("lab_in_a_box.iso_image", _matches(_FILE_RE)),
    ("provider.region", _matches(_NAME_RE)),
    ("provider.instance_type", _matches(_FILE_RE)),
    ("provider.ttl_hours", _ttl_hours),
    ("story.language", _matches(_LANG_RE)),
    ("story.id", _matches(_NAME_RE)),
    ("content.facts.*", _fact_value),
)
PARAM_ALLOWLIST = tuple(p for p, _ in PARAM_RULES)
# Integrity pins and URLs are never data an override may change.
_PARAM_NEVER = re.compile(r"(checksum|sha\d*|digest|url|repo|ref|path)", re.I)


def _override_paths(key: str, value: Any) -> list[tuple[str, Any]]:
    """(leaf path, value) pairs an override sets (a dict sets each of its leaves)."""
    if isinstance(value, dict) and value:
        out: list[tuple[str, Any]] = []
        for k, v in value.items():
            out += _override_paths(f"{key}.{k}", v)
        return out
    return [(key, value)]


def _canonical_override(key: str, value: Any) -> Any:
    """Store network values canonically (2001:DB8::1 -> 2001:db8::1)."""
    if isinstance(value, dict):
        return {k: _canonical_override(f"{key}.{k}", v) for k, v in value.items()}
    if _rule_for(key) is _net_value and isinstance(value, str):
        return _canonical_net(value) or value
    return value


def _rule_for(path: str) -> Any:
    parts = path.split(".")
    for pattern, check in PARAM_RULES:
        pp = pattern.split(".")
        if len(pp) == len(parts) and all(a == "*" or a == b for a, b in zip(pp, parts)):
            return check
    return None


def _template_param_ok(value: Any) -> bool:
    if isinstance(value, bool) or _is_int(value) or isinstance(value, float):
        return True
    return isinstance(value, str) and value.isascii() and bool(_PARAM_SCALAR_RE.fullmatch(value))


def _reject_disallowed_keys(
    overrides: list[tuple[str, Any]], where: str, template_params: set[str]
) -> None:
    for key, value in overrides:
        for path, leaf in _override_paths(str(key), value):
            if path in template_params and _rule_for(path) is None:
                if not _template_param_ok(value):
                    raise ConfigError(
                        f"{where} {path}: a template parameter takes a plain scalar "
                        f"(letters, digits, . _ : @ + -), not {value!r}."
                    )
                continue
            check = _rule_for(path)
            never = path.startswith("versions.") and _PARAM_NEVER.search(path.split(".")[-1])
            if check is None or never:
                params = f" and this plan's parameters ({', '.join(sorted(template_params))})" \
                    if template_params else ""
                raise ConfigError(
                    f"{where} {path}: -P and --paramfile can only set "
                    f"{', '.join(PARAM_ALLOWLIST)}{params}.\n"
                    "Set anything else in rodeo-plan.yaml (rodeo trust covers it)."
                )
            if not check(leaf):
                raise ConfigError(f"{where} {path}: {leaf!r} is not a valid value for this key.")


# --- libvirt URIs ------------------------------------------------------------------
# A libvirt URI can run a command (qemu+ext://...?command=, ?netcat=, ?socket=),
# and status/plan/clean connect with it, so every URI from a plan, an override or
# the command line is checked before rodeo opens it.
_LIBVIRT_SCHEMES = ("qemu", "qemu+ssh", "qemu+tls", "qemu+tcp")
_LIBVIRT_NETLOC_RE = re.compile(
    r"^(?P<user>[A-Za-z_][A-Za-z0-9._-]{0,31}@)?"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]"
    r"|(?=[^:]{1,253}(:|$))[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)"
    r"(?P<port>:[0-9]{1,5})?$"
)
_LIBVIRT_URI_RE = re.compile(r"^(qemu|qemu\+ssh|qemu\+tls|qemu\+tcp)://([^/?#]*)(/system|/session)$")


def validate_libvirt_uri(uri: Any, where: str = "libvirt.uri") -> str:
    """No query string or fragment at all (libvirt also splits parameters on ';'
    and reads command=, netcat=, socket=, keyfile=, known_hosts=...), a plain
    [user@]host[:port], and exactly /system or /session."""
    text = str(uri or "")
    m = _LIBVIRT_URI_RE.fullmatch(text) if text.isascii() else None
    netloc = m.group(2) if m else ""
    n = _LIBVIRT_NETLOC_RE.fullmatch(netloc) if m and m.group(1) != "qemu" else None
    ok = bool(m) and ((m.group(1) == "qemu" and netloc == "") or bool(n))
    if ok and n:
        host = n.group("host")
        if host.startswith("["):
            try:
                ok = ipaddress.ip_address(host[1:-1]).version == 6
            except ValueError:
                ok = False
        port = n.group("port")
        if port and not 0 < int(port[1:]) <= 65535:
            ok = False
    if not ok:
        raise ConfigError(
            f"{where} {text!r} is not allowed: use qemu:///system, qemu:///session or "
            "qemu+ssh://user@host[:port]/system, with no query parameters"
        )
    return text


def _nofollow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _parse_trusted_labs(raw: bytes, path: Path) -> dict[str, str]:
    try:
        data = yaml.safe_load(raw.decode()) if raw.strip() else {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: corrupt trust file: {exc}")
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ConfigError(
            f"{path}: corrupt trust file (expected lab directory: sha256 entries).\n"
            "Delete it and run rodeo trust in each lab you use."
        )
    return data


def load_trusted_labs() -> dict[str, str]:
    """{lab realpath: sha256}. Missing file: empty. Corrupt file: ConfigError."""
    path = rodeo_trusted_labs_path()
    try:
        fd = os.open(path, os.O_RDONLY | _nofollow())
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read trust file: {exc}")
    with os.fdopen(fd, "rb") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        raw = fh.read()
    return _parse_trusted_labs(raw, path)


def _update_trusted_labs(lab_dir: str | Path, digest: str | None) -> Path:
    """Set (or with None remove) one lab's entry."""
    return _update_trust_entry(os.path.realpath(lab_dir), digest)


def _update_trust_entry(key: str, value: str | None) -> Path:
    """Set (or with None remove) one entry: read-modify-write under flock."""
    path = rodeo_trusted_labs_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | _nofollow(), 0o600)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot open trust file: {exc}")
    with os.fdopen(fd, "r+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        os.fchmod(fh.fileno(), 0o600)
        data = _parse_trusted_labs(fh.read(), path)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        fh.seek(0)
        fh.truncate()
        fh.write(yaml.safe_dump(dict(sorted(data.items())), default_flow_style=False).encode())
        fh.flush()
        os.fsync(fh.fileno())
    # Written as root after a sudo re-exec: hand it back to the invoking user.
    from .paths import fix_invoking_ownership

    fix_invoking_ownership()
    return path


# A fleet lab.source is a git URL each host clones: there is no local lab to
# hash, so the operator approves the URL and branch once on the laptop.
SOURCE_PREFIX = "source:"


def source_key(url: str, ref: str | None) -> str:
    return f"{SOURCE_PREFIX}{url}@{ref or ''}"


def trust_source(url: str, ref: str | None) -> Path:
    """Approve a git lab source (by URL and branch, not commit)."""
    return _update_trust_entry(source_key(url, ref), "approved")


def is_source_trusted(url: str, ref: str | None) -> bool:
    return load_trusted_labs().get(source_key(url, ref)) == "approved"


def trust_lab(lab_dir: str | Path, digest: str | None = None) -> Path:
    """Record the lab as trusted: ``digest`` when given (the one the operator
    reviewed), else its current fingerprint. Refuses a lab with lab_problems()."""
    problems = lab_problems(lab_dir)
    if problems:
        raise UntrustedLab("This lab can't be trusted as it is:\n  " + "\n  ".join(problems))
    return _update_trusted_labs(lab_dir, digest or lab_fingerprint(lab_dir))


def revoke_lab(lab_dir: str | Path) -> bool:
    """Forget the lab's trust. True when it had an entry."""
    had = os.path.realpath(lab_dir) in load_trusted_labs()
    if had:
        _update_trusted_labs(lab_dir, None)
    return had


def is_lab_trusted(lab_dir: str | Path | None) -> bool:
    if lab_dir is None:
        return False
    recorded = load_trusted_labs().get(os.path.realpath(lab_dir))
    return recorded is not None and not lab_problems(lab_dir) and recorded == lab_fingerprint(lab_dir)


@contextlib.contextmanager
def keep_trust(lab_dir: str | Path | None):
    """Wrap rodeo's own edits of a lab's plan: a trusted lab stays trusted."""
    trusted = False
    try:
        trusted = lab_dir is not None and is_lab_trusted(lab_dir)
    except ConfigError:
        pass
    yield
    if trusted:
        trust_lab(lab_dir)


def _lab_layout(plan_path: Path, config_dir: str | Path | None) -> tuple[str | None, str | None]:
    """(lab realpath trust is recorded against, problem that blocks trust or None).

    Everything is compared as realpaths, so a symlinked plan can't borrow the
    trust of the directory it points into.
    """
    if config_dir is not None:
        lab = os.path.realpath(config_dir)
    elif plan_path.exists():
        lab = os.path.realpath(plan_path.parent)
    else:
        return None, None
    if not plan_path.exists():
        return lab, None
    if plan_path.is_symlink():
        return lab, f"{plan_path} is a symlink: write commands need the plan file itself in the lab"
    if os.path.dirname(os.path.realpath(plan_path)) != lab:
        return lab, ("--config outside --config-dir: write commands need the plan inside the "
                     "lab directory, so one rodeo trust covers both")
    return lab, None


def lab_dir_of(cfg: dict) -> str | None:
    """The lab directory trust is recorded against (resolved realpath)."""
    lab = cfg.get("config_dir") or cfg.get("plan_dir")
    return os.path.realpath(lab) if lab else None


def _resolve_secret_value(value: str, secrets: dict, mode: SecretsMode) -> Any:
    """Resolve one ??placeholder as far as ``mode`` allows. On failure the
    literal is kept so validate_config() fails closed with a clear message.

    Supported forms:
      ??key                  -> secrets files ("keys" and "all")
      ??env:NAME             -> environment variable ("all")
      ??file:/path           -> file contents ("all")
      ??cmd:some command     -> stdout of a shell command ("all")
    """
    spec = value[2:]
    if not is_secret_source(value):
        return secrets.get(spec, value) if mode in ("keys", "all") else value
    if mode != "all":
        return value
    if spec.startswith("env:"):
        return os.environ.get(spec[4:]) or value
    if spec.startswith("file:"):
        try:
            return Path(spec[5:]).read_text().strip() or value
        except OSError:
            return value
    try:
        # shell=True is deliberate: the lab, plan included, is trusted.
        r = subprocess.run(spec[4:], shell=True, capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return value


def _resolve_secrets(cfg: dict, secrets: dict, mode: SecretsMode) -> dict:
    """Replace ??placeholders throughout the config."""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("??"):
            value = _resolve_secret_value(obj, secrets, mode)
            if value != obj:
                register_secret(value)  # masked in event sinks from now on
            return value
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj
    return _walk(cfg)


def checked_lab_secrets(lab_dir: str | Path) -> Path | None:
    """The lab's own secrets file, or None when it doesn't exist.

    Refused (ConfigError) when it is a symlink, a hard link, not a regular file,
    outside the lab, or the global ~/.rodeo/secrets.yaml itself: any of those
    would hand global secrets to an untrusted lab in "keys" mode, or let
    `rodeo up` append to a file elsewhere (a link to ~/.bashrc).
    """
    path = Path(lab_dir) / LAB_SECRETS_FILE
    if path.is_symlink():
        raise ConfigError(f"{path} is a symlink: refusing it")
    if not path.exists():
        return None
    st = path.lstat()
    bad = (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or os.path.dirname(os.path.realpath(path)) != os.path.realpath(lab_dir)
    )
    glob = _resolve_secrets_path()
    if not bad and glob.exists():
        gst = glob.stat()
        bad = (gst.st_dev, gst.st_ino) == (st.st_dev, st.st_ino)
    if bad:
        raise ConfigError(f"{path} is a hard link, not a regular file, or the global secrets file: refusing it")
    return path


def _load_secrets(lab_dir: Path | None, *, include_global: bool) -> dict:
    """The lab's own secrets file, over ~/.rodeo/secrets.yaml when allowed."""
    secrets: dict = {}
    secrets_path = _resolve_secrets_path()
    if include_global and secrets_path.exists():
        try:
            secrets = yaml.safe_load(secrets_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{secrets_path}: invalid YAML: {exc}")
    # A lab's own secrets (e.g. one set of generated passwords per lab instance)
    # win over the global file; see secretgen.ensure_plan_secrets.
    if lab_dir is not None:
        lab_secrets = checked_lab_secrets(lab_dir)
        if lab_secrets is not None:
            from .labfs import read_lab_file

            # Read with O_NOFOLLOW and a hard-link check on the same fd: no gap
            # between checked_lab_secrets() and the read.
            text = read_lab_file(lab_dir, LAB_SECRETS_FILE) or ""
            try:
                secrets = {**secrets, **(yaml.safe_load(text) or {})}
            except yaml.YAMLError as exc:
                raise ConfigError(f"{lab_secrets}: invalid YAML: {exc}")
    register_secrets(secrets)
    return secrets


def load_config(
    plan_path: str | Path = "rodeo-plan.yaml",
    params: tuple[str, ...] = (),
    paramfile: str | Path | None = None,
    config_dir: str | Path | None = None,
    secrets: SecretsMode = "off",
    resolve_secrets: bool | None = None,
) -> dict:
    """Load config, with optional --config-dir for definition + artifacts (EIB model).

    ``secrets`` picks which ??placeholders resolve (see SecretsMode above). The
    default "off" resolves nothing, so a caller that forgets cannot run a shell
    or read the environment. "all" also requires a trusted lab.
    ``resolve_secrets`` is the deprecated bool form (False -> "off", True -> "all").

    If config_dir provided and contains rodeo-plan.yaml, and plan_path is default,
    the dir's plan is used. definition.yaml in dir (if present) is preferred by inventory.
    """
    if resolve_secrets is not None:
        warnings.warn(
            "load_config(resolve_secrets=...) is deprecated: use secrets='off'|'keys'|'all'",
            DeprecationWarning,
            stacklevel=2,
        )
        secrets = "all" if resolve_secrets else "off"
    if secrets not in _SECRETS_MODES:
        raise ValueError(f"secrets must be one of {_SECRETS_MODES}, got {secrets!r}")
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

    for entry in params:  # raw text too: YAML folds "a\nb" into "a b"
        _reject_injected(entry, f"-P {entry.partition('=')[0]}")
    cli_params = [_parse_param(p) for p in params]
    _reject_injected(paramfile_data, f"--paramfile {paramfile}")
    plan_text = plan_path.read_text() if plan_path.exists() else ""
    template_params = {str(k) for k in _extract_parameters_block(plan_text)}
    _reject_disallowed_keys(cli_params, "-P", template_params)
    _reject_disallowed_keys(list(paramfile_data.items()), "--paramfile", template_params)
    cli_params = [(k, _canonical_override(k, v)) for k, v in cli_params]
    paramfile_data = {k: _canonical_override(str(k), v) for k, v in paramfile_data.items()}

    plan: dict = {}
    if plan_path.exists():
        text = plan_text
        # Template context: parameters block < paramfile scalars < -P entries
        context = _extract_parameters_block(text)
        context.update(
            {k: v for k, v in paramfile_data.items() if not isinstance(v, dict)}
        )
        context.update({k: v for k, v in cli_params if "." not in k})
        plan = _render_plan(text, str(plan_path), context)
        _record_lab_dir(Path(os.path.realpath(plan_path.parent)))

    # A lab dir found directly in cwd (plan_path already existed, so the
    # find_lab_dir()/config_dir auto-detect above never ran) still needs its
    # definition.yaml picked up — otherwise default_cfg() never loads the real
    # node topology and preflight/resource sizing silently falls back to the
    # generic 3-node formula, even for e.g. a 2-node profile.
    if config_dir is None and (plan_path.parent / "definition.yaml").exists():
        config_dir = str(plan_path.parent)

    # Determine type early so profile defaults can be merged before plan overrides.
    type_name = plan.get("type", _BASE_DEFAULTS["type"])
    try:
        from .profiles import get_profile
        profile_defaults = get_profile(type_name).default_cfg(config_dir=config_dir)
    except (ImportError, ValueError):
        profile_defaults = {}

    # Deep copy: _deep_merge shares nested dicts it doesn't override, and
    # finalize_cfg mutates cfg["network"] in place. The old always-on secret
    # walk rebuilt every dict and hid that; with secrets="off" one lab's
    # network would leak into _BASE_DEFAULTS for the rest of the process.
    cfg = _deep_merge(copy.deepcopy(_BASE_DEFAULTS), profile_defaults)
    cfg = _deep_merge(cfg, plan)
    # Names from the plan's parameters: block only feed the template above.
    cfg = _deep_merge(cfg, {k: v for k, v in paramfile_data.items()
                            if not (k in template_params and _rule_for(k) is None)})
    for key, value in cli_params:
        if key in template_params and _rule_for(key) is None:
            continue
        _set_path(cfg, key, value)

    lab_dir = Path(config_dir) if config_dir is not None else (plan_path.parent if plan_path.exists() else None)
    trust_dir, layout = _lab_layout(plan_path, config_dir)
    # None: not computed ("off" hashes nothing); validate_config(check=True) does it.
    trusted: bool | None = None
    if secrets != "off":
        trusted = trust_dir is not None and layout is None and is_lab_trusted(trust_dir)
        if secrets == "all" and trust_dir is not None and not trusted:
            problems = [layout] if layout else lab_problems(trust_dir)
            detail = ("\n  " + "\n  ".join(problems)) if problems else ""
            raise UntrustedLab(f"{UNTRUSTED_LAB}\nLab: {trust_dir}{detail}")
        cfg = _resolve_secrets(cfg, _load_secrets(lab_dir, include_global=trusted), secrets)
    cfg[LAB_TRUSTED_KEY] = trusted
    cfg["_lab_split"] = layout is not None

    env_path = os.environ.get("RODEO_ANSIBLE_PATH")
    if env_path:
        cfg["ansible"]["path"] = env_path

    # Derived here only: a plan, -P or --paramfile value for these would point a
    # trusted lab at another directory's scripts and manifests.
    cfg.pop("config_dir", None)
    cfg.pop("plan_dir", None)
    if config_dir is not None:
        cfg["config_dir"] = str(Path(config_dir).resolve())
    if plan_path.exists():
        # The lab's own directory even without a definition.yaml (config_dir unset).
        # The directory the plan entry is in, not where a symlinked plan points.
        cfg["plan_dir"] = os.path.realpath(plan_path.parent)

    try:
        from .profiles import get_profile
        cfg = get_profile(cfg.get("type", _BASE_DEFAULTS["type"])).finalize_cfg(cfg)
    except (ImportError, ValueError):
        pass

    libvirt = cfg.get("libvirt")
    if isinstance(libvirt, dict) and libvirt.get("uri") is not None:
        validate_libvirt_uri(libvirt["uri"])

    return cfg


def _placeholder_paths(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """(dotted path, value) for every string still starting with ?? in the config."""
    if isinstance(obj, str):
        return [(path, obj)] if obj.startswith("??") else []
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _placeholder_paths(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _placeholder_paths(v, f"{path}[{i}]")
    return out


def _generatable_keys(cfg: dict) -> set[str]:
    """??keys `rodeo up` would generate (not operator secrets): deferred in check mode."""
    from .secretgen import plan_secret_keys

    operator = {str(k) for k in cfg.get("operator_secrets") or []}
    try:
        from .labinabox_host import cloud_secret_keys

        operator |= cloud_secret_keys(cfg)
    except Exception:
        pass
    return plan_secret_keys(cfg) - operator


def _validate_placeholders(cfg: dict, *, check: bool) -> None:
    lab = lab_dir_of(cfg)
    if check and lab is not None:
        trusted = cfg.get(LAB_TRUSTED_KEY)
        if trusted is None:  # loaded with secrets="off": hash the lab now
            trusted = not cfg.get("_lab_split") and is_lab_trusted(lab)
        if not trusted:
            raise ConfigError(f"{UNTRUSTED_LAB}\nLab: {lab}")
    leftover = _placeholder_paths(cfg)
    if check and leftover:
        sdir = cfg.get("config_dir") or cfg.get("plan_dir")
        have = _load_secrets(Path(sdir) if sdir else None, include_global=True)
        generatable = _generatable_keys(cfg)
        # In a trusted lab a ??env:/??cmd:/??file: resolves at deploy time.
        leftover = [
            (p, v) for p, v in leftover
            if not is_secret_source(v) and v[2:] not in have and v[2:] not in generatable
        ]
    if leftover:
        raise ConfigError(
            f"Secrets not resolved: {', '.join(p for p, _ in leftover)}\n"
            "For ??key: edit ~/.rodeo/secrets.yaml or run: rodeo init\n"
            "For ??env:/??file:/??cmd:: the source returned nothing, "
            "check the variable, file, or command.\n"
            "Under sudo the environment is not passed on: use ??key there."
        )


def validate_config(cfg: dict, *, check: bool = False) -> None:
    """Raise ConfigError on unresolved secrets, empty credentials, or bad values.

    The whole config is checked for leftover ??placeholders, not only
    ``credentials``. ``check=True`` (rodeo plan, deploy --check) needs a trusted
    lab and accepts placeholders that would resolve at deploy time: a ??key in
    the secrets files or one `rodeo up` generates, and ??env:/??cmd:/??file:.
    """
    _validate_placeholders(cfg, check=check)
    creds = cfg.get("credentials", {})
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
    # Targets come from the host-context registry (built-ins + plugins),
    # so a plugin-provided deployment_target validates like the built-ins.
    from .host_context import is_known_target, known_targets

    if not is_known_target(target):
        raise ConfigError(
            f"Invalid deployment_target '{target}' — use one of: {', '.join(known_targets())}."
        )
    if target == "aws":
        _validate_aws_provider(cfg)
    if target == "gcp":
        _validate_gcp_provider(cfg)
    if target == "remote":
        from .providers.remote_host import validate as _validate_remote_host

        _validate_remote_host(cfg)
    if cfg.get("content"):
        from .content import content_spec

        content_spec(cfg)

    libvirt = cfg.get("libvirt", {})
    if not isinstance(libvirt, dict):
        raise ConfigError("libvirt must be a mapping")
    _ALLOWED_DISK_CACHE = ("none", "writethrough", "writeback", "unsafe", "directsync")
    _ALLOWED_DISK_IO = ("native", "threads", "io_uring")
    cache = libvirt.get("disk_cache")
    if cache is not None and cache not in _ALLOWED_DISK_CACHE:
        raise ConfigError(
            f"Invalid libvirt.disk_cache '{cache}' — "
            f"use one of: {', '.join(_ALLOWED_DISK_CACHE)}."
        )
    disk_io = libvirt.get("disk_io")
    if disk_io is not None and disk_io not in _ALLOWED_DISK_IO:
        raise ConfigError(
            f"Invalid libvirt.disk_io '{disk_io}' — "
            f"use one of: {', '.join(_ALLOWED_DISK_IO)}."
        )

    # Let's Encrypt email must be a real address — example.com is rejected by the ACME
    # server, and so (less obviously) is a leftover ??key placeholder: an unresolved
    # secret has no `??` guard here (unlike the credentials{} loop above), so it was
    # passing this check as a literal string and reaching Let's Encrypt as the account
    # contact — which correctly rejects it ("contact email contains a question mark"),
    # but only after the whole rancher phase had already spent 10+ min waiting on it.
    tls = cfg.get("rancher_tls", {})
    if tls.get("source") == "letsEncrypt":
        email = tls.get("email", "")
        if check and str(email).startswith("??"):
            pass  # a deferred placeholder, already checked above
        elif (
            not email
            or email.startswith("??")
            or "example.com" in email
            or email.endswith("@example.com")
        ):
            raise ConfigError(
                "rancher_tls.email must be a real address for Let's Encrypt registration "
                f"(got {email!r}).\n"
                "Set it in rodeo-plan.yaml under rancher_tls.email, or add "
                "rancher_letsencrypt_email: <your address> to ~/.rodeo/secrets.yaml "
                "if using the ??key form."
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


def validate_aws_provider(cfg: dict) -> None:
    """The provider: block check alone (rodeo destroy --cloud needs no secrets)."""
    _validate_aws_provider(cfg)


def _validate_aws_provider(cfg: dict) -> None:
    """Require a valid shared provider: block when deployment_target is aws.

    Skipped when already running on EC2. The provider block describes how to
    *acquire* a host, and on the host itself there is nothing to acquire — but
    the plan still needs deployment_target: aws so apply_host_context raises
    disk_gb to the aws floor and sets storage.backend: nvme for the instance
    store. Requiring the block there would mean shipping the laptop's
    provisioning config to a machine that cannot act on it.
    """
    from .providers.remote_up import on_ec2

    if on_ec2():
        return
    provider = cfg.get("provider")
    if not isinstance(provider, dict):
        raise ConfigError(
            "deployment_target: aws requires a provider: block "
            "(same shape as workshop.yaml — type, region, ami or Leap filter, …)."
        )
    ptype = str(provider.get("type") or "").strip().lower()
    if ptype != "aws":
        raise ConfigError(
            f"deployment_target: aws requires provider.type: aws (got {ptype!r})"
        )
    from .providers.aws import AwsHostProvider

    AwsHostProvider(sleep=lambda _s: None).validate(dict(provider))


def validate_gcp_provider(cfg: dict) -> None:
    """Public entry for destroy --cloud (same as the gcp branch of validate_config)."""
    _validate_gcp_provider(cfg)


def _validate_gcp_provider(cfg: dict) -> None:
    """deployment_target: gcp needs a provider: block of type gcp (skipped on the VM itself)."""
    from .providers.remote_up import on_gce

    if on_gce():
        return
    provider = cfg.get("provider")
    if not isinstance(provider, dict):
        raise ConfigError(
            "deployment_target: gcp requires a provider: block "
            "(type: gcp, region, instance_type or instance_tier, ...)."
        )
    ptype = str(provider.get("type") or "").strip().lower()
    if ptype != "gcp":
        raise ConfigError(f"deployment_target: gcp requires provider.type: gcp (got {ptype!r})")
    from .providers.gcp import GcpHostProvider

    GcpHostProvider(sleep=lambda _s: None).validate(dict(provider))


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
