"""測試用的最小 FTPS 伺服器（明示 TLS：AUTH TLS、PBSZ、PROT P、PASV）。只供自動測試在本機 127.0.0.1 使用。

正式環境的 InterServer 連線無法在開發環境測試（沒有帳號與網路），由這個伺服器模擬：
  - 憑證由測試自己產生的 CA 簽發（用戶端以 cafile 驗證，與正式環境一樣會驗證憑證）
  - require_tls=True：AUTH TLS 之前送 USER/PASS 一律拒絕（並記錄「有沒有在明文收到密碼」）
  - no_tls=True：模擬不支援 FTPS 的伺服器（AUTH TLS 回 502）
  - commands：記錄收到的指令（PASS 只記錄「收到」，不記錄內容），測試用來確認「暫存名稱上傳 → 改名」
"""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import tempfile
import threading
from typing import Optional


def make_certs(directory: str) -> tuple[str, str, str]:
    """回傳 (ca.pem, server.pem, server.key)：自簽 CA＋localhost／127.0.0.1 的伺服器憑證。"""
    ca_key, ca = os.path.join(directory, "ca.key"), os.path.join(directory, "ca.pem")
    key, csr, crt = os.path.join(directory, "server.key"), os.path.join(directory, "server.csr"), os.path.join(directory, "server.pem")
    ext = os.path.join(directory, "ext.cnf")
    with open(ext, "w") as f:
        # Python 3.13 起預設嚴格檢查（VERIFY_X509_STRICT）：CA 要有 keyUsage、伺服器憑證要有 AKID
        f.write("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
    run = lambda *a: subprocess.run(["openssl", *a], check=True, capture_output=True)  # noqa: E731
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", ca_key, "-out", ca, "-days", "2", "-subj", "/CN=iStudy Test CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", csr, "-subj", "/CN=localhost")
    run("x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-CAcreateserial", "-out", crt, "-days", "2", "-extfile", ext)
    return ca, crt, key


class FtpsTestServer:
    def __init__(self, root: str, cert: str, key: str, user: str = "tester", password: str = "s3cret-ftp",
                 require_tls: bool = True, no_tls: bool = False):
        self.corrupt_retr = False      # True：下載時改掉一個位元組（測試「下載回來比對 SHA-256」）
        self.root = os.path.realpath(root)
        self.user, self.password = user, password
        self.require_tls, self.no_tls = require_tls, no_tls
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []
        self.plain_password_seen = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "FtpsTestServer":
        self.thread.start()
        return self

    def close(self) -> None:
        """停止接受連線（Linux 上只 close 不會喚醒另一個執行緒裡的 accept，要先 shutdown）。"""
        self.stop.set()
        for fn in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                fn()
            except OSError:
                pass
        self.thread.join(5)

    def _serve(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    # ---------- 一個連線 ----------
    def _session(self, conn: socket.socket) -> None:
        s = {"conn": conn, "tls": False, "user": None, "auth": False, "cwd": "/", "prot": False, "pasv": None, "rnfr": None}
        f = conn.makefile("rb")

        def reply(line: str) -> None:
            s["conn"].sendall((line + "\r\n").encode("utf-8"))

        reply("220 iStudy test FTPS")
        try:
            while True:
                raw = f.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper()
                self.commands.append("PASS ***" if cmd == "PASS" else line)
                if cmd == "AUTH":
                    if self.no_tls:
                        reply("502 AUTH not supported")
                        continue
                    reply("234 AUTH TLS ok")
                    s["conn"] = self.ctx.wrap_socket(s["conn"], server_side=True)
                    s["tls"] = True
                    f = s["conn"].makefile("rb")
                elif cmd in ("USER", "PASS") and self.require_tls and not s["tls"]:
                    if cmd == "PASS":
                        self.plain_password_seen = True
                    reply("530 TLS required")
                elif cmd == "USER":
                    s["user"] = arg
                    reply("331 password please")
                elif cmd == "PASS":
                    if not s["tls"]:
                        self.plain_password_seen = True
                    if s["user"] == self.user and arg == self.password:
                        s["auth"] = True
                        reply("230 logged in")
                    else:
                        reply("530 Login incorrect")
                elif cmd in ("SYST", "FEAT", "NOOP", "TYPE", "PBSZ", "OPTS"):
                    reply({"SYST": "215 UNIX Type: L8", "FEAT": "211 End", "NOOP": "200 ok", "TYPE": "200 type ok",
                           "PBSZ": "200 PBSZ=0", "OPTS": "200 ok"}[cmd])
                elif cmd == "PROT":
                    s["prot"] = arg.upper() == "P"
                    reply("200 PROT ok")
                elif cmd == "QUIT":
                    reply("221 bye")
                    break
                elif not s["auth"]:
                    reply("530 not logged in")
                elif cmd == "PWD":
                    reply(f'257 "{s["cwd"]}"')
                elif cmd == "CWD":
                    p = self._path(s, arg)
                    if p and os.path.isdir(p):
                        rel = os.path.relpath(p, self.root)
                        s["cwd"] = "/" if rel == "." else "/" + rel
                        reply("250 ok")
                    else:
                        reply("550 no such directory")
                elif cmd == "MKD":
                    p = self._path(s, arg)
                    if p and not os.path.exists(p):
                        os.mkdir(p)
                        reply(f'257 "{arg}" created')
                    else:
                        reply("550 cannot create")
                elif cmd == "PASV":
                    ds = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    ds.bind(("127.0.0.1", 0))
                    ds.listen(1)
                    s["pasv"] = ds
                    port = ds.getsockname()[1]
                    reply(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})")
                elif cmd in ("STOR", "RETR", "NLST", "LIST"):
                    self._transfer(s, cmd, arg, reply)
                elif cmd == "DELE":
                    p = self._path(s, arg)
                    if p and os.path.isfile(p):
                        os.remove(p)
                        reply("250 deleted")
                    else:
                        reply("550 no such file")
                elif cmd == "RNFR":
                    p = self._path(s, arg)
                    if p and os.path.exists(p):
                        s["rnfr"] = p
                        reply("350 ready")
                    else:
                        reply("550 no such file")
                elif cmd == "RNTO":
                    p = self._path(s, arg)
                    if s["rnfr"] and p:
                        os.replace(s["rnfr"], p)
                        s["rnfr"] = None
                        reply("250 renamed")
                    else:
                        reply("503 RNFR first")
                else:
                    reply("502 not implemented")
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                s["conn"].close()
            except OSError:
                pass

    def _path(self, s: dict, arg: str) -> Optional[str]:
        rel = arg if arg.startswith("/") else os.path.join(s["cwd"], arg)
        p = os.path.realpath(os.path.join(self.root, rel.lstrip("/")))
        return p if p == self.root or p.startswith(self.root + os.sep) else None

    def _transfer(self, s: dict, cmd: str, arg: str, reply) -> None:
        if not s["pasv"]:
            reply("425 use PASV first")
            return
        p = self._path(s, arg or ".")
        if cmd == "RETR" and not (p and os.path.isfile(p)):
            reply("550 no such file")
            return
        if cmd == "STOR" and not p:
            reply("553 bad name")
            return
        reply("150 opening data connection")
        ds = s["pasv"]
        s["pasv"] = None
        dc, _ = ds.accept()
        ds.close()
        if s["prot"]:
            dc = self.ctx.wrap_socket(dc, server_side=True)
        try:
            if cmd == "STOR":
                with open(p, "wb") as out:
                    while True:
                        b = dc.recv(65536)
                        if not b:
                            break
                        out.write(b)
            elif cmd == "RETR":
                with open(p, "rb") as src:
                    first = True
                    while True:
                        b = src.read(65536)
                        if not b:
                            break
                        if first and self.corrupt_retr:
                            b = bytes([b[0] ^ 0xFF]) + b[1:]
                        first = False
                        dc.sendall(b)
            else:
                d = p if p and os.path.isdir(p) else os.path.join(self.root, s["cwd"].lstrip("/"))
                dc.sendall("".join(n + "\r\n" for n in sorted(os.listdir(d))).encode("utf-8"))
            if isinstance(dc, ssl.SSLSocket):
                try:
                    dc.unwrap()
                except (OSError, ssl.SSLError):
                    pass
        finally:
            dc.close()
        reply("226 transfer complete")


def start_test_server(**kw) -> tuple[FtpsTestServer, str, str]:
    """回傳（伺服器, 遠端根目錄, CA 憑證路徑）。"""
    d = tempfile.mkdtemp(prefix="istudy-ftps-")
    root = os.path.join(d, "home")
    os.makedirs(os.path.join(root, "public_html"))
    ca, crt, key = make_certs(d)
    return FtpsTestServer(root, crt, key, **kw).start(), root, ca
