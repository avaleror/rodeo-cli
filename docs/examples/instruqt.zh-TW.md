# 範例：建置 Instruqt track 映像

這個範例說明如何建置一個內含預先部署好 Harvester lab 的 Instruqt track。參加者一開始就有正在執行的叢集：workshop 期間不用等 iPXE 安裝。

## 運作方式 { #how-it-works }

Instruqt 會給你一台 **builder 執行個體**，你在上面部署 lab。接著你 **Save** 一份 hostimage（快照）。當參加者開始一個 challenge 時，會拿到那份映像的副本。

和裸機最主要的差異：**不要把 `finalise` 燒進 hostimage。** 這個階段會啟用 VM 自動啟動 + `libvirt-guests`。如果映像開機時這些是開啟的，nested KVM / virbr0 可能會和 cloud-init 搶時序，讓 Instruqt console 停在 **Please Wait**。設定 `deployment_target: instruqt` 時，rodeo 會自動略過 `finalise`。請改為在 track 的 setup 腳本中使用 **`rodeo start-if-needed`**，讓每台執行個體都把 lab 帶起來。

## Instruqt 上的主機規格 { #host-sizing-on-instruqt }

使用規格充裕的 builder（`n2-standard-32` 或約 40 vCPU），並啟用 nested virtualization：

| 資源 | 數值 |
|----------|-------|
| RAM | 128 GiB |
| CPU | 32–40 vCPU |
| 磁碟 | 1–2 TB |
| Nested virt | 必要 |

如果你使用的是 SUSE 的 Instruqt 組織，請在 Instruqt sandbox 設定中申請 `geekohive` 機器類型。

**Guest vCPU 預算：** 讓 Σ guest vCPU ≤ 主機邏輯 CPU 的約 70%。當你以
`deployment_target: instruqt` 建立 lab 時（在 Instruqt 主機上執行 `rodeo up`，或
`rodeo up --deployment-target instruqt`），rodeo 會套用依主機調整的預設規格，在
32–40 vCPU 的 builder 上通常是每個 Harvester 節點 **6–8 vCPU / 20 GiB**、Rancher
**4 vCPU / 8 GiB**。隨時可以用 `-P resources.harvester.vcpu=…` 覆寫。

在 Instruqt 上，guest 磁碟快取預設為 `writeback`/`threads`（請見 plan 參考中的 `libvirt.disk_cache`）：
在 nested 雲端環境中的 I/O 表現比裸機的 `none`/`native` 好。

## 步驟 1：在 builder 上安裝 rodeo-cli { #step-1-install-rodeo-cli-on-the-builder }

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

這會 clone repo、在內部建好 Python 環境、把 `rodeo` 連結成系統指令，並安裝主機相依套件（KVM、libvirt、ansible、kubectl）。

## 步驟 2：設定部署目標 { #step-2-set-deployment-target }

建立你的 lab 目錄，並把目標設為 `instruqt`：

```bash
rodeo new mylab --from harvester
```

編輯 `~/.rodeo/profiles/mylab/rodeo-plan.yaml`：

```yaml
deployment_target: instruqt   # 關鍵：略過 finalise（絕不要把它燒進 hostimage）
```

或直接使用內建的 `harvester` profile，在部署時覆寫：

```bash
rodeo deploy -P deployment_target=instruqt
```

## 步驟 3：部署 lab { #step-3-deploy-the-lab }

```bash
rodeo up --profile harvester --yes
```

`rodeo up` 會立刻把自己包進名為 `rodeo-harvester` 的 tmux session。這在 Instruqt 上非常重要：在 90–150 分鐘的部署期間，瀏覽器分頁可能會逾時或關閉，沒有 tmux 的話行程會中止，讓主機處於損壞的狀態。

**如果你的 Instruqt 分頁關閉或逾時**，重新開啟終端機分頁並接回來：

```bash
tmux attach -t rodeo-harvester
```

不停止部署、只離開 session：`Ctrl+b  d`。

設定 `deployment_target: instruqt` 時，`finalise` 階段會自動略過。這正是 hostimage 需要的。

Save 之前，先確認叢集健康：

```bash
rodeo status
```

預期結果：3 個 Harvester 節點都是 Ready、VIP 連得到、Rancher UI 可以存取。

## 步驟 4：hostimage 檢查清單（Save 之前） { #step-4-hostimage-checklist-before-save }

部署後的成功畫面會印出同一份檢查清單。按下 **Save** 之前請確認：

| 檢查項目 | 原因 |
|-------|-----|
| **不要**執行 `rodeo deploy --finalise` | 會把 `libvirt-guests` + VM 自動啟動燒進映像 → 可能讓開機卡住／停在 **Please Wait** |
| `firewall-cmd --list-ports` 包含 **15778/tcp** 與 **15779/tcp** | Instruqt 終端機／編輯器 agent 的 port |
| `systemctl is-enabled libvirt-guests` 為 **disabled** | 在映像中必須保持關閉 |
| track setup 會執行 `rodeo start-if-needed` | 每次開機時啟動 VM + 重新套用 DNAT/nft |

選用：Save 之前執行 `rodeo stop --all --yes`，讓 nested guest 磁碟（etcd/檔案系統）更乾淨。agent 連線不需要這一步。

**Save 擷取的是按下當時的磁碟狀態。** 在 Save *之後*才執行 `finalise`，只會改變運作中的 builder；它**不會**更新已經儲存的 hostimage。在 finalise 之後重新 Save 就是映像被污染的原因，workshop 的 hostimage 不要這樣做。

## 步驟 5：建立 Instruqt 快照 { #step-5-take-the-instruqt-snapshot }

使用 Instruqt UI（**Save** hostimage）或 API。此時叢集可能仍在 builder 上執行，但映像中的 VM **不能**設定為自動啟動，這樣才是正確的。

## 步驟 6：track setup + 在參加者執行個體上驗證 { #step-6-track-setup-verify-on-an-attendee-instance }

**把 `rodeo start-if-needed` 接進 track 的參加者開機／setup 腳本**（也就是 Instruqt 從 hostimage 啟動新執行個體時會執行的任何東西）。要讓 lab 健康運作，這一步不是選用的：

- nested VM 不會自動啟動（這是刻意的設計：映像中沒有 finalise）。
- libvirt 用來重新套用 DNAT + `guest_input` accept 的 qemu hook，只有在真正啟動 VM 時才會可靠地觸發，在 VM 保持「原狀」的 hostimage 冷開機時則不會。
- `start-if-needed` 可以重複執行：視需要啟動主機服務 + VM，然後強制套用 nft 規則。

```bash
rodeo start-if-needed
```

從執行個體內部檢查：

```bash
rodeo status
```

如果開機後 VM 是關閉的，或 UI 連不到，很可能是 setup 腳本漏了 `start-if-needed`，把它加上去再重新測試；**不要**用把 finalise 燒進新的 Save 來「修正」。

## firewalld 的時序限制 { #the-firewalld-timing-constraint }

`kvm_host` Ansible role 會在準備主機時（建置初期，任何 VM 都還不存在時）**停用、停止並 mask firewalld**。這是刻意的：在 SLES 16 上，firewalld 透過 D-Bus 和 NetworkManager 整合，而 cloud-init 不會替外部網卡指定 zone，在這件事處理好之前啟動 firewalld，可能會中斷 Instruqt 的管理連線。

firewalld 本身會在稍後的 `cluster` 階段被 unmask 並啟動，遠早於快照之前，而不是由 `finalise` 處理。這樣是安全的，因為在 Instruqt 上，同一個步驟還會執行：

```
firewall-cmd --zone=public --change-interface=<ext-iface> --permanent
```

`--permanent` 旗標會把介面→zone 的對應寫進 firewalld 自己保存的設定。參加者的執行個體全新開機時，firewalld 會直接讀取這個對應，而不是透過 D-Bus 和 NetworkManager 協商即時的 zone 指派，搶時序的問題正是因此消除。所以快照中的 firewalld 是啟用並執行中的，這是預期的，沒有問題。

快照之前真正重要的是：確認這個固定 zone 的步驟有執行，而且挑對了網路介面。在部署日誌中找 `Instruqt: pinning <iface> to public zone...`，並用 `firewall-cmd --get-active-zones` 確認你真正的管理網卡出現在 `public` 底下。從快照第一次以參加者身分開機時，再用同一個指令檢查一次，這才是驗證固定設定有沒有保留下來的真正測試。

絕對不能把 `finalise` 燒進 hostimage：它會啟用 `libvirt-guests` + VM 自動啟動，讓 nested 開機和 Instruqt agent 搶時序，可能讓 console 停在 **Please Wait**。

## 快照中的帳密 { #credentials-in-the-snapshot }

帳密會透過 `~/.rodeo/secrets.yaml` 燒進快照。所有參加者的執行個體都共用同一組帳密。在 workshop 用途中這是預期的：不要使用正式環境的密碼。

Instruqt 執行個體上預設的帳密位置：

```bash
cat ~/.rodeo/secrets.yaml
```

## Instruqt 特有問題的故障排除 { #troubleshooting-instruqt-specific-issues }

### Save／啟動 hostimage 後 console 卡在「Please Wait」 { #console-stuck-on-please-wait-after-save-start-hostimage }

Instruqt 的終端機與編輯器分頁是和**主機上的 agent**（TCP
**15778** 與 **15779**）溝通，而不是只靠 SSH。在 `cluster`（或 `boot`）unmask
並啟動 firewalld 之後，這些 port 必須在 public zone 上開放，否則
已儲存的 hostimage 冷開機時，即使作業系統已經起來，UI 仍會停在 **Please Wait**。

設定 `deployment_target: instruqt` 時，rodeo 會開放這些 port（Ansible `kvm_host` 防火牆
工作 + 在 `_start_firewalld` 中再次確認）。如果是在這個修正之前建置的舊映像，
先手動復原一次，再重新 Save：

```bash
sudo firewall-cmd --permanent --zone=public --add-port=15778/tcp
sudo firewall-cmd --permanent --zone=public --add-port=15779/tcp
sudo firewall-cmd --reload
# 或重新執行：rodeo deploy --from cluster   # 針對 instruqt 目標再次確認 port
```

也要確認你在 Save 之前**沒有**把 `finalise` 燒進映像
（啟用 `libvirt-guests` + VM 自動啟動可能讓 `network-online.target` 卡住，並
擋住 agent）。請優先使用 `deployment_target: instruqt`，並在參加者開機時執行
`rodeo start-if-needed`，而不是把 finalise 燒進映像。

### 參加者執行個體開機後連不到叢集 { #cluster-not-reachable-after-attendee-instance-boots }

1. 檢查 VM 是否已啟動：`virsh list --all`
2. 如果 VM 已定義但沒有執行，可能是 track 的 setup 腳本沒有執行 `rodeo start-if-needed`，現在執行它，並永久加進 setup。**不要**只為了自動啟動就把 `finalise` 燒進重新 Save 的映像。
3. 如果 VM **正在**執行，但 Harvester/Rancher UI 仍然連不到，幾乎都是 nftables 規則的問題，而不是 VM：用來重新套用 DNAT + `guest_input`-accept 規則的 qemu hook，在 hostimage 開機時不會可靠地觸發。執行：
   ```bash
   rodeo start-if-needed
   ```
   `rodeo start --all --yes` 在這裡**不能**替代，它會啟動 VM，但不會動到 nftables，所以修不好這種特定的故障情況。
4. 把 `rodeo start-if-needed` 接進參加者的開機／setup 腳本，讓每次執行個體啟動時都能自動修復。

### 建置期間 iPXE 安裝卡住 { #ipxe-install-hangs-during-build }

Instruqt 的 nested KVM 效能不一。如果 iPXE 安裝逾時：

```bash
rodeo logs harvester1   # 查看安裝進行到哪裡
rodeo deploy --from cluster --force   # 接續 cluster 階段
```

### firewalld 中斷了 Instruqt 管理連線 { #firewalld-drops-the-instruqt-management-connection }

如果你在快照之前不小心啟用了 firewalld，導致 builder 執行個體失去連線：

- 透過 Instruqt 緊急 console 存取執行個體
- 執行：`systemctl stop firewalld`
- 恢復連線後，在 firewalld 停止的狀態下重新建立快照

### 在 Instruqt 上應該執行 `rodeo deploy --finalise` 嗎？ { #should-i-run-rodeo-deploy-finalise-on-instruqt }

**對 hostimage 來說：不應該。** 讓 finalise 留在映像之外，並在開機時使用 `rodeo start-if-needed`。

`finalise` 仍然可以用在裸機上（以及少數搭配 `--finalise` 的進階情況）。如果你在 builder 上啟用它然後 Save，下一次啟動 hostimage 時就可能卡在 **Please Wait**。
