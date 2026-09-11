# harvester-aws profile

Same 3-node Harvester HCI + Rancher Prime topology as the `harvester` profile
(same `definition.yaml`), but `rodeo-plan.yaml` is pre-tuned for
`deployment_target: aws` instead of `instruqt`/`baremetal`:

- `provider:` block present (fill in `region` and `subnet_id`)
- `resources.harvester.disk_gb: 600` — matches the AWS host-context floor for
  this profile (3 x 600 GB + 60 GB Rancher = 1860 GB), instead of the generic
  `harvester` profile's 320 GB (which `apply_host_context()` would still raise
  automatically on AWS, but showing 600 here means the file isn't
  under-provisioned before the first deploy)
- `provider.instance_tier: recommended` resolves to `m8id.12xlarge`
  (48 vCPU / 192 GiB RAM / a single ~2.85 TiB NVMe device) — see
  `rodeo/providers/instance_catalog.py`

The `harvester` profile itself is untouched and stays generic — this is a
separate, AWS-specific profile, not a replacement.

```bash
rodeo up --yes --no-tmux --profile harvester-aws --target aws
```

See [docs/reference/plan.md](../../../../docs/reference/plan.md) for the full
`provider:` schema and the security-group auto-management behaviour (omit
`security_group_ids` to let rodeo create/manage one for you).
