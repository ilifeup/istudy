"""深度掃描教材（第 13 章）。

- 教材根目錄是 /Media（dev.59 起；之前是 /Media/videos）。第一層資料夾＝課程資料夾（權限單位）。
- 第一層的系統資料夾（playback、subtitles、doc-cache…，見 util.SYSTEM_TOP_DIRS）不掃描，列在略過清單；
  只看第一層：課程裡剛好同名的子資料夾照常掃描。
- 略過「.」開頭、符號連結、系統暫存檔、Server App 設定的排除名單；名稱不是 UTF-8、含反斜線或控制字元、路徑太長的也略過並寫明原因。
- 以相對路徑比對；新路徑先用「大小＋前後各 1MB 雜湊」找遺失的教材，相符就沿用原教材 ID（搬移／改名）。
- 整個掃描在一個資料庫交易內完成；失敗就整批回復。
- dry_run：只統計不寫入（首次設定精靈的教材盤點）。
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import os
from dataclasses import dataclass
from typing import Optional

from istudy_common.checks import disk_usage
from istudy_common.util import (JUNK_NAMES, JUNK_PREFIX, JUNK_SUFFIX, MAX_REL_BYTES, SUBTITLE_EXT, TZ, is_system_top, kind_for_ext,
                                 name_problem, natural_key)

from . import clips
from .context import Ctx, JobError

CHUNK = 1024 * 1024


@dataclass
class FileInfo:
    rel: str
    size: int
    mtime: float
    ext: str
    kind: str


def quick_hash(path: str, size: Optional[int] = None) -> str:
    """檔案大小＋前後各 1MB 的 SHA-256（不讀整個大檔）。"""
    size = os.path.getsize(path) if size is None else size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > 2 * CHUNK:
            f.seek(size - CHUNK)
            h.update(f.read(CHUNK))
        elif size > CHUNK:
            h.update(f.read())
    return h.hexdigest()[:32]


def _excluded(rel: str, name: str, patterns: list[str]) -> bool:
    low_rel, low_name = rel.lower(), name.lower()
    return any(fnmatch.fnmatchcase(low_rel, p.lower()) or fnmatch.fnmatchcase(low_name, p.lower()) for p in patterns)


def walk(root: str, patterns: list[str]) -> tuple[list[str], dict[str, FileInfo], list[dict]]:
    """遞迴掃描 root，回傳（資料夾相對路徑、檔案、略過清單）。"""
    if not os.path.isdir(root):
        raise JobError(f"找不到教材根目錄 {root}")
    dirs: list[str] = []
    files: dict[str, FileInfo] = {}
    skipped: list[dict] = []

    def rec(abs_dir: str, rel_dir: str) -> None:
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: natural_key(e.name))
        except OSError as e:
            skipped.append({"path": rel_dir or "/", "reason": f"無法讀取資料夾：{e.strerror}"})
            return
        for e in entries:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            if e.name.startswith("."):
                continue
            if not rel_dir and is_system_top(e.name):
                skipped.append({"path": rel, "reason": "系統資料夾（略過）", "system": True})
                continue
            why = name_problem(e.name)
            if why:
                skipped.append({"path": rel.encode("utf-8", "replace").decode("utf-8"), "reason": why})
                continue
            if len(rel.encode("utf-8")) > MAX_REL_BYTES:
                skipped.append({"path": rel[:200] + "…", "reason": f"完整路徑超過 {MAX_REL_BYTES} 位元組"})
                continue
            low = e.name.lower()
            if low in JUNK_NAMES or low.startswith(JUNK_PREFIX) or low.endswith(JUNK_SUFFIX):
                continue
            if _excluded(rel, e.name, patterns):
                skipped.append({"path": rel, "reason": "排除名單"})
                continue
            try:
                if e.is_symlink():
                    skipped.append({"path": rel, "reason": "符號連結（略過）"})
                    continue
                if e.is_dir(follow_symlinks=False):
                    dirs.append(rel)
                    rec(e.path, rel)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                skipped.append({"path": rel, "reason": f"無法讀取：{err.strerror}"})
                continue
            ext = os.path.splitext(e.name)[1].lstrip(".").lower()
            kind = kind_for_ext(ext)
            if kind is None:
                if ext not in SUBTITLE_EXT:  # 字幕檔由 Super 在教材清單「指定字幕」使用，不算教材
                    skipped.append({"path": rel, "reason": "不支援的檔案類型", "size": st.st_size})
                continue
            if st.st_size == 0:
                skipped.append({"path": rel, "reason": "空檔案"})
                continue
            if "/" not in rel:
                skipped.append({"path": rel, "reason": "放在根目錄（不屬於任何課程），只有 super 看得到", "warn_only": True})
            files[rel] = FileInfo(rel, st.st_size, st.st_mtime, ext, kind)

    rec(root, "")
    return dirs, files, skipped


def summarize(dirs: list[str], files: dict[str, FileInfo], skipped: list[dict], media_root: str) -> dict:
    by_kind: dict[str, dict] = {}
    by_ext: dict[str, dict] = {}
    need_convert = 0
    need_convert_size = 0
    for f in files.values():
        k = by_kind.setdefault(f.kind, {"count": 0, "size": 0})
        k["count"] += 1
        k["size"] += f.size
        x = by_ext.setdefault(f.ext, {"count": 0, "size": 0})
        x["count"] += 1
        x["size"] += f.size
        if f.kind == "video" and f.ext not in ("mp4", "m4v"):
            need_convert += 1
            need_convert_size += f.size
    usage = disk_usage(media_root) or {}
    return {
        "folders": len(dirs),
        "courses": len([d for d in dirs if "/" not in d]),
        "files": len(files),
        "total_size": sum(f.size for f in files.values()),
        "by_kind": by_kind,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1]["size"])),
        "videos_need_convert": need_convert,
        # 預估：換封裝約等於原檔大小、重新編碼通常較小；以原檔大小保守估計 30 天重疊期需要的空間
        "estimated_extra_space": need_convert_size,
        "media_free": usage.get("free"),
        "media_total": usage.get("total"),
        "skipped": len([s for s in skipped if not s.get("warn_only") and not s.get("system")]),
        "system_dirs": [s["path"] for s in skipped if s.get("system")],
    }


def _ts(f: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(f, TZ)


def _mtime_changed(row_mtime, file_mtime: float) -> bool:
    if row_mtime is None:
        return True
    return abs(row_mtime.timestamp() - file_mtime) > 1.0


def apply(ctx: Ctx, dirs: list[str], files: dict[str, FileInfo]) -> tuple[dict, list[tuple[str, dict, int]], dict]:
    """寫入資料庫（呼叫端負責交易）。回傳（統計、要排入的工作、明細）。"""
    conn = ctx.conn
    now = _dt.datetime.now(TZ)
    stats = {"added": 0, "changed": 0, "moved": 0, "missing": 0}
    details: dict[str, list] = {"added": [], "changed": [], "moved": [], "missing": []}
    jobs: list[tuple[str, dict, int]] = []

    # ---- 資料夾 ----
    existing = {r["rel_path"]: r for r in conn.execute("SELECT id, rel_path, parent_id, status FROM folders")}
    folder_id: dict[str, int] = {}
    course_of: dict[str, int] = {}
    for rel in sorted(dirs, key=lambda r: (r.count("/"), natural_key(r))):
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else None
        parent_id = folder_id.get(parent_rel) if parent_rel else None
        top = rel.split("/", 1)[0]
        name = rel.rsplit("/", 1)[-1]
        if rel in existing:
            fid = existing[rel]["id"]
            conn.execute("UPDATE folders SET status = 'present', parent_id = %s, scanned_at = %s, updated_at = NOW() WHERE id = %s",
                         [parent_id, now, fid])
        else:
            fid = conn.val("INSERT INTO folders(parent_id, rel_path, name, is_course, scanned_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                           [parent_id, rel, name, parent_rel is None, now])
        folder_id[rel] = fid
        course_of[rel] = fid if parent_rel is None else course_of[top]
        conn.execute("UPDATE folders SET course_id = %s WHERE id = %s AND course_id IS DISTINCT FROM %s", [course_of[rel], fid, course_of[rel]])
    seen = set(dirs)
    for rel, r in existing.items():
        if rel not in seen and r["status"] != "missing":
            conn.execute("UPDATE folders SET status = 'missing', updated_at = NOW() WHERE id = %s", [r["id"]])

    # ---- 教材 ----
    rows = conn.execute("SELECT id, rel_path, size_bytes, mtime, quick_hash, status, kind, folder_id FROM media")
    by_path = {r["rel_path"]: r for r in rows}
    gone = [r for p, r in by_path.items() if p not in files and r["status"] != "original_deleted"]
    candidates: dict[tuple[int, str], list[dict]] = {}
    for r in gone:
        if r["quick_hash"]:
            candidates.setdefault((r["size_bytes"], r["quick_hash"]), []).append(r)
    relinked: set[int] = set()
    touched_folders: set[int] = set()

    def queue_processing(mid: int, kind: str) -> None:
        if kind == "video":
            conn.execute("UPDATE media SET convert_status = 'pending', convert_error = NULL WHERE id = %s", [mid])
            jobs.append(("probe", {"media_id": mid}, 10))
        elif kind == "office":
            conn.execute("UPDATE media SET doc_status = 'pending', doc_error = NULL WHERE id = %s", [mid])
            jobs.append(("office_pdf", {"media_id": mid}, 5))

    for rel in sorted(files, key=natural_key):
        f = files[rel]
        dir_rel = rel.rsplit("/", 1)[0] if "/" in rel else None
        fid = folder_id.get(dir_rel) if dir_rel else None
        cid = course_of.get(dir_rel) if dir_rel else None
        full = os.path.join(ctx.paths.library, rel)
        row = by_path.get(rel)
        if row:
            if row["kind"] != f.kind:
                continue  # 不可能發生（相同路徑同副檔名）
            changed = row["size_bytes"] != f.size or _mtime_changed(row["mtime"], f.mtime)
            if row["status"] == "missing" and not changed:
                conn.execute("UPDATE media SET status = 'present', folder_id = %s, course_id = %s, updated_at = NOW() WHERE id = %s",
                             [fid, cid, row["id"]])
                continue
            if changed:
                qh = quick_hash(full, f.size)
                conn.execute("UPDATE media SET size_bytes = %s, mtime = %s, quick_hash = %s, status = 'present', folder_id = %s, "
                             "course_id = %s, updated_at = NOW() WHERE id = %s", [f.size, _ts(f.mtime), qh, fid, cid, row["id"]])
                stats["changed"] += 1
                details["changed"].append(rel)
                queue_processing(row["id"], f.kind)
                if fid:
                    touched_folders.add(fid)
            elif row["folder_id"] != fid:
                conn.execute("UPDATE media SET folder_id = %s, course_id = %s WHERE id = %s", [fid, cid, row["id"]])
            continue
        qh = quick_hash(full, f.size)
        cand = None
        for c in candidates.get((f.size, qh), []):
            if c["id"] not in relinked and c["kind"] == f.kind:
                cand = c
                break
        name = rel.rsplit("/", 1)[-1]
        if cand:
            relinked.add(cand["id"])
            conn.execute("UPDATE media SET rel_path = %s, file_name = %s, folder_id = %s, course_id = %s, mtime = %s, status = 'present', "
                         "updated_at = NOW() WHERE id = %s", [rel, name, fid, cid, _ts(f.mtime), cand["id"]])
            stats["moved"] += 1
            details["moved"].append({"id": cand["id"], "from": cand["rel_path"], "to": rel})
            if fid:
                touched_folders.add(fid)
            if cand["folder_id"]:
                touched_folders.add(cand["folder_id"])
            continue
        mid = conn.val(
            "INSERT INTO media(folder_id, course_id, kind, title, file_name, ext, rel_path, size_bytes, mtime, quick_hash, "
            "convert_status, doc_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            [fid, cid, f.kind, os.path.splitext(name)[0], name, f.ext, rel, f.size, _ts(f.mtime), qh,
             "pending" if f.kind == "video" else "none", "pending" if f.kind == "office" else "none"])
        stats["added"] += 1
        details["added"].append(rel)
        queue_processing(mid, f.kind)
        if fid:
            touched_folders.add(fid)

    for r in gone:
        if r["id"] in relinked or r["status"] == "missing":
            continue
        conn.execute("UPDATE media SET status = 'missing', updated_at = NOW() WHERE id = %s", [r["id"]])
        stats["missing"] += 1
        details["missing"].append(r["rel_path"])

    # 合集判定：有變動的資料夾（檢測完成後 probe 也會再判定一次）
    threshold = float(ctx.config("clip_threshold_gib", 1.9) or 1.9)
    for fid in sorted(touched_folders):
        clips.detect(conn, fid, threshold)
    return stats, jobs, details


def relink(ctx: Ctx, media_id: int) -> dict:
    """Super 在教材清單重新指定路徑後：更新資料夾、雜湊並重新檢測。"""
    m = ctx.conn.one("SELECT id, rel_path, kind FROM media WHERE id = %s", [media_id])
    if not m:
        raise JobError("找不到教材")
    full = ctx.paths.original(m["rel_path"])
    st = os.stat(full)
    dir_rel = m["rel_path"].rsplit("/", 1)[0] if "/" in m["rel_path"] else None
    folder = ctx.conn.one("SELECT id, course_id FROM folders WHERE rel_path = %s", [dir_rel]) if dir_rel else None
    with ctx.conn.transaction():
        ctx.conn.execute("UPDATE media SET size_bytes = %s, mtime = %s, quick_hash = %s, status = 'present', folder_id = %s, course_id = %s, "
                         "updated_at = NOW() WHERE id = %s",
                         [st.st_size, _ts(st.st_mtime), quick_hash(full, st.st_size), folder["id"] if folder else None,
                          (folder["course_id"] or folder["id"]) if folder else None, media_id])
    if m["kind"] == "video":
        ctx.conn.execute("UPDATE media SET convert_status = 'pending' WHERE id = %s", [media_id])
        ctx.enqueue("probe", {"media_id": media_id, "force": True}, 20, unique_key={"media_id": media_id})
    elif m["kind"] == "office":
        ctx.enqueue("office_pdf", {"media_id": media_id, "force": True}, 20, unique_key={"media_id": media_id})
    return {"relinked": media_id}


def handle(ctx: Ctx, job: dict) -> dict:
    payload = job.get("payload") or {}
    if payload.get("relink_media_id"):
        return relink(ctx, int(payload["relink_media_id"]))
    ctx.require_media_marker()
    dry = bool(payload.get("dry_run"))
    patterns = [p for p in (ctx.config("exclude_patterns", []) or []) if isinstance(p, str)]
    run_id = ctx.conn.val("INSERT INTO scan_runs(job_id, dry_run) VALUES (%s, %s) RETURNING id", [job["id"], dry])
    try:
        dirs, files, skipped = walk(ctx.paths.library, patterns)
        summary = summarize(dirs, files, skipped, ctx.paths.media_root)
        if dry:
            ctx.conn.execute("UPDATE scan_runs SET status = 'done', finished_at = NOW(), summary = CAST(%s AS jsonb), "
                             "skipped = CAST(%s AS jsonb) WHERE id = %s", [_json(summary), _json(skipped[:2000]), run_id])
            return {"scan_run_id": run_id, "dry_run": True, "summary": summary}
        with ctx.conn.transaction():
            stats, jobs, details = apply(ctx, dirs, files)
        for jtype, payload2, prio in jobs:
            ctx.enqueue(jtype, payload2, prio, unique_key={"media_id": payload2["media_id"]})
        ctx.conn.execute(
            "UPDATE scan_runs SET status = 'done', finished_at = NOW(), added = %s, changed = %s, moved = %s, missing = %s, "
            "skipped = CAST(%s AS jsonb), summary = CAST(%s AS jsonb), details = CAST(%s AS jsonb) WHERE id = %s",
            [stats["added"], stats["changed"], stats["moved"], stats["missing"], _json(skipped[:2000]), _json(summary),
             _json({k: v[:2000] for k, v in details.items()}), run_id])
        return {"scan_run_id": run_id, **stats, "jobs": len(jobs)}
    except Exception as e:
        ctx.conn.execute("UPDATE scan_runs SET status = 'failed', finished_at = NOW(), error = %s WHERE id = %s", [str(e)[:2000], run_id])
        raise


def _json(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False, default=str)
