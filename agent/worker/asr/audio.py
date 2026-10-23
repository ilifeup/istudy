"""音訊前處理（dev.57）：FFmpeg 轉 16 kHz 單聲道 WAV、讀取片段、Silero VAD 切段（含前後留白與合併短間隔）。

規劃書 P-10／P-12：所有引擎用同一份 16 kHz WAV 與同一套切段，時間一律以原始影片時間軸為準（不變速、不刪靜音再回貼）。
"""
from __future__ import annotations

import os
import subprocess
import wave
from typing import Callable, Optional

SR = 16000
PAD_MS = 200          # 段落前後各留 0.2 秒，避免切到字
MERGE_GAP_MS = 300    # 間隔小於 0.3 秒的相鄰段落合併
MAX_SEG_MS = 20000    # 合併後的段落最長 20 秒（送進模型的長度）


class AudioError(Exception):
    pass


def extract_wav(src: str, dst: str, timeout: int = 7200) -> float:
    """影片 → 16 kHz 單聲道 16-bit WAV；回傳秒數。"""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", "-f", "wav", dst]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AudioError("抽取音訊逾時") from e
    if p.returncode != 0 or not os.path.exists(dst):
        raise AudioError("抽取音訊失敗：" + (p.stderr.strip().splitlines() or ["影片沒有音軌？"])[-1][:200])
    return wav_seconds(dst)


def wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or SR)


def read_samples(path: str, start_ms: int, end_ms: int):
    """讀取 [start, end) 的樣本 → numpy float32（-1～1）。"""
    import numpy as np  # 只有真的辨識時才需要 numpy
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        a = max(0, min(n, int(start_ms * SR / 1000)))
        b = max(a, min(n, int(end_ms * SR / 1000)))
        w.setpos(a)
        raw = w.readframes(b - a)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def pad_and_merge(segs: list[tuple[int, int]], duration_ms: int, pad: int = PAD_MS, gap: int = MERGE_GAP_MS,
                  max_len: int = MAX_SEG_MS) -> list[tuple[int, int]]:
    """VAD 段落（ms）前後加留白、合併短間隔；不超過影片長度、不重疊。"""
    out: list[list[int]] = []
    for s, e in sorted(segs):
        s, e = max(0, s - pad), min(duration_ms, e + pad)
        if e <= s:
            continue
        if out and s - out[-1][1] < gap and e - out[-1][0] <= max_len:
            out[-1][1] = max(out[-1][1], e)
        elif out and s < out[-1][1]:
            # 留白造成重疊、但合併後太長：從中間切開
            mid = (s + out[-1][1]) // 2
            out[-1][1] = mid
            out.append([mid, e])
        else:
            out.append([s, e])
    return [(a, b) for a, b in out if b > a]


def detect_speech(path: str, vad_model: str, threads: int = 1,
                  on_progress: Optional[Callable[[float], None]] = None) -> list[tuple[int, int]]:
    """Silero VAD（sherpa-onnx）→ 原始語音段落 [(start_ms, end_ms)]。"""
    import numpy as np
    import sherpa_onnx  # type: ignore

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = vad_model
    cfg.silero_vad.threshold = 0.4
    cfg.silero_vad.min_silence_duration = 0.4
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 12   # 超過時 VAD 內部提高門檻、找停頓切開
    cfg.sample_rate = SR
    cfg.num_threads = max(1, threads)
    window = cfg.silero_vad.window_size
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    segs: list[tuple[int, int]] = []

    def drain():
        while not vad.empty():
            f = vad.front
            segs.append((int(f.start * 1000 / SR), int((f.start + len(f.samples)) * 1000 / SR)))
            vad.pop()

    with wave.open(path, "rb") as w:
        total = w.getnframes() or 1
        done = 0
        rest = np.zeros(0, dtype=np.float32)
        while True:
            raw = w.readframes(SR * 30)
            if not raw:
                break
            buf = np.concatenate([rest, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0])
            n = (len(buf) // window) * window
            for i in range(0, n, window):
                vad.accept_waveform(buf[i:i + window])
            rest = buf[n:]
            done += len(raw) // 2
            drain()
            if on_progress:
                on_progress(done / total)
    vad.flush()
    drain()
    return segs
