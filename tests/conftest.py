"""Shared fixtures: isolated HOME, state dir, and a fake profile."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

from rodeo.engine.runner import DeployEvent
from rodeo.profiles import _REGISTRY
from rodeo.profiles.base import RodeoProfile


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep ~/.rodeo state/vars and secrets inside tmp_path for every test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RODEO_PASSWORD", raising=False)
    for var in ("RODEO_REPO", "RODEO_INSTALL_URL_TEMPLATE", "RODEO_LABINABOX_REPO",
                "RODEO_LABINABOX_REF", "RODEO_LABINABOX_PATH"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class FakeProfile(RodeoProfile):
    """Three-phase profile with scriptable per-phase results."""

    name = "fake"
    phases = ["alpha", "beta", "gamma"]
    vm_names = ["vm1", "vm2"]
    ansible_phases = frozenset()
    guarded_phases = frozenset(["gamma"])

    def __init__(self) -> None:
        self.results: dict[str, int] = {}      # phase -> rc (default 0)
        self.raises: set[str] = set()          # phases that raise mid-run
        self.ran: list[str] = []

    def default_cfg(self) -> dict:
        return {"vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}}}

    def run_phase(self, phase, runner, vars_file: Path) -> Iterator[DeployEvent]:
        self.ran.append(phase)
        if phase in self.raises:
            raise RuntimeError(f"boom in {phase}")
        runner._last_rc = self.results.get(phase, 0)
        return
        yield  # pragma: no cover — makes this a generator


@pytest.fixture
def fake_profile(monkeypatch):
    profile = FakeProfile()
    monkeypatch.setitem(_REGISTRY, "fake", profile)
    return profile


@pytest.fixture
def fake_cfg():
    return {
        "type": "fake",
        "name": "test-plan",
        "deployment_target": "baremetal",
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9"},
        "credentials": {
            "harvester_os_password": "Secret123",
            "harvester_admin_password": "Secret123",
            "rancher_admin_password": "Secret123",
            "harvester_token": "test-token-123",
        },
        "ansible": {"inventory": "deployer/inventory.local"},
        "vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}},
    }


def drain(gen):
    """Exhaust a generator, returning (events, return_value)."""
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as exc:
            return events, exc.value


# ── Rodeo Builder chapter sources (tests/test_builder*.py) ─────────────────

def make_instruqt(root: Path) -> Path:
    for name, title, check in (("01-intro", "Welcome!", ""), ("02-manage", "Managing distros", "#!/bin/bash\nexit 0\n")):
        d = root / "tracks" / "smlms" / name
        d.mkdir(parents=True)
        (d / "assignment.md").write_text("---\nslug: x\ntitle: {}\ntimelimit: 6000\n---\n\nBody of {}\n".format(title, name))
        if check:
            (d / "check-smlm").write_text(check)
    return root


def make_markdown(root: Path) -> Path:
    (root / "docs" / "exercises").mkdir(parents=True)
    (root / "checks").mkdir()
    (root / "docs" / "exercises" / "01-the-arrival.md").write_text("# Exercise 1: The Arrival\n\n**Time:** 30 min\n\nText\n")
    (root / "docs" / "exercises" / "bonus-final.md").write_text("# Bonus\n\nNo time line\n")
    (root / "checks" / "check-exercise-1.sh").write_text("#!/bin/bash\necho ok\n")
    return root


@pytest.fixture(scope="session")
def instruqt_source(tmp_path_factory) -> Path:
    """A minimal Instruqt track checkout (tracks/smlms) for the smlm-workshop source."""
    return make_instruqt(tmp_path_factory.mktemp("instruqt"))


@pytest.fixture(scope="session")
def markdown_source(tmp_path_factory) -> Path:
    """A minimal markdown exercises checkout (docs/exercises + checks) for the virt-workshop source."""
    return make_markdown(tmp_path_factory.mktemp("markdown"))


def make_labinabox(root: Path) -> Path:
    """A lab-in-a-box checkout whose lab-builder API answers a small catalogue."""
    lib = root / "webui" / "lib"
    lib.mkdir(parents=True)
    (lib / "api.py").write_text(
        "def dispatch(action, method, params, body):\n"
        "    return 200, {'components': [\n"
        "        {'name': 'install_smlm', 'targets': ['container']},\n"
        "        {'name': 'install_client_registration', 'kind': 'addon', 'targets': ['vm']},\n"
        "        {'name': 'pxe', 'kind': 'infrastructure', 'targets': []},\n"
        "        {'name': 'rke2', 'kind': 'kcluster', 'targets': []}]}\n")
    return root


@pytest.fixture(scope="session")
def labinabox_checkout(tmp_path_factory) -> Path:
    """A fake lab-in-a-box checkout for the builder's catalogue (smlm, client_registration, pxe, rke2)."""
    return make_labinabox(tmp_path_factory.mktemp("liab"))


FAKE_RMSTORY = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
cmd = args[0]
if cmd == "engines":
    print("deepl                 translate only")
    print("ollama                translate + generate")
    sys.exit(0)
if cmd == "validate":
    files = sorted(Path(args[1]).glob("*.md"))
    for f in files:
        text = f.read_text()
        if "WARN" in text:
            print("warning: {}:2: odd span".format(f), file=sys.stderr)
        if "NOID" in text:
            print("error: {}:3: span carries lang/hist/no but has no id attribute".format(f), file=sys.stderr)
            sys.exit(1)
    print("{} tagged span(s) in {} file(s), 0 warning(s)".format(3, len(files)))
    sys.exit(0)
if cmd == "extract":
    if os.environ.get("FAKE_RMSTORY_FAIL"):
        print("error: engine refused: bad key", file=sys.stderr)
        sys.exit(1)
    to, engine = args[args.index("--to") + 1], args[args.index("--engine") + 1]
    store = Path(os.environ["RMSTORY_STRINGS_PATH"])
    rows = 0
    for f in sorted(Path(args[1]).glob("*.md")):
        text = f.read_text().replace('<span lang="en">', '<span id="{}.9" lang="en">'.format(f.stem), 1)
        f.write_text(text)
        for sid, content in re.findall(r'<span (?:lang="en" id|id)="([^"]+)"[^>]*>([^<]*)</span>', text):
            for lang, value, who in (("en", content, "rmstory.extract"),
                                     (to, "[{}] {}".format(to, content), "rmstory.engines:" + engine)):
                leaf = store / lang / sid / "@default" / "content.json"
                if lang == to and leaf.exists():
                    continue
                leaf.parent.mkdir(parents=True, exist_ok=True)
                leaf.write_text(json.dumps({"content": value, "status": "draft", "updated_by": who}))
                rows += lang == to
    print("extracted spans, machine-translated {} row(s) via {}".format(rows, engine))
    sys.exit(0)
if cmd == "generate" and args[1] == "rewrite":
    theme, out = args[args.index("--theme") + 1], Path(args[args.index("--out") + 1])
    for f in sorted(Path(args[2]).glob("*.md")):
        text = re.sub(r'(<span lang="en" id="[^"]+"[^>]*>)[^<]*', lambda m: m.group(1) + theme, f.read_text())
        if text != f.read_text():
            out.mkdir(exist_ok=True)
            (out / f.name).write_text(text)
    sys.exit(0)
if cmd == "generate":
    prompt, lang = args[args.index("--prompt") + 1].strip().splitlines()[-1], args[args.index("--lang") + 1]
    print('<span lang="{}" id="draft.one">{}</span>'.format(lang, prompt))
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def fake_rmstory(tmp_path, monkeypatch) -> Path:
    """An ``rmstory`` on PATH that answers like the real CLI: ``engines`` (deepl, ollama),
    ``validate`` (a warning for WARN, an error and exit 1 for NOID), ``extract --engine --to``
    (fills the filesystem store with "[<lang>] <text>" and names the first unnamed span
    ``<file>.9``), ``generate rewrite`` (every ``lang="en"`` span with an id says the theme)
    and ``generate new`` (one span holding the prompt's last line)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "rmstory"
    script.write_text("#!{}\n{}".format(sys.executable, FAKE_RMSTORY))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", "{}{}{}".format(bindir, os.pathsep, os.environ.get("PATH", "")))
    return script
