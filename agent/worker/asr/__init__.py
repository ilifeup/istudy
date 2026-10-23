"""字幕辨識（ASR，dev.57）：可替換引擎＋共用字幕管線。

管線（規劃書第 8、10 章）：
  影片 → FFmpeg 16 kHz 單聲道 WAV → Silero VAD 切段（前後留白、合併短間隔）→ 引擎逐段辨識
  → 去標籤、文字迴圈縮短 → 依逐字時間戳（沒有就依字數比例）二次斷句
  → 每條字幕：OpenCC s2twp、保護詞還原、修正表、全形標點 → 排序、去除連續重複、修正重疊 → 品質檢查
所有引擎（含現有的 Whisper）都走同一條管線，結果才能公平比較。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Callable, Optional

from . import audio, engines, quality, text
from .engines import EngineUnavailable

BATCH = 8   # 一次送進模型的段落數


def settings_from_config(cfg: dict) -> tuple[text.Settings, dict]:
    """server_config 的 asr 設定 → (斷句與詞彙設定, 其他設定)。缺少的值用預設。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    fixes, protect, hot = text.parse_glossary(cfg.get("glossary") or {})

    def num(k, lo, hi, default):
        v = cfg.get(k)
        return int(v) if isinstance(v, (int, float)) and lo <= v <= hi else default
    st = text.Settings(max_chars=num("max_chars", 10, 40, 20), max_ms=num("max_sec", 3, 10, 7) * 1000,
                       fixes=fixes, protect=protect)
    # 熱詞（glossary.hotwords）只有 dev.58 以前的 Whisper 使用；dev.59 起沒有引擎使用，資料保留在設定裡不刪除
    return st, {"engine": str(cfg.get("engine") or "auto"), "threads": num("threads", 0, 32, 0), "hotwords": hot}


def default_threads() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


def _fake_segments(duration_ms: int) -> list[tuple[int, int]]:
    d = max(3000, duration_ms)
    return [(0, min(d, 10000)), (min(d, 10000), min(d, 20000)), (min(d, 20000), d)]


FAKE_TEXT = ["大家好，今天复习第一堂课的内容", "练习气功时，先让身体自然放松", "注意身体感受，保持自然稳定"]


def transcribe(src: str, engine_choice: str, models_dir: str, cfg: dict, duration_sec: Optional[float],
               on_progress: Callable[[float], None], env: Optional[dict] = None, opencc_pair=None) -> tuple[list[tuple[int, int, str]], dict]:
    """影片檔 → (字幕 [(start_ms, end_ms, text)], 報告)。報告含引擎、版本、速度、品質檢查。"""
    env = env if env is not None else dict(os.environ)
    st, other = settings_from_config(cfg)
    threads = other["threads"] or int(env.get("ISTUDY_ASR_THREADS", "0") or 0) or default_threads()
    fake = env.get("ISTUDY_ASR_ENGINE") == "fake"
    t0 = time.time()
    s2tw, t2s = opencc_pair if opencc_pair is not None else text.load_opencc()
    conv = text.TextConverter(s2tw, t2s, st)
    tmp = tempfile.mkdtemp(prefix="istudy-asr-")
    partial: dict = {}      # dev.61：失敗時也要寫進 ASR 處理記錄（長度、引擎、訊號品質）
    try:
        return _transcribe(src, engine_choice, models_dir, st, threads, fake, conv, duration_sec, on_progress, tmp, t0, partial)
    except Exception as e:  # noqa: BLE001
        try:
            e.asr_partial = partial          # type: ignore[attr-defined]
        except AttributeError:
            pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _signal(wav: str, segs: list[tuple[int, int]]) -> dict:
    """訊號品質指標；量測失敗不影響辨識（記下原因）。"""
    try:
        return quality.signal_metrics(wav, segs)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def _transcribe(src, engine_choice, models_dir, st, threads, fake, conv, duration_sec, on_progress, tmp, t0, partial):
    """transcribe() 的本體（暫存資料夾與失敗時的部分資訊由 transcribe() 處理）。"""
    sig = None
    if fake:
        eid, version = (engine_choice if engine_choice not in ("", "auto") else "paraformer-zh"), "fake"
        if eid in engines.REMOVED:
            raise EngineUnavailable(engines.REMOVED[eid])
        if engines.BY_ID.get(eid, {}).get("stage") != "ready":
            raise EngineUnavailable(f"{engines.BY_ID.get(eid, {}).get('name', eid)}：尚未提供")
        partial["engine"] = eid
        dur_ms = int((duration_sec or 30) * 1000)
        if os.path.isfile(src):       # 測試：有檔案時照樣量測訊號品質（真的 FFmpeg）
            try:
                wav = os.path.join(tmp, "audio.wav")
                dur_ms = int(audio.extract_wav(src, wav) * 1000) or dur_ms
                sig = _signal(wav, _fake_segments(dur_ms))
            except audio.AudioError as e:
                sig = {"error": str(e)[:200]}
        partial["audio_sec"] = round(dur_ms / 1000, 1)
        segs = _fake_segments(dur_ms)
        raws = [{"text": FAKE_TEXT[i % len(FAKE_TEXT)], "tokens": [], "timestamps": []} for i in range(len(segs))]
        raw_segs = segs[:-1] + [(segs[-1][0], max(segs[-1][0] + 1, segs[-1][1] - 1000))]   # 假資料：結尾留 1 秒靜音
        on_progress(100.0)
    else:
        eid = engines.resolve(engine_choice, models_dir)
        partial["engine"] = eid
        wav = os.path.join(tmp, "audio.wav")
        on_progress(1.0)
        dur_ms = int(audio.extract_wav(src, wav) * 1000)
        partial["audio_sec"] = round(dur_ms / 1000, 1)
        vad_model = os.path.join(models_dir, engines.VAD_FILE)
        if not os.path.isfile(vad_model):
            raise EngineUnavailable("缺少 VAD 模型（sudo istudyctl asr-models --fetch all）")
        raw_segs = audio.detect_speech(wav, vad_model, 1, lambda f: on_progress(round(2 + f * 8, 1)))
        sig = _signal(wav, raw_segs)
        partial["audio_quality"] = quality.grade(sig)
        segs = audio.pad_and_merge(raw_segs, dur_ms)
        if not segs:
            raise EngineUnavailable("整支影片沒有偵測到人聲（VAD），沒有產生字幕")
        eng = engines.create(eid, models_dir, threads)
        version = getattr(eng, "version", "") or _sherpa_version()
        raws = []
        speech_total = sum(e - s for s, e in segs) or 1
        done = 0
        for i in range(0, len(segs), BATCH):
            part = segs[i:i + BATCH]
            raws += eng.recognize([audio.read_samples(wav, s, e) for s, e in part])
            done += sum(e - s for s, e in part)
            on_progress(round(10 + done / speech_total * 88, 1))
    cues: list[text.Cue] = []
    loops = empty = 0
    engine_chars = cue_chars = 0          # dev.58 診斷：引擎輸出的字數 vs 斷句後的字數（簡繁轉換前）
    last_text = ""
    for (s, e), r in zip(segs, raws):
        t = text.normalize_spaces(text.strip_tags(r.get("text", "")))
        t, n = text.collapse_loops(t)
        loops += n
        if not text.cer_normalize(t):
            empty += 1
            continue
        engine_chars += len(text.cer_normalize(t))
        last_text = t
        for c in text.segment_cues(t, s, e, st, r.get("tokens"), r.get("timestamps")):
            cue_chars += len(text.cer_normalize(c.text))
            c.text = text.trim_cue_punct(conv.convert(c.text))
            if c.text:
                cues.append(c)
    cues, stats = text.finalize(cues, dur_ms, st)
    if not cues:
        raise EngineUnavailable("辨識結果是空的，沒有產生字幕")
    speech_ms = sum(e - s for s, e in segs)
    diag = {"chars": {"engine": engine_chars, "cues": cue_chars},
            "tail": {"audio_ms": dur_ms, "speech_end_ms": raw_segs[-1][1] if raw_segs else None,
                     "last_cue_ms": [cues[-1].start, cues[-1].end], "last_engine_text": last_text[-40:]}}
    qc = text.quality(cues, len(segs), empty, loops, stats, speech_ms, dur_ms, conv.available, diag)
    aq = quality.grade(sig, {"empty_ratio": round(empty / len(segs), 3) if segs else None,
                             "chars_per_sec": round(cue_chars / (speech_ms / 1000), 2) if speech_ms > 0 else None,
                             "qc_status": qc["status"]})
    if sig and sig.get("error"):
        aq["signal_error"] = sig["error"]
    elapsed = time.time() - t0
    report = {
        "engine": eid, "model": eid, "engine_version": version, "route": engines.BY_ID[eid]["route"],
        "qc": qc,
        "audio_quality": aq,
        "diag": diag,
        "metrics": {"audio_sec": round(dur_ms / 1000, 1), "speech_sec": round(speech_ms / 1000, 1),
                    "elapsed_sec": round(elapsed, 1), "rtf": round(elapsed / max(0.001, dur_ms / 1000), 3),
                    "segments": len(segs), "cues": len(cues), "threads": threads},
        "settings": {"max_chars": st.max_chars, "max_sec": st.max_ms // 1000, "fixes": len(st.fixes),
                     "protect": len(st.protect),
                     "opencc": "s2twp" if conv.available else None},
    }
    return text.to_vtt_lines(cues), report


def _sherpa_version() -> str:
    try:
        from importlib.metadata import version
        return "sherpa-onnx " + version("sherpa-onnx")
    except Exception:  # noqa: BLE001
        return "sherpa-onnx"
