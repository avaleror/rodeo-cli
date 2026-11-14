#!/bin/bash
set -euo pipefail

# Intentionally always-pass, matching suse-virt-rodeo's own check for this
# chapter (05-the-invisible-intruder-networking/check-kvm-host). Exercise 5
# (The Invisible Intruder) is self-contained: everything it needs is
# pre-created by deploy automation, and nothing later in the workshop reads
# back state you create here.

echo "PASS: Exercise 5 has no automated grading by design — verify in the UI: closed-loop and overlay networks Active, a VM successfully attached to one."
