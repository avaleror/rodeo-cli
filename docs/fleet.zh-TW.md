# Fleet：多主機 workshop 編排

在筆電端執行的控制平面，透過 OpenSSH 驅動**多台遠端 KVM 主機**。
每台主機仍然執行一般的單機 `rodeo`；fleet 只負責分派與追蹤工作。
Engine 階段、Ansible role 與 nested 網路都沒有改變。

另請參閱：[開始使用](get-started.md)（單機）、[架構](architecture.md)、
[ROADMAP.md](https://github.com/avaleror/rodeo-cli/blob/main/ROADMAP.md)（Phase I）。

## 各階段功能 { #capabilities-by-phase }

| 階段 | 狀態 | 內容 | 指令 |
|-------|--------|------|----------|
| **F0** | 已推出 | 機器可讀的主機 CLI | `rodeo doctor --output json`, `rodeo status --output json` |
| **F1** | 已推出 | 清單 + 唯讀分派 | `rodeo fleet doctor`, `rodeo fleet status` |
| **F2** | 已推出 | 部署／重試／存取表 | `rodeo fleet deploy`, `retry`, `access` |
| **F2.2** | 已推出 | 一個指令跑完整個流程 | `rodeo fleet up` |
| **F2.1** | 已推出 | 失敗鑑識 | `rodeo fleet diagnose` |
| **F4a** | 已推出 | AWS 取得主機，每台主機有 6 小時自動銷毀計時（dead-man switch） | `rodeo fleet provision`, `deprovision` |
| **F4b–d** | 規劃中 | GCP → Vultr BM → Hetzner | （規劃中） |
| **F5** | 已推出（AWS，v0.18） | 學員領取入口（claim portal）：每位學員輸入 workshop 代碼，就能拿到自己的 lab；學員 SSH 走獨立的 port | `rodeo fleet open-access`, `rodeo fleet portal ...`（[設計](claim-portal.md)、[計畫](claim-portal-plan.md)） |

主機前置條件（在每台 lab 機器上執行 [`install.sh`](https://github.com/avaleror/rodeo-cli/blob/main/install.sh) 之後）：

```bash
rodeo doctor --output json
# 在 lab 目錄中執行：
rodeo status --output json
```

---

## 需要操作者 secret 或其他 UI 的 lab { #labs-with-operator-secrets-or-other-uis }

有些 lab 需要只有你才有的值（lab 的 `operator_secrets`，例如 SCC
regcode 或預先建好的映像網址）。把它們列在 `workshop.yaml` 裡：`fleet deploy`
會在 `rodeo up` 之前，只把這些 key 從你的 `~/.rodeo/secrets.yaml` 複製到每台主機（透過 SSH
stdin，權限 0600）。只要缺少任何一個，它就會停下來，不會動到任何主機。`ui_ports` 會在 `fleet access` 加上欄位：

```yaml
lab:
  profile: smlm-workshop
  dir: /root/rodeo-lab
  components: []                 # 這個 lab 沒有 Harvester/Rancher UI
  ui_ports: {smlm: 443}
  operator_secrets: [smlm_image_url, smlm_image_sha256, smlm_image_admin_pass,
                     sles15sp5_image_url, sles15sp5_image_sha256,
                     sles15sp6_image_url, sles15sp6_image_sha256]
```

## 規劃 { #roadmap }

Fleet **尚未**推出的部分。完整檢查清單：[ROADMAP Phase I](https://github.com/avaleror/rodeo-cli/blob/main/ROADMAP.md#phase-i--fleet--workshop-fan-out)。

### F4：取得主機 { #f4-host-acquire }

**從筆電建立 KVM 主機**，合併進 `workshop.yaml`，然後執行一般的
收斂迴圈。provider 只負責到清單為止；deploy／diagnose／retry 仍然
只走 OpenSSH。

| 順序 | Provider | 狀態 |
|-------|----------|--------|
| **F4a** | **AWS**（`boto3`，`[aws]` extra：請見[安裝](install.md)） | **MVP 已推出**：依 tag 建立／重用、等待 running + SSH、寫入 `hosts[]`、只終止有 tag 的執行個體 |
| **F4b** | **GCP**（`google-cloud-compute`） | 規劃中 |
| **F4c** | **Vultr Bare Metal**（`[vultr]` extra） | 規劃中：排在 GCP 之後；用實體機跑 nested KVM |
| **F4d** | **Hetzner Cloud**（`hcloud`） | 規劃中：排在 Vultr 之後；nested KVM 必須先驗證 |

**不在範圍內：** Equinix Metal（服務已終止）。跨主機共用 secret。
為多主機修改 nested 階段 engine。

**MVP 缺口（AWS）：** 還沒有 `plan` dry-run；自動 SG 之後再做。

**本機清理。** `deprovision` 也會移除筆電為每台被終止的
主機保留的東西：`workshop.yaml` 中它的 `hosts[]` 項目、
`~/.rodeo/fleet/<workshop>/known_hosts` 中它的 host key，以及它的學員 SSH key。當主機都沒了
而且 portal 也已移除時，`~/.rodeo/fleet/<workshop>/` 與
`workshop.job.yaml` 也會一併刪除。BYO 主機、終止失敗的主機、`workshop.yaml`
的其他內容、plan、`secrets.yaml` 以及受管理的 SSH key 都不會被動到。`--json` 會在 `local_cleanup` 底下列出移除了哪些東西。

```bash
pip install -e '.[aws]'        # 只需一次，在你的 rodeo-cli checkout 中執行（請見 install.md）
# AWS API 帳密（boto3：絕不放進 YAML）。二擇一：
#   ~/.aws/credentials  （+ 可選的 AWS_PROFILE / ~/.aws/config）
#   或 AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY（+ 可選的 AWS_SESSION_TOKEN）
# SSH：每個 workshop 都有自己的 key（~/.rodeo/ssh/labs/<workshop>/），會隨 workshop 一起刪除
rodeo fleet provision -f workshop.yaml    # 建立／重用 → 寫入 hosts[]
rodeo fleet deploy -f workshop.yaml       # 在每台主機安裝 rodeo，啟動 lab
rodeo fleet doctor -f workshop.yaml       # 主機上需要有 rodeo，所以要在 deploy 之後
rodeo fleet deprovision -f workshop.yaml --yes
```

共用的 `HostProvider` Protocol 與 `provider:` YAML schema：
請見下方的[取得主機設計](#f4-host-acquire-design)。

### 相關功能（不限 Fleet） { #related-not-fleet-only }

單機的 `deployment_target: aws` 與 fleet 建立主機共用同一個 `rodeo/providers/aws`
adapter 與 `provider:` YAML schema。
在 EC2 guest 上，lab 仍然以 `baremetal` 執行。

實機冒煙測試檢查清單：

- [AWS 單機 + fleet](examples/aws-fleet-claude-test-plan.md)
- [測試：AWS 實機冒煙測試](examples/testing.md#aws-live-smoke)

---

## F0：單機上的 JSON { #f0-json-on-a-single-host }

結構化報告放在 `rodeo/service/`，讓 CLI 與 fleet 共用同一種格式。

```bash
rodeo doctor --output json
rodeo status --output json   # 需要有 lab（目前目錄或 --config-dir）
```

預設的 `--output text` 保留原本的 Rich 表格。

---

## 清單（`workshop.yaml`） { #inventory-workshopyaml }

```yaml
name: suse-virt-rodeo-emea
lab:
  dir: /root/suse-virt-workshop     # 遠端路徑（status + deploy 的工作目錄）
  # F2：以下二擇一：
  source: git:https://github.com/avaleror/suse-virt-workshop.git
  # profile: harvester              # 另一種做法：植入內建／自訂 profile
  branch: main                      # 可選（僅限 git）
  target: baremetal                 # baremetal | instruqt（AWS 主機請用 baremetal）
  concurrency: 4                    # deploy/retry 預設的 -j
  ports:
    harvester: 8443                 # 在主機 public IP 上做 DNAT
    rancher: 30002
  # components: [harvester]         # 可選：請見下方的「存取表」。
  #                                  # 省略的話，會顯示 fleet 能組出的所有網址。
  # ref: main                        # 主機要執行的 rodeo-cli git ref
  # install_url: https://…           # install.sh 的 fork 或離線鏡像
defaults:
  ssh_user: ec2-user                 # AMI 使用者（ec2-user / sles / root）；非 root 時透過 sudo -n 執行
  # identity_file: 可選；預設為 workshop key（雲端）或 ~/.rodeo/ssh/id_ed25519
hosts:
  - id: student-01
    ssh: 203.0.113.11               # host 或 user@host
    public_ip: 203.0.113.11         # 供 fleet access 使用
    labels: { room: a }
  - id: student-02
    ssh: root@lab-02.example
    public_ip: 203.0.113.12
    labels: { room: b }
```

驗證採 fail-closed：`id` 不可重複、`ssh` 必填、`lab.dir` 必填。
`lab.source` 或 `lab.profile` 只有在 **deploy**／**retry** 時才是必填。

---

## F1：doctor 與 status { #f1-doctor-and-status }

```bash
rodeo fleet doctor -f workshop.yaml
rodeo fleet status -f workshop.yaml --output json

rodeo fleet doctor -f workshop.yaml --label room=a
rodeo fleet status -f workshop.yaml --host student-01 -j 4
```

- 只有在每台選定的主機都成功時，結束碼才是 `0`。
- 兩者都會在主機上執行 `rodeo`，所以在裝好之前都會以 `rodeo: command not found` 失敗。
  對於由 `rodeo fleet provision` 建立的主機，安裝是在
  `rodeo fleet deploy` 中進行，所以要在 deploy 之後才跑 doctor。對於已經執行過
  `install.sh` 的 BYO 主機，doctor 在 deploy 之前也能用。
- **doctor：** 只看遠端程序的結束碼還不夠，fleet 也會檢查 KVM、nested
  virt、核心工具，以及內建 profile 是否放得進 RAM
  （`rodeo/fleet/doctor.py::_readiness_problems`）。
- **status：** 在每台主機的 `lab.dir` 中執行。如果有 `workshop.job.yaml`，
  status 會更新每台主機的工作狀態，供之後重試使用。

---

## 一個指令：`rodeo fleet up` { #one-command-rodeo-fleet-up }

`rodeo fleet up` 會依序執行下方整套講師流程：`provision`、
`deploy`、`portal up`、等到每個 lab 都完成、`open-access`、
`portal publish` 與 `portal info`（沒有 portal 時則是 `access`）。

```bash
# 新的 workshop：寫入 workshop.yaml，然後部署
rodeo fleet up --profile virt-workshop --count 3 \
  --guide-url https://avaleror.github.io/suse-virt-workshop/

# 既有的清單，或接續一次中斷的執行
rodeo fleet up -f workshop.yaml
```

加上 `--profile` 與 `--count` 時，它會寫入 `-f`（預設為 `workshop.yaml`，這個檔案
必須還不存在）：AWS 位於 `--region`（預設為 `RODEO_AWS_REGION`，否則為
eu-north-1）、使用 profile 的 `--instance-tier`（預設 `recommended`），除非
指定了 `--instance-type`、使用該 region 預設 VPC 的 public subnet，除非指定了
`--subnet-id`、`--ttl-hours`（預設 6），以及領取入口，除非加上
`--no-portal`。之後每一個 `rodeo fleet` 指令都使用同一個檔案。

它最多等待 `--wait-minutes`（預設 90），遇到第一個失敗的 lab 就停下來，
並列出要執行的 `diagnose`／`retry --failed-only` 指令。再次執行
`rodeo fleet up -f workshop.yaml` 就會接續：主機會被重用，已完成的
lab 會被略過。用 `rodeo fleet deprovision -f workshop.yaml --yes` 拆除。

## F2：deploy、retry、access { #f2-deploy-retry-access }

### 講師流程 { #instructor-flow }

```bash
rodeo fleet deploy -f workshop.yaml -j 4
rodeo fleet doctor -f workshop.yaml -j 8     # 在 deploy 之後：主機上需要有 rodeo
rodeo fleet status -f workshop.yaml          # 反覆查詢，直到各階段完成
rodeo fleet diagnose -f workshop.yaml        # 抓取失敗主機的 log
rodeo fleet retry -f workshop.yaml --failed-only
rodeo fleet access -f workshop.yaml --output json
```

### `fleet deploy` 在每台主機上做了什麼 { #what-fleet-deploy-does-on-each-host }

1. 確保 `rodeo` 在 PATH 上（缺少時執行 `install.sh`，指定 ref 時則每次都執行；
   請見[主機執行哪一版 rodeo-cli](#which-rodeo-cli-the-hosts-run)）。
2. 同步 lab：`git clone`／`git pull --ff-only`，若是 profile 則執行 `rodeo up --no-deploy`。
3. 啟動**背景執行（detached）的 tmux**，在 `lab.dir` 中執行 `rodeo up --yes --no-tmux`
   （session 名稱為 `rodeo-fleet-<workshop>-<host-id>`）。
4. 立即返回：**不會**等待 90–150 分鐘的安裝。
5. 在清單旁邊寫入 **`workshop.job.yaml`**（chmod 600）。

**可快取**的階段已經全部 `completed` 的主機會被**略過**，
除非加上 `--force`。`apply` 階段永遠不會被快取（每次本機部署都會重跑），
也不會擋住這項檢查。

secret：由遠端 `rodeo up` **為每台主機**各自產生，fleet 絕不會 scp 一份共用的
`secrets.yaml`。

### 工作檔 { #job-file }

```yaml
workshop: suse-virt-rodeo-emea
hosts:
  student-01:
    state: running    # pending | running | ok | failed
    tmux: rodeo-fleet-suse-virt-rodeo-emea-student-01
  student-02:
    state: failed
    last_error: "..."
```

### 重試 { #retry }

```bash
rodeo fleet retry -f workshop.yaml --failed-only   # 預設
rodeo fleet retry -f workshop.yaml --all-selected  # 忽略工作失敗紀錄；搭配 --host/--label 使用
```

先從即時的 `status` 更新工作狀態，再對選定的主機以 `--force`
重新啟動部署。

### 主機執行哪一版 rodeo-cli { #which-rodeo-cli-the-hosts-run }

主機會從 GitHub 自行安裝：你本機的工作目錄永遠不會傳到
主機上。預設情況下，安裝**只在缺少 `rodeo` 的主機上**執行，所以主機
會一直保留第一次安裝時的程式碼；`--force` 重跑的是*部署*，不是
安裝。

對於鎖定在某個 release 的 workshop，這樣沒問題；但如果你剛
push 了一個修正，就不對了：整個 fleet 的部署會悄悄地在每台主機上同時跑舊的程式碼。
指定一個 ref 來強制更新：

```bash
rodeo fleet deploy -f workshop.yaml --ref main         # 每台主機都用 main 的最新版
rodeo fleet retry  -f workshop.yaml --ref feat/my-fix  # 用修正版重跑失敗的主機
```

指定 ref 時，安裝每次都會執行，`install.sh --ref` 會把每台
主機的 checkout 強制重設（hard reset）到那個 ref。`lab.ref` 在清單中設定同樣的東西；`--ref`
會覆寫它。安裝程式會從它要 checkout 的同一個 ref 抓取，除非
`lab.install_url` 明確指向別處（fork、離線鏡像），這時會
原樣使用該網址，並把 ref 傳給它。無效的 ref 會在載入清單時就失敗，在連到任何主機之前。

這和單機的 `rodeo up --target aws --ref …` 是同一套機制，兩條
路徑共用 `rodeo/install_source.py`。

### Diagnose（失敗鑑識） { #diagnose-failure-forensics }

`fleet status`／工作檔會告訴你是*哪一台*主機、哪個階段失敗。要知道
*原因*，把 log 抓回筆電：

```bash
rodeo fleet diagnose -f workshop.yaml                  # 失敗的主機（預設）
rodeo fleet diagnose -f workshop.yaml --all-selected   # 每台選定的主機
rodeo fleet diagnose -f workshop.yaml -o /tmp/diag --output json
```

每台主機的結果放在 `<inventory>.diagnose-<utc>/<host-id>/`（或 `-o` 指定的位置）下：

| 產出物 | 來源 |
|----------|--------|
| `status.json` | 遠端 `rodeo status --output json` |
| `logs/*.log` | `~/.rodeo/logs/` 的結尾部分（包含 `fleet-up.log`） |
| `meta/state/` | `~/.rodeo/state/*.yaml` 階段快取 |
| `meta/tmux-pane.txt` | 工作 session 還在時擷取的 tmux pane 內容 |
| `summary.json` | 該主機的簡短失敗摘要 |
| `index.json` | 整個 workshop 的索引（使用 `--output json` 時也會印出） |

不會印出或收集 secret。收集成功時結束碼為 `0`；任何主機的 SSH
或封存解壓縮失敗時為 `1`。

### 存取表 { #access-sheet }

```bash
rodeo fleet access -f workshop.yaml
```

| id | Harvester | Rancher |
|----|-----------|---------|
| student-01 | `https://203.0.113.11:8443` | `https://203.0.113.11:30002` |

Nested VIP 在每台主機內部維持 `192.168.122.10`；學員使用**主機 public IP +
DNAT**。密碼**絕不會**印出：它們存放在每台主機的
`~/.rodeo/secrets.yaml`。

預設情況下，`access` 會為每台主機印出兩個網址，因為 fleet 在本機沒有可靠的
訊號判斷某個 lab 實際提供哪些 UI（內建 profile
名稱不會一對一對應到元件：例如 `test` profile 的範例目錄
完全沒有 Rancher 節點）。在清單中設定 `lab.components: [harvester]` 或
`[rancher]`，就能隱藏不適用於你的
workshop 的網址。

存取表是給講師用的。若要讓每位學員拿到自己的 lab（連結、
帳密，而且從任何網路都連得到），請使用
[領取入口](#claim-portal-f5)。

### 學員網路存取（AWS） { #student-network-access-aws }

rodeo 管理的 security group 只允許操作者的 IP，所以預設情況下學員
連不到自己的 lab。若要讓他們從任何網路進來、又不用知道他們的
IP，請設定 `provider.student_access: open`，並在每個 lab 都起來之後開放 lab UI 的 port：

```yaml
provider:
  type: aws
  student_access: open     # operator（預設） | open
```

```bash
rodeo fleet provision -f workshop.yaml     # port 維持只對操作者開放
rodeo fleet deploy -f workshop.yaml
rodeo fleet status -f workshop.yaml        # 等到每台主機都完成
rodeo fleet open-access -f workshop.yaml   # 8443/30002 對 0.0.0.0/0 開放
rodeo fleet open-access --close -f workshop.yaml
```

除非每台主機都已完成所有階段，而且 Harvester
與 Rancher 管理者密碼都夠強（16 個字元以上，包含大寫、小寫與數字），否則 `open-access` 會拒絕執行。
檢查在每台主機上進行，只回報 strong／weak／missing；密碼絕不會
離開主機。security group 由整個 workshop 共用，所以只要有一台主機
還沒準備好，就會擋住所有主機的開放。port 依照 `lab.components` 決定；SSH（`22`）一律
只對操作者開放。之後任何一次 `fleet provision` 都會再把 port 關起來。若使用
`provider.security_group_ids`（BYO），rodeo 不會做任何變更，而是請你自己開放
這些 port。

### 領取入口（F5） { #claim-portal-f5 }

學員開啟一個 HTTPS 頁面，輸入你給他們的 **workshop 代碼**（來自
`portal info`，例如 `RODEO-XVFD-20260930`；大小寫、空格與連字號都不影響）。
輸入之後才會看到 lab 看板與領取表單：姓名、email，以及自己選的 6 位數
PIN（123456 或 111111 這類太明顯的會被拒絕）。他們會拿到
自己的 lab：Harvester 與 Rancher 網址加上管理者密碼，也可以選擇提供
lab 主機的 SSH key。你可以看到誰拿了哪個 lab。設計：
[claim-portal.md](claim-portal.md)。

回到 lab 的方式：

1. **同一台裝置：** 瀏覽器會記住這個 lab 3 天；重新開啟 portal
   會顯示「Continue to your lab」，甚至在輸入 workshop 代碼之前就會出現。「Not you? Forget this
   device」可以在共用電腦上清除紀錄。
2. **另一台裝置：** 「Already have a lab? Get it back」只要求輸入 email 與 PIN
   （email 用來找到 lab，PIN 證明這是他們的，所以兩位學員用同一個
   PIN 也沒關係）。它會用新的連結回傳同一個 lab；舊的連結會
   失效。領取關閉期間也能使用。PIN 錯五次會鎖住該 email。
3. **其他情況：** 講師頁面上有每一個 lab，而
   `rodeo fleet portal unlock EMAIL` 可以解除鎖定。

隱私：每個頁面都有一行頁尾（「we only keep your name and email for this
workshop, deleted when it ends; essential cookies only」），連結到 `/privacy`，
領取表單在輸入資料的地方也會重複這段說明。`/privacy`（公開，不需要代碼）
說明保留了什麼、保留多久，以及三個 cookie（`csrf`、`access`、`lab`）；
其中的期限來自設定 cookie 的同一組常數，若兩者不一致，測試就會失敗。
在公開活動之前，請先讓你的隱私團隊審閱這些措辭。

```yaml
provider:
  type: aws
  student_access: open        # 必填：學員必須連得到自己的 lab
portal:
  enabled: true
  mode: open                  # open（workshop 代碼 + email） | roster（邀請連結） | both
  # roster: students.csv      # roster / both：CSV，欄位為 name,email[,host_id]
  student_ssh: true           # 每個 lab 一個 `student` 使用者 + key，獨立的 sshd 在 :2222（僅限 key）
  title: SUSE Virtualization workshop
  guide_url: https://avaleror.github.io/suse-virt-workshop/   # 或 /guide/，若由 portal 提供
  # code_letters: 6           # RODEO-XXXXXX-YYYYMMDD 中的隨機字母數（4-8；6 = 1.91 億組代碼）
  # hostname: labs.example.com   # 預設為 portal-<ip>.sslip.io（Let's Encrypt）
  # instance_type: t3.small
```

```bash
rodeo fleet provision -f workshop.yaml     # lab + 一台小型 portal VM（獨立 SG：443/80 開放，22 限操作者）
rodeo fleet portal up -f workshop.yaml     # portal 服務 + 使用正式 TLS 憑證的 Caddy
rodeo fleet deploy -f workshop.yaml
rodeo fleet open-access -f workshop.yaml   # 等每個 lab 都完成後執行（啟用 student_ssh 時會加上 :2222）
rodeo fleet portal publish --watch -f workshop.yaml  # 在講師頁面上追蹤部署進度；
                                           # 每個 lab 一準備好就會發布
rodeo fleet portal info -f workshop.yaml   # 放在投影片上的網址 + workshop 代碼
rodeo fleet portal status -f workshop.yaml # 誰拿了哪個 lab
rodeo fleet portal admin-link -f workshop.yaml  # 講師頁面（包含所有 lab 帳密；不要投影出來）
rodeo fleet deprovision --yes -f workshop.yaml  # lab + portal（加 --keep-portal 保留 portal）
```

**為沒到場的人縮減規模。** 依預期人數建立主機，等
現場人數穩定之後，再移除沒人領取的 lab：

```bash
rodeo fleet portal status -f workshop.yaml                  # 還有幾個空的？
rodeo fleet deprovision --yes -f workshop.yaml --unclaimed 35   # 保留幾個備用
rodeo fleet deprovision --yes -f workshop.yaml --unclaimed all  # 或移除所有未領取的 lab
```

`--unclaimed N` 會自己挑選 lab：先挑還沒準備好的（仍在建置或
失敗的），再從編號最大的空閒 lab 往下挑，所以留下來的備用 lab 是
編號小的，也就是新的領取會優先拿到的那些。如果數字大於
portal 上未領取的 lab 數，它會拒絕執行。若要手動挑選 lab，改用 `--host <lab>`
（可重複指定）；學員已領取的 lab 會被拒絕，除非你加上 `--force`。

不論哪種方式，在移除 lab 的期間都會暫停領取，之後再重新開放（如果
原本是開放的），而被終止的 lab 也會從 portal 移除，所以沒有人會
領到已經不存在的 lab。portal VM 本身永遠不會被動到。在 roster 模式下，每位
受邀的學員都算已領取，不論有沒有開啟過。在這個 release 之前建立的 portal
需要執行一次 `rodeo fleet portal up`，才會學到移除指令；在那之前，
`deprovision` 會發出警告並讓領取維持關閉。

| 講師指令 | 內容 |
|--------------------|------|
| `portal publish [--watch] [--interval 60]` | 把 lab 推送到 portal。加上 `--watch` 時：講師頁面上會顯示每個 lab 的部署進度（已完成階段、目前階段、經過時間、失敗），每個 lab 一準備好就能被領取。請讓筆電保持喚醒（[install.md](install.md#differences-and-things-to-know)） |
| `portal status` / `export [-o file.csv]` | 領取紀錄：lab、姓名、email、領取方式、領取時間、第一次開啟時間 |
| `portal admin-link` | 產生講師頁面的新秘密連結：包含每一筆領取紀錄，以及每個 lab 的網址、密碼與 SSH key（舊的連結會失效） |
| `portal open` / `close` / `rotate-code` | 領取時段；產生新的 workshop 代碼（會讓所有人登出看板；個人 lab 連結仍可使用） |
| `portal release LAB` / `revoke EMAIL` / `reassign EMAIL LAB` / `unlock EMAIL` | 在 workshop 進行中修正錯誤 |
| `portal invite [--rotate]` | Roster 模式：保留 lab，寫出包含個人連結的 `<workshop>-invites.csv`（0600） |

各項防護機制：

- **portal 是被動的。** 它永遠不會連到 lab 主機或雲端 API，也不持有
  rodeo SSH key 或雲端帳密。是由筆電透過 SSH、經由 stdin 把 lab 紀錄推送給它。
  portal 的程式碼是 `rodeo/portal/`（只用標準函式庫），原樣複製過去，
  並以非特權使用者在強化過的 systemd unit 中執行。
- **先輸入 workshop 代碼。** portal 的主機名稱幾分鐘內就會公開（TLS
  憑證會記錄在 Certificate Transparency 中），所以在輸入代碼之前，首頁
  只會顯示一個代碼欄位。輸入之後，一個 cookie（綁定
  目前的代碼，24 小時）會顯示所有 lab 的看板（空閒／已領取／建置中），
  上面只有領取者的*姓名*，絕不顯示 email 或任何帳密，還有領取
  表單。預設代碼有 6 個隨機字母（約 1.91 億組代碼）。錯誤的代碼
  也會在所有位址合計後設上限（每 10 分鐘 200 次），所以即使 1,000 個
  位址同時嘗試，在 8 小時的 workshop 中猜中的機率也只有約 0.06%。設定 `portal.code_letters`（4-8）可以改變長度，若
  代碼外洩則用 `rotate-code`。升級之後，對既有 portal 執行的下一次 `portal up` 會把它的
  代碼換成新的長度（設定 `code_letters: 4` 可保留舊的代碼）。
- **Workshop 指南連結：** 設定 `portal.guide_url` 後，每個學員頁面（領取頁面，以及
  他們的 lab 頁面中帳密上方）都會連結到練習題：可以是 `https://` 網址，例如
  GitHub Pages，或是由 portal 本身提供的路徑。指南預設放在 GitHub Pages，
  因為 portal 會隨 fleet 一起銷毀，而學員在 workshop 結束後仍可保留指南。
  隨時可以用 `portal up` 更改；領取紀錄會保留。
- **講師頁面**放在秘密連結後面，會顯示 email、部署進度以及
  每個 lab 的帳密。
- **領取：** 個人連結是 24 個隨機位元組，以 SHA-256 儲存，PIN 以 scrypt 儲存，
  PIN 錯 5 次會鎖住該 email，每個表單都有 CSRF 防護，並設有 no-store 與 CSP 標頭。只有
  *失敗*的嘗試（錯誤的代碼或 PIN、未知的連結）會計入每個位址每 10 分鐘 30 次的
  上限，所以整間教室共用一個 NAT 位址也能領取。
  錯誤的 workshop 代碼也會計入整個 portal 每 10 分鐘 200 次的上限。
  超過之後，只有已經送出 3 次錯誤代碼的位址需要等待；新學員仍然
  可以進入，已經進入的人也會保有 24 小時的存取權。
  log 中絕不會包含 token、email、代碼、PIN 或密碼。
- **學員 SSH：** 每台 lab 主機一個 `student` 使用者，僅限 key 登入，key 是在
  你的筆電上另外產生的（`~/.rodeo/fleet/<workshop>/student-keys/`）。Publish 會在每台
  主機上驗證該使用者沒有 sudo、不屬於任何特權群組，而且無法讀取 `/root`
  （整個 fleet 共用的 rodeo key 就放在那裡）；任何一項檢查失敗都會中止。學員永遠拿不到
  lab 主機的 root。
- `publish` 可以安全地重跑（在 `fleet retry` 之後，或某個 lab 較晚完成時）：
  還沒準備好的 lab 會對學員顯示為「still building」，而且絕不會被
  分配出去。

---

## OpenSSH 需求 { #openssh-requirements }

- 使用 `BatchMode=yes` 的 key 驗證（不會出現密碼提示）。
- Host key 在第一次使用時信任，之後依 workshop 固定下來
  （`StrictHostKeyChecking=accept-new`、`~/.rodeo/fleet/<workshop>/known_hosts`），因為
  fleet 會透過這些連線傳送 lab 密碼與學員 key。建立主機時
  會忘掉它建立的每個位址的 key（EC2 會重複使用 public IP）。如果某台主機
  在 rodeo 之外被重建，而 SSH 回報 host key 已變更，請用
  `ssh-keygen -R <ip> -f ~/.rodeo/fleet/<workshop>/known_hosts` 移除。lab 內部的
  主機→VM 連線（`rodeo/ssh.py`）仍然略過驗證：那些 VM 一直在
  私有網路上被重建。
- 筆電的 PATH 上要有 `ssh`；Agent／`ProxyJump`／`identity_file` 照常可用。
- 每台遠端主機上：SSH 使用者的 PATH 上要有 `rodeo` + `tmux`；通常是 `root@`。

Fleet **不會**在筆電上以 sudo 重新執行自己。

## 設計備註 { #design-notes }

- `rodeo/ssh.py` = 主機→VM 的 lab 連線。
- `rodeo/fleet/ssh_exec.py` = 筆電→KVM 主機。
- 並行數預設值：doctor/status 為 `-j 8`；deploy/retry 使用 `lab.concurrency`
  （預設 4），除非指定了 `-j`。deploy 建議使用低並行數（ISO／網路）。
- F4 取得主機請見[規劃](#roadmap)。Equinix 不在範圍內。
  共用 secret 與修改階段 pipeline 仍然不屬於 Fleet。

---

## F4 取得主機設計 { #f4-host-acquire-design }

取得主機的共用 Protocol／schema。**AWS（F4a）MVP 已實作**於
`rodeo/providers/`，供 Fleet 與單機 `deployment_target: aws` 使用。
GCP／Vultr／Hetzner 仍是 stub。摘要請見[規劃](#roadmap)。

**重要：絕不要在 `workshop.yaml` 中設定 `lab.target: aws`。**

由 AWS 建立的 workshop 主機以 `lab.target: baremetal` 執行 lab（完整的
firewalld／DNAT／`finalise`）。`deployment_target: aws` 是筆電
控制平面／plan 的主機情境標記（`rodeo up --target aws`，以及
保留在執行個體上、用於磁碟下限 + `destroy --cloud` 的 plan）。把
`lab.target: aws` 混進 Fleet 清單會破壞階段的行為。

| 面向 | 單機 | Fleet workshop |
|------|-------------|----------------|
| 取得主機 | `rodeo up --target aws` + plan 中的 `provider:` | `rodeo fleet provision` + `workshop.yaml` 中的 `provider:` |
| Lab 拓撲 profile | `--profile harvester` | 在每台主機上植入相同的基礎 profile |
| 在 KVM 主機上執行 | 以 `baremetal` 執行各階段 | `lab.target: baremetal` |
| 主機上的 plan 標記 | 保留 `deployment_target: aws` | **不要**設定 `lab.target: aws` |

### `HostProvider` Protocol { #hostprovider-protocol }

位於規劃中的 `rodeo/providers/` 的共用合約。Fleet CLI 絕不直接 import boto3/GCP/hcloud：
只透過 registry + 這組介面。

**型別（概念上）**

| 名稱 | 角色 |
|------|------|
| `ProviderConfig` | 解析後的 `workshop.yaml` → `provider:` 對應（type + 各 type 專屬欄位） |
| `ProvisionSpec` | Workshop 名稱、想要的數量／host id、SSH 預設值、要套用的 label |
| `ProvisionedHost` | 一對一對應到清單的 `hosts[]`：`id`、`ssh`、`public_ip`、`labels`、可選的 `provider_id`（雲端執行個體 id） |
| `DeprovisionResult` | 每台主機的結果：destroyed／skipped／error |

**必要操作**

| 方法 | 行為 |
|--------|----------|
| `name` | 固定 id：`aws` \| `gcp` \| `vultr` \| `hetzner` |
| `validate(config) → None` | 欄位缺少或無效時 fail closed（`ConfigError`），**只檢查該 type 的欄位** |
| `plan(spec, config) → list[action]` | 可選的 dry-run：對每個想要的 host id 回報 create／reuse／noop（F4a 有的話更好） |
| `provision(spec, config) → list[ProvisionedHost]` | 冪等：重用已為此 workshop+host id 加上 tag 的執行個體；其餘的則建立；等到 **running** + **SSH BatchMode** 成功 |
| `deprovision(spec, config) → list[DeprovisionResult]` | **只**銷毀帶有下方擁有權 tag 的資源；拒絕沒有 tag 的資源 |

**共用部分（非各 provider 各自實作）**

- SSH 等待／探測使用既有的 `rodeo/fleet/ssh_exec.py`（不用 paramiko）。
- 清單合併：依 `id` 寫入／更新 `hosts[]`；除非加上 `--prune`，否則不刪除靜態主機。
- 可選 extra：`[aws]`、`[gcp]`、`[vultr]`、`[hetzner]`，讓核心安裝保持輕量。

**擁有權 tag**（每個建立的執行個體都有；所有雲端使用相同的 key）

| Tag／label key | 值 |
|-----------------|-------|
| `ManagedBy` | `rodeo` |
| `rodeo-workshop` | plan 的 `name:`／workshop 的 `name:` |
| `rodeo-host-id` | 清單中的主機 `id`（例如 `student-01`），單機則為 `primary` |

GCP 使用 label（DNS-1123）；在雲端要求的地方把 key 轉成小寫，但保留相同的邏輯名稱。

**Protocol 的非目標**

- 不做跨雲端的「通用機型」列舉：規格／映像維持各 provider 專屬。
- F4 不做共用 secret／AMI 發布 pipeline。
- 預設路徑不需要 Libcloud／OpenTofu。

### `workshop.yaml` 的 `provider:` schema { #workshopyaml-provider-schema }

最上層的 `provider:` 是可選的。沒有設定 → 維持目前的行為（只有靜態 `hosts:`）。
有設定 → 可以使用 `fleet provision`／`deprovision`；`type` 決定使用哪個 adapter。

#### 通用欄位 { #common-fields }

```yaml
name: suse-virt-rodeo-emea          # 作為 rodeo-workshop tag 使用
lab:
  dir: /root/suse-virt-workshop
  source: git:https://github.com/example/suse-virt-workshop.git
  # … 既有的 lab key 不變 …
defaults:
  ssh_user: root                     # 或依 AMI 使用 ec2-user / sles / …
  identity_file: ~/.ssh/rodeo-workshop.pem
  # ssh_options: ["ProxyJump=bastion"]
provider:
  type: aws                         # aws | gcp | vultr | hetzner（有 provider: 時必填）
  count: 12                         # hosts: [] 或數量不足時，要確保的主機數
  # host_id_prefix: student-        # 預設為 "student-"；id 為 student-01 … student-N
  # ttl_hours: 10                   # 自動銷毀計時，預設 6：每台主機（以及
  #                                 # portal VM）在啟動後經過這段時間就會關機並被終止。
  #                                 # 超過 6 小時的 workshop 請設定這個值。
  # 套用到每台建立的主機的可選覆寫值：
  # labels: { room: a, event: emea }
hosts: []                           # 空的 → 由 provision 建立；或預先放入靜態 + 雲端混合
```

驗證規則（fail closed）：

- `provider.type` ∈ `{aws, gcp, vultr, hetzner}`。
- 設定 `provider.student_access` 時須 ∈ `{operator, open}`（預設 `operator`）；請見[學員網路存取](#student-network-access-aws)。
- 設定 `provider.ttl_hours` 時須大於 0 且不超過 168（預設 6）；它無法關閉，只有 [kvm-hosts](guide-kvm-hosts.md) lab 可以設為 `off`。
- `provider.host_to_host: true` 讓主機之間可以在任何 port 互連（kvm-hosts 預設啟用）。請見[自動銷毀計時](reference/plan.md#provider-when-deployment_target-aws)。
- 設定 `provider.count` 時須為 1–64 的整數；如果 `hosts:` 不是空的而且省略 count，就只確保那些 id（依 `rodeo-host-id` 重用／建立）。
- 雲端主機上的 SSH 身分是 `~/.rodeo/ssh/labs/<workshop>/` 底下每個 workshop 專屬的 key（請見下方的 SSH）。BYO 主機使用 `~/.rodeo/ssh/id_ed25519` 或 `defaults.identity_file`。`provider.key_name` 會被忽略。
- 各 type 專屬的必填 key 只由該 adapter 的 `validate()` 檢查。

#### AWS API 帳密 { #aws-api-credentials }

建立主機**只使用 boto3**（AWS CLI 是可選的）。透過以下其中一種方式提供帳密：

| 方式 | 做法 |
|--------|-----|
| 共用檔案 | `~/.aws/credentials`（以及可選的 `~/.aws/config`／`AWS_PROFILE`） |
| 環境變數 | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`（+ 可選的 `AWS_SESSION_TOKEN`） |

絕不要把 AWS access key 放進 `workshop.yaml` 或 `rodeo-plan.yaml`。

#### `provider.type: aws` { #providertype-aws }

Harvester lab 請使用 **`m8id.8xlarge`**（32 vCPU / 128 GiB / 一顆本機 NVMe 裝置；目錄中的 `recommended` 等級），SUSE Edge 與 `rancher` profile 則用 `m8id.4xlarge`。Metal 機型仍然可用。Fleet 需要在 `workshop.yaml` 中設定 `region` 與 `subnet_id`；自動的 region 與 subnet 預設值只適用於單機的 `rodeo up --target aws`。

每台建立的主機，以及 portal VM，都有**自動銷毀計時**：啟動後經過 `provider.ttl_hours`，它會關機並由 AWS 終止（預設 6，最多 168，除了 [kvm-hosts](guide-kvm-hosts.md) lab 以外無法關閉）。如果 workshop 比這更長，請在 `fleet provision` 之前設定；`fleet provision` 會顯示每台主機的到期時間。
`apply_host_context` 會把 `resources.harvester.disk_gb` 提高到固定的 **500 GB**，
並把 `resources.rancher.disk_gb` 提高到 **60 GB**：絕不依節點數放大，所以
NVMe 裝置的其餘空間是刻意保留的；它也會把 NVMe 掛載到
`image_dir`。根磁碟的 `volume_size_gib` 只需要放作業系統（約 100 GiB）。過小／
burstable 機型會在驗證時被拒絕。非 metal 機型預設**啟用** nested virt。

SSH：每次部署都有自己的 key。當 workshop 沒有存活中的執行個體時，
`provision` 會在 `~/.rodeo/ssh/labs/<workshop>/` 產生一組新的 ed25519 金鑰對，並把它
匯入成 EC2 key pair **`rodeo-<workshop>-<random>`**（加上 `ManagedBy=rodeo` tag）。之後
加入同一個存活中 workshop 的主機，以及 portal VM，都會重用這把 key。rodeo 會把它
放到每台 KVM 主機上，讓 nested VM 也共用它，而 `rodeo ssh`／`fleet` 會自動
取用。當 workshop 的最後一個執行個體被移除時（`fleet deprovision`、
`rodeo destroy --cloud`），rodeo 會刪除 EC2 中的 key pair 與本機的 key。以其他方式結束的
lab（自動銷毀計時、主控台）的 key，會在同一個 region 的下一次
`provision` 時清掉。如果該 workshop 名稱已經有存活中的執行個體，
而且啟動時用的 key 不在這台機器上（共用帳號中的其他使用者），
`provision` 會以錯誤停止，而不是接管它們：請換一個名稱。
新的執行個體也會取得 cloud-init UserData：root authorized_keys + 為
`ssh_user` 設定 `NOPASSWD` sudo，讓遠端 `rodeo up` 永遠不會要求輸入密碼。
使用 `rodeo ssh student-01` 或 `rodeo ssh student-01/rancher`。

```yaml
provider:
  type: aws
  count: 12
  region: eu-central-1              # 必填
  instance_type: m8id.8xlarge       # 或 instance_tier: recommended
  # ttl_hours: 10                   # 自動銷毀計時，預設 6
  # 省略 ami → 使用最新的 SLES 16 PAYG（suse-sles-16-0-v*-hvm-ssd-x86_64）
  # ami: ami-0123456789abcdef0      # 可選，固定版本
  subnet_id: subnet-0abc…           # 必填
  # security_group_ids: [sg-0abc…]  # 省略 → rodeo 管理一個只限你 IP 的 SG
  # associate_public_ip: true       # 預設 true
  # nested_virtualization: true     # 非 metal 機型預設啟用
  # volume_size_gib: 100            # 根 EBS；lab 磁碟透過 host_context 使用 NVMe
```

**`security_group_ids` 是可選的，但真正的 fleet 請明確設定。**
省略的話，rodeo 會自動管理一個，範圍限定在*執行 `fleet
provision` 的那台機器*目前的 public IP：這適用於單機的 `rodeo up --target
aws`（你既是操作者，也是唯一需要連進去的人），但不適用於
多位學員的 fleet，因為每位學員都從自己的 IP 連線：除了你以外，
他們全都會被擋在外面。只要 `count` > 1 位真正的學員，就把 `security_group_ids` 設成一個真的
涵蓋學員網路的 SG（教室的 CIDR、公開
workshop 用 `0.0.0.0/0`，或 VPN 網段）。

先在 [Marketplace 上的 openSUSE Leap](https://aws.amazon.com/marketplace/pp/prodview-wn2xje27ui45o) 訂閱一次
（目前的 build 範例：*openSUSE Leap 16.0 (x86_64) - v20260629*）。SSH 使用者：**`ec2-user`**。
SLES 16 也可以用：把 `ami:` 設成該映像的 id，如果使用者不是 `ec2-user`，也要設定 `ssh_user`。

#### `provider.type: gcp`（F4b） { #providertype-gcp-f4b }

```yaml
provider:
  type: gcp
  count: 12
  project: my-gcp-project           # 必填
  zone: europe-west3-a              # 必填
  machine_type: n2-standard-32      # 必填
  image: projects/…/global/images/… # 必填（或 family）
  network: default                  # 或完整網址
  subnetwork: regions/…/subnetworks/…
  # enable_nested_virtualization: true   # rodeo 預設 true
  # min_cpu_platform: "Intel Cascade Lake"
  # tags: [rodeo-fleet]             # 供防火牆使用的 GCP network tag
```

驗證：Application Default Credentials／service account，不存放在 `workshop.yaml` 中。

#### `provider.type: vultr`（F4c） { #providertype-vultr-f4c }

```yaml
provider:
  type: vultr
  count: 12
  region: ewr                       # 必填（Vultr location id）
  plan: vbm-8c-128gb                # 必填：完整的 Harvester lab 請用 ≥128 GiB
  os_id: 2284                       # 必填（或 snapshot_id / iPXE）
  # sshkey_id: ["…"]                # Vultr SSH key id
  # firewall_group_id: "…"          # 必須允許 22 / UI port，存取表才能用
  # label_prefix: rodeo-            # 可選
```

驗證：`VULTR_API_KEY`（API key 可能需要設定 IP 允許清單）。建議使用 REST `/v2/bare-metals`
或 OpenAPI client，而不是簡易的社群 wrapper。nested KVM **只能用 bare metal**，
不能用 Vultr Cloud VPS。依所選 profile 的 RAM 篩選 plan（`rodeo doctor`）。

#### `provider.type: hetzner`（F4d） { #providertype-hetzner-f4d }

```yaml
provider:
  type: hetzner
  count: 12
  location: fsn1                    # 必填
  server_type: cpx51                # 必填：必須通過 lab 用的 nested-KVM 驗證
  image: rocky-9                    # 必填（或 snapshot id）；SLES 做法待定
  # ssh_keys: ["rodeo-workshop"]    # Hetzner SSH key 名稱／id
  # networks: []                    # 可選的私有網路
  # firewalls: []                   # 必須開放 22 / UI port，存取表才能用
```

驗證：`HCLOUD_TOKEN`（或未來的 `??` secret key），不以明文放在清單中。
**關卡：** 在 `fleet doctor` 於 workshop 實際使用的 Hetzner Cloud 機型上顯示
nested KVM 之前，不要把 F4d 標為完成。

#### 合併語意 { #merge-semantics }

| 情境 | `fleet provision` |
|-----------|-------------------|
| `hosts: []`、`count: N` | 建立／重用 `student-01`…`student-N`；寫入 `hosts[]` |
| `hosts:` 列出 id | 只確保那些 id（忽略 count，或要求 count ≥ 數量） |
| 執行個體已帶有 `rodeo-workshop` + `rodeo-host-id` tag | 重用；更新 YAML 中的 `ssh`／`public_ip` |
| `fleet deprovision` | 終止／刪除帶有 tag 的執行個體；清除或標記 YAML 中來自雲端的主機 |

靜態主機（沒有 `labels.provider`／沒有雲端 `provider_id`）絕不會被
deprovision 銷毀，除非之後明確選取（`--all-tagged` 維持預設的安全行為）。
