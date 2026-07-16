# definition.yaml reference

`definition.yaml` is the topology declaration for a lab. It answers: which VMs exist, what are their names and IPs, in what order do they start, how are they networked, and which services are exposed on the host?

It does **not** contain resource sizing, credentials, or deployment target. Those live in [`rodeo-plan.yaml`](plan.md).

---

## Structure overview

```
definition:
  name:                    # lab name (usually matches plan name)
  description:             # human-readable label
  start_order:             # ordered list of VM names to start
  harvester_node_names:    # which of those are Harvester cluster members
  harvester_ready_count:   # how many must reach Ready before the cluster phase completes
  etcd_join_gap_seconds:   # pause before each non-first Harvester node starts

  network:                 # libvirt network config
  node_templates:          # NIC blueprints per VM flavor
  exposed_services:        # host port-forwards to guest services
  boot:                    # PXE/iPXE boot server config (Harvester only)
  storage:                 # lab storage layout
  components:              # high-level recipe description
  harvester:               # Harvester-specific cluster config
  host_prep:               # host OS requirements (sysctls, SELinux, libvirt)
  nodes:                   # concrete VM declarations
```

---

## Full annotated example — 2-node Harvester (no Rancher)

```yaml
apiVersion: 1.0   # increment for breaking schema changes

definition:
  name: suse-virt
  description: "2-node Harvester HCI cluster on nested KVM"

  # The ordered list of VMs to start during the cluster phase.
  # rodeo starts them in this order. Harvester1 always goes first (bootstrap node).
  start_order:
    - harvester1
    - harvester2

  # Which VMs in start_order are Harvester cluster members.
  # Drives: VIP wait, etcd gap application, node Ready count, ISO eject.
  harvester_node_names:
    - harvester1
    - harvester2

  # How many nodes must reach Ready before the cluster phase completes.
  # Must equal len(harvester_node_names) for a healthy cluster.
  harvester_ready_count: 2

  # Seconds to wait before starting each Harvester join node (not the first).
  # Prevents etcd join races when multiple nodes try to join simultaneously.
  # Do not reduce below 60s without live testing — 90s is proven safe.
  etcd_join_gap_seconds: 90

  network:
    name: default          # libvirt network name
    bridge: virbr0         # bridge interface on the host
    mode: nat              # nat | bridge (nat is standard for nested KVM)
    cidr: 192.168.122.0/24
    gateway: 192.168.122.1
    domain: rodeo.lab   # DNS domain for all lab VMs
    dhcp_range:
      start: 192.168.122.100
      end: 192.168.122.254  # static leases (.9–.13, .10 VIP) are outside this range

  # NIC blueprints. Each flavor defines the logical cables a VM of that type has.
  # The renderer (inventory.py) uses these to generate libvirt XML, DHCP leases,
  # iPXE scripts, and firewall rules.
  node_templates:
    harvester:
      flavor: harvester
      ssh_user: rancher
      infra_type: harvester    # tells stop/start this is a clustered Harvester node
      interfaces:
        - role: mgmt           # management NIC: PXE boot, VIP, Kubernetes API, etcd
          model: e1000
          pxe: true            # this NIC gets the iPXE boot entry
          ip_family: static    # DHCP static lease assigned from the node's ip field
        - role: storage        # Longhorn CSI traffic
          model: virtio
        - role: migration      # KubeVirt live migration
          model: virtio
        - role: service        # Kube-OVN primary uplink
          model: virtio
          count: 2             # generates service1 + service2 NICs

    rancher:
      flavor: rancher
      ssh_user: root
      infra_type: rancher      # tells stop/start this is a K3s/Rancher node
      interfaces:
        - role: mgmt
          model: virtio
          ip_family: static

  # Services reachable from outside the host. Each entry becomes a firewalld
  # port_forward (DNAT) rule on the public zone.
  exposed_services:
    - name: harvester-ui
      description: "Harvester dashboard and API"
      host_port: 8443
      guest_port: 443
      target: vip       # "vip" → the Harvester VIP; or a node name like "rancher"
      proto: tcp

    # Full lab (with Rancher) also adds:
    # - name: rancher-ui
    #   host_port: 30002
    #   guest_port: 30002
    #   target: rancher
    #   proto: tcp

  # iPXE boot server config (suse-virt profile only).
  # Drives the pxe_server Ansible role.
  boot:
    pxe:
      http_port: 8080
      http_bind: "{{ libvirt_network_gateway }}"   # binds on the virbr0 IP
      tftp_root: /var/lib/libvirt/dnsmasq
      http_root: /srv/harvester-pxe
      ipxe_efi_url: "https://boot.ipxe.org/x86_64-efi/ipxe.efi"

  storage:
    device: "/dev/nvme1n1"             # leave "" for single-disk hosts
    mount_point: /var/lib/libvirt/images
    fs_type: xfs
    libvirt_pool_name: default
    libvirt_pool_path: /var/lib/libvirt/images
    image_dir: /var/lib/libvirt/images

  # High-level recipe description. Used for introspection ("rodeo describe") and docs.
  components:
    - name: harvester-cluster
      description: "2-node Harvester HCI (RKE2 + Longhorn + KubeVirt + Kube-OVN)"
      nodes: harvester_node_names
      on_host: false
    - name: pxe-server
      description: "iPXE + HTTP server for diskless Harvester node boot"
      on_host: true
    - name: host-network-prep
      description: "libvirt network, firewalld DNAT, sysctls, SELinux for nested KVM"
      on_host: true

  # Harvester-specific cluster configuration embedded in per-node config ISOs.
  harvester:
    networks:
      mgmt:
        role: management
        description: "PXE boot, VIP, Kubernetes API, etcd, RKE2"
      storage:
        role: storage
        description: "Longhorn CSI traffic"
      migration:
        role: migration
        description: "KubeVirt live migration"
      service1:
        role: service
        description: "Kube-OVN primary uplink"
      service2:
        role: service
        description: "Kube-OVN secondary uplink"
    ntp_servers:
      - 0.pool.ntp.org
      - 1.pool.ntp.org

  # Host OS requirements. The kvm_host Ansible role applies these.
  # Change only if you know what you are doing — these values are tested on SLES 16.
  host_prep:
    sysctls:
      - net.bridge.bridge-nf-call-iptables=0
      - net.bridge.bridge-nf-call-ip6tables=0
      - net.bridge.bridge-nf-call-arptables=0
      - net.ipv4.conf.all.rp_filter=2
      - net.ipv4.conf.default.rp_filter=2
    selinux_mode: permissive
    libvirt:
      ovmf_code: /usr/share/qemu/ovmf-x86_64-4m-code.bin
      ovmf_vars_template: /usr/share/qemu/ovmf-x86_64-4m-vars.bin
      use_modular_daemons: true
    network:
      unmanaged_interfaces:
        - virbr0
      rp_filter_loose: true

  # Concrete VM declarations. Each node references a template and adds instance data.
  nodes:
    - name: harvester1
      template: harvester      # references node_templates.harvester above
      index: 1
      hostname: alpha           # alpha.rodeo.lab
      uuid: "1c5e1f38-1fb8-4f03-9eaa-12838f70faa1"
      ip: "192.168.122.11"
      config_iso_name: "harvester-config-node1"
      ssh_user: rancher
      interfaces:
        - role: mgmt
          mac: "02:00:00:0D:62:E1"
        - role: storage
          mac: "02:00:00:0D:63:E1"
        - role: migration
          mac: "02:00:00:0D:64:E1"
        - role: service
          mac: "02:00:00:0D:65:E1"
        - role: service
          mac: "02:00:00:0D:66:E1"
      # Legacy flat fields — kept for Ansible role compatibility during the Phase C transition
      mgmt_mac: "02:00:00:0D:62:E1"
      storage_mac: "02:00:00:0D:63:E1"
      migration_mac: "02:00:00:0D:64:E1"
      service1_mac: "02:00:00:0D:65:E1"
      service2_mac: "02:00:00:0D:66:E1"

    - name: harvester2
      template: harvester
      index: 2
      hostname: bravo
      uuid: "4e156918-3e00-44c2-bf47-0b532b3807c1"
      ip: "192.168.122.12"
      config_iso_name: "harvester-config-node2"
      ssh_user: rancher
      interfaces:
        - role: mgmt
          mac: "02:00:00:0D:62:E2"
        - role: storage
          mac: "02:00:00:0D:63:E2"
        - role: migration
          mac: "02:00:00:0D:64:E2"
        - role: service
          mac: "02:00:00:0D:65:E2"
        - role: service
          mac: "02:00:00:0D:66:E2"
      mgmt_mac: "02:00:00:0D:62:E2"
      storage_mac: "02:00:00:0D:63:E2"
      migration_mac: "02:00:00:0D:64:E2"
      service1_mac: "02:00:00:0D:65:E2"
      service2_mac: "02:00:00:0D:66:E2"
```

---

## Field reference

### `apiVersion`

Schema version. Increment this when you make breaking changes to the definition format so consumers can detect incompatible files.

### Top-level cluster fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | Usually matches `rodeo-plan.yaml name` |
| `description` | string | no | Human-readable label |
| `start_order` | list | yes | VM names in the order they should start |
| `harvester_node_names` | list | Harvester only | Subset of `start_order` that are Harvester members |
| `harvester_ready_count` | int | Harvester only | Must equal `len(harvester_node_names)` |
| `etcd_join_gap_seconds` | int | Harvester only | Seconds before each join node starts (default 90) |

### `network`

| Field | Notes |
|-------|-------|
| `name` | libvirt network name — `default` for the standard NAT network |
| `bridge` | Host bridge interface — `virbr0` for the default NAT network |
| `mode` | `nat` for nested KVM labs; `bridge` for direct host networking |
| `cidr` | The lab subnet. All VM IPs must be within this CIDR. |
| `gateway` | The host-side IP on `virbr0`. Usually `.1` of the CIDR. |
| `domain` | DNS domain. libvirt's dnsmasq serves `<hostname>.<domain>` for all nodes. |
| `dhcp_range.start/end` | Dynamic DHCP pool. Static leases for lab VMs are assigned outside this range. |

### `node_templates`

Templates define the NIC (interface) blueprint for a VM flavor. Individual nodes reference a template and list concrete MACs.

**Interface roles:**

| Role | Used for |
|------|----------|
| `mgmt` | PXE boot, static DHCP lease, SSH, Kubernetes API, etcd |
| `storage` | Longhorn CSI traffic between nodes |
| `migration` | KubeVirt live migration traffic |
| `service` | Kube-OVN uplinks (primary and secondary) |

**Interface fields:**

| Field | Notes |
|-------|-------|
| `model` | libvirt NIC model: `e1000` (PXE-capable) or `virtio` (faster, no PXE) |
| `pxe` | `true` on the mgmt NIC tells the renderer to add iPXE boot entries |
| `ip_family` | `static` means a DHCP static lease is created for this NIC's MAC |
| `count` | Generates multiple NICs with the same role (e.g. `service` × 2) |

**`infra_type`** on a template tells `rodeo stop` and `rodeo start` how to handle the VM:

| Value | Behaviour |
|-------|-----------|
| `harvester` | Graceful ACPI shutdown in reverse start order; waits for RKE2 to drain |
| `rancher` | Graceful ACPI shutdown after Harvester nodes |

### `exposed_services`

Each entry becomes a `firewalld port_forward` rule on the host's public zone.

| Field | Notes |
|-------|-------|
| `host_port` | Port on the host that external users connect to |
| `guest_port` | Port on the guest VM or VIP that the traffic is forwarded to |
| `target` | `vip` for the Harvester cluster VIP, or a node `name` from the `nodes` list |
| `proto` | `tcp` or `udp` |

### `nodes`

Each node is a concrete VM. Fields:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Unique VM name. Must appear in `start_order`. |
| `template` | yes | References a key in `node_templates` |
| `index` | yes | Integer index used in naming (config ISO name, etc.) |
| `hostname` | no | Short hostname (without domain). Generated from index if omitted. |
| `uuid` | no | libvirt domain UUID. Generated deterministically if omitted. |
| `ip` | yes | Static IP. Must be in `network.cidr`, outside `dhcp_range`. |
| `config_iso_name` | Harvester only | Name of the Harvester config ISO file (without extension) |
| `ssh_user` | no | OS user for `rodeo ssh`. Defaults to the template's `ssh_user`. |
| `interfaces` | yes | List of NIC declarations with `role` and `mac` |

**MACs:** must be unique per lab, start with `02:` (locally administered), and match the static DHCP leases. If you omit a MAC, the renderer generates one deterministically from the plan name and node name. Provide explicit values when you need exact, reproducible identities.

**Legacy flat MAC fields** (`mgmt_mac`, `storage_mac`, etc.) are kept alongside the `interfaces` list for Ansible role compatibility during the transition to full inventory-driven provisioning. They must stay in sync with the `interfaces` entries.

---

## What belongs here vs. rodeo-plan.yaml

| Put it in `definition.yaml` | Put it in `rodeo-plan.yaml` |
|-----------------------------|-----------------------------|
| Node names, IPs, MACs | VM resource sizing (RAM, CPU, disk) |
| Network CIDR, gateway, DNS domain | Credentials and secret references |
| Start order and etcd join gap | Deployment target (baremetal/instruqt) |
| Exposed services and port mapping | Software versions |
| Node templates (interface roles) | Storage device path |
| Host prep requirements | Jinja2 parameters |

The rule of thumb: the definition is **what**. The plan is **how big** and **where**.

---

## Generated vs. explicit values

The renderer (`rodeo/inventory.py`) can generate missing values deterministically:

| Field | How generated when omitted |
|-------|---------------------------|
| `mac` | Hash of `plan.name + node.name + role + index` — stable across runs |
| `uuid` | UUID v5 from the same hash |
| `hostname` | Nice labels: `alpha`, `bravo`, `charlie`, … or `<name>-N` |

Explicit values always win. Provide them when you need exact, stable identities — for example, when reprovisioning a node that other systems (DNS, monitoring) already know by its MAC.
