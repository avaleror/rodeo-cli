#!/bin/bash
# Exercise 2 (The Subterranean Divide) self-check — verifies 2.2 and 2.4.
# 2.1 and 2.3 are pure UI inspection, 2.5/2.6/2.7/2.8 have no fixed
# expected values to check against (network/SSH key/image name/user-data
# are all "whatever you set up" by design) — nothing to verify for those.
#
# Adapted from suse-virt-rodeo's own Instruqt check for this chapter
# (02-the-subterranean-divide-cluster-prep/check-kvm-host) — same checks,
# same names (this workshop's namespaces and StorageClass match the rodeo's
# exactly), just pointed at this workshop's kubeconfig path.
#
# Run from the KVM/EC2 host:
#   ./checks/check-exercise-2.sh

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

FAIL=0
fail(){ echo "FAIL: $*"; FAIL=1; }

# --- 2.2: namespaces ---
for ns in prod dev; do
  kubectl get namespace "${ns}" &>/dev/null || fail "namespace ${ns} not found."
done

# --- 2.4: dev cost-tier StorageClass ---
if kubectl get storageclass harvester-longhorn-1rep &>/dev/null; then
  REPLICAS="$(kubectl get storageclass harvester-longhorn-1rep -o jsonpath='{.parameters.numberOfReplicas}' 2>/dev/null)"
  [ "${REPLICAS}" = "1" ] || fail "harvester-longhorn-1rep numberOfReplicas is '${REPLICAS:-empty}', expected 1."
else
  fail "StorageClass harvester-longhorn-1rep not found."
fi

if [[ "${FAIL}" -eq 1 ]]; then
  exit 1
fi

echo "PASS: Exercise 2 objectives verified (namespaces prod/dev, harvester-longhorn-1rep StorageClass)"
