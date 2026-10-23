"""dev.55 WOL-01／02／03：Server 端的輕量網路檢測（Agent 使用主機網路，network_mode: host）。

- lan_info()：Server 的區域網路介面、IP、網路遮罩、廣播位址（WOL「自動」廣播位址用）。
- net_probe：按需 ICMP ping 已登記設備的 LAN IP。沒回應只代表「未回應」，不代表關機。
- lan_scan：手動觸發，只掃 Server 所在網段且最多 /24（254 台），每台逾時約 1 秒；
  MAC 從 Server 的 ARP 表讀取，取不到就留空；名稱用反查 DNS，取不到就留空（不虛構）。
不常駐掃描：只有 jobs 表有 net_probe／lan_scan 工作時才動作。
"""
from __future__ import annotations

import concurrent.futures as cf
import fcntl
import ipaddress
import os
import select
import socket
import struct
import time
from typing import Optional

from .context import Ctx, JobError

SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B
SKIP_IFACE = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "wg", "zt")
MAX_HOSTS = 254


class PingUnavailable(Exception):
    pass


# ---------- Server 的區域網路介面 ----------

def _ifaddr(ifname: str, req: int) -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            r = fcntl.ioctl(s.fileno(), req, struct.pack("256s", ifname[:15].encode()))
        return socket.inet_ntoa(r[20:24])
    except OSError:
        return None


def _routes(path: str = "/proc/net/route") -> list[dict]:
    out = []
    try:
        with open(path, encoding="ascii") as f:
            next(f, None)
            for ln in f:
                p = ln.split()
                if len(p) >= 8:
                    out.append({"iface": p[0], "dest": p[1], "flags": int(p[3], 16), "mask": p[7]})
    except OSError:
        pass
    return out


def lan_info(server_ip: Optional[str] = None, route_file: str = "/proc/net/route") -> dict:
    """回傳 {iface, ip, netmask, prefix, network, broadcast}；判斷不出來回傳 {}（呼叫端改用 255.255.255.255）。"""
    server_ip = server_ip or os.environ.get("ISTUDY_SERVER_IP", "10.3.3.100")
    routes = _routes(route_file)
    cands = []
    for r in routes:
        name = r["iface"]
        if name.startswith(SKIP_IFACE) or name in cands:
            continue
        cands.append(name)
    default = [r["iface"] for r in routes if r["dest"] == "00000000" and r["flags"] & 0x3 == 0x3]
    best = None
    for name in cands:
        ip = _ifaddr(name, SIOCGIFADDR)
        if not ip:
            continue
        mask = _ifaddr(name, SIOCGIFNETMASK) or "255.255.255.0"
        info = (name, ip, mask)
        if ip == server_ip:
            best = info
            break
        if best is None or (name in default and best[0] not in default):
            best = info
    if not best:
        return {}
    name, ip, mask = best
    try:
        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except ValueError:
        return {}
    return {"iface": name, "ip": ip, "netmask": mask, "prefix": net.prefixlen, "network": str(net),
            "broadcast": str(net.broadcast_address), "is_private": net.is_private}


def auto_broadcast() -> str:
    return lan_info().get("broadcast") or "255.255.255.255"


# ---------- ICMP ping（不需要 root：Linux ping socket；退而求其次才用 raw socket） ----------

def _checksum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _echo(ident: int, seq: int) -> bytes:
    payload = b"istudy-probe"
    hdr = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
    return struct.pack("!BBHHH", 8, 0, _checksum(hdr + payload), ident, seq) + payload


def _icmp_socket() -> tuple[socket.socket, str]:
    errors = []
    for kind, args in (("dgram", (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)),
                       ("raw", (socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP))):
        try:
            return socket.socket(*args), kind
        except OSError as e:
            errors.append(f"{kind}: {e.strerror or e}")
    raise PingUnavailable("無法建立 ping socket（" + "；".join(errors) + "）：請確認 net.ipv4.ping_group_range 包含 Agent 的群組")


def ping_many(ips: list[str], timeout: float = 1.0, tries: int = 2, gap: float = 0.004) -> dict[str, bool]:
    """同一個 socket 依序送出（每台間隔 gap 秒），再收回覆。回傳 {ip: 是否回應}。"""
    ips = list(dict.fromkeys(ips))
    alive: set[str] = set()
    if not ips:
        return {}
    sock, kind = _icmp_socket()
    ident = os.getpid() & 0xFFFF
    try:
        for attempt in range(tries):
            todo = [ip for ip in ips if ip not in alive]
            if not todo:
                break
            for i, ip in enumerate(todo):
                try:
                    sock.sendto(_echo(ident, (attempt * 1000 + i) & 0xFFFF), (ip, 0))
                except OSError:
                    pass
                if gap:
                    time.sleep(gap)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                r, _, _ = select.select([sock], [], [], left)
                if not r:
                    break
                try:
                    data, addr = sock.recvfrom(2048)
                except OSError:
                    continue
                if kind == "raw" and data:
                    data = data[(data[0] & 0x0F) * 4:]
                    if len(data) >= 8 and struct.unpack("!H", data[4:6])[0] != ident:
                        continue
                if data and data[0] == 0 and addr[0] in ips:
                    alive.add(addr[0])
    finally:
        sock.close()
    return {ip: ip in alive for ip in ips}


def nudge_arp(ips: list[str], gap: float = 0.004) -> None:
    """ping 不能用時：送一個極小的 UDP 封包（discard 埠 9），讓 Server 對每台做 ARP，之後才讀得到 MAC。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for ip in ips:
            try:
                s.sendto(b"", (ip, 9))
            except OSError:
                pass
            if gap:
                time.sleep(gap)


def read_arp(iface: Optional[str] = None, path: str = "/proc/net/arp") -> dict[str, str]:
    """{ip: mac}：只取已完成解析（flags 0x2）的項目。"""
    out = {}
    try:
        with open(path, encoding="ascii") as f:
            next(f, None)
            for ln in f:
                p = ln.split()
                if len(p) >= 6 and int(p[2], 16) & 0x2 and p[3] != "00:00:00:00:00:00" and (not iface or p[5] == iface):
                    out[p[0]] = p[3].lower()
    except OSError:
        pass
    return out


def reverse_names(ips: list[str], budget: float = 3.0) -> dict[str, str]:
    """反查 DNS 名稱；整體最多 budget 秒，查不到的就沒有（不虛構）。"""
    names: dict[str, str] = {}
    if not ips:
        return names
    ex = cf.ThreadPoolExecutor(max_workers=16)
    futs = {ex.submit(socket.gethostbyaddr, ip): ip for ip in ips}
    try:
        for f in cf.as_completed(futs, timeout=budget):
            try:
                names[futs[f]] = f.result()[0]
            except OSError:
                pass
    except cf.TimeoutError:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return names


# ---------- 工作 ----------

def net_probe(ctx: Ctx, job: dict) -> dict:
    """ping 已登記設備（LAN IP；沒填時用心跳回報的最後 IP）。結果寫回 client_devices.probe_ok／probe_at。"""
    rows = ctx.conn.execute("SELECT id, lan_ip, last_ip FROM client_devices WHERE registered ORDER BY id")
    targets: dict[int, str] = {}
    for r in rows:
        ip = (r["lan_ip"] or r["last_ip"] or "").strip()
        try:
            if ip and ipaddress.IPv4Address(ip).is_private:
                targets[int(r["id"])] = ip
        except ValueError:
            continue
    if not targets:
        return {"probed": 0, "message": "沒有可檢測的設備（請在設備清單填 LAN IP）"}
    try:
        res = ping_many(list(targets.values()))
    except PingUnavailable as e:
        ctx.syslog("warn", "Server 主動檢測（ping）無法執行：" + str(e))
        ctx.conn.execute("UPDATE client_devices SET probe_ok = NULL, probe_at = NOW() WHERE id = ANY(CAST(%s AS bigint[]))",
                         [list(targets)])
        return {"probed": 0, "ping_available": False, "message": str(e)}
    for did, ip in targets.items():
        ctx.conn.execute("UPDATE client_devices SET probe_ok = %s, probe_at = NOW() WHERE id = %s", [res.get(ip, False), did])
    return {"probed": len(targets), "responded": sum(1 for v in res.values() if v), "ping_available": True}


def scan_range(info: dict) -> tuple[ipaddress.IPv4Network, list[str], list[str]]:
    """掃描範圍：Server 所在網段，最多 /24。回傳（網段、主機清單、說明）。"""
    notes = []
    net = ipaddress.IPv4Network(info["network"])
    if net.prefixlen < 24:
        net = ipaddress.IPv4Network(f"{info['ip']}/24", strict=False)
        notes.append(f"網段 {info['network']} 大於 /24，只掃 Server 所在的 {net}")
    if not net.is_private:
        raise JobError(f"{net} 不是私有網段，拒絕掃描")
    hosts = [str(h) for h in net.hosts() if str(h) != info["ip"]][:MAX_HOSTS]
    return net, hosts, notes


def lan_scan(ctx: Ctx, job: dict) -> dict:
    info = lan_info()
    if not info:
        raise JobError("無法判斷 Server 的區域網路介面")
    net, hosts, notes = scan_range(info)
    started = time.monotonic()
    ping_ok = True
    try:
        alive = ping_many(hosts)
    except PingUnavailable as e:
        ping_ok = False
        notes.append("ping 無法使用（" + str(e) + "）；只列出 ARP 表查得到的裝置")
        nudge_arp(hosts)
        time.sleep(1.5)
        alive = {}
    arp = read_arp(info["iface"])
    found = sorted({ip for ip, ok in alive.items() if ok} | {ip for ip in arp if ip in set(hosts)},
                   key=lambda x: ipaddress.IPv4Address(x))
    names = reverse_names(found)
    known = {}
    for d in ctx.conn.execute("SELECT id, name, mac, array_to_string(all_macs, ',') AS macs, lan_ip, registered FROM client_devices"):
        for m in [d["mac"]] + (d["macs"] or "").split(","):
            if m:
                known[("mac", str(m).strip().lower())] = d
        if d["lan_ip"]:
            known[("ip", d["lan_ip"])] = d
    out = []
    for ip in found:
        mac = arp.get(ip, "")
        d = known.get(("mac", mac)) if mac else None
        d = d or known.get(("ip", ip))
        out.append({"ip": ip, "mac": mac, "name": names.get(ip, ""), "ping": alive.get(ip) if ping_ok else None,
                    "known_device_id": int(d["id"]) if d else None, "known_name": d["name"] if d else "",
                    "known_registered": bool(d["registered"]) if d else False})
    if not ping_ok or any(not h["mac"] for h in out):
        notes.append("部分裝置取不到 MAC（不同網段、或 ARP 未回應），無法直接加入 WOL")
    if not names:
        notes.append("區域網路沒有提供 DNS 反查名稱；名稱請自行填寫")
    return {"network": str(net), "iface": info["iface"], "server_ip": info["ip"], "broadcast": info["broadcast"],
            "scanned": len(hosts), "ping_available": ping_ok, "seconds": round(time.monotonic() - started, 1),
            "hosts": out, "partial": (not ping_ok) or any(not h["mac"] for h in out), "notes": notes}
