#!/bin/bash
# Exercise 1 (The Arrival) self-check.
#
# Adapted from suse-virt-rodeo's own Instruqt check for this chapter
# (01-the-arrival-welcome/check-kvm-host) — same two checks, same logic,
# just pointed at this workshop's kubeconfig path instead of Instruqt's.
#
# Run from the KVM/EC2 host as any user, no sudo needed:
#   /opt/suse-virt-workshop/checks/check-exercise-1.sh

set -uo pipefail

if [[ -z "${KUBECONFIG:-}" && ! -f "${HOME}/.kube/config" ]]; then
  # No kubeconfig saved from Exercise 1.3 (~/.kube/config, kubectl's own
  # default): fall back to the one rodeo-cli writes for whoever ran `rodeo up`.
  # rodeo-cli resolves ~/.rodeo to the *invoking* user's home even under sudo
  # (rodeo/paths.py invoking_home()), so mirror that here.
  if [[ -n "${SUDO_USER:-}" ]]; then
    INVOKING_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
  else
    INVOKING_HOME="${HOME}"
  fi
  [[ -r "${INVOKING_HOME}/.rodeo/harvester-kubeconfig" ]] \
    && export KUBECONFIG="${INVOKING_HOME}/.rodeo/harvester-kubeconfig"
fi

if ! KUBECTL_ERR=$(kubectl get nodes 2>&1 >/dev/null); then
  if grep -q 'x509' <<<"${KUBECTL_ERR}"; then
    # A kubeconfig downloaded through the public UI URL points at
    # <host-ip>:8443, which the Harvester certificate doesn't cover.
    echo "FAIL: kubectl rejects the Harvester certificate. Point the kubeconfig at the cluster VIP (Exercise 1.5):"
    echo "  sed -i 's#https://[^/]*/k8s#https://192.168.122.10/k8s#' ~/.kube/config"
  else
    echo "FAIL: kubectl can't reach the Harvester cluster. Save the kubeconfig from Exercise 1.3 as ~/.kube/config first."
  fi
  exit 1
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
