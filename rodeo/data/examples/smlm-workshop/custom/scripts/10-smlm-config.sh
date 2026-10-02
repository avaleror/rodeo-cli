#!/bin/bash
# Re-apply the SMLM configuration once the zz* clients have registered.
#
# lab-in-a-box runs the smlm addon before any client VM exists, so group
# membership and custom-info values for the pre-registered clients can only
# land on a second pass. install_smlm is idempotent: everything already in
# place is left alone. Runs after every `rodeo up` (custom_scripts phase).
set -euo pipefail

lab_json="${RODEO_CONFIG_DIR:?}/.labinabox/lab.json"
smlm_node=$(python3 - "${lab_json}" <<'PY'
import json, sys
nodes = json.load(open(sys.argv[1]))["nodes"]
print(next(name for name, node in nodes.items()
           if any(a == "smlm" or (isinstance(a, dict) and "smlm" in a) for a in node.get("addons", []))))
PY
)
_vm_name="${smlm_node}" exec /usr/local/bin/install_smlm "${lab_json}"
