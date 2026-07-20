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
rodeo fleet diagnose -f workshop.yaml
rodeo fleet access -f workshop.yaml
```
