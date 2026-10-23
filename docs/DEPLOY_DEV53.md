# iStudy 1.0.0-dev.53：Ubuntu 26.04.1 安裝與部署後驗收

## 0. 先看：你拿到的是什麼

| 檔案 | 內容 | 能不能直接部署 |
|---|---|---|
| `istudy_1.0.0-dev.53-src.zip` | 原始碼，加上**已打包並測試**的 Server App 安裝檔 `dist/istudy-server-app_1.0.0-dev.53_amd64.deb` | **不能**：沒有 Docker 映像 |
| `istudy_1.0.0-dev.53-offline.zip` | 完整離線部署包：Docker 映像、Server App、istudyctl、compose、Nginx、polkit、SHA256SUMS | 可以；**要在 Ubuntu 上用第 1 節一次性產生** |

開發環境沒有 Docker daemon，也連不到 Docker Hub、Ubuntu／Debian apt、apt.postgresql.org、PyPI，無法建置 `istudy/php`、`istudy/agent` 映像。所以完整離線部署包**沒有在開發環境產生**，也沒有用原始碼冒充。

Server App .deb 的 SHA256：`1a9f376ff3f3e78bbe07d55e100ba0d4d7664f585a07ada7de8d4dad4168ba66`

固定環境：
- Ubuntu 26.04.1 LTS，主機 IP `10.3.3.100`
- SSD：系統、Docker、PostgreSQL 18、`/opt/istudy`、`/srv/istudy/backup`、`/srv/istudy/updates`
- ZFS Mirror `/Media`：只放教材與衍生檔案
- Docker 只有 php、nginx、agent 三個服務；資料庫是主機的 PostgreSQL 18（經 Unix socket，不開 5432 到 LAN）

---

## 1. 一次性建置完整離線部署包（在 Server 本機，需要網路，約 20～40 分鐘）

```bash
# 1-1 解壓、確認 Server App 安裝檔
cd ~ && unzip istudy_1.0.0-dev.53-src.zip && cd ~/istudy
sha256sum dist/istudy-server-app_1.0.0-dev.53_amd64.deb   # 要等於上面的 SHA256

# 1-2 確認建置工具（這台主機已有 Docker CE＋Compose plugin；不要再裝 docker.io）
docker compose version
docker buildx version
sudo docker info --format '{{.ServerVersion}}'    # Docker 服務要在執行

# 1-3 建置映像並產生完整離線部署包
mkdir -p ~/istudy-debs                             # Docker 已安裝：留空即可
sudo python3 deploy/build_bundle.py --debs ~/istudy-debs --zip
#   選用：Whisper 字幕模型（沒有也能部署，只是不會自動產生字幕）
#   pip install --user huggingface_hub
#   ~/.local/bin/huggingface-cli download Systran/faster-whisper-small --local-dir ~/istudy-models/faster-whisper-small
#   sudo python3 deploy/build_bundle.py --debs ~/istudy-debs --models ~/istudy-models --zip
```

成功時最後會顯示：

```
✓ 離線部署套件：/home/<帳號>/istudy/dist/istudy-server-1.0.0-dev.53-offline（N 個檔案）
✓ 完整離線部署包：/home/<帳號>/istudy/dist/istudy_1.0.0-dev.53-offline.zip（約 x GB）
  SHA256：…（另存 istudy_1.0.0-dev.53-offline.zip.sha256）
```

建置時會下載：
- 基礎映像：`ubuntu:24.04`、`php:8.3.26-fpm-bookworm`、`nginx:1.28.0-alpine`
- 套件：Ubuntu／Debian 套件、apt.postgresql.org 的 `postgresql-client-18`、PyPI 的 faster-whisper 等

任何一步失敗都會停止並顯示錯誤，**不會產生 offline.zip**。請把終端機最後 30 行傳給我。

| 建置錯誤 | 處理 |
|---|---|
| `找不到 Server App .deb` | ZIP 內的 .deb 被移動或刪除：`python3 deploy/package_server_app.py`（會從 GitHub 下載 Electron） |
| `E: Unable to locate package postgresql-client-18` | apt.postgresql.org 連不到或沒有這個套件：把錯誤訊息傳給我 |
| `docker: 'buildx' is not a docker command` | `sudo apt install docker-buildx-plugin`（Docker CE 的套件） |
| `Cannot connect to the Docker daemon` | `sudo systemctl start docker` |

`--skip-images` 只產生測試用的結構，**不能部署**；加 `--zip` 會直接拒絕。

---

## 2. 安裝前檢查（唯讀）

`install.sh` 第 2 步會自動檢查，有 ✗ 時停止、不安裝任何東西。也可以先自己看：

```bash
findmnt -T /Media -o SOURCE,FSTYPE,TARGET            # Media  zfs  /Media
sudo zpool list && sudo zpool status -x             # 全部 ONLINE（pool 名稱以 zpool list 為準；程式不依賴 pool 名稱）
sudo pg_lsclusters                                   # 18  main  5432  online
sudo -u postgres psql -Atc 'SHOW unix_socket_directories;'   # /var/run/postgresql
sudo -u postgres psql -Atc "SELECT rolname FROM pg_roles WHERE rolname='istudy';"          # 第一次部署前：沒有輸出
sudo -u postgres psql -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'istudy%';"  # 第一次部署前：沒有輸出
docker compose version
ss -ltn | grep -E ':(4080|4081|5432)\b' || true      # 4080／4081 沒有；5432 只在 127.0.0.1
groups | grep -w sudo                                # 部署帳號要在 sudo 群組
pgrep -af polkit                                     # 要有 polkit 驗證代理（Server App 的系統密碼視窗）
df -h /                                              # 系統碟剩餘 30 GB 以上
```

| 安裝前檢查項目 | ✗（停止） | ⚠（可確認後繼續） |
|---|---|---|
| PostgreSQL 18（主機） | 沒有 18 版叢集、或沒有執行 | — |
| /Media | 不存在、不是掛載點、**不是 ZFS**、不可寫入 | ZFS pool 不是 ONLINE、剩餘 < 20% |
| Docker Compose | 沒有 Compose，套件也沒有 Docker 安裝檔 | Docker 服務沒有執行（部署時會啟動） |
| 連接埠 | 4080／4081 被其他程式占用 | — |
| 系統碟、RAM | 剩餘 < 30 GB、RAM < 8 GB | — |
| 其他 | 不是 Ubuntu x64 | IP 不是 10.3.3.100、沒有 /dev/dri、NTP、沒有桌面或 polkit 驗證代理 |

---

## 3. 安裝與部署

```bash
# 3-1 （建置在別台電腦時）把 offline.zip 複製到 Server 後先驗證
sha256sum -c istudy_1.0.0-dev.53-offline.zip.sha256
unzip istudy_1.0.0-dev.53-offline.zip                 # → istudy-server-1.0.0-dev.53-offline/
#     建置在 Server 本機時可以直接用：cd ~/istudy/dist/istudy-server-1.0.0-dev.53-offline

# 3-2 安裝（5 步：必要檔案與雜湊 → 安裝前檢查 → istudyctl → polkit → Server App）
cd istudy-server-1.0.0-dev.53-offline
sudo ./install.sh
```

| install.sh 結束碼 | 意思 |
|---|---|
| 0 | 安裝完成（尚未部署）→ 下一步 |
| 2 | 套件缺少必要檔案或雜湊不符（例如用了 `--skip-images` 的結構套件）：重新產生或重新複製 |
| 3 | 安裝前檢查有 ✗：照畫面處理後重跑；**沒有安裝任何 iStudy 程式** |
| 4 | istudyctl 已安裝但 Server App 沒裝成功：改用 `sudo ./install.sh --cli` |

3-3 部署（14 步）：
- 從選單開 **iStudy Server App**（不要用 sudo），按 **開始部署**，輸入 Super 帳號與密碼。跳出系統密碼視窗時輸入 Ubuntu 密碼。
- 桌面無法使用時：`sudo ./install.sh --cli`
- 第 8 步「資料庫」會在主機 PostgreSQL 18 建立：
  - 角色 `istudy`（最小權限）
  - 資料庫 `istudy`、`istudy_restore_test`
  - `pg_hba.conf` 最前面一條 `local istudy,istudy_restore_test istudy scram-sha-256`（修改前備份到 `/var/lib/istudy/pg_hba.conf.before-istudy`）
- 第 8 步不改 `listen_addresses`、不動其他資料庫與規則。可以重複執行；遇到不是 iStudy 的同名角色或資料庫會停止。

3-4 首次設定精靈（9 步）：
- 第 6 步「備份」：備份儲存於 SSD `/srv/istudy/backup`，不需要外接硬碟。按「執行第一次備份並測試還原」。

---

## 4. 部署後驗收（請實際執行並保留輸出）

```bash
# 4-1 只有三個服務（沒有 db）
sudo docker compose --project-directory /opt/istudy --env-file /opt/istudy/.env -f /opt/istudy/compose.yaml ps --format '{{.Service}} {{.State}}'

# 4-2 版本一致：都是 1.0.0-dev.53
curl -s http://127.0.0.1:4081/api/health ; echo
cat /opt/istudy/VERSION.json | grep '"server"'
dpkg -s istudy-server-app | grep Version             # 1.0.0~dev.53（Debian 的寫法）

# 4-3 自我檢查（PostgreSQL 18、API 與資料庫、Agent 心跳、管理 API 隔離、Quick Sync、文件轉換）
sudo istudyctl doctor
sudo istudyctl status                                # agent_heartbeat_age_sec 小於 90

# 4-4 PostgreSQL 18：權限、規則、socket 掛載
sudo -u postgres psql -Atc "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='istudy';"   # f|f|f
sudo -u postgres psql -Atc "SELECT line_number, database, user_name, auth_method FROM pg_hba_file_rules ORDER BY line_number LIMIT 2;"
sudo -u postgres psql -d istudy -Atc "SELECT count(*) FROM pg_tables WHERE schemaname='public';"               # 27
sudo docker inspect istudy-php-1 istudy-agent-1 --format '{{.Name}} {{range .Mounts}}{{if eq .Destination "/var/run/postgresql"}}{{.Source}} RW={{.RW}}{{end}}{{end}}'
#    → 兩個都是 /var/run/postgresql RW=false
ss -ltn | grep ':5432\b'                             # 只有 127.0.0.1（或沒有）；不可是 0.0.0.0

# 4-5 LAN 與管理 API 隔離（在另一台電腦）
#    http://10.3.3.100:4080/api/health 有回應；http://10.3.3.100:4080/api/admin/overview 是 404；10.3.3.100:4081 連不到

# 4-6 備份與還原（只動 iStudy 的資料庫）
sudo istudyctl backup
ls -l /srv/istudy/backup/                            # istudy-db-<時間>.sql，權限 -rw-------
sudo istudyctl restore --file /srv/istudy/backup/istudy-db-<時間>.sql --target test   # 列出筆數；正式資料不變
#    Server App「備份」→「立即備份並測試還原」→ 表格「還原測試」欄要顯示 ✓ 筆數相符

# 4-7 重新開機
sudo reboot
#    重新登入後：systemctl status istudy postgresql；再做 4-1、4-3
```

驗收用的測試帳號與教材，完成後請刪除（或在匯入正式教材前驗收），不要留在正式資料。

---

## 5. 失敗排除（常見）

| 狀況 | 處理 |
|---|---|
| install.sh 結束碼 3：「PostgreSQL 18（主機）」✗ | `sudo systemctl start postgresql`、`sudo pg_lsclusters` |
| install.sh 結束碼 3：「/Media 不是 ZFS」 | `findmnt -T /Media`；ZFS 沒有掛載：`sudo zfs mount Media`（請確認後再執行） |
| install.sh 結束碼 3：「沒有 Docker Compose」 | 主機原本有 Docker CE＋Compose plugin：確認 `docker compose version`；不要改裝 docker.io |
| 第 8 步失敗 | 依訊息看 [DEPLOY_PG18.md](DEPLOY_PG18.md)「失敗排除」：同名角色／資料庫、pg_hba、socket 目錄 |
| 開機後沒啟動（`check-db` 失敗） | 主機 PostgreSQL 90 秒內沒有起來：`sudo systemctl status postgresql`，正常後 `sudo systemctl restart istudy` |
| 其他 | `sudo istudyctl doctor` 與 `ls -t /var/log/istudy/ \| head -3`（報告不含密碼）傳給我 |

更多細節：
- 一般部署與 Client：[DEPLOY.md](DEPLOY.md)
- 資料庫：[DEPLOY_PG18.md](DEPLOY_PG18.md)
- `/Media/Backups`、`/Media/Installers` 的一次性人工處理：DEPLOY_PG18.md 第八節
