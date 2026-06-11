#!/usr/bin/env bash
# start-vms.sh — Start the pre-defined Harvester/Rancher domains and wait for
# the Harvester cluster to come up. Called by deploy.sh; env-driven.
#
# Env:
#   HARVESTER_VIP   Harvester floating VIP to poll (default 192.168.122.10)
#   MAX_WAIT        seconds to wait for first response (default 3600)
set -euo pipefail

HARVESTER_VIP="${HARVESTER_VIP:-192.168.122.10}"
MAX_WAIT="${MAX_WAIT:-3600}"
SERIAL_LOG_DIR="/var/log/libvirt/qemu"

log() { echo "[start-vms] $*"; }
die() { echo "[start-vms] ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Must run as root"

start_vm() {
  local name="$1"
  if virsh domstate "$name" 2>/dev/null | grep -q "running"; then
    log "$name is already running."
  else
    virsh start "$name"
    log "Started $name."
  fi
}

log "Starting harvester1 (cluster bootstrap node)..."
start_vm harvester1

log "Monitor install progress: tail -f ${SERIAL_LOG_DIR}/harvester1_serial.log"
log "Waiting for Harvester to respond on ${HARVESTER_VIP} (20-40 minutes)..."

ELAPSED=0
while true; do
  if curl -sk --max-time 5 "https://${HARVESTER_VIP}" 2>&1 | grep -qiE "harvester|DOCTYPE|Found|301|Unauthorized"; then
    log "Harvester is responding."
    break
  fi
  ELAPSED=$((ELAPSED + 30))
  if [[ ${ELAPSED} -ge ${MAX_WAIT} ]]; then
    die "Timed out after ${MAX_WAIT}s. Check: tail -f ${SERIAL_LOG_DIR}/harvester1_serial.log"
  fi
  log "  ${ELAPSED}s / ${MAX_WAIT}s..."
  sleep 30
done

log "Starting harvester2..."
start_vm harvester2

log "Waiting 90s before starting harvester3 (reduces etcd join race)..."
sleep 90

log "Starting harvester3..."
start_vm harvester3

log "Starting rancher VM..."
start_vm rancher

log ""
virsh list --all

# Wait for all 3 Harvester nodes to reach Ready before handing off to setup-rancher.
# Fetch the kubeconfig from the VIP; the Ansible playbook already baked the host
# ed25519 key into the Harvester nodes, so SSH is key-based.
SSH_KEY="/root/.ssh/id_ed25519"
KUBECONFIG_TMP="/tmp/harvester-kubeconfig"

log "Fetching kubeconfig from Harvester VIP (this starts succeeding once node1 is up)..."
FETCH_ELAPSED=0
FETCH_MAX=1800   # 30 min — node1 just responded, but sshd may lag a few minutes
while ! ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \
    "rancher@${HARVESTER_VIP}" \
    "sudo cat /etc/rancher/rke2/rke2.yaml" \
    > "${KUBECONFIG_TMP}" 2>/dev/null; do
  FETCH_ELAPSED=$((FETCH_ELAPSED + 15))
  [[ ${FETCH_ELAPSED} -ge ${FETCH_MAX} ]] && die "Timed out (${FETCH_MAX}s) fetching kubeconfig from VIP"
  log "  SSH not ready yet — ${FETCH_ELAPSED}s / ${FETCH_MAX}s..."
  sleep 15
done
# Rewrite the loopback address in the kubeconfig so kubectl hits the VIP
sed -i "s|127.0.0.1|${HARVESTER_VIP}|g" "${KUBECONFIG_TMP}"
log "Kubeconfig fetched."

log "Waiting for all 3 Harvester nodes to be Ready (up to 90 minutes)..."
NODE_ELAPSED=0
NODE_MAX=5400
until [[ "$(KUBECONFIG=${KUBECONFIG_TMP} kubectl get nodes --no-headers 2>/dev/null \
    | grep -c ' Ready' || echo 0)" -ge 3 ]]; do
  sleep 20
  NODE_ELAPSED=$((NODE_ELAPSED + 20))
  [[ ${NODE_ELAPSED} -ge ${NODE_MAX} ]] && die "Timed out (${NODE_MAX}s) waiting for 3 nodes Ready"
  if [[ $((NODE_ELAPSED % 120)) -eq 0 ]]; then
    READY=$(KUBECONFIG=${KUBECONFIG_TMP} kubectl get nodes --no-headers 2>/dev/null \
        | grep -c ' Ready' || echo 0)
    log "  ${NODE_ELAPSED}s elapsed — ${READY}/3 nodes Ready"
  fi
done
log "All 3 Harvester nodes Ready. Handing off to Rancher setup."
