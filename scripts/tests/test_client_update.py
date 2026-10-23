"""dev.61 Ubuntu Client 自動更新服務（apps/client/linux/istudy-client-update.py）的測試。

  python3 -m unittest scripts/tests/test_client_update.py -v

真的：HTTP 下載（本機 HTTP 伺服器模擬 iStudy Server 的 /updates/ubuntu/）、SHA256 與大小核對、dpkg-deb 讀套件資訊
（用 dpkg-deb --build 做出真的小 .deb）、dpkg --compare-versions、pgrep 的 App 判斷樣式。
假的：apt-get（只記錄要安裝哪個檔案並更新「已安裝版本」）、dpkg-query、pgrep 的結果（用檔案控制 App 是否在執行）。
不是在 Ubuntu Client 實機（systemd、root、真正安裝）上的測試：那部分 NOT_TESTED。
"""
from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "apps", "client", "linux", "istudy-client-update.py")


def make_deb(path: str, version: str, package: str = "istudy-client") -> None:
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "DEBIAN"))
        os.makedirs(os.path.join(d, "opt", "iStudy"))
        with open(os.path.join(d, "opt", "iStudy", "v.txt"), "w") as f:
            f.write(version)
        with open(os.path.join(d, "DEBIAN", "control"), "w") as f:
            f.write(f"Package: {package}\nVersion: {version.replace('-dev.', '~dev.')}\nArchitecture: amd64\n"
                    "Maintainer: test <t@localhost>\nDescription: test\n")
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", d, path], check=True, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Server:
    def __init__(self, root: str):
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=root, **k)  # noqa: E731
        http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ClientUpdate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="istudy-cu-")
        self.www = os.path.join(self.d, "www")
        self.pub = os.path.join(self.www, "updates", "ubuntu")
        os.makedirs(self.pub)
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.state = os.path.join(self.d, "state")
        self.cache = os.path.join(self.d, "cache")
        self.req = os.path.join(self.d, "run")
        os.makedirs(self.req)
        with open(os.path.join(self.d, "installed"), "w") as f:
            f.write("1.0.0~dev.61")
        with open(os.path.join(self.d, "running"), "w") as f:
            f.write("0")
        self.stub("dpkg-query", 'cat "$T/installed"')
        self.stub("apt-get", 'echo "$@" >> "$T/apt.log"; for a; do f="$a"; done; '
                             'dpkg-deb -f "$f" Version > "$T/installed"; [ ! -f "$T/apt-fail" ]')
        self.stub("pgrep", '[ "$(cat "$T/running")" = 1 ]')
        self.srv = Server(self.www)
        with open(os.path.join(self.d, "client.json"), "w") as f:
            json.dump({"server": self.srv.url}, f)
        self.env = {**os.environ, "T": self.d, "PATH": self.bin + os.pathsep + os.environ["PATH"],
                    "ISTUDY_UPDATE_CONFIG": os.path.join(self.d, "client.json"), "ISTUDY_UPDATE_STATE": self.state,
                    "ISTUDY_UPDATE_CACHE": self.cache, "ISTUDY_UPDATE_REQUESTS": self.req}

    def tearDown(self):
        self.srv.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def stub(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)

    def publish(self, version, package="istudy-client", sha=None, size=None):
        f = f"istudy-client-ubuntu_{version}_amd64.deb"
        make_deb(os.path.join(self.pub, f), version, package)
        with open(os.path.join(self.pub, f), "rb") as fh:
            data = fh.read()
        with open(os.path.join(self.pub, "latest.json"), "w") as fh:
            json.dump({"channel": "ubuntu", "version": version, "file": f, "size": size or len(data),
                       "sha256": sha or hashlib.sha256(data).hexdigest()}, fh)
        return f

    def run_upd(self, *args, wait=True):
        p = subprocess.Popen([sys.executable, "-I", SCRIPT, *args], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if not wait:
            return p
        out, _ = p.communicate(timeout=120)
        return p.returncode, out

    def status(self):
        with open(os.path.join(self.state, "status.json"), encoding="utf-8") as f:
            return json.load(f)

    def installed(self):
        with open(os.path.join(self.d, "installed")) as fh:
            return fh.read().strip()

    def apt_calls(self):
        try:
            with open(os.path.join(self.d, "apt.log")) as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    # ---------------------------------------------------------------
    def test_up_to_date_does_nothing(self):
        self.publish("1.0.0-dev.61")
        rc, out = self.run_upd()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.status()["state"], self.apt_calls()), ("up_to_date", []))
        self.assertFalse(os.path.exists(self.cache) and os.listdir(self.cache))
        self.assertEqual(oct(os.stat(os.path.join(self.state, "status.json")).st_mode & 0o777), "0o644", "App（一般使用者）要能讀")

    def test_newer_installs_when_app_not_running(self):
        f = self.publish("1.0.0-dev.62")
        rc, out = self.run_upd()
        self.assertEqual(rc, 0, out)
        st = self.status()
        self.assertEqual((st["state"], self.installed()), ("installed", "1.0.0~dev.62"))
        calls = self.apt_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("install -y --no-install-recommends", calls[0])
        self.assertTrue(calls[0].endswith(os.path.join(self.cache, f)), "安裝本機下載並核對過的檔案")
        self.assertEqual(os.listdir(self.cache), [], "安裝後刪除下載檔")
        rc, _ = self.run_upd()
        self.assertEqual((self.status()["state"], len(self.apt_calls())), ("up_to_date", 1), "再檢查一次：不重複安裝")

    def test_app_running_waits_then_boot_installs_offline(self):
        f = self.publish("1.0.0-dev.62")
        with open(os.path.join(self.d, "running"), "w") as fh:
            fh.write("1")
        rc, out = self.run_upd()
        st = self.status()
        self.assertEqual((rc, st["state"], st["available"], self.apt_calls()), (0, "waiting_close", "1.0.0-dev.62", []), out)
        self.assertTrue(os.path.isfile(os.path.join(self.cache, f)), "已下載並核對，等 App 關閉")
        self.srv.close()                                                   # 開機時不連網
        self.srv = Server(self.d)                                          # （tearDown 用）
        with open(os.path.join(self.d, "running"), "w") as fh:
            fh.write("0")
        rc, out = self.run_upd("--boot")
        self.assertEqual((rc, self.status()["state"], self.installed()), (0, "installed", "1.0.0~dev.62"), out)

    def test_boot_without_staged_update_is_quick_noop(self):
        rc, out = self.run_upd("--boot")
        self.assertEqual((rc, self.status()["state"], self.apt_calls()), (0, "up_to_date", []), out)

    def test_sha_mismatch_is_refused(self):
        self.publish("1.0.0-dev.62", sha="0" * 64)
        rc, out = self.run_upd()
        st = self.status()
        self.assertEqual((rc, st["state"], self.apt_calls(), self.installed()), (1, "failed", [], "1.0.0~dev.61"))
        self.assertIn("SHA256", st["error"])
        self.assertEqual(os.listdir(self.cache), [], "沒通過核對的檔案不留下")

    def test_wrong_package_or_downgrade_is_refused(self):
        self.publish("1.0.0-dev.62", package="something-else")
        rc, _ = self.run_upd()
        self.assertEqual((rc, self.status()["state"], self.apt_calls()), (1, "failed", []))
        self.assertIn("套件名稱", self.status()["error"])
        self.publish("1.0.0-dev.60")
        rc, _ = self.run_upd()
        self.assertEqual((rc, self.status()["state"], self.apt_calls()), (0, "up_to_date", []), "不降版")

    def test_bad_manifest_and_config(self):
        with open(os.path.join(self.pub, "latest.json"), "w") as fh:
            json.dump({"version": "1.0.0-dev.62", "file": "../../etc/passwd", "size": 10, "sha256": "0" * 64}, fh)
        rc, _ = self.run_upd()
        self.assertEqual((rc, self.status()["state"]), (1, "failed"))
        self.assertIn("檔名", self.status()["error"])
        with open(os.path.join(self.d, "client.json"), "w") as f:
            json.dump({"server": "https://evil.example/"}, f)
        rc, _ = self.run_upd()
        self.assertIn("server 格式錯誤", self.status()["error"])

    def test_install_failure_keeps_current_version(self):
        self.publish("1.0.0-dev.62")
        open(os.path.join(self.d, "apt-fail"), "w").close()
        self.stub("apt-get", 'echo "$@" >> "$T/apt.log"; echo "E: 相依套件不足" >&2; exit 100')
        rc, _ = self.run_upd()
        self.assertEqual((rc, self.status()["state"], self.installed()), (1, "failed", "1.0.0~dev.61"))
        self.assertIn("安裝失敗", self.status()["error"])

    def test_request_waits_for_app_to_exit_and_records_request_id(self):
        self.publish("1.0.0-dev.62")
        with open(os.path.join(self.d, "running"), "w") as fh:
            fh.write("1")
        with open(os.path.join(self.req, "request-1000-123"), "w") as fh:
            fh.write("1000-123\n")
        p = self.run_upd("--request", wait=False)
        time.sleep(3)
        self.assertIsNone(p.poll(), "App 還在執行：等待")
        with open(os.path.join(self.d, "running"), "w") as fh:
            fh.write("0")                                                  # App 關閉
        out, _ = p.communicate(timeout=60)
        st = self.status()
        self.assertEqual((p.returncode, st["state"], st["request_id"], self.installed()), (0, "installed", "1000-123", "1.0.0~dev.62"), out)
        self.assertEqual(os.listdir(self.req), [], "要求檔已處理並刪除（path 單元不會一直觸發）")

    def test_app_pattern_matches_app_but_not_updater(self):
        spec = importlib.util.spec_from_file_location("cu", SCRIPT)
        cu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cu)
        d = os.path.join(self.d, "opt", "iStudy")
        os.makedirs(d)
        app = os.path.join(d, "istudy-client")
        shutil.copy("/bin/sleep", app)
        upd = os.path.join(d, "istudy-client-update.py")
        shutil.copy("/bin/sleep", upd)
        real = shutil.which("pgrep", path="/usr/bin:/bin")
        q = subprocess.Popen([upd, "30"])
        try:
            time.sleep(0.3)
            self.assertNotEqual(subprocess.run([real, "-f", cu.APP_PATTERN]).returncode, 0, "更新程式本身不算 App")
            a = subprocess.Popen([app, "30"])
            try:
                time.sleep(0.3)
                self.assertEqual(subprocess.run([real, "-f", cu.APP_PATTERN]).returncode, 0, "App 執行中")
            finally:
                a.kill()
        finally:
            q.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)
