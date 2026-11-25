# rodeo-plan.yaml 參考

`rodeo-plan.yaml` 是 lab 的資源與部署設定。它回答的是：VM 有多大、存放在哪裡、帳密是什麼，以及這個 lab 跑在哪個部署目標上？

它**不**描述拓撲（節點、網路、服務）。拓撲放在 [`definition.yaml`](definition.md)。

這兩個檔案合起來構成一個 **profile**：也就是 `rodeo up`、`plan` 與 `deploy` 所讀取的完整 lab 描述。

---

## 完整註解範例 { #full-annotated-example }

```yaml
# 這個 plan 使用的流程。決定要執行哪些階段。
# suse-virt: Harvester HCI（含或不含 Rancher）
# rancher:   只有 K3s 上的 Rancher Prime（沒有 Harvester、沒有 PXE）
type: suse-virt

# 用作 libvirt 物件前綴、狀態檔名稱與 log 標籤。
# 如果在同一台主機上跑多個 lab，每台主機內必須唯一。
name: my-lab

# 這個 lab 跑在哪裡。控制 finalise 階段是否延後執行，
# 以及（aws 時）筆電是否先建立一台 EC2 主機再遠端部署。
# baremetal: 在這台主機上完整部署，包含主機重開機時 VM 自動啟動
# remote:    透過 SSH 部署到 remote_host: 中的 KVM 主機（各階段在那台主機上以 baremetal 執行）
# instruqt:  略過 finalise，直到你執行 `rodeo deploy --from finalise --finalise`
# aws:       筆電當控制端：建立 EC2，然後遠端執行 `rodeo up --target baremetal`
deployment_target: baremetal

# 虛擬機資源配置。
# 這些是每台 VM 的值。主機總 RAM = (harvester 節點數 × memory_mib) + rancher.memory_mib。
resources:
  harvester:
    memory_mib: 16384   # 每個 Harvester 節點的 RAM，單位 MiB（16 GiB）
    vcpu: 8             # 每個 Harvester 節點的 vCPU
    disk_gb: 320        # 每個 Harvester 節點的磁碟：不要低於約 250（見說明）

  rancher:
    memory_mib: 8192    # Rancher VM 的 RAM，單位 MiB（8 GiB）
    vcpu: 4             # Rancher 的 vCPU
    disk_gb: 60         # Rancher VM 的磁碟

# 帳密。以 ?? 開頭的值會在部署時解析：絕不在這裡寫死。
#
# ?? 的解析來源（依優先順序）：
#   ??key            → ~/.rodeo/secrets.yaml 中名為 "key" 的 key
#   ??env:VAR_NAME   → 環境變數 VAR_NAME
#   ??file:/path     → 檔案內容
#   ??cmd:command    → shell 指令的 stdout（去除前後空白）
#
# secret 只會在受信任的 lab 中解析。rodeo trust 會核准整個 lab
# 目錄（對其中每個檔案計算 sha256，但不含 rodeo 自己寫入的狀態，例如
# .rodeo-secrets.yaml、content/ 與 logs）；任何變更都會讓它重新變成
# 不受信任。rodeo 從內建 profile 建立到空目錄中的 lab
# 本來就受信任；clone、匯入、catalog 與手工建立的 lab 需要
# `rodeo trust`（自動化時用 `rodeo trust --yes`）。以 URL 抓取的
# content repository 依 URL 與 ref 信任，而不是依 commit。寫入類指令（deploy、up、clean、export……）會解析
# secret；讀取類指令（plan、status、logs……）永遠不會。-P 與 --paramfile
# 的值不能包含 ??、{{、{% 或換行，而且只能設定資料類的 key：
# resources、versions、network、name、type、deployment_target、instance、
# libvirt.disk_cache/disk_io、rancher_tls.email、elemental.registrations、
# lab_in_a_box.variant/iso_image、provider.region/instance_type/ttl_hours、
# story.language/id、content.facts（每個值都會檢查），以及下方
# parameters: 中的名稱（只餵給 template 的純量值）。URL、ref、
# checksum 與路徑類的 key，例如 plan 宣告的 versions.<x>_url 或
# versions.<x>_ref，不能透過 -P 設定：請在這個檔案裡修改。
# content.facts 只接受一般句子或 http(s) URL：它們只會填進
# 渲染出來的指南，絕不會進入 shell。provider.ttl_hours 必須在 (0, 168] 之間。在 sudo 底下環境變數
# 不會傳下去，所以在那裡能用的是 ??key 這種形式。
#
# rodeo up 與 rodeo init 會自動產生 ~/.rodeo/secrets.yaml。
# 只要有任何 ?? 占位符無法解析，部署就會立刻失敗。
credentials:
  harvester_os_password: "??harvester_os_password"     # Harvester 節點的作業系統登入（rancher 使用者）
  harvester_admin_password: "??harvester_admin_password" # Harvester 網頁介面的管理者密碼
  rancher_admin_password: "??rancher_admin_password"   # Rancher 網頁介面的管理者密碼
  harvester_token: "??harvester_token"                 # RKE2 叢集 join token（內部使用）

# plan 層級的網路設定。大部分網路設定都在 definition.yaml。
# 只有在你的主機使用非預設的 VIP 或 gateway 時才在這裡覆寫。
network:
  vip: 192.168.122.10     # Harvester 叢集 VIP（kube-vip 浮動 IP）

# 主機的儲存設定。
storage:
  device: ""                          # "" = 使用預設的單一磁碟路徑
                                      # "/dev/nvme1n1" = 專用第二顆磁碟
  image_dir: /var/lib/libvirt/images  # VM 磁碟與 ISO 的存放位置

# 軟體版本。固定這些值就能重現特定的 lab。
versions:
  harvester: "1.8.2"          # 要下載並安裝的 Harvester ISO 版本
  rancher: "2.14.5"           # Rancher Prime Helm chart 版本
  k3s: "v1.35.3+k3s1"         # Rancher VM 上的 K3s 版本
  cert_manager: "v1.20.1"     # cert-manager Helm chart 版本（Rancher 的相依套件）

# Jinja2 樣板：定義這個檔案中使用的變數。
# 部署時可以用 -P 或 --paramfile 覆寫這些值。
parameters:
  harvester_mem: 16384

# 然後這樣使用：
# resources:
#   harvester:
#     memory_mib: {{ harvester_mem }}
```

---

## 欄位參考 { #field-reference }

### `type`

| 值 | 流程 | 階段 |
|-------|----------|--------|
| `suse-virt` | Harvester HCI（含或不含 Rancher） | `kvm_host` → `vms` → `pxe_server` → `cluster` → `rancher` → `apply` → `finalise` → `custom_scripts` |
| `rancher`、`rancher-test` | K3s 上的 Rancher Prime + 它建立的 K3s/RKE2 叢集 | `kvm_host` → `vms` → `boot` → `rancher` → `downstream` → `apply` → `finalise` → `custom_scripts` |
| `suse-edge` | SUSE Edge 3.7（Rancher + Elemental + EIB + edge 節點） | `kvm_host` → `vms` → `boot` → `rancher` → `elemental` → `apply` → `finalise` → `custom_scripts` |
| `lab-in-a-box` | 外部外掛：由 lab-in-a-box 引擎建立 lab | 它自己的階段（請見該外掛的文件） |

**必填。**沒有預設值。

### `name`

字串。用作所有 libvirt 物件的前綴（`<name>-harvester1` 等）、狀態檔名稱（`~/.rodeo/state/<name>.yaml`）與 log 標籤。

在每台主機內必須唯一。部署後更改 `name` 會留下孤立的資源，請先執行 `rodeo clean`。

### `deployment_target`

| 值 | 行為 |
|-------|-----------|
| `baremetal` | 在 rodeo 執行的主機（這台機器）上完整部署。`finalise` 會啟用主機重開機時 VM 自動啟動。 |
| `instruqt` | 自動略過 `finalise`。在 Instruqt 快照之後再執行：`rodeo deploy --from finalise --finalise` |
| `aws` | 筆電當控制端：需要 `provider:` 區塊，建立或沿用一台 EC2 KVM 主機、等待 SSH 可用，然後在那台主機上遠端執行 `rodeo up`。用 `rodeo destroy --cloud --yes` 拆除。自備（BYO）：自己建立 instance，保留 `deployment_target: aws`（或設定 `storage.backend: nvme`），然後在那台機器上部署。 |
| `gcp` | 和 `aws` 相同，只是在 Google Cloud 上：建立或沿用一台啟用 nested virtualization 的 Compute Engine VM、等待 SSH 可用，然後在 VM 上遠端執行 `rodeo up`。用 `rodeo destroy --cloud --yes` 拆除。請見 [`provider`（gcp）](#provider-when-deployment_target-gcp)。 |

| `remote` | 控制端：部署到你已經擁有的 KVM 主機。需要 `remote_host:` 區塊，會透過 SSH 把 lab 的 plan 檔案送過去，並在主機上執行 `rodeo up --target baremetal`。不會建立任何資源，`rodeo destroy --cloud` 也不適用：主機是你的。 |

**必填。**省略時預設為 `baremetal`。外掛可以透過
`host_context.register_host_context()` 新增目標（請見架構文件的擴充點）：
註冊過的目標在這裡、在 `--target`，以及在
`rodeo up` 的提示中都會被接受。

在 **instruqt** 上，`rodeo up` / lab 初次建立（seed）時也會套用依主機調整的 `resources` 預設組合，
讓 guest vCPU 總和維持在 builder 的約 70% 左右（Harvester 通常為 6–8 vCPU / 20 GiB）。
重新部署時不會改寫既有的 plan；只有 seed 出來的 plan 會套用這些預設組合。
當 plan 仍然超出預算時，`rodeo doctor` / `rodeo deploy --check` 會發出警告（不會中止）。

### `remote_host`

`deployment_target: remote` 必填，其他目標一律忽略。主機是你的：
rodeo 既不建立也不銷毀它，只需要連得到它。

```yaml
deployment_target: remote
remote_host:
  host: kvm-01.lab.example        # 名稱或 IP（必填）
  ssh_user: root                  # 登入帳號；預設 root
  identity_file: ~/.ssh/id_ed25519  # 私鑰；預設為 rodeo 管理的金鑰
  lab_dir: /root/rodeo-lab        # lab 放在主機上的位置
```

```bash
rodeo up --yes --profile harvester --target remote
```

控制端會透過 SSH 送出 `rodeo-plan.yaml`、`definition.yaml` 與實際生效的 secret，
在主機上安裝或更新 rodeo（`--ref` 可固定它執行的版本），然後在那裡執行
`rodeo up --target baremetal`，並把輸出串流回來。主機需要：能以
*ssh_user* 搭配該金鑰 SSH 登入、該使用者有**免密碼 sudo**，以及一個能
執行 lab 的發行版（與本機 `baremetal` 部署的需求相同）。它的 log 留在主機上的
`~/.rodeo/logs/remote-up.log`。

私鑰本身永遠不會被送出：只有控制端會讀取它。在 builder 中，
儲存的 **KVM host (SSH)** 帳密會填入這個區塊，並把金鑰寫進 lab
目錄，檔名為 `.rodeo-remote-key`（權限 600）。

`--detach` 會在主機開始部署後立刻返回，印出一行 JSON（pid、log、
host）；之後 `rodeo status --cloud` 會回報那次執行以及它 log 的結尾。每個目標在主機上
都有自己的檔案（`remote-up.log`、`.pid`、`.exit`，與 aws 的檔案並列），所以部署
到 KVM 主機的那一次與部署到 EC2 主機的那一次，絕不會讀取或佔用彼此的狀態。在那裡已有一次部署執行中時，第二次
`rodeo up` 會以 75 結束，而不是跟它搶著執行。

在 **aws** 上，`apply_host_context()`（seed + deploy）會把 `resources.harvester.disk_gb`
提高到固定的**每個 Harvester 節點 500 GB**，並把 `resources.rancher.disk_gb` 提高到 **60 GB**
（絕不依節點數縮放：NVMe 裝置的其餘空間刻意保留不用，
以符合實際環境/Instruqt 的配置）。同時會設定 `storage.backend: nvme`，並把
最大的非 root NVMe 掛載到 `image_dir`。在非 metal
類型上預設會啟用 nested virt。

**選項 A：一個拓撲 profile，AWS 只是部署的地方。**使用基本 profile 加上
`--target aws`（沒有另外的 `*-aws` plan 樣板）：

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

Instance 大小來自 `rodeo/providers/instance_catalog.py`，以那個
**相同的** profile 名稱為 key。`harvester` 與 `harvester-2n` 的 **recommended** 是
**`m8id.8xlarge`**（32 vCPU / 128 GiB / 單一約 1.9 TiB 的 NVMe 裝置，不像
`i7i.8xlarge` 會把 NVMe 分散在兩個裝置上，而 rodeo 只會掛載
其中一個）。`suse-edge` 的 recommended 是 **`m8id.4xlarge`**。明確指定的
`provider.instance_type` 仍然優先。

在 instance 上的 plan 中保留 `deployment_target: aws`（host-context +
`destroy --cloud` 標記）。在 EC2 機器本身，各階段以 **baremetal** 執行。
絕對不要在 Fleet 的 `workshop.yaml` 中設定 `lab.target: aws`，那裡請用 `lab.target: baremetal`
（請見 [Fleet](../fleet.md)）。


### `provider`（`deployment_target: aws` 時） { #provider-when-deployment_target-aws }

格式與 Fleet 的 [`workshop.yaml` provider](../fleet.md#workshopyaml-provider-schema) 相同。
AWS 需要 `type`、`region`、`subnet_id`，以及 **`instance_type`** 或
**`instance_tier`** 其中之一。

**對 `rodeo up --target aws` 而言，這個區塊是選填的。**任何 profile 都能用
`rodeo up --profile <name> --target aws` 部署，完全不需要 `provider:`。缺少的
值會被補上並寫回 `rodeo-plan.yaml`，讓重新執行與
`rodeo destroy --cloud` 使用相同的值：

| 欄位 | 預設值 |
|-------|---------|
| `type` | `aws` |
| `region` | `$RODEO_AWS_REGION`，否則為 `eu-north-1`（不是 AWS CLI 設定的 region：帳號的防護規則可能讓那個 region 沒有對外網路） |
| `subnet_id` | 該 region 的預設 VPC 中，位於有提供該 instance type 的 AZ 內的第一個公開 default-for-AZ subnet |
| `instance_type` | profile 的 `recommended` 等級（見下方） |
| `ttl_hours` | `6`：自動銷毀計時（dead-man switch）（見下方） |

沒有預設 VPC 的 region 會失敗，並在訊息中列出需要設定的欄位。

**每台雲端主機都有 dead-man switch。**rodeo 建立的每個 instance
（單台主機、fleet 主機、portal VM）都會在啟動後 `provider.ttl_hours` 到期，
預設為 **6 小時**。到期時，guest 裡的 systemd timer 會將它關機，
而且因為 instance 建立時設定了 `InstanceInitiatedShutdownBehavior:
terminate`，AWS 會將它終止，而不是留下一個仍然要為磁碟計費的已停止 instance。
timer 以絕對的 UTC 時間執行，並設定
`Persistent=true`，所以一台停機超過到期時間的主機，一開機就會
再次關機。到期時間記錄在 instance 標籤 `rodeo-expires-at`、主機上的
`/etc/rodeo-expires-at`，以及 `rodeo up --target aws`
與 `rodeo fleet provision` 的輸出中。

這個計時無法關閉，唯一的例外是用 `rodeo fleet` 部署的 [kvm-hosts](../guide-kvm-hosts.md) lab（`ttl_hours: off`）。較長的 workshop 請在主機建立**之前**設定 `provider.ttl_hours`
（大於 0，最多 168）；對既有主機重新執行 `up` 或
`fleet provision` 會保留它原本的到期時間。

**除非你指定，否則 security group 會自動管理。**省略
`security_group_ids` 時，rodeo 會在 subnet 所在的 VPC 中建立（或沿用）名為 `rodeo-<name>` 的 SG，
只開放主機需要的連接埠：SSH (22)、
Harvester UI (8443)、Rancher NodePort (30002)，並把這三個都限定在
*這台機器目前的公開 IP*，會自動偵測。從
不同的 IP 重新執行（換了 wifi、切換 VPN）時，會就地更新規則，而不是
累積一堆過時的規則。`rodeo destroy --cloud --yes` 會在沒有其他
標記為該 workshop 的資源還在執行時刪除它（盡力而為：如果 instance
還沒完成分離，請重新執行 destroy：它絕不會被當成硬性
失敗）。明確設定 `security_group_ids` 就能改回手動管理的
SG：適合多位學員共用的 IP 範圍、bastion 拓撲，或是你公司資安政策已經管理的
SG。

**Instance 大小（單主機 v1）：**為 lab profile 從三個等級中選一個，或設定
明確的 type。兩者都沒設定時，`rodeo up --target aws` 會以互動方式詢問；
加上 `--yes` 時使用 **recommended**。

| 等級 | 意義 |
|------|---------|
| `budget` | 符合 profile 的最低需求（通常是 EBS-backed Nitro） |
| `recommended` | 該 profile 的建議大小（通常有本機 NVMe；請見 `instance_catalog`） |
| `performance` | Metal / 更大規格的備案 |

建立之前，rodeo 會檢查該 type 是否**在該 region 有提供**，並透過
`RunInstances` DryRun 探測容量。如果該 region 沒有容量，會失敗並提示你
改試其他 region 或等級：它不會在沒有提示的情況下自動降規。

```yaml
deployment_target: aws
provider:
  type: aws
  region: eu-central-1
  # 二選一：
  instance_tier: recommended          # budget | recommended | performance
  # 或明確指定：
  # instance_type: m8id.8xlarge
  # ttl_hours: 6                      # dead-man switch，預設 6，最多 168
  # 省略 ami → 使用最新的 suse-sles-16-0-v<date>-hvm-ssd-x86_64（SLES 16 PAYG）
  # ami: ami-…                        # 選填，固定版本
  # ami_name_filter: "suse-sles-16-0-v????????-hvm-ssd-x86_64"
  subnet_id: subnet-…
  # security_group_ids: [sg-…]        # 省略 → rodeo 建立／管理一個，
  #                                    # 限定在這台機器的公開 IP
  ssh_user: ec2-user                  # SLES 16 / Leap 的預設值
  # nested_virtualization: true       # 非 metal 預設開啟
  # volume_size_gib: 100              # root EBS；lab 磁碟使用 NVMe
  # ref: main                         # 要在主機上執行的 rodeo-cli git ref
  # install_url: https://…/install.sh # fork 或離線環境的 mirror
```

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

**主機執行的是哪一版 rodeo-cli（`--ref` / `provider.ref`）。**Instance
會用 GitHub 上的 `install.sh` 自行 bootstrap，你本機的工作目錄
永遠不會傳到那裡。預設情況下，bootstrap **只在沒有 `rodeo` 時**
執行，所以主機會停留在它第一次安裝時的程式碼上，就像
`clean --refresh` 在沒被要求時不會變更已固定版本主機的版本一樣。

傳入一個 ref 就能改變這個行為：

```bash
rodeo up --target aws --ref main         # 取得 bootstrap 之後推送的 commit
rodeo up --target aws --ref v0.15.0      # 固定在某個 release
rodeo up --target aws --ref feat/my-fix  # 在真實主機上測試某個 branch
```

指定 ref 會讓 bootstrap **每次都**執行，並把主機的
checkout 硬性重設到該 ref（`install.sh --ref`），這是讓剛推送的
commit 進到既有主機的唯一方法。安裝程式本身也從同一個
ref 抓取，所以 `install.sh` 與它安裝的程式碼不會不一致。明確指定的
`provider.install_url` 會原樣使用：用於 fork 或離線環境的 mirror，
而 ref 仍然會傳給它。`--ref` 優先於 `provider.ref`；無效的
ref 會在啟動任何 instance **之前**被拒絕，所以打錯字不會有任何代價。
`--ref` 只在筆電是 AWS 控制端時有效；其他情況下
會發出警告並忽略。

要在不編輯 plan 的情況下測試 fork，請在執行 `rodeo` 的地方
設定環境變數：

| 變數 | 作用 |
|---|---|
| `RODEO_REPO` | `install.sh` clone 與更新來源的 git URL；會傳給遠端的 bootstrap。GitHub URL 也會讓預設的安裝 URL 指向該 repository 的 `install.sh` |
| `RODEO_INSTALL_URL_TEMPLATE` | 含 `{ref}` 占位符的 install.sh URL，用於非 GitHub 的 mirror |

設定的 `install_url` 仍然優先於這兩者。Fleet 也有相同的機制，
請見 [`fleet deploy --ref` / `lab.ref`](../fleet.md#which-rodeo-cli-the-hosts-run)。

**AWS API 帳密**（boto3：絕不放在 plan 裡）：`~/.aws/credentials` /
`AWS_PROFILE`，**或** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
（+ 選填的 `AWS_SESSION_TOKEN`）。AWS CLI 是選用的。`rodeo up` 會在建立任何資源之前
檢查它們（`sts get-caller-identity`），缺少或過期時就會停止。

**AMI 的選擇比看起來更重要。**預設是 SLES 16
**pay-as-you-go**（由 SUSE 透過 Amazon 發布），它和其他選項
不能互換：

- **openSUSE Leap 16** 是 Marketplace 產品，它的上架條件不允許
  第 7 代 Intel instance type。nested virtualisation 偏偏就需要這些，
  所以 Leap 除了 `*.metal` 之外，無法在任何類型上跑 nested lab。
- **SLES BYOS** 映像允許 `i7i`，但不附訂閱，所以 zypper
  沒有 repo，`install-deps` 會失敗。rodeo 不會幫主機註冊。

PAYG 可以避開這兩個陷阱，代價是在 `i7i.8xlarge` 上每小時約 $0.125 的授權附加費。
不需要訂閱 Marketplace 或額外啟用。

**SSH / root：**新的 instance 會拿到 cloud-init UserData，為 **root** 安裝受管理的
公鑰，並為 `ssh_user`（SLES 16 上是 `ec2-user`）設定**免密碼 sudo**。
遠端的 `rodeo up` 以 `sudo -n` 執行，所以絕不會跳出提示。`rodeo ssh primary`
或 `rodeo ssh primary/rancher` 使用同一把受管理的金鑰。

### `provider`（`deployment_target: gcp` 時） { #provider-when-deployment_target-gcp }

**`rodeo up --target gcp` 可以不寫這個區塊。**用
`gcloud auth application-default login` 登入一次之後，任何 profile 都可以用
`rodeo up --profile <name> --target gcp` 部署。缺少的欄位會自動補上，並寫回
`rodeo-plan.yaml`：

| 欄位 | 預設值 |
|-------|---------|
| `type` | `gcp` |
| `project` | `$RODEO_GCP_PROJECT`／`$GOOGLE_CLOUD_PROJECT`，否則用 application default credentials 的 quota project，再否則用 `gcloud config get-value project` |
| `region` | `$RODEO_GCP_REGION`，否則 `europe-west4`（歐盟唯一提供所有等級機型的 region） |
| `zone` | 該 region 中第一個提供此機型的 zone；某個 zone 容量不足時改用下一個 |
| `instance_type` | profile 的 `recommended` 等級（見下表） |
| `image` | `suse-cloud` 中最新的 `sles-16-1-x86-64`（SLES 16.1 pay-as-you-go）。用 `image_family`／`image_project` 指定其他 family |
| `ssh_user` | `rodeo`（由 guest agent 依 `ssh-keys` metadata 中的 lab key 建立，具備免密碼 sudo） |
| `network`／`subnetwork` | 兩者都是 `rodeo-<region>`，第一次使用時建立（10.90.0.0/20）。要用自己的 VPC 就設定這兩個欄位 |
| `allowed_cidrs` | 這台機器的公開 IP（`/32`） |
| `boot_disk_gb` | `-lssd` 機型為 100；沒有 local SSD 的機型為 plan 中 guest 磁碟的總和加 60 |
| `service_account` | `default`（project 的 compute service account），只有 `userinfo.email` scope |
| `ttl_hours` | `6`：自動銷毀計時 |

rodeo 建立的資源全部都有 `managed-by=rodeo` 與 `rodeo-workshop=<lab>` label：

- **VM**，啟用 `enableNestedVirtualization`。GCE 上只有 Intel 系列能跑 nested KVM
  （C4、N4、N2、C3、Z3）；AMD、Arm 與 E2 機型會被拒絕。
- **一條防火牆規則** `rodeo-<lab>`，對 `allowed_cidrs` 開放 22、8443 與 30002 埠；
  你的 IP 改變時會重新調整範圍，最後一台 instance 刪除時一併刪除。
- **自動銷毀計時是 GCP 原生功能**：`scheduling.maxRunDuration` 搭配
  `instanceTerminationAction: DELETE`。時間到時 Google 會刪除 VM 與它的磁碟，
  即使你的筆電已經關機也一樣。
- **VM 上的 service account。**SLES pay-as-you-go 用 VM 的 identity token
  註冊 repo。沒有 service account 就沒有 token，zypper 也就沒有 repo。

**各等級的機型**（暫不提供 bare metal：最小規格就是 192 vCPU，每小時約 $9）：

| 等級 | 系列 | 範例 |
|------|--------|---------|
| `budget` | N4 + Hyperdisk Balanced（沒有 local SSD，儲存池放在開機磁碟上） | `n4-standard-16` |
| `recommended` | C4 `-lssd`（本機 NVMe） | `c4-standard-16-lssd`；Harvester 用 `c4-standard-32-lssd` |
| `performance` | 更大或高記憶體的 C4 `-lssd` | `c4-highmem-32-lssd` |

Local SSD 以 375 GiB 的 NVMe 磁碟提供，超過 8 vCPU 的機型會有好幾顆
（`c4-standard-32-lssd` 有 5 顆）。`kvm_host` 階段會把它們組成一個 RAID0 儲存池，
作為 `image_dir`。VM 停止後資料就會消失，和 AWS 的 instance store 一樣。

### `resources`

每台 VM 的大小。所有欄位都是每個節點的值（不是總和）。

| 欄位 | 單位 | 最小值 | 說明 |
|-------|------|---------|-------|
| `memory_mib` | MiB | Harvester 為 12288（12 GiB） | Harvester 至少需要 12 GiB；建議 16 GiB |
| `vcpu` | 執行緒 | 4 | 在啟用 hyper-threading 的主機上，每個執行緒都算一個 |
| `disk_gb` | GiB | **Harvester 為 320** | 不論磁碟多大，Elemental 的持久分割區都固定佔掉至少 150 GiB；低於約 250 時空間會不夠用，containerd 就會失敗；其餘空間給 Longhorn 自己的分割區，所以磁碟越大，Longhorn 的空間就越多 |

只有在拓撲包含 Rancher VM 時才會用到 `rancher` 區塊（請見 `definition.yaml`）。

### `credentials`

所有帳密值都必須使用 `??` 占位符。plan 檔案中的明文密碼會被 `validate_config()` 拒絕。

| Key | 用途 |
|-----|----------|
| `harvester_os_password` | Harvester 節點的作業系統登入（`rancher` 使用者的 SSH + console） |
| `harvester_admin_password` | Harvester 網頁介面中的 `admin` 使用者 |
| `rancher_admin_password` | Rancher 網頁介面中的 `admin` 使用者 |
| `harvester_token` | RKE2 叢集 join token：內部使用；不要分享給學員 |

`rodeo up` 與 `rodeo init` 會產生隨機值，並寫入 `~/.rodeo/secrets.yaml`（chmod 600）。絕對不要 commit 這個檔案。

### `network`

| 欄位 | 預設值 | 說明 |
|-------|---------|-------|
| `vip` | `192.168.122.10` | Harvester 叢集 VIP。必須與節點在同一個 `/24`，但在 DHCP 範圍之外。 |

大部分網路拓撲（CIDR、gateway、DNS 網域、每個節點的 IP）都在 `definition.yaml`。

### `storage`

| 欄位 | 預設值 | 說明 |
|-------|---------|-------|
| `device` | `""` | 單一磁碟的主機請留空。設為 `/dev/nvme1n1`（或類似的裝置）以專用第二顆磁碟。請用 `lsblk` 確認。 |
| `image_dir` | `/var/lib/libvirt/images` | libvirt 存放 VM 磁碟與 ISO 的位置。完整 lab 需要 600–900 GiB 的可用空間。 |

### `libvirt`

| 欄位 | 預設值 | 說明 |
|-------|---------|-------|
| `uri` | `qemu:///system` | libvirt 連線 URI：`qemu:///system`、`qemu:///session`，或以 `qemu+ssh`/`qemu+tls`/`qemu+tcp` 連到 `[user@]host[:port]`（可用加上中括號的 IPv6 位址）再加上 `/system` 或 `/session`。不能有 query string：`?keyfile=`、`?known_hosts=`、`?no_verify=` 之類的都會被拒絕（有些會執行指令）；SSH 選項請改放在 `~/.ssh/config`。 |
| `disk_cache` | 依目標而定 | guest qcow2 的 `cache=` 模式。baremetal 預設 `none`，`deployment_target: instruqt` 時預設 `writeback`（nested 雲端 I/O 較好）。可用值：`none`、`writethrough`、`writeback`、`unsafe`、`directsync`。 |
| `disk_io` | 依目標而定 | guest qcow2 的 `io=` 模式。baremetal 預設 `native`，Instruqt 上預設 `threads`。可用值：`native`、`threads`、`io_uring`。 |

不用編輯 plan，在部署時覆寫：

```bash
rodeo deploy -P libvirt.disk_cache=writeback -P libvirt.disk_io=threads
```

### `versions`

預設值依引擎類型而定：

| 欄位 | `suse-virt` | `rancher` / `rancher-test` | `suse-edge` | 說明 |
|-------|-------------|----------------------------|-------------|-------|
| `harvester` | `"1.8.2"` | | | Harvester ISO；沒有固定 sha512 的版本 rodeo 會拒絕（請設定 `versions.harvester_iso_checksum: sha512:<digest>`）。 |
| `rancher` | `"2.14.5"` | `"2.15.2"` | `"2.15.1"` | Rancher Prime Helm chart。 |
| `k3s` | `"v1.35.3+k3s1"` | `"v1.36.4+k3s1"` | `"v1.36.3+k3s1"` | Rancher VM 上的 K3s。 |
| `cert_manager` | `"v1.20.1"` | `"v1.21.2"` | `"v1.20.1"` | cert-manager Helm chart。 |
| `downstream_k3s` | | `"v1.36.4+k3s1"` | | Rancher 建立的 K3s 叢集的 Kubernetes 版本。 |
| `downstream_rke2` | | `"v1.36.4+rke2r1"` | | Rancher 建立的 RKE2 叢集的 Kubernetes 版本。 |
| `elemental_operator`, `elemental_operator_crds` | | | `"1.9.2"` | Elemental Operator chart。 |
| `elemental_ui_extension` | | | `"3.0.3"` | Rancher 中的 Elemental UI extension。 |
| `eib` | | | `"1.3.4"` | Edge Image Builder 容器。 |

`downstream_k3s` / `downstream_rke2` 必須是該 Rancher release 支援的版本（Rancher 2.15.2：最高到 v1.36）。

### `parameters`

選填的 Jinja2 樣板變數對應表。用來避免在檔案中重複相同的值。部署時可以用 `-P key=value` 或 `--paramfile overrides.yaml` 覆寫。

---

## CLI 覆寫 { #cli-overrides }

不用編輯檔案，你可以在部署時覆寫任何 plan 值：

```bash
# 單一值
rodeo deploy -P resources.harvester.memory_mib=20480

# 多個值
rodeo deploy -P resources.harvester.vcpu=10 -P versions.harvester=1.5.0

# 從檔案（深度合併到 plan 之上）
rodeo deploy --paramfile big-lab.yaml
```

**優先順序（由低到高）：**profile 預設值 → `rodeo-plan.yaml` → `--paramfile` → `-P`

---

## story：workshop 敘事、語言與變體 { #story-workshop-narrative-languages-and-variants }

`rodeo story render` 會從 lab 的 `story/` 目錄中帶有 rmstory 標記的 markdown，
產生這個 lab 給學員的講義：

```
<lab>/story/*.md       帶標記的 markdown 原始檔（以英文撰寫）
<lab>/story/stories/   story 變體索引，每個變體一個 <id>.yaml
<lab>/story/strings/   翻譯儲存區（rmstory 檔案系統後端）
```

選填的 `story:` 區塊設定預設值（CLI 旗標可覆寫）：

```yaml
story:
  language: es          # 目標語言（預設 en = 原文，不翻譯）
  id: villain-arc       # 要組合的 story 變體（預設：所有段落）
  engine: gemini        # 用來以機器補齊缺少翻譯的 rmstory 引擎
  engine_env:           # 引擎的帳密：?? secret 會被解析
    GEMINI_API_KEY: "??gemini_api_key"
```

部署資訊（fact）會以 Jinja 運算式代入渲染後的文字中，
請把它們寫在不變段落（invariant span）裡，翻譯才永遠不會動到它們：
`<span no>{{ rancher_url }}</span>`。可用的 fact：`name`、`type`、
`language`、`vip`、`harvester_url`、`rancher_ip`、`rancher_url`、
`rancher_nodeport`、`dns_domain`、`gateway`、`vms`、`vm_names`、`credentials`。

以原文語言渲染且不指定變體時，不需要安裝任何東西；
翻譯與變體組合會用到 `rmstory` 系統套件
（`sudo rodeo install-deps --story`：發行版套件，絕不用 PyPI）。

部署後的成功畫面也使用同一套機制：每個 profile 都附有一個
帶標記的 `success.md`，lab 可以用 `story/success.md` 覆寫它，而
`story.language` 會將它在地化。翻譯出問題時，一律退回
英文原文：成果畫面永遠不會失敗。

---

## content：lab 的操作說明，與基礎架構分開 { #content-the-labs-instructions-apart-from-its-infrastructure }

plan 可以指定它的操作說明放在哪裡，而不是把說明放在
profile 裡。這樣環境（`rodeo up`）與操作說明（`rodeo content
apply`）就能分開部署：更新操作說明永遠不會重新部署
lab。

```yaml
content:
  repo: https://github.com/<owner>/<repo>   # https、公開、不含帳密
  ref: main             # branch、tag 或 commit（預設：預設 branch）
  path: story           # repository 中的目錄（預設：根目錄）
  format: rmstory       # rmstory | instruqt | markdown
  facts:                # instruqt：[[ Instruqt-Var key="..." ]] 的值
    SMLM_URL: https://smlm.rodeo.lab
  replace:              # 渲染時逐字替換的文字
    "smlm.example.com": smlm.rodeo.lab
```

`rodeo content apply`（在 lab 目錄中執行）會把 content 抓到
`<lab>/content/`、在 `<lab>/content.lock` 記錄 commit，並把
指南渲染到 `<lab>/guide/`（只有擁有者可讀的檔案：fact 可能包含 lab 密碼）：

**語言 branch。**content repository 把原文語言放在
預設 branch，每個翻譯放在以該語言命名的 branch
（`es`、`pt-br`……）。沒有 `ref` 時，如果 plan 的 `story.language` 不是
原文語言（`en`），就會使用以該語言命名的 branch。如果
repository 沒有這樣的 branch，操作說明會取自預設 branch，並且
rodeo 會發出警告。無論是使用語言 branch，還是把 `ref:` 設成它，rodeo 也會在
該 branch 落後預設 branch 時發出警告：也就是預設 branch 有
語言 branch 尚未納入的 commit。`rmstory/`、
`rmstory-stories/`、`story/` 與 `.github/` 底下的變更不算。catalog rodeo
（`rodeo new <name> --from <repo>@<lang>`，或 catalog 的 `ref:`）在該 ref 是它 plan 的
`story.language` 時，也會收到同樣的警告。

`rodeo story check [DIR]` 會在 content repository 的 clone 上執行這項檢查。
它會為每個語言 branch 列出該 branch 缺少的 commit，並在
有 branch 落後時以 1 結束，所以可以當作 CI 的關卡（請 checkout 完整歷史，例如
`fetch-depth: 0`）。沒有 `--language` 時，它會檢查每個名稱像
兩個字母語言標籤的 branch。當原文 branch 不是 clone 的預設 branch 時，用 `--source` 指定它。

| format | 從 `<path>` 讀取 | 指南 |
|---|---|---|
| `rmstory` | `*.md`、`chapters.yaml`、`stories/`、`strings/` | `guide.md`，渲染方式與 `rodeo story render` 相同 |
| `instruqt` | `<challenge>/assignment.md` | 每個 challenge 一個 `<challenge>.md`，套用 `facts` 與 `replace` |
| `markdown` | `*.md` | 每個檔案一份，套用 `replace` |

在 repository 的根目錄（不論 `path` 指向哪裡），content 也可以帶有
profile 結構中的 `checks/` 與 `custom/scripts/`：

- `checks/` 會安裝到 `/opt/rodeo/<plan name>/checks/`，擁有者為 root，
  主機上每個使用者（包含學員的帳號）都可以讀取與執行，
  並取代先前的版本。安裝需要 root；沒有 root 時
  指南仍會渲染，指令也會說明如何重新執行。`rodeo clean`
  會移除它（`rodeo clean --all`：移除每個 plan 的）。
- `custom/scripts/` 放的是設定腳本：其中可執行的腳本會依排序順序
  在主機上、從該目錄執行，使用的環境與 profile 自己的
  `custom/scripts/` 相同（`KUBECONFIG`、`RODEO_PLAN_NAME`、`RODEO_CONFIG_DIR`、
  `RODEO_LAB_DIR`），另外加上 `RODEO_CONTENT_DIR`，也就是抓下來的 repository。它們在
  每次 apply 時都會執行，所以請寫成 可重複執行。每個腳本都會印出自己的輸出；某個腳本失敗
  不會讓其他腳本停止，但 `rodeo content apply` 最後會以 1 結束。

腳本以執行 rodeo 的使用者身分執行（在 `rodeo up` 底下是 root），內容取自
`ref` 指向的版本：repository 不是你的時，請固定在某個 tag 或 commit。

`rodeo up` 會在部署成功後、在 profile 自己的
`custom/scripts/` 之後套用它；那裡的失敗只會發出警告。如果 content 腳本與
profile 自己的 `custom/scripts/` 中某個腳本同名，就會在那裡被略過，因為 profile 的版本
已經執行過了。在 `deployment_target: instruqt` 上，
對於會略過 profile 自訂腳本的引擎（那些腳本應該放進預先建好的映像），`rodeo up` 也會略過這些設定腳本；
`rodeo content apply` 則會執行它們。`rodeo content show` 會印出這個區塊、最後一次抓取的 commit、
設定腳本，以及 checks 安裝在哪裡。當 lab 沒有自己的 `story/` 時，
`rodeo story render` 讀取的也是 rmstory content。
`workshop:` 區塊（lab-in-a-box lab 的 Instruqt track）與
profile 內的 `story/` 都照舊運作。

---

## lab_in_a_box：用 lab-in-a-box 部署，或匯出成 lab-in-a-box { #lab_in_a_box-deploying-with-or-exporting-to-lab-in-a-box }

[lab-in-a-box](https://github.com/SUSE-Technical-Marketing/lab-in-a-box) 會從
`lab.json` 建立 VM 並執行外掛（SUSE Multi-Linux Manager、Uyuni、NeuVector……）。rodeo 會從 plan 與 definition 渲染出這個檔案，
而 plan 與 definition 仍然是唯一的資料來源。用法有兩種：

- `type: lab-in-a-box` 透過它部署。rodeo 會準備主機（套件、
  libvirt 網路與 DNS、`exposed_services` 的防火牆轉送、基礎
  映像、secret），安裝 lab-in-a-box（除非
  `source.ref` 另有指定，否則安裝最新 release），然後執行它的 `setup_lab.py`。各階段為
  `kvm_host`、`labinabox_host`、`labinabox` 與 `custom_scripts`。
  `rodeo clean` 會執行它的 `destroy_lab.py`。
- `rodeo export --format lab-in-a-box` 只會寫出 `lab.json`，給你自己運作的
  lab-in-a-box automation node 使用。

`lab_in_a_box:` 區塊放的是只存在於 lab-in-a-box 端的設定：

```yaml
lab_in_a_box:
  source:                               # 僅 type: lab-in-a-box，要執行哪個 lab-in-a-box
    repo: https://github.com/SUSE-Technical-Marketing/lab-in-a-box
    ref: latest                         # latest（最高版本 tag，預設）、tag、branch 或 SHA
  parallel: 4                           # setup_lab.py --parallel=N
  root_password: "??universal_pwd"      # 每台 VM 的 root 密碼
  images:                               # 基礎映像：在 lab.json 中變成 SOURCE_IMAGE/SOURCE_SHA256；
                                        # lab-in-a-box 會下載並驗證到 ISO_LOC
    - name: ubuntu-24.04-server-cloudimg-amd64.img
      url: https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
      sha256_url: https://cloud-images.ubuntu.com/releases/24.04/release/SHA256SUMS   # 或 sha256: <hex>
  nodes:                                # 每個節點的 lab.json key，以節點短名稱為 key
    ubuntu2404lts: {SOURCE_IMAGE: ubuntu-24.04-server-cloudimg-amd64.img, ssh_pwauth: "true"}
    smlm: {SOURCE_IMAGE: smlm.qcow2, addons: [smlm]}
  iso_image: openSUSE-Leap-15.6.qcow2   # 沒有自己映像的節點所用的共同基礎映像
  config_method: cloud-init             # cloud-init（預設） | virt_customize | ""（ignition/combustion）
  cluster_name: mgmt                    # kcluster 名稱（也是它的 DNS 紀錄：<name>.<domain>）
  cluster_type: k3s                     # k3s（預設） | rke2
  clu_rel: stable                       # 安裝 channel，精確的版本固定不會帶過去
  addons: [rancher]                     # 覆寫推導出來的 kcluster install_<addon> 清單
  sections:                             # 原樣放入 lab.json 的區段（外掛設定）
    smlm: {smlm_deployment: podman, smlm_admin_pass: "??smlm_admin_password"}
```

**lab 本身：**`lab_in_a_box.lab` 也可以改為放一整個 lab-in-a-box lab
（`common`、以 FQDN 為 key 的 `nodes`、`kclusters`、每個外掛一個區段），也就是
lab-in-a-box lab-builder 編輯的格式。rodeo 會照原樣部署它，只設定
屬於主機的部分：common 的網路 key 與 `lab_name`、每個節點的
`BRIDGE`、`myip`、`mymac` 與 FQDN 網域、kcluster 的 `mydomain`、映像
（`SOURCE_IMAGE`）與 secret。lab 的節點就是 lab 的 VM：
`definition.yaml` 有列出的節點會保留該位址，其他節點則在 lab 網路中
使用其 `myip` 的主機部分（若該位址空著，否則使用下一個空位址）；lab 沒有列出的
definition 節點不會被部署。上面的 `nodes` 與 `sections`，以及變體中的同名設定，
都會合併進去。在外掛區段中以 `${node_fqdn:<vm>}` /
`${node_ip:<vm>}` 引用 VM。`smlm-workshop` 就是用這種方式帶著它的 lab。
`rodeo new <name> --from-lab-json <file>` 會從既有的 lab 寫出這樣的 plan
（請見[建立自己的 rodeo](../custom-rodeos.md)）。

```yaml
lab_in_a_box:
  lab:
    common: {config_method: cloud-init}
    nodes:
      smlm.rodeo.lab: {myip: "192.168.122.20", VM_MEM: "16384", VM_CPU: "8", VM_DSK: "300",
                       SOURCE_IMAGE: smlm.qcow2, addons: [smlm]}
    smlm: {smlm_deployment: podman, smlm_admin_pass: "??smlm_admin_password"}
```

**變體：**`lab_in_a_box.variants.<name>` 會疊加在這個區塊上，
`lab_in_a_box.variant` 則選擇其中一個（`-P lab_in_a_box.variant=<name>`）。在
疊加時，dict 會合併、`null` 會刪除一個 key，而 `images` 依名稱合併
（`remove: true` 會移除一個）。變體可以加入 `operator_secrets`，以及部署時
記錄的 `notice`。只會詢問被選中變體的 secret。
`smlm-workshop` 用這個機制在「從預先建好的伺服器開機」
（`image`）與「從標準 SLES 建置」（`scratch`）之間切換。

映像的 `url` 也可以是一份 mirror 清單，會依序嘗試，並且全部以
同一個 checksum 驗證。

**Secret：**每個帳密都是一個 `??key`。plan 引用了但 `~/.rodeo/secrets.yaml` 缺少的
一般 `??key`，`rodeo up` 會自動產生。列在頂層 `operator_secrets:`
底下的 key 是例外：那些是只有你才有的值，例如 SCC regcode 或預先建好映像的密碼，
`rodeo up` 會改為詢問你（在 `--yes` 底下它會停止，並說明要加入哪些）。
渲染出來的 `lab.json` 包含解析後的值，所以會以 `0600` 寫到
`<lab>/.labinabox/`。只要還有任何 `??` 值未解析，部署就會停止。

要在不編輯 plan 的情況下測試另一個 lab-in-a-box，請設定
`RODEO_LABINABOX_REPO` 和/或 `RODEO_LABINABOX_REF`；它們各自優先於
對應的 `source:` key。若要使用本機的 lab-in-a-box checkout，請設定
`RODEO_LABINABOX_PATH=/path/to/lab-in-a-box`；它優先於上述所有設定。
這些變數是在執行 `rodeo up` 的主機上讀取。當 lab-in-a-box 的來源改變時，
重新執行會重新安裝它：改用另一個 repo 或 ref，或是本機
checkout 有新的 commit 或未 commit 的修改。

**既有的 lab-in-a-box 主機：**預設情況下（`target.mode: auto`），rodeo 會把
lab-in-a-box 安裝在它執行的主機上。在以下情況，它會改用既有的 lab-in-a-box：該
主機已經設定好 lab-in-a-box（有一個不是 rodeo 寫的 `/etc/lab_creation.cfg`），
或 `target.host` 指定了一台遠端 automation node：

```yaml
lab_in_a_box:
  target:
    mode: auto            # auto | managed | existing
    host: automation.example.lab   # 遠端 lab-in-a-box 主機（rodeo 的金鑰要在它的 authorized_keys 中）
    ssh_user: root
    libvirt_uri: qemu+ssh://root@kvm1.example.lab/system   # 它的 hypervisor，用於 status/start/stop
```

使用既有的 lab-in-a-box 時，rodeo 會略過 `kvm_host`，不動那台主機的
安裝、網路、防火牆與金鑰。lab 必須配合那台主機自己的
網路：在 `definition.yaml` 中設定 `network.bridge`/`cidr`/`gateway`/`domain`，
其他設定則放在 `sections.common` 底下（例如 `mydns`）。

遠端主機會透過 SSH stdin 收到 `lab.json`，放在 `~/.rodeo-labs/<plan>/`（權限
0600，附有擁有者標記）。`setup_lab.py` 在那裡執行，輸出會串流
回來。`rodeo clean` 會在那裡執行 `destroy_lab.py`，但只針對帶有這個
plan 標記的 lab。`rodeo ssh <vm>` 會經由那台主機跳轉。

每次部署，不論哪種模式，都會先以已安裝的
lab-in-a-box 自己的 schema（`lab_schema`）檢查 `lab.json`，如果那個
lab-in-a-box 對這個 plan 來說太舊，就會停止並列出相關欄位。

**用雲端帳號中的 VM 取代 nested KVM：**使用 `lab_in_a_box.cloud` 時，
lab-in-a-box 會透過它自己的運算
後端，在雲端帳號中建立每個節點：aws、gcp、hetzner、alibaba、scaleway、upcloud、ovhcloud、exoscale。此時
rodeo 主機只負責執行 lab-in-a-box，所以不需要 KVM（筆電或一台小型
VM 就夠了）：

```yaml
lab_in_a_box:
  iso_image: ami-0123456789abcdef0      # provider 的映像（AMI、映像名稱/ID），依 region 而定
  cloud:
    cloudtype: aws
    account: aws-lab                    # 帳密檔名稱，每個 lab 一個（clean 會移除它）
    settings:                           # 後端的 key，與 lab-in-a-box 的 README 相同
      AWS_REGION: eu-north-1
      AWS_ACCESS_KEY_ID: "??aws_access_key_id"
      AWS_SECRET_ACCESS_KEY: "??aws_secret_access_key"
  nodes:
    vm1: {cloud_instance_type: t3.large}
```

使用 AWS SSO 登入時，請用 profile 取代金鑰：`AWS_PROFILE: <profile>`
（執行 rodeo 的那個使用者的 profile；profile 優先於金鑰）。臨時金鑰
還需要 `AWS_SESSION_TOKEN`。

`cloudtype: aws` 時，lab-in-a-box 會呼叫 `aws` CLI：`sudo rodeo install-deps --aws`
會從 AWS 安裝 AWS CLI v2（會先以 AWS 的金鑰檢查安裝程式的 PGP 簽章），
CLI 不存在時 `rodeo up` 會主動提議幫你安裝。接著 preflight
會向 AWS 查詢 plan 的帳密屬於誰，在沒有帳密、帳密過期或被拒絕時停止部署
（例如「run `aws sso login --profile <profile>`」）。

rodeo 會把帳號寫到 `/etc/lab_creation/credentials/<account>.yaml`（0600）。
`settings` 中的任何 `??key` 一律會詢問，絕不自動產生。節點不會有靜態
IP/MAC：由 provider 指派。rodeo 會在部署後讀回這些位址，
所以 `rodeo ssh <vm>` 與成功畫面會使用它們。

省略 `settings`，就會使用 lab-in-a-box 主機上已經存在的帳號，
例如用 `setup_credentials.py` 建立的加密帳號。

`rodeo status`/`start`/`stop`/`restart` 在每個 provider 上都會詢問 lab-in-a-box 的 `vm_power.py`。

`exposed_services` 會變成每台目標 VM 的 `open_ports`。lab-in-a-box 會用
provider 自己的機制開放它們：

| Provider | 連接埠如何開放 |
|---|---|
| aws | security group 規則（需要 `AWS_SECURITY_GROUP_ID`） |
| gcp | 一條防火牆規則，加上 VM 上的網路標籤 |
| alibaba | `ALIBABA_SECURITY_GROUP_ID` 上的規則 |
| exoscale | 每台 VM 一個 security group |
| hetzner, scaleway, upcloud, ovhcloud | 不需要開放：它們預設允許入站流量，除非你自己加了防火牆 |

**同一台主機上的多個 instance：**頂層的 `instance: N`（1–99；0 代表照
plan 原樣）會把一個 lab-in-a-box lab 移開，避免與其他副本衝突：

| | instance 0 | instance N |
|---|---|---|
| libvirt 網路 / bridge | `default` / `virbr0` | `rodeo-iN` / `rbrN` |
| 子網路、節點 IP | 照定義 | 第三個八位元組 + N |
| DNS 網域 | 照定義 | `iN.<domain>` |
| MAC | 照定義 | 第三個位元組 = N |
| 對外的主機連接埠 | 照定義 | + N × `instance_port_stride`（1000） |

產生的密碼會寫到 lab 自己的 `.rodeo-secrets.yaml`，所以每個 instance
都有自己的密碼；操作者的 secret 則保留在 `~/.rodeo/secrets.yaml`。
`rodeo instances new <profile> --count N` 會建立編號的 lab，`rodeo instances
up` 會逐一部署它們，`rodeo instances list` 會列出它們（以及這台主機剩餘的 RAM 與磁碟
還放得下幾個），`rodeo instances clean` 則會
移除它們。

在外掛區段中，`${node_fqdn:<vm>}` 與 `${node_ip:<vm>}` 代表該 lab
自己的節點。`lab_in_a_box.dns_aliases: {<vm>: [name, ...]}` 會在該 instance 的網路中
為節點加上它會回應的名稱，例如預先建好的伺服器映像安裝時使用的固定名稱。

**Workshop track：**頂層的 `workshop:` 區塊會在部署時把 Instruqt track 抓到
`<lab>/workshop`。接著它會針對沒有對應
lab VM 的 track 機器，以及沒有值的 assignment 變數發出警告：

```yaml
workshop:
  repo: https://github.com/SUSE-Technical-Marketing/instruqt-SMLM
  branch: main
  track: smlms
  skip_vms: [zbastion]                  # lab 刻意取代掉的機器
  facts: {SMLM_USERNAME: myadmin}       # [[ Instruqt-Var ]] 的值，會顯示在成功畫面上
```

`rodeo export` 不會帶過去的項目（匯出時會警告）：以 PXE 開機的
Harvester 節點（lab-in-a-box 沒有 PXE：用 `--skip-unsupported` 匯出
其餘部分）、對外服務的主機 port forward、storage/image-dir 的選擇，以及
精確的 k3s/rke2 版本固定。

---

## 哪些放這裡、哪些放 definition.yaml { #what-belongs-here-vs-definitionyaml }

| 放在 `rodeo-plan.yaml` | 放在 `definition.yaml` |
|-----------------------------|----------------------------|
| VM 資源大小（RAM、CPU、磁碟） | 節點名稱、IP、MAC |
| 帳密與 secret 引用 | 網路 CIDR、gateway、DNS 網域 |
| 部署目標（baremetal/instruqt/aws） | 啟動順序與 etcd join 間隔 |
| 軟體版本 | 對外服務與連接埠對應 |
| 儲存裝置路徑 | 節點 template（介面角色） |
| Jinja2 參數 | 主機準備需求（sysctl、SELinux） |

經驗法則：plan 是**多大**與**在哪裡**。definition 是**什麼**。
