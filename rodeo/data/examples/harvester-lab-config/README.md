# Harvester Lab Config Dir Example (for --config-dir)

This is a minimal self-contained config directory for use with:

    rodeo --config-dir ./harvester-lab-config plan
    rodeo --config-dir ./harvester-lab-config deploy

It demonstrates the EIB-inspired model:

- `definition.yaml` — (optional) your custom or override definition for the rodeo. If present, inventory prefers it over the bundled suse-virt one.
- `rodeo-plan.yaml` — (optional) your plan overrides + parameters. If present and no --config, it is auto-used.
- `certs/` — CA / cert files to make available on nodes or host.
- `manifests/` — Kubernetes manifests to pre-apply to the Harvester clusters (future: auto image extraction for airgap like EIB embedded registry).
- `helm/values/` — values.yaml files for pre-deployed helm charts.
- `custom/scripts/` — numbered scripts (e.g. 50-my-setup.sh) run in order for custom bootstrap or post-deploy steps.

## Quick start (preferred — minimal manual steps)

Prerequisite (once, on the host): after your venv + `pip install -e`, do `sudo $RODEO install-deps --link` (where $RODEO is the full venv path the first time). This puts a stable `rodeo` symlink in /usr/local/bin so you can use plain `rodeo` / `sudo rodeo` everywhere after.

1. mkdir -p /tmp/my-lab && cd /tmp/my-lab
2. rodeo init --force --example harvester-lab-config
   - This seeds definition.yaml (the 2-node no-Rancher test variant), the tuned rodeo-plan, certs/, manifests/, helm/, custom/ etc. in one shot.
   - It also creates rodeo-secrets.env + rewrites the plan to `??env:` form.
3. source rodeo-secrets.env
4. (Optional) further edit rodeo-plan.yaml / definition.yaml / add your artifacts.
5. rodeo --config-dir . plan
6. sudo -E rodeo --config-dir . deploy --check
7. sudo -E rodeo --config-dir . deploy

Resources are pre-tuned for Ryzen 8c/16t hosts (2*6 vCPU = 12 logical; hyperthreads count as CPUs for qemu, ~16 total visible). storage.device points at the large disk — confirm with lsblk.

(The old `cp -r ...` + manual cd + init still works if you prefer to start from the files in the rodeo source tree.) If `rodeo` command not found, use the full path or ensure /usr/local/bin is in PATH (or activate the venv).

The inventory (build_inventory) records `_config_dir` with the discovered files.
Future phases (build command, artifact embedding) will use the contents.

See the top of rodeo/data/profiles/suse-virt/definition.yaml for the authoritative format and more subdir ideas.
Hardware notes + CPU reasoning are in the comments of the local rodeo-plan.yaml and definition.yaml .
