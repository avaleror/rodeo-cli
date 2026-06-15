# Rodeo CLI Lab Deployment Runbook

## Overview
This runbook explains how to create custom lab deployment models and definition files from existing ones in the rodeo-cli project. The process involves scaffolding a new profile based on an existing one, then customizing the definition file.

## Building Your Own Lab Deployment Model

### Step 1: Scaffolding a Custom Lab

To create a custom lab, start by copying an existing working profile:

```bash
rodeo new mylab --from harvester
```

This command creates a copy of the Harvester profile in `~/.rodeo/profiles/mylab/` that you can customize.

### Step 2: Understanding Profile Structure

A rodeo profile consists of two main YAML files:

1. **rodeo-plan.yaml** - Defines the resources, credentials, and deployment settings:
   ```yaml
   type: suse-virt              # pipeline type (suse-virt or rancher)
   name: mylab                  # used for state + libvirt object names
   deployment_target: baremetal # baremetal | instruqt
   
   resources:
     harvester: { memory_mib: 16384, vcpu: 8, disk_gb: 270 }
     rancher:   { memory_mib: 8192,  vcpu: 4, disk_gb: 60 }
   
   credentials:                 # ??key resolves from ~/.rodeo/secrets.yaml
     harvester_os_password: "??harvester_os_password"
     lab_admin_password: "??lab_admin_password"
     harvester_token: "??harvester_token"
   
   storage:
     device: ""                 # "" = single disk; or /dev/nvme1n1 on a multi-disk host
     image_dir: /var/lib/libvirt/images
   ```

2. **definition.yaml** - Describes the topology (nodes, network, exposed services):
   ```yaml
   definition:
     name: mylab
     start_order: [harvester1, harvester2, harvester3, rancher]
     harvester_node_names: [harvester1, harvester2, harvester3]
     harvester_ready_count: 3

     network:
       cidr: 192.168.122.0/24
       gateway: 192.168.122.1
       domain: aerogrid.com

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
   ```

### Step 3: Customizing Your Lab

After scaffolding, edit the definition.yaml file to change:

- Cluster size (edit nodes, start_order, harvester_node_names, harvester_ready_count)
- Resource allocation (resources.harvester and resources.rancher in plan)
- Network settings
- Exposed services
- Node configurations

### Step 4: Validation

Before deploying, validate your changes:

```bash
cd ~/.rodeo/profiles/mylab
rodeo plan
```

## Deploying Your Custom Lab

### Step 1: Check Host Requirements

First, verify your host is ready for the deployment:

```bash
rodeo doctor
```

### Step 2: Deploy the Lab

Deploy using your custom profile:

```bash
rodeo up --profile mylab
```

The command will:
- Verify host readiness
- Generate necessary secrets
- Execute the deployment pipeline (kvm_host → vms → pxe_server → cluster → rancher → finalise)
- Print login information

### Step 3: Access Your Lab

After successful deployment, you'll get URLs and credentials to access your lab components.

## Common Modifications

### Changing Cluster Size
To reduce a 3-node Harvester cluster to 1 node:
1. Modify `nodes` section to include only one harvester VM
2. Update `start_order` accordingly
3. Change `harvester_node_names` to match
4. Set `harvester_ready_count` to 1

### Changing Resources
Adjust memory, CPU, and disk allocation in the `rodeo-plan.yaml` resources section:

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
```

### Exposing New Services
Add entries to `exposed_services` in definition.yaml to make additional services accessible from outside the host.

## Troubleshooting

If your deployment fails:
1. Check host requirements with `rodeo doctor`
2. Review changes made to the definition files
3. Validate configuration with `rodeo plan`
4. Look at error messages for specific issues
5. Clean previous deployments if needed with `rodeo clean --all --yes`

## Notes

- Profiles deploy from `~/.rodeo/profiles/<name>/` in place
- Changes to files are picked up automatically on re-deploy
- Credentials remain in `??key` form; secrets.yaml is auto-generated
- Structural changes require testing on a real KVM host before relying on them
- Always backup your custom profiles before making significant changes