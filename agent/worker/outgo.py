"""dev.61 Outgo 錄音轉文字（工作類型 outgo_asr）：/Media/Outgo 底下的錄音 → 逐字稿 TXT、SRT。

- Outgo 不是教材：不寫 media、folders、subtitles 等教材資料表。
- 工作狀態存在錄音旁邊的隱藏檔「.<檔名>.asr.json」；資料庫的 jobs 只用來排隊，工作結束後就刪除那一筆（短期）。
- 結果寫成「<檔名>.asr.txt」「<檔名>.asr.srt」（UTF-8）：先寫暫存檔，兩個都成功才換掉舊結果；失敗時舊結果與原始錄音都不動。
- 沿用字幕辨識的流程（FFmpeg 轉 16 kHz 單聲道 → Silero VAD → FunASR → 斷句、繁體轉換、修正表），長錄音由 Agent 背景處理，
  不經過 PHP、不會讓 HTTP 請求逾時。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from istudy_common.util import TZ

from . import asr, asrlog
from .context import Ctx, JobError

# 錄音格式：FFmpeg 都能解碼；不含影片格式（Outgo 只做錄音轉文字）
AUDIO_EXT = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "wma", "aif", "aiff", "amr", "mka", "ac3", "wv", "3ga"}
TXT_SUFFIX = ".asr.txt"
SRT_SUFFIX = ".asr.srt"


def is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lstrip(".").lower() in AUDIO_EXT


def state_path(audio: str) -> str:
    d, n = os.path.split(audio)
    return os.path.join(d, f".{n}.asr.json")


def read_state(audio: str) -> Optional[dict]:
    try:
        with open(state_path(audio), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _write_atomic(path: str, text: str) -> None:
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_state(audio: str, data: dict) -> None:
    _write_atomic(state_path(audio), json.dumps(data, ensure_ascii=False, indent=1))


def srt_time(ms: int) -> str:
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[tuple[int, int, str]]) -> str:
    out = []
    for i, (s, e, t) in enumerate(cues, 1):
        out += [str(i), f"{srt_time(s)} --> {srt_time(e)}", t, ""]
    return "\n".join(out)


def to_txt(cues: list[tuple[int, int, str]]) -> str:
    return "\n".join(t for _s, _e, t in cues) + "\n"


def now() -> str:
    return _dt.datetime.now(TZ).isoformat(timespec="seconds")


def recognize(ctx: Ctx, job: dict) -> dict:
    p = job.get("payload") or {}
    rel = str(p.get("path") or "")
    try:
        audio = ctx.paths.outgo_file(rel)
    except ValueError as e:
        raise JobError(str(e)) from e
    if not os.path.isfile(audio) or os.path.islink(audio):
        raise JobError("找不到這個錄音檔（可能已被移動或刪除）")
    if not is_audio(audio):
        raise JobError("不是支援的錄音格式")
    cfg = ctx.config("asr", {}) or {}
    choice = str(p.get("engine") or (cfg.get("engine") if isinstance(cfg, dict) else "") or "auto")
    base = {"path": rel, "job_id": job["id"], "engine_requested": p.get("engine") or "", "requested_by": job.get("created_by"),
            "size": os.path.getsize(audio), "started_at": now()}
    old = read_state(audio) or {}
    keep = {k: old[k] for k in ("last_done",) if k in old}
    try:
        write_state(audio, {**keep, **base, "state": "running"})
    except OSError as e:
        raise JobError(f"Outgo 資料夾不能寫入（{e.strerror}）：請執行 sudo istudyctl media-acl --fix") from e
    txt, srt = audio + TXT_SUFFIX, audio + SRT_SUFFIX
    tmps = [os.path.join(os.path.dirname(audio), f".{os.path.basename(x)}.new") for x in (txt, srt)]
    log = dict(kind="outgo", job=job, path="Outgo/" + rel, src=audio, engine_requested=str(p.get("engine") or ""),
               started_at=base["started_at"])
    report = None
    try:
        cues, report = asr.transcribe(audio, choice, ctx.env.get("ISTUDY_MODELS_DIR", "/models"), cfg, None,
                                      lambda pct: ctx.progress(job["id"], {"percent": pct}), ctx.env)
        with open(tmps[0], "w", encoding="utf-8", newline="\n") as f:
            f.write(to_txt(cues))
        with open(tmps[1], "w", encoding="utf-8", newline="\n") as f:
            f.write(to_srt(cues))
        os.replace(tmps[0], txt)        # 兩個暫存檔都寫好之後才換掉舊結果
        os.replace(tmps[1], srt)
    except Exception as e:  # noqa: BLE001 — 任何失敗都要寫進狀態檔；原始錄音與舊結果不動
        for t in tmps:
            try:
                os.remove(t)
            except OSError:
                pass
        known = isinstance(e, (asr.EngineUnavailable, asr.audio.AudioError, JobError))
        msg = str(e) if known else (f"無法寫入結果（{e.strerror}）" if isinstance(e, OSError) else f"內部錯誤：{type(e).__name__}: {e}")
        try:
            write_state(audio, {**keep, **base, "state": "failed", "error": msg[:500], "finished_at": now()})
        except OSError:
            pass
        asrlog.record(ctx, report=report, error=e, error_text=msg, **log)
        raise JobError(msg) from e
    aq = report.get("audio_quality") or {}
    done = {"engine": report["engine"], "model": report["model"], "segments": len(cues), "qc": report["qc"]["status"],
            "audio_sec": report["metrics"]["audio_sec"], "rtf": report["metrics"]["rtf"], "finished_at": now(),
            "quality": aq.get("level", "unknown"), "quality_reasons": aq.get("reasons", [])[:6],
            "txt": os.path.basename(txt), "srt": os.path.basename(srt)}
    write_state(audio, {**base, "state": "done", **done, "last_done": done})
    asrlog.record(ctx, report=report, **log)
    return {"path": rel, **{k: done[k] for k in ("engine", "segments", "rtf", "qc", "quality")}}
