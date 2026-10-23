"""字幕：自動辨識（dev.57 起為可替換引擎，見 worker/asr）與 SRT／VTT 匯入（第 12 章）。

輸出 /Media/subtitles/<教材ID>.vtt，並同步寫入 subtitle_cues（字幕搜尋與進階字幕面板用）。
工作類型與 subtitles.source 是「asr」（＝自動辨識；dev.58 前叫 whisper）；實際引擎記在 subtitles.model 與 asr_report。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Iterable, Optional

from istudy_common.util import TZ

from . import asr, asrlog
from .context import Ctx, JobError



def fmt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_vtt(cues: Iterable[tuple[int, int, str]]) -> str:
    out = ["WEBVTT", ""]
    for s, e, t in cues:
        out += [f"{fmt_time(s / 1000)} --> {fmt_time(e / 1000)}", t, ""]
    return "\n".join(out) + "\n"


_ts = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _ms(t: str) -> Optional[int]:
    m = _ts.fullmatch(t.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    return ((h * 60 + int(m.group(2))) * 60 + int(m.group(3))) * 1000 + int(m.group(4).ljust(3, "0"))


def parse_vtt(text: str) -> list[tuple[int, int, str]]:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = block.strip().split("\n")
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        i = 0 if "-->" in lines[0] else 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, b = lines[i].split("-->", 1)
        s, e = _ms(a), _ms(b.strip().split()[0] if b.strip() else "")
        body = "\n".join(lines[i + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body).strip()
        if s is None or e is None or e < s or not body:
            continue
        cues.append((s, e, body))
    cues.sort(key=lambda c: c[0])
    return cues


def save_subtitle(ctx: Ctx, media_id: int, cues: list[tuple[int, int, str]], source: str, model: str = "",
                  created_by: Optional[int] = None, report: Optional[dict] = None) -> dict:
    if not cues:
        raise JobError("沒有任何字幕片段")
    os.makedirs(ctx.paths.subtitles, exist_ok=True)
    path = ctx.paths.subtitle_file(media_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(to_vtt(cues))
    # dev.60：資料庫寫入成功後才換掉 VTT 檔；任何一步失敗，舊字幕（資料庫與檔案）都保持原樣
    try:
        _save_rows(ctx, media_id, cues, source, model, created_by, report, os.path.basename(path))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    return {"segments": len(cues), "path": os.path.basename(path)}


def _save_rows(ctx: Ctx, media_id: int, cues: list[tuple[int, int, str]], source: str, model: str,
               created_by: Optional[int], report: Optional[dict], name: str) -> None:
    with ctx.conn.transaction():
        ctx.conn.execute("DELETE FROM subtitle_cues WHERE media_id = %s", [media_id])
        # 批次寫入（每批 500 筆）
        for start in range(0, len(cues), 500):
            batch = cues[start:start + 500]
            values, params = [], []
            for idx, (s, e, t) in enumerate(batch, start):
                values.append("(%s, %s, %s, %s, %s)")
                params += [media_id, idx, s, e, t]
            ctx.conn.execute("INSERT INTO subtitle_cues(media_id, idx, start_ms, end_ms, text) VALUES " + ",".join(values), params)
        ctx.conn.execute(
            "INSERT INTO subtitles(media_id, path, source, model, segment_count, created_by, asr_report) "
            "VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS jsonb)) "
            "ON CONFLICT (media_id) DO UPDATE SET path = EXCLUDED.path, source = EXCLUDED.source, model = EXCLUDED.model, "
            "segment_count = EXCLUDED.segment_count, created_at = NOW(), created_by = EXCLUDED.created_by, "
            "asr_report = EXCLUDED.asr_report",
            [media_id, name, source, model, len(cues), created_by,
             json.dumps(report, ensure_ascii=False) if report else None])


# ---------- 匯入 ----------

def import_subtitle(ctx: Ctx, job: dict) -> dict:
    p = job["payload"]
    mid = int(p["media_id"])
    if not ctx.conn.val("SELECT 1 FROM media WHERE id = %s AND kind = 'video'", [mid]):
        raise JobError("找不到影片")
    cues = parse_vtt(p.get("vtt") or "")
    return save_subtitle(ctx, mid, cues, "import", "", p.get("account_id") or job.get("created_by"))


# ---------- 自動辨識 ----------

def asr_status(ctx: Ctx) -> dict:
    """Agent 心跳用：每個引擎是否可用。"""
    models_dir = ctx.env.get("ISTUDY_MODELS_DIR", "/models")
    return asr.engines.status_all(models_dir)


def recognize(ctx: Ctx, job: dict) -> dict:
    """自動產生字幕（工作類型 asr）。payload.engine 可指定引擎，沒有就用設定（預設「自動」）。"""
    mid = int(job["payload"]["media_id"])
    m = ctx.conn.one("SELECT * FROM media WHERE id = %s", [mid])
    if not m or m["kind"] != "video":
        raise JobError("找不到影片")
    if m["play_mode"] == "direct" and m["status"] == "present":
        src = ctx.paths.original(m["rel_path"])
    elif m["convert_status"] == "done":
        src = ctx.paths.playback_file(mid)  # 以播放用 MP4 為輸入，原檔刪除後仍可重新產生
    else:
        raise JobError("影片尚未轉檔完成")
    if not os.path.exists(src) and not _fake(ctx):
        raise JobError("影片檔不存在")
    cfg = ctx.config("asr", {}) or {}
    choice = str(job["payload"].get("engine") or (cfg.get("engine") if isinstance(cfg, dict) else "") or "auto")
    started = _dt.datetime.now(TZ).isoformat(timespec="seconds")
    log = dict(kind="subtitle", job=job, path=m["rel_path"], src=src, engine_requested=str(job["payload"].get("engine") or ""),
               media_id=mid, started_at=started)
    try:
        cues, report = asr.transcribe(src, choice, ctx.env.get("ISTUDY_MODELS_DIR", "/models"), cfg, m["duration_sec"],
                                      lambda pct: ctx.progress(job["id"], {"percent": pct}), ctx.env)
    except Exception as e:  # noqa: BLE001 — dev.61：失敗也寫 ASR 處理記錄
        asrlog.record(ctx, error=e, **log)
        if isinstance(e, (asr.EngineUnavailable, asr.audio.AudioError)):
            raise JobError(str(e)) from e
        raise
    try:
        res = save_subtitle(ctx, mid, cues, "asr", report["model"], job.get("created_by"), report)
    except Exception as e:  # noqa: BLE001
        asrlog.record(ctx, report=report, error=e, **log)
        raise
    asrlog.record(ctx, report=report, **log)
    aq = report.get("audio_quality") or {}
    return {**res, "engine": report["engine"], "model": report["model"], "qc": report["qc"]["status"],
            "qc_reasons": report["qc"]["reasons"], "rtf": report["metrics"]["rtf"], "audio_quality": aq.get("level")}


def _fake(ctx: Ctx) -> bool:
    return ctx.env.get("ISTUDY_ASR_ENGINE") == "fake"
