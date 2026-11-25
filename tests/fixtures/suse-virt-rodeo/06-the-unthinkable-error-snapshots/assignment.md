---
slug: the-unthinkable-error-snapshots
id: nkrkc4vyyywt
type: challenge
title: '<span id="assignment.116" lang="en" hist="vertrex-bank">⏪ Chapter 6: The Unthinkable Error</span>'
teaser: <span id="assignment.117" lang="en" hist="vertrex-bank">A slipped cursor just deleted a $100M settlement record. Turn back the clock
  with VM snapshots, verify the recovery in a safe staging clone, then make protection
  permanent with scheduled off-cluster backups.</span>
tabs:
- id: lygpkmkmyndn
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: gofsbdvdnoyj
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: 4accqnqjfweo
  title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
difficulty: intermediate
timelimit: 3600
enhanced_loading: null
---

[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]

In the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.122" lang="en" no>, log into the virtual machine (it may take a couple of minutes until the VM starts):

**Now capture the baseline.** Switch to the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.123" lang="en" no>:

Return to the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.126" lang="en" no> and simulate the junior admin's terrible mistake:

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.127" lang="en" no>.

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.129" lang="en" no>:

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.134" lang="en" no>:

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.136" lang="en" no>:
