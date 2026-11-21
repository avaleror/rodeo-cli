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
import os
import re
import subprocess
import warnings
from pathlib import Path
from typing import Any, Literal

import yaml

from .paths import rodeo_last_lab_file, rodeo_secrets_path, rodeo_trusted_labs_path


class ConfigError(ValueError):
    """User-facing configuration problem (bad YAML, bad values, bad params)."""


# Base defaults that apply to every rodeo type.
# Profile-specific defaults (vms, resources, versions) are merged from the profile.
_BASE_DEFAULTS: dict[str, Any] = {
    "type": "suse-virt",
    "name": "suse-virt-rodeo",
    "deployment_target": "baremetal",  # instruqt | baremetal | aws
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
# protects little. `rodeo trust` records a sha256 over the lab's plan,
# definition.yaml and custom/ tree; any change to them makes the lab untrusted
# again. Labs rodeo seeds itself from a bundled or catalog profile are trusted
# at seed time. -P and --paramfile values may not carry `??`, `{{`, `{%` or a
# newline, so an agent passing parameters can't add a secret source.
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


# Written by rodeo itself into a lab directory, so not part of what is trusted.
# A name here is skipped at the top level of the lab; .git and __pycache__
# are skipped at any depth.
_UNHASHED_TOP = frozenset({
    ".rodeo", ".labinabox", LAB_SECRETS_FILE, ".rodeo-shipped-secrets.yaml",
    "rodeo-secrets.env", "logs", "events", "content", "content.lock", "guide",
    "workshop-guide", "workshop-guide.pdf", ".builder-deploy.log",
})
_UNHASHED_ANY = frozenset({".git", "__pycache__"})


def _hashed_files(lab: Path) -> list[Path]:
    """Every file (or symlink) in the lab that trust covers, sorted."""
    out: list[Path] = []
    for root, dirs, files in os.walk(lab):
        here = Path(root)
        top = here == lab
        keep = []
        for d in dirs:
            if d in _UNHASHED_ANY or (top and d in _UNHASHED_TOP):
                continue
            if (here / d).is_symlink():
                out.append(here / d)  # hashed as a link, never followed
            else:
                keep.append(d)
        dirs[:] = sorted(keep)
        for f in files:
            if top and (f in _UNHASHED_TOP or f.endswith(".log")):
                continue
            out.append(here / f)
    return sorted(out)


# -P / --paramfile may not set these: each picks code, a script source or a key
# that a deploy then uses (find_ansible_root, the host bootstrap, content and
# workshop repos, lab-in-a-box source, the SSH key, the lab directories).
PARAM_DENYLIST = (
    "ansible",
    "provider.install_url",
    "provider.ref",
    "content.repo",
    "content.ref",
    "content.path",
    "workshop.repo",
    "workshop.branch",
    "workshop.track",
    "lab_in_a_box.source",
    "ssh.identity_file",
    "config_dir",
    "plan_dir",
    LAB_TRUSTED_KEY,
)


def _override_paths(key: str, value: Any) -> list[str]:
    paths = [key]
    if isinstance(value, dict):
        for k, v in value.items():
            paths += _override_paths(f"{key}.{k}", v)
    return paths


def _reject_denied_keys(overrides: list[tuple[str, Any]], where: str) -> None:
    for key, value in overrides:
        for path in _override_paths(key, value):
            for denied in PARAM_DENYLIST:
                if path == denied or path.startswith(denied + ".") or denied.startswith(path + "."):
                    raise ConfigError(
                        f"{where} {path}: -P and --paramfile may not set {denied}.\n"
                        "It picks code or keys a deploy uses: set it in rodeo-plan.yaml."
                    )


def lab_fingerprint(lab_dir: str | Path) -> str:
    """sha256 over the lab directory: rodeo-plan.yaml, definition.yaml, custom/,
    manifests/, helm/, certs/, story/, per-host manifests... everything except
    the state rodeo writes itself (_UNHASHED_TOP).

    Paths are sorted and hashed with their contents. Symlinks are hashed by
    their target string, never followed. Content repos fetched by URL are
    covered only through the URL (and ref) written in the plan.
    """
    lab = Path(lab_dir)
    h = hashlib.sha256()
    for path in _hashed_files(lab):
        h.update(str(path.relative_to(lab)).encode() + b"\0")
        if path.is_symlink():
            h.update(b"link\0" + os.readlink(path).encode())
        else:
            h.update(b"file\0" + path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def has_hashed_files(lab_dir: str | Path) -> bool:
    """True when the directory already holds anything trust would cover."""
    lab = Path(lab_dir)
    return lab.is_dir() and bool(_hashed_files(lab))


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
    """Set (or with None remove) one lab's entry: read-modify-write under flock."""
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
        key = os.path.realpath(lab_dir)
        if digest is None:
            data.pop(key, None)
        else:
            data[key] = digest
        fh.seek(0)
        fh.truncate()
        fh.write(yaml.safe_dump(dict(sorted(data.items())), default_flow_style=False).encode())
        fh.flush()
        os.fsync(fh.fileno())
    # Written as root after a sudo re-exec: hand it back to the invoking user.
    from .paths import fix_invoking_ownership

    fix_invoking_ownership()
    return path


def trust_lab(lab_dir: str | Path) -> Path:
    """Record the lab's current fingerprint as trusted."""
    return _update_trusted_labs(lab_dir, lab_fingerprint(lab_dir))


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
    return recorded is not None and recorded == lab_fingerprint(lab_dir)


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


def lab_dir_of(cfg: dict) -> str | None:
    """The lab directory trust is recorded against (resolved realpath)."""
    lab = cfg.get("plan_dir") or cfg.get("config_dir")
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
            return _resolve_secret_value(obj, secrets, mode)
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj
    return _walk(cfg)


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
        lab_secrets = lab_dir / LAB_SECRETS_FILE
        if lab_secrets.exists():
            try:
                secrets = {**secrets, **(yaml.safe_load(lab_secrets.read_text()) or {})}
            except yaml.YAMLError as exc:
                raise ConfigError(f"{lab_secrets}: invalid YAML: {exc}")
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
    _reject_denied_keys(cli_params, "-P")
    _reject_denied_keys(list(paramfile_data.items()), "--paramfile")

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
        _record_lab_dir(plan_path.resolve().parent)

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
    cfg = _deep_merge(cfg, paramfile_data)
    for key, value in cli_params:
        _set_path(cfg, key, value)

    lab_dir = Path(config_dir) if config_dir is not None else (plan_path.parent if plan_path.exists() else None)
    # Trust is recorded against the plan's own directory (or --config-dir).
    trust_dir = plan_path.resolve().parent if plan_path.exists() else lab_dir
    split = (
        config_dir is not None and plan_path.exists()
        and os.path.realpath(config_dir) != os.path.realpath(plan_path.parent)
    )
    # None: not computed ("off" hashes nothing); validate_config(check=True) does it.
    trusted: bool | None = None
    if secrets != "off":
        trusted = trust_dir is not None and not split and is_lab_trusted(trust_dir)
        if secrets == "all" and trust_dir is not None and not trusted:
            if split:
                raise ConfigError(
                    "--config outside --config-dir: write commands need the plan inside the "
                    "lab directory, so one rodeo trust covers both."
                )
            raise ConfigError(f"{UNTRUSTED_LAB}\nLab: {os.path.realpath(trust_dir)}")
        cfg = _resolve_secrets(cfg, _load_secrets(lab_dir, include_global=trusted), secrets)
    cfg[LAB_TRUSTED_KEY] = trusted
    cfg["_lab_split"] = split

    env_path = os.environ.get("RODEO_ANSIBLE_PATH")
    if env_path:
        cfg["ansible"]["path"] = env_path

    if config_dir is not None:
        cfg["config_dir"] = str(Path(config_dir).resolve())
    if plan_path.exists():
        # The lab's own directory even without a definition.yaml (config_dir unset).
        cfg["plan_dir"] = str(plan_path.resolve().parent)

    try:
        from .profiles import get_profile
        cfg = get_profile(cfg.get("type", _BASE_DEFAULTS["type"])).finalize_cfg(cfg)
    except (ImportError, ValueError):
        pass

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
            trusted = not cfg.get("_lab_split") and is_lab_trusted(cfg.get("plan_dir") or lab)
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
