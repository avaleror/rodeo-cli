"""WP4 seams: event sinks, begin_operation, plugin MCP tools, the MCP lab allowlist."""
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from rodeo import hooks, plugins, redact
from rodeo.engine.runner import DeployRunner, LogLine, PhaseDone, PhaseStarted
from rodeo.mcp import tools as mcp_tools
from rodeo.mcp.server import RodeoMCPServer


@pytest.fixture(autouse=True)
def _clean_hooks(monkeypatch):
    monkeypatch.setattr(hooks, "_sinks", [])
    monkeypatch.setattr(hooks, "_operation_hook", None)
    monkeypatch.setattr(mcp_tools, "_PLUGIN_TOOLS", {})
    monkeypatch.setattr(plugins, "_loaded", True)  # no real entry points unless a test asks
    from rodeo import inventory as inv_mod

    real_build = inv_mod.build_inventory
    monkeypatch.setattr(inv_mod, "build_inventory",
                        lambda cfg: {} if cfg.get("type") == "fake" else real_build(cfg))


def _req(method, params=None, req_id=1):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


# --- event sinks --------------------------------------------------------------


def test_no_sink_is_a_plain_pass_through(fake_profile, fake_cfg, tmp_path):
    events = list(DeployRunner(fake_cfg, tmp_path).run())
    assert [type(e).__name__ for e in events][-1] == "DeployComplete"
    from rodeo.events_log import write_event

    assert hooks.event_sinks() == [write_event]  # only the built-in events file


def test_plugin_registered_sink_receives_events(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """A rodeo.plugins entry point whose register() adds a sink gets the runner's events."""
    got: list[dict] = []

    class _EP:
        name = "test-plugin"

        def load(self):
            return lambda: hooks.register_event_sink(got.append)

    monkeypatch.setattr(plugins, "_loaded", False)
    monkeypatch.setattr(plugins.metadata, "entry_points", lambda group: [_EP()])
    events = list(DeployRunner(fake_cfg, tmp_path).run())
    assert len(got) == len(events)
    first = got[0]
    assert set(first) >= {"ts", "lab", "phase", "kind", "text"}
    started = [r for r in got if r["kind"] == "PhaseStarted"]
    assert [r["phase"] for r in started] == ["alpha", "beta", "gamma"]
    done = [r for r in got if r["kind"] == "PhaseDone"]
    assert all("elapsed" in r for r in done)


def test_failing_sink_never_fails_the_deploy(fake_profile, fake_cfg, tmp_path, caplog):
    def boom(record):
        raise RuntimeError("sink down")

    seen: list[dict] = []
    hooks.register_event_sink(boom)
    hooks.register_event_sink(seen.append)
    events = list(DeployRunner(fake_cfg, tmp_path).run())
    assert type(events[-1]).__name__ == "DeployComplete"
    assert len(seen) == len(events)  # the next sink still runs
    assert "sink down" in caplog.text


def test_sink_gets_redacted_copy_and_the_ui_the_raw_event(fake_profile, fake_cfg, tmp_path):
    """Credential literals, URL userinfo and a secret registered mid-run are masked."""
    late = "LateTokenFromThePhase99"

    def run_phase(phase, runner, vars_file):
        runner._last_rc = 0
        yield LogLine(f"login with {fake_cfg['credentials']['harvester_token']}")
        yield LogLine("pull https://bob:hunter2pw@registry.example/x")
        redact.register_secret(late)  # e.g. a token fetched from a node during the phase
        yield LogLine(f"joined with {late}")

    fake_profile.run_phase = run_phase
    got: list[dict] = []
    hooks.register_event_sink(got.append)
    raw = [e.line for e in DeployRunner(fake_cfg, tmp_path).run() if isinstance(e, LogLine)]
    text = "\n".join(r["text"] for r in got)
    assert "test-token-123" not in text and "hunter2pw" not in text and late not in text
    assert "***" in text
    assert any("test-token-123" in line for line in raw)  # the deploy itself is unchanged


def test_resolved_secret_is_masked(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """A ??env: value resolved by load_config(secrets="all") is masked in sink records."""
    from rodeo import config

    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(
        "name: t\nregistry:\n  mirror: ??env:RODEO_HOOK_TEST\n")
    monkeypatch.setenv("RODEO_HOOK_TEST", "EnvResolvedValue123")
    config.trust_lab(lab)
    cfg = config.load_config(str(lab / "rodeo-plan.yaml"), secrets="all")
    assert cfg["registry"]["mirror"] == "EnvResolvedValue123"
    cfg = {**fake_cfg, "registry": cfg["registry"], "plan_dir": cfg["plan_dir"]}

    def run_phase(phase, runner, vars_file):
        runner._last_rc = 0
        yield LogLine("mirror is EnvResolvedValue123")

    fake_profile.run_phase = run_phase
    got: list[dict] = []
    hooks.register_event_sink(got.append)
    list(DeployRunner(cfg, tmp_path).run())
    logs = [r for r in got if r["kind"] == "LogLine"]
    assert logs and all("EnvResolvedValue123" not in r["text"] for r in logs)
    assert logs[0]["lab"] == os.path.realpath(lab)


def test_sink_mutation_does_not_reach_other_sinks(fake_profile, fake_cfg, tmp_path):
    def mutate(record):
        record["text"] = "changed"
        record["kind"] = "changed"

    seen: list[dict] = []
    hooks.register_event_sink(mutate)
    hooks.register_event_sink(seen.append)
    list(DeployRunner(fake_cfg, tmp_path).run())
    assert all(r["kind"] != "changed" for r in seen)


def test_event_record_schema():
    rec = hooks.event_record(PhaseDone("vms", 1.23456), lab="/l", phase=None, redact=str)
    assert rec["phase"] == "vms" and rec["kind"] == "PhaseDone" and rec["elapsed"] == 1.235
    rec = hooks.event_record(LogLine("x"), lab="/l", phase="vms", redact=str)
    assert rec["phase"] == "vms" and rec["text"] == "x" and "rc" not in rec
    assert hooks.event_record(PhaseStarted("a"), lab=None, phase=None, redact=str)["text"] == ""


# --- begin_operation --------------------------------------------------------


def test_begin_operation_defaults_to_none(tmp_path):
    assert hooks.begin_operation(tmp_path, "deploy") is None


def test_begin_operation_calls_the_hook_with_realpath(tmp_path):
    calls = []
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    hooks.register_operation_hook(lambda lab, action: calls.append((lab, action)) or "op-1")
    assert hooks.begin_operation(link, "up") == "op-1"
    assert calls == [(os.path.realpath(tmp_path), "up")]


def test_begin_operation_hook_error_is_none(tmp_path, caplog):
    def bad(lab, action):
        raise RuntimeError("store down")

    hooks.register_operation_hook(bad)
    assert hooks.begin_operation(tmp_path, "deploy") is None
    assert "store down" in caplog.text


def test_nested_operations_share_one_id(tmp_path):
    calls = []
    hooks.register_operation_hook(lambda lab, action: calls.append(action) or "op-1")
    with hooks.operation(tmp_path, "fleet.up") as op:
        assert hooks.begin_operation(tmp_path, "fleet.deploy") == op == "op-1"
    assert calls == ["fleet.up"]
    hooks.begin_operation(tmp_path, "fleet.deploy")
    assert calls == ["fleet.up", "fleet.deploy"]


def _stub_deploy(monkeypatch, tmp_path):
    from rodeo.commands import deploy as deploy_mod

    monkeypatch.setattr(deploy_mod, "is_root", lambda: True)
    monkeypatch.setattr("rodeo.hostos.kvm_host_problem", lambda: None)
    monkeypatch.setattr("rodeo.preflight.detect_host", lambda: {})
    monkeypatch.setattr(deploy_mod, "find_ansible_root", lambda cfg: tmp_path)
    (tmp_path / "ansible").mkdir(exist_ok=True)
    (tmp_path / "ansible" / "playbook.yml").write_text("[]\n")
    monkeypatch.setattr(deploy_mod, "run_preflight", lambda cfg, root, phases_to_run=None: True)
    seen: list = []
    monkeypatch.setattr(deploy_mod, "execute_deploy",
                        lambda cfg, root, **kw: seen.append((root, kw)) or 0)
    return deploy_mod, seen


@pytest.mark.parametrize("hook", [None, lambda lab, action: None])
def test_begin_operation_none_does_not_change_deploy(monkeypatch, tmp_path, hook):
    """deploy runs the same pipeline call with no hook and with a hook returning None."""
    from rodeo import config

    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text("name: t\n")
    config.trust_lab(lab)
    deploy_mod, seen = _stub_deploy(monkeypatch, tmp_path)
    calls = []
    if hook is not None:
        hooks.register_operation_hook(lambda lab_, a: calls.append((lab_, a)) or hook(lab_, a))
    result = CliRunner().invoke(deploy_mod.deploy_cmd, ["--config", str(lab / "rodeo-plan.yaml")])
    assert result.exit_code == 0, result.output
    assert seen == [(tmp_path, {"from_phase": None, "install_collections": True, "force": False,
                                "reconcile": True, "include_guarded": False, "ansible_verbose": 0,
                                "tui": None})]
    if hook is not None:
        assert calls == [(os.path.realpath(lab), "deploy")]


def test_deploy_check_does_not_begin_an_operation(monkeypatch, tmp_path):
    from rodeo import config

    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text("name: t\n")
    config.trust_lab(lab)
    deploy_mod, _ = _stub_deploy(monkeypatch, tmp_path)
    calls = []
    hooks.register_operation_hook(lambda lab_, a: calls.append(a))
    CliRunner().invoke(deploy_mod.deploy_cmd, ["--config", str(lab / "rodeo-plan.yaml"), "--check"])
    assert calls == []


def test_mcp_mutating_call_begins_operation_and_argv_is_unchanged(monkeypatch, tmp_path):
    seen: list = []
    monkeypatch.setattr(mcp_tools, "run_rodeo",
                        lambda args, **kw: seen.append((args, kw)) or (0, "ok"))
    server = RodeoMCPServer(allow_write=True, lab_dir=tmp_path)
    server.handle(_req("tools/call", {"name": "rodeo_stop", "arguments": {}}))
    calls = []
    hooks.register_operation_hook(lambda lab_, a: calls.append((lab_, a)))  # returns None
    server.handle(_req("tools/call", {"name": "rodeo_stop", "arguments": {}}))
    server.handle(_req("tools/call", {"name": "rodeo_status", "arguments": {}}))
    assert seen[0] == seen[1]
    assert calls == [(os.path.realpath(tmp_path), "mcp.rodeo_stop")]  # read tools: none


# --- plugin MCP tools -------------------------------------------------------


def _plugin_tool(name="rodeo_ai_hello", **kw):
    return mcp_tools.Tool(name, "Plugin tool.", handler=lambda p: "hi", **kw)


def test_plugin_tool_cannot_shadow_a_builtin():
    for name in ("rodeo_plan", "rodeo_deploy", "rodeo_clean"):
        with pytest.raises(ValueError, match="built in"):
            mcp_tools.register_mcp_tool(_plugin_tool(name), mutating=False, lab_scoped=False)
    assert mcp_tools.tool_map(allow_write=True)["rodeo_plan"].command == ("plan",)


def test_plugin_tool_must_declare_flags_and_be_unique():
    with pytest.raises(TypeError):
        mcp_tools.register_mcp_tool(_plugin_tool())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        mcp_tools.register_mcp_tool(_plugin_tool(), mutating=None, lab_scoped=False)
    mcp_tools.register_mcp_tool(_plugin_tool(), mutating=False, lab_scoped=False)
    with pytest.raises(ValueError, match="already"):
        mcp_tools.register_mcp_tool(_plugin_tool(), mutating=False, lab_scoped=False)
    with pytest.raises(ValueError):
        mcp_tools.register_mcp_tool(_plugin_tool("Bad Name"), mutating=False, lab_scoped=False)


def test_mutating_plugin_tool_needs_allow_write():
    mcp_tools.register_mcp_tool(_plugin_tool("rodeo_ai_fix"), mutating=True, lab_scoped=True)
    mcp_tools.register_mcp_tool(_plugin_tool("rodeo_ai_look"), mutating=False, lab_scoped=False)
    ro = mcp_tools.tool_map(allow_write=False)
    assert "rodeo_ai_fix" not in ro and "rodeo_ai_look" in ro
    rw = mcp_tools.tool_map(allow_write=True)
    assert rw["rodeo_ai_fix"].mutating and rw["rodeo_ai_fix"].lab_scoped
    assert "lab_dir" in rw["rodeo_ai_fix"].input_schema()["properties"]


def test_plugin_tool_goes_through_the_lab_allowlist(tmp_path, monkeypatch):
    from rodeo import lab_allowlist

    listed = tmp_path / "listed"
    listed.mkdir()
    (listed / "rodeo-plan.yaml").write_text("name: x\n")
    lab_allowlist.add_lab(listed)
    mcp_tools.register_mcp_tool(_plugin_tool("rodeo_ai_fix"), mutating=True, lab_scoped=True)
    server = RodeoMCPServer(allow_write=True, lab_dir=listed)
    ok = server.handle(_req("tools/call", {"name": "rodeo_ai_fix", "arguments": {}}))["result"]
    assert ok["isError"] is False
    bad = server.handle(_req("tools/call", {"name": "rodeo_ai_fix",
                                            "arguments": {"lab_dir": str(tmp_path)}}))["result"]
    assert bad["isError"] is True and "lab list" in bad["content"][0]["text"]


# --- lab allowlist ----------------------------------------------------------


def _lab(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "rodeo-plan.yaml").write_text("name: x\n")
    return path


@pytest.fixture
def mcp_runs(monkeypatch):
    seen: list = []
    monkeypatch.setattr(mcp_tools, "run_rodeo",
                        lambda args, **kw: seen.append((args, kw)) or (0, "ok"))
    return seen


def _call(server, name, arguments):
    return server.handle(_req("tools/call", {"name": name, "arguments": arguments}))["result"]


def test_unknown_lab_dir_rejected_only_after_lab_add(tmp_path, monkeypatch, mcp_runs):
    from rodeo.commands import lab_cmd as lab_mod

    listed, other = _lab(tmp_path / "listed"), _lab(tmp_path / "other")
    server = RodeoMCPServer(allow_write=True, lab_dir=listed)
    assert _call(server, "rodeo_status", {"lab_dir": str(other)})["isError"] is False

    monkeypatch.setattr(lab_mod, "_interactive", lambda: True)
    result = CliRunner().invoke(lab_mod.lab_cmd, ["add", str(listed)])
    assert result.exit_code == 0, result.output
    assert oct(os.stat(tmp_path / ".rodeo" / "labs.yaml").st_mode & 0o777) == "0o600"

    assert _call(server, "rodeo_status", {"lab_dir": str(listed)})["isError"] is False
    refused = _call(server, "rodeo_status", {"lab_dir": str(other)})
    assert refused["isError"] is True and "lab list" in refused["content"][0]["text"]
    assert len(mcp_runs) == 2  # the refused call never ran rodeo


def test_allowlist_resolves_symlinks_dotdot_and_relative_paths(tmp_path, mcp_runs):
    from rodeo import lab_allowlist

    listed, other = _lab(tmp_path / "listed"), _lab(tmp_path / "other")
    (listed / "escape").symlink_to(other)
    lab_allowlist.add_lab(listed)
    server = RodeoMCPServer(allow_write=True, lab_dir=listed)
    for lab_dir in (str(listed / "escape"), str(listed / ".." / "other"), "../other"):
        res = _call(server, "rodeo_status", {"lab_dir": lab_dir})
        assert res["isError"] is True, lab_dir
    # A path argument relative to the lab, escaping it, is refused too.
    res = _call(server, "rodeo_up", {"dir": "../other"})
    assert res["isError"] is True and "dir" in res["content"][0]["text"]
    res = _call(server, "rodeo_init", {"target_dir": "escape"})
    assert res["isError"] is True
    assert _call(server, "rodeo_init", {"target_dir": "sub"})["isError"] is False
    assert mcp_runs and all(kw["cwd"] == listed for _, kw in mcp_runs)


def test_allowlist_covers_every_path_argument(tmp_path, mcp_runs):
    from rodeo import lab_allowlist

    listed = _lab(tmp_path / "listed")
    outside = tmp_path / "outside.yaml"
    outside.write_text("x: 1\n")
    lab_allowlist.add_lab(listed)
    server = RodeoMCPServer(allow_write=True, lab_dir=listed)
    cases = [
        ("rodeo_instances_list", {"dir": str(tmp_path)}),
        ("rodeo_fleet_status", {"file": str(outside)}),
        ("rodeo_fleet_doctor", {"file": str(outside)}),
        ("rodeo_new", {"name": "n", "from_zip": str(outside)}),
        ("rodeo_new", {"name": "n", "from_lab_json": str(outside)}),
        ("rodeo_new", {"name": "n", "from_base": "https://github.com/x/y"}),
        ("rodeo_new", {"name": "n", "from_base": str(outside)}),
        ("rodeo_up", {"dir": str(tmp_path / "elsewhere")}),
    ]
    for name, args in cases:
        assert _call(server, name, args)["isError"] is True, (name, args)
    assert mcp_runs == []
    assert _call(server, "rodeo_new", {"name": "n", "from_base": "harvester"})["isError"] is False
    inside = listed / "workshop.yaml"
    inside.write_text("x: 1\n")
    assert _call(server, "rodeo_fleet_status", {"file": str(inside)})["isError"] is False


def test_allowlist_refuses_a_listed_dir_without_a_plan(tmp_path, mcp_runs):
    """rodeo would fall back to ~/.rodeo/last_lab, which may be outside the list."""
    from rodeo import lab_allowlist

    empty = tmp_path / "empty"
    empty.mkdir()
    lab_allowlist.add_lab(empty)
    server = RodeoMCPServer(allow_write=True, lab_dir=empty)
    assert _call(server, "rodeo_deploy", {})["isError"] is True
    assert _call(server, "rodeo_up", {"profile": "rancher"})["isError"] is True
    assert _call(server, "rodeo_up", {"dir": str(empty / "new")})["isError"] is False
    assert _call(server, "rodeo_init", {})["isError"] is False


def test_empty_or_corrupt_list_refuses_everything(tmp_path, mcp_runs):
    from rodeo import lab_allowlist

    lab = _lab(tmp_path / "lab")
    lab_allowlist.add_lab(lab)
    lab_allowlist.remove_lab(lab)
    server = RodeoMCPServer(allow_write=True, lab_dir=lab)
    assert _call(server, "rodeo_status", {})["isError"] is True
    lab_allowlist.allowlist_path().write_text("labs: {not: a list}\n")
    res = _call(server, "rodeo_status", {})
    assert res["isError"] is True and "corrupt" in res["content"][0]["text"]
    assert mcp_runs == []


def test_no_list_warns_once_and_accepts_any_dir(tmp_path, capsys, mcp_runs):
    from io import StringIO

    from rodeo.mcp.server import serve

    frames = StringIO(json.dumps(_req("tools/call", {"name": "rodeo_status",
                                                     "arguments": {"lab_dir": str(tmp_path)}})) + "\n")
    out = StringIO()
    serve(stdin=frames, stdout=out, lab_dir=tmp_path)
    err = capsys.readouterr().err
    assert err.count("accept any directory") == 1
    assert "refused" not in out.getvalue()
    assert len(mcp_runs) == 1


def test_lab_add_and_remove_need_a_tty(tmp_path, monkeypatch):
    from rodeo import lab_allowlist
    from rodeo.commands import lab_cmd as lab_mod

    monkeypatch.setattr(lab_mod, "_interactive", lambda: False)
    assert CliRunner().invoke(lab_mod.lab_cmd, ["add", str(tmp_path)]).exit_code == 2
    assert lab_allowlist.load_labs() is None
    assert CliRunner().invoke(lab_mod.lab_cmd, ["remove", str(tmp_path)]).exit_code == 2
    listing = CliRunner().invoke(lab_mod.lab_cmd, ["list"])
    assert listing.exit_code == 0 and "any directory" in listing.output


def test_lab_list_file_is_not_followed_through_a_symlink(tmp_path):
    from rodeo import lab_allowlist
    from rodeo.config import ConfigError

    target = tmp_path / "elsewhere.yaml"
    target.write_text("labs: []\n")
    path = lab_allowlist.allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    with pytest.raises(ConfigError):
        lab_allowlist.add_lab(tmp_path)
    with pytest.raises(ConfigError):
        lab_allowlist.load_labs()
