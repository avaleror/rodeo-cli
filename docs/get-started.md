# Get started

## Host requirements

You need a Linux host (SLES 16 / Leap 16 recommended) with nested KVM available, root or passwordless sudo, and enough RAM for the profile you pick — see the table below.

## Install

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash</div>
</div>

This clones the repo, sets up a Python environment internally, and links `rodeo` as a system command. No venv to activate, no PATH to set, no sudo prefix.

## Deploy

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up</div>
</div>

That's it. `rodeo up` checks the host, picks a profile that fits the available RAM, generates `~/.rodeo/secrets.yaml`, self-escalates with sudo, deploys, and prints the URLs and credentials to log in. It also wraps itself in a tmux session automatically, so a dropped SSH or Instruqt connection doesn't kill a running deploy — reattach any time with `tmux attach -t rodeo-<profile>`.

To pick a specific profile instead of letting `rodeo up` choose:

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up --profile rancher        <span class="rc-val"># Rancher Prime only, ~10 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester-ha   <span class="rc-val"># 3-node Harvester HA, ~52 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester      <span class="rc-val"># 3-node Harvester + Rancher, ~60 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile suse-edge      <span class="rc-val"># Rancher + Elemental + EIB + edge nodes</span></div>
</div>

## Pick a profile

| Profile | What it builds | RAM |
|---|---|---|
| `rancher` | Rancher Prime on K3s, single VM | ~10-16 GiB |
| `harvester-2n` | 2-node Harvester HCI + Rancher | ~40 GiB |
| `harvester-ha` | 3-node Harvester HCI, no Rancher | ~52 GiB |
| `harvester` | 3-node Harvester HCI + Rancher | ~60 GiB |
| `suse-edge` | Rancher + Elemental Operator + EIB + 4 edge nodes | varies |
| `test` | Minimal single-node Harvester for fast iteration | smallest |

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

## Many hosts (workshop fleet)

To deploy the **same** lab on a list of remote KVM hosts from your laptop (bare
metal today), use `rodeo fleet` with a `workshop.yaml` inventory — doctor, status,
deploy, diagnose, retry, and an access URL sheet.

**Shipped:** F0–F2.1 (JSON reports, fan-out, deploy/retry/access/diagnose).  
**Roadmap:** MCP (F3); F4a AWS provision shipped (MVP); next GCP → Vultr Bare Metal → Hetzner (F4b–d).

### Single-host AWS

`rodeo up --target aws` provisions one EC2 KVM host then remote-deploys. Pick
`--instance-tier budget|recommended|performance` (or set `provider.instance_type`);
rodeo checks regional availability before create. See
[provider fields](reference/plan.md#provider-when-deployment_target-aws).

```bash
pip install 'rodeo-cli[aws]'
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

See [Fleet](fleet.md) and [Fleet roadmap](fleet.md#roadmap).

## Something not working?

Check the [Troubleshooting runbook](runbook.md) — it covers stuck deploys, timed-out Harvester installs, unreachable VIPs, and a handful of other issues hit on real hosts.
