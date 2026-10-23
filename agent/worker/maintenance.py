"""Server 診斷、備份、清理（第 10、15、18 章）。"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from typing import Optional

from istudy_common import checks
from istudy_common.util import TZ

from .context import Ctx, JobError, Retry, agent_version

HOSTCHECK_FILE = "/var/lib/istudy/hostcheck.json"
BACKUP_TABLES = ["accounts", "folders", "media", "bookmarks", "view_history", "subtitles", "subtitle_cues", "course_groups",
                 "client_devices", "audit_log"]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# ---------- 診斷 ----------

def read_hostcheck(ctx: Ctx) -> tuple[dict | None, int | None]:
    path = ctx.env.get("ISTUDY_HOSTCHECK_FILE", HOSTCHECK_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data, int(time.time() - os.path.getmtime(path))
    except (OSError, ValueError):
        return None, None


def asr_info(ctx: Ctx) -> dict:
    """dev.59：Server 診斷列出字幕辨識引擎的實際狀態（取代 dev.58 以前的 Whisper 套件／模型列舉）。"""
    try:
        from .asr import engines
        return engines.status_all(ctx.env.get("ISTUDY_MODELS_DIR", "/models"))
    except Exception as e:  # noqa: BLE001 — 診斷不能因為這一項失敗
        return {"error": str(e)[:200]}


ENCODER_NAME = {"qsv": "Intel Quick Sync（h264_qsv）", "vaapi": "Intel VA-API（h264_vaapi）", "cpu": "CPU（libx264）"}
TIER_LABEL = {"qsv": "Quick Sync", "vaapi": "VA-API", "cpu": "CPU"}


def encoder_result(encoder: Optional[str], tests: dict, forced: str = "auto") -> dict:
    """dev.55 HW-01：實際使用的編碼器（由 Agent 回報）。降級到 CPU 時列出原因，Super 後台顯示 ⚠。
    dev.59：設定指定（.env 的 ISTUDY_ENCODER）時寫出來，例如 QSV 檢測通過但正式轉檔仍指定 VA-API。"""
    enc = encoder or "cpu"
    note = f"設定指定（ISTUDY_ENCODER={forced}）" if forced in ("qsv", "vaapi", "cpu") else "自動（Quick Sync → VA-API → CPU）"
    if enc in ("qsv", "vaapi"):
        return checks.result("encoder", "轉檔編碼器（實際使用）", checks.PASS, note, ENCODER_NAME[enc])
    why = "；".join(f"{TIER_LABEL[e]}：{(tests.get(e) or {}).get('message') or '未檢測'}"
                   for e in ("qsv", "vaapi") if not (tests.get(e) or {}).get("ok"))
    return checks.result("encoder", "轉檔編碼器（實際使用）", checks.WARN,
                         "⚠ 已降級為 CPU（較慢）" + (f"：{why}" if why else "（設定指定）"), ENCODER_NAME["cpu"])


def encoder_tiers(encoder: Optional[str], tests: dict) -> list[dict]:
    """三層各自的檢測結果。較高層失敗但已有可用的硬體編碼時標 SKIP（不影響），不把 QSV 失敗說成整個 GPU 失敗。"""
    out = []
    hw_ok = any((tests.get(e) or {}).get("ok") for e in ("qsv", "vaapi"))
    for e in ("qsv", "vaapi", "cpu"):
        r = tests.get(e)
        if r is None:
            out.append(checks.result(f"encoder_{e}", f"轉檔檢測：{TIER_LABEL[e]}", checks.SKIP, "未檢測"))
            continue
        if r.get("ok"):
            status, msg = checks.PASS, ("使用中" if e == encoder else "可用（目前沒有用來轉檔）") + (f"：{r['message']}" if r.get("message") else "")
        elif hw_ok:
            status, msg = checks.SKIP, f"未通過（不影響，已使用 {TIER_LABEL.get(encoder or '', encoder)}）：{r.get('message')}"
        else:
            status, msg = checks.WARN, f"未通過：{r.get('message')}"
        item = checks.result(f"encoder_{e}", f"轉檔檢測：{TIER_LABEL[e]}", status, msg)
        if r.get("stderr"):
            item["detail"] = r["stderr"]
        out.append(item)
    return out


def diagnose(ctx: Ctx, job: dict | None = None) -> dict:
    conn = ctx.conn
    host, age = read_hostcheck(ctx)
    media = checks.disk_usage(ctx.paths.media_root)
    marker_ok, marker_msg = ctx.media_marker_ok()
    db = {"ok": True, "size": conn.val("SELECT pg_database_size(current_database())"), "version": conn.val("SHOW server_version")}
    queue = conn.one("""SELECT
        COUNT(*) FILTER (WHERE type = 'convert' AND status = 'queued') AS convert_queued,
        COUNT(*) FILTER (WHERE type = 'convert' AND status = 'running') AS convert_running,
        COUNT(*) FILTER (WHERE type = 'asr' AND status = 'queued') AS asr_queued,
        COUNT(*) FILTER (WHERE status = 'failed' AND finished_at > NOW() - INTERVAL '7 days') AS failed_7d
        FROM jobs""")
    items = [
        checks.result("db", "資料庫", checks.PASS, "", db["version"]),
        checks.result("agent", "Agent", checks.PASS, "", agent_version()),
        checks.result("marker", "媒體標記", checks.PASS if marker_ok else checks.FAIL, marker_msg),
        encoder_result(ctx.encoder, ctx.encoders, str(ctx.env.get("ISTUDY_ENCODER", "auto") or "auto")),
    ] + encoder_tiers(ctx.encoder, ctx.encoders)
    if media:
        items.append(checks.result("media_free", "/Media 剩餘空間", checks.PASS if media["free_pct"] >= 20 else checks.WARN,
                                   "" if media["free_pct"] >= 20 else f"剩餘 {media['free_pct']}%", media))
    if host is None:
        items.append(checks.result("hostcheck", "主機檢查", checks.WARN, "沒有主機檢查結果（istudy-hostcheck.timer 未執行？）"))
    elif age is not None and age > 1800:
        items.append(checks.result("hostcheck", "主機檢查", checks.WARN, f"⚠ 主機檢查未更新（{age // 60} 分鐘前）"))
    else:
        items.append(checks.result("hostcheck", "主機檢查", checks.PASS, "", age))
        for c in host.get("checks", []):
            if c.get("id") in ("zfs", "smart", "system_disk", "containers"):
                items.append(c)
    data = {
        "time": _dt.datetime.now(TZ).isoformat(),
        "agent_version": agent_version(),
        "cpu": {"model": checks.cpu_model(), "cores": os.cpu_count(), "load": checks.load_avg()},
        "memory": {"total": checks.mem_total_bytes(), "available": checks.mem_available_bytes()},
        "media_disk": media,
        "database": db,
        "queue": queue,
        "ffmpeg": checks.ffmpeg_version(),
        "encoder": ctx.encoder,
        "encoders": ctx.encoders,
        "libreoffice": checks.libreoffice_version(),
        "asr": asr_info(ctx),
        "media_marker": {"ok": marker_ok, "message": marker_msg},
        "host": host,
        "host_age_sec": age,
        "checks": items,
        "status": checks.summarize(items),
    }
    did = conn.val("INSERT INTO diagnostics(kind, data) VALUES ('server', CAST(%s AS jsonb)) RETURNING id", [_j(data)])
    _fill_checklist(ctx, data)
    return {"diagnostic_id": did, "status": data["status"]}


def _fill_checklist(ctx: Ctx, d: dict) -> None:
    """把診斷結果填入 Super 檢查清單的自動欄位（人工再確認）。"""
    host = d.get("host") or {}
    sysinfo = host.get("system") or {}
    mem = d.get("memory") or {}
    media = d.get("media_disk") or {}
    values = {
        ("os_packages", "Server：Ubuntu 版本"): sysinfo.get("os"),
        ("os_packages", "Server：Docker Engine"): (host.get("docker") or {}).get("docker"),
        ("os_packages", "Server：Docker Compose"): (host.get("docker") or {}).get("compose"),
        ("os_packages", "Server：PostgreSQL"): (d.get("database") or {}).get("version"),
        ("os_packages", "Server：FFmpeg"): d.get("ffmpeg"),
        ("os_packages", "Server：Intel 媒體驅動（iHD）"): {"qsv": "Quick Sync 可用", "vaapi": "VA-API 可用（Quick Sync 未通過）"}.get(
            d.get("encoder") or "", "硬體編碼無法使用（CPU 轉檔）"),
        ("os_packages", "Server：LibreOffice"): d.get("libreoffice"),
        ("os_packages", "Server：Python"): sysinfo.get("python"),
        ("server_hw", "CPU"): (d.get("cpu") or {}).get("model"),
        ("server_hw", "RAM"): f"{round((mem.get('total') or 0) / 1024 ** 3, 1)} GB" if mem.get("total") else None,
        ("server_hw", "磁碟容量與剩餘空間"): f"/Media 剩 {media.get('free_pct')}%" if media else None,
        ("server_hw", "ZFS"): ", ".join(f"{p['name']}={p['health']}" for p in (host.get("zfs") or {}).get("pools", [])) or None,
        ("server_hw", "SMART"): ", ".join(f"{x['device']}:{'OK' if x.get('passed') else '異常' if x.get('passed') is False else '?'}"
                                          for x in host.get("smart") or []) or None,
        ("server_hw", "網路"): ", ".join(sysinfo.get("ipv4") or []) or None,
    }
    for (cat, title), v in values.items():
        if v:
            ctx.conn.execute("UPDATE checklist_items SET auto_value = %s, updated_at = NOW() WHERE category = %s AND title = %s AND device_id IS NULL",
                             [str(v)[:500], cat, title])


# ---------- 備份 ----------

def _db_env(ctx: Ctx) -> dict:
    e = dict(os.environ)
    e["PGPASSWORD"] = ctx.env.get("DB_PASSWORD", "")
    return e


def _db_args(ctx: Ctx, dbname: str | None = None) -> list[str]:
    return ["-h", ctx.env.get("DB_HOST", "127.0.0.1"), "-p", ctx.env.get("DB_PORT", "5432"), "-U", ctx.env.get("DB_USER", "istudy"),
            "-d", dbname or ctx.env.get("DB_NAME", "istudy")]


def _counts(ctx: Ctx, conn=None) -> dict:
    c = conn or ctx.conn
    return {t: int(c.val(f"SELECT COUNT(*) FROM {t}")) for t in BACKUP_TABLES}


def _backup_failed(ctx: Ctx, kind: str, message: str, level: str = "warn") -> JobError:
    ctx.conn.execute("INSERT INTO backup_history(kind, status, format, error_message) VALUES (%s, 'failed', 'age', %s)", [kind, message[:1000]])
    ctx.syslog(level, "備份失敗：" + message)
    return JobError(message)


def _min_free_bytes(ctx: Ctx) -> int:
    """建立新備份前要有的剩餘空間：至少 1 GiB（可用 ISTUDY_BACKUP_MIN_FREE_MB 調整），且不少於上一份加密備份的 2 倍。"""
    floor = int(ctx.env.get("ISTUDY_BACKUP_MIN_FREE_MB", "1024")) * 1024 * 1024
    last = ctx.conn.val("SELECT size_bytes FROM backup_history WHERE format = 'age' AND status = 'success' ORDER BY id DESC LIMIT 1")
    return max(floor, 2 * int(last or 0))


def _plan_b_members(ctx: Ctx, work: str) -> list[tuple[str, str]]:
    """方案 B 的內容（不含 manifest）：(檔案路徑, 備份內名稱)。教材原檔、播放 MP4、Outgo、快取、模型都不放。"""
    out = [(os.path.join(work, "db.sql"), "db.sql")]
    if os.path.isdir(ctx.paths.subtitles):
        for name in sorted(os.listdir(ctx.paths.subtitles)):
            full = os.path.join(ctx.paths.subtitles, name)
            if name.endswith(".vtt") and not name.startswith(".") and os.path.isfile(full) and not os.path.islink(full):
                out.append((full, f"subtitles/{name}"))
    # 必要系統設定一定要在：少了就停止，不產生之後才驗證失敗的不完整備份
    cfg_dir = ctx.env.get("ISTUDY_HOST_CONFIG_DIR", "/host-config")
    missing = []
    for name in ("compose.yaml", ".env", "VERSION.json"):
        fp = os.path.join(cfg_dir, name)
        if os.path.isfile(fp):
            out.append((fp, f"config/{name}"))
        else:
            missing.append(fp)
    install_id = ctx.config("install_id")
    if install_id:
        fp = os.path.join(work, "install_id")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(str(install_id) + "\n")
        out.append((fp, "config/install_id"))
    else:
        missing.append("資料庫的安裝識別碼（install_id）")
    if missing:
        raise JobError("缺少必要的系統設定，沒有建立備份：" + "、".join(missing) + "（Agent 容器的 /host-config 掛載不正確？）")
    return out


def _write_encrypted(ctx: Ctx, dest: str, kind: str, recipient: str):
    """（持有備份鎖時呼叫）pg_dump → tar.gz 串流 → age 公鑰加密 → 改名 → .sha256。失敗時清掉半成品。"""
    from istudy_common import backupcrypt as bc
    stamp = _dt.datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    final = os.path.join(dest, f"istudy-backup-{stamp}.tar.gz.age")
    part = os.path.join(dest, f".istudy-backup-{stamp}.tar.gz.age.part")
    work = tempfile.mkdtemp(prefix="istudy-backup-")
    age = None
    try:
        before = _counts(ctx)
        dump = os.path.join(work, "db.sql")
        r = subprocess.run(["pg_dump", "--no-owner", "--no-privileges", "-f", dump] + _db_args(ctx), capture_output=True,
                           text=True, env=_db_env(ctx), timeout=3600)
        if r.returncode != 0:
            raise JobError("pg_dump 失敗：" + r.stderr.strip()[-300:])
        after = _counts(ctx)
        migrations = [x["version"] for x in ctx.conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        version = agent_version()
        members = _plan_b_members(ctx, work)
        restore = os.path.join(work, bc.RESTORE_NAME)
        with open(restore, "w", encoding="utf-8") as f:
            f.write(bc.restore_md(version, recipient))
        members.append((restore, bc.RESTORE_NAME))
        files = [{"name": arc, "size": os.path.getsize(fp), "sha256": bc.sha256_file(fp)} for fp, arc in members]
        manifest = {"format": bc.FORMAT, "created_at": _dt.datetime.now(TZ).isoformat(), "server_version": version, "kind": kind,
                    "migrations": migrations, "counts_before": before, "counts_after": after, "files": files,
                    "excluded": bc.EXCLUDED, "encryption": {"tool": "age", "recipient": recipient}}
        mf = os.path.join(work, "manifest.json")
        with open(mf, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        os.close(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        age = subprocess.Popen([ctx.env.get("ISTUDY_AGE_BIN") or "age", "-r", recipient, "-o", part],
                               stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        with tarfile.open(fileobj=age.stdin, mode="w|gz", compresslevel=6) as tar:   # type: ignore[arg-type]
            for fp, arc in members:
                tar.add(fp, arc, recursive=False)
            tar.add(mf, "manifest.json", recursive=False)       # 最後放 Manifest（含其他檔案的 SHA-256）
        age.stdin.close()  # type: ignore[union-attr]
        err = age.stderr.read().decode("utf-8", "replace") if age.stderr else ""
        if age.wait(timeout=600) != 0:
            raise JobError("age 加密失敗：" + err.strip()[-300:])
        if not bc.looks_like_age(part):
            raise JobError("加密結果不是 age 格式")
        os.replace(part, final)
        digest = bc.sha256_file(final)
        bc.write_sidecar(final, digest)
        return final, digest, files, after, migrations, version
    except BaseException:
        if age and age.poll() is None:
            age.kill()
        for x in (part, final, bc.sidecar_path(final)):
            if os.path.exists(x):
                os.remove(x)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)   # 明文 db.sql 只在暫存目錄，結束就刪


def backup(ctx: Ctx, job: dict) -> dict:
    """dev.62 方案 B：資料庫＋字幕＋必要設定＋Manifest＋RESTORE.md，以 age 公鑰加密成 istudy-backup-<時間>.tar.gz.age（附 .sha256）。
    Agent 只有公鑰（server_config.backup_key.recipient），無法解密；解密驗證、上傳、清理由主機 istudyctl 執行。
    本機只保存加密版本（壓縮內容直接串流給 age，不落地成明文 tar）。"""
    from istudy_common import backupcrypt as bc
    p = job.get("payload") or {}
    kind = p.get("kind", "daily")
    dest = ctx.env.get("ISTUDY_BACKUP_DIR", "/srv/istudy/backup")
    if ctx.env.get("ISTUDY_BACKUP_REQUIRE_MOUNT", "1") != "0" and not os.path.ismount(dest):
        raise _backup_failed(ctx, kind, "✗ 備份磁碟未連接（請在 Server App「主機維護」設定備份磁碟）")
    key = ctx.config("backup_key", {}) or {}
    recipient = str(key.get("recipient") or "")
    if not bc.RECIPIENT_RE.match(recipient):
        raise _backup_failed(ctx, kind, "✗ 尚未設定備份恢復密碼（加密金鑰）：不會產生未加密的備份。請在 Server App「備份」設定恢復密碼", "error")
    os.makedirs(dest, exist_ok=True)
    need = _min_free_bytes(ctx)
    free = shutil.disk_usage(dest).free
    if free < need:
        # 不擅自刪除任何備份騰出空間（BAK-08）
        raise _backup_failed(ctx, kind, f"✗ 備份磁碟空間不足：剩 {free / 1024 ** 3:.2f} GiB，需要至少 {need / 1024 ** 3:.2f} GiB；"
                                        "已停止這次備份，沒有刪除任何既有備份", "error")
    try:
        lock = bc.BackupLock(dest, wait=float(ctx.env.get("ISTUDY_BACKUP_LOCK_WAIT", "30"))).__enter__()
    except bc.Busy:
        raise Retry("主機正在驗證、上傳或清理備份，10 分鐘後重試", 600) from None
    try:
        try:
            final, digest, files, after, migrations, version = _write_encrypted(ctx, dest, kind, recipient)
        except JobError as e:
            raise _backup_failed(ctx, kind, str(e)) from None
        except Exception as e:
            ctx.conn.execute("INSERT INTO backup_history(kind, status, format, error_message) VALUES (%s, 'failed', 'age', %s)", [kind, str(e)[:1000]])
            raise
        size = os.path.getsize(final)
        summary = {"format": bc.FORMAT, "server_version": version, "migration": migrations[-1] if migrations else None,
                   "counts": after, "files": len(files) + 1, "subtitles": sum(1 for f in files if f["name"].startswith("subtitles/")),
                   "recipient": recipient}
        # 紀錄寫好才放開鎖：主機的驗證／清理只處理資料庫裡有紀錄的備份
        bid = ctx.conn.val("INSERT INTO backup_history(kind, status, file_path, size_bytes, file_count, format, sha256, enc_status, manifest) "
                           "VALUES (%s, 'success', %s, %s, %s, 'age', %s, 'unverified', CAST(%s AS jsonb)) RETURNING id",
                           [kind, final, size, len(files), digest, _j(summary)])
    finally:
        lock.__exit__(None, None, None)
    return {"backup_id": bid, "file": final, "size": size, "files": len(files), "sha256": digest, "encrypted": True}


def _open_test_db(ctx: Ctx, test_db: str):
    """還原檢查用的資料庫 istudy_restore_test：正式環境由 istudyctl bootstrap 預先建立（擁有者 istudy）。
    istudy 角色沒有 CREATEDB 權限，所以 Agent 不建立、不刪除資料庫；只有開發測試（有 CREATEDB 的帳號）才在這裡建立。"""
    from istudy_common import db as dbmod
    try:
        return dbmod.connect(dbmod.conninfo_from_env({**ctx.env, "DB_NAME": test_db}))
    except dbmod.DBError as e:
        if "does not exist" not in str(e):
            raise
    try:
        admin = dbmod.connect(dbmod.conninfo_from_env({**ctx.env, "DB_NAME": "postgres"}))
        try:
            admin.exec_raw(f"CREATE DATABASE {test_db}")
        finally:
            admin.close()
    except dbmod.DBError as e:
        raise JobError(f"還原檢查用的資料庫 {test_db} 不存在：請執行 sudo istudyctl bootstrap（會建立）") from e
    return dbmod.connect(dbmod.conninfo_from_env({**ctx.env, "DB_NAME": test_db}))


RESET_SCHEMA = "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public"


def verify_backup(ctx: Ctx, archive: str) -> dict:
    """還原到測試資料庫 istudy_restore_test，比對資料表筆數（第 18 章：先還原到測試資料庫）。
    每次先清空並重建它的 public schema（istudy 是擁有者，不需要 CREATEDB），結束後再清空。"""
    test_db = "istudy_restore_test"
    work = tempfile.mkdtemp(prefix="istudy-verify-")
    tconn = None
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.getmember("db.sql")
            tar.extract(member, work, filter="data")
            manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
        tconn = _open_test_db(ctx, test_db)
        tconn.exec_raw(RESET_SCHEMA)
        r = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "-f", os.path.join(work, "db.sql")] + _db_args(ctx, test_db),
                           capture_output=True, text=True, env=_db_env(ctx), timeout=3600)
        if r.returncode != 0:
            return {"passed": False, "message": "還原失敗：" + r.stderr.strip()[-300:]}
        restored = _counts(ctx, tconn)
        b, a = manifest["counts_before"], manifest["counts_after"]
        bad = [t for t in BACKUP_TABLES if not (min(b[t], a[t]) <= restored[t] <= max(b[t], a[t]))]
        return {"passed": not bad, "restored_counts": restored, "expected": b,
                "message": "" if not bad else "筆數不符：" + "、".join(bad)}
    finally:
        if tconn:
            try:
                tconn.exec_raw(RESET_SCHEMA)
            finally:
                tconn.close()
        shutil.rmtree(work, ignore_errors=True)


def integrity_check(archive: str) -> dict:
    """dev.55 BK-01「檢查壓縮檔完整性」：完整讀過 gzip（檢查 CRC 與長度）與 tar 結構，確認 db.sql、manifest.json 存在。
    只讀檔案，不解壓到磁碟、不連資料庫。通過只代表檔案完整，不代表可以還原（還原要另做「隔離還原測試」）。"""
    try:
        with gzip.open(archive, "rb") as g:
            while g.read(1 << 20):
                pass
        names = []
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar:
                names.append(m.name)
            manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
            dbm = tar.getmember("db.sql")
    except KeyError as e:
        return {"passed": False, "message": f"缺少必要檔案：{e}"}
    except (OSError, EOFError, tarfile.TarError, ValueError) as e:
        return {"passed": False, "message": f"壓縮檔損壞或無法讀取：{type(e).__name__}: {e}"[:300]}
    if dbm.size <= 0:
        return {"passed": False, "message": "db.sql 是空的"}
    data = [n for n in names if n != "manifest.json"]
    return {"passed": True, "message": "", "data_files": len(data), "manifest": 1, "members": len(names),
            "db_sql_bytes": dbm.size, "created_at": manifest.get("created_at"), "server_version": manifest.get("server_version")}


def encrypted_integrity(path: str, expected: Optional[str]) -> dict:
    """dev.62：加密備份的完整性（Agent 不能解密）：檔頭是 age、SHA-256 與紀錄及 .sha256 旁附檔一致。"""
    from istudy_common import backupcrypt as bc
    if not bc.looks_like_age(path):
        return {"passed": False, "message": "檔頭不是 age 加密格式"}
    digest = bc.sha256_file(path)
    side = bc.read_sidecar(path)
    if expected and digest != expected:
        return {"passed": False, "message": "SHA-256 與建立時的紀錄不符（檔案可能損壞或被修改）"}
    if side and digest != side:
        return {"passed": False, "message": "SHA-256 與 .sha256 檔不符"}
    return {"passed": True, "message": "", "sha256": digest, "note": "加密檔完整（SHA-256 相符）；能否解密要由主機做「解密驗證」"}


def backup_check(ctx: Ctx, job: dict) -> dict:
    """dev.55 BK-01：對「既有的一份備份」做檢查，與建立備份分開。
    mode=integrity：只讀壓縮檔；mode=restore_test：還原到隔離的 istudy_restore_test 比對筆數（絕不碰正式資料庫 istudy）。
    正式還原不在這裡：只能在主機終端機執行 istudyctl restore --target prod（先測試庫、再輸入「覆蓋」確認）。"""
    p = job.get("payload") or {}
    mode = p.get("mode")
    if mode not in ("integrity", "restore_test"):
        raise JobError("檢查方式只能是 integrity 或 restore_test")
    row = ctx.conn.one("SELECT id, file_path, format, sha256 FROM backup_history WHERE id = %s AND status = 'success'", [p.get("backup_id")])
    if not row or not row["file_path"]:
        raise JobError("找不到這份備份")
    dest = os.path.realpath(ctx.env.get("ISTUDY_BACKUP_DIR", "/srv/istudy/backup"))
    path = os.path.realpath(row["file_path"])
    if os.path.dirname(path) != dest or not os.path.basename(path).startswith("istudy-backup-"):
        raise JobError("備份檔不在備份目錄內，拒絕檢查")
    encrypted = row.get("format") == "age"
    if encrypted and mode == "restore_test":
        # dev.62：Agent 沒有解密私鑰（BAK-12）；加密備份的解密驗證與還原測試由主機 istudyctl 執行
        raise JobError("加密備份的還原測試需要解密私鑰，由主機執行：請在 Server App「備份」按「解密驗證＋還原測試」")
    if not os.path.isfile(path):
        res = {"passed": False, "message": "備份檔已不存在（可能已依保留規則刪除）"}
    elif encrypted:
        res = encrypted_integrity(path, row.get("sha256"))
    elif mode == "integrity":
        res = integrity_check(path)
    else:
        from istudy_common import backupcrypt as bc
        try:
            with bc.BackupLock(dest, wait=float(ctx.env.get("ISTUDY_BACKUP_LOCK_WAIT", "30"))):   # 與主機的還原測試共用測試資料庫
                res = verify_backup(ctx, path)
        except bc.Busy:
            raise Retry("主機正在處理備份，10 分鐘後重試", 600) from None
        res["target_db"] = "istudy_restore_test"
    res["checked_at"] = _dt.datetime.now(TZ).isoformat()
    ctx.conn.execute("UPDATE backup_history SET verify_result = COALESCE(verify_result, '{}'::jsonb) || CAST(%s AS jsonb) WHERE id = %s",
                     [_j({mode: res}), row["id"]])
    return {"backup_id": row["id"], "mode": mode, **{k: res[k] for k in ("passed", "message") if k in res}}


# ---------- 清理（每日） ----------

def cleanup(ctx: Ctx, job: dict) -> dict:
    c = ctx.conn
    out = {
        "sessions": len(c.execute("DELETE FROM sessions WHERE expires_at < NOW() RETURNING 1")),
        "login_failures": len(c.execute("DELETE FROM login_failures WHERE COALESCE(locked_until, first_failed_at) < NOW() - INTERVAL '1 day' RETURNING 1")),
        "client_logs": len(c.execute("DELETE FROM client_logs WHERE created_at < NOW() - INTERVAL '90 days' RETURNING 1")),
        "system_logs": len(c.execute("DELETE FROM system_logs WHERE created_at < NOW() - INTERVAL '180 days' RETURNING 1")),
        "jobs": len(c.execute("DELETE FROM jobs WHERE status IN ('done','cancelled') AND finished_at < NOW() - INTERVAL '90 days' RETURNING 1")),
        "diagnostics": len(c.execute("DELETE FROM diagnostics WHERE created_at < NOW() - INTERVAL '180 days' RETURNING 1")),
        # 使用記錄（audit_log）永久保留，不清除（Q-07）
    }
    tmp = 0
    cutoff = time.time() - 86400
    for d in (ctx.paths.playback, ctx.paths.doc_cache, ctx.paths.subtitles):
        try:
            for name in os.listdir(d):
                full = os.path.join(d, name)
                if (name.endswith(".tmp") or name.endswith(".tmp.mp4")) and os.path.getmtime(full) < cutoff:
                    os.remove(full)
                    tmp += 1
        except OSError:
            continue
    out["temp_files"] = tmp
    return out
