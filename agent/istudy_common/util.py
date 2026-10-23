"""Agent 與 istudyctl 共用的小工具：設定、路徑、教材類型、自然排序、排程時段。只用標準函式庫。"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import Optional

TZ = _dt.timezone(_dt.timedelta(hours=8), "Asia/Taipei")

VIDEO_EXT = {"mts", "m2ts", "ts", "mp4", "m4v", "mov", "mkv", "avi", "wmv", "mpg", "mpeg", "webm", "flv", "3gp", "vob", "mod", "tod"}
PDF_EXT = {"pdf"}
OFFICE_EXT = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf"}
TEXT_EXT = {"txt"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
SUBTITLE_EXT = {"srt", "vtt"}
# 系統暫存檔（掃描略過，不列入報告）
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
JUNK_PREFIX = ("~$", ".~lock")
JUNK_SUFFIX = (".tmp", ".part", ".crdownload", ".lnk")
# dev.59：教材根目錄就是 /Media。這些名稱只在「/Media 第一層」視為系統資料夾、不當教材（不分大小寫）；
# 課程裡的子資料夾或檔案剛好同名（例如 /Media/課程A/subtitles/）照常是教材。
# dev.61：Outgo＝Super 的錄音轉文字工作區（/Media/Outgo），整個目錄樹都不是教材。
SYSTEM_TOP_DIRS = ("playback", "subtitles", "doc-cache", "Backups", "Installers", "lost+found",
                   "$RECYCLE.BIN", "System Volume Information", "Outgo")
OUTGO_DIR = "Outgo"
_SYSTEM_TOP = {n.lower() for n in SYSTEM_TOP_DIRS}
MAX_REL_BYTES = 2048   # 與 PHP safe_rel_path 相同


def is_system_top(name: str) -> bool:
    """/Media 第一層的這個名稱是不是系統資料夾。"""
    return name.lower() in _SYSTEM_TOP


def name_problem(name: str) -> Optional[str]:
    """檔名或資料夾名稱不能當教材的原因（沒有問題回傳 None）。Linux 只禁止 / 與 NUL，其他字元（中文、空白、: ? * #）都可以。"""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return "名稱不是 UTF-8 編碼（請改名後重新掃描）"
    if "\\" in name:
        return "名稱含有反斜線「\\」（請改名後重新掃描）"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "名稱含有控制字元（例如換行，請改名後重新掃描）"
    return None


def kind_for_ext(ext: str) -> Optional[str]:
    e = ext.lower()
    if e in VIDEO_EXT:
        return "video"
    if e in PDF_EXT:
        return "pdf"
    if e in OFFICE_EXT:
        return "office"
    if e in TEXT_EXT:
        return "text"
    if e in IMAGE_EXT:
        return "image"
    return None


@dataclass(frozen=True)
class Paths:
    media_root: str

    @property
    def library(self) -> str:
        """教材根目錄（dev.59 起就是 /Media；第一層的系統資料夾見 SYSTEM_TOP_DIRS）。"""
        return self.media_root

    @property
    def playback(self) -> str:
        return os.path.join(self.media_root, "playback")

    @property
    def subtitles(self) -> str:
        return os.path.join(self.media_root, "subtitles")

    @property
    def doc_cache(self) -> str:
        return os.path.join(self.media_root, "doc-cache")

    @property
    def marker(self) -> str:
        return os.path.join(self.media_root, ".istudy-media")

    def playback_file(self, media_id: int) -> str:
        return os.path.join(self.playback, f"{media_id}.mp4")

    def playback_tmp(self, media_id: int) -> str:
        return os.path.join(self.playback, f".{media_id}.tmp.mp4")

    def subtitle_file(self, media_id: int) -> str:
        return os.path.join(self.subtitles, f"{media_id}.vtt")

    def doc_file(self, media_id: int) -> str:
        return os.path.join(self.doc_cache, f"{media_id}.pdf")

    @property
    def outgo(self) -> str:
        return os.path.join(self.media_root, OUTGO_DIR)

    def outgo_file(self, rel_path: str) -> str:
        """Outgo 底下的相對路徑 → 實體路徑。拒絕跳出 Outgo、隱藏項目（.開頭）與任何一層符號連結。"""
        if not rel_path or rel_path.startswith("/") or "\\" in rel_path or len(rel_path.encode("utf-8", "replace")) > MAX_REL_BYTES:
            raise ValueError("Outgo 路徑格式錯誤")
        parts = rel_path.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") or name_problem(p) for p in parts):
            raise ValueError("Outgo 路徑格式錯誤")
        if os.path.islink(self.outgo) or not os.path.isdir(self.outgo):
            raise ValueError("找不到 /Media/Outgo（或它是符號連結）")
        root = os.path.realpath(self.outgo)
        joined = os.path.join(root, *parts)
        if os.path.realpath(joined) != joined:      # 中間任何一層是符號連結（或跳出 Outgo）
            raise ValueError("Outgo 路徑含有符號連結或超出 Outgo")
        return joined

    def original(self, rel_path: str) -> str:
        """rel_path → 實體路徑；拒絕跳出 /Media，也拒絕 /Media 第一層的系統資料夾。"""
        if not rel_path or rel_path.startswith("/") or "\\" in rel_path or any(p in ("", ".", "..") for p in rel_path.split("/")):
            raise ValueError("教材路徑格式錯誤")
        if is_system_top(rel_path.split("/", 1)[0]) or rel_path.startswith("."):
            raise ValueError("教材路徑指向系統資料夾")
        full = os.path.realpath(os.path.join(self.library, rel_path))
        root = os.path.realpath(self.library)
        if not full.startswith(root + os.sep):
            raise ValueError("教材路徑超出允許的根目錄")
        # dev.61：符號連結解開後也不能落在系統資料夾（例如 Outgo、playback）或隱藏項目
        real_top = full[len(root) + 1:].split(os.sep, 1)[0]
        if is_system_top(real_top) or real_top.startswith("."):
            raise ValueError("教材路徑指向系統資料夾")
        return full


def paths_from_env() -> Paths:
    return Paths(os.environ.get("MEDIA_ROOT", "/Media"))


# ---------- 自然排序（與 PHP strnatcasecmp 的結果一致：數字依數值比較，不分大小寫） ----------

_num = re.compile(r"(\d+)")


def natural_key(s: str):
    parts = _num.split(s.lower())
    return [(0, int(p), p) if p.isdigit() else (1, 0, p) for p in parts], s


# ---------- 排程時段（可跨午夜） ----------

def parse_hhmm(v: str) -> _dt.time:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", v or "")
    if not m:
        raise ValueError(f"時間格式錯誤：{v}")
    return _dt.time(int(m.group(1)), int(m.group(2)))


def in_window(start: str, end: str, now: Optional[_dt.datetime] = None) -> bool:
    """now 是否在 [start, end) 時段內；start > end 代表跨午夜（例如 22:30～03:00）。"""
    now = now or _dt.datetime.now(TZ)
    t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    s, e = parse_hhmm(start), parse_hhmm(end)
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e


def now_tz() -> _dt.datetime:
    return _dt.datetime.now(TZ)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"
