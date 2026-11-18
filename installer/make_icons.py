"""Draws the app icon (a blue rounded tile with an open book and a
magnifier) and saves icon.png, icon.ico (Windows) and icon.icns (Mac)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=(12, 74, 160, 255))
# open book
d.polygon([(200, 330), (500, 390), (500, 790), (200, 730)], fill=(255, 255, 255, 255))
d.polygon([(524, 390), (824, 330), (824, 730), (524, 790)], fill=(232, 240, 252, 255))
for y in range(450, 700, 70):
    d.line([(250, y), (450, y + 35)], fill=(12, 74, 160, 255), width=18)
# magnifier
d.ellipse([560, 470, 800, 710], outline=(255, 196, 0, 255), width=44)
d.line([(770, 690), (880, 800)], fill=(255, 196, 0, 255), width=60)
img.save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out / "icon.icns")
print("icons written to", out)
