"""Email 報告（dev.57）：用本機假的 SMTP 伺服器測試真正的寄送流程（smtplib、AUTH、附件、錯誤訊息）。

不連網際網路；Gmail 等真實郵件伺服器要在 Server 上實際寄一封才算驗收。
"""
from __future__ import annotations

import base64
import os
import socket
import socketserver
import sys
import threading
import unittest
from email import message_from_bytes, policy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from worker import mailer  # noqa: E402
from worker.context import JobError  # noqa: E402

RECEIVED: list[dict] = []


class FakeSMTP(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.reply("220 fake ESMTP")
        mail = {"rcpt": [], "auth": None}
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            up = line.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                self.wfile.write(b"250-fake\r\n250-AUTH PLAIN\r\n250 OK\r\n")
            elif up.startswith("AUTH PLAIN"):
                parts = line.split()
                cred = base64.b64decode(parts[2]).split(b"\0") if len(parts) > 2 else []
                if cred[1:] == [b"sender@example.tw", b"right-pass"]:
                    mail["auth"] = cred[1].decode()
                    self.reply("235 ok")
                else:
                    self.reply("535 authentication failed")
            elif up.startswith("MAIL FROM"):
                self.reply("250 ok")
            elif up.startswith("RCPT TO"):
                mail["rcpt"].append(line.split(":", 1)[1].strip("<> "))
                self.reply("250 ok")
            elif up == "DATA":
                self.reply("354 go")
                data = b""
                while True:
                    ln = self.rfile.readline()
                    if ln in (b".\r\n", b".\n", b""):
                        break
                    data += ln[1:] if ln.startswith(b"..") else ln
                mail["data"] = data
                RECEIVED.append(dict(mail))
                self.reply("250 queued")
            elif up == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class MailerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def settings(self, **kw):
        st = {"host": "127.0.0.1", "port": self.port, "security": "none", "username": "sender@example.tw",
              "password": "right-pass", "from": "sender@example.tw", "to": ["eachcore.app@gmail.com"]}
        st.update(kw)
        return st

    def test_send_message_with_attachment(self):
        RECEIVED.clear()
        st = self.settings()
        msg = mailer.build(None, {"report": "message", "note": "教室電腦不能上網，請 Server 代寄", "requested_by": "superadmin",
                                  "uem_html": "<!doctype html><title>UEM</title>"}, st)
        mailer.send(msg, st)
        self.assertEqual(len(RECEIVED), 1)
        got = RECEIVED[0]
        self.assertEqual((got["auth"], got["rcpt"]), ("sender@example.tw", ["eachcore.app@gmail.com"]))
        m = message_from_bytes(got["data"], policy=policy.default)
        self.assertIn("[iStudy] 訊息", m["Subject"])
        self.assertIn("教室電腦不能上網，請 Server 代寄", m.get_body(("plain",)).get_content())
        att = [p for p in m.iter_attachments()]
        self.assertEqual(len(att), 1)
        self.assertEqual(att[0].get_content_charset(), "utf-8")
        self.assertTrue(att[0].get_filename().startswith("istudy-uem-"))
        self.assertNotIn(b"right-pass", got["data"], "密碼不會出現在信件內容")

    def test_wrong_password_plain_message(self):
        st = self.settings(password="wrong")
        with self.assertRaises(JobError) as e:
            mailer.send(mailer.build(None, {"report": "test"}, st), st)
        self.assertIn("帳號或密碼錯誤", str(e.exception))

    def test_unreachable_server(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        st = self.settings(port=port)
        with self.assertRaises(JobError) as e:
            mailer.send(mailer.build(None, {"report": "test"}, st), st, timeout=3)
        self.assertIn("連不到郵件伺服器", str(e.exception))

    def test_custom_subject_and_default_recipient(self):
        class C:
            def config(self, key, default=None):
                return {"email": {"method": "smtp", "host": "smtp.gmail.com", "from": "a@b.tw"}, "email_secret": {}}.get(key, default)
        st = mailer.load_settings(C())
        self.assertEqual((st["to"], st["port"], st["security"]), (["eachcore.app@gmail.com"], 587, "starttls"))
        msg = mailer.build(None, {"report": "message", "note": "x", "subject": "教室 3 號電腦"}, st)
        self.assertEqual(msg["Subject"], "教室 3 號電腦")

    def test_relay_via_website_php_mail(self):
        """網站代寄：實際啟動 deploy/email/istudy-mail-relay.php（php -S），mail() 改寫到檔案，檢查寄出的信。"""
        import shutil
        import subprocess
        import tempfile
        import time
        if not shutil.which("php"):
            self.skipTest("沒有 php")
        root = os.path.dirname(os.path.dirname(HERE))
        d = tempfile.mkdtemp(prefix="relay-")
        key = "relay-test-key-0123456789abcdef"
        with open(os.path.join(root, "deploy", "email", "istudy-mail-relay.php"), encoding="utf-8") as f:
            src = f.read()
        src = src.replace("CHANGE-ME-至少 24 個字的隨機金鑰", key)
        with open(os.path.join(d, "relay.php"), "w", encoding="utf-8") as f:
            f.write(src)
        eml = os.path.join(d, "out.eml")
        fake = os.path.join(d, "sendmail.sh")      # 假的 sendmail：記錄參數（-f）與信件
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {d}/args.txt\ncat >> {eml}\n')
        os.chmod(fake, 0o755)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        p = subprocess.Popen(["php", "-d", f"sendmail_path={fake}", "-S", f"127.0.0.1:{port}", "-t", d],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            st = {"method": "relay", "relay_url": f"http://127.0.0.1:{port}/relay.php", "relay_key": key,
                  "from": "istudy@localhost", "to": ["eachcore.app@gmail.com"]}
            uem = '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>iStudy 主機盤點（完整）</title></head>' \
                  '<body><h1>iStudy 主機盤點（完整）</h1><p>系統 SSD／ZFS mirror（2 顆）✓ 通過</p></body></html>'
            msg = mailer.build(None, {"report": "message", "note": "教室 3 號電腦不能上網，請 Server 代寄", "uem_html": uem}, st)
            r = mailer.relay_send(msg, st)
            self.assertEqual((r["to"], r["envelope"], r["relay"]), (["eachcore.app@gmail.com"], "-f no-reply@work.eachcore.com", "relay-v2"))
            with open(eml, "rb") as f:
                raw = f.read()
            m = message_from_bytes(raw, policy=policy.default)
            self.assertEqual(m["From"], "iStudy Server <no-reply@work.eachcore.com>")
            with open(os.path.join(d, "args.txt")) as f:
                self.assertIn("-fno-reply@work.eachcore.com", f.read(), "與 Life Dashboard 相同：mail() 帶 -f 信封寄件人")
            self.assertEqual(m.get_body(("plain",))["Content-Transfer-Encoding"], "8bit")
            self.assertIn("[iStudy] 訊息", m["Subject"])
            self.assertIn("教室 3 號電腦不能上網", m.get_body(("plain",)).get_content())
            atts = list(m.iter_attachments())
            self.assertEqual([a.get_filename()[:11] for a in atts], ["istudy-uem-"])
            # dev.57 實機：網站代寄的 HTML 附件中文變「�」。附件要逐字相同
            self.assertEqual(atts[0].get_payload(decode=True).decode("utf-8").rstrip("\n"), uem)
            self.assertNotIn("\ufffd", atts[0].get_payload(decode=True).decode("utf-8"))
            self.assertNotIn(key.encode(), raw)
            with self.assertRaises(JobError) as e:
                mailer.relay_send(msg, {**st, "relay_key": "wrong-key-0123456789abcdefgh"})
            self.assertIn("金鑰錯誤", str(e.exception))
            with self.assertRaises(JobError) as e:
                mailer.relay_send(msg, {**st, "to": ["someone@else.tw"]})
            self.assertIn("允許的清單", str(e.exception))
        finally:
            p.terminate()
            p.wait(5)
            shutil.rmtree(d, ignore_errors=True)

    def test_feedback_mail(self):
        """問題回報：主旨寫回報者，內文有回報者資料與問題概述，寄給設定好的收件人。"""
        RECEIVED.clear()
        st = self.settings()
        msg = mailer.build(None, {"report": "feedback", "note": "第三堂課影片播到一半沒有聲音\n換一台電腦也一樣",
                                  "requested_by": "wang01",
                                  "reporter": {"username": "wang01", "full_name": "王小明", "role": "user", "title": "助教"},
                                  "app": "Client 1.0.0-dev.57", "pc": "CLASS-PC-03", "page": "問題回報"}, st)
        self.assertEqual(msg["Subject"], "[iStudy] 問題回報：wang01（王小明）")
        mailer.send(msg, st)
        m = message_from_bytes(RECEIVED[0]["data"], policy=policy.default)
        body = m.get_body(("plain",)).get_content()
        for s in ("【回報者】", "wang01", "王小明", "助教", "Client 1.0.0-dev.57", "CLASS-PC-03", "【問題概述】", "換一台電腦也一樣"):
            self.assertIn(s, body)
        self.assertEqual(RECEIVED[0]["rcpt"], ["eachcore.app@gmail.com"])

    def test_test_mail_names_method_and_summary_job_names(self):
        """測試信寫出實際的寄送方式；摘要的失敗工作用中文名稱並附最近一次原因（dev.57 實機寫成 whisper×6）。"""
        relay = mailer.build(None, {"report": "test"}, {"method": "relay", "from": "a@b.tw", "to": ["x@y.tw"]})
        self.assertIn("「網站代寄」設定正確", relay.get_body(("plain",)).get_content())
        smtp = mailer.build(None, {"report": "test"}, {"method": "smtp", "from": "a@b.tw", "to": ["x@y.tw"]})
        self.assertIn("「SMTP」設定正確", smtp.get_body(("plain",)).get_content())
        self.assertNotIn("SMTP", relay.get_body(("plain",)).get_content())
        self.assertEqual(mailer.JOB_TYPE_TEXT["asr"], "字幕辨識")

    def test_not_configured(self):
        class C:
            def config(self, key, default=None):
                return default
        with self.assertRaises(JobError) as e:
            mailer.load_settings(C())
        self.assertIn("網站代寄網址與金鑰", str(e.exception))     # 預設寄送方式是網站代寄

        class R:
            def config(self, key, default=None):
                return {"email": {"relay_url": "https://w.tw/r.php"}, "email_secret": {"relay_key": "k" * 30}}.get(key, default)
        st = mailer.load_settings(R())
        self.assertEqual((st["method"], st["to"]), ("relay", ["eachcore.app@gmail.com"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
