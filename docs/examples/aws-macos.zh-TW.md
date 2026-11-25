# 範例：從 Mac 部署到 AWS，逐步說明

這個範例從一台什麼都沒裝的 Mac 開始，最後在 AWS 上跑起 `rancher` profile：K3s 上的 Rancher Prime，加上它管理的三個下游叢集（單節點 K3s、單節點 RKE2、三節點 RKE2）。Mac 只是控制端。lab 以 nested VM 的形式在一台 EC2 主機上執行。其他 profile 也是一樣的做法：改 `--profile` 就好。

## 你需要什麼 { #what-you-need }

| 需求 | 原因 | 檢查方式 |
|---|---|---|
| 有 git 與 curl 的 macOS | 安裝程式會 clone repo，並在需要時下載自己的 Python | `git --version` |
| Homebrew | 用來安裝 AWS CLI 與 tmux | `brew --version` |
| AWS CLI 2.32.0 或更新版本 | 較舊的版本沒有 `aws login` | `aws --version` |
| tmux（建議） | 終端機關閉時，讓本機的 `rodeo up` session 繼續存在 | `tmux -V` |
| AWS 主控台存取權限 | `aws login` 會沿用你的主控台 session（SUSE 帳號：用 SUSE ID 登入） | 你能開啟 AWS 主控台 |

不需要安裝 Python，也不需要 AWS access key。

請照原樣貼上下面的指令區塊。在互動模式下貼上時，zsh 不會把 `#` 開頭的行當作註解，所以貼上的內容裡不要包含註解。

## 步驟 1：command line tools { #step-1-command-line-tools }

```bash
xcode-select --install
```

如果已經裝好，就繼續下一步。

## 步驟 2：Homebrew { #step-2-homebrew }

如果 `brew --version` 能執行，就略過這一步。

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## 步驟 3：AWS CLI 與 tmux { #step-3-aws-cli-and-tmux }

```bash
brew install awscli tmux
brew upgrade awscli
aws --version
```

只有在 CLI 已經裝過時，`brew upgrade` 才有作用。版本必須是 2.32.0 或更新。

## 步驟 4：安裝 rodeo-cli { #step-4-install-rodeo-cli }

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

在 Mac 上，安裝程式會把 rodeo 安裝成控制端：不需要 root，安裝在 `~/.local/share/rodeo-cli`，指令在 `~/.local/bin/rodeo`，並包含 `[aws]` extra。如果 shell 找不到 `rodeo`：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
rodeo --help
```

之後要更新：`rodeo self-update`。

## 步驟 5：登入 AWS { #step-5-log-in-to-aws }

先在瀏覽器登入 AWS 主控台，然後：

```bash
aws login
aws sts get-caller-identity
```

第一次執行時，如果沒有設定預設 region，`aws login` 會詢問：輸入 `eu-north-1`，也就是 rodeo 使用的 region。接著它會開啟一個瀏覽器頁面讓你確認。`get-caller-identity` 會顯示帳號與角色：建立任何資源之前先確認。session 最長持續 12 小時，視角色而定。過期時，再執行一次 `aws login`。如果你的帳號是用 IAM Identity Center（SSO）登入，改用 `aws sso login --profile <名稱>`，再 `export AWS_PROFILE=<名稱>`：rodeo 會用這個 profile。IAM principal 需要 `SignInLocalDevelopmentAccess` managed policy（root 使用者不需要）。

第一次登入後，較新版本的 AWS CLI 會詢問是否要為 AI coding agent 設定 AWS skills 與 AWS MCP server。lab 不需要這些：每個提示都回答 `n`。

## 步驟 6：部署 { #step-6-deploy }

```bash
rodeo up --profile rancher --target aws --yes
```

rodeo 會挑選 region（`eu-north-1`，或 `$RODEO_AWS_REGION`）、預設 VPC 的公開 subnet，以及這個 profile 建議的執行個體，並把它們寫進 lab plan。它會建立一把 SSH 金鑰，以及一個只允許你目前公開 IP 的 security group，啟動主機，然後在主機上的 tmux 裡建置 lab。最後會印出 Rancher 網址、自動銷毀時間與拆除指令；帳密不會在這裡印出，請見步驟 7。`rancher` 預計需要 20 到 40 分鐘。

它在本機建立的檔案：

| 路徑 | 內容 |
|---|---|
| `~/rodeo-labs/rancher/` | 含 region、subnet 與執行個體的 lab plan。`rodeo destroy` 需要它 |
| `~/.rodeo/secrets.yaml` | 產生的密碼（chmod 600） |
| `~/.rodeo/ssh/labs/rancher/` | 這個 lab 專用的 SSH 金鑰，用於主機與 lab VM。為這次部署產生，並由 `rodeo destroy --cloud` 刪除 |

如果部署期間終端機關閉了：`tmux attach -t rodeo-rancher`。

## 步驟 7：登入 Rancher { #step-7-log-in-to-rancher }

- 網址：`https://<host-public-ip>:30002`
- 使用者：`admin`
- 密碼：`~/.rodeo/secrets.yaml` 裡的 `rancher_admin_password`

在 Mac 上要用 `rodeo status --cloud`（在 `~/rodeo-labs/rancher` 執行，或加上 `--config-dir ~/rodeo-labs/rancher`）：它會連到 AWS 主機，印出 Rancher 網址與帳密。不加 `--cloud` 的 `rodeo status` 查的是本機的 libvirt，在 Mac 上只會顯示每個階段都還沒完成。rodeo 部署的 Rancher 已經設定好 server URL，登入後會直接進入首頁。SSH 與 day-2 指令請見 [Rancher profile 指南](../guide-rancher.md#day-2-operations)。從 `~/rodeo-labs/rancher` 執行它們，或加上 `--config-dir ~/rodeo-labs/rancher`。

## 步驟 8：拆除 { #step-8-tear-down }

主機會在 `provider.ttl_hours` 之後自行終止（預設 6 小時，請見[自動銷毀計時（dead-man switch）](../reference/plan.md#provider-when-deployment_target-aws)）。要提早刪除：

```bash
rodeo destroy --cloud --yes --config-dir ~/rodeo-labs/rancher
```


## SSH 金鑰與共用帳號 { #ssh-key-and-shared-accounts }

每次部署都會在 `~/.rodeo/ssh/labs/<lab>/` 產生自己的 SSH 金鑰，並匯入成名為 `rodeo-<lab>-<random>` 的 EC2 key pair。同一個 AWS 帳號裡的兩位使用者永遠不會共用或覆寫彼此的 key pair。`rodeo destroy --cloud` 會刪除 key pair 與本機金鑰。如果 dead-man switch 先結束了 lab，下一次 `rodeo up` 會把它們清掉。

lab 名稱仍然必須是你自己的。如果帳號裡有其他人正在跑同一個 plan 名稱的 lab，`rodeo up` 會停下來並顯示：

```
lab 'rancher' already has instances in AWS (...) launched with key pair ..., whose private key is not on this machine
```

不要銷毀那個 lab：那是別的使用者的。修改 `~/rodeo-labs/rancher/rodeo-plan.yaml` 裡的 `name:`（例如 `rancher-yourname`），再執行一次 `rodeo up`。

## 常見問題 { #common-problems }

| 症狀 | 解法 |
|---|---|
| `aws: unknown command 'login'` | AWS CLI 早於 2.32.0：`brew upgrade awscli` |
| `session has expired` 或 `CreateOAuth2Token` | 再執行一次 `aws login`。偶發一次的 `CreateOAuth2Token` 錯誤可能只需要重跑 |
| `MissingDependencyException` | 缺少 `[aws]` extra：再執行一次安裝程式 |
| `no default VPC in <region>` | `export RODEO_AWS_REGION=<region with a default VPC>`，再執行一次 `rodeo up` |
| Rancher UI 或 SSH 逾時 | 你的公開 IP 變了（新的 Wi-Fi、VPN）。重新執行 `rodeo up` 指令：它會把規則改成你的新 IP，並略過已完成的階段 |
| `already has instances in AWS ... not on this machine` | 其他使用者有一個用你的 plan 名稱、正在執行的 lab。請見上方的 SSH 金鑰一節 |
| `rodeo: command not found` | 把 `~/.local/bin` 加進 PATH（步驟 4） |

更多內容請見 [runbook](../runbook.md)。
