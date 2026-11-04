# iSTUDY｜Upgrade Manager 自動化升級設計提案（2026-11-04）

> 狀態：**設計提案，尚未實作、未核准部署**。適用 Dev64 規劃與後續版本。遵守根目錄 [AGENTS.md](../AGENTS.md)、[跨版本 SOP](VERSION_UPGRADE_SOP.md) 的 G-1/G0～G8。**本提案不降低備份、回滾演練與授權門檻。**

## 目標與非目標

**目標**：一般使用者只需一個升級入口；在 CI／發行環境預先自動建立隔離測試資源，正式 Ubuntu 主機執行有防護的升級預檢、可驗證備份／回復點、受控切換、非破壞性 Smoke Test、報告與失敗處置。減少**人工**隔離與重複測試，不是取消隔離或跳過測試。

**非目標**：不允許對正式 DB 執行會寫入的 E2E、不保證任何 migration 都能無損自動回滾、不自動刪除舊備份或映像、不自行對外開放管理 API、不以 CI PASS 取代實機／Windows Client／WOL／USB E2E。

## 推薦架構

```text
Source Commit + VERSION + Signed/Hashed Artifacts
    -> CI isolated PostgreSQL + PHP/Agent + migrations + auth/API E2E
    -> Release Manifest / SHA-256 / compatibility / tested rollback plan
    -> Ubuntu Upgrade Manager: preflight (G0-G2) + approval gate
    -> consistent recovery set (G3) + restore rehearsal (G4)
    -> authorized apply (G5): lock -> drain -> expand -> migrate -> switch
    -> smoke + UEM/Doctor + targeted acceptance (G6)
    -> observe; safe rollback or STOP/manual recovery (G7)
    -> sanitized report, retention and sign-off (G8)
```

## 核心模組

| 模組 | 強制能力 | 失敗處置 |
|---|---|---|
| Release Manifest | Source Commit、VERSION、映像 digest、.deb、migration hash、Client API 範圍、SHA-256；產物同源 | STOP |
| Preflight | UEM Quick／Full／Doctor 新鮮度、磁碟、ZFS、PG、Port、排程／工作、舊版版本、依賴、相容矩陣 | STOP |
| Lock & Journal | 單一升級鎖、持久化 state machine、每步前後狀態與 exit code、SIGTERM／當機恢復判斷 | UNKNOWN 時 STOP，禁止盲目重跑 |
| Backup & Restore | DB／設定／金鑰／映像／Server App／必要媒體的一致復原集合；異故障域副本；隔離還原驗證 | 備份可讀≠可還原；未通過 STOP |
| Migration Planner | Expand→Migrate→Contract；每支 migration 可逆性、鎖表風險、執行時間、相容窗口 | 破壞性變更需單獨核准 |
| Deployment | 明確的 maintenance window、工作排空、版本切換、失敗條件、舊映像保留 | 僅符合相容條件才自動回退 |
| Acceptance | 自動非破壞性 Smoke；CI 隔離 HTTP/角色/寫入 E2E；實體 USB/WOL/Windows Client 另列 | SKIP/NOT_TESTED 不得計入 PASS |
| Reporting | 單一 Markdown 報告＋詳細證據，PASS/FAIL/INFRA/SKIP/UNKNOWN，去識別化 GitHub 摘要 | 不得假報結案 |

## 資料安全與回滾規則

1. 版本升級不是單純替換 Docker tag；DB schema、資料、Client 協定、設定與媒體都須相容。
2. 先 Expand（向後相容新增），再 Migrate（搬遷及雙讀寫策略經審核），最後在**後續獨立版本** Contract（刪舊欄位）；Contract 不得與仍需舊 schema 的可回退版本共存。
3. 已接受新版寫入、不可逆 migration 或新舊 DB/Client 不相容時，**禁止**自動只切回舊 Docker；進入安全停寫、保存現場、人工核准一致性還原，明示 RPO/RTO。
4. 避免使用既有固定名稱測試 DB（例如 `istudy_restore_test`）做自動還原；隔離資源必須新建、驗證不存在、限制權限，不自動刪除未知 DB。
5. 禁止將 .env、備份、個資、完整 log、MAC、IP、密鑰推送公開 GitHub。

## 建議分期與驗收門檻

- **Dev64 / Upgrade Manager v1**：先做設計、預檢、Release Manifest、單一鎖、持久化日誌、隔離 CI API E2E、G0～G4 Go/No-Go、非破壞性 Smoke、報告；不預設自動 apply。
- **Dev65+**：在已驗證的相容／回滾條件下，導入單一指令受控 apply、觀察、有限條件自動回退；Client、實體 USB/WOL 仍有獨立驗收。
- **未來目標 CLI（未實作）**：`sudo istudy upgrade dev64`。不得誤以為目前已有此命令。

## Dev63 實測帶來的防錯要求

- 容器測試使用唯讀來源掛載、停用網路、non-root、tmpfs、timeout、資源上限；確認測試檔可讀且 Python `PYTHONPATH` 正確。
- 不可用 `timeout` 直接呼叫 shell function；不可假設容器可讀 `/dev/stdin`；不可把 INFRA 誤列成業務 FAIL。
- PHP Mock 測試不得與完整 `core.php` 的 `q_all()` 重複宣告；長期將 `pg_bool()` 抽為可獨立載入的純函式。
- 群組成員交易內權限守衛須維持 `account_target(..., true)` 的鎖定；撤銷教材權限時，歷程 API **完全省略** `tags` 欄位，不是回傳空陣列。
- 修正測試契約要追溯正式程式，不能為追求綠燈放寬安全；每次測試保存實際執行的版本與 source hash。

## 與現行 SOP 的關係

本提案是「將人工隔離流程自動化」的實作路線，**不是免除 G4 隔離還原**。若要調整現行 SOP，須在 Dev64 規格審查時明確核准，並同步更新 AGENTS、SOP、計畫書、CI 與驗收矩陣。
