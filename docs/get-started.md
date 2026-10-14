# Get started

## Host requirements

You need a Linux host (SLES 16 / Leap 16 recommended) with nested KVM available, root or passwordless sudo, and enough RAM for the profile you pick, see the table below.

To drive remote or cloud hosts from a laptop instead (macOS or any other Linux), the same `install.sh` installs rodeo as a control machine: see [Install on Linux and macOS](install.md).

## Install

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash</div>
</div>

On SLES 16 / Leap 16 this installs rodeo as a KVM host: it clones the repo, sets up a Python environment internally, and links `rodeo` as a system command. No venv to activate, no PATH to set, no sudo prefix. On a laptop it installs a control machine instead, see [Install](install.md).

## Deploy

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up</div>
</div>

That's it. `rodeo up` checks the host, picks a profile that fits the available RAM, generates `~/.rodeo/secrets.yaml`, self-escalates with sudo, deploys, and prints the URLs and credentials to log in. It also wraps itself in a tmux session automatically, so a dropped SSH or Instruqt connection doesn't kill a running deploy, reattach any time with `tmux attach -t rodeo-<profile>`.

To pick a specific profile instead of letting `rodeo up` choose:

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up --profile rancher-test   <span class="rc-val"># Rancher + single-node K3s and RKE2, ~18 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile rancher        <span class="rc-val"># Rancher + K3s, RKE2 and 3-node RKE2, ~30 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester-ha   <span class="rc-val"># 3-node Harvester HA, ~52 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester      <span class="rc-val"># 3-node Harvester + Rancher, ~72 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile suse-edge      <span class="rc-val"># Rancher + Elemental + EIB + edge nodes</span></div>
</div>

## Pick a profile

| Profile | What it builds | RAM |
|---|---|---|
| `rancher-test` | Rancher Prime + single-node K3s and RKE2 clusters | ~18 GiB |
| `rancher` | Rancher Prime + single-node K3s, single-node RKE2 and 3-node RKE2 clusters | ~30 GiB |
| `test` | 2-node Harvester cluster, no Rancher | ~36 GiB |
| `harvester-ha` | 3-node Harvester HCI, no Rancher | ~52 GiB |
| `harvester-2n` | 2-node Harvester HCI + Rancher | ~56 GiB |
| `harvester` | 3-node Harvester HCI + Rancher | ~72 GiB |
| `virt-workshop-aws` | `harvester` + image cache, NFS and sample VMs for the SUSE Virtualization workshop | AWS `m8id.8xlarge` |
| `suse-edge` | Rancher + Elemental + EIB + edge nodes | ~40 GiB |
| `smlm-workshop` | SUSE Multi-Linux Manager workshop (lab-in-a-box plugin) | ~30 GiB |

Full walkthroughs live in the profile guides: [Rancher Prime](guide-rancher.md), [Harvester HCI](guide-harvester.md), [SUSE Edge](guide-suse-edge.md).

## Day-2 operations

Once a lab is up, it's something you operate, not a one-shot script:

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo status              <span class="rc-val"># what's deployed, what's drifted</span>
<span class="rc-cmd">$</span> rodeo stop / start        <span class="rc-val"># graceful, infra-aware</span>
<span class="rc-cmd">$</span> rodeo set-password        <span class="rc-val"># rotate admin credentials, no redeploy</span>
<span class="rc-cmd">$</span> rodeo install-extensions  <span class="rc-val"># reconcile UI extensions post-deploy</span>
<span class="rc-cmd">$</span> rodeo clean               <span class="rc-val"># tear the lab down</span></div>
</div>

## Want a lab that isn't bundled?

<code>rodeo new mylab --from harvester</code> scaffolds an editable profile under `~/.rodeo/profiles/mylab`. Edit the YAML, run `rodeo up --profile mylab`, and the lab converges to match. See [Create your own rodeo](custom-rodeos.md).

## On AWS, from your laptop

Install rodeo on your laptop (macOS or any Linux) with the same `install.sh`, log in to AWS, and deploy:

```bash
aws login
rodeo up --profile harvester --target aws --yes
```

No `provider:` block needed: rodeo uses `eu-north-1` (or `$RODEO_AWS_REGION`), the region's default VPC public subnet and the profile's recommended instance, and writes them to the lab plan. It checks the instance type is available before it creates anything.

Every cloud host has a **6-hour dead-man switch**: it powers off and AWS terminates it. Set `provider.ttl_hours` for longer sessions, and tear down sooner with `rodeo destroy --cloud --yes --config-dir ~/rodeo-labs/<profile>`. See [provider fields](reference/plan.md#provider-when-deployment_target-aws).

## Many hosts (workshop fleet)

To run the **same** lab for a whole room, use `rodeo fleet` with a `workshop.yaml` inventory. On AWS, `rodeo fleet provision` creates one host per attendee; `deploy` builds every lab in parallel, and `status`, `doctor`, `retry` and `diagnose` work per lab. The claim portal lets each attendee claim their own lab with a workshop code. See [Fleet](fleet.md).

## Something not working?

Check the [Troubleshooting runbook](runbook.md), it covers stuck deploys, timed-out Harvester installs, unreachable VIPs, and a handful of other issues hit on real hosts.
