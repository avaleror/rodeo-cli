---
title: rodeo-cli
---

<div class="rc-hero" markdown>
<div class="rc-eyebrow">// declarative lab deployment</div>

# Deploy real lab infrastructure without writing Ansible or touching libvirt

<p class="rc-tagline">rodeo-cli is a declarative CLI that turns a YAML file into a working lab on KVM: SUSE Virtualization (Harvester), Rancher Prime, SUSE Edge, SUSE Telco Cloud, or a plain KVM host. The profile is the lab. Where it runs is separate: a SLES 16 host, an Instruqt builder, AWS, Google Cloud, or a KVM host you already own.</p>

<div class="rc-hero-actions">
  <a href="get-started/" class="rc-btn rc-btn-primary">Get started →</a>
  <a href="https://github.com/avaleror/rodeo-cli" class="rc-btn rc-btn-secondary">View on GitHub</a>
</div>
</div>

<div class="rc-stats" markdown>
<div class="rc-stat"><div class="rc-stat-num">12</div><div class="rc-stat-label">Bundled profiles</div></div>
<div class="rc-stat"><div class="rc-stat-num">1,800+</div><div class="rc-stat-label">Tests</div></div>
<div class="rc-stat"><div class="rc-stat-num">1</div><div class="rc-stat-label">Command to deploy</div></div>
<div class="rc-stat"><div class="rc-stat-num rc-stat-num--text">GPL-3.0</div><div class="rc-stat-label">License</div></div>
</div>

## Why it exists

Standing up a Harvester cluster or a Rancher Prime instance for a demo, a workshop, or a test usually means writing Ansible, wiring libvirt networks by hand, and re-learning the same iPXE boot chain every time. rodeo-cli replaces all of that with one idea: describe the lab you want, and let a plan/apply pipeline build it.

A **profile** is a config-dir with two YAML files. `rodeo-plan.yaml` sets resources and credentials. `definition.yaml` describes the topology: nodes, network, exposed services, boot order. The CLI reads those, runs `rodeo plan` to show you the diff against the host, and `rodeo deploy` to converge. Re-runs are safe: deploy reconciles VM memory/vCPU drift by default (`--no-reconcile` to opt out); use `--force` or `--from` when you need a broader re-apply.

## Main features

<div class="rc-grid" markdown>

<div class="rc-card" markdown>
<div class="rc-card-label">Declarative</div>
### Plan, then apply
`rodeo plan` diffs your YAML against the live host before anything changes. No surprises, no guessing what a re-run will touch.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Batteries included</div>
### 12 bundled profiles
Harvester HCI (2-node, 3-node, HA, with or without Rancher), the SUSE Virtualization workshop, Rancher Prime with K3s and RKE2, SUSE Edge, SUSE Telco Cloud, SUSE Multi-Linux Manager, and plain KVM hosts.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Yours to shape</div>
### Custom rodeos
`rodeo new mylab --from harvester` scaffolds an editable profile. Change the topology, re-run, and the lab converges to match, no forking the tool.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Built for real hosts</div>
### One-command on-ramp
`rodeo up` checks the host, picks a lab that fits the available RAM, generates secrets, self-escalates with sudo, and wraps itself in tmux so a dropped SSH session does not kill a live deploy.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">No surprises</div>
### Secrets stay out of git
Credentials live in `~/.rodeo/secrets.yaml`, chmod 600, referenced from plans with `??key` placeholders. Nothing sensitive ever needs to touch a repo.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Day-2 ready</div>
### Manage, not just deploy
`rodeo status`, `rodeo stop`/`start`, `rodeo set-password`, `rodeo install-extensions`, `rodeo clean`: the lab is a thing you operate, not a one-shot script.
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Where it runs</div>
### Five deployment targets
`baremetal` and `instruqt` deploy on the machine you are on. `aws` and `gcp` create the machine from a laptop and build the lab over SSH. `remote` uses a KVM host you already own. Cloud hosts terminate after 6 hours unless you set `provider.ttl_hours`. Vultr and Hetzner are named in the fleet docs and are not implemented. See [Get started](get-started.md).
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Workshops</div>
### A lab per attendee
`rodeo fleet` builds the same lab on many hosts in parallel, with status, retry and
diagnose per lab. A claim portal lets each attendee grab their own lab with a
workshop code. See [Fleet](fleet.md).
</div>

</div>

## See it in one command

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
<span class="rc-cmd">$</span> rodeo up
<span class="rc-val">→ checking host resources...
→ picking a profile that fits (harvester-ha, ~52 GiB)
→ generating secrets...
→ deploying...</span>
<span class="rc-cmd">✓</span> <span class="rc-val">Lab is up. URLs and credentials printed below.</span></div>
</div>

<p>Ready to try it? Head to <a href="get-started/">Get started</a>, or jump straight to a <a href="guide-rancher/">profile guide</a>.</p>

<div style="margin-top:1rem">
<span class="rc-tag accent">GPL-3.0</span>
<span class="rc-tag">Python 3.10+</span>
<span class="rc-tag">KVM / libvirt</span>
<span class="rc-tag">Ansible under the hood</span>
</div>
