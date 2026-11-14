#!/bin/bash
set -euo pipefail

# Intentionally always-pass, matching suse-virt-rodeo's own check for this
# chapter (06-the-unthinkable-error-snapshots/check-kvm-host). Exercise 6
# (The Unthinkable Error) is self-contained: everything it needs is
# pre-created by deploy automation, and nothing later in the workshop reads
# back state you create here.

echo "PASS: Exercise 6 has no automated grading by design — verify in the UI: snapshot restored, backup-target configured, a schedule created."
