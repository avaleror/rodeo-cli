#!/bin/bash
set -euo pipefail

# Intentionally always-pass, matching suse-virt-rodeo's own check for this
# chapter (07-the-stampede-automation/check-kvm-host). Exercise 7 (The
# Stampede) is self-contained: everything it needs is pre-created by deploy
# automation, and nothing later in the workshop reads back state you create
# here.

echo "PASS: Exercise 7 has no automated grading by design — verify in the UI: template created, fleet scaled to 5, then scaled back down."
