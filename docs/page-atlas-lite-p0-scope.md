# iSTUDY Page Atlas Lite — P0 範圍與驗收

> 2026-11-03 決策：Lite 為正式交付；PR #5 為過度開發研究保留，不得整體合併。

## 必要功能
1. 永久頁碼、頁面名稱；既有索引以 34 頁為起點，非永久上限。
2. 頁面清單、搜尋、基本篩選。
3. 頁面對應原始碼檔案、元件位置。
4. API 與 DB 簡易參照；未驗證關聯顯示「待確認」，禁止猜測。
5. 上一頁／下一頁及路由開啟。
6. 一鍵複製頁面摘要供 AI 分析。

## 安全與架構
- 使用現有 React + PHP API + PostgreSQL 架構；不新增資料庫表、不修改正式 DB。
- 私有索引僅限 Super 權限由後端提供；不打包進公開 Client/Renderer bundle。
- 保留必要的權限驗證、私有索引隔離、錯誤重試與安全驗收。

## 非 P0（保留研究成果，不強制執行）
- 深度 Call Graph、PHP/SQL 跨檔推導、IPC/CLI/FILE 追蹤、圖形依賴分析、自動截圖、複雜 Preview。
- 不從 PR #5 整體 cherry-pick；僅選取 Lite 所需的變更。

## 交付驗收
- 整合 Lite UI、34 頁索引、PHP Super 私有 API 與必要測試。
- TypeScript typecheck、Client/Server Renderer build、Bundle 私有資料防洩漏檢查、Lite 測試與 Preview 隔離測試通過。
- Ubuntu 實機確認頁面清單、搜尋、導航、複製、權限與失敗重試。
- CI 綠燈不等於正式結案；Ubuntu 驗收後才由專案負責人決定是否合併。
- PR 初期 Draft；禁止自動合併。

## 研究保留
PR #5：https://github.com/ilifeup/istudy/pull/5
待 Lite PR 合併 main、確認研究成果保留後，關閉 PR #5，不合併。
