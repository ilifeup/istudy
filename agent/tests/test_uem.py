"""主機盤點（UEM，dev.56）單元測試：外部指令（lsblk、findmnt、zpool、ss、systemctl、docker、dpkg-query）全部以假資料取代。

磁碟歸屬的測試資料改寫自 UEM v0.8.1 的 test_uem.py（LSBLK_SSD_HDD、ZPOOL）。
真實 Ubuntu Server（ZFS mirror、Docker、PostgreSQL 18）上的結果需要在 Server 實機驗收。
"""
from __future__ import annotations

import builtins
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from istudy_common import uem  # noqa: E402

LSBLK = [
    {"name": "nvme0n1", "kname": "nvme0n1", "path": "/dev/nvme0n1", "size": 500e9, "model": "Samsung 980", "type": "disk", "rota": False, "tran": "nvme",
     "children": [{"name": "nvme0n1p1", "kname": "nvme0n1p1", "path": "/dev/nvme0n1p1", "size": 1e9, "type": "part", "fstype": "vfat", "mountpoints": ["/boot/efi"]},
                  {"name": "nvme0n1p2", "kname": "nvme0n1p2", "path": "/dev/nvme0n1p2", "size": 499e9, "type": "part", "fstype": "ext4", "mountpoints": ["/"]}]},
    {"name": "sda", "kname": "sda", "path": "/dev/sda", "size": 4e12, "model": "WD Red", "type": "disk", "rota": True, "tran": "sata",
     "children": [{"name": "sda1", "kname": "sda1", "path": "/dev/sda1", "size": 4e12, "type": "part", "fstype": "zfs_member", "mountpoints": []}]},
    {"name": "sdb", "kname": "sdb", "path": "/dev/sdb", "size": 4e12, "model": "WD Red", "type": "disk", "rota": True, "tran": "sata",
     "children": [{"name": "sdb1", "kname": "sdb1", "path": "/dev/sdb1", "size": 4e12, "type": "part", "fstype": "zfs_member", "mountpoints": []}]},
    {"name": "sdc", "kname": "sdc", "path": "/dev/sdc", "size": 2e12, "model": "Seagate", "type": "disk", "rota": True, "tran": "usb",
     "children": [{"name": "sdc1", "kname": "sdc1", "path": "/dev/sdc1", "size": 2e12, "type": "part", "fstype": "ext4", "mountpoints": ["/mnt/x"]}]},
    {"name": "zram0", "kname": "zram0", "path": "/dev/zram0", "size": 0, "type": "disk", "rota": False},
    {"name": "loop0", "kname": "loop0", "path": "/dev/loop0", "size": 1e8, "type": "loop", "rota": False},
]

ZPOOL = """  pool: Tank
 state: ONLINE
  scan: scrub repaired 0B in 01:02:03 with 0 errors on Sun Oct 12 00:24:02 2026
config:

\tNAME            STATE     READ WRITE CKSUM
\tTank            ONLINE       0     0     0
\t  mirror-0      ONLINE       0     0     0
\t    /dev/sda1   ONLINE       0     0     0
\t    /dev/sdb1   ONLINE       0     0     0

errors: No known data errors
"""
ZLIST = "Tank\t4000000000000\t1000000000000\t3000000000000\tONLINE\n"

ZPOOL_SINGLE = ZPOOL.replace("\t  mirror-0      ONLINE       0     0     0\n\t    /dev/sda1   ONLINE       0     0     0\n\t    /dev/sdb1   ONLINE       0     0     0\n",
                             "\t  /dev/sda1     ONLINE       0     0     0\n")
ZPOOL_DEGRADED = """  pool: Tank
 state: DEGRADED
status: One or more devices could not be used.
config:

\tNAME                      STATE     READ WRITE CKSUM
\tTank                      DEGRADED     0     0     0
\t  mirror-0                DEGRADED     0     0     0
\t    /dev/sda1             ONLINE       0     0     0
\t    1234567890123456789   UNAVAIL      0     0     0  was /dev/sdb1
\tcache
\t  /dev/nvme0n1p3          ONLINE       0     0     0
\tspares
\t  /dev/sdc1               AVAIL

errors: No known data errors
"""


class Fake:
    """假的外部指令：findmnt 以最長路徑前綴對應掛載。"""

    def __init__(self, tmp, lsblk=LSBLK, zstatus=ZPOOL, zlist=ZLIST, mounts=None, ss="", units=None, docker=True, dpkg="1.0.0~dev.56"):
        self.tmp, self.lsblk, self.zstatus, self.zlist, self.ss, self.dpkg, self.docker = tmp, lsblk, zstatus, zlist, ss, dpkg, docker
        self.mounts = mounts or {}
        self.units = units if units is not None else {"docker": ("active", "loaded"), "istudy": ("active", "loaded"),
                                                       "istudy-hostcheck.timer": ("active", "loaded"),
                                                       "ssh": ("inactive", "loaded"), "ssh.socket": ("active", "loaded")}
        self.calls = []

    def cmd(self, args, timeout=8):
        self.calls.append(args)
        a = args[0]
        if a == "lsblk":
            return json.dumps({"blockdevices": self.lsblk}) if self.lsblk is not None else None
        if a == "zpool":
            return self.zstatus if args[1] == "status" else self.zlist
        if a == "findmnt":
            p = args[2]
            best = max((m for m in self.mounts if p == m or p.startswith(m.rstrip("/") + "/")), key=len, default=None)
            if best is None:
                return None
            s, f, t = self.mounts[best]
            return f'SOURCE="{s}" FSTYPE="{f}" TARGET="{t}"'
        if a == "ss":
            return self.ss
        if a == "dpkg-query":
            return self.dpkg
        if a == "docker":
            if not self.docker:
                return None
            if args[1] == "info":
                return os.path.join(self.tmp, "docker")
            if args[1] == "ps" and "-q" in args:
                return "aaa bbb ccc"
            if args[1] == "ps":
                return "istudy-php-1\tistudy/php:1.0.0-dev.56\nistudy-nginx-1\tnginx:1.28.0-alpine\nistudy-agent-1\tistudy/agent:1.0.0-dev.56"
            if args[1] == "inspect":
                assert "Env" not in args[3], "不可讀取容器環境變數"
                media = os.path.join(self.tmp, "Media")
                return "\n".join([
                    "/istudy-agent-1\tagent\tistudy/agent:1.0.0-dev.56\trunning\t" + json.dumps([
                        {"Type": "bind", "Source": media, "Destination": "/Media", "RW": True},
                        {"Type": "bind", "Source": os.path.join(self.tmp, "opt/istudy/.env"), "Destination": "/host-config/.env", "RW": False}]),
                    "/istudy-nginx-1\tnginx\tnginx:1.28.0-alpine\trunning\t" + json.dumps([{"Type": "bind", "Source": media, "Destination": "/Media", "RW": False}]),
                    "/istudy-php-1\tphp\tistudy/php:1.0.0-dev.56\texited\tnull",
                ])
        return None

    def cmd_any(self, args, timeout=8):
        if args[0] != "systemctl":
            return None
        st = self.units.get(args[2], ("inactive", "not-found"))
        return st[0] if args[1] == "is-active" else st[1]


def http_ok(url):
    return 200, {"ok": True, "version": "1.0.0-dev.56"}


class UemBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uem-")
        j = os.path.join
        self.opt, self.varlib, self.srv, self.media = j(self.tmp, "opt/istudy"), j(self.tmp, "var/lib/istudy"), j(self.tmp, "srv/istudy"), j(self.tmp, "Media")
        for d in (self.opt, self.varlib, j(self.srv, "backup"), j(self.srv, "updates"), j(self.tmp, "pg"), j(self.tmp, "docker"),
                  *(j(self.media, s) for s in ("playback", "subtitles", "doc-cache"))):
            os.makedirs(d)
        with open(j(self.opt, "VERSION.json"), "w") as f:
            json.dump({"server": "1.0.0-dev.56"}, f)
        with open(j(self.opt, ".env"), "w") as f:
            f.write("DB_PASSWORD=TopSecret-xyz\n")
        os.chmod(j(self.opt, ".env"), 0o640)
        with open(j(self.varlib, "hostcheck.json"), "w") as f:
            json.dump({"time": "2026-10-20T10:00:00+08:00", "system": {"hostname": "srv"}, "checks": [{"id": "zfs", "name": "ZFS", "status": "pass"}]}, f)
        self.mounts = {"/": ("/dev/nvme0n1p2", "ext4", "/"), self.media: ("Tank", "zfs", self.media),
                       j(self.media, "playback"): ("Tank/playback", "zfs", j(self.media, "playback"))}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def collect(self, fake=None, http=http_ok, **kw):
        fake = fake or Fake(self.tmp, mounts=self.mounts, **kw)
        ctx = dict(opt=self.opt, varlib=self.varlib, srv=self.srv, media=self.media, pg_major="18",
                   pg_clusters=[{"version": "18", "cluster": "main", "port": 5432, "status": "online", "owner": "postgres",
                                 "data_directory": os.path.join(self.tmp, "pg")}],
                   hostcheck_file=os.path.join(self.varlib, "hostcheck.json"), version_file=os.path.join(self.opt, "VERSION.json"), http=http)
        with mock.patch.object(uem, "cmd", fake.cmd), mock.patch.object(uem, "cmd_any", fake.cmd_any):
            rep = uem.collect(ctx)
        rep.update(uem.summarize(rep))
        rep.update(mode="quick", time="2026-10-20T10:00:00+08:00")
        self.fake = fake
        return rep

    @staticmethod
    def by_key(rep):
        return {p["key"]: p for p in rep["paths"]}


class TestZpoolParse(unittest.TestCase):
    def test_mirror(self):
        p = uem.parse_zpool_status(ZPOOL)["Tank"]
        self.assertEqual(p["state"], "ONLINE")
        self.assertEqual(p["layout"], "mirror")
        self.assertEqual([d["path"] for d in p["devices"]], ["/dev/sda1", "/dev/sdb1"])

    def test_single_disk_is_not_mirror(self):
        p = uem.parse_zpool_status(ZPOOL_SINGLE)["Tank"]
        self.assertEqual(p["layout"], "single")
        self.assertEqual(len(p["devices"]), 1)

    def test_degraded_missing_member_and_cache_spares_excluded(self):
        p = uem.parse_zpool_status(ZPOOL_DEGRADED)["Tank"]
        self.assertEqual(p["state"], "DEGRADED")
        self.assertEqual(p["layout"], "mirror")
        self.assertEqual([d["state"] for d in p["devices"]], ["ONLINE", "UNAVAIL"])   # cache／spares 不算成員
        self.assertNotIn("/dev/sdc1", [d["path"] for d in p["devices"]])

    def test_raidz_and_empty(self):
        txt = ZPOOL.replace("mirror-0", "raidz2-0")
        self.assertEqual(uem.parse_zpool_status(txt)["Tank"]["layout"], "raidz")
        self.assertEqual(uem.parse_zpool_status(None), {})


class TestOwnership(UemBase):
    def test_normal_layout(self):
        rep = self.collect()
        p = self.by_key(rep)
        for k in ("root", "opt", "varlib", "pgdata", "docker", "updates"):
            self.assertEqual(p[k]["status"], uem.PASS, (k, p[k]))
            self.assertEqual(p[k]["storage_label"], "系統 SSD")
        for k in ("media", "playback", "subtitles", "doccache"):
            self.assertEqual(p[k]["status"], uem.PASS, (k, p[k]))
            self.assertEqual(p[k]["storage_label"], "ZFS mirror（2 顆）")
            self.assertEqual([d["name"] for d in p[k]["disks"]], ["sda", "sdb"])
        self.assertIn("Tank/playback", p["playback"]["chain"])
        self.assertEqual(p["old_videos"]["status"], uem.PASS)   # dev.59：搬移後 /Media/videos 不存在
        self.assertEqual(p["pgdata"]["path"], os.path.join(self.tmp, "pg"))     # 資料目錄來自 pg_lsclusters，不是猜的
        self.assertEqual(p["docker"]["path"], os.path.join(self.tmp, "docker"))
        self.assertEqual(p["backup"]["status"], uem.INFO)
        self.assertIn("同一顆磁碟", p["backup"]["detail"])
        self.assertEqual(p["old_backups"]["status"], uem.PASS)
        self.assertEqual(p["models"]["status"], uem.INFO)                        # 選用目錄不存在不是問題
        dm = {d["name"]: d for d in rep["disk_map"]}
        self.assertNotIn("zram0", dm)
        self.assertNotIn("loop0", dm)
        self.assertEqual(dm["nvme0n1"]["role"], "系統碟")
        self.assertIn("ZFS pool Tank（mirror）成員", dm["sda"]["role"])
        self.assertEqual(dm["sdc"]["role"], "未被 iStudy 使用")
        self.assertIn(self.media, [x["path"] for x in dm["sda"]["istudy_dirs"]])
        z = rep["zfs"]["pools"][0]
        self.assertEqual((z["name"], z["status"], z["media_pool"], z["used_pct"]), ("Tank", uem.PASS, True, 25.0))

    def test_zfs_not_mounted_media_on_system_disk_is_fail(self):
        self.mounts = {"/": ("/dev/nvme0n1p2", "ext4", "/")}
        p = self.by_key(self.collect())
        self.assertEqual(p["media"]["status"], uem.FAIL)
        self.assertIn("教材會寫進系統碟", p["media"]["detail"])
        self.assertEqual(p["playback"]["status"], uem.FAIL)

    def test_system_path_on_other_hdd_is_warn(self):
        self.mounts[os.path.join(self.tmp, "docker")] = ("/dev/sdc1", "ext4", os.path.join(self.tmp, "docker"))
        p = self.by_key(self.collect())
        self.assertEqual(p["docker"]["status"], uem.WARN)
        self.assertEqual(p["docker"]["storage_label"], "HDD（不是系統碟）")

    def test_unknown_when_lsblk_unavailable(self):
        rep = self.collect(fake=Fake(self.tmp, lsblk=None, mounts=self.mounts))
        p = self.by_key(rep)
        self.assertEqual(p["root"]["status"], uem.UNKNOWN)
        self.assertEqual(p["opt"]["status"], uem.UNKNOWN)            # 不可當成「通過」
        self.assertEqual(p["media"]["disks"], [])                   # 實體磁碟對應不到；mirror 結構以 zpool status 為準
        self.assertEqual(p["media"]["storage_label"], "ZFS mirror（2 顆）")
        self.assertEqual(rep["disk_map"], [])
        self.assertTrue(any("lsblk" in n for n in rep["notes"]))

    def test_no_guess_from_path_name(self):
        """/Media 沒有任何掛載資訊時是「未知」，不因為名稱叫 Media 就當成 ZFS。"""
        self.mounts = {}
        p = self.by_key(self.collect())
        self.assertEqual(p["media"]["status"], uem.UNKNOWN)
        self.assertEqual(p["media"]["storage_label"], "未知")

    def test_single_disk_pool_and_degraded(self):
        rep = self.collect(zstatus=ZPOOL_SINGLE)
        self.assertEqual(self.by_key(rep)["media"]["status"], uem.WARN)
        self.assertIn("不是 mirror", self.by_key(rep)["media"]["detail"])
        self.assertEqual(rep["zfs"]["pools"][0]["status"], uem.WARN)
        rep = self.collect(zstatus=ZPOOL_DEGRADED)
        z = rep["zfs"]["pools"][0]
        self.assertEqual(z["status"], uem.WARN)
        self.assertIn("UNAVAIL", z["detail"])
        m = self.by_key(rep)["media"]
        self.assertEqual((m["status"], m["detail"]), (uem.WARN, "pool 狀態 DEGRADED"))
        self.assertEqual(m["storage_label"], "ZFS mirror（2 顆）")
        self.assertEqual([d["name"] for d in m["disks"]], ["sda"])     # UNAVAIL 的成員對應不到實體磁碟

    def test_old_folder_reported_not_deleted(self):
        old = os.path.join(self.media, "Backups")
        os.makedirs(old)
        p = self.by_key(self.collect())
        self.assertEqual(p["old_backups"]["status"], uem.WARN)
        self.assertIn("不會刪除", p["old_backups"]["detail"])
        self.assertTrue(os.path.isdir(old))


class TestEnvAndSecrets(UemBase):
    def test_env_content_never_read(self):
        env = os.path.join(self.opt, ".env")
        real_open = builtins.open

        def guarded(f, *a, **k):
            if os.path.abspath(str(f)) == env:
                raise AssertionError(".env 不可被讀取")
            return real_open(f, *a, **k)
        with mock.patch.object(builtins, "open", guarded):
            rep = self.collect()
        self.assertEqual(self.by_key(rep)["env"]["status"], uem.PASS)
        text = json.dumps(rep, ensure_ascii=False) + uem.render_md(rep) + uem.render_html(rep)
        self.assertNotIn("TopSecret-xyz", text)

    def test_env_world_readable_is_warn(self):
        os.chmod(os.path.join(self.opt, ".env"), 0o644)
        p = self.by_key(self.collect())
        self.assertEqual(p["env"]["status"], uem.WARN)
        self.assertIn("640", p["env"]["detail"])

    def test_scrub_and_html_escape(self):
        rep = self.collect()
        rep["notes"].append("DB_PASSWORD=abc123 token: xyz <script>alert(1)</script>")
        rep = uem.scrub(rep)
        html_ = uem.render_html(rep)
        self.assertNotIn("abc123", html_)
        self.assertNotIn("xyz", html_)
        self.assertNotIn("<script>", html_)
        self.assertIn("&lt;script&gt;", html_)
        self.assertNotIn("http://", html_.replace('http-equiv', ''))    # 沒有外部資源
        self.assertNotIn("src=", html_)
        self.assertEqual(uem.scrub("token_version=3"), "token_version=3")

    def test_board_dev61(self):
        """dev.61 P2：主機板與 BIOS 讀 /sys/class/dmi/id（不需 root、不讀序號／UUID）；讀不到顯示「未知」；檢查項目數量不變。"""
        dmi = os.path.join(self.tmp, "dmi")
        os.makedirs(dmi)
        for k, v in (("board_vendor", "ASUSTeK COMPUTER INC.\n"), ("board_name", "PRIME B760M-A\n"), ("board_version", "Rev 1.xx\n"),
                     ("bios_vendor", "American Megatrends Inc.\n"), ("board_serial", "SECRET-SN-123\n"), ("product_uuid", "0000-SECRET\n")):
            with open(os.path.join(dmi, k), "w") as f:
                f.write(v)
        b = uem.read_board(dmi)
        self.assertEqual(b, {"board_vendor": "ASUSTeK COMPUTER INC.", "board_name": "PRIME B760M-A", "board_version": "Rev 1.xx",
                             "bios_vendor": "American Megatrends Inc.", "bios_version": None})
        self.assertEqual(set(uem.read_board(os.path.join(self.tmp, "沒有"))), set(b))
        self.assertTrue(all(v is None for v in uem.read_board(os.path.join(self.tmp, "沒有")).values()))
        base = self.collect()
        with mock.patch.object(uem, "DMI_DIR", dmi):
            rep = self.collect()
        rep2 = self.collect()
        self.assertEqual(base["counts"], rep2["counts"])
        for r in (rep, base):
            self.assertEqual(set(r["host"]["board"]), set(b))
        sec = {s[0]: s for s in uem.sections(dict(rep, host={**rep["host"], "board": b}))}
        self.assertIn("主機板", sec)
        rows = dict((x[0], x[1]) for x in sec["主機板"][2])
        self.assertEqual((rows["主機板型號"], rows["BIOS 版本"]), ("PRIME B760M-A", "未知"))
        r3 = dict(rep, host={**rep["host"], "board": b})
        md, html_ = uem.render_md(r3), uem.render_html(r3)
        for out in (md, html_):
            self.assertIn("PRIME B760M-A", out)
            self.assertNotIn("SECRET", out)
        self.assertEqual(json.loads(json.dumps(r3, ensure_ascii=False))["host"]["board"]["board_name"], "PRIME B760M-A")
        self.assertEqual(sum(uem.summarize(r3)["counts"].values()), sum(base["counts"].values()), "主機板只是資訊，不算檢查項目")


class TestServicesPortsVersions(UemBase):
    def test_services(self):
        units = {"docker": ("active", "loaded"), "istudy": ("failed", "loaded"), "istudy-hostcheck.timer": ("inactive", "not-found"),
                 "ssh": ("inactive", "loaded"), "ssh.socket": ("active", "loaded")}
        rep = self.collect(units=units)
        s = {x["unit"]: x for x in rep["services"]}
        self.assertEqual(s["postgresql@18-main"]["status"], uem.PASS)
        self.assertEqual(s["docker"]["status"], uem.PASS)
        self.assertEqual(s["istudy"]["status"], uem.FAIL)
        self.assertEqual(s["istudy-hostcheck.timer"]["status"], uem.WARN)
        self.assertEqual(s["ssh"]["status"], uem.PASS)
        self.assertIn("ssh.socket", s["ssh"]["detail"])
        c = {x["name"]: x for x in rep["containers"]}
        self.assertEqual(c["agent"]["status"], uem.PASS)
        self.assertEqual(c["php"]["status"], uem.FAIL)
        self.assertEqual(rep["db"]["status"], uem.PASS)
        m = [x for x in rep["mounts"] if x["target"] == "/Media"]
        self.assertEqual(len(m), 2)
        self.assertTrue(all(x["storage_label"] == "ZFS mirror（2 顆）" for x in m))

    def test_systemctl_unavailable_is_unknown(self):
        rep = self.collect(units={})
        s = {x["unit"]: x for x in rep["services"]}
        self.assertEqual(s["docker"]["status"], uem.FAIL)       # 單元不存在（not-found）
        f = Fake(self.tmp, mounts=self.mounts)
        f.cmd_any = lambda args, timeout=8: None
        s = {x["unit"]: x for x in self.collect(fake=f)["services"]}
        self.assertEqual(s["docker"]["status"], uem.UNKNOWN)

    def test_ports(self):
        ss = ("LISTEN 0 511 0.0.0.0:4080 0.0.0.0:*\nLISTEN 0 511 [::]:4080 [::]:*\nLISTEN 0 511 127.0.0.1:4081 0.0.0.0:*\n"
              "LISTEN 0 200 127.0.0.1:5432 0.0.0.0:*\nLISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n")
        p = {x["port"]: x for x in self.collect(ss=ss)["ports"]}
        self.assertEqual(p[4080]["status"], uem.PASS)
        self.assertEqual(p[4080]["addrs"], ["0.0.0.0", "::"])
        self.assertEqual(p[4081]["status"], uem.PASS)
        self.assertEqual(p[5432]["status"], uem.PASS)
        p = {x["port"]: x for x in self.collect(ss=ss.replace("127.0.0.1:4081", "0.0.0.0:4081").replace("127.0.0.1:5432", "0.0.0.0:5432"))["ports"]}
        self.assertEqual(p[4081]["status"], uem.FAIL)
        self.assertEqual(p[5432]["status"], uem.WARN)
        p = {x["port"]: x for x in self.collect(ss="", http=lambda u: (None, None))["ports"]}
        self.assertEqual(p[4080]["status"], uem.FAIL)
        self.assertEqual(p[5432]["status"], uem.INFO)            # iStudy 走 Unix socket，不開 TCP 是正常的
        p = {x["port"]: x for x in self.collect(ss=None, http=lambda u: (None, None))["ports"]}
        self.assertEqual(p[4081]["status"], uem.UNKNOWN)

    def test_versions(self):
        v = self.collect()["versions"]
        self.assertEqual(v["status"], uem.PASS, v)
        self.assertIn("Server App（.deb）", [r["name"] for r in v["rows"]])
        self.assertEqual({r["value"] for r in v["rows"]}, {"1.0.0-dev.56"})     # 1.0.0~dev.56 視為同一版
        v = self.collect(dpkg="1.0.0~dev.55")["versions"]
        self.assertEqual(v["status"], uem.WARN)
        self.assertIn("不一致", v["detail"])
        v = self.collect(http=lambda u: (None, None))["versions"]
        self.assertEqual(v["status"], uem.WARN)
        self.assertIn("執行中（API）", v["detail"])

    def test_summary_and_renderers(self):
        rep = self.collect(ss="LISTEN 0 1 0.0.0.0:4081 0.0.0.0:*\n")
        self.assertEqual(rep["overall"], uem.FAIL)
        self.assertEqual(rep["issues"][0]["status"], uem.FAIL)
        md = uem.render_md(rep)
        self.assertIn("## 重要路徑與磁碟歸屬", md)
        self.assertIn("✗ 異常", md)
        html_ = uem.render_html(rep)
        self.assertIn("<h2>ZFS</h2>", html_)
        self.assertIn("✓ 通過", html_)                           # 符號＋文字，不只靠顏色
        rep["doctor"] = {"time": "t", "carried": True, "items": [{"name": "Agent 心跳", "status": "fail", "message": "x"}]}
        s = uem.summarize(rep)
        self.assertFalse(any(i["section"] == "自我檢查" for i in s["issues"]))   # 沿用的舊結果不算這次的問題
        rep["doctor"]["carried"] = False
        self.assertTrue(any(i["section"] == "自我檢查" for i in uem.summarize(rep)["issues"]))


if __name__ == "__main__":
    unittest.main()
