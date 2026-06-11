#!/usr/bin/env bash
# setup-rancher.sh — Install K3s + Rancher Prime inside the rancher VM and
# import the Harvester cluster. Called by deploy.sh; fully env-driven (no
# secrets baked in).
#
# Env (all have deploy.sh-provided values):
#   RANCHER_VM_IP          rancher VM management IP        (default 192.168.122.9)
#   RANCHER_VERSION        Rancher Prime chart version     (default 2.13.1)
#   K3S_VERSION            K3s install version             (default v1.31.4+k3s1)
#   HARVESTER_VIP          Harvester floating VIP          (default 192.168.122.10)
#   HARVESTER_OS_PASSWORD  Harvester admin password (Rancher import API login)
#   CERT_MANAGER_VERSION   cert-manager version            (default v1.16.2)
#
# SSH is key-based: the playbook bakes the host public key into the Rancher VM
# (cloud-init, root) and the Harvester nodes (os.ssh_authorized_keys, user rancher).
set -euo pipefail

RANCHER_VM_IP="${RANCHER_VM_IP:-192.168.122.9}"
RANCHER_VERSION="${RANCHER_VERSION:-2.13.1}"
K3S_VERSION="${K3S_VERSION:-v1.31.4+k3s1}"
HARVESTER_VIP="${HARVESTER_VIP:-192.168.122.10}"
HARVESTER_OS_PASSWORD="${HARVESTER_OS_PASSWORD:?HARVESTER_OS_PASSWORD is required}"
CERT_MANAGER_VERSION="${CERT_MANAGER_VERSION:-v1.16.2}"
# Single known admin password for the Rancher AND Harvester dashboards/APIs.
LAB_ADMIN_PASSWORD="${LAB_ADMIN_PASSWORD:-Foobar12345\$}"

RANCHER_VM_USER="root"
HARVESTER_VM_USER="rancher"      # Harvester's default OS user; root login is disabled
RANCHER_HOSTNAME="rancher.${RANCHER_VM_IP}.sslip.io"
RANCHER_ADMIN_PASS_FILE="/root/rancher-password"
RANCHER_NODEPORT="${RANCHER_NODEPORT:-30002}"
# K3s has traefik disabled, so Rancher is reached on a NodePort, not :443.
# All setup-time API calls and the agent server-url use this endpoint.
RANCHER_API="https://${RANCHER_VM_IP}:${RANCHER_NODEPORT}"
SSH_KEY="${SSH_KEY:-/root/.ssh/id_ed25519}"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"

log() { echo "[setup-rancher] $*"; }
die() { echo "[setup-rancher] ERROR: $*" >&2; exit 1; }

ssh_vm() { ssh ${SSH_OPTS} "${RANCHER_VM_USER}@${RANCHER_VM_IP}" "$@"; }

for cmd in ssh curl jq kubectl; do
  command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
done

# ---------------------------------------------------------------------------
# Wait for rancher VM SSH
# ---------------------------------------------------------------------------
log "Waiting for rancher VM SSH on ${RANCHER_VM_IP}..."
for i in $(seq 1 30); do
  if ssh_vm "echo ok" &>/dev/null; then log "SSH is up."; break; fi
  [[ $i -eq 30 ]] && die "SSH not reachable after 5 minutes"
  log "  Attempt $i/30 failed, retrying in 10s..."
  sleep 10
done

# ---------------------------------------------------------------------------
# K3s
# ---------------------------------------------------------------------------
log "Installing K3s ${K3S_VERSION}..."
ssh_vm bash -s <<EOF
set -euo pipefail
export INSTALL_K3S_VERSION="${K3S_VERSION}"
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644 --disable traefik --node-name rancher
EOF

log "Waiting for K3s node Ready..."
ssh_vm bash -s <<'EOF'
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
for i in $(seq 1 60); do
  [[ "$(kubectl get nodes --no-headers 2>/dev/null | awk '{print $2}' | head -1)" == "Ready" ]] && echo "Ready" && exit 0
  echo "  Waiting... (${i}/60)"; sleep 10
done
echo "ERROR: K3s node never became Ready" >&2; exit 1
EOF

# ---------------------------------------------------------------------------
# Helm + cert-manager + Rancher Prime
# ---------------------------------------------------------------------------
log "Installing Helm..."
ssh_vm bash -s <<'EOF'
set -euo pipefail
curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
EOF

log "Adding Helm repos + cert-manager ${CERT_MANAGER_VERSION}..."
ssh_vm bash -s <<EOF
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm repo add rancher-prime https://charts.rancher.com/server-charts/prime
helm repo add jetstack https://charts.jetstack.io
helm repo update
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.crds.yaml
helm install cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --version ${CERT_MANAGER_VERSION}
kubectl -n cert-manager rollout status deployment/cert-manager --timeout=180s
kubectl -n cert-manager rollout status deployment/cert-manager-webhook --timeout=180s
EOF

log "Installing Rancher Prime ${RANCHER_VERSION}..."
ssh_vm bash -s <<RANCHEREOF
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm install rancher rancher-prime/rancher \\
  --namespace cattle-system --create-namespace \\
  --version "${RANCHER_VERSION}" \\
  --set hostname="${RANCHER_HOSTNAME}" \\
  --set bootstrapPassword="admin" \\
  --set replicas=1 \\
  --set ingress.tls.source=rancher \\
  --wait --timeout 600s
RANCHEREOF

# Expose Rancher on a fixed NodePort (K3s has traefik disabled, so there is no
# ingress on :443). Strategic-merge patch the rancher service on its :443 port
# (merge key "port") so the real https targetPort + the :80 port are preserved.
log "Exposing Rancher on NodePort ${RANCHER_NODEPORT}..."
ssh_vm bash -s <<EOF
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl -n cattle-system patch svc rancher -p '{"spec":{"type":"NodePort","ports":[{"port":443,"nodePort":${RANCHER_NODEPORT}}]}}'
EOF

log "Waiting for Rancher /ping on ${RANCHER_API}..."
for i in $(seq 1 60); do
  curl -sk --max-time 5 "${RANCHER_API}/ping" | grep -q "pong" && { log "Rancher is up."; break; }
  [[ $i -eq 60 ]] && die "Rancher did not respond after 10 minutes"
  log "  Attempt $i/60..."; sleep 10
done

# ---------------------------------------------------------------------------
# Admin password + API token + server URL
# ---------------------------------------------------------------------------
log "Setting Rancher admin password..."
TEMP_TOKEN=$(curl -sk -X POST "${RANCHER_API}/v3-public/localProviders/local?action=login" \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"admin"}' | jq -r '.token')
[[ -n "${TEMP_TOKEN}" && "${TEMP_TOKEN}" != "null" ]] || die "Failed to get initial login token"

# Set the fixed lab admin password (predictable for API/UI use).
ADMIN_PASS="${LAB_ADMIN_PASSWORD}"
curl -sk -X POST "${RANCHER_API}/v3/users?action=changepassword" \
  -H "Authorization: Bearer ${TEMP_TOKEN}" -H "Content-Type: application/json" \
  -d "{\"currentPassword\":\"admin\",\"newPassword\":\"${ADMIN_PASS}\"}"
echo "${ADMIN_PASS}" > "${RANCHER_ADMIN_PASS_FILE}"; chmod 600 "${RANCHER_ADMIN_PASS_FILE}"
log "Admin password set to the lab password; written to ${RANCHER_ADMIN_PASS_FILE}"

API_TOKEN=$(curl -sk -X POST "${RANCHER_API}/v3-public/localProviders/local?action=login" \
  -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PASS}\"}" | jq -r '.token')
[[ -n "${API_TOKEN}" && "${API_TOKEN}" != "null" ]] || die "Failed to authenticate with new password"

# server-url must be reachable by the Harvester cattle-cluster-agent on the same
# network — point it at the NodePort, not :443.
curl -sk -X PUT "${RANCHER_API}/v3/settings/server-url" \
  -H "Authorization: Bearer ${API_TOKEN}" -H "Content-Type: application/json" \
  -d "{\"value\":\"${RANCHER_API}\"}"

# ---------------------------------------------------------------------------
# Import the Harvester cluster
# ---------------------------------------------------------------------------
log "Importing Harvester cluster into Rancher..."
CLUSTER_ID=$(curl -sk -X POST "${RANCHER_API}/v3/clusters" \
  -H "Authorization: Bearer ${API_TOKEN}" -H "Content-Type: application/json" \
  -d '{"type":"cluster","name":"harvester","harvesterConfig":{},"annotations":{"field.cattle.io/description":"Harvester HCI cluster for SUSE Virt Rodeo"}}' \
  | jq -r '.id')
log "  Cluster record: ${CLUSTER_ID}"

MANIFEST_URL=$(curl -sk "${RANCHER_API}/v3/clusterregistrationtokens?clusterId=${CLUSTER_ID}" \
  -H "Authorization: Bearer ${API_TOKEN}" | jq -r '.data[0].manifestUrl')

HARVESTER_KUBECONFIG="/tmp/harvester-kubeconfig"
if [[ ! -f "${HARVESTER_KUBECONFIG}" ]]; then
  log "  Fetching Harvester kubeconfig from the VIP (rke2.yaml is root-only, so sudo)..."
  ssh ${SSH_OPTS} "${HARVESTER_VM_USER}@${HARVESTER_VIP}" \
    "sudo cat /etc/rancher/rke2/rke2.yaml" > "${HARVESTER_KUBECONFIG}" 2>/dev/null \
    || die "Could not fetch Harvester kubeconfig. Is the cluster up and the host key accepted on the nodes?"
  sed -i "s|127.0.0.1|${HARVESTER_VIP}|g" "${HARVESTER_KUBECONFIG}"
fi

# Persist the Harvester kubeconfig where the Instruqt track expects it
# (setup-geekohive reads /root/.kube/harvester.yaml). Harmless for non-Instruqt use.
mkdir -p /root/.kube
cp "${HARVESTER_KUBECONFIG}" /root/.kube/harvester.yaml
chmod 600 /root/.kube/harvester.yaml
log "  Harvester kubeconfig saved to /root/.kube/harvester.yaml (API at ${HARVESTER_VIP}:6443)"

# ---------------------------------------------------------------------------
# Patch RKE2 CoreDNS to forward aerogrid.com to the host libvirt dnsmasq.
# Pods inside Harvester use the cluster's own CoreDNS which by default has no
# knowledge of aerogrid.com. A forward zone closes that gap so every pod can
# resolve virtualization/rancher/alpha/bravo/charlie.aerogrid.com.
# ---------------------------------------------------------------------------
LAB_DNS_SERVER="${LAB_DNS_SERVER:-192.168.122.1}"
log "Patching RKE2 CoreDNS: aerogrid.com → ${LAB_DNS_SERVER}..."
if KUBECONFIG="${HARVESTER_KUBECONFIG}" kubectl get cm rke2-coredns-rke2-coredns -n kube-system &>/dev/null; then
  COREDNS_CM="rke2-coredns-rke2-coredns"
elif KUBECONFIG="${HARVESTER_KUBECONFIG}" kubectl get cm coredns -n kube-system &>/dev/null; then
  COREDNS_CM="coredns"
else
  log "  WARNING: CoreDNS ConfigMap not found — pod DNS patch skipped"
  COREDNS_CM=""
fi
if [[ -n "${COREDNS_CM}" ]]; then
  CURRENT_CF=$(KUBECONFIG="${HARVESTER_KUBECONFIG}" \
    kubectl get cm "${COREDNS_CM}" -n kube-system -o jsonpath='{.data.Corefile}')
  if echo "${CURRENT_CF}" | grep -q "aerogrid.com"; then
    log "  aerogrid.com zone already present — skipping"
  else
    KUBECONFIG="${HARVESTER_KUBECONFIG}" \
      kubectl get cm "${COREDNS_CM}" -n kube-system -o json \
      | jq --arg zone "
aerogrid.com:53 {
    errors
    forward . ${LAB_DNS_SERVER}
    cache 30
}" '.data.Corefile += $zone' \
      | KUBECONFIG="${HARVESTER_KUBECONFIG}" kubectl apply -f -
    log "  CoreDNS patched. Reload plugin picks it up within ~30s."
  fi
fi

curl -sk "${MANIFEST_URL}" | KUBECONFIG="${HARVESTER_KUBECONFIG}" kubectl apply -f -
log "  Import manifest applied. Waiting for the cluster to go Active..."

for i in $(seq 1 60); do
  STATE=$(curl -sk "${RANCHER_API}/v3/clusters/${CLUSTER_ID}" \
    -H "Authorization: Bearer ${API_TOKEN}" | jq -r '.state // "unknown"')
  log "  Cluster state: ${STATE} (attempt $i/60)"
  [[ "${STATE}" == "active" ]] && break
  [[ $i -eq 60 ]] && log "WARNING: cluster not Active in time — check the Rancher UI."
  sleep 30
done

# Set the Harvester dashboard admin password to the lab password. Harvester's
# embedded Rancher ships a bootstrap admin/admin and forces a password set on
# first login; this does that non-interactively (best-effort — if it is already
# set to the lab password, the bootstrap login simply returns no token).
log "Setting the Harvester dashboard admin password..."
HV_BOOTSTRAP_TOKEN=$(curl -sk -X POST "https://${HARVESTER_VIP}/v3-public/localProviders/local?action=login" \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"admin"}' | jq -r '.token // empty')
if [[ -n "${HV_BOOTSTRAP_TOKEN}" ]]; then
  curl -sk -X POST "https://${HARVESTER_VIP}/v3/users?action=changepassword" \
    -H "Authorization: Bearer ${HV_BOOTSTRAP_TOKEN}" -H "Content-Type: application/json" \
    -d "{\"currentPassword\":\"admin\",\"newPassword\":\"${LAB_ADMIN_PASSWORD}\"}" >/dev/null \
    && log "  Harvester admin password set to the lab password."
else
  log "  Bootstrap admin/admin login returned no token — Harvester admin password may already be set."
fi

HARVESTER_TOKEN=$(curl -sk -X POST "https://${HARVESTER_VIP}/v3-public/localProviders/local?action=login" \
  -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"${LAB_ADMIN_PASSWORD}\"}" | jq -r '.token // empty')
[[ -n "${HARVESTER_TOKEN}" ]] && { echo "${HARVESTER_TOKEN}" > /root/harvester-token; chmod 600 /root/harvester-token; log "Harvester API token saved to /root/harvester-token"; }

# ---------------------------------------------------------------------------
# Eject installer ISOs so the disks boot standalone
# ---------------------------------------------------------------------------
eject_cdrom() {
  local domain="$1" dev="$2" err
  err=$(virsh change-media "$domain" "$dev" --eject --live --config 2>&1) || {
    echo "$err" | grep -qiE "no media|not a cdrom|No such file" || log "WARNING: eject ${domain}:${dev} -- ${err}"
  }
}
log "Ejecting installer ISOs from Harvester VMs..."
for node in harvester1 harvester2 harvester3; do
  for cdrom in sda sdb; do eject_cdrom "$node" "$cdrom"; done
  log "  ${node}: CDROMs ejected"
done

log ""
log "Rancher URL    : ${RANCHER_API}  (NodePort)"
log "Admin password : $(cat ${RANCHER_ADMIN_PASS_FILE})"
log "Cluster ID     : ${CLUSTER_ID}"
