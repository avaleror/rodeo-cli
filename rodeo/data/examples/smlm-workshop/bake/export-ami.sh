#!/bin/bash
# export-ami.sh — the aws variant's counterpart of export-image.sh. Run where the
# `aws` CLI can reach the bake account, after generalise.sh powered the
# smlm.rodeo.lab instance off.
#
# Creates a private AMI from the stopped instance: AMIs are private to the
# account by default — anyone in that account may use it, nobody outside.
# Hand its ID to the workshop as smlm_image_ami (operator secret), together
# with smlm_image_admin_pass from the bake lab's .rodeo-secrets.yaml.
set -euo pipefail

region="${AWS_REGION:?set AWS_REGION to the region of the bake account}"
name="smlm.rodeo.lab"
id=$(aws ec2 describe-instances --region "${region}" \
      --filters "Name=tag:Name,Values=${name}" "Name=instance-state-name,Values=stopped" \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [[ -z "${id}" || "${id}" == "None" ]]; then
    echo "ERROR: no stopped instance tagged ${name} in ${region} — run generalise.sh inside it first." >&2
    exit 1
fi
# The AMI and its snapshot bill every month they exist, so they carry the bake
# instance's rodeo tags (issue #175): ManagedBy + rodeo-workshop always, plus
# owner and deployment when the instance has them.
keep='`["rodeo-workshop","rodeo-owner","rodeo-deployment","rodeo-version","rodeo-created-by"]`'
copied=$(aws ec2 describe-instances --region "${region}" --instance-ids "${id}" \
      --query "Reservations[0].Instances[0].Tags[?contains(${keep}, Key)]" --output json | tr -d '\n')
[[ "${copied}" == "null" ]] && copied="[]"
copied=${copied#*[}; copied=${copied%]*}
tags='{"Key":"ManagedBy","Value":"rodeo"}'
grep -q '"rodeo-workshop"' <<<"${copied}" || tags+=',{"Key":"rodeo-workshop","Value":"smlm-workshop"}'
[[ -n "${copied//[[:space:]]/}" ]] && tags+=",${copied}"
spec="[{\"ResourceType\":\"image\",\"Tags\":[${tags}]},{\"ResourceType\":\"snapshot\",\"Tags\":[${tags}]}]"
ami=$(aws ec2 create-image --region "${region}" --instance-id "${id}" \
      --name "smlm-workshop-server-$(date +%Y%m%d%H%M)" \
      --description "smlm-workshop SMLM server, channels pre-synced (private)" \
      --tag-specifications "${spec}" \
      --query ImageId --output text)
# A 300 GB snapshot outlasts `aws ec2 wait image-available` (10 min) and can take over 3 h: poll for up to 6 h.
echo "# waiting for ${ami} to become available (large snapshots take a while)"
state=pending
for _ in $(seq 720); do
    state=$(aws ec2 describe-images --region "${region}" --image-ids "${ami}" \
            --query 'Images[0].State' --output text)
    [[ "${state}" == available || "${state}" == failed ]] && break
    sleep 30
done
if [[ "${state}" != available ]]; then
    echo "ERROR: ${ami} is ${state}; check it in the EC2 console (AMIs)." >&2
    exit 1
fi
echo "# smlm_image_ami:"
echo "${ami}"
