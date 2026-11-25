---
title: rodeo-cli
---

<div class="rc-hero" markdown>
<div class="rc-eyebrow">// 宣告式 lab 部署</div>

# 不用寫 Ansible、不用碰 libvirt，就能建出真正的 lab 環境

<p class="rc-tagline">rodeo-cli 是一套宣告式 CLI，把一個 YAML 檔變成 KVM 上可以實際操作的 lab：SUSE Virtualization（Harvester HCI）叢集、搭配 K3s 與 RKE2 叢集的 Rancher Prime，或是完整的 SUSE Edge 堆疊。可以在 SLES 16 主機上執行，也可以從你的筆電部署到 AWS。選好 profile，一個指令就完成。</p>

<div class="rc-hero-actions">
  <a href="get-started/" class="rc-btn rc-btn-primary">開始使用 →</a>
  <a href="https://github.com/avaleror/rodeo-cli" class="rc-btn rc-btn-secondary">前往 GitHub</a>
</div>
</div>

<div class="rc-stats" markdown>
<div class="rc-stat"><div class="rc-stat-num">10</div><div class="rc-stat-label">內建 profile</div></div>
<div class="rc-stat"><div class="rc-stat-num">1,000+</div><div class="rc-stat-label">項測試通過</div></div>
<div class="rc-stat"><div class="rc-stat-num">1</div><div class="rc-stat-label">個指令完成部署</div></div>
<div class="rc-stat"><div class="rc-stat-num rc-stat-num--text">GPL-3.0</div><div class="rc-stat-label">授權</div></div>
</div>

## 為什麼要做這個工具 { #why-it-exists }

為了 demo、workshop 或測試建一套 Harvester 叢集或 Rancher Prime，通常得寫 Ansible、手動接 libvirt 網路，而且每次都要重新摸一遍同樣的 iPXE 開機流程。rodeo-cli 用一個概念取代這一切：描述你要的 lab，交給 plan/apply 流程去建。

一個 **profile** 就是一個設定目錄，裡面有兩個 YAML 檔。`rodeo-plan.yaml` 設定資源與帳密；`definition.yaml` 描述拓撲：節點、網路、對外服務、開機順序。CLI 讀取這兩個檔案，用 `rodeo plan` 顯示和主機現況的差異，再用 `rodeo deploy` 收斂到目標狀態。重複執行是安全的：deploy 預設會把 VM 記憶體與 vCPU 的漂移調回設定值（加 `--no-reconcile` 可關閉）；需要更大範圍重新套用時，用 `--force` 或 `--from`。

## 主要功能 { #main-features }

<div class="rc-grid" markdown>

<div class="rc-card" markdown>
<div class="rc-card-label">宣告式</div>
### 先 plan，再套用 { #plan-then-apply }
`rodeo plan` 會在任何變更發生前，先比對你的 YAML 與主機現況。不會有意外，也不用猜重跑會動到什麼。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">開箱即用</div>
### 10 個內建 profile { #10-bundled-profiles }
兩到三節點的 Harvester HCI（可搭配或不搭配 Rancher）、SUSE Virtualization workshop lab、搭配 K3s 與 RKE2 叢集的 Rancher Prime，以及含 Elemental、EIB 與 edge 節點的完整 SUSE Edge 堆疊。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">自由調整</div>
### 自訂 rodeo { #custom-rodeos }
`rodeo new mylab --from harvester` 會產生一份可編輯的 profile。修改拓撲後重跑，lab 就會收斂成新的樣子，不必 fork 這個工具。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">為實機打造</div>
### 一個指令就上手 { #one-command-on-ramp }
`rodeo up` 會檢查主機、挑一個記憶體放得下的 lab、產生帳密、自動以 sudo 提權，並在 tmux 裡執行，SSH 斷線也不會中斷正在進行的部署。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">安全無虞</div>
### 帳密不進 git { #secrets-stay-out-of-git }
帳密存放在 `~/.rodeo/secrets.yaml`（權限 chmod 600），plan 裡用 `??key` 占位符引用。任何敏感資料都不需要進到 repo。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Day-2 維運</div>
### 不只部署，還能管理 { #manage-not-just-deploy }
`rodeo status`、`rodeo stop`/`start`、`rodeo set-password`、`rodeo install-extensions`、`rodeo clean`：lab 是你可以持續維運的環境，不是跑一次就丟的腳本。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">雲端</div>
### 從筆電部署到 AWS { #aws-from-your-laptop }
`rodeo up --profile harvester --target aws` 會建立 EC2 主機，自動選定 region、
subnet 與規格，再把 lab 建好。每台雲端主機預設 6 小時後自動終止，
除非你另外設定。macOS 或任何 Linux 都能用。請見[安裝](install.md)。
</div>

<div class="rc-card" markdown>
<div class="rc-card-label">Workshop</div>
### 每位學員一套 lab { #a-lab-per-attendee }
`rodeo fleet` 會在多台主機上平行建出同樣的 lab，每套 lab 都能個別查狀態、重試與診斷。
學員可以透過領取入口，用 workshop 代碼領到自己的 lab。請見 [Fleet](fleet.md)。
</div>

</div>

## 一個指令看效果 { #see-it-in-one-command }

<div class="rc-terminal" markdown>
<div class="rc-terminal-bar">
  <span class="rc-dot red"></span><span class="rc-dot yellow"></span><span class="rc-dot green"></span>
  <span class="rc-terminal-title">bash</span>
</div>
<div class="rc-terminal-body"><span class="rc-cmd">$</span> curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
<span class="rc-cmd">$</span> rodeo up
<span class="rc-val">→ checking host resources...
→ picking a profile that fits (harvester-ha, ~52 GiB)
→ generating secrets...
→ deploying...</span>
<span class="rc-cmd">✓</span> <span class="rc-val">Lab is up. URLs and credentials printed below.</span></div>
</div>

<p>準備好試試看了嗎？前往<a href="get-started/">開始使用</a>，或直接看 <a href="guide-rancher/">profile 指南</a>。</p>

<div style="margin-top:1rem">
<span class="rc-tag accent">GPL-3.0</span>
<span class="rc-tag">Python 3.10+</span>
<span class="rc-tag">KVM / libvirt</span>
<span class="rc-tag">底層使用 Ansible</span>
</div>
