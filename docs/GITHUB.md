# GitHub 開發流程（ilifeup/istudy）

Repository 維持 Private。合併 main 不等於發布：不會部署正式 Ubuntu Server（10.3.3.100），也不會發布 Client 更新。

## 工作流程（.github/workflows）

| Workflow | 何時執行 | 內容 |
|---|---|---|
| `CI`（ci.yml） | 每個 PR、main 的每次 push、手動 | `secrets`：專案機密檢查＋gitleaks（整個 git 歷史）<br>`node`：TypeScript、建置 Server App 與四個 Client、Client 測試、打包驗證、shell／Python／PHP 語法<br>`server`：PostgreSQL 18、PHP 規則、Migration、Server API 整合測試、Agent 測試（FFmpeg、LibreOffice、age） |
| `Build Client`（build-client.yml） | 手動、推送 `v*` 標籤 | Win10、Win11、Win7、Ubuntu 安裝檔＋latest.yml／blockmap＋SHA256SUMS.txt → Artifact |
| `Build Server`（build-server.yml） | 手動、推送 `v*` 標籤 | Server App .deb、Migration 檢查、patch_dev62.sh 流程測試、SHA256SUMS.txt → Artifact |

需要正式 Server、InterServer、教室 Windows 電腦的驗收不在 CI 內（見各版 DEPLOY_DEV*.md 的實機驗收）。

## 合併規則（Claude 與 ChatGPT 都一樣）

GitHub Free 的 Private Repository **沒有** Ruleset／分支保護，GitHub 不會自動擋下 CI 失敗的 PR；以下規則由執行合併的人（AI）自己遵守：

1. 每項修改一個分支、一個非 Draft PR；不直接 push main、不強制推送、不改寫歷史。
2. 合併前確認 PR 最新 Head SHA 的 `CI` 執行（event＝pull_request）三個 job：`secrets`、`node`、`server` 都是 **success**。
   FAIL、PENDING、CANCELLED、SKIPPED、TIMED_OUT、找不到、或 SHA 對不上，一律不合併。
3. 只能用 Squash Merge，並指定測試過的 SHA（`gh pr merge <PR> --squash --match-head-commit <SHA>`）；期間有新 Commit 就重新等 CI。
4. 合併後確認 PR 是 MERGED、main 的新 SHA，以及 main 的 `CI`（event＝push）結果。
5. 兩個 AI 同時工作時用不同分支；改到同一個檔案前先同步最新 main。

```bash
PR=123
HEAD=$(gh pr view $PR --json headRefOid -q .headRefOid)
gh run list --workflow CI --event pull_request --commit "$HEAD" --json databaseId,status,conclusion
gh pr checks $PR          # 三個 job 都要 pass
gh pr merge $PR --squash --match-head-commit "$HEAD"
```

## 通知

GitHub 事件不會喚醒已結束的 Claude 或 ChatGPT 對話。要收到 PR、CI 結果：GitHub → Settings → Notifications →
「Actions」勾選 Only notify for failed workflows（或全部）、「Watching」設定 Repository 為 All Activity；手機可裝 GitHub App 收推播。
