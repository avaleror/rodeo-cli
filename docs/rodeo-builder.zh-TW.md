# Rodeo Builder

**[→ 開啟 Rodeo Builder](../../builder/){ .md-button .md-button--primary }**

Rodeo Builder 讓你在瀏覽器中組出一個新的 rodeo：選擇 lab engine、排好
story 章節的順序、為章節文字加上翻譯標記與 story id，然後把結果
下載成一個 profile，再用 `rodeo` 安裝。

同一個頁面有兩種版本：

- **已發布版**（上方的按鈕）：由 GitHub Actions 隨文件一起建置的靜態頁面。
  你在那裡做的任何操作都不會離開你的瀏覽器；你會下載一個 zip。
- **即時版**，隨 rodeo-cli 一起安裝：`rodeo builder` 在你的
  機器上提供這個頁面，即時讀取資料，並加上 **Save to profiles**，直接把
  rodeo 寫進 `~/.rodeo/profiles/<name>/`。

```bash
rodeo builder                 # 開啟 http://127.0.0.1:8678/
rodeo up --profile <name>     # 在 Save to profiles 之後
```

在有 systemd 的 Linux 上，安裝程式已經把它設成 `rodeo-builder.service` 執行，
監聽所有網路介面，所以不用自己啟動，頁面就已經在 `https://<host>:8678/` 上；
請見[安裝](install.md#rodeo-builder-service)（`--no-builder` 會關閉它，
`--builder-host 127.0.0.1` 讓它只在本機）。使用 `--host 0.0.0.0`（或 `::`）時，
`rodeo builder` 會回應這台機器自己的主機名稱與網路介面位址
（每次啟動時讀取），以及任何 `--allow-host`。

## 你可以組出什麼 { #what-you-compose }

- **Lab engine。** engine 就是已安裝的 rodeo 類型（包含外掛提供的）；
  提供的部署目標與說明文件格式都是 rodeo 認得的那些，
  絕不是固定清單。`suse-virt`、`rancher` 與 `suse-edge`
  是 rodeo 自己的 engine，放在 **native** 卡片底下；`lab-in-a-box`（以及其他任何外部
  engine）則是旁邊的另一張卡片。選擇 native 會顯示 **Engine** 列（`suse-virt`、`rancher`、
  `suse-edge`）；選擇某個 engine 會顯示 **Profile** 列，列出該 engine 類型的每個內建
  profile 與其規格，預設的排第一；rodeo 的 plan 從選定的 profile 開始。engine 上的
  **(i)** 會顯示它的規格、它的 profile，以及它提供什麼（例如 Harvester、
  KubeVirt、Longhorn、Rancher、Fleet）。
  `suse-virt` 的 profile 有 `harvester`（預設）、`harvester-2n`、`harvester-ha`、
  `test` 以及 workshop 用的 profile。`lab-in-a-box` 可以建出 lab-in-a-box 能建的任何 lab；選擇它
  會顯示 **Lab** 列：
    - **create**（預設）會在頁面內開啟空白的 lab-builder；在那裡按「Send this
      lab」就會把 lab 交回來。
    - **modify** 會在頁面內開啟 lab-builder，並載入這個 rodeo 的 lab。在
      rodeo 有 lab 之前，它會呈現灰色並顯示「no lab selected」。
    - **examples** 會顯示 **Example** 列：一份清單，列出內建的 lab-in-a-box rodeo
      （`smlm-workshop`），以及 lab-in-a-box checkout 提供的範例 lab，包括
      release 附帶的與主機自己的（`EXAMPLE_LABS_DIR`，預設為
      `/etc/lab_creation/examples`）。只有 release 附帶的才會有星號與
      版本；太舊而無法說明範例來源的 release 則都沒有。每張卡片只顯示名稱，以及節點
      與叢集數量；篩選框可以縮小清單範圍，超過八個時清單會折疊。
      各列下方的方框會顯示所選 lab 的說明與 add-on。範例 lab
      的處理方式和上傳的 lab 檔案一樣，所以會以 `lab_in_a_box.lab` 進入 plan；
      內建的 rodeo 在每個分頁都會維持選取，所以 lab-builder 開啟的就是它的 lab。
    每種模式的方框都提供 **Open embedded lab-builder**，會在
    這個頁面內開啟 lab-builder，並載入該模式對應的 lab（create 時沒有，modify 時是 rodeo 的 lab，
    examples 時是選定的範例），以及 **Upload lab**，上傳 lab-in-a-box 能讀取的
    lab 定義檔（JSON 或 YAML；頁面用內建的
    `vendor/js-yaml.min.js`（MIT）解析 YAML）。create 與 modify 也提供 **Open lab-builder ↗**，
    在獨立分頁中開啟 lab-builder 並填入這個 rodeo 的 lab（當 lab
    太大、放不進連結時，只填入它的 add-on）；在那裡下載 lab，再上傳到這裡就能帶回來。

  **Provides** 列出 lab 能提供給章節的東西，來自 lab-in-a-box 的完整
  目錄：add-on、Kubernetes 叢集（`rke2`、`k3s`……）以及基礎架構
  （例如 `pxe`）。輸入名稱即可新增。add-on 會放進產生的 plan；
  Kubernetes 叢集與基礎架構則在 lab-builder 中設定（Embed
  here），由它的 lab 帶回來。有 lab 時，它會列出該 lab 的 add-on
  （在 VM 與 Kubernetes 叢集上）、Kubernetes 叢集類型以及基礎架構。

  帶回來的 lab（傳回的，或以 JSON 或 YAML lab 檔案上傳的）會成為 plan 的
  `lab_in_a_box.lab`（請見 plan 參考文件）；plan 的其他部分，例如
  匯入的 workshop 的變體與 `workshop:` 區塊，都會保留。之後它的 add-on
  就在 lab-builder 中編輯。

  那個 lab 中的帳密不會進入 plan。密碼、token、
  註冊碼、API key 與 secret key 欄位的值會變成 `??<field>`
  占位符，這些占位符會被加入 plan 的
  `operator_secrets`，讓 `rodeo up` 只詢問一次；通知訊息會列出它們的名稱。
  在 add-on 設定中直接寫死的節點名稱與位址（例如
  `client_registration_server: smlm.rodeo.lab`）會變成 `${node_fqdn:smlm}`／
  `${node_ip:smlm}`，由 rodeo 填入每台主機與每個執行個體的值；
  與 rodeo 原本那個 lab 相比沒有變更的設定會維持原樣。看起來像
  lab 位址、但不屬於任何節點的位址，會列在面板中供你檢查。

  有 lab 時，面板會以唯讀方式列出它的節點（位址、vCPU、RAM、add-on、叢集）
  與 Kubernetes 叢集，並標示出 rodeo 會部署但不會
  完全驅動的部分：Harvester 節點與透過 PXE 開機的節點。它也會列出 builder 的
  lab-in-a-box release 不認得的 lab 欄位（它的 schema 與目錄一起讀取），
  這些欄位會讓 `rodeo up` 拒絕執行。在命令列中，
  `rodeo new <name> --from-lab-json <file>` 會做同樣的匯入。
- **從 workshop 開始。** 在章節庫上方，有一份完整 workshop 的清單，
  可以作為 rodeo 的起點：內建的範例、隨
  profile 提供的章節來源、目錄中每一個已發布的 rodeo，以及內建的 lab-in-a-box
  rodeo（也在 lab engine 的 **examples** 列底下）。**Start from**
  會把 rodeo 的 lab 與章節換成該 workshop 的：它的 lab engine 與
  profile（例如 `suse-virt` 搭配 `virt-workshop` profile，練習題需要它的
  額外設定）以及它所有的章節（**Undo** 可以還原）。
  目錄中的 rodeo 會在你以它為起點時抓取，所以它會帶來發布時的
  所有內容：它的 plan、它的 lab-in-a-box lab、它的章節與其
  檢查，以及它的翻譯，包括語言分支。已發布版頁面
  無法抓取它；在那裡，它的項目會顯示 `rodeo new <name> --from <repo>`。
- **章節。** 章節庫列出每個 workshop 的章節，可以加進 rodeo：內建
  範例的 `story/` 目錄、章節放在自己
  repository 中的 workshop（列在 `rodeo/builder/chapter_sources.yaml`：SMLM workshop 的
  Instruqt track、SUSE Virtualization Rodeo 的 Instruqt track，以及 Virtualization
  workshop 的練習題；每個項目的 `ref` 是 branch、tag 或完整的 commit id，用來固定
  某個 release；沒有 `engine` 與 `profile` 的項目，對應的是有章節但沒有
  rodeo-cli 設定的 repository，只會把它的章節加進章節庫，不能作為任何 rodeo 的起點）、
  目錄中每一個已發布 rodeo 的 `story/`（`rodeo/data/catalog.d/`，除非
  `chapter_sources.yaml` 已經列出它的 repository；它的翻譯會一起
  帶過來，來自 `story/strings/` 與各個語言分支，所以加入它的某個
  章節時，也會加入你還沒有的那些 span 的翻譯），以及你用 **+ New**
  建立的章節。Workshop 預設是折疊的：點擊 workshop 的標題即可
  顯示它的章節。旁邊的 **(i)**（滑鼠移上去，或點擊讓它保持開啟）
  會顯示章節的來源、它們執行的 lab engine 與 profile、
  章節數量與時間，以及它們需要什麼。**+ all** 會把它的章節加到
  rodeo 中。章節卡片會顯示標題；滑鼠移上去可以看到它的
  時間與需求，或打開搜尋旁邊的清單按鈕，讓每張卡片都顯示這些資訊。
  旁邊的漏斗按鈕只會顯示適用於這個 lab
  engine 的 workshop、這個 lab 能執行的章節，或還沒加進 rodeo 的章節。
  把章節拖進 rodeo（清單上任何位置都可以），或直接點擊；再點一次
  章節就會把它移除。點擊 rodeo 中的某一列，新章節就會插在它
  後面，而不是加在最後。用拖曳或 Alt+↑/↓ 重新排序。移除
  章節或 span 後，幾秒鐘內會提供 **Undo**。當每個章節都
  來自在另一個 engine 上執行的 workshop 時，會出現橫幅提議切換到該 engine。需要 lab 沒有提供的東西的章節會被標示出來，
  若是 lab-in-a-box，點一下就能加上缺少的 add-on。附帶
  檢查腳本的章節會把腳本一起帶過來。新章節的 **Requires from the lab** 欄位
  會在你輸入時建議 engine 真正提供的東西：native engine 的
  功能，以及 lab-in-a-box 的目錄（add-on、基礎架構服務、
  Kubernetes 叢集類型）。如果無法讀取 lab-in-a-box 目錄，
  頁面會說明原因。`rodeo builder` 伺服器對每個 checkout 只讀取一次目錄與 lab schema，
  只有在 checkout 的檔案變更時才會重新讀取。頁面
  啟動期間，會有一則提示說明正在載入；如果頁面無法啟動（腳本錯誤、
  builder 資料無法載入），提示會說明原因並提供 **Reload**，
  30 秒後還會列出仍在等待伺服器回應的呼叫。沒有符合的項目時，按 Enter 會把你輸入的內容加為新需求：
  也就是代表某個還沒有任何 engine 提供的東西的占位符，例如 `missing_addon`。這種
  需求只會把章節標示為「work needed」（也會依名稱列在
  下載內容的 README 中）；它絕不會擋住 rodeo，也絕不會進入 lab。
- **匯入。** 章節庫上方的 **Import** 可以從網址加入 workshop：一個 git
  repository 或其中的某個目錄（`https://<host>/<owner>/<repo>/tree/<ref>/<dir>`）。
  builder 會偵測格式（rodeo 的 `story/`、Instruqt track 的
  `<dir>/*/assignment.md`，或 markdown 的 `<dir>/*.md`），並把它的章節列成一個
  新群組；**×** 會再把群組移除。已發布版頁面透過 GitHub API 讀取公開的
  github.com repository（未驗證時每小時 60 次請求）；
  rodeo builder 伺服器可以 clone 任何公開的 https repository（shallow
  且 sparse，不使用帳密）。匯入的群組會和草稿一起保存。
- **Story。** 章節上的 **Edit** 會開啟 story 編輯器（有 span
  警告的列會顯示警告數量）。選取文字後點擊 **Tag selection**、使用工具列，或按
  Alt+T / Alt+I / Alt+N，把它標記為 Translatable、Don't translate 或 No-lang。
  **Story**（Alt+S）會標記要為學員重新改寫的文字：它會採用章節的
  story，或詢問 story 的名稱，並建議其他章節所用的那個。
  當章節講述的 story 不只一個時，Review 會發出警告並提供 **Make all
  the same story**。在 span 上按兩下可以變更它的類型、
  語言、id 或 story id。id 遵循 rmstory 的規則（`<chapter>.N`，
  巢狀時為 `<parent>.N`），編輯器顯示的警告與
  `rmstory validate` 相同。原始碼與預覽並排顯示，捲動時
  同步；預覽會把章節以 Markdown 或 HTML 呈現（只允許安全的標籤，
  圖片以連結呈現），並標示出其中的 span。Ctrl+Z / Ctrl+Shift+Z 可以復原與重做；
  **Cancel** 會關閉編輯器並捨棄變更，**Done** 則保留變更。
- **翻譯。** story 編輯器的 **translations** 分頁會依 rodeo 的每種語言，
  列出所有可翻譯的 span。**Add new language…**（在這裡，或
  在 rodeo 分頁的 Language 清單最後，該清單只顯示 rodeo 自己的
  語言）會詢問語言代碼（`es`、`pt-br`：命名方式與 Publish
  放置該語言的語言分支相同），然後以空白開始、以另一份
  翻譯的草稿副本開始，或在即時版頁面上用 rmstory engine 翻譯；它也可以
  成為 rodeo 的語言（`story.language`）。點擊儲存格即可撰寫或審閱
  翻譯；巢狀的 span 會以 `<span id="..."/>` 保留在其中。下載內容
  會把翻譯放在 rmstory 的翻譯儲存區 `story/strings/` 中，
  `rodeo story render --language <lang>` 會使用它們（仍然缺少的 span
  則搭配 `--engine`）。

**Review and download**（步驟 03）會在你下載之前檢查 rodeo：
lab 是否涵蓋每個章節、span id 是否有效（每個警告都有 **Fix**
按鈕）、zip 中確切有哪些檔案（點擊 **N files in
<name>.zip** 時列出），以及安裝它的指令，可以直接複製。缺少的 lab 功能與 span 警告絕不會擋住下載。

頁面顯示的每個警告與錯誤都有一個 **×** 可以關閉，Esc
則會關閉頁面底部的訊息。關閉代表「已讀」，而不是
「已修正」：上方的計數仍會把它算進去，內容不同的警告
仍會顯示，重新載入後也會全部再出現。

頁面會在這個瀏覽器中保存你的 rodeo 草稿。重新載入後，它會提議
還原草稿。

### 在即時版 builder 上搭配 rmstory { #with-rmstory-on-the-live-builder }

當 `rmstory` 安裝在執行 `rodeo builder` 的地方時（`sudo rodeo install-deps
--story`），即時版頁面也會使用它：

- **Translate missing**（translations 分頁）：用 rmstory engine 填入所選語言缺少的
  翻譯（`rmstory extract --engine <e> --to <lang>`）。
  這些翻譯會以 `draft` 狀態儲存；你寫過或審閱過的內容會保留。rmstory 也會
  為沒有名稱的 span 加上 id（`<chapter>.N`）。
- **Validate**（步驟 03）：對章節執行 rmstory 自己的 `validate`。
- **Draft with an engine**（New chapter）：能產生文字的 engine
  （`rmstory engines` 顯示「translate + generate」，例如 `claude-code`、
  `ollama` 或 `gemini`）會根據你的描述草擬章節，並告知它
  lab 提供什麼、它前面有哪些章節。它的 span 會重新編號為
  `new-chapter.N`；建立章節之前請先審閱草稿。
- **New story**（rodeo 分頁）：用不同的 story 講述同樣的練習題。你
  用 span 標記（story 編輯器）指出哪些是哪一類：
  - **story**：帶有 story id（`hist`）的 span：會被重新改寫；
  - **exercise**：沒有 story id 的可翻譯 span：會被翻譯，但絕不會
    被改寫；
  - **fixed**：不翻譯（`no`）或 no-lang 的 span，以及 span 以外的文字：一字不改
    地保留。

  說明新的 story 要講什麼，並選擇能產生文字的 engine；
  `rmstory generate rewrite --theme` 會改寫 story span，exercise
  span 則絕不會送到 engine。改寫後的章節（id 為 `<chapter>-<theme>`，
  story span id 為 `<theme>.<id>`；exercise 與 fixed span 保留它們的 id 與
  翻譯）會成為一個章節庫群組，並取代 rodeo 的章節；
  **Undo** 會把原本的章節放回去。在你下載、儲存到 profiles 或發布之前，
  它們只存在於這個瀏覽器中（頁面的草稿）。

engine 會從 `rodeo builder` 執行時的環境讀取 key（例如
`DEEPL_AUTH_KEY`、`GEMINI_API_KEY`、`RMSTORY_OLLAMA_URL`，或 `claude-code` 使用已登入的 `claude`
指令），或從儲存的帳密讀取：Settings 頁面為每個 provider 儲存
一份（*Google Gemini API key*、*DeepL API key*……），各自以自己的
密碼加密。需要 key 但兩者都沒有的 engine 會以紅色列出，標示為
*no credential*；執行它時會說明如何新增，並連到已選好該類型的 Settings
表單。有儲存的帳密時，頁面會詢問要用哪一份
（有多份時）以及它的密碼；key 會在伺服器上解密，
只交給 rmstory，絕不會交給頁面。儲存的帳密在部署
對話框的帳密可用的地方都能用（在 loopback 上的 builder，或在網頁伺服器的登入之後）。
已發布版頁面只能手動撰寫翻譯。

右側面板可以設定名稱、story 標題、語言、部署目標與
**Story told to students**（當章節有帶 story id 的 span 時顯示：*All
stories together* 會呈現完整的章節；選擇某個 story 則只依序呈現該 story 的
span），並預覽下載內容中的每個檔案。**plan.yaml** 是一個
編輯器：修改任何內容，下載時都會保留；`name`、`deployment_target`
與 `story` 會與 rodeo 分頁保持同步，**Reset to generated** 會捨棄你的
修改。變更 lab engine 會在詢問後重新產生 plan。

## 基礎架構與說明文件 { #infrastructure-and-instructions }

頁面把 lab（**Infrastructure**：engine、基礎 profile、plan、
definition、lab-in-a-box lab）與學員閱讀的內容（**Instructions**：
章節、story、翻譯、檢查）分開。步驟 03 可以下載整個
rodeo（**Download: All**），或只下載其中一部分：**Infrastructure definition only**（`<name>-infrastructure.zip`，
可用 `rodeo new --from-zip` 安裝）或 **Instructions only**
（`<name>-instructions.zip`：`story/` 與 `checks/`，排列方式可直接推送成
內容 repository）。

**Where students' instructions come from**（rodeo 分頁）可以是 *This rodeo's chapters*
（預設：中間欄的章節，也就是下載內容中的 `story/`），或 *A
repository*，讓 plan 指向放在自己 repository 中的說明文件
（例如 Publish 建立的 `-instructions` repository）；這時章節欄
會說明部署的說明文件來自那裡。網址（`https://<host>/<owner>/<repo>[/tree/<ref>/<path>]`）
與格式會成為 plan 的 `content:` 區塊。`rodeo up` 會在 lab 起來之後呈現它們，
為主機上的每個使用者安裝 repository 的 `checks/`，並
執行它的 `custom/scripts/`，而 `rodeo content apply` 可以在執行中的 lab 上更新它們，不需要
重新部署（請見 [plan 參考文件](reference/plan.md#content-the-labs-instructions-apart-from-its-infrastructure)）。

## 安裝你下載的內容 { #install-what-you-downloaded }

zip 中包含一個 profile 目錄 `<name>/`，裡面有 `rodeo-plan.yaml`、
章節（`story/NN-<id>.md`）、`story/chapters.yaml`（每個章節的分鐘數、需求與檢查
腳本）、每個 story 一份 `story/stories/<story id>.yaml`、
`checks/check-<id>.sh` 骨架、一份列出確切指令的 README，以及當
rodeo 從某個 profile 開始時（`suse-virt`、`rancher`、`suse-edge` 或匯入的
lab-in-a-box rodeo），一份列出該基礎 profile 的 `builder.yaml`。

```bash
rodeo new <name> --from-zip <name>.zip
rodeo up --profile <name>
```

`--from-zip` 會複製基礎 profile，把 zip 中的檔案覆蓋上去，並寫入
`~/.rodeo/profiles/<name>/`。新的 lab-in-a-box rodeo 自帶
`definition.yaml`，不需要基礎 profile。`--from <profile>` 會覆寫基礎 profile，
`--force` 則會取代既有的 profile。zip 中只能包含
builder 會寫出的檔案；其他任何東西（其他路徑、連結、過大的檔案）都會在
寫入任何東西之前被拒絕。`rodeo builder` 中的 **Save to profiles** 不經過 zip，
做的是同樣的事。

已發布的 rodeo（請見[發布到 GitHub](#publish-to-github)）以同樣的方式
從它的 repository 安裝，目錄中的 rodeo 則只要名稱就能安裝：

```bash
rodeo new <name> --from https://github.com/<owner>/<repo>[@<ref>]
rodeo up --profile <catalog name>     # 第一次使用時抓取並安裝
rodeo profiles                        # 也會列出目錄
```

repository 會以 shallow 方式、不使用帳密抓取，而且只會安裝
builder rodeo 可以包含的檔案；`~/.rodeo/profiles/<name>/rodeo.lock`
會記錄 repository、ref 與 commit。`--force` 會重新抓取。

## 即時版 builder { #the-live-builder }

`rodeo builder` 提供 `rodeo/builder/htdocs/`，並以
`rodeo/builder/api.py` 回應頁面的請求（`rodeo/builder/server.py`，只用標準函式庫）。
它會連結並嵌入 lab-in-a-box 已發布的靜態 lab-builder，或
`--lab-builder-url` 指定的那個。加上 `--start-lab-builder` 時，它會在旁邊的
`--lab-builder-port` 上啟動 checkout 自己的動態 lab-builder（`webui/run-local.py`），
並以瀏覽器存取 builder 時所用的主機名稱開啟（若
checkout 沒有或無法啟動，則改用已發布版）。那個 lab-builder 沒有登入也沒有 token，
所以你瀏覽的任何網站都能透過它儲存 lab：它預設關閉，而且只
允許在 loopback 的 `--host` 上使用。
啟動時，它會抓取章節來源（無法抓取的來源會被略過，
並發出警告）以及 lab-in-a-box 目錄：`--labinabox <checkout>`，
否則用 `RODEO_LABINABOX_PATH`，再否則用 lab-in-a-box 的最新 release，以部署時
相同的方式抓到 `~/.rodeo/vendor/lab-in-a-box/`。

在 loopback 上，它使用一般的 HTTP：沒有任何東西離開這台機器。在其他任何
位址上，它使用 HTTPS，搭配 `--tls-cert` 與 `--tls-key`，或在沒有指定時，使用
由你自己的 CA 簽署的憑證，這個 CA 只會在
`~/.rodeo/builder/tls/` 中建立一次（`ca.pem`，key 權限 0600）。憑證會列出這台主機的
FQDN 與短名稱、它的位址以及每個 `--allow-host`，並在
缺少某個名稱或剩下不到 30 天時重新產生。在每個瀏覽器中
匯入一次 `ca.pem`（從 `https://<host>:<port>/ca.pem` 或檔案匯入），之後這個 CA
簽署的每個 builder 都會被信任；在那之前，瀏覽器會對憑證發出警告。
這個 CA 有名稱限制，只限於建立它時那些名稱所屬的網域，所以之後
超出範圍的名稱會在啟動時回報，並被瀏覽器拒絕：刪除
`ca.pem` 與 `ca.key` 就能建立新的 CA（並重新匯入）。

| 選項 | 預設值 | |
|---|---|---|
| `--host`, `--port` | `127.0.0.1`, `8678` | 監聽的位置（`--port 0` 會挑一個空閒的 port）。 |
| `--expose` | 關閉 | 非 loopback 的 `--host` 必須加上這個選項，並透過 HTTPS 提供：任何連得到它的人都能以你的身分儲存 profile。**Publish** 這時會使用 github.com 的頁面，絕不使用這台主機的 GitHub 登入；**Deploy** 與儲存的帳密需要登入。 |
| `--allow-host NAME` | | 額外接受的 `Host` 名稱（搭配 `--expose` 時用戶端使用的名稱或 IP）；也會放進產生的憑證中。 |
| `--tls-cert FILE`, `--tls-key FILE` | rodeo 自己的 CA | 用來提供 HTTPS 的 PEM 憑證（含憑證鏈）與 key：可以是你的 CA 簽發的，或 certbot 的 `fullchain.pem` 與 `privkey.pem`。 |
| `--labinabox` | 最新 release | 提供目錄與 lab-builder 的 lab-in-a-box checkout。 |
| `--lab-builder-url URL` (`RODEO_LAB_BUILDER_URL`) | 已發布的 lab-builder | 你已經在執行的 lab-builder（例如在自動化節點上），讓頁面連結並嵌入；不會啟動任何東西。 |
| `--start-lab-builder` | 關閉 | 啟動 checkout 自己的動態 lab-builder。僅限 loopback 的 `--host`。 |
| `--lab-builder-port` | `8677` | `--start-lab-builder` 啟動的 lab-builder 所用的 port，位址與 `--host` 相同。 |
| `--source ID=CHECKOUT`, `--no-fetch-sources` | 全部抓取 | 本機的章節來源，或都不用。 |
| `--open/--no-open` | 開啟 | 在瀏覽器中開啟頁面。 |

寫入操作有防護：伺服器只回應 `Host` 是允許名稱的請求
（預設為 loopback 名稱），所以其他網站無法透過 DNS
rebinding 連到它，而 **Save** 會送出只有該頁面才有的每次執行專屬 token。
頁面在 Content-Security-Policy 下執行，只允許它自己的檔案（字型
放在 `htdocs/fonts/`）以及 lab-builder frame。

## rodeo 部署到哪裡 { #where-a-rodeo-deploys }

**Deployment target** 的選擇就是 plan 的 `deployment_target`。頁面會依實際用途為
內建選項命名：**This host (localhost)** 是 `baremetal`，也就是 builder
本身執行的那台主機——plan 中的值沒有改變，只有標籤說明它會落在哪裡。

**A KVM host of mine (SSH)** 是 `deployment_target: remote`：builder 會把 rodeo 送到
你擁有的主機上，並在那裡執行 `rodeo up`（請見
[remote_host](reference/plan.md#remote_host)）。只有在你於 Settings 頁面儲存了一份
**KVM host (SSH)** 帳密（主機、登入帳號與私鑰）之後，才會提供這個選項，因為
沒有它就無處可部署。接著部署對話框會詢問要用哪台主機，以及那份
帳密的密碼；key 會以 `.rodeo-remote-key` 寫進 lab 目錄
（權限 600），絕不會離開 builder 的主機。

## 不儲存直接部署 { #deploy-without-saving }

**Deploy**（頁首）會在 builder 的主機上依照頁面目前的樣子執行 rodeo，
不需要先儲存到你的 profiles。`rodeo up` 需要 root 權限：
builder 以 root 執行，或使用者有免密碼 sudo（`sudo -n true`）。
否則，以及在已發布版頁面上，這個按鈕會呈現灰色，滑鼠移上去時會說明原因。

- 在 `--expose` 的 builder 上，部署與使用儲存的帳密需要
  `/etc/rodeo-builder/htpasswd` 中的登入帳號：第一次按 **Deploy** 會讓
  瀏覽器詢問帳號（按 Cancel 頁面會維持原樣），之後瀏覽器會在每個請求中
  送出它。builder 會在 5 分鐘後
  再次檢查登入。builder 提供的是一般 HTTP，所以登入資訊會以未加密的方式
  經過網路。在 loopback 上不會要求登入。

- 對話框會清楚說明 rodeo 尚未儲存，並在 **Deploy without saving** 旁邊提供 **Save to
  profiles and deploy**。
- 部署會從 `~/rodeo-labs/<name>-unsaved-<UTC time>/` 中的副本執行 `rodeo up --yes --no-tmux --dir <lab>`
  （基礎 profile 加上頁面的檔案，排列方式與 Save 相同），輸出寫到 `<lab>/.builder-deploy.log`。
  rodeo 能產生的 secret 會放進 lab 的 `.rodeo-secrets.yaml`；只有你才有的 secret 
  （`operator_secrets`）會在對話框中詢問，並寫入
  `~/.rodeo/secrets.yaml`，之後不會再顯示。
- 橫幅中的 **Log** 會在部署執行時保留完整輸出，最多 100000
  行，並自動跟隨最新內容，除非你往上捲動。頁面只會要求
  它還沒看過的位元組，所以長時間的部署不會在每次輪詢時重新傳送整份 log；
  完整的輸出一律在 `<lab>/.builder-deploy.log` 中。
- 執行期間與結束之後，都會有一個橫幅持續顯示 lab 目錄、
  log、**Stop**（像 Ctrl-C 一樣中斷 `rodeo up`）與 **Save to profiles**，
  而且在你儲存之前會一直提示 rodeo 尚未儲存。部署
  結束後，**×** 可以關閉橫幅；執行期間沒有 **×**，因為 **Stop** 與
  **Log** 是執行中的部署僅有的控制項。一次只能執行一個部署；
  要重跑或接續，請在 lab 目錄中執行 `rodeo up`。

## 設定與儲存的帳密 { #settings-and-stored-credentials }

即時版 builder 頁首右側的 **Settings** 會在新分頁中開啟
Settings 頁面：位於 builder 本身的位址與 port 上的 `/rodeo-builder/settings/`。
它會保存你部署時用的帳密——SCC 註冊碼、registry 與
Application Collection 登入帳號、AWS key、SSH key、GitHub token，或任何
`key: value` 欄位——在該主機上加密保存，每份帳密一個檔案，放在
root 的 `~/.rodeo/vault/`，也就是 builder 讀取的儲存區。

這個頁面由 `rodeo builder` 本身提供，並使用它的 API（`GET vault`、
`POST vault-add` 與 `vault-delete`，搭配頁面的 token）。新增或刪除
帳密需要登入：第一次時，瀏覽器會詢問登入檔中的使用者
（`rodeo logins add <user>`；htpasswd 格式的 bcrypt 雜湊，
放在 `/etc/rodeo-builder/htpasswd`，否則放在 `~/.rodeo/builder/htpasswd`，請見
[安裝](install.md#settings-page)），而 `--expose` 的 builder
在列出任何東西之前就會詢問。不在 loopback 上時，builder 使用 HTTPS，所以登入資訊
與值絕不會以明文經過網路。

- 每份帳密都有自己的密碼，至少 8 個字元，在
  新增時、每次使用時以及刪除時都會詢問：密碼錯誤的刪除
  會被拒絕。頁面不會保存密碼或值：
  表格會顯示每份帳密的名稱、應用程式、engine（這裡儲存的帳密
  為 `rodeo`）、是否加密、新增時間與最後
  使用時間，絕不顯示它的值。rodeo 帳密的應用程式就是它的類型。
- 其他 engine 的帳密也會列出並可使用，並標示其 engine 的
  名稱：目前是 lab-in-a-box engine 的，來自 `/etc/lab_creation/credentials/`；
  它們的應用程式是檔名 `<application>-<account>.yaml` 中
  第一個 `-` 之前的部分（`aws-tmm.yaml`：`aws`）。
  它們在 rodeo 中是唯讀的（沒有 **Delete**）。未加密儲存的帳密
  不需要密碼即可開啟；加密的帳密會詢問它的 passphrase。
- 密碼與登入從連續第五次失敗開始，等待時間會越來越長：1 分鐘，
  然後 2、4……每次嘗試之間最長 1 小時。成功一次就會重設計數。
- 同一組 API 也提供給腳本使用：`POST /api?action=vault-add`
  （`{name, type, fields, password}`）與 `vault-delete`（`{id, password}`），搭配
  頁面的 `X-Rodeo-Builder-Token`，以及一組依
  `/etc/rodeo-builder/htpasswd` 檢查的 `Authorization: Basic` 登入。沒有登入時會回應 `401`。
- 在部署對話框中，lab 詢問的每個 secret 都會提供 **Use stored…**，列出
  能填入它的帳密。輸入密碼後，頁面會填入那個
  secret，以及同一份帳密能提供的其他所有 secret（AWS 帳密
  會同時填入 `aws_access_key_id` 與 `aws_secret_access_key`）。通用
  帳密會填入名稱與其欄位名稱相符的 secret。
- 填入的值和手動輸入的值去處相同：放進 `~/.rodeo/secrets.yaml` 供
  `rodeo up` 使用。刪除儲存的帳密不會把它們從那裡移除。
- 頁面也會列出 root 的 `~/.rodeo/secrets.yaml` 中已有的 key，只有名稱
  而且是唯讀的：`rodeo up` 會直接讀取那個檔案。在部署對話框中，
  **Use stored…** 會把它們列在 *secrets.yaml* 底下（與 secret 名稱有共同單字的
  排在前面）。選擇其中一個不需要密碼，並會鎖住輸入欄
  （「from secrets.yaml: <key>」，按 **Clear** 可改為手動輸入）；builder 會在它的主機上複製
  這個值，所以它絕不會傳到瀏覽器。

## 發布到 GitHub { #publish-to-github }

**Publish** 會把 rodeo 放進你的一個公開 GitHub repository，並在
rodeo-cli 上開一個 pull request，把它加入 rodeo 目錄
（`rodeo/data/catalog.d/<name>.yaml`：名稱、標題、repository、ref、engine、
語言）。repository 中放的是下載內容的檔案。

- **在 loopback 上的 `rodeo builder`** 會使用它主機上已有的 GitHub 登入，
  但不會儲存它：`GH_TOKEN` 或 `GITHUB_TOKEN`，否則用 GitHub CLI 的登入
  （`gh auth token`），再否則用 git 儲存的 github.com 帳密。都沒有時，**Log
  in to GitHub** 會執行 `gh auth login --web`，並顯示一次性代碼，讓你
  在 github.com/login/device 輸入；沒有 `gh` 的話，請安裝它、執行 `gh auth login`，或
  在執行 `rodeo builder` 之前設定 `GH_TOKEN`。發布時會建立
  `github.com/<you>/<name>`、在 `main` 上推送一個 commit、在你不擁有
  rodeo-cli 時 fork 它，並開出目錄的 pull request。**Update my existing
  repository** 會推送一個取代所有檔案的新 commit（repository 的
  `.github/` 會保留），而且不開 pull request。`RODEO_CATALOG_REPO` 可以讓
  pull request 指向另一個 repository。

  翻譯會放到語言分支。`main` 放的是原文語言
  （`story.language: en`）。每種翻譯語言都有一個以它命名的分支：
  `main` 加上該語言的 `story/strings/<lang>/`，並設定 `story.language:
  <lang>`。更新時，既有的語言分支只會取代它的
  `story/strings/<lang>/`。`main` 也會加上
  `.github/workflows/rodeo-languages.yml`：每次推送到 `main` 時，它會為每個落後 `main`
  的語言分支開一個 `main` → `<lang>` 的 pull request
  （也就是 `rodeo story check` 的檢查），你再在其中解決衝突。
  Publish 會允許 repository 的 Actions 開這些 pull request（**Allow GitHub
  Actions to create and approve pull requests**）。結果會列出語言
  分支，並對任何被略過的東西發出警告：workflow（當 GitHub 登入
  無法寫入 workflow 時；它需要 `workflow` scope）；上述設定（當 GitHub
  拒絕時）；無法推送的語言分支。
- **已發布版頁面**（以及 `rodeo builder --expose`）會連到 github.com
  的頁面，使用你瀏覽器的 GitHub 登入，如果你還沒登入，GitHub 會要求你登入：
  建立 repository、上傳解壓縮後的下載內容（或 `git
  push`），並加入目錄檔案，這會 fork rodeo-cli 並以你的帳號開出 pull
  request。

## 已發布版頁面如何建置 { #how-the-published-page-is-built }

已發布版頁面是即時版頁面的靜態子集。`scripts/build-builder-static.py`
會把 `rodeo/builder/api.py` 回應的內容（來自
`rodeo/data/` 的 engine 與內建章節、章節來源的章節，以及 lab-in-a-box
最新 release 的目錄）嵌入頁面，並把它標示為靜態，這會隱藏 **Save to
profiles**；文件 workflow 會把它發布在 `builder/`。建置時會以 shallow、sparse 的 git clone
抓取每個章節來源（只抓章節檔案與檢查
腳本），如果有任何一個無法抓取，或無法讀取 lab-in-a-box 目錄，
建置就會失敗。在本機建置：

```bash
python3 scripts/build-builder-static.py --output /tmp/builder --labinabox /path/to/lab-in-a-box
python3 -m http.server -d /tmp/builder 8000
```

`--source <id>=<checkout>` 讓某一個章節來源使用本機 checkout，
`--no-fetch-sources` 不使用章節來源建置（離線），`--no-labinabox` 則
不使用 lab-in-a-box 目錄建置（頁面會顯示目錄無法使用）。
