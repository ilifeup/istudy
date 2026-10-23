# iStudy 1.0.0-dev.62 測試結果

日期：2026-10-22。開發環境：
- PostgreSQL 18 測試叢集、PHP 8.3、真實 FFmpeg、sherpa-onnx。
- age v1.2.1（官方靜態執行檔）。
- 本機測試 FTPS 伺服器：真 TLS、自簽 CA，模擬 InterServer。
- Xvfb＋Electron：Client 44.9.0／22.3.27、Server App 44.9.0。

**正式 Ubuntu Server、教室電腦、InterServer 全部 NOT_TESTED**（未部署、沒有帳號）。只用測試產生的檔案，沒有碰正式教材。正式 Ubuntu dev.61 在開發期間沒有被修改。

## 開發環境結果

| 項目 | 結果 |
|---|---|
| P0-A 永久刪除（test_dev62） | **9/9**，涵蓋：<br>· **DEL-01**：新資料夾的 TXT。<br>· **DEL-02**：sticky bit 預檢擋下、不誤報；權限不足（EPERM）的實際狀態驗證。<br>· **DEL-04**：復原失敗如實回報，再試一次可完成。<br>· **DEL-05**：資料庫提交後暫存檔刪不掉＝待清理，重啟後清掉。<br>· **DEL-06**：重啟續做；失敗留下的暫存檔改回。<br>· **DEL-07**：同 ID 舊衍生檔不動；cleanup_staged 不誤刪。<br>· **DEL-03／08**：有字幕影片與 PDF 回歸＋重新掃描。<br>· 缺寫入權限的說明 |
| P0-B 直接播放 MP4 字幕（test_api test_19） | **PASS**，涵蓋：<br>· 時段外自動排入但不執行。<br>· 「立即執行」把同一個工作改成馬上執行，不重複建立。<br>· 產生字幕後 convert_status 仍是 direct。<br>· 失敗後重試建立新工作；辨識中回報進度；尚未可播放回 409。<br>· 轉檔佇列的字幕狀態 |
| 方案 B 加密備份（test_backup62） | **15/15**（BAK-01～13 的開發環境部分）：<br>· **BAK-01**：沒有金鑰不產生任何備份；內容只有 db.sql（含序列）、字幕、設定、install_id、RESTORE.md、Manifest，沒有影片、文件、Outgo、播放檔；Manifest 雜湊相符；本機只有 .age＋.sha256，`sha256sum -c` OK。<br>· **BAK-02**：主機解密驗證；竄改檔案被抓到。<br>· **BAK-03**：恢復密碼太短、兩次不同被擋；金鑰檔權限 0600／0700。age 每次被呼叫的參數與環境變數、所有檔案、資料庫（Log、工作、設定、備份紀錄）、程式輸出都沒有密碼。<br>· **BAK-04**：修改密碼後舊密碼無效；刪掉主機私鑰後用恢復金鑰檔＋新密碼匯入，舊備份還原到測試資料庫成功。<br>· **BAK-05**（本機 FTPS）：public_html 被擋、明文 FTP 被擋；連線測試；下載比對不符時不標示已驗證；暫存名稱上傳再改名；遠端有 .age、.sha256、恢復金鑰、RESTORE.md。<br>· **BAK-06**：3 天內不上傳，超過才上傳最新一份；每天清理一次。<br>· **BAK-07、08**：10～15 天；最後一份可用備份保留；舊 .tar.gz 與 istudy-db-*.sql 不動；遠端只留最新一份已驗證副本；空間不足停止且不刪任何備份；清理寫入 Log。<br>· **BAK-09**：伺服器停掉時上傳失敗、本機不動、有警告；恢復後重試成功。<br>· **BAK-10**：還原測試在 istudy_restore_test，正式（測試案例）資料庫筆數與設定不變，結束後測試資料庫清空。<br>· **BAK-12**：compose 沒有掛 /etc/istudy；Agent 程式不碰私鑰；正式環境的一次性容器指令無網路、唯讀、不留輸出、私鑰經標準輸入。<br>· **BAK-13**：dev.61 寫法自動標為舊格式；舊備份的完整性與還原測試照舊；istudyctl restore 接受舊 .tar.gz；dev.61 清理規則不會碰 .age。<br>· **序列化**：鎖住時 Agent 稍後重試、主機指令回 75。<br>· Agent 重啟不動主機工作，由主機下次收尾 |
| FTPS 用戶端（本機） | AUTH TLS 之後才送帳密；憑證驗證失敗、帳密錯誤、伺服器不支援 TLS 都有白話錯誤；不支援 TLS 時沒有送出密碼 |
| API（test_api） | **19/19**，另含：<br>· 備份清單的新欄位、警告（沒有金鑰、沒有遠端、上傳失敗、舊未加密備份）。<br>· 保留天數只能 10～15（使用記錄）；遠端資訊不回傳密碼。<br>· jobs 一次查多種類型。<br>· **WOL**：管理員新增、修改、刪除、登記、掃描都回 403 super_only，設備沒有被改；Super 可以；管理員仍可查看、檢測 |
| Agent 回歸 | test_agent 22、test_dev61 18、test_istudyctl 9、test_mediaacl 3、test_asr 25（3 項需模型的另外跑）、test_mailer 8、test_office 9、test_uem 20、test_qsv_check 3：全部 OK |
| 資料庫 migration | **5/5**：0011 由全新安裝與 dev.53 升級都會套用；既有備份紀錄不變並標為舊格式；新工作類型可寫入；format 只能 legacy／age |
| PHP 規則 | 75 項 |
| Client 單元測試 | 連線偵測、更新、打包 24/24；Ubuntu 更新服務 10/10 |
| 部署腳本 patch_dev62.sh 流程測試 | **23/23**：<br>· 只接受 dev.61；Super≠1、基底不對、沒有回退安裝檔、age 壓縮檔被換過都停止且不改任何東西。<br>· 建映像不連網、內容含 age 壓縮檔與 0011；istudy-backup.timer 由新版 istudyctl 寫入並啟用；順序正確；.env 只改版本。<br>· 失敗自動退回；rollback 執行中拒絕、移除排程；加密備份、舊備份、金鑰都不刪。<br>· 從不刪除舊映像與回退檔；退回後可再更新 |
| Agent 映像的 age 安裝指令 | 把 Dockerfile 那一段在主機暫存資料夾照樣執行：SHA-256 OK、`age --version`＝v1.2.1、暫存檔清除；完整版與增量版 Dockerfile 的這一段完全相同 |
| 畫面（Electron） | 見下方「畫面測試」 |
| TypeScript | tsc 無錯誤 |

## 畫面測試

Xvfb＋Electron，連到開發環境的 PHP API、Agent、PostgreSQL 18、本機 FTPS 伺服器；截圖逐張看過。

| 範圍 | 結果 |
|---|---|
| 完整畫面測試（Client win10、Client win7、Server App） | **190/190**，其中 dev.62 新增：<br>· **登入頁眼睛按鈕**（三個 App）：按一下顯示明碼、再按一下隱藏，按鈕有「顯示／隱藏」文字。<br>· **登入頁喚醒清單**：只列已啟用的電腦，並說明「另有 N 台已登記但未啟用喚醒」。<br>· **播放器**：進入播放頁就開始緩衝（preload=auto，測試影片已緩衝 3 秒）；倍速、學習狀態選單在預覽頁與全螢幕都有對比配色；倍速選項 0.5x～3x（沒有「1.x」）；全螢幕開 Tag 視窗，標題、說明、按鈕的文字對比 4.4～14.4（修正前約 1）。<br>· **按 X**：Server 端已登出、畫面回到登入頁、視窗縮到系統匣；再開啟是登入畫面。<br>· **WOL**：管理員只看到喚醒與狀態（編輯類按鈕 0 個）；Super 對未啟用的電腦按「啟用」後，登入畫面的喚醒清單就有這台。<br>· **備份頁**：方案 B 範圍說明；沒有恢復密碼時「建立備份」不能按；設定恢復密碼（兩次不同被擋）；建立 .tar.gz.age 並解密驗證；設定 FTPS 遠端、測試連線、上傳＋下載比對「✓ 遠端已驗證」；保留天數 10～15；恢復密碼與 FTP 密碼沒有出現在 Server App 資料夾、備份資料夾、系統 Log、備份 API。<br>· **永久刪除**：逐項列出每個檔案的實際結果 |
| 安裝 .deb 後的 Server App（dpkg -i 後從 /opt 啟動） | **5/5** |

Client 是以開發環境的 Electron 執行畫面測試，不是 Windows 安裝檔；win7 用 Electron 22.3.27（與 Win7 安裝檔相同）。

## 這次測試中找到並修正的問題

- 測試用 FTPS 伺服器：Linux 上關閉監聽 socket 不會喚醒另一個執行緒的 accept，「停止」後仍接受連線，改成先 shutdown（只影響測試工具）。
- 主機手動指令遇到備份鎖會等 2 分鐘：保留這個行為（Agent 正在建立備份時），測試用環境變數縮短。
- Python 3.13 預設嚴格檢查憑證：測試 CA 補上 keyUsage／AKID（只影響測試憑證；正式環境用系統 CA）。
- 登入頁未啟用電腦的提示原本可能在畫面顯示「0」（JSX 數字短路），改成明確比較。
- 畫面測試抓到：Agent 在沒有主機設定檔（/host-config）時仍會建立「缺 VERSION.json」的備份，解密驗證才失敗。改成 Agent 缺任何必要設定檔就不建立備份並說明缺哪個檔；所有測試環境補上主機設定資料夾。
- 截圖檢查抓到：倍速選單 1 倍顯示成「1.x」（dev.61 起就有），改成 0.5x／1x／1.25x…，並加入畫面測試。
- 複查「Tag」配色時抓到：全螢幕時開的共用 Tag 視窗會繼承全螢幕的白字，淺色底上幾乎看不到（對比約 1）。視窗改成一律一般配色，全螢幕的深色按鈕只套用在播放器控制列；加入畫面測試（計算文字對比）。
- 主機指令成功訊息前面自帶 ✓，畫面又加一次變成「✓ ✓」，已移除重複。
- dev.62 的永久刪除結果框會停留顯示每一項結果，畫面測試改成逐項確認後按「關閉」。

## NOT_TESTED（需要實機）

- **Ubuntu Server 部署**：patch_dev62.sh 的 check、apply、rollback（真的 docker build、compose、dpkg、systemctl）。開發環境沒有 dev.61 的 Agent 基底映像，無法實際建出 dev.62 映像。
- **InterServer 實際連線**：沒有帳號；協定、主機、連接埠需你確認（這版實作 FTPS 明示 TLS）。
- **Server App → pkexec → istudyctl**：開發環境以同一支 istudyctl（測試模式）經標準輸入驗證；polkit 視窗與 pkexec 實際傳遞標準輸入要在 Server 上確認。
- **systemd 排程實際執行**：istudy-backup.timer 每小時觸發。
- **DEPLOY_DEV62.md 第 4 節全部實機項目**：永久刪除、ASR、教材目錄、備份、部署、Client、WOL。
- **Client 安裝檔與自動更新**：Windows 要在 Windows 上打包；Ubuntu .deb 要連網下載打包工具。
- **Client 實機**：按 X 登出、播放開頭是否還卡、下拉選單、登入頁喚醒清單、WOL 管理（管理員）。
