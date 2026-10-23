"""dev.61 中文音訊品質估計（高／中／低）：給 ASR 處理記錄與「統計與報告」用的粗略判斷，不是正式的音質量測。

訊號面（用 ASR 已經產生的 16 kHz 單聲道 WAV 與 VAD 語音段落，逐段讀檔，記憶體不隨長度增加）：
  - 訊噪比：語音段落的音量（第 75 百分位）減去非語音部分的音量（中位數）。
  - 語音音量：語音段落的音量（dBFS）。太小聲時辨識率下降。
  - 爆音：樣本到達滿刻度的比例（削波）。
  - 頻寬：語音中 4.2～8 kHz 的能量比例。電話、AMR 這類 8 kHz 錄音幾乎沒有，子音（ㄙ、ㄘ、ㄕ）容易聽錯。
辨識面：辨識不出文字的語音段比例、每秒語音辨識出的字數（中文一般講話每秒約 3～5 字）、字幕品質檢查結果。
每一項分成高／中／低，總結取最差的一項，並寫出原因；門檻是經驗值，只用來大略分類。
"""
from __future__ import annotations

import wave
from typing import Optional

SR = 16000
FRAME = SR // 50          # 20 ms
FFT_N = 512               # 32 ms
MAX_FFT = 1500            # 頻寬估計最多取 1500 個語音窗（約 48 秒）
LABEL = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}
RANK = {"low": 1, "medium": 2, "high": 3}


def signal_metrics(wav_path: str, speech_segs: list[tuple[int, int]]) -> dict:
    """WAV（16 kHz 單聲道 16-bit）＋語音段落（ms）→ 訊號指標。"""
    import numpy as np
    with wave.open(wav_path, "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("需要 16 kHz 單聲道 16-bit WAV")
        total = w.getnframes()
        n_frames = total // FRAME
        speech = np.zeros(n_frames, dtype=bool)
        for s, e in speech_segs:
            a, b = max(0, int(s * SR / 1000) // FRAME), min(n_frames, int(e * SR / 1000) // FRAME)
            if b > a:
                speech[a:b] = True
        n_speech = int(speech.sum())
        step = max(1, n_speech // MAX_FFT)
        db = np.empty(n_frames, dtype=np.float32)
        clipped = 0
        hf = lf = 0.0
        freqs = np.fft.rfftfreq(FFT_N, 1 / SR)
        band_hi = freqs >= 4200          # 8 kHz 錄音（電話、AMR）在 4 kHz 以上幾乎沒有能量
        band_all = freqs >= 200
        win = np.hanning(FFT_N).astype(np.float32)
        seen_speech = 0
        idx = 0
        chunk = FRAME * 1500          # 30 秒
        while idx < n_frames:
            raw = w.readframes(chunk)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16)
            clipped += int(np.count_nonzero(np.abs(x.astype(np.int32)) >= 32700))
            k = len(x) // FRAME
            if k == 0:
                break
            fr = x[:k * FRAME].astype(np.float32).reshape(k, FRAME) / 32768.0
            rms = np.sqrt(np.mean(fr * fr, axis=1))
            db[idx:idx + k] = 20 * np.log10(np.maximum(rms, 1e-5))
            sp = speech[idx:idx + k]
            js = np.nonzero(sp)[0]
            pick = js[(seen_speech + np.arange(len(js))) % step == 0]
            seen_speech += len(js)
            for j in pick[pick * FRAME + FFT_N <= len(x)]:      # 平均取樣語音窗（最多約 MAX_FFT 個）
                seg = x[j * FRAME:j * FRAME + FFT_N].astype(np.float32) / 32768.0
                p = np.abs(np.fft.rfft(seg * win)) ** 2
                hf += float(p[band_hi].sum())
                lf += float(p[band_all].sum())
            idx += k
        db = db[:idx]
        speech = speech[:idx]
    out: dict = {"frames": int(idx), "speech_ratio": round(float(speech.mean()) if idx else 0.0, 3),
                 "clip_pct": round(clipped / max(1, total) * 100, 3)}
    if speech.any():
        out["speech_dbfs"] = round(float(np.percentile(db[speech], 75)), 1)
        quiet = db[~speech]
        noise = float(np.median(quiet)) if len(quiet) >= 50 else float(np.percentile(db, 10))
        out["noise_dbfs"] = round(noise, 1)
        out["snr_db"] = round(out["speech_dbfs"] - noise, 1)
        out["noise_from"] = "non_speech" if len(quiet) >= 50 else "quietest_10pct"
        out["hf_pct"] = round(hf / lf * 100, 2) if lf > 0 else 0.0
    return out


def _lvl(v: Optional[float], high: float, medium: float, reverse: bool = False) -> Optional[str]:
    if v is None:
        return None
    if reverse:      # 越小越好
        return "high" if v < high else "medium" if v < medium else "low"
    return "high" if v >= high else "medium" if v >= medium else "low"


def grade(sig: Optional[dict], asr: Optional[dict] = None) -> dict:
    """訊號指標＋辨識結果 → {level, label, reasons, factors, metrics}。沒有任何資料時 level＝unknown。"""
    factors: dict[str, str] = {}
    reasons: list[str] = []
    sig = sig or {}
    asr = asr or {}
    if "snr_db" in sig:
        factors["snr"] = _lvl(sig["snr_db"], 25, 15)
        if factors["snr"] != "high":
            reasons.append(f"背景雜音偏高（訊噪比約 {sig['snr_db']:.0f} dB）")
    if "speech_dbfs" in sig:
        factors["loudness"] = _lvl(sig["speech_dbfs"], -35, -45)
        if factors["loudness"] != "high":
            reasons.append(f"人聲音量偏小（約 {sig['speech_dbfs']:.0f} dBFS）")
    if "clip_pct" in sig and "speech_dbfs" in sig:
        factors["clipping"] = _lvl(sig["clip_pct"], 0.1, 1.0, reverse=True)
        if factors["clipping"] != "high":
            reasons.append(f"有爆音（削波約 {sig['clip_pct']:.2f}% 的樣本）")
    if "hf_pct" in sig:
        factors["bandwidth"] = "high" if sig["hf_pct"] >= 0.2 else "medium"      # 窄頻最多「中」：仍聽得懂
        if factors["bandwidth"] != "high":
            reasons.append("頻寬窄（像電話或 8 kHz 錄音），子音容易辨識錯")
    if asr.get("empty_ratio") is not None:
        factors["recognized"] = _lvl(asr["empty_ratio"], 0.15, 0.3, reverse=True)
        if factors["recognized"] != "high":
            reasons.append(f"{round(asr['empty_ratio'] * 100)}% 的語音段辨識不出文字")
    if asr.get("chars_per_sec") is not None:
        factors["density"] = _lvl(asr["chars_per_sec"], 2.0, 1.0)
        if factors["density"] != "high":
            reasons.append(f"每秒語音只辨識出約 {asr['chars_per_sec']:.1f} 個字（中文一般約 3～5 字）")
    if asr.get("qc_status") == "needs_review":
        factors["qc"] = "medium"
        reasons.append("字幕品質檢查需要人工確認")
    vals = [v for v in factors.values() if v]
    level = min(vals, key=lambda x: RANK[x]) if vals else "unknown"
    metrics = {k: sig[k] for k in ("snr_db", "speech_dbfs", "noise_dbfs", "clip_pct", "hf_pct", "speech_ratio") if k in sig}
    metrics.update({k: asr[k] for k in ("empty_ratio", "chars_per_sec") if asr.get(k) is not None})
    return {"level": level, "label": LABEL[level], "reasons": reasons, "factors": factors, "metrics": metrics}
