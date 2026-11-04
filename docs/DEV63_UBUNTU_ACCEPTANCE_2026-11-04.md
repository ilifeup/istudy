# iSTUDY Dev63｜Ubuntu 實機驗收進度與 Dev64 防錯交接（2026-11-04）

> **狀態：Dev63 已在使用者 Ubuntu 主機原地部署，非破壞性整合驗收 PASS；完整 G6／G8 尚未結案。** GitHub `main` 仍是 Dev62 原始碼；本頁記錄 Ubuntu **本機 Dev63 候選／部署**的實測證據，不代表 Dev63 原始碼已推上 main。未經許可不建立 PR、不推送未驗收程式、不修改正式 DB／教材。

## 1. 真實部署與版本

- 2026-11-04，Ubuntu 26.04.1 主機；原地 Dev61→Dev63 部署後，HTTP `/api/health` 回報 `ok=true`、`db=ok`、`version=1.0.0-dev.63`、`api_level=1`、`min_api_level=1`。
- 三個容器狀態及映像契約：`istudy-php-1` = `istudy/php:1.0.0-dev.63`、`istudy-agent-1` = `istudy/agent:1.0.0-dev.63`、`istudy-nginx-1` = `nginx:1.28.0-alpine`；均 PASS。
- PostgreSQL 18.6；先前核對 migration 0011～0014 已套用；WOL `show_on_login` 預設 false，USB 批次／檔案資料表與相關索引契約已核對。
- Dev61 回復資料保留；**不能因備份存在而宣稱 G4 隔離還原、完整回滾、Client 相容性已通過**。不得刪除舊映像、備份或回復資料。

## 2. 分批驗收紀錄（勿混淆靜態／實機）

| 階段 | 當次結果 | 證據類型／限制 |
|---|---|---|
| B01–B06 | PASS 16／FAIL 0／WARN 2／SKIP 6 | 靜態／唯讀檢查為主 |
| B07–B11 | PASS 12／FAIL 0／WARN 0／SKIP 7 | 靜態／唯讀檢查為主 |
| B12–B18 | PASS 23／FAIL 0／WARN 0／SKIP 4 | 靜態／唯讀檢查為主 |
| B19–B22 v1 | 24 FAIL（測試基礎設施誤報） | `timeout` 呼叫 shell function、容器 `/dev/stdin` 權限等；**不得當業務失敗** |
| B19–B22 v2 | 18 FAIL（測試基礎設施誤報） | PHP 測試來源不可讀、Python `PYTHONPATH` 未設 |
| B19–B22 v3 | 19 PASS／2 FAIL／0 INFRA／0 SKIP（21 項） | 兩個 PHP 契約／測試隔離問題，後續已修正 |
| **最終非破壞性整合驗收** | **27 PASS／0 FAIL／0 INFRA／3 SKIP（30 項）** | **2026-11-04 15:24:34 +08:00，實際 Ubuntu 執行** |
| E2E Readiness | 環境盤點完成；未執行 E2E | 2026-11-04 15:26:23 +08:00 |

**最終 27 PASS 的構成**：A 類 API/容器 4 項；B19 15 支 PHP 測試＋測試清單 1 項；B22 6 組 USB 測試＋清單 1 項。B22 的 6 組共 **52 個單元案例 PASS**（8+8+13+3+3+17）。B20／B21 另有前期唯讀 migration／WOL 契約檢查結果，**不應將不同輪次的 PASS 累加當成同一批 E2E**。

### 最終報告判定

```text
FINISHED PASS=27 FAIL=0 INFRA=0 SKIP=3 TOTAL=30
VERDICT=NON_DESTRUCTIVE_CHECKS_PASS_PHYSICAL_E2E_PENDING
```

**SKIP 三項**（全部未驗收，不可當 PASS）：

1. WOL 實體喚醒及 Client 登入頁公開設備顯示／登入後入口移除。
2. USB 專用實體裝置、真實匯入、校驗、中斷恢復／回復。
3. 真實角色登入、HTTP API 寫入、401/403、交易與隔離 PostgreSQL E2E。

**其他仍待逐項核實**：Win10 Dev61 Client 與 Dev63 Server 相容、Server App 完整操作、Page Atlas、UEM Full/Doctor 新鮮報告、備份隔離還原、正式回滾演練與 G8 結案；不得用本次 27 PASS 取代。

## 3. B19 兩項實際修正（僅 Ubuntu 候選來源測試檔）

**`server/tests/dev63_group_mutation_contract.php`**：原靜態測試只找 `require_editable_account($a, account_target($a, $aid))`，正式路由實際使用 `account_target($a, $aid, true)`，在交易內鎖帳號列（`FOR UPDATE`），先全批驗權再寫入與稽核；更新測試契約後 PASS。**不得移除正式鎖定來迎合舊測試**。

**`server/tests/unit/dev63_history_revoked_tags.php`**：
- `personal_pg_bool()` 依賴 `core.php` 的 `pg_bool()`；隔離 Mock 測試缺載入，先出現 undefined function。
- 直接載入完整 `core.php` 又與 Mock `q_all()` 重複宣告；改為測試內等效 `pg_bool()` 替身（長期應重構純函式共用）。
- 正式 `personal_media_redact()` 在撤銷權限後**省略** `tags` 欄位；舊測試要求 `tags=[]` 不符合安全契約。改成 `array_key_exists('tags', ...)` 必須為 false，且仍有權限的 `tags=['public']`，最終 PASS。

兩個修正只在 Ubuntu 本機 Dev63 候選測試來源；**尚未同步到 GitHub Dev63 程式分支**，不可聲稱原始碼已修復。未修改正式業務程式。

## 4. Dev64 避免重犯的驗收工具設計

- 優先參考 **`dev63-final-acceptance.sh`**（當次 27/27 非破壞性檢查 PASS）與 **`dev63-e2e-readiness.sh`**（只盤點），分批腳本保留作歷史參考。
- 本輪 11 支 Bash 腳本已彙整成離線 ZIP，含 `README.md`、`SHA256SUMS.txt`，分為目前參考版／歷史錯誤版／歷史升級版。**ZIP 為使用者本機交付，不在公開 GitHub 上傳腳本、完整原始日誌或敏感資料。**
- ZIP SHA-256：`ed3627752c6c8fbe663413f4d5101fdfafed33719b103b27917657a0e09a3279`；此 hash 僅驗證封存檔完整性，不代表 Dev64 可直接執行。
- 執行時區分 PASS／FAIL／INFRA／SKIP；使用非 root 隔離容器、唯讀來源、停用網路、`PYTHONPATH`、timeout、資源限制；每次檢查退出碼及證據。
- 不得直接對正式 DB、`/Media` 或 WOL 目標執行破壞性 E2E。Dev64 以 CI 自動隔離與正式主機非破壞性 Smoke 分工，詳見 [Upgrade Manager 提案](UPGRADE_MANAGER_DEV64_PROPOSAL_2026-11-04.md)。

## 5. G6/G8 未完成事項與下一步

1. 在獨立且**新建、未使用**的 PostgreSQL 實例／資料庫執行認證角色 API E2E，保留 DB/HTTP/交易證據；不覆寫 `istudy_restore_test`。
2. 準備專用測試 USB 與隔離目的地，完成真實匯入／重複檔／失敗回復；正式教材不受影響。
3. 指定測試 WOL 設備與明確授權，測登入前／後顯示與真正喚醒。
4. Win10 Client 相容、UEM Full/Doctor、回滾演練等必要 G6 項目補證；取得 G8 核准才結案。
5. 保留 Dev61 rollback 證據，兩支 PHP 測試修正待正式 Dev63 來源同步授權，不擅自開 PR。

## 6. GitHub 與現場資料邊界

此頁為公開 GitHub **去識別化摘要**。詳細測試報告、原始輸出、環境資訊、備份與密鑰保留在使用者本機受控目錄；不得公開。2026-11-04 15:24 的 27 PASS 來自現場執行輸出與上傳報告，不代表本次 GitHub 文件更新又重新執行測試。
