"""Workshop track parsing, plan-driven secret generation, and the no-credentials-in-git guard."""
from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.secretgen import append_secret, ensure_plan_secrets, plan_secret_keys
from rodeo.workshop import parse_track, topology_warnings, workshop_spec

DATA = Path(__file__).resolve().parent.parent / "rodeo" / "data"


# ── workshop ────────────────────────────────────────────────────────────────

def _track(tmp_path: Path) -> Path:
    track = tmp_path / "tracks" / "smlms"
    (track / "01-intro").mkdir(parents=True)
    (track / "02-distros").mkdir()
    (track / "assets").mkdir()
    (track / "01-intro" / "assignment.md").write_text(
        'Open [[ Instruqt-Var key="SMLM_URL" hostname="smlm" ]] as [[ Instruqt-Var key="SMLM_USERNAME" ]]')
    (track / "02-distros" / "assignment.md").write_text('Password: [[ Instruqt-Var key="UNIVERSAL_PWD" ]]')
    (track / "track.yml").write_text("title: SUSE Multi-Linux Hands-on Workshop\n")
    (track / "config.yml").write_text(yaml.safe_dump({
        "version": "3",
        "virtualbrowsers": [{"name": "smlm-www", "url": "https://smlm"}],
        "virtualmachines": [{"name": n} for n in ("smlm", "centos7", "zbastion", "ubuntu2404lts")],
        "secrets": [{"name": "SMLM_ADMIN_PASSWORD"}],
    }))
    return track


def test_parse_track(tmp_path):
    track = parse_track(_track(tmp_path))
    assert track["title"] == "SUSE Multi-Linux Hands-on Workshop"
    assert track["vms"] == ["smlm", "centos7", "zbastion", "ubuntu2404lts"]
    assert track["browsers"] == ["smlm-www"]
    assert track["secrets"] == ["SMLM_ADMIN_PASSWORD"]
    assert track["challenges"] == ["01-intro", "02-distros"]
    assert track["variables"] == ["SMLM_URL", "SMLM_USERNAME", "UNIVERSAL_PWD"]


def test_parse_track_requires_config(tmp_path):
    with pytest.raises(ConfigError, match="workshop.track"):
        parse_track(tmp_path)


def test_workshop_spec_validation():
    assert workshop_spec({}) is None
    spec = workshop_spec({"workshop": {"repo": "https://github.com/x/y", "track": "smlms"}})
    assert spec["branch"] == "main" and spec["skip_vms"] == [] and spec["facts"] == {}
    for bad in ({"repo": "git@github.com:x/y", "track": "t"},
                {"repo": "https://x", "track": "../t"},
                {"repo": "https://x", "track": "t", "branch": "a b"}):
        with pytest.raises(ConfigError):
            workshop_spec({"workshop": bad})


def test_topology_warnings(tmp_path):
    track = parse_track(_track(tmp_path))
    spec = workshop_spec({"workshop": {
        "repo": "https://x", "track": "smlms", "skip_vms": ["zbastion"],
        "facts": {"SMLM_URL": "u", "SMLM_USERNAME": "m"}}})
    warnings = topology_warnings(track, spec, ["smlm.rodeo.lab", "centos7.rodeo.lab"])
    assert warnings == [
        "track machine(s) with no lab VM: ubuntu2404lts — add them to the definition or list them in workshop.skip_vms",
        "assignment variable(s) with no workshop.facts value: UNIVERSAL_PWD",
    ]
    spec["facts"]["UNIVERSAL_PWD"] = "p"
    assert topology_warnings(track, spec, ["smlm", "centos7", "ubuntu2404lts"]) == []


def test_smlm_workshop_plan_covers_the_real_track_variables():
    plan = yaml.safe_load((DATA / "examples" / "smlm-workshop" / "rodeo-plan.yaml").read_text())
    # Variables used by tracks/smlms assignments on instruqt-SMLM main.
    assert set(plan["workshop"]["facts"]) >= {"COMPANY_NAME", "SMLM_URL", "SMLM_USERNAME", "UNIVERSAL_PWD"}


# ── plan secrets ────────────────────────────────────────────────────────────

PLAN = {
    "credentials": {"a": "??admin_pw"},
    "lab_in_a_box": {"sections": {"smlm": {"users": [{"password": "??universal_pwd"}],
                                           "regcode": "??scc_regcode",
                                           "env": "??env:HOME", "file": "??file:/x"}}},
    "operator_secrets": ["scc_regcode"],
}


def test_plan_secret_keys_only_plain_form():
    assert plan_secret_keys(PLAN) == {"admin_pw", "universal_pwd", "scc_regcode"}


def test_ensure_plan_secrets_generates_and_reports(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text("# keep me\nadmin_pw: \"existing\"\n")
    generated, missing = ensure_plan_secrets(PLAN, path)
    assert generated == ["universal_pwd"]
    assert missing == ["scc_regcode"]
    text = path.read_text()
    assert text.startswith("# keep me\nadmin_pw: \"existing\"\n")
    data = yaml.safe_load(text)
    assert data["admin_pw"] == "existing"
    assert len(data["universal_pwd"]) >= 12
    assert "scc_regcode" not in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Idempotent: nothing new on a second run.
    assert ensure_plan_secrets(PLAN, path) == ([], ["scc_regcode"])


def test_append_secret_quotes_any_value(tmp_path):
    path = tmp_path / "secrets.yaml"
    append_secret(path, "odd", 'a: "b" #c')
    assert yaml.safe_load(path.read_text()) == {"odd": 'a: "b" #c'}


# ── no credentials in git ──────────────────────────────────────────────────

_SECRET_KEY = re.compile(r"(pass|passwd|password|pwd|regcode|token|secret|private_key|sha256)$", re.I)
# Values that are fine in git: placeholders, empty, booleans, pinned public checksums.
_ALLOWED = re.compile(r"^(\?\?.+|true|false|)$", re.I)
_PUBLIC_CHECKSUM_FILES = {"rodeo/data/examples/smlm-workshop/rodeo-plan.yaml"}


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _walk(value, f"{path}[{i}]")
    else:
        yield path, obj


def test_bundled_plans_hold_no_literal_credentials():
    root = DATA.parent.parent
    offenders = []
    for path in sorted((DATA / "examples").rglob("*.yaml")) + sorted((DATA / "platforms").rglob("*.yaml")):
        rel = str(path.relative_to(root))
        for doc in yaml.safe_load_all(path.read_text()):
            for key_path, value in _walk(doc):
                leaf = re.split(r"[.\[]", key_path)[-1]
                if not isinstance(value, str) or not _SECRET_KEY.search(leaf) or _ALLOWED.match(value):
                    continue
                if leaf == "sha256" and rel in _PUBLIC_CHECKSUM_FILES and re.fullmatch(r"[0-9a-f]{64}", value):
                    continue  # checksum of a public image, not a secret
                offenders.append(f"{rel}: {key_path}")
    assert offenders == []


# ── workshop: ref pin, lock, guide rendering, warn-only fetch ──────────────

def test_ref_pin_and_lock(tmp_path):
    from rodeo.workshop import lock_text, track_fetch_commands

    spec = workshop_spec({"workshop": {"repo": "https://x/y", "track": "t", "ref": "a" * 40}})
    fetch = next(c for c in track_fetch_commands(spec, tmp_path) if "fetch" in c)
    assert fetch[-1] == "a" * 40
    assert yaml.safe_load(lock_text(spec, "b" * 40)) == {
        "repo": "https://x/y", "branch": "main", "ref": "a" * 40, "track": "t", "commit": "b" * 40}
    with pytest.raises(ConfigError):
        workshop_spec({"workshop": {"repo": "https://x/y", "track": "t", "ref": "main; rm -rf /"}})


def test_render_guide(tmp_path):
    from rodeo.workshop import render_guide

    spec = workshop_spec({"workshop": {
        "repo": "https://x", "track": "smlms",
        "facts": {"SMLM_URL": "https://smlm.rodeo.lab", "SMLM_USERNAME": "myadmin"},
        "replace": {"smlm.${_SANDBOX_ID}.instruqt.io": "smlm.rodeo.lab"}}})
    track = _track(tmp_path)
    (track / "01-intro" / "assignment.md").write_text(
        'curl smlm.${_SANDBOX_ID}.instruqt.io; open [[ Instruqt-Var key="SMLM_URL" hostname="smlm" ]] '
        'as [[ Instruqt-Var key="SMLM_USERNAME" ]] / [[ Instruqt-Var key="UNIVERSAL_PWD" ]]')
    out = tmp_path / "guide"
    files = render_guide(track, spec, out)
    assert [f.name for f in files] == ["01-intro.md", "02-distros.md"]
    assert files[0].read_text() == "curl smlm.rodeo.lab; open https://smlm.rodeo.lab as myadmin / <UNIVERSAL_PWD>"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(out.stat().st_mode) == 0o700


def test_workshop_fetch_failure_only_warns(tmp_path):
    from rodeo.engine import labinabox_phase as phase
    from rodeo.engine.runner import LogLine

    class Runner:
        cfg = {"config_dir": str(tmp_path)}
        root = tmp_path
        _last_rc = 0

        def _stream_subprocess(self, cmd, env=None):
            self._last_rc = 128
            yield LogLine("fatal: unable to access")

    runner = Runner()
    spec = workshop_spec({"workshop": {"repo": "https://x/y", "track": "t"}})
    lines = [e.line for e in phase._stream_workshop(runner, spec, {"nodes": {}}) if isinstance(e, LogLine)]
    assert any("lab itself is unaffected" in line for line in lines)
    assert not (tmp_path / ".labinabox" / "workshop.lock").exists()


# ── workshop.pdf: the track's own build tool, run on the rendered guide ─────

def _pdf_track(tmp_path, tool_body: str) -> Path:
    track = _track(tmp_path)
    (track / "tools").mkdir()
    (track / "tools" / "build_pdf.py").write_text(tool_body)
    return track


def test_prepare_pdf_build_uses_the_rendered_guide(tmp_path):
    from rodeo.workshop import prepare_pdf_build, render_guide

    track = _pdf_track(tmp_path, "")
    spec = workshop_spec({"workshop": {"repo": "https://x", "track": "smlms",
                                       "facts": {"SMLM_URL": "https://smlm.rodeo.lab"}}})
    guide = tmp_path / "guide"
    render_guide(track, spec, guide)
    tool = prepare_pdf_build(track, guide, tmp_path / "build")
    assert tool == tmp_path / "build" / "tools" / "build_pdf.py"
    copied = (tmp_path / "build" / "01-intro" / "assignment.md").read_text()
    assert "https://smlm.rodeo.lab" in copied and "Instruqt-Var" not in copied.split("SMLM_USERNAME")[0]
    assert "Instruqt-Var" in (track / "01-intro" / "assignment.md").read_text()   # original untouched
    assert prepare_pdf_build(_track(tmp_path / "other"), guide, tmp_path / "b2") is None


def _pdf_runner(tmp_path):
    class Runner:
        cfg = {"config_dir": str(tmp_path)}
        root = tmp_path
        _last_rc = 0
    (tmp_path / "workshop-guide").mkdir(exist_ok=True)
    return Runner()


def test_guide_pdf_built_private(tmp_path):
    from rodeo.engine import labinabox_phase as phase
    from rodeo.engine.runner import LogLine

    track = _pdf_track(tmp_path, "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text('%PDF')\n")
    runner = _pdf_runner(tmp_path)
    lines = [e.line for e in phase._stream_guide_pdf(runner, track) if isinstance(e, LogLine)]
    pdf = tmp_path / "workshop-guide.pdf"
    assert pdf.read_text() == "%PDF" and stat.S_IMODE(pdf.stat().st_mode) == 0o600
    assert any("workshop guide PDF" in line for line in lines)


def test_guide_pdf_failure_only_warns(tmp_path):
    from rodeo.engine import labinabox_phase as phase
    from rodeo.engine.runner import LogLine

    track = _pdf_track(tmp_path, "raise SystemExit('No Chromium/Chrome binary found in PATH')\n")
    runner = _pdf_runner(tmp_path)
    lines = [e.line for e in phase._stream_guide_pdf(runner, track) if isinstance(e, LogLine)]
    assert any("No Chromium" in line and "needs pandoc" in line for line in lines)
    assert runner._last_rc == 0
