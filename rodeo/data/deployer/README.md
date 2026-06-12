# Legacy note

The bash deployer (`deploy.sh`, `lib/start-vms.sh`, `lib/setup-rancher.sh`)
was retired in rodeo-cli v0.3. Use `rodeo deploy` — the same logic lives in
`rodeo/engine/cluster.py` (ClusterPhase) and `rodeo/engine/rancher.py`
(RancherPhase).

Only `inventory.local` is still used: it is the Ansible inventory referenced
by the plan default `ansible.inventory: deployer/inventory.local`.
