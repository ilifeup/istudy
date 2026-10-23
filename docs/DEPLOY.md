# iStudy 1.0.0-dev.53 部署：Ubuntu 26.04.1＋XFCE Server，與 Client 安裝

這一版是 MVP：**Server 後端**（Docker：PHP API、Nginx、Python Agent；資料庫是 **Ubuntu 主機的 PostgreSQL 18**）＋**Server App**（Super 專用，裝在 Server 本機）＋**Client App**（Win10、Win11、Ubuntu、Win7 四個版別，見第六節）。
dev.53 起不再使用 Docker 內的 PostgreSQL 17；主機 PostgreSQL 18 的初始化、權限、備份還原與驗收見 **[DEPLOY_PG18.md](DEPLOY_PG18.md)**。

| 項目 | 這台 Server |
|---|---|
| 作業系統 | Ubuntu 26.04.1 LTS x64 |
| 桌面 | XFCE＋X11＋LightDM（**不需要 GNOME**，現有設定不用改） |
| 操作方式 | 接螢幕直接操作，或不接螢幕用 RustDesk；兩種都用同一個 Server App |
| 教材 | ZFS 鏡像（pool／dataset `Media`，mirror-0）掛在 `/Media`；**只放教材與衍生檔案** |
| 資料庫 | 主機的 PostgreSQL 18（資料目錄 `/var/lib/postgresql/18/main`，系統 SSD）；iStudy 使用自己的資料庫 `istudy` 與角色 `istudy`，經 Unix socket 連線，不開新的 TCP 連接埠 |
| 系統 SSD | Ubuntu、`/opt/istudy`、`/etc/istudy`、Docker（`/var/lib/docker`）、PostgreSQL 18、備份 `/srv/istudy/backup`、Client 更新檔 `/srv/istudy/updates` |

以下指令都在 Server 上的 XFCE 終端機執行（RustDesk 連進去也可以）。準備階段需要網路；部署完成後 Server 不需要連網。

---

## 一、準備（需要網路，約 20～30 分鐘）

```bash
# 1. 解壓（放在家目錄，Server App 會自動找到部署套件）
cd ~ && unzip istudy_1.0.0-dev.53-src.zip && cd ~/istudy

# 2. 確認 Docker 與 PostgreSQL 18（這台主機已安裝 Docker CE＋Compose plugin，不要再裝 docker.io）
docker compose version              # 要有版本號；沒有時才安裝 Docker（不要混裝 docker.io 與 docker-ce）
docker buildx version               # 建置映像需要（Docker CE 的 docker-buildx-plugin）
sudo pg_lsclusters                  # 要看到 18  main  5432  online
sudo apt install -y pkexec acl smartmontools

# 3. Server App（.deb）：ZIP 內已附 dist/istudy-server-app_1.0.0-dev.53_amd64.deb（已打包並測試）。
#    要自己重新打包時（會從 GitHub 下載 Electron 44.9.0 並比對 SHA256）：python3 deploy/package_server_app.py

# 4. 建置 Docker 映像並產生部署套件（約 10～20 分鐘）
mkdir -p ~/istudy-debs            # Docker 已安裝，這個資料夾留空即可
sudo python3 deploy/build_bundle.py --debs ~/istudy-debs --zip
#    → ~/istudy/dist/istudy-server-1.0.0-dev.53-offline/（部署套件資料夾）
#    → ~/istudy/dist/istudy_1.0.0-dev.53-offline.zip＋.sha256（完整離線部署包，可複製到 USB）
#    已經打包好 Client 安裝檔（第六節）時加上：--clients ~/istudy/dist/clients
```

第 4 步如果失敗，請把終端機最後 30 行訊息傳給我。
第 4 步找不到第 3 步產生的 Server App .deb 時會直接停止（不會產生缺少 Server App 的套件）；只打算用終端機 `sudo ./install.sh --cli` 部署時，才加 `--no-server-app`。

（可選）字幕模型：沒有模型也能部署，只是不會自動產生字幕。字幕用 FunASR（Paraformer-zh 預設、SenseVoiceSmall），部署後執行 `sudo istudyctl asr-models --fetch all` 下載（約 450 MB，需要網路，比對 SHA256）。
要放進離線部署套件時，把已下載的模型目錄（結構同 `/opt/istudy/models`，裡面是 `asr/…`）用 `--models` 帶入：

```bash
sudo python3 deploy/build_bundle.py --debs ~/istudy-debs --models ~/istudy-models   # ~/istudy-models/asr/paraformer-zh …
```

dev.59 起已移除 Whisper（faster-whisper）引擎，部署套件不再收 `faster-whisper-*` 模型。

---

## 二、部署

### 1. 確認條件

```bash
findmnt /Media                              # 必須是掛載點（ZFS）
ss -ltn | grep -E ':(4080|4081)\b'          # 必須沒有輸出（這兩個埠給 iStudy 用）
sudo pg_lsclusters                          # PostgreSQL 18 必須 online（iStudy 不另外安裝資料庫）
sudo -u postgres psql -Atc 'SHOW unix_socket_directories;'   # 通常是 /var/run/postgresql
groups | grep -w sudo                       # 你的帳號必須在 sudo 群組（系統密碼視窗要用）
pgrep -af polkit                            # 要看到 polkit 驗證代理（見下方「系統密碼視窗」）
```

教材放在 `/Media/<課程資料夾>/…`（dev.59 起；第一層資料夾＝課程。`playback`、`subtitles`、`doc-cache`、`Backups`、`Installers`、`lost+found` 等名稱保留給系統，不會被當成教材）。也可以部署完成後再放；大量複製後執行 `sudo istudyctl media-acl` 檢查 Agent 能不能讀取與刪除原檔。

### 2. 安裝

```bash
cd ~/istudy/dist/istudy-server-1.0.0-dev.53-offline
sudo ./install.sh
```

`install.sh` 共 5 步：①確認必要檔案並比對所有檔案的雜湊值 ②**安裝前檢查**（PostgreSQL 18、ZFS `/Media`、Docker Compose、連接埠、磁碟、桌面；有 ✗ 時停止，什麼都不安裝）③安裝 `istudyctl` ④polkit 規則 ⑤Server App。出現 ⚠ 時看下方的故障排除。

### 3. 開 Server App，按「開始部署」

1. 從 XFCE 的應用程式選單開啟 **iStudy Server App**（在「教育」或「系統」分類），或在終端機輸入 `istudy-server-app`。**不要用 sudo 開。**
2. 畫面顯示「初始部署」，部署套件會自動選好；沒有的話按 **選擇資料夾…**，選 `~/istudy/dist/istudy-server-1.0.0-dev.53-offline`。
3. 在第 9 步填 **Super 帳號**與**密碼**（至少 10 字，輸入兩次）。
4. 按 **開始部署**。跳出系統密碼視窗時，輸入你的 Ubuntu 登入密碼。
5. 預檢有 ⚠ 時（例如 NTP 未同步），確認可以接受就按 **了解，繼續**；有 ✗ 時依說明處理後按 **重新檢查**。
6. 約 10～20 分鐘後顯示「部署完成」，以 Super 登入，會自動開啟 **首次設定精靈**（9 步，可以中途離開，下次繼續）。

可以重複執行：中途失敗修正後再按「開始部署」，會從未完成的步驟繼續，不會刪除任何資料。

沒有桌面或 Server App 打不開時，可以改用終端機做同一件事：

```bash
sudo ./install.sh --cli        # 在終端機詢問 Super 帳號與密碼
```

### 4. 首次設定精靈要注意的事

- **第 3 步 教材盤點**：先試掃描（只統計，不寫入），看「需要轉檔的影片」與「預估增加的空間」，確認後再正式掃描。
- **第 4 步 試轉檔**：新安裝時**批次轉檔是暫停的**；選一支 .MTS 試轉，四項檢查通過、播放抽查沒問題，再按「確認，開放批次轉檔」。之後轉檔只在系統工作時段（預設 22:30～03:00）進行。
- **第 6 步 備份磁碟**：備份已決定放在系統 SSD 的 `/srv/istudy/backup`，**不需要外接硬碟**（畫面文字仍寫外接硬碟，可直接略過「設為備份磁碟」）；在這一步執行第一次備份並測試還原即可。
- **第 7 步 Client 電腦**：目前還沒有 Client App，直接略過。

---

## 三、RustDesk 操作

- 和接螢幕時用同一個 Server App，操作完全一樣。
- 不接螢幕時的虛擬解析度常是 1024×768；Server App 最小可以縮到 1024×640。
- 系統密碼視窗會出現在同一個桌面上，RustDesk 看得到、也可以直接輸入。
- 視窗出現黑畫面或白畫面時，關掉 App，在終端機改用：`istudy-server-app --disable-gpu`

---

## 四、部署後常用指令

```bash
sudo istudyctl status            # 容器、Agent 心跳、版本
sudo istudyctl doctor            # 自我檢查
sudo istudyctl uem               # 主機盤點（只讀）：磁碟歸屬、ZFS、服務、連接埠、版本；--mode full 另加自我檢查
                                 # 報告在 /var/lib/istudy/uem/latest.html，Server App「主機盤點（UEM）」也看得到
sudo istudyctl asr-models        # 字幕辨識模型狀態；--fetch all 下載 FunASR 模型（需要暫時連網）
sudo istudyctl media-acl         # 教材權限檢查（只讀）；--fix 確認後補上 Agent 的 ACL（不碰系統資料夾、不改檔案內容）
sudo istudyctl encoder           # 正式轉檔編碼器；encoder vaapi|qsv|auto 切換（dev.59 預設 vaapi）
sudo istudyctl restart           # 重新啟動 iStudy 服務
sudo istudyctl reset-super       # Super 忘記密碼
sudo istudyctl backup            # 立即備份資料庫到 /srv/istudy/backup（主機 pg_dump）
sudo istudyctl restore --file <備份檔> --target test    # 先還原到測試資料庫檢查（不影響正式資料）
sudo docker compose --project-directory /opt/istudy --env-file /opt/istudy/.env -f /opt/istudy/compose.yaml logs --tail 50
                                 # 各容器最後 50 行 Log
```

重新開機後服務會自動啟動（等 `/Media` 掛載完成、主機 PostgreSQL 的 socket 出現後才啟動）。

移除 Server App：`sudo apt remove istudy-server-app`（不影響 Server 服務與資料）。

---

## 五、故障排除

| 狀況 | 處理 |
|---|---|
| 按「開始部署」顯示「**沒有跳出系統密碼視窗**」 | XFCE 預設可能沒有啟動 polkit 驗證代理。執行 `sudo apt install policykit-1-gnome`（找不到這個套件時用 `apt-cache search polkit \| grep -i agent` 找另一個），然後登出再登入桌面（或重開機後用 RustDesk 重新連線）。也可以直接用 `sudo ./install.sh --cli`。 |
| 顯示「**找不到 pkexec**」 | `sudo apt install pkexec` |
| 顯示「**系統密碼不正確或沒有管理員權限**」 | 確認密碼；`groups` 裡要有 `sudo`。 |
| 選單找不到 Server App | `dpkg -s istudy-server-app` 確認已安裝；在終端機執行 `istudy-server-app`，把錯誤訊息傳給我。 |
| Server App 開了馬上關掉（終端機出現 sandbox 字樣） | `ls -l /opt/istudy-server-app/chrome-sandbox` 要是 `-rwsr-xr-x root root`；不是的話：`sudo chown root:root /opt/istudy-server-app/chrome-sandbox && sudo chmod 4755 /opt/istudy-server-app/chrome-sandbox` |
| 預檢 ✗「/Media 掛載」 | `findmnt /Media`、`zfs list` 確認 ZFS 已掛載到 /Media。 |
| 預檢 ✗「連接埠 4080／4081」 | `sudo ss -ltnp \| grep -E ':(4080\|4081)\b'` 看是哪個程式占用。 |
| Server App 顯示「伺服器無法連線」 | `sudo istudyctl status`；`/Media` 未掛載時服務刻意不啟動（避免把空目錄掃描成全部遺失）。 |
| 預檢 ✗「PostgreSQL 18（主機）」或第 8 步「資料庫」失敗 | 見 [DEPLOY_PG18.md](DEPLOY_PG18.md) 的「失敗排除」：叢集未啟動、socket 目錄不同、pg_hba 規則、同名角色或資料庫已存在。 |
| 開機後 iStudy 沒有啟動，`systemctl status istudy` 顯示 check-db 失敗 | 主機 PostgreSQL 90 秒內沒有啟動：`sudo systemctl status postgresql`，啟動後 `sudo systemctl restart istudy`。 |
| 原檔刪除失敗：「沒有權限刪除原檔」 | ZFS 沒有開 POSIX ACL。`zfs get acltype <pool/dataset>` 是 `off` 時，**確認後**再執行 `sudo zfs set acltype=posixacl xattr=sa <pool/dataset>`，然後 `sudo istudyctl bootstrap --bundle <部署套件>` 重套權限。這是 ZFS 屬性變更，請你決定是否執行。 |

回報問題時請附上：

```bash
sudo istudyctl doctor
ls -t /var/log/istudy/ | head -3          # 最新的部署報告（不含密碼）
```

以及 Server App「Log」分頁的畫面，或終端機的錯誤訊息。

---

## 六、Client App（Win10／Win11／Ubuntu／Win7）

### 1. 第一次打包安裝檔

> **現況：原始碼準備完成，安裝檔尚未實際建置。** 開發環境連不到 npm，`npm install` 與 electron-builder 都沒有執行過；以下是第一次實際打包的步驟，請保留完整的終端機訊息。

**必要軟體與條件**

| | Windows 版（Win10、Win11、Win7） | Ubuntu 版 |
|---|---|---|
| 在哪裡打包 | 一台 **Windows 10 或 11（64 位元）** 電腦 | 一台 **Ubuntu**（可以就是 Server 本機） |
| Node.js | Node.js 22 LTS（x64），從 nodejs.org 下載安裝 | `sudo apt install -y nodejs npm`（20 版以上即可） |
| 網路 | 需要：下載 npm 套件、Electron（44.9.0、Win7 版 22.3.27）、NSIS 等 | 需要：下載 npm 套件、Electron 44.9.0、fpm |
| 磁碟空間 | 約 3 GB | 約 2 GB |

Windows（開「命令提示字元」，切到解壓縮後的 `istudy` 資料夾）：

```bat
cd C:\istudy-src\istudy
npm install --no-audit --no-fund
node scripts\package_client.mjs --channel win10
node scripts\package_client.mjs --channel win11
node scripts\package_client.mjs --channel win7
```

Ubuntu：

```bash
cd ~/istudy
npm install --no-audit --no-fund
node scripts/package_client.mjs --channel ubuntu
```

- `npm install` 第一次會產生 `package-lock.json`（ZIP 裡沒有）：請保留，之後同一份專案可用 `npm ci` 重裝同一組套件，也請把它回傳，下一版會放進 ZIP。
- 打包成功時每個版別會依序顯示：
  - `原始碼建置：main.js 已包含 electron-updater（打包後另檢查安裝產物）`（Windows 版）
  - `✓ 打包產物靜態驗證：app.asar 內的主程序包含 electron-updater（自動更新功能本身尚未實機測試）`
  - `✓ latest.yml：版本 1.0.0-dev.53、安裝檔名稱、SHA512 與 .blockmap 一致`
  - `✓ 已發布 Win10 1.0.0-dev.53（Electron 44.9.0）→ dist/clients/win10/：…exe、…exe.blockmap、latest.yml`
- 產出：`dist/clients/win10/`、`win11/`、`win7/`（.exe＋.blockmap＋latest.yml）、`dist/clients/ubuntu/`（只有 .deb；Ubuntu 版採手動更新）。
- 任何一項檢查不通過，打包會**失敗並列出全部問題**，不會發布到 `dist/clients/<版別>/`（原有的檔案保留）；這次的建置輸出留在 `dist/.client-build/<版別>/out/` 供查看。

打包時的常見錯誤：

| 訊息 | 處理 |
|---|---|
| `找不到 electron-builder` | 還沒執行 `npm install`，或 `npm install` 中途失敗（看它的錯誤訊息，多半是網路）。 |
| `找不到 electron-updater` | 同上；Windows 版必須包含自動更新，所以不會繼續打包。 |
| `Windows 安裝檔請在 Windows 10／11 上打包…` | 在 Ubuntu 上打包 Windows 版：改到 Windows 打包（或 `sudo apt install wine`）。 |
| `Ubuntu .deb 只能在 Linux（Ubuntu）上打包` | 在 Windows 上打包 Ubuntu 版：改到 Ubuntu 打包。 |
| Windows 上 `Cannot create symbolic link : 用戶端沒有這項特殊權限`（winCodeSign） | 以「系統管理員身分」開命令提示字元再執行一次，或在 Windows 設定開啟「開發人員模式」。 |
| `main.js 沒有正確包含 electron-updater`，或 `驗證失敗，沒有發布到 dist/clients/…`（下面列出問題） | 這次的安裝檔不合格，沒有發布；原有的檔案不受影響。把整段訊息回傳。 |
| 下載 Electron 很慢或逾時 | 重新執行同一個指令（已下載的部分會快取）。 |

### 2. 放到 Server（更新來源）

在 Server 上（`dist/clients/` 複製到 `~/istudy/dist/clients/`）：

```bash
sudo python3 deploy/build_bundle.py --debs ~/istudy-debs --clients ~/istudy/dist/clients
cd ~/istudy/dist/istudy-server-1.0.0-dev.53-offline && sudo ./install.sh
```

然後在 Server App「主機維護 → 從 USB 更新」選這個套件（已部署過的 Server），或「開始部署」（第一次）。
安裝檔會放到 `/srv/istudy/updates/<版別>/`，Client 從 `http://10.3.3.100:4080/updates/<版別>/` 檢查更新。

只想先試 Client、不重新部署時，可以直接複製：

```bash
sudo cp ~/istudy/dist/clients/win10/* /srv/istudy/updates/win10/
```

### 3. 在 Client 電腦安裝

| 版別 | 安裝 |
|---|---|
| Win10／Win11 | 執行 `iStudy-Client-Win10-…exe` 或 `iStudy-Client-Win11-…exe`（不需要系統管理員權限）。版別和作業系統不符時，安裝程式會停止並說明該用哪一個。 |
| Win7 | 執行 `iStudy-Client-Win7-…exe`（Windows 7 SP1 x64） |
| Ubuntu | `sudo apt install ./istudy-client-ubuntu_1.0.0-dev.53_amd64.deb`（需要 sudo；由管理者安裝） |

安裝檔可以從 USB 複製，或在 Client 電腦的瀏覽器開 `http://10.3.3.100:4080/updates/win10/iStudy-Client-Win10-1.0.0-dev.53.exe`（依版別替換路徑與檔名）。

安裝時會自動：
- 寫入 Server 位址設定檔：Windows `%ProgramData%\iStudy\client.json`，Ubuntu `/etc/istudy/client.json`。預設內容是 `{"server":"http://10.3.3.100:4080"}`，已經有這個檔案就不覆蓋。
  Windows 版安裝在使用者資料夾、不需要系統管理員；Windows 預設允許一般使用者在 `%ProgramData%` 建立資料夾與檔案，所以一般帳號也能寫入。萬一這台電腦的權限被改過而寫不進去，安裝會照常完成，App 使用內建的同一個預設位址。Server 位址不是 10.3.3.100:4080 時，才需要以系統管理員編輯這個檔案。
- 設定開機後自動啟動。Windows 是第一次開啟 App 後生效；Ubuntu 是安裝後所有使用者登入桌面時都會啟動。
- 關閉視窗時縮到系統匣，心跳繼續送；要完全結束，從系統匣選「結束 iStudy」。

### 4. 更新到新版

| 版別 | 方式 |
|---|---|
| Win10、Win11、Win7 | **自動更新**：把新版的 .exe、.blockmap、latest.yml 放到 Server 的 `/srv/istudy/updates/<版別>/`。App 每次啟動與每 6 小時檢查一次，下載完成後顯示「有新版本，重新啟動即可更新」，從系統匣結束再開啟即可。每個版別只讀自己的資料夾（Win7 版只讀 `/updates/win7/`），不會裝到其他版別。自動更新**尚未實機測試**；不行時直接執行新版 .exe 覆蓋安裝（設定與登入資料保留）。 |
| Ubuntu | **手動更新**（.deb 更新需要系統管理員權限，學生帳號無法完成，所以 App 不自動更新）：管理者在每台 Ubuntu Client 執行 `sudo apt install ./istudy-client-ubuntu_<新版>_amd64.deb`。升級會保留 `/etc/istudy/client.json` 與每個使用者的 `~/.config/iStudy`。移除：`sudo apt remove istudy-client`（client.json 保留）。 |

App 的 API 等級低於 Server 要求時，登入頁會顯示「✗ App 需要更新」，Server App「Client 版本」也會列出待更新的電腦。

### 5. 安裝後（Q-05：收集 PC 資訊）

1. 在這台 Client 用 **Super** 登入，開「PC 資訊」，按 **上傳到 Server**（會送作業系統、CPU、記憶體、顯示卡、網卡、螢幕、App 版本）。
2. 回到 Server App「系統檢查與管理 → 5. Client 硬體通用檢查」：可以看到每台電腦的資料，以及「已收到 N 台：Win10 x 台、Win11 y 台…」。
3. 在 Server App「WOL 管理 → 待登記設備」登記這台電腦（名稱、廣播位址），並試喚醒一次。
4. 登出 Super，交給學生使用。

### 6. Client 故障排除

| 狀況 | 處理 |
|---|---|
| 登入頁「✗ 伺服器無法連線」 | Client 電腦的瀏覽器開 `http://10.3.3.100:4080/api/health`，要看到 `"ok":true`；不通時檢查網路與 Server 防火牆（4080）。Server 位址不同時修改 client.json 後重開 App。 |
| 安裝程式說「這是 iStudy Win10 版，只能安裝在…」 | 訊息會列出這台電腦偵測到的 Windows 版本與組建號；改用對應版別的安裝檔。Win7 需先裝 SP1（組建號 7601）。Windows 10 升級成 11 之後，Win10 版的自動更新會停止，請改裝 Win11 版。 |
| 想知道 App 用的是哪個 Server 位址 | Client Log 開頭有「啟動 …，Server http://…」；找不到 client.json 時另有一筆「使用內建預設位址」。 |
| Windows 出現 SmartScreen 警告 | 安裝檔沒有程式碼簽章；按「其他資訊 → 仍要執行」。 |
| Ubuntu 開啟後馬上關閉 | `ls -l /opt/iStudy/chrome-sandbox` 要是 `-rwsr-xr-x root root`；不是的話 `sudo chmod 4755 /opt/iStudy/chrome-sandbox`。 |
| Windows 沒有收到自動更新 | 確認 `/srv/istudy/updates/<版別>/` 有新版的 .exe、.blockmap 與 `latest.yml`，而且 latest.yml 的 version 比目前版本新；Client Log 會記「更新檢查失敗：…」。App 每次啟動與每 6 小時檢查一次。自動更新**尚未實機測試**；不可用時直接執行新版 .exe。Ubuntu 版不自動更新（見「4. 更新到新版」）。 |
| Client 的 Log | Windows：`%APPDATA%\iStudy\logs`；Ubuntu：`~/.config/iStudy/logs`。每天第一次啟動時自動上傳，Server App「Log → Client 上傳的 Log」可以看。 |
