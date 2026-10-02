#!/bin/bash
# The extra client prep the track's setup-zz* scripts do, for the pre-registered
# zz* clients: SCAP tooling for the security exercise, and updates downloaded
# ahead of time so the patching exercises don't wait on the network.
#
# Runs on each VM in the background (as the track does) and returns at once.
# Idempotent: installing present packages and re-downloading are no-ops.
set -euo pipefail

lab_json="${RODEO_CONFIG_DIR:?}/.labinabox/lab.json"
python3 - "${lab_json}" <<'PY' | while read -r ip; do
import json, sys
for node in json.load(open(sys.argv[1]))["nodes"].values():
    if any(isinstance(a, dict) and "client_registration" in a for a in node.get("addons", [])):
        print(node["myip"])
PY
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${ip}" \
        'nohup sh -c "if command -v zypper >/dev/null; then
                        zypper -n in -y openscap-utils scap-security-guide
                        zypper -n dup --download-only -y
                      else
                        yum -y update --downloadonly
                      fi" >/var/log/rodeo-client-prep.log 2>&1 </dev/null &' \
        && echo "client prep started on ${ip}" \
        || echo "WARNING: could not start client prep on ${ip}" >&2
done
