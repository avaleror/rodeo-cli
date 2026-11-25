---
slug: the-rising-tide-live-migration
id: xjv2r0tfyztq
type: challenge
title: "<span id="assignment.65" lang="en" hist="vertrex-bank">\U0001F30A Chapter 4: The Rising Tide</span>"
teaser: <span id="assignment.66" lang="en" hist="vertrex-bank">A coolant leak is flooding the rack hosting the Payment Gateway. Execute a
  zero-downtime live migration before the hardware shorts out, while transactions
  keep flowing.</span>
tabs:
- id: fpgxlmifoynn
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: dltxk4yfrhwa
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: js6k1cai9uqc
  title: Rancher Prime UI
  type: service
  hostname: kvm-host
  port: 30002
  protocol: https
difficulty: intermediate
timelimit: 2400
enhanced_loading: null
---

[[ Instruqt-Var key="RANCHER_PASSWORD" hostname="kvm-host" ]]

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.73" lang="en" no>, go to <span id="assignment.40.2" lang="nolang" no>**Virtual Machines**</span>:

Switch to the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.76" lang="en" no>. You need a continuous heartbeat monitor to prove the network connection remains unbroken during the evacuation.

ssh -o StrictHostKeyChecking=accept-new  sles@[[ Instruqt-Var key="PAYMENT_GATEWAY_IP" hostname="kvm-host" ]] 'ping 192.168.122.1'

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.78" lang="en" no>, go to **<span id="assignment.6.2" lang="nolang" no>Virtual Machines</span>** and locate the following instance:

Immediately switch back to the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.81" lang="en" no> tab and watch the ping sequence.

Back in the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.86" lang="en" no>, the <span id="assignment.79.1" lang="nolang" no>**Node**</span> column for <b class="highlightcopy">webserver-prod</b> now shows a **different node** than the one you wrote down. </span><span id="assignment.87" lang="en" hist="vertrex-bank">The gateway physically moved while its customers never noticed.</span>

ssh -o StrictHostKeyChecking=accept-new  sles@[[ Instruqt-Var key="PAYMENT_GATEWAY_IP" hostname="kvm-host" ]] "hostname && uptime"

Return to the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.90" lang="en" no>. Locate the virtual machine you paused earlier:

In the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.94" lang="en" no>, go to <span id="assignment.94.1" lang="nolang" no>**Hosts**</span>:

New to <span id="assignment.2.2" lang="nolang" no>Kubernetes</span>? **Skip ahead freely.** Otherwise: every migration is itself a <span id="assignment.2.2" lang="nolang" no>Kubernetes</span> object, which means it is auditable. In the </span> [button label="Cluster Terminal" variant="success"](tab-1) <span id="assignment.98" lang="en" no>:
