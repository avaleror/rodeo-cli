# SUSE Virtualization（Harvester）：profile 指南

本指南說明 rodeo-cli 裡的 Harvester profile。這些 profile 都會在單一主機上，以 nested KVM VM 的形式部署 **SUSE Virtualization（Harvester HCI）**：可以是你自己的 SLES 16 主機，也可以是 rodeo 替你建立的 EC2 執行個體。請依你的主機與 workshop 目標挑一個適合的。

**元件版本：** Harvester 1.8.2（RKE2、Longhorn、KubeVirt、Kube-OVN）· K3s v1.35.3+k3s1 上的 Rancher Prime 2.14.5 · cert-manager v1.20.1 · Harvester UI extension 1.8.2。

---

## 選擇 profile { #pick-your-profile }

| Profile | 節點 | Rancher | etcd | 需要的 RAM | 適用情境 |
|---------|-------|---------|------|-----------|----------|
| `test` | 2 台 Harvester | 無 | 1 個 leader | 約 36 GiB | 快速評估、主機資源吃緊、不需要 HA |
| `harvester-ha` | 3 台 Harvester | 無 | 3 成員 HA | 約 52 GiB | 具備完整 etcd HA 的 Harvester workshop，不含 Rancher |
| `harvester-2n` | 2 台 Harvester + 1 台 Rancher | 有 | 1 個 leader | 約 56 GiB | 在中型主機上跑 HCI + Rancher，不需要 etcd HA |
| `harvester` | 3 台 Harvester + 1 台 Rancher | 有 | 3 成員 HA | 約 72 GiB | 完整 lab：HCI + 多叢集管理 |
| `virt-workshop` | 3 台 Harvester + 1 台 Rancher | 有 | 3 成員 HA | AWS `m8id.8xlarge` | [SUSE Virtualization workshop](https://avaleror.github.io/suse-virt-workshop/)：`harvester` 再加上映像快取、NFS 備份目標與範例 VM |

執行 `rodeo doctor`，查看哪些 profile 放得進你主機的可用 RAM。

---

## 你會得到什麼 { #what-you-get }

### 所有 Harvester profile { #all-harvester-profiles }

| 元件 | 預設 IP | 存取方式 |
|-----------|-----------|--------|
| Harvester VIP | 192.168.122.10 | `https://192.168.122.10`（直接連線，在 lab 網路內） |
| 透過主機存取 Harvester UI | 主機 IP | `https://<host>:8443`（DNAT，可從外部連線） |
| harvester1 | 192.168.122.11 | `rodeo ssh harvester1` |
| harvester2 | 192.168.122.12 | `rodeo ssh harvester2` |
| harvester3 | 192.168.122.13 | `rodeo ssh harvester3`（`test` / `harvester-2n` 沒有這台） |

### `harvester` 與 `harvester-2n` profile 另外加上 { #harvester-and-harvester-2n-profiles-add }

| 元件 | 預設 IP | 存取方式 |
|-----------|-----------|--------|
| Rancher Prime | 192.168.122.9 | `https://<host>:30002`（DNAT） |

---

## 主機需求 { #host-requirements }

lab 的占用量（guest RAM 總和）列在上表。`rodeo doctor` 使用較高的**主機可用 RAM** 門檻，讓 nested KVM 有餘裕，例如 `harvester` 的 guest 需要約 60 GiB，但 doctor 建議主機上要有約 72 GiB 可用。

| Profile | 主機 RAM（doctor） | 磁碟 | CPU |
|---------|-------------------|------|-----|
| `test` | 約 36 GiB | `/var/lib/libvirt/images` 約 600 GiB | 約 16 vCPU |
| `harvester-ha` | 約 52 GiB | 約 1000 GiB | 約 20 vCPU |
| `harvester-2n` | 約 56 GiB | 約 700 GiB | 約 24 vCPU |
| `harvester` | 約 72 GiB | 約 1050 GiB | 約 34 vCPU |

**作業系統：** 具備 KVM 的 SLES 16 或 Leap 16。在其他任何機器上，`rodeo up` 會停下來並引導你改用 `--target aws`（設定 `RODEO_ALLOW_ANY_KVM_HOST=1` 可以在其他 Linux 上試跑，風險自負）。如果主機本身是 VM（雲端、Instruqt），必須啟用 nested virtualization。

Harvester 透過 **iPXE 網路開機**安裝（`pxe_server` 階段會透過 HTTP 提供開機腳本與設定）。這就是磁碟需求這麼高的原因：每個節點都有一顆 320 GiB 的虛擬磁碟（不論磁碟多大，Harvester 的 Elemental 安裝程式都會切出固定 150 GiB 的 persistent 分割區，剩下的空間給 Longhorn 自己的分割區）。

---

## 部署 { #deploy }

```bash
rodeo up --profile test           # 兩節點，最快
rodeo up --profile harvester-ha   # 三節點 HA，不含 Rancher
rodeo up --profile harvester      # 完整 lab
```

`rodeo up` 會檢查主機、安裝缺少的套件（會先徵求你同意）、產生帳密，並執行完整的 pipeline。它會自動以 sudo 提權。

**斷線保護（tmux）：** `rodeo up` 在開始部署前，會自動把自己包進一個具名的 tmux session。如果你的 SSH 或 Instruqt 連線在部署途中斷掉，部署行程會繼續執行。隨時可以接回來：

```bash
tmux attach -t rodeo-harvester    # 或 rodeo-test、rodeo-harvester-ha，和 --profile 名稱對應
```

不停止部署、只離開 session：`Ctrl+b  d`。如果你再次執行 `rodeo up` 而 session 已經存在，它會接回正在執行的部署，而不是啟動第二次部署。在腳本裡用 `--no-tmux` 可以略過這個行為。

### 在 AWS 上 { #on-aws }

從你的筆電（macOS 或任何 Linux，請見[安裝](install.md)）：

```bash
aws login
rodeo up --profile harvester --target aws --yes          # 完整 lab
rodeo up --profile virt-workshop --target aws --yes  # workshop lab
```

rodeo 會挑選 region、subnet 與執行個體（`m8id.8xlarge`，32 vCPU / 128 GiB / 本機 NVMe），建立 EC2 主機，並在上面執行同樣的 pipeline。在這種執行個體上，workshop lab 從頭到尾大約花了 25 分鐘。主機有 6 小時的自動銷毀計時（dead-man switch）（用 `provider.ttl_hours` 延長）；用 `rodeo destroy --cloud --yes --config-dir ~/rodeo-labs/<profile>` 拆除。

如果要每位參加者一套 lab，請見 [Fleet](fleet.md)。

---

## 部署期間會發生什麼事 { #what-happens-during-deploy }

`suse-virt` pipeline（四個 Harvester profile 都使用它）會執行這些階段：

1. **kvm_host**：準備 hypervisor：KVM 套件、libvirt daemon、防火牆規則（包括讓 `:8443` 連到 Harvester VIP 的 DNAT 規則），以及映像儲存池
2. **vms**：下載 Harvester ISO、建立虛擬磁碟（大小依 profile 而定，請見上方的主機需求），並寫出 libvirt XML 定義（VM 已定義但尚未啟動）
3. **pxe_server**：在主機的 `virbr0`（192.168.122.1）上設定 nginx + TFTP + dnsmasq。產生一個 `boot.ipxe`，串接到每個節點依 MAC 對應的腳本，並為每個節點產生一份 Harvester 設定 YAML
4. **cluster**：依序啟動 VM（先 `harvester1`，間隔一段時間讓 etcd 就緒，再啟動其餘節點）。等待每個節點透過 iPXE 安裝 Harvester、加入叢集並變成 `Ready`。監看 VIP（192.168.122.10），等叢集收斂
5. **rancher**：（`harvester`、`harvester-2n` 與 workshop profile）在 `rancher` VM 上安裝 K3s + Rancher Prime，透過 NodePort 30002 對外提供服務，設定 admin API 與 Harvester UI extension。預設把 Harvester 匯入 Rancher 留作 lab 練習；在 plan 裡設定 `harvester_auto_import: true` 就會由 rodeo 來做
6. **finalise**：啟用主機重新開機時 VM 自動啟動（在 Instruqt 上會略過；請在 hostimage 開機時使用 `rodeo start-if-needed`，而不是把 finalise 燒進映像）

### 時間估計 { #time-estimates }

在 AWS `m8id.8xlarge`（本機 NVMe）上，完整的 workshop lab 大約花了 25 分鐘，這是 2026 年 9 月與 10 月的量測結果。在磁碟較慢的主機上，請預留更多時間：

| Profile | 在較慢主機上的一般時間 |
|---------|-------------|
| `test` | 45–90 分鐘 |
| `harvester-ha` | 60–120 分鐘 |
| `harvester` | 90–150 分鐘 |

大部分的時間都花在 nested KVM 裡的 Harvester iPXE 安裝。即時查看進度：

```bash
rodeo watch              # 分割畫面的 TUI：階段 + serial 日誌
rodeo logs harvester1    # 只看單一節點的 serial 日誌
```

TUI 會以垂直分割的方式同時顯示所有 VM 的 serial console。左側面板有整個部署的總經過時間計時器。每個 console 視窗會顯示該 VM 自己的經過時間計時器，從該節點收到第一筆 serial 輸出時開始計時，方便找出較晚啟動或停止回應的節點。

---

## 登入 { #log-in }

部署完成後，`rodeo up` 會印出網址與帳密。要再看一次：

```bash
rodeo status
```

- **Harvester UI：** `https://<host>:8443`
- **使用者名稱：** `admin`
- **密碼：** `~/.rodeo/secrets.yaml` 裡 `harvester_admin_password` 的值
- **Rancher UI**（含 Rancher 的 profile）：`https://<host>:30002`，密碼為 `rancher_admin_password`

如果叢集還沒收斂，Harvester 的 bootstrap 流程會在第一次登入時要求輸入 VIP。如果 UI 要求 VIP，請使用 `192.168.122.10`。

> **Instruqt lab：** 把 Harvester 叢集匯入 Rancher 是第一個 lab 挑戰。基礎架構已經準備好；學員透過 Rancher UI > Virtualization Management > Import Existing 完成匯入。

### 下載 kubeconfig { #download-the-kubeconfig }

從 Harvester UI：Support → Download KubeConfig。

或透過 SSH 通道連到 VIP：

```bash
ssh -i ~/.ssh/your-key -N -L 6443:192.168.122.10:6443 user@<host>
# 然後在另一個終端機：
kubectl --kubeconfig ~/.rodeo/harvester-kubeconfig get nodes
```

部署完成後，主機上的 `/root/.rodeo/harvester-kubeconfig` 也有這份 kubeconfig。

---

## Day-2 維運 { #day-2-operations }

| 工作 | 指令 |
|------|---------|
| 檢查叢集與 VM 健康狀態 | `rodeo status` |
| 查看即時日誌 | `rodeo watch` |
| SSH 進入節點 | `rodeo ssh harvester1` |
| 持續查看 serial 日誌 | `rodeo logs harvester2` |
| 重新啟動單一節點 | `rodeo restart harvester1` |
| 正常停止（保留 VM） | `rodeo stop --all --yes` |
| 停止後啟動 | `rodeo start --all --yes` |
| 銷毀 lab | `rodeo clean --yes` |
| 完整重設主機 | `rodeo clean --all --yes --secrets` |
| Support bundle | `rodeo logs --bundle -o rodeo-bundle.tar.gz` |
| 更換管理者密碼 | `rodeo set-password` |
| 安裝／同步 Rancher UI extension | `rodeo install-extensions` |

### 接續失敗的部署 { #resume-a-failed-deploy }

```bash
rodeo status                      # 查看哪個階段失敗
rodeo deploy --from cluster       # 從那個階段接續
```

部署 pipeline 在同一個階段內可以重複執行。如果 `cluster` 階段在等待節點時逾時，接續之前先查看 serial 日誌（`rodeo logs harvester1`）找出根本原因。

### Rancher UI extension { #rancher-ui-extensions }

extension（例如 SUSE Virtualization / Harvester extension）只要在 `definition.yaml` 的
`rancher.ui_extensions` 下宣告一次，格式請見[完整欄位
參考](reference/definition.md#rancherui_extensions)。每次
部署都會自動同步宣告的清單；如果要在已部署的 lab 上安裝或升級某個 extension，
而不重新部署，執行：

```bash
rodeo install-extensions --yes
```

它會透過 SSH/API 直接和正在執行的 Rancher Prime 溝通，所以對運作中的叢集執行
也很安全。先確認實際安裝了什麼：

```bash
rodeo ssh rancher
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl -n cattle-ui-plugin-system get uiplugins.catalog.cattle.io harvester \
  -o jsonpath='{.spec.plugin.version}'
```

結果是空的（或 `NotFound`）表示還沒安裝；版本字串
和 `definition.yaml` 不符，表示該同步了。

---

## Instruqt 工作流程 { #instruqt-workflow }

這是建立內含預先部署好 Harvester 叢集的 **Instruqt hostimage** 的工作流程。完整說明：[Instruqt 範例](examples/instruqt.md)。

1. 在 `rodeo-plan.yaml` 設定 `deployment_target: instruqt`（會略過 `finalise`，請維持這樣）
2. 部署 lab（`rodeo up --profile harvester --yes` 或 `rodeo deploy`）
3. 驗證：`rodeo status`
4. **儲存之前：** **不要**執行 `rodeo deploy --finalise`；確認 agent port `15778`/`15779`，以及 `libvirt-guests` 已停用（成功畫面會印出這份檢查清單）
5. 在 Instruqt UI 儲存 hostimage
6. 把 **`rodeo start-if-needed`** 接進 track 的 setup 腳本，讓每位參加者開機時都會啟動 VM，並重新套用 DNAT/nft

不要把 `finalise` 燒進 hostimage：nested VM 自動啟動會和 Instruqt agent 搶時序，可能讓 console 停在 **Please Wait**。

---

## 故障排除 { #troubleshooting }

### cluster 階段之後連不到 Harvester VIP { #harvester-vip-not-reachable-after-cluster-phase }

cluster 階段會等待 VIP（`192.168.122.10`）在 port 443 回應。如果逾時：

```bash
rodeo logs harvester1    # 檢查安裝日誌，是不是還在安裝？
rodeo ssh harvester1     # 如果節點已經起來，檢查：kubectl get nodes
```

常見原因：磁碟太小（Harvester 的 Elemental 安裝程式一律會切出固定 150 GiB 的 persistent 分割區，磁碟太小會讓它空間不足，導致 containerd 出現「no space」錯誤）、沒有啟用 nested virt、RAM 不足。

### UI 連得到，但 host:8443 不通 { #ui-reachable-but-host8443-not-working }

從 `host:8443` 到 Harvester VIP 的 DNAT 會經過 libvirt 的 nftables 防火牆。如果在 `virsh net-destroy`／重新啟動之後 port forwarding 壞掉：

```bash
sudo /etc/libvirt/hooks/network default started   # 重新套用 DNAT 規則
```

這個 hook 由 `kvm_host` 階段安裝。libvirt 重新啟動 default 網路時，它會自動重新執行。

### 節點一直沒有加入叢集 { #node-never-joins-the-cluster }

查看卡住節點的 serial 日誌。常見問題：

- **iPXE 設定回傳 HTTP 403**：nginx 讀不到設定檔（權限問題）。重新執行 `rodeo deploy --from pxe_server`。
- **設定裡的網卡名稱錯誤**：Harvester 設定用 `hwAddr`（MAC）識別網路介面，而不是 `eth0`。這會自動處理：如果你看到網卡錯誤，檢查 `definition.yaml` 裡的 MAC 和 VM 是否相符。
- **加入時 etcd 逾時**：啟動 harvester1 與啟動其他節點之間有內建的間隔（預設 60 秒）。如果 etcd 還是逾時，可能是第一個節點還沒裝完，請檢查它的日誌。

### etcd 復原（最後手段） { #etcd-recovery-last-resort }

如果某個 etcd 成員永久損壞（例如不小心銷毀了它的 VM），復原分成三個步驟：

```bash
# 1. 從健康的節點移除損壞的成員
crictl exec <etcd-container-id> etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  member remove <member-id>

# 2. 在損壞的節點上清除 etcd 資料，並加上重新加入的設定
rm -rf /var/lib/rancher/rke2/server/db/etcd
cat > /etc/rancher/rke2/config.yaml.d/00-rejoin.yaml <<EOF
server: https://192.168.122.10:9345
token: <cluster-token>
EOF

# 3. 在損壞的節點上重新啟動 rke2-server
systemctl restart rke2-server
```

叢集 token 是 `~/.rodeo/secrets.yaml` 裡的 `harvester_token`。

### 不知道管理者密碼（不在 secrets.yaml、不是保存的檔案裡那組、也不是 admin/admin） { #admin-password-unknown-not-in-secretsyaml-not-the-persisted-file-not-adminadmin }

`rodeo set-password` 會嘗試已知的候選密碼來找回管理者密碼：
`secrets.yaml` 裡設定的值、它上次保存在主機上的值，以及
`admin`/`admin` 的 bootstrap 密碼。如果這些都不符（例如有人在
rodeo-cli 之外手動改了密碼又忘記），它就沒有任何可以*用來*登入的密碼，因此
無法把密碼往下更新。這時請改用 `kubectl` 直接復原：這和 Rancher 針對「被鎖在外面、
不知道目前密碼」所記載的技巧相同，而且在這裡也適用，因為 Harvester 的 dashboard 內嵌了同一個
`users.management.cattle.io` 使用者管理 CRD（已在實機確認：`cattle-system`
執行完整的 3 副本 `rancher` deployment，作為 Harvester UI/API 的前端）。

```bash
# 1. 找出管理者使用者的物件名稱
export KUBECONFIG=/root/.rodeo/harvester-kubeconfig
kubectl get users.management.cattle.io \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.username}{"\n"}{end}'
# -> 例如 user-4wwtk admin

# 2. 產生新密碼的 bcrypt 雜湊（Go 的 bcrypt 對 $2a$/$2b$/$2y$ 一視同仁）
python3 -m pip install --quiet bcrypt
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YourNewPassword123', bcrypt.gensalt(10)).decode())"

# 3. 直接 patch 使用者物件：一次設定密碼雜湊並清除
#    「必須變更密碼」旗標
kubectl patch users.management.cattle.io <user-object-name> --type=merge \
  -p '{"password":"<hash-from-step-2>","mustChangePassword":false}'

# 4. 重新啟動 Rancher，讓 3 個副本都丟掉記憶體裡快取的
#    舊雜湊：不這樣做的話，kube-vip 會把你的登入嘗試輪流分配到各個
#    副本，即使 patch 已經順利寫進 etcd，你還是會間歇看到 401。只會動到 cattle-system：不影響 VM／儲存／網路。
kubectl -n cattle-system rollout restart deployment/rancher
kubectl -n cattle-system rollout status deployment/rancher --timeout=180s
```

驗證之後，把 `secrets.yaml` 與保存候選密碼的檔案同步回來，
下次更換密碼時才不會再發生：

```bash
curl -sk -X POST https://192.168.122.10/v3-public/localProviders/local?action=login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YourNewPassword123"}'
# -> 回傳的 JSON 內容有 "token" 欄位，就表示成功了

# 把 ~/.rodeo/secrets.yaml 的 harvester_admin_password 更新成相同的值，然後：
echo -n 'YourNewPassword123' > /root/harvester-password
chmod 600 /root/harvester-password
```

如果手邊有 `htpasswd`（`apache2-utils` 的一部分）而沒有 Python，`htpasswd
-bnBC 10 "" 'YourNewPassword123' | tr -d ':\n'` 可以產生等效的雜湊。

---

## 自訂 { #customize }

### 變更節點資源 { #change-node-resources }

編輯 `~/.rodeo/profiles/<profile>/` 裡的 profile 檔案，或在部署時覆寫：

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
rodeo deploy -P resources.harvester.vcpu=10
```

### 變更磁碟大小 { #change-the-disk-size }

預設每個 Harvester 節點 320 GiB。不要低於約 250：不論磁碟多大，Harvester 的 Elemental 安裝程式一律會切出固定 150 GiB 的 persistent 分割區，磁碟太小會讓它空間不足、無法拉取容器映像，也讓 Longhorn 本身沒剩多少空間。

```bash
rodeo deploy -P resources.harvester.disk_gb=400
```

### 建立自訂的 Harvester lab { #build-a-custom-harvester-lab }

```bash
rodeo new edge-lab --from harvester-ha    # 以三節點 HA 為基礎產生
$EDITOR ~/.rodeo/profiles/edge-lab/definition.yaml
rodeo up --profile edge-lab
```

完整格式參考：[建立自己的 rodeo](custom-rodeos.md)。
