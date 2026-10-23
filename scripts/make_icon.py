"""python3 scripts/make_icon.py apps/client/assets：產生 iStudy 圖示：松綠玉印「無相」（直書）。輸出 PNG 512 與各尺寸，並組 Windows .ico。"""
import sys
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"

def make(size: int) -> Image.Image:
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 40
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=120, fill=(46, 94, 78, 255))
    inner = 92
    d.rounded_rectangle([inner, inner, S - inner, S - inner], radius=80, outline=(242, 246, 243, 150), width=14)
    try:
        font = ImageFont.truetype(FONT, 330, index=0)
    except OSError:
        font = ImageFont.load_default()
    for i, ch in enumerate("無相"):
        bbox = d.textbbox((0, 0), ch, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (S - w) / 2 - bbox[0]
        y = (S / 2 - 30 - h) if i == 0 else (S / 2 + 30)
        d.text((x, y - bbox[1]), ch, font=font, fill=(242, 246, 243, 255))
    return img.resize((size, size), Image.LANCZOS)

out = sys.argv[1]
big = make(512)
big.save(f"{out}/icon.png")
make(256).save(f"{out}/icon-256.png")
big.save(f"{out}/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
