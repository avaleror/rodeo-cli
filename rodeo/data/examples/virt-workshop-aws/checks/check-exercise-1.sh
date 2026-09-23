#!/bin/bash
# Exercise 1 (The Arrival) self-check.
#
# Adapted from suse-virt-rodeo's own Instruqt check for this chapter
# (01-the-arrival-welcome/check-kvm-host) — same two checks, same logic,
# just pointed at this workshop's kubeconfig path instead of Instruqt's.
#
# Run from the KVM/EC2 host, as the user (or via sudo) that ran `rodeo up`:
#   ./checks/check-exercise-1.sh

set -uo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  # rodeo-cli resolves ~/.rodeo to the *invoking* user's home even under sudo
  # (rodeo/paths.py invoking_home()) — mirror that here. /root/rodeo-lab (and
  # this script alongside it) is only readable via sudo, but the kubeconfig
  # itself lands in the invoking user's home, not root's.
  if [[ -n "${SUDO_USER:-}" ]]; then
    INVOKING_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
  else
    INVOKING_HOME="${HOME}"
  fi
  export KUBECONFIG="${INVOKING_HOME}/.rodeo/harvester-kubeconfig"
fi

# All 3 Harvester nodes must be Ready
READY=$(kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready' || echo 0)
if [[ "$READY" -lt 3 ]]; then
  echo "FAIL: Only ${READY}/3 Harvester nodes are Ready. Wait for the cluster to finish initialising."
  exit 1
fi

# Longhorn storage nodes must all be schedulable
SCHEDULABLE=$(kubectl get nodes.longhorn.io -n longhorn-system --no-headers 2>/dev/null | \
  grep -c 'True' || echo 0)
TOTAL=$(kubectl get nodes.longhorn.io -n longhorn-system --no-headers 2>/dev/null | \
  wc -l | tr -d ' ')
if [[ "$SCHEDULABLE" -lt "$TOTAL" || "$TOTAL" -eq 0 ]]; then
  echo "FAIL: Not all Longhorn storage nodes are schedulable (${SCHEDULABLE}/${TOTAL}). Check the Longhorn dashboard."
  exit 1
fi

echo "PASS: All 3 nodes Ready and Longhorn storage is healthy"
