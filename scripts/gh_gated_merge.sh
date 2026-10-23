#!/usr/bin/env bash
# CI 通過才 Squash Merge（GitHub Free 沒有分支保護時使用；規則見 docs/GITHUB.md）。
#   scripts/gh_gated_merge.sh <PR 編號>            # 檢查並合併
#   scripts/gh_gated_merge.sh <PR 編號> --check    # 只檢查，不合併
# 需要 gh（REST API）。任何一項無法確認就停止，回傳 1，不合併。
set -euo pipefail
REPO=${GH_REPO:-ilifeup/istudy}
PR=${1:?PR 編號}
MODE=${2:-merge}
REQUIRED=(secrets node server)

say() { echo "$*"; }
stop() { echo "✗ 不合併：$*"; exit 1; }

pr_json() { gh api "repos/$REPO/pulls/$PR"; }
P=$(pr_json)
state=$(jq -r .state <<<"$P"); draft=$(jq -r .draft <<<"$P"); H=$(jq -r .head.sha <<<"$P")
base=$(jq -r .base.ref <<<"$P"); mergeable=$(jq -r .mergeable <<<"$P")
[ "$state" = open ] || stop "PR #$PR 狀態是 $state"
[ "$draft" = false ] || stop "PR #$PR 是 Draft"
[ "$base" = main ] || stop "目標分支是 $base，不是 main"
[ "$mergeable" != false ] || stop "有合併衝突（mergeable=false）"
say "PR #$PR head=$H"

# 1) 這個 Head SHA 的 CI（pull_request 事件）最新一次執行
RUN=$(gh api "repos/$REPO/actions/runs?head_sha=$H&event=pull_request" \
  --jq '[.workflow_runs[] | select(.name=="CI")] | sort_by(.created_at) | last | .id // empty')
[ -n "$RUN" ] || stop "找不到 $H 的 CI 執行"
R=$(gh api "repos/$REPO/actions/runs/$RUN")
[ "$(jq -r .head_sha <<<"$R")" = "$H" ] || stop "CI 執行的 SHA 與 PR Head 不同"
[ "$(jq -r .status <<<"$R")" = completed ] || stop "CI 還在執行（$(jq -r .status <<<"$R")）"
[ "$(jq -r .conclusion <<<"$R")" = success ] || stop "CI 結論是 $(jq -r .conclusion <<<"$R")"
say "CI run $RUN：success（https://github.com/$REPO/actions/runs/$RUN）"

# 2) 必要 job 都存在且 success
JOBS=$(gh api "repos/$REPO/actions/runs/$RUN/jobs")
for j in "${REQUIRED[@]}"; do
  c=$(jq -r --arg n "$j" '[.jobs[] | select(.name==$n)] | last | .conclusion // "missing"' <<<"$JOBS")
  [ "$c" = success ] || stop "必要檢查 $j 是 $c"
  say "  ✓ $j"
done

# 3) 這個 SHA 上的所有 check run 都完成且沒有失敗（含其他 workflow）
bad=$(gh api "repos/$REPO/commits/$H/check-runs?per_page=100" \
  --jq '[.check_runs[] | select(.status!="completed" or (.conclusion|IN("success","neutral")|not)) | "\(.name)=\(.status)/\(.conclusion)"] | join(", ")')
[ -z "$bad" ] || stop "其他檢查未通過：$bad"

[ "$MODE" = --check ] && { say "✓ 可以合併（只檢查）"; exit 0; }

# 4) 合併前再確認 Head SHA 沒變；合併時指定 sha，期間有新 Commit 時 GitHub 會拒絕
H2=$(pr_json | jq -r .head.sha)
[ "$H2" = "$H" ] || stop "檢查期間有新 Commit（$H2），要重新等 CI"
title=$(jq -r .title <<<"$P")
M=$(gh api -X PUT "repos/$REPO/pulls/$PR/merge" -f merge_method=squash -f sha="$H" -f commit_title="$title (#$PR)")
[ "$(jq -r .merged <<<"$M")" = true ] || stop "GitHub 回覆未合併：$(jq -r .message <<<"$M")"
MS=$(jq -r .sha <<<"$M")

# 5) 合併後確認
[ "$(pr_json | jq -r .merged)" = true ] || stop "PR 狀態不是 merged"
MAIN=$(gh api "repos/$REPO/branches/main" --jq .commit.sha)
say "✓ 已 Squash Merge：PR #$PR → main $MS"
[ "$MAIN" = "$MS" ] && say "✓ main 目前 SHA = $MAIN" || say "⚠ main 目前 SHA = $MAIN（合併後又有新的 commit）"
