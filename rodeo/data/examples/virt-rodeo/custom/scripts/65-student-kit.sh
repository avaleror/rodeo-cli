#!/bin/bash
# suse-virt-workshop custom_scripts step: the student kit.
#
# Lets anyone with a plain (non-root) shell on the lab host follow the guide:
# a claim-portal student on AWS gets an unprivileged `student` account with no
# sudo, so nothing the exercises need can live under /root. This installs, at
# /opt/suse-virt-workshop/ (readable by every user on the host):
#   checks/       a copy of the self-check scripts; they only need a kubeconfig
#   lab-vm-key    the shared lab VM key (+ .pub). 70-webserver-prod.sh puts it
#                 on the pre-created VMs and Exercise 2.6 registers it as
#                 prod/default, so one key opens every VM in the lab.
#
# The private key is deliberately world-readable. It only opens the nested lab
# VMs, which every lab user already controls as Harvester admin, so it grants
# nothing new. It never leaves the host and is not the rodeo key in /root/.ssh.
#
# Runs before 70-webserver-prod.sh (needs the key). Idempotent: the key is
# generated once and kept, the checks are re-copied on every run.

set -euo pipefail

log(){ echo ">>> [student-kit] $*"; }

KIT=/opt/suse-virt-workshop
KEY="${KIT}/lab-vm-key"
# The lab dir is two levels up from custom/scripts/ (rodeo runs each script
# from there). Not $RODEO_LAB_DIR: that is rodeo's own data root, not the lab.
LAB_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

install -d -m 755 "${KIT}"

if [ ! -f "${KEY}" ]; then
  log "generating the shared lab VM key ..."
  ssh-keygen -q -t ed25519 -N '' -C "suse-virt-workshop lab VMs" -f "${KEY}"
fi
chmod 644 "${KEY}" "${KEY}.pub"

if [ -d "${LAB_DIR}/checks" ]; then
  log "installing self-checks to ${KIT}/checks ..."
  rm -rf "${KIT}/checks.new"
  cp -r "${LAB_DIR}/checks" "${KIT}/checks.new"
  chmod -R a+rX "${KIT}/checks.new"
  chmod a+x "${KIT}/checks.new"/*.sh
  rm -rf "${KIT}/checks"
  mv "${KIT}/checks.new" "${KIT}/checks"
else
  log "warn: no checks/ in ${LAB_DIR}; self-checks not installed."
fi

log "student kit ready at ${KIT}"
