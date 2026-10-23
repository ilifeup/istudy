"""字幕辨識（ASR，dev.57）測試。

1. 純邏輯（一定執行）：引擎清單與可用性、VAD 留白與合併、斷句與時間分配、繁體化／保護詞／修正表、
   重複與迴圈、品質檢查、CER、假引擎走完整條管線。
2. 真實模型（有 sherpa-onnx 與模型時才執行）：設定 ISTUDY_ASR_TEST_MODELS（含 asr/… 的資料夾）與
   ISTUDY_ASR_TEST_MEDIA（一段中文語音的影片或音檔），用 Paraformer-zh 與 SenseVoiceSmall 實際辨識。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from worker import asr  # noqa: E402
from worker.asr import audio, engines, text as T  # noqa: E402

S2TW, T2S = T.load_opencc()


def fake_s2tw(s: str) -> str:
    table = str.maketrans({"这": "這", "个": "個", "们": "們", "说": "說", "后": "後", "气": "氣", "学": "學", "课": "課",
                           "练": "練", "习": "習", "时": "時", "让": "讓", "体": "體", "觉": "覺", "复": "複", "无": "無", "相": "相"})
    return s.translate(table).replace("软件", "軟體")


def fake_t2s(s: str) -> str:
    return s.translate(str.maketrans({"這": "这", "個": "个", "們": "们", "說": "说", "後": "后", "氣": "气", "學": "学",
                                      "無": "无", "複": "复", "體": "体"}))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="asr-models-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def touch(self, *rel):
        for r in rel:
            p = os.path.join(self.d, r)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()

    def test_local_names_and_online_reserved(self):
        local = [e["id"] for e in engines.ENGINES if e["route"] == "local"]
        self.assertEqual(local, ["paraformer-zh", "sensevoice-small", "qwen3-asr-0.6b", "breeze-asr-25"])   # dev.59 移除 whisper
        self.assertEqual(engines.AUTO_ORDER, ("paraformer-zh", "sensevoice-small"))
        st = engines.status_all(self.d)
        self.assertEqual(st["engines"]["qwen3-asr-0.6b"], {"ok": False, "reason": "尚未提供"})
        self.assertEqual(st["engines"]["breeze-asr-25"]["reason"], "尚未提供")
        self.assertEqual(st["engines"]["online"], {"ok": False, "reason": "尚未設定"})
        with self.assertRaises(engines.EngineUnavailable):
            engines.create("online", self.d, 1)
        with self.assertRaises(engines.EngineUnavailable):
            engines.create("qwen3-asr-0.6b", self.d, 1)

    def test_removed_whisper_is_an_explicit_error(self):
        """dev.59：舊設定或佇列中的舊工作指定 whisper → 明確說明已移除，不默默改用別的引擎。"""
        self.touch("faster-whisper-small/model.bin")            # 舊模型目錄還在也一樣：不再讀取
        self.assertNotIn("whisper", engines.status_all(self.d)["engines"])
        ok, why = engines.availability("whisper", self.d)
        self.assertFalse(ok)
        self.assertIn("dev.59 移除", why)
        for call in (lambda: engines.resolve("whisper", self.d), lambda: engines.create("whisper", self.d, 1)):
            with self.assertRaises(engines.EngineUnavailable) as e:
                call()
            self.assertIn("「自動」", str(e.exception))
        with self.assertRaises(asr.EngineUnavailable) as e:
            asr.transcribe("/x.mp4", "whisper", "/x", {}, 10.0, lambda p: None, {"ISTUDY_ASR_ENGINE": "fake"})
        self.assertIn("dev.59 移除", str(e.exception))

    def test_agent_code_has_no_whisper_runtime(self):
        """靜態檢查：Agent 程式碼不再 import faster_whisper／WhisperModel；requirements 也不再安裝。"""
        agent = os.path.dirname(HERE)
        hits = []
        for base, _dirs, files in os.walk(agent):
            if "tests" in base.split(os.sep) or "__pycache__" in base:
                continue
            for f in files:
                if f.endswith(".py") or f == "requirements.txt":
                    with open(os.path.join(base, f), encoding="utf-8") as fh:
                        for n, line in enumerate(fh, 1):
                            code = line.split("#", 1)[0]
                            if "faster_whisper" in code or "WhisperModel" in code or (f == "requirements.txt" and "whisper" in code.lower()):
                                hits.append(f"{os.path.relpath(os.path.join(base, f), agent)}:{n}")
        self.assertEqual(hits, [])

    def test_missing_models_are_unavailable_not_silently_replaced(self):
        if not engines._has_module("sherpa_onnx"):
            ok, why = engines.availability("paraformer-zh", self.d)
            self.assertFalse(ok)
            self.assertIn("sherpa-onnx", why)
            return
        ok, why = engines.availability("paraformer-zh", self.d)
        self.assertFalse(ok)
        self.assertIn("asr-models --fetch paraformer-zh", why)
        self.touch("asr/paraformer-zh/model.int8.onnx", "asr/paraformer-zh/tokens.txt", engines.PUNCT_FILE)
        self.assertFalse(engines.availability("paraformer-zh", self.d)[0], "缺 VAD 也不可用")
        self.touch(engines.VAD_FILE)
        self.assertEqual(engines.availability("paraformer-zh", self.d), (True, ""))
        self.assertEqual(engines.resolve("auto", self.d), "paraformer-zh")
        with self.assertRaises(engines.EngineUnavailable) as e:
            engines.resolve("sensevoice-small", self.d)        # 指定的引擎不可用：報錯，不改用別的
        self.assertIn("SenseVoiceSmall", str(e.exception))

    def test_auto_with_nothing_installed(self):
        with self.assertRaises(engines.EngineUnavailable) as e:
            engines.resolve("auto", self.d)
        self.assertIn("asr-models --fetch all", str(e.exception))


class TestVadMerge(unittest.TestCase):
    def test_pad_merge_clamp(self):
        segs = audio.pad_and_merge([(1000, 2000), (2300, 3000), (9000, 9500), (59900, 60500)], 60000)
        # 留白 0.2 秒；1000-2000 與 2300-3000 間隔 < 0.3 秒 → 合併；最後一段不超過影片長度
        self.assertEqual(segs, [(800, 3200), (8800, 9700), (59700, 60000)])

    def test_no_overlap_when_merge_too_long(self):
        segs = audio.pad_and_merge([(0, 19900), (20000, 30000)], 40000, max_len=20000)
        self.assertEqual(len(segs), 2)
        self.assertLessEqual(segs[0][1], segs[1][0])


class TestSegmentation(unittest.TestCase):
    st = T.Settings()

    def test_token_timestamps_used(self):
        tokens = list("大家好今天我们复习第一堂课")
        times = [i * 0.3 for i in range(len(tokens))]
        cues = T.segment_cues("大家好，今天我们复习第一堂课。", 10000, 14500, self.st, tokens, times)
        self.assertEqual([c.text for c in cues], ["大家好，今天我们复习第一堂课"])
        self.assertTrue(cues[0].precise)
        self.assertEqual(cues[0].start, 10000)

    def test_split_by_length_at_punctuation(self):
        t = "第一，我们先放松全身的肌肉，第二，注意呼吸保持自然稳定的状态，然后慢慢地把注意力放到丹田的位置"
        cues = T.segment_cues(t, 0, 20000, self.st)
        self.assertGreater(len(cues), 2)
        self.assertTrue(all(sum(T.unit_weight(u) for u in T.units(c.text)) <= self.st.max_chars for c in cues), [c.text for c in cues])
        self.assertFalse(any(c.text.endswith("，") for c in cues))
        self.assertFalse(cues[0].precise)          # 沒有逐字時間 → 依字數比例
        self.assertEqual(cues[0].start, 0)
        self.assertTrue(all(cues[i].start < cues[i + 1].start for i in range(len(cues) - 1)))

    def test_no_tiny_tail(self):
        cues = T.segment_cues(T.normalize_spaces("開飯時間早上9點至下午5點"), 0, 8000, self.st)
        self.assertEqual([c.text for c in cues], ["開飯時間早上9點至下午5點"])

    def test_english_spacing_and_period(self):
        t = T.normalize_spaces("today is tuesday.明天是星期三。yesterday was星期一")
        cues = T.segment_cues(t, 0, 8000, self.st)
        self.assertEqual([c.text for c in cues], ["today is tuesday", "明天是星期三", "yesterday was 星期一"])

    def test_misaligned_tokens_fall_back(self):
        cues = T.segment_cues("早上9点", 0, 2000, self.st, ["早", "上", "九", "点"], [0.0, 0.2, 0.4, 0.6])
        self.assertFalse(cues[0].precise)          # ITN 改了字數：對不上就改用比例估算


class TestTextAndQc(unittest.TestCase):
    def conv(self, fixes=(), protect=()):
        st = T.Settings(fixes=list(fixes), protect=list(protect))
        return T.TextConverter(fake_s2tw, fake_t2s, st)

    def test_tags_loops_punct(self):
        self.assertEqual(T.strip_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>大家好"), "大家好")
        t, n = T.collapse_loops("谢谢谢谢谢谢谢谢谢谢大家")
        self.assertEqual((t, n), ("谢谢大家", 1))                      # 失控的重複只留兩次
        self.assertEqual(T.collapse_loops("哈哈哈大家好"), ("哈哈哈大家好", 0))   # 重複 3 次以內不動
        self.assertEqual(T.full_width_punct("好,我们?是!3.5公分"), "好，我们？是！3.5公分")

    def test_fixes_after_opencc_and_protect(self):
        c = self.conv(fixes=[("圓三", "研三")])
        self.assertEqual(c.convert("这个软件 圓三"), "這個軟體 研三")
        # 保護詞：轉回簡體再轉繁體會變成別的字時，還原成保護詞
        c = self.conv(protect=["后天氣功"])
        self.assertEqual(c.restore, [("後天氣功", "后天氣功")])
        self.assertEqual(c.convert("学后天气功"), "學后天氣功")

    def test_real_opencc_if_available(self):
        if not S2TW:
            self.skipTest("沒有 OpenCC（正式 Agent 映像有）")
        c = T.TextConverter(S2TW, T2S, T.Settings())
        self.assertEqual(c.convert("这个视频里的软件和内存"), "這個影片裡的軟體和記憶體")

    def test_finalize_repeats_overlap_min(self):
        st = T.Settings()
        cues = [T.Cue(0, 2000, "好"), T.Cue(1500, 1800, "注意"), T.Cue(3000, 4000, "放松"), T.Cue(4000, 5000, "放松"),
                T.Cue(5000, 6000, "放松"), T.Cue(6000, 7000, "放松"), T.Cue(9000, 9100, "结束")]
        out, stats = T.finalize(cues, 9500, st)
        self.assertEqual([c.text for c in out], ["好", "注意", "放松", "放松", "结束"])
        self.assertEqual((stats["repeat_runs"], stats["repeat_removed"]), (1, 2))
        self.assertEqual(out[0].end, 1500)               # 重疊修正
        self.assertEqual(out[1].end, 2500)               # 太短 → 延到 1 秒
        self.assertEqual(out[-1].end, 9500)              # 不超過影片長度
        qc = T.quality(out, 7, 0, 0, stats, 6000, 9500, True)
        self.assertEqual(qc["status"], "needs_review")
        self.assertIn("連續重複", qc["reasons"][0])

    def test_quality_ok_and_empty_ratio(self):
        cues = [T.Cue(0, 1000, "好", True)]
        self.assertEqual(T.quality(cues, 10, 0, 0, {}, 5000, 10000, True)["status"], "ok")
        qc = T.quality(cues, 10, 4, 0, {}, 5000, 10000, True)
        self.assertIn("辨識不出文字", qc["reasons"][0])
        qc = T.quality(cues, 1, 0, 0, {}, 5000, 10000, False)
        self.assertIn("OpenCC", qc["reasons"][0])

    def test_cer(self):
        self.assertEqual(T.cer("大家好，今天", "大家好今天")["cer"], 0.0)
        r = T.cer("大家好今天", "大家今天天")
        self.assertEqual((r["edits"], r["ref_chars"]), (2, 5))
        self.assertEqual(T.cer("這個說", "这个说", fake_t2s)["cer"], 0.0)   # 先統一字形再比（規劃書 P-18）
        self.assertEqual(T.cer("這個說", "这个说")["cer"], 1.0)             # 不統一字形，每個字都算錯

    def test_settings_from_config(self):
        st, other = asr.settings_from_config({"engine": "sensevoice-small", "max_chars": 16, "max_sec": 99,
                                              "glossary": {"fixes": [["a", "b"], ["bad"]], "protect": ["無相"], "hotwords": ["氣功", ""]}})
        self.assertEqual((st.max_chars, st.max_ms), (16, 7000))      # 超出範圍 → 預設
        self.assertEqual((st.fixes, st.protect, other["hotwords"], other["engine"]), ([("a", "b")], ["無相"], ["氣功"], "sensevoice-small"))


class TestFakePipeline(unittest.TestCase):
    def test_fake_engine_full_pipeline(self):
        cues, rep = asr.transcribe("/nonexistent.mp4", "auto", "/nonexistent", {}, 25.0, lambda p: None,
                                   {"ISTUDY_ASR_ENGINE": "fake"}, opencc_pair=(fake_s2tw, fake_t2s))
        self.assertEqual(rep["engine"], "paraformer-zh")
        self.assertEqual(rep["engine_version"], "fake")
        self.assertEqual(rep["qc"]["status"], "ok", rep["qc"])
        self.assertTrue(all(s < e for s, e, _ in cues))
        self.assertIn("練習氣功時", "".join(t for _, _, t in cues))
        self.assertEqual(rep["metrics"]["segments"], 3)

    def test_fake_rejects_reserved(self):
        with self.assertRaises(asr.EngineUnavailable):
            asr.transcribe("/x.mp4", "breeze-asr-25", "/x", {}, 10.0, lambda p: None, {"ISTUDY_ASR_ENGINE": "fake"})


class TestDiagnostics(unittest.TestCase):
    """dev.58：辨識報告的字數與結尾資訊（V06 結尾缺字調查用）。"""

    def test_lost_chars_and_tail_note(self):
        cues = [T.Cue(0, 1000, "你好", True)]
        qc = T.quality(cues, 1, 0, 0, {}, 1000, 2000, True,
                          {"chars": {"engine": 5, "cues": 2}, "tail": {"speech_end_ms": 1900}})
        self.assertEqual(qc["status"], "needs_review")
        self.assertIn("少了 3 個字", qc["reasons"][0])
        self.assertTrue(any("仍在講話" for n in qc["notes"]))
        ok = T.quality(cues, 1, 0, 0, {}, 1000, 5000, True, {"chars": {"engine": 2, "cues": 2}, "tail": {"speech_end_ms": 3000}})
        self.assertEqual((ok["status"], ok["notes"]), ("ok", []))

    def test_report_has_diag_in_fake_mode(self):
        cues, rep = asr.transcribe("/x.mp4", "paraformer-zh", "/x", {}, 30.0, lambda p: None, {"ISTUDY_ASR_ENGINE": "fake"})
        d = rep["diag"]
        self.assertEqual(d["chars"]["engine"], d["chars"]["cues"])
        self.assertEqual(d["tail"]["audio_ms"], 30000)
        self.assertEqual(d["tail"]["last_cue_ms"][1], cues[-1][1])


MODELS = os.environ.get("ISTUDY_ASR_TEST_MODELS")
MEDIA = os.environ.get("ISTUDY_ASR_TEST_MEDIA")


@unittest.skipUnless(MODELS and MEDIA and engines._has_module("sherpa_onnx"), "需要 sherpa-onnx、模型與測試音檔")
class TestRealModels(unittest.TestCase):
    """真實辨識：FunASR Paraformer-zh 與 SenseVoiceSmall（sherpa-onnx int8）。"""

    def run_engine(self, eid):
        cues, rep = asr.transcribe(MEDIA, eid, MODELS, {}, None, lambda p: None)
        print(f"\n[{eid}] {rep['metrics']} qc={rep['qc']['status']} precise={rep['qc']['precise_ratio']}")
        for s, e, t in cues[:6]:
            print(f"   {s / 1000:7.2f}–{e / 1000:7.2f}  {t}")
        return cues, rep

    def check(self, cues, rep):
        self.assertTrue(cues)
        self.assertEqual(rep["qc"]["status"], "ok", rep["qc"])
        self.assertTrue(all(0 <= s < e for s, e, _ in cues))
        self.assertTrue(all(cues[i][0] <= cues[i + 1][0] for i in range(len(cues) - 1)))
        self.assertTrue(all(e <= rep["metrics"]["audio_sec"] * 1000 for _, e, _ in cues))
        joined = "".join(t for _, _, t in cues)
        if S2TW:
            self.assertFalse(set(joined) & set("这个们说对时间问题"), "應已轉成繁體")
        self.assertLess(rep["metrics"]["rtf"], 1.0)
        self.assertGreater(rep["qc"]["precise_ratio"], 0.8, "應有逐字時間戳")

    def test_paraformer(self):
        self.check(*self.run_engine("paraformer-zh"))

    def test_sensevoice(self):
        self.check(*self.run_engine("sensevoice-small"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
