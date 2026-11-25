# MCP server

`rodeo mcp` 透過 MCP **stdio** transport（以換行分隔的 JSON-RPC 2.0），把 rodeo 提供給 MCP 用戶端——Claude Code、編輯器、agent
執行環境——使用。
它不需要額外的相依套件：協定實作在 `rodeo/mcp/` 中。

每個工具都是直接執行 rodeo CLI，所以涵蓋的範圍包括 **native** 階段
pipeline（`suse-virt`、`rancher`、`suse-edge`、`suse-telco`）以及**外部
engine**（`lab-in-a-box`、外掛提供的類型），用的就是操作者
輸入的同一組指令。

## 執行 { #run-it }

```bash
rodeo mcp --list-tools                      # 會提供哪些工具
rodeo mcp                                   # 唯讀 server
rodeo mcp --allow-write --lab-dir ~/rodeo-labs/harvester
```

| 選項 | 作用 |
|---|---|
| `--allow-write` | 也提供生命週期工具。預設關閉，所以 server 無法部署、清除或銷毀任何東西。 |
| `--lab-dir DIR` | lab 範圍工具的預設 lab 目錄。沒有指定時，使用偵測到的 lab 目錄，否則使用目前的工作目錄。 |
| `--list-tools` | 印出提供的工具後結束。 |

lab 範圍的工具在每次呼叫時也接受 `lab_dir` 參數，它會覆寫
server 的預設值——rodeo 指令是以 lab 目錄為基準的。

## 把 server 限制在列出的 lab { #limit-the-server-to-listed-labs }

```bash
rodeo lab add ~/rodeo-labs/harvester    # 需要終端機
rodeo lab list
rodeo lab remove ~/rodeo-labs/harvester # 需要終端機
```

`rodeo lab add` 會把 lab 的 realpath 寫入 `~/.rodeo/labs.yaml`（權限 0600）。
這個檔案一旦存在，即使清單是空的，MCP 工具收到的每個路徑
都必須解析到列出的 lab 或其中的路徑：`lab_dir`（或 server
預設值）、`rodeo_up` 的 `dir`、`rodeo_init` 的 `target_dir`、`rodeo_instances_list`
的 `dir`、`rodeo_new` 的 `from_zip` 與 `from_lab_json`，以及 fleet 的 `file`。symlink
與 `..` 會先被解析，相對路徑則以工具的 lab
目錄為基準。這時 `rodeo_new` 的 `from_base` 只接受內建的 profile 名稱，不接受
網址。lab 範圍的工具也需要該目錄本身有 `rodeo-plan.yaml` 或 `definition.yaml`，
否則 rodeo 會退回使用上層的 lab
或最後使用的 lab（`rodeo_init`，以及帶 `dir` 的 `rodeo_up`，可以植入一個
空的）。被拒絕的呼叫會成為工具錯誤，不會執行任何東西。

沒有這個檔案時，server 會維持目前的行為，接受任何目錄，
並在啟動時於 stderr 印出一則警告。

這份清單只是一般的使用者資料：它能防止 MCP 用戶端或搞混的模型
把工具指向其他目錄，但擋不住以同一個使用者身分執行的程序，
因為那種程序可以編輯這個檔案。

## 向用戶端註冊 { #register-it-with-a-client }

Claude Code（`~/.claude.json` 或專案的 `.mcp.json`）：

```json
{
  "mcpServers": {
    "rodeo": {
      "command": "rodeo",
      "args": ["mcp", "--allow-write", "--lab-dir", "/root/rodeo-labs/harvester"]
    }
  }
}
```

如果 agent 只應該觀察 lab，就拿掉 `--allow-write`。

## 讓 agent 只用 MCP 工具：`rodeo agent-setup` { #keep-the-agent-on-the-mcp-tools-rodeo-agent-setup }

有 shell 的 agent 可能會略過 MCP server，自己輸入 `rodeo up` 或 `sudo
virsh destroy`。`rodeo agent-setup` 會在 lab 中寫入一套 Claude Code 工具組，
用來攔下這種錯誤：

```bash
cd ~/rodeo-labs/harvester
rodeo agent-setup            # 寫入 .claude/settings.json
rodeo agent-setup --check    # 缺少工具組時以結束碼 1 結束
```

它會合併進 `<lab>/.claude/settings.json`：

- `permissions.deny`：`Bash(rodeo)`、`Bash(rodeo *)`、`Bash(sudo *)`、
  `Bash(virsh *)`，以及 plan 中列出的每個 VM
  名稱、VM 位址、VIP 與 AWS 主機的 `Bash(ssh *HOST*)`／`Bash(scp *HOST*)`。
- Bash 上的一個 `PreToolUse` hook（`python -I .../rodeo/agent_guard.py`），它也會
  攔下串接的形式（`cd lab && rodeo up`、`bash -c '...'`、
  `$(rodeo status)`），並告訴模型改用 MCP 工具。

你自己的規則與 hook 會保留，奇怪或無效的 JSON 會被拒絕而不是
覆寫，symlink 的 settings 檔或 `.claude` 目錄會被拒絕，
第二次執行不會改變任何東西。在 lab 的主機變更之後（例如新增一台
AWS 主機）請重跑：新的主機規則會被加入，rodeo 的 hook 項目也會
就地更新。`.claude/` 不屬於 lab hash 的一部分，所以寫入工具組
不會讓 lab 變成不受信任。

**這是錯誤過濾器，不是安全邊界。** 它是一個同一個使用者就能編輯或刪除的
設定檔，所以它擋得住搞混的模型，擋不住惡意的
模型。

MCP 本機部署（`rodeo_up`、`rodeo_deploy` 以及其他需要 root 的寫入工具）
仍然需要執行 MCP server 的使用者有 NOPASSWD sudo。
工具組只在 agent 的 shell 中禁止 `sudo`，不會在 server 中禁止。

`rodeo doctor` 會顯示目前的 lab 是否已安裝工具組（而
`rodeo doctor --output json` 會以 `agent_kit` 帶出這項資訊）。它能判斷
工具組已安裝，但無法判斷是否有 agent session 在沒有工具組的情況下執行（當
doctor 本身是從沒有工具組的 Claude Code shell 執行時，它會說明這一點）。目前只有
Claude Code 有工具組。當 doctor 發現 Gemini CLI 或 Codex CLI 的
設定時，會回報「no kit for this client」。

## 工具 { #tools }

唯讀（一律提供）：

| 工具 | rodeo 指令 |
|---|---|
| `rodeo_version` | `rodeo --version` |
| `rodeo_help` | `rodeo [COMMAND] --help`——找出沒有專屬工具的指令 |
| `rodeo_engines` | 已註冊的 lab 類型，每個都標示為 `native` 或 `external`（JSON） |
| `rodeo_doctor` | `rodeo doctor` |
| `rodeo_profiles` | `rodeo profiles` |
| `rodeo_plan` | `rodeo plan`（`output: json` 輸出單一 JSON 物件，請見下方） |
| `rodeo_status` | `rodeo status` |

| `rodeo_plan` | `rodeo plan` |
| `rodeo_status` | `rodeo status`；`cloud: true` 會執行 `rodeo status --cloud`（AWS 主機的部署） |
| `rodeo_list` | `rodeo list` |
| `rodeo_logs` | `rodeo logs --no-follow` |
| `rodeo_instances_list` | `rodeo instances list` |
| `rodeo_fleet_status` | `rodeo fleet status` |
| `rodeo_fleet_doctor` | `rodeo fleet doctor` |
| `rodeo_preflight` | `rodeo deploy --check`——只驗證，不部署任何東西 |

生命週期（只在加上 `--allow-write` 時提供）：

| 工具 | rodeo 指令 |
|---|---|
| `rodeo_up` | `rodeo up --yes --no-tmux`；目標為 `aws` 時會加上 `--detach`（請見下方） |
| `rodeo_deploy` | `rodeo deploy --no-tui`（`from_phase` 從某個階段接續執行） |
| `rodeo_init` | `rodeo init` |
| `rodeo_new` | `rodeo new` |
| `rodeo_start`, `rodeo_stop`, `rodeo_start_if_needed`, `rodeo_restart` | VM 電源 |
| `rodeo_clean` | `rodeo clean --yes`——具破壞性 |
| `rodeo_destroy` | `rodeo destroy --yes`——具破壞性 |
| `rodeo_set_password` | `rodeo set-password` |
| `rodeo_install_extensions` | `rodeo install-extensions` |
| `rodeo_self_update` | `rodeo self-update` |

每個工具都是非互動式的：沒有 TUI、沒有 tmux 包裝、沒有提示、沒有顏色。
失敗的指令會以工具錯誤的形式回傳，帶有 rodeo 的結束碼與
輸出的結尾部分，絕不會以協定錯誤的形式回傳。

逾時或被用戶端取消（`notifications/cancelled`）的呼叫
會停止它整個 process group：先送 SIGTERM，5 秒後再送 SIGKILL。需要 root 的寫入
工具會透過 `sudo` 重新執行自己，所以它們的 `rodeo` 及其
`ansible-playbook`/`ssh` 子程序都以 root 執行，server 碰不到。sudo
會把 SIGTERM 轉送給那個 root 的 `rodeo`，由它停止自己啟動的程序
（它的子孫程序，以及當它是 process group leader 時的整個 process group），對於忽略 SIGTERM 的程序，
3 秒後送 SIGKILL，然後結束。它絕不會對只是繼承而來的 group 中
其他成員送訊號，例如執行
`sudo rodeo` 的腳本或 `timeout`。從 MCP 進行的本機部署
需要 NOPASSWD sudo，因為沒有終端機可以輸入密碼。

## 以 JSON 輸出 plan { #plan-as-json }

`rodeo plan --output json`（MCP：`rodeo_plan` 搭配 `output: json`）會印出一個
物件：`schema`（1）、`name`、`type`、`host_reachable`、`creates`、`changes`
（`kind` 為 memory、vcpu 或 dhcp，並帶有 `from` 與 `to`）、`unchanged`、
`foreign_vms`、`network`、`storage`（`image_dir`、`downloads`）、`phases`
（`state` 為 done、drift、guarded、pending 或 failed）、`phases_pending`、
`summary` 與 `config_error`。無法載入的 plan 只會印出
`{"schema": 1, "error": "..."}`。

這個物件不含任何帳密值：`plan` 不會解析任何 `??` 占位符、
絕不讀取 `secrets.yaml` 或 lab 的 secret 檔，並會遮蔽 plan 本身
直接寫入的帳密值，以及網址中任何的 `user:password@`。結束碼
與文字輸出一致：當部署會拒絕這個 plan 時（不受信任的 lab、
無效的值）為 2，即使 JSON 已經印出。受信任 lab 中的 `??` 來源
會延後到部署時處理，不算失敗。

## 外掛工具 { #plugin-tools }

外掛（一個 `rodeo.plugins` entry point）透過
`rodeo.mcp.tools.register_mcp_tool(tool, mutating=..., lab_scoped=...)` 新增工具。兩個
旗標都是必填。內建名稱，或已被其他外掛使用的名稱，會被拒絕：
外掛不能取代內建工具。會變更狀態的外掛工具只在
加上 `--allow-write` 時提供，而每個外掛工具都要經過上述的 lab 清單檢查
（lab 範圍時檢查 `lab_dir`，另外還有每個以 `path="path"` 宣告的 `Arg`）。

### AWS 上的 `rodeo_up` 會回傳 pid { #rodeo_up-on-aws-returns-a-pid }

AWS 部署需要一到兩個小時。當目標是 `aws` 時（`target`
參數，否則為 plan 的 `deployment_target`），`rodeo_up` 會傳入 `--detach`：
rodeo 建立 EC2 主機、在那裡啟動 `rodeo up`，並回傳一個 JSON
物件 `{"state": "started", "pid", "start", "log", "host"}`。部署會在主機上
繼續執行。用 `rodeo_status` 搭配 `cloud: true` 追蹤，它會
回報 `running`、帶結束碼的 `exited`、`gone`（被終止或主機
重開機）或 `none`，再加上 log 的結尾部分。如果那裡已經有部署在執行，
會以工具錯誤的形式回傳
`{"state": "already_running", "pid", "log", "host"}`（rodeo 結束碼 75）。

本機部署目前還沒有 detach：其他任何目標的 `rodeo_up` 都會一直等到
部署結束，在 server 的逾時範圍內。

## 新增工具 { #adding-a-tool }

在 `rodeo/mcp/tools.py` 的 `READ_TOOLS` 或 `WRITE_TOOLS` 中加入一列：rodeo
子指令、它帶型別的 `Arg`，以及任何固定的旗標。Schema、分派與
寫入閘門都會從這一列自動產生——`rodeo/mcp/server.py` 不需要修改。
