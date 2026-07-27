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
# workshop.yaml — hosts: [] until provision fills them
name: demo
lab:
  dir: /root/lab
  profile: harvester
defaults:
  ssh_user: ec2-user
  identity_file: ~/.ssh/rodeo-workshop.pem
provider:
  type: aws
  count: 2
  region: eu-central-1
  instance_type: m7i.metal-24xl   # or nested-virt Nitro + nested_virtualization: true
  ami: ami-…
  key_name: rodeo-workshop
  subnet_id: subnet-…
  security_group_ids: [sg-…]
hosts: []
```

```bash
pip install 'rodeo-cli[aws]'
rodeo fleet provision -f workshop.yaml
rodeo fleet doctor -f workshop.yaml
rodeo fleet deprovision -f workshop.yaml --yes
```

See [Fleet F4](../fleet.md#f4-host-acquire).

### Single-host AWS (`rodeo up --target aws`)

Same `provider:` shape in `rodeo-plan.yaml`:

```yaml
deployment_target: aws
provider:
  type: aws
  region: eu-central-1
  instance_type: m7i.metal-24xl
  ami: ami-…
  key_name: rodeo-workshop
  subnet_id: subnet-…
  security_group_ids: [sg-…]
  identity_file: ~/.ssh/rodeo-workshop.pem
  ssh_user: ec2-user
  volume_size_gib: 500
```

```bash
pip install 'rodeo-cli[aws]'
rodeo up --yes --profile harvester --target aws
# tear down the EC2 host (not nested VMs alone):
rodeo destroy --cloud --yes
```
