"""dev.62 P0-A：永久刪除的一致性（實機：TXT 顯示「Operation not permitted；已復原」，檔案卻看不到）。

真實 PostgreSQL、真實檔案系統；「改名失敗」「刪除失敗」用替換 os.rename／os.remove 模擬（測試以 root 執行，真的權限錯誤做不出來），
sticky 位元用真的資料夾權限＋模擬 Agent 的 uid 10001 檢查。只用測試建立的檔案。
環境變數：DB_HOST、DB_PORT、DB_USER、DB_PASSWORD（需可建立資料庫）。
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import TZ, Paths  # noqa: E402
from worker import main as wmain, purge  # noqa: E402
from worker.context import Ctx  # noqa: E402

import make_media  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "server", "sql", "schema.sql")
TEST_DB = os.environ.get("ISTUDY_TEST_DB62", "istudy_dev62_t")
INSTALL_ID = "62626262626262626262626262626262"


class Purge62(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="istudy-dev62-")
        make_media.build(cls.root)
        with open(os.path.join(cls.root, ".istudy-media"), "w") as f:
            f.write(INSTALL_ID)
        a = dbmod.connect(dbmod.conninfo_from_env({**os.environ, "DB_NAME": "postgres"}))
        a.exec_raw(f"DROP DATABASE IF EXISTS {TEST_DB}")
        a.exec_raw(f"CREATE DATABASE {TEST_DB}")
        a.close()
        os.environ.update({"DB_NAME": TEST_DB, "MEDIA_ROOT": cls.root, "ISTUDY_ENCODER": "cpu", "ISTUDY_X264_PRESET": "ultrafast",
                           "ISTUDY_ASR_ENGINE": "fake", "ISTUDY_RESERVE_GB": "0"})
        cls.conn = dbmod.connect()
        with open(SCHEMA, encoding="utf-8") as f:
            cls.conn.exec_raw(f.read())
        for mf in sorted(glob.glob(os.path.join(os.path.dirname(SCHEMA), "migrations", "[0-9][0-9][0-9][0-9]_*.sql"))):
            with open(mf, encoding="utf-8") as f:
                cls.conn.exec_raw(f.read())
        cls.ctx = Ctx(conn=cls.conn, paths=Paths(cls.root), env=dict(os.environ), encoder="cpu")
        cls.ctx.set_config("install_id", INSTALL_ID)
        cls.ctx.set_config("conversion_paused", False)
        now = _dt.datetime.now(TZ)
        cls.ctx.set_config("schedule", {"start": (now - _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "end": (now + _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "auto_convert": True, "auto_whisper": True, "whisper_after_convert": True,
                                        "backup_time": "23:59", "backup_keep": 3})
        cls.super_id = cls.conn.val("INSERT INTO accounts(username, password_hash, role, full_name) VALUES ('su62', 'x', 'super', 'S') RETURNING id")
        cls.scan()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def scan(cls):
        cls.ctx.enqueue("scan", {}, 50)
        for _ in range(300):
            job = wmain.pick(cls.ctx, list(wmain.HANDLERS))
            if not job:
                break
            wmain.run_job(cls.ctx, job)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def new_txt(self, folder: str, name: str = "delete_test.txt") -> dict:
        os.makedirs(self.p(folder), exist_ok=True)
        with open(self.p(folder, name), "w", encoding="utf-8") as f:
            f.write("永久刪除測試 24 bytes..")
        self.scan()
        m = self.conn.one("SELECT * FROM media WHERE rel_path = %s", [f"{folder}/{name}"])
        self.assertIsNotNone(m)
        self.assertEqual(m["kind"], "text")
        return m

    def purge_job(self, m: dict, jid: int | None = None) -> tuple[str, dict]:
        jid = jid or self.ctx.enqueue("purge_media", {"media_id": m["id"], "rel_path": m["rel_path"]}, 90, self.super_id)
        job = self.conn.one("UPDATE jobs SET status = 'running', started_at = NOW() WHERE id = %s RETURNING *", [jid])
        st = wmain.run_job(self.ctx, job)
        return st, self.conn.one("SELECT * FROM jobs WHERE id = %s", [jid])

    @staticmethod
    def res(job):
        r = job["result"]
        return r if isinstance(r, dict) else json.loads(r or "{}")

    def hidden(self, folder):
        return sorted(f for f in os.listdir(self.p(folder)) if f.startswith(purge.STAGE_PREFIX))

    # ---------------------------------------------------------------
    def test_01_txt_in_new_folder(self):
        """DEL-01、DEL-08：新建資料夾裡的 TXT：檔案與紀錄都刪除、畫面寫成功、重新掃描不出現、沒有留下暫存檔。"""
        m = self.new_txt("PurgeTest")
        st, job = self.purge_job(m)
        self.assertEqual(st, "done", job["error"])
        r = self.res(job)
        self.assertEqual((r["committed"], r["deleted"], r["left"]), (True, ["原檔"], []))
        self.assertEqual([x["state"] for x in r["items"]], ["gone"])
        self.assertFalse(os.path.exists(self.p("PurgeTest", "delete_test.txt")))
        self.assertEqual(self.hidden("PurgeTest"), [])
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [m["id"]]))
        self.scan()
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE rel_path = 'PurgeTest/delete_test.txt'"))

    def test_02_permission_precheck_changes_nothing(self):
        """DEL-02：sticky 位元（Agent 不是檔案擁有者）：先檢查就停止，不改名、不刪紀錄、不寫「已復原」。"""
        m = self.new_txt("StickyDir")
        os.chmod(self.p("StickyDir"), 0o1777)
        os.chown(self.p("StickyDir", "delete_test.txt"), 1234, 1234)
        real = purge._euid
        purge._euid = lambda: 10001
        try:
            st, job = self.purge_job(m)
        finally:
            purge._euid = real
            os.chmod(self.p("StickyDir"), 0o755)
        self.assertEqual(st, "failed")
        self.assertIn("sticky 位元", job["error"])
        self.assertIn("沒有刪除任何東西", job["error"])
        self.assertNotIn("已復原", job["error"])
        self.assertTrue(os.path.isfile(self.p("StickyDir", "delete_test.txt")))
        self.assertEqual(self.hidden("StickyDir"), [])
        self.assertTrue(self.conn.val("SELECT 1 FROM media WHERE id = %s", [m["id"]]))
        self.assertEqual([x["state"] for x in self.res(job)["items"]], ["original"])

    def test_03_rename_fails_without_precheck_reports_verified_state(self):
        """DEL-02：預先檢查通過、實際改名卻失敗（EPERM）：檔案沒動，訊息寫「已確認所有檔案都在原位置」。"""
        m = self.new_txt("EpermDir")
        real = os.rename

        def eperm(a, b):
            raise PermissionError(1, "Operation not permitted")
        purge.os.rename = eperm
        try:
            st, job = self.purge_job(m)
        finally:
            purge.os.rename = real
        self.assertEqual(st, "failed")
        self.assertIn("Operation not permitted", job["error"])
        self.assertIn("已確認所有檔案都在原位置", job["error"])
        self.assertTrue(os.path.isfile(self.p("EpermDir", "delete_test.txt")))
        self.assertTrue(self.conn.val("SELECT 1 FROM media WHERE id = %s", [m["id"]]))

    def make_video_with_subtitle(self, folder: str) -> dict:
        os.makedirs(self.p(folder), exist_ok=True)
        make_media.mts_interlaced_ac3(self.p(folder, "V.MTS"), 2, 300)
        self.scan()
        m = self.conn.one("SELECT * FROM media WHERE rel_path = %s", [f"{folder}/V.MTS"])
        self.assertEqual(m["convert_status"], "done", m)
        self.assertTrue(self.conn.val("SELECT 1 FROM subtitles WHERE media_id = %s", [m["id"]]))
        return m

    def test_04_restore_failure_is_reported_and_retry_completes(self):
        """DEL-04：第 2 個檔案改名失敗、第 1 個又改不回去：不能寫「已復原」，要寫出檔案現在在哪；再按一次刪除可以接著完成。"""
        m = self.make_video_with_subtitle("RestoreFail")
        orig = self.p("RestoreFail", "V.MTS")
        real = os.rename
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] >= 2:                 # 第 2 個改名（播放檔）失敗，之後的改回也失敗
                raise PermissionError(1, "Operation not permitted")
            real(a, b)
        purge.os.rename = flaky
        try:
            st, job = self.purge_job(m)
        finally:
            purge.os.rename = real
        self.assertEqual(st, "failed")
        self.assertNotIn("已確認所有檔案都在原位置", job["error"])
        self.assertIn("沒有完成復原", job["error"])
        self.assertIn(".istudy-purge-", job["error"])
        self.assertFalse(os.path.exists(orig))
        self.assertEqual(len(self.hidden("RestoreFail")), 1, "原檔在暫存名稱（這就是實機「看起來消失」的情況）")
        self.assertTrue(self.conn.val("SELECT 1 FROM media WHERE id = %s", [m["id"]]), "資料庫紀錄沒有刪")
        states = {x["label"]: x["state"] for x in self.res(job)["items"]}
        self.assertEqual(states["原檔"], "staged")
        # 再按一次「永久刪除」：接著完成
        st, job = self.purge_job(m)
        self.assertEqual(st, "done", job["error"])
        self.assertEqual(self.hidden("RestoreFail"), [])
        self.assertFalse(os.path.exists(self.ctx.paths.playback_file(m["id"])))
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [m["id"]]))

    def test_05_cleanup_failure_after_commit_is_success_with_pending(self):
        """DEL-05：資料庫已提交、暫存檔刪不掉：工作算成功（不是失敗），列為待清理；Agent 重新啟動時再刪。"""
        m = self.new_txt("CleanupFail")
        real = os.remove

        def busy(f):
            if purge.STAGE_PREFIX in f:
                raise PermissionError(1, "Operation not permitted")
            real(f)
        purge.os.remove = busy
        try:
            st, job = self.purge_job(m)
        finally:
            purge.os.remove = real
        self.assertEqual(st, "done", job["error"])
        r = self.res(job)
        self.assertTrue(r["committed"])
        self.assertEqual(len(r["left"]), 1)
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [m["id"]]))
        self.assertEqual(len(self.hidden("CleanupFail")), 1)
        out = purge.recover_unfinished(self.ctx)            # Agent 重新啟動
        self.assertEqual(out["removed"], 1)
        self.assertEqual(self.hidden("CleanupFail"), [], "課程資料夾裡的暫存檔也會處理")

    def test_06_agent_restart_recovers(self):
        """DEL-06：(a) 改名後、提交前中斷 → 同一工作重新排入後接著完成；(b) 失敗留下暫存檔 → 重啟時改回原名（教材還在）。"""
        m = self.new_txt("Restart")
        items, _ = purge.plan(self.ctx, m)
        jid = self.ctx.enqueue("purge_media", {"media_id": m["id"], "rel_path": m["rel_path"]}, 90)
        self.ctx.progress(jid, {"items": items, "plan": items})
        os.rename(items[0]["path"], items[0]["staged"])
        self.conn.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = %s", [jid])
        wmain.recover(self.ctx)                           # Agent 重新啟動：running → queued
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [jid]), "queued")
        st, job = self.purge_job(m, jid)
        self.assertEqual(st, "done", job["error"])
        self.assertEqual(self.hidden("Restart"), [])
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [m["id"]]))
        # (b)
        m2 = self.new_txt("Restart", "second.txt")
        items, _ = purge.plan(self.ctx, m2)
        jid = self.ctx.enqueue("purge_media", {"media_id": m2["id"], "rel_path": m2["rel_path"]}, 90)
        self.ctx.progress(jid, {"items": items})
        os.rename(items[0]["path"], items[0]["staged"])
        self.conn.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", [jid])
        out = wmain.recover(self.ctx)
        self.assertEqual(out["purge_recovered"]["restored"], 1)
        self.assertTrue(os.path.isfile(self.p("Restart", "second.txt")), "教材紀錄還在 → 改回原名")
        self.assertEqual(self.hidden("Restart"), [])

    def test_07_same_id_old_derived_files_untouched(self):
        """DEL-07：同 ID 的舊衍生檔（資料庫沒有記錄屬於這個教材）不刪；衍生資料夾裡教材還在的暫存檔也不被啟動清理刪掉。"""
        m = self.new_txt("OldId")
        stale = [self.ctx.paths.playback_file(m["id"]), self.ctx.paths.subtitle_file(m["id"]), self.ctx.paths.doc_file(m["id"])]
        for f in stale:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "wb") as fh:
                fh.write(b"old install")
        try:
            items, notes = purge.plan(self.ctx, m)
            self.assertEqual([x["label"] for x in items], ["原檔"])
            self.assertEqual(sum("同 ID 的舊檔" in n for n in notes), 3)
            st, job = self.purge_job(m)
            self.assertEqual(st, "done", job["error"])
            self.assertTrue(all(os.path.isfile(f) for f in stale), "舊檔不動")
            # 教材還在的暫存檔（沒完成的刪除）：啟動清理不刪
            live = self.conn.val("SELECT id FROM media WHERE kind = 'video' ORDER BY id LIMIT 1")
            keep = os.path.join(self.ctx.paths.playback, f"{purge.STAGE_PREFIX}{live}-0")
            gone = os.path.join(self.ctx.paths.playback, f"{purge.STAGE_PREFIX}999999-0")
            for f in (keep, gone):
                with open(f, "wb") as fh:
                    fh.write(b"x")
            self.assertEqual(purge.cleanup_staged(self.ctx), 1)
            self.assertTrue(os.path.exists(keep) and not os.path.exists(gone))
            os.remove(keep)
        finally:
            for f in stale:
                if os.path.exists(f):
                    os.remove(f)

    def test_08_video_with_subtitle_and_pdf_regression(self):
        """DEL-03：已有字幕的影片、PDF：照常刪除原檔、播放檔、字幕檔與紀錄。"""
        m = self.make_video_with_subtitle("Regress")
        files = [self.p("Regress", "V.MTS"), self.ctx.paths.playback_file(m["id"]), self.ctx.paths.subtitle_file(m["id"])]
        st, job = self.purge_job(m)
        self.assertEqual(st, "done", job["error"])
        self.assertEqual(self.res(job)["deleted"], ["原檔", "播放用 MP4", "字幕檔"])
        self.assertFalse(any(os.path.lexists(f) for f in files))
        self.assertEqual(self.conn.val("SELECT count(*) FROM subtitle_cues WHERE media_id = %s", [m["id"]]), 0)
        os.makedirs(self.p("Regress"), exist_ok=True)
        make_media.pdf(self.p("Regress", "講義.pdf"))
        self.scan()
        pm = self.conn.one("SELECT * FROM media WHERE rel_path = 'Regress/講義.pdf'")
        st, job = self.purge_job(pm)
        self.assertEqual(st, "done", job["error"])
        self.assertFalse(os.path.exists(self.p("Regress", "講義.pdf")))
        self.scan()
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE rel_path LIKE 'Regress/%%'"), "重新掃描不會復活")

    def test_09_can_rename_reports_missing_write_permission(self):
        d = tempfile.mkdtemp(dir=self.root)
        f = os.path.join(d, "a.txt")
        open(f, "w").close()
        real = os.access
        purge.os.access = lambda p, mode: False
        try:
            self.assertIn("不能寫入資料夾", purge.can_rename(f))
        finally:
            purge.os.access = real
        self.assertIsNone(purge.can_rename(f))
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
