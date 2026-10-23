"""環境檢查：istudyctl（bootstrap 預檢、doctor、hostcheck）與 Agent（diagnose）共用同一套程式（第 17 章）。

每個檢查回傳 dict：{"id", "name", "status": pass|warn|fail|skip, "message", "value"}。
只用 Python 標準函式庫；外部指令不存在時回傳 skip，不會讓整體失敗。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
from typing import Optional

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
GIB = 1024 ** 3


def result(cid: str, name: str, status: str, message: str = "", value=None) -> dict:
    return {"id": cid, "name": name, "status": status, "message": message, "value": value}


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    """執行固定指令（不經過 shell）。指令不存在回傳 127。"""
    if not shutil.which(cmd[0]):
        return 127, "", f"{cmd[0]} not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 126, "", str(e)


# ---------- 系統資訊 ----------

def os_release(path: str = "/etc/os-release") -> dict:
    info = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    info[k] = v.strip('"')
    except OSError:
        pass
    return info


def mem_total_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def mem_available_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def load_avg() -> Optional[list[float]]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def disk_usage(path: str) -> Optional[dict]:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {"path": path, "total": u.total, "used": u.used, "free": u.free,
            "free_pct": round(u.free * 100 / u.total, 1) if u.total else 0}


def is_mountpoint(path: str) -> bool:
    try:
        return os.path.ismount(path)
    except OSError:
        return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def local_ipv4() -> list[str]:
    rc, out, _ = run(["ip", "-4", "-o", "addr", "show"])
    if rc == 0:
        return sorted(set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", out)) - {"127.0.0.1"})
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)} - {"127.0.0.1"})
    except OSError:
        return []


# ---------- 個別檢查 ----------

def check_os(expected_version: str = "26.04") -> dict:
    info = os_release()
    name = info.get("PRETTY_NAME", platform.platform())
    arch = platform.machine()
    ubuntu_x64 = info.get("ID") == "ubuntu" and arch in ("x86_64", "amd64")
    ver = info.get("VERSION_ID", "")
    if ubuntu_x64 and ver.startswith(expected_version):
        return result("os", "作業系統", PASS, "", f"{name} {arch}")
    if ubuntu_x64 and ver.startswith("24.04"):
        # MVP：上一版 LTS 可以使用（Docker .deb 需對應 24.04）；正式目標仍是 26.04
        return result("os", "作業系統", WARN, f"目標為 Ubuntu {expected_version} LTS；目前 {name} 可以使用，Docker 的 .deb 需對應 24.04", f"{name} {arch}")
    return result("os", "作業系統", FAIL, f"需要 Ubuntu {expected_version} LTS x64（目前：{name}，{arch}）", f"{name} {arch}")


def check_ram(min_gib: int = 8) -> dict:
    t = mem_total_bytes()
    if t is None:
        return result("ram", "記憶體", SKIP, "無法讀取 /proc/meminfo")
    gib = round(t / GIB, 1)
    return result("ram", "記憶體", PASS if gib >= min_gib - 0.5 else FAIL,
                  "" if gib >= min_gib - 0.5 else f"至少需要 {min_gib}GB（目前 {gib}GB）", f"{gib} GB")


def check_system_disk(path: str = "/", min_free_gib: int = 30) -> dict:
    u = disk_usage(path)
    if not u:
        return result("system_disk", "系統碟剩餘空間", SKIP, "無法讀取")
    free = round(u["free"] / GIB, 1)
    ok = free >= min_free_gib
    return result("system_disk", "系統碟剩餘空間", PASS if ok else FAIL,
                  "" if ok else f"系統碟剩餘不足 {min_free_gib}GB（目前 {free}GB）", f"{free} GB")


def mount_fstype(path: str, mounts: str = "/proc/self/mounts") -> str:
    """掛載點 path 的檔案系統類型（/proc/self/mounts；空白等字元以 \\040 表示）。"""
    found = ""
    try:
        with open(mounts, encoding="utf-8") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and p[1].replace("\\040", " ") == path:
                    found = p[2]  # 同一點有多層掛載時取最後一層
    except OSError:
        pass
    return found


def check_media_mount(media_root: str = "/Media", require_mount: bool = True) -> dict:
    if not os.path.isdir(media_root):
        return result("media_mount", "/Media 掛載", FAIL, f"{media_root} 不存在；請先把 ZFS dataset 掛載到 {media_root}")
    if require_mount and not is_mountpoint(media_root):
        return result("media_mount", "/Media 掛載", FAIL, f"{media_root} 不是掛載點（ZFS 尚未掛載？）")
    if require_mount:
        fstype = mount_fstype(media_root)
        if fstype != "zfs":
            return result("media_mount", "/Media 掛載", FAIL, f"{media_root} 不是 ZFS（目前是 {fstype or '未知'}）；教材必須放在 ZFS Mirror")
    if not os.access(media_root, os.W_OK):
        return result("media_mount", "/Media 掛載", FAIL, f"{media_root} 不可寫入")
    return result("media_mount", "/Media 掛載", PASS, "", media_root)


def check_media_free(media_root: str = "/Media", warn_pct: float = 20.0) -> dict:
    u = disk_usage(media_root)
    if not u:
        return result("media_free", "/Media 剩餘空間", SKIP, "無法讀取")
    ok = u["free_pct"] >= warn_pct
    return result("media_free", "/Media 剩餘空間", PASS if ok else WARN,
                  "" if ok else f"/Media 剩餘不足 {warn_pct:.0f}%（{u['free_pct']}%）", u)


def _istudy_port_owners() -> str:
    rc, out, _ = run(["docker", "ps", "--format", "{{.Names}} {{.Ports}}"])
    return out if rc == 0 else ""


def _owned_by_istudy(port: int, owners: str, hint: str = "istudy") -> bool:
    return bool(re.search(rf"{hint}\S*\s+.*:{port}->", owners))


def check_ports(ports=(4080, 4081), allowed_owner_hint: str = "istudy") -> dict:
    name = "連接埠 " + "／".join(map(str, ports))
    busy = [p for p in ports if port_in_use(p)]
    if not busy:
        return result("ports", name, PASS, "", [])
    # 已部署過的 iStudy 自己占用，不算衝突
    owners = _istudy_port_owners()
    foreign = [p for p in busy if not _owned_by_istudy(p, owners, allowed_owner_hint)]
    if not foreign:
        return result("ports", name, PASS, "已由 iStudy 使用", busy)
    return result("ports", name, FAIL, "被其他程式占用：" + "、".join(map(str, foreign)), foreign)


def pg_clusters() -> list[dict]:
    """主機 PostgreSQL 叢集（Ubuntu postgresql-common 的 pg_lsclusters；一般使用者也能執行）。"""
    if not shutil.which("pg_lsclusters"):
        return []
    code, out, _ = run(["pg_lsclusters", "-h"])
    if code != 0:
        return []
    res = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 6 and f[2].isdigit():
            res.append({"version": f[0], "cluster": f[1], "port": int(f[2]), "status": f[3], "owner": f[4], "data_directory": f[5]})
    return res


def check_postgres(expected_major: str = "18") -> dict:
    """iStudy 使用主機的 PostgreSQL（預設 18）：必須有一個該版本、執行中的叢集。"""
    name = f"PostgreSQL {expected_major}（主機）"
    cl = pg_clusters()
    if not cl:
        return result("postgres", name, FAIL, f"找不到主機 PostgreSQL（pg_lsclusters）；iStudy 使用 Ubuntu 主機的 PostgreSQL {expected_major}")
    same = [c for c in cl if c["version"] == expected_major]
    up = [c for c in same if c["status"].startswith("online")]
    if up:
        c = sorted(up, key=lambda c: (c["cluster"] != "main", c["port"]))[0]
        return result("postgres", name, PASS, f"{c['version']}/{c['cluster']}，連接埠 {c['port']}，資料目錄 {c['data_directory']}", c)
    if same:
        return result("postgres", name, FAIL, f"PostgreSQL {expected_major} 叢集沒有執行（sudo systemctl start postgresql）", same)
    return result("postgres", name, FAIL, "主機只有其他版本：" + "、".join(f"{c['version']}/{c['cluster']}" for c in cl), cl)


def check_docker_compose(has_debs: bool = False) -> dict:
    """Docker Compose（這台主機原本使用 Docker CE＋Compose plugin）；沒有時只有在套件含 Docker 安裝檔時才能繼續。"""
    name = "Docker Compose"
    if shutil.which("docker"):
        code, out, _ = run(["docker", "compose", "version", "--short"])
        if code == 0:
            c2, o2, _ = run(["docker", "info", "--format", "{{.ServerVersion}}"])
            if c2 == 0:
                return result("docker", name, PASS, f"Compose {out.strip()}，Docker Engine {o2.strip()}")
            return result("docker", name, WARN, f"Compose {out.strip()}；Docker 服務沒有執行（部署時會執行 systemctl enable --now docker）")
    if has_debs:
        return result("docker", name, WARN, "沒有 Docker Compose：部署第 3 步會從套件的 debs/ 安裝")
    return result("docker", name, FAIL, "沒有 Docker Compose（docker compose version 失敗），套件也沒有 Docker 安裝檔：請先安裝 Docker（不要混裝 docker.io 與 docker-ce）")


def check_ip(expected: str = "10.3.3.100") -> dict:
    ips = local_ipv4()
    ok = expected in ips
    return result("ip", "固定 IP", PASS if ok else WARN,
                  "" if ok else f"{expected} 未設定在本機（偵測到：{', '.join(ips) or '無'}）；只警告，不修改網卡", ips)


def check_dri(device: str = "/dev/dri/renderD128") -> dict:
    ok = os.path.exists(device)
    return result("dri", "內顯（Quick Sync／VA-API）", PASS if ok else WARN,
                  "" if ok else f"找不到 {device}，轉檔將改用 CPU（請確認 BIOS 已啟用內顯）", device if ok else None)


def check_ntp() -> dict:
    rc, out, _ = run(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"])
    if rc != 0:
        return result("ntp", "時間同步", SKIP, "無法執行 timedatectl")
    vals = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    synced = vals.get("NTPSynchronized") == "yes"
    tz = vals.get("Timezone", "")
    msgs = []
    if not synced:
        msgs.append("NTP 未同步（LAN 沒有 NTP 時請手動校正時間）")
    if tz and tz != "Asia/Taipei":
        msgs.append(f"時區為 {tz}，建議設定 Asia/Taipei")
    return result("ntp", "時間同步與時區", PASS if not msgs else WARN, "；".join(msgs), vals)


def desktop_sessions() -> list[str]:
    """已安裝的桌面工作階段（/usr/share/xsessions、wayland-sessions 的名稱）。"""
    names = []
    for d, kind in (("/usr/share/xsessions", "X11"), ("/usr/share/wayland-sessions", "Wayland")):
        try:
            for f in sorted(os.listdir(d)):
                if f.endswith(".desktop"):
                    names.append(f"{f[:-8]}（{kind}）")
        except OSError:
            pass
    return names


def check_desktop() -> dict:
    """Server App 需要圖形桌面（XFCE、GNOME 等都可以；X11 或 Wayland）。只警告：服務本身不需要桌面。"""
    sessions = desktop_sessions()
    dm = ""
    try:
        with open("/etc/X11/default-display-manager", encoding="utf-8") as f:
            dm = os.path.basename(f.read().strip())
    except OSError:
        pass
    if not sessions:
        return result("desktop", "桌面環境", WARN, "找不到圖形桌面，Server App 無法使用（服務本身不受影響；可用 sudo ./install.sh --cli）")
    return result("desktop", "桌面環境", PASS, "、".join(sessions) + (f"；登入管理器 {dm}" if dm else ""), {"sessions": sessions, "display_manager": dm})


# 常見的 polkit 驗證代理（負責跳出系統密碼視窗）；GNOME Shell 內建，不另外執行
POLKIT_AGENTS = (
    "/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1",
    "/usr/libexec/policykit-1-gnome/polkit-gnome-authentication-agent-1",
    "/usr/libexec/polkit-gnome-authentication-agent-1",
    "/usr/lib/x86_64-linux-gnu/polkit-mate/polkit-mate-authentication-agent-1",
    "/usr/libexec/polkit-mate-authentication-agent-1",
    "/usr/lib/xfce-polkit/xfce-polkit", "/usr/libexec/xfce-polkit", "/usr/bin/xfce-polkit",
    "/usr/bin/lxpolkit", "/usr/bin/lxqt-policykit-agent",
    "/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1",
    "/usr/bin/gnome-shell",
)
_AGENT_RE = re.compile(r"(polkit[-_\w]*agent|xfce-polkit|lxpolkit|policykit-agent|gnome-shell)")


def running_polkit_agents(proc: str = "/proc") -> list[str]:
    found = set()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                cmd = f.read().split(b"\0")[0].decode("utf-8", "replace")
        except OSError:
            continue
        if _AGENT_RE.search(os.path.basename(cmd)):
            found.add(os.path.basename(cmd))
    return sorted(found)


def check_polkit_agent() -> dict:
    """Server App 以 pkexec 執行 istudyctl，需要 pkexec 與桌面上的 polkit 驗證代理（跳出系統密碼視窗）。"""
    name = "系統密碼視窗（pkexec＋polkit 驗證代理）"
    if not shutil.which("pkexec"):
        return result("polkit_agent", name, WARN, "沒有 pkexec：Server App 的部署與主機維護按鈕無法使用（sudo apt install pkexec，或改用 sudo ./install.sh --cli）")
    running = running_polkit_agents()
    if running:
        return result("polkit_agent", name, PASS, "執行中：" + "、".join(running), running)
    installed = [p for p in POLKIT_AGENTS if os.path.exists(p)]
    if installed:
        return result("polkit_agent", name, WARN, "已安裝但目前沒有在桌面執行（登出再登入桌面；或確認桌面的自動啟動項目有啟用）", installed)
    return result("polkit_agent", name, WARN, "沒有 polkit 驗證代理：按 Server App 的部署按鈕時不會跳出系統密碼視窗（XFCE 需另外安裝，例如 policykit-1-gnome；或改用 sudo ./install.sh --cli）")


def zpool_status() -> dict:
    rc, out, err = run(["zpool", "list", "-H", "-o", "name,size,alloc,free,health"])
    if rc == 127:
        return {"available": False}
    pools = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            pools.append(dict(zip(["name", "size", "alloc", "free", "health"], parts)))
    rc2, out2, _ = run(["zpool", "status", "-x"])
    return {"available": True, "pools": pools, "summary": out2.strip()[:2000], "error": err.strip()[:500] if rc else ""}


def check_zfs() -> dict:
    z = zpool_status()
    if not z.get("available"):
        return result("zfs", "ZFS", SKIP, "沒有 zpool 指令")
    bad = [p for p in z.get("pools", []) if p["health"] != "ONLINE"]
    if not z.get("pools"):
        return result("zfs", "ZFS", WARN, "沒有 ZFS pool", z)
    return result("zfs", "ZFS", PASS if not bad else WARN,
                  "" if not bad else "pool 狀態：" + "、".join(f"{p['name']}={p['health']}" for p in bad), z)


def smart_status() -> list[dict]:
    rc, out, _ = run(["smartctl", "--scan-open"])
    if rc == 127:
        return []
    disks = []
    for line in out.splitlines():
        dev = line.split()[0] if line.strip() and not line.startswith("#") else None
        if not dev:
            continue
        rc2, out2, _ = run(["smartctl", "-H", "-j", dev], timeout=30)
        try:
            j = json.loads(out2)
            passed = j.get("smart_status", {}).get("passed")
            temp = j.get("temperature", {}).get("current")
            model = j.get("model_name") or j.get("model_family") or ""
        except ValueError:
            passed, temp, model = None, None, ""
        disks.append({"device": dev, "model": model, "passed": passed, "temperature": temp})
    return disks


def check_smart() -> dict:
    disks = smart_status()
    if not disks:
        return result("smart", "SMART", SKIP, "沒有 smartctl 或找不到磁碟")
    bad = [d for d in disks if d["passed"] is False]
    return result("smart", "SMART", PASS if not bad else WARN,
                  "" if not bad else "SMART 異常：" + "、".join(d["device"] for d in bad), disks)


def ffmpeg_version() -> Optional[str]:
    rc, out, _ = run(["ffmpeg", "-hide_banner", "-version"])
    return out.splitlines()[0] if rc == 0 and out else None


def libreoffice_version() -> Optional[str]:
    for exe in ("soffice", "libreoffice"):
        rc, out, _ = run([exe, "--version"], timeout=60)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0]
    return None


def docker_versions() -> dict:
    rc, out, _ = run(["docker", "version", "--format", "{{.Server.Version}}"])
    rc2, out2, _ = run(["docker", "compose", "version", "--short"])
    return {"docker": out.strip() if rc == 0 else None, "compose": out2.strip() if rc2 == 0 else None}


def thermal() -> list[dict]:
    out = []
    base = "/sys/class/thermal"
    try:
        for z in sorted(os.listdir(base)):
            if not z.startswith("thermal_zone"):
                continue
            try:
                with open(os.path.join(base, z, "temp")) as f:
                    t = int(f.read().strip()) / 1000
                with open(os.path.join(base, z, "type")) as f:
                    typ = f.read().strip()
                out.append({"zone": typ, "celsius": round(t, 1)})
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return out


def summarize(results: list[dict]) -> str:
    if any(r["status"] == FAIL for r in results):
        return FAIL
    if any(r["status"] == WARN for r in results):
        return WARN
    return PASS
