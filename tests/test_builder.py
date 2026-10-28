"""Rodeo Builder: data layer (rodeo/builder/discovery.py, api.py) and the static build."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from rodeo.builder import discovery
from rodeo.builder.api import Api

REPO = Path(__file__).resolve().parent.parent


# ── discovery ───────────────────────────────────────────────────────────────

def test_engines_have_bases_resources_and_capabilities():
    data = discovery.engines()
    engines = {e["name"]: e for e in data["engines"]}
    assert list(engines) == ["suse-virt", "rancher", "suse-edge", "lab-in-a-box"]
    for name in ("suse-virt", "rancher", "suse-edge"):
        assert yaml.safe_load(engines[name]["plan"])["resources"], name
        assert engines[name]["resources"]["nodes"] > 0 and engines[name]["resources"]["memory_mib"] > 0
        assert engines[name]["provides"]
    liab = engines["lab-in-a-box"]
    assert liab["external"] and liab["provides"] == [] and "plan" not in liab
    assert yaml.safe_load(liab["definition"])["definition"]["nodes"][0]["name"] == "vm1"
    provides = {c for e in engines.values() for c in e["provides"]}
    assert data["capabilities"] == sorted(provides) and "rancher" in data["capabilities"]
    assert data["targets"] == ["aws", "baremetal", "instruqt"]
    assert data["content_formats"] == ["rmstory", "instruqt", "markdown"]


def test_engines_and_targets_come_from_the_registries(monkeypatch):
    from rodeo import host_context, profiles
    from rodeo.profiles.rancher import RancherProfile

    class Plugin(RancherProfile):
        name = "plugin-type"
        builder = {"title": "Plugin", "base": "rancher", "provides": ["plugin-cap"], "note": "From a plugin."}

    monkeypatch.setitem(profiles._REGISTRY, "plugin-type", Plugin())
    monkeypatch.setitem(host_context._TARGETS, "plugin-target", lambda cfg, facts: None)
    data = discovery.engines()
    assert [e["name"] for e in data["engines"]][-1] == "plugin-type"
    assert "rancher-test" not in [e["name"] for e in data["engines"]]
    assert "plugin-cap" in data["capabilities"] and "plugin-target" in data["targets"]


def test_workshops_read_chapters_and_their_metadata():
    shops = {w["id"]: w for w in discovery.workshops()["workshops"]}
    first = shops["rancher-lab-config"]
    assert first["title"] == "First ride" and first["engine"] == "rancher"
    chapter = first["chapters"][0]
    assert chapter["id"] == "welcome" and chapter["file"] == "01-welcome.md"
    assert chapter["title"] == "Welcome to the rodeo"
    assert chapter["mins"] == 10 and chapter["needs"] == ["rancher"] and chapter["spans"] > 0
    assert '<span lang="en" id="welcome.intro">' in chapter["body"]
    assert first["variants"] == {"first-ride": ["welcome.first-task"]}


def test_chapter_defaults_without_metadata(tmp_path, monkeypatch):
    example = tmp_path / "examples" / "demo"
    (example / "story").mkdir(parents=True)
    (example / "story" / "02-intro.md").write_text("no heading here\n")
    monkeypatch.setattr(discovery, "EXAMPLES", tmp_path / "examples")
    shop = discovery.workshops()["workshops"][0]
    assert shop["title"] == "demo" and shop["engine"] == "suse-virt"
    chapter = shop["chapters"][0]
    assert (chapter["id"], chapter["title"], chapter["mins"], chapter["needs"]) == ("intro", "intro", 10, [])


def test_labinabox_imports_list_bundled_labinabox_rodeos():
    imports = {i["profile"]: i for i in discovery.labinabox_imports()}
    assert set(imports) == {"smlm-workshop"}
    assert {"smlm", "client_registration"} <= set(imports["smlm-workshop"]["addons"])
    assert yaml.safe_load(imports["smlm-workshop"]["plan"])["type"] == "lab-in-a-box"
    lab = imports["smlm-workshop"]["lab"]
    assert lab == yaml.safe_load(imports["smlm-workshop"]["plan"])["lab_in_a_box"]["lab"]
    assert len(lab["nodes"]) == 8 and lab["smlm"]["smlm_admin_pass"] == "??smlm_admin_password"


def _fake_labinabox(tmp_path: Path, body: str) -> Path:
    lib = tmp_path / "liab" / "webui" / "lib"
    lib.mkdir(parents=True)
    (lib / "api.py").write_text(body)
    return tmp_path / "liab"


def test_labinabox_addons_come_from_its_api(tmp_path):
    checkout = _fake_labinabox(tmp_path, (
        "import os\n"
        "def dispatch(action, method, params, body):\n"
        "    assert os.environ['LABBUILDER_SCRIPTS_DIR'].endswith('scripts')\n"
        "    return 200, {'components': [{'name': 'install_smlm', 'targets': ['container']},\n"
        "                                {'name': 'install_client_registration', 'targets': ['vm']}]}\n"))
    assert discovery.labinabox_addons(checkout) == [
        {"name": "smlm", "kind": "addon", "targets": ["container"]},
        {"name": "client_registration", "kind": "addon", "targets": ["vm"]}]
    data = discovery.labinabox(checkout, "https://example/lb/", "1.10.0")
    assert data["builder_url"] == "https://example/lb/" and data["version"] == "1.10.0"
    assert [a["name"] for a in data["addons"]] == ["smlm", "client_registration"]
    assert data["error"] == ""


def test_labinabox_catalogue_is_split_by_kind(labinabox_checkout):
    data = discovery.labinabox(labinabox_checkout)
    assert [a["name"] for a in data["addons"]] == ["smlm", "client_registration"]
    assert [a["name"] for a in data["infrastructure"]] == ["pxe"]
    assert [a["name"] for a in data["kclusters"]] == ["rke2"]


def test_labinabox_addons_run_without_their_named_interpreter(tmp_path):
    checkout = _fake_labinabox(tmp_path, (
        "import os, subprocess\n"
        "def dispatch(action, method, params, body):\n"
        "    script = os.path.join(os.environ['LABBUILDER_SCRIPTS_DIR'], 'install_smlm')\n"
        "    r = subprocess.run([script, '--schema', 'json'], capture_output=True, text=True)\n"
        "    names = [r.stdout.strip()] if r.returncode == 0 else []\n"
        "    return 200, {'components': [{'name': n, 'targets': []} for n in names]}\n"))
    script = checkout / "scripts" / "install_smlm"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env python3.0-rodeo-missing\nprint('install_smlm')\n")
    script.chmod(0o755)
    assert discovery.labinabox_addons(checkout) == [{"name": "smlm", "kind": "addon", "targets": []}]


def test_labinabox_without_addons_fails_loudly(tmp_path):
    checkout = _fake_labinabox(tmp_path, (
        "def dispatch(action, method, params, body):\n"
        "    return 200, {'components': [{'name': 'rke2', 'kind': 'kcluster', 'targets': []}]}\n"))
    with pytest.raises(RuntimeError, match="none reported"):
        discovery.labinabox_addons(checkout)
    assert "none reported" in discovery.labinabox(checkout)["error"]


def test_labinabox_addons_fail_loudly(tmp_path):
    checkout = _fake_labinabox(tmp_path, "def dispatch(*a):\n    return 500, {'error': 'boom'}\n")
    with pytest.raises(RuntimeError, match="boom"):
        discovery.labinabox_addons(checkout)
    broken = discovery.labinabox(checkout)
    assert broken["addons"] == [] and "boom" in broken["error"]
    missing = discovery.labinabox()
    assert missing["addons"] == [] and "no lab-in-a-box checkout" in missing["error"]
    assert discovery.labinabox(missing="offline today")["error"] == "offline today"


# ── api ─────────────────────────────────────────────────────────────────────

def test_api_dispatch(labinabox_checkout):
    api = Api(labinabox_checkout)
    assert api.dispatch("engines")[0] == 200
    assert api.dispatch("nope") == (404, {"error": "unknown action: nope"})
    assert api.dispatch("engines", "POST")[0] == 405
    assert api.dispatch("save", "POST", body=b"{}") == (403, {"error": "saving needs the rodeo builder server"})
    data = api.static_data()
    assert set(data) == {"engines", "workshops", "labinabox"}


def test_static_data_requires_the_catalogue_unless_told_otherwise():
    with pytest.raises(RuntimeError, match="lab-in-a-box catalogue: no lab-in-a-box checkout"):
        Api().static_data()
    assert Api().static_data(require_labinabox=False)["labinabox"]["error"]


def test_static_data_raises_when_an_answer_fails(monkeypatch):
    def broken(*_):
        raise OSError("disk gone")
    monkeypatch.setattr(discovery, "workshops", broken)
    assert Api().dispatch("workshops") == (500, {"error": "disk gone"})
    with pytest.raises(RuntimeError, match="workshops: disk gone"):
        Api().static_data()


# ── logic.js (Node) and the plans it generates ─────────────────────────────

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_logic_js_unit_tests():
    files = sorted(str(f) for f in (REPO / "tests" / "builder").glob("*.test.js"))
    assert files
    r = subprocess.run([NODE, "--test", *files], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def _node_plans(script: str) -> dict:
    prelude = "const RB = require({});\n".format(json.dumps(str(REPO / "rodeo/builder/htdocs/logic.js")))
    r = subprocess.run([NODE, "-e", prelude + script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@needs_node
def test_generated_labinabox_plan_is_a_valid_rodeo(tmp_path):
    from rodeo.config import load_config
    from rodeo.labinabox import build_lab_json

    engines = {e["name"]: e for e in discovery.engines()["engines"]}
    out = _node_plans("console.log(JSON.stringify({plan: RB.planLabinabox({name: 'liab-demo', target: 'baremetal', "
                      "addons: ['smlm', 'client_registration'], story: {language: 'es', id: 'first-ride'}})}))")
    lab = tmp_path / "liab-demo"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(out["plan"])
    (lab / "definition.yaml").write_text(engines["lab-in-a-box"]["definition"])
    cfg = load_config(lab / "rodeo-plan.yaml")
    assert cfg["type"] == "lab-in-a-box" and cfg["story"] == {"language": "es", "id": "first-ride"}
    lab_json, _ = build_lab_json(cfg)
    vm = lab_json["nodes"]["vm1.rodeo.lab"]
    assert vm["addons"] == ["smlm", "client_registration"]
    assert vm["VM_MEM"] == "4096"
    assert lab_json["common"]["ISO_URL"].endswith("openSUSE-Leap-15.6-Minimal-VM.x86_64-Cloud.qcow2")


@needs_node
def test_a_lab_from_the_lab_builder_is_the_workshops_lab(tmp_path, monkeypatch):
    """The SMLM workshop with a lab sent back from the lab-builder (one VM resized, one added):
    the plan keeps the workshop and deploys that lab."""
    from rodeo.config import load_config
    from rodeo.labinabox import build_lab_json
    from rodeo.labseed import example_dir

    base = next(i for i in discovery.labinabox_imports() if i["profile"] == "smlm-workshop")
    lab = base["lab"]
    lab["nodes"]["smlm.rodeo.lab"]["VM_MEM"] = "24576"
    lab["nodes"]["extra.rodeo.lab"] = {"myip": "192.168.122.61", "VM_MEM": "1024", "VM_CPU": "1", "VM_DSK": "10",
                                       "ISO_IMAGE": "ubuntu-24.04-server-cloudimg-amd64.img", "flag": "on"}
    out = _node_plans("console.log(JSON.stringify({plan: RB.planFromBase(RB.setStoredLab(" + json.dumps(base["plan"]) +
                      ", " + json.dumps(lab) + "), {name: 'smlm-mine', target: 'baremetal', story: {language: 'en'}})}))")
    plan, original = yaml.safe_load(out["plan"]), yaml.safe_load(base["plan"])
    assert plan["lab_in_a_box"]["lab"] == lab
    for key in ("workshop", "operator_secrets", "resources"):
        assert plan[key] == original[key], key
    assert plan["lab_in_a_box"]["variants"] == original["lab_in_a_box"]["variants"]
    home = tmp_path / "home"
    (home / ".rodeo").mkdir(parents=True)
    keys = set(re.findall(r"\?\?(\w+)", out["plan"]))
    (home / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump({k: "x" for k in keys}))
    monkeypatch.setenv("HOME", str(home))
    shutil.copytree(example_dir("smlm-workshop"), tmp_path / "smlm-mine")
    (tmp_path / "smlm-mine" / "rodeo-plan.yaml").write_text(out["plan"])
    cfg = load_config(tmp_path / "smlm-mine" / "rodeo-plan.yaml")
    assert cfg["vms"]["extra"]["ip"] == "192.168.122.61"
    rendered, _ = build_lab_json(cfg)
    assert rendered["nodes"]["smlm.rodeo.lab"]["VM_MEM"] == "24576"
    assert rendered["nodes"]["extra.rodeo.lab"]["flag"] == "on"
    assert rendered["client_registration"]["client_registration_server_ip"] == "192.168.122.20"


@needs_node
@pytest.mark.parametrize("engine", ["suse-virt", "rancher", "suse-edge"])
def test_generated_native_plans_keep_their_base(engine, tmp_path):
    base = next(e for e in discovery.engines()["engines"] if e["name"] == engine)
    out = _node_plans("console.log(JSON.stringify({plan: RB.planFromBase(" + json.dumps(base["plan"]) +
                      ", {name: 'mine', target: 'instruqt', story: {language: 'de'}})}))")
    plan, original = yaml.safe_load(out["plan"]), yaml.safe_load(base["plan"])
    assert plan["name"] == "mine" and plan["deployment_target"] == "instruqt"
    assert plan["story"] == {"language": "de"}
    assert plan["resources"] == original["resources"]
    assert {k: v for k, v in plan.items() if k not in ("name", "deployment_target", "story")} == \
        {k: v for k, v in original.items() if k not in ("name", "deployment_target", "story")}


# ── static build ────────────────────────────────────────────────────────────

BUILD = REPO / "scripts" / "build-builder-static.py"


def test_static_build_embeds_every_answer(tmp_path, labinabox_checkout):
    out = tmp_path / "builder"
    r = subprocess.run([sys.executable, str(BUILD), "--output", str(out), "--lab-builder-url", "https://x/lb/",
                        "--no-fetch-sources", "--labinabox", str(labinabox_checkout)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["app.js", "assets", "fonts", "index.html", "logic.js", "style.css", "theme.js"]
    html = (out / "index.html").read_text()
    assert "__RODEOVERSION__" not in html
    payload = re.search(r'<script id="static-api-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(payload.replace("<\\/", "</"))
    assert set(data) == {"engines", "workshops", "labinabox"}
    assert data["labinabox"]["builder_url"] == "https://x/lb/"
    assert [a["name"] for a in data["labinabox"]["kclusters"]] == ["rke2"]
    assert "window.RB_STATIC = true" in html and "rodeo-builder-token" not in html
    assert html.index("static-api-data") > html.index('src="app.js"')
    assert "style=" not in html


def test_static_build_fails_without_data(tmp_path):
    r = subprocess.run([sys.executable, str(BUILD), "--output", str(tmp_path / "b"), "--labinabox", str(tmp_path / "missing"),
                        "--no-fetch-sources"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "lab-in-a-box add-ons" in r.stderr
    assert not (tmp_path / "b").exists()


def test_static_build_needs_the_catalogue_or_an_explicit_opt_out(tmp_path):
    r = subprocess.run([sys.executable, str(BUILD), "--output", str(tmp_path / "b"), "--no-fetch-sources"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no lab-in-a-box checkout" in r.stderr
    assert not (tmp_path / "b").exists()
    r = subprocess.run([sys.executable, str(BUILD), "--output", str(tmp_path / "b"), "--no-fetch-sources", "--no-labinabox"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "built without the lab-in-a-box catalogue" in r.stderr


# ── chapter sources outside rodeo-cli ───────────────────────────────────────

def test_chapter_sources_resolve_the_smlm_workshop_block():
    sources = {s["id"]: s for s in discovery.chapter_sources()}
    smlm = sources["smlm-workshop"]
    assert smlm["repo"] == "https://github.com/SUSE-Technical-Marketing/instruqt-SMLM"
    assert (smlm["ref"], smlm["path"], smlm["engine"], smlm["format"]) == ("main", "tracks/smlms", "lab-in-a-box", "instruqt")
    virt = sources["virt-workshop"]
    assert (virt["engine"], virt["profile"], virt["format"]) == ("suse-virt", "virt-workshop-aws", "markdown")


def test_chapter_sources_reject_incomplete_entries(tmp_path, monkeypatch):
    bad = tmp_path / "sources.yaml"
    bad.write_text("sources:\n  - {id: x, title: X, engine: rancher, profile: rancher, format: pdf, repo: r, ref: m, path: p}\n")
    monkeypatch.setattr(discovery, "SOURCES_FILE", bad)
    with pytest.raises(ValueError, match="chapter source x"):
        discovery.chapter_sources()


def test_source_fetch_commands_are_sparse_and_shallow(tmp_path):
    virt = next(s for s in discovery.chapter_sources() if s["id"] == "virt-workshop")
    clone, sparse = discovery.source_fetch_commands(virt, tmp_path / "v")
    assert clone[:10] == ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
                          "-c", "core.symlinks=false", "--branch"]
    assert sparse[-2:] == ["/docs/exercises/*.md", "/checks/*"]
    smlm = next(s for s in discovery.chapter_sources() if s["id"] == "smlm-workshop")
    assert discovery.source_fetch_commands(smlm, tmp_path / "s")[1][-2:] == [
        "/tracks/smlms/*/assignment.md", "/tracks/smlms/*/check-*"]


def test_fetch_sources_keeps_given_checkouts_and_reports_failed_clones(tmp_path):
    calls = []

    class Done:
        def __init__(self, rc, err=""):
            self.returncode, self.stderr = rc, err

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Done(1, "fatal: repository not found") if "virt-workshop" in " ".join(cmd) else Done(0)

    checkouts, errors = discovery.fetch_sources(tmp_path, {"smlm-workshop": Path("/given")}, run=run)
    assert checkouts == {"smlm-workshop": Path("/given")}
    assert errors == {"virt-workshop": "fatal: repository not found"}
    assert all("smlm-workshop" not in " ".join(c) for c in calls)


def test_instruqt_and_markdown_chapters(instruqt_source, markdown_source):
    sources = {s["id"]: s for s in discovery.chapter_sources()}
    smlm = discovery.source_chapters(sources["smlm-workshop"], instruqt_source)
    assert [(c["id"], c["title"], c["mins"], c["check"]) for c in smlm] == [
        ("intro", "Welcome!", 20, False), ("manage", "Managing distros", 20, True)]
    assert smlm[1]["body"] == "Body of 02-manage\n" and smlm[1]["check_script"].startswith("#!/bin/bash")
    assert smlm[0]["needs"] == ["smlm"]
    virt = discovery.source_chapters(sources["virt-workshop"], markdown_source)
    assert [(c["id"], c["title"], c["mins"], c["check"]) for c in virt] == [
        ("the-arrival", "Exercise 1: The Arrival", 30, True), ("bonus-final", "Bonus", 10, False)]
    assert virt[0]["check_script"] == "#!/bin/bash\necho ok\n"


def test_workshops_list_fetched_sources_after_the_bundled_ones(markdown_source):
    shops = discovery.workshops({"virt-workshop": markdown_source})["workshops"]
    assert [w["id"] for w in shops] == ["rancher-lab-config", "virt-workshop"]
    virt = shops[1]
    assert virt["source"] == "https://github.com/avaleror/suse-virt-workshop/tree/main/docs/exercises"
    assert yaml.safe_load(virt["plan"])["name"] and virt["profile"] == "virt-workshop-aws"
    assert shops[0]["profile"] == "rancher" and yaml.safe_load(shops[0]["plan"])["type"] == "rancher"


def _catalog(monkeypatch, **entries):
    from rodeo import catalog

    monkeypatch.setattr(catalog, "entries", lambda: entries)


def test_catalog_rodeos_are_chapter_sources_after_the_file(monkeypatch, tmp_path):
    _catalog(monkeypatch,
             ride={"name": "ride", "title": "Ride", "repo": "https://github.com/rider/ride", "ref": "", "engine": "rancher"},
             dup={"name": "dup", "repo": "https://github.com/avaleror/suse-virt-workshop.git", "ref": "main"})
    sources = discovery.chapter_sources()
    assert [s["id"] for s in sources] == ["smlm-workshop", "virt-workshop", "catalog-ride"]
    ride = sources[-1]
    assert (ride["format"], ride["path"], ride["profile"], ride["engine"], ride["title"]) == ("rmstory", "story", "", "rancher", "Ride")
    clone, sparse = discovery.source_fetch_commands(ride, tmp_path / "r")
    assert "--branch" not in clone and clone[-3:] == ["--", "https://github.com/rider/ride", str(tmp_path / "r")]
    assert sparse[-3:] == ["/story/*.md", "/story/chapters.yaml", "/story/stories/*.yaml"]


def test_rmstory_sources_read_a_story_directory(monkeypatch, tmp_path):
    _catalog(monkeypatch, ride={"name": "ride", "title": "Ride", "repo": "https://github.com/rider/ride", "ref": "v1",
                                "engine": "rancher"})
    story = tmp_path / "story"
    (story / "stories").mkdir(parents=True)
    (story / "01-hello.md").write_text('# Hello\n<span lang="en" id="hello.1" hist="short">Hi</span>\n')
    (story / "02-bye.md").write_text("# Bye\n")
    (story / "chapters.yaml").write_text("chapters:\n  - file: 01-hello.md\n    mins: 7\n    needs: [rancher]\n")
    (story / "stories" / "short.yaml").write_text("- hello.1\n")
    shops = discovery.workshops({"catalog-ride": tmp_path})["workshops"]
    ride = next(w for w in shops if w["id"] == "catalog-ride")
    assert [(c["id"], c["mins"], c["needs"]) for c in ride["chapters"]] == [("hello", 7, ["rancher"]), ("bye", 10, [])]
    assert ride["variants"] == {"short": ["hello.1"]} and ride["plan"] == "" and ride["profile"] == ""
    assert ride["source"] == "https://github.com/rider/ride/tree/v1/story"


def test_chapter_sources_accept_the_rmstory_format_in_the_file(monkeypatch, tmp_path):
    _catalog(monkeypatch)
    f = tmp_path / "sources.yaml"
    f.write_text("sources:\n  - {id: s, title: S, engine: rancher, profile: rancher, format: rmstory, repo: https://h/a/b, ref: main, path: story}\n")
    monkeypatch.setattr(discovery, "SOURCES_FILE", f)
    assert [s["id"] for s in discovery.chapter_sources()] == ["s"]


def test_static_build_with_local_sources(tmp_path, instruqt_source, markdown_source):
    out = tmp_path / "builder"
    r = subprocess.run([sys.executable, str(BUILD), "--output", str(out), "--no-fetch-sources", "--no-labinabox",
                        "--source", "smlm-workshop=" + str(instruqt_source),
                        "--source", "virt-workshop=" + str(markdown_source)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "3 workshops, 5 chapters" in r.stderr
    bad = subprocess.run([sys.executable, str(BUILD), "--output", str(tmp_path / "b2"), "--source", "nope"],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and "ID=CHECKOUT" in bad.stderr
