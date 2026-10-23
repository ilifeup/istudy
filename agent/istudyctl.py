#!/usr/bin/env python3
"""istudyctl：Ubuntu Server 主機端工具（第 17、18 章）。只用 Python 標準函式庫，以 Ubuntu 內建 python3 執行。

需要 root 的操作只由本程式執行：Server App 以 pkexec 呼叫（polkit 只允許 /usr/local/sbin/istudyctl），
或在終端機用 sudo。只接受固定子指令，不執行任意指令；所有外部程式都以參數陣列呼叫（不經過 shell）。

  bootstrap [--check-only] [--bundle 目錄] [--restore 備份檔] [--super-user 帳號] [--json]
  doctor | status | check-ip | check-media | check-db | hostcheck
  update --bundle 目錄 | restart | backup-disk --uuid UUID | list-disks
  backup [--destination 目錄] | restore --file 備份檔 --target test|prod | reset-super
  uem [--mode quick|full]   主機盤點（只讀）：quick＝磁碟、路徑、ZFS、服務、連接埠、版本；full＝再加自我檢查
  asr-models [--fetch paraformer-zh|sensevoice-small|all]   字幕辨識模型狀態／下載（需要暫時連網）
  backup-key --init|--change|--verify|--status|--import [--file F] [--password-stdin]   備份恢復密碼與加密金鑰（dev.62）
  backup-remote --set|--test | backup-verify [--id N] [--restore-test] | backup-upload [--id N] | backup-prune | backup-run
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import getpass
import glob
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from istudy_common import backupcrypt as bc  # noqa: E402
from istudy_common import checks  # noqa: E402

PREFIX = os.environ.get("ISTUDY_ROOT_PREFIX", "")  # 測試用：把主機路徑導到暫存目錄


def P(path: str) -> str:
    return PREFIX + path if PREFIX else path


def _read(path: str, encoding: str = "utf-8") -> str:
    with open(path, encoding=encoding) as f:
        return f.read()


def _json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


OPT = P("/opt/istudy")
ETC = P("/etc/istudy")
VARLIB = P("/var/lib/istudy")
VARLOG = P("/var/log/istudy")
SRV = P("/srv/istudy")
MEDIA = os.environ.get("ISTUDY_MEDIA_ROOT", "/Media")
SYSTEMD = P("/etc/systemd/system")
POLKIT = P("/usr/share/polkit-1/actions")
SBIN = P("/usr/local/sbin")
STATE_FILE = os.path.join(VARLIB, "bootstrap.json")
HOSTCHECK_FILE = os.path.join(VARLIB, "hostcheck.json")
DEFAULT_SERVER_IP = "10.3.3.100"
AGENT_UID = 10001
CHANNELS = ("win7", "win10", "win11", "ubuntu")
SKIP_DOCKER = os.environ.get("ISTUDY_SKIP_DOCKER") == "1"  # 測試用
PG_MAJOR = os.environ.get("ISTUDY_PG_MAJOR", "18")          # 主機 PostgreSQL 主版本（測試環境可改）
# 測試模式（ISTUDY_ROOT_PREFIX）才接受的替代設定：資料庫名稱、PostgreSQL 連線（host:port:user）
DB_NAME, DB_ROLE, DB_TEST = ((os.environ.get("ISTUDY_TEST_DB_NAME") if PREFIX else None) or "istudy"), "istudy", "istudy_restore_test"
AUX_DBS = (DB_TEST, "istudy_before_restore", "istudy_failed_update")  # 只有這些 iStudy 自己的輔助資料庫可以被刪除
HBA_MARK = "# iStudy（由 istudyctl 加入）：Docker 容器內的 iStudy 經 Unix socket 以密碼登入；必須在其他 local 規則之前"
HBA_RULE = f"local   {DB_NAME},{DB_TEST}   {DB_ROLE}   scram-sha-256"


# ---------------- 輸出（終端機或 JSON 逐行） ----------------

class Out:
    json_mode = False
    log_file: Optional[str] = None
    lines: list[str] = []

    @classmethod
    def emit(cls, event: str, **data) -> None:
        data = {"event": event, "time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), **data}
        text = json.dumps(data, ensure_ascii=False, default=str)
        cls.lines.append(text)
        if cls.json_mode:
            print(text, flush=True)
        else:
            if event == "step":
                mark = {"running": "…", "ok": "✓", "warn": "⚠", "fail": "✗", "skip": "－"}.get(data.get("status"), "·")
                msg = f" {data['message']}" if data.get("message") else ""
                if data.get("status") != "running":
                    print(f"[{data['step']:>2}/14] {data['name']}  {mark}{msg}", flush=True)
            elif event == "check":
                mark = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "－"}.get(data.get("status"), "·")
                print(f"   {mark} {data['name']}" + (f"：{data['message']}" if data.get("message") else ""), flush=True)
            elif event in ("info", "done", "error"):
                print(data.get("message", ""), flush=True)

    @classmethod
    def save_report(cls, name: str) -> str:
        os.makedirs(VARLOG, exist_ok=True)
        path = os.path.join(VARLOG, f"{name}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(cls.lines) + "\n")
        os.chmod(path, 0o640)
        return path



def make_outgo(path: str) -> None:
    """dev.61：建立 /Media/Outgo（只在不存在時）。擁有者與群組同 /Media（放錄音的人可以寫入），2775（不用 777）；
    PHP（唯讀掛載）能列出內容；Agent（uid 10001）由 ACL 寫入逐字稿。"""
    st = os.stat(os.path.dirname(path))
    os.mkdir(path, 0o755)
    os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, 0o2775)

def die(msg: str, code: int = 1) -> None:
    Out.emit("error", message="✗ " + msg)
    sys.exit(code)


def run(cmd: list[str], check: bool = True, input_text: Optional[str] = None, timeout: Optional[int] = None,
        cwd: Optional[str] = None, env: Optional[dict] = None) -> subprocess.CompletedProcess:  # noqa: D401
    p = subprocess.run(cmd, capture_output=True, text=True, input=input_text, timeout=timeout, cwd=cwd, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}… 失敗：{(p.stderr or p.stdout).strip()[-400:]}")
    return p


def require_root() -> None:
    if os.geteuid() != 0 and not PREFIX:
        die("需要系統管理員權限：請用 sudo istudyctl …（或由 Server App 輸入 Ubuntu 系統密碼）", 77)


# ---------------- 主機 PostgreSQL 18 ----------------
# 管理操作（建立角色與資料庫、備份、還原）以主機 postgres 帳號（peer）執行；SQL 一律經標準輸入送入，不出現在指令列。
# 容器內的 iStudy（php、agent）以 istudy 角色＋密碼（SCRAM）經唯讀綁定的 Unix socket 連線。

def pg_cluster() -> dict:
    r = checks.check_postgres(PG_MAJOR)
    if r["status"] != checks.PASS:
        raise RuntimeError(r["message"])
    return r["value"]


def _pg_cmd(tool: str, db: str) -> list[str]:
    t = os.environ.get("ISTUDY_TEST_PG") if PREFIX else None
    if t:   # 測試模式：以 TCP 連測試叢集（密碼由 PGPASSWORD 提供）
        host, port, user = t.split(":")
        return [tool, "-h", host, "-p", port, "-U", user, "-d", db]
    c = pg_cluster()
    return ["runuser", "-u", "postgres", "--", tool, "--cluster", f"{c['version']}/{c['cluster']}", "-d", db]


def pg_admin(sql: Optional[str] = None, db: str = "postgres", file: Optional[str] = None, as_istudy: bool = False,
             check: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    """以 postgres 帳號執行 psql。as_istudy=True 時先 SET ROLE istudy，建立的物件歸 istudy 所有（還原用）。"""
    cmd = _pg_cmd("psql", db) + ["-X", "-q", "-tA", "-v", "ON_ERROR_STOP=1"]
    if as_istudy:
        cmd += ["-c", f"SET ROLE {DB_ROLE}"]
    cmd += ["-f", "-"]
    if file:
        with open(file, "rb") as f:
            raw = subprocess.run(cmd, stdin=f, capture_output=True, timeout=timeout, cwd="/")
        p = subprocess.CompletedProcess(cmd, raw.returncode, raw.stdout.decode("utf-8", "replace"), raw.stderr.decode("utf-8", "replace"))
    else:
        p = run(cmd, check=False, input_text=sql or "", timeout=timeout, cwd="/")
    if check and p.returncode != 0:
        raise RuntimeError("PostgreSQL：" + (p.stderr or p.stdout).strip()[-400:])
    return p


def pg_dump_to(path: str, db: str = DB_NAME) -> None:
    """主機的 pg_dump（純文字 SQL，不含擁有者與權限）；輸出檔權限 600。"""
    with open(path, "w", encoding="utf-8") as f:
        os.chmod(path, 0o600)
        p = subprocess.run(_pg_cmd("pg_dump", db) + ["--no-owner", "--no-privileges"], stdout=f, stderr=subprocess.PIPE,
                           text=True, timeout=3600, cwd="/")
    if p.returncode != 0:
        raise RuntimeError("pg_dump 失敗：" + p.stderr.strip()[-300:])


def pg_socket_dir() -> str:
    d = pg_admin("SHOW unix_socket_directories").stdout.strip().split(",")[0].strip()
    if not d.startswith("/"):
        raise RuntimeError("主機 PostgreSQL 沒有設定 Unix socket 目錄（unix_socket_directories）")
    return d


def socket_ok(sock_dir: str, port: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(os.path.join(sock_dir, f".s.PGSQL.{port}")).st_mode)
    except OSError:
        return False


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """PostgreSQL 的 SCRAM-SHA-256 密碼驗證值（RFC 5802／7677）。送給 ALTER ROLE 的是驗證值，不是密碼本身，
    即使伺服器記錄 SQL 也不會出現密碼。密碼由 secrets.token_urlsafe 產生（ASCII，SASLprep 不改變內容）。"""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def db_owner(name: str) -> str:
    return pg_admin(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '{name}'").stdout.strip()


def create_db(name: str) -> None:
    """建立 iStudy 的資料庫（擁有者 istudy；UTF8＋C.UTF-8，排序依字碼、不受主機語系影響）；其他角色不能連線。"""
    pg_admin(f"CREATE DATABASE {name} OWNER {DB_ROLE} TEMPLATE template0 ENCODING 'UTF8' "
             f"LOCALE_PROVIDER libc LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';\n"
             f"REVOKE ALL ON DATABASE {name} FROM PUBLIC;\n")


def drop_aux_db(name: str) -> None:
    if name not in AUX_DBS:
        raise RuntimeError(f"拒絕刪除資料庫 {name}（只允許 iStudy 的輔助資料庫）")
    pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def ensure_hba() -> str:
    """在主機 pg_hba.conf 最前面加入 iStudy 的 scram-sha-256 規則（只限 istudy 角色與 istudy 的兩個資料庫）。
    第一次修改前備份到 /var/lib/istudy/pg_hba.conf.before-istudy；不動其他規則（postgres 的 peer 管理入口保留）。"""
    path = pg_admin("SHOW hba_file").stdout.strip()
    original = _read(path)
    lines = original.splitlines()
    norm = lambda t: " ".join(t.split())  # noqa: E731
    rules = [i for i, ln in enumerate(lines) if ln.strip() and not ln.strip().startswith("#")]
    if rules and norm(lines[rules[0]]) == norm(HBA_RULE):
        return "pg_hba.conf 已有 iStudy 規則"
    kept = [ln for ln in lines if norm(ln) != norm(HBA_RULE) and ln.strip() != HBA_MARK]
    first = next((i for i, ln in enumerate(kept) if ln.strip() and not ln.strip().startswith("#")), len(kept))
    new = kept[:first] + [HBA_MARK, HBA_RULE] + kept[first:]
    backup = os.path.join(VARLIB, "pg_hba.conf.before-istudy")
    os.makedirs(VARLIB, exist_ok=True)
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    st = os.stat(path)

    def write(text: str) -> None:
        tmp = path + ".istudy.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    write("\n".join(new) + "\n")
    pg_admin("SELECT pg_reload_conf()")
    bad = pg_admin("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").stdout.strip()
    ours = pg_admin(f"SELECT count(*) FROM pg_hba_file_rules WHERE type = 'local' AND database @> ARRAY['{DB_NAME}'] "
                    f"AND user_name @> ARRAY['{DB_ROLE}'] AND auth_method = 'scram-sha-256'").stdout.strip()
    if bad != "0" or ours == "0":
        write(original)
        pg_admin("SELECT pg_reload_conf()", check=False)
        raise RuntimeError(f"pg_hba.conf 更新後檢查失敗，已還原原本的內容（{path}）")
    return f"已在 {path} 最前面加入 iStudy 規則（原檔備份：{backup}）"


def verify_login(password: str, sock_dir: str, port: str) -> None:
    """以一般帳號（nobody，peer 不會通過）用密碼經 Unix socket 登入，確認容器內的連線方式可行。"""
    env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}
    p = run(["runuser", "-u", "nobody", "--", "psql", "-X", "-tA", "-h", sock_dir, "-p", str(port), "-U", DB_ROLE, "-d", DB_NAME,
             "-c", "SELECT current_user"], check=False, env=env, cwd="/")
    if p.returncode != 0 or p.stdout.strip() != DB_ROLE:
        raise RuntimeError("istudy 帳號無法以密碼經 Unix socket 登入：" + (p.stderr or p.stdout).strip()[-300:])


def provision_database(password: str) -> str:
    """主機 PostgreSQL 18：建立／更新 istudy 角色（最小權限）、istudy 與 istudy_restore_test 資料庫、pg_hba 規則。
    可重複執行；既有資料庫不刪除、不重建；擁有者不是 istudy 的同名資料庫或有管理權限的同名角色一律停止。"""
    flags = pg_admin(f"SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls FROM pg_roles "
                     f"WHERE rolname = '{DB_ROLE}'").stdout.strip()
    if flags == "t":
        raise RuntimeError(f"主機 PostgreSQL 已有角色 {DB_ROLE}，但具有 SUPERUSER／CREATEDB／CREATEROLE 等權限，不是 iStudy 建立的；"
                           "為避免影響其他系統，部署停止（請先確認這個角色的用途）")
    for name in (DB_NAME, DB_TEST):
        owner = db_owner(name)
        if owner and owner != DB_ROLE:
            raise RuntimeError(f"主機 PostgreSQL 已有資料庫 {name}，擁有者是 {owner}，不是 iStudy；為避免影響既有資料，部署停止")
    pg_admin(f"""DO $$BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{DB_ROLE}') THEN CREATE ROLE {DB_ROLE} LOGIN; END IF;
END$$;
ALTER ROLE {DB_ROLE} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{scram_verifier(password)}';
""")
    created = []
    for name in (DB_NAME, DB_TEST):
        if not db_owner(name):
            create_db(name)
            created.append(name)
    hba = ensure_hba()
    c = pg_cluster()
    verify_login(password, pg_socket_dir(), str(c["port"]))
    ver = pg_admin("SHOW server_version").stdout.strip()
    return (f"PostgreSQL {ver}（{c['version']}/{c['cluster']}）；角色 {DB_ROLE} 已設定（最小權限）；"
            + ("建立資料庫 " + "、".join(created) if created else "資料庫已存在，未覆蓋") + "；" + hba)


def check_db(wait: int = 90) -> bool:
    """開機時 istudy.service 的 ExecStartPre：等主機 PostgreSQL 的 Unix socket 出現（不登入資料庫）。"""
    env = read_env()
    sock, port = env.get("PG_SOCKET_DIR", "/var/run/postgresql"), env.get("DB_PORT", "5432")
    end = time.time() + wait
    while True:
        if socket_ok(sock, port):
            print(f"✓ 主機 PostgreSQL socket：{sock}/.s.PGSQL.{port}")
            return True
        if time.time() >= end:
            print(f"✗ {wait} 秒內沒有看到主機 PostgreSQL 的 socket（{sock}/.s.PGSQL.{port}）：sudo systemctl status postgresql",
                  file=sys.stderr)
            return False
        time.sleep(2)


# ---------------- Docker Compose ----------------

def compose_cmd(*args: str) -> list[str]:
    return ["docker", "compose", "--project-directory", OPT, "--env-file", os.path.join(OPT, ".env"), "-f",
            os.path.join(OPT, "compose.yaml"), *args]


def compose(*args: str, check: bool = True, input_text: Optional[str] = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return run(compose_cmd(*args), check=check, input_text=input_text, timeout=timeout)


def php_cli(*args: str, input_text: Optional[str] = None) -> dict:
    p = compose("exec", "-T", "php", "php", "/var/www/html/cli/istudy.php", *args, check=False, input_text=input_text)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": (p.stderr or p.stdout).strip()[-400:]}


def read_env() -> dict:
    env = {}
    try:
        with open(os.path.join(OPT, ".env"), encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k] = v
    except OSError:
        pass
    return env


def wait_health(url: str, timeout: int = 90) -> Optional[dict]:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                d = json.loads(r.read().decode())
                if d.get("ok"):
                    return d
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return None


def http_status(url: str) -> Optional[int]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


# ---------------- 預檢（bootstrap 步驟 2） ----------------

def preflight(media_root: str = MEDIA, bundle: Optional[str] = None) -> list[dict]:
    has_debs = bool(bundle and glob.glob(os.path.join(bundle, "debs", "docker-ce_*.deb")))
    res = [
        checks.check_os(os.environ.get("ISTUDY_EXPECT_UBUNTU", "26.04")),
        checks.check_media_mount(media_root, require_mount=os.environ.get("ISTUDY_REQUIRE_MOUNT", "1") != "0"),
        checks.check_system_disk("/", int(os.environ.get("ISTUDY_MIN_SYSTEM_GB", "30"))),
        checks.check_ram(8),
        checks.check_ports(tuple(int(x) for x in os.environ.get("ISTUDY_PREFLIGHT_PORTS", "4080,4081").split(","))),
        (checks.result("postgres", f"PostgreSQL {PG_MAJOR}（主機）", checks.SKIP, "測試模式") if SKIP_DOCKER
         else checks.check_postgres(PG_MAJOR)),
        (checks.result("docker", "Docker Compose", checks.SKIP, "測試模式") if SKIP_DOCKER
         else checks.check_docker_compose(has_debs)),
        checks.check_ip(DEFAULT_SERVER_IP),
        checks.check_dri(),
        checks.check_ntp(),
        checks.check_zfs(),
        checks.check_media_free(media_root, 20),
        checks.check_desktop(),
        checks.check_polkit_agent(),
    ]
    # ZFS DEGRADED 只警告；沒有 zpool 指令時不擋
    return res


# ---------------- bootstrap ----------------

class State:
    def __init__(self) -> None:
        self.data = {"steps": {}}
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass

    def done(self, step: int) -> bool:
        return self.data["steps"].get(str(step), {}).get("status") in ("ok", "warn", "skip")

    def mark(self, step: int, status: str, message: str = "") -> None:
        self.data["steps"][str(step)] = {"status": status, "message": message, "at": _dt.datetime.now().isoformat(timespec="seconds")}
        os.makedirs(VARLIB, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o644)  # Server App（一般 OS 帳號）要讀取部署進度；內容不含密碼與金鑰
        os.replace(tmp, STATE_FILE)


STEPS = {1: "驗證套件", 2: "預檢", 3: "安裝 Docker", 4: "系統設定", 5: "目錄與媒體標記", 6: "產生設定", 7: "載入映像",
         8: "資料庫", 9: "建立 Super", 10: "開機啟動", 11: "啟動服務", 12: "Client 安裝檔", 13: "自我檢查", 14: "報告"}


def verify_bundle(bundle: str) -> int:
    sums = os.path.join(bundle, "SHA256SUMS")
    if not os.path.isfile(sums):
        raise RuntimeError("套件缺少 SHA256SUMS")
    n = 0
    with open(sums, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            digest, name = line.split(None, 1)
            name = name.lstrip("*")
            full = os.path.normpath(os.path.join(bundle, name))
            if not full.startswith(os.path.normpath(bundle) + os.sep):
                raise RuntimeError(f"SHA256SUMS 含有不安全的路徑：{name}")
            h = hashlib.sha256()
            try:
                with open(full, "rb") as g:
                    for chunk in iter(lambda: g.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                raise RuntimeError(f"缺少檔案：{name}（請重新複製 USB）")
            if h.hexdigest() != digest:
                raise RuntimeError(f"檔案內容不符：{name}（請重新複製 USB）")
            n += 1
    return n


def bundle_version(bundle: str) -> dict:
    with open(os.path.join(bundle, "VERSION.json"), encoding="utf-8") as f:
        return json.load(f)


def step_install_docker(bundle: str) -> str:
    if SKIP_DOCKER:
        return "略過（測試模式）"
    if shutil.which("docker"):
        p = run(["docker", "compose", "version", "--short"], check=False)
        if p.returncode == 0:
            run(["systemctl", "enable", "--now", "docker"], check=False)
            note = ""
            if not shutil.which("smartctl"):
                try:
                    _install_debs(bundle, ["smartmontools"])
                except RuntimeError:
                    note = "；沒有 smartmontools，硬碟 SMART 檢查略過（sudo apt install smartmontools）"
            return f"已安裝（Compose {p.stdout.strip()}），略過" + note
    names = ["containerd.io", "docker-ce-cli", "docker-ce", "docker-buildx-plugin", "docker-compose-plugin", "smartmontools"]
    _install_debs(bundle, names)
    run(["systemctl", "enable", "--now", "docker"])
    return "已從套件安裝"


def _install_debs(bundle: str, prefixes: list[str]) -> None:
    debs = []
    for pre in prefixes:
        debs += sorted(glob.glob(os.path.join(bundle, "debs", f"{pre}_*.deb")))
    if not debs:
        raise RuntimeError("套件內找不到 Docker 安裝檔（debs/）")
    run(["dpkg", "-i", *debs], timeout=900)


def step_system_settings() -> str:
    msgs = []
    if not PREFIX:
        run(["systemctl", "mask", "sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target"], check=False)
        msgs.append("已停用睡眠與休眠")
        p = run(["getent", "group", "istudy"], check=False)
        if p.returncode != 0:
            run(["groupadd", "-g", str(AGENT_UID), "istudy"], check=False)
    gid = render_gid()
    msgs.append(f"render 群組 ID {gid}")
    return "；".join(msgs)


def render_gid() -> int:
    """以 /dev/dri/renderD128 實際的群組 ID 為準；沒有裝置時才用 render 群組，最後才用 109。"""
    try:
        return os.stat("/dev/dri/renderD128").st_gid
    except OSError:
        try:
            import grp
            return grp.getgrnam("render").gr_gid
        except (KeyError, ImportError):
            return 109


def step_dirs_and_marker(media_root: str) -> str:
    for d in (OPT, ETC, VARLIB, VARLOG, os.path.join(SRV, "backup")):
        os.makedirs(d, exist_ok=True)
    for ch in CHANNELS:
        os.makedirs(os.path.join(SRV, "updates", ch), exist_ok=True)
    os.makedirs(os.path.join(OPT, "models"), exist_ok=True)
    if not PREFIX:
        os.chmod(OPT, 0o755)
        os.chown(OPT, 0, 0)
        # 備份放在系統 SSD 的 /srv/istudy/backup：Agent（uid 10001）每日備份需要寫入
        os.chown(os.path.join(SRV, "backup"), AGENT_UID, AGENT_UID)
        os.chmod(os.path.join(SRV, "backup"), 0o770)
    created = []
    acl_warn = ""
    for sub in ("playback", "subtitles", "doc-cache", "Outgo"):   # dev.59：教材直接放在課程資料夾；dev.61：Outgo 錄音轉文字工作區
        p = os.path.join(media_root, sub)
        if not os.path.isdir(p) and not os.path.islink(p):
            os.makedirs(p)
            created.append(sub)
    # Agent（uid 10001）需要寫入播放檔、字幕、文件快取；刪除原檔需要教材資料夾的寫入權限：
    # 在 /Media 根目錄加 ACL 與預設 ACL（不遞迴），之後放進來的課程資料夾會繼承；已有的內容用 istudyctl media-acl --fix
    if not PREFIX:
        for sub in ("playback", "subtitles", "doc-cache"):
            os.chown(os.path.join(media_root, sub), AGENT_UID, AGENT_UID)
            os.chmod(os.path.join(media_root, sub), 0o2775)
        if "Outgo" in created:
            os.rmdir(os.path.join(media_root, "Outgo"))      # 剛建立的空資料夾：改用與 /Media 相同的擁有者重建
            make_outgo(os.path.join(media_root, "Outgo"))
        acl_ok = False
        if shutil.which("setfacl"):
            acl_ok = run(["setfacl", "-m", f"g:{AGENT_UID}:rwx", "-m", f"d:g:{AGENT_UID}:rwX", media_root],
                         check=False, timeout=60).returncode == 0
            outgo = os.path.join(media_root, "Outgo")
            if os.path.isdir(outgo) and not os.path.islink(outgo):   # Outgo：Agent 要能寫逐字稿；放錄音的人照 /Media 的預設 ACL
                run(["setfacl", "-m", f"g:{AGENT_UID}:rwx", "-m", f"d:g:{AGENT_UID}:rwX", outgo], check=False, timeout=60)
        if not acl_ok:
            # 不自動修改 ZFS 屬性或檔案擁有者；只提示（其餘功能不受影響）
            acl_warn = "；⚠ /Media 無法設定 ACL，之後無法刪除原檔（見 DEPLOY.md「ZFS ACL」）"
    if not os.path.exists(HOSTCHECK_FILE):
        with open(HOSTCHECK_FILE, "w") as f:
            f.write("{}")
    marker = os.path.join(media_root, ".istudy-media")
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            install_id = f.read().strip()
        if not re.fullmatch(r"[a-f0-9]{32}", install_id):
            raise RuntimeError(f"{marker} 內容格式錯誤")
        msg = "媒體標記已存在"
    else:
        install_id = secrets.token_hex(16)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(install_id)
        os.chmod(marker, 0o644)
        msg = "已寫入媒體標記"
    with open(os.path.join(ETC, "install_id"), "w") as f:
        f.write(install_id)
    return msg + ("；建立 " + "、".join(created) if created else "；既有目錄不變動") + acl_warn


DRI_LINES = ('    group_add:\n', '      - "${RENDER_GID:-109}"', '    devices:\n', '      - /dev/dri:/dev/dri\n')


def install_compose(src: str, dst: str, dri: Optional[bool] = None) -> bool:
    """複製 compose.yaml；主機沒有 /dev/dri（沒有內顯或 BIOS 未啟用）時拿掉 agent 的 devices／group_add，
    否則 docker compose up 會因找不到裝置而失敗。回傳是否保留 /dev/dri。"""
    if dri is None:
        dri = os.path.exists("/dev/dri/renderD128")
    with open(src, encoding="utf-8") as f:
        lines = f.readlines()
    if not dri:
        lines = [ln for ln in lines if not any(ln.startswith(d) for d in DRI_LINES)]
    with open(dst, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return dri


def step_config(bundle: Optional[str]) -> str:
    env_path = os.path.join(OPT, ".env")
    if bundle:
        stack = os.path.join(bundle, "stack")
        install_compose(os.path.join(stack, "compose.yaml"), os.path.join(OPT, "compose.yaml"))
        dst = os.path.join(OPT, "server", "nginx")
        os.makedirs(dst, exist_ok=True)
        for f in glob.glob(os.path.join(stack, "nginx", "*.conf")):
            shutil.copy2(f, dst)
        shutil.copy2(os.path.join(bundle, "VERSION.json"), os.path.join(OPT, "VERSION.json"))
        models = os.path.join(bundle, "models")
        if os.path.isdir(models):
            for m in os.listdir(models):
                src = os.path.join(models, m)
                if os.path.isdir(src) and not os.path.isdir(os.path.join(OPT, "models", m)):
                    shutil.copytree(src, os.path.join(OPT, "models", m))
    version = _json(os.path.join(OPT, "VERSION.json")).get("server", "dev")
    if os.path.exists(env_path):
        env = read_env()
        env["ISTUDY_VERSION"] = version
        _write_env(env)
        return ".env 已存在，沿用（只更新版本號）"
    env = {
        "DB_PASSWORD": secrets.token_urlsafe(30),
        "ISTUDY_PORT": "4080",
        "ISTUDY_VERSION": version,
        "RENDER_GID": str(render_gid()),
        "MEDIA_ROOT": MEDIA,
        "ISTUDY_ENCODER": "auto",
        "PG_SOCKET_DIR": "/var/run/postgresql",
        "DB_PORT": "5432",
    }
    _write_env(env)
    return "已產生 .env（密碼不顯示）"


def _write_env(env: dict) -> None:
    path = os.path.join(OPT, ".env")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# 由 istudyctl 產生；權限 640（root:istudy），Agent 備份時需要讀取\n")
        for k, v in env.items():
            f.write(f"{k}={v}\n")
    os.chmod(tmp, 0o640)
    if not PREFIX:
        os.chown(tmp, 0, AGENT_UID)
    os.replace(tmp, path)


def step_load_images(bundle: str) -> str:
    if SKIP_DOCKER:
        return "略過（測試模式）"
    tar_path = os.path.join(bundle, "images", "istudy-images.tar")
    manifest = _json(os.path.join(bundle, "images", "manifest.json"))

    def present() -> bool:
        for ref, image_id in manifest.items():
            p = run(["docker", "image", "inspect", "--format", "{{.Id}}", ref], check=False)
            if p.returncode != 0 or p.stdout.strip() != image_id:
                return False
        return True
    if present():
        return f"{len(manifest)} 個映像已載入且相符，略過"
    run(["docker", "load", "-i", tar_path], timeout=3600)
    bad = []
    for ref, image_id in manifest.items():
        p = run(["docker", "image", "inspect", "--format", "{{.Id}}", ref], check=False)
        if p.returncode != 0 or p.stdout.strip() != image_id:
            bad.append(ref)
    if bad:
        raise RuntimeError("映像 ID 與套件清單不符：" + "、".join(bad))
    return f"已載入 {len(manifest)} 個映像"


def step_database(restore_file: Optional[str]) -> str:
    if SKIP_DOCKER:
        return "略過（測試模式）"
    env = read_env()
    c = pg_cluster()
    sock, port = pg_socket_dir(), str(c["port"])
    if not socket_ok(sock, port):
        raise RuntimeError(f"找不到主機 PostgreSQL 的 Unix socket：{sock}/.s.PGSQL.{port}")
    if (env.get("PG_SOCKET_DIR"), env.get("DB_PORT")) != (sock, port):
        env["PG_SOCKET_DIR"], env["DB_PORT"] = sock, port
        _write_env(env)
    pmsg = provision_database(env["DB_PASSWORD"])
    Out.emit("info", message="主機資料庫：" + pmsg)
    compose("up", "-d", "--no-build", "php")
    time.sleep(2)
    if restore_file:
        msg = restore(restore_file, "prod", from_bootstrap=True)
        r = php_cli("migrate")
        if r.get("applied"):
            msg += "；已套用 migration：" + "、".join(r["applied"])
    else:
        r = php_cli("migrate")
        if not r.get("ok"):
            raise RuntimeError("建立資料庫結構失敗：" + str(r.get("error")))
        msg = "全新資料庫已建立結構" if r.get("fresh") else ("已套用 migration：" + "、".join(r["applied"]) if r.get("applied") else "資料庫已有資料，未覆蓋")
    install_id = _read(os.path.join(ETC, "install_id")).strip()
    r = php_cli("install-id", install_id)
    if not r.get("ok"):
        raise RuntimeError("安裝識別碼不符：" + str(r.get("error")) + "（/Media 可能是另一台主機的磁碟）")
    return msg


def step_super(username: Optional[str], password_lines: Optional[list[str]]) -> str:
    if SKIP_DOCKER:
        return "略過（測試模式）"
    st = php_cli("status")
    if st.get("has_super"):
        return "已有 Super，略過"
    username = username or (input("Super 帳號：").strip() if sys.stdin.isatty() else "")
    if not username:
        raise RuntimeError("請輸入 Super 帳號")
    if password_lines is None:
        if not sys.stdin.isatty():
            raise RuntimeError("沒有收到 Super 密碼")
        a = getpass.getpass("Super 密碼（至少 10 字）：")
        b = getpass.getpass("再輸入一次：")
        password_lines = [a, b]
    r = php_cli("create-super", username, input_text="\n".join(password_lines[:2]) + "\n")
    if not r.get("ok"):
        raise RuntimeError(str(r.get("error")))
    return f"已建立 Super：{username}"


# dev.62：加密備份的主機排程（解密驗證、每 3 天上傳、每日清理）；Agent 每日建立備份的時間不變（排程設定）
BACKUP_UNITS = {
    "istudy-backup.service": """[Unit]
Description=iStudy 加密備份：解密驗證、遠端上傳（每 3 天）、保留清理
After=postgresql.service docker.service
Wants=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/istudyctl backup-run
TimeoutStartSec=4h
Nice=10
IOSchedulingClass=idle
""",
    "istudy-backup.timer": """[Unit]
Description=每小時檢查 iStudy 加密備份（新備份解密驗證、到期上傳、每日清理）

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
""",
}


def write_units() -> None:
    os.makedirs(SYSTEMD, exist_ok=True)
    units = {
        "istudy.service": f"""[Unit]
Description=iStudy Server（Docker Compose：php／nginx／agent；資料庫為主機 PostgreSQL 18）
Requires=docker.service
After=docker.service network-online.target zfs-mount.service postgresql.service
Wants=network-online.target postgresql.service
RequiresMountsFor={MEDIA}

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/istudy
# /Media 未掛載或媒體標記不符時，整組服務不啟動（第 17 章）
ExecStartPre=/usr/local/sbin/istudyctl check-media
# 主機 PostgreSQL 的 Unix socket 出現後才啟動容器（最多等 90 秒）
ExecStartPre=/usr/local/sbin/istudyctl check-db
ExecStart=/usr/bin/docker compose --project-directory /opt/istudy --env-file /opt/istudy/.env -f /opt/istudy/compose.yaml up -d --no-build --remove-orphans
ExecStop=/usr/bin/docker compose --project-directory /opt/istudy --env-file /opt/istudy/.env -f /opt/istudy/compose.yaml stop
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
""",
        "istudy-hostcheck.service": """[Unit]
Description=iStudy 主機檢查（ZFS、SMART、容器狀態、磁碟）

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/istudyctl hostcheck
""",
        "istudy-hostcheck.timer": """[Unit]
Description=每 10 分鐘執行 iStudy 主機檢查

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
""",
        **BACKUP_UNITS,
    }
    for name, text in units.items():
        with open(os.path.join(SYSTEMD, name), "w", encoding="utf-8") as f:
            f.write(text)


def step_boot() -> str:
    write_units()
    if PREFIX:
        return "已寫入 systemd 設定（測試模式未啟用）"
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "istudy.service", "istudy-hostcheck.timer", "istudy-backup.timer"])
    run(["systemctl", "start", "istudy-hostcheck.timer", "istudy-backup.timer"], check=False)
    return "istudy.service、istudy-hostcheck.timer、istudy-backup.timer 已啟用"


def step_start() -> str:
    if SKIP_DOCKER:
        return "略過（測試模式）"
    hostcheck(quiet=True)
    p = run(["systemctl", "restart", "istudy.service"], check=False, timeout=400) if not PREFIX else None
    if p is None or p.returncode != 0:
        compose("up", "-d", "--no-build", "--remove-orphans")
    ps = container_states()
    bad = [n for n, s in ps.items() if s != "running"]
    if bad:
        logs = compose("logs", "--tail", "50", *bad, check=False).stdout[-3000:]
        raise RuntimeError(f"容器未就緒：{'、'.join(bad)}\n{logs}")
    return "php、nginx、agent 已啟動（資料庫：主機 PostgreSQL）"


def container_states() -> dict:
    p = compose("ps", "--all", "--format", "json", check=False)
    states = {}
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items = json.loads(line)
        except ValueError:
            continue
        for it in items if isinstance(items, list) else [items]:
            states[it.get("Service")] = it.get("State")
    return states


CLIENT_MANIFESTS = ("latest.yml", "latest.json")   # Client 讀的更新描述檔：最後才換，Client 不會看到「有描述檔、沒有安裝檔」
CLIENT_VER = r"(\d+\.\d+\.\d+(?:-dev\.\d+)?)"
CLIENT_INSTALLER = {
    "win7": re.compile(rf"^iStudy-Client-Win7-{CLIENT_VER}\.exe$"), "win10": re.compile(rf"^iStudy-Client-Win10-{CLIENT_VER}\.exe$"),
    "win11": re.compile(rf"^iStudy-Client-Win11-{CLIENT_VER}\.exe$"), "ubuntu": re.compile(rf"^istudy-client-ubuntu_{CLIENT_VER}_amd64\.deb$"),
}


def copy_client_files(src: str, dst: str, names: list[str]) -> int:
    """安裝檔先、描述檔最後；每個檔先寫暫存檔再改名（Client 不會下載到寫一半的檔案）。同大小的安裝檔略過。"""
    os.makedirs(dst, exist_ok=True)
    order = [n for n in names if n not in CLIENT_MANIFESTS] + [n for n in names if n in CLIENT_MANIFESTS]
    copied = 0
    for f in order:
        target = os.path.join(dst, f)
        if f not in CLIENT_MANIFESTS and os.path.exists(target) and os.path.getsize(target) == os.path.getsize(os.path.join(src, f)):
            continue
        tmp = os.path.join(dst, f".{f}.tmp")
        shutil.copy2(os.path.join(src, f), tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        copied += 1
    return copied


def check_client_channel(ch: str, d: str) -> tuple[Optional[str], list[str], list[str]]:
    """一個版別資料夾 → (版本, 要發布的檔案, 問題)。Windows 核對 latest.yml 的 SHA512；Ubuntu 核對 latest.json 的 SHA256 與大小。"""
    problems: list[str] = []
    if ch == "ubuntu":
        try:
            with open(os.path.join(d, "latest.json"), encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError) as e:
            return None, [], [f"沒有或無法讀取 latest.json（{e}）：Ubuntu Client 無法自動更新"]
        inst = str(m.get("file") or "")
        mm = CLIENT_INSTALLER[ch].match(inst)
        if not mm or mm.group(1) != m.get("version"):
            return None, [], ["latest.json 的檔名與版本不符"]
        full = os.path.join(d, inst)
        if not os.path.isfile(full):
            return None, [], [f"找不到 {inst}"]
        if os.path.getsize(full) != m.get("size"):
            problems.append("大小與 latest.json 不符")
        elif sha256_file(full) != m.get("sha256"):
            problems.append("SHA256 與 latest.json 不符")
        return m.get("version"), [inst, "latest.json"], problems
    try:
        with open(os.path.join(d, "latest.yml"), encoding="utf-8") as f:
            top = dict(re.findall(r"^(version|path|sha512):\s*['\"]?([^'\"\n]+?)['\"]?\s*$", f.read(), re.M))
    except OSError as e:
        return None, [], [f"沒有 latest.yml（{e}）"]
    inst = top.get("path", "")
    mm = CLIENT_INSTALLER[ch].match(inst)
    if not mm or mm.group(1) != top.get("version"):
        return None, [], ["latest.yml 的安裝檔名稱與版本不符"]
    full = os.path.join(d, inst)
    if not os.path.isfile(full):
        return None, [], [f"找不到 {inst}"]
    h = hashlib.sha512()
    with open(full, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    if base64.b64encode(h.digest()).decode() != top.get("sha512"):
        problems.append("SHA512 與 latest.yml 不符")
    files = [inst] + ([inst + ".blockmap"] if os.path.isfile(full + ".blockmap") else []) + ["latest.yml"]
    return top.get("version"), files, problems


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def clients_publish(src: str) -> int:
    """dev.61：把新版 Client 安裝檔發布到 /srv/istudy/updates/<版別>/，各台 Client 會自己下載更新（Super 不用逐台安裝）。
    src＝原始碼包的 dist/clients（裡面有 win7、win10、win11、ubuntu 資料夾，有哪個發布哪個）。核對不過的版別不發布。"""
    require_root()
    src = os.path.realpath(src)
    found = [ch for ch in CHANNELS if os.path.isdir(os.path.join(src, ch))]
    if not found:
        Out.emit("done", status="fail", message=f"{src} 裡沒有 win7、win10、win11、ubuntu 任何一個資料夾（請選原始碼包的 dist/clients）")
        return 2
    ok = bad = 0
    for ch in found:
        d = os.path.join(src, ch)
        version, files, problems = check_client_channel(ch, d)
        if problems or not version:
            bad += 1
            Out.emit("log", message=f"✗ {ch}：沒有發布（{'；'.join(problems)}）")
            continue
        n = copy_client_files(d, os.path.join(SRV, "updates", ch), files)
        ok += 1
        Out.emit("log", message=f"✓ {ch}：已發布 {version}（{n} 個檔案；Client 下次檢查時自動下載）")
    st = "ok" if ok and not bad else "warn" if ok else "fail"
    Out.emit("done", status=st, message=f"Client 發布完成：成功 {ok} 個版別" + (f"、失敗 {bad} 個" if bad else ""))
    return 0 if ok and not bad else 1


def step_clients(bundle: str) -> str:
    src = os.path.join(bundle, "clients")
    if not os.path.isdir(src):
        return "套件沒有 Client 安裝檔"
    copied = 0
    for ch in CHANNELS:
        d = os.path.join(src, ch)
        if os.path.isdir(d):
            copied += copy_client_files(d, os.path.join(SRV, "updates", ch), sorted(os.listdir(d)))
    return f"已更新 {copied} 個檔案" if copied else "同版本已存在，略過"


def media_access_report() -> dict:
    """dev.59：在 Agent 容器裡以 uid 10001 實際測試教材權限（測試模式在本機執行）。"""
    if PREFIX or SKIP_DOCKER:
        from istudy_common.mediaacl import scan_access
        return scan_access(MEDIA)
    q = compose("exec", "-T", "agent", "python3", "-c",
                "import sys,json;sys.path.insert(0,'/opt/istudy-agent');from istudy_common.mediaacl import scan_access;"
                "print(json.dumps(scan_access('/Media'),ensure_ascii=False))", check=False, timeout=600)
    try:
        return json.loads(q.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (q.stderr or q.stdout or "Agent 沒有回應").strip()[-300:]}


def check_media_acl() -> dict:
    """自我檢查「教材權限（/Media）」：Agent 能不能讀取全部教材、刪除已轉檔的原檔。"""
    name = "教材權限（/Media，Agent uid 10001）"
    rep = media_access_report()
    if rep.get("error"):
        return checks.result("media_acl", name, checks.WARN, "無法檢查：" + rep["error"])
    if rep.get("ok"):
        return checks.result("media_acl", name, checks.PASS, f"{rep['dirs']} 個資料夾、{rep['files']} 個檔案", rep.get("counts"))
    from istudy_common.mediaacl import summary_lines
    bad = [ln for ln in summary_lines(rep) if ln.startswith(("✗", "⚠"))]
    return checks.result("media_acl", name, checks.WARN, "；".join(bad)[:400] + "（sudo istudyctl media-acl 查看，--fix 修復）", rep.get("counts"))


def media_acl(fix: bool) -> int:
    """dev.59 istudyctl media-acl：預設只檢查；--fix 在確認後只用 setfacl 補上 Agent 權限（不碰系統資料夾、不改檔案內容、不 chmod 777）。"""
    from istudy_common.mediaacl import library_entries, summary_lines
    rep = media_access_report()
    if rep.get("error"):
        Out.emit("done", status="fail", message="無法檢查：" + rep["error"])
        return 2
    for ln in summary_lines(rep):
        Out.emit("log", message=ln)
    Out.emit("media_acl", report=rep)
    if not fix:
        Out.emit("done", status="pass" if rep.get("ok") else "warn",
                 message="教材權限正常" if rep.get("ok") else "有權限問題；確認後執行 sudo istudyctl media-acl --fix 修復")
        return 0 if rep.get("ok") else 1
    require_root()
    targets = library_entries(MEDIA)
    sticky = (rep.get("examples") or {}).get("sticky", [])
    if (rep.get("counts") or {}).get("sticky", 0) > len(sticky):   # 例子只列 20 個：重新找出全部 sticky 資料夾
        sticky = [os.path.relpath(r, MEDIA) for t in targets for r, _, _ in os.walk(os.path.join(MEDIA, t))
                  if os.lstat(r).st_mode & stat.S_ISVTX]
    Out.emit("log", message=f"將修復：{MEDIA}（只改這一層的 ACL）＋第一層 {len(targets)} 個教材項目（遞迴加 Agent 的 ACL）"
                            + (f"；移除 {len(sticky)} 個資料夾的 sticky bit" if sticky else "")
                            + "；Outgo（沒有就建立，遞迴加 Agent 的 ACL）。其他系統資料夾不碰、不改檔案內容。")
    if os.environ.get("ISTUDY_MEDIA_ACL_CONFIRM") != "修復":
        if not sys.stdin.isatty():
            die("需要確認：請在終端機執行，並在提示時輸入「修復」", 64)
        if input("輸入「修復」繼續，其他任何輸入都會停止：").strip() != "修復":
            Out.emit("done", status="ok", message="已停止，沒有修改任何權限")
            return 0
    if not shutil.which("setfacl"):
        die("沒有 setfacl（sudo apt install acl）", 2)
    errs = []
    p = run(["setfacl", "-m", f"g:{AGENT_UID}:rwx", "-m", f"d:g:{AGENT_UID}:rwX", MEDIA], check=False, timeout=60)
    if p.returncode != 0:
        errs.append(f"{MEDIA}：{p.stderr.strip()[-200:]}")
    for t in targets:
        full = os.path.join(MEDIA, t)
        args = ["setfacl", "-R", "-m", f"g:{AGENT_UID}:rwX", "-m", f"d:g:{AGENT_UID}:rwX", full] if os.path.isdir(full) \
            else ["setfacl", "-m", f"g:{AGENT_UID}:rwX", full]
        p = run(args, check=False, timeout=7200)
        if p.returncode != 0:
            errs.append(f"{t}：{p.stderr.strip()[-200:]}")
    for rel in sticky:
        full = os.path.join(MEDIA, rel)
        if rel.split("/", 1)[0] in targets and os.path.isdir(full) and not os.path.islink(full):
            os.chmod(full, stat.S_IMODE(os.lstat(full).st_mode) & ~stat.S_ISVTX)
    # dev.61：Outgo（不存在就建立；已存在的內容不動，只補 Agent 的 ACL；是符號連結就不碰）
    outgo = os.path.join(MEDIA, "Outgo")
    if os.path.islink(outgo):
        errs.append(f"{outgo} 是符號連結：沒有處理（請改成一般資料夾）")
    else:
        if not os.path.isdir(outgo):
            make_outgo(outgo)
        p = run(["setfacl", "-R", "-m", f"g:{AGENT_UID}:rwX", "-m", f"d:g:{AGENT_UID}:rwX", outgo], check=False, timeout=7200)
        if p.returncode != 0:
            errs.append(f"Outgo：{p.stderr.strip()[-200:]}")
    after = media_access_report()
    for ln in summary_lines(after):
        Out.emit("log", message="修復後：" + ln)
    ok = not errs and after.get("ok")
    Out.emit("done", status="ok" if ok else "warn",
             message="已修復，Agent 可以讀取並刪除原檔" if ok else "部分無法修復：" + "；".join(errs)[:400] if errs else "仍有問題（見上方）；ZFS 需 acltype=posixacl")
    return 0 if ok else 1


ENCODER_CHOICES = ("auto", "qsv", "vaapi", "cpu")


def encoder_cmd(choice: Optional[str]) -> int:
    """dev.59 istudyctl encoder：查看或指定正式轉檔用的編碼器（寫在 .env 的 ISTUDY_ENCODER，Agent 重建後生效）。"""
    env = read_env()
    cur = env.get("ISTUDY_ENCODER") or "auto"
    hb_enc = None
    try:
        p = pg_admin("SELECT value->>'encoder' FROM server_config WHERE key='agent_heartbeat'", db=DB_NAME, check=False, timeout=30)
        hb_enc = (p.stdout or "").strip() or None
    except Exception:  # noqa: BLE001
        pass
    if not choice:
        Out.emit("done", status="ok", message=f"設定 ISTUDY_ENCODER={cur}；Agent 目前使用：{hb_enc or '未知（沒有心跳）'}")
        return 0
    require_root()
    env["ISTUDY_ENCODER"] = choice
    _write_env(env)
    if not SKIP_DOCKER:
        compose("up", "-d", "--no-build", "agent", timeout=300)
    Out.emit("done", status="ok", message=f"已設定 ISTUDY_ENCODER={choice}（原本 {cur}）；Agent 已重新建立，啟動時會重新檢測編碼器")
    return 0


def doctor_checks() -> list[dict]:
    """自我檢查（bootstrap 步驟 13 與 istudyctl doctor）。"""
    res = []
    if SKIP_DOCKER:
        return [checks.result("docker", "Docker", checks.SKIP, "測試模式")]
    pgc = checks.check_postgres(PG_MAJOR)
    env = read_env()
    sock, port = env.get("PG_SOCKET_DIR", "/var/run/postgresql"), env.get("DB_PORT", "5432")
    if pgc["status"] == checks.PASS and not socket_ok(sock, port):
        pgc = checks.result("postgres", pgc["name"], checks.FAIL, f"找不到 Unix socket {sock}/.s.PGSQL.{port}")
    res.append(pgc)
    v = _json(os.path.join(OPT, "VERSION.json"))
    h = wait_health("http://127.0.0.1:4081/api/health", 60)
    res.append(checks.result("api", "API 與資料庫", checks.PASS if h and h.get("version") == v.get("server") else checks.FAIL,
                             "" if h else "127.0.0.1:4081/api/health 沒有回應", h))
    lan = http_status(f"http://{DEFAULT_SERVER_IP}:4080/api/health")
    res.append(checks.result("lan", "LAN 服務", checks.PASS if lan == 200 else checks.WARN,
                             "" if lan == 200 else f"{DEFAULT_SERVER_IP}:4080 沒有回應（IP 未設定？）"))
    adm_lan = http_status(f"http://{DEFAULT_SERVER_IP}:4081/api/health")
    adm_404 = http_status(f"http://{DEFAULT_SERVER_IP}:4080/api/admin/overview")
    iso_ok = adm_lan is None and adm_404 in (404, None)
    res.append(checks.result("admin_isolation", "管理 API 隔離", checks.PASS if iso_ok else checks.FAIL,
                             "" if iso_ok else "LAN 上可以連到管理 API！"))
    agent = _agent_heartbeat_age()
    res.append(checks.result("agent", "Agent 心跳", checks.PASS if agent is not None and agent <= 90 else checks.FAIL,
                             "" if agent is not None and agent <= 90 else "Agent 90 秒內沒有心跳", agent))
    marker_ok = check_media(quiet=True)
    res.append(checks.result("marker", "媒體標記", checks.PASS if marker_ok else checks.FAIL, "" if marker_ok else "標記與資料庫不符"))
    # dev.55 HW-01：與 Agent 用同一段檢測程式（worker.media_tools.probe_encoders），QSV／VA-API／CPU 分開列出
    q = compose("exec", "-T", "agent", "python3", "-c",
                "import sys,json;sys.path.insert(0,'/opt/istudy-agent');from worker.media_tools import probe_encoders;"
                "print(json.dumps(probe_encoders(),ensure_ascii=False))", check=False, timeout=180)
    try:
        enc = json.loads(q.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        enc = {}
    hw = [e for e in ("qsv", "vaapi") if (enc.get(e) or {}).get("ok")]
    for e, name in (("qsv", "Quick Sync（h264_qsv）"), ("vaapi", "VA-API（h264_vaapi）")):
        r = enc.get(e) or {}
        if r.get("ok"):
            res.append(checks.result(e, name, checks.PASS, r.get("message") or ""))
        else:
            res.append(checks.result(e, name, checks.SKIP if hw else checks.WARN,
                                     (r.get("message") or "無法檢測") + ("（不影響：已有可用的硬體編碼）" if hw else "")))
    # dev.59：依 .env 的 ISTUDY_ENCODER 決定（與 Agent 的 choose_encoder 相同）；指定的編碼器沒通過檢測時要說清楚
    forced = (read_env().get("ISTUDY_ENCODER") or "auto").strip()
    label = {"qsv": "Quick Sync", "vaapi": "VA-API", "cpu": "CPU"}
    if forced in ("qsv", "vaapi"):
        ok = (enc.get(forced) or {}).get("ok")
        res.append(checks.result("encoder", "轉檔將使用", checks.PASS if ok else checks.WARN,
                                 f"設定指定 {label[forced]}" + ("" if ok else f"，但 {label[forced]} 檢測未通過（轉檔失敗時會改用下一層）"),
                                 label[forced]))
    elif forced == "cpu":
        res.append(checks.result("encoder", "轉檔將使用", checks.WARN, "設定指定 CPU（較慢）", "CPU"))
    else:
        res.append(checks.result("encoder", "轉檔將使用", checks.PASS if hw else checks.WARN,
                                 "自動（Quick Sync → VA-API → CPU）" if hw else "⚠ 降級為 CPU（較慢）", label.get(hw[0], "CPU") if hw else "CPU"))
    r = compose("exec", "-T", "agent", "python3", "-c",
                "import sys,os,tempfile;sys.path.insert(0,'/opt/istudy-agent');from worker.office import convert_to_pdf;"
                "d=tempfile.mkdtemp();s=os.path.join(d,'t.txt');open(s,'w',encoding='utf-8').write('中文測試');"
                "os.makedirs(d+'/o');os.makedirs(d+'/p');print(os.path.getsize(convert_to_pdf(s,d+'/o',d+'/p',120)))",
                check=False, timeout=200)
    fonts = compose("exec", "-T", "agent", "fc-list", ":lang=zh", "family", check=False, timeout=30).stdout
    doc_ok = r.returncode == 0 and r.stdout.strip().isdigit() and "Noto" in fonts
    res.append(checks.result("office", "文件轉換（中文字型）", checks.PASS if doc_ok else checks.FAIL,
                             "" if doc_ok else ("缺少 Noto CJK 字型" if "Noto" not in fonts else (r.stdout + r.stderr)[-200:])))
    res.append(check_media_acl())
    res.append(check_asr_models())
    res.append(check_backup())
    if not PREFIX:
        en = run(["systemctl", "is-enabled", "istudy.service", "istudy-hostcheck.timer"], check=False)
        ok = en.stdout.split() == ["enabled", "enabled"]
        res.append(checks.result("boot", "開機啟動", checks.PASS if ok else checks.FAIL, "" if ok else en.stdout.strip()))
    return res


def check_backup() -> dict:
    """dev.62：加密備份。沒設定恢復密碼、排程未啟用、超過 2 天沒有新備份時警告（不算失敗）。"""
    name = "加密備份（方案 B）"
    try:
        key = cfg_get("backup_key")
        if not os.path.exists(IDENTITY) or not bc.RECIPIENT_RE.match(str(key.get("recipient") or "")):
            return checks.result("backup", name, checks.WARN, "尚未設定備份恢復密碼：不會產生新備份（Server App →「備份」設定）")
        if not PREFIX:
            en = run(["systemctl", "is-enabled", "istudy-backup.timer"], check=False).stdout.strip()
            if en != "enabled":
                return checks.result("backup", name, checks.WARN, "istudy-backup.timer 未啟用：新備份不會自動驗證、上傳、清理")
        age = db_val("SELECT EXTRACT(EPOCH FROM NOW() - MAX(created_at))::int FROM backup_history WHERE format = 'age' AND status = 'success'")
        remote = "遠端已設定" if cfg_get("backup_remote").get("configured") else "⚠ 遠端未設定"
        if not age:
            return checks.result("backup", name, checks.WARN, f"金鑰已設定，還沒有加密備份；{remote}")
        hours = int(age) // 3600
        if hours > 48:
            return checks.result("backup", name, checks.WARN, f"最近一份加密備份是 {hours} 小時前；{remote}")
        return checks.result("backup", name, checks.PASS, f"金鑰已設定；最近一份 {hours} 小時前；{remote}")
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        return checks.result("backup", name, checks.WARN, f"無法檢查：{e}"[:200])


def _agent_heartbeat_age() -> Optional[int]:
    try:
        p = pg_admin("SELECT EXTRACT(EPOCH FROM NOW() - (value->>'at')::timestamptz)::int FROM server_config WHERE key='agent_heartbeat'",
                     db=DB_NAME, check=False, timeout=30)
        return int(p.stdout.strip())
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
        return None


def bootstrap(args) -> int:
    require_root()
    bundle = os.path.abspath(args.bundle) if args.bundle else None
    state = State()
    password_lines = None
    if args.password_stdin:
        password_lines = [sys.stdin.readline().rstrip("\n"), sys.stdin.readline().rstrip("\n")]
    overall = "ok"

    def step(n: int, fn: Callable[[], str], always: bool = False) -> bool:
        nonlocal overall
        name = STEPS[n]
        if not always and state.done(n):
            Out.emit("step", step=n, name=name, status="skip", message="已完成")
            return True
        Out.emit("step", step=n, name=name, status="running")
        try:
            msg = fn()
            state.mark(n, "ok", msg)
            Out.emit("step", step=n, name=name, status="ok", message=msg)
            return True
        except Exception as e:  # noqa: BLE001 — 每一步的失敗都要轉成白話訊息
            state.mark(n, "fail", str(e))
            Out.emit("step", step=n, name=name, status="fail", message=str(e))
            overall = "fail"
            return False

    if bundle and not args.check_only:
        if not step(1, lambda: f"{verify_bundle(bundle)} 個檔案雜湊值相符", always=True):
            return finish("bootstrap", overall)
    elif bundle:
        Out.emit("step", step=1, name=STEPS[1], status="skip", message="只做檢查（套件已由 install.sh 驗證）")
    else:
        Out.emit("step", step=1, name=STEPS[1], status="skip", message="未指定套件（只做檢查或沿用已安裝的檔案）")
    # 步驟 2：預檢（每次都重做）
    Out.emit("step", step=2, name=STEPS[2], status="running")
    results = preflight(bundle=bundle)
    for r in results:
        Out.emit("check", **r)
    summary = checks.summarize(results)
    state.mark(2, {"pass": "ok"}.get(summary, summary), "")
    if summary == "fail":
        Out.emit("step", step=2, name=STEPS[2], status="fail", message="有必須先處理的項目（見上方 ✗）")
        return finish("bootstrap", "fail")
    if summary == "warn" and not args.accept_warnings and not args.check_only:
        Out.emit("step", step=2, name=STEPS[2], status="warn", message="有警告；確認後加上 --accept-warnings（Server App 按「了解，繼續」）再執行")
        return finish("bootstrap", "warn", code=3)
    Out.emit("step", step=2, name=STEPS[2], status="warn" if summary == "warn" else "ok",
             message="有警告，已確認繼續" if summary == "warn" else "")
    if args.check_only:
        return finish("bootstrap-check", summary)
    if not bundle:
        die("請指定離線套件：--bundle /media/USB/istudy-server-<版本>-offline")
    ok = (step(3, lambda: step_install_docker(bundle))
          and step(4, step_system_settings)
          and step(5, lambda: step_dirs_and_marker(MEDIA), always=True)
          and step(6, lambda: step_config(bundle), always=True)
          and step(7, lambda: step_load_images(bundle), always=True)
          and step(8, lambda: step_database(args.restore), always=True)
          and step(9, lambda: step_super(args.super_user, password_lines))
          and step(10, step_boot, always=True)
          and step(11, step_start, always=True)
          and step(12, lambda: step_clients(bundle), always=True))
    if ok:
        def self_check() -> str:
            res = doctor_checks()
            for r in res:
                Out.emit("check", **r)
            s = checks.summarize(res)
            if s == "fail":
                raise RuntimeError("自我檢查有失敗項目（見上方 ✗）；修正後可執行 sudo istudyctl doctor 重新檢查")
            return "全部通過" if s == "pass" else "通過（有警告）"
        step(13, self_check, always=True)
    report = Out.save_report("bootstrap")
    Out.emit("step", step=14, name=STEPS[14], status="ok" if overall == "ok" else "fail", message=report)
    state.mark(14, "ok" if overall == "ok" else "fail", report)
    if overall == "ok":
        Out.emit("done", status="ok", message="部署完成，請以 Super 登入 Server App 完成首次設定", report=report)
        return 0
    Out.emit("done", status="fail", message="部署未完成：修正上方 ✗ 項目後再執行一次，會從未完成的步驟繼續", report=report)
    return 1


def finish(name: str, status: str, code: Optional[int] = None) -> int:
    report = Out.save_report(name)
    Out.emit("done", status=status, report=report, message=f"結果：{status}（報告 {report}）")
    if code is not None:
        return code
    return 0 if status in ("ok", "pass", "warn") else 1


# ---------------- 其他子指令 ----------------

def check_media(quiet: bool = False) -> bool:
    """開機時 istudy.service 的 ExecStartPre：/Media 已掛載且媒體標記與安裝識別碼相同。"""
    marker = os.path.join(MEDIA, ".istudy-media")
    try:
        expected = _read(os.path.join(ETC, "install_id")).strip()
        actual = _read(marker).strip()
    except OSError as e:
        if not quiet:
            print(f"✗ /Media 未掛載或找不到媒體標記：{e}", file=sys.stderr)
        return False
    if os.environ.get("ISTUDY_REQUIRE_MOUNT", "1") != "0" and not os.path.ismount(MEDIA):
        if not quiet:
            print(f"✗ {MEDIA} 不是掛載點（ZFS 尚未掛載？）", file=sys.stderr)
        return False
    if expected != actual:
        if not quiet:
            print("✗ 媒體標記與安裝識別碼不同（可能掛錯磁碟）", file=sys.stderr)
        return False
    if not quiet:
        print("✓ /Media 與媒體標記正常")
    return True


def hostcheck(quiet: bool = False) -> dict:
    """以 root 收集主機層資料，寫入 /var/lib/istudy/hostcheck.json（唯讀掛進 agent 容器）。"""
    info = checks.os_release()
    zfs = checks.zpool_status()
    smart = checks.smart_status()
    res = [checks.check_system_disk("/", 30), checks.check_zfs(), checks.check_smart()]
    states = container_states() if shutil.which("docker") and os.path.exists(os.path.join(OPT, "compose.yaml")) and not SKIP_DOCKER else {}
    if states:
        bad = [k for k, v in states.items() if v != "running"]
        res.append(checks.result("containers", "容器狀態", checks.PASS if not bad else checks.WARN,
                                 "" if not bad else "未執行：" + "、".join(bad), states))
    data = {
        "time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": {"os": info.get("PRETTY_NAME"), "kernel": platform.release(), "python": platform.python_version(),
                   "hostname": platform.node(), "uptime_sec": _uptime(), "ipv4": checks.local_ipv4()},
        "disks": [d for d in (checks.disk_usage("/"), checks.disk_usage(MEDIA)) if d],
        "zfs": zfs, "smart": smart, "thermal": checks.thermal(), "docker": checks.docker_versions() if not SKIP_DOCKER else {},
        "containers": states, "checks": res,
    }
    os.makedirs(VARLIB, exist_ok=True)
    tmp = HOSTCHECK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    os.chmod(tmp, 0o644)
    os.replace(tmp, HOSTCHECK_FILE)
    if not quiet:
        for r in res:
            Out.emit("check", **r)
    return data


UEM_DIR = os.path.join(VARLIB, "uem")
UEM_KEEP = 10  # history/ 只保留最近 10 份（只刪 UEM 自己寫的報告）


def _uem_latest() -> Optional[dict]:
    try:
        d = _json(os.path.join(UEM_DIR, "latest.json"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def uem_run(mode: str) -> int:
    """主機盤點（UEM）：只讀；寫入 /var/lib/istudy/uem/latest.{json,md,html} 與 history/（Server App 讀取顯示）。"""
    require_root()
    from istudy_common import uem
    t0 = time.time()
    now = lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds")  # noqa: E731
    vf = os.path.join(OPT, "VERSION.json")
    try:
        ver = _json(vf).get("server")
    except (OSError, ValueError, AttributeError):
        ver = None
    Out.emit("info", message="盤點磁碟、重要路徑、ZFS、服務、連接埠與版本（只讀，不修改任何設定）…")
    rep = uem.collect(dict(opt=OPT, varlib=VARLIB, srv=SRV, media=MEDIA, pg_major=PG_MAJOR,
                           pg_clusters=[] if SKIP_DOCKER else checks.pg_clusters(),
                           hostcheck_file=HOSTCHECK_FILE, version_file=vf))
    if mode == "full":
        Out.emit("info", message="執行自我檢查（含轉檔與文件轉換試轉，約 1～3 分鐘）…")
        rep["doctor"] = {"time": now(), "carried": False, "items": doctor_checks()}
    else:
        prev = _uem_latest()
        if prev and isinstance(prev.get("doctor"), dict) and prev["doctor"].get("items"):
            rep["doctor"] = {**prev["doctor"], "carried": True}
    rep.update(mode=mode, time=now(), duration_sec=round(time.time() - t0, 1), istudy_version=ver, tool="istudyctl uem")
    rep.update(uem.summarize(rep))
    rep = uem.scrub(rep)
    hist = os.path.join(UEM_DIR, "history")
    os.makedirs(hist, exist_ok=True)
    for d in (UEM_DIR, hist):
        os.chmod(d, 0o755)
    body = json.dumps(rep, ensure_ascii=False, indent=1, default=str)
    for name, text in (("latest.json", body), ("latest.md", uem.render_md(rep)), ("latest.html", uem.render_html(rep))):
        tmp = os.path.join(UEM_DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(UEM_DIR, name))
    hp = os.path.join(hist, f"uem-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{mode}.json")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(hp, 0o644)
    for old in sorted(glob.glob(os.path.join(hist, "uem-*.json")))[:-UEM_KEEP]:
        os.remove(old)
    c = rep["counts"]
    if not Out.json_mode:
        for i in rep["issues"]:
            where = f"（{i['path']}）" if i.get("path") else ""
            print(f"   {uem.STATUS_MARK.get(i['status'], '·')} {uem.STATUS_TEXT.get(i['status'], i['status'])}"
                  f"｜{i['section']}｜{i['name']}{where}" + (f"：{i['detail']}" if i.get("detail") else ""), flush=True)
    Out.emit("done", status=rep["overall"].lower(), report=os.path.join(UEM_DIR, "latest.html"),
             message=f"主機盤點完成（{'完整' if mode == 'full' else '快速'}，{rep['duration_sec']} 秒）：異常 {c['FAIL']}、注意 {c['WARN']}、"
                     f"不存在 {c['MISSING']}、未知 {c['UNKNOWN']}；報告 {os.path.join(UEM_DIR, 'latest.html')}")
    return 0


def _uptime() -> Optional[int]:
    try:
        return int(float(_read("/proc/uptime").split()[0]))
    except (OSError, ValueError):
        return None


def check_ip() -> int:
    r = checks.check_ip(DEFAULT_SERVER_IP)
    print(("PASS" if r["status"] == "pass" else "WARNING") + f"  預期 {DEFAULT_SERVER_IP}；偵測到 {', '.join(r['value']) or '無'}")
    return 0 if r["status"] == "pass" else 1


def status() -> int:
    states = container_states()
    v = _json(os.path.join(OPT, "VERSION.json")) if os.path.exists(os.path.join(OPT, "VERSION.json")) else {}
    out = {"server": v.get("server"), "clients": {k: x.get("version") for k, x in (v.get("client") or {}).items()},
           "containers": states, "agent_heartbeat_age_sec": _agent_heartbeat_age() if states else None}
    Out.emit("status", **out)
    if not Out.json_mode:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def restart() -> int:
    require_root()
    if not check_media():
        return 2
    p = run(["systemctl", "restart", "istudy.service"], check=False, timeout=400)
    Out.emit("done", status="ok" if p.returncode == 0 else "fail", message="服務已重新啟動" if p.returncode == 0 else p.stderr.strip()[-300:])
    return p.returncode


def list_disks() -> list[dict]:
    """可設為備份磁碟的分割區：不在 /Media（ZFS）、不是系統碟。"""
    p = run(["lsblk", "-J", "-b", "-o", "NAME,UUID,FSTYPE,SIZE,MOUNTPOINT,LABEL,MODEL,TRAN,TYPE,PKNAME"], check=False)
    try:
        tree = json.loads(p.stdout).get("blockdevices", [])
    except ValueError:
        return []
    out = []

    def walk(nodes, parent_model=""):
        for n in nodes:
            model = n.get("model") or parent_model
            if n.get("type") == "part" and n.get("uuid") and n.get("fstype") in ("ext4", "xfs", "btrfs", "exfat", "vfat", "ntfs"):
                mp = n.get("mountpoint") or ""
                if mp in ("/", "/boot", "/boot/efi") or mp.startswith(MEDIA):
                    continue
                out.append({"name": n["name"], "uuid": n["uuid"], "fstype": n["fstype"], "size": int(n.get("size") or 0),
                            "mountpoint": mp, "label": n.get("label") or "", "model": model, "transport": n.get("tran") or ""})
            walk(n.get("children") or [], model)
    walk(tree)
    return out


def backup_disk(uuid: str) -> int:
    require_root()
    if not re.fullmatch(r"[0-9A-Fa-f\-]{8,40}", uuid):
        die("UUID 格式錯誤")
    disk = next((d for d in list_disks() if d["uuid"] == uuid), None)
    if not disk:
        die("找不到這顆磁碟（需已連接，且不是系統碟或 /Media）")
    target = os.path.join(SRV, "backup")
    os.makedirs(target, exist_ok=True)
    fstab = P("/etc/fstab")
    lines = _read(fstab, encoding="utf-8").splitlines() if os.path.exists(fstab) else []
    lines = [ln for ln in lines if not (ln.split() and len(ln.split()) > 1 and ln.split()[1] == "/srv/istudy/backup")]
    opts = "defaults,nofail,x-systemd.device-timeout=10s"
    lines.append(f"UUID={uuid} /srv/istudy/backup {disk['fstype']} {opts} 0 2")
    shutil.copy2(fstab, fstab + ".istudy.bak") if os.path.exists(fstab) else None
    with open(fstab, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if not PREFIX:
        if disk["mountpoint"] and disk["mountpoint"] != "/srv/istudy/backup":
            run(["umount", disk["mountpoint"]], check=False)
        run(["systemctl", "daemon-reload"], check=False)
        run(["mount", "/srv/istudy/backup"])
        os.chown("/srv/istudy/backup", AGENT_UID, AGENT_UID)
        os.chmod("/srv/istudy/backup", 0o770)
        # agent 容器掛載的是 /srv/istudy/backup；重新建立容器以看到新掛載
        compose("up", "-d", "--no-build", "--force-recreate", "agent", check=False)
    Out.emit("done", status="ok", message=f"已設為備份磁碟：{disk['label'] or disk['name']}（{disk['fstype']}）", disk=disk)
    return 0


def backup_cmd(destination: Optional[str]) -> int:
    require_root()
    dest = destination or os.path.join(SRV, "backup")
    os.makedirs(dest, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(dest, f"istudy-db-{stamp}.sql")
    pg_dump_to(path)
    Out.emit("done", status="ok", message=f"資料庫已備份到 {path}", file=path)
    return 0


# ---------------- 字幕辨識模型（dev.57） ----------------
# FunASR 的 Paraformer-zh／SenseVoiceSmall（sherpa-onnx int8 版）、Silero VAD、CT-Transformer 標點。
# 只從固定網址下載、核對固定的 SHA256，放到 /opt/istudy/models/asr/<資料夾>（Agent 容器內唯讀掛在 /models/asr）。
ASR_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
FUNASR_LICENSE = "FunASR MODEL_LICENSE（https://github.com/modelscope/FunASR）：商用與署名條件請自行確認"
ASR_MODELS = {
    "vad": {"path": "asr-models/silero_vad.onnx", "size": 643854,
            "sha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            "files": ["silero_vad.onnx"], "dir": "vad", "license": "MIT（snakers4/silero-vad）"},
    "punct-ct-transformer": {"path": "punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2",
                             "size": 64717756, "sha256": "c0d5aa5f8eeb686032345e180bedf39319dc2e0556781c6264bcadba8328a6e1",
                             "files": ["model.int8.onnx"], "dir": "punct-ct-transformer", "license": FUNASR_LICENSE},
    "paraformer-zh": {"path": "asr-models/sherpa-onnx-paraformer-zh-2023-09-14.tar.bz2", "size": 234051698,
                      "sha256": "9c49fd9c6fb63de8e18c1054cf3d100f804741b7e608e187923cd8ff09fa9f03",
                      "files": ["model.int8.onnx", "tokens.txt"], "dir": "paraformer-zh", "license": FUNASR_LICENSE},
    "sensevoice-small": {"path": "asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2", "size": 163002883,
                         "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
                         "files": ["model.int8.onnx", "tokens.txt"], "dir": "sensevoice-small", "license": FUNASR_LICENSE},
}
ASR_TARGETS = {"paraformer-zh": ["vad", "punct-ct-transformer", "paraformer-zh"],
               "sensevoice-small": ["vad", "sensevoice-small"],
               "all": ["vad", "punct-ct-transformer", "paraformer-zh", "sensevoice-small"]}


def _asr_dir(name: str) -> str:
    return os.path.join(OPT, "models", "asr", ASR_MODELS[name]["dir"])


def _asr_installed(name: str) -> bool:
    d = _asr_dir(name)
    return all(os.path.isfile(os.path.join(d, f)) for f in ASR_MODELS[name]["files"])


def check_asr_models():
    """自我檢查「字幕模型」：FunASR（VAD＋Paraformer-zh／SenseVoiceSmall）。
    dev.59 起不再列舉 /opt/istudy/models/faster-whisper-*（Whisper 引擎已移除；舊模型目錄不會被讀取，也不會被自動刪除）。"""
    funasr = [n for n in ("paraformer-zh", "sensevoice-small") if _asr_installed(n)]
    vad = _asr_installed("vad")
    punct = _asr_installed("punct-ct-transformer")
    value = {"funasr": funasr, "vad": vad, "punct": punct}
    if funasr and vad:
        note = "" if punct or "paraformer-zh" not in funasr else "（Paraformer-zh 缺標點模型：sudo istudyctl asr-models --fetch paraformer-zh）"
        return checks.result("models", "字幕模型", checks.PASS if not note else checks.WARN, f"FunASR：{'、'.join(funasr)}{note}", value)
    return checks.result("models", "字幕模型", checks.WARN, "沒有字幕辨識模型（sudo istudyctl asr-models --fetch all）", value)


def _download(url: str, dst: str, size: int, label: str) -> str:
    """下載到 dst（.part 完成後才改名）；回傳 SHA256。進度每 10% 回報一次。"""
    h = hashlib.sha256()
    part = dst + ".part"
    got, last = 0, -10
    req = urllib.request.Request(url, headers={"User-Agent": "istudyctl"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                pct = int(got * 100 / size) if size else 0
                if pct >= last + 10:
                    last = pct - pct % 10
                    Out.emit("info", message=f"  下載 {label}：{min(pct, 100)}%（{got // (1 << 20)} MB）")
    except (OSError, urllib.error.URLError) as e:
        if os.path.exists(part):
            os.remove(part)
        raise RuntimeError(f"下載 {label} 失敗：{getattr(e, 'reason', e)}（Server 需要暫時連網，能連到 github.com）") from e
    os.replace(part, dst)
    return h.hexdigest()


def asr_models(fetch: Optional[str]) -> int:
    """沒有 --fetch：列出模型狀態。--fetch paraformer-zh|sensevoice-small|all：下載、核對 SHA256、安裝。"""
    if not fetch:
        rows = []
        for name, m in ASR_MODELS.items():
            ok = _asr_installed(name)
            rows.append({"name": name, "installed": ok, "dir": _asr_dir(name), "license": m["license"]})
            Out.emit("check", id=f"asr_{name}", name=name, status=checks.PASS if ok else checks.WARN,
                     message=_asr_dir(name) if ok else "未安裝")
        Out.emit("asr_models", models=rows)
        Out.emit("done", status="pass" if all(r["installed"] for r in rows) else "warn",
                 message="字幕辨識模型：" + "、".join(f"{r['name']}{'✓' if r['installed'] else '✗'}" for r in rows))
        return 0
    require_root()
    if fetch not in ASR_TARGETS:
        die(f"不支援的模型：{fetch}（可用：{'、'.join(ASR_TARGETS)}）", 64)
    base = os.environ.get("ISTUDY_ASR_MODEL_BASE", ASR_BASE) if PREFIX else ASR_BASE  # 測試才可改下載來源
    todo = [n for n in ASR_TARGETS[fetch] if not _asr_installed(n)]
    if not todo:
        Out.emit("done", status="ok", message=f"{fetch} 需要的模型都已安裝，沒有重新下載")
        return 0
    root = os.path.join(OPT, "models", "asr")
    os.makedirs(root, exist_ok=True)
    need = sum(ASR_MODELS[n]["size"] for n in todo)
    free = shutil.disk_usage(root).free
    if free < need * 3:
        die(f"{root} 空間不足：需要約 {need * 3 // (1 << 20)} MB，剩 {free // (1 << 20)} MB")
    Out.emit("info", message=f"下載 {len(todo)} 個模型（約 {need // (1 << 20)} MB）：{'、'.join(todo)}；來源 {base}")
    work = tempfile.mkdtemp(prefix=".download-", dir=root)
    try:
        for name in todo:
            m = ASR_MODELS[name]
            fname = os.path.basename(m["path"])
            arc = os.path.join(work, fname)
            digest = _download(base + m["path"], arc, m["size"], name)
            if digest != m["sha256"]:
                raise RuntimeError(f"{name} 的 SHA256 不符（{digest[:16]}…），已停止，沒有安裝任何檔案")
            out = os.path.join(work, name)
            os.makedirs(out)
            if fname.endswith(".tar.bz2"):
                wanted = set(m["files"])
                with tarfile.open(arc, "r:bz2") as tf:
                    for mem in tf.getmembers():
                        base_name = os.path.basename(mem.name)
                        # 只取需要的檔案、只取一般檔案；以檔名寫出，不使用壓縮檔內的路徑
                        if mem.isfile() and base_name in wanted and "test_wavs" not in mem.name:
                            src = tf.extractfile(mem)
                            with open(os.path.join(out, base_name), "wb") as f:
                                shutil.copyfileobj(src, f)
                            wanted.discard(base_name)
                if wanted:
                    raise RuntimeError(f"{name} 壓縮檔缺少 {'、'.join(sorted(wanted))}")
            else:
                shutil.copy2(arc, os.path.join(out, m["files"][0]))
            files = {}
            for f in m["files"]:
                fh_hash = hashlib.sha256()
                with open(os.path.join(out, f), "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        fh_hash.update(chunk)
                files[f] = fh_hash.hexdigest()
                os.chmod(os.path.join(out, f), 0o644)
            with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as f:
                json.dump({"name": name, "source": base + m["path"], "archive_sha256": digest, "files": files,
                           "license": m["license"], "fetched_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")},
                          f, ensure_ascii=False, indent=1)
            os.chmod(out, 0o755)
            dst = _asr_dir(name)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            os.replace(out, dst)
            os.remove(arc)
            Out.emit("info", message=f"  ✓ {name} → {dst}")
    except RuntimeError as e:
        die(str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    os.chmod(root, 0o755)
    Out.emit("done", status="ok", message=f"字幕辨識模型已安裝（{fetch}）；Agent 約 30 秒內會回報可用")
    return 0


def _extract_sql(file: str, work: str) -> str:
    if file.endswith(".sql"):
        return file
    if file.endswith(".age"):
        # dev.62：加密備份由主機以私鑰解密（串流），只取出 db.sql 到 root 專用的暫存目錄
        res, rc, err = age_decrypt(file, lambda f: _extract_member(f, "db.sql", work))
        if rc != 0 or not res:
            raise RuntimeError("解密失敗：" + (err or "備份內沒有 db.sql"))
        return res
    with tarfile.open(file, "r:*") as tar:
        m = tar.getmember("db.sql")
        tar.extract(m, work, filter="data")
    return os.path.join(work, "db.sql")


TABLES = ["accounts", "folders", "media", "bookmarks", "view_history", "subtitles", "audit_log"]


def _restore_to_test(sql: str, tables: list[str]) -> dict:
    """還原到隔離的測試資料庫 istudy_restore_test（擁有者 istudy），回傳各資料表筆數。不碰正式資料庫。"""
    drop_aux_db(DB_TEST)
    create_db(DB_TEST)
    p = pg_admin(file=sql, db=DB_TEST, as_istudy=True, check=False)
    if p.returncode != 0:
        raise RuntimeError("還原到測試資料庫失敗：" + p.stderr.strip()[-300:])
    counts = {}
    for t in tables:
        if not re.fullmatch(r"[a-z_]{1,40}", t):
            continue
        r = pg_admin(f"SELECT COUNT(*) FROM {t}", db=DB_TEST, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"測試資料庫缺少資料表 {t}")
        counts[t] = int(r.stdout.strip() or 0)
    return counts


def restore(file: str, target: str, from_bootstrap: bool = False) -> str:
    """一律先還原到測試資料庫並檢查資料表與筆數；target=prod 時確認後才覆蓋正式資料庫。
    dev.62：也接受加密備份（.tar.gz.age，需要主機私鑰；沒有私鑰時先用恢復密碼 backup-key --import）。"""
    if not from_bootstrap:
        require_root()
    if not os.path.isfile(file):
        raise RuntimeError(f"找不到備份檔 {file}")
    work = tempfile.mkdtemp(prefix="istudy-restore-")
    try:
        sql = _extract_sql(file, work)
        counts = _restore_to_test(sql, TABLES)
        Out.emit("info", message="測試資料庫檢查：" + "、".join(f"{k} {v} 筆" for k, v in counts.items()), counts=counts)
        if target != "prod":
            return "已還原到測試資料庫 istudy_restore_test（正式資料庫未變動）"
        if not from_bootstrap:
            # 正式還原一定要在終端機互動確認；沒有終端機（例如由其他程式呼叫）時一律拒絕，不會默默覆蓋正式資料庫
            if not sys.stdin.isatty():
                raise RuntimeError("正式還原必須在終端機執行並輸入「覆蓋」確認；已停止（正式資料庫未變動）")
            ans = input("確定要以這份備份覆蓋正式資料庫？輸入「覆蓋」確認：").strip()
            if ans != "覆蓋":
                raise RuntimeError("已取消（正式資料庫未變動）")
        compose("stop", "agent", "php", check=False)
        pg_admin(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_NAME}' AND pid <> pg_backend_pid()")
        drop_aux_db("istudy_before_restore")
        pg_admin(f"ALTER DATABASE {DB_NAME} RENAME TO istudy_before_restore")
        r = pg_admin(f"ALTER DATABASE {DB_TEST} RENAME TO {DB_NAME}", check=False)
        if r.returncode != 0:
            pg_admin(f"ALTER DATABASE istudy_before_restore RENAME TO {DB_NAME}", check=False)
            raise RuntimeError("切換資料庫失敗，已恢復原本的資料庫")
        create_db(DB_TEST)  # 空的測試資料庫，給 Agent 的還原檢查使用
        compose("up", "-d", "--no-build", "php", "agent", check=False)
        return "已還原為正式資料庫（原資料庫保留為 istudy_before_restore）"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------- dev.62 加密備份（方案 B）：金鑰、解密驗證、還原測試、遠端上傳、清理 ----------------
# Agent 只有公鑰（server_config.backup_key.recipient）；私鑰與遠端帳密只在 /etc/istudy/backup（root 0700），不掛入任何容器。
# 主機沒有安裝 age：以 Agent 映像（內含 age）跑一次性容器（--network none、唯讀、--log-driver none）；
# 私鑰、密碼一律經標準輸入傳入，不出現在指令列、環境變數、檔案或日誌。

BK_ETC = os.path.join(ETC, "backup")
IDENTITY = os.path.join(BK_ETC, "identity.key")
WRAPPED = os.path.join(BK_ETC, "recovery-key.age")
REMOTE_CFG = os.path.join(BK_ETC, "remote.json")
HOST_JOB_TYPES = ("backup_verify", "backup_upload", "backup_prune", "backup_remote_test")
RECOVERY_PW_MIN = 12
AGEPASS_IN_IMAGE = "/opt/istudy-agent/istudy_common/agepass.py"


def bk_dir() -> str:
    d = os.environ.get("ISTUDY_BACKUP_DIR") if PREFIX else None   # 測試模式：與測試 Agent 共用的備份目錄
    return d or os.path.join(SRV, "backup")


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


# ---------- 資料庫（以主機 postgres 帳號；值一律轉成 SQL 字面值） ----------

def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (dict, list)):
        return "CAST(" + _lit(json.dumps(v, ensure_ascii=False, default=str)) + " AS jsonb)"
    return "'" + str(v).replace("\x00", "").replace("'", "''") + "'"


def db_rows(sql: str) -> list[dict]:
    p = pg_admin(f"SELECT COALESCE(json_agg(t), '[]'::json) FROM ({sql}) t", db=DB_NAME, timeout=120)
    return json.loads(p.stdout.strip() or "[]")


def db_val(sql: str) -> str:
    return pg_admin(sql, db=DB_NAME, timeout=120).stdout.strip()


def cfg_get(key: str) -> dict:
    v = db_val(f"SELECT value::text FROM server_config WHERE key = {_lit(key)}")
    try:
        d = json.loads(v) if v else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def cfg_merge(key: str, data: dict) -> None:
    db_val(f"INSERT INTO server_config(key, value, updated_at) VALUES ({_lit(key)}, {_lit(data)}, NOW()) "
           f"ON CONFLICT (key) DO UPDATE SET value = server_config.value || EXCLUDED.value, updated_at = NOW()")


def hlog(level: str, message: str, **context) -> None:
    """系統 Log（Server App「Log → 主機維護」）。不寫任何密碼或私鑰。"""
    try:
        db_val(f"INSERT INTO system_logs(source, level, message, context) VALUES ('host', {_lit(level)}, {_lit(message[:2000])}, {_lit(context)})")
    except (RuntimeError, OSError, subprocess.SubprocessError):
        pass


def _ts(v) -> Optional[_dt.datetime]:
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v))
    except ValueError:
        return None


class HostJob:
    """主機執行的備份工作寫進 jobs（只記錄進度與結果；Agent 不執行這些類型），Server App 看得到。"""

    def __init__(self, jtype: str, payload: Optional[dict] = None):
        self.type, self.payload, self.id, self.result = jtype, payload or {}, None, {}

    def __enter__(self) -> "HostJob":
        try:
            self.id = int(db_val(f"INSERT INTO jobs(type, payload, status, started_at, attempts) VALUES "
                                 f"({_lit(self.type)}, {_lit(self.payload)}, 'running', NOW(), 1) RETURNING id"))
        except (RuntimeError, ValueError):
            self.id = None
        Out.emit("info", message=f"開始：{HOST_JOB_LABEL.get(self.type, self.type)}" + (f"（備份 #{self.payload['backup_id']}）" if self.payload.get("backup_id") else ""))
        return self

    def __exit__(self, et, e, tb) -> bool:
        if self.id is not None:
            if et is None:
                db_val(f"UPDATE jobs SET status = 'done', finished_at = NOW(), result = {_lit(self.result)} WHERE id = {self.id}")
            else:
                db_val(f"UPDATE jobs SET status = 'failed', finished_at = NOW(), result = {_lit(self.result)}, "
                       f"error = {_lit(str(e)[:500])} WHERE id = {self.id}")
        return False


HOST_JOB_LABEL = {"backup_verify": "解密驗證", "backup_upload": "遠端上傳", "backup_prune": "保留清理", "backup_remote_test": "遠端連線測試"}


def _close_stale_host_jobs() -> None:
    """拿到備份鎖時，不可能還有其他主機備份工作在跑：留下的 running 是上次中斷的。"""
    db_val("UPDATE jobs SET status = 'failed', finished_at = NOW(), error = '中斷（主機程序未正常結束）' "
           f"WHERE status = 'running' AND type IN ({', '.join(_lit(t) for t in HOST_JOB_TYPES)})")


def _lock(wait: float) -> bc.BackupLock:
    """手動操作最多等 2 分鐘（Agent 正在建立備份時）；ISTUDY_BACKUP_HOST_WAIT 可調整（測試用）。"""
    os.makedirs(bk_dir(), exist_ok=True)
    return bc.BackupLock(bk_dir(), wait=float(os.environ.get("ISTUDY_BACKUP_HOST_WAIT") or wait))


# ---------- age（一次性容器；測試模式直接用 ISTUDY_AGE_BIN） ----------

def _age_test_bin() -> Optional[str]:
    return os.environ.get("ISTUDY_AGE_BIN") if PREFIX else None


def _age_cmd(tool: str, args: list[str], ro_dir: Optional[str] = None) -> list[str]:
    tb = _age_test_bin()
    if tb:
        if tool == "agepass":
            return [sys.executable, "-I", os.path.join(HERE, "istudy_common", "agepass.py"), *args]
        return [tb if tool == "age" else os.path.join(os.path.dirname(tb), tool), *args]
    image = f"istudy/agent:{read_env().get('ISTUDY_VERSION') or 'dev'}"
    cmd = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=8m",
           "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH", "--security-opt", "no-new-privileges",
           "--log-driver", "none"]   # 不讓 Docker 保存容器輸出（解密內容、私鑰）
    if ro_dir:
        cmd += ["-v", f"{ro_dir}:/in:ro"]
    if tool == "agepass":
        return cmd + ["--entrypoint", "python3", image, "-I", AGEPASS_IN_IMAGE, *args]
    return cmd + ["--entrypoint", tool, image, *args]


def _age_file(path: str) -> tuple[Optional[str], str]:
    """（容器要掛載的唯讀資料夾, 容器內的路徑）；測試模式直接用主機路徑。"""
    if _age_test_bin():
        return None, path
    return os.path.dirname(os.path.realpath(path)), "/in/" + os.path.basename(path)


def age_recipient(identity: str) -> str:
    p = subprocess.run(_age_cmd("age-keygen", ["-y"]), input=identity.encode(), capture_output=True, timeout=180)
    r = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not bc.RECIPIENT_RE.match(r):
        raise RuntimeError("無法由私鑰算出公鑰：" + p.stderr.decode("utf-8", "replace").strip()[-200:])
    return r


def age_keygen() -> tuple[str, str]:
    p = subprocess.run(_age_cmd("age-keygen", []), capture_output=True, timeout=180)
    identity = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0 or "AGE-SECRET-KEY-" not in identity:
        raise RuntimeError("產生金鑰失敗：" + p.stderr.decode("utf-8", "replace").strip()[-200:])
    return identity, age_recipient(identity)


def age_wrap(identity: str, password: str) -> bytes:
    p = subprocess.run(_age_cmd("agepass", ["wrap"]), input=password.encode("utf-8") + b"\n" + identity.encode(),
                       capture_output=True, timeout=300)
    if p.returncode != 0 or not p.stdout.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----"):
        raise RuntimeError("恢復金鑰加密失敗：" + p.stderr.decode("utf-8", "replace").strip()[-200:])
    return p.stdout


def age_unwrap(wrapped: bytes, password: str) -> str:
    p = subprocess.run(_age_cmd("agepass", ["unwrap"]), input=password.encode("utf-8") + b"\n" + wrapped,
                       capture_output=True, timeout=300)
    if p.returncode == 2:
        raise RuntimeError("恢復密碼錯誤")
    if p.returncode != 0:
        raise RuntimeError("恢復金鑰解密失敗：" + p.stderr.decode("utf-8", "replace").strip()[-200:])
    return p.stdout.decode("utf-8", "replace")


def _read_identity() -> str:
    try:
        with open(IDENTITY, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise RuntimeError(f"找不到解密私鑰 {IDENTITY}：請先設定恢復密碼；或用恢復金鑰檔匯入（sudo istudyctl backup-key --import --file "
                           f"{bc.RECOVERY_KEY_NAME} --password-stdin）") from None


def age_decrypt(path: str, consume: Callable):
    """以私鑰解密（串流），consume(解密後的串流) 的結果、age 結束碼、錯誤訊息。私鑰經標準輸入交給 age。"""
    identity = _read_identity()
    ro, arg = _age_file(path)
    p = subprocess.Popen(_age_cmd("age", ["-d", "-i", "-", arg], ro), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    try:
        p.stdin.write(identity.encode())  # type: ignore[union-attr]
        p.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:
        pass
    res = None
    try:
        res = consume(p.stdout)
        while p.stdout.read(1 << 20):  # type: ignore[union-attr]
            pass
    except Exception:
        p.kill()
        raise
    finally:
        err = p.stderr.read().decode("utf-8", "replace").strip()[-300:] if p.stderr else ""  # type: ignore[union-attr]
        rc = p.wait(timeout=600)
    return res, rc, err


def _extract_member(fobj, name: str, work: str) -> Optional[str]:
    with tarfile.open(fileobj=fobj, mode="r|gz") as tar:
        for m in tar:
            if m.name == name and m.isfile():
                tar.extract(m, work, filter="data")
                return os.path.join(work, name)
    return None


def _write_secret(path: str, data: bytes, mode: int = 0o600) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read_passwords(n: int, from_stdin: bool) -> list[str]:
    if from_stdin:
        return [sys.stdin.readline().rstrip("\n").rstrip("\r") for _ in range(n)]
    if not sys.stdin.isatty():
        raise RuntimeError("請加 --password-stdin，由標準輸入提供恢復密碼")
    vals = [getpass.getpass("備份恢復密碼：")]
    if n == 2:
        vals.append(getpass.getpass("再輸入一次："))
    return vals


def _check_new_password(a: str, b: str) -> None:
    if a != b:
        raise RuntimeError("兩次輸入的恢復密碼不同")
    if len(a) < RECOVERY_PW_MIN:
        raise RuntimeError(f"恢復密碼至少 {RECOVERY_PW_MIN} 字")
    if len(a) > 256 or any(ord(c) < 32 for c in a):
        raise RuntimeError("恢復密碼格式錯誤（不能有換行等控制字元，最多 256 字）")


def _publish_recovery_copies(wrapped: bytes, recipient: str) -> None:
    """備份目錄也放一份恢復金鑰檔（需要密碼才能用）與還原說明，複製備份資料夾到別處時一起帶走。"""
    d = bk_dir()
    os.makedirs(d, exist_ok=True)
    _write_file(os.path.join(d, bc.RECOVERY_KEY_NAME), wrapped, 0o600)
    version = (_json(os.path.join(OPT, "VERSION.json")) if os.path.exists(os.path.join(OPT, "VERSION.json")) else {}).get("server", "")
    _write_file(os.path.join(d, bc.RESTORE_NAME), bc.restore_md(version, recipient).encode("utf-8"), 0o644)


def _write_file(path: str, data: bytes, mode: int) -> None:
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def backup_key(mode: str, password_stdin: bool, file: Optional[str] = None) -> int:
    """備份恢復密碼與加密金鑰（BAK-03、BAK-04）。
    init：產生 age 金鑰對；私鑰存 root 專用；以恢復密碼加密一份（恢復金鑰檔）；公鑰寫入資料庫給 Agent。
    change：私鑰不變，只換恢復密碼（舊備份照樣能解密）。verify：確認恢復密碼能解開恢復金鑰檔。
    import：主機沒有私鑰時（重灌），用恢復金鑰檔＋恢復密碼還原私鑰。"""
    require_root()
    key = cfg_get("backup_key")
    if mode == "status":
        Out.emit("done", status="ok", message="已設定" if os.path.exists(IDENTITY) else "尚未設定",
                 initialized=os.path.exists(IDENTITY), recipient=key.get("recipient"), wrapped=os.path.exists(WRAPPED))
        return 0
    if mode == "init":
        if os.path.exists(IDENTITY):
            raise RuntimeError("已經設定過恢復密碼（主機已有私鑰）；要更換請用「修改恢復密碼」")
        a, b = _read_passwords(2, password_stdin)
        _check_new_password(a, b)
        identity, recipient = age_keygen()
        wrapped = age_wrap(identity, a)
        if age_unwrap(wrapped, a).strip() != identity.strip():
            raise RuntimeError("恢復金鑰自我檢查失敗（已停止，沒有寫入任何檔案）")
        _write_secret(IDENTITY, identity.encode())
        _write_secret(WRAPPED, wrapped)
        _publish_recovery_copies(wrapped, recipient)
        now = _now().isoformat(timespec="seconds")
        cfg_merge("backup_key", {"recipient": recipient, "created_at": now, "changed_at": None, "remote_synced": False})
        hlog("info", f"已設定備份恢復密碼與加密金鑰（公鑰 {recipient[:20]}…）")
        Out.emit("done", status="ok", message="已設定恢復密碼；之後的備份都以這把金鑰加密（私鑰只在主機 root 可讀）", recipient=recipient)
        return 0
    if mode == "change":
        identity = _read_identity()
        a, b = _read_passwords(2, password_stdin)
        _check_new_password(a, b)
        recipient = age_recipient(identity)
        wrapped = age_wrap(identity, a)
        if age_unwrap(wrapped, a).strip() != identity.strip():
            raise RuntimeError("恢復金鑰自我檢查失敗（已停止，原本的恢復密碼仍有效）")
        _write_secret(WRAPPED, wrapped)
        _publish_recovery_copies(wrapped, recipient)
        cfg_merge("backup_key", {"recipient": recipient, "changed_at": _now().isoformat(timespec="seconds"), "remote_synced": False})
        hlog("info", "已修改備份恢復密碼（加密金鑰不變，舊備份照樣可以還原）")
        Out.emit("done", status="ok", message="已修改恢復密碼；加密金鑰不變，舊備份照樣可以還原。遠端的恢復金鑰檔會在下次上傳時更新")
        return 0
    if mode == "verify":
        (pw,) = _read_passwords(1, password_stdin)
        src = WRAPPED if os.path.exists(WRAPPED) else os.path.join(bk_dir(), bc.RECOVERY_KEY_NAME)
        if not os.path.isfile(src):
            raise RuntimeError("找不到恢復金鑰檔：請先設定恢復密碼")
        with open(src, "rb") as f:
            identity = age_unwrap(f.read(), pw)
        rec = age_recipient(identity)
        ok = rec == key.get("recipient") and (not os.path.exists(IDENTITY) or identity.strip() == _read_identity().strip())
        hlog("info" if ok else "warn", "測試恢復密碼：" + ("正確" if ok else "能解開恢復金鑰檔，但與目前的加密金鑰不同"))
        Out.emit("done", status="ok" if ok else "fail",
                 message="恢復密碼正確，可以解開恢復金鑰並解密所有加密備份" if ok else "恢復金鑰檔與目前使用的加密金鑰不同")
        return 0 if ok else 1
    if mode == "import":
        if not file or not os.path.isfile(file):
            raise RuntimeError("請指定恢復金鑰檔（--file）")
        with open(file, "rb") as f:
            wrapped = f.read(64 * 1024)
        (pw,) = _read_passwords(1, password_stdin)
        identity = age_unwrap(wrapped, pw)
        rec = age_recipient(identity)
        if os.path.exists(IDENTITY):
            if _read_identity().strip() != identity.strip():
                raise RuntimeError("主機已有另一把私鑰，拒絕覆蓋")
            Out.emit("done", status="ok", message="主機已有同一把私鑰，不需要匯入")
            return 0
        _write_secret(IDENTITY, identity.encode())
        _write_secret(WRAPPED, wrapped)
        if key.get("recipient") not in (None, rec):
            Out.emit("info", message="⚠ 資料庫記錄的公鑰與匯入的私鑰不同：新的備份仍會用資料庫裡的公鑰加密，請確認")
        else:
            cfg_merge("backup_key", {"recipient": rec, "imported_at": _now().isoformat(timespec="seconds")})
        hlog("info", f"已用恢復金鑰檔匯入解密私鑰（公鑰 {rec[:20]}…）")
        Out.emit("done", status="ok", message="已匯入解密私鑰；現在可以解密驗證與還原加密備份", recipient=rec)
        return 0
    raise RuntimeError("不支援的動作")


# ---------- 解密驗證、還原測試 ----------

def _row_path(row: dict) -> Optional[str]:
    """只處理備份目錄裡、名稱符合 istudy-backup-<時間>.tar.gz.age 的檔案（不信任資料庫裡任意路徑）。"""
    p = os.path.realpath(row.get("file_path") or "")
    if os.path.dirname(p) != os.path.realpath(bk_dir()) or not bc.NAME_RE.match(os.path.basename(p)):
        return None
    return p


def _set_verify(row_id: int, enc_status: Optional[str], key: str, res: dict) -> None:
    sets = [f"verify_result = COALESCE(verify_result, '{{}}'::jsonb) || {_lit({key: res})}"]
    if enc_status:
        sets.append(f"enc_status = {_lit(enc_status)}")
    db_val(f"UPDATE backup_history SET {', '.join(sets)} WHERE id = {int(row_id)}")


def _decrypt_check(row: dict) -> dict:
    """BAK-02：SHA-256（紀錄、.sha256 檔、實際檔案）一致，並以私鑰完整解密、核對 Manifest 中每個檔案的 SHA-256。"""
    res = {"checked_at": _now().isoformat(timespec="seconds")}
    path = _row_path(row)
    if not path:
        return {**res, "passed": False, "message": "檔案不在備份目錄或名稱不符，拒絕處理"}
    if not os.path.isfile(path):
        return {**res, "passed": False, "message": "備份檔已不存在"}
    digest = bc.sha256_file(path)
    side = bc.read_sidecar(path)
    if row.get("sha256") and digest != row["sha256"]:
        return {**res, "passed": False, "message": "SHA-256 與建立時的紀錄不符（檔案可能損壞或被修改）"}
    if side and side != digest:
        return {**res, "passed": False, "message": "SHA-256 與 .sha256 檔不符"}
    v, rc, err = age_decrypt(path, bc.verify_tar_stream)
    if rc != 0:
        return {**res, "passed": False, "message": "解密失敗（私鑰不符或檔案損壞）：" + err}
    return {**res, **(v or {"passed": False, "message": "沒有內容"}), "sha256": digest}


def _restore_check(row: dict, expect: dict) -> dict:
    """BAK-10：解密後只取 db.sql，還原到隔離的測試資料庫並比對筆數；結束後把測試資料庫清空。正式資料庫完全不碰。"""
    res = {"checked_at": _now().isoformat(timespec="seconds"), "target_db": DB_TEST}
    path = _row_path(row)
    if not path or not os.path.isfile(path):
        return {**res, "passed": False, "message": "備份檔不存在"}
    work = tempfile.mkdtemp(prefix="istudy-restore-test-")
    try:
        sql = _extract_sql(path, work)
        b, a = expect.get("counts_before") or {}, expect.get("counts") or {}
        tables = sorted(set(a) | set(TABLES))
        counts = _restore_to_test(sql, tables)
        bad = [t for t in a if t in counts and not (min(b.get(t, a[t]), a[t]) <= counts[t] <= max(b.get(t, a[t]), a[t]))]
        return {**res, "passed": not bad, "restored_counts": counts, "message": "" if not bad else "筆數不符：" + "、".join(bad)}
    except RuntimeError as e:
        return {**res, "passed": False, "message": str(e)[:300]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            drop_aux_db(DB_TEST)
            create_db(DB_TEST)      # 留下空的測試資料庫（舊格式備份的還原檢查也用它）
        except RuntimeError:
            pass


def _verify_row(row: dict, restore_test: bool) -> dict:
    with HostJob("backup_verify", {"backup_id": row["id"], "restore_test": restore_test}) as job:
        d = _decrypt_check(row)
        _set_verify(row["id"], "verified" if d.get("passed") else "failed", "decrypt", d)
        job.result = {"decrypt": {k: d.get(k) for k in ("passed", "message", "members")}}
        Out.emit("check", id="decrypt", name=f"解密驗證 #{row['id']}", status="pass" if d.get("passed") else "fail", message=d.get("message", ""))
        out = {"decrypt": d}
        if not d.get("passed"):
            hlog("error", f"備份 #{row['id']} 解密驗證失敗：{d.get('message')}")
            raise RuntimeError(f"解密驗證失敗：{d.get('message')}")
        if restore_test:
            r = _restore_check(row, d)
            _set_verify(row["id"], None, "restore_test", r)
            job.result["restore_test"] = {k: r.get(k) for k in ("passed", "message")}
            Out.emit("check", id="restore", name=f"隔離還原測試 #{row['id']}（{DB_TEST}）", status="pass" if r.get("passed") else "fail",
                     message=r.get("message", ""))
            out["restore_test"] = r
            if not r.get("passed"):
                hlog("error", f"備份 #{row['id']} 隔離還原測試失敗：{r.get('message')}")
                raise RuntimeError(f"還原測試失敗：{r.get('message')}")
        return out


AGE_ROWS = "SELECT id, created_at, file_path, sha256, enc_status, remote_status, remote_path, remote_verified_at, manifest " \
           "FROM backup_history WHERE format = 'age' AND status = 'success' AND deleted_at IS NULL"


def _row(bid: int) -> dict:
    rows = db_rows(AGE_ROWS + f" AND id = {int(bid)}")
    if not rows:
        raise RuntimeError(f"找不到加密備份 #{bid}（或已刪除；舊的未加密備份請用備份頁的「檢查完整性／隔離還原測試」）")
    return rows[0]


def backup_verify(bid: Optional[int], restore_test: bool) -> int:
    require_root()
    with _lock(120):
        _close_stale_host_jobs()
        rows = [_row(bid)] if bid else db_rows(AGE_ROWS + " AND enc_status = 'unverified' ORDER BY id")
        if not rows:
            Out.emit("done", status="ok", message="沒有需要驗證的加密備份")
            return 0
        failed = 0
        for r in rows:
            try:
                _verify_row(r, restore_test)
            except RuntimeError as e:
                failed += 1
                Out.emit("info", message=f"✗ #{r['id']}：{e}")
    Out.emit("done", status="fail" if failed else "ok",
             message=f"驗證 {len(rows)} 份：{len(rows) - failed} 份通過" + (f"、{failed} 份失敗" if failed else ""))
    return 1 if failed else 0


# ---------- 遠端（FTPS） ----------

def _remote_cfg() -> Optional[dict]:
    try:
        with open(REMOTE_CFG, encoding="utf-8") as f:
            return bc.validate_remote(json.load(f))
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as e:
        raise RuntimeError(f"遠端設定檔有問題：{e}") from None


def _ftps(cfg: dict) -> bc.FtpsClient:
    cafile = os.environ.get("ISTUDY_TEST_FTPS_CAFILE") if PREFIX else None   # 測試模式：本機測試 FTPS 伺服器的憑證
    return bc.FtpsClient(cfg, timeout=float(os.environ.get("ISTUDY_FTPS_TIMEOUT", "60")), cafile=cafile)


def backup_remote(mode: str) -> int:
    require_root()
    if mode == "set":
        raw = sys.stdin.readline()
        try:
            data = json.loads(raw)
        except ValueError:
            raise RuntimeError("遠端設定格式錯誤") from None
        old = None
        try:
            old = _remote_cfg()
        except RuntimeError:
            pass
        same = bool(old and old["host"] == str(data.get("host", "")).strip() and old["username"] == str(data.get("username", "")).strip())
        try:
            cfg = bc.validate_remote(data, require_password=not same)
        except ValueError as e:
            raise RuntimeError(str(e)) from None
        if not cfg["password"] and same and old:
            cfg["password"] = old["password"]          # 主機與帳號沒變、密碼留空：沿用原密碼（換主機時一定要重新輸入）
        _write_secret(REMOTE_CFG, json.dumps(cfg, ensure_ascii=False).encode("utf-8"))
        pub = {k: cfg[k] for k in ("protocol", "host", "port", "username", "dir")}
        cfg_merge("backup_remote", {**pub, "configured": True, "updated_at": _now().isoformat(timespec="seconds")})
        hlog("info", f"已設定遠端備份：FTPS {cfg['host']}:{cfg['port']} {cfg['dir']}（帳號 {cfg['username']}）")
        Out.emit("done", status="ok", message="已儲存遠端設定（密碼只存在主機 root 可讀的檔案）；請按「測試遠端連線」確認", **pub)
        return 0
    if mode == "test":
        cfg = _remote_cfg()
        if not cfg:
            raise RuntimeError("尚未設定遠端")
        with _lock(120), HostJob("backup_remote_test") as job:
            name = f".istudy-test-{secrets.token_hex(6)}.txt"
            work = tempfile.mkdtemp(prefix="istudy-ftps-")
            local = os.path.join(work, "t.txt")
            with open(local, "wb") as f:
                f.write(secrets.token_bytes(4096))
            digest = bc.sha256_file(local)
            try:
                with _ftps(cfg) as c:
                    c.ensure_dir(cfg["dir"])
                    c.put(local, name)
                    back = c.sha256(name)
                    c.delete(name)
                if back != digest:
                    raise RuntimeError("下載回來的內容與上傳的不同")
                msg, ok = f"✓ 連線正常：FTPS 登入、建立資料夾、上傳、改名、下載比對 SHA-256、刪除都成功（{cfg['dir']}）", True
            except Exception as e:  # noqa: BLE001
                msg, ok = "✗ " + (str(e) if isinstance(e, RuntimeError) else bc.ftps_error_text(e)), False
            finally:
                shutil.rmtree(work, ignore_errors=True)
            cfg_merge("backup_remote", {"last_test": {"at": _now().isoformat(timespec="seconds"), "ok": ok, "message": msg}})
            hlog("info" if ok else "warn", "遠端連線測試：" + msg)
            job.result = {"ok": ok, "message": msg}
            if not ok:
                raise RuntimeError(msg)
        Out.emit("done", status="ok", message=msg.lstrip("✓ "))   # 畫面的訊息框自己會加 ✓
        return 0
    raise RuntimeError("不支援的動作")


def _upload_row(row: dict, cfg: dict) -> dict:
    """BAK-05：先以暫存名稱上傳再改名；下載回來比對 SHA-256 才標示已驗證。失敗時本機檔案完全不動（BAK-09）。"""
    path = _row_path(row)
    if not path or not os.path.isfile(path):
        raise RuntimeError("本機備份檔不存在")
    digest = bc.sha256_file(path)
    if row.get("sha256") and digest != row["sha256"]:
        raise RuntimeError("本機備份檔的 SHA-256 與紀錄不符，不上傳")
    name = os.path.basename(path)
    key = cfg_get("backup_key")
    db_val(f"UPDATE backup_history SET remote_status = 'uploading', remote_error = NULL WHERE id = {int(row['id'])}")
    work = tempfile.mkdtemp(prefix="istudy-upload-")
    try:
        side = bc.sidecar_path(path) if os.path.isfile(bc.sidecar_path(path)) else bc.write_sidecar(os.path.join(work, name), digest)
        readme = os.path.join(work, bc.RESTORE_NAME)
        with open(readme, "w", encoding="utf-8") as f:
            f.write(bc.restore_md((_json(os.path.join(OPT, "VERSION.json")) if os.path.exists(os.path.join(OPT, "VERSION.json")) else {}).get("server", ""),
                                  key.get("recipient", "")))
        with _ftps(cfg) as c:
            c.ensure_dir(cfg["dir"])
            c.put(path, name)
            c.put(side, name + ".sha256")
            back = c.sha256(name)
            if back != digest:
                raise RuntimeError("下載回來的 SHA-256 與本機不符（遠端檔案已上傳但未通過驗證）")
            if os.path.exists(WRAPPED):           # 恢復金鑰檔（要恢復密碼才能用）與還原說明（不含秘密）
                c.put(WRAPPED, bc.RECOVERY_KEY_NAME)
                if c.sha256(bc.RECOVERY_KEY_NAME) != bc.sha256_file(WRAPPED):
                    raise RuntimeError("恢復金鑰檔上傳後比對不符")
            c.put(readme, bc.RESTORE_NAME)
    except Exception as e:  # noqa: BLE001
        msg = str(e) if isinstance(e, RuntimeError) else bc.ftps_error_text(e)
        db_val(f"UPDATE backup_history SET remote_status = 'failed', remote_error = {_lit(msg[:500])} WHERE id = {int(row['id'])}")
        rem = cfg_get("backup_remote")
        cfg_merge("backup_remote", {"last_upload": {"at": _now().isoformat(timespec="seconds"), "ok": False, "message": msg, "backup_id": row["id"]},
                                    "upload_failures": int(rem.get("upload_failures") or 0) + 1})
        hlog("warn", f"遠端上傳失敗（備份 #{row['id']}）：{msg}；本機備份保留，可重試")
        raise RuntimeError(msg) from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rpath = cfg["dir"].rstrip("/") + "/" + name
    db_val(f"UPDATE backup_history SET remote_status = 'verified', remote_path = {_lit(rpath)}, remote_verified_at = NOW(), "
           f"remote_error = NULL WHERE id = {int(row['id'])}")
    cfg_merge("backup_remote", {"last_upload": {"at": _now().isoformat(timespec="seconds"), "ok": True, "message": "", "backup_id": row["id"]},
                                "upload_failures": 0})
    if os.path.exists(WRAPPED):
        cfg_merge("backup_key", {"remote_synced": True})
    hlog("info", f"已上傳備份 #{row['id']} 到遠端 {rpath}（下載比對 SHA-256 相符）")
    return {"remote_path": rpath, "sha256": digest}


def _upload_with_checks(row: dict, cfg: dict, auto: bool) -> dict:
    """上傳前：解密驗證＋隔離還原測試都通過才上傳（只上傳已驗證的備份）。"""
    checked = _verify_row(row, restore_test=True)
    with HostJob("backup_upload", {"backup_id": row["id"], "auto": auto}) as job:
        out = _upload_row(row, cfg)
        job.result = out
    return {**out, "decrypt": checked["decrypt"].get("passed"), "restore_test": checked["restore_test"].get("passed")}


def _upload_candidate() -> Optional[dict]:
    """最新一份檔案還在的加密備份；它已經有遠端副本（或驗證失敗）就不挑更舊的。"""
    for r in db_rows(AGE_ROWS + " ORDER BY created_at DESC, id DESC LIMIT 5"):
        p = _row_path(r)
        if p and os.path.isfile(p):
            return r if r["enc_status"] != "failed" and r["remote_status"] in ("none", "failed", "uploading") else None
    return None


def backup_upload(bid: Optional[int]) -> int:
    require_root()
    cfg = _remote_cfg()
    if not cfg:
        raise RuntimeError("尚未設定遠端（InterServer FTPS）")
    with _lock(120):
        _close_stale_host_jobs()
        row = _row(bid) if bid else _upload_candidate()
        if not row:
            Out.emit("done", status="ok", message="沒有需要上傳的備份（最新的備份都已有遠端副本）")
            return 0
        out = _upload_with_checks(row, cfg, auto=False)
    Out.emit("done", status="ok", message=f"已上傳備份 #{row['id']} 並下載比對 SHA-256 相符：{out['remote_path']}")
    return 0


# ---------- 保留與清理 ----------

def _prune() -> dict:
    """BAK-07、BAK-08：只刪超過保留天數的加密備份；最後一份可用備份永遠保留；舊的未加密 .tar.gz 與 istudy-db-*.sql 不處理。"""
    keep = bc.keep_days_of(cfg_get("backup_settings"))
    now = _now()
    rows = db_rows(AGE_ROWS)
    missing = []
    for r in rows:
        r["created_at"] = _ts(r["created_at"])
        p = _row_path(r)
        r["exists"] = bool(p and os.path.isfile(p))
        if p and not r["exists"]:
            missing.append(r["id"])
    plan = bc.retention_plan(rows, now, keep)
    by_id = {r["id"]: r for r in rows}
    deleted = []
    for d in plan["delete"]:
        p = _row_path(by_id[d["id"]])
        if not p:
            continue
        for f in (p, bc.sidecar_path(p)):
            if os.path.isfile(f):
                os.remove(f)
        db_val(f"UPDATE backup_history SET deleted_at = NOW(), delete_reason = {_lit(d['reason'])} WHERE id = {int(d['id'])}")
        deleted.append({"id": d["id"], "file": os.path.basename(p), "reason": d["reason"]})
    remote_deleted, remote_err = [], ""
    cfg = None
    try:
        cfg = _remote_cfg()
    except RuntimeError as e:
        remote_err = str(e)
    rplan = bc.remote_retention_plan(rows, now, keep) if cfg else []
    if rplan:
        try:
            with _ftps(cfg) as c:   # type: ignore[arg-type]
                c.ensure_dir(cfg["dir"])   # type: ignore[index]
                names = set(c.names())
                for d in rplan:
                    name = os.path.basename(by_id[d["id"]].get("remote_path") or by_id[d["id"]]["file_path"])
                    if not bc.NAME_RE.match(name):
                        continue
                    for n in (name, name + ".sha256"):
                        if n in names:
                            c.delete(n)
                    db_val(f"UPDATE backup_history SET remote_status = 'deleted' WHERE id = {int(d['id'])}")
                    remote_deleted.append({"id": d["id"], "file": name, "reason": d["reason"]})
        except Exception as e:  # noqa: BLE001
            remote_err = bc.ftps_error_text(e)
    legacy = int(db_val("SELECT COUNT(*) FROM backup_history WHERE format = 'legacy' AND status = 'success'") or 0)
    res = {"keep_days": keep, "deleted": deleted, "protected": plan["protected"], "protected_reason": plan["protected_reason"],
           "remote_deleted": remote_deleted, "remote_error": remote_err, "missing": missing, "legacy_untouched": legacy}
    msg = (f"清理（保留 {keep} 天）：刪除本機 {len(deleted)} 份" + (f"（{'、'.join(x['file'] for x in deleted)}）" if deleted else "")
           + f"、遠端 {len(remote_deleted)} 份；保留最後可用備份 #{plan['protected']}（{plan['protected_reason']}）"
           + (f"；舊的未加密備份 {legacy} 份不處理" if legacy else "") + (f"；遠端清理失敗：{remote_err}" if remote_err else "")
           + (f"；⚠ 有 {len(missing)} 份紀錄找不到檔案" if missing else ""))
    hlog("warn" if remote_err or missing else "info", msg, **res)
    cfg_merge("backup_host", {"last_prune": {"at": now.isoformat(timespec="seconds"), "message": msg}})
    Out.emit("info", message=msg)
    return res


def backup_prune() -> int:
    require_root()
    with _lock(120):
        _close_stale_host_jobs()
        with HostJob("backup_prune") as job:
            job.result = _prune()
    Out.emit("done", status="ok", message="清理完成")
    return 0


def backup_run() -> int:
    """systemd istudy-backup.timer（每小時）：驗證新備份 → 到期時上傳一份已驗證的備份 → 每天清理一次。每一步失敗不影響下一步。"""
    require_root()
    key = cfg_get("backup_key")
    if not bc.RECIPIENT_RE.match(str(key.get("recipient") or "")) or not os.path.exists(IDENTITY):
        Out.emit("done", status="skip", message="尚未設定備份恢復密碼：沒有加密備份可處理")
        return 0
    now = _now()
    notes, ok = [], True
    with _lock(float(os.environ.get("ISTUDY_BACKUP_RUN_WAIT", "3000"))):
        _close_stale_host_jobs()
        for r in db_rows(AGE_ROWS + " AND enc_status = 'unverified' ORDER BY id"):
            try:
                _verify_row(r, restore_test=False)
                notes.append(f"#{r['id']} 解密驗證通過")
            except RuntimeError as e:
                ok = False
                notes.append(f"#{r['id']} {e}")
        cfg = None
        try:
            cfg = _remote_cfg()
        except RuntimeError as e:
            ok = False
            notes.append(str(e))
        if cfg:
            rem = cfg_get("backup_remote")
            last_ok = _ts(db_val("SELECT MAX(remote_verified_at) FROM backup_history WHERE remote_status = 'verified'"))
            last_try = rem.get("last_upload") or {}
            recently_failed = not last_try.get("ok", True) and (_ts(last_try.get("at")) or now) > now - _dt.timedelta(hours=6)
            if bc.upload_due(last_ok, now) and not recently_failed:
                row = _upload_candidate()
                if row:
                    try:
                        out = _upload_with_checks(row, cfg, auto=True)
                        notes.append(f"#{row['id']} 已上傳 {out['remote_path']}")
                    except RuntimeError as e:
                        ok = False
                        notes.append(f"#{row['id']} 上傳失敗：{e}")
        state = cfg_get("backup_host")
        if state.get("prune_date") != now.date().isoformat():
            try:
                with HostJob("backup_prune", {"auto": True}) as job:
                    job.result = _prune()
                cfg_merge("backup_host", {"prune_date": now.date().isoformat()})
            except RuntimeError as e:
                ok = False
                notes.append(f"清理失敗：{e}")
    msg = "；".join(notes) or "沒有需要處理的項目"
    cfg_merge("backup_host", {"last_run": {"at": now.isoformat(timespec="seconds"), "ok": ok, "message": msg[:1000]}})
    Out.emit("done", status="ok" if ok else "warn", message=msg)
    return 0


def reset_super() -> int:
    require_root()
    a = getpass.getpass("新的 Super 密碼（至少 10 字）：")
    b = getpass.getpass("再輸入一次：")
    r = php_cli("reset-super", input_text=f"{a}\n{b}\n")
    if not r.get("ok"):
        die(str(r.get("error")))
    print(f"✓ 已重設 Super（{r.get('username')}）的密碼，所有登入已失效")
    return 0


def update(bundle: str) -> int:
    """從 USB 更新（第 18 章）：驗證 → 預檢 → 備份資料庫 → 載入新映像 → migration → 重啟 → Server App → Client 安裝檔 → 自我檢查；失敗自動復原。"""
    require_root()
    bundle = os.path.abspath(bundle)
    steps = []

    def s(name: str, fn: Callable[[], str]) -> str:
        Out.emit("step", step=len(steps) + 1, name=name, status="running")
        msg = fn()
        steps.append(name)
        Out.emit("step", step=len(steps), name=name, status="ok", message=msg)
        return msg

    old_version = _json(os.path.join(OPT, "VERSION.json")).get("server")
    new = bundle_version(bundle)
    work = os.path.join(VARLIB, "update")
    os.makedirs(work, exist_ok=True)
    dump = os.path.join(work, f"before-{new.get('server')}.sql")
    try:
        s("驗證套件", lambda: f"{verify_bundle(bundle)} 個檔案雜湊值相符")
        pre = preflight(bundle=bundle)
        for r in pre:
            Out.emit("check", **r)
        if checks.summarize(pre) == "fail" and not os.environ.get("ISTUDY_UPDATE_IGNORE_PREFLIGHT"):
            raise RuntimeError("預檢有失敗項目")

        def dump_db() -> str:
            pg_dump_to(dump)
            return dump
        s("備份資料庫", dump_db)
        s("暫停 Agent", lambda: (compose("stop", "agent", check=False), "進行中的轉檔中止後會重新排入")[1])
        for ref in ("istudy/php", "istudy/agent"):
            run(["docker", "tag", f"{ref}:{old_version}", f"{ref}:rollback"], check=False)
        saved = os.path.join(work, "rollback-config")
        shutil.rmtree(saved, ignore_errors=True)
        os.makedirs(os.path.join(saved, "nginx"))
        for name in ("VERSION.json", "compose.yaml"):
            shutil.copy2(os.path.join(OPT, name), saved)
        for f in glob.glob(os.path.join(OPT, "server", "nginx", "*.conf")):
            shutil.copy2(f, os.path.join(saved, "nginx"))
        s("載入新映像", lambda: step_load_images(bundle))
        s("更新設定檔", lambda: step_config(bundle))
        s("套用 migration", lambda: (compose("up", "-d", "--no-build", "php"), json.dumps(php_cli("migrate"), ensure_ascii=False))[1])
        s("重新啟動服務", step_start)
        debs = sorted(glob.glob(os.path.join(bundle, "app", "istudy-server-app_*.deb")))
        if debs:
            s("安裝 Server App", lambda: (run(["dpkg", "-i", debs[-1]], timeout=600), os.path.basename(debs[-1]))[1])
        s("Client 安裝檔", lambda: step_clients(bundle))

        def self_check() -> str:
            res = doctor_checks()
            for r in res:
                Out.emit("check", **r)
            if checks.summarize(res) == "fail":
                raise RuntimeError("自我檢查失敗")
            return "通過"
        s("自我檢查", self_check)
    except Exception as e:  # noqa: BLE001
        Out.emit("step", step=len(steps) + 1, name="失敗，自動復原上一版", status="running", message=str(e))
        try:
            env = read_env()
            env["ISTUDY_VERSION"] = "rollback"
            _write_env(env)
            saved = os.path.join(work, "rollback-config")
            if os.path.isdir(saved):
                for name in ("VERSION.json", "compose.yaml"):
                    if os.path.exists(os.path.join(saved, name)):
                        shutil.copy2(os.path.join(saved, name), os.path.join(OPT, name))
                for f in glob.glob(os.path.join(saved, "nginx", "*.conf")):
                    shutil.copy2(f, os.path.join(OPT, "server", "nginx"))
            compose("stop", "agent", "php", check=False)
            if os.path.exists(dump):
                pg_admin(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_NAME}' AND pid <> pg_backend_pid()")
                drop_aux_db("istudy_failed_update")
                pg_admin(f"ALTER DATABASE {DB_NAME} RENAME TO istudy_failed_update")
                create_db(DB_NAME)
                pg_admin(file=dump, db=DB_NAME, as_istudy=True)
            compose("up", "-d", "--no-build", check=False)
            Out.emit("step", step=len(steps) + 1, name="失敗，自動復原上一版", status="ok", message="已恢復更新前的資料庫與映像")
        except Exception as e2:  # noqa: BLE001
            Out.emit("step", step=len(steps) + 1, name="失敗，自動復原上一版", status="fail", message=str(e2))
        report = Out.save_report("update")
        Out.emit("done", status="fail", message=f"更新失敗：{e}（報告 {report}）", report=report)
        return 1
    report = Out.save_report("update")
    Out.emit("done", status="ok", message=f"已更新為 {new.get('server')}（報告 {report}）", report=report)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="istudyctl", description="iStudy 主機維護（只接受固定子指令）")
    ap.add_argument("--json", action="store_true", help="以 JSON 逐行輸出進度（Server App 使用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--bundle")
    b.add_argument("--check-only", action="store_true")
    b.add_argument("--restore")
    b.add_argument("--super-user")
    b.add_argument("--password-stdin", action="store_true", help="從標準輸入讀取兩行 Super 密碼")
    b.add_argument("--accept-warnings", action="store_true")
    for name in ("doctor", "status", "check-ip", "check-media", "check-db", "hostcheck", "restart", "reset-super", "list-disks"):
        sub.add_parser(name)
    u = sub.add_parser("update")
    u.add_argument("--bundle", required=True)
    d = sub.add_parser("backup-disk")
    d.add_argument("--uuid", required=True)
    bk = sub.add_parser("backup")
    bk.add_argument("--destination")
    r = sub.add_parser("restore")
    r.add_argument("--file", required=True)
    r.add_argument("--target", choices=["test", "prod"], default="test")
    am = sub.add_parser("asr-models", help="字幕辨識模型：狀態或下載")
    am.add_argument("--fetch", choices=sorted(ASR_TARGETS))
    ue = sub.add_parser("uem", help="主機盤點（只讀）")
    ue.add_argument("--mode", choices=["quick", "full"], default="quick")
    ma = sub.add_parser("media-acl", help="教材權限：預設只檢查；--fix 確認後修復")
    ma.add_argument("--fix", action="store_true")
    cp = sub.add_parser("clients-publish", help="發布新版 Client 安裝檔（各台 Client 自動更新）")
    cp.add_argument("--from", dest="src", required=True, help="原始碼包的 dist/clients 資料夾")
    en = sub.add_parser("encoder", help="查看或指定正式轉檔編碼器")
    en.add_argument("choice", nargs="?", choices=ENCODER_CHOICES)
    # dev.62 加密備份（方案 B）
    bkk = sub.add_parser("backup-key", help="備份恢復密碼與加密金鑰（密碼只從標準輸入或終端機讀取）")
    g = bkk.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_const", const="init", dest="mode")
    g.add_argument("--change", action="store_const", const="change", dest="mode")
    g.add_argument("--verify", action="store_const", const="verify", dest="mode")
    g.add_argument("--status", action="store_const", const="status", dest="mode")
    g.add_argument("--import", action="store_const", const="import", dest="mode")
    bkk.add_argument("--file", help="--import 用：恢復金鑰檔")
    bkk.add_argument("--password-stdin", action="store_true", help="從標準輸入讀取恢復密碼（新設定、修改要兩行）")
    bkr = sub.add_parser("backup-remote", help="遠端備份（FTPS）設定與連線測試")
    g2 = bkr.add_mutually_exclusive_group(required=True)
    g2.add_argument("--set", action="store_const", const="set", dest="mode", help="從標準輸入讀一行 JSON：host、port、username、password、dir")
    g2.add_argument("--test", action="store_const", const="test", dest="mode")
    bkv = sub.add_parser("backup-verify", help="加密備份：解密驗證（可加隔離還原測試）")
    bkv.add_argument("--id", type=int)
    bkv.add_argument("--restore-test", action="store_true")
    bku = sub.add_parser("backup-upload", help="上傳加密備份到遠端（先解密驗證＋還原測試，上傳後下載比對）")
    bku.add_argument("--id", type=int)
    sub.add_parser("backup-prune", help="依保留天數清理加密備份（最後一份可用備份永遠保留）")
    sub.add_parser("backup-run", help="排程用：驗證新備份、到期上傳、每日清理")
    args = ap.parse_args(argv)
    Out.json_mode = args.json
    try:
        if args.cmd == "bootstrap":
            return bootstrap(args)
        if args.cmd == "doctor":
            res = doctor_checks()
            for x in res:
                Out.emit("check", **x)
            s = checks.summarize(res)
            Out.emit("done", status=s, message=f"自我檢查：{s}")
            return 0 if s != "fail" else 1
        if args.cmd == "status":
            return status()
        if args.cmd == "clients-publish":
            return clients_publish(args.src)
        if args.cmd == "check-ip":
            return check_ip()
        if args.cmd == "check-media":
            return 0 if check_media() else 2
        if args.cmd == "check-db":
            return 0 if check_db(int(os.environ.get("ISTUDY_DB_WAIT", "90"))) else 2
        if args.cmd == "hostcheck":
            require_root()
            data = hostcheck(quiet=not args.json)
            if args.json:
                Out.emit("done", status=checks.summarize(data["checks"]), message="主機狀態已更新（" + data["time"] + "）")
            return 0
        if args.cmd == "restart":
            return restart()
        if args.cmd == "reset-super":
            return reset_super()
        if args.cmd == "list-disks":
            disks = list_disks()
            Out.emit("disks", disks=disks)
            if not args.json:
                for x in disks:
                    print(f"{x['name']:>10}  {x['uuid']}  {x['fstype']:>6}  {x['size'] / 1024 ** 3:8.1f} GB  {x['label']} {x['model']}")
            return 0
        if args.cmd == "update":
            return update(args.bundle)
        if args.cmd == "backup-disk":
            return backup_disk(args.uuid)
        if args.cmd == "backup":
            return backup_cmd(args.destination)
        if args.cmd == "uem":
            return uem_run(args.mode)
        if args.cmd == "media-acl":
            return media_acl(args.fix)
        if args.cmd == "encoder":
            return encoder_cmd(args.choice)
        if args.cmd == "asr-models":
            return asr_models(args.fetch)
        if args.cmd == "restore":
            msg = restore(args.file, args.target)
            Out.emit("done", status="ok", message=msg)
            return 0
        if args.cmd == "backup-key":
            return backup_key(args.mode, args.password_stdin, args.file)
        if args.cmd == "backup-remote":
            return backup_remote(args.mode)
        if args.cmd == "backup-verify":
            return backup_verify(args.id, args.restore_test)
        if args.cmd == "backup-upload":
            return backup_upload(args.id)
        if args.cmd == "backup-prune":
            return backup_prune()
        if args.cmd == "backup-run":
            return backup_run()
    except bc.Busy as e:
        die(f"{e}，請稍後再試", 75)
    except RuntimeError as e:
        die(str(e))
    except KeyboardInterrupt:
        die("已中斷", 130)
    return 1


if __name__ == "__main__":
    sys.exit(main())
