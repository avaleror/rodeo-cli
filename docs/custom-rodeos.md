# Create your own rodeo (declarative)

A rodeo is described by data, not code. You scaffold one from a working lab, edit two
YAML files, and deploy it. This guide shows the loop and the file format.

```bash
rodeo new mylab --from harvester      # copy a working lab you can edit
$EDITOR ~/.rodeo/profiles/mylab/definition.yaml
rodeo up --profile mylab              # deploy your edited lab
```

---

## Two words that both sound like "profile"

| Term | What it is | Where it lives |
|------|-----------|----------------|
| **engine `type`** | the deploy *pipeline* (which phases run) | `type:` in the plan; code in `rodeo/profiles/` |
| **profile** (`--profile`) | a named, runnable *lab* you pick on the CLI | a config-dir (bundled or under `~/.rodeo/profiles/`) |

There are three engine types today: `suse-virt` (Harvester HCI + Rancher), `rancher`
(Rancher Prime on K3s, no Harvester), and `suse-edge` (Rancher + Elemental + EIB + edge
nodes). Your custom labs pick one of these as their `type` and customize the rest. You
are authoring topology, not a new pipeline.

List everything you can deploy:

```bash
rodeo profiles
```

| Profile | Kind | type | What it is |
|---------|------|------|------------|
| `rancher` | bundled | rancher | 1 VM, Rancher Prime on K3s (smallest) |
| `test` | bundled | suse-virt | 2-node Harvester, no Rancher |
| `harvester-ha` | bundled | suse-virt | 3-node Harvester, no Rancher (etcd HA) |
| `harvester-2n` | bundled | suse-virt | 2-node Harvester + Rancher Prime |
| `harvester` | bundled | suse-virt | 3-node Harvester HCI + Rancher Prime |
| `suse-edge` | bundled | suse-edge | Rancher + Elemental + EIB + edge nodes (SUSE Edge 3.6) |
| *(yours)* | custom | any | whatever you scaffold and edit |

---

## A profile is a config-dir

Two files plus optional artifact folders:

```
mylab/
├── rodeo-plan.yaml     # type, resources, credentials, deployment_target
├── definition.yaml     # the topology (nodes, network, exposed services, host prep)
├── certs/              # optional: CA certs to make available
├── <hostname>/         # optional: manifests to kubectl-apply on that node (see below)
└── custom/scripts/     # optional: numbered scripts
```

`manifests/` and `helm/values/` also show up in bundled examples and are recorded in
inventory metadata, but nothing consumes them yet — they're reserved for a future
phase. Don't put files there expecting them to be applied; use the per-hostname
directories described next.

`rodeo up`, `plan`, and `deploy` auto-detect this dir (walk up from the current
directory), so inside a lab dir you can drop `--config-dir`.

### rodeo-plan.yaml — the knobs

```yaml
type: suse-virt              # pipeline: suse-virt or rancher
name: mylab                  # used for state + libvirt object names
deployment_target: baremetal # baremetal | instruqt

resources:
  harvester: { memory_mib: 16384, vcpu: 8, disk_gb: 320 }
  rancher:   { memory_mib: 8192,  vcpu: 4, disk_gb: 60 }

credentials:                 # ??key resolves from ~/.rodeo/secrets.yaml
  harvester_os_password: "??harvester_os_password"
  harvester_admin_password: "??harvester_admin_password"
  rancher_admin_password: "??rancher_admin_password"
  harvester_token: "??harvester_token"

storage:
  device: ""                 # "" = single disk; or /dev/nvme1n1 on a multi-disk host
  image_dir: /var/lib/libvirt/images
```

### definition.yaml — the topology

The declarative model. You describe the *logical* lab; the renderer compiles it into
concrete MACs, DHCP leases, libvirt network, firewall rules, and PXE data. The bundled
files are heavily commented — read them as the reference:

- Harvester: `rodeo/data/platforms/suse-virt/definition.yaml`
- Rancher: `rodeo/data/platforms/rancher/definition.yaml`
- SUSE Edge: `rodeo/data/platforms/suse-edge/definition.yaml`

Key sections:

```yaml
definition:
  name: mylab
  start_order: [harvester1, harvester2, harvester3, rancher]
  harvester_node_names: [harvester1, harvester2, harvester3]
  harvester_ready_count: 3

  network:
    cidr: 192.168.122.0/24
    gateway: 192.168.122.1
    domain: rodeo.lab

  node_templates:        # the "blueprint" per node flavor
    harvester:
      flavor: harvester
      infra_type: harvester     # makes stop/start infra-aware
      interfaces: [...]         # the NIC "cables"
    rancher:
      flavor: rancher
      infra_type: rancher

  exposed_services:      # becomes host firewall DNAT
    - { name: rancher, host_port: 30002, guest_port: 30002, target: rancher }

  nodes:                 # the concrete VMs
    - name: harvester1
      template: harvester
      ip: 192.168.122.11
      # mac/uuid/hostname are generated if you omit them
```

You can omit MACs, UUIDs, and hostnames — the renderer generates them deterministically
from the plan name. Provide them only when you need exact values.

---

## Applying manifests to a node

Drop `.yaml`/`.yml` files into a directory named after a node's hostname (matching a
`name:` under `nodes:` in `definition.yaml`), for example:

```
mylab/
└── harvester1/
    └── namespaces.yaml
```

The `apply` phase SSHes into that node and runs `kubectl apply -f -` for every file
found there, in sorted order — one directory per node, applied to that node only.
`apply` is the one phase that always re-runs (it is never cached), so editing files
here and re-running `rodeo up --profile mylab` picks up the change immediately,
unlike edits to the topology or resources (see below).

---

## Common edits

**Change cluster size** (Harvester labs): edit `nodes`, `start_order`,
`harvester_node_names`, and `harvester_ready_count` together so they agree, then size
`resources` to your host.

**Change resources**: edit `resources.harvester` / `resources.rancher` in the plan, or
override at deploy time without editing files:

```bash
rodeo up --profile mylab            # or, ad hoc:
rodeo deploy -P resources.harvester.memory_mib=20480
```

**Use a dedicated disk**: set `storage.device: /dev/nvme1n1` in the plan (confirm with
`lsblk`).

**Expose another service**: add an entry to `exposed_services` in the definition.

After editing, preview before you deploy:

```bash
cd ~/.rodeo/profiles/mylab
rodeo plan
```

---

## Re-running after a deploy

Idempotency here is **phase-level, not resource-level**. Each phase (`kvm_host`, `vms`,
`pxe_server`, `cluster`, `rancher`, `finalise`) is marked done in
`~/.rodeo/state/<name>.yaml` once it succeeds, and a plain re-run skips any phase already
marked done — regardless of what you changed in `definition.yaml` or `rodeo-plan.yaml`.
The `apply` phase is the one exception (it never caches, so node-manifest edits always
take effect on the next run — see [Applying manifests to a node](#applying-manifests-to-a-node)).

So after a lab has deployed once, editing `nodes`, `resources`, or `network` and
re-running `rodeo up --profile mylab` used to skip completed phases. As of B2 step 5,
**memory/vCPU drift is reconciled by default** on `rodeo deploy` / `rodeo up` (reset from
`vms`). Topology changes (add/remove nodes) still need a manual path:

```bash
cd ~/.rodeo/profiles/mylab
rodeo plan                  # always shows live drift
rodeo deploy                # reconciles memory/vCPU drift by default
rodeo deploy --no-reconcile # old phase-cache-only behaviour
rodeo deploy --from vms     # resume from a specific phase, or
rodeo deploy --force        # redo every phase, or
rodeo clean --yes && rodeo up --profile mylab   # destroy this lab's VMs and redeploy
```

Default reconcile clears the cached `vms` (and later) phase state when live memory/vCPU
differs from the plan. For **running** domains, libvirt still will not live-resize —
stop/start the domain (or `clean` + redeploy) so the inactive XML is applied.

---

## The loop, end to end

```bash
rodeo new edge-lab --from harvester     # scaffold from the full Harvester lab
$EDITOR ~/.rodeo/profiles/edge-lab/definition.yaml   # trim to 1 node, retune
rodeo profiles                          # confirm it is listed (custom)
rodeo up --profile edge-lab             # doctor → secrets → deploy → login info
```

Custom profiles deploy **in place** from `~/.rodeo/profiles/<name>/`. Before the lab has
deployed once, editing the files and re-running `rodeo up --profile <name>` just works —
there's no state yet, so every phase runs fresh. Once it *has* deployed, re-running picks
up file edits only for the `apply` phase; other phases need `--force` or `--from` (see
[Re-running after a deploy](#re-running-after-a-deploy)) or they're silently skipped. To
start a fresh copy instead of editing in place, scaffold under a new name or pass `--dir`.

---

## Notes

- `--from` must be a bundled base (`rancher`, `test`, `harvester-ha`, `harvester-2n`,
  `harvester`, `suse-edge`). Copy the closest working lab and trim down — it is the
  lowest-risk path to a valid topology.
- Credentials stay in `??key` form; `rodeo up`/`init` generate `~/.rodeo/secrets.yaml`
  for you. No `source` or `sudo -E` needed.
- Deploying a Harvester topology touches the MAC ↔ DHCP ↔ config-ISO chain. After
  structural changes, validate on a real KVM host before relying on it.
- `rodeo generate` is the older interactive generator. Prefer `rodeo new`.
