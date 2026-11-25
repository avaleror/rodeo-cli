# 故障排除手冊

在已部署的 rodeo lab 中診斷問題並復原的快速參考。每一節都先說明如何確認問題，再說明修正方式。

---

## 部署在 pipeline 中途失敗 { #deploy-failed-mid-pipeline }

**確認：** `rodeo status` 顯示有紅色的階段。終端機輸出中有錯誤訊息。

**修正：** 找出失敗的階段，然後接續執行：

```bash
rodeo status                      # 找出最後一個失敗的階段
rodeo deploy --from <phase>       # 從那裡接續執行
```

如果你不確定改了什麼，就強制完整重跑失敗的階段：

```bash
rodeo deploy --from kvm_host --force   # 從 kvm_host 階段開始全部重跑
```

**回顧某次執行：** 每次部署都會把事件附加到
`<lab>/.rodeo/events.jsonl`（權限 0600），每行一個 JSON 物件，已知的
secret 值會被遮蔽：

```bash
jq -r 'select(.kind=="PhaseFailed") | "\(.phase) rc=\(.rc) \(.text)"' .rodeo/events.jsonl
```

Schema v0：`ts`（Unix 時間）、`lab`（lab 的 realpath）、`phase`（第一個階段之前為 null）、
`kind`（`PhaseStarted`、`PhaseSkipped`、`PhaseDone`、
`PhaseFailed`、`LogLine`、`ProgressUpdate`、`DeployComplete`）、`text`（log
行、失敗訊息、略過原因或進度步驟）、`rc`（僅 PhaseFailed）、
`elapsed`（秒數，PhaseDone 與 ProgressUpdate）以及 `v`（0）。檔案到 10 MB 時
會移到 `events.jsonl.1`，取代較舊的那份。rodeo 以 lab 目錄為基準寫入這個檔案，
如果那裡是 symlink 或 hard link 就拒絕寫入；寫入失敗只會記錄一次 log，絕不會中止部署。

---

## Harvester 安裝看起來卡住：卡住還是逾時 { #harvester-install-appears-stuck-stuck-vs-timed-out }

**背景：** `cluster` 階段最多會等 60 分鐘讓 Harvester VIP 起來。在 nested KVM 上這可能要 45–90 分鐘。問題在於安裝是否仍在進行，還是真的卡死了。

**步驟 1：檢查 heartbeat 檔案：**

```bash
cat ~/.rodeo/logs/<lab-name>-heartbeat.txt
```

rodeo 在等待 VIP 與節點就緒期間，每 5 分鐘寫入這個檔案一次。它記錄 VM 狀態與經過時間。如果檔案存在而且時間戳記是最近的（< 6 分鐘前），部署就還活著。如果檔案不見了或太舊，主機上的程序可能已經死掉。

**步驟 2：檢查 VM 狀態：**

```bash
virsh list --all
```

- 全部是 `running` = 安裝進行中（正常）。用 `rodeo logs harvester1` 觀察 kernel／安裝程式的輸出。
- 全部是 `shut off` = 安裝程式提早結束。查看 serial log 中的錯誤：

```bash
rodeo logs harvester1
```

- 有任何 `paused` = 主機記憶體吃緊。釋放 RAM 或加上 swap，然後執行 `virsh resume <vm>`。

**步驟 3：區分安裝卡住與 Instruqt session 逾時：**

Instruqt 會在平台設定的閒置逾時之後終止 GCP lab VM（不只是瀏覽器 session）。如果 lab VM 已經不在，就無法重新連線，安裝也不可能是「卡住」，因為主機已經不存在了。

lab 已被終止（而不是卡住）的跡象：
- `virsh list --all` 失敗，顯示「failed to connect to the hypervisor」
- SSH 連到 Instruqt 主機完全逾時
- heartbeat 檔案不見了，或是上一次執行留下的

安裝真的卡住的跡象：
- VM 是 `running`、heartbeat 是新的，但 serial log 超過 15 分鐘都沒有捲動
- Serial log 顯示重複出現的錯誤（磁碟已滿、找不到網路介面、etcd 選舉迴圈）

**常見的安裝卡住原因：**

| Serial log 中的症狀 | 原因 | 修正 |
|---|---|---|
| `no network interface found` 或卡在網路設定 | 網路介面名稱不符：安裝程式拿到的設定寫的是 `eth0`，但 kernel 使用 `ens3` | 清掉 VM（`rodeo deploy --from vms --force`）後重新部署 |
| 連到設定網址時出現 `curl: (7) Failed to connect` | nginx 沒有執行，或 virbr0 沒有起來 | 檢查 nginx：`rodeo ssh <host> -- sudo systemctl status nginx` |
| 磁碟已滿／`containerd` 錯誤 | VM 磁碟太小（Elemental 的 persistent partition 下限固定為 150 GiB） | 在 plan 中設定 `disk_gb: 320` 後重新部署 |
| `etcd` 選舉迴圈，節點一直無法加入 | 三節點環境中快速加入造成的競爭 | 調高 `definition.yaml` 中的 `etcd_join_gap_seconds`（預設：90） |

---

## Harvester VIP 沒有回應 { #harvester-vip-not-responding }

**症狀：** `cluster` 階段在等待 `192.168.122.10:443` 時逾時。

**確認：**

```bash
rodeo logs harvester1   # 安裝程式還在跑嗎？
```

如果 log 還在前進（你看到 kernel 訊息或 Harvester 安裝程式），就繼續等：在磁碟較慢的主機上，nested KVM 可能要 45–90 分鐘。

如果 log 卡住或是空的：

```bash
rodeo ssh harvester1              # 連到節點
sudo journalctl -u rke2-server -f # 檢查 RKE2 狀態
```

**常見原因與修正：**

| 原因 | 修正 |
|-------|-----|
| 磁碟太小 | 清除後在 plan 中設定 `disk_gb: 320` 重新部署 |
| RAM 不足 | 用 `rodeo doctor` 檢查；釋放 RAM 或改用較小的 profile |
| `pxe_server` 提供了錯誤的設定 | 檢查 nginx log：`rodeo ssh <host> -- sudo journalctl -u nginx` |
| etcd 加入競爭 | 90 秒的 `etcd_join_gap_seconds` 可以避免這個問題；檢查它是否被調低了 |

---

## 主機 port forward 無效（host:8443 / host:30002） { #host-port-forward-not-working-host8443-host30002 }

**症狀：** 從外部連 `https://<host>:8443` 會逾時，但在主機內部連 `https://192.168.122.10:443` 是正常的。

**確認：**

```bash
# 在主機上：
sudo nft list table ip libvirt_network   # 找 guest_input chain
```

如果 `guest_input` chain 有 `reject` 規則，但它前面沒有 `ct status dnat accept` 規則，代表 libvirt hook 不見了或沒有觸發。

**修正：**

```bash
sudo /etc/libvirt/hooks/network default started   # 重新套用 DNAT 規則
```

如果 hook 檔案不見了（例如 `kvm_host` 階段沒有完成之後）：

```bash
rodeo deploy --from kvm_host --force   # 重新安裝 hook
```

---

## secret 無法解析 { #secrets-not-resolved }

**症狀：** 部署立即失敗，顯示 `unresolved credential` 或 `empty password`。

**修正：**

```bash
# 重新產生 secret
rodeo init --force
# 然後重試部署
rodeo deploy --from kvm_host --force
```

或直接編輯 `~/.rodeo/secrets.yaml`，補上缺少的 key。

---

## 「python3-lxml not found」錯誤 { #python3-lxml-not-found-error }

**症狀：** `vms` Ansible 階段失敗，錯誤是缺少 `lxml` 模組。

**修正：**

```bash
sudo rodeo install-deps   # 會連同其他主機套件一起安裝 python3-lxml
```

然後接續執行：

```bash
rodeo deploy --from vms
```

---

## VM 卡在 `paused` 狀態 { #vm-stuck-in-paused-state }

**確認：**

```bash
virsh list --all   # 顯示 STATE = paused
```

**修正：**

```bash
virsh resume <vm-name>
# 或透過 rodeo：
rodeo restart <vm-name>
```

如果它馬上又暫停，請檢查主機 RAM：記憶體壓力到達臨界時，hypervisor 會暫停 VM。

---

## etcd 成員損壞（遺失一個節點） { #etcd-member-broken-one-node-lost }

如果叢集在執行中時，某台 Harvester VM 被強制刪除或它的磁碟損毀，就會發生這種情況。

**從健康的節點確認：**

```bash
rodeo ssh harvester2
# 在 VM 內：
ETCD_CERT=/var/lib/rancher/rke2/server/tls/etcd
crictl exec $(crictl ps --name etcd -q) etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=$ETCD_CERT/server-ca.crt \
  --cert=$ETCD_CERT/server-client.crt \
  --key=$ETCD_CERT/server-client.key \
  member list
```

如果有一個成員顯示 `unstarted` 或不見了：

**復原（3 個步驟）：**

```bash
# 步驟 1：從健康的節點移除損壞的成員
MEMBER_ID=<id from member list>
crictl exec <etcd-container-id> etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=$ETCD_CERT/server-ca.crt \
  --cert=$ETCD_CERT/server-client.crt \
  --key=$ETCD_CERT/server-client.key \
  member remove $MEMBER_ID
```

```bash
# 步驟 2：在損壞的節點上清除 etcd 資料，並加上重新加入的設定
rodeo ssh harvester1   # 或是任何一台損壞的節點
sudo rm -rf /var/lib/rancher/rke2/server/db/etcd
sudo tee /etc/rancher/rke2/config.yaml.d/00-rejoin.yaml <<EOF
server: https://192.168.122.10:9345
token: $(grep harvester_token ~/.rodeo/secrets.yaml | awk '{print $2}')
EOF
```

```bash
# 步驟 3：在損壞的節點上重新啟動 rke2-server
sudo systemctl restart rke2-server
# 等待約 3 分鐘，然後驗證：
kubectl get nodes   # 應該全部都是 Ready
```

---

## libvirt 無法啟動 { #libvirt-not-starting }

**症狀：** `virsh list` 失敗，出現連線錯誤。

**確認：**

```bash
systemctl status virtqemud.socket virtnetworkd.socket
```

SLES 16 使用**模組化 libvirt**（由 socket 啟動的 daemon），而不是單一的 `libvirtd`。如果 socket 沒有啟用：

```bash
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket
```

---

## 主機重開機後 virbr0 不見了 { #virbr0-missing-after-host-reboot }

**症狀：** VM 無法啟動，因為 `virbr0` 不存在。

**原因：** 沒有啟用 `libvirt-guests`（`finalise` 階段被略過或失敗），所以 libvirt 沒有自動啟動。

**修正：**

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

或重跑 finalise：

```bash
rodeo deploy --from finalise --finalise
```

---

## 不小心用 virsh 銷毀了 virbr0 網路 { #accidentally-destroyed-the-virbr0-network-with-virsh }

**症狀：** 在叢集執行中時執行了 `virsh net-destroy default`。所有 VM 的網路連線都中斷了。

**修正（不重新啟動 VM 就恢復連線）：**

```bash
# 重新建立 bridge 並重新接上 tap 裝置
sudo ip link set virbr0 up
for tap in $(virsh domiflist harvester1 | awk '/vnet/{print $1}') \
           $(virsh domiflist harvester2 | awk '/vnet/{print $1}') \
           $(virsh domiflist harvester3 | awk '/vnet/{print $1}'); do
    sudo ip link set $tap master virbr0
    sudo ip link set $tap up
done
sudo ip neigh flush dev virbr0

# 重新啟動 libvirt 網路（重新套用 DNAT 規則與防火牆 hook）
sudo virsh net-start default
sudo /etc/libvirt/hooks/network default started
```

驗證連線：

```bash
ping -c 3 192.168.122.11   # harvester1
ping -c 3 192.168.122.10   # VIP
```

如果 VM 遺失了叢集狀態，你可能需要復原 etcd（請見上方）。

---

## 完整重設主機 { #full-host-reset }

當你想完全重新開始時使用，這會清除所有 lab VM、磁碟、狀態與帳密，但保留套件與 rodeo 執行檔：

```bash
rodeo stop --all --yes 2>/dev/null || true   # 先正常關機
rodeo clean --all --yes --secrets --force-network
```

然後重新部署：

```bash
rodeo up --profile <name>
```

---

## 收集支援套件 { #collect-a-support-bundle }

```bash
rodeo logs --bundle -o rodeo-bundle.tar.gz
```

套件包含：遮蔽過的 plan（secret 以 `***` 取代）、階段狀態、所有 VM 最近的 serial log 結尾，以及 `virsh` domain 資訊。回報 bug 或向維護者升級處理時，請附上這個套件。

---

## 多主機 workshop 失敗 { #multi-host-workshop-failures }

對於 fleet 部署，從工作檔或 `rodeo fleet status` 找出失敗的主機，然後把鑑識資料抓回筆電：

```bash
rodeo fleet diagnose -f workshop.yaml                  # 失敗的主機（預設）
rodeo fleet diagnose -f workshop.yaml --all-selected   # 每台選定的主機
```

產出物的目錄結構請見 [Fleet：Diagnose](fleet.md#diagnose-failure-forensics)。

---

## 「This machine cannot host labs」／「not a supported KVM host」 { #this-machine-cannot-host-labs-not-a-supported-kvm-host }

**確認：** `rodeo up` 或 `rodeo deploy` 停止，並顯示 `macOS cannot host labs` 或 `<distribution> is not a supported KVM host`。

**原因：** lab 只能在 SLES 16 / Leap 16 上執行。其他任何機器都是控制端：它驅動 AWS 上或透過 SSH 連線的主機，但自己絕不執行 lab。

**修正：** 從那台機器部署到雲端，或在 SLES 16 主機上執行 rodeo：

```bash
rodeo up --profile <name> --target aws
```

`RODEO_ALLOW_ANY_KVM_HOST=1` 讓你仍然可以在其他 Linux 上試跑本機 lab，但不受支援。

---

## 安裝失敗，出現 `setuptools>=68` 或其他 pip 錯誤 { #install-fails-with-setuptools68-or-another-pip-error }

**確認：** `install.sh` 在設定 Python 環境時停止，常見於 SLES/Leap 15（Python 3.6）。

**修正：** 重跑目前版本的安裝程式。從 v0.22 開始，它會在這些系統上安裝成控制端，使用它找到的 Python 3.10+，或下載一份私有的 Python，並重建損壞的環境：

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

---

## AWS：開始部署之後晚點再回來看 { #aws-start-a-deploy-and-come-back-later }

`rodeo up --target aws --detach` 會在主機開始執行 `rodeo up` 後立即返回。它會印出一行 JSON（`pid`、`log`、`host`），部署則在主機上繼續執行。在 lab 目錄中查看進度：

```bash
rodeo status --cloud            # running / exited N / gone / none，再加上最後 40 行 log
rodeo status --cloud --lines 200 --output json
```

`gone` 表示記錄的程序已經死掉，而且沒有結束碼（被終止，或主機重開機）。在已有一個執行中時再執行一次 `rodeo up --target aws`，會以結束碼 75 停止。沒有 `--detach` 時，`rodeo up --target aws` 會像以前一樣等待並追蹤 log。

---

## AWS：登入、region 與容量錯誤 { #aws-login-region-and-capacity-errors }

| 訊息 | 修正 |
|---------|-----|
| `session has expired` / `CreateOAuth2Token` | 再執行一次 `aws login`。指令執行中途出現暫時性的 `CreateOAuth2Token` 錯誤，也可能只需要重跑一次。 |
| `MissingDependencyException` | 缺少 `[aws]` extra（`boto3[crt]`）。重跑 `install.sh`，它會在控制端上安裝這個 extra。 |
| `no default VPC in <region>` | 該 region 沒有預設 VPC。把 `RODEO_AWS_REGION` export 成有預設 VPC 的 region，或在 plan 中設定 `provider.region` 與 `provider.subnet_id`。 |
| `not offered in region` / `not enough capacity` | 換一個 region 或 `--instance-tier`，或固定 `provider.instance_type`。rodeo 絕不會自己降低規格。 |
| `has no public IP` / 沒有網際網路路由 | 該 subnet 沒有路由到 internet gateway。請選擇 public subnet。 |

---

## AWS：lab 連不到或主機不見了 { #aws-lab-unreachable-or-host-gone }

**從筆電連 UI 或 SSH 逾時。** rodeo 的 security group 只允許你在建立主機時的 public IP。如果你的 IP 變了（換了網路、VPN），在 lab 目錄中重跑 `rodeo up --target aws`：它會把規則改成你目前的 IP，並略過已完成的階段。

**主機在 session 中途消失。** 是自動銷毀計時（dead-man switch）把它終止了：每台雲端主機都會在啟動後經過 `provider.ttl_hours`（預設 6）到期，這會在 `rodeo up` 結束時以 `Self-destructs` 顯示，也記錄在 `rodeo-expires-at` 執行個體 tag 中。重新部署，並在為長時間 session 建立主機之前，設定更長的 `provider.ttl_hours`。

**還有東西在計費嗎？** 用 `rodeo destroy --cloud --yes --config-dir <lab dir>` 拆除（fleet 則用 `rodeo fleet deprovision`）。兩者也會移除被終止主機在本機留下的東西（host key、學員 key、`~/.rodeo/fleet/<name>/`），所以重用 IP 的新執行個體絕不會撞到過期的 host key。要檢查每個 region 是否有殘留：

```bash
for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
  aws ec2 describe-instances --region "$r" \
    --filters Name=tag:ManagedBy,Values=rodeo Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output text
done
```

---

## `sudo rodeo`／`sudo ssh -i /root/.ssh/id_ed25519 ...` 顯示「command not found」 { #sudo-rodeo-sudo-ssh-i-rootsshid_ed25519-says-command-not-found }

SUSE 預設的 `/usr/etc/sudoers` 內含兩行互相衝突的 `Defaults secure_path=`；
第二行（範圍較窄，缺少 `/usr/local/*`）會生效，所以 `sudo` 找不到
`rodeo`（安裝在 `/usr/local/bin`），即使不加
`sudo` 時一切正常。從全新的 `rodeo deploy` 開始，`kvm_host` 階段會在 SUSE 主機上自動修正這個問題（請見
`/etc/sudoers.d/rodeo-secure-path`）。

如果你在已經有 VM 在執行的主機上遇到這個問題，不要只為了這件事重跑 `--from
kvm_host`：在運作中的 lab 上，那個階段也會重新動到 firewalld
與 libvirt 網路，可能會和已經起來的 domain 衝突。
改為直接套用一次性的修正：

```bash
echo 'Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
  | sudo tee /etc/sudoers.d/rodeo-secure-path >/dev/null
sudo visudo -c   # 在信任它之前，先確認語法可以解析
```
