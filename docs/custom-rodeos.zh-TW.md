# 建立自己的 rodeo（宣告式）

rodeo 是用資料描述的，不是用程式碼。你從一個可用的 lab 產生一份 rodeo、編輯兩個
YAML 檔，然後部署它。本指南說明這個流程與檔案格式。

```bash
rodeo new mylab --from harvester      # 複製一個可用、可編輯的 lab
$EDITOR ~/.rodeo/profiles/mylab/definition.yaml
rodeo up --profile mylab              # 部署你編輯過的 lab
```

比較喜歡在瀏覽器裡組合？[Rodeo Builder](rodeo-builder.md) 可以挑選 lab 引擎、
排列故事章節，並下載 profile。

已經有 lab-in-a-box 的 lab（一個 `lab.json`，或它的 lab-builder 下載的 YAML）？
一個步驟就能把它變成 rodeo：

```bash
rodeo new mylab --from-lab-json lab.json
rodeo up --profile mylab
```

profile 的 plan 會以 `lab_in_a_box.lab` 承載這個 lab（請見
[plan 參考](reference/plan.md)），rodeo 會照原樣部署它，並套用主機的
網路、網域、節點位址與 secrets。匯入時，帳密值
（password、token、registration code、API key 與 secret key 欄位）會變成 `??<field>`
占位符，並列在 `operator_secrets` 裡，所以 `rodeo up` 只會詢問一次，
plan 裡不會存放任何 secret；在 add-on 設定中直接寫死的節點名稱與位址會
變成 `${node_fqdn:<vm>}` / `${node_ip:<vm>}`。這個指令會列出這兩者，以及需要
檢查的項目：看起來像這個 lab 的位址卻不屬於任何節點的、Harvester 節點
（`rodeo ssh`/`status` 會把它們當成一般 VM），以及透過 PXE 開機的節點（在
lab-in-a-box 安裝好之前連不到）。它也會列出這台機器上的 lab-in-a-box
不認得的欄位，這些欄位 `rodeo up` 會拒絕。它會對照已安裝的版本來檢查，沒有的話用
`RODEO_LABINABOX_PATH`，再沒有的話用 rodeo 已下載到 `~/.rodeo/vendor/` 的最新版本。
這些都沒有時，它會告訴你這個 lab 沒有被檢查；不過 `rodeo up` 在
建置前還是會檢查。

---

## 兩個聽起來都像「profile」的詞 { #two-words-that-both-sound-like-profile }

| 名詞 | 它是什麼 | 放在哪裡 |
|------|-----------|----------------|
| **engine `type`** | 部署的 *pipeline*（會執行哪些階段） | plan 裡的 `type:`；程式碼在 `rodeo/profiles/` |
| **profile**（`--profile`） | 你在 CLI 上挑選、有名稱、可以執行的 *lab* | 一個 config-dir（內建，或放在 `~/.rodeo/profiles/` 下） |

目前的 engine type：`suse-virt`（Harvester HCI + Rancher）、`rancher` 與
`rancher-test`（Rancher Prime 加上它建立的 K3s/RKE2 叢集，不含 Harvester），以及
`suse-edge`（Rancher + Elemental + EIB + edge 節點）。`lab-in-a-box` 是外部外掛，
有自己的引擎。你的自訂 lab 從中挑一個當作 `type`，其餘部分自行
自訂。你撰寫的是拓撲，不是新的 pipeline。

列出所有可以部署的項目：

```bash
rodeo profiles
```

| Profile | 類別 | type | 內容 |
|---------|------|------|------------|
| `rancher-test` | 內建 | rancher-test | Rancher Prime + 單節點 K3s 與 RKE2 叢集（最小） |
| `rancher` | 內建 | rancher | Rancher Prime + 單節點 K3s、單節點 RKE2 與三節點 RKE2 叢集 |
| `test` | 內建 | suse-virt | 兩節點 Harvester，不含 Rancher |
| `harvester-ha` | 內建 | suse-virt | 三節點 Harvester，不含 Rancher（etcd HA） |
| `harvester-2n` | 內建 | suse-virt | 兩節點 Harvester + Rancher Prime |
| `harvester` | 內建 | suse-virt | 三節點 Harvester HCI + Rancher Prime |
| `virt-workshop` | 內建 | suse-virt | `harvester` + 映像快取、NFS 與範例 VM，供 SUSE Virtualization workshop 使用 |
| `virt-workshop-2n` | 內建 | suse-virt | workshop lab 的兩節點版本 |
| `suse-edge` | 內建 | suse-edge | Rancher + Elemental + EIB + edge 節點（SUSE Edge 3.7） |
| `smlm-workshop` | 內建 | lab-in-a-box | SUSE Multi-Linux Manager + 8 台用戶端，由 lab-in-a-box 外掛建置 |
| `kvm-hosts` | 內建 | kvm-hosts | 一般 KVM 主機，不含 VM；搭配 fleet 就是小型 KVM 叢集（[指南](guide-kvm-hosts.md)） |
| *（你的）* | 自訂 | 任何 | 你產生並編輯的任何內容 |

---

## profile 就是一個 config-dir { #a-profile-is-a-config-dir }

兩個檔案，加上選用的產出物資料夾：

```
mylab/
├── rodeo-plan.yaml     # type、資源、帳密、deployment_target
├── definition.yaml     # 拓撲（節點、網路、對外服務、主機準備）
├── certs/              # 選用：要提供的 CA 憑證
├── <hostname>/         # 選用：要在該節點上 kubectl apply 的 manifest（請見下文）
└── custom/scripts/     # 選用：編號的腳本
```

`manifests/` 與 `helm/values/` 也會出現在內建範例裡，並記錄在
inventory metadata 中，但目前沒有任何東西會使用它們，它們保留給未來的
階段。不要把檔案放在那裡、期待它們被套用；請使用下一節說明的依主機名稱
命名的目錄。

### `custom/scripts/`：在 `finalise` 之後的 `custom_scripts` 階段 { #customscripts-a-custom_scripts-phase-after-finalise }

編號的可執行腳本（`50-my-setup.sh`、`60-seed-vm.sh`……）會依排序
順序，在部署的最後一個階段執行，此時叢集與網路都已完全就緒。
每個腳本會拿到：

- `KUBECONFIG`：Harvester 的 kubeconfig（如果存在的話，`~/.rodeo/harvester-kubeconfig`）
- `RODEO_PLAN_NAME`、`RODEO_LAB_DIR`、`RODEO_CONFIG_DIR`，用來識別執行情境

把它們寫成可重複執行（先檢查再建立）：這個階段和 `apply` 一樣是 `no_cache_phase`，
所以每次 `rodeo up` 都會重新執行，不只是第一次。失敗的腳本會
被明顯地回報，但不會中止其他腳本（它們通常是彼此獨立的步驟）；只要有任何腳本失敗，
這個階段本身最後仍然會被標為失敗，所以 `rodeo status`／CI 會注意到。
`custom_scripts` 的防護方式和 `finalise` 相同：在
`deployment_target: instruqt` 時會略過，除非加上 `--finalise`，因為這裡的腳本會建立
持久的叢集狀態，這些狀態應該燒進映像快照，而不是在
每次 session 開機時重新執行。

`rodeo up`、`plan` 與 `deploy` 會自動偵測這個目錄（從目前的
目錄往上層找），所以在 lab 目錄裡可以省略 `--config-dir`。

### rodeo-plan.yaml：各項設定 { #rodeo-planyaml-the-knobs }

```yaml
type: suse-virt              # pipeline：suse-virt、rancher、rancher-test 或 suse-edge
name: mylab                  # 用於狀態與 libvirt 物件名稱
deployment_target: baremetal # baremetal | instruqt | aws

resources:
  harvester: { memory_mib: 16384, vcpu: 8, disk_gb: 320 }
  rancher:   { memory_mib: 8192,  vcpu: 4, disk_gb: 60 }

credentials:                 # ??key 會從 ~/.rodeo/secrets.yaml 解析
  harvester_os_password: "??harvester_os_password"
  harvester_admin_password: "??harvester_admin_password"
  rancher_admin_password: "??rancher_admin_password"
  harvester_token: "??harvester_token"

storage:
  device: ""                 # "" = 單一磁碟；在多磁碟主機上可寫 /dev/nvme1n1
  image_dir: /var/lib/libvirt/images
```

### definition.yaml：拓撲 { #definitionyaml-the-topology }

這是宣告式模型。你描述*邏輯上*的 lab；renderer 會把它編譯成
具體的 MAC、DHCP 租約、libvirt 網路、防火牆規則與 PXE 資料。內建的
檔案有大量註解：請把它們當作參考來讀：

- Harvester：`rodeo/data/platforms/suse-virt/definition.yaml`
- Rancher：`rodeo/data/platforms/rancher/definition.yaml`（它的 `downstream_clusters` 區塊列出 K3s/RKE2 叢集，以及每個叢集由哪些 VM 加入）
- SUSE Edge：`rodeo/data/platforms/suse-edge/definition.yaml`

主要區段：

```yaml
definition:
  name: mylab
  start_order: [harvester1, harvester2, harvester3, rancher]
  harvester_node_names: [harvester1, harvester2, harvester3]
  harvester_ready_count: 3

  network:
    cidr: 192.168.122.0/24
    gateway: 192.168.122.1
    domain: rodeo.lab

  node_templates:        # 每種節點類型的「藍圖」
    harvester:
      flavor: harvester
      infra_type: harvester     # 讓 stop/start 能依基礎架構處理
      interfaces: [...]         # 網卡的「線路」
    rancher:
      flavor: rancher
      infra_type: rancher

  exposed_services:      # 會變成主機防火牆的 DNAT
    - { name: rancher, host_port: 30002, guest_port: 30002, target: rancher }

  nodes:                 # 具體的 VM
    - name: harvester1
      template: harvester
      ip: 192.168.122.11
      # 省略 mac/uuid/hostname 時會自動產生
```

你可以省略 MAC、UUID 與主機名稱：renderer 會根據 plan 名稱以確定性的方式
產生它們。只有在需要確切的值時才提供。

---

## 把 manifest 套用到節點 { #applying-manifests-to-a-node }

把 `.yaml`/`.yml` 檔放進以節點主機名稱命名的目錄（和
`definition.yaml` 中 `nodes:` 下的某個 `name:` 相符），例如：

```
mylab/
└── harvester1/
    └── namespaces.yaml
```

`apply` 階段會 SSH 進那個節點，依排序順序對在那裡找到的每個檔案執行
`kubectl apply -f -`：每個節點一個目錄，只套用到那個節點。
`apply` 是唯一一個一定會重新執行的階段（它從不快取），所以在這裡編輯檔案
後重新執行 `rodeo up --profile mylab`，變更會立即生效，
這點和修改拓撲或資源不同（請見下文）。

---

## 常見的修改 { #common-edits }

**變更叢集大小**（Harvester lab）：一起編輯 `nodes`、`start_order`、
`harvester_node_names` 與 `harvester_ready_count`，讓它們彼此一致，然後依你的主機
調整 `resources`。

**變更資源**：編輯 plan 裡的 `resources.harvester` / `resources.rancher`，或
不編輯檔案、在部署時覆寫：

```bash
rodeo up --profile mylab            # 或臨時覆寫：
rodeo deploy -P resources.harvester.memory_mib=20480
```

**使用專用磁碟**：在 plan 裡設定 `storage.device: /dev/nvme1n1`（用
`lsblk` 確認）。

**對外提供另一個服務**：在 definition 的 `exposed_services` 加上一筆。

編輯之後，部署之前先預覽：

```bash
cd ~/.rodeo/profiles/mylab
rodeo plan
```

---

## 部署之後重新執行 { #re-running-after-a-deploy }

這裡的可重複執行是**階段層級，不是資源層級**。每個階段（`kvm_host`、`vms`、
`pxe_server`、`cluster`、`rancher`、`finalise`）成功後會在
`~/.rodeo/state/<name>.yaml` 標記為完成，單純重新執行時會略過任何已
標記為完成的階段：不論你在 `definition.yaml` 或 `rodeo-plan.yaml` 改了什麼。
`apply` 階段是唯一的例外（它從不快取，所以節點 manifest 的修改一定會在
下次執行時生效：請見[把 manifest 套用到節點](#applying-manifests-to-a-node)）。

所以 lab 部署過一次之後，編輯 `nodes`、`resources` 或 `network` 再
重新執行 `rodeo up --profile mylab`，會略過已完成的階段，只有一個例外：
`rodeo deploy` / `rodeo up` **預設會調回記憶體／vCPU 的漂移**（從
`vms` 重設）。拓撲變更（新增／移除節點）仍然需要手動處理：

```bash
cd ~/.rodeo/profiles/mylab
rodeo plan                  # 一律顯示目前的漂移
rodeo deploy                # 預設會調回記憶體／vCPU 漂移
rodeo deploy --no-reconcile # 舊的僅依階段快取的行為
rodeo deploy --from vms     # 從特定階段接續，或
rodeo deploy --force        # 重做每個階段，或
rodeo clean --yes && rodeo up --profile mylab   # 銷毀這個 lab 的 VM 並重新部署
```

預設的調整會在實際的記憶體／vCPU 和 plan 不同時，清除快取的 `vms`（以及之後）
階段狀態。對於**正在執行**的 domain，libvirt 仍然不會即時調整大小，
請停止／啟動 domain（或 `clean` + 重新部署），讓非作用中的 XML 生效。

---

## 完整流程 { #the-loop-end-to-end }

```bash
rodeo new edge-lab --from harvester     # 以完整的 Harvester lab 為基礎產生
$EDITOR ~/.rodeo/profiles/edge-lab/definition.yaml   # 縮減成 1 個節點，重新調整
rodeo profiles                          # 確認它有列出來（custom）
rodeo up --profile edge-lab             # doctor → secrets → 部署 → 登入資訊
```

自訂 profile 會從 `~/.rodeo/profiles/<name>/` **就地**部署。在 lab
部署過一次之前，編輯檔案後重新執行 `rodeo up --profile <name>` 就能正常運作，
因為還沒有任何狀態，每個階段都會重新執行。一旦它*已經*部署過，重新執行只會在
`apply` 階段讀取檔案的修改；其他階段需要 `--force` 或 `--from`（請見
[部署之後重新執行](#re-running-after-a-deploy)），否則會被默默略過。如果
想要從一份新的副本開始、而不是就地編輯，就用新名稱產生，或加上 `--dir`。

---

## 附註 { #notes }

- `--from` 可以接受任何內建 profile（`rodeo profiles` 會列出它們）。複製最接近的
  可用 lab 再刪減：這是得到有效拓撲風險最低的方式。
- 帳密維持 `??key` 形式；`rodeo up`/`init` 會替你產生 `~/.rodeo/secrets.yaml`。
  不需要 `source` 或 `sudo -E`。
- 部署 Harvester 拓撲會牽動 MAC ↔ DHCP ↔ config-ISO 這條鏈。做了
  結構性的修改之後，先在真正的 KVM 主機上驗證，再依賴它。
- `rodeo generate` 是較舊的互動式產生器。請優先使用 `rodeo new`。
