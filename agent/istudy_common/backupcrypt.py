"""dev.62 方案 B 加密備份：Agent（只有加密公鑰）與主機 istudyctl（root，持有解密私鑰）共用的工具。只用 Python 標準函式庫。

- 加密一律用 age（https://age-encryption.org），不自製加密演算法。
- 備份檔：istudy-backup-<時間>.tar.gz.age，旁邊附 <檔名>.sha256（sha256sum 格式，可直接 sha256sum -c）。
- 備份、驗證、上傳、清理共用一把檔案鎖（備份目錄的 .istudy-backup.lock），同一時間只做一件事。
- 遠端：FTPS（明示 TLS，AUTH TLS）；憑證一定驗證，不允許明文 FTP。
"""
from __future__ import annotations

import datetime as _dt
import errno
import fcntl
import ftplib
import hashlib
import json
import os
import re
import socket
import ssl
import tarfile
import time
from typing import IO, Iterable, Optional

AGE_MAGIC = b"age-encryption.org/v1"
NAME_RE = re.compile(r"^istudy-backup-(\d{8}-\d{6})\.tar\.gz\.age$")
LEGACY_RE = re.compile(r"^istudy-backup-(\d{8}-\d{6})\.tar\.gz$")
RECIPIENT_RE = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")
LOCK_NAME = ".istudy-backup.lock"
RECOVERY_KEY_NAME = "istudy-recovery-key.age"
RESTORE_NAME = "RESTORE.md"
FORMAT = "istudy-plan-b-1"
KEEP_DAYS_DEFAULT, KEEP_DAYS_MIN, KEEP_DAYS_MAX = 15, 10, 15
UPLOAD_EVERY_DAYS = 3
REQUIRED_MEMBERS = ("db.sql", "manifest.json", RESTORE_NAME, "config/VERSION.json", "config/.env", "config/compose.yaml", "config/install_id")
# 方案 B 不包含的內容（寫進 Manifest 與 RESTORE.md；畫面也要明確告知）
EXCLUDED = [
    "教材原始影片、音訊、文件（/Media 課程資料夾）",
    "/Media/playback/*.mp4（播放用影片；原檔刪除後可能是唯一一份）",
    "/Media/Outgo 錄音與逐字稿",
    "可再生的快取（/Media/doc-cache）、字幕辨識模型、Client 安裝檔、UEM 報告",
]
# 遠端資料夾不能在網站公開目錄裡（任何一層叫這些名字都拒絕）
WEB_ROOT_NAMES = {"public_html", "www", "htdocs", "html", "public", "web", "wwwroot", "httpdocs", "httpsdocs", "private_html"}


class Busy(Exception):
    """另一個備份相關工作正在進行。"""


class BackupLock:
    """備份目錄的檔案鎖（flock）。Agent（uid 10001）與主機 root 都能開（唯讀開啟即可加鎖）。"""

    def __init__(self, directory: str, wait: float = 0.0):
        self.path = os.path.join(directory, LOCK_NAME)
        self.wait = wait
        self.fd: Optional[int] = None

    def __enter__(self) -> "BackupLock":
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o644)
        end = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= end:
                    os.close(self.fd)
                    self.fd = None
                    raise Busy("另一個備份工作（建立、驗證、上傳或清理）正在進行") from None
                time.sleep(min(2.0, max(0.05, self.wait / 50)))

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


# ---------- 雜湊、旁附檔 ----------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_path(path: str) -> str:
    return path + ".sha256"


def write_sidecar(path: str, digest: str, mode: int = 0o640) -> str:
    side = sidecar_path(path)
    tmp = side + ".part"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(f"{digest}  {os.path.basename(path)}\n")
    os.chmod(tmp, mode)
    os.replace(tmp, side)
    return side


def read_sidecar(path: str) -> Optional[str]:
    try:
        with open(sidecar_path(path), encoding="ascii") as f:
            first = f.read(200).split()
    except (OSError, UnicodeDecodeError):
        return None
    return first[0].lower() if first and re.fullmatch(r"[0-9a-fA-F]{64}", first[0]) else None


def looks_like_age(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.readline().rstrip(b"\n") == AGE_MAGIC
    except OSError:
        return False


def stamp_of(name: str) -> Optional[_dt.datetime]:
    m = NAME_RE.match(name) or LEGACY_RE.match(name)
    return _dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else None


# ---------- 解密後的 tar 串流驗證 ----------

def verify_tar_stream(fobj: IO[bytes]) -> dict:
    """逐一讀取解密後的 tar.gz 串流，計算每個檔案的 SHA-256，與 manifest.json 的檔案清單比對。
    只在記憶體計算雜湊，不把內容寫到磁碟。"""
    hashes: dict[str, str] = {}
    sizes: dict[str, int] = {}
    manifest = None
    try:
        with tarfile.open(fileobj=fobj, mode="r|gz") as tar:
            for m in tar:
                name = m.name
                if not m.isfile() or name.startswith("/") or ".." in name.split("/"):
                    return {"passed": False, "message": f"備份內有不合規的項目：{name}"}
                f = tar.extractfile(m)
                h = hashlib.sha256()
                data = bytearray() if name == "manifest.json" else None
                for chunk in iter(lambda: f.read(1 << 20), b""):  # type: ignore[union-attr]
                    h.update(chunk)
                    if data is not None:
                        data.extend(chunk)
                hashes[name] = h.hexdigest()
                sizes[name] = m.size
                if data is not None:
                    manifest = json.loads(bytes(data).decode("utf-8"))
    except (tarfile.TarError, OSError, EOFError, ValueError) as e:
        return {"passed": False, "message": f"解密後的內容無法讀取（可能損壞）：{type(e).__name__}: {e}"[:300]}
    missing = [n for n in REQUIRED_MEMBERS if n not in hashes]
    if missing:
        return {"passed": False, "message": "缺少必要檔案：" + "、".join(missing)}
    listed = {f["name"]: f for f in (manifest or {}).get("files", [])}
    bad = [n for n, f in listed.items() if hashes.get(n) != f.get("sha256")]
    extra = [n for n in hashes if n != "manifest.json" and n not in listed]
    if bad or extra:
        return {"passed": False, "message": ("SHA-256 不符：" + "、".join(bad[:5]) if bad else "") + ("；Manifest 沒有列出：" + "、".join(extra[:5]) if extra else "")}
    return {"passed": True, "message": "", "members": len(hashes), "db_sql_bytes": sizes.get("db.sql", 0),
            "created_at": manifest.get("created_at"), "server_version": manifest.get("server_version"),
            "migration": (manifest.get("migrations") or [None])[-1], "counts": manifest.get("counts_after") or {},
            "counts_before": manifest.get("counts_before") or {}}


# ---------- 保留與清理（純函式，方便測試） ----------

def keep_days_of(settings: Optional[dict]) -> int:
    try:
        v = int((settings or {}).get("keep_days", KEEP_DAYS_DEFAULT))
    except (TypeError, ValueError):
        return KEEP_DAYS_DEFAULT
    return v if KEEP_DAYS_MIN <= v <= KEEP_DAYS_MAX else KEEP_DAYS_DEFAULT


def retention_plan(rows: Iterable[dict], now: _dt.datetime, keep_days: int) -> dict:
    """rows：加密備份（format=age、尚未刪除）的 {id, created_at, exists, enc_status, remote_status}。
    規則：
      1. 最後一份「可用」的本機備份（解密驗證通過、檔案還在）永遠保留——不論多舊、有沒有遠端副本。
         還沒有任何一份通過驗證時，保留最新一份檔案還在的備份。
      2. 其餘超過保留天數的才刪除；舊的未加密 .tar.gz、istudy-db-*.sql 不在 rows 裡（永遠不自動刪）。"""
    live = sorted((r for r in rows if r.get("exists")), key=lambda r: (r["created_at"], r["id"]), reverse=True)
    verified = [r for r in live if r.get("enc_status") == "verified"]
    protected = verified[0] if verified else (live[0] if live else None)
    cutoff = now - _dt.timedelta(days=keep_days)
    delete = []
    for r in live:
        if protected and r["id"] == protected["id"]:
            continue
        if r["created_at"] < cutoff:
            delete.append({"id": r["id"], "reason": f"超過保留 {keep_days} 天"})
    return {"delete": delete, "protected": protected["id"] if protected else None,
            "protected_reason": ("最後一份可用（已通過解密驗證）的備份" if verified else "還沒有通過驗證的備份：保留最新一份") if protected else "",
            "keep_days": keep_days}


def remote_retention_plan(rows: Iterable[dict], now: _dt.datetime, keep_days: int) -> list[dict]:
    """遠端：已驗證的遠端副本中，最新一份永遠保留；其餘超過保留天數才刪。"""
    ok = sorted((r for r in rows if r.get("remote_status") == "verified"), key=lambda r: (r["created_at"], r["id"]), reverse=True)
    cutoff = now - _dt.timedelta(days=keep_days)
    return [{"id": r["id"], "reason": f"遠端副本超過保留 {keep_days} 天"} for r in ok[1:] if r["created_at"] < cutoff]


def upload_due(last_ok: Optional[_dt.datetime], now: _dt.datetime, every_days: int = UPLOAD_EVERY_DAYS) -> bool:
    """每 every_days 天上傳一次；留 2 小時寬限，避免每天同一時間執行時因幾分鐘差距而延後一天。"""
    return last_ok is None or now - last_ok >= _dt.timedelta(days=every_days) - _dt.timedelta(hours=2)


# ---------- 遠端設定 ----------

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")


def check_remote_dir(d: str) -> Optional[str]:
    if not isinstance(d, str) or not d.startswith("/") or len(d) > 300 or any(c in d for c in "\0\r\n\\"):
        return "遠端資料夾要以 / 開頭（例如 /istudy-backup）"
    parts = [p for p in d.split("/") if p]
    if not parts:
        return "遠端資料夾不能是根目錄 /，請指定一個專用資料夾（例如 /istudy-backup）"
    if any(p in (".", "..") for p in parts):
        return "遠端資料夾不能含 . 或 .."
    web = [p for p in parts if p.lower() in WEB_ROOT_NAMES]
    if web:
        return f"遠端資料夾在網站公開目錄（{web[0]}）裡，任何人可能從網址下載；請改放在公開目錄之外（例如 /istudy-backup）"
    return None


def validate_remote(cfg: dict, require_password: bool = True) -> dict:
    """檢查遠端設定；回傳整理後的設定（只支援 FTPS）。主機、連接埠、帳號一律由使用者輸入，不猜測。"""
    if not isinstance(cfg, dict):
        raise ValueError("遠端設定格式錯誤")
    if (cfg.get("protocol") or "ftps") != "ftps":
        raise ValueError("只支援 FTPS（明示加密 TLS）；不支援明文 FTP")
    host = str(cfg.get("host") or "").strip()
    if not HOST_RE.match(host):
        raise ValueError("主機名稱格式錯誤（只填主機名稱或 IP，不要加 ftp://）")
    try:
        port = int(cfg.get("port"))
    except (TypeError, ValueError):
        raise ValueError("請填連接埠（以 InterServer 控制台顯示的 FTPS 連接埠為準）") from None
    if not 1 <= port <= 65535:
        raise ValueError("連接埠要在 1～65535")
    user = str(cfg.get("username") or "").strip()
    if not user or len(user) > 200 or any(c in user for c in "\0\r\n"):
        raise ValueError("請填 FTP 帳號")
    pw = cfg.get("password")
    if pw is not None and (not isinstance(pw, str) or len(pw) > 500 or any(c in pw for c in "\0\r\n")):
        raise ValueError("密碼格式錯誤")
    if require_password and not pw:
        raise ValueError("請填 FTP 密碼")
    d = str(cfg.get("dir") or "").strip()
    err = check_remote_dir(d)
    if err:
        raise ValueError(err)
    return {"protocol": "ftps", "host": host, "port": port, "username": user, "password": pw or "", "dir": "/" + "/".join(p for p in d.split("/") if p)}


# ---------- FTPS ----------

class _FTPS(ftplib.FTP_TLS):
    """資料通道沿用控制通道的 TLS 工作階段（ProFTPD、Pure-FTPd 常要求 session reuse）。"""

    def ntransfercmd(self, cmd, rest=None):  # noqa: D401
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class FtpsClient:
    def __init__(self, cfg: dict, timeout: float = 60.0, cafile: Optional[str] = None):
        self.cfg = cfg
        self.timeout = timeout
        self.ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ftp: Optional[_FTPS] = None

    def __enter__(self) -> "FtpsClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        c = self.cfg
        ftp = _FTPS(context=self.ctx, timeout=self.timeout)
        try:
            ftp.connect(c["host"], int(c["port"]), timeout=self.timeout)
            ftp.auth()                               # 先 AUTH TLS；伺服器不支援時這裡就失敗，不會用明文送帳密
            ftp.login(c["username"], c["password"], secure=False)
            ftp.prot_p()                             # 資料通道也加密
            ftp.set_pasv(True)
        except Exception:
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self.ftp = ftp

    def close(self) -> None:
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:  # noqa: BLE001
                try:
                    self.ftp.close()
                except Exception:  # noqa: BLE001
                    pass
            self.ftp = None

    def ensure_dir(self, d: str) -> None:
        assert self.ftp
        self.ftp.cwd("/")
        for part in [p for p in d.split("/") if p]:
            try:
                self.ftp.cwd(part)
            except ftplib.error_perm:
                self.ftp.mkd(part)
                self.ftp.cwd(part)

    def put(self, local: str, name: str) -> None:
        """先以暫存名稱上傳，完成後改名（中途中斷不會留下看起來完整的檔案）。"""
        assert self.ftp
        tmp = f".{name}.part"
        with open(local, "rb") as f:
            self.ftp.storbinary(f"STOR {tmp}", f, blocksize=1 << 16)
        try:
            self.ftp.delete(name)                    # 改名前先刪同名舊檔（恢復金鑰、RESTORE.md 會更新）
        except ftplib.error_perm:
            pass
        self.ftp.rename(tmp, name)

    def sha256(self, name: str) -> str:
        assert self.ftp
        h = hashlib.sha256()
        self.ftp.retrbinary(f"RETR {name}", h.update, blocksize=1 << 16)
        return h.hexdigest()

    def delete(self, name: str) -> None:
        assert self.ftp
        self.ftp.delete(name)

    def names(self) -> list[str]:
        assert self.ftp
        try:
            return [os.path.basename(n) for n in self.ftp.nlst()]
        except ftplib.error_perm:
            return []


def ftps_error_text(e: BaseException) -> str:
    """遠端錯誤的白話說明（不含密碼）。"""
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"TLS 憑證驗證失敗（{e.verify_message}）：請確認主機名稱與 InterServer 控制台顯示的 FTP 主機一致"
    if isinstance(e, ssl.SSLError):
        return f"TLS 連線失敗：{e}"[:300]
    if isinstance(e, ftplib.error_perm):
        msg = str(e)
        if msg.startswith("530"):
            return "帳號或密碼錯誤（530）"
        if msg.startswith(("500", "502", "504", "534")) and "AUTH" in msg.upper():
            return f"伺服器不支援 FTPS（AUTH TLS）：{msg}；不會改用明文 FTP"[:300]
        return f"遠端拒絕：{msg}"[:300]
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "連線逾時：請確認主機、連接埠與網路"
    if isinstance(e, (ConnectionRefusedError,)):
        return "連線被拒（連接埠可能不對或伺服器未開放）"
    if isinstance(e, socket.gaierror):
        return "找不到主機（DNS）：請確認主機名稱與 Server 是否能連外"
    return f"{type(e).__name__}: {e}"[:300]


# ---------- RESTORE.md ----------

def restore_md(version: str, recipient: str) -> str:
    """放進每份備份（加密）以及遠端（明文說明，不含任何秘密）。"""
    ex = "\n".join(f"- {x}" for x in EXCLUDED)
    return f"""# iStudy 加密備份還原說明（方案 B）

建立版本：{version}　加密公鑰：{recipient}

## 這份備份有什麼

- PostgreSQL 資料庫（pg_dump：結構、資料、序列）：db.sql
- 字幕檔：subtitles/*.vtt
- 系統設定：config/.env、config/compose.yaml、config/VERSION.json、config/install_id
- manifest.json：版本、Migration、資料表筆數、檔案清單與每個檔案的 SHA-256

## 這份備份沒有什麼（不能用它恢復）

{ex}

## 解密需要什麼

備份以 age 加密。解密需要下列其中一項：

1. Server 主機上的解密私鑰 /etc/istudy/backup/identity.key（只有 root 可讀）；或
2. 恢復金鑰檔 {RECOVERY_KEY_NAME}（遠端與 /etc/istudy/backup/recovery-key.age 各有一份）＋ 備份恢復密碼。

**私鑰與恢復密碼都遺失時，備份無法解密，沒有任何人能救回。**

## 還原步驟（Server 主機）

1. 先確認檔案完整：`sha256sum -c istudy-backup-時間.tar.gz.age.sha256`
2. 主機已沒有私鑰時，先用恢復密碼匯入：
   `sudo istudyctl backup-key --import --file {RECOVERY_KEY_NAME} --password-stdin`（輸入恢復密碼後按 Ctrl+D）
3. 先還原到測試資料庫檢查：`sudo istudyctl restore --file istudy-backup-時間.tar.gz.age --target test`
4. 確認無誤後才覆蓋正式資料庫：`sudo istudyctl restore --file istudy-backup-時間.tar.gz.age --target prod`
   （會再要求在終端機輸入「覆蓋」）

## 沒有 iStudy 主機時（手動）

需要 age（https://github.com/FiloSottile/age/releases）：

    age -d -o identity.key {RECOVERY_KEY_NAME}        # 輸入恢復密碼
    age -d -i identity.key -o backup.tar.gz istudy-backup-時間.tar.gz.age
    tar -xzf backup.tar.gz                              # 得到 db.sql、subtitles、config…

解出的 identity.key、config/.env 都是機密，用完請刪除。
"""
