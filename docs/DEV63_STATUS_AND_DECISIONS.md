# iSTUDY Dev63｜進度與決策中心（2026-11-03）

> **主要規格**：[Dev63 完整詳細開發計畫書 v2.0](./DEV63_PLAN_v2.0.html)（HTML，離線可讀，含 Page Atlas Lite 與 Claude 本機審查）。本文用於工作狀態、驗收紀錄、下一步和決策索引；實作細節以 v2.0 為準。若 v2.0 歷史附錄與最新決策衝突，以本頁「已定案」及 v2.0 最新決策專章為準。

## 一、開發邊界（已定案）
- 現有基線：Dev62；目標 `1.0.0-dev.63`，完成驗收後才改版號。
- **本輪在 Ubuntu 本機開發**；GitHub 只同步計畫、決策、驗收與進度，**不在 GitHub 上開發 Dev63 功能**。
- 不自動合併 PR；本輪不建立 Dev63 開發 PR、不推送未驗收 Dev63 程式。
- 不直接操作正式資料庫。開發與測試須使用隔離環境；資料庫破壞性變更先備份、預演、驗收。
- 全新 Dev63 規格，不建立舊版相容層；舊資訊不能推翻已決策內容。

## 二、重要決策（2026-11-03）
| 類別 | 規則 |
|---|---|
| Client 登入前 | 登入頁公開喚醒，免登入；僅列 `enabled=true AND show_on_login=true` 設備，LAN 限制、冷卻、稽核 |
| Client 登入後 | 完全移除 WOL 入口、管理、喚醒；不得殘留可繞過的登入後 WOL API |
| WOL 設備 | `show_on_login` **全部預設 false**，包含既有設備；只有 Super 可逐台開啟。與 `enabled` 獨立；`login_display_name` 獨立可設 |
| Server 登入 | **僅 Super／Admin**，User 禁止登入（包含 API 檢查） |
| Client 文字 | 不顯示字面 `Super`；移除不必要說明，不要自動替換成「最高管理者」 |
| PC 資訊 | Super、Admin 可用，實際欄位授權需審查 |
| Log | Server error、Agent job、Host maintenance、Client upload 四類**各自**可按「全部清除」；清除需 Super、確認、稽核；不可混刪使用記錄與安全 audit |
| Page Atlas Lite | P0；索引的頁面、API／DB 對應須查實、測試、Ubuntu 驗收，不把 `unverified` 當完成 |
| USB | UDisks2 + 受控 `istudyctl media-import` 主機服務；USB 唯讀來源、白名單目標 /Media、分段複製及校驗，Agent 深度掃描建立教材索引；App Admin 不等於 Linux sudo |
| 教材／閱讀 | Admin 授權教材操作；PDF 大小寫無關、全文命中高亮；字幕面板響應；刪最愛、保留書籤 |

## 三、已完成（區分來源）
- [x] Dev63 完整需求規格及 v2.0 計畫書完成；GitHub 同步文件。
- [x] Claude 產出 Dev62 → Dev63 本機靜態審查 HTML。
- [x] Claude 製作 **Dev62 main 0501715 + Page Atlas Lite** 的本機整合候選 ZIP，附 `docs/DEV63_PAGE_ATLAS_INTEGRATION.md`。
- [x] Claude **回報**：typecheck、5 renderer build、Atlas 59/59、Preview 4/4、PHP syntax、私有索引 bundle 防洩漏通過。**以上僅是 Claude 本機紀錄；未經 Ubuntu 實機複驗。**

## 四、尚未完成／不得誤標完成
- [ ] Ubuntu 本機候選基線比對、檔案校驗、保留 Dev62 安全回退點。
- [ ] 批次 A：登入／角色／WOL（含曾漏掉的 `POST /api/wol/wake`）、Server 18→6 分類、學習中心／會員與群組、Admin PC Info。
- [ ] **P0 Page Atlas Lite**：按新導覽更新 34 頁及新增頁；逐一驗證 route/source/component/API/DB，標記來源與驗證證據；安全／索引一致性／Bundle／Ubuntu 驗收。
- [ ] 批次 B：USB UDisks2 受控匯入、批次改名、批次刪除、目錄／DB／字幕／轉檔關聯一致性。
- [ ] 批次 C：總覽、UEM、4 類 Log 獨立清除、備份容量、排程。
- [ ] 批次 D：PDF 搜尋高亮、字幕、書籤、Client 文字與響應。
- [ ] 批次 E：Win10 latest.yml 404 **先查是否未發布**；備份內容、容量及跨環境回歸。
- [ ] Ubuntu 實機及安全驗收；尚未宣告 Dev63 完成。
- [ ] PR #5 深度 Call Graph 研究版不合併；PR #6 Lite 僅供參考。本輪不操作 PR。

## 五、驗收門檻
1. WOL：公開清單只有 Super 手動開啟的設備；Client 登入後 WOL 全部消失；普通登入者無法呼叫登入後喚醒；未登入公開喚醒仍正常。
2. Server App：只有 Super/Admin 可登入；Admin 的受限總覽、教材、PC資訊能使用；User 前後端均拒絕。
3. Page Atlas：每個 API/DB 對應有實際程式證據；權限 401/403/200、私有索引不洩漏、路由／頁碼一致，Ubuntu 實機通過。
4. USB：裝置掛載安全、來源唯讀、目的地限制、空間預檢、檔案校驗、中斷恢復、資料庫同步、稽核。
5. Logs：四類各自清除、保留必要稽核，不破壞使用記錄。
6. Dev63 全功能驗收前，不變更正式版、不把 Claude 靜態測試視作正式 PASS。

## 六、最高工作規則
**CI PASS ≠ 驗收 PASS**。問題須在同一追蹤中心記錄原因、修正、測試與回歸，不能因單次 CI／本機測試綠燈直接結案。權限與破壞性操作使用真實角色測試；不自動合併。下次更新計畫或進度，**同步更新**本頁、[Dev63 v2.0 HTML 計畫](./DEV63_PLAN_v2.0.html) 及對應追蹤 Issue；嚴禁已定案事項回退舊規格。


## 七、2026-11-04｜Dev63 候選版與 Ubuntu 隔離驗收進度

> **狀態：候選原始碼尚未在 Ubuntu 完成整合驗收；GitHub main 仍為 Dev62。此處記錄工程估計，不是正式完成率。**

- Dev63 沙盒候選 ZIP：最新已回報版本為 `iSTUDY_Dev63_Five_Modules_Offline_Validation_Full_Coverage_20261104.zip`（542 個 ZIP 項目）；**尚未推送候選原始碼至 GitHub**。
- 五個核心工作群組之工程估計：Page Atlas Lite **77%**；PostgreSQL／Server API **71%**；Super／Admin 權限 **74%**；書籤／學習歷程 **84%**；USB 匯入／Agent **75%**。以上不是子功能逐項核定百分比。
- Dev63 專項 Node.js 測試：**100 PASS**（沙盒）；較廣範圍測試紀錄：**221 PASS／2 FAIL／1 SKIP**，其中兩個失敗涉及缺少 `esbuild`。完整 TypeScript build、PostgreSQL 18 HTTP E2E、USB 實機與跨平台安裝**均未完成**。
- Ubuntu 主機使用者回報：**Ubuntu 26.04.1 LTS**、Node **24.22.1**、npm **11.19.1**、PostgreSQL **18.6**、Docker **29.8.2**；**主機 PHP CLI 尚未安裝**（需確認容器 PHP／PDO PostgreSQL 驅動的實際配置）。
- 使用者執行預檢時所在目錄是 `/home/media/GitDev/ShareModules-MVP-Test/Shared_Modules_V1/mobile`，**屬 Shared Modules Flutter 專案，不是 iSTUDY Dev63**；該目錄已有未提交修改，嚴禁對其執行 iSTUDY 安裝、Migration、reset 或清理。
- **下一步優先**：① 在獨立目錄確認 Dev63 ZIP、檔案完整性與 Node 依賴，執行 typecheck/build；② 確認 PHP 執行環境後，使用隔離 PostgreSQL 18 測試 DB 做 API／角色權限／交易 E2E；③ 再驗 USB／Agent、Page Atlas、備份還原與 Client 安裝。不得操作正式 DB、正式教材或服務。
- 全部需求逐項盤點目前是**工作清單而非已逐條驗證的驗收矩陣**；不要將工程估計誤寫成正式驗收率。


## 八、2026-11-04｜升級 SOP 與 Ubuntu Dev61 實機盤點

- **跨版本強制規範**：[VERSION_UPGRADE_SOP.md](VERSION_UPGRADE_SOP.md)；每次升級按 G0～G8 執行並留存去識別化證據，先證明可回滾再 apply。
- **本次部署模式已更新**：使用者指定將現有 Ubuntu **Dev61 原地升級至 Dev63**，不新增第二套常駐服務；Windows 10 Client 目前 **Dev61**，相容性待驗證。這是部署模式核准，**不是略過備份或立即執行 apply 的核准**。
- **2026-11-04 12:26:56 UEM Quick**：29 PASS／0 WARN／0 FAIL／0 UNKNOWN；VERSION.json、API、Agent、PHP、.deb 均 dev.61；PostgreSQL 18/main、Docker、istudy.service、hostcheck.timer 正常；4080 LAN、4081/5432 localhost；/Media ZFS mirror ONLINE。
- **尚有風險**：UEM Quick 內嵌 Doctor 來自 **11/02 15:49** 舊報告，當時 /Media/PurgeTest 有 sticky bit 權限警告；須重新 Full／Doctor 確認。DB 與 /srv/istudy/backup 同 SSD，需離線副本及隔離還原驗證。
- **未完成**：Dev63 ZIP 來源與雜湊、Dev61→Dev63 migration／Client API 差異、舊 .deb／Docker image 可重建、備份／回滾預演、實機 apply 與 Win10 E2E。**目前不得宣稱已升級或具備回滾保證**。

## 九、2026-11-04｜升級 SOP v2.0 納入 Dev63 HTML 計畫書與 Full 實測

- 已將 [跨版本發行治理 SOP v2.0](VERSION_UPGRADE_SOP.md) 納入 [Dev63 HTML 計畫書](DEV63_PLAN_v2.0.html) 的新 Tab「20 升級SOP v2.0」，涵蓋 G-1/G0～G8、UEM／Doctor／Hostcheck、報告保存、回滾與 Win10 Client 相容。
- **Full UEM 新實測**：使用者於 2026-11-04 執行 `sudo istudyctl uem --mode full`，耗時 **9.0 秒**；輸出一項 `教材權限（/Media，Agent uid 10001）` 警告，涉及 `/Media/PurgeTest` sticky bit；但最終彙總顯示 `注意 0`。**兩者矛盾，需讀取 `latest.json` 的 `doctor.items`、`issues`、`counts`、時間戳釐清，不能視為無警告 PASS。**
- 下一步：`sudo istudyctl media-acl` 只讀檢查、保存 Full `latest.md`／`latest.json`；**未核准不得執行 `--fix`**。舊版 .deb／映像／DB／Media 回滾能力及 Dev63 Migration／Win10 Client 相容性仍待驗證，G5 apply 不放行。

## 十、2026-11-04｜Dev61 Full UEM 附件核對及教材 ACL 修復

- 使用者已執行 `sudo istudyctl media-acl --fix`（輸入「修復」確認），再執行 `sudo istudyctl media-acl` 顯示「教材權限正常」。這是 **ACL 單項修復成功**，仍需重跑 Full UEM 建立修復後基準。
- 依使用者提供的 Full UEM `latest.json`／`latest.md`（2026-11-04 12:37:25，修復前）核對：版本來源均為 `1.0.0-dev.61`，PostgreSQL 18、三容器、ZFS mirror、4080/4081/5432 綁定位址、QSV/VA-API、Office、FunASR 均通過。
- **UEM 報告統計缺陷待修**：`counts.PASS=29, WARN=0, INFO=1`，但 `doctor.items` 中 `media_acl.status=warn`，`issues` 有 1 筆 WARN，`overall=WARN`；彙總 counts 未涵蓋 Doctor 的警告，造成表頭「注意 0」誤導。需修改 UEM 統計與顯示規則，補回歸測試，區分主機項與 Doctor 項，避免雙重計數。
- `/srv/istudy/backup` 與 PostgreSQL data 同位於 SSD；升級前需獨立備份、隔離還原驗證。**尚未核准 Dev63 apply。**

## 十一、2026-11-04｜ACL 修復後 Full UEM 複驗

- 使用者於 `sudo istudyctl media-acl --fix` 修復後重新執行 `sudo istudyctl uem --mode full`，終端顯示 **5.4 秒、異常 0、注意 0、不存在 0、未知 0**；另執行 `sudo istudyctl media-acl` 顯示 **「教材權限正常」**。
- **G1 暫列通過**（依現場輸出）；修復後 `latest.md`／`latest.json` 尚待另行保存／核對，以形成可追溯的正式升級證據。
- **G2～G4 尚未完成**：Dev61→Dev63 Migration／API／Win10 Client 相容矩陣、舊 .deb／Docker image／DB／Media 回滾包、獨立備份及隔離還原預演。G5 apply 仍未放行。


## 十二、2026-11-04 15:26｜Ubuntu Dev63 已部署與最終非破壞性驗收（更新，取代前述「尚未 apply」的歷史狀態）

> **最新狀態優先**：本頁第七～十一節為當日較早的歷史快照；當時的「尚未升級／禁止 apply」不再是目前部署事實。**Ubuntu 現場已原地部署 Dev63**；GitHub `main` 仍是 Dev62 程式。這是部署與非破壞性測試進度，**不是 G6/G8 全面結案**。

- 現場 `/api/health`：`ok=true`、`db=ok`、`version=1.0.0-dev.63`；PHP/Agent/Nginx 容器版本／運作檢查 PASS；PostgreSQL 18.6，0011～0014 migration 已有前期唯讀核對證據。
- **2026-11-04 15:24:34 最終整合驗收**：`PASS=27 FAIL=0 INFRA=0 SKIP=3 TOTAL=30`，`NON_DESTRUCTIVE_CHECKS_PASS_PHYSICAL_E2E_PENDING`。其中 B19 **15 支 PHP 測試 PASS**，B22 **6 組、52 個 USB 單元案例 PASS**；不要將 52 個案例加到 30 個檢查項目重複計數。
- 早期 B19/B22 腳本 v1/v2 的大量 FAIL 為測試基礎設施問題；v3 剩 2 項 PHP 測試契約問題，已在 Ubuntu 候選來源修正後 PASS：群組批次權限 `FOR UPDATE` 斷言，以及撤銷權限歷史紀錄不回傳 `tags`（另修正 Mock `pg_bool` 初始化）。**這兩個修正尚未同步到 GitHub 的 Dev63 程式來源。**
- **2026-11-04 15:26:23 Readiness**：只完成環境盤點，**沒有**建立隔離 DB、執行 E2E 寫入、USB 匯入或 WOL 封包。
- **3 項 SKIP**：WOL 實體喚醒與登入顯示、USB 真實匯入及回復、角色認證 API 寫入 E2E。Win10 Client、UEM Full/Doctor 最新驗證、完整回滾與 G8 仍待另行確認，**不宣稱 Dev63 全面驗收完成**。
- 已將 **11 支本輪 Bash 腳本**彙整為本機 ZIP（目前參考版／歷史失敗版／歷史升級版，附 SHA-256），避免 Dev64 重複犯錯；公開 GitHub 只記錄去識別化摘要，不上傳本機原始日誌、密碼或備份。
- **Dev64 規劃**：以 [Upgrade Manager 自動化升級提案](UPGRADE_MANAGER_DEV64_PROPOSAL_2026-11-04.md) 取代每次人工準備隔離環境；**CI 自動隔離仍必須存在**，正式 Ubuntu 只做非破壞性 Smoke 與受控升級，保留 G0～G8 安全關卡。

詳細紀錄：[Dev63 Ubuntu 驗收與 Dev64 防錯交接](DEV63_UBUNTU_ACCEPTANCE_2026-11-04.md)。本次只更新文件，**未建立 PR、未改正式 DB、未推送 Dev63 程式**。

## 十三、2026-11-04｜Server App Dev63 實機 UI 驗收、視覺缺失與 Client 更新待查

> 本節為第十二節之後的最新現場證據；僅更新文件，不表示 G6/G8 結案。

### Ubuntu Server App 實際安裝與畫面
- 現場起初安裝 `istudy-server-app 1.0.0~dev.61`，即使後端 `/api/health` 已回報 `1.0.0-dev.63`，GUI 仍是 Dev61；原因為**部署流程未同步更新桌面 .deb**，不能用後端健康檢查代表全套升級。
- 使用者已驗證候選 `istudy-server-app_1.0.0-dev.63_amd64.deb`（SHA-256 `892bb36e44c9bd08d991b611c6e3d6f13503a9e004e540adbe36a94cfbd2f4b1`），安裝輸出 `SERVER_APP_DEV63_INSTALL_PASS`；dpkg 回報 `1.0.0~dev.63`，`build-info.json` 回報 `1.0.0-dev.63`／Electron `44.9.0`。
- 安裝版 `resources/app/renderer/app.js` 與 Dev63 `dist/server/renderer/app.js` SHA-256 一致（`d5122882cc6bb44e82bd8cea931a97df2317d77b08672246f8aa52f8deabc0fc`），與 Dev61 編譯檔不同。
- 使用者提供兩張**實際 Ubuntu GUI 截圖**：Super 已登入，左側顯示 Dev63 新增「USB 匯入紀錄」，頂部可見 Page Atlas，頁尾顯示 `Server V1.0.0-dev.63`；另一張為課程群組／個人覆寫頁。這證明新版 GUI 已載入，**不是舊版快取或未安裝**。
- `apps/server/src/index.tsx` 已註冊 `srv-usb-imports`；`apps/server/src/srv.tsx` 已接入選單，並限制 `srv-usb-imports` 為 Super-only。此為 UI 可見證據，不等於 USB 真實匯入成功。
- USB 頁實際顯示「USB 主機偵測工具尚未安裝」及「USB 異常核對工具尚未安裝」；**工具／部署缺口未解決，USB 實體 E2E 未通過**。不得按匯入、修復或重新部署來掩蓋缺口。

### UI 設計驗收：未達原定美化預期
- 使用者明確指出 Dev63 UI 仍大致沿用 Dev61 視覺，未達先前承諾的明顯美化／全面重設計。雖有功能與導航更新，**UI 視覺設計驗收不通過**；不可把「新功能已顯示」誤記成「UI 改版完成」。
- 截圖可見：頂部主選單＋管理次選單＋側邊 Server 導覽三層並存；大量原生表單／按鈕／邊框、資訊層級及間距不一致；USB 頁大量留白；課程群組頁操作區分散；Page Atlas 頁碼未與版面一致整合。
- **Dev64 待辦（規劃，未實作）**：先確認視覺稿及功能頁清單，再建立共用 design tokens／表單／卡片／表格／導覽；統一 Server App 與 Client 的設計語言、響應式排版與 Page Atlas 呈現。需有實機安裝後的新舊截圖比對、逐頁一致性、Super/Admin/User 權限及 E2E 驗收；不能只改 CSS 或宣稱 build PASS 即結案。
- 使用者指示 Dev63 UI 討論暫停於此；**不要在本次文件更新自行重構 UI、開 PR 或改動程式**。

### Client 自動更新：實作設計與實際發布分開驗證
- GitHub main 的 `apps/client/electron/main.ts`／`updater.ts` 設計：Windows Client 啟動及每六小時檢查 `/updates/<channel>/`，`electron-updater` 下載後，在無人登入連續兩分鐘時自動安裝並重啟；Ubuntu Client 另依 root systemd 更新服務與狀態檔。
- **此為程式設計，並非 Dev63 已發布或 Windows Client 已收到更新的證據**。需逐版核對 Windows 實際安裝版本、channel、Server `/updates/<channel>/latest.yml`／安裝包與 checksum、下載 HTTP 狀態、更新日誌、相容性及自動安裝行為。
- Dev63 Windows Client 尚未實機升級／驗收；不得因 Server 已升級 Dev63 就宣稱 Client 會自動升級。若更新 feed 未發布或返回 404，應先完成正式發布及安全驗證，不能直接要求 Client 反覆重裝。

### 發行流程防錯
- Dev64+ G0/G6 增加**分別驗證** API、Agent、Server App .deb、Server App 實際 UI、各平台 Client 安裝版／更新 feed 的版本矩陣；部署完成前不得漏掉桌面套件。
- 現場 Dev61 回滾 .deb 與備份仍須保留；G6 仍有 USB、WOL、真實權限 API、Win10 Client、隔離還原等未驗收事項。
