"""istudyctl 測試：在暫存根目錄（ISTUDY_ROOT_PREFIX）與測試模式（不呼叫 Docker）下跑 bootstrap 的非 Docker 步驟。

完整的 Docker 步驟需要在 Ubuntu 26.04 實機或虛擬機驗收（V-18）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
ROOT = os.path.dirname(AGENT)


class IstudyctlSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="istudyctl-")
        cls.prefix = os.path.join(cls.tmp, "root")
        cls.media = os.path.join(cls.tmp, "Media")
        os.makedirs(cls.prefix)
        os.makedirs(cls.media)
        cls.dist = os.path.join(cls.tmp, "dist")
        subprocess.run([sys.executable, os.path.join(ROOT, "deploy", "build_bundle.py"), "--out", cls.dist, "--skip-images"],
                       check=True, capture_output=True)
        ver = json.load(open(os.path.join(ROOT, "VERSION.json")))["server"]
        cls.bundle = os.path.join(cls.dist, f"istudy-server-{ver}-offline")
        with open(os.path.join(cls.bundle, "clients", "win10", "latest.yml"), "w") as f:
            f.write("version: test\n")
        # 新增檔案後重算 SHA256SUMS
        subprocess.run(["sh", "-c", "cd '%s' && find . -type f ! -name SHA256SUMS | sed 's|^./||' | sort | xargs -d '\\n' sha256sum > SHA256SUMS" % cls.bundle],
                       check=True)
        cls.env = {**os.environ, "ISTUDY_ROOT_PREFIX": cls.prefix, "ISTUDY_SKIP_DOCKER": "1", "ISTUDY_MEDIA_ROOT": cls.media,
                   "ISTUDY_EXPECT_UBUNTU": __import__("platform").freedesktop_os_release().get("VERSION_ID", "24.04"),
                   "ISTUDY_REQUIRE_MOUNT": "0", "ISTUDY_MIN_SYSTEM_GB": "1", "ISTUDY_PREFLIGHT_PORTS": "4080,4081"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ctl(self, *args, input_text=None):
        return subprocess.run([sys.executable, os.path.join(AGENT, "istudyctl.py"), "--json", *args], env=self.env,
                              capture_output=True, text=True, input=input_text)

    def events(self, p):
        return [json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")]

    def test_1_bundle_tamper_detected(self):
        bad = os.path.join(self.tmp, "bad")
        shutil.copytree(self.bundle, bad)
        with open(os.path.join(bad, "VERSION.json"), "a") as f:
            f.write(" ")
        p = self.ctl("bootstrap", "--bundle", bad, "--accept-warnings")
        ev = [e for e in self.events(p) if e["event"] == "step" and e["step"] == 1]
        self.assertEqual(ev[-1]["status"], "fail")
        self.assertIn("內容不符", ev[-1]["message"])
        self.assertNotEqual(p.returncode, 0)

    def test_2_warnings_need_confirmation(self):
        p = self.ctl("bootstrap", "--bundle", self.bundle)
        self.assertEqual(p.returncode, 3, p.stdout)  # 有警告（IP、內顯、桌面…）需確認

    def test_3_bootstrap_steps_and_rerun(self):
        p = self.ctl("bootstrap", "--bundle", self.bundle, "--accept-warnings", "--super-user", "superadmin", "--password-stdin",
                     input_text="SuperSecret123\nSuperSecret123\n")
        steps = {e["step"]: e for e in self.events(p) if e["event"] == "step" and e["status"] != "running"}
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for n in range(1, 15):
            self.assertIn(steps[n]["status"], ("ok", "skip", "warn"), (n, steps[n]))
        marker = os.path.join(self.media, ".istudy-media")
        install_id = open(marker).read().strip()
        self.assertRegex(install_id, r"^[a-f0-9]{32}$")
        self.assertEqual(open(os.path.join(self.prefix, "etc/istudy/install_id")).read().strip(), install_id)
        env = open(os.path.join(self.prefix, "opt/istudy/.env")).read()
        self.assertRegex(env, r"DB_PASSWORD=\S{30,}")
        self.assertEqual(oct(os.stat(os.path.join(self.prefix, "opt/istudy/.env")).st_mode)[-3:], "640")
        self.assertNotIn(env.split("DB_PASSWORD=")[1].split()[0], p.stdout, "密碼不可出現在輸出")
        for sub in ("playback", "subtitles", "doc-cache"):
            self.assertTrue(os.path.isdir(os.path.join(self.media, sub)))
        self.assertFalse(os.path.exists(os.path.join(self.media, "videos")), "dev.59：教材直接放在 /Media，不再建立 videos")
        unit = open(os.path.join(self.prefix, "etc/systemd/system/istudy.service")).read()
        self.assertIn("RequiresMountsFor=", unit)
        self.assertIn("ExecStartPre=/usr/local/sbin/istudyctl check-media", unit)
        self.assertTrue(os.path.exists(os.path.join(self.prefix, "srv/istudy/updates/win10/latest.yml")))
        report = steps[14]["message"]
        self.assertTrue(os.path.exists(report))
        self.assertNotIn("SuperSecret123", open(report).read(), "報告不含密碼")
        # 重跑：.env 與媒體標記不重新產生、不刪資料
        os.makedirs(os.path.join(self.media, "課程A"))
        open(os.path.join(self.media, "課程A", "keep.txt"), "w").close()
        p2 = self.ctl("bootstrap", "--bundle", self.bundle, "--accept-warnings")
        self.assertEqual(p2.returncode, 0, p2.stdout)
        self.assertEqual(open(marker).read().strip(), install_id)
        self.assertEqual(open(os.path.join(self.prefix, "opt/istudy/.env")).read().split("DB_PASSWORD=")[1].split()[0],
                         env.split("DB_PASSWORD=")[1].split()[0])
        self.assertTrue(os.path.exists(os.path.join(self.media, "課程A", "keep.txt")))

    def test_4_check_media(self):
        p = self.ctl("check-media")
        self.assertEqual(p.returncode, 0, p.stderr)
        marker = os.path.join(self.media, ".istudy-media")
        good = open(marker).read()
        with open(marker, "w") as f:
            f.write("0" * 32)
        p = self.ctl("check-media")
        self.assertEqual(p.returncode, 2)
        self.assertIn("掛錯磁碟", p.stderr)
        with open(marker, "w") as f:
            f.write(good)

    def test_5_hostcheck_file(self):
        p = self.ctl("hostcheck")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.load(open(os.path.join(self.prefix, "var/lib/istudy/hostcheck.json")))
        self.assertIn("system", data)
        self.assertIn("checks", data)

    def test_6_unknown_command_rejected(self):
        p = subprocess.run([sys.executable, os.path.join(AGENT, "istudyctl.py"), "sh", "-c", "id"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("invalid choice", p.stderr)


    def test_7_prod_restore_requires_terminal_confirmation(self):
        """dev.55 BK-01：正式還原沒有終端機確認時一律拒絕；測試庫還原不需要確認。資料庫指令以假函式取代。"""
        sys.path.insert(0, AGENT)
        import importlib
        os.environ["ISTUDY_ROOT_PREFIX"] = self.prefix
        import istudyctl as C
        importlib.reload(C)
        calls = []

        class R:
            returncode, stdout, stderr = 0, "3", ""
        C.require_root = lambda: None
        C.drop_aux_db = lambda name: calls.append(("drop", name))
        C.create_db = lambda name: calls.append(("create", name))
        C.pg_admin = lambda sql=None, db="postgres", file=None, as_istudy=False, check=True, timeout=3600: (calls.append(("sql", db, sql)), R())[1]
        C.compose = lambda *a, **k: calls.append(("compose",) + a)
        dump = os.path.join(self.tmp, "x.sql")
        with open(dump, "w") as f:
            f.write("-- PostgreSQL database dump\n")
        self.assertIn("測試資料庫", C.restore(dump, "test"))
        self.assertFalse(any(c[0] == "sql" and c[1] == "istudy" or "RENAME" in str(c) for c in calls), "測試庫還原不碰正式資料庫")
        calls.clear()
        old = sys.stdin
        sys.stdin = open(os.devnull)
        try:
            with self.assertRaises(RuntimeError) as e:
                C.restore(dump, "prod")
        finally:
            sys.stdin.close()
            sys.stdin = old
        self.assertIn("必須在終端機", str(e.exception))
        self.assertFalse(any("RENAME" in str(c) for c in calls), "沒有確認就不改正式資料庫")
        self.assertFalse(any(c[0] == "compose" for c in calls), "沒有停止服務")

    def test_8_uem_reports(self):
        """dev.56 UEM：寫出 latest.json／md／html（0644）與 history（保留 10 份）；快速盤點沿用上次完整盤點的自我檢查；不洩漏 .env。"""
        opt = os.path.join(self.prefix, "opt/istudy")
        os.makedirs(opt, exist_ok=True)
        if not os.path.exists(os.path.join(opt, ".env")):
            with open(os.path.join(opt, ".env"), "w") as f:
                f.write("DB_PASSWORD=never-in-report-42\n")
        udir = os.path.join(self.prefix, "var/lib/istudy/uem")
        hist = os.path.join(udir, "history")
        os.makedirs(hist, exist_ok=True)
        for i in range(12):
            open(os.path.join(hist, f"uem-20200101-0000{i:02d}-quick.json"), "w").close()
        p = self.ctl("uem", "--mode", "full")
        self.assertEqual(p.returncode, 0, p.stderr)
        done = [e for e in self.events(p) if e["event"] == "done"][-1]
        self.assertIn("主機盤點完成", done["message"])
        self.assertIn(done["status"], ("pass", "warn", "fail"))
        with open(os.path.join(udir, "latest.json"), encoding="utf-8") as f:
            rep = json.load(f)
        self.assertEqual((rep["schema"], rep["mode"]), ("istudy-uem/1", "full"))
        self.assertFalse(rep["doctor"]["carried"])
        p = self.ctl("uem")                                      # 預設 quick
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(udir, "latest.json"), encoding="utf-8") as f:
            rep = json.load(f)
        self.assertEqual(rep["mode"], "quick")
        self.assertTrue(rep["doctor"]["carried"])
        for n in ("latest.json", "latest.md", "latest.html"):
            path = os.path.join(udir, n)
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o644", n)
            with open(path, encoding="utf-8") as f:
                self.assertNotIn("never-in-report-42", f.read())
        files = sorted(os.listdir(hist))
        self.assertEqual(len(files), 10)
        self.assertTrue(files[-1].endswith("-quick.json") and not files[-1].startswith("uem-2020"))
        p = subprocess.run([sys.executable, os.path.join(AGENT, "istudyctl.py"), "uem", "--mode", "all"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)

    def test_9_clients_publish_dev61(self):
        """dev.61：Super 一個指令發布新版 Client，各台自動更新。核對 latest.yml（SHA512）／latest.json（SHA256、大小），
        不通過的版別不發布；描述檔最後才換。"""
        import base64
        import hashlib
        src = os.path.join(self.tmp, "clients-src")
        ver = "1.0.0-dev.62"
        w10 = os.path.join(src, "win10")
        os.makedirs(w10)
        exe = f"iStudy-Client-Win10-{ver}.exe"
        data = os.urandom(4096)
        with open(os.path.join(w10, exe), "wb") as f:
            f.write(data)
        with open(os.path.join(w10, exe + ".blockmap"), "wb") as f:
            f.write(b"bm")
        sha512 = base64.b64encode(hashlib.sha512(data).digest()).decode()
        with open(os.path.join(w10, "latest.yml"), "w") as f:
            f.write(f"version: {ver}\nfiles:\n  - url: {exe}\n    sha512: {sha512}\n    size: 4096\npath: {exe}\nsha512: {sha512}\nreleaseDate: '2026-10-22T00:00:00.000Z'\n")
        ub = os.path.join(src, "ubuntu")
        os.makedirs(ub)
        deb = f"istudy-client-ubuntu_{ver}_amd64.deb"
        with open(os.path.join(ub, deb), "wb") as f:
            f.write(data)
        with open(os.path.join(ub, "latest.json"), "w") as f:
            json.dump({"channel": "ubuntu", "version": ver, "file": deb, "size": 4096, "sha256": hashlib.sha256(data).hexdigest()}, f)
        w7 = os.path.join(src, "win7")             # 壞的：SHA512 不符
        os.makedirs(w7)
        exe7 = f"iStudy-Client-Win7-{ver}.exe"
        with open(os.path.join(w7, exe7), "wb") as f:
            f.write(b"tampered")
        with open(os.path.join(w7, "latest.yml"), "w") as f:
            f.write(f"version: {ver}\npath: {exe7}\nsha512: {sha512}\n")
        p = self.ctl("clients-publish", "--from", src)
        ev = self.events(p)
        msgs = [e.get("message", "") for e in ev]
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(any("win10：已發布 1.0.0-dev.62" in m for m in msgs) and any("ubuntu：已發布" in m for m in msgs), msgs)
        self.assertTrue(any("win7：沒有發布" in m and "SHA512" in m for m in msgs), msgs)
        up = os.path.join(self.prefix, "srv/istudy/updates")
        self.assertEqual(sorted(os.listdir(os.path.join(up, "win10"))), sorted([exe, exe + ".blockmap", "latest.yml"]))
        self.assertEqual(sorted(os.listdir(os.path.join(up, "ubuntu"))), sorted([deb, "latest.json"]))
        self.assertFalse(os.path.exists(os.path.join(up, "win7", exe7)), "核對不過的不發布")
        self.assertEqual(oct(os.stat(os.path.join(up, "ubuntu", "latest.json")).st_mode & 0o777), "0o644")
        p = self.ctl("clients-publish", "--from", os.path.join(self.tmp, "沒有這個"))
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
