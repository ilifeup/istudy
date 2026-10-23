"""Agent 端到端測試：真實 ffmpeg／LibreOffice、真實 PostgreSQL。

環境變數：DB_HOST、DB_PORT、DB_USER、DB_PASSWORD（需可建立資料庫）。
執行：python3 -m unittest tests.test_agent -v（在 agent/ 目錄）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import shutil
import socket
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import TZ, Paths, in_window, natural_key  # noqa: E402
from worker import clips, main as wmain  # noqa: E402
from worker.context import Ctx  # noqa: E402

import make_media  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "server", "sql", "schema.sql")
TEST_DB = os.environ.get("ISTUDY_TEST_DB", "istudy_agent_t")
INSTALL_ID = "0123456789abcdef0123456789abcdef"


def admin_conn() -> dbmod.Connection:
    return dbmod.connect(dbmod.conninfo_from_env({**os.environ, "DB_NAME": "postgres"}))


class AgentEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="istudy-media-")
        cls.backup_dir = tempfile.mkdtemp(prefix="istudy-backup-")
        make_media.build(cls.root)
        with open(os.path.join(cls.root, ".istudy-media"), "w") as f:
            f.write(INSTALL_ID)
        a = admin_conn()
        a.exec_raw(f"DROP DATABASE IF EXISTS {TEST_DB}")
        a.exec_raw(f"CREATE DATABASE {TEST_DB}")
        a.close()
        os.environ.update({"DB_NAME": TEST_DB, "MEDIA_ROOT": cls.root, "ISTUDY_ENCODER": "cpu", "ISTUDY_X264_PRESET": "ultrafast",
                           "ISTUDY_ASR_ENGINE": "fake", "ISTUDY_RESERVE_GB": "0", "ISTUDY_BACKUP_REQUIRE_MOUNT": "0",
                           "ISTUDY_BACKUP_DIR": cls.backup_dir, "ISTUDY_HOSTCHECK_FILE": os.path.join(cls.root, "hostcheck.json"),
                           "ISTUDY_HOST_CONFIG_DIR": make_media.host_config(tempfile.mkdtemp(prefix="istudy-hostcfg-")), "ISTUDY_BACKUP_MIN_FREE_MB": "1"})
        cls.conn = dbmod.connect()
        with open(SCHEMA, encoding="utf-8") as f:
            cls.conn.exec_raw(f.read())
        # dev.55：與 PHP migrate 相同，依序套用 migrations（0002 之後）
        import glob
        for mf in sorted(glob.glob(os.path.join(os.path.dirname(SCHEMA), "migrations", "[0-9][0-9][0-9][0-9]_*.sql"))):
            with open(mf, encoding="utf-8") as f:
                cls.conn.exec_raw(f.read())
        cls.ctx = Ctx(conn=cls.conn, paths=Paths(cls.root), env=dict(os.environ), encoder="cpu")
        cls.ctx.set_config("install_id", INSTALL_ID)
        cls.ctx.set_config("clip_threshold_gib", 0.0000001)  # 測試檔很小：門檻設極低
        cls.ctx.set_config("conversion_paused", False)  # 新安裝預設暫停（首次設定第 4 步才開放）
        now = _dt.datetime.now(TZ)
        # 讓目前時間落在排程時段內
        cls.ctx.set_config("schedule", {"start": (now - _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "end": (now + _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "auto_convert": True, "auto_whisper": True, "whisper_after_convert": True,
                                        "backup_time": "23:59", "backup_keep": 3})

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.root, ignore_errors=True)
        shutil.rmtree(cls.backup_dir, ignore_errors=True)

    # ---------- helpers ----------
    def drain(self, types=None, limit=200) -> list[tuple[str, str]]:
        done = []
        for _ in range(limit):
            job = wmain.pick(self.ctx, types or list(wmain.HANDLERS))
            if not job:
                break
            st = wmain.run_job(self.ctx, job)
            done.append((job["type"], st))
        return done

    def enqueue(self, jtype, payload=None, created_by=None):
        return self.ctx.enqueue(jtype, payload or {}, 50, created_by)

    def media(self, rel):
        return self.conn.one("SELECT * FROM media WHERE rel_path = %s", [rel])

    # ---------- tests ----------
    def test_00_window_and_sort(self):
        t = lambda h, m: _dt.datetime(2026, 10, 18, h, m, tzinfo=TZ)  # noqa: E731
        self.assertTrue(in_window("22:30", "03:00", t(23, 0)))
        self.assertTrue(in_window("22:30", "03:00", t(2, 59)))
        self.assertFalse(in_window("22:30", "03:00", t(3, 0)))
        self.assertFalse(in_window("22:30", "03:00", t(12, 0)))
        self.assertTrue(in_window("01:00", "05:00", t(4, 0)))
        names = ["0010", "0002", "0001", "a10", "A2"]
        self.assertEqual(sorted(names, key=natural_key), ["0001", "0002", "0010", "A2", "a10"])

    def test_01_dry_run(self):
        self.enqueue("scan", {"dry_run": True})
        self.drain(["scan"])
        run = self.conn.one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        self.assertEqual(run["status"], "done")
        self.assertTrue(run["dry_run"])
        s = run["summary"]
        self.assertEqual(s["by_kind"]["video"]["count"], 6)
        self.assertEqual(s["videos_need_convert"], 4)  # 3 支 MTS＋1 支 AVI
        self.assertEqual(s["courses"], 3)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM media"), 0, "試掃描不可寫入教材")
        reasons = {x["path"]: x["reason"] for x in run["skipped"]}
        self.assertIn("基礎班/第一堂課/notes.xyz", reasons)
        self.assertIn("進修班/連結.pdf", reasons)  # 符號連結略過
        self.assertNotIn(".hidden/secret.pdf", reasons)
        # dev.59：教材根目錄就是 /Media；第一層的系統資料夾不掃描，列在報告裡
        self.assertEqual(s["system_dirs"], ["doc-cache", "playback", "subtitles"])
        self.assertEqual(reasons.get("playback"), "系統資料夾（略過）")

    def test_01b_library_rules(self):
        """dev.59：只有 /Media 第一層的系統資料夾排除；課程裡同名的照常是教材；有問題的檔名略過、不讓整次掃描失敗。"""
        import tempfile
        from worker import scan as wscan
        d = tempfile.mkdtemp(prefix="lib-")
        try:
            for rel in ("playback/9.mp4", "PlayBack2/x.mp4", "Subtitles/1.vtt", "doc-cache/1.pdf", "Backups/a.mp4", "installers/b.pdf",
                        "lost+found/c.mp4", "$RECYCLE.BIN/d.mp4", "System Volume Information/e.pdf", "課程A/第一堂/影片01.mp4",
                        "課程A/第一堂/講義.pdf", "課程A/playback.mp4", "課程A/subtitles/補充.pdf", "課程B/基礎/a: b?# 影片03.mp4",
                        "課程B/" + "長" * 80 + ".pdf", "課程C/a\\b.mp4", "課程C/換\n行.mp4"):
                full = os.path.join(d, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(b"x")
            with open(os.path.join(os.fsencode(d), "課程C".encode(), b"bad\xff.mp4"), "wb") as f:   # 不是 UTF-8 的檔名
                f.write(b"x")
            dirs, files, skipped = wscan.walk(d, [])
            reasons = {x["path"]: x["reason"] for x in skipped}
            for top in ("playback", "Subtitles", "doc-cache", "Backups", "installers", "lost+found", "$RECYCLE.BIN", "System Volume Information"):
                self.assertEqual(reasons.get(top), "系統資料夾（略過）", top)
            self.assertEqual(sorted(files), sorted(["PlayBack2/x.mp4", "課程A/第一堂/影片01.mp4", "課程A/第一堂/講義.pdf", "課程A/playback.mp4",
                                                    "課程A/subtitles/補充.pdf", "課程B/基礎/a: b?# 影片03.mp4", "課程B/" + "長" * 80 + ".pdf"]))
            self.assertIn("課程A/subtitles", dirs, "課程裡剛好叫 subtitles 的子資料夾照常掃描")
            self.assertTrue(any("反斜線" in r for r in reasons.values()))
            self.assertTrue(any("控制字元" in r for r in reasons.values()))
            self.assertTrue(any("UTF-8" in r for r in reasons.values()))
            json.dumps(skipped, ensure_ascii=False).encode("utf-8")   # 報告可以寫進資料庫（不會因為檔名編碼失敗）
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_02_scan(self):
        self.enqueue("scan", {})
        self.drain(["scan"])
        run = self.conn.one("SELECT * FROM scan_runs WHERE NOT dry_run ORDER BY id DESC LIMIT 1")
        self.assertEqual(run["status"], "done", run["error"])
        self.assertEqual(run["added"], 12)
        courses = {r["name"]: r for r in self.conn.execute("SELECT * FROM folders WHERE parent_id IS NULL")}
        self.assertEqual(set(courses), {"基礎班", "進修班", "砭石"})
        self.assertTrue(all(c["is_course"] for c in courses.values()))
        sub = self.conn.one("SELECT * FROM folders WHERE rel_path = '基礎班/第一堂課'")
        self.assertEqual(sub["course_id"], courses["基礎班"]["id"])
        root_pdf = self.media("根目錄.pdf")
        self.assertIsNone(root_pdf["course_id"])
        self.assertFalse(self.conn.val("SELECT COUNT(*) FROM media WHERE rel_path LIKE '.hidden%%'"))
        kinds = {r["rel_path"]: r["kind"] for r in self.conn.execute("SELECT rel_path, kind FROM media")}
        self.assertEqual(kinds["基礎班/第一堂課/課程表.docx"], "office")
        self.assertEqual(kinds["基礎班/第一堂課/說明.txt"], "text")
        jobs = {r["type"]: r["n"] for r in self.conn.execute("SELECT type, COUNT(*) n FROM jobs WHERE status = 'queued' GROUP BY type")}
        self.assertEqual(jobs.get("probe"), 6)
        self.assertEqual(jobs.get("office_pdf"), 1)

    def test_03_process_all(self):
        done = self.drain()
        failed = self.conn.execute("SELECT id, type, error FROM jobs WHERE status = 'failed'")
        self.assertEqual(failed, [], failed)
        m = {r["file_name"]: r for r in self.conn.execute("SELECT * FROM media WHERE kind = 'video'")}
        self.assertEqual(m["0001_上集.mp4"]["play_mode"], "direct")
        self.assertEqual(m["0001_上集.mp4"]["convert_status"], "direct")
        self.assertEqual(m["0002 中集.mp4"]["play_mode"], "remux")  # 索引在檔尾 → 換封裝
        self.assertEqual(m["00019.MTS"]["play_mode"], "transcode")  # 交錯 → 去交錯重新編碼
        self.assertTrue(m["00019.MTS"]["probe"]["interlaced"])
        self.assertEqual(m["00019.MTS"]["probe"]["audio_codec"], "ac3")
        self.assertEqual(m["示範 a.avi"]["play_mode"], "transcode")
        for name in ("0002 中集.mp4", "00019.MTS", "00020.MTS", "00021.MTS", "示範 a.avi"):
            r = m[name]
            self.assertEqual(r["convert_status"], "done", (name, r["convert_error"]))
            self.assertTrue(r["verify_result"]["passed"])
            self.assertEqual(len(r["verify_result"]["checks"]), 4)
            self.assertTrue(os.path.exists(os.path.join(self.root, "playback", f"{r['id']}.mp4")))
            self.assertAlmostEqual((r["delete_due_at"] - r["verified_at"]).days, 30)
        # 字幕（fake 引擎）與 OpenCC 轉換後寫入 cues
        subs = self.conn.val("SELECT COUNT(*) FROM subtitles")
        self.assertEqual(subs, 6)
        self.assertGreater(self.conn.val("SELECT COUNT(*) FROM subtitle_cues"), 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, "subtitles", f"{m['00019.MTS']['id']}.vtt")))
        # dev.57：自動字幕記錄引擎、速度與品質檢查（假引擎走同一條字幕管線）
        rep = self.conn.val("SELECT asr_report FROM subtitles WHERE media_id = %s", [m["00019.MTS"]["id"]])
        self.assertEqual((rep["engine"], rep["engine_version"]), ("paraformer-zh", "fake"))
        self.assertIn(rep["qc"]["status"], ("ok", "needs_review"))
        self.assertIn("rtf", rep["metrics"])
        self.assertEqual(self.conn.val("SELECT model FROM subtitles WHERE media_id = %s", [m["00019.MTS"]["id"]]), "paraformer-zh")
        doc = self.media("基礎班/第一堂課/課程表.docx")
        self.assertEqual(doc["doc_status"], "done", doc["doc_error"])
        self.assertTrue(os.path.getsize(os.path.join(self.root, "doc-cache", f"{doc['id']}.pdf")) > 500)
        self.assertIn(("convert", "done"), done)

    def test_04_clip_group(self):
        rows = self.conn.execute("SELECT file_name, clip_group_id, clip_seq FROM media WHERE ext = 'mts' ORDER BY file_name")
        gids = {r["clip_group_id"] for r in rows}
        self.assertEqual(len(gids), 1)
        self.assertIsNotNone(gids.pop())
        self.assertEqual([r["clip_seq"] for r in rows], [1, 2, 3])
        g = self.conn.one("SELECT * FROM clip_groups")
        self.assertTrue(g["name"].startswith("00019～00021"))
        self.assertEqual(g["source"], "auto")
        # 門檻提高 → 不再符合（前一支太小）→ 自動合集取消
        folder = self.conn.val("SELECT folder_id FROM media WHERE file_name = '00019.MTS'")
        clips.detect(self.conn, folder, 1.9)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM clip_groups"), 0)
        clips.detect(self.conn, folder, 0.0000001)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM clip_groups"), 1)
        # 手動調整過的不參與自動判定
        self.conn.execute("UPDATE media SET clip_manual = TRUE, clip_group_id = NULL, clip_seq = NULL WHERE file_name = '00021.MTS'")
        clips.detect(self.conn, folder, 0.0000001)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM media WHERE clip_group_id IS NOT NULL"), 2)
        self.conn.execute("UPDATE media SET clip_manual = FALSE WHERE file_name = '00021.MTS'")
        clips.detect(self.conn, folder, 0.0000001)
        self.assertEqual(self.conn.val("SELECT COUNT(*) FROM media WHERE clip_group_id IS NOT NULL"), 3)

    def test_05_move_rename_and_missing(self):
        c1 = os.path.join(self.root, "基礎班", "第一堂課")
        before = self.media("基礎班/第一堂課/講義.pdf")
        os.rename(os.path.join(c1, "講義.pdf"), os.path.join(self.root, "基礎班", "講義（新）.pdf"))
        os.remove(os.path.join(c1, "示意圖.png"))
        self.enqueue("scan")
        self.drain(["scan"])
        run = self.conn.one("SELECT * FROM scan_runs WHERE NOT dry_run ORDER BY id DESC LIMIT 1")
        self.assertEqual((run["moved"], run["missing"], run["added"]), (1, 1, 0), run)
        after = self.media("基礎班/講義（新）.pdf")
        self.assertEqual(after["id"], before["id"], "搬移後要沿用原教材 ID")
        self.assertEqual(self.media("基礎班/第一堂課/示意圖.png")["status"], "missing")

    def test_06_space_low_retry(self):
        mid = self.media("進修班/示範 a.avi")["id"]
        self.conn.execute("UPDATE media SET convert_status = 'queued' WHERE id = %s", [mid])
        self.ctx.env["ISTUDY_RESERVE_GB"] = "999999"
        jid = self.enqueue("convert", {"media_id": mid})
        self.drain(["convert"])
        j = self.conn.one("SELECT * FROM jobs WHERE id = %s", [jid])
        self.assertEqual(j["status"], "queued")
        self.assertIn("空間不足", j["error"])
        self.assertIn("space_low", self.ctx.flags)
        self.ctx.env["ISTUDY_RESERVE_GB"] = "0"
        self.conn.execute("UPDATE jobs SET run_after = NOW() WHERE id = %s", [jid])
        self.drain(["convert"])
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [jid]), "done")

    def test_07_window_closed_blocks_convert(self):
        now = _dt.datetime.now(TZ)
        sch = self.ctx.config("schedule")
        self.ctx.set_config("schedule", {**sch, "start": (now + _dt.timedelta(hours=2)).strftime("%H:%M"),
                                         "end": (now + _dt.timedelta(hours=3)).strftime("%H:%M")})
        mid = self.media("進修班/示範 a.avi")["id"]
        jid = self.enqueue("convert", {"media_id": mid})
        self.assertIsNone(wmain.pick(self.ctx, ["convert"]), "時段外不可開始轉檔")
        imm = self.ctx.enqueue("convert", {"media_id": mid, "immediate": True}, 30)
        job = wmain.pick(self.ctx, ["convert"])
        self.assertEqual(job["id"], imm, "立即執行不受時段限制")
        wmain.run_job(self.ctx, job)
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", [jid])
        self.ctx.set_config("schedule", sch)

    def test_08_asr_waits_for_convert(self):
        mid = self.media("基礎班/第一堂課/0001_上集.mp4")["id"]
        self.conn.execute("DELETE FROM subtitles WHERE media_id = %s", [mid])
        w = self.enqueue("asr", {"media_id": mid, "engine": "paraformer-zh"})
        c = self.ctx.enqueue("convert", {"media_id": self.media("進修班/示範 a.avi")["id"]}, 0)
        job = wmain.pick(self.ctx, ["asr", "convert"])
        self.assertEqual(job["id"], c, "字幕要等轉檔佇列空了")
        wmain.run_job(self.ctx, job)
        job = wmain.pick(self.ctx, ["asr"])
        self.assertEqual(job["id"], w)
        # dev.58：Log 要寫出要求的引擎與實際用的引擎（實機 dev.57 只寫「job 37 whisper done」）
        self.assertEqual(wmain.start_note(job), " engine=paraformer-zh")
        with self.assertLogs("istudy.agent", level="INFO") as lg:
            wmain.run_job(self.ctx, job)
        self.assertTrue(any(f"job {w} asr done" in m and "engine=paraformer-zh" in m and "rtf=" in m for m in lg.output), lg.output)
        self.assertEqual(self.conn.val("SELECT source || '/' || model FROM subtitles WHERE media_id = %s", [mid]), "asr/paraformer-zh")
        # 資料庫不再接受舊名稱：漏改的程式會立刻報錯，不會悄悄卡住
        with self.assertRaises(Exception):
            self.ctx.enqueue("whisper", {"media_id": mid}, 0)

    def test_08a_regenerate_keeps_old_subtitle_until_new_one_succeeds(self):
        """dev.60 SUB-01：重新產生失敗時（辨識失敗、資料庫寫入失敗）舊字幕的資料庫內容與 VTT 檔都不變。"""
        from worker import subtitles as wsub
        mid = self.media("基礎班/第一堂課/0001_上集.mp4")["id"]
        self.conn.execute("DELETE FROM jobs WHERE type = 'asr' AND status IN ('queued','running')")
        j = self.enqueue("asr", {"media_id": mid, "engine": "sensevoice-small"})
        wmain.run_job(self.ctx, wmain.pick(self.ctx, ["asr"]))
        vtt = self.ctx.paths.subtitle_file(mid)
        before = (self.conn.val("SELECT model || '/' || segment_count FROM subtitles WHERE media_id = %s", [mid]), open(vtt).read())
        self.assertTrue(before[0].startswith("sensevoice-small/"), before)
        # 1) 辨識失敗（指定已移除的引擎）
        j = self.enqueue("asr", {"media_id": mid, "engine": "whisper"})
        self.assertEqual(wmain.run_job(self.ctx, wmain.pick(self.ctx, ["asr"])), "failed")
        # 2) 辨識成功但資料庫寫入失敗
        real = wsub._save_rows
        def boom(*a, **k):
            raise RuntimeError("模擬資料庫寫入失敗")
        wsub._save_rows = boom
        try:
            j = self.enqueue("asr", {"media_id": mid, "engine": "paraformer-zh"})
            self.assertEqual(wmain.run_job(self.ctx, wmain.pick(self.ctx, ["asr"])), "failed")
        finally:
            wsub._save_rows = real
        after = (self.conn.val("SELECT model || '/' || segment_count FROM subtitles WHERE media_id = %s", [mid]), open(vtt).read())
        self.assertEqual(after, before, "失敗時保留舊字幕")
        self.assertFalse(os.path.exists(vtt + ".tmp"), "暫存檔已清除")
        # 3) 重新產生成功才取代
        j = self.enqueue("asr", {"media_id": mid, "engine": "paraformer-zh"})
        self.assertEqual(wmain.run_job(self.ctx, wmain.pick(self.ctx, ["asr"])), "done")
        self.assertTrue(self.conn.val("SELECT model FROM subtitles WHERE media_id = %s", [mid]) == "paraformer-zh")

    def test_08b_unknown_job_type_is_logged(self):
        self.conn.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_type_check")
        try:
            jid = self.conn.val("INSERT INTO jobs(type, payload) VALUES ('mystery', '{}') RETURNING id")
            with self.assertLogs("istudy.agent", level="WARNING") as lg:
                rows = wmain.warn_unknown_types(self.ctx)
            self.assertEqual([r["id"] for r in rows], [jid])
            self.assertTrue(any("type=mystery" in m for m in lg.output))
            self.conn.execute("DELETE FROM jobs WHERE id = %s", [jid])
        finally:
            self.conn.execute("DELETE FROM jobs WHERE type NOT IN ('scan','probe','convert','verify','delete_original','asr','office_pdf','wol_wake',"
                              "'diagnose','backup','cleanup','subtitle_import','net_probe','lan_scan','backup_check','email_report')")
            self.conn.execute("ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN ('scan','probe','convert','verify','delete_original','asr',"
                              "'office_pdf','wol_wake','diagnose','backup','cleanup','subtitle_import','net_probe','lan_scan','backup_check','email_report'))")

    def test_09_subtitle_import(self):
        mid = self.media("進修班/示範 a.avi")["id"]
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n匯入的第一句\n\n00:00:01.500 --> 00:00:03.000\n第二句\n"
        self.enqueue("subtitle_import", {"media_id": mid, "vtt": vtt, "account_id": None})
        self.drain(["subtitle_import"])
        cues = self.conn.execute("SELECT * FROM subtitle_cues WHERE media_id = %s ORDER BY idx", [mid])
        self.assertEqual([c["text"] for c in cues], ["匯入的第一句", "第二句"])
        self.assertEqual(self.conn.val("SELECT source FROM subtitles WHERE media_id = %s", [mid]), "import")

    def test_10_delete_original(self):
        r = self.media("基礎班/第一堂課/00020.MTS")
        src = os.path.join(self.root, r["rel_path"])
        self.assertTrue(os.path.exists(src))
        # 未滿 30 天：拒絕
        j1 = self.enqueue("delete_original", {"media_id": r["id"], "expected_playback_size": r["playback_size"]})
        self.drain(["delete_original"])
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [j1]), "failed")
        self.assertTrue(os.path.exists(src))
        # MP4 大小不符：拒絕
        self.conn.execute("UPDATE media SET delete_due_at = NOW() - INTERVAL '1 day' WHERE id = %s", [r["id"]])
        j2 = self.enqueue("delete_original", {"media_id": r["id"], "expected_playback_size": 1})
        self.drain(["delete_original"])
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [j2]), "failed")
        self.assertTrue(os.path.exists(src))
        j3 = self.enqueue("delete_original", {"media_id": r["id"], "expected_playback_size": r["playback_size"]})
        self.drain(["delete_original"])
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [j3]), "done")
        self.assertFalse(os.path.exists(src))
        after = self.media("基礎班/第一堂課/00020.MTS")
        self.assertEqual(after["status"], "original_deleted")
        self.assertIsNotNone(after["clip_group_id"], "刪原檔後合集紀錄仍保留")
        # 重新掃描不可把已刪原檔的教材標成遺失
        self.enqueue("scan")
        self.drain(["scan"])
        self.assertEqual(self.media("基礎班/第一堂課/00020.MTS")["status"], "original_deleted")
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log WHERE action = 'original.deleted'"))

    def test_11_media_marker(self):
        marker = os.path.join(self.root, ".istudy-media")
        with open(marker, "w") as f:
            f.write("ffffffffffffffffffffffffffffffff")
        j = self.enqueue("scan")
        self.drain(["scan"])
        row = self.conn.one("SELECT status, error FROM jobs WHERE id = %s", [j])
        self.assertEqual(row["status"], "failed")
        self.assertIn("媒體標記", row["error"])
        with open(marker, "w") as f:
            f.write(INSTALL_ID)

    def _age_key(self) -> str:
        """測試用 age 金鑰：私鑰寫在暫存檔（正式環境只在主機 root），公鑰給 Agent。"""
        age = os.environ.get("ISTUDY_AGE_BIN", "age")
        keygen = os.path.join(os.path.dirname(age), "age-keygen") if os.path.isabs(age) else "age-keygen"
        ident = os.path.join(self.root, "test-identity.key")
        if not os.path.exists(ident):
            subprocess.run([keygen, "-o", ident], check=True, capture_output=True)
        rec = subprocess.run([keygen, "-y", ident], check=True, capture_output=True, text=True).stdout.strip()
        self.ctx.set_config("backup_key", {"recipient": rec})
        return ident

    def test_12_backup_and_verify(self):
        # dev.62 方案 B：age 加密；資料庫＋字幕＋設定，不含教材原檔（影片、文件）
        ident = self._age_key()
        self.enqueue("backup", {"kind": "manual"})
        self.drain(["backup"])
        b = self.conn.one("SELECT * FROM backup_history ORDER BY id DESC LIMIT 1")
        self.assertEqual(b["status"], "success", b["error_message"])
        self.assertEqual((b["format"], b["enc_status"]), ("age", "unverified"))
        import io
        import tarfile
        raw = subprocess.run([os.environ.get("ISTUDY_AGE_BIN", "age"), "-d", "-i", ident, b["file_path"]], check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
            names = t.getnames()
        self.assertIn("db.sql", names)
        self.assertTrue(any(n.startswith("subtitles/") for n in names))
        self.assertFalse(any(n.endswith("課程表.docx") for n in names), "方案 B 不含教材原檔")
        self.assertFalse(any(n.endswith((".MTS", ".mp4", ".avi")) for n in names), "影片不進日常備份")
        # 未掛載備份磁碟：失敗
        self.ctx.env["ISTUDY_BACKUP_REQUIRE_MOUNT"] = "1"
        self.enqueue("backup", {"kind": "manual"})
        self.drain(["backup"])
        self.assertIn("備份磁碟未連接", self.conn.val("SELECT error_message FROM backup_history ORDER BY id DESC LIMIT 1"))
        self.ctx.env["ISTUDY_BACKUP_REQUIRE_MOUNT"] = "0"

    def test_13_diagnose_wol_cleanup(self):
        with open(self.ctx.env["ISTUDY_HOSTCHECK_FILE"], "w") as f:
            json.dump({"checks": [{"id": "zfs", "name": "ZFS", "status": "pass", "message": ""}],
                       "system": {"os": "Ubuntu 26.04.1 LTS", "python": "3.12"}, "zfs": {"pools": [{"name": "media", "health": "ONLINE"}]}}, f)
        self.enqueue("diagnose")
        self.drain(["diagnose"])
        d = self.conn.one("SELECT data FROM diagnostics WHERE kind = 'server' ORDER BY id DESC LIMIT 1")["data"]
        self.assertIn(d["status"], ("pass", "warn"))
        self.assertEqual(self.conn.val("SELECT auto_value FROM checklist_items WHERE title = 'Server：Ubuntu 版本'"), "Ubuntu 26.04.1 LTS")
        # WOL：送到本機 UDP，確認封包格式
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        port = rx.getsockname()[1]
        rx.settimeout(3)
        from worker import wol
        wol.send("aa:bb:cc:dd:ee:ff", "127.0.0.1", port, 1)
        data, _ = rx.recvfrom(200)
        rx.close()
        self.assertEqual(data[:6], b"\xff" * 6)
        self.assertEqual(data[6:12], bytes.fromhex("aabbccddeeff"))
        self.assertEqual(len(data), 102)
        self.enqueue("cleanup")
        self.drain(["cleanup"])
        self.assertTrue(self.conn.val("SELECT 1 FROM audit_log LIMIT 1"), "使用記錄不可被清除")

    def test_14_recover_and_scheduler(self):
        jid = self.enqueue("convert", {"media_id": self.media("進修班/示範 a.avi")["id"]})
        self.conn.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = %s", [jid])
        open(os.path.join(self.root, "playback", ".999.tmp.mp4"), "w").close()
        info = wmain.recover(self.ctx)
        self.assertGreaterEqual(info["requeued"], 1)
        self.assertEqual(info["temp_removed"], 1)
        self.assertEqual(self.conn.val("SELECT status FROM jobs WHERE id = %s", [jid]), "queued")
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", [jid])
        sch = self.ctx.config("schedule")
        self.ctx.set_config("schedule", {**sch, "backup_time": "00:00"})
        self.ctx.set_config("scheduler_state", {})
        created = wmain.scheduler_tick(self.ctx, _dt.datetime.now(TZ).replace(hour=12))
        self.assertIn("backup", created)
        self.assertNotIn("backup", wmain.scheduler_tick(self.ctx, _dt.datetime.now(TZ).replace(hour=12)), "同一天不重複排備份")
        self.ctx.set_config("schedule", sch)

    # ---------- dev.55 ----------
    def test_16_dev55_encoder_tiers(self):
        from worker import maintenance, media_tools
        info = {"has_audio": True, "interlaced": True}
        cmd = media_tools.build_command("transcode", "in.mts", "out.mp4", info, "vaapi")
        self.assertEqual(cmd[cmd.index("-vaapi_device") + 1], media_tools.VAAPI_DEVICE)
        self.assertLess(cmd.index("-vaapi_device"), cmd.index("-i"))
        self.assertEqual(cmd[cmd.index("-vf") + 1], "bwdif,format=nv12,hwupload")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_vaapi")
        self.assertIn("+faststart", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac", "保留音訊")
        cpu = media_tools.build_command("transcode", "in.mts", "out.mp4", info, "cpu")
        self.assertEqual(cpu[cpu.index("-c:v") + 1], "libx264")
        self.assertNotIn("-vaapi_device", cpu)
        # 三層選擇：QSV 失敗＋VA-API 通過 → VA-API（不直接落 CPU）
        tests = {"qsv": {"ok": False, "message": "h264_qsv 測試編碼失敗：MFX session -9"}, "vaapi": {"ok": True}, "cpu": {"ok": True}}
        self.assertEqual(wmain.choose_encoder(tests), ("vaapi", "Quick Sync：h264_qsv 測試編碼失敗：MFX session -9"))
        self.assertEqual(wmain.choose_encoder({**tests, "vaapi": {"ok": False, "message": "x"}})[0], "cpu")
        self.assertEqual(wmain.choose_encoder(tests, "cpu"), ("cpu", "設定指定"))
        # 診斷：VA-API 使用中 → 實際使用 PASS、QSV 標「略過（不影響）」而不是整個 GPU 失敗；CPU 降級 → WARN 並列出原因
        r = maintenance.encoder_result("vaapi", tests)
        self.assertEqual((r["status"], r["value"]), ("pass", "Intel VA-API（h264_vaapi）"))
        tiers = {t["id"]: t for t in maintenance.encoder_tiers("vaapi", {**tests, "qsv": {**tests["qsv"], "stderr": "line1\nline2"}})}
        self.assertEqual(tiers["encoder_qsv"]["status"], "skip")
        self.assertEqual(tiers["encoder_qsv"]["detail"], "line1\nline2", "保留 FFmpeg 錯誤輸出")
        self.assertEqual(tiers["encoder_vaapi"]["message"], "使用中")
        down = maintenance.encoder_result("cpu", {**tests, "vaapi": {"ok": False, "message": "h264_vaapi 測試編碼失敗"}})
        self.assertEqual(down["status"], "warn")
        self.assertIn("已降級為 CPU", down["message"])
        self.assertIn("VA-API：h264_vaapi 測試編碼失敗", down["message"])
        # 這台開發機沒有 /dev/dri：硬體兩層都回報找不到裝置，CPU 一定可用
        if not os.path.exists(media_tools.VAAPI_DEVICE):
            p = media_tools.probe_encoders()
            self.assertEqual((p["qsv"]["ok"], p["vaapi"]["ok"], p["cpu"]["ok"]), (False, False, True))

    def test_17_dev55_vaapi_failure_falls_back_in_order(self):
        """轉檔時硬體失敗：QSV → VA-API → CPU 依序嘗試，每一步都寫 Log；最後實際使用的編碼器寫回 media.encoder。"""
        from worker import video
        mid = self.conn.val("SELECT id FROM media WHERE kind = 'video' AND play_mode = 'transcode' ORDER BY id LIMIT 1")
        self.assertIsNotNone(mid)
        self.conn.execute("UPDATE media SET convert_status = 'queued', playback_path = NULL WHERE id = %s", [mid])
        calls = []
        real = video.run_ffmpeg

        def fake(cmd, duration, progress, should_stop=None):
            enc = cmd[cmd.index("-c:v") + 1]
            calls.append(enc)
            if enc in ("h264_qsv", "h264_vaapi"):
                return 1, "Error: device busy\n"
            return real(cmd, duration, progress, should_stop)
        old = (self.ctx.encoder, self.ctx.encoders)
        self.ctx.encoder, self.ctx.encoders = "qsv", {"qsv": {"ok": True}, "vaapi": {"ok": True}, "cpu": {"ok": True}}
        video.run_ffmpeg = fake
        try:
            self.enqueue("convert", {"media_id": mid, "immediate": True})
            self.drain(["convert"])
        finally:
            video.run_ffmpeg = real
            self.ctx.encoder, self.ctx.encoders = old
        self.assertEqual(calls, ["h264_qsv", "h264_vaapi", "libx264"])
        self.assertEqual(self.conn.val("SELECT encoder FROM media WHERE id = %s", [mid]), "cpu")
        logs = [r["message"] for r in self.conn.execute(
            "SELECT message FROM system_logs WHERE context->>'media_id' = %s ORDER BY id", [str(mid)])]
        self.assertTrue(any("Quick Sync 轉檔失敗，改用 VA-API" in m for m in logs), logs)
        self.assertTrue(any(m.startswith("⚠ 轉檔降級為 CPU") for m in logs), logs)

    def test_18_dev55_backup_check_separate(self):
        from worker import maintenance
        self._age_key()
        self.enqueue("backup", {"kind": "manual"})
        self.drain(["backup"])
        b = self.conn.one("SELECT * FROM backup_history WHERE status = 'success' ORDER BY id DESC LIMIT 1")
        self.assertIsNone(b["verify_result"], "建立備份不做還原")
        # dev.62：加密備份的完整性＝SHA-256；還原測試需要私鑰，Agent 拒絕（由主機執行）
        self.enqueue("backup_check", {"backup_id": b["id"], "mode": "integrity"})
        self.enqueue("backup_check", {"backup_id": b["id"], "mode": "restore_test"})
        self.assertEqual([st for _, st in self.drain(["backup_check"])], ["done", "failed"])
        v = self.conn.val("SELECT verify_result FROM backup_history WHERE id = %s", [b["id"]])
        self.assertTrue(v["integrity"]["passed"], v)
        self.assertNotIn("restore_test", v)
        # 舊格式（未加密 .tar.gz）的檢查照舊：損壞的壓縮檔完整性不通過（不碰資料庫）
        bad = os.path.join(self.backup_dir, "istudy-backup-broken.tar.gz")
        import tarfile
        with tarfile.open(bad, "w:gz") as t:
            t.add(__file__, "db.sql")
        with open(bad, "rb") as f:
            data = f.read()
        with open(bad, "wb") as f:
            f.write(data[: len(data) // 2])
        r = maintenance.integrity_check(bad)
        self.assertFalse(r["passed"])
        self.assertIn("損壞", r["message"])
        # 備份目錄以外的路徑一律拒絕
        bid = self.conn.val("INSERT INTO backup_history(kind, status, file_path) VALUES ('manual', 'success', '/etc/passwd') RETURNING id")
        self.enqueue("backup_check", {"backup_id": bid, "mode": "integrity"})
        self.assertEqual([st for _, st in self.drain(["backup_check"])], ["failed"])
        os.remove(bad)

    def test_19_dev55_net_helpers(self):
        from worker import net
        info = {"iface": "eno1", "ip": "10.3.3.100", "netmask": "255.255.0.0", "network": "10.3.0.0/16", "broadcast": "10.3.255.255"}
        rng, hosts, notes = net.scan_range(info)
        self.assertEqual((str(rng), len(hosts)), ("10.3.3.0/24", 253), "超過 /24 只掃 Server 所在的 /24，且不含自己")
        self.assertNotIn("10.3.3.100", hosts)
        self.assertTrue(notes)
        with self.assertRaises(Exception):
            net.scan_range({"ip": "8.8.8.8", "network": "8.8.8.0/24"})
        arp = os.path.join(self.root, "arp")
        with open(arp, "w") as f:
            f.write("IP address       HW type     Flags       HW address            Mask     Device\n"
                    "10.3.3.21        0x1         0x2         AA:BB:CC:00:11:22     *        eno1\n"
                    "10.3.3.22        0x1         0x0         00:00:00:00:00:00     *        eno1\n"
                    "172.17.0.2       0x1         0x2         02:42:ac:11:00:02     *        docker0\n")
        self.assertEqual(net.read_arp("eno1", arp), {"10.3.3.21": "aa:bb:cc:00:11:22"}, "只取已解析、同一網卡的項目")
        route = os.path.join(self.root, "route")
        with open(route, "w") as f:
            f.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                    "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n")
        self.assertEqual(net.lan_info(route_file=route), {}, "只有 docker 網卡時判斷不出來，不猜")
        live = net.lan_info()
        if live:
            self.assertIn("broadcast", live)
        try:
            self.assertEqual(net.ping_many(["127.0.0.1"], timeout=0.5), {"127.0.0.1": True})
        except net.PingUnavailable as e:
            self.skipTest(f"這台開發機不能 ping：{e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
