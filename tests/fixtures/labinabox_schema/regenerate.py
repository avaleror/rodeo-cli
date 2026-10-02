#!/usr/bin/env python3
"""Regenerate schema.json from a lab-in-a-box checkout.

    python3 tests/fixtures/labinabox_schema/regenerate.py <lab-in-a-box checkout>

Run it against the lab-in-a-box ref rodeo pins (rodeo/labinabox_host.py:LIAB_REF)
whenever that pin moves. The snapshot holds only field names — enough for
tests/test_labinabox_platform.py to catch rodeo emitting a lab.json field that
lab-in-a-box doesn't know.
"""
import json
import subprocess
import sys
from pathlib import Path

ADDONS = ("smlm", "client_registration")


def _schema(checkout: Path, *args: str) -> dict:
    out = subprocess.run([sys.executable, str(checkout / "scripts" / "lab_schema"), *args],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main() -> None:
    checkout = Path(sys.argv[1]).resolve()
    base = _schema(checkout, "--base", "json")["sections"]
    snapshot = {
        "common": sorted(f["name"] for f in base["common"]["fields"]),
        "nodes": sorted(f["name"] for f in base["nodes"]["fields"]),
        "kclusters": sorted(f["name"] for f in base["kclusters"]["fields"]),
        "addons": {
            addon: sorted(f["name"] for f in _schema(
                checkout, str(checkout / "scripts" / f"install_{addon}.py"), "json")["fields"])
            for addon in ADDONS
        },
    }
    out = Path(__file__).with_name("schema.json")
    out.write_text(json.dumps(snapshot, indent=1) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
