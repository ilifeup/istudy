# iSTUDY｜版本生命週期、發行治理、系統盤點、升級與災難復原最高 SOP

**文件代碼**：ISTUDY-RELEASE-SOP｜**版本**：v2.0｜**修訂日期**：2026-11-04｜**適用**：每次開發版／正式版的部署、原地升級、跨版升級、Client 發行、回滾、災難復原與交接。

> **總方針**：版本升級不是把 ZIP 覆蓋進主機，而是一次可審計、可驗證、可復原的**發行事件**。每次皆先確認目標與授權，再用 iSTUDY 原有工具取得真實基線，盤點跨版本變動，驗證發行產物與資料一致性，證明可以退回，才進行升級。升級完成也必須持續觀察、保留回滾點和結案證據。
>
> **不可混淆**：GitHub CI PASS ≠ Ubuntu 實機 PASS；UEM Quick PASS ≠ Full／當下 Doctor PASS；備份存在 ≠ 備份可用；資料庫可還原 ≠ 整套系統能回滾；Dev63 ZIP 完整 ≠ Dev63 已編譯／安裝；一台 Client 正常 ≠ 各平台都正常。

## 0. 文件定位、權限、適用邊界

### 0.1 文件層級與必讀順序

1. [根目錄 AGENTS.md](../AGENTS.md)：最高 AI 權限、禁止未核准 PR、不得擅自部署與改動資料。
2. **本文件**：跨版本通用的全生命週期治理和強制關卡；適用全部未來升級。
3. [C 混合方案打包規範](ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)：原始碼 ZIP／正式成品／執行環境明確分離。
4. [Dev62 部署及回滾文件](DEPLOY_DEV62.md)／[Dev62 測試結果](TEST_RESULTS_DEV62.md)：歷史實作先例，不是可跨版本重用的無條件執行指令。
5. 每版專屬的 **Release Plan、Migration Matrix、Compatibility Matrix、Rollback Runbook、驗收報告與升級紀錄**。Dev63 另參照 [決策進度中心](DEV63_STATUS_AND_DECISIONS.md)。

如有衝突，**安全界線與使用者最新明確授權優先；不可因新版工具或口頭「可以升級」跳過檢查**。SOP 是操作規範，**不是部署自動授權**。

### 0.2 決策與授權分層

| 操作 | 預設政策 |
|---|---|
| 讀 GitHub 文件、檢查現況、分析 UEM 報告 | 可在使用者交付資料／授權範圍內處理 |
| 執行 UEM Quick／Doctor／Full | 先核對安裝版能力；Full 涉及試轉檔／文件轉換，需安排合適時間 |
| 將 SOP／進度更新至 GitHub main | 依使用者指定文件／分支執行，並回報 commit；**不得因此開 PR** |
| 建立 PR、合併、部署、資料庫 migration、刪除資料、覆寫安裝檔 | **各自需要明確授權**，任何一項不能推定另一項 |
| 原地升級與隔離升級 | 每次明確選擇；即使原地升級，也要做**臨時隔離還原測試** |
| 缺備份／無法回滾／未知風險 | **STOP，先補證據**；不可為進度略過 |

### 0.3 發行目標：從功能完成到可維護系統

每個版本必須管理 **原始碼、執行產物、資料、服務、使用者端、網路安全、還原能力、後續觀察** 八個面向。工程進度百分比或測試數不代表部署就緒。發行中不得隨手修改程式；問題集中列入批次修正、重新產生完整同源產物與 SHA-256，再走關卡。

**角色**：AI 負責提出檢查結果、差異和指令；現場執行者確認現況、保管金鑰與執行命令；升級批准者對 G5 的特定版本、主機與時窗核准。AI 不得聲稱已操作未接觸的 Ubuntu 主機。

---

## 1. 版本生命週期與決策關卡（Release Gates）

| 關卡 | 發行階段 | 主要證據／交付 | 未通過處理 |
|---|---|---|---|
| **G-1** | 版本啟動與變更凍結 | 來源版本、目標、部署範圍、Owner、相容策略、風險等級 | STOP |
| **G0** | 現況辨識與來源完整性 | 版本、主機、服務、Git/ZIP、映像、安裝檔、工具版本 | STOP |
| **G1** | **既有工具優先**的全盤基線 | UEM Quick、Full、Doctor、Hostcheck、Status、DB，含時間戳 | STOP／分級處理 |
| **G2** | 程式／DB／API／Client 差異及測試矩陣 | 升級路徑、Migration、契約、相依、回退與破壞性變更 | STOP |
| **G3** | 備份與不可變回滾包 | 檔案、舊映像、.deb、DB、設定、金鑰、媒體、Manifest | STOP |
| **G4** | 隔離還原、回滾與失敗注入預演 | 乾淨環境復原結果、可操作 Runbook、風險及 RPO/RTO | STOP |
| **G5** | 審批與受控部署 | apply 核准、窗口、凍結寫入、逐步日誌、檢查點 | 立即停止／回滾 |
| **G6** | 版本／功能／安全／跨平台驗收 | 重新 UEM Full、E2E、USB、Win10 Client、資料完整性 | FAIL 不結案 |
| **G7** | 觀察、事件應變、回滾 | 異常觸發器、回滾結果、重新驗證、事件報告 | FAIL／繼續復原 |
| **G8** | 結案、知識沉澱、舊產物退場 | 升級紀錄、驗收簽核、保留政策、下一版改善清單 | OPEN |

### 1.1 Go／No-Go 與嚴重度

- **P0／STOP**：版本對不上、未知來源、無合法可用備份、回滾未驗證、資料損失可能性未界定、安全 API 對 LAN 暴露、破壞性 migration 未審、Client 不相容且無升級路徑、重要測試失敗、容量不足。
- **P1／需先解決或明確風險接受**：無關鍵影響的文件／效能警告、可說明的舊資料權限警告、已知且無替代方案的測試環境限制。
- **P2／觀察**：不影響相容與復原的低風險待辦。不可用 P2 標記迴避 P0。
- 「WARN=0、FAIL=0」只是某工具範圍內的檢查結果。**任一必要能力 NOT_TESTED／UNKNOWN／STALE 仍不得算 PASS**。任何例外須寫明原因、影響、替代控制與核准，不可默默略過核心安全關卡。

---

## 2. iSTUDY 既有能力索引：先找、先用、再補

**此段為本次特別補完的基礎知識**：Dev61／Dev62 已有相當完整的盤點、自我診斷和升級工具，**每次都先查現場已安裝版本，禁止在未閱讀既有設計前重新造輪子**。

### 2.1 CLI 功能、檢查深度與適用情境

| 現場 CLI／功能 | 實際作用 | 何時使用／注意事項 |
|---|---|---|
| \`sudo istudyctl uem --mode quick\` | 磁碟 SSD/HDD 歸屬、ZFS、重要目錄、容器與掛載、systemd、Port、程式與 API／映像／.deb 版本 | **第一輪盤點**；不修改系統設定或 DB，但會寫 UEM 報告 |
| \`sudo istudyctl uem --mode full\` | Quick 項目＋即時 \`doctor_checks()\`，含編碼器與文件轉換試跑 | **完整基線／升級後驗收**；先排除工作衝突，勿假定純無副作用 |
| \`sudo istudyctl doctor\` | API／DB、LAN、管理 API 隔離、Agent heartbeat、編碼器、中文文件、/Media 權限、字幕模型、備份／開機等自我檢查 | 驗證當前健康；執行前核對工具及版本，保存當次輸出 |
| \`sudo istudyctl status\` | 服務與 Agent 心跳、Server／Client 設定版本摘要 | 記錄關機、重啟、部署前／後狀態 |
| \`sudo istudyctl hostcheck\` | 主機層診斷，與排程 hostcheck 報告交叉比對 | 補充環境健康與失敗原因 |
| \`sudo istudyctl check-db\` | PostgreSQL 連線／socket 可用性 | DB 預檢；**不等於檢查 Schema 或 restore 成功** |
| \`istudyctl restore --file … --target test\` | 先將受支援格式備份還原至 \`istudy_restore_test\` | **會建立／清理隔離測試 DB，非純只讀**；必須核對 DB 名稱、權限、流程，取得操作核准 |
| Dev62 \`deploy/patch_dev62.sh check\` | 檢查符合 dev61→dev62 版本、舊 .deb、Docker 基底、空間等 | 歷史範例；**不能套用 dev61→dev63** |
| Dev62 \`deploy/patch_dev62.sh apply\`／\`rollback\` | 備份、建映像、Migration、更新與回退 | 只支援程式碼聲明的來源／目標版本；需專版審核和實機證據 |

**程式來源**：[\`agent/istudyctl.py\`](../agent/istudyctl.py)、[\`agent/istudy_common/uem.py\`](../agent/istudy_common/uem.py)、[\`deploy/patch_dev62.sh\`](../deploy/patch_dev62.sh)、[\`docs/DEPLOY_DEV62.md\`](DEPLOY_DEV62.md)。以上功能為 GitHub main 程式可見的設計；**不能推定 Ubuntu 現場的 Dev61 工具已含所有 Dev62 新功能**。

### 2.2 UEM 正確語意、報告位置和保存機制

\`uem.py\` 明確遵循「讀不到就 UNKNOWN、不能靠 /Media 路徑猜磁碟類型」。沿用 UEM v0.8.1 磁碟歸屬設計：路徑 → mount → partition → physical disk；ZFS：dataset → pool → members。UEM 不讀取 \`.env\` 的內容（只看存在與權限）、不刪除教材或修改主機設定。

GitHub main 的 \`uem_run()\` 實際行為：

1. Quick 會執行新一次主機盤點，但**可能直接沿用上次 Full 的 Doctor 資料**，並標記 \`carried=true\`。
2. Full 才真正執行 \`doctor_checks()\`，其中包含編碼器探測試轉及 LibreOffice 中文文件轉 PDF 等測試；應觀察 Agent 負載與佇列。
3. Quick 與 Full 均會改寫 \`latest.json\`、\`latest.md\`、\`latest.html\`；同時寫一份 \`history/uem-YYYYMMDD-HHMMSS-{quick|full}.json\`。
4. \`history/\` **只保留最近 10 份 JSON**（程式 \`UEM_KEEP=10\`）；**不是永久發行證據庫**。升級前務必複製並雜湊，並將原始報告放在權限受控的**非公開**備份位置。
5. \`uem_run()\` 在正常完成盤點後回傳 0，**不能只看 shell exit code=0 就宣稱所有項目 PASS**；應解析報告 \`overall\`、\`counts\`、\`issues\`、\`doctor.time\`、\`doctor.carried\`。
6. Quick 顯示「29 PASS」並不代表 Doctor 今天重新測試 29 項。Full／Doctor 的時間必須和這次升級窗口一致。

| 報告／索引 | 實際用途 |
|---|---|
| \`/var/lib/istudy/uem/latest.md\` | **人工與 AI 審閱首選**；可作為本機上傳版 |
| \`/var/lib/istudy/uem/latest.html\` | Chrome 可讀的視覺化盤點 |
| \`/var/lib/istudy/uem/latest.json\` | 程式化差異比較、統計、時序、機器驗證 |
| \`/var/lib/istudy/uem/history/\` | 最近 10 筆盤點原始 JSON，自動輪替 |
| \`/var/lib/istudy/hostcheck.json\` | 主機健康檢查歷史狀態，須留意時間與新鮮度 |

**資料治理**：報告可能列出主機板、磁碟型號、內部路徑、版本等環境細節；**勿不經檢視直接上傳公開 GitHub**。私鑰／密碼／\`.env\`／pg_dump 只能保存於權限受控或加密存放處。

### 2.3 現場第一次 Quick：完整可複製指令

先確認版本與安裝路徑，再執行；\`command -v\` 找不到時**停止、回報，不得去舊資料夾挑一支隨便執行**。

\`\`\`bash
echo "===== ISTUDYCTL LOCATION ====="
command -v istudyctl || true
ls -l /usr/local/bin/istudyctl \
  /usr/local/sbin/istudyctl \
  /opt/istudy/agent/istudyctl.py 2>/dev/null || true

echo "===== ISTUDY VERSION ====="
cat /opt/istudy/VERSION.json 2>/dev/null || true

echo "===== UEM QUICK ====="
sudo istudyctl uem --mode quick

echo "===== UEM REPORT FILES ====="
sudo ls -lh /var/lib/istudy/uem/ 2>/dev/null || true
\`\`\`

> **第一步中若 \`istudyctl\` 不存在，不可因後續 \`|| true\` 就當作成功**；必須先釐清可執行路徑與版本。不得執行不存在／不支援的旗標。

### 2.4 保存 Quick 的 Markdown／JSON 報告（不覆蓋既有檔）

下列指令**會建立使用者家目錄的報告副本**（不修改服務與 DB）；一律保留時間戳，避免將不同版本覆寫成同一份。先檢查報告有產生，且操作完確認雜湊。

\`\`\`bash
set -e
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$HOME/istudy-upgrade-evidence/$STAMP-quick"
mkdir -p "$OUT"
sudo test -s /var/lib/istudy/uem/latest.md
sudo test -s /var/lib/istudy/uem/latest.json
sudo cp /var/lib/istudy/uem/latest.md "$OUT/uem-quick.md"
sudo cp /var/lib/istudy/uem/latest.json "$OUT/uem-quick.json"
sudo cp /var/lib/istudy/uem/latest.html "$OUT/uem-quick.html"
sudo chown "$(id -u):$(id -g)" "$OUT"/uem-quick.*
sha256sum "$OUT"/uem-quick.* > "$OUT/SHA256SUMS"
echo "請上傳報告（先檢查敏感資訊）：$OUT/uem-quick.md"
\`\`\`

若僅需將一份 Quick 報告複製到首頁方便上傳，可使用這個**歷史一次性範例**：

\`\`\`bash
sudo cp /var/lib/istudy/uem/latest.md "$HOME/istudy-dev61-uem-before-upgrade.md"
sudo chown "$USER:$(id -gn)" "$HOME/istudy-dev61-uem-before-upgrade.md"
\`\`\`

**較佳作法仍是有時間戳的目錄與 hash，並保留 JSON，以利自動比較。** 家目錄不是長期異地備份；回滾證據另依 G3 保存。

### 2.5 Quick → Full → Doctor 的判斷流程

1. **Quick 先行**：檢視總覽、磁碟、分割區、ZFS、PostgreSQL、版本、Port、挂載與報告新鮮度。異常先查，不直接跳 Full。
2. **Quick 正常才規劃 Full**：\`sudo istudyctl uem --mode full\`；它會做實際試轉／文件轉換，安排適當時段、注意正在執行的工作；收集最新 \`latest.md/json/html\` 及結果。
3. **Doctor 單獨複查**：\`sudo istudyctl doctor\`，確認各檢查及報告時刻。既有 Doctor 的檢查項目以**現場版本**為準，不得由 GitHub 新版欄位推算 Dev61 已支援。
4. **Status／Hostcheck／Check-db 補充**：\`sudo istudyctl status\`、\`sudo istudyctl hostcheck\`、\`sudo istudyctl check-db\`。這些工具不替代 DB schema 與備份可復原性測試。
5. 對 WARN／FAIL／UNKNOWN／STALE 逐一建「項目－原因－風險－處理－復測」記錄。例如權限 sticky bit，應先用 \`sudo istudyctl media-acl\` 只讀查看，**不可未經確認執行 \`--fix\`、\`chmod 777\` 或刪除測試資料**。
6. 當 Quick 與 Full 相抵觸時，**以時間戳與實際測試範圍判斷**，不以「數量比較多」決定哪份正確。

### 2.6 既有工具缺口：何時才需要補充自訂盤點？

在已使用 UEM 與 Doctor 後，僅針對**證據缺口**做補充：**Git Commit／ZIP SHA、舊版 .deb 的實體可用性、Docker 映像持久保存、DB Migration 清單、Client 版本及 API 相容性、pg_restore／隔離 DB、正式發行日誌、磁碟故障恢復與資料一致性**。不得重複跑大量與版本風險無關的通用硬體指令。

只讀補充範例：

\`\`\`bash
date -Is
docker ps --format 'table {{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'
docker image ls
pg_lsclusters
systemctl list-timers --all --no-pager | grep -Ei 'istudy|backup' || true
df -hT / /Media
sudo ls -ld /opt/istudy /srv/istudy/backup /srv/istudy/updates
\`\`\`

警告：\`docker compose config\` 若不加 \`--quiet\`，可能印出展開後敏感設定；\`docker inspect\` 的 \`Config.Env\`、\`cat .env\` 與 \`psql\` 連線字串**不得貼入公開報告**。

---

## 3. G-1／G0：升級目標、來源、範圍與基線確認

### 3.1 每次升級需建立「發行任務單」

最低欄位：

| 欄位 | 必填內容 |
|---|---|
| 發行識別 | 例如 \`20261104-dev61-to-dev63\`、操作人、審核人、核准來源 |
| 現行與目標 | Server、Agent、PHP、Nginx、Ubuntu Server App .deb、Win10／Ubuntu Client 各自版本 |
| 升級模式 | 原地／隔離／平行；哪台主機、哪個 PostgreSQL／服務、是否有測試資料 |
| 發行範圍 | 變更模組、API、DB migrations、資料夾與權限、硬體（USB／編碼器）、安裝包 |
| 來源識別 | Repo、Commit/tag、ZIP 名、SHA-256、來源 Manifest、必要 dependencies |
| 停機與凍結 | 影響範圍、排程、工作佇列、Client 連線、寫入凍結方法、維護窗口 |
| 服務目標 | 可接受停機 **RTO**、可接受資料回復點 **RPO**（未經使用者確認不得自訂數字） |
| 回滾策略 | 備份來源、舊版映像／.deb、金鑰、DB／Media 一致點、預演結果 |
| 安全與保密 | 是否含私鑰、帳密、教材個資、資料庫 dump、內部 IP |
| 放行／禁止條件 | 所有 STOP 項目、Go/No-Go 決策與證據連結 |

**不可用「目前有 Dev62 原始碼」推論「執行中的主機就是 Dev62」**；以安裝版 UEM／API／映像／dpkg 實測為主。

### 3.2 來源信任與發行產物

- 首先驗證候選 ZIP／Repo 的 sha256sum、檔案清單、版本號、主要原始碼、Migration、測試與建置腳本；不接受 ZIP 命名即證明版本。
- 根據 [C 混合方案](ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)：原始碼 ZIP 不含 \`dist/\`、\`node_modules/\`、\`.deb\`、Client installer、大型 cache/log；保留必要 \`deploy/vendor/age-v1.2.1-linux-amd64.tar.gz\` 例外。
- 建置／測試在 Ubuntu 實機受控工作目錄進行；原地升級**不代表在 /opt/istudy 現場直接編譯或覆寫原檔**。先建置、產生不可變成品、再 apply。
- PHP CLI 版本不必等於 Docker PHP 版本；Host PHP 8.5 lint 通過**不代表** PHP 8.3 Docker runtime 通過。Electron／Client 也需要其實際平台建置與安裝驗收。
- 安裝包與容器映像、Migration、版本字串必須來自同一受審核的來源快照；產物附 SHA-256／Image ID 和 build log；未測到的項目只能標 NOT_TESTED。
- GitHub main 現階段 Dev62，Dev63 候選版不能在未驗收前改成「已完成已部署」。

---

## 4. G2：差異與相容治理，而不只是檔案比對

### 4.1 變更影響矩陣

每次由版本 A → B 必須逐項核對：

1. **程式執行層**：PHP API、Agent／Worker、Nginx、Server App Electron、istudyctl、systemd timer/service、Dockerfile／Compose／user/group、模型、轉檔器、API 閘道、Log／備份機制。
2. **資料層**：PostgreSQL schema、migration 順序、DDL lock、資料搬遷／序列、角色與權限、index、extension、checksum、備份／復原、快取／實體檔。
3. **API／身份層**：Login Session、Super/Admin/User、授權到資料筆層、401/403、Admin 127.0.0.1:4081 限制、WOL、Page Atlas、USB、檔案下載。
4. **Windows／Ubuntu Client**：登入、版本檢查、更新、播放字幕、PDF、最愛／書籤、歷程、檔案下載與回退；**舊 Win10 Client Dev61 是否可連 Dev63 Server 須實測**。
5. **媒體與硬體**：/Media 原始教材不可擅自刪改；playback、subtitles、doc-cache、ZFS、權限、USB 掛載與檢查碼、GPU QSV／VAAPI、離線模型。
6. **營運**：備份、異常通知、停機、佇列排空、日誌、審計、升級失敗自動退回及告警。

### 4.2 Migration、相容性與回滾矩陣

每支 migration 至少記錄：ID、依賴版本、up 行為、是否破壞性、交易支援、非交易 DDL、回滾方式、原資料映射、索引時間、可能鎖表、失敗重試、前後 schema 比對及資料抽樣校驗。

不得把 **Dev61→Dev63** 當作自動可跳版；必須明確查出 Dev62、Dev63 每支 migration、是否中間版也須先部署服務、API 的前後相容窗口。破壞性 schema 要**預備舊資料還原**，不能僅宣稱 down migration 可回復。

相容性清單必須列 **Server A＋Client A、Server B＋Client A、Server B＋Client B、Server A＋Client B（回滾後）** 四種組合；無法支援者要指定必要更新順序與回滾方案。**先測 Client 協定，再決定順序，不可自行推測。**

### 4.3 不可省略的實機測試

- Node.js test、TypeScript typecheck、Electron renderer build、PHP lint、Agent Python tests、Nginx config test／Compose config check。
- 真實 PostgreSQL 18 上的 migration 與 HTTP E2E（**隔離資料庫**）；包含 schema、權限與 API 錯誤處理。
- 重要業務流程：Server App Super/Admin、管理 API 隔離、媒體掃描、轉檔、字幕、PDF、最愛、歷程、Page Atlas、備份、USB 匯入／拒絕非法目標、WOL（依新版決策）。
- Windows 10 Client 真機：安裝、啟動、版本、登入、下載、播放、PDF、字幕、更新／回滾。沒有 Win10 實機就記 NOT_TESTED。
- 測試報表採 \`PASS/FAIL/WARN/SKIP/UNKNOWN/NOT_TESTED\`，每項附 Build ID、版本、平台、執行時間、證據。**沙盒單元測試 PASS 不可替代真機整合測試**。

---

## 5. G3：備份不是單一檔案，而是完整可回滾「復原集合」

### 5.1 回滾點必須包含

| 類型 | 必須保留的內容與驗證 |
|---|---|
| 版本與程式 | VERSION.json、Source Commit/ZIP、完整舊版 /opt/istudy 程式、Server App 原版 \`.deb\`、必要 installer、istudyctl、systemd、polkit、Nginx／Compose／Dockerfile |
| Docker | 既有 agent/php/nginx 映像 **Image ID／digest**、不受新 tag 覆蓋的舊版、必要時 \`docker save\` 的離線 image archive 和 hash |
| PostgreSQL | 完整目標 DB schema/data/sequence/migration、role／extension／權限及必要全域物件；**要測試資料庫還原成功** |
| 設定與金鑰 | .env、服務設定、密碼／私鑰／恢復金鑰、備份密鑰、公鑰、外部服務設定；**加密與權限 0600**，不得放公開 repo |
| 媒體與業務檔 | /Media 原始教材、subtitles、播放檔、文檔轉換產物、更新檔與 DB 檔案一致性時間點；清楚界定哪些可重建 |
| 排程與工作 | Agent jobs／佇列狀態、systemd timers、執行中任務、持久狀態、Client 更新／鎖檔 |
| 證據 | UEM Quick／Full／Doctor、版本、hash、Manifest、時間戳、操作日誌、回滾驗證清單 |

**特別注意**：
- Dev61 UEM 顯示 \`/var/lib/postgresql/18/main\` 和 \`/srv/istudy/backup\` 均在同一 NVMe SSD；**SSD 故障會同時損失 DB 與該備份**。至少留有**不依賴該 SSD**、可存取且可解密的副本。
- \`/Media\` ZFS mirror 是硬碟故障容錯，**不是備份**；不能防止誤刪、惡意覆寫或錯誤 Migration。
- Dev62 方案 B 的加密備份約定只包含 DB、字幕、設定等**列明的項目**，**不等於教材、Client installer、模型與所有媒體都備份**；升級工作另補差額。
- **不得為了測試回滾先把現行 DB 覆寫**；務必用經確認的隔離 DB、明確名稱與權限。

### 5.2 驗證標準

1. 同一個升級凍結點產生程式、DB、檔案與設定 Manifest：每項提供檔案名稱、來源、大小、SHA-256／Image ID、建立時間及用途。
2. 驗證檔案可讀、解密金鑰可用、舊版 .deb／容器映像可復原；**只有 hash 相符不能證明業務資料可還原**。
3. 在獨立 DB 演練恢復，抽樣檢查資料表、筆數、關聯、索引、序列、帳號與權限；以舊版程式和 API 驗證讀取，避免僅確認 SQL 載入。
4. **釐清 RPO／RTO**：回滾到升級前的 DB 可能失去升級後新增資料；若不能接受，升級前應凍結寫入／建立補償計畫，經核准後再上線。
5. 回滾點保留期、異地副本、磁碟空間與清理授權必須記錄；禁止在驗收時自動執行 \`docker image prune\`、\`docker system prune\`、\`apt autoremove\`、清空備份。

---

## 6. G4：還原演練與故障注入，形成真的 Rollback Runbook

1. 閱讀**現場版本** \`istudyctl restore --help\` 與原始程式實作；Dev62 的 \`restore --file FILE --target test\` 會操作 \`istudy_restore_test\`，不是只讀指令，執行前必須先核對測試資料庫不存在其他資料／任務，取得核准。
2. 隔離還原後要確認 \`accounts\`、\`folders\`、\`media\`、\`bookmarks\`、\`view_history\`、\`subtitles\`、\`audit_log\` 等核心表格及實際業務資料；再查序列、索引、migration 狀態，逐項留存證據。
3. 在獨立環境確認舊版 \`.deb\` 可安裝、Agent／PHP 映像可離線載入、舊 Compose／systemd／istudyctl 可恢復；有能力時用完整應用堆疊演練。
4. 回滾流程要設計「**失敗在哪一步，就從哪個 checkpoint 恢復**」；覆寫中斷、Docker build 失敗、資料庫 migration 失敗、重啟失敗、Client API 不符等故障注入需有可驗證的復原結果。
5. 回滾腳本需具備版本守衛、一次一個更新鎖、重入保護、訊號／中斷處理、清楚日誌、失敗後狀態驗證、絕不刪除舊映像或備份、不能報假成功。
6. 升級如涉及不可逆 DB migration，**舊版程式與舊版 DB／Media 必須成套還原**；單獨 \`rollback\` 也許只回退檔案而無法回退 DB。必須讀透腳本才可宣告「有回退保證」。
7. 將預演結果具體記為 \`RESTORE_TEST_PASS\`、\`APP_ROLLBACK_TEST_PASS\`、\`CLIENT_COMPAT_PASS\`，未完成不可當作 PASS。

**Dev62 可參考，但不可直接套用：** [\`patch_dev62.sh\`](../deploy/patch_dev62.sh) 固定只接受 \`dev.61 → dev.62\`，其 check／apply／rollback、失敗退出、映像備份、安裝檔保存、migration 0011、Systemd timer 處理及 23 項腳本測試是設計先例。**Dev63 應先產生專用升級器與測試；不得執行 Dev62 patch 當成 Dev63 升級器。**

---

## 7. G5：受控原地升級的 Runbook

### 7.1 升級前最後一次 Go／No-Go

逐一核對：

- [ ] 升級來源／目標、原地部署模式、Host、Windows Client 順序獲確認
- [ ] UEM Quick、Full、Doctor、Hostcheck、Check-db 有**當次**結果與 report hash
- [ ] Dev61 舊 .deb、舊 Docker image、設定、金鑰、可離線副本完整
- [ ] PostgreSQL 備份及隔離還原 PASS；必要 Media 快照／備份可用
- [ ] 升級版 Migration、API／Client 相容、資料一致性已審核
- [ ] Dev63 專版 \`check / apply / rollback\` 已審核、演練與測試
- [ ] 安裝包與映像的同源版本、hash 與 Build ID 一致
- [ ] 排程／工作／Agent 匯入或字幕任務已安全排空，停機窗口可接受
- [ ] 回滾觸發條件、RPO/RTO、遇故障責任人、檢驗步驟已寫明
- [ ] 已取得**這一次特定版本 apply 操作**的明確核准

### 7.2 操作順序（概念流程，非未經審核可直接貼上執行的指令）

\`\`\`text
核對舊版健康及凍結點
  → 維護窗口／暫停新增工作／排空或受控停止既有任務
  → 完整回滾點／DB-媒體一致性與 hash 再確認
  → 專版 upgrade check（只檢查）
  → 最後 Go/No-Go
  → 停止受影響 iSTUDY 服務／timer（不動無關服務）
  → 執行專版 migration／替換應用與映像／Server App
  → 依相依順序重啟並比對版本、Port、權限
  → Smoke Test → Full Test → Windows Client E2E
  → 成功觀察；失敗立即依 G7 回滾
\`\`\`

每一步記錄時間、指令、返回碼、輸出檔、執行者、服務／DB 狀態，且每個 checkpoint 要有明確「可重跑／不可重跑」判定。**未審核新版腳本前不得用 \`sudo ./deploy/patch_dev62.sh apply\` 代替 Dev63 apply**。

---

## 8. G6：上線驗收、版本觀察與 Client 端檢查

| 驗收面向 | 最低標準 |
|---|---|
| 版本一致性 | VERSION.json、4081 API health、Agent image、PHP image、Server .deb 與版本／協定相符 |
| 主機健全 | UEM Quick + Full、Doctor **全新測試時間**、hostcheck、ZFS、Disk、Docker、systemd |
| 服務與網路 | 4080 LAN、4081 localhost only、PostgreSQL 5432 localhost only；未新增未授權入口 |
| DB／資料 | Migration 正確，schema／索引／角色／帳號與教材、書籤、歷程、備份資料可存取 |
| 身分與安全 | Server App 僅 Super/Admin；401/403 與越權測試、管理 API 隔離、WOL 新規則 |
| 媒體與工作 | 原教材、字幕、PDF、轉檔、編碼器、佇列、Agent 心跳、權限、備份試還原 |
| 新功能 | Page Atlas、USB 匯入與白名單、Log 權限、書籤、PDF 與其他 Dev63 模組 |
| Windows Client | **Win10 實機**：Dev61 舊 Client 連新版 Server 或新版 Client 安裝，登入、播放、PDF、字幕、下載、更新、回滾 |
| 復原能力 | 新版能備份及測試還原、舊版完整回滾點**仍保存** |

所有項目必須有 \`PASS/FAIL/WARN/SKIP/UNKNOWN/NOT_TESTED\` 與證據，不可單純依 CI 綠燈結案。存在安全、資料完整性或舊 Client 無法工作的 P0 時，啟動 G7。

---

## 9. G7：事件回滾、故障分類與復原驗證

### 9.1 觸發與決策

- 版本混搭／Migration 失敗、DB 不能讀取或資料對不齊；
- 管理 API 意外暴露、登入或角色繞過；
- Server App 無法啟動、Agent 持續失敗、教材／字幕丟失；
- 既有 Win10 Client 無法登入／播放且無修復路線；
- 出現不可接受的停機或超出 RPO/RTO。

### 9.2 回滾「整體狀態」，而非只換回某個檔案

\`\`\`text
觸發 → 停止新版寫入／工作／排程
    → 保存故障日誌、版本與資料現況（避免復原過程湮滅證據）
    → 回復原版 Compose、Docker image、Server .deb、istudyctl／設定
    → 必要時將 PostgreSQL 還原到升級前一致點
    → 必要時將 /Media 和衍生資料一起還原
    → 重啟舊版並用 UEM、Doctor、API、Win10 Client 逐項驗收
    → 全部成功才標記 ROLLBACK_PASS；部分復原不可宣稱成功
\`\`\`

新舊資料庫不可混用；如已接受新版 Client 更新，必須按照相容矩陣一起回退。所有破壞性動作依當次授權與已預演的 Runbook 執行。**回滾之後不得自行再次 apply**。

### 9.3 災難復原分級

- **一般部署失敗**：舊程式／映像／DB restore，優先維持測試服務可用。
- **SSD 故障**：需外部 DB／配置／金鑰／映像備份，不可把同一 SSD 的 /srv/istudy/backup 當作異地備份。
- **/Media ZFS mirror 損毀或誤刪**：需獨立教材備份／快照；mirror 不能處理誤刪。
- **私鑰遺失**：即使備份檔在，也可能無法解密；須驗證恢復金鑰與取回路徑。
- **中斷／當機／停電**：依 checkpoint 判斷 DB／檔案是否半套；先隔離狀態再恢復，**不要盲目重跑 apply**。

---

## 10. G8：發行紀錄、知識沉澱與持續改善

### 10.1 每次升級必留的版本紀錄

於 \`docs/upgrade-records/YYYY-MM-DD_DevNN_to_DevMM.md\` 保存**去識別化公開摘要**；原始敏感報告、備份與日誌留受控本機／加密存放位置，不得上傳公開 GitHub。

\`\`\`text
Release ID／日期／操作人／核准人
版本對照：Server／Agent／PHP／Nginx／Server App／Win10 Client
來源：Git Commit／ZIP SHA／映像 ID／.deb SHA／建置紀錄
部署：原地或隔離、窗口、RTO/RPO、變更面向
G-1 與 G0：來源與版本真實性／現行 UEM 基線
G1：Quick／Full／Doctor／hostcheck／check-db 時間、數值、PASS/FAIL
G2：Schema migrations／API／Windows Client／安全差異
G3：回滾包清單／離線副本／Manifest／加密密鑰
G4：DB／程式／Client 回滾演練，具體檢查結果
G5：check、apply、Migration、重啟、版本檢查與異常
G6：實機測試、UEM/Doctor、Windows Client 結果
G7：是否回滾、故障事件、回滾完整性／RPO
G8：結案狀態、未完成事項、問題與文件改善
\`\`\`

- 針對每次失敗建立根因、重現方式、修復與回歸測試，避免後續 AI 反覆踩坑。
- DevN 產物／備份直到通過完整驗收、觀察期和核准清理前不得刪除；清理需確認仍保有法定／營運所需備份。
- 每次修改本 SOP，要增加版本號、修訂日期與原因；不能悄悄使原回滾措施失效。

### 10.2 強制交接句式

> 「請先閱讀 AGENTS.md、docs/VERSION_UPGRADE_SOP.md、目前版本部署文件、最新 DevXX 決策中心；先執行安裝版 UEM Quick 與 Full、Doctor，保存時間戳與 SHA-256；確認 DB／應用／Client 回滾能力及 G-1～G4 全部完成，再規劃 G5。不得未經明確授權開 PR、apply、修改正式資料或刪除舊版備份。」

---

## 11. Dev61 → Dev63 實例：2026-11-04 真實 Ubuntu 基線與待驗

此處只記**本次真實驗證**，不以計畫書推定已通過。

| 項目 | 實際資料與判斷 |
|---|---|
| 部署目標 | 使用者指定：目前 Ubuntu Server App **Dev61 原地升 Dev63**，不建立第二套常駐服務；Win10 Client **Dev61** 先維持 |
| Ubuntu | 26.04.1；Node 24.22.1、npm 11.19.1、主機 PHP CLI 8.5.4（含 PDO/pgsql）、PostgreSQL 18.6、Docker 29.8.2 |
| UEM Quick | **2026-11-04 12:26:56，PASS 29／WARN 0／FAIL 0／UNKNOWN 0** |
| 版本來源 | VERSION.json、127.0.0.1:4081/api/health、agent、php image、Server App .deb **全為 1.0.0-dev.61** |
| 容器 | Agent dev61、PHP dev61、Nginx 1.28.0-alpine，運行正常 |
| Port | 4080 LAN；4081、5432 **只限 localhost** |
| Storage | OS、DB 及 /srv/istudy/backup 在同 SSD；/Media 雙 HDD ZFS mirror ONLINE |
| 潛在問題 | Quick 搭載的 Doctor 結果日期為 **2026-11-02 15:49:42**，當時有 \`/Media/PurgeTest\` sticky bit 警告，**尚未以新 Full 排除** |
| Dev63 候選 | Dev63 source ZIP 尚未在 Ubuntu 驗證；實際 Server／Client 打包、Migration、Win10 相容尚待實測 |
| 回滾狀態 | **尚未盤點及驗證舊 .deb、映像存檔、DB 備份、異地副本、隔離還原**，不得宣告可安全回滾 |
| 結論 | **目前 G0 部分、G1 Quick PASS；G1 Full、G2～G8 未放行。不得直接 apply。** |

### 11.1 本次後續順序

1. Ubuntu 執行 \`sudo istudyctl uem --mode full\`，上傳**最新** \`latest.md\`（必要時 \`latest.json\`）；Full 會試轉與文件轉換，先確認測試負載可接受。
2. 針對 Doctor 警告先用 \`sudo istudyctl media-acl\` 調查，不在未評估前 \`--fix\`。
3. 載入／驗證 Dev63 原始碼 ZIP、SHA-256 與版本；先完成 build／migration 差異審查。
4. 保留 Dev61 \`.deb\`、Docker 影像／設定；製作 DB 及必要 Media 回滾點、加密或異地副本。
5. 先進行隔離還原預演／Client 相容性測試／專版腳本 \`check\`；全部 GO 才取得 \`apply\` 核准。
6. 原地升 Dev63、執行新 UEM Full、Win10 真機 E2E；通過才結案並保留舊版回滾點。

---

## 12. 文件修訂紀錄

| 日期 | 版本 | 修訂 |
|---|---|---|
| 2026-11-04 | v1.0 | 初版 G0～G8、Dev61 UEM 基線 |
| 2026-11-04 | **v2.0** | 重構為發行生命週期治理；補齊既有 UEM／Doctor／Hostcheck 詳細操作、Quick 舊 Doctor 陷阱、報告保存與 history 僅 10 份、不可只看退出碼、Dev62 check/apply/rollback 限制、版本與相容矩陣、備份／金鑰／媒體一致性、回滾演練、例外與 Go/No-Go、實機驗收及知識沉澱 |
