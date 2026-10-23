# GitHub 開發流程（ilifeup/istudy）

Repository 維持 Private。合併 main 不等於發布：不會部署正式 Ubuntu Server（10.3.3.100），也不會發布 Client 更新。

## 工作流程（.github/workflows）

| Workflow | 何時執行 | 內容 |
|---|---|---|
| `CI`（ci.yml） | 每個 PR、main 的每次 push、手動 | `secrets`：專案機密檢查＋gitleaks（整個 git 歷史）<br>`node`：TypeScript、建置 Server App 與四個 Client、Client 測試、打包驗證、shell／Python／PHP 語法<br>`server`：PostgreSQL 18、PHP 規則、Migration、Server API 整合測試、Agent 測試（FFmpeg、LibreOffice Writer、age） |
| `Build Client`（build-client.yml） | 手動、推送 `v*` 標籤 | Win10、Win11、Win7、Ubuntu 安裝檔＋latest.yml／blockmap＋SHA256SUMS.txt → Artifact |
| `Build Server`（build-server.yml） | 手動、推送 `v*` 標籤 | Server App .deb、Migration 檢查、patch_dev62.sh 流程測試、SHA256SUMS.txt → Artifact |

**不在 CI 內**（需要實機或 Runner 裝不起來）：畫面測試（apps/tests/e2e.mjs，Xvfb＋Electron）、Excel／PowerPoint 轉 PDF（LibreOffice Calc／Impress）、真實 ASR 模型、
dev.53 回退相容（deploy/tests/rollback_compat_dev53.py，要 dev.53 原始碼）、正式 Server、InterServer、教室 Windows 電腦。

## 看結果（不需要下載 Log）

每個測試步驟由 `.github/ci_step.sh` 執行：
- 成功或失敗都把測試數量寫進該 job 最後一則註解「測試數量」。
- 失敗時把錯誤明細（Python unittest 從第一個 `======` 起，其他取最後 150 行）寫成 error 註解。

```bash
gh api "repos/ilifeup/istudy/actions/runs/<RUN>/jobs" --jq '.jobs[] | "\(.name) \(.conclusion) \(.id)"'
gh api "repos/ilifeup/istudy/check-runs/<JOB_ID>/annotations" --jq '.[] | "\(.title)\n\(.message)"'
```

## 合併規則（Claude 與 ChatGPT 都一樣）

GitHub Free 的 Private Repository **沒有** Ruleset／分支保護（API 回 403「Upgrade to GitHub Pro」），GitHub 不會擋下 CI 失敗的 PR；
以下規則由執行合併的人（AI）自己遵守，`scripts/gh_gated_merge.sh` 照這些規則檢查並合併：

1. 每項修改一個分支、一個非 Draft PR；不直接 push main、不強制推送、不改寫歷史。
2. 合併前確認 PR 最新 Head SHA 的 `CI` 執行（event＝pull_request）已完成且 success，`secrets`、`node`、`server` 三個 job 都是 success，
   這個 SHA 上的其他檢查也都通過。FAIL、PENDING、CANCELLED、SKIPPED、TIMED_OUT、找不到、或 SHA 對不上，一律不合併。
3. 只用 Squash Merge，並指定測試過的 SHA（REST：`PUT /pulls/<PR>/merge`，`merge_method=squash`、`sha=<HEAD>`）；
   合併前再讀一次 Head SHA，期間有新 Commit 就重新等 CI。
4. 合併後確認 PR 是 merged、main 的新 SHA，以及 main 的 `CI`（event＝push）結果。
5. 兩個 AI 同時工作時用不同分支；改到同一個檔案前先同步最新 main。

```bash
scripts/gh_gated_merge.sh 12 --check   # 只檢查
scripts/gh_gated_merge.sh 12           # 檢查通過才 Squash Merge
```

## 通知

GitHub 事件不會喚醒已結束的 Claude 或 ChatGPT 對話。要收到 PR、CI 結果：GitHub 右上角頭像 → Settings → Notifications：
- 「Actions」：勾選 Email（或 Web and Mobile），可選 Only notify for failed workflows。
- Repository 頁面右上角「Watch」→ All Activity（或 Custom：Pull requests、Releases）。
- 手機安裝 GitHub App 即可收到推播。
