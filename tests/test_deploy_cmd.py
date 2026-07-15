"""rodeo deploy — plain-mode output must not choke on raw tool output."""
from __future__ import annotations

from rodeo.commands import deploy as deploy_mod
from rodeo.engine.runner import DeployRunner, LogLine


def test_deploy_plain_survives_bracketed_tool_output(monkeypatch, tmp_path, capsys):
    """Regression: hauler's own log format ("adding file [/tmp/foo] to the store
    as [...]") crashed the whole deploy — Rich's console.print() interprets
    "[...]" as markup, and a bracket value starting with "/" parses as an
    (unmatched) closing tag, raising rich.errors.MarkupError and killing the
    process mid-run instead of just printing the line."""

    lines = [
        "adding file [/tmp/leap-micro-selfinstall.iso] to the store",
        "successfully added file [hauler/leap-micro-selfinstall.iso:latest]",
    ]

    def fake_run(self):
        for line in lines:
            yield LogLine(line)

    monkeypatch.setattr(DeployRunner, "run", fake_run)

    code = deploy_mod._deploy_plain(
        cfg={"name": "test"}, root=tmp_path, from_phase=None, install_collections=False,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "/tmp/leap-micro-selfinstall.iso" in out
