"""SQLite storage for the claim portal (WAL, parameterised queries only)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = "/var/lib/rodeo-portal/portal.db"
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS labs(
  id    TEXT PRIMARY KEY,
  ord   INTEGER NOT NULL,
  data  TEXT NOT NULL,              -- JSON lab card (URLs, credentials, ssh)
  ready INTEGER NOT NULL DEFAULT 0  -- 0 = still building, not claimable
);
CREATE TABLE IF NOT EXISTS claims(
  lab_id     TEXT PRIMARY KEY REFERENCES labs(id),
  email      TEXT UNIQUE NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL,         -- open | roster
  pin_hash   TEXT,                  -- scrypt; NULL for roster invites
  token_hash TEXT UNIQUE NOT NULL,  -- sha256 of the personal link token
  claimed_at TEXT NOT NULL,
  opened_at  TEXT,
  bad_pins   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

# v2: deploy progress pushed by `rodeo fleet portal publish --watch`.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS progress(
  lab_id TEXT PRIMARY KEY,
  data   TEXT NOT NULL              -- JSON: phases, state, started_at, error
);
"""


def db_path() -> str:
    return os.environ.get("RODEO_PORTAL_DB", DEFAULT_DB)


def connect(path: str | None = None) -> sqlite3.Connection:
    """Autocommit connection; callers open ``BEGIN IMMEDIATE`` for writes."""
    con = sqlite3.connect(path or db_path(), timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def migrate(path: str | None = None) -> None:
    """Create or upgrade the schema; the database file is always 0600."""
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        con = connect(str(p))
    finally:
        os.umask(old_umask)
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            con.executescript(_SCHEMA)
        if version < 2:
            con.executescript(_SCHEMA_V2)
        if version < SCHEMA_VERSION:
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    finally:
        con.close()
    os.chmod(p, 0o600)


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value),
    )
