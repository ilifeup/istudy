"""Office → PDF（第 12 章）：LibreOffice headless，獨立的使用者設定目錄，一次一份，逾時 5 分鐘中止。

dev.60：
  - 簡報明確指定 Impress 的 PDF 匯出選項：不輸出備註頁、不輸出隱藏投影片（不依賴設定目錄裡的預設值）。
  - 設定目錄預先寫入「亞洲文字預設語言＝zh-TW」：沒有指定字型的中文用繁體中文字形（Noto Sans CJK TC），不會用簡體字形。
  - 轉完記錄 PDF 頁數；簡報與「有效投影片數」比對；Excel 讀原檔的版面設定（紙張、方向、縮放、調整成一頁寬、列印範圍、隱藏工作表），
    原檔設定會讓表格分頁時寫在結果裡（不改原檔、不改紙張或縮放、不裁切內容）。
"""
from __future__ import annotations

import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

from .context import Ctx, JobError

TIMEOUT = 300
IMPRESS_EXT = {"ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp"}
# 簡報 → PDF：只輸出投影片本身（不含備註頁、講義、隱藏投影片）
IMPRESS_PDF = {"ExportNotesPages": False, "ExportOnlyNotesPages": False, "ExportHiddenSlides": False}
PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Linguistic/General"><prop oor:name="DefaultLocale_CJK" oor:op="fuse"><value>zh-TW</value></prop></item>
</oor:items>
"""
MAX_XML = 64 * 1024 * 1024   # 讀 OOXML 版面資訊時，單一 XML 超過這個大小就不讀（避免異常檔案耗盡記憶體）


def _soffice() -> str:
    for exe in ("soffice", "libreoffice"):
        p = shutil.which(exe)
        if p:
            return p
    raise JobError("agent 映像沒有安裝 LibreOffice")


def convert_arg(ext: str) -> str:
    """--convert-to 的參數：簡報明確帶 Impress 匯出選項；其他文件用 LibreOffice 預設（依原檔的頁面設定）。"""
    if ext.lower() in IMPRESS_EXT:
        opts = {k: {"type": "boolean", "value": "true" if v else "false"} for k, v in IMPRESS_PDF.items()}
        return "pdf:impress_pdf_Export:" + json.dumps(opts, separators=(",", ":"))
    return "pdf"


def seed_profile(profile_dir: str) -> None:
    d = os.path.join(profile_dir, "user")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "registrymodifications.xcu")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(PROFILE_XCU)


def convert_to_pdf(src: str, out_dir: str, profile_dir: str, timeout: int = TIMEOUT) -> str:
    seed_profile(profile_dir)
    ext = os.path.splitext(src)[1].lstrip(".")
    cmd = [_soffice(), "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
           f"-env:UserInstallation=file://{profile_dir}", "--convert-to", convert_arg(ext), "--outdir", out_dir, src]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace",
                           env={**os.environ, "HOME": profile_dir})
    except subprocess.TimeoutExpired as e:
        raise JobError("文件轉換逾時（超過 5 分鐘）") from e
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if p.returncode != 0 or not os.path.exists(pdf) or os.path.getsize(pdf) == 0:
        raise JobError("文件轉換失敗：" + ((p.stderr or p.stdout).strip().splitlines() or ["未知錯誤"])[-1][:300])
    return pdf


# ---------- 轉換後的檢查（只讀原檔與 PDF，不修改） ----------

def pdf_pages(path: str) -> Optional[int]:
    """LibreOffice 產生的 PDF 頁數（頁面物件沒有壓縮，直接數 /Type /Page）。"""
    try:
        with open(path, "rb") as f:
            n = len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", f.read()))
        return n or None
    except OSError:
        return None


def _xml(z: zipfile.ZipFile, name: str) -> Optional[ET.Element]:
    try:
        info = z.getinfo(name)
        if info.file_size > MAX_XML:
            return None
        return ET.fromstring(z.read(name))
    except (KeyError, ET.ParseError):
        return None


def _rels(z: zipfile.ZipFile, base: str) -> dict:
    """base（例如 ppt/presentation.xml）的關聯：rId → 壓縮檔內的路徑。"""
    d, f = posixpath.split(base)
    root = _xml(z, posixpath.join(d, "_rels", f + ".rels"))
    out = {}
    for r in (root if root is not None else []):
        t = r.get("Target") or ""
        out[r.get("Id")] = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(d, t))
    return out


NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def pptx_slides(path: str) -> Optional[tuple[int, int]]:
    """(投影片總數, 隱藏張數)；不是 OOXML 簡報（例如舊版 .ppt）時回傳 None。"""
    try:
        with zipfile.ZipFile(path) as z:
            pres = _xml(z, "ppt/presentation.xml")
            if pres is None:
                return None
            rels = _rels(z, "ppt/presentation.xml")
            ids = [e.get(NS_R + "id") for e in pres.iter(NS_P + "sldId")]
            hidden = 0
            for rid in ids:
                sl = _xml(z, rels.get(rid, ""))
                if sl is not None and sl.get("show") in ("0", "false"):
                    hidden += 1
            return len(ids), hidden
    except (zipfile.BadZipFile, OSError):
        return None


PAPER = {1: ("Letter", 612, 792), 5: ("Legal", 612, 1008), 8: ("A3", 842, 1191), 9: ("A4", 595, 842), 11: ("A5", 420, 595),
         13: ("B5", 516, 729)}
MDW = 7   # Excel 預設字型（Calibri 11）的數字寬度（像素）；欄寬單位＝這個寬度的字元數


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref.upper()).group(0):
        n = n * 26 + ord(ch) - 64
    return n


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def xlsx_layout_notes(path: str) -> list[str]:
    """讀 Excel 原檔的列印設定，找出會讓 PDF 分頁或只轉出部分內容的設定（不判斷內容對錯）。"""
    notes: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            wb = _xml(z, "xl/workbook.xml")
            if wb is None:
                return notes
            rels = _rels(z, "xl/workbook.xml")
            areas = {}
            for dn in wb.iter(NS_S + "definedName"):
                if dn.get("name") == "_xlnm.Print_Area" and dn.get("localSheetId") is not None:
                    areas[int(dn.get("localSheetId"))] = (dn.text or "").split("!")[-1].replace("$", "")
            for i, sh in enumerate(wb.iter(NS_S + "sheet")):
                name = sh.get("name") or f"第 {i + 1} 個工作表"
                if sh.get("state") in ("hidden", "veryHidden"):
                    notes.append(f"工作表「{name}」是隱藏的：不會轉出（與 Excel 列印相同）")
                    continue
                ws = _xml(z, rels.get(sh.get(NS_R + "id"), ""))
                if ws is None:
                    continue
                if i in areas:
                    notes.append(f"工作表「{name}」設定了列印範圍 {areas[i]}：只轉出這個範圍")
                    continue
                dim = ws.find(NS_S + "dimension")
                ref = (dim.get("ref") if dim is not None else "A1") or "A1"
                last = _col_index(ref.split(":")[-1])
                fmt = ws.find(NS_S + "sheetFormatPr")
                default_w = _num(fmt.get("defaultColWidth") if fmt is not None else None,
                                 _num(fmt.get("baseColWidth") if fmt is not None else None, 8) + 0.71)
                widths = {}
                cols = ws.find(NS_S + "cols")
                for c in (cols if cols is not None else []):
                    for k in range(int(c.get("min", 1)), min(int(c.get("max", 1)), last) + 1):
                        widths[k] = 0.0 if c.get("hidden") in ("1", "true") else _num(c.get("width"), default_w)
                width_pt = sum(widths.get(k, default_w) * MDW * 0.75 for k in range(1, last + 1))
                ps = ws.find(NS_S + "pageSetup")
                ps = ps if ps is not None else ET.Element("pageSetup")
                pr = ws.find(f"{NS_S}sheetPr/{NS_S}pageSetUpPr")
                fit = pr is not None and pr.get("fitToPage") in ("1", "true")
                fit_w = int(_num(ps.get("fitToWidth"), 1))
                if fit and fit_w >= 1:
                    continue      # 原檔已設定「調整成 N 頁寬」：LibreOffice 會照著縮放
                paper, pw, ph = PAPER.get(int(_num(ps.get("paperSize"), 9)), PAPER[9])
                land = ps.get("orientation") == "landscape"
                mg = ws.find(NS_S + "pageMargins")
                margin = (_num(mg.get("left"), 0.7) + _num(mg.get("right"), 0.7)) * 72 if mg is not None else 1.4 * 72
                printable = (ph if land else pw) - margin
                scale = _num(ps.get("scale"), 100)
                pages = math.ceil(width_pt * scale / 100 / max(printable, 1) - 0.02)
                if pages > 1:
                    notes.append(f"工作表「{name}」：表格寬度約 {pages} 頁（{paper} {'橫式' if land else '直式'}、縮放 {scale:g}%，"
                                 "原檔沒有設定「調整成一頁寬」），右邊的欄位會分到後面的頁面；Excel 列印也一樣。"
                                 "要一頁寬請在 Excel「版面配置 → 調整成符合大小 → 寬度 1 頁」後重新放入")
    except (zipfile.BadZipFile, OSError, ValueError):
        pass
    return notes


def layout_check(src: str, ext: str, pdf: str) -> dict:
    """轉換結果的頁數與說明（寫在工作結果，Server App「文件轉換」顯示）。"""
    pages = pdf_pages(pdf)
    notes: list[str] = []
    ext = ext.lower()
    if ext in ("pptx", "ppsx", "potx"):
        sl = pptx_slides(src)
        if sl:
            total, hidden = sl
            if hidden:
                notes.append(f"{total} 張投影片中有 {hidden} 張隱藏：不轉出（與 PowerPoint 投影片放映相同）")
            if pages is not None and pages != total - hidden:
                notes.append(f"⚠ PDF 有 {pages} 頁，但有效投影片是 {total - hidden} 張：請檢查原檔")
    elif ext in ("xlsx", "xlsm"):
        notes += xlsx_layout_notes(src)
    return {"pages": pages, "notes": notes}


def office_pdf(ctx: Ctx, job: dict) -> dict:
    mid = int(job["payload"]["media_id"])
    m = ctx.conn.one("SELECT * FROM media WHERE id = %s", [mid])
    if not m or m["kind"] != "office":
        raise JobError("找不到 Office 文件")
    src = ctx.paths.original(m["rel_path"])
    if not os.path.exists(src):
        ctx.conn.execute("UPDATE media SET doc_status = 'failed', doc_error = %s WHERE id = %s", ["原檔不存在", mid])
        raise JobError("原檔不存在")
    st = os.stat(src)
    sig = f"{st.st_size}:{int(st.st_mtime)}"
    dst = ctx.paths.doc_file(mid)
    # 快取以教材 ID＋原檔大小＋修改時間判斷
    if not job["payload"].get("force") and m["doc_status"] == "done" and m["doc_source_sig"] == sig and os.path.exists(dst):
        return {"skipped": "快取仍有效"}
    ctx.conn.execute("UPDATE media SET doc_status = 'converting', doc_error = NULL WHERE id = %s", [mid])
    work = tempfile.mkdtemp(prefix=f"istudy-office-{mid}-")
    try:
        profile = os.path.join(work, "profile")
        out = os.path.join(work, "out")
        os.makedirs(profile)
        os.makedirs(out)
        # 複製到暫存目錄再轉換（避免中文、空白檔名與唯讀來源造成問題）
        local = os.path.join(work, f"source.{m['ext']}")
        shutil.copy2(src, local)
        try:
            pdf = convert_to_pdf(local, out, profile)
        except JobError as e:
            ctx.conn.execute("UPDATE media SET doc_status = 'failed', doc_error = %s WHERE id = %s", [str(e), mid])
            raise
        check = layout_check(local, m["ext"], pdf)
        os.makedirs(ctx.paths.doc_cache, exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copyfile(pdf, tmp)
        os.replace(tmp, dst)
        size = os.path.getsize(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ctx.conn.execute("UPDATE media SET doc_status = 'done', doc_pdf_path = %s, doc_source_sig = %s, doc_error = NULL, updated_at = NOW() "
                     "WHERE id = %s", [os.path.basename(dst), sig, mid])
    return {"pdf_size": size, **check}
