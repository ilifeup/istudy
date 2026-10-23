"""字幕文字後處理（dev.57）：去標籤、重複過濾、繁體化（OpenCC s2twp）、保護詞、修正表、二次斷句、品質檢查、CER。

順序依「iStudy ASR 技術選型規劃書」第 8 章：先在原始文字上斷句與分配時間，再對每一條字幕做文字轉換，
這樣繁簡轉換或修正表改變字數時，不會打亂時間軸。全部只用 Python 標準函式庫（OpenCC 由呼叫端傳入）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

TAG_RE = re.compile(r"<\|[^|>]{0,40}\|>")          # SenseVoice 的 <|zh|><|NEUTRAL|><|Speech|><|withitn|>
CJK = "㐀-䶿一-鿿豈-﫿"
STRONG = "。！？；!?;"                                  # 句子結束：優先換條
WEAK = "，、：,:"                                       # 句中停頓：字數太多時在這裡換條
PUNCT = STRONG + WEAK + "…—「」『』（）()《》〈〉\"'“”‘’.·"
LOOP_RE = re.compile(r"(.{1,10}?)\1{3,}")              # 同一小段連續重複 4 次以上（模型失控）


@dataclass
class Cue:
    start: int          # ms
    end: int            # ms
    text: str
    precise: bool = True   # True＝時間來自模型的逐字時間戳；False＝依字數比例估算


@dataclass
class Settings:
    max_chars: int = 20       # 每條字幕最多字數（中文字＝1，英數＝0.5）
    max_ms: int = 7000        # 每條最長顯示時間
    min_ms: int = 1000        # 每條最短顯示時間（不夠時往後延，不蓋到下一條）
    fixes: list = field(default_factory=list)      # [(錯字, 正確)]：繁體化之後套用
    protect: list = field(default_factory=list)    # 保護詞：繁體化後被改掉時還原


# ---------------------------------------------------------------- 基本清理 ----

def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def normalize_spaces(text: str) -> str:
    """中文字之間不留空白；英數之間保留一個空白；英文句點後接中文時當作句號。"""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", t)
    t = re.sub(rf"(?<=[{CJK}])\s+(?=[，。！？；：、])", "", t)
    t = re.sub(rf"(?<=[A-Za-z])\.\s*(?=[{CJK}])", "。", t)
    return t


def collapse_loops(text: str) -> tuple[str, int]:
    """「謝謝謝謝謝謝謝謝」這類模型失控的迴圈只留兩次；回傳（新文字, 修正次數）。"""
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return m.group(1) * 2
    return LOOP_RE.sub(rep, text), n


def full_width_punct(text: str) -> str:
    """中文旁的半形標點改全形；數字中的小數點、英文句子不動。"""
    t = text
    for a, b in ((",", "，"), ("?", "？"), ("!", "！"), (";", "；"), (":", "：")):
        t = re.sub(rf"(?<=[{CJK}])\{a}|\{a}(?=[{CJK}])", b, t)
    t = re.sub(rf"(?<=[{CJK}])\.(?!\d)", "。", t)
    return t


def trim_cue_punct(text: str) -> str:
    """字幕行尾不留逗號、句號、頓號；保留問號、驚嘆號。"""
    return re.sub(r"[，。、；：,.;:\s]+$", "", text).strip()


# ---------------------------------------------------------------- 字元與時間 ----

def units(text: str) -> list[str]:
    """斷句單位：每個中文字、每個英數詞；標點黏在前一個單位後面。"""
    out: list[str] = []
    for m in re.finditer(rf"[{CJK}]|[A-Za-z0-9][A-Za-z0-9'\-.%]*|[^\s]", text):
        s = m.group(0)
        if s and all(ch in PUNCT for ch in s) and out:
            out[-1] += s
        else:
            out.append(s)
    return out


def unit_weight(u: str) -> float:
    core = u.rstrip(PUNCT)
    if not core:
        return 0.0
    if re.match(rf"[{CJK}]", core):
        return 1.0
    return max(1.0, len(core) / 2)


def _core(u: str) -> str:
    return re.sub(rf"[^{CJK}A-Za-z0-9]", "", u).lower()


def align_times(text_units: list[str], tokens: Optional[list[str]], times: Optional[list[float]]) -> Optional[list[float]]:
    """把模型的逐 token 時間（秒，相對於段落開頭）對到每個斷句單位；對不上就回傳 None（改用比例估算）。"""
    if not tokens or not times or len(tokens) != len(times):
        return None
    chars: list[tuple[str, float]] = []
    for i, (tok, t) in enumerate(zip(tokens, times)):
        piece = _core(tok.replace("@@", "").replace("▁", ""))
        if not piece:
            continue
        nxt = times[i + 1] if i + 1 < len(times) else t + 0.2 * len(piece)
        step = max(0.0, (nxt - t) / max(1, len(piece)))
        for k, ch in enumerate(piece):
            chars.append((ch, t + k * step))
    stream = "".join(c for c, _ in chars)
    target = "".join(_core(u) for u in text_units)
    if not target or stream != target:
        return None
    out, pos = [], 0
    for u in text_units:
        c = _core(u)
        if not c:
            out.append(out[-1] if out else (chars[0][1] if chars else 0.0))
            continue
        out.append(chars[pos][1])
        pos += len(c)
    return out


def segment_cues(text: str, seg_start: int, seg_end: int, settings: Settings,
                 tokens: Optional[list[str]] = None, times: Optional[list[float]] = None) -> list[Cue]:
    """一個語音段落（VAD）→ 數條字幕。有逐字時間就用，沒有就依字數比例分配。"""
    us = units(text)
    if not us:
        return []
    dur = max(1, seg_end - seg_start)
    aligned = align_times(us, tokens, times)
    precise = aligned is not None
    if aligned is None:
        total = sum(unit_weight(u) for u in us) or 1.0
        acc, aligned = 0.0, []
        for u in us:
            aligned.append(acc / total * dur / 1000)
            acc += unit_weight(u)
    starts = [seg_start + int(round(t * 1000)) for t in aligned]
    groups: list[list[int]] = []
    cur: list[int] = []
    weight = 0.0
    suffix = [0.0] * (len(us) + 1)          # 從第 i 個單位到段落結尾的字數：剩下很少時不再切，避免一兩個字一條
    for i in range(len(us) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + unit_weight(us[i])
    for i, u in enumerate(us):
        w = unit_weight(u)
        tail_small = suffix[i] <= 3 and weight + suffix[i] <= settings.max_chars * 1.3
        if cur and not tail_small and (weight + w > settings.max_chars or starts[i] - starts[cur[0]] > settings.max_ms):
            # 字數或時間到上限：回頭找最後一個停頓標點（至少在 40% 之後）切開
            cut = None
            acc = 0.0
            for j, idx in enumerate(cur):
                acc += unit_weight(us[idx])
                if us[idx][-1:] in STRONG + WEAK and acc >= settings.max_chars * 0.4 and j < len(cur) - 1:
                    cut = j
            if cut is not None:
                groups.append(cur[:cut + 1])
                cur = cur[cut + 1:]
            else:
                groups.append(cur)
                cur = []
            weight = sum(unit_weight(us[k]) for k in cur)
        cur.append(i)
        weight += w
        if u[-1:] in STRONG and weight >= 4:
            groups.append(cur)
            cur, weight = [], 0.0
    if cur:
        groups.append(cur)
    cues: list[Cue] = []
    for gi, g in enumerate(groups):
        s = starts[g[0]]
        e = starts[groups[gi + 1][0]] if gi + 1 < len(groups) else seg_end
        txt = trim_cue_punct("".join(_join(us, g)))
        if txt:
            cues.append(Cue(s, max(e, s + 1), txt, precise))
    return cues


def _join(us: list[str], idxs: list[int]) -> list[str]:
    """英數詞之間、英文字與中文字之間留一個空白（數字與中文之間不留，例如「9點」）。"""
    out = []
    for k, i in enumerate(idxs):
        u = us[i]
        if k:
            prev = us[idxs[k - 1]]
            if (re.match(r"[A-Za-z0-9]", u) and re.search(r"[A-Za-z0-9]$", prev)) \
                    or (re.match(rf"[{CJK}]", u) and re.search(r"[A-Za-z]$", prev)) \
                    or (re.match(r"[A-Za-z]", u) and re.search(rf"[{CJK}]$", prev)):
                out.append(" ")
        out.append(u)
    return out


# ---------------------------------------------------------------- 繁體化與詞彙表 ----

class TextConverter:
    """OpenCC s2twp（簡體 → 台灣正體＋台灣用詞）＋保護詞＋修正表。沒有 OpenCC 時原樣輸出並標示。"""

    def __init__(self, s2tw: Optional[Callable[[str], str]], t2s: Optional[Callable[[str], str]], settings: Settings):
        self.s2tw = s2tw
        self.available = s2tw is not None
        self.fixes = [(a, b) for a, b in settings.fixes if a and a != b]
        # 保護詞被 s2twp 改成什麼樣子：先轉回簡體再轉一次，看結果是否不同
        self.restore: list[tuple[str, str]] = []
        if s2tw and t2s:
            for w in settings.protect:
                if w:
                    bad = s2tw(t2s(w))
                    if bad != w:
                        self.restore.append((bad, w))

    def convert(self, text: str) -> str:
        t = self.s2tw(text) if self.s2tw else text
        for bad, good in self.restore:
            t = t.replace(bad, good)
        for a, b in self.fixes:
            t = t.replace(a, b)
        return full_width_punct(t)


def load_opencc() -> tuple[Optional[Callable[[str], str]], Optional[Callable[[str], str]]]:
    try:
        import opencc  # type: ignore
    except ImportError:
        return None, None

    def make(names):
        for cfg in names:
            try:
                return opencc.OpenCC(cfg).convert
            except Exception:  # noqa: BLE001 — 不同套件版本的設定名稱不同
                continue
        return None
    return make(("s2twp", "s2twp.json")), make(("t2s", "t2s.json"))


# ---------------------------------------------------------------- 合併、整理、品質檢查 ----

def finalize(cues: list[Cue], duration_ms: Optional[int], settings: Settings) -> tuple[list[Cue], dict]:
    """排序、移除連續重複（3 次以上）、修正重疊、補足最短顯示時間；回傳（字幕, 統計）。"""
    stats = {"repeat_runs": 0, "repeat_removed": 0, "overlap_fixed": 0, "out_of_range": 0}
    cues = sorted((c for c in cues if c.text), key=lambda c: (c.start, c.end))
    out: list[Cue] = []
    run = 0
    for c in cues:
        if out and _core(c.text) == _core(out[-1].text):
            run += 1
            if run >= 2:                  # 第 3 次起同一句：視為失控，刪掉
                if run == 2:
                    stats["repeat_runs"] += 1
                stats["repeat_removed"] += 1
                out[-1].end = max(out[-1].end, c.end)
                continue
        else:
            run = 0
        out.append(Cue(c.start, c.end, c.text, c.precise))
    for i, c in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else None
        if duration_ms and c.end > duration_ms:
            stats["out_of_range"] += 1
            c.end = duration_ms
        if nxt is not None and c.end > nxt:
            stats["overlap_fixed"] += 1
            c.end = nxt
        if c.end - c.start < settings.min_ms:
            limit = nxt if nxt is not None else (duration_ms or c.start + settings.min_ms)
            c.end = max(c.end, min(c.start + settings.min_ms, limit))
        if c.end <= c.start:
            c.end = c.start + 1
    return out, stats


def quality(cues: list[Cue], seg_total: int, seg_empty: int, loops: int, stats: dict,
            speech_ms: int, duration_ms: Optional[int], converter_ok: bool, diag: Optional[dict] = None) -> dict:
    """品質檢查：needs_review 的原因都用白話寫；字幕仍會儲存，讓你人工確認。"""
    reasons, notes = [], []
    chars = (diag or {}).get("chars") or {}
    if chars.get("cues") is not None and chars.get("engine") is not None and chars["cues"] < chars["engine"]:
        reasons.append(f"斷句後比引擎輸出少了 {chars['engine'] - chars['cues']} 個字（字幕流程遺漏，請回報）")
    tail = (diag or {}).get("tail") or {}
    if duration_ms and tail.get("speech_end_ms") is not None and tail["speech_end_ms"] >= duration_ms - 300:
        notes.append("影片結束時仍在講話：最後一句可能在句子中間就結束了")
    if seg_total and seg_empty / seg_total > 0.3:
        reasons.append(f"{round(seg_empty / seg_total * 100)}% 的語音段落辨識不出文字（音量太小或不是人聲？）")
    if stats.get("repeat_runs"):
        reasons.append(f"有 {stats['repeat_runs']} 處同一句連續重複 3 次以上（已刪除多餘的 {stats['repeat_removed']} 條）")
    if loops:
        reasons.append(f"有 {loops} 處文字迴圈重複（模型失控，已縮短）")
    if duration_ms and duration_ms > 120000 and speech_ms < duration_ms * 0.05:
        reasons.append("整支影片偵測到的人聲不到 5%")
    if any(c.end <= c.start for c in cues):
        reasons.append("有字幕的結束時間不晚於開始時間")
    if any(cues[i].start < cues[i - 1].start for i in range(1, len(cues))):
        reasons.append("字幕時間倒退")
    if not converter_ok:
        reasons.append("沒有 OpenCC，字幕沒有轉成繁體")
    imprecise = sum(1 for c in cues if not c.precise)
    if cues and imprecise / len(cues) > 0.5:
        notes.append("時間軸大多依字數比例估算（模型沒有逐字時間戳），精度較低")
    if stats.get("overlap_fixed"):
        notes.append(f"修正 {stats['overlap_fixed']} 處時間重疊")
    return {"status": "needs_review" if reasons else "ok", "reasons": reasons, "notes": notes,
            "precise_ratio": round(1 - imprecise / len(cues), 3) if cues else None}


# ---------------------------------------------------------------- 評測：CER ----

def cer_normalize(text: str, to_same_script: Optional[Callable[[str], str]] = None) -> str:
    t = strip_tags(text)
    if to_same_script:
        t = to_same_script(t)
    return re.sub(rf"[^{CJK}A-Za-z0-9]", "", t).lower()


def cer(ref: str, hyp: str, to_same_script: Optional[Callable[[str], str]] = None) -> dict:
    """字元錯誤率：兩邊先統一字形、去標點與空白再比（規劃書 P-18）。"""
    r, h = cer_normalize(ref, to_same_script), cer_normalize(hyp, to_same_script)
    if not r:
        return {"cer": None, "ref_chars": 0, "edits": None}
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return {"cer": round(prev[-1] / len(r), 4), "ref_chars": len(r), "edits": prev[-1]}


def parse_glossary(raw: dict) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """server_config asr.glossary → (修正表, 保護詞, 熱詞)；格式不對的項目略過。"""
    raw = raw if isinstance(raw, dict) else {}
    fixes = [(str(p[0]), str(p[1])) for p in raw.get("fixes") or [] if isinstance(p, (list, tuple)) and len(p) == 2]
    protect = [str(w) for w in raw.get("protect") or [] if isinstance(w, str) and w.strip()]
    hot = [str(w) for w in raw.get("hotwords") or [] if isinstance(w, str) and w.strip()]
    return fixes, protect, hot


def to_vtt_lines(cues: Iterable[Cue]) -> list[tuple[int, int, str]]:
    return [(c.start, c.end, c.text) for c in cues]
