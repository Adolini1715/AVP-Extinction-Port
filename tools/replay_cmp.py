#!/usr/bin/env python3
"""replay_cmp.py A.rph B.rph [--mask noise.txt] [--write-mask noise.txt] [--show N]

Compare two AVPE_REPLAY_OUT page-hash files (notes/replay.md). Checkpoints are
matched by frame number. For each one prints the number of 4 KB pages that
differ (outside the mask), and for the first differing checkpoint the page
ranges, so a full snapshot (AVPE_REPLAY_SNAP) at that frame can be diffed.

--write-mask: write every page that differs at any checkpoint (use on two runs
of the SAME build: those pages are run-to-run noise) as hex page addresses.
--fail: exit 1 if any checkpoint differs (pipe.sh replay).
--mask: ignore those pages (one hex address or lo-hi range per line).
"""
import struct, sys

def load(path):
    d = {}
    with open(path, 'rb') as f:
        while True:
            h = f.read(32)
            if len(h) < 32: break
            magic, frame, n, polls, tlo, thi, bp, _ = struct.unpack('<8I', h)
            assert magic == 0x48535052, path
            hs = f.read(8 * n)
            if len(hs) < 8 * n: break
            d[frame] = (struct.unpack('<%dQ' % n, hs), polls, (thi << 32) | tlo, bp)
    return d

def ranges(pages):
    out = []
    for p in sorted(pages):
        if out and out[-1][1] == p - 1: out[-1][1] = p
        else: out.append([p, p])
    return out

def main():
    a = sys.argv[1]; b = sys.argv[2]; args = sys.argv[3:]
    mask = set(); wmask = None; show = 40; fail = False
    i = 0
    while i < len(args):
        if args[i] == '--mask':
            for line in open(args[i + 1]):
                line = line.split('#')[0].strip()
                if not line: continue
                lo, _, hi = line.partition('-')
                lo = int(lo, 16) >> 12; hi = (int(hi, 16) >> 12) if hi else lo
                mask.update(range(lo, hi + 1))
            i += 2
        elif args[i] == '--write-mask': wmask = args[i + 1]; i += 2
        elif args[i] == '--fail': fail = True; i += 1
        elif args[i] == '--show': show = int(args[i + 1]); i += 2
        else: sys.exit('unknown option ' + args[i])
    A, B = load(a), load(b)
    frames = sorted(set(A) & set(B))
    if not frames: sys.exit('no common checkpoints (%d / %d)' % (len(A), len(B)))
    noise = set(); first = None
    for fr in frames:
        ha, pa, ta, ba = A[fr]; hb, pb, tb, bb = B[fr]
        diff = [p for p in range(len(ha)) if ha[p] != hb[p] and p not in mask]
        noise.update(diff)
        extra = '' if (pa, ta, ba) == (pb, tb, bb) else '  polls %u/%u clock %X/%X briefing %u/%u' % (pa, pb, ta, tb, ba, bb)
        print('frame %6u: %5d pages differ%s' % (fr, len(diff), extra))
        if diff and first is None: first = (fr, diff)
    print('checkpoints %d, last frame %u; A has %d, B has %d' % (len(frames), frames[-1], len(A), len(B)))
    if first:
        fr, diff = first
        print('first divergence at frame %u, %d pages:' % (fr, len(diff)))
        for lo, hi in ranges(diff)[:show]:
            print('  %08X-%08X (%d pages)' % (lo << 12, (hi << 12) + 0xFFF, hi - lo + 1))
    else:
        print('identical at every common checkpoint')
    if wmask:
        with open(wmask, 'w') as f:
            for lo, hi in ranges(noise): f.write('%08X-%08X\n' % (lo << 12, (hi << 12) + 0xFFF))
        print('wrote %d noise pages to %s' % (len(noise), wmask))

    if fail and first: sys.exit(1)

main()
