"""Draw firmware/pinout.png: Raspberry Pi Pico (top view) with HydraFlasher's pins.

python3 tools/draw_pinout.py firmware/pinout.png   (needs Pillow and DejaVu fonts)"""
import sys

from PIL import Image, ImageDraw, ImageFont

out = sys.argv[1]
K = 3  # supersampling
W, H = 820, 760
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
f_label = ImageFont.truetype(BOLD, 15 * K)
f_num = ImageFont.truetype(BOLD, 13 * K)
f_gp = ImageFont.truetype(REG, 11 * K)
f_small = ImageFont.truetype(BOLD, 11 * K)

im = Image.new("RGBA", (W * K, H * K), (0, 0, 0, 0))
d = ImageDraw.Draw(im)


def s(*v):
    return [x * K for x in v]


# Board
BX0, BX1 = 330, 490
TOP, PITCH = 70, 31
BY0, BY1 = TOP - 40, TOP + 19 * PITCH + 40
d.rounded_rectangle(s(BX0, BY0, BX1, BY1), radius=10 * K, fill="#2e7d32")
# USB connector, BOOTSEL, RP2040, flash chip
d.rounded_rectangle(s(375, BY0 - 14, 445, BY0 + 34), radius=4 * K, fill="#cfd4d8", outline="#9aa0a6", width=2 * K)
d.text(s(410, BY0 + 46), "USB", font=f_small, fill="#e8f5e9", anchor="mm")
d.rounded_rectangle(s(422, 190, 462, 230), radius=6 * K, fill="#f5f5f5", outline="#bdbdbd", width=2 * K)
d.text(s(442, 243), "BOOTSEL", font=f_small, fill="#e8f5e9", anchor="mm")
d.rectangle(s(405, 330, 465, 390), fill="#263238")
d.text(s(435, 360), "RP2040", font=f_small, fill="#90a4ae", anchor="mm")
d.rectangle(s(425, 440, 455, 470), fill="#263238")

LEFT = {  # pin: (label, colour, GPIO)
    4: ("SCK", "#43a047", "GP2"),
    5: ("MOSI", "#43a047", "GP3"),
    6: ("MISO", "#43a047", "GP4"),
    7: ("CS0", "#1e88e5", "GP5"),
    9: ("CS1", "#1e88e5", "GP6"),
    10: ("CS2", "#1e88e5", "GP7"),
    11: ("CS3", "#1e88e5", "GP8"),
    12: ("CS4 (spare)", "#64b5f6", "GP9"),
    14: ("CS5 (spare)", "#64b5f6", "GP10"),
    15: ("LED busy", "#fb8c00", "GP11"),
    16: ("LED pass", "#2e7d32", "GP12"),
    17: ("LED fail", "#e53935", "GP13"),
}
LEFT_GND = {3, 8, 13, 18}
RIGHT = {36: ("3V3 (OUT)", "#e53935"), 38: ("GND", "#37474f")}
RIGHT_GND = {33, 28, 23}


def pin_y(pin):  # left row 1..20 top to bottom, right row 40..21
    i = pin - 1 if pin <= 20 else 40 - pin
    return TOP + i * PITCH


def pad(x, y):
    d.ellipse(s(x - 9, y - 9, x + 9, y + 9), fill="#d4a62a")
    d.ellipse(s(x - 4, y - 4, x + 4, y + 4), fill="#1b5e20")


def num_box(x, y, n):
    d.rounded_rectangle(s(x - 16, y - 11, x + 16, y + 11), radius=3 * K, fill="#cfd8dc")
    d.text(s(x, y), str(n), font=f_num, fill="#263238", anchor="mm")


def label(x1, y, text, colour, align_right):
    w = f_label.getlength(text) / K + 20
    x0 = x1 - w if align_right else x1
    xe = x1 if align_right else x1 + w
    d.rounded_rectangle(s(x0, y - 13, xe, y + 13), radius=4 * K, fill=colour)
    d.text(s((x0 + xe) / 2, y), text, font=f_label, fill="#ffffff", anchor="mm")


for pin in range(1, 41):
    y = pin_y(pin)
    left = pin <= 20
    px = BX0 + 14 if left else BX1 - 14
    pad(px, y)
    if left:
        if pin in LEFT:
            text, colour, gp = LEFT[pin]
            d.line(s(BX0 - 60, y, px, y), fill="#90a4ae", width=2 * K)
            num_box(BX0 - 44, y, pin)
            label(BX0 - 66, y, text, colour, align_right=True)
            d.text(s(px + 16, y), gp, font=f_gp, fill="#e8f5e9", anchor="lm")
        elif pin in LEFT_GND:
            d.line(s(BX0 - 30, y, px, y), fill="#90a4ae", width=2 * K)
            d.text(s(BX0 - 36, y), f"{pin}  GND", font=f_small, fill="#78909c", anchor="rm")
    else:
        if pin in RIGHT:
            text, colour = RIGHT[pin]
            d.line(s(px, y, BX1 + 60, y), fill="#90a4ae", width=2 * K)
            num_box(BX1 + 44, y, pin)
            label(BX1 + 66, y, text, colour, align_right=False)
        elif pin in RIGHT_GND:
            d.line(s(px, y, BX1 + 30, y), fill="#90a4ae", width=2 * K)
            d.text(s(BX1 + 36, y), f"GND  {pin}", font=f_small, fill="#78909c", anchor="lm")

# Corner pin numbers on the board, like the silkscreen
d.text(s(BX0 + 30, pin_y(1)), "1", font=f_small, fill="#e8f5e9", anchor="lm")
d.text(s(BX1 - 30, pin_y(40)), "40", font=f_small, fill="#e8f5e9", anchor="rm")
d.text(s(BX0 + 30, pin_y(20)), "20", font=f_small, fill="#e8f5e9", anchor="lm")
d.text(s(BX1 - 30, pin_y(21)), "21", font=f_small, fill="#e8f5e9", anchor="rm")

# Legend
ly = BY1 + 28
for i, (text, colour) in enumerate((("SPI (shared by all sockets)", "#43a047"),
                                    ("chip select, one per socket", "#1e88e5"),
                                    ("status LEDs", "#fb8c00"))):
    x = 120 + i * 220
    d.rounded_rectangle(s(x, ly - 7, x + 14, ly + 7), radius=3 * K, fill=colour)
    d.text(s(x + 22, ly), text, font=f_gp, fill="#78909c", anchor="lm")

im = im.resize((W, H), Image.LANCZOS)
# A 256-colour palette keeps the antialiased edges and is ~5x smaller than RGBA
im = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
im.save(out, optimize=True)
print(out, im.size)
