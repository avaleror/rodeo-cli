# Plain KVM hosts (kvm-hosts)

`kvm-hosts` gives you KVM hosts and nothing else. rodeo installs KVM and libvirt, a NAT network (`default`, 192.168.122.0/24) and the firewall, then stops. The VMs are yours: a VM manager you are building or testing, scripts, or `virsh` by hand.

With `rodeo fleet` you get several of them that can talk to each other, which is a small KVM cluster on AWS.

## A 3-host cluster for a week

```bash
rodeo fleet up --profile kvm-hosts --count 3 --no-deadman
# ... use the hosts for as long as the work takes ...
rodeo fleet destroy -f workshop.yaml --yes
```

`fleet up` writes `workshop.yaml`, creates 3 `m7i.2xlarge` hosts (8 vCPU / 32 GiB, room for 3 or 4 small test VMs each), prepares them and prints their SSH access. Print it again any time with `rodeo fleet access -f workshop.yaml`.

`fleet destroy` (the same command as `fleet deprovision`) terminates the hosts, every VM on them goes with them, and the security group is removed.

## What is different from a workshop

| | Workshop profiles | kvm-hosts |
|---|---|---|
| Student portal | on by default | off |
| Access | `student_access: open` | operator only |
| Traffic between hosts | blocked | allowed (`provider.host_to_host: true`) |
| Dead-man switch | always on, at most 168 h | on by default, `--no-deadman` turns it off |

`provider.host_to_host: true` adds one security group rule that lets members of the workshop's own security group reach each other on any port. Nothing outside the group matches it.

## Running without a dead-man switch

`--no-deadman` (or `provider.ttl_hours: off` in `workshop.yaml`) is accepted only for `kvm-hosts` labs deployed with `rodeo fleet`. Every other lab, and `rodeo up --target aws`, refuses it.

Without the switch:

- the hosts are tagged `rodeo-expires-at: never` and `fleet provision` prints a warning,
- they bill until you run `rodeo fleet destroy`,
- a `poweroff` from inside a host only stops it, so the lab survives it. Stopping hosts overnight from the AWS console saves the compute cost, the disks still bill.

If you only need a few days, keep the switch and lengthen it instead: `--ttl-hours 168` gives a week and still cleans up after a forgotten lab.

## Your own steps

Start a profile of your own from this one with `rodeo new mylab --from kvm-hosts` and put numbered scripts in its `custom/scripts/` to install your software on every host. They run after the host is ready, on every `rodeo up`, so write them idempotent. For a fleet, push that profile to a git repo and point `lab.source` in `workshop.yaml` at it (see [Fleet](fleet.md)).
