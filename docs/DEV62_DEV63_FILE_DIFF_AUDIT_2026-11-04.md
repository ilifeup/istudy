# iSTUDY｜Dev62／Dev63 ZIP 檔案差異盤點（2026-11-04）

> **來源與範圍**：使用者提供之 Dev62 與 Dev63 **本機 ZIP** 的唯讀 SHA-256 檔案盤點（非 GitHub main 對 Dev63 程式碼比較）。本頁為供 AI 接手的摘要，不是 Ubuntu 編譯或功能測試報告。原始基線名稱：`istudy_1.0.0-dev.62-src.zip` 與 `istudy_dev63-base_dev62PageAtlasLite.zip`；同一檔在上傳環境可能帶有 `(1)` 後綴。

## 實際比較

| ZIP 內項目 | Dev62 A | Dev63 B |
|---|---:|---:|
| 壓縮大小（Claude 報告口徑，MB） | 約 102.05 MB | 約 8.99 MB |
| ZIP 檔案數 | 215 | 237 |
| 共同檔案、SHA-256 相同 | \- | **197** |
| 共同路徑、SHA-256 不同 | \- | **4** |
| 僅 Dev62 | **14** | \- |
| 僅 Dev63 | \- | **36** |

**同路徑但內容不同（必須保留差異並審核功能）**：
- `package.json`
- `packages/ui/src/App.tsx`
- `packages/ui/src/styles.css`
- `server/public/index.php`

**僅 Dev62 的 14 項**全部位於 `dist/`，屬已建置成品、Electron `.deb` 與前端 renderer 資源；未發現 Dev62 獨有的第一方核心原始碼。**僅 Dev63 的 36 項**涉及 Page Atlas Lite 等新增檔案；不能因為僅以 SHA-256 檢查，就斷言新增功能已驗收或不需要舊版編譯資源。

## 容量落差的主要來源

- `dist/istudy-server-app_1.0.0-dev.62_amd64.deb`：約 **92.32 MB**（以十進位 MB 報告；約 88 MiB）。主要是 Electron／Chromium runtime，而非 92 MB 的 iSTUDY 第一方原始碼。
- `dist/server/` 等 renderer 建置產物：可由相應原始碼與工具鏈重建；不可將這些舊成品混入 Dev63 開發 ZIP。
- `deploy/vendor/age-v1.2.1-linux-amd64.tar.gz` 約 5.19 MB：**Dev62／Dev63 都保留**的離線 Agent 必要依賴，不是造成主要差距的獨有項。
- `docs/screenshots/` 約 2.87 MB：可視交接用途另外交付，但這不是可直接刪除的核心程式。

## 證據支持的結論與限制

1. **Dev63 可作「後續原始碼開發的候選基線」**，因 Dev62 獨有項集中於 `dist/`，並非因為 Dev63 ZIP 比較小就判定完整。
2. 本檢查**未編譯、未執行 E2E／Ubuntu 實機驗收**，所以不能稱為正式可部署、可回退或功能全數完成；`AGENTS.md` 與 [Dev63 進度中心](DEV63_STATUS_AND_DECISIONS.md) 的驗收門檻仍適用。
3. 不要把「未發現 Dev62 獨有核心原始碼」等同於「Dev63 的新程式沒有 bug」，尤其 4 個共路徑異動檔與 36 個新增檔仍須按版本規格追蹤。
4. 本盤點只涵蓋上述**兩個 ZIP 快照**；GitHub `main` 目前仍為 Dev62，Dev63 本機整合成果不應被誤稱為已同步的 GitHub 程式。

詳見：[開發／發行分離規範](ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)、[Server App 審查](DEV63_SERVER_APP_PACKAGING_REVIEW_2026-11-04.md)。
