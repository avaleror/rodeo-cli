# 一般 KVM 主機（kvm-hosts）

`kvm-hosts` 只給你 KVM 主機，其他什麼都沒有。rodeo 會安裝 KVM 與 libvirt、一個 NAT 網路（`default`，192.168.122.0/24）與防火牆，然後就停下來。VM 由你自己決定：你正在開發或測試的 VM 管理工具、腳本，或是手動用 `virsh`。

搭配 `rodeo fleet`，你可以拿到好幾台彼此互通的主機，也就是 AWS 上的一個小型 KVM 叢集。

## 用一週的三主機叢集 { #a-3-host-cluster-for-a-week }

```bash
rodeo fleet up --profile kvm-hosts --count 3 --no-deadman
# ... 工作需要多久，就用這些主機多久 ...
rodeo fleet destroy -f workshop.yaml --yes
```

`fleet up` 會寫出 `workshop.yaml`、建立 3 台 `m7i.2xlarge` 主機（8 vCPU / 32 GiB，每台放得下 3 到 4 台小型測試 VM）、完成準備，並印出 SSH 存取方式。隨時可以用 `rodeo fleet access -f workshop.yaml` 再印一次。

`fleet destroy`（和 `fleet deprovision` 是同一個指令）會終止這些主機，上面所有 VM 也會跟著消失，security group 也會被移除。

## 和 workshop 有什麼不同 { #what-is-different-from-a-workshop }

| | Workshop profile | kvm-hosts |
|---|---|---|
| 學員入口 | 預設開啟 | 關閉 |
| 存取 | `student_access: open` | 只有操作者 |
| 主機之間的流量 | 封鎖 | 允許（`provider.host_to_host: true`） |
| 自動銷毀計時（dead-man switch） | 一律開啟，最長 168 小時 | 預設開啟，`--no-deadman` 可關閉 |

`provider.host_to_host: true` 會加上一條 security group 規則，讓 workshop 自己的 security group 成員之間可以透過任何 port 互連。group 以外的任何東西都不符合這條規則。

## 不用 dead-man switch 執行 { #running-without-a-dead-man-switch }

`--no-deadman`（或在 `workshop.yaml` 設 `provider.ttl_hours: off`）只接受用 `rodeo fleet` 部署的 `kvm-hosts` lab。其他所有 lab，以及 `rodeo up --target aws`，都會拒絕這個設定。

沒有這個計時時：

- 主機會被加上 `rodeo-expires-at: never` 標籤，`fleet provision` 會印出警告，
- 在你執行 `rodeo fleet destroy` 之前都會持續計費，
- 從主機內部執行 `poweroff` 只會讓它停止，所以 lab 不會因此消失。晚上從 AWS 主控台停止主機可以省下運算費用，但磁碟仍會計費。

如果你只需要幾天，就保留這個計時、改成延長它：`--ttl-hours 168` 可以用一週，而且忘記收的 lab 仍然會被清掉。

## 你自己的步驟 { #your-own-steps }

用 `rodeo new mylab --from kvm-hosts` 以這個 profile 為基礎建立你自己的 profile，並在它的 `custom/scripts/` 放入編號的腳本，在每台主機上安裝你的軟體。這些腳本會在主機準備好之後、於每次 `rodeo up` 時執行，所以要寫成可重複執行。如果是 fleet，把這個 profile 推到 git repo，再讓 `workshop.yaml` 裡的 `lab.source` 指向它（請見 [Fleet](fleet.md)）。
