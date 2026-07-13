# Harvester Lab Config Dir Example (for --config-dir)

This is a minimal self-contained config directory for use with:

    rodeo --config-dir ./harvester-lab-config plan
    rodeo --config-dir ./harvester-lab-config deploy

It demonstrates the EIB-inspired model:

- `definition.yaml` — (optional) your custom or override definition for the rodeo. If present, inventory prefers it over the bundled suse-virt one.
- `rodeo-plan.yaml` — (optional) your plan overrides + parameters. If present and no --config, it is auto-used.
- `certs/` — CA / cert files to make available on nodes or host.
- `<hostname>/` — manifests to `kubectl apply` on that node, e.g. `harvester1/namespaces.yml` (see docs/custom-rodeos.md).
- `manifests/`, `helm/values/` — reserved for a future phase (bulk-apply / pre-staged Helm values); nothing consumes them yet.
- `custom/scripts/` — numbered scripts (e.g. 50-my-setup.sh) run in order for custom bootstrap or post-deploy steps.

## Quick start (preferred — minimal manual steps)

On clean SLES (KVM/nested enabled): one curl does it all (prereqs, hidden internal setup, link for clean `rodeo` command, lab dir with 2-node example):

```
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/scripts/bootstrap-sles.sh | bash
```

Follow the commands printed by the script (cd ~/harvester-rodeo-lab, source rodeo-secrets.env, rodeo plan, sudo -E rodeo deploy).

This is the minimal manual interaction path. `rodeo` works globally; the lab dir is the clean declarative context.

Lab lifecycle commands (infra-aware from definition):
- `rodeo stop --all --yes` / `start --all --yes`: graceful stop (VMs + host services) / start; restartable.
- `rodeo clean --all --yes --secrets --hard`: full host reset (VMs/networks/states/passwords; packages + binary untouched).

See the SLES test guide for details.

The inventory (build_inventory) records `_config_dir` with the discovered files.
Future phases (build command, artifact embedding) will use the contents.

See the top of rodeo/data/profiles/suse-virt/definition.yaml for the authoritative format and more subdir ideas.
Hardware notes + CPU reasoning are in the comments of the local rodeo-plan.yaml and definition.yaml .
