"""dev.62：以 age 的「密碼模式」（age -p）加密／解密恢復金鑰。

age 只從終端機讀密碼（不接受參數、環境變數或檔案），所以這裡建立一個虛擬終端機（pty）把密碼交給 age：
密碼只經過記憶體與 pty，不出現在指令列、環境變數、檔案或日誌。

主機沒有安裝 age：istudyctl 以 Agent 映像（內含 age）跑一次性容器執行本程式（--network none、唯讀）：
    標準輸入：第一行是密碼，其後是內容（wrap：私鑰文字；unwrap：恢復金鑰檔）；標準輸出：結果
    結束碼：0 成功、2 密碼錯誤、1 其他錯誤
"""
from __future__ import annotations

import fcntl
import os
import select
import subprocess
import sys
import termios
import threading
import time

AGE_BIN = os.environ.get("ISTUDY_AGE_BIN") or "age"


class WrongPassword(Exception):
    pass


def _run(args: list[str], data: bytes, password: str, prompts: int, age_bin: str, timeout: float = 120.0) -> bytes:
    pw = password.encode("utf-8")
    master, slave = os.openpty()

    def _ctty() -> None:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    p = subprocess.Popen([age_bin, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=slave,
                         preexec_fn=_ctty, close_fds=True, pass_fds=(slave,))
    os.close(slave)
    out: list[bytes] = []
    reader = threading.Thread(target=lambda: out.append(p.stdout.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    try:
        p.stdin.write(data)  # type: ignore[union-attr]
        p.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:
        pass
    tty = b""
    sent = 0
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if p.poll() is not None and sent >= prompts:
            break
        r, _, _ = select.select([master], [], [], 0.2)
        if master in r:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            tty += chunk
            if sent < prompts and tty.lower().count(b"passphrase") > sent:
                time.sleep(0.15)                       # 讓 age 先關閉終端機回顯
                os.write(master, pw + b"\n")
                sent += 1
        elif p.poll() is not None:
            break
    try:
        rc = p.wait(timeout=max(1.0, end - time.monotonic()))
    except subprocess.TimeoutExpired:
        p.kill()
        rc = p.wait()
    reader.join(10)
    os.close(master)
    tty = tty.replace(pw, b"***")                       # 萬一回顯，錯誤訊息裡也不會有密碼
    if rc != 0:
        msg = tty.decode("utf-8", "replace")
        if "incorrect passphrase" in msg:
            raise WrongPassword("恢復密碼錯誤")
        err = [ln.strip() for ln in msg.replace("\r", "\n").split("\n") if ln.strip().startswith("age: error")]
        raise RuntimeError((err[0] if err else f"age 結束碼 {rc}")[:300])
    return out[0] if out else b""


def wrap(identity: bytes, password: str, age_bin: str = AGE_BIN) -> bytes:
    """以密碼加密私鑰（ASCII armor），輸出可存檔、可上傳的恢復金鑰檔。"""
    return _run(["-p", "-a"], identity, password, 2, age_bin)


def unwrap(wrapped: bytes, password: str, age_bin: str = AGE_BIN) -> bytes:
    return _run(["-d"], wrapped, password, 1, age_bin)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("wrap", "unwrap"):
        print("用法：agepass.py wrap|unwrap（標準輸入：第一行密碼，其後內容）", file=sys.stderr)
        return 1
    raw = sys.stdin.buffer.read()
    nl = raw.find(b"\n")
    if nl < 0:
        print("缺少密碼", file=sys.stderr)
        return 1
    password, data = raw[:nl].decode("utf-8").rstrip("\r"), raw[nl + 1:]
    try:
        res = wrap(data, password) if argv[1] == "wrap" else unwrap(data, password)
    except WrongPassword as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(str(e)[:300], file=sys.stderr)
        return 1
    sys.stdout.buffer.write(res)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
