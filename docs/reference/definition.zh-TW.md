# definition.yaml 參考

`definition.yaml` 是 lab 的拓撲宣告。它回答的是：有哪些 VM、它們的名稱與 IP 是什麼、依什麼順序啟動、網路怎麼接，以及主機上對外開放哪些服務？

它**不**包含資源大小、帳密或部署目標。這些放在 [`rodeo-plan.yaml`](plan.md)。

---

## 結構總覽 { #structure-overview }

```
definition:
  name:                    # lab 名稱（通常與 plan 名稱相同）
  description:             # 給人看的標籤
  start_order:             # 要啟動的 VM 名稱，依順序排列
  harvester_node_names:    # 其中哪些是 Harvester 叢集成員
  harvester_ready_count:   # 叢集階段完成前，需要有幾個節點達到 Ready
  etcd_join_gap_seconds:   # 每個非第一個 Harvester 節點啟動前的等待時間

  network:                 # libvirt 網路設定
  node_templates:          # 每種 VM flavor 的 NIC 藍圖
  exposed_services:        # 主機 port forward 到 guest 服務
  boot:                    # PXE/iPXE 開機伺服器設定（僅 Harvester）
  storage:                 # lab 儲存配置
  components:              # 高層次的組成說明
  harvester:               # Harvester 專用的叢集設定
  rancher:                 # Rancher VM 的 cloud-init + 要同步的 UI extension
  host_prep:               # 主機作業系統需求（sysctl、SELinux、libvirt）
  nodes:                   # 具體的 VM 宣告
```

---

## 完整註解範例：兩節點 Harvester（不含 Rancher） { #full-annotated-example-2-node-harvester-no-rancher }

```yaml
apiVersion: 1.0   # schema 有不相容變更時遞增

definition:
  name: suse-virt
  description: "2-node Harvester HCI cluster on nested KVM"

  # 叢集階段要啟動的 VM，依順序排列。
  # rodeo 依這個順序啟動。harvester1 永遠第一個（bootstrap 節點）。
  start_order:
    - harvester1
    - harvester2

  # start_order 中哪些 VM 是 Harvester 叢集成員。
  # 影響：VIP 等待、etcd 間隔套用、節點 Ready 計數、ISO 退出。
  harvester_node_names:
    - harvester1
    - harvester2

  # 叢集階段完成前，需要有幾個節點達到 Ready。
  # 要讓叢集健康，必須等於 len(harvester_node_names)。
  harvester_ready_count: 2

  # 每個 Harvester join 節點（不含第一個）啟動前要等待的秒數。
  # 避免多個節點同時加入時發生 etcd join 競爭。
  # 沒有實機測試前不要調到 60s 以下：90s 已證實安全。
  etcd_join_gap_seconds: 90

  network:
    name: default          # libvirt 網路名稱
    bridge: virbr0         # 主機上的 bridge 介面
    mode: nat              # nat | bridge（nested KVM 標準用 nat）
    cidr: 192.168.122.0/24
    gateway: 192.168.122.1
    domain: rodeo.lab   # 所有 lab VM 的 DNS 網域
    dhcp_range:
      start: 192.168.122.100
      end: 192.168.122.254  # 靜態租約（.9–.13，.10 是 VIP）在這個範圍之外

  # NIC 藍圖。每種 flavor 定義該類型 VM 有哪些邏輯網路線。
  # renderer（inventory.py）用它們產生 libvirt XML、DHCP 租約、
  # iPXE 腳本與防火牆規則。
  node_templates:
    harvester:
      flavor: harvester
      ssh_user: rancher
      infra_type: harvester    # 告訴 stop/start 這是叢集化的 Harvester 節點
      interfaces:
        - role: mgmt           # 管理 NIC：PXE 開機、VIP、Kubernetes API、etcd
          model: e1000
          pxe: true            # 這張 NIC 會拿到 iPXE 開機項目
          ip_family: static    # 依節點的 ip 欄位指派 DHCP 靜態租約
        - role: storage        # Longhorn CSI 流量
          model: virtio
        - role: migration      # KubeVirt live migration
          model: virtio
        - role: service        # Kube-OVN 主要 uplink
          model: virtio
          count: 2             # 產生 service1 + service2 兩張 NIC

    rancher:
      flavor: rancher
      ssh_user: root
      infra_type: rancher      # 告訴 stop/start 這是 K3s/Rancher 節點
      interfaces:
        - role: mgmt
          model: virtio
          ip_family: static

  # 可從主機外部連到的服務。每一筆會在 public zone 上變成一條 firewalld
  # port_forward（DNAT）規則。
  exposed_services:
    - name: harvester-ui
      description: "Harvester dashboard and API"
      host_port: 8443
      guest_port: 443
      target: vip       # "vip" → Harvester VIP；或像 "rancher" 這樣的節點名稱
      proto: tcp

    # 完整 lab（含 Rancher）還會加上：
    # - name: rancher-ui
    #   host_port: 30002
    #   guest_port: 30002
    #   target: rancher
    #   proto: tcp

  # iPXE 開機伺服器設定（僅 suse-virt profile）。
  # 驅動 pxe_server Ansible role。
  boot:
    pxe:
      http_port: 8080
      http_bind: "{{ libvirt_network_gateway }}"   # 綁定在 virbr0 的 IP 上
      tftp_root: /var/lib/libvirt/dnsmasq
      http_root: /srv/harvester-pxe
      ipxe_efi_url: "https://boot.ipxe.org/x86_64-efi/ipxe.efi"

  storage:
    device: "/dev/nvme1n1"             # 單一磁碟的主機請留 ""
    mount_point: /var/lib/libvirt/images
    fs_type: xfs
    libvirt_pool_name: default
    libvirt_pool_path: /var/lib/libvirt/images
    image_dir: /var/lib/libvirt/images

  # 高層次的組成說明。用於自我檢視（"rodeo describe"）與文件。
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

  # Harvester 專用的叢集設定，會嵌入每個節點的 config ISO。
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

  # 主機作業系統需求。由 kvm_host Ansible role 套用。
  # 除非你清楚自己在做什麼，否則不要改：這些值已在 SLES 16 上測試過。
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

  # 具體的 VM 宣告。每個節點引用一個 template，再加上實例資料。
  nodes:
    - name: harvester1
      template: harvester      # 引用上面的 node_templates.harvester
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
      # 舊版扁平欄位：在 Phase C 過渡期間為了相容 Ansible role 而保留
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

## 欄位參考 { #field-reference }

### `apiVersion`

Schema 版本。當你對 definition 格式做了不相容的變更時要遞增這個值，讓讀取端能偵測出不相容的檔案。

### 頂層叢集欄位 { #top-level-cluster-fields }

| 欄位 | 型別 | 必填 | 說明 |
|-------|------|----------|-------|
| `name` | string | 是 | 通常與 `rodeo-plan.yaml name` 相同 |
| `description` | string | 否 | 給人看的標籤 |
| `start_order` | list | 是 | VM 名稱，依應啟動的順序排列 |
| `harvester_node_names` | list | 僅 Harvester | `start_order` 中屬於 Harvester 成員的子集 |
| `harvester_ready_count` | int | 僅 Harvester | 必須等於 `len(harvester_node_names)` |
| `etcd_join_gap_seconds` | int | 僅 Harvester | 每個 join 節點啟動前的秒數（預設 90） |

### `network`

| 欄位 | 說明 |
|-------|-------|
| `name` | libvirt 網路名稱：標準 NAT 網路為 `default` |
| `bridge` | 主機的 bridge 介面：預設 NAT 網路為 `virbr0` |
| `mode` | nested KVM lab 用 `nat`；直接使用主機網路用 `bridge` |
| `cidr` | lab 子網路。所有 VM IP 都必須在這個 CIDR 內。 |
| `gateway` | `virbr0` 上主機端的 IP。通常是 CIDR 的 `.1`。 |
| `domain` | DNS 網域。libvirt 的 dnsmasq 會為所有節點提供 `<hostname>.<domain>` 解析。 |
| `dhcp_range.start/end` | 動態 DHCP 位址池。lab VM 的靜態租約會指派在這個範圍之外。 |

### `node_templates`

Template 定義某個 VM flavor 的 NIC（網路介面）藍圖。個別節點引用一個 template，並列出具體的 MAC。

**介面角色：**

| 角色 | 用途 |
|------|----------|
| `mgmt` | PXE 開機、DHCP 靜態租約、SSH、Kubernetes API、etcd |
| `storage` | 節點之間的 Longhorn CSI 流量 |
| `migration` | KubeVirt live migration 流量 |
| `service` | Kube-OVN uplink（主要與次要） |

**介面欄位：**

| 欄位 | 說明 |
|-------|-------|
| `model` | libvirt NIC 型號：`e1000`（支援 PXE）或 `virtio`（較快，不支援 PXE） |
| `pxe` | 在 mgmt NIC 上設 `true`，會讓 renderer 加入 iPXE 開機項目 |
| `ip_family` | `static` 表示會為這張 NIC 的 MAC 建立 DHCP 靜態租約 |
| `count` | 產生多張相同角色的 NIC（例如 `service` × 2） |

template 上的 **`infra_type`** 告訴 `rodeo stop` 與 `rodeo start` 要如何處理這台 VM：

| 值 | 行為 |
|-------|-----------|
| `harvester` | 依啟動順序的反向進行 ACPI 正常關機；等待 RKE2 drain 完成 |
| `rancher` | 在 Harvester 節點之後進行 ACPI 正常關機 |
| `k3s`、`rke2` | 下游叢集節點（`rancher` profile），跟其他 VM 一樣停止 |

**Flavor**（template 上的 `flavor`）決定 VM 的建置方式：`harvester`（iPXE 安裝）、`rancher` 與 `eib`（cloud image + cloud-init）、`edge-node`（SUSE Edge，從 Elemental 映像開機），以及 `k3s-node` / `rke2-node`（Rancher 建立叢集用的 Leap 16 cloud image；大小由 plan 中的 `resources.k3s-node` / `resources.rke2-node` 決定）。

### `exposed_services`

每一筆會在主機的 public zone 上變成一條 `firewalld port_forward` 規則。

| 欄位 | 說明 |
|-------|-------|
| `host_port` | 外部使用者連線的主機連接埠 |
| `guest_port` | 流量要轉送到的 guest VM 或 VIP 連接埠 |
| `target` | `vip` 表示 Harvester 叢集 VIP，或是 `nodes` 清單中的某個節點 `name` |
| `proto` | `tcp` 或 `udp` |

### `nodes`

每個節點是一台具體的 VM。欄位：

| 欄位 | 必填 | 說明 |
|-------|----------|-------|
| `name` | 是 | 唯一的 VM 名稱。必須出現在 `start_order` 中。 |
| `template` | 是 | 引用 `node_templates` 中的一個 key |
| `index` | 是 | 用於命名的整數索引（config ISO 名稱等） |
| `hostname` | 否 | 短主機名稱（不含網域）。省略時依 index 產生。 |
| `uuid` | 否 | libvirt domain UUID。省略時以確定性方式產生。 |
| `ip` | 是 | 靜態 IP。必須在 `network.cidr` 內、`dhcp_range` 之外。 |
| `config_iso_name` | 僅 Harvester | Harvester config ISO 檔名（不含副檔名） |
| `ssh_user` | 否 | `rodeo ssh` 使用的作業系統帳號。預設為 template 的 `ssh_user`。 |
| `interfaces` | 是 | NIC 宣告清單，含 `role` 與 `mac` |

**MAC：**在每個 lab 中必須唯一、以 `02:` 開頭（locally administered），並且與 DHCP 靜態租約一致。如果省略 MAC，renderer 會依 plan 名稱與節點名稱以確定性方式產生一個。需要精確、可重現的識別時，請提供明確的值。

**舊版扁平 MAC 欄位**（`mgmt_mac`、`storage_mac` 等）與 `interfaces` 清單並存，是為了在過渡到完全由 inventory 驅動建置的期間相容 Ansible role。它們必須與 `interfaces` 中的項目保持一致。

### `downstream_clusters`

僅限 `rancher` 與 `rancher-test` profile。每一筆是 Rancher 在 `downstream` 階段於 lab VM 上建立的一個 custom cluster。每個節點都同時執行 etcd、control plane 與 worker，所以三節點叢集就是三成員的 etcd HA。

```yaml
downstream_clusters:
  - name: k3s-single
    distro: k3s            # k3s | rke2
    nodes: [k3s]
  - name: rke2-ha
    distro: rke2
    nodes: [rke2-ha1, rke2-ha2, rke2-ha3]
```

| 欄位 | 說明 |
|-------|-------|
| `name` | 在 Rancher 中的叢集名稱（`fleet-default` namespace） |
| `distro` | `k3s` 或 `rke2`；Kubernetes 版本取自 plan 中的 `versions.downstream_k3s` / `versions.downstream_rke2` |
| `nodes` | `nodes` 中的 VM 名稱；一台 VM 只能屬於一個叢集 |

這個階段會等到每個叢集與其所有節點都 Ready，重新執行時會略過已經註冊的節點。

### `rancher.ui_extensions`

宣告這個 rodeo 需要哪些 Rancher Prime UI extension（例如 SUSE Virtualization / Harvester
extension），每一個都固定在某個版本。只對有 Rancher VM 的 profile 有意義：
沒有 Rancher 的純 Harvester profile，或不需要任何 extension 的 profile，請省略這個 key（或讓清單保持空白）。

每次部署時（以及每次執行 `rodeo install-extensions` 時），rodeo-cli
都會把每個宣告的項目與實際叢集比對同步：確認 chart
repo（`ClusterRepo`）存在、強制重新建立索引，讓固定的版本即使在快取索引過舊時也能解析到，
然後透過 Rancher catalog API 安裝 chart（或就地升級較舊的 release）。已經是最新的 extension 不會被動到。
同步失敗不會中止流程：失敗的項目會記錄一則警告，部署繼續進行；
它絕不會因為某個 chart 拉取失敗就擋住整個流程的其他部分。

```yaml
definition:
  rancher:
    ui_extensions:
      - name: harvester              # repo 中的 chart 名稱，也是 UIPlugin 名稱
        version: "1.8.1"             # 與 Harvester server 版本保持一致
        repo:
          name: rancher              # ClusterRepo 名稱（已存在就沿用）
          git_repo: https://github.com/rancher/ui-plugin-charts
          git_branch: main
```

| 欄位 | 必填 | 說明 |
|-------|----------|-------|
| `name` | 是 | repo 中的 chart 名稱。也會成為安裝後 `UIPlugin` 資源的名稱。 |
| `version` | 是 | 固定的 chart 版本。同步時會升級或降級到完全相符。 |
| `repo.name` | 是 | `ClusterRepo` 名稱。共用同一個 repo 的項目會沿用它：只要宣告一次。 |
| `repo.git_repo` | 僅在 repo 尚不存在時 | chart repo 的 Git URL。如果 `repo.name` 已經以 `ClusterRepo` 存在（例如由前面的項目宣告，或叢集上原本就有），就會略過。 |
| `repo.git_branch` | 否 | 預設為 `main`。 |

`ui_extensions` 是一個清單：宣告多個項目，就能同步來自同一個或不同 repo 的多個
extension。

**這個設定實際上必須放在哪裡：**`rodeo up --profile <name>` 與 `rodeo new
--from <name>` 是從 `rodeo/data/examples/<name>/definition.yaml` 部署（內建的範例會被複製到你的 lab 目錄），**不是**從
`rodeo/data/platforms/suse-virt/definition.yaml`。如果你是把這個設定加到內建 profile，而不是自訂 profile，
請編輯該範例自己的 `definition.yaml`，否則它會在沒有任何提示的情況下永遠不生效。事後要如何檢查與安裝，請見 [`rodeo
install-extensions`](../guide-harvester.md#day-2-operations)。

---

## 哪些放這裡、哪些放 rodeo-plan.yaml { #what-belongs-here-vs-rodeo-planyaml }

| 放在 `definition.yaml` | 放在 `rodeo-plan.yaml` |
|-----------------------------|-----------------------------|
| 節點名稱、IP、MAC | VM 資源大小（RAM、CPU、磁碟） |
| 網路 CIDR、gateway、DNS 網域 | 帳密與 secret 引用 |
| 啟動順序與 etcd join 間隔 | 部署目標（baremetal/instruqt） |
| 對外服務與連接埠對應 | 軟體版本 |
| 節點 template（介面角色） | 儲存裝置路徑 |
| 主機準備需求 | Jinja2 參數 |

經驗法則：definition 是**什麼**。plan 是**多大**與**在哪裡**。

---

## 自動產生的值與明確指定的值 { #generated-vs-explicit-values }

renderer（`rodeo/inventory.py`）可以用確定性方式產生缺少的值：

| 欄位 | 省略時如何產生 |
|-------|---------------------------|
| `mac` | `plan.name + node.name + role + index` 的雜湊，每次執行都相同 |
| `uuid` | 由同一個雜湊產生的 UUID v5 |
| `hostname` | 好記的標籤：`alpha`、`bravo`、`charlie`、…… 或 `<name>-N` |

明確指定的值永遠優先。需要精確、穩定的識別時請提供，例如重新建置一個其他系統（DNS、監控）已經用 MAC 認得的節點時。
