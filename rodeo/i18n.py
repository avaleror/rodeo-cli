"""Read-only lookup of per-language lab-content strings, backed by multilang.

multilang (https://github.com/avaleror/multilang-lib in progress — install via
the ``i18n`` extra once published) is a standalone `(string_id, language_id) ->
string` store with ports in five languages. rodeo-cli only ever calls
retrieve_data: writing translated strings is a content-repo build step, not
something rodeo does at runtime, so insert_data is deliberately not wrapped
here.

The multilang package is optional — callers get None back (not an exception)
when it isn't installed, so a plan with no `language:` set never needs it.
"""
from __future__ import annotations

from .paths import i18n_db_path


def get_string(string_id: str, language_id: str, context: str = "") -> str | None:
    """Look up one lab-content string for a locale.

    Returns None if multilang isn't installed, the database doesn't exist
    yet, or no matching row is found — never raises for those cases.
    """
    try:
        from multilang import db_connector, retrieve_data
    except ImportError:
        return None

    db_path = i18n_db_path()
    if not db_path.exists():
        return None

    conn = db_connector("sqlite", path=str(db_path))
    try:
        return retrieve_data(conn, string_id, language_id, context=context)
    finally:
        conn.close()
