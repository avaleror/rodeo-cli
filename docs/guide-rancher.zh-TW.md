# K3s 上的 Rancher Prime：profile 指南

本指南說明兩個 Rancher profile。兩者都在獨立的 VM 上執行 **K3s 上的 Rancher Prime**，不含 Harvester，另外再由 Rancher 自己在小型 lab VM 上建立下游叢集：

| Profile | 下游叢集 | VM 數 | RAM |
|---------|--------------------|-----|-----|
| `rancher-test` | `k3s-single`（單節點 K3s）、`rke2-single`（單節點 RKE2） | 3 | 約 18 GiB |
| `rancher` | `k3s-single`、`rke2-single`、`rke2-ha`（三節點 RKE2，etcd HA） | 6 | 約 30 GiB |

下游節點的規格是各發行版的最低需求（K3s 2 vCPU / 2 GiB、RKE2 2 vCPU / 4 GiB），所以這些 lab 是用來測試的，不適合跑實際工作負載。

當 workshop 或 demo 的重點是 **Rancher 多叢集管理**、而不是 Harvester HCI 時，就用這兩個 profile。

---

## 你會得到什麼 { #what-you-get }

| 元件 | 預設 IP | Port |
|-----------|-----------|------|
| Rancher Prime（Rancher UI） | 192.168.122.9 | 30002（NodePort） |
| 透過主機 DNAT 存取 Rancher UI | 主機 IP | 30002 |
| `k3s-single` 節點 `k3s` | 192.168.122.41 | |
| `rke2-single` 節點 `rke2` | 192.168.122.42 | |
| `rke2-ha` 節點 `rke2-ha1..3`（僅 `rancher`） | 192.168.122.51-53 | |
| SSH 存取 | `rodeo ssh rancher`、`rodeo ssh k3s`…… | |

每台 VM 都從 openSUSE Leap 16 cloud image 開機（不走 iPXE，不用等漫長的安裝）。Rancher 透過 Helm 安裝在 K3s 上。接著 rodeo 會在 Rancher 裡為每個下游叢集建立一個 custom cluster，並在每個節點上執行註冊指令。每個節點都擔任全部三種角色（etcd、control plane、worker）。Rancher 會在節點上安裝 K3s 或 RKE2，這些叢集會以 Rancher 管理的叢集出現在 **Cluster Management**，所以可以直接從 UI 升級或擴充。

版本：Rancher Prime 2.15.2、cert-manager v1.21.2、K3s/RKE2 v1.36.4（Rancher 2.15.2 支援的最新 Kubernetes）。可以在 plan 的 `versions` 區塊覆寫（`rancher`、`k3s`、`cert_manager`、`downstream_k3s`、`downstream_rke2`）。

---

## 主機需求 { #host-requirements }

| 資源 | 最低需求 |
|----------|---------|
| 作業系統 | 具備 KVM 的 Linux，若主機本身是 VM 需支援 nested virtualization |
| RAM | 可用約 18 GiB（`rancher-test`）或約 30 GiB（`rancher`） |
| 磁碟 | `/var/lib/libvirt/images` 需有約 130 GiB（`rancher-test`）或約 220 GiB（`rancher`）可用空間 |
| CPU | 可分配約 8（`rancher-test`）或約 14（`rancher`）個 vCPU |
| Python | 3.10+ |

執行 `rodeo doctor` 檢查主機，確認放得下這個 profile。

---

## 部署 { #deploy }

```bash
rodeo up --profile rancher        # 或：rodeo up --profile rancher-test
```

`rodeo up` 會檢查主機、安裝缺少的套件（會先徵求你同意）、產生帳密，然後開始部署。它會自動以 sudo 提權，你不需要自己在前面加 `sudo`。

`rodeo up` 會自動在 tmux session（`rodeo-rancher`）裡執行，SSH 斷線也不會中止部署。用 `tmux attach -t rodeo-rancher` 接回來。在腳本裡可以加 `--no-tmux` 略過。

如果已經裝好 `rodeo up`，而且之前執行過一次 `install-deps`：

```bash
rodeo up --profile rancher --yes    # 略過所有確認提示
```

### 部署過程中會發生什麼 { #what-happens-during-deploy }

流程依序執行以下階段：

1. **kvm_host**：在主機上設定 libvirt、防火牆規則與 storage pool
2. **vms**：下載 cloud image、注入 cloud-init、建立 VM 磁碟與 libvirt 定義
3. **boot**：啟動 libvirt 網路與 VM（不用等 PXE）
4. **rancher**：等 Rancher VM 取得 IP、安裝 K3s、以 Helm 部署 Rancher Prime，並等 UI 進入健康狀態
5. **downstream**：在 Rancher 建立每個下游叢集、註冊節點，並等到每個叢集都 Ready
6. **finalise**：設定主機重開機時 VM 自動啟動（在 Instruqt 上會略過；改在 hostimage 開機時執行 `rodeo start-if-needed`，不要把 finalise 做進映像）

總時間：一般主機約 **20–40 分鐘**。大部分時間花在 Rancher 建立下游叢集；三節點 RKE2 叢集是一次加入一個節點。

---

## 登入 { #log-in }

`rodeo up` 完成後會印出 Rancher 網址與帳密。之後要再查的話：

```bash
rodeo status
```

- **網址：** `https://<host-ip>:30002`
- **帳號：** `admin`
- **密碼：** `~/.rodeo/secrets.yaml` 裡 `rancher_admin_password` 的值

如果第一次登入時要求確認 server URL，請填主機 IP（`rodeo status` 顯示的那個），不要填 `localhost`。在 AWS 上由 rodeo 部署的 Rancher 已經設定好 server URL，登入後直接進入首頁；從 Mac 部署到 AWS 時，網址與帳密要用 `rodeo status --cloud` 查。

---

## Day-2 維運 { #day-2-operations }

| 工作 | 指令 |
|------|---------|
| 檢查 VM 與服務健康狀態 | `rodeo status` |
| SSH 進 Rancher VM | `rodeo ssh rancher` |
| SSH 進下游節點 | `rodeo ssh k3s`、`rodeo ssh rke2-ha1`…… |
| 追蹤 serial log | `rodeo logs rancher` |
| 重新啟動 VM | `rodeo restart rancher` |
| 正常關機 | `rodeo stop --all --yes` |
| 關機後重新啟動 | `rodeo start --all --yes` |
| 刪除整套 lab | `rodeo clean --yes` |
| 完整重置主機 | `rodeo clean --all --yes --secrets` |

### 從失敗的地方接續部署 { #resume-a-failed-deploy }

```bash
rodeo status                    # 找出最後失敗的階段
rodeo deploy --from rancher     # 從該階段接續
```

---

## Instruqt 流程 { #instruqt-workflow }

部署前在 `rodeo-plan.yaml` 設定 `deployment_target: instruqt`，讓 `finalise` 被略過。**不要**把 `finalise` 做進 hostimage（自動啟動可能讓 Instruqt agent 或 console 卡住）。部署完成後，照成功畫面上的檢查清單操作、儲存 hostimage，並在 track 的 setup script 裡加上 **`rodeo start-if-needed`**，讓 lab 每次開機都會啟動。請見 [Instruqt 範例](examples/instruqt.md)。

---

## 自訂 { #customize }

所有設定都在 `~/.rodeo/profiles/rancher/`（若用 `--profile` 部署）或你本機的 lab 目錄。

在部署時覆寫資源：

```bash
rodeo deploy -P resources.rancher.memory_mib=12288
rodeo deploy -P resources.rancher.vcpu=6
rodeo deploy -P resources.rke2-node.memory_mib=6144   # 套用到每個 RKE2 節點
```

有哪些叢集、哪台 VM 加入哪個叢集，定義在 `definition.yaml`（`downstream_clusters` 與 `nodes`）。

要複製一份可以編輯、重新部署的版本：

```bash
rodeo new myrancher --from rancher
$EDITOR ~/.rodeo/profiles/myrancher/rodeo-plan.yaml
rodeo up --profile myrancher
```

完整格式說明：[建立自己的 rodeo](custom-rodeos.md)。
