"""Claim portal logic (F5.1): assignment, re-claim, roster, instructor fixes."""
from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path

import pytest

from rodeo.portal import claims
from rodeo.portal.db import connect, migrate

PORTAL_DIR = Path(__file__).resolve().parents[1] / "rodeo" / "portal"


@pytest.fixture()
def dbp(tmp_path):
    p = str(tmp_path / "portal.db")
    migrate(p)
    con = connect(p)
    claims.ensure_defaults(con, mode="both", title="T")
    claims.import_labs(con, [
        {"id": "lab-01", "ready": True, "data": {"components": []}},
        {"id": "lab-02", "ready": True, "data": {"components": []}},
        {"id": "lab-03", "ready": False, "data": {}},
    ])
    con.close()
    return p


def _con(p):
    return connect(p)


def _code(p):
    con = _con(p)
    try:
        return claims.settings(con)["code"]
    finally:
        con.close()


PIN = "583920"
OTHER_PIN = "739273"


def _claim(p, email, pin=PIN, code=None, name="N"):
    con = _con(p)
    try:
        return claims.claim_open(con, code=code or _code(p), email=email, name=name, pin=pin)
    finally:
        con.close()


def _recover(p, email, pin=PIN, code=None):
    con = _con(p)
    try:
        return claims.recover(con, code=code or _code(p), email=email, pin=pin)
    finally:
        con.close()


def test_db_is_private(tmp_path):
    p = tmp_path / "x.db"
    migrate(str(p))
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_new_email_gets_next_ready_lab_in_order(dbp):
    _, a = _claim(dbp, "a@x.io")
    _, b = _claim(dbp, "b@x.io")
    assert (a, b) == ("lab-01", "lab-02")


def test_building_lab_is_never_claimed(dbp):
    _claim(dbp, "a@x.io")
    _claim(dbp, "b@x.io")
    with pytest.raises(claims.ClaimError, match="No free labs"):
        _claim(dbp, "c@x.io")


def test_recover_same_lab_new_link_old_link_dies(dbp):
    t1, lab = _claim(dbp, "a@x.io")
    t2, lab2 = _recover(dbp, "A@X.io")  # email is case-insensitive
    assert lab2 == lab and t1 != t2
    con = _con(dbp)
    assert claims.lab_for_token(con, t1) is None
    assert claims.lab_for_token(con, t2)["id"] == lab
    con.close()


def test_wrong_pin_refused_and_locks_out(dbp):
    _claim(dbp, "a@x.io")
    for _ in range(claims.PIN_LOCKOUT):
        with pytest.raises(claims.GuessError, match="No lab matches"):
            _recover(dbp, "a@x.io", pin=OTHER_PIN)
    with pytest.raises(claims.GuessError, match="Too many wrong PINs"):
        _recover(dbp, "a@x.io")
    con = _con(dbp)
    claims.unlock(con, "a@x.io")
    con.close()
    _recover(dbp, "a@x.io")


def test_unknown_email_and_wrong_pin_look_the_same(dbp):
    _claim(dbp, "a@x.io")
    with pytest.raises(claims.GuessError) as unknown:
        _recover(dbp, "nobody@x.io")
    with pytest.raises(claims.GuessError) as wrong:
        _recover(dbp, "a@x.io", pin=OTHER_PIN)
    assert str(unknown.value) == str(wrong.value)


def test_claiming_twice_points_to_recovery(dbp):
    _claim(dbp, "a@x.io")
    with pytest.raises(claims.ClaimError, match="Get my lab back"):
        _claim(dbp, "a@x.io", name="Someone else")


def test_two_students_may_share_a_pin(dbp):
    """The email is the lookup key; a shared PIN opens only each one's own lab."""
    _, a = _claim(dbp, "a@x.io")
    _, b = _claim(dbp, "b@x.io")
    assert _recover(dbp, "a@x.io")[1] == a and _recover(dbp, "b@x.io")[1] == b


def test_recovery_works_while_claiming_is_closed(dbp):
    _claim(dbp, "a@x.io")
    con = _con(dbp)
    with claims.write_tx(con):
        claims.set_setting(con, "open", "0")
    con.close()
    assert _recover(dbp, "a@x.io")[1] == "lab-01"


@pytest.mark.parametrize("pin", ["123456", "654321", "000000", "111111", "121212", "123123",
                                 "112233", "998877", "345678", "901234", "484848", "135790"])
def test_obvious_pins_refused(dbp, pin):
    with pytest.raises(claims.ClaimError, match="too easy"):
        _claim(dbp, "a@x.io", pin=pin)


@pytest.mark.parametrize("pin", ["583920", "739273", "204816", "907315"])
def test_ordinary_pins_accepted(pin):
    assert claims.pin_problem(pin) is None


def test_bad_code_closed_and_roster_mode_refuse(dbp):
    with pytest.raises(claims.ClaimError, match="workshop code"):
        _claim(dbp, "a@x.io", code="AAAA-AAAA")
    con = _con(dbp)
    with claims.write_tx(con):
        claims.set_setting(con, "open", "0")
    con.close()
    with pytest.raises(claims.ClaimError, match="closed"):
        _claim(dbp, "a@x.io")
    con = _con(dbp)
    with claims.write_tx(con):
        claims.set_setting(con, "open", "1")
        claims.set_setting(con, "mode", "roster")
    con.close()
    with pytest.raises(claims.ClaimError, match="personal lab links"):
        _claim(dbp, "a@x.io")


@pytest.mark.parametrize("email,pin,name,msg", [
    ("not-an-email", PIN, "N", "valid email"),
    ("a@x.io", "12a456", "N", "6 digits"),
    ("a@x.io", "5839", "N", "6 digits"),
    ("a@x.io", PIN, "  ", "name"),
])
def test_input_validation(dbp, email, pin, name, msg):
    with pytest.raises(claims.ClaimError, match=msg):
        _claim(dbp, email, pin=pin, name=name)


def test_concurrent_claims_assign_each_lab_once(tmp_path):
    p = str(tmp_path / "race.db")
    migrate(p)
    con = connect(p)
    claims.ensure_defaults(con)
    claims.import_labs(con, [{"id": f"lab-{i:02d}", "ready": True} for i in range(10)])
    code = claims.settings(con)["code"]
    con.close()
    got: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(i):
        c = connect(p)
        try:
            _, lab = claims.claim_open(c, code=code, email=f"u{i}@x.io", name="U", pin=PIN)
            with lock:
                got.append(lab)
        except claims.ClaimError as exc:
            with lock:
                errors.append(str(exc))
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == [f"lab-{i:02d}" for i in range(10)]
    assert len(errors) == 40 and all("No free labs" in e for e in errors)


def test_roster_invite_reserves_labs_before_open_claims(dbp):
    con = _con(dbp)
    res = claims.invite(con, [{"name": "R", "email": "r@x.io", "host_id": "lab-02"}])
    assert res[0]["lab"] == "lab-02" and res[0]["token"]
    again = claims.invite(con, [{"name": "R", "email": "r@x.io"}])
    assert again[0]["status"] == "existing" and again[0]["token"] is None
    rotated = claims.invite(con, [{"name": "R", "email": "r@x.io"}], rotate=True)
    assert rotated[0]["token"] and claims.lab_for_token(con, res[0]["token"]) is None
    con.close()
    _, lab = _claim(dbp, "a@x.io")
    assert lab == "lab-01"
    with pytest.raises(claims.ClaimError, match="personal invite"):
        _claim(dbp, "r@x.io")


def test_roster_pin_to_taken_or_unknown_lab_fails(dbp):
    _claim(dbp, "a@x.io")
    con = _con(dbp)
    with pytest.raises(claims.ClaimError, match="already assigned"):
        claims.invite(con, [{"email": "r@x.io", "host_id": "lab-01"}])
    with pytest.raises(claims.ClaimError, match="unknown lab"):
        claims.invite(con, [{"email": "r@x.io", "host_id": "nope"}])
    con.close()


def test_release_revoke_reassign(dbp):
    t, lab = _claim(dbp, "a@x.io")
    con = _con(dbp)
    claims.reassign(con, "a@x.io", "lab-02")
    assert claims.lab_for_token(con, t)["id"] == "lab-02"
    assert claims.release(con, "lab-02") is True
    assert claims.lab_for_token(con, t) is None
    con.close()
    t, _ = _claim(dbp, "b@x.io")
    con = _con(dbp)
    assert claims.revoke(con, "b@x.io") is True
    assert claims.lab_for_token(con, t) is None
    con.close()


def test_publish_upsert_keeps_claims(dbp):
    t, lab = _claim(dbp, "a@x.io")
    con = _con(dbp)
    claims.import_labs(con, [{"id": "lab-01", "ready": True, "data": {"components": [1]}}])
    assert claims.lab_for_token(con, t)["id"] == lab
    con.close()


def test_secrets_stored_hashed(dbp):
    t, _ = _claim(dbp, "a@x.io", pin="804615")
    con = _con(dbp)
    row = con.execute("SELECT pin_hash, token_hash FROM claims").fetchone()
    con.close()
    assert t not in row["token_hash"] and "804615" not in row["pin_hash"]


def test_admin_token_rotation(dbp):
    con = _con(dbp)
    a = claims.rotate_admin_token(con)
    assert claims.is_admin_token(con, a)
    b = claims.rotate_admin_token(con)
    assert not claims.is_admin_token(con, a) and claims.is_admin_token(con, b)
    assert not claims.is_admin_token(con, "")
    con.close()


def test_portal_package_is_stdlib_only_with_relative_imports():
    """`fleet portal up` copies rodeo/portal verbatim to the portal VM and runs it
    with system python3: no rodeo.* or third-party import may sneak in."""
    stdlib = set(sys.stdlib_module_names)
    for f in PORTAL_DIR.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in stdlib, f"{f.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert node.module.split(".")[0] in stdlib | {"__future__"}, (
                        f"{f.name}: from {node.module}"
                    )


def test_guessing_errors_are_marked_for_the_rate_limiter(dbp):
    with pytest.raises(claims.GuessError):
        _claim(dbp, "a@x.io", code="RODEO-AAAA-20000101")
    _claim(dbp, "a@x.io")
    with pytest.raises(claims.GuessError):
        _recover(dbp, "a@x.io", pin=OTHER_PIN)
    with pytest.raises(claims.ClaimError) as weak:
        _claim(dbp, "b@x.io", pin="123456")
    assert not isinstance(weak.value, claims.GuessError)
    with pytest.raises(claims.ClaimError) as exc:
        _claim(dbp, "not-an-email")
    assert not isinstance(exc.value, claims.GuessError)


def test_new_code_uses_today_and_unambiguous_letters():
    from datetime import datetime, timezone

    code = claims.new_code(today=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert code.startswith("RODEO-") and code.endswith("-20260930")
    assert not set(code.split("-")[1]) & set("IO01")
    assert len(claims.new_code(99).split("-")[1]) == claims.CODE_LETTERS_MAX


def test_code_letters_setting_survives_rotation(tmp_path):
    p = str(tmp_path / "c.db")
    migrate(p)
    con = connect(p)
    claims.ensure_defaults(con, code_letters=6)
    assert len(claims.settings(con)["code"].split("-")[1]) == 6
    assert len(claims.rotate_code(con).split("-")[1]) == 6
    con.close()


def test_access_token_is_bound_to_the_current_code(dbp):
    con = _con(dbp)
    tok = claims.access_token(con)
    assert claims.has_access(con, tok) and not claims.has_access(con, "")
    claims.rotate_code(con)
    assert not claims.has_access(con, tok)
    con.close()


@pytest.mark.parametrize("bad", ["../x", "a b", "", "x" * 70, 'a"b', "a\r\nb"])
def test_import_rejects_unsafe_lab_ids(dbp, bad):
    con = _con(dbp)
    with pytest.raises(claims.ClaimError, match="invalid lab id"):
        claims.import_labs(con, [{"id": bad, "ready": True}])
    con.close()
