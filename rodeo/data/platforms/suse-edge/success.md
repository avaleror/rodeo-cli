{% if edge_nodes %}[bold]<span lang="en" id="success.suse-edge.edge-ref">Edge node reference</span>[/bold]  (<span lang="en" id="success.suse-edge.edge-ref-hint">static IP baked into each EIB image, matched by MAC</span>)
  node    MAC                  IP
{% for e in edge_nodes %}  {{ "%-7s"|format(e.name) }} {{ "%-20s"|format(e.mac) }} {{ e.ip }}
{% endfor %}
{% endif %}[bold]<span lang="en" id="success.suse-edge.heading">First things to try</span>[/bold]
  rodeo status                 # <span lang="en" id="success.suse-edge.status">health + phase progress</span>
  rodeo ssh eib            # <span lang="en" id="success.suse-edge.ssh-eib">shell into the EIB VM (build Elemental OS images here)</span>
  rodeo ssh <host>/<vm>    # <span lang="en" id="success.suse-edge.ssh-hop">from laptop: hop via KVM/EC2 host</span>
  <span lang="en" id="success.suse-edge.eib-edit">On the eib VM: fetch the eib-config Gitea repo to /home/eib-workspace/</span>
    <span lang="en" id="success.suse-edge.eib-reg">→ get the MachineRegistration URL and fill in elemental/elemental_config.yaml</span>
    <span lang="en" id="success.suse-edge.eib-build">→ build edge1/edge2 (Elemental ISOs) from /home/eib-workspace, edge3/edge4 (RKE2/K3s RAW) from a copy without elemental/</span>
  <span lang="en" id="success.suse-edge.pull-image">From the KVM host: rodeo pull-edge-image --image <file> --nodes <node>   # seed edge1-edge4</span>
  rodeo start edge1 edge2                    # <span lang="en" id="success.suse-edge.start-edges">Elemental nodes install, then register with Rancher</span>
  rodeo start edge3 edge4                    # <span lang="en" id="success.suse-edge.start-standalone">standalone RKE2/K3s nodes boot ready to import</span>
  <span lang="en" id="success.suse-edge.fleet">In Rancher: Fleet → Git Repos → <span no>{{ alien_geeko_fleet_name }}</span> is waiting for edge clusters</span>
    <span lang="en" id="success.suse-edge.label">→ label your edge cluster: <span no>{{ alien_geeko_target_labels_str }}</span></span>
