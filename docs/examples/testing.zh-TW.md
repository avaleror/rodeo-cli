# 範例：測試與 CI

這個範例說明如何在自動化與測試情境中執行 rodeo-cli，從可在任何機器上執行的快速單元測試，到在 KVM 主機上執行的完整整合測試。

## 兩個層級的測試 { #two-tiers-of-testing }

| 層級 | 涵蓋範圍 | 執行位置 | 時間 |
|------|----------------|---------------|------|
| 單元測試／lint | 設定解析、CLI 介面、preflight 邏輯、labseed、階段事件模型 | 任何機器（不需要 KVM） | 約 1 分鐘 |
| 整合測試 | 完整部署 pipeline：kvm_host → vms → pxe_server → cluster → rancher | 自架的 KVM 主機 | 每個 profile 1–3 小時 |

兩者都需要。單元測試能抓到 Python 程式碼的回歸問題。整合測試能抓到真正的失敗：磁碟大小、網路、iPXE 鏈、etcd bootstrap。

## 單元測試 { #unit-tests }

可在任何作業系統上執行：不需要 KVM、libvirt，也不需要 sudo：

```bash
pip install -e ".[dev]"
ruff check rodeo tests
pytest tests/ -v
# Ansible role（在本機是選用的；在 CI 中是必要的）：
pip install "ansible-lint>=25" "ansible-core>=2.16,<2.19"
ansible-galaxy collection install -r rodeo/data/ansible/requirements.yml
ansible-lint rodeo/data/ansible
```

如果要保持主機乾淨（或主機上有運作中的 libvirt 會干擾
stop/start 測試），可以用非 root 使用者，在用完即丟的容器中執行同樣的檢查：
這和 CI 矩陣完全一致：

```bash
scripts/test-in-container.sh                 # Python 3.10 + 3.12，ruff + pytest
scripts/test-in-container.sh 3.12            # 單一版本
scripts/test-in-container.sh 3.12 -- -k labinabox   # 在 -- 之後傳入 pytest 參數
```

單元測試涵蓋的範圍：

| 測試檔 | 範圍 |
|-----------|------|
| `test_config.py` | plan 載入、Jinja2 算繪、`-P` 覆寫、`??` secret 解析器、驗證 |
| `test_runner.py` | pipeline 事件、Instruqt 防護、vars 檔產生 |
| `test_ansible_consistency.py` | profile ↔ Ansible 預設值 ↔ plan 範本的一致性 |
| `test_ansible_vars_contract.py` | vars 檔的鍵和 Ansible role 實際使用的相符 |
| `test_pxe_integration.py` | playbook 順序、pxe_server role、開機順序、JOIN VIP 防護 |
| `test_cluster.py` | ClusterPhase 輪詢迴圈、逾時、節點解析 |
| `test_rancher.py` | RancherPhase API 呼叫順序、逾時處理 |
| `test_plan_cmd.py` | `rodeo plan` 差異輸出 |
| `test_labseed.py` | profile 產生、`rodeo new`、secrets 產生 |
| `test_preflight.py` | `detect_host`、`recommend_profile`、RAM／磁碟檢查 |

### 不用 KVM 測試 CLI 介面 { #test-the-cli-surface-without-kvm }

`rodeo up --no-deploy` 會建立 lab 並執行 preflight，但會在部署 pipeline 開始之前停下來。適合在沒有 KVM 主機的 CI 中驗證上手流程：

```bash
rodeo up --no-deploy --yes --profile test --dir /tmp/test-lab
ls /tmp/test-lab   # 應該要有 rodeo-plan.yaml + definition.yaml
cat ~/.rodeo/secrets.yaml   # 如果原本不存在，會自動產生
```

### 測試設定載入與覆寫 { #test-config-loading-and-overrides }

```bash
# 試跑：載入並驗證 plan，印出會部署的內容
cd /tmp/test-lab
rodeo plan

# 覆寫一個值，確認有反映出來
rodeo plan -P resources.harvester.memory_mib=20480
```

## 整合測試：手動 { #integration-tests-manual }

在符合[裸機需求](bare-metal.md#host-requirements)的主機上，一次執行一個 profile：

```bash
# 先清除先前的狀態
rodeo clean --all --yes --secrets --force-network

# 部署並驗證（--no-tmux 會略過 session 包裝，供腳本執行使用）
rodeo up --profile test --yes --no-tmux
rodeo status

# 為下一次執行清理環境
rodeo clean --all --yes --secrets --force-network
```

### profile 測試順序（由小到大） { #profile-test-order-smallest-to-largest }

依這個順序執行，先在較小的 profile 上快速發現失敗，再投入時間跑較大的：

1. `rancher`（約 10 分鐘）：最快；驗證不經 PXE 的開機 + Rancher 路徑
2. `test`（約 60 分鐘）：兩節點 Harvester；驗證完整的 iPXE 鏈
3. `harvester-ha`（約 90 分鐘）：三節點 Harvester；驗證 3 成員 etcd HA
4. `harvester`（約 120 分鐘）：完整 lab；驗證在 Harvester 之上的 Rancher 匯入

### AWS 實機冒煙測試 { #aws-live-smoke }

依基礎架構自動調整的主機情境（provision 或自帶主機）的手動檢查清單。只需要 AWS 帳密，
其他都不需要：預設 AMI（SLES 16 PAYG）不需要訂閱 Marketplace，
而建議的 `m8id.8xlarge` 支援 nested virt。

```bash
# 以控制端安裝 rodeo（install.sh；請見 ../install.md）
# Provision 路徑（recommended 等級 → harvester 用 m8id.8xlarge；會先檢查 region 容量）：
rodeo up --yes --profile harvester --target aws --instance-tier recommended
# 或自帶主機：SSH 到既有的 m8id.8xlarge（或類似規格），設定 deployment_target: aws，然後：
#   rodeo up --yes --profile harvester
```

**測試你自己的修改：** 執行個體會從 GitHub 安裝 rodeo-cli，所以
本機的工作目錄對它來說是看不到的：而且預設情況下，已經完成 bootstrap 的
主機會保留它第一次安裝時的程式碼。先 push，再傳入 ref：

```bash
rodeo up --yes --no-tmux --target aws --ref feat/my-fix
```

對於腳本或 agent 驅動的執行，`--no-tmux` 很重要：沒有它的話，`up` 會重新
在 tmux 裡執行，呼叫端永遠看不到輸出。要看的遠端日誌是主機上的
`/root/.rodeo/logs/aws-up.log`：比在本機看到的資訊多得多。

在 `kvm_host` 之後，在 KVM 主機上驗證：

1. `resources.harvester.disk_gb` 是 **500**，`resources.rancher.disk_gb` 是 **60**，這是每個節點固定的下限，不會依節點數縮放（plan / vars）
2. `storage.backend: nvme`，而且 `image_dir` 掛載在 instance-store NVMe 上（`findmnt`、`lsblk`）
3. guest 磁碟放在那個 `image_dir` 底下（而不是只在根 EBS 上）
4. Harvester VIP / `rodeo status` 健康；如果是 provision 建立的，用 `rodeo destroy --cloud --yes` 拆除

單元測試涵蓋：`tests/test_host_context.py`、`tests/test_kvm_host_nvme_storage.py`。

較長的 agent／維運檢查清單（單一主機 + fleet）：[aws-fleet-claude-test-plan.md](aws-fleet-claude-test-plan.md)。

## 整合測試：GitHub Actions（自架 runner） { #integration-tests-github-actions-self-hosted-runner }

整合測試 workflow 位於 `.github/workflows/integration.yml`。它在帶有 `self-hosted`、`kvm`、`sles16` 標籤的自架 runner 上執行。

### 手動觸發 { #trigger-manually }

從 GitHub UI：Actions → Integration tests → Run workflow → 選擇一個 profile，或留空代表全部。

從 CLI：

```bash
# 所有 profile（每晚執行的方式）
gh workflow run integration.yml

# 只跑一個 profile
gh workflow run integration.yml -f profile=test
```

### 每個 job 做什麼 { #what-each-job-does }

1. **執行前清理**：清除前一次執行留下的任何東西（`rodeo clean --all --yes --secrets --force-network`），並忽略失敗，讓損壞的前一次執行不會擋住下一次
2. **部署**：`rodeo up --profile <name> --yes`
3. **驗證**：`rodeo status`（斷言 VM 正在執行、VIP 連得到）
4. **執行後清理**：一律會執行，即使部署失敗也一樣

`max-parallel: 1` 確保共用的 KVM 主機上一次只跑一個 profile。`fail-fast: false` 表示即使有一個失敗，四個 profile 也都會執行，讓你看到完整的情況。

### 設定自架 runner { #setting-up-a-self-hosted-runner }

設定步驟請見 [CONTRIBUTING.md](https://github.com/avaleror/rodeo-cli/blob/main/CONTRIBUTING.md) 或 [GitHub Actions runner 說明文件](https://docs.github.com/en/actions/hosting-your-own-runners/adding-self-hosted-runners)。runner 必須：

- 執行過一次 `rodeo install-deps`（KVM、libvirt、ansible、kubectl）
- `rodeo` 執行檔在 PATH 上（透過 `install-deps --link`）
- runner 使用者有免密碼 sudo

### 每晚排程 { #nightly-schedule }

這個 workflow 也會透過 cron 觸發，在每晚 02:00 UTC 執行。執行失敗時，會在 `main` 分支上產生一個失敗的檢查。

## 測試特定 Ansible role 的修改 { #testing-a-specific-ansible-role-change }

修改 Ansible role（例如 `roles/kvm_host/tasks/firewall.yml`）時，單元測試抓不到回歸問題，因為它們不會執行 Ansible。合併之前必須跑一次實機整合測試。

role 修改的最低回歸測試：

```bash
rodeo deploy --from kvm_host --force   # 重新執行修改過的 role
rodeo status                           # 驗證叢集仍然正常運作
```

對 PXE 鏈（`pxe_server` role）而言，最低要求是從頭完整部署一次 `test` profile，因為只有 Harvester 安裝成功時，開機鏈才算端到端驗證過。

## 測試 definition.yaml 的修改 { #testing-definitionyaml-changes }

編輯 `definition.yaml` 之後，部署前先驗證：

```bash
rodeo plan --profile mylab   # 必須成功且沒有錯誤
```

接著用最小的對應 profile 測試：

```bash
rodeo clean --all --yes --secrets
rodeo up --profile mylab --yes
rodeo status
```

如果 definition 改變了節點數或 IP，整個部署都必須從頭執行，不要用 `--from cluster`，因為 VM 磁碟與 DHCP 租約的變更來自 `vms` 階段。

## 測試 secret 解析 { #testing-secret-resolution }

```bash
# 驗證所有 ?? 占位符都能解析
rodeo deploy --check

# 檢查占位符但不解析任何東西（如果 deploy 會拒絕，exit 2）
rodeo plan
```

`deploy --check` 會執行 preflight 檢查組（包括 secret 驗證），但不會啟動任何階段。它只會解析 `??key`，而且需要受信任的 lab（`rodeo trust`）。占位符必須寫在 `rodeo-plan.yaml` 裡：`-P` 與 `--paramfile` 的值不能包含 `??`。
