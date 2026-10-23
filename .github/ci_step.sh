#!/usr/bin/env bash
# 執行一個 CI 步驟；失敗時把重點輸出寫成 GitHub 註解（annotation）與 Job Summary，
# 讓沒有下載 Log 權限的工具（Claude、ChatGPT 的 GitHub 連線）也能用 API 看到失敗原因。
# 重點輸出：Python unittest 從第一個「======」（錯誤明細）開始，其他取最後 150 行；
# 每則註解最多約 3500 字元，最多 9 則。
#   .github/ci_step.sh "步驟名稱" 指令 參數…
set -uo pipefail
name=$1; shift
log=$(mktemp)
"$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
# 測試數量（Python unittest「Ran N tests／OK」、node --test「# pass／# fail」、「N … passed／通過」）寫進
# $RUNNER_TEMP/ci_counts.txt，job 最後由 .github/ci_counts.sh 寫成一則註解，API 讀得到
summary=$(grep -E '^Ran [0-9]+ tests?|^(OK|FAILED)( \(.*\))?$|^# (tests|pass|fail|skipped) [0-9]+$|[0-9]+ (rule checks passed|通過)' "$log" \
  | tr '\n' ' ' | sed 's/  */ /g; s/ $//')
if [ "$rc" -eq 0 ]; then
  echo "✓ ${name}：${summary:-OK}" >> "${RUNNER_TEMP:-/tmp}/ci_counts.txt"
else
  echo "✗ ${name}（exit ${rc}）：${summary}" >> "${RUNNER_TEMP:-/tmp}/ci_counts.txt"
fi
if [ "$rc" -ne 0 ]; then
  if grep -q '^======' "$log"; then
    detail=$(sed -n '/^======/,$p' "$log")
  else
    detail=$(tail -n 150 "$log")
  fi
  { echo "### ✗ ${name}（exit ${rc}）"; echo '```'; echo "$detail"; echo '```'; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  NAME="$name" RC="$rc" python3 -c '
import os, sys
text = sys.stdin.read()
chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)][:9] or [""]
for n, c in enumerate(chunks, 1):
    enc = c.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print("::error title=%s（exit %s，%d/%d）::%s" % (os.environ["NAME"], os.environ["RC"], n, len(chunks), enc))
' <<< "$detail"
fi
rm -f "$log"
exit "$rc"
