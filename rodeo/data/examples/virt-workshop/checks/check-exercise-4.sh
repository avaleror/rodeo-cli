#!/bin/bash
set -euo pipefail

# Intentionally always-pass, matching suse-virt-rodeo's own check for this
# chapter (04-the-rising-tide-live-migration/check-kvm-host). Exercise 4
# (The Rising Tide) is self-contained: everything it needs is pre-created by
# deploy automation, and nothing later in the workshop reads back state you
# create here. There is nothing meaningful to assert beyond "did the VM
# migrate", which is inherently a live, momentary UI observation (uptime not
# resetting, node column changing) rather than steady-state cluster
# resources a script can check after the fact.

echo "PASS: Exercise 4 has no automated grading by design — verify the migration in the UI instead (uptime unchanged, Node column updated)."
