"""dev.61 ASR 處理記錄：字幕辨識（asr）與 Outgo 錄音轉文字（outgo_asr）每次處理都寫一筆（成功、失敗都記）。

寫在資料表 asr_log（migration 0009），「統計與報告」頁列出與匯出；同時在系統 Log 留一行摘要。
寫入失敗不影響辨識結果（只在系統 Log 記警告）。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from istudy_common import db as dbmod

from .asr import quality
from .context import Ctx

KIND_TEXT = {"subtitle": "字幕", "outgo": "Outgo"}


def _hms(sec) -> str:
    if not isinstance(sec, (int, float)):
        return "長度未知"
    s = int(round(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def record(ctx: Ctx, kind: str, job: dict, path: str, src: Optional[str], engine_requested: str,
           report: Optional[dict] = None, error: Optional[BaseException] = None, media_id: Optional[int] = None,
           started_at: Optional[str] = None, error_text: Optional[str] = None) -> Optional[int]:
    rep = report or {}
    part = getattr(error, "asr_partial", None) or {} if error is not None else {}
    metrics = rep.get("metrics") or {}
    aq = rep.get("audio_quality") or part.get("audio_quality") or quality.grade(None)
    try:
        size = os.path.getsize(src) if src and os.path.isfile(src) else None
    except OSError:
        size = None
    status = "failed" if error is not None else "done"
    err = (error_text or str(error) or type(error).__name__)[:500] if error is not None else None
    audio_sec = metrics.get("audio_sec", part.get("audio_sec"))
    engine = rep.get("engine") or part.get("engine")
    row = [started_at, kind, job.get("id"), media_id, path, size, audio_sec, engine_requested or None, engine, status, err,
           metrics.get("rtf"), metrics.get("segments"), metrics.get("cues"), (rep.get("qc") or {}).get("status"),
           aq.get("level", "unknown"), json.dumps(aq, ensure_ascii=False), job.get("created_by")]
    try:
        lid = ctx.conn.val(
            "INSERT INTO asr_log(started_at, kind, job_id, media_id, path, file_size, audio_sec, engine_requested, engine, status, error,"
            " rtf, segments, cues, qc_status, quality, quality_detail, requested_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS jsonb), %s) RETURNING id", row)
    except dbmod.DBError as e:
        ctx.syslog("warn", f"ASR 處理記錄寫入失敗：{e}", path=path, job_id=job.get("id"))
        return None
    label = quality.LABEL.get(aq.get("level", "unknown"), "未知")
    if error is None:
        ctx.syslog("info", f"ASR 完成（{KIND_TEXT.get(kind, kind)}）：{path}｜{engine or '引擎未知'}｜{_hms(audio_sec)}｜音訊品質 {label}",
                   asr_log_id=lid, job_id=job.get("id"))
    else:
        ctx.syslog("warn", f"ASR 失敗（{KIND_TEXT.get(kind, kind)}）：{path}｜{err}", asr_log_id=lid, job_id=job.get("id"))
    return lid
