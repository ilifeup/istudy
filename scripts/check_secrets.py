#!/usr/bin/env python3
"""上傳 GitHub 前與 CI 的機密檢查（只用標準函式庫）。

擋下：私鑰、age 私鑰、各家 Token／API Key、JWT、正式 .env、資料庫備份、加密備份、教材媒體檔。
測試檔裡刻意寫的假密碼（例如 SuperSecret123）不是機密；真正的機密格式（私鑰、Token）在任何檔案都擋。

  python3 scripts/check_secrets.py            # 檢查 git 追蹤的檔案（沒有 git 時檢查整個資料夾）
  python3 scripts/check_secrets.py --all      # 檢查資料夾內所有檔案（排除 node_modules、dist、.git）
找到問題時列出「檔案:行號 類型」並回傳 1；內容只顯示前後各 4 個字元。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__"}

# 檔名：不得進版本庫
BAD_NAMES = [
    (re.compile(r"(^|/)\.env$"), "正式 .env（只允許 .env.example）"),
    (re.compile(r"(^|/)\.env\.(?!example$)[^/]+$"), ".env 變體"),
    (re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$", re.I), "金鑰檔"),
    (re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$"), "SSH 金鑰"),
    (re.compile(r"\.age$"), "加密備份"),
    (re.compile(r"(^|/)istudy-backup-[^/]*\.tar\.gz$"), "備份壓縮檔"),
    (re.compile(r"(^|/)istudy-db-[^/]*\.sql(\.gz)?$"), "資料庫備份"),
    (re.compile(r"\.(dump|bak|sqlite3?)$", re.I), "資料庫檔"),
    (re.compile(r"\.(mp4|mts|m2ts|mov|avi|mkv|wmv|mp3|wav|m4a|flac)$", re.I), "教材媒體檔"),
]

# 內容：真正的機密格式
PATTERNS = [
    (re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY( BLOCK)?-----"), "私鑰"),
    (re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{50,}"), "age 私鑰"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"), "GitHub Token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"), "GitHub Fine-grained Token"),
    (re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{32,}\b"), "OpenAI／Anthropic API Key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack Token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API Key"),
    (re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_-]{33}\b"), "Telegram Bot Token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"), "SendGrid Key"),
    (re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|ftps?|sftp)://[^\s:/@]+:[^\s@/]{6,}@(?!127\.0\.0\.1|localhost)"), "連線字串含密碼"),
]

TEXT_LIMIT = 2 * 1024 * 1024


def files(all_files: bool) -> list[str]:
    if not all_files:
        try:
            out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                 capture_output=True, check=True).stdout
            return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p})
        except (OSError, subprocess.CalledProcessError):
            pass
    res = []
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            res.append(os.path.relpath(os.path.join(d, n), ROOT).replace(os.sep, "/"))
    return sorted(res)


def mask(s: str) -> str:
    return s[:4] + "…" + s[-4:] if len(s) > 10 else "…"


def main() -> int:
    problems = []
    checked = 0
    for rel in files("--all" in sys.argv):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for rx, what in BAD_NAMES:
            if rx.search(rel):
                problems.append(f"{rel} {what}")
        if os.path.getsize(path) > TEXT_LIMIT:
            continue
        with open(path, "rb") as f:
            raw = f.read()
        if b"\0" in raw[:8192]:
            continue  # 二進位檔（例如 deploy/vendor 的 age 官方壓縮檔，SHA-256 另外在 Dockerfile 核對）
        checked += 1
        text = raw.decode("utf-8", "replace")
        for no, line in enumerate(text.splitlines(), 1):
            for rx, what in PATTERNS:
                m = rx.search(line)
                if m:
                    problems.append(f"{rel}:{no} {what}（{mask(m.group(0))}）")
    for p in problems:
        print("✗ " + p)
    print(f"{'✗' if problems else '✓'} 機密檢查：{checked} 個文字檔，{len(problems)} 個問題")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
