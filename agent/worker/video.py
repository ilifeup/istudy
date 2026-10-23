"""影片：檢測（probe）、轉檔（convert，含四項自動檢查）、重新檢查（verify）、刪除原檔（delete_original）。第 11 章。"""
from __future__ import annotations

import datetime as _dt
import json
import os

from istudy_common.checks import disk_usage
from istudy_common.util import TZ

from . import clips
from .context import Ctx, JobError, Retry
from .media_tools import (ToolError, _stderr_tail, build_command, decide_play_mode, ffprobe, recorded_time, run_ffmpeg, summarize_probe,
                          verify_output)

GIB = 1024 ** 3
DELETE_AFTER_DAYS = 30


LABEL = {"qsv": "Quick Sync", "vaapi": "VA-API", "cpu": "CPU"}


def _media(ctx: Ctx, media_id: int) -> dict:
    m = ctx.conn.one("SELECT * FROM media WHERE id = %s", [media_id])
    if not m:
        raise JobError("找不到教材（可能已解除關聯）")
    return m


def _jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def queue_asr_if_needed(ctx: Ctx, media_id: int) -> None:
    sch = ctx.schedule()
    if not sch["auto_whisper"]:
        return
    if ctx.conn.val("SELECT 1 FROM subtitles WHERE media_id = %s", [media_id]):
        return
    ctx.enqueue("asr", {"media_id": media_id}, 0, unique_key={"media_id": media_id})


# ---------- probe ----------

def probe(ctx: Ctx, job: dict) -> dict:
    mid = int(job["payload"]["media_id"])
    m = _media(ctx, mid)
    if m["kind"] != "video":
        raise JobError("不是影片")
    if m["status"] == "original_deleted":
        return {"skipped": "原檔已刪除，維持現有 MP4"}
    src = ctx.paths.original(m["rel_path"])
    if not os.path.exists(src):
        ctx.conn.execute("UPDATE media SET status = 'missing' WHERE id = %s", [mid])
        raise JobError("原檔不存在（請重新掃描）")
    ctx.conn.execute("UPDATE media SET convert_status = 'probing' WHERE id = %s", [mid])
    try:
        raw = ffprobe(src)
        info = summarize_probe(src, raw)
        mode = decide_play_mode(info, m["ext"])
    except ToolError as e:
        ctx.conn.execute("UPDATE media SET convert_status = 'failed', convert_error = %s WHERE id = %s", [str(e), mid])
        raise JobError(str(e)) from e
    rec = recorded_time(src, raw)
    if mode == "direct":
        ctx.conn.execute(
            "UPDATE media SET probe = CAST(%s AS jsonb), duration_sec = %s, recorded_at = %s, play_mode = 'direct', convert_status = 'direct', "
            "convert_error = NULL, playback_path = NULL, updated_at = NOW() WHERE id = %s", [_jdump(info), info.get("duration"), rec, mid])
        queue_asr_if_needed(ctx, mid)
    else:
        ctx.conn.execute(
            "UPDATE media SET probe = CAST(%s AS jsonb), duration_sec = %s, recorded_at = %s, play_mode = %s, convert_status = 'queued', "
            "convert_error = NULL, updated_at = NOW() WHERE id = %s", [_jdump(info), info.get("duration"), rec, mode, mid])
        ctx.enqueue("convert", {"media_id": mid}, 0, unique_key={"media_id": mid})
    if m["folder_id"]:
        clips.detect(ctx.conn, m["folder_id"], float(ctx.config("clip_threshold_gib", 1.9) or 1.9))
    return {"play_mode": mode, "interlaced": info.get("interlaced"), "video": info.get("video_codec"), "audio": info.get("audio_codec"),
            "duration": info.get("duration")}


# ---------- convert ----------

def _space_ok(ctx: Ctx, size: int) -> tuple[bool, int, int]:
    reserve = int(float(ctx.env.get("ISTUDY_RESERVE_GB", "50")) * GIB)
    need = int(size * 1.2) + reserve
    u = disk_usage(ctx.paths.media_root)
    free = u["free"] if u else 0
    return free >= need, free, need


def convert(ctx: Ctx, job: dict) -> dict:
    ctx.require_media_marker()
    mid = int(job["payload"]["media_id"])
    m = _media(ctx, mid)
    if m["status"] != "present":
        raise JobError("原檔不存在，無法轉檔")
    info = m["probe"] or {}
    mode = m["play_mode"]
    if mode not in ("remux", "transcode"):
        return {"skipped": f"處理方式為 {mode}，不需轉檔"}
    ok, free, need = _space_ok(ctx, int(m["size_bytes"]))
    if not ok:
        ctx.flags.add("space_low")
        raise Retry(f"⚠ 空間不足：需要 {need / GIB:.1f}GB，剩 {free / GIB:.1f}GB", 1800, "space_low")
    ctx.flags.discard("space_low")
    src = ctx.paths.original(m["rel_path"])
    tmp = ctx.paths.playback_tmp(mid)
    dst = ctx.paths.playback_file(mid)
    os.makedirs(ctx.paths.playback, exist_ok=True)
    if os.path.exists(tmp):
        os.remove(tmp)
    encoder = ctx.encoder or "cpu"
    if mode == "remux":
        encoder = "copy"
    ctx.conn.execute("UPDATE media SET convert_status = 'converting', convert_error = NULL, encoder = %s WHERE id = %s",
                     [encoder if encoder != "copy" else None, mid])

    def progress(pct: float) -> None:
        ctx.progress(job["id"], {"stage": "converting", "percent": pct})

    duration = m["duration_sec"]
    cmd = build_command(mode, src, tmp, info, encoder)
    if mode == "remux":
        cmd.insert(cmd.index("-sn"), "-avoid_negative_ts")
        cmd.insert(cmd.index("-sn"), "make_zero")
    rc, err = run_ffmpeg(cmd, duration, progress)
    # dev.55 HW-01：硬體編碼失敗時依 QSV → VA-API → CPU 往下試；VA-API 檢測可用時不會直接跳到 CPU。
    while rc != 0 and encoder in ("qsv", "vaapi"):
        nxt = "vaapi" if encoder == "qsv" and (ctx.encoders.get("vaapi") or {}).get("ok") else "cpu"
        level = "warn"
        msg = f"{LABEL[encoder]} 轉檔失敗，改用 {LABEL[nxt]}"
        if nxt == "cpu":
            msg = f"⚠ 轉檔降級為 CPU（較慢）：{LABEL[encoder]} 失敗"
        ctx.syslog(level, msg, media_id=mid, from_encoder=encoder, to_encoder=nxt,
                   error=_stderr_tail(err))
        encoder = nxt
        if os.path.exists(tmp):
            os.remove(tmp)
        ctx.conn.execute("UPDATE media SET encoder = %s WHERE id = %s", [encoder, mid])
        rc, err = run_ffmpeg(build_command(mode, src, tmp, info, encoder), duration, progress)
    if rc != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        msg = "轉檔失敗：" + (err.strip().splitlines() or ["未知錯誤"])[-1][:300]
        ctx.conn.execute("UPDATE media SET convert_status = 'failed', convert_error = %s WHERE id = %s", [msg, mid])
        raise JobError(msg)
    # 先檢查暫存檔，通過才改名為正式檔，避免播放到半成品
    ctx.conn.execute("UPDATE media SET convert_status = 'verifying' WHERE id = %s", [mid])
    ctx.progress(job["id"], {"stage": "verifying", "percent": 100})
    result = verify_output(tmp, duration, bool(info.get("has_audio")))
    if not result["passed"]:
        os.remove(tmp)
        failed = "；".join(c["name"] for c in result["checks"] if not c["ok"])
        ctx.conn.execute("UPDATE media SET convert_status = 'verify_failed', verify_result = CAST(%s AS jsonb), convert_error = %s WHERE id = %s",
                         [_jdump(result), "✗ 驗證失敗：" + failed, mid])
        raise JobError("✗ 驗證失敗：" + failed)
    os.replace(tmp, dst)
    size = os.path.getsize(dst)
    now = _dt.datetime.now(TZ)
    ctx.conn.execute(
        "UPDATE media SET convert_status = 'done', playback_path = %s, playback_size = %s, verify_result = CAST(%s AS jsonb), "
        "verified_at = %s, delete_due_at = %s, encoder = %s, convert_error = NULL, updated_at = NOW() WHERE id = %s",
        [os.path.basename(dst), size, _jdump(result), now, now + _dt.timedelta(days=DELETE_AFTER_DAYS),
         encoder if encoder != "copy" else None, mid])
    queue_asr_if_needed(ctx, mid)
    return {"mode": mode, "encoder": encoder, "playback_size": size, "original_size": int(m["size_bytes"]),
            "verify": result}


# ---------- verify（Super 要求重新檢查既有 MP4） ----------

def verify(ctx: Ctx, job: dict) -> dict:
    mid = int(job["payload"]["media_id"])
    m = _media(ctx, mid)
    dst = ctx.paths.playback_file(mid)
    if not os.path.exists(dst):
        raise JobError("播放用 MP4 不存在")
    result = verify_output(dst, m["duration_sec"], bool((m["probe"] or {}).get("has_audio")))
    ctx.conn.execute("UPDATE media SET verify_result = CAST(%s AS jsonb) WHERE id = %s", [_jdump(result), mid])
    if not result["passed"]:
        ctx.conn.execute("UPDATE media SET convert_status = 'verify_failed', delete_due_at = NULL, convert_error = %s WHERE id = %s",
                         ["✗ 驗證失敗", mid])
    return result


# ---------- delete_original（Super 確認後；第 11 章） ----------

def delete_original(ctx: Ctx, job: dict) -> dict:
    ctx.require_media_marker()
    p = job["payload"]
    mid = int(p["media_id"])
    m = _media(ctx, mid)
    if m["status"] != "present" or m["original_deleted_at"] is not None:
        raise JobError("原檔已不存在或已刪除")
    if m["play_mode"] not in ("remux", "transcode") or m["convert_status"] != "done" or not m["verified_at"]:
        raise JobError("MP4 尚未通過自動檢查，不能刪除原檔")
    if m["delete_due_at"] is None or m["delete_due_at"] > _dt.datetime.now(TZ):
        raise JobError("尚未滿 30 天，不能刪除原檔")
    dst = ctx.paths.playback_file(mid)
    # 再檢查一次 MP4 存在且大小不變
    if not os.path.exists(dst):
        raise JobError("播放用 MP4 不存在，取消刪除")
    size = os.path.getsize(dst)
    expected = p.get("expected_playback_size")
    if size != m["playback_size"] or (expected is not None and int(expected) != size):
        raise JobError("播放用 MP4 大小已改變，取消刪除（請重新檢查）")
    src = ctx.paths.original(m["rel_path"])
    if not os.path.exists(src):
        raise JobError("原檔已不存在")
    orig_size = os.path.getsize(src)
    try:
        os.remove(src)
    except PermissionError:
        raise JobError("沒有權限刪除原檔：原檔所在的資料夾需要讓 Agent（uid 10001）可以寫入。"
                       "請執行 sudo istudyctl media-acl 檢查，確認後 sudo istudyctl media-acl --fix 修復（ZFS 需 acltype=posixacl）")
    ctx.conn.execute("UPDATE media SET status = 'original_deleted', original_deleted_at = NOW(), updated_at = NOW() WHERE id = %s", [mid])
    ctx.audit("original.deleted", {"original_size": orig_size, "playback_size": size, "job_id": job["id"]}, mid, m["rel_path"],
              actor_id=job.get("created_by"))
    return {"deleted": m["rel_path"], "freed": orig_size}


def cleanup_temp(ctx: Ctx) -> int:
    """啟動時清除轉檔暫存檔（停電、重開機中斷）。"""
    n = 0
    try:
        for name in os.listdir(ctx.paths.playback):
            if name.startswith(".") and name.endswith(".tmp.mp4"):
                try:
                    os.remove(os.path.join(ctx.paths.playback, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n
