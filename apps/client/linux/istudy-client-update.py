#!/usr/bin/env python3
"""iStudy Client（Ubuntu）自動更新（dev.61）：由 systemd 以 root 執行，學生帳號不需要 sudo。

  istudy-client-update.timer        開機 2 分鐘後、之後每 30 分鐘（各台錯開最多 5 分鐘）：檢查、下載；App 沒在執行時安裝
  istudy-client-update-boot.service 開機、登入畫面出現前：只安裝「已下載並核對過」的新版（不連網、很快）
  istudy-client-update-now.path     App 在沒有人登入時要求更新（App 準備關閉）：等 App 結束後立刻安裝

流程：/etc/istudy/client.json 的 Server → GET <server>/updates/ubuntu/latest.json（版本、檔名、大小、SHA256）
→ 比已安裝的版本新才下載到 /var/cache/istudy-client-update/，核對大小、SHA256、套件名稱與版本
→ App 沒有在執行時，用 apt-get 安裝本機檔案（只用本機已有的相依套件，不連網際網路）；App 執行中就先等（waiting_close）。
狀態寫在 /var/lib/istudy-client-update/status.json（所有人可讀，App 用來顯示「有新版本」提示）。
只接受比目前新的版本；任何一步失敗都不安裝、不影響目前的版本。
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

PKG = "istudy-client"
CONFIG = os.environ.get("ISTUDY_UPDATE_CONFIG", "/etc/istudy/client.json")
STATE_DIR = os.environ.get("ISTUDY_UPDATE_STATE", "/var/lib/istudy-client-update")
CACHE_DIR = os.environ.get("ISTUDY_UPDATE_CACHE", "/var/cache/istudy-client-update")
REQUEST_DIR = os.environ.get("ISTUDY_UPDATE_REQUESTS", "/run/istudy-client-update")
# App 的主程序與子程序（/opt/iStudy/istudy-client、/usr/bin/istudy-client …）；不會誤判本程式（istudy-client-update.py）
APP_PATTERN = os.environ.get("ISTUDY_UPDATE_APP_PATTERN", "(^|/)istudy-client( |$)")
MAX_SIZE = 600 * 1024 * 1024
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-dev\.\d+)?$")
SERVER_RE = re.compile(r"^http://([A-Za-z0-9.\-]+)(?::(\d{1,5}))?/?$")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def say(msg: str) -> None:
    print(msg, flush=True)          # systemd 會記到 journal（journalctl -u istudy-client-update）


def run(cmd: list[str], timeout: int = 60, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def debver(v: str) -> str:
    return v.replace("-dev.", "~dev.")   # dpkg 版本寫法（1.0.0-dev.61 → 1.0.0~dev.61）


def read_server() -> str:
    with open(CONFIG, encoding="utf-8") as f:
        v = json.load(f).get("server")
    m = SERVER_RE.match(v.strip()) if isinstance(v, str) else None
    if not m or (m.group(2) and not 1 <= int(m.group(2)) <= 65535):
        raise ValueError(f"{CONFIG} 的 server 格式錯誤")
    return v.strip().rstrip("/")


def installed_version() -> str | None:
    p = run(["dpkg-query", "-W", "-f=${Version}", PKG])
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def newer(candidate_deb: str, current: str | None) -> bool:
    if not current:
        return False                  # 沒有安裝 Client：不由這裡安裝
    return run(["dpkg", "--compare-versions", candidate_deb, "gt", current]).returncode == 0


def app_running() -> bool:
    return run(["pgrep", "-f", APP_PATTERN]).returncode == 0


def load_status() -> dict:
    try:
        with open(os.path.join(STATE_DIR, "status.json"), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def save_status(st: dict) -> None:
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    tmp = os.path.join(STATE_DIR, ".status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(STATE_DIR, "status.json"))


def fetch_manifest(server: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 區網直連，不走 Proxy
    with opener.open(f"{server}/updates/ubuntu/latest.json", timeout=15) as r:
        m = json.loads(r.read(65536).decode("utf-8"))
    if not isinstance(m, dict):
        raise ValueError("latest.json 格式錯誤")
    v, f, h, n = m.get("version"), m.get("file"), m.get("sha256"), m.get("size")
    if not isinstance(v, str) or not VERSION_RE.match(v):
        raise ValueError("latest.json 的版本格式錯誤")
    if f != f"istudy-client-ubuntu_{v}_amd64.deb":
        raise ValueError("latest.json 的檔名與版本不符")
    if not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h):
        raise ValueError("latest.json 的 SHA256 格式錯誤")
    if not isinstance(n, int) or not 0 < n <= MAX_SIZE:
        raise ValueError("latest.json 的檔案大小不合理")
    return {"version": v, "file": f, "sha256": h, "size": n}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify_deb(path: str, m: dict) -> None:
    if os.path.getsize(path) != m["size"]:
        raise ValueError("下載的檔案大小與 latest.json 不符")
    if sha256_of(path) != m["sha256"]:
        raise ValueError("下載的檔案 SHA256 與 latest.json 不符")
    p = run(["dpkg-deb", "-f", path, "Package", "Version"])
    fields = dict(ln.split(": ", 1) for ln in p.stdout.strip().splitlines() if ": " in ln)
    if p.returncode != 0 or fields.get("Package") != PKG or fields.get("Version") != debver(m["version"]):
        raise ValueError("安裝檔的套件名稱或版本不對")


def download(server: str, m: dict) -> str:
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    dst = os.path.join(CACHE_DIR, m["file"])
    if os.path.isfile(dst):
        try:
            verify_deb(dst, m)
            return dst                 # 已下載並核對過
        except ValueError:
            os.remove(dst)
    for old in glob.glob(os.path.join(CACHE_DIR, "*.deb")) + glob.glob(os.path.join(CACHE_DIR, "*.part")):
        os.remove(old)                 # 只留最新的一份
    part = dst + ".part"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    got = 0
    try:
        with opener.open(f"{server}/updates/ubuntu/{m['file']}", timeout=30) as r, open(part, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                got += len(b)
                if got > m["size"]:
                    raise ValueError("下載的檔案比 latest.json 寫的大")
                f.write(b)
        verify_deb(part, m)
    except BaseException:
        try:
            os.remove(part)            # 沒通過核對的檔案不留下
        except OSError:
            pass
        raise
    os.replace(part, dst)
    return dst


def install(path: str) -> None:
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    p = run(["apt-get", "install", "-y", "--no-install-recommends", "-o", "Dpkg::Options::=--force-confold", path], timeout=900, env=env)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        raise RuntimeError("安裝失敗：" + " / ".join(tail)[:400])


def take_requests() -> list[str]:
    """App 的「立即更新」要求（檔名 request-<uid>-<時間>）；讀完就刪，path 單元才不會一直觸發。"""
    ids = []
    for f in glob.glob(os.path.join(REQUEST_DIR, "request-*")):
        try:
            if not os.path.islink(f):
                with open(f, encoding="utf-8") as fh:
                    rid = fh.read(200).strip()
                if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", rid):
                    ids.append(rid)
        except OSError:
            pass
        try:
            os.remove(f)
        except OSError:
            pass
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", action="store_true", help="開機：只安裝已下載並核對過的新版，不連網")
    ap.add_argument("--request", action="store_true", help="App 要求立即更新：等 App 結束（最多 60 秒）再安裝")
    a = ap.parse_args(argv)
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        say("另一個更新程序正在執行，略過")
        return 0
    req_ids = take_requests() if a.request else []
    st = load_status()
    cur_deb = installed_version()
    st.update({"checked_at": now(), "current": cur_deb, "mode": "boot" if a.boot else "request" if a.request else "timer"})
    if req_ids:
        st["request_id"] = req_ids[-1]
    try:
        if a.boot:
            m = st.get("manifest") if isinstance(st.get("manifest"), dict) else None
            path = os.path.join(CACHE_DIR, m["file"]) if m and isinstance(m.get("file"), str) else None
            if not m or not path or not os.path.isfile(path) or not newer(debver(m["version"]), cur_deb):
                st["state"] = st.get("state") if st.get("state") in ("up_to_date", "installed") else "up_to_date"
                save_status(st)
                return 0
            verify_deb(path, m)
        else:
            server = read_server()
            m = fetch_manifest(server)
            st["available"] = m["version"]
            if not newer(debver(m["version"]), cur_deb):
                st.update(state="up_to_date", error=None, manifest=None)
                save_status(st)
                say(f"已是最新版（{cur_deb}；Server 上是 {m['version']}）")
                return 0
            path = download(server, m)
            st.update(manifest=m, downloaded_at=now())
        if a.request:
            for _ in range(30):
                if not app_running():
                    break
                time.sleep(2)
        if app_running():
            st.update(state="waiting_close", error=None, available=m["version"])
            save_status(st)
            say(f"新版 {m['version']} 已下載並核對；App 執行中，關閉後（或下次開機）安裝")
            return 0
        say(f"安裝 {m['version']}（目前 {cur_deb}）")
        install(path)
        try:
            os.remove(path)
        except OSError:
            pass
        st.update(state="installed", installed_at=now(), current=installed_version(), error=None, manifest=None)
        save_status(st)
        say(f"已安裝 {m['version']}")
        return 0
    except Exception as e:  # noqa: BLE001 — 任何失敗都不安裝；寫進狀態檔與 journal
        st.update(state="failed", error=f"{type(e).__name__}: {e}"[:400])
        save_status(st)
        say(f"更新失敗：{st['error']}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
