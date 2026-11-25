---
slug: the-arrival-welcome
id: ermykdy1tbse
type: challenge
title: "<span id="assignment.7" lang="en" hist="vertrex-bank">\U0001F3E6 Chapter 1: The Arrival</span>"
teaser: <span id="assignment.8" lang="en" hist="vertrex-bank">Vertex Trust Bank is drowning in legacy <span id="ch1.intro1.1" lang="nolang" no>hypervisor</span>
  costs. Step into the boardroom, take command of SUSE Virtualization,
  and inspect your new command center.</span>
notes:
- type: text
  contents: |
    <span id="assignment.1" lang="en" no>
    # Welcome to the <span id="assignment.1.1"  lang="nolang" no>SUSE Virtualization Rodeo!</span>

    Please wait while we prepare your lab environment.

    </span><span lang="en" id="ch1.waiting1" hist="vertrex-bank">The rain is lashing against the windows of Vertex Trust Bank headquarters...
    Sarah, the CTO, is waiting for you in the boardroom.</span>
    <img class="logos" src="../assets/logos/suse_logo.svg"/>
tabs:
- id: 3veafppy6ial
  title: SUSE Virtualization UI
  type: service
  hostname: kvm-host
  path: /
  port: 8443
  protocol: https
- id: ljaolp3q406m
  title: Cluster Terminal
  type: terminal
  hostname: kvm-host
- id: ihjqc1cl533q
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

> <a href="https://kvm-host.[[ Instruqt-Var key="_SANDBOX_ID" hostname="kvm-host" ]].instruqt.io:8443">https://kvm-host.[[ Instruqt-Var key="_SANDBOX_ID" hostname="kvm-host" ]].instruqt.io:8443</a>

Navigate to the </span> [button label="SUSE Virtualization UI" variant="success"](tab-0) <span id="assignment.4" lang=en> tab and log in using your credentials.

Open the [button label="Rancher Prime UI" variant="success"](tab-2), log in with the same credentials, and select **Virtualization Management** from the left menu.

5. Below it you can see the registration instructions. Follow them, and remember to select **<span id="assignment.6.14" lang="nolang" no>Insecure Skip TLS Verify</span>** when editing the <b class="highlightcopy">cluster-registration-url</b> setting. This should be done in [button label="SUSE Virtualization UI" variant="success"](tab-0).

6. Switch back to [button label="Rancher Prime UI" variant="success"](tab-2) and click on "<span id="assignment.2.1" lang="nolang" no>Harvester</span> Clusters" on the top left of the UI.

You will spend most of this mission in the UI, <span id="ch1.task3a"  lang="en" hist="vertrex-bank">but an architect always verifies their emergency access</span>. Click on the [button label="Cluster Terminal" variant="success"](tab-1) tab and run one command to check that your connection to the underlying <span id="assignment.2.2" lang="nolang" no>Kubernetes</span> engine is active:

The [button label="SUSE Virtualization UI" variant="success"](tab-0) already shows information about the storage health, but it is also possible to access the <span id="assignment.2.7" lang="nolang" no>SUSE Storage</span> (<span id="assignment.2.8" lang="nolang" no>Longhorn</span>) dashboard by enabling the **Extension developer features**:

New to <span id="assignment.2.2" lang="nolang" no>Kubernetes</span>? **Skip ahead freely**: everything that matters is in the UI. If you want to peek at the machinery, run these extra checks in the [button label="Cluster Terminal" variant="success"](tab-1):
