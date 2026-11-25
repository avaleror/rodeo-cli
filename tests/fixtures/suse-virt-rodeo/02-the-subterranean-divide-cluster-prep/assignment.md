---
slug: the-subterranean-divide-cluster-prep
id: tmmoesxdhg4b
type: challenge
title: "<span id="assignment.12" lang="en" hist="vertrex-bank">\U0001F6D7 Chapter 2: The Subterranean Divide</span>"
teaser: <span lang="en" hist="vertrex-bank" id="ts2">Two hardware silos, two teams that barely speak. Descend into the datacenter,
  map the node topology, and give every disk in the fabric a price tag the bank can
  live with.</span>
tabs:
- id: gix6w5fqkxd6
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: duhbmh2ml3qo
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: zgpgmllqoznu
  title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
difficulty: basic
timelimit: 2400
enhanced_loading: null
---

[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]

Go to the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.16" lang="en" no>, navigate to the left-hand menu, and click on **<span id="assignment.6.1" lang="nolang" no>Hosts</span>**.

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.19" lang="en" no>, select <span id="assignment.19.1" lang="nolang" no>**Namespaces**</span> from the left-hand menu. You will notice <b class="highlightcopy">prod</b> already sitting in the list. <span id="assignment.19.2" lang="en" hist="vertrex-bank">The platform team provisioned it before you arrived, and it is where the bank's production workloads will live.</span>

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.22" lang="en" no>, go to <span id="assignment.22.1" lang="nolang" no>**Advanced > Storage Classes**</span> and click on <b class="highlightcopy">harvester-longhorn</b>.

1. Go to the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.25" lang="en" no> and SSH into one of the <span id="ch1.intro1.2" lang="nolang" no>SUSE Virtualization</span> hosts:

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.26" lang="en" no>, under <span id="assignment.22.1" lang="nolang" no>**Advanced > Storage Classes**</span>:

New to <span id="assignment.2.2" lang="nolang" no>Kubernetes</span>? **Skip ahead freely.** If you are curious, everything you just did in the UI is also visible through the <span id="assignment.2.2" lang="nolang" no>Kubernetes</span> API, open the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.30" lang="en" no>:
