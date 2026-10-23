"""Super 永久刪除教材（工作類型 purge_media）：原檔、衍生檔（播放用 MP4、字幕 VTT、文件 PDF）與資料庫紀錄。

dev.61 起分三步（檔案系統與資料庫不能一起交易）：
  1. 把要刪的檔案在「同一個資料夾內」改名成隱藏的暫存名稱（.istudy-purge-<教材ID>-<序號>），可以完整改回。
  2. 在一個資料庫交易裡刪除教材紀錄與關聯（字幕、段落、書籤、學習歷程 ON DELETE CASCADE；工作、使用記錄、ASR 處理記錄、系統 Log 的教材 ID）。
  3. 刪掉暫存檔。

dev.62 修正（實機：TXT 顯示「Operation not permitted；已復原」，檔案卻看不到）：
  - 先檢查：改名前確認 Agent 能改每一個檔案（資料夾寫入權限、sticky 位元）；不行就停止，什麼都不動。
    預先檢查不能保證成功（權限可能在中間改變），所以後面每一步仍然照實處理。
  - 衍生檔只處理「資料庫記錄屬於這個教材」的檔案；同 ID 但不屬於它的舊檔（例如重新安裝前留下的）不動，只列出。
  - 結果照實際狀態寫：失敗後逐一確認每個檔案在原位置、在暫存名稱或找不到；只有確認全部在原位置，才寫「已復原」。
  - 以資料庫交易為準：交易沒提交＝失敗、教材保留；交易已提交＝成功，暫存檔刪不掉只列為「待清理」，不算失敗。
  - 計畫與每個檔案的狀態寫在工作結果（result.items）。同一教材的下一次刪除、或 Agent 重新啟動時，
    會接著處理上次留下的暫存檔（課程資料夾裡的也會處理）：教材還在 → 改回原名；教材已刪 → 刪掉暫存檔。
"""
from __future__ import annotations

import glob
import os
import stat
from typing import Optional

from .context import Ctx, JobError

STAGE_PREFIX = ".istudy-purge-"


def _staged_name(path: str, media_id: int, i: int) -> str:
    return os.path.join(os.path.dirname(path), f"{STAGE_PREFIX}{media_id}-{i}")


def _regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _euid() -> int:
    return os.geteuid()


def can_rename(path: str) -> Optional[str]:
    """改名（刪除）這個檔案會不會被拒絕；回傳原因（None＝可以）。只讀檢查，不改任何東西。"""
    d = os.path.dirname(path)
    if not os.access(d, os.W_OK | os.X_OK):
        return f"Agent（uid {_euid()}）不能寫入資料夾 {d}（權限或 ACL 不足）"
    try:
        dst, fst = os.stat(d), os.lstat(path)
    except OSError as e:
        return f"無法讀取 {path}（{e.strerror}）"
    euid = _euid()
    if dst.st_mode & stat.S_ISVTX and euid not in (0, fst.st_uid, dst.st_uid):
        return f"資料夾 {d} 有 sticky 位元，Agent（uid {euid}）不能改名別人（uid {fst.st_uid}）的檔案"
    return None


def where(it: dict) -> str:
    """一個檔案目前的位置：original（原位置）、staged（暫存名稱）、both（兩個都在，異常）、gone（都不在）。"""
    a, b = os.path.lexists(it["path"]), os.path.lexists(it["staged"])
    return "both" if a and b else "original" if a else "staged" if b else "gone"


def plan(ctx: Ctx, m: dict) -> tuple[list[dict], list[str]]:
    """要刪除的檔案清單與備註。原檔路徑不安全或是符號連結時丟出 JobError（不刪任何東西）。"""
    mid = int(m["id"])
    items, notes = [], []
    if m["status"] != "original_deleted":
        try:
            ctx.paths.original(m["rel_path"])          # 格式、系統資料夾、跳出 /Media
        except ValueError as e:
            raise JobError(f"教材路徑不安全，停止：{e}") from e
        raw = os.path.join(ctx.paths.library, *m["rel_path"].split("/"))
        if os.path.lexists(raw):
            if os.path.realpath(raw) != os.path.join(os.path.realpath(ctx.paths.library), *m["rel_path"].split("/")):
                raise JobError("原檔或它所在的資料夾是符號連結：不刪除，請先確認")
            if not _regular_file(raw):
                raise JobError("原檔不是一般檔案（可能是資料夾或特殊檔案）：不刪除")
            items.append({"label": "原檔", "path": raw})
        else:
            notes.append("原檔已不存在")
    # dev.62：只處理資料庫記錄屬於這個教材的衍生檔（同 ID 的舊檔不動）
    sub = ctx.conn.val("SELECT path FROM subtitles WHERE media_id = %s", [mid])
    owned = [
        ("播放用 MP4", ctx.paths.playback_file(mid), m.get("playback_path") == f"{mid}.mp4",
         "SELECT count(*) FROM media WHERE id <> %s AND playback_path = %s"),
        ("字幕檔", ctx.paths.subtitle_file(mid), sub == f"{mid}.vtt", "SELECT count(*) FROM subtitles WHERE media_id <> %s AND path = %s"),
        ("文件 PDF", ctx.paths.doc_file(mid), m.get("doc_pdf_path") == f"{mid}.pdf",
         "SELECT count(*) FROM media WHERE id <> %s AND doc_pdf_path = %s"),
    ]
    for label, path, mine, shared_sql in owned:
        if not os.path.lexists(path):
            continue
        if not mine:
            notes.append(f"{os.path.basename(path)} 是同 ID 的舊檔（資料庫沒有記錄屬於這個教材）：不處理")
            continue
        if not _regular_file(path) or os.path.realpath(path) != path:
            notes.append(f"{label} 不是一般檔案或是符號連結：保留 {os.path.basename(path)}")
            continue
        if int(ctx.conn.val(shared_sql, [mid, os.path.basename(path)]) or 0):
            notes.append(f"{label} 也被其他教材使用：保留")
            continue
        items.append({"label": label, "path": path})
    for i, it in enumerate(items):
        it["staged"] = _staged_name(it["path"], mid, i)
    return items, notes


def _restore(items: list[dict]) -> None:
    """把在暫存名稱的檔案改回原名（失敗的留給 where() 照實回報）。"""
    for it in items:
        if where(it) == "staged":
            try:
                os.rename(it["staged"], it["path"])
            except OSError:
                pass


def _states(items: list[dict]) -> list[dict]:
    return [{"label": it["label"], "path": it["path"], "staged": it["staged"], "state": where(it)} for it in items]


def _db_purge(ctx: Ctx, m: dict, job: dict, deleted: list[str], notes: list[str]) -> None:
    mid = int(m["id"])
    c = ctx.conn
    with c.transaction():
        c.execute("UPDATE jobs SET status = 'cancelled', finished_at = NOW(), error = '教材已永久刪除' "
                  "WHERE status = 'queued' AND id <> %s AND (payload->>'media_id' = %s OR payload->>'relink_media_id' = %s)",
                  [job["id"], str(mid), str(mid)])
        # 這個教材的舊工作紀錄刪除；但其他「永久刪除」工作保留（裡面記著暫存檔的位置，Agent 重啟時要用）
        c.execute("DELETE FROM jobs WHERE id <> %s AND type <> 'purge_media' AND status IN ('done','failed','cancelled') "
                  "AND (payload->>'media_id' = %s OR payload->>'relink_media_id' = %s)", [job["id"], str(mid), str(mid)])
        c.execute("UPDATE audit_log SET media_id = NULL WHERE media_id = %s", [mid])
        c.execute("UPDATE asr_log SET media_id = NULL WHERE media_id = %s", [mid])      # ASR 處理記錄保留（路徑、品質、結果）
        c.execute("UPDATE system_logs SET context = context - 'media_id' WHERE context->>'media_id' = %s", [str(mid)])
        c.execute("DELETE FROM media WHERE id = %s", [mid])   # 字幕、字幕段落、書籤、學習歷程：ON DELETE CASCADE
        if m.get("clip_group_id"):
            c.execute("DELETE FROM clip_groups g WHERE g.id = %s AND NOT EXISTS (SELECT 1 FROM media x WHERE x.clip_group_id = g.id)",
                      [m["clip_group_id"]])
        ctx.audit("media.purged", {"media_id_removed": mid, "title": m["title"], "kind": m["kind"], "deleted": deleted,
                                   "notes": notes, "job_id": job["id"]}, None, m["rel_path"], actor_id=job.get("created_by"))


def _leftovers(ctx: Ctx, mid: int, exclude_job: Optional[int] = None) -> list[dict]:
    """之前的永久刪除工作留下、仍在暫存名稱的檔案（原位置已沒有檔案）。"""
    out = []
    for r in ctx.conn.execute("SELECT id, result FROM jobs WHERE type = 'purge_media' AND payload->>'media_id' = %s "
                              "AND id <> %s ORDER BY id DESC LIMIT 5", [str(mid), exclude_job or 0]):
        res = r["result"] or {}
        for it in (res.get("items") or res.get("plan") or []):
            if isinstance(it, dict) and it.get("path") and it.get("staged") and where(it) == "staged":
                if it["path"] not in {x["path"] for x in out}:
                    out.append({"label": it.get("label", "檔案"), "path": it["path"], "staged": it["staged"]})
    return out


def _fail(ctx: Ctx, job: dict, items: list[dict], what: str, why: str) -> None:
    """失敗：改回已改名的檔案，再照「實際狀態」寫結果與訊息。"""
    _restore(items)
    states = _states(items)
    ctx.progress(job["id"], {"items": states, "committed": False})
    stuck = [s for s in states if s["state"] != "original"]
    if not stuck:
        raise JobError(f"{what}（{why}）。沒有刪除任何東西：已確認所有檔案都在原位置，教材資料庫紀錄保留")
    lines = "；".join(f"{s['label']} 應在 {s['path']}，目前" + ("在暫存名稱 " + s["staged"] if s["state"] == "staged"
                                                             else "原位置與暫存名稱都有" if s["state"] == "both" else "兩處都找不到")
                     for s in stuck)
    raise JobError(f"{what}（{why}）。⚠ 沒有完成復原：{lines}。教材資料庫紀錄沒有刪除；"
                   "可以再按一次「永久刪除」接著完成，或 Agent 重新啟動時會自動改回原名")


def purge_media(ctx: Ctx, job: dict) -> dict:
    p = job.get("payload") or {}
    mid = int(p["media_id"])
    ctx.require_media_marker()
    prev = (job.get("result") or {}).get("items") or (job.get("result") or {}).get("plan")   # Agent 中途重啟：沿用這個工作的計畫
    m = ctx.conn.one("SELECT * FROM media WHERE id = %s", [mid])
    if m is None:
        left = _unlink_staged(prev or [])
        return {"purged": mid, "already_removed": True, "left": left, "items": _states(prev or [])}
    if p.get("rel_path") and m["rel_path"] != p["rel_path"]:
        raise JobError("教材路徑已改變（移動或改名），沒有刪除任何東西；請重新整理後再操作")
    busy = ctx.conn.val("SELECT count(*) FROM jobs WHERE status = 'running' AND id <> %s AND type <> 'purge_media' "
                        "AND (payload->>'media_id' = %s OR payload->>'relink_media_id' = %s)", [job["id"], str(mid), str(mid)])
    if int(busy or 0):
        raise JobError("這個教材有工作正在執行（轉檔或字幕辨識），沒有刪除任何東西；請等它完成後再刪")
    items, notes = plan(ctx, m)
    # 這個工作或之前的工作已改成暫存名稱的檔案：接著處理（原位置沒有檔案，所以 plan() 沒列）
    carried = [x for x in (prev or []) if isinstance(x, dict) and where(x) == "staged"] + _leftovers(ctx, mid, job["id"])
    known = {it["path"] for it in items}
    for x in carried:
        if x["path"] not in known:
            items.append({"label": x.get("label", "檔案"), "path": x["path"], "staged": x["staged"]})
            known.add(x["path"])
            if "原檔已不存在" in notes and x.get("label") == "原檔":
                notes.remove("原檔已不存在")
    # 先檢查（只讀）：任何一個不能改名就停止，什麼都不動
    for it in items:
        if where(it) == "original":
            if os.path.lexists(it["staged"]):
                raise JobError(f"沒有刪除任何東西：暫存名稱 {it['staged']} 已存在（可能是上次留下的），請先確認")
            why = can_rename(it["path"])
            if why:
                ctx.progress(job["id"], {"items": _states(items), "committed": False})
                raise JobError(f"沒有刪除任何東西：{it['label']} 無法刪除——{why}。"
                               "請執行 sudo istudyctl media-acl --fix 後再試（不需要 chmod 777）")
    ctx.progress(job["id"], {"items": _states(items), "plan": items, "committed": False})
    for it in items:
        st = where(it)
        if st == "staged":
            continue                              # 之前已改名
        if st != "original":
            _fail(ctx, job, items, f"{it['label']} 狀態異常", "原位置與暫存名稱" + ("都有檔案" if st == "both" else "都找不到"))
        try:
            os.rename(it["path"], it["staged"])
        except OSError as e:
            _fail(ctx, job, items, f"無法處理{it['label']} {it['path']}", e.strerror or str(e))
    try:
        _db_purge(ctx, m, job, [it["label"] for it in items], notes)
    except Exception as e:  # noqa: BLE001
        _fail(ctx, job, items, "資料庫刪除失敗", str(e)[:200])
    # 交易已提交：教材已刪除。暫存檔刪不掉只列為待清理（Agent 重新啟動時會再刪）
    left = _unlink_staged(items)
    states = _states(items)
    ctx.progress(job["id"], {"items": states, "committed": True})
    return {"purged": mid, "title": m["title"], "rel_path": m["rel_path"], "deleted": [it["label"] for it in items],
            "notes": notes, "left": left, "items": states, "committed": True}


def _unlink_staged(items: list[dict]) -> list[str]:
    left = []
    for it in items:
        try:
            if os.path.lexists(it["staged"]):
                os.remove(it["staged"])
        except OSError as e:
            left.append(f"{it['staged']}（{e.strerror}）")
    return left


def recover_unfinished(ctx: Ctx) -> dict:
    """Agent 啟動時：處理之前永久刪除留下的暫存檔（含課程資料夾）。
    教材紀錄已刪除（交易已提交）→ 刪掉暫存檔；教材紀錄還在（沒有提交）→ 改回原名。只看最近 60 天的永久刪除工作。"""
    out = {"restored": 0, "removed": 0, "left": 0}
    rows = ctx.conn.execute("SELECT id, payload, result FROM jobs WHERE type = 'purge_media' AND status IN ('done','failed','cancelled') "
                            "AND created_at > NOW() - INTERVAL '60 days' ORDER BY id")
    for r in rows:
        res = r["result"] or {}
        items = [x for x in (res.get("items") or res.get("plan") or []) if isinstance(x, dict) and x.get("staged") and x.get("path")]
        pending = [x for x in items if where(x) == "staged"]
        if not pending:
            continue
        mid = (r["payload"] or {}).get("media_id")
        exists = mid is not None and ctx.conn.val("SELECT 1 FROM media WHERE id = %s", [int(mid)])
        for x in pending:
            try:
                if exists:
                    os.rename(x["staged"], x["path"])
                    out["restored"] += 1
                else:
                    os.remove(x["staged"])
                    out["removed"] += 1
            except OSError:
                out["left"] += 1
        ctx.progress(r["id"], {"items": _states(items)})
        ctx.syslog("warn" if out["left"] else "info",
                   f"永久刪除工作 #{r['id']} 留下的暫存檔：" + ("已改回原名（教材紀錄還在）" if exists else "已刪除（教材已刪除）")
                   + (f"；{out['left']} 個處理不了，請人工確認" if out["left"] else ""), job_id=r["id"])
    return out


def cleanup_staged(ctx: Ctx) -> int:
    """Agent 啟動時（在 recover_unfinished 之後）：刪除衍生資料夾裡、教材已不存在的暫存檔
    （只看 playback、subtitles、doc-cache，以隱藏的固定前綴辨識）。dev.62：教材還在的不刪（那是沒完成的刪除，要改回）。"""
    n = 0
    for d in (ctx.paths.playback, ctx.paths.subtitles, ctx.paths.doc_cache):
        for f in glob.glob(os.path.join(glob.escape(d), STAGE_PREFIX + "*")):
            try:
                mid = int(os.path.basename(f)[len(STAGE_PREFIX):].split("-")[0])
            except ValueError:
                continue
            try:
                if _regular_file(f) and not ctx.conn.val("SELECT 1 FROM media WHERE id = %s", [mid]):
                    os.remove(f)
                    n += 1
            except OSError:
                pass
    return n
