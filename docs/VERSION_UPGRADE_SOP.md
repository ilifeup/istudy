# iSTUDY｜跨版本升級、全盤盤點、備份與回滾標準作業程序（SOP）

**版次**：1.0｜**建立**：2026-11-04｜**適用**：Dev61→Dev63 及往後每次 Dev／正式版本升級；Ubuntu Server App、PHP／Nginx／Agent、主機 PostgreSQL、Windows／Ubuntu Client。

> **最高原則**：先證明現況正常，再證明可還原，最後才允許升級。**備份成功 ≠ 還原成功；UEM Quick PASS ≠ Doctor Full PASS；CI PASS ≠ Ubuntu／Client 實機驗收 PASS。** 未達關卡一律 STOP，不得為趕進度跳過。所有指令以「現場安裝版 istudyctl --help／程式實作」核對，禁止把新版本 CLI 參數直接套在舊版主機。

## 0. 授權、安全與部署模式

- GitHub 僅同步 SOP、進度、證據摘要；**不因更新文件而取得開 PR、部署、修改資料庫或刪除資料的權限**。未經明確同意不得開 PR、合併、修改正式資料。
- 每次升級先記錄「現行版本／目標版本、來源快照、升級範圍、停機窗口、資料保留政策、Client 升級順序、回滾目標、操作人與核准」。即使全是測試資料，也不能省略回滾。
- **部署模式須由使用者逐次決定**：① 隔離環境；② **既有服務原地升級**（本次 Dev61→Dev63 已指定此模式）。原地升級不另開第二套常駐服務，但**仍須離線／暫時性隔離還原演練**，避免測試復原破壞現行 DB。
- 原地升級保持原有 LAN 4080、localhost 4081、localhost PostgreSQL 5432 安全邊界；不得擅自開放管理 API、覆寫 /Media、執行 docker volume prune／system prune、刪除舊映像或舊 .deb。
- **Windows 10 Client Dev61 與 Server Dev63 的 API／更新協定相容性必須先確認**；不相容時，需備妥新版 Client 安裝檔、Windows 回退安裝檔及順序，不能只升 Server 後假定舊 Client 正常。
- 開發原始碼 ZIP 遵守 [C 混合打包規範](ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)：原始碼與 .deb／Client installer 分開；產物附 SHA-256、版本與來源識別。

## 1. 關卡 G0｜確認現行基線及升級來源（只讀）

1. 確認目前所在主機／路徑，**不可誤在 Shared Modules Flutter 工作樹執行 iSTUDY 指令**；核對 /opt/istudy、/srv/istudy、/var/lib/istudy、/Media。
2. 讀取 /opt/istudy/VERSION.json、127.0.0.1:4081/api/health、Docker agent/php 映像 tag、dpkg Server App 版本；五者應一致，否則 STOP 調查。
3. 核對 Ubuntu、Node/npm、PHP CLI 與 Docker PHP 版本（兩者不一定相同）、PostgreSQL 主機版本／pg_dump 版本、Docker Compose、磁碟剩餘空間、ZFS、systemd、連接埠與執行中容器。
4. 來源檢查：候選 ZIP SHA-256、檔案清單、版本、來源 Commit／Manifest、必要原始碼與 migration、與現行版本差異；未取得 Dev63 原始碼不得開始 apply。
5. 升級前確認沒有執行中的轉檔／字幕／USB 匯入／備份／排程／Client 更新等不可中斷工作，並規劃停機或排空；不得直接強殺。
6. 盤點輸出需有時間、版本、主機、執行指令、結果、報告 SHA-256、未解決問題及操作人；公開 GitHub 僅放**去識別化摘要**，不放 .env、密碼、私鑰、完整機敏日誌。

**可用的只讀預檢**（不自動套用任何變更）：

```bash
date -Is
cat /opt/istudy/VERSION.json
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker compose -f /opt/istudy/compose.yaml config --quiet
pg_lsclusters
df -h / /Media
sudo istudyctl status
```

> compose config 在部分版本可能讀取 .env；只使用 --quiet，不將展開後的含密碼設定上傳。先檢查命令存在與權限。

## 2. 關卡 G1｜優先使用 iSTUDY 既有 UEM／Doctor，而非自製通用腳本

**順序：Quick → 解讀報告 → Doctor／Full → 補充缺口。** 現有工具以 [agent/istudyctl.py](../agent/istudyctl.py) 與 [agent/istudy_common/uem.py](../agent/istudy_common/uem.py) 為準。

```bash
command -v istudyctl
sudo istudyctl uem --mode quick
sudo ls -lh /var/lib/istudy/uem/
# 檢視 Quick 後，再執行完整盤點；Full 可能包含試轉檔／文件轉換
sudo istudyctl uem --mode full
sudo istudyctl doctor
sudo istudyctl hostcheck
sudo istudyctl check-db
```

- **Quick**：磁碟實體歸屬（SSD/HDD）、ZFS mirror、重要目錄、容器掛載與唯讀／讀寫、systemd、PostgreSQL、Docker、端口、版本一致性。
- **Full**：額外執行自我檢查；應於可接受測試負載／已確認功能時段進行，記錄實際測試與跳過項目。
- 報告位置：`/var/lib/istudy/uem/latest.md`、`latest.json`、`latest.html`、`history/`。升級前複製並加時間戳、SHA-256，避免被下一次盤點覆寫。
- **Doctor 結果有自己的檢查時間**：Quick 報告引用的 Doctor 可能是上次 Full 的舊資料。不可將「29 PASS」誤寫成「Doctor 當下 29 PASS」。對每個 FAIL/WARN/UNKNOWN/STALE 分別建處理紀錄，重跑後再核定。
- UEM 只盤點**已知項目**，不等於已確認資料庫可還原、安裝檔存在、Client 相容、Migration 可逆或所有安全控制。
- 若現場舊版沒有某個子命令，記錄「不支援」，改採對應只讀檢查；**不可先安裝新 istudyctl 再回頭盤點舊版**，以免改變基線。

### 2.1 2026-11-04 Dev61 實際基線範例（使用者上傳 UEM Quick）

| 類別 | 實測紀錄 |
|---|---|
| 報告時間／版本 | 2026-11-04 12:26:56；1.0.0-dev.61 |
| Quick 摘要 | PASS 29、WARN 0、FAIL 0、MISSING 0、UNKNOWN 0 |
| 主機服務 | PostgreSQL 18/main、Docker、istudy.service、hostcheck.timer、SSH 正常 |
| Docker | istudy/agent:1.0.0-dev.61、istudy/php:1.0.0-dev.61、nginx:1.28.0-alpine |
| 版本一致性 | VERSION.json、API health、Agent、PHP、Server App .deb 均 dev.61 |
| 網路 | 4080 LAN；4081 僅 127.0.0.1；5432 僅 127.0.0.1 |
| 磁碟 | OS／PostgreSQL／Docker／/srv/istudy/backup 在同一 NVMe SSD；/Media 為雙 HDD ZFS mirror ONLINE |
| UEM 中 Doctor 區塊 | **舊資料**：2026-11-02 15:49:42；曾提示 /Media/PurgeTest sticky bit 權限警告，須重跑驗證 |
| 風險 | 備份與 PostgreSQL 同 SSD，**不具磁碟故障隔離**；升級前至少有一份可離線取得的備份副本／可驗證恢復方式 |
| 結論 | **G1 Quick 通過，Full／最新 Doctor／可還原性尚未驗證；不得直接升級** |

本範例為一次歷史快照，不代表後續版本或當前主機仍相同；**不要將完整 UEM 原始報告、主機識別或其他敏感資訊上傳公開 repo**。

## 3. 關卡 G2｜DevN → DevN+X 差異與相容性審查

必須建立明確的差異表，涵蓋：

1. **程式**：Server App（Electron）、PHP API、Agent、Nginx、CLI、systemd、Dockerfile、Compose、設定檔、版本字串與安裝包。
2. **資料庫**：每一支 migration 的前置版本、交易、鎖表、資料搬遷、預估耗時、破壞性欄位變更、向前／向後相容性；跨兩版升級不能假設可跳過中間 migration。
3. **資料**：/Media 教材原檔、playback、subtitles、doc-cache、索引、書籤、學習歷程、工作佇列、排程、金鑰、備份。
4. **權限**：Super／Admin／User、登入前 WOL 與登入後 WOL、Page Atlas API/DB 映射、USB Agent 最小權限、管理 API localhost。
5. **Client**：Win10 Dev61 連 Dev63 Server 的登入、更新 manifest、下載、播放、PDF、書籤、離線及回滾相容性；不相容須先備 Client 更新與回退。
6. **產物**：目標 .deb／映像／Client installer **須從同一已核對來源建置**，核對 SHA-256；不得拿 Dev62 .deb 假冒 Dev63。
7. **測試**：Node test、TypeScript typecheck、renderer build、PHP lint、Agent Python test、PostgreSQL 真實 HTTP E2E、UI、USB 實機、備份／還原、Windows Client；區分已跑／未跑／失敗。

**G2 放行條件**：升級計畫、版本路徑、必要 migration、Client 協定與回滾策略皆有具體證據；不明確就 STOP。

## 4. 關卡 G3｜建立「可用的舊版回滾點」

**不得只備份資料庫、也不得只保存 .deb。必須保留能重建完整 DevN 的一致快照。**

- **程式與執行環境**：舊版 Server App .deb（實體檔案，非只記套件版本）、/opt/istudy 程式與 Compose、Docker **舊映像 ID／可離線重載的 image archive**、Agent、Nginx、istudyctl、systemd unit/timer、polkit、必要模型／版本與設定。映像只靠 tag 可能被覆蓋，須固定 digest／image ID。
- **資料庫**：PostgreSQL 18 主機上的 iSTUDY DB 完整一致性備份（結構、資料、序列、migration 紀錄、必要 roles／extensions／ownership），使用與現場相容的 pg_dump；有需要時備份全域角色及權限。**備份後不代表已能還原**。
- **設定與密鑰**：.env、金鑰／備份恢復資訊、Compose、反向代理、systemd、Nginx、必要憑證，採權限 0600、加密或安全離線保存，**不公開上傳 GitHub／聊天**。
- **媒體與檔案**：明確列出 DB 與 /Media 的一致性界線。只備份 DB 但 Dev63 修改／刪除 /Media，會導致回滾不一致。升級前停止破壞性 Agent 工作；若有媒體改寫，須另建可回復快照／備份並驗證可用空間。**ZFS mirror 是硬碟備援，不是備份**。
- **備份副本**：由於現場 DB 與 /srv/istudy/backup 同 SSD，至少再保留一份不依賴該 SSD 的安全副本；確認磁碟容量、存取權與復原金鑰。
- **備份 Manifest**：版本、時間、來源、DB 名稱、migration、檔案清單、SHA-256、加密狀態、位置、操作人、備份／還原結果。SHA-256 僅能證明位元組一致，**不能證明 DB 可還原**。
- **回滾腳本**：先檢查現有 [Dev62 patch 腳本與規格](DEPLOY_DEV62.md) 的 check/apply/rollback 實作，再開發**針對實際 Dev61→Dev63 的專用版本**；舊 `patch_dev62.sh` 不得直接套用 Dev63。
- **備份完成後須驗證**：檔案存在、權限正確、hash 通過、加密解密可行、舊版 .deb／Docker image 可重裝、PostgreSQL 可以隔離還原並驗證表／筆數／索引／序列／API；失敗即 STOP。

## 5. 關卡 G4｜回滾預演與「可以退回」的證據

1. 使用**隔離、臨時的** PostgreSQL 還原測試目標，不連現行 iSTUDY DB；確認資料表、migration 版本、抽樣筆數、關聯、API／應用讀取正常。
2. 檢查舊版 .deb 可安裝、舊映像可還原、舊 Compose／systemd／istudyctl 可恢復、密鑰可用；盡可能在臨時環境演練，不在現有執行中服務上測試破壞性復原。
3. 明確界定資料回復點（RPO）：**回滾 DB 到升級前時間點，升級後新增的測試資料將遺失**；回滾需先停止新版寫入與 Agent／排程，防止新舊資料混用。
4. **特別驗證破壞性 migration**：不能靠「down migration」推測能退回；必須用舊版 DB 快照配舊版程式一起復原。
5. 測試成功記錄時間、命令版本、hash、還原目標、驗證結果；若無法演練，狀態只能「回滾未證實」，**不得宣稱保證能降回舊版**。

## 6. 關卡 G5｜原地升級執行（須另行取得本次 apply 核准）

**在 G0～G4 通過後**，才由操作人於約定停機窗口執行，且每步失敗立刻停止，不可帶病繼續：

1. 凍結舊版工作／新寫入、等待佇列排空、記錄最後健康檢查與備份時間。
2. 停止／暫停受影響服務及排程；**只動 iSTUDY 指定單元**，不可停止無關 Docker／PostgreSQL 工作。
3. 再次確認 DB／媒體快照一致，保留原版安裝檔、映像、設定。
4. 使用經審查的**目標版本專用**升級腳本與同版 artifacts；先 `check`、確認差異及備份，再 `apply`。未經審查不得直接執行舊版 patch。
5. migration 逐步記錄開始／完成／失敗；可重跑性與失敗清理明確；不要對正式／測試主庫執行未核准的破壞性 SQL。
6. 按依賴順序啟動 PostgreSQL → PHP／Agent／Nginx → Server App，確認 4080／4081／5432 安全邊界與 API。
7. Client 採已核准順序升級；若保留 Win10 舊版 Client，必須有實測相容證據。
8. 記錄所有命令、時間、返回碼、映像 ID、版本、migration、hash、服務狀態，保留原始日誌於受保護本機。

**不得因使用者說「可直接升級」就跳過 G0～G4；「可原地升級」不等於「可不備份」。**

## 7. 關卡 G6｜升級後完整驗收

| 面向 | 必驗 |
|---|---|
| UEM | 重新 Quick + Full；與升級前差異比對，Doctor 時間須為升級後新結果 |
| 版本 | VERSION.json、API、Agent、PHP、.deb、Client 版本／相容性 |
| 服務 | systemd、Docker health、Agent 心跳、PostgreSQL、4080 LAN／4081 localhost／5432 localhost |
| 資料 | migration、表數／筆數、教材索引、書籤、歷程、字幕、備份記錄 |
| 功能 | Server App 登入角色、Page Atlas、USB、PDF、字幕、轉檔、備份、WOL、Log 權限 |
| Client | Win10 真機登入、播放、下載、更新、書籤、PDF、必要回退測試 |
| 安全 | 401／403／200 權限矩陣、管理 API 不暴露、密鑰與資料權限、稽核 |
| 備份 | 新版可備份、驗證及隔離還原；舊版回滾點保留至核准清理 |

每項標記 PASS／FAIL／SKIP／UNKNOWN、執行環境、時間、證據位置；**SKIP 不算 PASS**。關鍵項失敗即啟動 G7 回滾評估。

## 8. 關卡 G7｜異常處理與回滾（先恢復可用，再查根因）

**觸發**：migration 中斷、API／登入失敗、權限繞過、資料不一致、Agent 破壞資料、Client 無法使用、回歸測試關鍵 FAIL 或逾停機窗口。

1. 停止 Dev63 寫入／Agent／排程，封存錯誤日誌與現況，不刪除調查證據。
2. 依預演計畫回復**舊版程式、舊 Docker 映像、舊 Server App .deb、設定、systemd 與 PostgreSQL DB 快照**；必要時恢復與 DB 同時間點的 /Media 資料。
3. 啟動 Dev61 並執行 UEM Quick／Full、Doctor、API、Client Dev61 實機驗收，與升級前基線比對。
4. 只有確認**版本、資料、服務與 Client 都正常**才可宣告「回滾完成」；否則標記「回滾失敗／部分復原」並保留現場。
5. 回滾後**不自動重新 apply**；先記錄根因、修正、再驗證、取得新核准。

## 9. 關卡 G8｜版本結案、GitHub 與持續改善

每次升級建立或更新一份 `docs/upgrade-records/YYYY-MM-DD_DevXX_to_DevYY.md`（公開版**只含去識別化摘要**），包含：

```text
日期／主機代號（非實際識別）：
舊版 → 新版；Server／Client 各自版本：
部署模式／授權範圍／停機窗口：
來源 ZIP／Commit／SHA-256／發行成品：
G0 現況／G1 Quick／Full／Doctor（附時間）：
G2 程式、DB migration、API、Client 差異：
G3 備份 Manifest、離線副本、金鑰可用性：
G4 隔離還原演練／舊版可重建驗證：
G5 執行結果／migration／服務：
G6 Ubuntu、Windows Client、功能、安全驗收：
G7 是否回滾／資料回復點／回滾後驗收：
未完成／已知風險／後續 Issue：
結案判斷／操作人／核准：
```

- 每次操作**先讀本 SOP、AGENTS.md、目前版本的部署文件與進度中心**；GitHub 文件更新不等於已部署。
- 不將私鑰、.env、完整 DB dump、帶敏感資料的原始 UEM 報告提交到公開 GitHub。
- 更新 SOP 必須說明原因與適用版本，避免新 SOP 靜默推翻既有安全限制。
- Dev63 專案進度追蹤：[DEV63_STATUS_AND_DECISIONS.md](DEV63_STATUS_AND_DECISIONS.md)。

## 10. 本次 Dev61→Dev63 待辦與放行狀態（2026-11-04）

- [x] 使用者指定**原地升級現有 Ubuntu Server App Dev61**，不另開常駐隔離服務；Win10 Client 目前 Dev61。
- [x] Ubuntu 26.04.1、Node 24.22.1、npm 11.19.1、PHP CLI 8.5.4（PDO/pgsql）、PostgreSQL 18.6、Docker 29.8.2 已盤點；PHP Docker 版本另查。
- [x] UEM Quick：29 PASS／0 WARN／0 FAIL，版本一致、ZFS ONLINE、服務與連接埠符合預期。
- [ ] 重新執行 UEM Full／Doctor；處理或確認舊報告 /Media/PurgeTest sticky bit 警告。
- [ ] Dev63 候選 ZIP 上傳 Ubuntu、hash／Manifest／來源與 migration 完整性核對。
- [ ] 比對 Dev61→Dev63 DB schema、API、Server App 與 Win10 Client 相容性。
- [ ] 確認 Dev61 舊 .deb、映像、設定、密鑰、備份／離線副本與隔離還原測試。
- [ ] 完成專用升級與回滾腳本審查及預演，取得執行核准。
- [ ] 原地 apply、Ubuntu／Win10 E2E、升級後 UEM Full 與結案。

**目前僅完成 G0 部分及 G1 Quick；不得標記「Dev63 已部署」或「已具備回滾保證」。**
