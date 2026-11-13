#!/usr/bin/env python3
"""Sharp font pages for the PC build: the game's own letters, traced.

    trace_fonts.py MEM.BIN DUMPDIR OUTDIR [--scale 4] [--only PAGEHASH,...]

MEM.BIN: guest memory at a frame where the fonts are loaded (the probe
server's mem.bin). DUMPDIR: decoded textures from AVPE_PC_TEXDUMP (texNN_<res>
_fXX_WxH.ppm + texNN_<res>_alpha.pgm). For each CzFont object (vtable
0x207B14) the glyph records (+0x20, 20 bytes: +2 page, +6 advance, +0xA
height, +0xC x0, +0xE x1, +0x10 y0, +0x12 y1, sixteenths of a pixel with a +7
bias) give every glyph's box on its page; the page list (+0x1C) leads through
each page's sprite object (+4) to its texture, whose first word is the D3D
resource the renderer dumps by.

Each white glyph (the font's letters: the game tints them when it draws) is
traced: its alpha upsampled 8x (bicubic), cut at half coverage, outlined by
potrace (curves, corners kept) and rendered back at SCALE times the page with
antialiasing. Coloured glyphs (button icons, skulls) and everything outside
the glyph boxes (HUD art shares these pages) keep a bilinear upscale. The
button glyphs the prompt layer redraws (0xA9-0xB4, braces, 0x88, 0x99) are
left as they are; it draws over them.

Output: OUTDIR/<page hash>.bmp (32-bit with alpha, what pc_hd.cpp loads; the
hash is FNV-1a 64 over the decoded RGBA, as the renderer computes it) and
OUTDIR/preview_<hash>.png (original 4x nearest | traced, side by side).
"""
import argparse, glob, os, struct, subprocess, sys, tempfile
import numpy as np
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument('mem'); ap.add_argument('dump'); ap.add_argument('out')
ap.add_argument('--scale', type=int, default=4)
ap.add_argument('--only', default='')
args = ap.parse_args()
S = args.scale
os.makedirs(args.out, exist_ok=True)

m = open(args.mem, 'rb').read()
u32 = lambda a: struct.unpack_from('<I', m, a)[0]
u16 = lambda a: struct.unpack_from('<H', m, a)[0]

PROMPT_CODES = set(range(0xA9, 0xB5)) | {ord('{'), ord('}'), 0x88, 0x99}


def fnv(rgba):
    h = 1469598103934665603
    for b in rgba.tobytes():
        h = ((h ^ b) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def load_page(res):
    fs = glob.glob(os.path.join(args.dump, 'tex*_%08X_f*_*x*.ppm' % res))
    if not fs:
        return None
    f = fs[0]
    rgb = np.array(Image.open(f).convert('RGB'))
    a = np.array(Image.open(f.split('_f')[0] + '_alpha.pgm'))
    return np.dstack([rgb, a]).astype(np.uint8)


# ---- glyph boxes per page ----------------------------------------------------
pages = {}                                          # res -> list of (x0, y0, x1, y1, char code)
for f in range(0x265D60, len(m) - 4, 4):
    if u32(f) != 0x00207B14:
        continue
    recs, plist = u32(f + 0x20), u32(f + 0x1C)
    cmap = m[f + 0x24:f + 0x24 + 256]
    chars = {}
    for c in range(256):
        if cmap[c] != 0xFF:
            chars.setdefault(cmap[c], c)
    for g, c in chars.items():
        r = recs + 20 * g
        pg = u16(r + 2)
        spr = u32(plist + 4 * pg)
        if not (0x1000 < spr < 0x4000000):
            continue
        tex = u32(spr + 4)
        if not (0x1000 < tex < 0x4000000):
            continue
        res = u32(tex)
        x0, x1, y0, y1 = [(u16(r + o) - 7) // 16 for o in (0xC, 0xE, 0x10, 0x12)]
        if x1 > x0 and y1 > y0:
            pages.setdefault(res, set()).add((x0, y0, x1, y1, c))

print('fonts found; pages:', ', '.join('%08X (%d glyphs)' % (k, len(v)) for k, v in pages.items()))


def trace_cov(c):
    """c: float coverage crop (h, w) 0..1 -> traced coverage at S x, same box (potrace curves)."""
    h, w = c.shape
    U, pad = 8, 3
    # edge padding: a letter the box cuts (Zono's boxes clip some strokes) runs on past the edge
    # instead of getting an outline there
    big = Image.fromarray((np.pad(c, pad, mode='edge') * 255).astype(np.uint8)).resize(((w + 2 * pad) * U, (h + 2 * pad) * U), Image.BICUBIC)
    bw = (np.array(big) >= 128)
    if not bw.any():
        return np.zeros(((h + 2 * pad) * S, (w + 2 * pad) * S), np.float32), pad
    with tempfile.TemporaryDirectory() as td:
        pbm = os.path.join(td, 'g.pbm'); svg = os.path.join(td, 'g.svg'); png = os.path.join(td, 'g.png')
        Image.fromarray((~bw).astype(np.uint8) * 255).convert('1').save(pbm)
        subprocess.check_call(['potrace', '-s', '-t', '6', '-a', '1.0', '-O', '0.3', '-o', svg, pbm])
        ow, oh = (w + 2 * pad) * S, (h + 2 * pad) * S
        subprocess.check_call(['rsvg-convert', '-w', str(ow), '-h', str(oh), '-o', png, svg])
        r = np.array(Image.open(png).convert('RGBA')).astype(np.float32) / 255.0
    return r[..., 3], pad                                  # padded: the caller crops after building the rim


def glyph_hd(crop):
    """One glyph box (RGBA uint8) -> its S x redraw (float RGBA, straight alpha), or None for an icon.
    The letters are a bright fill (a white to pale cyan gradient) with a soft dark rim in the alpha:
    the fill is traced, the rim rebuilt around it at its measured width and strength, the gradient
    taken row by row from the original."""
    from scipy import ndimage
    A = crop[..., 3].astype(np.float32) / 255.0
    rgb = crop[..., :3].astype(np.float32)
    L = rgb.max(axis=2) / 255.0
    FA = L * A
    if (FA > 0.3).sum() < 3:
        return None
    peak = np.percentile(FA[FA > 0.3], 90)
    Fo = np.clip(FA / max(peak, 1e-3), 0, 1)
    core = Fo > 0.7
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[core]
    if core.sum() == 0 or sat.mean() > 70 or (rgb.max(axis=2)[core]).mean() < 140:
        return None                                       # coloured: an icon
    Fp, pad = trace_cov(Fo)
    h, w = A.shape
    # the rim: alpha outside the fill
    fillmask = Fo > 0.5
    dist = ndimage.distance_transform_edt(~np.pad(fillmask, 3, mode='edge'))[3:-3, 3:-3]
    rim = (~fillmask) & (A > 0.06) & (dist <= 3)
    if rim.sum() >= 3:
        ha = float(np.clip(A[rim & (dist <= 1.5)].mean() if (rim & (dist <= 1.5)).any() else A[rim].mean(), 0, 1))
        hw = float(max(0.75, np.mean(dist[rim]) - 0.25))    # the rim's typical reach (original pixels)
    else:
        ha, hw = 0.0, 0.0
    # gradient: the fill colour per row, interpolated between row centres
    rows = np.zeros((h, 3), np.float32); have = np.zeros(h, bool)
    for y in range(h):
        m_ = Fo[y] > 0.7
        if m_.any():
            rows[y] = rgb[y][m_].mean(axis=0); have[y] = True
    if not have.any():
        rows[:] = 255
    else:
        idx = np.where(have)[0]
        for k in range(3):
            rows[:, k] = np.interp(np.arange(h), idx, rows[idx, k])
        rows = ndimage.gaussian_filter1d(rows, 1.2, axis=0, mode='nearest')   # no row-to-row streaks
    yy = (np.arange(h * S) + 0.5) / S - 0.5
    col = np.stack([np.interp(yy, np.arange(h), rows[:, k]) for k in range(3)], axis=1)   # (h*S, 3)
    col = np.repeat(col[:, None, :], w * S, axis=1)
    # the rebuilt rim: coverage falling off over about a quarter pixel past its width
    if ha > 0 and hw > 0:
        dh = ndimage.distance_transform_edt(Fp < 0.5)        # on the padded trace: no rim along the box edges
        # a crisp outline: solid out to its measured reach, then a short edge (a third of an
        # original pixel), not a long fade (that read as a smudge at 1440p)
        Op = np.clip(((hw + 0.15) * S - dh) / (0.35 * S), 0, 1) * min(1.0, ha * 1.25)
    else:
        Op = np.zeros_like(Fp)
    cy, cx = slice(pad * S, pad * S + h * S), slice(pad * S, pad * S + w * S)
    F, O = Fp[cy, cx], Op[cy, cx]
    alpha = F + (1 - F) * O
    out = np.zeros((h * S, w * S, 4), np.float32)
    out[..., 3] = alpha * 255
    with np.errstate(invalid='ignore', divide='ignore'):
        k = np.where(alpha > 1e-4, F / alpha, 0)
    out[..., :3] = col * k[..., None]
    return out


for res, boxes in pages.items():
    page = load_page(res)
    if page is None:
        print('page %08X: not in the dump, skipped' % res)
        continue
    h0 = fnv(page)
    if args.only and ('%016x' % h0) not in args.only.split(','):
        continue
    H, W = page.shape[:2]
    base = np.array(Image.fromarray(page).resize((W * S, H * S), Image.BILINEAR)).astype(np.float32)
    out = base.copy()
    traced = skipped = 0
    for (x0, y0, x1, y1, c) in sorted(boxes):
        if c in PROMPT_CODES:
            skipped += 1; continue
        hd = glyph_hd(page[y0:y1, x0:x1])
        if hd is None:
            skipped += 1; continue
        out[y0 * S:y1 * S, x0 * S:x1 * S] = hd
        traced += 1
    img = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    name = '%016x' % h0
    # 32-bit BMP with an alpha mask (BITMAPV5HEADER), bottom-up BGRA: SDL_LoadBMP keeps alpha
    hh, ww = img.shape[:2]
    bgra = img[::-1, :, [2, 1, 0, 3]].tobytes()
    v5 = struct.pack('<IiiHHIIiiII', 124, ww, hh, 1, 32, 3, len(bgra), 2835, 2835, 0, 0)
    v5 += struct.pack('<IIII', 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000) + b'BGRs' + b'\0' * 36 + struct.pack('<IIII', 0, 0, 0, 0) + struct.pack('<IIII', 4, 0, 0, 0)[:16]
    v5 = v5[:124]
    with open(os.path.join(args.out, name + '.bmp'), 'wb') as f:
        f.write(b'BM' + struct.pack('<IHHI', 14 + 124 + len(bgra), 0, 0, 14 + 124) + v5 + bgra)
    # preview: alpha of the original (nearest 4x) next to the traced alpha
    orig = np.array(Image.fromarray(page[..., 3]).resize((W * S, H * S), Image.NEAREST))
    orgb = np.array(Image.fromarray(page).resize((W * S, H * S), Image.NEAREST)).astype(np.float32)
    def over_grey(x):
        a = x[..., 3:4] / 255.0
        return (x[..., :3] * a + 110 * (1 - a)).astype(np.uint8)
    Image.fromarray(np.hstack([over_grey(orgb), over_grey(img.astype(np.float32))])).save(os.path.join(args.out, 'preview_%s.png' % name))
    print('page %08X hash %s: %d glyphs traced, %d left (icons, prompt glyphs)' % (res, name, traced, skipped))
