# 在 Linux 與 macOS 上安裝

rodeo 有兩種角色，各自有不同的平台規則：

| 角色 | 用途 | Linux | macOS |
|------|--------------|-------|-------|
| **KVM 主機** | 執行 lab 本身：`rodeo up`、`status`、`stop`、`clean`…… | 可以（建議 SLES 16 / Leap 16） | 不行 |
| **控制端** | 操作遠端主機：`rodeo up --target aws`、`rodeo fleet ...`、`rodeo fleet portal ...`、`rodeo ssh` | 可以 | 可以 |

Mac 永遠不能當 KVM 主機：lab 需要 `/dev/kvm`、libvirt 與 Linux 的 `/proc`。
在 Mac 上執行 `rodeo doctor` 會回報 0 GiB RAM 而且沒有 `/dev/kvm`，這是正常的。
其他功能（雲端主機、fleet、領取入口（claim portal））在兩種平台上用起來都
一樣。

## 一支安裝程式，兩種角色 { #one-installer-two-roles }

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

`install.sh` 會檢查這台機器，決定它的角色：

| 機器 | 角色 | 安裝內容 |
|---------|------|------------------|
| SLES 16 / Leap 16 | **KVM 主機** | 需要 root。用 zypper 安裝 `python3`、`pip` 與 `git`，rodeo 裝在 `/opt/rodeo-cli`、`/usr/local/bin/rodeo`。接著執行：`rodeo up` |
| macOS、SLES/Leap 15、Ubuntu、Fedora、其他任何 Linux | **控制端** | 不需要系統套件，也不需要 root。含 `[aws,gcp]` extras 的 rodeo 裝在 `~/.local/share/rodeo-cli`、`~/.local/bin/rodeo`（以 root 安裝時：`/opt/rodeo-cli`、`/usr/local/bin`）。接著執行：`rodeo up --profile <name> --target aws`（或 `--target gcp`） |

在控制端上，安裝程式會使用機器上已經有的 Python 3.10+
（Homebrew、SLES 15 上的 `python311`……）。如果沒有，例如 macOS 的
Python 3.9 或 SLES 15 的 3.6，它會用固定版本的
[uv](https://github.com/astral-sh/uv) 下載一份私有的 Python 到安裝目錄。
系統的 Python 完全不會被動到，也不會修改任何 shell profile；如果 `~/.local/bin`
還不在 `PATH` 裡，安裝程式會印出要加上的 `PATH` 那一行。

它需要 `git` 與 `curl`。在 macOS 上，這兩個工具隨 Xcode command line tools 提供
（`xcode-select --install`）；在 Linux 上以 root 執行時，安裝程式會自己補裝。

- 預設會安裝**最新的已驗證版本**：也就是最近一次通過實際部署的版本，記錄在
  [`STABLE`](https://github.com/avaleror/rodeo-cli/blob/main/STABLE)
  檔案裡。在它之後才打 tag 的版本，要等到同樣通過驗證才會被安裝
  （[版本如何驗證](releasing.md)）。
- `--ref <tag|branch|sha>` 可以安裝其他版本：指定的版本
  （`--ref v0.33.0`），或用 `--ref main` 安裝最新的程式碼。
- `--mode kvm-host|control-plane`（或 `RODEO_MODE`）會覆寫自動選擇的結果，
  例如在其他發行版上當 KVM 主機，風險自負。
- `--no-builder`（或 `RODEO_BUILDER=0`）會略過 [Rodeo Builder 服務](#rodeo-builder-service)，
  並移除先前安裝時設定好的服務。`--builder-host ADDR`（或
  `RODEO_BUILDER_HOST`）設定它監聽的位址（預設 `0.0.0.0`）。
- 重新執行安裝程式就會更新 rodeo。如果環境還停留在太舊的 Python，
  會被重建。

在雲端主機上，你通常不需要手動執行它：`rodeo up --target aws` 與
`rodeo fleet deploy` 會替你在主機上執行。

在控制端上，不加 `--target aws` 執行 `rodeo up` 會停下來並告訴你原因，而不是
啟動一個它跑不了的 lab（`--no-deploy` 仍然會把 lab 設定好）。
設定 `RODEO_ALLOW_ANY_KVM_HOST=1` 可以讓你還是在不支援的 Linux 上試跑本機 lab。

### Rodeo Builder 服務 { #rodeo-builder-service }

在使用 systemd 的 Linux 上，不論哪種角色，安裝程式都會把
[Rodeo Builder](rodeo-builder.md) 設成 `rodeo-builder.service` 執行，所以
安裝完成後以及每次重新開機後，`https://<this host>:8678/` 都會在線上。它
執行 `rodeo builder --no-open --host 0.0.0.0 --expose`，透過 HTTPS 監聽所有
網路介面（憑證由 rodeo 自己的 CA 簽發；每個瀏覽器都要匯入一次
`https://<this host>:8678/ca.pem`，請見
[Rodeo Builder](rodeo-builder.md#the-live-builder)），而且每次更新時，安裝程式與
`rodeo self-update` 都會重新啟動它（`--no-restart` 可略過），所以
它執行的永遠是剛安裝好的程式碼。如果伺服器還在跑舊的程式碼，它會繼續
提供啟動時讀到的頁面，卻發出新的腳本，頁面就會打不開。

在所有網路介面上，builder 只回應這台機器的主機名稱以及它網路介面上的
位址，這些在每次啟動時讀取；其他任何 `Host` 都會被拒絕，所以其他網站仍然無法透過 DNS rebinding 存取它。
如果用戶端使用的名稱是這台機器自己看不到的（DNS 別名、NAT 後面的公開 IP），
用 `RODEO_BUILDER_ALLOW="name1 name2"` 加上去。

對外開放時，任何能連到 port 8678 的人都能以這個服務的使用者身分儲存 profile；
**Deploy** 與已儲存的帳密會要求登入（`rodeo logins add <user>`，
請見[設定頁面](#settings-page)），而 **Publish** 使用的是
github.com 的頁面，而不是這台主機的 GitHub 登入（請見
[Rodeo Builder](rodeo-builder.md)）。用防火牆限制誰能連到這個 port，
或用 `--builder-host 127.0.0.1` 讓它只留在這台機器上，再透過通道連線
（`ssh -L 8678:127.0.0.1:8678 <host>`）。

| 安裝身分 | Unit 檔 | 管理方式 |
|--------------|-----------|-------------|
| root | `/etc/systemd/system/rodeo-builder.service`（以 root 執行） | `systemctl status rodeo-builder`、`journalctl -u rodeo-builder` |
| 一般使用者 | `~/.config/systemd/user/rodeo-builder.service` | `systemctl --user status rodeo-builder`、`journalctl --user -u rodeo-builder` |

使用者服務只在該使用者登入期間執行；`loginctl enable-linger` 可以讓它在
沒有 session 時繼續執行。

要關閉它，用 `--no-builder`（或 `RODEO_BUILDER=0`）重新執行安裝程式：
它會停止、停用並移除這個 unit。`systemctl disable --now rodeo-builder`
也可以，但下次不加 `--no-builder` 安裝時又會重新開啟。macOS
與沒有 systemd 的機器不會有這個服務；在那些機器上請手動執行 `rodeo builder`。

### 設定頁面 { #settings-page }

builder 的設定頁面（請見 [Rodeo Builder](rodeo-builder.md#settings-and-stored-credentials)）
由 `rodeo builder` 本身提供，使用它自己的位址與 port。在 Linux 上以 root 安裝時，
不論哪種角色，安裝程式只會準備它的登入檔：

| 路徑 | 內容 |
|------|------|
| `/etc/rodeo-builder/htpasswd` | 登入檔：可以新增或刪除帳密的使用者（連結到 lab-builder 的 `/etc/lab-builder/htpasswd`，不存在時會建立一個空檔） |

用 `rodeo logins add <user>` 新增使用者（另有 `list`、`remove`）。這個檔案是
htpasswd 格式，雜湊是 bcrypt，由 rodeo 自己驗證：不需要任何 Apache
工具，`htpasswd -B` 產生的行也能用。用 `htpasswd -m`
（APR1-MD5）建立的使用者可以登入，並會在下次設定密碼時改成 bcrypt；
其他任何雜湊（SHA-1、crypt）在重新設定密碼之前都無法登入。
沒有系統檔案時，例如在筆電上或非 root 安裝，`rodeo builder`
會使用 `~/.rodeo/builder/htpasswd`。

舊版透過 Apache 提供這個頁面；更新時會移除舊版為此安裝的內容
（`/srv/www/rodeo-builder/`、`/usr/local/sbin/rodeo-builder-helper`、
`/etc/sudoers.d/rodeo-builder` 以及 Apache `conf.d` 裡的 `rodeo-builder.conf`，
並重新載入正在執行的 Apache）。登入檔會保留。

### 從 checkout 安裝（如果你要修改 rodeo 本身） { #from-a-checkout-if-you-change-rodeo-itself }

```bash
git clone https://github.com/avaleror/rodeo-cli.git ~/GitHub/rodeo-cli
cd ~/GitHub/rodeo-cli
bash install.sh --dev        # 就地以可編輯模式安裝，包含 dev extras
```

這是可編輯安裝，所以那個目錄目前 checkout 的是哪個分支，
`rodeo` 執行的就是哪個分支。

## AWS 帳密（兩種平台都適用） { #aws-credentials-both-platforms }

rodeo 使用 boto3，絕不會從 `workshop.yaml` 或 `rodeo-plan.yaml` 讀取金鑰。任何
標準的 AWS 帳密來源都可以用：`aws login`（主控台 session，SUSE
帳號使用這種）、`aws sso login`、`~/.aws/credentials`，或環境變數。

```bash
aws login
aws sts get-caller-identity             # 建立任何資源之前先確認
```

`aws login` session 需要 `awscrt` 套件，`[aws]` extra 會安裝它
（`boto3[crt]`）。少了它，每個 AWS 呼叫都會失敗並出現 `MissingDependencyException`。
這類 session 也可能在指令執行到一半時丟出暫時性的 `CreateOAuth2Token` 錯誤，但 session
其實仍然有效：重新執行指令即可。

## 差異與注意事項 { #differences-and-things-to-know }

| 主題 | Linux | macOS |
|-------|-------|-------|
| 安裝程式 | `install.sh`（在 SLES 16 / Leap 16 上是 KVM 主機，其他環境是控制端） | `install.sh`（控制端） |
| Python | 發行版的 `python3` 3.10+，否則透過 uv 安裝私有版本 | 有 Homebrew 的就用它，否則透過 uv 安裝私有版本（系統的 `python3` 是 3.9） |
| `[aws]` extra | 在控制端上安裝 | 會安裝 |
| 本機 lab（不加 `--target aws` 的 `rodeo up`） | 可以，在 KVM 主機上 | 不行 |
| SSH | OpenSSH | 內建 OpenSSH；不需要額外安裝 |
| rodeo 自己的 SSH 金鑰 | `~/.rodeo/ssh/id_ed25519`，第一次使用時建立；AWS lab 在 `~/.rodeo/ssh/labs/<lab>/` 有各自的金鑰，會隨 lab 一起刪除 | 相同 |

**你筆電的公開 IP。** rodeo 管理的 AWS security group 只允許最近一次執行
`provision`（或 `up --target aws`）的那台機器的公開 IP。換網路
（換一個 Wi-Fi、VPN、手機網路分享）就會被擋在外面：SSH 與 `rodeo fleet` 會
逾時。重新執行 `rodeo fleet provision -f workshop.yaml`（或再執行一次 `rodeo up --target aws`）：
它會沿用原本的主機，並把規則改成你的新 IP。它也會關閉所有
學員 port，所以之後要再執行一次 `rodeo fleet open-access -f workshop.yaml`。

**長時間執行時筆電休眠。** 部署是在主機上執行的（在 tmux 裡），所以筆電
休眠不會讓它中斷。會停掉的是接在後面、從你的機器執行的指令，
例如 `rodeo fleet portal publish --watch`。這些指令執行期間，請讓機器保持喚醒：

```bash
# macOS
caffeinate -i rodeo fleet portal publish --watch -f workshop.yaml
# Linux (systemd)
systemd-inhibit --what=sleep rodeo fleet portal publish --watch -f workshop.yaml
```

如果 watch 還是停了，重新啟動它就好：已經發布的 lab 會維持發布狀態，
而且講師頁面會在更新停止傳來時發出警告。

單一主機的 `rodeo up --target aws` 也是一樣：如果筆電失去
session（休眠、Wi-Fi、Ctrl-C），部署仍會在主機上繼續執行。它的日誌在主機上的
`/root/.rodeo/logs/aws-up.log`（先 `rodeo ssh`，再用 `sudo tail -f` 看它）。在
那次部署還在進行時再次執行 `rodeo up`，會以 exit 75 停止，而不會
啟動第二次部署。

**哪個 rodeo 在哪裡執行。** 你本機的 checkout 只負責控制端。KVM
主機會從 GitHub 安裝 rodeo：和你筆電執行的版本相同；如果你的筆電跑的是
兩個版本之間的 checkout，就是 `main`；或是你用 `--ref` /
`lab.ref` 指定的 ref。本機的修改要先 push，才會到達主機。唯一的
例外是領取入口：`rodeo fleet portal up` 會把入口的程式碼從
你本機的 checkout 複製到入口 VM。

**包在 rodeo 外面的腳本。** macOS 內建的是 BSD 工具與 zsh，而不是 GNU 工具與 bash。
你包在 rodeo 外面寫的指令可能會有不同的行為：沒有 `timeout`
（`brew install coreutils` 會提供 `gtimeout`）、`sed -i` 要寫成 `sed -i ''`，而且 zsh
不會對沒加引號的變數做斷詞。rodeo 本身在控制端上不依賴這些工具。
