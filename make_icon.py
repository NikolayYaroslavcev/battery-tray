"""Draws icon.ico / icon.png (build-time only: pip install pillow)."""
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
white, green = (245, 245, 247, 255), (48, 209, 88, 255)

d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=(28, 28, 30, 255))

# battery body + terminal
d.rounded_rectangle((150, 320, 790, 704), radius=90, outline=white, width=56)
d.rounded_rectangle((810, 430, 880, 594), radius=30, fill=white)

# three charge segments
for i in range(3):
    x0 = 230 + i * 180
    d.rounded_rectangle((x0, 400, x0 + 150, 624), radius=36, fill=green)

small = img.resize((256, 256), Image.LANCZOS)
small.save("icon.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
small.save("icon.png")
