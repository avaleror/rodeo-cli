"""Claim logic: pure functions over a sqlite connection.

Every assignment runs inside one ``BEGIN IMMEDIATE`` transaction, so two students
claiming at the same moment can never get the same lab. Link tokens are stored as
SHA-256, PINs as scrypt, and every secret comparison uses ``hmac.compare_digest``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .db import get_setting, set_setting

MODES = ("open", "roster", "both")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
PIN_LOCKOUT = 5
EMAIL_RE = re.compile(r"^[^@\s<>\"'`]{1,64}@[^@\s<>\"'`]{1,190}\.[A-Za-z]{2,}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


class ClaimError(Exception):
    """Refusal shown to the student verbatim: never include secrets."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def new_code() -> str:
    pick = lambda n: "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"{pick(4)}-{pick(4)}"


def new_token() -> str:
    return secrets.token_urlsafe(24)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def check_pin(pin: str, stored: str | None) -> bool:
    if not stored or ":" not in stored:
        return False
    salt, digest = stored.split(":", 1)
    calc = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return hmac.compare_digest(calc, digest)


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 254:
        raise ClaimError("Enter a valid email.")
    return e


@contextmanager
def write_tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def ensure_defaults(con: sqlite3.Connection, *, mode: str = "both", title: str = "") -> None:
    with write_tx(con):
        if get_setting(con, "code") is None:
            set_setting(con, "code", new_code())
        if get_setting(con, "open") is None:
            set_setting(con, "open", "1")
        if get_setting(con, "mode") is None:
            set_setting(con, "mode", mode if mode in MODES else "both")
        if title:
            set_setting(con, "title", title)


def settings(con: sqlite3.Connection) -> dict[str, Any]:
    return {
        "code": get_setting(con, "code"),
        "open": get_setting(con, "open") == "1",
        "mode": get_setting(con, "mode", "both"),
        "title": get_setting(con, "title", ""),
    }


# ------------------------------------------------------------------ labs
def import_labs(con: sqlite3.Connection, labs: list[dict[str, Any]]) -> int:
    """Upsert lab records. Never touches claims (publish is safe to repeat)."""
    with write_tx(con):
        for i, lab in enumerate(labs):
            lab_id = str(lab["id"])
            con.execute(
                "INSERT INTO labs(id, ord, data, ready) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET ord=excluded.ord, data=excluded.data, "
                "ready=excluded.ready",
                (lab_id, int(lab.get("ord", i)), json.dumps(lab.get("data") or {}),
                 1 if lab.get("ready") else 0),
            )
    return len(labs)


# ------------------------------------------------------------------ open claim
def claim_open(
    con: sqlite3.Connection, *, code: str, email: str, name: str, pin: str
) -> tuple[str, str]:
    """Return ``(token, lab_id)``: a fresh personal link for this email's lab.

    A new email gets the next free *ready* lab. A known email with the right PIN
    gets its same lab back under a new link (the old link stops working).
    """
    email = normalize_email(email)
    name = (name or "").strip()[:80]
    if not name:
        raise ClaimError("Enter your name.")
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise ClaimError("The PIN must be 4 digits.")
    token = new_token()
    with write_tx(con):
        s = settings(con)
        if s["mode"] == "roster":
            raise ClaimError("This workshop uses personal invite links. Check your email.")
        if not s["open"]:
            raise ClaimError("Claiming is closed. Ask your instructor.")
        if not hmac.compare_digest((code or "").strip().upper(), s["code"] or ""):
            raise ClaimError("That event code is not valid.")
        row = con.execute("SELECT * FROM claims WHERE email=?", (email,)).fetchone()
        if row:
            if row["source"] != "open":
                raise ClaimError("This email has a personal invite link. Use that link.")
            if row["bad_pins"] >= PIN_LOCKOUT:
                raise ClaimError("Too many wrong PINs for this email. Ask your instructor.")
            if not check_pin(pin, row["pin_hash"]):
                con.execute("UPDATE claims SET bad_pins=bad_pins+1 WHERE email=?", (email,))
                # commit the counter, then refuse
                con.execute("COMMIT")
                con.execute("BEGIN IMMEDIATE")
                raise ClaimError("This email already has a lab and the PIN does not match.")
            con.execute(
                "UPDATE claims SET token_hash=?, bad_pins=0 WHERE email=?", (sha256(token), email)
            )
            return token, row["lab_id"]
        lab = con.execute(
            "SELECT id FROM labs WHERE ready=1 AND id NOT IN (SELECT lab_id FROM claims) "
            "ORDER BY ord LIMIT 1"
        ).fetchone()
        if not lab:
            raise ClaimError("No free labs left. Ask your instructor.")
        con.execute(
            "INSERT INTO claims(lab_id, email, name, source, pin_hash, token_hash, claimed_at) "
            "VALUES(?, ?, ?, 'open', ?, ?, ?)",
            (lab["id"], email, name, hash_pin(pin), sha256(token), now()),
        )
        return token, lab["id"]


# ------------------------------------------------------------------ roster
def invite(
    con: sqlite3.Connection, roster: list[dict[str, Any]], *, rotate: bool = False
) -> list[dict[str, Any]]:
    """Reserve a lab per roster row. Returns one result per row; ``token`` is set
    only for new invites (or every invite with ``rotate``), because only the hash
    is stored."""
    out: list[dict[str, Any]] = []
    with write_tx(con):
        for entry in roster:
            email = normalize_email(str(entry.get("email") or ""))
            name = str(entry.get("name") or "").strip()[:80]
            pinned = str(entry.get("host_id") or "").strip() or None
            row = con.execute("SELECT * FROM claims WHERE email=?", (email,)).fetchone()
            if row:
                token = new_token() if rotate else None
                if token:
                    con.execute(
                        "UPDATE claims SET token_hash=? WHERE email=?", (sha256(token), email)
                    )
                out.append({"email": email, "name": name, "lab": row["lab_id"],
                            "token": token, "status": "rotated" if token else "existing"})
                continue
            if pinned:
                lab = con.execute("SELECT id FROM labs WHERE id=?", (pinned,)).fetchone()
                if not lab:
                    raise ClaimError(f"roster pins unknown lab {pinned!r}")
                taken = con.execute("SELECT email FROM claims WHERE lab_id=?", (pinned,)).fetchone()
                if taken:
                    raise ClaimError(f"lab {pinned!r} is already assigned")
            else:
                lab = con.execute(
                    "SELECT id FROM labs WHERE id NOT IN (SELECT lab_id FROM claims) "
                    "ORDER BY ord LIMIT 1"
                ).fetchone()
                if not lab:
                    raise ClaimError(f"no free lab left for {email}")
            token = new_token()
            con.execute(
                "INSERT INTO claims(lab_id, email, name, source, pin_hash, token_hash, claimed_at) "
                "VALUES(?, ?, ?, 'roster', NULL, ?, ?)",
                (lab["id"], email, name, sha256(token), now()),
            )
            out.append({"email": email, "name": name, "lab": lab["id"], "token": token,
                        "status": "invited"})
    return out


# ------------------------------------------------------------------ lookups
def lab_for_token(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    if not TOKEN_RE.match(token or ""):
        return None
    row = con.execute(
        "SELECT l.id, l.data, l.ready, c.email, c.name, c.opened_at FROM claims c "
        "JOIN labs l ON l.id = c.lab_id WHERE c.token_hash=?",
        (sha256(token),),
    ).fetchone()
    if row is not None and row["opened_at"] is None:
        con.execute(
            "UPDATE claims SET opened_at=? WHERE token_hash=? AND opened_at IS NULL",
            (now(), sha256(token)),
        )
    return row


def status_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT l.id AS lab, l.ready, c.name, c.email, c.source, c.claimed_at, c.opened_at "
        "FROM labs l LEFT JOIN claims c ON c.lab_id = l.id ORDER BY l.ord"
    ).fetchall()
    return [
        {
            "lab": r["lab"],
            "state": "claimed" if r["email"] else ("free" if r["ready"] else "building"),
            "name": r["name"] or "",
            "email": r["email"] or "",
            "source": r["source"] or "",
            "claimed_at": r["claimed_at"] or "",
            "opened_at": r["opened_at"] or "",
        }
        for r in rows
    ]


def set_progress(con: sqlite3.Connection, labs: dict[str, Any]) -> int:
    """Replace deploy progress per lab (instructor view only) and stamp the time."""
    with write_tx(con):
        for lab_id, data in labs.items():
            con.execute(
                "INSERT INTO progress(lab_id, data) VALUES(?, ?) "
                "ON CONFLICT(lab_id) DO UPDATE SET data=excluded.data",
                (str(lab_id), json.dumps(data)),
            )
        set_setting(con, "progress_at", now())
    return len(labs)


def progress(con: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], str | None]:
    rows = {r["lab_id"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM progress")}
    return rows, get_setting(con, "progress_at")


def lab_data(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Every lab's published record (credentials included): instructor view only."""
    return {r["id"]: json.loads(r["data"] or "{}")
            for r in con.execute("SELECT id, data FROM labs ORDER BY ord")}


# ------------------------------------------------------------------ instructor fixes
def release(con: sqlite3.Connection, lab_id: str) -> bool:
    """Free a lab. Its link stops working; the student can claim again."""
    with write_tx(con):
        return con.execute("DELETE FROM claims WHERE lab_id=?", (lab_id,)).rowcount > 0


def revoke(con: sqlite3.Connection, email: str) -> bool:
    email = normalize_email(email)
    with write_tx(con):
        return con.execute("DELETE FROM claims WHERE email=?", (email,)).rowcount > 0


def reassign(con: sqlite3.Connection, email: str, lab_id: str) -> None:
    """Move a student to another free lab; their personal link keeps working."""
    email = normalize_email(email)
    with write_tx(con):
        if not con.execute("SELECT 1 FROM labs WHERE id=?", (lab_id,)).fetchone():
            raise ClaimError(f"unknown lab {lab_id!r}")
        taken = con.execute("SELECT email FROM claims WHERE lab_id=?", (lab_id,)).fetchone()
        if taken and taken["email"] != email:
            raise ClaimError(f"lab {lab_id!r} is already assigned")
        if con.execute("UPDATE claims SET lab_id=? WHERE email=?", (lab_id, email)).rowcount == 0:
            raise ClaimError("no claim for that email")


def unlock(con: sqlite3.Connection, email: str) -> None:
    """Reset the wrong-PIN counter for one email."""
    email = normalize_email(email)
    with write_tx(con):
        con.execute("UPDATE claims SET bad_pins=0 WHERE email=?", (email,))


# ------------------------------------------------------------------ instructor web view
def rotate_admin_token(con: sqlite3.Connection) -> str:
    token = new_token()
    with write_tx(con):
        set_setting(con, "admin_hash", sha256(token))
    return token


def is_admin_token(con: sqlite3.Connection, token: str) -> bool:
    stored = get_setting(con, "admin_hash")
    return bool(stored and TOKEN_RE.match(token or "")
                and hmac.compare_digest(sha256(token), stored))
