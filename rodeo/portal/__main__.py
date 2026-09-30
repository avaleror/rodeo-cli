"""Portal entry point: ``python3 -m rodeo_portal serve|admin ...`` on the portal VM.

The laptop drives ``admin`` over SSH (``rodeo fleet portal ...``). Input that can
hold secrets (lab records, roster) comes on stdin as JSON, never on argv, so it
never lands in shell history or ``ps`` output. Output is JSON (``export``: CSV).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import claims
from .db import connect, db_path, migrate


def _admin(args: argparse.Namespace) -> int:
    migrate()
    con = connect()
    try:
        claims.ensure_defaults(con)
        cmd = args.cmd
        if cmd == "init":
            claims.ensure_defaults(con, mode=args.mode, title=args.title or "")
            with claims.write_tx(con):
                claims.set_setting(con, "mode", args.mode)
            out: object = claims.settings(con)
        elif cmd == "import":
            out = {"imported": claims.import_labs(con, json.load(sys.stdin))}
        elif cmd == "progress":
            out = {"progress": claims.set_progress(con, json.load(sys.stdin))}
        elif cmd == "invite":
            out = claims.invite(con, json.load(sys.stdin), rotate=args.rotate)
        elif cmd == "status":
            out = {**claims.settings(con), "labs": claims.status_rows(con)}
        elif cmd == "export":
            rows = claims.status_rows(con)
            w = csv.writer(sys.stdout)
            w.writerow(["lab", "name", "email", "via", "claimed_at", "opened_at"])
            for r in rows:
                if r["email"]:
                    w.writerow([r["lab"], r["name"], r["email"], r["source"],
                                r["claimed_at"], r["opened_at"]])
            return 0
        elif cmd == "info":
            out = claims.settings(con)
        elif cmd in ("open", "close"):
            with claims.write_tx(con):
                claims.set_setting(con, "open", "1" if cmd == "open" else "0")
            out = claims.settings(con)
        elif cmd == "rotate-code":
            with claims.write_tx(con):
                claims.set_setting(con, "code", claims.new_code())
            out = claims.settings(con)
        elif cmd == "release":
            out = {"lab": args.lab, "released": claims.release(con, args.lab)}
        elif cmd == "revoke":
            out = {"revoked": claims.revoke(con, args.email)}
        elif cmd == "reassign":
            claims.reassign(con, args.email, args.lab)
            out = {"lab": args.lab, "reassigned": True}
        elif cmd == "unlock":
            claims.unlock(con, args.email)
            out = {"unlocked": True}
        elif cmd == "admin-token":
            out = {"token": claims.rotate_admin_token(con)}
        else:  # pragma: no cover - argparse enforces choices
            return 2
    except claims.ClaimError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    finally:
        con.close()
    print(json.dumps(out))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rodeo_portal")
    sub = p.add_subparsers(dest="mode_", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--dev", action="store_true", help="plain-HTTP cookies for local testing")

    a = sub.add_parser("admin")
    asub = a.add_subparsers(dest="cmd", required=True)
    init = asub.add_parser("init")
    init.add_argument("--mode", choices=claims.MODES, default="both")
    init.add_argument("--title", default="")
    for name in ("import", "progress", "status", "export", "info", "open", "close", "rotate-code",
                 "admin-token"):
        asub.add_parser(name)
    inv = asub.add_parser("invite")
    inv.add_argument("--rotate", action="store_true")
    asub.add_parser("release").add_argument("lab")
    for name in ("revoke", "unlock"):
        asub.add_parser(name).add_argument("email")
    ra = asub.add_parser("reassign")
    ra.add_argument("email")
    ra.add_argument("lab")

    args = p.parse_args(argv)
    if args.mode_ == "admin":
        return _admin(args)

    from .web import make_server

    migrate()
    con = connect()
    try:
        claims.ensure_defaults(con)
    finally:
        con.close()
    srv = make_server(args.host, args.port, db_path=db_path(), secure_cookie=not args.dev)
    print(f"rodeo portal on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
