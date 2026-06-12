# Harvester Lab Config Dir Example (for --config-dir)

This is a minimal self-contained config directory for use with:

    rodeo --config-dir ./harvester-lab-config plan
    rodeo --config-dir ./harvester-lab-config deploy

It demonstrates the EIB-inspired model:

- `definition.yaml` — (optional) your custom or override definition for the rodeo. If present, inventory prefers it over the bundled suse-virt one.
- `rodeo-plan.yaml` — (optional) your plan overrides + parameters. If present and no --config, it is auto-used.
- `certs/` — CA / cert files to make available on nodes or host.
- `manifests/` — Kubernetes manifests to pre-apply to the Harvester/Rancher clusters (future: auto image extraction for airgap like EIB embedded registry).
- `helm/values/` — values.yaml files for pre-deployed helm charts.
- `custom/scripts/` — numbered scripts (e.g. 50-my-setup.sh) run in order for custom bootstrap or post-deploy steps.

## Quick start

1. cp -r rodeo/data/examples/harvester-lab-config /tmp/my-lab
2. Edit rodeo-plan.yaml in place (or cp your own) for baremetal/instruqt, resources, etc. The shipped one has placeholders for secrets.
3. (optional) customize definition.yaml in the dir (it overrides the bundled profile one for this rodeo).
4. Add artifacts to certs/, manifests/, etc. as needed.
5. rodeo --config-dir /tmp/my-lab plan
6. rodeo --config-dir /tmp/my-lab deploy

The dir now ships with definition.yaml + rodeo-plan.yaml ready for testing.

The inventory (build_inventory) records `_config_dir` with the discovered files.
Future phases (build command, artifact embedding) will use the contents.

See the top of rodeo/data/profiles/suse-virt/definition.yaml for the authoritative format and more subdir ideas.
