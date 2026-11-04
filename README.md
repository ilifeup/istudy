# iStudy 1.0.0-dev.62（MVP）

心賞氣悅・無相氣學 助教複習系統。這一版是 MVP：**Ubuntu Server 後端＋Server App＋Client App（Win10、Win11、Ubuntu、Win7）**。

> **開發／打包規範（2026-11-04）**：正式採用 [C 混合方案：精簡原始碼 ZIP 與正式安裝包分離](docs/ISTUDY_DEV_PACKAGING_POLICY_2026-11-04.md)。Dev63 開發 ZIP 不含 `.deb`／`dist/`／`node_modules/`；Ubuntu 整合／發行時再建置 Server App。詳見 [Dev62／Dev63 檔案差異盤點](docs/DEV62_DEV63_FILE_DIFF_AUDIT_2026-11-04.md) 及 [Electron Server App 架構審查](docs/DEV63_SERVER_APP_PACKAGING_REVIEW_2026-11-04.md)。**GitHub main 仍為 Dev62，Dev63 本機候選不得視為已合併。**

- 部署步驟：[docs/DEPLOY.md](docs/DEPLOY.md)
- 開發狀態、測試結果、與計畫書的差異：[docs/STATUS.md](docs/STATUS.md)
- **dev.62 更新（Server 目前是 dev.61，從這裡開始）**：[docs/DEPLOY_DEV62.md](docs/DEPLOY_DEV62.md)；測試結果：[docs/TEST_RESULTS_DEV62.md](docs/TEST_RESULTS_DEV62.md)
- dev.53 全新安裝與驗收：[docs/DEPLOY_DEV53.md](docs/DEPLOY_DEV53.md)；測試結果：[docs/TEST_RESULTS_DEV53.md](docs/TEST_RESULTS_DEV53.md)
- 主機 PostgreSQL 18 的初始化、權限、備份還原：[docs/DEPLOY_PG18.md](docs/DEPLOY_PG18.md)

## 目錄

| 路徑 | 內容 |
|---|---|
| `server/` | PHP 8.3 API、Nginx 設定、`sql/schema.sql`、CLI（migrate、create-super）、測試 |
| `agent/` | Python Agent（轉檔、驗證、字幕、Office 轉 PDF、掃描、WOL、診斷、備份）與主機工具 `istudyctl.py` |
| `packages/ui/` | 畫面（React）：Client 全部功能，Server App 共用 |
| `packages/shell/` | Electron 主程序共用：安全設定、連線檢查、登入憑證注入 |
| `apps/server/` | Server App（Super 專用）：初始部署、首次設定精靈、Server 管理 |
| `apps/client/` | Client App 主程序（四個版別同一份程式；Win7 用 Electron 22） |
| `dist/server/` | 可重建的 Server App 建置產物；**不放入日常原始碼 ZIP**，正式 Ubuntu 打包時才重建（舊版 README 描述改依 C 方案） |
| `scripts/package_client.mjs` | Client 安裝檔（NSIS .exe、Ubuntu .deb） |
| `deploy/` | `package_server_app.py`（.deb）、`build_bundle.py`（離線套件）、`install.sh`、polkit 規則 |
| `compose.yaml` | Docker Compose：php／nginx／agent（資料庫是 Ubuntu 主機的 PostgreSQL 18，經 Unix socket 連線） |
| `VERSION.json` | 所有版本號的單一來源 |

## 快速指令

> 以下打包命令僅供正式整合／發行使用，不應在日常 AI 雲端交接時反覆執行；完整步驟見上方 C 混合方案。執行 `package_server_app.py` 前應先有與來源一致的 `dist/server` 建置結果。

```bash
python3 deploy/package_server_app.py          # Server App .deb
python3 deploy/build_bundle.py --debs <目錄>   # 離線部署套件（需要 Docker 與網路；找不到上一行的 .deb 會停止）
node scripts/build.mjs                        # 重新建置 dist/server（改畫面時；需先 npm install）
npm install --no-audit --no-fund              # 第一次安裝相依套件（需要網路；產生 package-lock.json，之後可用 npm ci）
node scripts/package_client.mjs --channel win10   # Client 安裝檔（win10／win11／win7 在 Windows 上打包；ubuntu 在 Ubuntu 上打包）
```
