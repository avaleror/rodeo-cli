---
slug: the-stampede-automation
id: euwnv5ojhvfl
type: challenge
title: "<span id="assignment.138" lang="en" no>\U0001F920 Chapter 7: The Stampede</span>"
teaser: <span id="assignment.139" lang="en" hist="vertrex-bank">The markets are in freefall and the quants need the calculation fleet scaled
  from three nodes to five, now. Forge a golden VM template and stamp out identical
  machines on demand.</span>
tabs:
- id: xxc2ymjtxzih
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: uclhjzflraeo
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: inaridrpaxka
  title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
difficulty: intermediate
timelimit: 3000
enhanced_loading: null
---

[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]

In </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.144" lang="en" no> navigate to <span id="assignment.144.1" lang="nolang" no>**Advanced > Templates**</span> and click <span id="assignment.19.3" lang="nolang" no>**Create**</span>, then fill in the following details:

In </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.148" lang="en" no> go to **<span id="assignment.6.2" lang="nolang" no>Virtual Machines</span>** and click <span id="assignment.19.3" lang="nolang" no>**Create**</span>, then fill in the following details:

In </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.154" lang="en" no> navigate to the <span id="assignment.40.2" lang="nolang" no>**Virtual Machines**</span> section:

New to <span id="assignment.2.2" lang="nolang" no>Kubernetes</span>? **Skip ahead freely.** Otherwise, prove in the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.157" lang="en" no> that the UI, the fleet, and the API all agree:
