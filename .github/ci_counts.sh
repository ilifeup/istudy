#!/usr/bin/env bash
# 把這個 job 每個步驟的測試數量（.github/ci_step.sh 寫的 $RUNNER_TEMP/ci_counts.txt）寫成一則 notice 註解與 Job Summary。
f="${RUNNER_TEMP:-/tmp}/ci_counts.txt"
[ -s "$f" ] || exit 0
{ echo "### 測試數量"; sed 's/^/- /' "$f"; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
python3 -c '
import sys
t = open(sys.argv[1], encoding="utf-8").read().strip()
print("::notice title=測試數量::" + t.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))' "$f"
