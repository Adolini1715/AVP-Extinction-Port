"""The launcher's background and the shortcut icon, from art captured off the player's own disc.

    launcher_art.py TITLE.bmp OUTDIR

TITLE: the title screen's 640 x 480 picture, written by avpe.exe with AVPE_PC_TITLEART
(platform/pc_titleart.cpp). From it: the art (the alien drawn as Leonardo's Vitruvian figure,
faded out above the logo, which is drawn over its lower part) and the logo (its own colours,
keyed off the dark rust art behind it). Writes:
  OUTDIR/background.bmp   2240 x 1400, the launcher's picture (layout of Launcher B, drawn at 2x)
  OUTDIR/avpe.ico         the wheel in the game's own colours, 16 to 256 px
Pure Python apart from Pillow, which the installer installs from a pinned wheel.
"""
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

K = 2                      # drawn at twice the 1120 x 700 design
W, H = 1120 * K, 700 * K
BG = (6, 8, 11)


def lift(img, bright=1.9, contrast=1.1):
    return ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(bright)).enhance(contrast)


def background(art, logo):
    canvas = Image.new("RGB", (W, H), BG)
    a = lift(art.convert("RGB"))
    a = a.resize((round(a.width * H / a.height), H), Image.LANCZOS)
    canvas.paste(a, (W + 60 * K - a.width, 0))
    # left to right: solid until 38%, 55% at 58%, clear from 80% (the mockup's gradient)
    shade = Image.new("L", (W, 1))
    for x in range(W):
        t = x / W
        if t < 0.38:
            v = 1.0
        elif t < 0.58:
            v = 1.0 - (t - 0.38) / 0.20 * 0.45
        elif t < 0.80:
            v = 0.55 * (1 - (t - 0.58) / 0.22)
        else:
            v = 0.0
        shade.putpixel((x, 0), int(v * 255 + 0.5))
    shade = shade.resize((W, H))
    bottom = Image.new("L", (1, H))
    for y in range(H):
        t = (y - (H - 140 * K)) / (140 * K)
        bottom.putpixel((0, y), int(max(0, min(1, t)) * 255 + 0.5))
    bottom = bottom.resize((W, H))
    mask = ImageChops.lighter(shade, bottom)
    canvas = Image.composite(Image.new("RGB", (W, H), BG), canvas, mask)
    lg = logo.convert("RGBA")
    lw = 440 * K
    lg = lg.resize((lw, round(lg.height * lw / lg.width)), Image.LANCZOS)
    canvas.paste(lg, (44 * K, 34 * K), lg)
    return canvas


def icon(art):
    """the wheel in the game's colours, lifted, in a rounded tile with a rust rim"""
    a = lift(art.convert("RGB"), 1.9, 1.15)
    # the wheel: a square around the figure, from the top of the ring
    side = min(a.width, int(a.height * 1.085))
    cx = a.width // 2
    box = (max(0, cx - side // 2), 0, max(0, cx - side // 2) + side, side)
    tile = a.crop(box).resize((512, 512), Image.LANCZOS).convert("RGBA")
    big = 2048
    r = int(512 * 0.19)
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, big - 1, big - 1), r * 4, fill=255)
    m = m.resize((512, 512), Image.LANCZOS)
    out = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    out.paste(tile, (0, 0), m)
    rim = Image.new("L", (big, big), 0)
    ImageDraw.Draw(rim).rounded_rectangle((0, 0, big - 1, big - 1), r * 4, outline=255, width=40)
    rim = rim.resize((512, 512), Image.LANCZOS)
    out.paste(Image.new("RGBA", (512, 512), (190, 120, 100, 255)), (0, 0), rim)
    return out


def cut(title):
    """the art and the logo out of the 640 x 480 title picture"""
    import numpy as np
    t = np.asarray(title.convert("RGB")).astype(np.float32)
    art = t[18:347, 222:587].copy()                           # the wheel and the figure, down to the logo
    y = np.arange(art.shape[0])[:, None, None] + 18
    art *= np.clip((347 - y) / 27.0, 0, 1)                    # faded out where the logo starts
    lg = t[340:425, 80:565]
    R, G, B = lg[..., 0], lg[..., 1], lg[..., 2]
    lum = np.maximum(np.maximum(R, G), B) / 255
    rust = np.clip((R - G - 8) / 30, 0, 1)                    # the art behind the letters: red over green
    alpha = np.clip((lum - 0.15) / 0.35, 0, 1) * (1 - rust)
    logo = np.dstack([lg, alpha * 255]).astype(np.uint8)
    return Image.fromarray(art.astype(np.uint8), "RGB"), Image.fromarray(logo, "RGBA")


def main():
    title_path, outdir = sys.argv[1:3]
    os.makedirs(outdir, exist_ok=True)
    art, logo = cut(Image.open(title_path))
    background(art, logo).save(os.path.join(outdir, "background.bmp"))
    ic = icon(art)
    ic.save(os.path.join(outdir, "avpe.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    ic.save(os.path.join(outdir, "icon.png"))
    print("launcher art written to " + outdir)


if __name__ == "__main__":
    main()
