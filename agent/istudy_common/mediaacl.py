"""教材權限檢查（dev.59，istudyctl media-acl 使用）。

檢查要在 Agent 容器裡、以 Agent 本身（uid 10001）執行：由 Linux 實際判斷 ACL、mask、群組與 sticky bit，
不自己解析 ACL 規則。只看教材：/Media 第一層的系統資料夾與「.」開頭的項目不檢查、也不修復。

Agent 需要：
  - 資料夾：可進入、可讀（掃描）、可寫（刪除已轉檔的原檔需要「所在資料夾」可寫）；沒有 sticky bit（sticky 只允許擁有者刪除）。
  - 檔案：可讀（掃描、轉檔、串流）。
只用標準函式庫。
"""
from __future__ import annotations

import os
import stat

from .util import OUTGO_DIR, is_system_top

KINDS = {
    "dir_no_access": "資料夾無法進入或讀取（底下的教材掃描不到）",
    "dir_no_write": "資料夾不能寫入（底下的原檔轉檔後無法刪除）",
    "sticky": "資料夾有 sticky bit（只有擁有者能刪除，原檔轉檔後無法刪除）",
    "file_no_read": "檔案讀不到（無法掃描、轉檔或播放）",
}
EXAMPLES = 20


def library_entries(root: str) -> list[str]:
    """/Media 第一層要檢查／修復的項目（排除系統資料夾與隱藏項目、符號連結）。"""
    out = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name.startswith(".") or is_system_top(name) or os.path.islink(p):
            continue
        out.append(name)
    return out


def scan_access(root: str) -> dict:
    """以目前的使用者實際測試權限。回傳 {uid, dirs, files, counts, examples, root_write}。"""
    found: dict[str, list[str]] = {k: [] for k in KINDS}
    counts = {k: 0 for k in KINDS}
    n_dirs = n_files = root_files = 0

    def add(kind: str, rel: str) -> None:
        counts[kind] += 1
        if len(found[kind]) < EXAMPLES:
            found[kind].append(rel)

    def check_dir(path: str, rel: str) -> bool:
        nonlocal n_dirs
        n_dirs += 1
        st = os.lstat(path)
        if st.st_mode & stat.S_ISVTX:
            add("sticky", rel)
        if not os.access(path, os.W_OK):
            add("dir_no_write", rel)
        if not os.access(path, os.R_OK | os.X_OK):
            add("dir_no_access", rel)
            return False
        return True

    root_write = os.access(root, os.W_OK)
    root_read = os.access(root, os.R_OK | os.X_OK)
    if root_read:
        for name in library_entries(root):
            top = os.path.join(root, name)
            if os.path.isdir(top):
                stack = [(top, name)]
                while stack:
                    path, rel = stack.pop()
                    if not check_dir(path, rel):
                        continue
                    try:
                        entries = list(os.scandir(path))
                    except OSError:
                        add("dir_no_access", rel)
                        continue
                    for e in entries:
                        if e.name.startswith(".") or e.is_symlink():
                            continue
                        sub = f"{rel}/{e.name}"
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, sub))
                        elif e.is_file(follow_symlinks=False):
                            n_files += 1
                            if not os.access(e.path, os.R_OK):
                                add("file_no_read", sub)
            elif os.path.isfile(top):
                n_files += 1
                root_files += 1
                if not os.access(top, os.R_OK):
                    add("file_no_read", name)
    # 根目錄要可寫，只在「根目錄本身就放了教材檔案」時才需要（刪除這些原檔）
    need_root_write = root_files > 0
    outgo = outgo_access(os.path.join(root, OUTGO_DIR))
    return {"uid": os.getuid(), "root": root, "root_read": root_read, "root_write": root_write or not need_root_write,
            "root_files": root_files, "dirs": n_dirs, "files": n_files, "counts": counts, "examples": found, "outgo": outgo,
            "ok": root_read and (root_write or not need_root_write) and not any(counts.values()) and outgo["ok"]}


def outgo_access(path: str) -> dict:
    """dev.61：/Media/Outgo（錄音轉文字工作區）：要存在、不是符號連結；Agent 要能讀取並在每一層寫入逐字稿。"""
    if os.path.islink(path):
        return {"path": path, "exists": True, "symlink": True, "no_write": [], "ok": False}
    if not os.path.isdir(path):
        return {"path": path, "exists": False, "symlink": False, "no_write": [], "ok": False}
    bad = []
    for d, subdirs, _files in os.walk(path):
        subdirs[:] = [x for x in subdirs if not x.startswith(".") and not os.path.islink(os.path.join(d, x))]
        if not os.access(d, os.R_OK | os.W_OK | os.X_OK):
            bad.append(os.path.relpath(d, os.path.dirname(path)))
            if len(bad) >= EXAMPLES:
                break
    return {"path": path, "exists": True, "symlink": False, "no_write": bad, "ok": not bad}


def summary_lines(rep: dict) -> list[str]:
    lines = [f"檢查身分 uid {rep.get('uid')}：{rep.get('dirs', 0)} 個資料夾、{rep.get('files', 0)} 個檔案（不含系統資料夾）"]
    if not rep.get("root_read"):
        lines.append(f"✗ {rep.get('root')} 無法讀取：所有教材都掃描不到")
    if not rep.get("root_write"):
        lines.append(f"✗ {rep.get('root')} 不能寫入：根目錄的 {rep.get('root_files')} 個原檔轉檔後無法刪除")
    for k, label in KINDS.items():
        n = (rep.get("counts") or {}).get(k, 0)
        if n:
            ex = "、".join((rep.get("examples") or {}).get(k, [])[:5])
            lines.append(f"✗ {label}：{n} 個（例如 {ex}）")
    og = rep.get("outgo") or {}
    if og and og.get("symlink"):
        lines.append(f"✗ {og['path']} 是符號連結：Outgo 停用（請改成一般資料夾）")
    elif og and not og.get("exists"):
        lines.append(f"✗ 找不到 {og['path']}（Outgo 錄音轉文字工作區）：--fix 會建立")
    elif og and og.get("no_write"):
        lines.append(f"✗ Outgo 有 {len(og['no_write'])} 個資料夾 Agent 不能寫入逐字稿（例如 {'、'.join(og['no_write'][:5])}）")
    if rep.get("ok"):
        lines.append("✓ Agent 可以讀取全部教材，也能刪除已轉檔的原檔；Outgo 可以寫入逐字稿")
    return lines
