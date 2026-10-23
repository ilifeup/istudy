"""istudy_common.mediaacl：以非 root 身分（nobody）實際測試權限偵測。由 Linux 判斷權限，不靠解析 ACL 文字。

修復（setfacl）需要 acl 工具與支援 POSIX ACL 的檔案系統，只能在 Server 上驗證。
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
sys.path.insert(0, AGENT)
from istudy_common import mediaacl  # noqa: E402


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("setpriv"), "需要 root 與 setpriv（切換成 nobody 測試）")
class MediaAclTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="acl-")
        os.chmod(self.d, 0o755)
        m = self.media = os.path.join(self.d, "Media")
        for sub in ("playback", "課程A/第一堂", "課程B", "課程C", "Outgo/錄音"):
            os.makedirs(os.path.join(m, sub))
        def w(rel, mode):
            p = os.path.join(m, rel)
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, mode)
        w("課程A/第一堂/影片01.mp4", 0o644)
        w("課程A/第一堂/講義.pdf", 0o600)          # 複製時保留了 600：Agent 讀不到
        w("playback/1.mp4", 0o600)                  # 系統資料夾：不檢查
        w("V06.mp4", 0o644)                         # 根目錄的教材
        for rel, mode in (("", 0o755), ("課程A", 0o777), ("課程A/第一堂", 0o777), ("課程B", 0o755), ("課程C", 0o1777),
                          ("Outgo", 0o777), ("Outgo/錄音", 0o777)):
            os.chmod(os.path.join(m, rel) if rel else m, mode)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def as_nobody(self) -> dict:
        code = f"import sys,json;sys.path.insert(0,{AGENT!r});from istudy_common.mediaacl import scan_access;print(json.dumps(scan_access({self.media!r})))"
        p = subprocess.run(["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", sys.executable, "-I", "-c", code],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_detects_problems_without_touching_system_dirs(self):
        rep = self.as_nobody()
        self.assertEqual(rep["uid"], 65534)
        ex = rep["examples"]
        self.assertEqual(ex["file_no_read"], ["課程A/第一堂/講義.pdf"])
        self.assertIn("課程B", ex["dir_no_write"])
        self.assertEqual(ex["sticky"], ["課程C"])
        self.assertFalse(rep["root_write"], "根目錄有教材檔案（V06.mp4）時，根目錄要可寫才能刪除原檔")
        self.assertFalse(rep["ok"])
        self.assertNotIn("playback", json.dumps(rep, ensure_ascii=False), "系統資料夾不檢查")
        text = "\n".join(mediaacl.summary_lines(rep))
        self.assertIn("檔案讀不到", text)
        self.assertIn("sticky", text)

    def test_outgo_must_exist_and_be_writable(self):
        """dev.61：Outgo 是系統資料夾（不當教材檢查），但 Agent 要能在每一層寫逐字稿。"""
        os.chmod(os.path.join(self.media, "課程A/第一堂/講義.pdf"), 0o644)
        os.chmod(os.path.join(self.media, "課程B"), 0o777)
        os.chmod(os.path.join(self.media, "課程C"), 0o777)
        os.remove(os.path.join(self.media, "V06.mp4"))
        os.chmod(os.path.join(self.media, "Outgo/錄音"), 0o755)
        rep = self.as_nobody()
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["outgo"]["no_write"], ["Outgo/錄音"])
        self.assertIn("Outgo 有 1 個資料夾 Agent 不能寫入逐字稿", "\n".join(mediaacl.summary_lines(rep)))
        self.assertNotIn("Outgo", json.dumps(rep["examples"], ensure_ascii=False), "Outgo 不當教材檢查")
        shutil.rmtree(os.path.join(self.media, "Outgo"))
        rep = self.as_nobody()
        self.assertFalse(rep["ok"])
        self.assertIn("找不到", "\n".join(mediaacl.summary_lines(rep)))

    def test_ok_when_everything_accessible(self):
        os.chmod(os.path.join(self.media, "課程A/第一堂/講義.pdf"), 0o644)
        os.chmod(os.path.join(self.media, "課程B"), 0o777)
        os.chmod(os.path.join(self.media, "課程C"), 0o777)
        os.remove(os.path.join(self.media, "V06.mp4"))     # 根目錄沒有教材檔案時，根目錄不需要可寫
        rep = self.as_nobody()
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(mediaacl.library_entries(self.media), ["課程A", "課程B", "課程C"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
