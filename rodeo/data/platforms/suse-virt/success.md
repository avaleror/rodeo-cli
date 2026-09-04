[bold]<span lang="en" id="success.suse-virt.heading">First things to try</span>[/bold]
  rodeo status                 # <span lang="en" id="success.suse-virt.status">health + phase progress</span>
  rodeo ssh {{ ssh_target }}       # <span lang="en" id="success.suse-virt.ssh">shell into the VM</span>
{% if has_harvester %}  <span lang="en" id="success.suse-virt.harvester">In Harvester: create a VM from an image, then watch it boot</span>
{% elif has_rancher %}  <span lang="en" id="success.suse-virt.rancher">In Rancher: explore Cluster Management and install an app from Charts</span>
{% endif %}