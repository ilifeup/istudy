"""dev.55 migration 測試（真實 PostgreSQL）：0002 Super 唯一、0003 稱謂、0004 WOL／工作類型。

執行（在專案根目錄）：
  DB_HOST=127.0.0.1 DB_USER=postgres python3 -m unittest server/tests/integration/test_migrations_dev55.py -v
每個測試建立自己的資料庫，結束後刪除。
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SQL = os.path.join(ROOT, "server", "sql")
CLI = os.path.join(ROOT, "server", "cli", "istudy.php")
PSQL = os.environ.get("PSQL", "psql")


def env(db: str) -> dict:
    e = dict(os.environ)
    e.update({"DB_NAME": db, "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"), "DB_USER": os.environ.get("DB_USER", "postgres"),
              "PGHOST": os.environ.get("DB_HOST", "127.0.0.1"), "PGUSER": os.environ.get("DB_USER", "postgres"), "PGDATABASE": db})
    return e


def psql(db: str, sql: str, check: bool = True) -> str:
    p = subprocess.run([PSQL, "-X", "-q", "-tA", "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True, env=env(db))
    if check and p.returncode != 0:
        raise AssertionError(p.stderr)
    return p.stdout.strip()


def php(db: str, *args: str, stdin: str = "") -> dict:
    p = subprocess.run(["php", CLI, *args], capture_output=True, text=True, env=env(db), input=stdin)
    return json.loads(p.stdout.strip().splitlines()[-1])


class MigrationsDev55(unittest.TestCase):
    def setUp(self):
        self.db = "istudy_mig54_" + self._testMethodName.split("_")[1]
        psql("postgres", f"DROP DATABASE IF EXISTS {self.db} WITH (FORCE)")
        psql("postgres", f"CREATE DATABASE {self.db} ENCODING 'UTF8' TEMPLATE template0")

    def tearDown(self):
        psql("postgres", f"DROP DATABASE IF EXISTS {self.db} WITH (FORCE)")

    def dev53(self):
        """模擬 dev.53 的資料庫：只有 schema.sql（0001），加上實機 2026-10-20 的帳號與 WOL 設備狀況。"""
        p = subprocess.run([PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", os.path.join(SQL, "schema.sql")],
                           capture_output=True, text=True, env=env(self.db))
        self.assertEqual(p.returncode, 0, p.stderr)
        psql(self.db, """INSERT INTO accounts(id, username, password_hash, role) VALUES
            (1, 'ricky', '$2y$10$abcdefghijklmnopqrstuuSuperHashKeepMe0000000000000000', 'super'),
            (2, 'helper', '$2y$10$abcdefghijklmnopqrstuuMiniHashKeepMe00000000000000000', 'mini_admin'),
            (3, 'stu01', '$2y$10$abcdefghijklmnopqrstuuUserHashKeepMe00000000000000000', 'user');
            SELECT setval('accounts_id_seq', 3);
            INSERT INTO client_devices(name, mac, broadcast, registered) VALUES
            ('Lenovo_S_PC', 'aa:bb:cc:00:00:03', '10.3.3.3', TRUE),
            ('AcerMiniPc', 'aa:bb:cc:00:00:02', '10.3.3.2', TRUE),
            ('AcerMediaServer2020', 'aa:bb:cc:00:00:01', '10.3.3.1', TRUE),
            ('Broadcast', 'aa:bb:cc:00:00:04', '10.3.3.255', TRUE),
            ('Limited', 'aa:bb:cc:00:00:05', '255.255.255.255', TRUE);""")

    def test_1_fresh_install_applies_all(self):
        r = php(self.db, "migrate")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["applied"], ["0001_schema", "0002_one_super", "0003_account_titles", "0004_wol_net_jobs",
                                        "0005_asr_report", "0006_email_report", "0007_asr_naming", "0008_purge_outgo_jobs", "0009_asr_log", "0010_client_update_status", "0011_backup_v2"])
        self.assertEqual(php(self.db, "migrate")["applied"], [], "第二次執行不重複套用")
        # dev.62：備份紀錄的新欄位都有預設值（dev.61 的寫法照常可用）；4 種主機備份工作類型
        psql(self.db, "INSERT INTO backup_history(kind, status, file_path) VALUES ('daily', 'success', '/srv/istudy/backup/istudy-backup-20261001-033000.tar.gz')")
        self.assertEqual(psql(self.db, "SELECT format || '/' || enc_status || '/' || remote_status FROM backup_history"), "legacy/none/none")
        for t in ("backup_verify", "backup_upload", "backup_prune", "backup_remote_test"):
            psql(self.db, f"INSERT INTO jobs(type) VALUES ('{t}')")
        self.assertNotEqual(subprocess.run([PSQL, "-X", "-q", "-c", "UPDATE backup_history SET format = 'zip'"], capture_output=True, env=env(self.db)).returncode, 0,
                            "format 只能是 legacy 或 age")
        # dev.57：字幕辨識報告欄位；背景工作可以是 email_report
        self.assertEqual(psql(self.db, "SELECT data_type FROM information_schema.columns WHERE table_name = 'subtitles' AND column_name = 'asr_report'"), "jsonb")
        self.assertIn("email_report", psql(self.db, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'jobs'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%subtitle_import%'"))
        # dev.61：永久刪除教材、Outgo 錄音轉文字兩種工作類型
        for t in ("purge_media", "outgo_asr"):
            psql(self.db, f"INSERT INTO jobs(type) VALUES ('{t}')")
        # dev.58：whisper → asr
        self.assertIsNone(psql(self.db, "INSERT INTO jobs(type) VALUES ('asr') RETURNING NULL") or None)
        self.assertNotEqual(subprocess.run([PSQL, "-X", "-q", "-c", "INSERT INTO jobs(type) VALUES ('whisper')"], capture_output=True, env=env(self.db)).returncode, 0,
                            "舊名稱 whisper 會被拒絕")
        # dev.55 ROLE-03：稱謂表只有助教、講師（不含 admin、super、學生）；權限表只有 user／admin／super＝1／2／3
        self.assertEqual(psql(self.db, "SELECT string_agg(title_id || '=' || zh_title, ',' ORDER BY sort_order) FROM account_titles"),
                         "assistant=助教,lecturer=講師")
        self.assertEqual(psql(self.db, "SELECT string_agg(role || '=' || title_level, ',' ORDER BY title_level) FROM account_roles"),
                         "user=1,admin=2,super=3")
        self.assertEqual(psql(self.db, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'account_titles' AND column_name = 'title_level'"),
                         "0", "title_level 只在權限表")
        self.assertEqual(psql(self.db, "SELECT string_agg(conname, ',' ORDER BY conname) FROM pg_constraint WHERE conrelid = 'accounts'::regclass AND contype = 'f'"),
                         "accounts_role_fkey,accounts_title_id_fkey", "accounts 以外鍵分別關聯權限與稱謂")

    def test_2_upgrade_from_dev53_keeps_ids_and_hashes(self):
        self.dev53()
        before = psql(self.db, "SELECT string_agg(id || ':' || username || ':' || password_hash, '|' ORDER BY id) FROM accounts")
        super_before = psql(self.db, "SELECT id || ':' || username || ':' || password_hash FROM accounts WHERE role = 'super'")
        psql(self.db, "INSERT INTO backup_history(kind, status, file_path, size_bytes) VALUES ('daily', 'success', '/srv/istudy/backup/old.tar.gz', 123)")
        r = php(self.db, "migrate")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["applied"], ["0002_one_super", "0003_account_titles", "0004_wol_net_jobs", "0005_asr_report", "0006_email_report",
                                        "0007_asr_naming", "0008_purge_outgo_jobs", "0009_asr_log", "0010_client_update_status", "0011_backup_v2"])
        self.assertEqual(psql(self.db, "SELECT file_path || '|' || size_bytes || '|' || format || '|' || enc_status FROM backup_history"),
                         "/srv/istudy/backup/old.tar.gz|123|legacy|none", "既有備份紀錄不變，標為舊格式、未加密")
        after = psql(self.db, "SELECT string_agg(id || ':' || username || ':' || password_hash, '|' ORDER BY id) FROM accounts")
        self.assertEqual(before, after, "帳號 ID、名稱、密碼雜湊都不變")
        self.assertEqual(super_before, psql(self.db, "SELECT id || ':' || username || ':' || password_hash FROM accounts WHERE role = 'super'"),
                         "唯一 Super 的 ID、帳號、密碼不變，仍是 super")
        # 原 mini_admin 改為 user（不提升為 admin）；Super 沒有稱謂；其他帳號補「助教」
        self.assertEqual(psql(self.db, "SELECT string_agg(id || '=' || role || '/' || COALESCE(title_id, '-'), ',' ORDER BY id) FROM accounts"),
                         "1=super/-,2=user/assistant,3=user/assistant")
        self.assertEqual(psql(self.db, "SELECT count(*) FROM accounts WHERE role NOT IN ('user','admin','super')"), "0")
        self.assertEqual(psql(self.db, "SELECT string_agg(role, ',' ORDER BY role) FROM tag_role_policy"), "admin,super,user", "Tag 政策也沒有 mini_admin")
        for bad in ("mini_admin", "student"):
            p = subprocess.run([PSQL, "-X", "-tA", "-c", f"INSERT INTO accounts(username, password_hash, role) VALUES ('x_{bad}', 'h', '{bad}')"],
                               capture_output=True, text=True, env=env(self.db))
            self.assertIn("accounts_role_fkey", p.stderr, f"{bad} 不是合法權限")
        # 稱謂與權限獨立：改稱謂不影響權限、改權限不影響稱謂
        psql(self.db, "UPDATE accounts SET title_id = 'lecturer' WHERE id = 3")
        self.assertEqual(psql(self.db, "SELECT role FROM accounts WHERE id = 3"), "user")
        psql(self.db, "UPDATE accounts SET role = 'admin' WHERE id = 3")
        self.assertEqual(psql(self.db, "SELECT title_id FROM accounts WHERE id = 3"), "lecturer")
        # WOL：既有設備的廣播位址不自動改寫（人工核對）
        rows = psql(self.db, "SELECT string_agg(name || '|' || lan_ip || '|' || broadcast, ';' ORDER BY name) FROM client_devices")
        self.assertEqual(rows, "AcerMediaServer2020||10.3.3.1;AcerMiniPc||10.3.3.2;Broadcast||10.3.3.255;"
                               "Lenovo_S_PC||10.3.3.3;Limited||255.255.255.255")
        # 新工作類型可以寫入；不在清單的仍被拒絕
        psql(self.db, "INSERT INTO jobs(type) VALUES ('net_probe'), ('lan_scan'), ('backup_check'), ('wol_wake')")
        self.assertIn("violates check constraint", subprocess.run(
            [PSQL, "-X", "-tA", "-c", "INSERT INTO jobs(type) VALUES ('shell')"], capture_output=True, text=True, env=env(self.db)).stderr)
        self.assertEqual(psql(self.db, "SELECT count(*) FROM pg_constraint WHERE conrelid = 'jobs'::regclass AND contype = 'c' "
                                       "AND pg_get_constraintdef(oid) LIKE '%subtitle_import%'"), "1", "只有一個工作類型限制")

    def test_5_asr_naming_and_rollback(self):
        """dev.58 0007：既有 whisper 工作與字幕改名為 asr；patch_dev58.sh rollback 用的 SQL 能改回去，之後可以再升級。"""
        self.assertTrue(php(self.db, "migrate")["ok"])
        psql(self.db, "DELETE FROM schema_migrations WHERE version = '0007_asr_naming'")
        down = os.path.join(ROOT, "deploy", "patch", "0007_asr_naming_down.sql")
        p = subprocess.run([PSQL, "-X", "-q", "-1", "-v", "ON_ERROR_STOP=1", "-f", down], capture_output=True, text=True, env=env(self.db))
        self.assertEqual(p.returncode, 0, p.stderr)
        # 模擬 dev.57 的資料：whisper 工作與字幕
        psql(self.db, "INSERT INTO media(rel_path, title, kind, file_name) VALUES ('V06.mp4', 'V06', 'video', 'V06.mp4')")
        mid = psql(self.db, "SELECT id FROM media WHERE rel_path = 'V06.mp4'")
        psql(self.db, f"INSERT INTO jobs(type, payload, status) VALUES ('whisper', '{{\"media_id\": {mid}}}', 'done'), ('whisper', '{{}}', 'failed')")
        psql(self.db, f"INSERT INTO subtitles(media_id, path, source, model, segment_count, asr_report) VALUES ({mid}, 'v.vtt', 'whisper', 'sensevoice-small', 15, '{{\"engine\": \"sensevoice-small\"}}')")
        r = php(self.db, "migrate")
        self.assertEqual((r["ok"], r["applied"]), (True, ["0007_asr_naming"]))
        self.assertEqual(psql(self.db, "SELECT string_agg(type, ',' ORDER BY id) FROM jobs WHERE payload ? 'media_id' OR status = 'failed'"), "asr,asr")
        self.assertEqual(psql(self.db, "SELECT source || '/' || model || '/' || segment_count || '/' || (asr_report->>'engine') FROM subtitles"),
                         "asr/sensevoice-small/15/sensevoice-small", "只改名稱；引擎、句數、辨識報告不變")
        # rollback：改回 dev.57 的寫法；dev.57 程式寫 whisper 可以成功
        p = subprocess.run([PSQL, "-X", "-q", "-1", "-v", "ON_ERROR_STOP=1", "-f", down], capture_output=True, text=True, env=env(self.db))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(psql(self.db, "SELECT DISTINCT source FROM subtitles"), "whisper")
        psql(self.db, "INSERT INTO jobs(type) VALUES ('whisper')")
        self.assertEqual(psql(self.db, "SELECT count(*) FROM schema_migrations WHERE version = '0007_asr_naming'"), "0")
        self.assertEqual(php(self.db, "migrate")["applied"], ["0007_asr_naming"], "退回後可以再升級")

    def test_3_second_super_blocked_by_database(self):
        self.dev53()
        php(self.db, "migrate")
        p = subprocess.run([PSQL, "-X", "-tA", "-c", "INSERT INTO accounts(username, password_hash, role) VALUES ('x2', 'h', 'super')"],
                           capture_output=True, text=True, env=env(self.db))
        self.assertIn("accounts_one_super", p.stderr)
        p = subprocess.run([PSQL, "-X", "-tA", "-c", "UPDATE accounts SET role = 'super' WHERE id = 2"],
                           capture_output=True, text=True, env=env(self.db))
        self.assertIn("accounts_one_super", p.stderr)
        self.assertEqual(psql(self.db, "SELECT count(*) FROM accounts WHERE role = 'super'"), "1")
        # create-super 已有就略過；reset-super 只動唯一那位
        self.assertTrue(php(self.db, "create-super", "另一位", stdin="abcdefghij\nabcdefghij\n").get("skipped"))
        r = php(self.db, "reset-super", stdin="NewPassword123\nNewPassword123\n")
        self.assertEqual(r.get("username"), "ricky", r)
        self.assertEqual(php(self.db, "status")["super_count"], 1)

    def test_4_existing_two_supers_stops_without_changes(self):
        self.dev53()
        psql(self.db, "INSERT INTO accounts(id, username, password_hash, role) VALUES (9, 'dup', 'h', 'super')")
        before = psql(self.db, "SELECT string_agg(id || ':' || role, ',' ORDER BY id) FROM accounts")
        r = php(self.db, "migrate")
        self.assertFalse(r["ok"])
        self.assertIn("ROLE-04", r["error"])
        self.assertEqual(psql(self.db, "SELECT string_agg(version, ',' ORDER BY version) FROM schema_migrations"), "0001_schema",
                         "0002 失敗後不再往下套用")
        self.assertEqual(before, psql(self.db, "SELECT string_agg(id || ':' || role, ',' ORDER BY id) FROM accounts"), "沒有刪除或修改帳號")
        self.assertEqual(psql(self.db, "SELECT to_regclass('public.account_titles') IS NULL"), "t")
        r = php(self.db, "reset-super", stdin="NewPassword123\nNewPassword123\n")
        self.assertFalse(r["ok"])
        self.assertIn("2 個 Super", r["error"])


if __name__ == "__main__":
    unittest.main()
