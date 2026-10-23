"""dev.62 方案 B 加密備份（BAK-01～BAK-13）：Agent 只用公鑰加密；主機 istudyctl（測試模式）做金鑰、解密驗證、還原測試、
FTPS 上傳（本機測試 FTPS 伺服器，真 TLS）、保留清理。

需要：PostgreSQL 測試叢集（DB_HOST／DB_PORT／DB_USER／DB_PASSWORD）、age（ISTUDY_AGE_BIN 指向 age 執行檔，同資料夾要有 age-keygen）。
InterServer 實際連線無法在開發環境測試（沒有帳號）：這裡的遠端測試是本機模擬，正式連線另外標記 NOT_TESTED。
"""
from __future__ import annotations

import glob
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
ROOT = os.path.dirname(AGENT)
sys.path.insert(0, AGENT)
sys.path.insert(0, HERE)

from istudy_common import backupcrypt as bc  # noqa: E402
from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import Paths  # noqa: E402
from worker import main as wmain  # noqa: E402
from worker import maintenance  # noqa: E402
from worker.context import Ctx  # noqa: E402

TEST_DB = "istudy_bak62_t"
AGE = os.environ.get("ISTUDY_AGE_BIN") or shutil.which("age") or ""
PW1 = "第一組恢復密碼 correct-horse-1"
PW2 = "第二組恢復密碼 battery-staple-2"
FTP_PW = "ftp-密碼-s3cret"


def _wrapper(dirpath: str, real_dir: str, log: str) -> str:
    """age／age-keygen 的包裝：記下每次被呼叫時的參數與環境變數（檢查密碼沒有出現在指令列或環境），再執行真正的程式。"""
    os.makedirs(dirpath, exist_ok=True)
    for tool in ("age", "age-keygen"):
        p = os.path.join(dirpath, tool)
        with open(p, "w") as f:
            f.write(f"""#!/usr/bin/env python3
import json, os, sys
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ)}}, ensure_ascii=False) + "\\n")
os.execv({os.path.join(real_dir, tool)!r}, [{os.path.join(real_dir, tool)!r}] + sys.argv[1:])
""")
        os.chmod(p, 0o755)
    return os.path.join(dirpath, "age")


@unittest.skipUnless(AGE and os.path.exists(AGE), "需要 age：設定 ISTUDY_AGE_BIN")
class Backup62(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ftps_testserver import start_test_server
        cls.tmp = tempfile.mkdtemp(prefix="istudy-bak62-")
        t = cls.tmp
        cls.media = os.path.join(t, "Media")
        for d in ("subtitles", "playback", "Outgo", "doc-cache", "基礎班"):
            os.makedirs(os.path.join(cls.media, d))
        for rel, data in (("subtitles/1.vtt", "WEBVTT\n\n00:00.000 --> 00:01.000\n字幕\n"), ("subtitles/2.vtt", "WEBVTT\n"),
                          ("playback/1.mp4", "mp4"), ("Outgo/錄音.txt", "逐字稿"), ("doc-cache/3.pdf", "pdf"),
                          ("基礎班/講義.pdf", "pdf"), ("基礎班/上課.mp4", "mp4"), (".istudy-media", "ab" * 16)):
            with open(os.path.join(cls.media, rel), "w", encoding="utf-8") as f:
                f.write(data)
        cls.hostcfg = os.path.join(t, "host-config")
        os.makedirs(cls.hostcfg)
        with open(os.path.join(cls.hostcfg, ".env"), "w") as f:
            f.write("DB_PASSWORD=secret-db-password\nISTUDY_VERSION=1.0.0-dev.62\n")
        shutil.copy(os.path.join(ROOT, "compose.yaml"), os.path.join(cls.hostcfg, "compose.yaml"))
        shutil.copy(os.path.join(ROOT, "VERSION.json"), os.path.join(cls.hostcfg, "VERSION.json"))
        cls.bkdir = os.path.join(t, "backup")
        os.makedirs(cls.bkdir)
        cls.prefix = os.path.join(t, "root")
        os.makedirs(cls.prefix)
        cls.agelog = os.path.join(t, "age-calls.jsonl")
        cls.age = _wrapper(os.path.join(t, "agebin"), os.path.dirname(os.path.realpath(AGE)), cls.agelog)

        admin = dbmod.connect(dbmod.conninfo_from_env({**os.environ, "DB_NAME": "postgres"}))
        admin.exec_raw(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.exec_raw(f"CREATE DATABASE {TEST_DB}")
        admin.exec_raw("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'istudy') THEN CREATE ROLE istudy NOLOGIN; END IF; END $$")
        admin.close()
        cls.env = {**os.environ, "DB_NAME": TEST_DB, "MEDIA_ROOT": cls.media, "ISTUDY_BACKUP_DIR": cls.bkdir, "ISTUDY_BACKUP_REQUIRE_MOUNT": "0",
                   "ISTUDY_HOST_CONFIG_DIR": cls.hostcfg, "ISTUDY_AGE_BIN": cls.age, "ISTUDY_VERSION_FILE": os.path.join(ROOT, "VERSION.json"),
                   "ISTUDY_BACKUP_MIN_FREE_MB": "1", "ISTUDY_BACKUP_LOCK_WAIT": "1"}
        subprocess.run(["php", os.path.join(ROOT, "server", "cli", "istudy.php"), "migrate"], env=cls.env, check=True, capture_output=True)
        cls.conn = dbmod.connect(dbmod.conninfo_from_env(cls.env))
        cls.ctx = Ctx(conn=cls.conn, paths=Paths(cls.media), env=cls.env)
        cls.ctx.set_config("install_id", "ab" * 16)
        cls.conn.execute("INSERT INTO audit_log(actor_name, actor_role, action) VALUES ('測試', 'super', 'login')")
        cls.srv, cls.ftproot, ca = start_test_server(password=FTP_PW)
        cls.hostenv = {**os.environ, "ISTUDY_ROOT_PREFIX": cls.prefix, "ISTUDY_SKIP_DOCKER": "1", "ISTUDY_MEDIA_ROOT": cls.media,
                       "ISTUDY_TEST_PG": f"{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '5432')}:{os.environ.get('DB_USER', 'postgres')}",
                       "PGPASSWORD": os.environ.get("DB_PASSWORD", ""), "ISTUDY_TEST_DB_NAME": TEST_DB, "ISTUDY_BACKUP_DIR": cls.bkdir,
                       "ISTUDY_AGE_BIN": cls.age, "ISTUDY_TEST_FTPS_CAFILE": ca, "ISTUDY_FTPS_TIMEOUT": "10", "ISTUDY_BACKUP_RUN_WAIT": "5", "ISTUDY_BACKUP_HOST_WAIT": "2"}
        cls.outputs: list[str] = []

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------- helpers ----------
    def ctl(self, *args, stdin=None, env=None):
        p = subprocess.run([sys.executable, os.path.join(AGENT, "istudyctl.py"), "--json", *args], env=env or self.hostenv,
                           capture_output=True, text=True, input=stdin, timeout=300)
        self.outputs.append(p.stdout + p.stderr)
        return p

    @staticmethod
    def done(p) -> dict:
        ev = [json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")]
        d = [e for e in ev if e["event"] in ("done", "error")]
        return d[-1] if d else {}

    def backup(self, env=None) -> tuple[str, dict]:
        ctx = Ctx(conn=self.conn, paths=Paths(self.media), env=env or self.env) if env else self.ctx
        jid = ctx.enqueue("backup", {"kind": "manual"}, 60)
        job = self.conn.one("UPDATE jobs SET status = 'running' WHERE id = %s RETURNING *", [jid])
        st = wmain.run_job(ctx, job)
        return st, self.conn.one("SELECT * FROM jobs WHERE id = %s", [jid])

    def last_row(self) -> dict:
        return self.conn.one("SELECT * FROM backup_history ORDER BY id DESC LIMIT 1")

    def last_good(self) -> dict:
        """最新一份成功、未刪除的加密備份。"""
        return self.conn.one("SELECT * FROM backup_history WHERE format = 'age' AND status = 'success' AND deleted_at IS NULL ORDER BY id DESC LIMIT 1")

    def identity(self) -> str:
        return os.path.join(self.prefix, "etc", "istudy", "backup", "identity.key")

    def decrypt(self, path: str) -> dict:
        out = subprocess.run([os.path.realpath(AGE), "-d", "-i", self.identity(), path], capture_output=True, check=True).stdout
        members = {}
        with tarfile.open(fileobj=io.BytesIO(out), mode="r:gz") as tar:
            for m in tar.getmembers():
                members[m.name] = tar.extractfile(m).read()
        return members

    def db_text(self) -> str:
        """資料庫裡所有可能出現密碼的地方（Log、工作、設定、備份紀錄）。"""
        parts = [self.conn.val("SELECT COALESCE(string_agg(message || ' ' || context::text, ' '), '') FROM system_logs"),
                 self.conn.val("SELECT COALESCE(string_agg(payload::text || ' ' || COALESCE(result::text, '') || ' ' || COALESCE(error, ''), ' '), '') FROM jobs"),
                 self.conn.val("SELECT COALESCE(string_agg(key || ' ' || value::text, ' '), '') FROM server_config"),
                 self.conn.val("SELECT COALESCE(string_agg(row_to_json(b)::text, ' '), '') FROM backup_history b")]
        return " ".join(parts)

    def files_text(self, *roots) -> bytes:
        data = b""
        for r in roots:
            for dirpath, _, names in os.walk(r):
                for n in names:
                    try:
                        with open(os.path.join(dirpath, n), "rb") as f:
                            data += f.read() + b"\n"
                    except OSError:
                        pass
        return data

    # ---------- BAK-01（沒有金鑰）：不產生任何備份，也不產生未加密備份 ----------
    def test_01_no_key_no_backup(self):
        st, job = self.backup()
        self.assertEqual(st, "failed")
        self.assertIn("尚未設定備份恢復密碼", job["error"])
        self.assertEqual([n for n in os.listdir(self.bkdir) if n.startswith("istudy-backup")], [], "沒有金鑰時不產生任何檔案")
        self.assertEqual(self.last_row()["status"], "failed")
        self.assertTrue(self.conn.val("SELECT 1 FROM system_logs WHERE level = 'error' AND message LIKE '%尚未設定備份恢復密碼%'"))

    # ---------- BAK-03：設定恢復密碼（密碼只經標準輸入；不寫入指令列、環境變數、資料庫、Log、檔案） ----------
    def test_02_key_init(self):
        self.assertIn("至少 12 字", self.done(self.ctl("backup-key", "--init", "--password-stdin", stdin="short\nshort\n"))["message"])
        self.assertIn("不同", self.done(self.ctl("backup-key", "--init", "--password-stdin", stdin=f"{PW1}\n{PW1}x\n"))["message"])
        self.assertIn("--password-stdin", self.done(self.ctl("backup-key", "--init", stdin=""))["message"], "沒有終端機時一定要用標準輸入")
        p = self.ctl("backup-key", "--init", "--password-stdin", stdin=f"{PW1}\n{PW1}\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rec = self.done(p)["recipient"]
        self.assertRegex(rec, bc.RECIPIENT_RE)
        self.assertEqual(self.conn.val("SELECT value->>'recipient' FROM server_config WHERE key = 'backup_key'"), rec, "公鑰給 Agent")
        etc = os.path.dirname(self.identity())
        self.assertEqual(stat.S_IMODE(os.stat(etc).st_mode), 0o700)
        for n in ("identity.key", "recovery-key.age"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(etc, n)).st_mode), 0o600, n)
        self.assertTrue(open(os.path.join(etc, "recovery-key.age"), "rb").read().startswith(b"-----BEGIN AGE ENCRYPTED FILE-----"))
        self.assertTrue(os.path.isfile(os.path.join(self.bkdir, bc.RECOVERY_KEY_NAME)) and os.path.isfile(os.path.join(self.bkdir, bc.RESTORE_NAME)))
        self.assertIn("已經設定過", self.done(self.ctl("backup-key", "--init", "--password-stdin", stdin=f"{PW1}\n{PW1}\n"))["message"])
        # 安全輸入：age 被呼叫時的參數與環境變數都沒有密碼；檔案、資料庫、輸出也沒有
        calls = [json.loads(x) for x in open(self.agelog, encoding="utf-8")]
        self.assertTrue(any("-p" in c["argv"] for c in calls), "恢復金鑰用 age -p（密碼模式）")
        for c in calls:
            self.assertNotIn(PW1, json.dumps(c, ensure_ascii=False))
        self.assertNotIn(PW1.encode(), self.files_text(self.prefix, self.bkdir))
        self.assertNotIn(PW1, self.db_text())
        self.assertNotIn(PW1, "".join(self.outputs))

    # ---------- BAK-01、BAK-02：內容與排除項目；SHA-256；只保存加密版本 ----------
    def test_03_backup_contents(self):
        st, job = self.backup()
        self.assertEqual(st, "done", job["error"])
        row = self.last_row()
        path = row["file_path"]
        self.assertRegex(os.path.basename(path), bc.NAME_RE)
        self.assertEqual((row["format"], row["enc_status"], row["status"]), ("age", "unverified", "success"))
        self.assertTrue(bc.looks_like_age(path))
        self.assertEqual(bc.sha256_file(path), row["sha256"])
        self.assertEqual(bc.read_sidecar(path), row["sha256"])
        r = subprocess.run(["sha256sum", "-c", os.path.basename(path) + ".sha256"], cwd=self.bkdir, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(n for n in os.listdir(self.bkdir) if n.startswith("istudy-backup")),
                         sorted([os.path.basename(path), os.path.basename(path) + ".sha256"]), "本機只保存加密版本（沒有明文 tar）")
        self.assertFalse(glob.glob(os.path.join(self.bkdir, ".*.part")))
        m = self.decrypt(path)
        self.assertEqual(sorted(m), sorted(["db.sql", "subtitles/1.vtt", "subtitles/2.vtt", "config/.env", "config/compose.yaml",
                                            "config/VERSION.json", "config/install_id", "RESTORE.md", "manifest.json"]))
        man = json.loads(m["manifest.json"])
        self.assertEqual(man["format"], bc.FORMAT)
        self.assertIn("0011_backup_v2", man["migrations"])
        self.assertEqual(man["counts_after"]["audit_log"], 1)
        self.assertEqual(man["encryption"]["recipient"], self.conn.val("SELECT value->>'recipient' FROM server_config WHERE key = 'backup_key'"))
        self.assertTrue(any("playback" in x for x in man["excluded"]) and any("Outgo" in x for x in man["excluded"]))
        for f in man["files"]:
            self.assertEqual(__import__("hashlib").sha256(m[f["name"]]).hexdigest(), f["sha256"], f["name"])
        self.assertIn(b"CREATE TABLE", m["db.sql"])
        self.assertIn(b"pg_catalog.setval", m["db.sql"], "pg_dump 含序列")
        self.assertEqual(m["config/install_id"].strip(), b"ab" * 16)
        self.assertNotIn(PW1, json.dumps(man, ensure_ascii=False))
        self.assertIn("不能用它恢復", m["RESTORE.md"].decode())

    # ---------- BAK-02：主機解密驗證；竄改檔案會被抓到 ----------
    def test_04_host_verify_and_tamper(self):
        row = self.last_row()
        p = self.ctl("backup-verify", "--id", str(row["id"]))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.last_row()
        self.assertEqual(row["enc_status"], "verified")
        self.assertTrue(row["verify_result"]["decrypt"]["passed"])
        self.assertEqual(row["verify_result"]["decrypt"]["members"], 9)
        self.assertTrue(self.conn.val("SELECT 1 FROM jobs WHERE type = 'backup_verify' AND status = 'done'"), "主機工作有紀錄")
        # 竄改一份複本（不同 id）：SHA-256 不符 → 驗證失敗
        bad = os.path.join(self.bkdir, "istudy-backup-20200101-000000.tar.gz.age")
        shutil.copy(row["file_path"], bad)
        with open(bad, "r+b") as f:
            f.seek(200)
            f.write(b"\x00\x01")
        bid = self.conn.val("INSERT INTO backup_history(kind, status, file_path, format, sha256, enc_status) VALUES ('manual', 'success', %s, 'age', %s, 'unverified') RETURNING id",
                            [bad, row["sha256"]])
        p = self.ctl("backup-verify", "--id", str(bid))
        self.assertNotEqual(p.returncode, 0)
        r = self.conn.one("SELECT enc_status, verify_result FROM backup_history WHERE id = %s", [bid])
        self.assertEqual(r["enc_status"], "failed")
        self.assertIn("SHA-256", r["verify_result"]["decrypt"]["message"])
        # 紀錄沒有 SHA-256 時：解密本身也會抓到損壞
        self.conn.execute("UPDATE backup_history SET sha256 = NULL, enc_status = 'unverified' WHERE id = %s", [bid])
        os.remove(bad + ".sha256") if os.path.exists(bad + ".sha256") else None
        self.ctl("backup-verify", "--id", str(bid))
        self.assertEqual(self.conn.val("SELECT enc_status FROM backup_history WHERE id = %s", [bid]), "failed")
        os.remove(bad)
        self.conn.execute("UPDATE backup_history SET deleted_at = NOW(), delete_reason = '測試用竄改檔' WHERE id = %s", [bid])

    # ---------- BAK-10：還原到測試資料庫，正式資料庫不受影響 ----------
    def test_05_restore_test_isolated(self):
        before = maintenance._counts(self.ctx)
        sentinel = self.conn.val("SELECT value::text FROM server_config WHERE key = 'install_id'")
        row = self.last_good()
        p = self.ctl("backup-verify", "--id", str(row["id"]), "--restore-test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rt = self.last_good()["verify_result"]["restore_test"]
        self.assertTrue(rt["passed"], rt)
        self.assertEqual(rt["target_db"], "istudy_restore_test")
        self.assertEqual(rt["restored_counts"]["audit_log"], 1)
        self.assertEqual(maintenance._counts(self.ctx), before, "正式（測試案例的）資料庫筆數不變")
        self.assertEqual(self.conn.val("SELECT value::text FROM server_config WHERE key = 'install_id'"), sentinel)
        t = dbmod.connect(dbmod.conninfo_from_env({**self.env, "DB_NAME": "istudy_restore_test"}))
        try:
            self.assertEqual(int(t.val("SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public'")), 0, "測試資料庫結束後清空")
        finally:
            t.close()

    # ---------- BAK-04：修改密碼後仍能還原舊備份；主機私鑰遺失時用恢復金鑰＋新密碼匯入 ----------
    def test_06_change_password_old_backups(self):
        old = self.last_good()
        p = self.ctl("backup-key", "--change", "--password-stdin", stdin=f"{PW2}\n{PW2}\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.ctl("backup-key", "--verify", "--password-stdin", stdin=f"{PW2}\n").returncode, 0)
        p = self.ctl("backup-key", "--verify", "--password-stdin", stdin=f"{PW1}\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("恢復密碼錯誤", self.done(p)["message"])
        # 模擬重灌：主機私鑰不見了 → 用恢復金鑰檔＋新密碼匯入 → 舊備份照樣能解密、還原
        wrapped = os.path.join(self.bkdir, bc.RECOVERY_KEY_NAME)
        os.remove(self.identity())
        self.assertNotEqual(self.ctl("backup-verify", "--id", str(old["id"])).returncode, 0, "沒有私鑰無法解密")
        self.assertIn("恢復密碼錯誤", self.done(self.ctl("backup-key", "--import", "--file", wrapped, "--password-stdin", stdin=f"{PW1}\n"))["message"])
        p = self.ctl("backup-key", "--import", "--file", wrapped, "--password-stdin", stdin=f"{PW2}\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.ctl("restore", "--file", old["file_path"], "--target", "test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("istudy_restore_test", self.done(p)["message"])
        self.assertNotIn(PW2.encode(), self.files_text(self.prefix, self.bkdir))
        self.assertNotIn(PW2, self.db_text())
        for c in (json.loads(x) for x in open(self.agelog, encoding="utf-8")):
            self.assertNotIn(PW2, json.dumps(c, ensure_ascii=False))

    # ---------- BAK-05：FTPS 上傳（暫存名稱→改名）、下載比對 SHA-256 才標示已驗證；遠端資料夾不能在網站公開目錄 ----------
    def test_07_remote_upload(self):
        cfg = {"host": "localhost", "port": self.srv.port, "username": "tester", "password": FTP_PW, "dir": "/public_html/istudy"}
        p = self.ctl("backup-remote", "--set", stdin=json.dumps(cfg) + "\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("公開目錄", self.done(p)["message"])
        self.assertIn("只支援 FTPS", self.done(self.ctl("backup-remote", "--set", stdin=json.dumps({**cfg, "protocol": "ftp"}) + "\n"))["message"])
        p = self.ctl("backup-remote", "--set", stdin=json.dumps({**cfg, "dir": "/istudy-backup"}) + "\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rc = os.path.join(self.prefix, "etc", "istudy", "backup", "remote.json")
        self.assertEqual(stat.S_IMODE(os.stat(rc).st_mode), 0o600)
        self.assertNotIn(FTP_PW, self.db_text(), "FTP 密碼不在資料庫")
        p = self.ctl("backup-remote", "--test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.conn.val("SELECT (value->'last_test'->>'ok')::boolean FROM server_config WHERE key = 'backup_remote'"))
        self.assertEqual(os.listdir(os.path.join(self.ftproot, "istudy-backup")), [], "測試檔已刪除")
        self.assertFalse(self.srv.plain_password_seen)
        # 下載回來比對不符 → 不標示已驗證，本機檔案不動
        row = self.last_good()
        self.srv.corrupt_retr = True
        p = self.ctl("backup-upload", "--id", str(row["id"]))
        self.srv.corrupt_retr = False
        self.assertNotEqual(p.returncode, 0)
        r = self.conn.one("SELECT remote_status, remote_error FROM backup_history WHERE id = %s", [row["id"]])
        self.assertEqual(r["remote_status"], "failed")
        self.assertIn("SHA-256", r["remote_error"])
        # 正常上傳
        self.srv.commands.clear()
        p = self.ctl("backup-upload", "--id", str(row["id"]))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.conn.one("SELECT * FROM backup_history WHERE id = %s", [row["id"]])
        name = os.path.basename(row["file_path"])
        self.assertEqual((r["remote_status"], r["remote_path"]), ("verified", "/istudy-backup/" + name))
        self.assertIsNotNone(r["remote_verified_at"])
        remote = os.path.join(self.ftproot, "istudy-backup")
        self.assertEqual(sorted(os.listdir(remote)), sorted([name, name + ".sha256", bc.RECOVERY_KEY_NAME, bc.RESTORE_NAME]))
        self.assertEqual(bc.sha256_file(os.path.join(remote, name)), row["sha256"])
        self.assertEqual(bc.sha256_file(os.path.join(remote, bc.RECOVERY_KEY_NAME)), bc.sha256_file(os.path.join(self.bkdir, bc.RECOVERY_KEY_NAME)))
        stor = [c for c in self.srv.commands if c.startswith(("STOR", "RNFR", "RNTO"))]
        self.assertIn(f"STOR .{name}.part", stor)
        self.assertLess(stor.index(f"STOR .{name}.part"), stor.index(f"RNTO {name}"), "先暫存名稱，再改名")
        self.assertTrue(self.conn.val("SELECT (value->>'remote_synced')::boolean FROM server_config WHERE key = 'backup_key'"))
        verify = self.conn.one("SELECT verify_result FROM backup_history WHERE id = %s", [row["id"]])["verify_result"]
        self.assertTrue(verify["restore_test"]["passed"], "上傳前先做解密驗證＋還原測試")
        self.assertNotIn(FTP_PW, "".join(self.outputs))

    # ---------- BAK-09：遠端失敗保留本機檔案、可重試 ----------
    def test_08_remote_failure_keeps_local(self):
        st, _ = self.backup()
        self.assertEqual(st, "done")
        row = self.last_good()
        digest = bc.sha256_file(row["file_path"])
        port = self.srv.port
        self.srv.close()
        p = self.ctl("backup-upload")
        self.assertNotEqual(p.returncode, 0)
        r = self.conn.one("SELECT remote_status, remote_error FROM backup_history WHERE id = %s", [row["id"]])
        self.assertEqual(r["remote_status"], "failed")
        self.assertTrue(os.path.isfile(row["file_path"]) and bc.sha256_file(row["file_path"]) == digest, "本機備份完全不動")
        rem = self.conn.one("SELECT value FROM server_config WHERE key = 'backup_remote'")["value"]
        self.assertFalse(rem["last_upload"]["ok"])
        self.assertEqual(rem["upload_failures"], 1)
        self.assertTrue(self.conn.val("SELECT 1 FROM system_logs WHERE source = 'host' AND message LIKE '%本機備份保留%'"))
        # 伺服器恢復（換了連接埠；主機與帳號不變、密碼留空＝沿用）→ 重試成功
        from ftps_testserver import FtpsTestServer
        d = os.path.dirname(self.ftproot)
        self.__class__.srv = FtpsTestServer(self.ftproot, os.path.join(d, "server.pem"), os.path.join(d, "server.key"), password=FTP_PW).start()
        self.assertNotEqual(self.srv.port, port)
        p = self.ctl("backup-remote", "--set", stdin=json.dumps({"host": "localhost", "port": self.srv.port, "username": "tester", "password": "",
                                                                 "dir": "/istudy-backup"}) + "\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.ctl("backup-upload")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.conn.val("SELECT remote_status FROM backup_history WHERE id = %s", [row["id"]]), "verified")
        self.assertEqual(self.conn.val("SELECT (value->>'upload_failures')::int FROM server_config WHERE key = 'backup_remote'"), 0)
        self.assertIn("已有遠端副本", self.done(self.ctl("backup-upload"))["message"], "最新一份已上傳就不再上傳")

    # ---------- BAK-06：每 3 天上傳一次（主機排程 backup-run） ----------
    def test_09_upload_schedule(self):
        st, _ = self.backup()
        self.assertEqual(st, "done")
        new = self.last_good()
        uploads = lambda: int(self.conn.val("SELECT COUNT(*) FROM jobs WHERE type = 'backup_upload' AND status = 'done'"))  # noqa: E731
        n0 = uploads()
        p = self.ctl("backup-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.conn.val("SELECT enc_status FROM backup_history WHERE id = %s", [new["id"]]), "verified", "新備份自動解密驗證")
        self.assertEqual(uploads(), n0, "3 天內已有遠端副本：不上傳")
        self.assertEqual(self.conn.val("SELECT remote_status FROM backup_history WHERE id = %s", [new["id"]]), "none")
        self.conn.execute("UPDATE backup_history SET remote_verified_at = remote_verified_at - INTERVAL '3 days' WHERE remote_status = 'verified'")
        p = self.ctl("backup-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(uploads(), n0 + 1, "超過 3 天：上傳最新一份")
        self.assertEqual(self.conn.val("SELECT remote_status FROM backup_history WHERE id = %s", [new["id"]]), "verified")
        self.assertTrue(self.conn.val("SELECT value->>'prune_date' FROM server_config WHERE key = 'backup_host'"), "每天清理一次")
        self.assertTrue(bc.upload_due(None, bc._dt.datetime.now()))
        now = bc._dt.datetime(2026, 10, 22, 4, 30)
        self.assertFalse(bc.upload_due(now - bc._dt.timedelta(days=2), now))
        self.assertTrue(bc.upload_due(now - bc._dt.timedelta(days=3, minutes=-30), now), "每天同一時間執行，幾分鐘的差距不延後一天")

    # ---------- BAK-07、BAK-08：10～15 天；最後一份可用備份永遠保留；舊的未加密備份與 istudy-db-*.sql 不動 ----------
    def test_10_retention(self):
        self.assertEqual(bc.keep_days_of({"keep_days": 9}), 15)
        self.assertEqual(bc.keep_days_of({"keep_days": 16}), 15)
        self.assertEqual(bc.keep_days_of({"keep_days": 10}), 10)
        legacy = os.path.join(self.bkdir, "istudy-backup-20261001-033000.tar.gz")
        dbsql = os.path.join(self.bkdir, "istudy-db-20261001-120000.sql")
        for f in (legacy, dbsql):
            with open(f, "w") as x:
                x.write("legacy")
        self.conn.execute("INSERT INTO backup_history(kind, status, file_path, created_at) VALUES ('daily', 'success', %s, NOW() - INTERVAL '40 days')", [legacy])
        rows = self.conn.execute("SELECT id, file_path, enc_status FROM backup_history WHERE format = 'age' AND status = 'success' AND deleted_at IS NULL ORDER BY id")
        self.assertGreaterEqual(len(rows), 3)
        # 全部變成 20 天以前：只保留最新一份「已驗證」的
        self.conn.execute("UPDATE backup_history SET created_at = NOW() - INTERVAL '20 days' - (INTERVAL '1 minute' * (1000 - id)) WHERE format = 'age'")
        newest_unverified = rows[-1]
        self.conn.execute("UPDATE backup_history SET enc_status = 'unverified' WHERE id = %s", [newest_unverified["id"]])
        protected = self.conn.val("SELECT id FROM backup_history WHERE format = 'age' AND status = 'success' AND deleted_at IS NULL AND enc_status = 'verified' "
                                  "ORDER BY created_at DESC LIMIT 1")
        p = self.ctl("backup-prune")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        left = self.conn.execute("SELECT id, file_path FROM backup_history WHERE format = 'age' AND status = 'success' AND deleted_at IS NULL")
        self.assertEqual([r["id"] for r in left], [protected])
        self.assertTrue(os.path.isfile(left[0]["file_path"]))
        for r in rows:
            if r["id"] != protected:
                self.assertFalse(os.path.exists(r["file_path"]) or os.path.exists(r["file_path"] + ".sha256"), r)
                self.assertIn("超過保留 15 天", self.conn.val("SELECT delete_reason FROM backup_history WHERE id = %s", [r["id"]]))
        self.assertTrue(os.path.isfile(legacy) and os.path.isfile(dbsql), "舊的未加密備份與 istudy-db-*.sql 不動")
        self.assertIsNone(self.conn.val("SELECT deleted_at FROM backup_history WHERE file_path = %s", [legacy]))
        msg = self.conn.val("SELECT message FROM system_logs WHERE source = 'host' AND message LIKE '清理%%' ORDER BY id DESC LIMIT 1")
        self.assertIn(f"保留最後可用備份 #{protected}", msg)
        self.assertIn("舊的未加密備份", msg)
        # 遠端：最新一份已驗證的遠端副本保留，其餘過期的刪除
        remote = os.path.join(self.ftproot, "istudy-backup")
        rv = self.conn.execute("SELECT id, remote_path, remote_status FROM backup_history WHERE format = 'age' AND remote_path IS NOT NULL ORDER BY created_at")
        self.assertTrue(all(r["remote_status"] in ("verified", "deleted") for r in rv))
        kept = [r for r in rv if r["remote_status"] == "verified"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(sorted(n for n in os.listdir(remote) if n.startswith("istudy-backup")),
                         sorted([os.path.basename(kept[0]["remote_path"]), os.path.basename(kept[0]["remote_path"]) + ".sha256"]))
        # 保留天數內的不刪
        st, _ = self.backup()
        self.assertEqual(st, "done")
        fresh = self.last_good()
        self.ctl("backup-prune")
        self.assertIsNone(self.conn.val("SELECT deleted_at FROM backup_history WHERE id = %s", [fresh["id"]]))
        self.assertTrue(os.path.isfile(fresh["file_path"]))

    # ---------- BAK-08：磁碟空間不足：停止新備份並記錄，不刪任何備份 ----------
    def test_11_low_disk(self):
        before = sorted(os.listdir(self.bkdir))
        st, job = self.backup(env={**self.env, "ISTUDY_BACKUP_MIN_FREE_MB": str(10 ** 9)})
        self.assertEqual(st, "failed")
        self.assertIn("空間不足", job["error"])
        self.assertIn("沒有刪除任何既有備份", job["error"])
        self.assertEqual(sorted(os.listdir(self.bkdir)), before)
        self.assertTrue(self.conn.val("SELECT 1 FROM system_logs WHERE level = 'error' AND message LIKE '%空間不足%'"))
        # 必要系統設定（/host-config）不見了：不產生不完整的備份
        empty = tempfile.mkdtemp(dir=self.tmp)
        st, job = self.backup(env={**self.env, "ISTUDY_HOST_CONFIG_DIR": empty})
        self.assertEqual(st, "failed")
        self.assertIn("缺少必要的系統設定", job["error"])
        self.assertEqual(sorted(os.listdir(self.bkdir)), before, "沒有留下任何檔案")

    # ---------- BAK-12：私鑰不暴露給 Agent ----------
    def test_12_private_key_not_in_agent(self):
        compose = open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8").read()
        self.assertNotIn("/etc/istudy", compose, "compose 沒有把 /etc/istudy 掛進任何容器")
        for f in glob.glob(os.path.join(AGENT, "worker", "*.py")):
            self.assertNotIn("identity.key", open(f, encoding="utf-8").read(), f)
        self.assertNotIn("identity", open(os.path.join(AGENT, "worker", "maintenance.py"), encoding="utf-8").read().replace("install_id", ""))
        # 正式環境（非測試模式）的一次性容器：無網路、唯讀、不留容器輸出、私鑰經標準輸入（不掛載）
        spec = importlib.util.spec_from_file_location("istudyctl_prod", os.path.join(AGENT, "istudyctl.py"))
        saved = os.environ.pop("ISTUDY_ROOT_PREFIX", None)
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        finally:
            if saved is not None:
                os.environ["ISTUDY_ROOT_PREFIX"] = saved
        ro, arg = mod._age_file("/srv/istudy/backup/istudy-backup-20261022-033000.tar.gz.age")
        cmd = mod._age_cmd("age", ["-d", "-i", "-", arg], ro)
        s = " ".join(cmd)
        for must in ("--network none", "--read-only", "--log-driver none", "--cap-drop ALL", "-v /srv/istudy/backup:/in:ro", "-d -i -"):
            self.assertIn(must, s)
        self.assertNotIn("/etc/istudy", s)
        self.assertEqual(mod.IDENTITY, "/etc/istudy/backup/identity.key")
        wrap = " ".join(mod._age_cmd("agepass", ["wrap"]))
        self.assertIn("/opt/istudy-agent/istudy_common/agepass.py wrap", wrap)
        self.assertNotIn("-v ", wrap, "包裝恢復金鑰不掛載任何資料夾")

    # ---------- BAK-13：舊的未加密備份相容 ----------
    def test_13_legacy_compat(self):
        work = tempfile.mkdtemp(dir=self.tmp)
        dump = os.path.join(work, "db.sql")
        subprocess.run(["pg_dump", "--no-owner", "--no-privileges", "-f", dump] + maintenance._db_args(self.ctx), env=maintenance._db_env(self.ctx),
                       check=True, capture_output=True)
        counts = maintenance._counts(self.ctx)
        with open(os.path.join(work, "manifest.json"), "w") as f:
            json.dump({"created_at": "2026-10-01T03:30:00+08:00", "server_version": "1.0.0-dev.61", "counts_before": counts, "counts_after": counts}, f)
        legacy = os.path.join(self.bkdir, "istudy-backup-20261002-033000.tar.gz")
        with tarfile.open(legacy, "w:gz") as tar:
            tar.add(dump, "db.sql")
            tar.add(os.path.join(work, "manifest.json"), "manifest.json")
        bid = self.conn.val("INSERT INTO backup_history(kind, status, file_path, size_bytes, file_count) VALUES ('daily', 'success', %s, 1, 1) RETURNING id", [legacy])
        row = self.conn.one("SELECT format, enc_status, remote_status FROM backup_history WHERE id = %s", [bid])
        self.assertEqual((row["format"], row["enc_status"], row["remote_status"]), ("legacy", "none", "none"), "dev.61 的寫法自動視為舊格式")
        for mode in ("integrity", "restore_test"):
            jid = self.ctx.enqueue("backup_check", {"backup_id": bid, "mode": mode})
            job = self.conn.one("UPDATE jobs SET status = 'running' WHERE id = %s RETURNING *", [jid])
            self.assertEqual(wmain.run_job(self.ctx, job), "done", mode)
        v = self.conn.val("SELECT verify_result FROM backup_history WHERE id = %s", [bid])
        self.assertTrue(v["integrity"]["passed"] and v["restore_test"]["passed"], v)
        p = self.ctl("restore", "--file", legacy, "--target", "test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.ctl("backup-prune")
        self.assertTrue(os.path.isfile(legacy))
        self.assertNotIn(os.path.basename(legacy), json.dumps(self.conn.val("SELECT value FROM server_config WHERE key = 'backup_remote'")))
        # dev.61 回滾：dev.61 的保留份數清理只比對 istudy-backup-*.tar.gz，不會碰到 .age
        dev61 = sorted(glob.glob(os.path.join(self.bkdir, "istudy-backup-*.tar.gz")))
        self.assertTrue(dev61 and all(not f.endswith(".age") for f in dev61))
        # 加密備份交給 Agent 的「隔離還原測試」會說明要由主機做；「完整性」只比對 SHA-256
        age_row = self.last_good()
        jid = self.ctx.enqueue("backup_check", {"backup_id": age_row["id"], "mode": "restore_test"})
        job = self.conn.one("UPDATE jobs SET status = 'running' WHERE id = %s RETURNING *", [jid])
        self.assertEqual(wmain.run_job(self.ctx, job), "failed")
        self.assertIn("由主機執行", self.conn.val("SELECT error FROM jobs WHERE id = %s", [jid]))
        jid = self.ctx.enqueue("backup_check", {"backup_id": age_row["id"], "mode": "integrity"})
        job = self.conn.one("UPDATE jobs SET status = 'running' WHERE id = %s RETURNING *", [jid])
        self.assertEqual(wmain.run_job(self.ctx, job), "done")

    # ---------- 工作序列化：備份、驗證、上傳、清理共用一把鎖 ----------
    def test_14_serialized(self):
        with bc.BackupLock(self.bkdir):
            st, job = self.backup()
            self.assertEqual(st, "queued", "主機在處理時 Agent 稍後重試")
            self.assertIn("重試", job["error"])
            p = self.ctl("backup-prune")
            self.assertEqual(p.returncode, 75)
            self.assertIn("另一個備份工作", self.done(p)["message"])
        self.conn.execute("DELETE FROM jobs WHERE type = 'backup' AND status = 'queued'")

    # ---------- Agent 重新啟動不會改到主機的工作；主機下次執行收尾 ----------
    def test_15_agent_recover_ignores_host_jobs(self):
        jid = self.conn.val("INSERT INTO jobs(type, status, started_at, attempts) VALUES ('backup_upload', 'running', NOW(), 1) RETURNING id")
        wmain.recover(self.ctx)
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [jid]), "running")
        self.ctl("backup-prune")
        r = self.conn.one("SELECT status, error FROM jobs WHERE id = %s", [jid])
        self.assertEqual((r["status"], r["error"]), ("failed", "中斷（主機程序未正常結束）"))


if __name__ == "__main__":
    unittest.main()
