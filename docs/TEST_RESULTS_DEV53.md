# iStudy 1.0.0-dev.53 測試結果

日期：2026-10-19。執行環境：開發用容器（Ubuntu 24.04；有 Docker CLI 與 Compose v5.5.1，**沒有 Docker daemon**；可連 GitHub，連不到 Docker Hub、apt、PyPI）。
**沒有任何一項是在使用者的 Ubuntu 26.04.1 主機上執行的。**

結果定義：
- PASS＝實際執行且符合預期
- FAIL＝實際執行但不符合
- BLOCKED＝環境限制無法執行
- NOT TESTED＝尚未執行（需要實機）

## 一、總表

| 驗收項目 | 結果 | 說明 |
|---|---|---|
| Python 語法（agent、deploy、tests） | PASS | `compileall` |
| PHP 語法（server/ 全部 .php） | PASS | `php -l` |
| Shell 語法（install.sh、istudyctl.sh） | PASS | `bash -n`、`sh -n` |
| JavaScript／TypeScript | PASS | `node --check`、`tsc` |
| `docker compose config` | PASS | 服務只有 php、nginx、agent；沒有 /dev/dri 時的版本也通過 |
| PostgreSQL 18 連線與權限 | PASS | 真正的 PostgreSQL 18.4 伺服器 37 項（見第二節） |
| Ubuntu pg_wrapper 路徑（PG16） | PASS | 37 項（見第二節） |
| 原有測試與回歸測試 | PASS | 見第三節 |
| Server App 打包（.deb） | PASS | `package_server_app.py` 實際產生 `istudy-server-app_1.0.0-dev.53_amd64.deb`；在開發容器安裝並以 Playwright 實際操作 5 項 |
| **Docker 映像建置**（istudy/php、istudy/agent） | **BLOCKED** | 沒有 Docker daemon，也連不到 Docker Hub、Ubuntu／Debian apt、apt.postgresql.org、PyPI |
| Agent Dockerfile 能否安裝 `postgresql-client-18` | **BLOCKED** | 連不到 apt.postgresql.org，無法查證套件清單；第一次建置時會實際確認（找不到時建置失敗並停止） |
| **完整離線部署包**（含映像）與它的 SHA256 驗證 | **BLOCKED** | 需要上面的映像；`istudy_1.0.0-dev.53-offline.zip` **沒有在開發環境產生**，請依 DEPLOY_DEV53.md 第 1 節在 Ubuntu 產生 |
| 部署套件結構與 SHA256SUMS 驗證（不含映像） | PASS | `build_bundle.py --skip-images` 產生 16 個檔案（含真正的 Server App .deb），`verify_bundle` 16 個雜湊值相符。這個結構套件**不能部署** |
| 部署腳本必要檔案檢查 | PASS | install.sh 對結構套件（映像是空的）在第 1 步停止，結束碼 2，訊息「不是完整的離線部署套件」 |
| 安裝前檢查失敗時不安裝 | PASS | 測試用副本（放入佔位檔通過第 1 步；不是可部署的套件）：install.sh 第 2 步因 PostgreSQL 18、/Media、系統碟三項 ✗ 停止，結束碼 3；istudyctl、polkit 檔案的修改時間不變 |
| `build_bundle.py --zip --skip-images` 拒絕產生 offline.zip | PASS | 防止把結構套件當成部署包 |
| Ubuntu 26.04.1 實機：安裝、部署、驗收 | NOT TESTED | 見第四節 |

## 二、PostgreSQL 18 連線與權限

### PostgreSQL 18.4 伺服器（37 項全部 PASS）

**伺服器**：PostgreSQL 18.4（theseus-rs 發行的 Linux 執行檔，SHA256 與發行頁相符）。以和 Ubuntu 相同的方式設定：socket 目錄 2775、socket 0777、`local all all peer`。
**這不是 Ubuntu 的 postgresql-18 套件**：叢集位置由測試程式指定，其餘都是實際的 istudyctl、Agent、PHP 程式。

**角色與資料庫**
- istudy 角色只有 LOGIN（非 SUPERUSER／CREATEDB／CREATEROLE／REPLICATION／BYPASSRLS）；密碼存成 SCRAM-SHA-256。
- `istudy`、`istudy_restore_test` 擁有者 istudy，UTF8／C.UTF-8；PUBLIC 不能連線。

**pg_hba.conf 與認證**
- 第一條是 iStudy 的 scram-sha-256 規則；其他規則保留，修改前已備份，`pg_hba_file_rules` 沒有錯誤；重複執行不重建、不重複。
- 沒有密碼時以 istudy 登入被拒（證明走 scram、不是 peer）；istudy 連不到 postgres 資料庫；postgres 的 peer 管理入口照常。
- 同名資料庫擁有者不是 istudy、或同名角色有 CREATEDB 時停止。

**PHP 與 Agent 經 socket＋密碼連線**
- PHP（www-data，uid 33）：建立 27 張資料表、讀寫、CLI status／create-super。
- Agent（uid 10001）：讀寫；istudyctl 讀 Agent 心跳。

**備份與還原**
- 主機 pg_dump 備份（檔案 600）；`restore --target test`／`prod`；不能刪除正式資料庫。
- Agent 的 pg_dump 18 備份與還原檢查（不需要 CREATEDB）。

**開機前檢查**：`check-db` 在 socket 存在／不存在時各自正確。

### Ubuntu pg_wrapper 路徑（37 項全部 PASS）

同一套測試，不替換任何函式，走實際的 `pg_lsclusters` → `runuser -u postgres -- psql --cluster 16/main`。開發容器只有 PostgreSQL 16，所以以 `ISTUDY_PG_MAJOR=16` 執行。

### 未涵蓋

Docker 容器經**唯讀 bind mount** 的 socket 連線（含 AppArmor）：沒有 daemon，BLOCKED。測試是以相同 uid 在主機上直接連線。

## 三、原有測試與回歸

| 測試 | 結果 |
|---|---|
| istudyctl 6 組（bootstrap 測試模式、離線套件結構） | PASS |
| Agent 15 組（含備份＋還原檢查） | PASS |
| API 整合測試 12 組（版本 1.0.0-dev.53） | PASS |
| PHP 規則 37 項 | PASS |
| Client 打包驗證與發布 18 項（`npm run test:packaging`） | PASS |
| Server App 畫面 33 項（Xvfb＋Electron 44；含「重新檢查」執行預檢） | PASS |
| 已安裝的 Server App .deb 5 項（isPackaged、初始部署畫面、Super 登入總覽、主機維護走 pkexec、無 JS 錯誤） | PASS |
| `/Media` 檔案系統判斷（zfs、路徑含空白、不存在） | PASS |

## 四、尚未實機驗證（NOT TESTED）

照 [DEPLOY_DEV53.md](DEPLOY_DEV53.md) 第 1～4 節在 Ubuntu 26.04.1 執行：

1. 建置映像與完整離線部署包，以及 offline.zip 的 SHA256 驗證
2. install.sh 5 步（含安裝前檢查）與 bootstrap 14 步
3. 以下 PostgreSQL 18 相關項目：
   - Ubuntu 的 postgresql-18 套件：socket 位置、`pg_lsclusters` 顯示 18
   - 容器經唯讀 bind mount 的 socket 連線
4. Docker 只有三個服務；5432 不對 LAN
5. PHP → PG18、Agent → PG18、API 寫入讀取
6. 備份與還原、重新開機後自動連線（systemd 的 `After=postgresql.service`＋`check-db`）
7. Quick Sync、Whisper、RustDesk 無螢幕操作、Client 安裝檔

## 五、這一版的修改（相對交付的 istudy_dev52_pg18.zip）

| 檔案 | 修改 |
|---|---|
| `apps/server/src/pages/SetupWizard.tsx`、`BackupsLogs.tsx` | 第 6 步改名「備份」，文字改為「備份儲存於 SSD /srv/istudy/backup（不需要外接硬碟）」，移除精靈內的外接硬碟選擇；備份頁同步。（主機維護頁仍可選用外接硬碟） |
| `agent/istudy_common/checks.py` | 新增 Docker Compose 檢查；`/Media` 必須是 ZFS |
| `agent/istudyctl.py` | 預檢加入 Docker Compose（知道套件是否附 Docker 安裝檔）；`bootstrap --check-only --bundle` 不重算整份雜湊（給 install.sh 用） |
| `deploy/install.sh` | 5 步：必要檔案與雜湊 → **安裝前檢查（有 ✗ 停止，不安裝）** → istudyctl → polkit → Server App；Server App 安裝失敗時以結束碼 4 明確告知 |
| `deploy/build_bundle.py` | 新增 `--zip`：產生 `istudy_1.0.0-dev.53-offline.zip`（單一最上層資料夾、保留執行權限）＋`.sha256`；`--skip-images` 時拒絕 |
| `dist/server/`、`dist/istudy-server-app_1.0.0-dev.53_amd64.deb` | 重新建置與打包 |
| `docs/DEPLOY.md`、`DEPLOY_PG18.md`、`STATUS.md`、`README.md`；新增 `DEPLOY_DEV53.md`、本檔（取代 `TEST_RESULTS.md`） | 檔名、指令、步驟與實際產物一致 |
