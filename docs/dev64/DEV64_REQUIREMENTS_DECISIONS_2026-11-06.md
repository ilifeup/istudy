# iSTUDY ZERO-OPS Dev64｜開發需求與已確認決議

> 狀態：2026-11-06 已確認決議。此文件記錄開發需求，不代表已完成實作或實機驗收。
> 計畫書基準：iSTUDY ZERO-OPS Dev64 Native Rebuild v4.1 完整圖解計畫書（HTML）。如與歷史 Dev62/Dev63 設計衝突，以本文件最新決議為準；190 項需求逐項核對，未確認的差異須提出供使用者決定。

## 1. 專案基線
- 全新 Dev64，版本 `1.0.0-dev.64`。目前只有一台 Win10 Dev63 Client；不要求舊 Client 相容，也不必延續舊更新通道。
- Dev63 為測試環境，沒有要遷移的正式資料；全新 PostgreSQL 資料庫，不做舊資料遷移。
- 不開發 Dev64 回退至 Dev63 的專用流程；新版本升級仍須具備失敗停止、健康檢查與必要備份。
- 首次 Server App 安裝允許連網取得依賴，日常系統使用限制 LAN。
- iSTUDY 優先採 Ubuntu 原生 Nginx、PHP-FPM、PostgreSQL 18、Python venv/systemd、ZFS /Media；其他專案 Docker 不受影響。
- 優先重用 Dev63 已驗證 PHP、Python、Electron 程式，不為形式重寫 Controller/Service/Repository 或新增多層 Job 框架。
- Web Core **不獨立更新**：Electron Shell 與 UI 維持完整安裝包更新；不額外引入遠端 Web Core 載入信任邊界。
- Worker 優先維持既有單一常駐服務和既有多工作通道；QoE 資源採樣不得阻塞 WOL、心跳、備份等工作。

## 2. P0｜教材預讀快取（必須開發）
- 先查現有播放器的 HTTP Range、緩衝與快取；不重複實作。
- 優先當前影片後續片段；課程順序明確時可評估下一教材預讀。
- 正常播放優先於預讀；限制預讀容量、頻寬、並行量；具清理、取消、錯誤退化能力。
- 七台 PC 各播放不同三小時影片、倍速及跳轉情境均須實測；預讀不得造成共同卡頓。

## 3. P0｜QoE 七台同步播放診斷（必須開發）
- 七台 Client 各觀看不同的三小時影片；可同時 3× 倍速、seek、預讀。
- 記錄 Session/Client/影片識別、播放起始、倍速變更、seek 起訖、卡頓起訖與累積時長、錯誤、完成率、緩衝、掉影格與可取得的解碼資訊。
- Client 端事件採集、短期斷線緩衝及批次上傳；可先用每 10 秒批次作可調初值。
- Server 端每 5 秒採集必要 CPU、網路、ZFS I/O 等指標作可調初值；逾時與故障隔離，不新增專用 QoE Worker。
- 現有 PHP API + PostgreSQL + Server App 提供跨 Client 時間軸、共同卡頓偵測及播放/預讀/Server 負載關聯。
- 校正 Client 與 Server 時差，使用單調時鐘計算持續時間，補送需去重。
- 報告須區分「時間相關」「有共同瓶頸證據」「因果未確認」；不得僅因 A 切換 3× 後 B 卡頓就斷定因果。
- 不引入 Prometheus、Grafana、Redis、額外 Python QoE Worker 或獨立監控平台。

## 4. P0｜USB 匯入
- Ubuntu Server App 選取**已掛載 USB**中的來源資料夾，完整保留該資料夾名稱及全部巢狀內容，複製到固定目標 `/Media/`。
- Electron UI + Main Process(Node.js) 呼叫 Ubuntu `rsync`；不以 PHP-FPM 長時間複製、不新增 Python USB Agent 或專用資料表。
- UI 提供進度、錯誤、取消、結果；來源不刪除、不壓縮、不轉碼。
- 同名目標資料夾存在時先顯示可能選錯目錄警告，**等待五秒後**才開放「確認覆蓋既有資料」與「取消」；預設取消，未確認不得覆蓋。
- 先暫存複製及完整性驗證，成功後切換；切換失敗不得破壞原目標。
- 舊目錄保護區不設七天或容量比例自動清除，僅 Super 明確確認後可清理；暫存與保護區不得被教材掃描收錄。
- 必須驗證掛載點、來源/目標路徑、symlink/特殊檔案、權限、空間不足、USB 拔除、取消及斷電後狀態。

## 5. P0｜WOL、Email、API 安全
- WOL 以 Dev62 已實作功能為基準；Super 可管理、Admin 不可管理，公開喚醒只依核准範圍開放。
- Email：Worker 已將信交給郵件服務但尚未回寫 DB 就中斷時，不得在狀態不明下自動重寄；需測試及人工確認流程。
- Nginx + PHP-FPM 原生部署需驗證本機管理 API 的信任邊界、401/403/200、FastCGI 參數與權限；UI 可觸發的 root 指令必須明確白名單。
- 資料庫測試破壞性操作必須先驗證測試庫名稱、PG18、連線位置/叢集身分，做正反向測試；不可連正式資料庫。

## 6. G0/G1/G2/G3 開發與驗收
- v7.8 31 Tab、190 項需求逐項對照 Dev63 程式：需求 ID、狀態、原始碼位置、重用/新開發、優先級、驗收方式。
- 證據分四層：原始碼存在且邏輯正確、模組測試、需求專屬測試、實機端到端驗收。模組 PASS 不等於需求 PASS。
- 目前 G0/G1 為隔離環境基線；PG16 migration PASS 不可冒充 PG18 實機 PASS；PHP、Agent 失敗及 SKIP 必須逐項追查，不刪安全斷言。
- G1 主機先唯讀盤點；未另行授權不得 apt 安裝、修改主機服務、資料庫、Dev63 容器、EachCore 或 `/Media`。
- G2/G3 允許隔離工作樹集中開發及回歸測試；未經另外授權不得 Push、開 PR、合併或部署。
- 腳本及驗收須即時顯示 RUNNING/PASS/FAIL/SKIP、百分比、耗時、逾時並持續輸出 Markdown 報告。
- 避免過度開發；已確定決議不反覆詢問；不要重複產生 HTML 計畫書。開發中如發現規格矛盾，提出最小方案與影響供使用者裁決。

## 7. 尚待實機確認
PHP 8.5、Python 3.14/sherpa-onnx/OpenCC、PG18、VA-API、LibreOffice、rsync/ZFS/USB 權限、4090/4091 連接埠及原生 Nginx/FPM 隔離。未測不可標記 PASS。
