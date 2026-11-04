# AGENTS.md｜iSTUDY AI 開發、打包與交付最高規範

**適用** ChatGPT、Claude、Codex、GitHub Actions 與其他 AI 開發代理。請先讀 [README](README.md)、[Dev63 進度決策中心](docs/DEV63_STATUS_AND_DECISIONS.md)、[正式 C 混合方案打包規範](docs/ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)、[跨版本升級與回滾 SOP](docs/VERSION_UPGRADE_SOP.md)。此文件不授權自行部署、合併或修改正式資料。

## 0. 最高權限規則：未經許可不得開 PR（2026-11-04）

**禁止 AI 擅自建立 Pull Request（PR）**，包含 Draft PR、為文件變更建立 PR、為 CI 或開發方便建立 PR，以及在其他分支或其他 Repository 代開 PR。**使用者明確說「請更新 GitHub」「請修改檔案」「請同步 main」都不等於授權開 PR**。只有使用者**明確指定要建立 PR**（或對明確的 PR 建立提案給予同意）才可以開立。此規則優先於其他文件中「每項修改一個分支、一個 PR」等舊流程敘述。

- 未獲開 PR 許可：只在使用者授權範圍內直接更新指定檔案／分支；如果既有規則不允許直接更新 main，則**停止並請求使用者指定處理方式**，不可自行改以 PR、分支或合併繞過。
- **建立分支、合併 PR、部署正式環境、修改正式 DB、刪除資料**都是不同操作；不能從「允許修改文件」推定已取得這些操作的許可。
- 不得自動合併任何 PR。對過去未獲許可而建立的 PR，應告知使用者並按其指示處理，不可悄悄合併。
- 每次 GitHub 寫入前核對 Repo、分支、檔案、授權範圍；完成後回報實際 Commit 與修改路徑，不可只說「已更新」卻未確認。

## 1. 開發與發行嚴格分開（2026-11-04）

- **雲端 AI／沙盒的任務是開發、程式審查、差異追蹤、必要輕量 build／typecheck／測試，以及交付完整精簡原始碼 ZIP**。日常交接**不要**先下載 Electron／Chromium、製作 `.deb`／Windows Client installer，再塞進 `*-src.zip`。
- **正式採用 C 混合方案**：`*-src.zip` **不含 `dist/`、`node_modules/`、`.deb`、Client 安裝程式、快取、測試暫存與 log**；正式成品另外交付，附版本、來源快照／Commit、SHA-256。Dev63 ZIP 盤點約 8.99 MB，非固定大小限制。必要的 `deploy/vendor/age-v1.2.1-linux-amd64.tar.gz` 是離線建置例外，**必須保留**。
- **Ubuntu 隔離環境優先負責完整組譯、安裝依賴、製作新版 Server App `.deb`、Linux Client 安裝檔、整合驗收**；Windows Client 在適當 Windows 環境或核准 CI Runner 打包。**不代表一般 GitHub CI 不可編譯／測試**；只有在明確發行／驗證需求下，才在 CI 產生大型安裝 artifact。
- **保留完整核心原始碼**：`apps/server/`、`apps/client/`、`packages/`、`server/`、`agent/`、`scripts/`、`deploy/`、`types/`、SQL／migrations、測試與設定。禁止以「縮小 ZIP」之名移除 Electron Server App 原始碼、首次設定精靈、教材管理、API 安全邊界或必要工具。
- 原始碼 ZIP 交付前先掃目錄、比對 SHA-256、核對必留檔案、生成或附 `PACKAGING-MANIFEST.md`。不得因為 ZIP 小就宣稱程式完整或可用。參考[盤點](docs/DEV62_DEV63_FILE_DIFF_AUDIT_2026-11-04.md)與[架構審查](docs/DEV63_SERVER_APP_PACKAGING_REVIEW_2026-11-04.md)。
- **部署不等於交付 ZIP**：`deploy/patch_dev62.sh` 既有流程需要同版 `dist/*.deb`；正式部署前在受控 Ubuntu 先建版號一致的成品。不要拿舊 Dev62 `.deb` 假冒新版。這次只改文件，不得私自修改部署腳本或 CI。

## 2. 安全與驗收

- Server App 的 `/api/admin/*` 刻意限制於 `127.0.0.1:4081`；不准擅自開到 LAN、公開網路或 Cloudflare Tunnel。要做手機 Web 管理必須獨立規劃授權、驗證、操作代理、最小權限與稽核後再經核准。
- 禁止自行修改正式 PostgreSQL、正式 Ubuntu、`/Media` 教材或密鑰；需要資料庫驗證，先用隔離測試環境。
- `CI PASS ≠ Ubuntu 實機驗收 PASS`；區分「靜態檢查／編譯／模擬／實機整合／正式驗收」。沒有測的項目標「待驗證」，不得聲稱已完成。
- **禁止未經授權開 PR**；不自動合併 PR、不推送未驗收 Dev63 程式；本次已獲授權的文件更新可直接提交指定分支；GitHub main 目前是 Dev62，Dev63 本機候選開發依既有進度中心處理。
- 需求或架構改動、取消 GUI、改 localhost API、修改資料庫／權限及部署流程都要使用者另外核准。

## 3. AI 接手順序

1. 先確認本次作業基線（GitHub main、Dev63 本機 ZIP 或 Ubuntu 工作樹）；勿混淆不同來源及進度。
2. 讀 `docs/DEV63_STATUS_AND_DECISIONS.md`，遵守已核准的業務功能與安全界線。
3. 按任務實際需求決定是否測試；不為了生成交接 ZIP 就做大型安裝封裝。
4. 輸出精簡 ZIP／程式改動摘要、SHA-256、必留／排除清單及「已測／未測」證據。
5. 僅在正式發行階段建 `.deb`／Client installer，並完成目標 Ubuntu／Windows 驗收。

**嚴禁把「原始碼 ZIP 排除 `.deb`」誤解成「正式系統無需 Server App」。** 詳細規則以 [C 混合方案](docs/ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md) 為準。

## 4. 所有版本升級強制 SOP（2026-11-04）

- **每次 Dev／正式版本升級前，必須先閱讀並逐關執行 [VERSION_UPGRADE_SOP.md](docs/VERSION_UPGRADE_SOP.md)**：G0 現況與來源 → G1 UEM Quick／Full／Doctor → G2 版本／Migration／Client 差異 → G3 舊版完整備份 → G4 隔離還原與回滾預演 → G5 核准後 apply → G6 Ubuntu／Client 驗收 → G7 失敗回滾 → G8 GitHub 去識別化結案紀錄。
- **優先使用 iSTUDY 既有 UEM、Doctor、istudyctl 和對應版本部署腳本**，不要一開始就自製替代盤點；Quick PASS 不等於 Full／Doctor 新鮮 PASS，備份存在不等於可還原。
- 使用者可核准**原地升級現有測試服務**，但不得因此跳過備份、回滾驗證；不能用舊版 patch 腳本直接升跨版本。Client 相容性須先驗證。
- 未通過任何必要關卡不得聲稱已部署或可安全回滾；更新文件不等於授權 apply、PR、正式 DB 修改。
