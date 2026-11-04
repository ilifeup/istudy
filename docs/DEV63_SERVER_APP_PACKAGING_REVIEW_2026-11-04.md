# iSTUDY｜Dev63 Server App（.deb）及精簡交付審查摘要（2026-11-04）

> **來源**：Claude 提供之 `iSTUDY_Dev63_打包精簡與ServerApp審查.md`，以唯讀方式分析 Dev62 原始碼、`.deb`、Electron main process、建置與部署腳本；下列「已確認」是**Claude 報告有檔案證據**，不是本次在 Ubuntu 重跑。未修改程式或 CI。

## 核心結論：正式採用 C 混合方案

- **原始碼 ZIP**：只含重建所需第一方程式、設定、測試及必要離線資源；**不含 `.deb`、`dist/`、`node_modules/`**。本次 Dev63 ZIP 約 8.99 MB，若另交付文件截圖可約 6.1 MB；隨程式增長不保證固定 6–9 MB。
- **正式發行成品獨立交付**：`istudy-server-app_<version>_amd64.deb` 與 Client installers 另建置、另發行，包含 SHA-256、版本、來源 Commit／快照及必要的安裝／回退說明。
- **由 Ubuntu 優先完成最後完整編譯、打包和實機驗收**；GitHub Actions 仍可在明確發行需要時建置 artifact 作為備援。不因日常交接而在 AI 沙盒反覆下載 Electron／Chromium。
- **不取消 Server App**：只從**原始碼 ZIP** 移除已建置安裝包，不得刪 `apps/server/` 等原始碼、部署腳本或安全邊界。

## 實際 .deb 職責（不是後端）

- 套件 `istudy-server-app` 1.0.0~dev.62，amd64，壓縮約 92.32 MB，安裝後約 286 MiB；Electron 44.9.0。
- 內含約 2.5 MB iSTUDY renderer／管理程式，其餘主要為 Electron／Chromium 執行環境及資源。
- 裝到 `/opt/istudy-server-app/`，提供 Ubuntu 桌面入口、AppArmor 與 sandbox 設定。**不包含** Docker 後端、PostgreSQL、PHP API、Agent、資料庫、systemd 服務或 `istudyctl`。
- `apps/server/electron/main.ts` 固定連本機 `127.0.0.1:4081`，透過 `pkexec` 呼叫受控 `istudyctl`；本機管理 API 受 `compose.yaml`、Nginx、`require_local_super()` 保護，**不對 LAN 暴露**。
- `deploy/package_server_app.py` 取 Electron 官方 Linux x64 套件（支援 `--electron-zip` 離線提供、固定 SHA-256 校驗）與 `node scripts/build.mjs --app server` 的 `dist/server`，生成 `.deb`。Claude 報告引用 2026-10-23 的 GitHub Actions Build Server 成功記錄；本次未複驗該 run。

## 功能重疊判斷

| 功能 | 可以不用 GUI 完成的部分 | 目前仍依賴 Server App 的部分 |
|---|---|---|
| 初始部署 | `sudo ./install.sh --cli`、`sudo istudyctl bootstrap` | GUI 導引與離線 USB 操作便利性 |
| 首次設定精靈（資料夾、排程、編碼、字幕、帳號群組、備份） | 部分 CLI／API | **完整六步精靈無對等 Web／CLI 版本** |
| 教材、轉檔、字幕 | Agent 的掃描／轉檔／ASR 排程照常執行 | 目錄、佇列、文件、字幕、原檔刪除與狀態管理畫面 |
| 主機維護、備份、UEM | `istudyctl doctor`、`hostcheck`、`backup-*`、`uem` 等 | 備份清單、Log／UEM 報告的圖形檢視及主機維護按鈕 |

**安全提醒**：`/api/admin/*` 僅 localhost；Nginx 沒有提供 Browser 版 Server 管理頁。**不可**為手機 Chrome 直接開放該 API 至 LAN／Cloudflare Tunnel；另設計管理驗證、授權、稽核與受控主機操作流程才可實作。

## 改為精簡 ZIP 後的實際限制

- `deploy/patch_dev62.sh` 目前固定從 `dist/` 找 `istudy-server-app_<NEW>_amd64.deb`；**正式更新前需先產生相符的新版本 `.deb`**，或另行核准修改部署流程。原始碼 ZIP 的精簡與部署要求並不矛盾。
- `deploy/vendor/age-v1.2.1-linux-amd64.tar.gz`（約 5.19 MB）為離線 Agent 映像建置必要例外，不能誤刪。
- 原 ZIP 沒有 `package-lock.json`，間接相依沒有固定；建議受控開發環境產生並納入原始碼，經驗證後改用 `npm ci`。
- 此次只證明**重建路徑可用、沒有發現必須從舊 .deb 取回的唯一核心原始碼**；不保證新 Dev63 已編譯、功能完整或正式驗收通過。

詳見 [正式打包規範](ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md) 及 [Dev62／Dev63 差異盤點](DEV62_DEV63_FILE_DIFF_AUDIT_2026-11-04.md)。
