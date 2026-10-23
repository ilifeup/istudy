# iStudy 1.0.0-dev.62 開發狀態

日期：2026-10-22　dev.62 = dev.61＋：
- **P0-A 永久刪除一致性**：權限預檢、失敗後確認每個檔案的實際位置（不再誤報「已復原」）、資料庫提交後的暫存檔改為「待清理」、Agent 重啟續做（含課程資料夾）、只處理資料庫記錄屬於該教材的衍生檔；畫面逐項列出結果。
- **P0-B 直接播放 MP4 的字幕**：「立即執行」把排隊中的字幕工作改成馬上執行；「轉檔佇列 → 完成」加字幕欄與「立即產生／重試字幕」。
- **教材目錄樹狀顯示**：/Media 標頭、第一層區塊、子層引導線、根目錄檔案分區。
- **方案 B 加密備份（age）**：Agent 只有公鑰；主機 istudyctl 做解密驗證、隔離還原測試、InterServer（FTPS）每 3 天上傳並下載比對、10～15 天保留（最後一份可用備份永遠保留）；恢復密碼與 Super 密碼分開；systemd `istudy-backup.timer`。
- **WOL**：設備編輯只有 Super；登入畫面喚醒清單說明未啟用的電腦；新增、登記預設啟用。
- **登入頁「顯示密碼」**（Server App、Client）。
- **Client（版本 1.0.0-dev.62）**：按 X 先登出再縮到系統匣；播放頁預先緩衝；播放器下拉選單配色；WOL 管理對管理員只顯示喚醒與狀態。
- migration 0011（backup_history 欄位、4 種主機備份工作類型）。

**部署現況**：
- 正式 Server：Ubuntu 26.04.1、dev.61 已部署（Doctor 13 PASS；migration 0008～0010）。dev.62 尚未部署，**實機全部 NOT_TESTED**。
- Win10 Client dev.61 已安裝一台。dev.62 Client 安裝檔要在 Windows 打包後發布（開發環境做不出安裝檔）。
- 字幕預設引擎：資料庫目前的設定是 sensevoice-small。

**文件**：
- 部署與驗收：[DEPLOY_DEV62.md](DEPLOY_DEV62.md)。以 dev.61 映像做增量映像：`deploy/patch_dev62.sh check|apply|rollback`，不需要網路。
- 測試結果：[TEST_RESULTS_DEV62.md](TEST_RESULTS_DEV62.md)。

| 唯一鍵 | 狀態 |
|---|---|
| ASR 本機路線 | **引擎**：FunASR Paraformer-zh、SenseVoiceSmall（sherpa-onnx int8）。Whisper 已在 dev.59 移除。<br>**dev.61**：長度建議、音訊品質估計、ASR 處理記錄、Outgo 錄音轉文字。開發環境 PASS；實機 NOT_TESTED |
| 永久刪除教材 | dev.61 新增；dev.62 一致性修正（權限預檢、實際狀態驗證、重啟續做、同 ID 舊檔隔離）。開發環境 PASS；實機 NOT_TESTED |
| Outgo | dev.61 新增。開發環境 PASS（掃描排除、權限隔離、各格式解碼、ASR 流程）；實機 NOT_TESTED |
| 文件轉 PDF | **方式**：LibreOffice 24.2 headless；Word、原生 PDF 照常。<br>**dev.60**：補字型與繁中對應、Impress 匯出選項、頁數與版面說明；檢視器修正 16:9 與橫式頁。**實機**：NOT_TESTED |
| 原檔刪除（30 天） | 規則不變（四項檢查通過＋滿 30 天＋輸入「永久刪除」）。實際刪除 NOT_TESTED |
| 教材根目錄 | dev.59：`/Media`，第一層資料夾＝課程；系統資料夾不掃描（dev.61 加 Outgo） |
| HW-01 | VA-API 實機 PASS；QSV 自我檢查實機 PASS（dev.59）。真實 MTS 轉檔、播放、拖曳定位 NOT_TESTED，正式轉檔仍用 VA-API |
| Email 報告（Super） | 網站代寄（relay-v2）或 SMTP；MAIL-01、02、04 實機 PASS |
| 問題回報（Client 任何使用者） | 開發環境 PASS；Client 實機 NOT_TESTED |
| Client 連線偵測 | dev.61：模擬 Server 停止、恢復的狀態轉換測試 PASS；實際拔、插網路線 NOT_TESTED |
| Client 線上更新 | dev.61：更新服務（下載、核對、只升版、等 App 關閉、開機安裝、立即更新要求）與閒置判斷、重新開啟程序的測試 PASS；Server 發布指令與「Client 版本與更新」頁 PASS。<br>Client 安裝檔未建置；Windows／Ubuntu 實機更新 NOT_TESTED |
| UEM 主機盤點 | SERVER_REAL_TEST_PASS（dev.57）；dev.61 主機板／BIOS 實機 NOT_TESTED |
| 帳號與權限 | Server App 新增／編輯 PASS（實機）；Super 唯一。user／admin API 權限隔離 NOT_TESTED |
| WOL | Server App 喚醒與狀態：使用者回報 OK；實際喚醒 NOT_TESTED。dev.62：設備編輯只有 Super；登入畫面清單說明未啟用的電腦 |
| 備份（方案 B） | dev.62：age 加密、恢復密碼、主機解密驗證與還原測試、InterServer FTPS（本機模擬 PASS；實際連線 NOT_TESTED）、10～15 天保留。資料庫與備份在同一顆 SSD：遠端副本是異地保護 |

---

## dev.53 開發狀態（歷史）

日期：2026-10-19　範圍：**Ubuntu Server（後端）＋Server App＋Client MVP**。
dev.53 = dev.52＋**資料庫改用 Ubuntu 主機的 PostgreSQL 18**（取消 Docker PostgreSQL 17；Docker 只剩 php／nginx／agent）。版本固定為 1.0.0-dev.53。原始碼 ZIP：`istudy_1.0.0-dev.53-src.zip`（附已打包的 Server App .deb）；完整離線部署包 `istudy_1.0.0-dev.53-offline.zip` 需在 Ubuntu 以 `build_bundle.py --zip` 產生（開發環境無法建置 Docker 映像）。
安裝與驗收：[DEPLOY_DEV53.md](DEPLOY_DEV53.md)；資料庫細節：[DEPLOY_PG18.md](DEPLOY_PG18.md)；測試結果：[TEST_RESULTS_DEV53.md](TEST_RESULTS_DEV53.md)。

> **Client 安裝檔：原始碼準備完成，安裝檔尚未實際建置。**（與 dev.52 相同，這一版沒有修改 Client。）

判定規則：程式存在才算「已實作」；在**實機**（Ubuntu Server、各台 Client 電腦）測過才算「已驗收」。
**這一版還沒有任何項目在實機驗收**；「開發環境」只代表在開發用容器（Ubuntu 24.04、Xvfb）裡測過。

## dev.53 相對 dev.52 的變更（整合用）

新增 3（`docs/DEPLOY_PG18.md`、`docs/DEPLOY_DEV53.md`、`docs/TEST_RESULTS_DEV53.md`）、刪除 0、修改如下（含部署包整備：精靈第 6 步改為 SSD 備份文字、install.sh 安裝前檢查、`/Media` 必須是 ZFS、Docker Compose 檢查、`build_bundle.py --zip`；明細見 TEST_RESULTS_DEV53.md 第五節）。

| 檔案 | 變更 | 原因 |
|---|---|---|
| `compose.yaml` | 刪除 `db` 服務與 `pgdata`、`pgsocket`；php、agent 改為唯讀綁定主機 socket 目錄（`${PG_SOCKET_DIR}` → `/var/run/postgresql`，`create_host_path: false`），`DB_HOST=/var/run/postgresql`、`DB_PORT=${DB_PORT}`；刪除對 db 的 depends_on；agent 加 `ISTUDY_BACKUP_REQUIRE_MOUNT=0` | 改用主機 PostgreSQL 18；Agent 雖是 host network，Unix socket 仍要掛載；備份已決定放在系統 SSD |
| `agent/istudyctl.py` | 新增主機 PostgreSQL 函式（`pg_cluster`、`pg_admin`、`pg_dump_to`、`pg_socket_dir`、`scram_verifier`、`create_db`、`drop_aux_db`、`ensure_hba`、`verify_login`、`provision_database`、`check_db`）；預檢加 PostgreSQL 18；步驟 8 改為佈建主機資料庫；doctor、status、backup、restore、update 改用主機 psql／pg_dump；systemd 加 `After/Wants=postgresql.service` 與 `ExecStartPre=istudyctl check-db`；新子指令 `check-db`；`.env` 加 `PG_SOCKET_DIR`、`DB_PORT`；`RENDER_GID` 以 /dev/dri/renderD128 的實際群組為準；`/srv/istudy/backup` 交給 Agent（770）；`_read()` 補上 `encoding` 參數 | 移除對 db 容器的依賴。最後一項：dev.52 的 `backup-disk` 與還原以 `_read(…, encoding=…)` 呼叫會 TypeError |
| `agent/istudy_common/checks.py` | 新增 `pg_clusters()`、`check_postgres()` | 預檢與自我檢查 |
| `agent/worker/maintenance.py` | 還原檢查改用預先建立的 `istudy_restore_test`，清空並重建 public schema（不建立、不刪除資料庫） | istudy 角色沒有 CREATEDB |
| `agent/Dockerfile` | `postgresql-client-17` → `postgresql-client-18` | pg_dump 不能比伺服器舊 |
| `.env.example`、`README.md`、`docs/DEPLOY.md` | 版本；主機 PostgreSQL 18 與磁碟分工；不再要求安裝 docker.io／docker-compose-v2；精靈第 6 步可略過外接硬碟 | 文件與現況一致 |
| `VERSION.json`、`package.json`、`server/tests/integration/test_api.py` | 版本 1.0.0-dev.53（測試只改版本字串） | — |
| `dist/server/` | 重新建置（只有版本） | — |

**沒有修改**：PHP API 與 `server/`（DSN 本來就支援 socket 路徑）、`schema.sql`（在 PostgreSQL 18.4 實測可用）、Nginx、`/Media` 與教材路徑、4080／4081、WOL、Quick Sync、Whisper、Server App 與 Client 的畫面與程式、`deploy/build_bundle.py`（自動不再帶 postgres 映像）、`install.sh`。

主要設計：
- **連線**：主機 Unix socket，不開新的 TCP、不改 `listen_addresses`。
- **認證**：pg_hba 最前面加 `local istudy,istudy_restore_test istudy scram-sha-256`，修改前備份，postgres 的 peer 入口保留。
- **角色**：istudy 只有 LOGIN。
- **管理操作**：建立、備份、還原由 istudyctl 以 postgres（peer）執行；還原的物件以 `SET ROLE istudy` 建立。
- **密碼**：以 SCRAM 驗證值設定，不出現在指令列或 Log。

測試結果見 [TEST_RESULTS_DEV53.md](TEST_RESULTS_DEV53.md)：
- 真正的 PostgreSQL 18.4 伺服器 37 項 PASS。
- Ubuntu pg_wrapper 路徑（PG16）37 項 PASS。
- 既有測試全部 PASS。
- Docker daemon、Ubuntu 的 postgresql-18 套件、systemd 都是 BLOCKED。

## dev.52 相對 dev.51 的變更（整合用，保留）

新增 1、刪除 0、修改如下。

### P0-1：安裝產物中的 electron-updater 驗證會錯誤放行（確認存在）

- **dev.51 程式**：`scripts/package_client.mjs` 的 `packageOne()`：
  `try { asar = require('@electron/asar') } catch {}` → `if (asar && fs.existsSync(asarFile)) {…檢查…} else { console.log('（無法讀取 app.asar，以打包前 dist 的 main.js 檢查結果為準…）') }`。
  沒有讀取工具或找不到 app.asar 時只印一行就繼續，等於放行；而且只假設主程序叫 main.js。
- **dev.52 修改**：
  - 新增 `readAsarEntry()`：直接依 ASAR 格式讀檔（檔頭 → JSON 目錄 → offset／size → SHA256 integrity），**不依賴 @electron/asar**，因此沒有「工具不存在」的情況；檔頭、目錄、範圍、雜湊任何不符都丟出錯誤。不加 @electron/asar 為相依套件的原因：開發環境無法確認 npm 上的版本，寫錯版本會讓 `npm install` 整個失敗。讀取程式已用 Electron 44.9.0、22.3.27 官方發行版內真正的 `default_app.asar` 驗證。
  - 新增 `verifyPackagedUpdater()`：檢查真正的打包產物——`asar: true` 時讀 `resources/app.asar`，`asar: false` 時讀 `resources/app/`；主程序由產物內 package.json 的 `main` 決定；產物版本必須等於 VERSION.json。
  - 產物位置由 `unpackedResources()` 依 electron-builder 的命名決定：x64（本專案唯一的架構）是 `win-unpacked`，其他架構 `win-<arch>-unpacked`。本專案沒有 asarUnpack 設定；若檔案標成 unpacked，會改讀 `app.asar.unpacked/`。
  - 移除退回檢查打包前 `dist/client-*/main.js` 的分支。任何一項失敗都視為失敗。
- **字串特徵為何可靠**（`updaterBundled()`）：① 沒打包進去時 esbuild 會留下 `require("electron-updater")`；② 打包進去時 electron-updater 入口會匯出 `NsisUpdater`，esbuild 不改屬性名稱與字串；iStudy 自己的程式（`apps/`、`packages/`）沒有這個字（全專案只有檢查程式本身出現）。兩者成立只證明「程式碼在產物裡」，**不代表自動更新可以正常執行**。
- 三種狀態分開標示：原始碼建置（`build.mjs`，訊息改為「原始碼建置：main.js 已包含 electron-updater」）／打包產物靜態驗證（`verifyPackagedUpdater`）／安裝後自動更新功能（NOT TESTED）。

### P0-2：未通過驗證的檔案會留在正式輸出目錄（確認存在）

- **dev.51 程式**：`packageOne()` 在 `builder.build()` 之後先 `fs.rmSync(dest)`（刪掉 `dist/clients/<版別>/` 的舊版）→ 把 .exe／.blockmap／latest.yml 複製進 `dest` → 才 `verifyUpdateInfo(dest)`。驗證失敗時不合格檔案已在正式目錄，舊版已被刪除。
- **dev.52 修改**（建置暫存目錄 → 驗證 → 同一資料夾下的暫存發布目錄 → 切換）：
  1. electron-builder 輸出在 `dist/.client-build/<版別>/out/`（原本就是）；`builder.build()` 失敗時直接結束，沒有發布任何檔案。
  2. `validateChannelOutput()` 在 out/ 裡驗證：安裝檔存在且非空、檔名含正確版本；`latest.yml` 存在且沒有其他更新描述檔（例如 dev.yml）；version 正確；path 與 files.url 都是本次安裝檔；sha512 與安裝檔實際內容一致；`.blockmap`（設定 `differentialPackage: true`）存在且非空、名稱為「安裝檔名＋.blockmap」；P0-1 產物靜態驗證。所有問題一次列出。
  3. 全部通過才 `publishChannel()`：複製到 `dist/clients/.staging-<版別>-…`，逐檔比對 SHA256；再把原目錄改名為 `.old-…`、暫存目錄改名為 `<版別>`，成功後刪除 `.old-…`。複製失敗：刪除暫存，原目錄不動。改名失敗：放回原目錄並刪除暫存。只處理這個版別的目錄。
  4. 失敗訊息列出全部問題，並保留 `dist/.client-build/<版別>/out/` 供除錯。
- **限制**：Windows／Linux 沒有「一次交換兩個資料夾」的原子操作；兩次改名之間有極短時間 `dist/clients/<版別>/` 不存在（同一資料夾內改名，不會只剩一半的檔案）。若「放回原目錄」也失敗（例如資料夾被其他程式鎖住），原有檔案會留在 `dist/clients/.old-<版別>-…`，錯誤訊息會說明。`build_bundle.py --clients` 只讀四個版別資料夾，不會讀到 `.staging-*`／`.old-*`。

### 修改檔案

| 檔案 | 說明 |
|---|---|
| `scripts/package_client.mjs` | P0-1：`readAsarEntry`、`unpackedResources`、`verifyPackagedUpdater`，移除退回分支；P0-2：`validateChannelOutput`、`publishChannel`、`finalizeChannel`，`packageOne` 改為「建置 → 驗證 → 發布」；`verifyUpdateInfo` 加上 path 必須是本次安裝檔；NSIS 設定明寫 `differentialPackage: true`（與驗證一致）；`updaterBundled` 註明特徵理由 |
| `scripts/tests/package_client.test.mjs`（新增） | 18 項單元／整合測試（見下表） |
| `scripts/build.mjs` | 只改一行訊息：「原始碼建置：main.js 已包含 electron-updater（打包後另檢查安裝產物）」 |
| `package.json` | 版本；新增 `npm run test:packaging` |
| `docs/DEPLOY.md` | 版本；打包成功／失敗時的訊息改為實際輸出（指令沒有變） |
| `VERSION.json`、`.env.example`、`README.md` | 版本 1.0.0-dev.52 |
| `server/tests/integration/test_api.py` | **只改兩處版本字串**（health 與頁尾版本的斷言讀的是 VERSION.json，不改會讓既有測試失敗）；server/ 其他程式未動 |
| `dist/server/build-info.json`、`dist/server/package.json` | 以 dev.52 重新建置 Server App 的結果，只有版本與建置時間改變，程式檔（main.js、renderer）與 dev.51 相同 |
| `docs/STATUS.md` | 本檔 |

**沒有修改**：`server/`（程式、SQL、Nginx）、`agent/`、`compose.yaml`、`deploy/`、`apps/`（含 Client 主程序與 Server App）、`packages/`、Windows 安裝器版別判斷（`installerNsh`）、Ubuntu 手動更新、自動更新版本比較與 URL。

### dev.52 測試結果

| 測試 | 結果 | 說明 |
|---|---|---|
| JS 語法（scripts、測試檔）；TypeScript 型別檢查 | PASS | `node --check`、`tsc` |
| Client 四個版別、Server App 原始碼建置 | PASS | esbuild；本機沒有 electron-updater，Windows 版建置結果不含更新器（顯示 ⚠，屬預期） |
| P0-1 ASAR 讀取工具不存在 | PASS | 程式不再依賴 @electron/asar，也不含退回 dist/main.js 的分支（測試檢查原始碼） |
| P0-1 找不到 app.asar（dist/main.js 存在且正確） | PASS | FAIL Closed |
| P0-1 ASAR 截斷、不是 ASAR、雜湊不符 | PASS | 三種都失敗 |
| P0-1 找不到主程序、package.json 沒有 main、沒有 package.json | PASS | 三種都失敗 |
| P0-1 主程序沒有更新器；有字串但仍是外部 require | PASS | 兩種都失敗 |
| P0-1 產物版本不符 | PASS | 失敗 |
| P0-1 合格產物（asar 與 asar:false 兩種） | PASS | 僅靜態驗證 |
| ASAR 讀取程式解析 Electron 官方 default_app.asar（44.9.0、22.3.27） | PASS | 真正的 ASAR 檔，不是自製 |
| P0-2 latest.yml 版本錯誤／SHA512 不一致／安裝檔缺失／blockmap 缺失／path 指向其他檔案／多出 dev.yml／沒有 latest.yml | PASS | 都不發布，原有版本保留，沒有暫存殘留 |
| P0-2 產物靜態驗證（P0-1）失敗 | PASS | 不發布 |
| P0-2 複製途中失敗；切換那一步失敗 | PASS | 清除暫存、原有版本保留（以注入錯誤的方式測試） |
| P0-2 合格產物發布、取代舊版；另一版別失敗與成功都不影響其他版別 | PASS | |
| P0-2 Ubuntu：只發布 .deb；缺檔不發布 | PASS | |
| 測試有效性（突變檢查） | PASS | 把程式改回 dev.51 的行為（先刪正式目錄／讀不到 ASAR 就放行）時，分別有 2 項、3 項測試失敗 |
| 打包錯誤處理：沒有 electron-builder、Linux 無 wine 打包 Windows 版、`build.mjs --require-updater` 沒有 electron-updater | PASS | 實際執行 |
| `npm install`／`npm ci` | BLOCKED | 開發環境連不到 npm registry |
| electron-builder 產生 .exe／.deb、真正的 app.asar 與 latest.yml | BLOCKED | 同上；驗證程式會在第一次實際打包時執行 |
| Windows 安裝、自動更新、Ubuntu .deb 安裝與升級 | NOT TESTED | 需要實機 |

以上 P0-1／P0-2 測試是**單元／整合測試**：用自製 fixture（ASAR、安裝檔、latest.yml）呼叫實際的驗證與發布函式並檢查檔案系統結果，**不是 electron-builder 真正安裝檔的測試**。執行方式：`npm run test:packaging`（或 `node --test scripts/tests/package_client.test.mjs`）。

## dev.51 相對 dev.50 的變更（整合用，保留）

新增 0、刪除 0、修改如下：

| 檔案 | 變更 | 原因 |
|---|---|---|
| `scripts/package_client.mjs` | ① NSIS 版別檢查：仍讀登錄 `CurrentMajorVersionNumber`／`CurrentMinorVersionNumber`／`CurrentBuildNumber`／`CurrentVersion`；加上 **Win7 需 SP1（組建號 ≥ 7601）**；讀不到組建號時不猜測；**不符時訊息顯示偵測到的 Windows 版本與組建號**，並指出該用哪個安裝檔 | 原本訊息看不出原因；Win7 未裝 SP1 也會被當成 Win7 |
| 〃 | ② Windows 版打包前必須找得到 electron-updater，否則停止；打包後從安裝檔的 app.asar 取出 main.js，確認真的包含 electron-updater（讀不到 asar 時以打包前的檢查為準並註明） | 不得出現「打包成功但更新器缺失」 |
| 〃 | ③ 打包後檢查 latest.yml：version＝VERSION.json 版本、path／files.url＝實際安裝檔名、sha512＝安裝檔實際雜湊；不符就失敗 | 更新描述檔與安裝檔不一致時 Client 會下載失敗 |
| 〃 | ④ Ubuntu 版不設定更新來源（`publish: null`），不產生 latest-linux.yml | Ubuntu 改為手動更新（見下） |
| 〃 | ⑤ `installerNsh`、`verifyUpdateInfo` 可被 import；直接執行時才打包 | 讓 NSIS 編譯與檢查函式可以單獨測試 |
| `scripts/build.mjs` | electron-updater 只打包進 Windows 三個版別；`--require-updater` 時找不到就失敗（在清除舊輸出之前）；打包後確認 main.js 沒有外部 `require("electron-updater")` 且包含 `NsisUpdater` | 同 ② |
| `apps/client/electron/main.ts` | Ubuntu 版不啟動自動更新，Log 記「Ubuntu 版採手動更新」 | electron-updater 的 .deb 更新要系統管理員權限，學生帳號無法完成 |
| `docs/DEPLOY.md` | 第六節：Windows／Ubuntu 第一次打包的必要軟體、實際指令、成功時應看到的訊息、常見錯誤；新增「4. 更新到新版」（Windows 自動、Ubuntu 手動、保留設定）；故障排除 | 文件與程式一致 |
| `README.md` | build_bundle 會檢查 Server App .deb 的說明 | — |
| `VERSION.json`、`package.json`、`.env.example`、`README.md`、`docs/DEPLOY.md`、`server/tests/integration/test_api.py` | 版本 1.0.0-dev.51（`apps/tests/e2e.mjs` 自 dev.50 起讀 VERSION.json） | — |
| `dist/server/` | 以 dev.51 重新建置 | Server App 頁尾版本 |
| `docs/STATUS.md` | 本檔 | — |

**沒有修改**：`server/`（API、SQL、Nginx）、`agent/`、`compose.yaml`、`deploy/`、`packages/`、`apps/server/`、資料庫結構、Docker／Agent 架構。

### 查證後不是 Bug 的項目（dev.51）

| 項目 | 程式碼證據與理由 |
|---|---|
| 用登錄值判斷 Windows 版本「不可靠」 | `CurrentMajorVersionNumber`（Win10 起才有）＋`CurrentBuildNumber` 不受相容性模式與安裝程式 manifest 影響。NSIS 的 `WinVer.nsh` 用 `GetVersionEx`，它自己註明「沒有正確的 ManifestSupportedOS 時 8.1 以後會被當成 Windows 8」，而 electron-builder 範本的 manifest 不在我們控制內，所以**不改用 WinVer.nsh**。Win10／Win11 都是 NT 10.0，以組建號 22000 區分（Microsoft 的區分方式）；不使用 ProductName（Windows 11 的 ProductName 仍寫 Windows 10）。 |
| NSIS 寫入路徑與 Client 讀取路徑不一致 | NSIS：`ReadEnvStr PROGRAMDATA` ＋ `\iStudy\client.json`；Client：`process.env.ProgramData \|\| 'C:\ProgramData'` ＋ `iStudy/client.json`（`packages/shell/src/config.ts`）。Windows 環境變數不分大小寫，兩者相同。 |
| 一般使用者寫不進 %ProgramData% | Windows 7／10／11 預設 `C:\ProgramData` 允許 Users 建立資料夾與檔案（建立者有完全控制）。寫入失敗時 NSIS 略過、不中斷安裝；Client 讀不到時用同一個內建位址並記一筆 Log。 |
| 回退位址的 Log 會重複大量產生 | `readServerAddress` 只在 App 啟動時呼叫一次（`main.ts` 模組載入時），每次啟動最多一筆。 |
| Win7／Win10／Win11 會下載到錯誤版別 | 更新來源是 `${server}/updates/${build.channel}/`，`build.channel` 來自打包時寫入的 build-info.json；每個版別只讀自己的資料夾。即使放錯檔案，靜默安裝時 customInit 的版別檢查也會停止安裝（`/SD IDOK` 後 Quit）。 |
| 更新描述檔名稱與 Client 查詢名稱不一致 | `detectUpdateChannel: false` → electron-builder 產生 `latest.yml`；electron-updater 的 generic provider 在 Windows 讀 `latest.yml`（未設定 channel）。兩者一致；實際檔名在第一次打包時由上面 ③ 檢查。 |
| 預發行版本比較 | electron-updater 以 semver 比較。以 npm 內建的 semver 實際執行：dev.51＞dev.50、dev.100＞dev.99、1.0.0＞1.0.0-dev.99；1.0.0-dev.51 不會被當成比 1.0.0 新（不會降級）。 |
| Electron 22 與 electron-updater | 主程序以 esbuild 依版別編譯（Win7 版目標 node16），不使用 Node 18 以後才有的語法；**實際在 Win7 執行未測**。 |
| 部署套件缺少 Server App .deb | dev.50 已改為預設停止；`--no-server-app` 只在明確指定時略過（`deploy/build_bundle.py`）。依賴順序：`package_server_app.py` →（可選）`package_client.mjs` → `build_bundle.py --clients`。沒有修改。 |
| 低解析度 | dev.50 的最小 960×640 保留；本版再檢查設定類畫面，不需要 CSS 修改（見下表）。 |

### dev.51 測試結果

| 測試 | 結果 | 說明 |
|---|---|---|
| JavaScript 語法（`scripts/*.mjs`、e2e） | PASS | `node --check` |
| TypeScript 型別檢查 | PASS | `tsc -p tsconfig.json` |
| Python 語法（agent、deploy、tests） | PASS | `compileall` |
| Shell 語法（install.sh、istudyctl.sh） | PASS | `bash -n` |
| Client 四個版別、Server App 原始碼建置 | PASS | esbuild；開發環境沒有 electron-updater，所以這裡建出的 Windows 版**不含**自動更新（顯示 ⚠） |
| NSIS 自訂巨集編譯（Win10、Win11、Win7） | PASS | electron-builder 使用的 NSIS 3.0.4.1（Linux 版 makensis），`-WX` 警告視為錯誤，三個版別都編譯成功。**只編譯自訂巨集，不是完整安裝程式；沒有在 Windows 上執行** |
| 打包錯誤處理：沒有 electron-builder | PASS | 停止並提示 `npm install`（結束碼 2） |
| 打包錯誤處理：沒有 electron-updater（`build.mjs --require-updater`） | PASS | 停止，且不會先清掉舊的輸出 |
| 打包錯誤處理：Linux 沒有 wine 打包 Windows 版；`--all` 略過不能打包的版別 | PASS | |
| latest.yml 檢查函式本身 | PASS | 以自製的範例檔測試「正確／版本不符／SHA512 不符／檔名不符／沒有檔案」五種情況。**這只測檢查函式，不代表 electron-builder 的產物** |
| semver 版本順序 | PASS | npm 內建 semver |
| 960×640 視窗（1024×768 虛擬螢幕）：登入、課程目錄、播放器＋字幕面板、PDF、個人資料、帳號管理、課程群組、WOL 管理、Tag 設定、PC 資訊 | PASS | Win10 版（Electron 44.9.0）與 Win7 版（Electron 22.3.27）各 10 個畫面，沒有橫向溢出、按鈕都在畫面內。**Linux＋Xvfb，不是 Windows 實機** |
| `npm install`／`npm ci` | BLOCKED | 開發環境無法連線 npm registry |
| electron-builder 產生 .exe／.deb | BLOCKED | 同上 |
| 實際 latest.yml 內容 | BLOCKED | 同上 |
| 安裝檔內 electron-updater（app.asar） | BLOCKED | 同上；檢查程式已寫進 package_client.mjs，第一次打包時會執行 |
| NSIS 安裝程式在 Windows 7／10／11 執行（版別檢查、client.json、捷徑、解除安裝） | NOT TESTED | 需要 Windows 實機 |
| 自動更新（Windows 三版） | NOT TESTED | 需要實機與兩個版本的安裝檔 |
| Ubuntu .deb 安裝、手動升級、移除 | NOT TESTED | 需要 Ubuntu Client 實機 |
| Server 端（PHP、API、Agent、istudyctl 以外） | NOT TESTED（未重跑） | 程式與 dev.50 相同 |

## dev.50 相對 dev.49 的變更（整合用，保留）

| 檔案 | 變更 | 原因 |
|---|---|---|
| `apps/client/electron/main.ts` | 視窗最小 1100×700 → **960×640**；預設大小不超過螢幕工作區（開啟後仍最大化） | 1024×768 螢幕放不下最小寬度 1100 |
| `packages/shell/src/config.ts` | 找不到或讀不到 client.json 時，在 Client Log 記一筆「使用內建預設位址 http://10.3.3.100:4080」 | 安裝時若 ProgramData 寫入失敗會改用預設位址，日後 IP 改變時要能從 Log 查出原因（Client Log 會上傳到 Server App「Log」） |
| `deploy/build_bundle.py` | 找不到 Server App .deb 時**停止**（在建立套件之前）；只用終端機部署時加 `--no-server-app` | dev.49 只警告，會產生缺少 Server App 的部署套件 |
| `docs/DEPLOY.md` | 第一節第 4 步說明上一項；版本號 | — |
| `apps/tests/e2e.mjs` | 版本字串改讀 VERSION.json | dev.49 漏改一處（仍寫 dev.48），e2e 重跑會誤判失敗 |
| `VERSION.json`、`package.json`、`.env.example`、`README.md`、`server/tests/integration/test_api.py` | 版本 1.0.0-dev.50 | — |
| `dist/server/` | 以 dev.50 重新建置 | Server App 頁尾版本 |
| `docs/STATUS.md` | 本檔 | — |

**沒有修改**：`server/`（PHP、SQL、Nginx）、`agent/`、`compose.yaml`、`deploy/install.sh`、`deploy/package_server_app.py`、polkit、`scripts/`、Client 其他程式。

### 審查意見的查證結果（dev.50）

| 意見 | 查證 | 結論 |
|---|---|---|
| Electron 44.9.0、22.3.27 是否存在可下載 | GitHub electron/electron 發行頁的 SHASUMS256.txt 都有 `win32-x64.zip` 與 `linux-x64.zip`；兩個版本的 linux-x64 在開發環境實際下載、比對 SHA256 並執行過（Server App 與 Client 畫面測試） | 存在。Windows 版 zip 由 electron-builder 打包時下載，尚未實際下載過 |
| NSIS 腳本沒有真正編譯 | 以 electron-builder 使用的 NSIS 3.0.4.1（Linux 版 makensis）實際編譯三個版別的 `installer.nsh`（customInit 版別檢查＋customInstall 寫 client.json），沒有錯誤或警告 | 自訂巨集語法正確。**完整安裝程式**（含 electron-builder 範本）仍要第一次真正打包才知道 |
| 預發行版本號能否被偵測為新版 | 以 npm 內建的 semver 確認：dev.50＞dev.49、dev.100＞dev.99、1.0.0＞1.0.0-dev.99；electron-updater 用 semver 比較 latest.yml 的版本 | 版本規則沒問題；實際更新流程仍未測 |
| 低解析度 | 在 1024×768 的虛擬螢幕把 Client 縮到 960×640：登入頁、課程目錄、播放器＋字幕面板、PDF 都沒有橫向溢出，按鈕都在畫面內 | 已修正（開發環境） |
| electron-updater 打包進 main.js | 開發環境沒有 npm，無法驗證 | **待第一次 `npm install` 後打包確認**：`package_client.mjs` 內部重建時會顯示「自動更新：已打包」；若執行時載入失敗，Client Log 會記「electron-updater 無法載入，改為手動更新」，App 其他功能不受影響 |
| package-lock.json | 開發環境連不到 npm registry，無法產生 | 第一次 `npm install` 後產生；**請把產生的 package-lock.json 回傳**，下一版會一併放進 ZIP |

### dev.50 開發環境檢查

| 檢查 | 結果 |
|---|---|
| TypeScript 型別檢查；Server App 與四個 Client 版別建置 | 通過 |
| NSIS 3.0.4.1 編譯三個 Windows 版別的自訂巨集 | 通過 |
| `build_bundle.py` 沒有 Server App .deb 時停止且不留下半成品；istudyctl 測試 6 組（含 `--skip-images` 套件） | 通過 |
| Client 960×640 畫面（上表） | 通過 |
| 以模擬 electron-builder 執行 `package_client.mjs`（三個 Windows 版別） | 通過（不是真的打包） |

## dev.49 相對 dev.48 的變更（整合用，保留）

| 檔案 | 變更 | 原因 |
|---|---|---|
| `scripts/package_client.mjs` | ① electron-builder 設定加 `detectUpdateChannel: false` | 版本號是 `1.0.0-dev.N` 時 electron-builder 預設產生 `dev.yml`，但 Client 讀 `latest.yml`，而且腳本只保留 `latest*.yml` → 更新描述檔會遺失，自動更新永遠找不到新版 |
| 〃 | ② 打包前檢查這台電腦能否打包該版別：Windows 版要在 Windows 上（或 Linux 有 wine），Ubuntu 版要在 Linux 上；不行時停止並說明 | electron-builder 在 Linux 打包 NSIS 必須用 wine 建立解除安裝程式；dev.48 文件寫「Linux 也能打包」是錯的，沒有 wine 會在打包途中失敗 |
| 〃 | ③ `--all` 只打包這台電腦能打包的版別，其餘列出原因並略過 | 原本在 Windows 上 `--all` 會在 Ubuntu 版中途失敗 |
| 〃 | ④ 找不到 electron-builder 時提示先執行 `npm install` | 原本只顯示 Node 的 MODULE_NOT_FOUND |
| 〃 | ⑤ 沒有產生安裝檔時視為失敗；沒有產生 `latest*.yml` 時警告「只能手動更新」 | 避免以為打包成功 |
| 〃 | ⑥ NSIS 寫入 `%ProgramData%\iStudy\client.json`：已存在就不寫；建立資料夾或開檔失敗就略過（不中斷安裝）；BOM 改用 `\uFEFF` 寫法 | 見下方「ProgramData 權限」 |
| `scripts/build.mjs` | 提示訊息與註解：`npm ci` → `npm install`；註解改指向 package_client.mjs | ZIP 沒有 package-lock.json，`npm ci` 一定失敗 |
| `docs/DEPLOY.md` | 第六節：第一次用 `npm install`（會產生 package-lock.json，之後可用 `npm ci`）；Windows 版在 Windows 打包、Ubuntu 版在 Ubuntu 打包；打包常見錯誤（含 winCodeSign 符號連結權限）；client.json 權限說明；自動更新標示未實機測試 | 修正錯誤的安裝流程 |
| `README.md` | 快速指令改為 `npm install`；版本號 | 同上 |
| `VERSION.json`、`package.json`、`.env.example` | 版本 1.0.0-dev.49 | — |
| `server/tests/integration/test_api.py`、`apps/tests/e2e.mjs` | 測試中的版本字串 | 只有測試 |
| `dist/server/` | 以 dev.49 版本號重新建置（程式內容不變） | Server App 頁尾版本 |
| `docs/STATUS.md` | 本檔 | — |

**沒有修改**：`server/`、`agent/`、`compose.yaml`、`deploy/`、`packages/`、`apps/client/`、`apps/server/`（除了上列測試檔）。

### package-lock.json

開發環境連不到 npm registry，無法產生帶完整性雜湊的 package-lock.json，所以 ZIP 仍然沒有這個檔。
package.json 的直接相依套件都是固定版本（沒有 `^`），第一次在有網路的電腦執行 `npm install` 會產生 package-lock.json；之後保留它，就可以用 `npm ci` 重現同一組套件。

### ProgramData 權限（Windows 一般使用者）

Client 安裝在使用者資料夾（`%LOCALAPPDATA%\Programs\iStudy`），安裝程式不要求系統管理員。
Windows 7／10／11 預設的 `C:\ProgramData` 權限允許一般使用者建立資料夾與檔案（`BUILTIN\Users:(CI)(WD,AD,WEA,WA)`，建立者在自己建立的資料夾內有完全控制），所以一般帳號也能寫入 `%ProgramData%\iStudy\client.json`，與 Client 讀取的位置（`packages/shell/src/config.ts`）一致，**不需要改路徑**。
若某台電腦的權限被改過而寫不進去：安裝照常完成，Client 讀不到檔案時使用內建的同一個預設位址 `http://10.3.3.100:4080`。只有 Server 位址不同時，才需要以系統管理員編輯該檔。此項**尚未在 Windows 實機確認**。

### dev.49 尚未執行的測試（開發環境無法執行）

- [ ] `npm install`（開發環境連不到 npm registry）
- [ ] `package_client.mjs` 實際呼叫 electron-builder 產生 Win10、Win11、Win7 的 .exe 與 Ubuntu 的 .deb（**四個安裝檔都還沒有實際產生過**）
- [ ] 產生的 `latest.yml`／`latest-linux.yml` 內容與檔名
- [ ] NSIS 安裝程式：版別檢查訊息、client.json 寫入（一般使用者帳號）、捷徑、解除安裝
- [ ] 自動更新：Win10／Win11、Win7（Electron 22）、Ubuntu（electron-updater 的 deb 更新需要系統密碼，學生帳號可能無法完成）都**未實機測試**；不可用時手動安裝新版

開發環境只做了：腳本語法檢查、以模擬的 electron-builder 確認腳本流程（版別檢查、設定內容、只保留安裝檔與更新描述檔）、型別檢查、重新建置 Server App 與四個 Client 版別（見下方「開發環境測試」）。

## dev.48 相對 dev.47 的變更（整合用，保留）

合併 Ubuntu 實機修正時，以 dev.47 為基準：Server 修正改在下列以外的檔案時不會衝突；下列檔案若兩邊都改，需要逐行合併。

| 檔案 | 變更 | 影響範圍 |
|---|---|---|
| `scripts/package_client.mjs`（新增） | Client 安裝檔打包：Win10／Win11／Win7（NSIS .exe＋latest.yml）、Ubuntu（.deb）；安裝前檢查作業系統與版別、寫入 client.json、Ubuntu 開機自動啟動與沙盒設定 | 只有 Client |
| `scripts/build.mjs` | Client 建置時把 electron-updater 打包進 main.js（安裝檔不需要 node_modules）；沒裝時顯示提醒 | 只有 Client（Server App 建置結果不變） |
| `packages/shell/src/bridge.ts` | PC 資訊加入顯示卡（Q-05）；`pcInfo` 改為非同步 | Client＋Server App 的「PC 資訊」 |
| `packages/ui/src/admin/PcInfo.tsx` | PC 資訊畫面顯示顯示卡 | Client＋Server App |
| `apps/server/src/pages/SystemCheck.tsx` | 「5. Client 硬體通用檢查」加顯示卡欄與「已收到 N 台：Win10 x 台…」統計 | 只有 Server App 畫面 |
| `VERSION.json` | 全部版本 1.0.0-dev.48（開發階段每次交付統一編號） | 版本顯示 |
| `package.json` | 版本；新增 `electron-builder` 25.1.8（開發用）、`electron-updater` 6.3.9 | 打包 |
| `.env.example`、`README.md` | 版本號 | — |
| `server/tests/integration/test_api.py`、`apps/tests/e2e.mjs` | 測試中的版本字串；e2e 修正兩個選取器 | 只有測試 |
| `docs/DEPLOY.md` | 新增第六節 Client 打包、放到 Server、安裝、PC 資訊收集、故障排除 | 文件 |
| `docs/STATUS.md` | 本檔 | 文件 |

**沒有修改**：`server/`（PHP API、SQL、Nginx）、`agent/`（Agent、istudyctl）、`compose.yaml`、`deploy/`（install.sh、build_bundle.py、package_server_app.py、polkit）、`apps/client/`（Client 主程序與入口沿用既有程式）。
不需要新增 Server API：Client 用到的 API（登入、目錄、串流、字幕、心跳、WOL、PC 資訊上傳、Client Log、更新檔 `/updates/<版別>/`）dev.47 都已經有。

## Client MVP

| 項目 | 狀態 | 開發環境 |
|---|---|---|
| 登入頁（連線狀態、提示文字、免登入喚醒、版別與版本）、課程目錄、播放器與字幕面板、PDF／Office、Tag、書籤、學習歷程、個人資料、搜尋、帳號、群組、使用記錄、WOL、Tag 設定、PC 資訊 | 已實作（與 Server App 共用 packages/ui） | ✓ Win10 版（Electron 44.9.0）28 項、Win7 版（Electron 22.3.27）28 項實際操作通過（在 Linux＋Xvfb 執行，不是 Windows） |
| 心跳回報（每 60 秒）→ 待登記設備 | 已實作 | ✓ |
| 系統匣常駐、關閉視窗縮到系統匣、開機自動啟動 | 已實作 | 未測（需要實機桌面） |
| 每天第一次啟動上傳前次錯誤記錄 | 已實作 | 未測 |
| 自動更新：Windows 三版（electron-updater，來源 `/updates/<版別>/`，啟動時與每 6 小時）；Ubuntu 版手動更新（dev.51 起） | 已實作 | 未測（開發環境無法安裝 electron-updater） |
| Q-05：Super 在 Client 上傳 PC 資訊（含顯示卡），Server App 統計各作業系統台數 | 已實作 | 畫面已測；上傳 API 在 API 測試中 |
| 安裝檔：Win10、Win11、Win7（NSIS，版別與作業系統檢查、client.json）、Ubuntu（.deb，沙盒、AppArmor、client.json、開機自動啟動） | 已實作（`scripts/package_client.mjs`） | **未測**：開發環境連不到 npm，無法執行 electron-builder |
| 四個版別建置（`dist/client-win10`、`win11`、`ubuntu`、`win7`） | — | ✓ 建置通過；Win7 版主程序 node16、畫面 chrome108 |

## 開發環境測試（dev.49）

| 測試 | 結果 |
|---|---|
| `package_client.mjs`、`build.mjs` 語法檢查；TypeScript 型別檢查 | 通過 |
| Server App 與四個 Client 版別以 dev.49 重新建置 | 通過（開發環境沒有 electron-updater，所以這裡建出的 Client 不含自動更新；打包時會在 `npm install` 後自動重建） |
| 以模擬的 electron-builder 執行 `package_client.mjs`：沒有 electron-builder 時的提示、Linux 無 wine 時拒絕 Windows 版、`--all` 略過不能打包的版別、設定含 `detectUpdateChannel: false`、輸出只保留安裝檔＋`latest*.yml`＋blockmap、installer.nsh 有 BOM、Ubuntu 安裝後腳本沒有未展開的 `${…}` 且 `sh -n` 通過 | 通過（**不是**真的 electron-builder，不代表安裝檔能產生） |
| PHP、API、Agent、istudyctl、畫面操作測試 | 未重跑（這些程式與 dev.48 相同） |

## 開發環境測試（dev.48）

| 測試 | 結果 |
|---|---|
| TypeScript 型別檢查、打包腳本語法檢查 | 通過 |
| Server App 與四個 Client 版別建置 | 通過 |
| PHP 規則 37 項、API 整合測試 12 組、Agent 15 組、istudyctl 6 組 | 通過 |
| Client Win10 版（Electron 44）實際操作 | 28 項中 27 項通過；未通過的 1 項是測試本身的預期文字錯誤，已修正，同一項在 Win7 那次執行已通過 |
| Client Win7 版（Electron 22）實際操作 | 28 項通過 |

## 尚未實機驗收：Client

- [ ] `npm install` 與 `package_client.mjs` 產生四個安裝檔（Windows 版在 Windows 上打包，Ubuntu 版在 Ubuntu 上打包）
- [ ] Win10、Win11：安裝、版別檢查（裝錯版別要停止）、開始功能表與桌面捷徑、開機自動啟動、系統匣
- [ ] Ubuntu（Ubuntu Desktop／Lubuntu）：.deb 安裝、沙盒、開機自動啟動、移除
- [ ] Win7 SP1：Electron 22 能否啟動、播放、PDF；electron-updater 是否可用（不行就改手動安裝）
- [ ] 第一次打包：`npm install` 成功、package_client.mjs 顯示「安裝檔內的 main.js 已包含 electron-updater」與「latest.yml … 一致」
- [ ] 版別檢查：在 Win10 執行 Win11 版（與反過來、Win7 版裝在 Win10）要停止，訊息顯示偵測到的版本與組建號
- [ ] 自動更新（Win10、Win11、Win7 各自確認）：Server 放新版後 Client 提示「有新版本，重新啟動即可更新」，重新啟動後版本號改變
- [ ] Ubuntu 手動升級：`sudo apt install ./新版.deb` 後 client.json 與使用者資料保留
- [ ] 低解析度（1024×768）電腦：Client 視窗最大化後畫面完整、可縮到 960×640
- [ ] 刪除或改壞 client.json 時，Client Log 出現「使用內建預設位址」
- [ ] 心跳 → 待登記設備 → 登記 → WOL 實際喚醒
- [ ] Super 上傳 PC 資訊（顯示卡名稱）→ Server App 統計
- [ ] 7 人同時播放

## dev.47 修改內容（相對 dev.46）

| 修改 | 原因 |
|---|---|
| 預檢的「桌面」改為偵測任何 X11／Wayland 工作階段（XFCE、GNOME 等），並檢查 polkit 驗證代理 | dev.46 寫死 GNOME，XFCE 會被誤判 |
| iStudy 資料庫**不再對主機開連接埠**：PHP 走 Docker 內部網路；Agent 經共用 volume 的 Unix socket 連線 | 主機既有的 PostgreSQL 占用 5432；這樣完全不衝突，也不碰既有的 PostgreSQL |
| 預檢的連接埠檢查只剩 4080、4081 | 同上 |
| polkit 規則：遠端（非使用中）工作階段也會詢問系統密碼 | RustDesk 無螢幕操作時不會被直接拒絕；仍然一定要輸入密碼 |
| pkexec 失敗訊息改成白話：沒有驗證代理、按取消、密碼錯誤、找不到 pkexec | dev.46 會誤報成「polkit 規則未安裝」 |
| Server App 視窗最小 1024×640（原 1200×720） | RustDesk 無螢幕時常見 1024×768 虛擬解析度 |
| `install.sh` 檢查有沒有 pkexec 與 polkit 驗證代理，沒有就提示 | XFCE 預設可能沒有驗證代理 |
| ZFS 沒開 ACL 時：部署步驟與自我檢查會提示、刪除原檔時顯示原因；**不自動修改 ZFS** | dev.46 會默默失敗 |
| 沒有 smartmontools 時不再讓部署第 3 步失敗（只略過 SMART 檢查） | 用 apt 裝 Docker 時套件內沒有 smartmontools 的 .deb |
| `docs/DEPLOY.md` 改寫為 Ubuntu 26.04.1＋XFCE 的安裝步驟與故障排除 | — |

沒有修改：畫面與功能（不加新功能）、PHP API、Agent 轉檔與字幕流程、Docker 映像內容、.deb 打包方式、四個 Client 的原始碼。

## dev.47 時的開發環境測試

| 測試 | 結果 |
|---|---|
| TypeScript 型別檢查、Python／PHP／Shell 語法檢查、`docker compose config` | 通過 |
| Server App 建置（`dist/server`，1.0.0-dev.47） | 通過 |
| PHP 規則 37 項、API 整合測試 12 組、Agent 15 組、istudyctl 6 組 | 通過 |
| Agent 以 Unix socket 連資料庫、pg_dump 以 socket 備份 | 通過 |
| Server App 在 1024×768 畫面啟動，沒有超出畫面 | 通過 |

dev.46 時另外做過、這一版沒有重跑的：Server App 實際操作 33 項（Xvfb＋Electron 44）、.deb 安裝與一般帳號啟動 5 項。

## 尚未實機驗收：Server（dev.47 起，請在 Ubuntu 26.04.1 主機測試）

- [ ] `package_server_app.py` 下載 Electron 並產生 .deb
- [ ] `build_bundle.py` 建置 php、agent 映像，拉取 postgres、nginx 映像
- [ ] `install.sh`：雜湊比對、istudyctl、polkit 規則、Server App 安裝
- [ ] XFCE 選單與終端機啟動 Server App；Electron 沙盒（不以 root 執行）
- [ ] 系統密碼視窗：接螢幕操作、RustDesk 操作兩種模式
- [ ] bootstrap 14 步（Docker 已用 apt 安裝時第 3 步略過）
- [ ] 和主機既有的 PostgreSQL 並存（iStudy 不使用 5432）
- [ ] `/Media` ZFS：掛載檢查、媒體標記、資料夾權限、ACL
- [ ] 首次設定精靈 9 步
- [ ] Super 登入、Server 管理 14 頁、帳號與群組
- [ ] 教材掃描與資料庫對應、AVCHD 合集
- [ ] 影片播放、拖曳、字幕面板；PDF 閱讀；Office 轉 PDF
- [ ] Quick Sync 轉檔（i5-14400 UHD 內顯）與 CPU 備援
- [ ] Whisper 字幕產生與台灣繁體轉換（需要模型檔）
- [ ] 診斷、Log、備份與還原測試
- [ ] 重新開機後服務自動恢復
- [ ] RustDesk 下的畫面大小與操作

實機發現的問題請附上錯誤訊息，下一版修正。

## 已決定事項

- Q-05（7 台 Client 的作業系統與硬體）：Client 安裝時由 Super 登入，以「PC 資訊 → 上傳到 Server」收集（含顯示卡），在 Server App 統計。

## 與計畫書 v1.7 不同的地方

| 項目 | 計畫書 | 實作 |
|---|---|---|
| 開發順序 | Client 與 Server App 同時 | 先 Server＋Server App（dev.47），再 Client（dev.48：Win10／Win11 → Ubuntu → Win7） |
| Server 桌面 | GNOME | 任何桌面；目標主機為 XFCE＋X11＋LightDM |
| 資料庫連線 | db 綁 127.0.0.1:5432 給 Agent | 不開主機連接埠；Agent 用 Unix socket（共用 volume） |
| 畫面建置工具 | Vite | esbuild（`scripts/build.mjs`） |
| Server App 打包 | electron-builder | `deploy/package_server_app.py`（Electron 官方 Linux 版＋dpkg-deb，含 AppArmor 設定與 setuid 沙盒）；Client 仍用 electron-builder（`scripts/package_client.mjs`） |
| Client 安裝位置 | 未明定 | Windows 安裝在使用者自己的資料夾（不需要系統管理員權限，自動更新不會跳 UAC）；四個版別同一個 App ID，換裝別的版別時直接取代 |
| 作業系統版本 | 只接受 26.04 | 26.04 通過；24.04 顯示警告但可繼續 |
| 新安裝的轉檔佇列 | 未明定 | 預設暫停，首次設定精靈第 4 步確認後開放 |
| 沒有內顯 | 未明定 | bootstrap 自動拿掉 agent 的 `/dev/dri` 對應 |
| API 位址 | 畫面不知道 Server 位址 | 主程序把 API 位址交給畫面，只用來組請求網址，不在任何畫面顯示 |
| 資料表與工作 | 第 14、15 章 | 另加 `subtitle_cues`、`system_logs`、`folders.course_id`；工作類型另加 `subtitle_import` |

## 測試指令

```bash
php server/tests/unit/rules_test.php
cd agent && DB_HOST=127.0.0.1 DB_USER=postgres python3 -m unittest tests.test_agent tests.test_istudyctl
DB_HOST=127.0.0.1 DB_USER=postgres python3 -m unittest server/tests/integration/test_api.py
```
