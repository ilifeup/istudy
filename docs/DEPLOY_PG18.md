# iStudy 1.0.0-dev.53：使用 Ubuntu 主機的 PostgreSQL 18

dev.53 起，iStudy **不再使用 Docker 內的 PostgreSQL 17**，改用 Ubuntu 主機已安裝的 PostgreSQL 18。Docker 只剩 `php`、`nginx`、`agent` 三個服務。
一般部署步驟仍照 [DEPLOY.md](DEPLOY.md)。本文件說明資料庫相關的檢查、初始化、權限、驗收、備份還原與失敗排除。

> 本文件的指令都還**沒有在這台 Ubuntu 主機上實際執行過**；開發環境的測試結果見 [TEST_RESULTS_DEV53.md](TEST_RESULTS_DEV53.md)。

---

## 一、Ubuntu Server 現況

以下是使用者 2026-10-19 以 `lsblk`、`findmnt`、`zfs list`、`docker info`、`psql SHOW data_directory` 及 UEM 11:51 報告確認的狀態。**部署前請用第三節的指令再查一次**。

| 項目 | 實測現況 |
|---|---|
| OS | Ubuntu 26.04.1 LTS x86_64；kernel `7.0.0-38-generic`（UEM 當時） |
| 桌面 | XFCE＋X11＋LightDM；RustDesk 無螢幕遠端（不重裝桌面） |
| LAN | `10.3.3.100`（部署前再確認） |
| 系統 SSD | `nvme0n1`，ADATA LEGEND 710，476.9 GiB；EFI `/dev/nvme0n1p1`（vfat，`/boot/efi`）；根目錄 `/dev/nvme0n1p2`（ext4，`/`） |
| Docker | Docker CE＋Compose plugin；Docker Root Dir `/var/lib/docker`（SSD） |
| PostgreSQL | 主機 **PostgreSQL 18**，資料目錄 `/var/lib/postgresql/18/main`（SSD）；UEM 當時只聽 `127.0.0.1:5432` |
| ZFS | pool／dataset `Media`，mirror-0（`/dev/sda` OOS12000G、`/dev/sdb` ST12000NM0127，各 10.9 TiB），ONLINE；掛載 `/Media`，約 10.8 TiB 可用 |
| 舊 iStudy | 清理報告顯示 `/opt/istudy`、`/etc/istudy`、`/Media/iStudy`、舊服務與容器已移除 |
| 連接埠 | UEM 當時 4080／4081 沒有程式在聽 |
| `/Media/Backups/`、`/Media/Installers/` | **使用者已要求刪除，但還沒收到刪除後的實測結果**，不能當作已清空（見第八節） |

**磁碟分工（已決定）**：SSD 放 Ubuntu、iStudy 程式與設定、Docker、PostgreSQL 18、備份；ZFS `/Media` 只放 iStudy 教材與衍生檔案（`videos`、`playback`、`subtitles`、`doc-cache`、`.istudy-media`）。ZFS 不放資料庫、Docker 或程式。

| 目錄 | 磁碟 | 用途 |
|---|---|---|
| `/`、`/home/media`、`/opt/istudy`、`/etc/istudy`、`/var/lib/istudy` | SSD | 系統、使用者、iStudy 程式、設定與主機狀態 |
| `/var/lib/docker` | SSD | Docker 映像與容器 |
| `/var/lib/postgresql/18/main` | SSD | PostgreSQL 18（iStudy 使用自己的資料庫與角色，和其他資料庫共用同一個 instance） |
| `/srv/istudy/backup` | SSD | iStudy 備份 |
| `/srv/istudy/updates` | SSD | Client 更新檔 |
| `/Media` | ZFS Mirror | 教材 |

---

## 二、iStudy 如何連到 PostgreSQL 18

| 項目 | 設定 |
|---|---|
| 連線方式 | **Unix socket**（主機 `unix_socket_directories`，通常 `/var/run/postgresql`），以唯讀綁定掛載進 `php` 與 `agent` 容器的 `/var/run/postgresql` |
| 不做的事 | 不改 `listen_addresses`、不開新的 TCP 連接埠、不經 Docker gateway、不改防火牆 |
| Agent | 雖然使用 `network_mode: host`（WOL 廣播），Unix socket 是檔案，仍需綁定掛載（host 網路不共用檔案系統） |
| 資料庫 | `istudy`（正式）、`istudy_restore_test`（還原檢查用，平常是空的）；擁有者 `istudy`，UTF8，`C.UTF-8`；其他角色不能連線（`REVOKE ALL … FROM PUBLIC`） |
| 角色 | `istudy`：LOGIN；**沒有** SUPERUSER、CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS |
| 密碼 | `istudyctl bootstrap` 隨機產生，存在 `/opt/istudy/.env`（640，root:10001）；設定到 PostgreSQL 時送的是 SCRAM-SHA-256 驗證值，不是密碼本身，不會出現在指令列、Log 或報告 |
| 認證 | Ubuntu 預設 `local all all peer`，容器內的帳號（www-data 33、agent 10001）通不過 peer。bootstrap 在 `pg_hba.conf` **最前面**加一條：`local istudy,istudy_restore_test istudy scram-sha-256`。只對 istudy 角色與這兩個資料庫有效；postgres 的 peer 管理入口與其他規則不變 |
| 管理操作 | 建立角色與資料庫、備份、還原，由 `istudyctl`（root）以主機 `postgres` 帳號（peer）執行 `psql`／`pg_dump`，不需要 iStudy 密碼 |

---

## 三、部署前檢查（唯讀）

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL
findmnt -T / -o SOURCE,FSTYPE,TARGET
findmnt -T /Media -o SOURCE,FSTYPE,TARGET          # 要是 Media（zfs）
sudo zpool list && sudo zpool status -x           # 要 ONLINE（pool 名稱以 zpool list 為準：2026-10-19 盤點寫 Media、10-19 交辦書寫 tank）
sudo zfs list -t filesystem,snapshot                # 找出掛在 /Media 的 dataset
sudo ls -la /Media
sudo pg_lsclusters                                 # 要有 18 main 5432 online
sudo -u postgres psql -Atc 'SHOW server_version;'
sudo -u postgres psql -Atc 'SHOW data_directory;'
sudo -u postgres psql -Atc 'SHOW unix_socket_directories;'
sudo -u postgres psql -Atc 'SHOW hba_file;'
sudo -u postgres psql -Atc "SELECT rolname FROM pg_roles WHERE rolname='istudy';"          # 第一次部署前應該沒有輸出
sudo -u postgres psql -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'istudy%';"  # 同上
ls -ld /var/run/postgresql                         # 通常 drwxrwsr-x postgres postgres（其他人要有 x）
ls -l /var/run/postgresql/.s.PGSQL.5432            # 通常 srwxrwxrwx
docker compose version
docker info --format '{{.DockerRootDir}}'          # /var/lib/docker
stat -c '%g %G' /dev/dri/renderD128                # Quick Sync 用的群組 ID（bootstrap 會自動讀取）
ss -ltn | grep -E ':(4080|4081|5432)\b' || true    # 4080／4081 要沒有；5432 只在 127.0.0.1
```

若已經有 `istudy` 角色或 `istudy`／`istudy_restore_test` 資料庫：bootstrap 會檢查。擁有者不是 istudy，或角色有管理權限時會**停止**，不會修改或刪除；請先確認用途。

---

## 四、安裝

照 [DEPLOY_DEV53.md](DEPLOY_DEV53.md)：一次性建置完整離線部署包 → `sudo ./install.sh` → Server App「開始部署」（或 `sudo ./install.sh --cli`）。
離線套件**不再包含 `postgres:17.6-alpine` 映像**，只有 `istudy/php`、`istudy/agent`、`nginx:1.28.0-alpine`。

bootstrap（14 步）中和資料庫有關的部分：

| 步驟 | 內容 |
|---|---|
| 2. 預檢 | 新增「PostgreSQL 18（主機）」：沒有執行中的 18 版叢集時停止；「Docker Compose」；`/Media` 必須是 ZFS。install.sh 安裝前也會先執行同一份檢查 |
| 5. 目錄 | `/srv/istudy/backup`（SSD）交給 Agent（uid 10001，770）寫入每日備份 |
| 6. 產生設定 | `.env` 新增 `PG_SOCKET_DIR`、`DB_PORT`；`RENDER_GID` 以 `/dev/dri/renderD128` 的實際群組為準 |
| 8. 資料庫 | 讀取主機 socket 目錄與連接埠 → 建立／更新 istudy 角色（最小權限、SCRAM）→ 建立 `istudy`、`istudy_restore_test`（已存在就不動）→ `pg_hba.conf` 最前面加規則（第一次修改前備份到 `/var/lib/istudy/pg_hba.conf.before-istudy`）→ `pg_reload_conf()` → 以一般帳號用密碼經 socket 登入確認 → 啟動 php → 建立資料表（migration） |
| 10. 開機啟動 | `istudy.service` 加上 `After=postgresql.service`，並在啟動容器前執行 `istudyctl check-db`（最多等 90 秒，等主機 PostgreSQL 的 socket 出現） |

bootstrap 可以重複執行：既有資料庫不刪除、不重建。整個流程沒有 `DROP DATABASE istudy`、`pg_dropcluster`、移除套件等指令。只有 `restore`／`update` 的失敗復原會刪除 iStudy 自己的輔助資料庫（`istudy_restore_test`、`istudy_before_restore`、`istudy_failed_update`），程式會拒絕刪除其他名稱。

如果想先手動建立（不一定要做；bootstrap 會自動完成，而且結果相同）：

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE istudy LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE istudy OWNER istudy TEMPLATE template0 ENCODING 'UTF8' LOCALE_PROVIDER libc LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
CREATE DATABASE istudy_restore_test OWNER istudy TEMPLATE template0 ENCODING 'UTF8' LOCALE_PROVIDER libc LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
REVOKE ALL ON DATABASE istudy FROM PUBLIC;
REVOKE ALL ON DATABASE istudy_restore_test FROM PUBLIC;
SQL
# 密碼由 bootstrap 依 /opt/istudy/.env 設定；pg_hba.conf 規則也由 bootstrap 加入
```

---

## 五、部署後驗收（請實際執行並保留輸出）

```bash
# 1. Docker 只剩三個服務
sudo docker compose --project-directory /opt/istudy --env-file /opt/istudy/.env -f /opt/istudy/compose.yaml ps --format '{{.Service}} {{.State}}'
#    → php running／nginx running／agent running（沒有 db）

# 2. 自我檢查（含 PostgreSQL 18、API 與資料庫、Agent 心跳）
sudo istudyctl doctor

# 3. PHP → PG18（API 讀取資料庫）
curl -s http://127.0.0.1:4081/api/health          # "db":"ok"，version 1.0.0-dev.53

# 4. Agent → PG18（心跳寫入資料庫）
sudo istudyctl status                              # agent_heartbeat_age_sec 應小於 90

# 5. 資料庫內容與權限
sudo -u postgres psql -d istudy -Atc "SELECT count(*) FROM pg_tables WHERE schemaname='public';"   # 27
sudo -u postgres psql -d istudy -Atc "SELECT version FROM schema_migrations;"                     # 0001_schema
sudo -u postgres psql -Atc "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='istudy';"  # f|f|f
sudo -u postgres psql -Atc "SELECT line_number, database, user_name, auth_method FROM pg_hba_file_rules ORDER BY line_number LIMIT 3;"
#    第一條：{istudy,istudy_restore_test} {istudy} scram-sha-256

# 6. API 寫入／讀取：以 Super 登入 Server App，新增一個測試帳號再刪除；或在首次設定精靈完成第 2 步（基本設定）

# 7. 備份與還原（只動 iStudy 的資料庫）
sudo istudyctl backup                                              # → /srv/istudy/backup/istudy-db-<時間>.sql
sudo istudyctl restore --file /srv/istudy/backup/istudy-db-<時間>.sql --target test
#    顯示各資料表筆數；正式資料庫不變
#    Server App「備份」→「立即備份並測試還原」：Agent 備份＋還原到 istudy_restore_test 檢查筆數（表格「還原測試」欄顯示 ✓ 筆數相符）

# 8. 重新開機後
sudo reboot
#    重新登入後：systemctl status istudy postgresql；再做 1～4
```

**測試資料不要寫進正式資料**：驗收用的帳號、教材請在完成後刪除，或在正式匯入教材前完成驗收。

---

## 六、備份與還原

| 方式 | 內容 | 位置 |
|---|---|---|
| Agent 每日備份（預設 03:30，保留 14 份） | 資料庫（容器內 pg_dump 18）、字幕、文件、設定檔（compose.yaml、.env、VERSION.json）；可選擇同時還原到 `istudy_restore_test` 檢查筆數 | `/srv/istudy/backup/istudy-backup-<時間>.tar.gz`（600） |
| `sudo istudyctl backup` | 只有資料庫（主機 pg_dump） | `/srv/istudy/backup/istudy-db-<時間>.sql`（600） |
| `sudo istudyctl restore --file <檔> --target test` | 還原到 `istudy_restore_test` 並列出筆數 | 不影響正式資料庫 |
| `sudo istudyctl restore --file <檔> --target prod` | 先還原到測試資料庫、檢查通過、輸入「覆蓋」確認後，停止 php／agent，正式資料庫改名為 `istudy_before_restore` 保留，測試資料庫改名為 `istudy` | 只動 iStudy 的資料庫 |

- 還原時，資料表由 istudy 角色建立（`SET ROLE istudy`），還原後 PHP、Agent 照常可以連線。
- Agent 的還原檢查不需要 CREATEDB：只清空並重建 `istudy_restore_test` 的 public schema。
- `pg_dump` 的版本不能比伺服器舊：Agent 映像改裝 `postgresql-client-18`；主機的 `pg_dump` 由 PostgreSQL 18 套件提供。

---

## 七、失敗排除

| 狀況 | 原因與處理 |
|---|---|
| 預檢 ✗「找不到主機 PostgreSQL」 | `pg_lsclusters` 不存在：PostgreSQL 套件（postgresql-common）未安裝。iStudy 不會自動安裝資料庫。 |
| 預檢 ✗「PostgreSQL 18 叢集沒有執行」 | `sudo systemctl start postgresql`，再 `sudo pg_lsclusters` |
| 預檢 ✗「主機只有其他版本」 | 只有 PostgreSQL 17 或其他版本；iStudy dev.53 要求 18。請先確認主機的資料庫規劃。 |
| 第 8 步「已有角色 istudy，但具有 SUPERUSER／CREATEDB…」 | 不是 iStudy 建立的同名角色。確認用途後再處理；iStudy 不會修改它。 |
| 第 8 步「已有資料庫 …，擁有者是 …」 | 同名資料庫不是 iStudy 的。iStudy 不會刪除或覆蓋。 |
| 第 8 步「pg_hba.conf 更新後檢查失敗，已還原原本的內容」 | 檔案內容有其他錯誤：`sudo -u postgres psql -c "SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL"` |
| 第 8 步「istudy 帳號無法以密碼經 Unix socket 登入」 | ① `pg_hba.conf` 中 iStudy 規則不在最前面，或前面有 include 檔案的規則；② `password_encryption` 或 `scram` 被停用；③ socket 目錄其他人沒有 x 權限（`ls -ld /var/run/postgresql`）。 |
| 容器 Log：`connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory` | `.env` 的 `PG_SOCKET_DIR`／`DB_PORT` 與主機不同：`sudo -u postgres psql -Atc 'SHOW unix_socket_directories; SHOW port;'`，再執行 `sudo istudyctl bootstrap --bundle <套件>`（會自動更新）。 |
| 容器 Log：`Permission denied`（socket） | socket 權限被改過（`unix_socket_permissions`）或目錄缺少其他人的 x 權限；請恢復 Ubuntu 預設（目錄 2775、socket 0777）。 |
| `docker compose up` 顯示 bind source path does not exist | 主機 PostgreSQL 還沒啟動，socket 目錄不存在；compose 設定 `create_host_path: false`，Docker 不會自行建立（避免以 root 建立而讓 PostgreSQL 無法啟動）。啟動 PostgreSQL 後再試。 |
| 開機後 iStudy 沒有啟動（`check-db` 失敗） | PostgreSQL 90 秒內沒有啟動：`sudo systemctl status postgresql`，正常後 `sudo systemctl restart istudy`。 |
| Agent 備份失敗「pg_dump: error: aborting because of server version mismatch」 | Agent 映像的 pg_dump 比伺服器舊：確認使用 dev.53 的 `istudy/agent` 映像（postgresql-client-18）。 |
| 想撤銷 pg_hba.conf 的修改 | `sudo cp /var/lib/istudy/pg_hba.conf.before-istudy "$(sudo -u postgres psql -Atc 'SHOW hba_file;')"`，再 `sudo -u postgres psql -c 'SELECT pg_reload_conf();'`（iStudy 會因此無法連線）。 |

---

## 八、ZFS 上的 `Backups/`、`Installers/`（一次性人工處理）

使用者已要求刪除，但**還沒收到刪除後的實測結果**。部署腳本**不會**刪除這兩個資料夾（也不會掃描它們：教材只從 `/Media/videos` 掃描）。

先檢查（唯讀）：

```bash
sudo ls -la /Media
sudo du -sh /Media/Backups /Media/Installers 2>/dev/null
sudo zfs list -t snapshot                          # 有快照時，刪除後空間不會立刻釋放
```

確認內容不再需要後，再由使用者自行執行（不可復原）：

```bash
sudo rm -rf --one-file-system /Media/Backups /Media/Installers
sudo ls -la /Media                                 # 確認只剩 iStudy 的教材目錄
```

執行後請保留 `ls -la /Media` 的輸出，作為「已驗證刪除完成」的依據。
