# SUSE Edge 3.7：profile 指南

本指南說明 `suse-edge` profile：在 nested KVM 上建出完整的 **SUSE Edge 3.7** 堆疊。它會建立一個搭配 **Elemental Operator** 的 Rancher Prime 管理叢集、一台 **Edge Image Builder（EIB）** VM，以及一組由 Elemental 管理的 **edge 節點**，這些就是端到端示範零接觸（zero-touch）edge 上線所需要的元件。

當 workshop 或 demo 的重點是 **edge 生命週期**時就用它：用 EIB 建置作業系統映像，再用 Elemental 透過 TPM 把 edge 節點註冊進 Rancher。

---

## 你會得到什麼 { #what-you-get }

| 元件 | 預設 IP | 存取方式 |
|-----------|-----------|--------|
| Rancher Prime（管理叢集） | 192.168.122.9 | `https://rancher.<host-ip>.sslip.io`（Traefik + Let's Encrypt） |
| Edge Image Builder（EIB） | 192.168.122.20 | `rodeo ssh eib` |
| edge1 | 192.168.122.31 | `rodeo ssh edge1` |
| edge2 | 192.168.122.32 | `rodeo ssh edge2` |
| edge3 | 192.168.122.33 | `rodeo ssh edge3` |
| edge4 | 192.168.122.34 | `rodeo ssh edge4` |

Rancher 透過 **Traefik ingress** 存取，而不是 NodePort：cert-manager 透過 HTTP01 challenge（port 80）取得真正的 Let's Encrypt 憑證，UI + API + Elemental 註冊端點都在 port 443 上提供，使用由主機外部 IP 衍生的 `sslip.io` 主機名稱。edge 節點由 `vms` 階段建立，但**留作 lab 練習來啟動**，不會自動啟動，這就是 workshop 的動手實作部分。

**元件版本（SUSE Edge 3.7）：** K3s v1.36.3+k3s1 上的 Rancher Prime 2.15.1（lab）· cert-manager v1.20.1 · Elemental Operator 1.9.2 · Elemental UI extension 3.0.3 · Edge Image Builder 1.3.4。

---

## 主機需求 { #host-requirements }

| 資源 | 最低需求 |
|----------|---------|
| 作業系統 | 具備 KVM 的 Linux，若主機本身是 VM 需支援 nested virt |
| RAM | 可用約 40 GiB |
| 磁碟 | `/var/lib/libvirt/images` 需有約 200 GiB 可用空間 |
| CPU | 可分配約 8 個 vCPU |
| 其他 | `swtpm`，提供 edge 節點使用的虛擬 TPM 2.0（由 `install-deps` 安裝） |
| Python | 3.10+ |

主機也需要讓你瀏覽的地方能連入 **port 80 與 443**，這樣 Let's Encrypt 才能完成 ACME challenge，你也才能連到 Rancher UI。

執行 `rodeo doctor` 檢查主機，確認放得下這個 profile。

### 在 AWS 上 { #on-aws }

```bash
rodeo up --profile suse-edge --target aws --yes
```

rodeo 的 AWS security group 只對你的 IP 開放 22、8443 與 30002，所以 Let's Encrypt 在那裡跑不起來。在 AWS 上，rodeo 會把 Rancher 改用它自己的憑證，位址是 `https://<host>:30002`（請接受瀏覽器警告），並給 Rancher 與 EIB VM 更多 RAM 與磁碟。建議的執行個體是 `m8id.4xlarge`，主機也有一般的 6 小時自動銷毀計時（dead-man switch）。

---

## 部署 { #deploy }

```bash
rodeo up --profile suse-edge
```

`rodeo up` 會檢查主機、安裝缺少的套件（會先徵求你同意）、產生帳密，然後開始部署。它會自動以 sudo 提權：你不需要自己在前面加 `sudo`。

`rodeo up` 會自動把自己包進 tmux session（`rodeo-suse-edge`），SSH 斷線也不會中止部署。用 `tmux attach -t rodeo-suse-edge` 接回來。在腳本裡可以加 `--no-tmux` 略過。

### 部署期間會發生什麼事 { #what-happens-during-deploy }

pipeline 會依序執行這些階段：

1. **kvm_host**：在主機上設定 libvirt、防火牆規則（包括 80/443 的 DNAT）、swtpm 與儲存池
2. **vms**：下載基礎映像、注入 cloud-init，建立 Rancher、EIB 與 edge 節點的磁碟及 libvirt 定義
3. **boot**：啟動 libvirt 網路與 Rancher + EIB VM（edge 節點保持關機，留作 lab 練習）
4. **rancher**：安裝 K3s、透過 Helm 部署 Rancher Prime，並設定 cert-manager + Traefik 以取得 Let's Encrypt 憑證
5. **elemental**：安裝 Elemental Operator（CRD + Operator），並建立 MachineRegistration，讓 edge 節點可以透過 TPM 註冊
6. **apply**：套用額外的 manifest（Fleet GitOps、示範工作負載）
7. **finalise**：啟用主機重新開機時 VM 自動啟動（在 Instruqt 上會略過；請在 hostimage 開機時使用 `rodeo start-if-needed`，而不是把 finalise 燒進映像）

總時間：在一般主機上約 **20–40 分鐘**，視映像下載速度而定。

---

## 登入 { #log-in }

`rodeo up` 完成後會印出 Rancher 網址與帳密。如果需要再看一次：

```bash
rodeo status
```

- **網址：** `https://rancher.<host-ip>.sslip.io`
- **使用者名稱：** `admin`
- **密碼：** `~/.rodeo/secrets.yaml` 裡 `rancher_admin_password` 的值

因為 Rancher 使用真正的 Let's Encrypt 憑證，不會有需要點掉的瀏覽器警告，但主機必須能從公開網際網路透過 port 80 與 443 連線，憑證才能核發。

---

## edge lab 練習 { #the-edge-lab-exercise }

edge 節點就是這個 profile 的重點。部署完成後：

1. 在 **EIB** VM 上建置 Elemental 作業系統映像（edge1/edge2 用 SelfInstall ISO，edge3/edge4 用 Default RAW）。
2. 啟動一個 edge 節點並從那個映像開機：`rodeo restart edge1`，然後用 `rodeo logs edge1` 查看。
3. 節點會透過 Elemental MachineRegistration，以它的虛擬 TPM 註冊進 Rancher。
4. 在 Rancher UI 的 **Cluster Management → Machine Inventory** 下看它出現。

用 plan 裡的 `elemental.registrations` 調整要有幾個註冊。

---

## Day-2 維運 { #day-2-operations }

| 工作 | 指令 |
|------|---------|
| 檢查 VM 與服務健康狀態 | `rodeo status` |
| SSH 進入 VM | `rodeo ssh rancher` / `rodeo ssh eib` / `rodeo ssh edge1` |
| 持續查看 serial 日誌 | `rodeo logs edge1` |
| 啟動 edge 節點 | `rodeo restart edge1` |
| 正常停止 | `rodeo stop --all --yes` |
| 停止後啟動 | `rodeo start --all --yes` |
| 銷毀 lab | `rodeo clean --yes` |
| 完整重設主機 | `rodeo clean --all --yes --secrets` |

### 接續失敗的部署 { #resume-a-failed-deploy }

```bash
rodeo status                      # 找出最後失敗的階段
rodeo deploy --from elemental     # 從那個階段接續
```

---

## Instruqt 工作流程 { #instruqt-workflow }

部署前在 `rodeo-plan.yaml` 設定 `deployment_target: instruqt`，這樣會略過 `finalise`。**不要**把 `finalise` 燒進 hostimage（自動啟動可能讓 Instruqt agent／console 卡住）。部署完成後，照著成功畫面的檢查清單做，儲存 hostimage，並把 **`rodeo start-if-needed`** 放進 track 的 setup 腳本，讓 lab 每次開機都會起來。請見 [Instruqt 範例](examples/instruqt.md)。

---

## 自訂 { #customize }

所有設定都放在 `~/.rodeo/profiles/suse-edge/`（如果是用 `--profile` 部署）或你本機的 lab 目錄。

在部署時覆寫資源或 edge 行為：

```bash
rodeo deploy -P resources.rancher.memory_mib=12288
rodeo deploy -P rancher_tls.email=me@example.com
rodeo deploy -P elemental.registrations=2
```

要建立一份可以編輯並重新部署的修改版：

```bash
rodeo new myedge --from suse-edge
$EDITOR ~/.rodeo/profiles/myedge/rodeo-plan.yaml
rodeo up --profile myedge
```

完整格式參考：[建立自己的 rodeo](custom-rodeos.md)。
