"""Lint: values interpolated into shell commands go through shlex.quote.

A plan value such as ``versions.k3s: "1.31; touch MARKER"`` used to reach the
rancher VM as shell (``INSTALL_K3S_VERSION="{self.k3s_version}"``). This test
walks the modules that build shell commands and fails on any f-string
placeholder inside a shell context that is not one of:

- ``shlex.quote(...)`` or ``q(...)`` (``rodeo.shellquote.q``),
- ``heredoc(...)`` (a quoted-heredoc body checked for its terminator),
- ``int(...)`` or ``len(...)``,
- an ALL_CAPS name or attribute (a module or class constant),
- a name ending in ``_q`` (a value quoted earlier in the function),
- an entry in ``ALLOWED`` below, each with the reason it is safe.

A shell context is: an argument of a call to one of ``SINKS``, a list literal
that starts with ``bash``/``sh`` and ``-c``/``-lc``/``-s``, the value assigned
to a name containing ``script``, ``cmd`` or ``command``, and the return value
of a function whose name contains one of those words.

Limits (read before trusting a green run):

- Only the modules in ``MODULES`` are scanned. A new module that builds shell
  must be added here.
- Only f-strings are checked. ``str.format``, ``%`` and plain ``+``
  concatenation of an unquoted value are not seen, and neither is a string
  built in a variable with another name and concatenated later.
- It cannot tell a double-quoted shell context from an unquoted one:
  ``"{q(v)}"`` passes the lint and is still wrong (the quotes become literal).
  The behaviour tests in ``test_shell_quoting.py`` cover the fixed sites.
- An ALL_CAPS attribute is trusted as a constant. Making one configurable
  from the plan defeats this check.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "rodeo"

MODULES = [
    "engine/rancher/cluster_setup.py",
    "engine/rancher/content.py",
    "engine/rancher/downstream.py",
    "engine/rancher/elemental.py",
    "engine/rancher/extensions.py",
    "engine/rancher/harvester.py",
    "engine/rancher/hauler.py",
    "engine/rancher/remote.py",
    "engine/rancher/__init__.py",
    "engine/telco.py",
    "engine/runner.py",
    "engine/cluster.py",
    "engine/labinabox_phase.py",
    "fleet/sync.py",
    "fleet/deploy.py",
    "fleet/diagnose.py",
    "fleet/doctor.py",
    "fleet/status.py",
    "fleet/student_access.py",
    "fleet/portal.py",
    "fleet/bootstrap.py",
    "providers/remote_up.py",
    "install_source.py",
    "labinabox_host.py",
    "ssh_key.py",
]

SINKS = {
    "_ssh_run", "_ssh_script", "_eib_ssh_script", "_node_ssh_script",
    "_script_step", "run_remote", "_ssh",
}
SAFE_CALLS = {"shlex.quote", "quote", "q", "heredoc", "int", "len"}
CONTEXT_NAME = re.compile(r"script|cmd|command", re.IGNORECASE)
CONST = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
# ALL_CAPS attributes that are NOT constants: suse-telco sets them per instance
# from telco.rancher_repo in the plan.
NOT_CONST = {"RANCHER_REPO_NAME", "RANCHER_REPO_URL"}

# (module, expression) pairs that are safe without quoting, with the reason.
ALLOWED: dict[tuple[str, str], str] = {
    ("engine/rancher/cluster_setup.py", "self.nodeport"): "int() in RancherPhase.__init__",
    ("engine/rancher/cluster_setup.py", "disable_traefik"): "one of two literals",
    ("engine/rancher/cluster_setup.py", "patch"): "json.dumps of an int port, single-quoted",
    ("engine/rancher/cluster_setup.py", "version"): "shlex.quote just above",
    ("engine/rancher/cluster_setup.py", "remote_values"): "literal path",
    ("engine/rancher/cluster_setup.py", "marker"): "literal heredoc marker",
    ("engine/rancher/cluster_setup.py", "values_yaml"): "checked for the marker above",
    ("fleet/diagnose.py", "n"): "int() and clamped",
    ("fleet/diagnose.py", "sess"): "shlex.quote just above",
    ("fleet/deploy.py", "sess"): "shlex.quote just above",
    ("fleet/deploy.py", "lab"): "shlex.quote just above",
    ("fleet/deploy.py", "target"): "shlex.quote just above",
    ("fleet/deploy.py", "profile_bits"): "built from shlex.quote",
    ("fleet/deploy.py", "trust_bits"): "built from shlex.quote",
    ("fleet/deploy.py", "bootstrap_script(inventory)"): "built from shlex.quote",
    ("fleet/deploy.py", "sync_script(inventory)"): "built from shlex.quote",
    ("fleet/deploy.py", "start_up_script(inventory, session)"): "built from shlex.quote",
    ("fleet/sync.py", "lab"): "shlex.quote just above",
    ("fleet/sync.py", "url"): "shlex.quote just above",
    ("fleet/sync.py", "br"): "shlex.quote just above",
    ("fleet/sync.py", "profile"): "shlex.quote just above",
    ("fleet/sync.py", "target"): "shlex.quote just above",
    ("fleet/sync.py", "update"): "built from shlex.quote",
    ("fleet/sync.py", "clone"): "built from shlex.quote",
    ("providers/remote_up.py", "lab"): "shlex.quote just above",
    ("providers/remote_up.py", "shipped"): "shlex.quote just above",
    ("providers/remote_up.py", "unpack"): "built from shlex.quote",
    ("providers/remote_up.py", "profile_bits"): "built from shlex.quote",
    ("providers/remote_up.py", "bootstrap_fragment(install_url=install_url, ref=ref)"):
        "built from shlex.quote",
    ("install_source.py", "url"): "shlex.quote just above",
    ("install_source.py", "bash"): "built from shlex.quote",
    ("install_source.py", "install"): "built from shlex.quote",
    ("engine/rancher/remote.py", "self.rancher_ip"): "ssh destination after root@, argv not shell",
    ("engine/rancher/remote.py", "self.eib_ip"): "ssh destination after root@, argv not shell",
    ("engine/rancher/downstream.py", "ip"): "ssh destination after root@, argv not shell",
    ("engine/rancher/downstream.py", "command"): "Rancher's own node registration command, meant as shell",
    ("engine/rancher/downstream.py", "roles"): "literal",
    ("engine/rancher/extensions.py", "create_script"): "built from q and heredoc",
    ("engine/rancher/hauler.py", "raw_decompress_cmd"): "built from q",
    ("engine/rancher/hauler.py", "iso_fname"): "fixed literal in RancherPhase.__init__",
    ("engine/rancher/hauler.py", "raw_fname_dl"): "fixed literal in RancherPhase.__init__",
    ("engine/rancher/hauler.py", "mirror_host"): "heredoc() for both nested markers",
    ("engine/rancher/content.py", "nm_path"): "q() just above",
    ("engine/cluster.py", "self.harvester_user"): "ssh argv after --, not shell",
    ("engine/cluster.py", "self.vip"): "ssh argv after --, not shell",
    ("engine/runner.py", "vars_file"): "ansible-playbook argv, no shell",
    ("engine/telco.py", "d"): "podman argv, no shell",
    ("engine/telco.py", "ns"): "each namespace shlex.quote'd just above",
    ("fleet/diagnose.py", "lab"): "shlex.quote just above",
    ("fleet/portal.py", "_package_b64()"): "base64 alphabet",
    ("fleet/portal.py", "_portal_unit()"): "built from constants, quoted heredoc",
    ("fleet/portal.py", "_caddy_unit()"): "built from constants, quoted heredoc",
    ("fleet/portal.py", "admin"): "built from module constants",
    ("labinabox_host.py", "python_bin"): "env argv, no shell",
    ("labinabox_host.py", "checkout"): "env argv, no shell",
    ("labinabox_host.py", "cmd"): "built from q",
    ("ssh_key.py", "pub_b64"): "base64 alphabet",
}


def _is_safe(module: str, node: ast.expr) -> bool:
    if isinstance(node, ast.Call) and ast.unparse(node.func) in SAFE_CALLS:
        return True
    if isinstance(node, ast.Name) and (CONST.match(node.id) or node.id.endswith("_q")):
        return True
    if isinstance(node, ast.Attribute) and CONST.match(node.attr) and node.attr not in NOT_CONST:
        return True
    return (module, ast.unparse(node)) in ALLOWED


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _shell_list(node: ast.List) -> bool:
    lits = [e.value for e in node.elts[:3] if isinstance(e, ast.Constant)]
    return bool(lits) and lits[0] in ("bash", "sh", "sudo") and any(
        x in ("-c", "-lc", "-s") for x in lits
    )


def _contexts(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in SINKS:
            yield from node.args
        elif isinstance(node, ast.List) and _shell_list(node):
            yield node
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any(
                isinstance(t, ast.Name) and CONTEXT_NAME.search(t.id) for t in targets
            ):
                yield node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and CONTEXT_NAME.search(node.name):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    yield sub.value


def _walk_unquoted(node: ast.AST):
    """ast.walk that does not descend into q(...)/heredoc(...) arguments."""
    yield node
    if isinstance(node, ast.Call) and ast.unparse(node.func) in SAFE_CALLS:
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_unquoted(child)


def violations() -> list[str]:
    found: set[str] = set()
    for module in MODULES:
        path = ROOT / module
        tree = ast.parse(path.read_text(), filename=str(path))
        for ctx in _contexts(tree):
            for node in _walk_unquoted(ctx):
                if not isinstance(node, ast.JoinedStr):
                    continue
                for part in node.values:
                    if isinstance(part, ast.FormattedValue) and not _is_safe(module, part.value):
                        found.add(f"{module}:{part.value.lineno}: {{{ast.unparse(part.value)}}}")
    return sorted(found)


def test_shell_contexts_quote_interpolated_values():
    bad = violations()
    assert not bad, (
        "unquoted values in shell commands; wrap them in shlex.quote (or q), "
        "or add them to ALLOWED with the reason they are safe:\n" + "\n".join(bad)
    )


def test_lint_catches_an_unquoted_value(tmp_path, monkeypatch):
    src = tmp_path / "rodeo" / "probe.py"
    src.parent.mkdir()
    src.write_text(
        "def f(self, v):\n"
        "    script = f'helm install --version {v}\\n'\n"
        "    self._ssh_script(f'echo {v}')\n"
        "    ok = f'echo {v}'\n"
        "    script2 = f'echo {q(v)} {shlex.quote(v)} {self.KUBECONFIG}'\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", src.parent)
    monkeypatch.setattr(sys.modules[__name__], "MODULES", ["probe.py"])
    assert violations() == ["probe.py:2: {v}", "probe.py:3: {v}"]
