#!/usr/bin/env python3
"""Sharp font pages for the PC build: the game's letters replaced by a matching typeface.

    replace_fonts.py MEM.BIN DUMPDIR OUTDIR [--scale 4] [--sheet]

MEM.BIN: guest memory at a frame where the fonts are loaded (the probe
server's mem.bin); DUMPDIR: decoded textures from AVPE_PC_TEXDUMP. The CzFont
objects (vtable 0x207B14) give every glyph's box on its page (records at +0x20,
20 bytes: +2 page, +6 advance, +0xA height, +0xC x0 +0xE x1 +0x10 y0 +0x12 y1,
in sixteenths with a +7 bias; pages through +0x1C -> sprite +4 -> texture, whose
first word is the D3D resource the renderer dumps by).

Each letter is drawn new from a vector font into its own box, so the game's
layout and spacing are unchanged:
  - one scale per font, from the original's cap height (its H), the baseline
    where the original H stands; strokes thickened by the difference when the
    original's are heavier (the H's stem against its cap height);
  - the width of the original letter's ink, over the same columns (measured
    from the pixels: getbbox counts the side bearings);
  - the original's fill colours (white, or the big font's white to pale cyan
    gradient, row by row) and its dark rim as a thin crisp outline;
  - drawn at 16x the page and filtered down to SCALE x.
Glyphs that are art (coloured icons) and the button glyphs the prompt layer
redraws stay as they are (bilinear). Typefaces: DejaVu Sans Bold for the main
text font (wide, heavy, big x-height, as Zono's), Barlow Condensed ExtraBold (OFL; its stem and width match the original's H within 3%)
for the big condensed menu font and the capitals font; FONT_DIR has the
converted Barlow files (fontsource woff2 -> ttf).

Even spacing (--respace, on by default) for the main text font: the original's
bitmap letters sit 0, 1 or 2 pixels apart depending on where each one's ink
fell in its cell ("Bra vo"), which reads as uneven at 1440p. Each letter gets
the same gap G (the original's mean, so line lengths and word wrap stay as
they were): advance = round(ink + G) whole pixels (the game's word wrap
truncates advances to whole pixels, so they stay whole), the letter drawn
G/2 into its cell at the width advance - G (within half a pixel of the
original ink). Its box on the page grows to the new advance into the empty
space after it. The metric changes (advance, box) and a taller line go to
--header for pc_text.cpp to apply to the font's glyph records in memory.

Output: OUTDIR/<page hash>.bmp (pc_hd.cpp; the whole page, for local use), <page hash>.ovl.bmp
(the overlay that ships: our letters only, the rest keyed to the player's own page) and, with
--sheet, OUTDIR/sheet_<hash>.png.
"""
import argparse, glob, os, struct, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ap = argparse.ArgumentParser()
ap.add_argument('mem'); ap.add_argument('dump'); ap.add_argument('out')
ap.add_argument('--scale', type=int, default=4)
ap.add_argument('--fontdir', default='/home/claude/fonts_npm/ttf')
ap.add_argument('--sheet', action='store_true')
ap.add_argument('--respace', type=int, default=1, help='even letter spacing for the main text font (1, default) or the original spacing (0)')
ap.add_argument('--lineh', type=int, default=15, help='line height for the main text font when respaced (the original is 13)')
ap.add_argument('--header', default='', help='write the metric changes as a C header (pc_font_metrics.h)')
args = ap.parse_args()
S, R = args.scale, 4                                    # page scale; supersampling over it
os.makedirs(args.out, exist_ok=True)

DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
BARLOW = [os.path.join(args.fontdir, 'barlow-condensed-latin-800-normal.ttf'),
          os.path.join(args.fontdir, 'barlow-condensed-latin-ext-800-normal.ttf'), DEJAVU]
PROMPT_CODES = set(range(0xA9, 0xB5)) | {ord('{'), ord('}'), 0x88, 0x99}

m = open(args.mem, 'rb').read()
u32 = lambda a: struct.unpack_from('<I', m, a)[0]
u16 = lambda a: struct.unpack_from('<H', m, a)[0]


def fnv(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


page_cache = {}
def page(res):
    if res not in page_cache:
        fs = glob.glob(os.path.join(args.dump, 'tex*_%08X_f*_*x*.ppm' % res))
        page_cache[res] = None if not fs else np.dstack([
            np.array(Image.open(fs[0]).convert('RGB')),
            np.array(Image.open(fs[0].split('_f')[0] + '_alpha.pgm'))]).astype(np.uint8)
    return page_cache[res]


# ---- the fonts and their glyphs ----------------------------------------------------
fonts = []
for f in range(0x265D60, len(m) - 4, 4):
    if u32(f) != 0x00207B14:
        continue
    recs, plist = u32(f + 0x20), u32(f + 0x1C)
    cmap = m[f + 0x24:f + 0x24 + 256]
    glyphs = {}
    for c in range(256):
        g = cmap[c]
        if g == 0xFF or g in glyphs:
            continue
        r = recs + 20 * g
        spr = u32(plist + 4 * u16(r + 2))
        if not (0x1000 < spr < 0x4000000) or not (0x1000 < u32(spr + 4) < 0x4000000):
            continue
        res = u32(u32(spr + 4))
        x0, x1, y0, y1 = [(u16(r + o) - 7) // 16 for o in (0xC, 0xE, 0x10, 0x12)]
        if x1 > x0 and y1 > y0:
            glyphs[g] = (c, res, x0, y0, x1, y1)
    n = len(set(c for c in cmap if c != 0xFF))
    fonts.append((f, n, glyphs, recs, bytes(cmap)))
    print('font %08X: %d glyphs' % (f, n))


def typeface_for(nglyphs):
    """212 glyphs: the main text font; 127 and 46: the big condensed font and its capitals."""
    if nglyphs >= 200:
        return [DEJAVU]
    if nglyphs in (127, 46):
        return BARLOW
    return None                                         # front-end fonts: not matched yet


def coverage(crop):
    """fill coverage of an original glyph: brightness times alpha, normalised"""
    A = crop[..., 3].astype(np.float32) / 255
    L = crop[..., :3].max(axis=2).astype(np.float32) / 255
    FA = L * A
    if (FA > 0.3).sum() < 2:
        return None
    return np.clip(FA / max(np.percentile(FA[FA > 0.3], 90), 1e-3), 0, 1)


def is_art(crop, Fo):
    rgb = crop[..., :3].astype(np.float32)
    core = Fo > 0.7
    if core.sum() == 0:
        return True
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[core]
    return sat.mean() > 70 or rgb.max(axis=2)[core].mean() < 140


def font_metrics(glyphs):
    """cap height and baseline row of the original (from its H), the rim strength"""
    capH = base = None
    for g, (c, res, x0, y0, x1, y1) in glyphs.items():
        if c == ord('H') and page(res) is not None:
            Fo = coverage(page(res)[y0:y1, x0:x1])
            rows = np.where((Fo > 0.5).any(axis=1))[0]
            capH, base = rows[-1] - rows[0] + 1, rows[-1] + 1     # base: the row under the ink
    ha = []
    for g, (c, res, x0, y0, x1, y1) in list(glyphs.items())[:80]:
        p = page(res)
        if p is None or c in PROMPT_CODES:
            continue
        crop = p[y0:y1, x0:x1]
        Fo = coverage(crop)
        if Fo is None or is_art(crop, Fo):
            continue
        A = crop[..., 3] / 255
        ring = (ndimage.binary_dilation(Fo > 0.5) & ~(Fo > 0.5) & (A > 0.05))
        if ring.any():
            ha.append(A[ring].mean())
    return capH, base, (float(np.median(ha)) if ha else 0.0)


_cmaps = {}
def cmap_of(path):
    if path not in _cmaps:
        from fontTools.ttLib import TTFont
        _cmaps[path] = set(TTFont(path).getBestCmap().keys())
    return _cmaps[path]


def stem_ratio(cov):
    """H: the left stem's width (coverage sum on an upper row) over the cap height"""
    rows = np.where((cov > 0.5).any(axis=1))[0]
    cap = rows[-1] - rows[0] + 1
    s, started = 0.0, False
    for v in cov[rows[0] + cap // 4]:
        if v > 0.05: s += min(v, 1.0); started = True
        elif started: break
    return s / cap


_face_stem = {}
def face_stem(path):
    if path not in _face_stem:
        fo = ImageFont.truetype(path, 400); bb = fo.getbbox('H')
        im = Image.new('L', (bb[2] + 20, bb[3] + 20), 0); ImageDraw.Draw(im).text((10, 10), 'H', font=fo, fill=255)
        _face_stem[path] = stem_ratio(np.array(im).astype(np.float32) / 255)
    return _face_stem[path]


def draw_char(ch, faces, px_cap, box_w, box_h, ink_cx, base_row, ink_w, rows_rgb, rim_a, rim_w, orig_stem):
    """the letter at R*S x the box: (h, w, 4) float RGBA, straight alpha, then filtered to S x"""
    K = S * R
    path = next((pth for pth in faces if ord(ch) in cmap_of(pth)), None)
    if path is None:
        return None
    fo = ImageFont.truetype(path, 400)
    bbH = fo.getbbox('H', anchor='ls'); size = 400 * px_cap * K / (bbH[3] - bbH[1])
    fo = ImageFont.truetype(path, max(4, int(round(size))))
    # the original's weight: heavier strokes than the typeface's get the difference as a stroke
    sw = max(0, int(round((orig_stem - face_stem(path)) / 2 * px_cap * K))) if orig_stem else 0
    # drawn on its baseline (anchor ls) in a roomy canvas; the ink measured from the pixels
    # (getbbox includes the side bearings, which made every letter too narrow)
    pad = 6 * K
    size_px = int(fo.size)
    im = Image.new('L', (size_px * 2 + 2 * pad, size_px * 2 + 2 * pad), 0)
    base_y = size_px + pad
    ImageDraw.Draw(im).text((pad, base_y), ch, font=fo, fill=255, anchor='ls', stroke_width=sw, stroke_fill=255)
    a = np.array(im)
    cols = np.where((a > 127).any(axis=0))[0]
    if not len(cols):
        return None
    ix0, ix1 = cols[0], cols[-1] + 1
    gw = ix1 - ix0
    # the original letter's width: the typeface's letter scaled to it (within reason)
    sx = float(np.clip(ink_w * K / gw, 0.7, 1.45)) if ink_w else 1.0
    crop = im.crop((ix0 - pad, 0, ix1 + pad, im.height))
    crop = crop.resize((max(1, int(round(crop.width * sx))), crop.height), Image.LANCZOS)
    W, H = box_w * K, box_h * K
    canvas = Image.new('L', (W, H), 0)
    # place: the ink's left edge on the original's (the width matches it); the baseline where the original H stands (the
    # stroke puts the H's ink sw below the baseline)
    left = int(round(ink_cx * K - pad * sx))                 # ink_cx: the original ink's left edge
    top = int(round(base_row * K - sw - base_y))
    canvas.paste(crop, (left, top))
    fill = np.array(canvas).astype(np.float32) / 255
    # outline: the fill grown by rim_w original pixels, crisp edge
    if rim_a > 0 and rim_w > 0:
        d = ndimage.distance_transform_edt(fill < 0.5)
        rim = np.clip((rim_w * K - d) / (0.25 * K), 0, 1) * rim_a
    else:
        rim = np.zeros_like(fill)
    # colours per row (the original's gradient), at K rows per original row
    yy = (np.arange(H) + 0.5) / K - 0.5
    col = np.stack([np.interp(yy, np.arange(box_h), rows_rgb[:, k]) for k in range(3)], axis=1)
    col = np.repeat(col[:, None, :], W, axis=1)
    # premultiplied composite: fill over rim (black)
    a = fill + (1 - fill) * rim
    pre = np.zeros((H, W, 4), np.float32)
    pre[..., :3] = col * fill[..., None]
    pre[..., 3] = a
    # filter down R x (box), back to straight alpha
    pre = pre.reshape(box_h * S, R, box_w * S, R, 4).mean(axis=(1, 3))
    out = np.zeros_like(pre)
    al = pre[..., 3]
    with np.errstate(invalid='ignore', divide='ignore'):
        out[..., :3] = np.where(al[..., None] > 1e-4, pre[..., :3] / al[..., None], 0)
    out[..., 3] = al * 255
    return out


# ---- build the pages ------------------------------------------------------------------
work = {}                                                # res -> list of (box, rgba)
patches = []                                             # (font, fingerprint before, after, line height, [(glyph, adv16, x1raw)], page hashes)


def fingerprint(cmap, rec):
    """FNV-1a 64 over the font's character map and glyph records for codes 0x21..0x7E
    (rec(g) -> the 20 record bytes); pc_text.cpp computes the same in memory"""
    h = 1469598103934665603
    for c in range(0x21, 0x7F):
        g = cmap[c]
        for b in bytes([g]) + (rec(g) if g != 0xFF else b''):
            h = ((h ^ b) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def ink_extent(crop):
    """the original ink's left edge and width, to a sub-pixel (a column's coverage says how much of it is ink)"""
    Fo = coverage(crop)
    if Fo is None:
        return None, None, None
    cols = np.where((Fo > 0.5).any(axis=0))[0]
    if not len(cols):
        return Fo, 0.0, None
    colcov = np.clip(Fo.max(axis=0), 0, 1)
    ink_cx = cols[0] + (1 - colcov[cols[0]]) * 0.5         # a faint first column starts the ink later
    ink_w = (cols[-1] - cols[0] + 1) - (1 - colcov[cols[0]]) * 0.5 - (1 - colcov[cols[-1]]) * 0.5
    return Fo, ink_cx, ink_w


def free_right(res, box, glyphs):
    """how far the box may grow to the right on its page: up to a pixel short of the next box in its rows"""
    x0, y0, x1, y1 = box
    lim = page(res).shape[1]
    for (c, r2, a0, b0, a1, b1) in glyphs.values():
        if r2 == res and a0 >= x1 and b0 < y1 and b1 > y0:
            lim = min(lim, a0 - 1)
    return lim


for f, n, glyphs, recs, cmap in fonts:
    faces = typeface_for(n)
    if not faces:
        print('font %08X (%d glyphs): no typeface chosen, left' % (f, n)); continue
    capH, base, rim_a = font_metrics(glyphs)
    if not capH:
        print('font %08X: no H, left' % f); continue
    ostem = None
    for g, (c, res, x0, y0, x1, y1) in glyphs.items():
        if c == ord('H'):
            ostem = stem_ratio(coverage(page(res)[y0:y1, x0:x1]))
    print('   stem/cap: original %.3f, %s %.3f' % (ostem, os.path.basename(faces[0]), face_stem(faces[0])))
    print('font %08X (%d glyphs): cap %d px, baseline row %d, rim alpha %.2f' % (f, n, capH, base, rim_a))
    respace = args.respace and n >= 200
    G = None
    if respace:
        # the gap: the original's mean over the letters (ink to ink), so lines keep their length
        gaps = []
        for g, (c, res, x0, y0, x1, y1) in glyphs.items():
            if chr(c).isalpha() and c < 0x7F and page(res) is not None:
                Fo, ic, iw = ink_extent(page(res)[y0:y1, x0:x1])
                if iw:
                    gaps.append((u16(recs + 20 * g + 6) / 16.0) - iw)
        G = float(np.mean(gaps))
        print('   respaced: gap %.2f px (the original\'s ranged %.2f..%.2f), line height %d' % (G, min(gaps), max(gaps), args.lineh))
    newrec = {}                                          # glyph -> (left16, adv16, x0raw, x1raw)
    if respace:
        # the objective bullet ('*', the red dot: art): the first line of an objective starts
        # "* " (14 px) and its continuation lines four spaces (20 px) in, so the first line's
        # text sat 6 px left of the rest. The bullet's advance grows by those 6 and its quad
        # moves 6 right (the record's left edge, +4; its page box is unchanged: HUD art sits
        # next to it), so the dot keeps the same gap to the text and hangs left of the column.
        gs = cmap[ord('*')]
        if gs != 0xFF and gs in glyphs:
            c, res, x0, y0, x1, y1 = glyphs[gs]
            r = recs + 20 * gs
            E = 6
            newrec[gs] = (u16(r + 4) + E * 16, u16(r + 6) + E * 16, u16(r + 0xC), u16(r + 0xE))
    done = left = 0
    for g, (c, res, x0, y0, x1, y1) in glyphs.items():
        p = page(res)
        # ASCII only: from 0x7F up the game's codes are its own character set (0x7F is the
        # art of a-grave, and so on), so a code does not say which letter its box shows
        if p is None or c in PROMPT_CODES or c < 0x21 or c > 0x7E:
            continue
        ch = bytes([c]).decode('cp1252', errors='replace')
        if ch == '�':
            continue
        crop = p[y0:y1, x0:x1]
        Fo = coverage(crop)
        if Fo is None or is_art(crop, Fo):
            left += 1; continue
        _, ink_cx, ink_w = ink_extent(crop)
        bw = x1 - x0
        if respace and ink_w:
            adv = max(1, int(round(ink_w + G)))
            adv = min(adv, free_right(res, (x0, y0, x1, y1), glyphs) - x0)
            ink_cx, ink_w, bw = G / 2, adv - G, adv
            r = recs + 20 * g
            newrec[g] = (u16(r + 4), adv * 16, u16(r + 0xC), u16(r + 0xC) + adv * 16)
        # the fill's colour per row
        hgt = y1 - y0
        rows_rgb = np.full((hgt, 3), 255, np.float32); have = np.zeros(hgt, bool)
        for yy in range(hgt):
            mk = Fo[yy] > 0.7
            if mk.any():
                rows_rgb[yy] = crop[yy, :, :3][mk].mean(axis=0); have[yy] = True
        if have.any():
            idx = np.where(have)[0]
            for k in range(3):
                rows_rgb[:, k] = np.interp(np.arange(hgt), idx, rows_rgb[idx, k])
            rows_rgb = ndimage.gaussian_filter1d(rows_rgb, 1.5, axis=0, mode='nearest')
        hd = draw_char(ch, faces, capH, bw, hgt, ink_cx, base, ink_w, rows_rgb, rim_a, 0.6, ostem)
        if hd is None:
            left += 1; newrec.pop(g, None); continue
        work.setdefault(res, []).append(((x0, y0, x0 + bw, y1), hd))
        done += 1
    print('   %d letters drawn, %d left (art)' % (done, left))
    if respace and newrec:
        def rec_before(g):
            return m[recs + 20 * g:recs + 20 * g + 20]
        def rec_after(g):
            b = bytearray(rec_before(g))
            if g in newrec:
                for o, v in zip((4, 6, 0xC, 0xE), newrec[g]):
                    struct.pack_into('<H', b, o, v)
            return bytes(b)
        hashes = sorted(set('%016x' % fnv(page(res).tobytes()) for (c, res, *_r) in glyphs.values() if page(res) is not None))
        patches.append((f, fingerprint(cmap, rec_before), fingerprint(cmap, rec_after), args.lineh, sorted((g,) + v for g, v in newrec.items()), hashes))

def write_bmp(path, img):
    hh, ww = img.shape[:2]
    bgra = img[::-1, :, [2, 1, 0, 3]].tobytes()
    v5 = struct.pack('<IiiHHIIiiII', 124, ww, hh, 1, 32, 3, len(bgra), 2835, 2835, 0, 0)
    v5 = (v5 + struct.pack('<IIII', 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000) + b'BGRs' + b'\0' * 80)[:124]
    with open(path, 'wb') as f:
        f.write(b'BM' + struct.pack('<IHHI', 14 + 124 + len(bgra), 0, 0, 14 + 124) + v5 + bgra)


for res, items in work.items():
    p = page(res)
    Hh, Ww = p.shape[:2]
    out = np.array(Image.fromarray(p).resize((Ww * S, Hh * S), Image.BILINEAR)).astype(np.float32)
    # the overlay that ships: only our letters; every other texel is the key colour
    # (255, 0, 255, alpha 0), which pc_hd.cpp fills from the player's own page
    ovl = np.zeros_like(out)
    ovl[...] = (255, 0, 255, 0)
    # clear a margin around every replaced box first (filtering at a box edge reads a texel or
    # two past it: the old art there drew dark seams), then paste the letters
    mg = max(2, S // 2)
    for (x0, y0, x1, y1), hd in items:
        out[max(0, y0 * S - mg):y1 * S + mg, max(0, x0 * S - mg):x1 * S + mg] = 0
        ovl[max(0, y0 * S - mg):y1 * S + mg, max(0, x0 * S - mg):x1 * S + mg] = 0
    for (x0, y0, x1, y1), hd in items:
        out[y0 * S:y1 * S, x0 * S:x1 * S] = hd
        ovl[y0 * S:y1 * S, x0 * S:x1 * S] = hd
    img = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    name = '%016x' % fnv(p.tobytes())
    write_bmp(os.path.join(args.out, name + '.bmp'), img)
    write_bmp(os.path.join(args.out, name + '.ovl.bmp'), np.clip(ovl + 0.5, 0, 255).astype(np.uint8))
    if args.sheet:
        a = img[..., 3:4].astype(np.float32) / 255
        Image.fromarray((img[..., :3] * a + 30 * (1 - a)).astype(np.uint8)).save(os.path.join(args.out, 'sheet_%s.png' % name))
    print('page %08X -> %s.bmp (%d letters)' % (res, name, len(items)))


if args.header:
    with open(args.header, 'w') as hf:
        hf.write('/* Generated by tools/font_art/replace_fonts.py --header: even letter spacing and a taller\n'
                 ' * line for the main text font (see that script). Applied by pc_text.cpp to a font whose\n'
                 ' * records match "before"; "after" marks one already changed. */\n')
        hf.write('struct GlyphPatch { unsigned char glyph; unsigned short left16, adv16, x0raw, x1raw; };\n')
        hf.write('struct FontPatch { unsigned long long before, after; unsigned lineH; unsigned n; const GlyphPatch *g; unsigned long long page; };\n')
        for i, (f, b, a, lh, gl, hs) in enumerate(patches):
            hf.write('static const GlyphPatch kGlyphPatch%d[%d] = {\n' % (i, len(gl)))
            for k in range(0, len(gl), 6):
                hf.write('    ' + ' '.join('{%d, %d, %d, %d, %d},' % t for t in gl[k:k + 6]) + '\n')
            hf.write('};\n')
        hf.write('static const FontPatch kFontPatches[%d] = {\n' % len(patches))
        for i, (f, b, a, lh, gl, hs) in enumerate(patches):
            hf.write('    {0x%016xull, 0x%016xull, %d, %d, kGlyphPatch%d, 0x%sull},   /* font at %08X in the snapshot */\n' % (b, a, lh, len(gl), i, hs[0], f))
        hf.write('};\n')
    print('metrics ->', args.header)
