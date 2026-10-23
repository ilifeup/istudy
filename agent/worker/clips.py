"""AVCHD 連續片段（合集）判定（第 13 章；Q-02 決定不合併檔案，只記錄合集）。

判定條件（全部符合）：
 ① 同一資料夾、副檔名為 .MTS／.M2TS
 ② 檔名數字連號（00019 → 00020）
 ③ 前一支的大小接近攝影機切檔上限（預設 1.9 GiB 以上，可調整）
 ④ 影像與音訊格式相同（解析度、影格率、編碼、聲道）——兩支都已檢測才比對；尚未檢測時先不判定
 ⑤ 讀得到錄影時間時，前一支結束與下一支開始相差 5 秒以內；讀不到時依 ①～④ 判定
Super 手動調整過（clip_manual）的影片不參與自動判定。
"""
from __future__ import annotations

import re
from typing import Optional

from istudy_common.db import Connection
from istudy_common.util import natural_key

CLIP_EXT = {"mts", "m2ts"}
GIB = 1024 ** 3
_num = re.compile(r"(\d+)\s*$")


def _seq_number(file_name: str) -> Optional[tuple[str, int, int]]:
    stem = file_name.rsplit(".", 1)[0]
    m = _num.search(stem)
    if not m:
        return None
    return stem[: m.start()], int(m.group(1)), len(m.group(1))


def _signature(probe: Optional[dict]) -> Optional[tuple]:
    if not probe:
        return None
    return (probe.get("width"), probe.get("height"), probe.get("fps"), probe.get("video_codec"),
            probe.get("audio_codec"), probe.get("audio_channels"))


def is_continuation(prev: dict, nxt: dict, threshold_bytes: int) -> bool:
    a, b = _seq_number(prev["file_name"]), _seq_number(nxt["file_name"])
    if not a or not b or a[0] != b[0] or b[1] != a[1] + 1:
        return False
    if prev["size_bytes"] < threshold_bytes:
        return False
    sa, sb = _signature(prev.get("probe")), _signature(nxt.get("probe"))
    if sa is None or sb is None or sa != sb:
        return False
    if prev.get("recorded_at") and nxt.get("recorded_at") and prev.get("duration_sec"):
        end = prev["recorded_at"].timestamp() + float(prev["duration_sec"])
        if abs(nxt["recorded_at"].timestamp() - end) > 5:
            return False
    return True


def group_name(members: list[dict]) -> str:
    first = members[0]["file_name"].rsplit(".", 1)[0]
    last = members[-1]["file_name"].rsplit(".", 1)[0]
    rec = members[0].get("recorded_at")
    return f"{first}～{last}（{rec.strftime('%Y-%m-%d')} 錄影）" if rec else f"{first}～{last}"


def compute_runs(rows: list[dict], threshold_bytes: int) -> list[list[dict]]:
    vids = sorted([r for r in rows if r["ext"] in CLIP_EXT and r["status"] != "missing"], key=lambda r: natural_key(r["file_name"]))
    runs, cur = [], []
    for r in vids:
        if cur and is_continuation(cur[-1], r, threshold_bytes):
            cur.append(r)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [r]
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def detect(conn: Connection, folder_id: int, threshold_gib: float = 1.9) -> dict:
    """重新判定一個資料夾的自動合集。回傳 {groups, members}。"""
    threshold = int(float(threshold_gib) * GIB)
    rows = conn.execute(
        "SELECT m.id, m.file_name, m.ext, m.size_bytes, m.probe, m.recorded_at, m.duration_sec, m.status, m.clip_group_id, "
        "m.clip_manual, g.source AS group_source FROM media m LEFT JOIN clip_groups g ON g.id = m.clip_group_id "
        "WHERE m.folder_id = %s AND m.kind = 'video'", [folder_id])
    auto_rows = [r for r in rows if not r["clip_manual"] and (r["clip_group_id"] is None or r["group_source"] == "auto")]
    runs = compute_runs(auto_rows, threshold)
    keep_groups: set[int] = set()
    assigned: set[int] = set()
    with conn.transaction():
        for run in runs:
            ids = [r["id"] for r in run]
            existing = {r["clip_group_id"] for r in run}
            gid = None
            if len(existing) == 1 and None not in existing:
                gid = existing.pop()
                count = conn.val("SELECT COUNT(*) FROM media WHERE clip_group_id = %s", [gid])
                if count != len(ids):
                    gid = None
            name = group_name(run)
            if gid is None:
                gid = conn.val("INSERT INTO clip_groups(folder_id, name, recorded_at, source) VALUES (%s, %s, %s, 'auto') RETURNING id",
                               [folder_id, name, run[0].get("recorded_at")])
            else:
                conn.execute("UPDATE clip_groups SET name = %s, recorded_at = %s, updated_at = NOW() WHERE id = %s AND source = 'auto'",
                             [name, run[0].get("recorded_at"), gid])
            for seq, r in enumerate(run, 1):
                conn.execute("UPDATE media SET clip_group_id = %s, clip_seq = %s WHERE id = %s", [gid, seq, r["id"]])
                assigned.add(r["id"])
            keep_groups.add(gid)
        # 不再符合的自動合集成員移出
        for r in auto_rows:
            if r["id"] not in assigned and r["clip_group_id"] is not None:
                conn.execute("UPDATE media SET clip_group_id = NULL, clip_seq = NULL WHERE id = %s", [r["id"]])
        conn.execute("DELETE FROM clip_groups g WHERE g.folder_id = %s AND g.source = 'auto' AND "
                     "(SELECT COUNT(*) FROM media m WHERE m.clip_group_id = g.id) < 2", [folder_id])
        conn.execute("UPDATE media SET clip_group_id = NULL, clip_seq = NULL WHERE folder_id = %s AND clip_group_id IS NOT NULL "
                     "AND clip_group_id NOT IN (SELECT id FROM clip_groups)", [folder_id])
    return {"groups": len(runs), "members": len(assigned)}
