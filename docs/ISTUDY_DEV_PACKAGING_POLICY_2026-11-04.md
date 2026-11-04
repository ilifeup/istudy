# iSTUDY｜開發原始碼與正式發行成品分離規範（2026-11-04）

> **正式採用 C 混合方案。** 適用 ChatGPT、Claude、GitHub Actions、Ubuntu 開發與交付流程。這是**交付與建置規範**，不是取消 Electron Server App 或降低測試門檻。相關證據：[Dev62／Dev63 ZIP 差異盤點](DEV62_DEV63_FILE_DIFF_AUDIT_2026-11-04.md)、[Server App 架構審查](DEV63_SERVER_APP_PACKAGING_REVIEW_2026-11-04.md)。

## 1. 不可混淆的三個產物

1. **開發用原始碼 ZIP（`*-src.zip`）**：提供修改、審查、後續開發、重建；**不得**夾帶 Electron、Chromium、`.deb`、Client 安裝程式、`dist/`、`node_modules/` 等已生成的大型發行產物。Dev63 盤點約 **8.99 MB**，選擇移出文件截圖後約 **6.1 MB**；僅是該次 ZIP 實測，不是硬性容量上限。
2. **正式發行成品**：`istudy-server-app_<version>_amd64.deb`、Windows／Ubuntu Client 安裝檔、必要 Docker／離線部署套件等。**與原始碼分開**，用獨立發布位置、版本、Commit／來源快照和 SHA-256 管理；不得將舊版 `.deb` 假裝是新原始碼的產物。
3. **正式 Ubuntu 執行環境**：PHP／Nginx／Agent Docker 服務、PostgreSQL、`istudyctl`、systemd 與資料；不是這個 Electron `.deb` 的內容。不要把「ZIP 不附 `.deb`」誤寫成「正式主機不需安裝 Server App」。

## 2. 四個階段的正確責任

| 階段 | 雲端 AI／沙盒 | GitHub CI | Ubuntu 實機／正式發行 |
|---|---|---|---|
| 需求、開發、修正 | 修改**原始碼**；按需要靜態分析、語法、型別、局部建置或隔離測試；不可把無法測的功能宣稱 PASS | PR 運行既有 CI 與安全檢查；允許必要的編譯以發現錯誤，**不等同發行封裝** | 保留實機差異與待驗證事項 |
| 日常交付／跨 AI 交接 | 原始碼 ZIP ＋差異清單／檢查證據；**不因交接而下載整個 Electron runtime 或產生安裝包** | 不須每次跑 Build Server／Build Client 發行 workflow | 無需因每次原始碼變更重新安裝 |
| 整合／部署準備 | 清楚交付建置腳本、相依版本、版本號、回退與環境需求 | 可按需建置及檢查；不自動部署 | 在**受控 Ubuntu 測試環境**安裝相依、編譯、檢查產物／權限、做完整整合測試 |
| 正式發行 | 不把成品混進原始碼 ZIP | **僅明確要求發行時**可作備援建置、產生 artifact 與 SHA-256，不能自動部署 | **優先**在 Ubuntu 建置新版 Server App `.deb` 及 Linux Client；Windows Client 原生安裝包在 Windows 或核准 CI Runner 建置；實機驗收後由人核准發布／部署 |

**不是全面禁止雲端編譯**：`npm run typecheck`、`node scripts/build.mjs --all`、CI 回歸測試都可保留；禁止的是日常交付**無需求地反覆產生／攜帶大型發行安裝包**。若 CI 的 `Build Server` 在使用者手動觸發或推送 `v*` 標籤時建置 `.deb`，屬於明確的發行／驗證用途，可保留。不得為縮小 ZIP 偷刪測試或建置能力。

## 3. 原始碼 ZIP 的必留項目

- **核心程式**：`apps/server/`、`apps/client/`、`packages/`（含 `ui`、`shell`）、`server/`（API、Nginx、CLI、SQL、Schema、migrations、私有索引）、`agent/`、`types/`。
- **重建能力**：`scripts/`、`deploy/` 建置與部署腳本、`compose.yaml`、`VERSION.json`、`package.json`、TypeScript／Vite／Electron 及其他必要設定、`.env.example`、`.github/`、測試程式、文字規格與必要圖示。
- **例外必要第三方資源**：`deploy/vendor/age-v1.2.1-linux-amd64.tar.gz`（約 5.19 MB）在目前 Agent 映像**離線**建置流程中需保留，且必須校驗既定 SHA-256。若要改為下載，先提出安全、離線與供應鏈方案，不得擅自刪掉。
- **lockfile**：現有 ZIP 沒有 `package-lock.json`；在有網路的受控開發／Ubuntu 環境產生並核准固定相依版本，納入原始碼；後續適用時用 `npm ci`。**本次文件更新不產生 lockfile，也不修改建置程式。**

## 4. 原始碼 ZIP 應排除／條件保留

| 類別 | 路徑或型態 | 原則 |
|---|---|---|
| **必排除** | `dist/`（含 `istudy-server-app_*_amd64.deb`、`dist/server/`、Client installer、build cache）、`node_modules/`、`build/`、`out/`、`.ui-preview/`、`__pycache__/`、`*.pyc`、`*.tsbuildinfo`、測試暫存、日誌 | 可重建物不放原始碼 ZIP；`dist/server` 僅在特殊**無 Node 的成品組裝流程**可另附於發行素材，並記錄來源與版本，不混入一般 `*-src.zip` |
| **必排除** | 實際 `.env`、金鑰、Token、備份（`*.age`、DB dump）、教材、ASR 模型、個人資料 | 保密及資料安全；只交付範例設定 |
| **條件保留** | `docs/screenshots/`（盤點約 2.87 MB） | 不是執行依賴；交接需要圖文驗收時可保留，否則另放文件包；不得讓有效文件無處可查 |
| **必留例外** | `deploy/vendor/age-v1.2.1-linux-amd64.tar.gz` | 已有離線依賴與固定雜湊，不能被「全排除 `*.tar.gz`」誤刪 |

## 5. ZIP 產出／驗收關卡（每次交接都要做）

1. 以專案根目錄打包；依相對路徑建立 manifest，記錄檔案數量、解壓後大小、ZIP 大小、SHA-256、`VERSION.json`、來源 Commit（若可取得；離線開發另記來源 ZIP／快照）。
2. **掃描** `dist/`、`node_modules/`、`*.deb`、`*.exe`、`*.AppImage`、`*.msi`、快取／模型／資料庫備份／金鑰，驗證沒有意外混入。
3. **核對必留核心目錄及必要資源**；確認 `deploy/vendor/age` 存在且雜湊正確、所有建置腳本／Schema／API／Agent／Client／Server 原始碼完整。沒有驗證就寫「待確認」，**不因 ZIP 小就判定完整**。
4. 與上一候選來源用**正規化根目錄、相對路徑及串流 SHA-256**比較；明列相同、異動、僅舊版、僅新版，核心檔案完整列路徑。
5. 不可為縮小容量而裁掉必要 runtime 在**正式發行成品**中的內容；精簡的是**原始碼交付**，不是縮減正式安裝功能。不同平台相依不要在雲端重複大量下載，除非已核准發行。
6. 產出 `PACKAGING-MANIFEST.md`（可隨交付、非必須每次提交 GitHub）：包含排除清單、例外、可重建指令、版本與 SHA-256、無法驗證事項、回退依據。若大小異常增加，先列出增加來源再決定是否需要。

## 6. Ubuntu 重建與部署（僅步驟，不在本次文件 PR 執行）

1. **Ubuntu 隔離環境**：先確定來源快照、相依版本、工具鏈、空間、網路／離線 Electron zip、資料庫回退方案。補齊 lockfile 後優先 `npm ci`；目前無 lockfile 時只能按受控流程暫用 `npm install`，不得聲稱可重現。
2. 靜態與單元驗證：`npm run typecheck`，`node scripts/build.mjs --all`，必要 PHP、Python、Agent、權限與 API 測試。
3. 建 Server App：`python3 deploy/package_server_app.py`。其預設讀 `dist/server/build-info.json`，必須先執行 `node scripts/build.mjs --app server` 或具備有來源證明的相符 build；Electron 44.9.0 下載版須核對 SHA-256。**打包產物放獨立發行目錄／artifact，不回塞開發 ZIP**。
4. Windows／Linux Client 各在合適平台打包；分別記錄 SHA-256、來源版本及測試結果。
5. **既有部署腳本的限制**：`deploy/patch_dev62.sh` 目前固定讀 `dist/istudy-server-app_<NEW>_amd64.deb`；新安裝離線 bundle 通常也要求 `.deb`。部署時先建置再依腳本要求暫放到部署工作目錄，**不可拿 Dev62 的舊 `.deb` 當 Dev63 安裝包**。若未來要支援無圖形 CLI-only 模式，需要另審需求，不可在這次文件 PR 偷改腳本。
6. 真實 Ubuntu 驗收：Server App 登入／首次設定精靈／18 個管理頁／polkit；Docker／API／DB／Agent；轉檔、字幕、PDF、備份、UEM、權限與回退。**CI 綠燈≠Ubuntu 實機驗收。**

## 7. Server App 不能因壓縮而刪除

目前 `.deb` 是 Electron 圖形管理端，不是後端本體：`apps/server/electron/main.ts` 連 `127.0.0.1:4081`，管理 API／頁面尚無完整手機 Web 替代；`pkexec` 操作受控 `istudyctl`。初始部署與主機維護多有 CLI 替代；但**首次設定精靈、教材與轉檔管理畫面、備份清單、Log、UEM 報告**尚未完整替代。**不准為移除 Electron 而直接將管理 API 暴露在 LAN／Cloudflare Tunnel**；需另行設計授權、審計與主機操作代理並核准。

## 8. 變更控制與完成定義

- **此規範只更新開發及交付作法**：不更動 Dev63 功能、API、DB、權限、部署腳本或現有 CI；未來調整 workflow 必須獨立 PR 和驗證。
- Repo `main` 目前仍以 Dev62 為基準；Dev63 來自**本機 ZIP 候選**，不得稱「GitHub main 已包含 Dev63 功能」。
- 不自動合併 PR、不推送未經驗收的 Dev63 程式、不操作正式資料庫／正式 Ubuntu。
- 驗收結果、缺口、來源與限制要明確分列；不能以 ZIP 容量、一次 CI 成功或 AI 報告推定正式功能完整。
