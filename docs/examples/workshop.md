# Example workshop inventory

Copy to `workshop.yaml` and edit host SSH targets. Full reference: [Fleet](../fleet.md).

```yaml
name: suse-virt-rodeo-emea
lab:
  dir: /root/suse-virt-workshop
  source: git:https://github.com/avaleror/suse-virt-workshop.git
  target: baremetal
  concurrency: 4
  ports:
    harvester: 8443
    rancher: 30002
defaults:
  ssh_user: root
hosts:
  - id: student-01
    ssh: 203.0.113.11
    public_ip: 203.0.113.11
    labels: { room: a }
  - id: student-02
    ssh: 203.0.113.12
    public_ip: 203.0.113.12
    labels: { room: a }
```

```bash
rodeo fleet doctor -f workshop.yaml
rodeo fleet deploy -f workshop.yaml -j 4
rodeo fleet status -f workshop.yaml
rodeo fleet diagnose -f workshop.yaml   # pull logs if a host fails
rodeo fleet retry -f workshop.yaml --failed-only
rodeo fleet access -f workshop.yaml
```

### AWS host-acquire (F4a MVP)

```yaml
# workshop.yaml: hosts: [] until provision fills them
name: demo
lab:
  dir: /root/lab
  profile: harvester
defaults:
  ssh_user: ec2-user              # SLES 16 PAYG default AMI
provider:
  type: aws
  count: 2
  region: eu-central-1
  instance_type: m8id.8xlarge     # catalog 'recommended' for harvester
  # ttl_hours: 10                 # dead-man switch, default 6
  # ami omitted → newest SLES 16 PAYG
  subnet_id: subnet-…
  # security_group_ids: [sg-…]    # omit → rodeo manages one scoped to your IP
hosts: []
```

```bash
# rodeo installed on your laptop with install.sh (see ../install.md)
# Creds: aws login, ~/.aws/credentials, or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
# SSH key: auto ~/.rodeo/ssh/id_ed25519 → EC2 key pair "rodeo"
rodeo fleet provision -f workshop.yaml
rodeo fleet deploy -f workshop.yaml     # installs rodeo on each host, starts the lab
rodeo fleet doctor -f workshop.yaml     # needs rodeo on the host, so after deploy
rodeo ssh student-01
rodeo ssh student-01/rancher
rodeo fleet deprovision -f workshop.yaml --yes
```

See [Fleet F4](../fleet.md#f4-host-acquire).

### Single-host AWS (`rodeo up --target aws`)

The `provider:` block is optional here: with none, rodeo picks `eu-north-1` (or `$RODEO_AWS_REGION`), the default VPC subnet and the recommended instance. To set it yourself, it has the same shape:

```yaml
deployment_target: aws
provider:
  type: aws
  region: eu-central-1
  instance_tier: recommended   # harvester → m8id.8xlarge (see instance_catalog)
  # instance_type: m8id.8xlarge  # or pin explicitly
  # SLES 16 PAYG by default; pin with ami: ami-… if needed
  # ttl_hours: 6                 # dead-man switch, default 6
  subnet_id: subnet-…
  # security_group_ids: [sg-…]   # omit → rodeo manages one scoped to your IP
  ssh_user: ec2-user
  volume_size_gib: 100            # root EBS; lab disks on NVMe via host_context
```

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
rodeo ssh primary
rodeo ssh primary/rancher
# tear down the EC2 host (not nested VMs alone):
rodeo destroy --cloud --yes
```

AWS is `--target aws` on the base topology (no separate `*-aws` profile). In Fleet
`workshop.yaml`, never set `lab.target: aws`: use `lab.target: baremetal` on
provisioned hosts.
