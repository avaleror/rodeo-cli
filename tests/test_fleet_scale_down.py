"""Scaling a fleet down mid-workshop: `fleet deprovision --host` with a live portal.

Terminated labs must leave the portal (or the next claim gets a dead lab), and
a lab a student has claimed must not be terminated by a typo.
"""
from __future__ import annotations

import contextlib
import io
import json

import pytest
import yaml
from click.testing import CliRunner

from rodeo.commands import fleet_cmd
from rodeo.config import ConfigError
from rodeo.fleet import portal as fleet_portal
from rodeo.portal import __main__ as portal_main
from rodeo.portal import claims
from rodeo.portal.db import connect, migrate
from rodeo.providers.base import DeprovisionResult

from tests.test_fleet_local_cleanup import _setup

PIN = "583920"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = str(tmp_path / "portal.db")
    monkeypatch.setenv("RODEO_PORTAL_DB", p)
    migrate(p)
    con = connect(p)
    claims.ensure_defaults(con)
    claims.import_labs(con, [{"id": f"student-0{i}", "ready": True, "data": {}} for i in (1, 2, 3)])
    claims.set_progress(con, {"student-02": {"state": "ok"}})
    con.close()
    return p


def _claim(db, email):
    con = connect(db)
    try:
        return claims.claim_open(con, code=claims.settings(con)["code"], email=email, name="N", pin=PIN)
    finally:
        con.close()


def _lab_ids(db):
    con = connect(db)
    try:
        return [r["lab"] for r in claims.status_rows(con)]
    finally:
        con.close()


# ---------------------------------------------------------------- claims.remove_labs
def test_removed_labs_can_no_longer_be_claimed(db):
    con = connect(db)
    assert claims.remove_labs(con, ["student-01", "student-02", "nope"]) == ["student-01", "student-02"]
    con.close()
    assert _lab_ids(db) == ["student-03"]
    assert _claim(db, "a@x.io")[1] == "student-03"


def test_a_claimed_lab_refuses_the_whole_removal(db):
    _claim(db, "a@x.io")  # takes student-01
    con = connect(db)
    with pytest.raises(claims.ClaimError, match="student-01"):
        claims.remove_labs(con, ["student-01", "student-02"])
    con.close()
    assert _lab_ids(db) == ["student-01", "student-02", "student-03"]


def test_force_removes_a_claimed_lab_and_its_claim(db):
    token, _ = _claim(db, "a@x.io")
    con = connect(db)
    assert claims.remove_labs(con, ["student-01"], force=True) == ["student-01"]
    assert claims.lab_for_token(con, token) is None
    con.close()


def test_admin_remove_command(db, capsys):
    assert portal_main.main(["admin", "remove", "--", "student-03"]) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": ["student-03"]}


# ---------------------------------------------------------------- fleet deprovision --host
def _in_process_portal(inventory, args, *, stdin=None, raw=False):
    """portal_admin without SSH: run the portal's admin entry point here."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = portal_main.main(["admin", *args])
    data = json.loads(out.getvalue().strip().splitlines()[-1])
    if rc or data.get("error"):
        raise ConfigError(str(data.get("error")))
    return data


@pytest.fixture()
def fleet(tmp_path, monkeypatch, db):
    inv = _setup(tmp_path, portal_ip="198.51.100.50")
    plan = yaml.safe_load(inv.read_text())
    plan["provider"]["student_access"] = "open"  # a portal requires it
    inv.write_text(yaml.safe_dump(plan, sort_keys=False))
    monkeypatch.setattr(fleet_portal, "portal_admin", _in_process_portal)
    terminated: list[list[str]] = []

    def fake_deprovision(inventory, host_ids=None):
        terminated.append(list(host_ids or []))
        return [DeprovisionResult(id=h, ok=True, provider_id="i-x", detail="terminating")
                for h in host_ids or []]

    monkeypatch.setattr(fleet_cmd, "fleet_deprovision", fake_deprovision)
    return inv, terminated


def _run(inv, *args):
    return CliRunner().invoke(fleet_cmd.fleet_cmd, ["deprovision", "--yes", "-f", str(inv), *args])


def test_scale_down_removes_terminated_labs_from_the_portal(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")  # student-01
    result = _run(inv, "--host", "student-02", "--host", "student-03")
    assert result.exit_code == 0, result.output
    assert terminated == [["student-02", "student-03"]]
    assert _lab_ids(db) == ["student-01"]
    assert "removed 2 lab(s)" in result.output


def test_scale_down_refuses_a_claimed_lab_before_terminating_anything(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")  # student-01
    result = _run(inv, "--host", "student-01", "--host", "student-02")
    assert result.exit_code == 1
    assert "student-01" in result.output and "--force" in result.output
    assert terminated == []
    assert _lab_ids(db) == ["student-01", "student-02", "student-03"]


def test_force_terminates_a_claimed_lab(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")
    result = _run(inv, "--host", "student-01", "--force")
    assert result.exit_code == 0, result.output
    assert terminated == [["student-01"]]
    assert _lab_ids(db) == ["student-02", "student-03"]


def test_an_old_portal_without_remove_only_warns(fleet, db, monkeypatch):
    inv, terminated = fleet

    def old_portal(inventory, args, **kw):
        if args[0] == "remove":
            raise ConfigError("portal admin remove failed (exit 2)")
        return _in_process_portal(inventory, args, **kw)

    monkeypatch.setattr(fleet_portal, "portal_admin", old_portal)
    result = _run(inv, "--host", "student-03")
    assert result.exit_code == 0, result.output
    assert terminated == [["student-03"]]
    assert "still listed on the portal" in " ".join(result.output.split())


# ---------------------------------------------------------------- --unclaimed N|all
def _row(lab, state):
    return {"lab": lab, "state": state}


def test_pick_unclaimed_takes_unready_labs_then_highest_free():
    rows = [_row("s-01", "claimed"), _row("s-02", "free"), _row("s-03", "building"),
            _row("s-04", "free"), _row("s-05", "free")]
    assert fleet_portal.pick_unclaimed(rows, 2) == ["s-03", "s-05"]
    assert fleet_portal.pick_unclaimed(rows, None) == ["s-03", "s-05", "s-04", "s-02"]
    with pytest.raises(ConfigError, match="only 4 unclaimed"):
        fleet_portal.pick_unclaimed(rows, 5)


def _is_open(db):
    con = connect(db)
    try:
        return claims.settings(con)["open"]
    finally:
        con.close()


def test_unclaimed_count_removes_the_highest_free_labs_and_reopens_claiming(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")  # student-01
    result = _run(inv, "--unclaimed", "1")
    assert result.exit_code == 0, result.output
    assert terminated == [["student-03"]]
    assert _lab_ids(db) == ["student-01", "student-02"]
    assert _is_open(db)  # paused during the removal, open again after


def test_unclaimed_all_leaves_only_claimed_labs(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")
    result = _run(inv, "--unclaimed", "all")
    assert result.exit_code == 0, result.output
    assert sorted(terminated[0]) == ["student-02", "student-03"]
    assert _lab_ids(db) == ["student-01"]


def test_unclaimed_more_than_available_terminates_nothing(fleet, db):
    inv, terminated = fleet
    _claim(db, "a@x.io")
    result = _run(inv, "--unclaimed", "3")
    assert result.exit_code == 1 and "only 2 unclaimed" in result.output
    assert terminated == [] and _is_open(db)


def test_claiming_closed_before_the_scale_down_stays_closed(fleet, db):
    inv, _ = fleet
    con = connect(db)
    with claims.write_tx(con):
        claims.set_setting(con, "open", "0")
    con.close()
    assert _run(inv, "--unclaimed", "1").exit_code == 0
    assert not _is_open(db)


@pytest.mark.parametrize("args", [["--unclaimed", "0"], ["--unclaimed", "some"],
                                  ["--unclaimed", "1", "--host", "student-03"]])
def test_bad_unclaimed_usage_is_refused(fleet, args):
    inv, terminated = fleet
    assert _run(inv, *args).exit_code == 1
    assert terminated == []
