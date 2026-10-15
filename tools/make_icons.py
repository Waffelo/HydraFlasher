"""Make the app icons from logo-mark.png (the hydra in the window header).

python3 tools/make_icons.py   (needs Pillow; run from the repo root)

Writes packaging/icons/hydraflasher.ico (16-256 px, Windows .exe and installer),
packaging/icons/hydraflasher.png (256 px, Linux AppImage / desktop entry) and the
APP_ICON_PNG block in HydraFlasher.py (the window and taskbar icon)."""
import base64
import io
import os
import re

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "packaging", "icons")
mark = Image.open(os.path.join(ROOT, "logo-mark.png")).convert("RGBA")


def square(size: int) -> Image.Image:
    """The mark centred on a transparent square, scaled to fit (never enlarged
    beyond its own 240 px)."""
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((256 - mark.width) // 2, (256 - mark.height) // 2))
    return canvas if size == 256 else canvas.resize((size, size), Image.LANCZOS)


os.makedirs(OUT, exist_ok=True)
square(256).save(os.path.join(OUT, "hydraflasher.png"), optimize=True)
square(256).save(
    os.path.join(OUT, "hydraflasher.ico"),
    sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
)

# Window icon: several sizes, so the window manager picks a sharp one
entries = []
for size in (32, 64, 128):
    buf = io.BytesIO()
    square(size).save(buf, format="PNG", optimize=True)
    data = base64.b64encode(buf.getvalue()).decode()
    lines = "\n".join(f'        "{data[i:i + 76]}"' for i in range(0, len(data), 76))
    entries.append(f"    {size}: (\n{lines}\n    ),")
block = (
    "APP_ICON_PNG = {  # window/taskbar icon, from logo-mark.png (tools/make_icons.py)\n"
    + "\n".join(entries)
    + "\n}\n"
)
path = os.path.join(ROOT, "HydraFlasher.py")
src = open(path, encoding="utf-8").read()
pattern = re.compile(r"APP_ICON_PNG = \{.*?\n\}\n", re.S)
src = pattern.sub(lambda m: block, src) if pattern.search(src) else src.replace(
    "LOGO_PNG = {", block + "\n" + "LOGO_PNG = {", 1
)
open(path, "w", encoding="utf-8").write(src)
print("wrote", OUT, "and APP_ICON_PNG in HydraFlasher.py")
