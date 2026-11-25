# 開始使用

## 主機需求 { #host-requirements }

你需要一台 Linux 主機（建議 SLES 16 / Leap 16），可使用 nested KVM、有 root 或免密碼 sudo 權限，並有足夠的記憶體跑你選的 profile，請見下表。

如果要從筆電（macOS 或其他 Linux）操作遠端或雲端主機，同一支 `install.sh` 會把 rodeo 安裝成控制端：請見[在 Linux 與 macOS 上安裝](install.md)。

## 安裝 { #install }

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash</div>
</div>

在 SLES 16 / Leap 16 上，這會把 rodeo 安裝成 KVM 主機：它會 clone repo、在內部建好 Python 環境，並把 `rodeo` 連結成系統指令。不用啟用 venv、不用設定 PATH，也不用加 sudo。在筆電上則會安裝成控制端，請見[安裝](install.md)。

## 部署 { #deploy }

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up</div>
</div>

就這樣。`rodeo up` 會檢查主機、挑一個記憶體放得下的 profile、產生 `~/.rodeo/secrets.yaml`、自動以 sudo 提權、完成部署，最後印出登入用的網址與帳密。它也會自動在 tmux session 裡執行，SSH 或 Instruqt 連線中斷也不會中止正在進行的部署，隨時可以用 `tmux attach -t rodeo-<profile>` 接回來。

如果要指定 profile，而不是讓 `rodeo up` 自己挑：

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo up --profile rancher-test   <span class="rc-val"># Rancher + 單節點 K3s 與 RKE2，約 18 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile rancher        <span class="rc-val"># Rancher + K3s、RKE2 與三節點 RKE2，約 30 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester-ha   <span class="rc-val"># 三節點 Harvester HA，約 52 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile harvester      <span class="rc-val"># 三節點 Harvester + Rancher，約 72 GiB RAM</span>
<span class="rc-cmd">$</span> rodeo up --profile suse-edge      <span class="rc-val"># Rancher + Elemental + EIB + edge 節點</span></div>
</div>

## 選擇 profile { #pick-a-profile }

| Profile | 建出的環境 | RAM |
|---|---|---|
| `rancher-test` | Rancher Prime + 單節點 K3s 與 RKE2 叢集 | 約 18 GiB |
| `rancher` | Rancher Prime + 單節點 K3s、單節點 RKE2 與三節點 RKE2 叢集 | 約 30 GiB |
| `test` | 兩節點 Harvester 叢集，不含 Rancher | 約 36 GiB |
| `harvester-ha` | 三節點 Harvester HCI，不含 Rancher | 約 52 GiB |
| `harvester-2n` | 兩節點 Harvester HCI + Rancher | 約 56 GiB |
| `harvester` | 三節點 Harvester HCI + Rancher | 約 72 GiB |
| `virt-workshop` | `harvester` + 映像快取、NFS 與範例 VM，供 SUSE Virtualization workshop 使用 | AWS `m8id.8xlarge` |
| `suse-edge` | Rancher + Elemental + EIB + edge 節點 | 約 40 GiB |
| `smlm-workshop` | SUSE Multi-Linux Manager workshop（lab-in-a-box 外掛） | 約 30 GiB |

完整的操作說明請見各 profile 指南：[Rancher Prime](guide-rancher.md)、[Harvester HCI](guide-harvester.md)、[SUSE Edge](guide-suse-edge.md)。

## Day-2 維運 { #day-2-operations }

lab 建好之後，它是一個你要持續維運的環境，不是跑一次就丟的腳本：

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> rodeo status              <span class="rc-val"># 部署了什麼、哪裡有漂移</span>
<span class="rc-cmd">$</span> rodeo stop / start        <span class="rc-val"># 依基礎架構順序正常關機／開機</span>
<span class="rc-cmd">$</span> rodeo set-password        <span class="rc-val"># 更換管理者密碼，不用重新部署</span>
<span class="rc-cmd">$</span> rodeo install-extensions  <span class="rc-val"># 部署後補裝或同步 UI extension</span>
<span class="rc-cmd">$</span> rodeo clean               <span class="rc-val"># 拆除整套 lab</span></div>
</div>

## 想要內建以外的 lab？ { #want-a-lab-that-isnt-bundled }

<code>rodeo new mylab --from harvester</code> 會在 `~/.rodeo/profiles/mylab` 產生一份可編輯的 profile。修改 YAML 後執行 `rodeo up --profile mylab`，lab 就會收斂成新的樣子。請見[建立自己的 rodeo](custom-rodeos.md)。

## 從筆電部署到 AWS { #on-aws-from-your-laptop }

在筆電（macOS 或任何 Linux）用同一支 `install.sh` 安裝 rodeo，登入 AWS 後部署：

```bash
aws login
rodeo up --profile harvester --target aws --yes
```

不需要寫 `provider:` 區塊：rodeo 預設使用 `eu-north-1`（或 `$RODEO_AWS_REGION` 指定的 region）、該 region 預設 VPC 的 public subnet，以及 profile 建議的機型，並把這些寫進 lab plan。建立任何資源之前，它會先確認該機型可用。

每台雲端主機都有 **6 小時自動銷毀計時（dead-man switch）**：時間到會關機，接著由 AWS 終止。需要更長的時段請設定 `provider.ttl_hours`；要提早拆除，執行 `rodeo destroy --cloud --yes --config-dir ~/rodeo-labs/<profile>`。請見 [provider 欄位](reference/plan.md#provider-when-deployment_target-aws)。

## 多台主機（workshop fleet） { #many-hosts-workshop-fleet }

要讓整間教室都跑**同一套** lab，請用 `rodeo fleet` 搭配 `workshop.yaml` 清單。在 AWS 上，`rodeo fleet provision` 會為每位學員建立一台主機；`deploy` 會平行建好每一套 lab，`status`、`doctor`、`retry` 與 `diagnose` 都可以針對單一 lab 執行。學員可以透過領取入口，用 workshop 代碼領到自己的 lab。請見 [Fleet](fleet.md)。

## 遇到問題？ { #something-not-working }

請查看[故障排除手冊](runbook.md)，裡面涵蓋部署卡住、Harvester 安裝逾時、VIP 連不到，以及其他在實機上遇過的問題。
