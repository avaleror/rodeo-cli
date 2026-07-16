# harvester-ha — 3-node Harvester, no Rancher (etcd HA)

A 3-node Harvester HCI cluster with **no Rancher** — like the 2-node `test`
profile, but three nodes, so you get a real 3-member etcd HA control plane.

```bash
rodeo up --profile harvester-ha
```

Or as a config-dir:

```bash
rodeo --config-dir ./harvester-ha-config plan
sudo -E rodeo --config-dir ./harvester-ha-config deploy
```

## What it deploys

- 3 Harvester nodes (`harvester1/2/3`), all control-plane + etcd
- 16 GiB RAM / 6 vCPU / 320 GB disk per node
- No Rancher VM — the `suse-virt` profile skips the rancher phase automatically
  because the topology has no Rancher node

## Sizing notes

- **CPU:** 3 × 6 = 18 vCPU. Mild overcommit on a 16-thread host is fine
  (validated live; load stayed ~5 after bootstrap).
- **Disk:** 320 GB per node. Harvester's Elemental installer always carves a
  fixed 150 GiB persistent partition (container images, etc.) regardless of
  disk size — going much smaller starves it and containerd fails ("no space")
  so the cluster never converges; going bigger leaves Longhorn's own partition
  more room (~165 GB at 320 GB, vs ~95 GB at the old 250 GB).
- **Host:** ~50 GiB RAM + ~1000 GB free disk for the three nodes.

## Files

- `definition.yaml` — the topology (3 Harvester nodes; no Rancher node/components)
- `rodeo-plan.yaml` — resources, network, credentials (`??key` from `~/.rodeo/secrets.yaml`)
- `certs/`, `custom/scripts/` — optional artifacts
- `<hostname>/` — manifests to `kubectl apply` on that node (see docs/custom-rodeos.md)
- `manifests/`, `helm/values/` — reserved for a future phase; nothing consumes them yet
