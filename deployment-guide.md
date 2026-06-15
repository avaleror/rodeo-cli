# Step-by-Step Deployment Guide for Custom Rodeo Labs

## Prerequisites

Before deploying your custom lab, ensure you have:
1. A Linux host with KVM and libvirt installed
2. Python 3.10 or higher
3. Sufficient RAM for the lab profile you're creating (check with `rodeo doctor`)
4. Proper permissions to install packages and create VMs

## Step-by-Step Deployment Process

### Step 1: Prepare Your Environment

Navigate to your rodeo-cli directory:
```bash
cd /Users/avalero/GitHub/rodeo-cli
```

Set up the virtual environment:
```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e .
```

### Step 2: Verify Host Readiness

Check that your host meets requirements for deploying labs:
```bash
rodeo doctor
```

This command will analyze your system and recommend the largest profile that fits your available RAM.

### Step 3: Create Your Lab Profile (if not already done)

If you haven't created your custom profile yet, scaffold one from an existing lab:

```bash
rodeo new mylab --from harvester
```
This creates a copy of the Harvester lab in `~/.rodeo/profiles/mylab/` that you can customize.

### Step 4: Customize Your Lab Definition

Edit your definition.yaml file to meet your requirements:
```bash
$EDITOR ~/.rodeo/profiles/mylab/definition.yaml
```

Make changes as needed such as:
- Reducing node count for smaller clusters
- Adjusting resource allocation (memory, CPU)
- Modifying network settings
- Adding or changing exposed services

### Step 5: Validate Your Configuration

Preview what the deployment would change without making actual modifications:
```bash
cd ~/.rodeo/profiles/mylab
rodeo plan
```

Review the output to ensure your changes are correct.

### Step 6: Deploy Your Lab

Execute the full deployment process:
```bash
rodeo up --profile mylab
```

This command will:
1. Verify host readiness (if not already done)
2. Generate necessary credential files (~/.rodeo/secrets.yaml)
3. Run the deployment pipeline through multiple phases (kvm_host → vms → pxe_server → cluster → rancher → finalise)
4. Configure all VMs and services
5. Present login information for accessing your lab components

### Step 7: Access Your Lab

After successful deployment, you'll see output containing URLs and credentials to access:
- Harvester dashboard
- Rancher UI  
- SSH access details
- Any other exposed services

### Step 8: Monitor During Deployment

If you want to watch the deployment process in real-time:
```bash
rodeo watch
```

This shows both phase progress and VM serial logs.

## Managing Your Deployment

### Checking Status
```bash
rodeo status
```
Displays VM states, VIP reachability, and phase progress.

### Stopping Your Lab
```bash
rodeo stop
```
Gracefully stops all infrastructure in reverse definition order.

### Starting Your Lab
```bash
rodeo start
```
Starts host services and VMs in correct definition order.

### Cleaning Up
To completely remove the lab:
```bash
rodeo clean --all --yes
```

This destroys all VMs, disks, and state.