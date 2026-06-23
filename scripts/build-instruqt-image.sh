#!/usr/bin/env bash
# build-instruqt-image.sh — full unattended build of the suse-virt-rodeo-180 Instruqt image.
#
# Run as root on the geekohive Instruqt sandbox (n2-standard-32, SLES 16).
# Sequence: clean → install rodeo-cli → host deps → init → deploy → load Leap 16
#           image → stop VMs.
# When done, snapshot geekohive as suse/suse-virt-rodeo-180 from the Instruqt console.

set -euo pipefail

RODEO_VERSION="v0.10.1"
RODEO_VENV="/opt/rodeo-venv"
RODEO="/usr/local/bin/rodeo"
LAB_DIR="/root/rodeo-lab"
IMAGE_DIR="/var/lib/libvirt/images"
HARVESTER_VIP="192.168.122.10"
RANCHER_IP="192.168.122.9"
RANCHER_PORT="30002"
LEAP16_URL="https://download.opensuse.org/distribution/leap/16.0/appliances/Leap-16.0-Minimal-VM.x86_64-kvm-and-xen.qcow2"

step() { echo; echo ">>> [$1] $2"; }

# ─── 1. CLEAN UP ──────────────────────────────────────────────────────────────
step "1/6" "Cleaning up any previous state..."

for vm in harvester1 harvester2 harvester3 rancher; do
  virsh destroy   "$vm" 2>/dev/null || true
  virsh undefine --nvram "$vm" 2>/dev/null || true
done
virsh net-destroy  default 2>/dev/null || true
virsh net-undefine default 2>/dev/null || true

rm -f  "$IMAGE_DIR"/harvester*.qcow2     \
       "$IMAGE_DIR"/harvester*_vars.bin  \
       "$IMAGE_DIR"/rancher*.qcow2       \
       "$IMAGE_DIR"/Leap-*.qcow2         \
       "$IMAGE_DIR"/harvester-config-*.iso \
       "$IMAGE_DIR"/harvester-v*.iso
rm -rf /srv/harvester-pxe/
rm -rf ~/.rodeo/ "$LAB_DIR" "$RODEO_VENV"
rm -f  "$RODEO"

echo ">>> Clean complete."

# ─── 2. INSTALL RODEO-CLI ─────────────────────────────────────────────────────
step "2/6" "Installing rodeo-cli $RODEO_VERSION..."

zypper install -y --no-recommends python3-pip python3-venv git
python3 -m venv "$RODEO_VENV"
"$RODEO_VENV/bin/pip" install --quiet \
  "git+https://github.com/avaleror/rodeo-cli.git@$RODEO_VERSION"
ln -sf "$RODEO_VENV/bin/rodeo" "$RODEO"

echo ">>> $(rodeo --version)"

# ─── 3. HOST DEPENDENCIES ─────────────────────────────────────────────────────
step "3/6" "Installing host dependencies (ansible, kubectl, collections)..."

"$RODEO" install-deps

# ─── 4. INIT LAB ──────────────────────────────────────────────────────────────
step "4/6" "Initialising lab (harvester profile, deployment_target: instruqt)..."

mkdir -p "$LAB_DIR"
"$RODEO" init --profile harvester --dir "$LAB_DIR"

echo ">>> Admin password: $(grep harvester_admin_password ~/.rodeo/secrets.yaml | awk '{print $2}')"

# ─── 5. DEPLOY (2–3 h) ────────────────────────────────────────────────────────
step "5/6" "Deploying lab infra (kvm_host → vms → pxe_server → cluster → rancher)..."
echo ">>> Tail serial logs in a second terminal:"
echo ">>>   tail -f /var/log/libvirt/qemu/harvester1_serial.log"

"$RODEO" deploy --no-tui --config-dir "$LAB_DIR"

# Quick sanity check — Rancher must show harvester cluster active.
echo ">>> Verifying Rancher cluster state..."
PASS=$(cat /root/rancher-password)
TOKEN=$(curl -sk -X POST \
  "https://${RANCHER_IP}:${RANCHER_PORT}/v3-public/localProviders/local?action=login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$PASS\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

CLUSTER_STATE=$(curl -sk -H "Authorization: Bearer $TOKEN" \
  "https://${RANCHER_IP}:${RANCHER_PORT}/v3/clusters" \
  | python3 -c "
import sys, json
clusters = {c['name']: c['state'] for c in json.load(sys.stdin)['data']}
print(clusters.get('harvester','missing'))
")

if [[ "$CLUSTER_STATE" != "active" ]]; then
  echo "ERROR: harvester cluster state is '$CLUSTER_STATE', expected 'active'"
  exit 1
fi
echo ">>> Harvester cluster: active."

# ─── 6. LOAD LEAP 16 IMAGE ───────────────────────────────────────────────────
step "6/6" "Loading openSUSE Leap 16 KVM image into Harvester..."

HVTOKEN=$(cat /root/harvester-token)

# Create the image record — idempotent (ignore 409 conflict if it already exists).
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" -X POST \
  -H "Authorization: Bearer $HVTOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"metadata\": {\"name\": \"leap16\", \"namespace\": \"default\"},
    \"spec\": {
      \"displayName\": \"openSUSE Leap 16\",
      \"url\": \"$LEAP16_URL\",
      \"sourceType\": \"download\"
    }
  }" \
  "https://${HARVESTER_VIP}/v1/harvesterhci.io.virtualmachineimages")

if [[ "$HTTP_CODE" != "201" && "$HTTP_CODE" != "409" ]]; then
  echo "ERROR: image create returned HTTP $HTTP_CODE"
  exit 1
fi

echo ">>> Waiting for leap16 image to become active (up to 30 min)..."
ELAPSED=0
MAX_WAIT=1800
until curl -sk -H "Authorization: Bearer $HVTOKEN" \
    "https://${HARVESTER_VIP}/v1/harvesterhci.io.virtualmachineimages/default/leap16" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status',{}).get('storageClassName') else 1)" \
    2>/dev/null; do
  sleep 15
  ELAPSED=$((ELAPSED + 15))
  PROGRESS=$(curl -sk -H "Authorization: Bearer $HVTOKEN" \
    "https://${HARVESTER_VIP}/v1/harvesterhci.io.virtualmachineimages/default/leap16" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('progress','?'))" 2>/dev/null || echo "?")
  echo ">>>   ${ELAPSED}s / ${MAX_WAIT}s — progress: ${PROGRESS}%"
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "ERROR: leap16 image did not become active within ${MAX_WAIT}s"
    exit 1
  fi
done
echo ">>> leap16 image: active."

# ─── STOP VMs FOR SNAPSHOT ───────────────────────────────────────────────────
echo
echo ">>> Stopping all VMs cleanly for snapshot..."
"$RODEO" stop --yes --all --config-dir "$LAB_DIR"

# Wait for all VMs to be shut off.
ELAPSED=0
MAX_WAIT=300
while virsh list --all | grep -q " running"; do
  sleep 5
  ELAPSED=$((ELAPSED + 5))
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "WARNING: some VMs still running after ${MAX_WAIT}s — force-stopping..."
    for vm in harvester1 harvester2 harvester3 rancher; do
      virsh destroy "$vm" 2>/dev/null || true
    done
    break
  fi
done

echo
echo ">>> VM state:"
virsh list --all
echo
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Build complete. All VMs shut off.                      ║"
echo "║                                                          ║"
echo "║  Next: snapshot geekohive as suse-virt-rodeo-180        ║"
echo "║        from the Instruqt web console.                   ║"
echo "╚══════════════════════════════════════════════════════════╝"
