"""Super 專用「Email 報告」（dev.57）：寄出系統狀態摘要、字幕辨識結果、自訂訊息與 Client 的問題回報。

簡單設計：只寄給設定裡的收件人（預設 eachcore.app@gmail.com）、純文字內文、可附上主機盤點 HTML；
不做排程、不做通知平台。兩種寄送方式：
  relay（預設）：HTTPS POST 到你網站主機上的 istudy-mail-relay.php，由網站的 PHP mail() 寄出，不需要信箱密碼；
  smtp：寄件信箱帳號（網站主機的寄件信箱或另開的 Gmail 應用程式密碼）。
金鑰與密碼只在這裡讀取，不寫進工作結果、Log 或報告。
寄送成功只代表「寄送主機已接受」（網站主機的 mail() 或 SMTP 伺服器回覆接受），不代表收件人已收到；
畫面與工作結果都寫「已提交寄送」。
"""
from __future__ import annotations

import datetime as _dt
import json
import smtplib
import socket
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Optional

from .context import Ctx, JobError, agent_version

DEFAULT_TO = ["eachcore.app@gmail.com"]
REPORTS = {"summary": "系統狀態摘要", "asr": "字幕辨識結果", "message": "訊息", "test": "測試信", "feedback": "問題回報"}
METHOD_TEXT = {"relay": "網站代寄", "smtp": "SMTP"}
JOB_TYPE_TEXT = {"asr": "字幕辨識", "subtitle_import": "匯入字幕", "convert": "轉檔", "probe": "讀取影片資訊", "verify": "轉檔檢查",
                 "delete_original": "刪除原檔", "office_pdf": "文件轉 PDF", "scan": "掃描教材", "backup": "備份",
                 "backup_check": "備份檢查", "cleanup": "清理", "diagnose": "診斷", "wol_wake": "喚醒電腦",
                 "net_probe": "網路檢測", "lan_scan": "LAN 掃描", "email_report": "Email 寄送"}
SUBMITTED_NOTE = "已提交寄送：寄送主機已接受，不代表已送達；請到收件匣（含垃圾郵件）確認"


def _n(ctx: Ctx, sql: str, params: Optional[list] = None) -> int:
    v = ctx.conn.val(sql, params or [])
    return int(v or 0)


def _ago(ts) -> str:
    if not ts:
        return "—"
    d = _dt.datetime.now(ts.tzinfo) - ts
    if d.days >= 1:
        return f"{d.days} 天前"
    h = d.seconds // 3600
    return f"{h} 小時前" if h else f"{max(1, d.seconds // 60)} 分鐘前"


def summary_text(ctx: Ctx) -> str:
    hb = ctx.config("agent_heartbeat", {}) or {}
    hb_at = None
    try:
        hb_at = _dt.datetime.fromisoformat(str(hb.get("at"))) if hb.get("at") else None
    except ValueError:
        pass
    q = {k: _n(ctx, sql) for k, sql in {
        "video": "SELECT COUNT(*) FROM media WHERE kind = 'video'",
        "playable": "SELECT COUNT(*) FROM media WHERE kind = 'video' AND (play_mode = 'direct' OR convert_status = 'done')",
        "conv_failed": "SELECT COUNT(*) FROM media WHERE kind = 'video' AND convert_status = 'failed'",
        "office": "SELECT COUNT(*) FROM media WHERE kind = 'office'",
        "other": "SELECT COUNT(*) FROM media WHERE kind NOT IN ('video','office')",
        "missing": "SELECT COUNT(*) FROM media WHERE status = 'missing'",
        "conv_wait": "SELECT COUNT(*) FROM jobs WHERE type = 'convert' AND status = 'queued'",
        "conv_run": "SELECT COUNT(*) FROM jobs WHERE type = 'convert' AND status = 'running'",
        "subs": "SELECT COUNT(*) FROM subtitles",
        "subs_review": "SELECT COUNT(*) FROM subtitles WHERE asr_report->'qc'->>'status' = 'needs_review'",
        "asr_wait": "SELECT COUNT(*) FROM jobs WHERE type = 'asr' AND status IN ('queued','running')",
    }.items()}
    agent_txt = "運作中" if hb_at else "沒有心跳"
    lines = [
        f"Server 版本：{agent_version()}",
        f"Agent：{agent_txt}（最後心跳 {_ago(hb_at)}）；轉檔編碼器：{hb.get('encoder') or '—'}",
        "",
        "【教材】",
        f"  影片 {q['video']} 支：可播放 {q['playable']}、轉檔失敗 {q['conv_failed']}",
        f"  文件 {q['office']} 份、其他 {q['other']} 個；檔案遺失 {q['missing']}",
        "",
        "【背景工作】",
        f"  轉檔：等待 {q['conv_wait']}、處理中 {q['conv_run']}",
        f"  字幕：已有 {q['subs']} 支（需檢查 {q['subs_review']}）、等待辨識 {q['asr_wait']}",
    ]
    failed = ctx.conn.execute(
        "SELECT type, COUNT(*) n, (ARRAY_AGG(error ORDER BY finished_at DESC))[1] AS last_error FROM jobs "
        "WHERE status = 'failed' AND finished_at > NOW() - INTERVAL '7 days' GROUP BY type ORDER BY n DESC")
    lines.append("  近 7 天失敗：" + ("" if failed else "無"))
    for r in failed:   # 中文工作名稱＋最近一次的失敗原因
        lines.append(f"    - {JOB_TYPE_TEXT.get(r['type'], r['type'])} {r['n']} 次；最近一次：{str(r['last_error'] or '—')[:120]}")
    b = ctx.conn.one("SELECT created_at, size_bytes FROM backup_history WHERE status = 'success' ORDER BY created_at DESC LIMIT 1")
    lines += ["", "【備份】", f"  上次成功：{b['created_at'].strftime('%Y-%m-%d %H:%M')}（{_ago(b['created_at'])}）" if b else "  尚未有成功的備份"]
    errs = _n(ctx, "SELECT COUNT(*) FROM system_logs WHERE level = 'error' AND created_at > NOW() - INTERVAL '24 hours'")
    lines += ["", "【系統 Log】", f"  近 24 小時錯誤 {errs} 則"]
    for r in ctx.conn.execute("SELECT created_at, source, message FROM system_logs WHERE level = 'error' "
                              "AND created_at > NOW() - INTERVAL '24 hours' ORDER BY id DESC LIMIT 5"):
        lines.append(f"  - {r['created_at'].strftime('%m-%d %H:%M')} [{r['source']}] {str(r['message'])[:160]}")
    return "\n".join(lines)


def asr_text(ctx: Ctx) -> str:
    rows = ctx.conn.execute("SELECT s.created_at, s.model, s.asr_report, m.title FROM subtitles s JOIN media m ON m.id = s.media_id "
                            "WHERE s.source = 'asr' ORDER BY s.created_at DESC LIMIT 30")
    if not rows:
        return "還沒有自動產生的字幕。"
    out = ["最近 30 支自動字幕（引擎、速度 RTF＝處理時間÷影片長度、品質檢查）：", ""]
    for r in rows:
        rep = r["asr_report"] or {}
        qc = rep.get("qc") or {}
        mt = rep.get("metrics") or {}
        st = {"ok": "✓ 通過", "needs_review": "⚠ 需檢查"}.get(qc.get("status"), "—")
        out.append(f"- {r['created_at'].strftime('%m-%d %H:%M')}  {r['title']}")
        out.append(f"    引擎 {rep.get('engine') or r['model'] or '—'}　RTF {mt.get('rtf', '—')}　影片 {mt.get('audio_sec', '—')} 秒　{st}")
        for why in (qc.get("reasons") or [])[:3]:
            out.append(f"    ・{why}")
    return "\n".join(out)


def build(ctx: Ctx, p: dict, settings: dict) -> EmailMessage:
    kind = p.get("report") if p.get("report") in REPORTS else "message"
    now = _dt.datetime.now().astimezone()
    subject = (p.get("subject") or "").strip() or f"[iStudy] {REPORTS[kind]} {now.strftime('%Y-%m-%d %H:%M')}"
    body = [f"iStudy {REPORTS[kind]}（{now.strftime('%Y-%m-%d %H:%M')}）", ""]
    note = (p.get("note") or "").strip()
    if kind == "feedback":
        r = p.get("reporter") or {}
        who = f"{r.get('username') or '—'}" + (f"（{r['full_name']}）" if r.get("full_name") else "")
        subject = f"[iStudy] 問題回報：{who}"
        body += ["【回報者】", f"  帳號：{who}", f"  稱謂／權限：{r.get('title') or '—'}／{r.get('role') or '—'}",
                 f"  App：{p.get('app') or '—'}　電腦：{p.get('pc') or '—'}　畫面：{p.get('page') or '—'}",
                 f"  時間：{now.strftime('%Y-%m-%d %H:%M')}", "", "【問題概述】", note or "（沒有內容）", ""]
    elif note:
        body += ["【說明】", note, ""]
    if kind == "summary":
        body.append(summary_text(ctx))
    elif kind == "asr":
        body.append(asr_text(ctx))
    elif kind == "test":
        body.append(f"這是測試信：收到代表「{METHOD_TEXT.get(settings.get('method'), 'Email')}」設定正確。")
    if p.get("uem_html"):
        body += ["", "附件：主機盤點（UEM）報告，可用瀏覽器離線開啟。"]
    body += ["", "—", f"由 iStudy Server 寄出（{p.get('requested_by') or 'Super'}）。這封信是自動產生的，請勿直接回覆。"]
    msg = EmailMessage()
    msg["Subject"] = subject[:200]
    msg["From"] = formataddr(("iStudy Server", settings["from"]))
    msg["To"] = ", ".join(settings["to"])
    msg["Message-ID"] = make_msgid(domain=settings["from"].split("@")[-1] or "istudy.local")
    msg.set_content("\n".join(body))
    if p.get("uem_html"):
        # 以文字加入並標明 UTF-8（dev.57 以位元組加入、沒有 charset，網站代寄取出時被當成 ASCII → 中文變「�」）
        msg.add_attachment(str(p["uem_html"]), subtype="html", charset="utf-8", cte="base64",
                           filename=p.get("attachment_name") or f"istudy-uem-{now.strftime('%Y%m%d-%H%M')}.html")
    return msg


def attachment_html(att) -> str:
    """附件的原始內容（一律讀位元組再以 UTF-8 解碼，不依賴 Content-Type 的 charset）。"""
    raw = att.get_payload(decode=True) or b""
    return raw.decode("utf-8", "replace")


def load_settings(ctx: Ctx) -> dict:
    s = ctx.config("email", {}) or {}
    secret = ctx.config("email_secret", {}) or {}
    to = [x for x in (s.get("to") or DEFAULT_TO) if isinstance(x, str) and "@" in x]
    method = s.get("method") or "relay"
    if method == "relay":
        if not s.get("relay_url") or not secret.get("relay_key"):
            raise JobError("Email 尚未設定：請在 Server App「Email 報告」填寫網站代寄網址與金鑰")
        return {"method": "relay", "relay_url": str(s["relay_url"]), "relay_key": str(secret["relay_key"]),
                "from": "istudy@localhost", "to": to or DEFAULT_TO}
    if not s.get("host") or not s.get("from"):
        raise JobError("Email 尚未設定：請在 Server App「Email 報告」填寫 SMTP 主機與寄件人")
    return {"method": "smtp", "host": str(s["host"]), "port": int(s.get("port") or 587), "security": s.get("security") or "starttls",
            "username": s.get("username") or "", "password": secret.get("password") or "", "from": str(s["from"]),
            "to": to or DEFAULT_TO}


def relay_send(msg: EmailMessage, st: dict, timeout: int = 30) -> dict:
    """網站代寄：把主旨、內文、附件 POST 給 istudy-mail-relay.php；回傳 {to, envelope, relay}（網站主機已接受的資訊）。"""
    att = next(iter(msg.iter_attachments()), None)
    body = {"subject": str(msg["Subject"]), "text": msg.get_body(("plain",)).get_content(), "to": st["to"]}
    if att is not None:
        body["attachment_name"] = att.get_filename()
        body["attachment_html"] = attachment_html(att)
    req = urllib.request.Request(st["relay_url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "X-iStudy-Key": st["relay_key"],
                                          "User-Agent": "iStudy-Server"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read().decode("utf-8", "replace")).get("error") or ""
        except ValueError:
            why = ""
        raise JobError(f"網站代寄拒絕（HTTP {e.code}）：{why or e.reason}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise JobError(f"連不到網站代寄程式（Server 需要能連上網際網路）：{getattr(e, 'reason', e)}") from e
    if not res.get("ok"):
        raise JobError("網站代寄失敗：" + str(res.get("error") or "未知原因"))
    return {"to": list(res.get("to") or st["to"]), "envelope": str(res.get("envelope") or ""), "relay": str(res.get("relay") or "")}


def send(msg: EmailMessage, st: dict, timeout: int = 30) -> None:
    ctx_ssl = ssl.create_default_context()
    try:
        if st["security"] == "ssl":
            smtp = smtplib.SMTP_SSL(st["host"], st["port"], timeout=timeout, context=ctx_ssl)
        else:
            smtp = smtplib.SMTP(st["host"], st["port"], timeout=timeout)
        with smtp:
            smtp.ehlo()
            if st["security"] == "starttls":
                smtp.starttls(context=ctx_ssl)
                smtp.ehlo()
            if st["username"]:
                smtp.login(st["username"], st["password"])
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise JobError("SMTP 帳號或密碼錯誤（Gmail 請使用「應用程式密碼」）") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise JobError("收件人被郵件伺服器拒絕：" + "、".join(e.recipients)) from e
    except (socket.gaierror, socket.timeout, ConnectionError, OSError) as e:
        raise JobError(f"連不到郵件伺服器 {st['host']}:{st['port']}（Server 需要能連上網際網路）：{e}") from e
    except smtplib.SMTPException as e:
        raise JobError(f"郵件伺服器拒絕寄送：{e}") from e


def email_report(ctx: Ctx, job: dict) -> dict:
    st = load_settings(ctx)
    msg = build(ctx, job.get("payload") or {}, st)
    if ctx.env.get("ISTUDY_EMAIL_FAKE") == "1":   # 測試：不連郵件伺服器，只檢查信件內容
        return {"to": st["to"], "subject": msg["Subject"], "bytes": len(msg.as_bytes()), "fake": True,
                "preview": msg.get_body(("plain",)).get_content()[:4000]}
    if st["method"] == "relay":
        r = relay_send(msg, st)
        return {"to": r["to"], "subject": msg["Subject"], "bytes": len(msg.as_bytes()), "method": "relay", "accepted": True,
                "envelope": r["envelope"], "relay": r["relay"], "note": SUBMITTED_NOTE}
    send(msg, st)
    return {"to": st["to"], "subject": msg["Subject"], "bytes": len(msg.as_bytes()), "method": "smtp", "accepted": True,
            "note": SUBMITTED_NOTE}
