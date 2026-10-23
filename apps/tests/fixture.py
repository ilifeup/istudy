"""畫面測試用的 Server：建立資料庫與教材、Agent 處理全部工作、帳號與群組，然後以 PHP 內建伺服器提供兩個埠。

  DB_HOST=127.0.0.1 DB_USER=postgres python3 apps/tests/fixture.py --state /tmp/e2e.json
  （Ctrl+C 或 SIGTERM 結束；--keep-media 保留教材目錄）

LAN 埠模擬 Nginx 的 :4080；管理埠模擬 127.0.0.1:4081（ISTUDY_LOCAL_ADMIN=1）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "agent", "tests"))

from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import TZ, Paths  # noqa: E402
from worker import main as wmain  # noqa: E402
from worker.context import Ctx  # noqa: E402

import make_media  # noqa: E402

DB = "istudy_e2e"
INSTALL_ID = "e2ee2ee2ee2ee2ee2ee2ee2ee2ee2e01"
ACCOUNTS = {
    "super": ("superadmin", "SuperSecret123"),
    "admin": ("teacher01", "AdminPass123"),
    "lect": ("lect01", "LectPass123"),
    "user": ("stu01", "StudentPass1"),
}


def call(port, method, path, body=None, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--lan", default="18280")
    ap.add_argument("--admin", default="18281")
    ap.add_argument("--keep-media", action="store_true")
    ap.add_argument("--setup-done", action="store_true", help="把首次設定標為完成（測試一般畫面時）")
    args = ap.parse_args()

    media = tempfile.mkdtemp(prefix="istudy-e2e-media-")
    make_media.build(media)
    make_media.office_demo(media)   # dev.60：Excel／PowerPoint 轉 PDF 與 16:9 頁面顯示
    # dev.61：Outgo（錄音轉文字，不是教材）與一份只給「永久刪除」測試用的教材
    og = os.path.join(media, "Outgo")
    os.makedirs(os.path.join(og, "第一期"))
    make_media.ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=3", os.path.join(og, "第一期", "上課錄音.mp3")])
    make_media.ff(["-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-c:a", "wmav2", os.path.join(og, "討論.wma")])
    open(os.path.join(og, ".隱藏.mp3"), "wb").close()
    os.symlink(os.path.join(media, "基礎班"), os.path.join(og, "連結"))
    make_media.pdf(os.path.join(media, "砭石", "e2e-丟棄講義.pdf"))
    with open(os.path.join(media, ".istudy-media"), "w") as f:
        f.write(INSTALL_ID)
    a = dbmod.connect(dbmod.conninfo_from_env({**os.environ, "DB_NAME": "postgres"}))
    a.exec_raw(f"DROP DATABASE IF EXISTS {DB}")
    a.exec_raw(f"CREATE DATABASE {DB}")
    # dev.62：主機的隔離還原測試以 SET ROLE istudy 還原（正式環境由 bootstrap 建立這個角色）
    a.exec_raw("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'istudy') THEN CREATE ROLE istudy NOLOGIN; END IF; END $$")
    a.close()
    env = {**os.environ, "DB_NAME": DB, "MEDIA_ROOT": media, "ISTUDY_VERSION_FILE": os.path.join(ROOT, "VERSION.json"),
           "ISTUDY_SENDFILE": "php", "ISTUDY_ENCODER": "cpu", "ISTUDY_X264_PRESET": "ultrafast", "ISTUDY_ASR_ENGINE": "fake",
           "ISTUDY_EMAIL_FAKE": "1",  # dev.57：Email 報告不連網，只產生信件內容
           "ISTUDY_RESERVE_GB": "0", "ISTUDY_BACKUP_REQUIRE_MOUNT": "0", "PHP_CLI_SERVER_WORKERS": "6",
           # dev.62：加密備份寫到暫存資料夾（Server App 的主機指令也用同一個）；age 由 ISTUDY_AGE_BIN 指定
           "ISTUDY_BACKUP_DIR": tempfile.mkdtemp(prefix="istudy-e2e-backup-"), "ISTUDY_AGE_BIN": os.environ.get("ISTUDY_AGE_BIN", "age"),
           "ISTUDY_BACKUP_MIN_FREE_MB": "1", "ISTUDY_HOST_CONFIG_DIR": make_media.host_config(tempfile.mkdtemp(prefix="istudy-e2e-hostcfg-"))}
    php = ["php", os.path.join(ROOT, "server", "cli", "istudy.php")]
    subprocess.run(php + ["migrate"], env=env, check=True, capture_output=True)
    su, pw = ACCOUNTS["super"]
    subprocess.run(php + ["create-super", su], env=env, input=f"{pw}\n{pw}\n", text=True, check=True, capture_output=True)
    subprocess.run(php + ["install-id", INSTALL_ID], env=env, check=True, capture_output=True)

    conn = dbmod.connect(dbmod.conninfo_from_env(env))
    ctx = Ctx(conn=conn, paths=Paths(media), env=env, encoder="cpu")
    now = _dt.datetime.now(TZ)
    ctx.set_config("schedule", {"start": (now - _dt.timedelta(hours=1)).strftime("%H:%M"), "end": (now + _dt.timedelta(hours=2)).strftime("%H:%M"),
                                "auto_convert": True, "auto_whisper": True, "whisper_after_convert": True, "backup_time": "03:30", "backup_keep": 14})
    ctx.set_config("clip_threshold_gib", 0.0000001)
    ctx.set_config("conversion_paused", False)
    ctx.enqueue("scan", {}, 50)
    for _ in range(300):
        job = wmain.pick(ctx, list(wmain.HANDLERS))
        if not job:
            break
        wmain.run_job(ctx, job)
    ctx.enqueue("diagnose", {}, 80)
    job = wmain.pick(ctx, ["diagnose"])
    if job:
        wmain.run_job(ctx, job)
    if not args.setup_done:
        ctx.set_config("conversion_paused", True)

    procs = []
    # 真正的 Agent 常駐迴圈（心跳、排程、處理畫面排入的工作）
    procs.append(subprocess.Popen([sys.executable, "-m", "worker"], cwd=os.path.join(ROOT, "agent"), env=env,
                                  stdout=open(os.path.join(tempfile.gettempdir(), "istudy-e2e-agent.log"), "w"), stderr=subprocess.STDOUT))
    for port, admin in ((args.lan, False), (args.admin, True)):
        e = dict(env)
        if admin:
            e["ISTUDY_LOCAL_ADMIN"] = "1"
        procs.append(subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", os.path.join(ROOT, "server", "public"),
                                       os.path.join(ROOT, "server", "public", "index.php")], env=e,
                                      stdout=subprocess.DEVNULL, stderr=open(os.path.join(tempfile.gettempdir(), f"istudy-e2e-{port}.log"), "w")))
    for _ in range(100):
        try:
            call(args.lan, "GET", "/api/health")
            call(args.admin, "GET", "/api/health")
            break
        except OSError:
            time.sleep(0.1)

    tok = call(args.admin, "POST", "/api/login", {"username": su, "password": pw})["token"]
    groups = call(args.admin, "GET", "/api/groups", token=tok)
    courses = {c["name"]: c["id"] for c in groups["courses"]}
    g1 = call(args.admin, "POST", "/api/groups", {"name": "週一基礎班"}, token=tok)["group"]["id"]
    call(args.admin, "PUT", f"/api/groups/{g1}/courses", {"course_ids": [courses["基礎班"]]}, token=tok)
    ids = {}
    for key, role, title, name in (("admin", "admin", "assistant", "王老師"), ("lect", "user", "lecturer", "李講師"), ("user", "user", "assistant", "陳助教")):
        u, p = ACCOUNTS[key]
        r = call(args.admin, "POST", "/api/accounts", {"username": u, "password": p, "full_name": name, "role": role, "title_id": title,
                                                        "group_ids": [g1] if role == "user" else []}, token=tok)
        ids[key] = r["account"]["id"]
    # 一台已登記的 WOL 設備（不在線）
    call(args.admin, "POST", "/api/wol/devices", {"name": "教室 A-01", "mac": "00:11:22:33:44:01", "os_hint": "Windows 10", "enabled": True,
                                                  "device_code": "A01", "sort_order": 1}, token=tok)
    # dev.62：一台已登記但未啟用喚醒的設備（登入畫面不列出，只說明「另有 1 台未啟用」）
    call(args.admin, "POST", "/api/wol/devices", {"name": "教室 A-02（停用）", "mac": "00:11:22:33:44:02", "os_hint": "Windows 10", "enabled": False,
                                                  "device_code": "A02", "sort_order": 2}, token=tok)
    if args.setup_done:
        call(args.admin, "PUT", "/api/admin/setup", {"completed": True, "step": 9}, token=tok)
    call(args.admin, "POST", "/api/logout", {}, token=tok)
    mids = {r["file_name"]: r["id"] for r in conn.execute("SELECT id, file_name FROM media")}
    # dev.62：本機測試 FTPS 伺服器（模擬 InterServer；真 TLS、自簽 CA）
    from ftps_testserver import start_test_server
    ftps, ftps_root, ftps_ca = start_test_server(password="e2e-ftp-密碼-1")
    state = {"lan": f"http://127.0.0.1:{args.lan}", "admin": f"http://127.0.0.1:{args.admin}", "media_root": media,
             "accounts": {k: {"username": v[0], "password": v[1]} for k, v in ACCOUNTS.items()}, "courses": courses, "media": mids,
             "pid": os.getpid(), "db": DB, "backup_dir": env["ISTUDY_BACKUP_DIR"], "age_bin": env["ISTUDY_AGE_BIN"],
             "ftps": {"host": "localhost", "port": ftps.port, "username": "tester", "password": "e2e-ftp-密碼-1", "ca": ftps_ca, "root": ftps_root}}
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print("ready", flush=True)

    def stop(*_):
        for p in procs:
            p.terminate()
        conn.close()
        if not args.keep_media:
            shutil.rmtree(media, ignore_errors=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
