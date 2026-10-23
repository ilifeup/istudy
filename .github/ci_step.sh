#!/usr/bin/env bash
# 執行一個 CI 步驟；失敗時把最後 150 行輸出寫成 GitHub 註解（annotation）與 Job Summary，
# 讓沒有下載 Log 權限的工具（Claude、ChatGPT 的 GitHub 連線）也能用 API 看到失敗原因。
#   .github/ci_step.sh "步驟名稱" 指令 參數…
set -uo pipefail
name=$1; shift
log=$(mktemp)
"$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  tail_text=$(tail -n 150 "$log")
  enc=${tail_text//'%'/'%25'}; enc=${enc//$'\r'/'%0D'}; enc=${enc//$'\n'/'%0A'}
  echo "::error title=${name}（exit ${rc}）::${enc}"
  { echo "### ✗ ${name}（exit ${rc}）"; echo '```'; echo "$tail_text"; echo '```'; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
fi
rm -f "$log"
exit "$rc"
