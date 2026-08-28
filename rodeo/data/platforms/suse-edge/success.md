{% if edge_nodes %}[bold]<span lang="en" id="success.suse-edge.edge-ref">Edge node reference</span>[/bold]  (<span lang="en" id="success.suse-edge.edge-ref-hint">static DHCP — MAC determines IP</span>)
  node    MAC                  IP
{% for e in edge_nodes %}  {{ "%-7s"|format(e.name) }} {{ "%-20s"|format(e.mac) }} {{ e.ip }}  (DHCP pre-assigned)
{% endfor %}
{% endif %}[bold]<span lang="en" id="success.suse-edge.heading">First things to try</span>[/bold]
  rodeo status                 # <span lang="en" id="success.suse-edge.status">health + phase progress</span>
  rodeo ssh eib            # <span lang="en" id="success.suse-edge.ssh-eib">shell into the EIB VM (build Elemental OS images here)</span>
  rodeo ssh <host>/<vm>    # <span lang="en" id="success.suse-edge.ssh-hop">from laptop: hop via KVM/EC2 host</span>
  <span lang="en" id="success.suse-edge.eib-edit">On the eib VM: edit /home/eib-config/edge-definition.yaml</span>
    <span lang="en" id="success.suse-edge.eib-reg">→ replace REPLACE_WITH_REGISTRATION_URL with the MachineRegistration URL</span>
    <span lang="en" id="success.suse-edge.eib-build">→ run EIB to build the Elemental OS image (base OS from Hauler: http://localhost:8080)</span>
  <span lang="en" id="success.suse-edge.pull-image">From the KVM host: rodeo pull-edge-image   # seed edge1/2/3 boot disks</span>
  rodeo start edge1 edge2 edge3              # <span lang="en" id="success.suse-edge.start-edges">boot edge nodes into Elemental</span>
  <span lang="en" id="success.suse-edge.fleet">In Rancher: Fleet → Git Repos → <span no id="success.suse-edge.fleet-name">alien-geeko</span> is waiting for edge clusters</span>
    <span lang="en" id="success.suse-edge.label">→ label your edge cluster: demo=true  edge-type=x86-cluster</span>
