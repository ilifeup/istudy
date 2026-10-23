"""dev.59 QSV 自我檢查：2 秒 1280×720 編碼＋ffprobe 驗證的判斷邏輯。

開發環境沒有 Intel GPU：用 libx264 代替 h264_qsv 產生真的檔案，只驗證「ffprobe 檢查」與錯誤訊息；
h264_qsv 本身只能在 Server 的 Agent 容器裡實測。
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from worker import media_tools as mt  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg／ffprobe")
class QsvCheckTest(unittest.TestCase):
    def setUp(self):
        self.real = mt._run

    def tearDown(self):
        mt._run = self.real

    def swap(self, **extra):
        def fake(cmd, timeout=None):
            cmd = ["libx264" if c == "h264_qsv" else c for c in cmd]
            if "-global_quality" in cmd:
                i = cmd.index("-global_quality")
                cmd[i:i + 2] = ["-crf", "30", "-preset", "ultrafast"]
            if extra.get("fps") and "-i" in cmd:
                i = cmd.index("-i") + 1
                cmd[i] = cmd[i].replace("rate=30", f"rate={extra['fps']}")
            return self.real(cmd, timeout)
        mt._run = fake

    def test_valid_output_passes_with_frame_count(self):
        self.swap()
        r = mt._qsv_file_test()
        self.assertTrue(r["ok"], r)
        self.assertIn("60 幀", r["message"])
        self.assertIn("1280×720", r["message"])

    def test_wrong_output_is_not_reported_as_pass(self):
        self.swap(fps=25)
        r = mt._qsv_file_test()
        self.assertFalse(r["ok"])
        self.assertIn("50 幀", r["message"])

    def test_encode_failure_shows_ffmpeg_reason(self):
        def fail(cmd, timeout=None):
            return self.real(["sh", "-c", "echo 'Error creating a MFX session: -9.' >&2; echo 'Conversion failed!' >&2; exit 1"], timeout)
        mt._run = fail
        r = mt._qsv_file_test()
        self.assertFalse(r["ok"])
        self.assertIn("Conversion failed", r["message"])
        self.assertIn("MFX session: -9", r["stderr"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
