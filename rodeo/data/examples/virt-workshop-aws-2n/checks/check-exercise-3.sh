#!/bin/bash
# Exercise 3 (The Flash Crash) self-check — verifies 3.1-3.4 (the
# state-changing steps). 3.5 is manual console/SSH validation with nothing
# new to check beyond what 3.1-3.4 already establish (Running VM, correct
# volumes). API-based only, no actual SSH into the VM.
#
# Adapted from suse-virt-rodeo's own Instruqt check for this chapter
# (03-the-flash-crash-first-vm/check-kvm-host). Two real differences from
# the rodeo version: the VM is named algo-trader-01 here (not
# vertex-trader-01), and this workshop lets you upload any image under any
# name in 2.7 (the rodeo pins one exact SLES image and URL) — so this only
# checks that the documented name `official-images/sles16` exists and is
# Active, not which URL it came from. Node scheduling (stage=prod) is
# optional here per Exercise 3.4's own text, so a mismatch only warns.
#
# Run from the KVM/EC2 host as any user, no sudo needed:
#   /opt/suse-virt-workshop/checks/check-exercise-3.sh

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

NS="prod"
IMAGE_NS="official-images"
IMAGE_NAME="sles16"
VM_NAME="algo-trader-01"

FAIL=0
fail(){ echo "FAIL: $*"; FAIL=1; }
warn(){ echo "WARN: $*"; }

# --- 2.7's image, confirmed Active ---
if kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" "${IMAGE_NAME}" &>/dev/null; then
  PROGRESS="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" "${IMAGE_NAME}" \
    -o jsonpath='{.status.progress}' 2>/dev/null)"
  [ "${PROGRESS}" = "100" ] || fail "${IMAGE_NS}/${IMAGE_NAME} found but not Active yet (progress=${PROGRESS:-0})."
else
  fail "Image ${IMAGE_NS}/${IMAGE_NAME} not found. Did you use a different name in 2.7?"
fi

# --- 2.8's Cloud Config Template (a plain ConfigMap, not a dedicated CRD) ---
if ! kubectl get configmap -n "${NS}" prod &>/dev/null; then
  warn "Cloud Config Template prod/prod not found (ConfigMap 'prod' missing in namespace ${NS}) — 2.8 is optional, skip if you didn't do it."
fi

# --- 3.2-3.4: algo-trader-01 VM ---
if ! kubectl get vm -n "${NS}" "${VM_NAME}" &>/dev/null; then
  fail "VirtualMachine ${VM_NAME} not found in ${NS}."
else
  CORES="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.spec.template.spec.domain.cpu.cores}' 2>/dev/null)"
  [ "${CORES}" = "2" ] || fail "${VM_NAME} has ${CORES:-?} CPU cores, expected 2."

  # requests.memory is NOT what you typed in the UI — Harvester applies its own
  # overcommit ratio there. limits.memory is the field that reflects UI input.
  MEM="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.spec.template.spec.domain.resources.limits.memory}' 2>/dev/null)"
  [ "${MEM}" = "2Gi" ] || fail "${VM_NAME} memory limit is '${MEM:-empty}', expected 2Gi."

  STAGE_LABEL="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.metadata.labels.stage}' 2>/dev/null)"
  [ "${STAGE_LABEL}" = "prod" ] || fail "${VM_NAME} label stage='${STAGE_LABEL:-empty}', expected 'prod'."

  SSHNAMES="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.spec.template.metadata.annotations.harvesterhci\.io/sshNames}' 2>/dev/null)"
  echo "${SSHNAMES}" | grep -q "prod/default" || fail "${VM_NAME} sshNames annotation is '${SSHNAMES:-empty}', expected to include prod/default."

  NETNAME="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.spec.template.spec.networks[0].multus.networkName}' 2>/dev/null)"
  [ "${NETNAME}" = "${NS}/service" ] || fail "${VM_NAME} network is '${NETNAME:-empty}', expected ${NS}/service."

  NODE_KEY="$(kubectl get vm -n "${NS}" "${VM_NAME}" \
    -o jsonpath='{.spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].key}' 2>/dev/null)"
  NODE_VAL="$(kubectl get vm -n "${NS}" "${VM_NAME}" \
    -o jsonpath='{.spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].values[0]}' 2>/dev/null)"
  if [ "${NODE_KEY}" = "stage" ] && [ "${NODE_VAL}" = "prod" ]; then
    :
  else
    warn "${VM_NAME} has no stage=prod node scheduling rule — fine, 3.4 marks this optional."
  fi

  VOL_COUNT="$(kubectl get vm -n "${NS}" "${VM_NAME}" -o jsonpath='{.spec.template.spec.volumes}' 2>/dev/null | grep -o 'persistentVolumeClaim' | wc -l | tr -d ' ')"
  [ "${VOL_COUNT}" -ge 2 ] || fail "${VM_NAME} has ${VOL_COUNT} PVC-backed volumes, expected at least 2 (boot disk + market-data-vol)."

  # Harvester's UI always appends a random suffix to volume claim names, so
  # an exact-name lookup never matches a UI-built VM — match by prefix.
  kubectl get pvc -n "${NS}" -o name | grep -q "^persistentvolumeclaim/${VM_NAME}-market-data-vol" \
    || fail "No PVC matching ${VM_NAME}-market-data-vol* found."

  PHASE="$(kubectl get vmi -n "${NS}" "${VM_NAME}" -o jsonpath='{.status.phase}' 2>/dev/null)"
  [ "${PHASE}" = "Running" ] || fail "${VM_NAME} VMI phase is '${PHASE:-empty}', expected Running."
fi

if [[ "${FAIL}" -eq 1 ]]; then
  exit 1
fi

echo "PASS: Exercise 3 objectives verified (${IMAGE_NAME} image, ${VM_NAME} running with 2+ volumes)"
