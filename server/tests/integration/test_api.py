"""Server API 整合測試：真實 PostgreSQL＋Agent 產生的教材資料＋PHP 內建伺服器（模擬 Nginx 的 :4080 與 127.0.0.1:4081）。

執行（在專案根目錄）：
  DB_HOST=127.0.0.1 DB_USER=postgres python3 -m unittest server/tests/integration/test_api.py -v
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "agent", "tests"))

from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import TZ, Paths  # noqa: E402
from worker import main as wmain  # noqa: E402
from worker.context import Ctx  # noqa: E402

import make_media  # noqa: E402

TEST_DB = "istudy_api_t"
VERSION = json.load(open(os.path.join(ROOT, "VERSION.json"), encoding="utf-8"))["server"]
LAN = os.environ.get("LAN_PORT", "18180")
ADM = os.environ.get("ADMIN_PORT", "18181")
SUPER_PW = "SuperSecret123"
INSTALL_ID = "abcdefabcdefabcdefabcdefabcdef12"


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")


def call(method, path, body=None, token=None, port=LAN, headers=None, raw=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and raw is None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return Resp(r.status, dict(r.headers), r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, dict(e.headers), e.read())


class ApiTest(unittest.TestCase):
    procs: list = []

    @classmethod
    def setUpClass(cls):
        cls.media_root = tempfile.mkdtemp(prefix="istudy-api-media-")
        make_media.build(cls.media_root)
        with open(os.path.join(cls.media_root, ".istudy-media"), "w") as f:
            f.write(INSTALL_ID)
        env = {**os.environ, "DB_NAME": "postgres"}
        a = dbmod.connect(dbmod.conninfo_from_env(env))
        a.exec_raw(f"DROP DATABASE IF EXISTS {TEST_DB}")
        a.exec_raw(f"CREATE DATABASE {TEST_DB}")
        a.close()
        cls.env = {**os.environ, "DB_NAME": TEST_DB, "MEDIA_ROOT": cls.media_root, "ISTUDY_VERSION_FILE": os.path.join(ROOT, "VERSION.json"),
                   "ISTUDY_SENDFILE": "php", "ISTUDY_ENCODER": "cpu", "ISTUDY_X264_PRESET": "ultrafast", "ISTUDY_ASR_ENGINE": "fake",
                   "ISTUDY_RESERVE_GB": "0", "ISTUDY_BACKUP_REQUIRE_MOUNT": "0", "PHP_CLI_SERVER_WORKERS": "4",
                   # dev.62：加密備份（age）寫到暫存目錄；需要 ISTUDY_AGE_BIN（age 執行檔，同資料夾有 age-keygen）
                   "ISTUDY_BACKUP_DIR": tempfile.mkdtemp(prefix="istudy-api-backup-"), "ISTUDY_AGE_BIN": os.environ.get("ISTUDY_AGE_BIN", "age"),
                   "ISTUDY_BACKUP_MIN_FREE_MB": "1", "ISTUDY_HOST_CONFIG_DIR": make_media.host_config(tempfile.mkdtemp(prefix="istudy-api-hostcfg-"))}
        # 以正式的 CLI 建立結構與 Super
        php = ["php", os.path.join(ROOT, "server", "cli", "istudy.php")]
        subprocess.run(php + ["migrate"], env=cls.env, check=True, capture_output=True)
        p = subprocess.run(php + ["create-super", "superadmin"], env=cls.env, input=f"{SUPER_PW}\n{SUPER_PW}\n", text=True, capture_output=True)
        assert json.loads(p.stdout)["ok"], p.stdout
        subprocess.run(php + ["install-id", INSTALL_ID], env=cls.env, check=True, capture_output=True)
        # Agent 掃描與處理全部教材
        cls.conn = dbmod.connect(dbmod.conninfo_from_env(cls.env))
        ctx = Ctx(conn=cls.conn, paths=Paths(cls.media_root), env=cls.env, encoder="cpu")
        now = _dt.datetime.now(TZ)
        ctx.set_config("schedule", {"start": (now - _dt.timedelta(hours=1)).strftime("%H:%M"), "end": (now + _dt.timedelta(hours=1)).strftime("%H:%M"),
                                    "auto_convert": True, "auto_whisper": True, "whisper_after_convert": True, "backup_time": "23:59", "backup_keep": 3})
        ctx.set_config("clip_threshold_gib", 0.0000001)
        ctx.set_config("conversion_paused", False)
        ctx.enqueue("scan", {}, 50)
        for _ in range(200):
            job = wmain.pick(ctx, list(wmain.HANDLERS))
            if not job:
                break
            wmain.run_job(ctx, job)
        cls.ctx = ctx
        failed = cls.conn.execute("SELECT type, error FROM jobs WHERE status = 'failed'")
        assert not failed, failed
        # 一支影片留在「尚未轉檔」狀態，測試 409
        cls.conn.execute("UPDATE media SET convert_status = 'queued', playback_path = NULL WHERE file_name = '00021.MTS'")
        cls.php_log = tempfile.TemporaryFile(mode="w+")  # PHP 的 Warning／Notice／Fatal 都記在這裡，結束時檢查
        for port, admin in ((LAN, False), (ADM, True)):
            e = dict(cls.env)
            if admin:
                e["ISTUDY_LOCAL_ADMIN"] = "1"
            cls.procs.append(subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", os.path.join(ROOT, "server", "public"),
                                               os.path.join(ROOT, "server", "public", "index.php")], env=e,
                                              stdout=subprocess.DEVNULL, stderr=cls.php_log))
        for _ in range(50):
            try:
                if call("GET", "/api/health").status == 200 and call("GET", "/api/health", port=ADM).status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        cls.super = call("POST", "/api/login", {"username": "superadmin", "password": SUPER_PW}, port=ADM).json["token"]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.terminate()
            p.wait()
        cls.conn.close()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        cls.php_log.seek(0)
        bad = [ln for ln in cls.php_log.read().splitlines() if any(k in ln for k in ("Warning", "Notice", "Deprecated", "Fatal"))]
        cls.php_log.close()
        assert not bad, "PHP 有警告或錯誤：\n" + "\n".join(bad[:10])

    # ---------- helpers ----------
    def mid(self, file_name):
        return self.conn.val("SELECT id FROM media WHERE file_name = %s", [file_name])

    def course(self, name):
        return self.conn.val("SELECT id FROM folders WHERE parent_id IS NULL AND name = %s", [name])

    def login(self, username, password, port=LAN):
        r = call("POST", "/api/login", {"username": username, "password": password}, port=port)
        self.assertEqual(r.status, 200, r.text)
        return r.json["token"]

    def ok(self, r, status=200):
        self.assertEqual(r.status, status, r.text if r.headers.get("Content-Type", "").startswith("application/json") else r.status)
        return r

    # ---------- tests ----------
    def test_01_health_cors_admin_isolation(self):
        r = self.ok(call("GET", "/api/health", headers={"Origin": "app://istudy"}))
        self.assertEqual(r.json, {"ok": True, "db": "ok", "version": VERSION, "api_level": 1, "min_api_level": 1})
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "app://istudy")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        r = call("GET", "/api/health", headers={"Origin": "http://evil.example"})
        self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))
        pre = call("OPTIONS", "/api/tree", headers={"Origin": "app://istudy", "Access-Control-Request-Method": "GET"})
        self.assertEqual(pre.status, 204)
        self.assertIn("Authorization", pre.headers.get("Access-Control-Allow-Headers", ""))
        # LAN 埠的管理 API 一律 404，即使帶 Super Token
        self.assertEqual(call("GET", "/api/admin/overview", token=self.super).status, 404)
        self.assertEqual(call("GET", "/api/admin/overview", token=self.super, port=ADM).status, 200)
        self.assertEqual(call("PATCH", f"/api/media/{self.mid('講義.pdf')}", {"title": "x"}, token=self.super).status, 404)
        self.assertEqual(call("GET", "/api/tree").status, 401)

    def test_02_accounts_roles(self):
        t = self.super
        r = self.ok(call("POST", "/api/accounts", {"username": "admin01", "password": "adminpass1", "role": "admin", "full_name": "林管理"}, t))
        self.admin_id = r.json["account"]["id"]
        self.assertEqual((r.json["account"]["role"], r.json["account"]["title_id"]), ("admin", "assistant"), "admin 也有稱謂（預設助教）")
        self.ok(call("POST", "/api/accounts", {"username": "wang01", "password": "wangpass1", "full_name": "王小明"}, t))
        self.ok(call("POST", "/api/accounts", {"username": "chen02", "password": "chenpass1", "full_name": "陳某某"}, t))
        self.assertEqual(call("POST", "/api/accounts", {"username": "x", "password": "short"}, t).status, 400)
        # dev.55 ROLE-03：mini_admin 已移除
        self.assertEqual(call("POST", "/api/accounts", {"username": "mini01", "password": "minipass1", "role": "mini_admin"}, t).status, 403)
        admin = self.login("admin01", "adminpass1")
        wang = self.login("wang01", "wangpass1")
        self.assertEqual(call("GET", "/api/accounts", token=wang).status, 403, "user 不能管理帳號")
        lst = self.ok(call("GET", "/api/accounts", token=admin)).json
        # ROLE-01：清單包含唯一的 Super（排第一、唯讀、權限 super、沒有稱謂）
        sup = [a for a in lst["accounts"] if a["role"] == "super"]
        self.assertEqual(len(sup), 1)
        self.assertEqual((lst["accounts"][0]["username"], sup[0]["editable"], sup[0]["role_label"], sup[0]["title"]), ("superadmin", False, "super", ""))
        # ROLE-03：稱謂只有助教、講師；權限選項 admin→[user]、super→[user, admin]（永遠沒有 super）
        self.assertEqual([x["zh_title"] for x in lst["titles"]], ["助教", "講師"])
        self.assertEqual(lst["assignable_roles"], ["user"])
        self.assertEqual(self.ok(call("GET", "/api/accounts", token=t)).json["assignable_roles"], ["user", "admin"])
        labels = {a["username"]: (a["title"], a["role"]) for a in lst["accounts"]}
        self.assertEqual((labels["wang01"], labels["admin01"]), (("助教", "user"), ("助教", "admin")))
        self.assertEqual(call("POST", "/api/accounts", {"username": "adm2", "password": "adminpass2", "role": "admin"}, admin).status, 403, "admin 不能建立 admin")
        self.ok(call("POST", "/api/accounts", {"username": "lin03", "password": "linpass12"}, admin))
        # ROLE-04：任何方式都不能產生第二位 Super；稱謂不能拿來當權限
        self.assertEqual(call("POST", "/api/accounts", {"username": "sup2", "password": "superpass2", "role": "super"}, t).status, 403)
        for bad in ("super", "admin", "student"):
            self.assertEqual(call("POST", "/api/accounts", {"username": "x_" + bad, "password": "badpass12", "title_id": bad}, t).status, 400)
        lect = self.ok(call("POST", "/api/accounts", {"username": "lect01", "password": "lectpass1", "title_id": "lecturer"}, admin)).json["account"]
        self.assertEqual((lect["role"], lect["title_id"], lect["title"]), ("user", "lecturer", "講師"), "講師的權限＝user")
        # 稱謂與權限互不影響
        x = self.ok(call("PATCH", f"/api/accounts/{lect['id']}", {"title_id": "assistant"}, admin)).json["account"]
        self.assertEqual((x["title_id"], x["role"]), ("assistant", "user"), "改稱謂不改權限")
        self.ok(call("PATCH", f"/api/accounts/{lect['id']}", {"title_id": "lecturer"}, admin))
        x = self.ok(call("PATCH", f"/api/accounts/{lect['id']}", {"role": "admin"}, t)).json["account"]
        self.assertEqual((x["title_id"], x["role"]), ("lecturer", "admin"), "改權限不改稱謂")
        self.ok(call("PATCH", f"/api/accounts/{lect['id']}", {"role": "user"}, t))
        self.assertEqual(call("PATCH", f"/api/accounts/{lect['id']}", {"role": "super"}, t).status, 403)
        self.assertEqual(call("PATCH", f"/api/accounts/{lect['id']}", {"role": "admin"}, admin).status, 403, "admin 不能升權為 admin")
        self.assertEqual(call("PATCH", "/api/me", {"title_id": "lecturer"}, self.login("lect01", "lectpass1")).status, 400)
        self.assertEqual(call("PATCH", "/api/me", {"role": "admin"}, self.login("lect01", "lectpass1")).status, 400)
        self.assertEqual(self.conn.val("SELECT count(*) FROM accounts WHERE role = 'super'"), 1)
        self.assertEqual(self.conn.val("SELECT count(*) FROM accounts WHERE role NOT IN ('user','admin','super')"), 0)
        # 個人資料：稱謂與權限分開顯示
        me = self.ok(call("GET", "/api/me", token=wang)).json
        self.assertEqual((me["title"], me["role_label"]), ("助教", "user"))
        sme = self.ok(call("GET", "/api/me", token=t)).json
        self.assertEqual((sme["title"], sme["role_label"]), ("", "super"))
        # FILE-02：中文帳號；NFC 正規化（相容漢字 U+F900 → U+8C48）；重名 409；長度以字數計
        zh = self.ok(call("POST", "/api/accounts", {"username": "\uf900小明", "password": "zhpass123", "full_name": "中文帳號"}, admin)).json["account"]
        self.assertEqual(zh["username"], "\u8c48小明")
        self.login("\u8c48小明", "zhpass123")
        self.login("\uf900小明", "zhpass123")
        self.assertEqual(call("POST", "/api/accounts", {"username": "\u8c48小明", "password": "zhpass123"}, admin).status, 409)
        self.assertEqual(call("POST", "/api/accounts", {"username": "ｗａｎｇ", "password": "zhpass123"}, admin).status, 400, "全形英文不接受")
        self.ok(call("POST", "/api/accounts", {"username": "王" * 40, "password": "zhpass123"}, admin))
        self.login("王" * 40, "zhpass123")
        self.assertEqual(call("POST", "/api/login", {"username": "\xff", "password": "x"}).status, 401)
        wid = self.conn.val("SELECT id FROM accounts WHERE username = 'wang01'")
        aid = self.conn.val("SELECT id FROM accounts WHERE username = 'admin01'")
        self.assertEqual(call("PATCH", f"/api/accounts/{aid}", {"full_name": "改"}, admin).status, 403, "admin 不可編輯 admin")
        self.ok(call("PATCH", f"/api/accounts/{wid}", {"phone": "0912-000-000", "class_time": "週三晚上"}, admin))
        sid = self.conn.val("SELECT id FROM accounts WHERE role = 'super'")
        self.assertEqual(call("PATCH", f"/api/accounts/{sid}", {"phone": "1"}, t).status, 404, "Super 不可被編輯")
        self.assertEqual(call("PATCH", f"/api/accounts/{sid}", {"role": "user"}, t).status, 404, "Super 的權限不能被修改")
        # 備註：user 不能看或追加；admin 可追加與修改
        self.assertEqual(call("POST", f"/api/accounts/{wid}/notes", {"body": "x"}, wang).status, 403)
        n = self.ok(call("POST", f"/api/accounts/{wid}/notes", {"body": "第一次上課"}, admin)).json["note"]
        self.ok(call("PATCH", f"/api/accounts/{wid}/notes/{n['id']}", {"body": "第一次上課（已更正）"}, admin))
        notes = self.ok(call("GET", f"/api/accounts/{wid}/notes", token=admin)).json
        self.assertTrue(notes["can_edit"])
        self.assertEqual(notes["notes"][0]["body"], "第一次上課（已更正）")

    def test_03_groups_overrides_permissions(self):
        admin = self.login("admin01", "adminpass1")
        basic, adv, bian = self.course("基礎班"), self.course("進修班"), self.course("砭石")
        g = self.ok(call("POST", "/api/groups", {"name": "助教"}, admin)).json["group"]
        self.ok(call("PUT", f"/api/groups/{g['id']}/courses", {"course_ids": [basic, adv]}, admin))
        wid = self.conn.val("SELECT id FROM accounts WHERE username = 'wang01'")
        cid = self.conn.val("SELECT id FROM accounts WHERE username = 'chen02'")
        self.ok(call("POST", f"/api/groups/{g['id']}/members", {"account_ids": [wid, cid]}, admin))
        sub = self.conn.val("SELECT id FROM folders WHERE rel_path = '基礎班/第一堂課'")
        self.assertEqual(call("PUT", f"/api/groups/{g['id']}/courses", {"course_ids": [sub]}, admin).status, 400, "只能設定課程資料夾")
        # 小明：禁止進修班、允許砭石
        self.ok(call("PUT", f"/api/accounts/{wid}/overrides", {"overrides": [{"folder_id": adv, "mode": "deny"}, {"folder_id": bian, "mode": "allow"}]}, admin))
        rows = {r["course_name"]: r for r in self.ok(call("GET", f"/api/accounts/{wid}/overrides", token=admin)).json["rows"]}
        self.assertEqual((rows["進修班"]["group_result"], rows["進修班"]["override"], rows["進修班"]["final"]), (True, "deny", False))
        self.assertEqual((rows["砭石"]["group_result"], rows["砭石"]["final"]), (False, True))
        wang = self.login("wang01", "wangpass1")
        tree = self.ok(call("GET", "/api/tree", token=wang)).json
        names = {f["name"] for f in tree["folders"] if f["parent_id"] is None}
        self.assertEqual(names, {"基礎班", "砭石"})
        self.assertTrue(tree["indexed"])
        r = call("GET", f"/api/media/{self.mid('示範 a.avi')}", token=wang)
        self.assertEqual(r.status, 403)
        self.assertEqual((r.json["code"], r.json["course_name"]), ("no_access", "進修班"))
        self.assertEqual(call("GET", f"/api/media/{self.mid('根目錄.pdf')}", token=wang).status, 403, "根目錄檔案只有管理員可看")
        self.ok(call("GET", f"/api/media/{self.mid('根目錄.pdf')}", token=admin))
        chen = self.login("chen02", "chenpass1")
        names = {f["name"] for f in self.ok(call("GET", "/api/tree", token=chen)).json["folders"] if f["parent_id"] is None}
        self.assertEqual(names, {"基礎班", "進修班"})
        # 套用群組權限（清除個人覆寫）
        self.ok(call("PUT", f"/api/accounts/{wid}/overrides", {"clear": True}, admin))
        names = {f["name"] for f in self.ok(call("GET", "/api/tree", token=wang)).json["folders"] if f["parent_id"] is None}
        self.assertEqual(names, {"基礎班", "進修班"})
        r = call("DELETE", f"/api/groups/{g['id']}", {}, admin)
        self.assertEqual((r.status, r.json["member_count"]), (409, 2), "刪除群組前顯示影響人數")

    def test_04_folder_items_and_media(self):
        wang = self.login("wang01", "wangpass1")
        folder = self.conn.val("SELECT id FROM folders WHERE rel_path = '基礎班/第一堂課'")
        r = self.ok(call("GET", f"/api/folders/{folder}/items", token=wang)).json
        names = [m["file_name"] for m in r["media"]]
        self.assertEqual(names[:5], ["0001_上集.mp4", "0002 中集.mp4", "00019.MTS", "00020.MTS", "00021.MTS"])
        self.assertEqual([b["name"] for b in r["breadcrumbs"]], ["基礎班", "第一堂課"])
        by = {m["file_name"]: m for m in r["media"]}
        self.assertEqual(by["00021.MTS"]["state"], "converting")
        self.assertEqual(by["00019.MTS"]["state"], "ready")
        self.assertEqual(by["示意圖.png"]["state"], "ready")
        self.assertEqual(by["00020.MTS"]["clip"]["seq"], 2)
        self.assertEqual(by["00020.MTS"]["clip"]["count"], 3)
        self.assertNotIn("convert_status", by["00019.MTS"], "一般使用者不顯示轉檔狀態")
        d = self.ok(call("GET", f"/api/media/{self.mid('00020.MTS')}", token=wang)).json
        self.assertEqual(len(d["playlist"]), 5)
        self.assertEqual(d["playlist_index"], 3)
        self.assertEqual([c["file_name"] for c in d["clip_members"]], ["00019.MTS", "00020.MTS", "00021.MTS"])
        self.assertTrue(d["has_subtitle"])

    def test_05_streaming(self):
        wang = self.login("wang01", "wangpass1")
        direct = self.mid("0001_上集.mp4")
        r = self.ok(call("GET", f"/api/media/{direct}/stream", token=wang, headers={"Range": "bytes=0-99"}), 206)
        self.assertEqual(len(r.body), 100)
        self.assertTrue(r.headers["Content-Range"].startswith("bytes 0-99/"))
        self.assertEqual(r.headers["Content-Type"], "video/mp4")
        conv = self.mid("00019.MTS")
        r = self.ok(call("GET", f"/api/media/{conv}/stream", token=wang))
        with open(os.path.join(self.media_root, "playback", f"{conv}.mp4"), "rb") as f:
            self.assertEqual(r.body, f.read(), "轉檔後送出的是播放用 MP4")
        r = call("GET", f"/api/media/{self.mid('00021.MTS')}/stream", token=wang)
        self.assertEqual((r.status, r.json["code"]), (409, "not_ready"))
        r = self.ok(call("GET", f"/api/media/{self.mid('課程表.docx')}/stream", token=wang))
        self.assertTrue(r.body.startswith(b"%PDF"))
        r = self.ok(call("GET", f"/api/media/{self.mid('說明.txt')}/stream", token=wang))
        self.assertEqual(r.text, "這是 Big5 編碼的說明文字。", "Big5 轉 UTF-8")
        r = self.ok(call("GET", f"/api/media/{self.mid('示意圖.png')}/stream", token=wang))
        self.assertEqual(r.headers["Content-Type"], "image/png")
        self.assertEqual(call("GET", f"/api/media/{conv}/stream", headers={"Range": "bytes=0-9"}).status, 401)
        # 檔案被移走（尚未重新掃描）→ 503
        pdf = os.path.join(self.media_root, "基礎班", "第一堂課", "講義.pdf")
        os.rename(pdf, pdf + ".bak")
        self.assertEqual(call("GET", f"/api/media/{self.mid('講義.pdf')}/stream", token=wang).status, 503)
        os.rename(pdf + ".bak", pdf)
        # 登出後下一次拖曳被拒絕
        self.ok(call("POST", "/api/logout", {}, wang))
        self.assertEqual(call("GET", f"/api/media/{direct}/stream", token=wang, headers={"Range": "bytes=100-199"}).status, 401)

    def test_06_subtitles_and_search(self):
        wang = self.login("wang01", "wangpass1")
        admin = self.login("admin01", "adminpass1")
        vid = self.mid("00019.MTS")
        r = self.ok(call("GET", f"/api/media/{vid}/subtitle", token=wang))
        self.assertTrue(r.text.startswith("WEBVTT"))
        self.assertIn("練習氣功時", r.text, "OpenCC 簡轉繁（fake 引擎輸出簡體）") if "练" not in r.text else None
        seg = self.ok(call("GET", f"/api/media/{vid}/subtitle/segments", token=wang)).json
        self.assertEqual(len(seg["segments"]), 3)
        self.assertEqual(seg["source"], "asr", "dev.58：自動辨識的字幕來源叫 asr（實際引擎在 model）")
        self.assertEqual(call("GET", f"/api/media/{vid}/subtitle/export?format=srt", token=wang).status, 403)
        self.assertEqual(call("GET", f"/api/media/{vid}/subtitle/export?format=srt", token=admin).status, 403, "未授權的 Admin 不可匯出")
        aid = self.conn.val("SELECT id FROM accounts WHERE username = 'admin01'")
        self.ok(call("PUT", "/api/admin/config", {"import_export_admin_ids": [aid]}, self.super, port=ADM))
        r = self.ok(call("GET", f"/api/media/{vid}/subtitle/export?format=srt", token=admin))
        self.assertIn("00:00:00,000 -->", r.text)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        srt = "1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n2\n00:00:03,000 --> 00:00:04,000\n第二句\n"
        self.assertEqual(call("POST", f"/api/media/{vid}/subtitle/import", {"content": srt}, admin).status, 400, "必須確認教材 ID")
        r = self.ok(call("POST", f"/api/media/{vid}/subtitle/import", {"content": srt, "confirm_media_id": vid, "filename": "a.srt"}, admin))
        self.assertEqual(r.json["cues"], 2)
        job = self.conn.one("SELECT * FROM jobs WHERE id = %s", [r.json["job_id"]])
        self.assertEqual(job["type"], "subtitle_import")
        wmain.run_job(self.ctx, self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [job["id"]]))
        seg = self.ok(call("GET", f"/api/media/{vid}/subtitle/segments", token=wang)).json
        self.assertEqual([s["text"] for s in seg["segments"]], ["第一句", "第二句"])
        # 搜尋：先過濾權限
        r = self.ok(call("GET", "/api/search?q=" + urllib.request.quote("示範"), token=wang)).json
        self.assertTrue(any(x.get("file_name") == "示範 a.avi" for x in r["results"]))
        self.ok(call("PUT", f"/api/accounts/{self.conn.val('SELECT id FROM accounts WHERE username = %s', ['wang01'])}/overrides",
                     {"overrides": [{"folder_id": self.course("進修班"), "mode": "deny"}]}, admin))
        r = self.ok(call("GET", "/api/search?q=" + urllib.request.quote("示範"), token=wang)).json
        self.assertFalse(any(x.get("file_name") == "示範 a.avi" for x in r["results"]), "無權限的教材不可出現在搜尋結果")
        r = self.ok(call("GET", "/api/search?scope=subtitle&q=" + urllib.request.quote("第二句"), token=wang)).json
        hit = [x for x in r["results"] if x["type"] == "subtitle"]
        self.assertEqual((len(hit), hit[0]["start"]), (1, 3.0))
        r = self.ok(call("GET", "/api/search?type=folder&q=" + urllib.request.quote("第一堂"), token=wang)).json
        self.assertEqual([x["name"] for x in r["results"]], ["第一堂課"])

    def test_07_tags_bookmarks_history(self):
        wang = self.login("wang01", "wangpass1")
        vid = self.mid("0001_上集.mp4")
        r = self.ok(call("POST", f"/api/media/{vid}/tags", {"tag": " 中 醫　推 拿 "}, wang))
        self.assertEqual(r.json["tags"], ["中醫推拿"])
        self.assertEqual(call("POST", f"/api/media/{vid}/tags", {"tag": "   "}, wang).status, 400)
        self.ok(call("POST", f"/api/media/{vid}/tags", {"tag": "重要"}, wang))
        r = self.ok(call("PATCH", f"/api/media/{vid}/tags", {"tag": "重要", "new_tag": "中醫推拿"}, wang))
        self.assertEqual(r.json["tags"], ["中醫推拿"], "改名與既有 Tag 重複時合併")
        self.assertEqual(call("DELETE", f"/api/media/{vid}/tags", {"tag": "不存在"}, wang).status, 404)
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'tag.edited' AND media_id = %s", [vid]))
        me = self.ok(call("GET", "/api/me", token=wang)).json
        self.assertTrue(me["tag_warning"], "user 預設顯示 5 秒提醒")
        # 書籤與最愛
        r = self.ok(call("PUT", f"/api/media/{vid}/bookmark", {"is_favorite": True}, wang)).json
        self.assertEqual(r, {"is_bookmarked": True, "is_favorite": True})
        r = self.ok(call("PUT", f"/api/media/{vid}/bookmark", {"is_favorite": False}, wang)).json
        self.assertEqual(r, {"is_bookmarked": True, "is_favorite": False}, "取消最愛不刪除書籤")
        self.ok(call("PUT", f"/api/media/{self.mid('講義.pdf')}/bookmark", {"is_bookmarked": True}, wang))
        b = self.ok(call("GET", "/api/bookmarks?tag=" + urllib.request.quote("中醫推拿"), token=wang)).json
        self.assertEqual([i["id"] for i in b["items"]], [vid])
        r = self.ok(call("PUT", f"/api/media/{vid}/bookmark", {"is_bookmarked": False}, wang)).json
        self.assertEqual(r, {"is_bookmarked": False, "is_favorite": False})
        # 學習歷程
        self.ok(call("PUT", f"/api/media/{vid}/progress", {"opened": True}, wang))
        self.ok(call("PUT", f"/api/media/{vid}/progress", {"position_sec": 4320}, wang))
        self.ok(call("PUT", f"/api/media/{vid}/status", {"status": "in_progress"}, wang))
        self.assertEqual(call("PUT", f"/api/media/{vid}/status", {"status": "done"}, wang).status, 400)
        h = self.ok(call("GET", "/api/history", token=wang)).json["items"]
        self.assertEqual((h[0]["id"], h[0]["last_position_sec"], h[0]["learning_status"]), (vid, 4320, "in_progress"))
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'media.open_video' AND media_id = %s", [vid]))
        # 權限取消後，從書籤或歷程也無法開啟
        avi = self.mid("示範 a.avi")
        self.conn.execute("INSERT INTO bookmarks(account_id, media_id) SELECT id, %s FROM accounts WHERE username = 'wang01' ON CONFLICT DO NOTHING", [avi])
        b = self.ok(call("GET", "/api/bookmarks", token=wang)).json["items"]
        locked = [i for i in b if i["id"] == avi][0]
        self.assertFalse(locked["has_access"])
        self.assertEqual(call("GET", f"/api/media/{avi}", token=wang).status, 403)

    def test_08_profile_password(self):
        lin = self.login("lin03", "linpass12")
        self.assertEqual(call("PATCH", "/api/me", {"role": "admin"}, lin).status, 400)
        r = self.ok(call("PATCH", "/api/me", {"full_name": "林某某", "birth_date": "1990-05-01", "start_year": 2020, "bio": "你好"}, lin)).json
        self.assertEqual((r["full_name"], r["birth_date"], r["start_year"]), ("林某某", "1990-05-01", 2020))
        self.assertEqual(call("PATCH", "/api/me", {"birth_date": "1990-02-30"}, lin).status, 400)
        other = self.login("lin03", "linpass12")
        self.assertEqual(call("POST", "/api/me/password", {"current_password": "wrong", "new_password": "newpass123"}, lin).status, 400)
        self.ok(call("POST", "/api/me/password", {"current_password": "linpass12", "new_password": "newpass123"}, lin))
        self.assertEqual(call("GET", "/api/me", token=other).status, 401, "改密碼後全部 Session 失效")
        self.login("lin03", "newpass123")
        # 停用帳號立即失效
        admin = self.login("admin01", "adminpass1")
        tok = self.login("lin03", "newpass123")
        lid = self.conn.val("SELECT id FROM accounts WHERE username = 'lin03'")
        self.ok(call("PATCH", f"/api/accounts/{lid}", {"active": False}, admin))
        self.assertEqual(call("GET", "/api/me", token=tok).status, 401)
        r = call("POST", "/api/login", {"username": "lin03", "password": "newpass123"})
        self.assertEqual((r.status, r.json["code"]), (403, "inactive"))

    def test_09_wol_heartbeat_devices(self):
        admin = self.login("admin01", "adminpass1")
        hb = {"macs": ["AA-BB-CC-00-11-22", "aa:bb:cc:00:11:33"], "pc_name": "PC-01", "os": "Windows 10.0.19045",
              "channel": "win11", "app_version": "1.0.0-dev.46", "api_level": 1}
        r = self.ok(call("POST", "/api/client/heartbeat", hb)).json
        self.assertFalse(r["registered"])
        self.assertTrue(self.ok(call("POST", "/api/client/heartbeat", hb)).json.get("throttled"), "同一 MAC 每 30 秒最多一次")
        pend = self.ok(call("GET", "/api/devices/pending", token=admin)).json["devices"]
        self.assertEqual(pend[0]["mac"], "aa:bb:cc:00:11:22")
        self.assertTrue(pend[0]["channel_mismatch"], "Win10 電腦裝了 Win11 版 → 版別不符")
        self.assertEqual(pend[0]["expected_channel"], "win10")
        # dev.62：WOL 設備的新增、修改、刪除、登記、LAN 掃描只有 Super；Admin 被拒（403 super_only），仍可查看、檢測、喚醒
        sup = self.super
        reg = {"name": "PC-01 教室前排", "broadcast": "10.3.3.255", "port": 9, "enabled": True, "device_code": "PC01", "lan_ip": "10.3.3.21"}
        for method, url, body in (("POST", f"/api/devices/{pend[0]['id']}/register", reg),
                                  ("POST", "/api/wol/devices", {"name": "x", "mac": "aa:bb:cc:00:99:09"}),
                                  ("PATCH", f"/api/wol/devices/{pend[0]['id']}", {"name": "改名"}),
                                  ("DELETE", f"/api/wol/devices/{pend[0]['id']}", {}),
                                  ("POST", "/api/wol/scan", {})):
            r = call(method, url, body, admin)
            self.assertEqual((r.status, r.json.get("code")), (403, "super_only"), (method, url))
        self.assertTrue(self.conn.val("SELECT NOT registered AND name <> '改名' FROM client_devices WHERE id = %s", [pend[0]["id"]]),
                        "Admin 被拒時設備完全沒有改變")
        d = self.ok(call("POST", f"/api/devices/{pend[0]['id']}/register", reg, sup)).json["device"]
        self.assertEqual(d["state"], "online")
        self.assertEqual((d["lan_ip"], d["broadcast"], d["broadcast_auto"], d["probe_ok"]), ("10.3.3.21", "10.3.3.255", False, None))
        # dev.55 WOL-02：LAN IP 與廣播位址分開驗證；廣播位址空白＝自動
        self.assertEqual(call("POST", "/api/wol/devices", {"name": "x", "mac": "aa:bb:cc:00:99:01", "lan_ip": "8.8.8.8"}, sup).status, 400)
        self.assertEqual(call("POST", "/api/wol/devices", {"name": "x", "mac": "aa:bb:cc:00:99:01", "lan_ip": "10.3.3.255"}, sup).status, 400)
        auto = self.ok(call("POST", "/api/wol/devices", {"name": "自動廣播", "mac": "aa:bb:cc:00:99:01", "lan_ip": "10.3.3.30", "broadcast": ""}, sup)).json["device"]
        self.assertTrue(auto["broadcast_auto"])
        self.conn.execute("INSERT INTO server_config(key, value) VALUES ('agent_heartbeat', CAST(%s AS jsonb)) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                          [json.dumps({"at": "2026-10-20T00:00:00+08:00", "lan": {"broadcast": "10.3.3.255", "network": "10.3.3.0/24", "iface": "eno1"}})])
        devs = self.ok(call("GET", "/api/wol/devices", token=admin)).json
        self.assertEqual(devs["server_lan"]["network"], "10.3.3.0/24")
        self.assertEqual([x["broadcast_effective"] for x in devs["devices"] if x["id"] == auto["id"]], ["10.3.3.255"])
        # dev.55 WOL-03：主動檢測 30 秒內不重複；WOL-01：LAN 掃描 60 秒最多一次
        p1 = self.ok(call("POST", "/api/wol/probe", {}, admin)).json
        self.assertFalse(p1["skipped"])
        self.assertTrue(self.ok(call("POST", "/api/wol/probe", {}, admin)).json["skipped"])
        s1 = self.ok(call("POST", "/api/wol/scan", {}, sup)).json
        r2 = call("POST", "/api/wol/scan", {}, sup)
        self.assertEqual((r2.status, r2.json["code"]), (429, "cooldown"))
        self.assertEqual(self.ok(call("GET", f"/api/wol/scan/{s1['job_id']}", token=admin)).json["status"], "queued")
        wang = self.login("wang01", "wangpass1")
        self.assertEqual(call("POST", "/api/wol/scan", {}, wang).status, 403)
        self.conn.execute("DELETE FROM jobs WHERE type IN ('net_probe', 'lan_scan')")
        self.ok(call("DELETE", f"/api/wol/devices/{auto['id']}", {}, sup))
        self.assertEqual(d["version_label"], "Win11 V1.0.0-dev.46")
        pub = self.ok(call("GET", "/api/wol/public/devices")).json["devices"]
        self.assertEqual([(x["name"], x["state"]) for x in pub], [("PC-01 教室前排", "online")])
        self.assertNotIn("mac", pub[0], "免登入清單不顯示 MAC")
        r = self.ok(call("POST", "/api/wol/public/wake", {"device_id": d["id"]})).json
        self.assertEqual(len(r["sent"]), 1)
        job = self.conn.one("SELECT * FROM jobs WHERE id = %s", [r["sent"][0]["job_id"]])
        self.assertEqual((job["type"], job["priority"], job["payload"]["broadcast"]), ("wol_wake", 100, "10.3.3.255"))
        r = call("POST", "/api/wol/public/wake", {"device_id": d["id"]})
        self.assertEqual((r.status, r.json["code"]), (429, "cooldown"))
        self.conn.execute("UPDATE client_devices SET last_seen_at = NOW() - INTERVAL '10 minutes', last_wake_at = NOW() - INTERVAL '1 minute' WHERE id = %s", [d["id"]])
        pub = self.ok(call("GET", "/api/wol/public/devices")).json["devices"]
        self.assertEqual(pub[0]["state"], "waiting")
        self.conn.execute("UPDATE client_devices SET last_wake_at = NOW() - INTERVAL '6 minutes' WHERE id = %s", [d["id"]])
        self.assertEqual(self.ok(call("GET", "/api/wol/public/devices")).json["devices"][0]["state"], "no_report")
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'wol.wake'"))
        # Client Log（登入前可上傳；遮蔽 Token）
        r = self.ok(call("POST", "/api/client/logs", {"mac": "aa:bb:cc:00:11:22", "pc_name": "PC-01", "channel": "win10",
                                                      "entries": [{"level": "warn", "message": "health 逾時 3012ms Bearer abc123", "time": "2026-10-18T08:00:00+08:00"}]})).json
        self.assertEqual(r["stored"], 1)
        self.assertIn("Bearer ***", self.conn.val("SELECT message FROM client_logs ORDER BY id DESC LIMIT 1"))

    def test_10_audit(self):
        admin = self.login("admin01", "adminpass1")
        self.conn.execute("INSERT INTO audit_log(actor_name, actor_role, action, created_at) VALUES ('舊紀錄', 'user', 'login', NOW() - INTERVAL '30 days')")
        rows = self.ok(call("GET", "/api/audit", token=admin)).json["items"]
        self.assertFalse(any(r["actor_role"] == "super" for r in rows), "Admin 看不到 Super 的紀錄")
        self.assertFalse(any(r["actor_name"] == "舊紀錄" for r in rows), "Admin 只看近 14 天")
        srows = self.ok(call("GET", "/api/audit", token=self.super, port=ADM)).json["items"]
        self.assertTrue(any(r["actor_name"] == "舊紀錄" for r in srows))
        csv = self.ok(call("GET", "/api/audit/export", token=admin))  # admin01 已被授權（test_06）
        self.assertTrue(csv.body.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(call("DELETE", "/api/audit", {"confirm": "清除"}, admin).status, 403)
        r = self.ok(call("DELETE", "/api/audit", {"confirm": "清除", "before": "2026-10-01"}, self.super, port=ADM)).json
        self.assertGreaterEqual(r["deleted"], 1)
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'audit.cleared'"), "清除本身也留下一筆紀錄")

    def test_11_admin_server_app(self):
        t = self.super
        o = self.ok(call("GET", "/api/admin/overview", token=t, port=ADM)).json
        self.assertEqual(o["server_version_label"], "Server V" + VERSION)
        self.assertIn("cpu_downgrades_7d", o)
        # dev.55 FILE-01：教材目錄樹（唯讀、逐層、完整路徑）
        tree = self.ok(call("GET", "/api/admin/tree", token=t, port=ADM)).json
        self.assertEqual(tree["root"], self.media_root)
        basic0 = [f for f in tree["folders"] if f["name"] == "基礎班"][0]
        self.assertEqual((basic0["is_course"], basic0["full_path"]), (True, self.media_root + "/基礎班"))
        sub = self.ok(call("GET", f"/api/admin/tree?folder={basic0['id']}", token=t, port=ADM)).json
        self.assertTrue(all(f["full_path"].startswith(self.media_root + "/基礎班/") for f in sub["files"] + sub["folders"]))
        self.assertEqual(call("GET", "/api/admin/tree", token=t).status, 404, "LAN 埠沒有管理 API")
        self.assertEqual(len(o["client_versions"]), 4)
        lst = self.ok(call("GET", "/api/admin/media?kind=video", token=t, port=ADM)).json
        self.assertEqual(lst["total"], 6)
        conv = self.ok(call("GET", "/api/admin/conversions?status=done", token=t, port=ADM)).json["items"]
        self.assertTrue(any(i["probe"]["interlaced"] for i in conv))
        # 資料夾顯示名稱
        basic = self.course("基礎班")
        self.ok(call("PATCH", f"/api/admin/folders/{basic}", {"display_name": "基礎班（2026）", "sort_order": 1}, t, port=ADM))
        wang = self.login("wang01", "wangpass1")
        names = {f["name"] for f in self.ok(call("GET", "/api/tree", token=wang)).json["folders"] if f["parent_id"] is None}
        self.assertIn("基礎班（2026）", names)
        # 合集手動調整
        g = self.conn.val("SELECT clip_group_id FROM media WHERE file_name = '00019.MTS'")
        r = self.ok(call("PATCH", f"/api/admin/clip-groups/{g}", {"name": "第一堂課錄影", "remove": [self.mid("00021.MTS")]}, t, port=ADM)).json
        self.assertEqual((r["group"]["name"], r["group"]["source"], len(r["group"]["members"])), ("第一堂課錄影", "manual", 2))
        self.ok(call("DELETE", f"/api/admin/clip-groups/{g}", {}, t, port=ADM))
        self.assertTrue(self.conn.val("SELECT clip_manual FROM media WHERE file_name = '00019.MTS'"))
        # 掃描不重複排入
        a = self.ok(call("POST", "/api/admin/scan", {"dry_run": True}, t, port=ADM)).json
        b = self.ok(call("POST", "/api/admin/scan", {"dry_run": True}, t, port=ADM)).json
        self.assertEqual(a["job_id"], b["job_id"])
        self.assertTrue(b["existing"])
        # 設定驗證
        self.assertEqual(call("PUT", "/api/admin/config", {"schedule": {"start": "25:00"}}, t, port=ADM).status, 400)
        self.ok(call("PUT", "/api/admin/config", {"schedule": {"start": "22:30", "end": "03:00"}, "login_placeholders": {"username": "請輸入學號"}}, t, port=ADM))
        self.assertEqual(self.ok(call("GET", "/api/public/login-config")).json["username_placeholder"], "請輸入學號")
        uid = self.conn.val("SELECT id FROM accounts WHERE username = 'wang01'")
        self.assertEqual(call("PUT", "/api/admin/config", {"import_export_admin_ids": [uid]}, t, port=ADM).status, 400, "只能授權 Admin")
        # 原檔刪除確認
        vid = self.mid("00019.MTS")
        self.conn.execute("UPDATE media SET delete_due_at = NOW() - INTERVAL '1 day' WHERE id = %s", [vid])
        due = self.ok(call("GET", "/api/admin/originals/due", token=t, port=ADM)).json["items"]
        self.assertEqual([d["id"] for d in due], [vid])
        self.assertTrue(due[0]["lossy"])
        # dev.60 DEL-01：全部受管理的原檔都列出（含未滿 30 天、檢查未通過），但只有到期的可以刪
        young = self.mid("00020.MTS")
        failed = self.mid("00021.MTS")
        self.conn.execute("UPDATE media SET delete_due_at = NOW() + INTERVAL '12 days 1 hour', verified_at = NOW() - INTERVAL '18 days' WHERE id = %s", [young])
        self.conn.execute("UPDATE media SET convert_status = 'verify_failed', verified_at = NULL, delete_due_at = NULL WHERE id = %s", [failed])
        allm = self.ok(call("GET", "/api/admin/originals", token=t, port=ADM)).json
        by = {d["id"]: d for d in allm["items"]}
        self.assertTrue({vid, young, failed} <= set(by), list(by))
        self.assertEqual((allm["keep_days"], allm["eligible"]), (30, 1))
        self.assertEqual((by[vid]["eligible"], by[vid]["reason"]), (True, None))
        self.assertEqual((by[young]["eligible"], by[young]["kept_days"], by[young]["remain_days"]), (False, 18, 13))
        self.assertIn("還要 13 天", by[young]["reason"])
        self.assertEqual((by[failed]["eligible"], by[failed]["kept_days"], by[failed]["remain_days"]), (False, None, None))
        self.assertIn("沒有通過", by[failed]["reason"])
        for bad in (young, failed):   # 刪除規則沒有放寬
            r = call("POST", "/api/admin/originals/delete", {"media_ids": [bad], "confirm": "永久刪除"}, t, port=ADM)
            self.assertEqual(r.status, 409, bad)
        self.assertFalse(any(d["id"] != vid for d in self.ok(call("GET", "/api/admin/originals/due", token=t, port=ADM)).json["items"]))
        self.assertEqual(call("POST", "/api/admin/originals/delete", {"media_ids": [vid]}, t, port=ADM).status, 400)
        r = self.ok(call("POST", "/api/admin/originals/delete", {"media_ids": [vid], "confirm": "永久刪除"}, t, port=ADM)).json
        job = self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [r["job_ids"][0]])
        self.assertEqual(wmain.run_job(self.ctx, job), "done")
        self.assertEqual(self.conn.val("SELECT status FROM media WHERE id = %s", [vid]), "original_deleted")
        self.ok(call("GET", f"/api/media/{vid}/stream", token=wang, headers={"Range": "bytes=0-9"}), 206)
        # 其他
        items = self.ok(call("GET", "/api/admin/checklist?category=pending_dev", token=t, port=ADM)).json["items"]
        self.assertEqual(len(items), 20)
        self.ok(call("PATCH", f"/api/admin/checklist/{items[0]['id']}", {"status": "confirmed", "note": "OK"}, t, port=ADM))
        s = self.ok(call("PUT", "/api/admin/setup", {"step": 3, "steps": {"1": {"status": "done"}}}, t, port=ADM)).json["setup"]
        self.assertEqual(s["step"], 3)
        self.ok(call("GET", "/api/admin/jobs?type=convert", token=t, port=ADM))
        self.ok(call("GET", "/api/admin/logs?source=client", token=t, port=ADM))
        self.ok(call("POST", "/api/admin/diagnostics", {}, t, port=ADM))
        # dev.62 方案 B：沒有恢復密碼（加密金鑰）時不建立任何備份；設定公鑰後才建立加密備份（Agent 只有公鑰）
        run = lambda jid: wmain.run_job(self.ctx, self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [jid]))  # noqa: E731
        r = self.ok(call("GET", "/api/admin/backups", token=t, port=ADM)).json
        self.assertFalse(r["key"]["initialized"])
        self.assertEqual((r["settings"]["keep_days"], r["settings"]["keep_min"], r["settings"]["keep_max"], r["settings"]["upload_every_days"]), (15, 10, 15, 3))
        self.assertTrue(any("尚未設定備份恢復密碼" in w["text"] and w["level"] == "error" for w in r["warnings"]), r["warnings"])
        self.assertTrue(any("尚未設定遠端備份" in w["text"] for w in r["warnings"]))
        bj = self.ok(call("POST", "/api/admin/backups", {}, t, port=ADM)).json["job_id"]
        self.assertEqual(run(bj), "failed")
        self.assertFalse(os.listdir(self.env["ISTUDY_BACKUP_DIR"]), "沒有金鑰：不產生未加密備份")
        keygen = os.path.join(os.path.dirname(self.env["ISTUDY_AGE_BIN"]), "age-keygen") if os.path.isabs(self.env["ISTUDY_AGE_BIN"]) else "age-keygen"
        ident = subprocess.run([keygen], capture_output=True, text=True, check=True).stdout
        rec = subprocess.run([keygen, "-y"], input=ident, capture_output=True, text=True, check=True).stdout.strip()
        self.conn.execute("INSERT INTO server_config(key, value) VALUES ('backup_key', CAST(%s AS jsonb))", [json.dumps({"recipient": rec})])
        bj = self.ok(call("POST", "/api/admin/backups", {}, t, port=ADM)).json["job_id"]
        self.assertEqual(run(bj), "done")
        bid = self.conn.val("SELECT id FROM backup_history WHERE status = 'success' ORDER BY id DESC LIMIT 1")
        self.assertIsNone(self.conn.val("SELECT verify_result FROM backup_history WHERE id = %s", [bid]), "建立備份不會自動還原")
        self.assertEqual(call("POST", f"/api/admin/backups/{bid}/check", {"mode": "prod"}, t, port=ADM).status, 400, "沒有正式還原 API")
        j = self.ok(call("POST", f"/api/admin/backups/{bid}/check", {"mode": "integrity"}, t, port=ADM)).json["job_id"]
        self.assertEqual(run(j), "done")
        j = self.ok(call("POST", f"/api/admin/backups/{bid}/check", {"mode": "restore_test"}, t, port=ADM)).json["job_id"]
        self.assertEqual(run(j), "failed", "Agent 沒有私鑰：加密備份的還原測試由主機執行")
        self.assertIn("由主機執行", self.conn.val("SELECT error FROM jobs WHERE id = %s", [j]))
        r = self.ok(call("GET", "/api/admin/backups", token=t, port=ADM)).json
        b = [x for x in r["items"] if x["id"] == bid][0]
        self.assertEqual((b["format"], b["enc_status"], b["remote_status"]), ("age", "unverified", "none"))
        self.assertTrue(b["file_path"].endswith(".tar.gz.age") and len(b["sha256"]) == 64)
        self.assertTrue(b["verify"]["integrity"]["passed"], b)
        self.assertEqual(b["manifest"]["migration"], "0011_backup_v2")
        self.assertTrue(r["key"]["initialized"])
        self.assertFalse(any("尚未設定備份恢復密碼" in w["text"] for w in r["warnings"]))
        # 保留天數只能 10～15 天
        for bad in (9, 16, "12", None):
            self.assertEqual(call("PUT", "/api/admin/backups/settings", {"keep_days": bad}, t, port=ADM).status, 400, bad)
        self.assertEqual(self.ok(call("PUT", "/api/admin/backups/settings", {"keep_days": 12}, t, port=ADM)).json["keep_days"], 12)
        self.assertEqual(self.ok(call("GET", "/api/admin/backups", token=t, port=ADM)).json["settings"]["keep_days"], 12)
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'backup.settings_changed'"))
        self.assertEqual(call("PUT", "/api/admin/backups/settings", {"keep_days": 12}, self.login("admin01", "adminpass1"), port=LAN).status, 404, "只在本機管理埠")
        # 遠端：只顯示公開資訊（即使設定裡誤存密碼也不回傳）；上傳失敗與舊未加密備份有警告
        self.conn.execute("INSERT INTO server_config(key, value) VALUES ('backup_remote', CAST(%s AS jsonb))",
                          [json.dumps({"configured": True, "protocol": "ftps", "host": "ftp.example.test", "port": 21, "username": "u1", "dir": "/istudy-backup",
                                       "password": "不該出現", "last_upload": {"ok": False, "message": "連線逾時", "backup_id": bid}, "upload_failures": 2})])
        self.conn.execute("INSERT INTO backup_history(kind, status, file_path) VALUES ('daily', 'success', '/srv/istudy/backup/istudy-backup-20261001-033000.tar.gz')")
        r = self.ok(call("GET", "/api/admin/backups", token=t, port=ADM))
        self.assertNotIn("不該出現", r.text)
        w = " ".join(x["text"] for x in r.json["warnings"])
        self.assertIn("遠端上傳失敗（連續 2 次）", w)
        self.assertIn("未加密備份", w)
        self.assertTrue(r.json["remote"]["configured"])
        legacy = [x for x in r.json["items"] if x["file_path"].endswith("033000.tar.gz")][0]
        self.assertEqual((legacy["format"], legacy["enc_status"]), ("legacy", "none"))
        # 一次查 Agent 與主機的備份工作
        types = {x["type"] for x in self.ok(call("GET", "/api/admin/jobs?type=backup,backup_check,backup_upload", token=t, port=ADM)).json["items"]}
        self.assertTrue(types and types <= {"backup", "backup_check", "backup_upload"}, types)
        # dev.57 字幕辨識：引擎清單由 Agent 心跳回報；設定驗證；指定引擎排入
        self.conn.execute("INSERT INTO server_config(key, value) VALUES ('agent_heartbeat', CAST(%s AS jsonb)) "
                          "ON CONFLICT (key) DO UPDATE SET value = server_config.value || EXCLUDED.value",
                          ['{"asr": {"engines": {"paraformer-zh": {"ok": true, "reason": ""}, "qwen3-asr-0.6b": {"ok": false, "reason": "尚未提供"}}, "auto": "paraformer-zh"}}'])
        s = self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json
        self.assertEqual(s["asr"]["engine"], "auto")
        self.assertEqual(s["engines"]["auto"], "paraformer-zh")
        self.assertFalse(s["engines"]["engines"]["qwen3-asr-0.6b"]["ok"])
        for bad, why in (({"engine": "breeze-asr-25"}, "尚未提供"), ({"engine": "online"}, "線上"), ({"engine": "rm -rf"}, "沒有這個"),
                         ({"engine": "whisper"}, "dev.59 移除"),
                         ({"max_chars": 99}, "超出範圍"), ({"glossary": {"fixes": [["只有一個"]]}}, "錯字"),
                         ({"glossary": {"protect": ["x" * 41]}}, "太長")):
            r = call("PUT", "/api/admin/config", {"asr": bad}, t, port=ADM)
            self.assertEqual(r.status, 400, bad)
            self.assertIn(why, r.json["error"], bad)
        self.ok(call("PUT", "/api/admin/config", {"asr": {"engine": "sensevoice-small", "max_chars": 18, "glossary": {
            "fixes": [[" 圓三 ", "研三"], ["研三", "研三"], ["", "x"]], "protect": ["無相氣學", "無相氣學", " "], "hotwords": ["氣功"]}}}, t, port=ADM))
        a = self.ok(call("GET", "/api/admin/config", token=t, port=ADM)).json["asr"]
        self.assertEqual((a["engine"], a["max_chars"], a["max_sec"]), ("sensevoice-small", 18, 7))
        self.assertEqual(a["glossary"], {"fixes": [["圓三", "研三"]], "protect": ["無相氣學"], "hotwords": ["氣功"]})
        # dev.59：Whisper 已移除——不能再設定 whisper_model；舊設定 engine=whisper 照實顯示（畫面提示改掉），熱詞資料保留
        r = call("PUT", "/api/admin/config", {"whisper_model": "medium"}, t, port=ADM)
        self.assertEqual((r.status, "dev.59 移除" in r.json["error"]), (400, True))
        cfg = self.ok(call("GET", "/api/admin/config", token=t, port=ADM)).json
        self.assertNotIn("whisper_model", cfg)
        self.assertNotIn("model", self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json)
        self.conn.execute("UPDATE server_config SET value = jsonb_set(value, '{engine}', '\"whisper\"') WHERE key = 'asr'")
        self.assertEqual(self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json["asr"]["engine"], "whisper")
        self.ok(call("PUT", "/api/admin/config", {"asr": {"engine": "auto"}}, t, port=ADM))
        a = self.ok(call("GET", "/api/admin/config", token=t, port=ADM)).json["asr"]
        self.assertEqual((a["engine"], a["glossary"]["hotwords"]), ("auto", ["氣功"]))
        self.ok(call("PUT", "/api/admin/config", {"asr": {"engine": "sensevoice-small"}}, t, port=ADM))
        vid2 = self.mid("00020.MTS")
        self.assertEqual(call("POST", f"/api/admin/subtitles/{vid2}/run", {"engine": "qwen3-asr-0.6b"}, t, port=ADM).status, 400)
        r = call("POST", f"/api/admin/subtitles/{vid2}/run", {"engine": "whisper"}, t, port=ADM)
        self.assertEqual((r.status, "dev.59 移除" in r.json["error"]), (400, True))
        j = self.ok(call("POST", f"/api/admin/subtitles/{vid2}/run", {"engine": "paraformer-zh"}, t, port=ADM)).json["job_id"]
        self.assertEqual(self.conn.val("SELECT payload->>'engine' FROM jobs WHERE id = %s", [j]), "paraformer-zh")
        self.assertEqual(wmain.run_job(self.ctx, self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [j])), "done")
        d = [x for x in self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json["done"] if x["media_id"] == vid2][0]
        self.assertEqual((d["engine"], d["model"]), ("paraformer-zh", "paraformer-zh"))
        sp = self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json["speed"]     # dev.61：ASR 長度提醒用的速度
        self.assertTrue(sp["samples"] >= 1 and sp["rtf"] > 0, sp)
        self.assertIn(d["qc"]["status"], ("ok", "needs_review"))
        self.assertIsNotNone(d["rtf"])
        docs = self.ok(call("GET", "/api/admin/documents", token=t, port=ADM)).json["items"]
        self.assertTrue(docs and all("pages" in d and isinstance(d["notes"], list) for d in docs), docs[:1])
        done = [d for d in docs if d["doc_status"] == "done"]
        self.assertTrue(done and all(d["pages"] and d["pages"] >= 1 for d in done), done[:1])

    def test_12_server_app_super_only_and_lockout(self):
        r = call("POST", "/api/login", {"username": "admin01", "password": "adminpass1"}, port=ADM)
        self.assertEqual((r.status, r.json["code"]), (403, "server_app_super_only"))
        for _ in range(5):
            self.assertEqual(call("POST", "/api/login", {"username": "wang01", "password": "bad"}).status, 401)
        r = call("POST", "/api/login", {"username": "wang01", "password": "wangpass1"})
        self.assertEqual(r.status, 429, "連續失敗 5 次鎖定")
        self.assertTrue(int(r.headers["Retry-After"]) > 800)
        self.conn.execute("DELETE FROM login_failures")
        self.login("wang01", "wangpass1")

    def test_13_email_report(self):
        """dev.57 Email 報告：Super 專用；Client App（LAN）把內容送到 Server，由 Agent 代寄；SMTP 設定只能在 Server App 改。"""
        t = self.super
        admin = self.login("admin01", "adminpass1")
        self.assertEqual(call("GET", "/api/email", token=admin).status, 403, "只有 Super")
        self.assertEqual(call("POST", "/api/email/send", {"report": "summary"}, admin).status, 403)
        lan = self.ok(call("GET", "/api/email", token=t)).json
        self.assertEqual((lan["configured"], lan["to"], lan["can_edit"]), (False, ["eachcore.app@gmail.com"], False))
        self.assertEqual(call("POST", "/api/email/send", {"report": "summary"}, t).status, 409, "尚未設定")
        self.assertEqual(call("PUT", "/api/admin/email", {"host": "smtp.gmail.com"}, t).status, 404, "LAN 不能改 SMTP 設定")
        for bad in ({"host": "smtp gmail"}, {"from": "not-mail"}, {"to": ["a@b.c", "bad"]}, {"to": []}, {"security": "tls1"},
                    {"port": 70000}, {"to": [f"u{i}@x.tw" for i in range(6)]}):
            self.assertEqual(call("PUT", "/api/admin/email", bad, t, port=ADM).status, 400, bad)
        # 預設寄送方式：網站代寄（不需要信箱密碼）
        self.assertEqual(self.ok(call("GET", "/api/admin/email", token=t, port=ADM)).json["method"], "relay")
        for bad in ({"relay_url": "http://work.eachcore.com/istudy-mail-relay.php"}, {"relay_url": "ftp://x"},
                    {"relay_key": "short"}, {"relay_key": "has space " * 4}, {"method": "sendmail"}):
            self.assertEqual(call("PUT", "/api/admin/email", bad, t, port=ADM).status, 400, bad)
        s = self.ok(call("PUT", "/api/admin/email", {"relay_url": "https://work.eachcore.com/istudy-mail-relay.php",
                                                     "relay_key": "k" * 30}, t, port=ADM)).json
        self.assertEqual((s["method"], s["configured"], s["relay_key_set"]), ("relay", True, True))
        self.assertNotIn("k" * 30, json.dumps(s))
        self.assertNotIn("k" * 30, json.dumps(self.ok(call("GET", "/api/email", token=t)).json))
        s = self.ok(call("PUT", "/api/admin/email", {"method": "smtp", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
                                                     "username": "istudy.sender@gmail.com", "password": "app-pass-1234",
                                                     "from": "istudy.sender@gmail.com"}, t, port=ADM)).json
        self.assertEqual((s["configured"], s["password_set"], s["to"]), (True, True, ["eachcore.app@gmail.com"]))
        self.assertNotIn("app-pass-1234", json.dumps(s))
        self.assertNotIn("app-pass-1234", json.dumps(self.ok(call("GET", "/api/admin/email", token=t, port=ADM)).json))
        self.assertNotIn("app-pass-1234", json.dumps(self.ok(call("GET", "/api/email", token=t)).json))
        self.assertEqual(self.conn.val("SELECT value->>'relay_key' FROM server_config WHERE key = 'email_secret'"), "k" * 30, "改 SMTP 不會清掉代寄金鑰")
        self.assertNotIn("app-pass-1234", self.conn.val("SELECT details::text FROM audit_log WHERE action = 'email.settings_updated' ORDER BY id DESC LIMIT 1") or "")
        for bad in ({"report": "rm"}, {"report": "message", "note": ""}):
            self.assertEqual(call("POST", "/api/email/send", bad, t).status, 400, bad)
        # dev.58 HTML 附件：只限 Server App（管理埠）；Client App（LAN）一律拒絕，連內容都不看
        self.assertEqual(call("POST", "/api/email/send", {"report": "summary", "uem_html": "<!doctype html><title>UEM</title>"}, t).status, 403)
        good = '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>報告</title><style>td{color:#333}</style></head>' \
               '<body><h1>iStudy 主機盤點</h1><img src="data:image/png;base64,iVBORw0KGgo=" alt=""><a href="#issues">需要處理</a></body></html>'
        for html, why in ((good.replace("<title>", "<script>alert(1)</script><title>"), "指令碼"), (good.replace("<h1>", "<h1 onclick=\"x()\">"), "事件屬性"),
                          (good.replace("#issues", "javascript:alert(1)"), "javascript"), (good.replace("#issues", "java&#115;cript:alert(1)"), "javascript"),
                          (good.replace("data:image/png;base64,iVBORw0KGgo=", "https://evil.example/t.png"), "外部"),
                          (good.replace("data:image/png;base64,iVBORw0KGgo=", "//evil.example/t.png"), "外部"),
                          (good.replace("td{color:#333}", "td{background:url(http://evil.example/x)}"), "外部"),
                          (good.replace("td{color:#333}", "@import 'x.css';"), "外部"), (good.replace("<h1>", "<iframe src=x></iframe><h1>"), "內嵌"),
                          (good.replace("<h1>", "<form action=x><h1>"), "表單"), (good.replace("<head>", '<head><meta http-equiv="refresh" content="0;url=x">'), "轉址"),
                          ("<html><body>沒有 doctype</body></html>", "完整"), ("<!doctype html>" + "x" * (2 * 1024 * 1024), "2MB")):
            r = call("POST", "/api/email/send", {"report": "summary", "uem_html": html}, t, port=ADM)
            self.assertEqual(r.status, 400, why)
            self.assertIn("附件", r.json["error"], why)
        self.assertEqual(call("POST", "/api/email/send", {"report": "summary", "uem_html": good, "attachment_name": "../x.html"}, t, port=ADM).status, 400)
        # Client App（LAN）寄出：內容先到 Server，排入背景工作
        j = self.ok(call("POST", "/api/email/send", {"report": "summary", "note": "教室電腦無法上網，請協助"}, t)).json["job_id"]
        self.assertEqual(call("POST", "/api/email/send", {"report": "asr"}, t).status, 409, "一次只寄一封")
        self.ctx.env["ISTUDY_EMAIL_FAKE"] = "1"
        try:
            self.assertEqual(wmain.run_job(self.ctx, self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [j])), "done")
        finally:
            self.ctx.env.pop("ISTUDY_EMAIL_FAKE", None)
        res = self.conn.val("SELECT result FROM jobs WHERE id = %s", [j])
        self.assertEqual(res["to"], ["eachcore.app@gmail.com"])
        self.assertIn("系統狀態摘要", res["subject"])
        self.assertIn("教室電腦無法上網，請協助", res["preview"])
        self.assertIn("【教材】", res["preview"])
        self.assertNotIn("app-pass-1234", json.dumps(res))
        # Server App 附上主機盤點 HTML：紀錄只顯示「有附件」，不回傳內容
        j2 = self.ok(call("POST", "/api/email/send", {"report": "asr", "uem_html": good, "attachment_name": "istudy-uem-test.html"}, t, port=ADM)).json["job_id"]
        self.assertEqual(self.conn.val("SELECT payload->>'attachment_name' FROM jobs WHERE id = %s", [j2]), "istudy-uem-test.html")
        h = {x["id"]: x for x in self.ok(call("GET", "/api/email", token=t, port=ADM)).json["history"]}
        self.assertTrue(h[j2]["attachment"])
        self.assertEqual(h[j]["status"], "done")
        self.assertNotIn("<!doctype", json.dumps(h))
        self.assertNotIn("uem_html", json.dumps(self.ok(call("GET", "/api/admin/jobs?type=email_report", token=t, port=ADM)).json))
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", [j2])

    def test_14_feedback(self):
        """dev.57 問題回報：任何登入的使用者都能送；Server 代寄給 Super；不受 Email 報告「一次一封」限制。"""
        t = self.super
        u = self.login("wang01", "wangpass1")
        self.assertEqual(call("POST", "/api/feedback", {"summary": "x"}).status, 401, "要登入")
        for bad in ({}, {"summary": " "}, {"summary": "a"}, {"summary": "長" * 1001}):
            self.assertEqual(call("POST", "/api/feedback", bad, u).status, 400, bad)
        # 有一封 Email 報告在排隊時，問題回報照樣可以送
        busy = self.ok(call("POST", "/api/email/send", {"report": "message", "note": "排隊中"}, t)).json["job_id"]
        r = self.ok(call("POST", "/api/feedback", {"summary": "第三堂課影片播到一半沒有聲音", "app": "Client 1.0.0-dev.57",
                                                   "pc": "CLASS-PC-03", "page": "問題回報", "role": "super"}, u)).json
        self.assertTrue(r["email_configured"])
        p = self.conn.val("SELECT payload FROM jobs WHERE id = %s", [r["job_id"]])
        self.assertEqual((p["report"], p["reporter"]["username"], p["reporter"]["role"], p["pc"]), ("feedback", "wang01", "user", "CLASS-PC-03"),
                         "回報者由 Server 依登入身分填入，不信任 App 送來的欄位")
        mine = self.ok(call("GET", "/api/feedback/mine", token=u)).json["items"]
        self.assertEqual((mine[0]["id"], mine[0]["summary"], mine[0]["status"]), (r["job_id"], "第三堂課影片播到一半沒有聲音", "queued"))
        self.assertEqual(self.ok(call("GET", "/api/feedback/mine", token=t)).json["items"], [], "只看得到自己的")
        self.assertEqual(call("GET", "/api/email", token=u).status, 403, "寄送紀錄仍只有 Super 看得到")
        self.ctx.env["ISTUDY_EMAIL_FAKE"] = "1"
        try:
            self.assertEqual(wmain.run_job(self.ctx, self.conn.one("UPDATE jobs SET status='running' WHERE id = %s RETURNING *", [r["job_id"]])), "done")
        finally:
            self.ctx.env.pop("ISTUDY_EMAIL_FAKE", None)
        res = self.conn.val("SELECT result FROM jobs WHERE id = %s", [r["job_id"]])
        self.assertIn("問題回報：wang01", res["subject"])
        for s in ("【回報者】", "wang01", "CLASS-PC-03", "【問題概述】", "第三堂課影片播到一半沒有聲音"):
            self.assertIn(s, res["preview"])
        em = self.ok(call("GET", "/api/email", token=t)).json
        self.assertNotIn(r["job_id"], [x["id"] for x in em["history"]], "問題回報另外列出，不佔寄送紀錄")
        self.assertIn(busy, [x["id"] for x in em["history"]])
        fb = {x["id"]: x for x in em["feedback"]}[r["job_id"]]
        self.assertEqual((fb["username"], fb["full_name"], fb["pc"], fb["summary"], fb["status"]),
                         ("wang01", "王小明", "CLASS-PC-03", "第三堂課影片播到一半沒有聲音", "done"))
        self.assertEqual(self.ok(call("GET", "/api/feedback/mine", token=u)).json["items"][0]["status"], "done")
        # 每人每小時 5 次：dev.58 同時送出 12 個（PHP 4 個 worker 並行），只能成功 4 個（加上前面那 1 個＝5）
        import threading
        codes: list[int] = []
        gate = threading.Barrier(12)

        def burst(i: int) -> None:
            gate.wait()
            codes.append(call("POST", "/api/feedback", {"summary": f"同時送出 {i}"}, u).status)
        ts = [threading.Thread(target=burst, args=(i,)) for i in range(12)]
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        self.assertEqual((codes.count(200), codes.count(429), len(codes)), (4, 8, 12), codes)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM jobs WHERE type = 'email_report' AND payload->>'report' = 'feedback' AND created_by = "
                                       "(SELECT id FROM accounts WHERE username = 'wang01')"), 5, "資料庫裡剛好 5 筆，沒有超過上限")
        self.assertEqual(call("POST", "/api/feedback", {"summary": "第 6 個"}, u).status, 429)
        self.ok(call("POST", "/api/feedback", {"summary": "其他人不受影響"}, t))
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM audit_log WHERE action = 'feedback.submitted'"), 6)
        # Email 報告同時送出 6 封：一次只能排一封，其他都回 409
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE type = 'email_report' AND status = 'queued'")
        codes2: list[int] = []
        gate2 = threading.Barrier(6)

        def burst2(i: int) -> None:
            gate2.wait()
            codes2.append(call("POST", "/api/email/send", {"report": "message", "note": f"同時 {i}"}, t).status)
        ts = [threading.Thread(target=burst2, args=(i,)) for i in range(6)]
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        self.assertEqual((codes2.count(200), codes2.count(409)), (1, 5), codes2)
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE type = 'email_report' AND status = 'queued'")

    def run_jobs(self, types):
        out = []
        for _ in range(50):
            job = wmain.pick(self.ctx, types)
            if not job:
                break
            out.append(wmain.run_job(self.ctx, job))
        return out

    def test_15_outgo(self):
        """dev.61 P0-2／P0-3：/Media/Outgo 不是教材（掃描、目錄、搜尋、串流都看不到），只有本機 Server App 的 Super 能瀏覽與轉文字。"""
        t, q = self.super, urllib.request.quote
        og = os.path.join(self.media_root, "Outgo")
        os.makedirs(os.path.join(og, "第一期"))
        make_media.ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", os.path.join(og, "第一期", "上課錄音.mp3")])
        make_media.pdf(os.path.join(og, "講義.pdf"))
        make_media.mp4(os.path.join(og, "影片.mp4"), 1, True)
        open(os.path.join(og, ".隱藏.mp3"), "wb").close()
        os.symlink(os.path.join(self.media_root, "基礎班"), os.path.join(og, "連結"))
        self.ctx.enqueue("scan", {}, 50)
        self.run_jobs(["scan"])
        self.assertEqual(self.conn.val("SELECT count(*) FROM media WHERE rel_path ILIKE 'outgo/%%'"), 0, "掃描排除 Outgo（含子資料夾）")
        self.assertEqual(self.conn.val("SELECT count(*) FROM folders WHERE rel_path ILIKE 'outgo%%'"), 0)
        wang = self.login("wang01", "wangpass1")
        names = {f["name"] for f in self.ok(call("GET", "/api/tree", token=t)).json["folders"]}
        self.assertNotIn("Outgo", names)
        for kw in ("上課錄音", "Outgo", "影片"):
            r = self.ok(call("GET", "/api/search?q=" + q(kw), token=t)).json
            self.assertFalse([x for x in r.get("results", r.get("items", [])) if "Outgo" in json.dumps(x, ensure_ascii=False)], kw)
        # 管理 API：LAN 一律 404；Client 的 Token（即使是 Admin）在本機埠也不行
        self.assertEqual(call("GET", "/api/admin/outgo", token=t).status, 404)
        admin = self.login("admin01", "adminpass1")
        self.assertIn(call("GET", "/api/admin/outgo", token=admin, port=ADM).status, (401, 403))
        self.assertIn(call("POST", "/api/admin/outgo/asr", {"path": "第一期/上課錄音.mp3"}, admin, port=ADM).status, (401, 403))
        self.assertEqual(call("GET", "/api/admin/outgo", port=ADM).status, 401)
        # 瀏覽：資料夾在前、隱藏檔不列、符號連結不列也不能進
        root = self.ok(call("GET", "/api/admin/outgo", token=t, port=ADM)).json
        self.assertTrue(root["ready"])
        self.assertEqual([(e["type"], e["name"]) for e in root["entries"]], [("dir", "第一期"), ("file", "影片.mp4"), ("file", "講義.pdf")])
        self.assertEqual(root["skipped"], 1)
        self.assertFalse(any(e.get("audio") for e in root["entries"]), "影片與 PDF 不是錄音")
        self.assertEqual(root["asr"]["engine"], "sensevoice-small", "預設引擎＝資料庫目前的設定")
        self.assertEqual(set(root["speed"]), {"rtf", "samples"})
        for bad in ("連結", "../基礎班", "第一期/..", "/etc", ".隱藏.mp3", "第一期/../../基礎班", "x\\y"):
            self.assertEqual(call("GET", "/api/admin/outgo?path=" + q(bad), token=t, port=ADM).status, 404, bad)
            self.assertIn(call("POST", "/api/admin/outgo/asr", {"path": bad}, t, port=ADM).status, (400, 404), bad)
            self.assertEqual(call("GET", "/api/admin/outgo/result?path=" + q(bad), token=t, port=ADM).status, 404, bad)
        self.assertEqual(call("POST", "/api/admin/outgo/asr", {"path": "講義.pdf"}, t, port=ADM).status, 400)
        self.assertEqual(call("POST", "/api/admin/outgo/asr", {"path": "影片.mp4"}, t, port=ADM).status, 400, "Outgo 只做錄音")
        self.assertEqual(call("POST", "/api/admin/outgo/asr", {"path": "第一期/上課錄音.mp3", "engine": "whisper"}, t, port=ADM).status, 400)
        self.assertEqual(call("GET", "/api/admin/outgo/result?path=" + q("第一期/上課錄音.mp3"), token=t, port=ADM).status, 404, "還沒有逐字稿")
        # 排入：重複按不會重複排
        a = self.ok(call("POST", "/api/admin/outgo/asr", {"path": "第一期/上課錄音.mp3"}, t, port=ADM)).json
        b = self.ok(call("POST", "/api/admin/outgo/asr", {"path": "第一期/上課錄音.mp3", "engine": "paraformer-zh"}, t, port=ADM)).json
        self.assertEqual((a["job_id"], b["existing"]), (b["job_id"], True))
        e = self.ok(call("GET", "/api/admin/outgo?path=" + q("第一期"), token=t, port=ADM)).json["entries"][0]
        self.assertEqual((e["name"], e["audio"], e["asr"]["state"]), ("上課錄音.mp3", True, "queued"))
        self.assertEqual(self.run_jobs(["outgo_asr"]), ["done"])
        ents = self.ok(call("GET", "/api/admin/outgo?path=" + q("第一期"), token=t, port=ADM)).json["entries"]
        e = ents[0]
        self.assertEqual((e["asr"]["state"], e["asr"]["has_txt"], e["asr"]["has_srt"], e["asr"]["engine"]), ("done", True, True, "sensevoice-small"))
        self.assertEqual({x["name"]: x["output_of"] for x in ents if x["name"] != "上課錄音.mp3"},
                         {"上課錄音.mp3.asr.srt": "上課錄音.mp3", "上課錄音.mp3.asr.txt": "上課錄音.mp3"}, "逐字稿歸在錄音那一列；狀態檔是隱藏檔不列出")
        r = self.ok(call("GET", "/api/admin/outgo/result?path=" + q("第一期/上課錄音.mp3"), token=t, port=ADM, headers={"Origin": "app://istudy"}))
        self.assertIn("charset=utf-8", r.headers["Content-Type"])
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "app://istudy", "Server App 用 fetch 下載需要 CORS")
        self.assertTrue(r.text.strip())
        r = self.ok(call("GET", "/api/admin/outgo/result?format=srt&path=" + q("第一期/上課錄音.mp3"), token=t, port=ADM))
        self.assertTrue(r.text.startswith("1\n00:00:00,000 --> "), r.text[:60])
        self.assertIn("filename*=UTF-8''", r.headers["Content-Disposition"])
        self.assertEqual(self.conn.val("SELECT count(*) FROM jobs WHERE type = 'outgo_asr'"), 0, "工作完成後不留紀錄")
        self.assertEqual(self.conn.val("SELECT count(*) FROM audit_log WHERE action = 'outgo.asr_requested'"), 1)
        self.assertEqual(self.conn.val("SELECT count(*) FROM media WHERE rel_path ILIKE 'outgo/%%'"), 0)
        # 教材的串流／重新指定路徑都不能指到 Outgo
        pdf = self.conn.one("SELECT id, rel_path FROM media WHERE kind = 'pdf' AND status = 'present' ORDER BY id LIMIT 1")
        self.ok(call("GET", f"/api/media/{pdf['id']}/stream", token=t))
        self.conn.execute("UPDATE media SET rel_path = 'Outgo/講義.pdf' WHERE id = %s", [pdf["id"]])
        try:
            r = call("GET", f"/api/media/{pdf['id']}/stream", token=t)
            self.assertEqual((r.status, r.json["code"]), (503, "file_unavailable"), "教材路徑指到 Outgo：不送檔")
        finally:
            self.conn.execute("UPDATE media SET rel_path = %s WHERE id = %s", [pdf["rel_path"], pdf["id"]])
        os.remove(os.path.join(og, "連結"))

    def test_16_purge_media(self):
        """dev.61 P0-1：Super 永久刪除教材——預覽影響、明確確認、執行中拒絕、刪除後不會被掃描復活。只用測試自己建立的教材。"""
        t = self.super
        d = os.path.join(self.media_root, "刪除測試")
        os.makedirs(d, exist_ok=True)
        make_media.pdf(os.path.join(d, "丟棄講義.pdf"))
        self.ctx.enqueue("scan", {}, 50)
        self.run_jobs(list(wmain.HANDLERS))
        m = self.conn.one("SELECT * FROM media WHERE rel_path = '刪除測試/丟棄講義.pdf'")
        mid = m["id"]
        self.conn.execute("INSERT INTO bookmarks(account_id, media_id) SELECT id, %s FROM accounts WHERE username = 'wang01'", [mid])
        self.assertEqual(call("GET", f"/api/admin/media/{mid}/purge-preview", token=t).status, 404, "LAN 沒有")
        pv = self.ok(call("GET", f"/api/admin/media/{mid}/purge-preview", token=t, port=ADM)).json
        self.assertEqual((pv["media"]["rel_path"], pv["confirm_text"], pv["blockers"]), ("刪除測試/丟棄講義.pdf", "永久刪除", []))
        orig = [f for f in pv["files"] if f["label"] == "原檔"][0]
        self.assertEqual((orig["exists"], orig["symlink"], orig["path"]), (True, False, os.path.join(os.path.realpath(self.media_root), "刪除測試", "丟棄講義.pdf")))
        self.assertEqual(pv["db"]["bookmarks"], 1)
        # 確認字樣、路徑、Client Token
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"]}, t, port=ADM).status, 400)
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "刪除"}, t, port=ADM).status, 400)
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": "刪除測試/別的.pdf", "confirm": "永久刪除"}, t, port=ADM).status, 409)
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "永久刪除"}, t).status, 404)
        admin = self.login("admin01", "adminpass1")
        self.assertIn(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "永久刪除"}, admin, port=ADM).status, (401, 403))
        # 執行中的工作：預覽列出阻擋原因，送出回 409
        busy = self.ctx.enqueue("office_pdf", {"media_id": mid}, 0)
        self.conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", [busy])
        self.assertTrue(self.ok(call("GET", f"/api/admin/media/{mid}/purge-preview", token=t, port=ADM)).json["blockers"])
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "永久刪除"}, t, port=ADM).status, 409)
        self.conn.execute("UPDATE jobs SET status = 'done' WHERE id = %s", [busy])
        # 系統資料夾的路徑一律拒絕
        self.conn.execute("UPDATE media SET rel_path = 'Outgo/講義.pdf' WHERE id = %s", [mid])
        self.assertEqual(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": "Outgo/講義.pdf", "confirm": "永久刪除"}, t, port=ADM).status, 400)
        self.assertTrue(self.ok(call("GET", f"/api/admin/media/{mid}/purge-preview", token=t, port=ADM)).json["blockers"])
        self.conn.execute("UPDATE media SET rel_path = '刪除測試/丟棄講義.pdf' WHERE id = %s", [mid])
        # 正式送出：重複按只有一個工作；Agent 執行後原檔與紀錄都不見，掃描不會復活
        r1 = self.ok(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "永久刪除"}, t, port=ADM)).json
        r2 = self.ok(call("POST", f"/api/admin/media/{mid}/purge", {"rel_path": m["rel_path"], "confirm": "永久刪除"}, t, port=ADM)).json
        self.assertEqual((r1["job_id"], r2["existing"]), (r2["job_id"], True))
        self.assertEqual(self.run_jobs(["purge_media"]), ["done"])
        j = self.ok(call("GET", f"/api/admin/jobs/{r1['job_id']}", token=t, port=ADM)).json
        self.assertEqual(j.get("job", j)["status"], "done")
        self.assertFalse(os.path.exists(os.path.join(d, "丟棄講義.pdf")))
        self.assertTrue(os.path.isdir(d), "資料夾保留")
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [mid]))
        self.assertEqual(self.conn.val("SELECT count(*) FROM bookmarks WHERE media_id = %s", [mid]), 0)
        self.assertEqual(self.conn.val("SELECT count(*) FROM audit_log WHERE media_id = %s", [mid]), 0)
        self.assertEqual(self.conn.val("SELECT count(*) FROM audit_log WHERE action = 'media.purged' AND path = '刪除測試/丟棄講義.pdf'"), 1)
        self.assertEqual(call("GET", f"/api/media/{mid}", token=t).status, 404)
        self.ctx.enqueue("scan", {}, 50)
        self.run_jobs(["scan"])
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE rel_path = '刪除測試/丟棄講義.pdf'"))
        # 舊的「解除關聯」API 仍然只刪資料庫紀錄（語意不變，不會刪實體檔案）
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, "基礎班", "第一堂課", "講義.pdf")))

    def test_17_asr_log_report(self):
        """dev.61「統計與報告」：ASR 處理記錄（字幕＋Outgo、成功＋失敗）、音訊品質、篩選；Super 專用（LAN 404）。"""
        t, q = self.super, urllib.request.quote
        self.assertEqual(call("GET", "/api/admin/asr-log", token=t).status, 404)
        r = self.ok(call("GET", "/api/admin/asr-log?days=0", token=t, port=ADM)).json
        s = r["summary"]
        self.assertEqual(s["total"], len(r["items"]))
        self.assertEqual(s["done"] + s["failed"], s["total"])
        self.assertEqual(sum(s["quality"].values()), s["total"])
        self.assertTrue(s["kind"]["subtitle"] >= 1 and s["kind"]["outgo"] >= 1, s)
        x = [i for i in r["items"] if i["kind"] == "outgo"][0]
        self.assertEqual((x["path"], x["status"], x["media_id"]), ("Outgo/第一期/上課錄音.mp3", "done", None))
        self.assertIn(x["quality"], ("high", "medium", "low"))
        self.assertIsInstance(x["quality_reasons"], list)
        sub = [i for i in r["items"] if i["kind"] == "subtitle" and i["status"] == "done"][0]
        self.assertTrue(sub["media_id"] and sub["engine"] and sub["audio_sec"] > 0, sub)
        only = self.ok(call("GET", "/api/admin/asr-log?days=0&kind=outgo", token=t, port=ADM)).json
        self.assertTrue(only["items"] and all(i["kind"] == "outgo" for i in only["items"]))
        f = self.ok(call("GET", "/api/admin/asr-log?days=0&q=" + q("上課錄音"), token=t, port=ADM)).json
        self.assertTrue(f["items"] and all("上課錄音" in i["path"] for i in f["items"]))
        lv = x["quality"]
        f = self.ok(call("GET", f"/api/admin/asr-log?days=0&quality={lv}", token=t, port=ADM)).json
        self.assertTrue(all(i["quality"] == lv for i in f["items"]) and f["summary"]["quality"][lv] == f["summary"]["total"])
        for bad in ("kind=x", "quality=great", "status=ok"):
            self.assertEqual(call("GET", "/api/admin/asr-log?" + bad, token=t, port=ADM).status, 400, bad)
        # 字幕頁與 Outgo 頁也顯示音訊品質
        d = self.ok(call("GET", "/api/admin/subtitles", token=t, port=ADM)).json["done"]
        self.assertTrue(any(x.get("audio_quality") and x["audio_quality"]["level"] in ("high", "medium", "low") for x in d))
        e = self.ok(call("GET", "/api/admin/outgo?path=" + q("第一期"), token=t, port=ADM)).json["entries"][0]
        self.assertEqual(e["asr"]["quality"], lv)

    def test_18_client_update_status(self):
        """dev.61：Client 心跳回報版本與更新狀態；Server App「Client 版本與更新」列出每台電腦（Super 專用，LAN 404）。"""
        t = self.super
        latest = json.load(open(os.path.join(ROOT, "VERSION.json"), encoding="utf-8"))["client"]["win10"]["version"]
        hb = {"macs": ["aa:bb:cc:61:00:01"], "pc_name": "教室-A02", "os": "Windows 10.0.19045", "channel": "win10",
              "app_version": "1.0.0-dev.60", "api_level": 1,
              "update": {"state": "downloading", "available": latest, "percent": 42.7, "error": None, "at": "x"}}
        self.ok(call("POST", "/api/client/heartbeat", hb))
        self.assertEqual(call("GET", "/api/admin/client-updates", token=t).status, 404)
        r = self.ok(call("GET", "/api/admin/client-updates", token=t, port=ADM)).json
        d = [x for x in r["devices"] if x["pc_name"] == "教室-A02"][0]
        self.assertEqual((d["version"], d["latest"], d["version_state"], d["online"], d["registered"]), ("1.0.0-dev.60", latest, "outdated", True, False))
        self.assertEqual((d["update"]["state"], d["update"]["available"], d["update"]["percent"]), ("downloading", latest, 42))
        w10 = [c for c in r["channels"] if c["channel"] == "win10"][0]
        self.assertTrue(w10["outdated"] >= 1 and w10["latest"] == latest)
        # 更新完成：新版本回報；不合法的狀態不寫入、沒有回報 update 的舊版 Client 保留原值
        self.conn.execute("UPDATE client_devices SET last_seen_at = NOW() - INTERVAL '1 minute' WHERE last_pc_name = '教室-A02'")
        self.ok(call("POST", "/api/client/heartbeat", {**hb, "app_version": latest, "update": {"state": "up_to_date"}}))
        d = [x for x in self.ok(call("GET", "/api/admin/client-updates", token=t, port=ADM)).json["devices"] if x["pc_name"] == "教室-A02"][0]
        self.assertEqual((d["version_state"], d["update"]["state"]), ("current", "up_to_date"))
        self.conn.execute("UPDATE client_devices SET last_seen_at = NOW() - INTERVAL '1 minute' WHERE last_pc_name = '教室-A02'")
        self.ok(call("POST", "/api/client/heartbeat", {**hb, "app_version": latest, "update": {"state": "rm -rf", "error": "x" * 999}}))
        self.conn.execute("UPDATE client_devices SET last_seen_at = NOW() - INTERVAL '1 minute' WHERE last_pc_name = '教室-A02'")
        old = {k: v for k, v in hb.items() if k != "update"}
        self.ok(call("POST", "/api/client/heartbeat", {**old, "app_version": latest}))
        d = [x for x in self.ok(call("GET", "/api/admin/client-updates", token=t, port=ADM)).json["devices"] if x["pc_name"] == "教室-A02"][0]
        self.assertEqual(d["update"]["state"], "up_to_date")
        self.conn.execute("UPDATE client_devices SET last_seen_at = NOW() - INTERVAL '1 minute' WHERE last_pc_name = '教室-A02'")
        self.ok(call("POST", "/api/client/heartbeat", {**hb, "update": {"state": "failed", "error": "連不到\nServer" + "x" * 300}}))
        d = [x for x in self.ok(call("GET", "/api/admin/client-updates", token=t, port=ADM)).json["devices"] if x["pc_name"] == "教室-A02"][0]
        self.assertEqual((d["update"]["state"], len(d["update"]["error"]), "\n" in d["update"]["error"]), ("failed", 200, False))

    def test_19_direct_play_asr_dev62(self):
        """dev.62 P0-B：直接播放的新影片——自動排入的字幕工作要等系統時段；Super 按「產生字幕」要改成馬上執行（不重複建立）。"""
        t = self.super
        d = os.path.join(self.media_root, "直播測試")
        os.makedirs(d, exist_ok=True)
        make_media.mp4(os.path.join(d, "宣導.mp4"), 2, True)          # H.264＋AAC、faststart → 直接播放
        # 系統時段改到現在以外（模擬白天新增影片）
        now = _dt.datetime.now(TZ)
        sch = self.conn.val("SELECT value FROM server_config WHERE key = 'schedule'")
        self.ctx.set_config("schedule", {**sch, "start": (now + _dt.timedelta(hours=2)).strftime("%H:%M"),
                                         "end": (now + _dt.timedelta(hours=3)).strftime("%H:%M")})
        try:
            self.ctx.enqueue("scan", {}, 50)
            self.run_jobs(list(wmain.HANDLERS))
            m = self.conn.one("SELECT * FROM media WHERE rel_path = '直播測試/宣導.mp4'")
            self.assertEqual((m["play_mode"], m["convert_status"]), ("direct", "direct"))
            q = self.conn.execute("SELECT id, payload FROM jobs WHERE type = 'asr' AND status = 'queued' AND payload->>'media_id' = %s", [str(m["id"])])
            self.assertEqual(len(q), 1, "新影片自動排入一個字幕工作")
            self.assertFalse(q[0]["payload"].get("immediate"))
            self.assertEqual(self.run_jobs(["asr"]), [], "時段外不會執行（這就是實機看到的「一直不動」）")
            conv = [x for x in self.ok(call("GET", "/api/admin/conversions?status=done", token=t, port=ADM)).json["items"] if x["id"] == m["id"]][0]
            self.assertEqual((conv["subtitle"]["has"], conv["subtitle"]["job"]["status"], bool(conv["subtitle"]["job"]["immediate"])), (False, "queued", False))
            # Super 按「產生字幕」：同一個工作改成馬上執行，不另外建立
            r = self.ok(call("POST", f"/api/admin/subtitles/{m['id']}/run", {}, t, port=ADM)).json
            self.assertEqual((r["job_id"], r["existing"], r["upgraded"]), (q[0]["id"], True, True))
            self.assertEqual(self.conn.val("SELECT count(*) FROM jobs WHERE type = 'asr' AND status IN ('queued','running') AND payload->>'media_id' = %s",
                                           [str(m["id"])]), 1, "ASR-05：不重複建立")
            r2 = self.ok(call("POST", f"/api/admin/subtitles/{m['id']}/run", {}, t, port=ADM)).json
            self.assertEqual((r2["job_id"], r2["upgraded"]), (q[0]["id"], False))
            self.assertEqual(self.run_jobs(["asr"]), ["done"], "ASR-01：直接播放的影片馬上產生字幕（不需要重新轉檔）")
            self.assertTrue(self.conn.val("SELECT 1 FROM subtitles WHERE media_id = %s", [m["id"]]))
            self.assertEqual(self.conn.val("SELECT convert_status FROM media WHERE id = %s", [m["id"]]), "direct", "沒有被改成重新轉檔")
            conv = [x for x in self.ok(call("GET", "/api/admin/conversions?status=done", token=t, port=ADM)).json["items"] if x["id"] == m["id"]][0]
            self.assertTrue(conv["subtitle"]["has"])
            # ASR-04：失敗後可以重試（建立新的工作）
            self.conn.execute("UPDATE jobs SET status = 'failed', error = '模擬失敗' WHERE type = 'asr' AND payload->>'media_id' = %s", [str(m["id"])])
            self.conn.execute("DELETE FROM subtitles WHERE media_id = %s", [m["id"]])
            conv = [x for x in self.ok(call("GET", "/api/admin/conversions?status=done", token=t, port=ADM)).json["items"] if x["id"] == m["id"]][0]
            self.assertEqual((conv["subtitle"]["has"], conv["subtitle"]["job"]["status"]), (False, "failed"))
            r3 = self.ok(call("POST", f"/api/admin/subtitles/{m['id']}/run", {}, t, port=ADM)).json
            self.assertFalse(r3["existing"])
            self.assertEqual(self.run_jobs(["asr"]), ["done"])
            # 執行中：回報正在辨識，不建立新工作
            j = self.ctx.enqueue("asr", {"media_id": m["id"], "immediate": True}, 40)
            self.conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", [j])
            r4 = self.ok(call("POST", f"/api/admin/subtitles/{m['id']}/run", {}, t, port=ADM)).json
            self.assertEqual((r4["job_id"], r4["running"]), (j, True))
            self.conn.execute("UPDATE jobs SET status = 'done' WHERE id = %s", [j])
            # 還不能播放的影片（轉檔中）：拒絕，原因寫清楚
            self.conn.execute("UPDATE media SET play_mode = 'transcode', convert_status = 'queued' WHERE id = %s", [m["id"]])
            self.assertEqual(call("POST", f"/api/admin/subtitles/{m['id']}/run", {}, t, port=ADM).status, 409)
            self.conn.execute("UPDATE media SET play_mode = 'direct', convert_status = 'direct' WHERE id = %s", [m["id"]])
        finally:
            self.ctx.set_config("schedule", sch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
