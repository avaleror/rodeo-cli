#!/bin/bash
# virt-workshop-aws custom_scripts step 3/3.
#
# Pre-creates the "already running in production" VM that suse-virt-rodeo's
# chapter 4 (The Rising Tide, zero-downtime live migration) assumes exists
# before the student arrives — on the Instruqt track it is baked into the
# saved cluster image; here, since every AWS deploy starts from a genuinely
# blank cluster, this script builds the same end state on top of the harvester-
# aws profile's plain harvester+rancher deploy: namespace, VM network, node
# labels, and the VM itself.
#
# Runs after 50-image-cache.sh (needs the cached image already served) and
# 60-nfs-backup-target.sh (order only, no direct dependency) as the last of
# the three custom_scripts. Idempotent — every kubectl step checks before
# creating (this is a no_cache_phase, so it reruns on every `rodeo up`).
#
# KNOWN RISK: the NetworkAttachmentDefinition shape below (bridge on mgmt-br,
# vlan 0 = untagged/native, piggybacking the same L2 as harvester1-3
# themselves) follows Harvester's standard "VM Network on the management
# network" convention, but suse-virt-rodeo's own repo has this baked into
# its image rather than scripted anywhere — there is no proven-working
# manifest to copy. Live-verify VM connectivity after first deploy of this
# profile; adjust here if the network doesn't come up as expected.

set -uo pipefail
export KUBECONFIG="${KUBECONFIG:-/root/.rodeo/harvester-kubeconfig}"

log(){ echo ">>> [webserver-prod] $*"; }

NS="prod"
NET_NAME="service"
NET="${NS}/${NET_NAME}"
IMAGE_NS="official-images"
IMAGE_HTTP_URL="http://192.168.122.1:8889/Leap-16.0-Minimal-VM.x86_64-kvm-and-xen.qcow2"
IMAGE_DISPLAY_NAME="Leap-16.0-Minimal-VM.x86_64-kvm-and-xen.qcow2"
VM_NAME="webserver-prod"

for pubkey_file in /root/.rodeo/ssh/id_ed25519.pub /root/.ssh/id_ed25519.pub /root/.ssh/id_rsa.pub; do
  [ -f "${pubkey_file}" ] && SSH_PUBKEY="$(cat "${pubkey_file}")" && break
done
if [ -z "${SSH_PUBKEY:-}" ]; then
  log "warn: no SSH public key found in any known location; ${VM_NAME} will come up without SSH access."
  SSH_PUBKEY=""
fi

log "waiting for kubeconfig / API server ..."
for _ in $(seq 1 30); do
  kubectl get nodes &>/dev/null && break
  sleep 5
done
kubectl get nodes &>/dev/null || { echo ">>> [webserver-prod] FAILED: API server not reachable" >&2; exit 1; }

# --- Namespace ---------------------------------------------------------
log "ensuring namespace ${NS} ..."
kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# --- Node labels (stage=prod/dev, matching suse-virt-rodeo's exactly one
# non-candidate-node story for chapter 4's live migration) ---------------
log "labeling nodes (harvester1/2 = stage=prod, harvester3 = stage=dev) ..."
for NODE in harvester1 harvester2; do
  kubectl label node "${NODE}" stage=prod migration=yes \
    network.harvesterhci.io/service=true storage=prod --overwrite >/dev/null \
    || log "warn: failed to label ${NODE}"
done
kubectl label node harvester3 stage=dev storage=dev --overwrite >/dev/null \
  || log "warn: failed to label harvester3"

# --- VM network (NetworkAttachmentDefinition) ---------------------------
log "ensuring VM network ${NET} ..."
if ! kubectl get network-attachment-definitions.k8s.cni.cncf.io -n "${NS}" "${NET_NAME}" &>/dev/null; then
  cat <<EOF | kubectl apply -f -
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ${NET_NAME}
  namespace: ${NS}
  annotations:
    network.harvesterhci.io/vlan-id: "0"
    network.harvesterhci.io/route: '{"mode":"auto"}'
spec:
  config: '{"cniVersion":"0.3.1","name":"${NET_NAME}","type":"bridge","bridge":"mgmt-br","promiscMode":true,"vlan":0,"ipam":{}}'
EOF
fi

log "waiting for VM network ${NET} to be reconciled ..."
for _ in $(seq 1 24); do
  kubectl get network-attachment-definitions.k8s.cni.cncf.io -n "${NS}" "${NET_NAME}" &>/dev/null && break
  sleep 5
done

# --- VM image (VirtualMachineImage, downloaded from the local cache) ----
find_image() {
  kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.url}{"\n"}{end}' 2>/dev/null \
    | grep -F "${IMAGE_HTTP_URL}" | awk '{print $1}' | head -n1
}

log "ensuring namespace ${IMAGE_NS} ..."
kubectl create namespace "${IMAGE_NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "checking for cached image in ${IMAGE_NS} ..."
IMAGE_NAME="$(find_image)"
if [ -z "${IMAGE_NAME}" ]; then
  log "not found — creating VirtualMachineImage from ${IMAGE_HTTP_URL} ..."
  cat <<EOF | kubectl create -f -
apiVersion: harvesterhci.io/v1beta1
kind: VirtualMachineImage
metadata:
  generateName: image-
  namespace: ${IMAGE_NS}
spec:
  sourceType: download
  displayName: ${IMAGE_DISPLAY_NAME}
  url: ${IMAGE_HTTP_URL}
  storageClassParameters:
    numberOfReplicas: "1"
EOF
  sleep 5
  IMAGE_NAME="$(find_image)"
fi

log "waiting for image ${IMAGE_NS}/${IMAGE_NAME:-<pending>} to become Active (up to 10 minutes) ..."
READY=""
for _ in $(seq 1 60); do
  [ -z "${IMAGE_NAME}" ] && IMAGE_NAME="$(find_image)"
  if [ -n "${IMAGE_NAME}" ]; then
    READY="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" "${IMAGE_NAME}" \
      -o jsonpath='{.status.progress}' 2>/dev/null)"
    [ "${READY}" = "100" ] && break
  fi
  sleep 10
done

if [ -z "${IMAGE_NAME}" ] || [ "${READY:-}" != "100" ]; then
  echo ">>> [webserver-prod] FAILED: image not Active after 10 minutes — skipping VM creation" >&2
  exit 1
fi
log "image ready: ${IMAGE_NS}/${IMAGE_NAME}"

IMAGE_SC="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" "${IMAGE_NAME}" \
  -o jsonpath='{.status.storageClassName}' 2>/dev/null)"
[ -n "${IMAGE_SC}" ] || { echo ">>> [webserver-prod] FAILED: no storageClassName on ${IMAGE_NS}/${IMAGE_NAME}" >&2; exit 1; }

# The boot disk PVC must be >= the image's own virtual (logical) size, not its
# download size — a qcow2's compressed download can be tiny while its
# filesystem is much larger (this openSUSE image: ~308 MiB download, 24 GiB
# virtual size). A too-small PVC never binds (Longhorn/Harvester can't shrink
# the volume to fit) and the VM sits ErrorUnschedulable forever. Compute the
# real floor from .status.virtualSize instead of hardcoding a value that only
# happens to work for today's cached image.
IMAGE_VIRTUAL_SIZE="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" "${IMAGE_NAME}" \
  -o jsonpath='{.status.virtualSize}' 2>/dev/null)"
GIB=1073741824
DISK_GI=5
if [ -n "${IMAGE_VIRTUAL_SIZE}" ] && [ "${IMAGE_VIRTUAL_SIZE}" -gt 0 ] 2>/dev/null; then
  # Ceiling-divide to whole GiB, then add a 1 GiB buffer.
  NEEDED_GI=$(( (IMAGE_VIRTUAL_SIZE + GIB - 1) / GIB + 1 ))
  [ "${NEEDED_GI}" -gt "${DISK_GI}" ] && DISK_GI="${NEEDED_GI}"
else
  log "warn: could not read image virtualSize, falling back to ${DISK_GI}Gi (may be too small)"
fi
log "boot disk size: ${DISK_GI}Gi (image virtualSize=${IMAGE_VIRTUAL_SIZE:-unknown} bytes)"

# --- The VM itself --------------------------------------------------------
# 1 vCPU / 1 GiB RAM — matches suse-virt-workshop's own documented spec for
# the student-created version of this VM (Exercise 4.1); the boot disk is
# sized dynamically above from the cached image's real virtual size.
if kubectl get vm -n "${NS}" "${VM_NAME}" &>/dev/null; then
  log "${VM_NAME} already exists, skipping creation."
else
  log "creating ${VM_NAME} (1 vCPU / 1 GiB / ${DISK_GI}Gi, DHCP on ${NET}) ..."
  cat <<EOF | kubectl apply -f -
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: ${VM_NAME}
  namespace: ${NS}
  labels:
    stage: prod
  annotations:
    harvesterhci.io/volumeClaimTemplates: |-
      [{"metadata":{"name":"${VM_NAME}-disk-0","annotations":{"harvesterhci.io/imageId":"${IMAGE_NS}/${IMAGE_NAME}"}},"spec":{"accessModes":["ReadWriteMany"],"resources":{"requests":{"storage":"${DISK_GI}Gi"}},"volumeMode":"Block","storageClassName":"${IMAGE_SC}"}}]
spec:
  runStrategy: Always
  template:
    metadata:
      labels:
        harvesterhci.io/vmName: ${VM_NAME}
    spec:
      hostname: ${VM_NAME}
      evictionStrategy: LiveMigrateIfPossible
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: stage
                operator: In
                values:
                - prod
      domain:
        cpu:
          cores: 1
          sockets: 1
          threads: 1
        resources:
          requests:
            cpu: "1"
            memory: 1Gi
          limits:
            cpu: "1"
            memory: 1Gi
        machine:
          type: q35
        devices:
          disks:
          - name: disk-0
            bootOrder: 1
            disk:
              bus: virtio
          - name: cloudinitdisk
            disk:
              bus: virtio
          interfaces:
          - name: default
            model: virtio
            bridge: {}
      networks:
      - name: default
        multus:
          networkName: ${NET}
      volumes:
      - name: disk-0
        persistentVolumeClaim:
          claimName: ${VM_NAME}-disk-0
      - name: cloudinitdisk
        cloudInitNoCloud:
          userData: |
            #cloud-config
            hostname: ${VM_NAME}
            ssh_authorized_keys:
              - ${SSH_PUBKEY}
EOF
fi

log "waiting for ${VM_NAME} to reach Running phase (up to 5 minutes) ..."
PHASE=""
for _ in $(seq 1 30); do
  PHASE="$(kubectl get vmi -n "${NS}" "${VM_NAME}" -o jsonpath='{.status.phase}' 2>/dev/null)"
  [ "${PHASE}" = "Running" ] && { log "${VM_NAME}: Running"; break; }
  sleep 10
done
[ "${PHASE:-}" = "Running" ] || log "warn: ${VM_NAME} not Running yet after 5 minutes (non-fatal, check the UI)."

echo ">>> [webserver-prod] custom_scripts step complete."
exit 0
