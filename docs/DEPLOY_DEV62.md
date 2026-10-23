# iStudy 1.0.0-dev.62 部署與驗收（dev.61 → dev.62）

日期：2026-10-22。範圍：Ubuntu Server、Server App、API、Agent、備份；依你 10/22 的指示，Client 也一起修改（版本 1.0.0-dev.62）。
正式轉檔編碼器（VA-API）、原檔 30 天刪除規則、Nginx 設定、字幕引擎與模型都不變。

> 未經你確認，我不會在正式 Ubuntu Server 上執行 apply、migration、服務重啟或任何修改。以下指令都由你自己執行。

## 變更內容

| 鍵 | 內容 |
|---|---|
| P0-A 永久刪除一致性 | **預檢**：改名前確認每個檔案所在資料夾可寫入（含 sticky bit 規則），不夠就停止、什麼都不動，並說明是哪個資料夾、少了什麼權限。預檢只是第一道，之後每一步仍以實際結果為準。<br>**狀態驗證**：任何失敗都先嘗試改回，再逐一確認每個檔案實際在哪裡（原位置／暫存名稱／兩處都有／都不見）。全部確認回到原位置才顯示「沒有刪除任何東西」；否則顯示「⚠ 沒有完成復原」與每個檔案的實際位置，不再誤報「已復原」。<br>**資料庫提交之後**：暫存檔刪不掉不算失敗，標示「待清理」；Agent 重新啟動會接著處理（資料庫紀錄還在→改回原名；紀錄已刪→刪除暫存檔），也處理課程資料夾裡的暫存檔。<br>**只刪屬於這個教材的衍生檔**：播放 MP4、字幕、文件 PDF 必須是資料庫記錄屬於這個教材的，同 ID 的舊檔案不處理。<br>**畫面**：逐項列出每個檔案的結果；失敗後按「再試一次（接著處理）」。不使用 chmod 777 |
| P0-B 直接播放 MP4 的字幕 | **原因**：直接播放（不轉檔）的 MP4 會排入一個「等系統時段」的字幕工作；Super 按「立即執行」時只回「已在佇列中」，沒有把它改成馬上執行，白天就一直不動。「轉檔佇列 → 完成」也沒有字幕按鈕。<br>**修正**：「立即執行」會把排隊中的那個字幕工作改成馬上執行（不建立第二個）；正在辨識時顯示進度；失敗的可以重試（建立新工作）。<br>「轉檔佇列 → 完成」加「字幕」欄：有字幕／辨識中 N%／已排入馬上執行／等系統時段＋「立即產生」／失敗＋「重試字幕」／尚無字幕＋「產生字幕」。不強制轉檔，`convert_status` 仍是 direct；Client 不需要修改 |
| 教材目錄樹狀顯示 | `/Media（教材根目錄）` 標頭；每個第一層資料夾是有框的區塊，展開後結尾有「— 名稱 結束 —」；子層縮排＋引導線；直接放在 /Media 的檔案獨立成「根目錄檔案」區塊；窄視窗（1024 寬）標籤換行不重疊。展開、收合、掃描照舊。不改資料庫、不改目錄結構、不加套件 |
| 方案 B 加密備份 | **內容**：pg_dump（結構、資料、序列）、`/Media/subtitles/*.vtt`、`.env`、compose.yaml、VERSION.json、安裝識別碼、Manifest（版本、Migration、筆數、每個檔案的 SHA-256）、RESTORE.md。<br>**不含**：教材原檔、`/Media/playback/*.mp4`、Outgo、快取、模型、Client 安裝檔、UEM 報告（畫面明確告知）。<br>**加密**：age（不自製演算法）→ `istudy-backup-<時間>.tar.gz.age`＋`.sha256`；壓縮內容直接串流給 age，本機只有加密版本。<br>**金鑰**：Agent 只有公鑰（資料庫 `server_config.backup_key`）；私鑰 `/etc/istudy/backup/identity.key`（root、0600，資料夾 0700），不掛進任何容器。恢復金鑰檔＝私鑰以恢復密碼加密（age -p），存在 `/etc/istudy/backup/recovery-key.age`、備份資料夾與遠端。<br>**恢復密碼**：與 Super 登入密碼分開；至少 12 字；修改只換密碼、私鑰不變，舊備份照樣能還原。密碼只經 Server App → pkexec → istudyctl 的標準輸入，再經虛擬終端機交給 age，不寫入指令列、環境變數、資料庫、Log、Manifest 或任何明文檔案 |
| 主機的備份工作 | 解密驗證、隔離還原測試、遠端上傳、保留清理、遠端連線測試由主機 `istudyctl`（root）執行。主機沒有安裝 age：用 Agent 映像跑一次性容器（`--network none`、唯讀、`--log-driver none`），私鑰經標準輸入傳入。<br>**排程**：`istudy-backup.timer` 每小時——新備份解密驗證；到期時上傳；每天清理一次。<br>**序列化**：Agent 建立備份與主機的所有備份工作共用一把檔案鎖，同一時間只做一件。<br>**紀錄**：主機工作寫進 jobs（新工作類型 backup_verify／upload／prune／remote_test，只記錄，Agent 不執行）與系統 Log（主機維護） |
| InterServer 遠端 | **協定**：FTPS（明示 TLS、AUTH TLS），伺服器憑證一定驗證；不支援、也不會改用明文 FTP。主機、連接埠、帳號、資料夾都由你輸入，程式不猜測。<br>**密碼**：只存在 `/etc/istudy/backup/remote.json`（root 0600），不寫資料庫、Log；主機或帳號改了一定要重新輸入密碼。<br>**資料夾**：任何一層叫 public_html、www、htdocs、html、public 等就拒絕。<br>**上傳**：每 3 天一份「解密驗證＋隔離還原測試都通過」的備份；先以暫存名稱上傳再改名；下載回來比對 SHA-256 才標示「遠端已驗證」；同時上傳 `.sha256`、恢復金鑰檔、RESTORE.md（不含秘密）。失敗時本機檔案完全不動，可按「立即上傳」重試 |
| 保留與清理 | 預設 15 天，可設 10～15 天。只刪超過天數的加密備份；**最後一份可用（已通過解密驗證）的備份永遠保留**，不論多舊、有沒有遠端副本。遠端也保留最新一份已驗證的副本。磁碟剩餘不足（至少 1 GB 且不少於上一份的 2 倍）時停止新備份並記錄錯誤，不刪任何備份。每次清理都寫進系統 Log |
| 舊備份相容 | 舊的 `.tar.gz` 標示「⚠ 未加密（舊格式）」，不自動上傳、不自動刪除，原本的「檢查完整性」「隔離還原測試」照舊。`istudy-db-*.sql` 完全不動。dev.61 回退後，它的保留份數清理只比對 `istudy-backup-*.tar.gz`，不會碰 `.age` |
| WOL | **Server**：設備的新增、修改、刪除、登記、LAN 掃描只有 Super；管理員呼叫會被拒（「WOL 設備只有 Super 能…」）。<br>**登入畫面「喚醒電腦」**：仍然只能喚醒；清單只列「已登記並啟用」的電腦；有已登記但未啟用的會寫出台數與原因（之前看起來像「沒有任何電腦」）。<br>**WOL 管理**：管理員只看「喚醒與狀態」；Super 可在該頁直接按「啟用」。新增、登記時預設「✓ 啟用」（掃描 LAN 一次加入多台仍預設停用） |
| 登入頁「顯示密碼」 | Server App 與 Client：密碼欄右側小按鈕「顯示／隱藏」（眼睛圖示＋文字），按一下常態顯示明碼、再按隱藏；登入後恢復隱藏 |
| Client：按 X | 先儲存播放位置、向 Server 登出（撤銷 Session）、清除本機登入、回到登入畫面，再縮到系統匣（心跳照常）。Server 連不上時也清除本機登入。Windows 會跳出「iStudy 已登出」提示。要完全結束：系統匣圖示右鍵 →「結束 iStudy」 |
| Client：播放 | **預先緩衝**：進入播放頁立即建立影片並開始緩衝（`preload=auto`），教材資料與字幕同時讀取（以前讀完資料才讀字幕，最後才開始讀影片）。<br>**下拉選單**：倍速、學習狀態在預覽頁與全螢幕都明確配色（全螢幕以前是白字白底），展開的選項也一樣；倍速 1 倍原本顯示成「1.x」，改成 1x。<br>**全螢幕的 Tag 視窗**：以前繼承全螢幕的白字，淺色底上看不到；改成視窗一律一般配色 |

**資料庫（migration 0011）**：
- `backup_history` 加欄位：format（預設 legacy）、sha256、enc_status（預設 none）、remote_status（預設 none）、remote_path、remote_verified_at、remote_error、deleted_at、delete_reason、manifest。既有紀錄自動是「舊格式、未加密」。
- jobs 多允許 4 種主機備份工作類型。
- 不改任何既有資料。回退 dev.61 時保留，dev.61 不使用、不受影響。

**Agent 映像**：以 dev.61 映像為基底，加入 age v1.2.1（官方 GitHub release 的 linux-amd64 靜態執行檔，隨原始碼附在 `deploy/vendor/`，SHA-256 `7df45a6c…79d50`，建置時核對）。建映像不需要網路，主機不安裝任何套件。

## 1. 部署

先準備 dev.61 的 Server App 安裝檔（dev.61 原始碼包 `dist/istudy-server-app_1.0.0-dev.61_amd64.deb`），回退要用。

```bash
unzip istudy_1.0.0-dev.62-src.zip -d ~/istudy-dev62 && cd ~/istudy-dev62/istudy
OLD=~/istudy-dev61/istudy/dist/istudy-server-app_1.0.0-dev.61_amd64.deb   # 改成實際位置
sudo ./deploy/patch_dev62.sh check --old-deb "$OLD"                  # 只檢查，不改任何東西
sudo ./deploy/patch_dev62.sh apply --old-deb "$OLD" --restore-test   # check 全部 ✓ 後再執行，約 5～7 分鐘
```

**`check` 會停止的情況**：目前版本不是 dev.61；Super 不是剛好 1 位；dev.61 映像缺元件；沒有 dev.61 的 Server App 安裝檔；age 壓縮檔的 SHA-256 不符；備份資料夾剩餘不足 1.5 GB。

**`check` 會列出**：舊的未加密備份與 istudy-db-*.sql 各有幾份（不動）、已登記但未啟用喚醒的電腦台數、教材數、編碼器設定。

**`apply` 會做的**：
1. 自我檢查（基準）。
2. 備份資料庫（istudy-db-*.sql）；加 `--restore-test` 時另外做隔離還原測試。
3. 保存 dev.61 的設定（`/var/lib/istudy/patch-dev62`）。
4. 以 dev.61 映像為基底建 dev.62 映像（加 age；`--network none`）。
5. `.env` 只改 `ISTUDY_VERSION`，更新 istudyctl，寫入並啟用 `istudy-backup.timer`。
6. 重啟服務，套用 migration 0011，確認 Super，安裝 Server App。
7. 再做一次自我檢查。尚未設定恢復密碼時「加密備份（方案 B）」一定是 ⚠，結果會是 WARN。

任何一步失敗都會自動退回 dev.61。dev.61（與更早）的映像、回退檔都保留，腳本不會執行任何 prune 或 rmi。

## 2. 回退

```bash
sudo ./deploy/patch_dev62.sh rollback
```

- `.env` 版本、VERSION.json、istudyctl 換回 dev.61，用 dev.61 映像重啟，裝回 dev.61 的 Server App。
- 停用並移除 `istudy-backup.timer`（dev.61 的 istudyctl 沒有這個功能）。
- **不刪除**：加密備份（.age）、`/etc/istudy/backup` 的金鑰、舊備份。dev.61 的 Agent 會恢復建立未加密的 .tar.gz。要解密 .age：備份資料夾的 RESTORE.md，或重新更新到 dev.62（`istudy/agent:1.0.0-dev.62` 映像保留）。
- 有備份工作正在執行（建立、驗證、上傳、清理）時停止、不做任何修改。
- migration 0011 保留。已更新到 dev.62 的 Client 不需要退回（與 dev.61 Server 相容）。

## 3. 部署後（依序）

1. **關閉並重新開啟 Server App**，右下角應顯示 `Server V1.0.0-dev.62`。
2. **設定恢復密碼**：Server App「備份」→「設定恢復密碼」。至少 12 字，不要和 Super 登入密碼相同，**寫下來另外保管**。設定前不會產生新的每日備份（頁面有 ✗ 警告）。
   - 私鑰與恢復密碼都遺失時，所有加密備份都無法解密，任何人都救不回。
3. **確認金鑰權限**（只讀）：

   ```bash
   sudo ls -la /etc/istudy/backup     # 預期：資料夾 drwx------ root；identity.key、recovery-key.age 都是 -rw------- root
   systemctl list-timers istudy-backup.timer
   ```

4. **InterServer**：先在 InterServer 控制台確認 FTP 帳號支援 FTPS（明示 TLS），記下主機、連接埠、帳號，在主目錄建立網站公開目錄以外的資料夾（例如 `/istudy-backup`）。然後「備份」→「設定遠端」→「測試遠端連線」。
5. **Client**：見第 6 節。
6. **WOL**：Super 在「WOL 管理 → 喚醒與狀態」看哪些電腦顯示「✗ 未啟用（登入畫面不顯示）」，要出現在登入畫面的按「啟用」。

## 4. 實機驗收（請填 PASS／FAIL／NOT_TESTED；只用測試檔案）

### 永久刪除（只用測試檔案，不要拿正式教材）

| 鍵 | 操作 | 預期 |
|---|---|---|
| DEL-01 | 在一個新的測試課程資料夾放 TXT，掃描，永久刪除 | 「已永久刪除」；檔案與紀錄都不見；逐項結果都是「✓ 已刪除」 |
| DEL-02 | 把測試資料夾權限改成 Agent 不能寫（例如 `chmod 755` 且擁有者不是 10001、沒有 ACL），永久刪除 | 停止，說明哪個資料夾缺寫入權限；「沒有刪除任何東西」；不出現「已復原」誤報 |
| DEL-03 | 已有字幕的測試影片永久刪除 | 原檔、播放 MP4、字幕都刪；逐項列出 |
| DEL-04 | （開發環境模擬：改名或復原失敗）實機不需要刻意製造 | 開發環境 PASS；實機 NOT_TESTED 可接受 |
| DEL-05 | （開發環境模擬：資料庫提交後刪不掉暫存檔） | 開發環境 PASS |
| DEL-06 | 永久刪除進行中重啟 Agent（`sudo docker restart istudy-agent-1`） | 重啟後自動接著處理，沒有殘留的 `.istudy-purge-*` 暫存檔 |
| DEL-07 | `/Media/playback/` 有一個同 ID、但不屬於這個教材的舊檔 | 對話框顯示「同 ID 的舊檔（不屬於這個教材）：不處理」，那個檔案不動 |
| DEL-08 | 刪除後按「掃描」 | 不會復活 |
| DEL-RC | **10/20 那次「已復原」誤報的根因**（只讀）：見第 5 節的指令 | 把輸出貼給我 |

### ASR

| 鍵 | 操作 | 預期 |
|---|---|---|
| ASR-01 | `PurgeTest/1126投票宣導篇_國語.mp4`（直接播放）→ 轉檔佇列「完成」的「字幕」欄 →「立即產生」 | 改成「⏳ 已排入，馬上執行」→「… 辨識中 N%」→「✓ 有字幕」；Client 播放有字幕 |
| ASR-02 | 一支需要轉檔的測試影片，轉檔完成後 | 同上 |
| ASR-03 | 已有字幕的影片 | 字幕欄「✓ 有字幕」，沒有多出工作 |
| ASR-04 | 字幕失敗的影片 →「重試字幕」 | 新工作，成功後有字幕 |
| ASR-05 | 同一支連按兩次「立即產生」 | 只有一個字幕工作 |
| ASR-06 | 「轉檔佇列」「字幕」頁的狀態文字 | 與實際工作狀態一致 |
| ASR-07 | Client 播放有字幕的影片 | 正常（字幕協定沒有改） |

### 教材目錄

| 鍵 | 操作 | 預期 |
|---|---|---|
| UI-01 | 「教材目錄」展開幾個課程 | `/Media（教材根目錄）` 標頭；每個課程是有框的區塊、結尾「— 名稱 結束 —」；子層縮排有引導線；根目錄檔案獨立一區 |
| UI-02 | 把 Server App 視窗縮到最小寬度 | 標籤換行，不和檔名重疊 |
| UI-03 | 展開、收合、「掃描」 | 與 dev.61 相同 |

### 備份

| 鍵 | 操作 | 預期 |
|---|---|---|
| BAK-01 | 設定恢復密碼後按「建立備份」；用第 3 節的 `sudo istudyctl restore --file … --target test` 或解密驗證 | 只有 `.tar.gz.age`＋`.sha256`；內容只有 db.sql、字幕、設定、Manifest、RESTORE.md，沒有影片、文件、Outgo |
| BAK-02 | 在 `/srv/istudy/backup` 執行 `sha256sum -c istudy-backup-*.tar.gz.age.sha256`；備份頁按「解密驗證」 | OK；「✓ 解密驗證通過」 |
| BAK-03 | 「設定恢復密碼」→「測試恢復密碼」（輸入錯的一次、對的一次） | 錯的顯示「恢復密碼錯誤」；對的顯示「✓ 恢復密碼正確」 |
| BAK-04 | 「修改恢復密碼」→ 對**修改前**的那份備份按「解密＋還原測試」；「測試恢復密碼」用舊密碼 | 還原測試通過；舊密碼「恢復密碼錯誤」、新密碼正確 |
| BAK-05 | 設定遠端 → 測試連線 → 「立即上傳最新備份」 | 「✓ 遠端已驗證」；InterServer 檔案管理員看到 .age、.sha256、istudy-recovery-key.age、RESTORE.md，沒有 .part |
| BAK-06 | 隔天、第 3 天看備份清單的「遠端」欄 | 大約每 3 天一份「✓ 遠端已驗證」 |
| BAK-07 | 保留天數改成 10 天，再改回 | 只能選 10～15 天；使用記錄有 backup.settings_changed |
| BAK-08 | 「立即清理」 | 「Log → 主機維護」有清理紀錄；最後一份可用備份保留；舊 .tar.gz、istudy-db-*.sql 不動 |
| BAK-09 | 遠端設定故意填錯連接埠 → 立即上傳；改回正確 → 再上傳 | 失敗時本機檔案不動、頁面 ⚠ 警告；改回後成功 |
| BAK-10 | 「解密＋還原測試」 | 「✓ 筆數相符」，目標是 istudy_restore_test；正式資料庫不受影響 |
| BAK-11 | 備份頁整體 | 方案 B 範圍說明、警告、金鑰、遠端、保留天數、清單（格式、解密驗證、還原測試、遠端）都正確 |
| BAK-12 | `sudo docker exec istudy-agent-1 ls /etc/istudy 2>&1`；`sudo docker inspect istudy-agent-1 --format '{{json .Mounts}}'` | 容器裡沒有 /etc/istudy；掛載清單沒有 /etc/istudy |
| BAK-13 | 舊的 .tar.gz 那一列 | 顯示「⚠ 未加密（舊格式）」，「檢查完整性」「隔離還原測試」可用，不會上傳 |

### 部署

| 鍵 | 操作 | 預期 |
|---|---|---|
| DEP-01 | `patch_dev62.sh check` | 全部 ✓，列出影響範圍 |
| DEP-02 | `apply` | migration 0011 套用；教材數、帳號數不變 |
| DEP-03 | `sudo istudyctl doctor` | 設定恢復密碼前「加密備份」⚠，其他與 dev.61 相同；設定並有備份後 ✓ |
| DEP-04 | （可選，在你確認的時間）`rollback` 再 `apply` | 回退後 dev.61 正常；再更新回 dev.62 正常 |
| DEP-05 | 掃描、播放、轉檔、文件轉換、原檔刪除確認、Outgo、統計與報告、Email 測試信 | 與 dev.61 相同 |
| DEP-06 | Client | 依你的指示 dev.62 有修改 Client：版本 1.0.0-dev.62、修改項目見第 6 節 |

### Client 與 WOL

| 鍵 | 操作 | 預期 |
|---|---|---|
| CLI-X | 在 Client 登入後按視窗右上角 X，再從系統匣開啟 | 開啟時是登入畫面；Server App「使用記錄」有一筆登出；Windows 跳出「iStudy 已登出」 |
| CLI-EYE | Client 與 Server App 登入頁，按密碼欄右側「顯示」 | 明碼顯示；再按「隱藏」恢復；登入後再開登入頁是隱藏 |
| CLI-PLAY | 從教材清單點一支長影片，立刻按播放 | 開頭不卡（或明顯比 dev.61 少卡） |
| CLI-SEL | 預覽頁與全螢幕：展開「速度」與「學習狀態」 | 選單與選項文字都看得清楚；速度選項是 0.5x、0.75x、1x、1.25x…（沒有「1.x」）；全螢幕按「🏷 Tag」，視窗裡的標題、說明、Tag、按鈕文字都看得清楚 |
| WOL-LIST | 登入頁「喚醒電腦」 | 列出已啟用的電腦；有未啟用的會寫「另有 N 台已登記但未啟用喚醒」；只有喚醒按鈕 |
| WOL-ADMIN | 管理員帳號登入 Client →「WOL 管理」 | 只有「喚醒與狀態」，沒有新增、編輯、刪除、登記、掃描 |

## 5. 永久刪除「已復原」誤報的根因（只讀，請把輸出貼給我）

```bash
ls -la /Media/PurgeTest/
stat -c '%A %U:%G %n' /Media /Media/PurgeTest /Media/PurgeTest/*
getfacl -p /Media/PurgeTest
sudo -u postgres psql -d istudy -c "SELECT id, rel_path, kind, status FROM media WHERE rel_path LIKE 'PurgeTest/%'"
sudo -u postgres psql -d istudy -c "SELECT id, status, attempts, error, result->'plan' FROM jobs WHERE type='purge_media' ORDER BY id DESC LIMIT 3"
```

程式缺陷已確認並修正（第 1 節 P0-A）：dev.61 失敗時沒有確認檔案實際位置就顯示「已復原」；衍生檔只看 ID；課程資料夾的暫存檔不會被處理。
`delete_test.txt` 仍顯示「✓ 已登錄」，最可能是檔案停在隱藏的 `.istudy-purge-*` 暫存名稱。確切觸發原因（權限、sticky bit 或其他）要看上面的輸出才能確定；dev.62 不假設是 sticky bit。
dev.62 部署後 Agent 啟動時會自動處理：資料庫紀錄還在的改回原名。

## 6. Client（dev.62）：建置與發布

修改項目：按 X 登出、登入頁顯示密碼、登入頁喚醒清單說明、WOL 管理對管理員只顯示喚醒與狀態、播放預先緩衝、播放器下拉選單配色。

這個開發環境做不出 Client 安裝檔：Windows 安裝檔要在 Windows 10／11 上打包；Ubuntu .deb 需要下載打包工具。

```bash
# 在 Windows 10／11 上（原始碼包解壓後的 istudy 資料夾）
npm install
node scripts/package_client.mjs --all      # dist/clients/<版別>/：.exe＋latest.yml
```

**發布**：Server App →「Client 版本與更新」→「發布新版 Client…」→ 選 `dist/clients`（或 `sudo istudyctl clients-publish --from <dist/clients>`）。已裝 dev.61 的電腦會自動更新：沒有人登入滿 2 分鐘就安裝並重新開啟。

## 7. 已知限制與風險

- **原檔清理與方案 B（風險報告，這版不擴大範圍）**：「原檔刪除確認」刪掉原始影片後，`/Media/playback/<ID>.mp4` 就是那支影片唯一的一份。方案 B 不備份它，所以 ZFS 鏡像是唯一的保護；ZFS 池損毀或誤刪時，這些影片無法用方案 B 恢復。建議之後評估：原檔刪除前確認另有離線備份，或把播放檔納入另一套檔案備份。
- **InterServer 實際連線 NOT_TESTED**：沒有帳號與網路。協定選 FTPS（明示 TLS），是依常見的 cPanel 共享主機能力實作；InterServer 若只提供 SFTP 或只有隱含式 FTPS（port 990），這版連不上，請先在控制台確認後告訴我。
- **設定恢復密碼之前沒有新備份**：本機只保存加密版本，所以部署到設定之間不會產生每日備份（頁面與 Doctor 都會警告）。apply 時產生的 istudy-db-*.sql 仍在。
- **Server App 的 pkexec 實機路徑 NOT_TESTED**：開發環境以同一支 istudyctl（測試模式）經標準輸入驗證；實機的 polkit 視窗與 pkexec 傳遞標準輸入要在 Server 上確認（bootstrap 的 Super 密碼也是同一做法）。
- **真正的 docker build NOT_TESTED**：開發環境沒有 dev.61 的 Agent 基底映像；增量 Dockerfile 的 age 安裝指令已在主機上照樣執行驗證，建置內容由流程測試確認。
- **age 執行檔來源**：官方 GitHub release `age-v1.2.1-linux-amd64.tar.gz`（大小與官方頁面相符，4.95 MB）。官方沒有公布 SHA-256，這裡記錄的是下載檔的雜湊；你可以在有網路的電腦自行下載比對。
- **按 X 的行為**：會登出並縮到系統匣；App 仍在背景執行（心跳讓 WOL 知道電腦開著）。要完全結束要用系統匣「結束 iStudy」。
- **實機全部 NOT_TESTED**：本頁所有實機項目都要在 Server 與教室電腦上驗收。
