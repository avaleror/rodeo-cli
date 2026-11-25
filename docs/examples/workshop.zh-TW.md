# workshop 清單範例

複製成 `workshop.yaml`，再編輯主機的 SSH 目標。完整參考：[Fleet](../fleet.md)。

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
rodeo fleet diagnose -f workshop.yaml   # 主機失敗時拉取日誌
rodeo fleet retry -f workshop.yaml --failed-only
rodeo fleet access -f workshop.yaml
```

### AWS 主機取得（F4a MVP） { #aws-host-acquire-f4a-mvp }

```yaml
# workshop.yaml：在 provision 填入之前 hosts 都是 []
name: demo
lab:
  dir: /root/lab
  profile: harvester
defaults:
  ssh_user: ec2-user              # SLES 16 PAYG 預設 AMI
provider:
  type: aws
  count: 2
  region: eu-central-1
  instance_type: m8id.8xlarge     # 目錄中 harvester 的「recommended」規格
  # ttl_hours: 10                 # 自動銷毀計時（dead-man switch），預設 6
  # 省略 ami → 最新的 SLES 16 PAYG
  subnet_id: subnet-…
  # security_group_ids: [sg-…]    # 省略 → rodeo 管理一個只限你 IP 的 security group
hosts: []
```

```bash
# 用 install.sh 在你的筆電上安裝 rodeo（請見 ../install.md）
# 帳密：aws login、~/.aws/credentials，或 AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
# SSH 金鑰：每個 workshop 在 ~/.rodeo/ssh/labs/<workshop>/ 產生一把新的，deprovision 時刪除
rodeo fleet provision -f workshop.yaml
rodeo fleet deploy -f workshop.yaml     # 在每台主機上安裝 rodeo，並啟動 lab
rodeo fleet doctor -f workshop.yaml     # 主機上要有 rodeo，所以在 deploy 之後執行
rodeo ssh student-01
rodeo ssh student-01/rancher
rodeo fleet deprovision -f workshop.yaml --yes
```

請見 [Fleet F4](../fleet.md#f4-host-acquire)。

### 單一主機的 AWS（`rodeo up --target aws`） { #single-host-aws-rodeo-up-target-aws }

這裡的 `provider:` 區塊是選用的：沒有的話，rodeo 會挑 `eu-north-1`（或 `$RODEO_AWS_REGION`）、預設 VPC 的 subnet 與建議的執行個體。如果要自己設定，格式相同：

```yaml
deployment_target: aws
provider:
  type: aws
  region: eu-central-1
  instance_tier: recommended   # harvester → m8id.8xlarge（請見 instance_catalog）
  # instance_type: m8id.8xlarge  # 或明確指定
  # 預設為 SLES 16 PAYG；需要時用 ami: ami-… 指定
  # ttl_hours: 6                 # 自動銷毀計時（dead-man switch），預設 6
  subnet_id: subnet-…
  # security_group_ids: [sg-…]   # 省略 → rodeo 管理一個只限你 IP 的 security group
  ssh_user: ec2-user
  volume_size_gib: 100            # 根 EBS；lab 磁碟透過 host_context 放在 NVMe 上
```

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
rodeo ssh primary
rodeo ssh primary/rancher
# 拆除 EC2 主機（不只是 nested VM）：
rodeo destroy --cloud --yes
```

AWS 是在基礎拓撲上加 `--target aws`（沒有另外的 `*-aws` profile）。在 Fleet 的
`workshop.yaml` 裡，絕對不要設定 `lab.target: aws`：在建立好的主機上請使用
`lab.target: baremetal`。
