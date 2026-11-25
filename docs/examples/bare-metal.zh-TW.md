# 範例：裸機部署

這個範例逐步說明如何在裸機 Linux 主機上部署完整的 `harvester` profile（三節點 Harvester HCI + Rancher Prime）。同樣的步驟也適用於已啟用 nested virtualization 的大型雲端 VM。

## 主機需求 { #host-requirements }

| 資源 | 最低需求 |
|----------|---------|
| 作業系統 | SLES 16 或 Leap 16（其他 Linux 只能搭配 `RODEO_ALLOW_ANY_KVM_HOST=1`，不受支援） |
| RAM | 64 GiB（3 × 16 GiB Harvester + 8 GiB Rancher + 主機本身的開銷） |
| CPU | 可分配 28 個 vCPU |
| 磁碟 | `/var/lib/libvirt/images` 需有 900 GiB 可用空間（或一顆專用的第二顆磁碟） |
| KVM | 有 `/dev/kvm`；裸機不需要 nested virt |

## 步驟 1：安裝 rodeo-cli { #step-1-install-rodeo-cli }

在全新的 SLES 16 / Leap 16 主機上：

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

這會 clone repo、在內部建好 Python 環境，並把 `rodeo` 連結成系統指令。不用啟用 venv，也不用設定 PATH。

如果要另外安裝 KVM 套件、libvirt、ansible-core 與 kubectl：

```bash
rodeo install-deps
```

如果缺少相依套件，`rodeo up`（步驟 3）會自動執行這一步。

## 步驟 2：檢查主機 { #step-2-check-the-host }

```bash
rodeo doctor
```

主機準備就緒時的預期輸出：

```
Host facts
  ✓  root
  ✓  /dev/kvm
  ✓  nested virt        (not required on bare metal, shown as warning if absent)
  ✓  RAM     120 GiB total, 100 GiB available
  ✓  disk    900 GiB free in /var/lib/libvirt/images
  ✓  ansible-playbook
  ✓  ansible-galaxy
  ✓  kubectl
  ✓  python: libvirt
  ✓  python: lxml

Recommended profile: harvester (60 GiB needed, 100 GiB available)
```

## 步驟 3：部署 { #step-3-deploy }

```bash
rodeo up --profile harvester
```

`rodeo up` 會：
1. 啟動名為 `rodeo-harvester` 的 tmux session，讓部署在斷線後仍能繼續
2. 確認主機檢查通過
3. 產生內含隨機帳密的 `~/.rodeo/secrets.yaml`
4. 把 lab 目錄建立到 `~/rodeo-labs/harvester/`
5. 自動以 sudo 提權並啟動部署 pipeline
6. 完成時印出登入資訊

**如果你的 SSH session 斷線**，接回正在執行的部署：

```bash
tmux attach -t rodeo-harvester
```

不停止部署、只離開 session：`Ctrl+b  d`。

部署執行期間，開第二個 tmux 視窗（`Ctrl+b  c`）查看進度：

```bash
rodeo watch         # 分割畫面的 TUI：階段 + serial 日誌
rodeo logs harvester1   # 或持續查看單一節點
```

總時間：90–150 分鐘（大部分時間花在 nested KVM 上的 Harvester iPXE 安裝；裸機會快很多）。

## 步驟 4：登入 { #step-4-log-in }

`rodeo up` 完成後：

- **Harvester UI：** `https://<host-ip>:8443`
- **Rancher UI：** `https://<host-ip>:30002`
- **使用者名稱：** `admin`
- **密碼：** 顯示在成功訊息裡，或查看 `~/.rodeo/secrets.yaml`

## 使用專用的第二顆磁碟 { #using-a-dedicated-second-disk }

如果你的主機有另一顆資料磁碟（用 `lsblk` 檢查），在 `rodeo-plan.yaml` 裡設定：

```yaml
storage:
  device: /dev/nvme1n1    # 你的資料磁碟：請先用 lsblk 確認
  image_dir: /var/lib/libvirt/images
```

`kvm_host` Ansible role 會在建立 VM 之前準備並掛載它。

## clean 之後重新部署 { #redeploying-after-a-clean }

```bash
rodeo clean --all --yes --secrets   # 銷毀所有東西，清除帳密
rodeo up --profile harvester        # 以新的帳密重新部署
```

## 作業系統與資料分開在不同磁碟的多磁碟主機 { #multi-disk-host-with-separate-os-and-data-disks }

典型的裸機伺服器：作業系統在 `/dev/nvme0n1`，資料在 `/dev/nvme1n1`。

```yaml
# rodeo-plan.yaml
storage:
  device: /dev/nvme1n1
  mount_point: /var/lib/libvirt/images
  image_dir: /var/lib/libvirt/images
  fs_type: xfs
```

如果尚未掛載，`kvm_host` role 會替這個裝置分割、建立 XFS 檔案系統，並掛載到 `image_dir`。

## Day-2 維運 { #day-2-operations }

| 工作 | 指令 |
|------|---------|
| 健康檢查 | `rodeo status` |
| SSH 進入節點 | `rodeo ssh harvester1` |
| 持續查看 serial 日誌 | `rodeo logs harvester2` |
| 正常停止 | `rodeo stop --all --yes` |
| 停止後啟動 | `rodeo start --all --yes` |
| 銷毀 lab | `rodeo clean --yes` |
| 完整重設主機 | `rodeo clean --all --yes --secrets` |
