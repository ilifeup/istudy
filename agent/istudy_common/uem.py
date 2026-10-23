"""主機盤點（UEM）：`istudyctl uem` 與 Server App「主機盤點（UEM）」頁使用，只在 Ubuntu Server 上執行。

磁碟歸屬鏈改寫自 UEM v0.8.1（uem_istudy.py）：路徑 → 掛載點 → 分割區 → 實體磁碟（SSD／HDD 依 ROTA），
ZFS 則是 dataset → pool → 成員磁碟。沿用 UEM 的原則「未知就標未知」：讀不到就是 UNKNOWN，
不以路徑字串（例如 /Media）推測儲存位置。

只讀：不改系統設定、不讀 .env 內容（只看是否存在與權限）、不清理或刪除任何東西、不常駐、不建資料庫。
只用 Python 標準函式庫；外部指令都以參數陣列執行（不經過 shell），工具不存在時該項標 UNKNOWN。
"""
from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional

SCHEMA = "istudy-uem/1"
PASS, WARN, FAIL, UNKNOWN, MISSING, INFO = "PASS", "WARN", "FAIL", "UNKNOWN", "MISSING", "INFO"
STATUS_MARK = {PASS: "✓", WARN: "⚠", FAIL: "✗", UNKNOWN: "?", MISSING: "－", INFO: "·", "SKIP": "－"}
STATUS_TEXT = {PASS: "通過", WARN: "注意", FAIL: "異常", UNKNOWN: "未知", MISSING: "不存在", INFO: "資訊", "SKIP": "略過"}
_ORDER = {FAIL: 0, WARN: 1, MISSING: 2, UNKNOWN: 3, INFO: 4, PASS: 5}


# ---------------------------------------------------------------- 外部指令 ----

def cmd(args: list[str], timeout: int = 8) -> Optional[str]:
    """結束碼 0 時回傳 stdout；失敗或工具不存在回傳 None。"""
    if not shutil.which(args[0]):
        return None
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cmd_any(args: list[str], timeout: int = 8) -> Optional[str]:
    """不論結束碼都回傳 stdout（systemctl is-active 需要）；工具不存在回傳 None。"""
    if not shutil.which(args[0]):
        return None
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def http_json(url: str, timeout: int = 4) -> tuple[Optional[int], Optional[dict]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            try:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
            except ValueError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except OSError:
        return None, None


# ------------------------------------------------------------- 區塊裝置（UEM） ----

def read_lsblk() -> Optional[list]:
    """lsblk 樹狀資料；依 util-linux 版本由新到舊嘗試欄位組合。讀不到回傳 None。"""
    for cols in ("NAME,KNAME,PATH,SIZE,MODEL,FSTYPE,MOUNTPOINTS,TYPE,ROTA,TRAN",
                 "NAME,KNAME,PATH,SIZE,MODEL,FSTYPE,MOUNTPOINT,TYPE,ROTA,TRAN",
                 "NAME,SIZE,MODEL,FSTYPE,MOUNTPOINT,TYPE"):
        raw = cmd(["lsblk", "-J", "-b", "-o", cols])
        if raw:
            try:
                return json.loads(raw).get("blockdevices", [])
            except ValueError:
                continue
    return None


def _rota(v) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true")
    return bool(v)


def flatten_lsblk(devices, parent=None, out=None) -> list[dict]:
    """攤平；每個節點記錄上層（同一裝置可能出現在多個上層之下，例如 RAID／LVM）。"""
    if out is None:
        out = []
    for d in devices or []:
        mps = d.get("mountpoints")
        if mps is None:
            mps = [d.get("mountpoint")]
        name = d.get("name") or ""
        out.append({
            "name": name, "kname": d.get("kname") or name,
            "path": d.get("path") or ("/dev/" + name if name else ""),
            "model": (d.get("model") or "").strip(), "size": d.get("size"),
            "fstype": d.get("fstype") or "", "mountpoints": [m for m in (mps or []) if m],
            "parent": parent, "type": d.get("type") or "", "rota": _rota(d.get("rota")), "tran": d.get("tran") or "",
        })
        flatten_lsblk(d.get("children") or [], parent=name, out=out)
    return out


def _gb(v) -> Optional[float]:
    try:
        return round(float(v) / 1e9, 1) if v else None
    except (TypeError, ValueError):
        return None


class DiskIndex:
    def __init__(self, flat: list[dict]):
        self.flat = flat
        self.by_name: dict[str, dict] = {}
        self.parents: dict[str, set] = {}
        self.info: dict[str, dict] = {}
        for d in flat:
            for key in {d["name"], d["kname"], d["path"]}:
                if key:
                    self.by_name.setdefault(key, d)
            if d["parent"]:
                self.parents.setdefault(d["name"], set()).add(d["parent"])
            self.info.setdefault(d["name"], d)

    def lookup(self, dev: str) -> Optional[dict]:
        if not dev:
            return None
        dev = re.sub(r"\[.*\]$", "", dev)  # btrfs「/dev/x[/@]」
        for key in (dev, os.path.realpath(dev), os.path.basename(dev)):
            if key in self.by_name:
                return self.by_name[key]
        return None

    def roots(self, name: str) -> list[str]:
        """這個裝置所在的實體（最上層）磁碟。"""
        seen, stack, roots = set(), [name], set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            ps = self.parents.get(n)
            if ps:
                stack.extend(ps)
            else:
                roots.add(n)
        return sorted(roots)

    def media(self, name: str) -> str:
        d = self.info.get(name) or {}
        if d.get("rota") is True:
            return "HDD"
        if d.get("rota") is False:
            return "SSD"
        if name.startswith(("nvme", "mmcblk")) or d.get("tran") == "nvme":
            return "SSD"
        return "UNKNOWN"

    def describe(self, name: str) -> dict:
        d = self.info.get(name) or {}
        return {"name": name, "model": d.get("model", ""), "media": self.media(name), "size_gb": _gb(d.get("size")), "tran": d.get("tran", "")}


# ------------------------------------------------------------------- ZFS ----

_ZSTATES = r"ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL|REMOVED|AVAIL|INUSE"
_VDEV_RE = re.compile(r"^(mirror|raidz[123]?|draid\S*|replacing|spare)(-\d+)?$")
_SECTIONS = ("logs", "cache", "spares", "special", "dedup")


def parse_zpool_status(text: Optional[str]) -> dict:
    """`zpool status -P` → {pool: {state, layout, vdevs[], devices[{path,state}]}}；只計資料區（不含 logs／cache／spares）。"""
    pools: dict = {}
    cur, name, base, section = None, None, None, None
    for raw in (text or "").splitlines():
        line = raw.expandtabs(8)
        m = re.match(r"\s*pool:\s*(\S+)", line)
        if m:
            name = m.group(1)
            cur = pools.setdefault(name, {"state": None, "layout": "unknown", "vdevs": [], "devices": []})
            base, section = None, None
            continue
        if cur is None:
            continue
        m = re.match(r"\s*state:\s*(\S+)", line)
        if m and cur["state"] is None and base is None:
            cur["state"] = m.group(1)
            continue
        m = re.match(r"^(\s*)(\S+)\s+(" + _ZSTATES + r")\b", line)
        if not m:
            m2 = re.match(r"^(\s*)(" + "|".join(_SECTIONS) + r")\s*$", line)
            if m2 and base is not None and len(m2.group(1)) == base:
                section = m2.group(2)
            continue
        ind, nm, st = len(m.group(1)), m.group(2), m.group(3)
        if base is None:
            if nm == name:
                base, section = ind, "data"
            continue
        if section != "data" or ind <= base:
            continue
        top = ind == base + 2
        v = _VDEV_RE.match(nm)
        if v:
            if top:
                cur["vdevs"].append({"name": nm, "state": st, "type": re.sub(r"\d$", "", v.group(1)) if v.group(1).startswith("raidz") else v.group(1)})
            continue
        cur["devices"].append({"path": nm, "state": st})
        if top:
            cur["vdevs"].append({"name": nm, "state": st, "type": "disk"})
    for p in pools.values():
        types = [v["type"] for v in p["vdevs"]]
        if not types:
            p["layout"] = "unknown"
        elif all(t == "mirror" for t in types):
            p["layout"] = "mirror"
        elif all(t == "disk" for t in types):
            p["layout"] = "single" if len(types) == 1 else "stripe"
        elif len(set(types)) == 1:
            p["layout"] = types[0]
        else:
            p["layout"] = "mixed"
    return pools


LAYOUT_TEXT = {"mirror": "mirror", "single": "單顆磁碟", "stripe": "多顆串接（無備援）", "raidz": "RAID-Z",
               "draid": "dRAID", "mixed": "混合結構", "unknown": "結構未知"}


def parse_zpool_list(text: Optional[str]) -> dict:
    """`zpool list -H -p -o name,size,alloc,free,health` → {pool: {size, alloc, free, health}}（位元組）。"""
    res = {}
    for line in (text or "").splitlines():
        f = line.split("\t")
        if len(f) == 5:
            try:
                res[f[0]] = {"size": int(f[1]), "alloc": int(f[2]), "free": int(f[3]), "health": f[4]}
            except ValueError:
                res[f[0]] = {"size": None, "alloc": None, "free": None, "health": f[4]}
    return res


# ------------------------------------------------------------ 掛載與分類 ----

def parse_findmnt_pairs(text: Optional[str]) -> Optional[dict]:
    """`findmnt -n -P -o SOURCE,FSTYPE,TARGET` 的 KEY="值" 格式（值內的特殊字元以 \\xHH 表示）。"""
    if not text:
        return None
    first = text.splitlines()[0]
    kv = {k: re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), v)
          for k, v in re.findall(r'(\w+)="([^"]*)"', first)}
    return kv or None


def classify_mount(source: Optional[str], fstype: Optional[str], idx: DiskIndex, zpools: dict) -> dict:
    """這個掛載實際落在哪裡：{actual, pool, layout, pool_state, disks[], chain}。"""
    res = {"actual": "UNKNOWN", "pool": None, "layout": None, "pool_state": None, "disks": [], "chain": ""}
    if fstype == "zfs" and source:
        pool = source.split("/")[0]
        info = zpools.get(pool) or {}
        names, notes = [], []
        for dev in info.get("devices", []):
            d = idx.lookup(dev["path"])
            if d:
                for r in idx.roots(d["name"]):
                    if r not in names:
                        names.append(r)
            if dev["state"] != "ONLINE":
                notes.append(f"{os.path.basename(dev['path'])}={dev['state']}")
        res.update(actual="ZFS", pool=pool, layout=info.get("layout") if info else None, members=len(info.get("devices", [])),
                   pool_state=info.get("state"), disks=[idx.describe(n) for n in names])
        disks_txt = " + ".join(f"{d['name']}（{d['media']}）" for d in res["disks"]) or "成員未知"
        res["chain"] = (f"{source} → pool {pool}（{LAYOUT_TEXT.get(res['layout'] or 'unknown', res['layout'])}，"
                        f"{res['pool_state'] or '狀態未知'}）→ {disks_txt}" + (f"（異常：{'、'.join(notes)}）" if notes else ""))
        return res
    if source and source.startswith("/dev"):
        d = idx.lookup(source)
        if d:
            res["disks"] = [idx.describe(r) for r in idx.roots(d["name"])]
            medias = {x["media"] for x in res["disks"]}
            res["actual"] = medias.pop() if len(medias) == 1 else ("MIXED" if medias else "UNKNOWN")
            res["chain"] = f"{source} → " + " + ".join(f"{x['name']}（{x['media']}）" for x in res["disks"])
            return res
        res["chain"] = f"{source} → 在 lsblk 找不到這個裝置"
        return res
    if fstype in ("tmpfs", "overlay", "devtmpfs", "ramfs", "proc", "sysfs"):
        res.update(actual="VIRTUAL", chain=f"{fstype}（記憶體／虛擬檔案系統，不是實體磁碟）")
    return res


def storage_class(cls: dict, sys_disks: list[str]) -> tuple[str, str]:
    """磁碟類別：(代號, 顯示文字)。系統碟＝與 / 相同的實體磁碟；讀不到就是「未知」。"""
    a = cls["actual"]
    names = sorted(d["name"] for d in cls["disks"])
    on_sys = bool(sys_disks) and bool(names) and names == sorted(sys_disks)
    if a == "ZFS":
        if on_sys:
            return "system", "系統碟（ZFS）"
        if cls.get("layout") == "mirror":   # 以 zpool status 的成員數為準（實體磁碟對應不到時仍是 mirror）
            return "zfs_mirror", f"ZFS mirror（{cls.get('members') or len(names)} 顆）"
        if not cls.get("layout") or cls.get("layout") == "unknown":
            return "zfs", "ZFS（結構未知）"
        return "zfs", f"ZFS（{LAYOUT_TEXT.get(cls['layout'], cls['layout'])}，不是 mirror）"
    if on_sys:
        return "system", {"SSD": "系統 SSD", "HDD": "系統碟（HDD）"}.get(a, "系統碟（類型未知）")
    if a == "SSD":
        return "ssd", "SSD（不是系統碟）"
    if a == "HDD":
        return "hdd", "HDD（不是系統碟）"
    if a == "MIXED":
        return "mixed", "多顆不同類型的磁碟"
    if a == "VIRTUAL":
        return "virtual", "虛擬（不是實體磁碟）"
    return "unknown", "未知"


# ------------------------------------------------------------- 路徑清單 ----

def istudy_paths(opt: str, varlib: str, srv: str, media: str, pg_data: Optional[str], docker_root: Optional[str],
                 pg_major: str = "18") -> list[dict]:
    """要盤點的 iStudy 路徑。expect：system＝應在系統碟；zfs_mirror＝應在 ZFS mirror；None＝只顯示。"""
    j = os.path.join
    return [
        dict(key="root", name="Ubuntu 系統", path="/", purpose="作業系統", expect="system"),
        dict(key="opt", name="程式部署", path=opt, purpose="Compose、程式碼、istudyctl", expect="system"),
        dict(key="env", name="部署設定 .env", path=j(opt, ".env"), purpose="資料庫密碼等設定（只檢查存在與權限，不讀內容）",
             expect="system", secret=True),
        dict(key="models", name="字幕模型", path=j(opt, "models"), purpose="字幕辨識模型：FunASR（asr/ 底下；掛進 agent 的 /models）",
             expect="system", optional=True),
        dict(key="varlib", name="主機狀態", path=varlib, purpose="主機檢查結果、首次設定進度、主機盤點報告", expect="system"),
        dict(key="pgdata", name=f"PostgreSQL {pg_major} 資料", path=pg_data or f"/var/lib/postgresql/{pg_major}/main",
             purpose="主機資料庫", expect="system", guessed=not pg_data),
        dict(key="docker", name="Docker 資料", path=docker_root or "/var/lib/docker", purpose="映像與容器",
             expect="system", guessed=not docker_root),
        dict(key="backup", name="資料庫備份", path=j(srv, "backup"), purpose="每日備份；還原測試從這裡讀取", expect=None),
        dict(key="updates", name="Client 更新檔", path=j(srv, "updates"), purpose="Client 安裝與更新檔下載", expect="system"),
        dict(key="media", name="教材根目錄", path=media, purpose="教材（ZFS mirror）；第一層資料夾＝課程，系統資料夾除外", expect="zfs_mirror"),
        dict(key="playback", name="播放檔", path=j(media, "playback"), purpose="轉檔後的播放檔", expect="zfs_mirror"),
        dict(key="subtitles", name="字幕", path=j(media, "subtitles"), purpose="字幕與逐字稿", expect="zfs_mirror"),
        dict(key="doccache", name="文件快取", path=j(media, "doc-cache"), purpose="文件轉成的 PDF", expect="zfs_mirror"),
        dict(key="old_backups", name="舊備份資料夾", path=j(media, "Backups"), purpose="清理後不應存在", invert=True),
        dict(key="old_installers", name="舊安裝資料夾", path=j(media, "Installers"), purpose="清理後不應存在", invert=True),
        dict(key="old_videos", name="舊教材資料夾", path=j(media, "videos"), purpose="dev.59 起教材直接放在 /Media 的課程資料夾；搬移後不應存在", invert=True),
    ]


class Mounts:
    """findmnt -T 的結果（同一路徑只查一次）。"""

    def __init__(self):
        self.cache: dict[str, Optional[dict]] = {}

    def of(self, path: str) -> Optional[dict]:
        if path not in self.cache:
            self.cache[path] = parse_findmnt_pairs(cmd(["findmnt", "-T", path, "-n", "-P", "-o", "SOURCE,FSTYPE,TARGET"]))
        return self.cache[path]


def _locate(path: str, mounts: Mounts, idx: DiskIndex, zpools: dict, sys_disks: list[str]) -> dict:
    m = mounts.of(path) or {}
    cls = classify_mount(m.get("SOURCE"), m.get("FSTYPE"), idx, zpools)
    sc, label = storage_class(cls, sys_disks)
    return {"mount": m.get("TARGET"), "source": m.get("SOURCE"), "fstype": m.get("FSTYPE"),
            "storage": sc, "storage_label": label, "pool": cls["pool"], "layout": cls["layout"],
            "pool_state": cls["pool_state"], "disks": cls["disks"], "chain": cls["chain"] or ("findmnt 沒有結果" if not m else "")}


def check_path(spec: dict, mounts: Mounts, idx: DiskIndex, zpools: dict, sys_disks: list[str],
               media_pool: Optional[str]) -> dict:
    path = spec["path"]
    exists = os.path.lexists(path)
    item = {k: spec.get(k) for k in ("key", "name", "path", "purpose", "expect")}
    item.update(exists=exists, mount=None, source=None, fstype=None, storage="unknown", storage_label="—",
                pool=None, disks=[], chain="", status=UNKNOWN, detail="")
    if spec.get("invert"):
        item.update(status=WARN if exists else PASS,
                    detail="仍然存在（UEM 只回報，不會刪除；是否清理請人工決定）" if exists else "不存在（正確）")
        if exists:
            item.update(_locate(path, mounts, idx, zpools, sys_disks))
        return item
    if not exists:
        if spec.get("optional"):
            item.update(status=INFO, detail="尚未建立（選用）")
        else:
            item.update(status=MISSING, detail="不存在" + ("（路徑是預設值，實際位置未能讀取）" if spec.get("guessed") else ""))
        return item
    item.update(_locate(path, mounts, idx, zpools, sys_disks))
    notes = []
    if spec.get("secret"):
        try:
            st = os.stat(path)
            mode = stat.S_IMODE(st.st_mode)
            item["mode"] = oct(mode)
            if mode & 0o007:
                notes.append(f"權限 {oct(mode)[2:]}：其他使用者可讀取，應為 640")
        except OSError as e:
            notes.append(f"無法讀取權限：{e.strerror}")
    exp, sc = spec.get("expect"), item["storage"]
    if sc == "unknown" or (exp == "system" and not sys_disks):
        status, detail = UNKNOWN, "無法判斷所在磁碟（請以 findmnt -T 與 lsblk 人工確認）"
    elif exp == "system":
        if sc == "system":
            status, detail = PASS, ""
        else:
            status, detail = WARN, f"不在系統碟上，目前是{item['storage_label']}"
    elif exp == "zfs_mirror":
        if sc == "zfs_mirror":
            status, detail = PASS, ""
            if item["pool_state"] and item["pool_state"] != "ONLINE":
                status, detail = WARN, f"pool 狀態 {item['pool_state']}"
            elif media_pool and item["pool"] != media_pool:
                status, detail = WARN, f"與教材根目錄不同 pool（{item['pool']}，教材根目錄是 {media_pool}）"
        elif sc == "zfs":
            known = item["layout"] not in (None, "unknown")
            status = WARN if known else UNKNOWN
            detail = "不是 mirror：沒有磁碟備援" if known else "在 ZFS 上，但讀不到 pool 結構（zpool status）"
        else:
            status = FAIL
            detail = f"不在 ZFS mirror 上，目前是{item['storage_label']}" + (
                "。ZFS 沒有掛載時，教材會寫進系統碟" if sc == "system" else "")
    else:
        status, detail = INFO, ""
    if notes:
        status = WARN if status in (PASS, INFO) else status
        detail = "；".join([x for x in [detail] + notes if x])
    item.update(status=status, detail=detail)
    return item


# ------------------------------------------------------------- 服務與連接埠 ----

def unit_state(unit: str) -> tuple[Optional[str], Optional[str]]:
    active = cmd_any(["systemctl", "is-active", unit])
    load = cmd_any(["systemctl", "show", unit, "-p", "LoadState", "--value"])
    return (active.splitlines()[0] if active else active), load


SERVICES = [
    # unit, 名稱, 重要（停止＝異常）
    ("docker", "Docker", True),
    ("istudy", "iStudy 開機服務（istudy.service）", True),
    ("istudy-hostcheck.timer", "主機檢查排程（每 10 分鐘）", False),
]


def collect_services(pg: list[dict], pg_major: str) -> list[dict]:
    out = []
    same = [c for c in pg if str(c.get("version")) == str(pg_major)]
    if not pg:
        out.append(dict(name=f"PostgreSQL {pg_major}（主機）", unit="postgresql", status=UNKNOWN, detail="pg_lsclusters 沒有結果"))
    elif not same:
        out.append(dict(name=f"PostgreSQL {pg_major}（主機）", unit="postgresql", status=FAIL,
                        detail="主機只有其他版本：" + "、".join(f"{c['version']}/{c['cluster']}" for c in pg)))
    else:
        for c in same:
            online = str(c.get("status", "")).startswith("online")
            out.append(dict(name=f"PostgreSQL {c['version']}/{c['cluster']}（主機）", unit=f"postgresql@{c['version']}-{c['cluster']}",
                            status=PASS if online else FAIL, detail=f"{c.get('status')}，連接埠 {c.get('port')}"))
    for unit, name, critical in SERVICES:
        active, load = unit_state(unit)
        if not active or active in ("unknown", "offline"):
            status, detail = UNKNOWN, "無法查詢（systemctl 不可用）"
        elif active == "active":
            status, detail = PASS, "active"
        elif load == "not-found":
            status, detail = (FAIL if critical else WARN), "沒有安裝這個服務單元"
        else:
            status, detail = (FAIL if critical else WARN), f"狀態 {active or '未知'}"
        out.append(dict(name=name, unit=unit, status=status, detail=detail))
    # Ubuntu 24.04 起 SSH 預設以 ssh.socket 啟動：兩者任一 active 即可；沒有安裝只是資訊
    a1, l1 = unit_state("ssh")
    a2, _ = unit_state("ssh.socket")
    if not a1 or a1 in ("unknown", "offline"):
        st, d = UNKNOWN, "無法查詢"
    elif "active" in (a1, a2):
        st, d = PASS, "ssh.service" if a1 == "active" else "ssh.socket（連線時才啟動）"
    elif l1 == "not-found":
        st, d = INFO, "沒有安裝"
    else:
        st, d = INFO, f"未啟用（{a1}）"
    out.append(dict(name="SSH", unit="ssh", status=st, detail=d))
    return out


def parse_ss(text: Optional[str]) -> dict[int, list[str]]:
    """`ss -ltnH` → {連接埠: [監聽位址…]}。"""
    res: dict[int, list[str]] = {}
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        local = f[3]
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        addr = addr.strip("[]").split("%")[0]
        res.setdefault(int(port), [])
        if addr not in res[int(port)]:
            res[int(port)].append(addr)
    return res


def _loopback(a: str) -> bool:
    return a.startswith("127.") or a in ("::1", "localhost")


def collect_ports(pg_port: int, http: Callable[[str], tuple]) -> list[dict]:
    ss = cmd(["ss", "-ltnH"])
    lst = parse_ss(ss) if ss is not None else None
    out = []

    def addrs(p):
        return (lst or {}).get(p, [])

    # 4080：LAN 服務
    a = addrs(4080)
    code, _ = http("http://127.0.0.1:4080/api/health")
    if a:
        st, d = PASS, "監聽 " + "、".join(a)
    elif code == 200:
        st, d = PASS, "HTTP 有回應（ss 看不到監聽，可能是 Docker 的轉送方式）"
    else:
        st, d = (FAIL, "沒有監聽：Client 無法連線") if lst is not None else (UNKNOWN, "沒有 ss 指令，且 HTTP 沒有回應")
    out.append(dict(name="TCP 4080（LAN：Client 連線）", port=4080, addrs=a, status=st, detail=d))
    # 4081：管理 API 只能在本機
    a = addrs(4081)
    code, _ = http("http://127.0.0.1:4081/api/health")
    exposed = [x for x in a if not _loopback(x)]
    if exposed:
        st, d = FAIL, "管理 API 對外監聽（" + "、".join(exposed) + "）：應只限 127.0.0.1"
    elif a:
        st, d = PASS, "只限本機（" + "、".join(a) + "）"
    elif code == 200:
        st, d = PASS, "HTTP 有回應（ss 看不到監聽位址，無法確認是否只限本機）"
    else:
        st, d = (FAIL, "沒有監聽：Server App 無法連線") if lst is not None else (UNKNOWN, "沒有 ss 指令，且 HTTP 沒有回應")
    out.append(dict(name="TCP 4081（只限本機：Server App）", port=4081, addrs=a, status=st, detail=d))
    # PostgreSQL TCP：iStudy 只用 Unix socket
    a = addrs(pg_port)
    if lst is None:
        st, d = UNKNOWN, "沒有 ss 指令"
    elif not a:
        st, d = INFO, "沒有開 TCP（iStudy 經 Unix socket 連線，不需要 TCP）"
    elif all(_loopback(x) for x in a):
        st, d = PASS, "只限本機（" + "、".join(a) + "）"
    else:
        st, d = WARN, "對外監聽（" + "、".join(a) + "）：iStudy 只用 Unix socket，可考慮改為只限本機（UEM 不會修改設定）"
    out.append(dict(name=f"TCP {pg_port}（PostgreSQL）", port=pg_port, addrs=a, status=st, detail=d))
    return out


# --------------------------------------------------------------- 容器 ----

_INSPECT_FMT = ('{{.Name}}\t{{index .Config.Labels "com.docker.compose.service"}}\t{{.Config.Image}}\t'
                '{{.State.Status}}\t{{json .Mounts}}')


def collect_containers(project: str = "istudy") -> Optional[list[dict]]:
    """compose 專案的容器（只取名稱、映像、狀態、掛載；不取環境變數）。docker 不可用回傳 None。"""
    ids = cmd(["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    if ids is None:
        return None
    ids_l = ids.split()
    if not ids_l:
        return []
    raw = cmd(["docker", "inspect", "-f", _INSPECT_FMT, *ids_l], timeout=15)
    out = []
    for line in (raw or "").splitlines():
        f = line.split("\t")
        if len(f) < 5:
            continue
        try:
            mounts = json.loads(f[4]) or []
        except ValueError:
            mounts = []
        out.append({
            "name": f[0].lstrip("/"), "service": "" if f[1] == "<no value>" else f[1], "image": f[2], "state": f[3],
            "mounts": [{"type": m.get("Type"), "source": m.get("Source"), "target": m.get("Destination"), "rw": bool(m.get("RW"))}
                       for m in mounts if isinstance(m, dict)],
        })
    return sorted(out, key=lambda c: c["service"] or c["name"])


def other_postgres_containers() -> Optional[list[str]]:
    dps = cmd(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    if dps is None:
        return None
    return [ln for ln in dps.splitlines() if re.search(r"postgres", ln.split("\t")[-1], re.I)]


# --------------------------------------------------------------- 版本 ----

def norm_ver(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return re.sub(r"^v", "", s.replace("~", "-"))


def collect_versions(version_file: str, health: Optional[dict], containers: Optional[list[dict]]) -> dict:
    rows = []
    try:
        with open(version_file, encoding="utf-8") as f:
            v = json.load(f).get("server")
        rows.append(dict(name="已安裝（VERSION.json）", value=norm_ver(v), source=version_file))
    except (OSError, ValueError, AttributeError):
        rows.append(dict(name="已安裝（VERSION.json）", value=None, source=version_file, note="無法讀取"))
    rows.append(dict(name="執行中（API）", value=norm_ver((health or {}).get("version")), source="127.0.0.1:4081/api/health",
                     note="" if health else "API 沒有回應"))
    if containers is None:
        rows.append(dict(name="容器映像", value=None, source="docker", note="docker 不可用"))
    else:
        for c in containers:
            img = c.get("image") or ""
            if img.startswith("istudy/") and ":" in img:
                rows.append(dict(name=f"映像 {c['service'] or c['name']}", value=norm_ver(img.rsplit(":", 1)[1]), source=img))
    deb = cmd(["dpkg-query", "-W", "-f", "${Version}", "istudy-server-app"])
    rows.append(dict(name="Server App（.deb）", value=norm_ver(deb), source="dpkg istudy-server-app",
                     note="" if deb else "未安裝或無法查詢"))
    vals = {r["value"] for r in rows if r["value"]}
    missing = [r["name"] for r in rows if not r["value"]]
    if len(vals) > 1:
        status, detail = WARN, "版本不一致：" + "；".join(f"{r['name']}={r['value']}" for r in rows if r["value"])
    elif not vals:
        status, detail = UNKNOWN, "讀不到任何版本"
    elif missing:
        status, detail = WARN, "讀不到：" + "、".join(missing)
    else:
        status, detail = PASS, f"全部是 {vals.pop()}"
    return {"rows": rows, "status": status, "detail": detail}


# ------------------------------------------------------------- 磁碟對照 ----

_SKIP_DISK = ("loop", "zram", "ram", "sr", "fd")


def build_disk_map(flat: list[dict], idx: DiskIndex, paths: list[dict], zpools: dict, sys_disks: list[str]) -> list[dict]:
    """實體磁碟 → 分割區／ZFS pool → iStudy 目錄。"""
    member_of: dict[str, list[str]] = {}
    for pname, info in zpools.items():
        for dev in info.get("devices", []):
            d = idx.lookup(dev["path"])
            if d:
                for r in idx.roots(d["name"]):
                    member_of.setdefault(r, [])
                    if pname not in member_of[r]:
                        member_of[r].append(pname)
    out = []
    for r in flat:
        if r["parent"] is not None or r["type"] in ("loop", "rom") or r["name"].startswith(_SKIP_DISK):
            continue
        desc = idx.describe(r["name"])
        kids = [d for d in flat if d["parent"] == r["name"]]
        used = [{"name": p["name"], "path": p["path"]} for p in paths
                if p.get("exists") and not p["key"].startswith("old_") and any(x["name"] == r["name"] for x in p.get("disks", []))]
        pools = member_of.get(r["name"], [])
        role = []
        if r["name"] in sys_disks:
            role.append("系統碟")
        for pn in pools:
            role.append(f"ZFS pool {pn}（{LAYOUT_TEXT.get((zpools.get(pn) or {}).get('layout', 'unknown'), '')}）成員")
        out.append({
            **desc, "type": r["type"], "role": "、".join(role) or ("未被 iStudy 使用" if not used else "—"),
            "partitions": [{"name": k["name"], "size_gb": _gb(k.get("size")), "fstype": k["fstype"], "mountpoints": k["mountpoints"]} for k in kids],
            "zfs_pools": pools, "istudy_dirs": used,
        })
    return out


def build_zfs(zpools: dict, zlist: dict, idx: DiskIndex, media_pool: Optional[str], available: bool) -> dict:
    pools = []
    for name in sorted(set(zpools) | set(zlist)):
        p = zpools.get(name) or {}
        lst = zlist.get(name) or {}
        state = p.get("state") or lst.get("health")
        members = []
        for dev in p.get("devices", []):
            d = idx.lookup(dev["path"])
            roots = idx.roots(d["name"]) if d else []
            members.append({"path": dev["path"], "state": dev["state"],
                            "disk": roots[0] if roots else None, "media": idx.media(roots[0]) if roots else "UNKNOWN",
                            "model": (idx.info.get(roots[0]) or {}).get("model", "") if roots else ""})
        layout = p.get("layout", "unknown")
        if state == "ONLINE":
            status, detail = PASS, ""
        elif state == "DEGRADED":
            status, detail = WARN, "DEGRADED：有成員磁碟異常，請盡快更換"
        elif state:
            status, detail = FAIL, state
        else:
            status, detail = UNKNOWN, "讀不到狀態"
        if name == media_pool and status == PASS and layout != "mirror":
            status, detail = WARN, f"教材 pool 不是 mirror（{LAYOUT_TEXT.get(layout, layout)}）：沒有磁碟備援"
        bad = [m for m in members if m["state"] != "ONLINE"]
        if bad:
            detail = "；".join(x for x in [detail, "異常成員：" + "、".join(f"{os.path.basename(m['path'])}={m['state']}" for m in bad)] if x)
        size, free = lst.get("size"), lst.get("free")
        pools.append({"name": name, "state": state, "layout": layout, "layout_text": LAYOUT_TEXT.get(layout, layout),
                      "media_pool": name == media_pool, "members": members, "size": size, "free": free,
                      "used_pct": round(100 * (size - free) / size, 1) if size and free is not None else None,
                      "status": status, "detail": detail})
    return {"available": available, "pools": pools}


# ---------------------------------------------------------------- 彙整 ----

def collect(ctx: dict) -> dict:
    """ctx：opt、varlib、srv、media、pg_major、pg_clusters、hostcheck_file、version_file、project、http（選填）。"""
    http = ctx.get("http") or http_json
    notes = []
    tree = read_lsblk()
    if tree is None:
        notes.append("lsblk 無法執行：磁碟類別一律為「未知」")
    flat = flatten_lsblk(tree or [])
    idx = DiskIndex(flat)
    zraw = cmd(["zpool", "status", "-P"])
    zavailable = zraw is not None or bool(shutil.which("zpool"))
    if zraw is None:
        notes.append("zpool status 無法執行（沒有 ZFS 工具或沒有權限）：ZFS 項目為「未知」")
    zpools = parse_zpool_status(zraw)
    zlist = parse_zpool_list(cmd(["zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health"]))

    pg = ctx.get("pg_clusters") or []
    pg_major = str(ctx.get("pg_major") or "18")
    pg_same = [c for c in pg if str(c.get("version")) == pg_major]
    pg_c = sorted(pg_same, key=lambda c: (c.get("cluster") != "main", c.get("port") or 0))[0] if pg_same else None
    docker_root = cmd(["docker", "info", "-f", "{{.DockerRootDir}}"], timeout=10)

    mounts = Mounts()
    root_loc = _locate("/", mounts, idx, zpools, [])
    sys_disks = [d["name"] for d in root_loc["disks"]]
    media = ctx["media"]
    mloc = _locate(media, mounts, idx, zpools, sys_disks) if os.path.isdir(media) else {}
    media_pool = mloc.get("pool") if mloc.get("fstype") == "zfs" else None

    specs = istudy_paths(ctx["opt"], ctx["varlib"], ctx["srv"], media,
                         pg_c.get("data_directory") if pg_c else None, docker_root, pg_major)
    paths = [check_path(s, mounts, idx, zpools, sys_disks, media_pool) for s in specs]
    backup = next(p for p in paths if p["key"] == "backup")
    pgd = next(p for p in paths if p["key"] == "pgdata")
    if backup["exists"] and pgd["exists"] and backup["disks"] and \
            sorted(d["name"] for d in backup["disks"]) == sorted(d["name"] for d in pgd["disks"]):
        backup["detail"] = "與資料庫在同一顆磁碟（這顆磁碟故障時兩者會一起遺失；可在「主機維護」設定外接備份磁碟）"

    containers = collect_containers(ctx.get("project", "istudy"))
    if containers is None:
        notes.append("docker 無法查詢：容器、映像版本與掛載來源未盤點")
    cont_rows = []
    for c in containers or []:
        cont_rows.append(dict(name=c["service"] or c["name"], container=c["name"], image=c["image"], state=c["state"],
                              status=PASS if c["state"] == "running" else FAIL,
                              detail="" if c["state"] == "running" else f"狀態 {c['state']}"))
    if containers == []:
        cont_rows.append(dict(name="（沒有容器）", container="", image="", state="", status=FAIL, detail="找不到 istudy 專案的容器"))
    mount_rows = []
    for c in containers or []:
        for m in c["mounts"]:
            if m["type"] != "bind" or not m["source"]:
                continue
            loc = _locate(m["source"], mounts, idx, zpools, sys_disks)
            mount_rows.append(dict(container=c["service"] or c["name"], source=m["source"], target=m["target"], rw=m["rw"],
                                   storage=loc["storage"], storage_label=loc["storage_label"], mount=loc["mount"]))

    health_code, health = http("http://127.0.0.1:4081/api/health")
    services = collect_services(pg, pg_major)
    ports = collect_ports(int(pg_c["port"]) if pg_c and pg_c.get("port") else 5432, http)

    others = other_postgres_containers()
    online = bool(pg_c) and str(pg_c.get("status", "")).startswith("online")
    if others:
        db = dict(status=WARN, detail="有 PostgreSQL 容器在執行（" + "；".join(o.replace("\t", " ") for o in others[:3]) +
                  "）：iStudy 使用主機的 PostgreSQL，請確認沒有兩份資料庫")
    elif online:
        db = dict(status=PASS, detail=f"主機 PostgreSQL {pg_c['version']}/{pg_c['cluster']}（{pg_c.get('data_directory')}）"
                  + ("；沒有 PostgreSQL 容器" if others is not None else "；docker 無法查詢，未確認容器"))
    elif pg_c:
        db = dict(status=FAIL, detail=f"主機 PostgreSQL {pg_major} 沒有執行（{pg_c.get('status')}）")
    else:
        db = dict(status=UNKNOWN if not pg else FAIL, detail="讀不到主機 PostgreSQL 叢集" if not pg else f"沒有 PostgreSQL {pg_major} 叢集")
    db["name"] = "資料庫架構（主機 PostgreSQL、不在 Docker 內）"

    versions = collect_versions(ctx["version_file"], health if health_code == 200 else None, containers)
    zfs = build_zfs(zpools, zlist, idx, media_pool, zavailable)
    disk_map = build_disk_map(flat, idx, paths, zpools, sys_disks)

    hostcheck = None
    try:
        with open(ctx["hostcheck_file"], encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict) and h.get("time"):
            hostcheck = {"time": h.get("time"), "checks": h.get("checks") or [], "system": h.get("system") or {}}
    except (OSError, ValueError):
        pass

    host = {"hostname": platform.node(), "kernel": platform.release(), "os": _os_name(), "board": read_board(ctx.get("dmi_dir", DMI_DIR))}
    rep = {
        "schema": SCHEMA, "based_on": "UEM v0.8.1（磁碟歸屬鏈）",
        "host": host,
        "paths": paths, "disk_map": disk_map, "zfs": zfs, "services": services, "containers": cont_rows,
        "ports": ports, "db": db, "versions": versions, "mounts": mount_rows, "hostcheck": hostcheck, "notes": notes,
    }
    return rep


# dev.61：主機板與 BIOS（/sys/class/dmi/id 裡一般使用者可讀的檔案；不讀序號、UUID 等識別資訊）
DMI_DIR = "/sys/class/dmi/id"
BOARD_FIELDS = (("board_vendor", "主機板製造商"), ("board_name", "主機板型號"), ("board_version", "主機板版本"),
                ("bios_vendor", "BIOS 製造商"), ("bios_version", "BIOS 版本"))


def read_board(dmi_dir: str = DMI_DIR) -> dict:
    """{欄位: 值}；讀不到或空白是 None（畫面顯示「未知」，不猜測）。"""
    out = {}
    for key, _label in BOARD_FIELDS:
        try:
            with open(os.path.join(dmi_dir, key), encoding="utf-8", errors="replace") as f:
                v = f.read(256).strip()
            out[key] = v or None
        except OSError:
            out[key] = None
    return out


def _os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("PRETTY_NAME="):
                    return ln.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _items(rep: dict) -> list[tuple[str, dict]]:
    out = [("重要路徑", p) for p in rep.get("paths", [])]
    out += [("ZFS", p) for p in (rep.get("zfs") or {}).get("pools", [])]
    out += [("服務", s) for s in rep.get("services", [])]
    out += [("容器", c) for c in rep.get("containers", [])]
    out += [("連接埠", p) for p in rep.get("ports", [])]
    if rep.get("db"):
        out.append(("資料庫", rep["db"]))
    if rep.get("versions"):
        out.append(("版本", {"name": "版本對照", **rep["versions"]}))
    return out


def summarize(rep: dict) -> dict:
    """統計各狀態數量、列出需要處理的項目（異常在前）；自我檢查（doctor）另計。"""
    counts = {k: 0 for k in (PASS, WARN, FAIL, MISSING, UNKNOWN, INFO)}
    issues = []
    for sec, it in _items(rep):
        st = it.get("status", UNKNOWN)
        counts[st] = counts.get(st, 0) + 1
        if st in (FAIL, WARN, MISSING, UNKNOWN):
            issues.append({"section": sec, "name": it.get("name", ""), "path": it.get("path", ""), "status": st, "detail": it.get("detail", "")})
    doc = rep.get("doctor") or {}
    if doc and not doc.get("carried"):
        for c in doc.get("items", []):
            st = DOCTOR_STATUS.get(c.get("status"), UNKNOWN)
            if st in (FAIL, WARN):
                issues.append({"section": "自我檢查", "name": c.get("name", ""), "path": "", "status": st, "detail": c.get("message", "")})
    issues.sort(key=lambda x: (_ORDER.get(x["status"], 9), x["section"]))
    doc_fail = doc and not doc.get("carried") and any(c.get("status") == "fail" for c in doc.get("items", []))
    doc_warn = doc and not doc.get("carried") and any(c.get("status") == "warn" for c in doc.get("items", []))
    overall = FAIL if counts[FAIL] or doc_fail else (WARN if counts[WARN] or counts[MISSING] or doc_warn else PASS)
    return {"counts": counts, "issues": issues, "overall": overall}


DOCTOR_STATUS = {"pass": PASS, "warn": WARN, "fail": FAIL, "skip": "SKIP"}

# ------------------------------------------------------------- 機密防護 ----

_SECRET_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)")


def scrub(obj):
    """報告寫出前的最後防線：任何「password=…」「token: …」形式的值一律遮蔽。"""
    if isinstance(obj, str):
        return _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***", obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- 輸出 ----

def fmt_time(v) -> str:
    """2026-10-20T05:40:34+08:00 → 2026-10-20 05:40:34（報告上的時間一律是 Server 的本地時間）。"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(v or ""))
    return f"{m.group(1)} {m.group(2)}" if m else str(v or "—")


def _st(s: str) -> str:
    s = DOCTOR_STATUS.get(s, s)
    return f"{STATUS_MARK.get(s, '·')} {STATUS_TEXT.get(s, s)}"


def _disks_txt(disks: list[dict]) -> str:
    return " + ".join(f"{d['name']}（{d['media']}{'，' + d['model'] if d.get('model') else ''}）" for d in disks) or "—"


def _md_cell(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def _md_table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
    return out + [""]


def sections(rep: dict) -> list[tuple[str, list[str], list[list], str]]:
    """Markdown 與 HTML 共用的表格內容：(標題, 欄位, 列, 說明)。"""
    out = []
    out.append(("需要處理", ["狀態", "類別", "項目", "說明"],
                [[_st(i["status"]), i["section"], i["name"] + (f"（{i['path']}）" if i.get("path") else ""), i["detail"]]
                 for i in rep.get("issues", [])], "沒有需要處理的項目" if not rep.get("issues") else ""))
    board = (rep.get("host") or {}).get("board") or {}
    out.append(("主機板", ["項目", "內容"], [[label, board.get(k) or "未知"] for k, label in BOARD_FIELDS],
                "讀取 /sys/class/dmi/id（不需要 root，不讀序號）；讀不到就是「未知」。只供參考，不列入盤點統計。"))
    out.append(("重要路徑與磁碟歸屬", ["名稱", "完整路徑", "掛載點", "來源", "磁碟類別", "用途", "狀態", "說明"],
                [[p["name"], p["path"], p.get("mount") or "—", p.get("source") or "—", p.get("storage_label") or "—",
                  p.get("purpose", ""), _st(p["status"]), p.get("detail") or p.get("chain", "")] for p in rep.get("paths", [])],
                "磁碟類別由 findmnt → lsblk（ROTA）→ zpool 成員實際查出；讀不到就是「未知」，不依路徑名稱推測。"))
    out.append(("磁碟對照", ["磁碟", "類型", "型號", "容量", "用途", "分割區", "iStudy 目錄"],
                [[d["name"], d["media"], d.get("model") or "—", f"{d['size_gb']} GB" if d.get("size_gb") else "—", d["role"],
                  "、".join(f"{k['name']}（{k['fstype'] or '—'}{'，' + '、'.join(k['mountpoints']) if k['mountpoints'] else ''}）" for k in d["partitions"]) or "—",
                  "、".join(x["path"] for x in d["istudy_dirs"]) or "—"] for d in rep.get("disk_map", [])], ""))
    z = rep.get("zfs") or {}
    out.append(("ZFS", ["pool", "結構", "成員", "容量／已用", "狀態", "說明"],
                [[p["name"] + ("（教材）" if p.get("media_pool") else ""), p["layout_text"],
                  "、".join(f"{m['disk'] or os.path.basename(m['path'])}（{m['media']}，{m['state']}）" for m in p["members"]) or "—",
                  (f"{round(p['size'] / 1e9)} GB／{p['used_pct']}%" if p.get("size") else "—"), _st(p["status"]), p.get("detail", "")]
                 for p in z.get("pools", [])], "" if z.get("pools") else ("沒有 ZFS pool" if z.get("available") else "沒有 ZFS 工具或無法讀取")))
    out.append(("服務", ["服務", "單元", "狀態", "說明"], [[s["name"], s["unit"], _st(s["status"]), s["detail"]] for s in rep.get("services", [])], ""))
    out.append(("容器", ["服務", "映像", "狀態", "說明"], [[c["name"], c["image"], _st(c["status"]), c.get("detail") or c.get("state", "")]
                                                        for c in rep.get("containers", [])], "" if rep.get("containers") else "docker 無法查詢"))
    out.append(("連接埠", ["連接埠", "監聽位址", "狀態", "說明"], [[p["name"], "、".join(p["addrs"]) or "—", _st(p["status"]), p["detail"]]
                                                           for p in rep.get("ports", [])], ""))
    db = rep.get("db") or {}
    out.append(("資料庫架構", ["項目", "狀態", "說明"], [[db.get("name", ""), _st(db.get("status", UNKNOWN)), db.get("detail", "")]] if db else [], ""))
    v = rep.get("versions") or {}
    out.append(("版本對照", ["來源", "版本", "讀取位置"], [[r["name"], r["value"] or "（讀不到）", r.get("note") or r["source"]] for r in v.get("rows", [])],
                (_st(v.get("status", UNKNOWN)) + "　" + v.get("detail", "")) if v else ""))
    out.append(("容器掛載來源", ["容器", "主機路徑", "容器內路徑", "讀寫", "磁碟類別"],
                [[m["container"], m["source"], m["target"], "讀寫" if m["rw"] else "唯讀", m["storage_label"]] for m in rep.get("mounts", [])],
                "" if rep.get("mounts") else "docker 無法查詢或沒有綁定掛載"))
    doc = rep.get("doctor")
    if doc:
        out.append(("自我檢查（doctor）", ["項目", "狀態", "說明"], [[c.get("name", ""), _st(c.get("status", "")), c.get("message") or ""] for c in doc.get("items", [])],
                    f"檢查時間 {fmt_time(doc.get('time'))}" + ("（沿用上次完整盤點的結果）" if doc.get("carried") else "")))
    hc = rep.get("hostcheck")
    if hc:
        out.append(("主機檢查（每 10 分鐘）", ["項目", "狀態", "說明"], [[c.get("name", ""), _st(c.get("status", "")), c.get("message") or ""] for c in hc.get("checks", [])],
                    f"最後更新 {fmt_time(hc.get('time'))}"))
    return out


def render_md(rep: dict) -> str:
    c = rep.get("counts", {})
    lines = [f"# iStudy 主機盤點（{'完整' if rep.get('mode') == 'full' else '快速'}）", "",
             f"- 時間：{fmt_time(rep.get('time'))}　主機：{(rep.get('host') or {}).get('hostname') or '—'}　iStudy：{rep.get('istudy_version') or '—'}",
             f"- 整體：{_st(rep.get('overall', UNKNOWN))}　（通過 {c.get(PASS, 0)}、注意 {c.get(WARN, 0)}、異常 {c.get(FAIL, 0)}、"
             f"不存在 {c.get(MISSING, 0)}、未知 {c.get(UNKNOWN, 0)}）",
             "- 只讀盤點：不修改設定、不讀 .env 內容、不刪除任何檔案。", ""]
    for n in rep.get("notes", []):
        lines.append(f"> {n}")
    if rep.get("notes"):
        lines.append("")
    for title, head, rows, note in sections(rep):
        lines += [f"## {title}", ""]
        if note:
            lines += [note, ""]
        if rows:
            lines += _md_table(head, rows)
    return "\n".join(lines).rstrip() + "\n"


_CSS = """
:root{--bg:#f6f7f9;--fg:#1d232b;--dim:#5b6573;--line:#d5dae1;--card:#fff;--fail:#b3261e;--warn:#8a5a00;--pass:#1e6b35}
@media (prefers-color-scheme:dark){:root{--bg:#15181d;--fg:#e4e8ee;--dim:#9aa4b1;--line:#333a44;--card:#1d2128;--fail:#ff8a80;--warn:#ffcc66;--pass:#7fd49a}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 "Noto Sans CJK TC","Noto Sans TC",system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:24px 0 8px}
.meta{color:var(--dim)}.note{color:var(--dim);margin:4px 0 8px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-weight:600;white-space:nowrap}td{word-break:break-word}td.nw{white-space:nowrap;word-break:normal}
.s-FAIL{color:var(--fail);font-weight:600}.s-WARN,.s-MISSING{color:var(--warn);font-weight:600}.s-PASS{color:var(--pass)}
"""


_NOWRAP = {"名稱", "狀態", "掛載點", "來源", "磁碟", "磁碟類別", "類型", "容量", "讀寫", "版本", "類別", "結構", "容量／已用"}


def render_html(rep: dict) -> str:
    """單一檔案、可離線開啟（沒有外部資源、沒有指令碼）；狀態一律以符號＋文字表示，不只靠顏色。"""
    e = html.escape
    c = rep.get("counts", {})
    title = f"iStudy 主機盤點（{'完整' if rep.get('mode') == 'full' else '快速'}）"
    parts = [f"<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             f"<title>{e(title)}</title><style>{_CSS}</style></head><body><main>",
             f"<h1>{e(title)}</h1>",
             f"<p class=\"meta\">時間 {e(fmt_time(rep.get('time')))}　主機 {e(str((rep.get('host') or {}).get('hostname') or '—'))}"
             f"　iStudy {e(str(rep.get('istudy_version') or '—'))}</p>",
             f"<p><b class=\"s-{e(rep.get('overall', UNKNOWN))}\">整體：{e(_st(rep.get('overall', UNKNOWN)))}</b>　通過 {c.get(PASS, 0)}、注意 {c.get(WARN, 0)}、"
             f"異常 {c.get(FAIL, 0)}、不存在 {c.get(MISSING, 0)}、未知 {c.get(UNKNOWN, 0)}</p>",
             "<p class=\"note\">只讀盤點：不修改設定、不讀 .env 內容、不刪除任何檔案。</p>"]
    for n in rep.get("notes", []):
        parts.append(f"<p class=\"note\">⚠ {e(n)}</p>")
    for title_, head, rows, note in sections(rep):
        parts.append(f"<h2>{e(title_)}</h2>")
        if note:
            parts.append(f"<p class=\"note\">{e(note)}</p>")
        if rows:
            parts.append("<div class=\"wrap\"><table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>")
            for r in rows:
                cells = []
                for h, v in zip(head, r):
                    s = str("" if v is None else v)
                    classes = ["nw"] if h in _NOWRAP else []
                    if h == "狀態":
                        key = next((k for k, t in STATUS_TEXT.items() if s.endswith(t)), "")
                        if key:
                            classes.append(f"s-{key}")
                    cls = f" class=\"{' '.join(classes)}\"" if classes else ""
                    cells.append(f"<td{cls}>{e(s)}</td>")
                parts.append("<tr>" + "".join(cells) + "</tr>")
            parts.append("</tbody></table></div>")
    parts.append("</main></body></html>")
    return "".join(parts)
