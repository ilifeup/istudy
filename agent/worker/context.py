"""Agent 執行環境：資料庫、路徑、設定、工作佇列與記錄。"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from istudy_common import db as dbmod
from istudy_common.util import Paths, paths_from_env

AGENT_VERSION_FILE = os.environ.get("ISTUDY_VERSION_FILE", "/opt/istudy/VERSION.json")

log = logging.getLogger("istudy.agent")


class JobError(Exception):
    """工作失敗；訊息會顯示在 Server App（白話說明）。"""


class Retry(Exception):
    """暫時無法執行（例如空間不足），稍後重新排入，不算失敗。"""

    def __init__(self, message: str, delay_sec: int = 1800, flag: Optional[str] = None):
        super().__init__(message)
        self.delay_sec = delay_sec
        self.flag = flag


def agent_version() -> str:
    try:
        with open(AGENT_VERSION_FILE, encoding="utf-8") as f:
            return str(json.load(f).get("server", "0.0.0"))
    except (OSError, ValueError):
        return "0.0.0"


@dataclass
class Ctx:
    conn: dbmod.Connection
    paths: Paths
    env: dict = field(default_factory=lambda: dict(os.environ))
    flags: set = field(default_factory=set)
    encoder: Optional[str] = None  # qsv / vaapi / cpu（啟動時檢測的最佳可用編碼器）
    encoders: dict = field(default_factory=dict)  # dev.55：三層各自的檢測結果 {qsv|vaapi|cpu: {ok, message, stderr}}
    current_job: Optional[dict] = None

    # ---------- 設定 ----------
    def config(self, key: str, default: Any = None) -> Any:
        v = self.conn.val("SELECT value FROM server_config WHERE key = %s", [key])
        return default if v is None else v

    def set_config(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO server_config(key, value, updated_at) VALUES (%s, CAST(%s AS jsonb), NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            [key, json.dumps(value, ensure_ascii=False)])

    def schedule(self) -> dict:
        s = self.config("schedule", {}) or {}
        return {"start": s.get("start", "22:30"), "end": s.get("end", "03:00"),
                "auto_convert": s.get("auto_convert", True), "auto_whisper": s.get("auto_whisper", True),
                "whisper_after_convert": s.get("whisper_after_convert", True),
                "backup_time": s.get("backup_time", "03:30"), "backup_keep": int(s.get("backup_keep", 14))}

    # ---------- 工作 ----------
    def enqueue(self, jtype: str, payload: dict, priority: int = 0, created_by: Optional[int] = None,
                unique_key: Optional[dict] = None) -> int:
        if unique_key is not None:
            existing = self.conn.val(
                "SELECT id FROM jobs WHERE type = %s AND status IN ('queued','running') AND payload @> CAST(%s AS jsonb) ORDER BY id LIMIT 1",
                [jtype, json.dumps(unique_key)])
            if existing:
                return int(existing)
        return int(self.conn.val(
            "INSERT INTO jobs(type, payload, priority, created_by) VALUES (%s, CAST(%s AS jsonb), %s, %s) RETURNING id",
            [jtype, json.dumps(payload, ensure_ascii=False), priority, created_by]))

    def progress(self, job_id: int, data: dict) -> None:
        self.conn.execute("UPDATE jobs SET result = COALESCE(result, '{}'::jsonb) || CAST(%s AS jsonb) WHERE id = %s",
                          [json.dumps(data, ensure_ascii=False), job_id])

    # ---------- 記錄 ----------
    def syslog(self, level: str, message: str, **context) -> None:
        getattr(log, "warning" if level == "warn" else level, log.info)(message)
        try:
            self.conn.execute("INSERT INTO system_logs(source, level, message, context) VALUES ('agent', %s, %s, CAST(%s AS jsonb))",
                              [level, message[:2000], json.dumps(context, ensure_ascii=False, default=str)])
        except dbmod.DBError:
            pass

    def audit(self, action: str, details: dict, media_id: Optional[int] = None, path: str = "",
              actor_id: Optional[int] = None) -> None:
        name, role = "Server Agent", "system"
        if actor_id:
            a = self.conn.one("SELECT username, full_name, role FROM accounts WHERE id = %s", [actor_id])
            if a:
                name, role = (a["full_name"] or a["username"]), a["role"]
        self.conn.execute(
            "INSERT INTO audit_log(actor_id, actor_name, actor_role, action, media_id, path, details) "
            "VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS jsonb))",
            [actor_id, name, role, action, media_id, path[:1000], json.dumps(details, ensure_ascii=False, default=str)])

    # ---------- 媒體標記（第 17 章：避免 /Media 未掛載或掛錯磁碟） ----------
    def media_marker_ok(self) -> tuple[bool, str]:
        if self.env.get("ISTUDY_REQUIRE_MEDIA_MARKER", "1") == "0":
            return True, "已停用標記檢查（開發模式）"
        expected = self.config("install_id")
        try:
            with open(self.paths.marker, encoding="utf-8") as f:
                actual = f.read().strip()
        except OSError:
            return False, f"找不到媒體標記 {self.paths.marker}（/Media 可能未掛載）"
        if not expected:
            return False, "資料庫沒有安裝識別碼（請執行 istudyctl bootstrap）"
        if actual != expected:
            return False, "媒體標記與資料庫的安裝識別碼不同（可能掛錯磁碟）"
        return True, ""

    def require_media_marker(self) -> None:
        ok, msg = self.media_marker_ok()
        if not ok:
            self.flags.add("media_marker_mismatch")  # Server App 總覽顯示「✗ /Media 未掛載或媒體標記不符」
            raise JobError("拒絕執行：" + msg)
        self.flags.discard("media_marker_mismatch")


def make_ctx(conn: Optional[dbmod.Connection] = None) -> Ctx:
    return Ctx(conn=conn or dbmod.connect(), paths=paths_from_env())


def setup_logging() -> None:
    logging.basicConfig(level=os.environ.get("ISTUDY_LOG_LEVEL", "INFO"), stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
