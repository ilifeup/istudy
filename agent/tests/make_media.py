"""產生測試教材（以 ffmpeg 與 LibreOffice 建立真實格式的小檔案）。

用法：python3 tests/make_media.py /tmp/istudy-media
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

FF = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]


def ff(args: list[str]) -> None:
    subprocess.run(FF + args, check=True)


def mts_interlaced_ac3(path: str, seconds: int, freq: int) -> None:
    """模擬攝影機 AVCHD：MPEG-TS、H.264 交錯（TFF）、AC-3 音訊。"""
    ff(["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}", "-f", "lavfi",
        "-i", f"sine=frequency={freq}:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-flags", "+ildct+ilme", "-x264opts", "tff=1", "-pix_fmt", "yuv420p",
        "-c:a", "ac3", "-b:a", "192k", "-f", "mpegts", path])


def mp4(path: str, seconds: int, faststart: bool) -> None:
    args = ["-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={seconds}", "-f", "lavfi",
            "-i", f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k"]
    if faststart:
        args += ["-movflags", "+faststart"]
    ff(args + [path])


def avi_mpeg4(path: str, seconds: int) -> None:
    ff(["-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}", "-f", "lavfi",
        "-i", f"sine=frequency=550:duration={seconds}", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "pcm_s16le", path])


def pdf(path: str) -> None:
    # 最小的有效 PDF（一頁）
    content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" \
              b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\nxref\n0 4\n0000000000 65535 f \n" \
              b"0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n159\n%%EOF\n"
    with open(path, "wb") as f:
        f.write(content)


def docx(path: str) -> None:
    work = tempfile.mkdtemp()
    try:
        src = os.path.join(work, "src.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("第一堂課講義\n練習氣功時，先讓身體自然放鬆。\n")
        prof = os.path.join(work, "p")
        subprocess.run(["soffice", "--headless", "--norestore", f"-env:UserInstallation=file://{prof}", "--convert-to", "docx",
                        "--outdir", work, src], check=True, capture_output=True, timeout=180, env={**os.environ, "HOME": prof})
        shutil.copy(os.path.join(work, "src.docx"), path)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def png(path: str) -> None:
    ff(["-f", "lavfi", "-i", "testsrc=size=64x64:duration=1", "-frames:v", "1", path])


def office_demo(root: str) -> list[str]:
    """dev.60 畫面測試用：/Media/OfficeDemo 的繁體中文 Excel 與 16:9 PowerPoint（需要 openpyxl／python-pptx；沒有就略過）。
    D02.xlsx：第 1 個工作表直式 A4、沒有設定縮放而且比一頁寬（列印會分頁）；第 2 個工作表橫式＋調整成一頁寬。
    D03.pptx：4 張 16:9 投影片（含講者備註），第 3 張隱藏。"""
    d = os.path.join(root, "OfficeDemo")
    os.makedirs(d, exist_ok=True)
    made = []
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = "課程表"
        heads = ["編號", "課程名稱", "日期", "時間", "地點", "講師", "助教", "人數", "費用", "備註", "教材", "影片", "狀態", "聯絡人"]
        for c, h in enumerate(heads, 1):
            ws.cell(row=1, column=c, value=h)
        for r in range(2, 30):
            for c, v in enumerate([r - 1, f"站樁與呼吸練習第{r - 1}堂", "2026/10/21", "19:00-21:00", "台北教室", "陳鴻文", "助教甲", 20,
                                   1200, "請提前十分鐘到場", "講義第三章", "V01.mp4", "已開課", "02-2345-6789"], 1):
                ws.cell(row=r, column=c, value=v)
        for i, w in enumerate([6, 24, 12, 12, 12, 8, 8, 6, 8, 20, 12, 10, 8, 14], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws2 = wb.create_sheet("一頁寬")
        for r in range(1, 40):
            for c in range(1, 13):
                ws2.cell(row=r, column=c, value=f"第{r}列 第{c}欄")
        ws2.page_setup.orientation = "landscape"
        ws2.sheet_properties.pageSetUpPr.fitToPage = True
        ws2.page_setup.fitToWidth = 1
        ws2.page_setup.fitToHeight = 0
        wb.save(os.path.join(d, "D02.xlsx"))
        made.append("D02.xlsx")
    except ImportError:
        pass
    try:
        from pptx import Presentation
        from pptx.util import Inches
        p = Presentation()
        p.slide_width, p.slide_height = Inches(13.333), Inches(7.5)
        for i in range(4):
            s = p.slides.add_slide(p.slide_layouts[1])
            s.shapes.title.text = f"第 {i + 1} 張：無相氣學 複習"
            s.placeholders[1].text = "站樁要點：放鬆、呼吸自然"
            s.notes_slide.notes_text_frame.text = f"講者備註 {i + 1}"
        p.slides[2]._element.set("show", "0")
        p.save(os.path.join(d, "D03.pptx"))
        made.append("D03.pptx")
    except ImportError:
        pass
    return made


def build(root: str) -> dict:
    videos = root          # dev.59：教材直接放在 /Media 的課程資料夾（第一層系統資料夾除外）
    for d in ("playback", "subtitles", "doc-cache"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    c1 = os.path.join(videos, "基礎班", "第一堂課")
    c2 = os.path.join(videos, "進修班")
    c3 = os.path.join(videos, "砭石")
    for d in (c1, c2, c3, os.path.join(videos, ".hidden")):
        os.makedirs(d, exist_ok=True)
    mts_interlaced_ac3(os.path.join(c1, "00019.MTS"), 4, 440)
    mts_interlaced_ac3(os.path.join(c1, "00020.MTS"), 4, 440)
    mts_interlaced_ac3(os.path.join(c1, "00021.MTS"), 3, 440)
    mp4(os.path.join(c1, "0001_上集.mp4"), 3, True)
    mp4(os.path.join(c1, "0002 中集.mp4"), 3, False)
    pdf(os.path.join(c1, "講義.pdf"))
    docx(os.path.join(c1, "課程表.docx"))
    with open(os.path.join(c1, "說明.txt"), "wb") as f:
        f.write("這是 Big5 編碼的說明文字。".encode("big5"))
    png(os.path.join(c1, "示意圖.png"))
    with open(os.path.join(c1, "notes.xyz"), "w") as f:
        f.write("unsupported")
    with open(os.path.join(c1, "Thumbs.db"), "w") as f:
        f.write("junk")
    avi_mpeg4(os.path.join(c2, "示範 a.avi"), 3)
    pdf(os.path.join(c3, "砭石講義.pdf"))
    pdf(os.path.join(videos, "根目錄.pdf"))
    pdf(os.path.join(videos, ".hidden", "secret.pdf"))
    try:
        os.symlink(os.path.join(c1, "講義.pdf"), os.path.join(c2, "連結.pdf"))
    except OSError:
        pass
    return {"root": root}


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/istudy-media"))


def host_config(root_dir: str) -> str:
    """dev.62：模擬 Agent 容器的 /host-config（.env、compose.yaml、VERSION.json）；方案 B 備份一定要有這些設定檔。"""
    import shutil as _sh
    d = os.path.join(root_dir, "host-config")
    os.makedirs(d, exist_ok=True)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
        f.write("DB_PASSWORD=test-only\nISTUDY_VERSION=test\n")
    for name in ("compose.yaml", "VERSION.json"):
        _sh.copy(os.path.join(repo, name), os.path.join(d, name))
    return d
