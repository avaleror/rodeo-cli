---
slug: the-flash-crash-first-vm
id: 09d4eiczcvaw
type: challenge
title: '<span id="assignment.33" lang="en" hist="vertrex-bank">⚡ Chapter 3: The Flash Crash</span>'
teaser: <span id="assignment.34" lang="en" hist="vertrex-bank">The Asian markets are melting down and the quants need a calculation engine
  NOW. Deploy a fully configured VM with storage and credentials in minutes, not days.</span>
tabs:
- id: 6byxu4pxkfpm
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: 381amyptjwzi
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: wxzurljjianr
  title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
difficulty: basic
timelimit: 3000
enhanced_loading: null
---

[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]

Go to the </span> [button label="<span id="ch1.intro1.2" lang="nolang" no>SUSE Virtualization</span> UI" variant="success"](tab-0) <span id="assignment.39" lang="en" no>, navigate to **<span id="assignment.6.3" lang="nolang" no>Images</span>** on the left side panel, and confirm that the base <span id="assignment.39.1" lang="nolang" no>**SLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2**</span> operating system image is present and marked as **<span id="assignment.6.17" lang="nolang" no>Active</span>**.

Monitor the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.61" lang="en" no> until the virtual machine transitions to the **Running** state.

New to <span id="assignment.2.2" lang="nolang" no>Kubernetes</span>? **Skip ahead freely.** Otherwise, back in the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.62" lang="en" no>, look at what the platform actually created for you:
