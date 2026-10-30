# iStudy 1.0.0-dev.62（MVP）

iStudy 學習系統。這一版是 MVP：**Ubuntu Server 後端＋Server App＋Client App（Win10、Win11、Ubuntu、Win7）**。

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
| `dist/server/` | 已建置好的 Server App 程式檔（打包 .deb 用，不需要 Node） |
| `scripts/package_client.mjs` | Client 安裝檔（NSIS .exe、Ubuntu .deb） |
| `deploy/` | `package_server_app.py`（.deb）、`build_bundle.py`（離線套件）、`install.sh`、polkit 規則 |
| `compose.yaml` | Docker Compose：php／nginx／agent（資料庫是 Ubuntu 主機的 PostgreSQL 18，經 Unix socket 連線） |
| `VERSION.json` | 所有版本號的單一來源 |

## 快速指令

```bash
python3 deploy/package_server_app.py          # Server App .deb
python3 deploy/build_bundle.py --debs <目錄>   # 離線部署套件（需要 Docker 與網路；找不到上一行的 .deb 會停止）
node scripts/build.mjs                        # 重新建置 dist/server（改畫面時；需先 npm install）
npm install --no-audit --no-fund              # 第一次安裝相依套件（需要網路；產生 package-lock.json，之後可用 npm ci）
node scripts/package_client.mjs --channel win10   # Client 安裝檔（win10／win11／win7 在 Windows 上打包；ubuntu 在 Ubuntu 上打包）
```
