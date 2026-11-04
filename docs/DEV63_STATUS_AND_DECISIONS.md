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
