"""dev.61：Outgo（掃描排除、路徑隔離、錄音 ASR 工作）與 Super 永久刪除教材（成功、失敗保護、不復活）。

真實 PostgreSQL、真實 ffmpeg；ASR 用 fake 引擎（開發環境沒有 FunASR 模型），另外用真的 ffmpeg 驗證各種錄音格式都能解碼。
只用測試產生的暫存教材，不碰任何正式教材。
環境變數：DB_HOST、DB_PORT、DB_USER、DB_PASSWORD（需可建立資料庫）。
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from istudy_common import db as dbmod  # noqa: E402
from istudy_common.util import TZ, Paths  # noqa: E402
from worker import main as wmain, outgo, purge, scan  # noqa: E402
from worker.asr import quality  # noqa: E402
from worker.asr import audio, text as asr_text  # noqa: E402
from worker.context import Ctx  # noqa: E402

import make_media  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "server", "sql", "schema.sql")
TEST_DB = os.environ.get("ISTUDY_TEST_DB61", "istudy_dev61_t")
INSTALL_ID = "61616161616161616161616161616161"


def tone(path: str, seconds: float = 2.0, extra: list[str] | None = None) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}", *(extra or []), path], check=True, timeout=120)


def synth_wav(path: str, speech_rms: float, noise_rms: float, clip: bool = False, narrow: bool = False, seconds: int = 20) -> list:
    """合成「語音段落＋背景雜音」的 16 kHz WAV；回傳語音段落（ms）。語音用白噪音（有高頻），narrow＝只有 3 kHz 以下。"""
    import numpy as np
    import wave as _w
    rng = np.random.default_rng(61)
    sr = 16000
    x = rng.normal(0, noise_rms, sr * seconds)
    segs = [(1000, 4000), (6000, 9000), (11000, 14000), (16000, 19000)]
    for a, b in segs:
        n = (b - a) * sr // 1000
        if narrow:
            t = np.arange(n) / sr
            v = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (220, 470, 900, 1600, 2600))
            v = v / np.sqrt(np.mean(v ** 2)) * speech_rms
        else:
            v = rng.normal(0, speech_rms, n)
        x[a * sr // 1000:a * sr // 1000 + n] += v
    if clip:
        x = np.clip(x, -1.0, 1.0)
    pcm = (np.clip(x, -1, 32767 / 32768) * 32768).astype("<i2")
    with _w.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return segs


class AudioQuality(unittest.TestCase):
    """dev.61 中文音訊品質（高／中／低）粗略估計：用合成訊號驗證每個判斷依據。"""

    def grade(self, **kw):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "a.wav")
            segs = synth_wav(f, **kw)
            sig = quality.signal_metrics(f, segs)
            return sig, quality.grade(sig)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_clean_is_high(self):
        sig, g = self.grade(speech_rms=0.1, noise_rms=0.001)
        self.assertGreater(sig["snr_db"], 30)
        self.assertEqual((g["level"], g["label"], g["reasons"]), ("high", "高", []))

    def test_noisy_is_low(self):
        sig, g = self.grade(speech_rms=0.1, noise_rms=0.04)
        self.assertLess(sig["snr_db"], 15)
        self.assertEqual(g["level"], "low")
        self.assertIn("背景雜音偏高", g["reasons"][0])

    def test_quiet_is_low(self):
        _sig, g = self.grade(speech_rms=0.003, noise_rms=0.00003)
        self.assertEqual(g["level"], "low")
        self.assertTrue(any("音量偏小" in r for r in g["reasons"]))

    def test_clipping_is_low(self):
        sig, g = self.grade(speech_rms=0.8, noise_rms=0.001, clip=True)
        self.assertGreater(sig["clip_pct"], 1)
        self.assertEqual(g["level"], "low")
        self.assertTrue(any("爆音" in r for r in g["reasons"]))

    def test_narrowband_is_at_most_medium(self):
        sig, g = self.grade(speech_rms=0.1, noise_rms=0.0005, narrow=True)
        self.assertLess(sig["hf_pct"], 0.2)
        self.assertEqual(g["level"], "medium")
        self.assertTrue(any("頻寬窄" in r for r in g["reasons"]))

    def test_recognition_factors_and_unknown(self):
        good = {"snr_db": 35, "speech_dbfs": -20, "clip_pct": 0, "hf_pct": 30}
        self.assertEqual(quality.grade(good, {"empty_ratio": 0.05, "chars_per_sec": 3.8, "qc_status": "ok"})["level"], "high")
        self.assertEqual(quality.grade(good, {"empty_ratio": 0.5})["level"], "low")
        self.assertEqual(quality.grade(good, {"chars_per_sec": 0.6})["level"], "low")
        self.assertEqual(quality.grade(good, {"qc_status": "needs_review"})["level"], "medium")
        self.assertEqual(quality.grade(None)["level"], "unknown")
        self.assertEqual(quality.grade({"error": "x"})["label"], "未知")

    def test_long_audio_memory_is_bounded(self):
        """2 小時的 WAV：逐段讀取，記憶體不隨長度增加太多（每 20 ms 只留一個數字）。"""
        import resource
        import numpy as np
        import wave as _w
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "long.wav")
            sr, sec = 16000, 7200
            block = (np.random.default_rng(1).normal(0, 0.05, sr * 60) * 32768).astype("<i2").tobytes()
            with _w.open(f, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                for _ in range(sec // 60):
                    w.writeframes(block)
            before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            sig = quality.signal_metrics(f, [(i * 60000, i * 60000 + 40000) for i in range(sec // 60)])
            grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
            self.assertEqual(sig["frames"], sec * 50)
            self.assertLess(grown_mb, 120, f"記憶體增加 {grown_mb:.0f} MB")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Dev61(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="istudy-dev61-")
        make_media.build(cls.root)
        with open(os.path.join(cls.root, ".istudy-media"), "w") as f:
            f.write(INSTALL_ID)
        og = os.path.join(cls.root, "Outgo")
        os.makedirs(os.path.join(og, "第一期", "第3堂"))
        tone(os.path.join(og, "第一期", "第3堂", "上課錄音.mp3"))
        tone(os.path.join(og, "討論.m4a"), extra=["-c:a", "aac"])
        with open(os.path.join(og, "講義.pdf"), "wb") as f:
            f.write(b"%PDF-1.4")
        tone(os.path.join(og, "第一期", "影片.mp4"), extra=["-f", "mp4"])   # Outgo 裡的影片也不能變成教材
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
        cls.ctx.set_config("asr", {"engine": "sensevoice-small"})
        now = _dt.datetime.now(TZ)
        cls.ctx.set_config("schedule", {"start": (now - _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "end": (now + _dt.timedelta(hours=1)).strftime("%H:%M"),
                                        "auto_convert": True, "auto_whisper": True, "whisper_after_convert": True,
                                        "backup_time": "23:59", "backup_keep": 3})
        cls.super_id = cls.conn.val("INSERT INTO accounts(username, password_hash, role, full_name) VALUES ('su61', 'x', 'super', '超級管理員') RETURNING id")
        cls.ctx.enqueue("scan", {}, 50)
        cls.drain()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def drain(cls, types=None) -> list[tuple[str, str]]:
        done = []
        for _ in range(300):
            job = wmain.pick(cls.ctx, types or list(wmain.HANDLERS))
            if not job:
                break
            done.append((job["type"], wmain.run_job(cls.ctx, job)))
        return done

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    # ---------------- Outgo ----------------
    def test_01_scan_excludes_outgo_recursively(self):
        rows = self.conn.execute("SELECT rel_path FROM media UNION ALL SELECT rel_path FROM folders")
        self.assertTrue(rows, "測試教材有掃描進來")
        self.assertFalse([r for r in rows if r["rel_path"].lower().startswith("outgo")], "Outgo 與子資料夾都不能進教材資料表")
        run = self.conn.one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        self.assertIn("Outgo", json.loads(run["summary"]) ["system_dirs"] if isinstance(run["summary"], str) else run["summary"]["system_dirs"])
        # 大小寫不同也一樣是系統資料夾
        dirs, files, skipped = scan.walk(self.root, [])
        self.assertFalse([f for f in files if f.lower().startswith("outgo/")])

    def test_02_paths_cannot_reach_outgo(self):
        paths = self.ctx.paths
        for bad in ("Outgo/討論.m4a", "outgo/討論.m4a", "OUTGO/第一期/影片.mp4"):
            with self.assertRaises(ValueError):
                paths.original(bad)
        # 課程裡的符號連結指向 Outgo：解開後落在 Outgo，也要拒絕
        os.symlink(self.p("Outgo", "討論.m4a"), self.p("基礎班", "連到Outgo.m4a"))
        try:
            with self.assertRaises(ValueError):
                paths.original("基礎班/連到Outgo.m4a")
        finally:
            os.remove(self.p("基礎班", "連到Outgo.m4a"))
        # Outgo 內部的路徑檢查
        self.assertEqual(paths.outgo_file("第一期/第3堂/上課錄音.mp3"), os.path.join(os.path.realpath(self.p("Outgo")), "第一期", "第3堂", "上課錄音.mp3"))
        for bad in ("../基礎班/第一堂課/講義.pdf", "/etc/passwd", "第一期/../../x", ".hidden.mp3", "第一期/.x/a.mp3", "a\\b.mp3", ""):
            with self.assertRaises(ValueError, msg=bad):
                paths.outgo_file(bad)
        os.symlink(self.p("基礎班"), self.p("Outgo", "連結資料夾"))
        try:
            with self.assertRaises(ValueError):
                paths.outgo_file("連結資料夾/第一堂課/講義.pdf")
        finally:
            os.remove(self.p("Outgo", "連結資料夾"))

    def test_03_outgo_asr_job_writes_results_inside_outgo_only(self):
        before = {t: self.conn.val(f"SELECT count(*) FROM {t}") for t in ("media", "folders", "subtitles", "subtitle_cues")}
        jid = self.ctx.enqueue("outgo_asr", {"path": "第一期/第3堂/上課錄音.mp3"}, 30, self.super_id)
        self.assertEqual(self.drain(["outgo_asr"]), [("outgo_asr", "done")])
        audio_f = self.p("Outgo", "第一期", "第3堂", "上課錄音.mp3")
        txt, srt = audio_f + ".asr.txt", audio_f + ".asr.srt"
        self.assertTrue(os.path.isfile(audio_f), "原始錄音不動")
        with open(txt, encoding="utf-8") as f:
            body = f.read()
        # 繁體轉換用 OpenCC（正式映像有；開發機沒裝時字幕流程本來就保留原文）
        self.assertIn("練習氣功時" if asr_text.load_opencc()[0] else "练习气功时", body, "逐字稿（UTF-8）")
        with open(srt, encoding="utf-8") as f:
            s = f.read()
        self.assertTrue(s.startswith("1\n00:00:00,000 --> "), s[:40])
        st = outgo.read_state(audio_f)
        self.assertEqual((st["state"], st["engine"], st["requested_by"]), ("done", "sensevoice-small", self.super_id), "預設採資料庫目前的設定")
        self.assertIsNone(self.conn.val("SELECT id FROM jobs WHERE id = %s", [jid]), "短期工作：完成後刪除 jobs 這一筆")
        after = {t: self.conn.val(f"SELECT count(*) FROM {t}") for t in before}
        self.assertEqual(after, before, "不寫入任何教材資料表")
        lg = self.conn.one("SELECT * FROM asr_log WHERE job_id = %s", [jid])
        self.assertEqual((lg["kind"], lg["path"], lg["status"], lg["media_id"], lg["engine"]),
                         ("outgo", "Outgo/第一期/第3堂/上課錄音.mp3", "done", None, "sensevoice-small"), "ASR 處理記錄：Outgo 也要記")
        self.assertIn(lg["quality"], ("high", "medium", "low"))
        self.assertTrue(lg["audio_sec"] and abs(lg["audio_sec"] - 2.0) < 0.2 and lg["file_size"] > 0)
        self.assertEqual(st["quality"], lg["quality"], "狀態檔與處理記錄的品質一致")
        self.assertTrue(self.conn.val("SELECT count(*) FROM system_logs WHERE message LIKE 'ASR 完成（Outgo）：Outgo/第一期/第3堂/上課錄音.mp3%%'"))

    def test_04_outgo_failure_keeps_audio_and_previous_result(self):
        audio_f = self.p("Outgo", "討論.m4a")
        self.ctx.enqueue("outgo_asr", {"path": "討論.m4a", "engine": "paraformer-zh"}, 30)
        self.assertEqual(self.drain(["outgo_asr"]), [("outgo_asr", "done")])
        with open(audio_f + ".asr.txt", encoding="utf-8") as f:
            first = f.read()
        # 重新辨識失敗（指定已移除的引擎）：舊結果與錄音都保留，狀態寫「失敗」與原因
        self.ctx.enqueue("outgo_asr", {"path": "討論.m4a", "engine": "whisper"}, 30)
        self.assertEqual(self.drain(["outgo_asr"]), [("outgo_asr", "failed")])
        st = outgo.read_state(audio_f)
        self.assertEqual(st["state"], "failed")
        self.assertIn("dev.59 移除", st["error"])
        self.assertEqual(st["last_done"]["engine"], "paraformer-zh", "保留上次成功的資訊")
        with open(audio_f + ".asr.txt", encoding="utf-8") as f:
            self.assertEqual(f.read(), first, "失敗時不換掉舊結果")
        self.assertTrue(os.path.isfile(audio_f))
        self.assertFalse(glob.glob(self.p("Outgo", ".*.new")), "暫存檔已清除")
        self.assertFalse(self.conn.val("SELECT count(*) FROM jobs WHERE type = 'outgo_asr'"), "失敗的工作也不留在 jobs")
        lg = self.conn.one("SELECT * FROM asr_log WHERE path = 'Outgo/討論.m4a' ORDER BY id DESC LIMIT 1")
        self.assertEqual((lg["status"], lg["engine_requested"], lg["quality"]), ("failed", "whisper", "unknown"), "失敗也寫 ASR 處理記錄")
        self.assertIn("dev.59 移除", lg["error"])

    def test_05_outgo_rejects_non_audio_and_escapes(self):
        for payload, why in (({"path": "講義.pdf"}, "不是支援的錄音格式"), ({"path": "../基礎班/第一堂課/0001_上集.mp4"}, "格式錯誤"),
                             ({"path": "沒有這個.mp3"}, "找不到")):
            self.ctx.enqueue("outgo_asr", payload, 30)
            job = wmain.pick(self.ctx, ["outgo_asr"])
            self.assertEqual(wmain.run_job(self.ctx, job), "failed")
            msg = self.conn.val("SELECT message FROM system_logs WHERE context->>'job_id' = %s", [str(job["id"])])
            self.assertIn(why, msg or "", payload)

    def test_06_audio_formats_decode_with_ffmpeg(self):
        """真的 ffmpeg：各種錄音格式都能轉成 ASR 用的 16 kHz 單聲道 WAV（與 FunASR 用的是同一段程式）。"""
        d = tempfile.mkdtemp()
        try:
            for ext, extra in (("mp3", []), ("wav", []), ("m4a", ["-c:a", "aac"]), ("aac", ["-c:a", "aac"]), ("flac", []),
                               ("ogg", ["-c:a", "libvorbis"]), ("opus", ["-c:a", "libopus"]), ("wma", ["-c:a", "wmav2"])):
                src = os.path.join(d, f"a.{ext}")
                tone(src, 3.0, extra)
                self.assertTrue(outgo.is_audio(src))
                dur = audio.extract_wav(src, os.path.join(d, f"{ext}.wav"))
                self.assertAlmostEqual(dur, 3.0, delta=0.15, msg=ext)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertFalse(outgo.is_audio("x.mp4"), "影片不在 Outgo 的錄音清單")

    # ---------------- 永久刪除教材 ----------------
    def make_victim(self, name: str, convert: bool = True) -> dict:
        """建立一支只給這個測試用的影片：掃描、轉檔、產生字幕；回傳教材紀錄。"""
        rel = f"刪除測試/{name}"
        os.makedirs(self.p("刪除測試"), exist_ok=True)
        make_media.mts_interlaced_ac3(self.p(*rel.split("/")), 2, 300)
        self.ctx.enqueue("scan", {}, 50)
        self.drain()
        m = self.conn.one("SELECT * FROM media WHERE rel_path = %s", [rel])
        self.assertIsNotNone(m)
        if convert:
            self.assertEqual(m["convert_status"], "done", m)
            self.assertTrue(self.conn.val("SELECT 1 FROM subtitles WHERE media_id = %s", [m["id"]]))
        self.conn.execute("INSERT INTO bookmarks(account_id, media_id) VALUES (%s, %s)", [self.super_id, m["id"]])
        self.conn.execute("INSERT INTO view_history(account_id, media_id) VALUES (%s, %s)", [self.super_id, m["id"]])
        self.ctx.audit("media.updated", {"x": 1}, m["id"], rel, actor_id=self.super_id)
        return m

    def purge(self, m: dict) -> tuple[str, dict]:
        jid = self.ctx.enqueue("purge_media", {"media_id": m["id"], "rel_path": m["rel_path"]}, 90, self.super_id)
        job = self.conn.one("UPDATE jobs SET status = 'running', started_at = NOW() WHERE id = %s RETURNING *", [jid])
        st = wmain.run_job(self.ctx, job)
        return st, self.conn.one("SELECT * FROM jobs WHERE id = %s", [jid])

    def test_10_purge_deletes_files_and_rows_and_does_not_resurrect(self):
        m = self.make_victim("V1.MTS")
        mid = m["id"]
        files = [self.p("刪除測試", "V1.MTS"), self.ctx.paths.playback_file(mid), self.ctx.paths.subtitle_file(mid)]
        self.assertTrue(all(os.path.isfile(f) for f in files))
        lg = self.conn.one("SELECT * FROM asr_log WHERE media_id = %s", [mid])
        self.assertEqual((lg["kind"], lg["path"], lg["status"]), ("subtitle", "刪除測試/V1.MTS", "done"), "字幕辨識寫 ASR 處理記錄")
        rep = self.conn.val("SELECT asr_report FROM subtitles WHERE media_id = %s", [mid])
        rep = rep if isinstance(rep, dict) else json.loads(rep)
        self.assertEqual(rep["audio_quality"]["level"], lg["quality"], "字幕報告裡也有音訊品質")
        others = {r["id"] for r in self.conn.execute("SELECT id FROM media WHERE id <> %s", [mid])}
        mine = {f"{mid}.mp4", f"{mid}.vtt"}
        other_files = sorted(f for f in glob.glob(self.p("playback", "*.mp4")) + glob.glob(self.p("subtitles", "*.vtt"))
                             if os.path.basename(f) not in mine)
        st, job = self.purge(m)
        self.assertEqual(st, "done", job["error"])
        res = job["result"] if isinstance(job["result"], dict) else json.loads(job["result"])
        self.assertEqual(res["deleted"], ["原檔", "播放用 MP4", "字幕檔"])
        self.assertEqual(res["left"], [])
        self.assertFalse(any(os.path.lexists(f) for f in files), "原檔與衍生檔都刪除")
        self.assertFalse(glob.glob(self.p("**", ".istudy-purge-*"), recursive=True), "沒有留下暫存檔")
        self.assertTrue(os.path.isdir(self.p("刪除測試")), "不刪資料夾")
        for t in ("media WHERE id", "subtitles WHERE media_id", "subtitle_cues WHERE media_id", "bookmarks WHERE media_id",
                  "view_history WHERE media_id", "audit_log WHERE media_id"):
            self.assertEqual(self.conn.val(f"SELECT count(*) FROM {t} = %s", [mid]), 0, t)
        self.assertEqual(self.conn.val("SELECT count(*) FROM jobs WHERE id <> %s AND payload->>'media_id' = %s", [job["id"], str(mid)]), 0,
                         "這個教材的工作紀錄也刪除")
        self.assertEqual(self.conn.val("SELECT count(*) FROM asr_log WHERE media_id = %s", [mid]), 0, "ASR 處理記錄不再連到已刪除的教材")
        self.assertTrue(self.conn.val("SELECT count(*) FROM asr_log WHERE path = '刪除測試/V1.MTS' AND kind = 'subtitle' AND status = 'done'"),
                        "ASR 處理記錄保留（路徑、品質、結果）")
        a = self.conn.one("SELECT * FROM audit_log WHERE action = 'media.purged' ORDER BY id DESC LIMIT 1")
        self.assertEqual((a["media_id"], a["path"], a["actor_id"]), (None, "刪除測試/V1.MTS", self.super_id), "稽核保留，但沒有失效的教材關聯")
        self.assertEqual({r["id"] for r in self.conn.execute("SELECT id FROM media WHERE id <> %s", [mid])}, others, "其他教材不動")
        self.assertEqual(sorted(glob.glob(self.p("playback", "*.mp4")) + glob.glob(self.p("subtitles", "*.vtt"))), other_files,
                         "其他教材的衍生檔不動")
        self.ctx.enqueue("scan", {}, 50)
        self.drain(["scan"])
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE rel_path = '刪除測試/V1.MTS'"), "重新掃描不會復活")

    def test_11_purge_failure_changes_nothing(self):
        m = self.make_victim("V2.MTS")
        mid = m["id"]
        files = [self.p("刪除測試", "V2.MTS"), self.ctx.paths.playback_file(mid), self.ctx.paths.subtitle_file(mid)]
        # 1) 第 2 個檔案改名失敗（例如權限）：已改名的改回來，什麼都沒刪
        real = os.rename
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] == 2:
                raise PermissionError(13, "Permission denied")
            real(a, b)
        purge.os.rename = flaky
        try:
            st, job = self.purge(m)
        finally:
            purge.os.rename = real
        self.assertEqual(st, "failed")
        self.assertIn("沒有刪除任何東西", job["error"])
        self.assertTrue(all(os.path.isfile(f) for f in files))
        self.assertTrue(self.conn.val("SELECT 1 FROM media WHERE id = %s", [mid]))
        # 2) 資料庫失敗：檔案全部改回原名，教材保持原狀
        real_db = purge._db_purge

        def boom(*a, **k):
            raise RuntimeError("模擬資料庫中斷")
        purge._db_purge = boom
        try:
            st, job = self.purge(m)
        finally:
            purge._db_purge = real_db
        self.assertEqual(st, "failed")
        self.assertIn("已確認所有檔案都在原位置", job["error"])
        self.assertTrue(all(os.path.isfile(f) for f in files))
        self.assertEqual(self.conn.val("SELECT count(*) FROM subtitle_cues WHERE media_id = %s", [mid]) > 0, True)
        self.assertFalse(glob.glob(self.p("**", ".istudy-purge-*"), recursive=True))
        # 3) Agent 在改名之後、刪資料庫之前中斷：同一個工作重新執行會接著完成
        items, _ = purge.plan(self.ctx, self.conn.one("SELECT * FROM media WHERE id = %s", [mid]))
        jid = self.ctx.enqueue("purge_media", {"media_id": mid, "rel_path": m["rel_path"]}, 90)
        self.ctx.progress(jid, {"plan": items})
        os.rename(items[0]["path"], items[0]["staged"])          # 只改了第 1 個就中斷
        job = self.conn.one("UPDATE jobs SET status = 'running' WHERE id = %s RETURNING *", [jid])
        self.assertEqual(wmain.run_job(self.ctx, job), "done")
        self.assertFalse(any(os.path.lexists(f) for f in files))
        self.assertIsNone(self.conn.val("SELECT id FROM media WHERE id = %s", [mid]))

    def test_12_purge_refuses_symlink_running_job_and_changed_path(self):
        m = self.make_victim("V3.MTS", convert=False)
        mid = m["id"]
        # 執行中的工作（轉檔或字幕）：拒絕，什麼都不動
        busy = self.ctx.enqueue("asr", {"media_id": mid}, 0)
        self.conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", [busy])
        st, job = self.purge(m)
        self.assertEqual(st, "failed")
        self.assertIn("正在執行", job["error"])
        self.conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", [busy])
        # 路徑已改變（例如被移動）：拒絕
        self.conn.execute("UPDATE media SET rel_path = '刪除測試/改名.MTS' WHERE id = %s", [mid])
        st, job = self.purge(m)
        self.assertIn("路徑已改變", job["error"])
        self.conn.execute("UPDATE media SET rel_path = %s WHERE id = %s", [m["rel_path"], mid])
        # 原檔是符號連結：拒絕（不刪連結也不刪目標）
        target = self.p("刪除測試", "真正的檔案.bin")
        os.rename(self.p("刪除測試", "V3.MTS"), target)
        os.symlink(target, self.p("刪除測試", "V3.MTS"))
        st, job = self.purge(m)
        self.assertEqual(st, "failed")
        self.assertIn("符號連結", job["error"])
        self.assertTrue(os.path.islink(self.p("刪除測試", "V3.MTS")) and os.path.isfile(target))
        self.assertTrue(self.conn.val("SELECT 1 FROM media WHERE id = %s", [mid]))
        # 衍生檔名稱被其他教材使用：保留
        os.remove(self.p("刪除測試", "V3.MTS"))
        os.rename(target, self.p("刪除測試", "V3.MTS"))
        other = self.conn.val("SELECT id FROM media WHERE id <> %s AND kind = 'video' AND playback_path IS NOT NULL ORDER BY id LIMIT 1", [mid])
        with open(self.ctx.paths.playback_file(mid), "wb") as f:
            f.write(b"x")
        self.conn.execute("UPDATE media SET playback_path = %s WHERE id = %s", [f"{mid}.mp4", other])
        try:
            st, job = self.purge(m)
            self.assertEqual(st, "done", job["error"])
            res = job["result"] if isinstance(job["result"], dict) else json.loads(job["result"])
            self.assertIn("播放用 MP4 也被其他教材使用：保留", res["notes"])
            self.assertTrue(os.path.isfile(self.ctx.paths.playback_file(mid)))
        finally:
            self.conn.execute("UPDATE media SET playback_path = %s WHERE id = %s", [f"{other}.mp4", other])
            os.remove(self.ctx.paths.playback_file(mid))

    def test_13_purge_refuses_paths_outside_library(self):
        m = self.make_victim("V4.MTS", convert=False)
        self.conn.execute("UPDATE media SET rel_path = 'Outgo/討論.m4a' WHERE id = %s", [m["id"]])
        st, job = self.purge({**m, "rel_path": "Outgo/討論.m4a"})
        self.assertEqual(st, "failed")
        self.assertIn("不安全", job["error"])
        self.assertTrue(os.path.isfile(self.p("Outgo", "討論.m4a")), "Outgo 的檔案不會被刪")

    def test_14_staged_leftovers_cleaned_on_start(self):
        f = os.path.join(self.ctx.paths.playback, ".istudy-purge-999-0")
        with open(f, "w") as fh:
            fh.write("x")
        self.assertEqual(purge.cleanup_staged(self.ctx), 1)
        self.assertFalse(os.path.exists(f))


if __name__ == "__main__":
    unittest.main(verbosity=2)
