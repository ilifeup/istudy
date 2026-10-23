"""iStudy Server Agent（第 15 章）：讀取 PostgreSQL jobs 表執行固定類型的系統工作。

- 以 SELECT … FOR UPDATE SKIP LOCKED 取工作；只執行固定的工作類型，不接受任意指令。
- 喚醒（wol_wake）走獨立的快速通道，每 2 秒檢查一次，不被長時間的轉檔擋住。
- 每 30 秒寫一次自己的心跳（server_config.agent_heartbeat），Server App 顯示 Agent 是否在運作。
- 轉檔與字幕只在 Super 設定的時段內開始（預設 22:30～03:00）；字幕在轉檔佇列空了之後才開始；
  時段結束時不再開始新的一支，進行中的那一支做完為止；「立即執行」不受時段限制。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import signal
import threading
import time
import traceback
from typing import Callable, Optional

from istudy_common import db as dbmod
from istudy_common.util import in_window, now_tz, parse_hhmm

from . import asr, mailer, maintenance, net, office, outgo, purge, scan, subtitles, video, wol
from .context import Ctx, JobError, Retry, agent_version, make_ctx, setup_logging
from .media_tools import ENCODER_ORDER, probe_encoders

log = logging.getLogger("istudy.agent")

HANDLERS: dict[str, Callable[[Ctx, dict], dict]] = {
    "scan": scan.handle,
    "probe": video.probe,
    "convert": video.convert,
    "verify": video.verify,
    "delete_original": video.delete_original,
    "asr": subtitles.recognize,          # 字幕自動辨識（引擎見 worker/asr；dev.58 前叫 whisper）
    "subtitle_import": subtitles.import_subtitle,
    "office_pdf": office.office_pdf,
    "wol_wake": wol.wol_wake,
    "diagnose": maintenance.diagnose,
    "backup": maintenance.backup,
    "backup_check": maintenance.backup_check,
    "cleanup": maintenance.cleanup,
    "net_probe": net.net_probe,
    "lan_scan": net.lan_scan,
    "email_report": mailer.email_report,
    "purge_media": purge.purge_media,    # dev.61：Super 永久刪除教材
    "outgo_asr": outgo.recognize,        # dev.61：/Media/Outgo 錄音轉文字（不寫教材資料表）
}
TRANSIENT_TYPES = ("outgo_asr",)         # 結果與狀態另外保存：工作結束後刪除 jobs 這一筆
NET_TYPES = ["net_probe", "lan_scan", "email_report"]  # 網路檢測與寄信走獨立通道，不被長時間轉檔擋住
MAIN_TYPES = [t for t in HANDLERS if t != "wol_wake" and t not in NET_TYPES]
MAX_ATTEMPTS = 3

PICK_SQL = """
UPDATE jobs SET status = 'running', started_at = NOW(), attempts = attempts + 1, error = NULL
 WHERE id = (
   SELECT id FROM jobs
    WHERE status = 'queued' AND run_after <= NOW() AND type = ANY(CAST(%s AS text[]))
      AND (type <> 'convert' OR COALESCE((payload->>'immediate')::boolean, FALSE) OR %s)
      AND (type <> 'asr' OR COALESCE((payload->>'immediate')::boolean, FALSE)
           OR (%s AND (NOT %s OR NOT EXISTS (SELECT 1 FROM jobs c WHERE c.type = 'convert' AND c.status IN ('queued','running')))))
    ORDER BY priority DESC, id
    LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING *"""


def start_note(job: dict) -> str:
    """Log 補充：字幕辨識寫出「要求的引擎」（auto＝依設定自動選）。"""
    if job.get("type") != "asr":
        return ""
    return f" engine={(job.get('payload') or {}).get('engine') or 'auto'}"


def result_note(result: dict) -> str:
    """Log 補充：有引擎資訊的結果寫出「實際用的引擎」與速度、品質。"""
    keys = ("engine", "model", "rtf", "qc", "segments", "method", "envelope")
    return "".join(f" {k}={result[k]}" for k in keys if isinstance(result, dict) and result.get(k) not in (None, ""))


def warn_unknown_types(ctx: Ctx) -> list[dict]:
    """排隊中、但這版 Agent 不認得的工作類型（例如改名後漏改的程式）：寫進 Log 與系統 Log，不會悄悄卡住。"""
    rows = ctx.conn.execute("SELECT id, type FROM jobs WHERE status = 'queued' AND NOT (type = ANY(CAST(%s AS text[]))) "
                            "ORDER BY id LIMIT 20", [list(HANDLERS)])
    for r in rows:
        log.warning("job %s type=%s：這版 Agent 沒有對應的處理程式，工作不會執行", r["id"], r["type"])
    if rows:
        ctx.syslog("warn", "有 Agent 不認得的工作類型在排隊：" + "、".join(f"#{r['id']} {r['type']}" for r in rows))
    return rows


def gates(ctx: Ctx, now: Optional[_dt.datetime] = None) -> tuple[bool, bool, bool]:
    """回傳（可開始轉檔、可開始字幕、字幕需等轉檔佇列清空）。"""
    s = ctx.schedule()
    window = in_window(s["start"], s["end"], now)
    paused = bool(ctx.config("conversion_paused", False))
    convert_ok = window and s["auto_convert"] and not paused
    asr_ok = window and s["auto_whisper"]          # 設定鍵名沿用 auto_whisper：「時段內自動產生字幕」
    wait_convert = s["whisper_after_convert"] and s["auto_convert"] and not paused
    return convert_ok, asr_ok, wait_convert


def pick(ctx: Ctx, types: list[str], now: Optional[_dt.datetime] = None) -> Optional[dict]:
    convert_ok, asr_ok, wait_convert = gates(ctx, now)
    return ctx.conn.one(PICK_SQL, [types, convert_ok, asr_ok, wait_convert])


def run_job(ctx: Ctx, job: dict) -> str:
    """執行一個工作並寫回結果。回傳最後狀態。"""
    handler = HANDLERS.get(job["type"])
    ctx.current_job = {"id": job["id"], "type": job["type"], "media_id": (job.get("payload") or {}).get("media_id"),
                       "started_at": now_tz().isoformat()}
    try:
        if handler is None:
            raise JobError(f"不支援的工作類型：{job['type']}")
        result = handler(ctx, job) or {}
        ctx.conn.execute("UPDATE jobs SET status = 'done', finished_at = NOW(), "
                         "result = COALESCE(result, '{}'::jsonb) || CAST(%s AS jsonb) WHERE id = %s",
                         [json.dumps(result, ensure_ascii=False, default=str), job["id"]])
        log.info("job %s %s done%s", job["id"], job["type"], result_note(result))
        _forget(ctx, job)
        return "done"
    except Retry as r:
        ctx.conn.execute("UPDATE jobs SET status = 'queued', attempts = GREATEST(attempts - 1, 0), run_after = NOW() + make_interval(secs => %s), "
                         "error = %s WHERE id = %s", [r.delay_sec, str(r), job["id"]])
        log.info("job %s retry later: %s", job["id"], r)
        return "queued"
    except JobError as e:
        _fail(ctx, job, str(e))
        _forget(ctx, job)
        return "failed"
    except dbmod.DBError as e:
        log.error("job %s DB error: %s", job["id"], e)
        _fail(ctx, job, "資料庫錯誤：" + str(e)[:300])
        return "failed"
    except Exception as e:  # noqa: BLE001 — 任何未預期錯誤都要記錄並讓 Agent 繼續運作
        log.error("job %s crashed: %s\n%s", job["id"], e, traceback.format_exc())
        _fail(ctx, job, f"內部錯誤：{type(e).__name__}: {e}"[:500])
        _forget(ctx, job)
        return "failed"
    finally:
        ctx.current_job = None


def _forget(ctx: Ctx, job: dict) -> None:
    """短期工作（Outgo 錄音轉文字）：結果已寫在 Outgo 的狀態檔，工作結束後刪除 jobs 這一筆。"""
    if job.get("type") in TRANSIENT_TYPES:
        try:
            ctx.conn.execute("DELETE FROM jobs WHERE id = %s AND status IN ('done','failed')", [job["id"]])
        except dbmod.DBError:
            pass


def _fail(ctx: Ctx, job: dict, message: str) -> None:
    try:
        if not ctx.conn.ok():
            ctx.conn = dbmod.connect()
        log.info("job %s %s failed%s: %s", job["id"], job["type"], start_note(job), message)
        ctx.conn.execute("UPDATE jobs SET status = 'failed', finished_at = NOW(), error = %s WHERE id = %s", [message, job["id"]])
        ctx.syslog("warn", f"工作失敗 #{job['id']} {job['type']}：{message}", job_id=job["id"], type=job["type"])
    except dbmod.DBError:
        log.error("cannot record failure for job %s", job["id"])


def recover(ctx: Ctx) -> dict:
    """啟動時處理中斷的工作：重新排入，並清除轉檔暫存檔（停電、重開機）。"""
    c = ctx.conn
    # dev.62：只處理 Agent 自己的工作類型；主機 istudyctl 執行的備份工作（backup_verify、backup_upload…）由主機自己收尾
    mine = list(HANDLERS)
    requeued = c.execute("UPDATE jobs SET status = 'queued', error = '中斷後重新排入', run_after = NOW() "
                         "WHERE status = 'running' AND type <> 'scan' AND type = ANY(CAST(%s AS text[])) AND attempts < %s RETURNING id",
                         [mine, MAX_ATTEMPTS])
    failed = c.execute("UPDATE jobs SET status = 'failed', finished_at = NOW(), error = '中斷（Agent 重新啟動）' "
                       "WHERE status = 'running' AND type = ANY(CAST(%s AS text[])) RETURNING id", [mine])
    c.execute("UPDATE media SET convert_status = 'queued' WHERE convert_status IN ('converting','verifying')")
    c.execute("UPDATE media SET convert_status = 'pending' WHERE convert_status = 'probing'")
    c.execute("UPDATE media SET doc_status = 'pending' WHERE doc_status = 'converting'")
    c.execute("UPDATE scan_runs SET status = 'failed', finished_at = NOW(), error = '中斷（Agent 重新啟動）' WHERE status = 'running'")
    purged = purge.recover_unfinished(ctx)            # dev.62：先處理沒完成的永久刪除（改回或刪掉暫存檔）
    removed = video.cleanup_temp(ctx) + purge.cleanup_staged(ctx)
    outgo_ok = ensure_outgo(ctx)
    return {"requeued": len(requeued), "failed": len(failed), "temp_removed": removed, "outgo": outgo_ok, "purge_recovered": purged}


def ensure_outgo(ctx: Ctx) -> bool:
    """dev.61：檢查 /Media/Outgo。由部署（patch_dev61.sh、istudyctl bootstrap／media-acl --fix）以 root 建立，
    擁有者與 /Media 相同（放錄音的人可以寫入）、Agent 用 ACL 寫入；Agent 自己不建立（建出來會是 uid 10001 的，使用者放不進檔案）。"""
    path = ctx.paths.outgo
    if os.path.islink(path):
        ctx.syslog("warn", f"{path} 是符號連結：Outgo 停用（請改成一般資料夾）")
        return False
    if not os.path.isdir(path):
        ctx.syslog("warn", f"找不到 {path}：Outgo（錄音轉文字）停用；請執行 sudo istudyctl media-acl --fix 建立")
        return False
    return True


# ---------- 排程（每日備份、清理、字幕補排） ----------

def scheduler_tick(ctx: Ctx, now: Optional[_dt.datetime] = None) -> list[str]:
    now = now or now_tz()
    today = now.date().isoformat()
    state = ctx.config("scheduler_state", {}) or {}
    s = ctx.schedule()
    created = []
    if now.time() >= parse_hhmm(s["backup_time"]) and state.get("backup_date") != today:
        ctx.enqueue("backup", {"kind": "daily"}, 50, unique_key={"kind": "daily"})
        state["backup_date"] = today
        created.append("backup")
    if now.time() >= _dt.time(4, 0) and state.get("cleanup_date") != today:
        ctx.enqueue("cleanup", {}, 40, unique_key={})
        state["cleanup_date"] = today
        created.append("cleanup")
    if s["auto_whisper"] and in_window(s["start"], s["end"], now) and state.get("asr_catchup_date") != today:
        rows = ctx.conn.execute(
            "SELECT m.id FROM media m WHERE m.kind = 'video' AND m.status <> 'missing' AND (m.play_mode = 'direct' OR m.convert_status = 'done') "
            "AND NOT EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) "
            "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.type = 'asr' AND (j.payload->>'media_id')::bigint = m.id "
            "AND (j.status IN ('queued','running') OR (j.status = 'failed' AND j.finished_at > NOW() - INTERVAL '1 day')))")
        for r in rows:
            ctx.enqueue("asr", {"media_id": r["id"]}, 0)
        state["asr_catchup_date"] = today
        if rows:
            created.append(f"asr×{len(rows)}")
    if created:
        ctx.set_config("scheduler_state", state)
    return created


# ---------- 執行緒：心跳、喚醒快速通道 ----------

class Agent:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.stop = threading.Event()

    def _asr_status(self, conn) -> dict:
        """dev.57：每個字幕辨識引擎是否可用（只檢查套件與模型檔，不載入模型）。"""
        try:
            return asr.engines.status_all(self.ctx.env.get("ISTUDY_MODELS_DIR", "/models"))
        except Exception as e:  # noqa: BLE001 — 心跳不能因為這一項失敗
            return {"error": str(e)[:200]}

    def heartbeat_loop(self, interval: float = 30.0) -> None:
        conn = None
        while not self.stop.is_set():
            try:
                conn = conn if conn and conn.ok() else dbmod.connect()
                hb = {"at": now_tz().isoformat(), "version": agent_version(), "pid": os.getpid(),
                      "current_job": self.ctx.current_job, "flags": sorted(self.ctx.flags), "encoder": self.ctx.encoder,
                      "encoders": self.ctx.encoders, "lan": net.lan_info(), "asr": self._asr_status(conn)}
                conn.execute("INSERT INTO server_config(key, value, updated_at) VALUES ('agent_heartbeat', CAST(%s AS jsonb), NOW()) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                             [json.dumps(hb, ensure_ascii=False, default=str)])
            except dbmod.DBError as e:
                log.warning("heartbeat failed: %s", e)
                conn = None
            self.stop.wait(interval)

    def wake_loop(self, interval: float = 2.0) -> None:
        conn = None
        while not self.stop.is_set():
            try:
                conn = conn if conn and conn.ok() else dbmod.connect()
                lane = Ctx(conn=conn, paths=self.ctx.paths, env=self.ctx.env)
                while True:
                    job = lane.conn.one(PICK_SQL, [["wol_wake"], False, False, False])
                    if not job:
                        break
                    run_job(lane, job)
            except dbmod.DBError as e:
                log.warning("wake lane failed: %s", e)
                conn = None
            self.stop.wait(interval)

    def net_loop(self, interval: float = 2.0) -> None:
        """Server 主動 ping 與 LAN 掃描：只在有人按下（或開啟 WOL 頁）時才有工作，平常只查一下 jobs 表。"""
        conn = None
        while not self.stop.is_set():
            try:
                conn = conn if conn and conn.ok() else dbmod.connect()
                lane = Ctx(conn=conn, paths=self.ctx.paths, env=self.ctx.env)
                while True:
                    job = lane.conn.one(PICK_SQL, [NET_TYPES, False, False, False])
                    if not job:
                        break
                    run_job(lane, job)
            except dbmod.DBError as e:
                log.warning("net lane failed: %s", e)
                conn = None
            self.stop.wait(interval)

    def run(self, idle_sleep: float = 3.0) -> None:
        threading.Thread(target=self.heartbeat_loop, name="heartbeat", daemon=True).start()
        threading.Thread(target=self.wake_loop, name="wol", daemon=True).start()
        threading.Thread(target=self.net_loop, name="net", daemon=True).start()
        last_tick = 0.0
        while not self.stop.is_set():
            try:
                if not self.ctx.conn.ok():
                    self.ctx.conn = dbmod.connect()
                if time.time() - last_tick >= 60:
                    scheduler_tick(self.ctx)
                    warn_unknown_types(self.ctx)
                    last_tick = time.time()
                job = pick(self.ctx, MAIN_TYPES)
                if job:
                    log.info("job %s %s start%s", job["id"], job["type"], start_note(job))
                    run_job(self.ctx, job)      # 完成／失敗的 Log 由 run_job 記（含實際引擎）
                    continue
            except dbmod.DBError as e:
                log.warning("main loop DB error: %s", e)
                try:
                    self.ctx.conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self.stop.wait(5)
                try:
                    self.ctx.conn = dbmod.connect()
                except dbmod.DBError:
                    continue
            self.stop.wait(idle_sleep)


LABEL = {"qsv": "Quick Sync", "vaapi": "VA-API", "cpu": "CPU"}


def choose_encoder(tests: dict, forced: str = "auto") -> tuple[str, str]:
    """dev.55 HW-01：依 QSV → VA-API → CPU 選第一個實測可用的；回傳（編碼器、未採用較高層級的原因）。"""
    if forced in ENCODER_ORDER:
        return forced, "設定指定"
    skipped = []
    for e in ENCODER_ORDER:
        r = tests.get(e) or {}
        if r.get("ok"):
            return e, "；".join(skipped)
        skipped.append(f"{LABEL[e]}：{r.get('message') or '未通過'}")
    return "cpu", "；".join(skipped)


def detect_encoder(env: dict) -> tuple[str, str, dict]:
    tests = probe_encoders()
    enc, why = choose_encoder(tests, env.get("ISTUDY_ENCODER", "auto"))
    return enc, why, tests


def main() -> None:
    setup_logging()
    while True:
        try:
            ctx = make_ctx()
            break
        except dbmod.DBError as e:
            log.warning("waiting for database: %s", e)
            time.sleep(5)
    info = recover(ctx)
    ctx.encoder, why, ctx.encoders = detect_encoder(ctx.env)
    ctx.syslog("warn" if ctx.encoder == "cpu" else "info",
               f"Agent 啟動（{agent_version()}），編碼器 {LABEL.get(ctx.encoder, ctx.encoder)}{('（' + why + '）') if why else ''}",
               **info, encoders=ctx.encoders)
    ok, msg = ctx.media_marker_ok()
    if not ok:
        ctx.syslog("warn", "媒體標記檢查未通過：" + msg)
    agent = Agent(ctx)
    signal.signal(signal.SIGTERM, lambda *_: agent.stop.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop.set())
    agent.run()


if __name__ == "__main__":
    main()
