---
slug: the-invisible-intruder-networking
id: 6y9uhwn9zyll
type: challenge
title: "<span id="assignment.100" lang="en" hist="vertrex-bank">\U0001F575️ Chapter 5: The Invisible Intruder</span>"
teaser: <span id="assignment.101" lang="en" hist="vertrex-bank">A 2 AM security alert. The public web server shares a flat network with the
  bank's most sensitive database. Build a software-defined vault and lock the database
  inside.</span>
tabs:
- id: 69jpoti7gjds
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: hssojxkhutjx
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: pg01vcvbyns3
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

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.107" lang="en" no>, navigate to <span id="assignment.107.1" lang="nolang" no>**Networks**</span> in the left menu, then select <span id="assignment.107.2" lang="nolang" no>**Cluster Network Configuration**</span>:

**An extra isolated network is needed for QA: <span id="assignment.2.2" lang="nolang" no>Kubernetes</span> network policies.** We need to be able to replicate this setup in QA to make sure there are no surprises when moving into production, apply a strict policy that drops unauthorized traffic at the pod level, underneath the VLAN isolation. In the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.115" lang="en" no>, apply a default deny-all ingress policy to the secure namespace:
