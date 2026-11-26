"""Chapter runner (plan WP1): order, check gating, skip, cleanup, agent shim.

The fixture is suse-virt-rodeo's real front matter and button/variable lines
(tests/fixtures/suse-virt-rodeo). The chapter scripts are generated per test:
each one appends "<chapter> <kind>" to $HOME/calls and exits with the code
the test asks for, so the call order is the assertion.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from rodeo.player import agent
from rodeo.player.runner import (
    Runner,
    RunnerError,
    load_track,
    parse_assignment,
    render_body,
    track_test,
)

FIXTURE = Path(__file__).parent / "fixtures" / "suse-virt-rodeo"
SCRIPTS = json.loads((FIXTURE / "scripts.json").read_text())
SLUGS = [
    "the-arrival-welcome",
    "the-subterranean-divide-cluster-prep",
    "the-flash-crash-first-vm",
    "the-rising-tide-live-migration",
    "the-invisible-intruder-networking",
    "the-unthinkable-error-snapshots",
    "the-stampede-automation",
    "a-new-horizon-whats-next",
]


def _track(tmp_path: Path, fail: dict[tuple[int, str], int] | None = None,
           body: dict[tuple[int, str], str] | None = None) -> Path:
    """Copy the fixture and write a stand-in for every script it lists."""
    fail = fail or {}
    body = body or {}
    track = tmp_path / "track"
    shutil.copytree(FIXTURE, track)
    for i, (dirname, kinds) in enumerate(sorted(SCRIPTS.items())):
        for kind in kinds:
            f = track / dirname / f"{kind}-kvm-host"
            extra = body.get((i + 1, kind), "")
            f.write_text(
                "#!/bin/bash\n"
                f'echo "{i + 1} {kind}" >> "$HOME/calls"\n'
                f"{extra}\n"
                f'echo "{kind} output for chapter {i + 1}"\n'
                f"exit {fail.get((i + 1, kind), 0)}\n"
            )
            f.chmod(0o755)
    return track


def _runner(tmp_path: Path, **kw) -> Runner:
    track = _track(tmp_path, kw.pop("fail", None), kw.pop("body", None))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return Runner(track, tmp_path / "state", home=str(home), kubeconfig=str(home / "kubeconfig"),
                  sandbox_id="k7q2mx", **kw)


def _calls(tmp_path: Path) -> list[str]:
    p = tmp_path / "home" / "calls"
    return p.read_text().splitlines() if p.exists() else []


def _reset_calls(tmp_path: Path) -> None:
    (tmp_path / "home" / "calls").unlink(missing_ok=True)


# -- loading -------------------------------------------------------------------

def test_eight_chapters_in_order_and_chapter_8_has_no_check():
    chapters = load_track(FIXTURE)
    assert [c.slug for c in chapters] == SLUGS
    assert not chapters[7].scripts
    assert not chapters[7].has("check")


def test_front_matter_with_nested_quotes_loads(tmp_path):
    chapters = load_track(_track(tmp_path))
    assert chapters[0].title.endswith("\U0001F3E6 Chapter 1: The Arrival</span>")
    assert "\\U" not in chapters[0].title
    assert [t.title for t in chapters[0].tabs] == ["SUSE Virtualization UI", "Cluster Terminal", "Rancher Prime UI"]
    assert [(t.type, t.port) for t in chapters[0].tabs] == [("service", 8443), ("terminal", None), ("service", 30002)]


def test_scripts_found_per_chapter(tmp_path):
    chapters = load_track(_track(tmp_path))
    assert sorted(chapters[1].scripts) == ["check", "cleanup", "setup", "solve"]
    assert sorted(chapters[2].scripts) == ["check", "solve"]


def test_parse_assignment_without_front_matter():
    assert parse_assignment("just text") == ({}, "just text")


# -- check / next ----------------------------------------------------------------

def test_failing_check_does_not_advance(tmp_path):
    r = _runner(tmp_path, fail={(1, "check"): 1})
    assert r.enter().ok
    res = r.check()
    assert not res.ok
    assert r.current == 0
    assert res.results[0].exit_code == 1
    assert "check output for chapter 1" in res.results[0].output


def test_passing_check_advances_and_runs_next_setup(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    res = r.check()
    assert res.ok and r.current == 1 and r.entered
    assert _calls(tmp_path) == ["1 setup", "1 check", "2 setup"]


def test_check_on_chapter_2_runs_cleanup_then_chapter_3_has_no_setup(tmp_path):
    r = _runner(tmp_path, fail={(2, "cleanup"): 1})
    r.enter()
    r.check()
    _reset_calls(tmp_path)
    res = r.check()
    assert _calls(tmp_path) == ["2 check", "2 cleanup"]
    assert res.ok, "a failing cleanup is recorded and the move still happens"
    assert [x.kind for x in res.results] == ["check", "cleanup"]
    assert res.results[1].exit_code == 1
    assert r.current == 2 and r.entered


def test_failed_setup_blocks_check_until_retry(tmp_path):
    r = _runner(tmp_path, fail={(1, "setup"): 3})
    res = r.enter()
    assert not res.ok and not r.entered
    with pytest.raises(RunnerError):
        r.check()


def test_chapter_8_offers_next_not_check(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    r.skip(7)
    with pytest.raises(RunnerError):
        r.check()
    res = r.next()
    assert res.ok and res.message == "track complete"
    assert 7 in r.completed


def test_next_refused_on_a_chapter_with_check(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    with pytest.raises(RunnerError):
        r.next()


# -- skip / back -------------------------------------------------------------------

def test_skip_1_to_4_runs_setup_solve_cleanup_per_chapter_in_order(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    _reset_calls(tmp_path)
    res = r.skip(3)
    # Chapter 1 was entered (its setup ran on Start); 2 and 3 were not, so their
    # setup runs before their solve, like pressing Skip once per chapter.
    assert _calls(tmp_path) == ["1 solve", "2 setup", "2 solve", "2 cleanup", "3 solve", "4 setup"]
    assert res.ok and r.current == 3 and r.entered
    assert r.completed == [0, 1, 2]
    assert r.skipped == [0, 1, 2]


def test_skipped_lists_only_chapters_a_skip_completed(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    assert r.check().ok          # chapter 1 passed by its check
    r.skip(3)                    # 2 and 3 solved for the student
    assert r.completed == [0, 1, 2] and r.skipped == [1, 2]
    # An older state file without the key still loads.
    state = tmp_path / "state" / "state.json"
    data = json.loads(state.read_text())
    del data["skipped"]
    state.write_text(json.dumps(data))
    again = Runner(tmp_path / "track", tmp_path / "state", home=str(tmp_path / "home"),
                   kubeconfig=str(tmp_path / "home" / "kubeconfig"), sandbox_id="k7q2mx")
    assert again.completed == [0, 1, 2] and again.skipped == []


def test_failing_solve_stops_the_skip(tmp_path):
    r = _runner(tmp_path, fail={(2, "solve"): 1})
    r.enter()
    _reset_calls(tmp_path)
    res = r.skip(3)
    assert _calls(tmp_path) == ["1 solve", "2 setup", "2 solve"]
    assert not res.ok and r.current == 1 and not r.entered
    assert "02-the-subterranean-divide-cluster-prep" in res.message


def test_skip_backwards_refused_and_back_runs_nothing(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    r.skip(3)
    with pytest.raises(RunnerError):
        r.skip(1)
    _reset_calls(tmp_path)
    assert r.back(1).ok
    assert _calls(tmp_path) == []
    assert r.current == 1


def test_state_survives_a_restart(tmp_path):
    r = _runner(tmp_path)
    r.enter()
    r.skip(4)
    again = Runner(r.chapters[0].path.parent, tmp_path / "state", home=r.home, kubeconfig=r.kubeconfig)
    assert again.current == 4 and again.entered and again.completed == [0, 1, 2, 3]


# -- agent shim and variables ------------------------------------------------------

def test_agent_variable_set_reaches_the_renderer_only(tmp_path):
    r = _runner(tmp_path, body={(4, "setup"): 'agent variable set PAYMENT_GATEWAY_IP 192.0.2.10'})
    r.enter()
    r.skip(3)
    assert r.variables()["PAYMENT_GATEWAY_IP"] == "192.0.2.10"
    assert "192.0.2.10" in r.render(3)
    assert oct(r.vars_file.stat().st_mode & 0o777) == "0o600"
    # A process the runner did not start sees neither the value nor the file.
    out = subprocess.run(["env"], capture_output=True, text=True, env={"PATH": os.environ["PATH"]}).stdout
    assert "192.0.2.10" not in out and agent.ENV_VARS_FILE not in out
    assert agent.ENV_VARS_FILE not in os.environ


def test_clean_path_has_no_agent(tmp_path):
    r = _runner(tmp_path)
    code, out, _, _ = r.run_command(["/bin/sh", "-c", "command -v agent || echo NOAGENT"],
                                    cwd=tmp_path, timeout=10, with_agent=False)
    assert code == 0 and "NOAGENT" in out
    code, out, _, _ = r.run_command(["/bin/sh", "-c", "command -v agent"], cwd=tmp_path, timeout=10)
    assert code == 0 and out.strip() == str(r.state_dir / "bin" / "agent")


def test_scripts_get_a_wiped_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    r = _runner(tmp_path)
    _, out, _, _ = r.run_command(["/usr/bin/env"], cwd=tmp_path, timeout=10)
    keys = {line.split("=", 1)[0] for line in out.splitlines() if "=" in line}
    assert "AWS_SECRET_ACCESS_KEY" not in keys
    assert {"PATH", "HOME", "KUBECONFIG", agent.ENV_VARS_FILE} <= keys


def test_agent_cli_get_and_errors(tmp_path, monkeypatch):
    vars_file = tmp_path / "v.json"
    monkeypatch.setenv(agent.ENV_VARS_FILE, str(vars_file))
    assert agent.main(["variable", "set", "RANCHER_PASSWORD", "s3cret"]) == 0
    assert agent.main(["variable", "get", "MISSING"]) == 1
    assert agent.main(["variable", "set", "bad-key", "x"]) == 2
    assert agent.main(["checkpoint"]) == 2
    monkeypatch.delenv(agent.ENV_VARS_FILE)
    assert agent.main(["variable", "set", "A", "b"]) == 2
    assert json.loads(vars_file.read_text()) == {"RANCHER_PASSWORD": "s3cret"}


# -- rendering --------------------------------------------------------------------

def test_render_substitutes_every_fixture_variable(tmp_path):
    r = _runner(tmp_path)
    r.set_variable("RANCHER_PASSWORD", "<b>pw&</b>")
    r.set_variable("PAYMENT_GATEWAY_IP", "192.0.2.10")
    for i in range(len(r.chapters)):
        out = r.render(i)
        assert "Instruqt-Var" not in out
        assert "[button" not in out
        assert "<b>pw&</b>" not in out, "script values are escaped"
    assert "&lt;b&gt;pw&amp;&lt;/b&gt;" in "".join(r.render(i) for i in range(8))
    assert "k7q2mx" in "".join(r.render(i) for i in range(8)), "_SANDBOX_ID comes from the lab id"


def test_missing_variable_is_visible():
    out = render_body('[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]', {})
    assert out == '<span class="rodeo-var-missing">RANCHER_PASSWORD not set</span>'


@pytest.mark.parametrize("line,tab,label", [
    ('[button label="Cluster Terminal" variant="success"](tab-1)', 1, "Cluster Terminal"),
    ('[button label="Rancher Prime UI" variant="success"](tab-2)', 2, "Rancher Prime UI"),
    ('[button label="SUSE Virtualization UI" variant="success"](tab-0)', 0, "SUSE Virtualization UI"),
    ('[button label="<span id="ch1.intro1.2" lang="nolang" no>SUSE Virtualization</span> UI" variant="success"](tab-0)',
     0, '<span id="ch1.intro1.2" lang="nolang" no>SUSE Virtualization</span> UI'),
])
def test_button_rewrite_for_the_four_real_labels(line, tab, label):
    assert render_body(f"before {line} after", {}) == (
        f'before <button type="button" class="rodeo-tab-button" data-tab="{tab}">{label}</button> after'
    )


# -- limits ------------------------------------------------------------------------

def test_check_timeout_kills_the_process_group(tmp_path):
    marker = tmp_path / "home" / "child-alive"
    r = _runner(tmp_path, check_timeout=1,
                body={(1, "check"): f'(sleep 3; touch "{marker}") & sleep 30'})
    r.enter()
    start = time.monotonic()
    res = r.check()
    assert time.monotonic() - start < 10
    assert not res.ok and res.results[0].timed_out
    assert "timed out" in res.results[0].output
    time.sleep(3.5)
    assert not marker.exists(), "the background child was killed with its group"


def test_output_is_capped(tmp_path):
    r = _runner(tmp_path, output_cap=1000, body={(1, "check"): "head -c 50000 /dev/zero | tr '\\0' x"})
    r.enter()
    out = r.check().results[0].output
    assert out.startswith("[... ")
    assert len(out) < 1200


def test_history_records_codes_not_output(tmp_path):
    r = _runner(tmp_path, body={(1, "setup"): "echo SECRET-PASSWORD"})
    r.enter()
    history = (r.state_dir / "history.jsonl").read_text()
    assert "SECRET-PASSWORD" not in history
    assert json.loads(history.splitlines()[0])["kind"] == "setup"


# -- track test --------------------------------------------------------------------

def test_track_test_runs_setup_check_solve_check_cleanup(tmp_path):
    r = _runner(tmp_path)
    report = track_test(r)
    assert [t.chapter for t in report] == sorted(SCRIPTS)
    assert all(t.ok for t in report)
    calls = _calls(tmp_path)
    assert calls[:5] == ["1 setup", "1 check", "1 solve", "1 check", "2 setup"]
    assert "2 cleanup" in calls
    assert report[7].first_check is None


def test_track_test_stops_on_failing_final_check(tmp_path):
    r = _runner(tmp_path, fail={(3, "check"): 1})
    report = track_test(r)
    assert len(report) == 3 and not report[-1].ok


# -- the real track ----------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("RODEO_PLAYER_TRACK"),
                    reason="set RODEO_PLAYER_TRACK to a suse-virt-rodeo checkout")
def test_real_track_loads_and_renders():
    chapters = load_track(os.environ["RODEO_PLAYER_TRACK"])
    assert [c.slug for c in chapters] == SLUGS
    for c in chapters:
        out = render_body(c.body, {"RANCHER_PASSWORD": "x", "PAYMENT_GATEWAY_IP": "y", "_SANDBOX_ID": "z"})
        assert "Instruqt-Var" not in out and "[button" not in out
        assert sorted(c.scripts) == sorted(SCRIPTS[c.dirname])


def test_failing_setup_of_a_skipped_chapter_stops_the_skip(tmp_path):
    r = _runner(tmp_path, fail={(2, "setup"): 1})
    r.enter()
    _reset_calls(tmp_path)
    res = r.skip(3)
    assert _calls(tmp_path) == ["1 solve", "2 setup"]
    assert not res.ok and r.current == 1 and not r.entered


def test_track_test_judges_the_first_check_against_expectations(tmp_path):
    from rodeo.player.runner import load_expectations, track_test

    r = _runner(tmp_path)
    names = [c.dirname for c in r.chapters]
    exp = tmp_path / "expect.yaml"
    # In this fixture every check passes, before and after the solve.
    exp.write_text(f"first_check:\n  {names[0]}: fail\n  {names[1]}: pass\n")
    report = track_test(r, stop_on_failure=False, expect=load_expectations(exp))
    assert not report[0].first_check_as_expected and not report[0].ok
    assert report[1].first_check_as_expected


def test_bundled_expectations_cover_the_real_track():
    from pathlib import Path

    import rodeo
    from rodeo.player.runner import load_expectations

    exp = load_expectations(Path(rodeo.__file__).parent / "data" / "player" / "suse-virt-rodeo.expect.yaml")
    assert exp["01-the-arrival-welcome"] == "pass" and exp["03-the-flash-crash-first-vm"] == "fail"
    assert set(exp.values()) <= {"pass", "fail", "any"}


def test_bad_expectation_is_refused(tmp_path):
    from rodeo.player.runner import load_expectations

    exp = tmp_path / "e.yaml"
    exp.write_text("first_check:\n  01-x: maybe\n")
    with pytest.raises(ValueError):
        load_expectations(exp)


def test_rmstory_spans_are_stripped_like_the_track_ci():
    from rodeo.player.runner import strip_rmstory_spans

    src = ('<span id="a.67" lang="en" hist="vertrex-bank">\nChapter 4\n=====\n</span>\n'
           '<span class="keep">x</span> <span lang="en"><span no>**Rack 4**</span></span>')
    out = strip_rmstory_spans(src)
    assert out == '\nChapter 4\n=====\n\n<span class="keep">x</span> **Rack 4**'


def test_real_track_bodies_render_without_rmstory_spans():
    import os
    from pathlib import Path

    from rodeo.player.runner import load_track, render_body, strip_rmstory_spans

    track = Path(os.environ.get("RODEO_PLAYER_TRACK", Path.home() / "GitHub" / "suse-virt-rodeo"))
    if not (track / "04-the-rising-tide-live-migration").is_dir():
        pytest.skip("no suse-virt-rodeo checkout")
    for ch in load_track(track):
        out = render_body(strip_rmstory_spans(ch.body), {})
        assert 'lang="en"' not in out and "hist=" not in out


URLS = {"8443": "https://hv-ab12cd34.example.org/", "30002": "https://rancher-ab12cd34.example.org/"}
SBX = '[[ Instruqt-Var key="_SANDBOX_ID" hostname="kvm-host" ]]'


def test_instruqt_sandbox_links_point_at_this_lab():
    from rodeo.player.runner import render_body

    body = (f'> <a href="https://kvm-host.{SBX}.instruqt.io:8443">https://kvm-host.{SBX}.instruqt.io:8443</a>\n'
            "Rancher: https://kvm-host.abc123.instruqt.io:30002/dashboard/home and "
            "home: https://kvm-host.abc123.instruqt.io/\n")
    out = render_body(body, {"_SANDBOX_ID": "k7q2mx"}, URLS)
    assert "instruqt" not in out
    assert out.count('https://hv-ab12cd34.example.org/') == 2
    assert "https://rancher-ab12cd34.example.org/dashboard/home" in out
    assert "home: /" in out  # no tab for 443: the player itself


def test_a_variable_inside_a_tag_is_plain_text_not_a_chip():
    from rodeo.player.runner import render_body

    out = render_body('<a href="https://x/[[ Instruqt-Var key="K" ]]">go</a> [[ Instruqt-Var key="K" ]]',
                      {"K": 'a"b'})
    assert '<a href="https://x/a&quot;b">go</a>' in out
    assert out.endswith('<code class="rodeo-var" title="Click to copy">a&quot;b</code>')
