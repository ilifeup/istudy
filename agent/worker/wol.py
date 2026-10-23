"""WOL 喚醒封包（第 09 章）：由 Agent 依設備設定的廣播位址與連接埠送出（取代 dev.45 固定 255.255.255.255）。"""
from __future__ import annotations

import re
import socket
import time

from . import net
from .context import Ctx, JobError


def magic_packet(mac: str) -> bytes:
    hexs = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hexs) != 12:
        raise JobError("MAC 格式錯誤")
    return b"\xff" * 6 + bytes.fromhex(hexs) * 16


def send(mac: str, broadcast: str, port: int, count: int = 3, interval: float = 0.1) -> int:
    pkt = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for i in range(max(1, min(3, count))):
            s.sendto(pkt, (broadcast, int(port)))
            if i + 1 < count:
                time.sleep(interval)
    return count


def wol_wake(ctx: Ctx, job: dict) -> dict:
    p = job["payload"]
    # dev.55 WOL-02：廣播位址空白＝自動，依 Server 網卡的網路遮罩計算（例如 10.3.3.0/24 → 10.3.3.255）
    broadcast = str(p.get("broadcast") or "") or net.auto_broadcast()
    try:
        n = send(str(p["mac"]), broadcast, int(p.get("port") or 9), int(p.get("send_count") or 3))
    except OSError as e:
        raise JobError(f"送出喚醒封包失敗：{e.strerror or e}") from e
    return {"sent": n, "broadcast": broadcast, "auto": not p.get("broadcast"), "port": p.get("port")}
